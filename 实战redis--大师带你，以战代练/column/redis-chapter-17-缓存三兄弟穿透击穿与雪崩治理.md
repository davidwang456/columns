# 第17章：缓存三兄弟穿透击穿与雪崩治理

## 1. 项目背景

电商系统上线一周后，商品详情接口的平均延迟很好看，但某天大促预热时数据库突然被打满。排查发现有三类流量同时出现：第一类是恶意请求不存在的商品 ID，缓存查不到，数据库也查不到；第二类是首页爆款商品缓存刚好过期，几千个请求同时回源；第三类是凌晨批量预热时给一批 key 设置了相同 TTL，半小时后一起失效。这就是缓存系统最常见的“三兄弟”：穿透、击穿和雪崩。

缓存穿透的本质是“请求绕过缓存持续打到数据库”，常见原因是无效参数、攻击流量或数据库确实不存在。缓存击穿的本质是“单个热点 key 过期后大量请求同时回源”。缓存雪崩则是“大量 key 在同一时间不可用”，可能是集中过期，也可能是 Redis 故障、网络抖动或容量淘汰。

本章实战目标是围绕商品详情接口做三层治理：用参数校验和空值缓存拦住低成本穿透，用 Bloom Filter 拦住明显不存在 ID，用互斥锁或逻辑过期保护热点 key，用 TTL 随机化、多级缓存和降级策略降低雪崩影响。理论不展开成论文，我们重点看命令、流程和事故复盘。

## 2. 项目设计

小胖皱着眉说：“用户查不存在的商品，不就是返回空吗？为什么会把数据库打挂？”

小白解释：“如果缓存里没有 `product:99999999`，每次都去查库，攻击者只要不断换 ID，缓存永远帮不上忙。空结果也应该被缓存一小段时间。”

大师写下第一条规则：“技术映射：穿透治理先做参数校验，再做空值缓存，流量更大时加 Bloom Filter。不要让明显非法请求进入数据库层。”

小胖又问：“那爆款商品是真实存在的呀，为什么也会出事故？”

小白说：“热点 key 一过期，第一批请求都发现缓存 miss，然后一起查数据库。它不是不存在，而是同一瞬间没人守门。”

大师点头：“这叫击穿。常见方案有互斥锁和逻辑过期。互斥锁让一个请求回源，其他请求等待或返回旧值；逻辑过期让缓存物理上不过期，只在 value 里写过期时间，后台异步刷新。技术映射：击穿治理的关键是控制回源并发。”

小胖接着问：“雪崩听起来像一大片商品同时没缓存，是不是把 TTL 都改长就行？”

小白摇头：“改长只能推迟问题。相同 TTL、Redis 重启、主从切换、网络异常都可能造成大面积不可用。还要有随机 TTL、本地缓存、限流、熔断和兜底数据。”

大师补充：“雪崩不是一个 key 的问题，而是系统可用性问题。设计时要问：Redis 短暂不可用时，哪些接口必须降级返回？哪些可以直接失败？哪些要走本地缓存？”

小胖总结：“穿透像有人拿假饭票排队，击穿像全校都抢一份红烧肉，雪崩像食堂窗口同时关门。”

大师笑着说：“比喻可以。技术映射：穿透看请求合法性，击穿看热点回源并发，雪崩看缓存层整体可用性。”

## 3. 项目实战

先准备商品缓存：

```bash
docker run --name redis-cache-17 -p 6379:6379 -d redis:8.6
docker exec -it redis-cache-17 redis-cli
SET mall:cache:product:1001 "{\"id\":1001,\"name\":\"机械键盘\"}" EX 600
```

第一步，参数校验和空值缓存。商品 ID 必须是正整数，长度要有限制。查库为空时，不要什么都不写：

```bash
SET mall:cache:product:999999 "{}" EX 60
GET mall:cache:product:999999
```

伪代码：

```text
getProduct(id):
  if id <= 0 or id too long:
    return bad_request
  value = GET mall:cache:product:{id}
  if value == "{}":
    return null
  if value exists:
    return deserialize(value)
  product = queryDb(id)
  if product not exists:
    SET mall:cache:product:{id} "{}" EX 60
    return null
  SET mall:cache:product:{id} json(product) EX random(600, 1800)
  return product
```

第二步，用 Bloom Filter 思路拦截不存在 ID。原生 Redis 不自带 Bloom 命令，若安装 RedisBloom 模块可用：

```bash
BF.RESERVE mall:bf:product 0.01 1000000
BF.ADD mall:bf:product 1001
BF.EXISTS mall:bf:product 1001
BF.EXISTS mall:bf:product 999999
```

没有模块时，学习阶段可以先用 Set 模拟白名单，但要知道 Set 是精确存储，内存成本更高：

```bash
SADD mall:product:ids 1001 1002 1003
SISMEMBER mall:product:ids 999999
```

第三步，互斥锁防击穿。只有拿到锁的请求回源数据库：

```bash
SET mall:lock:rebuild:product:1001 request-abc NX PX 3000
```

释放锁必须校验 value，避免误删别人的锁：

```bash
EVAL "if redis.call('GET',KEYS[1]) == ARGV[1] then return redis.call('DEL',KEYS[1]) else return 0 end" 1 mall:lock:rebuild:product:1001 request-abc
```

流程：

```text
if cache miss:
  token = uuid
  if SET lock token NX PX 3000 success:
    product = queryDb()
    SET cache product EX random(600, 1800)
    release lock by lua
  else:
    sleep 50ms
    retry GET cache
```

第四步，逻辑过期保护超级热点。value 内带 `expireAt`，Redis key 不设置短 TTL：

```bash
SET mall:hot:product:1001 "{\"data\":{\"id\":1001},\"expireAt\":1777550000}"
GET mall:hot:product:1001
```

伪代码：

```text
value = GET hotKey
if value.expireAt > now:
  return value.data
if tryLock(refreshKey):
  async rebuild cache
return value.data
```

第五步，雪崩治理。写缓存时加入随机 TTL：

```text
ttl = baseTtl + random(0, 300)
SET mall:cache:product:{id} json EX ttl
```

同时准备降级策略：商品详情可以返回本地缓存的旧数据；推荐列表可以返回静态榜单；非核心装饰信息可以隐藏。事故复盘时记录：影响接口、命中率变化、数据库 QPS、Redis 是否重启、是否存在同批 TTL、是否有热点 key。

常见坑：空值缓存 TTL 不能太长，否则新商品入库后可能仍返回空；Bloom Filter 有误判率，不能当权限系统；互斥锁等待线程不能无限堆积；逻辑过期会短暂返回旧数据，必须让产品和业务接受这个边界。

## 4. 项目总结

穿透、击穿和雪崩不是三个孤立名词，而是缓存系统在不同压力形态下的失败方式。穿透要减少无效回源，击穿要限制热点回源并发，雪崩要提升缓存层整体韧性。

优点：空值缓存实现简单，见效快；互斥锁适合多数热点重建；逻辑过期能保护超级热点；随机 TTL 和降级策略能减少大面积故障。缺点：空值可能污染结果，锁会带来等待和超时，逻辑过期牺牲强实时，Bloom Filter 需要维护初始化和更新链路。

思考题：
1. 如果商品刚创建，但空值缓存还没过期，怎样让新商品立即可见？
2. 逻辑过期返回旧数据和互斥锁等待新数据，分别适合哪些业务？

推广建议：开发要在代码模板里固化缓存治理流程；测试要构造不存在 ID、热点过期和批量过期场景；运维要监控命中率、数据库 QPS、热点 key 和 Redis 可用性。缓存事故不是 Redis 一个人的问题，而是接口、数据库、限流和降级共同承担的系统问题。
