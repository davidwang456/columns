# 第22章：信号表（Signaling）与通知渠道

## 1. 项目背景

"我们的 orders 表已经增长到 5 亿行了，现在业务方要求新增一张 `order_attachments` 关联表也需要做 CDC 同步。但负责 CDC 运维的同事休假去了——我难道要等他回来才能给新表触发全量快照吗？"

这是大规模 CDC 运维中的一个典型痛点。传统方式下，给新表启动快照需要修改 Connector 配置（`table.include.list` 中添加新表名），然后手动 `POST /connectors/{name}/restart` 重启 Connector。但这个过程有几个问题：一是需要运维权限和 REST API 访问（开发人员不一定有）；二是重启 Connector 会短暂中断所有表的同步（不仅是新表）；三是无法实现自动化——"新表上线→自动触发增量快照"这个需求无法满足。

**信号表（Signaling）** 正是为了解决这个问题而生的。它是 Debezium 2.x 引入的"控制面"机制——通过在数据库的一张特殊表中 INSERT 一条记录，就能远程控制 Connector 的行为：触发增量快照、暂停快照、记录日志水位等。全程不需要碰 Connector 配置，不需要 REST API 权限，更不需要重启 Connector。

## 2. 项目设计——三人对话

**（周四下午，小胖在研究 Debezium 2.7 的 release notes）**

**小胖**："大师，我看到 Debezium 新版本有个叫 Signaling 的功能——通过 INSERT 一条记录就能控制 Connector？这听起来像黑魔法啊！怎么做到的？"

**大师**："原理其实很简单——Connector 在 streaming 阶段不只是读 binlog/WAL，它还会**定期轮询**你指定的一张信号表。如果你在这张表里 INSERT 了一条特定格式的记录，Connector 读到后就会执行对应的操作。就像你在餐厅点菜——服务员（Connector）不仅给你上菜（streaming events），还会时不时走到收银台看一眼新增的点菜单（signal table）。"

**小白**："那信号表的结构是什么样的？不同类型的信号怎么区分？会不会被当成普通数据变更也被捕获了？"

**大师**："三个好问题——"

```sql
-- 信号表的标准 DDL
CREATE TABLE debezium_signal (
    id VARCHAR(42) PRIMARY KEY,
    type VARCHAR(32) NOT NULL,    -- 信号类型（execute-snapshot / pause-snapshot 等）
    data TEXT,                     -- 信号参数（JSON 格式）
    status VARCHAR(32) DEFAULT 'REQUESTED',  -- REQUESTED → PROCESSED
    create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

关于"会不会被捕获"——如果你把信号表也配入了 `table.include.list`，那它 INSERT 的记录确实会被当成普通 CDC 事件发给 Kafka。所以**信号表不能列入 `table.include.list`**。Connector 是"偷偷"去查信号表的，不会把查到的信号记录当成数据变更事件。

**小胖**："都有哪些类型的信号？我目前最想要的是让 Connector 对新表执行增量快照。"

**大师**："目前支持四种信号类型："

| 信号 type | 作用 | data 示例 | 应用场景 |
|-----------|------|----------|---------|
| `execute-snapshot` | 触发增量快照 | `{"data-collections":["inventory.orders"]}` | 新表上线、数据修复 |
| `pause-snapshot` | 暂停正在执行的快照 | `{}` | 高峰期暂停，低峰期恢复 |
| `resume-snapshot` | 恢复暂停的快照 | `{}` | 同上 |
| `log` | 在 Connector 日志中记录水位 | `{"message":"Checkpoint at 3PM"}` | 调试、做标记 |

**小白**："通知渠道（Notification）又是什么？和信号表有什么联系？"

**大师**："通知是信号的'回执'。当你发送信号让 Connector 执行增量快照后，你怎么知道快照完成没有？成功了还是失败了？通知渠道就是 Connector 完成了某个信号操作后的结果汇报——它可以发到 Kafka Topic（`notification.sink.topic.name`），也可以通过 JMX 暴露，甚至可以写日志。"

**技术映射**：信号 = 你给餐厅服务员的任务纸条（"加个汤"）；通知 = 服务员完成任务后微信告诉你"汤好了"。

**小胖**："那快照完成后，我需要手动去查信号表的 status 字段吗？还是自动通知？"

**大师**："通知渠道会自动发送。如果你配置了 `notification.sink.topic.name=debezium-notifications`，快照完成后 Connector 会自动往这个 Kafka Topic 发一条通知消息，包含快照的表名、开始/结束时间、处理行数等信息。"

---

## 3. 项目实战

### 环境准备

```bash
# 创建信号表
docker exec mysql mysql -uroot -proot1234 inventory << 'SQL'
CREATE TABLE IF NOT EXISTS debezium_signal (
    id VARCHAR(42) PRIMARY KEY,
    type VARCHAR(32) NOT NULL,
    data TEXT,
    status VARCHAR(32) DEFAULT 'REQUESTED',
    create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
SQL

# 准备一张测试表
docker exec mysql mysql -uroot -proot1234 inventory << 'SQL'
CREATE TABLE IF NOT EXISTS big_data_test (
    id INT PRIMARY KEY AUTO_INCREMENT,
    name VARCHAR(255) NOT NULL,
    value DECIMAL(10,2),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
-- 快速插入 10 万行
DELIMITER //
CREATE PROCEDURE fill_big_data()
BEGIN DECLARE i INT DEFAULT 1;
    WHILE i <= 100000 DO
        INSERT INTO big_data_test (name, value) VALUES (CONCAT('Row-', i), ROUND(RAND()*10000,2));
        SET i = i + 1;
    END WHILE;
END //
DELIMITER ;
CALL fill_big_data();
DROP PROCEDURE fill_big_data;
SQL
```

### 步骤1：创建支持信号表的 Connector

**目标**：创建一个 Connector，只监控 `orders` 表（不包含信号表），但会轮询信号表。

```bash
curl -X POST http://localhost:8083/connectors \
  -H "Content-Type: application/json" \
  -d '{
    "name": "signal-demo-connector",
    "config": {
      "connector.class": "io.debezium.connector.mysql.MySqlConnector",
      "database.hostname": "mysql",
      "database.port": "3306",
      "database.user": "debezium",
      "database.password": "dbz1234",
      "database.server.id": "184221",
      "topic.prefix": "signal_demo",
      "database.include.list": "inventory",
      "table.include.list": "inventory.orders",
      "schema.history.internal.kafka.bootstrap.servers": "kafka:9092",
      "schema.history.internal.kafka.topic": "schema-changes.signal-demo",
      "snapshot.mode": "schema_only",
      "signal.enabled.channels": "source",
      "signal.data.collection": "inventory.debezium_signal"
    }
  }'

sleep 15
curl http://localhost:8083/connectors/signal-demo-connector/status | python3 -m json.tool
```

**关键参数**：
- `table.include.list` 只包含 `inventory.orders`——信号表不在其中，不会被当成数据源
- `signal.enabled.channels=source`——开启信号功能
- `signal.data.collection=inventory.debezium_signal`——指定信号表的全限定名

### 步骤2：通过信号触发增量快照

**目标**：INSERT 一条信号记录触发对 `big_data_test` 表的增量快照。

```bash
# 发送增量快照信号
docker exec mysql mysql -uroot -proot1234 inventory -e "
  INSERT INTO debezium_signal (id, type, data) VALUES (
    UUID(),
    'execute-snapshot',
    '{\"data-collections\": [\"inventory.big_data_test\"]}'
  );
"

# 观察 Connector 日志中的信号处理
docker logs connect -f --tail 30 2>&1 | grep -E "signal|Signal|incremental|snapshot"
# 预期输出：
# Received signal 'execute-snapshot' with data '{"data-collections":["inventory.big_data_test"]}'
# Starting incremental snapshot for table 'inventory.big_data_test'
# Snapshot chunk 1 completed (5000 rows)
# Snapshot chunk 2 completed (5000 rows)
# ...
# Incremental snapshot 'execute-snapshot' completed

# 确认信号状态已更新
docker exec mysql mysql -uroot -proot1234 inventory -e "SELECT id, type, status FROM debezium_signal;"
# 预期：status 由 REQUESTED 变为 PROCESSED（Debezium 2.5+ 自动更新）
```

### 步骤3：配置通知渠道——快照完成自动通知

**目标**：设置通知渠道，在增量快照完成后 Connector 自动发送通知消息到 Kafka Topic。

```bash
# 修改 Connector 配置，开启通知渠道
curl -X PUT http://localhost:8083/connectors/signal-demo-connector/config \
  -H "Content-Type: application/json" \
  -d '{
    "connector.class": "io.debezium.connector.mysql.MySqlConnector",
    "database.hostname": "mysql",
    "database.port": "3306",
    "database.user": "debezium",
    "database.password": "dbz1234",
    "database.server.id": "184221",
    "topic.prefix": "signal_demo",
    "database.include.list": "inventory",
    "table.include.list": "inventory.orders",
    "schema.history.internal.kafka.bootstrap.servers": "kafka:9092",
    "schema.history.internal.kafka.topic": "schema-changes.signal-demo",
    "snapshot.mode": "schema_only",
    "signal.enabled.channels": "source",
    "signal.data.collection": "inventory.debezium_signal",
    "notification.enabled.channels": "sink",
    "notification.sink.topic.name": "debezium-notifications"
  }'

# 发送另一个信号
docker exec mysql mysql -uroot -proot1234 inventory -e "
  INSERT INTO debezium_signal (id, type, data) VALUES (
    UUID(), 'log', '{\"message\":\"Checkpoint: before peak hours\"}'
  );
"

# 消费通知 Topic
docker exec kafka kafka-console-consumer --bootstrap-server localhost:9092 \
  --topic debezium-notifications --from-beginning --max-messages 3 --timeout-ms 10000 | python3 -m json.tool
```

**通知消息示例**：
```json
{
  "id": "notification-abc123",
  "aggregate_type": "Incremental Snapshot",
  "type": "IN_PROGRESS",
  "additional_data": {
    "data_collections": ["inventory.big_data_test"],
    "chunk_size": 5000,
    "total_rows_estimated": 100000
  },
  "timestamp": 1715600000
}
```

### 步骤4：暂停和恢复快照——高峰期的运维利器

**目标**：在快照进行中发送暂停信号，观察快照暂停；再发送恢复信号。

```bash
# 先发送一个长快照信号
docker exec mysql mysql -uroot -proot1234 inventory -e "
  INSERT INTO debezium_signal (id, type, data) VALUES (
    UUID(), 'execute-snapshot',
    '{\"data-collections\": [\"inventory.big_data_test\"]}'
  );
"

# 等待 5 秒，发送暂停信号
sleep 5
docker exec mysql mysql -uroot -proot1234 inventory -e "
  INSERT INTO debezium_signal (id, type, data) VALUES (UUID(), 'pause-snapshot', '{}');
"

# 观察日志——快照应该在当前 chunk 完成后暂停
docker logs connect 2>&1 | tail -5 | grep -i "pause\|snapshot"

# 发送恢复信号
docker exec mysql mysql -uroot -proot1234 inventory -e "
  INSERT INTO debezium_signal (id, type, data) VALUES (UUID(), 'resume-snapshot', '{}');
"
```

### 步骤5：信号表自动化——定时触发增量快照

**目标**：利用 MySQL Event Scheduler 定时插入信号，实现"每天凌晨 3 点自动对 orders 表执行增量快照"。

```sql
-- 创建定时事件
CREATE EVENT IF NOT EXISTS daily_incremental_snapshot
ON SCHEDULE EVERY 1 DAY STARTS '2024-01-01 03:00:00'
DO
  INSERT INTO debezium_signal (id, type, data) VALUES (
    UUID(), 'execute-snapshot',
    '{"data-collections": ["inventory.orders"]}'
  );
```

### 可能遇到的坑及解决方法

| 坑 | 现象 | 根因 | 解决 |
|----|------|------|------|
| 信号不被处理 | INSERT 后 Connector 无反应 | `signal.data.collection` 配置错误或信号表不存在 | 确认全限定名格式为 `database.table` |
| 信号表被当成数据源 | Kafka 中也收到了信号表的 INSERT 事件 | 信号表名字被 `table.include.list` 包含 | 确保信号表不在 include 列表中 |
| 增量快照的 data-collections 格式错误 | 快照未触发 | JSON 格式错误或表名不在 Connector 范围内 | `data-collections` 必须是数组，表名格式为 `db.table` |
| 高并发下信号表锁竞争 | 信号处理慢 | 多个 Connector 共用一张信号表且高并发写入 | 每个 Connector 使用独立的信号表 |

---

## 4. 项目总结

### 优点 & 缺点

| 维度 | 信号表方式 | REST API 方式 | 直接改配置 restart |
|------|-----------|--------------|-------------------|
| 触发快照 | INSERT 一条记录 | curl POST | 修改 JSON + restart |
| 权限要求 | 数据库 INSERT 权限 | Connect REST API 权限 | Connect REST API + 数据库权限 |
| 自动化 | ★★★★★ 触发器/定时任务 | ★★★☆☆ 需脚本 | ★★☆☆☆ 全手动 |
| 对其他表的影响 | ★★★★★ 无（只快照指定表） | ★★★★★ 无 | ★☆☆☆☆ 重启中断所有表 |
| 可观测性 | ★★★★☆ 通知渠道 | ★★★☆☆ 需轮询 | ★★☆☆☆ 人工确认 |

### 适用场景

1. **新表上线零人工**：DBA 建表 → 自动 INSERT 信号 → Connector 自动触发增量快照
2. **高峰期避让**：白天暂停快照，凌晨恢复，避免高峰时段资源争抢
3. **数据修复**：发现某张表数据不一致 → INSERT 信号触发增量快照做数据重同步
4. **多环境同步**：dev/staging/prod 各自通过信号表触发快照，不需要拆分配置
5. **自动化运维平台**：数据平台的门户中点击"同步此表"→ 自动生成信号 INSERT 语句

### 不适用场景

1. **一次性快速重建**：如果 offset 已经丢失需要全量重建，直接用 REST API restart 更直接
2. **信号表不可用**：如 MySQL 数据库故障期间，信号表无法读写

### 注意事项

- **`signal.data.collection` 的格式是 `database.table`，不是 `topic.prefix.database.table`**
- **信号表不要纳入 `table.include.list`**——否则 Cdc 事件和信号控制混在一起
- **通知渠道的 `sink` 模式需要 Kafka 可用**——如果 Kafka 故障，通知消息会丢失

### 常见踩坑经验

1. **"信号发送成功但快照没触发"**——检查 Connector 的 `snapshot.mode` 是否为 `never`（不能是 `initial_only`），且 `database.include.list` 包含了目标表所在的数据库。
2. **"信号表的 status 字段一直是 REQUESTED"**——Debezium 2.5 以下版本不会自动更新 status，需要手动更新或升级到 2.5+。
3. **"信号 ID 冲突导致主键冲突"**——使用 `UUID()` 函数生成唯一的信号 ID，不要用自增或固定前缀。

### 思考题

1. 如果信号表因为误操作被 TRUNCATE（清空），正在执行的增量快照会受影响吗？信号功能会被禁用吗？如何恢复？

2. 一个 Connector 监控 50 张表，通过信号触发了对其中 2 张表的增量快照。这个信号是插入到源数据库的信号表中还是任何可达的数据库？如果 Connector 连接的数据库和信号表所在的数据库不同，是否可行？

**（第21章思考题答案）**

1. 当 Worker 恢复响应后重新加入 Consumer Group，Leader 检测到成员变化触发 Rebalance。在 Rebalance 过程中，所有 Task 被撤销（revoke），然后 Leader 根据最新的 Worker 列表重新分配 Task。旧 Worker 上的 Task 在新的分配方案中会被"夺走"并分配给正确的 Worker。这个过程中，消费者端的 offset 在 Rebalance 前由 Worker 提交到 `connect-offsets` Topic，新 Worker 从提交的 offset 继续消费。

2. 新 Leader 通过 Kafka Consumer Group 协议的 Leader 选举机制选出——Group Coordinator（Kafka Broker 中的一个组件）选择 Group 中第一个加入的成员作为新 Leader。新 Leader 从 `connect-configs` Topic 读取所有 Connector 的配置，从 `connect-statuses` Topic 读取之前的 Task 分配状态，从 `connect-offsets` Topic 读取当前的 offset 信息。消费者端的 offset 不受 Leader 崩溃影响——因为 offset 持久化在 Kafka 的 `connect-offsets` Topic 中，独立于 Worker 生命周期。

---

> **推广提示**：将信号表的 INSERT 语句封装为存储过程（`sp_trigger_incremental_snapshot(db_name, table_name)`），提供给开发团队和 DBA 使用，降低信号使用的门槛。CI/CD 流水线中增加"新表上线→自动发送信号"的步骤，实现 CDC 的声明式管理。
