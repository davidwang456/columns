# 第24章：Sentinel 高可用：自动故障转移实战

## 1. 项目背景

上一章的一主两从解决了读扩展和副本问题，但主库故障仍然需要人工介入。凌晨两点主库宕机，应用还在连旧主，运维要先确认故障，再选一个从库执行 `REPLICAOF NO ONE`，再让其他从库复制新主，最后通知应用改连接地址。这个流程只要慢几分钟，订单、库存、活动页都会受到影响。

Sentinel 的价值就是让主从架构具备自动故障转移能力。它不是 Redis 数据节点，而是一组“哨兵进程”：持续监控主从节点，判断主库是否下线，多个 Sentinel 达成共识后选举 Leader，由 Leader 推动某个从库晋升为新主，并通知客户端新的主库地址。

本章以“优惠券库存缓存高可用”为场景，搭建三节点 Sentinel 和一主两从 Redis，模拟主库宕机，观察自动切主、客户端如何发现新主，以及脑裂和数据一致性风险。重点是理解 Sentinel 能提升可用性，但不能消除异步复制带来的数据丢失窗口。

## 2. 项目设计

小胖说：“有了 Sentinel，是不是主库挂了就像外卖店换店长，大家继续点单，不用管了？”

小白追问：“谁来判断店长真的挂了？如果只是网络抖动，一个 Sentinel 看不到主库，另一个还能看到，能不能乱切？切完以后客户端怎么知道新店长是谁？”

大师回答：“Sentinel 分两层判断。一个 Sentinel 自己认为主库不可达，叫主观下线；多个 Sentinel 达成足够票数后，叫客观下线。只有客观下线后才会触发 failover。客户端不应该写死主库地址，而是向 Sentinel 查询当前 master。”

技术映射：`S_DOWN` 是主观下线，`O_DOWN` 是客观下线，`quorum` 决定需要多少 Sentinel 同意主库下线。

小胖问：“为什么 Sentinel 要三个？两个不也能互相商量吗？”

大师解释：“高可用系统最怕脑裂。奇数节点更容易形成多数派。三个 Sentinel 可以容忍一个 Sentinel 故障，仍然有两个节点完成判断和选举。生产还应把它们放在不同机器或故障域。”

小白继续问：“如果旧主其实没死，只是网络隔离了，它还在接受写入，新主也接受写入，数据怎么办？”

大师说：“这就是脑裂风险。Sentinel 可以降低风险，但不能让异步复制变强一致。要配合 `min-replicas-to-write`、客户端超时、网络隔离策略和业务幂等。故障切换后，旧主恢复会被重新配置为从库，它隔离期间接受的写入可能丢失。”

技术映射：Sentinel 解决自动选主和通知问题，不解决所有一致性问题。

小胖最后问：“那 Sentinel 和 Cluster 是不是二选一？”

大师回答：“Sentinel 面向单分片主从高可用；Cluster 面向多分片水平扩展，并内置分片级故障转移。数据量不大但要高可用，用 Sentinel；需要横向拆分容量和吞吐，再考虑 Cluster。”

## 3. 项目实战

### 3.1 启动 Redis 主从

创建网络：

```bash
docker network create redis-ha-net
```

启动一主两从：

```bash
docker run --name redis-master-24 --network redis-ha-net -p 6379:6379 -d redis:8.6 redis-server --appendonly yes
docker run --name redis-replica1-24 --network redis-ha-net -p 6380:6379 -d redis:8.6 redis-server --appendonly yes --replicaof redis-master-24 6379
docker run --name redis-replica2-24 --network redis-ha-net -p 6381:6379 -d redis:8.6 redis-server --appendonly yes --replicaof redis-master-24 6379
```

验证复制：

```bash
docker exec -it redis-master-24 redis-cli INFO replication
```

### 3.2 准备 Sentinel 配置

为三个 Sentinel 准备类似配置，端口分别为 26379、26380、26381：

```conf
port 26379
sentinel monitor mymaster redis-master-24 6379 2
sentinel down-after-milliseconds mymaster 5000
sentinel failover-timeout mymaster 60000
sentinel parallel-syncs mymaster 1
```

把配置保存为 `sentinel1.conf` 后挂载启动：

```bash
docker run --name redis-sentinel1-24 --network redis-ha-net \
  -p 26379:26379 \
  -v "$PWD/sentinel1.conf:/etc/redis/sentinel.conf" \
  -d redis:8.6 redis-sentinel /etc/redis/sentinel.conf
```

另外两个 Sentinel 只需修改 `port` 和映射端口。Sentinel 会自动发现主库下的从库，也会互相发现其他 Sentinel。

### 3.3 查询当前主库

客户端不应该直接写死 `redis-master-24:6379`，而是查询 Sentinel：

```bash
redis-cli -p 26379 SENTINEL get-master-addr-by-name mymaster
redis-cli -p 26379 SENTINEL replicas mymaster
redis-cli -p 26379 SENTINEL sentinels mymaster
```

业务连接流程：

```text
应用启动
  -> 连接 Sentinel 列表
  -> 查询 mymaster 当前地址
  -> 建立写连接池
  -> 订阅或定时刷新主库变化
  -> 写失败时重新向 Sentinel 查询
```

支持 Sentinel 的客户端通常会内置这个流程，但仍要配置多个 Sentinel 地址，不能只配一个。

### 3.4 模拟主库宕机

写入测试数据：

```bash
docker exec -it redis-master-24 redis-cli SET coupon:stock:202604 1000
```

停止主库：

```bash
docker stop redis-master-24
```

等待超过 `down-after-milliseconds` 和 failover 时间后，查询新主：

```bash
redis-cli -p 26379 SENTINEL get-master-addr-by-name mymaster
redis-cli -p 26379 SENTINEL master mymaster
```

在新主写入：

```bash
SET coupon:stock:202604 999
INFO replication
```

旧主恢复后，Sentinel 会尝试把它改造成新主的从库：

```bash
docker start redis-master-24
docker exec -it redis-master-24 redis-cli INFO replication
```

### 3.5 常见坑

第一，Sentinel 数量太少或都部署在同一台机器上，故障域没有隔离，高可用只是形式。

第二，客户端只配置一个 Sentinel 地址，这个 Sentinel 挂了应用就无法发现新主。

第三，`down-after-milliseconds` 设置太短，网络抖动会引发误判；设置太长，故障恢复又太慢。

第四，主从异步复制下，故障切换可能丢失旧主上尚未复制到从库的数据。关键写入要结合 `min-replicas-to-write` 和数据库最终校验。

第五，防火墙、Docker 网络、NAT 会导致 Sentinel 宣告地址不可达。生产要显式配置 announce IP 和端口。

## 4. 项目总结

Sentinel 为 Redis 主从架构补上自动故障转移能力：监控、下线判断、Leader 选举、从库晋升、新主通知。它适合数据量能放进单个主节点，但又需要高可用的业务场景。

它的边界也很清楚：Sentinel 不做数据分片，不提升单分片容量，也不能消除异步复制的数据丢失窗口。要让它真正可靠，必须配合奇数 Sentinel、多故障域部署、客户端 Sentinel 模式、复制延迟监控和故障演练。

适用场景：配置缓存、商品缓存、会话状态、活动库存缓存等单分片高可用场景。不适用场景：需要大规模水平扩容、多 key 跨分片写入或强一致交易的系统。

思考题：
1. 为什么 Sentinel 判断主库下线要区分主观下线和客观下线？
2. 如果故障切换发生时旧主还有 100 条写入未复制，你会如何评估和补偿这些数据？
