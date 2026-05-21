# 第27章：性能调优从慢命令到火焰图

## 1. 项目背景

大促压测时，商品详情接口平均延迟只有 3ms，但 P99 偶尔飙到 300ms。应用日志显示数据库没有慢 SQL，网关也没有限流，最后大家把目光投向 Redis。开发同学说“Redis 很快，不可能是它”，运维同学拿出 `SLOWLOG` 后发现有 `LRANGE` 一次返回几万条，也有定时任务在高峰期执行大范围 `SCAN`。

性能调优最怕凭感觉。Redis 的瓶颈可能来自慢命令、大 key、热 key、网络 RTT、连接池不足、Pipeline 使用不当、Lua 脚本过长、持久化 fork 抖动，也可能来自宿主机 CPU、网卡、磁盘或容器限制。只盯平均值会掩盖尾延迟，只看 Redis 进程也可能漏掉客户端排队。

本章把目标设成一份可执行的性能诊断报告：先用 Redis 自带命令定位慢查询和延迟事件，再用压测工具复现，最后在 Linux 环境下引入 `perf` 和火焰图观察 CPU 热点。理论只讲必要的复杂度和尾延迟，重点放在“发现问题、复现问题、验证优化”的闭环。

## 2. 项目设计

小胖先说：“平均 3ms 不是挺好吗？偶尔慢一下就像奶茶店偶尔排队，用户忍忍不行吗？”

小白摇头：“线上看的是 P95、P99。只要 1% 请求慢，核心链路就可能被放大。更何况 Redis 是单线程执行命令，一个慢命令会挡住后面的快命令。”

大师接过话：“性能调优先分层。客户端看连接池和超时，网络看 RTT 和带宽，Redis 看命令复杂度、大 key、热 key、慢日志和延迟事件，机器层看 CPU、内存、磁盘和 fork。技术映射：不要先改配置，先用证据定位瓶颈位置。”

小胖又问：“那是不是把所有命令都 Pipeline，一次发一堆，就快了？”

小白追问：“Pipeline 减少 RTT，但如果批量太大，会不会挤爆客户端缓冲区？如果一个批次里有慢命令，是不是整批响应都慢？”

大师回答：“Pipeline 是减少网络往返，不是降低命令本身复杂度。1000 个 `GET` 可以合批，但一次 `HGETALL` 超大 Hash 还是会阻塞。技术映射：优化 RTT 用 Pipeline，优化单命令耗时要改数据结构、拆 key 或改访问模式。”

小胖看见火焰图很兴奋：“这个彩色山脉图是不是越漂亮越快？”

大师笑了：“火焰图是最后一层证据。先用 `SLOWLOG GET`、`LATENCY DOCTOR`、`INFO commandstats` 判断问题类型，再用 `redis-benchmark` 或 `memtier_benchmark` 复现。如果 CPU 异常高，再用 `perf record` 采样。不要拿火焰图替代基础监控。”

## 3. 项目实战

### 3.1 启动实验环境

```bash
docker run --name redis-lab-27 -p 6379:6379 -d redis:8.6 \
  redis-server --slowlog-log-slower-than 10000 --slowlog-max-len 256 \
  --latency-monitor-threshold 50
```

基础检查：

```bash
redis-cli PING
redis-cli CONFIG GET slowlog-log-slower-than
redis-cli INFO commandstats
```

`slowlog-log-slower-than 10000` 表示超过 10ms 的命令进入慢日志，单位是微秒。生产环境阈值要结合业务 SLO 调整，核心缓存链路通常要更敏感。

### 3.2 构造慢命令和大 key

写入一个大 List：

```bash
for i in $(seq 1 100000); do
  redis-cli RPUSH feed:user:10086 "msg-$i" >/dev/null
done
redis-cli LRANGE feed:user:10086 0 -1 >/dev/null
redis-cli SLOWLOG GET 5
```

优化方式不是调大慢日志阈值，而是改变访问方式：

```bash
redis-cli LRANGE feed:user:10086 0 99
redis-cli LTRIM feed:user:10086 0 999
redis-cli MEMORY USAGE feed:user:10086
```

业务上只展示最近 100 条，就不应该每次返回全部历史。对 Feed、日志、消息列表这类场景，要设置长度上限，并把归档数据转移到数据库或对象存储。

### 3.3 观察延迟事件

```bash
redis-cli LATENCY LATEST
redis-cli LATENCY DOCTOR
redis-cli INFO stats
redis-cli INFO persistence
```

如果 `LATENCY DOCTOR` 提示 fork、command、expire-cycle 等事件，要结合时间点排查。比如 `BGSAVE` 或 AOF rewrite 期间，写入越多，copy-on-write 带来的内存和延迟风险越高。

常用运维流程：

1. 先看业务 P95/P99 是否异常，并确认是否集中在 Redis 调用。
2. 查 `SLOWLOG GET` 和 `INFO commandstats`，找高耗时命令。
3. 查 `MEMORY USAGE`、`--bigkeys`、`--hotkeys`，定位大 key 或热 key。
4. 查客户端连接池等待时间、超时和重试。
5. 复现后再调整数据结构、Pipeline、实例拆分或配置。

### 3.4 压测优化前后

用内置工具做基线：

```bash
redis-benchmark -h 127.0.0.1 -p 6379 -t get,set -n 100000 -c 100 -P 16
```

如果安装了 memtier：

```bash
memtier_benchmark --server=127.0.0.1 --port=6379 \
  --clients=20 --threads=4 --requests=10000 --pipeline=16 \
  --ratio=1:10 --key-pattern=R:R
```

记录报告时不要只写 QPS，要写 P50、P95、P99、错误率、CPU、网络流量、命中率和 Redis 版本。优化前后的结论应像这样：把 `LRANGE 0 -1` 改为分页后，慢日志数量从每分钟 120 条降为 0，P99 从 280ms 降到 18ms，接口功能不变。

### 3.5 火焰图采样

在 Linux 主机上可以这样采样 Redis 进程：

```bash
pid=$(pidof redis-server)
sudo perf record -F 99 -p $pid -g -- sleep 30
sudo perf script > out.perf
```

配合 FlameGraph 工具生成图：

```bash
stackcollapse-perf.pl out.perf > out.folded
flamegraph.pl out.folded > redis.svg
```

常见坑：第一，压测机和 Redis 在同一台机器上，压测结果被客户端 CPU 污染。第二，用 `MONITOR` 长时间排查线上问题，它本身会制造压力。第三，把连接池最大连接数调得过大，导致 Redis 连接数和上下文开销上升。第四，只优化 Redis，忘记网关超时、客户端重试会放大流量。

## 4. 项目总结

Redis 性能调优的路线是：先用指标确认问题，再用命令定位类型，然后复现并验证优化。慢命令治理关注复杂度和返回数据量，网络优化关注 RTT 与 Pipeline，尾延迟治理关注 P99、fork、阻塞命令和客户端排队。

优点：Redis 工具链完整，很多问题可以用内置命令快速定位；压测复现成本低；火焰图能在复杂 CPU 问题上给出证据。缺点：压测结果容易受环境影响，火焰图需要 Linux 工具基础，错误的 Pipeline 或连接池配置会带来反效果。

适用场景包括大促前压测、接口尾延迟治理、大 key 专项、热点 key 拆分和版本升级验证。不适合在缺少监控和业务上下文时盲目套用所谓最佳配置。

思考题：
1. 为什么平均延迟正常时，仍然必须关注 P99？
2. Pipeline 能降低网络 RTT，为什么不能解决 `HGETALL` 超大 Hash 的阻塞问题？

推广建议：开发团队要提供 key 访问模式和命令清单，测试团队要设计混合读写压测，运维团队要沉淀慢查询和火焰图采样 SOP，架构团队要决定是否拆分实例或引入多级缓存。
