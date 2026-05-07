# 第3章：编写第一个 Dag——从 Cron 到 Python 脚本编排

## 1 项目背景

某电商公司的数据分析师小陈，每天上班第一件事就是手动跑一堆脚本：先用 SQL 从 MySQL 导出昨天的订单数据，再用 Python 脚本做数据清洗和聚合，最后用另一个 Python 脚本生成业务报表发到钉钉群。这套流程他做了半年，闭着眼睛都能操作——但问题也很明显：

1. **人工依赖**：小陈请假时，同事不知道脚本之间的执行顺序，经常搞错
2. **没有容错**：Python 脚本偶尔因为网络波动报错，就得从头手动重跑
3. **无法回溯**：某个周六的数据漏跑了，周一才发现，手动补跑时已经搞不清该用哪个参数
4. **缺乏可见性**：老板问"今天报表跑完了吗？"小陈只能去翻终端日志

小陈尝试用 crontab 自动化，但他很快发现 crontab 的局限：每天 8:00 跑 A 脚本，8:30 跑 B 脚本——但如果 A 拖到 8:35 才结束呢？B 需要 A 的输出结果，设置 8:30 是"猜"的时间，一旦不准就全乱套了。

> 这就是 Airflow 最基础也最核心的价值——**把"脚本编排"变成"工作流定义"**。

---

## 2 项目设计

**小胖**（打开 IDE，准备写第一个 Dag）："所以 Airflow 的 Dag 本质上就是一个 Python 文件？跟写普通 Python 脚本有什么区别？"

**大师**："好问题。Dag 文件是配置文件，不是执行文件。它的作用是**描述**任务是什么、任务之间怎么依赖，而不是直接执行。拿炒菜来比喻：Dag 文件是菜谱（先切菜、再热油、最后下锅），真正的执行是由 Airflow 的 Scheduler 和 Executor 在合适的时机触发的。"

**小白**："那 `with DAG(...)` 这个上下文管理器是做什么的？"

**大师**：`with DAG(...)` 创建了一个工作流定义的作用域。在这个作用域里定义的每个 Operator 实例都会自动注册到当前 Dag 上。你不需要显式地 `dag.add_task(t1)`——这是 Airflow 的魔法所在。但要注意，一切在 `with DAG(...)` 块内的顶层代码在 Dag Processor 解析文件时就会执行，所以不要在顶层做数据库连接、读取大文件之类的重操作。"

**小胖**："那 `>>` 操作符呢？它做了什么？"

**大师**：`t1 >> t2` 本质上是调用了 `t1.set_downstream(t2)`，在 Airflow 内部建立了一条有向边。这告诉 Scheduler：t2 必须在 t1 成功后才能调度。你可以在 Python 中任意链式调用：`t1 >> t2 >> t3` 或者 `t1 >> [t2, t3]`——后者的意思是 t2 和 t3 都依赖 t1，但它们之间可以并行。"

**小白**："我注意到 `start_date` 和 `schedule` 参数——这两个是定义调度规则的关键吧？"

**大师**："没错。`schedule='@daily'` 表示每天跑一次，`start_date` 表示从哪天开始跑。但这里有一个新手最容易踩的坑：**start_date 不等于第一次执行的时间**。如果 start_date 是昨天，schedule 是每天 8 点，那 Airflow 会在昨天 8 点+1 个调度间隔后（也就是今天 8 点）触发生效的第一个 DagRun。"

**小胖**："等等，这什么逻辑？为啥不是直接昨天 8 点跑？"

**大师**："因为在 Airflow 的哲学里，调度间隔覆盖的时间段 `[start, end)` 是左闭右开区间。以 `@daily` 为例，2025-01-01 的 DagRun 覆盖的是 2025-01-01 00:00 到 2025-01-02 00:00 的数据。这个 DagRun 应该在区间结束时（即 2025-01-02 00:00 之后）被触发——因为你得等这一天结束了才有完整的数据。"

> **技术映射**：Airflow 调度 = 快递员取件——start_date 是"开始提供服务"的日子，schedule 是"每天几点来取"，真正第一次取件是在 start_date 之后的下一个取件时间点。

---

## 3 项目实战

### 环境准备

确保第 2 章搭建的 Docker Compose 环境已正常运行：

```bash
docker compose ps | grep healthy
```

### 3.1 创建第一个 Dag：订单数据 ETL

**步骤目标**：编写一个完整的订单数据 ETL Dag，包含三个任务（抽取、清洗、加载），并在 Web UI 中触发运行。

**步骤 1：创建 Dag 文件**

在 `dags/` 目录下创建 `order_etl.py`：

```python
"""
订单数据 ETL Dag
功能：每小时从 MySQL 抽取订单数据 → Python 清洗 → 写入到输出目录
"""
from datetime import datetime, timedelta
from airflow.sdk import DAG
from airflow.sdk.operators.bash import BashOperator
from airflow.sdk.operators.python import PythonOperator
from airflow.sdk.operators.empty import EmptyOperator

# ============================================================
# 默认参数：所有 Task 共享的配置
# ============================================================
default_args = {
    "owner": "data-team",
    "retries": 2,                           # 失败后最多重试 2 次
    "retry_delay": timedelta(minutes=5),     # 每次重试间隔 5 分钟
    "email": ["data-team@company.com"],
    "email_on_failure": True,
}

with DAG(
    dag_id="order_etl_v1",
    default_args=default_args,
    description="订单数据 ETL 流水线",
    # 每天凌晨 2 点执行
    schedule="0 2 * * *",
    # 从 2025 年 1 月 1 日开始调度
    start_date=datetime(2025, 1, 1),
    # 不回溯历史（设为 False 避免一次性创建大量历史 DagRun）
    catchup=False,
    # 标签用于在 UI 中分类过滤
    tags=["etl", "orders"],
) as dag:

    # ============================================================
    # Task 1: 从 MySQL 抽取数据（BashOperator 模拟）
    # ============================================================
    extract_task = BashOperator(
        task_id="extract_orders",
        bash_command="""
            echo "[{{ ts }}] 开始抽取订单数据..."
            echo "连接 MySQL: order_db.orders 表"
            sleep 3  # 模拟数据抽取耗时
            echo "抽取完成：10234 条订单记录"
            mkdir -p /tmp/airflow-data/{{ ds }}
            echo "10234" > /tmp/airflow-data/{{ ds }}/row_count.txt
        """,
    )

    # ============================================================
    # Task 2: Python 数据清洗
    # ============================================================
    def _clean_orders(**context):
        """
        清洗订单数据：
        - 读取上一任务输出的行数统计
        - 模拟数据清洗逻辑
        - 返回清洗后的摘要信息（自动成为 XCom）
        """
        import json

        # 从 context 中获取调度信息
        ds = context["ds"]          # 执行日期，格式 2025-01-15
        ts = context["ts"]          # 执行时间戳
        task_instance = context["task_instance"]

        # 读取上游任务生成的文件
        with open(f"/tmp/airflow-data/{ds}/row_count.txt") as f:
            raw_count = int(f.read().strip())

        print(f"开始清洗 {ds} 的数据，共 {raw_count} 条原始记录")

        # 模拟清洗规则
        valid_count = int(raw_count * 0.95)    # 95% 有效
        invalid_count = raw_count - valid_count

        # 写入清洗结果
        result = {
            "date": ds,
            "raw_count": raw_count,
            "valid_count": valid_count,
            "invalid_count": invalid_count,
            "clean_rate": f"{valid_count/raw_count*100:.2f}%",
        }

        with open(f"/tmp/airflow-data/{ds}/clean_result.json", "w") as f:
            json.dump(result, f, ensure_ascii=False)

        print(f"清洗完成：有效 {valid_count} 条，无效 {invalid_count} 条")

        # 返回结果会自动转为 XCom，下游任务可以读取
        return result

    clean_task = PythonOperator(
        task_id="clean_orders",
        python_callable=_clean_orders,
    )

    # ============================================================
    # Task 3: 加载结果并生成摘要
    # ============================================================
    def _load_and_summarize(**context):
        """
        加载清洗后的数据并生成业务摘要：
        - 通过 XCom 获取上游任务的输出
        - 生成摘要报告
        """
        task_instance = context["task_instance"]

        # 通过 XCom 拉取上游任务的返回值
        clean_result = task_instance.xcom_pull(task_ids="clean_orders")

        print(f"加载 {clean_result['date']} 的清洗结果：")
        print(f"  原始记录：{clean_result['raw_count']}")
        print(f"  有效记录：{clean_result['valid_count']}")
        print(f"  无效记录：{clean_result['invalid_count']}")
        print(f"  清洗率：{clean_result['clean_rate']}")

        return f"订单清洗完成，清洗率 {clean_result['clean_rate']}"

    load_task = PythonOperator(
        task_id="load_and_summarize",
        python_callable=_load_and_summarize,
    )

    # ============================================================
    # 定义任务依赖：extract → clean → load
    # ============================================================
    extract_task >> clean_task >> load_task
```

**步骤 2：等待 Dag 加载**

Dag Processor 每 30 秒扫描一次 `dags/` 目录。等待约 1 分钟后，刷新 Web UI（http://localhost:8080），你应该能在首页看到 `order_etl_v1`。

如果 Dag 没有出现，检查错误：

```bash
docker compose logs airflow-dag-processor-1 | grep -i error
```

**步骤 3：手动触发 Dag**

在 Web UI 中：
1. 点击 Dag 名称 `order_etl_v1` 进入详情页
2. 点击右上角的播放按钮（▶ Trigger Dag）
3. 可选：输入 `execution_date` 或 `run_id`
4. 点击 "Trigger" 确认

**步骤 4：观察执行过程**

切换到 Grid 视图，你会看到：
- 灰色方块 = 待执行
- 绿色方块 = 成功
- 正在运行的方块边跑边闪

点击任一 Task 方块，可以查看日志：

```bash
# 也可以通过 CLI 查看日志
docker exec airflow-scheduler-1 airflow tasks logs order_etl_v1 extract_orders 2025-01-15T00:00:00
```

### 3.2 理解通过 XCom 传递数据

上面的例子中，`clean_task` 的返回值自动成为 XCom，`load_task` 通过 `xcom_pull` 读取：

```python
# 写入（TaskFlow 自动处理）
return {"valid_count": 9500}

# 读取
result = task_instance.xcom_pull(task_ids="clean_orders")
```

你还可以在 Web UI 中直接查看 XCom：Admin → XComs → 搜索对应 Dag ID 和 Task ID。

### 3.3 使用 CLI 触发和测试

```bash
# 测试单个 Dag（会实际执行任务）
docker exec airflow-scheduler-1 airflow dags test order_etl_v1 2025-01-15

# 仅渲染模板，不执行（用于检查 Jinja 模板是否正确）
docker exec airflow-scheduler-1 airflow tasks render order_etl_v1 extract_orders 2025-01-15

# 查看 Dag 的运行历史
docker exec airflow-scheduler-1 airflow dags list-runs -d order_etl_v1
```

### 可能遇到的坑

1. **Jinja 模板渲染错误**：`{{ ds }}` 只能在 BashOperator 的 `bash_command` 中使用（它天然支持模板），但在 PythonOperator 的 `python_callable` 参数中无效——Python 函数内部需要通过 `context["ds"]` 获取。

2. **XCom 读取为空**：确保在正确的时刻读取——XCom 只有在下游任务通过 `xcom_pull` 时才能读取到上游任务的数据，且上游必须已经成功执行。

3. **文件路径不存在**：示例中写入了 `/tmp/airflow-data/`，确保目录存在（或在 Task 内部先 `mkdir -p`）。

---

## 4 项目总结

### Dag 定义核心要素

| 要素 | 作用 | 示例 |
|------|------|------|
| DAG 对象 | 声明工作流 | `DAG(dag_id="my_etl", schedule="0 2 * * *")` |
| Operator | 定义单个任务 | `BashOperator(task_id="run_sh")` |
| 依赖关系 | 定义执行顺序 | `t1 >> t2 >> t3` |
| default_args | 共享默认配置 | `{"retries": 2, "owner": "me"}` |

### Airflow vs Cron 执行模型对比

| 特性 | Cron | Airflow |
|------|------|---------|
| 依赖管理 | 无，需手动控制 | DAG 拓扑自动识别 |
| 失败处理 | 仅知道脚本退出码 | 自动重试 + 状态追踪 + 回调 |
| 并行执行 | 可同时触发，但无法协调 | Executor 管理并行度 |
| 参数传递 | 环境变量/文件 | XCom 原生支持 |
| 可视化 | 无 | Web UI 全生命周期可见 |

### 注意事项

1. **Dag 文件是声明式的，不是执行式的**：不要在里面写 `if __name__ == "__main__"` 并直接运行——它应该被 Airflow 加载和管理。
2. **import 要谨慎**：Dag 文件顶层的 import 会在每次 Dag 解析时执行。重量级库（如 pandas）的 import 应该放在 Task 函数内部。
3. **dag_id 必须全局唯一**：重复的 dag_id 会导致覆盖，先加载的 Dag 会被后加载的替换。

### 思考题

1. 如果 `extract_orders` 任务失败重试了 2 次后才成功，`clean_orders` 会等待所有重试结束后才执行，还是会在第一次成功后就开始执行？
2. 假设你有三个下游任务 B、C、D 都依赖任务 A，但你希望 A 成功之后 B 和 C 并行执行，等 B 和 C 都成功后 D 才执行，应该如何定义依赖关系？

*（答案将在后续章节揭晓）*

---

> **本章完成**：你已经编写了人生中第一个 Airflow Dag，理解了 DAG、Operator、依赖、XCom 的关系。下一章我们将深入 Web UI，学会如何通过控制台高效管理和诊断工作流。
