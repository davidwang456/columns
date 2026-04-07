本故事纯属虚构，如有雷同，纯属巧合。

> 人是活的，剑法是死的，活人不可给死剑法所拘！
> 
> --风清扬

**故事背景**

要想领悟“独孤九剑”内功要领，就要把握好独孤剑学的精气真髓，而这个精髓其实就是九剑第一式——“总诀式”，360 种变化，用以体会总诀，须得朝夕念诵，方可烂熟于胸，而后可融会贯通。

![](http://p6.toutiaoimg.com/large/pgc-image/27d0dda85873492888dee422ea5b18ea)

**Redis 总诀式**

**大师道**：今天是实战课程<独孤九剑学 Redis>的第一节课<总诀式>，

它的口诀是：

> Redis is an open source (BSD licensed), in-memory data structure store, used as a database, cache and message broker. It supports data structures such as strings, hashes, lists, sets, sorted sets with range queries, bitmaps, hyperloglogs, geospatial indexes with radius queries and streams. Redis has built-in replication, Lua scripting, LRU eviction, transactions and different levels of on-disk persistence, and provides high availability via Redis Sentinel and automatic partitioning with Redis Cluster.

它的要义是：

1. Redis 存储于内存中，使用数据结构存储;
2. Redis 用作数据库，缓存和消息代理；
3. Redis 支持多种数据结构如支持范围查询的字符串，散列表，列表，集合，有序集合，支持散射和流查询的位图，hyperloglog，地理位置索引；
4. Redis 具有内置的复制、Lua 脚本、LRU 清除、事务和不同级别的磁盘持久性，并通过 Redis Sentinel 和带有自动分区的 Redis 集群提供高可用性。

**大师又道**：这总诀是独孤九剑的根本关键，你此刻虽记住了，只是为求速成，全凭硬记，不明其中道理，日后甚易忘记。从今天起，须得朝夕念诵。”

**小白应道**：“是！”

---

**番外：总诀背后，源码里藏了什么？**

**大师**：你背熟了总诀，不妨再记一句“行功路线”：Redis 服务端是**单线程执行命令**（网络 I/O 可配置多线程，但命令仍在一处串行处理），请求从连接读入、解析、执行到写回，都在事件循环里轮转。你本地这份仓库里，`ae.c` / `ae_epoll.c`（或对应平台的 `ae_*`）实现事件循环，`networking.c` 负责客户端读写缓冲与命令分发——总诀里说的复制、持久化、集群，都是在这条主循环上挂出来的子系统。

**小白**：也就是说，总诀里的“快”，不仅是内存，还有**没有锁竞争下的纯内存数据结构操作**？

**大师**：正是。日后你读 `server.c` 里主循环、再看各 `t_*.c`（如 `t_string.c`、`t_hash.c`）里的命令实现，就会把今天背的总诀一句句对上号。总诀是纲，源码是目；纲举则目张。
