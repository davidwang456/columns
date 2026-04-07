> Redis 有哪些数据结构？
> 
> 字符串 String、字典 Hash、列表 List、集合 Set、有序集合 SortedSet。

很多人面试时都遇到过这种场景吧？

其实除了上面的几种常见数据结构，还需要加上数据结构**HyperLogLog、Geo、Bitmap**。

可是很多人不知道 Redis 不仅有上面的几种数据结构，还内藏了内部的数据结构。即 Redis 可以分为外部数据结构和内部数据结构。

> **吐槽役上线**：面试背「String Hash List Set ZSet」就像报菜名——好听，但端上桌的可能是 `embstr`、可能是 `quicklist`、也可能是「你以为是个 Hash 其实内核正在偷偷升级编码」。本文专治「背了类型却看不懂 `OBJECT ENCODING`」的胸闷。

**1. 如何查看 Redis 的数据结构？**

1.1 如何查看 Redis 的外部数据结构？

可以使用 type 命令，返回 key 的类型，如 string, list, set, zset, hash 和 stream，实例如下：

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
redis> 
```

1.2 如何查看 Redis 的内部数据结构

可以通过 Object 命令来查看。object 命令允许从内部察看给定 key 的 Redis 对象。

它通常用在除错(debugging)或者了解为了节省空间而对 key 使用特殊编码的情况。

当将 Redis 用作缓存程序时，你也可以通过它命令中的信息，决定 key 的驱逐策略(eviction policies)。

**2. Redis 数据结构的定义 `redisObject`（本仓库请以 `object.h` 为准）**

逻辑类型常量仍在 `server.h`（`OBJ_STRING`、`OBJ_LIST`、`OBJ_SET`、`OBJ_ZSET`、`OBJ_HASH`、`OBJ_MODULE`、`OBJ_STREAM` 等）；**物理编码**在 `object.h` 的 `OBJ_ENCODING_*`。`redisObject`（别名 `robj`）的完整定义与内存布局说明见 `object.h` 文件头长注释；核心字段仍是：**`type`（对外类型）+ `encoding`（内部实现）+ `ptr`（指向具体结构）**，新版还区分了 **`kvobj` 路径**（键与元数据嵌入对象）与更细的 `refcount`/元数据位域，阅读源码时请以当前结构体为准：

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

**encoding（内部实现）一览** — 同样摘自 `object.h`：`RAW`/`INT`/`EMBSTR`（字符串），`HT`（哈希表），`INTSET`，`SKIPLIST`，`QUICKLIST`（由多个 **listpack** 组成的链表），`LISTPACK` / `LISTPACK_EX`（紧凑列表及带扩展元数据的 Hash 等），`STREAM`（Stream 的 radix tree + listpack）。其中 **`ZIPMAP`/`ZIPLIST`/`LINKEDLIST` 标记为历史编码，新版本逻辑路径上已不再使用**。

内部类型总结

![](http://p9.toutiaoimg.com/large/pgc-image/4976fc65659d4bad82211b8adc4ef522)

**3.紧凑编码何时“升级”？看配置与 `redisServer`**

老文章里写的 `OBJ_HASH_MAX_ZIPLIST_*` 等宏，在本仓库已演进为 **`hash-max-listpack-*`、`set-max-listpack-*`、`zset-max-listpack-*`** 等运行时配置，对应 `server.h` 中 `struct redisServer` 的 `hash_max_listpack_entries`、`set_max_intset_entries`、`zset_max_listpack_value`、`list_max_listpack_size`、`stream_node_max_bytes`、`hll_sparse_max_bytes` 等字段；**默认值以 `redis.conf` 为准**（例如本仓库 `redis.conf` 中 `hash-max-listpack-entries 512`、`hash-max-listpack-value 64`）。改这些参数会改变“何时从 listpack/intset 升级为 dict/skiplist”，直接影响内存与单次遍历成本。

**4.实例**

4.1 字符串 String

int :8 个字节的长整型

embstr：小于 44 个字节的字符串(目前)，3.0 以前的版本为 39

raw：大于 39 个字节小于 512MB 的字符串

`object.c`

```
/* Create a string object with EMBSTR encoding if it is smaller than
 * OBJ_ENCODING_EMBSTR_SIZE_LIMIT, otherwise the RAW encoding is
 * used.
 *
 * The current limit of 44 is chosen so that the biggest string object
 * we allocate as EMBSTR will still fit into the 64 byte arena of jemalloc. */
#define OBJ_ENCODING_EMBSTR_SIZE_LIMIT 44
```

验证一下：

```
Connected.
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
local:0>
```

4.2 哈希 hash

在默认配置下，field 数量较少、且单个 value 不超过 `hash-max-listpack-value` 时，多为 **`listpack`** 编码；超过阈值后升级为 **`hashtable`**（`OBJ_ENCODING_HT`）。实现与转换见 `t_hash.c` 中的 `hashTypeConvert` 等逻辑。

```
Connected.
local:0>hmset hashtest1 field1 value1 field2 value2 field3 value3
"OK"
local:0>object encoding hashtest1
"listpack"
local:0>hset hashtest2 field1 "Redis modules can access Redis built-in data structures both at high level, by calling Redis commands, and at low level, by manipulating the data structures directly."
"1"
local:0>object encoding hashtest2
"hashtable"
local:0>
```

4.3 列表 list

本仓库（现代 Redis）列表统一为 **`quicklist`**：链表节点内嵌 **listpack**，通过 `list-max-listpack-size`、`list-compress-depth` 控制节点大小与两端压缩。命令实现见 `t_list.c`，结构见 `quicklist.c`。

```
Connected.
local:0>rpush listtest1 value1 value2 value3 value4 value5
"5"
local:0>object encoding listtest1
"quicklist"
local:0>rpush listtest2 "Redis modules can access Redis built-in data structures both at high level, by calling Redis commands, and at low level, by manipulating the data structures directly."
"1"
local:0>object encoding listtest2
"quicklist"
local:0>
```

4.4 集合 set

元素**全是整数**且数量在 `set-max-intset-entries` 以内时，可用 **`intset`**；否则可能为 **`listpack`** 或 **`hashtable`**，具体分支见 `t_set.c` 的 `setTypeConvert*`。下面示例在默认配置下仍常见 intset / hashtable；若中间经过 listpack 阶段，`OBJECT ENCODING` 会显示 `listpack`。

```
local:0>sadd settest1 1 2 3
"3"
local:0>object encoding settest1
"intset"
local:0>sadd settest2 "hello world!"
"1"
local:0>object encoding settest2
"hashtable"
local:0>
```

4.5 有序集合 zset

默认在小数据量、member 较短时为 **`listpack`**；规模或 member 长度超过 `zset-max-listpack-entries` / `zset-max-listpack-value` 后升级为 **`skiplist` + 哈希表**（`OBJ_ENCODING_SKIPLIST`），见 `t_zset.c` 的 `zsetConvert*`。

```
Connected.
local:0>zadd zsettest1 10 value1 20 value2 30 value3
"3"
local:0>object encoding zsettest1
"listpack"
local:0>zadd zsettest2 60 "Redis modules can access Redis built-in data structures both at high level, by calling Redis commands, and at low level, by manipulating the data structures directly."
"1"
local:0>object encoding zsettest2
"skiplist"
local:0>
```

4.6 Geo

GEO 在实现上基于 **有序集合**（`t_geo.c`），故 `TYPE` 为 `zset`；`OBJECT ENCODING` 随规模在 **`listpack`** 与 **`skiplist`** 之间变化，与 4.5 同一套阈值逻辑。

```
Connected.
local:0>GEOADD Sicily 13.361389 38.115556 "Palermo" 15.087269 37.502669 "Catania"
"2"
local:0>object encoding Sicily
"listpack"
local:0>
```

4.7 HyperLogLog

```
Connected.
local:0>PFADD hll a b c d e f g
"1"
local:0>object encoding hll
null
local:0>object encoding hll
"raw"
local:0>
```

4.8 Bitmap

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
local:5>
```

**5 总结**

1. 外部数据结构类型可以通过 `TYPE` 来查看

2. 内部数据结构类型可以通过 `OBJECT ENCODING` 来查看

3. 理解内部数据结构的实现有助于我们深入理解 Redis；**新版以 listpack 为主紧凑编码，ziplist/zipmap 仅保留为历史兼容**

4. 阈值在 `redis.conf` 与 `server.h` 的 `redisServer` 中成对出现，调参即可观察 `OBJECT ENCODING` 变化

5. 结合本仓库 `t_*.c` 阅读，能把命令时间复杂度从文档落到真实循环上

> **看完仍懵怎么办**：正常。第一次看编码切换像看变戏法，多看几次 `OBJECT ENCODING` 与 `redis.conf` 阈值对照，戏法就变算术了。
