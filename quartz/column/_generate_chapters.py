# -*- coding: utf-8 -*-
"""Skeleton generator for column/chapters/*.md.

警告：仓库内章节正文已定稿时，请勿运行本脚本，否则会覆盖 `column/chapters/*.md`
与 `answers-index.md`。仅在大纲结构调整后由维护者有意执行。
运行：python column/_generate_chapters.py
"""
import os

BASE = os.path.join(os.path.dirname(__file__), "chapters")
os.makedirs(BASE, exist_ok=True)


def windows_safe_filename(s: str) -> str:
    """NTFS / Windows：将文件名非法字符（含半角 / : * 等）替换为全角安全字符。"""
    trans = str.maketrans(
        {
            "\\": "＼",
            "/": "／",
            ":": "：",
            "*": "＊",
            "?": "？",
            '"': "＂",
            "<": "＜",
            ">": "＞",
            "|": "｜",
        }
    )
    s = s.translate(trans)
    s = "".join(ch for ch in s if ord(ch) >= 32 and ch not in "\r\n\t")
    return s.rstrip(" .")


def chapter_stem(num: int, title: str) -> str:
    return windows_safe_filename(f"第{num:02d}章：{title}")


def md_table(rows):
    if not rows:
        return "_（本章以概念为主，无固定 example 入口。）_\n"
    lines = ["| 类型 | 路径 |", "| --- | --- |"]
    for r in rows:
        lines.append(f"| {r[0]} | {r[1]} |")
    return "\n".join(lines) + "\n"


# 元组第二列 slug 仅作历史标识；实际文件名由 chapter_stem(num, title) 生成（与正文 H1 一致）。
CH = [
    (1, "01-why-quartz", "为什么需要 Quartz：与 Timer、ScheduledExecutor、Spring `@Scheduled` 的差异", "基础篇", [
        ("概念", "[readme.adoc](../../readme.adoc)"),
        ("文档索引", "[docs/index.md](../../docs/index.md)"),
    ]),
    (2, "02-first-scheduler", "第一个调度器：构建、启动、关闭", "基础篇", [
        ("example1", "[SimpleExample.java](../../examples/src/main/java/org/quartz/examples/example1/SimpleExample.java)"),
        ("example1", "[HelloJob.java](../../examples/src/main/java/org/quartz/examples/example1/HelloJob.java)"),
    ]),
    (3, "03-scheduler-lifecycle", "Scheduler / SchedulerFactory 生命周期与「先 schedule 再 start」语义", "基础篇", [
        ("example1", "[SimpleExample.java](../../examples/src/main/java/org/quartz/examples/example1/SimpleExample.java)"),
        ("源码", "[StdSchedulerFactory.java](../../quartz/src/main/java/org/quartz/impl/StdSchedulerFactory.java)"),
    ]),
    (4, "04-jobdetail-identity", "JobDetail、Identity（name/group）与可维护命名规范", "基础篇", [
        ("example1", "[SimpleExample.java](../../examples/src/main/java/org/quartz/examples/example1/SimpleExample.java)"),
        ("API", "`org.quartz.JobBuilder`"),
    ]),
    (5, "05-trigger-overview", "Trigger 总览：start/end、优先级占位、与 Job 绑定关系", "基础篇", [
        ("铺垫", "[example2 目录](../../examples/src/main/java/org/quartz/examples/example2)"),
        ("API", "`org.quartz.TriggerBuilder`"),
    ]),
    (6, "06-simple-trigger", "SimpleTrigger：间隔、重复、结束策略", "基础篇", [
        ("example2", "[SimpleTriggerExample.java](../../examples/src/main/java/org/quartz/examples/example2/SimpleTriggerExample.java)"),
        ("example2", "[SimpleJob.java](../../examples/src/main/java/org/quartz/examples/example2/SimpleJob.java)"),
    ]),
    (7, "07-cron-trigger", "CronTrigger 与 Cron 表达式工程化（时区、DST）", "基础篇", [
        ("example3", "[CronTriggerExample.java](../../examples/src/main/java/org/quartz/examples/example3/CronTriggerExample.java)"),
        ("example3", "[SimpleJob.java](../../examples/src/main/java/org/quartz/examples/example3/SimpleJob.java)"),
    ]),
    (8, "08-calendar-interval-triggers", "CalendarIntervalTrigger / DailyTimeIntervalTrigger 选型", "基础篇", [
        ("源码", "[CalendarIntervalTriggerImpl.java](../../quartz/src/main/java/org/quartz/impl/triggers/CalendarIntervalTriggerImpl.java)"),
        ("源码", "[DailyTimeIntervalTriggerImpl.java](../../quartz/src/main/java/org/quartz/impl/triggers/DailyTimeIntervalTriggerImpl.java)"),
    ]),
    (9, "09-jobdatamap", "JobDataMap 传参、序列化与大小控制", "基础篇", [
        ("example4", "[ColorJob.java](../../examples/src/main/java/org/quartz/examples/example4/ColorJob.java)"),
        ("example4", "[JobStateExample.java](../../examples/src/main/java/org/quartz/examples/example4/JobStateExample.java)"),
    ]),
    (10, "10-concurrency-stateful", "并发与状态：@DisallowConcurrentExecution、@PersistJobDataAfterExecution", "基础篇", [
        ("example4", "[JobStateExample.java](../../examples/src/main/java/org/quartz/examples/example4/JobStateExample.java)"),
    ]),
    (11, "11-quartz-properties", "默认配置解读：quartz.properties（线程池、RAMJobStore、misfireThreshold）", "基础篇", [
        ("配置", "[quartz.properties](../../quartz/src/main/resources/org/quartz/quartz.properties)"),
        ("源码", "[StdSchedulerFactory.java](../../quartz/src/main/java/org/quartz/impl/StdSchedulerFactory.java)"),
    ]),
    (12, "12-graceful-shutdown", "优雅停机：shutdown、waitForJobsToComplete", "基础篇", [
        ("example1", "[SimpleExample.java](../../examples/src/main/java/org/quartz/examples/example1/SimpleExample.java)"),
        ("API", "`org.quartz.Scheduler#shutdown`"),
    ]),
    (13, "13-holiday-calendar", "HolidayCalendar 等排除日历", "基础篇", [
        ("example8", "[CalendarExample.java](../../examples/src/main/java/org/quartz/examples/example8/CalendarExample.java)"),
        ("example8", "[SimpleJob.java](../../examples/src/main/java/org/quartz/examples/example8/SimpleJob.java)"),
    ]),
    (14, "14-misfire", "Misfire：阈值、misfireInstruction、各 Trigger 行为", "中级篇", [
        ("example5", "[MisfireExample.java](../../examples/src/main/java/org/quartz/examples/example5/MisfireExample.java)"),
        ("example5", "[StatefulDumbJob.java](../../examples/src/main/java/org/quartz/examples/example5/StatefulDumbJob.java)"),
    ]),
    (15, "15-stateful-misfire", "有状态 Job 与数据回写语义（与 misfire 联动）", "中级篇", [
        ("example5", "[MisfireExample.java](../../examples/src/main/java/org/quartz/examples/example5/MisfireExample.java)"),
    ]),
    (16, "16-job-exception", "Job 执行异常与 JobExecutionException", "中级篇", [
        ("example6", "[JobExceptionExample.java](../../examples/src/main/java/org/quartz/examples/example6/JobExceptionExample.java)"),
    ]),
    (17, "17-listeners", "JobListener / TriggerListener / SchedulerListener 与 Matcher", "中级篇", [
        ("example9", "[ListenerExample.java](../../examples/src/main/java/org/quartz/examples/example9/ListenerExample.java)"),
    ]),
    (18, "18-job-chaining", "Job 链：JobChainingJobListener", "中级篇", [
        ("源码", "[JobChainingJobListener.java](../../quartz/src/main/java/org/quartz/listeners/JobChainingJobListener.java)"),
    ]),
    (19, "19-interruptable-job", "可中断作业：InterruptableJob 与 JobInterruptMonitorPlugin", "中级篇", [
        ("example7", "[InterruptExample.java](../../examples/src/main/java/org/quartz/examples/example7/InterruptExample.java)"),
        ("插件", "[JobInterruptMonitorPlugin.java](../../quartz/src/main/java/org/quartz/plugins/interrupt/JobInterruptMonitorPlugin.java)"),
    ]),
    (20, "20-trigger-priority", "Trigger 优先级与饥饿", "中级篇", [
        ("example14", "[PriorityExample.java](../../examples/src/main/java/org/quartz/examples/example14/PriorityExample.java)"),
    ]),
    (21, "21-jdbc-jobstore", "JDBC JobStore：表结构、delegate、方言与索引", "中级篇", [
        ("源码", "[JobStoreTX.java](../../quartz/src/main/java/org/quartz/impl/jdbcjobstore/JobStoreTX.java)"),
        ("包", "[impl/jdbcjobstore](../../quartz/src/main/java/org/quartz/impl/jdbcjobstore)"),
    ]),
    (22, "22-jobstore-tx-cmt", "JobStoreTX vs JobStoreCMT：与 Spring/JTA 边界", "中级篇", [
        ("源码", "[JobStoreTX.java](../../quartz/src/main/java/org/quartz/impl/jdbcjobstore/JobStoreTX.java)"),
        ("源码", "[JobStoreCMT.java](../../quartz/src/main/java/org/quartz/impl/jdbcjobstore/JobStoreCMT.java)"),
    ]),
    (23, "23-datasource-provider", "数据源：ConnectionProvider、HikariCP/C3P0", "中级篇", [
        ("源码", "[HikariCpPoolingConnectionProvider.java](../../quartz/src/main/java/org/quartz/utils/HikariCpPoolingConnectionProvider.java)"),
        ("源码", "[C3p0PoolingConnectionProvider.java](../../quartz/src/main/java/org/quartz/utils/C3p0PoolingConnectionProvider.java)"),
    ]),
    (24, "24-clustering", "集群：isClustered、instanceId、多节点抢锁语义", "中级篇", [
        ("example13", "[ClusterExample.java](../../examples/src/main/java/org/quartz/examples/example13/ClusterExample.java)"),
    ]),
    (25, "25-requests-recovery", "requestsRecovery 与宕机恢复", "中级篇", [
        ("example13", "[SimpleRecoveryJob.java](../../examples/src/main/java/org/quartz/examples/example13/SimpleRecoveryJob.java)"),
        ("example13", "[SimpleRecoveryStatefulJob.java](../../examples/src/main/java/org/quartz/examples/example13/SimpleRecoveryStatefulJob.java)"),
    ]),
    (26, "26-throughput-threadpool", "吞吐与线程池：SimpleThreadPool、批量拉取与调参", "中级篇", [
        ("example11", "[LoadExample.java](../../examples/src/main/java/org/quartz/examples/example11/LoadExample.java)"),
        ("源码", "[SimpleThreadPool.java](../../quartz/src/main/java/org/quartz/simpl/SimpleThreadPool.java)"),
    ]),
    (27, "27-rmi-remoting", "RMI 远程调度模型与安全边界", "中级篇", [
        ("example12", "[RemoteServerExample.java](../../examples/src/main/java/org/quartz/examples/example12/RemoteServerExample.java)"),
        ("example12", "[RemoteClientExample.java](../../examples/src/main/java/org/quartz/examples/example12/RemoteClientExample.java)"),
    ]),
    (28, "28-std-scheduler-factory", "StdSchedulerFactory 配置解析与对象装配全景", "高级篇", [
        ("源码", "[StdSchedulerFactory.java](../../quartz/src/main/java/org/quartz/impl/StdSchedulerFactory.java)"),
    ]),
    (29, "29-quartz-scheduler-thread", "QuartzScheduler 与 QuartzSchedulerThread 主循环", "高级篇", [
        ("源码", "[QuartzScheduler.java](../../quartz/src/main/java/org/quartz/core/QuartzScheduler.java)"),
        ("源码", "[QuartzSchedulerThread.java](../../quartz/src/main/java/org/quartz/core/QuartzSchedulerThread.java)"),
    ]),
    (30, "30-ram-jobstore", "RAMJobStore：内存结构、触发顺序", "高级篇", [
        ("源码", "[RAMJobStore.java](../../quartz/src/main/java/org/quartz/simpl/RAMJobStore.java)"),
    ]),
    (31, "31-jobstore-support-locking", "JobStoreSupport：拉取触发器、Semaphore、行锁", "高级篇", [
        ("源码", "[JobStoreSupport.java](../../quartz/src/main/java/org/quartz/impl/jdbcjobstore/JobStoreSupport.java)"),
    ]),
    (32, "32-operable-trigger-misfire", "OperableTrigger 与 misfire 计算关键路径", "高级篇", [
        ("源码", "[AbstractTrigger.java](../../quartz/src/main/java/org/quartz/impl/triggers/AbstractTrigger.java)"),
        ("SPI", "[OperableTrigger.java](../../quartz/src/main/java/org/quartz/spi/OperableTrigger.java)"),
    ]),
    (33, "33-spi-extensions", "SPI 扩展点：ThreadPool、JobStore、JobFactory、InstanceIdGenerator", "高级篇", [
        ("包", "[org.quartz.spi](../../quartz/src/main/java/org/quartz/spi)"),
    ]),
    (34, "34-scheduler-plugins", "SchedulerPlugin 与内置插件（XML 热加载、history、shutdown hook）", "高级篇", [
        ("example10", "[PlugInExample.java](../../examples/src/main/java/org/quartz/examples/example10/PlugInExample.java)"),
        ("包", "[org.quartz.plugins](../../quartz/src/main/java/org/quartz/plugins)"),
    ]),
    (35, "35-xml-scheduling-data", "XMLSchedulingDataProcessor：校验、覆盖策略、生产发布", "高级篇", [
        ("源码", "[XMLSchedulingDataProcessor.java](../../quartz/src/main/java/org/quartz/xml/XMLSchedulingDataProcessor.java)"),
    ]),
    (36, "36-native-job", "NativeJob 与外部进程边界", "高级篇", [
        ("example15", "[NativeJobExample.java](../../examples/src/main/java/org/quartz/examples/example15/NativeJobExample.java)"),
        ("example15", "[NativeJob.java](../../examples/src/main/java/org/quartz/examples/example15/NativeJob.java)"),
    ]),
    (37, "37-quartz-jobs-module", "quartz-jobs 现成 Job 与二次封装", "高级篇", [
        ("模块", "[quartz-jobs](../../quartz-jobs)"),
    ]),
    (38, "38-extreme-sre", "极端场景与 SRE：时钟回拨、主从延迟、长事务、升级兼容", "高级篇", [
        ("综合", "`jdbcjobstore` + 运维案例（无单一 example）"),
    ]),
]

Q = [
    ("请对比 Quartz 与单机 ScheduledExecutor 在「持久化」与「misfire」上的差异边界。", "若业务要求「应用重启后任务不丢」，本章选型应如何调整？"),
    ("写出从 StdSchedulerFactory 获取 Scheduler 并 start 的最小步骤（伪代码即可）。", "scheduleJob 在 start 之前与之后调用，语义上有何不同？"),
    ("为何 Quartz 通常建议显式 shutdown，而不是依赖 JVM 退出？", "多 Scheduler 实例时，instanceName 应如何规划？"),
    ("JobKey 与 TriggerKey 同名不同组是否允许？冲突时会发生什么？", "若把 group 全部设为 DEFAULT，生产上有什么风险？"),
    ("同一个 JobDetail 能否被多个 Trigger 驱动？典型用例是什么？", "Trigger 的 endTime 到期后，关联的 JobDetail 是否会被自动删除？"),
    ("SimpleTrigger 的 repeatCount 与 repeatForever 如何取舍？", "若间隔小于线程执行时间，会出现什么现象？"),
    ("写出一个「每工作日 9:00」的 Cron，并说明时区应配置在哪里。", "夏令时切换日 Cron 可能踩坑，如何避免？"),
    ("CalendarIntervalTrigger 与 CronTrigger 在「按月滚动」场景各有什么优劣？", "什么情况下更推荐 DailyTimeIntervalTrigger？"),
    ("JobDataMap 中放入不可序列化对象会有什么后果（RAM vs JDBC）？", "大数据放入 JobDataMap 对集群有什么影响？"),
    ("@DisallowConcurrentExecution 与「单线程池」都能串行化，区别是什么？", "@PersistJobDataAfterExecution 与有状态 Job 注解组合时应注意什么？"),
    ("org.quartz.threadPool.threadCount 过小会导致什么可观测现象？", "misfireThreshold 增大能否「消除」misfire？"),
    ("shutdown(true) 与 shutdown(false) 对正在执行的 Job 分别意味着什么？", "如何实现「最多等 30 秒，超时强制停」？"),
    ("HolidayCalendar 与 Cron 排除语法相比，何时更应使用 Calendar？", "多个 Trigger 共享同一 Calendar 时要注意什么？"),
    ("什么是 misfire？它与「线程池满」有何不同？", "MISFIRE_INSTRUCTION_FIRE_NOW 可能带来什么业务副作用？"),
    ("有状态 Job 在 misfire 后多次执行，如何保证幂等？", "PersistJobDataAfterExecution 与 JDBC JobStore 组合时的写入频率问题？"),
    ("JobExecutionException 的 refireImmediately 与「快速重试」区别？", "若 Job 吞掉异常不抛出，调度器行为如何？"),
    ("三类 Listener 的触发顺序与事务边界关系（概念上）？", "全局 Listener 与局部 Matcher 同时存在时如何合并？"),
    ("Job 链与「一个 Job 内顺序调用」相比，失败隔离上有何优势？", "如何避免链式监听导致的无限循环？"),
    ("协作式中断为何不能保证毫秒级结束？", "JobInterruptMonitorPlugin 解决的是什么痛点？"),
    ("高优先级 Trigger 长期占用线程时，低优先级可能出现什么？", "除 priority 外，还有什么手段缓解饥饿？"),
    ("Quartz JDBC 表中最容易成为热点的是哪类行？", "delegate 选错会导致什么典型 SQL 错误？"),
    ("什么场景必须选 JobStoreCMT 而不是 JobStoreTX？", "Spring @Transactional 与 Quartz Job 事务边界如何切分（概念）？"),
    ("为何 Quartz 需要独立的 ConnectionProvider 而不是每次裸 DriverManager？", "HikariCP 的 maximumPoolSize 与 threadCount 应如何配比（经验法则）？"),
    ("集群模式下「同一任务会执行两次」的根因通常有哪些？", "instanceId 若冲突会发生什么？"),
    ("requestsRecovery 标记如何影响宕机后的触发？", "有状态 Job 做 recovery 时要额外注意什么？"),
    ("batchTriggerAcquisitionMaxCount 调大对 DB 与延迟分别有什么影响？", "LoadExample 对生产的启示是什么？"),
    ("RMI 暴露 Scheduler 的主要安全风险是什么？", "若必须远程控制，更现代的替代架构是什么？"),
    ("StdSchedulerFactory 初始化时最关键的 3 类组件是什么？", "同名 quartz.properties 在 classpath 多处时加载顺序？"),
    ("主循环中「时间推进」与「拉取触发器」大致如何协作？", "QuartzSchedulerThread 阻塞与唤醒的主要条件？"),
    ("RAMJobStore 下 misfire 行为与 JDBC 是否一致？", "RAMJobStore 的瓶颈通常在 CPU 还是锁？"),
    ("Semaphore 在 JDBC JobStore 中的作用？", "StdRowLockSemaphore 与 SimpleSemaphore 选型差异？"),
    ("misfire 计算发生在「触发前」还是「触发后」？", "updateAfterMisfire 与业务日历联动时要注意什么？"),
    ("自定义 ThreadPool 需要满足哪些契约？", "JobFactory 与 Spring 注入结合的关键接口方法？"),
    ("XMLSchedulingDataProcessorPlugin 的文件扫描频率过高会怎样？", "history 插件对日志量的影响？"),
    ("overwriteExistingJobs 为 true/false 在生产各适用什么发布策略？", "XML 校验失败时 Scheduler 会处于什么状态？"),
    ("NativeJob 相比 Java Job 的主要运维成本？", "如何限制外部脚本的资源与超时？"),
    ("quartz-jobs 中 FileScanJob 的典型用途？", "对邮件 Job 做二次封装时应隔离哪些配置？"),
    ("时钟回拨对 Quartz 集群的典型影响？", "大版本升级 Job 类不兼容时如何灰度？"),
]

assert len(Q) == 38


def body(num, slug, title, tier, anchors, prev_num):
    t1, t2 = Q[num - 1]
    prev_answers = ""
    if num > 1:
        pa1, pa2 = Q[num - 2]
        prev_answers = f"""
#### 第{prev_num:02d}章思考题揭底

1. **{pa1}**  
   _（定稿时展开：结合第{prev_num:02d}章正文「项目设计」中的技术映射写 3–6 句。）_
2. **{pa2}**  
   _（定稿时展开。）_

"""
    return f"""# 第{num:02d}章：{title}

> **篇别**：{tier}  
> **建议篇幅**：3000–5000 字（含对话与代码）  
> **结构约束**：对齐 [专栏模板](../template.md) 四段式。

## 示例锚点

{md_table(anchors)}

## 1 项目背景（约 500 字）

- **业务场景**：_（撰写：拟真需求，引出本章主题。）_
- **痛点放大**：_（撰写：无该技术时的性能/一致性/可维护性问题；可插入 Mermaid 流程图。）_

## 2 项目设计（约 1200 字）

**角色**：小胖（生活化提问）· 小白（边界与风险）· 大师（选型与比方）

- **对话结构**：小胖开球 1–2 轮 → 小白追问 2–3 轮 → 大师解答并引出子话题，循环 2–3 次。
- **技术映射**：每轮后大师用一句话把比方映射到 Quartz 术语（Scheduler / Trigger / JobStore 等）。

_（撰写：完整剧本式对话。）_

## 3 项目实战（约 1500–2000 字）

### 环境准备

- JDK / Gradle 版本与仓库子模块：`quartz`、`examples`。
- _（撰写：依赖坐标或 examples 运行方式。）_

### 分步实现

| 步骤 | 目标 | 代码/命令要点 | 验证 |
| --- | --- | --- | --- |
| 1 | _（一句话）_ | _（带注释片段，可指向本仓库路径）_ | _（日志/断言/curl）_ |

### 可能踩坑

- _（坑 1 + 解决）_
- _（坑 2 + 解决）_

### 完整代码清单

- 本仓库：`examples`、`quartz` 模块源码。
- _（定稿时可附外部 gist 链接。）_

## 4 项目总结（约 500–800 字）

### 优点与缺点（对比同类技术）

| 维度 | Quartz | 同类技术（如 Spring @Scheduled / K8s CronJob） |
| --- | --- | --- |
| _（填写）_ | _（填写）_ | _（填写）_ |

### 适用 / 不适用场景

- **适用**：_（3–5 条）_
- **不适用**：_（1–2 条）_

### 注意事项

- _（配置陷阱、版本兼容、安全边界）_

### 常见踩坑（生产案例）

1. _（案例 + 根因）_
2. _（案例 + 根因）_
3. _（案例 + 根因）_

{prev_answers}
### 思考题（答案见下一章或 [答案索引](answers-index.md)）

1. {t1}
2. {t2}

### 推广计划提示

- **测试**：_（本章验收点）_
- **运维**：_（配置与观测）_
- **开发**：_（扩展阅读路径）_
"""


def main():
    for i, (num, slug, title, tier, anchors) in enumerate(CH):
        prev_num = CH[i - 1][0] if i else None
        content = body(num, slug, title, tier, anchors, prev_num)
        path = os.path.join(BASE, chapter_stem(num, title) + ".md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    lines = [
        "# Quartz 专栏思考题与参考答案索引",
        "",
        "> **约定**：各章末尾 2 道思考题；揭底正文可写在「下一章 §4」或本索引。",
        "",
        "## 总览表",
        "",
        "| 出题章 | 思考题 | 参考答案位置 |",
        "| --- | --- | --- |",
    ]
    for i in range(38):
        n = i + 1
        stem = chapter_stem(n, CH[i][2])
        title_link = f"[{CH[i][2]}]({stem}.md)"
        if n < 38:
            nxt_stem = chapter_stem(n + 1, CH[i + 1][2])
            loc = f"[第{n + 1:02d}章 §4「第{n:02d}章思考题揭底」]({nxt_stem}.md#第{n:02d}章思考题揭底)"
        else:
            loc = f"[第38章正文 §「第38章思考题揭底」]({stem}.md#第38章思考题揭底)"
        lines.append(f"| 第{n:02d}章 {title_link} | Q1–Q2 | {loc} |")

    lines.extend(
        [
            "",
            "## 末章（第38章）思考题揭底",
            "",
            "### 第38章 思考题1",
            "",
            "**题**：时钟回拨对 Quartz 集群的典型影响？",
            "",
            "_（定稿：结合 NTP、系统时间、集群锁 TTL / 触发时间比较等撰写。）_",
            "",
            "### 第38章 思考题2",
            "",
            "**题**：大版本升级 Job 类不兼容时如何灰度？",
            "",
            "_（定稿：类版本、JobDataMap 迁移、双写调度、feature flag 等。）_",
            "",
            "## 跨章引用维护",
            "",
            "- 修改章节编号时，请同步更新本表锚点与各章「思考题揭底」小节标题。",
            "",
        ]
    )

    with open(os.path.join(BASE, "answers-index.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print("Wrote", len(CH) + 1, "files under", BASE)


if __name__ == "__main__":
    main()
