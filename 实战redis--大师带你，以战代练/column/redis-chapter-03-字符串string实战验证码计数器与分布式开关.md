# 第3章：字符串 String 实战：验证码、计数器与分布式开关

## 1. 项目背景

电商系统上线后，最先遇到的是一组“小而高频”的需求：登录短信验证码要 5 分钟过期；同一手机号一分钟只能发送一次；商品详情页需要统计浏览量；运营希望能随时打开或关闭某个活动入口。这些需求看起来不复杂，但如果全部落到数据库里，读写压力会快速放大。

验证码是临时数据，不适合长期落库；发送频率限制需要原子计数；浏览量每次访问都更新数据库会造成热点行；功能开关要求变更后立即生效。Redis String 正好适合这类场景。它不是只能存“字符串文本”，而是 Redis 中最基础、最通用的二进制安全 value，可以承载文本、数字、JSON、小对象序列化结果以及计数器。

本章会围绕三个实战功能展开：短信验证码、防刷计数器、动态功能开关。理论部分只解释必要命令语义和原子性边界，重点放在如何把命令组合成可运行的业务能力。

## 2. 项目设计

小胖说：“验证码直接存在数据库不就好了？字段加个 expire_time，到期就不认。”

小白反问：“那每次发送、校验、删除都访问数据库，登录高峰期怎么办？而且过期验证码还要定时清理。”

大师解释：“验证码是典型短生命周期数据，Redis 更合适。就像临时取餐号，过一会儿自动作废，不需要永久进档案室。我们用 `SET code:login:手机号 验证码 EX 300`，Redis 到期自动删除。”

技术映射：`SET key value EX seconds` 用于写入带秒级过期时间的 String。

小胖又问：“防刷是不是也用验证码 key？一分钟内存在就不发？”

小白补充：“可以用 `SET NX EX` 做发送冷却，也可以用 `INCR` 做次数统计。两者边界不同：冷却是有没有发过，计数是发了几次。”

大师点头：“设计时要区分业务语义。发送冷却用 `SET sms:cooldown:手机号 1 NX EX 60`；日发送次数用 `INCR sms:count:手机号:日期`，第一次递增时设置当天过期。”

技术映射：`NX` 保证 key 不存在时才写入；`INCR` 是原子自增，适合计数器。

小胖继续问：“浏览量每次都 `INCR`，会不会不准？如果 Redis 重启呢？”

大师回答：“浏览量通常允许短时间异步落库。Redis 负责承接高频写，后台任务定时把增量刷回数据库。要不要强一致，取决于这项数据是否影响交易。浏览量和点赞数一般可以最终一致，库存和余额就不能这么随意。”

## 3. 项目实战

### 3.1 验证码写入与校验

写入验证码：

```bash
SET code:login:13800000000 9527 EX 300
TTL code:login:13800000000
GET code:login:13800000000
```

校验成功后删除：

```bash
DEL code:login:13800000000
```

业务伪代码：

```java
String key = "code:login:" + phone;
String code = randomCode();
redis.set(key, code, Duration.ofMinutes(5));
```

校验时不要只比较验证码，还要处理 key 不存在的情况：

```java
String saved = redis.get("code:login:" + phone);
if (saved == null) {
    return "验证码已过期";
}
if (!saved.equals(inputCode)) {
    return "验证码错误";
}
redis.del("code:login:" + phone);
return "校验成功";
```

### 3.2 发送冷却

一分钟内只允许发送一次：

```bash
SET sms:cooldown:13800000000 1 NX EX 60
```

第一次返回 `OK`，说明允许发送；一分钟内再次执行返回空，说明应该拒绝。

业务伪代码：

```java
Boolean ok = redis.setIfAbsent(
    "sms:cooldown:" + phone,
    "1",
    Duration.ofSeconds(60)
);
if (!ok) {
    return "操作太频繁，请稍后再试";
}
sendSms(phone);
```

### 3.3 日发送次数限制

每天最多发送 10 次：

```bash
INCR sms:count:13800000000:20260430
EXPIRE sms:count:13800000000:20260430 86400
```

这里有一个细节：`INCR` 和 `EXPIRE` 是两条命令，如果第一条成功后服务宕机，key 可能没有 TTL。更严谨的做法是使用 Lua，把首次递增和设置过期合并。

```lua
local current = redis.call('INCR', KEYS[1])
if current == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return current
```

执行：

```bash
EVAL "local current = redis.call('INCR', KEYS[1]); if current == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]); end; return current" 1 sms:count:13800000000:20260430 86400
```

### 3.4 浏览量计数器

商品浏览量：

```bash
INCR product:view:1001
INCRBY product:view:1001 10
GET product:view:1001
```

后台任务可以每隔一分钟读取增量，再写入数据库。为了避免重复刷数，可以使用 `GETDEL` 获取并删除：

```bash
GETDEL product:view:1001
```

如果 Redis 版本或客户端不支持 `GETDEL`，可以使用 Lua 保证读取和删除原子完成。

### 3.5 分布式开关

写入活动开关：

```bash
SET switch:activity:double11 on
GET switch:activity:double11
```

应用读取后决定是否展示入口：

```java
String status = redis.get("switch:activity:double11");
boolean enabled = "on".equals(status);
```

生产中建议给开关加上默认值策略：Redis 不可用时，是默认开启还是默认关闭，必须由业务风险决定。

## 4. 项目总结

String 是 Redis 的第一块积木，适合临时值、计数器、小对象、开关和简单状态。它的优势是命令简单、性能高、原子自增方便；缺点是表达复杂对象时可维护性弱，value 过大时会带来网络和内存压力。

适用场景：
- 验证码、Token、一次性票据。
- 接口调用次数、浏览量、点赞数。
- 活动开关、灰度开关、降级开关。
- 小对象序列化缓存。

不适用场景：
- 字段频繁局部更新的大对象。
- 需要复杂查询、排序、关系运算的数据。

常见踩坑：
1. 只用 `INCR` 不设置 TTL，导致计数 key 永久堆积。
2. 把超大 JSON 存成 String，每次读取和写入都传输完整对象。
3. 用 Redis 开关控制高风险业务，却没有定义 Redis 故障时的默认策略。

思考题：
1. 验证码校验成功后为什么要删除 key？
2. 浏览量可以最终一致，库存为什么不能只靠普通 `INCR/DECR` 随意处理？
