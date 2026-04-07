本故事纯属虚构，如有雷同，纯属巧合。

> 「少林十八铜人」是为了防止功夫未成的少林弟子下山被人击败，辱及少林声名。故设十八铜人于寺门前，少林弟子能击退铜人即表示其功夫精湛。闯得过十八铜人，就是令江湖人竖起大拇指的好汉英雄！

**故事背景**

“十八铜人”的来历：

> 根据少林寺的传说，十八铜人真正的来历，应该是起源于唐朝的十八棍僧救唐王的传说。隋朝末年，群雄争天下，而当时还是秦王的李世民身陷险境，被王世充的部属追杀。逃亡途中得到当时少林寺武僧的协助，终于逃出生天。而后来李世民登基为帝之后，为了褒奖少林寺练武强身、匡扶天下的事迹，而铸造了以十八个武僧为原型的铜像，这就是真正的十八铜人。自此之后，十八铜人就成了少林弟子行侠仗义、救民保国的象征。再经由各种武侠小说和影视作品的渲染，十八铜人逐渐成为了少林寺的守护者。

**闯十八铜人阵**

**1. 缓存中 Redis 和 Memcached 的区别？**

相同点：

内存数据库

存储方式都是 key-value 形式

性能相似

主要区别：

![](http://p3.toutiaoimg.com/large/pgc-image/d149066d95a54595a717b6def65caafc)

**2. Redis 有哪些数据结构？各个数据结构有什么应用场景？**

![](http://p9.toutiaoimg.com/large/pgc-image/24e53922941845968e09740a33a732fd)

String：页面缓存

hash：对象存储

list：消息队列

set：去重

sorted set：排行榜

bitmap：bloom Filter

Hyperloglogs：基数统计

geo：附近的人

**3. Redis 批量操作如更新，删除，新增怎么处理？**

最优方式：cat data.txt | redis-cli --pipe

MSET 等多键写入命令

pipeline

事务 transaction

**4.数据库和 Redis 怎么保证数据一致性？**

双删策略+缓存超时设置

**5. 异步消息队列**

pub/sub

lpush/brpop

stream

**6. 分布式锁**

redisson 实现

**7.当 key 变化时，如何通知？**

unwatch

**8.计算 pv，uv**

pv：incrby

uv：set，hyperloglog

**9.缓存穿透、缓存击穿、缓存雪崩、热点数据集中失效**

**缓存穿透：**查询不存在数据的现象。常用方法：BloomFilter

**缓存击穿：**在平常高并发的系统中，大量的请求同时查询一个 key 时，此时这个 key 正好失效了，就会导致大量的请求都打到数据库上面去。常用方法：互斥锁

**缓存雪崩**：当某一时刻发生大规模的缓存失效的情况，导致数据库崩溃。常用方法：sentinel，hystrix

**热点数据集中失效**：我们在设置缓存的时候，一般会给缓存设置一个失效时间，过了这个时间，缓存就失效了。对于一些热点的数据来说，当缓存失效以后会存在大量的请求过来，然后打到数据库去。常用方法：加一个 Random 有效期，互斥锁

**10.分页查询**

SCAN cursor [MATCH pattern] [COUNT count]

SCAN 命令及其相关的 SSCAN, HSCAN 和 ZSCAN 命令都用于增量迭代一个集合元素。

SCAN 命令用于迭代当前数据库中的 key 集合。

SSCAN 命令用于迭代 SET 集合中的元素。

HSCAN 命令用于迭代 Hash 类型中的键值对。

ZSCAN 命令用于迭代有序集合（sorted set）中的元素和元素对应的分值

以上列出的四个命令都支持增量式迭代，它们每次执行都只会返回少量元素，所以这些命令可以用于生产环境，而不会出现像 KEYS 或者 SMEMBERS 命令带来的可能会阻塞服务器的问题。

不过，SMEMBERS 命令可以返回集合键当前包含的所有元素， 但是对于 SCAN 这类增量式迭代命令来说，有可能在增量迭代过程中，集合元素被修改，对返回值无法提供完全准确的保证。

因为 SCAN, SSCAN, HSCAN 和 ZSCAN 四个命令的工作方式都非常相似， 所以这个文档会一并介绍这四个命令，需要注意的是 SSCAN, HSCAN ,ZSCAN 命令的第一个参数总是一个 key； SCAN 命令则不需要在第一个参数提供任何 key，因为它迭代的是当前数据库中的所有 key。

**11.持久化 aof 和 rdb 区别**

> Redis provides a different range of persistence options:
> 
> The RDB persistence performs point-in-time snapshots of your dataset at specified intervals.The AOF persistence logs every write operation received by the server, that will be played again at server startup, reconstructing the original dataset. Commands are logged using the same format as the Redis protocol itself, in an append-only fashion. Redis is able to rewrite the log in the background when it gets too big.
> 
> If you wish, you can disable persistence completely, if you want your data to just exist as long as the server is running.
> 
> It is possible to combine both AOF and RDB in the same instance. Notice that, in this case, when Redis restarts the AOF file will be used to reconstruct the original dataset since it is guaranteed to be the most complete.
> 
> The most important thing to understand is the different trade-offs between the RDB and AOF persistence.

Redis 4.x 支持混合型

**12. Sentinel / Cluster**

sentinel：Master-slave replication

cluster：Multi-master replication

**13. Partitioning**

常见实现：

Redis Cluster

Twemproxy

注意：SWAPDB index index

该命令可以交换同一 Redis 服务器上的两个 DATABASE，可以实现连接某一数据库的连接立即访问到其他 DATABASE 的数据。访问交换前其他 database 的连接也可以访问到该 DATABASE 的数据。 如：

SWAPDB 0 1

交换 DATABASE 0，1。所有访问 0 号数据库的连接立刻可以访问到 1 号数据库的数据，同样的，访问 1 号数据库的连接立即可以访问 0 号数据库的数据。

**14.安全 password**

AUTH password

为 Redis 服务请求设置一个密码。Redis 可以设置在客户端执行 commands 请求前需要通过密码验证。通过修改配置文件的 requirepass 就可以设置密码。如果密码与配置文件里面设置的密码一致，服务端就会返回一个 OK 的状态码，接收客户端随后发送的其它请求命令；否则服务端会返回错误码，客户端需要换用正确密码再连接。

注意: 因为 Redis 的高性能能在短时间接受非常多的尝试性密码，所以请务必设置一个足够复杂的密码以防止可能的攻击。

**15.事务**

事务命令

DISCARD

EXEC

MULTI

UNWATCH

WATCH

**16.客户端**

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

**17. lua 脚本/限流**

sorted set 实现 token buckets/leaky bucket

**18.秒杀系统中控制调用频次/安全限流**

bloomFilter

sorted set 实现（如 17）

---

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
