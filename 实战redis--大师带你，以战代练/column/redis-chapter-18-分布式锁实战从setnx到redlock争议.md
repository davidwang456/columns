# 第18章：分布式锁实战从setnx到redlock争议

## 1. 项目背景

订单系统最怕重复提交。用户在支付页连续点两次“提交订单”，前端重试又发一次，网关超时后客户端再次补偿，如果后端没有保护，就可能创建两张订单、扣两次优惠券、冻结两次库存。数据库唯一索引能兜底，但如果所有重复请求都打到数据库，热点活动时仍会让数据库承受无意义的并发冲击。

Redis 分布式锁常被用来做入口防重：同一个用户、同一个业务单号，在一小段时间内只允许一个请求进入核心逻辑。它的优势是简单、快、跨进程可见；它的风险也很明显：锁会过期，业务会超时，客户端可能误删别人的锁，Redis 主从切换可能带来短暂一致性问题。本章不把 Redis 锁神化，而是用订单防重复提交场景讲清楚它能解决什么、不能承诺什么。

实战目标：实现 `SET key value NX PX` 加锁、Lua 校验释放锁、业务超时保护和并发测试流程，并讨论看门狗续期与 Redlock 争议。最后给出一个工程结论：Redis 锁适合降低并发冲突和重复执行概率，但关键资金、库存和订单一致性还要依赖数据库约束、幂等表或事务消息兜底。

## 2. 项目设计

小胖先问：“锁不就是 `SETNX order:lock 1` 吗？拿到就干活，干完 `DEL`，多简单。”

小白马上指出：“如果服务拿到锁后宕机，锁不删怎么办？如果锁过期了，另一个请求拿到新锁，前一个请求后来又执行 `DEL`，是不是把别人的锁删了？”

大师写下标准形态：“技术映射：加锁要用一条原子命令 `SET key value NX PX ttl`，value 必须是唯一 token，释放锁要用 Lua 比对 token 后再删除。不能用 `SETNX` 后再单独 `EXPIRE`，中间宕机会留下死锁。”

小胖挠头：“那 TTL 设多久？设短了业务没做完，设长了失败后别人等很久。”

小白补充：“这就是锁过期和业务超时的矛盾。我们要么把核心逻辑控制在 TTL 内，要么做续期，但续期也可能把异常任务拖得更久。”

大师说：“订单防重锁不是为了包住一个十分钟事务，而是保护一个短流程。锁 TTL 应该略大于 P99 执行时间，并且业务内部要有超时控制。技术映射：锁不是垃圾桶，不能把慢业务都扔进去。”

小胖又提到：“我听过 Redlock，五个 Redis 节点投票，听起来更高级，要不要直接上？”

小白说：“Redlock 有争议吧？时钟漂移、网络分区、客户端暂停都会影响安全性。很多场景用数据库唯一约束更直接。”

大师点头：“Redlock 的价值在于降低单 Redis 实例故障导致的锁错误，但它不是强一致分布式事务。工程上先问：失败代价是什么？如果重复提交只是多一次接口调用，单实例锁加幂等表够用；如果涉及金融扣款，应以数据库事务、唯一约束和业务幂等为准，Redis 锁只做前置削峰。”

## 3. 项目实战

启动 Redis：

```bash
docker run --name redis-lock-18 -p 6379:6379 -d redis:8.6
docker exec -it redis-lock-18 redis-cli
```

第一步，用正确姿势加锁：

```bash
SET mall:lock:submit:user:7:order:20260430001 token-a NX PX 5000
GET mall:lock:submit:user:7:order:20260430001
TTL mall:lock:submit:user:7:order:20260430001
```

如果第二个请求再次加锁：

```bash
SET mall:lock:submit:user:7:order:20260430001 token-b NX PX 5000
```

预期返回空，说明锁已存在。

第二步，用 Lua 安全释放：

```bash
EVAL "if redis.call('GET',KEYS[1]) == ARGV[1] then return redis.call('DEL',KEYS[1]) else return 0 end" 1 mall:lock:submit:user:7:order:20260430001 token-a
```

如果传入 `token-b`，返回 `0`，不会误删。

第三步，订单防重伪代码：

```text
submitOrder(userId, requestId):
  lockKey = mall:lock:submit:user:{userId}:request:{requestId}
  token = uuid()
  locked = SET lockKey token NX PX 5000
  if not locked:
    return "处理中，请勿重复提交"

  try:
    if existsIdempotentRecord(requestId):
      return previousResult
    checkCouponAndStock()
    createOrderWithUniqueRequestId(requestId)
    saveIdempotentRecord(requestId, orderId)
    return orderId
  finally:
    release lock by lua with token
```

注意这里有两层保护：Redis 锁挡住短时间重复请求，数据库或幂等表用 `requestId` 做最终兜底。

第四步，模拟业务超时。先加一个短锁：

```bash
SET mall:lock:submit:user:8:req:slow token-slow NX PX 1000
```

等待 2 秒后：

```bash
GET mall:lock:submit:user:8:req:slow
SET mall:lock:submit:user:8:req:slow token-new NX PX 1000
```

这说明锁过期后别人可以进入。如果旧请求没有 token 校验直接 `DEL`，就会误删新锁。

第五步，看门狗续期思路：

```text
if lock acquired:
  start watchdog every ttl/3:
    if GET lockKey == token:
      PEXPIRE lockKey ttl
  run business
  stop watchdog
  release by lua
```

看门狗适合执行时间波动但仍可控的任务，不适合无限等待外部系统。续期线程本身也要处理进程暂停、网络抖动和释放顺序。

Redlock 简化流程如下：

```text
1. 客户端依次向 5 个独立 Redis 主节点加同一把锁。
2. 在有效时间内，超过半数节点加锁成功才认为成功。
3. 锁有效时间要扣除网络耗时和时钟漂移预算。
4. 释放时向所有节点发送 token 校验删除。
```

常见坑：不要用固定 value，比如 `1`；不要先 `SETNX` 再 `EXPIRE`；不要释放时直接 `DEL`；不要把锁 TTL 设成业务补偿时间；不要用 Redis 锁替代数据库唯一索引；不要在锁内做慢 SQL、远程大文件上传或人工审批。

## 4. 项目总结

Redis 分布式锁的核心不是 `SETNX` 三个字，而是“原子加锁、唯一标识、超时释放、校验删除、业务兜底”。它能减少重复提交、重复调度和短时并发冲突，但不能单独承担强一致承诺。

优点：性能高，实现简单，适合跨实例互斥；锁 TTL 可以避免永久死锁；Lua 释放能避免误删。缺点：锁过期和业务耗时难完全匹配；Redis 故障转移可能影响锁语义；续期和 Redlock 会提高复杂度。

适用场景包括订单防重复提交、定时任务防并发执行、缓存重建互斥、短流程资源占用。不适合长事务、资金最终一致性唯一保障、跨多资源强事务协调。

思考题：
1. 如果订单接口拿到锁后创建订单成功，但释放锁前服务宕机，后续重复请求应该如何返回同一个订单？
2. Redlock 能降低哪些风险？又不能解决哪些分布式系统问题？

推广建议：开发团队统一封装锁工具，强制 token 和 Lua 释放；测试团队压测重复请求、锁过期和服务异常；运维团队监控 Redis 延迟、主从切换和客户端超时。锁越底层越要保守，真正的业务正确性要落在幂等和数据约束上。
