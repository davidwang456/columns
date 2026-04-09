# 第七章（分篇二）：`RBlockingQueue`——阻塞等待与「仍在处理中」风险

[← 第七章导览](14-队列与流.md)｜[目录](README.md)

---

## 1. 项目背景

Worker 线程不想 **死循环 sleep**，希望在 **有任务时立刻被唤醒**；或网关后置逻辑要从 Redis 队列 **拉一条就处理**。  
`RBlockingQueue<V>` 实现 **`java.util.concurrent.BlockingQueue`**，支持 **`take` / `poll(timeout)`** 等阻塞语义，底层通常对应 Redis **BLPOP/BRPOP** 一类阻塞原语（以版本与实现为准）。

---

## 2. 项目设计（大师 × 小白）

**小白**：阻塞多优雅，我全用 `take`？  
**大师**：优雅的前提是 **线程池与超时可控**。`take` 会一直等——**Shutdown 时要能中断**，否则进程停不下来。

**小白**：`take` 出来就稳了吧？  
**大师**：**取出 ≠ 处理完**。线程在业务里跑一半被 `kill -9`，消息已经从队列移除——和分篇一一样，**中间态仍可能丢**，除非你用 **可靠队列或 Stream**。

---

## 3. 项目实战（主代码片段）

```java
import org.redisson.api.RBlockingQueue;
import org.redisson.api.RedissonClient;

import java.util.concurrent.TimeUnit;

RedissonClient redisson = /* ... */;

RBlockingQueue<String> q = redisson.getBlockingQueue("notify:pending");

// 生产侧
q.offer("user:9527:EMAIL");

// 消费侧（注意中断与线程池生命周期）
try {
    String msg = q.poll(30, TimeUnit.SECONDS);
    if (msg != null) {
        // 临界区尽量短；需要强可靠则评估 RReliableQueue / RStream
    }
} catch (InterruptedException e) {
    Thread.currentThread().interrupt();
}
```

**扩展**：`pollFromAny` 等可在 **多个队列名** 上等待（见接口 JavaDoc），适合 **分片队列** 的消费端。

---

## 4. 项目总结

| 维度 | 说明 |
|------|------|
| **优点** | **阻塞语义**省 CPU；与 Java 并发库习惯一致；可多队列 **`pollFromAny`**。 |
| **缺点** | **仍无处理完成确认**；长阻塞连接对 **超时、断线重连** 敏感；错误配置易 **占满线程**。 |
| **适用场景** | 单组 worker、延迟敏感、可接受 **至多一次** 或业务层 **幂等兜底** 的任务拉取。 |
| **注意事项** | 与 **第三章** 联动：勿在 **Netty EventLoop** 里调阻塞 `take`。 |
| **常见踩坑** | 认为 **阻塞队列 = 可靠队列**；无 **队列深度监控**；中断未正确处理导致 **假死**。 |

---

## 本章实验室（约 35 分钟）

**环境**：单 Redis；两个线程：线程 1 生产，线程 2 消费。

### 步骤

1. `getBlockingQueue("lab:rbq:task")`，线程 1 延迟 3s 后 `offer("hello")`。  
2. 线程 2 先调用 `poll(1, TimeUnit.SECONDS)`，应 **超时返回 null**；再 `poll(10, TimeUnit.SECONDS)`，应 **收到 hello**。  
3. 在线程 2 的 `poll` 阻塞期间，线程 3 调用 `Thread.interrupt()` 打断线程 2，确认 **捕获 `InterruptedException` 且中断标志已恢复处理**（`interrupt()` 调用）。  
4. 连续 `offer` 10 条，`poll` 清空后 `redis-cli LLEN` 为 0。

### 验证标准

- 能演示：**超时 poll 与永久阻塞的差异**。  
- 中断后线程 **不会** 永久卡死（或能说明为何仍卡死及配置问题）。

### 记录建议

- 检查清单：**生产环境阻塞队列线程池 shutdown 时是否 interrupt worker**。

**上一篇**：[第七章（分篇一）RQueue / RDeque](15-RQueue与RDeque.md)｜**下一篇**：[第七章（分篇三）RReliableQueue](17-RReliableQueue.md)｜**下一章**：[第八章上](20-分布式锁-RLock与看门狗.md)
