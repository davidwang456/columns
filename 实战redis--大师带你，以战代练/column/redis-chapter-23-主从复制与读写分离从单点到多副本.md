# 第23章：主从复制与读写分离：从单点到多副本

## 1. 项目背景

商品详情服务接入 Redis 后，读请求越来越多。大促当天，商品、库存快照、优惠券配置和排行榜都在 Redis 中高频读取，单机 Redis 虽然还能扛住，但运维同学开始担心两个问题：第一，单点故障，一台机器挂了业务就大面积降级；第二，读流量继续上涨时，主库既要处理写入又要处理读取，后续扩展空间有限。

主从复制是 Redis 高可用和读写分离的第一层基础。一个主库负责写入，多个从库复制数据并承担读请求。它不能自动完成故障转移，那是下一章 Sentinel 的任务；它也不能解决内存容量水平拆分，那是 Cluster 的任务。但在很多中小规模系统里，一主两从已经能显著提升读容量和备份能力。

本章以“商品详情缓存读写分离”为场景，搭建一主两从，观察全量同步、增量同步、复制延迟和 `min-replicas-to-write` 数据保护。实战目标不是只跑通 `REPLICAOF`，而是知道哪些读可以走从库，哪些业务必须读主库。

## 2. 项目设计

小胖先说：“主从复制不就是多开几个 Redis 备份吗？主库写了，从库跟着抄作业，读请求丢给从库就行。”

小白追问：“如果从库抄慢了，用户刚改完昵称，下一秒读从库看到旧昵称怎么办？复制断了又恢复，是不是要全量同步？大 key 会不会拖垮网络？”

大师解释：“主从复制确实像抄作业，但不是同时写完。Redis 复制是异步的，主库写成功不代表从库立刻可见。读写分离要先做业务分级：商品详情、排行榜这类允许短暂旧值的读可以走从库；支付状态、刚提交订单后的查询最好读主库或数据库。”

技术映射：从库通过 `REPLICAOF host port` 连接主库，首次通常触发全量同步，之后依赖复制流和 backlog 做增量同步。

小胖问：“全量同步是不是把主库所有数据打包发过去？”

大师说：“可以这么理解。主库生成 RDB 发给从库，从库加载后继续接收同步期间积累的写命令。这个过程会消耗 CPU、内存、磁盘和网络，所以不能在高峰期随便加从库。”

小白补充：“如果网络闪断，能不能只补断开期间的命令？”

大师回答：“这就看复制积压缓冲区 backlog 是否还保留那段增量。如果保留，就部分重同步；如果不够，只能重新全量同步。配置 `repl-backlog-size` 要结合写入峰值和可接受断线时间。”

技术映射：`master_repl_offset`、`slave_repl_offset` 和 `master_link_status` 是观察复制状态的关键指标。

小胖又问：“那主库写入时能不能要求至少一个从库收到，防止主库突然挂掉丢数据？”

大师回答：“Redis 有 `min-replicas-to-write` 和 `min-replicas-max-lag`，可以在从库数量或延迟不满足时拒绝主库写入。它提高安全性，但会牺牲可用性。高峰期网络抖动时，业务可能写失败，所以要和产品确认降级策略。”

## 3. 项目实战

### 3.1 启动一主两从

先创建自定义网络，确保容器名可以被解析：

```bash
docker network create redis-repl-net
```

用三个端口启动 Redis：

```bash
docker run --name redis-master-23 --network redis-repl-net -p 6379:6379 -d redis:8.6 redis-server --appendonly yes
docker run --name redis-replica1-23 --network redis-repl-net -p 6380:6379 -d redis:8.6 redis-server --appendonly yes
docker run --name redis-replica2-23 --network redis-repl-net -p 6381:6379 -d redis:8.6 redis-server --appendonly yes
```

让两个从库复制主库：

```bash
docker exec -it redis-replica1-23 redis-cli REPLICAOF redis-master-23 6379
docker exec -it redis-replica2-23 redis-cli REPLICAOF redis-master-23 6379
```

### 3.2 验证复制状态

主库写入商品缓存：

```bash
docker exec -it redis-master-23 redis-cli
SET product:1001 "{\"name\":\"机械键盘\",\"price\":299}"
HSET stock:snapshot:1001 available 500 locked 0
ZADD rank:hot:today 100 product:1001
INFO replication
```

从库读取：

```bash
docker exec -it redis-replica1-23 redis-cli GET product:1001
docker exec -it redis-replica2-23 redis-cli HGETALL stock:snapshot:1001
```

从库默认只读，尝试写入会失败：

```bash
SET product:1002 test
```

预期返回类似：

```text
READONLY You can't write against a read only replica.
```

### 3.3 业务读写分离流程

推荐先从低风险读开始：

```text
商品详情接口
  -> 写请求：更新数据库，删除或更新主库缓存
  -> 普通读：优先读从库，未命中读数据库并回填主库
  -> 刚写后读：短时间内读主库，避免复制延迟
  -> 强一致查询：直接读数据库或主库
```

客户端侧可以配置两个连接池：

```text
redisWritePool -> master:6379
redisReadPool  -> replica1:6380, replica2:6381
```

如果使用支持读写分离的客户端，要确认它如何发现从库、如何处理从库延迟、主从切换后是否自动刷新拓扑。

### 3.4 模拟断链和延迟观察

查看主从复制偏移：

```bash
INFO replication
```

关注字段：

```text
role:master
connected_slaves:2
master_repl_offset:...
slave0:ip=...,state=online,offset=...,lag=0
```

停止一个从库：

```bash
docker stop redis-replica1-23
```

主库继续写入：

```bash
INCRBY stock:snapshot:counter 1
SET product:last_update "replica1 stopped"
```

重启从库后观察是否能增量追上：

```bash
docker start redis-replica1-23
docker exec -it redis-replica1-23 redis-cli INFO replication
```

生产配置片段：

```conf
replica-read-only yes
repl-backlog-size 256mb
repl-backlog-ttl 3600
min-replicas-to-write 1
min-replicas-max-lag 5
```

### 3.5 常见坑

第一，把所有读都切到从库会引发读旧数据。用户刚提交的订单、刚修改的资料、支付结果查询不适合盲目读从库。

第二，从库也会占用内存。Redis 不是“复制后免费扩容”，每个副本都要存一份完整数据。

第三，复制缓冲和 backlog 配置过小，网络抖动后容易触发全量同步，反而造成更大压力。

第四，`min-replicas-to-write` 会在从库不足时拒绝写入，要提前设计业务错误提示和降级方案。

第五，从库不能替代备份。误删命令会同步到所有从库，仍需要 RDB/AOF 备份和恢复演练。

## 4. 项目总结

主从复制把 Redis 从单点推进到多副本，是 Sentinel、Cluster 和读写分离的基础。它适合扩展读能力、降低单机故障风险、提供备份来源，但它是异步复制，不能天然保证强一致。

工程落地时，要按业务一致性要求拆分读路径：允许短暂旧值的热点读走从库，强一致读走主库或数据库；同时监控复制延迟、断链次数、全量同步频率和 backlog 命中情况。

适用场景：商品详情、配置缓存、排行榜读取、报表类近实时查询。不适用场景：支付确认、余额查询、需要读己之写的关键链路。

思考题：
1. 如果从库延迟 3 秒，哪些接口可以接受，哪些接口必须回主库？
2. 写入峰值为 20MB/s，希望网络断开 10 秒仍可部分重同步，`repl-backlog-size` 至少应如何估算？
