# 第9章：HyperLogLog 实战：接口 UV 与活动去重估算

## 1. 项目背景

活动平台上线后，运营每天都要看三个指标：商品详情接口 UV、活动页独立访客数、领取优惠券的独立设备数。第一版实现用 Set 去重，用户访问一次就 `SADD uv:api:20260430 userId`，统计时 `SCARD`。这个方案结果精确，也容易理解。

问题出在规模上。高频接口一天可能有几千万独立用户，多个接口、多个渠道、多个活动叠加后，Set 的内存占用会迅速膨胀。更现实的是，运营看日报时并不一定需要精确到个位数。活动页 UV 是 10,000,000 还是 10,000,137，对决策影响很小；但 Redis 内存从几 GB 涨到几十 GB，就会影响稳定性和成本。

HyperLogLog 是 Redis 提供的近似基数统计结构，适合回答“有多少个不同元素”。它不能列出具体用户，也不能判断某个用户是否出现过，但能用很小的空间估算 UV。Redis 的 HLL 标准误差约 0.81%，这对很多统计类业务完全可接受。

## 2. 项目设计

小胖先问：“去重还要近似？我卖了 100 个包子，少数一个都不行啊。”

小白说：“订单数当然要精确，但 UV 统计不是结算数据。用户规模上千万时，为了省内存接受小误差，可能更划算。”

大师点头：“工程里不是所有数据都同等严格。库存、余额、订单必须精确；UV、独立设备数、曝光人数很多时候是趋势指标。HyperLogLog 用概率算法换空间，命令只有 `PFADD`、`PFCOUNT`、`PFMERGE`，非常适合高频去重估算。”

技术映射：HLL 适合基数估算，不适合精确名单、会员权益、风控黑名单。

小胖追问：“那它会不会越存越大？Set 是每个用户都存进去，HLL 是不是也存一堆？”

大师解释：“HLL 不保存完整成员，只保存用于估算的桶信息。你加入一百万用户和一千万用户，key 的空间增长非常有限。代价是不能再把用户拿出来，也不能删除某个用户对统计的影响。”

小白补充：“也就是说，HLL 是只进不出的估算器。数据更正和撤销不适合它。”

技术映射：HLL 支持添加和合并，不支持精确删除，不支持成员枚举。

小胖又问：“如果我有 App、Web、小程序三个渠道，想看总 UV，怎么办？”

大师回答：“每个渠道一个 HLL，最终用 `PFMERGE` 合并到临时 key，再 `PFCOUNT`。这很适合分片统计、跨天合并、跨渠道合并。注意临时合并 key 要设置过期时间。”

技术映射：HLL 的合并能力让分片统计简单，但统计口径必须统一，例如 userId、deviceId、cookieId 不能混用。

## 3. 项目实战

启动 Redis：

```bash
docker run --name redis-lab-09 -p 6379:6379 -d redis:8.6
docker exec -it redis-lab-09 redis-cli
```

### 3.1 接口 UV 统计

统计商品详情接口的日 UV：

```bash
PFADD uv:api:product:20260430 1001 1002 1003
PFADD uv:api:product:20260430 1002 1003 1004
PFCOUNT uv:api:product:20260430
```

虽然添加了重复用户，`PFCOUNT` 会估算独立用户数量。小数据下返回值常常看起来精确，但不要因为演示数据小就误以为 HLL 永远精确。

业务伪代码：

```text
recordApiVisit(apiName, uid, date):
  key = "uv:api:" + apiName + ":" + yyyymmdd(date)
  PFADD key uid
  EXPIRE key 7776000

queryApiUv(apiName, date):
  return PFCOUNT "uv:api:" + apiName + ":" + yyyymmdd(date)
```

如果没有登录用户，可以用设备 ID 或匿名 ID，但一定要保证统计口径稳定：

```text
visitorId = loginUserId if loggedIn else deviceId
```

不要今天用 userId，明天用 cookieId，否则 UV 趋势会断层。

### 3.2 活动分渠道统计与合并

分别记录 App、Web、小程序：

```bash
PFADD uv:campaign:618:app:20260430 1001 1002 1003
PFADD uv:campaign:618:web:20260430 1003 1004
PFADD uv:campaign:618:mini:20260430 1004 1005 1006

PFCOUNT uv:campaign:618:app:20260430
PFCOUNT uv:campaign:618:web:20260430
PFCOUNT uv:campaign:618:mini:20260430
```

合并统计全渠道：

```bash
PFMERGE uv:campaign:618:all:20260430 uv:campaign:618:app:20260430 uv:campaign:618:web:20260430 uv:campaign:618:mini:20260430
PFCOUNT uv:campaign:618:all:20260430
EXPIRE uv:campaign:618:all:20260430 86400
```

跨天周 UV 也类似：

```bash
PFMERGE uv:api:product:2026W18 uv:api:product:20260428 uv:api:product:20260429 uv:api:product:20260430
PFCOUNT uv:api:product:2026W18
```

临时合并 key 可以保留一段时间，避免报表系统重复合并。

### 3.3 与 Set 精确统计对比

同样记录一批用户：

```bash
SADD uv:set:api:product:20260430 1001 1002 1003 1004
PFADD uv:hll:api:product:20260430 1001 1002 1003 1004

SCARD uv:set:api:product:20260430
PFCOUNT uv:hll:api:product:20260430
MEMORY USAGE uv:set:api:product:20260430
MEMORY USAGE uv:hll:api:product:20260430
```

在很小的数据量下，Set 可能更省，因为 HLL 有自身结构开销。但当成员数量进入百万级，HLL 的优势会明显。判断用不用 HLL，不看示例里的 4 个成员，而看真实业务的独立用户规模和精度要求。

### 3.4 常见坑

第一，HLL 不能返回成员列表。如果后续要给访问过活动页的用户发券，就必须用 Set、数据库或日志系统保留明细。

第二，HLL 不能删除单个成员。误加数据后只能重建统计 key，或者从原始日志重新计算。

第三，误差是业务契约的一部分。报表页面要标注“估算 UV”，不要把它用于结算、风控处罚、抽奖资格。

第四，统计口径要统一。`userId`、`deviceId`、`ip` 混用会让数据失真；跨端合并前要明确一个人多设备是否算多个访客。

第五，`PFMERGE` 会写目标 key。对大量临时合并结果要设置 TTL，避免统计 key 越积越多。

## 4. 项目总结

HyperLogLog 的价值不在于功能复杂，而在于它给了我们一个工程取舍：当业务只关心“不同元素大概有多少”时，用极小空间换取可接受误差。它适合接口 UV、活动独立访客、广告曝光去重、独立设备数估算。

它不适合需要精确结果、需要成员明细、需要删除修正的场景。简单判断标准是：如果这个数字会影响钱、权益、资格，就不要只用 HLL；如果它用于趋势、报表、容量观察，HLL 往往非常合适。

本章思考题：

1. 如果活动抽奖要求“访问过页面的用户才有资格”，为什么不能只用 HyperLogLog？
2. 如果日报 UV 使用 HLL，周报 UV 是否可以直接把每天的 `PFCOUNT` 相加？为什么？
