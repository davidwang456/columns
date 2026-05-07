# 第5章：调度策略与定时触发——Cron、Timetable 与 Catchup

## 1 项目背景

某金融科技公司的风控团队使用 Airflow 管理反欺诈模型的日常训练流水线。流水线需要每天凌晨 3 点运行——因为上游数据在凌晨 2 点才能完全准备就绪。数据工程师小周在 Airflow 中设置了 `schedule="0 3 * * *"`，一切似乎完美运转。

直到有一天——反欺诈模型突然告警：连续两天没有更新。小周排查发现，原来因为元旦假期，上游数据源连续三天没有产出数据。恢复供数后，Airflow 并没有自动补齐这三天的训练任务。模型一直停留在 3 天前的状态，导致欺诈检测的准确率断崖式下降。

另一个场景：测试环境的 Airflow 因为磁盘满停了一周。修复后重新启动，小周惊恐地发现 Airflow 开始疯狂创建历史 DagRun——过去一周每天一个，瞬间将元数据库冲垮，调度器直接 OOM。

> 这两个事故指向同一个核心问题：**Airflow 的调度策略远不止一个 cron 表达式那么简单**。Catchup（回溯）、start_date、schedule 之间的微妙关系，决定了你的 Dag "跑几次"和"什么时候跑"。

---

## 2 项目设计

**小胖**（在 whiteboard 上画时间线）："大师，我需要一个 Dag 从 2025 年 1 月 1 日开始每天跑一次。我设置 `start_date=2025-01-01`，`schedule='@daily'`——那第一次执行是什么时候？"

**大师**："很多人以为第一次执行在 2025-01-01 00:00，但实际上是在 2025-01-02 00:00。原因是 Airflow 的调度哲学：一个调度间隔覆盖的时间段 `[start, end)` 结束后才会触发。所以 2025-01-01 的 DagRun 覆盖的是 2025-01-01 00:00 到 2025-01-02 00:00 的数据——它应该在 2025-01-02 00:00 之后才触发。"

**小白**："这有点像财务记账——月底结账必须等这个月过完了才能做。但如果不是日报而是小时级任务呢？"

**大师**："那就要用 `timedelta(hours=1)` 作为 schedule。比如 `start_date=2025-01-01 08:00`，`schedule=timedelta(hours=1)`——第一个 DagRun 覆盖 08:00~09:00，在 09:00 触发。"

**小胖**："那 Catchup 参数是干啥的？我看文档说它是控制是否回溯历史的。"

**大师**："Catchup 是 Airflow 中最容易被误解的参数。假设你的 start_date 是 30 天前，但你的 Airflow 今天才启动。如果 catchup=True，Scheduler 会创建 30 个历史 DagRun，从最早的开始依次执行。如果 catchup=False，只会调度最新那个。"

**小白**："catchup=True 听起来很合理——数据缺失了就该补嘛。但刚才小周那个事故，Airflow 停了一周后重启就疯了，是不是就是因为 catchup=True？"

**大师**："正是。catchup=True 在两种场景下是灾难：
1. **start_date 是静态日期且距今很久**：比如 start_date=2020-01-01，catchup=True——会创建上千个 DagRun
2. **Airflow 长时间停机**：停机期间积压了大量调度间隔，重启后一次性全部创建

所以在生产环境中，大多数 Dag 都会设置 `catchup=False`。如果需要补历史数据，用 Backfill 命令手动控制范围，而不是让调度器自动暴冲。"

**小胖**："那 schedule 除了 cron 和 timedelta，还有什么选择？"

**大师**："Airflow 3.x 最重要的新特性之一就是 **Asset（数据集）调度**。传统的 schedule 是纯时间驱动，Asset 是数据驱动——'当某个数据表有新分区时触发'。这是两种完全不同的调度范式。但我们先聚焦时间调度，Asset 在后面章节专门讲。"

> **技术映射**：schedule = 公交时刻表（固定时间发车），catchup = 末班车是否补开（错过了就不开了，还是补开一班），Asset 调度 = 网约车（有人下单才出发）。

---

## 3 项目实战

### 3.1 Cron 表达式速成

Airflow 的 cron 语法与 Unix cron 基本一致，但有细微差异：

```
# ┌───────────── 分钟 (0 - 59)
# │ ┌───────────── 小时 (0 - 23)
# │ │ ┌───────────── 日 (1 - 31)
# │ │ │ ┌───────────── 月 (1 - 12)
# │ │ │ │ ┌───────────── 星期 (0 - 6，0=周日)
# │ │ │ │ │
# * * * * *
```

**常用预设**（Airflow 特有）：

```python
from airflow.timetables.simple import (
    # Airflow 3.x 中预设不再作为字符串直接使用，而是通过 Timetable 对象
)
```

旧版 Airflow 2.x 的字符串预设（在 Airflow 3.x 中已改为使用 Timetable 对象）：

| 旧版预设 | 等价 Cron | 含义 |
|---------|----------|------|
| `@once` | — | 仅调度一次 |
| `@hourly` | `0 * * * *` | 每小时整点 |
| `@daily` | `0 0 * * *` | 每天零点 |
| `@weekly` | `0 0 * * 0` | 每周日零点 |
| `@monthly` | `0 0 1 * *` | 每月 1 号零点 |
| `@yearly` | `0 0 1 1 *` | 每年 1 月 1 日零点 |

在 Airflow 3.x 中推荐直接使用 `timedelta` 或 Timetable 对象：

```python
from datetime import timedelta
from airflow.sdk import DAG

# 推荐方式一：timedelta
with DAG(
    dag_id="hourly_job",
    schedule=timedelta(hours=1),
    start_date=datetime(2025, 1, 1),
) as dag:
    ...
```

### 3.2 实战：理解 Catchup 行为

**步骤目标**：通过对比实验，直观理解 catchup=True 和 catchup=False 的行为差异。

**步骤 1：创建对比 Dag 文件**

在 `dags/` 下创建 `catchup_demo.py`：

```python
"""
Catchup 行为对比实验
- catchup_true:  回溯历史所有 DagRun
- catchup_false: 只调度最新的 DagRun
"""
from datetime import datetime, timedelta
from airflow.sdk import DAG
from airflow.sdk.operators.bash import BashOperator

# ============================================================
# Dag A：catchup=True（默认）
# ============================================================
with DAG(
    dag_id="catchup_true_demo",
    description="Catchup=True — 会回溯历史 DagRun",
    schedule="*/2 * * * *",           # 每 2 分钟
    start_date=datetime(2025, 1, 1),  # 很久以前
    catchup=True,                      # 默认值
    tags=["demo", "catchup"],
) as catchup_true_dag:

    BashOperator(
        task_id="hello",
        bash_command='echo "Catchup=True! Execution date: {{ ds }} {{ ts }}"',
    )

# ============================================================
# Dag B：catchup=False
# ============================================================
with DAG(
    dag_id="catchup_false_demo",
    description="Catchup=False — 只跑最新的",
    schedule="*/2 * * * *",
    start_date=datetime(2025, 1, 1),
    catchup=False,
    tags=["demo", "catchup"],
) as catchup_false_dag:

    BashOperator(
        task_id="hello",
        bash_command='echo "Catchup=False! Execution date: {{ ds }} {{ ts }}"',
    )
```

**步骤 2：观察行为差异**

部署后等待 Dag 加载，然后观察：

- `catchup_true_demo`：会一次性创建大量历史 DagRun（从 2025-01-01 到现在所有间隔），并逐个执行。Scheduler 日志中会出现大量排队信息。
- `catchup_false_demo`：只创建最新的一个 DagRun 并执行。

**步骤 3：查看历史 DagRun**

```bash
# 查看两个 Dag 的 DagRun 数量差异
docker exec airflow-scheduler-1 airflow dags list-runs -d catchup_true_demo | wc -l
docker exec airflow-scheduler-1 airflow dags list-runs -d catchup_false_demo | wc -l
```

> **可能遇到的坑**：`catchup_true_demo` 如果 start_date 距今太远，会创建成千上万个 DagRun，可能导致数据库压力激增。在实验中建议：
> 1. 将 start_date 改成昨天，如 `datetime(2025, 1, 14)`
> 2. 实验完成后立即 Unpause 然后 Pause 该 Dag
> 3. 或直接删除该 Dag 文件

### 3.3 实战：使用 Timedelta Schedule

**步骤目标**：掌握 timedelta 调度模式，实现小时级数据同步。

在 `dags/` 下创建 `hourly_sync.py`：

```python
"""
小时级数据同步 Dag
使用 timedelta 替代 cron 表达式，更直观
"""
from datetime import datetime, timedelta
from airflow.sdk import DAG
from airflow.sdk.operators.bash import BashOperator
from airflow.sdk.operators.python import PythonOperator

with DAG(
    dag_id="hourly_data_sync",
    description="每小时从 API 同步数据",
    schedule=timedelta(hours=1),           # 每 1 小时
    start_date=datetime(2025, 1, 15, 0, 0), # 从今天 0 点开始
    catchup=False,
    tags=["production", "sync"],
) as dag:

    sync_task = BashOperator(
        task_id="sync_hourly_data",
        bash_command="""
            echo "同步 {{ data_interval_start }} 到 {{ data_interval_end }} 的数据"
            echo "执行时间: {{ ts }}"
            echo "逻辑日期: {{ ds }}"
        """,
    )

    def _validate_sync(**context):
        """验证同步的数据量是否合理"""
        data_interval_start = context["data_interval_start"]
        data_interval_end = context["data_interval_end"]
        print(f"验证时间段: {data_interval_start} → {data_interval_end}")
        return {"status": "ok"}

    validate = PythonOperator(
        task_id="validate_sync",
        python_callable=_validate_sync,
    )

    sync_task >> validate
```

### 3.4 实战：手动触发带参数的 DagRun

**步骤目标**：理解 Trigger Dag with config 的使用场景。

```bash
# 通过 REST API 触发，带自定义参数
curl -X POST "http://localhost:8080/api/v2/dags/hourly_data_sync/dagRuns" \
  -H "Content-Type: application/json" \
  --user "airflow:airflow" \
  -d '{
    "execution_date": "2025-01-15T08:00:00Z",
    "conf": {
      "force_sync": true,
      "source_table": "orders_archive"
    }
  }'
```

在 Web UI 中触发时，在 Trigger 对话框中输入 JSON 配置即可。

### 3.5 调度间隔关键概念图解

```
start_date=2025-01-01 00:00, schedule=@daily

时间线：
                      run_1              run_2              run_3
                 [01-01 ~ 01-02]    [01-02 ~ 01-03]    [01-03 ~ 01-04]
                       ↓                  ↓                  ↓
触发时间：        01-02 00:00        01-03 00:00        01-04 00:00
execution_date：  01-01 00:00        01-02 00:00        01-03 00:00

关键理解：
- execution_date（逻辑日期）≠ 实际执行时间
- Schedule 定义了间隔的结束时刻触发
- {{ ds }} 模板变量 = execution_date 的日期部分
- {{ data_interval_start }} = 区间的开始
- {{ data_interval_end }} = 区间的结束
```

---

## 4 项目总结

### Schedule 参数速查

| 参数 | 作用 | 常用值 |
|------|------|--------|
| schedule | 调度间隔定义 | `"0 2 * * *"`, `timedelta(hours=1)`, `None` |
| start_date | 调度开始日期 | `datetime(2025, 1, 1)` |
| end_date | 调度结束日期（可选） | `datetime(2025, 12, 31)` |
| catchup | 是否补跑历史 | `True`（默认） / `False` |

### Cron vs Timedelta 选型

| 特性 | Cron | Timedelta |
|------|------|----------|
| 精确度 | 分钟级 | 秒级 |
| 可读性 | 需要记忆语法 | 直观 |
| 时区支持 | 天然支持 | 天然支持 |
| 复杂规则 | 支持（如每月最后一个周五） | 不支持 |
| 适用场景 | 日报/周报/月报 | 每小时/N 分钟/N 秒 |

### Catchup 决策树

```
你需要补跑历史数据吗？
├── 是 → 使用 catchup=True，并确保 start_date 不太远
│       或者用 backfill 命令手动控制
└── 否 → catchup=False + 最近日期作为 start_date
```

### 常见踩坑经验

1. **"为什么 Dag 不自动触发？"**：最常见的原因是 start_date 在未来。Airflow 不会为未来的时间创建 DagRun。确保 start_date 已经过去，或者等到了那个时间。

2. **"catchup=False 但还是在创建历史 DagRun"**：旧版 Airflow 2.x 中 catchup 参数默认行为不一致。检查 `airflow.cfg` 中 `catchup_by_default` 的设置。

3. **"schedule 为 None 的 Dag 能手动触发吗？"**：可以。`schedule=None` 意味着这个 Dag 只有手动触发或通过 ExternalTaskSensor/Asset 触发。这是纯事件驱动 Dag 的经典设置。

### 思考题

1. 如果你的 Dag 每天凌晨 2 点运行，处理"上个小时"的数据，`{{ ds }}` 会是什么值？`{{ data_interval_start }}` 和 `{{ data_interval_end }}` 呢？
2. 某 Airflow 集群因维护停机了 3 天。重启后，你希望自动补跑这 3 天的 Dag，但又不想设置 catchup=True（因为你的 start_date 是 2020 年，catchup=True 会炸）。有什么办法？

*（答案将在后续章节揭晓）*

---

> **本章完成**：你对 Airflow 的调度哲学已经有了深刻理解。下一章我们将深入学习三种最常用的 Operator——PythonOperator、BashOperator 和 EmptyOperator。
