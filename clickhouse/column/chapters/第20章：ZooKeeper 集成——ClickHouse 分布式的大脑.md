# 第20章：ZooKeeper 集成——ClickHouse 分布式的大脑

> **版本**：ClickHouse 25.x LTS
> **定位**：中级篇核心章节。深入理解 ClickHouse 与 ZooKeeper 的协作机制，掌握 ZK 路径树、性能边界与 clickhouse-keeper 迁移策略。
> **前置阅读**：第12章（副本与复制机制）、第15章（日常运维与故障排查手册）
> **预计阅读**：45 分钟 | **实战耗时**：60 分钟

---

## 1. 项目背景

某金融数据平台，3 分片 × 2 副本的 ClickHouse 集群平稳运行了六个月。团队用 ReplicatedMergeTree 存了 500+ 张表，日均写入 30 亿行交易流水和行情快照。业务高峰期，Grafana 看板上的实时风控指标总能在 500ms 内刷出——一切看起来岁月静好。

直到那个黑色星期三。

凌晨两点四十分，运维工程师阿杰被 PagerDuty 叫醒：**所有 ReplicatedMergeTree 表全部进入只读模式**。`system.replicas` 面板一片猩红——`is_readonly = 1`。业务方的风控规则引擎因为无法写入计算结果，触发了熔断保护，整个交易链路的实时监控陷入盲区。

阿杰先尝试了常规三板斧：`SYSTEM RESTORE REPLICA`——报错 `Keeper error: connection loss`。`SYSTEM RESTART REPLICA`——同样失败。他盯着 ZooKeeper 监控面板，ZooKeeper 三个节点中有两个 CPU 100%，`mntr` 返回的 `znode_count` 赫然显示：**201,347**。

二十万个 ZNode。

经过连夜排查，根因浮出水面：

1. **ZooKeeper 内存膨胀至 8GB**：500+ 张 ReplicatedMergeTree 表，每张表的每个活跃 Part 在 ZK 中至少产生 3-5 个 ZNode——存储 metadata、columns、checksums、replication log entry。加上物化视图的内部表，实际管理的表数量远超 500。六个月的数据写入累计产生了海量 Part（后台 Merge 速度追不上写入速度），ZK 树节点数量悄悄突破了 20 万。

2. **建表耗时 30 秒+**：每创建一个新的 ReplicatedMergeTree 表，ClickHouse 需要在 ZK 中写入约 15-20 个持久 ZNode。在 ZK 节点已经严重过载的情况下，一次 `CREATE TABLE` 操作在 ZK 层的 ZooKeeper 事务排队时间超过 25 秒——开发团队以为是 ClickHouse "卡住了"，反复重试，进一步加重了 ZK 的负载。

3. **Merge 风暴引发复制延迟雪崩**：每月初是数据重算窗口，大量 `OPTIMIZE` 操作触发 Part 合并。每次 Merge 完成后，ClickHouse 需要在 ZK 的 `/log/` 路径下追加一条复制日志条目，其余副本轮询发现新条目后开始拉取新 Part。但 ZK 的写入吞吐只有约 10K ops/s——当 500 张表同时做 Merge，复制日志的写入请求瞬间把 ZK 打满，`replication_queue` 的 `absolute_delay` 从毫秒级飙升到分钟级。

运维团队的总结很扎心：**ZooKeeper 不是坏了，是被用坏了**。当初搭集群的时候，ZK 只是作为"配置中心"顺手部署的，谁也没想到有一天它要管理 20 万个 ZNode。更扎心的是：没有一个人完整理解 ClickHouse 到底在 ZK 里存了哪些东西——元数据、副本状态、分布式 DDL 的栅栏节点、`ON CLUSTER` 的同步屏障……这些概念散落在各个运维工程师的零星印象里，从未被系统性地梳理过。

**核心痛点**：ZooKeeper 是 ClickHouse 分布式架构的"大脑"——负责副本协调、DDL 同步、Leader 选举、Quorum 写入共识——但大多数团队把它当作黑盒对待。当集群规模从几十张表扩展到几百张表，从万级 Part 增长到十万级 Part，ZK 就从协调者变成了瓶颈。理解 ZK 的工作原理、知道它的性能边界、掌握从 ZK 迁移到 clickhouse-keeper 的时机和方法——这是每一个 ClickHouse 运维工程师必须跨越的中级门槛。

---

## 2. 项目设计：剧本式交锋对话

周五下午，大师把小白和小胖叫到监控大屏前。大屏上 ZooKeeper 的 `znode_count` 曲线已经连续三周呈 45 度角上扬。

**小胖**（瘫在椅子上刷手机）："大师，ZK 不就是存个配置吗？HBase 用 ZK 存 Region 位置、Kafka 用 ZK 存 Broker 元数据——到了 ClickHouse 这边，不就多存几张表的建表语句？至于把咱们半夜叫起来修故障吗？运维负责给 ZK 扩容不就行了？"

**大师**（指了指大屏上那个 20 万的数字）："小胖，你告诉我——ZK 官方推荐单个 Ensemble 管理的 ZNode 数量上限是多少？"

**小胖**（支吾了一下）："呃……几万？"

**大师**："更准确地说，生产环境中 ZK 能在 10 万 ZNode 以内稳定运行，超过 10 万风险明显上升，超过 20 万——就是你现在看到的样子。而且 ClickHouse 对 ZK 的使用远不止'存配置'这么简单。我让你看个东西。"

大师打开终端，连上 ZK CLI：

```
[zk: localhost:2181(CONNECTED) 0] ls /clickhouse
[tables, task_queue, dd_queue, macros, metadata]
```

**大师**："看清楚——`/clickhouse` 下面有五棵子树，每一棵都有特殊用途：

- **`/tables`**：这是最胖的一棵。每张 ReplicatedMergeTree 表在 `/tables/<db_id>/<table_name>/` 下有一整套子节点：`/metadata` 存 DDL、`/columns` 存列定义、`/replicas/<replica_name>/` 下存每个副本的 `host`、`port`、`is_active`、`parts`（每个 Part 的名字和 checksum）、`/log/` 存复制日志队列、`/leader_election/` 存 Leader 选举的临时节点、`/block_numbers/` 存去重块编号、`/quorum/` 存 Quorum 写入的协调状态、`/mutations/` 存未完成的 Mutation 任务。你刚才说 ZK'只存配置'——一张有 100 个活跃 Part 的 ReplicatedMergeTree 表，在 ZK 里就能产生 300+ 个 ZNode。500 张表，20 万 ZNode 就是这么来的。

- **`/task_queue`**：分布式 DDL 的任务队列。当你执行 `CREATE TABLE ... ON CLUSTER 'my_cluster'`，ClickHouse 会在这个路径下创建一个顺序节点作为 DDL 任务，所有节点轮询这个路径发现新任务并执行。你建一张表客户端等了 30 秒——那 25 秒不是在等 ClickHouse 创建本地目录，而是在等 ZK 写这几个 ZNode。

- **`/dd_queue`**：分布式 DDL 的同步栅栏。每个节点执行完 DDL 后在这个路径下写入一个 `finished` 节点，发起节点收集到全部 `finished` 节点后才返回客户端'成功'。

- **`/macros`**：集群宏变量——`{cluster}`、`{shard}`、`{replica}` 这些占位符的实际值都存在这里。

- **`/metadata`**：全局元数据——存储 `Database` 定义，用了什么引擎（Ordinary、Atomic、Replicated 等）."

**技术映射 #1**：ClickHouse 在 ZK 中不是简单地"存配置"——**ZK 承担了整套分布式协调基础设施的角色**：副本状态同步（Replication Log）、写入共识（Quorum）、Leader 选举、分布式 DDL 的屏障同步、去重块管理。理解这五棵子树的作用，是定位 ZK 问题的前提。

---

**小胖**（坐直了身体）："我靠，原来一个表在 ZK 里塞这么多东西……那 clickhouse-keeper 是啥？跟 ZK 有什么区别？为什么 ClickHouse 团队要自己再写一个？"

**大师**："好问题。clickhouse-keeper 在 21.12 版本作为实验性功能引入，22.3 版本正式 GA——用 C++ 重写了一个兼容 ZK 协议的替代品。它的核心优势有四个：

**第一，语言层面**：ZooKeeper 是 Java 程序，内存管理靠 JVM GC。ZK 管理的 ZNode 多了以后，JVM Heap 膨胀，Full GC 频繁——单次 STW（Stop The World）可能持续数秒，这期间 ClickHouse 发送的心跳包收不到响应，ZK 会话超时（默认 30 秒），所有 ReplicatedMergeTree 表直接切到只读模式。clickhouse-keeper 用 C++ 实现，没有 GC 停顿，内存占用大幅降低。

**第二，吞吐层面**：ZK 基于 Zab 协议，写操作必须经过 Leader，设计吞吐瓶颈约 10K ops/s。clickhouse-keeper 基于 Raft 协议 + NuRaft 库实现，内部做了大量的批处理和管道（Pipeline）写入优化，实测吞吐可达 100K ops/s——一个数量级的提升。

**第三，部署层面**：clickhouse-keeper 可以作为独立进程部署（与 ZK 一样去中心化），也可以**嵌入到 ClickHouse Server 进程中**。对于中小规模集群，嵌入模式省掉了额外维护一套 ZK 集群的运维成本。

**第四，功能层面**：clickhouse-keeper 内置了 `snapshot` 管理、`compact` 日志压缩、`check_consistency` 一致性校验——这些在 ZK 中需要外部脚本或手动干预。"

**技术映射 #2**：clickhouse-keeper 不是"ClickHouse 专属 ZK"，而是**ZK 协议的 C++ 高性能实现**——兼容 ZK 四字命令（mntr、stat、srvr），兼容 ZK 客户端协议，现有使用 ZK 的 ClickHouse 集群可以通过修改 `zookeeper` 配置中的 `host:port` 平滑迁移。

---

**小白**（合上笔记本，推了推眼镜）："大师，我理一下——所以 ZK/keeper 对 ClickHouse 来说是'不能挂'的组件。如果 ZK 完全不可用，ClickHouse 集群会发生什么？ReplicatedMergeTree 表还能读吗？还能写吗？"

**大师**："分两层回答你。"

**第一层：ZK 临时不可用（网络抖动，几秒钟）**。ClickHouse 的 ZK 客户端有重试机制（`session_timeout_ms` 默认 30000ms），短时间的连接中断不影响已建立会话的副本状态。你只要在 30 秒内恢复连接，一切如常。

**第二层：ZK 完全不可用（进程挂了，磁盘满了，超过 Session Timeout）**。这时每个 ReplicatedMergeTree 副本都会检测到 ZK 会话过期，然后将该表标记为 **`is_readonly = 1`**。关键结论：

- **查询（SELECT）不受影响**：只读模式下，ClickHouse 依然可以从本地磁盘读取现有的 Part 数据并返回查询结果。这是 ClickHouse 副本架构的一个重要设计——副本的本地数据是完整的，不依赖 ZK 来"告诉它数据在哪"。

- **写入（INSERT）被拒绝**：`INSERT` 操作会报错 `DB::Exception: Table is in readonly mode (zookeeper session expired)`。因为写入需要通过 ZK 协调副本之间的 Part 分发——没有 ZK，副本之间就失去了'我写了一个新 Part，你也去拉一下'的通信通道。

- **Merge 停止**：后台 Merge 同样需要 ZK 来更新副本状态。Merge 停止意味着 Part 持续堆积，磁盘空间加速消耗——ZK 恢复后，Merge 调度器会立即看到积压的 Part 并全力合并，造成短时间的 IO 风暴。"

**小胖**（挠头）："那 ClickHouse 为什么不用 MySQL Group Replication 那种 Paxos 协议，或者 MongoDB 的 Replica Set？非得依赖一个外部 ZK？"

**大师**："这个问题的关键在于**架构定位**。ClickHouse 的定位是 OLAP 分析引擎，不是 OLTP 数据库。它刻意把'分布式协调'这块剥离出去交给 ZK/keeper，而不是像 MySQL Group Replication 那样把 Paxos 嵌进存储引擎里。这样做的代价是多了一个外部依赖，但好处是：

1. **存储引擎更简单、更稳定**：MergeTree 的核心逻辑只有写入、Merge、读取——不掺和分布式共识，Bug 面小得多。
2. **协调层的故障隔离**：ZK 挂了，ClickHouse 只读但不丢数据；如果共识协议嵌在存储引擎里出了 Bug，可能导致数据不一致甚至损坏。
3. **生态复用**：ClickHouse 刚开源时直接借用了 Hadoop 生态成熟的 ZK 运维体系，降低用户的上手门槛。

但你要问'什么时候该考虑换成 clickhouse-keeper'——我的经验规则是：**当你的集群满足`总表数 > 100`或`活跃 Part 数 > 10000`或`ZK mntr 显示 znode_count > 50000`任意一条，就该评估迁移了。** 小规模集群用 ZK 完全够，运维也简单——你不需要为了一个 5 张表的小集群去折腾 keeper 的 Raft 配置。"

---

## 3. 项目实战

### 环境准备

本实验需要一个 ZooKeeper 集群（可从第 12 章环境复用）和一个单节点 ClickHouse（配置 ReplicatedMergeTree）。如果 ZooKeeper 未启动：

```bash
# 启动 ZK ensemble（3 节点）
docker run -d --name zk1 --network host \
  -e ZOO_MY_ID=1 -e ZOO_SERVERS="server.1=localhost:2888:3888;2181" \
  zookeeper:3.8

# 验证 ZK 可用
echo stat | nc localhost 2181
```

启动 ClickHouse：

```bash
docker run -d --name clickhouse-zk \
  --ulimit nofile=262144:262144 \
  -p 8123:8123 -p 9000:9000 \
  clickhouse/clickhouse-server:25.3

# 配置 ZK 连接
docker exec -it clickhouse-zk bash -c "
cat > /etc/clickhouse-server/config.d/zookeeper.xml << 'EOF'
<clickhouse>
  <zookeeper>
    <node>
      <host>host.docker.internal</host>
      <port>2181</port>
    </node>
  </zookeeper>
</clickhouse>
EOF
"
```

重启 ClickHouse 使 ZK 配置生效并验证连接：

```sql
SELECT * FROM system.zookeeper WHERE path = '/' LIMIT 1;
```

创建测试用的 ReplicatedMergeTree 表（模拟一个小规模业务场景）：

```sql
CREATE DATABASE IF NOT EXISTS ck20 ON CLUSTER 'default';

CREATE TABLE ck20.trades_local ON CLUSTER 'default' (
    trade_id       UInt64,
    symbol         LowCardinality(String),
    price          Decimal(18, 4),
    volume         UInt64,
    trade_time     DateTime,
    buyer_id       UInt64,
    seller_id      UInt64
) ENGINE = ReplicatedMergeTree('/clickhouse/tables/{shard}/ck20/trades', '{replica}')
PARTITION BY toYYYYMMDD(trade_time)
ORDER BY (symbol, trade_time);

-- 如果单节点测试，直接用 ReplicatedMergeTree
CREATE TABLE ck20.trades (
    trade_id       UInt64,
    symbol         LowCardinality(String),
    price          Decimal(18, 4),
    volume         UInt64,
    trade_time     DateTime,
    buyer_id       UInt64,
    seller_id      UInt64
) ENGINE = ReplicatedMergeTree('/clickhouse/tables/1/ck20/trades', 'ch1')
PARTITION BY toYYYYMMDD(trade_time)
ORDER BY (symbol, trade_time);
```

插入测试数据（模拟高频写入产生多个 Part）：

```sql
INSERT INTO ck20.trades
SELECT
    number AS trade_id,
    ['AAPL','GOOGL','TSLA','MSFT','NVDA'][rand() % 5 + 1] AS symbol,
    toDecimal64(randUniform(100, 5000), 4) AS price,
    rand64() % 100000 + 1 AS volume,
    now() - INTERVAL number % 7 DAY + INTERVAL number % 86400 SECOND AS trade_time,
    rand64() % 100000 AS buyer_id,
    rand64() % 100000 AS seller_id
FROM numbers(500000);

-- 强制多次 INSERT 产生多个 Part（模拟高频写入场景）
INSERT INTO ck20.trades SELECT * FROM ck20.trades WHERE trade_id < 10000;
INSERT INTO ck20.trades SELECT * FROM ck20.trades WHERE trade_id BETWEEN 10000 AND 20000;
INSERT INTO ck20.trades SELECT * FROM ck20.trades WHERE trade_id BETWEEN 20000 AND 30000;
```

---

### 分步实现

#### Step 1：浏览 ClickHouse 在 ZK 中的完整路径树

ZK 自带的客户端脚本 `zkCli.sh` 是最直接的探索工具。如果 ZK 部署在 Docker 中：

```bash
# 进入 ZK 容器
docker exec -it zk1 bash

# 启动 ZK CLI
zkCli.sh -server localhost:2181

# 查看 ClickHouse 根路径
ls /clickhouse
# 输出：[tables, task_queue, dd_queue, metadata, macros]
```

逐层深入：

```bash
# 1. 查看表列表
ls /clickhouse/tables
# 输出：[1]  —— 数字是 database 的内部 ID

ls /clickhouse/tables/1
# 输出：[ck20, ...]  —— 该 database 下的所有 ReplicatedMergeTree 表

# 2. 查看单张表的完整节点树
ls /clickhouse/tables/1/ck20/trades
# 输出：
# [metadata]         —— DDL 定义
# [columns]          —— 列元数据
# [replicas]         —— 副本状态
# [log]              —— 复制日志（顺序节点）
# [leader_election]  —— Leader 选举临时节点
# [block_numbers]    —— 去重块编号
# [quorum]           —— Quorum 写入协调
# [mutations]        —— 待执行的 Mutation 队列

# 3. 查看表级元数据
get /clickhouse/tables/1/ck20/trades/metadata
# 输出：engine: ReplicatedMergeTree
# partition_key: toYYYYMMDD(trade_time)
# sorting_key: symbol, trade_time
# ...

# 4. 查看副本信息
ls /clickhouse/tables/1/ck20/trades/replicas
# 输出：[ch1]

get /clickhouse/tables/1/ck20/trades/replicas/ch1/host
# 输出：clickhouse-zk  —— 副本所在主机名

ls /clickhouse/tables/1/ck20/trades/replicas/ch1/parts
# 输出：[20260401_1_1_0, 20260402_2_2_0, ...]  —— 每个 Part 一个 ZNode

get /clickhouse/tables/1/ck20/trades/replicas/ch1/parts/20260401_1_1_0
# 输出：该 Part 的 checksum、行数、大小等元数据

# 5. 查看复制日志（理解副本同步的核心）
ls /clickhouse/tables/1/ck20/trades/log
# 输出：[log-0000000000, log-0000000001, log-0000000002, ...]
# 每次 Merge 或 Mutation 完成后，Leader 在此追加一条日志

get /clickhouse/tables/1/ck20/trades/log/log-0000000000
# 输出：记录了这个操作的类型（GET_PART / MERGE_PARTS / DROP_RANGE）和参数

# 6. 查看 Leader 选举（临时节点——会话断开即删除）
ls /clickhouse/tables/1/ck20/trades/leader_election
# 输出：[leader_election-0000000000]  —— 当前 Leader 的临时节点
```

**关键认知**：`/log/` 下的复制日志条目是清理最容易被忽略的地方。ZK 中这些顺序节点默认不会被自动删除——ClickHouse 在多个副本都消费了某条日志后会发一个删除请求。但如果某些副本长期掉线或已废弃，日志条目会一直堆积，这就是 ZNode 数量膨胀的主要原因之一。

回到 ClickHouse 客户端，用 SQL 直接查询 ZK 数据（无需 zkCli）：

```sql
-- 查看根路径
SELECT * FROM system.zookeeper WHERE path = '/';

-- 查看表节点
SELECT name, numChildren 
FROM system.zookeeper 
WHERE path = '/clickhouse/tables/1/ck20/trades';

-- 查看副本的 Part 列表
SELECT name 
FROM system.zookeeper 
WHERE path = '/clickhouse/tables/1/ck20/trades/replicas/ch1/parts';
```

---

#### Step 2：监控 ZK 状态

ZK 提供了 "四字命令"（Four Letter Words），最常用的是 `mntr`、`stat`、`srvr`：

```bash
# mntr：监控指标全景
echo mntr | nc localhost 2181
# zk_version      3.8.0
# zk_avg_latency  2
# zk_max_latency  45
# zk_min_latency  0
# zk_packets_received 1520345
# zk_packets_sent     1530028
# zk_num_alive_connections 3
# zk_outstanding_requests   0
# zk_server_state   follower
# zk_znode_count    485     <—— 核心指标！
# zk_watch_count    32
# zk_ephemerals_count 15
# zk_approximate_data_size 1048576
# zk_open_file_descriptor_count 58
# zk_max_file_descriptor_count 1048576

# stat：连接统计 + 延迟
echo stat | nc localhost 2181
# Latency min/avg/max: 0/2/45
# Received: 1520345
# Sent: 1530028
# Connections: 3
# Znode count: 485

# srvr：服务器运行状态
echo srvr | nc localhost 2181
# Mode: follower（或 leader）
```

ClickHouse 侧的内置监控查询：

```sql
-- ClickHouse 与 ZK 的连接状态
SELECT * FROM system.zookeeper_connection;
-- connected 字段：1 = 正常连接，0 = 断开
-- session_uptime：当前会话持续时间

-- ZK 相关的事件统计
SELECT 
    event, 
    value 
FROM system.events 
WHERE event LIKE '%ZooKeeper%'
ORDER BY value DESC;
-- ZooKeeperTransactions：ZK 事务总数
-- ZooKeeperRequests：请求总数
-- ZooKeeperWatchResponse：Watch 回调触发次数

-- 检查所有副本的 ZK 路径是否存活
SELECT 
    database, 
    table, 
    zookeeper_path, 
    replica_path,
    is_readonly,
    zookeeper_exception
FROM system.replicas
WHERE is_readonly = 1;
```

---

#### Step 3：常见 ZK 问题——Part 数过多导致 ZNode 爆炸

这个问题在 3 分片 × 2 副本的金融数据集群中真实发生过。先诊断：

```sql
-- 按表统计活跃 Part 数
SELECT 
    table,
    count() AS active_parts,
    sum(rows) AS total_rows,
    formatReadableSize(sum(bytes_on_disk)) AS disk_size,
    -- 估算该表在 ZK 中的 ZNode 数（保守估算：每 Part × 3 + 固定开销 × 20）
    count() * 3 + 20 AS estimated_znodes
FROM system.parts
WHERE active
  AND database = 'ck20'
GROUP BY table
ORDER BY active_parts DESC;
```

**ZK ZNode 估算公式**：
```
ZK ZNode 数 ≈ 表数 × (20 + 副本数 × 平均 Part 数 × 3 + 复制日志堆积数 + Leader 选举临时节点)
```
其中 `×3` 是因为每个 Part 需要存储 `columns`、`checksums`、`metadata` 三个信息的 ZNode（实际在不同路径下）。

如果某张表 Part 数超过 5000，应立即采取行动：

```sql
-- 方案 1：加速 Merge（降低 Merge 触发门槛）
ALTER TABLE ck20.trades MODIFY SETTING 
    merge_with_ttl_timeout = 3600,    -- TTL Merge 间隔
    merge_max_block_size = 8192,       -- Merge 最大块大小
    max_parts_in_total = 5000;         -- Part 总数上限（超出后拒绝写入）

-- 方案 2：手动触发 Merge（在低峰期执行）
OPTIMIZE TABLE ck20.trades FINAL;

-- 方案 3：如果 Part 数已到危险水平，先止血
SYSTEM STOP MERGES ck20.trades;      -- 暂停 Merge，防止 ZK 进一步过载
-- 排查根因后恢复
SYSTEM START MERGES ck20.trades;
```

**根因排查清单**：
- 写入频率是否过高（频繁的小批量 INSERT → 大量微型 Part）
- 是否有人用 `ALTER DELETE/UPDATE`（Mutation 会增加 Part）
- TTL 是否配置不当（TTL 删除也会创建新 Part）
- Merge 是否因磁盘 IO 瓶颈而积压

---

#### Step 4：ZK 残留数据清理

当表被删除后，ZK 中的路径可能残存——尤其在 `ON CLUSTER` 执行不完全或某个副本被强制下线后：

```sql
-- 1. 标准删除路径：在所有副本上执行 DROP TABLE
DROP TABLE IF EXISTS ck20.old_trades ON CLUSTER 'default';

-- 2. 如果某个副本已不存在，但 ZK 中有残留
-- 在仍然存活的副本上执行
SYSTEM DROP REPLICA 'ghost_replica' FROM ZKPATH '/clickhouse/tables/1/ck20/old_trades';

-- 3. 对于完全残留的表路径（所有副本已删除但 ZK 路径仍在）
-- 需要直接在 ZK 中删除——但这不是推荐方式，仅在确认无副本使用时操作
-- 更安全的方式是在 ClickHouse 中执行
SYSTEM DROP REPLICA 'ch1';
-- 或如果路径已知
SYSTEM DROP REPLICA 'ch1' FROM ZKPATH '/clickhouse/tables/1/ck20/old_trades';
```

**重要警告**：不要直接使用 `zkCli.sh deleteall` 删除 `/clickhouse/tables/...` 路径。ClickHouse 在检测到 ZK 路径被外部删除后，会将对应表标记为 `readonly` 并且无法恢复——唯一的修复方式是从备份重建整个表。务必使用 `SYSTEM DROP REPLICA` 走 ClickHouse 的清理流程。

---

#### Step 5：迁移到 clickhouse-keeper

对于已经出现 ZK 性能瓶颈的集群，迁移到 clickhouse-keeper 是标准的升级路径。以下为配置示例：

```xml
<!-- config.xml 或 config.d/keeper.xml -->
<clickhouse>
    <!-- clickhouse-keeper 服务端配置（嵌入式模式） -->
    <keeper_server>
        <tcp_port>9181</tcp_port>
        <server_id>1</server_id>
        <log_storage_path>/var/lib/clickhouse/coordination/log</log_storage_path>
        <snapshot_storage_path>/var/lib/clickhouse/coordination/snapshots</snapshot_storage_path>
        
        <coordination_settings>
            <operation_timeout_ms>10000</operation_timeout_ms>
            <session_timeout_ms>30000</session_timeout_ms>
            <raft_logs_level>information</raft_logs_level>
            <snapshot_distance>100000</snapshot_distance>
            <snapshots_to_keep>3</snapshots_to_keep>
            <stale_log_gap>10000</stale_log_gap>
            <fresh_log_gap>200</fresh_log_gap>
        </coordination_settings>
        
        <raft_configuration>
            <server>
                <id>1</id>
                <hostname>ch1.example.com</hostname>
                <port>9444</port>
            </server>
            <server>
                <id>2</id>
                <hostname>ch2.example.com</hostname>
                <port>9444</port>
            </server>
            <server>
                <id>3</id>
                <hostname>ch3.example.com</hostname>
                <port>9444</port>
            </server>
        </raft_configuration>
    </keeper_server>
    
    <!-- ClickHouse 连接到本地的 clickhouse-keeper -->
    <zookeeper>
        <node>
            <host>localhost</host>
            <port>9181</port>
        </node>
    </zookeeper>
</clickhouse>
```

迁移策略建议：

1. **新建集群直接使用 keeper**：无需任何迁移工作。
2. **存量集群的"双写迁移"**（推荐）：先搭建独立的 clickhouse-keeper 集群（3 节点），在 ClickHouse 中配置 `zookeeper` 指向 keeper，观察 `system.zookeeper_connection` 确认连接正常后，使用 `clickhouse-keeper-client` 将 ZK 数据全量导出再导入到 keeper——keeper 兼容 ZK 的 Snapshot 格式。
3. **嵌入模式（Embedded）的局限**：嵌入式 keeper 与 ClickHouse Server 共享进程——ClickHouse 重启时 keeper 也重启，Raft 选举期间（2-5 秒）副本无法写入。对于需要高可用的核心业务，建议独立部署 keeper 进程。

---

#### Step 6：ZK 性能健康检查脚本

建立定期巡检机制，将以下查询整合为监控脚本（每日执行）：

```sql
-- ZK 健康检查综合查询
SELECT 'ZK Connectivity' AS check_item,
       CASE WHEN connected = 1 THEN 'OK' ELSE 'CRITICAL' END AS status,
       formatReadableTimeDelta(session_uptime) AS detail
FROM system.zookeeper_connection
UNION ALL
SELECT 'ZK ZNode Count',
       CASE 
           WHEN countDistinct(name) < 10000 THEN 'OK' 
           WHEN countDistinct(name) < 50000 THEN 'WARN' 
           ELSE 'CRITICAL' 
       END,
       toString(countDistinct(name)) || ' znodes'
FROM system.zookeeper 
WHERE path = '/clickhouse/tables'
UNION ALL
SELECT 'Active Parts Total',
       CASE 
           WHEN count() < 5000 THEN 'OK' 
           WHEN count() < 10000 THEN 'WARN' 
           ELSE 'CRITICAL' 
       END,
       toString(count()) || ' active parts'
FROM system.parts WHERE active
UNION ALL
SELECT 'Replication Lag (max)',
       CASE 
           WHEN max(absolute_delay) < 60 THEN 'OK' 
           WHEN max(absolute_delay) < 300 THEN 'WARN' 
           ELSE 'CRITICAL' 
       END,
       toString(max(absolute_delay)) || ' seconds'
FROM system.replication_queue
UNION ALL
SELECT 'Readonly Replicas',
       CASE 
           WHEN count() = 0 THEN 'OK' 
           WHEN count() < 3 THEN 'WARN' 
           ELSE 'CRITICAL' 
       END,
       toString(count()) || ' readonly replicas'
FROM system.replicas WHERE is_readonly = 1;
```

可以将其包装为 `cron` 任务，输出到监控系统：

```bash
#!/bin/bash
# check_zk_health.sh —— 每日 ZK 健康巡检
RESULT=$(clickhouse-client --query "
SELECT concat(check_item, ': ', status, ' (', detail, ')') 
FROM (...  -- 上述健康检查查询
")
echo "$RESULT" | grep -q "CRITICAL" && \
  echo "ALERT: ZK health check failed" && exit 1 || exit 0
```

---

### 测试验证

**验证 1：建表前后 ZK 树对比**

```sql
-- 建表前查看 ZK 根路径
SELECT name, numChildren FROM system.zookeeper WHERE path = '/clickhouse/tables/1';

-- 创建一张新表
CREATE TABLE ck20.test_znode (
    id UInt32, val String
) ENGINE = ReplicatedMergeTree('/clickhouse/tables/1/ck20/test_znode', 'ch1')
ORDER BY id;

-- 建表后再次查看——会看到 ck20/test_znode 节点及其子树
SELECT name, numChildren FROM system.zookeeper WHERE path = '/clickhouse/tables/1/ck20';
```

**验证 2：模拟 ZK 断连观察 ClickHouse 行为**

```sql
-- 通过 system.zookeeper_connection 观察连接状态
SELECT * FROM system.zookeeper_connection;
-- connected = 1

-- 此时如果停止 ZK 服务（docker stop zk1），30 秒后再次查询
SELECT 
    table, 
    is_readonly, 
    zookeeper_exception 
FROM system.replicas 
WHERE database = 'ck20';
-- is_readonly = 1
-- zookeeper_exception: "Session expired"

-- 恢复 ZK 后
SYSTEM RESTORE REPLICA ck20.trades;
-- is_readonly 恢复为 0
```

**验证 3：clickhouse-keeper 与 ZooKeeper 资源对比**

```sql
-- 在相同工作负载下对比
-- ZK 端（需要 JVM 监控）：
--   Heap 使用: 4-8GB（500 表场景）
--   写入吞吐: ~8K ops/s
--   GC 暂停: 出现频率 > 5 次/分钟

-- clickhouse-keeper 端（通过 ClickHouse 内置表查询）：
--   内存使用:  < 500MB（同等负载）
--   写入吞吐: ~80K ops/s
--   GC 暂停: 0（无 GC）
SELECT 
    metric, value 
FROM system.metrics 
WHERE metric LIKE '%Keeper%';
```

---

## 4. 项目总结

### ZK vs clickhouse-keeper 对比表

| 维度 | ZooKeeper | clickhouse-keeper |
|------|-----------|-------------------|
| 实现语言 | Java | C++（ClickHouse 原生） |
| 内存开销 | 高（JVM Heap 4-8GB，含 GC 开销） | 低（C++ 原生内存管理，同等工作负载 ≈ 500MB） |
| 写入吞吐 | ~10K ops/s（Zab 协议 + 单 Leader 瓶颈） | ~100K ops/s（NuRaft + Pipeline 批处理） |
| GC 影响 | Full GC 可能导致数秒 STW，触发 ClickHouse ZK 会话超时 | 无 GC 停顿 |
| 部署模式 | 独立进程（必须额外部署维护） | 独立进程 + 嵌入 ClickHouse 进程两种模式 |
| ZK 协议兼容 | 原生实现 | 完全兼容 ZK 四字命令和客户端协议 |
| 快照管理 | 需要外部脚本（PurgeTxnLog） | 内置自动快照和日志压缩 |
| ClickHouse 集成 | 通过 `<zookeeper>` 配置 | 通过 `<zookeeper>` 配置指向 keeper 端口即可 |
| 社区成熟度 | 20 年历史，Apache 顶级项目 | 2022 年 GA，ClickHouse 官方维护 |
| 适用规模 | ≤100 表，≤10000 Part，≤50000 ZNode | 无明确上限，已验证 500+ 表、百万 ZNode 稳定运行 |

### 适用场景

- **ZooKeeper 仍然合适**：小规模集群（< 20 张表，< 5000 Part），团队已有 ZK 运维经验，不想引入新组件。利用成熟的 ZK 监控和管理生态。
- **clickhouse-keeper 更适合**：中大规模集群（> 100 张表，> 10000 Part，> 50000 ZNode），对延迟敏感（GC 停顿不可接受），愿意拥抱 ClickHouse 原生方案。
- **嵌入式 keeper**：开发环境、测试环境、边缘节点——不需要独立协调服务的场景，减少运维复杂度。

### 注意事项

1. **ZK/keeper 集群节点数必须为奇数**（3、5、7）。偶数节点无法在脑裂时形成多数派，反而降低可用性。
2. **ZK 的数据目录必须放在 SSD 上**。ZooKeeper 的事务日志写入是同步的（fsync），HDD 的延迟会导致 ZK 写入吞吐骤降至几十 ops/s，连锁引发 ClickHouse 的 ZK 请求排队超时。
3. **ZK JVM Heap 通常设为 4-8GB**。过小导致频繁 GC，过大导致 GC 单次停顿时间过长。经验公式：Heap ≥ 2 × ZNode 数据总量。
4. **clickhouse-keeper 的 `snapshot_distance` 影响恢复速度**。默认 100000（每 10 万条日志生成一个快照）。写入密集型集群可适当调小此值（如 50000），以加快故障恢复时的日志重放速度。

### 常见踩坑经验

1. **ZK 磁盘满导致全集群只读**：这是 P0 级故障——ZK 写事务日志（`version-2/log.*`）失败后，整个 ZK Ensemble 拒绝服务，所有 ClickHouse 副本在 `session_timeout_ms`（默认 30s）后全部进入 `is_readonly`。唯一的修复是先清理 ZK 磁盘，再逐一 `SYSTEM RESTORE REPLICA`。防止此类事故的核心措施：**在 ZK 的数据目录挂独立的磁盘分区并设置容量告警**。

2. **废弃副本残留导致复制日志积压**：当某个物理节点被永久下线但未执行 `SYSTEM DROP REPLICA`，ZK 中的 `/log/` 路径会因 ClickHouse 一直等待该副本消费日志而停止清理——日志条目只增不删，ZNode 数量线性增长。每个月做一次副本健康检查：`SELECT * FROM system.replicas WHERE is_readonly OR total_replicas > 1 AND active_replicas < total_replicas`。

3. **ZK 版本不兼容导致集群启动失败**：ClickHouse 新版本可能要求更高版本的 ZooKeeper。升级 ClickHouse 前，先确认目标版本兼容的 ZK 版本范围——特别是从 23.x 升到 25.x 时，ZK 3.4.x（太老）已不再支持，需要至少 ZK 3.5.x 或 3.8.x。

4. **`ON CLUSTER` DDL 执行期间 ZK 故障**：`ON CLUSTER` 的语义是 "所有节点都成功才返回"。如果执行 `CREATE TABLE ON CLUSTER` 期间 ZK 抖动，DDL 在部分节点成功、部分节点失败——但 ZK 里可能已经写入了表元数据路径。后续重试会报 "Table already exists"，需要手动清理 ZK 残留路径后重新执行。

5. **clickhouse-keeper 迁移时忘了改 Snapshot 路径权限**：从 ZK 迁移到 clickhouse-keeper 后，`/var/lib/clickhouse/coordination/` 目录需要 `clickhouse:clickhouse` 用户写权限——如果使用 `root` 启动过一次 keeper，目录所有者变成 `root`，后续用 `clickhouse` 用户启动时会因权限不足反复 Crash Loop。

### 思考题

1. **ClickHouse 为什么选择依赖外部 ZK/keeper 做分布式协调，而不是像 MySQL Group Replication 或 MongoDB Replica Set 那样将共识协议（Paxos/Raft）直接嵌入存储引擎？** 请从以下角度分析：
   - 存储引擎的复杂度与稳定性
   - 协调层的故障隔离粒度
   - OLAP 场景下"写入高吞吐"和"强一致"的权衡
   - ClickHouse 的设计哲学



2. **如果 ZooKeeper 完全不可用且无法恢复（所有 3 个节点磁盘同时损坏），你的 ReplicatedMergeTree 表数据还在吗？你如何恢复集群的复制能力？** 请设计一套完整的灾后重建方案，包括：
   - 如何从剩余的 ClickHouse 节点本地磁盘恢复数据
   - 如何重建 ZK/keeper 中的元数据（提示：`SYSTEM RESTORE REPLICA` 不依赖原有 ZK 路径）
   - 业务恢复的优先级排序和预期 RTO（恢复时间目标）
   - 是否有可能不丢数据？

---

> **本章完**。至此，你已经理解了 ClickHouse 与 ZooKeeper/clickhouse-keeper 的完整协作机制——从 ZK 路径树到性能边界，从常见故障诊断到 keeper 迁移策略。下一章，我们将深入 Kafka 引擎与流式实时数据管道，探索 ClickHouse 如何在毫秒级延迟下接入海量事件流。
