# 第33章 Debezium引擎集成源码分析

## 1 项目背景

### 业务场景：理解Debezium在Flink CDC中的角色

第2章我们提到，Flink CDC = Debezium（翻译官）+ Flink（调度员）。但Debezium具体是怎么"翻译"Binlog的？它的Offset（位点）和Schema（表结构）在Flink Checkpoint中是怎么存储和恢复的？

如果不对Debezium引擎的集成方式有深入理解，生产环境中遇到"作业恢复后数据重复"、"Checkpoint失败导致不断重跑"等问题时，将无从下手。

### Debezium集成架构

```
┌─────────────────────────────────────────────┐
│  DebeziumSourceFunction<T>                  │
│  (implements SourceFunction,                │
│   CheckpointedFunction,                     │
│   CheckpointListener)                       │
│                                             │
│  ├─ offsetState: ListState<byte[]>          │
│  │   → 存储Debezium的Binlog位点信息         │
│  │                                          │
│  ├─ historyState: ListState<byte[]>         │
│  │   → 存储数据库Schema历史（DDL记录）      │
│  │                                          │
│  ├─ engine: DebeziumEngine                  │
│  │   → 内嵌的Debezium引擎（独立线程运行）    │
│  │                                          │
│  ├─ handover: Handover                      │
│  │   → 线程安全的交付队列（Debezium线程     │
│  │     → Flink主线程的数据传递通道）         │
│  │                                          │
│  └─ offsetBackingStore:                     │
│     FlinkOffsetBackingStore                 │
│     → 重写Debezium的OffsetBackingStore       │
│     → 将位点读写重定向到Flink State          │
└─────────────────────────────────────────────┘
```

---

## 2 项目设计 · 三人交锋对话

**小胖**（疑惑）：Debezium是一个独立的Java库，Flink CDC是把它嵌入到了Flink的一个SourceFunction中。他们之间怎么通信的？Debezium如何把Binlog事件"喂"给Flink？

**大师**：关键在于一个叫做**Handover**的类——它是Debezium引擎线程和Flink主线程之间的"桥梁"。

**Handover的工作原理：**
```
Debezium Engine线程 (Binlog解析线程)
    │
    │  produces(SourceRecord) → handover.produce(record)
    │  将解析出的SourceRecord放入Handover队列
    ▼
┌─────────────────────────────────┐
│  Handover (线程安全交付队列)      │
│  ├─ produce() 【Debezium线程调用】│
│  │   → 将事件放入队列             │
│  │   → 队列满时阻塞              │
│  │                              │
│  └─ pollNext() 【Flink线程调用】  │
│      → 从队列取出事件             │
│      → 队列空时阻塞              │
│      → 通过interrupt()支持取消    │
└─────────────────────────────────┘
    │
    ▼
Flink SourceFunction.run() (Flink处理线程)
    │
    │  handover.pollNext() → deserialize → collector.collect()
    │  从Handover取出事件，反序列化后输出
    │
    ▼
Flink DataStream (后续的Transform/Sink)
```

**小白**：那`FlinkOffsetBackingStore`又是什么？Debezium官方用文件存储Offset，Flink CDC怎么改成用Flink State？

**大师**：这是Flink CDC的高明之处。Debezium引擎默认用**文件**来存储它读取的Binlog位点（offset.dat文件）。但在Flink中：
- 文件存储和Flink的Checkpoint机制不兼容
- 如果TaskManager崩溃，本地文件丢失，位点也丢失了

`FlinkOffsetBackingStore`重写了Debezium的`OffsetBackingStore`接口，将位点的读写操作**重定向到Flink的`ListState`**：

```java
public class FlinkOffsetBackingStore implements OffsetBackingStore {
    // 不再是写入文件，而是写入Flink Managed State
    private ListState<byte[]> offsetState;

    @Override
    public void set(SourceRecord record) {
        // Debezium保存位点 → 写入Flink State
        offsetState.add(serialize(record));
    }

    @Override
    public Map<ByteArray, SourceRecord> get() {
        // Debezium读取位点 → 从Flink State读取
        return deserialize(offsetState.get());
    }
}
```

这样，当Flink做Checkpoint时，Debezium的Offset（即Binlog位点）和其他Operator的状态一起被持久化。恢复时一起恢复——实现了**Source端的Exactly-Once**。

**技术映射**：Handover像"工厂传送带上的缓冲区"——Debezium是生产工人（不断往上传送带放零件），Flink是装配工（从传送带取零件组装）。如果装配工手慢（反压），传送带会停（Handover阻塞），生产工人也停下——这就是反压从Sink传到Source的机制。

---

## 3 项目实战

### 分步实现

#### 步骤1：阅读DebeziumSourceFunction核心源码

```java
// 源码路径:
// flink-connector-debezium/src/main/java/org/apache/flink/cdc/debezium/DebeziumSourceFunction.java

public class DebeziumSourceFunction<T> extends RichSourceFunction<T>
    implements CheckpointedFunction, CheckpointListener {

    // ========== Flink运行时方法 ==========
    @Override
    public void run(SourceContext<T> sourceContext) throws Exception {
        // 1. 创建Debezium引擎
        properties.setProperty("name", "debezium-engine");
        properties.setProperty("offset.storage",
            FlinkOffsetBackingStore.class.getCanonicalName());
        properties.setProperty("database.history",
            FlinkDatabaseSchemaHistory.class.getCanonicalName());
        
        engine = DebeziumEngine.create(Connect.class)
            .using(properties)
            .notifying((records, committer) -> {
                // Callback: Debezium引擎每解析一个Binlog事件，回调此方法
                for (SourceRecord record : records) {
                    // 2. 通过Handover交付给Flink线程
                    handover.produce(record);
                }
            })
            .build();

        // 3. 启动Debezium引擎（独立线程）
        executor = Executors.newSingleThreadExecutor();
        executor.execute(() -> {
            Thread.currentThread().setName("debezium-engine");
            try {
                engine.run();
            } catch (Exception e) {
                handover.reportError(e);
            }
        });

        // 4. Flink处理线程：从Handover获取事件
        while (running) {
            SourceRecord record = handover.pollNext();
            // 5. 通过用户定义的DeserializationSchema反序列化
            deserializationSchema.deserialize(record, collector);
        }
    }

    // ========== Checkpoint接口 ==========
    @Override
    public void snapshotState(FunctionSnapshotContext context) throws Exception {
        // Checkpoint时：确保Debezium引擎停止处理
        // 并将当前Offset保存到State
        if (engine != null) {
            // 触发Debezium的Offset持久化
            // → FlinkOffsetBackingStore.set()被调用
            // → 写入ListState<byte[]> offsetState
        }
    }

    @Override
    public void initializeState(FunctionInitializationContext context) throws Exception {
        // 恢复时：从State加载Offset和Schema History
        offsetState = context.getOperatorStateStore()
            .getListState(new ListStateDescriptor<>("offsets", ...));
        historyState = context.getOperatorStateStore()
            .getListState(new ListStateDescriptor<>("history", ...));
    }

    @Override
    public void notifyCheckpointComplete(long checkpointId) throws Exception {
        // Checkpoint完成后：通知Debezium引擎Offset已持久化
    }
}
```

#### 步骤2：追踪FlinkOffsetBackingStore的实现

```java
// 源码路径:
// flink-connector-debezium/src/main/java/org/apache/flink/cdc/debezium/
//   internal/FlinkOffsetBackingStore.java

public class FlinkOffsetBackingStore implements OffsetBackingStore {
    
    // 由DebeziumSourceFunction.initializeState传入
    public void setFlinkState(ListState<byte[]> offsetState) {
        this.flinkOffsetState = offsetState;
    }

    @Override
    public void set(Map<ByteArray, byte[]> values, Callback<byte[]> callback) {
        // Debezium引擎调用：保存位点
        try {
            for (Map.Entry<ByteArray, byte[]> entry : values.entrySet()) {
                flinkOffsetState.add(entry.getValue());
                // ↑↑↑ 不是写入文件，而是写入Flink Managed State ↑↑↑
            }
            callback.completed(null, null);
        } catch (Exception e) {
            callback.completed(null, e);
        }
    }

    @Override
    public void start() {
        // 从Flink State恢复位点
        if (flinkOffsetState != null && flinkOffsetState.get().iterator().hasNext()) {
            for (byte[] offset : flinkOffsetState.get()) {
                // 恢复每个位点
            }
        }
    }
}
```

#### 步骤3：追踪FlinkDatabaseSchemaHistory

```java
// 源码路径:
// flink-connector-debezium/.../FlinkDatabaseSchemaHistory.java

public class FlinkDatabaseSchemaHistory extends AbstractDatabaseHistory {
    
    // 与FlinkOffsetBackingStore类似，将Schema History存储到Flink State
    // 而不是默认的文件

    @Override
    public void start() {
        super.start();
        // 从Flink State恢复所有DDL记录
        for (String ddl : recoveredHistory) {
            // 回放DDL，恢复表的Schema
        }
    }

    @Override
    protected void storeRecord(HistoryRecord record) {
        // 将DDL记录存储到Flink State
        historyState.add(record.toString());
    }
}
```

#### 步骤4：在IDEA中设置断点观察Handover的行为

```bash
# 在以下代码设置断点并运行FlinkCDCJsonDemo:
# 
# 1. Handover.produce():
#    Debezium线程将Binlog事件放入Handover时暂停
#    → 观察的是"Debezium解析了哪些事件"
#
# 2. Handover.pollNext():
#    Flink主线程从Handover取事件时暂停
#    → 观察的是"Flink正在处理哪些事件"
#
# 3. DebeziumSourceFunction.notifyCheckpointComplete():
#    Checkpoint完成时暂停
#    → 观察Checkpoint的完成时机
#
# 4. FlinkOffsetBackingStore.set():
#    Offset被写入Flink State时暂停
#    → 观察保存的Offset内容（Binlog文件名+位置+GTID）
```

**输出示例——Offset State内容：**
```
[{"file":"mysql-bin.000042","pos":12345,"gtid":"a2b3c4d5-e6f7:1-42","server_id":1}]
```

#### 常见陷坑及解决方法

| 问题 | 原因 | 解决方法 |
|------|------|---------|
| Debezium Engine启动失败 | 缺少JDBC驱动或Debezium连接参数错误 | 检查`properties`配置是否正确传递给Debezium |
| Handover队列满导致Debezium阻塞 | Flink处理速度 < Debezium生产速度 | 调大`max.queue.size`或增大Flink并行度 |
| 恢复后Binlog位点丢失 | FlinkOffsetBackingStore的State未正确恢复 | 检查Checkpoint状态路径是否一致 |
| Schema History恢复失败 | DDL信息未正确存储在Flink State中 | 增加`database.history`的容量配置 |

---

## 4 项目总结

### Debezium集成核心组件

| 组件 | 作用 | 为什么使用Flink State替代 |
|------|------|------------------------|
| Handover | 线程间事件交付 | 避免数据竞争，支持反压传导 |
| FlinkOffsetBackingStore | Offset持久化 | 文件存储不可靠，Flink State与Checkpoint集成 |
| FlinkDatabaseSchemaHistory | Schema历史持久化 | 同理，文件存储不可靠 |
| DebeziumEngine | Binlog解析引擎 | 核心引擎，Flink CDC直接嵌入运行 |

### Handover vs 其他线程间通信方式

| 方式 | 是否阻塞 | 是否支持背压 | 复杂度 |
|------|---------|-------------|-------|
| BlockingQueue | ✅阻塞 | ✅ | 低 |
| Handover（自定义） | ✅阻塞 | ✅ | 中 |
| Pipe（PipedInputStream/OutputStream） | ✅阻塞 | ⚠️ | 高 |
| Disruptor RingBuffer | ❌非阻塞 | ❌ | 高 |

Handover是Flink CDC专门为Debezium场景设计的——它既提供"生产者-消费者"的阻塞队列语义，又在Flink取消作业时能正确中断Debezium线程。

### 思考题

1. **进阶题①**：`DebeziumSourceFunction`实现了`CheckpointedFunction`和`CheckpointListener`两个接口。`snapshotState()`和`notifyCheckpointComplete()`在Checkpoint生命周期中分别在哪两个时间点被调用？如果`snapshotState()`成功但`notifyCheckpointComplete()`失败，Debezium的Offset会怎样？

2. **进阶题②**：Flink CDC 3.x在Pipeline API中不再使用`DebeziumSourceFunction`（它是Flink 1.x的SourceFunction API），而是用FLIP-27的`IncrementalSource`。但在`IncrementalSource`中，Debezium引擎被放在了`BinlogSplitReader`中。这两种实现方式在生产者和消费者模型上有什么核心差异？提示：对比`Handover`机制和`SplitReader.fetch()`的阻塞模型。

---

> **下一章预告**：第34章「SchemaOperator与分布式协调」——深入Flink CDC Pipeline的核心——Schema变更的协调处理。剖析`SchemaOperator`的Coordinator设计、`SchemaRegistry`/`SchemaManager`/`SchemaDerivator`的协作流程。
