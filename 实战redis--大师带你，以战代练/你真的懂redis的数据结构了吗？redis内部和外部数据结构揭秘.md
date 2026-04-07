> Redis有哪些数据结构？
> 
> 字符串String、字典Hash、列表List、集合Set、有序集合SortedSet。

很多人面试时都遇到过这种场景吧？

其实除了上面的几种常见数据结构，还需要加上数据结构**HyperLogLog、Geo、Bitmap**。

可是很多人不知道redis 不仅有上面的几种数据结构，还内藏了内部的数据结构。即redis可以分为外部数据结构和内部数据结构。

**1. 如何查看redis的数据结构？**

1.1 如何查看redis的外部数据结构？

可以使用type命令，返回key的类型，如string, list, set, zset, hash 和stream，实例如下：

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

1.2 如何查看redis的内部数据结构

可以通过Object命令来查看。object命令允许从内部察看给定 key 的 Redis 对象。

它通常用在除错(debugging)或者了解为了节省空间而对 key 使用特殊编码的情况。

当将Redis用作缓存程序时，你也可以通过它命令中的信息，决定 key 的驱逐策略(eviction policies)。

**2.redis数据结构的定义redisObject**

内部数据类型server.h

```
typedef struct redisObject {
 unsigned type:4;
 unsigned encoding:4;
 unsigned lru:LRU_BITS; /* LRU time (relative to global lru_clock) or
 * LFU data (least significant 8 bits frequency
 * and most significant 16 bits access time). */
 int refcount;
 void *ptr;
} robj;
```

其中，type为redis的外部数据结构，encoding为redis的内部数据结构实现

type的值如下：

```
/*-----------------------------------------------------------------------------
 * Data types
 *----------------------------------------------------------------------------*/
/* A redis object, that is a type able to hold a string / list / set */
/* The actual Redis Object */
#define OBJ_STRING 0 /* String object. */
#define OBJ_LIST 1 /* List object. */
#define OBJ_SET 2 /* Set object. */
#define OBJ_ZSET 3 /* Sorted set object. */
#define OBJ_HASH 4 /* Hash object. */
/* The "module" object type is a special one that signals that the object
 * is one directly managed by a Redis module. In this case the value points
 * to a moduleValue struct, which contains the object value (which is only
 * handled by the module itself) and the RedisModuleType struct which lists
 * function pointers in order to serialize, deserialize, AOF-rewrite and
 * free the object.
 *
 * Inside the RDB file, module types are encoded as OBJ_MODULE followed
 * by a 64 bit module type ID, which has a 54 bits module-specific signature
 * in order to dispatch the loading to the right module, plus a 10 bits
 * encoding version. */
#define OBJ_MODULE 5 /* Module object. */
#define OBJ_STREAM 6 /* Stream object. */
```

encoding的值如下：server.h

```
/* Objects encoding. Some kind of objects like Strings and Hashes can be
 * internally represented in multiple ways. The 'encoding' field of the object
 * is set to one of this fields for this object. */
#define OBJ_ENCODING_RAW 0 /* Raw representation */
#define OBJ_ENCODING_INT 1 /* Encoded as integer */
#define OBJ_ENCODING_HT 2 /* Encoded as hash table */
#define OBJ_ENCODING_ZIPMAP 3 /* Encoded as zipmap */
#define OBJ_ENCODING_LINKEDLIST 4 /* No longer used: old list encoding. */
#define OBJ_ENCODING_ZIPLIST 5 /* Encoded as ziplist */
#define OBJ_ENCODING_INTSET 6 /* Encoded as intset */
#define OBJ_ENCODING_SKIPLIST 7 /* Encoded as skiplist */
#define OBJ_ENCODING_EMBSTR 8 /* Embedded sds string encoding */
#define OBJ_ENCODING_QUICKLIST 9 /* Encoded as linked list of ziplists */
#define OBJ_ENCODING_STREAM 10 /* Encoded as a radix tree of listpacks */
```

内部类型总结

![](http://p9.toutiaoimg.com/large/pgc-image/4976fc65659d4bad82211b8adc4ef522)

**3.数据结构的限制server.h**

```
/* Zipped structures related defaults */
#define OBJ_HASH_MAX_ZIPLIST_ENTRIES 512
#define OBJ_HASH_MAX_ZIPLIST_VALUE 64
#define OBJ_SET_MAX_INTSET_ENTRIES 512
#define OBJ_ZSET_MAX_ZIPLIST_ENTRIES 128
#define OBJ_ZSET_MAX_ZIPLIST_VALUE 64
#define OBJ_STREAM_NODE_MAX_BYTES 4096
#define OBJ_STREAM_NODE_MAX_ENTRIES 100
/* HyperLogLog defines */
#define CONFIG_DEFAULT_HLL_SPARSE_MAX_BYTES 3000
```

**4.实例**

4.1 字符串String

int :8个字节的长整型

embstr：小于44个字节的字符串(目前)，3.0以前的版本为39

raw：大于39个字节小于512MB的字符串

object.c

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

4.2 哈希hash

当filed的个数少于512，且没有value大于64字节时，内部编码为ziplist

当filed的个数大于512，或者value大于64字节时，内部编码为hashtable

```
Connected.
local:0>hmset hashtest1 field1 value1 field2 value2 field3 value3
"OK"
local:0>object encoding hashtest1
"ziplist"
local:0>hset hashtest2 field1 "Redis modules can access Redis built-in data structures both at high level, by calling Redis commands, and at low level, by manipulating the data structures directly."
"1"
local:0>object encoding hashtest2
"hashtable"
local:0>
```

4.3 列表list

redis 3.2 之前

当列表list中的元素个数少于512，且没有value大于64字节时，内部编码为ziplist

当列表list中的元素个数大于512，或者value大于64字节时，内部编码为linkedlist

redis 3.2 之后

都使用quicklist

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

4.4 集合set

当集合set中的元素都是整数且元素个数小于512(默认时)使用intset

其它条件使用hashtable

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

4.5 有序集合zset

当有序集合zse中的元素个数少于128(默认)，且没有value大于64字节时，内部编码为ziplist

当有序集合zse中的元素个数大于128(默认)，或者value大于64字节时，内部编码为skiplist

```
Connected.
local:0>zadd zsettest1 10 value1 20 value2 30 value3
"3"
local:0>object encoding zsettest1
"ziplist"
local:0>zadd zsettest2 60 "Redis modules can access Redis built-in data structures both at high level, by calling Redis commands, and at low level, by manipulating the data structures directly."
"1"
local:0>object encoding zsettest2
"skiplist"
local:0>
```

4.6 Geo

```
Connected.
local:0>GEOADD Sicily 13.361389 38.115556 "Palermo" 15.087269 37.502669 "Catania"
"2"
local:0>object encoding Sicily
"ziplist"
local:0
```

4.7 HyperLogLog

```
Connected.
local:0>PFADD hll a b c d e f g
"1"
local:0>object encoding h11
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

1. 外部数据结构类型可以通过type来查看

2.内部数据结构类型可以通过object来查看

3. 理解内部数据结构的实现有助于我们深入理解redis

4. 可以复习一下数据结构及其实现
