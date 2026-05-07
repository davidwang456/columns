# 第10章：TaskFlow API 与装饰器——告别样板代码

## 1 项目背景

数据工程师小王维护着一个包含 30 多个任务的 Dag，使用的是传统 Operator 风格。代码长达 800 多行，充斥着大量的样板代码：

```python
extract_task = PythonOperator(task_id="extract", python_callable=_extract)
transform_task = PythonOperator(task_id="transform", python_callable=_transform)
extract_task >> transform_task
```

更痛苦的是 XCom 的传递：上游任务 return 了一个 dict，下游任务通过 `ti.xcom_pull()` 获取，但要手动处理 key 名、task_id 字符串、类型转换。有一次，上游修改了返回值的字段名（`total_rows` → `row_count`），但下游的 xcom_pull 还在引用旧字段名——代码没有报错，但在运行时因为 key 不存在而静默失败。

小王听说 Airflow 3.x 大力推广 **TaskFlow API**，用 `@task` 装饰器替代显式的 Operator 实例化，XCom 自动传递，依赖由函数调用关系自然推导。他心里犯嘀咕："这跟传统写法差别太大了，值得迁移吗？"

答案是：**绝对值得**。TaskFlow 不仅仅是语法糖，它代表了一种"函数即任务"的声明式编程范式，能减少 40%-60% 的代码量，同时消除大量 XCom 传递的样板代码和拼写错误。

---

## 2 项目设计

**小胖**（看到 TaskFlow 的代码后）："这……这看起来就跟普通 Python 函数调用一样？但 Airflow 怎么知道这些是 Task 而不是普通函数？"

**大师**："关键就是 `@task` 装饰器。当 PythonOperator 被 `@task` 装饰后，它的返回值不再是一个普通值，而是一个 `XComArg` 对象——一个'占位符'。你把它传给下一个 `@task` 函数时，Airflow 记录的是一个依赖关系，而不是立即执行。这就是'声明式'的精髓：你描述数据如何流动，Airflow 负责在想执行的时候执行。"

**小白**："那 `@dag` 装饰器呢？"

**大师**：`@dag` 替代了 `with DAG(...)` 上下文管理器。用 `@dag` 装饰一个函数，函数的返回值就是 Dag 对象。这种方式更 Pythonic，也更容易做参数化和单元测试——因为 Dag 是一个函数，你可以给函数传不同参数来生成不同的 Dag。"

**小胖**："那传统 Operator 和 TaskFlow 能混用吗？"

**大师**："当然可以。`@task` 底层的实现就是 PythonOperator，它们可以无缝混用。你可以在 TaskFlow 风格的 Dag 中直接使用 BashOperator，也可以用 `>>` 操作符把传统 Operator 和 `@task` 的输出连接起来。这让你可以渐进式迁移，而不是一刀切。"

**小白**："有没有什么场景不适合用 TaskFlow？"

**大师**："当你的任务不是纯 Python 函数时——比如 BashOperator、DockerOperator、KubernetesPodOperator。这些 Operator 的任务逻辑是 Shell 命令或容器镜像，不是 Python 代码，`@task` 装饰器帮不上忙。另外，如果你需要在同一个 Python 函数内定义多个任务的复杂分支逻辑，传统 Operator 的显式依赖声明可能更清晰。"

> **技术映射**：传统 Operator = 手动档汽车（每一步都要自己操作），TaskFlow = 自动档汽车（你只管方向，系统负责换挡），两者可以在同一条路上交替使用。

---

## 3 项目实战

### 3.1 `@task` 基础用法

```python
from airflow.sdk import DAG
from airflow.sdk.operators.python import task
from datetime import datetime

with DAG("taskflow_basic", schedule=None, start_date=datetime(2025, 1, 1)) as dag:

    @task
    def extract():
        """数据抽取"""
        data = {"users": 1000, "orders": 5000}
        print(f"抽取数据: {data}")
        return data  # 自动写入 XCom

    @task
    def transform(input_data: dict):
        """数据转换"""
        result = {
            "users_cleaned": input_data["users"] * 0.95,
            "orders_cleaned": input_data["orders"] * 0.98,
        }
        print(f"转换结果: {result}")
        return result

    @task
    def load(clean_data: dict):
        """数据加载"""
        print(f"加载 {clean_data['users_cleaned']} 用户, "
              f"{clean_data['orders_cleaned']} 订单")
        return "success"

    # 数据流：extract → transform → load
    raw = extract()
    cleaned = transform(raw)
    load(cleaned)
```

对比传统写法的代码量：

| 指标 | 传统 Operator | TaskFlow |
|------|-------------|----------|
| 代码行数 | ~45 行 | ~35 行 |
| XCom 操作 | 需手动 push/pull | 自动 |
| 依赖声明 | 显式 `>>` | 函数调用链 |
| 参数传递 | `op_kwargs` + `xcom_pull` | 函数参数自动注入 |

### 3.2 `@dag` 装饰器

```python
from airflow.sdk import dag, task
from datetime import datetime, timedelta

@dag(
    dag_id="dag_decorator_demo",
    schedule="@daily",
    start_date=datetime(2025, 1, 1),
    catchup=False,
    default_args={
        "owner": "data-team",
        "retries": 1,
    },
    tags=["taskflow"],
)
def create_etl_dag():
    """用函数定义 Dag——更像普通 Python 代码"""

    @task
    def validate_input(**context):
        print(f"验证 {context['ds']} 的输入数据")
        return context["ds"]

    @task
    def run_etl(date_str: str):
        print(f"在 {date_str} 执行 ETL")
        return True

    date = validate_input()
    run_etl(date)

# 注意：必须调用函数来生成 Dag！
create_etl_dag()
```

### 3.3 TaskFlow 高级模式

**并行任务**：

```python
@dag(schedule=None, start_date=datetime(2025, 1, 1))
def parallel_demo():
    @task
    def fetch_users():
        return 5000

    @task
    def fetch_orders():
        return 10000

    @task
    def fetch_products():
        return 2000

    @task
    def merge(u: int, o: int, p: int):
        return {"users": u, "orders": o, "products": p}

    # 三个任务并行（无依赖关系）
    users = fetch_users()
    orders = fetch_orders()
    products = fetch_products()

    # 汇聚：merge 依赖所有三个上游
    merge(users, orders, products)

parallel_demo()
```

**带重试和超时配置的 TaskFlow**：

```python
@task(
    retries=3,
    retry_delay=timedelta(minutes=5),
    retry_exponential_backoff=True,
    max_retry_delay=timedelta(hours=1),
    execution_timeout=timedelta(minutes=30),
)
def unreliable_api_call():
    import random
    if random.random() < 0.7:
        raise ValueError("API 暂时不可用")
    return "success"
```

### 3.4 混合风格：TaskFlow + 传统 Operator

```python
from airflow.sdk.operators.bash import BashOperator
from airflow.sdk.operators.empty import EmptyOperator

@dag(schedule=None, start_date=datetime(2025, 1, 1))
def mixed_style_demo():

    start = EmptyOperator(task_id="start")

    @task
    def generate_config():
        return {"db": "warehouse", "table": "orders"}

    # 传统 Operator 通过 Jinja 模板读取 TaskFlow 的 XCom
    dump_data = BashOperator(
        task_id="dump_data",
        bash_command="""
            echo "从数据库导出..."
            echo "配置: {{ ti.xcom_pull(task_ids='generate_config') }}"
        """,
    )

    @task
    def validate():
        print("验证导出结果")
        return True

    end = EmptyOperator(task_id="end")

    # 混合依赖链
    start >> generate_config() >> dump_data >> validate() >> end

mixed_style_demo()
```

### 3.5 条件分支 TaskFlow

```python
@dag(schedule=None, start_date=datetime(2025, 1, 1))
def branching_demo():

    @task
    def check_data():
        import random
        has_data = random.choice([True, False])
        print(f"Has data: {has_data}")
        return has_data

    @task.branch  # 分支任务
    def decide_path(has_data: bool):
        """返回下一个要执行的 task_id"""
        if has_data:
            return "process_data"
        else:
            return "skip_processing"

    @task
    def process_data():
        print("处理数据...")

    @task
    def skip_processing():
        print("没有数据，跳过处理")

    @task(trigger_rule="none_failed")
    def finalize():
        print("无论如何都执行收尾")

    has_data = check_data()
    path = decide_path(has_data)

    process = process_data()
    skip = skip_processing()
    final = finalize()

    # 分支两条路径汇聚到 finalize
    path >> [process, skip] >> final

branching_demo()
```

---

## 4 项目总结

### TaskFlow vs 传统 Operator

| 维度 | 传统 Operator | TaskFlow (`@task`) |
|------|-------------|-------------------|
| XCom 传递 | 手动 `xcom_push/pull` | 自动（函数返回值） |
| 依赖声明 | 显式 `>>` 操作符 | 函数调用链 |
| 参数传递 | `op_kwargs` 字典 | 函数参数（类型提示） |
| 代码量 | 高 | 低（减少 40%-60%） |
| IDE 支持 | 无类型检查 | 类型提示 + 自动补全 |
| 可测试性 | 需要 mock Airflow 上下文 | 可直接调用函数测试 |
| 适用场景 | 所有 Operator 类型 | 仅 Python 逻辑 |

### 注意事项

1. **`@task` 是延迟执行的**：函数体内的代码在 Worker 上执行，不是在 Dag 解析时执行。`@task` 的返回值是 `XComArg`，不是实际值。
2. **`@dag` 定义的函数必须被调用一次**：否则 Dag 不会被注册。
3. **类型注解不是强制的，但强烈推荐**：它们让代码自文档化，IDE 能提供更准确的提示。
4. **`@task.branch` 返回的是 task_id 字符串**，不是 Task 对象。

### 思考题

1. 用 TaskFlow 重写第 6 章的 `operator_deep_dive` Dag，对比代码行数、可读性、XCom 传递的清晰度。你更喜欢哪种风格？为什么？
2. 在 TaskFlow 中，如果 `task_a` 返回了一个 dict，`task_b` 的参数是 `result: dict`，但 `task_c` 只读取 dict 中的某个字段 `result["count"]`。如何设计函数签名让 downstream task 只接收 `count` 而不是整个 dict？这有什么好处？

*（答案将在后续章节揭晓）*

---

> **本章完成**：你已经掌握了 TaskFlow API 的核心用法。下一章将学习如何让 Dag 拥有"可配置"的灵活性——Dag 参数与动态 Dag 生成。
