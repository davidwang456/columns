# 第2章：环境搭建——从零部署 Airflow

## 1 项目背景

小王是一名刚入职的数据工程师，mentor 扔给他一台全新的云服务器："Airflow 环境交给你了，下周一之前要能跑起来，下周二我们要把 30 个 ETL 任务迁移上去。"

小王打开终端，面临第一个选择：怎么装？pip install？Docker？还是 Kubernetes？每种方式背后都是不同的部署哲学和运维成本。更头疼的是，Airflow 不是一个简单的单体应用——它有 Scheduler、Webserver、Database、Dag Processor、Triggerer、Worker 等多个组件，每个组件都有自己的配置项。

网上搜到的教程有的是 Airflow 2.x 的，有的是 1.x 的，配置项名称都不一样，小王照着教程一顿操作，结果 Webserver 能起来但 Scheduler 报错，或者 Scheduler 起来了但 Dag 不加载……他忍不住在群里吐槽："安装 Airflow 比写 Python 难多了。"

> 这几乎是每个 Airflow 新手必经的"入门痛"。

本章将带你从零搭建一个完全可用的 Airflow 开发环境，理解每个配置项的含义，避免小王踩过的坑。

---

## 2 项目设计

**小胖**（对着三个终端窗口抓耳挠腮）："大师，为啥装个 Airflow 这么麻烦？我 pip install 一下不就行了？"

**大师**："pip install 当然可以装，但 Airflow 不是 pip install 完就能用的。它依赖一个元数据库，需要你提前准备好 PostgreSQL 或者 MySQL。还要初始化表结构、创建管理员账号、启动多个进程。你 pip install 完直接跑，大概率会看到各种 connection refused。"

**小白**："那直接用 Docker 不就行了？docker compose up 一把梭。"

**大师**："Docker Compose 确实是入门最快的路径，也是官方推荐的本地开发方式。但理解底层的配置逻辑很重要——你不可能一辈子靠 docker compose up。将来上了生产环境，你需要调数据库连接池、改 Executor 类型、配安全策略，这些都要回到 `airflow.cfg` 里来。"

**小胖**："所以既要会用 Docker 快速启动，又要理解配置文件？"

**大师**："对。我建议的学习路径是：先用 Docker Compose 跑起来建立感性认识 → 然后理解 `airflow.cfg` 的核心配置项 → 最后尝试裸机部署一次，把每个组件串起来。"

**小白**："那元数据库选什么好？我看文档说 SQLite 也行？"

**大师**："SQLite 只适合快速体验，绝对不能上生产。原因有两点：一是 SQLite 不支持并发写——假如你有两个 Scheduler 实例，或者 Webserver 和 Scheduler 同时操作数据库，就会报 database is locked。二是 Airflow 大量使用 SQLAlchemy 的 ORM，某些查询在 SQLite 上的性能和语义跟 PostgreSQL/MySQL 差异很大。生产环境我推荐 PostgreSQL——它是 Airflow 社区测试最充分的后端。"

**小胖**："那 Executor 呢？LocalExecutor 和 CeleryExecutor 有什么区别？"

**大师**："LocalExecutor 是一个进程内执行器，任务直接在 Scheduler 进程里 fork 子进程来跑。适合你一个人开发调试，或者团队很小、任务量不大的场景。CeleryExecutor 是企业级的，它通过 Redis 或 RabbitMQ 做消息队列，把任务分发给独立的 Worker 进程——这些 Worker 可以跑在不同的机器上，水平扩展。用物流打比方：LocalExecutor 是你自己骑电动车送快递，CeleryExecutor 是拥有全国网点的快递公司。"

> **技术映射**：Docker Compose 部署 = 买精装房（拎包入住），裸机部署 = 自己装修（理解每个细节），配置文件 = 精装房的电路图（出了问题得会看）。

---

## 3 项目实战

### 3.1 方式一：Docker Compose 快速启动（推荐入门）

**步骤目标**：一键启动完整 Airflow 集群，包含 Scheduler、Webserver、Dag Processor、Triggerer、PostgreSQL、Redis。

**步骤 1：创建项目目录**

```bash
mkdir ~/airflow-lab && cd ~/airflow-lab
mkdir -p dags logs plugins config
```

**步骤 2：获取 docker-compose.yaml**

```bash
# Airflow 3.x 的 Compose 文件
curl -LfO 'https://airflow.apache.org/docs/apache-airflow/3.0.0/docker-compose.yaml'
```

**步骤 3：设置环境变量**

创建 `.env` 文件：

```bash
# 宿主机上的 Dag/Logs/Plugins 目录映射
echo "AIRFLOW_UID=$(id -u)" > .env
```

> Windows 用户：直接创建 `.env` 文件，内容为 `AIRFLOW_UID=50000`

**步骤 4：初始化数据库与管理员账号**

```bash
docker compose up airflow-init
```

成功输出示例：
```
airflow-init-1  | [2025-01-15 10:00:00] INFO - Creating tables...
airflow-init-1  | [2025-01-15 10:00:05] INFO - Upgrades done
airflow-init-1  | Admin user airflow created
```

**步骤 5：启动所有服务**

```bash
docker compose up -d
```

**步骤 6：验证各组件状态**

```bash
docker compose ps
```

预期输出：
```
NAME                       STATUS              PORTS
airflow-postgres-1         Up (healthy)        5432/tcp
airflow-redis-1            Up (healthy)        6379/tcp
airflow-webserver-1        Up (healthy)        0.0.0.0:8080->8080/tcp
airflow-scheduler-1        Up (healthy)        8974-8978/tcp
airflow-dag-processor-1    Up (healthy)        
airflow-triggerer-1        Up (healthy)        
```

**可能遇到的坑**：

1. **端口冲突**：8080 被占用。解决：修改 `docker-compose.yaml` 中 webserver 的 ports 映射，如 `8081:8080`。

2. **内存不足**：所有容器加在一起约需 4GB 内存。解决：在 Docker Desktop 设置中增大内存限制，或注释掉不需要的组件（如 flower、airflow-worker）。

3. **Dag 文件不生效**：Dag Processor 默认 30 秒扫描一次。如果发现 Dag 没出现，先检查语法：`docker exec airflow-dag-processor-1 python -m py_compile /opt/airflow/dags/your_dag.py`。

### 3.2 方式二：理解 airflow.cfg 核心配置

**步骤目标**：深入理解 Airflow 的配置系统，能够在任意环境下正确配置。

**步骤 1：查看默认配置**

```bash
# 在容器中导出完整配置
docker exec airflow-webserver-1 airflow config list
```

**步骤 2：核心配置项解读**

以下是最关键的配置段（`airflow.cfg` 格式）：

```ini
[core]
# 元数据库连接字符串
# SQLite（仅开发）: sqlite:////opt/airflow/airflow.db
# PostgreSQL（生产推荐）: postgresql+psycopg2://user:pass@host:5432/airflow
sql_alchemy_conn = postgresql+psycopg2://airflow:airflow@postgres/airflow

# 执行器类型：LocalExecutor / CeleryExecutor / KubernetesExecutor
executor = CeleryExecutor

# Dag 文件存放目录
dags_folder = /opt/airflow/dags

# 插件目录
plugins_folder = /opt/airflow/plugins

# 任务并行度上限
parallelism = 32

# 单个 Dag 的最大并发 TaskInstance 数
max_active_tasks_per_dag = 16

# 单个 Dag 的最大并发 DagRun 数
max_active_runs_per_dag = 16

[scheduler]
# 调度器心跳间隔（秒）
scheduler_heartbeat_sec = 5

# Dag 文件最小处理间隔（秒）
min_file_process_interval = 30

# 是否捕获任务输出到日志
parsing_process_heartbeat_timeout = 300

[webserver]
# Web 服务端口
web_server_port = 8080

# RBAC 认证后端
auth_backend = airflow.api_fastapi.auth.backend.session

[logging]
# 日志存储位置
base_log_folder = /opt/airflow/logs

# 远程日志（可选：S3/GCS）
remote_logging = False
remote_base_log_folder = s3://my-bucket/airflow-logs/
```

**配置可通过环境变量覆盖**，这是容器化部署的关键特性：

```bash
# 环境变量命名规则：AIRFLOW__{SECTION}__{KEY}
export AIRFLOW__CORE__SQL_ALCHEMY_CONN=postgresql+psycopg2://user:pass@host/airflow
export AIRFLOW__CORE__EXECUTOR=CeleryExecutor
export AIRFLOW__WEBSERVER__SECRET_KEY=my-super-secret-key
```

### 3.3 方式三：裸机 pip 安装（深入理解）

**步骤目标**：在裸机上手工搭建 Airflow，理解每个组件的作用。

**步骤 1：安装 Python 虚拟环境**

```bash
python3 -m venv airflow-venv
source airflow-venv/bin/activate  # Windows: airflow-venv\Scripts\activate
```

**步骤 2：安装 Airflow**

```bash
# 安装 Airflow 3.x（含约束文件确保依赖兼容）
AIRFLOW_VERSION=3.0.0
PYTHON_VERSION="$(python3 --version | cut -d ' ' -f 2 | cut -d '.' -f 1-2)"
CONSTRAINT_URL="https://raw.githubusercontent.com/apache/airflow/constraints-${AIRFLOW_VERSION}/constraints-${PYTHON_VERSION}.txt"

pip install "apache-airflow==${AIRFLOW_VERSION}" --constraint "${CONSTRAINT_URL}"
```

**步骤 3：配置 PostgreSQL（必须）**

```bash
# 安装 PostgreSQL 并创建数据库
sudo apt install postgresql postgresql-client
sudo -u postgres createuser airflow -P  # 设置密码
sudo -u postgres createdb airflow -O airflow
```

**步骤 4：配置 airflow.cfg**

```bash
# 生成配置文件
mkdir ~/airflow
export AIRFLOW_HOME=~/airflow
airflow config list  # 会自动生成 ~/airflow/airflow.cfg

# 修改数据库连接
sed -i 's|sql_alchemy_conn = sqlite:///.*|sql_alchemy_conn = postgresql+psycopg2://airflow:pass@localhost/airflow|' ~/airflow/airflow.cfg
sed -i 's|executor = SequentialExecutor|executor = LocalExecutor|' ~/airflow/airflow.cfg
```

**步骤 5：初始化数据库并创建管理员**

```bash
airflow db migrate
airflow users create \
  --username admin \
  --password admin \
  --firstname Admin \
  --lastname User \
  --role Admin \
  --email admin@example.com
```

**步骤 6：启动各组件（分三个终端）**

```bash
# 终端 1：启动 Dag Processor（先于 Scheduler 启动）
airflow dag-processor

# 终端 2：启动 Scheduler
airflow scheduler

# 终端 3：启动 Webserver
airflow webserver -p 8080
```

---

## 4 项目总结

### 三种部署方式对比

| 维度 | Docker Compose | pip 裸机安装 | K8s Helm Chart |
|------|---------------|-------------|----------------|
| 启动速度 | 5 分钟 | 30 分钟 | 1 小时+ |
| 适合阶段 | 本地开发/学习 | 理解原理/小规模 | 生产/大规模 |
| 组件完整性 | 开箱全组件 | 需手动启动每个进程 | 全组件 + 自动扩缩 |
| 配置复杂度 | 中（需理解 yaml） | 高（全手工） | 高（K8s 知识 + values） |
| 可复现性 | 优秀 | 差 | 优秀 |

### 适用场景

1. **Docker Compose**：个人学习、本地开发、Demo 演示
2. **pip 裸机**：理解底层原理、特殊环境部署、CI 环境
3. **Helm Chart**：生产集群、多租户、需要弹性伸缩

### 注意事项

1. **永远记住 AIRFLOW_HOME**：Airflow 的所有配置文件、日志、Dag 文件都相对于这个目录
2. **不要用 root 运行容器**：官方镜像默认使用 `airflow` 用户（UID 50000），确保挂载目录的权限匹配
3. **数据库密码不要硬编码在 airflow.cfg**：用环境变量或 Secrets Backend 替代
4. **开发环境可以关闭 celery flower 等非必要组件**：省内存、启动快

### 常见踩坑经验

1. **"ModuleNotFoundError: No module named 'airflow'"**：pip 安装后没有激活正确的虚拟环境，或用了系统 Python。解决：检查 `which airflow` 是否指向虚拟环境路径。

2. **PostgreSQL 连接报"Ident authentication failed"**：PostgreSQL 默认 local 连接使用 ident 认证。解决：修改 `pg_hba.conf`，将 local 连接的认证方式改为 `md5`。

3. **Docker 中 Dag Processor 一直重启**：通常是挂载的 `dags/` 目录权限不对。解决：`chmod -R 755 dags/` 并确保 `.env` 中 `AIRFLOW_UID` 正确。

### 思考题

1. 如果你的团队有 5 个数据工程师，每人都有自己的开发环境，你会选择哪种部署方式？为什么？如何确保他们的环境配置一致？
2. `airflow.cfg` 中 `parallelism = 32` 和 `max_active_tasks_per_dag = 16` 有什么区别？如果一个大团队有 100 个 Dag，你会如何调整这两个参数？

*（答案将在下一章中揭晓）*

---

> **本章完成**：你已经掌握了三种 Airflow 部署方式，具备了从开发到生产的环境搭建能力。下一章我们将编写第一个真正的 Dag，感受用 Python 代码编排工作流的魅力。
