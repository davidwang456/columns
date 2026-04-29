# 第2章 CDC技术概述与Flink CDC架构

## 1 项目背景

### 业务场景：微服务架构下的数据一致性困境

假设你是一家出行平台（类似滴滴）的后端工程师。平台由十几个微服务组成：订单服务、司机服务、支付服务、风控服务、搜索服务……每个服务有自己独立的数据库。现在，搜索服务需要实时展示"附近可用司机"——数据来自司机服务的地理位置表。

传统的做法是搜索服务定期（每30秒）查询司机服务的数据库。但这带来了几个严重问题：
1. **数据库压力大**：搜索服务每秒数千次查询，直接压垮了司机服务的MySQL
2. **数据一致性差**：30秒的查询间隔意味着用户看到的位置信息始终是过时的
3. **耦合度极高**：搜索服务直接依赖司机服务的数据库Schema，一旦司机服务表结构变更，搜索服务也需要修改

如果让"司机的GPS位置变更"能**实时推送**到搜索服务，问题就迎刃而解了。这就是CDC的用武之地。

### 痛点放大

在引入CDC之前，团队尝试了多种方案：

| 方案 | 缺陷 |
|------|------|
| **双写**（业务代码中同时写两个库） | 耦合高、代码侵入、事务难保证 |
| **定时批量同步（ETL）** | 延迟至少数分钟，无法做到实时 |
| **基于消息队列的解耦** | 业务代码仍需要手动发送消息，且无法保证数据与消息的原子性 |
| **业务触发器** | MySQL Trigger有性能瓶颈，难以维护 |

最终，团队意识到——最好的方案是**直接从数据库的Binlog/Write-Ahead Log中捕获变更**，不侵入业务代码，不增加数据库负载，并且能保证**毫秒级的实时性**。这就是CDC（Change Data Capture）。

### CDC架构演进图

```
┌─────────────────第一代（基于查询）─────────────────┐
│  定时 SELECT * FROM table WHERE update_time > ?     │
│  缺点：频繁查询压力大、无法捕获DELETE、延迟高        │
└─────────────────────────────────────────────────────┘

┌─────────────────第二代（基于日志，单机版）───────────┐
│         MySQL Binlog → Canal/Maxwell → Kafka        │
│  优点：实时、无侵入                                  │
│  缺点：需要额外部署组件，运维成本高                  │
└─────────────────────────────────────────────────────┘

┌─────────────────第三代（基于日志，Streaming引擎）───┐
│   MySQL Binlog → Debezium(Embedded) → Flink CDC     │
│  优点：实时、无侵入、Flink提供状态/窗口/容错/Exactly-Once  │
│  缺点：对Flink有一定学习成本                        │
└─────────────────────────────────────────────────────┘
```

### Flink CDC整体架构图

```
┌───────────┐    ┌───────────┐    ┌───────────┐    ┌───────────┐
│  MySQL    │    │PostgreSQL │    │  MongoDB  │    │  Oracle   │
│  Binlog   │    │  WAL      │    │Chg Stream │    │  Redo Log │
└─────┬─────┘    └─────┬─────┘    └─────┬─────┘    └─────┬─────┘
      │                │                │                │
      ▼                ▼                ▼                ▼
┌─────────────────────────────────────────────────────────────┐
│                  Debezium Engine Layer                       │
│      (Embedded Debezium: 数据库连接、日志解析、快照)          │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│              Flink CDC Connector Layer                       │
│  MySQLSource / PostgreSQLSource / MongoDBSource / OracleSource│
│  (Flink SourceFunction / Flink SourceReader / SQL Connector) │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                Flink Runtime (Streaming Engine)              │
│   Checkpoint机制  │  状态管理  │  Watermark  │  窗口计算    │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                    Sink (目标系统)                           │
│   Kafka  │  MySQL  │  Iceberg/Paimon  │  Elasticsearch      │
└─────────────────────────────────────────────────────────────┘
```

---

## 2 项目设计 · 三人交锋对话

### 角色
- **小胖**：爱吃爱玩，喜欢生活化类比
- **小白**：喜静喜深，专钻技术细节
- **大师**：资深技术Leader，善用比喻

---

**小胖**（喝了一口奶茶）：CDC……Change Data Capture，名字听着挺唬人。不就是给数据库装个摄像头，谁动了数据就记录下来吗？跟公司前台装监控有啥区别？

**大师**（笑着点头）：你这个"数据库摄像头"的比喻非常精准！CDC确实就是给数据库装了一个只读的、零侵入的摄像头。它不用你改任何业务代码，只要告诉它"盯着哪些表"，它就能一帧不落地记录下每条数据的"前世今生"——插入前的空、插入后的值、更新前的旧值、更新后的新值，甚至表结构的变化（DDL）。

**小胖**：那我直接查数据库的更新字段不就行了？`SELECT * FROM table WHERE update_time > ?`——简单粗暴，何必搞个CDC这么复杂？

**大师**：你说到早期很多公司的做法了。这叫**基于查询的CDC（Query-based CDC）**，看起来很美好，但三个致命问题：
1. **无法捕获DELETE**——数据都被删了，你还查什么？
2. **频繁查询压力**——每秒钟轮询一次，数据库CPU飙升，主库扛不住
3. **无法捕获物理删除和TRUNCATE**——有些业务真的物理删除数据

**小白**（若有所思）：那基于日志的CDC是怎么做到的？我理解数据库的Binlog（二进制日志）记录了所有变更，但格式复杂，难道要自己解析吗？而且不同数据库的日志格式完全不一样吧？

**大师**：这正是CDC领域最核心的工程挑战！每个数据库都有自己独特的日志格式：
- MySQL → Binlog（ROW格式下的binlog_row_image）
- PostgreSQL → WAL（Write-Ahead Log，通过逻辑复制插件输出）
- MongoDB → Oplog + Change Streams
- Oracle → Redo Log（通过LogMiner解析）

如果每个团队都自己去解析这些日志，那整个行业都在重复造轮子。所以**Debezium**应运而生——它是一个开源的分布式CDC平台，封装了所有主流数据库的日志解析逻辑，对外输出统一的变更事件格式。

**小白**：那Flink CDC和Debezium是什么关系？为什么有了Debezium还需要Flink CDC？

**大师**：这就是问题的关键了！Debezium解决了"读懂数据库日志"的问题，但它只是一个日志解析库——它本身不是流处理引擎。你可以把Debezium理解为一个**翻译官**，它把各种数据库的方言（Binlog、WAL、Redo Log）翻译成统一的普通话（Change Event）。但它不负责"怎么处理这些事件"。

Flink CDC = **Debezium（翻译官）+ Flink（聪明的调度员）**。Flink提供了：
1.  **容错**：Checkpoint机制保证不丢不重
2.  **状态管理**：实时聚合、去重、关联
3.  **并行读取**：多线程并发读取多张表，支持大规模并行
4.  **端到端一致性**：Exactly-Once写入下游

**技术映射**：Debezium就像电视信号解码器，把各种格式的信号解码成标准视频流；而Flink就是智能电视，不仅播放视频，还能录播（状态）、回放（容错）、画中画（多流Join）。

**小胖**：哦我懂了！那Flink CDC和其他CDC方案（Canal、Maxwell、DataX）比，到底强在哪？我看Canal也能同步MySQL到Kafka啊？

**大师**：这个问题非常实际。我们做个对比：

| 对比维度 | Flink CDC | Canal | Maxwell | DataX |
|---------|-----------|-------|---------|-------|
| **数据源** | MySQL/PG/Oracle/MongoDB等7+ | 仅MySQL | 仅MySQL | 30+（偏批量） |
| **处理能力** | Flink全栈（状态窗口、Join、聚合） | 仅转发，无计算 | 仅转发，无计算 | 纯批量，无实时 |
| **容错** | Exactly-Once + Checkpoint | At-Least-Once | At-Least-Once | 不支持增量 |
| **并行度** | 支持并行快照+并行读取 | 单线程解析Binlog | 单线程 | 多线程（批量） |
| **部署** | 内嵌在Flink作业中，无需独立集群 | 需要独立部署Canal Server + ZooKeeper | 需要独立部署 | 独立进程 |
| **Schema变更** | 支持自动Schema Evolution | 需手动处理 | 需手动处理 | 需手动处理 |

**小白**：Flink CDC在架构上还有一个有意思的设计——**增量快照（Incremental Snapshot）**。我听说它不需要锁表就能做全量同步，而且还能和增量数据无缝衔接？

**大师**：没错，这是Flink CDC最引以为傲的特性之一！传统的MySQL数据同步工具在做全量初始化时，通常需要`FLUSH TABLES WITH READ LOCK`——给整库加全局读锁，阻塞所有写入。这在线上环境是不可接受的。

Flink CDC的**增量快照算法（基于FLIP-27）**完美解决了这个问题：
1. 将表按主键范围分成多个Chunk（数据块）
2. 多线程并行读取每个Chunk，读取时记录当前的Binlog位置
3. Chunk全部读取完毕后，对增量数据进行"水位对齐"，补上在快照期间产生的变更
4. 最终输出一个**在时间点上完全一致**的全量+增量数据集

整个过程不需要任何锁，对源库几乎是零侵入。

---

## 3 项目实战

### 环境准备

**依赖与版本：**
- Flink CDC版本：3.0+（基于Flink 1.20+）
- Debezium版本：1.9.8.Final（内嵌在Flink CDC中，不需要单独安装）
- Java 11+

**Maven依赖：**
```xml
<!-- Flink CDC核心POM（BOM） -->
<dependencyManagement>
    <dependencies>
        <dependency>
            <groupId>org.apache.flink</groupId>
            <artifactId>flink-cdc-parent</artifactId>
            <version>3.0.0</version>
            <type>pom</type>
            <scope>import</scope>
        </dependency>
    </dependencies>
</dependencyManagement>

<!-- MySQL CDC连接器 -->
<dependency>
    <groupId>org.apache.flink</groupId>
    <artifactId>flink-connector-mysql-cdc</artifactId>
    <version>3.0.0</version>
</dependency>

<!-- PostgreSQL CDC连接器 -->
<dependency>
    <groupId>org.apache.flink</groupId>
    <artifactId>flink-connector-postgres-cdc</artifactId>
    <version>3.0.0</version>
</dependency>
```

### 分步实现

#### 步骤1：编写程序——获取MySQL所有表的结构信息（Metadata Accessor）

这个Demo不启动CDC读取，而是演示Flink CDC中最重要的底层接口之一——**MetadataAccessor**，帮助理解CDC如何获取数据库元信息。

```java
package com.example;

import org.apache.flink.cdc.connectors.mysql.source.MySqlSource;
import org.apache.flink.cdc.connectors.mysql.table.StartupOptions;
import org.apache.flink.cdc.debezium.DebeziumDeserializationSchema;
import org.apache.flink.cdc.debezium.JsonDebeziumDeserializationSchema;
import org.apache.flink.streaming.api.datastream.DataStreamSource;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;

public class FlinkCdcOverview {

    public static void main(String[] args) throws Exception {
        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        env.enableCheckpointing(5000);
        env.setParallelism(1);

        // 构建MySQL Source：使用JSON格式输出Debezium事件
        // 这样可以看到CDC事件的完整结构：schema、payload、before/after
        MySqlSource<String> mySqlSource = MySqlSource.<String>builder()
            .hostname("localhost")
            .port(3306)
            .databaseList("shop")        // 监控的数据库，支持正则
            .tableList("shop.orders")    // 监控的表，格式为db.table
            .username("cdc_user")
            .password("cdc_pass")
            .deserializer(new JsonDebeziumDeserializationSchema()) // JSON格式输出
            .startupOptions(StartupOptions.latest())  // 只读取最新的增量数据
            .serverTimeZone("Asia/Shanghai")
            .build();

        DataStreamSource<String> cdcStream = env
            .fromSource(mySqlSource, org.apache.flink.api.common.eventtime
                .WatermarkStrategy.noWatermarks(), "MySQL CDC Source");

        cdcStream.print();

        env.execute("Flink CDC Overview Demo");
    }
}
```

#### 步骤2：MySQL CDC事件的JSON格式解析

当orders表发生INSERT时，Flink CDC输出的一条典型事件如下：

```json
{
  "schema": {
    "type": "struct",
    "fields": [
      {"field": "before", "type": "struct", "fields": [...]},
      {"field": "after",  "type": "struct", "fields": [...]},
      {"field": "source", "type": "struct", "fields": [
        {"field": "version", "type": "string"},
        {"field": "connector", "type": "string"},
        {"field": "name", "type": "string"},
        {"field": "db", "type": "string"},
        {"field": "table", "type": "string"},
        {"field": "server_id", "type": "int32"},
        {"field": "gtid", "type": "string"},
        {"field": "file", "type": "string"},
        {"field": "pos", "type": "int64"},
        {"field": "row", "type": "int32"},
        {"field": "snapshot", "type": "boolean"},
        {"field": "ts_ms", "type": "int64"}
      ]},
      {"field": "op", "type": "string"},
      {"field": "ts_ms", "type": "int64"}
    ]
  },
  "payload": {
    "before": null,
    "after": {
      "id": 1001,
      "user_id": 42,
      "product": "iPhone 15",
      "amount": 6999.00,
      "status": "PAID",
      "create_time": 1714377600000
    },
    "source": {
      "version": "1.9.8.Final",
      "connector": "mysql",
      "name": "mysql_binlog_source",
      "db": "shop",
      "table": "orders",
      "server_id": 1,
      "gtid": "a2b3c4d5-e6f7-8g9h-0i1j-2k3l4m5n6o7p:1-42",
      "file": "mysql-bin.000042",
      "pos": 12345,
      "row": 0,
      "snapshot": false,
      "ts_ms": 1714377601000
    },
    "op": "c",
    "ts_ms": 1714377601000
  }
}
```

**事件字段解读：**

| 字段路径 | 含义 | 取值示例 |
|---------|------|---------|
| `payload.op` | 操作类型 | `c`=CREATE(INSERT), `u`=UPDATE, `d`=DELETE, `r`=READ(快照) |
| `payload.before` | 变更前数据 | UPDATE/DELETE时有值，INSERT时为null |
| `payload.after` | 变更后数据 | INSERT/UPDATE时有值，DELETE时为null |
| `payload.source.file` | Binlog文件名 | 用于断点续传的定位 |
| `payload.source.pos` | Binlog文件位移 | 结合file确定精确位置 |
| `payload.source.gtid` | GTID（MySQL 5.6+） | 全局事务ID，主从切换不断流的关键 |
| `payload.source.snapshot` | 是否为快照阶段 | `true`=全量快照，`false`=增量日志 |

#### 步骤3：观察不同操作类型的事件差异

在MySQL中依次执行以下SQL，观察Flink CDC的输出：

```sql
-- INSERT: op=c, before=null, after={新数据}
INSERT INTO shop.orders VALUES (1001, 42, 'iPhone 15', 6999.00, 'PAID', NOW());

-- UPDATE: op=u, before={旧数据}, after={新数据}
UPDATE shop.orders SET amount = 6499.00 WHERE id = 1001;

-- DELETE: op=d, before={被删除的数据}, after=null
DELETE FROM shop.orders WHERE id = 1001;
```

**Flink控制台输出示例：**
```
# INSERT事件
{"payload":{"before":null,"after":{"id":1001,...,"status":"PAID"},"op":"c",...}}

# UPDATE事件（注意before和after都有值）
{"payload":{"before":{"id":1001,...,"amount":6999.00},"after":{"id":1001,...,"amount":6499.00},"op":"u",...}}

# DELETE事件（注意after为null）
{"payload":{"before":{"id":1001,...},"after":null,"op":"d",...}}
```

#### 步骤4：与Canal对比——Flink CDC的差异化体验

如果你用过Canal，可以试试用同样的场景对比：

| 操作 | Canal输出 | Flink CDC输出 |
|------|----------|--------------|
| 数据格式 | Canal.protobuf格式，需额外解析 | JSON/Debezium格式，自带Schema |
| 元信息 | 需额外从Canal Admin获取 | 事件中自带（Binlog位置、GTID、Server ID） |
| Schema变更 | Canal仅转发DDL字符串 | Flink CDC可解析为`SchemaChangeEvent`结构化对象 |
| 断点续传 | 依赖ZooKeeper记录位置 | 使用Flink Checkpoint存储offset |
| 并行度 | Canal Server单线程解析 | 支持多Source并行、多Sink并行 |

#### 常见陷坑及解决方法

| 问题 | 原因 | 解决方法 |
|------|------|---------|
| 事件输出为空（无数据） | MySQL Binlog格式不是ROW | 检查`SHOW VARIABLES LIKE 'binlog_format'`，确保为ROW |
| 连接失败 | CDC用户权限不足 | 需要`SELECT, RELOAD, SHOW DATABASES, REPLICATION SLAVE, REPLICATION CLIENT`权限 |
| 全量快照导致源库CPU飙升 | 大表无主键，Flink CDC退化为单线程读取 | 为大表添加主键，或配置`scan.incremental.snapshot.chunk.size`调小 |
| JSON事件无法反序列化 | 包含MySQL特殊类型（如GEOMETRY、YEAR(4)） | 更换为自定义`DebeziumDeserializationSchema`，指定特殊类型处理 |

---

## 4 项目总结

### 优点 & 缺点

**Flink CDC的优势：**
1.  **多源统一**：一套API同时支持MySQL/PostgreSQL/Oracle/MongoDB等7+数据库
2.  **真正的流处理**：在Flink生态内完成计算、转换、写入，端到端Exactly-Once
3.  **无锁快照**：增量快照算法实现无锁全量数据读取，对线上零侵入
4.  **Schema Evolution**：自动感知源表DDL变更，同步更新目标表结构
5.  **生态丰富**：可写入Kafka/Iceberg/Paimon/Doris/Elasticsearch等20+目标

**Flink CDC的局限：**
1.  **学习曲线陡峭**：需要同时理解Flink + Debezium + 各数据库CDC原理
2.  **Flink版本绑定**：Flink CDC 3.x仅兼容Flink 1.20+，版本升级成本高
3.  **资源消耗较高**：相比Canal等纯转发工具，Flink需要更多内存和CPU
4.  **小众数据库支持弱**：DB2、Vitess等连接器成熟度不如MySQL

### 适用场景

**典型场景：**
1. **实时数据入湖**：MySQL/Oracle → Iceberg/Paimon/Hudi，构建实时数据湖
2. **异构数据库同步**：MySQL → PostgreSQL / Oracle → MySQL，跨数据库迁移
3. **实时数仓ODS层**：业务库变更实时写入Kafka/Doris，作为数仓数据源
4. **缓存失效/搜索引擎更新**：数据库变更实时同步到Redis/Elasticsearch
5. **微服务数据解耦**：通过CDC替代双写，解除微服务间的数据库耦合

**不适用场景：**
1. **纯文本日志解析**（如Nginx日志、App埋点日志）：使用Flink直接消费Kafka更简单
2. **对延迟极度敏感（< 1ms）的场景**：CDC本身有解析开销，极限延迟在10~100ms级别

### 注意事项

1. **MySQL server-id冲突**：每个Flink CDC作业必须使用唯一的server-id，否则会导致Binlog连接断开。使用`server-id`配置项指定范围，例如`5400-5409`（按并行度分配）。
2. **Debezium版本兼容**：不同Flink CDC版本内嵌的Debezium版本不同（1.6/1.8/1.9），功能差异较大。GTID支持、增量快照稳定性等特性随版本提升。
3. **全量+增量切换点**：增量快照的"水位对齐"依赖MySQL的Binlog位点，确保快照期间Binlog不被清理。建议设置`expire_logs_days=7`以上。
4. **表名大小写**：MySQL在大小写敏感配置下（`lower_case_table_names=0`），表名需要精确匹配。

### 常见踩坑经验

**故障案例1：增量快照卡死，无法进入流式读取阶段**
- **现象**：Flink CDC作业完成全量快照后，卡在"正在追增量"状态
- **根因**：Chunk切分时选取的split key包含NULL值，导致INCREMENTAL快照阶段的断点续传死循环
- **解决方案**：升级Flink CDC到3.0+（修复了NULL key问题），或在MySQL表上设置NOT NULL + 默认值

**故障案例2：主从切换后CDC作业彻底失联**
- **现象**：MySQL发生主从切换，Flink CDC作业报错`Lost connection to MySQL server`并无法自动恢复
- **根因**：切换后新主的Binlog文件名和GTID集合变化，Flink CDC无法根据旧的Checkpoint位置找到新主的对应位置
- **解决方案**：开启GTID模式（`gtid_mode=ON`），配置`startupOptions=StartupOptions.earliest()`并在连接参数中添加`useSSL=false&allowPublicKeyRetrieval=true`

**故障案例3：Flink CDC读取Oracle产生大量Undo日志**
- **现象**：Oracle数据库的Undo表空间爆满
- **根因**：Oracle LogMiner在处理大事务时，需要在Undo表空间记录数据前镜像
- **解决方案**：配置`debezium.log.mining.strategy=online_catalog`和`debezium.log.mining.transaction.retention.hours=1`

### 思考题

1. **进阶题①**：在Flink CDC 3.x中，`MySqlSource`同时支持`SnapshotSplit`（快照分片）和`StreamSplit`（增量流）两种Split类型。这两种Split在`HybridSplitAssigner`中的切换时机是什么？如何保证全量和增量数据在时间上不重叠、不丢失？

2. **进阶题②**：Flink CDC在读取PostgreSQL时使用的是逻辑复制槽（Logical Replication Slot）。如果一个Flink CDC作业暂停了24小时再恢复，PostgreSQL的WAL日志可能已经被清理了。Flink CDC如何检测并处理这种"日志断层"的场景？对比MySQL的Binlog保留策略，两种方案各有什么优缺点？

---

> **下一章预告**：第3章「环境搭建：Docker Compose一键部署」——你将亲手搭建一套完整的Flink CDC开发环境，包括MySQL、Kafka、Flink集群，10分钟内跑通第一个CDC流水线。
