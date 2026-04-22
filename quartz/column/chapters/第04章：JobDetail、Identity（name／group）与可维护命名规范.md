# 第04章：JobDetail、Identity（name/group）与可维护命名规范

> **篇别**：基础篇  
> **建议篇幅**：3000–5000 字（含对话与代码）  
> **结构约束**：对齐 [专栏模板](../template.md) 四段式。

## 示例锚点

| 类型 | 路径 |
| --- | --- |
| example1 | [SimpleExample.java](../../examples/src/main/java/org/quartz/examples/example1/SimpleExample.java) |
| API | `org.quartz.JobBuilder` |

## 1 项目背景（约 500 字）

### 业务场景

多租户 SaaS 为每个租户配置「日终对账」任务。初期全部使用 `group1` / `job1` 命名，运维在日志里无法区分租户；开发又用字符串拼接去 `deleteJob`，误删邻租任务。产品要求：**Identity 必须可映射到租户与业务域**，且支持 **程序化按 Key 精确操作**（暂停、替换 Job 类版本）。本章围绕 **`JobKey`（name + group）** 与 **`JobDetail` 的职责** 建立规范。

### 痛点放大

- **命名冲突**：同组同名导致 `ObjectAlreadyExistsException` 或静默覆盖（取决于 API）。
- **运维不可读**：`DEFAULT.DEFAULT` 满天飞。
- **与 Trigger 混淆**：误以为 Trigger 的 name 必须与 Job 相同。

## 2 项目设计（约 1200 字）

**角色**：小胖 · 小白 · 大师

---

**小胖**：`job1` 和 `group1` 不就是两个字符串吗？我全用 `DEFAULT` 最省事。

**小白**：`JobKey` 和 `TriggerKey` 同名不同组，到底算不算冲突？`JobDetail` 和 `Job` 实现类是一对一吗？

**大师**：把 `JobKey` 想成 **工牌上的「部门 + 工号」**——全公司可以有两个「张伟」，但不能 **同部门同工号**。`TriggerKey` 是另一套工牌系统：你可以 **同名的触发器在不同组**，它和 JobKey **命名空间独立**。`Job` 实现类则是「岗位技能说明书」，多个 `JobDetail` 可以指向同一类（不同工牌同一技能）。

**技术映射**：**`JobKey = name + group`**；**`JobDetail` 持有 JobKey + JobClass + JobDataMap**。

---

**小胖**：那我要给租户 A、B 各一个对账任务，group 写租户 ID？

**小白**：group 过长或含特殊字符有没有坑？JDBC 集群里 JobKey 会进索引吗？

**大师**：常见规范是 **`group = tenantId` 或 `bizDomain`**，`name = jobPurpose`，例如 `reports/dailyReconcile`。避免把 **易变参数**（如批次号）写进 Key，应放 `JobDataMap`。长度上一般足够用，但要考虑 **与监控系统、URL 编码** 的兼容性；数据库里 JobKey 会作为 **主键/联合键的一部分**，乱命名会增加排障成本。

**技术映射**：**Identity 稳定、可检索**；**易变数据进 JobDataMap**。

---

**小胖**：我想升级 `HelloJob` 到 `HelloJobV2`，要不要换 JobKey？

**小白**：换了 Key 算不算「新任务」？旧 Trigger 还绑得上吗？

**大师**：`JobDetail` 的 **durability** 与 **replace** 策略决定「换壳不换证」还是「新发证」。若仅升级类逻辑且保持 Key，可用 **replace=true** 的 API（具体方法名以版本为准）更新定义；若拆新旧版本并行灰度，应用 **新 Key + 新 Trigger**，避免一条 Trigger 绑两个语义。

**技术映射**：**`JobBuilder` + `withIdentity` + `storeDurably`** 控制无 Trigger 时是否保留。

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
## 3 项目实战（约 1500–2000 字）

### 环境准备

JDK + Gradle；打开 [SimpleExample.java](../../examples/src/main/java/org/quartz/examples/example1/SimpleExample.java)。

### 分步实现

**步骤 1：目标** —— 使用稳定命名空间。

```java
String tenant = "t-10001";
JobDetail job = newJob(HelloJob.class)
    .withIdentity("dailyReconcile", tenant)  // name, group
    .withDescription("Tenant daily reconcile")
    .build();

Trigger trigger = newTrigger()
    .withIdentity("dailyReconcileTrigger", tenant)
    .startAt(runTime)
    .forJob(job)  // 显式绑定，利于阅读
    .build();

sched.scheduleJob(job, trigger);
```

**验证**：日志打印 `job.getKey()` 应显示 `t-10001.dailyReconcile` 形式（以实际 `toString` 为准）。

**步骤 2：目标** —— 按 Key 查询与删除。

```java
JobKey jk = JobKey.jobKey("dailyReconcile", "t-10001");
boolean exists = sched.checkExists(jk);
sched.deleteJob(jk);
```

**验证**：`deleteJob` 后 `checkExists` 为 false；关联 Trigger 一并删除（默认语义，以 API 文档为准）。

**步骤 3：目标** —— 演示 **JobKey 与 TriggerKey 同名不同组不冲突**。

```java
JobDetail j1 = newJob(HelloJob.class).withIdentity("same", "groupA").build();
Trigger t1 = newTrigger().withIdentity("same", "groupB").startAt(future).forJob(j1).build();
sched.scheduleJob(j1, t1);
```

**验证**：成功调度；说明两套 Key 空间独立。

### 可能踩坑

| 坑 | 解决 |
| --- | --- |
| 全 DEFAULT | 制定 `group` 语义：租户/域/环境 |
| 把 UUID 当 name | 无法运维检索；UUID 放 JobDataMap |
| 忘记 `forJob` 在多 Trigger 场景 | 明确绑定，减少误连 |

### 完整代码清单

- [example1](../../examples/src/main/java/org/quartz/examples/example1)
- Quartz API：`JobBuilder`、`JobKey`

### 测试验证

断言 `sched.getJobDetail(JobKey.jobKey(...))` 非空；`getTriggersOfJob` 数量与预期一致。

## 4 项目总结（约 500–800 字）

### 优点与缺点（对比同类技术）

| 维度 | Quartz JobKey | Cron 表达式字符串 | 随机任务 ID |
| --- | --- | --- | --- |
| 可读性 | 高（若规范） | 中 | 低 |
| 操作 API | 丰富 | 需自建映射 | 需自建索引 |

### 适用 / 不适用场景

- **适用**：多租户、多环境、需批量暂停某租户全部任务。
- **不适用**：一次性匿名后台任务（可用无命名语义的其他 API 模式，但仍建议可读名）。

### 注意事项

- **大小写敏感性**：以 Quartz 版本文档为准。
- **与 Spring Bean name 区分**：不要混用两套命名。

### 常见踩坑（生产案例）

1. **脚本批量删错组**：根因是 group 常量拼写错误。
2. **迁移环境后 Key 冲突**：根因是未把 `env` 纳入 group。
3. **监控告警无法聚合**：根因是 name 使用随机 UUID。

#### 第03章思考题揭底

1. **为何建议显式 `shutdown` 而非只靠 JVM 退出**  
   **答**：JVM `exit` 不保证 Quartz 后台线程、**线程池、JobStore 连接** 按序释放；可能导致 **触发器状态未落库**（JDBC 模式）、**钩子未执行**、**文件锁残留**。显式 `shutdown(true/false)` 把 **调度语义** 与 **资源回收** 纳入可控路径，便于与容器生命周期对齐。

2. **`instanceName` 规划**  
   **答**：同一 JVM 内多个 `Scheduler` 应使用 **不同的 `org.quartz.scheduler.instanceName`**，避免 JMX、日志与部分资源混淆；若共享 **同一 JDBC JobStore 表**，还需 **唯一 `instanceId`（集群）**（第11、24章）。通常：**按业务域拆分 instanceName**，而非每租户一个 Scheduler（除非强隔离需求）。

### 思考题（答案见下一章或 [答案索引](answers-index.md)）

1. `JobKey` 与 `TriggerKey` 同名不同组是否允许？冲突时会发生什么？
2. 若把 group 全部设为 `DEFAULT`，生产上有什么风险？

### 推广计划提示

- **测试**：用例覆盖「非法字符、超长 group」边界（按企业规范）。
- **运维**：将 JobKey 纳入日志 MDC。
- **开发**：下一章进入 Trigger 抽象与多 Trigger 绑定。
