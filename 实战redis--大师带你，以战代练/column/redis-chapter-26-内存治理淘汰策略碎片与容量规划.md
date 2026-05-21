# 第26章：内存治理淘汰策略碎片与容量规划

## 1. 项目背景

电商平台进入大促前，缓存团队收到一个看似简单的需求：把商品详情、库存快照、用户权益、活动资格和推荐结果都放进 Redis，目标是让核心接口 P99 延迟稳定在 20ms 内。开发同学觉得只要给 Redis 分配一台大内存机器就行，运维同学却发现测试环境已经出现 `OOM command not allowed`，部分实例的 `mem_fragmentation_ratio` 也长期高于 1.6。

这类问题的麻烦在于，它不是“内存满了就扩容”这么简单。Redis 的内存由业务 key、过期字典、客户端缓冲区、复制缓冲区、AOF rewrite 期间的额外内存和 allocator 碎片共同组成。即使 `used_memory` 没有达到物理内存上限，fork、复制、慢客户端和大 value 也可能把实例推到危险区。

本章用一个“10 亿次请求规模的商品缓存”做背景，目标是建立容量模型，配置合理的 `maxmemory` 和淘汰策略，观察碎片率，并把内存水位、淘汰数量、命中率纳入日常运维流程。理论只服务于一个问题：怎样让 Redis 在有限内存里稳定工作，而不是等线上 OOM 后再救火。

## 2. 项目设计

小胖先开球：“内存不够就加内存呗，Redis 不就是个大冰箱吗？冰箱满了再买个双开门。”

小白马上追问：“如果冰箱里有临期食品、常吃食品、会员专属食品，应该先扔谁？而且 Redis 里还有碎片，表面看只装了 80%，实际可能已经塞不进大盒子了。”

大师点头：“容量治理的第一步不是买机器，而是分类。商品详情这种可回源数据可以淘汰，支付状态这类核心数据不应该只靠 Redis 保存。技术映射就是：能丢的缓存设置 TTL，不能丢的数据要有数据库或持久化兜底，实例层面用 `maxmemory` 控制上限，用淘汰策略决定满了以后怎么处理。”

小胖又问：“淘汰策略是不是选 LRU 就完事？我看名字像最近不用就扔。”

小白补充：“Redis 还有 `noeviction`、`allkeys-lru`、`volatile-lru`、`allkeys-lfu`、`volatile-ttl`。如果 key 没有 TTL，`volatile-*` 策略是不是根本淘汰不了？”

大师回答：“对。`noeviction` 适合写入不能被悄悄丢弃的场景，满了直接报错。`allkeys-lru` 适合全量都是缓存的实例。`volatile-ttl` 只在带 TTL 的 key 中优先淘汰快过期的。LFU 关注访问频率，更适合热点稳定的商品和内容缓存。技术映射：策略必须和 key 生命周期匹配，否则看起来配置了淘汰，实际满内存仍然会失败。”

小胖看着监控又说：“那碎片率高，是不是 Redis 偷吃内存？”

小白问：“碎片是 jemalloc 分配造成的吗？`MEMORY PURGE` 和 active defrag 能解决多少？会不会影响延迟？”

大师解释：“碎片来自分配和释放不同大小对象后的空洞，也来自大 key 删除、AOF rewrite、数据冷热变化。Redis 可以启用主动碎片整理，但它会消耗 CPU，所以要在低峰期观察。容量规划要预留 20% 到 40% 安全空间，不能把 `maxmemory` 贴着物理内存配。”

## 3. 项目实战

### 3.1 最小环境

启动一个带内存限制的实验实例：

```bash
docker run --name redis-lab-26 -p 6379:6379 -d redis:8.6 \
  redis-server --maxmemory 128mb --maxmemory-policy allkeys-lfu \
  --save "" --appendonly no
```

进入命令行并观察内存：

```bash
docker exec -it redis-lab-26 redis-cli
INFO memory
CONFIG GET maxmemory
CONFIG GET maxmemory-policy
```

常用关注项包括 `used_memory_human`、`used_memory_rss_human`、`mem_fragmentation_ratio`、`evicted_keys`、`keyspace_hits`、`keyspace_misses`。

### 3.2 构造容量模型

以商品详情缓存为例：预计 500 万个热点商品，每个序列化后平均 1KB，key 平均 40 字节，Redis 对象和字典开销粗略按 80 到 120 字节估算。单副本业务数据约为：

```text
5000000 * (1024 + 40 + 120) ≈ 5.9GB
```

再乘以复制、碎片、AOF rewrite、客户端缓冲区和增长余量，生产建议至少准备：

```text
业务数据 5.9GB / 目标水位 0.7 ≈ 8.5GB
```

如果是一主两从，集群总内存还要按副本数量计算。运维流程不是拍脑袋填规格，而是先算 key 数、value 大小、TTL 分布、命中率目标和可回源能力。

### 3.3 压测淘汰策略

写入一批带 TTL 的商品缓存：

```bash
for i in $(seq 1 200000); do
  redis-cli SET "product:detail:$i" "$(printf 'x%.0s' {1..1024})" EX 3600 >/dev/null
done
```

观察是否发生淘汰：

```bash
redis-cli INFO stats | grep evicted_keys
redis-cli DBSIZE
redis-cli MEMORY STATS
```

切换策略做对比：

```bash
redis-cli CONFIG SET maxmemory-policy volatile-ttl
redis-cli CONFIG SET maxmemory-policy allkeys-lru
redis-cli CONFIG SET maxmemory-policy noeviction
```

预期现象：`allkeys-lru` 和 `allkeys-lfu` 会在内存接近上限后淘汰旧 key；`volatile-ttl` 只淘汰有过期时间的 key；`noeviction` 会让新增写入失败。测试团队要记录命中率、写入错误数和延迟变化，不能只看服务是否还活着。

### 3.4 碎片治理配置

生产配置片段可以这样起步：

```conf
maxmemory 24gb
maxmemory-policy allkeys-lfu
maxmemory-samples 10
activedefrag yes
active-defrag-ignore-bytes 100mb
active-defrag-threshold-lower 10
active-defrag-threshold-upper 100
lazyfree-lazy-eviction yes
lazyfree-lazy-expire yes
```

运维流程建议：

1. 每日统计 `used_memory`、`used_memory_rss`、碎片率、淘汰数量和命中率。
2. 每周抽样执行 `MEMORY USAGE key`，校准容量模型。
3. 大促前压测目标流量的 1.5 倍，确认回源数据库能承受淘汰后的缺口。
4. 碎片率持续高于 1.5 且 RSS 快速上升时，先确认是否有大 key 删除、AOF rewrite 或连接缓冲区异常，再考虑重启迁移。

常见坑：第一，把 `maxmemory` 设置为机器总内存，fork 或复制时直接触发系统 OOM。第二，使用 `volatile-*` 策略却大量 key 不设置 TTL。第三，只看 `used_memory`，忽略 RSS 和客户端输出缓冲区。第四，用 `KEYS *` 查内存问题，反而制造阻塞。

## 4. 项目总结

内存治理的核心是“分类、限额、观测、演练”。分类决定哪些数据可以淘汰，限额避免 Redis 吃光系统内存，观测让团队提前看到水位变化，演练证明淘汰后业务能回源。

优点：合理的淘汰策略能提升可用性，容量模型能降低扩容成本，碎片治理能减少 RSS 失控。缺点：淘汰会降低命中率，LFU/LRU 都是近似算法，active defrag 会带来额外 CPU 消耗。

适用场景包括商品详情缓存、推荐结果缓存、活动资格缓存、热点内容缓存。不适合把不可恢复的核心交易状态交给淘汰策略保护。

思考题：
1. 为什么 `used_memory` 没到物理内存上限，Redis 仍可能因为 fork 或 RSS 过高出问题？
2. 一个实例里同时放可淘汰缓存和不可淘汰业务状态，会给 `maxmemory-policy` 选择带来什么矛盾？

推广建议：开发团队负责 key 大小和 TTL 规范，测试团队负责压测不同淘汰策略，运维团队负责内存水位、碎片率和扩容阈值，架构团队负责冷热分层和实例拆分边界。
