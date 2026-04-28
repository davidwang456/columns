# 第32章：自定义Connector——打造企业级Source/Sink

---

## 1. 项目背景

Flink内置的Connector覆盖了Kafka、JDBC、Elasticsearch、HDFS等常用系统。但真实世界中总有"非标"的存储系统：自研的消息队列、内部配置中心、专有的时序数据库……

如果需要对接这些系统，不能等Flink官方出Connector——需要自己实现。

自定义Flink Source/Sink需要实现的核心接口：

| 接口 | 用途 | 关键方法 |
|------|------|---------|
| `SourceFunction` / `RichSourceFunction` | 自定义Source（已废弃，但易于理解） | `run()`、`cancel()` |
| `ParallelSourceFunction` | 支持并行的Source | 同上 |
| `Source` (新版API，1.12+) | 新版Source接口 | `getSplitEnumerator()`、`createReader()` |
| `SinkFunction` / `RichSinkFunction` | 自定义Sink | `invoke()` |
| `Sink` (新版API，1.15+) | 新版Sink接口 | `createWriter()` |

本章使用新版Source/Sink API（FLIP-27 Source & FLIP-143 Sink）实现一个从Redis读取的自定义Source和写入Redis的自定义Sink。

---

## 2. 项目设计

> 场景：小胖需要用Flink读取公司自研的"Azoth"消息队列——没有现成的Connector。

**大师**：Flink的Source API做了很好的抽象——你只需要实现两个核心组件：SplitEnumerator（在JM端决定"读什么"）和SourceReader（在TM端真正"读数据"）。

**技术映射：新版Source API = Split Enumerator（源管理） + SourceReader（数据读取）。Enumerator决定怎么分片、Reader负责读——两者通过RPC通信。**

**小白**：那Enumerator和Reader之间通信什么？分片信息怎么传递？

**大师**：流程如下：

1. Enumerator检查源头（如Redis有多少分片 / Kafka有多少分区）
2. Enumerator分配"分片（Split）"给各个Reader
3. Reader收到Split后开始读取
4. Reader读取完毕或达到Checkpoint条件，向Enumerator请求新Split

**技术映射：Split = 数据源的一个"最小的可独立读取单元"。对于Kafka是一个分区，对于文件系统是一个文件，对于Redis可能是某个key的Stream。**

**小胖**：那Checkpoint和Exactly-Once怎么处理？自定义Connector要自己实现状态保存？

**大师**：对。新版Source API内置了Checkpoint支持——Reader需要实现`CheckpointListener`和`createCheckpointManifest()`接口，在Checkpoint时保存当前读取位置。

---

## 3. 项目实战

### 分步实现

#### 步骤1：新版Source API——自定义Redis Stream Source

**目标**：实现一个支持并行读取、Checkpoint容错的Redis Stream Source。

```java
package com.flink.column.chapter32.source;

import org.apache.flink.api.connector.source.*;
import org.apache.flink.core.io.SimpleVersionedSerializer;
import org.apache.flink.streaming.api.functions.source.SourceFunction;
import javax.annotation.Nullable;
import java.io.*;
import java.util.*;

/**
 * Redis Stream Source——使用新版的Source API
 * 支持：
 * - 并行读取不同Stream Key
 * - Checkpoint时记录Offset
 * - 从Checkpoint恢复时回溯Offset
 */
public class RedisStreamSource implements Source<String, RedisStreamSource.RedisSplit, RedisStreamSource.RedisEnumeratorState> {

    private final String redisHost;
    private final int redisPort;
    private final List<String> streamKeys;

    public RedisStreamSource(String host, int port, List<String> keys) {
        this.redisHost = host;
        this.redisPort = port;
        this.streamKeys = keys;
    }

    @Override
    public Boundedness getBoundedness() {
        return Boundedness.CONTINUOUS_UNBOUNDED;  // 无限流
    }

    @Override
    public SplitEnumerator<RedisSplit, RedisEnumeratorState> createEnumerator(
            SplitEnumeratorContext<RedisSplit> context) {
        return new RedisSplitEnumerator(context, streamKeys);
    }

    @Override
    public SplitEnumerator<RedisSplit, RedisEnumeratorState> restoreEnumerator(
            SplitEnumeratorContext<RedisSplit> context,
            RedisEnumeratorState checkpointState) {
        return new RedisSplitEnumerator(context, checkpointState);
    }

    @Override
    public SimpleVersionedSerializer<RedisSplit> getSplitSerializer() {
        return new RedisSplitSerializer();
    }

    @Override
    public SimpleVersionedSerializer<RedisEnumeratorState> getEnumeratorCheckpointSerializer() {
        return new RedisEnumeratorStateSerializer();
    }

    @Override
    public SourceReader<String, RedisSplit> createReader(SourceReaderContext context) {
        return new RedisStreamReader(redisHost, redisPort, context);
    }

    // ========== Split ==========
    public static class RedisSplit implements SourceSplit {
        public final String streamKey;
        public final String lastOffset;  // Checkpoint时记录的Offset

        public RedisSplit(String key, String offset) {
            this.streamKey = key;
            this.lastOffset = offset;
        }

        @Override
        public String splitId() {
            return streamKey;
        }
    }

    // ========== Enumerator State ==========
    public static class RedisEnumeratorState implements Serializable {
        public final Map<String, String> splitOffsets;  // StreamKey → Offset

        public RedisEnumeratorState(Map<String, String> offsets) {
            this.splitOffsets = new HashMap<>(offsets);
        }
    }

    // ========== Split Enumerator ==========
    public static class RedisSplitEnumerator
            implements SplitEnumerator<RedisSplit, RedisEnumeratorState> {

        private final SplitEnumeratorContext<RedisSplit> context;
        private final Map<String, String> splitOffsets = new HashMap<>();

        public RedisSplitEnumerator(SplitEnumeratorContext<RedisSplit> ctx, List<String> keys) {
            this.context = ctx;
            keys.forEach(k -> splitOffsets.put(k, "0"));  // 从最开始读
        }

        public RedisSplitEnumerator(SplitEnumeratorContext<RedisSplit> ctx, RedisEnumeratorState state) {
            this.context = ctx;
            this.splitOffsets.putAll(state.splitOffsets);
        }

        @Override
        public void start() {
            // 注册周期性检查——看是否有Reader需要更多Split
            context.callAsync(
                    () -> null,  // 不需要数据
                    (ignored, ctx) -> {
                        // 为所有Reader分配Split
                        context.registeredReaders().forEach((subtask, readerInfo) -> {
                            if (splitOffsets.containsKey("reader_" + subtask)) {
                                return;  // 已经分配了
                            }
                            // 分配所有Redis Stream Key给不同的Reader
                            int idx = 0;
                            for (Map.Entry<String, String> entry : splitOffsets.entrySet()) {
                                if (idx % context.currentParallelism() == subtask) {
                                    context.assignSplit(
                                            new RedisSplit(entry.getKey(), entry.getValue()),
                                            subtask);
                                }
                                idx++;
                            }
                        });
                    },
                    5000, 5000  // 每5秒检查一次
            );
        }

        @Override
        public void handleSplitRequest(int subtaskId, @Nullable String requesterHostname) {
            // 无操作——我们主动分配
        }

        @Override
        public RedisEnumeratorState snapshotState(long checkpointId) {
            return new RedisEnumeratorState(splitOffsets);
        }

        @Override
        public void close() {}
    }

    // ========== Source Reader ==========
    public static class RedisStreamReader
            implements SourceReader<String, RedisSplit> {

        private final String host;
        private final int port;
        private final SourceReaderContext context;
        private final List<RedisSplit> pendingSplits = new ArrayList<>();
        private transient jedis.Jedis jedis;

        public RedisStreamReader(String host, int port, SourceReaderContext ctx) {
            this.host = host;
            this.port = port;
            this.context = ctx;
        }

        @Override
        public void start() {
            this.jedis = new jedis.Jedis(host, port);
        }

        @Override
        public InputStatus pollNext(ReaderOutput<String> output) {
            // 从Redis Stream读取新消息
            for (RedisSplit split : pendingSplits) {
                // XREAD从指定Offset开始读
                List<Map.Entry<String, List<tuple.Tuple<String, String>>>> results =
                        jedis.xread(0, 1000, new StreamEntryID(split.lastOffset), 1000);

                for (Map.Entry<String, List<tuple.Tuple<String, String>>> entry : results) {
                    for (tuple.Tuple<String, String> msg : entry.getValue()) {
                        output.collect(msg.getString());  // 输出消息体
                    }
                }
            }

            // 没有更多数据时返回AVAILABLE（等下一次poll）
            return InputStatus.AVAILABLE;
        }

        @Override
        public List<RedisSplit> snapshotState(long checkpointId) {
            return pendingSplits;  // 返回当前分片和Offset
        }

        @Override
        public void notifyCheckpointComplete(long checkpointId) {}

        @Override
        public CompletableFuture<Void> isAvailable() {
            return CompletableFuture.completedFuture(null);
        }

        @Override
        public void addSplits(List<RedisSplit> splits) {
            pendingSplits.addAll(splits);
        }

        @Override
        public void handleNoMoreSplits() {}

        @Override
        public void close() {
            if (jedis != null) jedis.close();
        }
    }

    // ========== Serializers ==========
    public static class RedisSplitSerializer implements SimpleVersionedSerializer<RedisSplit> {
        @Override public int getVersion() { return 1; }
        @Override public byte[] serialize(RedisSplit split) throws IOException {
            ByteArrayOutputStream baos = new ByteArrayOutputStream();
            ObjectOutputStream oos = new ObjectOutputStream(baos);
            oos.writeUTF(split.streamKey);
            oos.writeUTF(split.lastOffset);
            oos.close();
            return baos.toByteArray();
        }
        @Override public RedisSplit deserialize(int version, byte[] serialized) throws IOException {
            ByteArrayInputStream bais = new ByteArrayInputStream(serialized);
            ObjectInputStream ois = new ObjectInputStream(bais);
            return new RedisSplit(ois.readUTF(), ois.readUTF());
        }
    }

    public static class RedisEnumeratorStateSerializer
            implements SimpleVersionedSerializer<RedisEnumeratorState> {
        @Override public int getVersion() { return 1; }
        @Override public byte[] serialize(RedisEnumeratorState state) throws IOException {
            ByteArrayOutputStream baos = new ByteArrayOutputStream();
            ObjectOutputStream oos = new ObjectOutputStream(baos);
            oos.writeObject(state.splitOffsets);
            oos.close();
            return baos.toByteArray();
        }
        @SuppressWarnings("unchecked")
        @Override public RedisEnumeratorState deserialize(int version, byte[] serialized) throws IOException {
            ByteArrayInputStream bais = new ByteArrayInputStream(serialized);
            ObjectInputStream ois = new ObjectInputStream(bais);
            try {
                return new RedisEnumeratorState((Map<String, String>) ois.readObject());
            } catch (ClassNotFoundException e) {
                throw new IOException(e);
            }
        }
    }
}
```

#### 步骤2：使用自定义Source

```java
// 使用自定义Source
DataStream<String> redisStream = env.fromSource(
        new RedisStreamSource("redis", 6379, Arrays.asList("stream:orders", "stream:payments")),
        WatermarkStrategy.noWatermarks(),
        "redis-stream-source");
```

#### 步骤3：自定义Sink Write API

**目标**：实现新版Sink接口的自定义Redis Sink。

```java
package com.flink.column.chapter32.sink;

import org.apache.flink.api.connector.sink.*;
import org.apache.flink.core.io.SimpleVersionedSerializer;
import org.apache.flink.streaming.api.functions.sink.RichSinkFunction;
import java.io.IOException;
import java.util.*;

/**
 * 新版Sink API——Redis Sink
 * 支持Exactly-Once（通过两阶段提交）
 */
public class RedisSinkV2 implements Sink<String, String, String, Void> {

    private final String host;
    private final int port;

    public RedisSinkV2(String host, int port) {
        this.host = host;
        this.port = port;
    }

    @Override
    public SinkWriter<String, String, String> createWriter(
            InitContext context, List<String> states) {
        return new RedisWriter(host, port, states);
    }

    @Override
    public SimpleVersionedSerializer<String> getWriterStateSerializer() {
        return new StringSerializer();  // 简化的
    }

    @Override
    public SimpleVersionedSerializer<String> getCommittableSerializer() {
        return new StringSerializer();
    }

    @Override
    public SimpleVersionedSerializer<Void> getGlobalCommittableSerializer() {
        return null;
    }

    @Override
    public Committer<String> createCommitter() {
        return new RedisCommitter(host, port);
    }

    // ========== Writer ==========
    private static class RedisWriter implements SinkWriter<String, String, String> {
        private final transient jedis.Jedis jedis;
        private final List<String> pendingCommits = new ArrayList<>();

        RedisWriter(String host, int port, List<String> states) {
            this.jedis = new jedis.Jedis(host, port);
        }

        @Override
        public void write(String element, Context context) {
            // 暂存写入
            pendingCommits.add(element);
        }

        @Override
        public List<String> snapshotState(long checkpointId) {
            return new ArrayList<>(pendingCommits);  // 返回待提交数据
        }

        @Override
        public CompletableFuture<List<String>> prepareCommit(boolean flush) {
            return CompletableFuture.completedFuture(new ArrayList<>(pendingCommits));
        }

        @Override
        public void close() {
            jedis.close();
        }
    }

    // ========== Committer ==========
    private static class RedisCommitter implements Committer<String> {
        private final transient jedis.Jedis jedis;

        RedisCommitter(String host, int port) {
            this.jedis = new jedis.Jedis(host, port);
        }

        @Override
        public List<String> commit(List<String> committables) {
            for (String data : committables) {
                // 实际写入Redis
                jedis.rpush("flink-output", data);
            }
            return Collections.emptyList();  // 不需要重试
        }

        @Override
        public void close() {
            jedis.close();
        }
    }

    private static class StringSerializer implements SimpleVersionedSerializer<String> {
        @Override public int getVersion() { return 1; }
        @Override public byte[] serialize(String obj) { return obj.getBytes(); }
        @Override public String deserialize(int version, byte[] serialized) {
            return new String(serialized);
        }
    }
}
```

#### 步骤4：旧版Source/Sink快速实现

**目标**：对于简单场景，使用旧版API（RichSourceFunction）更便捷。

```java
// 旧版RichSourceFunction（快速开发，但不建议新项目使用）
public class SimpleRedisSource extends RichSourceFunction<String> {
    private volatile boolean running = true;
    private transient jedis.Jedis jedis;

    @Override
    public void open(Configuration parameters) {
        jedis = new jedis.Jedis("redis", 6379);
    }

    @Override
    public void run(SourceContext<String> ctx) throws Exception {
        while (running) {
            // 从Redis List中阻塞读取
            List<String> msgs = jedis.blpop(5000, "source-stream");
            if (msgs != null && msgs.size() == 2) {
                synchronized (ctx.getCheckpointLock()) {
                    ctx.collect(msgs.get(1));
                }
            }
        }
    }

    @Override
    public void cancel() {
        running = false;
    }

    @Override
    public void close() {
        if (jedis != null) jedis.close();
    }
}
```

### 可能遇到的坑

1. **自定义Source在Checkpoint恢复时数据重复消费**
   - 根因：split的Offset没有正确保存到Checkpoint状态
   - 解决：实现`snapshotState()`返回正确的Offset信息；从Checkpoint恢复时Enumerator用保存的Offset创建Split

2. **自定义Sink在Exactly-Once模式下提交不完整**
   - 根因：`prepareCommit()`和`commit()`没有正确实现两阶段提交逻辑
   - 解方：确保prepareCommit返回的数据在checkpoint完成时由Committer真正提交

3. **Enumerator和Reader的序列化/反序列化版本不匹配**
   - 根因：Split的Serializer版本号没有正确管理
   - 解方：使用`SimpleVersionedSerializer`的`getVersion()`管理版本迁移

---

## 4. 项目总结

### 新版Source API核心接口

| 组件 | 职责 | 运行位置 |
|------|------|---------|
| Source | 工厂类，创建其他组件 | Client |
| SplitEnumerator | 管理Split分配、协调Reader | JobManager |
| SourceReader | 读取数据、返回给Flink | TaskManager (每个并行子任务一个) |
| Split | 最小数据分片 | 在Enumerator和Reader之间传递 |
| Serializer | 序列化Split和EnumeratorState | 全局 |

### 新版Sink API核心接口

| 组件 | 职责 |
|------|------|
| Sink | 工厂类 |
| SinkWriter | 写入数据、暂存待提交 |
| Committer | 两阶段提交中的最终提交 |
| GlobalCommitter | 全局提交协调（可选） |

### 注意事项
- 新版Source API（FLIP-27）自Flink 1.12引入，旧版SourceFunction仍可用但已标记为@PublicEvolving
- 自定义Source/Sink的核心难点在于**Checkpoint集成**——确保状态正确保存和恢复
- 对于简单场景（无Checkpoint需求），使用旧版RichSourceFunction更简单

### 常见踩坑经验

**案例1：自定义Source在并行度变化时数据丢失**
- 根因：从Savepoint恢复时并行度发生变化，Enumerator重新分配Split但不知道哪些Split已经被读取过了
- 解方：在EnumeratorState中保存所有Split的Offset信息，重新分配时根据Offset继续

**案例2：自定义Sink每次Checkpoint都重复写入相同数据**
- 根因：Writer的snapshotState()返回了空列表，Committer无法确认哪些数据是新写入的
- 解方：snapshotState()返回当前批次数据的唯一标识；Committer做幂等检查

**案例3：新版Source的Enumerator在执行context.callAsync报NPE**
- 根因：callAsync的第二个参数（回调函数）中访问了context，但context的生命周期管理不当
- 解方：使用context.runInCoordinatorThread()替代；或延迟初始化

### 优点 & 缺点

| | 新版Source/Sink API（FLIP-27/143） | 旧版SourceFunction/SinkFunction |
|------|-----------|-----------|
| **优点1** | Split Enumerator + Reader分离，职责清晰 | 所有逻辑在run()中，代码耦合 |
| **优点2** | 内置Checkpoint集成——snapshotState/restore | 需手动管理CheckpointLock |
| **优点3** | Enumerator运行在JM，全局管理Split分配 | 无法全局协调，各Reader独立运行 |
| **缺点1** | API复杂——需实现4+个接口和Serializer | 简单——继承一个类即可 |
| **缺点2** | 微批模式下Enumerator与Reader通信有延迟 | 直连Source系统，无中间层 |

### 适用场景

**典型场景**：
1. 对接自研消息队列/存储系统——需要实现自定义Source/Sink
2. 需要增强内置Connector功能——如增加Redis Stream Source
3. 需要Checkpoint容错的自定义Source——新API内置状态管理
4. 需要Exactly-Once语义的自定义Sink——新API支持两阶段提交

**不适用场景**：
1. 内置Connector已覆盖的场景（Kafka/JDBC/ES）——直接使用即可
2. 快速原型/简单测试——旧版RichSourceFunction开发更快

### 思考题

1. 新版Source API中，SplitEnumerator运行在JobManager上，SourceReader运行在TaskManager上。如果SplitEnumerator需要访问外部资源（如查数据库获取分片信息），但JM可能和外部资源网络不通——应该怎么处理？

2. 自定义Sink的`prepareCommit()`和`snapshotState()`有什么区别？两者都会保存状态——为什么需要两个不同的方法？

---

> **完整代码**：本章完整代码请参考附录或访问 https://github.com/flink-column/flink-practitioner  
> **思考题答案**：见附录文件 `appendix-answers.md`
