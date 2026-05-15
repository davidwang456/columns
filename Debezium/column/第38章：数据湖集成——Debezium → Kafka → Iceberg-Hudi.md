# 第38章：数据湖集成——Debezium → Kafka → Iceberg/Hudi

## 1. 项目背景

"我们每天有 10 亿行订单变更数据通过 Debezium 实时写入 Kafka——但 Kafka 受限于磁盘成本和运维策略，只保留最近 7 天的数据。而合规部门根据《证券交易管理办法》明确要求：所有交易数据必须至少保留 5 年，且需要支持精确到秒级的时间旅行查询（Time Travel）。去年一次监管审计中，审计员要求我们当场查询'2023 年 6 月 15 日 14:32:00 时刻，用户 ID 88421 的账户余额和持仓详情'——我们翻了 3 个小时的冷备份磁带才找到，差点吃了合规罚单。"

"还有更头疼的——我们的数据科学家团队需要基于历史 CDC 数据做特征工程。他们需要知道'过去 30 天每个用户的购买频次变化趋势'——这需要能够回放每天的 CDC 快照，而不只是最新的当前状态。"

这两个需求——"5 年时间旅行"和"历史快照回放"——都不是 Kafka 能解决的。Kafka 是实时消息总线，不是数据湖。这就需要 **Apache Iceberg** 或 **Apache Hudi**：它们是专为大数据场景设计的开放式表格式（Table Format），在云对象存储（S3/OSS/HDFS）之上提供 ACID 事务、Time Travel、Schema Evolution、以及对 CDC Upsert 的原生支持。

### 痛点放大

| 需求 | Kafka 能解决吗？ | Iceberg/Hudi 如何解决？ |
|------|----------------|----------------------|
| 数据保留 5 年 | ❌ 磁盘成本无法承受 | ✅ 数据存储在低成本对象存储（S3） |
| 时间旅行（1 年前） | ❌ 只保留最近 N 天 | ✅ `FOR SYSTEM_TIME AS OF` 原生 SQL |
| 历史趋势分析 | ❌ 只有最新 offset | ✅ 每次快照都可查询 |
| CDC Upsert 去重 | ❌ 需消费者自行处理 | ✅ V2 格式原生支持 MERGE |
| Schema 变更后兼容 | ❌ 消费者报错 | ✅ Schema Evolution（ADD/DROP） |
| 增量查询 | ⚠️ 需记录 offset | ✅ 基于 Snapshot ID 或 Commit Time |

## 2. 项目设计——三人对话

**（周四下午，数据平台团队和大师开需求讨论会）**

**小胖**："大师，我听说过 Iceberg 和 Hudi，但不太明白——它们不就是'把 Parquet 文件存到 S3 上'吗？和直接往 S3 写 Parquet 有什么区别？"

**大师**："这是最常见的误解。假设你往 S3 上写了一个 `orders.parquet` 文件，过了一小时又有 100 条新订单需要追加——你是新建一个 `orders_v2.parquet` 还是覆盖原文件？如果是覆盖，在覆盖的瞬间有一个正在读文件的 Flink 作业会读到半写半覆盖的损坏文件。Iceberg 的核心价值就是——**在文件之上加了一层表格式元数据（Manifest + Snapshot），让你可以在不停机、不锁定的情况下安全地追加、更新、删除数据**，所有读者始终看到一致的快照视图。"

**小白**："那 CDC 的 Upsert 语义——同一个订单 ID 可能被 INSERT 一次、UPDATE 五次——Iceberg 怎么处理的？"

**大师**："Iceberg V2 格式引入了 **Row-Level Delete** 机制。每次 UPDATE 操作会产生两个文件：一个 delete file（标记哪些行被'删除'了）、一个 data file（新的行数据）。当你查询时，Iceberg 自动合并这两个文件——你只看到最新的数据。对于 Debezium CDC 的 Change Event——INSERT → data file，UPDATE → delete file + data file，DELETE → delete file——Flink 的 Iceberg Connector 会自动把这个语义翻译成对应的 Iceberg 操作。"

**小胖**："那 Flink CDC 和 Debezium 在这个链路中各自负责什么？为什么不直接用 Flink CDC 从 MySQL 读，省掉 Debezium 和 Kafka 这两层？"

**大师**："这是一个很常见的技术选型问题。三层角色分明——"

```
Debezium Connector (CDC 专业工具)     →  Kafka (消息缓冲 + 分发)     →  Flink/Iceberg (持久化)
┌────────────────────┐              ┌──────────────────┐          ┌────────────────────┐
│ ✅ 成熟 offset 管理  │              │ ✅ 消息持久化       │          │ ✅ ACID 事务         │
│ ✅ Schema History   │              │ ✅ 多下游分发       │          │ ✅ Time Travel       │
│ ✅ SMT 脱敏/加密    │              │ ✅ 7天缓冲窗口      │          │ ✅ Schema Evolution  │
│ ✅ 信号表控制       │              │ ✅ 反压控制         │          │ ✅ 低成本存储(S3)    │
└────────────────────┘              └──────────────────┘          └────────────────────┘
```

"Flink CDC 也可以直接从 MySQL 读——但如果 Flink 作业挂了，binlog 位点可能丢失；而且中间没有缓冲层，所有下游都得重新消费。有了 Kafka 作为中间缓冲——Flink 挂了重拉就行，Kafka 里有 7 天的数据。另外，Debezium 的 SMT 链在消息进入 Kafka 之前就完成了脱敏/加密——数据进入 Kafka 时已经是合规的。如果直接用 Flink CDC，你需要在 Flink 作业中处理脱敏逻辑。"

**技术映射**：Debezium = 生产车间的流水线（产出标准化零件）；Kafka = 成品仓库（缓冲 + 分发）；Iceberg = 永久档案库（低成本长期存储，随时可查历史版本）。

**小胖**："那 Iceberg 和 Hudi 到底选哪个？公司里有人说 Hudi 更好，有人说 Iceberg 是趋势。"

**大师**："给你一个全面的对比——"

| 决策维度 | Apache Iceberg | Apache Hudi | 推荐 |
|---------|---------------|-------------|------|
| CDC Upsert | ✅ V2 格式 | ✅ COW/MOR 原生 | 都支持，各有优势 |
| Time Travel | ✅ `FOR SYSTEM_TIME AS OF` SQL | ✅ 基于 commit time | Iceberg SQL 更优雅 |
| Schema Evolution | ✅ ADD/DROP/RENAME/MODIFY | ⚠️ 部分支持 | **Iceberg 更灵活** |
| 分区演进 | ✅ 支持动态改变分区策略 | ⚠️ 不支持 | **Iceberg 唯一支持** |
| CDC 集成 | 通过 Flink CDC Connector | 原生 DeltaStreamer | Hudi 对 Spark 更好 |
| 生态 | Flink/Spark/Trino/Presto | Spark/Flink/Hive | Iceberg 更广 |
| 社区活跃度 | ★★★★★ Netflix/Apple 主导 | ★★★★ Uber 开源 | 都活跃 |

"如果你是 Spark 栈且重度依赖 DeltaStreamer——选 Hudi。如果你是 Flink 栈且需要灵活的 Schema Evolution 和分区演进——选 Iceberg。我们本章以 Iceberg + Flink 为主线。"

---

## 3. 项目实战

### 步骤1：Flink SQL 作业——三行 SQL 完成 CDC 实时入湖

**目标**：一条 Flink SQL 作业，将 Debezium CDC 事件持续写入 Iceberg 表，实现 Upsert 语义。

```sql
-- ============ Flink SQL 作业 ============

-- Step 1: 定义 Debezium CDC Source (Flink 原生 debezium-json 格式)
CREATE TABLE orders_cdc (
    id INT,
    customer_id INT,
    product_name STRING,
    quantity INT,
    price DECIMAL(10,2),
    status STRING,
    created_at TIMESTAMP(3),
    PRIMARY KEY (id) NOT ENFORCED
) WITH (
    'connector' = 'kafka',
    'topic' = 'ecom_orders.inventory.orders',
    'properties.bootstrap.servers' = 'kafka.internal:9092',
    'properties.group.id' = 'flink-iceberg-orders',
    'format' = 'debezium-json',                          -- Flink 原生 Debezium 格式解析
    'debezium-json.schema-include' = 'false',             -- 不需要重复的 schema 字段
    'scan.startup.mode' = 'earliest-offset'               -- 从最早 offset 开始消费
);

-- Step 2: 定义 Iceberg Sink 表（V2 格式支持 Row-Level Delete + Upsert）
CREATE TABLE orders_iceberg (
    id INT,
    customer_id INT,
    product_name STRING,
    quantity INT,
    price DECIMAL(10,2),
    status STRING,
    created_at TIMESTAMP(3),
    PRIMARY KEY (id) NOT ENFORCED
) WITH (
    'connector' = 'iceberg',
    'catalog-name' = 'hadoop_prod',
    'catalog-type' = 'hadoop',
    'warehouse' = 's3://data-lake-prod/iceberg/warehouse/',
    'format-version' = '2',                              -- V2: 支持 Upsert
    'write.upsert.enabled' = 'true',                     -- 开启 Upsert 模式
    'write.target-file-size-bytes' = '536870912',        -- 512MB 文件大小目标
    'write.distribution-mode' = 'hash',                  -- 按主键 hash 分发
    'write.metadata.delete-after-commit.enabled' = 'true', -- 自动清理过期的 delete files
    'write.metadata.previous-versions-max' = '10'         -- 保留最近 10 个版本的元数据
);

-- Step 3: 一行 SQL 完成实时入湖
INSERT INTO orders_iceberg SELECT * FROM orders_cdc;
```

### 步骤2：验证 Iceberg 三大核心能力

**目标**：依次验证 Upsert 自动合并、Time Travel 历史查询、Schema Evolution 在线变更。

```sql
-- ============ 验证 1: Upsert 自动合并 ============
-- MySQL 中先 INSERT 一条订单（id=5001）
-- 然后 UPDATE 这条订单的 status: 'pending' → 'shipped' → 'completed'
-- 最后查询 Iceberg——应该只看到 1 行，status='completed'

SELECT * FROM orders_iceberg WHERE id = 5001;
-- 预期：1 行，status = 'completed'（最新的状态）
-- Iceberg 内部：3 个 data files + 2 个 delete files，查询时自动合并

-- ============ 验证 2: Time Travel 时间旅行 ============
-- 查询 1 小时前（在 UPDATE 发生之前）的订单状态
SELECT * FROM orders_iceberg 
  FOR SYSTEM_TIME AS OF TIMESTAMP '2024-06-15 10:00:00' 
WHERE id = 5001;
-- 预期：1 行，status = 'pending'（那时候的原始状态）

-- 查询 7 天前的数据
SELECT * FROM orders_iceberg 
  FOR SYSTEM_TIME AS OF TIMESTAMP '2024-06-08 10:00:00' 
WHERE id = 5001;
-- 预期：0 行（那时候还没有这条订单）

-- 查看表的所有快照历史（用于选择合适的回滚时间点）
SELECT 
    snapshot_id, 
    committed_at, 
    operation,
    summary['added-data-files'] AS added_files,
    summary['deleted-data-files'] AS deleted_files,
    summary['total-records'] AS total_records
FROM orders_iceberg.snapshots 
ORDER BY committed_at DESC 
LIMIT 20;

-- ============ 验证 3: Schema Evolution ============
-- MySQL 中 ADD COLUMN discount DECIMAL(5,2) DEFAULT 0.00
-- Iceberg 会自动注册新列，旧数据中该列为 NULL
ALTER TABLE orders_iceberg ADD COLUMN discount DECIMAL(5,2);
-- 成功！不需要重写任何已有数据文件
SELECT id, status, discount FROM orders_iceberg WHERE id = 5001;
-- 旧数据: discount = NULL, 新数据: discount = 10.00
```

### 步骤3：Compaction 策略——防止"小文件爆炸"

**目标**：CDC 高频 UPDATE 会产生大量小 delete files，必须定期合并以维持查询性能。

```sql
-- Iceberg V2 的 CDC Upsert 会产生：(每个 UPDATE) 1 个 delete file + 1 个 data file
-- 如果 orders 表每天 1000 万次 UPDATE → 2000 万个文件 → 查询越来越慢
-- 解决：定期 Compaction

-- ★ 方式 1: 手动触发 Compaction (适合首次验证)
CALL catalog_prod.system.rewrite_data_files(
    table => 'orders_iceberg',
    strategy => 'binpack',                  -- 将小文件打包成大文件
    options => map(
        'target-file-size-bytes', '536870912',   -- 目标文件大小 512MB
        'min-file-size-bytes', '52428800',        -- 只合并 < 50MB 的文件
        'max-file-group-size-bytes', '1073741824', -- 每组最多 1GB
        'delete-file-threshold', '5'              -- 超过 5 个 delete files 才合并
    )
);

-- ★ 方式 2: K8s CronJob 自动化 (推荐生产)
-- 每 2 小时执行一次 Compaction
```

```yaml
# k8s/compaction-cronjob.yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: iceberg-compaction
  namespace: data-lake
spec:
  schedule: "0 */2 * * *"  # 每 2 小时
  jobTemplate:
    spec:
      template:
        spec:
          containers:
          - name: compaction
            image: apache/iceberg-flink:1.4.0
            command:
            - /bin/bash
            - -c
            - |
              /opt/flink/bin/sql-client.sh -f /scripts/compaction.sql
          restartPolicy: OnFailure
```

### 步骤4：Hudi CDC 入湖替代方案

```bash
# Hudi DeltaStreamer —— 原生支持 Debezium Source，一条命令入湖
spark-submit \
  --class org.apache.hudi.utilities.deltastreamer.HoodieDeltaStreamer \
  --master yarn \
  --deploy-mode cluster \
  --conf spark.serializer=org.apache.spark.serializer.KryoSerializer \
  --source-class org.apache.hudi.utilities.sources.debezium.MySqlDebeziumSource \
  --source-ordering-field updated_at \
  --target-base-path s3://data-lake/hudi/orders \
  --target-table orders_hudi \
  --table-type MERGE_ON_READ \
  --props s3://config-bucket/debezium-source.properties \
  --schemaprovider-class org.apache.hudi.utilities.schema.SchemaRegistryProvider \
  --continuous
```

### 步骤5：端到端延迟监控

```bash
# 监控从 MySQL binlog 写入到 Iceberg 可见的端到端延迟
# 方法：在 MySQL 中每 10 秒 INSERT 一条 heart 记录，然后查询 Iceberg 中该记录的出现时间

python3 << 'PYEOF'
import time, mysql.connector, requests

SRC = mysql.connector.connect(host="mysql-prod", user="monitor", password="***", database="monitor")

for i in range(60):
    src_ts = int(time.time() * 1000)
    SRC.cursor().execute(f"INSERT INTO cdc_heartbeat (source, ts) VALUES ('debezium', {src_ts})")
    SRC.commit()
    
    # 等 5 秒后查询 Iceberg 是否已可见
    time.sleep(5)
    # ... 通过 Trino/Presto 查询 Iceberg 表 ...
    
    print(f"[{i}] Heartbeat sent at {src_ts}, checking Iceberg...")
    time.sleep(5)
PYEOF
```

### 可能遇到的坑及解决方法

| 坑 | 现象 | 根因 | 解决方法 |
|----|------|------|---------|
| Iceberg 查询越来越慢 | 查询时间从 3s → 30s | 小文件爆炸（每个 UPDATE 产生 2 个文件） | CronJob 定期 Compaction（合并 < 50MB 文件） |
| Flink Checkpoint 失败后数据重复 | Iceberg 表中出现重复行 | Flink 从 Checkpoint 恢复后重放消息 | Iceberg V2 Upsert 是幂等的（同 PK 自动合并） |
| Schema 变更后 Flink 报错 | `FieldNotFoundException` | Flink 作业未重启，仍使用旧 Schema | 重启 Flink 作业加载新 Schema |
| `debezium-json` 格式解析报错 | Flink 无法读取消息 | JSON 消息结构与 Flink `debezium-json` 格式预期不一致 | 确认 Debezium 未用 SMT 拍平（用 ExtractNewRecordState 则结构变化） |

---

## 4. 项目总结

### 优点 & 缺点

| 维度 | Iceberg + Flink CDC | Hudi + DeltaStreamer | 直接写 S3 Parquet |
|------|-------------------|---------------------|-------------------|
| ACID 事务 | ★★★★★ | ★★★★★ | ☆☆☆☆☆ |
| Time Travel | ★★★★★ SQL 原生 | ★★★★☆ commit time | ☆☆☆☆☆ |
| CDC Upsert | ★★★★★ V2 Row-Level Delete | ★★★★★ COW/MOR | ☆☆☆☆☆ |
| Schema Evolution | ★★★★★ ADD/DROP/RENAME | ★★★☆☆ 部分 | ☆☆☆☆☆ |
| 运维复杂度 | ★★★☆☆ 需 Compaction | ★★★☆☆ 需 Compaction | ★★★★★ 极简单 |
| 查询性能 | ★★★★☆ Compaction 后优秀 | ★★★★☆ MOR 模式 | ★★★☆☆ 全表扫描 |

### 适用场景

1. **合规审计**：金融/医疗行业要求数据保留 5-10 年且可随时查询
2. **数据科学特征工程**：基于历史快照回放生成训练数据集
3. **增量 ETL**：每天只处理新增/变更数据，而不是全量重跑
4. **多引擎查询**：Flink 流式写入 + Spark 批处理 + Trino 交互查询共用一个 Iceberg 表
5. **数据回滚**：CDC 同步出错后可回滚到上一个正常快照

### 不适用场景

1. **纯实时查询（无历史需求）**：直接用 ClickHouse/Doris 更合适
2. **一次性的数据导出**：不需要 ACID 和 Time Travel 的开销

### 注意事项

- **Iceberg V2 需要 Flink 1.15+** 和 Iceberg 1.3+ 才稳定支持，部署前确认版本兼容
- **Compaction 会消耗额外的计算资源**：在生产低峰期（凌晨 2-4 点）执行
- **S3 的最终一致性**：Iceberg 依赖 S3 的 read-after-write 一致性，确保 S3 bucket 开启了版本控制

### 常见踩坑经验

1. **"Iceberg 表文件数量爆炸，Spark 查询超时"**——根因是忘记配置 Compaction。一上午的 CDC UPDATE 产生了 50 万个 delete files。修复：执行 `rewrite_data_files` + 设置 K8s CronJob 自动化。
2. **"Flink Checkpoint 一直失败导致作业无限重启"**——根因是 Checkpoint 超时时间太短（3 分钟），而 S3 写入大文件需要更久。解决：`execution.checkpointing.timeout = 600000`（10 分钟）。
3. **"Iceberg Time Travel 查不到 30 天前的数据"**——根因是 Snapshot 过期策略保留了最近 100 个快照，但 100 个快照只覆盖了 20 天。解决：`history.expire.max-snapshot-age-ms = 2592000000`（30 天）。

### 思考题

1. Iceberg V2 格式下 CDC 高频 UPDATE 产生大量 delete files → 查询性能下降。需要设计一个自适应 Compaction 策略：当文件数 > 1000 且平均文件大小 < 100MB 时自动触发合并。如何用 K8s CronJob + Prometheus 指标实现这个策略？

2. 如果需要将同一份 CDC 数据同时写入 Iceberg（用于数据湖长期存储）和 ClickHouse（用于实时大屏查询），Flink SQL 如何实现一次消费、双流输出？请写出具体的 SQL 和关键配置。

**（第37章思考题答案）**

1. offset.flush() 阻塞 poll 线程的根因是同步 flush。改进方案：① 异步 flush——将 offset 写入内存队列，后台线程定期批量 flush 到 Kafka（类似 WAL 机制）；② 减少 flush 频率——`offset.flush.interval.ms` 从默认 60s 调到 300s（5 分钟），同时增加 `offset.flush.timeout.ms` 防止超时。③ 监控 flush 耗时——通过 JMX 指标 `debezium_OffsetFlushTime` 跟踪，超过 1s 则告警。

2. zstd 压缩后虽然 CPU 增加了 10%，但数据量减少了 70%——这意味着：① Kafka Broker 的磁盘写入时间从 500ms/批 降到 150ms/批（减少了 70% 磁盘 I/O）；② 网络传输时间从 200ms/批 降到 60ms/批（减少了 70% 带宽）；③ 整体的端到端延迟 = CPU 处理时间 + 网络传输时间 + Broker 落盘时间 + Consumer 拉取时间。虽然 CPU 多了 50ms/批，但网络 + 磁盘省了 490ms/批——净省 440ms/批。

---

> **推广提示**：将 Iceberg 入湖的 Flink SQL 脚本模板化存入 Git 仓库的 `data-lake/` 目录。Compaction CronJob YAML 纳入数据湖运维 SOP。Flink Checkpoint 间隔建议 5 分钟——平衡作业恢复速度（RTO）和入湖延迟（RPO）。设置 Monitoring：Iceberg 表的文件数 > 2000 或平均文件大小 < 50MB 时自动告警。
