# 第15章：Redis 日常运维与故障排查入门

## 1. 项目背景

电商系统接入 Redis 后，开发同学终于把商品缓存、验证码、购物车、积分榜都跑起来了。上线第一周，告警群开始热闹：有时接口连接 Redis 超时，有时报 `NOAUTH Authentication required`，有时商品详情突然变慢，还有一次 Redis 内存打满后写入失败。大家第一反应是“Redis 挂了”，但运维查看进程发现服务还在，CPU 也不高。

Redis 故障排查的第一课，是把“服务不可用”拆成可观察的问题：连接是否正常、认证是否正确、内存是否足够、慢查询是否出现、大 key 或热 key 是否拖慢请求、配置变更是否刚发生。很多基础问题不需要一上来读源码，先用 `INFO`、`CLIENT LIST`、`SLOWLOG`、`LATENCY DOCTOR`、`TYPE`、`TTL`、`MEMORY USAGE` 就能定位方向。

本章以日常值班为场景，建立 Redis 初级排障清单。我们会模拟认证失败、类型错误、慢查询、大 key、无 TTL key 等问题，学习如何用命令收集证据，而不是凭感觉重启服务。

## 2. 项目设计

小胖看着告警说：“Redis 又抽风了，要不重启一下？重启治百病。”

小白马上拦住：“重启可能丢现场。连接超时、认证失败、OOM、慢命令、网络抖动都可能表现为接口失败，先看指标和日志。”

大师点头：“排障像看病。先问生命体征：`PING` 通不通，`INFO` 里内存、连接、命中率、命令量怎样；再看病历：`SLOWLOG`、客户端列表、最近配置变更；最后才考虑重启、扩容或回滚。”

技术映射：Redis 日常排查优先收集证据，避免在未知原因下直接重启。

小胖问：“`INFO` 那么多字段，我每个都背吗？”

小白说：“不用。初级阶段先看 `server`、`clients`、`memory`、`stats`、`persistence`、`keyspace`。比如连接数、内存占用、命中和未命中、过期数量、每个 db 的 key 数。”

大师补充：“监控不是为了好看，而是为了对比。正常时 `used_memory`、`connected_clients`、`instantaneous_ops_per_sec` 大概多少？出事时偏离多少？没有基线，排障就会变成猜谜。”

技术映射：`INFO` 是快照，监控系统要持续采集并形成趋势。

小胖又说：“慢查询我知道，直接 `SLOWLOG GET`。但 Redis 是单线程，慢一下是不是就全堵了？”

小白回答：“是的，慢命令、大 key 删除、大范围扫描、复杂 Lua 都会影响后面的请求。还要看命令复杂度，不能只看调用次数。”

大师总结：“初学阶段记住三个禁忌：生产慎用 `KEYS`、慎删大 key、慎跑长 Lua。发现慢，不要只怪 Redis，要看业务是不是把不合适的工作塞给了 Redis。”

技术映射：慢查询记录的是命令执行时间，不包含网络排队时间；客户端超时还可能来自连接池耗尽或网络问题。

## 3. 项目实战

### 3.1 启动带密码的 Redis

```bash
docker run --name redis-lab-15 -p 6379:6379 -d redis:8.6 redis-server --requirepass redis123 --maxmemory 128mb --maxmemory-policy allkeys-lru
```

未认证时执行：

```bash
docker exec -it redis-lab-15 redis-cli
PING
```

会看到 `NOAUTH Authentication required`。认证后：

```bash
AUTH redis123
PING
INFO server
INFO clients
INFO memory
INFO stats
INFO keyspace
```

常用观察点：

```text
connected_clients        当前客户端连接数
used_memory_human        Redis 已使用内存
maxmemory_human          配置的最大内存
keyspace_hits/misses     缓存命中与未命中
expired_keys             已过期删除的 key 数
evicted_keys             因内存淘汰的 key 数
instantaneous_ops_per_sec 每秒命令数
```

### 3.2 查看客户端连接

```bash
CLIENT LIST
CLIENT INFO
```

`CLIENT LIST` 会显示地址、空闲时间、执行命令、输出缓冲区等信息。排查连接问题时，重点关注连接数是否突然增多，某些客户端是否长时间空闲，是否存在输出缓冲区异常增长。

业务侧也要检查连接池配置：

```text
maxTotal: 最大连接数
maxIdle: 最大空闲连接数
minIdle: 最小空闲连接数
timeout: 命令超时
maxWaitMillis: 获取连接等待时间
```

常见现象是 Redis 本身很快，但应用连接池耗尽，线程都在等待连接，最终表现为接口超时。

### 3.3 慢查询与延迟诊断

设置较低阈值便于实验：

```bash
CONFIG SET slowlog-log-slower-than 1000
CONFIG SET slowlog-max-len 128
SLOWLOG RESET
```

制造一些数据：

```bash
SET mall:test:string value
LPUSH mall:test:list a b c d e
SLOWLOG GET 10
```

学习环境可以执行一次：

```bash
KEYS *
SLOWLOG GET 10
```

生产环境不要用 `KEYS *` 做巡检，应使用：

```bash
SCAN 0 MATCH mall:* COUNT 100
```

延迟工具：

```bash
LATENCY DOCTOR
LATENCY LATEST
```

如果没有启用相关监测或没有延迟事件，输出可能很少。真实环境中可以结合 Redis 日志、监控系统和业务调用链定位。

### 3.4 类型错误、无 TTL 与大 key

模拟类型错误：

```bash
SET mall:user:1001 "plain-string"
HGET mall:user:1001 name
TYPE mall:user:1001
```

`WRONGTYPE` 表示业务代码对同一个 key 的数据结构认知不一致。解决方式不是强行转换，而是追踪谁写入了错误类型，必要时更换 key 名或清理脏数据。

检查无 TTL key：

```bash
SET mall:cache:product:1001 '{"id":1001}'
TTL mall:cache:product:1001
EXPIRE mall:cache:product:1001 600
TTL mall:cache:product:1001
```

检查大小：

```bash
MEMORY USAGE mall:cache:product:1001
STRLEN mall:cache:product:1001
```

列表、集合、哈希还要看元素数量：

```bash
LLEN mall:test:list
HLEN mall:cart:user:9527
SCARD mall:tag:hot
ZCARD mall:rank:point
```

大 key 的处理要谨慎。删除巨大 key 时优先考虑 `UNLINK`，它会异步释放内存，降低阻塞风险：

```bash
UNLINK mall:big:key
```

### 3.5 备份、重启和配置变更

查看持久化状态：

```bash
INFO persistence
CONFIG GET appendonly
CONFIG GET save
```

手动触发后台保存：

```bash
BGSAVE
LASTSAVE
```

配置变更要区分临时和持久。`CONFIG SET` 改的是运行时配置，容器重启后可能丢失；生产环境应通过配置文件、编排平台或变更系统固化。重启前至少确认：是否有持久化、是否有副本、是否允许短暂不可用、客户端是否具备重连能力。

### 3.6 常见坑

第一，看到超时就重启。重启会清空现场，还可能触发缓存雪崩和连接风暴。

第二，只看 Redis 进程，不看客户端。很多问题来自连接池、DNS、网络策略或应用线程池。

第三，忽略 `evicted_keys`。如果淘汰数持续增长，说明内存压力已经影响业务数据。

第四，在线上执行危险命令。`KEYS`、大范围 `HGETALL`、长 Lua、大 key `DEL` 都可能让故障扩大。

## 4. 项目总结

Redis 运维入门的核心不是记住所有命令，而是建立排障顺序：先确认连接和认证，再看 `INFO` 基线，再查慢查询和延迟，再定位 key 设计、内存和客户端问题。能用证据说话，才不会把所有故障都归结为“Redis 不稳定”。

适用场景：
- 日常值班检查 Redis 运行状态。
- 接口超时、认证失败、OOM、WRONGTYPE 的初步定位。
- 上线前检查 key 数、TTL、慢命令和内存配置。
- 活动期间观察连接数、命中率和淘汰情况。

不适用做法：
- 未保存现场直接重启。
- 用生产 Redis 做随意实验。
- 只看 Redis 指标，不看应用调用链和数据库变化。

思考题：
1. 慢查询日志没有记录，但应用仍然 Redis 超时，可能有哪些原因？
2. 如果 `evicted_keys` 持续增长，但业务没有明显报错，是否可以不处理？为什么？

推广建议：开发团队要在代码中记录 Redis 命令耗时和关键 key；测试团队要模拟认证失败、连接池耗尽、缓存失效和类型错误；运维团队要建立基线仪表盘，并把危险命令审计纳入日常规范。
