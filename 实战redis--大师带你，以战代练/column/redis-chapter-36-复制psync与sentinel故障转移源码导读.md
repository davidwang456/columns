# 第36章：复制、PSYNC 与 Sentinel 故障转移源码导读

## 1. 项目背景

电商平台的 Redis 承担了登录态、活动配置、库存预热、排行榜和订单事件游标等核心读写路径。单机 Redis 跑得很快，但主库一旦宕机，业务会立刻出现大面积超时；如果只是手工把从库改成主库，又会遇到配置切换慢、客户端感知滞后、数据是否丢失说不清等问题。

本章把场景放在“活动大促前的高可用演练”。团队已经有一主两从和三哨兵，但大家只会配置 `replicaof`、`sentinel monitor`，并不清楚断链后为什么有时是增量同步，有时会全量同步；也不清楚 Sentinel 判断主观下线、客观下线、选举 leader、提升从库的流程到底在哪里实现。生产排障时，如果只盯着“主从状态不一致”这个表象，很难判断是复制积压缓冲区太小、网络抖动、从库延迟过高，还是故障转移窗口里发生了可接受但必须记录的数据丢失。

这一章的实战目标是搭建一个最小高可用环境，模拟主从断链和主库宕机，结合 Redis 源码文件、日志、`INFO replication`、Sentinel 命令，读懂 PSYNC 与故障转移的关键链路。

## 2. 项目设计

小胖先开球：“主从复制不就是主库把数据抄给从库吗？像我把作业发给小白，他照着写就行了，为什么还要 PSYNC、offset、backlog 这么多名词？”

小白马上追问：“如果网络断了十秒，小白回来后是从头抄一遍，还是接着断点抄？如果这十秒主库写了很多数据，断点还在不在？如果主库宕机，哨兵怎么知道该选哪台从库？”

大师在白板上画了一条时间轴：“复制的第一件事不是抄数据，而是确认双方是否还能续上同一段历史。主库有 `runid`，代表当前复制历史身份；主从都有 offset，代表复制流走到哪里；主库维护 replication backlog，像一段可回放的课堂录音。从库断线回来后发起 `PSYNC runid offset`，主库如果还认这个 runid，且 backlog 里还保留 offset 之后的数据，就返回 `+CONTINUE` 做增量复制；否则只能 `+FULLRESYNC`，重新生成 RDB 做全量复制。”

技术映射：复制状态机主要看 `src/replication.c`，重点观察 `replicationCron`、`syncWithMaster`、`masterTryPartialResynchronization`、`addReplyReplicationBacklog`、`replicationFeedSlaves`。

小胖又问：“那 Sentinel 是不是一个 Redis 管家？主库不动了，它就喊大家换房东？”

小白补充：“但它怎么判断主库真的挂了？网络分区时，有的 Sentinel 能连上，有的连不上，谁说了算？”

大师回答：“Sentinel 不是单点裁判，而是一组观察员。单个 Sentinel 通过 `PING`、命令连接、订阅连接判断实例是否长时间无响应，这叫主观下线 S_DOWN；多个 Sentinel 交换意见，票数达到 quorum，才进入客观下线 O_DOWN。之后还要选出一个 Sentinel leader，由它选择合适的从库提升为新主，重写其他从库复制关系，并发布配置纪元。”

技术映射：Sentinel 逻辑在 `src/sentinel.c`，观察 `sentinelTimer`、`sentinelHandleRedisInstance`、`sentinelCheckSubjectivelyDown`、`sentinelCheckObjectivelyDown`、`sentinelStartFailover`、`sentinelFailoverStateMachine`。

小胖有点担心：“那主库挂掉前刚写进去的订单状态，会不会没复制到从库？”

小白说：“这就涉及复制一致性边界。异步复制天然有丢数据窗口，除非用 `WAIT` 或业务层做补偿。”

大师点头：“高可用不等于零丢失。Redis 复制默认异步，主库给客户端返回成功后，从库可能还没收到。我们要做的是缩短窗口、监控窗口、在关键写路径加确认机制。比如活动库存扣减可以用数据库或消息日志兜底，支付状态不能只依赖 Redis。”

技术映射：`min-replicas-to-write`、`min-replicas-max-lag`、`WAIT numreplicas timeout` 可以降低风险，但会牺牲写入可用性和延迟。

## 3. 项目实战

### 3.1 环境准备

准备一个最小 Compose：一主两从三哨兵，端口可按本机情况调整。学习环境可以用官方镜像，源码导读环境建议再准备一份 Redis 源码编译版，便于加日志。

```yaml
services:
  redis-master:
    image: redis:8.6
    command: redis-server --appendonly yes --port 6379
    ports: ["6379:6379"]
  redis-replica-1:
    image: redis:8.6
    command: redis-server --replicaof redis-master 6379 --port 6380
    ports: ["6380:6380"]
  redis-replica-2:
    image: redis:8.6
    command: redis-server --replicaof redis-master 6379 --port 6381
    ports: ["6381:6381"]
```

Sentinel 配置核心如下：

```conf
port 26379
sentinel monitor mymaster redis-master 6379 2
sentinel down-after-milliseconds mymaster 5000
sentinel failover-timeout mymaster 30000
sentinel parallel-syncs mymaster 1
```

### 3.2 观察复制状态

先写入几条数据：

```bash
redis-cli -p 6379 set activity:36:stock 1000
redis-cli -p 6379 incr activity:36:orders
redis-cli -p 6379 info replication
redis-cli -p 6380 info replication
```

重点看主库的 `master_replid`、`master_repl_offset`、`repl_backlog_active`、`repl_backlog_size`，以及从库的 `master_link_status`、`slave_read_repl_offset`、`slave_repl_offset`。如果 offset 持续接近，说明复制延迟较小。

源码观察点：
- `src/replication.c`：`replicationCron` 定期维护复制连接、超时和心跳。
- `src/replication.c`：`replicationFeedSlaves` 把写命令送给从库。
- `src/replication.c`：`masterTryPartialResynchronization` 判断是否可以增量同步。
- `src/rdb.c`：全量同步时生成和发送 RDB 的链路。

### 3.3 模拟断链与 PSYNC

让从库短暂断开网络或暂停容器，再恢复：

```bash
docker pause redis-replica-1
redis-cli -p 6379 incrby activity:36:orders 10
docker unpause redis-replica-1
redis-cli -p 6380 info replication
```

如果断开期间写入量没有超过 backlog，日志里应看到类似 partial resynchronization accepted 的信息；如果把 `repl-backlog-size` 调得很小，再大量写入，就更容易触发 full resync。

为了源码调试，可以在 `masterTryPartialResynchronization` 附近临时增加日志，打印请求 runid、请求 offset、当前 backlog 起止 offset。验证时不要只看是否同步成功，还要判断为什么成功或失败。

### 3.4 模拟 Sentinel 故障转移

停止主库：

```bash
docker stop redis-master
redis-cli -p 26379 sentinel master mymaster
redis-cli -p 26379 sentinel replicas mymaster
```

观察 Sentinel 日志中的 `+sdown`、`+odown`、`+new-epoch`、`+try-failover`、`+selected-slave`、`+promoted-slave`、`+switch-master`。这些日志与 `src/sentinel.c` 的状态机可以一一对应。

验证步骤：
1. 新主库是否能写入：`redis-cli -p <new-master-port> set activity:36:failover ok`。
2. 旧从库是否改为复制新主：`info replication` 中 `master_host` 是否变化。
3. 客户端是否重新发现主库：通过 Sentinel 执行 `SENTINEL get-master-addr-by-name mymaster`。
4. 业务是否存在丢写：对比故障前后的订单日志、数据库流水或消息补偿记录。

常见坑有三个。第一，Sentinel quorum 不是多数派总数，它只决定 O_DOWN 资格，真正 failover leader 还需要选举。第二，复制正常不代表无延迟，要持续关注 offset 差值。第三，故障转移后旧主恢复时可能成为从库，不能让客户端继续直连旧地址写入。

## 4. 项目总结

本章把 Redis 高可用从配置层推进到源码层。PSYNC 解决的是“断线后能否接着复制”的问题，核心取决于 runid、offset 和 backlog；Sentinel 解决的是“主库不可用后谁来组织切换”的问题，核心是下线判断、leader 选举、从库选择和配置传播。

优点：
- 主从复制让读扩展、热备和故障恢复具备基础。
- PSYNC 能显著减少短断链后的全量同步成本。
- Sentinel 提供自动故障转移和主库发现能力。
- 源码日志与命令指标能帮助快速定位同步失败原因。

缺点：
- 默认异步复制存在丢数据窗口。
- 全量同步会带来 RDB、网络和内存压力。
- Sentinel 切换需要客户端正确接入，否则仍会写旧主。
- 网络分区场景需要结合业务一致性策略评估。

适用场景是缓存、会话、排行榜、可补偿的实时状态和读多写少系统。不适合把 Redis 主从当成强一致数据库。思考题：如果 backlog 太小，应该优先调大 backlog 还是减少写入峰值？如果关键写必须降低丢失概率，`WAIT`、数据库事务日志和消息队列各自应该放在哪里？
