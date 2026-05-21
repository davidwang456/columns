# 第24章：Kafka 集成与实时数据管道

> **版本**：ClickHouse 25.x LTS
> **定位**：中级篇核心章节。深入理解 Kafka 表引擎的消费模型、偏移量管理、Schema 演进与端到端延迟优化。
> **前置阅读**：第3章（数据类型与表引擎入门）、第4章（MergeTree 家族）、第14章（SQL 优化入门）
> **预计阅读**：45 分钟 | **实战耗时**：60 分钟

---

## 1. 项目背景

某 FinTech 公司的风控中心，周五晚上 11:47。风控总监老陈盯着大屏右上角一块红色的面板——"实时欺诈拦截率：78.3%"，旁边是灰色的延迟标签："数据延迟: 23h 47min"。近 24 小时的延迟，意味着此刻大屏上的风险指标计算的是一天前的交易数据。

公司的交易处理链路是：交易所核心 → Kafka（峰值 50 万 msg/s）→ Flink 流计算 → MySQL 结果表 → 每日凌晨 T+1 批量导出 → ClickHouse 分析库。链路上每一跳似乎都稳定可靠，但加起来就是**端到端延迟超过 24 小时**。一笔凌晨 3 点发生的可疑转账，风控引擎要到次日凌晨才"发现"——此时资金早已通过跨链桥转出，追回的黄金时间窗口早已关闭。

"能不能让 ClickHouse 直接接 Kafka？"老陈在一次技术评审上问。架构师小林的回答很诚实："理论上可以，但我们没试过——不确定 Kubernetes 里自动伸缩对消费者组的影响、不确定 Protobuf 格式怎么解析、不确定 Offset 管理在故障恢复时会不会丢数据。"

痛点梳理：
1. **延迟**：Flink → MySQL → 批量导出链路，每个环节都引入缓冲和延迟，端到端近 24 小时，欺诈交易早已逃逸。
2. **确定性**：Kafka 消费模型的 at-least-once 语义在写入 ClickHouse 合并树时能否保证去重？Offset 提交时机和故障恢复时怎么处理重复消费？
3. **Schema 演进**：交易日志使用 Protobuf 序列化，上游会不定期新增字段（如合规要求的 `compliance_code`），ClickHouse 是否能像 Avro + Schema Registry 那样兼容模式演化？
4. **弹性伸缩**：大促期间消息量从 50 万暴涨到 200 万 msg/s，ClickHouse 消费者能否水平扩展？分区数和消费者数如何匹配？

本章将逐个击破这些痛点，构建一条 **Kafka → ClickHouse → 实时物化视图** 的端到端延迟 < 5 秒的风控管道。

---

## 2. 项目设计：剧本式交锋对话

**Scene**：周一早上的架构评审会议室，白板上画满了数据流图。小胖靠在椅背上，嘴里嚼着刚从小卖部买的蛋黄酥。

**小胖**："Kafka 不就是个消息队列吗？ClickHouse 自己写个 Consumer 拉数据不就行了？这有啥好讨论的？Flink 那一层完全多余嘛——我一行 Java 都不写，直接 Kafka → ClickHouse，比你们那个 T+1 不知道高到哪里去了。"

**小白**（放下手中的枸杞茶）："小胖你先别嘴快。我问你几个实际问题：第一，Kafka Consumer Group 的 Offset 谁来管？如果 ClickHouse 进程挂了重启，它从哪个 Offset 继续消费？第二，Kafka 是 at-least-once 语义，写入 ClickHouse 的 MergeTree 表时，如果同一条消息被消费了两次，怎么去重？第三，Kafka 有 4 个 Partition，你开几个消费者线程？消费者 Rebalance 的时候怎么办？"

**小胖**（眨了眨眼）："呃……这些 ClickHouse 文档上应该有吧？"

**大师**（笑了笑合上笔帽）："小白问的每一条都在刀尖上。我们今天就把这些刀刃一一趟平。"

大师起身走到白板前，画了一张简图：

```
Kafka Topic (4 Partitions)
    │
    ├─ Consumer Thread 1 ──┐
    ├─ Consumer Thread 2 ──┤
    ├─ Consumer Thread 3 ──┼── Kafka Engine Table (缓冲区)
    ├─ Consumer Thread 4 ──┘       │
                                    ▼
                          Materialized View
                                    │
                                    ▼
                         MergeTree / ReplicatedMergeTree
```

"ClickHouse 的 Kafka 消费实现不是自研的网络 IO 库，而是内置了 `librdkafka`——这是 C 语言写的 Kafka 客户端，全世界最广泛使用、经过 LinkedIn 和 Confluent 生产验证的。ClickHouse 只负责把它包装成一个**表引擎**。关键是这张表有两种用法，决定了你的数据去留。"

**小胖**："嗯？表不是存数据的吗？怎么还有两种用法？"

**大师**："这就是最容易踩的坑。**Kafka 表引擎本身不持久化数据**——它只是一个消费缓冲区。Kafka Engine 表每收到一批消息，如果你不把它倒腾到别的地方，这批消息就被直接丢弃。实际使用中，你的选择是：

- **独立查询模式**：直接 `SELECT * FROM kafka_table`，每次查询返回 Kafka 当前缓冲区里的数据。适合调试、临时探查消息内容。
- **物化视图驱动模式**：`CREATE MATERIALIZED VIEW mv TO target_table AS SELECT ... FROM kafka_table`——这是标准的生产用法。物化视图被 Kafka 表上的新消息**触发**，把数据写入目标 MergeTree 表，实现持久化。

如果你建了 Kafka 表但忘了建物化视图，就像把自来水管接到了水槽上但不塞塞子——水哗哗流过，一滴都没留住。"

**技术映射 #1**：Kafka Engine Table = 一种"流式数据源"的抽象。它把 Kafka Topic 映射为一张 SQL 表，让开发者用 `SELECT` / `INSERT` / 物化视图这同一套 SQL 接口处理流数据。持久化依赖物化视图转发到 MergeTree——Kafka 表本身不落地。

---

**小白**（在本子上快速画着箭头）："懂了，Kafka 表是管道，物化视图是漏斗，MergeTree 是水缸。那我再追问一句——Offset 提交时机是什么样的？如果是先写入 MergeTree 再提交 Offset，那写入成功后、提交前进程崩溃了，重启后会重复消费。反过来如果先提交 Offset 再写入，那提交后、写入完成前崩溃，消息就丢了。ClickHouse 怎么处理的？"

**大师**："精准的观察。ClickHouse 的 Offsets 处理策略是这样的——它的流式消费默认使用 **auto-commit** 模式，但不是传统 Kafka Consumer 那种'定时提交'，而是和 MergeTree 写入绑定在一起：

1. Kafka Engine 通过 `kafka_poll_timeout_ms`（默认 100ms）定期从 Kafka 拉取消息。
2. 拉到的消息存放在一个内存缓冲区 `kafka_max_block_size`（默认 65536 行）。
3. 物化视图被触发，将这批消息写入目标 MergeTree 表。
4. **写入成功后**，ClickHouse 才提交这批消息的 Offset 到 Kafka。

所以，确切的语义是 **at-least-once**：如果写入 MergeTree 成功了但 Offset 提交时进程崩溃，重启后会从上次已提交的 Offset 重新消费——导致少量重复。但**不会丢数据**——因为只有写入成功才提交，写入成功的数据一定在 MergeTree 里。"

**小胖**："那重复消费的数据不就多算了吗？风控指标还能准？"

**大师**："好问题。MergeTree 的去重依赖两个机制：
- **ReplacingMergeTree**：按 ORDER BY 键去重，保留最新版本。如果你的消息有唯一 ID（如 `tx_id`），把它放在 ORDER BY 里，`ReplacingMergeTree` 会在后台 Merge 时删除旧版本。但注意：去重是异步的，不保证实时精确去重。
- **应用层幂等**：在物化视图的 SQL 里用 `_offset` 和 `_partition` 两个虚拟列，加上写入时间的 `_timestamp`，构建唯一标识，查询时用 `argMax()` 函数取最新版本。

实际上，对于大部分实时风控场景，at-least-once + 分钟级去重是合理的取舍——牺牲极少数重复的计算，换取零数据丢失和高吞吐。"

**技术映射 #2**：ClickHouse Kafka 引擎提供 at-least-once 语义。Offset 在数据写入 MergeTree 后提交——保住了数据不丢，但引入了少量重复。重复问题通过 ReplacingMergeTree 的异步去重或应用层幂等逻辑解决。

---

**小白**："那消费性能怎么保证？50 万 msg/s，一条消息算 500 字节，大概是 250 MB/s 的吞吐。一个 Consumer 线程吃得下吗？"

**大师**："吃不下的。这就涉及几个关键的 Settings：

- **`kafka_num_consumers`**：消费者线程数。经验值是设为 Kafka Topic 的 Partition 数——因为 Kafka 的设计是同一个 Partition 只能被同一个 Consumer Group 内的一个线程消费。你设 4 个 Consumer、Topic 有 4 个 Partition，刚好一对一；设 8 个 Consumer 但只有 4 个 Partition——多出来的 4 个线程就闲置吃空饷。
- **`kafka_thread_per_consumer`**：设为 1 时每个 Consumer 独占一个 OS 线程，适合高吞吐场景（你的 250 MB/s 就需要这个）；设为 0 时复用 ClickHouse 的后台线程池，省资源但吞吐打折扣。
- **`kafka_max_block_size`**：控制每次拉取的数据块行数。设太小，频繁 Poll 拉高 CPU 消耗；设太大，占用内存过高。64K 行是默认值，吞吐不够时可以调到 256K。
- **`kafka_poll_timeout_ms`**：Poll 间隔，默认 100ms。值越小延迟越低但 CPU 开销越大。

一个典型的高吞吐配置：4 Partition 的 Topic，4 个 Consumer，每线程专用 OS 线程，256K 行一批，端到端延迟稳定在 1-3 秒内。"

**小胖**："那 Flink 是不是可以彻底下岗了？ClickHouse 把 Kafka 的数据直接收了不就行了？"

**大师**："收回来——Flink 和 ClickHouse 各司其职。Flink 做的是**有状态的流计算**——比如 CEP（复杂事件处理）检测'同一用户 5 分钟内转账超过 3 笔且每笔 > 5 万'这种跨事件关联模式。ClickHouse 做的是**实时摄入 + OLAP 查询**——把数据快速落表、建立索引，然后秒级返回聚合分析结果。这是两个完全不同的环节：

- **Flink 的边界**：流式计算引擎，擅长窗口聚合、事件序列匹配、状态管理。接入 ClickHouse 数据时，ClickHouse 是它的下游存储。
- **ClickHouse 的边界**：OLAP 引擎，擅长海量数据的批量查询与聚合，能做简单的实时物化视图（如分钟级 SUM/COUNT），但不支持复杂的有状态流处理。

两者不是替代关系，是互补——Kafka 是火车站，Flink 是月台上的售货机（处理即时事件），ClickHouse 是货运仓库（存储和批量分析）。"

**技术映射 #3**：Kafka 引擎负责消费和感知新的数据；物化视图负责触发式落表；MergeTree 负责持久化和查询加速。Flink 与 ClickHouse 的边界在于"有状态流计算"与"实时摄入 + OLAP"的分工。

---

## 3. 项目实战

### 环境准备

使用 Docker Compose 一键拉起 Kafka + ZooKeeper + ClickHouse 的环境。所有服务共享 `app-tier` 网络，ClickHouse 可以通过 `kafka:9092` 访问 Kafka。

```yaml
# docker-compose.yml
services:
  zookeeper:
    image: confluentinc/cp-zookeeper:7.5.0
    environment:
      ZOOKEEPER_CLIENT_PORT: 2181
      ZOOKEEPER_TICK_TIME: 2000
    networks:
      - app-tier

  kafka:
    image: confluentinc/cp-kafka:7.5.0
    depends_on:
      - zookeeper
    ports:
      - "9092:9092"
    environment:
      KAFKA_BROKER_ID: 1
      KAFKA_ZOOKEEPER_CONNECT: zookeeper:2181
      KAFKA_ADVERTISED_LISTENERS: PLAINTEXT://kafka:9092
      KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR: 1
      KAFKA_TRANSACTION_STATE_LOG_MIN_ISR: 1
      KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR: 1
    networks:
      - app-tier

  clickhouse:
    image: clickhouse/clickhouse-server:25.3
    ports:
      - "8123:8123"
      - "9000:9000"
    environment:
      CLICKHOUSE_USER: default
      CLICKHOUSE_PASSWORD: ck123456
    networks:
      - app-tier
    volumes:
      - ./clickhouse-config.xml:/etc/clickhouse-server/config.d/custom.xml
    ulimits:
      nofile:
        soft: 262144
        hard: 262144

networks:
  app-tier:
    driver: bridge
```

```bash
# 启动环境
docker compose up -d

# 确认 ClickHouse 可用
curl -u default:ck123456 http://localhost:8123/?query=SELECT+1
# 返回: 1

# 确认 Kafka 可用
docker compose exec kafka kafka-topics --bootstrap-server kafka:9092 --list
```

---

### 分步实现

#### Step 1：创建 Kafka Topic 并生产测试数据

```bash
# 进入 Kafka 容器
docker compose exec kafka bash

# 创建主题：4 个分区，匹配后续消费者数量
kafka-topics --create \
  --topic transactions \
  --bootstrap-server kafka:9092 \
  --partitions 4 \
  --replication-factor 1

# 验证创建成功
kafka-topics --list --bootstrap-server kafka:9092

# 生产 10 万条模拟交易 JSON 消息
# 每条消息格式：{"tx_id":1,"user_id":42,"amount":89.50,"timestamp":"2026-04-30T10:30:00"}
for i in $(seq 1 100000); do
  json="{\"tx_id\":${i},\"user_id\":$(($RANDOM%1000)),\"amount\":$(($RANDOM%10000)/100.0),\"timestamp\":\"$(date -u +%Y-%m-%dT%H:%M:%S)\"}"
  echo "$json"
done | kafka-console-producer \
  --topic transactions \
  --bootstrap-server kafka:9092

# 一行命令速查分区数据量
kafka-run-class kafka.tools.GetOffsetShell \
  --broker-list kafka:9092 \
  --topic transactions
```

**常见坑点**：Windows 下 `$RANDOM` 和 `date` 命令不可用。替代方案是用 Python 脚本生成 JSON 文件后通过 `kafka-console-producer < data.json` 喂入。或者直接用 ClickHouse 内置的 `kafka_producer` 功能写回 Kafka（逆向测试时常用）。

---

#### Step 2：创建 Kafka 消费表和目标 MergeTree 表

```sql
-- ==========================================
-- 2.1 Kafka Engine 消费表
-- ==========================================
CREATE TABLE transactions_kafka (
    tx_id UInt64,
    user_id UInt32,
    amount Decimal(10,2),
    timestamp DateTime
) ENGINE = Kafka()
SETTINGS
    kafka_broker_list = 'kafka:9092',
    kafka_topic_list = 'transactions',
    kafka_group_name = 'clickhouse_consumer_group',
    kafka_format = 'JSONEachRow',
    kafka_num_consumers = 4,       -- 匹配 Partition 数
    kafka_thread_per_consumer = 1,  -- 每个消费者独占线程
    kafka_max_block_size = 65536,   -- 每批拉取行数
    kafka_poll_timeout_ms = 100;    -- 拉取间隔

-- ==========================================
-- 2.2 目标 MergeTree 表（持久化存储）
-- ==========================================
CREATE TABLE transactions (
    tx_id UInt64,
    user_id UInt32,
    amount Decimal(10,2),
    timestamp DateTime,
    -- 以下两列为 Kafka 元数据，用于去重和追踪
    _kafka_offset UInt64,
    _kafka_partition UInt16,
    _insert_time DateTime DEFAULT now()
) ENGINE = MergeTree()
ORDER BY (timestamp, tx_id)
PARTITION BY toYYYYMMDD(timestamp);

-- ==========================================
-- 2.3 物化视图：桥接 Kafka → MergeTree
-- ==========================================
CREATE MATERIALIZED VIEW transactions_mv TO transactions
AS SELECT
    tx_id,
    user_id,
    amount,
    timestamp,
    _offset AS _kafka_offset,       -- Kafka 虚拟列：偏移量
    _partition AS _kafka_partition,  -- Kafka 虚拟列：分区号
    now() AS _insert_time
FROM transactions_kafka;
```

**关键说明**：
- Kafka 引擎表不存数据，`SELECT * FROM transactions_kafka` 只返回当前缓冲区中的未处理消息——且消费后立即丢弃。
- `_offset` 和 `_partition` 是 Kafka 表引擎自动暴露的虚拟列，记录每条消息在 Kafka 中的物理位置，是实现去重的关键字段。
- 物化视图必须执行 `SELECT`——它被 Kafka 表上每批新消息触发，结果自动写入 `transactions` 表。

**踩坑警告**：如果你先建了物化视图再建 Kafka 表（或者反过来），不会报错——但物化视图不会自动开始消费 Kafka 数据。一定要确保 Kafka 表先建好、有数据流入，再建物化视图；或者建完物化视图后 `DETACH TABLE kafka_table; ATTACH TABLE kafka_table;` 触发重新消费。

---

#### Step 3：监控消费状态

```sql
-- 3.1 查看消费者组整体状态
SELECT
    database,
    table,
    total_rows,
    bytes_consumed,
    last_poll_time,
    last_exception
FROM system.kafka_consumers
WHERE table = 'transactions_kafka';

-- 预期输出（示例）：
-- database | table              | total_rows | bytes_consumed | last_poll_time     | last_exception
-- default  | transactions_kafka | 65536      | 12582912       | 2026-04-30 10:30:05| (null)

-- 3.2 目标表数据量（应持续增长）
SELECT
    count() AS total_rows,
    min(timestamp) AS earliest_msg,
    max(timestamp) AS latest_msg,
    dateDiff('second', max(timestamp), now()) AS latency_seconds
FROM transactions;

-- 3.3 按分区查看消费进度
SELECT
    _kafka_partition,
    count() AS rows_per_partition,
    min(_kafka_offset) AS min_offset,
    max(_kafka_offset) AS max_offset,
    max(_kafka_offset) - min(_kafka_offset) + 1 AS offset_range,
    count() / (max(_kafka_offset) - min(_kafka_offset) + 1.0) * 100 AS completeness_pct
FROM transactions
GROUP BY _kafka_partition
ORDER BY _kafka_partition;

-- 3.4 端到端延迟分析
SELECT
    toStartOfMinute(_insert_time) AS insert_minute,
    count() AS msg_count,
    avg(dateDiff('second', timestamp, _insert_time)) AS avg_e2e_latency_sec,
    quantile(0.95)(dateDiff('second', timestamp, _insert_time)) AS p95_latency_sec,
    quantile(0.99)(dateDiff('second', timestamp, _insert_time)) AS p99_latency_sec
FROM transactions
WHERE _insert_time >= now() - INTERVAL 10 MINUTE
GROUP BY insert_minute
ORDER BY insert_minute DESC;
```

---

#### Step 4：消费性能调优

当消息量暴涨（如大促期间从 50 万 msg/s 飙升到 200 万），需要调整消费参数：

```sql
-- 方案 A：通过 ALTER 修改 Settings（ClickHouse 25.x 支持部分参数热修改）
ALTER TABLE transactions_kafka MODIFY SETTING
    kafka_max_block_size = 262144;   -- 加大批次，减少 Poll 次数

-- 方案 B：重建 Kafka 表（Settings 不支持热修改时使用）
-- 注意：DETACH/ATTACH 会保留 Offset，消费不中断；DROP 则重置
DETACH TABLE transactions_kafka;

CREATE OR REPLACE TABLE transactions_kafka (
    tx_id UInt64,
    user_id UInt32,
    amount Decimal(10,2),
    timestamp DateTime
) ENGINE = Kafka()
SETTINGS
    kafka_broker_list = 'kafka:9092',
    kafka_topic_list = 'transactions',
    kafka_group_name = 'clickhouse_consumer_group',
    kafka_format = 'JSONEachRow',
    kafka_num_consumers = 4,          -- = Partition 数
    kafka_thread_per_consumer = 1,
    kafka_max_block_size = 262144,    -- 25 万行/批
    kafka_poll_timeout_ms = 50,       -- 更频繁的轮询
    kafka_flush_interval_ms = 5000,   -- 5 秒刷新一次
    kafka_skip_broken_messages = 100; -- 跳过最多 100 条格式错误消息
```

**调优经验清单**：

| 参数 | 默认值 | 高吞吐建议 | 说明 |
|------|--------|------------|------|
| `kafka_num_consumers` | 1 | = 分区数 | 超过分区数的线程闲置 |
| `kafka_thread_per_consumer` | 0 | 1（高吞吐时） | 独占线程 vs 共享线程池 |
| `kafka_max_block_size` | 65536 | 131072 ~ 524288 | 越大吞吐越高但内存占用越大 |
| `kafka_poll_timeout_ms` | 100 | 10 ~ 50 | 越小延迟越低，但 CPU 消耗更高 |
| `kafka_flush_interval_ms` | 7500 | 3000 ~ 5000 | 物化视图的刷新频率 |
| `kafka_skip_broken_messages` | 0 | 100 | 跳过格式异常消息，避免拖死整个消费 |

---

#### Step 5：实时聚合管道

消费数据只是第一步，核心价值在实时聚合。下面构建一个分钟级汇总的物化视图，直接支撑风控大屏。

```sql
-- ==========================================
-- 5.1 实时聚合物化视图（分钟粒度）
-- ==========================================
CREATE MATERIALIZED VIEW transactions_1min_agg
ENGINE = SummingMergeTree()
ORDER BY (minute, user_id)
PARTITION BY toYYYYMM(minute)
SETTINGS index_granularity = 8192
AS SELECT
    toStartOfMinute(timestamp) AS minute,
    user_id,
    count() AS tx_count,
    sum(amount) AS total_amount,
    max(amount) AS max_amount
FROM transactions
GROUP BY minute, user_id;

-- ==========================================
-- 5.2 风控大屏查询（实时）
-- ==========================================
-- 最近 10 分钟的每秒交易笔数（TPS）
SELECT
    minute,
    sum(tx_count) / 60.0 AS tps,
    sum(total_amount) AS revenue,
    avg(max_amount) AS avg_max_amount
FROM transactions_1min_agg
WHERE minute >= now() - INTERVAL 10 MINUTE
GROUP BY minute
ORDER BY minute DESC;

-- 最近 1 分钟的大额交易用户（风控模型输入）
SELECT
    user_id,
    sum(tx_count) AS cnt,
    sum(total_amount) AS total,
    max(max_amount) AS peak
FROM transactions_1min_agg
WHERE minute = toStartOfMinute(now())
  AND total_amount > 50000   -- 总金额 > 5 万
  OR max_amount > 10000      -- 单笔 > 1 万
GROUP BY user_id
ORDER BY total DESC
LIMIT 20;
```

**SummingMergeTree 说明**：`SummingMergeTree` 在后台 Merge 时自动对 ORDER BY 键相同的行做数值列求和——所以 `tx_count`、`total_amount` 会被累加，`max_amount` 会被覆盖为最大值。查询时需用 `SELECT ... GROUP BY minute, user_id` 再次聚合以确保正确性（因为去重是异步的）。

---

#### Step 6：Protobuf / Avro Schema 处理

生产环境中，消息体通常是 Protobuf 或 Avro 序列化的二进制格式。JSON 只是学习期的玩具。

```sql
-- ==========================================
-- 6.1 Protobuf 格式配置
-- ==========================================

-- 首先在 config.xml 或 custom.xml 中注册格式 schema
-- 路径：/etc/clickhouse-server/config.d/protobuf.xml
-- 内容：
-- <clickhouse>
--   <format_schema_path>/var/lib/clickhouse/protobuf_schemas/</format_schema_path>
-- </clickhouse>

-- 在 ClickHouse 节点上放置 .proto 文件
-- /var/lib/clickhouse/protobuf_schemas/Transaction.proto:
-- syntax = "proto3";
-- message Transaction {
--   uint64 tx_id = 1;
--   uint32 user_id = 2;
--   double amount = 3;
--   uint64 timestamp = 4;
-- }

-- Kafka 表使用 Protobuf 格式
CREATE TABLE transactions_kafka_proto (
    tx_id UInt64,
    user_id UInt32,
    amount Decimal(10,2),
    timestamp DateTime
) ENGINE = Kafka()
SETTINGS
    kafka_broker_list = 'kafka:9092',
    kafka_topic_list = 'transactions_proto',
    kafka_group_name = 'clickhouse_proto_group',
    kafka_format = 'ProtobufSingle',            -- 每条消息一个 Transaction
    kafka_schema = 'Transaction.proto:Transaction';

-- ==========================================
-- 6.2 Avro + Confluent Schema Registry 集成
-- ==========================================
CREATE TABLE transactions_kafka_avro (
    tx_id UInt64,
    user_id UInt32,
    amount Decimal(10,2),
    timestamp DateTime
) ENGINE = Kafka()
SETTINGS
    kafka_broker_list = 'kafka:9092',
    kafka_topic_list = 'transactions_avro',
    kafka_group_name = 'clickhouse_avro_group',
    kafka_format = 'AvroConfluent',             -- Confluent 格式（含 Schema ID 前缀的 5 字节头）
    kafka_avro_schema_registry_url = 'http://schema-registry:8081';
```

**Schema 演化注意事项**：
- Protobuf **向后兼容**：新增可选字段时，旧 Consumer 会自动忽略新字段。ClickHouse 26.x 起支持 `Protobuf` 格式的 `--skip_unknown_fields`。
- Avro + Schema Registry 支持 **Schema 版本管理**：上游加字段只增加 Schema Version，Consumer 从 Registry 拉取新 Schema。ClickHouse 的 AvroConfluent 格式打开消息时自动解析 5 字节的 Schema ID 头，由 Registry 返回对应 Schema。
- 如果新增字段需要写入 ClickHouse，使用 `ALTER TABLE ... ADD COLUMN ... AFTER ...` 扩展目标表后再重建物化视图。

---

#### Step 7：故障处理与重置

```sql
-- ==========================================
-- 7.1 消费者卡住 → 重启消费
-- ==========================================
-- DETACH + ATTACH 保留原来的 Offset 信息，不丢消费进度
DETACH TABLE transactions_kafka;
-- 检查 Kafka 端消费者组是否释放：
-- kafka-consumer-groups --bootstrap-server kafka:9092 --group clickhouse_consumer_group --describe
ATTACH TABLE transactions_kafka;

-- ==========================================
-- 7.2 重置 Offset → 重新消费所有数据
-- ==========================================
-- Step 1: 停止 ClickHouse 消费者
DETACH TABLE transactions_kafka;

-- Step 2: 在 Kafka 端重置 Offset 到最旧
-- docker compose exec kafka kafka-consumer-groups \
--   --bootstrap-server kafka:9092 \
--   --group clickhouse_consumer_group \
--   --topic transactions \
--   --reset-offsets --to-earliest --execute

-- Step 3: 清空 ClickHouse 目标表（如果需要重跑全量）
TRUNCATE TABLE transactions;

-- Step 4: 恢复消费者
ATTACH TABLE transactions_kafka;
-- 此时会从 Offset 0 开始重新消费

-- ==========================================
-- 7.3 彻底清理（销毁消费者组和全部数据）
-- ==========================================
DROP TABLE transactions_mv;        -- 先删物化视图
DROP TABLE transactions_kafka;     -- 再删 Kafka 表
DROP TABLE transactions;           -- 最后删目标表
DROP TABLE transactions_1min_agg;  -- 清理聚合表
```

---

### 测试验证

验证清单：

```sql
-- 验证 1：数据完整性
-- 生产完 10 万条消息后，确认目标表行数
SELECT count() FROM transactions;
-- 预期：≈ 100000（因 at-least-once 可能有少量重复，误差 < 1%）

-- 验证 2：分区均衡性
SELECT
    _kafka_partition,
    count() AS rows,
    count() / (SELECT count() FROM transactions) * 100 AS pct
FROM transactions
GROUP BY _kafka_partition
ORDER BY _kafka_partition;
-- 预期：4 个分区数据量接近 25% 均匀分布

-- 验证 3：故障恢复
-- 1. 记录当前最大 Offset
SELECT max(_kafka_offset) FROM transactions;
-- 2. docker compose stop clickhouse && sleep 30 && docker compose start clickhouse
-- 3. 再次查询 max offset，验证消费没中断

-- 验证 4：端到端延迟
-- 持续生产消息的同时查询延迟
SELECT
    dateDiff('second', timestamp, _insert_time) AS latency,
    count() AS cnt
FROM transactions
WHERE _insert_time >= now() - INTERVAL 5 MINUTE
GROUP BY latency
ORDER BY latency;
-- 预期：90% 的消息延迟 < 3 秒
```

**极限吞吐压测**：使用 Kafka 自带工具 `kafka-producer-perf-test` 模拟大促峰值。

```bash
# 压测：每秒 20 万条消息、每条 200 字节，持续 60 秒
kafka-producer-perf-test \
  --topic transactions \
  --num-records 12000000 \
  --record-size 200 \
  --throughput 200000 \
  --producer-props bootstrap.servers=kafka:9092
```

---

## 4. 项目总结

### Kafka 集成模式对比

| 模式 | 描述 | 数据持久化 | 适用场景 |
|------|------|-----------|---------|
| Kafka → KafkaTable → MV → MergeTree | 经典管道：消息消费 → 触发写入 → 持久化 | 是（MergeTree） | 绝大多数生产场景 |
| Kafka → KafkaTable（独立） | 直接 SELECT 查询 Kafka 缓冲区 | 否（缓冲区即查即挥） | 调试、临时探查 |
| Kafka → MV → SummingMergeTree | 消费即预聚合，查询看板直出 | 是（聚合表） | 实时看板、TPS 监控 |
| 多 Kafka 表 → 单 MergeTree | 多 Topic 合并写入一张表 | 是 | 日志归集、多来源统一 |

### 与 Flink 的边界

| 维度 | ClickHouse Kafka Engine | Flink |
|------|------------------------|-------|
| 核心能力 | 实时摄入 + OLAP 查询 | 有状态流计算 + 窗口聚合 |
| 数据处理 | 单条消息触发 MV，无跨消息状态 | CEP、Session Window、跨事件匹配 |
| SQL 复杂度 | MV 中的简单聚合、过滤、转换 | Flink SQL 支持复杂 JOIN、UDTF |
| 延迟 | 亚秒级摄入，查询有扫描延迟 | 毫秒级端到端 |
| 运维复杂度 | 表 + MV 管理，在 ClickHouse 内部闭环 | 独立集群，需 JobManager/TaskManager |
| 适用场景 | 数据落桶分析、实时看板、反查 | 规则引擎、实时特征计算、异常检测 |

### 适用场景

- **实时风控**：交易流 Kafka → ClickHouse，秒级更新风控规则命中率大屏。
- **IoT 传感器管道**：设备数据通过 Kafka 写入 ClickHouse，时间窗口分析设备健康。
- **应用日志归集**：微服务日志 → Kafka → ClickHouse → Grafana，替代 ELK 的日志存储层。
- **点击流分析**：用户行为事件实时入 ClickHouse，分钟级更新漏斗转化率。
- **不适用场景**：需要严格 exactly-once 语义的金融记账核心系统（应使用 Kafka → Flink → 事务型数据库）；需要毫秒级单条查询的在线服务（应使用 Redis/MySQL）。

### 注意事项

1. **Kafka 表不存数据**：它是一个消费窗口，数据只在物化视图刷新的那一瞬间可访问。查 `SELECT * FROM kafka_table` 返回的一定是空——因为数据已被 MV 消费并丢弃。
2. **消费者数量 ≤ 分区数**：`kafka_num_consumers` 超过分区数的线程无法分配到分区，处于永久空闲（Idle）状态。一个 Kafka Consumer Group 内，每个分区最多只能被一个线程消费。
3. **DETACH vs DROP 行为不同**：`DETACH` 保留消费者状态（Offset），重新 `ATTACH` 后从断点继续；`DROP` 则提交所有已消费 Offset 并从 Kafka Coordinator 移除——相当于销毁消费者组。
4. **格式修复不回溯**：如果 `kafka_format` 设置错了（写了 `JSONEachRow` 但实际消息是 CSV），消息解析失败会报错。修正格式后，已因解析失败而跳过的消息（如设置了 `kafka_skip_broken_messages`）不会被重新消费——因为它们已被标记为已处理。

### 常见踩坑经验

1. **忘了建物化视图**：建了 `kafka_table` 就开始查数据，SELECT 出来总是空——因为 Kafka 表不存储数据，消息被消费后直接丢弃。必须建 MV 把数据转发到 MergeTree。

2. **`kafka_num_consumers` 没对齐分区数**：Topic 有 8 个分区但只配了 1 个消费者——剩余 7 个分区的消息在 Kafka 里越积越多，ClickHouse 这边却"消费正常"（因为它在消费它分到的那个分区）。直观表现为：目标表数据量是生产量的 1/8。

3. **Protobuf 缺失 schema 文件**：创建 `kafka_format = 'Protobuf'` 的 Kafka 表时，`format_schema_path` 指向的目录下必须有对应的 `.proto` 文件。忘记拷贝 proto 文件到所有 ClickHouse 节点的对应路径是最常见的失败原因——建表不报错，但第一条消息到达时解析失败，报 `DB::Exception: Format schema file not found`。

4. **Rebalance 风暴拖慢消费**：Kafka Consumer Group 的 Rebalance 发生在消费者加入/离开时。如果 `session.timeout.ms` 设得太小（默认 45s），ClickHouse 的 Merge 或查询高峰期可能导致 Consumer 心跳超时，触发 Rebalance——期间整个 Consumer Group 停止消费。应对措施：增大 `kafka_session_timeout_ms`（如果 ClickHouse 版本支持），或调高 ClickHouse 的 `background_pool_size` 减轻 CPU 争抢。

### 思考题

1. **Kafka 表引擎能否实现 exactly-once 语义？如果不能，有什么补偿方案可以做到业务层精确去重？** 请从以下角度设计：
   - 利用 `_offset` + `_partition` 构建唯一标识
   - ReplacingMergeTree 的 ORDER BY 设计
   - 查询层的 argMax() 去重
   - 方案在各种故障场景（进程崩溃、网络分区、Kafka 重选 Leader）下的行为

2. **大促期间 Kafka 消息量从 50 万 msg/s 暴涨到 200 万 msg/s，客户端监控显示 `system.kafka_consumers` 中 `last_poll_time` 间隔越来越大，目标表的写入速度（行/s）反而在下降。请分析根因并提出至少三种优化方案。**
   提示路径：
   - Kafka 层面的 `kafka_num_consumers` + 分区扩容策略
   - ClickHouse 层面的 `kafka_max_block_size`、`kafka_thread_per_consumer`
   - 物化视图层面的写入放大（是否有多余的聚合 VIEW？）
   - `background_pool_size` 与 Merge IO 的相互影响

---

> **本章完**。你现在掌握了 Kafka → ClickHouse 实时管道的完整构建方法——从 Consumer Group 原理到 Offset 管理，从 JSON 到 Protobuf/Avro 的格式演进，从单表消费到多级物化视图的实时聚合链路。下一章，我们将深入数据压缩与编码优化——探究 `ZSTD`、`Delta`、`Gorilla` 等编码在列存引擎下的极致空间效率。

> **推广建议**：本章需开发、运维、数据三方协作——开发负责 Kafka 消息格式定义和物化视图 SQL（Step 2/5/6），运维负责 Docker 环境和 Kafka 集群配置（Step 1/4），数据工程师负责端到端延迟监控和压测方案（Step 3/测试验证）。建议开发先跑通 JSON 格式的 MVP，再引入 Protobuf/Avro。
