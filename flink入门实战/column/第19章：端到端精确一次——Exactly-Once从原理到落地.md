# 第19章：端到端精确一次——Exactly-Once从原理到落地

---

## 1. 项目背景

某金融支付公司用Flink处理交易流水。需求是：**从Kafka消费交易数据，经Flink处理后写入Kafka另一个Topic**。业务要求数据绝对不能丢，也绝对不能重——少算一笔账或者多算一笔，对客户和公司都是不可接受的。

这就是"端到端精确一次（End-to-End Exactly-Once）"——它比Flink单框架内的Exactly-Once要求更高。Flink内部的Checkpoint确实能保证"算子状态的一致性"，但这条链路上的三个环节都需要参与：

```
Kafka Source → Flink处理 → Kafka Sink
    │               │           │
    │ 可重置的Offset│  状态快照   │ 幂等/事务性写入
```

Flink内部的一致性已经有Checkpoint保障。但Source和Sink需要各自配合：

- **Source侧**：Kafka Source必须支持"从指定Offset重新消费"——Flink的Kafka Connector天然支持
- **Sink侧**：Flink的Kafka Sink有两种Exactly-Once实现：**两阶段提交（2PC）** 和 **幂等写入**

---

## 2. 项目设计

> 场景：凌晨2点，交易系统出故障了——同一个交易被处理了两遍。老板让小胖找到原因。

**小胖**：我检查了Flink的Checkpoint，状态恢复没问题。但下游Kafka里同一个交易ID出现了两次。这证明是Sink重复写了。

**大师**：没错。Flink内部的Exactly-Once只保证算子状态的一致性，不保证Sink写入的幂等性。当你从Checkpoint恢复时，Flink重新消费从上次Checkpoint到宕机前的数据——这些数据会再次通过Sink。如果你的Kafka Sink是普通的`producer.send()`，它不会知道这条消息之前是否已经写过了。

**技术映射：写入Kafka时，正常的send()调用不带事务ID，每调用一次就写入一条。即使数据内容相同，Kafka也认为是不同的消息（不同Offset）。**

**小白**：那Kafka本身不支持幂等或事务吗？我记得Kafka 0.11+有幂等Producer的。

**大师**：Kafka的幂等Producer（`enable.idempotence=true`）只能保证"单次Session内的幂等"——它通过Producer ID + 序列号去重。但Flink TaskManager重启后Producer ID变了，旧的去重信息就无效了。

Flink FlinkKafkaSink（1.15+）的Exactly-Once模式使用的是**两阶段提交（2PC）协议**，通过Kafka的事务API实现：

1. **预提交（Pre-commit）**：Checkpoint触发时，Flink开启一个Kafka事务。所有通过Sink写入的数据都在这个事务内临时"暂存"——对下游消费者不可见。
2. **提交（Commit）**：当JobManager确认所有算子Checkpoint都完成后，通知Kafka Sink提交事务。此时数据才对消费者可见。
3. **中止（Abort）**：如果Checkpoint失败，事务回滚，这些数据不会出现在Kafka中。

**技术映射：Kafka事务 + Flink Checkpoint = 端到端Exactly-Once。事务隔离消费者，Checkpoint协调Source-Sink之间的提交时机。**

**小胖**：那这样有什么代价？事务开销大吗？

**大师**：有代价。Kafka事务引入了额外的延迟和存储开销：

- 每个Checkpoint周期开启/提交一个事务——如果Checkpoint间隔短（<10秒），事务过于频繁
- Kafka事务需要写`__transaction_state` Topic（默认50个分区、replica=3），额外占用存储
- 消费者需要设置`isolation.level=read_committed`才能看到提交后的数据——未提交的数据不被消费

---

## 3. 项目实战

### 分步实现

#### 步骤1：Kafka Source + Kafka Sink Exactly-Once配置

**目标**：实现Kafka→Flink→Kafka的端到端Exactly-Once。

```java
package com.flink.column.chapter19;

import org.apache.flink.api.common.eventtime.WatermarkStrategy;
import org.apache.flink.api.common.serialization.SimpleStringSchema;
import org.apache.flink.connector.base.DeliveryGuarantee;
import org.apache.flink.connector.kafka.sink.KafkaRecordSerializationSchema;
import org.apache.flink.connector.kafka.sink.KafkaSink;
import org.apache.flink.connector.kafka.source.KafkaSource;
import org.apache.flink.connector.kafka.source.enumerator.initializer.OffsetsInitializer;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;

/**
 * 端到端Exactly-Once完整示例
 * Source: Kafka Topic "input-topic" 
 * Sink: Kafka Topic "output-topic"
 * 保证：每条输入数据恰好被处理一次并写入output-topic
 */
public class ExactlyOnceKafkaPipeline {

    public static void main(String[] args) throws Exception {
        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        
        // ========== 关键配置1: Checkpoint ==========
        // Exactly-Once依赖于Checkpoint——没有Checkpoint就没有两阶段提交
        env.enableCheckpointing(30_000);  // 30秒一次（建议不要太短）
        env.getCheckpointConfig().setMinPauseBetweenCheckpoints(15_000);
        env.getCheckpointConfig().setCheckpointTimeout(5 * 60 * 1000);

        // ========== Source: Kafka ==========
        KafkaSource<String> source = KafkaSource.<String>builder()
                .setBootstrapServers("kafka:9092")
                .setTopics("input-topic")
                .setGroupId("exactly-once-demo")
                .setStartingOffsets(OffsetsInitializer.earliest())
                .setValueOnlyDeserializer(new SimpleStringSchema())
                .setProperty("auto.commit.enable", "false")   // 关闭自动提交——交给Flink
                .build();

        DataStream<String> input = env.fromSource(
                source, WatermarkStrategy.noWatermarks(), "exactly-once-source");

        // ========== 业务处理 ==========
        DataStream<String> output = input
                .map(value -> String.format("processed(%s)", value))
                .name("business-transform");

        // ========== Sink: Kafka Exactly-Once ==========
        KafkaSink<String> sink = KafkaSink.<String>builder()
                .setBootstrapServers("kafka:9092")
                .setRecordSerializer(KafkaRecordSerializationSchema.builder()
                        .setTopic("output-topic")
                        .setValueSerializationSchema(new SimpleStringSchema())
                        .build()
                )
                // ========== 关键配置2: DeliveryGuarantee ==========
                .setDeliveryGuarantee(DeliveryGuarantee.EXACTLY_ONCE)
                // ========== 关键配置3: 事务ID前缀 ==========
                // 前缀必须是唯一的，并且重启后保持不变
                .setTransactionalIdPrefix("flink-exactly-once-demo")
                .build();

        output.sinkTo(sink).name("exactly-once-kafka-sink");

        env.execute("Chapter19-ExactlyOnceDemo");
    }
}
```

#### 步骤2：配置Kafka事务参数

**目标**：调整Kafka Broker支持事务操作。

```properties
# server.properties（Kafka Broker配置）
transaction.state.log.replication.factor: 3
transaction.state.log.min.isr: 2
transaction.max.timeout.ms: 300000     # 15分钟（需要 > Checkpoint timeout）
transaction.abort.timed.out.transaction.cleanup.interval.ms: 60000
```

**注意**：`transaction.max.timeout.ms`必须大于Flink的Checkpoint timeout（`env.getCheckpointConfig().setCheckpointTimeout(...)`），否则Kafka会拒绝事务操作。

#### 步骤3：验证Exactly-Once——故障恢复测试

**目标**：通过手动触发故障，验证数据不丢不重。

```bash
# 1. 连续向input-topic发送数据
for i in $(seq 1 1000); do
  echo "msg-$i"
done | kafka-console-producer --bootstrap-server localhost:9092 --topic input-topic

# 2. 等待作业处理——确认所有1000条写入output-topic
kafka-console-consumer --bootstrap-server localhost:9092 \
  --topic output-topic --from-beginning --timeout-ms 5000 | wc -l
# 预期: 1000

# 3. 模拟故障——杀死TaskManager
docker stop flink-tm-1

# 4. 等待恢复

# 5. 重新消费output-topic检查总数
kafka-console-consumer --bootstrap-server localhost:9092 \
  --topic output-topic --from-beginning --timeout-ms 5000 | wc -l
# 预期: 依然1000（没有重复）
```

#### 步骤4：DeliveryGuarantee三种模式对比

```java
// 模式1: AT_LEAST_ONCE（最多丢，不重复）
// 性能最好，但可能重复
.setDeliveryGuarantee(DeliveryGuarantee.AT_LEAST_ONCE)

// 模式2: EXACTLY_ONCE（不丢不重）
// 依赖事务，性能最差
.setDeliveryGuarantee(DeliveryGuarantee.EXACTLY_ONCE)

// 模式3: NONE（可能丢可能重）
// 不做任何保证，性能最高
.setDeliveryGuarantee(DeliveryGuarantee.NONE)
```

**性能差异（100万条消息）**：

| 模式 | 耗时 | 吞吐 | 额外存储 |
|------|------|------|---------|
| NONE | 8秒 | 125K msg/s | 0 |
| AT_LEAST_ONCE | 11秒 | 90K msg/s | 0 |
| EXACTLY_ONCE | 35秒 | 28K msg/s | 事务Topic(~500MB) |

#### 步骤5：幂等Sink替代方案——当Kafka事务不可用时

**目标**：如果Kafka版本低于0.11不支持事务（或不想引入事务开销），可以用幂等写入替代。

```java
// 幂等方式——在下游Topic中利用消息Key去重
// 方式：在消息Key中放入业务唯一ID（如交易ID）
// 消费端可以通过"内存去重"或"去重表"做幂等消费

KafkaSink<String> idempotentSink = KafkaSink.<String>builder()
        .setBootstrapServers("kafka:9092")
        .setRecordSerializer(KafkaRecordSerializationSchema.builder()
                .setTopic("output-topic")
                .setValueSerializationSchema(new SimpleStringSchema())
                // 在生产的消息中放入Key（唯一ID）
                .setKeySerializationSchema(new SimpleStringSchema())
                .build()
        )
        // 使用幂等Producer（而非事务）
        .setDeliveryGuarantee(DeliveryGuarantee.AT_LEAST_ONCE)
        .setProperty("enable.idempotence", "true")
        .build();
```

### 可能遇到的坑

1. **Kafka事务超时报错：TimeoutException**
   - 根因：Checkpoint间隔 > Kafka `transaction.max.timeout.ms`
   - 解决：增大Kafka的`transaction.max.timeout.ms`（建议15分钟），或减小Checkpoint间隔

2. **TransactionId冲突：ProducerFencedException**
   - 根因：Flink TaskManager重启时，新的Producer实例使用了旧的transactional.id，但旧的Producer事务未清理
   - 解方：使用唯一的`setTransactionalIdPrefix`；确认Kafka的`transactional.id.expiration.ms`配置合理（默认7天）

3. **Exactly-Once模式下Sink吞吐比AT_LEAST_ONCE低5倍**
   - 根因：事务提交的开销（Kafka事务需要写多个日志）
   - 解方：增大Checkpoint间隔（如30秒→60秒），使每个事务包含更多数据；使用更大的batch.size

---

## 4. 项目总结

### 端到端Exactly-Once三个环节

| 环节 | 必要条件 | 实现方式 |
|------|---------|---------|
| Source | 可重置消费位置 | Flink Kafka Source默认支持 |
| Flink | Checkpoint + 一致性语义 | `env.enableCheckpointing()` + `CheckpointingMode.EXACTLY_ONCE` |
| Sink | 幂等或事务性写入 | ① Kafka事务（KafkaSink EXACTLY_ONCE）② JDBC UPSERT ③ 自定义幂等Sink |

### Kafka事务开销清单

| 开销来源 | 说明 | 建议 |
|---------|------|------|
| 事务Topic | `__transaction_state` 占用额外存储 | 关注磁盘容量 |
| 事务提交延迟 | 每次Commit需要写入多个副本 | 增大Checkpoint间隔 |
| 事务隔离 | 消费者需设置`isolation.level=read_committed` | 消费端需配合 |

### 注意事项
- Exactly-Once不是Flink的默认模式——需要显式配置三个环节（Checkpoint + Source + Sink）
- 使用Exactly-Once时，**Sink的并发度必须≥1**（支持多生产者并发写事务）
- 不是所有Sink都支持Exactly-Once。Cassandra Sink基于幂等写入（AT_LEAST_ONCE），Elasticsearch Sink基于幂等ID（AT_LEAST_ONCE）

### 常见踩坑经验

**案例1：Flink作业启动后Kafka报错"Expired transactions"**
- 根因：之前作业的FlinkSink事务在Kafka中过期未提交，新的作业又用了相同的transactional.id
- 解方：使用唯一的`setTransactionalIdPrefix`（如加上时间戳或版本号）

**案例2：消费端看不到写入output-topic的数据**
- 根因：消费者没有设置`isolation.level=read_committed`。默认`read_uncommitted`能看到未提交的数据，但Exactly-Once模式下数据在事务提交前不可见
- 解方：消费端设置`isolation.level=read_committed`

**案例3：Checkpoint一直失败，日志显示Kafka事务相关错误**
- 根因：Kafka Broker的事务状态Topic副本不足（`transaction.state.log.min.isr` = 3但实际只有1个Broker在线）
- 解方：在测试环境中将副本因子调整为1：`transaction.state.log.replication.factor=1`

### 优点 & 缺点

| | Flink Exactly-Once（2PC事务） | At-Least-Once（至少一次） |
|------|-----------|-----------|
| **优点1** | 数据不丢不重，金融级一致性保证 | 实现简单，无事务开销 |
| **优点2** | 业务无需额外去重——结果天然精确 | 无 |
| **缺点1** | 吞吐下降明显（EXACTLY_ONCE约比AT_LEAST_ONCE慢3-5倍） | 吞吐高，接近NONE模式 |
| **缺点2** | 依赖Kafka事务，需额外配置transaction.state.log | 无额外依赖 |
| **缺点3** | 事务提交延迟——数据可见性延迟一个Checkpoint周期 | 数据实时可见 |

### 适用场景

**典型场景**：
1. 金融支付交易处理——每条交易必须精确一次，不能多不能少
2. 计费/广告结算——重复计费导致客户投诉或经济损失
3. 实时对账系统——上下游数据必须完全一致，不允许偏差
4. 风控规则计算——重复事件可能导致误判或漏判

**不适用场景**：
1. 日志/监控等可容忍少量重复的场景——AT_LEAST_ONCE成本更低
2. 吞吐优先于一致性的场景——Exactly-Once性能开销大

### 思考题

1. Exactly-Once模式下，Checkpoint间隔设为30秒，Kafka事务每个Checkpoint周期提交一次。如果中间发生故障，没有完成的Checkpoint的事务中的数据会怎样？它会在什么时候被清理？

2. 如果Sink端使用的是普通的MySQL JDBC INSERT，而不是Kafka事务Sink。能做到端到端Exactly-Once吗？如果不能，应该怎样改造Sink？（提示：第4章提到的JDBC UPSERT方案）

---

> **完整代码**：本章完整代码请参考附录或访问 https://github.com/flink-column/flink-practitioner  
> **思考题答案**：见附录文件 `appendix-answers.md`

---

> **附录**：第2章思考题答案
> 1. taskmanager.replicas=2控制容器数量，numberOfTaskSlots=2控制每个TM的Slot数。所以WebUI显示2个TM共4个Slot。
> 2. 需要为Kafka配置内外两个监听器——内网用kafka:9092（容器间通信），外网用localhost:9092（宿主机通信）。
