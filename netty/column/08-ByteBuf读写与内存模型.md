# 08-ByteBuf读写与内存模型

## 1. 项目背景
网络服务的性能瓶颈常常不在 CPU 算法，而在内存读写与对象分配。传统 `ByteBuffer` 用起来规范但不够灵活：扩容麻烦、读写指针共用、池化能力弱。Netty 通过 `ByteBuf` 提供了双指针（readerIndex/writerIndex）、动态扩容、堆内/堆外统一接口和池化分配器。很多线上抖动问题，本质是 ByteBuf 使用不当：重复拷贝、错误切片、读写越界、释放时机错。

`buffer/src/main/java/io/netty/buffer/AbstractByteBuf.java` 与 `PooledByteBufAllocator.java` 体现了 Netty 内存模型核心：尽量复用内存块、减少系统调用与 GC 压力。官方示例 `example/src/main/java/io/netty/example/echo` 虽简单，但已经依赖 ByteBuf 进行高效编解码。理解 ByteBuf 的读写语义，是进入高性能 Netty 编程的第一道门槛。

## 2. 项目设计（剧本式交锋对话）
小胖：我直接 `toString()` 转字符串处理，写完再 new 一个 ByteBuf 回包，不就行了？  
小白：能跑，但会产生额外拷贝和分配，吞吐一上来就抖。  
大师：ByteBuf 的价值就在“少拷贝、少分配”。技术映射：外卖盒反复复用，比每单都买新盒省得多。

小胖：`readBytes` 和 `getBytes` 我总搞混。  
小白：`readBytes` 会推进 readerIndex，`getBytes` 不会改索引。  
大师：一个是“读并前进”，一个是“窥视不前进”。技术映射：翻书做笔记会移动页码，偷看目录不会。

小胖：切片 `slice()` 很省内存，那是不是随便切？  
小白：切片共享底层内存，生命周期绑定原对象，乱用会出悬挂引用。  
大师：高效操作都伴随约束。技术映射：合租便宜，但公共空间要遵守规则。

## 3. 项目实战
### 3.1 环境准备
- JDK 17，Maven 3.9+
- 关键源码：`buffer/AbstractByteBuf.java`、`buffer/PooledByteBufAllocator.java`
- 示例路径：`example/src/main/java/io/netty/example/factorial`

### 3.2 分步实现
**步骤目标：实现长度字段协议解析，验证 ByteBuf 索引与切片行为。**

1. 使用 `LengthFieldBasedFrameDecoder` 拆包后，在 handler 中读取 `ByteBuf`。  
2. 先用 `getInt(readerIndex)` 预读消息头，不移动索引；确认完整帧后再 `readInt()` + `readBytes()`。  
3. 对消息体使用 `retainedSlice()` 传给下游，避免整块复制。  
4. 回包使用 `ctx.alloc().buffer()`，预估容量后写入，减少扩容次数。

命令示例：
```bash
mvn -pl example -DskipTests compile
mvn -pl example exec:java -Dexec.mainClass=io.netty.example.factorial.FactorialServer
```

客户端发送不同长度包体，观察日志中的 `readerIndex/writerIndex` 变化是否符合预期。

#### 项目实战补充：内存与性能双口径验收
ByteBuf 优化最怕“局部看起来快，整体却变差”，建议固定以下验收口径：

1. **功能口径**：同一组报文在高并发下不得出现解析错位与边界错误。  
2. **性能口径**：对比优化前后每秒分配次数、直接内存峰值、P99 延迟。  
3. **稳定性口径**：长压 30 分钟后，不应出现持续上升的直接内存占用。  
4. **可追溯口径**：变更需附带压测命令、样本数据和机器环境说明。

建议增加压测命令（示例）：

命令示例：
```bash
python .\bench\frame_bench.py --size-mix 64,512,4096 --conn 100 --seconds 300
```

观察点建议落在三类日志：分配器统计、GC 暂停时间、协议解析错误计数。

#### 常见坑
- **`read*` 与 `get*` 混用无意识**：索引错位导致协议解析失败。  
- **滥用 `copy()`**：吞吐下降，GC 压力上升。  
- **切片后原 buf 提前释放**：下游访问触发非法引用。  
- **容量估算过小**：频繁扩容引发性能毛刺。

#### 故障案例（高并发下常见）
- **案例 A：内存占用缓慢爬升**  
  根因：切片对象跨线程传递但生命周期约定不清，部分路径未释放。  
  修复：统一使用 `retainedSlice` + 明确接收方释放责任，并加泄漏检测回归。  

- **案例 B：偶发协议解析失败**  
  根因：`get*` 与 `read*` 混用导致 readerIndex 偏移不一致。  
  修复：先完整帧判定，再一次性推进索引，并用单测覆盖边界帧。

### 3.3 完整代码清单
- `buffer/src/main/java/io/netty/buffer/AbstractByteBuf.java`
- `buffer/src/main/java/io/netty/buffer/PooledByteBufAllocator.java`
- `example/src/main/java/io/netty/example/factorial/FactorialServerHandler.java`

### 3.4 测试验证

请结合本章步骤执行功能、稳定性与可观测性验证。

#### 验证口径

- [ ] **功能**：核心用例可复现，关键输入输出与预期一致。
- [ ] **稳定性**：连续压测或重复执行无异常抖动、无明显长尾退化。
- [ ] **可观测性**：日志、指标与关键错误信号可定位并支持问题回溯。

## 4. 项目总结
ByteBuf 不是“替代 ByteBuffer 的语法糖”，而是 Netty 性能模型的一部分。你写对索引语义、控制复制次数、合理使用切片与池化，系统延迟和吞吐会直接改善；你写错任何一条，问题会在高并发下被成倍放大。建议团队统一三条编码规范：先判完整帧再推进索引、默认避免不必要 copy、所有共享切片必须声明生命周期责任。

另外，性能优化要以指标为准：分配次数、直接内存占用、P99 延迟。只有把 ByteBuf 使用模式与指标联动，你才能知道优化是真收益还是心理安慰。

最后建议团队给 ByteBuf 相关变更加一条发布规则：凡涉及切片、零拷贝、池化参数调整，必须附“功能 + 性能 + 稳定性”三份验证结论。这样可以把高风险内存改动控制在可回滚范围内。

如果要进一步提升稳定性，可以把常见报文做成“标准回放集”，每次升级 Netty 或调整分配器参数都自动回放并对比结果。只要标准样本持续通过，ByteBuf 层面的回归风险就能被提前发现，而不是等线上高峰暴露问题。

### 4.5 思考题

1. 在协议解析场景中，`get*` 与 `read*` 各自适用于哪些步骤？  
2. `slice()`、`retainedSlice()`、`copy()` 的内存与生命周期差异是什么？你会如何选择？

答案见：[附录-思考题答案索引](附录-思考题答案索引.md)
