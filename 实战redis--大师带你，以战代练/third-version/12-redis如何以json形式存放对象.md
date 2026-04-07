# 背景

> **话外音**：把对象塞进 Redis，就像把行李箱塞进高铁行李架——要么整箱硬塞（序列化 blob），要么把衣服分袋挂钩（多 key / Hash field）。没有魔法，只有取舍。

在 Java 程序中，Redis 提供的方法插入的 key,value 要么是 string,要么就是 byte[]数组。那如果是要插入的 value 是个对象怎么办呢？一种方式是将对象转换成 JSON 然后传送。另外一种 Redis 使用 JSON 序列化。

# 示例

**1.创建一个包含 web 和 Redis 的 spring boot 项目**

**pom.xml**

![](http://p26.toutiaoimg.com/large/pgc-image/ccae5f01cdef407fb5bdaeaf2ab97c5b)

**2.存储对象**

![](http://p9.toutiaoimg.com/large/pgc-image/bd3f81bad7004f82940b08401ee60fd3)

**3.配置 Redis，使用 Jackson2JsonRedisSerializer 作为序列化类**

![](http://p3.itoutiaoimg.com/large/pgc-image/68778a5b24a54cc4b68858bf14ca41d0)

**4.测试存储字符串和对象的时间<本机情况，仅供参考>**

![](http://p26.toutiaoimg.com/large/pgc-image/3bfab020232e4279b9d88e9f254c52ed)

**5.测试结果**

使用 Jackson2JsonRedisSerializer 作为序列化方式的设置和获取速度比 StringRedisSerializer 快超出的预计。数据仅供参考。

![](http://p6.toutiaoimg.com/large/pgc-image/fce06ff6ce474f9d80d993c7117c113e)

# 总结

使用 Jackson2JsonRedisSerializer 作为序列化方式，可以大大简化代码，不用提前转换成 JSON 字符串再手动解析为对象，代码会清爽很多。

> **冷幽默**：JSON 再优雅，也改变不了「改一个字段仍要整包搬运」的物理定律——除非你愿意多写点 key 设计，与《破刀式》握手言和。

---

## 和 Redis 源码的对应关系（加深一层）

无论你在客户端把对象打成 JSON 还是二进制，**到达 Redis 服务端时仍然是 `SET`/`GET` 那套字符串路径**：值被建成 `robj`，走 `OBJ_STRING` + `RAW`/`EMBSTR`/`INT` 等编码（见 `object.h`、`object.c`、`t_string.c`）。因此：

- **优点**：实现简单，与语言生态结合紧。
- **代价**：无法像 **Hash** 那样单字段 `HGET`/`HSET` 更新；大 JSON 每次整包读写，带宽与反序列化 CPU 与《破刀式》里大师说的“只改一个字段却拉全量”是同一类问题。

若字段多、更新局部频繁，可在业务层仍用 JSON 存 Redis，但 key 设计改为 **Hash + field** 或 **多 key**；对照 `t_hash.c` 的 listpack→hashtable 升级逻辑，你会更清楚何时该拆 key、何时该控 field 数量。
