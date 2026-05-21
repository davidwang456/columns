# 第31章：中级篇综合实战构建生产级高并发缓存平台

## 1. 项目背景

前面中级篇我们拆过缓存穿透、击穿、雪崩，做过分布式锁、秒杀库存、Stream 事件流、集群、监控和安全。本章把这些能力合在一起，做一个“千万 DAU 电商平台 Redis 缓存与实时事件平台”的交付方案。

业务方给出的目标很直接：商品详情要快，秒杀库存不能超卖，订单状态变更要可追踪，首页热榜要实时更新，故障时不能让数据库被打穿，运维要能看到命中率、慢请求、内存、主从切换和消费积压。过去团队的做法是每个业务线各建一套 Redis，key 命名不统一，TTL 靠开发拍脑袋，监控只看机器 CPU。结果大促一来，商品缓存同时过期，数据库连接池打满；库存扣减脚本没有幂等，重试时重复扣；Stream 消费者挂了半小时没人知道。

本章的综合实战目标不是“搭一个最复杂的 Redis”，而是交付一套能被开发、测试、运维共同验收的最小生产级平台：多级缓存承担读流量，Redis Cluster 承担分片与扩展，核心写路径用 Lua 保证原子性，Stream 承接订单事件，Prometheus 或 `redis_exporter` 暴露指标，ACL 划分应用权限，压测和故障演练给出可验证结论。

验收标准先写清楚：商品详情缓存命中率稳定大于 90%，核心读接口 P99 小于 20ms；秒杀库存并发扣减不超卖；订单事件至少一次投递且可补偿；单节点故障切换过程可观测；RDB/AOF 策略能让核心数据在可接受窗口内恢复。

## 2. 项目设计

小胖先把需求拍到桌上：“这不就是给电商系统加个大号 Redis 吗？商品放进去、库存放进去、订单消息也放进去，机器买大点不就完事了？”

小白摇头：“如果只是一台大 Redis，容量怎么扩？热点 key 怎么办？库存扣减是写路径，商品详情是读路径，订单事件是消息路径，它们的可靠性要求不一样，不能混在一个设计里。”

大师在白板上画了四条泳道：“第一条是商品详情读路径，使用本地缓存加 Redis 旁路缓存；第二条是秒杀库存写路径，用 Redis Lua 做预扣，再由数据库落最终账；第三条是订单事件路径，用 Stream 做状态流转和补偿；第四条是平台治理，包含 Cluster、ACL、监控、备份和演练。”

技术映射：缓存平台不是一个 Redis 实例，而是一组围绕业务 SLA 设计的访问模式、部署拓扑、治理规范和验收流程。

小胖继续问：“商品详情为什么还要本地缓存？我直接查 Redis，不是已经很快了吗？”

大师回答：“Redis 快，但网络仍然有开销。首页、商品详情这种极热读场景，可以用 Caffeine 或应用内 LRU 做 1 到 3 秒短缓存，把同一进程内的重复请求挡住。Redis 负责跨实例共享，数据库负责真相来源。多级缓存要配合短 TTL、主动失效消息和降级策略，不能让本地缓存无限相信自己。”

小白追问：“那一致性怎么保证？运营改了商品价格，本地缓存还没过期，用户看到旧价怎么办？”

大师说：“先按数据类型分级。商品标题、图片可以容忍几秒延迟；价格、库存要走更短 TTL 或主动删除；支付金额不能只依赖缓存。我们不是追求所有缓存强一致，而是明确哪些字段允许短暂旧值，哪些必须回源校验。”

技术映射：多级缓存设计的核心是数据分级、TTL 分层、失效通道和回源保护。

小胖又指着秒杀：“库存我最懂，`DECR sku:100`，扣到负数就失败，够简单吧？”

小白立刻补刀：“并发下可能要校验活动状态、用户限购、幂等请求号，还要避免重试重复扣。单个 `DECR` 不够表达这些约束。”

大师点头：“秒杀库存用 Lua 把活动状态、库存、用户购买记录、幂等号放在一次原子执行里。Redis 只做前置拦截和排队削峰，最终订单仍要落数据库。Redis 成功扣减后写入 Stream，订单服务消费并创建订单；失败或超时要有补偿脚本把预扣库存还回去。”

技术映射：Lua 解决 Redis 内部多 key 原子判断，Stream 解决后续异步事件，数据库解决最终一致和审计。

小胖最后问：“集群、哨兵、监控、ACL 这些是不是运维的事？开发只管写代码？”

大师回答：“生产级平台一定是共同交付。开发要提供 key 规范、命令复杂度、超时和降级；测试要压测缓存命中、并发库存、故障切换；运维要提供容量规划、告警阈值、备份恢复和权限边界。谁都不能只管自己那一段。”

## 3. 项目实战

### 3.1 需求拆解

把需求拆成四组可验收功能：

1. 商品缓存：`product:{id}` 保存详情 JSON，TTL 10 到 30 分钟，加随机抖动；热点商品增加本地短缓存。
2. 秒杀库存：`seckill:stock:{sku}`、`seckill:user:{sku}`、`seckill:req:{requestId}` 配合 Lua 原子校验。
3. 订单事件流：`stream:order-events` 存放预扣成功、下单成功、下单失败、补偿完成等事件。
4. 平台治理：`INFO`、`SLOWLOG`、`LATENCY DOCTOR`、`redis_exporter`、ACL、RDB/AOF、故障演练报告。

### 3.2 最小部署

本地可以先用三节点模拟核心能力，生产再扩展到多主多从 Cluster。

```bash
docker network create redis-platform
docker run -d --name redis-platform-1 --network redis-platform -p 6379:6379 redis:8.6 redis-server --appendonly yes
docker run -d --name redis-platform-2 --network redis-platform -p 6380:6379 redis:8.6 redis-server --appendonly yes
docker run -d --name redis-platform-3 --network redis-platform -p 6381:6379 redis:8.6 redis-server --appendonly yes
```

如果要演示 Cluster，可改为每个节点开启 `--cluster-enabled yes`，再执行 `redis-cli --cluster create`。本章重点是平台交付链路，单机也能完成命令、脚本、监控和验收练习。

### 3.3 商品缓存实现

伪代码如下，重点是命中统计、回源保护和 TTL 抖动：

```python
import json, random, redis

r = redis.Redis(host="localhost", port=6379, decode_responses=True)

def load_product_from_db(product_id):
    return {"id": product_id, "name": "Redis实战课", "price": 99}

def get_product(product_id):
    key = f"product:{product_id}"
    cached = r.get(key)
    if cached:
        r.incr("metric:cache:hit")
        return json.loads(cached)

    r.incr("metric:cache:miss")
    lock_key = f"lock:rebuild:{product_id}"
    if r.set(lock_key, "1", nx=True, ex=5):
        product = load_product_from_db(product_id)
        ttl = 600 + random.randint(0, 300)
        r.setex(key, ttl, json.dumps(product, ensure_ascii=False))
        r.delete(lock_key)
        return product

    # 未抢到重建锁时短暂降级，避免并发打穿数据库。
    return {"id": product_id, "degraded": True}
```

验证命令：

```bash
redis-cli GET product:1001
redis-cli MGET metric:cache:hit metric:cache:miss
redis-cli TTL product:1001
```

### 3.4 秒杀库存 Lua

脚本目标：校验幂等号、检查库存、记录用户购买、扣减库存、写入 Stream。

```lua
-- KEYS[1]=stock key, KEYS[2]=user set, KEYS[3]=request key, KEYS[4]=stream key
-- ARGV[1]=userId, ARGV[2]=requestId, ARGV[3]=ttl seconds
if redis.call("exists", KEYS[3]) == 1 then
  return "DUPLICATE"
end
if redis.call("sismember", KEYS[2], ARGV[1]) == 1 then
  return "LIMITED"
end
local stock = tonumber(redis.call("get", KEYS[1]) or "0")
if stock <= 0 then
  return "SOLD_OUT"
end
redis.call("decr", KEYS[1])
redis.call("sadd", KEYS[2], ARGV[1])
redis.call("setex", KEYS[3], tonumber(ARGV[3]), "1")
redis.call("xadd", KEYS[4], "*", "type", "PRE_DEDUCT", "userId", ARGV[1], "requestId", ARGV[2])
return "OK"
```

执行前准备：

```bash
redis-cli SET seckill:stock:1001 100
redis-cli EVAL "$(cat seckill.lua)" 4 seckill:stock:1001 seckill:user:1001 seckill:req:abc stream:order-events u001 abc 3600
redis-cli XLEN stream:order-events
```

Windows PowerShell 下可以把脚本保存为文件后用客户端库加载，或在 `redis-cli` 中逐行调试简化版。

### 3.5 监控、压测与故障演练

压测读接口：

```bash
redis-benchmark -h 127.0.0.1 -p 6379 -t get,set -n 100000 -c 100
```

观察指标：

```bash
redis-cli INFO stats
redis-cli INFO commandstats
redis-cli SLOWLOG GET 10
redis-cli LATENCY DOCTOR
redis-cli XINFO STREAM stream:order-events
```

故障演练至少包含三项：停止一个 Redis 节点，确认应用超时和降级日志；制造缓存集中过期，观察数据库回源是否被锁保护；暂停订单消费者，确认 Stream 长度和消费组 pending 数被告警发现。

## 4. 项目总结

本章把中级篇能力组合成一套平台交付方案。生产级 Redis 缓存平台的价值不在于命令堆砌，而在于把读性能、写原子性、异步事件、可观测性和故障恢复串成闭环。

优点：多级缓存能显著降低数据库压力；Lua 让复杂库存校验具备原子性；Stream 让订单事件可追踪；监控和演练让平台具备可运营性。缺点也明显：链路更长，排障复杂度更高；多级缓存带来一致性取舍；Cluster、ACL、持久化和告警需要持续治理。

适用场景包括高并发商品读取、秒杀预扣、实时排行榜、订单事件流和热点保护。不适合把 Redis 当作唯一订单账本，也不适合在没有监控和演练的情况下贸然承载核心交易。

最终验收清单：缓存命中率大于 90%；P99 小于 20ms；库存压测不超卖；Stream 消费积压可观测；节点故障有降级和恢复记录；备份文件可恢复；应用账号通过 ACL 限制危险命令。开发、测试、运维只有围绕同一份验收清单协作，这个平台才算真的建成。
