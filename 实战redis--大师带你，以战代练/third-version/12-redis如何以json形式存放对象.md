本故事纯属虚构，如有雷同，纯属巧合。

> 《射雕》里黄蓉厨下功夫好，只因懂得**切配**：肉丝、笋丝分盘下锅，才快。把对象塞进 Redis，道理一样——**整锅炖（大 JSON）省事，单炒一碟（Hash field）才省带宽**。

**故事背景**

Java 里连 Redis，API 往往只认 `String` 或 `byte[]`。业务同学捧来一个 `UserDTO`，总不能 `toString()` 糊弄——于是**序列化**登场：有人手写 JSON，有人用 **Jackson2JsonRedisSerializer**，还有人祭出 **Kryo、Protobuf**……各显神通，也各埋其坑。

**Redis 别传：封箱式——Java 里 JSON 与 Redis 如何握手**

**大师**：你手头的对象，打算怎么进 Redis？

**小白**：转成 JSON 字符串 `SET` 进去，或者……用 Spring Data Redis 配个 `RedisTemplate`，让框架帮我转。

**大师**：两条路都通。但你要先想清三件事：**谁序列化、谁反序列化、改一个字段要不要整箱搬**。想不清的，上线后 CPU 与带宽教你做人。

**小白**：弟子用 Spring Boot，跟您走一遍？

**大师**：可。先搭灶，再下锅。

---

### 第一锅：建项与依赖

**大师**：建一个同时带 **Web** 与 **Redis** 的 Spring Boot 工程，把 Redis 与 JSON 相关依赖放进 `pom.xml`。

**小白**：照图搭好。

**pom.xml**

![](http://p26.toutiaoimg.com/large/pgc-image/ccae5f01cdef407fb5bdaeaf2ab97c5b)

**大师**：版本号与 BOM 随团队规范走；**生产务必锁版本**，莫让「最新版」在半夜给你惊喜。

---

### 第二锅：把对象放进 Redis

**大师**：Controller 或 Service 里注入 `RedisTemplate`，`opsForValue().set("user:1", user)` 一类写法，你按项目习惯来。

**小白**：存进去了，截图留证。

![](http://p9.toutiaoimg.com/large/pgc-image/bd3f81bad7004f82940b08401ee60fd3)

**大师**：Redis 里看见的 value 应是可读 JSON——**别在生产开 beautify 日志把隐私打满屏**，切记。

---

### 第三锅：换 serializer——Jackson2JsonRedisSerializer

**大师**：默认 `JdkSerialization` 二进制一团黑，排查痛苦。换 **Jackson2JsonRedisSerializer**，人眼可读、跨语言友好些，但要约定 **类型信息**（`@class` 字段或自定义 TypeId）否则反序列化会懵。

**小白**：按图配 `RedisTemplate` 的 key/value serializer。

![](http://p3.itoutiaoimg.com/large/pgc-image/68778a5b24a54cc4b68858bf14ca41d0)

**大师**：Key 建议仍用 **StringRedisSerializer**，避免 key 也被 JSON 包一层不好运维。

---

### 第四锅：掐表比一比（本机仅供参考）

**大师**：同样逻辑下，对比 **纯 String** 与 **Jackson JSON** 的 set/get 耗时，写进你的压测脚本——**本机截图只作相对趋势，不可当 SLA**。

![](http://p26.toutiaoimg.com/large/pgc-image/3bfab020232e4279b9d88e9f254c52ed)

**小白**：测完了。

![](http://p6.toutiaoimg.com/large/pgc-image/fce06ff6ce474f9d80d993c7117c113e)

**大师**：若 Jackson 路径更快，多半是**少了一层手动 `writeValueAsString`/`readValue` 的胶水代码**；若更慢，查 **对象嵌套深度、null 字段、PrettyPrint 误开**。

---

**大师**：代码清爽了，**物理定律**变没变？

**小白**：没变……改一个昵称仍要 `GET` 整坨 JSON。

**大师**：正是。无论你在客户端打成 JSON 还是二进制，**进服务端仍是 `OBJ_STRING` 那条路**（`object.h` / `t_string.c`），`HGET` 单字段的便宜占不到。

**小白**：那何时还坚持用 JSON String？

**大师**：**读多写少、整对象替换、与前端/移动端直接对协议**时，JSON 省事；**字段高频局部更新**时，改 **Hash 多 field** 或 **多 key**，回去翻《破刀式》。

---

**番外：除了 Jackson，江湖上还有几路人马**

**大师**：简记一笔，免你面试只答一半。

1. **Redis Stack / RedisJSON 模块**：值侧原生 JSON 路径（视部署与许可而定），与「String 里塞 JSON」不是一回事。
2. **Protobuf / Kryo / FST**：体积与速度往往更香，**可读性与跨语言**要单独设计。
3. **Pipeline / 批量**：对象再小，也怕**往返风暴**——与序列化格式无关的优化。

**小白**：弟子记下了。还有坑吗？

**大师**：**缓存穿透类 key、大 key、热 key** 不解决，换十种 serializer 也是裱糊。上线前用 `MEMORY USAGE`、慢查询、监控项过一遍。

---

**收式小结**

- **Jackson2JsonRedisSerializer** 换的是「客户端省心」，不换「服务端仍是字符串」。
- **Key 规范、字段裁剪、版本演进**（DTO 加字段）要比 serializer 名字更要紧。
- **大对象 + 高频局部更新** → 优先考虑 **Hash / 多 key**，与 JSON 并不对立，可混用。

**小白**：大师，弟子这式叫「封箱」，封的是对象，露的是 JSON……

**大师**：封得好是锦囊，封不好是棺材——**控制体积、控制更新粒度**，方可下山送货。

**小白**：恭送大师！

> **冷幽默**：JSON 再优雅，也改变不了「只改一个字段仍要整包搬运」——除非你愿意与 Hash 握手言和。
