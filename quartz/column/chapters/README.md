# Quartz 专栏章节目录

本目录含 **42** 篇独立章节 + **[思考题与参考答案索引](answers-index.md)**，正文结构对齐 [专栏模板](../template.md)。

章节 Markdown 文件名与正文 H1 对齐，格式为 **第nn章：主题.md**。在 Windows（NTFS）下，标题里的半角 / 等非法路径字符已替换为全角 **／** 等，与正文中的半角写法可能略有差异，属正常现象。

与 [专栏模板](../template.md) 对齐：**第00章** 为术语与工作原理导读；**第14、29、41章** 为 **基础篇／中级篇／高级篇** 结束后的 **独立综合实战** 章节（四段式正文）。

| 章 | 文件 | 篇别 |
| --- | --- | --- |
| 00 | [第00章：术语地图与工作原理：从「谁、何时、记在哪」读懂 Quartz.md](第00章：术语地图与工作原理：从「谁、何时、记在哪」读懂 Quartz.md) | 基础篇 |
| 01 | [第01章：为什么需要 Quartz：与 Timer、ScheduledExecutor、Spring `@Scheduled` 的差异.md](第01章：为什么需要 Quartz：与 Timer、ScheduledExecutor、Spring `@Scheduled` 的差异.md) | 基础篇 |
| 02 | [第02章：第一个调度器：构建、启动、关闭.md](第02章：第一个调度器：构建、启动、关闭.md) | 基础篇 |
| 03 | [第03章：Scheduler ／ SchedulerFactory 生命周期与「先 schedule 再 start」语义.md](第03章：Scheduler ／ SchedulerFactory 生命周期与「先 schedule 再 start」语义.md) | 基础篇 |
| 04 | [第04章：JobDetail、Identity（name／group）与可维护命名规范.md](第04章：JobDetail、Identity（name／group）与可维护命名规范.md) | 基础篇 |
| 05 | [第05章：Trigger 总览：start／end、优先级占位、与 Job 绑定关系.md](第05章：Trigger 总览：start／end、优先级占位、与 Job 绑定关系.md) | 基础篇 |
| 06 | [第06章：SimpleTrigger：间隔、重复、结束策略.md](第06章：SimpleTrigger：间隔、重复、结束策略.md) | 基础篇 |
| 07 | [第07章：CronTrigger 与 Cron 表达式工程化（时区、DST）.md](第07章：CronTrigger 与 Cron 表达式工程化（时区、DST）.md) | 基础篇 |
| 08 | [第08章：CalendarIntervalTrigger ／ DailyTimeIntervalTrigger 选型.md](第08章：CalendarIntervalTrigger ／ DailyTimeIntervalTrigger 选型.md) | 基础篇 |
| 09 | [第09章：JobDataMap 传参、序列化与大小控制.md](第09章：JobDataMap 传参、序列化与大小控制.md) | 基础篇 |
| 10 | [第10章：并发与状态：`@DisallowConcurrentExecution`、`@PersistJobDataAfterExecution`.md](第10章：并发与状态：`@DisallowConcurrentExecution`、`@PersistJobDataAfterExecution`.md) | 基础篇 |
| 11 | [第11章：默认配置解读：`quartz.properties`（线程池、RAMJobStore、misfireThreshold）.md](第11章：默认配置解读：`quartz.properties`（线程池、RAMJobStore、misfireThreshold）.md) | 基础篇 |
| 12 | [第12章：优雅停机：`shutdown`、`waitForJobsToComplete`.md](第12章：优雅停机：`shutdown`、`waitForJobsToComplete`.md) | 基础篇 |
| 13 | [第13章：`HolidayCalendar` 等排除日历.md](第13章：`HolidayCalendar` 等排除日历.md) | 基础篇 |
| 14 | [第14章：基础篇综合实战：券投放与 RAM 调度串联验收.md](第14章：基础篇综合实战：券投放与 RAM 调度串联验收.md) | 基础篇 |
| 15 | [第15章：Misfire：阈值、`misfireInstruction`、各 Trigger 行为.md](第15章：Misfire：阈值、`misfireInstruction`、各 Trigger 行为.md) | 中级篇 |
| 16 | [第16章：有状态 Job 与数据回写语义（与 misfire 联动）.md](第16章：有状态 Job 与数据回写语义（与 misfire 联动）.md) | 中级篇 |
| 17 | [第17章：Job 执行异常与 `JobExecutionException`.md](第17章：Job 执行异常与 `JobExecutionException`.md) | 中级篇 |
| 18 | [第18章：`JobListener` ／ `TriggerListener` ／ `SchedulerListener` 与 Matcher.md](第18章：`JobListener` ／ `TriggerListener` ／ `SchedulerListener` 与 Matcher.md) | 中级篇 |
| 19 | [第19章：Job 链：`JobChainingJobListener`.md](第19章：Job 链：`JobChainingJobListener`.md) | 中级篇 |
| 20 | [第20章：可中断作业：`InterruptableJob` 与 `JobInterruptMonitorPlugin`.md](第20章：可中断作业：`InterruptableJob` 与 `JobInterruptMonitorPlugin`.md) | 中级篇 |
| 21 | [第21章：Trigger 优先级与饥饿.md](第21章：Trigger 优先级与饥饿.md) | 中级篇 |
| 22 | [第22章：JDBC JobStore：表结构、delegate、方言与索引.md](第22章：JDBC JobStore：表结构、delegate、方言与索引.md) | 中级篇 |
| 23 | [第23章：`JobStoreTX` vs `JobStoreCMT`：与 Spring／JTA 边界.md](第23章：`JobStoreTX` vs `JobStoreCMT`：与 Spring／JTA 边界.md) | 中级篇 |
| 24 | [第24章：数据源：`ConnectionProvider`、HikariCP／C3P0.md](第24章：数据源：`ConnectionProvider`、HikariCP／C3P0.md) | 中级篇 |
| 25 | [第25章：集群：`isClustered`、`instanceId`、多节点抢锁语义.md](第25章：集群：`isClustered`、`instanceId`、多节点抢锁语义.md) | 中级篇 |
| 26 | [第26章：`requestsRecovery` 与宕机恢复.md](第26章：`requestsRecovery` 与宕机恢复.md) | 中级篇 |
| 27 | [第27章：吞吐与线程池：`SimpleThreadPool`、批量拉取与调参.md](第27章：吞吐与线程池：`SimpleThreadPool`、批量拉取与调参.md) | 中级篇 |
| 28 | [第28章：RMI 远程调度模型与安全边界.md](第28章：RMI 远程调度模型与安全边界.md) | 中级篇 |
| 29 | [第29章：中级篇综合实战：对账集群与观测闭环演练.md](第29章：中级篇综合实战：对账集群与观测闭环演练.md) | 中级篇 |
| 30 | [第30章：`StdSchedulerFactory` 配置解析与对象装配全景.md](第30章：`StdSchedulerFactory` 配置解析与对象装配全景.md) | 高级篇 |
| 31 | [第31章：`QuartzScheduler` 与 `QuartzSchedulerThread` 主循环.md](第31章：`QuartzScheduler` 与 `QuartzSchedulerThread` 主循环.md) | 高级篇 |
| 32 | [第32章：`RAMJobStore`：内存结构、触发顺序.md](第32章：`RAMJobStore`：内存结构、触发顺序.md) | 高级篇 |
| 33 | [第33章：`JobStoreSupport`：拉取触发器、`Semaphore`、行锁.md](第33章：`JobStoreSupport`：拉取触发器、`Semaphore`、行锁.md) | 高级篇 |
| 34 | [第34章：`OperableTrigger` 与 misfire 计算关键路径.md](第34章：`OperableTrigger` 与 misfire 计算关键路径.md) | 高级篇 |
| 35 | [第35章：SPI 扩展点：`ThreadPool`、`JobStore`、`JobFactory`、`InstanceIdGenerator`.md](第35章：SPI 扩展点：`ThreadPool`、`JobStore`、`JobFactory`、`InstanceIdGenerator`.md) | 高级篇 |
| 36 | [第36章：`SchedulerPlugin` 与内置插件（XML 热加载、history、shutdown hook）.md](第36章：`SchedulerPlugin` 与内置插件（XML 热加载、history、shutdown hook）.md) | 高级篇 |
| 37 | [第37章：`XMLSchedulingDataProcessor`：校验、覆盖策略、生产发布.md](第37章：`XMLSchedulingDataProcessor`：校验、覆盖策略、生产发布.md) | 高级篇 |
| 38 | [第38章：`NativeJob` 与外部进程边界.md](第38章：`NativeJob` 与外部进程边界.md) | 高级篇 |
| 39 | [第39章：`quartz-jobs` 现成 Job 与二次封装.md](第39章：`quartz-jobs` 现成 Job 与二次封装.md) | 高级篇 |
| 40 | [第40章：极端场景与 SRE：时钟回拨、主从延迟、长事务、升级兼容.md](第40章：极端场景与 SRE：时钟回拨、主从延迟、长事务、升级兼容.md) | 高级篇 |
| 41 | [第41章：高级篇综合实战：工厂—线程—插件—发布的串联审计.md](第41章：高级篇综合实战：工厂—线程—插件—发布的串联审计.md) | 高级篇 |

## 重新生成正文骨架（慎用）

仓库内 **42 章正文已定稿** 时，**请勿执行** python column/_generate_chapters.py，否则会覆盖全部章节与 [answers-index.md](answers-index.md)。仅在大纲或文件名批量调整时由维护者有意运行，并务必 **Git diff 审阅**。
