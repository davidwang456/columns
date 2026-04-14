# 第 4 章：副本与可用性入门——Replication Factor 与 min.insync.replicas

> 级别：基础  
> 预计阅读：约 3000～5000 字（正文以汉字为主，含标点）  
> 配套示例：`examples/src/main/java/org/example/column/ch04/ReplicationBasicsDemo.java`

---

## 1. 项目背景

第 3 章已经澄清：**分区（Partition）**负责并行与伸缩；但在评审会上仍常听到另一种混淆——「我们副本数设成 3，是不是吞吐能翻三倍？」这类问题背后，是把 **Replication Factor（副本因子，RF）**误当成了「多开几条并行管道」。事实上，**副本解决的是同一条分区日志在集群中的冗余拷贝与故障切换**，它服务的是**可用性、持久化与读路径（如 Follower 同步）**，而不是消费者侧的并行度。消费者组的并行度上限，仍然主要由**分区数**决定。

真实项目里，与副本相关的痛点往往表现为：**（1）** 单节点开发环境随意设 RF=3，Topic 创建失败却不知道为什么；**（2）** 生产集群 RF=3，但 `acks=1` 或 `min.insync.replicas` 配置与预期不一致，出现「以为写了三份、其实只等了一份确认」的认知偏差；**（3）** 监控里出现 `UnderReplicatedPartitions`、ISR 频繁抖动，开发与运维对 **Leader、Replicas、ISR** 没有共同语言。

本章产出物包括：**（1）** 能解释 RF 与分区数的区别，以及单 Broker 环境下 RF 为何只能为 1；**（2）** 能读懂 `Describe` 输出中的 Leader / Replicas / ISR；**（3）** 建立 **`min.insync.replicas` 与生产者 `acks=all` 的配对直觉**（精确行为在第 14 章展开），并知道在单副本 Topic 上该配置只能为 1。

从机制上看，每个分区在某一时刻有一个 **Leader** 负责读写请求，其余副本为 **Follower** 异步拉取复制。**ISR（In-Sync Replicas）**是与 Leader 保持同步的副本集合；Follower 落后过多会被移出 ISR，进而影响可用写入条件（当使用 `acks=all` 且配合 `min.insync.replicas` 时）。这些名词不必一次背完，但要在脑子里画一张「**一条分区日志 → 多副本 → ISR 是子集**」的图。

再补一层与**容量规划**的关系：**RF 越大**，同一条消息在集群内占用的存储与复制流量通常**近似按 RF 线性放大**（具体还与压缩、保留策略有关）。因此「为了安全把 RF 提到很高」在成本与网络带宽上都要付出代价；更稳妥的做法是：**先明确 RPO/RTO**，再选 RF、`min.insync.replicas`、跨机架副本放置与监控告警，而不是单一维度堆副本。开发在单机上无法复制多副本行为时，至少应通过文档与预发环境补齐「见过 ISR 抖动」的经验，避免线上第一次见告警才补课。

最后补一句与**测试**的衔接：自动化用例里若「硬编码 RF=3」，在开发者笔记本的单节点环境会直接失败；推荐把 **RF 写成环境变量**或由脚本读取 **当前集群 Broker 数**，让同一套测试在 CI（多容器集群）与本地（单节点）都能跑通**不同分支断言**。这与本章「先失败、再理解」的教学思路一致。这样可减少无效缺陷单与重复沟通。

---

## 2. 项目设计：大师与小白的对话

**小白**：线上要容灾，我把副本因子设成 3，分区也设成 3，这样又安全又快，对吧？

**大师**：前半句「RF=3」通常意味着**每个分区各有三份拷贝**，面向磁盘与机架故障；后半句「分区也设成 3」是**并行分片**，两件事可以叠加，但含义不同。类比：分区是「把账本切成多册并行处理」；副本是「每一册都复印多份防丢」。**复印册数不会自动增加「并行处理的册数」**——并行仍看分册数。

**小白**：那 Follower 能不能分担消费？我让消费者连到 Follower 读，是不是就负载均衡了？

**大师**：在经典 Kafka 使用方式里，**消费者读的是 Leader**（常规 `assign`/`subscribe` 语义下），不是想读哪副本就读哪副本。副本主要用于持久化与 Leader 故障时的选举切换，不是「多读点」的扩展手段。扩展消费并行仍然要**加分区**或**加消费者实例**（受分区数约束）。

**小白**：`min.insync.replicas` 我设成 2，是不是比 1 更保险？

**大师**：在 **RF≥3** 的集群里，常见搭配是 `min.insync.replicas=2`：配合 `acks=all`，要求至少 2 个 ISR 副本确认，**耐久性**更强；但当 ISR 因故障收缩到只剩 1 个时，写入可能被拒绝，**可用性**换耐久。反过来若 **RF=1**（单 Broker 开发环境只能如此），`min.insync.replicas` **只能为 1**，否则与物理副本数矛盾——这是 broker 侧约束，不是「想写 2 就能写 2」。

**小白**：`UnderReplicatedPartitions` 告警一响，我是不是要立刻加机器？

**大师**：先**定位分区**：是**单 Broker 故障**、**网络抖动**、还是**磁盘慢**导致复制跟不上？加机器未必解决**热点分区**或**单盘故障**。运维上通常先看 **ISR 是否恢复**、**Leader 是否切换成功**、**复制是否节流**；开发侧则确认**突发流量**是否让 Follower 长期落后。把「副本不足」当成**症状**而不是**唯一病因**。

**收束**：本章验收标准：**（1）** 在单 Broker 上尝试创建 RF=2 的 Topic，能识别失败原因；**（2）** 对 RF=1 的 Topic 执行 `Describe`，能读出各分区的 Leader、Replicas、ISR；**（3）** 能口述 `min.insync.replicas` 与多副本、ISR 的关系，并知道它与第 14 章「交付语义」强相关。

三个「看起来对但不对」的推论：**推论 A**，「RF 越大，消费越快」——消费并行主要看**分区数**；**推论 B**，「ISR 永远等于 Replicas」——落后副本会离开 ISR；**推论 C**，「开发环境 RF=3 与线上一致最好」——Broker 数不够时创建会失败，应用先理解约束再谈一致。

---

## 3. 项目实战

### 3.1 环境说明

与第 3 章相同，使用 [examples/docker-compose.yml](../examples/docker-compose.yml) 的 **单节点 KRaft** Broker（`localhost:9092`）。该环境 **仅 1 个 Broker**，因此**仅允许创建 RF=1** 的 Topic；这正是本章要利用的「可失败实验」：强行创建 RF=2 会触发 **`InvalidReplicationFactorException`**（或等价错误信息），从错误反推副本与 Broker 数量的关系。

### 3.2 主代码片段：RF 失败、Describe 与 min.insync.replicas

示例类：`org.example.column.ch04.ReplicationBasicsDemo`。流程分三步：**（1）** 调用 `createTopics` 创建 `column.ch04.try-rf2`（RF=2），捕获**预期失败**；**（2）** 创建 `column.ch04.replication`（3 分区、RF=1），并对该 Topic 调用 `describeTopics`，打印每个 `TopicPartitionInfo` 的 `leader`、`replicas`、`isr`；**（3）** `describeConfigs` 读取 Topic 配置中与耐久相关的项（如 `min.insync.replicas`），并在控制台打印说明。

```java
// 节选：单 Broker 上 RF=2 预期失败
admin.createTopics(List.of(new NewTopic(TOPIC_TRY_RF2, 1, (short) 2))).all().get();

// 节选：Describe 各分区 Leader / Replicas / ISR
TopicDescription td = admin.describeTopics(Collections.singleton(TOPIC_OK))
    .topicNameValues().get(TOPIC_OK).get();
td.partitions().forEach(tp -> { /* leader, replicas, isr */ });
```

完整代码见仓库。**阅读点**：`replicas` 列出该分区所有副本所在 Broker；`isr` 是同步子集；单节点下二者通常**只含同一 broker id**，与多副本集群的 `ISR` 动态变化形成对比。

**验证步骤**：

1. 启动 Broker：`cd kafka-column/examples && docker compose up -d`。
2. 运行：`mvn -q compile exec:java -Dexec.mainClass=org.example.column.ch04.ReplicationBasicsDemo`。
3. 观察：第一步打印 `InvalidReplicationFactorException` 或明确副本不足信息；第二步打印各分区 **leader** 与 **isr** 列表；第三步出现 `min.insync.replicas` 等配置项。

**期望输出（节选）**：

```text
=== 1) 尝试创建 RF=2 的 Topic（单节点集群预期失败）===
预期异常 InvalidReplicationFactorException: ...

=== 2) Describe：各分区的 Leader、Replicas、ISR ===
partition=0 leader=1 replicas=[1] isr=[1]
...
说明：单 Broker 时 replicas/isr 通常只含同一 broker id；多副本时 ISR ⊆ Replicas。

=== 3) Topic 配置：min.insync.replicas（与 acks=all 配合，见第 14 章）===
min.insync.replicas = 1 ...
```

**与 CLI 的对照（运维同屏）**：在容器内执行  
`/opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 --describe --topic column.ch04.replication`  
输出中的 `Replicas`、`Isr` 应与 Java `Describe` 一致。若不一致，优先排查是否连错集群或 Topic 名拼写。

**为何要先「故意失败」再成功**：教学上，**错误信息**往往比成功日志更难忘。`InvalidReplicationFactorException` 把「副本必须落在不同 Broker」这一约束具象化；若团队跳过这一步，容易在 IaC 模板里写死 RF=3，却在三节点以下的环境里反复创建失败。建议在内部脚手架里对 **Broker 数与 RF** 做静态校验，把失败前移到 CI。

### 3.3 贴近生产的变体：显式参数

生产集群上通常 **RF≥3**，并为 Topic 配置 **`min.insync.replicas=2`**（与集群策略一致），生产者使用 **`acks=all`**（第 5、14 章细讲）。本章变体建议：在**测试环境多 Broker**（可参考 Apache Kafka 官方仓库 `docker/examples` 多节点 compose）上重复本实验，观察 **RF=3** 时 `isr` 与 `replicas` 在节点故障前后的变化；同时记录 **`UnderReplicatedPartitions`** 指标与 `describe` 的对应关系。注意：不要在单节点强行改出不一致的 Topic 配置，**`min.insync.replicas` 不得超过 RF** 且需与集群 Broker 数匹配。

变体实验可进一步包括：**（1）** 人为停掉一个非 Leader Broker，观察该分区是否 **UnderReplicated**、ISR 是否缩小；**（2）** 在 **RF=3、ISR=2** 时，将 `min.insync.replicas` 保持为 2，验证 **`acks=all`** 仍成功；再模拟 ISR 收缩到 1，观察写入是否按预期失败或降级（取决于客户端与 broker 版本及策略）。这些步骤最好在**非生产**环境完成，并提前写好 **Rollback**（恢复节点、恢复副本同步）。

---

## 4. 项目总结

### 4.1 优点

- **语义清晰**：RF 与副本、ISR、Leader 的概念与监控、告警一致，便于跨部门协作。
- **耐久与可用可权衡**：通过 `min.insync.replicas` 与 `acks` 组合，在「写成功条件」上显式表达团队偏好。
- **故障可解释**：ISR 收缩、Leader 切换、Under-replicated 有相对明确的因果链（高级篇 27 章继续展开）。
- **与目录式存储一致**：副本机制让「同一条日志」在多台机器上可核对，有利于审计与灾备演练。

### 4.2 缺点

- **副本不是并行银弹**：增加 RF 会**增加复制与磁盘开销**，不解决消费并行不足。
- **强耐久与强可用常冲突**：`min.insync.replicas` 过高可能在 ISR 缩小时**拒绝写入**。
- **开发/生产环境差异**：单 Broker 无法演练多副本行为，易产生「本地能跑、上线参数不对」。
- **心智负担**：Leader 选举、ISR 变化、延迟复制等问题需要与网络、磁盘、GC 联合排查，学习曲线陡。

### 4.3 适用场景

- **需要机架/机房容错**：生产 RF=3、跨机架副本放置（运维与集群配置）。
- **强持久化写入**：`acks=all` + `min.insync.replicas` 与团队 RPO/RTO 对齐（与第 14 章联动）。
- **读多写少、可接受短暂只读**：某些场景下宁可拒绝写入，也不接受「未确认副本」的落盘（与业务共同决策）。

### 4.4 注意事项

- Topic 创建前确认 **Broker 数量 ≥ RF**；自动化脚本应对 `InvalidReplicationFactorException` 做友好提示。
- 变更 `min.insync.replicas` 与 RF 属于**运维级操作**，需评估写入可用性与下游重试。
- 监控告警与 **ISR、Leader 选举**、**网络** 联动分析，避免「只加副本不查网络」。
- **跨环境复制**（灾备、多集群）时，目标集群的 **Broker 拓扑与 RF 策略**可能与源集群不同，需单独评审，不宜照搬 Topic 模板。

### 4.5 常见踩坑

- **把 RF 当成分区数加倍**：二者独立，混用会导致容量与并行度误判。
- **以为 Follower 能直接消费减负**：常规客户端读 Leader，扩展消费仍靠分区与消费者。
- **忽略 ISR**：`acks=all` 时若 ISR 不足，写入失败，误以为是业务问题。

**跨团队沟通提示**：与业务对齐 **RPO/RTO** 时，用「**最少副本确认数**」与「**允许短暂不可写**」两种语言翻译 `min.insync.replicas` 策略，避免只谈「副本越多越好」。

**与后续章节的衔接**：第 10、27 章会从指标与协议角度深入 **ISR、HW/LEO**；第 14 章把 **`acks`、重试、幂等**与副本确认语义串成完整交付语义；第 23 章讨论 **扩缩容与副本迁移**对线上行为的影响。

**读者自测**：在纸上画一个分区、三个副本、ISR 从 3 变 2 再变 1 时，**若 `min.insync.replicas=2`，`acks=all` 在什么时刻会拒绝写入**？若能答出「ISR 内可确认副本数不足时」，说明本章与第 14 章的衔接已打通。

**与第 5 章的衔接**：当你配置生产者 **`acks=all`** 时，脑子里应同时出现 **ISR 与 `min.insync.replicas`** 的画面——否则「客户端已配置」与「Broker 实际允许写入」之间会出现断层。读完第 4 章再读第 5 章，把 **发送参数**与**副本集合**绑在一起记忆，后续学习交付语义会轻松很多。

---

## 附录（可选）

### 研发：设计评审问题清单

- 目标集群 **Broker 数与 RF** 是否满足？Topic 是否跨环境复制？
- 写入路径是 **`acks=all` 还是 `acks=1`**？与 `min.insync.replicas` 是否一致？
- 单分区热点是否被误当成「副本不足」？
- 若使用云托管或托管 Kafka，**RF 与机架感知**是否由平台托管，团队是否仍需关注 ISR 与告警？

### 测试：用例模板

- 正常：RF 与 Broker 合法时创建 Topic。
- 异常：RF 大于 Broker 数时创建失败（与单测环境一致）。
- Broker 重启：Leader 切换后，生产与消费是否仍满足 SLA（与第 10 章衔接）。
- 回归：同一 Topic 在 **describe** 与 **监控面板** 中的副本列表是否一致（防止连错环境）。

### 运维：变更与告警映射

- **UnderReplicatedPartitions / ISR 收缩**：副本落后、网络、磁盘 → 对照 `describe` 与 Broker 日志。
- **写入失败率上升**：ISR 与 `min.insync.replicas` 不匹配 → 与客户端 `acks` 配置联合排查。
- **扩缩 Broker 后**：观察 **分区重分配**是否完成、ISR 是否恢复，再解除变更窗口（与第 23 章衔接）。
