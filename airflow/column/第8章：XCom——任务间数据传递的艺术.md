# 第8章：XCom——任务间数据传递的艺术

## 1 项目背景

数据工程师小刘在 Airflow 上搭建了一条推荐系统训练流水线：每天凌晨 2 点，Task A 从 MySQL 导出用户行为数据，Task B 做特征工程，Task C 训练模型，Task D 评估并上线。

流程看起来很完美——直到上线后第二天就出了问题。Task C（模型训练）偶尔报错"训练数据为空"，但 Task A 明明成功导出了 50 万条数据。排查后发现：Task A 的数据库连接偶尔超时，导致实际导出数据量为 0，但脚本本身没有报错（它只是写了 0 条记录到一个 CSV 文件）。Task B 读了这个空文件，也正常处理了 0 条数据。Task C 拿到的特征表为空，训练失败。

小刘意识到：Task A 需要把"实际导出了多少条数据"这个信息传递给 Task B，Task B 再传递给 Task C。如果数据量为 0，B 和 C 应该直接跳过并告警，而不是继续执行。

这引出了 Airflow 的核心机制之一——**XCom（Cross-Communication，跨任务通信）**。XCom 允许任务之间通过元数据库传递小型数据（默认 < 48KB），实现任务的"协作"而非简单的前后顺序。

---

## 2 项目设计

**小胖**（看着 Task 间的配置文件傻眼）："任务间传数据不是应该用文件或者数据库吗？Airflow 搞个 XCom 又是啥？"

**大师**："XCom 本质上就是把上游任务的产出存到元数据库的 xcom 表里，下游任务再去查。它像一个公共白板——你写上去，我读过来。但注意：XCom 不是为了传大文件设计的，它适合传元数据摘要，比如'处理了 5000 行'、'输出文件路径是 s3://bucket/data/2025-01-15/'。"

**小白**："XCom 的数据存在哪里？有没有什么限制？"

**大师**："存在 `xcom` 数据库表中。默认情况下，每个 XCom 记录不超过 48KB（SQLite 默认 2GB，但不建议）。超过这个限制需要修改 `xcom_max_len`。最佳实践是：XCom 只传'指向数据的指针'，数据本身放在 S3、HDFS、数据库里。"

**小胖**："那我怎么往 XCom 里写数据？怎么读数据？"

**大师**："在 TaskFlow API 中，`@task` 装饰的函数返回值会自动写入 XCom。在传统 Operator 中，可以手动调用 `ti.xcom_push()` 写入、`ti.xcom_pull()` 读取。TaskFlow 的自动 XCom 是最推荐的方式——它让数据流变得像函数调用一样自然。"

**小白**："那多个 DagRun 之间的 XCom 会相互干扰吗？"

**大师**："不会。每个 XCom 记录都绑定了 `dag_id`、`task_id`、`run_id` 作为 key——不同的 DagRun 是天然隔离的。这也是 xcom_pull 默认读取同一个 DagRun 内数据的原因。"

> **技术映射**：XCom = 快递单号——不寄送实物（大文件），但记录了包裹内容（元数据）和包裹在哪（文件路径），方便上下游追踪。

---

## 3 项目实战

### 3.1 TaskFlow 自动 XCom

这是最简单、最推荐的方式：

```python
from airflow.sdk import DAG
from airflow.sdk.operators.python import task
from datetime import datetime

with DAG("xcom_taskflow", schedule=None, start_date=datetime(2025, 1, 1)) as dag:

    @task
    def extract():
        """上游任务：提取数据，返回摘要"""
        # 自动写入 XCom，key='return_value'
        return {"rows": 50000, "date": "2025-01-15", "file": "s3://bucket/data.parquet"}

    @task
    def transform(summary: dict):
        """下游任务：接收上游的返回值"""
        # 通过类型注解声明接收 XCom
        print(f"收到 {summary['rows']} 行数据")
        print(f"文件路径: {summary['file']}")

        if summary["rows"] == 0:
            raise ValueError("数据为空，终止处理！")

        return {"processed": summary["rows"] * 0.95}

    @task
    def load(result: dict):
        print(f"加载 {result['processed']} 条处理后的数据")

    # 数据流：extract → transform → load
    result = extract()
    processed = transform(result)
    load(processed)
```

数据流分析：`extract()` 的返回值通过 XCom 自动传递给 `transform()` 的 `summary` 参数，`transform()` 的返回值再传递给 `load()`。

### 3.2 手动 XCom 操作（传统 Operator 风格）

**写入 XCom**：

```python
def _extract_manual(**context):
    ti = context["task_instance"]

    # 写入单个值
    ti.xcom_push(key="row_count", value=50000)

    # 写入多个值
    ti.xcom_push(key="quality_metrics", value={
        "null_rate": 0.01,
        "dup_rate": 0.03,
        "completeness": 0.96,
    })

    # 不指定 key 时，key 默认为 'return_value'
    return "extract_completed"
```

**读取 XCom**：

```python
def _load_manual(**context):
    ti = context["task_instance"]

    # 读取上游任务的 return_value
    status = ti.xcom_pull(task_ids="extract_task")

    # 读取上游任务的特定 key（跨 Dag 读取）
    metrics = ti.xcom_pull(
        task_ids="extract_task",
        key="quality_metrics",
    )

    # 读取特定 DagRun 的数据（通常不这样用）
    row_count = ti.xcom_pull(
        task_ids="extract_task",
        key="row_count",
        dag_id="xcom_demo",
        include_prior_dates=True,  # 包含之前日期的 XCom
    )

    print(f"Status: {status}")
    print(f"Metrics: {metrics}")
```

### 3.3 完整实战：带数据质量检查的 ETL

在 `dags/` 下创建 `xcom_deep_dive.py`：

```python
"""
XCom 深度实战：带数据质量检查的 ETL 流水线
"""
from datetime import datetime, timedelta
from airflow.sdk import DAG
from airflow.sdk.operators.python import PythonOperator

default_args = {
    "owner": "xcom-demo",
    "retries": 1,
    "retry_delay": timedelta(minutes=1),
}

with DAG(
    dag_id="xcom_deep_dive",
    default_args=default_args,
    schedule=None,
    start_date=datetime(2025, 1, 1),
    catchup=False,
    tags=["demo", "xcom"],
) as dag:

    def _extract_data(**context):
        """模拟数据抽取，返回统计信息"""
        import random
        rows = random.randint(10000, 50000)
        result = {
            "total_rows": rows,
            "table": "user_events",
            "date": context["ds"],
            "extract_time": str(datetime.now()),
        }
        print(f"抽取值: {result}")
        return result

    extract = PythonOperator(
        task_id="extract",
        python_callable=_extract_data,
    )

    def _quality_check(**context):
        """数据质量检查"""
        ti = context["task_instance"]
        # 读取上游数据
        data = ti.xcom_pull(task_ids="extract")
        total_rows = data["total_rows"]

        print(f"检查数据质量: {total_rows} 行")

        quality_report = {
            "total_rows": total_rows,
            "passed": True,
            "checks": {
                "row_count_min": total_rows >= 1000,
                "row_count_max": total_rows <= 100000,
            },
        }

        # 写入质量报告
        ti.xcom_push(key="quality_report", value=quality_report)

        if not quality_report["passed"]:
            raise ValueError(f"数据质量检查失败: {quality_report['checks']}")

        return total_rows  # 下游可直接接收这个值

    quality = PythonOperator(
        task_id="quality_check",
        python_callable=_quality_check,
    )

    def _process_data(**context):
        """数据处理（同时读取两个上游的 XCom）"""
        ti = context["task_instance"]

        # 读取 extract 的 return_value
        extract_result = ti.xcom_pull(task_ids="extract")

        # 读取 quality_check 的 return_value
        total_rows = ti.xcom_pull(task_ids="quality_check")

        # 读取 quality_report（特定 key）
        quality_report = ti.xcom_pull(
            task_ids="quality_check",
            key="quality_report",
        )

        print(f"源表: {extract_result['table']}")
        print(f"行数: {total_rows}")
        print(f"质量检查: {quality_report['checks']}")

        return {"processed": total_rows * 0.95}

    process = PythonOperator(
        task_id="process",
        python_callable=_process_data,
    )

    # 依赖链
    extract >> quality >> process
```

### 3.4 BashOperator 中使用 XCom

```python
# BashOperator 通过 jinja 模板读取 XCom
read_xcom = BashOperator(
    task_id="read_xcom",
    bash_command="""
        echo "上游任务返回值：{{ ti.xcom_pull(task_ids='extract') }}"
        echo "总行数：{{ ti.xcom_pull(task_ids='extract')['total_rows'] }}"
    """,
)
```

### 3.5 XCom 后端自定义

默认情况下 XCom 存在数据库中。对于大数据量场景，可以自定义 XCom 后端存到对象存储：

```ini
# airflow.cfg
[core]
xcom_backend = mypackage.xcom_backends.S3XComBackend
```

自定义后端示例（存到 S3）：

```python
from airflow.models.xcom import BaseXCom
import boto3
import json

class S3XComBackend(BaseXCom):
    PREFIX = "airflow-xcom"
    BUCKET = "my-bucket"

    @classmethod
    def serialize_value(cls, value, **kwargs):
        """将值序列化后存入 S3，在数据库 XCom 表中只存引用"""
        import uuid
        key = f"{cls.PREFIX}/{uuid.uuid4()}.json"
        s3 = boto3.client("s3")
        s3.put_object(
            Bucket=cls.BUCKET,
            Key=key,
            Body=json.dumps(value).encode("utf-8"),
        )
        return BaseXCom.serialize_value(f"s3://{cls.BUCKET}/{key}")

    @classmethod
    def deserialize_value(cls, result):
        """从 S3 读取实际值"""
        value = super().deserialize_value(result)
        if isinstance(value, str) and value.startswith("s3://"):
            key = value.replace(f"s3://{cls.BUCKET}/", "")
            s3 = boto3.client("s3")
            obj = s3.get_object(Bucket=cls.BUCKET, Key=key)
            return json.loads(obj["Body"].read())
        return value
```

---

## 4 项目总结

### XCom 使用模式对比

| 模式 | 优点 | 缺点 |
|------|------|------|
| TaskFlow 自动 XCom | 代码简洁，像函数调用 | 仅限 `@task` 装饰器 |
| `xcom_push` / `xcom_pull` | 灵活，支持任意 Operator | 代码较冗长，key 管理需手动 |
| Jinja 模板引用 | BashOperator 最佳方式 | 只能读取，不能写入 |

### 适用场景

1. **数据量统计传递**：上游记录处理行数 → 下游判断是否继续
2. **文件路径传递**：上游生成文件 → 下游读取文件
3. **质量报告传递**：上游质量检查结果 → 下游决策分支
4. **配置覆盖**：上游动态生成配置 → 下游使用

**不适用场景**：
- 传递大于 48KB 的数据（应用对象存储 + XCom 传路径）
- 跨 Dag 高频通信（应使用 Asset 或 ExternalTaskSensor）
- 需要事务性保证的数据传递（XCom 没有事务语义）

### 注意事项

1. **XCom 不是队列**：不要在循环中 push 大量 XCom——每条都是数据库记录
2. **key 命名要规范**：避免 key 冲突，如 `quality_report`、`row_count`、`error_details`
3. **注意序列化**：XCom 值会被 JSON 序列化，datetime 对象、自定义类需要手动处理
4. **清理策略**：历史 XCom 不会自动清理，定期运行 `airflow db clean` 释放空间

### 思考题

1. 如果你需要在上游任务中 push 一个 500MB 的 DataFrame 给下游，直接用 XCom 会有什么问题？你有哪些替代方案？
2. TaskFlow 的自动 XCom 中，`@task` 函数的参数名与上游 `@task` 函数的返回值变量名需要一致吗？提示：思考 `expand()` 机制如何处理多个上游。

*（答案将在后续章节揭晓）*

---

> **本章完成**：你已经掌握了 XCom 的三种使用模式。下一章将学习 Airflow 的传感器——如何让工作流"等待"外部事件。
