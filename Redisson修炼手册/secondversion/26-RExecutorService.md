# 第十一章（分篇二）：`RExecutorService`——分布式任务投递与 Worker

[← 第十一章导览](24-分布式服务.md)｜[目录](README.md)

---

## 1. 项目背景

订单服务只想把 **「生成 PDF 凭证」** 扔给后台算力，任意 **空闲实例** 抢到任务执行即可；任务与结果需经 **Redis 排队**，并支持 **`RedissonClient` 注入** 访问共享数据。  
**`RExecutorService`** 实现 **`ExecutorService` 风格**：`submit(Runnable/Callable)`，底层把 **任务与返回结果** 序列化进 **请求/响应队列**（见官方 Overview）。

---

## 2. 项目设计（大师 × 小白）

**小白**：和我自己建个 `ExecutorService` + DB 任务表差在哪？  
**大师**：差在 **谁维护队列、谁抢任务、失败重试默认策略**。Redisson 帮你 **用 Redis 做协调**；但你仍要写 **幂等**、**监控**、**慢任务隔离**——魔法在 **分布式**，不在 **免设计**。

**小白**：Lambda 一写就提交？  
**大师**：Lambda **必须可序列化**（常见写法是 `Callable & Serializable` 或独立任务类）；否则 **反序列化端** 根本跑不起来。

---

## 3. 项目实战（主代码片段）

```java
import org.redisson.api.RExecutorService;
import org.redisson.api.RedissonClient;
import org.redisson.api.annotation.RInject;
import org.redisson.api.WorkerOptions;

import java.io.Serializable;
import java.util.concurrent.Callable;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;

// 任务类需可被 worker 端加载；生产常见：独立 jar + Redisson Node，或保证 classpath 一致
public static class SumMapTask implements Callable<Long>, Serializable {

    @RInject
    private RedissonClient redissonClient;

    @Override
    public Long call() {
        return redissonClient.getAtomicLong("metrics:sum").get();
    }
}

RedissonClient redisson = /* ... */;

// Worker 端（可与提交端同一进程，也可在专用节点）
RExecutorService workerSide = redisson.getExecutorService("report-workers");
workerSide.registerWorkers(WorkerOptions.defaults().workers(2).taskTimeout(120, TimeUnit.SECONDS));

// 提交端
RExecutorService submitSide = redisson.getExecutorService("report-workers");
Future<Long> future = submitSide.submit(new SumMapTask());
// Long r = future.get();
```

**更多**：`ExecutorOptions`（如 **taskRetryInterval**）、**Spring Bean 注入**、**取消与中断** 见 [services.md 的 Executor service](../data-and-services/services.md#executor-service)。

---

## 4. 项目总结

| 维度 | 说明 |
|------|------|
| **优点** | **标准线程池 API**；多实例 **自然负载分散**；`@RInject` 拿 **RedissonClient / taskId**。 |
| **缺点** | **可观测与死信** 需自建；**任务类版本** 不一致会导致 **执行失败**；大任务体 **占 Redis 内存与带宽**。 |
| **适用场景** | 与 Redis 共栈的 **离线计算片段**、批处理子任务、多 worker 抢单。 |
| **注意事项** | **`registerWorkers` 与 `submit` 使用同一 executor 名称**；配置 **taskTimeout**、队列监控。 |
| **常见踩坑** | **无 Worker** 导致任务永远排队；任务 **非 Serializable**；**无幂等** 在重试下 **双写**；慢任务堵死 **共享线程池**。 |

---

## 本章实验室（约 45～60 分钟）

**环境**：单 Redis；同一 executor 名称 `lab-exec-demo`；**先 registerWorkers 再 submit**。

### 步骤

1. `registerWorkers(WorkerOptions.defaults().workers(2).taskTimeout(30, TimeUnit.SECONDS))`。  
2. 提交 `SumMapTask`（或自定义任务）**10** 次，`Future.get` 全部完成，记录 **总耗时**。  
3. **不启动 worker**，仅 submit **1** 次，`get` 带 **5s 超时**，捕获超时异常并截图日志。  
4. 任务内 `redissonClient.getAtomicLong("lab:exec:counter").incrementAndGet()`，配置重试后故意让任务 **失败再成功**，统计 **counter 增量是否 > 成功次数**（理解 **至少一次** 副作用）。

### 验证标准

- 实验 3：**可复现**「无 worker 则阻塞/超时」。  
- 实验 4：**书面结论** 是否需要 **幂等键**。

### 记录建议

- **taskTimeout、重试间隔** 与业务 SLA 对照表。

**上一篇**：[第十一章（分篇一）Remote Service](25-RemoteService.md)｜**下一篇**：[第十一章（分篇三）RScheduledExecutorService](27-RScheduledExecutorService.md)｜**下一章**：[第十二章导览](29-Spring生态集成.md)
