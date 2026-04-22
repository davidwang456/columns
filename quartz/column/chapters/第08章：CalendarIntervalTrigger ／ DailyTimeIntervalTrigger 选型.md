# 第08章：CalendarIntervalTrigger / DailyTimeIntervalTrigger 选型

> **篇别**：基础篇  
> **建议篇幅**：3000–5000 字（含对话与代码）  
> **结构约束**：对齐 [专栏模板](../template.md) 四段式。

## 示例锚点

| 类型 | 路径 |
| --- | --- |
| 源码 | [CalendarIntervalTriggerImpl.java](../../quartz/src/main/java/org/quartz/impl/triggers/CalendarIntervalTriggerImpl.java) |
| 源码 | [DailyTimeIntervalTriggerImpl.java](../../quartz/src/main/java/org/quartz/impl/triggers/DailyTimeIntervalTriggerImpl.java) |

## 1 项目背景（约 500 字）

### 业务场景

SaaS 计费希望 **「每 3 个自然月」** 生成一次账单，而不是「每 90 天」——二者在月末/闰年附近行为不同；又如 **「每个工作日上午 10 点与下午 3 点各执行一次」**——用 Cron 要写多条或复杂表达式。`CalendarIntervalTrigger` 与 `DailyTimeIntervalTrigger` 提供 **以日历字段为步进** 的语义，弥补 **Simple 的「纯毫秒间隔」** 与 **Cron 的「字符串难维护」** 之间的空白。

### 痛点放大

- **「每 N 个月最后一天」**：Cron 难表达；CalendarInterval 更接近业务语言。
- **「每天多个时间段」**：拆多个 Cron 易漂移；DailyTimeInterval 可封装日内窗口。
- **持久化**：两类 Trigger 在 JDBC 中有对应 **PersistenceDelegate**（见 `jdbcjobstore` 包）。

## 2 项目设计（约 1200 字）

**角色**：小胖 · 小白 · 大师

---

**小胖**：每 3 个月跟「每 90 天」不就是乘除法吗？

**小白**：如果起始日是 1 月 31 日，加 3 个「月」该怎么落？会不会跳到 4 月？

**大师**：**日历步进** 要像 **信用卡账单周期**——按「账单日」滚动，而不是简单数 90 天。Java 日期 API 里也有类似「加月份」的边界规则；Quartz 的 CalendarInterval 正是把这类 **「按字段进位」** 固化在 Trigger 语义里，减少业务手算。

**技术映射**：**`CalendarIntervalScheduleBuilder`**。

---

**小胖**：`DailyTimeInterval` 是不是就是「每天八点到五点每隔十分钟」？

**小白**：它和 `cronSchedule("0 */10 8-17 * * ?")` 有什么维护成本差异？

**大师**：可以把 DailyTimeInterval 想成 **「在一天里画时间段 + 步长」的表单**；Cron 想成 **「一行正则」**。团队若强依赖可视化配置、且日内多段规则多，DailyTimeInterval **更结构化**；若已是 Cron 专家且规则稳定，Cron **更紧凑**。

**技术映射**：**`DailyTimeIntervalScheduleBuilder`**。

---

**小胖**：这两种 Trigger 在集群 JDBC 下有没有坑？

**小白**：升级 Quartz 小版本时，Trigger JSON/ blob 列会不会不兼容？

**大师**：持久化 Trigger 依赖 **Delegate** 序列化字段；升级要做 **回归测试：读出旧库触发器并计算 nextFireTime**。这是所有「非 Simple/Cron」触发器都要走的检查清单。

**技术映射**：**`TriggerPersistenceDelegate`**（第21、32章关联）。

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
## 3 项目实战（约 1500–2000 字）

### 环境准备

在自建 demo 模块或 `examples` 拷贝类中实验（勿破坏上游 example 结构亦可）。

### 分步实现

**步骤 1：目标** —— 每 3 个月（示意 API，方法名以当前 Quartz 为准）。

```java
import static org.quartz.CalendarIntervalScheduleBuilder.calendarIntervalSchedule;
import static org.quartz.JobBuilder.newJob;
import static org.quartz.TriggerBuilder.newTrigger;

Trigger billing = newTrigger()
    .withIdentity("quarterBilling", "saas")
    .startNow()
    .withSchedule(calendarIntervalSchedule()
        .withIntervalInMonths(3)
        .preserveHourOfDayAcrossDaylightSavings(true) // 视版本支持情况
        .skipDayIfNoHourExists(false))
    .build();
```

**验证**：打印 `getNextFireTime()` 序列，检查跨月边界。

**步骤 2：目标** —— 每天 10:00–10:30 每 5 分钟（示意）。

```java
import static org.quartz.DailyTimeIntervalScheduleBuilder.dailyTimeIntervalSchedule;

Trigger intraDay = newTrigger()
    .withIdentity("morningBurst", "saas")
    .withSchedule(dailyTimeIntervalSchedule()
        .startingDailyAt(org.quartz.TimeOfDay.hourAndMinuteOfDay(10, 0))
        .endingDailyAt(org.quartz.TimeOfDay.hourAndMinuteOfDay(10, 30))
        .withIntervalInMinutes(5))
    .build();
```

**验证**：仅 10:00–10:30 窗口内产生 fire time。

**步骤 3：目标** —— 与 `CronTrigger` 对照打印未来 20 次触发，比较差异。

### 可能踩坑

| 坑 | 解决 |
| --- | --- |
| 混淆月间隔与天间隔 | 从业务语言倒推字段 |
| DST 与 preserveHour | 阅读 API 文档与单测 |
| JDBC delegate 缺失 | 检查方言与表结构版本 |

### 完整代码清单

- [CalendarIntervalTriggerImpl.java](../../quartz/src/main/java/org/quartz/impl/triggers/CalendarIntervalTriggerImpl.java)
- [DailyTimeIntervalTriggerImpl.java](../../quartz/src/main/java/org/quartz/impl/triggers/DailyTimeIntervalTriggerImpl.java)

### 测试验证

JUnit：固定 `Clock` 或在测试中注入 `DateBuilder`，断言 `nextFireTime` 列表与 golden 文件一致。

## 4 项目总结（约 500–800 字）

### 优点与缺点（对比同类技术）

| 维度 | CalendarInterval / DailyTimeInterval | Cron | Simple |
| --- | --- | --- | --- |
| 业务语义对齐 | 高（按月/按日窗） | 中 | 低 |
| 学习成本 | 中 | 中-高 | 低 |
| 持久化复杂度 | 中（delegate） | 中 | 低 |

### 适用 / 不适用场景

- **适用**：账单周期、合规报送窗口、日内多峰。
- **不适用**：仅需纯秒级周期，用 Simple 更直观。

### 注意事项

- **API 演进**：以当前版本 Javadoc 为准。
- **与 HolidayCalendar 组合**：第13章。

### 常见踩坑（生产案例）

1. **月末加月跳变**：未做财务对账日评审。
2. **日内窗口跨夜**：结束时间小于开始时间的配置错误。
3. **升级后 trigger 读失败**：未跑持久化回归。

#### 第07章思考题揭底

1. **「每工作日 9:00」Cron 与时区**  
   **答**：示例：`0 0 9 ? * MON-FRI`（Quartz 七段式，年与秒字段按需要调整）。**时区** 应配置在 **`CronScheduleBuilder.inTimeZone(ZoneId/TimeZone)`** 或 Scheduler 级统一配置，使 **Cron 的「9」与业务城市对齐**，而非依赖服务器默认时区。

2. **夏令时避免踩坑**  
   **答**：**显式选 ZoneId**；对关键任务编写 **切换日集成测试**；必要时使用 API 提供的 **preserve/skip** 策略；在日志记录 **实际触发 instant + zone**；与业务确认「丢失一小时/重复一小时」可接受策略。

### 思考题（答案见下一章或 [答案索引](answers-index.md)）

1. `CalendarIntervalTrigger` 与 `CronTrigger` 在「按月滚动」场景各有什么优劣？
2. 什么情况下更推荐 `DailyTimeIntervalTrigger`？

### 推广计划提示

- **测试**：golden `nextFireTime`。
- **运维**：升级检查表增加「非 Cron 触发器」。
- **开发**：下一章结合 example4 学习 `JobDataMap`。
