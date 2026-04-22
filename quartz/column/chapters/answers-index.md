# Quartz 专栏思考题与参考答案索引

> **约定**：各章末尾 2 道思考题；揭底正文可写在「下一章 §4」或本索引。

## 总览表

| 出题章 | 思考题 | 参考答案位置 |
| --- | --- | --- |
| 第00章 [术语地图与工作原理：从「谁、何时、记在哪」读懂 Quartz](第00章：术语地图与工作原理：从「谁、何时、记在哪」读懂 Quartz.md) | Q1–Q2 | [第01章 §4「第00章思考题揭底」](第01章：为什么需要 Quartz：与 Timer、ScheduledExecutor、Spring `@Scheduled` 的差异.md#第00章思考题揭底) |
| 第01章 [为什么需要 Quartz：与 Timer、ScheduledExecutor、Spring `@Scheduled` 的差异](第01章：为什么需要 Quartz：与 Timer、ScheduledExecutor、Spring `@Scheduled` 的差异.md) | Q1–Q2 | [第02章 §4「第01章思考题揭底」](第02章：第一个调度器：构建、启动、关闭.md#第01章思考题揭底) |
| 第02章 [第一个调度器：构建、启动、关闭](第02章：第一个调度器：构建、启动、关闭.md) | Q1–Q2 | [第03章 §4「第02章思考题揭底」](第03章：Scheduler ／ SchedulerFactory 生命周期与「先 schedule 再 start」语义.md#第02章思考题揭底) |
| 第03章 [Scheduler / SchedulerFactory 生命周期与「先 schedule 再 start」语义](第03章：Scheduler ／ SchedulerFactory 生命周期与「先 schedule 再 start」语义.md) | Q1–Q2 | [第04章 §4「第03章思考题揭底」](第04章：JobDetail、Identity（name／group）与可维护命名规范.md#第03章思考题揭底) |
| 第04章 [JobDetail、Identity（name/group）与可维护命名规范](第04章：JobDetail、Identity（name／group）与可维护命名规范.md) | Q1–Q2 | [第05章 §4「第04章思考题揭底」](第05章：Trigger 总览：start／end、优先级占位、与 Job 绑定关系.md#第04章思考题揭底) |
| 第05章 [Trigger 总览：start/end、优先级占位、与 Job 绑定关系](第05章：Trigger 总览：start／end、优先级占位、与 Job 绑定关系.md) | Q1–Q2 | [第06章 §4「第05章思考题揭底」](第06章：SimpleTrigger：间隔、重复、结束策略.md#第05章思考题揭底) |
| 第06章 [SimpleTrigger：间隔、重复、结束策略](第06章：SimpleTrigger：间隔、重复、结束策略.md) | Q1–Q2 | [第07章 §4「第06章思考题揭底」](第07章：CronTrigger 与 Cron 表达式工程化（时区、DST）.md#第06章思考题揭底) |
| 第07章 [CronTrigger 与 Cron 表达式工程化（时区、DST）](第07章：CronTrigger 与 Cron 表达式工程化（时区、DST）.md) | Q1–Q2 | [第08章 §4「第07章思考题揭底」](第08章：CalendarIntervalTrigger ／ DailyTimeIntervalTrigger 选型.md#第07章思考题揭底) |
| 第08章 [CalendarIntervalTrigger / DailyTimeIntervalTrigger 选型](第08章：CalendarIntervalTrigger ／ DailyTimeIntervalTrigger 选型.md) | Q1–Q2 | [第09章 §4「第08章思考题揭底」](第09章：JobDataMap 传参、序列化与大小控制.md#第08章思考题揭底) |
| 第09章 [JobDataMap 传参、序列化与大小控制](第09章：JobDataMap 传参、序列化与大小控制.md) | Q1–Q2 | [第10章 §4「第09章思考题揭底」](第10章：并发与状态：`@DisallowConcurrentExecution`、`@PersistJobDataAfterExecution`.md#第09章思考题揭底) |
| 第10章 [并发与状态：`@DisallowConcurrentExecution`、`@PersistJobDataAfterExecution`](第10章：并发与状态：`@DisallowConcurrentExecution`、`@PersistJobDataAfterExecution`.md) | Q1–Q2 | [第11章 §4「第10章思考题揭底」](第11章：默认配置解读：`quartz.properties`（线程池、RAMJobStore、misfireThreshold）.md#第10章思考题揭底) |
| 第11章 [默认配置解读：`quartz.properties`（线程池、RAMJobStore、misfireThreshold）](第11章：默认配置解读：`quartz.properties`（线程池、RAMJobStore、misfireThreshold）.md) | Q1–Q2 | [第12章 §4「第11章思考题揭底」](第12章：优雅停机：`shutdown`、`waitForJobsToComplete`.md#第11章思考题揭底) |
| 第12章 [优雅停机：`shutdown`、`waitForJobsToComplete`](第12章：优雅停机：`shutdown`、`waitForJobsToComplete`.md) | Q1–Q2 | [第13章 §4「第12章思考题揭底」](第13章：`HolidayCalendar` 等排除日历.md#第12章思考题揭底) |
| 第13章 [`HolidayCalendar` 等排除日历](第13章：`HolidayCalendar` 等排除日历.md) | Q1–Q2 | [第14章 §4「第13章思考题揭底」](第14章：基础篇综合实战：券投放与 RAM 调度串联验收.md#第13章思考题揭底) |
| 第14章 [基础篇综合实战：券投放与 RAM 调度串联验收](第14章：基础篇综合实战：券投放与 RAM 调度串联验收.md) | Q1–Q2 | [第15章 §4「第14章思考题揭底」](第15章：Misfire：阈值、`misfireInstruction`、各 Trigger 行为.md#第14章思考题揭底) |
| 第15章 [Misfire：阈值、`misfireInstruction`、各 Trigger 行为](第15章：Misfire：阈值、`misfireInstruction`、各 Trigger 行为.md) | Q1–Q2 | [第16章 §4「第15章思考题揭底」](第16章：有状态 Job 与数据回写语义（与 misfire 联动）.md#第15章思考题揭底) |
| 第16章 [有状态 Job 与数据回写语义（与 misfire 联动）](第16章：有状态 Job 与数据回写语义（与 misfire 联动）.md) | Q1–Q2 | [第17章 §4「第16章思考题揭底」](第17章：Job 执行异常与 `JobExecutionException`.md#第16章思考题揭底) |
| 第17章 [Job 执行异常与 `JobExecutionException`](第17章：Job 执行异常与 `JobExecutionException`.md) | Q1–Q2 | [第18章 §4「第17章思考题揭底」](第18章：`JobListener` ／ `TriggerListener` ／ `SchedulerListener` 与 Matcher.md#第17章思考题揭底) |
| 第18章 [`JobListener` / `TriggerListener` / `SchedulerListener` 与 Matcher](第18章：`JobListener` ／ `TriggerListener` ／ `SchedulerListener` 与 Matcher.md) | Q1–Q2 | [第19章 §4「第18章思考题揭底」](第19章：Job 链：`JobChainingJobListener`.md#第18章思考题揭底) |
| 第19章 [Job 链：`JobChainingJobListener`](第19章：Job 链：`JobChainingJobListener`.md) | Q1–Q2 | [第20章 §4「第19章思考题揭底」](第20章：可中断作业：`InterruptableJob` 与 `JobInterruptMonitorPlugin`.md#第19章思考题揭底) |
| 第20章 [可中断作业：`InterruptableJob` 与 `JobInterruptMonitorPlugin`](第20章：可中断作业：`InterruptableJob` 与 `JobInterruptMonitorPlugin`.md) | Q1–Q2 | [第21章 §4「第20章思考题揭底」](第21章：Trigger 优先级与饥饿.md#第20章思考题揭底) |
| 第21章 [Trigger 优先级与饥饿](第21章：Trigger 优先级与饥饿.md) | Q1–Q2 | [第22章 §4「第21章思考题揭底」](第22章：JDBC JobStore：表结构、delegate、方言与索引.md#第21章思考题揭底) |
| 第22章 [JDBC JobStore：表结构、delegate、方言与索引](第22章：JDBC JobStore：表结构、delegate、方言与索引.md) | Q1–Q2 | [第23章 §4「第22章思考题揭底」](第23章：`JobStoreTX` vs `JobStoreCMT`：与 Spring／JTA 边界.md#第22章思考题揭底) |
| 第23章 [`JobStoreTX` vs `JobStoreCMT`：与 Spring/JTA 边界](第23章：`JobStoreTX` vs `JobStoreCMT`：与 Spring／JTA 边界.md) | Q1–Q2 | [第24章 §4「第23章思考题揭底」](第24章：数据源：`ConnectionProvider`、HikariCP／C3P0.md#第23章思考题揭底) |
| 第24章 [数据源：`ConnectionProvider`、HikariCP/C3P0](第24章：数据源：`ConnectionProvider`、HikariCP／C3P0.md) | Q1–Q2 | [第25章 §4「第24章思考题揭底」](第25章：集群：`isClustered`、`instanceId`、多节点抢锁语义.md#第24章思考题揭底) |
| 第25章 [集群：`isClustered`、`instanceId`、多节点抢锁语义](第25章：集群：`isClustered`、`instanceId`、多节点抢锁语义.md) | Q1–Q2 | [第26章 §4「第25章思考题揭底」](第26章：`requestsRecovery` 与宕机恢复.md#第25章思考题揭底) |
| 第26章 [`requestsRecovery` 与宕机恢复](第26章：`requestsRecovery` 与宕机恢复.md) | Q1–Q2 | [第27章 §4「第26章思考题揭底」](第27章：吞吐与线程池：`SimpleThreadPool`、批量拉取与调参.md#第26章思考题揭底) |
| 第27章 [吞吐与线程池：`SimpleThreadPool`、批量拉取与调参](第27章：吞吐与线程池：`SimpleThreadPool`、批量拉取与调参.md) | Q1–Q2 | [第28章 §4「第27章思考题揭底」](第28章：RMI 远程调度模型与安全边界.md#第27章思考题揭底) |
| 第28章 [RMI 远程调度模型与安全边界](第28章：RMI 远程调度模型与安全边界.md) | Q1–Q2 | [第29章 §4「第28章思考题揭底」](第29章：中级篇综合实战：对账集群与观测闭环演练.md#第28章思考题揭底) |
| 第29章 [中级篇综合实战：对账集群与观测闭环演练](第29章：中级篇综合实战：对账集群与观测闭环演练.md) | Q1–Q2 | [第30章 §4「第29章思考题揭底」](第30章：`StdSchedulerFactory` 配置解析与对象装配全景.md#第29章思考题揭底) |
| 第30章 [`StdSchedulerFactory` 配置解析与对象装配全景](第30章：`StdSchedulerFactory` 配置解析与对象装配全景.md) | Q1–Q2 | [第31章 §4「第30章思考题揭底」](第31章：`QuartzScheduler` 与 `QuartzSchedulerThread` 主循环.md#第30章思考题揭底) |
| 第31章 [`QuartzScheduler` 与 `QuartzSchedulerThread` 主循环](第31章：`QuartzScheduler` 与 `QuartzSchedulerThread` 主循环.md) | Q1–Q2 | [第32章 §4「第31章思考题揭底」](第32章：`RAMJobStore`：内存结构、触发顺序.md#第31章思考题揭底) |
| 第32章 [`RAMJobStore`：内存结构、触发顺序](第32章：`RAMJobStore`：内存结构、触发顺序.md) | Q1–Q2 | [第33章 §4「第32章思考题揭底」](第33章：`JobStoreSupport`：拉取触发器、`Semaphore`、行锁.md#第32章思考题揭底) |
| 第33章 [`JobStoreSupport`：拉取触发器、`Semaphore`、行锁](第33章：`JobStoreSupport`：拉取触发器、`Semaphore`、行锁.md) | Q1–Q2 | [第34章 §4「第33章思考题揭底」](第34章：`OperableTrigger` 与 misfire 计算关键路径.md#第33章思考题揭底) |
| 第34章 [`OperableTrigger` 与 misfire 计算关键路径](第34章：`OperableTrigger` 与 misfire 计算关键路径.md) | Q1–Q2 | [第35章 §4「第34章思考题揭底」](第35章：SPI 扩展点：`ThreadPool`、`JobStore`、`JobFactory`、`InstanceIdGenerator`.md#第34章思考题揭底) |
| 第35章 [SPI 扩展点：`ThreadPool`、`JobStore`、`JobFactory`、`InstanceIdGenerator`](第35章：SPI 扩展点：`ThreadPool`、`JobStore`、`JobFactory`、`InstanceIdGenerator`.md) | Q1–Q2 | [第36章 §4「第35章思考题揭底」](第36章：`SchedulerPlugin` 与内置插件（XML 热加载、history、shutdown hook）.md#第35章思考题揭底) |
| 第36章 [`SchedulerPlugin` 与内置插件（XML 热加载、history、shutdown hook）](第36章：`SchedulerPlugin` 与内置插件（XML 热加载、history、shutdown hook）.md) | Q1–Q2 | [第37章 §4「第36章思考题揭底」](第37章：`XMLSchedulingDataProcessor`：校验、覆盖策略、生产发布.md#第36章思考题揭底) |
| 第37章 [`XMLSchedulingDataProcessor`：校验、覆盖策略、生产发布](第37章：`XMLSchedulingDataProcessor`：校验、覆盖策略、生产发布.md) | Q1–Q2 | [第38章 §4「第37章思考题揭底」](第38章：`NativeJob` 与外部进程边界.md#第37章思考题揭底) |
| 第38章 [`NativeJob` 与外部进程边界](第38章：`NativeJob` 与外部进程边界.md) | Q1–Q2 | [第39章 §4「第38章思考题揭底」](第39章：`quartz-jobs` 现成 Job 与二次封装.md#第38章思考题揭底) |
| 第39章 [`quartz-jobs` 现成 Job 与二次封装](第39章：`quartz-jobs` 现成 Job 与二次封装.md) | Q1–Q2 | [第40章 §4「第39章思考题揭底」](第40章：极端场景与 SRE：时钟回拨、主从延迟、长事务、升级兼容.md#第39章思考题揭底) |
| 第40章 [极端场景与 SRE：时钟回拨、主从延迟、长事务、升级兼容](第40章：极端场景与 SRE：时钟回拨、主从延迟、长事务、升级兼容.md) | Q1–Q2 | [第41章 §4「第40章思考题揭底」](第41章：高级篇综合实战：工厂—线程—插件—发布的串联审计.md#第40章思考题揭底) |
| 第41章 [高级篇综合实战：工厂—线程—插件—发布的串联审计](第41章：高级篇综合实战：工厂—线程—插件—发布的串联审计.md) | Q1–Q2 | [第41章正文 §「第41章思考题揭底」](第41章：高级篇综合实战：工厂—线程—插件—发布的串联审计.md#第41章思考题揭底) |

## 第41章思考题揭底（索引镜像）

正文以最后一章综合实战文件为准；此处便于全文搜索。

## 跨章引用维护

- 修改章节编号时，请同步更新本表锚点与各章「思考题揭底」小节标题。

