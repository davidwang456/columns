# 第26章：Executors进阶——Celery深度配置与调优

## 1 项目背景

某金融科技公司团队从 LocalExecutor 迁移到 CeleryExecutor 已有三个月，日均调度 500+ DagRun、3000+ TaskInstance。起初一切平稳，但随着业务接入量翻倍，一系列深层问题开始浮现。

**事件一：消息积压风暴**。某天凌晨回填 180 天历史数据，Scheduler 瞬间向 Redis Broker 注入了 8000 条任务消息，3 个 Worker 节点按默认配置（prefetch_multiplier=4）疯狂预取。结果：先抢到任务的 Worker 内存 OOM 被 kill，未确认的消息重新入队，又被另一个 Worker 抢走……形成"消息乒乓球"效应，整个集群吞吐量降到零。

**事件二：Worker 漂移**。运维发现一台 Worker 的 CPU 持续 90%，另一台只有 10%，而所有 Task 都是同构的 ETL 任务。排查发现：Celery 默认的 Round-Robin 路由在消息量不大时表现正常，但在高并发下，`prefetch_multiplier` 和 `worker_concurrency` 的组合导致"忙者越忙、闲者越闲"的负载失衡——这正是 Celery 调优中经典的"预取倾斜"问题。

**事件三：连接泄漏**。监控平台告警：Redis 连接数从预期的 200 飙升至 8000。事后发现，Result Backend 配置不当，每个 Worker 的子进程在每次状态查询时都创建新的 Redis 连接且没有复用，加上 Flower Dashboard 的连接池未设上限，最终耗尽 Redis 的 `maxclients`。

> 这些问题指向同一个本质：**CeleryExecutor 不是"开了就能用"的分布武系統，它需要在 Broker、Worker、Result Backend 三个层面做深度配置与调优。** 本章将基于 Airflow Celery Provider 的源码（`providers/celery/src/airflow/providers/celery/executors/`）逐一剖析这些机制。

---

## 2 项目设计

**小胖**（盯着 Redis 的 `KEYS *` 输出里密密麻麻的 celery 前缀 key）："Celery 不就是个分布式任务队列吗？往 Redis 塞消息、Worker 取出来跑、跑完告诉 Scheduler。为啥大师你刚才说的那些故障我连听都听不懂？"

**大师**："你这个三句话概括对 Celery 的基本原理没错，但漏了最关键的四个字——'可靠性模型'。Celery 的可靠性由三个维度共同决定：Broker 的消息持久化与可见性超时、Worker 的任务预取与确认策略、Result Backend 的状态查询效率。任何一个维度配置不当，都会在规模起来后暴露问题。"

**小白**："先从 Broker 说起吧。Redis 和 RabbitMQ 到底怎么选？我看运维都喜欢 Redis 因为简单，但 RabbitMQ 的文档总说自己是'真正可靠的消息队列'。"

**大师**："两个 Broker 在 Airflow 的 CeleryExecutor 场景下的差异可以归纳为一张表：

| 维度 | Redis | RabbitMQ |
|------|-------|----------|
| 消息确认 | 无原生 ack，依赖 visibility_timeout | 原生 ack 机制，Worker 崩溃后消息自动重入队 |
| 持久化 | RDB/AOF 快照，可能丢消息 | 队列持久化 + 消息持久化，不丢消息 |
| 运维复杂度 | 极低，一个二进制即可 | 需要 Erlang 环境，集群配置复杂 |
| 性能 | 单进程 10 万 QPS | 单节点约 2 万 QPS（但通过多个队列可扩展） |
| Airflow 适配 | 需要设置 visibility_timeout（默认 24h） | 原生支持，无需额外配置 |

Airflow 源码中对 Redis Broker 的 visibility_timeout 处理在 `default_celery.py:39-66`——如果 Broker URL 是 `redis://`、`rediss://` 或 `sentinel://` 开头，且用户没有显式设置，它会自动赋予 86400 秒（24 小时）的默认值。这意味着：如果你的 Task 运行超过 24 小时，Redis 会认为消息超时并重新投递，导致同一任务被两个 Worker 同时执行。"

**小胖**："那 Redis Sentinel 高可用呢？生产环境总不能单点 Redis 吧？"

**大师**："Airflow 对 Redis Sentinel 的支持同样在 `default_celery.py` 中——它允许你在 `broker_transport_options` 中配置 `sentinel_kwargs`，Celery 会通过 Sentinel 自动发现主节点。但注意：Sentinel 只能解决高可用，不能解决消息可靠性的问题。如果你需要'一条消息都不能丢'，选 RabbitMQ 并开启 `confirm_publish`。"

**小白**："Worker 的 `prefetch_multiplier` 和 `task_acks_late` 这些参数是怎么相互影响的？"

**大师**："这是 Celery 调优中最容易踩坑的组合。先看 Airflow 的默认配置（`default_celery.py:105-111`）：

```python
config = {
    \"worker_prefetch_multiplier\": 1,   # 每个 Worker 进程预取 1 个任务
    \"task_acks_late\": True,            # 任务完成后才发送 ack
    \"task_track_started\": True,        # 跟踪 STARTED 状态
    \"worker_concurrency\": 16,          # 每个 Worker 16 个并发进程
}
```

这四个参数共同决定了 Worker 的行为：

- `worker_concurrency = 16`：每个 Worker 最多同时执行 16 个 Task
- `worker_prefetch_multiplier = 1`：每个 Worker 进程（共 16 个）预取 `1 × 1 = 1` 条消息，即整体同时持有的未确认消息最多 16 条
- `task_acks_late = True`：Task 执行**完成后**才确认消息，如果 Worker 崩溃，消息会重新入队

如果 `prefetch_multiplier` 设成默认的 4（Celery 原生默认值），那 16 个 Worker 进程会预取 64 条消息。一旦某个 Worker 节点崩溃，这 64 个任务全部需要重新投递——在 500+ TaskInstance 的高负载下，这就是'消息乒乓球'的根源。Airflow 的 Celery Provider 把默认值改成了 1，就是这个原因。"

**小胖**："那 Result Backend 为什么要用数据库而不是 Redis？"

**大师**："因为 Airflow 需要批量查询任务状态。看 `celery_executor_utils.py:479-574` 的 `BulkStateFetcher`——它是 CeleryExecutor 的状态查询引擎。它根据 Result Backend 的类型选择不同的批量查询策略：

- 如果是 `BaseKeyValueStoreBackend`（Redis）→ 用 `mget` 批量获取
- 如果是 `DatabaseBackend`（MySQL/PostgreSQL）→ 用 `SELECT ... WHERE task_id IN (...)` 单条 SQL
- 其他情况 → 用 `ProcessPoolExecutor` 逐个查询

Redis 的 `mget` 确实快，但它的数据会过期（默认 24 小时），而数据库后端可以持久保留。重要的是，Airflow 默认的 Result Backend 就是元数据库（`db+sql_alchemy_conn`），这保证了状态查询和 Scheduler 共享同一份连接池。"

> **技术映射**：Broker = 快递中转站（消息暂存）；Worker = 快递员（执行任务）；Prefetch = 快递员一次拿几个包裹（拿太多容易丢，拿太少效率低）；task_acks_late = 派件成功才发确认签名（防止包裹丢了没人知道）；Result Backend = 签收登记簿（记录每个包裹的派送结果）。

---

## 3 项目实战

### 3.1 环境准备

**目标**：用 Docker Compose 部署一套完整的高可用 CeleryExecutor 集群——3 台 Worker + Redis Sentinel（一主两从）+ Flower 监控 + PostgreSQL 元数据库。

**文件结构**：

```text
project/
├── docker-compose.yaml
├── config/
│   └── airflow.cfg
├── dags/
│   └── celery_stress_test.py
└── redis-sentinel/
    ├── sentinel.conf
    └── redis-slave.conf
```

**docker-compose.yaml**：

```yaml
x-airflow-common: &airflow-common
  image: apache/airflow:3.0.0
  environment: &airflow-env
    AIRFLOW__CORE__EXECUTOR: CeleryExecutor
    AIRFLOW__DATABASE__SQL_ALCHEMY_CONN: postgresql+psycopg2://airflow:airflow@postgres/airflow
    AIRFLOW__CELERY__BROKER_URL: sentinel://:26379/0;sentinel://:26380/0;sentinel://:26381/0
    AIRFLOW__CELERY__RESULT_BACKEND: db+postgresql+psycopg2://airflow:airflow@postgres/airflow
    AIRFLOW__CELERY__WORKER_CONCURRENCY: 8
    AIRFLOW__CELERY__WORKER_PREFETCH_MULTIPLIER: 1
    AIRFLOW__CELERY__TASK_ACKS_LATE: "True"
    AIRFLOW__CORE__PARALLELISM: 64
    # Redis Sentinel transport options
    AIRFLOW__CELERY_BROKER_TRANSPORT_OPTIONS: |
      {
        "master_name": "mymaster",
        "sentinel_kwargs": "{\\"password\\": \\"sentinelpass\\"}"
      }
  volumes:
    - ./dags:/opt/airflow/dags
    - ./config:/opt/airflow/config
  depends_on:
    postgres:
      condition: service_healthy

services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: airflow
      POSTGRES_PASSWORD: airflow
      POSTGRES_DB: airflow
    healthcheck:
      test: ["CMD", "pg_isready", "-U", "airflow"]
      interval: 5s
      retries: 5

  redis-master:
    image: redis:7-alpine
    command: redis-server --port 6379 --requirepass redispwd
    healthcheck:
      test: ["CMD", "redis-cli", "-a", "redispwd", "ping"]
      interval: 5s

  redis-slave-1:
    image: redis:7-alpine
    command: redis-server --port 6379 --requirepass redispwd --replicaof redis-master 6379 --masterauth redispwd
    depends_on:
      - redis-master

  redis-slave-2:
    image: redis:7-alpine
    command: redis-server --port 6379 --requirepass redispwd --replicaof redis-master 6379 --masterauth redispwd
    depends_on:
      - redis-master

  sentinel-1:
    image: redis:7-alpine
    command: >
      redis-server /etc/redis/sentinel.conf --sentinel
      --port 26379
    volumes:
      - ./redis-sentinel/sentinel.conf:/etc/redis/sentinel.conf
    depends_on:
      - redis-master

  scheduler:
    <<: *airflow-common
    command: airflow scheduler
    restart: always

  worker-1:
    <<: *airflow-common
    command: airflow celery worker --queues default,high_priority
    hostname: worker-1
    restart: always

  worker-2:
    <<: *airflow-common
    command: airflow celery worker --queues default --concurrency 12
    hostname: worker-2
    restart: always

  worker-3:
    <<: *airflow-common
    command: airflow celery worker --queues default,low_priority --concurrency 4
    hostname: worker-3
    restart: always

  flower:
    image: apache/airflow:3.0.0
    environment: *airflow-env
    command: airflow celery flower
    ports:
      - "5555:5555"
    depends_on:
      - scheduler

  webserver:
    <<: *airflow-common
    command: airflow webserver
    ports:
      - "8080:8080"
    restart: always
```

**redis-sentinel/sentinel.conf**：

```conf
sentinel monitor mymaster redis-master 6379 2
sentinel auth-pass mymaster redispwd
sentinel down-after-milliseconds mymaster 5000
sentinel failover-timeout mymaster 10000
sentinel parallel-syncs mymaster 1
```

**启动集群**：

```bash
# 初始化元数据库
docker compose run --rm scheduler airflow db migrate
# 创建管理员用户
docker compose run --rm scheduler airflow users create \
    --username admin --password admin --firstname Admin --lastname User \
    --role Admin --email admin@example.com
# 启动全部服务
docker compose up -d
```

### 3.2 airflow.cfg Celery 配置深度解析

**步骤目标**：逐项解析 `[celery]` 段的关键配置及其在源码中的生效路径。

```ini
[celery]
# === Broker 配置 ===
# Broker URL：支持 redis://、rediss://（SSL）、amqp://（RabbitMQ）、sqs://（AWS SQS）
# 源码：default_celery.py:54
BROKER_URL = redis://redis:6379/0

# Broker 连接在 Worker 启动时失败是否重试
# 源码：default_celery.py:115-117
broker_connection_retry_on_startup = True

# Celery 应用名称——用于标识不同的 Airflow 集群
# 源码：default_celery.py:132
CELERY_APP_NAME = airflow.executors.celery_executor

# === Worker 配置 ===
# 每个 Worker 的并发进程数（即 Worker 可同时执行的 Task 数）
# 源码：default_celery.py:123
WORKER_CONCURRENCY = 16

# 预取乘数：Worker 持有的未确认消息数 = concurrency × prefetch_multiplier
# Airflow 默认 1，避免高负载时消息分布不均
# 源码：default_celery.py:108
worker_prefetch_multiplier = 1

# 延迟确认：Task 执行完成后才发送 ack，防止 Worker 崩溃丢任务
# 源码：default_celery.py:109
task_acks_late = True

# 开启任务已启动状态追踪
# 源码：default_celery.py:112
task_track_started = True

# === Result Backend 配置 ===
# 不设置时自动使用 db+sql_alchemy_conn
# 源码：default_celery.py:77-85
RESULT_BACKEND = db+postgresql://airflow:airflow@postgres:5432/airflow

# Result Backend 的 SQLAlchemy 引擎选项（连接池配置）
# 源码：default_celery.py:120-122
result_backend_sqlalchemy_engine_options = {"pool_size": 20, "max_overflow": 30, "pool_recycle": 1800}

# === 超时与重试 ===
# Celery 操作超时时间（秒），用于 apply_async 和状态查询
# 源码：celery_executor_utils.py:95
OPERATION_TIMEOUT = 30.0

# 消息投递失败的最大重试次数
# 源码：celery_executor.py:156
task_publish_max_retries = 3

# === Broker Transport 选项 ===
[celery_broker_transport_options]
# Redis visibility_timeout：Worker 离线后 Broker 等待多久才重新分配未确认消息
# 源码：default_celery.py:57-66
visibility_timeout = 86400

# === 高级配置 ===
[celery]
# 状态同步并行度——Scheduler 查询 Celery 任务状态时使用的进程数
# 0 表示自动（CPU 核心数 - 1）
# 源码：celery_executor.py:148-150
SYNC_PARALLELISM = 0

# Worker 是否允许远程控制（如 revoke、ping）
worker_enable_remote_control = True

# SSL 配置（生产环境强烈建议开启）
SSL_ACTIVE = False
SSL_KEY = /etc/airflow/certs/client.key
SSL_CERT = /etc/airflow/certs/client.crt
SSL_CACERT = /etc/airflow/certs/ca.crt

# 额外的 Celery 原生配置（JSON 格式，会覆盖上面的默认值）
extra_celery_config = {}

# 自定义 Celery 配置字典的 Python 路径（高级用法）
celery_config_options =
```

### 3.3 Flower 监控 Dashboard 部署与实操

**步骤目标**：通过 Flower 实时监控 Celery 集群的任务吞吐量、Worker 负载和失败率。

**启动 Flower**（已包含在 docker-compose 中）：

```bash
# 独立启动
airflow celery flower

# 指定端口和访问控制
airflow celery flower --port=5555 --basic-auth=admin:flower123
```

访问 `http://localhost:5555`，Flower 提供以下关键仪表盘：

| 面板 | 路径 | 监控要点 |
|------|------|---------|
| Dashboard | `/` | 实时 Worker 数、任务吞吐（received/succeeded/failed） |
| Workers | `/workers` | 每个 Worker 的并发数、已处理任务数、心跳状态 |
| Tasks | `/tasks` | 按状态过滤任务（SUCCESS/FAILURE/STARTED/RETRY） |
| Task Detail | `/task/<uuid>` | 任务的 args、kwargs、result、traceback |
| Broker | `/broker` | Redis 队列长度、内存使用 |
| Monitor | `/monitor` | 实时图表：成功/失败率、处理时间分布 |

**关键运维操作**：

```bash
# 查看当前所有 Worker 状态（Flower REST API）
curl http://localhost:5555/api/workers | python -m json.tool

# 撤销一个卡死的 Celery 任务
curl -X POST http://localhost:5555/api/task/revoke/<task-id>

# 按时间范围查询任务
curl "http://localhost:5555/api/tasks?state=FAILURE&limit=50" | python -m json.tool

# 获取 Worker 池统计（并发、活跃进程数）
curl http://localhost:5555/api/workers/pool-stats | python -m json.tool
```

### 3.4 Celery Worker 核心调优实验

**步骤目标**：通过调整 `prefetch_multiplier` 和 `task_acks_late`，观察不同配置对吞吐和可靠性的影响。

#### 实验 A：prefetch_multiplier 对负载均衡的影响

创建 60 个耗时均匀的 Task（每个 sleep 10s），部署 3 个 Worker，每个 concurrency=4。分别测试 `prefetch_multiplier=1`、`4`、`0`（无上限）：

```python
"""
Celery Worker 负载均衡实验 Dag。
部署 60 个 Task 到 3 个 Worker（每 Worker 4 并发），
对比 prefetch_multiplier 对负载分布的影响。
"""
from airflow.sdk import DAG
from airflow.sdk.operators.python import PythonOperator
from datetime import datetime, timedelta
import time

def _uniform_task(task_idx: int, **context):
    """模拟均匀耗时的 ETL 任务"""
    import socket
    hostname = socket.gethostname()
    print(f"[{hostname}] Task #{task_idx} started")
    time.sleep(10)
    print(f"[{hostname}] Task #{task_idx} done")
    return hostname

with DAG(
    dag_id="celery_prefetch_experiment",
    schedule=None,
    start_date=datetime(2025, 1, 1),
    max_active_tasks=60,
    tags=["celery", "tuning"],
) as dag:
    for i in range(60):
        PythonOperator(
            task_id=f"task_{i:03d}",
            python_callable=_uniform_task,
            op_kwargs={"task_idx": i},
            queue="default",
        )
```

**观测方法**：查看 Flower Dashboard 的 Workers 面板，记录每个 Worker 的任务完成数分布。

**预期结果**：
- `prefetch_multiplier=1`：3 个 Worker 各约完成 20 个（±2），分布最均匀
- `prefetch_multiplier=4`：最快的 Worker 可能完成 30+ 个，最慢的只有 10 个
- `prefetch_multiplier=0`：与 =4 类似，但 Worker 可能在任务未完成时就持续拉取新消息，导致消息积压在某几个 Worker 上

#### 实验 B：task_acks_late 的可靠性验证

模拟 Worker 崩溃场景：

```python
"""
task_acks_late 可靠性验证——在 Task 执行过程中 kill 一个 Worker 进程，
观察未完成的任务是否被正确重新调度。
"""
def _crashable_task(task_idx: int, **context):
    import os
    import signal
    hostname = os.uname().nodename
    print(f"[{hostname}] Task #{task_idx} started, PID={os.getpid()}")
    time.sleep(5)
    if task_idx == 42:
        # 模拟 OOM：直接 kill 当前进程
        print(f"💥 Task #{task_idx} simulating OOM kill...")
        os.kill(os.getpid(), signal.SIGKILL)
    time.sleep(30)
    print(f"[{hostname}] Task #{task_idx} completed")
```

**验证步骤**：

```bash
# 1. 触发 Dag
airflow dags trigger celery_acks_late_test

# 2. 观察 Task #42 所在 Worker
docker compose exec worker-1 ps aux | grep "airflow"

# 3. 等待 OOM kill 发生，观察 Scheduler 日志
docker compose logs scheduler | grep "task_42"

# 4. 验证 Task #42 被另一个 Worker 重新执行并成功
#    ——这是 task_acks_late=True 的关键行为
```

### 3.5 500 并发 DagRun 压力测试

**步骤目标**：验证 CeleryExecutor 集群在极端负载下的吞吐和稳定性。

**压力测试 Dag——通过回填产生 500 个 DagRun**：

```python
"""
500 并发 DagRun 压力测试 Dag。
模拟典型 ETL 混合负载：短查询(30%) + 中查询(50%) + 长查询(20%)，
任务时长服从 5~120s 的随机分布。
"""
from airflow.sdk import DAG
from airflow.sdk.operators.python import PythonOperator
from airflow.sdk.operators.bash import BashOperator
from datetime import datetime, timedelta
import time, random

def _etl_task(stage: str, duration_min: int, duration_max: int, **context):
    duration = random.uniform(duration_min, duration_max)
    print(f"[{stage}] Running for {duration:.1f}s")
    time.sleep(duration)

with DAG(
    dag_id="celery_500_stress_test",
    start_date=datetime(2024, 1, 1),
    schedule="@daily",
    max_active_tasks=64,
    max_active_runs=64,
    catchup=True,  # 启用 catchup 以生成大量 DagRun
    tags=["stress", "celery"],
) as dag:
    # 阶段 1：数据抽取（短任务，5-20s）
    extract = PythonOperator(
        task_id="extract",
        python_callable=_etl_task,
        op_kwargs={"stage": "extract", "duration_min": 5, "duration_max": 20},
        queue="default",
    )

    # 阶段 2：数据转换（中等任务，15-45s）
    transforms = [
        PythonOperator(
            task_id=f"transform_{i}",
            python_callable=_etl_task,
            op_kwargs={"stage": f"transform_{i}", "duration_min": 15, "duration_max": 45},
            queue="default",
        )
        for i in range(4)
    ]

    # 阶段 3：数据加载（长任务，30-120s，路由到专用队列）
    load = BashOperator(
        task_id="load",
        bash_command="sleep $((30 + RANDOM % 90))",
        queue="low_priority",
    )

    extract >> transforms >> load
```

**执行压力测试**：

```bash
# 1. 启动所有 Worker 并监控
docker compose up -d && docker compose logs -f flower

# 2. 触发回填（生成 ~500 个 DagRun）
airflow dags backfill celery_500_stress_test \
    --start-date 2024-01-01 \
    --end-date 2025-05-15 \
    --reset-dagruns

# 3. 实时监控脚本
cat > dev/celery_monitor.py << 'PYEOF'
#!/usr/bin/env python
"""Celery 集群实时监控脚本——每 5 秒输出一次集群吞吐量"""
import subprocess, time, json
from collections import deque

history = deque(maxlen=60)  # 保留最近 5 分钟的数据

while True:
    try:
        out = subprocess.run(
            ["curl", "-s", "http://localhost:5555/api/workers?refresh=1"],
            capture_output=True, text=True, timeout=5
        )
        workers = json.loads(out.stdout)
        stats = {
            worker: (w["stats"]["total"], w["status"])
            for worker, w in workers.items()
        }
        history.append(stats)
        
        total_completed = sum(s[0] for s in stats.values())
        active_workers = [w for w, s in workers.items() if s["status"]]
        print(f"[{time.strftime('%H:%M:%S')}] "
              f"Active Workers: {len(active_workers)}/{len(workers)} | "
              f"Total Completed: {total_completed}")
    except Exception as e:
        print(f"Monitor error: {e}")
    time.sleep(5)
PYEOF

python dev/celery_monitor.py
```

**预期性能指标**（3 Worker × 8 并发 = 24 并行槽位）：

| 指标 | 目标值 | 警戒线 |
|------|--------|--------|
| 任务吞吐量 | ≥ 200 tasks/min | < 100 tasks/min |
| 任务失败率 | < 1% | > 5% |
| P50 排队延迟 | < 10s | > 60s |
| P99 排队延迟 | < 60s | > 180s |
| Redis 连接数 | < 300 | > 500 |
| Worker 内存 | < 2GB/Worker | > 4GB/Worker |

### 3.6 常见 Celery 故障排查手册

#### 故障 1：消息积压（Broker Queue Depletion）

**症状**：大量 Task 处于 QUEUED 状态但无 Worker 执行，Redis 队列长度持续增长。

**排查命令**：

```bash
# 查看 Redis 队列长度
docker compose exec redis-master redis-cli -a redispwd LLEN unacked
docker compose exec redis-master redis-cli -a redispwd LLEN default
docker compose exec redis-master redis-cli -a redispwd LLEN unacked_index

# 检查 Worker 是否存活
airflow celery worker --help  # 确保 CLI 正常
docker compose logs worker-1 | tail -50
```

**根因与修复**：
- Worker 全部宕机 → 启动 Worker
- Worker 的 `concurrency` 设置过低 → 调大或加机器
- `visibility_timeout` 太短导致消息反复重投 → 按最长 Task 时长设置（建议 4× max_task_duration）
- Broker URL 配置错误导致 Worker 连接到不同的 Redis DB → 统一 `redis://host:6379/0`

#### 故障 2：Worker 漂移（负载不均）

**症状**：部分 Worker CPU 100%，其余 Worker 空闲；Flower 中 Worker 的任务完成数差距 5 倍以上。

**根因**：`prefetch_multiplier > 1` 时，先启动的 Worker 抢走了大量消息。

**修复**：

```ini
[celery]
worker_prefetch_multiplier = 1  # 强制每个进程一次只拿一个任务
# 激进方案：启用以 Worker 为单位的任务分配
worker_prefetch_multiplier = 1
task_acks_late = True  # 配合使用
```

#### 故障 3：Result Backend 连接泄漏

**症状**：元数据库连接数持续增长，最终达到 `max_connections` 上限，Scheduler 无法写状态。

**排查**：

```sql
-- 查看 PostgreSQL 当前连接
SELECT application_name, COUNT(*) 
FROM pg_stat_activity 
GROUP BY application_name 
ORDER BY COUNT(*) DESC;
```

**根因**：`result_backend_sqlalchemy_engine_options` 未配置连接池复用。

**修复**：
```ini
[celery]
result_backend_sqlalchemy_engine_options = {"pool_size": 20, "max_overflow": 30, "pool_recycle": 1800, "pool_pre_ping": true}
```

---

## 4 项目总结

### 4.1 CeleryExecutor 核心配置速查表

| 配置项 | 默认值 | 建议生产值 | 影响维度 | 源码位置 |
|--------|--------|-----------|---------|---------|
| `BROKER_URL` | `redis://redis:6379/0` | Sentinel / RabbitMQ HA | 消息可靠性 | `default_celery.py:54` |
| `RESULT_BACKEND` | `db+sql_alchemy_conn` | 同默认 | 状态查询速度 | `default_celery.py:77-85` |
| `WORKER_CONCURRENCY` | 16 | CPU 核数 × 0.75（I/O 密集任务可翻倍） | Worker 吞吐 | `default_celery.py:123` |
| `worker_prefetch_multiplier` | 1 | 1（不变） | 负载均衡 | `default_celery.py:108` |
| `task_acks_late` | True | True | 任务可靠性 | `default_celery.py:109` |
| `SYNC_PARALLELISM` | 0（auto） | 4-8 | Scheduler 状态同步 | `celery_executor.py:148` |
| `OPERATION_TIMEOUT` | 30s | 60s（任务量大时） | 消息投递/查询延迟 | `celery_executor_utils.py:95` |
| `visibility_timeout` | 86400s | 4 × max_task_duration | 防止消息重复执行 | `default_celery.py:59` |
| `task_publish_max_retries` | 3 | 3-5 | 消息投递容错 | `celery_executor.py:156` |

### 4.2 Redis vs RabbitMQ 完整对比

| 维度 | Redis | RabbitMQ | 建议 |
|------|-------|----------|------|
| 消息可靠性 | visibility_timeout 超时重投（可能重复） | 原生 ack + 持久化（at-least-once） | RabbitMQ 更可靠 |
| 性能 | 单线程 10 万 QPS | 单节点 2 万 QPS | Redis 更快 |
| 运维 | 零依赖，Docker 一行启动 | Erlang + 集群管理 | Redis 更简单 |
| 持久化 | RDB/AOF 间隔写入，可丢消息 | 队列+消息独立持久化 | RabbitMQ 更安全 |
| 社区 | 极广泛，多为缓存场景 | 消息队列专用，生态成熟 | 平手 |
| 适用规模 | < 500 TaskInstance/min | > 500 TaskInstance/min 或可靠性敏感 | 按需选择 |
| Airflow 适配 | 需要 visibility_timeout 调优 | 开箱即用 | RabbitMQ 适配更好 |

### 4.3 适用场景

- **日均 500+ DagRun 的生产级调度**：CeleryExecutor 是 Airflow 社区推荐的生产级 Executor
- **需要跨多台机器分配任务负载**：Worker 节点可水平扩展，按队列隔离不同业务线
- **任务异构性高**：通过 queue 标签分流短查询、长 ETL、GPU 任务到不同 Worker 组
- **对 Worker 故障有快速恢复要求**：`task_acks_late=True` 保证 Worker 崩溃后任务自动重分配
- **已有 Redis/RabbitMQ 基础设施**：可直接复用现有消息队列

### 4.4 不适用场景

- **单机小规模开发测试**：LocalExecutor 足够，CeleryExecutor 引入不必要的运维复杂度
- **需要严格任务级资源隔离（CPU/GPU/内存）**：应使用 KubernetesExecutor，Pod 自带 cgroup 隔离
- **任务启动延迟敏感（< 1 秒）**：Celery 的消息投递 + Worker fork 有 0.5-2 秒开销，对高频微任务不如内嵌执行

### 4.5 注意事项与踩坑经验

| 陷阱 | 症状 | 根因 | 解决方案 |
|------|------|------|---------|
| visibility_timeout 过短 | 长任务被重复执行 | Redis 默认不调时，Airflow 默认 24h | 设为 4× 最大任务时长 |
| 未配置 Celery SSL | 消息明文传输 | `SSL_ACTIVE=False`（默认） | 生产环境务必开启 SSL + 双向 TLS |
| Result Backend 用 Redis | 任务状态丢失 | Redis 数据过期策略清除 celery-task-meta | 用 `db+sql_alchemy_conn` |
| Worker 主机名冲突 | Worker 注册失败 | 多 Worker 同主机名 | 设置 `hostname` 或使用 K8s Pod 名 |
| Sentry Integration 未配置 | Worker 异常无告警 | `sentry_integration` 虽已定义但需在 Sentry SDK 侧激活 | 配置 `sentry_sdk.init(integrations=[CeleryIntegration()])` |

### 4.6 CeleryExecutor 源码架构速览

```
celery_executor.py                    # CeleryExecutor 主类
├── __init__()                        # 创建 Celery App、BulkStateFetcher
├── _send_workloads()                 # 批量投递任务到 Celery（支持 ProcessPoolExecutor 并行）
├── _send_workloads_to_celery()       # 拆分为子进程，每个子进程调用 apply_async
├── sync()                            # 同步心跳：查询所有 running 任务的状态
├── update_all_workload_states()      # 通过 BulkStateFetcher 批量获取状态
├── try_adopt_task_instances()        # Scheduler 重启后重新接管已投递的任务
└── revoke_task()                     # 撤销 Celery 任务

celery_executor_utils.py              # 工具与基础设施
├── create_celery_app()               # 创建 Celery App（支持多 Team 隔离）
├── execute_workload()                # Celery Worker 端的 Task 入口（@app.task）
├── send_workload_to_executor()       # 子进程中调用 apply_async 序列化并投递任务
├── BulkStateFetcher                  # 批量状态查询（KV 后端 mget / DB 后端 SELECT IN / 多进程）
│   ├── _get_many_from_kv_backend()   # Redis 后端：mget 批量获取
│   ├── _get_many_from_db_backend()   # SQL 后端：SELECT ... WHERE task_id IN (...)
│   └── _get_many_using_multiprocessing()  # 兜底：多进程逐个查询
└── ExceptionWithTraceback            # 跨进程异常传播包装类

default_celery.py                     # 默认 Celery 配置提供者
└── get_default_celery_config()       # 解析 airflow.cfg [celery] 段 → Celery 配置字典
```

### 思考题

1. 你的 CeleryExecutor 集群有 5 个 Worker，每个 `worker_concurrency=16`，`prefetch_multiplier=1`，`parallelism=128`。当前有 200 个 SCHEDULED 状态的 Task 排队等待执行，所有任务的 `pool` 都用 `default_pool`（128 slots），上游依赖全部就绪。请问在同一时刻，最多有多少个 Task 同时处于 RUNNING 状态？这个上限是由哪个参数决定的？

2. 你需要设计一个"关键业务优先"的 Celery 队列体系：VIP 客户的数据刷新任务要求在 30 秒内开始执行，普通报表可以在 5 分钟内排队。你会如何利用 Celery 的 `queue` 参数和 Airflow 的 Pool 机制共同实现？请画出 Worker → Queue → Pool → Task 的四层映射关系图，并说明当 VIP 队列满时，普通 Worker 是否应该"借调"去执行 VIP 任务？

*（答案将在后续章节揭晓）*

---

> **本章完成**：你已深入掌握了 CeleryExecutor 的 Broker 选型、Worker 调优、Result Backend 配置、Flower 监控和常见故障排查全链路。结合第 20 章的执行器对比和第 22 章的 Pool 资源隔离，你现在具备了设计和运维大规模 CeleryExecutor 集群的完整知识体系。下一章我们将进入 KubernetesExecutor 领域，探索云原生调度架构。
