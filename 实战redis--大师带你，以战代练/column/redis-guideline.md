# Redis 实战修炼与原理进阶专栏大纲

> 版本：Redis 8.6.x
> 面向人群：新人开发、测试、核心开发、运维、架构师、资深开发
> 总章节：40 章（基础篇 16 章 / 中级篇 15 章 / 高级篇 9 章）
> 每章独立成文件，字数 3000-5000 字

---

## 专栏定位

本专栏以 Redis 8.6.x 为主线，采用「业务痛点 → 三人剧本对话 → 代码实战 → 总结思考」的章节结构，从单机 Redis 入门、常用数据结构实战，到高并发缓存架构、分布式一致性、性能调优、可观测性，再深入源码、模块扩展与 SRE 落地。整体遵循“实战为主，理论为辅”的原则：每一章先从真实业务问题出发，再解释必要原理，最后用可运行的命令、代码、压测或故障演练完成闭环。

固定剧本角色：
- 小胖：爱吃爱玩、不求甚解，用生活化比喻抛出问题。
- 小白：喜静、喜深入，追问原理、边界条件、风险和备选方案。
- 大师：资深技术 Leader，负责讲透业务约束、技术选型和落地路径。

每章建议结构：
1. 项目背景：用真实或拟真的业务需求引出主题，放大不用 Redis 或误用 Redis 时的痛点。
2. 项目设计：通过小胖、小白、大师多轮对话，解释核心概念、技术取舍和边界条件。
3. 项目实战：提供环境准备、分步实现、命令或代码片段、验证方式、常见坑。
4. 项目总结：归纳优缺点、适用场景、注意事项、生产故障案例、思考题和跨部门协作建议。

---

## 阅读路线建议

| 角色 | 建议阅读顺序 | 重点章节 |
|------|-------------|---------|
| 新人开发/测试 | 基础篇全读，按章节完成命令和代码练习 | 第 1-16 章 |
| 核心开发/运维 | 基础篇速读，中级篇精读，高级篇选读 | 第 17-31 章，辅以第 32-40 章 |
| 架构师/资深开发 | 中级篇和高级篇为主线，按需回溯基础篇 | 第 22-40 章 |

---

# 基础篇（第 1-16 章）

> **核心目标**：建立 Redis 核心概念，掌握单机部署、数据结构、常用命令、基础缓存模式与初级故障排查。
> **实战主线**：用 Redis 为一个电商小系统补齐登录、商品、库存、排行榜、消息通知和基础缓存能力。
> **源码关联**：src/server.c、src/networking.c、src/db.c、src/t_string.c、src/t_hash.c、src/t_list.c、src/t_set.c、src/t_zset.c。

---

## 第1章：Redis 术语全景与单线程工作原理
**定位**：开篇总览，建立 Redis 统一语系和整体架构图。
**核心内容**：
- 术语词典：key、value、db、client、event loop、command、expire、eviction、replication、cluster、module
- Redis 请求生命周期：连接建立、命令解析、执行、响应返回
- 单线程模型与 IO 多路复用：为什么 Redis 可以很快
- Redis 8.x 能力地图：核心数据结构、持久化、复制、集群、模块、查询、向量能力
- 架构图：客户端、网络层、命令执行层、数据结构层、持久化与复制层
**实战目标**：用 Docker 启动 Redis，执行 ping/set/get/info，画出一张可以给团队新人讲解的 Redis 工作原理图。

---

## 第2章：Windows、WSL2 与 Docker 环境搭建
**定位**：把学习环境一次搭稳，减少后续实战阻力。
**核心内容**：
- Windows 原生、WSL2、Docker Desktop 三种实践方式对比
- redis-server、redis-cli、redis-benchmark、redisinsight 的使用
- redis.conf 最小配置：端口、bind、requirepass、appendonly、dir
- Docker Compose 编排单机 Redis 与可视化工具
- 常见启动失败：端口占用、权限、数据目录、密码认证
**实战目标**：编写一份 Docker Compose 文件，启动带密码、AOF、数据挂载和 RedisInsight 的本地实验环境。

---

## 第3章：字符串 String 实战：验证码、计数器与分布式开关
**定位**：从最简单的数据结构开始做业务功能。
**核心内容**：
- set/get/mget/incr/decr/append/strlen/getrange/setrange
- set nx ex 的语义与一次性验证码
- 原子自增计数器：浏览量、接口调用次数、短信发送次数
- bitmap 之前的字符串二进制安全特性
- 大 key 与 value 过大带来的网络和内存问题
**实战目标**：实现短信验证码、防刷计数器和动态功能开关，并用 curl 或脚本验证过期与并发自增。

---

## 第4章：哈希 Hash 实战：用户画像与购物车对象存储
**定位**：用 Hash 存储结构化对象，避免把 Redis 当纯字符串仓库。
**核心内容**：
- hset/hget/hmget/hincrby/hscan
- 用户资料、商品库存快照、购物车条目的建模方式
- Hash 与 JSON 字符串的取舍
- 字段级过期能力与 Redis 8.x 新特性
- 小对象编码与内存占用基础
**实战目标**：实现一个购物车服务，支持添加商品、修改数量、统计总价，并演示字段级过期的使用场景。

---

## 第5章：列表 List 实战：任务队列与最新动态流
**定位**：掌握队列、栈和简单异步任务模型。
**核心内容**：
- lpush/rpush/lpop/rpop/blpop/brpop/lrange/ltrim
- List 作为队列、栈、最新 N 条消息列表
- 阻塞弹出与后台任务消费者
- List 队列的可靠性短板
- List 与 Stream 的适用边界
**实战目标**：实现一个订单异步通知队列，包含生产者、消费者、失败重试和最新 100 条操作日志。

---

## 第6章：集合 Set 实战：标签、好友关系与共同关注
**定位**：用集合表达关系，处理去重和交并差。
**核心内容**：
- sadd/srem/sismember/scard/sinter/sunion/sdiff/sscan
- 用户标签、权限集合、黑白名单建模
- 共同关注、可能认识的人、互斥权限检查
- 大集合 scan 与阻塞风险
- Set 与 Bitmap、Bloom Filter 的取舍
**实战目标**：实现一个社交关系小功能，支持关注、取关、共同关注、推荐好友和黑名单过滤。

---

## 第7章：有序集合 ZSet 实战：排行榜与延时任务
**定位**：掌握 Redis 中最常用的排序能力。
**核心内容**：
- zadd/zrange/zrevrange/zrank/zscore/zincrby/zremrangebyscore
- 积分排行榜、热榜、时间线、延时任务建模
- score 设计：分数、时间戳、复合分值
- 分页、并列名次和历史榜单
- ZSet 内部跳表与哈希表的基本思想
**实战目标**：实现游戏积分排行榜和订单超时关闭延时队列，验证排名变更和定时扫描。

---

## 第8章：Bitmap 与 BitField 实战：签到、活跃用户与状态压缩
**定位**：用位级结构处理海量布尔状态。
**核心内容**：
- setbit/getbit/bitcount/bitop/bitpos
- 用户签到、活跃天数、功能开关矩阵
- bitfield 的整数读写与溢出策略
- Bitmap 的内存优势与稀疏用户 ID 陷阱
- 与 Set 存储用户状态的对比
**实战目标**：实现月度签到系统，统计连续签到、月活用户和多渠道活跃交集。

---

## 第9章：HyperLogLog 实战：接口 UV 与活动去重估算
**定位**：理解近似去重，接受工程上的误差换资源。
**核心内容**：
- pfadd/pfcount/pfmerge
- UV、独立设备数、活动参与人数估算
- 近似统计误差与业务可接受边界
- HLL 与 Set 精确去重的成本对比
- 分片统计与合并
**实战目标**：实现高频接口 UV 统计，比较 Set 精确统计和 HyperLogLog 近似统计的内存差异。

---

## 第10章：GEO 实战：附近的人与门店搜索
**定位**：用 Redis 解决轻量地理位置查询。
**核心内容**：
- geoadd/geosearch/geodist/geopos
- 经纬度存储、半径搜索、矩形搜索
- GEO 底层与 ZSet 的关系
- 精度、排序、分页和距离过滤
- 与专业 GIS/搜索引擎的边界
**实战目标**：实现“附近门店”功能，支持按距离排序、门店类型过滤和结果分页。

---

## 第11章：Key 设计与过期策略：从能用到好维护
**定位**：建立 Redis 项目命名、生命周期和可维护性规范。
**核心内容**：
- key 命名规范：业务域、对象类型、对象 ID、字段含义
- ttl/expire/pexpire/persist 与过期时间设计
- 惰性删除、定期删除与过期风暴
- db 选择、命名空间与多租户隔离
- scan 替代 keys 的生产规范
**实战目标**：为电商系统设计一套 Redis key 规范，并编写脚本扫描不合规 key、无 TTL key 和疑似大 key。

---

## 第12章：缓存入门：旁路缓存与数据库加速
**定位**：把 Redis 接入真实业务读路径。
**核心内容**：
- Cache Aside 模式：先查缓存，未命中查数据库，再写缓存
- 缓存命中率、TTL、序列化格式和对象大小
- 缓存穿透、击穿、雪崩的基础解法
- 本地缓存与 Redis 缓存的协作
- 数据库与缓存一致性的初级策略
**实战目标**：为商品详情接口增加旁路缓存，压测对比接入前后的 QPS、P95 延迟和数据库查询次数。

---

## 第13章：Lua 脚本入门：把多步操作变成原子动作
**定位**：解决简单事务无法表达的原子业务逻辑。
**核心内容**：
- eval/evalsha/script load/script exists
- Lua 脚本参数传递与返回值
- 原子扣库存、限购、计数器复合更新
- 脚本执行时间与阻塞风险
- Lua 与 Redis Function 的演进关系
**实战目标**：用 Lua 实现库存扣减和限购检查，验证并发下不会出现超卖。

---

## 第14章：事务与管道：批量命令的正确姿势
**定位**：区分原子性、隔离性和网络往返优化。
**核心内容**：
- multi/exec/discard/watch 的语义
- 乐观锁与 CAS 更新
- pipeline 减少 RTT 的收益与风险
- 事务、Lua、Pipeline 的适用场景对比
- 批量操作中的错误处理
**实战目标**：实现批量写入用户积分和乐观锁更新账户余额，对比 Pipeline 前后的耗时。

---

## 第15章：Redis 日常运维与故障排查入门
**定位**：从能写命令到能定位基础问题。
**核心内容**：
- info、client list、slowlog、monitor、latency doctor
- 常见错误：连接超时、认证失败、OOM、NOAUTH、WRONGTYPE
- 慢查询定位与命令复杂度意识
- 大 key、热 key、无 TTL key 的初步发现
- 备份、重启和配置变更注意事项
**实战目标**：模拟 5 类常见故障，输出一份新人可执行的 Redis 排查 SOP。

---

## 第16章：【基础篇综合实战】搭建电商缓存与互动系统
**定位**：融会贯通基础篇的数据结构和缓存能力。
**核心内容**：
- 场景：为小型电商系统实现登录验证码、商品详情缓存、购物车、排行榜、签到和附近门店
- 需求拆解：数据结构选型、key 命名、TTL 策略、接口设计
- 分步实现：Docker Compose、Redis 客户端、业务接口、基础压测
- 验收标准：核心接口命中率 > 80%，P95 延迟 < 30ms，无明显大 key 和慢命令
**实战目标**：交付一个可运行的 Redis 电商基础实战项目，并附带接口测试脚本和故障排查清单。

---

# 中级篇（第 17-31 章）

> **核心目标**：掌握分布式缓存架构、并发控制、消息流、持久化、高可用、性能调优、安全和可观测性。
> **实战主线**：把单机 Redis 能力升级为支撑高并发业务的生产级缓存与实时数据平台。
> **源码关联**：src/rdb.c、src/aof.c、src/replication.c、src/cluster.c、src/expire.c、src/evict.c、src/blocked.c。

---

## 第17章：缓存三兄弟：穿透、击穿与雪崩治理
**定位**：解决缓存系统最常见的线上事故。
**核心内容**：
- 缓存穿透：空值缓存、布隆过滤器、参数校验
- 缓存击穿：互斥锁、逻辑过期、热点预热
- 缓存雪崩：TTL 随机化、多级缓存、限流降级
- 热点 key 的识别与拆分
- 事故复盘模板
**实战目标**：模拟商品热点访问和恶意不存在 ID 请求，分别实现互斥锁、逻辑过期和 Bloom Filter 防护。

---

## 第18章：分布式锁实战：从 SET NX 到 Redlock 争议
**定位**：理解 Redis 锁能做什么，不能做什么。
**核心内容**：
- set nx px + value 校验释放锁
- 锁过期、业务超时、误删锁和可重入问题
- 看门狗续期的收益与风险
- Redlock 算法与工程争议
- 锁、数据库唯一约束、消息队列的替代关系
**实战目标**：实现订单防重复提交锁，并通过并发测试验证锁释放、超时和误删保护。

---

## 第19章：库存扣减与秒杀系统：Redis 扛峰值流量
**定位**：用 Redis 处理高并发写入前置削峰。
**核心内容**：
- 预热库存、资格校验、限购、扣减、异步下单
- Lua 保证资格检查和库存扣减原子性
- 队列削峰与最终落库
- 防刷、限流和风控基础
- 超卖、少卖和重复下单的边界处理
**实战目标**：搭建一个简化秒杀系统，完成 1 万并发压测，验证无超卖和下单链路可追踪。

---

## 第20章：Stream 实战：可靠消息、消费组与幂等处理
**定位**：掌握 Redis 自带的轻量消息流能力。
**核心内容**：
- xadd/xread/xgroup/xreadgroup/xack/xpending/xclaim/xautoclaim
- 消费组、Pending List 和消息重投
- 消息 ID 设计与幂等消费
- Stream、List、Pub/Sub、Kafka 的对比
- 积压监控与消费者扩缩容
**实战目标**：实现订单事件流，支持多消费者组、失败重试、死信处理和幂等落库。

---

## 第21章：Pub/Sub 与客户端通知：实时广播的轻与重
**定位**：理解发布订阅适用场景和可靠性边界。
**核心内容**：
- publish/subscribe/psubscribe
- Keyspace Notification 监听过期事件
- Pub/Sub 不持久、不重放、不确认的限制
- 聊天室、配置广播、缓存失效通知
- 与 Stream 的选择标准
**实战目标**：实现一个配置变更广播和过期订单提醒 Demo，并说明消息丢失时的补偿方案。

---

## 第22章：持久化 RDB 与 AOF：数据不丢的底线
**定位**：理解 Redis 重启恢复和故障恢复的基础。
**核心内容**：
- RDB 快照：save/bgsave、fork、copy-on-write
- AOF：appendfsync、rewrite、混合持久化
- RDB 与 AOF 的恢复流程和性能影响
- 数据安全、磁盘 IO 和延迟抖动
- 备份、恢复和演练流程
**实战目标**：分别开启 RDB、AOF 和混合持久化，模拟进程崩溃，比较数据恢复完整性和启动耗时。

---

## 第23章：主从复制与读写分离：从单点到多副本
**定位**：构建 Redis 高可用的第一层基础。
**核心内容**：
- replicaof、全量同步、增量同步、复制积压缓冲区
- 主从延迟和读到旧数据
- 读写分离的业务适用场景
- 复制断链、重连和 backlog 配置
- min-replicas-to-write 数据安全保护
**实战目标**：搭建一主两从架构，压测读写分离效果，并模拟网络抖动观察复制延迟。

---

## 第24章：Sentinel 高可用：自动故障转移实战
**定位**：让 Redis 主从架构具备自动选主能力。
**核心内容**：
- Sentinel 监控、主观下线、客观下线
- Leader 选举与 failover 流程
- 客户端如何感知新主节点
- 脑裂风险和部署奇数节点原则
- Sentinel 与 Cluster 的关系
**实战目标**：搭建三节点 Sentinel，模拟主库宕机，验证自动切主、客户端重连和数据一致性风险。

---

## 第25章：Cluster 集群：分片、槽位与水平扩展
**定位**：掌握 Redis 横向扩容的核心模式。
**核心内容**：
- 16384 槽、CRC16、hash tag
- MOVED、ASK、重定向与客户端路由
- 集群创建、扩容、缩容、reshard
- 多 key 命令的槽位限制
- Cluster 故障转移和副本迁移
**实战目标**：搭建 3 主 3 从 Cluster，完成在线扩容和迁移，验证业务客户端在迁移期间的表现。

---

## 第26章：内存治理：淘汰策略、碎片与容量规划
**定位**：让 Redis 在有限内存中稳定运行。
**核心内容**：
- maxmemory 与 noeviction、allkeys-lru、volatile-ttl、lfu 等策略
- 过期删除、内存淘汰和 OOM 的关系
- 内存碎片、active defrag 与 jemalloc
- key/value 大小估算与容量规划
- 热点数据与冷数据分层
**实战目标**：设计 10 亿请求规模下的缓存容量模型，压测不同淘汰策略对命中率和延迟的影响。

---

## 第27章：性能调优：从慢命令到火焰图
**定位**：用数据定位 Redis 性能瓶颈。
**核心内容**：
- 命令复杂度与慢查询
- 网络 RTT、Pipeline、连接池参数
- big key、hot key、阻塞命令和 fork 抖动
- redis-benchmark、memtier_benchmark、perf、火焰图
- P50/P95/P99 延迟分析
**实战目标**：构造慢命令、大 key 和热点 key 场景，输出一份性能诊断报告和优化前后对比。

---

## 第28章：可观测性：Prometheus、Grafana 与告警体系
**定位**：从黑盒使用 Redis 到白盒运营 Redis。
**核心内容**：
- info 指标分类：server、clients、memory、stats、replication、cluster
- Redis Exporter 部署和指标映射
- Grafana 大盘：QPS、延迟、命中率、内存、连接数、复制延迟
- 告警规则：内存水位、慢查询、主从断链、集群槽异常
- 日志、指标和链路追踪的协作
**实战目标**：搭建 Redis + Exporter + Prometheus + Grafana 监控栈，配置 8 条生产告警规则。

---

## 第29章：安全加固：ACL、TLS 与多租户隔离
**定位**：把 Redis 从内网裸奔改造成可审计服务。
**核心内容**：
- requirepass 与 ACL 用户体系
- 命令权限、key 前缀权限和只读用户
- TLS 加密、证书配置和客户端连接
- 危险命令治理：flushall、config、keys、monitor
- 多业务共用 Redis 的隔离策略
**实战目标**：为开发、测试、运维配置不同 ACL 用户，并开启 TLS 验证客户端安全连接。

---

## 第30章：容器化与 Kubernetes 部署实践
**定位**：掌握 Redis 在云原生环境中的运行方式。
**核心内容**：
- Docker 镜像、配置挂载和数据卷
- StatefulSet、Headless Service、PVC
- 资源限制、探针、反亲和和滚动更新
- Redis Operator 与 Helm Chart
- 容器环境下的持久化和网络风险
**实战目标**：在 K8s 中部署 Redis 主从或 Sentinel 架构，验证故障恢复、持久化和监控接入。

---

## 第31章：【中级篇综合实战】构建生产级高并发缓存平台
**定位**：融会贯通中级篇的架构、稳定性和运维能力。
**核心内容**：
- 场景：为千万 DAU 电商平台设计 Redis 缓存与实时事件平台
- 功能需求：商品缓存、秒杀库存、订单事件流、排行榜、分布式锁、监控告警
- 架构设计：Cluster + Sentinel 思路对比、多级缓存、Stream、Prometheus、ACL
- 分步实现：容量规划、部署、压测、故障演练、告警验收
- 验收标准：缓存命中率 > 90%，P99 < 20ms，故障切换可观测，核心数据可恢复
**实战目标**：交付一套可运行的生产级 Redis 缓存平台方案和演练报告。

---

# 高级篇（第 32-40 章）

> **核心目标**：源码级理解 Redis 的执行模型、数据结构、持久化、复制、集群与模块扩展，掌握极端场景优化和 SRE 落地。
> **实战主线**：从 Redis 使用者进阶为能读源码、能定位内核问题、能设计平台化方案的 Redis 负责人。
> **源码关联**：src/server.c、src/ae.c、src/networking.c、src/db.c、src/object.c、src/t_*.c、src/rdb.c、src/aof.c、src/cluster.c、src/module.c。

---

## 第32章：Redis 对象系统与内部编码源码剖析
**定位**：理解外部数据结构背后的内部实现。
**核心内容**：
- redisObject：type、encoding、lru、refcount、ptr
- SDS、Listpack、Quicklist、Dict、Skiplist、Intset
- embstr/raw/int 等字符串编码
- 数据结构编码转换条件
- object encoding 和 memory usage 的验证方法
**实战目标**：编写脚本批量写入不同大小的数据，观察编码变化和内存占用，关联源码解释转换原因。

---

## 第33章：事件循环源码：ae.c 与网络请求处理链路
**定位**：从源码理解 Redis 为什么能高效处理请求。
**核心内容**：
- aeEventLoop、文件事件、时间事件
- acceptTcpHandler、readQueryFromClient、processCommand
- 命令表 redisCommand 与权限校验
- 单线程执行与多线程 IO 的边界
- 慢命令如何影响整个事件循环
**实战目标**：在源码关键路径增加日志，追踪一次 set/get 请求从网络读取到响应返回的完整链路。

---

## 第34章：过期删除与内存淘汰源码剖析
**定位**：理解 key 为什么“不是到点就立刻消失”。
**核心内容**：
- expire 字典与主字典的关系
- 惰性删除：访问时检查
- 定期删除：activeExpireCycle
- 淘汰入口与策略实现
- LRU/LFU 近似算法源码思路
**实战目标**：构造大量过期 key 和内存压力场景，用日志和指标观察删除、淘汰和延迟抖动。

---

## 第35章：RDB、AOF 与 Fork 源码链路
**定位**：深入理解持久化对性能和数据安全的影响。
**核心内容**：
- rdbSave、rdbLoad 与对象序列化
- AOF 追加、fsync、rewrite 和混合格式
- fork、copy-on-write 和内存峰值
- bio 后台线程与异步任务
- 崩溃恢复和数据一致性边界
**实战目标**：修改持久化相关日志，压测大写入期间 bgsave 和 aof rewrite 对延迟的影响。

---

## 第36章：复制、PSYNC 与 Sentinel 故障转移源码导读
**定位**：把高可用机制从配置层推进到实现层。
**核心内容**：
- replicationCron 与复制状态机
- PSYNC、run id、offset、backlog
- 全量复制与增量复制切换
- Sentinel 判断下线和执行 failover 的流程
- 复制一致性与丢数据窗口
**实战目标**：模拟主从断链和主库宕机，结合日志解释全量同步、增量同步和故障转移过程。

---

## 第37章：Cluster 源码：槽位、Gossip 与故障检测
**定位**：理解 Redis Cluster 如何维护分布式拓扑。
**核心内容**：
- clusterNode、clusterLink、clusterState
- 槽位映射与 key 路由
- Gossip 消息、PING/PONG/MEET/FAIL
- FAIL 判定、故障转移和投票
- 迁移过程中的 ASK/MOVED
**实战目标**：搭建源码调试版 Cluster，抓取节点消息并分析一次 reshard 和一次 failover。

---

## 第38章：Redis Module 开发：自定义命令与数据类型
**定位**：从 Redis 使用者进阶为扩展能力开发者。
**核心内容**：
- RedisModule_Init 与命令注册
- 模块 API：字符串、键空间、回复、内存、线程安全上下文
- 自定义数据类型、RDB 持久化和 AOF 重放
- 模块安全、版本兼容和部署方式
- Redis Stack 模块生态：JSON、Query、TimeSeries、Bloom、Vector
**实战目标**：开发一个简单限流模块或计数模块，支持自定义命令、持久化和基础压测。

---

## 第39章：Redis 8.x 新能力实战：JSON、Query、Vector 与语义缓存
**定位**：把 Redis 从缓存扩展到实时数据与 AI 应用底座。
**核心内容**：
- RedisJSON：对象存储与局部更新
- Redis Query Engine：索引、过滤、排序和聚合
- Vector Set 与向量检索基础
- 语义缓存、RAG 小管道和路由策略
- 模块能力与传统核心数据结构的协作
**实战目标**：构建一个迷你 RAG 语义缓存系统，支持文档写入、向量检索、缓存命中和结果回源。

---

## 第40章：【高级篇综合实战】从零设计企业级 Redis 平台
**定位**：融会贯通高级篇知识，产出可交付的 Redis 平台方案。
**核心内容**：
- 场景：为集团内部建设多租户 Redis 服务平台
- 架构设计：Cluster、Sentinel、代理层、监控、告警、备份、审计、容量管理
- 平台能力：实例申请、规格评估、自动扩缩容、慢查询治理、大 key 巡检、故障演练
- SRE 落地：SLO、错误预算、值班手册、应急预案和复盘机制
- 深度扩展：源码调试、模块开发、AI 语义缓存能力接入
**实战目标**：交付一份企业级 Redis 平台设计文档、演练脚本、监控大盘和上线检查清单。

---

# 附录与资源

## 附录 A：源码阅读路线图
1. 启动入口：src/server.c 的 main 函数。
2. 事件循环：src/ae.c、src/networking.c。
3. 命令执行：命令表、processCommand、call。
4. 数据结构：src/object.c、src/t_string.c、src/t_hash.c、src/t_list.c、src/t_set.c、src/t_zset.c。
5. 持久化：src/rdb.c、src/aof.c。
6. 高可用：src/replication.c、src/sentinel.c、src/cluster.c。
7. 扩展机制：src/module.c。

## 附录 B：推荐工具链
- 客户端：redis-cli、RedisInsight、RESP.app。
- 压测：redis-benchmark、memtier_benchmark、wrk、JMeter。
- 观测：Prometheus、Grafana、Redis Exporter、OpenTelemetry。
- 抓包与诊断：tcpdump、Wireshark、perf、bpftrace、火焰图。
- 部署：Docker、Docker Compose、Kubernetes、Helm。

## 附录 C：章节代码组织建议
- chapter-01-env：环境搭建与基础命令。
- chapter-03-string：验证码、计数器、开关。
- chapter-12-cache-aside：商品详情缓存。
- chapter-19-seckill：秒杀库存扣减。
- chapter-20-stream：订单事件流。
- chapter-31-cache-platform：中级篇综合项目。
- chapter-40-redis-platform：高级篇综合项目。

## 附录 D：推广计划与协作建议
- 开发团队：重点完成基础篇和中级篇实战，掌握数据结构选型、缓存一致性和高并发写入。
- 测试团队：重点关注故障注入、并发压测、数据一致性校验和回归脚本。
- 运维/SRE 团队：重点关注部署、容量规划、监控告警、备份恢复和故障演练。
- 架构团队：重点关注中级篇架构设计和高级篇源码、平台化、模块扩展。

---

> **版权声明**：本专栏基于 Redis 8.6.x 官方能力和开源生态编写，所有源码引用应遵循 Redis 对应版本许可证和相关模块许可证。
