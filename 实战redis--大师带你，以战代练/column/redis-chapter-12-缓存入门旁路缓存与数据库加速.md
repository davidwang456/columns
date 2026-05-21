# 第12章：缓存入门：旁路缓存与数据库加速

## 1. 项目背景

商品详情页是电商系统最典型的读多写少接口。用户进入首页、搜索页、活动页后都会点击商品详情，接口需要查询商品主表、价格表、库存快照、店铺信息和营销标签。刚开始日访问量不高，数据库还能扛住；促销活动一来，热门商品被反复查询，数据库连接数打满，接口 P95 延迟从 80 毫秒涨到 900 毫秒，页面还会偶发超时。

团队决定把 Redis 接入读路径，但“加缓存”并不等于随便 `GET`、`SET`。缓存数据从哪里来？更新商品后怎么失效？缓存穿透、击穿、雪崩怎么处理？本地缓存和 Redis 缓存如何协作？这些问题如果不先讲清楚，缓存可能从加速器变成故障放大器。

本章采用最常见的 Cache Aside，也叫旁路缓存模式：应用先查 Redis，未命中再查数据库，然后把结果写回 Redis。我们会给商品详情接口增加缓存，设计 TTL 和空值缓存，加入互斥重建的简化流程，并用命令观察命中、过期和更新行为。

## 2. 项目设计

小胖兴奋地说：“数据库慢，那我把商品都塞 Redis 里。以后只查 Redis，数据库下班！”

小白皱眉：“商品价格、上下架状态、库存都可能变化。如果 Redis 里是旧数据，用户看到 299 元，下单时数据库是 399 元，谁来背锅？”

大师笑了笑：“缓存不是让数据库下班，而是帮数据库挡住重复读。旁路缓存的主语仍然是应用：读请求先查缓存，缓存没有就查数据库，再写缓存；写请求先更新数据库，再删除或更新缓存。对商品详情这类读多写少场景，通常选择删除缓存，让下一次读重新加载。”

技术映射：Cache Aside 是应用层缓存模式，Redis 不自动知道数据库变化，缓存一致性要由业务流程维护。

小胖问：“那缓存没命中时不还是要查数据库吗？热门商品过期的一瞬间，所有请求都去查库怎么办？”

小白补充：“这就是缓存击穿。还有不存在的商品 ID 被恶意刷，会形成缓存穿透；大量 key 同时过期，就是缓存雪崩。”

大师在白板画了三条线：“穿透解决思路是参数校验、布隆过滤器或空值缓存；击穿解决思路是热点 key 互斥重建或逻辑过期；雪崩解决思路是 TTL 随机化和分批预热。基础阶段先掌握空值缓存、TTL 抖动和简单互斥锁。”

技术映射：缓存问题不是 Redis 单点问题，而是流量、TTL、数据库和业务容错共同作用的结果。

小胖又问：“本地缓存更快，为啥不用进程内 Map？”

小白说：“本地缓存每个实例一份，更新难同步，容量也受应用进程限制。Redis 是集中式缓存，跨实例共享，但多一次网络访问。”

大师总结：“热点极高、可容忍短暂不一致的数据，可以本地缓存加 Redis 二级缓存；多数新手项目先从 Redis 旁路缓存开始。不要一开始就堆多级缓存，先把命中率、TTL、序列化和失效流程跑通。”

技术映射：缓存层级越多，一致性和排查成本越高；基础架构先求清晰。

## 3. 项目实战

### 3.1 准备商品缓存

启动 Redis：

```bash
docker run --name redis-lab-12 -p 6379:6379 -d redis:8.6
docker exec -it redis-lab-12 redis-cli
```

模拟商品缓存：

```bash
SET mall:cache:product:1001 '{"id":1001,"name":"机械键盘","price":299,"stock":88}' EX 660
GET mall:cache:product:1001
TTL mall:cache:product:1001
DEL mall:cache:product:1001
GET mall:cache:product:1001
```

`DEL` 后再次 `GET` 返回空，代表缓存未命中，应用应该回源数据库。

### 3.2 旁路缓存流程

商品详情接口伪代码：

```text
function getProductDetail(productId):
    if productId is invalid:
        return error("非法商品 ID")

    key = "mall:cache:product:" + productId
    cached = redis.GET(key)
    if cached exists:
        if cached == "__NULL__":
            return notFound()
        return deserialize(cached)

    product = database.queryProduct(productId)
    if product not exists:
        redis.SET(key, "__NULL__", EX, 60)
        return notFound()

    ttl = 600 + random(0, 120)
    redis.SET(key, serialize(product), EX, ttl)
    return product
```

这里包含三个基础决策：非法 ID 先拦截，避免打到 Redis 和数据库；不存在的商品写短 TTL 空值，降低穿透风险；正常商品 TTL 加随机抖动，降低雪崩概率。

商品更新伪代码：

```text
function updateProduct(productId, updateCommand):
    database.begin()
    database.updateProduct(productId, updateCommand)
    database.commit()

    redis.DEL("mall:cache:product:" + productId)
```

为什么常见做法是“更新数据库后删除缓存”？因为直接更新缓存可能遗漏聚合字段、序列化格式或关联表变化。删除缓存让下一次读取重新组装数据，简单且不容易写错。

### 3.3 简化互斥重建

热门商品过期时，可以加一个短锁：

```text
function getProductDetailWithMutex(productId):
    key = "mall:cache:product:" + productId
    cached = redis.GET(key)
    if cached exists:
        return parseOrNotFound(cached)

    lockKey = "mall:lock:rebuild:product:" + productId
    locked = redis.SET(lockKey, requestId, NX, EX, 5)
    if locked:
        try:
            product = database.queryProduct(productId)
            redis.SET(key, serializeOrNull(product), EX, randomTtl())
            return product
        finally:
            if redis.GET(lockKey) == requestId:
                redis.DEL(lockKey)
    else:
        sleep(50ms)
        retry getProductDetail(productId)
```

对应 Redis 命令：

```bash
SET mall:lock:rebuild:product:1001 req-001 NX EX 5
GET mall:lock:rebuild:product:1001
DEL mall:lock:rebuild:product:1001
```

注意最后的“判断 requestId 再删除”在并发下应使用 Lua 保证原子性，第 13 章会专门实现。

### 3.4 观察命中率

Redis 的 `INFO stats` 可以看到全局命中情况：

```bash
INFO stats
```

重点关注：

```text
keyspace_hits: 命中次数
keyspace_misses: 未命中次数
```

命中率可粗略计算为：

```text
hit_rate = keyspace_hits / (keyspace_hits + keyspace_misses)
```

业务侧还应记录接口维度指标：商品详情 QPS、P95 延迟、数据库查询次数、缓存命中率、缓存重建耗时。只看 Redis 全局命中率不够，因为验证码、购物车、商品缓存会混在一起。

### 3.5 常见坑

第一，缓存对象太大。商品详情里不要塞完整评论列表、推荐列表和大段富文本，value 过大会增加网络耗时和内存碎片。

第二，缓存空值时间太长。不存在商品可能稍后创建，空值 TTL 建议短一些，比如 30 到 120 秒。

第三，先删缓存再更新数据库。并发读可能在数据库更新前把旧值重新写入缓存，导致旧数据存活一个 TTL。

第四，把缓存一致性理解成强一致。旁路缓存通常是最终一致，关键交易状态不要只依赖缓存判断。

## 4. 项目总结

本章完成了商品详情接口的基础缓存化。旁路缓存的优点是简单、通用、对业务侵入可控；缺点是应用要自己处理回源、失效、穿透、击穿和雪崩。对于读多写少、允许短暂不一致的数据，Redis 缓存能显著降低数据库压力；对于支付状态、库存扣减这类强一致链路，缓存只能辅助展示或限流，不能替代数据库事务。

适用场景：
- 商品详情、店铺资料、配置项等读多写少数据。
- 热点活动页、营销标签、频道页聚合数据。
- 查询成本高但可短暂不一致的接口。

不适用场景：
- 写入频繁且必须立即一致的数据。
- 复杂条件查询直接缓存全量结果。
- 缓存无法回源恢复的核心事实数据。

思考题：
1. 商品更新后是删除缓存、更新缓存，还是先写缓存再写数据库？请分别说明风险。
2. 如果某个商品成为超级热点，简单互斥锁仍然导致大量请求等待，还有哪些优化方向？

推广建议：开发团队先统一旁路缓存模板；测试团队重点压测命中、未命中、过期瞬间和数据库异常；运维团队要把命中率、Redis 延迟、数据库查询量放在同一张监控图里看。
