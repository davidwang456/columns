# 第12章：优雅停机：`shutdown`、`waitForJobsToComplete`

> **篇别**：基础篇  
> **建议篇幅**：3000–5000 字（含对话与代码）  
> **结构约束**：对齐 [专栏模板](../template.md) 四段式。

## 示例锚点

| 类型 | 路径 |
| --- | --- |
| example1 | [SimpleExample.java](../../examples/src/main/java/org/quartz/examples/example1/SimpleExample.java) |
| API | `org.quartz.Scheduler#shutdown` |

## 1 项目背景（约 500 字）

### 业务场景

发布窗口要求：**新版本上线前，正在执行的报表 Job 不能半截写脏数据**；但又不能无限等待（某次外部 API hang 住导致全站无法滚动发布）。需要理解 **`shutdown(true)` 等待正在执行 Job 完成** 与 **`shutdown(false)`** 的差异，并在容器环境实现 **「最多等待 T 秒，超时则记录告警并强制收尾」** 的折中策略。

### 痛点放大

- **K8s `terminationGracePeriodSeconds`** 与 Quartz `shutdown` 不对齐，进程被 SIGKILL。
- **Spring `@PreDestroy`** 与 Quartz 关闭顺序错误导致 Bean 已销毁但 Job 仍运行。
- **误调用 `shutdown` 后仍 `scheduleJob`**：调度器已不可再调度。

## 2 项目设计（约 1200 字）

**角色**：小胖 · 小白 · 大师

---

**小胖**：停机不就是 `kill` 吗？JVM 都没了还管啥 Quartz。

**小白**：`shutdown` 和 `standby` 我到底该在发布用哪个？

**大师**：`standby` 是 **暂停接新单**；`shutdown` 是 **关店盘点后歇业**。发布若 **同一进程内滚动线程池**，常用 `standby`；若 **进程要退出**，必须走 `shutdown` 释放 **线程池、JobStore 连接、插件资源**。

**技术映射**：**`standby` vs `shutdown`**。

---

**小胖**：`shutdown(true)` 会等多久？我能不能设个 30 秒 cap？

**小白**：若 Job 永不结束，`true` 会不会把发布卡死？

**大师**：`true` 的等待 **没有标准超时参数**——需要你在应用层 **`ExecutorService` + `Future.get(timeout)`** 或 **在 Job 内协作式中断**（第19章）组合实现 cap。像 **「消防演习疏散」**：先广播（`interrupt`），再等固定时间，最后关门。

**技术映射**：**应用层超时 + Quartz shutdown 语义**。

---

**小胖**：`shutdown` 之后 `Scheduler` 对象还能复用吗？

**小白**：Spring `SchedulerFactoryBean.destroy` 里通常写什么？

**大师**：`shutdown` 后调度器进入 **不可再调度** 状态；是否可 `getScheduler` 再拿新实例取决于工厂与配置（常见是 **新工厂或新配置**）。Spring 集成里 **`destroy()` 内 shutdown** 是标配，并应避免 **重复 destroy** 抛异常。

**技术映射**：**容器生命周期钩子 ↔ `Scheduler.shutdown(boolean)`**。

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

复习 [SimpleExample.java](../../examples/src/main/java/org/quartz/examples/example1/SimpleExample.java) 末尾 `sched.shutdown(true)`。

### 分步实现

**步骤 1：目标** —— 对比 `true/false`。

```java
sched.shutdown(true);  // 尽量等待在跑 Job 完成
// vs
sched.shutdown(false); // 更激进，仍应查阅版本文档精确语义
```

**验证**：在 `HelloJob` 中 `Thread.sleep(10000)`，分别调用 `true/false`，用秒表观察停机耗时差异。

**步骤 2：目标** —— 应用层 30 秒 cap（示意）。

```java
ExecutorService ex = Executors.newSingleThreadExecutor();
Future<?> f = ex.submit(() -> {
    try { sched.shutdown(true); } catch (SchedulerException e) { throw new RuntimeException(e); }
});
try {
    f.get(30, TimeUnit.SECONDS);
} catch (TimeoutException te) {
    // 记录告警，必要时 sched.shutdown(false) 或配合 interrupt
    f.cancel(true);
}
ex.shutdown();
```

**验证**：长任务场景下 cap 生效，进程不无限阻塞。

**步骤 3：目标** —— JVM ShutdownHook（示意）。

```java
Runtime.getRuntime().addShutdownHook(new Thread(() -> {
    try { sched.shutdown(true); } catch (Exception ignored) {}
}));
```

### 可能踩坑

| 坑 | 解决 |
| --- | --- |
| shutdown 后仍 schedule | 新建 Scheduler 或修复生命周期 |
| Hook 里再调 System.exit | 避免死锁 |
| 与 DB 池一起关 | 先 shutdown Quartz 再关 DataSource |

### 完整代码清单

- [SimpleExample.java](../../examples/src/main/java/org/quartz/examples/example1/SimpleExample.java)

### 测试验证

集成测试：嵌入式 RAM Scheduler + 慢 Job，断言 `shutdown(true)` 后 **业务副作用计数完整**。

## 4 项目总结（约 500–800 字）

### 优点与缺点（对比同类技术）

| 维度 | Quartz shutdown | kill -9 | Spring 容器 stop |
| --- | --- | --- | --- |
| 业务完整性 | 可配置等待 | 差 | 中 |
| 发布耗时 | 可能变长 | 短 | 中 |

### 适用 / 不适用场景

- **适用**：金融写库、对账、幂等窗口敏感的 Job。
- **不适用**：纯无状态可任意杀死的短任务（仍需规范，但容忍度高）。

### 注意事项

- **K8s**：`terminationGracePeriodSeconds` ≥ 应用 cap + 缓冲。
- **分布式**：shutdown 只影响 **本进程 Scheduler**，不通知其它节点。

### 常见踩坑（生产案例）

1. **grace 过短**：根因是未压测最长 Job。
2. **DataSource 先关**：根因是 Spring `@Order` 错误。
3. **shutdown 阻塞 UI 线程**：根因是在 EDT/Netty IO 线程同步调用。

#### 第11章思考题揭底

1. **`threadCount` 过小**  
   **答**：**触发延迟增大**、**misfire 增多**、CPU 不高但 **队列排队**；日志出现 **线程池饥饿**；`getTriggerState` 长期非正常完成。对外表现为 **定时任务「集体晚点」**。

2. **`misfireThreshold` 增大能否消除 misfire**  
   **答**：**不能**。只是 **推迟「被判定为 misfire」的时间点**；根本解决需 **增加线程、优化 Job 耗时、调整 Trigger 间隔、misfireInstruction**（第14章）。

### 思考题（答案见下一章或 [答案索引](answers-index.md)）

1. `shutdown(true)` 与 `shutdown(false)` 对正在执行的 Job 分别意味着什么？
2. 如何实现「最多等 30 秒，超时强制停」？

### 推广计划提示

- **测试**：停机与长任务组合压测。
- **运维**：K8s grace 与发布脚本对齐。
- **开发**：下一章学习 `HolidayCalendar` 与 example8。
