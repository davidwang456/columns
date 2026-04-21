# 02-Bootstrap启动参数绑定

## 1. 项目背景
接入层网关上线初期，团队把全部关注点放在“先跑起来”，结果每次发版都在改启动代码：端口改一次、`SO_BACKLOG` 改一次、`CONNECT_TIMEOUT_MILLIS` 又改一次。最危险的是，参数散落在 `main` 方法里，线上问题一来没人说得清“当前实例到底用了哪套配置”。当你面对突发连接洪峰时，真正决定系统上限的往往不是业务逻辑，而是 `ServerBootstrap` 的参数绑定是否统一、可审计、可回滚。

Netty 在 `transport/src/main/java/io/netty/bootstrap/ServerBootstrap.java` 与 `AbstractBootstrap.java` 里，把参数分成三类：`option`（服务端 `ServerChannel`）、`childOption`（每个已接入连接）、`attr/childAttr`（附加上下文属性）。这套机制的价值不是“多几个 API”，而是把“连接接入策略”从业务代码中剥离。官方示例 `example/src/main/java/io/netty/example/echo/EchoServer.java` 就是最小可运行模板：先组装参数，再绑定端口。我们要做的，是把这套模板升级成可发布的工程实践。

## 2. 项目设计（剧本式交锋对话）
小胖：我理解 `ServerBootstrap` 就是“填表开机”，那为什么还分 `option` 和 `childOption`？我全放一个地方不行吗？  
小白：如果都混在一起，监听 socket 的参数和业务连接参数就会串味。比如 `SO_BACKLOG` 只对监听队列生效，给子连接设置根本没用。  
大师：对。你可以把它理解成“门口排队规则”和“进店后服务规则”。`option` 是门口，`childOption` 是柜台。技术映射：`ServerChannel` 负责 accept，`SocketChannel` 负责 read/write。

小胖：那我看到有人在 `childHandler` 里再去改 option，这样不是更灵活吗？  
小白：灵活是灵活，但会导致不同连接拿到不同参数版本，排查线上问题会非常痛苦。  
大师：参数绑定必须“一次定义，分层生效”。启动阶段确定全局接入策略，运行阶段只处理业务。技术映射：把“编排期”与“执行期”分离，避免配置漂移。

小胖：还有个问题，`ChannelOption.AUTO_READ` 要不要关？关了是不是更省资源？  
小白：关掉后要手动 `read()`，没配好会造成连接假死。  
大师：只有在你明确实现背压策略时才关。否则默认开启，先保证吞吐与正确性。技术映射：不要为了“看起来高级”提前优化。

## 3. 项目实战
### 3.1 环境准备
- JDK 17，Maven 3.9+
- 参考示例：`example/src/main/java/io/netty/example/echo`
- 关键源码：`bootstrap/ServerBootstrap.java`、`bootstrap/AbstractBootstrap.java`

### 3.2 分步实现
**步骤目标：建立“配置对象 -> Bootstrap 绑定 -> 启动日志”三段式启动链路。**

1. 新建启动配置类 `GatewayBootstrapConfig`，集中定义端口、`backlog`、`rcvBuf`、`sndBuf`、`connectTimeoutMillis`。  
2. 在 `ServerBootstrap` 中显式绑定：
   - `.option(ChannelOption.SO_BACKLOG, config.getBacklog())`
   - `.childOption(ChannelOption.SO_KEEPALIVE, true)`
   - `.childOption(ChannelOption.TCP_NODELAY, true)`
   - `.childOption(ChannelOption.CONNECT_TIMEOUT_MILLIS, config.getConnectTimeoutMillis())`
3. 使用 `.childAttr(AttributeKey.valueOf("client-type"), "gateway")` 给子连接打标，便于在 handler 中审计。
4. 在 `bind(port).sync()` 成功后打印完整生效参数，作为运行时基线。

命令示例：
```bash
mvn -pl example -DskipTests compile
mvn -pl example exec:java -Dexec.mainClass=io.netty.example.echo.EchoServer
```

另开终端压测连接与功能验证：

命令示例：
```bash
nc 127.0.0.1 8007
hello-netty
```

预期结果：服务端日志出现“bootstrap options loaded”，输入 `hello-netty` 回显成功，且日志中可看到连接属性 `client-type=gateway`。

#### 常见坑
- **把 `SO_BACKLOG` 配在 `childOption`**：无效且不报错，表现为高峰期 accept 堆积。  
- **在业务 handler 动态改 Option**：不同连接行为不一致，线上不可追踪。  
- **盲目关闭 `AUTO_READ`**：未手动触发 `read()` 时连接看似建立但无数据流转。  
- **忽略启动日志**：参数是否生效无法核验，排障只能“猜”。

### 3.3 完整代码清单
- `example/src/main/java/io/netty/example/echo/EchoServer.java`
- `transport/src/main/java/io/netty/bootstrap/ServerBootstrap.java`
- `transport/src/main/java/io/netty/bootstrap/AbstractBootstrap.java`

### 3.4 测试验证

请结合本章步骤执行功能、稳定性与可观测性验证。

#### 验证口径

- [ ] **功能**：核心用例可复现，关键输入输出与预期一致。
- [ ] **稳定性**：连续压测或重复执行无异常抖动、无明显长尾退化。
- [ ] **可观测性**：日志、指标与关键错误信号可定位并支持问题回溯。

## 4. 项目总结
Bootstrap 参数绑定的核心不是 API 记忆，而是“边界清晰”：监听层参数只影响接入，子连接参数只影响会话，属性用于跨 handler 上下文传递。把参数从业务代码中抽离后，你获得三件事：可复现、可比对、可回滚。对于网关、RPC、长连接系统，这比“写一个更花哨的 handler”更有价值。

落地时建议遵循四条：第一，配置中心化，禁止散点赋值；第二，启动期一次绑定，运行期只读；第三，日志打印生效参数快照；第四，任何参数调整都走压测回归。这样做会让团队在连接风暴到来时有明确操作手册，而不是临时拍脑袋。

### 4.5 思考题

1. 为什么 `SO_BACKLOG` 适合 `option` 而不是 `childOption`？如果配错，在线上会出现什么可观测现象？  
2. 你会如何设计“参数变更灰度发布”方案，既能验证效果又避免连接行为分裂？

答案见：[附录-思考题答案索引](附录-思考题答案索引.md)
