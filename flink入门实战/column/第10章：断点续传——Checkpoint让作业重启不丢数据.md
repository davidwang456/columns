# 第10章：断点续传——Checkpoint让作业重启不丢数据

---

## 1. 项目背景

某金融公司用Flink做实时风控计算。某天凌晨2点，一个TaskManager的物理机宕机了——硬件故障。Flink自动将故障TaskManager上的任务重新调度到其他健康的TaskManager上。一切似乎恢复得很快。

但第二天早上，风控团队发现问题了：凌晨2:00-2:05之间有**3000笔交易没有被风控检查**，直接放过了系统。虽然没造成实际损失，但风控负责人拍着桌子问：

"你们的Flink不是号称Exactly-Once语义吗？为什么重启之后数据丢了？"

排查结果：Flink成功恢复了状态和Kafka Offset——理论上应该从宕机前的Offset继续消费。但问题在于，从宕机到重新调度之间有约30秒的"空白期"。这30秒内Kafka持续产生了新数据，但Flink没有消费它们。Offset重新定位到停掉前的Checkpoint位置重新消费，这部分数据被重新处理了——看起来"没丢"。

但真正的根源是：**Kafka生产者在这30秒内发送的数据，因为Flink消费者断开连接被Kafka判定为消费失败，生产者重试导致部分数据顺序错乱**。而下游的Sink没有做幂等处理，最终数据产生了重复。

"精确一次"和"数据不丢、不重"之间差了工程上的一整套保障机制。Checkpoint只是其中最基础的一环。

**Checkpoint**是Flink容错机制的基石。它周期性地对所有算子的状态做分布式快照（Snapshot），记录三件事：
1. 每个Source算子的消费位置（如Kafka Offset）
2. 每个算子的当前状态（ValueState/ListState等）
3. 数据流中正在传输但尚未处理的数据（Barrier对齐时在缓存中的数据）

当作业失败时，Flink从最近一个**完成的**Checkpoint恢复——Source Offset回退，算子状态回退，然后重新消费这段时间的数据。

---

## 2. 项目设计

> 场景：上午的复盘会上，小胖被点名解释"丢数据"的问题。

**小胖**：我开了Checkpoint的，env.enableCheckpointing(10000)，10秒一次。TaskManager挂了之后Flink自动恢复了，但处理过的数据还是丢了——这不科学啊。

**大师**：你做了第一步，但没做第二步。Checkpoint只是Flink内部的容错机制，它保证的是"Flink状态的一致性"。但端到端的一致性（End-to-End Exactly-Once）需要**Source和Sink配合**：

- **Source侧**：Kafka Source需要能"回溯"到指定的Offset重新消费。Flink的Kafka Source天然支持。
- **Sink侧**：下游MySQL/Kafka Sink需要支持"幂等写入"或"两阶段提交"。你如果用普通的JDBC INSERT，Checkpoint恢复时数据被重发到Sink，Insert就会造成重复。

**技术映射：端到端Exactly-Once = 可重置的Source + Flink Checkpoint + 幂等/事务性Sink。缺一不可。**

**小白**：Checkpoint本身是怎么做快照的？同时对100个算子拍快照，不会影响正常处理吗？

**大师**：Flink使用**Chandy-Lamport分布式快照算法**的变体——**Barrier对齐**。JobManager周期性地向所有Source注入一条Barrier（屏障）。Barrier在算子间流转：

1. Source收到Barrier后，记录当前Offset，将Barrier广播到所有下游
2. 下游算子收到Barrier时，会等待**所有输入通道**的Barrier到齐——这就是"对齐"阶段
3. 对齐完成后，算子对当前状态做快照，然后向JobManager报告"完成了"
4. JobManager确认所有算子都完成后，确认本次Checkpoint完成

**小胖**：那对齐期间正常数据被堵住了吗？会不会影响吞吐？

**大师**：对，这正是Checkpoint的开销所在。对齐时，先到达Barrier的通道后续数据会被缓存（不处理），直到所有通道的Barrier到齐。这个过程会导致短暂的数据积压——反压。

Flink 1.11+引入了**Unalign Checkpoint**——不对齐，直接拍快照，减少Checkpoint对正常处理的影响。代价是Checkpoint存储量更大（包含所有正在传输中的数据）。

**技术映射：Checkpoint的开销 ≈ 对齐时间 × 数据积压量。Unalign模式牺牲存储空间换取处理连续性。**

**小白**：那我们怎么监控Checkpoint是不是健康的？

**大师**：Flink WebUI上Checkpoint有明确的监控指标：
- **Checkpoint数量**：总数、完成数、失败数
- **持续时间**：最近一次Checkpoint的耗时
- **对齐时间**：Barrier对齐消耗了多少时间（对齐时间过长说明有反压）
- **状态大小**：Checkpoint占用的存储空间

这些指标应该配置到告警中——Checkpoint失败连续超过3次，立即触发告警。

---

## 3. 项目实战

### 分步实现

#### 步骤1：配置Checkpoint——最小值配置

**目标**：在已有作业中添加完整的Checkpoint配置。

```java
package com.flink.column.chapter10;

import org.apache.flink.api.common.eventtime.WatermarkStrategy;
import org.apache.flink.api.common.serialization.SimpleStringSchema;
import org.apache.flink.api.java.tuple.Tuple2;
import org.apache.flink.configuration.Configuration;
import org.apache.flink.connector.kafka.source.KafkaSource;
import org.apache.flink.connector.kafka.source.enumerator.initializer.OffsetsInitializer;
import org.apache.flink.runtime.state.hashmap.HashMapStateBackend;
import org.apache.flink.streaming.api.CheckpointingMode;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.environment.CheckpointConfig;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import java.time.Duration;

/**
 * 完整的Checkpoint配置示例
 */
public class CheckpointConfigDemo {

    private static final Logger LOG = LoggerFactory.getLogger(CheckpointConfigDemo.class);

    public static void main(String[] args) throws Exception {
        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();

        // ============== Checkpoint 基础配置 ==============
        // 启用Checkpoint：每10秒触发一次
        env.enableCheckpointing(10_000);

        // 设置模式：EXACTLY_ONCE（默认）或 AT_LEAST_ONCE
        env.getCheckpointConfig().setCheckpointingMode(CheckpointingMode.EXACTLY_ONCE);

        // ============== Checkpoint 高级配置 ==============
        // 超时时间：Checkpoint超过10分钟未完成则失败
        env.getCheckpointConfig().setCheckpointTimeout(Time.minutes(10).toMilliseconds());

        // 最小时间间隔：两次Checkpoint之间至少间隔5秒
        // 防止数据量太密集时Checkpoint过于频繁
        env.getCheckpointConfig().setMinPauseBetweenCheckpoints(5000);

        // 最大并发Checkpoint数：同一时刻最多1个Checkpoint在进行
        env.getCheckpointConfig().setMaxConcurrentCheckpoints(1);

        // Checkpoint失败后作业的行为：失败次数上限
        // setTolerableCheckpointFailureNumber(3): 容忍3次连续失败
        env.getCheckpointConfig().setTolerableCheckpointFailureNumber(3);

        // ============== 外部化 ==============
        // 作业cancel后保留Checkpoint（默认DELETE_ON_CANCELLATION）
        env.getCheckpointConfig().setExternalizedCheckpointCleanup(
                CheckpointConfig.ExternalizedCheckpointCleanup.RETAIN_ON_CANCELLATION);

        // ============== Unalign Checkpoint ==============
        // 当对齐超时时自动切换为Unalign模式
        env.getCheckpointConfig().setAlignedCheckpointTimeout(Duration.ofSeconds(30));
        // 强制使用Unalign Checkpoint（不推荐，通常用上面）
        // env.getCheckpointConfig().enableUnalignedCheckpoints();

        // ============== State Backend ==============
        // HashMap（内存）
        env.setStateBackend(new HashMapStateBackend());
        // Checkpoint存储路径（HDFS或本地文件系统）
        env.getCheckpointConfig().setCheckpointStorage("file:///tmp/flink-checkpoints");

        // ============== 定义作业 ==============
        KafkaSource<String> source = KafkaSource.<String>builder()
                .setBootstrapServers("kafka:9092")
                .setTopics("event-topic")
                .setGroupId("ch10-checkpoint-demo")
                .setStartingOffsets(OffsetsInitializer.earliest())
                .setValueOnlyDeserializer(new SimpleStringSchema())
                .build();

        DataStream<String> stream = env.fromSource(
                source, WatermarkStrategy.noWatermarks(), "kafka-source");

        stream.map(line -> Tuple2.of(line, 1))
              .returns(Types.TUPLE(Types.STRING, Types.INT))
              .keyBy(t -> t.f0)
              .sum(1)
              .print();

        LOG.info("Checkpoint配置完成，提交作业...");
        env.execute("Chapter10-CheckpointConfigDemo");
    }
}
```

#### 步骤2：模拟故障恢复

**目标**：通过杀死TaskManager来模拟故障，验证Checkpoint自动恢复。

**前置条件**：第2章的Docker Compose环境已启动。

```bash
# 1. 提交带Checkpoint配置的作业
docker exec flink-jm flink run -c com.flink.column.chapter10.CheckpointConfigDemo \
  /jobs/flink-practitioner.jar

# 2. 确认作业正常运行——检查WebUI
curl -s http://localhost:8081/jobs | python -m json.tool

# 3. 待Checkpoint完成几次后（确保至少1个completed checkpoint）
#    在WebUI中 Checkpoints 标签页看到 COMPLETED

# 4. 手动杀死一个TaskManager
docker stop flink-tm-1

# 5. 观察Flink WebUI——作业状态变为 RESTARTING
#    JobManager会检测到TaskManager丢失，将受影响的任务重新调度到flink-tm-2

# 6. 等待作业恢复为 RUNNING

# 7. 重启被杀的TaskManager
docker start flink-tm-1

# 8. 验证数据连续性——恢复后的作业从最近的Checkpoint继续处理
#    在Kafka中生产新数据，确认数据处理无中断
docker exec flink-kafka kafka-console-producer \
  --bootstrap-server localhost:9092 --topic event-topic
# 输入: recovery-test-message

# 9. 查看输出
docker logs flink-tm-2 --tail 20
```

#### 步骤3：从Savepoint/Checkpoint手动恢复

**目标**：掌握手动从Checkpoint恢复作业的两种方式。

**方式A：从Savepoint恢复**（推荐用于版本升级、逻辑变更）

```bash
# 1. 触发Savepoint并停止作业
docker exec flink-jm flink stop --savepointPath /tmp/savepoints <jobId>

# 2. 从Savepoint恢复（代码或配置可能已变更）
docker exec flink-jm flink run -s /tmp/savepoints/savepoint-<id> \
  -c com.flink.column.chapter10.CheckpointConfigDemo \
  /jobs/flink-practitioner.jar
```

**方式B：从Checkpoint恢复**（不推荐手动用，主要是自动恢复场景）

```bash
# 列出Checkpoint
ls /tmp/flink-checkpoints/<jobId>/chk-<number>/

# 从指定Checkpoint恢复
docker exec flink-jm flink run -s \
  file:///tmp/flink-checkpoints/<jobId>/chk-<number>/_metadata \
  -c com.flink.column.chapter10.CheckpointConfigDemo \
  /jobs/flink-practitioner.jar
```

**方式C：从外部化Checkpoint恢复（作业被cancel后保留）**

```
# 如果配置了 RETAIN_ON_CANCELLATION，cancel后Checkpoint保留
docker exec flink-jm flink cancel <jobId>

# 从最近一次外部化Checkpoint恢复（自动查找latest）
docker exec flink-jm flink run -s \
  file:///tmp/flink-checkpoints/<jobId>/latest \
  -c com.flink.column.chapter10.CheckpointConfigDemo \
  /jobs/flink-practitioner.jar
```

#### 步骤4：Checkpoint监控

**目标**：通过REST API获取Checkpoint指标，集成到监控系统。

```bash
# 获取所有Checkpoint状态
curl -s http://localhost:8081/jobs/<jobId>/checkpoints | python -m json.tool

# 获取最近一次Checkpoint详情
curl -s http://localhost:8081/jobs/<jobId>/checkpoints/details/<checkpointId>

# 返回示例
{
  "id": 42,
  "status": "COMPLETED",
  "is_savepoint": false,
  "trigger_timestamp": 1714293932000,
  "duration": 1234,
  "state_size": 8388608,
  "end_to_end_duration": 2345,
  "alignment_buffered": 1024,
  "num_subtasks": 4,
  "num_acknowledged_subtasks": 4,
  "tasks": {
    "total": 4,
    "pending": 0,
    "completed": 4
  }
}
```

**关键告警指标**：

| 指标 | 正常值 | 告警阈值 | 说明 |
|------|--------|---------|------|
| last_checkpoint_duration | <30秒 | >5分钟 | Checkpoint太慢 |
| number_of_failed_checkpoints | 0 | ≥3次 | 连续失败 |
| last_checkpoint_size | 稳定 | 突增>2x | 状态异常膨胀 |
| alignment_buffered | <1MB | >100MB | 对齐反压严重 |

### 可能遇到的坑

1. **Checkpoint一直IN_PROGRESS无法完成**
   - 根因：某些算子Barrier迟迟未到（通常是反压严重或Sink写入慢）
   - 解决：检查Metrics → 增大Checkpoint timeout → 调整Sink批量大小

2. **从Savepoint恢复后报错：Cannot scale down to fewer keys**
   - 根因：Savepoint时的并行度 > 恢复时的并行度。Flink不允许状态rebalance时key数量减少
   - 解方：恢复时使用>=原并行度的设置，或者使用`--allowNonRestoredState`忽略部分状态（但不推荐）

3. **RocksDB增量Checkpoint在HDFS上遗留大量小文件**
   - 根因：RocksDB增量Checkpoint的SST文件散落在HDFS上，持续积累
   - 解方：配置RocksDB的过期文件清理策略，或使用`rocksdb.auto-compaction`

---

## 4. 项目总结

### Checkpoint vs Savepoint

| 维度 | Checkpoint | Savepoint |
|------|-----------|-----------|
| 触发方式 | 自动（周期性） | 手动（用户触发） |
| 存储位置 | 配置的checkpoint storage | 指定的路径 |
| 保留策略 | 自动清理（只保留最近N个） | 持久保留（手动删除） |
| 用途 | 自动故障恢复 | 版本升级、作业调整、手动恢复 |
| 增量支持 | 支持（RocksDB） | 不支持（全量） |

### Checkpoint关键参数速查

| 参数 | 默认值 | 建议值 | 说明 |
|------|--------|--------|------|
| enableCheckpointing(intervalMs) | -1（关闭） | 5000-30000 | 间隔越小恢复越快，但开销越大 |
| setCheckpointTimeout | 10min | 5-30min | 超时时间 |
| setMinPauseBetweenCheckpoints | 0 | 500-5000 | 避免Checkpoint过于密集 |
| setTolerableCheckpointFailureNumber | 0 | 3-5 | 容忍连续失败次数 |
| setExternalizedCheckpointCleanup | DELETE_ON_CANCELLATION | RETAIN_ON_CANCELLATION | Cancel后是否保留 |

### 注意事项
- Checkpoint间隔不能小于`minPauseBetweenCheckpoints + averageCheckpointDuration`，否则Checkpoint堆积
- 使用RocksDB + 增量Checkpoint时，需要预留额外的磁盘空间（约状态大小的2-3倍）
- 生产环境务必设置`RETAIN_ON_CANCELLATION`，避免运维误操作cancel后丢失状态

### 常见踩坑经验

**案例1：Checkpoint大小持续增长，从100MB涨到10GB**
- 根因：State TTL没有配置，或者ListState/MapState中的元素只增不减
- 解方：检查所有StateDescriptor是否配置了TTL；检查是否有算子状态在无界增长

**案例2：Kafka Offset提交冲突导致Checkpoint一直失败**
- 根因：Kafka Consumer的auto.commit.enable=true（默认true）与Flink Checkpoint冲突
- 解方：显示设置 `setProperties(new Properties() {{ put("auto.commit.enable", "false"); }})`，让Flink Checkpoint管理Offset

**案例3：作业恢复时状态与数据不一致——看到聚合值跳变**
- 根因：Sink不是幂等的。Checkpoint恢复时Kafka重放数据，Flink状态也跟着回退再重算，但Sink写入的数据（如MySQL）已经在上一轮就写入了，且没有做幂等处理——现在又写入一次，造成数据翻倍
- 解方：Sink使用"UPSERT"语义（如`INSERT ... ON DUPLICATE KEY UPDATE`）或两阶段提交Sink

### 优点 & 缺点

| | Flink Checkpoint（自动分布式快照） | 无Checkpoint/手动管理Offset |
|------|-----------|-----------|
| **优点1** | 自动周期性快照，故障后秒级恢复 | 故障后手动补数据，耗时数小时 |
| **优点2** | Exactly-Once端到端一致性保障 | 至少一次或最多一次，丢数不可避免 |
| **优点3** | Chandy-Lamport算法对正常处理影响可控 | 无容错或需停机做全量快照 |
| **优点4** | 配合Savepoint实现作业升级/扩容不停机 | 版本升级需停作业重建状态 |
| **缺点1** | Barrier对齐期间产生短暂反压 | 无额外运行时开销 |
| **缺点2** | 需要额外存储空间（HDFS/S3）保存快照 | 无额外存储需求 |

### 适用场景

**典型场景**：
1. 生产环境所有Flink作业——作业必须开启Checkpoint才能保证故障恢复
2. 金融风控/交易处理——需要端到端Exactly-Once一致性保障
3. 需要作业版本升级/并行度变更——配合Savepoint实现不丢失状态
4. 有状态聚合计算——状态一致性和故障恢复依赖Checkpoint

**不适用场景**：
1. 实验性/一次性作业——资源开销不值得，关闭Checkpoint减少I/O
2. 纯无状态转发作业——Source→Sink直传无聚合，故障后重传即可

### 思考题

1. Checkpoint的interval设为10秒，minPauseBetweenCheckpoints设为5秒，averageCheckpointDuration=8秒。实际的Checkpoint间隔是多少？会不会出现Checkpoint排队积压？（提示：用公式 实际间隔 = max(interval, minPause + averageDuration)）

2. 如果你需要做"作业版本升级"（代码变更），但必须保留之前的状态——应该用Checkpoint还是Savepoint？新代码中的State结构如果变了（比如ValueState<Integer>改成了ValueState<String>），从旧Savepoint恢复会怎么样？

---

> **完整代码**：本章完整代码请参考附录或访问 https://github.com/flink-column/flink-practitioner  
> **思考题答案**：见附录文件 `appendix-answers.md`
