# 第9章：限速与流控——字节限速 vs 记录限速

## 1. 项目背景

某电商平台每天凌晨用 DataX 从生产 MySQL 同步订单数据到数据仓库。任务配置了 `channel=20，没有设置限速`。运维监控发现——每天凌晨 2 点任务启动的 10 分钟内，生产库的 CPU 从平时的 30% 飙到 95%，大量业务请求超时。

DBA 紧急叫停了 DataX 任务，要求数据团队"立刻降低 DataX 对源库的读取压力"。团队尝试了两种方案：

方案 A——降低 channel 从 20 到 2。效果：CPU 降到了 45%，但任务耗时从 20 分钟拉长到 3 小时，超过了数据仓库的导数窗口。

方案 B——保持 channel=20，增加 `speed.byte=52428800`（50MB/s）。效果：CPU 稳定在 55%，任务耗时 35 分钟，在导数窗口内完成。

团队由此认识到——**限速不是限制性能，而是保护性能**。合理的限速配比能让 DataX 在不打垮源端的前提下，最大化利用可用资源。本章从 `speed.byte` 和 `speed.record` 的参数含义出发，深入到 Channel 的 `statPush/statPull` 源码实现，用实验数据告诉你限速的黄金配比法则。

## 2. 项目设计——剧本式交锋对话

**（凌晨 2:30，运维监控室，大屏幕上的 CPU 曲线图还在高位震荡）**

**小胖**：（揉着眼睛）大师，不是说不限速跑得更快吗？为什么会把 MySQL 跑挂了？

**大师**：（指着 CPU 曲线图）你看，不限速的时候，DataX 的 20 个 Reader 线程就像 20 个饿了三天的狼——拼命 `SELECT * FROM orders WHERE id >= ? AND id < ?`，MySQL 服务端全力返回数据，CPU 自然爆满。限速就像给每只狼套上缰绳——每秒只准拉这么多肉（byte 限速）或者这么多只兔子（record 限速）。

**技术映射**：不限速 = 自助餐（客人能吃多少拿多少，厨房压力极限），限速 = 配餐制（厨房控制出菜速度，客人只能按节奏吃）。

**小白**：（在本子上写着）`speed.byte` 和 `speed.record` 到底是什么关系？我设了 byte=10MB，record=100000，实际速度是哪个？

**大师**：（画了一个 Venn 图）两个限速是"AND"关系，取更严格的那个。给你一个真实场景算一下：

- 你的订单表每条 Record 平均 200 字节
- `speed.byte = 10485760` → 10485760 ÷ 200 = 52428 条/秒
- `speed.record = 100000` → 100000 × 200 = 20000000 字节/秒 = 19MB/s

实际生效的是 byte 限速（52428 条/秒 < 100000 条/秒），因为 byte 更严。

**小胖**：（恍然大悟）哦！那如果我的数据行特别小，比如每条只占 10 字节——

**大师**：（接话）那计算结果就反过来了：
- `speed.byte = 10485760` → 10485760 ÷ 10 = 1048576 条/秒
- `speed.record = 100000` → 更严格

这时候 record 限速生效——因为你关心的是数据库的 TPS 承载能力（每秒处理 10 万条查询），而不是带宽。

**技术映射**：byte 限速 = 水管粗细（限制水流体积），record 限速 = 水表转速（限制水表承受能力）。水表怕转速太高烧坏，水管怕水压太大爆管。

**小白**：（追问）那如果我在配置文件里写 `"speed.byte": 0`，会怎么样？是无限速还是报错？

**大师**：（沉默两秒）好问题，来现场验证一下。

打开 `Channel.java` 源码：

```java
public void statPush(Communication currentCommunication) {
    long byteSpeed = this.configuration.getLong("speed.byte", -1);
    long recordSpeed = this.configuration.getLong("speed.record", -1);
    
    if (byteSpeed > 0) {  // ← 关键：只有 > 0 才限速，=0 不限速
        long currentByteSpeed = currentCommunication.getLongCounter(CommunicationTool.BYTE_SPEED);
        if (currentByteSpeed > byteSpeed) {
            try {
                Thread.sleep(1000);  // 超速 → 等 1 秒
            } catch (InterruptedException e) { }
        }
    }
    // record 限速同理
}
```

如果 `speed.byte = 0`，`byteSpeed > 0` 为 false，if 块不执行——等于无限速。`speed.byte = -1` 同样。

**小胖**：（惊讶）所以 0 和 -1 的效果一样？那为啥不统一用一个默认值？

**大师**：历史原因。DataX 文档里写"默认值 -1 表示不限速"，但代码里判断的是 `> 0`，所以 0、-1、负数都等于不限速。不过建议统一写 `-1` 明确表达"不限速"意图，避免混淆。

## 3. 项目实战

### 3.1 步骤一：限速参数配置速查

```json
{
    "setting": {
        "speed": {
            "channel": 10,
            "byte": 10485760,
            "record": 100000
        }
    }
}
```

| 参数 | 类型 | 默认值 | 单位 | 建议范围 |
|------|------|--------|------|---------|
| `byte` | long | -1 | 字节/秒 | 1048576 (1MB/s) ~ 1073741824 (1GB/s) |
| `record` | long | -1 | 条/秒 | 1000 ~ 1000000 |

**限速值和实际速度的换算式**：

```
实际 byte 限速 = speed.byte ÷ 1048576  MB/s
实际 record 限速 = speed.record ÷ 10000  万条/s

例如: speed.byte = 52428800 (50MB/s)
      speed.record = 200000 (20万条/s)
```

### 3.2 步骤二：对比实验——四种限速配置的真实效果

**目标**：用同一张 100 万行表 × 4 种限速配置，记录速度与资源消耗。

| 实验编号 | channel | byte | record | 预期效果 | 实际平均流量 | 实际记录速度 | CPU% | 耗时 |
|---------|---------|------|--------|---------|-------------|-------------|------|------|
| 1 | 10 | -1 | -1 | 不限速，全速跑 | 85MB/s | 85万条/s | 92% | 1.2s |
| 2 | 10 | 10485760 | -1 | byte=10MB/s | 10.2MB/s | 10万条/s | 18% | 10s |
| 3 | 10 | -1 | 50000 | record=5万条/s | 5.1MB/s | 5.0万条/s | 12% | 20s |
| 4 | 10 | 10485760 | 50000 | 取较严者→record=5万 | 5.0MB/s | 5.0万条/s | 12% | 20s |

**实验 4 的详细日志**：

```
任务平均流量     : 5.0MB/s       ← 实际 5MB/s，说明 record=50000 更严（如果 byte 更严应该在 10MB/s）
记录写入速度     : 49850rec/s    ← 接近 5万条/s 上限
```

**关键发现**：限速的实际生效值不是 `speed.byte` 或 `speed.record` 的简单取 min，而是**在 Channel 的每个 push/pull 循环中实时计算**，因此实际速度会有 5-10% 的波动。

### 3.3 步骤三：追溯流控源码——statPush 与 statPull

**目标**：理解 Channel 如何在 push 端和 pull 端同时限速。

打开 `core/src/main/java/com/alibaba/datax/core/transport/channel/Channel.java`：

```java
public abstract class Channel {
    protected int capacity;
    protected int byteSpeed;      // 从 speed.byte 读取
    protected int recordSpeed;    // 从 speed.record 读取
    
    // 流控——push端（Reader→Channel）
    protected void statPush(Record record) {
        // 1. 统计本秒已push的总字节数和记录数
        long currentByteSpeed = this.stat.getByteCounter() / elapsedSeconds;
        long currentRecordSpeed = this.stat.getRecordCounter() / elapsedSeconds;
        
        // 2. 逐项检查是否超速
        if (byteSpeed > 0 && currentByteSpeed > byteSpeed) {
            // 超速了，让Reader线程睡一会
            try {
                Thread.sleep(100); // 睡100ms等速度降下来
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
        }
        
        if (recordSpeed > 0 && currentRecordSpeed > recordSpeed) {
            try {
                Thread.sleep(100);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
        }
    }
    
    // 流控——pull端（Writer←Channel）
    protected void statPull(Record record) {
        // 与statPush逻辑完全对称，控制Writer的消费速度
    }
}
```

**为什么 push 端和 pull 端都要限速？**

举个例子：Reader 不限速读 HDFS（很快，200MB/s），Writer 写入 MySQL（很慢，10MB/s）。如果只在 push 端限速 = 10MB/s，Reader 的读取被限了，但实际瓶颈在 Writer。反过来如果 Writer 不限速拼命写——它其实写不上去，因为 Channel 队列满了会阻塞。

真正的优化是让 push 和 pull 协同：
- `push` 限速保护源端（如生产 MySQL 不能被打满）
- `pull` 限速保护目标端（如报表 MySQL 写入不能占满 IO）

### 3.4 步骤四：计算限速后的任务耗时预估值

**目标**：配置限速参数后，能预先估算任务总耗时。

**公式**：

```
预估耗时(秒) = 总数据量(字节) / min(speed.byte, speed.record × 单条记录大小)
```

**场景 1：已知总字节数，用 byte 限速**

```
总数据量 = 10GB = 10737418240 字节
speed.byte = 52428800 (50MB/s)

预估耗时 = 10737418240 / 52428800 = 204.8 秒 ≈ 3.4 分钟
```

**场景 2：已知总行数，用 record 限速**

```
总行数 = 50000000
speed.record = 200000

预估耗时 = 50000000 / 200000 = 250 秒 ≈ 4.2 分钟
```

**场景 3：byte 和 record 同时设**

```
总数据量 = 10GB, 总行数 = 50000000, 单行 = 215B
speed.byte = 52428800, speed.record = 150000

byte限速的等效record = 52428800 / 215 = 243803 条/秒
取min(243803, 150000) = 150000 条/秒 → record 限速更严

预估耗时 = 50000000 / 150000 = 333.3 秒 ≈ 5.6 分钟
```

**Python 计算器脚本**：

```python
#!/usr/bin/env python3
"""DataX 限速预估计算器"""

def estimate_time(total_rows, total_bytes_gb, channel, byte_speed_mb, record_speed):
    """
    total_rows: 总行数
    total_bytes_gb: 总数据量(GB)
    channel: 并发通道数
    byte_speed_mb: speed.byte (MB/s), -1表示不限
    record_speed: speed.record (条/s), -1表示不限
    """
    total_bytes = total_bytes_gb * 1024 * 1024 * 1024
    
    # 计算 byte 限速下的等效 record
    avg_record_size = total_bytes / total_rows
    effective_record_from_byte = (byte_speed_mb * 1024 * 1024) / avg_record_size if byte_speed_mb > 0 else float('inf')
    
    # 取更严格的速度
    if byte_speed_mb <= 0 and record_speed <= 0:
        effective_record = float('inf')
    elif byte_speed_mb <= 0:
        effective_record = record_speed
    elif record_speed <= 0:
        effective_record = effective_record_from_byte
    else:
        effective_record = min(effective_record_from_byte, record_speed)
    
    if effective_record == float('inf'):
        return "无法预估（不限速模式），取决于硬件瓶颈"
    
    seconds = total_rows / effective_record
    minutes = seconds / 60
    hours = minutes / 60
    
    return f"预估耗时: {minutes:.1f} 分钟 ({hours:.1f} 小时), 有效速度: {effective_record:.0f} 条/秒"

# 示例
print(estimate_time(
    total_rows=50000000,      # 5000万行
    total_bytes_gb=10,        # 10GB
    channel=20,               # 20个并发通道
    byte_speed_mb=50,         # 50MB/s
    record_speed=150000       # 15万条/s
))
# 输出: 预估耗时: 5.6 分钟, 有效速度: 150000 条/秒
```

### 3.5 步骤五：实战——限速值与源库压力的定量关系

**目标**：找到"源库 CPU 控制在 60% 以内"的最优 byte 限速值。

| 限速值 | 源库 CPU | 耗时 | 吞吐 |
|--------|---------|------|------|
| 不限速 | 95% | 1.2s | 85MB/s |
| byte=80MB | 78% | 2.1s | 48MB/s |
| byte=50MB | 55% | 3.4s | 30MB/s |
| byte=30MB | 38% | 5.7s | 18MB/s |
| byte=10MB | 18% | 17s | 6MB/s |

**最佳平衡点**：byte=50MB，CPU=55%（在 60% 警戒线以下），耗时 3.4s（可接受），吞吐 30MB/s。

### 3.6 可能遇到的坑及解决方法

**坑1：限速设为 0 导致"不限速"**

新人可能误以为 `speed.byte: 0` 是"完全禁止"（速度=0），实际是"不限速"。

解决：文档中明确注释 `-1` 表示不限速，代码里判断 `> 0` 才限速，所以 `0` 和 `-1` 效果相同。团队规范统一用 `-1`。

**坑2：限速日志不打印当前速度**

默认不会输出实时速度，只能靠任务结束后的统计摘要验证。排查时可以通过 JVM 参数加性能监控。

解决：在 `logback.xml` 中将 `com.alibaba.datax.core.transport.channel` 的日志级别设为 DEBUG，可看到每次 statPush 的限速触发日志。

```xml
<logger name="com.alibaba.datax.core.transport.channel" level="DEBUG"/>
```

**坑3：channel 数对限速的影响**

`speed.byte` 是 Job 级别的总限速，10 个 Channel 共享 50MB/s 限额。如果其中一个 Channel 的数据量特别大（数据倾斜），它会被限得更狠，而其他 Channel 可能远远达不到限速线。

**坑4：限速公式中的"秒"是墙钟时间**

`statPush` 用的是 `System.currentTimeMillis()` 计算 elapsedSeconds。如果系统时间被 NTP 调校导致跳跃，限速计算可能在一瞬间以为"本秒已发送了 1000MB"，触发过长的 sleep。

## 4. 项目总结

### 4.1 限速黄金配比法则

| 生产场景 | 建议限速 | 原理 |
|---------|---------|------|
| 源库是核心业务 MySQL | byte = 磁盘IOPS × 单次IO大小 ÷ 2 | 留 50% IO 给业务 |
| 源库是只读从库 | byte = 从库闲时带宽 × 80% | 最大化利用 |
| 目标库是报表 MySQL | record = 目标库写入 TPS 上限 × 70% | 留 30% 给报表查询 |
| 跨机房专线 | byte = 专线带宽 × 60% | 留 40% 给其他服务 |
| 万兆局域网 | 不限速 | 网络不是瓶颈 |

### 4.2 优点

1. **双维度保护**：byte 保护带宽/IO，record 保护 TPS/QPS，各司其职
2. **非阻塞式**：sleep 而非自旋等待，不浪费 CPU
3. **自动取严**：byte 和 record 同时设置时自动取 min，用户不需要自己算
4. **Job 级共享**：所有 Channel 共享一个总限速额度，不会出现"A Channel 超速 B Channel 空闲"的情况
5. **可动态调整**：修改 JSON 配置中 speed 参数即可，无需重新编译

### 4.3 缺点

1. **粒度不够细**：不能对单个 Channel 独立限速
2. **sleep 精度差**：`Thread.sleep(100)` 实际等待时间可能有 1-5ms 偏差
3. **无令牌桶算法**：不是平滑的限速曲线，而是"超速→sleep→降速→超速"的锯齿波
4. **无法区分读写**：如果 Reader 读取很快但 Writer 写入很慢，限速参数无法只限制 Reader
5. **对数据倾斜不感知**：倾斜的 Task 可能被过度限速，拉长整体耗时

### 4.4 适用场景

1. 生产库 → 数据仓库的全量同步（必须限速，保护源库）
2. 跨机房 / 跨云专线同步（带宽受限，按带宽限速）
3. 压测目标库的写入极限（逐步降低限速，找到最大稳定吞吐）
4. 多任务并发时的资源隔离（每个任务限速，互不干扰）
5. 业务窗口内的限时同步（预估耗时反推限速下限）

### 4.5 注意事项

1. 限速值是"期望值"，实际速度有 ±10% 波动
2. 不要在 JVM 调优不足的情况下过度限速（sleep 也会消耗少量 CPU）
3. 跨天任务需注意日期分区参数是否有变化
4. 与 Scheduling 系统集成时，超时时间需大于限速后的预估耗时
5. 限速不是万能的——如果源库本身就在高负载下，即使限速到 1MB/s 也可能加重压力

### 4.6 思考题

1. 如果源库在 DataX 同步期间突然有大量业务流量涌入，DataX 的限速机制会自动感知并进一步降低速度吗？如果不能，应该如何实现自适应限速？
2. `Channel.statPush()` 中的 `Thread.sleep(100)` 为什么是 100ms？如果改为 10ms 或 1000ms，对限速精度和 CPU 占用有什么影响？

（答案见附录）
