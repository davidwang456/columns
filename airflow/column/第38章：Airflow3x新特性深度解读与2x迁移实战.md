# 第38章：Airflow 3.x 新特性深度解读与2.x迁移实战

## 项目背景

Airflow 3.0的发布标志着这个十年之久的调度框架迎来了自2.0以来最重大的架构变革。核心团队从根本上重塑了调度器的安全边界和执行模型——Task SDK从核心项目中分离为独立包、Dag File Processor被强制隔离到独立进程、Worker不再直连Metadata数据库而是通过Execution API通信、Asset机制赋予了数据感知调度能力。

对某金融科技公司的平台团队来说，3.x的吸引力与风险并存：一方面，新架构解决了许多2.x的顽疾——Scheduler的安全隔离问题、Worker直接访问DB的安全隐患、Dag依赖管理的混乱；另一方面，从2.x到3.x的迁移并非无缝升级，配置格式变化、Operator API变动、Dag文件的兼容性都是需要逐一验证的课题。

小白已经提前两周在Staging环境上完成了3.0的部署，并整理了一份详细的迁移笔记。小胖则负责评估当前50个Dag的业务兼容性。大师作为技术顾问，随时准备拆解底层原理。

## 项目设计

小白打开终端，熟练地敲下`pip list | grep airflow`，屏幕上显示着3.0.1的版本号。

**小白**："小胖哥，你看这个最大的变化——Task SDK彻底独立了。以前我们装`apache-airflow`这一个包，所有Operator的依赖全给你拉下来，光是`amazon` Provider就要装`boto3`全家桶。现在Task SDK是单独的`airflow-sdk`包，Provider也各自独立版本号，语义化版本管理彻底分开。"

**小胖**（翻看着Staging环境的Dag列表）："但这对我们的Dag文件有什么影响？我那些`from airflow.operators.python import PythonOperator`的导入还能用吗？"

**大师**（在白板上画出2.x与3.x的架构对比图）："导入语句基本保持兼容，因为Task SDK通过符号链接保持了对旧导入路径的支持。真正需要注意的是这五个关键变化：

**1. Task SDK分离**：`airflow-sdk`是一个独立发行版，有自己的`pyproject.toml`和`uv.lock`。这意味着你在Dag文件中依赖的库（如`pandas`、`scikit-learn`）不再和Airflow核心依赖混在一起。Dag作者的依赖声明与平台运维的依赖管理终于解耦了。

**2. Dag Processor强制分离**：在2.x中，Dag File Processor是Scheduler的一个子进程，可以和Scheduler共享进程空间。在3.x中，DFP运行在完全独立的进程/容器中，有单独的网络命名空间——它无法直接连接Metadata DB，只能通过Execution API的上报接口提交序列化Dag。这是安全边界的质变。

**3. Execution API替代直连DB**：这是最核心的架构变化。在2.x中，Worker执行完任务后直接写`task_instance`表。在3.x中，Worker通过Execution API（基于FastAPI的REST服务）的`PATCH /task-instances/{id}/state`端点上报状态，API Server再写入DB。Worker永远不持有数据库连接凭证。

**4. Asset（数据感知调度）**：传统TimeTable只能表达时间依赖，Asset可以表达数据依赖——'等上游Dag的某个数据集更新完成，下游再触发'。它在底层是基于DatasetEvent的状态机，可以和TimeTable混合使用。

**5. Deferrable成为一等公民**：2.x的deferrable task是一种'可加可减'的特性；3.x中Triggerer被重新设计，成为架构的标准组件。Sensor和Deferrable Operator共享同一个异步运行时，触发条件用`@task.deferrable`装饰器声明。"

**小胖**："那迁移的坑主要在哪？"

**大师**伸出三根手指："**配置文件**——`airflow.cfg`中的很多section改名了，比如`[celery]`变成`[executors.celery]`；**Dag文件**——`on_success_callback`和`on_failure_callback`的签名变了，增加了`context`的字段；**Operator API**——一些废弃参数彻底移除，比如`provide_context`。但Airflow官方提供了`airflow-ctl migrate 2to3`一键检测工具，能自动扫描所有Dag文件中的不兼容项。"

## 项目实战

### Step 1：环境准备与兼容性检测

在开始迁移之前，必须理解Airflow 3.0的一系列架构决策背后的"为什么"。让我们从最核心的Task SDK分离谈起：

**Task SDK分离的设计原理**：在2.x时代，`apache-airflow`这个单一包承载了三重职责——给Dag作者提供API（`DAG`、`@task`、`PythonOperator`）、给平台运维提供执行引擎（Scheduler、Executor）、给Worker提供执行运行时。这种"大统一"导致了严重的依赖冲突：Dag作者想用最新版的`boto3`，但Airflow核心因为兼容性问题锁死了旧版本；Dag作者装的`pandas`新版本引入了Breaking Change，导致Scheduler解析Dag时直接Crash。

3.x将Task SDK拆分为独立的`airflow-sdk`包，核心思路是**依赖隔离**——Dag作者在自己的虚拟环境中安装Task SDK和所需的库，平台运维在另一个虚拟环境安装Airflow Core。两者的依赖树互不干扰。这就好比餐厅后厨和前厅的分工——大厨专心做菜（平台运维管Core），服务员专心接待顾客（Dag作者管SDK），互不干涉对方的工具。

在Staging环境安装Airflow 3.x：

```bash
# 安装 Task SDK（Dag作者侧）
uv pip install "apache-airflow-sdk>=3.0,<4.0"

# 安装 Airflow Core（平台运维侧）
uv pip install "apache-airflow>=3.0,<4.0"

# 安装必要的 Provider
uv pip install "apache-airflow-providers-cncf-kubernetes>=10.0.0"
uv pip install "apache-airflow-providers-amazon>=9.0.0"
```

然后运行兼容性检测工具：

```bash
# 一键扫描所有Dag文件中的不兼容项
airflow-ctl migrate 2to3 --dag-folder /opt/airflow/dags --output report.json

# 输出示例
# {
#   "total_dags": 50,
#   "incompatible": 14,
#   "warnings": [
#     {"dag_id": "etl_finance", "issue": "provide_context is removed", "line": 45, "fix": "Use **context"},
#     {"dag_id": "ml_training", "issue": "Dependencies in DAG definition need explicit imports", "line": 12},
#     ...
#   ]
# }
```

检测报告显示50个Dag中有14个存在兼容性问题，主要集中在三类：

1. `provide_context=True` 移除（8个Dag）
2. 顶层重型库导入导致DFP解析超时（4个Dag，3.x的DFP超时更严格）
3. 自定义Operator的`execute`方法返回值不兼容（2个Dag）

### Step 2：配置文件迁移

2.x的`airflow.cfg`到3.x的`airflow.cfg`主要变化：

```ini
# ====== 2.x 配置 ======
[core]
executor = CeleryExecutor
sql_alchemy_conn = postgresql://...
dags_folder = /opt/airflow/dags
store_serialized_dags = True

[celery]
broker_url = redis://...
result_backend = db+postgresql://...

[scheduler]
min_file_process_interval = 30

# ====== 3.x 配置 ======
[core]
executor = airflow.executors.celery_executor.CeleryExecutor
sql_alchemy_conn = postgresql+psycopg2://...
dags_folder = /opt/airflow/dags

# Celery配置独立为执行器子节
[executors.celery]
broker_url = redis://...
result_backend_type = database

[scheduler]
min_file_process_interval = 30

# 新增：Dag Processor配置
[dag_processor]
parsing_processes = 4
dag_bundle_storage = local
```

关键变化：
- `executor`从简短字符串改为完整路径
- `[celery]` section 迁移到 `[executors.celery]`
- 新增 `[dag_processor]` section，Dag Bundle机制需要配置存储后端
- `result_backend` 简化为 `result_backend_type`

### Step 3：Dag文件兼容性修改

**问题一：`provide_context` 移除**

```python
# 2.x 写法（不兼容）
task_a = PythonOperator(
    task_id="process_data",
    python_callable=my_function,
    provide_context=True,  # 3.x中已移除！
)

def my_function(**context):  # 隐式传递context
    ti = context["ti"]
    ti.xcom_push(key="result", value=42)

# 3.x 写法
from airflow.sdk import task

@task
def my_function(ti=None):
    ti.xcom_push(key="result", value=42)

# 或者保留PythonOperator的写法
task_a = PythonOperator(
    task_id="process_data",
    python_callable=my_function,
    # provide_context不再需要，**context一直可用
)
```

**问题二：Callback签名变化**

```python
# 2.x 写法
def on_success_callback(context):
    dag_id = context["dag"].dag_id
    print(f"Dag {dag_id} succeeded")

# 3.x 写法——context字段变化
def on_success_callback(context):
    dag_id = context["dag_run"].dag_id  # 注意层级变化
    print(f"DagRun {dag_id} succeeded")
```

**问题三：顶层重导入优化**

3.x的Dag Processor有严格的解析超时限制（默认60秒）：

```python
# 优化前：顶层import拉取350MB依赖
import tensorflow as tf  # 解析耗时 5.2s
import torch  # 解析耗时 3.1s

# 优化后：TYPE_CHECKING + 懒加载
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import tensorflow as tf
    import torch

with DAG("ml_pipeline", ...):
    @task
    def train_model(**context):
        import tensorflow as tf  # 仅在Worker执行时导入
        model = tf.keras.Sequential([...])
        return model.to_json()
```

### Step 4：Execution API对接

3.x最核心的变化：Worker不直连DB。需要在Worker和API Server之间配置JWT认证：

```python
# Worker 环境变量配置
export AIRFLOW__EXECUTION_API__JWT_SECRET="your-256-bit-secret"
export AIRFLOW__EXECUTION_API__SERVER_URL="http://airflow-api:8080/execution"

# Worker执行任务时的内部调用（SDK自动处理）
# airflow-sdk/src/airflow/sdk/execution_time/task_runner.py
from airflow.sdk.execution_time.comms import TaskExecutionClient

class TaskRunner:
    def __init__(self, task_instance_id: str, jwt_token: str):
        self.client = TaskExecutionClient(
            base_url=EXECUTION_API_URL,
            token=jwt_token,  # 短生命周期JWT，仅限当前TaskInstance
        )

    async def report_heartbeat(self) -> dict:
        return await self.client.patch(
            f"/task-instances/{self.task_instance_id}/state",
            json={"state": "running", "heartbeat": datetime.utcnow().isoformat()},
        )

    async def complete(self, return_value: Any) -> dict:
        return await self.client.patch(
            f"/task-instances/{self.task_instance_id}/state",
            json={"state": "success", "return_value": return_value},
        )
```

### Step 5：Asset数据感知调度

Asset是Airflow 3.0最具前瞻性的特性之一。在2.x时代，"任务依赖"只能在Dag内部表达（Task A >> Task B），跨Dag的依赖只能通过`ExternalTaskSensor`轮询——效率低下且语义模糊。Asset将"数据"提升为一等公民，让调度器能够基于数据可用性做决策。

先理解Asset的底层实现：

```python
# Asset的本质——一个带URI标识的数据集描述
from airflow.sdk.definitions.asset import Asset

# Asset不仅仅是字符串，它是带元数据的对象
raw_data = Asset(
    uri="s3://datalake/raw/sales/{{ ds }}",
    name="raw_sales_data",
    group="sales_pipeline",
    description="原始销售数据，由上游CDP系统每日同步",
    extra={
        "format": "parquet",
        "compression": "snappy",
        "retention_days": 90,
        "compliance_level": "PII_SENSITIVE",  # 合规级别标记
    },
)
```

当上游Dag完成并"产出"（produce）该Asset时，Airflow会自动在`dataset_event`表中记录一条事件。下游Dag的Schedule条件被解析时，Asset Backend会检查事件表，确认所需的数据是否已就绪。这一机制的优雅之处在于——**上游Dag不需要知道下游是谁，下游Dag也不需要知道上游的具体时间表，两者通过数据资产解耦**。

在实际使用中，Asset有三种触发模式：
- **纯Asset触发**：`schedule=[raw_data]`——数据到达即触发，与时间无关
- **混合触发**：`schedule=[raw_data, "@daily"]`——数据到达 AND 满足daily窗口
- **条件触发**：`schedule=[raw_data | cleaned_data]`——任意一个Asset就绪即触发（OR语义）

利用Asset实现"数据就绪即触发"：

```python
from airflow import DAG, Asset

# 定义两个数据集Asset
raw_data = Asset("s3://datalake/raw/sales/{{ ds }}")
cleaned_data = Asset("s3://datalake/cleaned/sales/{{ ds }}")

# 上游Dag：产出raw_data
with DAG("ingest_sales", schedule="@daily", assets=[raw_data]):
    ingest_task = PythonOperator(task_id="ingest", ...)

# 下游Dag：消费raw_data并产出cleaned_data
with DAG(
    "clean_sales",
    schedule=[raw_data],  # 基于Asset触发，而非cron
    assets=[cleaned_data],
):
    clean_task = PythonOperator(task_id="clean", ...)

# 混合触发：时间+Asset条件
with DAG(
    "final_report",
    schedule=[cleaned_data, "@daily"],  # 每天满足资产+e7b时触发
):
    report_task = PythonOperator(task_id="report", ...)
```

`[raw_data, "@daily"]`这种混合触发是3.x的特色——即使数据集在当天已经更新，也要等到daily时间窗口才执行。

### Step 6：Deferrable重构

3.x中Deferrable是标准Trigger模式：

```python
from airflow.sdk import DAG, task
from airflow.sdk.definitions.trigger import TaskStateTrigger
from airflow.providers.amazon.aws.triggers.s3 import S3KeySensorTrigger

@task
def wait_for_file(bucket: str, key: str):
    """返回一个Trigger，告诉Triggerer我需要等待的文件"""
    return S3KeySensorTrigger(bucket=bucket, key=key)

@task
def process_file(returned_value):
    print(f"File ready: {returned_value}")

with DAG("deferrable_example"):
    trigger = wait_for_file("my-bucket", "data/file.csv")
    process_file(trigger)
```

### Step 7：完整迁移测试验证

```bash
# 1. 并行回填50个Dag以验证兼容性
for dag_id in $(airflow dags list -o plain | awk '{print $1}'); do
  airflow dags backfill \
    --reset-dagruns \
    --start-date "2024-12-01" \
    --end-date "2024-12-03" \
    "$dag_id" &
done
wait

# 2. 验证Execution API通信正常
curl -H "Authorization: Bearer $(airflow token create)" \
  http://localhost:8080/execution/api/v1/health

# 3. 验证Asset触发链
airflow dags trigger clean_sales --run-id test_asset_trigger

# 4. 性能基线对比
airflow dags report --output json | jq '{
  total_dags, 
  parsing_time_avg, 
  scheduler_loop_time
}'
```

### 迁移踩坑记录与解决方案

| 坑 | 现象 | 根因 | 解决方案 |
|----|------|------|----------|
| DAG导入失败 | `ImportError: cannot import name 'DAG' from 'airflow'` | Task SDK路径分离 | `from airflow.sdk import DAG` 或保留`from airflow import DAG`（兼容层） |
| Celery任务卡住 | Worker日志显示`Connection refused` | Broker URL配置路径变化 | 检查`[executors.celery]` section |
| Triggerer不触发 | Sensor永远pending | Trigger注册表变化 | 升级Provider到3.x兼容版本 |
| Dag Bundle超时 | DFP日志`TimeoutError` | 顶层重导入 + 新超时限制 | 懒加载模式 |
| XCom写入失败 | `Forbidden: direct DB access blocked` | Execution API强制隔离 | 通过`@task`装饰器自动路由 |
| 权限问题 | Dag权限列表为空 | RBAC模型调整 | 重新运行`airflow db migrate` |

除了上述技术性踩坑，在迁移过程中我们还发现了一个容易被忽视的"软坑"——**团队的工作流程适配**。在2.x时代，开发同学习惯于在本地运行`airflow tasks test`来调试Dag，这个命令会直连Metadata DB。在3.x中该命令被废弃，取而代之的是`airflow dags test`——它通过Execution API与Airflow Core通信，不再需要本地数据库连接。这意味着开发环境的搭建流程也需要同步更新：

```bash
# 2.x 开发环境
pip install apache-airflow==2.9.0
export AIRFLOW__CORE__SQL_ALCHEMY_CONN=postgresql://dev:dev@localhost:5432/airflow_dev
airflow tasks test my_dag my_task 2024-01-01

# 3.x 开发环境
# 第一步：安装Task SDK（Dag作者侧）
uv pip install apache-airflow-sdk>=3.0.0
# 第二步：启动本地轻量Execution API（用于调试）
uv pip install apache-airflow>=3.0.0  # 提供airflow命令工具
airflow standalone --execution-api-only  # 仅启动API，不启动Scheduler/Worker
# 第三步：测试Dag
airflow dags test my_dag --task-id my_task --logical-date 2024-01-01
```

这种工作流变化虽然繁琐，但从安全角度是必要的——它强化了"开发环境也不应该持有Production DB凭证"的最佳实践。

### 迁移前后对比

| 维度 | 2.x 集群 | 3.x 集群 | 变化 |
|------|----------|----------|------|
| Scheduler CPU | 68% | 31% | ↓ 54% （DFP独立进程） |
| Worker DB连接数 | 96 | 0 | ↓ 100%（完全隔离） |
| 安全审计通过 | 7/12项 | 12/12项 | +5项 |
| Dag解析速度 | 8.3s | 2.1s | ↓ 75% |
| 依赖冲突次数/月 | 4-6次 | 0次 | ↓ 100%（SDK分离） |
| 数据感知触发延迟 | N/A | 12s | 新增能力 |

其中一个特别值得注意的变化是Worker的启动速度。在2.x中，由于Worker需要加载完整的`airflow.models`包和所有Provider依赖，每个新Worker Pod的启动时间长达90秒。在3.x中，Worker只需要Task SDK运行时，启动时间降到了15秒——这6倍的提升对KEDA之类弹性伸缩场景的意义极大，因为从伸缩信号发出到Pod Ready的延迟直接决定了突发流量时的排队长短。

此外，依赖冲突的消除不仅体现在数字上，更体现在工程师的心理负担上。小胖回忆道："以前每次版本升级我都要提前两周做兼容性测试，现在只需要确认Task SDK和Core的版本在兼容矩阵里就行——这省出来的时间，我可以去做更有价值的性能优化和架构改进。依赖管理从'情绪消耗'变成了'自动化检查'，这可能是3.x对我个人来说最大的收益。"

## 项目总结

从2.x到3.x的迁移，核心不是"升级版本号"，而是**拥抱一个新的安全架构模型**。Task SDK分离让Dag作者和平台运维各司其职——当某个Dag作者因为在自己的虚拟环境中安装了最新版`boto3`而兴高采烈时，平台运维不再需要担心这个`boto3`会让整个Scheduler崩溃。Execution API让Worker彻底脱离数据库依赖——这不仅是安全边界的加固，更让Worker的容器镜像体积减少了60%（不再需要安装`psycopg2`和SQLAlchemy）。Asset让任务调度从"时间触发"进化到"数据触发"，这种语义层面的变革比任何参数调整都更具颠覆性。

50个Dag的完整迁移验证了这条路径的可行性，但迁移过程中暴露的问题也提醒我们：**技术架构的升级总是比想象中更复杂，因为真正需要适配的不是代码，而是人和流程。** 开发团队花了整整两天时间才适应"本地调试必须启动Execution API"的新流程；运维团队在配置文件中翻来覆去地找`[celery]` section却发现它已经搬家了；Dag作者们对`provide_context`的移除感到困惑——"我一直是这么写的，为什么3.x就不行了？"

这些摩擦是不可避免的，因为每一次架构升级本质上都是一次"旧习惯的淘汰"。但Airflow 3.x给出的答案是令人信服的——牺牲一些短期便利性，换取长期的架构清晰度和安全性。

展望未来，3.x的架构为下一步的演进铺平了道路：Execution API的存在使得"多集群调度"成为可能（一个中心化的API Server管辖多个K8s集群的Worker）；Asset机制可以与数据湖的元数据服务（如AWS Glue Catalog、Databricks Unity Catalog）深度整合；Deferrable框架的完善使得非阻塞异步编程在数据管道中不再是一种特殊模式，而是默认范式。

### 思考题

1. Execution API的JWT Token是短生命周期的（默认5分钟），如果某个长任务（运行超过5分钟）在Token过期时需要上报心跳，SDK该如何安全地续签Token？请设计一个Token刷新机制，要求不能将长期凭证（如API Key）暴露给Worker进程。

2. Asset机制中，如果一个下游Dag同时依赖了`Asset("s3://datalake/A")`和`asset("s3://datalake/B")`，且两者由不同的上游Dag产出，到达时间存在分钟级的时间差。Airflow是如何保证"所有Asset都就绪"时才触发下游的？如果其中一个Asset从未产生（比如上游Dag失败），下游是否会永远等待？请设计一种超时兜底机制。
