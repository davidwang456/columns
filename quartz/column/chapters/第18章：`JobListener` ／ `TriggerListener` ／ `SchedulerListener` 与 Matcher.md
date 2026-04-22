# 第18章：`JobListener` / `TriggerListener` / `SchedulerListener` 与 Matcher

> **篇别**：中级篇  
> **建议篇幅**：3000–5000 字（含对话与代码）  
> **结构约束**：对齐 [专栏模板](../template.md) 四段式。

## 示例锚点

| 类型 | 路径 |
| --- | --- |
| example9 | [ListenerExample.java](../../examples/src/main/java/org/quartz/examples/example9/ListenerExample.java) |
| example9 | [Job1Listener.java](../../examples/src/main/java/org/quartz/examples/example9/Job1Listener.java) |

## 1 项目背景（约 500 字）

### 业务场景

审计要求：**每次关键任务执行前后打点**，失败写入 SIEM；运维希望 **Scheduler 启停事件** 进入集中日志。团队不想把横切逻辑复制到几十个 Job，于是引入 **Listener + Matcher**：只对 **`JobKey` 匹配某模式** 的任务安装 `JobListener`，实现 **可组合、可局部生效的观测面**。

### 痛点放大

- **全局 Listener 噪声**：所有 Job 打日志导致成本爆炸。
- **顺序误解**：Listener 与 Job 事务谁先谁后。
- **异常传播**：Listener 内抛异常可能影响调度（需谨慎，查文档）。

## 2 项目设计（约 1200 字）

**角色**：小胖 · 小白 · 大师

---

**小胖**：Listener 不就是 AOP 吗？我 Spring 包一层不行吗？

**小白**：三类 Listener 分别挂在什么生命周期上？能拿到 `JobExecutionContext` 吗？

**大师**：**JobListener** 盯 **「工人干活前后」**；**TriggerListener** 盯 **「闹钟响不响、为什么没响」**；**SchedulerListener** 盯 **「整个调度台开关机、Job 增删」**。`JobListener` 的 `jobWasExecuted` 能拿到 **上下文**；TriggerListener 更贴近 **触发器 veto** 等高级语义（以文档为准）。

**技术映射**：**`ListenerManager#addJobListener(listener, Matcher)`**。

---

**小胖**：`KeyMatcher.keyEquals` 和 `GroupMatcher` 啥区别？

**小白**：多个 Matcher 能 OR 吗？

**大师**：`KeyMatcher` 像 **「精确点名」**；`GroupMatcher` 像 **「整组点名」**。Quartz 提供 **`OrMatcher`/`AndMatcher`**（包 `org.quartz.impl.matchers`）组合条件，实现 **「财务组所有 Job」** 一类策略。

**技术映射**：**`Matcher<JobKey>` 组合**。

---

**小胖**：Listener 里能 `scheduleJob` 再挂一个任务吗？

**小白**：会不会死锁或递归？

**大师**：技术上 **可以链式触发**（example9 的 `Job1Listener` 思路），但要 **避免无限链** 与 **重入锁**：像 **多米诺骨牌**——设计好 **最大链长** 与 **失败断路**。

**技术映射**：**JobChainingJobListener**（第18章专用方案）。

---

**小胖**：这跟食堂打饭有啥关系？我就想把任务跑起来。

**小白**：那 **谁来背锅**：触发没发生、发生了两次、还是延迟太久？指标口径先定死。

**大师**：把 **Scheduler 当「编排台」**：Job 是工序，Trigger 是节拍，Listener 是质检；节拍错了，工序再快也白搭。

**技术映射**：**可观测性口径 + Job／Trigger 职责边界**。

---

**小胖**：配置一多我就晕，`quartz.properties` 到底哪些能碰？

**小白**：**线程数、misfireThreshold、JobStore 类型** 改了会不会让 **同一套代码** 在预发与生产行为不一致？

**大师**：做一张 **「配置变更矩阵」**：改一项就写清 **影响面、回滚方式、验证命令**；RAM 与 JDBC 不要混着试。

**技术映射**：**显式配置治理 + 环境一致性**。

---

**小胖**：我本地跑得飞起，一上集群就「偶尔不跑」。

**小白**：**时钟漂移、数据库时间、JVM 默认时区** 三者不一致时，**nextFireTime** 你怎么解释给业务？

**大师**：把 **时区写进契约**：服务器、Cron、业务日历 **同一基准**；日志里同时打 **UTC 与业务时区**。

**技术映射**：**时区／DST 与触发语义**。

---

**小胖**：Trigger 优先级是不是数字越大越牛？

**小白**：**饥饿**怎么办？低优先级永远等不到的话，SLA 谁负责？

**大师**：优先级是 **「同窗口抢锁」** 的 tie-breaker，不是万能插队票；该 **拆分队列** 的别硬挤一个 Scheduler。

**技术映射**：**Trigger 优先级与吞吐隔离**。

---

**小胖**：misfire 不就是晚了吗，晚跑一下不行？

**小白**：**合并、丢弃、立即补偿** 三种策略对 **资金类任务** 分别是啥后果？

**大师**：把 **业务幂等键** 与 **misfireInstruction** 绑在一起评审；没有幂等就别选「立刻全部补上」。

**技术映射**：**misfire 策略与业务一致性**。

---

**小胖**：`JobDataMap` 里塞个大 JSON 爽不爽？

**小白**：**序列化成本、版本升级、跨语言** 谁来买单？失败重试会不会把 **半截状态** 写回去？

**大师**：**小键值 + 外置大对象**；必须进 Map 的，**版本字段** 与 **兼容读** 写进规范。

**技术映射**：**JobDataMap 体积与演进策略**。

---

**小胖**：`@DisallowConcurrentExecution` 一贴我就安心了。

**小白**：**同 JobKey 串行** 会不会把 **补偿触发** 堵成长队？线程池够吗？

**大师**：先画 **并发模型草图**：哪些 Job 必须串行、哪些只是 **资源互斥**（应改用锁或分片）。

**技术映射**：**并发注解与队列时延**。

---

**小胖**：关机我直接拔电源，反正有下次触发。

**小白**：**在途 Job** 写了一半的外部副作用怎么算？**at-least-once** 下会不会双写？

**大师**：发布路径默认 **`shutdown(true)` + 超时**；`kill -9` 只能进 **混沌演练**，不进 **常规 Runbook**。

**技术映射**：**优雅停机与副作用幂等**。

---

**小胖**：Listener 里写业务逻辑最快了。

**小白**：Listener 异常会不会 **吞掉主流程** 或 **拖慢线程**？顺序保证吗？

**大师**：Listener 只做 **旁路观测与轻量编排**；重逻辑回 **Job** 或 **下游消息**。

**技术映射**：**Listener 边界与失败隔离**。

---

**小胖**：JDBC JobStore 不就是多几张表吗？

**小白**：**行锁、delegate、方言、索引** 哪个没对齐会出现 **幽灵触发** 或 **长时间抢锁**？

**大师**：把 **DB 监控**（慢查询、锁等待）与 **Quartz 线程栈** 对齐看；调参前先 **确认隔离级别与连接池**。

**技术映射**：**持久化 JobStore 与数据库协同**。

---

**小胖**：集群一开我就加节点，TPS 一定涨吧？

**小白**：**抢锁成本、心跳、instanceId** 乱配时，会不会 **越加越慢**？

**大师**：用 **压测曲线** 证明拐点；集群收益来自 **HA 与横向扩展边界**，不是魔法按钮。

**技术映射**：**集群伸缩与锁竞争**。

---

**小胖**：我想自定义 ThreadPool 秀一把。

**小白**：线程工厂、拒绝策略、上下文传递（MDC）**漏一项** 会出现啥线上症状？

**大师**：自定义可以，但要 **对齐 SPI 契约**与 **关闭语义**；否则 **泄漏线程** 比默认池更难查。

**技术映射**：**ThreadPool SPI 与生命周期**。

---

**小胖**：这跟食堂打饭有啥关系？我就想把任务跑起来。

**小白**：那 **谁来背锅**：触发没发生、发生了两次、还是延迟太久？指标口径先定死。

**大师**：把 **Scheduler 当「编排台」**：Job 是工序，Trigger 是节拍，Listener 是质检；节拍错了，工序再快也白搭。

**技术映射**：**可观测性口径 + Job／Trigger 职责边界**。
## 3 项目实战（约 1500–2000 字）

### 环境准备

阅读 [ListenerExample.java](../../examples/src/main/java/org/quartz/examples/example9/ListenerExample.java)：

```java
JobListener listener = new Job1Listener();
Matcher<JobKey> matcher = KeyMatcher.keyEquals(job.getKey());
sched.getListenerManager().addJobListener(listener, matcher);
```

### 分步实现

**步骤 1：目标** —— 运行 example9，观察 **job1 完成后** listener 触发的二次调度行为（读 `Job1Listener`）。

**步骤 2：目标** —— 自建 `TriggerListener` 打印 `triggerFired` / `vetoJobExecution`。

**步骤 3：目标** —— 注册 `SchedulerListener` 记录 `schedulerStarted` / `schedulerShutdown`。

### 可能踩坑

| 坑 | 解决 |
| --- | --- |
| Listener 过重拖慢调度 | 异步投递审计 |
| Listener 抛异常 | try-catch 隔离 |
| Matcher 过宽 | 从 KeyEquals 起步 |

### 完整代码清单

- [ListenerExample.java](../../examples/src/main/java/org/quartz/examples/example9/ListenerExample.java)
- [Job1Listener.java](../../examples/src/main/java/org/quartz/examples/example9/Job1Listener.java)

### 测试验证

单测：mock Scheduler 或使用 RAMScheduler，断言 listener 回调次数。

## 4 项目总结（约 500–800 字）

### 优点与缺点（对比同类技术）

| 维度 | Quartz Listener | Spring AOP | Micrometer Timer |
| --- | --- | --- | --- |
| 调度语义贴近度 | 高 | 中 | 中 |
| 与 Job 解耦 | 高 | 中 | 高 |

### 适用 / 不适用场景

- **适用**：审计、动态链式触发、Trigger 级 veto。
- **不适用**：极简单日志（直接 Job 内打亦可）。

### 注意事项

- **性能**：热点 Job 慎用同步重逻辑 Listener。
- **安全**：Listener 内访问外部系统需鉴权。

### 常见踩坑（生产案例）

1. **Listener 死锁**：根因是回调里同步 schedule。
2. **日志风暴**：根因是 Matcher 过宽。
3. **异常吞没**：根因是未在 Listener 打 error。

#### 第17章思考题揭底

1. **`refireImmediately` vs 业务快速重试**  
   **答**：**refireImmediately** 由 **调度器立即再次调度同一 Trigger 语义下的执行**，占用 **Quartz worker** 并与 **misfire/unschedule 标志** 交互；**业务 while 重试** 仍在 **同一次 execute 调用栈** 内，**不改变调度器看到的成功/失败边界**，对 **线程占用与指标** 的影响不同。

2. **吞异常不抛出**  
   **答**：调度器通常认为 **本次执行成功完成**；**Trigger 继续按节拍推进**；你可能 **看不到 `JobExecutionException` 路径上的 refire/unschedule**；监控出现 **「绿灯但业务没做」**。

### 思考题（答案见下一章或 [答案索引](answers-index.md)）

1. 三类 Listener 的触发顺序与事务边界关系（概念上）？
2. 全局 Listener 与局部 Matcher 同时存在时如何合并？

### 推广计划提示

- **测试**：Listener 异常隔离用例。
- **运维**：审计日志索引字段设计。
- **开发**：下一章用 `JobChainingJobListener` 规范链式任务。
