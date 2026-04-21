# 04-Channel生命周期

## 1. 项目背景
线上连接问题最难排查的一类，不是“连不上”，而是“偶发断、偶发卡、偶发不回包”。很多时候业务日志看起来正常，根因却藏在 `Channel` 生命周期事件处理不完整：`channelActive` 没初始化状态，`channelInactive` 没释放资源，`exceptionCaught` 没兜底关闭。Netty 的连接模型本质是事件驱动状态机，从注册、激活、读写到关闭，每个阶段都有明确回调。如果你忽略其中一个钩子，就可能把故障埋成“定时炸弹”。

源码里 `transport/src/main/java/io/netty/channel/AbstractChannel.java`、`DefaultChannelPipeline.java` 清楚定义了生命周期传播顺序。官方示例 `example/src/main/java/io/netty/example/discard/DiscardServerHandler.java` 和 `echo/EchoServerHandler.java` 体现了最常见的处理点。把生命周期写完整，不是为了“优雅”，而是为了把连接行为从“黑箱”变“可控”。

## 2. 项目设计（剧本式交锋对话）
小胖：我只关心收到消息，`channelActive` 这种回调可以不写吧？  
小白：不写就没法做连接级初始化，比如认证上下文、限流桶、心跳状态。  
大师：对。生命周期不是装饰，它是连接治理的主线。技术映射：用户进店、下单、离店，每一步都要有动作。

小胖：那 `channelInactive` 里到底该做什么？我怕写多了出错。  
小白：至少要做资源清理和指标上报，不然连接断了但状态还在，内存会慢慢涨。  
大师：把它当“销户流程”：释放引用、取消定时任务、记录断开原因。技术映射：离店后要撤销座位占用。

小胖：异常时是直接 `close()` 还是继续读？  
小白：看异常类型。协议错误一般直接断，短暂 IO 抖动可重试。  
大师：先定义异常分级，再决定关闭策略。技术映射：轻伤处理、重伤送医，不要一刀切。

## 3. 项目实战
### 3.1 环境准备
- JDK 17，Maven 3.9+
- 关键源码：`channel/AbstractChannel.java`、`channel/DefaultChannelPipeline.java`
- 示例路径：`example/src/main/java/io/netty/example/echo`

### 3.2 分步实现
**步骤目标：构建“生命周期可观测”的连接处理模板。**

1. 在自定义 `ChannelInboundHandlerAdapter` 中实现 `channelRegistered`、`channelActive`、`channelRead`、`channelInactive`、`exceptionCaught`。  
2. `channelActive` 中记录连接开始时间并写入 `Channel.attr()`。  
3. `channelInactive` 中读取开始时间计算连接时长，输出日志并清理附件对象。  
4. `exceptionCaught` 按异常分类处理：协议异常直接关闭，超时异常打点后关闭，其他异常告警并关闭。

命令示例：
```bash
mvn -pl example -DskipTests compile
mvn -pl example exec:java -Dexec.mainClass=io.netty.example.echo.EchoServer
nc 127.0.0.1 8007
```

手动触发断开：客户端输入后直接 `Ctrl+C`，服务端应按顺序打印 inactive 清理日志。

#### 项目实战补强：可执行检查与验收
建议把生命周期治理做成“可回归”的小流程，而不是靠人肉看日志：

1. **连接建立验收**：`channelActive` 必须打出连接 ID、远端地址、协议版本。  
2. **连接关闭验收**：`channelInactive` 必须打出关闭原因、连接时长、未完成请求数。  
3. **异常收口验收**：`exceptionCaught` 必须区分协议异常、超时异常、未知异常。  
4. **资源回收验收**：断开后 1 分钟内连接级缓存对象数量应回落到基线。

建议加入简单故障注入命令（示例）：

命令示例：
```bash
for /l %i in (1,1,50) do start cmd /c "echo ping-%i|nc 127.0.0.1 8007"
```

在压测同时主动断开部分客户端，验证 inactive 清理是否稳定执行。

#### 常见坑
- **只写 `channelRead` 不写 `channelInactive`**：连接级对象长期残留。  
- **异常后不关闭连接**：半坏连接占用 FD 和内存。  
- **清理顺序错误**：先清理再上报导致日志缺关键字段。  
- **忽略 `handlerRemoved` 场景**：动态移除 handler 后状态不一致。

#### 故障案例（线上高频）
- **案例 A：连接断开后内存持续上涨**  
  根因：`channelInactive` 没有取消定时任务，连接虽然断开但任务仍持有上下文。  
  修复：在 inactive 和 handlerRemoved 两处双保险取消任务，并置空引用。  

- **案例 B：偶发不回包**  
  根因：异常分支记录日志但未关闭连接，导致半失效连接继续占资源。  
  修复：异常分级后明确“哪些异常必须关闭”，并加监控告警。

### 3.3 完整代码清单
- `example/src/main/java/io/netty/example/echo/EchoServerHandler.java`
- `transport/src/main/java/io/netty/channel/AbstractChannel.java`
- `transport/src/main/java/io/netty/channel/DefaultChannelPipeline.java`

### 3.4 测试验证

请结合本章步骤执行功能、稳定性与可观测性验证。

#### 验证口径

- [ ] **功能**：核心用例可复现，关键输入输出与预期一致。
- [ ] **稳定性**：连续压测或重复执行无异常抖动、无明显长尾退化。
- [ ] **可观测性**：日志、指标与关键错误信号可定位并支持问题回溯。

## 4. 项目总结
Channel 生命周期是 Netty 稳定性的底座。连接不是“创建即用、断开即忘”的线性对象，而是带状态迁移的事件实体。你把生命周期钩子补全后，收益很直接：问题可复现、资源可回收、异常可分级。长期看，这会显著降低“偶发故障”的占比。

建议团队把生命周期回调沉淀成统一模板，新增协议接入必须复用：激活初始化、读写处理、异常分级、断开清理四项缺一不可。这样无论业务再怎么变化，连接治理都能维持同一质量基线。

进一步建议把“生命周期完整性”加入 CR 检查项：是否实现并测试了 active/inactive/exception 三个关键路径，是否有断连场景验证脚本，是否定义了可观测字段。只要这三点稳定执行，连接类故障会明显减少。

### 4.5 思考题

1. 如果 `channelInactive` 未执行就进程崩溃，哪些资源可能泄漏？你如何补偿清理？  
2. 你会如何设计“异常分级表”，让不同协议都能复用同一关闭策略？

答案见：[附录-思考题答案索引](附录-思考题答案索引.md)
