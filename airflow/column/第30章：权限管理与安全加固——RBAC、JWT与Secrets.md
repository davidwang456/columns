# 第30章：权限管理与安全加固——RBAC、JWT与Secrets

## 1. 项目背景

某金融科技公司的数据平台日均调度超过 10000 个任务实例，涉及用户画像、风控建模、反洗钱、监管报送等数十条业务线。随着公司通过 SOC2 Type II 审计，安全团队对 Airflow 平台提出了一组硬性合规要求："所有组件间通信必须启用 TLS 双向认证；Dag 作者不得直接访问元数据库；敏感凭证（数据库密码、API Key等）必须从外部密钥管理服务动态获取，不得明文存储在配置文件或环境变量中；所有 API 操作必须可审计，日志保留不少于 90 天。"

平台架构师老张面对这份安全整改清单，概括出四个核心战场。**第一，RBAC 权限模型需要从"全员 Admin"细化到 Admin、Op、User、Viewer 四级角色。** 之前团队为了方便，给所有接入用户分配了 Admin 角色——这意味着任何一个开发者都能查看和修改任何人的 Connection 凭证，甚至可以直接删除生产 Dag。**第二，JWT Token 认证在 Core API 和 Execution API 之间存在本质差异。** Core API 面向用户和外部系统，Token 生命周期为 24 小时，支持吊销机制；Execution API 面向 Worker 进程内部通信，Token 仅 10 分钟有效期，不设吊销。如果混淆两者，要么用户 Token 过短导致频繁登录，要么 Worker Token 过长带来安全隐患。**第三，Secrets Backend 是凭证管理的最后一道防线。** 当前所有 Connection 密码都用 Fernet 加密后存储在数据库 metadata 表中——一旦数据库被攻破，攻击者可利用数据库内存储的 Fernet Key 解密所有凭证。生产环境必须将凭证下沉到 HashiCorp Vault、AWS Secrets Manager 或 GCP Secret Manager 等专业密钥管理系统。**第四，网络安全层面需要补齐组件间 TLS、API 速率限制和 CORS 配置。**

> **架构提示：** Airflow 3 在安全模型上做了一次分水岭式的升级。Worker 任务进程被彻底剥夺了元数据库的直接访问权限，仅能通过 Execution API 与服务端通信；Dag File Processor 和 Triggerer 虽然仍留有软保护级别的数据库通道，但 Airflow 社区正在推进 Unix 用户级隔离和全线 API 化通信的战略计划。本章内容基于 Airflow 3.x 架构展开。

---

## 2. 项目设计

**角色介绍：**
- **小胖**：数据开发工程师，4 年 Python 经验，擅长写 SQL 和 Dag，但安全意识停留在"把密码放环境变量就行"的水平。
- **小白**：DevOps 工程师，刚接手 Airflow 集群的安全加固任务，对 RBAC 和 JWT 一知半解，求知欲极强。
- **大师**：平台架构师老张，10 年分布式系统经验，主导过多次安全审计和架构升级。

---

**场景一：RBAC 权限模型的颗粒度之争**

周一下午，小白抱着笔记本冲进大师的工位，屏幕上赫然显示着一行错误日志：`403 Forbidden — User 'data_intern' lacks permission 'can_edit on DAGs'`。

"老师傅，实习生小明刚才想暂停一个跑飞了的 Dag，结果被拒了。我们是不是权限配错了？"小白焦急地问。

一旁的小胖从零食堆里探出头来："这不简单嘛，给他 Admin 权限不就行了？我们之前不都是这么干的？"

大师放下手中的咖啡，摇了摇头："小胖，你这种'全员 Admin'的思路，就像是把公司所有办公室的门禁卡都刷成全通卡——方便是方便，但一旦一张卡丢了，整栋楼都没有安全可言。"

小白若有所思："那正确的做法是什么？像银行柜台一样，不同角色只能做对应的事？"

"说对了。"大师在白板上画出四行字：

```
Admin    → 管理用户、角色、权限，查看审计日志，全 Dag 读写
Op       → 与 Admin 几乎相同，但不能管理用户和查看审计日志
User     → 查看、触发、编辑 Dag，查看任务日志，读写 XCom
Viewer   → 纯只读，只能看 Dag 运行状态和日志，不能修改任何东西
```

大师继续解释："Airflow 3 的 RBAC 还有两个更细的角色。一个是 **Connection Configuration** 角色——他们可以配置 Connection，但无法查看敏感凭证的明文（Airflow 3 起 API 层面做了 Masking）。另一个是 **Audit Log** 角色——专门负责查看全平台的审计日志。比 Airflow 2 进步的是，敏感信息在 API 和 UI 层面都被掩码了，不再是裸奔状态。"

小胖挠挠头："那不同角色怎么映射到具体的 Dag 资源上呢？比如实习生小明可以操作 `daily_report` 这个 Dag，但不能碰 `fraud_detection`？"

大师赞许地点头："这就是 Dag-Level Access Control。RBAC 的权限可以按资源粒度收缩——你可以给一个角色赋予 `can_read on DAG: daily_report` 的权限，同时拒绝其对 `fraud_detection` 的访问。这背后是 Auth Manager（如 FAB Auth Manager）通过 `filter_authorized_dag_ids` 方法实现的资源级过滤。"

> **技术映射：** RBAC = Role-Based Access Control。角色（Role）是权限的集合；权限（Permission） = 动作（Action，如 can_read、can_edit、can_delete）+ 资源（Resource，如 DAG、Connection、Variable、Audit Log）；用户（User）被分配一个或多个角色。评估链路：User → Role → Permission → Resource。

---

**场景二：两种 JWT Token 的天壤之别**

"RBAC 我理解了，"小白翻开笔记本，"但上次做 API 自动化的时候，第 25 章讲的是用一个 `/auth/token` 端点拿到 JWT Token，然后在请求头里带上 `Authorization: Bearer <token>`。这次我又听说 Execution API 也有 JWT，它们是一回事吗？"

大师眼睛一亮："这是一个非常好的问题，90% 的新手都会搞混。虽然底层都是 JWT，但两者在设计和用途上完全不同。"

他画了一张对比表：

| 维度 | Core API Token | Execution API Token |
|------|---------------|-------------------|
| **受众** | UI 用户、CLI 工具、外部系统 | Worker 进程、内部组件 |
| **签发方** | API Server（通过 Auth Manager） | Scheduler |
| **Subject（sub）** | 用户标识（user_id） | 任务实例 UUID（task_instance_id） |
| **Scope（scope）** | 无 | `workload` / `execution` |
| **有效期** | 24 小时（可配置） | workload: ~600s；execution: 600s（10 分钟） |
| **吊销机制** | 有（存入 `revoked_token` 表） | 无（短生命周期天然过期） |
| **Audience（aud）** | 可配置 | `urn:airflow.apache.org:task`（固定） |

小胖插嘴道："等等，那 `ti:self` 是什么？我在源码注释里看到过。"

"这是 Execution API 最精巧的设计之一。"大师在白板上画了一条链路：

```
Scheduler 签发 workload Token → Executor 投递到 Worker
→ Worker 调用 POST /execution/run（携带 workload Token）
→ API Server 验证后下发 execution Token
→ Worker 后续所有请求使用 execution Token
→ JWTReissueMiddleware 在 Token 剩余 <20% 时自动刷新
```

"`ti:self` 的意思是：当 Worker 访问 `/execution/task-instances/{ti_id}/heartbeat` 这类端点时，服务端会校验 Token 的 `sub` 字段（即 Task Instance UUID）是否与 URL 中的 `ti_id` 一致。如果不一致，直接 403。这确保了 Worker A 不能冒充 Worker B 去操作别的任务实例。"

小白突然警觉："那如果整个集群共用同一个对称密钥（jwt_secret），一个 Worker 拿到了密钥岂不是可以伪造任意任务的 Token？"

"完全正确。这就是为什么生产环境推荐用非对称密钥。"大师补充道，"配置 `jwt_private_key_path` 指向 RSA 或 Ed25519 私钥，只有 Scheduler 持有私钥签名，API Server 通过 JWKS 公钥验证——Worker 永远拿不到签名能力。"

> **技术映射：** Core API Token = 长期用户身份凭证（类比签证护照，有效期长，可吊销）；Execution API Token = 短期任务令牌（类比酒店房卡，进出即弃）。`ti:self` Scope = 房卡上印了房号，拿到卡也只能进自己的房间。

---

**场景三：Secrets Backend —— 凭证管理的最后防线**

"好，RBAC 管住了用户权限，JWT 管住了 API 认证。那密码本身放哪里？"小胖提出了第三个关键问题。

小白抢答："放数据库里啊，Airflow 不是用 Fernet Key 加密存储 Connection 密码吗？"

大师摇了摇头，神情认真起来："Fernet 加密只是**静态加密**（Encryption at Rest）。它的密钥 `FERNET_KEY` 如果也放在配置文件或环境变量里，那和一个保险柜把钥匙插在门上没有区别。一旦数据库被拖库，攻击者拿到加密密文和 Fernet Key，就能解密所有密码。"

他展开一张新的白板：

```
┌──────────────────────────────────────────────────┐
│             Secrets Backend 搜索链                │
│  Connection 查找顺序（由高到低优先级）：            │
│  1. 环境变量（AIRFLOW_CONN_<CONN_ID>）            │
│  2. Metastore 数据库（Fernet 加密）               │
│  3. [secrets] backend 配置的外部密钥服务 ──→ Vault │
│         └─ 优先级可叠加，后者覆盖前者              │
└──────────────────────────────────────────────────┘
```

```ini
# airflow.cfg — 服务端 Secrets Backend 默认搜索路径
[secrets]
backend = airflow.providers.hashicorp.secrets.vault.VaultBackend
backend_kwargs = {"connections_path": "connections", "url": "https://vault.internal:8200", "mount_point": "airflow", "kv_engine_version": 2, "auth_type": "token", "token": "<vault-token>"}
```

"VaultBackend 拿到的 Connection 会以 JSON 形式存储在 Vault 的 `airflow/connections/<conn_id>` 路径下，字段包括 `conn_type`、`host`、`login`、`password`、`extra`。Airflow 在运行时按需从 Vault 拉取，凭证不出数据库。"

小白追问："Worker 进程怎么访问 Vault？Worker 没有数据库权限，但需要 Connection 来执行任务。"

"Worker 有两种路径获取敏感信息。第一，通过 Execution API（`GET /execution/variables/{key}`、`GET /execution/connections/{conn_id}`）——API Server 代为查询 Secrets Backend，Worker 只需要提供 JWT Token。第二，如果配置了 `[workers] secrets_backend_kwargs`，Worker 可以直接访问配置的 Secrets Backend。生产环境推荐前者，这样 Worker 完全不需要 Vault 的直接访问权限。"

> **技术映射：** Metastore（数据库）存储 = 把金银首饰锁在卧室抽屉里；Fernet Key 在同一个房间 = 钥匙放在枕头下；Secrets Backend = 把最值钱的珠宝存进银行保险库（Vault/AWS/GCP），卧室里只留一个取件凭证。

---

## 3. 项目实战

### 3.1 环境准备

本次实战基于以下环境：

- **Airflow 3.x**（任意 3.x 版本，本章以 3.1 为例）
- **HashiCorp Vault**（开发模式足够用于本地实验）
- **Python 3.11+**
- **操作系统**：Linux/macOS/Windows WSL2

```bash
# 1. 安装 Airflow 核心 + HashiCorp Provider
pip install apache-airflow==3.1.0
pip install apache-airflow-providers-hashicorp

# 2. 本地启动 Vault 开发模式（dev 模式会将数据存在内存，重启即丢）
vault server -dev -dev-root-token-id="root-token" -dev-listen-address="0.0.0.0:8200"

# 3. 另开终端，配置 Vault 客户端环境
export VAULT_ADDR="http://127.0.0.1:8200"
export VAULT_TOKEN="root-token"

# 4. 启用 KV v2 引擎并写入测试数据
vault secrets enable -path=airflow kv-v2
vault kv put airflow/connections/my_postgres \
    conn_type="postgres" \
    host="10.0.0.100" \
    schema="analytics" \
    login="etl_user" \
    password="s3cur3_p@ssw0rd" \
    port="5432" \
    extra='{"sslmode": "require"}'

vault kv get airflow/connections/my_postgres
```

**坑点提示：** Vault 开发模式每次重启数据丢失，需要重新写入。生产环境务必使用 Vault 集群模式 + Consul/Raft 存储后端。另外，Windows 用户建议使用 Docker 运行 Vault：

```bash
docker run --rm -d --cap-add=IPC_LOCK \
  -e 'VAULT_DEV_ROOT_TOKEN_ID=root-token' \
  -e 'VAULT_DEV_LISTEN_ADDRESS=0.0.0.0:8200' \
  -p 8200:8200 \
  vault:latest
```

### 3.2 配置 Vault 作为 Airflow Secrets Backend

**步骤一：修改 airflow.cfg**

```ini
# airflow.cfg — [secrets] 部分
[secrets]
backend = airflow.providers.hashicorp.secrets.vault.VaultBackend
backend_kwargs = {
    "connections_path": "connections",
    "variables_path": "variables",
    "url": "http://127.0.0.1:8200",
    "mount_point": "airflow",
    "kv_engine_version": 2,
    "auth_type": "token",
    "token": "root-token"
}
```

`backend_kwargs` 是一个 JSON 字符串，Airflow 在运行时解析为 Python dict 传给 `VaultBackend.__init__()`。完整参数参见 `VaultBackend` 类的文档字符串（`providers/hashicorp/src/airflow/providers/hashicorp/secrets/vault.py:30-100`）。

**步骤二：验证 Vault Connection 是否正确加载**

```python
"""test_vault_backend.py —— 验证 VaultBackend 能否正确读取 Connection"""
from airflow.providers.hashicorp.secrets.vault import VaultBackend

backend = VaultBackend(
    connections_path="connections",
    url="http://127.0.0.1:8200",
    mount_point="airflow",
    kv_engine_version=2,
    auth_type="token",
    token="root-token",
)

conn = backend.get_connection(conn_id="my_postgres")
print(f"conn_id: {conn.conn_id}")
print(f"conn_type: {conn.conn_type}")
print(f"host: {conn.host}")
print(f"login: {conn.login}")
print(f"schema: {conn.schema}")
print(f"port: {conn.port}")
```

**预期输出：**
```
conn_id: my_postgres
conn_type: postgres
host: 10.0.0.100
login: etl_user
schema: analytics
port: 5432
```

> **注意：** `password` 在 Connection 对象中会被记录为 `***`（掩码后的值），这是 Airflow 3 的安全规范。如果代码中确实需要明文密码，使用 `conn.password` 属性仍然可以获取（Dag 代码运行在 Worker 上，拥有 JWT Token）。

**坑点一：`mount_point` 和 `connections_path` 的层级关系**

VaultBackend 的查找路径结构为：`{mount_point}/data/{connections_path}/{conn_id}`（KV v2）

例如 `mount_point=airflow`, `connections_path=connections`, `conn_id=my_postgres`，实际请求的 Vault API 路径是：
```
GET /v1/airflow/data/connections/my_postgres
```

如果你的 Vault 数据直接存在 `secret/my_postgres` 下（不加 `connections` 子路径），则需要设置 `connections_path=""`（空字符串）或 `mount_point="secret"`。

**坑点二：KV v1 vs KV v2 引擎版本**

KV v2 引擎的 API 返回结构比 v1 多一层 `data.data` 嵌套，VaultBackend 会通过 `kv_engine_version` 参数自动适配。如果 Vault 数据能写入但读不到，大概率是这个版本号配错了。

### 3.3 RBAC 角色创建与权限分配（Airflow CLI）

```bash
# 创建自定义角色 "dag_viewer" —— 只能查看指定 Dag
airflow roles create dag_viewer

# 授予该角色对特定资源的基础权限
# (Airflow 3 中权限格式为 "Action Resource" 模型)

# 列出当前所有权限（供参考）
airflow permissions list

# 在 Web UI 中可以通过 Admin → Roles 界面手动勾选权限，
# CLIs 的批量操作建议配合自动化脚本。
```

**使用 Python 脚本批量创建角色与权限：**

```python
"""batch_rbac_setup.py —— 批量创建 RBAC 角色与权限分配"""
import requests
import os

BASE = os.getenv("AIRFLOW_BASE_URL", "http://localhost:8080")
ADMIN_USER = os.getenv("AIRFLOW_ADMIN_USER", "admin")
ADMIN_PASS = os.getenv("AIRFLOW_ADMIN_PASS")

# 获取 Admin Token
resp = requests.post(
    f"{BASE}/api/v2/auth/token",
    json={"username": ADMIN_USER, "password": ADMIN_PASS},
)
token = resp.json()["access_token"]
headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

# 定义团队角色矩阵
team_roles = {
    "team_analytics_viewer": {
        "actions": ["can_read"],
        "resources": ["DAG:analytics_etl", "DAG:analytics_report"],
    },
    "team_analytics_editor": {
        "actions": ["can_read", "can_edit", "can_trigger"],
        "resources": ["DAG:analytics_etl", "DAG:analytics_report"],
    },
    "team_fraud_operator": {
        "actions": ["can_read", "can_edit", "can_trigger", "can_delete"],
        "resources": ["DAG:fraud_detection", "DAG:fraud_alerting"],
    },
}

for role_name, spec in team_roles.items():
    print(f"创建角色: {role_name}")
    # Note: 具体 API 端点取决于 Auth Manager 实现
    # FAB Auth Manager 通常在 Security → List Roles 页面操作
    # 此处展示概念模型，实际建议通过 Web UI 或 Auth Manager API 实现
    print(f"  → 权限: {spec['actions']} on {spec['resources']}")

print("\nRBAC 角色矩阵已生成，请通过 Web UI 或 Auth Manager API 完成绑定。")
```

### 3.4 JWT 密钥配置 —— 生产级非对称签名

```bash
# 1. 生成 Ed25519 密钥对（推荐，签名速度快且密钥短）
openssl genpkey -algorithm Ed25519 -out jwt_private.pem
openssl pkey -in jwt_private.pem -pubout -out jwt_public.pem

# 2. 构建 JWKS（JSON Web Key Set）—— 仅含公钥
# 工具有 jose、pem-jwk 等，或用 Python 快速生成
```

```python
"""generate_jwks.py —— 从 PEM 公钥生成 JWKS 文件"""
from cryptography.hazmat.primitives import serialization
from authlib.jose import JsonWebKey
import json

with open("jwt_public.pem", "rb") as f:
    public_key = serialization.load_pem_public_key(f.read())

jwk = JsonWebKey.import_key(public_key, {"kty": "OKP", "crv": "Ed25519"})
jwks = {"keys": [json.loads(jwk.as_json())]}

with open("jwks.json", "w") as f:
    json.dump(jwks, f, indent=2)

print("JWKS 已生成: jwks.json")
```

**配置 airflow.cfg 使用非对称签名：**

```ini
[api_auth]
# 禁用对称密钥，使用非对称密钥
# jwt_secret =  (注释掉或清空)
jwt_private_key_path = /opt/airflow/keys/jwt_private.pem
jwt_algorithm = GUESS
trusted_jwks_url = file:///opt/airflow/keys/jwks.json
jwt_expiration_time = 86400
jwt_cli_expiration_time = 3600

[execution_api]
jwt_expiration_time = 600
jwt_audience = urn:airflow.apache.org:task
```

**关键安全实践：**
- `jwt_private.pem` **只能放在 Scheduler 节点**（只有 Scheduler 负责签发 Execution Token）。
- `jwks.json`（公钥）可以部署到所有 API Server 节点用于验证。
- Worker 节点**不应持有**私钥或 `jwt_secret`。Worker 只接收令牌，不直接访问密钥材料。

### 3.5 网络安全配置 —— TLS、CORS 与速率限制

```ini
# airflow.cfg — 网络安全相关配置段

[webserver]
# 启用 HTTPS（通过反向代理如 Nginx 更常见）
# web_server_ssl_cert = /etc/ssl/certs/airflow.crt
# web_server_ssl_key = /etc/ssl/private/airflow.key

# CORS 配置 —— 仅允许已知前端域名
access_control_allow_headers = Content-Type, Authorization
access_control_allow_methods = GET, POST, PATCH, DELETE, OPTIONS
access_control_allow_origins = https://airflow.company.com, https://monitoring.company.com

[api]
# API 速率限制（通过 Auth Manager 或中间件实现）
# Airflow 3 默认不提供内置速率限制，需配合反向代理
# 推荐在 Nginx/Envoy 层配置：
#   limit_req_zone $binary_remote_addr zone=airflow_api:10m rate=30r/s;
#   limit_req zone=airflow_api burst=20 nodelay;
```

**Nginx 反向代理配置示例（TLS 终结 + 速率限制）：**

```nginx
# /etc/nginx/conf.d/airflow.conf
upstream airflow_api {
    server 127.0.0.1:8080;
}

limit_req_zone $binary_remote_addr zone=api_limit:10m rate=30r/s;

server {
    listen 443 ssl;
    server_name airflow.company.com;

    ssl_certificate     /etc/ssl/certs/airflow.crt;
    ssl_certificate_key /etc/ssl/private/airflow.key;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;

    location /api/ {
        limit_req zone=api_limit burst=20 nodelay;
        limit_req_status 429;

        proxy_pass http://airflow_api;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location / {
        proxy_pass http://airflow_api;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### 3.6 审计日志 —— 操作追踪与合规

```python
"""audit_log_query.py —— 查询审计日志 API"""
import requests
import os

BASE = os.getenv("AIRFLOW_BASE_URL", "http://localhost:8080")

# Admin 用户登录（仅有 Admin 和 Audit Log 角色可查看审计日志）
resp = requests.post(
    f"{BASE}/api/v2/auth/token",
    json={"username": "admin", "password": os.getenv("AIRFLOW_ADMIN_PASS")},
)
token = resp.json()["access_token"]
headers = {"Authorization": f"Bearer {token}"}

# 查询最近 50 条审计事件
events_resp = requests.get(
    f"{BASE}/api/v2/audit_logs",
    headers=headers,
    params={"limit": 50, "order_by": "-timestamp"},
)
events = events_resp.json()

for event in events.get("audit_logs", []):
    print(f"[{event['timestamp']}] {event['owner']} "
          f"执行了 {event['event']} "
          f"→ 结果: {event['status']}")

# 按事件类型筛选：只查看 Connection 相关的操作
conn_events = requests.get(
    f"{BASE}/api/v2/audit_logs",
    headers=headers,
    params={"limit": 100, "event_name": "connection"},
).json()

print(f"\nConnection 操作记录数: {len(conn_events.get('audit_logs', []))}")
```

**审计日志常见事件类型：**

| 事件名 | 含义 | 记录时机 |
|--------|------|---------|
| `connection.create` | 创建 Connection | POST `/connections` |
| `connection.delete` | 删除 Connection | DELETE `/connections/{id}` |
| `variable.create` | 创建 Variable | POST `/variables` |
| `dag.trigger` | 手动触发 Dag | POST `/dags/{id}/dagRuns` |
| `dag.pause` / `dag.unpause` | 暂停/恢复 Dag | PATCH `/dags/{id}` |
| `cli_sync_perm` | 同步权限 | `airflow sync-perm` |
| `get_role` / `post_role` / `patch_role` / `delete_role` | 角色操作 | RBAC 管理操作 |
| `login` / `logout` | 用户登录/登出 | 认证事件 |

> **安全提示：** 审计日志本身也是敏感数据。仅 Admin 和 Audit Log 角色的用户有权查看全平台审计日志。拥有审计日志权限的用户可以看到所有 Dag 的日志——即使他们没有被授权访问那些 Dag。这是 RBAC 模型中的一个"高于 Dag 级"的权限，部署管理员需要审慎分配。

---

## 4. 项目总结

### 4.1 安全策略对比表

| 维度 | Airflow 2.x | Airflow 3.x | 推荐做法 |
|------|------------|------------|---------|
| **RBAC** | FAB 五角色（Admin/Viewer/User/Op/Public） | 新增 Connection Config / Audit Log 角色，敏感信息 API 层掩码 | 按最小权限原则分配，默认 Viewer |
| **Connection 密码** | 存在数据库，Fernet 加密；Connection Config 角色可明文查看 | 不可明文查看（API 掩码），仅 Worker 可通过 Execution API 获取 | 生产环境使用 Vault/AWS/GCP 外部密钥管理 |
| **Worker DB 访问** | Worker 直接持有 DB 连接信息 | Worker 被彻底剥夺 DB 访问权限，仅通过 Execution API 通信 | 不在 Worker 侧配置 `AIRFLOW__DATABASE__SQL_ALCHEMY_CONN` |
| **JWT Core API** | 2.x 后期引入，支持有限 | 完整的 JWT 令牌体系 + 吊销表 + 刷新中间件 | 启用非对称签名（Ed25519） |
| **JWT Execution API** | 无（Worker 直接访问 DB） | workload/execution 双 Scope + ti:self 强制校验 | 使用 `trusted_jwks_url` 部署公钥 |
| **Dag File Processor 隔离** | 无隔离 | 软保护（移除 DB Session）但仍同 Unix 用户运行 | 部署层实现 Unix 用户隔离或独立实例 |
| **Secrets Backend** | 支持（Vault/AWS/GCP） | 支持，新增 Worker 独立 Secrets Backend 配置项 | 服务端用 `[secrets]`，Worker 用 `[workers]` 段 |
| **审计日志** | 有（event_log 表） | 增强的事件分类 + API 端点查询 | 对接 SIEM（如 Splunk/ELK）做集中分析 |

### 4.2 安全配置清单（Deployment Manager 必查项）

1. **[ ] `AIRFLOW__API_AUTH__JWT_SECRET` 已显式配置**（不要依赖启动时随机生成，会导致多组件之间验证失败）。
2. **[ ] `AIRFLOW__CORE__FERNET_KEY` 已轮换且不存入代码仓库**。
3. **[ ] Worker 节点环境变量中不含 `AIRFLOW__DATABASE__SQL_ALCHEMY_CONN`**。
4. **[ ] `[secrets] backend` 指向外部 Secrets Manager（生产环境）**。
5. **[ ] API Server 前部署了反向代理并启用了 TLS**。
6. **[ ] 所有非 Admin 用户被分配了最小权限角色**。
7. **[ ] Dag 代码提交前有 Code Review 流程**（防止恶意代码读取高权限凭证）。
8. **[ ] 审计日志定期导出到外部 SIEM 系统**。
9. **[ ] NTP 已配置并跨组件时间同步**（JWT 依赖 `iat`/`exp` 时间校验）。

### 4.3 常见踩坑经验

**故障案例一：Vault Token 过期导致所有 Connection 不可用**

某团队配置了 VaultBackend 使用 Token 认证，但 Vault Token 默认 TTL 为 30 天。Token 到期后，所有 Dag 在运行时都无法获取 Connection，导致大批任务失败。根因：未配置 Token 自动续期或使用更稳定的认证方式（如 Kubernetes Auth、AppRole）。**修复方案：** 改为 Kubernetes Auth，Worker 通过 ServiceAccount JWT 自动向 Vault 认证，无过期之忧。

**故障案例二：对称 JWT 密钥泄露导致的安全事件**

一个内部工具意外将 `AIRFLOW__API_AUTH__JWT_SECRET` 打印到了日志中。由于该密钥用于所有组件的签名和验证，攻击者可以用它伪造任意 Admin Token 调用 API。根因：日志系统的敏感值掩码未覆盖到该环境变量。**修复方案：** 紧急轮换密钥，并迁移到非对称签名（Ed25519），确保 Scheduler 持有私钥，API Server 只持有公钥。

**故障案例三：`ti:self` 校验导致的跨任务调用失败**

某开发者在 Dag A 的 Task 中尝试通过 Execution API 手动更新 Dag B 的任务状态（用于跨 Dag 协调），结果收到 403 `ti:self scope violation`。根因：Execution API JWT 的 `sub` 与目标 `task_instance_id` 不匹配，`ti:self` 强制执行了任务级隔离。**修复方案：** 跨 Dag 协调应使用 Asset 触发、ExternalTaskSensor 或通过 Core API（使用用户级 Token）实现，而非在工作负载内直接操作其他任务。

### 4.4 注意事项

- **Secrets Backend 的搜索顺序是叠加而非互斥。** 环境变量 > Metastore > 外部 Secrets Manager。如果同一个 `conn_id` 在多个层级都存在，高优先级的会覆盖低优先级的。这既是灵活性也是隐患——要避免某个开发人员通过环境变量意外覆盖了 Vault 中的生产凭证。
- **Dag File Processor 和 Triggerer 的隔离在 Airflow 3 中仍然是"软保护"级别。** 这两个组件运行 Dag 作者提交的代码，运行在与父进程相同的 Unix 用户下，因此从理论上可以获取父进程的数据库凭证。多团队场景下建议运行独立实例或实施 Unix 用户级隔离。
- **JWT 时间同步是硬性要求。** 如果 Scheduler 和 API Server 之间的时钟偏差超过 `jwt_leeway`（默认 10 秒），Token 验证会直接失败。务必在所有 Airflow 节点上启用 NTP。
- **审计日志的 `can_read on Audit Logs` 权限会绕过 Dag 级访问控制。** 这意味着拥有审计日志查看权限的用户可以看到所有 Dag 的操作记录，即使他们没有对应 Dag 的读取权限。

### 4.5 思考题

**思考题一：** 某公司有三条业务线（财务、营销、物流），共享同一个 Airflow 集群。Depolyment Manager 启用了实验性的 `[core] multi_team` 功能。请问：(a) 该功能在 Airflow 3 中能否保证财务团队的 Task 无法访问营销团队的 Connection？(b) 如果不能，现阶段应该采取哪些部署层面的措施来实现团队间隔离？(c) 社区计划在哪些方面做后续改进？

**思考题二：** 你正在设计一个"一键吊销"功能——当某个用户的 API Token 泄露时，管理员可以立即让该 Token 失效。请问：(a) Core API Token 的吊销机制是如何实现的？（提示：`revoked_token` 表）(b) 为什么 Execution API Token 不提供吊销机制？(c) 如果必须在 Worker 任务执行期间强制终止其 API 访问权限，你会如何设计替代方案？

---

> **本章引用参考：**
> - Airflow Security Model（安全模型）：`airflow-core/docs/security/security_model.rst`
> - JWT Token Authentication（JWT 认证）：`airflow-core/docs/security/jwt_token_authentication.rst`
> - Audit Logs（审计日志）：`airflow-core/docs/security/audit_logs.rst`
> - Secrets Backend 配置：`airflow-core/docs/security/secrets/secrets-backend/index.rst`
> - VaultBackend 源码：`providers/hashicorp/src/airflow/providers/hashicorp/secrets/vault.py`
> - BaseSecretsBackend：`airflow-core/src/airflow/secrets/base_secrets.py`
> - Sensitive Configuration Variables 清单：`airflow-core/docs/security/security_model.rst`（AUTOGENERATED CORE SENSITIVE VARS 章节）
