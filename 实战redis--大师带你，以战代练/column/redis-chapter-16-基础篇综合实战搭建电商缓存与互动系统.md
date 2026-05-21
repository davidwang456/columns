# 第16章：基础篇综合实战搭建电商缓存与互动系统

## 1. 项目背景

前面十五章我们分别练过验证码、购物车、排行榜、签到、门店搜索、旁路缓存、Lua、事务和基础排障。单点能力都能跑通，但真实项目不会按章节出题。电商首页一打开，用户要登录，商品详情要秒开，购物车要实时显示数量，活动榜单要不断变化，门店要按距离排序，运营还希望看到签到活跃度。如果每个功能各写各的 key、TTL 和接口，很快就会变成一锅 Redis 粥。

本章要交付一个“小而全”的电商缓存与互动系统。它不追求复杂框架，而是把基础篇学过的数据结构串成一条业务链路：String 做验证码和接口计数，Hash 做购物车和商品快照，ZSet 做热销排行榜，Bitmap 做签到，GEO 做附近门店，Cache Aside 做商品详情缓存，Lua 做库存预扣的原子校验。验收目标很明确：核心接口缓存命中率超过 80%，P95 延迟低于 30ms，不能出现明显大 key、慢命令和无边界 TTL。

需求拆解如下：登录模块负责短信验证码和防刷；商品模块负责详情缓存和库存快照；购物车模块负责增删改查；互动模块负责签到、排行榜和附近门店；运维模块负责慢查询、命中率和 key 规范检查。我们会用 Redis 命令和伪代码表达完整流程，方便后续替换成 Java、Go 或 Python 客户端。

## 2. 项目设计

小胖兴奋地说：“这章像期末大作业吧？我想做一个能登录、能逛商品、能加购物车、还能看排行榜的小商城。Redis 是不是全都 `SET` 进去就行？”

小白马上拦住：“全用 String 会让结构不可读。购物车要按商品维度改数量，排行榜要排序，签到要压缩状态，门店要按经纬度查。我们应该先按业务动作选结构，再定 key 命名和 TTL。”

大师在白板写下 `mall:{module}:{id}`：“技术映射：登录验证码用 `String + EX`，购物车用 `Hash`，排行榜用 `ZSet`，签到用 `Bitmap`，附近门店用 `GEO`，商品详情用 Cache Aside。结构不是炫技，而是让业务操作变成 Redis 擅长的命令。”

小胖追问：“那需求这么多，先做哪个？我怕做到最后不知道怎么验收。”

大师把项目拆成四层：“第一层是环境，Docker 启动 Redis；第二层是 key 规范，所有 key 都带业务域和对象 ID；第三层是接口，按用户动作写；第四层是验收，用命中率、延迟、慢查询和数据正确性判断。不要只看功能通不通，还要看会不会给生产挖坑。”

小白继续问：“TTL 怎么定？验证码 5 分钟可以理解，商品详情缓存多久？排行榜和购物车要不要过期？”

大师回答：“TTL 跟数据生命周期走。验证码短 TTL；商品详情 10 到 30 分钟并加随机抖动；购物车可以 30 天续期；排行榜按活动周期设置；签到按月 key，自然按月归档。技术映射：TTL 不是随手写的数字，它是业务新鲜度、内存成本和故障恢复能力的折中。”

小胖又问：“验收标准里说命中率 > 80%，是不是只要 `INFO stats` 里 hit 多就行？”

小白补充：“还要看接口维度。商品详情命中率高，不代表购物车快；总命中率可能被无关命令稀释。”

大师点头：“所以我们既看 Redis 全局指标，也在业务伪代码里记录 `cache_hit`、`db_query`、`fallback`。综合实战要能解释每一次缓存未命中是正常冷启动、主动失效，还是设计问题。”

## 3. 项目实战

先启动实验环境：

```bash
docker run --name redis-mall-16 -p 6379:6379 -d redis:8.6
docker exec -it redis-mall-16 redis-cli
```

第一步，定义 key 规范和基础数据：

```bash
SET mall:sms:login:13800000000 "926531" EX 300
INCR mall:rate:sms:13800000000
EXPIRE mall:rate:sms:13800000000 60

HSET mall:product:1001 name "机械键盘" price 299 stock 500 category "keyboard"
SET mall:cache:product:1001 "{\"id\":1001,\"name\":\"机械键盘\",\"price\":299}" EX 1200
```

第二步，实现购物车：

```bash
HINCRBY mall:cart:user:7 1001 1
HINCRBY mall:cart:user:7 1002 2
HGETALL mall:cart:user:7
EXPIRE mall:cart:user:7 2592000
```

业务伪代码：

```text
addCart(userId, productId, count):
  assert count > 0
  HINCRBY mall:cart:user:{userId} productId count
  EXPIRE mall:cart:user:{userId} 30d
  return HGET mall:cart:user:{userId} productId
```

第三步，做互动能力：

```bash
ZINCRBY mall:rank:sales 1 1001
ZREVRANGE mall:rank:sales 0 9 WITHSCORES

SETBIT mall:signin:2026-04:user:7 29 1
BITCOUNT mall:signin:2026-04:user:7

GEOADD mall:geo:store 116.397128 39.916527 store:beijing
GEOADD mall:geo:store 121.473701 31.230416 store:shanghai
GEOSEARCH mall:geo:store FROMLONLAT 116.40 39.91 BYRADIUS 10 km WITHDIST ASC
```

第四步，接入商品详情旁路缓存：

```text
getProduct(productId):
  key = mall:cache:product:{productId}
  json = GET key
  if json exists:
    record cache_hit
    return json
  product = SELECT * FROM product WHERE id = productId
  if product not exists:
    SET key "{}" EX 60
    return null
  SET key serialize(product) EX random(600, 1800)
  return product
```

第五步，用 Lua 做一个最小库存预扣：

```bash
HSET mall:stock:sku:1001 available 500
EVAL "local s=tonumber(redis.call('HGET',KEYS[1],'available') or '0'); local n=tonumber(ARGV[1]); if s < n then return 0 end; redis.call('HINCRBY',KEYS[1],'available',-n); return 1" 1 mall:stock:sku:1001 1
```

基础压测可以先用 `redis-benchmark` 观察延迟，再用业务脚本循环调用接口：

```bash
redis-benchmark -h 127.0.0.1 -p 6379 -t get,set -n 10000 -c 50
```

验收标准：

```text
1. 商品详情缓存预热后，业务记录命中率 > 80%。
2. 常用接口 P95 延迟 < 30ms，本地实验可用脚本统计。
3. SLOWLOG GET 无明显慢命令。
4. SCAN 检查 key 命名都以 mall: 开头。
5. 购物车、签到、排行榜、附近门店命令结果符合预期。
```

常见坑有四个。第一，商品详情缓存不要永久有效，否则运营改价后会长期读旧值。第二，购物车 Hash 不要无限长，长期未登录用户要清理。第三，排行榜如果按全站维度无限累加，可能变成热 key。第四，压测不能只压 Redis 命令，要压完整接口，否则看不到序列化、数据库和网络开销。

## 4. 项目总结

本章把基础篇能力组合成了一个可验收的小项目。Redis 的价值不在于把所有数据塞进内存，而在于用合适结构承接合适动作：验证码要求短生命周期，购物车要求字段级更新，排行榜要求排序，签到要求状态压缩，附近门店要求地理搜索，商品详情要求缓存命中率。

优点：接口响应更快，数据库压力下降；数据结构清晰，命令能直接表达业务动作；每个模块都能独立验证。缺点：需要维护 key 规范和 TTL；缓存一致性要额外设计；热点 key 和大 key 会随着流量增长暴露。

思考题：
1. 如果商品价格更新后 5 秒内必须全网可见，当前旁路缓存需要怎么改？
2. 如果排行榜 `mall:rank:sales` 成为热 key，可以从业务和 Redis 两侧分别怎么拆？

推广建议：开发团队负责 key 规范、接口和缓存策略；测试团队负责并发、过期、异常和数据一致性用例；运维团队负责慢查询、内存、命中率和备份恢复检查。基础篇到这里结束，后面会进入更接近生产事故的缓存治理和高并发设计。
