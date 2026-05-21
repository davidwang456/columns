# 第33章：事件循环源码ae与网络请求处理链路

## 1. 项目背景

线上压测时，很多团队都会遇到同一个疑问：Redis 明明是单线程执行命令，为什么普通 `GET`、`SET` 可以做到很低延迟？反过来，一个慢 Lua、大 key 删除或大范围查询，为什么又会把其他请求一起拖慢？要回答这些问题，必须从事件循环和网络请求处理链路看起。

业务背景是一个商品详情服务。高峰期每秒有数万次读取，应用通过连接池访问 Redis。某次发布后，P99 延迟从 8ms 飙到 80ms，但机器 CPU 不高，网络也没丢包。排查发现，有一个后台任务在高峰期执行大批量 `HGETALL` 和复杂 Lua，阻塞了 Redis 主执行路径。开发最初以为“连接多就能并行”，测试以为“单个命令快就没事”，运维只看到慢查询列表，却没有把慢命令和事件循环排队关联起来。

本章要做的源码实战，是追踪一次 `SET`、`GET` 请求从客户端连接、网络读取、命令解析、命令执行到响应写回的完整路径。我们会观察 `ae.c` 的事件循环，`networking.c` 的连接读写，`server.c` 的命令处理入口，以及命令表 `redisCommand` 如何找到具体实现。

学完本章，你应该能说清楚三件事：Redis 事件循环在等什么；一次请求进入后经过哪些函数；为什么慢命令会影响同实例上的其他客户端。

## 2. 项目设计

小胖先开场：“单线程还高并发，听起来像一个人同时服务一百桌火锅。我不信，除非他会分身。”

小白说：“准确地说，Redis 不是一个线程同时执行多个命令，而是用事件循环快速处理就绪的连接事件。高并发更多来自非阻塞 IO、内存操作快、命令执行短。”

大师画了一个循环：“事件循环像餐厅叫号屏。谁的连接可读，就把请求读进来；谁有响应可写，就把结果写回去；时间事件到了，就执行定时任务。Redis 主线程在循环里不断取事件、处理事件。只要每个事件处理得足够快，整体吞吐就很高。”

技术映射：事件循环核心在 `src/ae.c`，Linux 下通常使用 `ae_epoll.c`，抽象为 `aeApiPoll` 等接口。

小胖追问：“那客户端连上来以后，Redis 怎么知道要执行 `GET product:1`？”

大师回答：“连接建立时，Redis 给这个 socket 注册读事件。客户端发来数据后，读事件触发，进入 `readQueryFromClient`，把数据读到查询缓冲区。接着解析 RESP 协议，形成参数数组，再进入 `processCommand`，查命令表，做权限、参数、状态校验，最后调用对应命令函数。”

小白补充：“响应不是直接随便写。Redis 会把回复放到 client 的输出缓冲区，再在可写事件中发回。如果客户端读得慢，输出缓冲区也可能成为风险。”

技术映射：网络链路重点看 `acceptTcpHandler`、`acceptCommonHandler`、`readQueryFromClient`、`processInputBuffer`、`processCommand`、`addReply`、`sendReplyToClient`。

小胖又问：“Redis 6 以后不是有 IO 多线程吗？那慢命令是不是就没影响了？”

大师摇头：“IO 多线程主要帮忙读写网络数据，命令执行的核心路径仍然要保持一致性和简单性。慢命令卡住的是执行阶段，不是单纯网络读写。所以大 key、慢脚本、阻塞式扫描仍然会影响事件循环。”

小白点头：“也就是说，多线程 IO 不是业务并行执行许可证。复杂命令还是要拆分、异步化或换模型。”

技术映射：理解边界很重要，网络 IO 辅助线程不能消除命令本身的时间复杂度。

小胖最后说：“那我们怎么证明这条链路？总不能只靠读源码吧。”

大师回答：“高级篇的源码学习一定要可验证。我们在关键函数加日志，重新编译 Redis，执行一次 `SET` 和 `GET`，看日志顺序；再执行一个慢 Lua，观察其他请求延迟变化。源码观察、日志证据、命令验证三者闭环。”

## 3. 项目实战

### 3.1 源码文件与观察点

重点文件：

- `src/ae.c`：事件循环创建、事件注册、事件分发。
- `src/ae_epoll.c`：Linux epoll 后端实现。
- `src/server.c`：服务启动、时间事件、命令执行入口。
- `src/networking.c`：客户端连接、读取查询缓冲区、写响应。
- `src/connection.c`：连接抽象。
- `src/commands.c` 与命令声明文件：命令表和命令元数据。
- `src/t_string.c`：`GET`、`SET` 等命令实现。

观察点：socket 读事件在哪里注册；客户端数据如何进入 query buffer；RESP 如何被解析成 argv；命令权限与参数在哪里校验；响应如何进入输出缓冲区；事件循环每轮是否会被慢命令拖住。

### 3.2 编译与启动

在 Linux、WSL2 或容器中准备源码：

```bash
git clone https://github.com/redis/redis.git
cd redis
make -j4
src/redis-server --port 6379 --loglevel notice
```

如果直接使用官方镜像，也可以先完成命令验证；源码加日志需要本地编译。

### 3.3 增加链路日志

为了不改变行为，只加轻量日志。示例观察点如下，实际行号以本地版本为准。

在 `networking.c` 的 `acceptCommonHandler` 附近打印连接建立：

```c
serverLog(LL_NOTICE, "[trace] accept client fd=%d", connGetFD(conn));
```

在 `readQueryFromClient` 读到数据后打印：

```c
serverLog(LL_NOTICE, "[trace] read query from client id=%llu", (unsigned long long)c->id);
```

在 `server.c` 的 `processCommand` 开始处打印命令名：

```c
serverLog(LL_NOTICE, "[trace] process command=%s client=%llu", c->argv[0]->ptr, (unsigned long long)c->id);
```

在回复写回路径 `sendReplyToClient` 附近打印：

```c
serverLog(LL_NOTICE, "[trace] send reply client=%llu", (unsigned long long)c->id);
```

重新编译并启动：

```bash
make -j4
src/redis-server --port 6379 --loglevel notice
```

### 3.4 执行请求并观察日志

另开终端：

```bash
src/redis-cli -p 6379 SET product:1 redis
src/redis-cli -p 6379 GET product:1
```

预期日志顺序大致为：accept client、read query、process command、send reply。连接复用时，不一定每次命令都有 accept，因为客户端连接可能已经建立。

再观察客户端信息：

```bash
src/redis-cli CLIENT LIST
src/redis-cli INFO clients
src/redis-cli INFO commandstats
```

`CLIENT LIST` 可以看到客户端地址、缓冲区、执行状态；`commandstats` 可以看到命令调用次数和耗时统计。

### 3.5 验证慢命令影响事件循环

准备一个慢 Lua。学习环境可以使用循环制造阻塞，不要在生产执行：

```bash
redis-cli EVAL "local x=0 for i=1,100000000 do x=x+i end return x" 0
```

在另一个终端同时执行：

```bash
redis-cli --latency -h 127.0.0.1 -p 6379
redis-cli PING
```

预期现象：Lua 执行期间，其他请求延迟升高，甚至等待脚本完成后才返回。随后查看：

```bash
redis-cli SLOWLOG GET 5
redis-cli LATENCY LATEST
```

这能证明慢命令阻塞的是主执行链路。即使网络层能接收连接，命令执行阶段仍然排队。

### 3.6 常见坑

第一，日志不要加在高频路径后长期运行。源码追踪只用于实验环境，生产应依赖慢日志、延迟监控、采样和 eBPF 等方式。

第二，连接池过大不等于吞吐无限增加。过多客户端会增加调度和缓冲区压力，且命令执行仍受 Redis 主线程约束。

第三，`MONITOR` 很适合学习链路，但生产环境使用会带来额外压力，应谨慎。

第四，慢查询阈值只统计命令执行时间，不包含网络排队和客户端等待全链路，所以还要结合应用端延迟和 `LATENCY` 事件。

## 4. 项目总结

本章从源码和命令两条线观察了 Redis 请求链路。事件循环负责分发连接读写和时间事件，网络层把字节流解析成命令，命令执行层完成校验和调用，回复再经输出缓冲区写回客户端。

Redis 快的原因不是“单线程魔法”，而是非阻塞 IO、紧凑内存结构、短命令路径和避免锁竞争共同作用。它的风险也来自同一个设计：一旦某个命令执行太久，就会拖慢同事件循环上的其他请求。

生产建议：禁止高峰期执行大范围阻塞命令；Lua 必须有复杂度评审和超时预案；客户端设置合理超时和连接池大小；慢日志、延迟事件、命令统计和应用端 P99 要一起看；对极热或极慢任务进行拆分、异步化或隔离实例。

思考题：如果一个实例上同时承载商品读缓存和后台批处理任务，为什么容易互相影响？Redis IO 多线程能解决哪些问题，又不能解决哪些问题？
