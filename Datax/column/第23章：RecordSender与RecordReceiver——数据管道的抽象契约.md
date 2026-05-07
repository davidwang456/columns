# 第23章：RecordSender与RecordReceiver——数据管道的抽象契约

## 1. 项目背景

某电商平台的数据团队接到一个需求：将 MongoDB 中的用户行为日志（日均 2 亿条）同步到 ClickHouse 分析库。团队决定开发一个自定义的 MongoDB Reader 插件，复用 DataX 的 Channel 和 Writer 基础设施。开发过程本身很顺利——按照 Reader 接口实现 `init() → prepare() → startRead()` 即可。但在集成测试阶段，一个诡异的 bug 出现了：Writer 端永远收不到最后一批数据。

排查过程耗时三天。日志显示 Reader 端已经读完了全部 2 亿条记录（`sendToWriter` 调用了 2 亿次），Writer 端也正常写入了 1.999 亿条——唯独少了最后 100 万条。根因最终定位在 `RecordSender` 的 `flush()` 和 `terminate()` 调用顺序上：开发者只调了 `flush()` 就以为数据发完了，没有调用 `terminate()`。Writer 端一直在 `getFromReader()` 中阻塞等待下一个 Record——永远等不到 `TerminateRecord` 这个特殊的哨兵信号。

这个看似"简单"的问题揭示了 DataX 数据传输管道设计中一个关键但容易被忽视的要点：`RecordSender` 和 `RecordReceiver` 不仅是 Reader 和 Writer 之间的通信接口，更是一套严格的**契约**——`createRecord() → sendToWriter() → flush() → terminate()` 的调用顺序决定了 Writer 能否正确感知数据流的结束。缺了任何一个步骤，都可能产生"数据丢失"或"死锁"的严重后果。

## 2. 项目设计——剧本式交锋对话

**（会议室，白板上画满了箭头和数据流向）**

**小胖**：（拿着 Mongo 的源码）我就不懂了——我在 `startRead()` 的最后明明调了 `recordSender.flush()`，为什么 Writer 还说没收到数据结束的信号？

**大师**：（拿红笔在"flush"旁边写了个大叉）因为 `flush()` 只负责"把缓冲区的数据刷到 Channel 里"，不负责"告诉 Writer 没有更多数据了"。你要再调一个 `recordSender.terminate()`，它会在 Channel 里塞一条**特殊的 Record**——`TerminateRecord`。

**小胖**：一条 Record？和普通 Record 一样？Writer 怎么知道这是终点？

**大师**：（翻开源码）看：

```java
// RecordSender.java
public interface RecordSender {
    Record createRecord();
    void sendToWriter(Record record);
    void flush();
    void terminate();  // ★ 往 Channel 里塞一条 TerminateRecord
    void shutdown();
}

// BufferedRecordExchanger.java — terminate() 实现
public void terminate() {
    flush();  // 先刷空缓冲区
    this.channel.push(TerminateRecord.get());  // ★ 哨兵 Record
}
```

`TerminateRecord` 是一个**单例**（全局唯一实例），Writer 端每 `getFromReader()` 一条就检查一下是不是 `TerminateRecord.get()` 这个实例——是就停止循环。

**技术映射**：RecordSender = 快递公司的发货流程。`createRecord()` = 拿空包裹盒，`sendToWriter()` = 把包裹塞进货车（Channel），`flush()` = 把货车开到转运中心，`terminate()` = 在最后一辆货车上贴"今日最后一班"的标签。Writer 看到这个标签就知道：今天没货了，关门下班。

**小白**：（翻到 RecordReceiver 的源码）那 Writer 端的接收逻辑是怎样的？

```java
// RecordReceiver.java
public interface RecordReceiver {
    Record getFromReader();
    void shutdown();
}

// Writer.Task.startWrite() 的典型实现
public void startWrite(RecordReceiver lineReceiver) {
    Record record;
    while ((record = lineReceiver.getFromReader()) != null) {
        // ★ 哨兵检查
        if (record == TerminateRecord.get()) {
            break;  // 数据流结束，退出循环
        }
        // 正常处理 Record
        this.transportOneRecord(record);
    }
}
```

**小胖**：（恍然大悟）所以如果我写了 `flush()` 但没写 `terminate()`，Writer 那边的 while 循环永远不会 break——它一直在等下一个 Record，而 Channel 已经空了，就阻塞在 `getFromReader()` 上！

**大师**：没错。而且还有一个更隐蔽的问题——如果你有**多个 Reader Task** 共享一个 Channel 向同一个 Writer 发送数据（在某种特殊调度模式下），每个 Reader Task 都要调 `terminate()`。Writer 需要收到**与 Reader Task 数量相等的 TerminateRecord** 才能安全退出——因为多个 TerminateRecord 可以用来做"计数确认"。

**小白**：说到 flush，我看 `BufferedRecordExchanger` 的 buffer 逻辑——它是攒满 `bufferSize` 条才一次性 pushAll 到 Channel，对吗？

**大师**：对。这个 `bufferSize` 就是配置里的 `recordBatchSize`。默认值通常是 1024 或 2048。它的设计目的是**减少 Channel 的 push/pull 频率**——如果逐条 push，每条 Record 都要经历"offer 入队 + 唤醒 Writer 线程"的开销，线程上下文切换成本极高。攒 1024 条一起 push，切换开销均摊到 1024 条，效率提升几十倍。

```java
// BufferedRecordExchanger — 批量缓冲区
private List<Record> buffer = new ArrayList<>(1024);
private int bufferSize;  // = recordBatchSize

public void sendToWriter(Record record) {
    this.buffer.add(record);
    if (this.buffer.size() >= this.bufferSize) {
        flush();  // pushAll 到 Channel
    }
}

public Record getFromReader() {
    if (this.buffer.isEmpty()) {
        this.channel.pullAll(this.buffer);  // 从 Channel 批量拉取
    }
    return this.buffer.remove(0);
}
```

**技术映射**：batch buffer = 超市购物车。你不会每挑一样东西就去排一次队结账——那样效率极低。你把东西攒满一购物车（buffer），一次性去结账（pushAll/pullAll），排队次数大幅减少。

## 3. 项目实战

### 3.1 步骤一：实现一个正确的 Reader.Task——完整的 RecordSender 调用链

**目标**：编写一个自定义 Reader，展示 `createRecord → setColumn → sendToWriter → flush → terminate` 的完整调用顺序。

```java
public class CustomReader extends Reader {
    
    public static class Task extends Reader.Task {
        
        @Override
        public void startRead(RecordSender recordSender) {
            // 1. 准备数据源（例如 JDBC ResultSet）
            Connection conn = getConnection();
            PreparedStatement ps = conn.prepareStatement(
                "SELECT id, name, amount, create_time FROM orders");
            ResultSet rs = ps.executeQuery();
            
            int batchCount = 0;
            int batchSize = 1024;  // 与 recordBatchSize 对齐
            
            while (rs.next()) {
                // 2. ★ createRecord：创建空 Record，由 DataX 统一管理
                Record record = recordSender.createRecord();
                
                // 3. ★ 逐列填充数据，注意类型映射
                record.addColumn(new LongColumn(rs.getLong("id")));
                record.addColumn(new StringColumn(rs.getString("name")));
                record.addColumn(new DoubleColumn(rs.getDouble("amount")));
                record.addColumn(new DateColumn(rs.getTimestamp("create_time")));
                
                // 4. ★ sendToWriter：发送给 Writer
                recordSender.sendToWriter(record);
                batchCount++;
                
                // 5. ★ 定期 flush：确保数据不会在缓冲区积压过久
                if (batchCount % batchSize == 0) {
                    recordSender.flush();
                }
            }
            
            // 6. ★★ 关键：最后必须 flush + terminate
            recordSender.flush();      // 刷空残余 buffer
            recordSender.terminate();  // 发送 TerminateRecord 哨兵
            
            // 7. 释放资源
            rs.close();
            ps.close();
            conn.close();
        }
    }
}
```

### 3.2 步骤二：在 Writer.Task 中正确处理 TerminateRecord

**目标**：展示 Writer 端如何识别 TerminateRecord 并优雅退出。

```java
public class CustomWriter extends Writer {
    
    public static class Task extends Writer.Task {
        
        @Override
        public void startWrite(RecordReceiver lineReceiver) {
            Connection conn = getConnection();
            conn.setAutoCommit(false);
            PreparedStatement ps = conn.prepareStatement(
                "INSERT INTO target_table (id, name, amount, create_time) VALUES (?, ?, ?, ?)");
            
            int batchCount = 0;
            int batchSize = 2048;
            
            Record record;
            while (true) {
                // 1. ★ 阻塞获取下一条 Record
                record = lineReceiver.getFromReader();
                
                // 2. ★★ 哨兵检查：单例引用比较
                if (record == TerminateRecord.get()) {
                    // 收到终止信号，提交残余 batch 后退出
                    if (batchCount > 0) {
                        ps.executeBatch();
                        conn.commit();
                    }
                    break;
                }
                
                // 3. 正常处理：从 Record 中取出 Column 值
                ps.setLong(1, record.getColumn(0).asLong());
                ps.setString(2, record.getColumn(1).asString());
                ps.setDouble(3, record.getColumn(2).asDouble());
                ps.setTimestamp(4, new Timestamp(
                    record.getColumn(3).asDate().getTime()));
                ps.addBatch();
                batchCount++;
                
                // 4. ★ 批量提交
                if (batchCount >= batchSize) {
                    ps.executeBatch();
                    conn.commit();
                    batchCount = 0;
                }
            }
            
            // 5. 释放资源
            ps.close();
            conn.close();
        }
    }
}
```

### 3.3 步骤三：BufferedRecordExchanger 的批量缓冲机制深度剖析

**目标**：理解 BufferedRecordExchanger 如何提升传输效率。

```java
// BufferedRecordExchanger.java — 完整核心逻辑
public class BufferedRecordExchanger implements RecordSender, RecordReceiver {
    
    private final Channel channel;
    private final List<Record> buffer;       // ★ 本地缓冲
    private final int bufferSize;            // = recordBatchSize
    private int byteAccumulator = 0;
    
    // ★ 构造时从配置读取 bufferSize
    public BufferedRecordExchanger(Channel channel, int bufferSize) {
        this.channel = channel;
        this.bufferSize = bufferSize;
        this.buffer = new ArrayList<>(bufferSize);
    }
    
    // ============ RecordSender 接口实现 ============
    
    @Override
    public Record createRecord() {
        return new DefaultRecord();
    }
    
    @Override
    public void sendToWriter(Record record) {
        if (record == null) return;
        
        // ★ 放入本地 buffer，不是直接 push 到 Channel
        this.buffer.add(record);
        this.byteAccumulator += record.getByteSize();
        
        // ★ 攒够 bufferSize 条 → 批量 push 到 Channel
        if (this.buffer.size() >= this.bufferSize) {
            flush();
        }
    }
    
    @Override
    public void flush() {
        if (this.buffer.isEmpty()) return;
        
        // ★★ 一次 pushAll，减少 Channel 操作次数
        this.channel.pushAll(this.buffer);
        
        // ★ 更新流控统计
        Communication stat = new Communication();
        stat.setLongCounter("byteSpeed", this.byteAccumulator);
        stat.setLongCounter("recordSpeed", (long) this.buffer.size());
        this.channel.statPush(stat);
        
        this.buffer.clear();
        this.byteAccumulator = 0;
    }
    
    @Override
    public void terminate() {
        // ★★ 先 flush 残余数据，再发送 TerminateRecord
        flush();
        this.channel.push(TerminateRecord.get());
    }
    
    @Override
    public void shutdown() {
        // 通知 Writer 端关闭
    }
    
    // ============ RecordReceiver 接口实现 ============
    
    @Override
    public Record getFromReader() {
        // ★ 本地 buffer 空了 → 从 Channel 批量拉取
        if (this.buffer.isEmpty()) {
            this.channel.pullAll(this.buffer);
            
            // ★ 更新流控统计
            long totalBytes = 0;
            for (Record r : this.buffer) {
                totalBytes += r.getByteSize();
            }
            Communication stat = new Communication();
            stat.setLongCounter("byteSpeed", totalBytes);
            stat.setLongCounter("recordSpeed", (long) this.buffer.size());
            this.channel.statPull(stat);
        }
        
        return this.buffer.remove(0);
    }
    
    @Override
    public void shutdown() {
        // 通知 Reader 端关闭
    }
}
```

### 3.4 步骤四：recordBatchSize 调优实验

**目标**：测试不同 bufferSize 对吞吐量的影响。

**测试环境**：16 核 CPU、32GB 内存、SSD 硬盘。Reader = StreamReader（1000 万条），Writer = StreamWriter（不落地）。

```json
{
    "reader": {
        "name": "streamreader",
        "parameter": {
            "column": [
                {"type": "string", "random": "50,200"},
                {"type": "long", "random": "1, 1000000"}
            ],
            "sliceRecordCount": 10000000
        }
    },
    "writer": {
        "name": "streamwriter",
        "parameter": {"print": false}
    },
    "setting": {
        "speed": {"channel": 8, "recordBatchSize": 1024}
    }
}
```

**测试矩阵**（channel=8，总共 1000 万条）：

| recordBatchSize | 每 Channel buffer 内存 | Channel 操作次数 | QPS | 耗时 | CPU 系统态占比 |
|----------------|----------------------|----------------|-----|------|-------------|
| 64 | 64×2KB = 128KB | 156,250 次 | 42 万条/s | 23.8s | 22%（频繁上下文切换） |
| 256 | 256×2KB = 512KB | 39,062 次 | 55 万条/s | 18.2s | 12% |
| 1024 | 1024×2KB = 2MB | 9,766 次 | 62 万条/s | 16.1s | 7% |
| 2048 | 2048×2KB = 4MB | 4,883 次 | 65 万条/s | 15.4s | 5% |
| 4096 | 4096×2KB = 8MB | 2,441 次 | 63 万条/s | 15.9s | 4% |

**关键结论**：
- bufferSize 从 64 到 1024，收益最大（Channel 操作次数从 15 万次降到 1 万次）
- bufferSize 从 1024 到 2048，边际收益递减（瓶颈转移到 Channel 队列本身）
- bufferSize=4096 反而下降——因为每个 buffer 太大，flush 间隔太长，Channel 出现了"间歇性空转"

### 3.5 步骤五：常见错误案例与修复

**错误案例 1：忘记调 terminate()**

```java
// ★ 错误示范
public void startRead(RecordSender sender) {
    for (Record r : generateRecords()) {
        sender.sendToWriter(r);
    }
    sender.flush();
    // ★★ BUG: 没调 sender.terminate() 
    // Writer 的 getFromReader() 永远阻塞在 Channel.take() 上
}
```

修复：

```java
// ✓ 正确
public void startRead(RecordSender sender) {
    for (Record r : generateRecords()) {
        sender.sendToWriter(r);
    }
    sender.flush();
    sender.terminate();  // ★★ 发送哨兵
}
```

**错误案例 2：terminate() 前没 flush()**

```java
// ★ 错误示范
public void startRead(RecordSender sender) {
    for (Record r : generateRecords()) {
        sender.sendToWriter(r);
    }
    // ★★ BUG: buffer 里还有数据没刷出，直接 terminate 了
    sender.terminate();  // terminate 内部会调 flush，但顺序混乱
}
```

实际上 `BufferedRecordExchanger.terminate()` 内部会先 `flush()` 再发哨兵，所以这个例子**在 BufferedRecordExchanger 下不会丢数据**。但如果你自己实现了 RecordSender 接口而没遵循这个惯例，就可能丢。

**错误案例 3：Writer 端没检查 TerminateRecord**

```java
// ★ 错误示范
public void startWrite(RecordReceiver receiver) {
    Record record;
    while ((record = receiver.getFromReader()) != null) {
        // ★★ BUG: TerminateRecord 不是 null，会进入处理逻辑
        processRecord(record);  // TerminateRecord 被当成普通数据！
    }
}
```

修复：

```java
// ✓ 正确
public void startWrite(RecordReceiver receiver) {
    Record record;
    while (true) {
        record = receiver.getFromReader();
        if (record == TerminateRecord.get()) {  // ★★ 哨兵检查
            break;
        }
        processRecord(record);
    }
}
```

## 4. 项目总结

### 4.1 RecordSender / RecordReceiver 的完整契约

```
Reader.Task.startRead(RecordSender sender)
  │
  ├─ sender.createRecord()        ← 创建空 Record
  ├─ record.addColumn(column)     ← 填充数据
  ├─ sender.sendToWriter(record)  ← 塞入 buffer（batch 攒满 → pushAll）
  ├─ sender.flush()               ← 刷空 buffer → Channel.pushAll()
  └─ sender.terminate()           ← 发送 TerminateRecord 哨兵

Writer.Task.startWrite(RecordReceiver receiver)
  │
  └─ while (record = receiver.getFromReader()) {
       ├─ if (record == TerminateRecord.get()) → break
       ├─ extractColumn(record)
       ├─ ps.setXxx(i, value)
       ├─ ps.addBatch()
       └─ if (batchCount >= batchSize) → executeBatch()
     }
```

### 4.2 优点

1. **契约清晰**：`create → send → flush → terminate` 四个步骤定义明确，不会歧义
2. **哨兵设计优雅**：用单例 `TerminateRecord` 而非 null 或异常来标记结束，避免 NPE
3. **批量缓冲**：BufferedRecordExchanger 显著减少 Channel push/pull 频率，降低上下文切换
4. **接口抽象**：RecordSender/RecordReceiver 将 Reader 和 Writer 完全解耦，只依赖接口
5. **流控集成**：BufferedRecordExchanger 在 flush/pullAll 时自动调用 statPush/statPull

### 4.3 缺点

1. **caller 责任重**：Reader 开发者必须记住 terminate()，缺一次就死锁
2. **buffer 不可见**：buffered data 在 flush 前对 Writer 完全不可见，可能导致"明明写了，Writer 收不到"
3. **无超时机制**：getFromReader() 无限阻塞，没有超时退出的备选方案
4. **单线程假设**：RecordReceiver 假设单 Writer 线程消费，不适合多消费者并行
5. **bufferSize 全局共享**：所有 Channel 共用一个 recordBatchSize，无法按表差异化

### 4.4 适用场景

1. 自定义 Reader/Writer 插件开发（必须遵循此契约）
2. 大字段表同步（靠批量缓冲减少操作频率）
3. 高并发全量迁移（频繁的 Channel push/pull 是瓶颈，buffer 越大越好）
4. 异构数据源桥接（Record 作为中间格式，两端只需理解 Record 格式）
5. Transformer 管道（BufferedRecordTransformerExchanger 在 RecordSender 基础上增加变换逻辑）

### 4.5 注意事项

1. `terminate()` 必须在所有数据都 `sendToWriter` 完且 `flush()` 后调用
2. Writer 端必须用 `==` 而非 `equals()` 来判断 TerminateRecord（它是单例）
3. `recordBatchSize` 过大会导致 flush 间隔长，Writer 端出现"饥饿"（等一批攒满）
4. `recordBatchSize` 过小 → Channel push/pull 频率高 → CPU 上下文切换开销大
5. BufferedRecordExchanger 的 buffer 是**每 Channel 一个**，N 个 Channel = N 个独立 buffer

### 4.6 常见踩坑经验

**坑 1：多个 Reader Task 共享 Channel 时的 TerminateRecord 计数**

如果两个 Reader Task 都往同一个 Channel 发数据，每个都会发一条 TerminateRecord。Writer 需要收到 2 条 TerminateRecord 才能退出。但如果 flush 后先发了一条 TerminateRecord，Writer 收到后退出——第二个 Reader 的 TerminateRecord 留在 Channel 里，永不被消费。

**坑 2：flush() 内部也做了 statPush，batch flush 时重复计数**

```java
public void flush() {
    channel.pushAll(buffer);
    channel.statPush(stat);  // ★ 批量统计
}
```

如果 buffered 的 1024 条中每条都是 2KB，statPush 一次性上报 2048KB 给流控——如果限速是 1MB/s，一秒只允许 flush 一次，Writer 端会大量 sleep。解决：增大 bufferSize 让每次 flush 的数据量接近限速值。

**坑 3：createRecord() 每次都 new，高频 GC 压力**

`recordSender.createRecord()` 在 DefaultRecord 实现中是 `new DefaultRecord()`。每秒 10 万条同步 = 10 万个对象创建。GC 年轻代的 Eden 区频繁填满 → Minor GC 频繁。解决：使用 JDK 11+ 的 ZGC 或 G1GC，调大 `-XX:NewRatio`。

### 4.7 思考题

1. 如果 Reader 和 Writer 之间插入了 Transformer，TerminateRecord 是否也需要被 Transformer 处理？`BufferedRecordTransformerExchanger` 是怎么处理这个问题的？
2. `recordBatchSize` 默认 1024，但 Channel 的 capacity 默认 128。如果 batch 攒满 1024 条后一次性 push，而 Channel 只有 128 的空位——会发生什么？如何设计 batchSize 和 capacity 的配比？

（答案见附录）
