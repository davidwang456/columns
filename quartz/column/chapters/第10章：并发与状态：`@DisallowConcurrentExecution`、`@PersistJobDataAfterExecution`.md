# 第10章：并发与状态：`@DisallowConcurrentExecution`、`@PersistJobDataAfterExecution`

> **篇别**：基础篇  
> **建议篇幅**：3000–5000 字（含对话与代码）  
> **结构约束**：对齐 [专栏模板](../template.md) 四段式。

## 示例锚点

| 类型 | 路径 |
| --- | --- |
| example4 | [JobStateExample.java](../../examples/src/main/java/org/quartz/examples/example4/JobStateExample.java) |
| example4 | [ColorJob.java](../../examples/src/main/java/org/quartz/examples/example4/ColorJob.java) |

## 1 项目背景（约 500 字）

### 业务场景

库存流水聚合 Job 被 **两个 Trigger** 驱动：一个每 5 分钟增量，一个每小时补偿。若两次执行重叠，会出现 **双线程并发写同一聚合表**，产生 **重复计数或丢失更新**。业务要求：**同一 `JobDetail` 身份下串行执行**；同时希望 **在 Job 内维护跨次执行的计数** 并写回 `JobDataMap` 供排障——这正是 [ColorJob.java](../../examples/src/main/java/org/quartz/examples/example4/ColorJob.java) 演示的 **`@DisallowConcurrentExecution` + `@PersistJobDataAfterExecution`** 组合。

### 痛点放大

- **把单线程池当万能钥匙**：全局串行损失吞吐；注解可 **按 Job 类粒度** 控制。
- **持久化状态与性能**：每次执行后写回 map 触发 **JDBC 更新**。
- **集群下「串行」范围**：同一 JobKey 在集群中 **只有一个实例执行**（配合 JDBC 锁），不是跨所有 Job。

## 2 项目设计（约 1200 字）

**角色**：小胖 · 小白 · 大师

---

**小胖**：并发不好吗？我线程池开大一点，Job 里自己加 `synchronized` 行不行？

**小白**：`@DisallowConcurrentExecution` 是进程内互斥还是集群互斥？和数据库行锁什么关系？

**大师**：这个注解像 **「同一工位同一时刻只能坐一个人」**——Quartz 在 **调度层** 避免同一 `JobDetail`（按身份）重叠执行；**集群** 下则由 **JobStore 的锁机制** 保证只有一个节点抢到执行权。自己在 Job 里加锁，容易 **锁粒度失控** 且 **与 Trigger 节拍脱节**。

**技术映射**：**`DisallowConcurrentExecution` = JobDetail 级串行语义**。

---

**小胖**：`@PersistJobDataAfterExecution` 是不是每次跑完都 `UPDATE` 数据库？

**小白**：如果 Job 抛异常，状态还写吗？与 misfire 叠加会怎样？

**大师**：把它想成 **「每次干完活交工作日志」**——成功执行后合并回 `JobDataMap` 并持久化（语义以文档为准）。抛异常时是否回滚写入，要结合 **JobStore 事务** 与 **JobExecutionException** 配置理解（第15、16、22章）。与 misfire 叠加时，可能出现 **多次重跑导致计数跳变**，需要 **幂等设计**。

**技术映射**：**`PersistJobDataAfterExecution` → JobDataMap 回写 + JDBC 持久化路径**。

---

**小胖**：example4 为啥还要强调不能用成员变量保存状态？

**小白**：如果我自己写 `StatefulJob` 接口（老 API）呢？

**大师**：历史接口与注解是 **两条演进线**；新项目优先 **注解 + 无状态类**。成员变量在 **多实例 classloader / JobFactory** 场景下更不可控。

**技术映射**：**无状态 `execute` + 外置/JobDataMap 状态**。

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

运行 `JobStateExample` 并对比 **带注解的 `ColorJob`** 日志：观察 **计数递增** 与 **成员变量 `_counter` 不具持久语义** 的输出。

### 分步实现

**步骤 1：目标** —— 复现 example4 的 job1/job2 配置。

打开 [JobStateExample.java](../../examples/src/main/java/org/quartz/examples/example4/JobStateExample.java)，注意 **job2 使用 `Red` 与不同初始 count**。

**步骤 2：目标** —— 自建「双 Trigger 同 Job」实验，验证串行。

```java
@PersistJobDataAfterExecution
@DisallowConcurrentExecution
public class CounterJob implements Job {
    @Override
    public void execute(JobExecutionContext context) {
        JobDataMap map = context.getJobDetail().getJobDataMap();
        int c = map.getInt("c");
        Thread.sleep(2000); // 拉长占用
        map.put("c", c + 1);
    }
}
```

为同一 `JobDetail` 注册 **两个短周期 Trigger**，观察日志是否 **从不重叠**（在 `threadCount` 足够前提下仍串行于同一 JobDetail）。

**步骤 3：目标** —— 去掉 `@DisallowConcurrentExecution` 对比。

**验证**：可能出现交错日志（谨慎在生产环境操作）。

### 可能踩坑

| 坑 | 解决 |
| --- | --- |
| 以为注解跨不同 JobKey | 串行范围是 **同类+同 JobDetail 身份**（以文档为准） |
| 回写大对象 | 控制 map 尺寸 |
| 与 `StatefulJob` 混用概念 | 统一团队规范 |

### 完整代码清单

- [ColorJob.java](../../examples/src/main/java/org/quartz/examples/example4/ColorJob.java)
- [JobStateExample.java](../../examples/src/main/java/org/quartz/examples/example4/JobStateExample.java)

### 测试验证

并发测试：`CountDownLatch` + 多 Trigger；断言 **最大并发度为 1**（对同一 JobDetail）。

## 4 项目总结（约 500–800 字）

### 优点与缺点（对比同类技术）

| 维度 | 注解串行 | 单线程池 | DB 悲观锁 |
| --- | --- | --- | --- |
| 粒度 | JobDetail 级 | 全局 | 行级 |
| 吞吐 | 中 | 低 | 视冲突率 |

### 适用 / 不适用场景

- **适用**：库存、对账、聚合等 **强一致串行**。
- **不适用**：完全独立可并行的子任务（应拆 Job）。

### 注意事项

- **类上注解**：影响所有该类型的 JobDetail 实例行为（以文档为准）。
- **性能**：持久化回写频率。

### 常见踩坑（生产案例）

1. **串行导致队列堆积**：根因是线程池过小 + 长任务。
2. **计数错误**：根因是 misfire 重入未幂等。
3. **注解不生效**：根因是 Job 类不是同一类型或多个类加载器。

#### 第09章思考题揭底

1. **不可序列化对象放入 JobDataMap**  
   **答**：**RAMJobStore** 可能在进程内勉强工作，但 **不推荐**；**JDBC JobStore** 在 **序列化/反序列化 JobDetail** 时会失败或抛异常，集群节点间也无法可靠传输。**应只放可持久化类型或外置引用**。

2. **大数据对集群影响**  
   **答**：**网络与数据库 BLOB 读写放大**；**锁持有时间变长**；**触发器恢复变慢**；可能导致 **GC 压力** 与 **misfire 级联**。应 **外置大对象**，map 中仅存 **ID 与版本**。

### 思考题（答案见下一章或 [答案索引](answers-index.md)）

1. `@DisallowConcurrentExecution` 与「单线程池」都能串行化，区别是什么？
2. `@PersistJobDataAfterExecution` 与有状态 Job 注解组合时应注意什么？

### 推广计划提示

- **测试**：并发与 misfire 组合压测。
- **运维**：观察 JDBC 更新频率与慢 SQL。
- **开发**：下一章进入 `quartz.properties` 与默认线程池。
