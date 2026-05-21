# 第36章：ReplicatedMergeTree 复制协议源码剖析

> **版本**：ClickHouse 25.x LTS
> **定位**：高级篇核心章节——深入 ReplicatedMergeTree 源码，拆解复制协议的设计哲学、数据流路径与故障根因。
> **前置阅读**：第12章（副本与复制机制）、第19章（ReplicatedMergeTree与数据一致性）、第14章（监控与告警体系）
> **预计阅读**：50 分钟 | **实战耗时**：90 分钟

---

## 1. 项目背景

某金融数据平台的生产集群采用 **3分片×3副本** 的 ClickHouse 架构，承载每日近 **80亿条** 交易流水与风控日志的写入与查询。三周前，运维团队发现一个诡异现象：Shard-2 的 Replica-3 始终比另外两个副本慢 **5分钟以上**。值班同学小王按惯例 `SYSTEM RESTART REPLICA`，重启后延迟消失了——但只持续了30分钟。30分钟后，`system.replication_queue` 里的 `absolute_delay` 再次飙到 **320秒**，而且这一次重启也救不回来——重启后不到10分钟就开始堆积。

小王把怀疑对象列了个清单：
- 是不是 Replica-3 的机器网卡坏了，fetchPart 拉数据太慢？
- 是不是 ZooKeeper 那台机器磁盘有坏道，`/log/` 节点的读写延迟暴涨？
- 是不是这台副本的 Merge 线程被什么东西卡住了，导致队列里的 `MERGE_PARTS` 入口堆积？
- 是不是 insert_quorum=2 的配置导致写链路被最慢的副本拖死？

他把怀疑清单发到技术群里，得到的回复五花八门——有人说"直接 decommission 掉这台副本重建"，有人说"调大 `max_replicated_fetches_network_bandwidth` 试试"，也有人说"ZK 换 SSD 就好了"。这些方案都有道理，但 **没人能给出确定的根因**。原因很简单：团队对 ReplicatedMergeTree 的复制协议理解停留在"往 ZK 写日志、其他副本拉数据"的粗粒度认知上。一旦出问题，复制的每一步——日志写入、队列构建、Leader选举、Part抓取、Quorum确认——全是黑盒。

**核心痛点**：复制延迟的根因诊断，离不开对复制协议数据流和状态机的源码级理解。不知道 `ReplicatedMergeTreeLogEntry` 的 Type 枚举有哪些、不知道 `fetchPart` 的 HTTP 握手有没有重试机制、不知道 Leader 选举用的是 Sequential Ephemeral Node 还是 Raft——每一个盲区都意味着故障排查时只能靠猜。

这一章，我们直接翻开 ClickHouse 源码，把 ReplicatedMergeTree 的复制协议从 ZK 日志格式到 Part 抓取协议全部拆开来看。

---

## 2. 项目设计：剧本式交锋对话

周四下午，大师把小白和小胖叫到白板前，上面画满了 ZooKeeper 的树状结构。

**小胖**（靠在椅子上晃腿）："复制不就是主从同步吗？MySQL 都做了 20 年了，Source-Replica 架构，binlog 同步，有啥新鲜的？ClickHouse 能玩出什么花来？"

**大师**（敲了敲白板）："小胖，你说的 binlog 同步是 **Push 模式**——MySQL 的 Source 把 binlog 推给 Replica。ClickHouse 恰好相反——它是 **Pull 模式**，副本自己去拉。更关键的是，ClickHouse 根本不把自己当数据库。MySQL 的 Source 知道每个 Replica 的同步位点，ClickHouse 的副本之间互不认识——所有协调信息存在 ZooKeeper 里，每个副本自己去读 ZK，自己做决定。这从根本上决定了复制延迟的表现形式和排查手法完全不一样。"

**技术映射 #1**：MySQL 主从复制是中心化 Push——Source 持有 Replica 列表，主动推送 binlog，复制位点由 Source 管理。ClickHouse 是去中心化 Pull——副本之间完全对等，协调元数据存放在 ZooKeeper，每个副本独立从 ZK 获取日志，独立通过 HTTP 从其他副本拉取 Part。这种设计的好处是副本间无耦合，坏处是延迟诊断需要同时看 ZK、网络和磁盘三张维度。

---

**小白**（眼睛盯着白板上的 ZK 树结构，笔在纸上画着）："大师，如果用 ZooKeeper 做协调，那 ClickHouse 的复制协议和 Paxos、Raft 这些共识算法是什么关系？我看 ZK 本身用的是 ZAB 协议——那 ClickHouse 是直接复用 ZAB，还是在 ZK 之上又做了一层？"

**大师**："问得好。答案是：**都不是**。ClickHouse 的复制协议 **不是共识算法**。它既不保证线性一致性，也不做多数派投票（除非显式开启 insert_quorum）。核心思路分三步：第一，任意副本写入成功后往 ZooKeeper 的 `/log/` 下追加一条序列化日志条目；第二，其他副本的后台线程每隔一秒扫描 `/log/`，发现自己还没处理的条目就拷贝到自己的 `/queue/` 里；第三，队列调度器逐条执行——如果是 `GET_PART` 就去源副本 HTTP 拉 Part，如果是 `MERGE_PARTS` 就在本地执行 Merge。

这个模型里有两个关键取舍：一是 **ZK 只存元数据，不存数据**——真正几十 GB 的 Part 数据通过 HTTP 直连副本抓取，ZK 只保存"这个 Part 叫什么、在哪个副本上"。二是 **日志写入不需要多数派**——任何一个副本都可以直接写 `/log/`，不需要等 Leader 许可，不需要 collect quorum。这带来极高的写入吞吐，代价是副本间可能短暂不一致。"

**技术映射 #2**：ClickHouse 复制协议的定位是 **Shared-Log + Pull-based Data Sync**，与 Paxos/Raft 有本质区别。Shared-Log 指 `/log/` 是所有副本的共享操作日志；Pull-based 指数据由各副本自行抓取。这是一个 **最终一致性模型**，类似 Kafka 的消费者拉取消息——每个副本维护自己的消费位点（`max_processed_node`）。

---

**小胖**（突然凑近白板）："等一下——你说每个副本都能写 `/log/`，那如果有两个副本同时 INSERT 了同一个 Part（比如同一个分区、同一个数据块），ZooKeeper 会怎样？会不会出现两个 `/log/` 条目指向同一个 Part 名？"

**大师**："小胖问到点子上了——这就是 **Part 名称冲突** 的 deduplication 逻辑。ClickHouse 的 Part 名称带有严格的层级信息：`{partition_id}_{min_block}_{max_block}_{level}`。比如 `20250301_0_5_1` 表示分区 20250301，数据块从 0 到 5，Level 为 1。这个命名规则保证了 **同一个分区内，合法 Merge 产生的 Part 名是唯一确定的**。

但 INSERT 进来的 Part 不一样——多个副本可能同时插入相同的数据块（比如业务层做双写）。这时候两个 `/log/` 条目可能指向同一个 Part 名。ClickHouse 的处理方式是：在 `registerPart` 时检查 `/replicas/{replica}/parts/{part_name}` 是否已存在，如果存在则跳过。如果两个副本的 Part 名一样但内容不同（理论上不该发生），会在 fetchPart 的 checksum 校验阶段被拦下来——ClickHouse 在副本间传输 Part 时会带上 `checksums.txt`，接收方逐列校验 CRC32。"

**技术映射 #3**：Part 名冲突的 dedup 不依赖 ZK 分布式锁，而是依赖 Part 命名规则的幂等性 + checksum 校验作为兜底。这与 HBase 依赖 ZK 分布式锁做 Region 分配有本质不同——ClickHouse 的锁竞争只发生在 Leader 选举和 Merge 协调这两个低频场景。

---

**小白**（推了推眼镜）："大师，我通读了 `ReplicatedMergeTreeLogEntry.h`，里面有五种 Type：`GET_PART`、`MERGE_PARTS`、`MUTATE_PART`、`ATTACH_PART` 和 `REPLACE_RANGE`。这些类型各自在什么场景下触发？它们的执行顺序有依赖关系吗？"

**大师**（在白板上逐一标注）：

"一共六种核心类型，每种都有明确的触发条件：

- **`GET_PART`**：最常见。任何副本执行 `INSERT` 成功后都会生成一条。意思是'我有一个新 Part，别的副本来拉'。源副本名记录在 `source_replica` 字段。注意：**这个 Part 已经写在源副本的本地磁盘上了，GET_PART 只是通知其他副本来取**。

- **`MERGE_PARTS`**：由 **Leader 副本**创建。当 Leader 通过 `ZooKeeperRetries` 检测到某个分区的 Active Part 数量超过阈值，Leader 计算出一个 Merge 计划（比如把 `0_0_0`、`1_1_0`、`2_5_0` 合并成 `0_5_1`），然后往 `/log/` 写入一条 `MERGE_PARTS` 条目。所有副本（包括 Leader 自己）的队列里都会出现这条条目，各自在本地执行 Merge。**关键点**：Merge 是每个副本独立做的，不是 Leader 做好再分发——这避免了 Merge 结果的重复传输。

- **`MUTATE_PART`**：`ALTER TABLE ... UPDATE/DELETE` 时触发。与 Merge 类似，由 Leader 协调，各副本本地执行。它的队列优先级低于 `GET_PART`——因为我们希望先补齐缺失的 Part 再做变异。

- **`ATTACH_PART`**：`ALTER TABLE ... ATTACH PART` 或副本重建时使用。告诉其他副本'这个 Part 已经存在了，不需要 fetch'。通常在 `SYSTEM RESTART REPLICA` 后批量生成。

- **`DROP_RANGE`**：`ALTER TABLE ... DROP PARTITION` 触发。

- **`REPLACE_RANGE`**：`ALTER TABLE ... REPLACE PARTITION` 触发。

执行顺序方面，队列调度器按 **ZooKeeper 序列号严格递增** 执行，不做并发乱序。因为日志条目之间可能有依赖：你得先拿到 Part 0_0_0，才能和 1_1_0 一起 Merge 成 0_1_1。乱序执行会导致 Merge 失败。"

---

**小胖**（挠着头）："等等，Leader 选举又是怎么回事？不是说副本之间完全对等吗，怎么又冒出来一个 Leader？"

**大师**："Leader 的角色非常窄——**只负责决定何时 Merge 以及 Merge 哪些 Part**。它不负责写入路由（任何副本都能写），不负责读取（任何副本都能读），不负责日志分发（ZK 已经在做了）。

选举算法非常简单——就是 ZooKeeper 的 **Sequential Ephemeral Node**：所有候选副本在 `/leader_election/` 下创建一个 `lock-` 前缀的临时序号节点，序号最小的那个当选 Leader。当前 Leader 断开 ZK 连接时，它的 Ephemeral 节点自动消失，序号次小的副本自动递补。没有投票、没有心跳超时、没有 Term 号——比 Raft 简单一个数量级。

这个设计在工程上很聪明，因为 ClickHouse 的 Leader 挂了并不影响读写——只是 Merge 暂停，Parts 会暂时堆积。等新 Leader 选出来，它一扫描发现 Parts 多了，立刻开始写 `MERGE_PARTS` 日志，所有副本恢复 Merge。这与 HBase RegionServer 挂掉导致的 Region 不可用完全不是一个级别的事故。"

**技术映射 #4**：Leader 的角色边界清晰：仅协调 Merge，不路由读写。这是 ClickHouse 复制协议设计中最精妙的一步棋——用最轻量的方式解决了分布式 Merge 的冲突问题，同时把 Leader 宕机的影响降到最低。

---

**小白**（快速翻着笔记本）："还有最后一个问题——`insert_quorum`。我理解它是让 INSERT 在多个副本间达成一致性确认，那它的实现和 Raft 的 commit 有什么本质区别？如果设置 `insert_quorum=2` 但 `insert_quorum_timeout=60`，超时之后会发生什么？"

**大师**："`insert_quorum` 是 ClickHouse 对强一致性需求的妥协方案，但它 **不是 Raft commit**。区别有三：

第一，Raft 的 commit 是日志复制到多数派才返回成功——数据必须落盘在多数节点上。ClickHouse 的 insert_quorum 是：写入发起方（source replica）先本地写 Part，然后在 ZK 上创建 `/quorum/{part_name}/` 节点；其他副本拉取完成后在 `/quorum/{part_name}/replicas/{replica_name}` 下报告完成；发起方轮询计数，满足 quorum_size 才返回成功。

第二，Raft 的 commit 失败意味着写操作失败，必须重试。ClickHouse 的 insert_quorum_timeout 超时后**默认不抛异常**——`insert_quorum_timeout` 参数的全称暗示了它是'超时容忍'而非'强一致性保证'。如果超时但 `select_sequential_consistency=0`，写入会返回成功（数据已在本地落地），但其他副本可能还没同步完。

第三，Raft 的 commit index 是全局有序的，可以用它做线性读。ClickHouse 的 quorum 是基于 Part 粒度的确认，Part 之间的顺序由 ZK 日志序列号保证，但没有全局 commit index 的概念。"

**小胖**（长大了嘴）："这么说是 **假 Quorum** 咯？超时了数据也返回成功，那到底算成功了还是没成功？"

**大师**："工程上叫 **尽力而为的一致性**。如果你真的需要强一致性，你需要设置 `insert_quorum=3`（所有副本确认）+ `insert_quorum_timeout=0`（不超时）+ `select_sequential_consistency=1`（读取时检查副本的一致性状态）。但代价是写入延迟至少是网络 RTT 的三倍——任何副本慢都会拖死整条写链路。这就是为什么生产环境最常见的配置是 `insert_quorum=2` + `insert_quorum_timeout=300`——容忍一个副本故障，但不等太久。"

---

## 3. 项目实战

### 环境准备

需要一套至少 3 副本的 ClickHouse 集群。如果本地没有条件，使用 `clickhouse-local` 配合单机多端口模拟也行——本章实战的重点是理解代码流程而非真实网络拓扑。本地搭建建议使用 Docker Compose：

```yaml
# docker-compose.yaml (简化版仅用于学习)
version: '3'
services:
  zookeeper:
    image: zookeeper:3.8
    ports: ["2181:2181"]
  clickhouse1:
    image: clickhouse/clickhouse-server:25.3
    ports: ["8123:8123", "9000:9000"]
    volumes:
      - ./macros1.xml:/etc/clickhouse-server/config.d/macros.xml
  clickhouse2:
    image: clickhouse/clickhouse-server:25.3
    ports: ["8124:8123", "9001:9000"]
    volumes:
      - ./macros2.xml:/etc/clickhouse-server/config.d/macros.xml
  clickhouse3:
    image: clickhouse/clickhouse-server:25.3
    ports: ["8125:8123", "9002:9000"]
    volumes:
      - ./macros3.xml:/etc/clickhouse-server/config.d/macros.xml
```

macros 配置中指定不同的副本名但相同的分片号，使三台 ClickHouse 互为副本。

---

### 分步实现

#### Step 1：复制日志结构——ReplicatedMergeTreeLogEntry 的二进制格式

打开源码 `src/Storages/MergeTree/ReplicatedMergeTreeLogEntry.h`，LogEntry 的核心定义如下：

```cpp
// src/Storages/MergeTree/ReplicatedMergeTreeLogEntry.h

struct ReplicatedMergeTreeLogEntry
{
    enum Type
    {
        GET_PART,        // "从其他副本拉取这个 Part"
        MERGE_PARTS,     // "将这些 Part 合并成一个新 Part"
        MUTATE_PART,     // "对这个 Part 执行 ALTER TABLE 变异"
        ATTACH_PART,     // "这个 Part 已存在，直接注册"
        DROP_RANGE,      // "删除这个分区范围"
        REPLACE_RANGE,   // "替换这个分区范围"
    };

    Type type;
    String source_replica;       // 源副本名（GET_PART 用）
    String new_part_name;        // 新 Part 名，如 "20250301_0_1_0"
    Strings source_parts;        // 源 Part 列表（MERGE_PARTS 用）
    String new_part_type;
    String mutate_from_part;     // 变异来源（MUTATE_PART 用）
    UInt64 create_time;
    String quorum_info;          // insert_quorum 相关信息

    // 序列化为 ZooKeeper 存储的二进制格式
    void writeText(WriteBuffer & out) const;
    void readText(ReadBuffer & in);
    String toString() const;
    static ReplicatedMergeTreeLogEntry parse(const String & s, const Coordination::Stat & stat);
};

// 序列化示例——写入 ZK 时不是 JSON，而是自定义紧凑格式
// 格式: "format version: 4\n" + type + "\n" + source_replica + "\n" + ...
void ReplicatedMergeTreeLogEntry::writeText(WriteBuffer & out) const
{
    writeIntText(FORMAT_VERSION, out);
    writeChar('\n', out);
    writeIntText(static_cast<Int8>(type), out);
    writeChar('\n', out);
    writeString(source_replica, out);
    writeChar('\n', out);
    writeString(new_part_name, out);
    writeChar('\n', out);
    writeVector(source_parts, out);
    writeChar('\n', out);
    writeIntText(create_time, out);
    writeChar('\n', out);
    // ... 更多字段
}
```

在 ZooKeeper 中，每一条 LogEntry 对应 `/clickhouse/tables/{shard}/{table}/log/log-{0000000000}` 这样一个顺序 ZNode。它的 ZNode 名称中的序号是全局单调递增的，这保证了日志的全序关系。ZNode 的 content 就是上面的二进制序列化内容，体积通常只有几百字节——**ZooKeeper 只存元数据，不存 Part 数据**。

```
ZooKeeper 树结构 (以单表为例):
/
└── clickhouse/
    └── tables/
        └── 01/                              ← shard 号
            └── transactions_log/             ← 表名
                ├── metadata                  ← 表 DDL 定义
                ├── columns                   ← 列定义
                ├── log/
                │   ├── log-0000000000        ← "Merge parts A into B"
                │   ├── log-0000000001        ← "Get part C from replica_1"
                │   ├── log-0000000002        ← "Drop partition 2025-01"
                │   └── ...
                ├── leader_election/
                │   ├── lock-0000000005        ← Ephemeral Sequential (Leader)
                │   ├── lock-0000000006        ← Ephemeral Sequential
                │   └── lock-0000000007        ← Ephemeral Sequential
                ├── quorum/
                │   └── 20250301_0_0_0/
                │       └── replicas/
                │           ├── replica_1
                │           └── replica_2
                ├── block_numbers/             ← 区块编号分配
                └── replicas/
                    ├── replica_1/
                    │   ├── host               ← IP:port
                    │   ├── is_active           ← Ephemeral (Heartbeat)
                    │   ├── max_processed_node  ← 已处理到的 log 序号
                    │   ├── queue/              ← 该副本待处理的 log 条目
                    │   │   ├── log-0000000001
                    │   │   └── log-0000000003
                    │   └── parts/              ← 该副本拥有的 Part 清单
                    │       ├── 20250301_0_0_0
                    │       └── 20250301_1_3_1
                    └── replica_2/
                        └── ...
```

---

#### Step 2：复制流程——从 INSERT 到全副本可见的完整路径

以下时序图展示了单个 INSERT 语句在 3 副本集群中的完整数据流：

```cpp
// src/Storages/StorageReplicatedMergeTree.cpp
// 路径 1: 源副本 (Replica-1) 处理 INSERT

void StorageReplicatedMergeTree::write(
    const ASTInsertQuery & /*query*/,
    const Block & block,
    const ContextPtr & local_context,
    bool /*is_async*/)
{
    // Step 1: 分配区块号——从 ZK 的 /block_numbers/ 取得唯一递增序号
    String block_id;
    if (insert_deduplication_token)
        block_id = getBlockID(block, insert_deduplication_token);
    // Step 2: 写 Part 到本地磁盘——这是 MergeTree 的标准写入路径
    //         Compression、Checksum、Index 全部在本地完成
    auto part = writer.writeTempPart(block, metadata_snapshot, context);
    writer.renameTempPartAndAdd(part, &deduplication_block);
    // Step 3: 序列化 LogEntry
    auto entry = std::make_shared<ReplicatedMergeTreeLogEntry>();
    entry->type = ReplicatedMergeTreeLogEntry::GET_PART;
    entry->source_replica = replica_name;
    entry->new_part_name = part->name;
    entry->create_time = time(nullptr);
    // Step 4: 写入 ZooKeeper /log/ 顺序节点
    String log_path = zookeeper_path + "/log/log-";
    String node_name = zk->create(log_path, entry->toString(),
        zkutil::CreateMode::PersistentSequential);
    // Step 5: 更新自己的 parts 列表
    zk->createIfNotExists(
        replica_path + "/parts/" + part->name, "");
    // Step 6: 如果是 insert_quorum 模式，创建 quorum 追踪节点
    if (insert_quorum > 1)
        writeQuorumPart(part, insert_quorum, insert_quorum_timeout);
}

// 路径 2: 目标副本 (Replica-2, Replica-3) 后台拉取

void StorageReplicatedMergeTree::queueUpdatingTask()
{
    // 这个函数由一个后台线程每 1000ms 调用一次
    while (!shutdown)
    {
        // Step A: 扫描 /log/ 目录，找出新条目
        Strings log_entries = zk->getChildren(zookeeper_path + "/log");
        for (const auto & entry_name : log_entries)
        {
            Int64 entry_num = parseIndex(entry_name); // "log-0000000123" → 123
            if (entry_num <= last_processed_log_index)
                continue;  // 已处理过，跳过
            // Step B: 把新条目拷贝到自己的 /queue/ 下
            String queue_path = replica_path + "/queue/" + entry_name;
            if (!zk->exists(queue_path))
            {
                auto entry = ReplicatedMergeTreeLogEntry::parse(
                    zk->get(zookeeper_path + "/log/" + entry_name),
                    stat);
                zk->create(queue_path, entry->toString(),
                    zkutil::CreateMode::Persistent);
            }
        }

        // Step C: 按顺序处理队列中的条目
        Strings queue_entries = zk->getChildren(replica_path + "/queue");
        std::sort(queue_entries.begin(), queue_entries.end());
        for (const auto & qe : queue_entries)
        {
            auto entry = ReplicatedMergeTreeLogEntry::parse(
                zk->get(replica_path + "/queue/" + qe), stat);
            if (entry->type == ReplicatedMergeTreeLogEntry::GET_PART)
            {
                // 核心：HTTP 直连源副本拉取 Part 数据
                fetchPart(entry->source_replica, entry->new_part_name);
            }
            else if (entry->type == ReplicatedMergeTreeLogEntry::MERGE_PARTS)
            {
                // 本地执行 Merge——不依赖网络
                mergeParts(entry->source_parts, entry->new_part_name);
            }
            // 处理完后删除队列条目，更新 max_processed_node
            zk->remove(replica_path + "/queue/" + qe);
            zk->set(replica_path + "/max_processed_node", qe);
        }

        sleepForMilliseconds(1000);
    }
}
```

**关键时序约束**：队列处理是单线程、串行的。这意味着如果一个 `GET_PART` 拉取 10GB 的数据花了 60 秒，后面所有的 `MERGE_PARTS` 都要等这 60 秒——这就是**单线程队列导致的全队延迟雪崩**。

---

#### Step 3：Leader 选举——Sequential Ephemeral Node 的极简实现

```cpp
// Leader 选举的核心逻辑（简化版）
// 源文件: src/Storages/StorageReplicatedMergeTree.cpp

void StorageReplicatedMergeTree::leaderElection()
{
    auto zk = getZooKeeper();
    String election_path = zookeeper_path + "/leader_election/";

    // 1. 创建临时序号节点
    String my_node_name = zk->create(
        election_path + "lock-",
        "",  // 节点内容为空——只靠序号本身
        zkutil::CreateMode::EphemeralSequential
    );
    // 实际节点名例如: lock-0000000005

    // 2. 获取所有候选节点
    Strings children = zk->getChildren(election_path);
    std::sort(children.begin(), children.end());

    // 3. 判断自己是否是序号最小的
    if (children.front() == my_node_name)
    {
        is_leader = true;
        LOG_INFO(log, "{}: I am the leader now", replica_name);
        // Leader 职责启动: 开始调度 Merge
        startBeingLeader();
    }
    else
    {
        is_leader = false;
        // 4. Watch 排在我前面的那个节点
        auto it = std::find(children.begin(), children.end(), my_node_name);
        String watch_node = *(it - 1);
        // 当前面的节点消失（对应副本崩溃/断连），Watch 触发，重新选举
        zk->exists(election_path + "/" + watch_node,
            [this](...) { leaderElection(); });  // 回调重新选举
    }
}

// Leader 的职责: 发现需要 Merge 的 Part 并写入 MERGE_PARTS 日志
void StorageReplicatedMergeTree::startBeingLeader()
{
    // 定期扫描每个分区的 Part 数量和状态
    // 当 Active Parts > min_parts_to_merge 时，选择最优 Merge 策略
    // 将 MERGE_PARTS 写入 /log/——所有副本的队列都获得这个任务
    // 关键: Merge 是各副本独立完成的，Leader 只负责决策
}
```

**与 Raft 的关键差异**：
- Raft 的 Leader 选举需要收集多数派投票 > N/2，ClickHouse 不需要——最小序号直接当选
- Raft 的 Leader 要处理所有写请求的路由，ClickHouse 的 Leader 只管 Merge
- Raft 的 Leader 挂了影响服务（进入选举期不服务），ClickHouse 的 Leader 挂了只影响 Merge 进度

---

#### Step 4：Quorum Write 实现——超时容忍 vs 强一致性的博弈

```cpp
// insert_quorum 的完整实现路径
// 源文件: src/Storages/StorageReplicatedMergeTree.cpp

void StorageReplicatedMergeTree::writeQuorumPart(
    const MergeTreeDataPartPtr & part,
    size_t quorum_size,
    size_t quorum_timeout_ms)
{
    auto zk = getZooKeeper();

    // 1. 创建 quorum 跟踪节点
    String quorum_path = zookeeper_path + "/quorum/" + part->name;
    zk->createAncestors(quorum_path);
    zk->create(quorum_path, toString(quorum_size),
        zkutil::CreateMode::Persistent);

    // 2. 轮询等待足够多的副本确认
    auto deadline = std::chrono::steady_clock::now()
        + std::chrono::milliseconds(quorum_timeout_ms);

    while (std::chrono::steady_clock::now() < deadline)
    {
        // 检查有多少副本已完成拉取并在 ZK 上注册了该 Part
        Strings replicas_with_part = zk->getChildren(
            quorum_path + "/replicas");
        if (replicas_with_part.size() >= quorum_size)
        {
            // Quorum 达成，写入确认节点
            zk->create(quorum_path + "/status", "ok",
                zkutil::CreateMode::Persistent);
            return;  // 成功返回
        }

        sleepForMilliseconds(100);  // 休眠 100ms 再检查
    }

    // 3. 超时处理——关键决策点
    if (quorum_timeout_ms == 0)
    {
        // 超时时间为 0 = 永不超时，物理上会一直等
        // 这会导致写链路被阻塞直到副本全部恢复
    }
    else
    {
        // 默认行为: 写入 fail 状态但 SELECT 不阻塞
        zk->create(quorum_path + "/status", "fail",
            zkutil::CreateMode::Persistent);

        // ⚠️ 注意: 函数不抛异常！调用者（write()）会正常返回
        // 数据在本地已落地，只是没满足 quorum
        // 查询侧通过 select_sequential_consistency 配置决定是否检查 quorum 状态
    }
}
```

**生产调优建议**：

```sql
-- 监控 Quorum 健康度
SELECT
    database,
    table,
    count() AS total_quorum_entries,
    countIf(status = 'fail') AS failed_quorums,
    round(failed_quorums / total_quorum_entries * 100, 2) AS fail_rate_pct
FROM (
    -- 通过 ZK 直查或 system.replication_queue 间接推断
    SELECT
        database, table,
        last_exception,
        multiIf(
            last_exception = '', 'ok',
            last_exception LIKE '%quorum_timeout%', 'fail',
            'other'
        ) AS status
    FROM system.replication_queue
    WHERE num_postponed > 0
)
GROUP BY database, table
HAVING fail_rate_pct > 5
ORDER BY fail_rate_pct DESC;
```

---

#### Step 5：Part 同步——fetchPart 的 HTTP Pull 协议

```cpp
// HTTP 拉取 Part 的完整流程
// 源文件: src/Storages/StorageReplicatedMergeTree.cpp

void StorageReplicatedMergeTree::fetchPart(
    const String & source_replica_zk_name,
    const String & part_name,
    bool to_detached)
{
    // 1. 从 ZK 解析源副本的物理地址
    String source_replica_path = zookeeper_path
        + "/replicas/" + source_replica_zk_name;
    String source_host = zk->get(source_replica_path + "/host");
    UInt16 source_port = parse<UInt16>(
        zk->get(source_replica_path + "/port"));

    // 2. 建立 HTTP 连接（不走 ClickHouse 的 TCP 协议）
    //    端点: /?endpoint=DataPartsExchange
    String uri = "/?endpoint=DataPartsExchange%3A%2Fclickhouse%2Ftables%2F"
        + escape(table_id) + "%2F" + replica_name
        + "&part=" + part_name;

    // 3. 流式接收 Part 数据
    //    接收端逐文件写入——columns.txt, checksums.txt, data.bin, mark.mrk...
    auto connection = std::make_unique<HTTPConnection>(
        source_host, source_port);
    connection->sendRequest(uri);

    // 4. 校验 checksum
    //    关键: 如果 checksum 不匹配，丢弃已下载文件，记录错误
    auto received_checksums = readChecksums(download_path);
    if (!received_checksums.verify(expected_checksums))
    {
        LOG_ERROR(log, "Checksum mismatch for part {} from {}",
            part_name, source_host);
        // 清理临时文件，等待下次重试
        removeRecursive(download_path);
        throw Exception(ErrorCodes::CHECKSUM_DOESNT_MATCH,
            "Checksums for part {} don't match", part_name);
    }

    // 5. Rename 临时目录到正式 Part 目录（原子操作）
    renameFile(download_path,
        data.getFullPath() + "detached/" + part_name);

    // 6. 在 ZK 注册该 Part 所有权
    zk->createIfNotExists(replica_path + "/parts/" + part_name, "");

    LOG_TRACE(log, "Fetched part {} from {} ({} bytes, {} seconds)",
        part_name, source_host, total_bytes, elapsed_seconds);
}
```

**Part 传输的性能瓶颈**：
- **单线程**：一次只 fetch 一个 Part，`max_fetch_partition_retries_count` 只控制重试次数，不管并发
- **带宽限制**：可以通过 `max_replicated_fetches_network_bandwidth` 限制（单位 bytes/s），默认为 0（不限制）
- **HTTP 开销**：每个 Part 一次 HTTP 连接，小 Part 多时连接开销显著

```sql
-- 在生产环境开启 TRACE 日志来诊断 fetchPart 慢的原因
-- 在 config.xml 中配置:
-- <logger>
--   <levels>
--     <ReplicatedMergeTreeQueue>trace</ReplicatedMergeTreeQueue>
--   </levels>
-- </logger>

-- 此时日志中会出现类似输出:
-- <Debug> StorageReplicatedMergeTree: fetchPart:
--   downloading 2457600000 bytes from replica-2:9009 for part 20250301_100_200_2
-- <Trace> StorageReplicatedMergeTree: fetchPart progress:
--   30% done, speed: 85 MB/s, ETA: 19s
```

---

#### Step 6：诊断复制延迟——从系统表到源代码的全链路排查

```sql
-- ============================================
-- Step 6.1: 宏观定位——哪些副本在落后？
-- ============================================
SELECT
    database,
    table,
    replica_name,
    absolute_delay,        -- 副本落后主副本的秒数
    queue_size,             -- 该副本队列中待处理条目数
    inserts_in_queue,       -- 队列中 GET_PART 条目数
    merges_in_queue,        -- 队列中 MERGE_PARTS 条目数
    log_max_index,          -- /log/ 中最新的日志序号
    log_pointer             -- 该副本已经处理到哪个日志序号
FROM system.replication_queue
WHERE absolute_delay > 10   -- 落后超过 10 秒的副本
ORDER BY absolute_delay DESC
FORMAT PrettyCompactMonoBlock;

-- 示例输出:
-- ┌─database─┬─table───┬─replica_name─┬─absolute_delay─┬─queue_size─┬─inserts_in_queue─┬─merges_in_queue─┬─log_max_index─┬─log_pointer─┐
-- │ risk_db  │ tx_log  │ replica-3    │           320  │       1247 │            1245   │              2  │        56423  │       55176 │
-- └──────────┴─────────┴──────────────┴────────────────┴────────────┴──────────────────┴─────────────────┴───────────────┴─────────────┘

-- 解读: replica-3 落后 320 秒，队列 1247 条，其中 1245 条是 GET_PART（待拉取）
-- 根因极可能是: fetchPart 太慢（网络带宽不足或源副本负载高）


-- ============================================
-- Step 6.2: 深入诊断——队列堆积的根因分类
-- ============================================
SELECT
    database, table, replica_name,
    queue_size,
    inserts_in_queue,
    merges_in_queue,
    -- 计算队列类型比例，判断瓶颈类别
    round(inserts_in_queue * 100.0 / greatest(queue_size, 1), 1) AS insert_pct,
    round(merges_in_queue * 100.0 / greatest(queue_size, 1), 1) AS merge_pct,
    -- 异常信息采样
    any(last_exception) AS sample_exception
FROM system.replication_queue
WHERE absolute_delay > 10
GROUP BY database, table, replica_name, queue_size,
         inserts_in_queue, merges_in_queue
ORDER BY absolute_delay DESC;

-- 诊断矩阵:
-- insert_pct > 90% → 网络带宽瓶颈，应检查 max_replicated_fetches_network_bandwidth
-- merge_pct  > 50% → Merge 速度不够，应检查 background_pool_size 和磁盘 IOPS
-- queue_size  > 1000 → ZK 通信延迟高，应检查 ZK 的 txn latency


-- ============================================
-- Step 6.3: 确认 ZK 侧的健康状态
-- ============================================
SELECT
    name,
    value,
    description
FROM system.zookeeper
WHERE path = '/' AND name IN ('zk_avg_latency', 'zk_max_latency', 'zk_outstanding_requests');

-- 如果 zk_avg_latency > 50ms: ZK 磁盘可能是 HDD，升级到 SSD
-- 如果 zk_outstanding_requests > 100: ZK 请求堆积，可能需要扩展 ZK 集群


-- ============================================
-- Step 6.4: 手动触发队列重试（紧急止血）
-- ============================================
-- 如果确认是偶发网络问题导致部分 Part 拉取失败:
SYSTEM RESTART REPLICA risk_db.tx_log;

-- 如果怀疑是特定 Part 的 checksum 损坏导致队列卡死:
ALTER TABLE risk_db.tx_log DETACH PARTITION '20250301';
ALTER TABLE risk_db.tx_log ATTACH PARTITION '20250301';
-- ⚠️ DETACH + ATTACH 会触发重新 fetch 整个分区的 Part，操作重量


-- ============================================
-- Step 6.5: 源码级埋点——在 fetchPart 中添加自定义诊断日志
-- ============================================
-- 如果你的团队维护了自己的 ClickHouse 分支，可以在 fetchPart 中加入:
LOG_INFO(log, "fetchPart [{}] replica={} source_host={} port={} part_size={}",
    part_name, source_replica_zk_name,
    source_host, source_port, content_length);
-- 这样在延迟复现时，可以关联 ZK 日志序号 → fetchPart 日志时间 → 网络监控，
-- 形成完整的问题证据链。
```

---

### 测试验证

**测试 1：模拟副本故障与恢复**

```sql
-- 在 replica-1 上插入大量数据
INSERT INTO risk_db.tx_log
SELECT now() - number * 10, rand() % 1000000, rand()
FROM numbers(5000000);

-- 立即查看 replica-2 的队列深度
SELECT queue_size, absolute_delay, inserts_in_queue
FROM system.replication_queue
WHERE replica_name = 'replica-2' AND table = 'tx_log';

-- 预期: queue_size > 0，absolute_delay 开始增长
--       但 inserts_in_queue 应该 ≈ queue_size (说明延迟来自 fetch 速度)

-- 模拟网络丢包: 在 replica-2 上用 iptables 限制带宽
-- sudo tc qdisc add dev eth0 root tbf rate 1mbit burst 32kbit latency 400ms

-- 再次查看: absolute_delay 应该显著增长
```

**测试 2：验证 Leader 选举**

```sql
-- 查看当前 Leader
SELECT replica_name, is_leader
FROM system.replicas
WHERE table = 'tx_log';

-- 手动断开 Leader 的 ZK 连接（在 Leader 节点上）
-- SYSTEM DROP REPLICA 'replica-1' FROM ZKPATH '/clickhouse/tables/01/tx_log'

-- 3 秒后查看新的 Leader
SELECT replica_name, is_leader FROM system.replicas WHERE table = 'tx_log';
-- 预期: 另一个副本自动成为 Leader

-- 恢复: SYSTEM RESTART REPLICA tx_log;
```

**测试 3：insert_quorum 行为验证**

```sql
-- 创建测试表，设置 quorum=2
CREATE TABLE quorum_test (
    event_time DateTime,
    user_id UInt64,
    event_type String
) ENGINE = ReplicatedMergeTree('/clickhouse/tables/{shard}/quorum_test', '{replica}')
PARTITION BY toYYYYMMDD(event_time)
ORDER BY (event_time, user_id)
SETTINGS insert_quorum = 2, insert_quorum_timeout = 10000;

-- 写入数据
INSERT INTO quorum_test VALUES (now(), 12345, 'login');

-- 在另一个会话中查看 ZK quorum 状态
-- 可以直接用 clickhouse-client 查询 ZK:
-- SELECT * FROM system.zookeeper WHERE path = '/clickhouse/tables/01/quorum_test/quorum/';

-- 模拟一个副本宕机后插入
-- INSERT INTO quorum_test VALUES (now(), 67890, 'logout');
-- 预期: 10 秒后返回成功（因为 quorum=2 被当前副本 + 另一个健康副本满足）
```

---

## 4. 项目总结

### ClickHouse 复制协议 vs Paxos/Raft 对比表

| 维度 | ClickHouse Replication | Raft (对比) |
|------|----------------------|-------------|
| **协调服务** | ZooKeeper (外部依赖) | 内嵌于节点进程 |
| **日志存储** | ZK Sequential ZNode (元数据) | 本地磁盘 Write-Ahead Log |
| **日志内容** | 仅 Part 名与操作类型 (~500B) | 完整数据变更记录 |
| **数据同步方向** | Pull (HTTP GET) | Push (RPC AppendEntries) |
| **写入路由** | 任意副本直接写 | 必须经过 Leader |
| **Leader 角色** | 仅协调 Merge | 所有写操作路由 + 日志复制 |
| **一致性模型** | 最终一致性 + Quorum 可选 | 强一致性 (Linearizable) |
| **选举算法** | Sequential Ephemeral Node | Randomized Timeout + Majority Vote |
| **写入开销** | 低 (ZK 仅写元数据) | 高 (完整日志需复制到多数派) |
| **副本间通信** | HTTP (Part Exchange) | RPC (AppendEntries心跳) |
| **故障影响** | Leader 宕机仅暂停 Merge | Leader 宕机期间不可写 |

### ClickHouse 复制协议的核心设计哲学

ClickHouse 的复制协议设计遵循三个原则，理解它们才能真正掌握延迟诊断的钥匙：

1. **ZooKeeper 是元数据中心，不是数据通道**。ZK 上保存的 LogEntry 只有几百字节——Part 名、操作类型、副本名。真正的数据传输走 HTTP 直连。这意味着复制延迟的根因要么在 ZK 通信（日志分发慢），要么在 HTTP 传输（数据拉取慢），两者诊断路径完全不同。

2. **Pull over Push**。每个副本独立从 ZK 拉取日志，独立从源副本拉取 Part。源副本不需要知道有多少个目标副本，也不需要维护复杂的同步位点。这种设计牺牲了强一致性，换来了极高的写入吞吐和副本间解耦。

3. **Leader 的职责最小化**。Leader 只协调 Merge，不影响读写。这与 HBase/HDFS 的 NameNode、Kafka 的 Controller、Raft 的 Leader 形成鲜明对比。在 ClickHouse 中，Leader 选举失败只意味着 Parts 暂时堆积，读写完全不受影响。

### 常见踩坑经验

1. **网络分区导致 ZK 脑裂**：两个数据中心的 ClickHouse 节点同时认为自己是 Leader，都开始写 `MERGE_PARTS` 条目。虽然 ZooKeeper 本身的 ZAB 协议保证了 `/log/` 的线性一致性，但如果使用了 `insert_quorum` 跨 AZ 部署，网络分区的恢复会触发大量的 Part 重新 fetch。解法：将 ZK 部署在独立的高可用区域，并在 `max_replicated_fetches_network_bandwidth` 上做合理限速。

2. **Part 数量超过 10 万导致复制队列雪崩**：`/log/` 和 `/replicas/{name}/queue/` 的 ZNode 数量过大时，`getChildren()` 调用本身就成为瓶颈——一次全量列出 10 万个 ZNode 可能耗时 > 5 秒，而 `queueUpdatingTask` 每秒调用一次，形成恶性循环。解法：合理设置 `parts_to_delay_insert` 和 `max_parts_in_total`，必要时用 `SYSTEM START MERGES` 配合 `OPTIMIZE` 直接吞并小 Part。

3. **insert_quorum_timeout 的沉默失败**：`insert_quorum=2` 但超时后不抛异常，数据只在源副本落地。如果此时源副本宕机且数据未同步到其他副本，数据就丢失了——但写入端已经收到了成功返回。解法：监控 `system.replication_queue.last_exception` 中包含 `quorum_timeout` 的记录，或者干脆使用 `insert_quorum_timeout=0` 配合严格的 SLA 重试。

4. **fetchPart 的单线程瓶颈在极端场景下放大**：假设 1000 个 1MB 的小 Part 在队列中等待 fetch，每个 HTTP 连接的建立 + 传输 + checksum 校验耗时约 200ms（跨 AZ 延迟），总耗时 = 1000 × 0.2s = 200 秒。而如果是 1 个 1GB 的大 Part，同样耗时 100 秒。小 Part 过多时连接开销是主要瓶颈。解法：调大 `min_bytes_for_wide_part` 和在业务层做 Micro-Batch 写入。

### 思考题

1. **为什么 ClickHouse 选择 Pull 模式（副本拉取）而不是 Push 模式（主推）来做数据同步？** 提示：考虑写入吞吐、副本故障隔离、网络拓扑多样性三个维度。

2. **如果 3 副本中 2 个宕机，剩下的 1 个能写入吗？能查询吗？取决于什么配置？** 提示：考虑 `insert_quorum` 参数和 ZK 连接状态。

3. **Leader 选举使用 Sequential Ephemeral Node 而非 Raft 的原因是什么？如果 ClickHouse 要实现跨数据中心的 Geo-Replication，目前的 Leader 选举方案够用吗？**

---

> **本章完。第 37 章将覆盖 ReplicatedMergeTree 的 Merge 调度器源码——后台线程如何决定 '何时合并、合并哪些 Part、用几路合并'。**
