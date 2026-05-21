# 第4章：哈希 Hash 实战：用户画像与购物车对象存储

## 1. 项目背景

商品详情和用户资料接入缓存后，团队很快遇到新的问题：如果把整个对象序列化成 JSON 字符串存入 Redis，每次只修改一个字段，也必须读出完整 JSON、反序列化、修改、再整体写回。购物车场景更明显，用户每添加一个商品，都要更新商品数量、勾选状态、更新时间。如果购物车对象很大，String 的整体读写成本会越来越高。

Redis Hash 更适合“一个对象包含多个字段”的场景。它可以把一个 key 当成对象名，把 field 当成对象属性。比如 `user:profile:1001` 下面有 `name`、`level`、`city`；`cart:1001` 下面有 `sku:2001`、`sku:2002`。这样读取单个字段、更新单个字段、对数字字段自增都很自然。

本章实战聚焦两个需求：用户画像缓存和购物车对象存储。我们会对比 Hash 与 JSON String 的取舍，并引入 Redis 8.x 字段级过期的思路，让读者理解 Hash 不只是“命令更多”，而是更贴近对象建模。

## 2. 项目设计

小胖说：“对象存 Redis 里，我直接转 JSON 不就完事了？一个 `GET` 拿回来，业务里改。”

小白问：“如果只改用户昵称，也要重写整个对象吗？如果购物车有 100 个商品，只改一个 SKU 数量，也要整体覆盖？”

大师解释：“JSON String 像把整张表封进一个文件，简单但局部修改麻烦。Hash 更像一张小表，字段可以单独读写。用户资料、购物车、配置项这类字段型对象，Hash 更自然。”

技术映射：`HSET key field value` 更新单个字段，`HGET key field` 读取单个字段，`HGETALL` 读取完整对象。

小胖追问：“那所有对象都用 Hash，是不是就不用 JSON 了？”

大师摇头：“不绝对。字段固定、需要局部更新、字段数量不大的对象适合 Hash；嵌套层级深、需要整体传输、客户端已经有成熟序列化逻辑的对象，JSON String 也可以。Redis Stack 的 JSON 类型还能做路径级更新和索引，但这是后面的内容。”

技术映射：数据结构选择优先服务业务访问模式，不是按个人喜好选择。

小白继续问：“购物车每个商品都放一个 field，那商品数量怎么加减？并发会不会覆盖？”

大师说：“数量字段可以用 `HINCRBY` 原子增减，避免读改写覆盖。购物车如果要保存更复杂的条目，可以把 field 设计成 SKU，value 存小 JSON；也可以一个 SKU 拆多个 field，但要权衡管理复杂度。”

## 3. 项目实战

### 3.1 用户画像缓存

写入用户资料：

```bash
HSET user:profile:1001 name "小胖" level 3 city "杭州" score 120
HGET user:profile:1001 name
HMGET user:profile:1001 name level city
HGETALL user:profile:1001
```

更新积分：

```bash
HINCRBY user:profile:1001 score 10
HGET user:profile:1001 score
```

设置整体过期：

```bash
EXPIRE user:profile:1001 3600
TTL user:profile:1001
```

用户画像常见 key 设计：

```text
user:profile:{userId}
user:risk:{userId}
user:setting:{userId}
```

### 3.2 购物车建模

简单购物车可以用 SKU 作为 field，数量作为 value：

```bash
HSET cart:1001 sku:2001 2
HINCRBY cart:1001 sku:2001 1
HGET cart:1001 sku:2001
HLEN cart:1001
HGETALL cart:1001
```

删除商品：

```bash
HDEL cart:1001 sku:2001
```

如果需要保存勾选状态、加入时间、价格快照，可以让 value 是小 JSON：

```bash
HSET cart:1001 sku:2002 "{\"count\":2,\"checked\":true,\"price\":3999}"
```

这种方式牺牲了部分局部更新能力，但保持了 key 数量较少，适合中等复杂度购物车。

### 3.3 字段级过期的业务想象

过去 Hash 通常只能给整个 key 设置过期时间。如果购物车中某个临时促销字段要单独过期，就需要拆 key 或由业务清理。Redis 8.x 引入字段级过期能力后，可以更自然地表达“对象不失效，但某些字段失效”。

示例思路：

```bash
HSET user:coupon:1001 coupon:888 "满100减20"
# 字段级过期命令以实际 Redis 版本支持为准
HEXPIRE user:coupon:1001 3600 FIELDS 1 coupon:888
```

适用场景：
- 用户临时权益。
- 购物车促销标记。
- 某个字段的短期风控状态。

注意：字段级过期是新能力，正式使用前要确认服务端版本、客户端支持和集群兼容性。

### 3.4 扫描大 Hash

不要对巨大 Hash 频繁使用 `HGETALL`。当字段很多时，应使用 `HSCAN` 分批处理：

```bash
HSCAN cart:1001 0 COUNT 20
```

生产经验是：对象型 Hash 应控制字段规模。如果一个用户购物车出现上万字段，业务上也应该限制或拆分，而不是依赖 Redis 硬扛。

## 4. 项目总结

Hash 适合对象型缓存和字段级操作。相比 String，它能减少整体序列化和整体覆盖；相比拆成大量 String key，它能降低 key 数量和管理成本。

优点：
- 字段可单独读写。
- 数字字段支持原子自增。
- 适合用户资料、配置、购物车等对象模型。
- 小 Hash 在内部编码上通常更省内存。

缺点：
- 复杂嵌套对象表达能力有限。
- 巨大 Hash 会带来阻塞和迁移风险。
- 字段 TTL 等新能力需要关注版本兼容。

常见踩坑：
1. 对大 Hash 使用 `HGETALL` 导致 Redis 阻塞或网络响应过大。
2. 把 Hash 当关系表用，field 无限制增长。
3. Hash 和数据库对象没有版本字段，导致缓存覆盖新数据。

思考题：
1. 用户画像适合 Hash，商品详情一定适合 Hash 吗？为什么？
2. 购物车 value 存数量和存小 JSON，各自的优缺点是什么？
