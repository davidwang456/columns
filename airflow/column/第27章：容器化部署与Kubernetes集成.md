# 第27章：容器化部署与 Kubernetes 集成

## 1 项目背景

某互联网金融公司数据平台的 Airflow 集群，在经历了"单机 LocalExecutor → 多机 CeleryExecutor"两轮升级后，又撞到了新的天花板。

**第一个痛点是机器利用率低**。Celery 集群的 20 台 Worker 节点，白天跑批量 ETL 任务时 CPU 打满，深夜几乎全部空转。运维团队尝试用云厂商的弹性伸缩方案，但 Worker 的扩缩容需要分钟级——一旦流量尖峰来了，任务已经排队 10 分钟了。

**第二个痛点是环境一致性灾难**。不同团队的任务依赖不同的 Python 库版本——算法团队的 PyTorch 模型训练需要 Python 3.11 + CUDA，而 ETL 团队的 Pandas 脚本还在 Python 3.9 上跑。运维被迫维护了四套不同的 Worker 镜像，每次库版本升级都是一场"打地鼠"式的排查：改了镜像 A，任务 X 好了；任务 Y 因为镜像 A 的变更报错……周而复始。

**第三个痛点是资源隔离不彻底**。一个大数据量的 Spark 提交任务占满了 Worker 的内存，导致同一台 Worker 上的其他轻量级 API 调用任务超时失败。Celery 的 `worker_concurrency` 只能限制并发数，无法限制单任务的内存和 CPU，更别说 GPU 了。

> 这些痛点指向同一个答案：**Kubernetes**。容器化不是"把进程装进镜像跑在 K8s 上"就完了——它需要重新设计 Airflow 每个组件的运行形态、网络拓扑、存储策略和安全边界。

---

## 2 项目设计

**小胖**（看着 Docker Compose 的配置文件发呆）："我们现在 Docker Compose 跑得好好的，为啥要费劲上 K8s？不就多了个 YAML 编排吗？"

**大师**："Docker Compose 本质上还是单机思维。你虽然定义了 scheduler、worker、redis 三个 service，但它们都在一台宿主机的 Docker 网络里。K8s 给你的核心能力是 Pod 级别的资源隔离——每个任务跑在一个独立的 Pod 里，这个 Pod 可以单独指定 CPU、内存、GPU、环境变量、镜像版本，甚至独立的 ServiceAccount。出了单机故障，Pod 自动漂移到别的 Node。这些能力 Docker Compose 都给不了你。"

**小白**："那 KubernetesExecutor 和 CeleryExecutor 的根本差异在哪里？"

**大师**："一句话总结：CeleryExecutor 的任务分配单位是'一个 Worker 进程'，KubernetesExecutor 的任务分配单位是'一个 Pod'。在 Celery 模型里，Worker 启动后长期存活，不断地从消息队列拉任务执行——这个 Worker 的环境是固定不变的。在 K8s 模型里，Scheduler 每次提交任务时，不是发给某个 Worker 进程，而是调用 K8s API Server 创建一个新 Pod。Pod 里跑一个单次容器，任务执行完、Pod 就被回收。这意味着每个任务可以有不同的运行环境。"

**小胖**："那不就跟 Serverless 一样了？每个请求一个容器？"

**大师**："对，思路非常接近。但代价也明确：Pod 创建有冷启动延迟（镜像拉取 + 容器启动 + Pod 调度），通常是 10-60 秒。对于秒级的 API 调用任务，这个开销不可接受。所以 KubernetesExecutor 更适合两种场景：一是需要极端资源隔离的任务（比如 GPU 训练）；二是运行时间长（分钟级以上）的任务。短小精悍的任务仍然建议 CeleryExecutor。"

> **技术映射**：CeleryExecutor = 公司雇佣一批固定员工，每人一台标配电脑，谁没事干谁接活；KubernetesExecutor = 外包平台，每来一个活就派一名临时工过去，可以按需配不同装备。

**小白**："那 Helm Chart 和 Docker Compose 是什么关系？我能不能直接手写 K8s YAML 来部署 Airflow？"

**大师**："你当然可以手写 Deployment、Service、ConfigMap 一把梭。但 Airflow 需要同时跑 Scheduler、Webserver、Worker（Celery 模式）、Dag Processor、Triggerer 总共五六个组件，每个组件还有各自的健康检查、探活、资源限制、持久化存储、ServiceAccount 权限……手写下来大概 800-1000 行 YAML，而且每次升级 Airflow 版本你还得自己追变更。Helm Chart 就像 Airflow 的'安装程序'——它把这些组件的 K8s 资源模板化了，你只需要在 `values.yaml` 里声明'我要几个 Worker 副本''镜像版本是 3.0.0''Dag 文件从哪个 Git 仓库同步'，Helm 帮你生成完整的 K8s 资源。"

**小胖**："那 Git-Sync 又是什么？Dag 文件不是放在 PV 里就行了吗？"

**大师**："PV 是最简单的方案——你把 Dag 文件放在一个 PersistentVolume 里，所有组件挂载同一个 PV。但问题来了：你怎么更新 Dag 文件？每次改 Dag 都要 `kubectl cp` 到 PV 里吗？版本怎么管理？Git-Sync 解决了这个问题：它用一个 Sidecar 容器，每 60 秒自动 `git pull` 你的 Dag 仓库，把最新 Dag 文件同步到共享 Volume。所有组件再从这个 Volume 读取。这样你的 Dag 仓库就是唯一真相源（Single Source of Truth），改 Dag 只要 push 到 Git，60 秒后全集群自动更新。"

> **技术映射**：PV 方案 = 共享 U 盘，谁都可以写但不知道谁改了啥；Git-Sync 方案 = 团队共享云盘 + 版本历史，每次修改都有记录可追溯。

**小白**："那网络策略呢？K8s 里组件之间的通信好像默认是全通的？"

**大师**："没错，K8s 默认扁平网络——同一个 Namespace 里的 Pod 可以互相访问。这在生产环境有风险：假如一个 Worker Pod 被入侵，攻击者可以通过内部网络直接访问 PostgreSQL 数据库。网络策略（NetworkPolicy）就是给组件间通信加防火墙。核心规则只有三条：Scheduler 和 Webserver 可以访问 DB；Worker 和 Dag Processor 不能访问 DB，只能访问 Execution API；外部流量只能通过 Ingress 访问 Webserver 的 8080 端口。这样即使 Worker 被攻破，攻击者也拿不到数据库凭据——因为网络上就不通。"

---

## 3 项目实战

### 3.1 自定义 Docker 镜像

**步骤目标**：基于官方 `apache/airflow` 构建包含团队自定义 Python 库的业务镜像。

```dockerfile
# Dockerfile.airflow-custom
FROM apache/airflow:3.0.0-python3.11

# 切换为 root 安装系统依赖
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# 切回 airflow 用户
USER airflow

# 安装自定义 Provider 和业务依赖
COPY --chown=airflow:root requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# 注入自定义 Dag 模板（可选）
COPY --chown=airflow:root pod_templates/ /opt/airflow/pod_templates/

# 验证安装
RUN pip list | grep -E "apache-airflow|pandas|certifi"
```

```txt
# requirements.txt
apache-airflow-providers-cncf-kubernetes>=10.0.0
apache-airflow-providers-amazon>=9.0.0
pandas==2.1.4
numpy==1.26.4
boto3==1.34.0
kubernetes==29.0.0
```

**构建并推送镜像**：

```bash
docker build -t my-registry.example.com/airflow-custom:3.0.0-v1 -f Dockerfile.airflow-custom .
docker push my-registry.example.com/airflow-custom:3.0.0-v1
```

> **常见坑**：`pip install` 时如果遇到 OpenSSL 相关错误，在 Dockerfile 中添加 `apt-get install -y libssl-dev`。另外，官方镜像默认已包含 Kubernetes Provider，如果你在 requirements.txt 中安装了不兼容的版本，可能导致执行器异常。

---

### 3.2 Docker Compose 生产级配置

**步骤目标**：在 `docker-compose.yaml` 中配置多副本、健康检查、持久化卷和日志驱动，为后续 K8s 迁移打好基础。

```yaml
# docker-compose.prod.yaml
version: "3.8"
x-airflow-common: &airflow-common
  image: my-registry.example.com/airflow-custom:3.0.0-v1
  environment: &airflow-env
    AIRFLOW__CORE__EXECUTOR: CeleryExecutor
    AIRFLOW__DATABASE__SQL_ALCHEMY_CONN: postgresql+psycopg2://airflow:airflow@postgres/airflow
    AIRFLOW__CELERY__BROKER_URL: redis://redis:6379/0
    AIRFLOW__CELERY__RESULT_BACKEND: db+postgresql://airflow:airflow@postgres/airflow
    AIRFLOW__LOGGING__REMOTE_LOGGING: "True"
    AIRFLOW__LOGGING__REMOTE_LOG_CONN_ID: s3_logs
    AIRFLOW__CORE__LOAD_EXAMPLES: "false"
  volumes:
    - airflow_dags:/opt/airflow/dags
    - airflow_logs:/opt/airflow/logs
    - ./plugins:/opt/airflow/plugins
    - ./airflow.cfg:/opt/airflow/airflow.cfg
  depends_on:
    postgres:
      condition: service_healthy
    redis:
      condition: service_healthy

services:
  postgres:
    image: postgres:15-alpine
    environment:
      POSTGRES_USER: airflow
      POSTGRES_PASSWORD: airflow
      POSTGRES_DB: airflow
    healthcheck:
      test: ["CMD", "pg_isready", "-U", "airflow"]
      interval: 10s
      retries: 5
    volumes:
      - postgres_data:/var/lib/postgresql/data
    deploy:
      resources:
        limits:
          memory: 2G
          cpus: "2"

  redis:
    image: redis:7-alpine
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      retries: 5

  scheduler:
    <<: *airflow-common
    command: scheduler
    healthcheck:
      test: ["CMD-SHELL", "airflow jobs check --job-type SchedulerJob --hostname $$(hostname)"]
      interval: 30s
      timeout: 10s
      retries: 3
    deploy:
      replicas: 2
      resources:
        limits:
          memory: 2G
          cpus: "2"

  webserver:
    <<: *airflow-common
    command: webserver
    ports:
      - "8080:8080"
    healthcheck:
      test: ["CMD", "curl", "--fail", "http://localhost:8080/health"]
      interval: 15s
      retries: 5
    deploy:
      replicas: 2

  triggerer:
    <<: *airflow-common
    command: triggerer
    deploy:
      replicas: 2

  dag-processor:
    <<: *airflow-common
    command: dag-processor
    deploy:
      replicas: 2

  worker:
    <<: *airflow-common
    command: celery worker
    deploy:
      replicas: 4
      resources:
        limits:
          memory: 4G
          cpus: "4"

  flower:
    <<: *airflow-common
    command: celery flower
    ports:
      - "5555:5555"
    healthcheck:
      test: ["CMD", "curl", "--fail", "http://localhost:5555/"]
      interval: 30s
      retries: 3

volumes:
  postgres_data:
  airflow_dags:
  airflow_logs:
```

**验证部署**：

```bash
docker compose -f docker-compose.prod.yaml up -d
docker compose -f docker-compose.prod.yaml ps --format "table {{.Name}}\t{{.Status}}\t{{.Ports}}"
```

---

### 3.3 部署 Airflow Helm Chart

**步骤目标**：在 K8s 集群中使用官方 Helm Chart 部署 Airflow，配置 KubernetesExecutor。

**前置条件**：
- K8s 集群 v1.27+
- `helm` v3.12+
- `kubectl` 已配置并能访问集群
- 集群有默认 StorageClass（用于动态 PV 创建）

**第一步：添加 Helm 仓库**

```bash
helm repo add apache-airflow https://airflow.apache.org
helm repo update
helm search repo apache-airflow
```

**第二步：编写 values.yaml**

```yaml
# values-custom.yaml
defaultAirflowTag: "3.0.0-python3.11"

# 使用 KubernetesExecutor
executor: KubernetesExecutor

# 数据库（外置 PostgreSQL，生产环境必须）
data:
  metadataConnection:
    user: airflow
    pass: Airflow123!  # 生产环境建议用 K8s Secret
    host: postgres-postgresql.airflow.svc.cluster.local
    port: 5432
  brokerUrl: redis://redis-master.airflow.svc.cluster.local:6379/0

# 各组件副本数
scheduler:
  replicas: 2
triggerer:
  replicas: 2
dagProcessor:
  replicas: 2
webserver:
  replicas: 2
  service:
    type: LoadBalancer  # 或 ClusterIP + Ingress

# Git-Sync 同步 Dag
dags:
  persistence:
    enabled: true
    size: 1Gi
    storageClassName: standard
  gitSync:
    enabled: true
    repo: https://github.com/my-team/airflow-dags.git
    branch: main
    rev: HEAD
    depth: 1
    subPath: "dags/"
    wait: 60

# KubernetesExecutor 的 Pod 模板配置
workers:
  keda:
    enabled: true
  persistence:
    enabled: false

# 环境变量 & 自定义配置
env:
  - name: AIRFLOW__CORE__LOAD_EXAMPLES
    value: "false"
  - name: AIRFLOW__LOGGING__REMOTE_LOGGING
    value: "True"
  - name: AIRFLOW__LOGGING__REMOTE_LOG_CONN_ID
    value: "s3_logs"

config:
  core:
    dags_folder: /opt/airflow/dags
  kubernetes_executor:
    namespace: airflow
    delete_worker_pods: true
    worker_container_repository: my-registry.example.com/airflow-custom
    worker_container_tag: 3.0.0-v1
    worker_container_image_pull_policy: IfNotPresent
    worker_pods_creation_batch_size: 10
    multi_namespace_mode: false
```

**第三步：安装 Helm Chart**

```bash
kubectl create namespace airflow

helm install airflow apache-airflow/airflow \
  --namespace airflow \
  --values values-custom.yaml \
  --timeout 10m
```

**第四步：验证部署状态**

```bash
kubectl get pods -n airflow -w
# 预期输出：
# NAME                                   READY   STATUS    RESTARTS
# airflow-scheduler-0                    1/1     Running   0
# airflow-scheduler-1                    1/1     Running   0
# airflow-triggerer-0                    1/1     Running   0
# airflow-webserver-0                    1/1     Running   0
# airflow-dag-processor-0                1/1     Running   0

helm list -n airflow
```

> **常见坑 1**：Helm 安装超时。通常是因为镜像拉取太慢（`apache/airflow:3.0.0` 约 1.2GB）。解决：提前 `docker pull` 到所有 Node 或用镜像代理。

> **常见坑 2**：Git-Sync 容器报权限错误。如果 Dag 仓库需要 SSH Key，在 values 中配置 `dags.gitSync.sshKeySecret`。

---

### 3.4 配置 KubernetesExecutor 的 Pod 模板

**步骤目标**：为不同 Task 指定不同的 Pod 资源模板。

```python
# k8s_pod_override_dag.py
from airflow.sdk import DAG
from airflow.sdk.operators.python import PythonOperator
from datetime import datetime

with DAG(
    dag_id="k8s_pod_override_demo",
    schedule=None,
    start_date=datetime(2025, 1, 1),
    tags=["k8s"],
) as dag:

    # 默认模板：1 核 2G
    def light_task(**ctx):
        print("轻量任务：默认资源即可")

    light = PythonOperator(
        task_id="light_task",
        python_callable=light_task,
    )

    # 通过 executor_config 覆盖 Pod 资源
    def heavy_task(**ctx):
        import pandas as pd
        df = pd.DataFrame({"col": range(10_000_000)})
        print(f"重计算任务完成，数据量: {len(df)} 行")

    heavy = PythonOperator(
        task_id="heavy_task",
        python_callable=heavy_task,
        executor_config={
            "pod_override": {
                "spec": {
                    "containers": [
                        {
                            "name": "base",
                            "resources": {
                                "requests": {"cpu": "4", "memory": "8Gi"},
                                "limits": {"cpu": "8", "memory": "16Gi"},
                            },
                            "env": [
                                {"name": "PYTHONUNBUFFERED", "value": "1"},
                            ],
                        }
                    ],
                    "node_selector": {"workload-type": "compute-heavy"},
                    "tolerations": [
                        {
                            "key": "workload-type",
                            "operator": "Equal",
                            "value": "compute-heavy",
                            "effect": "NoSchedule",
                        }
                    ],
                }
            }
        },
    )

    light >> heavy
```

**Pod 模板文件方式**（全局默认模板）：

```yaml
# pod_template.yaml
apiVersion: v1
kind: Pod
metadata:
  labels:
    app: airflow-task
spec:
  serviceAccountName: airflow-worker
  automountServiceAccountToken: true
  securityContext:
    fsGroup: 50000
  containers:
    - name: base
      image: my-registry.example.com/airflow-custom:3.0.0-v1
      imagePullPolicy: IfNotPresent
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
      volumeMounts:
        - name: shared-logs
          mountPath: /opt/airflow/logs
  volumes:
    - name: shared-logs
      emptyDir: {}
  restartPolicy: Never
```

在 airflow.cfg 中引用：

```ini
[kubernetes_executor]
pod_template_file = /opt/airflow/pod_templates/pod_template.yaml
```

---

### 3.5 网络策略配置

**步骤目标**：加固组件间通信，防止越权访问。

```yaml
# network-policy.yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: airflow-db-access
  namespace: airflow
spec:
  podSelector:
    matchLabels:
      component: "{{ .Chart.Name }}-scheduler"
  policyTypes:
    - Egress
  egress:
    - to:
        - namespaceSelector:
            matchLabels:
              name: postgres
      ports:
        - protocol: TCP
          port: 5432
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: airflow-deny-worker-db
  namespace: airflow
spec:
  podSelector:
    matchLabels:
      component: "airflow-worker-pod"
  policyTypes:
    - Egress
  egress:
    # 允许访问 Execution API（Webserver）
    - to:
        - podSelector:
            matchLabels:
              component: "airflow-webserver"
      ports:
        - protocol: TCP
          port: 8080
    # 允许 DNS 查询
    - to:
        - namespaceSelector: {}
          podSelector:
            matchLabels:
              k8s-app: kube-dns
      ports:
        - protocol: UDP
          port: 53
    # 禁止访问数据库
    # 默认拒绝所有其他 Egress，因此不写 DB 的规则 = 不通
```

> **注意**：NetworkPolicy 需要集群 CNI 插件（如 Calico、Cilium）支持。Flannel 默认不支持 NetworkPolicy。

---

### 3.6 运行端到端测试 Dag

**步骤目标**：验证 KubernetesExecutor 下 K8s Pod 的创建、日志收集和自动清理。

```python
# k8s_e2e_test.py
from airflow.sdk import DAG
from airflow.sdk.operators.python import PythonOperator
from airflow.sdk.operators.bash import BashOperator
from datetime import datetime

with DAG(
    dag_id="k8s_e2e_test",
    schedule=None,
    start_date=datetime(2025, 1, 1),
    tags=["k8s", "e2e"],
) as dag:

    check_env = BashOperator(
        task_id="check_k8s_env",
        bash_command="""
            echo "=== Pod 基本信息 ==="
            echo "Hostname: $(hostname)"
            echo "Namespace: $KUBERNETES_NAMESPACE"
            echo "Pod CPU Limit: $(cat /sys/fs/cgroup/cpu/cpu.cfs_quota_us || echo 'N/A')"
            echo "=== Python 环境 ==="
            python --version
            pip list | grep -E "apache-airflow|pandas|boto3"
        """,
    )

    def test_resource_isolation(**ctx):
        import time
        # 模拟内存消耗任务
        big_list = [0] * 50_000_000
        print(f"分配了 {len(big_list)} 个元素的列表，内存约 {len(big_list) * 8 / 1024 / 1024:.0f} MB")
        time.sleep(10)
        del big_list
        print("任务完成，内存已释放")

    cpu_task = PythonOperator(
        task_id="cpu_memory_test",
        python_callable=test_resource_isolation,
        executor_config={
            "pod_override": {
                "spec": {
                    "containers": [
                        {
                            "name": "base",
                            "resources": {
                                "requests": {"cpu": "500m", "memory": "1Gi"},
                                "limits": {"cpu": "1", "memory": "2Gi"},
                            },
                        }
                    ]
                }
            }
        },
    )

    check_env >> cpu_task
```

**触发 Dag 并观察 Pod 生命周期**：

```bash
# 触发 Dag
airflow dags trigger k8s_e2e_test

# 观察 K8s Pod 的创建
kubectl get pods -n airflow -w --selector=dag_id=k8s_e2e_test

# 查看 Pod 日志（等同于 Airflow Task 日志）
kubectl logs -n airflow <pod-name> -c base

# 验证 Pod 在任务完成后自动删除（delete_worker_pods=True）
kubectl get pods -n airflow --selector=dag_id=k8s_e2e_test
# 任务完成后 Pod 应该消失
```

---

## 4 项目总结

### 部署方案三维对比

| 维度 | Docker Compose | Helm Chart（Celery） | Helm Chart（K8s） |
|------|---------------|---------------------|-------------------|
| 部署复杂度 | 低（1 台机，1 个 compose 文件） | 中（需配置 K8s 集群 + Helm） | 高（需 K8s + ServiceAccount + RBAC + NetworkPolicy） |
| 资源隔离粒度 | 容器级（共享宿主机内核） | Worker 进程级（每 Worker 一个 Pod） | Task 级（每 Task 一个 Pod） |
| 单任务冷启动 | < 1 秒 | 1-3 秒（Celery dispatch） | 10-60 秒（Pod 创建 + 镜像拉取） |
| 弹性伸缩能力 | 无（需手动改 docker-compose） | 中等（HPA 基于 CPU/内存） | 强（KEDA 基于任务队列深度） |
| GPU 支持 | 不友好（需 nvidia-docker） | 通过 NodeSelector 间接支持 | 原生支持（Pod 声明 GPU 资源） |
| 高可用 | 单机，Docker 重启 | 多 Pod 自动漂移 | 多 Pod 自动漂移 + 跨 AZ |
| GitOps 友好度 | 差（手动更新文件） | 好（Git-Sync 自动同步） | 优秀（Git-Sync + Helm 版本化） |
| 运维成本 | 低（适合小团队） | 中（适合中型集群） | 高（需要 K8s 运维经验） |

### 适用场景

- **Docker Compose**：开发环境、< 50 个 Dag 的小团队、CI 中运行 Airflow 集成测试。
- **Helm Chart + CeleryExecutor**：生产环境、100-500 个 Dag、需要多 Worker 水平扩展、不想引入 Task 级 Pod 冷启动延迟。
- **Helm Chart + KubernetesExecutor**：需要极致资源隔离（ML 训练、大数据计算）、Task 运行时间长（> 5 分钟）、任务类型差异大（Python 版本、系统依赖各不相同）。

### 注意事项

1. **镜像大小直接影响冷启动**：`apache/airflow:3.0.0` 基础镜像约 1.2GB。如果每个 Task 都要重新拉镜像，累积延迟很可观。建议将所有 Node 提前预热镜像，或在 `imagePullPolicy` 中使用 `IfNotPresent`。
2. **Git-Sync 不是"实时"的**：默认 60 秒同步间隔意味着你 push 一个 Dag 后，最多 60 秒才会出现在 Airflow 中。如果需要秒级生效，考虑直接挂载 PV 并用 `kubectl cp` 或 CI 流程写入。
3. **KubernetesExecutor 不能和 Celery 混用**（除非专门配置 CeleryKubernetesExecutor）。选择了 KubernetesExecutor 后，所有 Task 都会走 Pod 创建流程。
4. **RBAC 权限要精细**：Worker Pod 的 ServiceAccount 只需要"对自己命名空间的 Pod 有 get/list 权限"（用于 Execution API Token），不要给集群级别的权限。
5. **日志持久化**：Pod 删除后，`kubectl logs` 就不可用了。必须配置 Remote Logging（S3/GCS/Azure Blob）或部署 Loki + Promtail 做日志聚合。

### 常见踩坑经验

1. **"Pod 创建了但一直 Pending"**：通常是资源不够——检查 Node 是否有足够的 CPU/内存满足 `resources.requests`。也可能是 PVC 没有默认 StorageClass。用 `kubectl describe pod <pod-name>` 看 Events。
2. **"Worker Pod 报 Permission Denied 写日志"**：Airflow 容器默认以 `airflow` 用户（UID 50000）运行。如果挂载的 PV 目录属主不是 50000，会写日志失败。解决：设置 Pod `securityContext.fsGroup: 50000`。
3. **"Git-Sync 容器 CPU Throttling"**：Git-Sync Sidecar 默认的 CPU 限制很低（100m），大仓库 git clone 时容易触发 CPU 限流导致超时。在 values.yaml 中调高 `dags.gitSync.resources.limits.cpu`。

### 思考题

1. **如果一个 Task 的 Pod 在运行过程中被 OOMKilled（内存超限被 K8s 杀掉），Airflow 会如何感知？重试机制会生效吗？如果该 Task 设了 `retries=3`，三次都 OOMKilled 后，如何避免重复创建这种"注定失败"的 Pod？**

2. **假设你的集群有 3 个可用区（AZ），每个 AZ 部署了若干 Node。如果数据库部署在 AZ-A，而 Scheduler 部署在 AZ-B，跨 AZ 的网络延迟会影响什么？你会如何设计 Pod 的亲和性/反亲和性规则来优化延迟和可用性的平衡？**

---

> **下一章预告**：第 28 章将搭建 Airflow 的完整可观测性体系——StatsD + Prometheus + Grafana，配置核心告警规则，让 Airflow 集群的运行状态一目了然。
