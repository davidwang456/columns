# Apache Airflow 实战修炼专栏大纲

> **版本**：Apache Airflow 3.x（task-sdk + airflow-core 分离架构）
> **面向人群**：数据工程师、DevOps、测试、架构师
> **总章节**：40 章（基础篇 16 章 / 中级篇 15 章 / 高级篇 9 章）
> **每章**：3000-5000 字，独立成文件，采用「业务痛点 → 三人剧本对话 → 代码实战 → 总结思考」四段式结构

---

## 专栏定位

以 Apache Airflow 3.x 最新架构为骨架，从核心概念到分布式部署，从 Dag 编写到调度器源码剖析，从性能调优到自定义扩展，全链路贯通。实战为主，理论为辅，由浅入深，兼顾趣味性与深度。

---

## 阅读路线建议

| 角色 | 建议阅读顺序 | 重点章节 |
|------|-------------|---------|
| 新人开发/测试 | 基础篇全读 → 中级篇选读 | 第 1-16 章 |
| 核心开发/运维 | 基础篇速读 → 中级篇精读 → 高级篇选读 | 第 17-31 章，辅以 32-40 章 |
| 架构师/资深开发 | 高级篇为主线，按需回溯中级篇 | 第 32-40 章，辅以 17-31 章 |

---

# 基础篇（第 1-16 章）

> **核心目标**：建立 Airflow 核心概念，掌握单机部署、Dag 编写、常用算子与初级实战。
> **源码关联**：`task-sdk/src/airflow/sdk/definitions/dag.py`、`task-sdk/src/airflow/sdk/bases/operator.py`

---

## 第1章：Airflow 术语全景与架构原理

**定位**：专栏总览与开篇，建立统一语系。

**核心内容**：
- 术语词典：Dag、Task、DagRun、TaskInstance、Operator、Sensor、Hook、Executor、Scheduler、Dag Processor、Triggerer、Metadata DB、XCom、Variable、Connection、Pool、Asset、TaskFlow
- 架构全景图：Scheduler → Dag Processor → Metadata DB → Executor → Worker → Execution API 的完整数据流
- 核心组件职责与交互：单机部署 vs 分布式部署 vs 隔离式 Dag 处理部署
- 安全模型初探：Dag Author / Deployment Manager / Operations User 三角色分离
- 源码文件关联：`task-sdk/definitions/dag.py`、`airflow-core/jobs/scheduler_job_runner.py`、`airflow-core/dag_processing/`

**实战目标**：绘制一张可讲解的 Airflow 整体架构图（含组件间数据流），输出到团队 Wiki。

---

## 第2章：环境搭建——从零部署 Airflow

**定位**：动手第一步，跑起来再说。

**核心内容**：
- 三种部署方式对比：pip install、Docker Compose、官方 Helm Chart
- airflow.cfg 核心配置项解读（database、executor、parallelism、dags_folder）
- 元数据库初始化（`airflow db migrate`）与 SQLite/PostgreSQL/MySQL 选型
- 创建管理员用户、启动 Webserver + Scheduler
- 目录结构：dags/、plugins/、logs/、airflow.cfg
- 常见踩坑：端口占用、数据库连接失败、Python 版本兼容

**实战目标**：Docker Compose 一键启动 Airflow，访问 Web UI（localhost:8080），验证调度器心跳。

---

## 第3章：编写第一个 Dag——从 Cron 到 Python 脚本编排

**定位**：理解 Dag 就是"一张有向无环的任务图"。

**核心内容**：
- DAG 对象的定义参数：dag_id、schedule、start_date、catchup、tags
- 第一个任务：BashOperator（执行 Shell）、PythonOperator（执行 Python 函数）
- 任务依赖：`>>` 和 `<<` 操作符，链式调用
- Dag 文件放置与加载流程（DagBag → DagModel 序列化 → UI 展示）
- 源码关联：`task-sdk/definitions/dag.py` DAG 类、`airflow-core/dag_processing/manager.py`

**实战目标**：编写一个"数据下载 → 数据清洗 → 数据加载"的三步 Dag，在 Web UI 中触发运行。

---

## 第4章：调度中心控制台总览——Web UI 导航与操作

**定位**：熟悉 Airflow 的操作界面，理解 Dag 生命周期可视化。

**核心内容**：
- 首页 Dashboard：Dag 列表、运行状态统计、近期任务结果
- Dag 详情页：Grid 视图 vs Graph 视图 vs Gantt 视图 vs 代码视图
- 任务操作：手动触发（Trigger Dag）、暂停（Pause）、清除（Clear）、标记成功/失败
- DagRun 与 TaskInstance 状态机：queued → running → success/failed/up_for_retry
- 过滤器与搜索：按状态、标签、Dag ID 快速定位
- 审计日志：事件日志查看、权限变更记录

**实战目标**：完成 10 次不同参数的 Dag 触发，学会通过 Grid 视图定位失败任务根因。

---

## 第5章：调度策略与定时触发——Cron、Timetable 与 Catchup

**定位**：理解"何时跑、跑几次"的调度哲学。

**核心内容**：
- Cron 表达式与 preset（@daily、@hourly、@once）详解
- schedule vs timetable：CronDataIntervalTimetable 的工作机制
- catchup 参数：为什么默认 True？什么场景需要 False？
- start_date 与 end_date 的边界条件（左闭右闭区间）
- 定时调度 vs 事件驱动调度（Asset 触发预告）
- 源码关联：`task-sdk/definitions/timetables/`、`airflow-core/models/dag.py` next_dagrun 计算逻辑

**实战目标**：配置一个"每小时执行一次，回溯过去 24 小时"的 Dag，观察 catchup 行为差异。

---

## 第6章：任务定义与算子入门——PythonOperator、BashOperator 与 EmptyOperator

**定位**：掌握最常用的三种基础算子，覆盖 80% 的任务场景。

**核心内容**：
- PythonOperator：python_callable、op_kwargs、op_args，虚拟环境隔离
- BashOperator：env 参数注入、输出捕获、exit code 处理
- EmptyOperator：占位节点、分支汇聚、工作流骨架设计
- 算子通用参数：task_id、retries、retry_delay、execution_timeout、trigger_rule
- TaskGroup：逻辑分组与 UI 折叠
- 源码关联：`task-sdk/bases/operator.py` BaseOperator 参数定义

**实战目标**：编写一个包含 Python 数据处理 + Bash 命令执行 + Empty 占位的混合 Dag。

---

## 第7章：变量、连接与配置管理——敏感信息的安全存放

**定位**：理解 Airflow 的配置三板斧。

**核心内容**：
- Variable：key-value 存储，通过 UI/CLI/代码 创建与读取，JSON 序列化
- Connection：conn_id、conn_type、host、login、password、extra 字段解析
- 环境变量注入：`AIRFLOW_VAR_*`、`AIRFLOW_CONN_*` 前缀约定
- Secrets Backend：HashiCorp Vault、AWS Secrets Manager 集成
- 安全最佳实践：不在 Dag 文件中硬编码密码，extra 字段的 JSON 格式
- 源码关联：`airflow-core/models/variable.py`、`airflow-core/models/connection.py`、`airflow-core/secrets/`

**实战目标**：创建 MySQL Connection 和 API Key Variable，在 PythonOperator 中安全读写。

---

## 第8章：XCom——任务间数据传递的艺术

**定位**：理解 Airflow 任务间通信的核心机制。

**核心内容**：
- XCom 工作原理：push（任务产出）→ metadata DB 存储 → pull（下游消费）
- TaskFlow 自动 XCom：`@task` 装饰器下 return 值自动 push
- 手动 push/pull：`ti.xcom_push()` / `ti.xcom_pull()`，task_ids 与 key 参数
- XCom 大小限制与性能权衡：大文件建议用对象存储（S3/GCS）+ XCom 传路径
- XCom 清理策略：自动清理 vs 手动清理，`xcom_backend` 自定义
- 源码关联：`airflow-core/models/xcom.py`、`task-sdk/execution_time/xcom.py`

**实战目标**：上游任务查询数据库返回行数 → XCom → 下游任务根据行数决定是否继续。

---

## 第9章：传感器与等待机制——灵敏的"守门员"

**定位**：掌握事件驱动工作流的关键组件。

**核心内容**：
- Sensor 基类机制：poke_interval、timeout、mode（poke/reschedule）
- 常用 Sensor：FileSensor、ExternalTaskSensor、S3KeySensor、HttpSensor
- Deferrable Sensor（异步传感器）：减少 worker 槽位占用，Triggerer 角色
- ExternalTaskSensor：跨 Dag 依赖的实现与 execution_date 匹配逻辑
- Sensor 死锁与超时陷阱：如何避免传感器耗尽所有 worker 槽位
- 源码关联：`task-sdk/bases/sensor.py`、`airflow-core/jobs/triggerer_job_runner.py`

**实战目标**：配置 FileSensor 等待上游系统落盘文件，超时自动告警。

---

## 第10章：TaskFlow API 与装饰器——告别样板代码

**定位**：掌握 Airflow 3.x 主推的声明式 Dag 编写范式。

**核心内容**：
- `@task` 装饰器：自动将 Python 函数转为 PythonOperator，XCom 自动传递
- `@dag` 装饰器：替代 `with DAG(...)` 上下文管理器
- TaskFlow 数据流：函数签名即依赖声明，无需显式 `>>` 操作符
- 与传统 Operator 风格混用：何时用 TaskFlow，何时用传统 Operator
- `@task.branch`、`@task.short_circuit` 装饰器
- 源码关联：`task-sdk/definitions/decorators/`

**实战目标**：用 TaskFlow 重写第 3 章的 ETL Dag，对比代码量减少比例。

---

## 第11章：Dag 参数与动态 Dag 生成

**定位**：让 Dag 拥有"可配置"的灵活性。

**核心内容**：
- Params：`dag.params` 定义、`context["params"]` 读取，支持 str/int/bool/json/enum 类型
- 参数校验：`Param` 的 `type`、`default`、`minimum`/`maximum`、`enum` 约束
- Trigger Dag with config：手动触发时传入 JSON 配置，不同 run 用不同参数
- 动态 Dag 生成模式：循环生成多 Dag、模板化 Dag 工厂函数
- 动态 Dag 的陷阱：调度器性能开销、Dag 爆炸、版本管理
- Jinja 模板变量：`{{ ds }}`、`{{ params.xxx }}`、`{{ task_instance.xcom_pull(...) }}`

**实战目标**：开发一个"多数据源同步"Dag 工厂，通过参数切换同步的表名和分区。

---

## 第12章：数据资产与数据集调度——让数据驱动工作流

**定位**：理解 Airflow 3.x 核心新范式——数据感知调度。

**核心内容**：
- Asset 概念：数据资产 URI 定义（表名、文件路径、S3 前缀）
- 生产者 Dag：`outlets=[Asset("my_table")]`
- 消费者 Dag：`schedule=[Asset("my_table")]`
- Asset 别名与通配符：跨团队解耦
- 与传统 timetable 调度的互补：时间 + 数据双重触发
- Asset 依赖图可视化管理
- 源码关联：`task-sdk/definitions/asset/`、`airflow-core/models/asset.py`

**实战目标**：构建"Flink 写入 → Asset 标记 → Airflow 消费者 Dag 自动触发"的生产者-消费者链路。

---

## 第13章：回填与历史任务重跑——时间的倒带键

**定位**：掌握 Dag 补数据和异常恢复的核心技能。

**核心内容**：
- Backfill：`airflow dags backfill` 命令与参数（start_date、end_date、reset_dagruns）
- Clear：清除特定 DagRun/TaskInstance 状态并重跑
- 幂等性设计：为什么任务必须可重跑、去重策略
- 回填的性能风险：大量历史 DagRun 对元数据库的压力
- 手动 Trigger 与 Backfill 的差异：参数传递、execution_date 语义
- 生产事故恢复 SOP：从发现异常到回填完成的完整流程

**实战目标**：模拟一个三天数据缺失的场景，使用 backfill 命令补齐所有历史分区。

---

## 第14章：日志、监控与告警基础——让 Airflow "会说话"

**定位**：建立可观测性第一道防线。

**核心内容**：
- 日志架构：本地文件 vs S3/GCS 远程存储，`airflow.cfg` 日志配置
- 任务日志查看：Web UI 直接查看、`airflow tasks logs` 命令
- Email 告警：`email_on_failure`、`email_on_retry`、SMTP 配置
- Callbacks：`on_failure_callback`、`on_success_callback`、`on_retry_callback`、`sla_miss_callback`
- 自定义 Notifier：Slack/DingTalk/飞书 消息推送
- 源码关联：`task-sdk/bases/notifier.py`、`airflow-core/callbacks/`、`providers/slack/`

**实战目标**：配置任务失败时自动发送钉钉群告警（含 Dag ID、Task ID、错误日志链接）。

---

## 第15章：调度器与执行器运行原理——任务的"大脑"与"手臂"

**定位**：理解 Airflow 任务从创建到执行的全生命周期。

**核心内容**：
- 调度器循环：_do_scheduling → 解析 Dag → 创建 DagRun → 检查依赖 → 提交任务
- 执行器角色：LocalExecutor 进程内执行 vs CeleryExecutor 分布式执行
- 任务状态流转：scheduled → queued → running → success/failed/up_for_retry
- Worker 安全模型：短生命周期 JWT Token、Execution API 通信、不直连 DB
- 并行度控制：parallelism、dag_concurrency、max_active_tasks、pool
- 源码关联：`airflow-core/jobs/scheduler_job_runner.py`（主循环）、`airflow-core/executors/`

**实战目标**：使用 LocalExecutor 运行一个 20 任务并发的 Dag，观察 parallelism 参数对执行顺序的影响。

---

## 第16章：【基础篇综合实战】构建端到端数据平台 ETL 流水线

**定位**：融会贯通基础篇 15 章知识。

**核心内容**：
- 场景：为一家电商公司构建订单数据 ETL 流水线
- 需求拆解：MySQL 数据抽取 → Python 数据清洗 → 分区写入 → 数据质量检查 → 报表生成 → 告警通知
- 技术栈：PythonOperator + BashOperator + FileSensor + MySQL Hook + Slack 告警
- 核心设计：XCom 传递数据摘要、参数化分区日期、失败自动重试 + 告警
- 分步实现：Docker 环境编排、Dag 文件编写、调度策略配置、回填测试
- 验收标准：每小时自动执行、失败任务 5 分钟内告警到钉钉、回填 30 天历史数据零报错

---

# 中级篇（第 17-31 章）

> **核心目标**：掌握分布式场景下的架构设计、性能调优、可观测性与容器化实践。
> **源码关联**：`airflow-core/jobs/`、`airflow-core/api_fastapi/`、`airflow-core/models/`、`providers/`

---

## 第17章：元数据库深度解析——DagModel、DagRun 与 TaskInstance

**定位**：理解 Airflow 状态存储的核心——元数据库的表结构与查询优化。

**核心内容**：
- 核心表关系图：dag → dag_run → task_instance → task_fail → xcom → log
- DagModel：is_active、is_paused、next_dagrun、serialized_dag 字段
- DagRun：state、execution_date、run_type（manual/scheduled/backfill/asset_triggered）、conf
- TaskInstance：state、try_number、duration、start_date、end_date、queued_by_job_id
- SQLAlchemy Session 管理：commit 规则、autocommit 模式、连接池
- 源码关联：`airflow-core/models/dag.py`、`airflow-core/models/dagrun.py`、`airflow-core/models/taskinstance.py`

**实战目标**：直接查询元数据库，编写 SQL 统计"过去 7 天各 Dag 的任务成功率 TOP 10"。

---

## 第18章：Dag 文件处理与序列化机制

**定位**：理解 Dag 文件变成数据库记录的完整过程。

**核心内容**：
- DagBag：从文件系统加载 Dag 文件、收集 DAG 对象
- Dag File Processor 进程模型：独立进程解析、软件隔离、不直连 DB
- 序列化流程：DAG → SerializedDagModel（JSON）→ 存入 metadata DB
- 反序列化：Scheduler 从 DB 读取 SerializedDagModel → 反序列化后执行调度
- Dag Bundle 机制：Git Bundle / Local Bundle / 自定义 Bundle
- 源码关联：`airflow-core/dag_processing/processor.py`、`airflow-core/dag_processing/manager.py`、`airflow-core/serialization/`

**实战目标**：手动创建 SerializedDagModel 记录并验证调度器可正常读取（测试序列化完整性）。

---

## 第19章：调度器源码架构——定时感知与任务生命周期管理

**定位**：深入调度器内部，理解 DagRun 的创建、排队与执行调度逻辑。

**核心内容**：
- 调度器主循环：_run_scheduler_loop → _do_scheduling → _create_dag_runs → _schedule_dag_tasks
- DagRun 创建条件判断：timetable.next_dagrun_date、catchup、backfill、asset 触发
- TaskInstance 可调度性检查：依赖就绪、Pool 槽位、并发上限、Executor 容量
- 调度器锁机制：advisory lock（SELECT ... FOR UPDATE）保证单实例调度
- 调度延迟：scheduler_heartbeat_sec、min_file_process_interval 调优
- 源码关联：`airflow-core/jobs/scheduler_job_runner.py`（3317 行主逻辑）

**实战目标**：在调度器主循环插入日志，追踪一个 DagRun 从创建到所有 TaskInstance 提交的完整耗时。

---

## 第20章：执行器架构对决——Local、Celery、Kubernetes 三维对比

**定位**：理解不同执行器的设计取舍与选型决策。

**核心内容**：
- LocalExecutor：进程内并行、适合开发/小规模、无分布式能力
- CeleryExecutor：Redis/RabbitMQ 消息队列、独立 Worker 进程、HA 支持
- KubernetesExecutor：每个 Task 一个 Pod、资源隔离最强、调度延迟最高
- 混合执行器：CeleryKubernetesExecutor 的协作模式
- Executor 接口源码：BaseExecutor.queue_command、trigger_tasks、sync
- 源码关联：`airflow-core/executors/base_executor.py`、`providers/celery/`、`providers/cncf/kubernetes/`

**实战目标**：分别在三种 Executor 下压测 100 并发任务的端到端延迟，输出选型对比报告。

---

## 第21章：TaskFlow 高级特性——动态任务映射与分支编排

**定位**：掌握 Airflow 工作流灵活性的极限。

**核心内容**：
- 动态任务映射（Dynamic Task Mapping）：`.expand()` 语法、MappedOperator
- 静态映射 vs 动态映射：`.partial()` 固定参数、运行时确定映射数量
- 分支逻辑：`@task.branch`、BranchPythonOperator、TriggerRule 详解
- 条件跳过：ShortCircuitOperator、`@task.short_circuit`
- 依赖规则：TriggerRule.ALL_SUCCESS / ALL_FAILED / ALL_DONE / ONE_SUCCESS / NONE_FAILED
- 源码关联：`task-sdk/definitions/mappedoperator.py`、`task-sdk/bases/branch.py`

**实战目标**：实现"根据上游查询结果行数动态生成 N 个并行处理任务"的弹性 ETL 流水线。

---

## 第22章：连接池管理与资源隔离——Pool、Priority 与并发控制

**定位**：避免任务洪峰打垮后端系统。

**核心内容**：
- Pool 机制：slot 计数、TaskInstance 占槽/释放、饥饿与排队
- Priority Weight：任务优先级权重对排队顺序的影响
- dag_concurrency vs max_active_tasks vs max_active_runs 三层并发控制
- Pool 监控：Web UI 槽位可视化、slot 泄漏排查
- 资源隔离的最佳实践：按系统/团队拆分 Pool，设置合理的槽位数
- 源码关联：`airflow-core/models/pool.py`、`airflow-core/jobs/scheduler_job_runner.py` 中 Pool 检查逻辑

**实战目标**：为 MySQL、Hive、API 三种资源创建独立 Pool，压测验证资源隔离效果。

---

## 第23章：任务重试、超时与容错——打造健壮的流水线

**定位**：从"能跑"到"稳跑"的进阶。

**核心内容**：
- retries 与 retry_delay：指数退避 vs 固定间隔
- retry_exponential_backoff、max_retry_delay 参数
- execution_timeout：任务级超时，超时自动失败
- dagrun_timeout：DagRun 级超时
- SLA（Service Level Agreement）：sla 参数 + sla_miss_callback
- Failure Callback vs Retry Callback：不同阶段的回调策略
- Zombie Task 检测：心跳超时、僵尸任务清理
- 源码关联：`task-sdk/bases/operator.py` 重试相关参数、`airflow-core/jobs/scheduler_job_runner.py` 超时检查

**实战目标**：为一个不稳定 API 调用任务配置指数退避重试（最大 5 次），模拟 3 次失败后成功场景。

---

## 第24章：Web UI 定制与插件系统开发

**定位**：让 Airflow 拥有"团队面孔"。

**核心内容**：
- Plugin 系统：AirflowPlugin 基类、admin_views、appbuilder_views、flask_blueprints
- 自定义菜单与页面：在 Web UI 中添加 Dashboard、报表页
- 自定义 Operator 链接：OperatorLink 让任务详情页跳转外部系统
- Listener 机制：监听 DagRun/TaskInstance 状态变更事件
- Branding 定制：Logo、标题、配色修改
- 源码关联：`airflow-core/plugins/`、`airflow-core/listeners/`

**实战目标**：开发一个 Plugin，在 Web UI 增加"团队任务看板"页面，展示各成员的 Dag 成功率排行榜。

---

## 第25章：REST API 编程与 CLI 自动化

**定位**：用代码操作 Airflow，迈向自动化运维。

**核心内容**：
- Core REST API 概览：`/api/v2/dags`、`/api/v2/dagRuns`、`/api/v2/taskInstances`
- API 认证：Basic Auth、JWT Token（Bearer Token）
- Python Client SDK：`airflow_client.client` 的使用
- CLI 自动化：`airflow dags trigger`、`airflow tasks clear`、`airflow dags backfill`
- CI/CD 集成：GitHub Actions 中自动部署 Dag + 触发运行
- 批量运维脚本：自动暂停失败 Dag、批量清理过期 XCom

**实战目标**：编写 Python 脚本，通过 REST API 批量触发 50 个 Dag 并轮询等待全部完成。

---

## 第26章：Executors 进阶——Celery 深度配置与调优

**定位**：掌握生产级 CeleryExecutor 的配置与故障排查。

**核心内容**：
- Celery Broker：Redis vs RabbitMQ 的选择与 HA 配置
- Celery Worker 参数：concurrency、prefetch_multiplier、任务确认机制
- Result Backend：存储任务结果，Airflow 中的角色
- Flower 监控面板：实时队列长度、Worker 状态、任务执行时间分布
- Celery 常见故障：消息堆积、Worker 漂移、连接泄漏
- `airflow.cfg` 中 Celery 相关配置详解
- 源码关联：`providers/celery/src/airflow/providers/celery/executors/`

**实战目标**：部署 Redis + 3-Celery-Worker 集群，运行 500 并发 DagRun 压力测试。

---

## 第27章：容器化部署与 Kubernetes 集成

**定位**：云原生时代 Airflow 的部署形态。

**核心内容**：
- 官方 Docker 镜像：`apache/airflow` 的构建、自定义 Dockerfile 扩展
- Docker Compose 生产配置：多副本、健康检查、持久化卷
- Helm Chart 架构：Scheduler、Webserver、Worker、Dag Processor、Triggerer 各组件的 K8s 资源
- KubernetesExecutor：Pod 模板、资源限制、镜像拉取策略、日志采集
- Git-Sync / PV 同步 Dag 文件
- 网络策略：组件间通信的安全组规则

**实战目标**：在 K8s 集群部署 Airflow Helm Chart，配置 KubernetesExecutor，运行一个包含 S3 读写的 Dag。

---

## 第28章：可观测性体系——Prometheus + Grafana + 分布式追踪

**定位**：从黑盒到白盒，全面监控 Airflow 集群。

**核心内容**：
- Airflow Metrics：StatsD 指标暴露（dag_processing、scheduler、ti_status、pool）
- Prometheus Exporter 配置：`airflow-prometheus-exporter` 或自定义 exporter
- Grafana 大盘设计：RED 方法（Rate/Errors/Duration）+ USE 方法（Utilization/Saturation/Errors）
- 核心告警规则：Dag 失败率 > 5%、Task 排队时间 > 10min、Scheduler 心跳超时
- 分布式追踪：OpenTelemetry 集成、TraceID 注入与全链路追踪
- 日志聚合：Fluentd/Filebeat → Elasticsearch → Kibana

**实战目标**：搭建 StatsD + Prometheus + Grafana 监控栈，配置 5 条核心告警规则。

---

## 第29章：多环境管理与 GitOps 实践

**定位**：从开发到生产的 Dag 治理。

**核心内容**：
- 多环境策略：dev / staging / prod 的配置差异管理
- 环境变量注入：`AIRFLOW__CORE__DAGS_FOLDER`、数据库连接覆盖
- Dag 版本管理：Git 分支策略与 CI/CD Pipeline 设计
- Dag 集成测试：`airflow dags test`、`airflow tasks test` 在 CI 中使用
- GitOps 工作流：Git Push → CI 校验 → CD 部署到 Airflow
- 回滚策略：Dag 文件的版本化与快速回退

**实战目标**：设计一套 GitHub Actions Pipeline，实现"PR 合并 → 自动部署到 Staging → 触发冒烟测试 → 部署到 Production"。

---

## 第30章：权限管理与安全加固——RBAC、JWT 与 Secrets

**定位**：构建企业级安全防线。

**核心内容**：
- RBAC 权限模型：角色（Admin/Viewer/User/Op）→ 权限 → 资源
- JWT Token 认证：Core API vs Execution API 的 Token 差异
- Worker 安全隔离：不直连 DB、短生命周期 JWT、Execution API 代理
- Secrets Backend 深度：Vault、AWS Secrets Manager、GCP Secret Manager
- 网络安全：组件间 TLS、API 限流、CORS 配置
- 审计与合规：Event Log、操作审计、数据血缘
- 源码关联：`airflow-core/security/`、`airflow-core/api_fastapi/execution_api/`

**实战目标**：配置 Vault 作为 Secrets Backend，Dag 中通过 `Variable.get()` 安全获取数据库密码。

---

## 第31章：【中级篇综合实战】构建分布式数据平台调度中心

**定位**：融会贯通中级篇知识。

**核心内容**：
- 场景：为一家互联网公司搭建统一的分布式数据调度平台
- 功能需求：多租户隔离、资源管控（Pool + Priority）、多 Executor 分层、统一监控告警
- 架构设计：CeleryExecutor（核心）+ KubernetesExecutor（弹性）+ Redis + PostgreSQL + Prometheus
- 核心实战：
  - 按业务线拆分 Pool（广告/推荐/风控各 32 slots）
  - Celery + K8s 混合执行器路由策略
  - 统一告警：钉钉/邮件/Slack 多通道分级
  - 跨 Dag 依赖编排（ExternalTaskSensor + Asset）
  - 日志 ELK 集成
- 验收标准：500+ Dag、日增 5 万 TaskInstance、P99 调度延迟 < 30s、零数据丢失

---

# 高级篇（第 32-40 章）

> **核心目标**：源码级理解 Airflow 核心实现，掌握自定义扩展与极端场景优化。
> **源码关联**：`airflow-core/jobs/scheduler_job_runner.py`、`airflow-core/dag_processing/`、`airflow-core/api_fastapi/`、`task-sdk/execution_time/`

---

## 第32章：Dag 解析引擎源码剖析

**定位**：理解 Dag 文件如何变成可执行的调度单元。

**核心内容**：
- DagBag 源码：文件扫描 → AST/import → DAG 对象提取 → 序列化
- SerializedDagModel：JSON Schema 定义、字段映射、反序列化
- Dag Processor 进程架构：独立进程、心跳监控、优雅重启
- TaskGroup 嵌套解析：树形结构展开为任务列表
- 动态映射（MappedOperator）的序列化与展开
- 源码关联：`airflow-core/dag_processing/dagbag.py`、`airflow-core/dag_processing/processor.py`、`airflow-core/serialization/serialized_objects.py`

**实战目标**：编写脚本扫描所有 Dag 文件，统计各类 Operator 使用频率与平均任务数。

---

## 第33章：调度器调度算法与性能调优

**定位**：深入调度器心脏，理解毫秒级调度决策。

**核心内容**：
- _do_scheduling 完整源码走读：DagRun 创建 → TaskInstance 可调度性检查 → 提交 Executor
- 数据库查询优化：lazy load、selectinload、批量更新、N+1 问题排查
- Parsimonious Parsing：Dag 选择性加载与缓存策略
- 调度器高可用：Advisory Lock、Leader Election、多 Scheduler 实例
- 调度延迟来源分析：DB I/O、Dag 文件解析、网络调用
- 源码关联：`airflow-core/jobs/scheduler_job_runner.py` 核心方法

**实战目标**：对 2000 Dag 集群做调度器性能 Profile，找出 Top 3 瓶颈并给出优化方案。

---

## 第34章：执行器扩展与自定义开发

**定位**：开发专属于团队的 Executor。

**核心内容**：
- BaseExecutor 接口定义：start、queue_command、trigger_tasks、sync、end
- 命令队列设计：内存队列 vs Redis List vs Kafka Topic
- 自定义 Worker 进程：任务拉取、沙箱执行、状态回传
- Executor 插件注册：setup.py entry_points、provider.yaml
- 执行器性能测试：throughput、latency、failure recovery
- 源码关联：`airflow-core/executors/base_executor.py`

**实战目标**：开发一个基于 Redis Queue 的自定义 Executor（MinimalRedisExecutor），实现 worker 心跳、任务分发与状态回传。

---

## 第35章：Execution API 内部机制与安全模型

**定位**：理解 Worker 如何安全地与 Airflow 核心通信。

**核心内容**：
- Execution API 端点：taskinstance、heartbeat、xcom、connection、variable
- JWT Token 生命周期：claim 结构（task_instance_id、exp）、签发与验证流程
- Worker 隔离模型：不直连 DB、最小权限 Token、范围限定
- 连接泄露场景：Token 过期、Worker 重启、网络中断的重连机制
- Supervisor 进程管理：任务超时 kill、内存限制、子进程监控
- 源码关联：`airflow-core/api_fastapi/execution_api/`、`task-sdk/execution_time/supervisor.py`、`task-sdk/api/client.py`

**实战目标**：抓包分析一次完整任务执行的 JWT 签发 → API 调用 → 结果回传的全链路。

---

## 第36章：自定义 Provider 开发——从零构建企业 Operator 包

**定位**：从使用者到贡献者。

**核心内容**：
- Provider 包标准结构：src/、tests/、docs/、pyproject.toml、provider.yaml
- 自定义 Hook：继承 BaseHook，实现 get_conn、test_connection
- 自定义 Operator：继承 BaseOperator，实现 execute 方法
- 自定义 Sensor：继承 BaseSensorOperator，实现 poke 方法
- 自定义 Trigger：deferrable operator 的异步实现
- Provider 打包与发布：provider.yaml 元数据、版本管理
- 源码关联：`providers/common/sql/`、`providers/amazon/`、`providers/google/`

**实战目标**：开发一个完整的 `apache-airflow-providers-wechat` Provider，包含 WeChatHook（消息发送 API）、WeChatOperator、WeChatSensor（接收回执）。

---

## 第37章：大规模集群性能调优——从百到万的极限挑战

**定位**：从 100 Dag 到 10000 Dag，从 1000 Task/Day 到 100 万 Task/Day 的进化。

**核心内容**：
- 数据库调优：PostgreSQL 连接池（PGBouncer）、索引优化、读写分离
- 调度器 Scale-out：多 Scheduler 实例、Lock 争用分析
- Worker 自动扩缩容：KEDA（Kubernetes Event-Driven Autoscaling）
- 元数据清理：TaskInstance、XCom、Log 的定期清理 job
- Dag 文件优化：减少顶层 import、延迟导入、轻量化 operator 构造
- 性能基准：wrk/stress 压测 API、大规模回填测试
- 源码关联：`airflow-core/jobs/scheduler_job_runner.py` 性能敏感路径

**实战目标**：对 5000 Dag × 10 万 TaskInstance 集群实施全套调优，输出"调优前 vs 调优后"性能对比报告。

---

## 第38章：Airflow 3.x 新特性深度解读与 2.x 迁移实战

**定位**：把握 Airflow 3.x 架构革命的每一处细节。

**核心内容**：
- Task SDK 分离：独立包管理、版本语义、依赖解耦
- Dag Processor 强制分离：安全边界、进程模型、Bundle 机制
- Execution API 替代 DB 直连：Worker → API → DB 的数据流
- Asset（数据集调度）：与传统 timetable 的混合使用
- Deferrable 成为一等公民：Triggerer 重构、异步生态
- 2.x → 3.x 迁移指南：配置文件变更、Dag 文件兼容性、Operator API 变化
- 源码关联：对比 `airflow-core/` 与 2.x 分支的 Diff

**实战目标**：将一个 2.x 版本的 50-Dag 集群完整迁移到 3.x，记录所有踩坑与解决过程。

---

## 第39章：高可用与灾备架构设计——让 Airflow 永不停机

**定位**：构建 99.99% 可用性的调度基础设施。

**核心内容**：
- Scheduler HA：多实例 + Advisory Lock + 故障自动切换
- Database HA：PostgreSQL Patroni 三节点 + etcd、读写分离
- Celery Broker HA：Redis Sentinel / RabbitMQ Mirrored Queue
- Webserver HA：Load Balancer（Nginx/ALB）+ Session 共享
- 灾备方案：跨 AZ 部署、元数据库异地备份与恢复演练
- 故障演练：模拟 Scheduler 宕机、DB 主库切换、Broker 不可用
- 容量规划：Task 增长曲线、存储容量预估、成本优化

**实战目标**：设计一套跨 3 个可用区的 Airflow HA 架构图，输出 CAP 成本分析。

---

## 第40章：【高级篇综合实战】构建金融级任务调度平台

**定位**：融会贯通高级篇知识，产出可交付的生产级方案。

**核心内容**：
- 场景：为一家金融科技公司从零构建自研任务调度平台
- 架构设计：Airflow 核心 + 自定义 Provider（合规审计） + 混合 Executor + 多级告警
- 功能实现：
  - 自定义 Executor：基于内部消息队列，支持任务优先级抢占
  - 合规审计 Provider：每次 DagRun 自动生成审计报告（PDF）
  - 多级审批工作流：人工节点（`@task.branch` + ExternalTaskSensor）
  - 数据血缘追踪：OpenLineage 集成 + 自定义 Asset Backend
  - SLA 强制熔断：超时自动终止 + 通知值班 + 自动切备份链路
- 性能指标：10000+ Dag、日百万级 TaskInstance、P99 延迟 < 5s、全年可用性 > 99.99%

---

# 附录与资源

## 附录 A：源码阅读路线图

1. 入口：`airflow-core/src/airflow/jobs/scheduler_job_runner.py` → `_run_scheduler_loop`
2. Dag 定义：`task-sdk/src/airflow/sdk/definitions/dag.py` → `DAG` 类
3. 任务执行：`task-sdk/src/airflow/sdk/execution_time/task_runner.py`
4. API 通信：`airflow-core/src/airflow/api_fastapi/execution_api/`
5. 模型定义：`airflow-core/src/airflow/models/`

## 附录 B：环境搭建速查卡

- Docker Compose 最小化部署命令
- airflow.cfg 核心配置速查表
- 常见错误码与排查（502/504/权限/数据库连接）

## 附录 C：推荐工具链

- 压测：`airflow dags backfill`、`wrk`、自定义脚本
- 调试：Python debugger（pdb）、IPython、日志注入
- 监控：Prometheus + Grafana、StatsD Exporter
- 容器：Docker、Kubernetes、Helm
- CI/CD：GitHub Actions、GitLab CI、Jenkins

## 附录 D：思考题参考答案索引

- 基础篇思考题答案：见各章末尾或本附录对应小节
- 中级篇思考题答案：见各章末尾或本附录对应小节
- 高级篇思考题答案：见各章末尾或本附录对应小节

---

> **章节统计**：基础篇 16 章（1-16）/ 中级篇 15 章（17-31）/ 高级篇 9 章（32-40）/ 总计 40 章
