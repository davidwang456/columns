# 第22章：JDBC JobStore：表结构、delegate、方言与索引

> **篇别**：中级篇  
> **建议篇幅**：3000–5000 字（含对话与代码）  
> **结构约束**：对齐 [专栏模板](../template.md) 四段式。

## 示例锚点

| 类型 | 路径 |
| --- | --- |
| 源码 | [JobStoreTX.java](../../quartz/src/main/java/org/quartz/impl/jdbcjobstore/JobStoreTX.java) |
| 包 | [impl/jdbcjobstore](../../quartz/src/main/java/org/quartz/impl/jdbcjobstore) |

## 1 项目背景（约 500 字）

### 业务场景

调度任务需 **应用重启后不丢**、支持 **多实例部署**。团队决定启用 **JDBC JobStore**，DBA 要求：给出 **官方表结构脚本**、明确 **delegate 方言**、并为 **`NEXT_FIRE_TIME` 等查询列建索引**。本章说明：**表前缀、`DriverDelegate`、不同数据库的锁与 SQL 差异**，为第22–25章打地基。

### 痛点放大

- **表未建全或版本不匹配**：Scheduler 启动失败或运行期 NPE。
- **delegate 选错**：SQL 方言函数不兼容。
- **索引缺失**：集群抢 Trigger 时 **全表扫**。

## 2 项目设计（约 1200 字）

**角色**：小胖 · 小白 · 大师

---

**小胖**：把任务写 Redis 不行吗？为啥一定要一堆 `QRTZ_*` 表？

**小白**：热点行在哪？会不会把数据库打挂？

**大师**：JDBC JobStore 像 **「把排班表贴在中央公告栏」**——所有实例抬头看同一张表，靠 **行锁/信号量** 抢下一班岗。热点通常在 **触发器拉取与状态更新** 相关行；所以 **索引与批量拉取参数** 是生命线。

**技术映射**：**`JobStoreSupport` + `DriverDelegate` SQL**。

---

**小胖**：`JobStoreTX` 名字里有 TX，是不是每次触发一个事务？

**小白**：和 Spring `@Transactional` 包在一起会怎样？

**大师**：TX 表示 **Quartz 自己管理 JDBC 事务边界**（第22章对比 CMT）。Spring 声明式事务 **不会自动套在 Quartz 内部锁逻辑上**，错误假设会导致 **「以为在同一事务」**。

**技术映射**：**JobStoreTX vs JobStoreCMT**。

---

**小胖**：MySQL 和 Postgres  delegate 能混用吗？

**小白**：云上 RDS 只读副本能不能给 Quartz 查？

**大师**：**delegate 必须与真实方言一致**；**只读副本不适合抢锁写路径**——调度主路径需要 **可写主库** 或 **官方支持的集群语义**（否则脑裂）。

**技术映射**：**主库 + 正确 delegate**。

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

- 准备空 schema；从 Quartz 发行包或文档获取 **`tables_*.sql`**（以你使用的 Quartz 版本为准）。
- 引入 JDBC 驱动；配置 `quartz.properties`：

```properties
org.quartz.jobStore.class = org.quartz.impl.jdbcjobstore.JobStoreTX
org.quartz.jobStore.driverDelegateClass = org.quartz.impl.jdbcjobstore.StdJDBCDelegate
org.quartz.jobStore.dataSource = myDS
org.quartz.dataSource.myDS.driver = com.mysql.cj.jdbc.Driver
org.quartz.dataSource.myDS.URL = jdbc:mysql://localhost:3306/quartz
org.quartz.dataSource.myDS.user = quartz
org.quartz.dataSource.myDS.password = ***
```

### 分步实现

**步骤 1：目标** —— 执行建表脚本并启动 RAM 改 JDBC 的最小应用。

**步骤 2：目标** —— 在 DB 中观察 **`QRTZ_TRIGGERS`**（或当前版本表名）行的 **`NEXT_FIRE_TIME`** 更新。

**步骤 3：目标** —— 用 `EXPLAIN` 分析 **acquire next triggers** 相关 SQL（需打开 Quartz DEBUG SQL 日志，注意脱敏）。

### 可能踩坑

| 坑 | 解决 |
| --- | --- |
| 表前缀与多租户 | `tablePrefix` |
| 时区与 TIMESTAMP | 统一 UTC 存储 |
| 连接池过小 | 调 `threadCount` 配比（第23章） |

### 完整代码清单

- [JobStoreTX.java](../../quartz/src/main/java/org/quartz/impl/jdbcjobstore/JobStoreTX.java)
- 官方 SQL 脚本（随发行版）

### 测试验证

集成测试：嵌入式 DB（如 H2/Derby）跑通 **schedule → restart → recover**。

## 4 项目总结（约 500–800 字）

### 优点与缺点（对比同类技术）

| 维度 | JDBC JobStore | RAM | DB 外自建 |
| --- | --- | --- | --- |
| 持久化 | 是 | 否 | 自定 |
| 运维复杂度 | 中 | 低 | 高 |

### 适用 / 不适用场景

- **适用**：需要重启恢复、集群协调。
- **不适用**：极轻量、可丢任务。

### 注意事项

- **版本升级**：表结构迁移脚本。
- **安全**：DB 账号最小权限 + 网络 ACL。

### 常见踩坑（生产案例）

1. **索引漏建导致 CPU 100%**：根因是 EXPLAIN 未做。
2. **delegate 错用 Oracle 语法在 MySQL**：根因是复制粘贴配置。
3. **用从库跑调度**：根因是误解读写分离。

#### 第21章思考题揭底

1. **高优长期占用线程，低优现象**  
   **答**：**饥饿**：低优 Trigger **长期得不到执行**，**misfire 堆积**，业务表现为 **报表/归档「永远晚一天」**；严重时 **状态机卡死**。

2. **除 priority 外缓解饥饿**  
   **答**：**增加 `threadCount`**、**拆分 Scheduler**、**降低高优频率**、**把重任务外移 MQ**、**错峰窗口**、**单独线程池隔离**（自定义 ThreadPool，高级篇）。

### 思考题（答案见下一章或 [答案索引](answers-index.md)）

1. Quartz JDBC 表中最容易成为热点的是哪类行？
2. delegate 选错会导致什么典型 SQL 错误？

### 推广计划提示

- **测试**：DB 集成与迁移测试。
- **运维**：慢 SQL 周报。
- **开发**：下一章对比 TX/CMT。
