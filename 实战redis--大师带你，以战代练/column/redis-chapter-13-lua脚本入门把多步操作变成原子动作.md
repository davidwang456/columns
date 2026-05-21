# 第13章：Lua 脚本入门：把多步操作变成原子动作

## 1. 项目背景

秒杀活动上线前，商品服务准备把库存放进 Redis。最初的实现很直观：先 `GET` 库存，判断大于 0，再 `DECR`，最后记录用户已购买。单线程测试没有问题，并发压测一跑，库存偶尔会扣成负数，或者同一个用户快速点击多次买到多份。原因也很简单：单条 Redis 命令是原子的，但“读取库存、判断资格、扣减库存、记录购买”这一组动作不是原子的。

如果把所有逻辑都搬回数据库，可以依赖事务和行锁，但高并发秒杀会让数据库承受巨大压力。Redis 的 Lua 脚本正适合处理这种“多个 Redis 命令必须一起成功或一起失败”的轻量业务规则。脚本在 Redis 服务端执行，中途不会被其他命令插入，能把多步操作变成一个原子动作。

本章用 Lua 实现库存扣减和限购检查。我们会学习 `EVAL`、`SCRIPT LOAD`、`EVALSHA` 的基本用法，传递 `KEYS` 和 `ARGV`，返回业务状态码，并讨论脚本执行时间、阻塞风险和 Redis Function 的演进关系。

## 2. 项目设计

小胖拍着桌子：“Redis 不是单线程吗？我先 `GET stock` 再 `DECR stock`，不也排队执行吗，怎么还会超卖？”

小白解释：“单条命令排队没错，但你的业务动作分成多条命令。两个请求都可能先看到库存是 1，然后都继续扣减。队列保证命令顺序，不保证你的客户端逻辑整体原子。”

大师补充：“要把判断和修改放到 Redis 内部一次执行。Lua 脚本像是把一张小纸条递给 Redis：你按纸条上的步骤连续做完，中间不要插入别人的动作。”

技术映射：Redis 执行 Lua 脚本期间不会穿插其他命令，因此脚本内的多个 Redis 调用具备原子性。

小胖问：“那我是不是可以把整套下单流程都写进 Lua？查库存、查优惠券、算价格、写订单，全塞进去。”

小白马上摇头：“Lua 在 Redis 主线程执行，脚本太慢会阻塞所有请求。它还不适合访问外部数据库。”

大师点头：“Lua 适合短小、确定、只操作 Redis 数据的规则，比如扣库存、限购、释放锁、复合计数器。不适合复杂计算、长循环、网络 IO。脚本必须有边界意识。”

技术映射：脚本越长，阻塞风险越大；Redis 7 以后有 Function 能管理服务端逻辑，但基础原理仍是把逻辑靠近数据执行。

小胖继续问：“脚本怎么传参数？直接拼字符串是不是最快？”

小白说：“拼字符串容易注入和难维护。Redis 规定 key 放在 `KEYS`，普通参数放在 `ARGV`，集群模式下也需要明确 key。”

大师总结：“脚本要像接口一样设计：输入哪些 key、哪些参数，返回哪些状态码，都要约定清楚。比如返回 1 表示成功，0 表示库存不足，-1 表示重复购买。”

技术映射：`KEYS[1]`、`KEYS[2]` 传 key，`ARGV[1]`、`ARGV[2]` 传用户 ID、扣减数量等参数。

## 3. 项目实战

### 3.1 准备秒杀数据

启动 Redis：

```bash
docker run --name redis-lab-13 -p 6379:6379 -d redis:8.6
docker exec -it redis-lab-13 redis-cli
```

初始化库存和购买记录：

```bash
SET mall:seckill:stock:sku1001 3
DEL mall:seckill:buyers:sku1001
GET mall:seckill:stock:sku1001
SMEMBERS mall:seckill:buyers:sku1001
```

购买记录用 Set 保存用户 ID，天然支持去重。

### 3.2 使用 EVAL 执行脚本

脚本逻辑：

```lua
local stockKey = KEYS[1]
local buyerKey = KEYS[2]
local userId = ARGV[1]
local quantity = tonumber(ARGV[2])

if redis.call('SISMEMBER', buyerKey, userId) == 1 then
  return -1
end

local stock = tonumber(redis.call('GET', stockKey) or '0')
if stock < quantity then
  return 0
end

redis.call('DECRBY', stockKey, quantity)
redis.call('SADD', buyerKey, userId)
return 1
```

在 `redis-cli` 中可以压成一行执行：

```bash
EVAL "local stockKey=KEYS[1]; local buyerKey=KEYS[2]; local userId=ARGV[1]; local quantity=tonumber(ARGV[2]); if redis.call('SISMEMBER', buyerKey, userId)==1 then return -1 end; local stock=tonumber(redis.call('GET', stockKey) or '0'); if stock < quantity then return 0 end; redis.call('DECRBY', stockKey, quantity); redis.call('SADD', buyerKey, userId); return 1" 2 mall:seckill:stock:sku1001 mall:seckill:buyers:sku1001 user-1 1
```

继续执行：

```bash
EVAL "local stockKey=KEYS[1]; local buyerKey=KEYS[2]; local userId=ARGV[1]; local quantity=tonumber(ARGV[2]); if redis.call('SISMEMBER', buyerKey, userId)==1 then return -1 end; local stock=tonumber(redis.call('GET', stockKey) or '0'); if stock < quantity then return 0 end; redis.call('DECRBY', stockKey, quantity); redis.call('SADD', buyerKey, userId); return 1" 2 mall:seckill:stock:sku1001 mall:seckill:buyers:sku1001 user-1 1
GET mall:seckill:stock:sku1001
SMEMBERS mall:seckill:buyers:sku1001
```

第一次返回 `1`，第二次同一用户返回 `-1`，库存不会重复扣减。

### 3.3 使用 SCRIPT LOAD 和 EVALSHA

生产代码不建议每次都发送完整脚本文本，可以先加载脚本：

```bash
SCRIPT LOAD "local stockKey=KEYS[1]; local buyerKey=KEYS[2]; local userId=ARGV[1]; local quantity=tonumber(ARGV[2]); if redis.call('SISMEMBER', buyerKey, userId)==1 then return -1 end; local stock=tonumber(redis.call('GET', stockKey) or '0'); if stock < quantity then return 0 end; redis.call('DECRBY', stockKey, quantity); redis.call('SADD', buyerKey, userId); return 1"
SCRIPT EXISTS <上一步返回的sha1>
EVALSHA <sha1> 2 mall:seckill:stock:sku1001 mall:seckill:buyers:sku1001 user-2 1
```

业务伪代码：

```text
sha = scriptCache.get("seckillDeduct")
if sha is empty:
    sha = redis.SCRIPT_LOAD(luaText)
    scriptCache.put("seckillDeduct", sha)

result = redis.EVALSHA(
    sha,
    keys = ["mall:seckill:stock:" + skuId, "mall:seckill:buyers:" + skuId],
    args = [userId, quantity]
)

if result == 1:
    createOrderAsync(userId, skuId)
elif result == 0:
    return "库存不足"
elif result == -1:
    return "请勿重复购买"
```

如果 Redis 重启后脚本缓存丢失，`EVALSHA` 可能返回 `NOSCRIPT`，客户端应捕获后重新 `SCRIPT LOAD`。

### 3.4 释放锁脚本

第 12 章提到互斥重建缓存时，释放锁要先比较再删除。Lua 写法如下：

```bash
SET mall:lock:rebuild:product:1001 req-001 NX EX 5
EVAL "if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) else return 0 end" 1 mall:lock:rebuild:product:1001 req-001
```

这个脚本避免了误删别人的锁：如果锁过期后被其他请求重新获得，旧请求的 `DEL` 不会删除新锁。

### 3.5 常见坑

第一，脚本里写大循环。Lua 在 Redis 主线程运行，扫描大集合、遍历大量 key 都可能阻塞服务。

第二，动态拼接 key 却没有通过 `KEYS` 声明。集群模式下 Redis 需要知道脚本访问哪些 key，跨槽访问还会失败。

第三，脚本返回值没有约定。不要让调用方靠猜测字符串判断业务结果，建议用明确状态码和文档说明。

第四，忽略 `NOSCRIPT`。脚本 SHA 不是永久存在，Redis 重启或主从切换后要能重新加载。

## 4. 项目总结

Lua 的价值在于把短小的 Redis 多步逻辑变成原子动作。它适合库存扣减、限购、释放锁、复合计数器等场景；不适合承载复杂业务编排、数据库访问和长时间计算。使用 Lua 时要控制脚本长度，明确 `KEYS` 与 `ARGV`，约定返回值，并准备 `NOSCRIPT` 重载机制。

适用场景：
- 判断后修改，例如库存充足才扣减。
- 比较后删除，例如释放分布式锁。
- 多个计数器同步更新，例如接口访问量和用户频控。

不适用场景：
- 跨系统调用、数据库查询、远程 HTTP。
- 大范围扫描和复杂报表计算。
- 需要长时间运行或可暂停恢复的任务。

思考题：
1. 为什么 Redis 单条命令原子，并不代表客户端多条命令组成的业务流程原子？
2. 秒杀扣库存脚本成功后，创建订单失败了怎么办？Redis 库存与数据库订单之间如何补偿？

推广建议：开发团队把 Lua 当作“短事务工具”，脚本纳入代码评审；测试团队要做并发压测和重复购买测试；运维团队关注慢脚本、阻塞和主从切换后的脚本重载行为。
