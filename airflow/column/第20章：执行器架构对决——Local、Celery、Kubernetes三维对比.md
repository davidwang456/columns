# 第20章：执行器架构对决——Local、Celery、Kubernetes 三维对比

## 1 项目背景

某创业公司的数据团队经历了三次 Airflow 架构升级。

**阶段一（0-50 Dag）**：用 LocalExecutor，Scheduler 和任务执行在同一台机器上。一切简单美好，直到有一天一个 Task 写得有 bug 导致 CPU 100%，整个调度器一起卡死。

**阶段二（50-200 Dag）**：升级为 CeleryExecutor，部署了 5 个 Worker 节点。终于不怕单任务卡死了。但新问题出现了：不同团队的任务对资源需求差异巨大——算法团队需要一个 GPU Worker，ETL 团队只需要 2 核 4G。Celery 的任务分配无法做到这么细粒度的资源隔离。

**阶段三（200+ Dag）**：引入 KubernetesExecutor 作为补充，算法团队的训练任务跑在 K8s Pod 里（每个 Pod 可以指定 GPU），ETL 团队继续用 CeleryExecutor。两个 Executor 通过 CeleryKubernetesExecutor 协同工作。

> 这个故事告诉我们：**没有"最佳"Executor，只有"最适合当前位置"的 Executor**。理解三种 Executor 的设计哲学和优劣权衡是正确选型的前提。

---

## 2 项目设计

**小胖**（看着三种 Executor 的配置文档一头雾水）："LocalExecutor 和 CeleryExecutor 到底差在哪里？不都是跑任务吗？"

**大师**："核心差异在于"任务的执行地点"。LocalExecutor 把任务放在 Scheduler 进程内部或 fork 的子进程中执行——Scheduler 和 Worker 是同一批进程，共享同一台机器。CeleryExecutor 把任务通过消息队列（Redis/RabbitMQ）分发给远程的 Worker 进程——Scheduler 和 Worker 可以在不同机器上。KubernetesExecutor 更进一步——它不给任务分配 Worker 进程，而是给每个任务创建一个独立的 K8s Pod。"

**小白**："那什么时候该选哪个？"

**大师**："三句话原则：LocalExecutor 适合'一个人的开发环境或小团队'——简单、零运维、但缺乏隔离和扩展性。CeleryExecutor 适合'你需要分布式执行但不想为每个任务开虚拟机'——经典的生产环境选择。KubernetesExecutor 适合'你已经在 K8s 上且需要极致的资源隔离'——每个任务的环境可以完全独立，但启动延迟是三者中最大的。"

**小胖**："好像还有个 CeleryKubernetesExecutor？"

**大师**："那是混合模式——你可以把轻量级的 ETL 任务路由到 Celery Worker（秒级启动），把重量级的 ML 训练任务路由到 K8s Pod（分钟级启动但资源隔离好）。通过 Task 的 `queue` 参数来指定路由。"

> **技术映射**：LocalExecutor = 同城快递员自己送（方便但不适合跨城市），CeleryExecutor = 全国网点统一调派（灵活但有调度开销），KubernetesExecutor = 每个包裹包一辆专车（贵但隔离好）。

---

## 3 项目实战

### 3.1 LocalExecutor：配置与观察

```ini
# airflow.cfg
[core]
executor = LocalExecutor

[core]
parallelism = 32
```

**Docker Compose 中的 LocalExecutor 配置**：

```yaml
# docker-compose.yaml（简化版）
services:
  scheduler:
    environment:
      - AIRFLOW__CORE__EXECUTOR=LocalExecutor
  # 不需要单独的 worker 服务
```

**观察 LocalExecutor 的执行**：

```bash
# Scheduler 日志中直接看到 Task 执行
docker compose logs airflow-scheduler-1 | grep "Executing"
```

### 3.2 CeleryExecutor：部署与调优

**环境准备**：

```yaml
# docker-compose.yaml
services:
  redis:
    image: redis:7-alpine
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]

  scheduler:
    environment:
      - AIRFLOW__CORE__EXECUTOR=CeleryExecutor
      - AIRFLOW__CELERY__BROKER_URL=redis://redis:6379/0
      - AIRFLOW__CELERY__RESULT_BACKEND=db+postgresql://airflow:airflow@postgres/airflow

  worker:
    environment:
      - AIRFLOW__CORE__EXECUTOR=CeleryExecutor
    command: celery worker
    deploy:
      replicas: 3  # 3 个 Worker 实例
```

**Celery 调优参数**：

```ini
[celery]
worker_concurrency = 16               # 每个 Worker 的并发进程数
worker_prefetch_multiplier = 1        # 预取任务数
task_acks_late = True                 # 任务完成后才确认（防止丢失）
task_reject_on_worker_lost = True     # Worker 丢失时重新分配任务
broker_transport_options = {"visibility_timeout": 3600}
```

**Celery 监控（Flower）**：

```bash
# 启动 Flower 监控面板
docker compose up -d flower

# 访问 http://localhost:5555
```

Flower 面板能看到的指标：
- 每个 Worker 的活跃/已完成任务数
- 队列长度（排队中的任务数）
- 任务执行时间分布
- Worker 上下线事件

### 3.3 KubernetesExecutor：Pod 模板配置

**环境准备**：

```ini
# airflow.cfg
[core]
executor = KubernetesExecutor

[kubernetes_executor]
namespace = airflow
pod_template_file = /opt/airflow/pod_templates/default.yaml
worker_container_repository = apache/airflow
worker_container_tag = 3.0.0
delete_worker_pods = True
```

**Pod 模板**（`/opt/airflow/pod_templates/default.yaml`）：

```yaml
apiVersion: v1
kind: Pod
metadata:
  labels:
    app: airflow-task
spec:
  containers:
    - name: base
      image: apache/airflow:3.0.0-python3.11
      resources:
        requests:
          cpu: "1"
          memory: "2Gi"
        limits:
          cpu: "2"
          memory: "4Gi"
      env:
        - name: AIRFLOW__CORE__EXECUTOR
          value: KubernetesExecutor
  restartPolicy: Never
```

**指定特定 Task 使用 GPU Pod**：

```python
# 在 Dag 中为特定的 Task 指定 Pod 模板
train_model = PythonOperator(
    task_id="train_model",
    python_callable=_train,
    executor_config={
        "pod_override": {
            "spec": {
                "containers": [{
                    "name": "base",
                    "resources": {
                        "limits": {
                            "nvidia.com/gpu": "1",
                            "cpu": "4",
                            "memory": "16Gi",
                        },
                    },
                }],
            },
        },
    },
)
```

### 3.4 混合执行器：CeleryKubernetesExecutor

```ini
[core]
executor = CeleryKubernetesExecutor

[celery_kubernetes_executor]
kubernetes_queue = kubernetes
```

在 Dag 中通过 `queue` 参数路由：

```python
# 路由到 Celery Worker
lightweight_task = PythonOperator(
    task_id="quick_etl",
    python_callable=_etl,
    queue="default",  # Celery 默认队列
)

# 路由到 K8s Pod
heavy_task = PythonOperator(
    task_id="ml_training",
    python_callable=_train,
    queue="kubernetes",  # K8s 队列
)
```

### 3.5 三种执行器性能对比测试

编写一个测试 Dag 来对比：

```python
"""执行器性能对比测试"""
from airflow.sdk import DAG
from airflow.sdk.operators.python import PythonOperator
from datetime import datetime, timedelta

with DAG(
    dag_id="executor_benchmark",
    schedule=None,
    start_date=datetime(2025, 1, 1),
    tags=["benchmark"],
) as dag:

    def _record_time(task_name, **context):
        ti = context["task_instance"]
        delay = (
            ti.start_date - ti.queued_dttm
        ).total_seconds() if ti.queued_dttm else -1
        run_time = (datetime.now() - ti.start_date).total_seconds()
        print(f"[{task_name}] 排队延迟: {delay:.1f}s, 运行时间: {run_time:.1f}s")
        return {"delay": delay, "run_time": run_time}

    # 创建 20 个并发 Task，对比排队延迟
    for i in range(20):
        PythonOperator(
            task_id=f"task_{i:02d}",
            python_callable=_record_time,
            op_kwargs={"task_name": f"task_{i:02d}"},
        )
```

预期结果对比：

| 指标 | LocalExecutor | CeleryExecutor | KubernetesExecutor |
|------|--------------|----------------|-------------------|
| 单任务启动延迟 | < 1s | 1~3s | 5~30s（Pod 启动） |
| 100 并发总时间 | ~30s | ~20s | ~60s |
| 资源隔离 | 无 | 进程级 | 容器级 |
| 单节点故障影响 | 全宕 | 部分宕 | 最小 |

---

## 4 项目总结

### 执行器选型决策树

```
你的团队规模？
├── 1-3 人，开发/测试环境
│   └── LocalExecutor
├── 10+ 人，生产环境，不在 K8s 上
│   └── CeleryExecutor
├── 10+ 人，生产环境，已在 K8s 上
│   ├── 对启动延迟不敏感 → KubernetesExecutor
│   └── 需要混合调度 → CeleryKubernetesExecutor
└── 50+ Dag，多种资源需求
    └── CeleryKubernetesExecutor
```

### 注意事项

1. **LocalExecutor 不要在生产用**：单点故障，无资源隔离
2. **Celery 的 visibility_timeout**：必须大于最长任务的执行时间，否则任务会被重复分配
3. **K8s Executor 的启动延迟**：镜像拉取、Pod 调度都需要时间，不适合需要秒级响应的场景
4. **Celery Broker 高可用**：生产环境务必配置 Redis Sentinel 或 RabbitMQ 集群

### 思考题

1. KubernetesExecutor 中，每个 Task 启动一个 Pod。如果一个 Dag 有 100 个并行 Task，瞬间创建 100 个 Pod 会导致什么？如何控制这种突发？
2. CeleryKubernetesExecutor 中，如何决定哪些 Task 走 Celery、哪些走 K8s？除了 `queue` 参数，还有什么策略？

*（答案将在下一章揭晓）*

---

> **本章完成**：你已经掌握了三种执行器的工作原理和选型决策。下一章将学习 TaskFlow 的高级特性——动态任务映射与分支编排。
