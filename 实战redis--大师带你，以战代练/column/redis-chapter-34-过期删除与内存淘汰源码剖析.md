# 第34章：过期删除与内存淘汰源码剖析

## 1. 项目背景

商品详情缓存设置了 30 分钟 TTL，运营以为 30 分钟后 key 会准时消失；风控黑名单设置了 5 分钟过期，测试却发现过期后偶尔还能在内存统计里看到占用；大促时 Redis 内存逼近上限，配置了 `allkeys-lru`，但业务仍然出现短暂延迟抖动。要解释这些现象，就必须理解过期删除和内存淘汰的源码链路。

Redis 的 key 过期并不是“到点立刻物理删除”。它同时使用惰性删除和定期删除：访问 key 时检查是否过期，过期则删除；后台周期性抽样扫描带 TTL 的 key，主动清理一部分。内存淘汰则是另一套机制：当写入触发内存检查，且 `used_memory` 超过 `maxmemory` 时，Redis 按配置策略选择 key 淘汰。过期删除解决“生命周期到了”，内存淘汰解决“内存不够了”，两者相关但不是一回事。

本章用一个真实场景推进：广告投放系统每天写入大量短 TTL 频控 key，活动峰值时又写入商品缓存和用户画像。团队需要知道过期 key 为什么会残留、如何观察 `expired_keys` 与 `evicted_keys`、淘汰策略如何影响业务，以及源码中哪些函数负责这些动作。

本章实战会构造大量过期 key 和内存压力，配合 `INFO stats`、`INFO memory`、`LATENCY DOCTOR`、`SLOWLOG` 观察删除与淘汰，再关联 `db.c`、`expire.c`、`evict.c` 等源码文件。

## 2. 项目设计

小胖先问：“TTL 到了还不删？这不就像外卖过期了还摆在货架上吗？”

小白解释：“逻辑上过期和物理删除是两件事。Redis 要在性能和及时性之间取舍，如果每个 key 到点都单独触发删除，定时器数量和调度成本会很高。”

大师点头：“过期删除像仓库清理。有人来取货时，仓管先看保质期，过期就扔掉，这是惰性删除。每天固定巡检一批货架，抽样清理，这是定期删除。Redis 不追求每个过期 key 在毫秒级准时释放，而是追求整体 CPU 开销可控。”

技术映射：过期时间通常存在 expires 字典中，访问时通过 `expireIfNeeded` 检查，周期清理由 `activeExpireCycle` 推进。

小胖又问：“那内存满了怎么办？是不是先删过期的？”

大师回答：“Redis 会尽量处理过期 key，但内存淘汰有自己的入口和策略。超过 `maxmemory` 后，根据 `maxmemory-policy` 选择候选 key，比如 `allkeys-lru`、`volatile-lru`、`allkeys-lfu`、`volatile-ttl`、`noeviction`。如果策略是 `volatile-*`，只从设置了过期时间的 key 中选；如果是 `allkeys-*`，所有 key 都可能被选。”

小白追问：“LRU、LFU 是精确的吗？如果不是，会不会淘汰错？”

大师说：“Redis 使用近似算法。它不会维护一个全局精确 LRU 链表，而是抽样若干 key，选出看起来最该淘汰的。这样牺牲一点精确度，换来更低成本。LFU 也不是简单计数器，而是带衰减的访问频率估计。”

技术映射：淘汰重点看 `evict.c` 的候选池、采样、策略判断，以及对象头中的 LRU/LFU 信息。

小胖继续问：“业务上怎么选策略？全都 `allkeys-lru` 行不行？”

小白说：“如果 Redis 里混了分布式锁、库存、会话、缓存，随便淘汰可能会出事故。不是所有 key 都适合被内存压力踢掉。”

大师回答：“生产建议按职责隔离实例。纯缓存实例可以选 `allkeys-lru` 或 `allkeys-lfu`；只希望淘汰有 TTL 的缓存，可以用 `volatile-lru`；核心状态不允许被淘汰，应使用 `noeviction` 并让写入方处理 OOM。策略选择本质是业务可靠性选择。”

技术映射：淘汰不是自动容灾。它只是在内存不足时减少占用，不能替代容量规划、key 分级和实例隔离。

## 3. 项目实战

### 3.1 源码文件与观察点

重点文件：

- `src/db.c`：数据库字典、key 查找、删除、过期检查入口。
- `src/expire.c`：主动过期循环，Redis 8.x 版本中过期逻辑可能拆分在该文件。
- `src/evict.c`：内存淘汰入口、策略、候选池。
- `src/server.c`：周期任务 `serverCron`，触发过期与维护工作。
- `src/object.c`：对象 LRU/LFU 元信息。
- `src/config.c`：`maxmemory` 和淘汰策略配置解析。

观察点：TTL 存在哪里；访问 key 时如何判断过期；定期删除每轮扫描多少；内存检查何时触发；淘汰候选如何采样；删除 key 是否可能带来延迟。

### 3.2 构造过期 key

启动实验 Redis：

```bash
docker run --name redis-expire-lab -p 6379:6379 -d redis:8.6
redis-cli FLUSHALL
```

写入一批短 TTL key：

```bash
for i in $(seq 1 10000); do redis-cli SET freq:$i 1 EX 5 > /dev/null; done
redis-cli DBSIZE
redis-cli INFO stats | grep expired_keys
```

PowerShell：

```powershell
1..10000 | ForEach-Object { redis-cli SET "freq:$_" 1 EX 5 | Out-Null }
redis-cli DBSIZE
redis-cli INFO stats
```

等待 10 秒后：

```bash
redis-cli DBSIZE
redis-cli INFO stats
redis-cli GET freq:1
redis-cli DBSIZE
```

预期现象：TTL 到期后，`DBSIZE` 可能不会瞬间归零；访问某个过期 key 会触发惰性删除；后台定期删除会逐步增加 `expired_keys`。实际速度与数据规模、CPU、配置和 Redis 版本有关。

源码对应：查找 `expireIfNeeded`，观察访问路径如何判断过期；查找 `activeExpireCycle`，理解后台抽样清理。

### 3.3 构造内存淘汰

配置一个较小的内存上限：

```bash
redis-cli CONFIG SET maxmemory 50mb
redis-cli CONFIG SET maxmemory-policy allkeys-lru
redis-cli CONFIG GET maxmemory-policy
```

写入大 value：

```bash
python - <<'PY'
import redis
r = redis.Redis()
value = b"x" * 10240
for i in range(10000):
    r.set(f"cache:{i}", value)
print("done")
PY
```

观察：

```bash
redis-cli INFO memory
redis-cli INFO stats | grep evicted_keys
redis-cli DBSIZE
redis-cli LATENCY DOCTOR
```

预期现象：内存达到上限后，`evicted_keys` 增加，部分旧 key 被淘汰。若策略改成 `noeviction`，写入可能返回 OOM 错误：

```bash
redis-cli CONFIG SET maxmemory-policy noeviction
redis-cli SET must:write value
```

源码对应：查找 `performEvictions`、`getMaxmemoryState`、`evictionPoolPopulate`，观察淘汰入口和候选选择。

### 3.4 对比 volatile 与 allkeys

清空数据后设置策略：

```bash
redis-cli FLUSHALL
redis-cli CONFIG SET maxmemory 50mb
redis-cli CONFIG SET maxmemory-policy volatile-lru
redis-cli SET permanent:1 keep
redis-cli SET cache:1 temp EX 3600
```

继续写入大量带 TTL 的缓存，观察被淘汰的通常只来自设置过期时间的 key。如果没有足够可淘汰 key，写入仍可能失败。这说明 `volatile-*` 不是“更安全的 allkeys”，它依赖业务正确设置 TTL。

### 3.5 日志与调试建议

源码实验可以在以下位置加日志：

```c
serverLog(LL_NOTICE, "[trace] expire key in active cycle");
serverLog(LL_NOTICE, "[trace] eviction needed, policy=%d", server.maxmemory_policy);
```

加日志时要谨慎，过期和淘汰路径非常高频，建议只在本地小数据集打开。更推荐生产使用指标：

```bash
redis-cli INFO stats
redis-cli INFO memory
redis-cli CONFIG GET maxmemory*
redis-cli LATENCY LATEST
redis-cli SLOWLOG GET 10
```

重点看 `expired_keys`、`evicted_keys`、`used_memory`、`mem_fragmentation_ratio`、`instantaneous_ops_per_sec`、`keyspace_hits`、`keyspace_misses`。

## 4. 项目总结

本章区分了两个常被混淆的机制：过期删除处理 key 生命周期，内存淘汰处理内存上限压力。过期 key 不一定到点立刻物理删除，淘汰 key 也不一定是已经过期的 key。

优点：惰性删除和定期删除让 Redis 用较低 CPU 成本管理 TTL；近似 LRU/LFU 在性能和效果之间取得平衡；多种淘汰策略能适配不同缓存场景。缺点：删除与淘汰都可能带来延迟抖动；策略选错会淘汰关键数据；只看 TTL 或只看内存都不足以判断风险。

生产建议：不同可靠性等级的数据尽量隔离实例；纯缓存实例设置合理 `maxmemory` 和淘汰策略；核心状态实例优先 `noeviction` 并监控写入失败；TTL 增加随机抖动，避免过期风暴；大 key 删除优先使用异步删除或拆分模型；告警同时覆盖 `expired_keys`、`evicted_keys`、内存使用率和 P99 延迟。

思考题：为什么 `volatile-lru` 要求 key 必须设置 TTL？如果一个热点 key 经常访问但占用巨大，近似 LRU 一定不会淘汰它吗？
