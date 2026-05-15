# 第34章：数据库适配器（Database Adapter）体系与新增数据库支持

## 1. 项目背景

"上周公司收购了一家使用 TiDB 的创业团队，今天 CTO 问我：'Debezium 能接 TiDB 吗？如果不能，我们自己开发一个 Connector 要多久？'"——某支付平台架构师在技术评审会上被问到这个问题，全场沉默。

这不是孤例。在企业环境中，数据库种类远不止 MySQL + PG + MongoDB 三种。你可能会遇到 TiDB（宣称兼容 MySQL binlog 协议）、CockroachDB（有自己的 Changefeeds 机制）、ClickHouse（没有传统事务日志）、OceanBase（自研分布式数据库）、甚至是内部自研的 KV 存储。面对这些"非标准"数据库，架构师的第一个问题总是：**能复用现有 Connector 吗？不能的话，从零开发的成本、工期、风险有多大？**

回答这些问题的关键在于深入理解 Debezium 的**数据库适配器（Database Adapter）体系**——这是一套四层抽象接口，将不同数据库的差异封装在 **Connection（连接管理）、Snapshotter（快照策略）、ChangeEventSource（变更捕获）、TypeConverter（类型映射）** 四个可替换的模块中。MySQL binlog → BinaryLogClient + EventDeserializer，PG WAL → Replication Slot + pgoutput，MongoDB Oplog → Change Streams——本质都是"事务日志"，只是读取方式和数据格式不同。

### 痛点放大

| 数据库 | 事务日志机制 | 适配难点 | 最坏后果 |
|--------|------------|---------|---------|
| TiDB | TiCDC（兼容 binlog） | DDL 集群同步延迟，GC 水位可能比 binlog 窗口短 | Connector 读到半成品 Schema，快照数据与 binlog 位点不一致 |
| CockroachDB | Changefeeds（KV 时间戳） | 与 PG Logical Replication 完全不同，没有 Publication/Slot | 不能直接用 PG Connector，需完整开发 ChangeEventSource |
| Oracle 19c+ | Redo Log → LogMiner | LogMiner 性能开销大，XStream 需额外 license | 高负载下 LogMiner 拖慢数据库 |
| ClickHouse | 无事务日志 | **完全没有 Native 变更流** | 只能用定时轮询（延迟 ≥ 轮询间隔），无法捕获 DELETE |
| 自研数据库 | 不确定 | 需要从零分析 | 开发周期 1-6 个月 |

## 2. 项目设计——三人对话

**（周一下午技术评审会，白板上画满了各种数据库的架构图）**

**小胖**："大师，CTO 给了一周时间评估 TiDB 接入 Debezium 的可行性。TiDB 的文档说'100% 兼容 MySQL 8.0 binlog 协议'——我就直接把 MySQL Connector 的 hostname 改成 TiDB 地址，能跑吗？"

**大师**："能跑——但不一定稳定。TiDB 通过 TiCDC 组件暴露了兼容 MySQL replication 协议的端口。BinaryLogClient 可以连上去消费。但你必须处理三个 TiDB 特有的'地雷'——"

**小胖**："三个地雷？快和我说说！"

**大师**：

"**第一颗雷：DDL 同步延迟**。MySQL 的 `ALTER TABLE` 在单节点上是原子操作——执行完立刻生效。但 TiDB 是分布式数据库，DDL 需要在集群所有 TiKV 节点上同步 Schema 变更——这个过程可能需要数秒到数十秒。如果 Connector 在 DDL 执行后的第一秒就消费到了包含新 Schema 的 binlog，但某些 TiKV 节点上 Schema 还没同步完——Connector 可能读到'半成品'表结构，导致 EventDeserializer 反序列化失败。"

"**第二颗雷：tidb_snapshot 与 MVCC 一致性**。TiDB 使用 MVCC 机制支持历史时间点读。快照阶段如果设置了 `SET @@tidb_snapshot = NOW()`，Connector 会在一个固定的 MVCC 时间点读取全量数据。但这个时间点和你记录的 binlog 起始位点之间，可能存在一个'窗口期'——在这个窗口期内提交的事务，既不在快照中，也不在 binlog streaming 中（因为 binlog 位点在快照时间点之后）。这就导致了数据丢失。"

"**第三颗雷：tikv_gc_safe_point**。TiDB 的 GC 机制会定期清理过期的 MVCC 版本。GC 的安全水位（safe point）由 `tikv_gc_safe_point` 控制——通常是当前时间减去 `tikv_gc_life_time`（默认 10 分钟）。如果你的 Connector 因为故障停了 15 分钟，恢复时 offset 指向的 binlog 位点还在，但该位点对应的 MVCC 数据可能已经被 GC 清理了——某些需要读历史版本的查询会失败。"

**小白**："那有没有办法规避这三颗雷？"

**大师**："有——针对性地配置。对于第一颗雷：在 TiDB 上设置 `tidb_enable_change_multi_schema = OFF`，让 DDL 逐个 Schema 变更而不是批量——虽然慢一些但 Connector 能更好地追踪。对于第二颗雷：快照开始前记录 binlog 位点 → 快照完成后，从位点开始 streaming，同时用增量快照的水印机制标记快照边界，让 Connector 自动去重。对于第三颗雷：把 `tikv_gc_life_time` 从默认 10 分钟调到 2 小时，并且监控 GC safe point 和 Connector offset 的差距——当差距 < 5 分钟时发送告警。"

**小胖**："那 CockroachDB 呢？它也说自己兼容 PG 协议，能用 PG Connector 吗？"

**大师**："这个坑更深。CRDB 的 SQL 语法确实兼容 PG——但它的 Changefeeds（变更捕获）机制和 PG 的 Logical Replication 是完全不同的两套协议：

- PG 的 Logical Replication 基于 WAL（Write-Ahead Log），通过 Replication Slot 维持消费位点，pgoutput 插件解码 WAL 记录
- CRDB 的 Changefeed 基于 KV 层的 MVCC 时间戳，通过 `EXPERIMENTAL CHANGEFEED FOR` 语句创建，返回的是 JSON 格式的变更事件

虽然 SQL 语法兼容，但底层的复制协议完全不同——**不能**直接用 PG Connector。需要基于 CRDB 的 Changefeed API 开发新的 `ChangeEventSource` 实现。好消息是 Connection（JDBC 连接）、Snapshotter（`SELECT *` 全量快照）、TypeConverter（类型映射）这三个模块可以大量复用 PG Connector 的代码——只需要替换 ChangeEventSource 这一层。"

**技术映射**：Connection = 门禁系统——每种数据库有自己的一套认证和连接方式，但对外暴露同样的"开门"接口。Snapshotter = 库房盘点方式——有些库房支持"一键全锁盘点"（MySQL FLUSH TABLES），有些不支持但可以用"MVCC 时间点拍照"（TiDB tidb_snapshot）。ChangeEventSource = 新货入库的通知方式——有些库房有实时通知系统（binlog/WAL），有些只能定时去问"有新货吗"（定时轮询）。

**小胖**："那 ClickHouse 怎么办？它是列存 OLAP 数据库，根本没有 binlog、WAL 这种东西。我们业务里的 ClickHouse 每天有 5000 万行新数据写入，也需要实时同步到下游。"

**大师**："这是一个'伪 CDC'场景。ClickHouse 没有事务日志，所以你唯一能做的是**定时增量轮询**——每分钟执行一次 `SELECT * FROM table WHERE updated_at > '$LAST_MAX_TIMESTAMP'`。但这个方案有四个硬伤：

1. **延迟下限 = 轮询间隔**：你最快只能每分钟同步一次，做不到秒级
2. **无法捕获物理 DELETE**：`DELETE FROM table WHERE id = 100` 执行后，这行数据从 ClickHouse 中彻底消失——下次轮询时你根本不知道它存在过。除非业务层用 `is_deleted` 标志位做软删除
3. **大表轮询压力**：5000 万行的表，每次 `WHERE updated_at > ...` 可能需要全表扫描（除非 updated_at 上有索引且 ClickHouse 能利用）
4. **无法保证顺序性**：两个事务 A 和 B 的时间戳可能相同或交错——轮询时可能先读到 B 的行再读到 A 的行，如果下游依赖顺序就会出错

所以对于 ClickHouse 这类数据库，如果下游对实时性要求不高（分钟级可接受），且业务用软删除——定时轮询方案可行。如果要求秒级实时同步，需要在应用层做'双写'——业务代码在写 ClickHouse 的同时也写一条 Kafka 消息。"

---

## 3. 项目实战

### 步骤1：新增数据库 Connector 标准化 Checklist（完整八步）

```
□ Step 1: 事务日志分析
   确认数据库有原生的事务日志机制（binlog/WAL/Oplog/Redo Log/CDC Table）
   → 如果无事务日志 → 评估定时轮询的延迟下限是否可接受

□ Step 2: Java 驱动可用性确认
   JDBC 驱动（MySQL Connector/J, PostgreSQL JDBC）或原生协议客户端（MongoDB Driver）
   → TiDB: mysql-connector-j (兼容), CRDB: pgjdbc (SQL 兼容但复制协议不兼容)

□ Step 3: 实现 Connection 接口
   继承 AbstractJdbcConnection，实现 execute()、getTableIds() 等方法
   → 需要处理：连接池管理、重连机制、查询超时、事务隔离级别

□ Step 4: 实现 Snapshotter 接口
   全量快照: SELECT * FROM table (分页 LIMIT OFFSET 或主键范围 WHERE id BETWEEN)
   增量快照: 按主键范围分 chunk (1-10000, 10001-20000, ...)
   → watermarket 策略选择: insert_insert (MySQL) / insert_delete (PG)

□ Step 5: 实现 ChangeEventSource 接口
   MySQL 风格: BinaryLogClient → EventDeserializer → RecordMaker
   PG 风格: Replication Slot + pgoutput 插件
   MongoDB 风格: Change Streams / Oplog tailing
   → 自研数据库: 直接消费事务日志 API → 自定义 EventDeserializer

□ Step 6: 实现 TypeConverter 类型映射表
   数据库列类型 → Kafka Connect Schema Type
   INT → Schema.INT32_SCHEMA
   VARCHAR(255) → Schema.STRING_SCHEMA
   DECIMAL(18,4) → Decimal.schema(4)  (注意 precision 和 scale 的保留)

□ Step 7: SPI 注册 (ServiceLoader 机制)
   META-INF/services/org.apache.kafka.connect.source.SourceConnector
   → com.example.debezium.MyNewDbConnector
   → 确保打包后的 JAR 中包含这个文件

□ Step 8: 测试矩阵（至少 6 个场景）
   ✅ INSERT 基本操作（单行 + 批量）
   ✅ UPDATE 操作（普通 + 主键变更 + 部分列更新）
   ✅ DELETE 操作（正常 + 物理删除 vs 软删除）
   ✅ DDL 变更（ADD COLUMN + DROP COLUMN + MODIFY COLUMN + RENAME COLUMN）
   ✅ 大表全量快照（> 100 万行，验证性能和数据完整性）
   ✅ 故障恢复（Connector 重启后 offset 恢复 + 不丢数据）
```

### 步骤2：TiDB 适配完整实战

**Step 2.1：验证 TiCDC 兼容性**

```bash
# 1. 确认 TiCDC 组件已部署并运行
mysql -h tidb-cluster -P 4000 -u root -e "SHOW VARIABLES LIKE 'ticdc%';"
# 预期：ticdc_version = 5.0.0+ 或更高

# 2. 用 mysqlbinlog 测试 binlog 协议连通性
mysqlbinlog -h tidb-cluster -P 4050 \
  --read-from-remote-server \
  --user=debezium --password='***' \
  --raw --stop-never \
  --result-file=/tmp/tidb-binlog/

# 3. 确认 GTID 模式已开启
mysql -h tidb-cluster -P 4000 -u root -e "SHOW VARIABLES LIKE 'gtid_mode';"
# 预期：gtid_mode = ON
```

**Step 2.2：创建 TiDB 专用 Connector 配置**

```bash
# 创建 Connector（注意 TiDB 特有的三个关键参数）
curl -X POST http://connect.internal:8083/connectors \
  -H "Content-Type: application/json" \
  -d '{
    "name": "tidb-orders-adapter",
    "config": {
      "connector.class": "io.debezium.connector.mysql.MySqlConnector",
      "database.hostname": "tidb-cluster.internal",
      "database.port": "4000",
      "database.user": "debezium_sync",
      "database.password": "${TIDB_PASSWORD}",
      "database.server.id": "184340",
      "topic.prefix": "tidb_prod",
      
      "database.include.list": "orders_db",
      "table.include.list": "orders_db.orders,orders_db.order_items",
      
      "database.initial.statements": "SET @@tidb_snapshot = NOW()",
      
      "snapshot.mode": "initial",
      "snapshot.locking.mode": "none",
      "snapshot.fetch.size": "10000",
      
      "schema.history.internal.kafka.bootstrap.servers": "kafka.internal:9092",
      "schema.history.internal.kafka.topic": "schema-changes.tidb",
      
      "provide.transaction.metadata": "true",
      "max.batch.size": "4096",
      "max.queue.size": "16384",
      "poll.interval.ms": "200",
      
      "transforms": "unwrap",
      "transforms.unwrap.type": "io.debezium.transforms.ExtractNewRecordState"
    }
  }'
```

**Step 2.3：TiDB GC 水位监控脚本**

```bash
#!/bin/bash
# tidb_gc_monitor.sh —— 每小时检查 GC safe point 与 Connector offset 的差距

# 获取 TiDB GC safe point（时间戳格式）
GC_SAFE_POINT=$(mysql -h tidb-cluster -P 4000 -u monitor -e \
  "SELECT VARIABLE_VALUE FROM mysql.tidb WHERE VARIABLE_NAME = 'tikv_gc_safe_point';" | tail -1)

# 获取 Connector 的当前 offset（binlog 时间戳）
OFFSET_TS=$(curl -s http://connect.internal:8083/connectors/tidb-orders-adapter/status | \
  python3 -c "import sys,json;d=json.load(sys.stdin);print(d['tasks'][0].get('trace',''))" | \
  grep -oP 'ts_sec=\K\d+')

# 计算差距（秒）
GAP=$(( $(date +%s) - OFFSET_TS ))
if [ $GAP -gt 300 ]; then  # 超过 5 分钟
  echo "⚠️  ALERT: TiDB Connector offset lag > 5min (${GAP}s) — GC safe point may overtake!"
  curl -X POST "$SLACK_WEBHOOK" -d "{\"text\":\"⚠️ TiDB CDC lag: ${GAP}s, GC risk!\"}"
fi
```

### 步骤3：ClickHouse"伪 CDC"轮询 Connector 实现

```java
// ClickHousePollingTask.java — 定时轮询 Connector 的核心实现
public class ClickHousePollingTask extends SourceTask {
    private long lastMaxTimestamp = 0;
    private String table;
    private int pollIntervalMs = 60000;  // 默认 60 秒轮询一次
    
    @Override
    public List<SourceRecord> poll() throws InterruptedException {
        List<SourceRecord> results = new ArrayList<>();
        
        // 查询上次轮询后新增/更新的行
        String sql = String.format(
            "SELECT * FROM %s WHERE updated_at > %d ORDER BY updated_at ASC LIMIT 10000",
            table, lastMaxTimestamp);
        
        try (Connection conn = dataSource.getConnection();
             Statement stmt = conn.createStatement();
             ResultSet rs = stmt.executeQuery(sql)) {
            
            long maxTs = lastMaxTimestamp;
            while (rs.next()) {
                long rowTs = rs.getTimestamp("updated_at").getTime();
                maxTs = Math.max(maxTs, rowTs);
                
                SourceRecord record = buildRecord(rs);
                results.add(record);
            }
            lastMaxTimestamp = maxTs;  // 更新游标
            
        } catch (SQLException e) {
            LOGGER.error("Polling failed for table {}", table, e);
        }
        
        // 等待轮询间隔
        Thread.sleep(pollIntervalMs);
        return results;
    }
}
```

### 可能遇到的坑及解决方法

| 坑 | 现象 | 根因 | 解决方法 |
|----|------|------|---------|
| TiDB `FLUSH TABLES WITH READ LOCK` 报错 | Connector FAILED | TiDB 不支持全局读锁 | `snapshot.locking.mode=none` |
| TiDB DDL 后反序列化失败 | `Unknown column X` 错误 | Schema 未完全同步到所有 TiKV | 设为 `tidb_enable_change_multi_schema=OFF` 逐个变更 |
| CockroachDB 直接用 PG Connector | 连接成功但无数据 | 底层复制协议完全不同 | 基于 Changefeed API 新开发 ChangeEventSource |
| ClickHouse 定时轮询丢 DELETE | 下游行数多于 ClickHouse | 轮询时已经看不到被删除的行 | 用 `is_deleted` 标志位做软删除 |

---

## 4. 项目总结

### 优点 & 缺点

| 维度 | Debezium 适配器体系 | 自研 CDC 方案 | 第三方商业 CDC 工具 |
|------|-------------------|-------------|-------------------|
| 多数据库支持 | ★★★★★ 统一接口 + 插件式 | ★★☆☆☆ 每种数据库独立开发 | ★★★★☆ 通常覆盖主流数据库 |
| 新数据库接入成本 | ★★★★☆ 按 Checklist 评估后复用 | ★★☆☆☆ 从零开发 1-6 月 | ★★★★☆ 依赖厂商支持 |
| 性能可控性 | ★★★★★ 源码在手，深度调优 | ★★★★☆ 完全可控 | ★★☆☆☆ 受限于厂商 |
| 社区生态 | ★★★★★ Debezium 社区活跃 | ★☆☆☆☆ 只有内部维护 | ★★★☆☆ 取决于商业支持 |

### 适用场景

1. **异构数据库实时同步**：MySQL → Kafka → PostgreSQL/ClickHouse/Elasticsearch
2. **数据库迁移**：从自研数据库迁移到 MySQL/PG 期间保持双写一致性
3. **多云/混合云**：本地 MySQL → Kafka → 云上 Snowflake/BigQuery
4. **合规数据归档**：生产 MySQL → Kafka → Iceberg/Hudi（保留 5-10 年）
5. **微服务数据解耦**：多个微服务的独立数据库 → 统一 Kafka 总线

### 不适用场景

1. **无事务日志的数据库 + 需要秒级延迟**：如 ClickHouse 实时同步——轮询方案的延迟不可接受
2. **极高安全隔离要求**：CDC 需要数据库的 REPLICATION 权限，某些安全策略不允许

### 注意事项

- **TiDB `tidb_gc_life_time` 默认只有 10 分钟**：生产环境必须调到 2 小时以上，且监控 GC safe point 与 Connector offset 的差距
- **ClickHouse 定时轮询的 `updated_at` 列必须有索引**：否则每次轮询都是全表扫描
- **CockroachDB Changefeed 的时间戳是 HLC（Hybrid Logical Clock）**：需要转换为 Unix 时间戳才能与 Debezium 的 ts_ms 字段对齐

### 常见踩坑经验

1. **"TiDB Connector 一切正常，但下游发现数据少了 3 秒的窗口"**——根因是 `tidb_snapshot = NOW()` 设置的时间点和 binlog 起始位点之间有 3-5 秒的间隙。解决：快照后不直接进入 streaming，而是通过增量快照的水印机制回补窗口期数据。
2. **"ClickHouse 轮询 Connector 在高峰期 OOM"**——根因是轮询返回了 100 万行数据一次性加载到内存。解决：`LIMIT 10000` + 循环分页 + 定期提交 offset。
3. **"CockroachDB Changefeed 在节点重启后 resume token 失效"**——CRDB 的 Changefeed cursor 在节点重启后不可用。解决：将 resume token 持久化到 Kafka offset 中，并在 restart 时检测 token 有效性，无效则从 `NOW()` 开始。

### 思考题

1. 某时序数据库（ClickHouse）没有事务日志，但业务要求"最多 3 秒的同步延迟"。设计一个方案：在 ClickHouse 的 `INSERT` 触发器中通过 MySQL 协议写入 binlog proxy，实现近似 CDC 的效果。这个方案的可行性和瓶颈在哪里？

2. 如果用 MySQL Connector 适配一个"99% 兼容" MySQL binlog 协议的自研分布式数据库，但该数据库不支持 `SHOW MASTER STATUS` 和 `FLUSH TABLES WITH READ LOCK`。如何在**不修改 Connector 源码**的前提下，通过 JDBC 拦截器或 MySQL Proxy 中间件来模拟这两个命令？

**（第33章思考题答案）**

1. fail-fast 适合开发和测试（错误尽早暴露，避免带着隐藏 bug 上线），fail-safe 适合生产（单列类型不识别不应阻塞整个 Connector，应该 skip 该列并告警）。建议：通过配置参数 `event.deserialization.failure.handling.mode` 控制——`fail`（开发）/`warn`（生产）。

2. Connector 通过对比新 Master 的 `Executed_Gtid_Set` 是否包含旧 offset 记录的 GTID UUID + Transaction ID 来判断连续性。如果不覆盖 → offset 失效 → Connector FAILED → 需重建全量快照。更优的方案是用 GTID + server_uuid + timestamp 复合去重，即使 GTID 的 UUID 变了，也能通过时间戳匹配找到对应的 binlog 位点。

---

> **推广提示**：将 Checklist 印为 A4 评估卡片，放入团队的"数据库技术选型包"。新数据库接入时逐项勾选——快速评估工期（1 天到 3 个月不等）、风险和技术可行性。评估结论写入 ADR（Architecture Decision Record），存入架构文档库。TiDB 适配的配置模板和 GC 监控脚本纳入运维工具集。
