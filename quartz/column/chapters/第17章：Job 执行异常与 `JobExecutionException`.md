# 第17章：Job 执行异常与 `JobExecutionException`

> **篇别**：中级篇  
> **建议篇幅**：3000–5000 字（含对话与代码）  
> **结构约束**：对齐 [专栏模板](../template.md) 四段式。

## 示例锚点

| 类型 | 路径 |
| --- | --- |
| example6 | [JobExceptionExample.java](../../examples/src/main/java/org/quartz/examples/example6/JobExceptionExample.java) |
| example6 | [BadJob1.java](../../examples/src/main/java/org/quartz/examples/example6/BadJob1.java)、[BadJob2.java](../../examples/src/main/java/org/quartz/examples/example6/BadJob2.java) |

## 1 项目背景（约 500 字）

### 业务场景

第三方 API 限流导致 Job 偶发失败：产品希望 **「立即再试一次」** 与 **「记录失败并等下一次周期」** 两种策略并存。[JobExceptionExample.java](../../examples/src/main/java/org/quartz/examples/example6/JobExceptionExample.java) 中 **BadJob1** 通过 `JobExecutionException` 配置 **refire immediately**；**BadJob2** 则 **不再 refire**。本章把该示例映射到生产：**如何用异常控制调度语义，而不是默默吞异常**。

### 痛点放大

- **吞异常**：调度器认为成功，监控无告警，业务静默失败。
- **滥用立即 refire**：与下游 rate limit 冲突，形成 **自激振荡**。
- **与 Listener 混用**：异常路径是否触发 `wasExecuted` 等需读文档（第17章）。

## 2 项目设计（约 1200 字）

**角色**：小胖 · 小白 · 大师

---

**小胖**：Job 里 `throw new RuntimeException()` 不行吗？

**小白**：`JobExecutionException` 的 `refireImmediately` 和我在代码里 `while` 重试三次有什么区别？

**大师**：`JobExecutionException` 是 **「告诉调度台：这趟车怎么处理」** 的标准手势；`RuntimeException` 若未包装，语义更偏 **「意外翻车」**。`refireImmediately` 是 **调度器层面的立刻加开一班**；业务 `while` 重试是 **同一执行线程内循环**，占用 worker 时间不同，对 **线程池与 misfire** 影响也不同。

**技术映射**：**`throw new JobExecutionException(..., RefireImmediately)`**（以 BadJob 源码为准）。

---

**小胖**：那 BadJob2「never refire」是不是就永远不再跑了？

**小白**：下一次 Trigger 周期还会进来吗？

**大师**：要区分 **「本次执行内的 refire」** 与 **「整个 Trigger 生命周期」**。example 注释写的是 **「throw an exception and never refire」**——指的是 **不因本次异常触发 Quartz 的立即再派机制**；**下一个正常调度 tick** 仍会到来，除非 Trigger 已完成或被取消。

**技术映射**：阅读 **BadJob2** 内 `JobExecutionException` 的 **flag 组合**。

---

**小胖**：异常日志打在哪？要不要每个 Job 自己 try-catch？

**小白**：全局 `JobListener` 能否统一记录异常？

**大师**：**分层**：Job 内处理可恢复业务错误；**不可恢复** 或需审计的，抛 `JobExecutionException` 或交由 **Listener** 统一落日志/告警平台。像 **「收银台」**：自己能找零的小问题柜台解决；涉及假币的上报安保系统。

**技术映射**：**JobListener#jobWasExecuted** 与异常对象。

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

阅读 `BadJob1` / `BadJob2` 源码（与 example6 同目录）。

### 分步实现

**步骤 1：目标** —— 运行 `JobExceptionExample`，观察 **30 秒窗口** 内日志频率差异。

**步骤 2：目标** —— 在自建 Job 中实验：

```java
throw new JobExecutionException("rate limited", true); // 参数以实际 API 为准
```

对照 Quartz Javadoc 设置 **`refireImmediately` / `unscheduleFiringTrigger` / `unscheduleAllTriggers`** 等布尔组合（版本差异务必核对）。

**步骤 3：目标** —— 对比 **吞异常**：

```java
try { risky(); } catch (Exception e) { log.error("ignored", e); }
```

**验证**：Trigger 仍按节拍走，但业务侧无补偿，监控可能 **false green**。

### 可能踩坑

| 坑 | 解决 |
| --- | --- |
| 版本 API 差异 | 以当前 Javadoc 为准 |
| refire 风暴 | 退避 + 熔断 |
| 与事务 JobStore 回滚 | 第22章 |

### 完整代码清单

- [JobExceptionExample.java](../../examples/src/main/java/org/quartz/examples/example6/JobExceptionExample.java)

### 测试验证

断言 Listener 收到 `JobExecutionException` 的次数；模拟下游 429。

## 4 项目总结（约 500–800 字）

### 优点与缺点（对比同类技术）

| 维度 | JobExecutionException | 业务 try-catch | 消息重试队列 |
| --- | --- | --- | --- |
| 与调度耦合 | 高 | 中 | 低 |
| 跨进程 | 否 | 否 | 是 |

### 适用 / 不适用场景

- **适用**：进程内快速重试、取消后续触发。
- **不适用**：长延迟重试（应用 MQ dead-letter）。

### 注意事项

- **可观测性**：异常必须带 **业务关联 ID**。
- **幂等**：refire 与 misfire 叠加（第15章）。

### 常见踩坑（生产案例）

1. **refire 打爆支付**：根因是无退避。
2. **吞异常导致对账缺失**：根因是规范未落地。
3. **错误使用 unscheduleAllTriggers**：根因是未读 API。

#### 第16章思考题揭底

1. **misfire 后多次执行如何保证幂等**  
   **答**：使用 **业务幂等键**（订单号+状态机版本）、**数据库唯一约束**、或 **乐观锁**；Quartz 层 **不替你保证业务幂等**；`@DisallowConcurrentExecution` 只解决 **同 JobDetail 并发**，不解决 **跨次重入**。

2. **`PersistJobDataAfterExecution` + JDBC 写入频率**  
   **答**：每次成功执行结束可能触发 **JobDetail 状态 UPDATE**；高频短周期 Job 会 **放大写 QPS**；应 **减少 map 体量**、**合并写**、或将 **大状态外置** 仅回写游标。

### 思考题（答案见下一章或 [答案索引](answers-index.md)）

1. `JobExecutionException` 的 `refireImmediately` 与「快速重试」区别？
2. 若 Job 吞掉异常不抛出，调度器行为如何？

### 推广计划提示

- **测试**：异常策略矩阵单测。
- **运维**：告警按 JobKey 分组。
- **开发**：下一章学习 Listener 与 Matcher。
