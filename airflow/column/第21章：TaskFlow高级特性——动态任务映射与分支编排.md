# 第21章：TaskFlow 高级特性——动态任务映射与分支编排

## 1 项目背景

数据平台负责人老李最近接手了一个"噩梦级"需求：为全国 500 多个门店生成每日运营报表，每个门店的数据需要单独抽取、清洗、汇总。运营团队要求"每个门店独立一个任务，失败不能互相影响，而且门店数量会随开店/关店动态变化"。

老李的第一版方案是写一个 Python 脚本，在脚本里用循环生成 500 个 `PythonOperator`。很快问题就来了：

```python
# 第一版：硬编码——噩梦的开始
for store_id in store_list:
    task = PythonOperator(
        task_id=f"process_store_{store_id}",
        python_callable=process_store_report,
        op_kwargs={"store_id": store_id},
    )
```

**痛点一：拓店/关店需要改 Dag 代码**。门店列表是自营业务的元数据，每月都在变化。每次新增门店，都需要运维同事手动修改 Dag 文件、提交 Git、重新部署。一个简单的 CSV 更新，硬生生拖成了 2 天的上线流程。

**痛点二：500 个 task 的 Dag 极难维护**。在 Web UI 的 Grid 视图里，500 个 task 节点展开后根本没法看；在 Graph 视图里，Dag 拓扑图变成了一团乱麻。更可怕的是，上游一个 "查询门店列表" 的任务需要被 500 个 task 同时依赖——任何一次依赖变更都要修改 500 行代码。

**痛点三：分支逻辑牵一发而动全身**。运营提了个新需求："如果工作日（周一至周五），走正常报表流程；如果周末，走快速汇总流程"。老李在 `PythonOperator` 里加了一堆 `if/else`，再用 `TriggerRule` 控制下游——结果 trigger_rule 配错了，周六的报表全部跳过，周一早上运营团队炸锅。

老李痛定思痛，开始研究 Airflow 3.x 的 TaskFlow 高级特性：**动态任务映射（Dynamic Task Mapping）**和**分支编排（Branching）**。这两把利器，恰好解决了他的全部痛点。

---

## 2 项目设计

**小胖**（一脸困惑）："大师，我之前写分支逻辑就是在一个 Python 函数里 `if/else`，然后返回不同的 `task_id`。但这个'动态任务映射'是什么概念？跟静态循环创建 task 有什么不同？"

**大师**："问得好。静态循环创建 task 是在 **Dag 解析时**就确定了任务的数量和拓扑结构。比如你在代码里写 `for i in range(500)`，解析器执行这个循环，生成 500 个 Operator 对象塞进 Dag。这 500 个对象长驻内存，Scheduler 每次调度都要遍历它们——所以当门店从 500 变成 5000 个时，解析速度会急剧下降。"

**小白**："那动态映射呢？"

**大师**："动态映射的核心思想是'延迟展开'。你在 Dag 里只定义一个**映射任务**（`MappedOperator`），它不直接执行。等到 Dag Run 实际运行时，Scheduler 根据上游任务返回的结果，**动态确定这一轮要生成多少个 task 实例**。关键是：Dag 解析时只看到一个 `MappedOperator`，无论最终展开成 10 个还是 10000 个实例，Dag 的结构都是简单清晰的。"

**小白**："这是不是类似函数的泛型？一个模板函数，根据输入类型生成不同数量的实例？"

**大师**："这个类比很精准。`MappedOperator` 就是任务模板，`.expand()` 传入的参数就是'实例化参数'。比如你上游查出门店列表 `["store_01", "store_02", ..., "store_500"]`，下游一个 `process_report.expand(store_id=store_list)` 就能生成 500 个并行任务实例，每个实例接收不同的 `store_id`。"

**小胖**："那分支编排呢？跟映射有关系吗？"

**大师**："分支编排解决的是另一个维度的问题——**路径选择**。映射是'一个任务模板生成 N 个平行实例'，分支是'从多条路径中选择一条（或多条）往下走'。两者经常组合使用：比如先根据数据量判断走快速通道还是完整通道（分支），然后在各自通道里动态生成对应数量的并行任务（映射）。"

> **核心源码映射**：动态映射由 `MappedOperator`（`task-sdk/src/airflow/sdk/definitions/mappedoperator.py:290`）和 `OperatorPartial`（同文件 `:166`）共同实现。分支逻辑由 `BaseBranchOperator`（`task-sdk/src/airflow/sdk/bases/branch.py:73`）和 `BranchMixIn`（同文件 `:33`）提供基础设施。装饰器 `@task.branch` 和 `@task.short_circuit` 分别是这两个底层类的 TaskFlow 封装。

**小白**："那 TriggerRule 跟分支又是什么关系？"

**大师**："TriggerRule 决定了任务'在什么条件下被触发执行'。默认是 `ALL_SUCCESS`——所有上游都成功才执行。但分支场景经常需要打破这个默认值。比如多个并行分支汇聚到一个 join 任务时，有些分支可能被跳过了，你用 `NONE_FAILED` 或 `ALL_DONE` 才能让 join 正确执行。TriggerRule 是分支编排完成闭环的关键胶水。"

---

## 3 项目实战

### 3.1 动态任务映射：`.expand()` 与 `MappedOperator`

**场景**：从上游查询门店列表，为每个门店并行生成运营报表。

```python
from airflow.sdk import DAG, task
from airflow.providers.common.compat.sdk import TriggerRule
from datetime import datetime


@dag(
    dag_id="dynamic_store_reports",
    schedule="@daily",
    start_date=datetime(2025, 1, 1),
    catchup=False,
    tags=["taskflow", "mapping"],
)
def store_report_pipeline():
    """每日门店报表——动态任务映射实战"""

    @task
    def query_stores():
        """查询当天需要生成报表的门店列表（模拟）"""
        # 实际场景：从数据库/API 获取活跃门店
        return [
            {"id": "SH001", "name": "北京朝阳店", "db": "bj_db"},
            {"id": "SH002", "name": "上海浦东店", "db": "sh_db"},
            {"id": "SH003", "name": "广州天河店", "db": "gz_db"},
            {"id": "SH004", "name": "深圳南山店", "db": "sz_db"},
            {"id": "SH005", "name": "成都武侯店", "db": "cd_db"},
        ]

    @task
    def distribute_tasks(stores: list[dict]) -> list[dict]:
        """分发逻辑：根据门店属性做预处理，返回可并行的任务参数列表"""
        # expand_kwargs 需要 list[dict] 格式
        return [{"store": s, "priority": "high"} for s in stores]

    @task(retries=2, retry_delay=timedelta(minutes=5))
    def generate_report(store: dict, priority: str):
        """为单个门店生成报表"""
        print(f"[{priority}] 正在生成 {store['name']} 报表...")
        print(f"  数据库: {store['db']}")
        # 实际业务逻辑：连接数据库、计算指标、生成 PDF
        import random
        if random.random() < 0.1:
            raise ValueError(f"{store['name']} 生成报表失败")
        return {"store": store["id"], "report": f"/reports/{store['id']}_daily.pdf"}

    @task(trigger_rule=TriggerRule.ALL_DONE)
    def summarize(reports: list[dict]):
        """汇总所有门店报表结果（无论某些门店是否失败）"""
        total = len(reports)
        succeeded = len([r for r in reports if r])
        print(f"报表生成完毕：共 {total} 个门店，成功 {succeeded} 个")
        return total

    # 静态依赖链
    stores_list = query_stores()
    task_params = distribute_tasks(stores_list)

    # 核心：动态映射
    # expand_kwargs 接收 list[dict]，每个 dict 作为被映射任务的 **kwargs
    reports = generate_report.expand_kwargs(task_params)

    # 汇聚：summarize 收集所有映射实例的结果（拉取的 XCom 是一个 list）
    summarize(reports)


store_report_pipeline()
```

**`.expand()` vs `.expand_kwargs()` 对比**：

| 方法 | 用途 | 输入 | 展开方式 |
|------|------|------|---------|
| `.expand(**kwargs)` | 按字段独立映射 | 每个 kwarg 是一个 `Sequence` | 笛卡尔积：`expand(a=[1,2], b=[3,4])` 生成 4 个实例 |
| `.expand_kwargs(xcom_arg)` | 按行映射 | 上游 XCom 返回的 `list[dict]` | 一对一：`[{a:1,b:3}, {a:2,b:4}]` 生成 2 个实例 |

**扩展阅读**：当使用 `.expand()` 时，Airflow 先将参数封装为 `DictOfListsExpandInput`（`task-sdk/src/airflow/sdk/definitions/_internal/expandinput.py:110`），然后对多个列表做笛卡尔积展开。使用 `.expand_kwargs()` 时，数据封装为 `ListOfDictsExpandInput`（同文件 `:220`），每个字典对应一个映射实例，逐行解包。

### 3.2 静态映射与动态映射的对比案例

为了直观理解"静态"和"动态"的差异，下面展示同一场景的两种实现：

```python
# === 方式一：静态映射（Static Mapping）——Dag 解析时确定任务数 ===
@dag(schedule="@daily", start_date=datetime(2025, 1, 1))
def static_store_pipeline():
    # 硬编码门店列表（一旦门店变化就要改代码）
    STORES = ["SH001", "SH002", "SH003", "SH004", "SH005"]

    for store_id in STORES:
        # 循环内创建 5 个独立的 Operator 对象
        @task(task_id=f"report_{store_id}")
        def make_report(sid=store_id):
            return f"report for {sid}"

        make_report()   # 调用注册到 Dag

static_store_pipeline()


# === 方式二：动态映射（Dynamic Mapping）——运行时按需展开 ===
@dag(schedule="@daily", start_date=datetime(2025, 1, 1))
def dynamic_store_pipeline():

    @task
    def get_stores():
        # 运行时从数据库获取——门店增减无需改 Dag
        return ["SH001", "SH002", "SH003", "SH004", "SH005"]

    @task
    def make_report(store_id: str):
        return f"report for {store_id}"

    # 只定义一个 MappedOperator，运行时展开为 N 个 task 实例
    stores = get_stores()
    make_report.expand(store_id=stores)

dynamic_store_pipeline()
```

**动态映射 vs 静态映射 对照表**：

| 维度 | 静态映射（`for` 循环） | 动态映射（`.expand()`） |
|------|----------------------|------------------------|
| 任务数量确定时机 | Dag 解析时 | Dag Run 运行时 |
| Dag 拓扑规模 | 随映射数量线性增长 | 始终只有 1 个 `MappedOperator` 节点 |
| UI Graph 视图 | N 个节点交织难辨 | 1 个映射节点，点击可展开 |
| 参数来源 | 硬编码或读配置文件 | 上游 XCom 动态传入 |
| 扩缩容 | 需修改 Dag 代码并重新部署 | 上游查询结果变化即可 |
| Dag 解析性能 | 映射量大时解析变慢 | 解析始终恒定（只有1个模板） |
| 失败隔离 | 单个 task 失败可能影响调度 | 每个映射实例独立，互不影响 |

### 3.3 `@task.branch`：分支编排

**场景**：根据当天是工作日还是周末，走不同的报表流程。工作日走完整流程（抽数 → 清洗 → 汇总），周末走快速通道（直出快报）。

```python
from airflow.sdk import DAG, task
from airflow.providers.common.compat.sdk import TriggerRule
from airflow.providers.standard.operators.empty import EmptyOperator
from datetime import datetime


@dag(
    dag_id="branching_workflow",
    schedule="@daily",
    start_date=datetime(2025, 1, 1),
    catchup=False,
    tags=["taskflow", "branch"],
)
def branch_workflow():
    """分支编排：工作日完整流程 vs 周末快速通道"""

    @task
    def check_day_type(**context):
        """检测当天是工作日还是周末"""
        from datetime import date
        # context["ds"] 是 dag 运行逻辑日期
        ds = context["ds"]
        weekday = date.fromisoformat(ds).weekday()  # 0=Mon ... 6=Sun
        return "workday" if weekday < 5 else "weekend"

    @task.branch()
    def choose_path(day_type: str) -> str:
        """分支决策：返回要执行的 task_id"""
        if day_type == "workday":
            return "workday_pipeline"
        else:
            return "weekend_pipeline"

    # 工作日分支
    @task(task_id="extract_full_data")
    def extract_full():
        print("抽取全量数据...")
        return "full_dataset"

    @task(task_id="clean_data")
    def clean():
        print("清洗数据...")
        return "clean_dataset"

    @task(task_id="aggregate_report")
    def aggregate():
        print("生成工作日完整报表...")
        return "workday_report"

    # 周末分支
    @task(task_id="quick_report")
    def quick():
        print("生成周末快报...")
        return "weekend_report"

    # 汇聚任务——无论哪个分支都执行
    join = EmptyOperator(task_id="join", trigger_rule=TriggerRule.NONE_FAILED_MIN_ONE_SUCCESS)

    # 通知任务——始终执行
    @task(trigger_rule=TriggerRule.ALL_DONE)
    def notify():
        print("发送每日报表通知...")
        return "sent"

    # 构建拓扑
    day_type = check_day_type()
    choice = choose_path(day_type)

    # 注意：分支任务下游直接连接分支路径的任务
    # 被分支"选中"的任务正常执行；未选中的被标记为 skipped
    extract_full() >> clean() >> aggregate()
    choice >> [
        aggregate(),    # 被选中 → 执行
        quick(),        # 未被选中 → skipped
    ] >> join >> notify()


branch_workflow()
```

**BranchPythonOperator 的执行机制**（源码参考 `task-sdk/src/airflow/sdk/bases/branch.py:73`）：
1. `choose_branch(context)` 方法（由用户覆写）返回一个或多个 `task_id`
2. `BaseBranchOperator.execute()` 调用 `do_branch()`（同文件 `:36`）
3. `do_branch()` 调用 `skip_all_except()` —— 将**所有未被选中的直接下游任务**标记为 `skipped`
4. `skip_all_except()` 是由 `SkipMixin`（同文件 `:33`）提供的核心方法，负责级联跳过

**关键原则**：`@task.branch` 返回的必须是**直接下游任务**的 `task_id` 字符串（或 `task_group_id`），不是更深层级的 task_id。如果想跳过整个 TaskGroup，返回 TaskGroup 的 group_id 即可，Airflow 会自动展开为其中的根任务。

### 3.4 `@task.short_circuit`：短路操作

ShortCircuit 与 Branch 的核心区别：
- **Branch**：选择**走哪条路**（多选一或多选多），未被选中的路径标记为 skipped
- **ShortCircuit**：判断**是否继续往下走**（是/否），返回 False 则短路——全部下游任务被跳过

```python
@dag(
    dag_id="short_circuit_demo",
    schedule="@daily",
    start_date=datetime(2025, 1, 1),
    catchup=False,
)
def short_circuit_workflow():
    """条件短路：数据为空则跳过全流程"""

    @task
    def check_data_availability():
        """检查上游数据是否就绪"""
        # 模拟场景：周日没有新数据
        from datetime import date
        ds = "{{ ds }}"
        if date.fromisoformat(ds).weekday() == 6:  # 周日
            return []
        return ["data_20250101.parquet", "data_20250102.parquet"]

    @task.short_circuit()
    def has_data(files: list) -> bool:
        """有文件 → 继续；无文件 → 短路"""
        if not files:
            print("没有数据文件，短路全流程")
        return bool(files)

    @task
    def extract(files: list):
        print(f"抽取 {len(files)} 个文件...")
        return len(files)

    @task
    def transform(record_count: int):
        print(f"转换 {record_count} 条记录...")
        return record_count

    @task
    def load(records: int):
        print(f"加载 {records} 条记录到数据仓库")
        return True

    @task(trigger_rule=TriggerRule.ALL_DONE)
    def record_status():
        """始终执行：记录本次运行状态"""
        print("记录运行状态到审计表")

    files = check_data_availability()
    check = has_data(files)
    extracted = extract(files)
    transformed = transform(extracted)
    loaded = load(transformed)
    check >> extracted >> transformed >> loaded >> record_status()
    check >> record_status()


short_circuit_workflow()
```

**ShortCircuitOperator 的短路逻辑**（源码参考 `providers/standard/src/airflow/providers/standard/operators/python.py:316`）：
- 若 Python callable 返回 `True` 或 truthy 值：下游任务正常执行
- 若返回 `False` 或 falsy 值：下游任务全部被跳过，Dag Run 标记为 successful（因为这是"有意为之"，不是错误）
- `ignore_downstream_trigger_rules=True`（默认）：跳过**所有**下游任务，无视它们的 trigger_rule
- `ignore_downstream_trigger_rules=False`：只跳过**直接**下游任务，间接下游的 trigger_rule 正常生效

### 3.5 TriggerRule 完整参考

TriggerRule 定义了任务的触发条件，是分支编排和汇聚逻辑的基石。所有值由 `TriggerRule` 枚举（`task-sdk/src/airflow/sdk/api/datamodels/_generated.py:541`）定义。

| TriggerRule | 语义 | 典型场景 |
|-------------|------|---------|
| `ALL_SUCCESS` | **所有**上游任务都成功才触发（**默认**） | 普通线性依赖 |
| `ALL_FAILED` | 所有上游任务都失败才触发 | 全部失败时触发告警/回滚 |
| `ALL_DONE` | 所有上游都**执行完毕**（无论成功/失败/跳过） | 最终收尾任务（日志、通知） |
| `ONE_SUCCESS` | 至少**一个**上游成功 | 多数据源只要有一个可用就行 |
| `ONE_FAILED` | 至少一个上游失败 | 检测到任一源有问题就告警 |
| `NONE_FAILED` | **没有任何**上游失败（成功或跳过都可以） | 汇聚任务：有成功有跳过也执行 |
| `NONE_FAILED_MIN_ONE_SUCCESS` | 至少一个成功且没有任何失败 | 分支汇聚：必须有数据产出 |
| `NONE_SKIPPED` | 没有任何上游被跳过 | 确保所有分支都执行过 |
| `ALWAYS` | **无条件**执行（无论上游什么状态） | 最常用的收尾任务 |

**TriggerRule 在分支汇聚中的应用示例**：

```python
# 场景：分支 A、B、C 可能被 skip，但 join 必须在有成功产出时执行
join = EmptyOperator(task_id="join", trigger_rule=TriggerRule.NONE_FAILED_MIN_ONE_SUCCESS)

# 场景：最终通知始终发送
final_notify = EmailOperator(
    task_id="notify",
    trigger_rule=TriggerRule.ALL_DONE,
    to="ops@company.com",
    subject="Dag Run {{ dag.dag_id }} 完成",
)
```

### 3.6 综合实战：动态映射 + 分支编排

**场景**：根据上游查询结果数量，动态生成 N 个并行处理任务，并根据数据量选择处理方式。

```python
from airflow.sdk import DAG, task
from airflow.providers.common.compat.sdk import TriggerRule
from airflow.providers.standard.operators.empty import EmptyOperator
from datetime import datetime, timedelta


@dag(
    dag_id="mapping_plus_branching",
    schedule="@daily",
    start_date=datetime(2025, 1, 1),
    catchup=False,
    tags=["advanced", "mapping", "branch"],
)
def advanced_pipeline():
    """综合实战：动态映射 + 分支编排 + 汇聚"""

    @task
    def query_upstream_partitions() -> list[str]:
        """查询上游 Hive 表当天的分区列表（模拟）"""
        partitions = [f"dt=2025-01-{i:02d}" for i in range(1, 11)]  # 10 个分区
        return partitions

    @task
    def check_data_volume(partitions: list[str]) -> dict:
        """检查总数据量并决定处理策略"""
        volume = len(partitions) * 10000  # 每个分区 10000 条
        return {
            "partitions": partitions,
            "total": volume,
            "strategy": "full" if volume > 30000 else "quick",
        }

    @task.branch()
    def route_by_strategy(meta: dict) -> str:
        """根据数据量选择处理策略"""
        if meta["strategy"] == "full":
            return "full_process_start"
        return "quick_process_start"

    # === 完整处理分支 ===
    start_full = EmptyOperator(task_id="full_process_start")

    @task
    def full_extract(partition: str):
        """完整抽取：每个分区单独处理（需要更细致）"""
        print(f"[FULL] 完整模式抽取分区 {partition}")
        return {"partition": partition, "rows": 10000}

    @task(trigger_rule=TriggerRule.ALL_DONE)
    def full_merge(results: list[dict]):
        total = sum(r["rows"] for r in results if r)
        print(f"[FULL] 完整模式汇总: {total} 行")

    # === 快速处理分支 ===
    start_quick = EmptyOperator(task_id="quick_process_start")

    @task
    def quick_batch(partitions: list[str]):
        """快速批量抽取（一次性处理所有分区）"""
        print(f"[QUICK] 快速模式批量处理 {len(partitions)} 个分区")
        return len(partitions) * 10000

    # === 公共收尾 ===
    @task(trigger_rule=TriggerRule.NONE_FAILED_MIN_ONE_SUCCESS)
    def notify(rows: int):
        print(f"处理完成，共 {rows} 行")
        return rows

    # 构建拓扑
    partitions = query_upstream_partitions()
    meta = check_data_volume(partitions)
    route = route_by_strategy(meta)

    # 完整分支：动态映射（每个分区一个 task 实例）
    route >> start_full >> full_extract.expand(partition=partitions) >> full_merge(meta)

    # 快速分支
    route >> start_quick >> quick_batch(partitions) >> notify(meta)


advanced_pipeline()
```

---

## 4 项目总结

### 核心能力对比

| 特性 | 静态循环 `for` | 动态映射 `.expand()` |
|------|--------------|-------------------|
| 任务数 | 解析时固化 | 运行时动态 |
| 拓扑简洁性 | O(N) 节点 | O(1) 节点 |
| 参数来源 | 硬编码/配置文件 | 上游 XCom |
| 扩缩容成本 | 修改代码 + 部署 | 零成本 |
| 适用场景 | 门店数固定、任务数恒定 | 门店增减频繁、上游驱动 |
| 底层实现 | 多个 `BaseOperator` | 1 个 `MappedOperator` |

### 三个编排原语的关系

| 原语 | 功能 | 底层类 | 装饰器 | 返回值影响 |
|------|------|--------|--------|----------|
| **分支（Branch）** | 多选一/多选多 | `BranchPythonOperator` | `@task.branch` | 返回 task_id 列表 → 未选中的被 skip |
| **短路（ShortCircuit）** | 条件继续/中止 | `ShortCircuitOperator` | `@task.short_circuit` | 返回 False → 全部下游被 skip |
| **映射（Mapping）** | 一任务变多实例 | `MappedOperator` | `.expand()` / `.expand_kwargs()` | 上游 list 长度决定实例数 |

### 注意事项

1. **`.expand()` 的参数必须是可迭代容器（`list`/`tuple`/`dict`），不能用字符串**。因为字符串是 `Iterable`，会被当作字符列表展开（"hello" → 5 个实例）。
2. **分支任务返回的 `task_id` 必须是直接下游任务**。如果返回了一个间接下游的 task_id，该 task 可能因为没有直接上游成功（被 skip 了）而无法触发。
3. **`trigger_rule=ALL_DONE` 不等于 `ALWAYS`**。`ALL_DONE` 等待所有上游执行完毕（包括 skipped）；`ALWAYS` 完全不关心上游状态。分支汇聚场景通常用 `NONE_FAILED_MIN_ONE_SUCCESS` 更合理。
4. **`MappedOperator` 的 `.expand()` 调用后不能再加 `.expand()`**。`OperatorPartial` 只能调用一次 `.expand()`，重复调用会报错。每个 `OperatorPartial` 也有析构检查——如果定义了 `partial` 但忘了调用 `.expand()`，运行时会警告。
5. **映射任务的 XCom 回传是一个 list**。下游接收时，参数类型是 `list[dict]` 而非单个 `dict`。如果需要逐一处理，调用 `.map()` 或在 `.expand_kwargs()` 中使用。
6. **`ignore_downstream_trigger_rules` 的默认值是 `True`**（短路模式），意味着短路后无视所有下游的 trigger_rule。如果你希望间接下游靠 trigger_rule 自救，需要显式设置 `ignore_downstream_trigger_rules=False`。

### 思考题

1. 假设有一个上游任务返回了 `[{"name": "A", "count": 100}, {"name": "B", "count": 200}]`，如果你用 `.expand_kwargs()` 对它映射一个 `@task` 函数 `def process(name: str, count: int)`，会生成几个 task 实例？每个实例的 `name` 和 `count` 分别是什么？如果改用 `.expand(name=names_list, count=counts_list)`，会生成几个实例？（提示：笛卡尔积）

2. 在综合实战（3.6 节）中，`full_extract.expand(partition=partitions)` 生成的 10 个映射实例，如果其中一个失败了，`full_merge` 是否还能执行？为什么？如果你希望 `full_merge` 在有任何分片失败时也仍然汇总成功的数据，应该怎么配置？

---

> **本章完成**：你已经掌握了 TaskFlow 三大高级编排原语——动态映射、分支、短路。下一章将深入 Airflow 的权限与安全模型，学习如何在多团队共享平台上安全地管理 Dag 和数据源。
