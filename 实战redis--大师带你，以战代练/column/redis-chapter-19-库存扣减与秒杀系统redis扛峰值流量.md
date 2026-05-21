# 第19章：库存扣减与秒杀系统redis扛峰值流量

## 1. 项目背景

秒杀系统是 Redis 最典型的高并发实战场景。平时商品详情接口每秒几百请求，大促开始的一瞬间可能变成每秒几万请求。如果所有请求都直接查数据库、扣库存、写订单，数据库会在活动开始前几秒被打满。更糟糕的是，并发扣减如果没有原子保护，还可能出现超卖；如果保护过度，又可能大量少卖，库存明明还有却没人买到。

本章要搭建一个简化秒杀链路：活动开始前把库存预热到 Redis；请求进来先做资格校验、防刷和限购；通过 Lua 原子判断库存并扣减；成功请求写入 Stream 或 List 队列；后台消费者异步落库创建订单；最后用订单号、用户 ID 和请求 ID 追踪链路。这里的重点不是把所有细节做成生产系统，而是理解 Redis 为什么能扛峰值：它把高频、短路径、强竞争的库存判断前置到内存，并用原子脚本减少数据库写压力。

验收目标：库存不为负，不重复下单，失败原因可解释，成功请求可追踪，压测时数据库不直接承接全部流量。我们会用命令和伪代码模拟 1 万并发前的核心逻辑。

## 2. 项目设计

小胖一上来就说：“秒杀不就是 `DECR stock` 吗？库存从 100 减到 0，谁抢到算谁的。”

小白立刻追问：“如果一个用户刷 100 次怎么办？如果库存扣了但订单没创建怎么办？如果活动没开始就请求进来了怎么办？”

大师画出链路：“技术映射：秒杀不是一个扣库存命令，而是资格校验、限购、防刷、库存扣减、排队下单、落库补偿的组合。Redis 负责前置削峰，不负责吞掉所有业务一致性问题。”

小胖问：“那为什么一定要 Lua？`GET stock` 看一下，大于 0 再 `DECR` 不行吗？”

小白回答：“两个请求可能同时看到库存为 1，然后都去扣。Redis 单条命令是原子的，但多条命令组合不是。”

大师点头：“用 Lua 把校验、限购和扣减放进一个原子脚本。技术映射：脚本执行期间不会被其他命令插入，适合短小、确定、无外部调用的业务判断。”

小胖又问：“扣完库存就算下单成功吗？”

小白说：“不能。Redis 扣减只是抢到资格，订单还要落数据库。否则 Redis 成功、数据库失败时，用户会看到不一致。”

大师补充：“所以返回给用户的状态最好是‘抢购成功，订单处理中’。后台消费者落库成功后再变成正式订单。如果落库失败，要有重试、死信和库存补偿策略。”

小胖最后问：“防刷放哪里？”

大师说：“越靠前越好。用户登录态、活动时间、IP 或用户限流、黑名单、验证码都要先挡住无效流量。Redis 的计数器和 Set 可以做第一层，复杂风控交给专门系统。”

## 3. 项目实战

启动 Redis：

```bash
docker run --name redis-seckill-19 -p 6379:6379 -d redis:8.6
docker exec -it redis-seckill-19 redis-cli
```

第一步，预热活动库存和活动状态：

```bash
SET mall:seckill:activity:9001:status running EX 3600
SET mall:seckill:stock:9001:sku:1001 100
DEL mall:seckill:buyers:9001:sku:1001
```

第二步，防刷计数：

```bash
INCR mall:seckill:rate:9001:user:7
EXPIRE mall:seckill:rate:9001:user:7 10
GET mall:seckill:rate:9001:user:7
```

如果 10 秒内超过阈值，直接返回“请求过快”。

第三步，用 Lua 做库存和限购原子校验：

```bash
EVAL "local stock=tonumber(redis.call('GET',KEYS[1]) or '0'); if stock <= 0 then return -1 end; if redis.call('SISMEMBER',KEYS[2],ARGV[1]) == 1 then return -2 end; redis.call('DECR',KEYS[1]); redis.call('SADD',KEYS[2],ARGV[1]); redis.call('XADD',KEYS[3],'*','userId',ARGV[1],'skuId',ARGV[2],'requestId',ARGV[3]); return 1" 3 mall:seckill:stock:9001:sku:1001 mall:seckill:buyers:9001:sku:1001 mall:stream:seckill:orders 7 1001 req-7-001
```

返回值约定：

```text
1  = 抢购资格成功，订单处理中
-1 = 库存不足
-2 = 用户已购买
```

第四步，创建消费组处理异步下单：

```bash
XGROUP CREATE mall:stream:seckill:orders order-workers 0 MKSTREAM
XREADGROUP GROUP order-workers c1 COUNT 10 BLOCK 1000 STREAMS mall:stream:seckill:orders >
XACK mall:stream:seckill:orders order-workers 1740000000000-0
```

业务消费者伪代码：

```text
consumeSeckillOrder(message):
  requestId = message.requestId
  if exists order_request(requestId):
    XACK message
    return
  try:
    begin transaction
    insert order with unique requestId and unique(activityId, userId, skuId)
    decrease db stock snapshot or record sold count
    commit
    XACK message
  catch duplicate:
    XACK message
  catch exception:
    keep pending for retry
```

第五步，失败补偿。若消费者多次失败，可以把消息转入死信队列：

```bash
XPENDING mall:stream:seckill:orders order-workers
XAUTOCLAIM mall:stream:seckill:orders order-workers c2 60000 0-0 COUNT 10
XADD mall:stream:seckill:deadletter * reason "db_error" requestId "req-7-001"
```

压测思路：

```text
1. 预热库存 100。
2. 准备 10000 个请求，其中同一用户重复请求占一部分。
3. 并发执行 Lua 脚本。
4. 验证 GET stock >= 0。
5. 验证 SCARD buyers <= 初始库存。
6. 验证 Stream 中成功消息数等于购买资格数。
```

常见坑：Lua 脚本不能做网络调用和慢逻辑；库存 key 不要和商品详情缓存混在一起；Set 记录购买用户会随活动变大，活动结束要归档或清理；Redis 成功不等于订单成功，前端文案要表达“处理中”；数据库仍需唯一约束防重复落库；补偿库存要谨慎，避免把已成功订单补回去。

## 4. 项目总结

秒杀系统的关键是把数据库最害怕的瞬时竞争前移到 Redis，用内存计数、集合限购和 Lua 原子脚本快速筛掉无效请求，再通过消息队列异步落库。Redis 扛峰值，不代表 Redis 单独完成订单系统，而是让每一层做自己擅长的事。

优点：库存扣减路径短，吞吐高；Lua 保证限购和扣减原子性；队列削峰让数据库按能力消费；请求结果可追踪。缺点：异步下单带来最终一致性；补偿逻辑复杂；活动 key 容易成为热点；脚本写错会放大事故。

适用场景包括限量抢购、优惠券领取、报名名额抢占、抽奖资格发放。不适合长时间占库存、强实时支付确认、需要复杂组合优惠的完整交易计算。

思考题：
1. Redis 扣库存成功，但数据库创建订单连续失败，应该自动补库存还是人工复核？
2. 如果一个活动有 100 万库存，`buyers` Set 过大时可以怎样拆分或替代？

推广建议：开发负责脚本、幂等和补偿；测试负责并发、重复请求、消费者失败和边界库存；运维负责热点 key、Stream 积压、Redis CPU 和慢脚本监控。秒杀不是一个接口，而是一套从入口到落库的流量治理系统。
