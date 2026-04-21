# 34-writeflush语义与批量写

## 1. 项目背景

某日志采集系统在峰值时吞吐不足，排查发现网络层频繁系统调用：业务每条消息都 `writeAndFlush`，导致 flush 过于密集，内核发送队列和 TCP 包聚合效率都很差。开发同学常把 `write` 与 `flush` 当成同一个动作，实际上在 Netty 中二者语义明确分离：`write` 只是入站到 `ChannelOutboundBuffer`，`flush` 才触发真正下发。理解这个分离，是做批量写优化的第一步。

高级优化不是简单“少 flush 一点”，而是根据实时压力、消息大小、延迟 SLA 设计批量策略：多大批次、多久强制 flush、遇到高水位如何回压、在连接关闭前如何确保尾包刷出。很多线上故障都发生在这个边界上：吞吐优化后延迟恶化，或者低延迟追求下 CPU 飙升。

本章聚焦源码语义与可落地策略，帮助你从“盲目 writeAndFlush”升级到“可观测、可调参、可回滚”的批量写体系。

## 2. 项目设计（剧本式交锋对话）

| 角色 | 定位 |
| --- | --- |
| 小胖 | 业务视角提出诉求与约束 |
| 小白 | 工程实现与落地执行 |
| 大师 | 架构把关与取舍决策 |

### 第一轮

小胖：`writeAndFlush` 一行搞定，不香吗？  
小白：如果每条都 flush，会不会把批量机会全丢掉？  
大师：正解。`write` 和 `flush` 拆开就是为了聚合。频繁 flush 会放大 syscall 与包头开销。

技术映射：快递每收一件就发车，时效看似高，运输成本会爆炸。

### 第二轮

小胖：那我一秒 flush 一次，吞吐肯定高。  
小白：但实时消息不就超时了？  
大师：所以要“大小阈值 + 时间阈值”双触发：既能吃到批量收益，又不突破延迟上限。

技术映射：班车满员发车，也要保证最晚发车时间。

### 第三轮

小胖：批量写和水位线有什么关系？  
小白：高水位触发后还能继续囤数据吗？  
大师：不能。批量写必须和 `isWritable()` 联动，越过高水位要降速或丢弃，避免把问题从网络层推成内存问题。

技术映射：仓库积压到警戒线就要限流，不是继续进货。

## 3. 项目实战

### 3.1 环境准备

- JDK 17、Netty 4.1.x  
- 压测：`wrk` 或自定义 TCP 写入脚本  
- 关键配置：
  - `ChannelOption.WRITE_BUFFER_WATER_MARK`
  - `ChannelOption.TCP_NODELAY`（按场景评估）
- 观测：flush 次数、每次批量大小、P99、系统调用频率

### 3.2 分步实现

**步骤目标 1：实现批量写缓冲器。**

```java
public final class BatchWriter {
    private final ChannelHandlerContext ctx;
    private int pendingBytes;
    private long lastFlushNanos = System.nanoTime();
    private final int maxBatchBytes = 64 * 1024;
    private final long maxDelayNanos = TimeUnit.MILLISECONDS.toNanos(5);

    public void send(ByteBuf msg) {
        pendingBytes += msg.readableBytes();
        ctx.write(msg);
        long now = System.nanoTime();
        if (pendingBytes >= maxBatchBytes || now - lastFlushNanos >= maxDelayNanos) {
            flush(now);
        }
    }

    public void flush(long now) {
        ctx.flush();
        pendingBytes = 0;
        lastFlushNanos = now;
    }
}
```

**步骤目标 2：与可写状态联动。**

```java
@Override
public void channelWritabilityChanged(ChannelHandlerContext ctx) {
    if (!ctx.channel().isWritable()) {
        rateLimiter.pause();
    } else {
        rateLimiter.resume();
    }
    ctx.fireChannelWritabilityChanged();
}
```

**步骤目标 3：关闭前尾包保障。**

```java
ctx.channel().closeFuture().addListener(f -> {
    ctx.flush();
});
```

命令：

命令示例：
```bash
wrk -t8 -c500 -d90s http://127.0.0.1:8080/push
```

可能遇到的坑：

1. 时间阈值过大导致实时业务超时。  
2. 只做批量不看水位线，触发内存积压。  
3. 关闭流程未补刷，尾部消息丢失。

### 3.3 完整代码清单

- `transport/src/main/java/io/netty/channel/ChannelOutboundBuffer.java`
- `transport/src/main/java/io/netty/channel/AbstractChannel.java`
- 业务样例：`gateway/outbound/AdaptiveBatchFlushHandler.java`
- 压测脚本：`bench/batch_flush_compare.sh`

### 3.4 测试验证

命令示例：
```bash
curl -X POST http://127.0.0.1:8080/push -d "hello"
```

命令示例：
```bash
wrk -t8 -c300 -d60s http://127.0.0.1:8080/push
```

验收：

- flush 次数下降，平均 batch bytes 上升
- P99 不超过目标 SLA
- 高水位触发后系统可恢复，不出现持续不可写

#### 验证口径

- [ ] **功能**：核心用例可复现，关键输入输出与预期一致。
- [ ] **稳定性**：连续压测或重复执行无异常抖动、无明显长尾退化。
- [ ] **可观测性**：日志、指标与关键错误信号可定位并支持问题回溯。

## 4. 项目总结

### 4.1 优点&缺点

| 维度 | 优点 | 缺点 |
| --- | --- | --- |
| 吞吐 | 减少 flush/syscall，提高批量发送效率 | 参数不当会增加尾延迟 |
| 资源 | 降低 CPU 与包头开销 | 批次缓存会增加瞬时内存占用 |
| 稳定性 | 可结合水位线做回压治理 | 需要精细化监控与动态调参 |

### 4.2 适用场景

适用：

1. 高频小消息推送
2. 可接受毫秒级聚合窗口的业务
3. 需要提高网卡利用率的网关

不适用：

1. 极低延迟交易链路（每条消息都需立刻发送）
2. 无法容忍批次内排队抖动的控制指令

### 4.3 注意事项

- 使用“字节阈值 + 时间阈值”而非单一阈值。
- 将批量参数做成可热更新配置，并配回滚开关。
- 必须观测每连接不可写时长，防止慢连接拖垮整体。

### 4.4 常见踩坑经验

1. **故障案例：吞吐提升但投诉增多**  
   根因：批量窗口设为 50ms，造成消息时效超标。
2. **故障案例：实例 OOM**  
   根因：持续不可写时仍不停 `write`，缓冲区爆涨。
3. **故障案例：重启丢尾包**  
   根因：关机流程未显式 flush，最后批次未下发。

### 4.5 思考题

1. 如何为“高吞吐日志流”和“低延迟控制流”在同一连接中设计差异化 flush 策略？
2. 当网络抖动导致可写状态频繁翻转时，批量写策略应如何避免抖动放大？

答案见：[附录-思考题答案索引](附录-思考题答案索引.md)
