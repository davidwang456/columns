# 第35章：RDB、AOF 与 Fork 源码链路

## 1. 项目背景

缓存平台上线后，业务团队开始问一个更现实的问题：Redis 重启后数据能恢复到什么程度？大促写入高峰时执行 `BGSAVE` 会不会卡住？AOF rewrite 为什么会让内存突然升高？一次云主机故障后，Redis 从 AOF 恢复耗时过长，订单事件流延迟积压，开发、测试、运维都需要知道持久化链路到底发生了什么。

Redis 的持久化常见有两条线：RDB 快照和 AOF 追加日志。RDB 像定期拍照片，恢复快、文件紧凑，但两次快照之间的数据可能丢失；AOF 像记录每一步操作，数据丢失窗口更小，但文件更大，恢复时需要重放命令。现代 Redis 还支持 AOF rewrite 和混合持久化，用 RDB 前缀加增量 AOF 平衡恢复速度与安全性。

真正影响生产性能的关键点是 `fork`。`BGSAVE` 和 AOF rewrite 通常由子进程完成，父进程继续处理请求。看起来很优雅，但操作系统的 copy-on-write 会让高写入期间产生额外内存开销；如果 fork 本身耗时过长，也会造成主线程短暂停顿。很多“Redis 持久化导致卡顿”的故障，本质上是数据量、写入速率、内存碎片、磁盘 IO 和 fork 成本叠加。

本章围绕一个订单事件平台做源码实战：打开 RDB 与 AOF，制造持续写入，分别执行 `BGSAVE` 和 `BGREWRITEAOF`，观察延迟、内存、fork 耗时、AOF 大小和恢复行为，再关联 `rdb.c`、`aof.c`、`server.c`、`bio.c` 等源码文件。

## 2. 项目设计

小胖先问：“RDB 和 AOF 都是备份吧？那我全开是不是最安全？”

小白说：“全开不等于没有代价。RDB 有快照窗口，AOF 有 fsync 策略和重写成本。还要看数据能不能从数据库重建，恢复时间目标是多少，允许丢多少秒数据。”

大师画了两条时间线：“RDB 是拍全家福，隔一段时间拍一张，恢复时直接拿照片；AOF 是记流水账，每个写命令都追加，恢复时从头回放。生产里常见组合是开启 AOF，设置 `appendfsync everysec`，同时保留 RDB 或混合 AOF，兼顾恢复速度和数据安全。”

技术映射：RDB 重点看 `rdbSave`、`rdbLoad`、对象序列化；AOF 重点看追加缓冲、fsync、rewrite 和加载流程。

小胖追问：“后台保存不是后台吗？为什么还会影响前台请求？”

大师回答：“后台子进程负责写文件，但创建子进程要 fork。fork 时父进程会短暂停顿，页表越大，fork 成本越高。fork 后父进程继续写数据，操作系统 copy-on-write 会复制被修改的内存页，所以高写入期间内存峰值会上升。如果可用内存不足，可能触发 swap 或 OOM。”

小白补充：“这就是为什么大促写入高峰不适合随便做重写，容量规划要预留 COW 内存。”

技术映射：fork 链路关注 `server.c` 中后台任务启动、`rdb.c`/`aof.c` 子进程逻辑，以及 `INFO persistence` 中的 `latest_fork_usec`、`rdb_bgsave_in_progress`、`aof_rewrite_in_progress`。

小胖又问：“AOF 每次写都刷盘最安全吗？”

大师说：“`appendfsync always` 最激进，但延迟成本高；`everysec` 通常是工程折中，最多丢约 1 秒；`no` 交给操作系统，性能好但风险大。安全不是单看 Redis，还要看磁盘、文件系统、云盘 SLA、主从复制和业务补偿。”

小白点头：“订单最终状态必须在数据库里，Redis Stream 可以持久化加补偿，但不能成为唯一审计账本。”

技术映射：持久化策略必须和业务恢复目标 RPO、恢复时间 RTO 一起设计。

## 3. 项目实战

### 3.1 源码文件与观察点

重点文件：

- `src/rdb.c`：RDB 保存、加载、对象序列化。
- `src/aof.c`：AOF 追加、fsync、rewrite、加载。
- `src/server.c`：后台保存、后台重写、fork 状态管理。
- `src/bio.c`：后台 IO 任务，例如异步关闭文件、fsync 等。
- `src/db.c`：加载和写入数据时涉及数据库操作。
- `src/config.c`：持久化配置项解析。

观察点：`BGSAVE` 如何创建子进程；子进程如何遍历数据库写 RDB；AOF 缓冲区何时追加和刷盘；rewrite 期间新增写命令如何处理；父子进程状态如何通过 `INFO persistence` 暴露。

### 3.2 启动持久化实验环境

创建目录并启动 Redis：

```bash
mkdir -p /tmp/redis-persist-lab
docker run --name redis-persist-lab -p 6379:6379 -v /tmp/redis-persist-lab:/data -d redis:8.6 redis-server --appendonly yes --appendfsync everysec --save 60 1000
```

Windows 可以把 `/tmp/redis-persist-lab` 换成本机目录挂载。启动后检查：

```bash
redis-cli CONFIG GET appendonly
redis-cli CONFIG GET appendfsync
redis-cli INFO persistence
```

### 3.3 制造写入压力

用 Python 持续写入模拟订单事件和缓存：

```python
import time, redis
r = redis.Redis(decode_responses=True)

payload = "x" * 1024
for i in range(200000):
    r.set(f"order:cache:{i}", payload, ex=3600)
    r.xadd("stream:order-events", {"orderId": i, "status": "PAID"})
    if i % 1000 == 0:
        print("written", i)
```

同时观察延迟：

```bash
redis-cli --latency
redis-cli INFO memory
redis-cli INFO persistence
```

### 3.4 执行 BGSAVE 并观察 RDB

执行：

```bash
redis-cli BGSAVE
redis-cli INFO persistence
redis-cli LASTSAVE
```

重点关注：

- `rdb_bgsave_in_progress` 是否从 1 变回 0。
- `rdb_last_bgsave_status` 是否为 `ok`。
- `latest_fork_usec` 是否异常升高。
- `rdb_last_cow_size` 是否显示明显 COW 开销。

源码对应：在 `rdb.c` 查找 `rdbSaveBackground`、`rdbSave`；在 `server.c` 查找后台子进程状态回收逻辑。

如果要增加日志，可在启动后台保存前后打印：

```c
serverLog(LL_NOTICE, "[trace] before bgsave fork");
serverLog(LL_NOTICE, "[trace] child saving rdb");
```

### 3.5 执行 AOF rewrite

执行：

```bash
redis-cli BGREWRITEAOF
redis-cli INFO persistence
```

观察：

- `aof_rewrite_in_progress`。
- `aof_last_rewrite_status`。
- `aof_current_size` 与 `aof_base_size`。
- `aof_rewrite_buffer_length` 或相关增量指标。
- `aof_last_cow_size`。

rewrite 的关键是：子进程根据当前数据生成更紧凑的新 AOF；父进程在 rewrite 期间继续接收写命令，并把增量写入缓冲；rewrite 完成后再合并切换文件。源码重点看 `rewriteAppendOnlyFileBackground`、`rewriteAppendOnlyFile`、AOF 缓冲刷盘相关函数。

### 3.6 崩溃恢复验证

写入一条标记数据：

```bash
redis-cli SET recover:marker ok
redis-cli XADD stream:order-events "*" orderId 999 status MARK
redis-cli SAVE
docker restart redis-persist-lab
redis-cli GET recover:marker
redis-cli XRANGE stream:order-events - + COUNT 3
```

再测试 AOF everysec 的边界：连续写入后立即强制杀掉容器，重启观察最后几条是否存在。这个实验只在本地做，目的是理解 `everysec` 可能存在约 1 秒窗口，不是证明它一定丢。

```bash
docker kill redis-persist-lab
docker start redis-persist-lab
redis-cli INFO persistence
```

### 3.7 常见坑

第一，持久化文件在容器里必须挂载数据卷，否则容器删除后数据也没了。

第二，`BGSAVE` 成功不代表备份可恢复，必须定期在隔离环境做恢复演练。

第三，AOF 文件变大后恢复时间可能超过业务 RTO，需要 rewrite 和混合持久化降低恢复成本。

第四，高写入期间 fork 会放大 COW 内存，生产容量要预留额外空间，不能只看平时 `used_memory`。

第五，磁盘慢会影响 AOF fsync，应用端会看到延迟抖动，要把 Redis 延迟和磁盘 IO 指标一起看。

## 4. 项目总结

本章把 RDB、AOF 和 fork 串成了一条可观察链路。RDB 负责快照，AOF 负责命令追加，rewrite 负责压缩历史，fork 负责把耗时文件写入交给子进程，copy-on-write 则是后台持久化最容易被低估的内存成本。

优点：RDB 文件紧凑、恢复快；AOF 数据丢失窗口小；混合持久化能兼顾恢复速度和安全；后台子进程让 Redis 大多数时间能继续服务。缺点：fork 可能造成短暂停顿；COW 会抬高内存峰值；AOF fsync 受磁盘影响明显；恢复时间和数据丢失窗口必须通过演练确认。

生产建议：按业务 RPO/RTO 选择策略，常见缓存实例可弱化持久化，事件流和会话类实例要更谨慎；开启数据卷和备份校验；监控 `latest_fork_usec`、`rdb_last_cow_size`、`aof_last_cow_size`、AOF 大小、rewrite 状态和磁盘延迟；避免在写入洪峰手动触发重写；把 Redis 持久化与数据库最终账本、业务补偿一起设计。

思考题：为什么开启 AOF everysec 仍然不能承诺零丢失？如果 `latest_fork_usec` 持续升高，你会从数据量、内存、系统配置和磁盘哪些方向排查？
