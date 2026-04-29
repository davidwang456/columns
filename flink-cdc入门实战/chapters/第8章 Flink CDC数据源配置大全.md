# 第8章 Flink CDC数据源配置大全

## 1 项目背景

### 业务场景：公司有7个数据库需要接入CDC

随着公司业务扩张，数据平台团队发现需要接入CDC的数据库越来越多：
- **MySQL**：核心订单库（3套集群，50+张表）
- **PostgreSQL**：用户画像系统
- **MongoDB**：商品评论和日志系统
- **Oracle**：老旧的ERP财务系统
- **SQL Server**：海外子系统的数据库

每个数据库的CDC接入方式截然不同——连接参数、权限配置、日志机制、数据类型映射都各有特点。如果不系统梳理，每次接入新数据源都要从头踩一遍坑。

### 各类数据库CDC原理对比

```
┌─────────────┬─────────────────┬─────────────────────┐
│   数据库     │   CDC日志机制    │  Flink CDC实现方式    │
├─────────────┼─────────────────┼─────────────────────┤
│   MySQL     │   Binlog        │ Debezium Embedded    │
│ PostgreSQL  │   WAL (逻辑复制) │ Debezium Embedded    │
│   Oracle    │   LogMiner      │ Debezium Embedded    │
│  SQL Server │   CDC Table     │ Debezium Embedded    │
│  MongoDB    │   Change Stream │ 自定义Flink Source   │
│  OceanBase  │   Liboblog      │ Debezium Embedded    │
│    TiDB     │   TiKV CDC      │ 自定义Flink Source   │
└─────────────┴─────────────────┴─────────────────────┘
```

---

## 2 项目设计 · 三人交锋对话

**小胖**（疑惑）：每种数据库的CDC配置都不同，那Flink CDC是不是每种数据库都有不同的代码？那我要学7种API？

**大师**：恰恰相反！Flink CDC 3.x的Pipeline API核心目标是**统一入口**。在YAML Pipeline中，所有数据源都用同一套配置结构：

```yaml
source:
  type: mysql           # 只改这里
  hostname: localhost   # 以下配置结构完全一样
  port: 3306
  username: cdc_user
  password: cdc_pass
  tables: shop.orders
```

但底层机制确实完全不同——理解这些差异才能在生产环境中避免踩坑。

**小白**：除了MySQL，PostgreSQL的CDC配置有什么特别的？我听说PostgreSQL的WAL日志清理机制和MySQL的Binlog不一样？

**大师**：对，这是最大的差异。PostgreSQL的逻辑复制基于**复制槽（Replication Slot）**——只要Slot存在，PostgreSQL会保留WAL日志，即使`wal_keep_size`配置更小。这意味着：

**好处**：Slot不删除，日志就不清理。Flink CDC作业停机一周回来，只要Slot还在，就能从断点续传。

**坏处**：如果Flink CDC作业挂了再也不回来（或者Slot忘记删除），WAL日志会无限增长，最终撑爆磁盘！

所以PostgreSQL的运维铁律是：**必须监控复制槽的状态，及时清理不再使用的Slot**。

**小白**：那Oracle的CDC呢？我听说Oracle不需要修改配置就能开启CDC？

**大师**：Oracle的CDC是最"重"的。Oracle通过**LogMiner**来读取Redo Log，配置上需要：
1. 开启ARCHIVELOG模式（需要重启数据库）
2. 安装LogMiner包（默认已安装）
3. 为Flink CDC用户授予`LOGMINING`权限
4. 配置Undo表空间足够大（大事务需要大量Undo）

而且Oracle的数据类型映射是最复杂的——`NUMBER`类型没有精度信息、`CLOB`/`BLOB`大对象处理、`TIMESTAMP WITH TIME ZONE`等。

**技术映射**：MySQL的CDC像"小区门口的门卫"——只要登记了就能进出（配置简单）。Oracle的CDC像"机场安检"——要提前预约、过各种检查（配置繁琐）。

**小胖**：那MongoDB呢？它没有Binlog，也没有WAL，CDC怎么实现的？

**大师**：MongoDB CDC靠的是**Change Streams**——MongoDB 3.6+引入的特性，通过oplog（操作日志）实现。Flink CDC的MongoDB连接器（`flink-connector-mongodb-cdc`）默认使用Change Streams API，不需要额外配置。

MongoDB CDC的独特挑战：
1. 需要MongoDB副本集（Replica Set）或分片集群（Sharded Cluster）
2. Change Streams默认只保留最近的操作历史（取决于oplog大小）
3. 文档的嵌套结构（JSON/BSON）在映射到Flink的行式类型时需要打平

---

## 3 项目实战

### 环境准备

以下配置涵盖了Flink CDC支持的所有生产级数据源。每个数据源需要对应的连接器JAR包。

### 分步实现

#### 步骤1：MySQL CDC——生产配置模板

```yaml
source:
  type: mysql
  hostname: localhost
  port: 3306
  username: cdc_user
  password: cdc_pass
  tables: shop.orders, shop.products     # 多表用逗号分隔，支持正则
  server-id: 5400-5404                    # 必须配置范围，覆盖并行度
  server-time-zone: Asia/Shanghai
  scan.startup.mode: initial              # initial | latest-offset | earliest-offset
  scan.incremental.snapshot.chunk.size: 8096  # 分块大小，大表建议调小
  scan.incremental.snapshot.enabled: true     # 是否使用增量快照算法
  scan.newly-added-table.enabled: true        # 动态发现新增表
  # Debezium透传参数（以debezium.开头）
  debezium:
    snapshot.mode: initial
    database.history: io.debezium.relational.history.MemoryDatabaseHistory
```

**MySQL源端权限：**
```sql
GRANT SELECT, RELOAD, SHOW DATABASES, REPLICATION SLAVE, REPLICATION CLIENT ON *.* TO 'cdc_user'@'%';
```

#### 步骤2：PostgreSQL CDC——配置模板

**PostgreSQL源端配置：**
```ini
# postgresql.conf
wal_level = logical                    # 必须开启逻辑复制
max_replication_slots = 10            # 至少为CDC分配一个Slot
max_wal_senders = 10                  # WAL发送进程数
```

**创建Publication（相当于MySQL的Binlog监听白名单）：**
```sql
-- 为所有表创建发布
CREATE PUBLICATION cdc_publication FOR ALL TABLES;

-- 或为指定表创建发布
CREATE PUBLICATION cdc_publication FOR TABLE orders, users;
```

**Flink CDC配置：**
```yaml
source:
  type: postgres
  hostname: localhost
  port: 5432
  username: cdc_user
  password: cdc_pass
  database: shop_pg
  schema-name: public
  tables: public.orders, public.users
  slot.name: flink_cdc_slot               # 复制槽名称（必须唯一）
  publication.name: cdc_publication       # Publication名称
  decoding.plugin.name: pgoutput           # 解码插件（pgoutput | decoderbufs | wal2json）
  debezium:
    snapshot.mode: initial
    slot.stream.params: true
```

**PostgreSQL与MySQL配置对比：**

| 配置项 | PostgreSQL | MySQL |
|-------|-----------|-------|
| 监听对象 | `publication.name` + `schema-name` | `database-list` + `table-list` |
| 断点续传机制 | Replication Slot | Binlog位点 / GTID |
| slot/Binlog清理策略 | Slot存在则不清理 | 按`expire_log_days`定时清理 |
| 超时保护 | `slot.max.retry.time.ms` | `connect.timeout` |
| Schema归属 | `schema-name`属于逻辑命名空间 | `database`与`schema`合一 |

#### 步骤3：MongoDB CDC——配置模板

**MongoDB源端要求：**
- 副本集（Replica Set）模式，而非单节点
- 开启Oplog（默认开启）
- CDC用户需要`read`和`changeStream`权限

**Flink CDC配置：**
```yaml
source:
  type: mongodb
  hosts: localhost:27017                    # 副本集多个节点
  username: cdc_user
  password: cdc_pass
  database: shop_mongo                      # 监控的数据库
  collection: orders                        # 监控的集合
  connection.options: replicaSet=rs0&authSource=admin
  scan.startup.mode: initial                # initial | latest-offset
  # MongoDB特有配置
  copy.existing: true                       # 是否全量复制存量数据
  copy.existing.queue.size: 10240           # 全量复制队列大小
  batch.size: 1024                          # 增量变更批量大小
  poll.await.time.ms: 1000                  # 轮询间隔
  heartbeat.interval.ms: 10000              # 心跳间隔（防止Change Stream超时）
```

**MongoDB数据类型映射：**

| MongoDB类型 | Flink类型 | 说明 |
|------------|----------|------|
| `ObjectId` | `STRING` | 转为24位十六进制字符串 |
| `ISODate` | `TIMESTAMP(3)` | 时间戳映射 |
| `NumberLong` | `BIGINT` | 64位整数 |
| `NumberDecimal` | `DECIMAL(38, 18)` | 高精度小数 |
| `Embedded Document` | `ROW<...>` 或 `STRING` | 嵌套文档，可展开或JSON序列化 |
| `Array` | `ARRAY<...>` 或 `STRING` | 数组映射 |
| `Binary` | `BYTES` | 二进制数据 |

#### 步骤4：Oracle CDC——配置模板

**Oracle源端配置：**
```sql
-- 开启归档模式（需要重启）
SHUTDOWN IMMEDIATE;
STARTUP MOUNT;
ALTER DATABASE ARCHIVELOG;
ALTER DATABASE OPEN;

-- 授予CDC用户权限
GRANT LOGMINING TO cdc_user;
GRANT CREATE SESSION TO cdc_user;
GRANT SELECT ON V_$DATABASE TO cdc_user;
GRANT SELECT_CATALOG_ROLE TO cdc_user;
GRANT SELECT ANY TRANSACTION TO cdc_user;
GRANT FLASHBACK ANY TABLE TO cdc_user;
-- LogMiner权限
GRANT EXECUTE ON DBMS_LOGMNR TO cdc_user;
GRANT EXECUTE ON DBMS_LOGMNR_D TO cdc_user;
```

**Flink CDC配置：**
```yaml
source:
  type: oracle
  hostname: localhost
  port: 1521
  username: cdc_user
  password: cdc_pass
  database: ORCLCDB
  schema-name: SCOTT                         # Oracle Schema
  tables: SCOTT.EMP, SCOTT.DEPT
  url: jdbc:oracle:thin:@//localhost:1521/ORCLCDB
  debezium:
    log.mining.strategy: online_catalog      # online_catalog | online_redo_log_catalog
    log.mining.continuous.mine: true         # 持续挖掘
    log.mining.transaction.retention.hours: 1  # 大事务保留时间
    database.history: io.debezium.relational.history.MemoryDatabaseHistory
```

#### 步骤5：多数据源并行读取——综合配置

```java
package com.example;

import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.cdc.connectors.mysql.source.MySqlSource;
import org.apache.flink.cdc.connectors.postgres.source.PostgresSource;
import org.apache.flink.cdc.debezium.JsonDebeziumDeserializationSchema;

/**
 * 多数据源并行读取演示
 * 同时从MySQL和PostgreSQL读取变更数据，合并为一个流
 */
public class MultiSourceDemo {

    public static void main(String[] args) throws Exception {
        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        env.enableCheckpointing(5000);
        env.setParallelism(2);

        // Source 1: MySQL CDC
        MySqlSource<String> mysqlSource = MySqlSource.<String>builder()
            .hostname("localhost").port(3306)
            .databaseList("shop").tableList("shop.orders")
            .username("cdc_user").password("cdc_pass")
            .deserializer(new JsonDebeziumDeserializationSchema())
            .serverId("5400-5401")
            .startupOptions(
                org.apache.flink.cdc.connectors.mysql.table.StartupOptions.latest())
            .build();

        // Source 2: PostgreSQL CDC
        PostgresSource<String> pgSource = PostgresSource.<String>builder()
            .hostname("localhost").port(5432)
            .database("shop_pg").schemaList("public").tableList("public.users")
            .username("cdc_user").password("cdc_pass")
            .deserializer(new JsonDebeziumDeserializationSchema())
            .slotName("flink_cdc_slot_2")
            .publicationName("cdc_publication")
            .decodingPluginName("pgoutput")
            .build();

        // 两个Source并行读取
        DataStream<String> mysqlStream = env.fromSource(
            mysqlSource,
            org.apache.flink.api.common.eventtime.WatermarkStrategy.noWatermarks(),
            "MySQL CDC");

        DataStream<String> pgStream = env.fromSource(
            pgSource,
            org.apache.flink.api.common.eventtime.WatermarkStrategy.noWatermarks(),
            "PostgreSQL CDC");

        // 合并为一个流（DataStream.union）
        DataStream<String> mergedStream = mysqlStream.union(pgStream);

        mergedStream.print();

        env.execute("Multi-source CDC Demo");
    }
}
```

#### 常见陷坑及解决方法

| 数据源 | 问题 | 解决方法 |
|-------|------|---------|
| MySQL | Binlog清理导致启动失败 | `expire_log_days`设7天以上，搭配`latest()`模式 |
| PostgreSQL | WAL日志暴增撑爆磁盘 | 监控`pg_replication_slots`，作业彻底删除后DROP SLOT |
| MongoDB | Change Stream因网络断开丢失 | 设置`heartbeat.interval.ms=10000`保活 |
| Oracle | LogMiner占用大量Undo | 配置`log.mining.transaction.retention.hours=1` |
| SQL Server | CDC Table清理不及时 | 配置`cdc_job_cleanup`定期清理 |

---

## 4 项目总结

### 数据源选型决策

```
需要实时CDC
├── 关系型数据库
│   ├── 开源（MySQL / PostgreSQL）
│   │   ├── 配置简单、文档丰富 → 推荐Flink CDC
│   │   └── 单表数据量>500GB → 注意Chunk切分配置
│   ├── 商业（Oracle / SQL Server）
│   │   ├── 配置复杂但Flink CDC支持成熟 → 推荐Flink CDC
│   │   └── 许可证费用已支付 → 可考虑
│   └── 国产（OceanBase / TiDB / DM8）
│       ├── OceanBase/TiDB → Flink CDC原生支持
│       └── DM8等 → 需自定义Connector
├── 文档型数据库
│   └── MongoDB → Flink CDC原生支持
└── 其他
    └── 自定义Source → 见第36、37章
```

### 注意事项

1. **连接字符串差异**：Docker内用服务名（`mysql`），Docker外用`localhost`或真实IP。配置文件中的`hostname`根据运行环境调整。
2. **密码安全**：生产环境中密码应通过环境变量或密钥管理服务注入，禁止硬编码在YAML或代码中。
3. **版本兼容性**：Flink CDC 2.x不支持PostgreSQL和SQL Server的Pipeline模式，需升级到3.x。
4. **Schema迁移**：从Flink CDC 2.x迁移到3.x时，Source API有破坏性变更（从`SourceFunction`变为FLIP-27 `Source`接口）。

### 常见踩坑经验

**故障案例1：PostgreSQL复制Slot积压导致磁盘爆满**
- **现象**：PostgreSQL服务器磁盘使用率从40%飙升到98%
- **根因**：Flink CDC作业的Slot未及时消费WAL日志，PostgreSQL保留了所有WAL文件
- **解决方案**：设置`max_slot_wal_keep_size=1GB`限制Slot最多保留1GB的WAL，并配置监控告警

**故障案例2：Oracle LogMiner因UNDO表空间不足失败**
- **现象**：Oracle CDC作业报错`ORA-30027: UNDO表空间不足`
- **根因**：大事务（修改了数千万行）导致LogMiner需要大量UNDO来记录数据前镜像
- **解决方案**：增大UNDO表空间，设置`debezium.log.mining.transaction.retention.hours=0.5`缩短大事务保留时间

**故障案例3：MongoDB Change Stream停止输出**
- **现象**：MongoDB CDC作业正常运行，但不再输出任何变更事件
- **根因**：Change Stream的游标（Cursor）因网络超时被MongoDB关闭，且Flink MongoDB连接器未自动恢复
- **解决方案**：升级到Flink CDC 3.0+（修复了游标自动恢复问题），设置`heartbeat.interval.ms=5000`

### 思考题

1. **进阶题①**：在Oracle CDC中，如果一张表没有主键，Flink CDC如何确定UPDATE和DELETE事件对应的行？这和MySQL CDC处理没有主键的表有什么区别？

2. **进阶题②**：PostgreSQL的`decoding.plugin.name`可选`pgoutput`、`decoderbufs`、`wal2json`三种。这三种解码方式在性能、数据类型精度、事件格式上有什么差异？生产环境推荐用哪一种？

---

> **下一章预告**：第9章「数据路由实战」——从源表到目标表的灵活映射，包括正则匹配、表名转换、多级路由优先级等核心配置。
