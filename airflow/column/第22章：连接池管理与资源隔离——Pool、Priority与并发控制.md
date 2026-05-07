# 第22章：连接池管理与资源隔离——Pool、Priority与并发控制

## 1 项目背景

某数据中台团队维护着 200+ 条 Dag，覆盖从 MySQL 归档、Hive 分区计算到第三方 API 推送的全链路。月初某天，业务方投诉"报表数据延迟 4 小时"。排查发现：凌晨 2 点集中触发了 60 条 Dag，每条 Dag 的末尾都调用同一个第三方支付接口做对账。该接口的 SLA 明确规定——**最大并发不得超过 5 个请求**，一旦超过就会返回 429 限流。结果当天凌晨同时涌入 200+ 个 TaskInstance 争抢这个接口，大量任务被限流后不断重试，重试又产生新请求，最终形成"雪崩"——所有调用该接口的 Task 全部失败。

团队紧急加了重试上限和指数退避，但治标不治本。更棘手的是：MySQL 归档任务（本应独占连接池）和 Hive 查询任务（本应通过队列串行化）挤在同一个 `default_pool` 中，彼此争抢槽位，导致一个慢查询就能拖慢几十个不相干的任务。

这些问题指向同一个根因：**缺少层次化的资源隔离与并发控制**。Airflow 的 Pool、Priority Weight 和三重并发上限（parallelism / max_active_tasks / max_active_runs）正是为解决这类问题设计的。本章将从源码角度剖析这些机制的工作原理，并构建一个多资源池隔离的实战方案。

> **技术映射**：如果把 Airflow 比作一座繁忙的机场，Pool 就是航空公司的值机柜台——每个柜台（槽位）同一时刻只能服务一位旅客（TaskInstance）；Priority Weight 决定旅客的优先登机顺序；而 parallelism 是整座机场的跑道总数。

---

## 2 项目设计

**小胖**（看着 Admin → Pools 页面里 `default_pool` 的 128 slots）："128 个槽位还不够用？我们有那么多任务吗？"

**大师**："你看一下页面上的 `used_slots`——现在显示 128/128，也就是说所有槽位都被占满了。但这 128 个槽位里，可能有 80 个被 Hive 慢查询占着（每条跑 20 分钟），30 个被 Sensor 的 reschedule 模式反复申请/释放，只剩 18 个给 MySQL 归档和 API 调用争抢。表面上槽位数很多，实际上不同系统间的任务在互相踩踏。"

**小白**："那为什么不能给每个系统单独分配槽位？Pool 机制不就是干这个的吗？"

**大师**："对。Pool 的本质是一个**带计数的命名槽位组**。你在 `airflow-core/src/airflow/models/pool.py:75` 看到——"

```python
class Pool(Base):
    __tablename__ = "slot_pool"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pool: Mapped[str | None] = mapped_column(String(256), unique=True)
    slots: Mapped[int] = mapped_column(Integer, default=0)  # -1 表示无限
    description: Mapped[str | None] = mapped_column(Text)
    include_deferred: Mapped[bool] = mapped_column(Boolean, nullable=False)
```

"`slots` 字段就是柜台窗口数。设为 -1 表示无限槽位（float('inf')），设为 0 则禁止任何任务使用该 Pool。`include_deferred` 控制 deferred 状态的任务是否计入已占用槽位——这对 Deferrable Operator 场景至关重要。"

**小胖**："那调度器是怎么判断'槽位够不够'的？"

**大师**："调度器在决定是否把一个 SCHEDULED 状态的 TaskInstance 提交给 Executor 前，会经过一条检查链，定义在 `airflow-core/src/airflow/ti_deps/dependencies_deps.py:64`——

```python
SCHEDULER_QUEUED_DEPS = {
    RunnableExecDateDep(),       # 1. 执行日期是否就绪
    ValidStateDep(QUEUEABLE_STATES),  # 2. 当前状态是否允许排队
    DagTISlotsAvailableDep(),    # 3. Dag 的 max_active_tasks 是否达上限
    TaskConcurrencyDep(),        # 4. 单 Task 的并发是否达上限
    PoolSlotsAvailableDep(),     # 5. Pool 是否有剩余槽位
    DagrunRunningDep(),          # 6. DagRun 是否在 running 状态
    DagUnpausedDep(),            # 7. Dag 是否被暂停
    ExecDateAfterStartDateDep(), # 8. 执行日期是否晚于 start_date
    TaskNotRunningDep(),         # 9. 同一 TaskInstance 是否已 running
}
```

其中 `PoolSlotsAvailableDep`（`airflow-core/src/airflow/ti_deps/deps/pool_slots_available_dep.py:29`）的核心逻辑是：

```python
open_slots = pool.open_slots(session=session)
if ti.state in pool.get_occupied_states():
    open_slots += ti.pool_slots  # 重新计算时加回自己占的槽位

if open_slots <= (ti.pool_slots - 1):
    yield self._failing_status(
        reason=f"Not scheduling since there are {open_slots} open slots "
               f"in pool {pool_name} and require {ti.pool_slots} pool slots"
    )
```

**小白**："有意思——它检查的是 `open_slots <= (ti.pool_slots - 1)` 而不是 `open_slots < ti.pool_slots`，为什么减 1？"

**大师**："这是正确的边界条件。假设 Pool 只有 1 个槽位，当前 open_slots = 1，ti.pool_slots = 1。如果条件是 `open_slots < 1`，那小于 1 才失败——但 1 不小于 1，所以通过。那任务就被错误地调度了，因为仅有的 1 个槽位已被自己占用（在 `occupied_states` 中），减去自己的 slot 后实际可用为 0。换成 `1 <= (1 - 1)` → `1 <= 0` → False，任务正确地被拒绝。"

**小胖**："那 Priority Weight 又是怎么影响排队的？如果 Pool 槽位有限，谁先进去？"

**大师**："在调度器的查询构建阶段（`airflow-core/src/airflow/jobs/scheduler_job_runner.py:579-607`），所有 SCHEDULED 状态的 TaskInstance 在被拉取时就按 `priority_weight DESC` 排序：

```python
query = (
    select(TI)
    .join(TI.dag_run)
    .join(TI.dag_model)
    .where(TI.state == TaskInstanceState.SCHEDULED)
    .where(DR.state == DagRunState.RUNNING)
    .where(~DM.is_paused)
    .order_by(-TI.priority_weight, DR.logical_date, TI.map_index)
)
```

然后在 Executor 侧（`airflow-core/src/airflow/executors/base_executor.py:404`），workload 队列也按 `priority_weight` 降序排列。所以高权重的任务不仅在调度器拉取时排在前列，在 Executor 内部队列中也优先被分配 Worker。"

**小白**："Priority Weight 除了直接设死一个 int 值，还有什么玩法？"

**大师**："Airflow 内置了三种权重策略，定义在 `airflow-core/src/airflow/task/priority_strategy.py:96-107`：

| 策略 | WeightRule 常量 | 计算方式 |
|------|----------------|---------|
| `_AbsolutePriorityWeightStrategy` | `ABSOLUTE` | 直接使用 `task.priority_weight`（默认 1） |
| `_DownstreamPriorityWeightStrategy` | `DOWNSTREAM` | 自身权重 + 所有下游任务的权重之和 |
| `_UpstreamPriorityWeightStrategy` | `UPSTREAM` | 自身权重 + 所有上游任务的权重之和 |

Downstream 策略适用一个直觉：**离 Dag 终点越近的任务越优先执行**——因为它们之后要释放的依赖链最短。Upstream 策略则相反：优先让上游跑起来，这样可以更快触发更多下游任务进入可调度状态。你还可以通过 Plugins 机制注册自定义策略（参考 `airflow-core/src/airflow/example_dags/plugins/decreasing_priority_weight_strategy.py`）。**

> **技术映射**：Pool = 银行柜台窗口；Priority Weight = VIP 客户的优先级号码；parallelism = 全行同时在服务的客户总数上限。

---

## 3 项目实战

### 3.1 环境准备

确保 Airflow 3.x 环境正常运行，元数据库为 SQLite（默认）或 PostgreSQL。本次实战使用 Airflow SDK 编写 Dag，所有操作通过 Web UI 和 CLI 完成。

### 3.2 创建独立资源池

**步骤目标**：为 MySQL、Hive、API 三类资源分别创建独立的 Pool，实现资源隔离。

首先通过 CLI 创建三个 Pool：

```bash
# 创建 MySQL 池——5 个并发连接（生产环境典型值）
airflow pools set mysql_pool 5 "MySQL 数据库连接池"

# 创建 Hive 池——3 个并发查询（HiveServer2 限制）
airflow pools set hive_pool 3 "Hive 查询资源池"

# 创建 API 池——2 个并发请求（第三方接口 SLA）
airflow pools set api_pool 2 "第三方 API 调用池"

# 查看所有 Pool 的槽位统计
airflow pools list
```

也可以通过 Python 代码在 Dag 中自动创建：

```python
"""
以编程方式创建/更新 Pool，适用于 CI/CD 或 Dag init 阶段。
注意：Pool.create_or_update_pool 会自动 commit，不要在 session 内调用。
"""
from airflow.models.pool import Pool

# 创建或更新 Pool（幂等操作）
Pool.create_or_update_pool(
    name="mysql_pool",
    slots=5,
    description="MySQL 数据库连接池——限制并发连接数",
    include_deferred=False,  # deferred 任务不计入已占用槽位
)

Pool.create_or_update_pool(
    name="hive_pool",
    slots=3,
    description="Hive 查询资源池——避免 HiveServer2 过载",
    include_deferred=False,
)

Pool.create_or_update_pool(
    name="api_pool",
    slots=2,
    description="第三方支付接口调用池——遵守 SLA 限流",
    include_deferred=False,
)
```

登录 Web UI → Admin → Pools，确认三个 Pool 均已创建，slots 分别为 5、3、2。

### 3.3 编写多 Pool 资源隔离 Dag

**步骤目标**：构建一条 Dag，将 MySQL 归档、Hive 聚合、API 推送分配到各自的 Pool 中运行。

```python
"""
多资源池隔离实战——模拟数据中台 ETL 流程。

Dag 结构：
  mysql_archive（mysql_pool, 5 slots）
    → hive_agg（hive_pool, 3 slots）
      → api_push（api_pool, 2 slots）

每个阶段创建多于 Pool 槽位数的并行 Task，观察调度器的排队行为。
"""
from airflow.sdk import DAG
from airflow.sdk.operators.python import PythonOperator
from datetime import datetime, timedelta
import time
import random


def _mysql_archive(task_id: str, **context):
    """模拟 MySQL 归档操作——每个任务占用 1 个 mysql_pool 槽位"""
    print(f"[{task_id}] 开始 MySQL 归档，连接池槽位占用中...")
    time.sleep(random.uniform(5, 15))  # 模拟 SQL 执行
    print(f"[{task_id}] MySQL 归档完成，释放槽位")


def _hive_aggregate(task_id: str, **context):
    """模拟 Hive 聚合查询——重量级操作，需要较长时间"""
    ti = context["ti"]
    pool_name = ti.pool
    print(f"[{task_id}] 开始 Hive 聚合（Pool: {pool_name}），等待 HiveServer2 资源...")
    time.sleep(random.uniform(10, 25))  # 模拟 MapReduce/Tez 作业
    print(f"[{task_id}] Hive 聚合完成，释放槽位")


def _api_push(task_id: str, **context):
    """模拟第三方 API 推送——严格遵守并发限制"""
    ti = context["ti"]
    priority = ti.priority_weight
    print(f"[{task_id}] 调用第三方支付对账接口（Priority: {priority}），严格遵守 ≤2 并发...")
    time.sleep(random.uniform(3, 8))  # 模拟 HTTP 请求
    print(f"[{task_id}] API 调用完成，释放槽位")


with DAG(
    dag_id="multi_pool_isolation_demo",
    schedule=None,
    start_date=datetime(2025, 1, 1),
    max_active_tasks=16,         # 单 Dag 最多同时运行 16 个 Task
    max_active_runs=1,            # 同一时刻只允许 1 个 DagRun
    catchup=False,
    tags=["demo", "pool", "concurrency"],
) as dag:

    # ============ 阶段 1: MySQL 归档（8 个并行任务，Pool 限制为 5）============
    mysql_tasks = []
    for i in range(8):
        task = PythonOperator(
            task_id=f"mysql_archive_{i:02d}",
            python_callable=_mysql_archive,
            op_kwargs={"task_id": f"mysql_{i:02d}"},
            pool="mysql_pool",         # ← 使用 MySQL 资源池
            pool_slots=1,              # 每个任务占用 1 个槽位
            priority_weight=10,        # 较高优先级——归档任务必须先完成
        )
        mysql_tasks.append(task)

    # ============ 阶段 2: Hive 聚合（6 个并行任务，Pool 限制为 3）============
    hive_tasks = []
    for i in range(6):
        task = PythonOperator(
            task_id=f"hive_agg_{i:02d}",
            python_callable=_hive_aggregate,
            op_kwargs={"task_id": f"hive_{i:02d}"},
            pool="hive_pool",          # ← 使用 Hive 资源池
            pool_slots=1,
            priority_weight=5,         # 中等优先级
        )
        hive_tasks.append(task)

    # ============ 阶段 3: API 推送（4 个并行任务，Pool 限制为 2）============
    api_tasks = []
    for i in range(4):
        task = PythonOperator(
            task_id=f"api_push_{i:02d}",
            python_callable=_api_push,
            op_kwargs={"task_id": f"api_{i:02d}"},
            pool="api_pool",           # ← 使用 API 资源池
            pool_slots=1,
            priority_weight=1,         # 最低优先级——API 推送可以最后完成
        )
        api_tasks.append(task)

    # 设置依赖链: mysql → hive → api
    for mt in mysql_tasks:
        for ht in hive_tasks:
            mt >> ht
    for ht in hive_tasks:
        for at in api_tasks:
            ht >> at
```

**运行与观察**：

```bash
# 触发 Dag
airflow dags trigger multi_pool_isolation_demo

# 实时查看 Pool 槽位状态
watch -n 2 'airflow pools list'
```

进入 Web UI → Admin → Pools，观察 `used_slots` 的变化：
- `mysql_pool`：used_slots 峰值 5/5（8 个 Task 中最多 5 个同时 running）
- `hive_pool`：used_slots 峰值 3/3（6 个 Task 中最多 3 个同时 running）
- `api_pool`：used_slots 峰值 2/2（4 个 Task 中最多 2 个同时 running）

Grid 视图中可以清晰看到：每个阶段的并行度严格受对应 Pool 的 slots 限制，超出部分排队等待（queued 状态）。

### 3.4 Priority Weight 排队顺序验证

**步骤目标**：通过设置不同的 priority_weight，观察高优先级任务是否先被调度。

在上面的 Dag 中，三种任务已经设置了不同优先级：`mysql(10) > hive(5) > api(1)`。更进一步，在同一个 Pool 内创建竞争：

```python
"""
Priority Weight 竞争实验——同一个 Pool 内 10 个任务，
1 个 weight=100，其余 weight=1，观察谁先跑。
"""
with DAG(
    dag_id="priority_weight_demo",
    schedule=None,
    start_date=datetime(2025, 1, 1),
    max_active_tasks=10,
    tags=["demo", "priority"],
) as dag:

    # 首先创建 VIP 任务
    vip_task = PythonOperator(
        task_id="vip_task",
        python_callable=lambda **ctx: (time.sleep(2), print("VIP 任务完成！")),
        pool="default_pool",
        pool_slots=1,
        priority_weight=100,  # ← 高权重
    )

    # 创建 9 个普通任务
    for i in range(9):
        normal = PythonOperator(
            task_id=f"normal_task_{i}",
            python_callable=lambda i=i, **ctx: print(f"普通任务 {i} 完成"),
            pool="default_pool",
            pool_slots=1,
            priority_weight=1,  # ← 默认权重
        )
        vip_task >> normal  # 必须等 VIP 完成后才执行，这不是 priority 的效果
```

**正确地验证 Priority**：在同一个 Pool 内，先手动触发一批低权重任务占满槽位，再提交高权重任务——观察高权重任务是否"插队"到队列头部。在 Scheduler 日志中搜索 `priority_weight` 关键词：

```bash
docker compose logs airflow-scheduler-1 | grep "priority_weight" | tail -20
```

你会看到类似这样的日志——TaskInstance 按 priority_weight 降序排列：

```
[2025-01-15 10:00:05] <TI: priority_weight_demo/vip_task/...> priority_weight=100 ...
[2025-01-15 10:00:05] <TI: priority_weight_demo/normal_task_0/...> priority_weight=1 ...
```

### 3.5 三层并发控制全景测试

**步骤目标**：对比 parallelism、max_active_tasks、max_active_runs 三个参数的实际效果。

```python
"""
三层并发控制测试：
  - 全局 parallelism = 32（airflow.cfg）
  - Dag 级 max_active_tasks = 4
  - Dag 级 max_active_runs = 1

创建 20 个并行 Task，预期同时 running 的 Task ≤ min(32, 4) = 4 个
"""
with DAG(
    dag_id="concurrency_three_layer_demo",
    schedule=None,
    start_date=datetime(2025, 1, 1),
    max_active_tasks=4,       # 第 2 层：单 Dag 并发上限
    max_active_runs=1,        # 第 3 层：单 Dag 同时跑几个 DagRun
    concurrency=4,            # 历史遗留（同 max_active_tasks），3.x 已 deprecated
    tags=["demo", "concurrency"],
) as dag:

    from airflow.sdk.operators.bash import BashOperator

    for i in range(20):
        BashOperator(
            task_id=f"task_{i:02d}",
            bash_command=f'echo "Task {i} running at $(date)" && sleep 10',
        )
```

**关键实验**：

1. **max_active_runs 测试**：将 `max_active_runs` 设为 1，手动回填 3 天的历史数据：
```bash
airflow dags backfill concurrency_three_layer_demo \
    --start-date 2025-01-01 \
    --end-date 2025-01-03
```
观察只有 1 个 DagRun 在 Running 状态，其余 2 个排队等待。

2. **parallelism 瓶颈测试**：临时将 `airflow.cfg` 中 `parallelism = 2`，重启 Scheduler，触发大量任务——全局最多 2 个 Task 同时 running。

### 3.6 槽位泄漏检测与监控

**步骤目标**：学习如何发现和修复 Pool 槽位泄漏。

**槽位泄漏场景**：当 Worker 异常退出（OOM Killed、网络分区），TaskInstance 状态卡在 Running，对应的 Pool 槽位永远不会被释放。

```python
"""
Pool 槽位泄漏检测脚本——查找长时间 Running 但已无心跳的 TaskInstance。
"""
from airflow.models.taskinstance import TaskInstance
from airflow.utils.session import provide_session
from airflow.utils.state import TaskInstanceState
from sqlalchemy import select
from datetime import datetime, timedelta


@provide_session
def detect_pool_slot_leaks(session=None):
    """检测卡在 running 状态超过 30 分钟但最后心跳超过 5 分钟的 Task"""
    from airflow.models.pool import Pool

    threshold = datetime.utcnow() - timedelta(minutes=30)
    heartbeat_threshold = datetime.utcnow() - timedelta(minutes=5)

    stale_tis = session.scalars(
        select(TaskInstance)
        .where(
            TaskInstance.state == TaskInstanceState.RUNNING,
            TaskInstance.start_date < threshold,
            # pid 是 Worker 进程 ID——进程已死但状态未更新
        )
    ).all()

    leaks_by_pool = {}
    for ti in stale_tis:
        pool_name = ti.pool
        if pool_name not in leaks_by_pool:
            leaks_by_pool[pool_name] = 0
        leaks_by_pool[pool_name] += ti.pool_slots

    for pool_name, leaked_slots in leaks_by_pool.items():
        pool = Pool.get_pool(pool_name, session=session)
        if pool:
            actual_used = pool.occupied_slots(session=session)
            print(
                f"Pool [{pool_name}]: 疑似泄漏 {leaked_slots} 槽位，"
                f"当前已占用 {actual_used}/{pool.slots}"
            )

    return leaks_by_pool


if __name__ == "__main__":
    # 将该脚本放在 dev/ 目录下运行
    leaks = detect_pool_slot_leaks()
    if leaks:
        print("⚠️  检测到槽位泄漏！建议手动清理或重启 Worker。")
    else:
        print("✅ Pool 槽位状态正常。")
```

**手动清理泄漏槽位**：

```bash
# 将卡死状态的 Task 标记为 failed，释放 Pool 槽位
airflow tasks failed \
    --task-id <task_id> \
    --dag-id <dag_id> \
    --run-id <run_id> \
    --map-index -1
```

---

## 4 项目总结

### 4.1 Pool 机制全景

| 维度 | 说明 | 源码位置 |
|------|------|---------|
| 数据表 | `slot_pool` | `airflow-core/src/airflow/models/pool.py:78` |
| 槽位字段 | `slots`: 总槽位数，-1 表示无限 | `pool.py:83` |
| 占用统计 | `slots_stats()` 按状态聚合 running/queued/deferred/scheduled | `pool.py:167-237` |
| 可用计算 | `open_slots = total - running - queued [- deferred]` | `pool.py:233-236` |
| 状态集合 | `EXECUTION_STATES` + 可选 `DEFERRED` | `pool.py:275-280` |
| 调度检查 | `PoolSlotsAvailableDep._get_dep_statuses()` | `ti_deps/deps/pool_slots_available_dep.py:36-84` |

### 4.2 三层并发控制对比

| 层次 | 参数 | 作用域 | 默认值 | 典型场景 |
|------|------|-------|--------|---------|
| 全局 | `parallelism` (airflow.cfg) | 所有 Dag 的所有 Task | 32 | 控制整个集群的 CPU/内存总量 |
| Dag 级 | `max_active_tasks` (Dag 参数) | 单个 Dag 的并发 Task | 16 | 防止单 Dag 占用全部资源 |
| DagRun 级 | `max_active_runs` (Dag 参数) | 单个 Dag 的并发 DagRun | 16 | 防止回填/补数据时创建过多 DagRun |
| Task 级 | `task_concurrency` (Task 参数) | 单个 Task 的并发实例 | 无限 | 限制同一 Task 跨 DagRun 的并发数 |
| Pool 级 | `Pool.slots` (Pool 配置) | 特定资源的并发访问 | 128 (default_pool) | 限制 MySQL/Hive/API 等外部资源 |

### 4.3 适用场景

- **多租户/多团队共享集群**：按团队分配独立 Pool，防止 A 团队的任务拖垮 B 团队
- **外部 API 限流**：为每个第三方接口分配独立 Pool，slots = API 的 QPS 上限
- **数据库连接池保护**：MySQL/Hive/Presto 各自独立 Pool，防止连接泄漏互相影响
- **Sensor 隔离**：为 Sensor 单独分配 Pool，避免它们的 reschedule 循环挤占业务任务槽位
- **延迟敏感任务加速**：高 priority_weight 的任务在 Executor 队列中插队执行

### 4.4 不适用场景

- **需要跨 TaskInstance 的精确资源配额**（如"每小时最多 100 次调用"）→ Pool 只有并发数限制，无时间窗口计数
- **需要任务级别的 CPU/内存隔离** → Airflow 本身不隔离 Worker 进程资源，需配合 K8s resource limits

### 4.5 注意事项与踩坑经验

| 陷阱 | 症状 | 根因 | 解决方案 |
|------|------|------|---------|
| `default_pool` 满 | 新 Task 全部 queued | 所有 Task 默认使用 default_pool | 按资源类型拆分 Pool |
| Sensor 槽位占用 | 大量 queued 但没有 running | reschedule Sensor 每次 poke 都申请 Pool 槽位 | 为 Sensor 单独分配大 slots 的 Pool |
| Deferred 计入槽位 | Triggerer 处理的 Task 占着 Pool 槽位 | `include_deferred=True` | 评估是否需要对 Triggerer 做槽位限制；通常设 False |
| Worker 异常退出 | Pool used_slots 不下降 | Task 状态卡在 Running | 设置 heartbeat 超时 + 定时巡检脚本 |
| max_active_tasks 与 Pool slots 冲突 | 单 Dag 的 running 数低于 Pool 剩余 | Dag 层面的 max_active_tasks 先于 Pool 检查触发 | `max_active_tasks` 设置 ≥ 该 Dag 各 Pool 槽位数之和 |
| 优先级反转 | 低权重要务先执行 | 高权重任务的依赖未就绪 | 结合 Downstream 权重策略 + trigger_rule |

### 4.6 Pool 监控指标建议

```bash
# 通过 StatsD/Prometheus 上报 Pool 状态
# Airflow 自动为每个 Pool 上报以下指标：
#   pool.open_slots.<pool_name>
#   pool.used_slots.<pool_name>
#   pool.running_slots.<pool_name>
#   pool.queued_slots.<pool_name>
#   pool.starving_tasks.<pool_name>

# 关键告警规则：
#   1. pool.open_slots.<name> = 0 持续 > 5 分钟 → P1 告警
#   2. pool.starving_tasks.<name> > 0 持续 > 10 分钟 → P2 告警
#   3. 任何 Task 的 pool 参数指向不存在的 Pool → P3 告警
```

### 4.7 最佳实践速查

1. **新建项目第一步**：创建专用 Pool，不要使用 `default_pool`
2. **slot 数黄金法则**：Pool slots = 目标资源最大并发数 × 0.8（留 20% 余量）
3. **include_deferred**：Triggerer 处理的 Deferrable Task 通常设 `False`，避免占死 Pool 槽位
4. **Priority Weight**：核心业务链路设置 weight ≥ 10，非关键路径设 1~5
5. **max_active_tasks**：设置为该 Dag 使用的所有 Pool slots 之和的 1.2 倍，避免反向限制
6. **定期审计**：每周检查一次 `airflow pools list`，确认 used_slots 未异常偏高

### 思考题

1. 你有一个 CeleryExecutor 集群，8 个 Worker 每个 16 并发（共 128 worker slots），`parallelism=64`，创建了一个 `api_pool`（slots=3），当前有 100 个 SCHEDULED 状态的 TaskInstance 都指向 `api_pool`，且上游依赖已全部就绪。请问最终同时处于 RUNNING 状态的 TaskInstance 最多有几个？

2. 设计一个 `PriorityWeightStrategy` 的插件实现，使得调度器在 Pool 槽位紧张时，优先调度"运行时间最短的历史任务"（即根据 TaskInstanceHistory 中的 duration 字段），而不是简单的 static priority_weight。这种策略在实际生产中有什么优缺点？

*（答案将在后续章节揭晓）*

---

> **本章完成**：你已经掌握了 Airflow 连接池管理与资源隔离的完整机制。从 Pool 槽位的计费模型到调度器的饥饿检测，再到三层并发控制的配合使用——这些知识将帮助你构建高可用、可隔离的生产级调度系统。下一章我们将深入 Executor 对比，探讨 LocalExecutor、CeleryExecutor 与 KubernetesExecutor 的选型决策。
