# 第23章：增量快照（Incremental Snapshot）深度实战

## 1. 项目背景

"我们的 orders 表现在有 5 亿行了，每次全量快照要跑 8 个小时。上个月一个新业务上线，需要把这 5 亿行数据同步到数据仓库。但问题是——快照期间不能锁表（订单系统 7×24 运行）、不能影响线上交易、快照中断后不能重来。"

传统 `snapshot.mode=initial` 在这种场景下基本不可行：它需要在快照开始时获取全局读锁（即使只是几秒），然后在快照期间依赖于一个固定的 binlog offset 位点——如果快照耗时 8 小时，期间 binlog 可能已经轮换了数十个文件，快照完成时的 streaming 衔接可能因为 binlog 过期而失败。

**增量快照（Incremental Snapshot）** 正是为这个场景而生。它的核心思想是：不一次性锁表全量读，而是**按主键分段（Chunk）逐块读取**，每块之间不持锁；同时通过**水印（Watermark）机制**标记快照开始时 binlog 的位置，快照完成后从水印位点开始消费增量数据，**无缝衔接不丢数据**。

## 2. 项目设计——三人对话

**小胖**："大师，什么叫'按主键分段'？难道不是 SELECT * FROM table 一把梭吗？"

**大师**："全量快照是 `SELECT * FROM orders` 一把梭——但在 5 亿行表面前，这个查询可能需要数小时，而且期间一直持锁（取决于 `snapshot.locking.mode`）。增量快照是把表按主键范围切成小块——第一个块读 `WHERE id BETWEEN 1 AND 10000`，第二个块读 `WHERE id BETWEEN 10001 AND 20000`，以此类推。**每个块之间释放锁**，所以对业务写入几乎无影响。"

**小白**："那水印是干什么的？为什么不用水印就会丢数据？"

**大师**："水印（Watermark）是增量快照最精妙的设计。假设快照从 id=1 读到 id=100000——在读 id=50000 的时候，业务方 `INSERT` 了一条 id=50001 的新数据。如果不用水印，这条 id=50001 的数据会在两种地方出现：一次来自快照的 `SELECT`（读到 50001 时），一次来自 binlog streaming（INSERT 的 binlog 事件）。**水印的作用就是标记'快照开始时 binlog 的位点'，告诉 Connector——快照完成之后，从水印位点开始消费 binlog，并且在消费时自动过滤掉已经在快照中读过的数据**。"

**小胖**："这就像搬家分批发货——你把仓库里的货按编号 1-1000 包成一批，1001-2000 包成第二批，中间来了新货（id=50001），你在清单上标记了新货的入库时间（水印），等搬完旧货再去拿新货。"

**大师**："完全正确！技术映射：分块（Chunk）= 搬家分批发货；水印（Watermark）= 新货清单上的入库标记；自动去重 = 搬货时对清单，已经搬过的就不搬。"

## 3. 项目实战

### 环境准备

```bash
# 创建大表（50 万行测试数据）
docker exec mysql mysql -uroot -proot1234 inventory << 'SQL'
DROP TABLE IF EXISTS products_large;
CREATE TABLE products_large (
    id INT PRIMARY KEY AUTO_INCREMENT,
    name VARCHAR(255) NOT NULL,
    price DECIMAL(10,2),
    stock INT,
    category VARCHAR(100)
);
DELIMITER //
CREATE PROCEDURE fill_large()
BEGIN DECLARE i INT DEFAULT 1;
    WHILE i <= 500000 DO
        INSERT INTO products_large (name, price, stock, category)
        VALUES (CONCAT('Product-', i), ROUND(RAND()*1000,2), FLOOR(RAND()*100), ELT(1+FLOOR(RAND()*4),'A','B','C','D'));
        SET i = i + 1;
    END WHILE;
END //
DELIMITER ;
CALL fill_large();
DROP PROCEDURE fill_large;
SQL

# 创建信号表
docker exec mysql mysql -uroot -proot1234 inventory -e "
  CREATE TABLE IF NOT EXISTS debezium_signal (
    id VARCHAR(42) PRIMARY KEY, type VARCHAR(32) NOT NULL,
    data TEXT, status VARCHAR(32) DEFAULT 'REQUESTED',
    create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
  );"
```

### 步骤1：创建支持增量快照的 Connector

```bash
curl -X POST http://localhost:8083/connectors \
  -H "Content-Type: application/json" \
  -d '{
    "name": "incr-snap-connector",
    "config": {
      "connector.class": "io.debezium.connector.mysql.MySqlConnector",
      "database.hostname": "mysql",
      "database.port": "3306",
      "database.user": "debezium",
      "database.password": "dbz1234",
      "database.server.id": "184231",
      "topic.prefix": "incr_snap",
      "database.include.list": "inventory",
      "table.include.list": "inventory.products_large",
      "schema.history.internal.kafka.bootstrap.servers": "kafka:9092",
      "schema.history.internal.kafka.topic": "schema-changes.incr-snap",
      "snapshot.mode": "never",
      "signal.enabled.channels": "source",
      "signal.data.collection": "inventory.debezium_signal",
      "incremental.snapshot.chunk.size": "10000",
      "incremental.snapshot.watermarking.strategy": "insert_insert"
    }
  }'

sleep 15
```

**关键参数**：`snapshot.mode=never`——不执行初始全量快照，全程通过信号触发增量快照；`incremental.snapshot.chunk.size=10000`——每块读 1 万行；`watermarking.strategy=insert_insert`——MySQL 推荐的水印策略。

### 步骤2：发送增量快照信号并监控进度

```bash
docker exec mysql mysql -uroot -proot1234 inventory -e "
  INSERT INTO debezium_signal (id, type, data) VALUES (
    UUID(), 'execute-snapshot',
    '{\"data-collections\": [\"inventory.products_large\"]}'
  );
"

# 监控快照进度——观察 chunk 的执行
watch -n 2 "docker logs connect 2>&1 | tail -8 | grep -E 'chunk|Chunk|completed'"
# 预期输出序列：
# Snapshot chunk 1/50 completed in 0.5s (10000 rows)
# Snapshot chunk 2/50 completed in 0.4s (10000 rows)
# ...
# Incremental snapshot completed for table inventory.products_large (500000 rows in 25s)

# 快照完成后验证数据
curl http://localhost:8083/connectors/incr-snap-connector/status | python3 -m json.tool
```

### 步骤3：快照期间并发写入测试——验证数据不丢失

**目标**：在快照执行期间持续写入新数据，验证快照完成后新数据被 streaming 捕获。

```bash
# 终端1：在快照进行中持续写入（模拟业务流量）
for i in {1..100}; do
    docker exec mysql mysql -uroot -proot1234 inventory \
      -e "INSERT INTO products_large (name, price, stock, category) VALUES ('Concurrent-$i', $((RANDOM%1000)), $((RANDOM%100)), 'Z');"
    sleep 1
done &

# 终端2：发送增量快照信号
docker exec mysql mysql -uroot -proot1234 inventory -e "
  INSERT INTO debezium_signal (id, type, data) VALUES (
    UUID(), 'execute-snapshot',
    '{\"data-collections\": [\"inventory.products_large\"]}'
  );
"

# 终端3：消费 Kafka Topic，统计总消息数
docker exec kafka kafka-console-consumer --bootstrap-server localhost:9092 \
  --topic incr_snap.inventory.products_large --from-beginning --timeout-ms 60000 2>/dev/null | wc -l
# 预期：总数 = 500000（快照） + 100（并发写入）= 500100
```

### 步骤4：对比增量快照 vs 初始快照

| 维度 | 初始快照 (initial) | 增量快照 (Incremental) |
|------|-------------------|----------------------|
| 锁表 | ⚠️ 短暂全局读锁 | ✅ 无锁（MVCC 快照读） |
| 可恢复性 | ❌ 中断后重头开始 | ✅ 从断点 Chunk 继续 |
| 50 万行耗时 | ~95 秒 | ~25 秒（chunk.size=10000） |
| 期间 DDL 影响 | ⚠️ 可能 Schema 不一致 | ✅ 水印机制处理 |
| 触发方式 | Connector 启动自触发 | 信号表手动触发 |

### 可能遇到的坑

| 坑 | 现象 | 根因 | 解决 |
|----|------|------|------|
| 水印策略不匹配 | 数据重复或丢失 | MySQL 用 `insert_insert`，PG 用 `insert_delete` | 根据数据库类型选择对应策略 |
| chunk size 过大 | 单块读取超时 | MySQL `wait_timeout` 默认 8 小时，但网络层面可能超时 | 调低至 5000-10000 |
| 无自增主键的表 | 增量快照无法分块 | 需要有序的索引列来分段 | 添加自增 ID 或使用复合主键排序 |

## 4. 项目总结

### 思考题

1. 如果增量快照读到第 300 个 Chunk 时 Connector 崩溃了，恢复后快照是从第 1 个 Chunk 重新开始还是从第 300 个继续？需要什么条件才能实现断点续传？

2. 水印的 `insert_insert` 策略和 `insert_delete` 策略在 MySQL 场景下的行为差异是什么？在 PG 场景下为什么 PG 只能用 `insert_delete`？

**（第22章思考题答案）**

1. TRUNCATE 信号表**不会**影响正在执行的增量快照——快照的控制流已经由 Connector 内部状态管理，信号只是一次性触发。但 TRUNCATE 后 Connector 无法处理新的信号（表空了无法 INSERT 新信号）。恢复方法：重新 CREATE TABLE debezium_signal，Connector 会自动检测到表存在并恢复信号轮询。

2. 信号表必须和 Connector 连接的是**同一个数据库**——因为 Connector 是通过轮询 `signal.data.collection` 指定的表来获取信号的，这个表必须在 Connector 的 JDBC 连接范围内。如果信号表在另一个数据库中，Connector 无法读取。如果要跨数据库触发信号，需要为每个数据库单独创建信号表。

---

> **推广提示**：对于 > 1000 万行的表，增量快照是唯一推荐的全量同步方案。将增量快照信号的 INSERT 封装为存储过程，集成到数据平台的自助门户——业务方点"同步此表"按钮 → 自动执行存储过程 → 快照开始。
