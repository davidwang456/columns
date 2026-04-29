# 第26章 Exactly-Once与幂等写入

## 1 项目背景

### 业务场景：金融级数据一致性要求

某金融公司使用Flink CDC将核心账务系统的数据同步到数仓。审计部门要求：**任何一条交易记录在同步过程中不得丢失、不得重复**。如果1万条转账记录中出现了1条重复，审计就会判定为"重大事故"。

Flink CDC的Checkpoint保证了Flink内部的Exactly-Once，但端到端的Exactly-Once需要Source + Flink + Sink三个环节的配合。Source已经通过Debezium的offset存储支持了数据重放，但**Sink如果没有幂等写入或事务写入，Checkpoint恢复时仍会出现重复**。

### 端到端Exactly-Once的挑战

```
MySQL Binlog:    记录1 → 记录2 → 记录3
    │
Flink CDC Source:  读取(offset=1) → 读取(offset=2) → 读取(offset=3)
    │                [CP1]          [CP2] ▲            [CP3]
    │                                      │ 崩溃在此
    ▼                                      │ 恢复从CP2开始
Kafka Sink:      写1成功 → 写2成功 → 写2重复！→ 写3
                                            ↑
                                      重复数据！
```

**问题：** Checkpoint CP2完成后，Sink已经成功写入了记录2，但由于Flink作业在记录3之前崩溃，恢复后从CP2开始重放——记录2被重复写入。

**解决方案：**
1. **幂等写入**：Kafka Sink使用幂等Producer + 事务，写入2次=写入1次
2. **事务写入**：Kafka Sink在Checkpoint提交时才提交Kafka事务，事务外的数据不可见

---

## 2 项目设计 · 三人交锋对话

**小胖**（抓头）：我第4章就跑了下`env.enableCheckpointing(5000)`，以为就是Exactly-Once了。原来还有这么多门道？

**大师**：`enableCheckpointing`只管Flink内部算子的状态一致性。**端到端Exactly-Once**需要三个层级的保证：

```
层级1：Flink内部一致性（通过Checkpoint）
  Source State + Operator State + Sink State
  → 保证Flink内部不丢不重

层级2：Source端可重放（通过Debezium offset）
  Checkpoint中包含Debezium的Binlog位置
  → 恢复时从正确位置重新读取

层级3：Sink端幂等/事务（通过Sink实现）
  Kafka Sink事务提交 / Doris Label去重
  → 保证写入目标不重复
```

**小胖**：那是不是所有Sink都能做到Exactly-Once？像`print()`这种能吗？

**大师**：不能。不同的Sink有不同的Exactly-Once保证级别：

| Sink类型 | Exactly-Once | 机制 | 场景 |
|---------|-------------|------|------|
| Kafka | ✅ | 事务写入（TwoPhaseCommitSinkFunction） | 大多数CDC场景 |
| Doris | ✅ | Label去重（幂等写入） | OLAP实时分析 |
| MySQL | ✅ | UPSERT幂等写入 | 数据库同步 |
| Iceberg | ✅ | 快照隔离 + 事务提交 | 数据湖 |
| Paimon | ✅ | LSM-Tree去重 | 流式湖存储 |
| print() | ❌ | 无 | 调试 |
| Socket | ❌ | 无 | 调试 |

**小白**：那Kafka的事务写入具体是怎么实现的？Flink的`TwoPhaseCommitSinkFunction`和Kafka事务是什么关系？

**大师**：Kafka的事务写入通过**Two-Phase Commit（2PC）**协议实现：

```
第一阶段——预提交（Pre-commit）：  
  Checkpoint触发 → Sink开启Kafka事务
  → 写入数据到Kafka（在事务中，但未提交，消费者不可见）
  → 将Kafka事务ID保存到Checkpoint State

第二阶段——提交（Commit）：  
  Checkpoint完成 → Sink提交Kafka事务
  → 数据对所有消费者可见
  → 如果提交失败，下次恢复时从Checkpoint重试

如果作业在阶段1和阶段2之间崩溃：
  → 恢复时从Checkpoint加载事务ID
  → 检查该事务是否已提交 → 若未提交则回滚
  → 重放数据到新的事务中
```

**技术映射**：2PC像"网购的付款流程"——第一阶段是"冻结余额"（数据写入但不可见），第二阶段是"确认收货"（数据提交可见）。如果在你冻结余额后、确认收货前手机关机了（作业崩溃），恢复后系统检查"这笔冻结了的交易有没有完成"——没完成就取消冻结（回滚），重新下单（重放）。

---

## 3 项目实战

### 分步实现

#### 步骤1：配置Kafka Exactly-Once写入

```yaml
sink:
  type: kafka
  properties:
    bootstrap.servers: localhost:9092
    # Exactly-Once配置
    transaction.timeout.ms: 600000     # 事务超时时间（默认1小时）
    enable.idempotence: true           # 开启幂等Producer
    
  sink:
    # Kafka Sink Exactly-Once专用配置
    semantic: exactly-once             # exactly-once | at-least-once | none
    # exactly-once = 使用2PC事务写入
    # at-least-once = 可能重复但不会丢
    # none = 可能丢也可能重复
```

Java代码配置Kafka Exactly-Once Sink：

```java
import org.apache.flink.connector.kafka.sink.KafkaSink;
import org.apache.flink.connector.kafka.sink.KafkaRecordSerializationSchema;
import org.apache.flink.streaming.api.datastream.DataStream;

// Kafka Exactly-Once Sink配置
KafkaSink<String> kafkaSink = KafkaSink.<String>builder()
    .setBootstrapServers("localhost:9092")
    .setRecordSerializer(KafkaRecordSerializationSchema.builder()
        .setTopic("cdc_orders")
        .setValueSerializationSchema(
            new SimpleStringSchema())
        .build()
    )
    // 关键配置：Exactly-Once语义
    .setDeliveryGuarantee(
        org.apache.flink.connector.base.DeliveryGuarantee.EXACTLY_ONCE)
    // 事务ID前缀（必须全局唯一）
    .setTransactionalIdPrefix("cdc_orders_")
    .build();

DataStream<String> cdcStream = env.fromSource(...);
cdcStream.sinkTo(kafkaSink);
```

#### 步骤2：验证Kafka Exactly-Once——模拟崩溃

```bash
#!/bin/bash
# 验证Kafka Exactly-Once写入

# 1. 准备验证脚本
echo "=== 验证Kafka Exactly-Once ==="

# 2. 启动消费端（统计消息数量）
docker exec kafka-cdc kafka-console-consumer \
  --topic cdc_orders \
  --bootstrap-server localhost:9092 \
  --from-beginning --timeout-ms 30000 > /tmp/kafka_msgs.txt &

# 3. 在MySQL执行一次INSERT
docker exec mysql-cdc mysql -uroot -proot123 -e "
USE shop;
INSERT INTO orders_full VALUES (10, 'ORD_EXACTLY_ONCE', 1007, 'Exactly-Once Test', 100.00, 'PAID', '13800001010', '验证', NOW(), NOW());
"

# 4. 等待CDC写入Kafka
sleep 5

# 5. 检查Kafka消息数——应该正好1条
COUNT=$(wc -l < /tmp/kafka_msgs.txt)
echo "Kafka received $COUNT messages"
if [ "$COUNT" -eq 1 ]; then echo "✅ Exactly-Once OK"; else echo "❌ DUPLICATE!"; fi
```

#### 步骤3：Doris Label幂等写入

Doris的Stream Load通过**Label机制**实现幂等写入：

```yaml
sink:
  type: doris
  sink:
    label-prefix: cdc_doris_            # Label前缀
    # 提交策略：Flink Checkpoint成功后才Commit Label
    semantic: exactly-once               # exactly-once | at-least-once
```

**Label去重原理：**
```
Flink Checkpoint 1: 生成Stream Load Label = cdc_doris_00001
Doris接收数据, 记录Label cdc_doris_00001
Checkpoint提交成功 → Label最终确认

Flink崩溃, 恢复从CP1
重新发送Label = cdc_doris_00001
Doris检测到Label已存在 → 跳过本次导入（幂等）
```

#### 步骤4：自定义幂等Sink——MySQL Upsert

实现一个基于UPSERT的MySQL幂等Sink：

```java
package com.example;

import org.apache.flink.configuration.Configuration;
import org.apache.flink.streaming.api.functions.sink.RichSinkFunction;

import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;

/**
 * 幂等MySQL Sink —— 使用INSERT ... ON DUPLICATE KEY UPDATE
 * 依赖MySQL主键去重实现幂等
 */
public class IdempotentJdbcSink extends RichSinkFunction<String> {

    private Connection conn;
    private PreparedStatement stmt;

    @Override
    public void open(Configuration parameters) throws Exception {
        conn = DriverManager.getConnection(
            "jdbc:mysql://localhost:3307/shop_backup",
            "cdc_user", "cdc_pass");
        stmt = conn.prepareStatement(
            "INSERT INTO orders_dup (id, order_id, status, amount) " +
            "VALUES (?, ?, ?, ?) " +
            "ON DUPLICATE KEY UPDATE " +
            "status = VALUES(status), " +
            "amount = VALUES(amount)"
        );
    }

    @Override
    public void invoke(String json, Context context) throws Exception {
        // 解析JSON → 设置参数 → execute
        // 同一个id重复执行不会产生重复行
        // 因为ON DUPLICATE KEY UPDATE是幂等操作
    }
}
```

#### 步骤5：验证MySQL Upsert幂等性

```sql
-- 1. 重复执行同一条INSERT
INSERT INTO orders_dup VALUES (1, 'ORD001', 'PAID', 100.00)
ON DUPLICATE KEY UPDATE status = 'PAID', amount = 100.00;
-- 执行2次 → 只有1行

-- 2. 验证幂等
SELECT COUNT(*) FROM orders_dup WHERE id = 1;
-- 结果应为1，不是2
```

#### 常见陷坑及解决方法

| 问题 | 原因 | 解决方法 |
|------|------|---------|
| Kafka事务超时 | `transaction.timeout.ms`小于`transaction.max.timeout.ms` | 设置`transaction.timeout.ms=900000`（15分钟） |
| Kafka事务ID冲突 | 多个作业使用相同的事务ID前缀 | 为每个作业设置唯一的`TransactionalIdPrefix` |
| Doris Label重复导致数据正确但报错 | Label去重返回的HTTP状态码是200但带警告 | 检查Doris FE日志，确认Label状态为"VISIBLE" |
| Exactly-Once场景下吞吐下降 | 2PC事务引入额外延迟 | 使用幂等写入（如UPSERT/Label去重）替代事务写入 |

---

## 4 项目总结

### 端到端一致性保证能力矩阵

| Sink类型 | Exactly-Once | 实现机制 | 性能影响 |
|---------|-------------|---------|---------|
| Kafka | ✅ | 事务2PC | 中（事务提交延迟） |
| MySQL/UPSERT | ✅ | 幂等写入 | 低 |
| Doris | ✅ | Label去重 | 低 |
| Iceberg | ✅ | 快照隔离 | 中 |
| Paimon | ✅ | LSM-Tree去重 | 低 |
| FileSystem | ⚠️ | 依赖下游去重 | 低 |
| Elasticsearch | ⚠️ | `_id`去重 | 低 |

### Exactly-Once配置检查清单

```
□ Source端: Debezium offset存储在Flink State中（已自动支持）
□ Flink端: enableCheckpointing已开启（间隔建议5~30秒）
□ Sink端: 
  □ Kafka: semantic=exactly-once + TransactionalIdPrefix
  □ Doris: semantic=exactly-once + label-prefix
  □ MySQL: INSERT ... ON DUPLICATE KEY UPDATE
  □ Iceberg: 自动支持（快照隔离）
□ Checkpoint配置:
  □ externalizedCheckpoints: RETAIN_ON_CANCELLATION
  □ minPauseBetweenCheckpoints > 0
□ 恢复验证: 从Checkpoint恢复后，数据没有重复和丢失
```

### 注意事项

1. **性能权衡**：Exactly-Once比At-Least-Once吞吐低10~30%。如果业务可以容忍<0.01%的重复（如非关键日志），使用At-Least-Once可提升性能。
2. **事务超时**：Kafka事务的`transaction.timeout.ms`不能超过Broker的`transaction.max.timeout.ms`（默认15分钟）。
3. **幂等设计原则**：Sink的幂等操作必须是"可重入的"——执行1次和执行N次结果相同。

### 常见踩坑经验

**故障案例1：Exactly-Once模式下Kafka事务越来越大**
- **现象**：Kafka Broker日志报错"Transaction Coordinator expired transaction"
- **根因**：Flink Checkpoint间隔太长（5分钟），Kafka事务在Checkpoint前一直不提交，事务日志积累
- **解决方案**：缩短Checkpoint间隔（30秒），或在Sink配置中减小`transaction.timeout.ms`

**故障案例2：恢复后看到重复的Kafka消息**
- **现象**：从Checkpoint恢复后，Kafka Topic中出现少量重复数据
- **根因**：Consumer启用了`isolation.level=read_uncommitted`（默认），读到了未提交的事务数据。重启后已提交的数据被重复读取
- **解决方案**：将Consumer的`isolation.level`设置为`read_committed`，保证只读已提交的数据

**故障案例3：Doris Label前缀冲突导致数据丢失**
- **现象**：两个不同Pipeline使用了相同的`label-prefix`，其中一个Pipeline的数据被Doris的Label去重机制"幂等"掉了
- **根因**：Label在Doris集群级别唯一。不同的Pipeline使用相同的前缀，生成的Label可能冲突
- **解决方案**：为每个Pipeline使用全局唯一的`label-prefix`，如`cdc_order_prod_` + 时间戳

### 思考题

1. **进阶题①**：Flink的`TwoPhaseCommitSinkFunction`的`preCommit()`和`commit()`方法在什么时机被调用？如果`preCommit()`成功但`commit()`失败，Flink重试`commit()`时是否可能导致数据重复？提示：查看源码中的`notifyCheckpointComplete()`方法。

2. **进阶题②**：Kafka的Exactly-Once语义通过事务实现。如果在Flink CDC作业的Checkpoint刚完成（事务已提交）但Sink的`commit()`方法还没被调用时，作业崩溃会发生什么？事务是提交了还是回滚了？提示：查看`KafkaSink`的`postCommit()`实现逻辑。

---

> **下一章预告**：第27章「性能调优：反压诊断与资源配置」——从Flink Web UI的反压指标开始，系统性诊断Flink CDC作业的性能瓶颈。内容包括并行度计算公式、内存配置指南、Debezium引擎参数调优、Source/Sink吞吐匹配策略。
