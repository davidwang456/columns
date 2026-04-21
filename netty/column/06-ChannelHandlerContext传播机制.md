# 06-ChannelHandlerContext传播机制

## 1. 项目背景
在 Netty 项目里，很多“诡异行为”都和 `ChannelHandlerContext` 传播入口有关：同样是发消息，有人用 `ctx.writeAndFlush`，有人用 `channel.writeAndFlush`；同样是读事件，有人 `fireChannelRead`，有人直接 return。结果是某些编码器不执行、某些统计节点被跳过、某些异常路径绕开兜底。团队往往以为是业务 bug，实际是事件传播路径错了。

`ChannelHandlerContext` 是 pipeline 节点上下文，定义在 `transport/src/main/java/io/netty/channel/AbstractChannelHandlerContext.java`。它不仅是“拿 channel 的入口”，更是“决定传播起点”的关键对象。理解 ctx 的传播语义，才能真正写出可预测链路：入站从当前节点往后，出站从当前节点往前；`channel` 级调用则从 pipeline 头尾重新开始。

## 2. 项目设计（剧本式交锋对话）
小胖：我图省事，业务里统一 `ctx.channel().writeAndFlush()`，应该最稳吧？  
小白：稳不稳要看你想让哪些出站 handler 生效。`channel.write` 可能多走也可能绕过局部语义。  
大师：先定义“从哪里开始传播”再选 API。技术映射：你把包裹交给总仓还是交给当前工位，路线不同。

小胖：那 `fireChannelRead` 和直接调用下个 handler 方法有啥区别？  
小白：直接调是强耦合，破坏 pipeline 封装；`fire*` 才是官方事件传播机制。  
大师：对，永远让框架调度链路，不要手搓跳转。技术映射：不要跨工位私下递件，要走传送带。

小胖：我能不能在一个 handler 里既处理业务又决定跳过后面节点？  
小白：可以，但必须明确条件，否则维护者看不懂“为什么某些消息没走完链路”。  
大师：任何“截断传播”都要日志与注释，且必须有测试覆盖。技术映射：临时封路要立警示牌。

## 3. 项目实战
### 3.1 环境准备
- JDK 17，Maven 3.9+
- 示例路径：`example/src/main/java/io/netty/example/echo`
- 关键源码：`channel/AbstractChannelHandlerContext.java`

### 3.2 分步实现
**步骤目标：对比不同传播入口对链路执行顺序的影响。**

1. 构造 pipeline：`A(入站日志)` -> `B(业务)` -> `C(出站日志)`。  
2. 在 B 中分别实现两条路径：
   - 路径一：`ctx.writeAndFlush(msg)`
   - 路径二：`ctx.channel().writeAndFlush(msg)`
3. 发送同一请求，观察 C 是否都命中，以及命中顺序差异。  
4. 在 A 中实验“拦截消息不传播”和“继续 fire”的差异，验证后续 handler 行为。

命令示例：
```bash
mvn -pl example -DskipTests compile
mvn -pl example exec:java -Dexec.mainClass=io.netty.example.echo.EchoServer
nc 127.0.0.1 8007
```

输入 `path-ctx` 与 `path-channel` 两种指令，日志应显示不同传播轨迹。

#### 项目实战补充：传播一致性验证
建议把“传播入口选择”从编码习惯升级为团队规范，并配套验证脚本：

1. **规范一**：业务 handler 内默认 `ctx.write*`，仅在明确需要全链路出站时使用 `channel.write*`。  
2. **规范二**：所有入站 handler 必须显式写出 `fireChannelRead` 或“终止传播”理由。  
3. **规范三**：终止传播时必须处理消息生命周期（释放或转移所有权）。  
4. **规范四**：关键链路必须打印 traceId、handlerName、传播入口，便于回放路径。

可执行验证建议：

命令示例：
```bash
mvn -pl example -Dtest=ContextPropagationTest test
```

测试至少断言三件事：出站节点命中顺序、截断分支释放行为、异常分支是否进入统一兜底。

#### 常见坑
- **混用传播 API 不设规范**：同类逻辑表现不一致。  
- **handler 内直接调用下游对象**：破坏解耦，改顺序就崩。  
- **条件分支截断无日志**：线上看起来像“随机丢消息”。  
- **忽略 `ReferenceCounted` 生命周期**：截断后忘记释放导致泄漏。

#### 故障案例（最容易踩的两个）
- **案例 A：编码器偶发不生效**  
  根因：某业务节点从 `channel.writeAndFlush` 改成了不一致调用，导致部分路径绕过预期上下文。  
  处置：统一传播入口，补日志字段 `route=ctx|channel`，并加回归测试。  

- **案例 B：消息“无声丢失”**  
  根因：入站拦截分支未 `fireChannelRead` 且未记录日志，线上只能看到客户端超时。  
  处置：所有截断分支增加结构化日志和计数器，超过阈值立即告警。

### 3.3 完整代码清单
- `transport/src/main/java/io/netty/channel/AbstractChannelHandlerContext.java`
- `transport/src/main/java/io/netty/channel/DefaultChannelPipeline.java`
- `example/src/main/java/io/netty/example/echo/EchoServerHandler.java`

### 3.4 测试验证

请结合本章步骤执行功能、稳定性与可观测性验证。

#### 验证口径

- [ ] **功能**：核心用例可复现，关键输入输出与预期一致。
- [ ] **稳定性**：连续压测或重复执行无异常抖动、无明显长尾退化。
- [ ] **可观测性**：日志、指标与关键错误信号可定位并支持问题回溯。

## 4. 项目总结
`ChannelHandlerContext` 的本质是“事件路由器”，不是“获取 Channel 的快捷键”。你的代码一旦选错入口，系统行为就会悄悄偏离预期。工程上要建立统一约定：业务 handler 内优先 `ctx.write*`，跨边界统一 `channel.write*`；所有截断行为必须记录理由并附测试。

做好这件事，能显著降低链路回归风险，尤其在多人协作下。因为团队成员不再靠“默契”理解传播路径，而是靠清晰规则和可观测日志来验证。

落地建议是把传播规范写进项目模板：新 handler 默认代码包含传播注释、异常处理和资源释放占位；PR 模板强制勾选“是否改变传播起点”。这样可以在编码阶段拦住大部分传播类故障。

### 4.5 思考题

1. 在什么场景下必须使用 `channel.writeAndFlush` 而不是 `ctx.writeAndFlush`？  
2. 如果一个入站 handler 选择不调用 `fireChannelRead`，应当如何处理消息对象生命周期以避免泄漏？

答案见：[附录-思考题答案索引](附录-思考题答案索引.md)
