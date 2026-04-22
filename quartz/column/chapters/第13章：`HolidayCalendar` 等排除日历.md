# 第13章：`HolidayCalendar` 等排除日历

> **篇别**：基础篇  
> **建议篇幅**：3000–5000 字（含对话与代码）  
> **结构约束**：对齐 [专栏模板](../template.md) 四段式。

## 示例锚点

| 类型 | 路径 |
| --- | --- |
| example8 | [CalendarExample.java](../../examples/src/main/java/org/quartz/examples/example8/CalendarExample.java) |
| example8 | [SimpleJob.java](../../examples/src/main/java/org/quartz/examples/example8/SimpleJob.java) |

## 1 项目背景（约 500 字）

### 业务场景

批处理需 **每个工作日 02:00 跑**，但 **法定节假日与调休工作日** 不能简单用「周一到周五」Cron 表达。公司有一份 **节假日 API**，运维希望 **不每次改 Cron**，而是维护一份 **排除日历** 绑定到多个 Trigger。Quartz 提供 **`org.quartz.Calendar` 体系**（如 `HolidayCalendar`），可与 Trigger **按名称关联**，实现 **「时间表达式 + 例外日」** 的组合。

### 痛点放大

- **纯 Cron**：调休导致「周六上班却不跑」或「周日休息却跑」难维护。
- **多套 Trigger**：春节、国庆规则不同，复制粘贴 Cron 易错。
- **时区**：节假日以 **行政日** 定义，与 UTC 午夜切割可能不一致。

## 2 项目设计（约 1200 字）

**角色**：小胖 · 小白 · 大师

---

**小胖**：节假日我直接在 Cron 里排除不行吗？

**小白**：`HolidayCalendar` 与 `AnnualCalendar`、`CronCalendar` 分工是什么？

**大师**：`HolidayCalendar` 像 **公司行政发的「不上班清单」**——上面列的日期，统统从排班里划掉。Cron 像 **默认周课表**；两者叠在一起，**课表先排，行政清单再划叉**。`AnnualCalendar` 更像 **每年固定纪念日**；`CronCalendar` 则是 **再用一段 Cron 描述禁止窗口**。

**技术映射**：**`scheduler.addCalendar(String name, Calendar cal, boolean replace, boolean updateTriggers)`** + **`TriggerBuilder.modifiedByCalendar`**。

---

**小胖**：多个 Trigger 共享一份「春节日历」会不会互相影响？

**小白**：更新 calendar 时 `updateTriggers` 传 true 会触发什么？

**大师**：共享就像 **共用一份放假通知**——改一次，所有引用它的 Trigger 都要 **重算 nextFireTime**（若 `updateTriggers=true`）。好处是 **一处维护**；风险是 **变更窗口要评审影响面**。

**技术映射**：**日历对象复用与触发器重算**。

---

**小胖**：我们节假日数据在数据库，怎么灌进 Quartz？

**小白**：Calendar 能持久化吗？重启后还在不在？

**大师**：**JDBC JobStore** 可把 calendar 定义持久化（具体以版本与配置为准）；否则需在 **应用启动时重建 `HolidayCalendar` 并 addCalendar**。常见模式是 **DB 为准、启动同步到 Quartz**。

**技术映射**：**启动装载 + addCalendar**。

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

阅读 [CalendarExample.java](../../examples/src/main/java/org/quartz/examples/example8/CalendarExample.java)。

### 分步实现

**步骤 1：目标** —— 创建 `HolidayCalendar` 并排除一天。

```java
HolidayCalendar holidays = new HolidayCalendar();
Calendar excludeDay = Calendar.getInstance();
excludeDay.set(Calendar.MONTH, Calendar.MAY);
excludeDay.set(Calendar.DAY_OF_MONTH, 20);
holidays.addExcludedDate(excludeDay.getTime());

sched.addCalendar("holidays", holidays, true, false);
```

**验证**：`sched.getCalendar("holidays")` 非空。

**步骤 2：目标** —— Trigger 绑定 calendar。

```java
Trigger trigger = newTrigger()
    .withIdentity("t1", "g1")
    .withSchedule(cronSchedule("0 0 12 * * ?"))
    .modifiedByCalendar("holidays")
    .build();
```

**验证**：在排除日 12:00 **不应触发**；次日恢复。

**步骤 3：目标** —— 多个 Trigger 共用 `holidays` 名称。

### 可能踩坑

| 坑 | 解决 |
| --- | --- |
| 忘记 `modifiedByCalendar` | Trigger 不生效 |
| 时区导致「排除日错位」 | 统一 `TimeZone` |
| 更新日历未触发重算 | `updateTriggers=true` |

### 完整代码清单

- [CalendarExample.java](../../examples/src/main/java/org/quartz/examples/example8/CalendarExample.java)

### 测试验证

单测：固定 `Clock` 或注入触发时间，断言 **排除日无触发记录**。

## 4 项目总结（约 500–800 字）

### 优点与缺点（对比同类技术）

| 维度 | Quartz Calendar | 巨型 Cron | 业务代码里 if |
| --- | --- | --- | --- |
| 可维护性 | 高 | 低 | 中 |
| 可视化 | 中 | 低 | 高（自建 UI） |

### 适用 / 不适用场景

- **适用**：法定节假日、公司维护窗、多 Trigger 共享例外。
- **不适用**：极简单「周末不跑」且团队 Cron 功力强。

### 注意事项

- **数据同步**：节假日变更频率与缓存一致性。
- **与 misfire 联动**：长假期后首次触发（第15章）。

### 常见踩坑（生产案例）

1. **排除日用了本地 0 点、业务用 UTC**：根因是时区未对齐。
2. **更新日历未传播**：根因是 `updateTriggers` 参数误用。
3. **与调休政策脱节**：根因是数据源未接入人事系统。

#### 第12章思考题揭底

1. **`shutdown(true)` vs `false`**  
   **答**：**`true`**：尽量 **等待已在执行的 Job 完成** 再停调度线程与资源；**`false`**：**更快停止调度**，在跑任务可能被中断（语义以版本文档为准）。生产敏感写操作多用 **`true`** 并结合 **超时 cap**。

2. **「最多等 30 秒」**  
   **答**：在独立线程调用 `shutdown(true)`，主线程 **`Future.get(30, SECONDS)`**；超时后 **记录告警**，并视策略 **`shutdown(false)`** 或对 Job 发 **interrupt**（第19章）。K8s 上同步调大 **`terminationGracePeriodSeconds`**。

### 思考题（答案见下一章或 [答案索引](answers-index.md)）

1. `HolidayCalendar` 与 Cron 排除语法相比，何时更应使用 Calendar？
2. 多个 Trigger 共享同一 Calendar 时要注意什么？

### 推广计划提示

- **测试**：节假日边界与闰年用例。
- **运维**：日历变更走变更单。
- **开发**：下一章为 **基础篇综合实战**（独立章节），随后进入 example5 精读 misfire。
