本故事纯属虚构，如有雷同，纯属巧合。

> 剑乃兵中皇者，刀乃兵中霸者！剑走轻灵，刀重刚猛！！破刀式破解诸般单刀、双刀、柳叶刀、鬼头刀、大砍刀、斩马刀种种刀法。

**故事背景**

![](http://p6.toutiaoimg.com/large/dfic-imagehandler/6644f23b-7f5d-4bbb-a1a2-24a8255e5011)

屠龙刀，金庸小说 《倚天屠龙记》中的第一号宝刀

屠龙刀中藏有岳飞的百胜兵法《武穆遗书》，倚天剑里藏有最上乘的武功秘籍《九阴真经》。“屠龙”之意，便是靖蓉夫妇期望有缘人得之，推翻蒙古政权，杀死蒙古皇帝，光复汉人江山。襄阳沦陷后，江湖上散布「武林至尊，宝刀屠龙，号令天下，莫敢不从，倚天不出，谁与争锋」的流言。襄阳城破之日，屠龙刀的主人郭破虏战死殉国，屠龙刀从此流落江湖，因为「武林至尊，宝刀屠龙，号令天下，莫敢不从」之传闻，引起武林中无数人的争夺

。

**Redis 破刀式**

> **江湖类比**：屠龙刀里藏的是秘籍，你若把整颗 `UserInfo` 大 blob 一把塞进 Redis，好比每次只想查《武穆遗书》某一页，却要把整柄刀扛起来称重——腰酸背痛，多半是带宽与序列化，而非内功不济。

**大师**：今天我们主要是学习破刀式，在这之前，我们先检查一下你昨天的破剑式的情况。先说一下昨天用户信息如何存储？

**小白**：我严格按照数据库设计规范，将用户在 Redis 中的 key 设置为：数据库：数据表：主键，示例：用户 id 为 123 的信息 key 表示：user:UserInfo:123，value 是用户实体，定义如下：

```
import java.io.Serializable;
import java.util.Date;
public class UserInfo implements Serializable {
    private String userName;
    private Date birthday;
    private int age;
    private boolean gender;
    //..........
}
```

**大师**：这种方式，有没有什么问题呢？提示：用户实体在网络传输前先要序列化，然后存入 Redis，从 Redis 获取数据时，也要反序列化后转成实体使用。

**小白**：确实，序列化和反序列化会降低性能，那么可以将 UserInfo 转为 JSON 存储，因为 JSON 字符串的存储和获取不需额外的序列化和反序列化。

**大师**：Json 虽好，莫要贪杯——字段一多，你照样得整包搬运。

**小白**：……大师，那叫 JSON，三个字母都要大写，弟子在代码审查里被骂过。

**大师**：善。可见你已经尝过「命名之棍」的滋味。下面教你分册保管，只取一页，不扛整刀。

**大师**：你的悟性很好，还有一个问题，如果用户的字段比较多，但我查询或者修改仅仅是其中一个字段 userName，那么每次都要完整地读取整个用户的信息，是不是对网络消耗比较大，有没有更好的方式呢？

**小白**：这个我暂时还不知道，请大师赐教！

**大师**：这就是我们今天要讲的 Redis 破刀式。

**Redis hash 是一个 string 类型的 field 和 value 的映射表，hash 特别适合用于存储对象**。Redis 中每个 hash 可以存储 2 的 32 次方 - 1 键值对（40 多亿）。

存储用户如下图所示：

![](http://p26.toutiaoimg.com/large/pgc-image/79dedc2093f64e528c0dc1e488ed9c84)

使用命令行的示例如下：

```
Connected.
local:0>hmset user:userinfo:123 username "www" age "30" birthday "1990-10-01" gender "m"
"OK"
local:0>hgetall user:userinfo:123
1) "username"
2) "www"
3) "age"
4) "30"
5) "birthday"
6) "1990-10-01"
7) "gender"
8) "m"
local:0>hget user:userinfo:123 username
"www"
local:0>hget user:userinfo:123 age
"30"
local:0>hget user:userinfo:123 birthday
"1990-10-01"
local:0>hget user:userinfo:123 gender
"m"
local:0>
```

存储的内容如下：

![](http://p9.toutiaoimg.com/large/pgc-image/5d91b913d4d742f19c7858e550ea1194)

**小白**：大师，破刀式看着功能很是强大，那么它有什么缺点吗？

**大师**：上面的示例程序其实存在一个问题，你能不能看出来？

**小白**：因为上面的数据都存储在内存中，对内存的压力是不是比较大？

**大师**：是的，Redis 虽然支持持久化，但我们一般不把 Redis 当作数据库来使用，毕竟，它的内存是有限的，我们往往使用 Redis 做缓存服务器，缓存一些需要快速加载的数据，缓存的话，一般要求有一个有效周期，让我们可以自己管理这些数据。让我们先看一个简单示例

```
local:0>ttl user:userinfo:123
"-1"
local:0>ttl user:userinfo:123
"-1"
local:0>expire user:userinfo:123 60
"1"
local:0>ttl user:userinfo:123
"45"
local:0>ttl user:userinfo:123
"35"
local:0>ttl user:userinfo:123
"18"
local:0>ttl user:userinfo:123
"2"
local:0>ttl user:userinfo:123
"-2"
local:0>ttl user:userinfo:123
"-2"
local:0>hgetall user:userinfo:123
local:0>
```

我们可以通过 expire 设置过期时间，如果不设置过期时间，默认是永久存储的(除非服务器关闭)。

**小白**：大师，如果有让某些 key 在凌晨失效，如何实现呢？

**大师**：Redis 本身提供了 **EXPIREAT** 指令可以完成，当然你也可以通过计算，得出凌晨与当前时间的间隔，然后使用 EXPIRE 指令。言归正传，因为 EXPIRE 这类指令只针对 key，没有针对 field 的设置，故 HSET 不能精确控制 key 内部的每个 field 的有效期，这是 HSET 的一个缺点；其它的缺点你需要慢慢探索。下面我们破刀式专有的招式一一给你讲解一遍：

> HDEL 删除 field
> 
> HEXISTS 判断 field 是否存在
> 
> HGET 获取 field
> 
> HGETALL 获取 key 的所有 field，包含值
> 
> HINCRBY 对 field 的值整数加
> 
> HINCRBYFLOAT 对 field 的值浮点数加
> 
> HKEYS 获取 key 的所有 field，不含有值
> 
> HLEN 获取 key 的 field 的个数
> 
> HMGET 获得 key 的多个 field 的值
> 
> HMSET 设置 key 的多个 field 的值
> 
> HSCAN 对 key 中 field 较多情况下，可以分页显示
> 
> HSET 设置 key 的 field 的值
> 
> HSETNX 如果 field 不存在，则设置该 field
> 
> HSTRLEN 获取 field 的 value 字符串的长度
> 
> HVALS 获取 key 的所有 field 的 value 值列表

这些招式，先全部记住，回去以后慢慢练习，直至可以随手使出。有个问题，你要思考一下：可否将所有用户信息存放到一个 hash key 里面，其 field 包含一个用户的 key，value 包含其它用户信息？

**小白**：弟子斗胆一问：这题是不是陷阱？把千万用户全塞进一个 Hash，好比在城门口立一块「天下英雄榜」，谁路过都要拓印整张——门都要被挤塌。

**大师**：唔，你悟了八分。剩下两分留到生产环境用慢查询与内存曲线教你。

**番外：Hash 在源码里是两副面孔**

**大师**：你嫌整对象 GET/SET 浪费带宽，HGET 只取一域——内核里 Hash 也分**紧凑编码**和**哈希表**两档。读 `t_hash.c` 开头注释：小 Hash 用 **listpack** 存 field-value 对，超过阈值再 **`hashTypeConvert` 成 `OBJ_ENCODING_HT`（真正的 dict）**。阈值来自 `redis.conf` 的 `hash-max-listpack-entries` / `hash-max-listpack-value`（本仓库默认与 `redis.conf` 中 512 / 64 一致），在 `server.h` 的 `redisServer` 结构里对应 `hash_max_listpack_entries` 等字段。

**小白**：那 `OBJECT ENCODING` 会看到什么？

**大师**：常见是 `listpack` 或 `hashtable`；若用上**域过期（Hash Field Expiration）**等扩展，还会看到 `listpack-ex` 路径，源码在 `t_hash.c` 的 `listpackEx*` 一族函数里，把 TTL 与 listpack 里的三元组编排在一起。实战含义是：字段多、值大时编码会升级，**内存与单次 HGETALL 的代价都会跳变**——所以生产上仍要控制 field 数量、避免无脑 `HGETALL` 大 Hash。

**大师**：好，今天到这里，回去再完善一下用户加载到缓存的方案，下次我们继续讨论。

**小白**：大师，走好！
