# 第09章：JobDataMap 传参、序列化与大小控制

> **篇别**：基础篇  
> **建议篇幅**：3000–5000 字（含对话与代码）  
> **结构约束**：对齐 [专栏模板](../template.md) 四段式。

## 示例锚点

| 类型 | 路径 |
| --- | --- |
| example4 | [ColorJob.java](../../examples/src/main/java/org/quartz/examples/example4/ColorJob.java) |
| example4 | [JobStateExample.java](../../examples/src/main/java/org/quartz/examples/example4/JobStateExample.java) |

## 1 项目背景（约 500 字）

### 业务场景

渠道网关要为不同商户调度「拉单任务」，参数包括 **商户 ID、API 版本、限流阈值**。团队最初把大 JSON 塞进 `JobDataMap`，JDBC 持久化后出现 **写入膨胀、反序列化失败**；又有开发把 **不可序列化的 `HttpClient` 实例** 放入 map 导致集群序列化异常。本章说明：**JobDataMap 的定位是「小而稳定的配置快照」**，不是通用缓存。

### 痛点放大

- **RAM vs JDBC**：内存模式宽松；持久化模式要求 **可序列化** 或 **Quartz 支持的类型**。
- **大对象**：每次触发携带巨量字节，拖慢 **数据库与网络**。
- **与成员变量混用**：example4 的 `ColorJob` 明确注释：**不要用非静态成员保存状态**（应使用 map 或注解状态）。

## 2 项目设计（约 1200 字）

**角色**：小胖 · 小白 · 大师

---

**小胖**：`JobDataMap` 不就是个 `HashMap` 吗？我把整个 Spring `ApplicationContext` 塞进去行不行？

**小白**：`JobDataMap` 和 `Trigger` 上的 `JobDataMap` 合并规则是什么？同名键谁覆盖谁？

**大师**：把它想成 **外卖订单小票上的备注栏**——只写「少辣、不要葱」，不会把整本菜谱贴上去。Quartz 在触发时会 **`JobDataMap` 合并**（JobDetail 与 Trigger 两侧），**Trigger 的值通常覆盖 JobDetail**（以文档为准）。备注栏太大，打印机卡纸；不可序列化对象， JDBC 集群根本 **寄不出包裹**。

**技术映射**：**`JobDataMap` 持久化语义 + `JobExecutionContext.getMergedJobDataMap()`**。

---

**小胖**：那我放 `byte[]` 总行吧？

**小白**：集群下每次执行回写 map 的频率如何？会不会写穿数据库？

**大师**：若使用 **`@PersistJobDataAfterExecution`**，每次执行结束都可能 **回写状态**（第10章）。大 payload + 高频触发 = **数据库热点**。正确做法是 **map 里只放主键/版本号**，执行时再从 **DB/Redis** 拉全量。

**技术映射**：**短引用 + 外置大对象**。

---

**小胖**：example4 里 `_counter` 成员变量为啥「没用」？

**小白**：那和 `count` 在 `JobDataMap` 里递增有什么本质区别？

**大师**：Quartz 每次可能 **new 一个新的 Job 实例** 执行（取决于 `JobFactory`），成员变量像 **临时工名牌上的手写 tally**——换人就丢。`JobDataMap` 里配合 **`@PersistJobDataAfterExecution`** 的计数，才是 **写回人事系统的考勤记录**。

**技术映射**：**无状态 Job 实例 + 有状态数据在 JobDataMap / 外部存储**。

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

阅读 [JobStateExample.java](../../examples/src/main/java/org/quartz/examples/example4/JobStateExample.java) 与 [ColorJob.java](../../examples/src/main/java/org/quartz/examples/example4/ColorJob.java)。

### 分步实现

**步骤 1：目标** —— 在 `JobDetail` 上放入初始化参数（与 example 一致）。

```java
JobDetail job1 = newJob(ColorJob.class).withIdentity("job1", "group1").build();
job1.getJobDataMap().put(ColorJob.FAVORITE_COLOR, "Green");
job1.getJobDataMap().put(ColorJob.EXECUTION_COUNT, 1);
```

**验证**：首次执行日志 `execution count ... is 1`。

**步骤 2：目标** —— 在 Job 内读取 **合并后的** map。

```java
public void execute(JobExecutionContext context) {
    JobDataMap data = context.getMergedJobDataMap();
    String color = data.getString(ColorJob.FAVORITE_COLOR);
    // ...
}
```

**验证**：若 Trigger 也放入同名键，观察覆盖行为（自建实验）。

**步骤 3：目标** —— JDBC 持久化前自检序列化。

```java
job.getJobDataMap().put("tenantId", 10001L);
// 避免：job.getJobDataMap().put("ctx", applicationContext);
```

**验证**：开启 `JobStoreTX` 后（第21章），任务应能正常恢复。

### 可能踩坑

| 坑 | 解决 |
| --- | --- |
| 放入非 Serializable | 仅放 DTO/基本类型/字符串 |
| 巨大 JSON | 外置对象存储，map 存 key |
| 误解成员变量状态 | 读 ColorJob 注释 |

### 完整代码清单

- [JobStateExample.java](../../examples/src/main/java/org/quartz/examples/example4/JobStateExample.java)
- [ColorJob.java](../../examples/src/main/java/org/quartz/examples/example4/ColorJob.java)

### 测试验证

单测序列化：`serialize(job.getJobDataMap())` 或触发一次持久化恢复流程。

## 4 项目总结（约 500–800 字）

### 优点与缺点（对比同类技术）

| 维度 | JobDataMap | 外部 DB | Spring 注入 |
| --- | --- | --- | --- |
| 与调度元数据同生命周期 | 是 | 否 | 中 |
| 大对象 | 差 | 好 | 好 |
| 集群一致性 | 需序列化 | 天然 | 视设计 |

### 适用 / 不适用场景

- **适用**：小型配置、分片索引、执行计数。
- **不适用**：大报文、连接句柄、不可序列化框架对象。

### 注意事项

- **键名规范**：常量化，避免魔法字符串。
- **类型安全**：使用 `getInt`/`getString` 等明确 API。

### 常见踩坑（生产案例）

1. **塞 HttpServletRequest**：集群反序列化炸；根因是误解 map 用途。
2. **大 JSON 写 BLOB**：DB 膨胀；根因是未外置。
3. **成员变量当状态**：重启丢；根因是未读官方示例注释。

#### 第08章思考题揭底

1. **CalendarInterval 与 Cron 在「按月滚动」**  
   **答**：**CalendarInterval** 以 **日历字段** 为步进，语义贴近「每 N 个月同一天附近」的业务账单；**Cron** 用表达式描述固定格子，跨月边界需手工维护，可读性依赖团队水平。**复杂滚动 + 月末规则** 往往 CalendarInterval 更不易写错；**固定日历格子** Cron 更紧凑。

2. **何时更推荐 DailyTimeIntervalTrigger**  
   **答**：当需求是 **「一天内多个时间窗 + 固定步长」**（如营业时间内每 10 分钟）且希望 **结构化 API 而非长 Cron 字符串** 时；需要 **与 Cron 混合编排** 时也可组合使用。

### 思考题（答案见下一章或 [答案索引](answers-index.md)）

1. `JobDataMap` 中放入不可序列化对象会有什么后果（RAM vs JDBC）？
2. 大数据放入 `JobDataMap` 对集群有什么影响？

### 推广计划提示

- **测试**：序列化与大小边界单测。
- **运维**：监控 DB 中 JOB 相关 BLOB 增长。
- **开发**：下一章精读 `ColorJob` 注解与并发语义。
