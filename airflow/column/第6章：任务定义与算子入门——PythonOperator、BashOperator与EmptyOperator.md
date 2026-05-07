# 第6章：任务定义与算子入门——PythonOperator、BashOperator 与 EmptyOperator

## 1 项目背景

数据工程师小李接手了一个遗留项目：一个包含 200 多行 Shell 脚本的"数据处理流水线"。这个脚本从 MySQL 导出数据 → 调用 Python 做数据清洗 → 再调用另一个 Shell 脚本加载到 Hive。整个脚本通过 `&&` 串联，出错时难以定位、难以重试、难以并行化。

小李想用 Airflow 重构，但他面对 Airflow 众多的 Operator 类型感到困惑：BashOperator、PythonOperator、EmptyOperator、BranchOperator、DockerOperator、KubernetesPodOperator……该用哪个？怎么选？

更关键的是：Operator 和 Task 是什么关系？一个 Operator 等于一个 Task 吗？BaseOperator 上那些参数（retries、execution_timeout、trigger_rule）分别控制什么？小李需要一种系统化的方式来理解 Airflow 的 Operator 体系。

> 本章聚焦三种最基础也最常用的 Operator——它们能覆盖 80% 的日常任务场景。掌握了它们，你就掌握了 Airflow 工作流的"原子单元"。

---

## 2 项目设计

**小胖**（打开 Airflow 文档的 Operator 目录）："天哪，Operator 有一百多种？我该从哪儿开始？"

**大师**："别被数量吓到。Airflow 的所有 Operator 都继承自 BaseOperator，它们共享一套通用参数和生命周期。你的目标是理解 BaseOperator 的设计哲学，然后针对不同场景选择对应的子类。先用好三种最基础的：PythonOperator、BashOperator、EmptyOperator。"

**小白**："Operator 和 Task 的区别是什么？我经常看到这两个词混用。"

**大师**："精确地说：Operator 是模板，Task 是实例。类比：Operator 是建筑图纸，Task 是按图纸盖出来的具体建筑。你在 Dag 文件中写 `BashOperator(task_id='hello')`——这创建了一个 Task。同一个 Operator 类可以在同一个 Dag 中被实例化多次，每个有不同的 task_id。"

**小胖**："那 EmptyOperator 是干嘛的？听起来就是什么都不做？"

**大师**："EmptyOperator 是最被低估的 Operator。它在三种场景下极其有用：一是作为 Dag 的'起点'和'终点'，让 Graph 视图更美观；二是作为分支汇聚点——多条平行路径在这里合并；三是作为工作流骨架——你先用 EmptyOperator 搭好结构，然后用真实 Operator 逐个替换。这不是'什么都不做'，这是'暂时不做，为了结构而存在'。"

**小白**："PythonOperator 和 BashOperator 在功能上有重叠吧——Python 代码也能调用 Shell 命令。"

**大师**："没错，但选择的标准是'边际成本'。如果你已经有现成的 Shell 脚本，直接用 BashOperator 包装，改动最小。如果是新的逻辑，用 PythonOperator——它有更好的错误处理、类型检查、测试支持。BashOperator 的另一个优势是 Jinja 模板直出——`{{ ds }}` 可以直接写在 bash_command 里，而 PythonOperator 需要通过 `context['ds']` 获取。"

> **技术映射**：Operator = 乐高积木件（不同形状但接口统一），Task = 拼好的乐高模型，BaseOperator 通用参数 = 每块积木上都有的卡扣设计。

---

## 3 项目实战

### 3.1 PythonOperator 深入实战

**PythonOperator 核心参数**：

```python
PythonOperator(
    task_id="my_python_task",
    python_callable=my_function,    # 要执行的函数
    op_args=[arg1, arg2],           # 位置参数
    op_kwargs={"key": "value"},     # 关键字参数
    op_kwarg_extras={"log": True},  # 额外关键字
    templates_dict={                # 可渲染模板的字典
        "path": "/data/{{ ds }}/",
    },
)
```

**实战：构建可配置的数据处理任务**

在 `dags/` 下创建 `operator_demo.py`：

```python
"""
Operator 深度实战：PythonOperator + BashOperator + EmptyOperator
"""
from datetime import datetime, timedelta
from airflow.sdk import DAG
from airflow.sdk.operators.python import PythonOperator
from airflow.sdk.operators.bash import BashOperator
from airflow.sdk.operators.empty import EmptyOperator

default_args = {
    "owner": "data-team",
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
    "execution_timeout": timedelta(minutes=30),
}

with DAG(
    dag_id="operator_deep_dive",
    default_args=default_args,
    schedule=None,
    start_date=datetime(2025, 1, 1),
    catchup=False,
    tags=["tutorial", "operators"],
) as dag:

    # ============================================================
    # EmptyOperator: 工作流骨架
    # ============================================================
    start = EmptyOperator(task_id="start")
    end = EmptyOperator(task_id="end")

    # ============================================================
    # BashOperator: 数据导出
    # ============================================================
    export_data = BashOperator(
        task_id="export_from_mysql",
        bash_command="""
            echo "==== 数据导出开始 ===="
            echo "日期: {{ ds }}"
            echo "时间戳: {{ ts }}"
            echo "数据间隔: {{ data_interval_start }} → {{ data_interval_end }}"

            # 模拟 mysqldump 导出
            OUTPUT_DIR="/tmp/airflow-data/{{ ds }}"
            mkdir -p "$OUTPUT_DIR"

            echo "导出 orders 表..."
            sleep 3
            echo "导出完成: $OUTPUT_DIR/orders.csv (10234 rows)"
            echo "10234" > "$OUTPUT_DIR/row_count.txt"
        """,
        # 设置环境变量
        env={
            "MYSQL_HOST": "{{ var.value.get('mysql_host', 'localhost') }}",
            "EXPORT_ENCODING": "utf-8",
        },
    )

    # ============================================================
    # PythonOperator: 数据清洗（带参数和异常处理）
    # ============================================================
    def _clean_data(source_table: str, threshold: float, **context):
        """
        清洗数据
        Args:
            source_table: 源表名（通过 op_kwargs 传入）
            threshold: 数据阈值（通过 op_kwargs 传入）
            **context: Airflow 上下文（自动注入）
        """
        import json
        import os

        ds = context["ds"]
        data_dir = f"/tmp/airflow-data/{ds}"

        # 读取行数
        with open(f"{data_dir}/row_count.txt") as f:
            total_rows = int(f.read().strip())

        print(f"清洗源表: {source_table}")
        print(f"总行数: {total_rows}")

        # 模拟数据清洗逻辑
        clean_rows = int(total_rows * 0.92)
        dirty_rows = total_rows - clean_rows

        # 数据质量检查
        if dirty_rows / total_rows > threshold:
            raise ValueError(
                f"数据脏率 {dirty_rows/total_rows:.2%} 超过阈值 {threshold:.2%}!"
            )

        result = {
            "source": source_table,
            "date": ds,
            "total": total_rows,
            "clean": clean_rows,
            "dirty": dirty_rows,
        }

        # 保存清洗结果
        with open(f"{data_dir}/clean_result.json", "w") as f:
            json.dump(result, f, ensure_ascii=False)

        print(f"清洗完成: {json.dumps(result, ensure_ascii=False)}")
        return result

    clean_data = PythonOperator(
        task_id="clean_orders",
        python_callable=_clean_data,
        op_kwargs={
            "source_table": "orders",
            "threshold": 0.15,
        },
    )

    # ============================================================
    # PythonOperator：使用 templates_dict 实现动态路径
    # ============================================================
    def _load_to_warehouse(templates_dict, **context):
        """使用 templates_dict 获取渲染后的路径"""
        target_path = templates_dict["target_path"]
        clean_result = context["task_instance"].xcom_pull(task_ids="clean_orders")

        print(f"目标路径: {target_path}")
        print(f"加载 {clean_result['clean']} 条清洗后数据")
        print("数据加载完成！")

    load_data = PythonOperator(
        task_id="load_to_dw",
        python_callable=_load_to_warehouse,
        templates_dict={
            "target_path": "/warehouse/{{ ds }}/clean_orders/",
        },
    )

    # ============================================================
    # 依赖链定义：start → export → clean → load → end
    # ============================================================
    start >> export_data >> clean_data >> load_data >> end
```

### 3.2 BaseOperator 通用参数速查表

以下是所有 Operator 共享的核心参数：

| 参数 | 类型 | 作用 | 常用值 |
|------|------|------|--------|
| task_id | str | 任务唯一标识 | `"extract_data"` |
| retries | int | 失败后重试次数 | `3` |
| retry_delay | timedelta | 重试间隔 | `timedelta(minutes=5)` |
| retry_exponential_backoff | bool | 指数退避重试 | `True` |
| max_retry_delay | timedelta | 最大重试间隔 | `timedelta(hours=1)` |
| execution_timeout | timedelta | 单次执行超时 | `timedelta(hours=2)` |
| trigger_rule | str | 触发条件 | `"all_success"`（默认） |
| depends_on_past | bool | 是否依赖上一次运行成功 | `False` |
| wait_for_downstream | bool | 是否等待下游完成 | `False` |
| priority_weight | int | 队列优先级权重 | `1` |
| weight_rule | str | 权重计算规则 | `"downstream"` |
| queue | str | 指定 Executor 队列 | `"gpu"` |
| pool | str | 指定资源池 | `"default_pool"` |
| pool_slots | int | 占用资源槽位数 | `1` |

### 3.3 触发规则（TriggerRule）详解

TriggerRule 控制"Task 在什么条件下应该被执行"：

```python
from airflow.sdk import TriggerRule

# 场景一：无论上游成功或失败都要执行
cleanup = BashOperator(
    task_id="cleanup",
    bash_command='echo "无论如何都清理临时文件"',
    trigger_rule=TriggerRule.ALL_DONE,  # 所有上游完成后执行（不管成败）
)

# 场景二：任一上游成功即执行
notify = PythonOperator(
    task_id="notify_partial",
    python_callable=_notify,
    trigger_rule=TriggerRule.ONE_SUCCESS,
)

# 场景三：上游全部失败才执行（错误处理分支）
error_handler = BashOperator(
    task_id="error_escalation",
    bash_command='echo "所有数据源都失败了！升级告警！"',
    trigger_rule=TriggerRule.ALL_FAILED,
)
```

TriggerRule 枚举值：

| 值 | 触发条件 |
|---|---------|
| ALL_SUCCESS（默认） | 所有上游成功 |
| ALL_FAILED | 所有上游失败 |
| ALL_DONE | 所有上游完成（无论成败） |
| ONE_SUCCESS | 至少一个上游成功 |
| ONE_FAILED | 至少一个上游失败 |
| NONE_FAILED | 无上游失败（成功或跳过） |
| NONE_FAILED_MIN_ONE_SUCCESS | 至少一个成功，无失败 |
| ALWAYS | 无条件执行 |

### 运行与验证

```bash
# 渲染 Task 的模板参数
docker exec airflow-scheduler-1 airflow tasks render operator_deep_dive export_from_mysql 2025-01-15

# 测试单个 Task
docker exec airflow-scheduler-1 airflow tasks test operator_deep_dive export_from_mysql 2025-01-15

# 查看 XCom
docker exec airflow-scheduler-1 airflow tasks test operator_deep_dive clean_orders 2025-01-15
```

---

## 4 项目总结

### Operator 选型决策表

| 场景 | 推荐 Operator | 理由 |
|------|--------------|------|
| 已有 Shell 脚本 | BashOperator | 改动最小 |
| 复杂 Python 逻辑 | PythonOperator | 类型安全、可测试 |
| 调用外部 API | PythonOperator + requests | 灵活控制 |
| Docker 容器化任务 | DockerOperator | 环境隔离 |
| 占位/骨架/汇聚 | EmptyOperator | 零开销 |
| 条件分支 | BranchPythonOperator | 动态路由 |

### 注意事项

1. **PythonOperator 的 `python_callable` 不要在顶层定义闭包**：Dag Processor 每次解析都会重新创建函数对象，应该定义在模块级别。
2. **BashOperator 的 `env` 参数不会继承系统环境变量**：如果脚本依赖 `$PATH` 或 `$HOME`，需要显式传入。
3. **EmptyOperator 也会占用调度资源**：虽然它什么都不做，但 Scheduler 仍然需要判断它的 TriggerRule 是否满足。

### 思考题

1. 如果你需要在一个 Task 内执行 3 个独立的子任务（互不依赖），你会选择在一个 PythonOperator 中串行执行，还是定义 3 个独立的 Task？各自的优缺点是什么？
2. `retries=3` 和 `trigger_rule=ALL_FAILED` 结合使用，如果 Task 重试 3 次后仍然失败，下游的 ALL_FAILED 触发器会触发吗？

*（答案将在后续章节揭晓）*

---

> **本章完成**：你已掌握三种核心 Operator 的使用和选型逻辑。下一章我们将学习 Airflow 的配置管理三剑客——Variable、Connection 和环境变量。
