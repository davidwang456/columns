# 35-Unsafe与零拷贝传输

## 1. 项目背景

某文件分发服务在 10Gbps 网络环境下吞吐始终上不去，CPU 占用却接近 90%。火焰图显示大量时间花在内存复制与用户态/内核态切换。团队把重点放在业务算法优化，几周后发现真正瓶颈在数据搬运路径：从磁盘读到用户态，再拷贝到内核 socket 缓冲，重复拷贝导致吞吐受限。Netty 在这类场景的高阶能力正是“尽量减少不必要拷贝”，包括 `FileRegion`、`CompositeByteBuf`、direct buffer 等零拷贝近似方案。

同时，`Channel.Unsafe` 作为底层传输抽象的内部接口，承载了注册、读写、flush、关闭等关键动作。理解 `Unsafe` 不是鼓励业务直接调用，而是帮助你定位“零拷贝优化在何处生效、在何处失效”。例如 TLS 加密链路会破坏文件直传路径，某些协议转换也会强制重新编码，导致“以为零拷贝，实际又拷回来了”。

本章会从源码边界讲清楚：哪些优化是真零拷贝，哪些是少拷贝；什么时候值得做，什么时候会引入复杂度陷阱。

## 2. 项目设计（剧本式交锋对话）

| 角色 | 定位 |
| --- | --- |
| 小胖 | 业务视角提出诉求与约束 |
| 小白 | 工程实现与落地执行 |
| 大师 | 架构把关与取舍决策 |

### 第一轮

小胖：零拷贝是不是“完全不拷贝”？  
小白：`sendfile` 能不能覆盖所有场景？  
大师：多数情况下是“减少拷贝次数”。`sendfile` 对明文文件下发很好，但加密、压缩、改写内容时就会失效。

技术映射：直达电梯最快，但中途要安检换装就必须绕路。

### 第二轮

小胖：那 `Unsafe` 名字这么吓人，我是不是别看它？  
小白：不直接调用也要懂它干什么？  
大师：对。`Unsafe` 是理解底层行为的“观察窗”，能帮助你定位写路径是否走了预期分支。

技术映射：你不修发动机，也要知道仪表盘每个灯代表什么。

### 第三轮

小胖：我把所有 buffer 都改 direct 就行了？  
小白：direct 内存管理不是更复杂吗？  
大师：direct 对网络 IO 通常更友好，但分配释放成本高，泄漏排查更难。要配池化和严格生命周期管理。

技术映射：高速通道更快，但通行规则更严格，违规代价更高。

## 3. 项目实战

### 3.1 环境准备

- JDK 17、Netty 4.1.x  
- 测试文件：1KB、64KB、10MB 三组  
- 系统工具：`perf`、`sar`、`iostat`  
- 开关：是否启用 TLS、是否使用 `FileRegion`

### 3.2 分步实现

**步骤目标 1：实现普通 ByteBuf 发送与 FileRegion 发送两套路径。**

```java
// 普通路径：读文件到 ByteBuf 再 write
ByteBuf buf = ctx.alloc().buffer((int) file.length());
buf.writeBytes(Files.readAllBytes(file.toPath()));
ctx.writeAndFlush(buf);
```

```java
// 零拷贝近似路径：FileRegion
try (RandomAccessFile raf = new RandomAccessFile(file, "r")) {
    FileChannel fc = raf.getChannel();
    FileRegion region = new DefaultFileRegion(fc, 0, fc.size());
    ctx.writeAndFlush(region);
}
```

**步骤目标 2：开启 TLS 验证失效边界。**

```java
pipeline.addFirst("ssl", sslContext.newHandler(ch.alloc()));
```

对比：同文件在 TLS 前后是否还能维持 sendfile 收益。

**步骤目标 3：观测底层写路径。**

命令示例：
```bash
perf record -g -p <pid> -- sleep 30
perf report
```

若 `copy_user_enhanced_fast_string` 占比下降，说明拷贝压力缓解。

可能遇到的坑：

1. 文件通道生命周期管理不当，导致句柄泄漏。  
2. TLS 场景误以为仍是零拷贝，性能预期错误。  
3. direct buffer 泄漏导致进程触达 `MaxDirectMemorySize`。

### 3.3 完整代码清单

- `transport/src/main/java/io/netty/channel/AbstractChannel.java`（`Unsafe` 相关）
- `transport/src/main/java/io/netty/channel/DefaultFileRegion.java`
- 业务样例：`example/filetransfer/ZeroCopyServer.java`
- 基准脚本：`bench/zerocopy_tls_compare.sh`

### 3.4 测试验证

命令示例：
```bash
curl -O http://127.0.0.1:8080/static/10m.bin
```

命令示例：
```bash
wrk -t4 -c64 -d60s http://127.0.0.1:8080/static/10m.bin
```

验收：

- 明文大文件路径吞吐显著提升
- TLS 下性能回落符合预期且稳定
- 无 direct memory 泄漏告警

#### 验证口径

- [ ] **功能**：核心用例可复现，关键输入输出与预期一致。
- [ ] **稳定性**：连续压测或重复执行无异常抖动、无明显长尾退化。
- [ ] **可观测性**：日志、指标与关键错误信号可定位并支持问题回溯。

## 4. 项目总结

### 4.1 优点&缺点

| 维度 | 优点 | 缺点 |
| --- | --- | --- |
| 吞吐 | 减少拷贝与上下文切换，大文件收益明显 | 场景受限，不是全链路通吃 |
| CPU | 降低用户态处理开销 | 观察与调优门槛更高 |
| 架构 | 与 Netty 传输层配合紧密 | 遇 TLS/变换链路会失效 |

### 4.2 适用场景

适用：

1. 大文件下载、静态资源分发
2. 明文内网传输
3. 对吞吐和 CPU 成本敏感的网关

不适用：

1. 强制 TLS 且需内容改写的链路
2. 小包高频且业务变换复杂场景

### 4.3 注意事项

- 先明确链路是否允许 `sendfile`，再投入优化开发。
- direct memory 监控必须与泄漏检测配套。
- 对“零拷贝收益”做按文件大小分层评估，避免平均值误导。

### 4.4 常见踩坑经验

1. **故障案例：上线后吞吐无提升**  
   根因：全链路 TLS，`FileRegion` 优势被加密过程抵消。
2. **故障案例：句柄耗尽**  
   根因：异常分支未关闭 `FileChannel`。
3. **故障案例：偶发 OOM Direct buffer memory**  
   根因：自定义 handler 持有 direct `ByteBuf` 未释放。

### 4.5 思考题

1. 你会如何在运行时自动判断“当前请求是否值得走 FileRegion 路径”？
2. 在 TLS 必选前提下，还有哪些“少拷贝”策略可以继续优化吞吐？

答案见：[附录-思考题答案索引](附录-思考题答案索引.md)
