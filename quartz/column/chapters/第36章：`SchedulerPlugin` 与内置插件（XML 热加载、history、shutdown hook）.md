# 第36章：`SchedulerPlugin` 与内置插件（XML 热加载、history、shutdown hook）

> **篇别**：高级篇  
> **建议篇幅**：3000–5000 字（含对话与代码）  
> **结构约束**：对齐 [专栏模板](../template.md) 四段式。

## 示例锚点

| 类型 | 路径 |
| --- | --- |
| example10 | [PlugInExample.java](../../examples/src/main/java/org/quartz/examples/example10/PlugInExample.java) |
| 包 | [org.quartz.plugins](../../quartz/src/main/java/org/quartz/plugins) |

## 1 项目背景（约 500 字）

### 业务场景

运维希望 **在不重启应用的情况下热更新 XML 任务定义**；审计希望 **记录每次 Trigger 触发历史**；SRE 希望 **JVM 退出时自动 `shutdown` Scheduler**。这些横切能力在 Quartz 中以 **`SchedulerPlugin`** 实现，example10 演示 **插件装配**；内置 **`LoggingJobHistoryPlugin` / `ShutdownHookPlugin` / `XMLSchedulingDataProcessorPlugin`** 等覆盖常见需求。

### 痛点放大

- **XML 扫描频率过高**：CPU 与文件 IO 飙升。
- **history 日志量**：磁盘与 ELK 成本。
- **插件初始化顺序**：依赖未满足导致 NPE。

## 2 项目设计（约 1200 字）

**角色**：小胖 · 小白 · 大师

---

**小胖**：插件和 Listener 有啥区别？

**小白**：插件能改调度器内部状态吗？

**大师**：插件像 **「装修时的模块化增项」**——在 **Scheduler 生命周期关键点** 挂钩子（`initialize`/`start`/`shutdown`）；Listener 更贴近 **Job/Trigger 事件**。插件适合 **横切基础设施**，Listener 适合 **业务可组合观测**。

**技术映射**：**`org.quartz.spi.SchedulerPlugin`**。

---

**小胖**：XML 热加载会不会加载半文件？

**小白**：`overwriteExistingJobs` 风险？

**大师**：要靠 **原子写文件（rename）** + **校验失败回滚策略**；`overwrite` true/false 决定 **是否覆盖线上已存在任务**，生产要 **评审变更单**。

**技术映射**：**`XMLSchedulingDataProcessorPlugin` 配置项**（第35章联动）。

---

**小胖**：shutdown hook 和 Spring 的 hook 会不会重复？

**小白**：顺序怎么定？

**大师**：要避免 **双停或死锁**——通常 **只保留一层** 或明确 **先后顺序**（先 Spring 容器停 Bean，再停 Quartz）。

**技术映射**：**JVM shutdown hook 顺序**。

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

阅读 [PlugInExample.java](../../examples/src/main/java/org/quartz/examples/example10/PlugInExample.java) 与对应 **properties**（若 example 提供）。

### 分步实现

**步骤 1：目标** —— 在 `quartz.properties` 注册 `org.quartz.plugins.history.LoggingJobHistoryPlugin`（前缀以文档为准）。

**步骤 2：目标** —— 观察 **日志字段** 是否包含 JobKey/TriggerKey。

**步骤 3：目标** —— 启用 **shutdown hook 插件**，对比 **kill 信号** 下资源释放。

### 可能踩坑

| 坑 | 解决 |
| --- | --- |
| 日志爆炸 | 采样/异步 appender |
| XML 路径错误 | 健康检查 |
| 插件类不在 classpath | fat jar 检查 |

### 完整代码清单

- [PlugInExample.java](../../examples/src/main/java/org/quartz/examples/example10/PlugInExample.java)
- [plugins 目录](../../quartz/src/main/java/org/quartz/plugins)

### 测试验证

集成测试：旋转 XML 文件，断言 **任务增删** 符合预期。

## 4 项目总结（约 500–800 字）

### 优点与缺点（对比同类技术）

| 维度 | Quartz Plugin | 自建守护线程 | Spring @EventListener |
| --- | --- | --- | --- |
| 集成度 | 高 | 低 | 中 |

### 适用 / 不适用场景

- **适用**：运维型横切能力。
- **不适用**：复杂业务状态机（用工作流）。

### 注意事项

- **安全**：XML 来源可信。
- **版本**：插件类名变更。

### 常见踩坑（生产案例）

1. **热加载误覆盖生产任务**：根因是无 code review。
2. **日志成本超预算**：根因是未采样。
3. **双 shutdown**：根因是多框架钩子。

#### 第35章思考题揭底

1. **自定义 `ThreadPool` 契约**  
   **答**：需实现 **`org.quartz.spi.ThreadPool`**：**`initialize`/`shutdown`/`getPoolSize`/`runInThread`** 等；保证 **任务提交不丢**、**关闭时拒绝新任务并尽量完成在跑**；与 **调度线程协作** 不产生 **死锁**；在 **Quartz 生命周期** 内可重复初始化语义以文档为准。

2. **`JobFactory` + Spring 关键方法**  
   **答**：**`Job newJob(TriggerFiredBundle bundle, Scheduler scheduler)`** —— 在此 **创建或获取 Job 实例** 并完成 **依赖注入**；常配合 **`scheduler.setJobFactory`** 使用；注意 **public no-arg 构造** 与 **Bean 作用域**。

### 思考题（答案见下一章或 [答案索引](answers-index.md)）

1. `XMLSchedulingDataProcessorPlugin` 的文件扫描频率过高会怎样？
2. history 插件对日志量的影响？

### 推广计划提示

- **测试**：XML 合法/非法用例。
- **运维**：日志配额与索引策略。
- **开发**：下一章 `XMLSchedulingDataProcessor` 细节。
