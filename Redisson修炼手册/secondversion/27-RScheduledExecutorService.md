# 第十一章（分篇三）：`RScheduledExecutorService`——分布式定时与 Cron

[← 第十一章导览](24-分布式服务.md)｜[目录](README.md)

---

## 1. 项目背景

需要在集群里 **只执行一份**「每小时对账」或 **每实例都要跑** 的「每 5 分钟自检」——需求不同，选型不同；Redisson 提供 **`RScheduledExecutorService`**（同样通过 `redisson.getExecutorService(name)` 获取，见官方文档），支持 **`schedule` / `scheduleAtFixedRate` / `scheduleWithFixedDelay`** 以及 **Cron 表达式**（Quartz 兼容格式）。

---

## 2. 项目设计（大师 × 小白）

**小白**：我用 Spring `@Scheduled` 不行吗？  
**大师**：行。若你要 **跨 JVM 协调「谁跑」**、或与 **Redis 数据紧耦合的延迟任务**，再评估 Redisson Scheduler；否则 **别为技术而技术**。

**小白**：Cron 写在代码里，改频率要发版？  
**大师**：常见痛点。要么 **外置配置中心**，要么接受 **运维成本**；分布式调度没有 **免费配置灵活性**。

---

## 3. 项目实战（主代码片段）

```java
import org.redisson.api.RScheduledExecutorService;
import org.redisson.api.RedissonClient;
import org.redisson.api.WorkerOptions;
import org.redisson.api.CronSchedule;

import java.io.Serializable;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;

// 任务类需 Serializable，且 worker 端 classpath 可见（与 RExecutorService 相同约束）
public static class HeartbeatTask implements Runnable, Serializable {
    @Override
    public void run() {
        // 周期自检逻辑
    }
}

RedissonClient redisson = /* ... */;

RScheduledExecutorService scheduler = redisson.getExecutorService("jobs");
scheduler.registerWorkers(WorkerOptions.defaults().workers(1));

ScheduledFuture<?> once = scheduler.schedule(new HeartbeatTask(), 1, TimeUnit.HOURS);
ScheduledFuture<?> fixed = scheduler.scheduleAtFixedRate(new HeartbeatTask(), 10, 25, TimeUnit.MINUTES);
scheduler.schedule(new HeartbeatTask(), CronSchedule.dailyAtHourAndMinute(2, 30));
```

---

## 4. 项目总结

| 维度 | 说明 |
|------|------|
| **优点** | **Cron 与固定周期** 开箱；与 **Executor** 同一套 Worker 模型；适合 **Redis 栈内定时闭环**。 |
| **缺点** | **时钟漂移、misfire、多实例重复跑** 要按业务设计；**可视化与审计** 通常弱于专业调度平台。 |
| **适用场景** | 轻量周期任务、延迟任务、与 Redisson 数据联动的 **维护型作业**。 |
| **注意事项** | 明确 **集群下需要几个 worker、是否幂等**；长任务配 **超时与中断**；**取消** 用 `RScheduledFuture.cancel` 等。 |
| **常见踩坑** | **多实例重复执行** 无分布式锁/租约；**cron 写错时区**；任务失败 **静默**（需 listener / 日志指标）。 |

---

## 本章实验室（约 40～60 分钟）

**环境**：单 Redis；`registerWorkers` **至少 1**；任务类 **Serializable**。

### 步骤

1. `schedule(new HeartbeatTask(), 5, TimeUnit.SECONDS)`，确认 **约 5s 后** 任务日志打印 **一次**。  
2. `scheduleAtFixedRate`，间隔 **10s**，运行 **2～3 个周期**，记录 **实际间隔**（与系统负载、worker 数关系）。  
3. 使用 `CronSchedule` 设 **下一分钟整分** 触发一次，对照 **本机时区与 JVM 默认时区** 是否一致（错时区时记录 **偏移现象**）。  
4. （可选）**两实例** 各 `registerWorkers`，同一 cron 任务，观察 **是否双跑**；若双跑，写出 **业务幂等** 或 **选主** 方案。

### 验证标准

- 至少 **一种** 周期调度与 **一种** 延迟调度 **按预期触发**。  
- 能回答：**与 Spring `@Scheduled` 相比，何时需要 Redisson 调度**。

### 记录建议

- **misfire、多实例** 两条写入 **上线 Runbook**。

**上一篇**：[第十一章（分篇二）RExecutorService](26-RExecutorService.md)｜**下一篇**：[第十一章（分篇四）Live Object](28-LiveObject.md)｜**下一章**：[第十二章导览](29-Spring生态集成.md)
