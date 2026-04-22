# 第20章：可中断作业：`InterruptableJob` 与 `JobInterruptMonitorPlugin`

> **篇别**：中级篇  
> **建议篇幅**：3000–5000 字（含对话与代码）  
> **结构约束**：对齐 [专栏模板](../template.md) 四段式。

## 示例锚点

| 类型 | 路径 |
| --- | --- |
| example7 | [InterruptExample.java](../../examples/src/main/java/org/quartz/examples/example7/InterruptExample.java) |
| example7 | [DumbInterruptableJob.java](../../examples/src/main/java/org/quartz/examples/example7/DumbInterruptableJob.java) |
| 插件 | [JobInterruptMonitorPlugin.java](../../quartz/src/main/java/org/quartz/plugins/interrupt/JobInterruptMonitorPlugin.java) |

## 1 项目背景（约 500 字）

### 业务场景

长报表 Job 单次可能运行 30 分钟，运维在发布窗口需要 **「请求中断」** 而非 `kill -9`。Java 无法安全「硬杀线程」，因此 Quartz 提供 **`InterruptableJob` 协作式中断**：Job 在循环中检查 **`interrupt()` 标志** 并退出；`JobInterruptMonitorPlugin` 则可在 **超时未响应** 时记录或采取后续策略（以插件与版本文档为准）。

### 痛点放大

- **误以为 `Thread.interrupt()` 立刻生效**：IO 阻塞点可能不响应。
- **与 `shutdown(true)` 混用**：停机等待与业务中断信号需统一产品语义。
- **集群**：`scheduler.interrupt(JobKey)` 需路由到正确节点（查文档）。

## 2 项目设计（约 1200 字）

**角色**：小胖 · 小白 · 大师

---

**小胖**：我 `Future.cancel(true)` 不香吗？

**小白**：Quartz 的 `interrupt` 和线程 `interrupt` 是同一个东西吗？

**大师**：Quartz 在 **`InterruptableJob`** 上封装的是 **「调度员举手示意停工」**——工人（Job）要 **抬头看一眼再放下工具**；`Future.cancel` 更像 **远程拉闸**，对 **阻塞在 native 或不可中断 IO** 的场景同样 **不保证毫秒级**。协作式中断的本质是 **业务代码愿意配合**。

**技术映射**：**`scheduler.interrupt(JobKey)` + `Job#interrupt()`**。

---

**小胖**：那插件是干啥的？监工吗？

**小白**：插件超时后会强杀线程吗？

**大师**：`JobInterruptMonitorPlugin` 像 **「安全员巡逻」**——发现某作业超时未响应中断请求，按策略 **记录或告警**（具体行为读源码与配置）；**强杀线程** 在现代 Java 中不推荐，插件通常也不会这么做。

**技术映射**：**插件 = 横切策略注入**（第34章总览）。

---

**小胖**：我们报表跑在只读库上，中断一半会有脏读吗？

**小白**：要不要和数据库 `statement.cancel` 联动？

**大师**：协作式中断应与 **资源释放** 同行：关闭 JDBC Statement、释放文件锁。只设标志不清理，像 **工人听到停工哨子却把机器卡死在半开状态**。

**技术映射**：**try/finally + 中断检查点**。

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

运行 [InterruptExample.java](../../examples/src/main/java/org/quartz/examples/example7/InterruptExample.java)，阅读 `DumbInterruptableJob` 循环内的 **`Thread.interrupted()` / `isInterrupted` 检查**（以源码为准）。

### 分步实现

**步骤 1：目标** —— 在 Job 内每 N 次循环检查中断。

```java
while (!isInterrupted) {
    // 业务切片
    if (Thread.currentThread().isInterrupted()) {
        break;
    }
}
```

**步骤 2：目标** —— 外部调用：

```java
sched.interrupt(JobKey.jobKey("longReport", "ops"));
```

**验证**：日志出现 **中断退出路径**。

**步骤 3：目标** —— 在 `quartz.properties` 注册 `JobInterruptMonitorPlugin`（属性名以文档为准），模拟 **超时未响应**。

### 可能踩坑

| 坑 | 解决 |
| --- | --- |
| 无检查点 | 长循环无法停 |
| 吞中断 | 恢复 `interrupted` 状态 |
| 集群误 interrupt | 确认 Job 所在实例 |

### 完整代码清单

- [InterruptExample.java](../../examples/src/main/java/org/quartz/examples/example7/InterruptExample.java)
- [JobInterruptMonitorPlugin.java](../../quartz/src/main/java/org/quartz/plugins/interrupt/JobInterruptMonitorPlugin.java)

### 测试验证

压测：中断后 **线程数恢复**、**无泄漏连接**。

## 4 项目总结（约 500–800 字）

### 优点与缺点（对比同类技术）

| 维度 | 协作式中断 | kill -9 | 分区取消令牌 |
| --- | --- | --- | --- |
| 数据安全 | 高 | 低 | 高 |
| 响应速度 | 中 | 「快但不安全」 | 中 |

### 适用 / 不适用场景

- **适用**：长循环、批处理切片。
- **不适用**：native 阻塞无法插桩。

### 注意事项

- **幂等**：中断后可能部分写入。
- **指标**：中断次数、平均响应时间。

### 常见踩坑（生产案例）

1. **无检查点导致停不了**：根因是 CPU 密集无 yield。
2. **中断后状态不一致**：根因是无事务边界。
3. **插件配置错误无告警**：根因是未接日志。

#### 第19章思考题揭底

1. **链式监听 vs 单 Job 顺序调用的失败隔离**  
   **答**：链式在 **A 成功完成后** 才调度 B，**天然切断「A 失败仍执行 B」**（若 A 抛异常）；单 Job 内顺序调用若 **中间未 return**，易 **继续执行后续步骤**。链式代价是 **调试路径分散**、需 **管理映射与全局 Listener**。

2. **避免无限循环**  
   **答**：启动前 **DAG 校验** 检测环；限制 **最大链深度**；在 **Listener 内加断路器**（失败计数）；对 **自触发** 使用 **显式状态机** 而非隐式环。

### 思考题（答案见下一章或 [答案索引](answers-index.md)）

1. 协作式中断为何不能保证毫秒级结束？
2. `JobInterruptMonitorPlugin` 解决的是什么痛点？

### 推广计划提示

- **测试**：中断与 JDBC 长查询组合。
- **运维**：发布 playbook 写明 interrupt 与 grace。
- **开发**：下一章 example14 看 Trigger 优先级。
