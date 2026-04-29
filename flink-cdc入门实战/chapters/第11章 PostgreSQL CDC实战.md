# 第11章 PostgreSQL CDC实战

## 1 项目背景

### 业务场景：微服务从MySQL迁移到PostgreSQL

公司用户服务因业务增长，决定从MySQL迁移到PostgreSQL，看中PostgreSQL对JSONB的原生支持、更强大的索引能力（GIN、GiST）以及开源协议友好。但数据层迁移面临一个核心问题：**在迁移过渡期，需要将MySQL的存量用户数据实时同步到PostgreSQL**，并持续保持两个数据库的数据一致。

这个场景下，Flink CDC的PostgreSQL连接器成为了关键组件——它既可以从PostgreSQL读取CDC事件，也可以作为目标库写入。

### PostgreSQL逻辑复制架构

```
┌────────────────────┐
│   PostgreSQL       │
│                    │
│  ┌──────────────┐  │
│  │  WAL (Write  │  │
│  │  Ahead Log)  │  │
│  └──────┬───────┘  │
│         │           │
│  ┌──────┴───────┐  │
│  │ Logical       │  │
│  │ Decoding      │  │  ← 将WAL解析为逻辑变更
│  │ (pgoutput)    │  │
│  └──────┬───────┘  │
│         │           │
│  ┌──────┴───────┐  │
│  │ Replication   │  │
│  │ Slot          │  │  ← 逻辑复制槽（持久化位点）
│  └──────┬───────┘  │
└─────────┼──────────┘
          │ WAL Sender 流式传输
          ▼
┌────────────────────┐
│  Flink CDC         │
│  PostgresSource    │
│  (Debezium Engine) │
└────────────────────┘
```

---

## 2 项目设计 · 三人交锋对话

**小胖**（好奇）：PostgreSQL的CDC和MySQL有啥区别？是不是就是把`mysql-cdc`改成`postgres-cdc`？

**大师**：API层面确实如此——替换连接器和配置参数即可。但底层机制完全不同，三个最大的差异：

1. **复制机制**：MySQL用Binlog，PostgreSQL用逻辑复制（Logical Replication）
2. **位点管理**：MySQL用`(filename, position)`或GTID，PG用复制槽（Replication Slot）
3. **WAL保留策略**：MySQL靠`expire_log_days`定时清理，PG靠Slot存在与否决定保留

**小白**：复制槽（Replication Slot）到底是啥？我查PG文档看得云里雾里。

**大师**：复制槽是PostgreSQL逻辑复制的"锚点"。当你创建一个Slot时，PG会记住"这个消费者已经消费到WAL的哪个位置了"。只要Slot没有被删除，PG就不会清理该位置之前的WAL日志。

这和MySQL的Binlog有本质区别：
- MySQL：清理策略是"按时间全量清理"（不管消费者）
- PG：清理策略是"按Slot位置清理"（只清理所有Slot已确认的位置）

所以PG的CDC有个显著优势：**作业停机几天几夜，回来还能从断点续传**（只要Slot没被删除）。

但这也意味着：**如果你再也不回去了，Slot会永远保留，WAL会无限增长，最终撑爆磁盘！**

**技术映射**：复制槽就像"图书馆的借书记录"——只要你有书没还（Slot未消费），图书馆就得保留你的座位（保留WAL）。可如果你退学了再也不来，座位就永远空着占地方。

**小白**：那Publication（发布）又是什么？和Slot有什么区别？

**大师**：Publication和Slot是逻辑复制的两个维度：

| 概念 | 类比 | 作用 |
|------|------|------|
| **Publication（发布）** | 直播间的"内容列表" | 定义"哪些表的数据可以被订阅"（白名单） |
| **Replication Slot（复制槽）** | 观众的"观看进度" | 记录"你已经看到哪条变更了"（断点续传） |

创建Publication相当于告诉PG："我要开始直播了，直播内容包括orders表和users表的数据变更"。
创建Slot相当于："我在观看了，我上次看到第1000条变更，继续从第1001条开始播放给我"。

Flink CDC的`PostgresSource`会自动创建和管理Slot，你不需要手动操作。但你需要在PG中先创建好Publication。

---

## 3 项目实战

### 环境准备

使用Docker Compose扩展一个PostgreSQL服务。

**docker-compose.yml新增PostgreSQL配置：**
```yaml
postgres:
  image: postgres:15
  container_name: postgres-cdc
  ports:
    - "5432:5432"
  environment:
    POSTGRES_DB: shop_pg
    POSTGRES_USER: cdc_user
    POSTGRES_PASSWORD: cdc_pass
  command:
    - "postgres"
    - "-c"
    - "wal_level=logical"
    - "-c"
    - "max_replication_slots=10"
    - "-c"
    - "max_wal_senders=10"
  volumes:
    - pg_data:/var/lib/postgresql/data
  networks:
    - flink-cdc-net
```

**Maven依赖：**
```xml
<dependency>
    <groupId>org.apache.flink</groupId>
    <artifactId>flink-connector-postgres-cdc</artifactId>
    <version>3.0.0</version>
</dependency>
```

### 分步实现

#### 步骤1：配置PostgreSQL逻辑复制

```sql
-- 1. 验证WAL级别
SHOW wal_level;
-- 必须为 logical，如果不是则修改 postgresql.conf 后重启

-- 2. 创建测试表
CREATE TABLE users (
    id          SERIAL PRIMARY KEY,
    username    VARCHAR(64) NOT NULL,
    email       VARCHAR(128),
    level       VARCHAR(32) DEFAULT 'normal',
    points      INT DEFAULT 0,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 3. 插入初始数据
INSERT INTO users (username, email, level, points) VALUES
('alice', 'alice@example.com', 'VIP', 5000),
('bob', 'bob@example.com', 'normal', 100),
('charlie', 'charlie@example.com', 'VIP', 10000);

-- 4. 创建Publication（发布——告诉PG要复制哪些表的数据）
CREATE PUBLICATION cdc_pub FOR TABLE users;

-- 验证Publication
SELECT * FROM pg_publication;
```

#### 步骤2：编写Flink CDC读取PostgreSQL

```java
package com.example;

import org.apache.flink.api.common.eventtime.WatermarkStrategy;
import org.apache.flink.cdc.connectors.postgres.source.PostgresSource;
import org.apache.flink.cdc.debezium.JsonDebeziumDeserializationSchema;
import org.apache.flink.streaming.api.datastream.DataStreamSource;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;

/**
 * PostgreSQL CDC读取演示
 */
public class PostgresCdcDemo {

    public static void main(String[] args) throws Exception {
        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        env.enableCheckpointing(5000);

        PostgresSource<String> source = PostgresSource.<String>builder()
            .hostname("localhost")
            .port(5432)
            .database("shop_pg")                     // 数据库名
            .schemaList("public")                    // Schema列表
            .tableList("public.users")               // 表列表（格式: schema.table）
            .username("cdc_user")
            .password("cdc_pass")
            .deserializer(new JsonDebeziumDeserializationSchema())
            // PostgreSQL特有配置
            .slotName("flink_cdc_slot")              // 复制槽名称（全局唯一）
            .publicationName("cdc_pub")              // Publication名称
            .decodingPluginName("pgoutput")          // 解码插件
            .build();

        DataStreamSource<String> pgStream = env.fromSource(
            source,
            WatermarkStrategy.noWatermarks(),
            "PostgreSQL CDC Source");

        pgStream.print();

        env.execute("PostgreSQL CDC Demo");
    }
}
```

#### 步骤3：执行变更观察CDC输出

```sql
-- 在PostgreSQL中执行：
INSERT INTO users (username, email, level, points) VALUES ('dave', 'dave@example.com', 'VIP', 20000);
UPDATE users SET points = 6000 WHERE username = 'alice';
DELETE FROM users WHERE username = 'bob';
```

**Flink CDC事件输出：**

INSERT事件（PostgreSQL格式）：
```json
{
  "payload": {
    "before": null,
    "after": {"id":4,"username":"dave","email":"dave@example.com","level":"VIP","points":20000},
    "op": "c",
    "source": {
      "version": "1.9.8.Final",
      "connector": "postgresql",
      "name": "pg_connector",
      "db": "shop_pg",
      "schema": "public",
      "table": "users",
      "lsn": 12345678,
      "snapshot": false,
      "ts_ms": 1714377601000
    }
  }
}
```

**与MySQL事件的差异：**
- `source.connector: "postgresql"`（不是mysql）
- `source.lsn` 替代 `source.file` + `source.pos`（PG用Log Sequence Number）
- `source.schema` 字段（PG的Schema是独立维度，MySQL没有）

#### 步骤4：复制槽管理——查看和清理手动创建的Slot

生产环境中需要监控和管理复制槽：

```sql
-- 查看所有复制槽状态（核心监控SQL）
SELECT
    slot_name,
    slot_type,
    database,
    active,                          -- true=正在被消费；false=闲置
    restart_lsn,                     -- 最早未被消费的WAL位置
    confirmed_flush_lsn,             -- 已确认消费的WAL位置
    pg_size_pretty(
        pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)
    ) AS wal_lag_size                -- 当前WAL积压量
FROM pg_replication_slots;

-- 手动删除复制槽（Flink CDC作业彻底下线后执行！）
SELECT pg_drop_replication_slot('flink_cdc_slot');
```

**Slot监控的告警阈值：**
- `active = false` 且超过30分钟 → 告警（Slot未被消费）
- `wal_lag_size > 1GB` → 警告（WAL积压过多）
- `wal_lag_size > 10GB` → 严重告警（磁盘可能爆满）

#### 步骤5：PostgreSQL → MySQL跨数据库同步

```java
package com.example;

import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.cdc.connectors.postgres.source.PostgresSource;
import org.apache.flink.cdc.debezium.JsonDebeziumDeserializationSchema;

/**
 * 跨数据库实时同步：PostgreSQL → Kafka（供MySQL消费）
 */
public class PgToKafkaSync {

    public static void main(String[] args) throws Exception {
        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        env.enableCheckpointing(5000);

        PostgresSource<String> source = PostgresSource.<String>builder()
            .hostname("localhost").port(5432)
            .database("shop_pg").schemaList("public").tableList("public.users")
            .username("cdc_user").password("cdc_pass")
            .deserializer(new JsonDebeziumDeserializationSchema())
            .slotName("flink_cdc_slot_sync")
            .publicationName("cdc_pub")
            .decodingPluginName("pgoutput")
            .build();

        env.fromSource(source,
            org.apache.flink.api.common.eventtime.WatermarkStrategy.noWatermarks(),
            "PG CDC")
            .map(json -> {
                // 转换JSON格式（PG→MySQL兼容）
                // PG的BIGINT对应MySQL的BIGINT，SERIAL对应INT AUTO_INCREMENT
                return json;
            })
            .print(); // 替换为KafkaSink

        env.execute("PG → Kafka Sync");
    }
}
```

#### 常见陷坑及解决方法

| 问题 | 原因 | 解决方法 |
|------|------|---------|
| Slot未找到报错 | Slot名称拼写错误或尚未创建 | `slotName()`设置唯一名称，Flink CDC会自动创建 |
| Publication不存在 | 忘记创建或名称不匹配 | 检查`SELECT * FROM pg_publication;` |
| WAL日志耗尽磁盘 | Slot不再使用但未删除 | 定期巡检`pg_replication_slots`，无用Slot及时删除 |
| `decoderbufs`插件不存在 | 需要安装protobuf解码器 | 使用`pgoutput`（PG 10+内置，无需安装） |
| 权限不足无法创建Slot | CDC用户缺少REPLICATION权限 | `ALTER USER cdc_user WITH REPLICATION;` |

---

## 4 项目总结

### MySQL vs PostgreSQL CDC对比

| 维度 | MySQL CDC | PostgreSQL CDC |
|------|----------|---------------|
| 日志机制 | Binlog（二进制日志） | WAL（预写日志）+ 逻辑解码 |
| 位点类型 | `(filename, position)` + GTID | LSN（Log Sequence Number） |
| 断点续传 | 依赖Checkpoint + Binlog保留 | 复制槽自动持久化位点 |
| 表标识 | `database.table` | `database.schema.table` |
| 主键约束 | 必须主键才能支持UPDATE/DELETE | 必须主键或REPLICA IDENTITY FULL |
| 快照模式 | 增量快照算法（FLIP-27，无锁） | 普通快照（短时读锁） |
| 超大表处理 | Chunk并行切分 | 串行快照（无Chunk机制） |
| WAL/Binlog清理 | `expire_log_days`统一清理 | 按Slot位置（可无限保留） |

### 适用场景

**PostgreSQL CDC典型场景：**
1. **PG → Kafka实时入湖**：PG作为OLTP库，数据变更实时同步到Kafka供流式计算
2. **PG → Elasticsearch**：全文搜索场景，PG数据实时同步到ES
3. **MySQL → PG数据迁移**：迁移过渡期双向同步，验证数据一致性
4. **PG变更数据审计**：所有数据变更记录审计日志

**不适用场景：**
1. PG版本低于9.4（不支持逻辑复制）
2. 使用Pgbouncer等连接池的场景（需要直连PostgreSQL，不支持连接池中间件）

### 注意事项

1. **REPLICA IDENTITY**：PostgreSQL表默认使用主键作为"行的唯一标识"。如果表没有主键，需设置`REPLICA IDENTITY FULL`（记录所有列的旧值），但这会显著增加WAL日志量。
2. **解码插件选择**：`pgoutput`（PG 10+内置，推荐）、`decoderbufs`（需要protobuf，性能好）、`wal2json`（JSON输出，调试用）。生产环境推荐`pgoutput`。
3. **网络连接**：PostgreSQL的逻辑复制依赖长连接WAL Sender进程，网络中断会触发自动重连，但重连期间的WAL不会被消费——确保Slot保留足够日志。

### 常见踩坑经验

**故障案例1：PG CDC消费中断后恢复，发现WAL已被清理**
- **现象**：Flink CDC作业停止5天后恢复，报错`requested WAL segment ... has already been removed`
- **根因**：PG参数`wal_keep_size`（PG 13+）或`wal_keep_segments`（PG 12以下）限制了WAL保留数量，即使Slot存在超过保留量的WAL也会被清理
- **解决方案**：设置`wal_keep_size = 2048`（至少保留2GB WAL），或监控`pg_replication_slots`的`restart_lsn`，确保不再需要的Slot及时删除

**故障案例2：大事务导致PG CDC内存溢出**
- **现象**：执行`UPDATE users SET points = 0`（更新全表1000万行）后，Flink CDC OOM
- **根因**：Debezium会缓存整个大事务的所有变更事件到内存，等事务提交后再一次性输出
- **解决方案**：设置`debezium.max.batch.size=10240`限制每批事件数；或在大事务执行前暂停CDC

**故障案例3：无主键表的UPDATE事件解析错误**
- **现象**：无主键表执行UPDATE后，CDC输出的`before`全部为null
- **根因**：默认REPLICA IDENTITY使用主键，无主键时无法唯一标识行。PG不会记录update前的"整行镜像"
- **解决方案**：`ALTER TABLE users REPLICA IDENTITY FULL;`——这会增加WAL日志量（before包含所有列）

### 思考题

1. **进阶题①**：PostgreSQL的逻辑复制槽可以在同一张表上创建多个Slot（被不同的消费者使用）。如果启动了两个Flink CDC作业监控同一张users表，但使用了不同的Slot名称，每个作业都能收到所有变更吗？还是只有其中一个能收到？

2. **进阶题②**：`decoding.plugin.name`有`pgoutput`、`decoderbufs`、`wal2json`三种选择。其中`wal2json`将每个事务输出为一个JSON文档，这和其他两种插件的逐行输出格式有什么不同？在Flink CDC中为什么推荐使用`pgoutput`？

---

> **下一章预告**：第12章「MongoDB CDC实战」——从关系型数据库到文档型数据库，你将学会MongoDB Change Streams原理、嵌套文档的处理、ObjectId映射等核心内容。
