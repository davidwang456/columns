# 第七章（分篇四）：`RStream`——消费者组、ACK 与 pending

[← 第七章导览](14-队列与流.md)｜[目录](README.md)

---

## 1. 项目背景

需要 **可堆积** 的业务事件流：多条服务往同一流 **`XADD`**，多个消费者组 **各自维护消费进度**，失败消息进 **pending**，超时 **`XAUTOCLAIM` / claim** 回收。  
Redis 5.0+ **Stream** 是通用方案；Redisson 封装为 **`RStream<K, V>`**（字段名与值类型由 `StreamAddArgs` 等描述），适合 **希望精细控制 Stream 语义** 的团队。

---

## 2. 项目设计（大师 × 小白）

**小白**：有 `RReliableQueue` 了还要 `RStream`？  
**大师**：可靠队列是 **高层消息产品**；`RStream` 是 **原语 + 消费者组**。你要 **多消费者组分频道、自定义字段、与外部工具读同一 Stream**，用 `RStream` 更直接。

**小白**：Stream 会不会消息无限涨？  
**大师**：会。**`MAXLEN`、修剪策略、监控长度** 是架构义务，不是可选项。

---

## 3. 项目实战（主代码片段）

```java
import org.redisson.api.RStream;
import org.redisson.api.RedissonClient;
import org.redisson.api.stream.StreamAddArgs;
import org.redisson.api.stream.StreamCreateGroupArgs;
import org.redisson.api.stream.StreamMessageId;

RedissonClient redisson = /* ... */;

RStream<String, String> stream = redisson.getStream("orders:events");

StreamMessageId id = stream.add(
        StreamAddArgs.entry("type", "PAID").entry("orderId", "1001"));

stream.createGroup(StreamCreateGroupArgs.name("fulfillment").makeStream());

// readGroup、ack、pendingRange、autoClaim 等见官方完整示例
// stream.readGroup("fulfillment", "consumer-1", StreamReadGroupArgs.neverDelivered());
// stream.ack("fulfillment", id);
```

**完整示例（Sync/Async/Reactive）**：[queues.md 的 Stream 小节](../data-and-services/queues.md#stream)。**接口**：[RStream JavaDoc](https://www.javadoc.io/doc/org.redisson/redisson/latest/org/redisson/api/RStream.html)。

---

## 4. 项目总结

| 维度 | 说明 |
|------|------|
| **优点** | **持久流 + 消费组**；**ACK / pending / reclaim** 标准语义；与 **Kafka 之外** 的轻量事件管线的常见选型。 |
| **缺点** | **手写消费逻辑多**；长度与内存需治理；**强顺序跨分片** 仍难；海量场景宜 **专业 MQ**。 |
| **适用场景** | 异步事件、可回放需求雏形、多订阅方 **分组合约**、要与 **redis-cli / 生态** 直接对齐的运维模型。 |
| **注意事项** | 部分 API 要求 **更高 Redis 版本**；消费端 **幂等 + 重试上限 + 死信** 仍必备。 |
| **常见踩坑** | 只 `readGroup` **不处理 pending** → 幽灵积压；把 Stream 当 **无限日志** 不修剪；**误解恰好一次**。 |

**pending 与 reclaim**：已投递未 ACK → pending；必须设计 **重试上限、死信、超时 reclaim**。

---

## 本章实验室（约 45～60 分钟）

**环境**：Redis 5+；参考 [queues.md Stream 示例](../data-and-services/queues.md#stream)。

### 步骤

1. `getStream("lab:stream:evt")`，`add(StreamAddArgs.entry("k","v"))` 连续 5 条，记录返回的 **ID**。  
2. `createGroup(StreamCreateGroupArgs.name("g1").makeStream())`（若已存在则跳过或换新流名）。  
3. `readGroup("g1", "c1", StreamReadGroupArgs.neverDelivered())`，消费 2 条后 **只对其中 1 条 ack**，另一条不 ack。  
4. `pendingRange("g1", "c1", ...)` 查看 **pending 条数 ≥ 1**；对未 ack 的 ID 执行 `ack`。  
5. `redis-cli`：`XLEN lab:stream:evt`（注意物理 key 名可能带前缀，用 `SCAN` 找）与 Java 侧条数对照。

### 验证标准

- pending 在未 ack 前 **非空**，ack 后 **对应项消失**（以 `XPENDING` 语义理解）。  
- 能口述：**pending 与「业务处理完成」的对应关系**。

### 记录建议

- 列出：**本服务 Stream 修剪策略（MAXLEN 或定时任务）** 初稿。

**上一篇**：[第七章（分篇三）RReliableQueue](17-RReliableQueue.md)｜**下一篇**：[第七章（分篇五）RRingBuffer](19-RRingBuffer.md)｜**下一章**：[第八章上](20-分布式锁-RLock与看门狗.md)
