# 第6章：Snapshot 快照机制全解

## 1. 项目背景

"新业务上线，需要把现有的 8000 万行订单数据同步到数据仓库——"这是小胖接到的需求。他信心满满地用上一章学到的 `snapshot.mode=initial` 启动了 Connector，30 分钟后 Connector 状态显示 FAILED。日志中赫然写着：`Lock wait timeout exceeded; try restarting transaction`。原来，`snapshot.mode=initial` 的全量快照默认会对表加读锁，而订单表是 7×24 小时持续写入的热点表，锁持有超过 MySQL 默认的 `innodb_lock_wait_timeout=50s` 时，快照失败，还导致部分业务请求超时。

这就是快照策略选择不当的代价。Debezium 提供了 6 种快照模式，每一种对应不同的"锁 vs 可用性"的权衡。选错了，轻则快照耗时翻倍，重则导致线上事故。本章将从快照的原理讲起，逐步深入每种模式的适用场景、锁机制和恢复能力。

### 痛点放大

快照策略选型失误的常见灾难：

- **大表 + initial 快照 = 锁表事故**：8000 万行表，`snapshot.mode=initial` 加锁期间（即使 `locking.mode=minimal` 也会持有短暂的全局锁），业务写入全部阻塞
- **快照中断后无法恢复**：快照读到第 6000 万行时 Connector 崩溃，重启后 offset 丢失，只能重头再来，浪费数小时
- **`snapshot.mode=never` 误用**：新建的 CDC 链路选了 `never`，streaming 模式只能捕获增量变更，表里原有的存量数据"消失"了，下游数据仓库缺少历史记录

---

## 2. 项目设计——三人对话

**（深夜加班，小胖盯着报错日志）**

**小胖**："大师救命！我的 Connector 快照失败了，日志里是 `Lock wait timeout`。8000 万行的表我是不是永远同步不了了？"

**大师**："别慌。快照策略有 6 种，你选了最暴力的一种。我先打个比方——你想抄一本 8000 页的书，你说'把整本书拿到复印机前，一次性复印完'，这就是 `initial` 快照。但如果这本书是活页的（有人随时在加页、改页），你就会和加页的人打架。正确的做法是多种模式的灵活选择。"

**小白**："6 种？我之前只知道 initial 和 when_needed。大师你给我们看看全貌？"

**大师**（在白板上列出）：

| snapshot.mode 值 | 行为 | 锁策略 | 适用场景 |
|-----------------|------|--------|---------|
| `initial` | 首次全量快照 + 流式 | minimal | 新链路，表量不大 |
| `when_needed` | offset 有效则跳过，无效则快照 | minimal | 重启/故障恢复 |
| `never` | 永不快照，只流式 | 无锁 | 已有全量数据，只需增量 |
| `initial_only` | 仅快照，完成后停止 | minimal | 一次性数据导出 |
| `schema_only` | 只读表结构，不读数据 | minimal | 测试 Schema，不需数据 |
| `no_data` | 读 Schema + offset，不读行数据 | minimal | 快速重启，跳过数据 |

**小胖**："那我的 8000 万行订单表应该用哪个？如果 initial 不行，这些模式好像都不对？"

**大师**："你说到痛点了。对于大表，传统 6 种模式都面临'锁表 vs 可用性'的困境。但其实还有一种第 7 种模式——**增量快照（Incremental Snapshot）**，它需要配合 signal 表使用。简单的说，增量快照把表按主键切成小块（chunk），一块一块地读，每块之间不持锁。就像不是一次性把 8000 页书全锁起来抄，而是每次只拿 10 页到隔壁去抄，抄完再回来拿 10 页。业务写入几乎不受影响。"

**小白**："那 `snapshot.locking.mode=minimal` 具体是怎么个加锁法？我看日志里说 'acquiring global read lock'，但为什么叫 minimal？"

**大师**："`locking.mode=minimal` 的实际行为是三层锁策略——"
- 第一层：记录当前 binlog 位点（不需要锁）
- 第二层：`FLUSH TABLES WITH READ LOCK`（全局读锁），获取所有表的 Schema 快照，然后立即释放（通常 < 1 秒）
- 第三层：对每张表分别执行 `SELECT * FROM table`（不加锁，因为是 InnoDB 的 MVCC 快照读）

**大师**："所以 minimal 不是 '不加锁'，而是'只在读 Schema 时短暂加锁'。对于 8000 万的大表，Schema 很少，加锁不到 1 秒，对业务几乎无影响。你的问题是 `locking.mode` 设成了其他值（比如 `minimal_percona` 或 `extended`），或者 MySQL 的 `innodb_lock_wait_timeout` 设得太短。"

**小胖**："这么看，是不是只要设成 `minimal` 就万事大吉了？"

**大师**："不是。`minimal` 的代价是——Schema 是快照开始时确定的，快照过程中执行的 DDL（如 ALTER TABLE）不会被快照捕获。如果你的表在快照期间发生了列变更，快照出来的 Schema 可能和后续 streaming 的 Schema 不一致。所以大表推荐增量快照。"

**小白**："那 offset 存储在 Kafka 里的什么位置？如果快照中断了，怎么知道我读到了第几行？"

**大师**（走到白板前画图）：

```
offset 存储层级：
┌─ Kafka Topic: connect-offsets ───────┐
│ Key: ["inventory-connector",{"server":"dbserver1"}]                     │
│ Value: {                              │
│   "transaction_id": null,             │
│   "ts_sec": 1700100000,               │
│   "file": "mysql-bin.000003",         │
│   "pos": 456789,                      │
│   "gtids": "f3b3c7e4-...:1-50",     │
│   "snapshot": true,                   │
│   "snapshot_completed": false,        │
│   "row": 1523345,        ← 当前读到第几行 │
│   "table": "inventory.orders"         │
│ }                                     │
└───────────────────────────────────────┘
```

**技术映射**：`offset.row` = 快照读到第几行了。如果 Connector 崩溃，重启后从 offset 记录的 row 位置继续读，不需要重头开始。

---

## 3. 项目实战

### 环境准备

沿用第2章环境，确认 MySQL 和 Kafka Connect 正常运行。

```bash
docker ps --filter "name=mysql" --filter "name=connect" --filter "name=kafka"
```

### 步骤1：在 MySQL 中创建一张大表用于快照实验

```bash
docker exec mysql mysql -uroot -proot1234 inventory << 'SQL'
DROP TABLE IF EXISTS products;
CREATE TABLE products (
    id INT PRIMARY KEY AUTO_INCREMENT,
    product_code VARCHAR(50) NOT NULL,
    product_name VARCHAR(255) NOT NULL,
    category VARCHAR(100),
    price DECIMAL(10,2),
    stock INT DEFAULT 0,
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
-- 插入 10 万行测试数据
DELIMITER //
CREATE PROCEDURE fill_products()
BEGIN
    DECLARE i INT DEFAULT 1;
    WHILE i <= 100000 DO
        INSERT INTO products (product_code, product_name, category, price, stock, description)
        VALUES (
            CONCAT('SKU-', LPAD(i, 8, '0')),
            CONCAT('Product ', i),
            ELT(FLOOR(1+RAND()*5), 'Electronics', 'Clothing', 'Food', 'Books', 'Sports'),
            ROUND(RAND()*1000, 2),
            FLOOR(RAND()*1000),
            REPEAT('Lorem ipsum dolor sit amet. ', 10)
        );
        SET i = i + 1;
    END WHILE;
END //
DELIMITER ;
CALL fill_products();
DROP PROCEDURE fill_products;
SQL

docker exec mysql mysql -uroot -proot1234 inventory -e "SELECT COUNT(*) FROM products;"
# 预期输出：100000
```

### 步骤2：对比 4 种 snapshot.mode 的行为

**目标**：依次用 `initial`、`when_needed`、`never`、`schema_only` 启动 Connector，观察行为差异。

```bash
# ---- 实验1：snapshot.mode=initial ----
curl -X POST http://localhost:8083/connectors \
  -H "Content-Type: application/json" \
  -d '{
    "name": "snapshot-test-initial",
    "config": {
      "connector.class": "io.debezium.connector.mysql.MySqlConnector",
      "database.hostname": "mysql",
      "database.port": "3306",
      "database.user": "debezium",
      "database.password": "dbz1234",
      "database.server.id": "184061",
      "topic.prefix": "snap1",
      "table.include.list": "inventory.products",
      "schema.history.internal.kafka.bootstrap.servers": "kafka:9092",
      "schema.history.internal.kafka.topic": "schema-changes.snap1",
      "snapshot.mode": "initial",
      "snapshot.locking.mode": "minimal"
    }
  }'

# 监控快照进度
docker logs connect -f 2>&1 | grep -E "Snapshot.*step|Snapshot.*completed|records.*during.*snapshot"
```

**典型日志输出**：
```
Snapshot step 1: Preparing...
Snapshot step 2: Determining captured tables...
Snapshot step 3: Locking captured tables...
Snapshot step 4: Determining snapshot offset...
Snapshot step 5: Reading structure of captured tables...
Snapshot step 6: Exporting data from table 'inventory.products'...
Snapshot step 7: Creating events... (12345 records)
Snapshot completed in 00:00:18
```

```bash
# 验证快照数据量
docker exec kafka kafka-console-consumer --bootstrap-server localhost:9092 \
  --topic snap1.inventory.products --from-beginning --timeout-ms 10000 2>/dev/null | wc -l
# 预期输出：约 100000（与 products 表行数一致）

# 删除 connector
curl -X DELETE http://localhost:8083/connectors/snapshot-test-initial
```

```bash
# ---- 实验2：snapshot.mode=when_needed ----
# 注意：connector 名称保持和实验1相同，复用 Kafka 中的 offset
curl -X POST http://localhost:8083/connectors \
  -H "Content-Type: application/json" \
  -d '{
    "name": "snapshot-test-initial",
    "config": {
      "connector.class": "io.debezium.connector.mysql.MySqlConnector",
      "database.hostname": "mysql",
      "database.port": "3306",
      "database.user": "debezium",
      "database.password": "dbz1234",
      "database.server.id": "184061",
      "topic.prefix": "snap1",
      "table.include.list": "inventory.products",
      "schema.history.internal.kafka.bootstrap.servers": "kafka:9092",
      "schema.history.internal.kafka.topic": "schema-changes.snap1",
      "snapshot.mode": "when_needed"
    }
  }'

# 观察日志——应该跳过 snapshot，直接进入 streaming
docker logs connect 2>&1 | tail -20 | grep -i "snapshot"
# 预期输出：类似 "No snapshot is required" 或直接进入 streaming 阶段

curl -X DELETE http://localhost:8083/connectors/snapshot-test-initial
```

```bash
# ---- 实验3：snapshot.mode=never ----
curl -X POST http://localhost:8083/connectors \
  -H "Content-Type: application/json" \
  -d '{
    "name": "snapshot-test-never",
    "config": { ... "snapshot.mode": "never" ... }
  }'

# 在 MySQL 中实时插入一条数据，验证 streaming 正常工作
docker exec mysql mysql -uroot -proot1234 inventory \
  -e "INSERT INTO products (product_code, product_name, category, price, stock) VALUES ('NEW-001', 'New Product', 'Test', 9.99, 100);"

# 消费验证（只收到新增的一条）
docker exec kafka kafka-console-consumer --bootstrap-server localhost:9092 \
  --topic snap1.inventory.products --from-beginning --max-messages 1

curl -X DELETE http://localhost:8083/connectors/snapshot-test-never
```

```bash
# ---- 实验4：snapshot.mode=schema_only ----
curl -X POST http://localhost:8083/connectors \
  -H "Content-Type: application/json" \
  -d '{
    "name": "snapshot-test-schemaonly",
    "config": { ... "snapshot.mode": "schema_only" ... }
  }'

# 验证：Topic 被创建了，但里面没有数据（只有 Schema 注册到了 Schema Registry）
docker exec kafka kafka-topics --bootstrap-server localhost:9092 --list | grep schemaonly
# Topic 存在

docker exec kafka kafka-console-consumer --bootstrap-server localhost:9092 \
  --topic snap1.inventory.products --from-beginning --max-messages 1 --timeout-ms 5000 2>/dev/null
# 预期：没有数据输出（没有行数据）

curl -X DELETE http://localhost:8083/connectors/snapshot-test-schemaonly
```

### 步骤3：观察快照完成与 Streaming 的切换过程

**目标**：理解 Connector 从快照阶段切换到流式阶段的无缝衔接点。

```bash
# 创建新的 Connector，观察完整的日志流程
curl -X POST http://localhost:8083/connectors \
  -H "Content-Type: application/json" \
  -d '{
    "name": "snapshot-observer",
    "config": {
      "connector.class": "io.debezium.connector.mysql.MySqlConnector",
      "database.hostname": "mysql",
      "database.port": "3306",
      "database.user": "debezium",
      "database.password": "dbz1234",
      "database.server.id": "184062",
      "topic.prefix": "snap2",
      "table.include.list": "inventory.orders",
      "schema.history.internal.kafka.bootstrap.servers": "kafka:9092",
      "schema.history.internal.kafka.topic": "schema-changes.snap2",
      "snapshot.mode": "initial",
      "snapshot.locking.mode": "minimal",
      "snapshot.fetch.size": 5000
    }
  }'

# 实时观察日志
docker logs connect -f --tail 50 2>&1
```

**快照 → 流式切换的典型日志时序**：

```
[timestamp] INFO Starting snapshot for jdbc:mysql://mysql:3306/?...
[timestamp] INFO Snapshot step 6 - Exporting data from table 'inventory.orders'
[timestamp] INFO Exported 5000 records during the snapshot
[timestamp] INFO Exported 10000 records during the snapshot
[timestamp] INFO Snapshot - Final stage: completed
[timestamp] INFO Snapshot completed in 00:00:02
[timestamp] INFO Transitioning from the snapshot to the stream mode
[timestamp] INFO Connected to MySQL binlog at mysql:3306, starting at binlog file mysql-bin.000003, pos=12345
[timestamp] INFO Started streaming changes
```

### 可能遇到的坑及解决方法

| 坑 | 现象 | 根因与解决 |
|----|------|-----------|
| 快照卡住不动 | 日志停在 `Exporting data` 不再前进 | 内存队列满（max.queue.size 过小）。调大 `max.queue.size` |
| 快照中途超时 | `Lock wait timeout exceeded` | `snapshot.locking.mode` 未设为 `minimal`。显式设置 `snapshot.locking.mode=minimal` |
| 快照完成后 offset 丢失 | 重启后重跑快照 | `connect-offsets` Topic 的 `cleanup.policy` 被设为 `delete`。改为 `compact` |
| DDL 在快照期间执行 | 快照数据 Schema 与 streaming 不一致 | 避免快照期间执行 DDL。如不可避免，使用增量快照 |

---

## 4. 项目总结

### 优点 & 缺点

| 维度 | initial | when_needed | never | schema_only |
|------|---------|-------------|-------|-------------|
| 全量数据 | ✅ | ✅（按需） | ❌ | ❌ |
| 锁持有时间 | 短 | 短 | 无 | 短 |
| 重启安全性 | ✅ 从 offset 恢复 | ✅ 从 offset 恢复 | ✅ 从 offset 恢复 | ❌ 无数据 offset |
| 适用大表 | ⚠️ 可能很慢 | ⚠️ 按需触发 | ✅ 不影响 | ✅ |
| 数据完整性 | ✅ 全量 + 增量 | ✅ 存量 + 增量 | ⚠️ 仅增量 | ❌ 无数据 |

### 适用场景

1. **新建 CDC 链路，表 < 100 万行**：`snapshot.mode=initial`
2. **Connector 故障恢复**：`snapshot.mode=when_needed`
3. **已通过其他方式完成全量同步**：`snapshot.mode=never`
4. **测试 Connector 配置是否正确**：`snapshot.mode=schema_only`
5. **大表（> 100 万行）不停机同步**：增量快照（第23章详讲）

### 注意事项

- **offset 持久化依赖 Kafka Topic**：确保 `connect-offsets` Topic 设置了合理的保留策略（`cleanup.policy=compact`）
- **schema history 同样关键**：`schema-changes.xxx` Topic 记录了所有 DDL 历史，和 offset 一样不能丢
- **`snapshot.fetch.size` 不宜过大**：虽然能加快快照，但过大可能导致 Kafka 消息批过大（超过 `max.message.bytes` 默认 1MB）

### 思考题

1. 如果一个表的快照刚完成 80% 时 Connector 所在的机器宕机了，重启后快照是从头开始还是从 80% 继续？什么情况下会从头开始？

2. `snapshot.mode=initial` 和 `snapshot.mode=initial_only` 的区别是什么？假设两者都完成了快照，Kafka Connect 内部的状态有什么不同？

**（第5章思考题答案）**

1. 通过 `database.include.list` 过滤更有效。原因：即使使用 `table.include.list` 只指定了 3 个库的表，Connector 在启动时仍需要连接数据库，获取所有 100 个库的 Schema 元数据（连接建立时调用 `SHOW DATABASES`、`SHOW TABLES`）。使用 `database.include.list` 可以限制 Connector 只连接指定的 3 个库，避免对其他 97 个库的元数据开销。从资源消耗角度，后者能显著降低 MySQL 的连接数、元数据查询开销和内存占用。

2. 选择 `decimal.handling.mode=precise`。三个理由：① BigDecimal 保证任意精度的十进制计算，不会出现 0.1+0.2≠0.3 的浮点误差；② 电商订单涉及金额和分成，一分钱的精度误差累积可能导致财务对账异常；③ 使用 Avro 的 `bytes(decimal)` 类型后，下游 Flink/Spark/ClickHouse 都能原生支持 Decimal 类型，无需额外转换。

---

> **推广提示**：建议 DBA 和运维团队制定内部快照策略规范——明确多大的表、什么时间段、用什么模式执行快照。在变更管理系统中，将"新增 CDC 链路的快照策略选择"作为必填审批项。
