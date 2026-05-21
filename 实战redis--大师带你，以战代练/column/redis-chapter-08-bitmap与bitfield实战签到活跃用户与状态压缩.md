# 第8章：Bitmap 与 BitField 实战：签到、活跃用户与状态压缩

## 1. 项目背景

运营要做一个“连续签到领积分”活动：用户每天打开 App 可以签到，连续 7 天额外送券；数据团队还想看每日活跃、月活、多个渠道的活跃交集。第一版方案是建签到表，字段包含用户 ID、日期、渠道、签到时间。这个方案最直观，但活动一热起来，签到表每天新增几百万行，统计连续签到和月活就要频繁扫表或依赖离线任务。

另一个需求也很常见：某些用户状态只有开关含义，例如是否已签到、是否看过新手引导、是否领取活动券。如果每个状态都存成字符串或 Hash 字段，单个用户看起来不多，放到千万用户规模就会浪费大量内存。

Redis Bitmap 不是一种新类型，而是把 String 当作二进制位数组使用。每一位只有 0 或 1，非常适合海量布尔状态。BitField 则能在字符串的指定 bit 范围内读写整数，适合把多个小状态压缩到一个 key 里。本章我们实现月度签到、活跃用户统计和状态压缩。

## 2. 项目设计

小胖先说：“签到不就是打卡盖章吗？日历上盖了章就是 1，没盖就是 0。”

小白追问：“那一个月 31 天，每个用户用 31 个布尔值就够了。可如果反过来统计某天有哪些用户活跃，按用户存会不会不方便？”

大师回答：“这就是 Bitmap 建模要先决定维度。按用户维度，`signin:{uid}:202604` 的第几位表示某天是否签到，适合查个人连续签到。按日期维度，`active:20260430` 的第 uid 位表示当天是否活跃，适合统计 DAU 和渠道交集。”

技术映射：Bitmap 的 offset 是业务 ID 或日期序号，value 是 0/1；同一个需求可以按用户或按日期建模。

小胖又问：“那我用户 ID 是 900000001，第 9 亿位是不是很吓人？”

大师点头：“这是 Bitmap 最大的坑。Redis 会按最高 offset 扩容字符串，如果用户 ID 稀疏，直接用原始 ID 当 offset 会浪费巨大内存。生产上常用连续内部编号、分片 Bitmap，或换 Set、HyperLogLog、Bloom Filter。”

小白补充：“所以 Bitmap 省内存的前提是 offset 相对连续。”

技术映射：Bitmap 适合连续编号下的海量布尔值，不适合稀疏巨大 ID 直接映射。

小胖继续问：“BitField 又是什么？听起来像把小格子切得更细。”

大师说：“对。Bitmap 每位只有 0/1，BitField 可以按 2 位、4 位、8 位读写小整数。例如一个用户当天状态：2 位表示登录渠道，3 位表示会员等级，1 位表示是否领券。它适合极致压缩，但可读性差，业务变化后迁移麻烦。”

技术映射：Bitmap 解决布尔状态，BitField 解决小整数状态压缩；越压缩，越要写清楚位布局。

## 3. 项目实战

启动 Redis：

```bash
docker run --name redis-lab-08 -p 6379:6379 -d redis:8.6
docker exec -it redis-lab-08 redis-cli
```

### 3.1 月度签到

按用户维度建模，4 月第 1 天 offset 为 0，第 30 天 offset 为 29：

```bash
SETBIT signin:1001:202604 0 1
SETBIT signin:1001:202604 1 1
SETBIT signin:1001:202604 2 1
SETBIT signin:1001:202604 5 1

GETBIT signin:1001:202604 2
BITCOUNT signin:1001:202604
```

`BITCOUNT` 返回本月签到天数。查询某天是否签到：

```text
isSigned(uid, date):
  key = "signin:" + uid + ":" + yyyyMM(date)
  offset = dayOfMonth(date) - 1
  return GETBIT key offset == 1
```

连续签到可以从今天向前逐位检查：

```text
continuousDays(uid, today):
  count = 0
  for d from today down to monthStart:
    if GETBIT signinKey offset(d) == 1:
      count += 1
    else:
      break
  return count
```

如果只查月内 31 天，这种循环很轻。不要为了 31 次 `GETBIT` 过早复杂化；如果跨月连续签到，再补查上个月末尾即可。

### 3.2 活跃用户和交集

按日期维度记录活跃用户，offset 使用内部连续用户编号：

```bash
SETBIT active:app:20260430 1001 1
SETBIT active:app:20260430 1002 1
SETBIT active:web:20260430 1002 1
SETBIT active:web:20260430 1003 1

BITCOUNT active:app:20260430
BITCOUNT active:web:20260430
```

统计 App 和 Web 都活跃的用户：

```bash
BITOP AND active:both:20260430 active:app:20260430 active:web:20260430
BITCOUNT active:both:20260430
EXPIRE active:both:20260430 3600
```

统计 4 月前 3 天的月活，可以做 OR：

```bash
SETBIT active:app:20260428 1001 1
SETBIT active:app:20260429 1004 1
BITOP OR active:app:mau:202604 active:app:20260428 active:app:20260429 active:app:20260430
BITCOUNT active:app:mau:202604
```

`BITOP` 会生成目标 key，临时统计 key 一定要设置过期时间，避免慢慢堆满内存。

### 3.3 BitField 状态压缩

假设我们为用户每日状态设计 8 位：

```text
第 0 位：是否签到，0/1
第 1-2 位：登录渠道，0 未知，1 App，2 Web，3 小程序
第 3-5 位：会员等级，0-7
第 6 位：是否领取券
第 7 位：预留
```

写入状态：

```bash
# offset 0 写 1 位无符号整数，表示已签到
BITFIELD state:1001:20260430 SET u1 0 1

# offset 1 写 2 位渠道，1 表示 App
BITFIELD state:1001:20260430 SET u2 1 1

# offset 3 写 3 位会员等级，5 表示 V5
BITFIELD state:1001:20260430 SET u3 3 5

# offset 6 写 1 位领券状态
BITFIELD state:1001:20260430 SET u1 6 1
```

读取：

```bash
BITFIELD state:1001:20260430 GET u1 0 GET u2 1 GET u3 3 GET u1 6
```

业务伪代码：

```text
saveDailyState(uid, date, channel, level, coupon):
  key = "state:" + uid + ":" + yyyymmdd(date)
  BITFIELD key SET u1 0 1 SET u2 1 channel SET u3 3 level SET u1 6 coupon
  EXPIRE key 7776000
```

BitField 还有溢出策略，例如 `OVERFLOW SAT` 表示超出范围时截断到最大值：

```bash
BITFIELD counter:demo OVERFLOW SAT INCRBY u3 0 10
```

3 位无符号整数最大是 7，使用 `SAT` 后结果会停在 7。

### 3.4 常见坑

第一，offset 从 0 开始。日期和用户编号映射时要统一，否则签到会整体偏一天。

第二，Bitmap 最高 offset 决定内存扩展。不要直接用手机号、雪花 ID、稀疏数据库 ID 当 offset。

第三，`BITOP` 对大 Bitmap 会消耗 CPU，并生成新字符串。统计任务最好在低峰执行，临时 key 设置 TTL。

第四，BitField 的位布局必须文档化。字段一旦上线，随意改布局会导致老数据解释错误。

## 4. 项目总结

Bitmap 把海量布尔状态压缩到位级别，适合签到、活跃、开关矩阵、用户状态标记。BitField 进一步支持小整数压缩，适合对内存极敏感、状态字段稳定的业务。

它们的优势是省内存、命令简单、统计快；代价是可读性低、offset 设计要求高、复杂查询能力弱。个人签到更适合按用户建模，DAU/MAU 更适合按日期建模。真正落地时，要先画出查询路径，再决定 key 的维度。

本章思考题：

1. 如果用户 ID 不连续且最大值很大，如何改造 Bitmap offset 映射？
2. 如果要统计“连续 7 天活跃用户”，你会使用 `BITOP AND` 还是逐用户计算？为什么？
