# 第28章：中级篇大综合——实时数仓CDC入湖全链路

---

## 1. 项目背景

某电商公司需要将MySQL业务库（订单、用户、商品）的变更数据实时同步到数据湖（Hudi/Iceberg），构建实时数仓ODS层。

架构：

```
MySQL(业务库)
    │
    ├── Canal/Debezium (CDC 捕获)
    │
    ├── Kafka (CDC数据Topic)
    │
    ├── Flink (写入引擎)
    │       ├── 流式写入Hudi/Iceberg
    │       └── 实时维表缓存到Redis
    │
    └── 下游：Hive/Spark/Presto查询
```

这是实时数仓中最经典的模式：**CDC（Change Data Capture）入湖**。Flink CDC Connector直接抓取MySQL的binlog，转换为Flink SQL中的表，再通过Flink SQL INSERT INTO 写入Hudi/Iceberg表。

本章涉及的所有技术——Flink CDC、Flink SQL、状态管理、Flink on K8S/YARN、多流Join、UDF、Watermark——全部在前面已学。

---

## 2. 项目设计

> 场景：架构师要求将ODS层从T+1离线升级为实时。小胖被安排做技术选型。

**小胖**：CDC我懂，用Debezium抓MySQL的binlog到Kafka，Flink消费Kafka写入Hudi不就完了？这有什么难的？

**大师**：你说的"写入Hudi"只有第一步。真正的难点在于：

- **Schema Evolution**：MySQL表增加了字段，Hudi表怎么同时更新？
- **Primary Key语义**：MySQL的INSERT/UPDATE/DELETE对应Hudi的什么操作？
- **Exactly-Once**：Flink的Checkpoint + Hudi的两阶段提交如何配合？
- **多表同步**：order表关联user表，业务上需要实时宽表——怎么在流上做多表Join？

**技术映射：CDC入湖 = 将MySQL的Changelog Stream映射为Flink SQL的Dynamic Table，再以Upsert模式写入湖存储。核心在于 ChangeLog → Append 的转换和CDC写入的幂等性。**

**小白**：那Flink CDC Connector和普通Kafka Source有什么区别？不就是读binlog吗？

**大师**：Flink CDC Connector做了两件普通Kafka Source做不到的事：

1. **全量+增量自动切换**：第一次启动时读取MySQL的全量数据，中间无感知切换到增量binlog——数据不丢不重
2. **元数据自动探测**：自动感知MySQL表结构，Flink SQL的Schema自动匹配MySQL表字段

**技术映射：Flink CDC Connector = 全量快照读取器 + binlog增量读取器 + 偏移量管理三合一的Source。启动时自动做全局锁（减少锁持有时间通过分段读取），全量完成后自动切换到增量。**

---

## 3. 项目实战

### 环境准备

| 组件 | 版本 | 用途 |
|------|------|------|
| MySQL | 8.0 | 业务库 |
| Canal / Debezium | 2.5+ | CDC捕获 |
| Kafka | 7.6.0 | 消息队列 |
| Flink | 1.18.1 | 计算引擎 |
| Hudi（或Iceberg）| 0.14+ | 湖存储 |
| Hive Metastore | 3.1+ | 元数据管理 |
| HDFS/S3 | - | 存储层 |

### 分步实现

#### 步骤1：开启MySQL binlog

**目标**：配置MySQL支持CDC。

```sql
-- 查看binlog是否开启
SHOW VARIABLES LIKE 'log_bin';

-- my.cnf配置
[mysqld]
log-bin=mysql-bin
binlog-format=ROW
binlog-row-image=FULL
server-id=1
expire_logs_days=7

-- 创建CDC用户
CREATE USER 'canal'@'%' IDENTIFIED BY 'canal123';
GRANT SELECT, REPLICATION SLAVE, REPLICATION CLIENT ON *.* TO 'canal'@'%';
FLUSH PRIVILEGES;
```

#### 步骤2：部署Flink CDC SQL Connector

**目标**：在Flink SQL中直接读取MySQL CDC。

```sql
-- DDL: 读取MySQL的订单表CDC
CREATE TABLE orders_cdc (
    orderId      STRING,
    userId       STRING,
    productId    STRING,
    amount       DOUBLE,
    orderStatus  INT,
    createTime   TIMESTAMP(3),
    updateTime   TIMESTAMP(3),
    PRIMARY KEY (orderId) NOT ENFORCED
) WITH (
    'connector' = 'mysql-cdc',
    'hostname' = 'mysql',
    'port' = '3306',
    'username' = 'canal',
    'password' = 'canal123',
    'database-name' = 'ecommerce',
    'table-name' = 'orders',
    'scan.startup.mode' = 'initial',       -- 首次全量+后续增量
    'server-id' = '5401-5404'             -- 需要4个（并行度4）
);

-- 查询验证（流式输出）
SELECT * FROM orders_cdc;
```

#### 步骤3：写入Hudi（Upsert模式）

**目标**：将CDC数据流写入Hudi表，支持UPSERT和DELETE。

```sql
-- DDL: Hudi Sink表
CREATE TABLE orders_hudi (
    orderId      STRING,
    userId       STRING,
    productId    STRING,
    amount       DOUBLE,
    orderStatus  INT,
    createTime   TIMESTAMP(3),
    updateTime   TIMESTAMP(3),
    PRIMARY KEY (orderId) NOT ENFORCED
) WITH (
    'connector' = 'hudi',
    'path' = 'hdfs://namenode:8020/warehouse/ods/orders',
    'table.type' = 'MERGE_ON_READ',            -- 支持增量读取
    'write.operation' = 'upsert',               -- UPSERT操作
    'hoodie.datasource.write.recordkey.field' = 'orderId',
    'write.precombine.field' = 'updateTime',   -- 同orderId时取最新的
    'hoodie.bucket.index.num.buckets' = '64',  -- 分桶数
    'write.batch.size' = '1024',
    'write.tasks' = '4'
);

-- CDC → Hudi
INSERT INTO orders_hudi
SELECT * FROM orders_cdc;
```

**三流CDC同步**：同时同步订单、用户、商品三张表。

```sql
-- 用户表CDC DDL（类似配置）
CREATE TABLE users_cdc (
    userId    STRING,
    userName  STRING,
    phone     STRING,
    city      STRING,
    createTime TIMESTAMP(3),
    updateTime TIMESTAMP(3),
    PRIMARY KEY (userId) NOT ENFORCED
) WITH (
    'connector' = 'mysql-cdc',
    'hostname' = 'mysql',
    'port' = '3306',
    'username' = 'canal',
    'password' = 'canal123',
    'database-name' = 'ecommerce',
    'table-name' = 'users',
    'scan.startup.mode' = 'initial'
);

-- 写入Hudi
INSERT INTO users_hudi SELECT * FROM users_cdc;
```

#### 步骤4：实时宽表——CDC四流Join

**目标**：将orders + users + products三流Join为实时宽表，写入Hudi。

```sql
-- 实时宽表DDL
CREATE TABLE order_wide_hudi (
    orderId      STRING,
    userId       STRING,
    userName     STRING,
    productId    STRING,
    productName  STRING,
    category     STRING,
    amount       DOUBLE,
    orderStatus  INT,
    createTime   TIMESTAMP(3),
    updateTime   TIMESTAMP(3),
    PRIMARY KEY (orderId) NOT ENFORCED
) WITH (
    'connector' = 'hudi',
    'path' = 'hdfs://namenode:8020/warehouse/dws/order_wide',
    'table.type' = 'MERGE_ON_READ',
    'write.operation' = 'upsert'
);

-- CDC表实时Join（基于Lookup Join）
INSERT INTO order_wide_hudi
SELECT
    o.orderId,
    o.userId,
    u.userName,
    o.productId,
    p.productName,
    p.category,
    o.amount,
    o.orderStatus,
    o.createTime,
    o.updateTime
FROM orders_cdc AS o
JOIN users_hudi FOR SYSTEM_TIME AS OF o.proctime AS u
    ON o.userId = u.userId
JOIN products_hudi FOR SYSTEM_TIME AS OF o.proctime AS p
    ON o.productId = p.productId;
```

#### 步骤5：Hudi增量查询验证

**目标**：验证数据已正确写入Hudi。

```bash
# 使用SparkSQL或Presto查询Hudi表
spark-sql --packages org.apache.hudi:hudi-spark3-bundle_2.12:0.14.0 \
  --conf 'spark.serializer=org.apache.spark.serializer.KryoSerializer'

spark-sql> SELECT COUNT(*) FROM order_wide_hudi;
spark-sql> SELECT * FROM order_wide_hudi LIMIT 10;
```

**验证CDC变更传播**：

```sql
-- 在MySQL中修改一条订单
UPDATE ecommerce.orders SET orderStatus = 2 WHERE orderId = 'ORD001';

-- 几秒后查询Hudi，确认变更已同步
spark-sql> SELECT orderId, orderStatus, updateTime FROM order_wide_hudi WHERE orderId = 'ORD001';
```

#### 步骤6：完整作业提交

**目标**：将CDC入湖作业提交到Flink集群。

```bash
# 编译包含依赖的fat jar
mvn clean package -DskipTests -Pflink-cdc,hudi

# 提交Application模式
./bin/flink run-application -t yarn-application \
  -Dstate.backend=rocksdb \
  -Dstate.checkpoints.dir=hdfs://namenode:8020/flink-checkpoints \
  -Dexecution.checkpointing.interval=60s \
  -Dparallelism.default=8 \
  -c com.flink.column.chapter28.CdcToHudiJob \
  /jobs/flink-cdc-hudi.jar
```

### 可能遇到的坑

1. **CDC Source全量读取时MySQL压力过大**
   - 根因：`scan.startup.mode=initial`时全量扫描大表（如10亿行）影响线上业务
   - 解决：使用`scan.startup.mode=latest-offset`只读增量；或调整`scan.incremental.snapshot.chunk.size`（默认8096，调小到1024减少每次扫描行数）

2. **Hudi写入小文件过多**
   - 根因：Flink每Checkpoint一次生成一个文件，Checkpoint间隔短导致大量小文件
   - 解方：增大Checkpoint间隔（30秒→120秒）；启用Hudi的Clustering（`hoodie.clustering.inline=true`）

3. **CDC表结构变更后Flink作业报错**
   - 根因：MySQL表DDL变更（如新增字段），Flink CDC的Schema没有自动更新
   - 解方：使用`scan.incremental.snapshot.chunk.key-column`或扩展`DebeziumDeserializationSchema`自定义Schema变更处理

---

## 4. 项目总结

### CDC入湖架构

```
MySQL Binlog → Debezium/Canal → Kafka(CDC Topic)
    │                                         │
    │  Flink CDC Connector                    │  Flink SQL
    │  (全量+增量自动切换)                     │  (Full Pipeline)
    ▼                                         ▼
Flink SQL（CDC Source）─→ 多流Join ─→ Hudi/Iceberg Sink
                                        │
                                        ▼
                                    Hive/Spark/Presto 查询
```

### 优点 & 缺点

| | Flink CDC入湖方案 | 传统离线ETL方案（对比） |
|------|----------------|------------------------|
| **优点1** | 秒级延迟——MySQL变更后即刻同步到Hudi | 小时~天级延迟（T+1定时任务） |
| **优点2** | 全量+增量自动切换，无需人工介入 | 全量导入和增量导入两套代码 |
| **优点3** | 流批一体——同一份数据可流式读取也可批处理 | 批处理单独跑，实时另建管道 |
| **优点4** | 无锁全量扫描（分段快照算法），对业务库影响小 | 全量导入可能锁表 |
| **缺点1** | 需要额外维护CDC组件（Debezium/Canal）和Kafka | 只需一个Sqoop命令行 |
| **缺点2** | binlog保存时间有限，作业重启超时后需重新全量 | 无此问题 |
| **缺点3** | CDC源表的Schema变更（DDL）会导致Flink作业异常 | ETL脚本重新跑即可 |

### CDC入湖核心配置

| 配置 | 推荐值 | 说明 |
|------|--------|------|
| scan.startup.mode | initial | 首次全量+增量 |
| server-id | 5401-54xx | 每个并行度一个ID |
| connect.timeout | 30s | 连接MySQL超时 |
| heartbeat.interval | 30s | 无数据变更时发送心跳 |

### 适用场景

**典型场景**：
1. 实时数仓ODS层建设——业务库变更实时同步到数据湖
2. 跨系统数据同步——MySQL → Hudi → Hive/Spark实时查询
3. 微服务CDC——多个微服务的业务表统一入湖做关联分析
4. 数据迁移——从MySQL迁移到Hudi/Iceberg，无缝切换
5. 实时宽表——多表CDC流Join为宽表，服务实时查询

**不适用场景**：
1. 一次性数据迁移——用Sqoop/DataX更简单直接
2. 源表频繁DDL——Flink CDC对ALTER TABLE的支持有限，建议用普通Kafka Source绕行

### 注意事项
- MySQL binlog保存时间（`expire_logs_days`）必须大于Flink CDC作业可能重启间隔——否则重启时binlog已清理，无法回退
- CDC Source并行度受server-id范围限制（每个并行度需要一个独立server-id），不能无限扩大
- Hudi写入的并行度建议=分区数，避免过多的Writer写入同一个小分区产生小文件

### 常见踩坑经验

**案例1：CDC全量读取阶段，Flink作业报"The server-side clock is not synchronized"**
- 根因：MySQL server和Flink所在机器的时间差太大
- 解方：同步所有机器时间（NTP）；或设置`server-time-zone=+08:00`

**案例2：Hudi写入报错"Could not instantiate HoodieTableMetaClient"**
- 根因：Hudi表的路径不存在或权限不足
- 解方：使用`hdfs dfs -mkdir -p`创建目标目录；确认HDFS用户有写权限

**案例3：CDC写入Hudi后，Hive查询不到最新数据**
- 根因：Hudi的sync to Hive配置没有开启
- 解方：开启`hoodie.datasource.hive_sync.enable=true`和`hoodie.datasource.hive_sync.table=orders_hudi`

### 思考题

1. CDC入湖时MySQL的DELETE操作怎么映射到Hudi？Hudi的`MERGE_ON_READ`和`COPY_ON_WRITE`两种表类型在处理DELETE时有什么不同？

2. 如果CDC Source读取的MySQL表有10亿行，全量扫描需要2小时。在这2小时内MySQL还有新的binlog变更——Flink CDC如何保证"全量+增量"切换时不丢数据？如果全量读到的某个行在切换前已经被更新了，两边的数据如何合并？
