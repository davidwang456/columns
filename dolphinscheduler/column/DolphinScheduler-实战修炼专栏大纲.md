# Apache DolphinScheduler 实战修炼专栏大纲

> 版本：DolphinScheduler 3.x（基于官方最新源码）
> 面向人群：大数据开发、运维、数据架构师、测试
> 总章节：38 章（基础篇 15 章 / 中级篇 13 章 / 高级篇 10 章）
> 每章独立成文件，字数 3000–5000 字

---

## 专栏定位

以 DolphinScheduler 官方源码为骨架，从核心概念到集群部署，从任务调度到工作流编排，从架构设计到源码深度剖析，从性能调优到生产落地，全链路贯通。每一章均采用「业务痛点 → 三人剧本对话 → 代码实战 → 总结思考」的四段式结构，兼顾趣味性、实战性与深度。

---

## 阅读路线建议

| 角色 | 建议阅读顺序 | 重点章节 |
|------|-------------|---------|
| 新人开发/测试 | 基础篇全读 → 中级篇选读 | 第 1–15 章 |
| 核心开发/运维 | 基础篇速读 → 中级篇精读 → 高级篇选读 | 第 16–28、29–38 章 |
| 架构师/资深开发 | 高级篇为主线，按需回溯中级篇 | 第 29–38 章，辅以 16–28 章 |

---

# 基础篇（第 1–15 章）

> **核心目标**：建立 DolphinScheduler 核心概念，掌握单机部署、常用任务类型开发、基本工作流编排与初级故障排查。

---

## 第 1 章：DolphinScheduler 术语全景与分布式调度架构原理

**定位**：专栏总览与开篇，建立统一语系。

**核心内容**：
- 术语词典：DAG、Task、Workflow、Process Instance、Task Instance、Command、Resource Center、Tenant、Queue、Worker Group、Alert Group
- 分布式调度架构全景图：API → Master → Worker → External Systems 四层模型
- 核心服务职责：ApiServer（入口）、MasterServer（编排引擎）、WorkerServer（任务执行器）、AlertServer（告警中心）
- 注册中心（Zookeeper/Etcd）的角色：服务发现、Leader 选举、分布式锁
- 元数据库（MySQL/PostgreSQL）的表体系概览
- 调度引擎核心流程：定时触发 → Command 入队 → Master 消费 → DAG 状态机 → Worker 执行 → 状态回调

**实战目标**：绘制一张 DolphinScheduler 整体架构图，输出到团队 Wiki。

---

## 第 2 章：单机快速部署与开发环境搭建

**定位**：让 DolphinScheduler 先跑起来。

**核心内容**：
- Standalone 模式 vs 集群模式的分工差异
- Docker Compose 一键部署（含 Zookeeper + MySQL + DS）
- 二进制包手动安装：解压 → 建库 → 修改配置 → 初始化 → 启动
- dolphinscheduler_env.sh 环境变量配置（JAVA_HOME、PYTHON_HOME、HADOOP_HOME）
- 源码编译构建：mvnw clean install -Prelease 全流程
- 开发环境搭建（IDE 导入多模块 Maven 工程、debug 配置）

**实战目标**：在本地搭建一套 Standalone 开发环境，启动后访问 Dashboard UI 验证。

---

## 第 3 章：调度中心控制台总览

**定位**：熟悉 UI 操作界面与核心功能导航。

**核心内容**：
- 首页仪表盘：项目数、流程数、实例状态分布、24h 执行统计
- 项目管理：创建/删除/权限分配
- 工作流定义：编辑画布、保存、导入导出
- 工作流实例：运行状态、甘特图、重跑/恢复/停止/暂停
- 任务实例：日志查看、强制成功、上下游追溯
- 定时管理：定时记录的查看/上线/下线
- 安全中心：租户/用户/告警组/队列/Token/Worker 分组管理
- 数据源中心：MySQL、Hive、ClickHouse 等数据源注册
- 监控中心：Master/Worker 节点状态、服务健康度

**实战目标**：创建第一个项目，完整遍历所有功能菜单，输出功能导航脑图。

---

## 第 4 章：工作流 DAG 设计与基本编排

**定位**：从画布到运行——上手第一个工作流。

**核心内容**：
- DAG 基本概念：节点、边、入度/出度、拓扑排序
- 画布操作：拖拽节点、连线、组合、复制粘贴
- 工作流核心配置：失败策略、通知策略、流程优先级、Worker 分组、超时告警
- 全局变量：set 与 ${}，上游参数传递
- 运行状态机：SUBMITTED → RUNNING_EXECUTION → SUCCESS/FAILURE/PAUSE/STOP

**实战目标**：创建一个包含 Shell + Python 两个节点的串行 DAG，跑通并查看日志。

---

## 第 5 章：Shell 与 Python 任务节点实战

**定位**：最常用的脚本型任务类型。

**核心内容**：
- Shell 节点：脚本编写规范、资源文件引用、退出码处理
- Python 节点：解释器配置（dolphinscheduler_env.sh 的 PYTHON_HOME）、包依赖管理
- 自定义参数：IN/OUT 方向、${set} 语法、上下游传递机制
- 内置系统参数：system.biz.date、system.biz.curdate、system.datetime
- Worker 执行原理：shell_executor / python_executor 的进程创建与日志采集

**实战目标**：编写 Shell 节点（数据清洗脚本） + Python 节点（数据分析脚本），传递日期参数，跑通上下游。

---

## 第 6 章：SQL 任务节点与数据源管理

**定位**：大数据调度最核心的任务类型。

**核心内容**：
- 数据源中心：注册 MySQL / Hive / PostgreSQL / ClickHouse 等数据源
- SQL 节点类型：查询 SQL vs 非查询 SQL（update/insert/delete）
- SQL 节点结构：前置 SQL → 主要 SQL → 后置 SQL 三段式
- SQL 结果邮件发送：查询结果以附件/表格形式发送
- UDF 函数管理：注册 Hive UDF / Spark UDF

**实战目标**：配置 MySQL 数据源 → 编写每日 ETL SQL，跑通并邮件接收结果。

---

## 第 7 章：定时调度与 Cron 表达式精讲

**定位**：让调度自动化运转起来。

**核心内容**：
- Cron 表达式语法：秒/分/时/日/月/周/年七个字段详解
- 常用 Cron 模板：每小时、每天、每周、每月、每年
- 定时启动/结束时间配置
- 定时配置的通知策略与工作流启动通知策略的区别
- 定时管理页面的设计意图：定时与流程定义的解耦
- Quartz 调度器内核简介

**实战目标**：为一个日报 ETL 流程配置"每天凌晨 2:00 执行"，验证定时触发成功。

---

## 第 8 章：依赖（Dependent）节点与跨流程编排

**定位**：解决流程间的依赖问题。

**核心内容**：
- Dependent 节点的本质：对另一个工作流实例的状态检查
- 依赖配置：项目 + 工作流 + 依赖周期（昨日/今日/本周/上周/本月）
- 复杂依赖逻辑：AND/OR 组合条件
- 日/周/月报告场景：周报需要上周每一天的日报都成功
- 依赖节点与补数的协同工作

**实战目标**：构建 A（日 ETL）→ B（日报）→ C（周报，依赖 B 上周 7 天均成功）的三级依赖链。

---

## 第 9 章：条件分支（Conditions）节点与动态路由

**定位**：让工作流根据运行时状态做出选择。

**核心内容**：
- Conditions 节点的逻辑判断：常用比较运算符
- 条件分支结构：成功分支 / 失败分支
- 自定义参数做分支条件：上游输出 → 下游条件判断 → 走不同分支
- 典型场景：数据量阈值检查
- 多条件组合（AND/OR）的配置技巧与陷阱

**实战目标**：构建带条件分支的工作流：检查昨天数据量 → 大于阈值走报表分支，小于阈值走告警分支。

---

## 第 10 章：子流程（SubProcess）节点与工作流复用

**定位**：模块化工作流，避免重复拖拽。

**核心内容**：
- SubProcess 节点：把父流程的一部分逻辑封装为子流程
- 父子流程参数传递：全局参数 vs SubProcess 节点参数
- 子流程的独立性：独立的流程定义、实例、日志
- 典型场景：多个报表都依赖同一个数据清洗子流程
- 子流程层级嵌套的注意事项

**实战目标**：封装数据清洗为子流程，在日报表和月报表两个父流程中引用。

---

## 第 11 章：告警系统与通知渠道配置

**定位**：出了问题能第一时间知道。

**核心内容**：
- AlertServer 架构：告警服务独立部署，插件化告警渠道
- 告警渠道类型：邮件、企业微信、钉钉、飞书、Slack、Telegram、HTTP
- 告警组 vs 收件人 vs 抄送人的区别
- 告警触发场景：任务超时、任务失败、工作流失败
- 超时告警的两种策略：超时告警（仅通知） vs 超时失败（告警+标记失败）

**实战目标**：配置企业微信告警渠道，设置任务超时 5 分钟告警，模拟超时场景验证告警消息接收。

---

## 第 12 章：资源中心与文件管理

**定位**：统一管理所有脚本和 JAR 包。

**核心内容**：
- 资源中心架构：本地文件系统 vs HDFS vs S3 vs OSS（Storage Plugin 体系）
- 文件管理：上传/下载/编辑/改目录/删除
- 文件类型：脚本、JAR 包、配置文件
- 任务节点引用资源：Shell 引用脚本、Spark 引用 JAR 包
- Python 资源引用：需为目录添加 __init__.py 文件
- UDF 管理：上传 JAR → 创建函数 → SQL 节点引用

**实战目标**：上传一个 PySpark JAR 包到资源中心，创建 Spark 任务节点引用它。

---

## 第 13 章：用户权限与多租户管理

**定位**：多人协作场景下的安全隔离。

**核心内容**：
- 用户体系：admin 管理员 / 普通用户 / 权限矩阵
- 租户：对应 Linux 操作系统用户，任务以租户身份运行
- Worker 分组：指定任务在哪些 Worker 上执行
- 队列管理：按 YARN/K8s 队列分配计算资源
- 令牌管理：API 调用 Token 的创建与权限
- 项目级权限：用户对指定项目的读/写/执行权限

**实战目标**：创建 3 个角色（管理员 + 开发 + 运维），分配不同项目和权限，验证权限隔离。

---

## 第 14 章：补数机制与历史任务执行

**定位**：历史数据回刷与数据修复。

**核心内容**：
- 补数的概念：为历史时间范围生成并执行流程实例
- 串行补数 vs 并行补数：执行方式的差异与适用场景
- 补数与依赖节点的协同：补数时 Dependent 节点的行为
- 补数的适用场景：数据修复、新表初始化、算法模型回刷
- 补数的风险：并行补数对下游系统的冲击

**实战目标**：为一个日报流程配置过去 7 天的补数任务，对比串行/并行两种模式。

---

## 第 15 章：【基础篇综合实战】构建电商数据中台调度体系

**定位**：融会贯通基础篇知识。

**核心内容**：
- 场景：为一家电商公司搭建完整的数据中台调度体系
- 需求拆解：日 ETL、日报、周报、告警、权限隔离
- 分步实现：创建项目 → 注册数据源 → 上传资源 → 编排 DAG → 配置定时 → 配置告警
- 验收标准：所有流程自动运行一周无故障，补数 30 天数据不出错

---

# 中级篇（第 16–28 章）

> **核心目标**：掌握集群化部署、高级任务类型、参数体系、调度策略、性能调优与可观测性。

---

## 第 16 章：集群架构部署与高可用设计

**定位**：从单机到集群的生产级部署。

**核心内容**：
- 集群组件角色分配：Master × 2 + Worker × 3 + API × 2 + Alert × 1
- Zookeeper 注册中心：节点注册、Master Leader 选举、分布式锁
- MySQL 元数据库高可用方案
- install.sh 部署脚本解读：配置分发、服务安装、进程启动
- 集群扩缩容：新增/下线 Worker 节点
- 从单机迁移到集群的操作步骤与注意事项

**实战目标**：在 3 节点集群上完成全组件部署，验证多 Worker 负载均衡。

---

## 第 17 章：Spark 与 MapReduce 任务节点深度实战

**定位**：大数据批处理任务的调度编排。

**核心内容**：
- Spark 节点：spark-submit 方式提交，程序类型（Java/Scala/Python）
- JAR 包管理：上传到资源中心 → 任务节点引用
- 参数配置：driver-memory、executor-memory、num-executors
- MapReduce 节点：hadoop jar 提交方式
- YARN 集成：队列指定、应用 ID 追踪
- Spark on K8s 的支持与配置

**实战目标**：编写 Spark 离线 ETL 程序 → 上传 JAR → 配置调度日执行 → 查看 YARN 执行日志。

---

## 第 18 章：Flink 实时任务调度与常驻任务管理

**定位**：实时流处理任务的调度挑战。

**核心内容**：
- Flink 节点：flink run / flink run-application 两种模式
- Flink 常驻任务的特点：任务提交后工作流一直处于"运行中"
- Flink 任务的生命周期管理：启停、重启、Savepoint 保存与恢复
- 在 DS 中使用 Flink 的场景分析
- Flink 任务失败重试与告警的特殊处理

**实战目标**：在 DS 中提交 Flink 实时流任务，配置 Savepoint，模拟故障后从 Savepoint 恢复。

---

## 第 19 章：DataX 与 Sqoop 数据同步节点实战

**定位**：异构数据源之间的桥梁。

**核心内容**：
- DataX 节点：模板生成方式 vs JSON 自定义方式
- DataX 的 Reader/Writer 插件体系概览
- Sqoop 节点：RDBMS ↔ Hadoop 的数据导入导出
- 增量数据同步方案：基于时间戳/自增 ID 的增量策略
- 数据同步中的常见坑：字符集、类型映射、NULL 值处理

**实战目标**：配置 DataX 节点每天从 MySQL 增量同步订单表到 Hive。

---

## 第 20 章：HTTP 节点与外部系统集成

**定位**：用调度器编排外部 API 调用。

**核心内容**：
- HTTP 节点的请求方法：GET / POST / PUT / DELETE / HEAD
- 请求体构造：JSON / Form / 自定义 Body
- 动态参数传递：将上游输出注入到 HTTP 请求头/Body
- 响应处理与条件判断：根据 HTTP 状态码/响应体决定下游分支
- 认证配置：Token、Basic Auth 等

**实战目标**：构建工作流，定时调用天气 API 获取数据 → 写入 MySQL → 生成报表。

---

## 第 21 章：全局参数与变量体系深度解析

**定位**：参数传递是工作流编排的灵魂。

**核心内容**：
- 内置系统参数大全：system.biz.date、system.biz.curdate、system.datetime
- 自定义参数：${setVar=IN/OUT}、同工作流上下游传递
- 全局参数 vs 本地参数的作用域与优先级
- 启动参数：启动流程实例时覆盖/补充全局参数
- Worker 端参数解析的内部机制

**实战目标**：构建三级工作流链，每一级产生一个参数传递给下一级，端到端验证参数传递。

---

## 第 22 章：Worker 分组与多租户资源隔离

**定位**：大规模集群下的资源管理策略。

**核心内容**：
- Worker 分组的本质：标签化 Worker 节点，任务按标签分配
- 工作流级 Worker 分组 vs 任务级 Worker 分组的关系与优先级
- 租户隔离：Linux 用户 → 任务执行身份 → HDFS 权限
- 队列管理：YARN 队列的指定与资源隔离
- Master 分发逻辑与 Worker 负载均衡

**实战目标**：配置 2 个 Worker 分组（ETL 组 / 报表组），验证不同分组任务只在指定 Worker 执行。

---

## 第 23 章：任务优先级与调度排队机制

**定位**：资源紧张时，重要任务优先执行。

**核心内容**：
- 流程优先级五个等级：HIGHEST / HIGH / MEDIUM / LOW / LOWEST
- 任务优先级 vs 流程优先级的关系
- Master 线程池与任务提交流程：Command → Ready Queue → Dispatcher
- Worker 任务执行线程池配置
- 当 Master/Worker 线程不足时的排队机制

**实战目标**：限制 Worker 线程数为 1，同时提交高/低优先级任务，验证高优先级优先执行。

---

## 第 24 章：监控体系与 Prometheus + Grafana 集成

**定位**：看得见才能管得好。

**核心内容**：
- DolphinScheduler 内置监控端点：Master/Worker 健康检查 API
- Prometheus Exporter 部署与指标映射
- 关键监控指标：Command 积压数、DAG 执行数、任务成功率、执行耗时
- Grafana 仪表盘设计：RED 方法（Rate、Error、Duration）
- 告警规则：5xx 率突增、Master 失联、任务积压超过阈值

**实战目标**：搭建 Prometheus + Grafana 监控栈，配置 3 条核心告警规则。

---

## 第 25 章：日志管理与故障排查实战

**定位**：出了问题能找到原因。

**核心内容**：
- 日志目录结构：Master / Worker / API / Alert 日志
- 日志级别调整：logback 配置
- 常见故障排查 SOP：任务超时、Worker 宕机、数据源不可达、权限不足、配置错误
- 全链路追踪：TraceID 注入（OpenTelemetry 集成思路）
- 日志采集与分析平台接入

**实战目标**：制造 5 种常见故障，按 SOP 排查并修复。

---

## 第 26 章：补数策略与大规模数据回刷

**定位**：应对历史数据修复的工业级方案。

**核心内容**：
- 补数的高级配置：串行/并行补数、补数日期区间选择
- 补数与 Dependent 节点的协同：历史依赖日期的工作流检查
- 大规模补数（30 天+）的策略：分批补数、限流、下游压力控制
- 补数的风险控制：数据备份、灰度补数
- 补数与正常调度的冲突处理

**实战目标**：为复杂工作流配置 60 天补数任务，设计分批执行方案。

---

## 第 27 章：Python SDK 与 API 编程调度

**定位**：以代码方式编排调度，实现 GitOps 工作流。

**核心内容**：
- Python SDK（apache-dolphinscheduler）的安装与基础使用
- 用 Python 代码创建/更新/上线/运行工作流
- 批量管理：脚本批量导入 100 个工作流定义
- API Token 认证与安全管理
- REST API 全面解读：所有控制器的端点清单
- CI/CD 集成：工作流定义文件化，Git Push → 自动上线调度

**实战目标**：编写 Python 脚本一次性创建 5 个相关的工作流并自动上线。

---

## 第 28 章：【中级篇综合实战】构建金融级数据调度中台

**定位**：融会贯通中级篇知识。

**核心内容**：
- 场景：为金融机构构建数据调度中台
- 功能需求：集群 HA 部署、多数据源管理、高级任务类型、多租户权限、监控告警
- Python SDK 自动化运维
- 验收标准：日执行 5000+ 任务实例，P99 延迟 < 30s，可用性 99.95%

---

# 高级篇（第 29–38 章）

> **核心目标**：源码级理解 DolphinScheduler 的设计原理，掌握架构设计、自定义扩展与极端场景优化。

---

## 第 29 章：DolphinScheduler 源码目录结构与构建体系

**定位**：从源码视角理解项目的五脏六腑。

**核心内容**：
- 多模块 Maven 工程全景：26 个核心模块
- 核心模块解读：common、spi、dao、service、extract、registry、meter
- 构建流程：mvnw clean install -Prelease
- 源码阅读路线图：main 入口 → 初始化 → 运行时
- 开发环境搭建与断点调试

**实战目标**：从 GitHub 克隆源码 → 本地编译 → debug 一个 Shell 任务执行全流程。

---

## 第 30 章：SPI 插件体系——可插拔架构的设计哲学

**定位**：理解 DS 如何做到一个核心 + 无限扩展。

**核心内容**：
- 五大家族插件：Task Plugin、Datasource Plugin、Storage Plugin、Alert Plugin、Registry Plugin
- SPI 接口定义规范：接口抽象 + META-INF/services + PluginLoader
- 插件加载机制与优先级
- 以 Shell Task Plugin 为例：从 SPI 注册到 Worker 调用的完整代码链路

**实战目标**：分析 Shell Task Plugin 源码，追踪从 SPI 注册到任务执行的全流程。

---

## 第 31 章：Master 源码——工作流编排引擎深度剖析

**定位**：理解 DAG 状态机的实现。

**核心内容**：
- MasterServer 启动流程：Spring Boot → ZK 注册 → 成为 Leader
- Command 消费：MasterSchedulerBootstrap 轮询 t_ds_command 表
- WorkflowExecuteRunnable：单个工作流实例的执行线程
- DAG 状态机设计：SUBMITTED → RUNNING_EXECUTION → SUCCESS/FAILURE
- 任务下发与状态回调处理
- 容错机制：Master 宕机 → ZK 临时节点消失 → 其他 Master 接管

**实战目标**：在 Master 关键方法中插入日志，追踪一个完整的 DAG 执行状态流转。

---

## 第 32 章：Worker 源码——任务执行器深度剖析

**定位**：理解任务执行的底层实现。

**核心内容**：
- WorkerServer 启动与注册流程
- TaskExecuteProcessor：从 RPC 接收任务 → 提交给 TaskPlugin
- Shell 任务执行的底层：ProcessBuilder 创建子进程 → 日志采集 → 退出码判断
- 资源下载：从 Storage 下载脚本/JAR 到本地工作目录
- 参数替换机制与日志回传
- 源码关联：dolphinscheduler-worker + dolphinscheduler-task-executor

**实战目标**：在 Worker 的 shell_executor 中修改日志采集逻辑，实现日志实时推送。

---

## 第 33 章：RPC 通信框架——Master 与 Worker 的对话

**定位**：理解服务间通信的技术选型与实现。

**核心内容**：
- 通信技术演进：HTTP → Netty → gRPC
- dolphinscheduler-extract 模块：跨服务 RPC 接口契约定义
- Master → Worker 通信协议：TaskDispatchCommand → TaskExecuteResultCommand
- RPC 序列化：Protobuf 的 IDL 定义与代码生成
- 连接池管理与心跳检测
- 异步回调与超时处理

**实战目标**：抓包分析一次完整的 RPC 数据交互过程。

---

## 第 34 章：注册中心与分布式协调

**定位**：Zookeeper 在大规模调度集群中的角色。

**核心内容**：
- ZK 节点结构：/dolphinscheduler/master/、/dolphinscheduler/worker/、/dolphinscheduler/lock/
- Master 选举机制：ZK 临时顺序节点 + 最小节点获得 Leader
- Worker 注册与发现：临时节点 + 心跳维持
- 分布式锁的应用场景：Command 消费竞争、定时触发互斥
- JDBC 注册中心与 Etcd 注册中心对比

**实战目标**：搭建 ZK 集群，启动 2 个 Master，Kill 一个 Master 观察 Failover。

---

## 第 35 章：元数据库表设计——状态驱动调度的数据模型

**定位**：理解 100+ 张表如何支撑分布式调度。

**核心内容**：
- 核心表实体分析：t_ds_process_definition、t_ds_task_instance、t_ds_command 等
- 表关系 ER 图：定义 → 实例生命周期的数据流转
- Command 表的设计：Master 轮询消费 → 状态变更 → 删除
- 分片策略与 SQL 迁移脚本的管理

**实战目标**：手工构造 Command 记录，观察 Master 自动消费并触发工作流执行。

---

## 第 36 章：容错与故障恢复机制深度解析

**定位**：保证 99.99% 可用性的关键技术。

**核心内容**：
- Master 容错：ZK 临时节点 → Master 宕机 → 其他 Master 接管
- Worker 容错：心跳超时 → Master 重新分配任务 → 任务重试
- 任务失败策略与流程级失败策略
- 从失败节点恢复与暂停/恢复机制

**实战目标**：构建 5 节点 DAG，运行中 Kill Worker 进程，验证任务自动重试到其他 Worker。

---

## 第 37 章：自定义 Task Plugin 开发实战

**定位**：从源码读者到源码作者——扩展自己的任务类型。

**核心内容**：
- Task Plugin 开发框架：继承 AbstractTask → 实现 TaskChannel → SPI 注册
- 配置结构设计与命令行注册
- 参数传递与解析
- 功能实现：进程执行器 vs 远程 API 调用
- 编译与集成：使用 --add-module 方式加载自定义插件
- 测试验证：单元测试 + 集成测试

**实战目标**：开发一个"数据质量检查"Task Plugin，在工作流中作为数据质量关卡节点。

---

## 第 38 章：【高级篇综合实战】从零构建企业级统一调度平台

**定位**：融会贯通全专栏知识。

**核心内容**：
- 场景：为 1000+ 人规模的数据团队构建统一调度平台
- 架构设计：DS 核心 + 自定义插件 + 多集群联邦 + 多租户
- 功能实现：GitOps 工作流管理、全链路监控、自动扩容、成本优化
- 验收标准：日调度 10 万+ 任务实例、P99 延迟 < 30s、可用性 99.99%

---

# 附录

## 附录 A：源码阅读路线图

1. 入口：dolphinscheduler-standalone-server → StandaloneServer.main()
2. 初始化：MasterServer → WorkerServer → AlertServer
3. 运行时：Command 入队 → Master 消费 → DAG 状态机 → Worker 执行
4. 收尾：任务完成 → 状态回写 → 告警触发

## 附录 B：技术栈速查表

| 类别 | 技术 | 用途 |
|------|------|------|
| 后端框架 | Spring Boot 2.6.1 + Jetty | 四服务独立运行 |
| ORM | MyBatis-Plus + HikariCP | 元数据访问 |
| 调度内核 | Quartz | Cron 定时触发 |
| 服务注册 | Zookeeper 3.8 / Etcd / JDBC | 发现与选举 |
| RPC | Netty + gRPC + Protobuf | 服务间通信 |
| 前端 | Vue 3 + Vite + TypeScript + Naive UI | 控制台 |
| SPI | Java ServiceLoader | 插件体系 |
| 构建 | Maven + Spotless + Maven Wrapper | 多模块管理 |

## 附录 C：推荐工具链

- 部署：Docker Compose、Kubernetes（Helm Chart）
- 监控：Prometheus、Grafana、OpenTelemetry + Jaeger
- 日志：ELK（Filebeat → Kafka → Logstash → Elasticsearch → Kibana）
- 测试：JUnit 5、Mockito、Testcontainers
- 调试：IntelliJ IDEA + Remote Debug + Arthas
- CI/CD：GitHub Actions / Jenkins

---

> **版权声明**：本专栏基于 Apache DolphinScheduler 官方源码（Apache License 2.0）编写，所有源码引用均遵循原许可证条款。
