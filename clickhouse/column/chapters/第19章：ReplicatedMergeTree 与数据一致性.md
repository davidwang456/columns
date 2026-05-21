# 第19章：ReplicatedMergeTree 与数据一致性

> **版本**：ClickHouse 25.x LTS
> **定位**：中级篇核心章节。深入 ReplicatedMergeTree 的复制机制，理解 ZooKeeper 在副本协调中的角色，掌握 Quorum Write 一致性保证和副本故障恢复流程。
> **前置阅读**：第4章（MergeTree 家族概览）、第12章（备份与恢复策略）、第18章（集群与分片架构）
> **预计阅读**：45 分钟 | **实战耗时**：60 分钟

---

## 1. 项目背景

某 FinTech 公司的量化交易数据平台，核心表 `trades` 存储了全市场每笔成交记录——从美股、港股、加密货币到外汇，日均写入量 **3 亿行**。架构此前是单节点 ClickHouse，撑了小半年，直到有一天凌晨 2 点服务器宕机，所有实时风控策略全部失明——风控团队看着一片空白的 Grafana 大屏，整整 47 分钟不知道市场上的仓位风险到底有多大。事后复盘，CTO 划了一道红线：**生产环境必须至少三副本，任何单点故障不得造成数据不可读。**

架构团队按 1 分片 × 3 副本的拓扑部署了 ReplicatedMergeTree，ZooKeeper 跑在独立节点上。上线第一周，一切风平浪静。第二周周三，运维例行重启了一台 ClickHouse 节点——`systemctl restart clickhouse-server`，一个再正常不过的操作。重启完成后，DBA 随手跑了一条行数校验：

```sql
SELECT count() FROM trades;
```

三个节点返回的结果让她后背一凉：

- **ch1（Leader）**：1,000,050 行
- **ch2（Follower）**：1,000,000 行
- **ch3（Follower）**：1,000,000 行

Leader 比 Follower 多出了整整 50 行。这是金融数据——50 行多出来可能是某个大客户的交易被重复写入，可能是某笔风控信号被漏掉了，可能意味着监管报告里的数字是错的。更要命的是，业务层查的是 Distributed 表，`SELECT count() FROM trades_dist` 有时候返回 1,000,050，有时候返回 1,000,000——**取决于查询被路由到了哪个副本**。运营总监看到报表上同一个指标的两次刷新差出 50 行，直接打电话质问："你们的数据库到底准不准？"

这就是 ReplicatedMergeTree 一致性问题的真实面貌。它不是"数据会不会丢"这种二元问题，而是一系列灰色地带：**插入操作什么时刻算"已提交"？Leader 宕机时正在飞行中的数据怎么办？`insert_quorum` 设成 1 和设成 3 到底差在哪？副本重启后怎么追上 Leader？ZooKeeper 在这里面到底扮演了什么角色？**

这些问题如果不在架构设计阶段回答清楚，到了生产环境就是定时炸弹。本章将从 ZooKeeper 路径结构讲起，逐一拆解复制日志、写入仲裁、副本恢复和状态监控的全链路，让你对 ReplicatedMergeTree 的数据一致性做到心里有数。

---

## 2. 项目设计：剧本式交锋对话

周一上午十点，大师被 CTO 拉去开了一个"数据不准"的紧急会议。会后大师回到工位，白板上已经不知被谁画了一只乌龟，乌龟壳上写着"ZooKeeper"。

**小胖**（端着咖啡晃过来，看了一眼白板上的乌龟）：“大师，副本不就是 MySQL 的主从复制嘛？INSERT 一条数据，等从库同步完了再查不就行了？搞什么 ZooKeeper、Leader 选举，绕这么大一圈子干嘛？”

**大师**（拿起板擦把乌龟擦了）：“小胖，你犯了一个根本性的概念错误——MySQL 的主从复制和 ClickHouse 的 ReplicatedMergeTree，虽然都叫'复制'，但它们的复制对象完全不同。MySQL 复制的是 **binlog 中的 SQL 语句或 Row 事件**——从库拿到 binlog 后，要把这些操作重放一遍，再建索引、写 Undo Log、维护 Buffer Pool。而 ClickHouse 复制的是 **Part——已经写好的、不可变的数据文件块**。”

**小胖**（放下咖啡杯）：“复制 Part？什么意思？”

**大师**：“你在 ClickHouse 里 INSERT 100 万行，MergeTree 会把这 100 万行写成一个 Part——一个包含了列数据（`.bin`）、标记文件（`.mrk`）、主键索引（`.idx`）的完整目录。写入结束后，这个 Part 被直接注册到 ZooKeeper 的复制日志里。Follower 发现日志里多了一条新条目，就把这个 Part 从 Leader 的磁盘上拉过来——**不是重放 SQL，是直接拷贝文件目录**。Follower 拿到的 Part 和 Leader 上的完全一致，连 Checksum 都一样。你理解这个区别吗？”

**技术映射 #1**：ClickHouse 的复制是**数据块级别**的，不是操作级别的。每个 INSERT 产生一个不可变的 Part，复制就是把 Part 目录完整地从 Leader 传输到 Follower。这意味着 Follower 不需要理解 SQL、不需要重新计算列值、不需要建索引——它只是拷贝文件。这也是为什么 ClickHouse 的副本延迟通常在亚秒级：没有 SQL 重放开销，只传文件。

---

**小白**（推开自己的键盘，笔记本上已经画了一张 ZK 路径图）：“大师，那 ZooKeeper 在这套文件拷贝流程里具体存什么？我之前看 `/clickhouse/tables/1/trades/` 下面一大堆子路径，完全看不出哪个是干嘛的。”

**大师**（赞许地看着小白的笔记）：“好，这是理解 ReplicatedMergeTree 的第一把钥匙——ZooKeeper 路径结构。来，我给你画全了。”

```
/clickhouse/tables/{shard_id}/{table_name}/
├── metadata/                      ← 表结构元数据（DDL 变更同步）
│   ├── columns                    ← 列定义
│   └── metadata_version           ← 版本号（DDL 变更时递增）
├── log/                           ← **复制日志（核心）**
│   └── log-0000000001             ← 每条日志对应一个事件
│   └── log-0000000002             ← 按序号递增
├── leader_election/               ← Leader 选举
│   └── leader_election-0000000001 ← 临时顺序节点，最小者当选 Leader
├── blocks/                        ← Part 的 Hash 去重
│   └── <block_hash>               ← 防止重复插入
├── nonincrement_block_numbers/    ← 非递增 Block 编号（用于分布式写入去重）
├── quorum/                        ← Quorum Write 状态跟踪
│   └── <block_number>/            ← 每个需要仲裁的写入
├── replicas/                      ← 各副本的状态
│   ├── ch1/
│   │   ├── host                   ← 副本的 Host/IP
│   │   ├── is_active              ← 是否存活（临时节点）
│   │   ├── parts/                 ← 本副本的 Part 列表
│   │   ├── max_processed_insert_time ← 最新处理到的时间
│   │   ├── metadata_version       ← 副本的 DDL 版本
│   │   └── queue/                 ← **待处理任务队列（副本视角）**
│   │       ├── node0000000001     ← 还未执行的复制任务
│   │       └── ...
│   ├── ch2/
│   └── ch3/
└── mutations/                     ← Mutation 任务队列（ALTER DELETE/UPDATE）
```

**小白**（飞快地记着）：“所以 `/log/` 是全局任务列表，`/replicas/{replica}/queue/` 是每个副本自己的待办清单？”

**大师**：“没错。当一个 INSERT 在 Leader（比如 ch1）上完成写入后，Leader 会在 `/log/` 下创建一个新条目——比如 `log-0000000050`——内容大致是：'有一个新 Part `20250115_100_150_0`，数据块 hash 是 `0x3fa2`，位置在 ch1 上'。所有 Follower 都盯着 `/log/` 节点——一旦发现新条目，就把这个条目拷贝到自己的 `/replicas/chX/queue/` 下，然后开始从 Leader 拉文件。拉取完成后，把 queue 里的这条任务删掉。所以你看——`/log/` 条目的总数减去 `/queue/` 条目数，就是在判断副本的同步延迟。”

**技术映射 #2**：ZooKeeper 在 ReplicatedMergeTree 中扮演的是**元数据协调中心**——不是数据传输通道。数据本身（Part 文件）是副本之间直连传输的，ZooKeeper 只负责：(1) 存储复制日志条目；(2) 管理 Leader 选举；(3) 跟踪每个副本的 Part 列表；(4) 协调 Quorum Write 的确认计数。ZooKeeper 的读写压力很小，因为传输的是几百字节的元数据指针，而不是几百 MB 的数据文件。

---

**小胖**（挠头）：“那我有个问题——如果在 Leader 上 INSERT 到一半宕机了，Part 写了半截，会怎样？ZooKeeper 里的日志条目已经创建了吗？”

**大师**：“这就是 ReplicatedMergeTree 事务边界的精妙之处。顺序是这样的：

1. Leader 接收 INSERT，开始写数据到磁盘，生成 Part 目录。
2. Part 写完、fsync 落盘（这是关键——数据已经在磁盘上）。
3. **此时 Leader 才去 ZooKeeper 的 `/log/` 下创建日志条目。**
4. Follower 发现日志条目后拉取 Part。

如果 Leader 在第 1 步和第 2 步之间宕机——Part 写了一半，是一个残缺目录——那第 3 步永远不会发生。ZooKeeper 里没有日志条目，Follower 压根不知道有这回事儿。残缺的 Part 目录会在 Leader 重启后被 Cleanup 线程检测到，直接删除——因为 ClickHouse 在写 Part 时，先写在临时目录，写完验证 Checksum 后才 `rename` 到正式目录。这个 `rename` 是原子操作。所以只有完整落盘的 Part 才能被注册到 ZK——这是**先写盘、后注册**，与数据库 Write-Ahead Log（先写日志、后写数据）正好相反。”

**小胖**（若有所思）：“所以 ClickHouse 不保证'INSERT 成功返回 = 已复制'？”

**大师**：“对，这是最核心的一点。默认情况下，INSERT 返回 `Ok.` 只意味着 Part 在**本节点**写盘成功了，日志条目已经在 ZK 里注册了。但此时 Follower 可能还没拉取这个 Part——默认是**异步复制**。Follower 通常在几百毫秒内就会拉取同步，但在极端情况下（网络抖动、ZK 延迟、Follower 磁盘满了），这个窗口可能拉长到几秒甚至几分钟。这就是为什么 Distributed 表查询可能返回不一致的结果——查到了不同时间点的快照。”

**小白**（放下笔）：“那如果业务上要求强一致——INSERT 返回时数据必须至少在两个副本上都落盘了，怎么办？”

**大师**：“`insert_quorum`。这是 ClickHouse 引入的类 Paxos 确认机制。设 `insert_quorum=2` 之后，INSERT 的流程变成这样：

1. Leader 写盘完成，去 ZK 的 `/quorum/` 下创建记录：'Block 42，期待 2 个副本确认，目前确认数 1'。
2. Follower 拉取 Part 并写盘成功后，同样去 `/quorum/` 下给自己勾上确认。
3. Leader 一直阻塞 INSERT 客户端，**直到确认数达到 quorum 值**——达到后返回 `Ok.`；超时未达则报错。

注意这里的'确认'是 Follower 自己主动去 ZK 记录的——不是 Leader 去问 Follower。所以即使 Leader 在步骤 3 之前宕机，只要 Follower 已经写盘完成了，数据就在 Follower 上完好无损——新 Leader 会从 ZK 里看到这个 Block 的 quorum 记录，知道这个 Part 应该被所有副本拥有。”

**技术映射 #3**：`insert_quorum` 本质是一个**异步确认，同步阻塞**的机制。它增加了 INSERT 延迟（需要等 Follower），但保证了多数派副本拥有数据。它与 Raft 的区别在于：它不是日志复制的共识协议，而是对已经完成复制的 Part 做数量确认——更像一个分布式计数器。

---

## 3. 项目实战

### 环境准备

沿用前几章的 1 分片 × 3 副本集群，ZooKeeper 已部署在独立节点上。确认集群状态：

```sql
SELECT cluster, shard_num, replica_num, host_name 
FROM system.clusters 
WHERE cluster = 'my_cluster';
```

预期输出三个节点：ch1、ch2、ch3，分属同一个 Shard 的三个 Replica。

### Step 1：创建 ReplicatedMergeTree 表并观察 ZK 路径

```sql
CREATE TABLE trades ON CLUSTER 'my_cluster' (
    trade_id UInt64,
    symbol LowCardinality(String),
    price Decimal(10,4),
    volume UInt64,
    trade_time DateTime
) ENGINE = ReplicatedMergeTree(
    '/clickhouse/tables/1/trades',
    '{replica}'
)
ORDER BY (trade_time, trade_id)
PARTITION BY toYYYYMMDD(trade_time);
```

这里两个参数是理解 ReplicatedMergeTree 的关键：

- **第一个参数 `/clickhouse/tables/1/trades`**：ZooKeeper 中的路径，**同一个分片的所有副本必须共享同一个路径**，这样它们才能看到同一份复制日志和同一个 `/leader_election/`。
- **第二个参数 `{replica}`**：ClickHouse 会自动用 `config.xml` 中配置的 `<replica_name>` 替换这个宏。每个副本必须唯一，否则两个副本会往 ZK 里写同一个 `/replicas/{name}/` 节点，导致元数据互相覆盖。

表创建完成后，进入 ZooKeeper 命令行查看路径结构：

```bash
# 进入 ZK CLI
zkCli.sh -server localhost:2181

# 查看表级别路径
ls /clickhouse/tables/1/trades
# 输出：[metadata, log, leader_election, blocks, nonincrement_block_numbers, quorum, replicas, mutations]

# 查看 metadata
get /clickhouse/tables/1/trades/metadata
# 输出：版本号、排序键等序列化定义

# 查看 leader_election
ls /clickhouse/tables/1/trades/leader_election
# 输出：[leader_election-0000000001, leader_election-0000000002, leader_election-0000000003]
# 编号最小的节点是当前 Leader 创建的临时顺序节点

# 查看各个副本
ls /clickhouse/tables/1/trades/replicas
# 输出：[ch1, ch2, ch3]

# 查看某个副本的 parts
ls /clickhouse/tables/1/trades/replicas/ch1/parts
# 输出：[all_0_0_0, ...]  ← 当前此副本拥有的全部 Part
```

### Step 2：观察正常插入的复制流程

在 ch1 上执行一条 INSERT，然后去三个节点上分别查询——记录时间差，体会"近实时"的含义。

```sql
-- 在 ch1（Leader）上插入
INSERT INTO trades VALUES 
    (1, 'BTC-USD', 50000.0000, 100, '2025-01-15 10:00:00');

-- 立即在 ch2 上查询（通常 100-500ms 内可见）
SELECT count() FROM trades WHERE trade_id = 1;
-- 返回 1（大概率能查到，偶有延迟）

-- 在 ch3 上同样查询
SELECT count() FROM trades WHERE trade_id = 1;
-- 返回 1
```

此时去 ZooKeeper 观察复制日志：

```bash
# 查看复制日志
ls /clickhouse/tables/1/trades/log
# 输出：[log-0000000000, log-0000000001]
# log-0000000001 就是刚才那条 INSERT 的复制日志条目

# 查看日志内容
get /clickhouse/tables/1/trades/log/log-0000000001
# 内容示例：
#   type: GET_PART
#   source_replica: ch1
#   new_part_name: 20250115_0_0_0
#   block_id: "20250115_4073401838507223398_47703401838507223398"

# 查看各副本处理到了哪条日志
ls /clickhouse/tables/1/trades/replicas/ch2/queue
# 如果为空 → ch2 已完全追上
# 如果还有残留条目 → ch2 存在复制延迟

get /clickhouse/tables/1/trades/replicas/ch2/queue/node0000000001
# 查看待处理任务的具体内容（源副本+目标Part名）
```

**关键观察**：日志条目是**全局顺序**的（SeqNo 递增），但每个副本的处理速度可能不同——这就是造成副本之间数据量差异的根源。

### Step 3：Quorum Write 一致性实验

这是本章最核心的实验——通过控制 `insert_quorum`，实测不同一致性级别下的行为和代价。

```sql
-- 实验 A：默认写入（无仲裁）——异步复制
INSERT INTO trades VALUES (2, 'ETH-USD', 3000.0000, 50, now());

-- 立即去 ch2、ch3 上查询 trade_id = 2
-- 结果：大概率查到，但不保证（如果刚好网络抖动，可能几百毫秒后才出现）

-- 实验 B：quorum = 2——等待 2 个副本确认
INSERT INTO trades VALUES (3, 'SOL-USD', 100.0000, 1000, now())
SETTINGS insert_quorum = 2;
-- 这条 INSERT 会阻塞，直到至少有 2 个副本（Leader 自身计 1 个 + 至少 1 个 Follower）确认写盘完成

-- 此时去三个节点重查，trade_id = 3 必定在至少 2 个副本上存在
SELECT count() FROM trades WHERE trade_id = 3;
```

**实验 C：quorum 超过存活副本数——观察超时行为**

```sql
-- 假设集群有 3 个副本，设 quorum = 5（超过总数）
-- 或者模拟 Follower 宕机后 quorum = 3
INSERT INTO trades VALUES (4, 'AVAX-USD', 40.0000, 500, now())
SETTINGS insert_quorum = 3, insert_quorum_timeout = 5000;
-- 5 秒后报错：
-- DB::Exception: Timeout exceeded while waiting for quorum.
-- 注意：该 INSERT 未成功，trade_id = 4 的数据不会出现在任何副本上
```

**实验 D：主动 kill 一个 Follower，观察 quorum 写入行为**

```bash
# 终端 1：停止 ch3
docker stop ch3

# 终端 2：在 ch1 上执行 quorum = 2 的写入
```

```sql
INSERT INTO trades VALUES (5, 'DOT-USD', 20.0000, 200, now())
SETTINGS insert_quorum = 2;
-- 成功！因为仍有 2 个副本存活（ch1 + ch2），quorum 满足
-- 注意：ch3 宕机，这条数据不会出现在 ch3 上——但 quorum 只保证"写入时"的副本数，不保证"之后"所有副本都有

INSERT INTO trades VALUES (6, 'LINK-USD', 15.0000, 300, now())
SETTINGS insert_quorum = 3, insert_quorum_timeout = 3000;
-- 一定超时报错——ch3 离线，只剩 2 个副本，达不到 quorum = 3
```

**quorum 写入的真实语义**：`insert_quorum = N` 保证的是"INSERT 返回成功时，至少有 N 个副本已经将 Part 完整落盘"。但请注意——如果之后又有副本宕机或数据损坏，`insert_quorum` 不会保护你。它不是 Raft 的 committed log——它只是一次性的、写入时刻的确认。

### Step 4：模拟 Leader 故障和自动恢复

```bash
# 停止 ch1（当前 Leader）
docker stop ch1
```

观察 ZK 中 Leader 选举的变化：

```bash
# 查看 leader_election 节点
ls /clickhouse/tables/1/trades/leader_election
# ch1 的临时节点（leader_election-0000000001）已经消失（会话超时后自动删除）
# 剩下的最小序号节点（比如 ch2 的 leader_election-0000000002）自动成为新 Leader

# 新 Leader 选举完成通常需要几秒（取决于 session_timeout_ms 配置）
```

此时在 ch2 上执行 INSERT：

```sql
INSERT INTO trades VALUES (7, 'UNI-USD', 8.0000, 500, now());
-- 成功！ch2 已经是新 Leader，ch3 作为 Follower 正常接收
```

重启 ch1：

```bash
docker start ch1
```

ch1 启动后，会自动检测 `/log/` 中自己缺失的日志条目。它发现自己落后了，于是从 Leader（现在是 ch2）拉取缺失的 Part：

```sql
-- 在 ch1 上查看复制队列
SELECT 
    database, table,
    node_name,
    task_type,
    replica_name,
    source_replica,
    num_postponed,
    last_exception
FROM system.replication_queue
WHERE table = 'trades';
-- 初期会显示 ch1 正在拉取的 Part（类型为 GET_PART）
-- 处理完毕后队列清空，ch1 重新加入活跃副本列表
```

验证 catch-up 完成：

```sql
-- 在 ch1 上查询 trade_id = 7——应该能查到
SELECT * FROM trades WHERE trade_id = 7;

-- 确认三副本数据量对齐
SELECT count() FROM trades;
-- ch1、ch2、ch3 三个节点返回相同行数
```

### Step 5：监控复制状态

日常运维中，这套 SQL 就是你的复制健康检查仪表盘。

**一、复制队列积压检测**

```sql
SELECT 
    database,
    table,
    replica_name,
    absolute_delay,         -- 与最新日志条目的延迟（秒）
    queue_size,             -- 队列里还有多少任务
    inserts_in_queue,       -- 队列中待处理的 INSERT
    merges_in_queue,        -- 队列中待处理的 Merge
    part_mutations_in_queue,-- 队列中待处理的 Mutation
    oldest_queue_time,      -- 最旧任务产生时间
    oldest_part_to_get,     -- 最旧未同步的 Part 名
    new_part_name
FROM system.replication_queue
WHERE table = 'trades'
ORDER BY absolute_delay DESC;
```

如果 `absolute_delay > 60` 且持续增长，说明有副本严重落后——优先检查该副本的磁盘 IO、网络带宽、或是否有大量 Merge 占用了资源。

**二、各副本状态概览**

```sql
SELECT 
    database,
    table,
    replica_name,
    is_leader,              -- 是否为分片的 Leader
    is_readonly,            -- 是否因 ZK 断开进入只读模式
    is_session_expired,     -- ZK 会话是否过期
    total_replicas,         -- 配置的总副本数
    active_replicas,        -- 当前活跃副本数
    future_parts,           -- 副本上最新 Part 时间戳
    parts_to_check,         -- 待校验 Part 数
    zookeeper_exception     -- 最近一次 ZK 异常信息
FROM system.replicas
WHERE table = 'trades';
```

**三、异常场景识别**

```sql
-- 场景 1：某个副本进入只读模式
SELECT replica_name, is_readonly, zookeeper_exception
FROM system.replicas
WHERE table = 'trades' AND is_readonly = 1;
-- 常见原因：ZK session_timeout 导致 ClickHouse 认为失去了 Quorum，自动切只读保安全
-- 修复：SYSTEM RESTART REPLICA trades; 或重启 ClickHouse

-- 场景 2：Part 校验未通过（可能存在数据损坏）
SELECT replica_name, parts_to_check
FROM system.replicas
WHERE table = 'trades' AND parts_to_check > 0;
-- 修复：ALTER TABLE trades DETACH PART 'xxx'; 然后等待自动重新拉取

-- 场景 3：活跃副本数不足
SELECT total_replicas, active_replicas
FROM system.replicas
WHERE table = 'trades';
-- 如果 active_replicas < total_replicas，去寻找离线节点
```

**四、Mutation 复制状态**

```sql
-- ALTER DELETE/UPDATE 的复制是独立的 Mutation 通道
SELECT
    database, table, mutation_id,
    command,                -- 具体的 Mutation 命令
    create_time,
    parts_to_do,            -- 还有多少个 Part 需要执行此 Mutation
    is_done                 -- 是否已完成
FROM system.mutations
WHERE table = 'trades' AND is_done = 0;
```

### 测试验证

| 测试场景 | 操作 | 预期结果 | 验证方式 |
|---------|------|---------|---------|
| Quorum 写入一致性 | INSERT with `insert_quorum=2` | 至少 2 个副本有数据 | `SELECT count() FROM trades WHERE trade_id=N` 分别在 3 个节点执行 |
| Quorum 超时保护 | Kill 1 个副本后 `insert_quorum=3` | INSERT 报 Timeout 异常 | 5s 后应抛异常，不写入 | 
| 副本重启追赶 | Stop/Start ch1 | ch1 自动追上 | `system.replication_queue` 先有数据后清空 |
| ZK 路径一致性 | 反复读写后 | `/log/` 序号递增，各副本 `/queue/` 清空 | `zkCli.sh` 逐路径查看 |
| 只读模式检测 | 手动断开 ZK 网络 | `is_readonly = 1` | `system.replicas` 查询 |

---

## 4. 项目总结

### 一致性级别全景对比

| 设置 | 行为 | INSERT 延迟 | 数据安全性 | 适用场景 |
|------|------|------------|-----------|---------|
| `insert_quorum=0`（默认） | 异步复制，Best Effort | 最低（~10ms） | 低——Leader 宕机可能丢数据 | 日志、埋点、监控指标 |
| `insert_quorum=auto` | ClickHouse 自动根据存活副本数选择（`floor(total/2)+1`） | 中（~50ms） | 中——多数派存活即可读 | 通用业务表，容忍少量延迟 |
| `insert_quorum=2` | 等待至少 2 个副本确认 | 较高（~80ms，取决于网络） | 较高——单副本故障不影响 | 重要业务数据（订单、支付记录） |
| `insert_quorum=N`（全部） | 等待所有副本确认 | 最高（×N 倍延迟） | 立刻最强，但后续不保证 | 关键金融数据、监管上报 |

**一个重要的澄清**：`insert_quorum` 解决的是"INSERT 返回成功那一刻，数据在几个节点上"，而**不解决**"后续某个节点数据坏了怎么办"。它不提供持续的数据完整性保证——那是副本校验和备份的职责。也不解决"Follower 读到了旧数据"——因为 Follower 可能在拉取 Part 之前就响应了读请求——那是 `select_sequential_consistency` 的职责（见下章）。

### ReplicatedMergeTree 的适用场景

- **高可用自动故障切换**：Leader 宕机后几秒内自动选举新 Leader，写入不停。
- **读扩展**：查询可路由到 Follower 分担读负载（在 Distributed 表层面配置 `load_balancing`）。
- **跨机房灾备**：将不同副本部署在不同物理机房，通过 `insert_quorum` 保证两地都有数据。

### 核心注意事项

1. **Quorum Write 增加 INSERT 延迟**——这是一个线性增长的成本。如果集群跨机房部署、RTT 50ms，`insert_quorum=2` 可能把单次 INSERT 延迟从 10ms 拉到 150ms。金融机构通常设 `insert_quorum=2` 并接受延迟代价；日志系统绝不设 quorum。
2. **Leader 选举不是瞬时的**——ZooKeeper 的 Session Timeout 通常在 10-30 秒。Leader 宕机后，新 Leader 要等旧 Leader 的 ZK 临时节点过期才能被选举。这中间有数秒到数十秒的**写入不可用窗口**。
3. **过多 Part 会让 ZK 成为瓶颈**——每个 Part 都在 ZK 里对应一个元数据节点。如果表有 10 万个 Part（因为频繁 INSERT 不合并），ZK 里就有 10 万个 ZNode——这会让 ZK 的 Snapshot 巨大、选主变慢、心跳超时。定期检查 `system.parts` 中非活跃 Part 数量，触发 `OPTIMIZE` 合并，或控制插入频率。
4. **ZooKeeper 不是数据通道**——Part 文件是 ClickHouse 节点之间直传的（HTTP 协议，端口 9009 或自定义的 `interserver_http_port`），ZooKeeper 只传递日志指针。所以 ZK 的带宽压力很小，不需要高性能磁盘。

### 常见踩坑经验

**坑 1：`{replica}` 宏配置错误导致副本互相覆盖**

如果 `config.xml` 中的 `<macros>` 区块缺失，或者两个物理节点不小心配了相同的 `replica_name`，它们会在 ZK 的 `/replicas/` 下往同一个节点写元数据——结果就是互相踩踏：A 写了自己的 Part 列表，B 马上覆盖掉，A 再去读发现 Part 列表"莫名其妙"变了。症状极其诡异：查询在几秒内结果飘忽不定，`system.replication_queue` 反复出现同样的 GET_PART 任务。**止血方案**：确认每个节点的 `<replica_name>` 全局唯一，重启后检查 `/replicas/` 下的子节点数量是否等于副本数。

**坑 2：Quorum 设置过高导致写入全线崩溃**

一家公司为"金融级安全"设置了 `insert_quorum=3` 但只有 3 个副本。某天例行维护停掉一个副本后，所有 INSERT 全部超时——`insert_quorum=3` 但活跃副本数只有 2，永远达不到 quorum。业务写入停了整整 40 分钟，因为没人意识到 quorum 参数的这个隐含逻辑。**经验**：`insert_quorum` 应设成 `floor(total_replicas / 2) + 1`（多数派），不要设成全部——除非你对自己的高可用运维有十足把握。

**坑 3：ZK 会话超时后表自动进入 readonly 模式**

ClickHouse 每张 ReplicatedMergeTree 表都维持一个与 ZK 之间的 Session。如果网络闪断超出了 `session_timeout_ms`（默认值因版本而异，通常为 30000-120000ms），ZK 会认为这个 ClickHouse 节点死了——删除其临时节点。ClickHouse 检测到 Session 过期后，会把涉及的全部 ReplicatedMergeTree 表设为只读——**不处理写入、不处理 INSERT、不参与 Merge**，直到手动执行 `SYSTEM RESTART REPLICA` 或重启 ClickHouse。症状是 `is_readonly=1`。在生产环境中，ZooKeeper 需要独立的监控——ZK 的 Session 超时常常不是 ClickHouse 的问题，而是 ZK 自身负载过高（比如 Snapshot 太大导致 GC 停顿）。

**坑 4：`ALTER TABLE ... DELETE` 的 Mutation 复制可能拖垮集群**

Mutation（`ALTER DELETE` / `ALTER UPDATE`）也是通过复制日志传递的——但它们会触发生成新的 Part。如果在一个 100TB 的表上执行 `ALTER TABLE ... DELETE WHERE ...` 并且复制到所有副本，会产生大量的 Mutation 任务，形成"复制风暴"。所有副本同时在做 Mutation，CPU 和磁盘 IO 全部打满。**经验**：Mutation 是最后的手段——优先用分区裁剪（`DROP PARTITION`）而非 `DELETE`；必须在业务低峰执行；执行前先估算影响范围（`SELECT count() WHERE ...`）。

### 思考题

1. **insert_quorum 与 Leader 宕机**：假如你设置了 `insert_quorum=2`，Leader（ch1）执行 INSERT 时，Follower（ch2）已确认但 ch3 还未确认，此时 ch1 宕机。数据会不会丢失？为什么？提示：考虑 ZK `/quorum/` 路径下的确认记录是否会随 Leader 宕机而消失，以及新 Leader 选举后如何看待这条未完成的 Quorum 记录。

2. **ReplicatedMergeTree 的 Leader 选举 vs Raft/Paxos**：ReplicatedMergeTree 的 Leader 选举依赖 ZooKeeper 的临时顺序节点机制，本质上是最小编号节点当选。这与 Raft 的 Term-based Leader Election 和 Paxos 的 Prepare/Promise 机制有什么核心区别？为什么 ClickHouse 不自己实现一个 Raft 库，而是把协调逻辑甩给 ZooKeeper？提示：考虑 ClickHouse 复制的对象是"不可变的 Part 文件"而非"可变的操作日志"，以及 ZooKeeper 本身的 Watcher 和顺序节点机制在工程上的成熟度。

---

> **本章完**。副本一致性是分布式数据库中最微妙的话题——它不是非黑即白的"同步/异步"，而是一个沿着延迟、吞吐、安全三轴连续变化的光谱。理解本章中 ZK 路径结构、复制日志和 Quorum Write 的配合机制后，你在面对生产环境的副本不一致问题时，就不会再是满屏报错中一头雾水——而是知道该去查 `/log/` 还是 `/queue/`，该动手修 ZK 还是重启副本。下一章，我们将深入 Distributed 表的查询路由与负载均衡，让分片和副本的能力真正服务于海量并发查询。
