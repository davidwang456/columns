本故事纯属虚构，如有雷同，纯属巧合。

> 「少林十八铜人」是为了防止功夫未成的少林弟子下山被人击败，辱及少林声名。故设十八铜人于寺门前，少林弟子能击退铜人即表示其功夫精湛。闯得过十八铜人，就是令江湖人竖起大拇指的好汉英雄！

**故事背景**

“十八铜人”的来历：

> 根据少林寺的传说，十八铜人真正的来历，应该是起源于唐朝的十八棍僧救唐王的传说。隋朝末年，群雄争天下，而当时还是秦王的李世民身陷险境，被王世充的部属追杀。逃亡途中得到当时少林寺武僧的协助，终于逃出生天。而后来李世民登基为帝之后，为了褒奖少林寺练武强身、匡扶天下的事迹，而铸造了以十八个武僧为原型的铜像，这就是真正的十八铜人。自此之后，十八铜人就成了少林弟子行侠仗义、救民保国的象征。再经由各种武侠小说和影视作品的渲染，十八铜人逐渐成为了少林寺的守护者。

**Redis 别传：铜人阵——面试弹词十八段**

**大师**：九式剑法你已练过，今日不教新招，只过**铜人阵**。阵里尽是面试官爱问的「熟面孔」，答的是**八股**，用的是**真功**。

**小白**：弟子若卡壳……

**大师**：卡壳便记**拆招口诀**，回去对照源码与监控数据——铜人不会问你「昨天谁删的库」，但线上会。

> **进场须知**：以下题目若答得磕磕绊绊，不必自废武功；答顺了也莫骄傲。二者兼顾，方可下山。

---

### 铜人第一阵 · 缓存双雄

**铜人**：（闷声）Redis 与 Memcached，有何异同？

**小白**：都是内存型、key-value、快。区别常见有：Redis **类型丰富**（String/Hash/List/Set/ZSet/Stream…），支持**持久化、复制、Lua、事务雏形、多种淘汰策略**；Memcached 更简单、纯缓存场景多，**集群扩展**常靠客户端分片。

**大师**：（点头）补一句加分：**使用场景与运维成本**——Redis 常被当「数据结构服务 + 缓存」；Memcached 仍是**纯 KV 缓存**利器。别踩一捧一，要踩需求。

相同点：

- 内存数据库
- 存储方式都是 key-value 形式
- 性能相似

主要区别：

![](http://p3.toutiaoimg.com/large/pgc-image/d149066d95a54595a717b6def65caafc)

---

### 铜人第二阵 · 数据结构与应用

**铜人**：Redis 有哪些结构？各干什么活？

**小白**：String 缓存页面片段、计数器；Hash 存对象字段；List 做队列或时间线；Set 去重、交并差；ZSet 排行榜、延时队列；Bitmap 布隆、位统计；HyperLogLog 近似去重；Geo 附近；Stream 消息日志与消费者组……

**大师**：背完再加 **`TYPE` 与 `OBJECT ENCODING` 各一层**，铜人当场给你让路。

![](http://p9.toutiaoimg.com/large/pgc-image/24e53922941845968e09740a33a732fd)

---

### 铜人第三阵 · 批量与管道

**铜人**：批量增删改，怎么快？

**小白**：**`redis-cli --pipe`** 灌文件；**`MSET`/`MGET`** 多键；**`pipeline`** 打包往返；**事务 `MULTI/EXEC`** 保一组命令原子执行（注意与「回滚」不是一回事）。

**大师**：_pipeline 是省 RTT，不是省 CPU；大 value 照样慢。`--pipe` 适合**冷启动灌库**，注意**错误处理与限速**。

---

### 铜人第四阵 · 一致性

**铜人**：数据库与缓存怎么一致？

**小白**：没有银弹。常见：**Cache Aside**（读先缓存、 miss 回源；写先更库再删/更缓存）；**延迟双删**缓解并发脏读；**过期时间 + 短 TTL** 兜底；强一致要上**版本号、订阅 binlog、分布式事务**等重武器。

**大师**：面试答「双删」可以，**要说清竞态窗口与业务容忍度**；别把 Redis 当唯一真相源除非你真的懂代价。

---

### 铜人第五阵 · 消息形态

**铜人**：异步解耦有哪些 Redis 玩法？

**小白**：**List + BRPOP** 简单队列；**Pub/Sub** 广播、不持久；**Stream + Consumer Group** 持久、可重放、有 PEL。

**大师**：三句话选型：**丢得起用 Pub/Sub；要简单阻塞队列用 List；要位点与重试用 Stream**。

---

### 铜人第六阵 · 分布式锁

**铜人**：分布式锁怎么搞？

**小白**：生产常用 **Redisson** 一类封装；自研则 **`SET key value NX PX ttl` + 唯一 value + Lua 解锁**；更复杂场景读 **Redlock** 争议与** fencing token** 讨论，别背成教条。

**大师**：加分项：**锁续期、可重入、主从切换下的安全性**——铜人爱听「我知道哪里可能丢锁」。

---

### 铜人第七阵 ·「谁动了我的 key」

**铜人**：key 变了，业务怎么感知？

**小白**：常见几条路：**Keyspace notifications**（`notify-keyspace-events`）；**Redis 6+ Client Side Caching / TRACKING**；应用层**版本号轮询**；Canal/Debezium 等**CDC**。

**大师**：（敲铜人脑袋）**`UNWATCH` 是事务乐观锁配套，不是「key 变更推送」**。答错这一字的，回去抄十遍 `WATCH` 文档。

---

### 铜人第八阵 · PV / UV

**铜人**：PV、UV 怎么记？

**小白**：**PV** 用 `INCR`/`INCRBY`；**UV** 精确用 **Set**（内存贵）、海量近似用 **HyperLogLog**；也可 Bitmap 视场景。

**大师**：追问「误差能不能接受」——**HLL 有标准误差带**，别拿它当会计账本。

---

### 铜人第九阵 · 缓存三害与热点

**铜人**：穿透、击穿、雪崩、热点失效？

**小白**：

- **穿透**：查不存在的数据，打穿到 DB → **布隆过滤器、空值短缓存、接口校验**。
- **击穿**：热点 key 过期瞬间并发打 DB → **互斥锁、逻辑过期、永不过期 + 异步刷新**。
- **雪崩**：大量 key 同时过期 → **随机 TTL、多级缓存、熔断限流**。
- **热点集中失效**：类似击穿 → **随机 TTL + 互斥重建**。

**大师**：`sentinel`、`hystrix` 是**体系能力**举例，现代栈可换成 **Resilience4j、服务网格、网关限流**——**答概念别背产品名死**。

---

### 铜人第十阵 · 遍历与分页

**铜人**：生产怎么扫 key？

**小白**：**`SCAN` 家族**（`SCAN`/`SSCAN`/`HSCAN`/`ZSCAN`）增量迭代；**禁止 `KEYS *` 在生产当循环用**。

**大师**：补一句：迭代中数据会变，**不保证快照绝对一致**——与业务核对语义。

**SCAN cursor [MATCH pattern] [COUNT count]**

SCAN 命令及其相关的 SSCAN, HSCAN 和 ZSCAN 命令都用于增量迭代一个集合元素。

SCAN 命令用于迭代当前数据库中的 key 集合。

SSCAN 命令用于迭代 SET 集合中的元素。

HSCAN 命令用于迭代 Hash 类型中的键值对。

ZSCAN 命令用于迭代有序集合（sorted set）中的元素和元素对应的分值

以上列出的四个命令都支持增量式迭代，它们每次执行都只会返回少量元素，所以这些命令可以用于生产环境，而不会出现像 KEYS 或者 SMEMBERS 命令带来的可能会阻塞服务器的问题。

不过，SMEMBERS 命令可以返回集合键当前包含的所有元素， 但是对于 SCAN 这类增量式迭代命令来说，有可能在增量迭代过程中，集合元素被修改，对返回值无法提供完全准确的保证。

因为 SCAN, SSCAN, HSCAN 和 ZSCAN 四个命令的工作方式都非常相似， 所以这个文档会一并介绍这四个命令，需要注意的是 SSCAN, HSCAN ,ZSCAN 命令的第一个参数总是一个 key； SCAN 命令则不需要在第一个参数提供任何 key，因为它迭代的是当前数据库中的所有 key。

---

### 铜人第十一阵 · 持久化

**铜人**：RDB 与 AOF？

**小白**：**RDB** 快照，恢复快、可能丢一段数据；**AOF** 日志，更完整、体积大可 rewrite；可混合；重启时若同开，**常以 AOF 优先重建**（以实际配置与版本文档为准）。

**大师**：追问 **fsync 策略、rewrite 时机、混合持久化** 即进入深水区。

> Redis provides a different range of persistence options:
>
> The RDB persistence performs point-in-time snapshots of your dataset at specified intervals.The AOF persistence logs every write operation received by the server, that will be played again at server startup, reconstructing the original dataset. Commands are logged using the same format as the Redis protocol itself, in an append-only fashion. Redis is able to rewrite the log in the background when it gets too big.
>
> If you wish, you can disable persistence completely, if you want your data to just exist as long as the server is running.
>
> It is possible to combine both AOF and RDB in the same instance. Notice that, in this case, when Redis restarts the AOF file will be used to reconstruct the original dataset since it is guaranteed to be the most complete.
>
> The most important thing to understand is the different trade-offs between the RDB and AOF persistence.

Redis 4.x 起支持**混合型**等能力演进，答题时带一句「看版本 release notes」显老练。

---

### 铜人第十二阵 · 高可用与分片

**铜人**：Sentinel？Cluster？

**小白**：**Sentinel** 管**主从故障转移、监控告警**；**Cluster** 做**数据分片、多主多从、槽迁移**。

**大师**：再补 **Twemproxy、Codis、云厂商托管** 等「分区/代理」方案，表示你见过真实架构图。

常见实现：

Redis Cluster

Twemproxy

注意：SWAPDB index index

该命令可以交换同一 Redis 服务器上的两个 DATABASE，可以实现连接某一数据库的连接立即访问到其他 DATABASE 的数据。访问交换前其他 database 的连接也可以访问到该 DATABASE 的数据。 如：

SWAPDB 0 1

交换 DATABASE 0，1。所有访问 0 号数据库的连接立刻可以访问到 1 号数据库的数据，同样的，访问 1 号数据库的连接立即可以访问 0 号数据库的数据。

---

### 铜人第十三阵 · 安全

**铜人**：密码与暴露面？

**小白**：`requirepass` / ACL；**TLS**；**禁用危险命令或改名**；**内网隔离**；防**暴力 AUTH**（Redis 极快，弱密码秒破）。

**大师**：生产**默认绑内网、配防火墙**，别只靠密码装安全。

AUTH password

为 Redis 服务请求设置一个密码。Redis 可以设置在客户端执行 commands 请求前需要通过密码验证。通过修改配置文件的 requirepass 就可以设置密码。如果密码与配置文件里面设置的密码一致，服务端就会返回一个 OK 的状态码，接收客户端随后发送的其它请求命令；否则服务端会返回错误码，客户端需要换用正确密码再连接。

注意: 因为 Redis 的高性能能在短时间接受非常多的尝试性密码，所以请务必设置一个足够复杂的密码以防止可能的攻击。

---

### 铜人第十四阵 · 事务

**铜人**：事务命令？

**小白**：`MULTI` → 入队 → `EXEC` 执行；`DISCARD` 放弃；`WATCH` 乐观锁监视 key，`UNWATCH` 取消监视。

**大师**：强调 **与关系型事务不同：无通用回滚**；命令错误分**入队失败**与**运行时错误**两类语义。

事务命令

DISCARD

EXEC

MULTI

UNWATCH

WATCH

---

### 铜人第十五阵 · 客户端生态

**铜人**：有哪些语言客户端？

**大师**：背几个熟的即可，**关键在连接池、超时、重试、序列化、TLS**——铜人不想听 alphabet soup。

> C
>
> C#
>
> C++
>
> Clojure
>
> Crystal
>
> D
>
> Dart
>
> Elixir
>
> Erlang
>
> Fancy
>
> Go
>
> Haskell
>
> Haxe
>
> Java
>
> JavaScript (Node.js)
>
> Lisp
>
> Lua
>
> MatLab
>
> Objective-C
>
> OCaml
>
> Pascal
>
> Perl
>
> PHP
>
> Prolog
>
> Pure Data
>
> Python
>
> R
>
> Rebol
>
> Ruby
>
> Rust
>
> Scala
>
> Scheme
>
> Smalltalk
>
> Swift
>
> Tcl
>
> Visual Basic

---

### 铜人第十六阵 · Lua 与限流

**铜人**：Lua 干啥？限流咋做？

**小白**：**Lua 脚本**在服务端原子执行，适合**复杂组合逻辑、减少往返**；限流可用 **令牌桶/漏桶** 思路，常配合 **ZSet 时间窗口** 或 **Redis Cell 模块**、网关层能力。

**大师**：别只会背「Lua」，要会举 **「库存扣减 + 校验」** 一类**原子组合**例子。限流可提 **ZSet 时间窗口**、**令牌桶/漏桶** 思路与网关配合。

---

### 铜人第十七阵 · 秒杀与风控

**铜人**：秒杀、防刷？

**小白**：**布隆过滤器**挡非法 id；**限流**（网关 + Redis）；**库存扣减 Lua 原子化**；**异步削峰**；**热点 key 拆分**；**CDN 与验证码**前置。

**大师**：一句 **「防超卖与防穿透不是同一招」** 就够铜人愣半拍。常用组合：**布隆 / 限流 / Lua 原子扣减 / 异步削峰**，与第十六阵呼应。

---

### 铜人第十八阵 · 源码落地（加分项）

**大师**：背完十八段，仍可能输在「**指哪打哪**」。赐你一张**源码速查**，答时捎带文件名，面试官眼睛会亮一下。

**19. 源码速查（与本仓库 `src/` 对齐，答面试能落地）**

| 话题 | 主要文件 | 一句话 |
|------|----------|--------|
| 对象与编码 | `object.h`, `object.c` | `type` + `encoding` + `ptr`，字符串 SDS |
| 字符串命令 | `t_string.c` | GET/SET/APPEND… |
| 哈希 | `t_hash.c` | listpack ↔ dict，`hashTypeConvert` |
| 列表 | `t_list.c`, `quicklist.c` | quicklist + listpack |
| 集合 | `t_set.c`, `intset.c` | intset / listpack / dict |
| 有序集 | `t_zset.c` | listpack ↔ skiplist+dict |
| GEO | `t_geo.c` | 基于 zset 存储编码坐标 |
| 位图 | `bitops.c` | 仍属字符串类型 |
| HyperLogLog | `hyperloglog.c` | `PFADD`/`PFCOUNT`/`PFMERGE` |
| Stream | `t_stream.c` | 消费组、PEL、持久化 |
| 事件循环 | `ae.c`, `networking.c` | 单线程执行命令模型 |
| 命令表 | `commands.def` → `commands.c` | 元数据与版本信息源头 |

**加分回答**：谈“Redis 单线程”时补充 **I/O 线程**与 **命令执行线程**区别；谈数据结构时举 **`OBJECT ENCODING` 与 `redis.conf` 阈值**如何改变 listpack/intset 是否升级。

---

**收阵**

**小白**：铜人沉默不语……弟子这是过了？

**大师**：阵是过了，**班还得上**。备份、限流、告警三件套，别忘揣进兜里。

**小白**：恭送大师！

> **下山赠言**：铜人阵过了，只说明你会答题；线上挂了，才说明你会做人——备份、限流、告警，三件套别忘带下山。
