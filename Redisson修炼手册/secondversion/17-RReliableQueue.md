# 第七章（分篇三）：`RReliableQueue`——基于 Stream 的可靠队列

[← 第七章导览](14-队列与流.md)｜[目录](README.md)

---

## 1. 项目背景

订单支付成功后要 **可靠投递** 一条「出账任务」：不能 **静默丢失**，又要支持 **可见性超时后重投**、**死信队列**、**去重** 与 **限流**。  
普通 `RQueue`/`RBlockingQueue` 取出即删，难以满足；Redisson 提供 **`RReliableQueue<V>`**，实现上基于 **Redis Stream**（见接口类注释），暴露 **显式 ACK / NACK**、配置项丰富的消息模型。

---

## 2. 项目设计（大师 × 小白）

**小白**：可靠队列和分篇五 `RStream` 自己 `readGroup` 有啥区别？  
**大师**：`RReliableQueue` 更像 **封装好的消息抽象**（`Message`、`QueueConfig`、DLQ 等）；`RStream` 更 **底层、可编排**。团队若不想手写 pending/reclaim，可优先看清 **可靠队列文档与限制**。

**小白**：上了可靠队列还要幂等吗？  
**大师**：**要。** 「至少一次」投递下 **重复仍可能发生**；ACK 只解决「未确认可重投」，不替你证明 **业务只执行一次**。

---

## 3. 项目实战（主代码片段）

```java
import org.redisson.api.Message;
import org.redisson.api.MessageArgs;
import org.redisson.api.RReliableQueue;
import org.redisson.api.RedissonClient;
import org.redisson.api.queue.QueueAckArgs;
import org.redisson.api.queue.QueueAddArgs;
import org.redisson.api.queue.QueueConfig;

import java.time.Duration;

RedissonClient redisson = /* ... */;

RReliableQueue<String> q = redisson.getReliableQueue("billing:tasks");
q.setConfigIfAbsent(
        QueueConfig.defaults()
                .visibility(Duration.ofSeconds(60))
                .deliveryLimit(5));

// 返回的 Message 可能为 null：队列满、去重命中等，需按业务处理
q.add(QueueAddArgs.messages(MessageArgs.payload("order:9001")));

Message<String> m = q.poll();
if (m != null) {
    try {
        process(m.getPayload());
        q.acknowledge(QueueAckArgs.ids(m.getId()));
    } catch (Exception e) {
        // negativeAcknowledge / DLQ 策略见官方文档与 QueueConfig
        throw e;
    }
}
```

**权威文档**：[queues.md](../data-and-services/queues.md) 中 **Reliable Queue** 相关小节；**`QueueConfig`** 字段多，上线前逐项评审（TTL、消息大小、同步复制等）。

---

## 4. 项目总结

| 维度 | 说明 |
|------|------|
| **优点** | **显式确认**、重投、DLQ、去重、优先级等 **开箱能力**；比手写 Stream 消费组 **省胶水代码**。 |
| **缺点** | **配置与运维复杂度高**；仍依赖 **Redis 可用性**；语义需对照文档 **吃透**（非「用了就 100% 不丢」）。 |
| **适用场景** | 需要 **比 List 队列更强保证**、又不想直接操作 **裸 Stream API** 的中台任务。 |
| **注意事项** | 先 **`setConfig` / `setConfigIfAbsent`**；监控 **积压、未 ACK、DLQ**；与 **集群拓扑** 对齐（第二章）。 |
| **常见踩坑** | **未 ACK** 导致消息反复投递却 **无幂等**；`poll` 后处理太长超过 **visibility** 被别实例抢走 → **重复执行**；配置随意 copy 生产。 |

---

## 本章实验室（约 45～60 分钟）

**环境**：单 Redis；按官方文档完成 **`setConfigIfAbsent`** 与最小 **`QueueConfig`**（visibility、deliveryLimit）。

### 步骤

1. `add(QueueAddArgs.messages(MessageArgs.payload("msg-1")))`，再 `poll()` 得到 `Message`，**故意不 acknowledge**，进程退出。  
2. 重启同一队列名，再次 `poll()`：记录 **是否再次拿到同一条或等价重投**（以实际语义为准）。  
3. 正常路径：`poll` → 业务空操作 → `acknowledge(QueueAckArgs.ids(id))`，再 `poll` 应为 **null**（或队列空）。  
4. （可选）将 `visibility` 设极短，`poll` 后 `Thread.sleep` 超过 visibility，另一进程 `poll` 是否拿到 **同消息**——记录 **重复投递** 现象。

### 验证标准

- 能画 **一张小状态机**：ready → in-flight (unacked) → acked / redeliver。  
- 文档化：**本服务消息的业务幂等键**（例如订单号）。

### 记录建议

- 贴 `QueueConfig` 最终版到 Wiki（**脱敏**）。

**上一篇**：[第七章（分篇二）RBlockingQueue](16-RBlockingQueue.md)｜**下一篇**：[第七章（分篇四）RStream](18-RStream.md)｜**下一章**：[第八章上](20-分布式锁-RLock与看门狗.md)
