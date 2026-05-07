# 第11章：Dag 参数与动态 Dag 生成

## 1 项目背景

数据平台团队负责为公司的 8 个业务线提供数据同步服务。每个业务线的需求大同小异：从 MySQL 同步数据到 Hive，只是表名、分区字段和调度频率不同。按照传统做法，每个业务线都需要一个独立的 Dag 文件——业务线 A 的 Dag 叫 `sync_mysql_to_hive_line_a.py`，业务线 B 的叫 `sync_mysql_to_hive_line_b.py`……

8 个 Dag 文件，每个 200 行，总计 1600 行代码。维护噩梦随之而来：当同步逻辑需要修改（比如增加数据质量检查），工程师需要在 8 个文件中做几乎相同的修改。漏改是常态，版本不一致是必然。

与此同时，另一个团队的数据分析师希望在手动触发 Dag 时能够指定不同的参数——比如"只同步最近 7 天的数据"或"只同步某个分区"。现有的 Dag 没有任何参数化能力，每次都得临时修改代码。

> Airflow 提供了两种优雅的解决方案：**Dag Params**（运行时参数化）和**动态 Dag 生成**（编译时参数化）。前者让同一个 Dag 在不同触发时表现出不同行为，后者让你用循环/模板生成结构相似但不完全相同的多个 Dag。

---

## 2 项目设计

**小胖**（看着 8 个几乎一样的 Dag 文件）："这不就是复制粘贴改个表名？能不能用一个循环生成 8 个 Dag？"

**大师**："完全可以——这就是动态 Dag 生成。你可以写一个 Python 循环，每次迭代创建一个 Dag。但有三点要特别注意：第一，Dag 是在 Dag Processor 解析文件时生成的，不是运行时生成的；第二，dag_id 必须全局唯一，你需要在循环中动态命名；第三，避免生成过多 Dag 导致解析变慢——8 个没问题，800 个就需要考虑性能了。"

**小白**："那 Params 呢？我看文档说可以手动触发时传 JSON 配置。"

**大师**："Params 是 Airflow 的一个强大特性——你在 Dag 定义中声明'这个 Dag 接受哪些参数'，用户在手动触发时填写，然后在 Task 中读取。这比环境变量更灵活——环境变量对所有 DagRun 都一样，Params 每个 DagRun 可以不同。但注意：Params 只在手动触发时才生效，定时触发的 DagRun 使用默认值。"

**小胖**："那 Params 和 Variable 有什么区别？感觉都能存配置。"

**大师**："Variable 是全局的、跨 DagRun 共享的配置——改了立刻影响所有后续执行。Params 是 DagRun 级别的——不同的 DagRun 可以有完全不同的 Params。用快递类比：Variable 是快递公司的运营规则（改一条全公司都变），Params 是每个包裹的特殊要求（今天这个要加急，明天那个要保价）。"

**小白**："除了手动触发时传 Params，还有什么方式？"

**大师**："你还可以通过 REST API 传、通过 ExternalTaskSensor 的 `execution_date_fn` 指定、甚至通过 Asset 触发时携带。在 Airflow 3.x 中，Params 的类型系统也更丰富了——支持 str、int、float、bool、dict、list、enum——还支持自定义校验。"

> **技术映射**：动态 Dag = 快餐菜单模板（不同菜品 = 不同 Dag，配方相同但用料不同），Params = 点餐备注（同一道菜，不同客户可以加辣/免葱）。

---

## 3 项目实战

### 3.1 Dag Params 基础

**步骤目标**：定义一个接受参数的 Dag，通过手动触发传参。

```python
from airflow.sdk import DAG
from airflow.sdk.operators.python import PythonOperator
from airflow.models.param import Param
from datetime import datetime

with DAG(
    dag_id="params_demo",
    schedule=None,
    start_date=datetime(2025, 1, 1),
    params={
        "source_table": Param(
            default="orders",
            type="string",
            description="要同步的源表名",
        ),
        "sync_days": Param(
            default=7,
            type="integer",
            minimum=1,
            maximum=90,
            description="同步最近 N 天的数据",
        ),
        "mode": Param(
            default="incremental",
            type="string",
            enum=["full", "incremental"],
            description="同步模式：全量 or 增量",
        ),
        "notify": Param(
            default=True,
            type="boolean",
            description="完成后是否发送通知",
        ),
        "target_partitions": Param(
            default=["2025-01-01"],
            type="array",
            description="目标分区列表",
        ),
    },
    tags=["demo", "params"],
) as dag:

    def _sync_data(**context):
        params = context["params"]
        print(f"源表: {params['source_table']}")
        print(f"同步天数: {params['sync_days']}")
        print(f"模式: {params['mode']}")
        print(f"通知: {params['notify']}")
        print(f"目标分区: {params['target_partitions']}")

        # 根据参数执行不同逻辑
        if params["mode"] == "full":
            print("执行全量同步...")
        else:
            print(f"增量同步最近 {params['sync_days']} 天...")

    sync = PythonOperator(task_id="sync_data", python_callable=_sync_data)
```

**手动触发时传参**：

在 Web UI 中点击 Trigger Dag，在 "Trigger configuration" 输入框中填入 JSON：

```json
{
  "source_table": "user_events",
  "sync_days": 3,
  "mode": "incremental",
  "target_partitions": ["2025-01-14", "2025-01-15"]
}
```

### 3.2 动态 Dag 生成

**步骤目标**：编写一个"多数据源同步"Dag 工厂函数。

```python
"""
动态 Dag 生成示例
一个 Python 文件生成多个结构相同但配置不同的 Dag
"""
from airflow.sdk import DAG
from airflow.sdk.operators.python import PythonOperator
from datetime import datetime, timedelta

# ============================================================
# 配置驱动：定义要生成的所有数据源
# ============================================================
SOURCE_CONFIGS = [
    {"name": "orders",   "table": "ods_orders",   "schedule": "0 2 * * *", "retries": 2},
    {"name": "users",    "table": "ods_users",    "schedule": "0 3 * * *", "retries": 1},
    {"name": "products", "table": "ods_products", "schedule": "0 4 * * *", "retries": 2},
    {"name": "payments", "table": "ods_payments", "schedule": "0 5 * * *", "retries": 3},
    {"name": "refunds",  "table": "ods_refunds",  "schedule": "0 6 * * *", "retries": 1},
]

# ============================================================
# Dag 工厂函数
# ============================================================
def create_sync_dag(source_config: dict) -> DAG:
    """根据配置生成一个同步 Dag"""

    dag_id = f"sync_{source_config['name']}_to_hive"

    default_args = {
        "owner": "data-sync-team",
        "retries": source_config["retries"],
        "retry_delay": timedelta(minutes=5),
    }

    dag = DAG(
        dag_id=dag_id,
        default_args=default_args,
        schedule=source_config["schedule"],
        start_date=datetime(2025, 1, 1),
        catchup=False,
        tags=["sync", "auto-generated"],
    )

    # 在 dag 上下文中定义 tasks
    with dag:

        def _sync_table(source_table: str, **context):
            print(f"同步表: {source_table}")
            print(f"日期: {context['ds']}")
            # 实际同步逻辑...
            return f"{source_table} 同步完成"

        PythonOperator(
            task_id=f"sync_{source_config['name']}",
            python_callable=_sync_table,
            op_kwargs={"source_table": source_config["table"]},
        )

    return dag


# ============================================================
# 循环生成所有 Dag
# ============================================================
for config in SOURCE_CONFIGS:
    globals()[f"dag_{config['name']}"] = create_sync_dag(config)
```

**关键要点**：
- 每个 Dag 必须有不同的 `dag_id`，否则会互相覆盖
- 使用 `globals()` 将生成的 Dag 放入模块命名空间，确保 Airflow 能够发现它们
- 配置数据可以来自 YAML/JSON 文件、数据库、甚至远程 API

### 3.3 高级动态 Dag：基于 YAML 配置

```yaml
# dags/configs/sync_configs.yaml
syncs:
  - name: orders
    source: mysql_warehouse
    target: hive_prod
    table: ods_orders
    primary_key: order_id
    partition_key: dt
    schedule: "0 2 * * *"
  - name: users
    source: mysql_warehouse
    target: hive_prod
    table: ods_users
    primary_key: user_id
    partition_key: dt
    schedule: "0 3 * * *"
```

```python
# dags/sync_factory.py
import yaml
import os
from airflow.sdk import DAG
from airflow.sdk.operators.python import PythonOperator
from datetime import datetime, timedelta

# 加载配置
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "configs", "sync_configs.yaml")
with open(CONFIG_PATH) as f:
    configs = yaml.safe_load(f)

def build_sync_dag(conf):
    dag_id = f"sync_{conf['name']}"

    with DAG(
        dag_id=dag_id,
        schedule=conf["schedule"],
        start_date=datetime(2025, 1, 1),
        catchup=False,
        default_args={"owner": "sync-factory", "retries": 2},
    ) as dag:

        def _run_sync(src, tgt, tbl, pk, pt, **ctx):
            print(f"从 {src}.{tbl} 同步到 {tgt}.{tbl}")
            print(f"分区: {ctx['ds']}")

        PythonOperator(
            task_id="sync",
            python_callable=_run_sync,
            op_kwargs={
                "src": conf["source"],
                "tgt": conf["target"],
                "tbl": conf["table"],
                "pk": conf["primary_key"],
                "pt": conf["partition_key"],
            },
        )

    return dag

for c in configs["syncs"]:
    globals()[f"dag_{c['name']}"] = build_sync_dag(c)
```

### 3.4 Jinja 模板变量速查表

在 `BashOperator` 和 `PythonOperator` 的 `templates_dict` 中可使用的模板变量：

| 变量 | 含义 | 示例值 |
|------|------|--------|
| `{{ ds }}` | 执行日期（YYYY-MM-DD） | `2025-01-15` |
| `{{ ds_nodash }}` | 无分隔符日期 | `20250115` |
| `{{ ts }}` | 时间戳（ISO 8601） | `2025-01-15T00:00:00+00:00` |
| `{{ ts_nodash }}` | 无分隔符时间戳 | `20250115T000000` |
| `{{ data_interval_start }}` | 数据区间起点 | `2025-01-14T00:00:00+00:00` |
| `{{ data_interval_end }}` | 数据区间终点 | `2025-01-15T00:00:00+00:00` |
| `{{ dag_run.conf }}` | DagRun 配置（Params） | `{"table":"orders"}` |
| `{{ params.xxx }}` | Dag 参数 | 见 Params 定义 |

---

## 4 项目总结

### 配置管理方式对比

| 方式 | 粒度 | 热更新 | 适用场景 |
|------|------|--------|---------|
| 硬编码 | 代码级 | 需部署 | 不变的值 |
| Variable | 全局 | 实时 | 全局开关、阈值 |
| Params | DagRun 级 | 触发时指定 | 单次不同的参数 |
| 动态 Dag 生成 | Dag 级 | 需重新解析 | 结构相同参数不同的 Dag 族 |

### 注意事项

1. **动态 Dag 的 globals() 技巧**：`globals()[name] = dag` 是必需的——Airflow 通过扫描模块的全局变量来发现 Dag 对象。
2. **dag_id 的命名规范**：建议包含业务标识 + 源/目标，如 `sync_orders_mysql_to_hive`。
3. **Params 不支持定时调度覆盖**：定时触发的 DagRun 只使用 Params 的默认值。如需定时动态参数，使用动态 Dag 生成或 Variable。
4. **不要过度动态化**：如果 100 个 Dag 只有细微差异，动态生成合适；如果每个 Dag 逻辑完全不同，独立文件更清晰。

### 思考题

1. 你的团队需要为 50 个客户分别生成独立的 Dag，每个 Dag 的配置存储在数据库的 `client_configs` 表中。每次 Airflow 重启时，Dag Processor 需要查询这张表来生成所有 Dag。这有什么潜在的性能风险？如何优化？
2. Params 支持 `enum` 类型，如果用户通过 REST API 传了一个不在 enum 列表中的值，会发生什么？

*（答案将在后续章节揭晓）*

---

> **本章完成**：你已掌握 Dag 参数化和动态生成两大灵活性利器。下一章将学习 Airflow 3.x 的重大特性——基于数据资产的调度。
