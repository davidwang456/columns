# 第13章 Checkpoint与状态恢复

## 1 项目背景

### 业务场景：CDC作业崩溃后如何做到"数据不丢不重"

某天凌晨3点，运维值班同学收到告警：Flink CDC作业`order-sync`状态变为`FAILED`。原因是TaskManager所在节点发生了OOM Kill。

运维同学立刻在Flink Web UI上点击"Cancel"以停止失败的作业，然后重新提交了作业。但恢复后发现：
- Kafka中`order_topic`的**数据量比MySQL多了324条**（重复）
- Elasticsearch中出现了**重复的订单记录**

运营同学上班后投诉："用户说收到了两次'订单已发货'的短信通知！"

这就是典型的**状态恢复失败**导致的重复数据问题。深入理解Flink的Checkpoint机制，才能避免这类问题。

### Checkpoint在Flink CDC中的角色

```
Flink CDC作业正常运行时：
  MySQL Binlog → Source算子 ─→ Transform算子 ─→ Sink算子 → Kafka
                       │              │               │
                       ▼              ▼               ▼
                  记录偏移量      记录中间状态     记录写入位置
                  (offset)       (aggregation)    (transaction)
                       │              │               │
                       └──────────────┼───────────────┘
                                      ▼
                            Checkpoint Coordinator
                            保存到 State Backend
                                    │
                                    ▼
                            HDFS / S3 / RocksDB
```

---

## 2 项目设计 · 三人交锋对话

**小胖**（紧张）：第4章我就`enableCheckpointing(5000)`一行代码，以为Checkpoint就搞定了。没想到还有这么多门道！

**大师**：`enableCheckpointing(5000)`只是开启了Checkpoint功能，但完整的状态恢复涉及一整套机制：

1. **State Backend（状态后端）**——状态存在哪里？
2. **Checkpoint Storage（快照存储）**——快照文件放哪里？
3. **Checkpoint配置**——多久做一次？超时多少？并发几个？
4. **Savepoint（保存点）**——手动的、版本的快照，用于升级和迁移

**小白**：我理解Checkpoint是自动定期的，Savepoint是手动触发的。但Debezium的offset是怎么存的？是不是存在Flink的State里？

**大师**：是的，这是Flink CDC容错的关键。Flink CDC的Source算子（`DebeziumSourceFunction`）内部维护了一个**State**，用于存储Debezium引擎的偏移量（offset）和Schema历史。具体来说：

```java
// DebeziumSourceFunction的内部状态
private ListState<byte[]> offsetState;      // 存储Binlog位点
private ListState<byte[]> historyState;     // 存储Schema历史
```

当Checkpoint触发时：
1. Debezium引擎暂停，将当前Binlog位置写入`offsetState`
2. 将Schema历史写入`historyState`
3. Checkpoint Coordinator确认所有算子状态已持久化
4. Debezium引擎继续读取

当作业恢复时：
1. Flink从最近的Checkpoint加载状态
2. `offsetState`恢复到Checkpoint时的Binlog位点
3. Debezium引擎从该位点重新开始读取Binlog
4. 这样实现了**Source端的Exactly-Once**

**技术映射**：Checkpoint就像"游戏存档"——不仅记录了"你当前在哪（offset）"，还记录了"你的装备（状态）"。Savepoint就是"手动存档"——升级游戏版本（Flink版本）之前，手动存一个档。

**小白**：那如果Checkpoint成功了，但数据还没写到Kafka呢？这种情况会不会丢数据？

**大师**：这取决于**Sink的类型**。Flink CDC的端到端Exactly-Once依赖Sink的写入语义：

| Sink类型 | 行为 | 是否有丢失风险 |
|---------|------|--------------|
| **幂等写入**（如Kafka幂等Producer） | 数据可以重复写，但不丢失 | ✅ 不丢，但可能重复 |
| **事务写入**（如Kafka Exactly-Once Sink） | Checkpoint提交时同时提交Kafka事务 | ✅ 不丢不重 |
| **至少一次写入**（如print()） | 可能重复写 | ⚠️ 可能重复 |
| **非事务写入** | 可能丢也可能重复 | ❌ 有丢失风险 |

所以在生产环境中，Sink最好使用**事务写入**（TwoPhaseCommitSinkFunction）或**幂等写入**。

**小胖**：那State Backend选哪个？Memory、FileSystem、RocksDB有啥区别？

**大师**：选择State Backend取决于你的状态有多大：

```yaml
# MemoryStateBackend（默认，测试用）——状态存在TaskManager堆内存
# 特点：快、状态小（< 256MB）
state.backend: jobmanager

# FsStateBackend（小规模生产）——状态存在TaskManager堆内存，Checkpoint存文件系统
# 特点：适合小状态（< 1GB），Checkpoint存HDFS/S3
state.backend: filesystem
state.checkpoints.dir: hdfs://namenode:8020/flink-checkpoints

# RocksDBStateBackend（大规模生产）——状态存在RocksDB（磁盘），支持增量Checkpoint
# 特点：适合大状态（> 1GB ~ 10TB），增量Checkpoint省时
state.backend: rocksdb
state.backend.incremental: true
state.checkpoints.dir: hdfs://namenode:8020/flink-checkpoints
```

对于Flink CDC场景，状态通常比较小（主要是offset和schema history），所以FileSystem就够用。但如果Source的表非常多（几百张），RocksDB可能更合适。

---

## 3 项目实战

### 环境准备

**Docker Compose新增Flink配置（挂载HDFS依赖或使用本地文件系统）：**

```yaml
jobmanager:
  image: flink:1.20.3
  container_name: flink-jm-cdc
  ports:
    - "8081:8081"
  command: jobmanager
  environment:
    - JOB_MANAGER_RPC_ADDRESS=jobmanager
  volumes:
    - ./lib:/opt/flink/lib
    - ./checkpoints:/tmp/flink-checkpoints  # 本地Checkpoint目录
```

### 分步实现

#### 步骤1：完整的Checkpoint配置

```java
import org.apache.flink.streaming.api.environment.CheckpointConfig;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.contrib.streaming.state.RocksDBStateBackend;

public class CheckpointConfigDemo {

    public static void main(String[] args) throws Exception {
        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();

        // ========== 1. 选择状态后端 ==========
        // 生产环境推荐RocksDB + 增量Checkpoint
        env.setStateBackend(new RocksDBStateBackend(
            "file:///tmp/flink-checkpoints",  // Checkpoint存储路径
            true                              // 启用增量Checkpoint
        ));

        // ========== 2. Checkpoint基础设置 ==========
        // 开启Checkpoint，间隔5秒
        env.enableCheckpointing(5000);

        // ========== 3. Checkpoint高级配置 ==========
        CheckpointConfig cpConfig = env.getCheckpointConfig();
        cpConfig.setCheckpointTimeout(600000);          // 超时10分钟
        cpConfig.setMinPauseBetweenCheckpoints(500);    // 两个Checkpoint最小间隔500ms
        cpConfig.setMaxConcurrentCheckpoints(1);        // 最大并发Checkpoint数
        cpConfig.enableExternalizedCheckpoints(         // 作业取消后保留Checkpoint
            CheckpointConfig.ExternalizedCheckpointCleanup.RETAIN_ON_CANCELLATION
        );
        cpConfig.setCheckpointStorage("file:///tmp/flink-checkpoints");

        // ========== 4. 容错相关 ==========
        cpConfig.setTolerableCheckpointFailureNumber(5); // 容忍5个Checkpoint失败
        cpConfig.enableUnalignedCheckpoints();           // 非对齐Checkpoint（反压严重时）
        cpConfig.setAlignmentTimeout(Duration.ofSeconds(30)); // 对齐超时

        // ========== 5. 重启策略 ==========
        // 固定延迟重启：最多3次，每次间隔10秒
        env.setRestartStrategy(RestartStrategies.fixedDelayRestart(
            3,                           // 最大尝试次数
            org.apache.flink.api.common.time.Time.seconds(10) // 间隔
        ));

        // ========== 6. Source配置（使用最新Checkpoint恢复） ==========
        MySqlSource<String> source = MySqlSource.<String>builder()
            .hostname("localhost").port(3306)
            .databaseList("shop").tableList("shop.orders")
            .username("cdc_user").password("cdc_pass")
            .deserializer(new JsonDebeziumDeserializationSchema())
            .serverId("5400-5401")
            // 默认使用latest()，结合Checkpoint实现断点续传
            .startupOptions(
                org.apache.flink.cdc.connectors.mysql.table.StartupOptions.latest())
            .build();

        env.fromSource(source,
            org.apache.flink.api.common.eventtime.WatermarkStrategy.noWatermarks(),
            "MySQL CDC")
            .print();

        env.execute("Checkpoint Config Demo");
    }
}
```

#### 步骤2：通过Savepoint手动备份和恢复

```bash
# 1. 提交Flink CDC作业（使用application ID或job ID）
flink run -d -c com.example.FlinkCDCJsonDemo \
  /opt/flink/flink-cdc-demo-1.0-SNAPSHOT.jar

# 输出: Job has been submitted with JobID a1b2c3d4e5f6a7b8c9d0e1f2

# 2. 等待作业运行后，手动触发Savepoint
flink savepoint a1b2c3d4e5f6a7b8c9d0e1f2 \
  /tmp/flink-savepoints

# 输出: Savepoint completed. Path: file:/tmp/flink-savepoints/savepoint-a1b2c3...

# 3. 停止作业
flink cancel a1b2c3d4e5f6a7b8c9d0e1f2

# 4. 从Savepoint恢复（适用于Flink版本升级后的作业）
flink run -s file:/tmp/flink-savepoints/savepoint-a1b2c3... \
  -c com.example.FlinkCDCJsonDemo \
  /opt/flink/flink-cdc-demo-1.0-SNAPSHOT.jar

# ========== 从最近的Checkpoint（而非Savepoint）恢复 ==========
# Flink取消命令启用RETAIN_ON_CANCELLATION后，Checkpoint会被保留
flink run -s file:/tmp/flink-checkpoints/a1b2c3.../chk-123 \
  -c com.example.FlinkCDCJsonDemo \
  /opt/flink/flink-cdc-demo-1.0-SNAPSHOT.jar
```

#### 步骤3：验证断点续传——模拟作业崩溃后恢复

```bash
# 1. 在MySQL中插入一条数据
docker exec mysql-cdc mysql -uroot -proot123 -e \
  "USE shop; INSERT INTO orders VALUES (1001, 'ORD_TEST_CP', 999, 'Test Item', 100.00, 'PAID', NOW(), NOW());"

# 2. 观察Flink控制台确认收到了这条数据

# 3. 强杀Flink作业（模拟崩溃）
docker exec flink-jm-cdc flink cancel <job-id>

# 4. 在作业恢复前，再插入一条数据（这条在Binlog中）
docker exec mysql-cdc mysql -uroot -proot123 -e \
  "USE shop; INSERT INTO orders VALUES (1002, 'ORD_TEST_CP2', 999, 'Test Item 2', 200.00, 'PAID', NOW(), NOW());"

# 5. 从Checkpoint恢复
flink run -s file:/tmp/flink-checkpoints/.../chk-1 \
  -c com.example.FlinkCDCJsonDemo \
  /opt/flink/flink-cdc-demo-1.0-SNAPSHOT.jar

# 6. 观察输出——应该先看到ORD_TEST_CP2（从Binlog位置续读），不会重复ORD_TEST_CP
```

#### 步骤4：Debezium offset状态解析

Debezium存储在Flink State中的offset格式：

```java
// DebeziumSourceFunction内部状态的解析
import org.apache.flink.api.common.state.ListState;
import org.apache.flink.runtime.state.FunctionInitializationContext;

// 在Source初始化时加载的offset状态
public class OffsetInspector {

    // Debezium存储的offset格式（二进制序列化）
    // 实际上是一个Map<String, String>，包含：
    public static void describeOffset() {
        System.out.println("Debezium Offset 包含以下信息:");
        System.out.println("  ├── key: 'file'        → value: 'mysql-bin.000042'");
        System.out.println("  ├── key: 'pos'         → value: '12345'");
        System.out.println("  ├── key: 'gtid'        → value: 'a2b3c4d5:1-42'");
        System.out.println("  ├── key: 'server_id'   → value: '1'");
        System.out.println("  ├── key: 'event'       → value: '5' (schema_id)");
        System.out.println("  ├── key: 'ts_sec'      → value: '1714377601'");
        System.out.println("  └── key: 'row'         → value: '0'");
        System.out.println();
        System.out.println("Schema History 包含:");
        System.out.println("  └── 所有DDL语句序列化列表，用于重建Schema");
    }
}
```

#### 常见陷坑及解决方法

| 问题 | 原因 | 解决方法 |
|------|------|---------|
| Checkpoint超时频繁 | 状态太大或Sink写入慢导致Barrier对齐超时 | 启用Unaligned Checkpoint，或增大超时时间 |
| Savepoint恢复后丢失DDL | Schema History状态未正确恢复 | 使用`MemoryDatabaseHistory`（默认），确保Schema History已Checkpoint |
| 恢复后作业从最开始重新读取 | Checkpoint路径配置错误 | 验证`state.checkpoints.dir`路径可访问 |
| RocksDB OOM | 状态过大，RocksDB堆外内存不足 | 配置`state.backend.rocksdb.memory.managed: true` |
| `Failed to rollback to checkpoint` | Checkpoint文件损坏或不完整 | 使用次新的Checkpoint尝试恢复，或从Savepoint恢复 |

---

## 4 项目总结

### State Backend选型对比

| 维度 | Memory | FileSystem | RocksDB (增量) |
|------|--------|-----------|---------------|
| 状态存储位置 | TaskManager堆内存 | TaskManager堆内存 | RocksDB（磁盘） |
| Checkpoint位置 | JobManager堆内存 | HDFS/S3/本地 | HDFS/S3/本地 |
| 最大状态大小 | 256MB | ~1GB | ~10TB |
| 访问速度 | 极快 | 快 | 中（需要序列化/反序列化） |
| 增量Checkpoint | 不支持 | 不支持 | 支持 |
| 恢复速度 | 快 | 快 | 中 |
| 适用场景 | 测试/调试 | 小规模生产 | 大规模生产 |

### Checkpoint优化建议

1. **CDC场景的Checkpoint间隔**：5~30秒。太短（<1秒）导致频繁快照增加开销；太长（>5分钟）导致恢复时丢失较多数据。
2. **恢复测试**：定期（每月）模拟作业崩溃并验证恢复后的数据一致性。很多团队只在发生故障时才处理恢复问题。
3. **Checkpoint存储冗余**：Checkpoint保存到HDFS/S3等可靠存储，不要只用本地磁盘（节点挂了Checkpoint也跟着没了）。
4. **Savepoint用于版本升级**：每次Flink CDC版本升级前，手动触发Savepoint。升级后从Savepoint恢复，避免版本兼容性问题。

### 常见踩坑经验

**故障案例1：Checkpoint积压导致延迟飙升**
- **现象**：Flink CDC作业处理延迟从1秒飙升到5分钟
- **根因**：Checkpoint定时触发后，Barrier从Source发到Sink需要时间。期间数据流暂停等待Barrier对齐。如果Sink写入慢，Barrier也会变慢
- **解决方案**：启用Unaligned Checkpoint（`enableUnalignedCheckpoints()`），允许Barrier跳过已被积压的数据Buffer

**故障案例2：更换State Backend后无法从旧Checkpoint恢复**
- **现象**：从RocksDB切换到FileSystem后，从旧Checkpoint恢复时失败
- **根因**：不同State Backend的状态序列化格式不同，不兼容
- **解决方案**：切换State Backend前手动触发Savepoint（Savepoint是通用格式），从Savepoint恢复

**故障案例3：CHECKPOINT目录权限错误**
- **现象**：Checkpoint写入失败，作业报错`Permission denied`
- **根因**：Flink进程没有HDFS/S3目录的写入权限
- **解决方案**：检查Flink的HDFS配置，确保`hadoop-user`（通常为flink）拥有Checkpoint目录的读写权限

### 思考题

1. **进阶题①**：Flink CDC作业如果使用了`StartupOptions.initial()`（先全量快照再增量流），在全量快照过程中发生了Checkpoint。此时Checkpoint保存的是什么——是全量快照的进度，还是Debezium的offset？如果全量快照完成后作业崩溃，恢复时是重新全量快照还是续传？

2. **进阶题②**：在生产环境中，如果一个Flink CDC作业运行了6个月，Checkpoint数量达到数万个（每5秒一个）。从最早的Checkpoint恢复时，Flink需要遍历所有Checkpoint记录吗？有没有"自动清理过期Checkpoint"的机制？提示：查看`state.checkpoints.num-retained`和`state.checkpoints.cleanup-strategy`配置。

---

> **下一章预告**：第14章「监控初探：日志、Metrics与Flink Web UI」——从Flink Web UI指标解读到自定义Metrics上报，你将学会如何建立Flink CDC作业的可观测性体系。
