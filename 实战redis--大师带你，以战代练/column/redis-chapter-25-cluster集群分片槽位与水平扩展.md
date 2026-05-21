# 第25章：Cluster 集群：分片、槽位与水平扩展

## 1. 项目背景

公司活动系统的 Redis 越用越大：商品缓存几十 GB，排行榜和用户状态还在增长，单机内存和网络带宽开始接近上限。一主两从和 Sentinel 能解决高可用，却不能把数据拆到多台主机上。继续纵向升级机器会越来越贵，故障影响面也越来越大。

Redis Cluster 用分片解决水平扩展问题。它把整个 key 空间拆成 16384 个槽位，每个主节点负责一部分槽位，客户端根据 key 计算槽位并路由到正确节点。节点扩容时，槽位可以迁移到新节点；某个主节点故障时，它的从节点可以晋升接管对应槽位。

本章以“活动缓存集群扩容”为场景，搭建 3 主 3 从 Cluster，写入商品、库存和排行榜数据，观察 `MOVED`、`ASK`、hash tag、多 key 命令限制，以及在线 reshard 的基本流程。重点是把 Cluster 当成分布式系统来设计，而不是把单机 Redis 命令原样搬过去。

## 2. 项目设计

小胖先问：“Cluster 是不是把 Redis 变成一个大硬盘？我不用管数据在哪台机器，随便 `SET` 就行？”

小白追问：“如果 `MGET product:1 stock:1` 两个 key 落到不同槽，Redis 怎么办？客户端遇到 `MOVED` 是自己重试，还是我们写代码处理？”

大师回答：“Cluster 对单 key 命令很友好，但对多 key 命令有槽位限制。它不是把所有节点伪装成一个完全透明的单机，而是要求客户端理解槽位路由。成熟客户端会维护槽位表，收到 `MOVED` 后刷新路由，收到 `ASK` 后临时跳转。”

技术映射：Redis Cluster 使用 CRC16(key) % 16384 计算槽位；`MOVED` 表示槽位已归属其他节点；`ASK` 常见于槽位迁移过程中的临时重定向。

小胖说：“那我想把同一个订单的多个 key 放一起，能不能指定它们去同一个槽？”

大师回答：“可以用 hash tag。Redis 只对 `{}` 中的内容计算槽位。例如 `order:{1001}:base` 和 `order:{1001}:items` 会落到同一槽，才能执行部分多 key 操作。但别滥用 hash tag，否则会把大量数据压到同一个槽，形成热点。”

小白继续问：“扩容时是不是停机搬数据？”

大师说：“Cluster 支持在线 reshard，把一部分槽位从旧主迁移到新主。迁移期间客户端可能收到 `ASK` 重定向，所以客户端必须支持 Cluster 协议。扩容不是没有成本，迁移会消耗网络和 CPU，要避开峰值并监控延迟。”

技术映射：Cluster 解决容量和吞吐扩展，同时引入路由、槽位均衡、跨槽限制、迁移抖动和节点通信复杂度。

小胖最后问：“Sentinel 和 Cluster 哪个更高级？”

大师说：“不是高级低级，而是目标不同。Sentinel 是单分片高可用，Cluster 是多分片扩展加每个分片的故障转移。数据量不大优先 Sentinel，容量和吞吐需要横向扩展才上 Cluster。”

## 3. 项目实战

### 3.1 启动 6 个 Redis 节点

创建网络：

```bash
docker network create redis-cluster-net
```

每个节点需要开启 cluster：

```conf
port 6379
cluster-enabled yes
cluster-config-file nodes.conf
cluster-node-timeout 5000
appendonly yes
```

可以用 Docker 快速启动 6 个节点，端口映射为 7001 到 7006：

```bash
docker run --name redis-c1 --network redis-cluster-net -p 7001:6379 -d redis:8.6 redis-server --cluster-enabled yes --cluster-config-file nodes.conf --cluster-node-timeout 5000 --appendonly yes
docker run --name redis-c2 --network redis-cluster-net -p 7002:6379 -d redis:8.6 redis-server --cluster-enabled yes --cluster-config-file nodes.conf --cluster-node-timeout 5000 --appendonly yes
docker run --name redis-c3 --network redis-cluster-net -p 7003:6379 -d redis:8.6 redis-server --cluster-enabled yes --cluster-config-file nodes.conf --cluster-node-timeout 5000 --appendonly yes
docker run --name redis-c4 --network redis-cluster-net -p 7004:6379 -d redis:8.6 redis-server --cluster-enabled yes --cluster-config-file nodes.conf --cluster-node-timeout 5000 --appendonly yes
docker run --name redis-c5 --network redis-cluster-net -p 7005:6379 -d redis:8.6 redis-server --cluster-enabled yes --cluster-config-file nodes.conf --cluster-node-timeout 5000 --appendonly yes
docker run --name redis-c6 --network redis-cluster-net -p 7006:6379 -d redis:8.6 redis-server --cluster-enabled yes --cluster-config-file nodes.conf --cluster-node-timeout 5000 --appendonly yes
```

创建 3 主 3 从集群：

```bash
docker exec -it redis-c1 redis-cli --cluster create \
  redis-c1:6379 redis-c2:6379 redis-c3:6379 \
  redis-c4:6379 redis-c5:6379 redis-c6:6379 \
  --cluster-replicas 1
```

### 3.2 验证槽位和路由

使用 Cluster 模式客户端连接：

```bash
docker exec -it redis-c1 redis-cli -c
```

写入业务数据：

```bash
SET product:1001 "{\"name\":\"耳机\",\"price\":199}"
SET stock:1001 500
ZADD rank:hot:202604 90 product:1001
CLUSTER KEYSLOT product:1001
CLUSTER NODES
CLUSTER INFO
```

如果不用 `-c`，访问不属于当前节点的槽位时可能看到：

```text
MOVED 1234 172.18.0.3:6379
```

这不是错误，而是在告诉客户端“这个槽位归另一个节点负责”。业务客户端必须使用支持 Cluster 的连接方式。

### 3.3 hash tag 与多 key 命令

普通多 key 可能跨槽失败：

```bash
MGET product:1001 stock:1001
```

预期可能返回：

```text
CROSSSLOT Keys in request don't hash to the same slot
```

使用 hash tag：

```bash
SET product:{1001}:base "{\"name\":\"耳机\"}"
SET product:{1001}:stock 500
MGET product:{1001}:base product:{1001}:stock
CLUSTER KEYSLOT product:{1001}:base
CLUSTER KEYSLOT product:{1001}:stock
```

业务建模流程：

```text
按商品 ID 聚合强相关 key
  -> 需要多 key 原子操作的 key 使用相同 hash tag
  -> 大范围列表、排行榜避免强行塞同一槽
  -> 热点商品单独评估拆分和限流
```

### 3.4 在线扩容和迁移

新增节点后可以执行 reshard。示例流程：

```bash
docker run --name redis-c7 --network redis-cluster-net -p 7007:6379 -d redis:8.6 redis-server --cluster-enabled yes --cluster-config-file nodes.conf --cluster-node-timeout 5000 --appendonly yes
docker exec -it redis-c1 redis-cli --cluster add-node redis-c7:6379 redis-c1:6379
docker exec -it redis-c1 redis-cli --cluster reshard redis-c1:6379
```

交互中选择迁移的槽位数量、接收节点 ID 和来源节点。迁移期间观察：

```bash
CLUSTER NODES
CLUSTER SLOTS
INFO stats
```

客户端如果支持 Cluster，会自动处理迁移中的 `ASK` 和迁移后的 `MOVED`。

### 3.5 常见坑

第一，把 Cluster 当单机用，结果多 key 命令大量 `CROSSSLOT`。设计 key 时要提前规划 hash tag。

第二，滥用 hash tag，把同一租户或同一活动所有 key 都压到一个槽，导致热点和容量倾斜。

第三，客户端没开启 Cluster 模式，只能看到重定向错误，业务不会自动重试。

第四，扩容迁移在高峰期执行，导致延迟抖动。reshard 要限速、分批，并持续观察 P95/P99。

第五，只看节点数量，不看槽位和数据分布。3 个主节点不代表负载一定均衡，热点 key 仍然可能打爆单节点。

## 4. 项目总结

Redis Cluster 的核心是槽位分片：16384 个槽位分布在多个主节点上，客户端按 key 计算槽位并路由请求。它让 Redis 获得水平扩展能力，也提供分片级副本和故障转移。

Cluster 的代价是复杂度上升：跨槽多 key 限制、客户端路由、迁移期间重定向、槽位均衡和热点治理都要进入架构设计。上 Cluster 前，应先确认单机加主从已经无法满足容量或吞吐，而不是为了“看起来高级”提前引入。

适用场景：大规模缓存、热点分散的商品数据、用户状态、分片排行榜。不适用场景：强依赖多 key 原子操作、数据量很小但追求简单运维、需要复杂查询的业务。

思考题：
1. 为什么 Redis Cluster 选择固定 16384 个槽位，而不是让每个 key 直接映射到节点？
2. 如果一个活动的所有 key 都使用 `{activity:202604}` 作为 hash tag，会带来什么好处和风险？
