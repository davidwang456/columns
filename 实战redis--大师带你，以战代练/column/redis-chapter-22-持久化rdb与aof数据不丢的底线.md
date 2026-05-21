# 第22章：持久化 RDB 与 AOF：数据不丢的底线

## 1. 项目背景

订单团队把 Redis 用在库存预热、活动资格、验证码、排行榜和部分实时状态上。上线初期大家觉得 Redis 是缓存，丢了可以回源；后来业务越来越依赖 Redis，问题开始出现：秒杀库存如果重启后少了一段扣减记录，可能导致超卖；排行榜如果丢失一小时数据，运营活动就无法复盘；分布式锁虽然不能靠持久化保证正确性，但重启后的残留状态也会影响排查。

Redis 以内存为核心，持久化是它给数据安全兜底的能力。RDB 像定期拍照片，恢复快、文件紧凑，但两次快照之间的数据可能丢失；AOF 像记录操作日志，安全性更高，但文件增长、重写和磁盘刷写会带来性能影响。生产设计不是简单选择“开或不开”，而是要根据业务可丢窗口、恢复时间、磁盘 IO、延迟抖动和备份策略做取舍。

本章用一个“活动库存和排行榜”的小场景，分别开启 RDB、AOF 和混合持久化，模拟进程崩溃后观察数据恢复。目标是让团队明白：Redis 持久化不是魔法保险箱，而是数据可靠性体系中的一条底线。

## 2. 项目设计

小胖开场：“我理解了，RDB 就像给冰箱拍照，AOF 就像记账本。那我两个都开，是不是 Redis 就永远不丢数据了？”

小白马上追问：“磁盘还可能坏，`appendfsync everysec` 也可能丢 1 秒。`fork` 做快照会不会卡主线程？AOF 重写期间新写入的数据怎么处理？”

大师回答：“两个都开是常见做法，但不是永不丢。RDB 负责快速恢复和备份，AOF 负责缩小数据丢失窗口。真正的数据安全还要靠主从复制、磁盘监控、备份校验和恢复演练。”

技术映射：`BGSAVE` 通过子进程生成 RDB，依赖 `fork` 和写时复制；AOF 根据 `appendfsync` 策略把写命令刷到磁盘。

小胖问：“那我能不能每次写命令都立刻刷盘？就像饭店每收一笔钱立刻存银行。”

小白补充：“`always` 应该最安全，但高并发下磁盘延迟会不会把 Redis 拖慢？”

大师说：“正是取舍。`appendfsync always` 数据安全性最高，性能最差；`everysec` 通常是生产默认选择，最多接受秒级丢失；`no` 把刷盘交给操作系统，性能好但风险更大。你要先问业务：这一秒数据丢了能不能通过数据库或消息补回来。”

技术映射：持久化策略必须绑定 RPO 和 RTO。RPO 是最多能丢多少数据，RTO 是多久恢复服务。

小胖又问：“AOF 文件一直追加，会不会越来越大？”

大师解释：“所以有 AOF rewrite。Redis 会根据当前内存数据重写一份更短的 AOF，去掉中间过程。例如一个 key 被加了 100 次，重写后可能只保留最终 `SET`。Redis 8.x 默认也常用混合持久化：AOF 文件前半段是 RDB 格式，后半段追加增量命令，兼顾恢复速度和安全。”

## 3. 项目实战

### 3.1 准备持久化目录

使用 Docker 启动一个带数据卷的 Redis：

```bash
mkdir -p ./redis-lab-22-data
docker run --name redis-lab-22 -p 6379:6379 -v "$PWD/redis-lab-22-data:/data" -d redis:8.6 redis-server --dir /data
```

写入业务数据：

```bash
docker exec -it redis-lab-22 redis-cli
SET stock:sku:1001 100
ZADD rank:activity:202604 80 user:1 95 user:2
HSET order:brief:1001 status pending amount 99
DBSIZE
```

### 3.2 测试 RDB 快照

手动触发后台快照：

```bash
BGSAVE
LASTSAVE
CONFIG GET save
```

配置片段示例：

```conf
save 900 1
save 300 10
save 60 10000
dir /data
dbfilename dump.rdb
```

模拟异常退出：

```bash
docker kill redis-lab-22
docker start redis-lab-22
docker exec -it redis-lab-22 redis-cli GET stock:sku:1001
```

如果数据在最近一次快照前已经写入，就能恢复；快照后又写入但没有生成新 RDB 的数据，可能丢失。

### 3.3 开启 AOF

执行：

```bash
CONFIG SET appendonly yes
CONFIG SET appendfsync everysec
CONFIG GET appendonly
CONFIG GET appendfsync
```

生产配置片段：

```conf
appendonly yes
appendfilename appendonly.aof
appendfsync everysec
auto-aof-rewrite-percentage 100
auto-aof-rewrite-min-size 64mb
aof-use-rdb-preamble yes
```

继续写入几条数据：

```bash
INCRBY stock:sku:1001 -3
ZINCRBY rank:activity:202604 10 user:1
SET ops:last_write "aof-enabled"
BGREWRITEAOF
```

再次杀进程并重启，验证：

```bash
docker kill redis-lab-22
docker start redis-lab-22
docker exec -it redis-lab-22 redis-cli MGET stock:sku:1001 ops:last_write
```

### 3.4 恢复演练流程

业务恢复不要只写“有备份”三个字，应形成流程：

```text
停止 Redis 写入入口
  -> 备份当前损坏目录
  -> 选择最近 RDB/AOF 备份
  -> 在隔离环境启动 Redis 校验 key 数量和核心样本
  -> 替换生产数据目录
  -> 启动服务并观察日志、延迟、业务校验
```

常用检查命令：

```bash
INFO persistence
CONFIG GET dir
CONFIG GET appendonly
DBSIZE
SCAN 0 MATCH stock:* COUNT 100
```

### 3.5 常见坑

第一，容器没挂载数据卷，Redis 重启能恢复，容器删除后数据却没了。

第二，`BGSAVE` 依赖 `fork`，内存很大时可能出现延迟抖动或因为系统内存不足失败。

第三，AOF `everysec` 不是零丢失承诺。操作系统、磁盘缓存和断电都会影响最后一秒数据。

第四，备份不等于可恢复。必须定期把备份拿到隔离环境启动验证。

第五，不要把 Redis 持久化当作关系数据库事务日志。核心订单、账务、支付状态仍应以数据库为准。

## 4. 项目总结

RDB 适合做周期快照、冷备和快速恢复，缺点是快照间隔内可能丢数据；AOF 适合降低丢失窗口，缺点是写放大、文件重写和刷盘策略会影响性能。两者同时开启是常见生产选择，Redis 恢复时通常优先使用 AOF，因为它包含更完整的写入历史。

持久化设计要回答四个问题：能丢多久的数据、多久必须恢复、磁盘能承受多大写入、恢复流程是否演练过。没有这些答案，只打开 `appendonly yes` 仍然不算可靠。

适用场景：排行榜、会话状态、库存预热、可由数据库补偿的实时状态。不适用场景：唯一账本、强一致交易流水、不可重复生成的关键数据。

思考题：
1. 如果业务要求最多丢 1 秒数据，你会选择什么 AOF 策略，还需要哪些外部保障？
2. Redis 内存 80GB 时执行 `BGSAVE` 可能遇到哪些系统层问题，如何提前压测？
