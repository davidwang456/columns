# 03-EventLoopGroup线程模型

## 1. 项目背景
很多团队第一次上 Netty 时，会把线程池当成“越多越好”的旋钮：Boss 线程开 8 个，Worker 线程开 64 个，业务线程再开 200 个。结果不是吞吐飙升，而是上下文切换暴涨、延迟毛刺明显、GC 更频繁。根因在于：Netty 的 `EventLoopGroup` 不是普通任务线程池，它遵循“一个 Channel 绑定一个 EventLoop，且串行执行”的并发模型。你如果不理解这个约束，就会在错误的位置加线程，最后把可预测性打碎。

源码层面，`transport/src/main/java/io/netty/channel/MultithreadEventLoopGroup.java` 和 `SingleThreadEventLoop.java` 已经把策略写死：每个 `EventLoop` 维护自己的任务队列和定时任务队列，IO 事件与普通任务在同一线程有序执行。官方示例 `example/src/main/java/io/netty/example/echo/EchoServer.java` 默认 `NioEventLoopGroup()`，就是为了让新人先踩在“正确心智模型”上。理解线程模型后，你才知道什么时候该把耗时业务迁移到自定义执行器，而不是在 IO 线程里硬扛。

## 2. 项目设计（剧本式交锋对话）
小胖：线程多不就更快吗？我准备把 worker 开到 CPU 核数的四倍。  
小白：可 Netty 里一个连接的事件是串行的，盲目加线程可能只会增加调度开销。  
大师：没错。EventLoop 模型要的是“局部无锁 + 事件有序”，不是“全局并行”。技术映射：一个柜台服务一条队伍，队伍内部不能并发插队。

小胖：那 Boss 和 Worker 应该怎么配？  
小白：Boss 主要 accept，通常 1~2 个够了；Worker 处理读写，接近 CPU 核心数更稳。  
大师：先按经验值起步，再用压测数据调优。不要反过来。技术映射：门口保安不需要比柜台客服还多。

小胖：业务里有慢 SQL，放 handler 里直接查行不行？  
小白：这会阻塞 EventLoop，导致同一线程上的其他连接抖动。  
大师：正确做法是把阻塞任务投递到独立线程池，再通过 `ctx.executor().execute` 或监听器回到 IO 线程回写。技术映射：重活外包，收尾回到原工位。

## 3. 项目实战
### 3.1 环境准备
- JDK 17，Maven 3.9+
- 关键源码：`channel/SingleThreadEventLoop.java`、`channel/nio/NioEventLoop.java`
- 示例路径：`example/src/main/java/io/netty/example/echo`

### 3.2 分步实现
**步骤目标：验证“同一连接单线程串行”与“阻塞任务隔离”的效果差异。**

1. 在 `EchoServerHandler` 中打印 `Thread.currentThread().getName()`，连续发送多条消息，观察同一连接始终落在同一 worker 线程。  
2. 人为加入 `Thread.sleep(200)` 模拟阻塞，使用两个客户端并发发送，观察延迟放大。  
3. 新建 `DefaultEventExecutorGroup`（例如 16 线程），把阻塞逻辑迁移到该执行器，IO 线程仅负责解码和回写。
4. 对比迁移前后 P95 延迟与吞吐。

命令示例：
```bash
mvn -pl example -DskipTests compile
mvn -pl example exec:java -Dexec.mainClass=io.netty.example.echo.EchoServer
```

并发压测示例：

命令示例：
```bash
for /l %i in (1,1,2) do start cmd /c "echo ping-%i|nc 127.0.0.1 8007"
```

预期现象：迁移前偶发“排队”明显；迁移后 IO 线程不再被长任务拖慢，回显更稳定。

#### 项目化验证口径（建议直接落地到 README）
为了避免“这次压测看起来快、下次又变慢”的偶然性，建议固定一套可重复口径：

1. **固定并发模型**：连接数 200、每连接每秒 20 条消息、压测 5 分钟，前 1 分钟预热不计入结果。  
2. **固定观察窗口**：每 10 秒采样一次 `pendingTasks`、P95、P99、CPU sys/user。  
3. **固定回归阈值**：P99 不得高于基线 20%，`pendingTasks` 峰值不得持续 30 秒以上。  
4. **固定结论模板**：记录线程参数、机器规格、JDK 参数、压测脚本版本，避免“同名压测不同条件”。

推荐增加一条脚本化检查命令（Windows）：

```powershell
1..5 | ForEach-Object { Write-Host \"round=$_\"; Measure-Command { python .\bench\echo_bench.py --conn 200 --seconds 60 } }
```

当多轮结果波动超过 15%，先排查机器背景负载，再讨论线程数调优。

#### 常见坑
- **阻塞调用放在 IO 线程**：单连接没问题，多连接马上出现长尾。  
- **错误共享可变对象**：跨 EventLoop 写同一状态，导致竞态。  
- **线程数拍脑袋配置**：环境变化后无法解释性能波动。  
- **忘记回到 Channel 所属线程**：回调里直接操作 Channel 触发并发问题。

#### 故障案例复盘（可演练）
- **案例 A：发布后延迟突增**  
  现象：CPU 未满但 P99 从 20ms 升到 180ms。  
  根因：新需求把 Redis 同步调用放进 `channelRead`，阻塞 IO 线程。  
  处置：迁移到独立业务线程池，并在回调里切回 EventLoop 回写。  
  验收：压测 10 分钟后 P99 恢复到 25ms 以内，`pendingTasks` 峰值下降 60% 以上。  

- **案例 B：扩线程后吞吐反降**  
  现象：worker 从 8 提到 64 后吞吐下降 12%。  
  根因：上下文切换和队列争用增加，业务并未增加可并行度。  
  处置：回退到接近核心数并固定亲和策略，保留压测证据。

### 3.3 完整代码清单
- `example/src/main/java/io/netty/example/echo/EchoServer.java`
- `transport/src/main/java/io/netty/channel/SingleThreadEventLoop.java`
- `transport/src/main/java/io/netty/channel/MultithreadEventLoopGroup.java`

### 3.4 测试验证

请结合本章步骤执行功能、稳定性与可观测性验证。

#### 验证口径

- [ ] **功能**：核心用例可复现，关键输入输出与预期一致。
- [ ] **稳定性**：连续压测或重复执行无异常抖动、无明显长尾退化。
- [ ] **可观测性**：日志、指标与关键错误信号可定位并支持问题回溯。

## 4. 项目总结
EventLoopGroup 的本质是“有边界的并发”：连接内串行、连接间并行。这个模型让 Netty 以较低锁开销维持高吞吐，也带来一条硬规则：IO 线程不能被阻塞业务污染。你应该把线程调优分成三层：Boss/Worker 基线、阻塞任务隔离、指标驱动微调。只有按这个顺序，性能优化才是工程行为而不是玄学。

生产实践里，建议固定四个观察指标：`eventLoop pendingTasks`、P95/P99 延迟、活跃连接数、CPU 上下文切换次数。每次改线程参数都要做同条件压测并留档。这样当业务量翻倍时，你可以按证据扩容，而不是靠“经验感觉”。

落地时可把“线程模型变更”纳入发布检查清单：是否新增阻塞调用、是否更新压测报告、是否设置回滚阈值、是否准备了故障演练脚本。这样即使人员轮换，团队也能持续保持同一套调优标准。

### 4.5 思考题

1. 为什么一个 `Channel` 绑定固定 `EventLoop` 能减少锁竞争？这会带来哪些边界约束？  
2. 你会如何在不破坏事件有序性的前提下，引入异步 DB 查询并回写响应？

答案见：[附录-思考题答案索引](附录-思考题答案索引.md)
