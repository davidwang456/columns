# 第七章（分篇一）：`RQueue` / `RDeque`——非阻塞队列与双端队列

[← 第七章导览](14-队列与流.md)｜[目录](README.md)

---

## 1. 项目背景

后台要把 **异步任务描述**（JSON 或 DTO）先堆进队列，由单机或多实例 worker **轮询 `poll`**；或审计系统用 **双端结构** 在头部追加「最新事件」、尾部消费历史。  
要求 **简单、低延迟**，且能接受：**一旦元素被取出，在业务处理完成前进程崩溃，这条任务可能丢失**（与可靠队列、Stream 对比见后续分篇）。

---

## 2. 项目设计（大师 × 小白）

**小白**：`RQueue` 和第六章的 `RList` 不都是 List 吗？  
**大师**：底层都可能用 Redis List，但 **`RQueue` 的心智是 `java.util.Queue`**：`offer`/`poll`/`peek`，适合 **FIFO 管道**；`RDeque` 再多 **头尾双端** 能力。别用 `RList` 的 `get(i)` 当队列使，**语义和监控**都会对不上。

**小白**：这比 `RBlockingQueue` 差在哪？  
**大师**：**差不差，是「堵不堵线程」**。`RQueue` **不阻塞**；没元素时 `poll` 立刻 `null`，你要自己 **sleep/调度** 或换分篇二的阻塞版。

---

## 3. 项目实战（主代码片段）

```java
import org.redisson.api.RDeque;
import org.redisson.api.RQueue;
import org.redisson.api.RedissonClient;

import java.util.concurrent.TimeUnit;

RedissonClient redisson = /* ... */;

// FIFO 队列
RQueue<String> jobs = redisson.getQueue("export:jobs");
jobs.offer("order-1001");
String job = jobs.poll(); // 空则 null

// 双端：可模拟栈或「一头进一头出」的流水线
RDeque<String> audit = redisson.getDeque("audit:events");
audit.addLast("ORDER_CREATED");
audit.addFirst("ADMIN_PIN"); // 置顶优先处理时可配合业务约定
String next = audit.pollFirst();
```

**说明**：`readAll()` 等 **一次性拉全量** 只适合可控小队列；长队列会 **压垮 Redis 与网络**。详见 [queues.md](../data-and-services/queues.md)。

---

## 4. 项目总结

| 维度 | 说明 |
|------|------|
| **优点** | API 与 **`Queue` / `Deque`** 一致；**非阻塞**，易嵌入定时任务；实现简单。 |
| **缺点** | **无 ACK**；取出后、业务完成前崩溃 → **消息可能丢失**；无内置 **消费组进度**。 |
| **适用场景** | 可丢或可容忍 **至多一次** 的轻量任务池、缓冲、双端流水。 |
| **注意事项** | 控制 **队列长度**；大队列避免 **全量 read**；多实例消费通常是 **竞争消费**（同一条只被一个 poll 走）。 |
| **常见踩坑** | 把 `RQueue` 当 **可靠 MQ**；`poll` 到消息后 **长事务 / 远程 RPC** 才 ACK（根本没有 ACK）→ 进程杀中间态全丢。 |

---

## 本章实验室（约 25 分钟）

**环境**：单 Redis；一个生产者 `main`、一个消费者 `main`（或同进程两线程）。

### 步骤

1. `getQueue("lab:rqueue:job")`，生产者 `offer` `"j1"`～`"j5"`。  
2. 消费者循环 `poll()` 直到 `null`，打印顺序，应与 **FIFO** 一致。  
3. `redis-cli`：`LLEN`（先定位 key）在消费前后对照，**空队列长度为 0**。  
4. **杀进程模拟**：`poll` 取出 `"j3"` 后 **不 `offer` 回去** 直接结束进程——再查 List，确认 **`j3` 已从 Redis 移除**；书面回答：**若业务未处理完，这条任务去哪了？**

### 验证标准

- 能口述：**非阻塞队列在「取出后崩溃」下的丢失窗口**。  
- `LLEN` 与 Java 侧 **剩余元素** 一致。

### 记录建议

- 一句话结论：**本服务若用 RQueue，能否接受至多一次**。

**上一章**：[第六章（分篇三）ZSet](13-ZSet与排行榜.md)｜[第六章导览](10-分布式集合选型.md)｜**下一篇**：[第七章（分篇二）RBlockingQueue](16-RBlockingQueue.md)｜**下一章**（读完全部分篇后）：[第八章上 分布式锁](20-分布式锁-RLock与看门狗.md)
