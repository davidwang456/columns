# 第24章：数据源：`ConnectionProvider`、HikariCP/C3P0

> **篇别**：中级篇  
> **建议篇幅**：3000–5000 字（含对话与代码）  
> **结构约束**：对齐 [专栏模板](../template.md) 四段式。

## 示例锚点

| 类型 | 路径 |
| --- | --- |
| 源码 | [HikariCpPoolingConnectionProvider.java](../../quartz/src/main/java/org/quartz/utils/HikariCpPoolingConnectionProvider.java) |
| 源码 | [C3p0PoolingConnectionProvider.java](../../quartz/src/main/java/org/quartz/utils/C3p0PoolingConnectionProvider.java) |

## 1 项目背景（约 500 字）

### 业务场景

JDBC JobStore 上线后，出现 **`Cannot get connection`、线程阻塞、`getTriggerState` 超时**——根因常是 **裸 `DriverManager` 或连接池 `maximumPoolSize` 远小于 `threadCount`**。本仓库提供 **`HikariCpPoolingConnectionProvider` 与 `C3p0PoolingConnectionProvider`**，用于把 Quartz **`dataSource` 配置** 与 **成熟池化** 对齐。

### 痛点放大

- **每请求新建连接**：吞吐崩溃。
- **池过大**：DB 端连接数打满。
- **与 Spring 池混用同一物理池**：调试困难。

## 2 项目设计（约 1200 字）

**角色**：小胖 · 小白 · 大师

---

**小胖**：Spring 已经有 `DataSource` Bean，Quartz 为啥还要 `ConnectionProvider`？

**小白**：两个池子会不会把连接数翻倍？

**大师**：`ConnectionProvider` 是 **「Quartz 取 JDBC 的唯一端口」**——你可以接 **Hikari、C3P0、或桥接到 Spring 的 `DataSource`**（视集成方式）。**双池** 确实可能 **翻倍连接**，因此常见做法是 **Quartz 独占一个池** 或 **桥接共享同一底层池**（需确保 **生命周期与关闭顺序**）。

**技术映射**：**`org.quartz.utils.ConnectionProvider`**。

---

**小胖**：`threadCount=50`，连接池开 10，会怎样？

**小白**：等待连接算 misfire 吗？

**大师**：像 **「50 个工人只有 10 把铲子」**——工人排队等铲子，**触发点被错过** 后仍可能 **判 misfire**（取决于阈值与实现）。这不是「线程池满」唯一形态，但表现类似。

**技术映射**：**`maximumPoolSize` ≥ 活跃调度需求 + 余量**。

---

**小胖**：Hikari 和 C3P0 选谁？

**小白**：公司规范只批了 C3P0 怎么办？

**大师**：从 **维护活跃度与性能** 看，新项目多偏向 **Hikari**；若遗留系统已深度绑定 C3P0，**优先统一运维规范**，再规划迁移。Quartz 侧 **抽象一致**，切换主要在 **properties 类名**。

**技术映射**：**`org.quartz.dataSource.*` 前缀配置**。

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

在 `quartz.properties` 中配置 **Hikari provider 类**（以 Quartz 文档示例为准），关键键通常包括：**driver、URL、user、password、maxConnections** 等。

### 分步实现

**步骤 1：目标** —— 使用 Hikari provider（伪配置，按版本调整）：

```properties
org.quartz.jobStore.class = org.quartz.impl.jdbcjobstore.JobStoreTX
org.quartz.jobStore.dataSource = qzDS
org.quartz.dataSource.qzDS.provider = hikaricp
# 其余 URL/user/password/maxConnections 按文档填写
```

**步骤 2：目标** —— 压测：固定 `threadCount`，从 `maxConnections=5` 逐步上调，记录 **misfire 率** 与 **获取连接等待时间**。

**步骤 3：目标** —— 在 Spring 中 **桥接**：自定义 `ConnectionProvider` 包装已有 `DataSource` Bean（注意 **非托管关闭**）。

### 可能踩坑

| 坑 | 解决 |
| --- | --- |
| 连接泄漏 | try-with-resources 在 delegate 内 |
| shutdown 顺序 | 先 Quartz 再池 |
| 密码明文 | 环境变量替换 |

### 完整代码清单

- [HikariCpPoolingConnectionProvider.java](../../quartz/src/main/java/org/quartz/utils/HikariCpPoolingConnectionProvider.java)

### 测试验证

Micrometer/Hikari metrics：`Active connections`、`Pending threads`。

## 4 项目总结（约 500–800 字）

### 优点与缺点（对比同类技术）

| 维度 | 池化 Provider | 无池 |
| --- | --- | --- |
| 稳定性 | 高 | 低 |
| 配置成本 | 中 | 低 |

### 适用 / 不适用场景

- **适用**：所有 JDBC JobStore 生产部署。
- **不适用**：RAM 模式（无需 JDBC 池）。

### 注意事项

- **云 RDS max_connections**。
- **网络抖动重试策略**（在池或驱动层）。

### 常见踩坑（生产案例）

1. **池过小**：根因是拍脑袋配置。
2. **双池**：根因是架构未评审。
3. **泄漏**：根因是自定义 delegate 未关闭。

#### 第23章思考题揭底

1. **何时必须 CMT**  
   **答**：当应用运行在 **托管事务环境**（如部分 Java EE 服务器）且 **要求 Quartz JDBC 访问参与容器管理事务** 时，应使用 **`JobStoreCMT`** 并配置 **非托管连接语义由容器接管**（以官方文档为准）。纯 Spring Boot 内嵌 Tomcat **通常用 `JobStoreTX`**。

2. **Spring `@Transactional` 与 Job 边界**  
   **答**：**`Job.execute` 运行在 Quartz worker 线程**，默认 **不在 Spring 事务模板内**；若需事务，应在 Job 内 **注入 `TransactionTemplate` 或自调用带事务的 Service**，并明确 **哪些写操作与 Quartz 元数据同事务/分事务**。

### 思考题（答案见下一章或 [答案索引](answers-index.md)）

1. 为何 Quartz 需要独立的 `ConnectionProvider` 而不是每次裸 `DriverManager`？
2. HikariCP 的 `maximumPoolSize` 与 `threadCount` 应如何配比（经验法则）？

### 推广计划提示

- **测试**：连接等待与 misfire 联合压测。
- **运维**：RDS 连接数告警。
- **开发**：下一章 example13 集群。
