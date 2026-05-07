# 第1章：Airflow 术语全景与架构原理

## 1 项目背景

某互联网公司的数据团队正陷入一场"调度地狱"。数据仓库每天运行着 200+ 个脚本，依赖关系错综复杂——ETL 任务 A 依赖上游文件到达，任务 B 又依赖 A 的输出，任务 C 则需要同时等待 B 和外部 API 的数据。这些脚本通过 cron 定时触发，但 cron 只管"到点执行"，完全不管依赖是否满足、上游是否成功。

最近一次事故令人记忆犹新：上游 Flink 任务延迟 2 小时落盘，下游 cron 脚本却照常执行，读到了不完整的数据文件，导致当天报表全部错误。运维同学凌晨 3 点爬起来手动重跑了 40 多个脚本，折腾到天亮才修复。

> "cron 就像一个没脑子的闹钟，"数据架构师老张在复盘会上说，"它只管响，不管你是不是还在睡觉。"

这正是 Apache Airflow 诞生要解决的核心问题——**编排**（Orchestration）。Airflow 不是简单的定时器，而是一个懂依赖、有状态、可编程的工作流调度平台。它让你用 Python 代码描述任务之间的依赖关系，然后自动在正确的时机、以正确的顺序执行它们。

本章作为专栏的开篇，将带你建立 Airflow 的完整术语体系和架构认知，为后续的实战修炼打下坚实基础。

---

## 2 项目设计

**小胖**（吃着薯片，盯着屏幕上密密麻麻的 crontab 配置）："老张不是说 Airflow 能解决咱的问题吗？我看了半天文档，什么 Dag、Task、Operator 的，这不就是把 crontab 换了个马甲？"

**大师**（放下咖啡杯）："来，我给你画张图。crontab 只管时间，但真实的数据工作流是时间和依赖的双重组合。Airflow 把工作流抽象成了 Dag——有向无环图。"

**小白**（推了推眼镜）："有向无环图……是不是就是数据结构课上学的那种？节点不能有循环引用？"

**大师**："对，而且 Airflow 里每个节点叫 Task，边代表依赖关系。A → B 意味着 B 必须在 A 成功之后才能跑。这比 crontab 高级在哪儿呢？crontab 里你要实现 A → B → C，得手动计算时间差，A 跑 5 分钟，你就设 B 在 5 分钟后启动——但 A 偶尔跑 10 分钟呢？"

**小胖**："那不就直接翻车了……"

**大师**："没错。Airflow 的调度器（Scheduler）会持续监控每个 Task 的状态，只有上游全部成功后，下游才会被触发。这就是事件驱动的调度，而不是盲目计时。"

**小白**："但 Airflow 怎么知道一个任务跑成功了还是失败了？"

**大师**："好问题。每个任务执行后会向元数据库（Metadata DB）汇报状态。这张数据库是 Airflow 的心脏，存着所有 Dag 的定义、DagRun（每次运行实例）、TaskInstance（每个任务实例）的状态。你可以把 Metadata DB 理解为 Airflow 的'记忆'。"

**小胖**："那谁来真正执行任务呢？"

**大师**："执行器（Executor）。LocalExecutor 直接在调度器进程里跑任务，适合单机；CeleryExecutor 通过消息队列分发给远程 Worker，适合分布式；KubernetesExecutor 给每个任务起一个 Pod，资源隔离最彻底。不同 Executor 就像不同的快递公司——有的是同城闪送，有的是全国物流。"

**小白**："我还看到文档里有 Dag Processor 和 Triggerer……"

**大师**："这两个是 Airflow 3.x 架构的核心。Dag Processor 负责解析你的 Dag 文件，序列化成 JSON 存到数据库。Scheduler 只读序列化后的数据来调度，**从不执行你的用户代码**。这是安全隔离的关键设计。Triggerer 则专门处理延迟任务（Deferrable Task），比如等一个文件到达可能要等几小时，传统 Sensor 会一直占着 Worker 槽位，Triggerer 用异步方式处理，大幅节省资源。"

**小胖**：*在纸上画了几个方框* "所以整体大概是：Dag 文件 → Dag Processor 解析 → 存 DB → Scheduler 读 DB 创建 DagRun → Executor 分发任务 → Worker 执行 → 结果回写 DB？"

**大师**："总结得不错。在 Airflow 3.x 里，Worker 还不直连 DB——它通过 Execution API 来读取连接信息、写入 XCom、汇报状态。每个 Worker 拿到的只是一个短生命周期的 JWT Token，只够完成当前任务。"

> **技术映射**：如果把 Airflow 比作一家物流公司，Dag 是运单模板，Task 是每个配送步骤，Scheduler 是调度中心，Executor 是配送网络，Metadata DB 是运单跟踪系统，Execution API 是快递员的扫码枪——每个环节各司其职、信息互通。

---

## 3 项目实战

### 环境准备

在开始编写代码之前，我们需要用 Docker Compose 快速启动一个 Airflow 3.x 环境。这也是验证你对架构理解的第一个实战。

**依赖要求**：
- Docker Desktop 4.x+
- 至少 4GB 可用内存
- 操作系统：macOS / Linux / Windows (WSL2)

**步骤一：获取官方 Docker Compose 文件**

```bash
# 创建项目目录
mkdir airflow-lab && cd airflow-lab

# 下载 Airflow 3.x 官方 docker-compose.yaml
curl -LfO 'https://airflow.apache.org/docs/apache-airflow/3.0.0/docker-compose.yaml'
```

**步骤二：初始化环境**

```bash
# 创建必要的目录
mkdir -p dags logs plugins config

# 初始化数据库和创建管理员账户
docker compose up airflow-init
```

初始化成功后会看到：
```
airflow-init-1  | Upgrades done
airflow-init-1  | Admin user airflow created
airflow-init-1  | 2.10.0
```

**步骤三：启动所有服务**

```bash
docker compose up -d
```

**步骤四：验证服务运行状态**

```bash
docker compose ps
```

预期输出：
```
NAME                    STATUS              PORTS
airflow-scheduler-1     Up                  ...
airflow-webserver-1     Up                  0.0.0.0:8080->8080/tcp
airflow-dag-processor-1 Up                  ...
airflow-triggerer-1     Up                  ...
airflow-postgres-1      Up                  5432/tcp
airflow-redis-1         Up                  6379/tcp
```

**步骤五：访问 Web UI**

打开浏览器访问 `http://localhost:8080`，使用以下凭证登录：
- 用户名：`airflow`
- 密码：`airflow`

你将看到 Airflow 的 Dashboard 页面，顶部是 Dag 列表（目前为空），右侧是运行状态统计。

**步骤六：创建你的第一个 Dag 文件**

在 `dags/` 目录下创建 `hello_airflow.py`：

```python
from datetime import datetime, timedelta
from airflow.sdk import DAG
from airflow.sdk.operators.bash import BashOperator
from airflow.sdk.operators.python import PythonOperator

default_args = {
    "owner": "team-data",
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
}

with DAG(
    dag_id="hello_airflow",
    default_args=default_args,
    description="我的第一个 Airflow Dag",
    schedule=timedelta(days=1),
    start_date=datetime(2025, 1, 1),
    catchup=False,
    tags=["tutorial"],
) as dag:

    # Task 1: 打印欢迎信息
    t1 = BashOperator(
        task_id="say_hello",
        bash_command='echo "Hello from Airflow! Today is {{ ds }}"',
    )

    # Task 2: Python 处理
    def _process_data(**context):
        execution_date = context["execution_date"]
        print(f"正在处理 {execution_date} 的数据...")
        return {"records_processed": 1024}

    t2 = PythonOperator(
        task_id="process_data",
        python_callable=_process_data,
    )

    # Task 3: 打印完成信息
    t3 = BashOperator(
        task_id="say_goodbye",
        bash_command='echo "任务完成！处理了 {{ ti.xcom_pull(task_ids="process_data")["records_processed"] }} 条记录"',
    )

    # 定义依赖：t1 → t2 → t3
    t1 >> t2 >> t3
```

**步骤七：等待 Dag 加载并触发运行**

Dag Processor 会每 30 秒扫描一次 `dags/` 目录。大约 1 分钟后，你就能在 Web UI 的 Dag 列表里看到 `hello_airflow`。点击左侧的播放按钮手动触发，然后进入 Grid 视图观察任务执行状态。

> **可能遇到的坑**：如果 Dag 列表一直不出现，检查 `docker compose logs airflow-dag-processor-1` 是否有 Python 语法错误或 import 失败。常见原因是 `dags/hello_airflow.py` 文件权限问题——确保 Docker 容器内的 `airflow` 用户可读。

### 完整代码清单

以上 `hello_airflow.py` 即完整代码。后续章节将以此为基础逐步扩展。

### 测试验证

```bash
# 方式一：通过 CLI 测试
docker exec -it airflow-scheduler-1 airflow dags test hello_airflow 2025-01-01

# 方式二：通过 curl 调用 REST API 触发
curl -X POST "http://localhost:8080/api/v2/dags/hello_airflow/dagRuns" \
  -H "Content-Type: application/json" \
  --user "airflow:airflow" \
  -d '{"execution_date": "2025-01-01T00:00:00Z"}'
```

---

## 4 项目总结

### 优点 & 缺点

| 维度 | Airflow | Cron |
|------|---------|------|
| 依赖管理 | 原生 DAG 依赖，支持条件和分支 | 无内置支持，需手动编排 |
| 状态追踪 | 完整的 DagRun/TaskInstance 状态机 | 仅退出码 |
| 可视化 | Web UI 提供 Grid/Graph/Gantt 视图 | 无 |
| 动态参数 | Jinja 模板 + Params 系统 | 仅环境变量 |
| 可扩展性 | Executor 插件 + Provider 生态 | 几乎不可扩展 |
| 学习曲线 | 较高，需理解系列术语 | 极低 |
| 部署复杂度 | 多组件（Scheduler/Web/DB/Worker） | 单一 cron daemon |

### 适用场景

1. **数据仓库 ETL 流水线**：多级依赖的数据清洗、聚合、入仓
2. **机器学习训练流水线**：数据准备 → 特征工程 → 模型训练 → 评估 → 部署
3. **报表生成调度**：日报、周报、月报的自动生成与分发
4. **跨系统数据同步**：MySQL → Hive、S3 → Redshift 等多源汇同步
5. **DevOps 自动化任务**：定时备份、日志清理、证书续期

**不适用场景**：
- 毫秒级实时任务调度（Airflow 最小调度间隔通常为 30 秒）
- 流式数据处理（应使用 Flink/Kafka Streams，Airflow 只做编排层）

### 注意事项

1. **不要用 SQLite 上生产**：SQLite 不支持并发写入，多 Scheduler 实例会导致锁冲突
2. **start_date 是静态的**：修改 start_date 会触发新的回填行为，务必理解后再操作
3. **Executor 选型要慎重**：LocalExecutor 适合开发，CeleryExecutor 适合生产，KubernetesExecutor 适合极致隔离需求
4. **Dag 文件保持轻量**：不要在 Dag 文件顶层做重 IO 操作（如读取大文件、连接数据库），这些应该放在 Task 内部

### 常见踩坑经验

1. **"Dag not found in DagBag"**：代码报错未在 UI 上显示，需要在 Scheduler 日志中排查。通常是 import 了不存在的模块或变量名拼写错误。
2. **Dag 文件加载超时**：Dag 文件顶部 `import pandas` 可能导致加载极慢，因为 Dag Processor 每次扫描都会重新 import。解决方案：将重量级 import 移到 Task 函数内部。
3. **多个 Dag 文件中重复的 dag_id**：会导致 Web UI 只显示一个，另一个被静默覆盖。

### 思考题

1. 如果让你设计一个"多级审批工作流"（员工提交申请 → 经理审批 → 总监审批 → 财务打款），你会用什么 Airflow 结构来实现？提示：思考状态流转和重试机制。
2. Airflow 3.x 为什么要将 Dag Processor 和 Scheduler 分离？这在安全性和性能上分别带来什么好处？

*（答案将在后续章节中揭晓）*

---

> **本章完成**：你已建立 Airflow 的完整术语体系和架构认知。下一章我们将动手从零部署 Airflow 环境，真正感受"跑起来"的快乐。
