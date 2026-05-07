# 第25章：REST API 编程与 CLI 自动化

## 1. 项目背景

随着企业数据平台规模的不断扩张，某大型互联网公司的数据团队管理的 Airflow 集群已承载超过 2000 个 Dag，每日调度任务实例数突破 50 万。业务线涵盖实时数仓、离线 ETL、用户画像、推荐系统、风控模型等数十个场景，每个场景都有独立的 Dag 集合和调度策略。

然而，运维团队在日常工作中遇到了三个核心痛点。**第一，重复性操作耗时巨大。** 每逢月初业务变更窗口，运维人员需要通过 Web UI 逐一暂停上百个消费类 Dag，待上游数据产出完毕后，再逐一手动恢复——每次操作耗时 3-4 小时，且极易遗漏。**第二，告警响应依赖人工。** 当监控系统发现某批 Dag 连续失败后，开发人员需要登录服务器，手动执行 `airflow dags trigger`、`airflow tasks clear` 等一系列命令，凌晨告警的响应时间平均长达 25 分钟。**第三，缺乏自动化测试与部署闭环。** Dag 文件通过 Git 管理，但部署流程依赖运维手动上传并刷新，缺少 CI/CD 流水线的集成，导致 Dag 上线与验证之间存在较长的时间窗口。

针对上述痛点，平台架构师老张提出了"Airflow 运维自动化"专项，目标有三：(1) 通过 REST API 实现 Dag/任务实例的批量管理；(2) 基于 Airflow CLI 构建标准化运维脚本库；(3) 将 Dag 发布与验证集成到 GitHub Actions CI/CD 流水线中。本章将以该专项的真实落地过程为蓝本，系统讲述 Airflow REST API 编程与 CLI 自动化的核心技术。

> **技术选型提示：** Airflow 从 2.0 版本起提供了稳定的 REST API（默认路径 `/api/v1`，2.6+ 版本升级为 `/api/v2`），同时保留了功能完备的 CLI 工具。对于编程集成场景推荐使用 REST API + Python Client SDK；对于运维脚本场景推荐使用 CLI 命令配合 Shell/Python 封装。

---

## 2. 项目设计

**角色介绍：**
- **小胖**：数据平台后端开发工程师，3 年 Python 经验，熟悉 Airflow 基本使用，但对 API 编程和 CLI 自动化缺乏系统认知。
- **小白**：初级数据工程师，刚接手 Dag 运维工作，每天都为手动重启失败任务而头疼。
- **大师**：平台架构师老张，深耕 Airflow 3 年，主导过多次大规模集群迁移与自动化改造。

---

**场景一：从手动到 API 的思维转变**

周一晨会刚结束，小白就抱着一杯咖啡找到大师，脸上写满了疲惫。

"老师傅，救命啊！"小白把笔记本往桌上一摊，"凌晨 3 点线上 30 个 Dag 全挂了，我从床上爬起来挨个点 Web UI 重启，搞了快两小时。这活没法干了！"

大师微微一笑："你有没有想过，为什么每次都要手动去点 Web UI？"

小白一愣："因为……因为挂了就要重跑啊，不然呢？"

一旁的小胖插话道："小白，你登录 Airflow Web Server，打开 F12 控制台，随便点一个触发按钮，看看发了什么 HTTP 请求。"

小白照做，片刻后惊呼："竟然是一个 POST 请求！`POST /api/v1/dags/{dag_id}/dagRuns`！"

大师点头："没错。Airflow Web UI 本质上就是一个 REST API 的消费者。你在界面上做的每一次点击，底层都是 HTTP 请求。既然如此，为什么不让程序替你发这些请求呢？"

小胖若有所思："也就是说，我们可以用 Python 脚本批量调用这些 API？"

"不仅 Python，"大师展开白板，画了一张架构图，"任何能发 HTTP 请求的工
具都可以——curl、requests、甚至一个 Shell 脚本。但 Airflow 官方提供了两套更高级的工具：一套是 **Python Client SDK**，底层封装了 REST API 调用，让你可以直接用 Python 对象操作 Airflow 资源；另一套是 **Airflow CLI**，提供了 `airflow dags`、`airflow tasks`、`airflow dags backfill` 等命令，适合运维脚本和定时任务。"

---

**场景二：API 认证与安全设计**

"等等，"小胖作为后端开发，第一反应是安全，"REST API 不需要认证吗？谁都能调的话也太危险了吧。"

大师赞许地看了他一眼："问得好。Airflow REST API 支持三种认证方式。"

他继续在白板上写道：

| 认证方式 | 适用场景 | 配置方式 |
|---------|---------|---------|
| **Basic Auth** | 内部脚本、快速原型 | 设置 `[api] auth_backends = airflow.api.auth.backend.basic_auth` |
| **JWT Token (Bearer)** | 生产环境、跨服务调用 | 通过 `/api/v2/auth/token` 获取短期 Token |
| **Kerberos** | 企业 AD 域环境 | 配置 `kerberos` 认证后端 |

"最关键的是，**永远不要在代码中硬编码密码**，"大师加重了语气，"API 密钥应该从环境变量或密钥管理服务（如 HashiCorp Vault）中读取。"

小胖点头，在本子上记下这条铁律。

---

**场景三：设计批量操作方案**

"好，现在我们来设计具体的自动化方案。"大师拿出一张新的白板，"小白，你说说目前最痛的两个操作是什么？"

小白想了想："第一，每次有一批 Dag 失败，我需要逐个找到它们，清除失败的任务状态，然后重新触发。第二，每天下午 6 点，我要手动触发 50 个日报 Dag，光点鼠标就要半小时。"

"这里我给出两个设计思路，"大师一边画一边说，"第一个思路是**生产者-消费者模式**：主服务批量推送 Dag 触发请求到 Airflow API，然后轮询 DagRun 状态直到全部完成。第二个思路是**事件驱动模式**：利用 Airflow 的 Callback 机制，在每个 Dag 的失败处理函数中调用我们的告警/自动修复服务。"

小胖眼睛一亮："第二个思路更好！这样失败了自动触发修复，不需要人工介入。"

"但要落地需要两个方案配合，"大师补充，"初步阶段用生产者-消费者快速见效；长期再逐步迁移到事件驱动。今天我们先用阶段一的方案。"

---

**场景四：CI/CD 集成设计**

"还有一个需求，"大师翻出老张的需求文档，"我们要把 Dag 部署集成到 GitHub Actions 中。流程是：开发者 Push Dag 文件到 Git → CI 自动语法校验 → 部署到 Airflow 集群 → 自动触发冒烟测试 Dag。"

小胖仔细思考后提问："部署到 Airflow 集群这一步怎么做？"

"两种方案，"大师回答，"小团队可以直接把 Dag 文件放到 Airflow DAG 目录（配合 Git-Sync Sidecar）；大团队建议用 **Airflow REST API 的 Import DAGs 端点**进行远程部署。无论哪种方案，CI 流水线最终都需要调用 API 来触发冒烟测试。"

---

## 3. 项目实战

### 3.1 环境准备与 Python Client SDK 安装

```bash
# 安装 Airflow Python Client SDK
pip install apache-airflow-client

# 确认版本（建议与 Airflow Server 版本一致）
pip show apache-airflow-client
```

**SDK 版本兼容性表：**

| Airflow 版本 | 推荐 SDK 版本 | API 路径前缀 |
|-------------|-------------|-------------|
| 2.0 - 2.5   | 2.5.1       | `/api/v1`    |
| 2.6 - 2.9   | 2.8.1       | `/api/v2`    |
| 2.10+       | 2.10.0      | `/api/v2`    |

### 3.2 REST API 端点速览

Airflow v2 REST API 提供了完整的资源管理端点，以下是核心列表：

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/v2/dags` | GET | 获取所有 Dag 列表 |
| `/api/v2/dags/{dag_id}` | PATCH | 更新 Dag（如暂停/激活） |
| `/api/v2/dags/{dag_id}/dagRuns` | GET | 获取指定 Dag 的运行记录 |
| `/api/v2/dags/{dag_id}/dagRuns` | POST | 触发新的 DagRun |
| `/api/v2/dags/{dag_id}/dagRuns/{dag_run_id}` | PATCH | 更新 DagRun 状态（如标记失败） |
| `/api/v2/dags/{dag_id}/dagRuns/{dag_run_id}/taskInstances` | GET | 获取某次运行的所有任务实例 |
| `/api/v2/dags/{dag_id}/dagRuns/{dag_run_id}/taskInstances/{task_id}` | PATCH | 更新指定任务实例状态 |
| `/api/v2/dags/{dag_id}/dagRuns/{dag_run_id}/taskInstances/{task_id}/dependencies` | GET | 获取任务依赖数据 |
| `/api/v2/connections` | GET/POST | 管理 Connection |
| `/api/v2/variables` | GET/POST | 管理 Variable |
| `/api/v2/xcoms` | GET | 查询 XCom 数据 |
| `/api/v2/health` | GET | 健康检查端点 |

### 3.3 API 认证实战

#### 方式一：Basic Auth（适合开发环境）

```python
import requests
from requests.auth import HTTPBasicAuth

AIRFLOW_BASE_URL = "http://localhost:8080"
AIRFLOW_USERNAME = "admin"
AIRFLOW_PASSWORD = "admin"

response = requests.get(
    f"{AIRFLOW_BASE_URL}/api/v2/dags",
    auth=HTTPBasicAuth(AIRFLOW_USERNAME, AIRFLOW_PASSWORD),
)
print(f"Status: {response.status_code}")
print(f"Dags count: {response.json()['total_entries']}")
```

#### 方式二：JWT Token / Bearer Token（生产环境推荐）

```python
import requests
import os

AIRFLOW_BASE_URL = os.getenv("AIRFLOW_BASE_URL", "http://localhost:8080")
AIRFLOW_USERNAME = os.getenv("AIRFLOW_USERNAME", "admin")
AIRFLOW_PASSWORD = os.getenv("AIRFLOW_PASSWORD")

# 第1步：获取 JWT Token
auth_response = requests.post(
    f"{AIRFLOW_BASE_URL}/api/v2/auth/token",
    json={
        "username": AIRFLOW_USERNAME,
        "password": AIRFLOW_PASSWORD,
    },
)
token = auth_response.json()["access_token"]

# 第2步：使用 Bearer Token 调用 API
headers = {"Authorization": f"Bearer {token}"}
response = requests.get(
    f"{AIRFLOW_BASE_URL}/api/v2/dags",
    headers=headers,
)
print(f"Dags: {response.json()['total_entries']}")
```

> **安全提示：** JWT Token 默认有效期为 1 小时（可在 `webserver.py` 中配置 `JWT_ACCESS_TOKEN_EXPIRES`）。长时间运行的脚本应实现 Token 自动刷新机制。生产环境中，API 密码必须通过环境变量或 Secrets Manager 注入，严禁硬编码。

#### 封装可复用的认证客户端

```python
import requests
import os
import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class AirflowAPIClient:
    """可复用的 Airflow REST API 客户端，支持 JWT Token 自动刷新"""

    base_url: str = os.getenv("AIRFLOW_BASE_URL", "http://localhost:8080")
    username: str = os.getenv("AIRFLOW_USERNAME", "admin")
    password: str = os.getenv("AIRFLOW_PASSWORD", "admin")
    _token: Optional[str] = None
    _token_expiry: float = 0.0

    @property
    def headers(self) -> dict:
        if time.monotonic() > self._token_expiry:
            self._refresh_token()
        return {"Authorization": f"Bearer {self._token}"}

    def _refresh_token(self) -> None:
        resp = requests.post(
            f"{self.base_url}/api/v2/auth/token",
            json={"username": self.username, "password": self.password},
        )
        resp.raise_for_status()
        self._token = resp.json()["access_token"]
        self._token_expiry = time.monotonic() + 3300  # 55 分钟，留 5 分钟缓冲

    def get(self, path: str, **kwargs) -> requests.Response:
        return requests.get(f"{self.base_url}{path}", headers=self.headers, **kwargs)

    def post(self, path: str, **kwargs) -> requests.Response:
        return requests.post(f"{self.base_url}{path}", headers=self.headers, **kwargs)

    def patch(self, path: str, **kwargs) -> requests.Response:
        return requests.patch(f"{self.base_url}{path}", headers=self.headers, **kwargs)

    def delete(self, path: str, **kwargs) -> requests.Response:
        return requests.delete(f"{self.base_url}{path}", headers=self.headers, **kwargs)
```

### 3.4 Python Client SDK 编程实战

```python
from airflow_client.client import AirflowClient
from airflow_client.api import dag_api, dag_run_api, task_instance_api
from airflow_client.model.dag_run import DAGRun
from airflow_client.model.dag import DAG
import os

# 初始化 Client
client = AirflowClient(
    base_url=os.getenv("AIRFLOW_BASE_URL", "http://localhost:8080"),
    username=os.getenv("AIRFLOW_USERNAME", "admin"),
    password=os.getenv("AIRFLOW_PASSWORD", "admin"),
)

# 示例 1：获取所有 Dag 并筛选出失败的
dag_api_instance = dag_api.DAGApi(client)
all_dags = dag_api_instance.get_dags(limit=500)
for dag in all_dags.dags:
    print(f"  DAG: {dag.dag_id}, Paused: {dag.is_paused}")

# 示例 2：暂停指定的 Dag
dag_api_instance.patch_dag(
    dag_id="my_etl_pipeline",
    dag=DAG(is_paused=True),
    update_mask=["is_paused"],
)
print("Dag paused successfully.")

# 示例 3：触发 DagRun
dag_run_api_instance = dag_run_api.DAGRunApi(client)
dag_run = dag_run_api_instance.post_dag_run(
    dag_id="my_etl_pipeline",
    dag_run=DAGRun(
        logical_date="2026-05-07T00:00:00Z",
        conf={"priority": "high", "source": "api_trigger"},
    ),
)
print(f"Triggered DagRun: {dag_run.dag_run_id}, State: {dag_run.state}")

# 示例 4：查询 DagRun 的任务实例
ti_api = task_instance_api.TaskInstanceApi(client)
task_instances = ti_api.get_task_instances(
    dag_id="my_etl_pipeline",
    dag_run_id=dag_run.dag_run_id,
)
for ti in task_instances.task_instances:
    print(f"  Task: {ti.task_id}, State: {ti.state}")
```

### 3.5 Airflow CLI 自动化命令

Airflow CLI 是运维自动化的利器，以下为常用命令速查：

```bash
# 1. Dag 管理
airflow dags list                        # 列出所有 Dag
airflow dags list --subdir /path/to/dags # 列出指定目录的 Dag
airflow dags trigger my_etl_pipeline     # 触发 Dag
airflow dags trigger -c '{"date":"2026-05-07"}' my_etl_pipeline  # 带参数触发
airflow dags pause my_etl_pipeline       # 暂停 Dag
airflow dags unpause my_etl_pipeline     # 激活 Dag
airflow dags delete my_etl_pipeline      # 删除 Dag

# 2. 任务实例管理
airflow tasks list my_etl_pipeline                  # 列出 Dag 的所有任务
airflow tasks state my_etl_pipeline extract_data 2026-05-07  # 查询任务状态
airflow tasks clear my_etl_pipeline -s 2026-05-01 -e 2026-05-07  # 清除任务状态
airflow tasks clear --dag-regex '^report_.*' -s 2026-05-01 -e 2026-05-07  # 批量清除
airflow tasks failed-deps my_etl_pipeline 2026-05-07  # 检查失败依赖

# 3. 回填与历史数据修复
airflow dags backfill my_etl_pipeline -s 2026-04-01 -e 2026-05-01  # 回填历史数据
airflow dags backfill my_etl_pipeline --reset-dagruns -s 2026-04-01 -e 2026-04-07  # 重置后回填

# 4. 系统信息与健康检查
airflow info                             # 查看系统信息
airflow version                          # 查看 Airflow 版本
airflow config list                      # 查看当前配置
airflow db check                         # 检查数据库连接
airflow kerberos                         # Kerberos 认证
```

### 3.6 实战案例一：批量触发 50 个 Dag 并轮询完成状态

这是本章最核心的实战案例，模拟小白每天下午 6 点手动触发 50 个日报 Dag 的场景。

```python
"""
批量触发 Dag 并轮询完成状态的自动化脚本
适用场景：定时触发大批量报表/ETL Dag，并等待全部完成后发送通知
"""

import time
import requests
import os
import sys
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum


class DagRunState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


TERMINAL_STATES = {DagRunState.SUCCESS, DagRunState.FAILED}


@dataclass
class AirflowAPIClient:
    base_url: str = os.getenv("AIRFLOW_BASE_URL", "http://localhost:8080")
    username: str = os.getenv("AIRFLOW_USERNAME", "admin")
    password: str = os.getenv("AIRFLOW_PASSWORD", "admin")
    _token: Optional[str] = None
    _token_expiry: float = 0.0

    @property
    def headers(self) -> dict:
        if time.monotonic() > self._token_expiry:
            self._refresh_token()
        return {"Authorization": f"Bearer {self._token}"}

    def _refresh_token(self) -> None:
        resp = requests.post(
            f"{self.base_url}/api/v2/auth/token",
            json={"username": self.username, "password": self.password},
        )
        resp.raise_for_status()
        self._token = resp.json()["access_token"]
        self._token_expiry = time.monotonic() + 3300

    def get(self, path: str, **kwargs) -> requests.Response:
        return requests.get(f"{self.base_url}{path}", headers=self.headers, **kwargs)

    def post(self, path: str, **kwargs) -> requests.Response:
        return requests.post(f"{self.base_url}{path}", headers=self.headers, **kwargs)

    def patch(self, path: str, **kwargs) -> requests.Response:
        return requests.patch(f"{self.base_url}{path}", headers=self.headers, **kwargs)


@dataclass
class BatchDagTriggerResult:
    dag_id: str
    dag_run_id: Optional[str] = None
    final_state: Optional[DagRunState] = None
    error: Optional[str] = None


def trigger_dag(client: AirflowAPIClient, dag_id: str, logical_date: str, conf: dict) -> BatchDagTriggerResult:
    """触发单个 Dag 并返回 dag_run_id"""
    result = BatchDagTriggerResult(dag_id=dag_id)
    try:
        payload = {
            "logical_date": logical_date,
            "conf": conf,
        }
        resp = client.post(f"/api/v2/dags/{dag_id}/dagRuns", json=payload)
        resp.raise_for_status()
        result.dag_run_id = resp.json()["dag_run_id"]
        print(f"[OK] 触发 {dag_id} -> dag_run_id={result.dag_run_id}")
    except requests.HTTPError as e:
        result.error = f"HTTP {e.response.status_code}: {e.response.text}"
        print(f"[FAIL] 触发 {dag_id} 失败: {result.error}")
    except Exception as e:
        result.error = str(e)
        print(f"[FAIL] 触发 {dag_id} 异常: {e}")
    return result


def poll_dag_run(client: AirflowAPIClient, dag_id: str, dag_run_id: str,
                 timeout: int = 3600, poll_interval: int = 10) -> BatchDagTriggerResult:
    """轮询单个 DagRun 直到终态或超时"""
    result = BatchDagTriggerResult(dag_id=dag_id, dag_run_id=dag_run_id)
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        try:
            resp = client.get(f"/api/v2/dags/{dag_id}/dagRuns/{dag_run_id}")
            resp.raise_for_status()
            state = DagRunState(resp.json()["state"])
            if state in TERMINAL_STATES:
                result.final_state = state
                print(f"[{'OK' if state == DagRunState.SUCCESS else 'FAIL'}] {dag_id} -> {state.value}")
                return result
        except Exception as e:
            print(f"[WARN] 轮询 {dag_id}/{dag_run_id} 异常: {e}")
        time.sleep(poll_interval)

    result.error = f"超时 ({timeout}s)"
    print(f"[TIMEOUT] {dag_id} 轮询超时")
    return result


def batch_trigger_and_poll(dag_ids: list[str], logical_date: str, conf: dict,
                           timeout: int = 3600, poll_interval: int = 10) -> dict:
    """
    批量触发 Dag 并轮询至全部完成
    返回统计结果字典
    """
    client = AirflowAPIClient()

    # 第一阶段：批量触发
    print("=" * 60)
    print(f"阶段一：批量触发 {len(dag_ids)} 个 Dag...")
    trigger_results = []
    for dag_id in dag_ids:
        r = trigger_dag(client, dag_id, logical_date, conf)
        trigger_results.append(r)

    # 过滤掉触发失败的
    triggered = [r for r in trigger_results if r.dag_run_id is not None]
    print(f"\n成功触发: {len(triggered)}/{len(dag_ids)}")

    # 第二阶段：轮询状态
    print("\n" + "=" * 60)
    print(f"阶段二：轮询 {len(triggered)} 个 DagRun 状态...")
    final_results = []
    for r in triggered:
        result = poll_dag_run(client, r.dag_id, r.dag_run_id, timeout, poll_interval)
        final_results.append(result)

    # 合并触发失败的结果
    failed_triggers = [r for r in trigger_results if r.dag_run_id is None]
    final_results.extend(failed_triggers)

    # 统计
    success = [r for r in final_results if r.final_state == DagRunState.SUCCESS]
    failed = [r for r in final_results if r.final_state == DagRunState.FAILED or r.error]

    return {
        "total": len(final_results),
        "success_count": len(success),
        "failed_count": len(failed),
        "success_dags": [r.dag_id for r in success],
        "failed_dags": [{"dag_id": r.dag_id, "error": r.error} for r in failed],
    }


# ===== 主入口 =====
if __name__ == "__main__":
    # 模拟 50 个日报 Dag
    REPORT_DAGS = [f"report_daily_{i:02d}" for i in range(1, 51)]

    summary = batch_trigger_and_poll(
        dag_ids=REPORT_DAGS,
        logical_date="2026-05-07T00:00:00Z",
        conf={"trigger_source": "batch_automation", "priority": "normal"},
        timeout=1800,       # 单个 Dag 最长等待 30 分钟
        poll_interval=15,   # 每 15 秒轮询一次
    )

    # 输出汇总
    print("\n" + "=" * 60)
    print("批量执行结果汇总")
    print("=" * 60)
    print(f"总计: {summary['total']}")
    print(f"成功: {summary['success_count']}")
    print(f"失败/超时: {summary['failed_count']}")
    if summary["failed_dags"]:
        print("\n失败详情:")
        for item in summary["failed_dags"]:
            print(f"  - {item['dag_id']}: {item['error']}")

    sys.exit(0 if summary["failed_count"] == 0 else 1)
```

### 3.7 实战案例二：批量操作运维脚本

#### 3.7.1 自动暂停失败 Dag

```python
"""
自动发现并暂停连续失败的 Dag
配合 Cron 定时执行：*/10 * * * * python auto_pause_failed_dags.py
"""

import requests
import os
from collections import defaultdict

AIRFLOW_BASE_URL = os.getenv("AIRFLOW_BASE_URL", "http://localhost:8080")
USERNAME = os.getenv("AIRFLOW_USERNAME")
PASSWORD = os.getenv("AIRFLOW_PASSWORD")
FAILURE_THRESHOLD = 3  # 连续失败 3 次后自动暂停


def get_token():
    resp = requests.post(
        f"{AIRFLOW_BASE_URL}/api/v2/auth/token",
        json={"username": USERNAME, "password": PASSWORD},
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def auto_pause_failed_dags():
    token = get_token()
    headers = {"Authorization": f"Bearer {token}"}

    # 获取所有活跃 Dag
    resp = requests.get(
        f"{AIRFLOW_BASE_URL}/api/v2/dags",
        headers=headers,
        params={"limit": 500, "paused": False},
    )
    resp.raise_for_status()
    dags = resp.json()["dags"]

    for dag in dags:
        dag_id = dag["dag_id"]
        # 获取最近 10 次 DagRun
        runs_resp = requests.get(
            f"{AIRFLOW_BASE_URL}/api/v2/dags/{dag_id}/dagRuns",
            headers=headers,
            params={"limit": 10, "order_by": "-start_date"},
        )
        runs_resp.raise_for_status()
        runs = runs_resp.json()["dag_runs"]

        # 检查连续失败次数
        consecutive_failures = 0
        for run in runs:
            if run["state"] == "failed":
                consecutive_failures += 1
            else:
                break

        if consecutive_failures >= FAILURE_THRESHOLD:
            print(f"[PAUSE] {dag_id} 连续失败 {consecutive_failures} 次，自动暂停")
            requests.patch(
                f"{AIRFLOW_BASE_URL}/api/v2/dags/{dag_id}",
                headers=headers,
                json={"is_paused": True},
                params={"update_mask": ["is_paused"]},
            )


if __name__ == "__main__":
    auto_pause_failed_dags()
```

#### 3.7.2 批量清理过期 XCom 数据

```python
"""
批量清理过期 XCom 数据
XCom 数据默认不自动清理，长期累积会膨胀数据库
"""

import requests
import os
from datetime import datetime, timedelta, timezone

AIRFLOW_BASE_URL = os.getenv("AIRFLOW_BASE_URL", "http://localhost:8080")
CLEANUP_DAYS = 30  # 保留最近 30 天的 XCom 数据


def get_token():
    resp = requests.post(
        f"{AIRFLOW_BASE_URL}/api/v2/auth/token",
        json={"username": os.getenv("AIRFLOW_USERNAME"),
              "password": os.getenv("AIRFLOW_PASSWORD")},
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def batch_cleanup_xcom():
    token = get_token()
    headers = {"Authorization": f"Bearer {token}"}
    cutoff = datetime.now(timezone.utc) - timedelta(days=CLEANUP_DAYS)
    cutoff_str = cutoff.strftime("%Y-%m-%dT%H:%M:%S")

    # 获取所有活跃 Dag
    resp = requests.get(
        f"{AIRFLOW_BASE_URL}/api/v2/dags",
        headers=headers,
        params={"limit": 1000},
    )
    resp.raise_for_status()
    dags = resp.json()["dags"]

    total_deleted = 0
    for dag in dags:
        dag_id = dag["dag_id"]
        try:
            # 获取该 Dag 的 XCom 列表（Airflow 2.x 版本功能）
            xcom_resp = requests.get(
                f"{AIRFLOW_BASE_URL}/api/v2/dags/{dag_id}/dagRuns/../xcomEntries",
                headers=headers,
                params={"limit": 10000},
            )
            # 注意：不同 Airflow 版本的 XCom 清理端点可能不同
            # 生产环境建议使用 airflow db clean 命令或以数据库直接清理
        except Exception:
            continue

    # 更推荐的方式：使用 Airflow CLI 命令
    # airflow db clean --clean-before-timestamp "2026-04-07T00:00:00" --skip-archive

    print(f"清理完成，建议通过 CLI 执行: airflow db clean --clean-before-timestamp ...")


if __name__ == "__main__":
    batch_cleanup_xcom()
```

> **最佳实践：** XCom 清理更推荐使用 `airflow db clean` 命令，它在 Airflow 2.3+ 版本中提供，可安全清理过期元数据（包括 XCom、TaskInstance、DagRun 等），且支持 `--dry-run` 预览模式。

### 3.8 CI/CD 集成：GitHub Actions 自动部署 Dag

```yaml
# .github/workflows/deploy-dags.yml
name: Deploy Airflow Dags

on:
  push:
    branches:
      - main
    paths:
      - "dags/**"         # 仅 Dag 目录变更时触发
  workflow_dispatch:      # 支持手动触发

jobs:
  validate-and-deploy:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install dependencies
        run: |
          pip install apache-airflow==2.10.0 apache-airflow-client

      - name: Validate Dag syntax
        run: |
          python -c "
          import importlib.util, sys, os
          errors = []
          for root, dirs, files in os.walk('dags'):
              for f in files:
                  if f.endswith('.py'):
                      path = os.path.join(root, f)
                      try:
                          spec = importlib.util.spec_from_file_location('dag_module', path)
                          mod = importlib.util.module_from_spec(spec)
                          spec.loader.exec_module(mod)
                          for obj in vars(mod).values():
                              if hasattr(obj, 'dag_id'):
                                  print(f'  OK: {obj.dag_id}')
                      except Exception as e:
                          errors.append(f'{path}: {e}')
          if errors:
              print('Dag validation errors:')
              for err in errors:
                  print(f'  {err}')
              sys.exit(1)
          print(f'All {sum(1 for _ in os.listdir(\"dags\") if _.endswith(\".py\"))} Dag files validated.')
          "

      - name: Sync Dags to Airflow (via SCP or API)
        env:
          AIRFLOW_HOST: ${{ secrets.AIRFLOW_HOST }}
          AIRFLOW_USER: ${{ secrets.AIRFLOW_USER }}
          SSH_KEY: ${{ secrets.AIRFLOW_SSH_KEY }}
        run: |
          mkdir -p ~/.ssh
          echo "$SSH_KEY" > ~/.ssh/id_rsa
          chmod 600 ~/.ssh/id_rsa
          rsync -avz --delete dags/ $AIRFLOW_USER@$AIRFLOW_HOST:/opt/airflow/dags/

      - name: Trigger smoke test Dag
        env:
          AIRFLOW_BASE_URL: ${{ secrets.AIRFLOW_BASE_URL }}
          AIRFLOW_USERNAME: ${{ secrets.AIRFLOW_API_USER }}
          AIRFLOW_PASSWORD: ${{ secrets.AIRFLOW_API_PASSWORD }}
        run: |
          python .github/scripts/trigger_smoke_test.py

      - name: Poll smoke test result
        env:
          AIRFLOW_BASE_URL: ${{ secrets.AIRFLOW_BASE_URL }}
          AIRFLOW_USERNAME: ${{ secrets.AIRFLOW_API_USER }}
          AIRFLOW_PASSWORD: ${{ secrets.AIRFLOW_API_PASSWORD }}
        run: |
          python .github/scripts/poll_smoke_result.py
```

配套的冒烟测试触发脚本 `.github/scripts/trigger_smoke_test.py`：

```python
import requests, os, json, sys

BASE = os.getenv("AIRFLOW_BASE_URL")
USER = os.getenv("AIRFLOW_USERNAME")
PASS = os.getenv("AIRFLOW_PASSWORD")

# 获取 Token
resp = requests.post(f"{BASE}/api/v2/auth/token", json={"username": USER, "password": PASS})
resp.raise_for_status()
token = resp.json()["access_token"]
headers = {"Authorization": f"Bearer {token}"}

# 触发冒烟测试 Dag
smoke_dag = "smoke_test_deployed_dags"
payload = {"conf": {"deploy_commit": os.getenv("GITHUB_SHA", "unknown")}}
resp = requests.post(f"{BASE}/api/v2/dags/{smoke_dag}/dagRuns", headers=headers, json=payload)

if resp.status_code == 200:
    dag_run_id = resp.json()["dag_run_id"]
    print(f"Smoke test triggered: dag_run_id={dag_run_id}")
    # 将 dag_run_id 写入 GITHUB_OUTPUT 供后续 job 使用
    with open(os.getenv("GITHUB_OUTPUT"), "a") as f:
        f.write(f"dag_run_id={dag_run_id}\n")
else:
    print(f"Failed: {resp.status_code} {resp.text}")
    sys.exit(1)
```

### 3.9 常见错误与调试技巧

| 错误现象 | 可能原因 | 解决方案 |
|---------|---------|---------|
| `401 Unauthorized` | Token 过期或用户名密码错误 | 检查用户名密码，确认 Token 有效期配置 |
| `403 Forbidden` | 认证后端配置不正确 | 检查 `[api] auth_backends` 配置项 |
| `404 Not Found` | Dag 不存在或 API 路径错误 | 先用 `airflow dags list` 确认 Dag ID |
| `409 Conflict` | DagRun 的 `logical_date` 重复 | 使用唯一的 `logical_date` 或添加唯一 `run_id` |
| `Connection Refused` | Airflow Web Server 未启动或端口错误 | 检查 `airflow webserver` 进程状态 |
| Rate Limit | 请求频率过高 | 添加指数退避重试机制 |

---

## 4. 项目总结

### 4.1 技术方案对比

| 维度 | REST API 直接调用 | Python Client SDK | Airflow CLI | Web UI |
|------|------------------|-------------------|-------------|--------|
| **适用场景** | 跨语言集成、微服务调用 | Python 项目深度集成 | 运维脚本、Cron 任务 | 人工操作、调试 |
| **学习成本** | 中（需了解 HTTP 和端点） | 低（Python 原生接口） | 低（命令行即用） | 极低 |
| **批量操作** | 支持（需自行封装循环） | 支持 | 部分支持（如 `--dag-regex`） | 不支持 |
| **编程灵活性** | 高 | 高 | 中（依赖 CLI 参数能力） | 不支持 |
| **CI/CD 集成** | 最佳 | 最佳 | 良好（需安装 airflow 包） | 不适用 |
| **认证方式** | Basic / Bearer Token | Basic / Bearer Token | `airflow.cfg` 配置 | 登录页面 |
| **错误处理** | 需自行解析 HTTP 状态码 | SDK 封装了异常处理 | 依赖退出码和 stderr | 人工判断 |

### 4.2 核心要点

1. **REST API 是一切自动化的基石。** 无论是 Python SDK 还是第三方工具，底层都是通过 HTTP 协议与 Airflow Web Server 交互。掌握 REST API 的端点结构和认证机制，是通往高级自动化的必经之路。

2. **JWT Token 认证是生产环境的标配。** Basic Auth 虽然配置简单，但在分布式微服务架构中，Bearer Token 的短期性和无状态性更为安全可靠。务必从环境变量或 Secrets Manager 获取凭证，杜绝硬编码。

3. **Python Client SDK 是最高效的编程入口。** SDK 提供了与 API 端点 1:1 对应的 Python 方法，支持 IDE 自动补全和类型提示，大幅降低了开发成本。但要注意 SDK 版本必须与 Airflow Server 版本匹配。

4. **CLI 命令适合快速脚本和 Cron 调度。** 对于一次性运维操作（如 `airflow tasks clear`）和定时维护任务（如 `airflow db clean`），CLI 是最直接的工具，无需编写额外代码。

5. **CI/CD 集成打通了 Dag 从开发到上线的最后一公里。** 通过 GitHub Actions 实现 Dag 语法校验、自动部署和冒烟测试，将 Dag 变更的验证时间从小时级压缩到分钟级。

### 4.3 注意事项

- **API 速率限制：** Airflow Web Server 默认没有速率限制，但大量并发请求可能压垮服务器。批量操作时应控制并发数（建议不超过 10-20 个并发），并在请求间添加适当延迟。
- **Token 过期处理：** JWT Token 默认有效期 1 小时。长时间运行的脚本必须实现 Token 自动刷新机制，否则会在运行中途遇到 401 错误。
- **幂等性保证：** 触发 DagRun 的 API 要求 `logical_date` 在同一个 Dag 中是唯一的。批量触发时务必确保每个请求的 `logical_date` 不重复，否则会收到 409 冲突错误。
- **Dag 状态一致性：** 通过 API 暂停/激活 Dag 后，状态变更可能需要几秒才能在调度器中生效。在 CI/CD 流水线中，应在操作间加入适当的等待时间。
- **安全审计：** 所有 API 调用建议开启审计日志，记录操作人、操作时间、操作内容和结果。可通过 Airflow 的 `[logging]` 配置项实现。

### 4.4 思考题

**思考题一：** 在批量触发 50 个 Dag 的脚本中，我们采用了"先全部触发、再轮询"的两阶段策略。如果改为"每次只触发 5 个 Dag，等它们全部完成后再触发下一批"的流水线策略，在什么场景下更优？请从以下角度分析：(a) Airflow 集群资源利用率，(b) 下游数据依赖，(c) 失败隔离与重试效率。

**思考题二：** 当前 CI/CD 流水线中，Dag 文件通过 `rsync` 同步到 Airflow 服务器。如果团队有 100 个 Dag、每天合并 20 个 PR，这种"全量同步"策略会带来哪些问题？请设计一种"增量变更"方案，要求：(a) 只部署变更的 Dag 文件，(b) 部署后自动触发受影响的 Dag 进行验证，(c) 支持一键回滚到上一版本。

---

> **本章引用参考：**
> - Airflow REST API 官方文档：`https://airflow.apache.org/docs/apache-airflow/stable/stable-rest-api-ref.html`
> - Airflow CLI 官方文档：`https://airflow.apache.org/docs/apache-airflow/stable/cli-and-env-variables-ref.html`
> - Python Client SDK 文档：`https://github.com/apache/airflow-client-python`
> - Airflow 安全模型与认证：`https://airflow.apache.org/docs/apache-airflow/stable/security/security_model.html`
