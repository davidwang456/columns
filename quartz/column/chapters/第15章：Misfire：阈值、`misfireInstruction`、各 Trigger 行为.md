# 第15章：Misfire：阈值、`misfireInstruction`、各 Trigger 行为

> **篇别**：中级篇  
> **建议篇幅**：3000–5000 字（含对话与代码）  
> **结构约束**：对齐 [专栏模板](../template.md) 四段式。

## 示例锚点

| 类型 | 路径 |
| --- | --- |
| example5 | [MisfireExample.java](../../examples/src/main/java/org/quartz/examples/example5/MisfireExample.java) |
| example5 | [StatefulDumbJob.java](../../examples/src/main/java/org/quartz/examples/example5/StatefulDumbJob.java) |
| 配置 | [quartz_misfire.properties](../../examples/src/main/resources/org/quartz/examples/example5/quartz_misfire.properties) |

## 1 项目背景（约 500 字）

### 业务场景

大促期间 **线程池只有 2 个工作线程**（见 [quartz_misfire.properties](../../examples/src/main/resources/org/quartz/examples/example5/quartz_misfire.properties)），而某批处理 Job **故意睡眠 10 秒**，Trigger 却要求 **每 3 秒触发一次**——必然出现 **「想触发却来不及」** 的情况。example5 文档说明：若 **`misfireThreshold` 小于约 7 秒**，就会进入 **misfire 检测**；两个 Trigger 分别配置 **「NOW_WITH_EXISTING_COUNT」** 与 **默认 smart policy**，日志对比 **立即补跑 vs 跳到下一 fire time**。

### 痛点放大

- **misfire 与「线程池满」不同**：后者是资源等待，前者是 **时间语义上的「错过预定触发点」**。
- **instruction 选错**：可能 **瞬间打爆下游**（fire now）或 **长期跳过**（smart advance）。
- **与有状态 Job 叠加**：回写 `JobDataMap` 与重入次数需幂等（第15章）。

## 2 项目设计（约 1200 字）

**角色**：小胖 · 小白 · 大师

---

**小胖**：线程忙不过来，为啥不叫「排队」而叫 misfire？

**小白**：`misfireThreshold` 设成 1 秒和 60 秒，对业务分别意味着什么？

**大师**：可以把 **预定触发时刻** 想成 **公交车时刻表**。你晚到站台 **1 分钟以内**，调度员还觉得你可能赶上（未 misfire）；**超过阈值**，系统认定你 **彻底错过这班车**，按 **「立刻加开一班」或「等下一班」** 两种政策处理——这就是 **instruction**。

**技术映射**：**`jobStore.misfireThreshold` + `Trigger` 的 misfireInstruction**。

---

**小胖**：`MISFIRE_INSTRUCTION_FIRE_NOW` 听起来很爽？

**小白**：下游 API 有 rate limit，立刻补跑会不会雪崩？

**大师**：**立刻补跑**适合 **可丢失中间节拍、但要尽快追上状态** 的场景；若下游脆弱，应 **退避 + 限速** 或选 **smart policy** 跳到 **下一合法 fire time**，并接受 **中间窗口无采样**。

**技术映射**：**业务可承受的数据空洞 vs 追实时** 的权衡。

---

**小胖**：Cron 和 Simple 的 misfire 选项一样吗？

**小白**：Calendar 排除日回来后第一次触发算不算 misfire？

**大师**：不同 Trigger 类型 **提供的 instruction 集合不同**；日历变更导致 **nextFireTime 重算** 与 **执行超时 misfire** 是两条线。排障时要先分：**是资源跑不过来** 还是 **日历/表达式变更**。

**技术映射**：查阅 **`SimpleTrigger`/`CronTrigger` 的 MISFIRE_INSTRUCTION_* 常量**。

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
## 3 项目实战（约 1500–2000 字）

### 环境准备

```bash
./gradlew :examples:compileJava
```

运行 `org.quartz.examples.example5.MisfireExample`。

### 分步实现

**步骤 1：目标** —— 确认 **低 threshold + 少线程** 配置。

阅读 `quartz_misfire.properties`：`threadCount=2`，`misfireThreshold=1000`。

**步骤 2：目标** —— 对照 [MisfireExample.java](../../examples/src/main/java/org/quartz/examples/example5/MisfireExample.java) 中 **trigger2**：

```java
.withSchedule(simpleSchedule()
    .withIntervalInSeconds(3)
    .repeatForever()
    .withMisfireHandlingInstructionNowWithExistingCount())
```

**验证**：日志中观察 **statefulJob2** 在 misfire 后 **更激进** 的触发行为（与注释一致）。

**步骤 3：目标** —— 将 `EXECUTION_DELAY` 从 10000 改为 2000，观察 misfire **消失或显著减少**。

### 可能踩坑

| 坑 | 解决 |
| --- | --- |
| 生产误用 example5 配置 | 分离 lab 与 prod properties |
| 认为 threshold 越大越好 | 理解「晚认定」副作用 |
| 忽略 stateful 语义 | 读 `StatefulDumbJob` |

### 完整代码清单

- [MisfireExample.java](../../examples/src/main/java/org/quartz/examples/example5/MisfireExample.java)
- [StatefulDumbJob.java](../../examples/src/main/java/org/quartz/examples/example5/StatefulDumbJob.java)

### 测试验证

记录 **每次 `execute` 进入时间** 与 **计划 fire time** 的差值分布，生成简单直方图（测试报告）。

## 4 项目总结（约 500–800 字）

### 优点与缺点（对比同类技术）

| 维度 | Quartz misfire | Executor 延迟执行 | 自建重试 |
| --- | --- | --- | --- |
| 语义标准化 | 高 | 低 | 中 |
| 可控策略 | 丰富 | 无 | 自定 |

### 适用 / 不适用场景

- **适用**：节拍敏感、需明确「错过怎么办」。
- **不适用**：完全由 MQ 消费速率决定执行（可不用定时 Trigger）。

### 注意事项

- **监控**：misfire 次数指标（自建或插件）。
- **版本**：instruction 名称随版本查阅。

### 常见踩坑（生产案例）

1. **NOW 策略打挂支付网关**：根因是未评估补跑风暴。
2. **threshold 过大导致「以为没 misfire」**：根因是监控盲区。
3. **与集群时钟漂移叠加**：根因是 NTP 未校（第40章）。

#### 第14章思考题揭底

1. **维护窗跨天与 Cron 日界**  
   **答**：以 Runbook 中的 **业务 timezone** 为单一真相；Calendar 与 Cron 共用 **`quartz.scheduler.timeZone`**；跨天维护窗用 **[start, end)** 半开区间建模；验收用 **固定 Instant 列表** 断言 `nextFireTime` 与日志 **fireTime** 一致。

2. **同一 JobKey 双 Trigger + `@DisallowConcurrentExecution`**  
   **答**：第二次触发可能 **阻塞等待** 或触发 **misfire**（视线程池余量与 Store 语义）；用 **TriggerListener** 观察 **refireCount** 与等待；业务写路径需 **幂等键** 防双写。

### 思考题（答案见下一章或 [答案索引](answers-index.md)）

1. 什么是 misfire？它与「线程池满」有何不同？
2. `MISFIRE_INSTRUCTION_FIRE_NOW` 可能带来什么业务副作用？

### 推广计划提示

- **测试**：为关键 Trigger 建立 misfire 场景矩阵。
- **运维**：监控 threadCount 与队列延迟。
- **开发**：下一章深挖有状态 Job 与 misfire 联动。
