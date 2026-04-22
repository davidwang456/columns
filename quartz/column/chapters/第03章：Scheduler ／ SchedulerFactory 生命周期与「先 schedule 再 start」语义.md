# 第03章：Scheduler / SchedulerFactory 生命周期与「先 schedule 再 start」语义

> **篇别**：基础篇  
> **建议篇幅**：3000–5000 字（含对话与代码）  
> **结构约束**：对齐 [专栏模板](../template.md) 四段式。

## 示例锚点

| 类型 | 路径 |
| --- | --- |
| example1 | [SimpleExample.java](../../examples/src/main/java/org/quartz/examples/example1/SimpleExample.java) |
| 源码 | [StdSchedulerFactory.java](../../quartz/src/main/java/org/quartz/impl/StdSchedulerFactory.java) |

## 1 项目背景（约 500 字）

### 业务场景

活动系统需要在 **应用启动阶段** 预注册大量定时任务（预热缓存、生成临时 token 桶），但要求 **对外接口就绪后再真正开始计时触发**，避免冷启动时数据库尚未连上就触发 Job。团队发现：有人在 `scheduleJob` 之前就调了监控接口读「下一次触发时间」，结果 NPE；又有人把 `start` 写在所有 `schedule` 之前，怀疑「先注册的任务会不会丢」。本章澄清 **`SchedulerFactory` → `Scheduler` 的创建、`start` 的语义、以及 schedule 与 start 的先后关系**。

### 痛点放大

- **误解 start**：以为 `getScheduler()` 后就会自动跑。
- **误解 schedule**：以为 `start` 之前的注册会丢失。
- **资源泄漏**：`StdSchedulerFactory` 创建的 Scheduler 未 `shutdown`，导致线程与端口（若 RMI）残留。

## 2 项目设计（约 1200 字）

**角色**：小胖 · 小白 · 大师

---

**小胖**：`getScheduler()` 一调用，后台线程就转起来了吗？我电脑风扇怎么没响？

**小白**：文档说 jobs can be scheduled before `start`——那在 `start` 之前注册的 Trigger，第一次 fire 的时间怎么算？会「补跑」错过的点吗？

**大师**：把 `Scheduler` 想成 **赛车场**：你可以在比赛开始前先把车手名单和发车时间表贴好（`scheduleJob`），但发令枪没响（`start`）之前，车不会出站。开赛瞬间，系统才按时间表去 **捞要到点的 Trigger**。`start` 之前已经 `Past` 的触发点是否补跑，取决于 **misfire 策略与 Trigger 类型**，不是「注册晚了就丢」这么简单。

**技术映射**：**`start()` 之前 `scheduleJob` 合法**；触发器在 start 后由 `QuartzSchedulerThread` 驱动（高级篇第29章）。

---

**小胖**：那 `SchedulerFactory` 是不是每次都要 new？能单例吗？

**小白**：多 `Scheduler` 与 `instanceName` 冲突会怎样？工厂类线程安全吗？

**大师**：`StdSchedulerFactory` 类似 **总工办公室**：你可以多次 `getScheduler()` 拿到 **同名默认单例**（取决于配置），也可以程序化构造多套 properties 得到多个调度器实例。冲突点在于 **同一套持久化 Store 里 instanceName / instanceId 规划**（集群章详述）。一般应用 **一个业务调度器实例** 足够，复杂域可拆分「报表调度器」「同步调度器」隔离故障面。

**技术映射**：**`SchedulerFactory` 负责装配**；**`Scheduler` 负责运行时状态**。

---

**小胖**：活动结束要「整台 Scheduler 暂停」，是 `pauseAll` 还是 `standby`？

**小白**：`shutdown` 之后还能 `start` 吗？

**大师**：`standby` 像 **把发令枪收进抽屉**：表还在，但不发车；`shutdown` 像 **散场锁门**：再进来要重新建场子（新的 `Scheduler` 实例）。是否可 `standby` 后恢复 `start`，取决于你是否仍持有同一 `Scheduler` 引用且未 shutdown。

**技术映射**：**`standby` / `start` 配对** 用于临时停表；**`shutdown`** 用于释放资源。

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

同第02章；本章重点阅读 [example2/SimpleTriggerExample.java](../../examples/src/main/java/org/quartz/examples/example2/SimpleTriggerExample.java) 开头注释：

> jobs can be scheduled before `sched.start()` has been called

### 分步实现

**步骤 1：目标** —— 在 `start` 前批量 `scheduleJob`。

```java
SchedulerFactory sf = new StdSchedulerFactory();
Scheduler sched = sf.getScheduler();

// 尚未 start：仅注册元数据与触发计划
JobDetail job = newJob(MyJob.class).withIdentity("j1", "g1").build();
Trigger t = newTrigger().withIdentity("t1", "g1").startAt(someFutureDate).build();
sched.scheduleJob(job, t);

sched.start(); // 此刻起 QuartzSchedulerThread 开始工作
```

**验证**：在 `start` 前后打日志，确认 **仅 start 后** 才出现触发执行。

**步骤 2：目标** —— 观察 `start` 后 `getTriggerState`。

```java
TriggerKey key = TriggerKey.triggerKey("t1", "g1");
sched.start();
// 稍后
TriggerState state = sched.getTriggerState(key);
```

**验证**：未到点时应接近 `NORMAL`（具体枚举以版本 API 为准）；到达后变为完成/执行中等状态。

**步骤 3：目标** —— `standby` 与恢复。

```java
sched.standby();
// 此窗口内应不再触发新的执行（已在执行的取决于实现与配置）
sched.start();
```

**验证**：对比 `standby` 前后业务计数是否停止增长。

### 可能踩坑

| 坑 | 解决 |
| --- | --- |
| 认为 `getScheduler()` 等于 `start` | 查阅 API，显式调用 `start()` |
| `shutdown` 后复用同一引用 | 新建 `SchedulerFactory` 或重新 `getScheduler`（视配置） |
| 集群下误用 `standby` 当维护 | 需结合数据库锁状态理解（第24章） |

### 完整代码清单

- [SimpleTriggerExample.java](../../examples/src/main/java/org/quartz/examples/example2/SimpleTriggerExample.java)（注释证明 start 顺序）
- [StdSchedulerFactory.java](../../quartz/src/main/java/org/quartz/impl/StdSchedulerFactory.java)

### 测试验证

编写最小 JUnit：mock 或嵌入式 RAM Scheduler，`scheduleJob` 后断言 `getJobDetail` 存在；`start` 前 Job 不应执行（可用 `CountDownLatch` 在 Job 中计数）。

## 4 项目总结（约 500–800 字）

### 优点与缺点（对比同类技术）

| 维度 | Quartz 显式生命周期 | Executor「提交即运行」 |
| --- | --- | --- |
| 启动控制 | start 闸门清晰 | 需自行封装 |
| 预注册 | 一等公民 | 需自行延迟 firstRun |
| API 复杂度 | 略高 | 低 |

### 适用 / 不适用场景

- **适用**：冷启动分阶段：加载配置 → 注册任务 → 对外健康 → start。
- **不适用**：极简单延迟一次任务，可用 `CompletableFuture` 减少概念负担。

### 注意事项

- **Spring Boot**：`SchedulerFactoryBean` 会包装生命周期，避免与手动 `start` 重复。
- **多 Scheduler**：日志中 `schedulerName` 区分。

### 常见踩坑（生产案例）

1. **健康检查早于 start**：K8s 探针通过但任务永不跑；根因是调度器未 start。
2. **重复 start**：部分版本抛异常或 no-op；根因是未读 API 契约。
3. **shutdown 遗漏**：热部署 ClassLoader 泄漏；根因是未在容器 destroy 中关闭。

#### 第02章思考题揭底

1. **最小步骤（伪代码）**  
   `SchedulerFactory f = new StdSchedulerFactory();` → `Scheduler s = f.getScheduler();` → `s.scheduleJob(job, trigger);` → `s.start();` →（运行后）`s.shutdown(true);`

2. **schedule 在 start 前后语义**  
   **前后均可注册**；在 **均未过期** 的前提下，触发计划都会被记录。**`start` 之前** 不会触发执行；**`start` 之后** 才进入运行线程循环。若注册时首次触发时间已在「当前时间之前」，则可能进入 **misfire** 处理路径（第14章）。

### 思考题（答案见下一章或 [答案索引](answers-index.md)）

1. 为何 Quartz 通常建议显式 `shutdown`，而不是依赖 JVM 退出？
2. 多 `Scheduler` 实例时，`instanceName` 应如何规划？

### 推广计划提示

- **测试**：增加「start 闸门」集成测试用例。
- **运维**：发布脚本中在 SIGTERM 后留足 `shutdown` 窗口。
- **开发**：阅读 `StdSchedulerFactory` 中 properties key 前缀 `org.quartz.scheduler`（第11章系统展开）。
