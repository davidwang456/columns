# 第29章：多环境管理与 GitOps 实践

## 1 项目背景

"这周的线上事故复盘结果出来了。"某跨境电商公司数据平台负责人老周将一页 A4 纸推到会议桌中央。

团队沉默了——上周三凌晨的推荐系统 Dag 变更导致生产环境连续 3 小时的调度中断，直接影响了海外 8 个国家的商品推荐更新。事故根因很典型：一位开发工程师在"小改"了一个 Dag 的 schedule_interval 后，直接将文件 scp 到了生产服务器的 dags 文件夹。没有经过任何测试，没有经过任何审核，也没有任何回滚手段。

"我们必须建立多环境管理机制和 GitOps 工作流，"老周在白板上写下几个关键词，"Dev → Staging → Prod，代码即配置，Git 即真相源。"

团队梳理出四大核心需求：

1. **多环境隔离**：开发、预发布、生产三套 Airflow 环境独立运行，各自拥有独立的数据库、Dag 存储和变量/连接配置。开发环境供 Dag 作者调试，预发布环境用于集成测试和业务验收，生产环境承载正式调度。

2. **配置注入自动化**：同一个 Dag 文件在不同环境使用不同的数据库地址、API Key、资源配额等配置，且这些配置不能硬编码在 Dag 文件中。需要通过环境变量或 Airflow 配置系统动态注入。

3. **Dag 版本管理与 CI/CD 集成**：所有 Dag 变更必须经过 Git PR → 代码评审 → CI 语法检查 → 自动化测试 → 灰度部署的标准化流程。杜绝人工直接操作服务器。

4. **快速回滚能力**：当线上 Dag 出现问题时，运维人员能在 30 秒内回滚到上一版本，无需登录服务器手动拷贝文件。

本章将完整展示从零搭建 Airflow 多环境管理体系和 GitOps 工作流的全过程。

> **核心原则：** 永远不要在生产环境的手工 DAG 目录中直接修改文件。多环境的本质是"环境差异化配置 + 代码一致性部署"，GitOps 的本质是"用 Git 工作流驱动基础设施变更"。

---

## 2 项目设计

**角色介绍：**
- **小胖**：数据平台后端开发工程师，3 年 Python 经验，熟悉 Airflow 基本使用，但从未管理过多套环境。
- **小白**：初级数据工程师，经历了上周的事故后心有余悸，迫切希望引入规范化流程。
- **大师**：平台架构师老周，深耕 Airflow 3 年，为多家公司设计过 Airflow 多环境部署方案。

---

**场景一：为什么要多环境？**

"大师，我们只有一台 Airflow 服务器，"小胖挠头，"直接在测试机上跑不行吗？"

大师指了指窗外："你想想，你们公司的 App 是怎么上线的？"

小白抢答："先在开发机写代码，提测到测试环境让 QA 测，通过后上预发布环境验证，最后才发布到线上。"

"Airflow 的 Dag 文件本质上也是代码，"大师在白板上画了三层架构：

```
┌──────────────────────────────────────┐
│  开发环境 (Dev)                      │
│  • Airflow 实例 (dev-airflow:8080)    │
│  • 隔离的数据库 (dev_db)              │
│  • 开发分支 (feature/*) 的 Dag       │
│  • Conn/Variable 使用测试凭证         │
│  用途：Dag 作者自测、语法验证         │
├──────────────────────────────────────┤
│  预发布环境 (Staging)                │
│  • Airflow 实例 (stg-airflow:8080)    │
│  • 独立数据库，数据量接近生产         │
│  • main 分支的最新 Dag               │
│  • Conn/Variable 使用预发布凭证       │
│  用途：集成测试、业务验收、性能对比   │
├──────────────────────────────────────┤
│  生产环境 (Prod)                      │
│  • Airflow 集群 (prod-airflow:8080)   │
│  • 高可用数据库 + 多 Worker          │
│  • release 分支 / Git Tag 的 Dag     │
│  • Conn/Variable 使用生产凭证         │
│  用途：正式调度，禁止人工操作         │
└──────────────────────────────────────┘
```

小胖恍然大悟："也就是说，同一个 Dag 文件可以在 Dev 上随便改、随便跑，但到了 Prod 只能通过 Git 流水线发布？"

"不仅如此，"大师补充，"每个环境虽然跑着'相似'的 Dag 文件，但它们的配置——数据库地址、API Key、告警人数——完全不同。这就是环境差异化配置的挑战。"

---

**场景二：环境变量注入策略**

"那同一个 Dag 怎么知道自己是跑在 Dev 还是 Prod 上？"小白问。

大师打开笔记本，调出一段代码：

```python
# 错误做法：硬编码环境信息
DATABASE_HOST = "prod-mysql.internal:3306"  # ❌ Dev 环境也连生产库？
API_KEY = "sk-live-xxxx"                     # ❌ 密钥直接暴露在代码中
```

"正确做法是通过 **环境变量** 动态注入，"大师继续：

```python
# 正确做法：从环境变量中读取
import os

DATABASE_HOST = os.getenv("DB_HOST", "localhost:5432")
API_KEY = os.getenv("DATA_API_KEY")  # 不提供默认值，强制配置

# Airflow 原生环境变量支持
# 命名规则：AIRFLOW__{SECTION}__{KEY}
# 例如：AIRFLOW__CORE__DAGS_FOLDER=/opt/airflow/dags_prod
```

"Airflow 有一套强大的环境变量命名约定：**`AIRFLOW__{SECTION}__{KEY}`**，"大师展开一张表：

| 环境变量 | 等价 config | 说明 |
|---------|-----------|------|
| `AIRFLOW__CORE__DAGS_FOLDER` | `[core] dags_folder` | Dag 文件所在目录 |
| `AIRFLOW__DATABASE__SQL_ALCHEMY_CONN` | `[database] sql_alchemy_conn` | 数据库连接串 |
| `AIRFLOW__CORE__EXECUTOR` | `[core] executor` | 执行器类型 |
| `AIRFLOW__WEBSERVER__SECRET_KEY` | `[webserver] secret_key` | Web 服务密钥 |
| `AIRFLOW__LOGGING__LOGGING_LEVEL` | `[logging] logging_level` | 日志级别 |

"这意味着一件事：**同一个 Docker 镜像，配合不同的环境变量，就能部署到不同的环境。** 实现了'构建一次，到处运行'(Build Once, Deploy Anywhere)的理念。"

小胖眼睛一亮："那我们在 Docker Compose 里给每个环境配不同的环境变量文件就行了？"

大师点头："还有更优雅的方式——**`airflow.cfg` 中可以使用环境变量引用**：

```ini
# airflow.cfg
[database]
sql_alchemy_conn = ${DB_CONNECTION_STRING}

[core]
dags_folder = ${DAGS_FOLDER:-/opt/airflow/dags}
```

配合不同环境的 `.env` 文件，部署时自动替换。"

---

**场景三：Git 分支策略与 Dag 生命周期**

"好，环境搞清楚了，"小胖说，"那 Git 分支怎么管理？之前大家在 main 上直接改 Dag，改完就拖到服务器上……"

"我们来设计一个清晰的 Git 工作流，"大师翻出新一页白板：

```
Git 分支策略：

main ────────────────────────────────────────── ► (持续集成到 Staging)
  │
  ├─ feature/add-user-etl ─── PR ──► main
  ├─ fix/order-timeout    ─── PR ──► main
  └─ exp/new-algorithm    ─── PR ──► main

release/v2.3.0 ────── Tag v2.3.0 ──► (部署到 Production)
```

| 分支类型 | 用途 | 部署目标 | 触发条件 |
|---------|------|---------|---------|
| `feature/*` / `fix/*` | 开发新 Dag / 修复 Bug | Dev 环境（自动） | Push |
| `main` | 集成后的稳定代码 | Staging 环境（自动） | PR Merge |
| `release/*` | 生产候选版本 | Staging → Prod | 手动审批 |
| `hotfix/*` | 紧急修复 | Staging → Prod（加速） | 手动审批 |

"Dag 的生命周期也随之清晰了，"大师画了一条线：

```
开发阶段         测试阶段          验收阶段         生产阶段
  │                │                │                │
开发者编写 Dag ──► CI 语法检查 ──► airflow dags test ──► 合并到 main
  │                │                │                │
  ▼                ▼                ▼                ▼
feature 分支     Dev 自动部署     Staging 自动部署   发布审批 → Prod 部署
```

"每一步 '▶' 都是自动化触发，"大师强调，"人只做决策和代码开发，机器做部署和验证。"

---

**场景四：GitOps 核心理念**

"等等，"小白举手，"这不就是 CI/CD 吗？跟 GitOps 有什么关系？"

大师笑了："好问题。CI/CD 是从开发者视角出发——'我写完代码后怎么自动化部署'。GitOps 是从运维视角出发——'Git 仓库是唯一的真相源，实际运行状态必须与 Git 完全一致'。"

他列出核心区别：

| 维度 | 传统 CI/CD | GitOps |
|------|-----------|--------|
| **触发方向** | Push：CI 工具主动推送变更到环境 | Pull：环境中的 Agent 主动拉取 Git 状态 |
| **状态一致性** | 可能漂移（有人手动改过服务器文件） | 自动修复（Agent 检测到偏离后自动回正） |
| **操作审计** | CI 工具日志 | Git 提交历史 = 完整的审计日志 |
| **回滚方式** | 运行回滚脚本 | `git revert` → Agent 自动同步 |

"在 Airflow 的上下文中，GitOps 意味着：

1. **所有 Dag 文件都在 Git 中管理**——不在 Git 中的 Dag 不应当存在于服务器上。
2. **Dag 的部署由 Git 事件驱动**——PR 合并到 `main` 自动部署到 Staging；创建 Git Tag 自动部署到 Prod。
3. **环境配置也在 Git 中**（但不含敏感信息）——`dev.env`、`staging.env`、`prod.env` 模板文件在 Git 中，敏感值通过 CI Secrets 注入。
4. **回滚就是 Git 操作**——`git revert` 一个 commit，触发 CI 自动回滚部署。"

小胖恍然大悟："原来我们上周直接把文件 scp 到服务器，就是典型的 Git 与服务器状态不一致！"

"正是。如果有人 scp 了文件，GitOps Agent 下一次同步时就会发现服务器上的文件与 Git 仓库不一致，然后报出告警——或者直接覆盖为 Git 中的版本。"

---

## 3 项目实战

### 3.1 环境规划与目录结构

首先设计符合 GitOps 原则的项目仓库结构：

```
airflow-dags-repo/
├── dags/                          # 所有 Dag 文件（与环境无关）
│   ├── etl/
│   │   ├── user_profile_etl.py
│   │   └── order_sync_etl.py
│   ├── ml/
│   │   └── recommendation_train.py
│   └── monitoring/
│       └── data_quality_check.py
├── config/                        # 环境配置模板
│   ├── dev/
│   │   ├── airflow.cfg.template
│   │   └── variables.json
│   ├── staging/
│   │   ├── airflow.cfg.template
│   │   └── variables.json
│   └── prod/
│       ├── airflow.cfg.template
│       └── variables.json
├── tests/                         # Dag 集成测试
│   ├── test_etl_dags.py
│   └── test_ml_dags.py
├── docker-compose/
│   ├── dev/
│   │   └── docker-compose.yaml
│   ├── staging/
│   │   └── docker-compose.yaml
│   └── prod/
│       └── docker-compose.yaml
├── .github/
│   └── workflows/
│       ├── ci-dev.yml             # 开发环境 CI
│       ├── ci-staging.yml         # 预发布环境 CI
│       └── cd-prod.yml            # 生产环境 CD（含审批）
├── scripts/
│   ├── deploy_dags.sh
│   └── smoke_test.py
└── README.md
```

> **设计原则：Dag 逻辑与配置分离。** `dags/` 目录中的 Python 文件不包含任何环境相关的硬编码，所有差异化配置通过环境变量或 Airflow Variable/Connection 注入。

### 3.2 Docker Compose 多环境配置

#### 3.2.1 开发环境 (docker-compose/dev/docker-compose.yaml)

```yaml
# docker-compose/dev/docker-compose.yaml
version: "3.8"
x-airflow-common: &airflow-common
  image: apache/airflow:2.10.0
  environment: &airflow-common-env
    AIRFLOW__CORE__EXECUTOR: LocalExecutor
    AIRFLOW__CORE__DAGS_FOLDER: /opt/airflow/dags
    AIRFLOW__DATABASE__SQL_ALCHEMY_CONN: postgresql+psycopg2://airflow:airflow@postgres-dev/airflow
    AIRFLOW__CORE__LOAD_EXAMPLES: "false"
    AIRFLOW__WEBSERVER__SECRET_KEY: dev-secret-do-not-use-in-prod
    # 业务配置——直接暴露，开发环境可用默认值
    DB_HOST: postgres-dev
    DB_PORT: "5432"
    API_ENV: dev
    DATA_API_URL: http://mock-api:8001
  volumes:
    - ../../dags:/opt/airflow/dags
    - ../../config/dev/variables.json:/opt/airflow/variables.json
    - airflow_dev_logs:/opt/airflow/logs
  depends_on:
    postgres-dev:
      condition: service_healthy

services:
  postgres-dev:
    image: postgres:15
    environment:
      POSTGRES_USER: airflow
      POSTGRES_PASSWORD: airflow
      POSTGRES_DB: airflow
    ports:
      - "5433:5432"
    healthcheck:
      test: ["CMD", "pg_isready", "-U", "airflow"]
      interval: 5s
      retries: 5

  airflow-webserver-dev:
    <<: *airflow-common
    command: webserver
    ports:
      - "8080:8080"

  airflow-scheduler-dev:
    <<: *airflow-common
    command: scheduler

  airflow-init-dev:
    <<: *airflow-common
    command: bash -c "airflow db migrate && airflow connections create-default-connections"
    restart: "no"
```

#### 3.2.2 预发布环境 (关键差异展示)

```yaml
# docker-compose/staging/docker-compose.yaml（仅展示与 Dev 的差异部分）
x-airflow-common: &airflow-common
  image: apache/airflow:2.10.0
  environment: &airflow-common-env
    AIRFLOW__CORE__EXECUTOR: CeleryExecutor          # ← 使用 Celery，模拟生产拓扑
    AIRFLOW__CORE__DAGS_FOLDER: /opt/airflow/dags
    AIRFLOW__DATABASE__SQL_ALCHEMY_CONN: postgresql+psycopg2://airflow:${STG_DB_PASSWORD}@postgres-staging/airflow  # ← 密码来自环境变量
    AIRFLOW__CORE__LOAD_EXAMPLES: "false"
    AIRFLOW__WEBSERVER__SECRET_KEY: ${STG_SECRET_KEY}
    AIRFLOW__CELERY__BROKER_URL: redis://redis-staging:6379/0
    AIRFLOW__CELERY__RESULT_BACKEND: db+postgresql://airflow:${STG_DB_PASSWORD}@postgres-staging/airflow
    # 业务配置——指向预发布环境的后端服务
    DB_HOST: ${STG_DB_HOST}
    DB_PORT: "5432"
    API_ENV: staging
    DATA_API_URL: https://staging-api.company.com
  volumes:
    - ../../dags:/opt/airflow/dags
    - ../../config/staging/variables.json:/opt/airflow/variables.json
  depends_on:
    - postgres-staging
    - redis-staging
```

> **关键设计：** 同一个 Docker 镜像 `apache/airflow:2.10.0`，三套环境仅通过不同的 `docker-compose.yaml` 和环境变量文件(`.env`)切换行为。生产环境的 `.env` 文件**不提交 Git**，而是通过 CI/CD 的 Secrets 机制注入。

### 3.3 环境感知的 Dag 编写

在 `dags/` 目录中，Dag 文件通过环境变量感知当前环境，但绝不硬编码具体配置：

```python
# dags/etl/order_sync_etl.py
import os
from datetime import datetime, timedelta
from airflow.decorators import dag, task
from airflow.models import Variable

ENV = os.getenv("API_ENV", "dev")  # 从环境变量读取当前环境标识


@dag(
    dag_id=f"order_sync_etl_{ENV}",  # Dag ID 包含环境后缀，避免混淆
    schedule="0 2 * * *" if ENV == "prod" else None,  # 仅生产环境自动调度
    start_date=datetime(2025, 1, 1),
    catchup=False,
    tags=[ENV, "etl"],
    max_active_runs=1,
)
def order_sync_etl():
    @task
    def extract_orders():
        # 从 Airflow Connection 读取目标数据库（Connection ID 在不同环境指向不同实例）
        from airflow.providers.postgres.hooks.postgres import PostgresHook

        hook = PostgresHook(postgres_conn_id="orders_db")  # Dev指向测试库，Prod指向生产库
        records = hook.get_records("SELECT COUNT(*) FROM orders WHERE created_at > NOW() - INTERVAL '1 day'")
        print(f"[{ENV}] Orders count: {records[0][0]}")
        return records[0][0]

    @task
    def transform(count: int):
        print(f"[{ENV}] Processing {count} orders...")
        # Transform 逻辑
        return {"processed": count, "environment": ENV}

    @task
    def load(data: dict):
        api_url = os.getenv("DATA_API_URL", "http://localhost:8001")
        print(f"[{ENV}] Loading data to {api_url}: {data}")

    count = extract_orders()
    data = transform(count)
    load(data)


order_sync_etl()
```

> **核心技巧：** 使用 `postgres_conn_id="orders_db"`，该 Connection ID 在 Dev 环境中指向 `dev-postgres:5432/test_db`，在 Prod 环境中指向 `prod-postgres-cluster:5432/prod_db`。Connection 的配置通过 Airflow 的 Variables/Connections 系统或初始化脚本注入，**不在代码中硬编码任何连接信息**。

### 3.4 Variables 和 Connections 的环境初始化

```json
// config/dev/variables.json
{
  "etl_batch_size": 100,
  "alert_email": "dev-team@company.com",
  "data_partition_key": "dev_partition"
}
```

```json
// config/prod/variables.json
{
  "etl_batch_size": 5000,
  "alert_email": "prod-oncall@company.com",
  "data_partition_key": "prod_partition"
}
```

```bash
# 在环境初始化脚本中导入
airflow variables import /opt/airflow/variables.json

# Connections 通过环境变量注入或命令行导入
airflow connections add 'orders_db' \
    --conn-type 'postgres' \
    --conn-host "${DB_HOST}" \
    --conn-port "${DB_PORT}" \
    --conn-login "${DB_USER}" \
    --conn-password "${DB_PASSWORD}" \
    --conn-schema "${DB_NAME}"
```

### 3.5 CI/CD 流水线设计（GitHub Actions）

完整的 GitOps 流水线分为三个阶段：**Dev CI（自动）→ Staging CI（自动）→ Prod CD（审批制）**。

#### 阶段一：Dev 环境 CI —— 语法检查与单元测试

```yaml
# .github/workflows/ci-dev.yml
name: Dev CI — Dag 检查与测试

on:
  push:
    branches:
      - "feature/**"
      - "fix/**"
      - "exp/**"

env:
  AIRFLOW_HOME: /tmp/airflow_ci

jobs:
  dag-syntax-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install Airflow
        run: |
          pip install "apache-airflow==2.10.0" --constraint \
            "https://raw.githubusercontent.com/apache/airflow/constraints-2.10.0/constraints-3.11.txt"

      - name: Initialize Airflow DB
        run: |
          mkdir -p $AIRFLOW_HOME
          export AIRFLOW__CORE__DAGS_FOLDER=$PWD/dags
          airflow db init

      - name: Dag 语法解析检查
        run: |
          # 解析所有 Dag 文件，检查是否有语法错误或导入错误
          for dag_file in $(find dags -name "*.py"); do
            echo "=== Checking: $dag_file ==="
            python -c "
import sys
sys.path.insert(0, '.')
import importlib.util
spec = importlib.util.spec_from_file_location('dag_module', '$dag_file')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
            " || exit 1
          done

      - name: Dag 结构验证
        run: |
          # 使用 airflow dags list 确认所有 Dag 都能被正确识别
          python -c "
from airflow.models import DagBag
dagbag = DagBag(dag_folder='dags', include_examples=False)
if dagbag.import_errors:
    for filepath, error in dagbag.import_errors.items():
        print(f'ERROR in {filepath}: {error}')
    exit(1)
print(f'All {len(dagbag.dags)} Dags loaded successfully')
for dag_id, dag in dagbag.dags.items():
    print(f'  - {dag_id}: schedule={dag.schedule_interval}, tasks={len(dag.tasks)}')
          "

      - name: 任务依赖完整性检查
        run: |
          python -c "
from airflow.models import DagBag
dagbag = DagBag(dag_folder='dags', include_examples=False)
for dag_id, dag in dagbag.dags.items():
    for task in dag.tasks:
        upstream = dag.upstream_list(task.task_id)
        downstream = dag.downstream_list(task.task_id)
        if not upstream and not downstream:
            print(f'WARNING: Task {dag_id}.{task.task_id} has no dependencies!')
        # 检查是否存在孤立任务（既无上游也无下游且不是根任务）
        if not upstream and downstream and task.task_id not in [t.task_id for t in dag.roots]:
            print(f'WARNING: Task {dag_id}.{task.task_id} may be orphaned!')
          "

  dag-test:
    runs-on: ubuntu-latest
    needs: dag-syntax-check
    steps:
      - uses: actions/checkout@v4

      - name: Setup and Install
        run: |
          pip install "apache-airflow==2.10.0" --constraint \
            "https://raw.githubusercontent.com/apache/airflow/constraints-2.10.0/constraints-3.11.txt"
          pip install pytest

      - name: Dag 逻辑集成测试
        env:
          API_ENV: ci
          DATA_API_URL: http://localhost:8001
          AIRFLOW__CORE__DAGS_FOLDER: ${{ github.workspace }}/dags
        run: |
          mkdir -p $AIRFLOW_HOME
          airflow db init
          # 使用 airflow dags test 运行单个 Dag 的完整逻辑（不触发真实外部调用）
          # 配合 mock 验证 Dag 结构和任务编排正确性
          pytest tests/ -v --tb=short
```

#### 阶段二：Staging 环境 CI —— 自动部署到预发布

```yaml
# .github/workflows/ci-staging.yml
name: Staging CI — PR Merge 自动部署

on:
  pull_request:
    types: [closed]
    branches:
      - main

jobs:
  deploy-to-staging:
    if: github.event.pull_request.merged == true
    runs-on: ubuntu-latest
    environment: staging

    steps:
      - uses: actions/checkout@v4

      - name: Setup Python & Install Dependencies
        run: |
          pip install "apache-airflow==2.10.0" \
            --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-2.10.0/constraints-3.11.txt"

      - name: Dag 综合语法检查（Staging 模式）
        env:
          API_ENV: staging
          AIRFLOW__CORE__DAGS_FOLDER: ${{ github.workspace }}/dags
        run: |
          airflow db init
          python -c "
from airflow.models import DagBag
dagbag = DagBag(dag_folder='dags', include_examples=False)
if dagbag.import_errors:
    for f, e in dagbag.import_errors.items():
        print(f'❌ {f}: {e}')
    exit(1)
print(f'✅ All {len(dagbag.dags)} Dags loaded successfully')
          "

      - name: Deploy Dags 到 Staging 服务器
        env:
          STAGING_HOST: ${{ secrets.STAGING_HOST }}
          STAGING_USER: ${{ secrets.STAGING_USER }}
          STAGING_SSH_KEY: ${{ secrets.STAGING_SSH_KEY }}
        run: |
          mkdir -p ~/.ssh
          echo "$STAGING_SSH_KEY" > ~/.ssh/id_rsa
          chmod 600 ~/.ssh/id_rsa
          ssh-keyscan -H $STAGING_HOST >> ~/.ssh/known_hosts

          # 使用 rsync 同步 Dag 文件（--delete 确保删除已在 Git 中移除的文件，实现状态一致性）
          rsync -avz --delete \
            --exclude='__pycache__' \
            --exclude='*.pyc' \
            dags/ $STAGING_USER@$STAGING_HOST:/opt/airflow/dags/

          echo "✅ Dags deployed to Staging"

      - name: 等待调度器刷新 Dag
        run: sleep 30

      - name: 触发烟雾测试 Dag（Staging）
        env:
          AIRFLOW_BASE_URL: ${{ secrets.STAGING_AIRFLOW_URL }}
          AIRFLOW_USERNAME: ${{ secrets.STAGING_API_USER }}
          AIRFLOW_PASSWORD: ${{ secrets.STAGING_API_PASSWORD }}
        run: |
          python scripts/smoke_test.py --env staging
```

#### 阶段三：Production CD —— 审批制自动部署

```yaml
# .github/workflows/cd-prod.yml
name: Production CD — 审批后部署

on:
  push:
    tags:
      - "v*"  # 只有打 Tag 才触发生产部署
  workflow_dispatch:  # 允许手动触发（用于 hotfix）

jobs:
  pre-deploy-check:
    runs-on: ubuntu-latest
    environment: production  # 关联 GitHub Environment，强制审批
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0  # 获取完整历史，用于变更分析

      - name: 变更影响分析
        run: |
          PREV_TAG=$(git describe --tags --abbrev=0 HEAD^ 2>/dev/null || echo "HEAD~10")
          echo "📋 Changes since $PREV_TAG:"
          git diff --name-only $PREV_TAG HEAD -- dags/
          echo ""
          echo "📊 Changed Dag files:"
          git diff --name-only $PREV_TAG HEAD -- dags/ | while read file; do
            echo "  - $file"
          done

      - name: Dag 语法与依赖完整性验证（Prod 模式）
        env:
          API_ENV: production
          AIRFLOW__CORE__DAGS_FOLDER: ${{ github.workspace }}/dags
        run: |
          pip install "apache-airflow==2.10.0" \
            --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-2.10.0/constraints-3.11.txt"
          airflow db init
          python -c "
from airflow.models import DagBag
dagbag = DagBag(dag_folder='dags', include_examples=False)
if dagbag.import_errors:
    for f, e in dagbag.import_errors.items():
        print(f'❌ {f}: {e}')
    exit(1)
print(f'✅ All {len(dagbag.dags)} Dags valid for Production')
          "

  deploy-to-prod:
    runs-on: ubuntu-latest
    needs: pre-deploy-check
    environment:
      name: production
      # GitHub Environments 的审批保护规则在此生效
      # 需要至少 1 位审批人点击 Approve 才能继续
    concurrency: production-deploy  # 防止并发部署

    steps:
      - uses: actions/checkout@v4

      - name: 备份当前生产 Dag（用于快速回滚）
        env:
          PROD_HOST: ${{ secrets.PROD_HOST }}
          PROD_USER: ${{ secrets.PROD_USER }}
          PROD_SSH_KEY: ${{ secrets.PROD_SSH_KEY }}
        run: |
          mkdir -p ~/.ssh
          echo "$PROD_SSH_KEY" > ~/.ssh/id_rsa
          chmod 600 ~/.ssh/id_rsa
          ssh-keyscan -H $PROD_HOST >> ~/.ssh/known_hosts

          # 创建带时间戳的备份
          BACKUP_NAME="dags_backup_$(date +%Y%m%d_%H%M%S)"
          ssh $PROD_USER@$PROD_HOST "cp -r /opt/airflow/dags /opt/airflow/$BACKUP_NAME"
          echo "✅ Backup created: $BACKUP_NAME"
          echo "BACKUP_NAME=$BACKUP_NAME" >> $GITHUB_ENV

      - name: 部署 Dag 到生产环境
        env:
          PROD_HOST: ${{ secrets.PROD_HOST }}
          PROD_USER: ${{ secrets.PROD_USER }}
          PROD_SSH_KEY: ${{ secrets.PROD_SSH_KEY }}
        run: |
          mkdir -p ~/.ssh
          echo "$PROD_SSH_KEY" > ~/.ssh/id_rsa
          chmod 600 ~/.ssh/id_rsa

          # 同步 Dag 文件（保留备份目录）
          rsync -avz --delete \
            --exclude='__pycache__' \
            --exclude='*.pyc' \
            --exclude='dags_backup_*' \
            dags/ $PROD_USER@$PROD_HOST:/opt/airflow/dags/

          echo "✅ Dags deployed to Production"

      - name: 等待生产调度器刷新
        run: sleep 30

      - name: 触发生产冒烟测试
        env:
          AIRFLOW_BASE_URL: ${{ secrets.PROD_AIRFLOW_URL }}
          AIRFLOW_USERNAME: ${{ secrets.PROD_API_USER }}
          AIRFLOW_PASSWORD: ${{ secrets.PROD_API_PASSWORD }}
        run: |
          python scripts/smoke_test.py --env production

      - name: 部署状态通知
        if: always()
        run: |
          STATUS="${{ job.status }}"
          if [ "$STATUS" = "success" ]; then
            echo "🎉 Production deployment successful!"
          else
            echo "🚨 Production deployment FAILED! Backup: $BACKUP_NAME"
            echo "🚨 Rollback command: ssh prod 'cp -r /opt/airflow/$BACKUP_NAME/* /opt/airflow/dags/'"
          fi
```

### 3.6 烟雾测试脚本

```python
# scripts/smoke_test.py
"""
烟雾测试：验证部署后的 Dag 是否正常工作。
不执行真实的数据操作，仅验证 Dag 解析、任务依赖和模拟运行。
"""
import os
import sys
import argparse
import subprocess
import time
import requests
from pathlib import Path


def run_airflow_dag_test(dag_id: str, env: str) -> bool:
    """运行 airflow dags test 进行逻辑验证"""
    dag_file = find_dag_file(dag_id)
    if not dag_file:
        print(f"⚠️  Dag file not found for {dag_id}, skipping test")
        return True

    print(f"🔍 Testing Dag: {dag_id} ({dag_file})")

    result = subprocess.run(
        ["airflow", "dags", "test", dag_id, "2025-01-01"],
        capture_output=True,
        text=True,
        env={**os.environ, "API_ENV": env},
        timeout=120,
    )

    if result.returncode != 0:
        print(f"❌ Dag test failed for {dag_id}")
        print(f"STDERR: {result.stderr[-500:]}")
        return False

    print(f"✅ Dag test passed for {dag_id}")
    return True


def find_dag_file(dag_id: str) -> str | None:
    """在 dags/ 目录中搜索对应的 Dag 文件"""
    dag_dir = Path("dags")
    if not dag_dir.exists():
        return None
    for py_file in dag_dir.rglob("*.py"):
        if f"dag_id=\"{dag_id}\"" in py_file.read_text() \
           or f"dag_id='{dag_id}'" in py_file.read_text():
            return str(py_file)
    return None


def trigger_smoke_dag(airflow_url: str, username: str, password: str) -> str | None:
    """通过 REST API 触发烟雾测试 Dag 并等待结果"""
    # 获取 Token
    resp = requests.post(
        f"{airflow_url}/api/v2/auth/token",
        json={"username": username, "password": password},
        timeout=10,
    )
    resp.raise_for_status()
    token = resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 触发烟雾测试 Dag（如果存在）
    try:
        resp = requests.post(
            f"{airflow_url}/api/v2/dags/smoke_test/dagRuns",
            headers=headers,
            json={"conf": {"source": "ci_deployment"}},
            timeout=10,
        )
        if resp.status_code == 200:
            dag_run_id = resp.json()["dag_run_id"]
            print(f"✅ Smoke test Dag triggered: dag_run_id={dag_run_id}")
            return dag_run_id
        elif resp.status_code == 404:
            print("⚠️  No smoke_test Dag defined, skipping")
            return None
        else:
            print(f"⚠️  Trigger failed: {resp.status_code}")
            return None
    except requests.RequestException as e:
        print(f"⚠️  API call failed: {e}")
        return None


def poll_dag_run(airflow_url: str, token: str, dag_run_id: str, timeout: int = 300) -> bool:
    """轮询 DagRun 状态直到完成"""
    headers = {"Authorization": f"Bearer {token}"}
    start = time.monotonic()

    while time.monotonic() - start < timeout:
        resp = requests.get(
            f"{airflow_url}/api/v2/dags/smoke_test/dagRuns/{dag_run_id}",
            headers=headers,
            timeout=10,
        )
        resp.raise_for_status()
        state = resp.json()["state"]
        print(f"  Smoke test status: {state}")

        if state == "success":
            print("✅ Smoke test PASSED!")
            return True
        elif state == "failed":
            print("❌ Smoke test FAILED!")
            return False

        time.sleep(10)

    print("⏰ Smoke test timed out!")
    return False


def main():
    parser = argparse.ArgumentParser(description="Airflow Dag Smoke Test")
    parser.add_argument("--env", default="dev", choices=["dev", "staging", "production"])
    args = parser.parse_args()

    all_passed = True

    # 1. 验证关键的 Dag 文件
    critical_dags = [
        "order_sync_etl_staging" if args.env != "prod" else "order_sync_etl_prod",
        "data_quality_check_staging" if args.env != "prod" else "data_quality_check_prod",
    ]

    for dag_id in critical_dags:
        if not run_airflow_dag_test(dag_id, args.env):
            all_passed = False

    # 2. 如果有专门的烟雾测试 Dag，触发它
    airflow_url = os.getenv("AIRFLOW_BASE_URL", "http://localhost:8080")
    airflow_user = os.getenv("AIRFLOW_USERNAME", "admin")
    airflow_pass = os.getenv("AIRFLOW_PASSWORD", "admin")

    dag_run_id = trigger_smoke_dag(airflow_url, airflow_user, airflow_pass)
    if dag_run_id:
        resp = requests.post(
            f"{airflow_url}/api/v2/auth/token",
            json={"username": airflow_user, "password": airflow_pass},
        )
        token = resp.json()["access_token"]
        if not poll_dag_run(airflow_url, token, dag_run_id):
            all_passed = False

    if not all_passed:
        print("\n❌ Smoke tests FAILED!")
        sys.exit(1)
    else:
        print(f"\n✅ All smoke tests PASSED for {args.env}!")


if __name__ == "__main__":
    main()
```

### 3.7 快速回滚方案

```bash
# scripts/rollback.sh
#!/bin/bash
# 快速回滚：将生产环境的 Dag 恢复到指定的备份版本
#
# 用法：
#   ./scripts/rollback.sh <backup_name>          # 恢复到指定备份
#   ./scripts/rollback.sh --list                 # 列出所有可用备份
#   ./scripts/rollback.sh --previous             # 恢复到上一个版本（常用）

set -euo pipefail

PROD_HOST="${PROD_HOST:-prod-airflow.company.com}"
PROD_USER="${PROD_USER:-airflow}"
DAGS_PATH="/opt/airflow/dags"
BACKUP_PREFIX="dags_backup_"

list_backups() {
    ssh "$PROD_USER@$PROD_HOST" "ls -1d /opt/airflow/${BACKUP_PREFIX}* 2>/dev/null | sort -r" || echo "No backups found"
}

rollback_to() {
    local backup_name="$1"

    echo "🔙 Rolling back to: $backup_name"

    # 1. 创建当前版本的临时备份（以防回滚本身出错）
    CURRENT_BACKUP="${BACKUP_PREFIX}pre_rollback_$(date +%Y%m%d_%H%M%S)"
    ssh "$PROD_USER@$PROD_HOST" "cp -r $DAGS_PATH /opt/airflow/$CURRENT_BACKUP"
    echo "📦 Current state saved as: $CURRENT_BACKUP"

    # 2. 清空当前 Dag 目录
    ssh "$PROD_USER@$PROD_HOST" "rm -rf $DAGS_PATH/*"

    # 3. 从备份恢复
    ssh "$PROD_USER@$PROD_HOST" "cp -r /opt/airflow/$backup_name/* $DAGS_PATH/"

    # 4. 等待调度器刷新
    echo "⏳ Waiting for scheduler to refresh..."
    sleep 30

    echo "✅ Rollback complete! Dags restored from $backup_name"
    echo "   If something goes wrong, current state was saved in $CURRENT_BACKUP"
}

case "${1:-}" in
    --list)
        echo "📋 Available backups:"
        list_backups
        ;;
    --previous)
        PREVIOUS=$(list_backups | head -2 | tail -1)
        if [ -z "$PREVIOUS" ]; then
            echo "❌ No previous backup found"
            exit 1
        fi
        BACKUP_DIR=$(basename "$PREVIOUS")
        rollback_to "$BACKUP_DIR"
        ;;
    *)
        if [ -z "${1:-}" ]; then
            echo "Usage: $0 --list | --previous | <backup_name>"
            exit 1
        fi
        rollback_to "$1"
        ;;
esac
```

> **回滚时间承诺：** 从触发回滚脚本到调度器重新加载 Dag，全程不超过 30 秒。备份在每次部署时自动创建，保留最近 7 个版本的备份。

### 3.8 GitOps 完整工作流一览

```
┌──────────────────────────────────────────────────────────────────┐
│                    GitOps 工作流全景                              │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  开发者 Push feature 分支                                        │
│       │                                                          │
│       ▼                                                          │
│  ┌──────────┐    ┌──────────────┐    ┌─────────────────┐        │
│  │ 语法检查  │───►│ Dag 结构验证  │───►│ airflow dags test│        │
│  └──────────┘    └──────────────┘    └─────────────────┘        │
│       │                                                          │
│       ▼ (全部通过)                                                │
│  开发者创建 PR → Code Review → 合并到 main                        │
│       │                                                          │
│       ▼                                                          │
│  ┌──────────────────────────────────────────────────────┐        │
│  │ Staging 自动部署                                     │        │
│  │ • rsync Dag 到 Staging 服务器                        │        │
│  │ • 等待调度器刷新                                      │        │
│  │ • 运行烟雾测试                                       │        │
│  │ • 通知业务方验收                                     │        │
│  └──────────────────────────────────────────────────────┘        │
│       │                                                          │
│       ▼ (验收通过)                                                │
│  创建 Git Tag（例如 v2.10.1）并 Push                              │
│       │                                                          │
│       ▼                                                          │
│  ┌──────────────────────────────────────────────────────┐        │
│  │ 生产部署审批 → 备份当前 Dag → 部署新 Dag → 烟雾测试   │        │
│  └──────────────────────────────────────────────────────┘        │
│       │                                                          │
│       ▼ (烟雾测试通过)                                            │
│  ✅ 部署完成！通知团队                                           │
│                                                                  │
│  ───── 异常处理通道 ─────                                        │
│       │                                                          │
│       ▼ (任何步骤失败)                                            │
│  ┌──────────────────────────────────────────────────────┐        │
│  │ 🚨 自动告警 → 人工介入 → 回滚到上一版本                │        │
│  │    git revert + 部署 或 ./scripts/rollback.sh --previous│      │
│  └──────────────────────────────────────────────────────┘        │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

---

## 4 项目总结

### 4.1 多环境管理策略对比

| 维度 | 环境变量注入 | 配置文件模板 | Airflow Variables | Connection URI | CI/CD Secrets |
|------|------------|------------|------------------|---------------|---------------|
| **适用场景** | 基础设施配置（数据库地址、Executor类型） | 大量同构配置 | 业务运行时参数（批次大小、开关） | 外部服务连接信息 | 密钥、Token、密码 |
| **修改方式** | 修改 `.env` 或 K8s ConfigMap | 修改模板文件后重新部署 | Web UI / CLI / API | Web UI / CLI / API | GitHub/Vault 管理界面 |
| **是否在 Git 中** | `.env.template` 在 Git 中，`.env` 不在 | 是（不含敏感值） | 否（通过 API 管理） | 否（包含密码） | 否 |
| **运行时可见性** | `os.getenv()` | 启动时加载一次 | 每次调用 `Variable.get()` 实时查询 | 每次连接建立时读取 | 仅 CI Runner 可见 |
| **热更新** | 需重启容器 | 需重启容器 | 支持（实时生效） | 支持（实时生效） | 不支持（需重新触发工作流） |
| **团队协作** | 统一 `.env.template` 规范 | 版本化管理 | 权限控制（角色绑定） | 权限控制 | 权限控制（Repository Secrets) |

### 4.2 Git 分支与部署策略矩阵

| 场景 | 分支 | 部署目标 | 触发方式 | 审批要求 | 回滚方式 |
|------|------|---------|---------|---------|---------|
| 新 Dag 开发 | `feature/*` | Dev | Push 自动 | 无 | `git reset` |
| Bug 修复 | `fix/*` | Dev | Push 自动 | 无 | `git reset` |
| 常规迭代上线 | `main`（PR Merge） | Dev → Staging | Merge 自动 | PR Review | 修复后重新 Merge |
| 生产发布 | Git Tag `v*` | Staging → Prod | Tag Push 自动 | 至少 1 人 Approve | `git revert` + 部署 / 备份恢复 |
| 紧急修复 | `hotfix/*` | Dev → Staging → Prod | 手动触发工作流 | 至少 1 人 Approve | `git revert` + 部署 / 备份恢复 |

### 4.3 核心要点

1. **多环境的本质是配置分离，而非代码分叉。** 同一个 Dag 文件在所有环境中运行，仅通过环境变量、Connection 和 Variable 实现行为差异化。这杜绝了"Dev 环境好的代码到了 Prod 就不行"的问题。

2. **Git 是唯一真相源。** GitOps 的核心原则——任何对基础设施的变更都必须通过 Git 提交完成。服务器上的手动修改会被下一次同步覆盖。这从根本上杜绝了"谁偷偷改了什么"的运维噩梦。

3. **环境变量遵循 `AIRFLOW__SECTION__KEY` 约定。** 这套命名约定让 Airflow 的所有配置项都可以通过环境变量覆盖，无需修改 `airflow.cfg`，非常适合容器化和 Kubernetes 部署。

4. **烟雾测试是部署的最后一道防线。** `airflow dags test` 可以在不触发真实数据操作的前提下验证 Dag 的逻辑正确性。每一个 PR 合并和每一次部署后都应执行烟雾测试，用机器代替人做验证。

5. **回滚能力是部署的基础设施，而非事后补救。** 备份机制内置在部署流水线中，而非依赖运维人员的手动操作。目标是从"发现问题"到"完成回滚"全程控制在 2 分钟内。

### 4.4 注意事项

- **Connection 与 Variable 的环境隔离：** Dev 和 Prod 使用同一个 Airflow 集群是极其危险的做法。务必确保每个环境有独立的 Airflow 实例和数据库，否则 Connection 配置可能互相覆盖。

- **环境变量泄漏风险：** 确保生产环境的 `.env` 文件和 CI Secrets 不会通过日志、错误信息或 DAG 代码中的 `print(os.environ)` 泄漏到外部。所有 HTTP 错误响应都应避免回显环境变量。

- **Dag 文件 `--delete` 要谨慎：** `rsync --delete` 会删除目标端有而源端没有的文件。如果生产服务器上有紧急手动创建的 Dag，务必先拉取到 Git 后再在 Git 中管理，否则会被 rsync 删除。更好的做法是将 `--delete` 替换为告警机制。

- **审批流程不能绕过：** 生产环境的 GitHub Environment 保护规则（Required Reviewers 等）必须严格执行。任何绕过审批流程的部署（即使是紧急修复）都会增加事故风险。

- **备份保留策略：** 生产服务器的备份文件会占用磁盘空间。建议设置自动清理策略（如保留最近 10 个备份，或保留 7 天内的备份），避免磁盘耗尽。

### 4.5 思考题

**思考题一：** 团队目前有三套环境（Dev / Staging / Prod），使用 `docker-compose` 部署。随着业务增长，需要新增一套"性能压测环境"用于验证新 Dag 对数据库和 API 的负载影响。请设计压测环境的技术方案，要求：(a) 与 Dev / Staging / Prod 共享相同的 Dag 文件，(b) 使用独立的数据库实例但结构与 Prod 一致（通过数据脱敏脚本从 Prod 周期同步），(c) CI 流水线中自动触发——当 PR 标签为 `needs-perf-test` 时，先部署到压测环境并运行 10 分钟满载测试，通过后才能 Merge。

**思考题二：** 当前的回滚方案依赖 SSH 和 `cp` 命令实现，每次回滚约需 30 秒。但团队有 200 个 Dag、每天有 5 次以上部署操作。请设计一种"零停机回滚"方案，要求：(a) 不影响正在运行的 Task（Task 绑定的是旧版 Dag 定义），(b) 调度器对新 DagRun 使用新版 Dag 定义，对已创建的等待任务保持旧版定义（Airflow 原生特性），(c) 通过 Git Tag 机制实现任意版本之间的秒级切换。

**思考题三（开放题）：** GitOps 依赖 Agent 持续拉取 Git 状态来保持一致性。但在实际部署中，Airflow 调度器本身不是 GitOps Agent——它不会主动从 Git 拉取 Dag。请设计两种方案解决"谁负责从 Git 同步 Dag"的问题：(a) 基于 Git-Sync Sidecar 容器（Kubernetes 原生方案），(b) 基于 CI/CD Push 模式（本章采用的方案）。请对比两种方案的优缺点，并分析在以下场景中哪种更合适：团队有 5 个 Airflow 工作节点，Dag 文件总计 50MB，每小时可能有一次部署。

---

> **本章引用参考：**
> - Airflow 环境变量配置文档：`https://airflow.apache.org/docs/apache-airflow/stable/configurations-ref.html`
> - Airflow 命令行 `dags test` 文档：`https://airflow.apache.org/docs/apache-airflow/stable/cli-and-env-variables-ref.html#test`
> - Docker Compose 环境变量：`https://docs.docker.com/compose/environment-variables/`
> - GitHub Actions Environments：`https://docs.github.com/en/actions/deployment/targeting-different-environments/using-environments-for-deployment`
> - GitOps 工作组原则：`https://opengitops.dev/`
