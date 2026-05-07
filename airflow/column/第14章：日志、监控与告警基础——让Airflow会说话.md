# 第14章：日志、监控与告警基础——让 Airflow "会说话"

## 1 项目背景

凌晨 3 点，运维工程师小陈被手机震动惊醒——业务方投诉"今天的报表还没出来"。小陈登录 Airflow Web UI，发现一个关键 Dag 的 `transform_data` 任务在凌晨 1:15 就失败了。但他没有收到任何通知——没有邮件、没有钉钉消息、没有告警。因为在这之前，没有人配置过任何告警。

更糟糕的是，小陈想排查失败原因时，发现 Airflow 默认的日志配置只保留了最近 30 天的数据，而 3 天前另一个看起来"成功"的 Task 实际上输出数据有问题——他想回溯查看那时的日志，却已经找不到了。

> 一个成熟的调度系统，不仅要"跑得对"，还要"看得见"和"叫得响"。**日志、监控、告警**构成 Airflow 可观测性的三根支柱。本章将带你建立 Airflow 的"神经系统"——让工作流的健康状况实时可见、异常事件及时通知。

---

## 2 项目设计

**小胖**（凌晨被叫醒后顶着黑眼圈）："大师，我怎么才能让 Airflow 任务失败时自动通知我？不能每次都让我自己去 Web UI 上盯着看吧？"

**大师**："Airflow 提供了多层级的告警机制。第一层是任务级别的回调：`on_failure_callback`、`on_success_callback`、`on_retry_callback`、`sla_miss_callback`。这些回调函数在对应事件发生时自动执行，你可以在这里发邮件、发钉钉、调 API。"

**小白**："那 SLA 是什么？和 timeout 有什么区别？"

**大师**：`execution_timeout` 是任务级别的硬性限制——超时就 kill。SLA 是 Dag 级别的软性监控——超时不kill，但会触发 `sla_miss_callback`，让你知道"这个任务跑得比预期慢"。SLA 的典型场景是：报表 Dag 应该在每天早上 9 点前完成，9 点以后还没完成就需要通知业务方，但任务本身应该继续跑完。"

**小胖**："日志呢？我看 Airflow 的任务日志可以直接在 Web UI 里查看——但日志存在哪？能存多久？"

**大师**："默认存在本地文件系统的 `AIRFLOW_HOME/logs/` 下。对于生产环境，推荐配远程日志存储——S3、GCS、Azure Blob。这样即使 Worker 容器被销毁，日志也不会丢失。Airflow 还支持自定义日志后端和日志格式——比如你想把所有日志以 JSON 格式输出，方便 ELK 解析。"

**小白**："有没有更全面的监控？比如我监控整个集群的 Dag 成功率、任务排队时间？"

**大师**："那就需要接入 Metrics（指标）系统了。Airflow 内置了 StatsD 指标暴露，可以对接 Prometheus + Grafana。这是中级篇第 28 章的内容，但基础设置可以先了解：在 airflow.cfg 中配置 statsd，Airflow 就会定期发送 dag_processing、scheduler、ti_status、pool 等指标。"

> **技术映射**：回调 = 烟雾报警器（火灾 = 任务失败，自动喷水 = 发通知），SLA = 快递时效承诺（超过承诺时间开始调查，但不拒收），StatsD + Prometheus = 全屋智能传感器（每个房间的状态自动上报到监控中心）。

---

## 3 项目实战

### 3.1 任务回调：失败/成功/重试通知

在 `dags/` 下创建 `alerting_demo.py`：

```python
"""
告警机制实战：邮件 + Slack + 钉钉通知
"""
from datetime import datetime, timedelta
from airflow.sdk import DAG
from airflow.sdk.operators.python import PythonOperator, task
import smtplib
import requests
import json

# ============================================================
# 回调函数定义（可在多个 Dag 间复用）
# ============================================================

def on_failure_notify(context):
    """任务失败时发送钉钉通知"""
    dag_id = context["dag"].dag_id
    task_id = context["task_instance"].task_id
    execution_date = context["execution_date"]
    exception = context.get("exception", "Unknown error")

    message = {
        "msgtype": "text",
        "text": {
            "content": (
                f"❌ Airflow 任务失败\n"
                f"Dag: {dag_id}\n"
                f"Task: {task_id}\n"
                f"时间: {execution_date}\n"
                f"错误: {str(exception)[:500]}\n"
                f"日志: http://airflow:8080/dags/{dag_id}/grid?task_id={task_id}&dag_run_id={context['run_id']}"
            )
        },
    }

    webhook_url = "https://oapi.dingtalk.com/robot/send?access_token=YOUR_TOKEN"
    try:
        requests.post(webhook_url, json=message, timeout=5)
        print("钉钉通知已发送")
    except Exception as e:
        print(f"钉钉通知发送失败: {e}")


def on_retry_notify(context):
    """任务重试时通知"""
    ti = context["task_instance"]
    print(f"⚠️ 任务 {ti.task_id} 第 {ti.try_number} 次重试...")


def on_success_notify(context):
    """任务成功时通知（通常不需要，仅关键任务）"""
    ti = context["task_instance"]
    print(f"✅ 任务 {ti.task_id} 完成")


def sla_miss_notify(dag, task_list, blocking_task_list, slas, blocking_tis):
    """SLA 未达标时通知"""
    message = {
        "msgtype": "text",
        "text": {
            "content": (
                f"⚠️ SLA 未达标\n"
                f"Dag: {dag.dag_id}\n"
                f"未完成 Task: {[t.task_id for t in task_list]}\n"
            )
        },
    }
    webhook_url = "https://oapi.dingtalk.com/robot/send?access_token=YOUR_TOKEN"
    requests.post(webhook_url, json=message, timeout=5)
```

### 3.2 完整告警 Dag 示例

```python
with DAG(
    dag_id="alert_demo",
    schedule="@daily",
    start_date=datetime(2025, 1, 1),
    catchup=False,
    default_args={
        "owner": "alert-demo",
        "retries": 2,
        "retry_delay": timedelta(minutes=1),
    },
    on_failure_callback=on_failure_notify,    # Dag 级别失败回调
    on_success_callback=on_success_notify,    # Dag 级别成功回调（可选）
) as dag:

    @task(
        retries=3,
        retry_delay=timedelta(seconds=30),
        on_retry_callback=on_retry_notify,    # Task 级别重试回调
        sla=timedelta(minutes=5),             # SLA：期望 5 分钟内完成
    )
    def critical_task():
        import time
        print("执行关键任务...")
        time.sleep(10)  # 模拟工作
        print("关键任务完成")

    @task(
        on_failure_callback=on_failure_notify,
    )
    def unstable_task():
        import random
        if random.random() < 0.5:
            raise ValueError("模拟随机失败！")
        return "success"

    @task
    def downstream():
        print("下游任务执行")

    critical_task() >> unstable_task() >> downstream()
```

### 3.3 日志配置实战

**步骤目标**：配置远程日志存储（以 S3 为例），实现日志持久化。

**airflow.cfg 配置**：

```ini
[logging]
# 日志存储目录（本地）
base_log_folder = /opt/airflow/logs

# 启用远程日志
remote_logging = True
remote_log_conn_id = s3_log_storage
remote_base_log_folder = s3://airflow-logs/production/

# 日志格式
logging_config_class = log_config.LOGGING_CONFIG

# 任务日志处理
task_log_reader = s3.task
```

**创建 S3 连接**：

```bash
docker exec airflow-scheduler-1 airflow connections add s3_log_storage \
  --conn-type aws \
  --conn-extra '{"region_name": "us-east-1"}'
```

**在 Web UI 中验证**：触发一个 Task 后，点击 Task → Logs，确认日志被写入 S3。

### 3.4 日志级别与过滤

```python
import logging

def _verbose_task(**context):
    logger = logging.getLogger("airflow.task")

    # 不同的日志级别
    logger.debug("这是调试信息（默认不显示）")
    logger.info("这是普通信息")
    logger.warning("这是警告信息")

    try:
        config = context["params"].get("threshold", 0.1)
        logger.info(f"使用阈值: {config}")
    except Exception as e:
        logger.error(f"参数解析失败: {e}", exc_info=True)
```

要在 Web UI 中看到 `debug` 级别的日志，需要修改 `airflow.cfg`：

```ini
[logging]
logging_level = DEBUG
```

### 3.5 关键 Metrics 指标暴露

```ini
# airflow.cfg
[metrics]
statsd_on = True
statsd_host = localhost
statsd_port = 8125
statsd_prefix = airflow
```

这会让 Airflow 自动暴露以下关键指标：

| 指标名 | 含义 | 告警建议 |
|--------|------|---------|
| `airflow.dag_processing.total_parse_time` | Dag 解析耗时 | > 60s 告警 |
| `airflow.scheduler.tasks.running` | 正在运行的任务数 | 接近并发上限时告警 |
| `airflow.ti_failures` | 任务失败计数 | 突然暴增时告警 |
| `airflow.ti.success` | 任务成功计数 | 突然下降时告警 |
| `airflow.pool.open_slots.pool_name` | Pool 可用槽位数 | < 10% 时告警 |

### 3.6 CLI 日志查看

```bash
# 查看特定 Task 的日志
docker exec airflow-scheduler-1 airflow tasks logs alert_demo critical_task 2025-01-15T00:00:00

# 查看最近一次运行的全部日志
docker exec airflow-scheduler-1 airflow tasks logs alert_demo critical_task

# 带 try-number 查看重试日志
docker exec airflow-scheduler-1 airflow tasks logs alert_demo unstable_task --try-number 2
```

---

## 4 项目总结

### 告警通道对比

| 通道 | 优点 | 缺点 | 适用场景 |
|------|------|------|---------|
| Email | 可附带详细日志 | 实时性差 | 日报汇总、非紧急通知 |
| 钉钉/飞书/Slack | 实时、IM 通知 | 消息大小有限 | 任务失败告警 |
| Webhook | 灵活、可对接任何系统 | 需自行开发 | 自定义告警管线 |
| PagerDuty/OpsGenie | 专业值班轮转 | 成本高 | 7×24 值班场景 |

### 注意事项

1. **告警疲劳**：不要为所有 Task 都配置 `on_failure_callback`，只为关键 Task 配置。否则每天收到 200 条告警，你很快就会无视它们。
2. **日志存储成本**：S3/GCS 存日志按量计费。设置日志过期策略（`AIRFLOW__LOGGING__REMOTE_LOG_RETENTION_DAYS=90`）。
3. **SLA 不是 timeout**：SLA 只是告警，不会终止 Task。如果需要在超时时终止，用 `execution_timeout`。

### 思考题

1. 如果你需要在一条 Dag 中包含 20 个 Task，其中只有 3 个是"关键任务"需要在失败时立即钉钉告警，其他 17 个失败时不告警（因为会自动重试）。你会如何设计回调配置？
2. 远程日志存储（S3）和本地日志存储（本地文件系统）可以同时使用吗？什么场景下需要这种双写策略？

*（答案将在后续章节揭晓）*

---

> **本章完成**：你已经为 Airflow 搭建了基础的可观测性体系。下一章将深入 Airflow 的"大脑"——调度器与执行器的工作原理。
