# 第17章：元数据库深度解析——DagModel、DagRun 与 TaskInstance

## 1 项目背景

某数据平台团队的 Airflow 集群已经运行了半年，积累了 3 万多条 DagRun 记录和 50 多万条 TaskInstance 记录。最近 Scheduler 的调度延迟从 5 秒飙升到了 30 秒，Web UI 的首页加载需要 10 秒以上。

DBA 分析发现：元数据库的 `task_instance` 表已经膨胀到 20GB，某些查询因为没有索引而全表扫描。更令人震惊的是——`xcom` 表中有 40% 的数据是 3 个月前的，一直没被清理。

团队之前把元数据库当作"黑盒"——只知道 Airflow 往里存数据，但从没关注过存了什么、怎么存的、存了多久。现在他们要直面这些问题：哪些表在增长最快？哪些查询最慢？如何在不影响在线服务的情况下清理历史数据？

> Airflow 的元数据库不是"set it and forget it"的组件——它需要被理解、监控和维护。本章将打开这个黑盒，带你深入理解 Airflow 的核心数据模型。

---

## 2 项目设计

**小胖**（看着几十张数据库表觉得头晕）："Airflow 的元数据库到底有多少张表？核心的是哪些？"

**大师**："大约 40 多张表，但 80% 的逻辑集中在 5 张核心表上：`dag`（Dag 定义）、`dag_run`（Dag 运行实例）、`task_instance`（任务实例）、`xcom`（任务间通信）、`log`（日志记录）。你理解了这 5 张表的关系，就理解了 Airflow 的全部状态。"

**小白**："DagModel 和 DAG 类有什么区别？我经常看到有人把这两个混着说。"

**大师**："这是最容易混淆的概念。`DAG` 类（在 task-sdk 中定义）是用户编写 Dag 时使用的 Python 类——它包含 `schedule`、`start_date`、`tasks` 等定义。`DagModel` 是 SQLAlchemy ORM 模型——它是元数据库 `dag` 表的映射。当 Dag Processor 解析 Dag 文件后，会把 DAG 对象序列化成 JSON，存入 `DagModel.serialized_dag` 字段。Scheduler 从不直接使用 DAG 类，它只读 `DagModel` 的序列化数据。"

**小胖**："那 DagRun 和 TaskInstance 的状态是怎么流转的？"

**大师**："DagRun 的状态流转：queued → running → success/failed。TaskInstance 的状态更复杂：scheduled → queued → running → success/failed/up_for_retry。但要注意——这些状态不是凭空变化的，而是由不同组件在不同时间点更新的：Scheduler 标记 scheduled/queued，Worker 标记 running/success/failed，Scheduler 检测超时后标记 zombie 清理。"

> **技术映射**：DagModel = 营业执照（静态的公司注册信息），DagRun = 每一次营业记录（某天某次的营业流水），TaskInstance = 收银小票（每个员工每次服务的明细）。

---

## 3 项目实战

### 3.1 核心表关系图

```
┌─────────────────────────────────────────────────────────────┐
│                      Airflow 元数据核心表                      │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌──────────┐    1:N    ┌──────────┐    1:N   ┌────────────┐│
│  │   dag    │──────────▶│ dag_run  │─────────▶│task_instance││
│  │ dag_id   │           │ run_id   │          │  task_id   ││
│  │ paused   │           │ state    │          │  state     ││
│  │ schedule │           │ exec_dt  │          │  try_number││
│  │ ser_dag  │           │ run_type │          │  duration  ││
│  └──────────┘           └──────────┘          └─────┬──────┘│
│                                                     │       │
│                                     ┌───────────────┼───┐   │
│                                     │               │   │   │
│                              ┌──────▼─────┐  ┌──────▼───┐  ││
│                              │    xcom    │  │   log    │  ││
│                              │  task_id   │  │  task_id │  ││
│                              │  key/value │  │  event   │  ││
│                              └────────────┘  └──────────┘  ││
│                                                              │
│  ┌──────────┐     ┌──────────┐     ┌──────────┐            │
│  │ variable │     │connection│     │   pool   │            │
│  └──────────┘     └──────────┘     └──────────┘            │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 直接查询元数据库

**步骤目标**：通过 SQL 直接查询元数据库，理解核心表结构。

```bash
# 进入 PostgreSQL 容器
docker exec -it airflow-postgres-1 psql -U airflow -d airflow
```

**查询 1：查看所有 Dag 及其状态**

```sql
SELECT 
    dag_id,
    is_paused,
    is_active,
    schedule_interval,
    next_dagrun,
    last_parsed_time
FROM dag
WHERE is_active = true
ORDER BY dag_id;
```

**查询 2：过去 7 天各 Dag 的成功率**

```sql
SELECT 
    dag_id,
    COUNT(*) AS total_runs,
    SUM(CASE WHEN state = 'success' THEN 1 ELSE 0 END) AS success_runs,
    SUM(CASE WHEN state = 'failed' THEN 1 ELSE 0 END) AS failed_runs,
    ROUND(100.0 * SUM(CASE WHEN state = 'success' THEN 1 ELSE 0 END) / COUNT(*), 1) AS success_rate
FROM dag_run
WHERE start_date >= CURRENT_DATE - INTERVAL '7 days'
GROUP BY dag_id
ORDER BY success_rate DESC;
```

**查询 3：最耗时的 Task（Top 10）**

```sql
SELECT 
    dag_id,
    task_id,
    execution_date,
    start_date,
    end_date,
    ROUND(EXTRACT(EPOCH FROM (end_date - start_date)), 1) AS duration_seconds
FROM task_instance
WHERE state = 'success'
  AND start_date >= CURRENT_DATE - INTERVAL '7 days'
ORDER BY duration_seconds DESC
LIMIT 10;
```

**查询 4：XCom 存储量统计**

```sql
SELECT 
    dag_id,
    task_id,
    COUNT(*) AS xcom_count,
    PG_SIZE_PRETTY(SUM(OCTET_LENGTH(value::text))) AS total_size
FROM xcom
GROUP BY dag_id, task_id
ORDER BY SUM(OCTET_LENGTH(value::text)) DESC
LIMIT 10;
```

**查询 5：各 Pool 的槽位使用情况**

```sql
SELECT 
    pool,
    slots,
    COUNT(*) FILTER (WHERE state IN ('running', 'queued')) AS used_slots,
    slots - COUNT(*) FILTER (WHERE state IN ('running', 'queued')) AS free_slots
FROM task_instance ti
JOIN slot_pool sp ON ti.pool = sp.pool
WHERE ti.state IN ('running', 'queued')
GROUP BY pool, slots;
```

### 3.3 元数据清理策略

```bash
# Airflow 内置的 db clean 命令
docker exec airflow-scheduler-1 airflow db clean \
  --clean-before-timestamp 2024-12-01T00:00:00 \
  --tables task_instance,dag_run,log,xcom \
  --dry-run  # 先试运行，看看会删多少

# 确认后去掉 --dry-run 实际执行
docker exec airflow-scheduler-1 airflow db clean \
  --clean-before-timestamp 2024-12-01T00:00:00 \
  --tables task_instance,dag_run,log,xcom

# 设置定期清理任务（示例 CronJob）
# 每天凌晨 3 点清理 90 天前的数据
```

**自定义清理 Dag**：

```python
from airflow.sdk import DAG
from airflow.sdk.operators.bash import BashOperator
from datetime import datetime

with DAG(
    dag_id="db_maintenance",
    schedule="0 3 * * 0",  # 每周日凌晨 3 点
    start_date=datetime(2025, 1, 1),
    catchup=False,
    tags=["maintenance"],
) as dag:

    clean_xcom = BashOperator(
        task_id="clean_xcom",
        bash_command="""
            airflow db clean \
              --clean-before-timestamp "$(date -d '90 days ago' -Iseconds)" \
              --tables xcom \
              --skip-archive
        """,
    )

    clean_ti = BashOperator(
        task_id="clean_task_instances",
        bash_command="""
            airflow db clean \
              --clean-before-timestamp "$(date -d '120 days ago' -Iseconds)" \
              --tables task_instance,dag_run,log \
              --skip-archive
        """,
    )

    clean_xcom >> clean_ti
```

### 3.4 SQLAlchemy Session 管理规则

在 Airflow Core 中操作数据库的核心规则：

```python
# ✅ 正确：使用 with 语句 + session 参数
def my_function(*, session: Session):
    query = select(DagModel).where(DagModel.dag_id == "my_dag")
    result = session.execute(query).scalar_one()
    return result
    # 不调用 session.commit()——由上层统一提交

# ❌ 错误：函数内调用 commit
def my_function(*, session: Session):
    session.execute(update(...))
    session.commit()  # 绝对不要这样做！
```

---

## 4 项目总结

### 核心表职责汇总

| 表名 | 职责 | 关注指标 |
|------|------|---------|
| `dag` | Dag 定义与元数据 | is_active、is_paused |
| `dag_run` | Dag 每次运行实例 | state、execution_date、run_type |
| `task_instance` | Task 每次执行实例 | state、duration、try_number |
| `xcom` | 任务间数据传递 | value 大小、清理周期 |
| `log` | 任务日志元数据 | 文件大小、清理周期 |

### 数据库性能优化清单

1. **定期清理**：`xcom` 和 `log` 表最容易膨胀，建议 90 天清理一次
2. **索引检查**：确保 `task_instance(state, start_date)`、`dag_run(execution_date)` 有索引
3. **连接池配置**：PostgreSQL 用 PGBouncer，连接数 = Worker 数 × 2
4. **监控慢查询**：开启 PostgreSQL 的 `log_min_duration_statement`

### 思考题

1. 如果你需要统计"每个 Dag 在过去 30 天中，每天的平均执行时长"，你会写什么样的 SQL？提示：涉及 `task_instance` 表按 `dag_id` 和 `DATE(start_date)` 分组。
2. 元数据库的 `dag` 表中有一个 `serialized_dag` 字段（JSON 类型）。为什么 Airflow 要存序列化后的 Dag，而不是每次都去读 Dag 文件？

*（答案将在下一章揭晓）*

---

> **本章完成**：你已理解 Airflow 元数据库的核心结构和维护方法。下一章将深入 Dag 的"消化系统"——Dag 文件处理与序列化机制。
