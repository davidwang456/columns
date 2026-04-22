# 第06章：SimpleTrigger：间隔、重复、结束策略

> **篇别**：基础篇  
> **建议篇幅**：3000–5000 字（含对话与代码）  
> **结构约束**：对齐 [专栏模板](../template.md) 四段式。

## 示例锚点

| 类型 | 路径 |
| --- | --- |
| example2 | [SimpleTriggerExample.java](../../examples/src/main/java/org/quartz/examples/example2/SimpleTriggerExample.java) |
| example2 | [SimpleJob.java](../../examples/src/main/java/org/quartz/examples/example2/SimpleJob.java) |

## 1 项目背景（约 500 字）

### 业务场景

订单履约系统需要 **每 30 秒轮询第三方物流接口** 拉取轨迹，直到签收或超时；同时要求 **最多重试 40 次** 后自动停止，避免无限空转。该需求本质是 **固定间隔 + 有限重复次数 + 可选结束时间**——`SimpleTrigger`（配合 `SimpleScheduleBuilder`）是最自然的表达。若误用 Cron，会出现「间隔不是严格 30 秒 wall clock」等理解偏差。

### 痛点放大

- **repeatCount 语义**：`0` 表示执行一次还是零次？`10` 是「总共 11 次」还是「额外 10 次」？
- **与 fixedRate 混淆**：业务需要「上次执行结束后间隔 T」时，SimpleTrigger 仍是 **调度侧固定节拍**，不等价于 `scheduleWithFixedDelay`。
- **线程执行时间大于间隔**：触发点堆积，进入 **misfire** 领域（第14章）。

## 2 项目设计（约 1200 字）

**角色**：小胖 · 小白 · 大师

---

**小胖**：`repeatCount` 听起来像「再跑几次」，那设 10 到底是几次？

**小白**：如果一次 Job 执行了 25 秒，间隔 10 秒，下一次是「严格每 10 秒排一次」还是「跑完再等 10 秒」？

**大师**：在 Quartz 的 `SimpleTrigger` 里，**`repeatCount` 表示「额外重复次数」**——`0` 表示 **总共 1 次**；`10` 表示 **1 + 10 = 11 次**。节拍是 **按时间表触发**，不是「上一次结束后再等」；后者要换模型或业务自行防抖。

**技术映射**：**`SimpleScheduleBuilder.withIntervalInXxx(...).withRepeatCount(n)`**。

---

**小胖**：那我要「永远每 5 分钟跑一次」怎么写？

**小白**：`repeatForever` 和 `endAt` 能一起用吗？谁先谁后？

**大师**：`repeatForever()` 像 **无限循环的节拍器**；`endAt` 像 **到点关电源**——组合使用时，**先到达的终止条件胜出**（以 Quartz 计算为准）。生产上建议 **永远带业务或时间窗上限**，否则发布/配置错误会刷爆下游。

**技术映射**：**`repeatForever()` + `TriggerBuilder.endAt`**。

---

**小胖**：example2 里同一个 job 又绑了 `group2` 的 trigger，这是啥魔法？

**小白**：第二次 `scheduleJob(trigger)` 和第一次 `scheduleJob(job, trigger)` 行为差在哪？

**大师**：第一次是 **连人带班表一起入职**；第二次是 **给已在职的人再加一张班表**——API 路径不同，但本质都是 **Trigger 指向同一 `JobKey`**。注意 **TriggerKey 必须唯一**，所以第二个 trigger 用了 `group2`。

**技术映射**：**`scheduleJob(Trigger trigger)` + `forJob(JobKey)`**。

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

`./gradlew :examples:compileJava`；重点打开 [SimpleTriggerExample.java](../../examples/src/main/java/org/quartz/examples/example2/SimpleTriggerExample.java)。

### 分步实现

**步骤 1：目标** —— 单次与固定间隔。

```java
Date start = DateBuilder.nextGivenSecondDate(null, 15);

// 仅一次：repeatCount 默认 0
SimpleTrigger once = (SimpleTrigger) newTrigger()
    .withIdentity("once", "demo")
    .startAt(start)
    .build();

// 共 11 次：间隔 10 秒
SimpleTrigger many = newTrigger()
    .withIdentity("many", "demo")
    .startAt(start)
    .withSchedule(simpleSchedule()
        .withIntervalInSeconds(10)
        .withRepeatCount(10))
    .build();
```

**验证**：日志中 `repeat: 10 times` 对应 **额外 10 次**（与 example 输出一致）。

**步骤 2：目标** —— 同一 Job 多 Trigger（摘录 example2 思路）。

```java
JobDetail job = newJob(SimpleJob.class).withIdentity("job3", "group1").build();
Trigger t3 = newTrigger().withIdentity("trigger3", "group1").startAt(start)
    .withSchedule(simpleSchedule().withIntervalInSeconds(10).withRepeatCount(10))
    .build();
sched.scheduleJob(job, t3);

Trigger t3b = newTrigger().withIdentity("trigger3", "group2").startAt(start)
    .withSchedule(simpleSchedule().withIntervalInSeconds(10).withRepeatCount(2))
    .forJob(job)
    .build();
sched.scheduleJob(t3b);
```

**验证**：`SchedulerMetaData` 中触发次数增加；两 Trigger 独立计数。

**步骤 3：目标** —— `futureDate` 辅助构造起始时间（见 example2 中 `DateBuilder.futureDate` 用法）。

### 可能踩坑

| 坑 | 解决 |
| --- | --- |
| 认为 repeatCount=「总次数」 | 查阅 API：额外重复 |
| 间隔小于执行时长 | 调线程池、或 `@DisallowConcurrentExecution`、或调 misfire（第14章） |
| 未设置 startTime | 立即进入可触发窗口，可能不符合预期 |

### 完整代码清单

- [SimpleTriggerExample.java](../../examples/src/main/java/org/quartz/examples/example2/SimpleTriggerExample.java)

### 测试验证

JUnit：`withRepeatCount(2)` + 短间隔，断言 Job 执行总次数（在 Job 内原子递增并暴露给测试）。

## 4 项目总结（约 500–800 字）

### 优点与缺点（对比同类技术）

| 维度 | SimpleTrigger | CronTrigger | fixedRate |
| --- | --- | --- | --- |
| 固定间隔 | 强 | 需拼凑 | 强 |
| 日历语义 | 弱 | 强 | 弱 |
| 有限次重复 | 原生 | 需计数 | 需自建 |

### 适用 / 不适用场景

- **适用**：轮询、心跳、重试窗口明确的同步拉取。
- **不适用**：「每工作日 9 点」类日历语义（用 Cron 或 CalendarInterval）。

### 注意事项

- **时区**：`Date` 基于 JVM 默认时区；统一用 `withSchedule` + 显式时区配置（第07章）。
- **持久化**：JDBC JobStore 下重复配置写入 DB（第21章）。

### 常见踩坑（生产案例）

1. **把 repeatCount 当总次数**：少跑或认为「多跑了一次」。
2. **无限 repeatForever 无 endAt**：大促后流量未回收，根因是配置治理缺失。
3. **双 Trigger 叠加**：以为只跑一条，实际两条 schedule。

#### 第05章思考题揭底

1. **同一 `JobDetail` 能否被多个 Trigger 驱动？典型用例？**  
   **答**：**可以**。典型用例：**同一任务不同频率**（如「每 5 分钟增量同步」+「每天凌晨全量校验」）、**临时促销窗口 Trigger** 与 **常驻心跳 Trigger** 并存（第05章架构）。

2. **`endTime` 到期后 `JobDetail` 是否自动删除？**  
   **答**：**默认不会**。`endTime` 作用在 **Trigger** 上，使其进入完成态；**`JobDetail` 仍保留** 除非 `non-durable` 且无其它 Trigger 时被清理（与 `JobBuilder.storeDurably` 等相关，详见官方文档与第04章）。

### 思考题（答案见下一章或 [答案索引](answers-index.md)）

1. `SimpleTrigger` 的 `repeatCount` 与 `repeatForever` 如何取舍？
2. 若间隔小于线程执行时间，会出现什么现象？

### 推广计划提示

- **测试**：边界用例 `repeatCount=0`、双 Trigger。
- **运维**：监控「实际触发间隔」与「业务处理耗时」比值。
- **开发**：下一章学习 `CronScheduleBuilder` 与 Cron 表达式。
