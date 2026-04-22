# 第07章：CronTrigger 与 Cron 表达式工程化（时区、DST）

> **篇别**：基础篇  
> **建议篇幅**：3000–5000 字（含对话与代码）  
> **结构约束**：对齐 [专栏模板](../template.md) 四段式。

## 示例锚点

| 类型 | 路径 |
| --- | --- |
| example3 | [CronTriggerExample.java](../../examples/src/main/java/org/quartz/examples/example3/CronTriggerExample.java) |
| example3 | [SimpleJob.java](../../examples/src/main/java/org/quartz/examples/example3/SimpleJob.java) |

## 1 项目背景（约 500 字）

### 业务场景

财务系统要求 **每个工作日 09:00（上海）** 生成应收日报；又要求 **每 20 秒** 做一次轻量对账探活。[CronTriggerExample.java](../../examples/src/main/java/org/quartz/examples/example3/CronTriggerExample.java) 展示了多种 Cron：`0/20 * * * * ?`、`15 0/2 * * * ?`、`0 0/2 8-17 * * ?` 等。工程化难点不在「写出字符串」，而在 **时区、夏令时（DST）、与业务日历的组合**——本章建立 **Cron 的可读性、可测试性与运行环境一致性** 规范。

### 痛点放大

- **服务器时区 ≠ 业务时区**：日志显示「9 点对」但用户侧错一小时。
- **DST 切换日**：出现「跳过一小时」或「重复一小时」的边缘触发。
- **Cron 可读性**：魔法字符串难以 code review。

## 2 项目设计（约 1200 字）

**角色**：小胖 · 小白 · 大师

---

**小胖**：Cron 不就是六个星号乱填吗？我复制粘贴网上的「每秒执行」最省事。

**小白**：Quartz Cron 和 Linux crontab 的字段顺序一样吗？`?` 和 `*` 在日与星期冲突时怎么用？

**大师**：Quartz 常用 **秒 分 时 日 月 周 [年]** 七段式（年可选）。**`?` 表示「不关心」**——用于「指定了日就不要指定周」这类互斥场景。把它想成 **排课表**：有些格子填「任意」，有些格子必须留白，否则课表自相矛盾。

**技术映射**：**`CronScheduleBuilder.cronSchedule("...")`**；**`?` 解决日/周互斥**。

---

**小胖**：业务说要「北京时间 9 点」，我写死 `0 0 9 * * ?` 够不够？

**小白**：JVM 默认 `Asia/Shanghai` 若被运维改成 UTC，会发生什么？应在 Trigger 上还是 Scheduler 上设时区？

**大师**：**时间语义必须显式化**。可以在 **`TriggerBuilder.withSchedule`** 使用带时区的 `CronScheduleBuilder`，或在 **Scheduler 层** 统一 `timeZone`（视版本与配置项而定）。原则是：**谁最贴近业务语义，谁持有权威时区**。仅依赖 JVM 默认时区，等于把「合同上的北京时间」交给机器本地设置去猜。

**技术映射**：**`CronScheduleBuilder.inTimeZone(TimeZone.getTimeZone("Asia/Shanghai"))`**（API 以版本文档为准）。

---

**小胖**：DST 跟我们国内有关系吗？

**小白**：全球化系统若跑在 `America/Los_Angeles`，Spring 与 Quartz 谁该背锅？

**大师**：国内通常无 DST，但 **全球化是常态**。DST 切换日要 **用集成测试扫一遍 Cron 边界**；责任划分上，**Quartz 负责按给定 TimeZone 计算下一次触发**，业务负责 **选对 ZoneId** 并在变更窗口监控 misfire。

**技术映射**：**`TimeZone` / `ZoneId` 一致性** + **第14章 misfire**。

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

运行并阅读 [CronTriggerExample.java](../../examples/src/main/java/org/quartz/examples/example3/CronTriggerExample.java)。

### 分步实现

**步骤 1：目标** —— 每 20 秒（与 example 一致）。

```java
CronTrigger trigger = newTrigger()
    .withIdentity("trigger1", "group1")
    .withSchedule(cronSchedule("0/20 * * * * ?"))
    .build();
```

**验证**：日志打印 `CronExpression` 与首次 `ft`。

**步骤 2：目标** —— 工作时段内每 2 分钟（8–17 点）。

```java
CronTrigger businessHours = newTrigger()
    .withIdentity("trigger3", "group1")
    .withSchedule(cronSchedule("0 0/2 8-17 * * ?"))
    .build();
```

**验证**：17 点之后不应再触发（观察日志窗口）。

**步骤 3：目标** —— 显式业务时区（示例代码，按项目 Quartz 版本调整方法名）。

```java
import java.util.TimeZone;

CronTrigger shanghaiDaily = newTrigger()
    .withIdentity("dailyCn", "finance")
    .withSchedule(
        cronSchedule("0 0 9 ? * MON-FRI")
            .inTimeZone(TimeZone.getTimeZone("Asia/Shanghai"))
    )
    .build();
```

**验证**：将 JVM 默认时区改为 `UTC`，触发仍在上海 9:00 语义下发生（以实际测试为准）。

### 可能踩坑

| 坑 | 解决 |
| --- | --- |
| 日与周同时 `*` | 使用 `?` 解除互斥 |
| 线上 UTC 本地 Asia | 显式 `inTimeZone` |
| 「L」「W」滥用 | 增加注释与单测 |

### 完整代码清单

- [CronTriggerExample.java](../../examples/src/main/java/org/quartz/examples/example3/CronTriggerExample.java)

### 测试验证

使用 **CronExpression 工具类**（若版本提供）打印未来 N 次触发时间，纳入单元测试 golden file。

## 4 项目总结（约 500–800 字）

### 优点与缺点（对比同类技术）

| 维度 | CronTrigger | SimpleTrigger | K8s schedule |
| --- | --- | --- | --- |
| 日历表达 | 强 | 弱 | 中 |
| 秒级字段 | 有 | 有 | 常见为分 |
| 可读性 | 中（靠规范） | 高 | 中 |

### 适用 / 不适用场景

- **适用**：工作日历、营业时间窗、复杂周期。
- **不适用**：仅固定间隔、用 SimpleTrigger 更清晰。

### 注意事项

- **版本**：Cron 方言以 Quartz 文档为准。
- **监控**：把 **resolved TimeZone** 打到日志首行。

### 常见踩坑（生产案例）

1. **夏令时重复执行**：根因是未在切换日评审 Cron。
2. **容器镜像默认 UTC**：根因是未在 Helm values 声明时区。
3. **复制「每秒」Cron 到生产**：根因是缺少 code review 钩子。

#### 第06章思考题揭底

1. **`repeatCount` 与 `repeatForever`**  
   **答**：**`repeatCount`** 用于 **有限次** 调度（含「只跑一次」=0）；**`repeatForever`** 用于 **无上限节拍**，通常应配合 **`endAt` 或业务侧开关** 防止失控。二者 **语义互斥**：forever 模式不使用 repeatCount。

2. **间隔小于执行时间**  
   **答**：触发点仍会按时间表 **到期排队**，线程池可能出现 **任务堆积**；若不允许并发，则 **后续触发 misfire 或阻塞**（视 `DisallowConcurrent` 与 misfire 策略）。表现为 **延迟越来越大** 或 **misfire 日志**（第14章）。

### 思考题（答案见下一章或 [答案索引](answers-index.md)）

1. 写出一个「每工作日 9:00」的 Cron，并说明时区应配置在哪里。
2. 夏令时切换日 Cron 可能踩坑，如何避免？

### 推广计划提示

- **测试**：时区矩阵单测；DST 边界用例（若业务涉及）。
- **运维**：镜像与 Cron 统一时区声明。
- **开发**：下一章对比 `CalendarIntervalTrigger` / `DailyTimeIntervalTrigger`。
