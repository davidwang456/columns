本故事纯属虚构，如有雷同，纯属巧合。

> **江湖规矩**：本系列「大师」「小白」为艺名，对话为教学演绎；若与你司架构评审现场雷同，纯属英雄所见略同。

> 《笑傲江湖》里，风清扬教令狐冲剑法，先要他忘掉招式；学 Redis 却相反——**对外要记清「型」，对内要猜透「编码」**，否则面试一问 `OBJECT ENCODING`，当场现出原形。

**故事背景**

江湖上流传一句面试黑话：「String、Hash、List、Set、ZSet 报一遍。」小白背得滚瓜烂熟，却在生产上看见 `quicklist`、`listpack` 一脸茫然——原来 Redis **对外是一种面相，对内另有一副筋骨**。

![](http://p9.toutiaoimg.com/large/pgc-image/4976fc65659d4bad82211b8adc4ef522)

**Redis 附篇：内视式——拆开 `redisObject` 看真身**

> （九式剑法练毕，特增**附篇**一节，专破「背得出类型名，却看不懂编码」之弊，与《总诀式》《破刀式》互为表里。）

**大师**：昨日九式你练的是「手上有招」；今日这式练的是「目中有骨」。先答我：面试官问「Redis 有哪些数据结构」，你答什么？

**小白**：String、Hash、List、Set、Sorted Set，外加 HyperLogLog、Geo、Bitmap、Stream……

**大师**：这是**对外类型**，江湖称**逻辑结构**。客户端敲 `TYPE key` 看见的便是这一层。可曾想过，同一逻辑类型，内核里未必长一个样？

**小白**：弟子确实没想过……只当 `SET` 进去就是一块字符串。

**大师**：那你且记下第二只眼睛：`OBJECT ENCODING key`。它看的是**物理编码**——为了省内存、换算法，同一把「剑」可能是 `embstr`，也可能是 `raw`，甚至悄悄变成整数编码。

---

**大师**：先验外伤，再照 X 光。`TYPE` 用法你可演示一遍？

**小白**：好。

```
redis> SET key1 "value"
"OK"
redis> LPUSH key2 "value"
(integer) 1
redis> SADD key3 "value"
(integer) 1
redis> TYPE key1
"string"
redis> TYPE key2
"list"
redis> TYPE key3
"set"
```

**大师**：善。再照 `OBJECT ENCODING`——记住，Redis 4/5/6/7 一路演进，**老文章里的 ziplist 多已换成 listpack**，你若背旧经，容易在源码面前撞墙。

**小白**：那 `OBJECT` 还有啥用？

**大师**：排障、估内存、决定淘汰策略时，看它一眼，便知这 key 是紧凑存储还是已「膨胀」成哈希表、跳表。做缓存架构的，这叫**行医先望闻问切**。

---

**大师**：内核里万物归一，先落在 `redisObject`（`robj`）上。你本地仓库以 **`src/object.h`** 为准，莫再抄旧帖里的半截定义。

逻辑类型常量在 **`server.h`**：`OBJ_STRING`、`OBJ_LIST`、`OBJ_SET`、`OBJ_ZSET`、`OBJ_HASH`、`OBJ_MODULE`、`OBJ_STREAM` 等；**编码**在 **`object.h`** 的 `OBJ_ENCODING_*`。

核心骨架仍是：**`type`（对外）+ `encoding`（对内）+ `ptr`（payload）**；新版还有 `kvobj`、元数据位域等，读注释比背博客稳。

```
/* 摘自 src/object.h —— 仅示意，以仓库实际代码为准 */
struct redisObject {
    unsigned type:4;
    unsigned encoding:4;
    unsigned refcount : OBJ_REFCOUNT_BITS;
    unsigned iskvobj : 1;
    unsigned metabits :8;
    unsigned lru:LRU_BITS;
    void *ptr;
};
```

**小白**：编码名好长……有没有 cheat sheet？

**大师**：字符串多走 `RAW`/`INT`/`EMBSTR`；Hash 小则 `LISTPACK` 大则 `HT`；List 是 `QUICKLIST`（节点里塞 listpack）；Set 有 `intset`/`listpack`/`hashtable`；ZSet 小 `listpack` 大 `SKIPLIST`（配 dict）；Stream 是 radix tree + listpack。**`ZIPMAP`/`ZIPLIST`/`LINKEDLIST` 已是史书上的名字**，新逻辑路径上别再指望它们。

---

**大师**：何时「变身」？别看玄学，看 **`redis.conf` + `server.h` 里 `redisServer`**：`hash-max-listpack-*`、`set-max-*`、`zset-max-listpack-*`、`list-max-listpack-size`、`stream-node-*`、`hll-sparse-max-bytes`……改阈值，`OBJECT ENCODING` 跟着变——**这是调优与故障复盘时最便宜的实验**。

**小白**：弟子明白了：对外吹牛用 `TYPE`，对内算账用 `ENCODING`。

**大师**：然。下面用实例把嘴皮子磨成肌肉记忆。

---

### 一、字符串：不止「一串字符」

**大师**：`SET` 一个纯数字、短串、长串，各看 `OBJECT ENCODING`。

**小白**：（操作后）`int`、`embstr`、`raw`……短串 44 字节界限在 `object.c` 的 `OBJ_ENCODING_EMBSTR_SIZE_LIMIT`，与 jemalloc 64B 槽位有关。

`object.c` 里写得分明：

```
#define OBJ_ENCODING_EMBSTR_SIZE_LIMIT 44
```

```
local:0>object encoding test1
"int"
local:0>object encoding test2
"embstr"
local:0>object encoding test3
"raw"
local:0>get test1
"10000"
local:0>get test2
"hello world!"
local:0>get test3
"Redis is not a plain key-value store, it is actually a data structures server, supporting different kinds of values. What this means is that, while in traditional key-value stores you associated string keys to string values, in Redis the value is not limited to a simple string, but can also hold more complex data structures. The following is the list of all the data structures supported by Redis, which will be covered separately in this tutorial:"
```

**大师**：大 value 一塞，编码未必变，**延迟与带宽**先变——这与九式里「破剑」说的分寸是同一道理。

---

### 二、Hash：listpack 与 dict 两副脸

```
local:0>hmset hashtest1 field1 value1 field2 value2 field3 value3
"OK"
local:0>object encoding hashtest1
"listpack"
local:0>hset hashtest2 field1 "Redis modules can access Redis built-in data structures both at high level, by calling Redis commands, and at low level, by manipulating the data structures directly."
"1"
local:0>object encoding hashtest2
"hashtable"
```

**大师**：阈值来自 `hash-max-listpack-entries` / `hash-max-listpack-value`，实现见 `t_hash.c` 的 `hashTypeConvert*`。与《破刀式》联动：**field 多、value 大，别无脑 `HGETALL`**。

---

### 三、List：quicklist 一统

```
local:0>rpush listtest1 value1 value2 value3 value4 value5
"5"
local:0>object encoding listtest1
"quicklist"
local:0>rpush listtest2 "Redis modules can access Redis built-in data structures both at high level, by calling Redis commands, and at low level, by manipulating the data structures directly."
"1"
local:0>object encoding listtest2
"quicklist"
```

**大师**：节点大小、压缩看 `list-max-listpack-size`、`list-compress-depth`，源码在 `quicklist.c` / `t_list.c`。

---

### 四、Set：intset / listpack / HT

```
local:0>sadd settest1 1 2 3
"3"
local:0>object encoding settest1
"intset"
local:0>sadd settest2 "hello world!"
"1"
local:0>object encoding settest2
"hashtable"
```

**大师**：中间态还可能出现 `listpack`，以 `t_set.c` 为准。

---

### 五、ZSet 与 Geo：score 背后的戏法

```
local:0>zadd zsettest1 10 value1 20 value2 30 value3
"3"
local:0>object encoding zsettest1
"listpack"
local:0>zadd zsettest2 60 "Redis modules can access Redis built-in data structures both at high level, by calling Redis commands, and at low level, by manipulating the data structures directly."
"1"
local:0>object encoding zsettest2
"skiplist"
```

**大师**：GEO 在 `t_geo.c` 里**借 ZSet 存身**，`TYPE` 仍是 `zset`，编码随规模在 listpack / skiplist 间变。

```
local:0>GEOADD Sicily 13.361389 38.115556 "Palermo" 15.087269 37.502669 "Catania"
"2"
local:0>object encoding Sicily
"listpack"
```

---

### 六、HyperLogLog 与 Bitmap

```
local:0>PFADD hll a b c d e f g
"1"
local:0>object encoding hll
"raw"
```

**大师**：HLL 常以 string 形态承载稀疏/稠密编码；若 key 尚空，个别版本上 `OBJECT` 可能暂无编码，属「尚未成器」，莫大惊小怪。

```
local:0>select 5
"OK"
local:5>setbit mykey 5 1
"0"
local:5>getbit mykey 5
"1"
local:5>get mykey
""
local:5>object encoding mykey
"raw"
```

**大师**：Bitmap 在命令谱上归 String 门下，实现却在 **`bitops.c`**——《破箭式》已表过，此处不赘。

---

**番外：内视之后，该翻哪几页源码？**

**小白**：弟子想从文档对照到循环，该按什么顺序读？

**大师**：`object.c`（建对象、选编码）→ 各 `t_*.c`（命令入口）→ `server.h` / `redis.conf`（阈值）。记住一张表：

| 话题 | 文件 | 记一句 |
|------|------|--------|
| 对象 | `object.h`, `object.c` | type + encoding + ptr |
| 字符串 | `t_string.c` | SDS |
| Hash | `t_hash.c` | listpack ↔ dict |
| List | `t_list.c`, `quicklist.c` | quicklist |
| Set | `t_set.c` | intset / listpack / HT |
| ZSet / GEO | `t_zset.c`, `t_geo.c` | listpack ↔ skiplist |
| 位操作 | `bitops.c` | 仍是 String |
| HLL | `hyperloglog.c` | PF* 族 |

**小白**：背完是不是就能下山？

**大师**：背完只能过铜人第一关。真下山，还要会 **`MEMORY USAGE`、`LATENCY`、`SLOWLOG`** 与线上曲线对质。

---

**收式小结**

1. **`TYPE` 看门面，`OBJECT ENCODING` 看装修。**
2. **listpack 已是紧凑存储主力，老 ziplist 文章当史料读。**
3. **阈值在 `redis.conf` 与 `redisServer` 成对出现，动一处，观 `ENCODING` 之变。**
4. **复杂度不只写在文档里，还写在 `t_*.c` 的循环里。**

**小白**：弟子这式叫作「内视」，果然越看越觉得自己以前是在「盲打」……

**大师**：盲打若能中靶，也算天赋；能内视，才算入门。今日到此，回去用三个真实业务 key 各跑一遍 `TYPE` + `OBJECT ENCODING`，写一页笔记再来。

**小白**：恭送大师！

> **花絮**：第一次看编码切换像变戏法，多看几次「改配置 → 重启或写入触发 → 再 `OBJECT`」，戏法就变算术了。
