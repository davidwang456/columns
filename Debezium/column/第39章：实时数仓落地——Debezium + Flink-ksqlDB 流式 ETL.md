# 第39章：实时数仓落地——Debezium + Flink/ksqlDB 流式 ETL

## 1. 项目背景

"我们已经用 Debezium 把 200+ 张业务表都实时同步到 Kafka 了——但数据太'碎片化'了。运营大屏需要实时展示 GMV、订单转化率、用户活跃度三个核心指标，这三个指标分别来自 `orders`、`users`、`products` 三张不同的表，散布在三个不同的 Kafka Topic 中。BI 团队每次想加一个新指标，就要写一个 Flink 作业去 Join 这几个 Topic，开发周期至少 3 天。他们问我：'能不能像 SQL 一样，一条 SELECT ... JOIN ... 就把这几个 Topic 的数据自动汇成一张宽表？'"

这是 CDC 管道完成"数据搬入 Kafka"后必然面对的问题——**怎么用这些数据？** 数据分散在数十个 Topic 中（每张源表对应一个 Topic），而业务需要的是"订单宽表"、"用户画像宽表"、"商品销售宽表"——每张宽表都需要 JOIN 3-5 个原始 Topic。如果每个指标都从头写一遍消费、Join、去重、聚合的代码，开发效率极低。

这就是**流式 ETL**要解决的问题。Apache Flink 和 ksqlDB 是两个主流方案——Flink SQL 提供了完整的 SQL 语义（JOIN/WHERE/GROUP BY/窗口），可以将分散的 CDC 数据实时汇聚为宽表；ksqlDB 则更轻量，直接集成在 Kafka 生态中，用类 SQL 的语法构建 Stream/Table 之间的 Join。

### 痛点放大

| 痛点 | 没有流式 ETL | 有流式 ETL |
|------|-------------|----------|
| 数据分散 | 3 个业务表分散在 3 个 Topic，BI 手动写代码 Join | 一条 Flink SQL JOIN 自动产出宽表 |
| 开发效率 | 每个新指标 = 一个新 Flink 作业 = 3 天 | 修改 SQL 即可，30 分钟上线 |
| 实时性 | 如果要后处理 Join，延迟叠加（CDC 延迟 + 消费延迟 + Join 延迟） | Flink 增量 Join，延迟 < 1s |
| 一致性 | 不同 Topic 的消费进度不同步 → Join 可能漏数据 | Flink Checkpoint 保证 Exact-Once，Join 状态和消费进度原子保存 |

## 2. 项目设计——三人对话

**（周二下午，BI 团队的一位数据工程师和大师、小胖、小白围坐在白板前）**

**小胖**："大师，我现在手上有三个 Kafka Topic——`ecom.orders`（订单表）、`ecom.users`（用户表）、`ecom.products`（商品表）。BI 团队想要一张'订单宽表'——包含订单 ID、用户姓名、用户邮箱、商品名称、商品类目、订单金额、订单状态。这个宽表怎么实时生成？"

**大师**："这就是流表 Join 的经典场景。三个 Topic 中，orders 是**事实流**（不断有新的 INSERT/UPDATE），users 和 products 是**维度表**（相对静态，偶尔 UPDATE）。Flink SQL 的做法是——把 orders 定义为 Append-only Source，users 和 products 定义为 CDC Source（带 PRIMARY KEY），然后用 LEFT JOIN 把它们 Join 成一张宽表。"

**小白**："Flink 怎么知道 users 表的最新状态是什么？比如用户 Alice 昨天改了名字从 'Alice' 改成 'Alice Wang'——Flink 读到这条 UPDATE 时，怎么更新之前的 JOIN 结果？"

**大师**："这就是 Flink 的 **Changelog Stream（变更流）** 机制。当 Debezium 产生的 Change Event 被 Flink 消费时，Flink 把每条消息解读为 `+I`（INSERT）、`-U`（UPDATE_BEFORE）、`+U`（UPDATE_AFTER）、`-D`（DELETE）四种行变更。JOIN 的结果也是一个 Changelog Stream——当 users 表的一条 UPDATE 到达时，Flink 自动撤回（`-U`）旧 JOIN 结果，并输出（`+U`）新 JOIN 结果。下游 ClickHouse 收到后做 Upsert 即可。"

**小胖**："那 ksqlDB 呢？它也能做流表 Join 吗？和 Flink SQL 比有什么不同？"

**大师**："ksqlDB 的语法更轻量——它把 Kafka Topic 映射为 **Stream**（追加流）或 **Table**（可更新表），然后用 `CREATE STREAM ... AS SELECT ... FROM stream JOIN table ... EMIT CHANGES` 的方式做 Join。优点是部署极简（一个 JAR），缺点是不支持复杂的时间窗口 Join 和状态后端选择。对于简单的宽表构建，ksqlDB 足够了；对于复杂的 ETL（多级 Join、窗口聚合、状态 TTL），Flink 更合适。"

**技术映射**：Flink Changelog Stream = 银行的"交易流水通知"。每次账户变动（INSERT/UPDATE/DELETE），银行不仅告诉你当前余额（新状态），还告诉你之前余额是多少（旧状态），以及操作类型（存款/取款/转账）——你可以基于这些信息完整地重建账户的历史状态。JOIN 后产生的宽表亦然——当 Alice 改了名字，宽表会自动"撤回"旧行并插入新行。

**小胖**："还有一个问题——如果 orders 表的数据先到了 Kafka，但对应的 users 数据因为网络延迟晚到了 5 秒。这个时间差内，JOIN 会输出 user_name = NULL 吗？"

**大师**："这是流表 Join 的'时间差'问题。Flink 的解决方案是 **Temporal Table Join** + **Watermark**。通过 `FOR SYSTEM_TIME AS OF` 语法，Flink 保证 JOIN 时使用的是 '在订单产生时刻'的 users 表的历史快照，而不是'当前最新'的 users 表。同时设置 `table.exec.source.idle-timeout` 告诉 Flink '如果 users Topic 超过 5 秒没收到新数据，就认为它已经追上了，不用再等'——避免无限等待。"

---

## 3. 项目实战

### 环境准备

```bash
# 确认 MySQL 中有三张源表
docker exec mysql mysql -uroot -proot1234 inventory -e "SHOW TABLES;" | grep -E "orders|products"
# orders, products 已存在

# 确保 Kafka Topic 中有数据
docker exec kafka kafka-topics --bootstrap-server localhost:9092 --list | grep ecom
```

### 步骤1：Flink SQL 三表实时 JOIN 宽表（完整作业）

**目标**：用一条 Flink SQL 作业，将 orders + users + products 三张 CDC 源表实时 JOIN 为宽表，写入 ClickHouse。

```sql
-- ============ Flink SQL 作业: 订单实时宽表 ============

-- Step 1: 定义三张 CDC Source 表（使用 Debezium Avro 格式）
CREATE TABLE orders_cdc (
    id INT,
    user_id INT,
    product_id INT,
    quantity INT,
    amount DECIMAL(10,2),
    status STRING,
    created_at TIMESTAMP(3),
    PRIMARY KEY (id) NOT ENFORCED
) WITH (
    'connector' = 'kafka',
    'topic' = 'ecom_orders.inventory.orders',
    'properties.bootstrap.servers' = 'kafka:9092',
    'properties.group.id' = 'flink-order-wide-orders',
    'format' = 'debezium-avro-confluent',
    'debezium-avro-confluent.schema-registry.url' = 'http://schema-registry:8081',
    'scan.startup.mode' = 'latest-offset'
);

CREATE TABLE users_cdc (
    id INT,
    name STRING,
    email STRING,
    phone STRING,
    PRIMARY KEY (id) NOT ENFORCED
) WITH (
    'connector' = 'kafka',
    'topic' = 'ecom_profiles.public.users',
    'properties.bootstrap.servers' = 'kafka:9092',
    'format' = 'debezium-avro-confluent',
    'debezium-avro-confluent.schema-registry.url' = 'http://schema-registry:8081',
    'scan.startup.mode' = 'latest-offset'
);

CREATE TABLE products_cdc (
    id INT,
    product_name STRING,
    category STRING,
    price DECIMAL(10,2),
    PRIMARY KEY (id) NOT ENFORCED
) WITH (
    'connector' = 'kafka',
    'topic' = 'ecom_products.inventory.products',
    'properties.bootstrap.servers' = 'kafka:9092',
    'format' = 'debezium-avro-confluent',
    'debezium-avro-confluent.schema-registry.url' = 'http://schema-registry:8081',
    'scan.startup.mode' = 'latest-offset'
);

-- Step 2: 定义 ClickHouse Sink 宽表
CREATE TABLE order_wide_clickhouse (
    order_id INT,
    user_id INT,
    user_name STRING,
    user_email STRING,
    product_id INT,
    product_name STRING,
    product_category STRING,
    quantity INT,
    amount DECIMAL(10,2),
    status STRING,
    created_at TIMESTAMP(3),
    etl_time TIMESTAMP(3),
    PRIMARY KEY (order_id) NOT ENFORCED
) WITH (
    'connector' = 'clickhouse',
    'url' = 'clickhouse://clickhouse:8123',
    'database-name' = 'dwd',
    'table-name' = 'order_wide',
    'username' = 'default',
    'password' = '',
    'sink.batch-size' = '5000',
    'sink.flush-interval' = '3s',
    'sink.max-retries' = '3'
);

-- Step 3: 实时三表 LEFT JOIN —— 一行 SQL 完成全链路
INSERT INTO order_wide_clickhouse
SELECT
    o.id                                   AS order_id,
    o.user_id,
    u.name                                 AS user_name,
    u.email                                AS user_email,
    o.product_id,
    p.product_name,
    p.category                             AS product_category,
    o.quantity,
    o.amount,
    o.status,
    o.created_at,
    NOW()                                  AS etl_time
FROM orders_cdc o
LEFT JOIN users_cdc FOR SYSTEM_TIME AS OF o.created_at AS u
    ON o.user_id = u.id
LEFT JOIN products_cdc FOR SYSTEM_TIME AS OF o.created_at AS p
    ON o.product_id = p.id;
```

### 步骤2：验证宽表实时更新

```bash
# 终端1：在 ClickHouse 中观察宽表数据变化
docker exec clickhouse clickhouse-client -q "
SELECT order_id, user_name, product_name, amount, status 
FROM dwd.order_wide 
ORDER BY order_id DESC LIMIT 5"

# 终端2：在 MySQL 中创建订单（触发宽表 INSERT）
docker exec mysql mysql -uroot -proot1234 inventory -e "
  INSERT INTO orders (customer_id, product_name, quantity, price, status) 
  VALUES (1, 'Widget Pro', 2, 199.99, 'pending');
"

# 终端3：更新用户信息（触发宽表 UPDATE——旧行撤回 + 新行插入）
docker exec postgres psql -U postgres -d user_profile -c "
  UPDATE users SET name = 'Alice Wang' WHERE username = 'alice';
"

# 返回终端1：再次查询——alice 的订单中 user_name 应为 'Alice Wang'
```

### 步骤3：ksqlDB 轻量方案

```sql
-- ksqlDB: 创建 Source Connector（ksqlDB 内置 Debezium 集成）
CREATE SOURCE CONNECTOR mysql_debezium_orders WITH (
    'connector.class' = 'io.debezium.connector.mysql.MySqlConnector',
    'database.hostname' = 'mysql',
    'database.port' = '3306',
    'database.user' = 'debezium',
    'database.password' = 'dbz1234',
    'database.server.id' = '184391',
    'topic.prefix' = 'ksql_orders',
    'table.include.list' = 'inventory.orders'
);

-- 将 Kafka Topic 映射为 Stream
CREATE STREAM orders_stream (
    id INT KEY,
    user_id INT,
    product_id INT,
    amount DOUBLE,
    status STRING
) WITH (
    kafka_topic = 'ksql_orders.inventory.orders',
    value_format = 'AVRO'
);

-- 将 Kafka Topic 映射为 Table（维度表）
CREATE TABLE users_table (
    id INT PRIMARY KEY,
    name STRING,
    email STRING
) WITH (
    kafka_topic = 'ksql_users.public.users',
    value_format = 'AVRO'
);

-- 流表 Join 输出宽表 Stream
CREATE STREAM order_enriched WITH (
    kafka_topic = 'order_wide_enriched',
    value_format = 'AVRO'
) AS
SELECT
    o.id AS order_id,
    o.amount,
    o.status,
    u.name AS user_name,
    u.email AS user_email
FROM orders_stream o
LEFT JOIN users_table u ON o.user_id = u.id
EMIT CHANGES;
```

### 步骤4：处理"赶不上的 JOIN"——时间差问题

```sql
-- 在 Source 表定义中加入 idle-timeout
-- 当 users Topic 超过 10 秒无新数据时，认为它已追上，不再等待
ALTER TABLE users_cdc SET (
    'scan.startup.mode' = 'latest-offset',
    'source.idle-timeout' = '10 s'
);

-- 在 JOIN 中加入 COALESCE 兜底——如果 user_name 为 NULL，显示默认值
SELECT
    o.id,
    COALESCE(u.name, '(用户数据延迟)') AS user_name,  -- NULL 兜底
    ...
FROM orders_cdc o
LEFT JOIN users_cdc FOR SYSTEM_TIME AS OF o.created_at AS u
    ON o.user_id = u.id;
```

### 可能遇到的坑及解决方法

| 坑 | 现象 | 根因 | 解决方法 |
|----|------|------|---------|
| Flink JOIN 状态无限增长 | Checkpoint 越来越慢，最终 OOM | 维度表的历史状态 TTL 未设置 | 设置 `table.exec.state.ttl = 86400000` (24h) |
| LEFT JOIN 输出 NULL | user_name 字段为空 | user 数据晚于 order 数据到达 | 用 `FOR SYSTEM_TIME` + `COALESCE` 兜底 |
| ksqlDB EMIT CHANGES 延迟大 | 宽表 Stream 输出延迟 10s+ | ksqlDB 默认 commit interval 太长 | 设置 `ksql.streams.commit.interval.ms=1000` |
| ClickHouse Sink 写入失败 | sink.max-retries 耗尽 | ClickHouse 表引擎与 Flink Sink 不兼容 | 使用 `ReplacingMergeTree` + `ENGINE = ReplacingMergeTree(etl_time)` |

---

## 4. 项目总结

### 优点 & 缺点

| 方案 | 优势 | 劣势 | 适合场景 |
|------|------|------|---------|
| Flink SQL | 功能完整、状态后端多、Checkpoint 强劲 | 运维复杂（JobManager+TaskManager） | 企业级复杂 ETL、海量数据 |
| ksqlDB | 部署极简（单 JAR）、Kafka 原生集成 | 不支持复杂窗口、状态管理有限 | 中规模快速原型、简单宽表 |

### 适用场景

1. **运营大屏**：GMV、订单量、用户活跃度实时宽表
2. **实时推荐**：用户行为 + 商品属性 + 用户画像的实时关联
3. **实时风控**：交易 + 设备指纹 + 历史行为的多流规则匹配
4. **数据中台 ODS→DWD 层**：从原始 CDC 数据到业务可用的明细宽表
5. **实时数据同步到 OLAP**：ClickHouse/Doris/StarRocks 的物化视图构建

### 不适用场景

1. **纯离线 ETL**（T+1 报表）：CDC 入湖后用 Spark 批处理更合适
2. **极简需求**（只需单表同步）：不需要 JOIN，直接用 Kafka Consumer 写入 ClickHouse 即可

### 注意事项

- **Flink State TTL 必须设置**：无限增长的 State 最终会 OOM
- **维度表的 Watermark 策略**：设置 `source.idle-timeout` 防止长时间等待
- **ClickHouse Sink 的表引擎**：推荐 `ReplacingMergeTree` + `ORDER BY (order_id)` 实现 Upsert 去重

### 思考题

1. 如果 orders 表到达时，对应的 users 数据因为 Kafka 分区延迟晚到 30 秒，Flink LEFT JOIN 会输出 `user_name = NULL`。如何在宽表查询时区分"用户不存在"和"用户数据延迟"两种情况？设计一个区分方案。

2. Flink 的 Changelog Stream 模式下，如果需要对宽表做 COUNT(DISTINCT user_id) 聚合——这个操作的语义是什么？是否需要先做去重？

**（第38章思考题答案）**

Iceberg V2 格式下每个 CDC UPDATE 产生 1 个 delete file + 1 个 data file，高频 UPDATE 导致小文件爆炸。Compaction 策略：① 设置 `write.target-file-size-bytes = 536870912`（512MB），让 Flink 写入时尽量产生大文件；② 定期运行 Iceberg 的 `rewriteDataFiles` 存储过程合并小文件：`CALL catalog.system.rewrite_data_files('orders_iceberg', 'file_size < 10MB')`；③ 通过 K8s CronJob 每小时执行一次合并，合并阈值 = 当前文件数 > 500 且平均大小 < 50MB。

---

> **推广提示**：将 Flink SQL 宽表脚本模板化，放入团队的 `flink-sql-templates/` Git 目录。新业务接入只需替换表名和 JOIN 条件——5 分钟产出新宽表。建议在 Flink 作业监控中增加两个核心指标：JOIN 后的 `NULL 率`（监控数据延迟）和 `State Size`（监控内存膨胀）。
