本故事纯属虚构，如有雷同，纯属巧合。

> 剑乃兵中皇者，刀乃兵中霸者！剑走轻灵，刀重刚猛！！破刀式破解诸般单刀、双刀、柳叶刀、鬼头刀、大砍刀、斩马刀种种刀法。

**故事背景**

![](http://p6.toutiaoimg.com/large/dfic-imagehandler/6644f23b-7f5d-4bbb-a1a2-24a8255e5011)

屠龙刀，金庸小说 《倚天屠龙记》中的第一号宝刀

屠龙刀中藏有岳飞的百胜兵法《武穆遗书》，倚天剑里藏有最上乘的武功秘籍《九阴真经》。“屠龙”之意，便是靖蓉夫妇期望有缘人得之，推翻蒙古政权，杀死蒙古皇帝，光复汉人江山。襄阳沦陷后，江湖上散布「武林至尊，宝刀屠龙，号令天下，莫敢不从，倚天不出，谁与争锋」的流言。襄阳城破之日，屠龙刀的主人郭破虏战死殉国，屠龙刀从此流落江湖，因为「武林至尊，宝刀屠龙，号令天下，莫敢不从」之传闻，引起武林中无数人的争夺

。

**Redis破刀式**

**大师**：今天我们主要是学习破刀式，在这之前，我们先检查一下你昨天的破剑式的情况。先说一下昨天用户信息如何存储？

**小白**：我严格按照数据库设计规范，将用户的redis的可以设置为：数据库：数据表：主键，示例：用户id为123的信息key表示：user:UserInfo:123, value是用户实体，定义如下：

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

**大师**：这种方式，有没有什么问题呢？提示：用户实体在网络传输前先要序列化，然后存入redis，从redis获取数据时，也要反序列化后转成实体使用。

**小白**：确实，序列化和反序列化会降低性能，那么可以将UserInfo转为json存储，因为json字符串的存储和获取不需额外的序列化和反序列化。

**大师**：你的悟性很好，还有一个问题，如果用户的字段比较多，但我查询或者修改仅仅是其中一个字段userName，那么每次都要完整的读取或者整个用户的信息，是不是对网络消耗比较大，有没有更好的方式呢？

**小白**：这个我暂时还不知道，请大师赐教！

**大师**：这就是我们今天要讲的Redis破刀式。

**Redis hash 是一个 string 类型的 field 和 value 的映射表，hash 特别适合用于存储对象**。Redis 中每个 hash 可以存储 2的32次方 - 1 键值对（40多亿）。

存储用户如下图所示：

![](http://p26.toutiaoimg.com/large/pgc-image/79dedc2093f64e528c0dc1e488ed9c84)

使用命令行的示例如下：

```
Connected.
local:0>hmset user:userinfo:123 username "www" age "30" birthday "1990-10-01" gendar "m"
"OK"
local:0>hgetall user:userinfo:123
1) "username"
2) "www"
3) "age"
4) "30"
5) "birthday"
6) "1990-10-01"
7) "gendar"
8) "m"
local:0>hget user:userinfo:123 username
"www"
local:0>hget user:userinfo:123 age
"30"
local:0>hget user:userinfo:123 birthday
"1990-10-01"
local:0>hget user:userinfo:123 gendar
"m"
local:0>
```

存储的内容如下：

![](http://p9.toutiaoimg.com/large/pgc-image/5d91b913d4d742f19c7858e550ea1194)

**小白**：大师，破刀式看着功能很是强大，那么它有什么缺点吗？

**大师**：上面的示例程序其实存在一个问题，你能不能看出来？

**小白**：因为上面的数据都存储在内存中，对内存的压力是不是比较大？

**大师**：是的，redis虽然支持持久化，但我们一般不把redis当作数据库来使用，毕竟，它的内存是有限的，我们往往使用redis做缓存服务器，缓存一些需要快速加载的数据，缓存的话，一般要求有一个有效周期，让我们可以自己管理这些数据。让我们先看一个简单示例

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

我们可以通过expire设置过期时间，如果不设置过期时间，默认是永久存储的(除非服务器关闭)。

**小白**：大师，如果有让某些key在凌晨失效，如何实现呢？

**大师**：redis本身提供了**EXPIREAT**指令可以完成，当然你直接也可以通过计算，得出凌晨和当前时间的间隔，然后使用expire指令。言归正传，因为expire类似的指令只针对于key，没有针对于field的设置，故hset不能精确控制key内部的每个field的有效期，这是hset的一个缺点，其它的缺点你需要满满探索，下面我们破刀式专有的招式一一给你讲解一遍：

> HDEL 删除field
> 
> HEXISTS 判断field是否存在
> 
> HGET 获取field
> 
> HGETALL获取key的所有field，包含值
> 
> HINCRBY 对field的值整数加
> 
> HINCRBYFLOAT对field的值浮点数加
> 
> HKEYS 获取key的所有filed，不含有值
> 
> HLEN 获取key的field的个数
> 
> HMGET 获得key的多个field的值
> 
> HMSET 设置key的多个field的值
> 
> HSCAN 对key中field较多情况下，可以分页显示
> 
> HSET 设置key的field的值
> 
> HSETNX 如果filed不存在，则设置该field
> 
> HSTRLEN 获取field的value字符串的长度
> 
> HVALS 获取key的所有field的value值列表

这些招式，先全部记住，回去以后满满练习，直至可以随意记得。有个问题，你要思考一下：可否将所有用户信息存放到一个hash key里面，其field包含一个用户的key，value包含其它用户信息？

**大师**：好，今天到这里，回去再完善一下用户加载到缓存的方案，下次我们继续讨论。

**小白**：大师，走好！
