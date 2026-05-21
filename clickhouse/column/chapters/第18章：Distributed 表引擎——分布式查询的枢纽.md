# 第18章：Distributed 表引擎——分布式查询的枢纽

> **版本**：ClickHouse 25.x LTS
> **定位**：中级篇核心章节——理解分布式查询的扇出、聚合与合并全过程。
> **前置阅读**：第17章（分布式架构设计——分片与副本的博弈）
> **预计阅读**：30 分钟 | **实战耗时**：45 分钟

---

## 1. 项目背景

上个月，数据组的小胖在 2 分片 × 2 副本的 ClickHouse 集群上跑了一条看似人畜无害的查询：

```sql
SELECT count(DISTINCT user_id)
FROM orders_dist
WHERE created_at >= '2025-01-01';
```

结果跑出来是 **3,287,415**。他愣了一下，顺手又跑了一次—— **3,452,881**。第三次？**3,311,099**。三次查询，三个不同结果。小胖把咖啡杯往桌上一顿："这 Distributed 表有毒吧？数据还能自己变多又变少的？"

这其实是 ClickHouse 分布式查询中最典型的一个坑：**Distributed 表不是普通视图，它会向所有分片的所有副本发送查询，如果 `internal_replication` 没配好或者表结构没对齐，结果就会被重复计算。** 更糟糕的是，即使结果碰巧"正确"，不了解 Distributed 表的扇出逻辑也会写出性能极差的 SQL——比如在 Distributed 表上做 `ORDER BY created_at LIMIT 10`，它实际会从每个分片各取 10 条，然后在发起节点做全局排序，既浪费网络带宽，又浪费 CPU。

本章要解决的核心问题就是：**Distributed 表到底是怎么把一条 SQL 变成 N 条子查询发出去的？中间哪些 SQL 被改写、哪些被直接转发、哪些根本无法下推？** 理解这个"扇出-合并"模型，是写出正确且高效的分布式查询的前提。

---

## 2. 项目设计：剧本式交锋对话

周一晨会刚结束，小胖就拉着小白和大师不放，说昨晚又因为 Distributed 表的结果不准被业务方投诉了。

**小胖**（打开笔记本，指着屏幕上的 SQL）："大师，我昨天查了半天文档，还是没搞懂——Distributed 表不就是一个 VIEW 吗？数据明明都在 `orders_local` 这张 ReplicatedMergeTree 里，Distributed 表就是把查询转发到每个分片去不解完了？为什么同样的 SQL 跑出三个不同结果？"

**大师**（拉过椅子坐下）："小胖，你这句 '就是一个 VIEW' 恰恰是最大的误解。来，先看 Distributed 表的 DDL：

```sql
CREATE TABLE orders_dist AS orders_local
ENGINE = Distributed('my_cluster', default, orders_local, user_id);
```

这里面四个参数，你能说出每个的含义吗？"

**小胖**（挠头）："cluster 是集群名，database 和 table 是本地表……最后一个 user_id 是干嘛的？"

**大师**："那是**分片键**。Distributed 表有两件事要做：读和写。**写入时**，ClickHouse 用这个分片键的值做哈希，决定这条数据应该去哪个分片。比如 `user_id = 42` 哈希到分片 1，那这条 INSERT 就只发到分片 1。**读取时**，如果你在 WHERE 条件里指定了 `user_id = 42`，Distributed 表可以跳过不需要的分片——这叫**分片裁剪**。但如果你不指定分片键，查询就会扇出到所有分片。"

**技术映射 #1**：`ENGINE = Distributed(cluster, database, table, sharding_key)` 四个参数：cluster 定义拓扑，database + table 定位本地表，sharding_key 控制写入路由和读取裁剪。

---

**小白**（在笔记本上飞速记着，突然抬头）："等等，那问题来了。我们 2 分片 × 2 副本的集群，每个分片有两个副本存着相同的数据。`SELECT count() FROM orders_dist` 到底会去读多少个副本？如果读了全部 4 个副本，count 结果不就是双倍的？"

**大师**："问到要害了。Distributed 表读取时的行为取决于 `internal_replication` 参数——这是创建集群配置时指定的，和 Distributed 表本身无关。如果 `internal_replication = true`（推荐配置），Distributed 表知道数据在各副本间已经通过 ReplicatedMergeTree 自动同步了，所以**每个分片只会发查询给一个副本**。如果 `internal_replication = false`，它会把查询发给所有副本，那你读到的结果就是重复的。"

**小胖**（一拍大腿）："那我上次三次跑出三个不同结果——"

**大师**："大概率是因为你对着 Distributed 表做了 `count(DISTINCT user_id)`。Distributed 表的聚合查询是分两阶段执行的：第一阶段——**Partial Aggregation**——在每个分片上做部分聚合，比如每个分片先算出自己那部分的 `uniqExact(user_id)` 中间态；第二阶段——**MergingAggregated**——发起节点把所有分片的结果合并。但 `uniqExact` 跨分片没法直接相加，需要特殊的 State/Merge 函数或者换个写法。你不理解这个两阶段模型，结果当然时对时错。"

**技术映射 #2**：两阶段聚合 = 分片级部分聚合 + 发起节点全局合并。注意：`uniq()` 系列函数在不同阶段的语义不同，`count(DISTINCT x)` 在 Distributed 表上直接使用会产生错误结果——应该用 `uniqExact(user_id)` 或 `groupUniqArray` 函数，它们会在第一阶段生成中间态 State，第二阶段调用 Merge 函数正确合并。

---

**小白**（若有所思）："那如果我想做一个跨分片的子查询呢？比如只统计 VIP 用户的订单——这些 VIP 用户列表存放在另一张 Distributed 表里。两个 Distributed 表各自分散在不同分片上，IN 子查询怎么工作？"

**大师**："好问题。如果把 `users` 表也做成 Distributed 表，写个普通的 IN 子查询：

```sql
SELECT user_id, count() FROM orders_dist
WHERE user_id IN (SELECT user_id FROM users_dist WHERE level = 'VIP')
GROUP BY user_id;
```

这条 SQL 的执行方式是：**每个分片各自独立执行完整的查询**——包括子查询。也就是说，分片 1 拿着自己本地的 `users` 数据去跑子查询，分片 2 也是。但如果 VIP 用户 id=100 的数据在分片 1 的 users 表里，而他的订单数据在分片 2 的 orders 表里——这个 VIP 用户就永远不会被统计到。"

**小胖**（张大嘴）："那不全漏了？"

**大师**："所以 ClickHouse 引入了 **GLOBAL 关键字**。`GLOBAL IN` 和 `GLOBAL JOIN` 会改变查询执行方式：**子查询先在发起节点上跑一遍，把完整结果集塞进一张临时内存表，然后广播到所有分片**。每个分片拿着全局的结果集再执行外层查询，问题就解决了。"

**技术映射 #3**：GLOBAL IN/GLOBAL JOIN = 发起节点先物化子查询 → 临时表广播到全部分片 → 各分片基于全局维度表执行查询。代价是发起节点要额外读取一次子查询数据，且临时表在内存中——数据量大时可能 OOM。

---

**小白**（放下笔）："广播临时表到所有分片……那发起节点的网络压力岂不是很大？而且如果我是从某台机器上跑查询，Distributed 表会不会优先用本地副本？"

**大师**（赞许地看了小白一眼）："这两个问题正是 `prefer_localhost_replica` 做的事。默认情况下，Distributed 表对副本的选择是随机的——它会尽量均匀地把查询分散到各副本上。但如果你在节点 ch1 上执行查询，而 ch1 正好是分片 1 的副本之一，那你肯定希望读 ch1 的本地数据，而不是跨网络去读 ch2 的同分片副本。设置 `SETTINGS prefer_localhost_replica = 1`，Distributed 表会优先把查询发送给与发起节点在同一台机器上的副本，省去一次网络往返。"

**小胖**（突然来了精神）："那写入呢？如果我对 Distributed 表做 INSERT，数据最后一定会落到对应的 ReplicatedMergeTree 上吗？万一那个 shard 宕机了怎么办？"

**大师**："写入也分两种情况。正常情况：Distributed 表接受到 INSERT 后，用分片键算 hash，确定目标分片，然后把数据异步发送过去——如果分片有多个副本，Distributed 表默认**只发给一个副本**，依赖 ReplicatedMergeTree 的内部复制同步到其他副本。如果启用了 `insert_quorum`，会在达到指定副本数后才返回成功。

故障情况：如果目标分片宕机，Distributed 表不会直接丢弃数据，也不会无限重试。它会把写入失败的 Block 序列化到本地磁盘上的一个**Write-Ahead 目录**——路径是 `/var/lib/clickhouse/data/default/orders_dist/` 下的临时文件——然后周期性重试。你可以通过 `system.distribution_queue` 查看积压的待发送数据。"

**技术映射 #4**：分布式写入 = 哈希路由到目标分片 → 异步发送到一个副本 → ReplicatedMergeTree 自动复制到同分片其他副本。失败 = 落盘到 Write-Ahead 目录 → 定期重试。这意味着分布式写入是 **at-least-once** 语义——极端情况下（发起节点宕机）可能丢数据，业务侧需做幂等设计。

---

## 3. 项目实战

### 环境准备

沿用第 17 章的 2 分片 × 2 副本集群（4 台 ClickHouse 节点 + 1 台 ZooKeeper / clickhouse-keeper）。确保集群状态正常：

```shell
# 在任意节点上检查集群状态
clickhouse-client --query "
SELECT cluster, shard_num, replica_num, host_name, host_address
FROM system.clusters
WHERE cluster = 'my_cluster'
ORDER BY shard_num, replica_num
"
```

预期输出：
```
my_cluster  1  1  ch1  192.168.1.11
my_cluster  1  2  ch2  192.168.1.12
my_cluster  2  1  ch3  192.168.1.13
my_cluster  2  2  ch4  192.168.1.14
```

### 分步实现

#### Step 1：分析 Distributed 表查询流程

**目标**：用 `EXPLAIN` 直观看到 Distributed 表的"扇出 → 部分聚合 → 合并"三阶段。

```sql
-- 确认 Distributed 表已创建（从第 17 章继承）
SHOW CREATE TABLE orders_dist;

-- 如果不存在，重新创建：
CREATE TABLE orders_dist ON CLUSTER 'my_cluster' AS orders_local
ENGINE = Distributed('my_cluster', default, orders_local, user_id);

-- 基础聚合查询的执行计划
EXPLAIN
SELECT count(), sum(amount)
FROM orders_dist
WHERE created_at >= '2025-01-01';

-- 观察 EXPLAIN 输出中的关键节点：
-- ReadFromRemote    → 扇出到各分片的本地表
-- Expression        → 各分片执行部分聚合
-- MergingAggregated → 发起节点合并所有分片的结果
```

**解读 EXPLAIN 输出**：

```
Expression (Projection)
  MergingAggregated
    ReadFromRemote (Read from remote replica)
      ReadFromMergeTree (default.orders_local)
```

`ReadFromRemote` 表示 ClickHouse 通过网络把查询发送到各分片；`MergingAggregated` 表示发起节点拿到的已经是部分聚合后的结果，只做最终合并。这和张量计算里的 MapReduce 一模一样——Map 在各分片完成，Reduce 在发起节点完成。

```sql
-- 对比：直接查本地表（无扇出，无合并）
EXPLAIN
SELECT count(), sum(amount)
FROM orders_local
WHERE created_at >= '2025-01-01';

-- 输出简洁得多：
-- Expression
--   Aggregating
--     ReadFromMergeTree (default.orders_local)
```

> **坑点提示**：`SELECT * FROM orders_dist LIMIT 10` 在 EXPLAIN 中会显示为 `ReadFromRemote` + `Limit`，但实际执行时 ClickHouse 默认会从**每个分片取 10 行**，然后在发起节点取前 10 行——总网络传输量可能是 LIMIT 数量的 N 倍（N = 分片数）。如果分片不够 N 行则取实际行数，如果分片行数远超需要可以通过 `distributed_push_down_limit` 优化（0 = 从每个分片取 `LIMIT` 条，1 = 下推 LIMIT）。

---

#### Step 2：GLOBAL IN 跨分片子查询

**目标**：理解为什么普通子查询在 Distributed 表上会漏数据，GLOBAL IN 如何解决。

先准备两张 Distributed 表的数据：

```sql
-- 用户维度表（分布到各分片）
CREATE TABLE users_local ON CLUSTER 'my_cluster' (
    user_id UInt32,
    level LowCardinality(String),
    city String
) ENGINE = ReplicatedMergeTree('/clickhouse/tables/{shard}/users', '{replica}')
ORDER BY user_id;

CREATE TABLE users_dist AS users_local
ENGINE = Distributed('my_cluster', default, users_local, user_id);

-- 插入测试数据
INSERT INTO users_dist VALUES
    (1, 'VIP', '北京'),
    (2, '普通', '上海'),
    (3, 'VIP', '深圳'),
    (4, '普通', '杭州');
```

```sql
-- 场景 1：普通 IN——每个分片独立执行子查询
SELECT user_id, count() AS order_count
FROM orders_dist
WHERE user_id IN (
    SELECT user_id FROM users_dist WHERE level = 'VIP'
)
GROUP BY user_id;

-- 如果 users 表中 VIP 用户 id=3 在分片 2，
-- 但 orders 表中 user_id=3 的订单在分片 1
-- → 分片 1 的子查询找不到 id=3（因为分片 1 的 users 表没这条数据）
-- → 结果漏掉 user_id=3 的订单
```

```sql
-- 场景 2：GLOBAL IN——子查询先在发起节点物化，再广播
SELECT user_id, count() AS order_count
FROM orders_dist
WHERE user_id GLOBAL IN (
    SELECT user_id FROM users_dist WHERE level = 'VIP'
)
GROUP BY user_id;

-- 执行逻辑：
-- 1. 发起节点先执行子查询，得到 VIP 用户集合 {1, 3}
-- 2. 在内存中创建临时表 _data_xxxx，存放 {1, 3}
-- 3. 将临时表广播到 orders_dist 的所有分片
-- 4. 各分片基于完整的 VIP 用户列表执行外层查询
-- 5. 发起节点合并结果
```

> **重要**：GLOBAL IN 创建的临时表只存在于**当前会话**的生命周期内，且存在内存中。如果子查询结果集太大（百万级以上），可能触发 `max_memory_usage` 限制导致查询失败。此时应考虑用字典（Dictionary）替代。

---

#### Step 3：GLOBAL JOIN 跨分片关联

**目标**：理解 GLOBAL JOIN 的执行机制，以及它与普通 JOIN 的区别。

```sql
-- 创建用户画像宽表（维度表）
CREATE TABLE user_profiles ON CLUSTER 'my_cluster' (
    user_id UInt32,
    level LowCardinality(String),
    city String,
    registered_at Date
) ENGINE = ReplicatedMergeTree('/clickhouse/tables/{shard}/user_profiles', '{replica}')
ORDER BY user_id;

CREATE TABLE user_profiles_dist AS user_profiles
ENGINE = Distributed('my_cluster', default, user_profiles, user_id);

-- 插入维度数据
INSERT INTO user_profiles_dist VALUES
    (1, 'VIP', '北京', '2023-06-15'),
    (2, '普通', '上海', '2024-01-20'),
    (3, 'VIP', '深圳', '2024-08-01');
```

```sql
-- 普通 JOIN 在 Distributed 表上的表现
-- 各分片独立 JOIN，结果重复或遗漏
SELECT
    u.level,
    count() AS orders,
    sum(o.amount) AS total_amount
FROM orders_dist o
JOIN user_profiles_dist u ON o.user_id = u.user_id
GROUP BY u.level;
-- 问题：如果 user_id=3 的订单在分片 1，profiles 里 id=3 在分片 2
-- 分片 1 的 JOIN 找不到匹配的 profile → 丢失数据
```

```sql
-- GLOBAL JOIN：将 user_profiles 全量加载到内存，广播到所有分片
SELECT
    u.level,
    count() AS orders,
    sum(o.amount) AS total_amount
FROM orders_dist o
GLOBAL JOIN user_profiles_dist u ON o.user_id = u.user_id
GROUP BY u.level;

-- 执行逻辑：
-- 1. 发起节点先把 user_profiles_dist 全量读入内存
-- 2. 将这份完整的维度数据广播到 orders_dist 的每个分片
-- 3. 各分片在自己本地 orders 数据上做 JOIN（此时维度表是完整的）
-- 4. 发起节点合并最终结果
```

> **GLOBAL JOIN 适用条件**：右表必须是**小表**（能全部放入内存），因为它会被完整广播到所有分片。如果你要对 Distributed 表做大表 JOIN 大表，GLOBAL JOIN 会 OOM——应该改为先将两张表做 Colocate（相同分片键）建表，避免跨分片 JOIN。

---

#### Step 4：prefer_localhost_replica 实验

**目标**：对比启用/不启用本地优先读取的性能差异。

```sql
-- 场景：从 ch1 节点执行查询
-- ch1 是分片 1 的副本 1，ch3 是分片 2 的副本 1

-- 不启用本地优先：可能被调度到 ch2（分片 1 副本 2）
-- 需要跨网络传输数据
SELECT count(), sum(amount)
FROM orders_dist
WHERE created_at >= '2025-01-01'
SETTINGS prefer_localhost_replica = 0;

-- 启用本地优先：分片 1 读 ch1 本地数据，分片 2 读 ch3 远程数据
-- 减少一次跨节点网络传输
SELECT count(), sum(amount)
FROM orders_dist
WHERE created_at >= '2025-01-01'
SETTINGS prefer_localhost_replica = 1;
```

**验证方式**：在 `system.query_log` 中查看两次查询的 `query_duration_ms` 和 `read_rows`，启用 `prefer_localhost_replica = 1` 时，如果发起节点恰好在某个分片的副本上，该分片的 `read_rows` 将来自本地表而非远程表。

```sql
-- 查看最近两条查询的耗时对比
SELECT
    query_start_time,
    query_duration_ms,
    substring(query, 1, 80) AS query_preview,
    Settings['prefer_localhost_replica'] AS prefer_local
FROM system.query_log
WHERE query LIKE '%orders_dist%'
  AND type = 'QueryFinish'
ORDER BY query_start_time DESC
LIMIT 2;
```

---

#### Step 5：分布式写入一致性

**目标**：验证 Distributed 表的写入路由和 Write-Ahead 故障容错机制。

```sql
-- 场景 1：正常写入——验证数据路由到正确分片
INSERT INTO orders_dist VALUES
    (999999, 1, 99.99, 'pending', now());

-- 在 ch1 和 ch2（分片 1 的两个副本）上分别查询
-- 因为 user_id=999999 的哈希落在分片 1
SELECT * FROM orders_local WHERE order_id = 999999;
-- 两个副本应该都有这条数据（ReplicatedMergeTree 自动同步）

-- 在 ch3 和 ch4（分片 2）上查询
SELECT * FROM orders_local WHERE order_id = 999999;
-- 应该查不到，因为哈希路由到了分片 1
```

```sql
-- 场景 2：查看写入队列积压
-- Distributed 表发送失败的数据存放在本地文件系统中
SELECT
    database,
    table,
    is_blocked,          -- 分发是否被阻塞（目标表无活跃副本）
    error_count,         -- 累计错误次数
    last_exception       -- 最近一次错误信息
FROM system.distribution_queue
WHERE table = 'orders_dist';
```

```sql
-- 场景 3：模拟故障——先 STOP 一个节点的 Distributed 发送
-- （在生产中这是危险的，仅在测试环境操作）
-- 在发起节点上执行：
SYSTEM STOP DISTRIBUTED SENDS orders_dist;

-- 然后 INSERT 几条数据
INSERT INTO orders_dist VALUES (888888, 2, 50.00, 'pending', now());

-- 查看积压的数据文件
-- 实际存放在 <data_path>/default/orders_dist/ 下
-- 可以通过以下方式查看积压状态：
SELECT * FROM system.distribution_queue WHERE table = 'orders_dist';
-- error_count 会增加，is_blocked 可能为 1

-- 恢复发送
SYSTEM FLUSH DISTRIBUTED orders_dist;

-- 再次查看：积压应该被清空
SELECT * FROM system.distribution_queue WHERE table = 'orders_dist';
```

> **关键认知**：`SYSTEM STOP DISTRIBUTED SENDS` 会阻止该节点向目标分片发送数据，但**不会阻止接收 INSERT**。数据会被序列化为字节块暂存到本地 WAL 目录。`SYSTEM FLUSH DISTRIBUTED` 强制立即重试发送所有积压数据。这个机制保证了：即使目标 shard 宕机，发起节点不会丢数据——但如果**发起节点**在积压期间宕机，这些暂存数据就丢失了。

---

#### Step 6：监控 Distributed 表

```sql
-- 检查各副本只读状态（如果 ZK 故障或磁盘满，副本会进入只读模式）
SELECT
    table,
    is_readonly,
    is_session_expired,
    absolute_delay,
    queue_size,
    inserts_in_queue
FROM system.replicas
WHERE table = 'orders_local';

-- 监控复制延迟（ReplicatedMergeTree 的同步队列）
SELECT
    database,
    table,
    replica_name,
    absolute_delay,       -- 当前时间与最近入队操作时间的差值
    queue_size,           -- 队列中等待执行的任务数
    inserts_in_queue,     -- 队列中的 INSERT 任务数
    merges_in_queue       -- 队列中的 Merge 任务数
FROM system.replication_queue
ORDER BY absolute_delay DESC;
```

---

### 测试验证

**验证 1：聚合结果正确性**

```sql
-- 1. 在 ch1 上查本地表（单分片数据）
SELECT count() FROM orders_local;
-- 得到 A

-- 2. 在 ch3 上查本地表（单分片数据）
SELECT count() FROM orders_local;
-- 得到 B

-- 3. 在任一节点查 Distributed 表
SELECT count() FROM orders_dist;
-- 应该等于 A + B
```

**验证 2：模拟分片故障**

```shell
# 停掉 ch3（分片 2 的一个副本）
docker stop ch3

# 在存活节点上执行 Distributed 查询
clickhouse-client --query "SELECT count() FROM orders_dist"
# 查询应该仍能成功——Distributed 表会自动跳过不可达的分片
# 但结果只包含分片 1 的数据 + 分片 2 的存活副本数据

# 如果某分片所有副本都宕机，Distributed 查询会报错：
# "All replicas are stale" 或超时
```

**验证 3：GLOBAL IN 正确性**

```sql
-- 先分别查看各分片本地表的数据
-- ch1/ch2 (分片 1):
SELECT user_id, level FROM users_local;
-- ch3/ch4 (分片 2):
SELECT user_id, level FROM users_local;

-- 确认某个 VIP 用户的订单和用户信息在不同分片后
-- 对比普通 IN 和 GLOBAL IN 的结果差异
-- 普通 IN 可能漏掉跨分片的用户
-- GLOBAL IN 应该包含所有符合条件的用户
```

---

## 4. 项目总结

### Distributed 表行为速查表

| 场景 | 行为 | 注意事项 |
|------|------|----------|
| `SELECT count()` | 扇出到各分片 → 部分聚合 → 发起节点合并 | `count(DISTINCT x)` 需用 `uniqExact` 替代 |
| `SELECT * LIMIT 10` | 每个分片取 10 条 → 合并取前 10 | 网络传输量 = LIMIT × 分片数 |
| `INSERT (分片键随机)` | 哈希路由到目标分片，写一个副本 | ReplicatedMergeTree 自动同步到其他副本 |
| `INSERT (分片键固定)` | 始终写入同一分片 | 写入热点风险——慎用常量分片键 |
| `GLOBAL IN` | 子查询先物化 → 临时表广播 → 各分片执行 | 子查询结果集必须能装入内存 |
| `GLOBAL JOIN` | 右表全量读入 → 广播 → 各分片 JOIN | 右表必须是小表，大表会 OOM |

### 适用场景

✅ **最适合 Distributed 表的场景**：
1. **跨分片聚合查询**——如全集群级别的 PV/UV/GMV 统计，两阶段聚合自动完成。
2. **写入负载均衡**——使用 `rand()` 或高基数字段做分片键，数据均匀分布到各分片。
3. **小维表跨分片 JOIN**——维度表做 Distributed + `GLOBAL JOIN`，轻松实现跨分片关联。
4. **高可用读取**——某个分片宕机时，Distributed 表自动读取同分片的其他副本。

❌ **不太适合的场景**：
1. **大表对大表的 JOIN**——应使用相同分片键的 Colocate Join，避免 GLOBAL JOIN 内存溢出。
2. **需要精确去重的大数据量查询**——`count(DISTINCT x)` 在 Distributed 表上行为与直觉不符，应用 `uniqExact` 或其他去重方案。
3. **对延迟极度敏感的点查**——多一次网络跳转，P50 延迟增加约 2-5ms，每条查询都要走 Distributed 的扇出路径。

### 注意事项

1. **Distributed 表是逻辑表，无存储**：`DROP TABLE orders_dist` 只删除 Distributed 表定义，不会影响底层 `orders_local` 的数据。但如果反过来——删了 `orders_local` 而留下 `orders_dist`——所有查询都会报错 "Table doesn't exist"。
2. **分片键的选择直接决定查询能否裁剪**：`WHERE sharding_key = xxx` 可以跳过无关分片；`WHERE other_column = xxx` 会扫描所有分片。分片键应该选**查询中最常作为过滤条件的字段**。
3. **`prefer_localhost_replica = 1` 不是银弹**：如果发起节点本身不持有任何副本（比如你从一台专门的查询网关执行 SQL），这个参数毫无意义。它只在你从集群节点本地执行查询时有效。
4. **Distributed 表的 `fsync_after_insert` 只影响本地写入**：设置为 0 可以提升写入性能，但如果发起节点在 fsync 前宕机，积压的 WAL 数据会丢失。

### 常见踩坑经验

**坑 1：在 Distributed 表上做 `ORDER BY ... LIMIT` 导致性能灾难**

某团队写了一条 `SELECT * FROM events_dist ORDER BY ts DESC LIMIT 100`——实际行为是从 10 个分片各取 100 条（共 1000 条），然后全局排序取前 100。网络传输和排序开销远超预期。正确做法：如果 ts 是排序键，直接查本地表然后合并，或者使用 `distributed_push_down_limit = 1` 将 LIMIT 下推到各分片。

**坑 2：GLOBAL IN 的子查询触发全表扫描**

`SELECT * FROM orders_dist WHERE user_id GLOBAL IN (SELECT user_id FROM users_dist WHERE level = 'VIP')`——这条 SQL 的子查询会对 `users_dist` 做全表扫描。如果 users 表有 1 亿行，发起节点内存直接爆炸。正确做法：将 users 表做成字典，用 `dictGet` 替代子查询，或在子查询上加限制条件。

**坑 3：Distributed 写入的 at-least-once 语义导致重复**

如果发起节点在 INSERT 写 WAL 后、确认发送前宕机，重启后 ClickHouse 会重放 WAL 重新发送——但目标分片可能已经收到了第一次发送的数据（网络层成功但应用层未确认）。解决方案：在业务侧使用 `order_id` 等唯一键做幂等，配合 ReplacingMergeTree 去重，或在写入层引入事务 ID。

### 思考题

1. `SELECT * FROM distributed_table ORDER BY created_at LIMIT 10` 在 5 分片集群中实际从每个分片取多少条？如果 `created_at` 是排序键的第一列，ClickHouse 如何利用这个信息优化？提示：思考 `optimize_read_in_order` 和 Distributed 表的交互。

2. GLOBAL JOIN 和普通 JOIN 在 Distributed 表上的性能差异有多大？什么场景下即使用了 GLOBAL JOIN 也解决不了问题，必须改为 Colocate（相同分片键）的建表方式？提示：考虑右表的数据量级和内存限制。

---

> **本章完**。你已经理解了 Distributed 表的扇出查询模型——从 `SELECT count()` 的两阶段聚合，到 `GLOBAL IN` 的临时表广播，再到 Write-Ahead 的故障容错。下一章我们将深入 ReplicatedMergeTree 的内部机制，理解副本间数据同步的 ZooKeeper 路径结构与复制日志——那是保证"每个分片内数据一致"的关键技术。
