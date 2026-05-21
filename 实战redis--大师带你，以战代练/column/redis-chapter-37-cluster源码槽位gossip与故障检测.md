# 第37章：Cluster 源码：槽位、Gossip 与故障检测

## 1. 项目背景

上一章解决的是单主高可用问题，但企业缓存平台很快会遇到第二个问题：一个 Redis 实例装不下全部数据，单机内存、单线程执行、网络带宽都会成为瓶颈。电商大促期间，商品详情、库存预热、推荐特征、会话状态加起来可能有几亿个 key，只靠一主两从很难撑住容量和吞吐。

Redis Cluster 用 16384 个槽位把 key 分散到多个主节点上，每个主节点再带从节点做高可用。业务看起来只是在连接一个集群，但背后涉及槽位映射、客户端重定向、节点间 Gossip、故障检测、故障转移和槽位迁移。很多生产问题都发生在“半懂不懂”的边界：客户端没有开启 cluster 模式导致 `MOVED` 报错；迁移期间没有处理 `ASK`；节点间总线端口不通导致互相误判；槽位未覆盖导致部分 key 不可用。

本章的目标是搭建一个源码调试版 Redis Cluster，模拟 reshard 和 failover，结合 `src/cluster.c` 观察 Cluster 如何维护拓扑、传播节点状态，并用命令验证每一步。

## 2. 项目设计

小胖先说：“这不就像把火锅店分成 16384 张桌子吗？客人来了看号码坐桌，桌子归哪个店长管就去哪边吃。”

小白追问：“那客户端怎么知道桌子在哪里？如果桌子搬家了呢？如果某个店长失联，其他店长怎么判断他是真下班还是路上堵车？”

大师把图分成三层：“第一层是槽位，Redis Cluster 不直接按节点数量取模，而是对 key 算 CRC16 后映射到 0 到 16383 的槽。第二层是节点元数据，每个节点都有 `clusterNode`，记录 node id、地址、flags、槽位、复制关系和最近通信时间。第三层是 Gossip，节点之间通过 cluster bus 交换 PING、PONG、MEET、FAIL 等消息，最终让拓扑信息在集群内传播。”

技术映射：核心源码在 `src/cluster.c`，重点结构体是 `clusterNode`、`clusterLink`、`clusterState`，关键函数包括 `clusterCron`、`clusterSendPing`、`clusterProcessPacket`、`clusterUpdateState`。

小胖问：“客户端拿错桌号怎么办？我去 A 节点问 `user:1`，结果这个槽在 B 节点。”

小白说：“这就是 `MOVED`？迁移中好像还有 `ASK`，两个有什么区别？”

大师解释：“`MOVED` 是稳定拓扑下的永久重定向，告诉客户端这个槽当前归哪个节点，客户端应更新本地槽位缓存。`ASK` 是迁移过程中的临时重定向，表示这个 key 可能已经迁到目标节点，但槽位所有权还没正式切换，客户端需要先发 `ASKING` 再执行命令。”

技术映射：命令执行前会检查 key 所属槽与当前节点是否匹配，相关逻辑可以跟到 `getNodeByQuery`、`clusterRedirectClient`。

小胖继续问：“那节点挂了，谁来拍板？”

小白补充：“上一章 Sentinel 有 quorum，Cluster 是每个分片自己投票吗？”

大师回答：“Cluster 的故障检测也是先 PFAIL，再 FAIL。某个节点认为目标节点超时，会先标记 PFAIL；当通过 Gossip 收到足够多主节点对目标节点的失败报告，才升级为 FAIL。故障转移时，挂掉主节点的从节点发起选举，请求其他主节点投票。得票成功后，从节点提升为主节点并接管槽位。”

技术映射：观察 `clusterNodeTimedOut`、`markNodeAsFailingIfNeeded`、`clusterSendFail`、`clusterHandleSlaveFailover`、`clusterRequestFailoverAuth`。

## 3. 项目实战

### 3.1 搭建六节点 Cluster

准备 6 个 Redis 实例，端口 7000 到 7005。每个配置至少包含：

```conf
port 7000
cluster-enabled yes
cluster-config-file nodes-7000.conf
cluster-node-timeout 5000
appendonly yes
protected-mode no
```

启动后创建集群：

```bash
redis-cli --cluster create 127.0.0.1:7000 127.0.0.1:7001 127.0.0.1:7002 127.0.0.1:7003 127.0.0.1:7004 127.0.0.1:7005 --cluster-replicas 1
redis-cli -c -p 7000 cluster nodes
redis-cli -c -p 7000 cluster slots
```

如果是 Docker 环境，要特别注意 cluster announce 地址和总线端口。Redis 客户端端口是 7000，cluster bus 默认是 17000，节点间总线不通会导致集群看似启动、实际互相握手失败。

### 3.2 验证槽位与重定向

写入几个 key：

```bash
redis-cli -c -p 7000 set user:37:1 xiaopang
redis-cli -c -p 7000 set order:37:1 paid
redis-cli -p 7000 cluster keyslot user:37:1
```

去非所属节点执行不带 `-c` 的命令，通常会看到：

```text
MOVED 1234 127.0.0.1:7001
```

这一步不是错误，而是 Cluster 协议的一部分。生产客户端必须启用 cluster mode，让客户端维护槽位表并在收到 `MOVED` 后刷新路由。

源码观察点：
- `src/cluster.c`：`keyHashSlot` 计算槽位，注意 `{hash-tag}` 规则。
- `src/cluster.c`：`clusterRedirectClient` 生成 `MOVED` 或 `ASK` 响应。
- `src/server.c` 与命令执行链路：命令进入执行前会做集群路由校验。

### 3.3 分析 Gossip

执行：

```bash
redis-cli -p 7000 cluster nodes
redis-cli -p 7000 cluster info
```

重点看 `cluster_state`、`cluster_slots_assigned`、`cluster_known_nodes`、节点 flags、ping/pong 时间。源码里 `clusterCron` 会定期选择节点发送 PING，消息体中携带部分其他节点的 Gossip 信息。接收方在 `clusterProcessPacket` 中更新本地视图。

如果要更直观看到消息，可以在源码调试版里给 `clusterSendPing` 和 `clusterProcessPacket` 增加临时日志，打印发送方、接收方、消息类型、携带 gossip 节点数量。也可以用 `tcpdump` 抓 cluster bus 端口，不过消息是 Redis 自定义二进制协议，源码日志更适合学习。

### 3.4 模拟 Reshard 与 ASK

迁移一个槽位：

```bash
redis-cli --cluster reshard 127.0.0.1:7000
```

迁移过程中观察源节点和目标节点：

```bash
redis-cli -p 7000 cluster nodes
redis-cli -p 7001 cluster nodes
redis-cli -c -p 7000 get user:37:1
```

如果命令命中正在迁移的 key，可能出现 `ASK`。客户端收到 `ASK` 后不能永久更新槽位表，而是对目标节点发送一次 `ASKING`，再执行原命令。这个差异决定了生产客户端是否能平稳支持在线扩容。

### 3.5 模拟 Failover

停止一个主节点：

```bash
docker stop redis-7000
redis-cli -c -p 7001 cluster nodes
redis-cli -c -p 7001 cluster info
```

观察从节点提升过程。日志中通常能看到 PFAIL、FAIL、failover auth request、elected、config epoch 更新。验证重点：
1. 原主节点的槽位是否由从节点接管。
2. `cluster_state` 是否恢复 ok。
3. 客户端是否收到并处理新的 `MOVED`。
4. 业务写入是否能恢复。

常见坑包括：节点时间漂移导致判断异常；`cluster-node-timeout` 配得过小导致网络抖动被放大；客户端连接池缓存旧节点；多 key 命令没有使用 hash tag，跨槽报 `CROSSSLOT`。

## 4. 项目总结

Redis Cluster 的核心不是“多个 Redis 拼在一起”，而是一套分布式拓扑维护机制。槽位解决数据分布，Gossip 解决状态传播，PFAIL/FAIL 和投票解决故障检测与接管，`MOVED`/`ASK` 解决客户端路由变化。

优点：
- 水平扩展容量和吞吐。
- 原生支持分片级故障转移。
- 槽位模型简单，便于客户端缓存路由。
- 在线 reshard 支持逐步迁移数据。

缺点：
- 多 key 操作受同槽限制。
- 客户端必须正确处理重定向。
- 节点间网络和总线端口要求更高。
- 故障检测参数过激会导致误判。

适用场景是大规模缓存、会话、排行榜、特征存储和可按 key 分片的实时数据。不适合强跨 key 事务、复杂多维查询或要求全局强一致的业务。思考题：为什么 Cluster 选择固定 16384 槽而不是按节点数取模？在线迁移时，哪些客户端行为会把一次平滑扩容变成线上故障？
