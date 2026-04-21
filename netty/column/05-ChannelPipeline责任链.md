# 05-ChannelPipeline责任链

## 1. 项目背景
当系统接入链路从一个协议变成多协议，从一个业务处理器变成十几个处理中间件时，最容易失控的不是性能，而是行为一致性：某些请求能过鉴权但不会打审计日志；某些异常被吞掉导致客户端一直等；某些回包绕过编码器直接发原始对象。根因通常是 ChannelPipeline 责任链设计不清：入站和出站方向没分清、传播 API 用错、handler 职责混杂。

Netty 在 `transport/src/main/java/io/netty/channel/DefaultChannelPipeline.java` 中用双向链表实现了 pipeline，每个 `ChannelHandlerContext` 都是节点。入站事件从 `head` 往后走，出站事件从当前节点向前走。这个机制让我们能“插件化”叠加日志、鉴权、限流、协议编解码和业务逻辑。官方 `example/src/main/java/io/netty/example/telnet/TelnetServerInitializer.java` 就是典型样板。真正的工程价值在于：通过明确责任链分层，实现可演进而非一次性代码。

## 2. 项目设计（剧本式交锋对话）
小胖：我把所有逻辑塞一个 handler 了，省得来回跳转，看起来更快。  
小白：短期确实快，但加需求会指数级变复杂，排障也无法定位。  
大师：pipeline 的意义就是把“变化点”拆开。技术映射：同一条生产线，但每个工位只做一件事。

小胖：我看有时 `fireChannelRead`，有时 `writeAndFlush`，到底谁接着谁？  
小白：入站靠 `fire*` 往后传播，出站靠 `write*` 往前传播，方向不同。  
大师：所以必须先画事件流。技术映射：送货和退货是两条车道，不能混用导航。

小胖：要是中间一个 handler 忘记传播会怎样？  
小白：后续节点收不到事件，表现就是“请求到达但无响应”。  
大师：这类故障最难发现，必须用日志 handler 和链路测试守住。技术映射：传送带上有人把箱子抱走了，后面自然啥也收不到。

## 3. 项目实战
### 3.1 环境准备
- JDK 17，Maven 3.9+
- 示例路径：`example/src/main/java/io/netty/example/telnet`
- 关键源码：`channel/ChannelPipeline.java`、`channel/DefaultChannelPipeline.java`

### 3.2 分步实现
**步骤目标：实现“解码 -> 鉴权 -> 业务 -> 编码 -> 异常收口”的最小责任链。**

1. 在 `ChannelInitializer` 中按顺序添加：`LoggingHandler`、`LineBasedFrameDecoder`、`StringDecoder`、`AuthHandler`、`BizHandler`、`StringEncoder`、`ExceptionHandler`。  
2. 在 `AuthHandler` 验证通过后调用 `ctx.fireChannelRead(msg)`，失败则回写错误并关闭连接。  
3. 在 `BizHandler` 中使用 `ctx.writeAndFlush("ok\n")`，避免绕过当前节点之前的出站处理。  
4. 在 `ExceptionHandler` 统一记录异常、连接 ID 和最近命令，最后 `ctx.close()`。

命令示例：
```bash
mvn -pl example -DskipTests compile
mvn -pl example exec:java -Dexec.mainClass=io.netty.example.telnet.TelnetServer
nc 127.0.0.1 8023
```

输入未鉴权命令预期返回 `auth failed`；输入合法命令预期返回 `ok` 且日志包含完整入站/出站链路。

#### 项目实战加固：发布前检查清单
责任链问题往往在“功能都能跑”时被忽视，建议上线前执行以下检查：

1. **顺序检查**：鉴权类 handler 必须位于业务 handler 之前，异常处理位于链路末端。  
2. **传播检查**：每个入站 handler 都要明确“继续传播/截断传播”的条件。  
3. **回写检查**：业务回写统一使用 `ctx.writeAndFlush`，并验证是否命中预期编码器。  
4. **可观测检查**：为关键节点增加命中计数与异常标签，便于快速定位“卡在哪一环”。

建议补充一条自动化验证命令：

命令示例：
```bash
mvn -pl example -Dtest=PipelineFlowTest test
```

测试最少覆盖三条路径：鉴权失败、业务成功、异常抛出，避免仅验证 happy path。

#### 常见坑
- **顺序颠倒**：先业务后鉴权，导致未授权请求被执行。  
- **遗漏传播**：handler 吞事件，后续节点失效。  
- **`channel.write` 滥用**：从尾节点起跳，绕过局部出站逻辑。  
- **异常分散处理**：每个 handler 各自 try/catch，最终没人真正兜底。

#### 故障案例（责任链常见回归）
- **案例 A：请求能进来但不回包**  
  根因：中间 handler 忘记 `fireChannelRead`，后续业务节点未触发。  
  定位：LoggingHandler 显示入站仅到达前两层。  
  修复：补齐传播并增加单测断言“下游 handler 必须命中”。  

- **案例 B：审计日志偶发缺失**  
  根因：业务改为 `channel.writeAndFlush`，绕过了局部出站审计节点。  
  修复：统一改回 `ctx.writeAndFlush`，并在 CR 规则中禁止无理由使用 `channel.write*`。

### 3.3 完整代码清单
- `example/src/main/java/io/netty/example/telnet/TelnetServerInitializer.java`
- `transport/src/main/java/io/netty/channel/DefaultChannelPipeline.java`
- `transport/src/main/java/io/netty/channel/AbstractChannelHandlerContext.java`

### 3.4 测试验证

请结合本章步骤执行功能、稳定性与可观测性验证。

#### 验证口径

- [ ] **功能**：核心用例可复现，关键输入输出与预期一致。
- [ ] **稳定性**：连续压测或重复执行无异常抖动、无明显长尾退化。
- [ ] **可观测性**：日志、指标与关键错误信号可定位并支持问题回溯。

## 4. 项目总结
ChannelPipeline 不是“写着玩”的模式，而是 Netty 架构可维护性的核心接口。责任链拆得清，系统就能在不改核心逻辑的前提下快速插拔能力；责任链拆不清，任何小改动都可能引发连锁故障。实践上，建议固定四层：连接治理层、协议层、安全层、业务层，异常统一收口。

同时要把可观测性前置：链路日志、handler 命中计数、传播断点告警都应成为发布门槛。这样你就能把“偶发没响应”这类疑难杂症，从不可解释变成可定位、可修复。

如果团队要支持运行时热插拔 handler，务必建立灰度策略：先在低流量连接组启用，再观察命中率、异常率和延迟长尾，确认稳定后全量推广。责任链不是不能变，而是每次变更都必须可验证、可回滚。

### 4.5 思考题

1. 在同一 pipeline 中，为什么说 `ctx.write` 比 `channel.write` 更适合业务 handler 内部回写？  
2. 如果要运行时热插拔风控 handler，如何确保不会破坏当前 in-flight 请求的事件顺序？

答案见：[附录-思考题答案索引](附录-思考题答案索引.md)
