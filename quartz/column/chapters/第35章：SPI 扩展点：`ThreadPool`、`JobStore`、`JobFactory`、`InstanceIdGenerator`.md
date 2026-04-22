# 第35章：SPI 扩展点：`ThreadPool`、`JobStore`、`JobFactory`、`InstanceIdGenerator`

> **篇别**：高级篇  
> **建议篇幅**：3000–5000 字（含对话与代码）  
> **结构约束**：对齐 [专栏模板](../template.md) 四段式。

## 示例锚点

| 类型 | 路径 |
| --- | --- |
| 包 | [org.quartz.spi](../../quartz/src/main/java/org/quartz/spi) |

## 1 项目背景（约 500 字）

### 业务场景

平台组希望 **统一注入 Spring Bean 到 Job**，需要自定义 **`JobFactory`**；基础设施希望 **基于 K8s Pod UID 生成 `instanceId`**，需要自定义 **`InstanceIdGenerator`**；性能组希望 **用托管线程池替换 `SimpleThreadPool`**。这些都落在 **SPI 包 `org.quartz.spi`** 与相关 **`org.quartz.simpl` 默认实现** 的替换上。

### 痛点放大

- **未实现完整 SPI 契约**：运行期 `ClassCastException`。
- **JobFactory 与作用域**：prototype vs request（Web 谨慎）。
- **线程池与 ClassLoader**：插件与隔离。

## 2 项目设计（约 1200 字）

**角色**：小胖 · 小白 · 大师

---

**小胖**：SPI 听起来像插件，跟 Spring 的 `@Autowired` 啥关系？

**小白**：`AdaptableJobFactory` 是不是官方方案？

**大师**：`JobFactory` 像 **「工人上岗前的工具分发处」**——调度器 new 出 Job 实例后，**由工厂决定要不要注入 Spring**。Spring Quartz 集成里有成熟样板；自定义时要小心 **循环依赖与线程安全**。

**技术映射**：**`JobFactory#newJob(TriggerFiredBundle, Scheduler)`**。

---

**小胖**：我能写个 `NoOpJobStore` 吗？

**小白**：测试里替换 JobStore 会不会太狠？

**大师**：可以，但 **要保证测试覆盖真实 JobStore 路径**；否则 **「测试全绿，上线全红」**。

**技术映射**：**测试替身 vs 集成测试分层**。

---

**小胖**：`InstanceIdGenerator` 用 UUID 会有性能问题吗？

**小白**：需要全局单调吗？

**大师**：**唯一性** 优先于 **可读性**；性能通常不是瓶颈。**可读 id** 交给日志 **MDC** 映射。

**技术映射**：**`org.quartz.spi.InstanceIdGenerator`**。

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

浏览 [spi 包](../../quartz/src/main/java/org/quartz/spi) 接口列表：`ThreadPool`、`JobStore`、`JobFactory`、`InstanceIdGenerator`、`ClassLoadHelper`…

### 分步实现

**步骤 1：目标** —— 最小 **`JobFactory`** 注入：

```java
public class SpringJobFactory implements JobFactory {
    private final AutowireCapableBeanFactory beanFactory;
    @Override
    public Job newJob(TriggerFiredBundle bundle, Scheduler scheduler) throws SchedulerException {
        Class<? extends Job> c = bundle.getJobDetail().getJobClass();
        return beanFactory.createBean(c);
    }
}
```

**步骤 2：目标** —— `scheduler.setJobFactory(...)`。

**步骤 3：目标** —— 自定义 **`InstanceIdGenerator`** 并在 properties 声明类名（键名以文档为准）。

### 可能踩坑

| 坑 | 解决 |
| --- | --- |
| Job 非 public 无参构造 | 规范 |
| Bean 单例被并发 Job 共享 | scope 管理 |
| ClassLoader 泄漏 | 销毁钩子 |

### 完整代码清单

- [spi/JobFactory.java](../../quartz/src/main/java/org/quartz/spi/JobFactory.java)

### 测试验证

Spring 测试上下文 + RAMScheduler 集成。

## 4 项目总结（约 500–800 字）

### 优点与缺点（对比同类技术）

| 维度 | SPI 扩展 | Fork 源码 |
| --- | --- | --- |
| 可维护性 | 高 | 低 |
| 自由度 | 中 | 最高 |

### 适用 / 不适用场景

- **适用**：平台型封装、企业标准集成。
- **不适用**：一次性功能（避免过度工程）。

### 注意事项

- **升级兼容**：SPI 变更阅读 release notes。
- **安全**：JobFactory 不要执行不可信类名。

### 常见踩坑（生产案例）

1. **请求作用域 Bean 进 Job**：根因是生命周期错配。
2. **自定义 ThreadPool 泄漏**：根因是未 shutdown。
3. **instanceId 碰撞**：根因是错误随机源。

#### 第34章思考题揭底

1. **misfire 计算触发前还是后**  
   **答**：**在调度器检测到错过触发点之后、更新 Trigger 状态并准备下一次调度之前** 调用 **`updateAfterMisfire`** 来 **修正时间线**；并非在业务 `Job.execute` 内部。精确时序以 **`QuartzSchedulerThread` + `JobStore`** 调用栈为准。

2. **与业务日历联动注意**  
   **答**：**`HolidayCalendar` 变更** 会改变 **合法 fire 集合**；`updateAfterMisfire` 重算 **必须与当前 calendar 绑定一致**；要避免 **在日历切换窗口内出现「跳过/重复」与业务解释不一致**；需 **集成测试覆盖**。

### 思考题（答案见下一章或 [答案索引](answers-index.md)）

1. 自定义 `ThreadPool` 需要满足哪些契约？
2. `JobFactory` 与 Spring 注入结合的关键接口方法？

### 推广计划提示

- **测试**：SPI 合约单测。
- **运维**：标准化镜像内 Quartz 扩展白名单。
- **开发**：下一章插件体系。
