# 第11章：Key 设计与过期策略：从能用到好维护

## 1. 项目背景

电商系统接入 Redis 一段时间后，最先暴露的问题往往不是“命令不会用”，而是“key 越来越乱”。商品详情有 `goods_1`、`product:1`、`p:detail:1` 三种写法；验证码有的 5 分钟过期，有的永不过期；测试环境和预发环境共用一个 Redis 库，偶尔还会互相覆盖。业务刚上线时这些问题不明显，等到运营活动开始，排查一个用户购物车异常就要在几十种 key 名里猜半天。

更麻烦的是生命周期失控。缓存 key 没有 TTL，会把 Redis 慢慢撑满；大量 key 设置相同过期时间，会在整点形成过期风暴；开发为了排查问题在生产执行 `KEYS *user*`，可能让 Redis 主线程长时间阻塞。Redis 的速度很快，但它不是“随便命名、随便过期、随便扫描”的垃圾桶。

本章要把电商系统的 key 从“能用”整理到“好维护”。我们会设计统一命名规范，区分业务域、对象类型、对象 ID 和字段含义；为验证码、商品缓存、购物车、限流计数器设置不同 TTL；最后用 `SCAN`、`TTL`、`TYPE`、`MEMORY USAGE` 做一次小型巡检，找出不合规 key、无 TTL key 和疑似大 key。

## 2. 项目设计

小胖看着 RedisInsight 里的 key 列表直挠头：“这不就像我电脑桌面吗？`新建文件夹`、`最终版`、`最终最终版`，能打开不就行了？”

小白接话：“能打开不等于能维护。比如 `user:1` 是用户资料、登录态还是购物车？如果多租户系统里两个商家都有用户 1，不加租户维度就会冲突。”

大师在白板写下格式：“推荐先统一为 `业务域:场景:对象类型:对象ID:字段`。例如 `mall:cache:product:1001`、`mall:cart:user:9527`、`mall:captcha:login:13800138000`。不是每个 key 都必须五段，但必须让人一眼看懂归属和生命周期。”

技术映射：key 命名要服务于排查、隔离、扫描和容量统计。冒号没有特殊语义，但它是最常见的命名分隔符。

小胖又问：“那我把所有 key 都设置 1 天过期，省心吧？过期了 Redis 自己删。”

小白摇头：“验证码 5 分钟，商品详情 10 分钟，购物车可能 30 天，分布式开关也许不应该过期。全用一个 TTL 会让业务语义变模糊，而且同一秒大量过期会不会有风险？”

大师回答：“TTL 不是清洁工，而是业务生命周期。临时数据必须有 TTL，核心状态要谨慎过期。大量缓存 key 可以加随机抖动，比如基础 600 秒，再随机 0 到 120 秒，避免同一时刻集中失效。Redis 删除过期 key 主要靠惰性删除和定期删除：访问时发现过期会删，后台也会周期性抽样清理。”

技术映射：`EXPIRE`、`PEXPIRE`、`TTL`、`PERSIST` 管理生命周期；过期风暴会带来缓存击穿和 CPU 抖动。

小胖继续追问：“排查时我想看有哪些商品缓存，`KEYS mall:cache:product:*` 不是最快吗？”

小白马上提醒：“学习环境可以，生产环境危险。`KEYS` 要遍历整个 keyspace，key 很多时会阻塞 Redis。”

大师点头：“生产巡检用 `SCAN`。它是渐进式游标扫描，每次拿一小批，不保证一次返回全部，也可能在数据变化时有重复，所以脚本要能去重或容忍重复。我们还要约定 db 的使用方式：小团队可以只用 db0，通过 key 前缀隔离；不要指望 `SELECT 1` 解决权限和治理问题，多环境最好物理实例或独立逻辑实例隔离。”

技术映射：`SCAN` 替代 `KEYS`，前缀替代随意 db，TTL 策略替代“写完不管”。

## 3. 项目实战

### 3.1 准备实验数据

启动 Redis 后进入命令行：

```bash
docker run --name redis-lab-11 -p 6379:6379 -d redis:8.6
docker exec -it redis-lab-11 redis-cli
```

写入几类 key：

```bash
SET mall:cache:product:1001 '{"id":1001,"name":"机械键盘","price":299}' EX 660
HSET mall:cart:user:9527 sku:1001 2 sku:1002 1
EXPIRE mall:cart:user:9527 2592000
SET mall:captcha:login:13800138000 746281 EX 300
INCR mall:limit:sms:13800138000
EXPIRE mall:limit:sms:13800138000 60
SET badkey "no-prefix"
SET mall:cache:product:1002 '{"id":1002,"name":"鼠标"}'
```

检查生命周期：

```bash
TTL mall:cache:product:1001
TTL mall:cart:user:9527
TTL mall:captcha:login:13800138000
TTL badkey
TYPE mall:cart:user:9527
MEMORY USAGE mall:cache:product:1001
```

`TTL` 返回正数表示还有多少秒过期，返回 `-1` 表示没有过期时间，返回 `-2` 表示 key 不存在。我们要重点关注临时缓存却返回 `-1` 的 key。

### 3.2 设计 key 规范

电商系统可以先定一张简单规范：

```text
mall:cache:product:{productId}        商品详情缓存，TTL 10-12 分钟
mall:cart:user:{userId}               用户购物车，TTL 30 天
mall:captcha:login:{phone}            登录验证码，TTL 5 分钟
mall:limit:sms:{phone}                短信频控，TTL 60 秒
mall:rank:sales:{yyyyMMdd}            销量榜，TTL 7-30 天
mall:lock:order:{orderId}             互斥锁，TTL 必须短且有唯一值
```

业务伪代码：

```text
function buildProductCacheKey(productId):
    assert productId is not empty
    return "mall:cache:product:" + productId

function cacheProduct(product):
    ttl = 600 + random(0, 120)
    SET buildProductCacheKey(product.id) serialize(product) EX ttl

function saveLoginCaptcha(phone, code):
    SET "mall:captcha:login:" + phone code EX 300
```

这里的关键不是字符串拼接，而是“统一入口”。不要让每个业务方法手写 key；项目里应建立 `RedisKey` 工具类或配置枚举，避免重构时漏改。

### 3.3 用 SCAN 做巡检

在命令行里可以先手动观察：

```bash
SCAN 0 MATCH mall:* COUNT 10
SCAN 0 MATCH * COUNT 10
```

巡检脚本的流程如下：

```text
cursor = 0
do:
    cursor, keys = SCAN cursor MATCH "*" COUNT 100
    for key in keys:
        if not startsWith(key, "mall:"):
            report("命名不合规", key)
        ttl = TTL key
        type = TYPE key
        size = MEMORY USAGE key
        if ttl == -1 and key looks like cache/captcha/limit:
            report("临时 key 缺少 TTL", key)
        if size > 1MB:
            report("疑似大 key", key, type, size)
while cursor != 0
```

如果使用 Python，可以用 Redis 客户端实现同样流程，注意 `SCAN` 可能返回重复 key，报告时用集合去重即可。生产巡检不要开太大的 `COUNT`，也不要在业务高峰跑全量内存分析。

### 3.4 常见坑

第一，把用户输入直接拼进 key。手机号、昵称、搜索词都可能包含空格或特殊字符，建议做标准化或哈希化，避免 key 过长和隐私泄露。

第二，用 db 编号当命名空间。`SELECT 1` 只能隔离逻辑库，不能解决权限、容量、慢命令互相影响，集群模式下还不支持多 db。

第三，所有缓存同一时间过期。批量导入商品缓存时，如果都设置 `EX 600`，十分钟后可能同时失效，数据库会突然被打满。

第四，过期时间只写在代码里，文档没人维护。建议把 key 规范放进项目 README 或配置中心，测试用例也要检查 TTL。

## 4. 项目总结

本章把 key 设计看成工程治理问题，而不是命令细节。好 key 应该具备四个特征：能读懂归属、能判断生命周期、能按前缀巡检、能支持容量统计。TTL 也不是越短越好，验证码、限流、缓存、购物车、锁都有不同的业务含义。

适用场景：
- 新项目接入 Redis 前，先制定 key 命名和 TTL 表。
- 老项目 Redis key 混乱时，用 `SCAN` 做低风险巡检。
- 多团队共用 Redis 时，用业务域前缀和权限边界减少误伤。
- 活动缓存预热时，为 TTL 增加随机抖动。

不适用做法：
- 用 `KEYS` 做生产巡检。
- 把 Redis 当无限临时文件夹。
- 依赖 db 编号替代环境和租户隔离。

思考题：
1. 如果一个商品详情缓存被多个渠道复用，key 中应该包含渠道 ID 吗？什么情况下必须包含？
2. 为什么 `SCAN` 不能简单理解成“安全版 KEYS”？它对脚本设计有什么要求？

推广建议：开发团队负责提供 key 清单和 TTL 语义；测试团队把无 TTL、过期风暴、大 key 纳入用例；运维团队定期输出 keyspace、内存、慢查询和过期指标报告。
