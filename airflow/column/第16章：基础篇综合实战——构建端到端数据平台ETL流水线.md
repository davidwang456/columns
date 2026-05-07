# 第16章：【基础篇综合实战】构建端到端数据平台 ETL 流水线

## 1 项目背景

某中型电商公司决定用 Airflow 替代现有的 cron + Shell 脚本调度体系。当前痛点包括：30+ 个脚本通过 crontab 分散在 3 台服务器上，依赖关系靠"预估时间"管理（A 脚本 2:00 启动，B 脚本 2:30 启动，假设 A 在 30 分钟内完成）；失败通知靠运维人工巡检；数据补跑需要手动写循环脚本。

CTO 提出需求：用 Airflow 搭建一条完整的订单数据 ETL 流水线，覆盖从数据抽取到报表生成的全链路，要求：
1. 每天凌晨 2 点自动执行，失败自动重试 3 次
2. 数据质量检查不合格时自动告警并中止下游
3. 支持手动触发指定日期的重跑
4. 敏感信息（数据库密码、API Key）不能硬编码在代码中

本章将融会贯通基础篇前 15 章的知识，从零构建这条生产级 ETL 流水线。

---

## 2 项目设计

**小胖**（看着架构图）："终于到综合实战了！我们要搭一个什么规模的流水线？"

**大师**："一个真实电商场景的订单数据处理链路，包含 6 个步骤：MySQL 订单抽取 → 数据质量检查 → 数据清洗 → 聚合计算 → 写入 Hive → 报表生成。但同时也要覆盖基础篇的核心知识点：Connection 管理、XCom 传递、回调告警、Backfill 回填。"

**小白**："技术选型上有什么建议？"

**大师**："TaskFlow API 作为主要编写范式（减少 XCom 样板代码），配合 BashOperator 处理已有的 Shell 脚本。Connection 统一管理数据库密码。钉钉 Webhook 做失败告警。使用 `max_active_tasks` 控制并发。整体结构清晰、可维护。"

**小胖**："验收标准呢？"

**大师**："三个维度验证：功能上，每小时自动执行、数据正确流转；容错上，Task 失败自动重试并告警；运维上，支持任意日期回填、日志可追溯。"

> **技术映射**：综合实战 = 驾校路考——不再是单个科目（倒库、侧方、坡起），而是在真实道路上综合运用所有技能。

---

## 3 项目实战

### 3.1 架构设计

```
┌─────────────────────────────────────────────────────────┐
│            Airflow 订单数据 ETL 流水线                      │
├─────────────────────────────────────────────────────────┤
│                                                          │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐           │
│  │ extract  │───▶│ quality  │───▶│  clean   │           │
│  │  订单抽取 │    │  质量检查 │    │  数据清洗 │           │
│  └──────────┘    └──────────┘    └────┬─────┘           │
│                                       │                 │
│                    ┌──────────────────┼──────┐          │
│                    ▼                  ▼      ▼          │
│              ┌──────────┐   ┌──────────┐  ┌─────────┐  │
│              │aggregate │   │ write_to │  │generate │  │
│              │ 指标聚合  │   │  hive    │  │ 报表    │  │
│              └────┬─────┘   └────┬─────┘  └────┬────┘  │
│                   │              │              │       │
│                   └──────────────┼──────────────┘       │
│                                  ▼                      │
│                            ┌──────────┐                 │
│                            │ notify   │                 │
│                            │执行完成通知│                 │
│                            └──────────┘                 │
└─────────────────────────────────────────────────────────┘
```

### 3.2 环境准备

```bash
# 1. 创建连接
docker exec airflow-scheduler-1 airflow connections add mysql_warehouse \
  --conn-type mysql \
  --conn-host 10.0.1.50 \
  --conn-schema warehouse_db \
  --conn-login etl_user \
  --conn-password 'SecurePass123!'

# 2. 创建变量
docker exec airflow-scheduler-1 airflow variables set quality_threshold 0.95
docker exec airflow-scheduler-1 airflow variables set dingtalk_webhook "https://oapi.dingtalk.com/robot/send?access_token=xxx"

# 3. 创建目录
docker exec airflow-scheduler-1 mkdir -p /tmp/airflow-data/reports
```

### 3.3 完整 Dag 代码

在 `dags/` 下创建 `comprehensive_etl.py`：

```python
"""
综合实战：订单数据 ETL 流水线
融会贯通基础篇 1-15 章知识
"""
from datetime import datetime, timedelta
from airflow.sdk import DAG
from airflow.sdk.operators.python import task
from airflow.sdk.operators.bash import BashOperator
from airflow.sdk.operators.empty import EmptyOperator
from airflow.models.variable import Variable
import json
import requests

# ============================================================
# 回调函数
# ============================================================
def dingtalk_failure_alert(context):
    """任务失败时发送钉钉告警"""
    dag_id = context["dag"].dag_id
    task_id = context["task_instance"].task_id
    exception = str(context.get("exception", "Unknown"))[:300]

    webhook = Variable.get("dingtalk_webhook")
    msg = {
        "msgtype": "text",
        "text": {
            "content": (
                f"❌ Airflow 任务失败\n"
                f"Dag: {dag_id} | Task: {task_id}\n"
                f"错误: {exception}\n"
            )
        },
    }
    try:
        requests.post(webhook, json=msg, timeout=5)
    except Exception as e:
        print(f"告警发送失败: {e}")

# ============================================================
# Dag 定义
# ============================================================
with DAG(
    dag_id="comprehensive_etl",
    description="订单数据 ETL 流水线（综合实战）",
    schedule="0 2 * * *",
    start_date=datetime(2025, 1, 12),
    catchup=False,
    max_active_tasks=4,
    default_args={
        "owner": "data-platform",
        "retries": 3,
        "retry_delay": timedelta(minutes=5),
        "on_failure_callback": dingtalk_failure_alert,
    },
    params={
        "force_full_sync": False,
        "quality_threshold": 0.95,
    },
    tags=["production", "etl", "comprehensive"],
) as dag:

    start = EmptyOperator(task_id="start")
    end = EmptyOperator(task_id="end")

    # ============================================================
    # Step 1: 订单数据抽取（BashOperator 模拟 MySQL 导出）
    # ============================================================
    extract_orders = BashOperator(
        task_id="extract_orders",
        bash_command="""
            echo "=== 订单数据抽取 ==="
            echo "日期: {{ ds }}"
            echo "模式: {{ '全量' if params.force_full_sync else '增量' }}"

            DIR="/tmp/airflow-data/{{ ds }}"
            mkdir -p "$DIR"

            # 模拟 MySQL 导出
            ROWS=$((50000 + RANDOM % 20000))
            echo "$ROWS" > "$DIR/raw_count.txt"
            echo "抽取完成: $ROWS 条订单"
        """,
    )

    # ============================================================
    # Step 2: 数据质量检查（TaskFlow）
    # ============================================================
    @task(retries=0)
    def quality_check(**context):
        """检查数据质量是否达标"""
        ds = context["ds"]
        threshold = context["params"]["quality_threshold"]
        count_file = f"/tmp/airflow-data/{ds}/raw_count.txt"

        with open(count_file) as f:
            total_rows = int(f.read().strip())

        print(f"总行数: {total_rows}")

        # 质量规则
        checks = {
            "min_rows": total_rows >= 10000,     # 最少 1 万行
            "max_rows": total_rows <= 200000,    # 最多 20 万行（异常值检测）
        }

        passed = all(checks.values())
        quality_score = sum(checks.values()) / len(checks)

        report = {
            "date": ds,
            "total_rows": total_rows,
            "checks": checks,
            "passed": passed,
            "score": quality_score,
        }

        # 保存质量报告
        with open(f"/tmp/airflow-data/{ds}/quality_report.json", "w") as f:
            json.dump(report, f, ensure_ascii=False)

        if not passed:
            raise ValueError(
                f"数据质量不合格！分数: {quality_score:.0%}\n"
                f"检查结果: {checks}"
            )

        print(f"质量检查通过，分数: {quality_score:.0%}")
        return report

    # ============================================================
    # Step 3: 数据清洗（TaskFlow）
    # ============================================================
    @task
    def clean_data(quality: dict):
        """清洗订单数据"""
        total = quality["total_rows"]
        print(f"开始清洗 {total} 条数据...")

        # 模拟清洗规则
        invalid = int(total * 0.03)
        duplicated = int(total * 0.02)
        valid = total - invalid - duplicated

        clean_report = {
            "total": total,
            "valid": valid,
            "invalid": invalid,
            "duplicated": duplicated,
            "clean_rate": f"{valid/total*100:.2f}%",
        }

        print(f"清洗完成: 有效 {valid}, 无效 {invalid}, 重复 {duplicated}")
        return clean_report

    # ============================================================
    # Step 4: 指标聚合（TaskFlow）
    # ============================================================
    @task
    def aggregate_metrics(clean: dict):
        """聚合业务指标"""
        print(f"聚合 {clean['valid']} 条有效数据的指标...")

        metrics = {
            "total_orders": clean["valid"],
            "estimated_gmv": clean["valid"] * 150.0,
            "avg_order_value": 150.0,
            "clean_rate": clean["clean_rate"],
        }

        print(f"指标聚合完成: {json.dumps(metrics, ensure_ascii=False)}")
        return metrics

    # ============================================================
    # Step 5: 写入 Hive（BashOperator）
    # ============================================================
    # 并行路径 A：写入聚合表
    write_to_hive = BashOperator(
        task_id="write_to_hive",
        bash_command="""
            echo "写入 Hive 表"
            echo "分区: dt={{ ds }}"
            echo "数据行数: {{ ti.xcom_pull(task_ids='aggregate_metrics')['total_orders'] }}"

            # 模拟写入
            mkdir -p /tmp/airflow-data/hive/dt={{ ds }}/
            echo "{{ ti.xcom_pull(task_ids='clean_data')['valid'] }}" \
                > /tmp/airflow-data/hive/dt={{ ds }}/orders.parquet
            echo "写入完成"
        """,
    )

    # ============================================================
    # Step 6: 报表生成（TaskFlow）
    # ============================================================
    # 并行路径 B：生成报表
    @task
    def generate_report(metrics: dict, quality: dict):
        """生成每日数据报表"""
        ds = quality["date"]

        report_content = f"""
        ============================
        订单数据日报 - {ds}
        ============================
        原始数据:   {quality['total_rows']:,} 行
        有效数据:   {metrics['total_orders']:,} 行
        数据清洗率: {metrics['clean_rate']}
        预估 GMV:   ¥{metrics['estimated_gmv']:,.2f}
        客单价:     ¥{metrics['avg_order_value']:,.2f}
        ============================
        """

        report_path = f"/tmp/airflow-data/reports/report_{ds}.txt"
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report_content)

        print(report_content)
        return report_path

    # ============================================================
    # Step 7: 完成通知（TaskFlow）
    # ============================================================
    @task(trigger_rule="all_done")
    def send_completion_notification(metrics=None, **context):
        """任务完成通知（无论成功失败都执行）"""
        if metrics:
            msg = f"✅ ETL 完成: {metrics['total_orders']:,} 条订单已处理"
        else:
            msg = "❌ ETL 异常：上游任务失败"

        webhook = Variable.get("dingtalk_webhook")
        requests.post(webhook, json={
            "msgtype": "text",
            "text": {"content": msg},
        }, timeout=5)
        print(f"通知已发送: {msg}")

    # ============================================================
    # 依赖关系定义
    # ============================================================
    start >> extract_orders

    quality_result = quality_check()
    extract_orders >> quality_result

    clean_result = clean_data(quality_result)

    metrics_result = aggregate_metrics(clean_result)

    # 并行路径
    clean_result >> write_to_hive
    report = generate_report(metrics_result, quality_result)

    [write_to_hive, report] >> send_completion_notification() >> end
```

### 3.4 测试验证

```bash
# 1. 语法检查
docker exec airflow-scheduler-1 python -m py_compile /opt/airflow/dags/comprehensive_etl.py

# 2. 渲染模板
docker exec airflow-scheduler-1 airflow tasks render comprehensive_etl extract_orders 2025-01-15

# 3. 测试单个 Task
docker exec airflow-scheduler-1 airflow tasks test comprehensive_etl extract_orders 2025-01-15

# 4. 执行完整 Dag（测试模式）
docker exec airflow-scheduler-1 airflow dags test comprehensive_etl 2025-01-15

# 5. 验证输出
docker exec airflow-scheduler-1 cat /tmp/airflow-data/reports/report_2025-01-15.txt

# 6. 测试回填
docker exec airflow-scheduler-1 airflow dags backfill \
  -s 2025-01-13 -e 2025-01-14 \
  --reset-dagruns \
  comprehensive_etl
```

---

## 4 项目总结

### 涉及的基础篇知识点

| 知识领域 | 对应章节 | 在本项目中的使用 |
|---------|---------|----------------|
| DAG 定义 | 第 3 章 | schedule、start_date、catchup、params |
| Operator | 第 6 章 | PythonOperator（TaskFlow）、BashOperator、EmptyOperator |
| 配置管理 | 第 7 章 | Connection 管理密码、Variable 存阈值 |
| XCom | 第 8 章 | TaskFlow 自动 XCom 传递 |
| 告警 | 第 14 章 | on_failure_callback、钉钉 Webhook |
| 调度参数 | 第 15 章 | max_active_tasks、retries |

### 扩展思考

这个项目还可以进一步优化：
1. **接入 Sensor**：在 extract 之前用 FileSensor 等待上游数据文件
2. **Asset 调度**：将 Hive 表的写入作为 Asset，让下游报表 Dag 自动触发
3. **Pool 隔离**：为 MySQL 连接创建独立 Pool，避免 extract 任务耗尽连接
4. **动态 Dag**：将表名参数化，用工厂函数生成多表同步 Dag

### 思考题

1. 如果 `quality_check` 失败（数据量为 0），观察 Grid 视图：哪些 Task 会被跳过？哪些会继续执行？`send_completion_notification` 会执行吗？
2. 这条流水线中 `write_to_hive`（BashOperator）和 `generate_report`（TaskFlow）是并行执行的。如果把 `write_to_hive` 也用 `@task` 重写，代码风格是否更统一？这样做有什么代价？

*（答案将在下一章揭晓）*

---

> **基础篇完成**！你已经掌握了 Airflow 的核心概念和单机实战技能。下一章我们将进入中级篇——深入元数据库，理解 Airflow 的状态存储机制。
