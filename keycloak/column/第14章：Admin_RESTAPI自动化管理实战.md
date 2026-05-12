# 第14章：Admin REST API自动化管理实战

## 1 项目背景

某SaaS平台支撑着2000+企业租户的日常业务，每个租户对应Keycloak中一个独立Realm。之前业务体量小，每周新增3-5个租户，运维同事在Admin Console上手动操作——创建Realm、配置SMTP、预置"管理员/审计员/普通用户"三个角色、注册客户端应用、生成初始管理员账号、邮件通知客户。一套流程下来大约30分钟，虽然手酸但还能扛住。

今年业务爆发式增长，市场部签下大客户，每天涌入50+新租户。运维团队彻底崩盘了——三班倒也处理不完，积压的入驻申请排到一周后，销售同事天天堵工位催单。更要命的是，手工操作的质量惨不忍睹：Realm名称敲错字母、忘了勾选"User Registration"开关、客户端Secret贴错地方、角色分配张冠李戴……每个错误都意味着客户第一印象崩塌，需要紧急修复并写事故报告。

祸不单行。安全部门每月初要求对所有测试环境的用户密码执行强制重置——200+测试账号分布在4个Realm中，运维同事打开Admin Console，挨个搜索用户、点Edit、输新密码、点Save。一个人做半天，两个人轮换也要浪费一整天。上个月漏了两个账号没重置，被安全审计抓到，扣了团队季度绩效。审计还指出另一个致命问题：所有运维操作凭记忆执行，没有操作日志，无法追溯"谁、什么时候、做了什么变更"——这在等保测评中是不合规的。

问题的本质很清楚：Admin Console是给人"点来点去"用的，不是给"规模化运营"用的。当Realm数量从个位数膨胀到三位数、用户从几十人扩展到数千人时，鼠标点击不再是效率工具，而是效率瓶颈。需要一个可编程、可审计、可编排的自动化方案——Keycloak Admin REST API正是为此而生。本章将构建一套完整的API自动化体系，实现租户自助入驻、批量用户管理、配置备份与恢复、定时任务编排。

---

## 2 项目设计——剧本式交锋对话

**小胖**（端着一杯奶茶走进会议室）：大师、小白，我昨天去麦当劳，突然想通一件事——Keycloak的Admin REST API，不就相当于自助点餐机嘛！以前只能去柜台跟服务员说"我要一个巨无霸套餐"，服务员在收银机上点点点；现在我自己在大屏幕上戳几下，订单直接传到后厨。Admin Console就是人工柜台，Admin REST API就是自助点餐机接口——后端厨房还是那个厨房，只是前台换了交互方式！

**大师**（笑着放下手里的文档）：小胖这个比喻很生动。不过我要补充一点——自助点餐机不仅省了人力，还能同时处理10个顾客的订单，不会像柜台那样"后边排着队、前边插着队"。Admin REST API的本质，就是把Keycloak的管理能力从GUI操作抽象成HTTP端点，让脚本和程序可以直接调用。你不是在"模拟点击"，而是在对数据模型直接操作。

**小白**（在白板上写字）：等一下。既然Admin Console能点来点去，为什么不直接抓包模拟？F12抓出Admin Console发出的请求，然后用curl复现，不是更省事？

**大师**：这是很多团队走过的弯路，我来拆解为什么不能这么做。第一，Admin Console是Keycloak官方维护的SPA应用，它的内部API调用路径（比如`/admin/realms/master/ui-ext/...`）是私有端点，不受Semantic Versioning保护——Keycloak版本升级时随时可能改变参数结构甚至删除端点，你的脚本会全线崩溃。第二，Admin Console的认证流程依赖前端Session Cookie和CSRF Token，直接抓包复制请求，光是Cookie和CSRF的时效性管理就够你头疼。第三，Admin Console请求中夹杂了大量UI渲染用的冗余字段，你根本没用到却要维护。正确的做法是面向Admin REST API的公开端点编程，它是Keycloak官方文档明确定义、承诺向后兼容的稳定接口。

> **大师技术映射**：抓包模拟Admin Console → 跟餐厅服务员说"他刚才点的那个也给我来一份"——能解决一顿饭，但不是点餐方案。Admin REST API → 菜单上的标准编号——稳、准、不依赖服务员心情。

---

**小胖**：好，那认证呢？我试了一下，Admin REST API好像不止一种登录方式？我用admin-cli、用Service Account、用普通用户的Token都能调，这三者到底怎么区分？

**大师**：问到核心了。Admin REST API的认证有三种主流方式，选错了要么权限不足、要么安全失控。

**方式一：Admin CLI Token（用户名密码直登）**。这是最直接的方式——用`admin-cli`这个预置Public Client，以`grant_type=password`模式，传入master realm的管理员账号密码，直接换一个Access Token。优点是零配置，拿到Token就能调用所有Realm的管理端点。缺点是这颗Token的权限太大——它是master realm下的管理员Token，默认可以操作**所有**Realm、所有用户、所有客户端，相当于拿着一把万能钥匙。而且Token有效期只有1分钟，脚本里忘记刷新Token就会在批量操作中途报401。

**方式二：Client Credentials（Service Account）**。创建一个专用客户端（比如`api-admin-client`），启用Service Account模式并设为Confidential（需要Client Secret）。通过`grant_type=client_credentials`获取Token。最大的优势是**细粒度权限控制**——你可以在该客户端对应的Service Account User上，精确授予它只能操作"tenant-*"前缀的Realm、只能管理用户而不能删除Realm。这把"钥匙"可以专门打磨成只能开特定几把锁的形状。

**方式三：Bearer Token（普通用户Token）**。用某个Realm中具备管理角色（如`realm-admin`角色的用户）登录后获取的Token来调API。这种Token的权限边界天然局限在该用户所属Realm内，无法越界操作其他Realm。适合租户自服务场景——给租户管理员一把只能操作自家Realm的API权限。

**小白**：那权限边界具体怎么控制？一个Token到底能操作哪些Realm？

**大师**：Keycloak的Admin权限由两套机制共同决定。第一层是**Realm边界**——你向`/realms/{realm}/protocol/openid-connect/token`请求Token时，这个Realm就已经定了。master realm的Token拥有跨Realm管理权限（因为master是管理平面），而其他Realm的Token只能操作本Realm内的资源。第二层是**Role-Based Access**——用户/Service Account必须拥有对应的管理角色（`manage-realm`、`manage-users`、`manage-clients`等），才能调用相应端点。举个例子：一个用户即使属于Realm A并拥有Realm A的`realm-admin`角色，它的Token也调不了Realm B的任何端点——哪怕你手动把Authorization头改成了Realm B的URL，Keycloak后台会校验Token的`azp`和`iss`字段，发现不对直接403。

> **大师技术映射**：Admin CLI Token → 总控室门禁卡，刷一次全楼通行。Service Account Token → 楼层卡，只能进特定楼层和房间。普通用户Token → 工位卡，连机房门都刷不开。

---

**小胖**（第二轮，奶茶快见底了）：那并发和限流呢？我上次写了个脚本，一口气POST了200个用户创建请求，结果第30个左右就开始报429——"Too Many Requests"。Keycloak在API层面有限流？

**大师**：Keycloak本身没有内置Admin REST API的限流，429错误通常来自反向代理层（Nginx/Ingress的`limit_req`），或者更隐蔽的原因——数据库连接池耗尽。Keycloak后台使用HikariCP连接池（默认最大连接数通常在20-50之间），当你的脚本并发发出大量API请求，每个请求都需要从数据库读/写一次，连接池很快打满，后续请求拿不到连接就开始排队超时。所以批量操作的黄金法则是：**控制并发，加间隔，处理分页**。

并发建议：使用信号量或线程池将并发控制在5-10个以内，每批完成后sleep 200-500ms。对于用户列表这类可能返回几千条记录的端点，Keycloak默认每页只返回100条——你必须在请求中加`?max=100&first=0`，循环递进`first`值直到返回空数组，才能真正遍历完所有数据。很多团队只调了一次API就以为拿全了用户列表，结果漏了80%的数据。

**小白**：那API的版本兼容性呢？我们用Keycloak 24写的自动化脚本，升级到26还能跑吗？

**大师**：Keycloak Admin REST API的版本策略遵循URL路径约定——所有管理端点都在`/admin/realms/{realm}/`路径下，**不携带版本号**。这意味着API的向后兼容性靠Keycloak团队的语义化保证来维持，而不是靠URL版本隔离。从24到26的实际升级经验是：核心CRUD端点（Realm/User/Client/Role）的JSON Schema向后兼容，参数名称不会变；但可能有**新增字段**——你POST创建用户时如果传了旧版不存在的字段，会被200忽略（不报错）；GET响应中可能出现新字段，你的JSON解析代码需要兼容未预期的key。真正容易出问题的是**Behavior Change**——比如某个端点从"同步操作"变成了"异步触发"，你去轮询状态的方式就得改。建议在CI中维护一套API冒烟测试——每次Keycloak版本升级后，自动跑一遍核心API的创建-查询-删除全生命周期，确保行为一致。

> **大师技术映射**：API并发 → 银行柜员只能同时服务5个人，再多就要取号排队（连接池+限流）。API分页 → 图书馆目录柜，每个抽屉最多装100张卡片，查完整馆藏得把所有抽屉翻一遍。版本兼容 → 手机APP升级，旧API要能继续用，新功能加字段但不删旧字段。

---

## 3 项目实战

### 环境准备

| 组件 | 版本/说明 |
|------|----------|
| Keycloak | 26.x，第2章部署的环境，确保master realm的admin-cli客户端可用 |
| Python 3 | 3.10+，安装依赖：`pip install requests` |
| curl + jq | 命令行调试API，jq用于JSON格式化输出（Windows上可用`choco install jq`） |
| 测试CSV文件 | users_batch.csv，用于批量导入（见步骤3） |

确保Keycloak服务正在运行，默认端口8080。

---

### 步骤1：获取Admin Token（三种认证方式）

**目标**：掌握三种Token获取方式，理解各自的适用场景和权限边界。

```bash
# ============ 方式1：Admin CLI Token（用户名密码，适合脚本快速启动）============
ADMIN_TOKEN=$(curl -s -X POST "http://localhost:8080/realms/master/protocol/openid-connect/token" \
  -d "client_id=admin-cli" \
  -d "username=admin" \
  -d "password=admin" \
  -d "grant_type=password" | jq -r '.access_token')

echo "Admin CLI Token: ${ADMIN_TOKEN:0:30}..."

# ============ 方式2：Service Account Token（Client Credentials，适合服务间调用）============
# 前置：通过Admin Console或API创建专用管理客户端并获取secret
# Admin Console → master realm → Clients → Create
# Client ID: api-admin-client, Client authentication: On, Service Account: On
# 然后在Credentials标签页复制Client Secret

SA_TOKEN=$(curl -s -X POST "http://localhost:8080/realms/master/protocol/openid-connect/token" \
  -d "client_id=api-admin-client" \
  -d "client_secret=YOUR_CLIENT_SECRET" \
  -d "grant_type=client_credentials" | jq -r '.access_token')

echo "Service Account Token: ${SA_TOKEN:0:30}..."

# ============ 方式3：刷新Token（应对Token过期）============
# 方式1和方式2返回的响应中都带有refresh_token字段
REFRESH_TOKEN=$(curl -s -X POST "http://localhost:8080/realms/master/protocol/openid-connect/token" \
  -d "client_id=admin-cli" \
  -d "username=admin" \
  -d "password=admin" \
  -d "grant_type=password" | jq -r '.refresh_token')

NEW_TOKEN=$(curl -s -X POST "http://localhost:8080/realms/master/protocol/openid-connect/token" \
  -d "client_id=admin-cli" \
  -d "refresh_token=${REFRESH_TOKEN}" \
  -d "grant_type=refresh_token" | jq -r '.access_token')

echo "Refreshed Token: ${NEW_TOKEN:0:30}..."
```

**运行结果**：三种方式均输出Token的前30个字符，验证Token获取成功。Admin CLI适用于一次性脚本；Service Account适合长期运行的服务编排；Refresh Token机制确保长任务不会因Token过期中断。

**易踩的坑**：方式2创建客户端时忘开"Service Accounts Enabled"开关——API会返回400 Bad Request提示客户端不支Client Credentials。方式1的Token默认60秒过期，批量任务必须嵌入自动刷新逻辑，否则操作到一半401中断。

---

### 步骤2：Realm全生命周期管理

**目标**：通过API创建租户专属Realm，配置会话参数，并在Realm内创建客户端应用。

```python
import requests, json, time

BASE_URL = "http://localhost:8080"
ADMIN_URL = f"{BASE_URL}/admin/realms"
TOKEN_URL = f"{BASE_URL}/realms/master/protocol/openid-connect/token"

def get_admin_token():
    """获取Admin Token，带自动刷新机制"""
    resp = requests.post(TOKEN_URL, data={
        "client_id": "admin-cli",
        "username": "admin", "password": "admin",
        "grant_type": "password"
    })
    resp.raise_for_status()
    return resp.json()["access_token"]

def api_get(url, token, max_retry=3):
    """封装GET请求，处理Token过期自动刷新与429限流重试"""
    for attempt in range(max_retry):
        resp = requests.get(url, headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        })
        if resp.status_code == 401 and attempt < max_retry - 1:
            token = get_admin_token()
            continue
        if resp.status_code == 429:
            time.sleep(2 ** attempt)
            continue
        return resp
    return resp

def api_post(url, data, token, max_retry=3):
    """封装POST请求，逻辑同上"""
    for attempt in range(max_retry):
        resp = requests.post(url, json=data, headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        })
        if resp.status_code == 401 and attempt < max_retry - 1:
            token = get_admin_token()
            continue
        if resp.status_code == 429:
            time.sleep(2 ** attempt)
            continue
        return resp
    return resp

token = get_admin_token()

# ========== 创建租户Realm ==========
realm_config = {
    "realm": "tenant-001",
    "enabled": True,
    "displayName": "Tenant 001 - 星辰科技",
    "loginWithEmailAllowed": True,
    "registrationAllowed": False,
    "accessTokenLifespan": 300,          # Access Token 5分钟
    "ssoSessionIdleTimeout": 1800,       # SSO空闲30分钟
    "ssoSessionMaxLifespan": 36000,      # SSO最长10小时
}
resp = api_post(ADMIN_URL, realm_config, token)
print(f"[Realm] 创建 tenant-001: HTTP {resp.status_code}")

# ========== 在新Realm中创建客户端应用 ==========
client_config = {
    "clientId": "tenant-001-web",
    "name": "星辰科技Web应用",
    "enabled": True,
    "publicClient": False,
    "protocol": "openid-connect",
    "redirectUris": ["https://tenant001.example.com/*"],
    "webOrigins": ["https://tenant001.example.com"],
    "standardFlowEnabled": True,
    "directAccessGrantsEnabled": True,
    "serviceAccountsEnabled": True
}
client_resp = api_post(f"{ADMIN_URL}/tenant-001/clients", client_config, token)
print(f"[Client] 创建 tenant-001-web: HTTP {client_resp.status_code}")

# ========== 获取Client UUID和Secret ==========
clients_resp = api_get(f"{ADMIN_URL}/tenant-001/clients?clientId=tenant-001-web", token)
clients = clients_resp.json()
if clients:
    client_uuid = clients[0]["id"]
    secret_resp = requests.get(
        f"{ADMIN_URL}/tenant-001/clients/{client_uuid}/client-secret",
        headers={"Authorization": f"Bearer {token}"}
    )
    print(f"[Client] UUID: {client_uuid}, Secret: {secret_resp.json().get('value')}")

# ========== 预置角色 ==========
role_names = ["tenant_admin", "tenant_auditor", "tenant_user"]
for role_name in role_names:
    role_config = {
        "name": role_name,
        "description": f"租户角色 - {role_name}",
        "composite": False,
        "clientRole": False  # Realm级别角色
    }
    role_resp = api_post(f"{ADMIN_URL}/tenant-001/roles", role_config, token)
    print(f"[Role] 创建 {role_name}: HTTP {role_resp.status_code}")
```

**运行结果**：
```
[Realm] 创建 tenant-001: HTTP 201
[Client] 创建 tenant-001-web: HTTP 201
[Client] UUID: a1b2c3d4-e5f6-7890-abcd-ef1234567890, Secret: abcdef1234567890...
[Role] 创建 tenant_admin: HTTP 201
[Role] 创建 tenant_auditor: HTTP 201
[Role] 创建 tenant_user: HTTP 201
```

---

### 步骤3：批量用户导入

**目标**：从CSV文件批量创建用户，处理创建失败的情况并输出统计报告。

**准备CSV文件** (`users_batch.csv`)：
```csv
username,email,firstName,lastName,department,hire_date,initial_password
wangwu,wangwu@tenant001.com,五,王,研发部,2025-01-15,Welcome@2025
lisi,lisi@tenant001.com,四,李,市场部,2025-02-20,Welcome@2025
zhaoqi,zhaoqi@tenant001.com,七,赵,财务部,2025-03-10,Welcome@2025
```

**批量导入脚本**：
```python
import csv

def batch_create_users(realm, csv_file, token):
    """从CSV批量创建用户，处理分页与失败重试"""
    url = f"{ADMIN_URL}/{realm}/users"
    success, failed = 0, 0
    failed_users = []

    with open(csv_file, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for idx, row in enumerate(reader, 1):
            user_payload = {
                "username": row["username"],
                "email": row["email"],
                "firstName": row["firstName"],
                "lastName": row["lastName"],
                "enabled": True,
                "attributes": {
                    "department": [row["department"]],
                    "hireDate": [row["hire_date"]]
                },
                "credentials": [{
                    "type": "password",
                    "value": row["initial_password"],
                    "temporary": True   # 首次登录强制改密码
                }]
            }

            resp = api_post(url, user_payload, token)
            if resp.status_code == 201:
                success += 1
            elif resp.status_code == 409:  # 用户名已存在
                print(f"[Skip] {row['username']}: 用户已存在")
                success += 1
            else:
                failed += 1
                failed_users.append(row["username"])
                print(f"[Failed] {row['username']}: HTTP {resp.status_code} - {resp.text[:100]}")

            # 每10个用户暂停300ms，避免压迫API
            if idx % 10 == 0:
                time.sleep(0.3)
                # 刷新Token防止过期
                token = get_admin_token()

    print(f"\n导入完成: Success={success}, Failed={failed}")
    if failed_users:
        print(f"失败用户: {', '.join(failed_users)}")

batch_create_users("tenant-001", "users_batch.csv", token)
```

**运行结果**：
```
导入完成: Success=3, Failed=0
```

进入Admin Console → tenant-001 → Users，确认三个用户均已创建，且"Credentials"标签页显示密码为temporary状态。

---

### 步骤4：批量角色分配

**目标**：为用户批量分配客户端和Realm级别角色。

```python
def get_client_uuid(realm, client_id, token):
    clients_resp = api_get(f"{ADMIN_URL}/{realm}/clients?clientId={client_id}", token)
    clients = clients_resp.json()
    return clients[0]["id"] if clients else None

def get_user_uuid(realm, username, token):
    users_resp = api_get(f"{ADMIN_URL}/{realm}/users?username={username}", token)
    users = users_resp.json()
    return users[0]["id"] if users else None

def assign_realm_role(realm, username, role_name, token):
    user_uuid = get_user_uuid(realm, username, token)
    if not user_uuid:
        print(f"[Error] 用户 {username} 不存在")
        return False
    # 获取角色详情
    roles_resp = api_get(f"{ADMIN_URL}/{realm}/roles/{role_name}", token)
    if roles_resp.status_code != 200:
        print(f"[Error] 角色 {role_name} 不存在")
        return False
    role = roles_resp.json()
    resp = requests.post(
        f"{ADMIN_URL}/{realm}/users/{user_uuid}/role-mappings/realm",
        json=[role],
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    )
    return resp.status_code == 204

# ========== 为用户分配角色 ==========
user_role_map = {
    "wangwu": "tenant_admin",
    "lisi": "tenant_auditor",
    "zhaoqi": "tenant_user"
}
for username, role in user_role_map.items():
    ok = assign_realm_role("tenant-001", username, role, token)
    print(f"[RoleAssign] {username} -> {role}: {'OK' if ok else 'FAILED'}")
```

**运行结果**：
```
[RoleAssign] wangwu -> tenant_admin: OK
[RoleAssign] lisi -> tenant_auditor: OK
[RoleAssign] zhaoqi -> tenant_user: OK
```

**易踩的坑**：角色分配API的请求体必须是一个数组`[{...}]`而非单个对象`{...}`。如果写成单个对象，Keycloak会返回500 Internal Server Error，错误信息隐晦。另外，修改角色映射用的是POST（覆盖式添加），不是PUT——如果用户已有角色A，POST角色B后只保留B，角色A会被移除。如需追加角色，用`POST /role-mappings/realm/add-available`端点。

---

### 步骤5：配置备份与恢复脚本

**目标**：定时导出Realm配置和用户数据，实现灾备自动化。

```bash
#!/bin/bash
set -euo pipefail

BACKUP_ROOT="./backups"
REALM="${1:-tenant-001}"
BACKUP_DIR="${BACKUP_ROOT}/$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP_DIR"

# 获取Token
ADMIN_TOKEN=$(curl -s -X POST "http://localhost:8080/realms/master/protocol/openid-connect/token" \
  -d "client_id=admin-cli" \
  -d "username=admin" -d "password=admin" \
  -d "grant_type=password" | jq -r '.access_token')

# 导出Realm配置（全量）
echo "[Backup] 导出Realm配置..."
curl -s "http://localhost:8080/admin/realms/${REALM}" \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" | jq '.' > "${BACKUP_DIR}/${REALM}-config.json"

# 分页导出所有用户
echo "[Backup] 导出用户列表..."
PAGE=0
MAX=500
rm -f "${BACKUP_DIR}/${REALM}-users.json"
echo "[" > "${BACKUP_DIR}/${REALM}-users.json"
FIRST_PAGE=true
while true; do
  RESP=$(curl -s "http://localhost:8080/admin/realms/${REALM}/users?max=${MAX}&first=$((PAGE * MAX))" \
    -H "Authorization: Bearer ${ADMIN_TOKEN}")
  COUNT=$(echo "$RESP" | jq 'length')
  if [ "$COUNT" -eq 0 ]; then
    break
  fi
  if [ "$FIRST_PAGE" = true ]; then
    echo "$RESP" | jq '.[]' >> "${BACKUP_DIR}/${REALM}-users.json"
    FIRST_PAGE=false
  else
    # 追加分页数据，用逗号分隔
    echo "," >> "${BACKUP_DIR}/${REALM}-users.json"
    echo "$RESP" | jq '.[]' >> "${BACKUP_DIR}/${REALM}-users.json"
  fi
  PAGE=$((PAGE + 1))
done
echo "]" >> "${BACKUP_DIR}/${REALM}-users.json"

echo "[Backup] 完成: ${BACKUP_DIR}"
ls -la "${BACKUP_DIR}"
```

**运行结果**：
```
[Backup] 导出Realm配置...
[Backup] 导出用户列表...
[Backup] 完成: ./backups/20250512_143022
-rw-r--r-- 1 user group   2048 May 12 14:30 tenant-001-config.json
-rw-r--r-- 1 user group   5120 May 12 14:30 tenant-001-users.json
```

---

### 步骤6：会话管理与强制下线

**目标**：通过API实现会话监控与批量登出操作。

```bash
# ========== 查看指定Realm的所有活跃会话 ==========
curl -s "http://localhost:8080/admin/realms/tenant-001/sessions" \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" | jq '.[] | {username, ipAddress, start: (.start | strftime("%Y-%m-%d %H:%M:%S"))}'

# ========== 强制下线特定用户 ==========
USER_UUID="xxxx-xxxx-xxxx"  # 从前一步查到的用户UUID
curl -X POST "http://localhost:8080/admin/realms/tenant-001/users/${USER_UUID}/logout" \
  -H "Authorization: Bearer ${ADMIN_TOKEN}"

# ========== 清除Realm中所有用户会话（全量登出，慎用）==========
# 适用于紧急安全事件，如发现密码泄露
curl -X POST "http://localhost:8080/admin/realms/tenant-001/logout-all" \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  -w "\nHTTP Status: %{http_code}\n"
```

**运行结果示例**：
```json
{
  "username": "wangwu",
  "ipAddress": "192.168.1.100",
  "start": "2025-05-12 14:15:30"
}
HTTP Status: 204
```

---

### 完整生产级封装：TenantProvisioner类

```python
import requests
import time
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

class TenantProvisioner:
    """Keycloak租户自动入驻工具，支持重试、Token刷新、审计日志"""

    def __init__(self, base_url, admin_user, admin_password):
        self.base_url = base_url
        self.admin_url = f"{base_url}/admin/realms"
        self.token_url = f"{base_url}/realms/master/protocol/openid-connect/token"
        self.admin_user = admin_user
        self.admin_password = admin_password
        self._token = None
        self._token = self._get_token()

    def _get_token(self):
        resp = requests.post(self.token_url, data={
            "client_id": "admin-cli",
            "username": self.admin_user,
            "password": self.admin_password,
            "grant_type": "password"
        })
        resp.raise_for_status()
        return resp.json()["access_token"]

    def _headers(self):
        return {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}

    def _request(self, method, url, **kwargs):
        for attempt in range(3):
            resp = method(url, headers=self._headers(), **kwargs)
            if resp.status_code == 401:
                logging.info("Token过期，自动刷新...")
                self._token = self._get_token()
                continue
            if resp.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            return resp
        return resp

    def provision(self, tenant_name, display_name, admin_username, admin_email, admin_password):
        """一站式租户开通"""
        log = logging.getLogger(tenant_name)
        log.info(f"开始开通租户: {tenant_name}")

        # 1. 创建Realm
        realm = {
            "realm": tenant_name, "enabled": True, "displayName": display_name,
            "loginWithEmailAllowed": True,
            "ssoSessionIdleTimeout": 1800, "ssoSessionMaxLifespan": 36000
        }
        resp = self._request(requests.post, self.admin_url, json=realm)
        if resp.status_code not in (201, 409):
            log.error(f"创建Realm失败: {resp.status_code} {resp.text}")
            return False
        log.info("Realm创建成功")

        # 2. 创建客户端
        client = {
            "clientId": f"{tenant_name}-app", "enabled": True,
            "publicClient": False, "serviceAccountsEnabled": True,
            "redirectUris": [f"https://{tenant_name}.example.com/*"],
            "webOrigins": [f"https://{tenant_name}.example.com"]
        }
        resp = self._request(requests.post, f"{self.admin_url}/{tenant_name}/clients", json=client)
        if resp.status_code not in (201, 409):
            log.error(f"创建客户端失败: {resp.status_code}")
            return False
        log.info("客户端创建成功")

        # 3. 创建租户管理员
        user = {
            "username": admin_username, "email": admin_email,
            "firstName": display_name, "lastName": "管理员",
            "enabled": True,
            "credentials": [{"type": "password", "value": admin_password, "temporary": True}]
        }
        resp = self._request(requests.post, f"{self.admin_url}/{tenant_name}/users", json=user)
        if resp.status_code not in (201, 409):
            log.error(f"创建管理员失败: {resp.status_code}")
            return False
        log.info(f"租户管理员 {admin_username} 创建成功")
        log.info(f"租户 {tenant_name} 开通完成")
        return True

# ========== 使用示例 ==========
provisioner = TenantProvisioner("http://localhost:8080", "admin", "admin")
provisioner.provision("tenant-002", "第二租户科技", "t2admin", "admin@tenant002.com", "Temp@Pass2025")
```

---

### 测试验证

验证完整租户创建链路：

```bash
# 1. 验证Realm是否创建
curl -s "http://localhost:8080/admin/realms/tenant-002" \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" | jq '{realm, enabled, displayName}'

# 2. 验证客户端是否创建
curl -s "http://localhost:8080/admin/realms/tenant-002/clients?clientId=tenant-002-app" \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" | jq '.[0].clientId'

# 3. 验证管理员用户是否创建
curl -s "http://localhost:8080/admin/realms/tenant-002/users?username=t2admin" \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" | jq '.[0].username'

# 4. 验证密码策略（管理员首次登录应被要求改密码）
```

**预期输出**：所有验证命令均返回预期JSON，确认Realm、Client、User全部创建成功。

**生产环境的额外注意**：
- Admin Token和Client Secret**绝对不能提交到Git仓库**——使用环境变量或Vault管理
- 删除Realm是**不可逆操作**（Keycloak不会做逻辑删除），脚本中删除操作必须三重确认（输入Realm名称确认）
- `logout-all`接口在生产中应加审批流程，意外调用会导致全租户用户强制下线
- 并发控制：10个并发请求是安全上限，超出容易触发数据库死锁

---

## 4 项目总结

### 三种管理方式对比

| 维度 | Admin Console手动 | Admin REST API脚本 | Terraform声明式 |
|------|-------------------|-------------------|----------------|
| 操作效率 | 单个Realm约30分钟 | 批量创建50个Realm < 5分钟 | 声明即部署，状态自动收敛 |
| 学习成本 | 低（GUI直觉操作） | 中（需理解API语义） | 高（需掌握HCL语法和Provider） |
| 可审计性 | 无（操作无日志绑定） | 脚本输出+API审计日志 | Git版本控制天然可追溯 |
| 幂等性 | 低（重复操作报错） | 需自行处理409冲突 | 天然幂等，plan/diff预览 |
| 适用规模 | < 10个Realm | 10 - 200个Realm | 200+个Realm，多环境管理 |
| 错误恢复 | 手工回滚 | 编写回滚脚本 | terraform destroy / apply |

### 适用场景

- **SaaS租户自助入驻**：新客户注册后自动触发Realm创建、角色预置、管理员账号生成
- **批量用户管理**：入职季批量创建用户、离职季批量禁用账号、月初密码强制重置
- **CI/CD集成**：Pipeline中自动化创建测试Realm、导入测试用户数据、运行集成测试后清理
- **运维自动化**：定时导出配置备份、监控会话数并告警、定期清理僵尸用户
- **不适用场景**：单环境单一Realm的手工运维（杀鸡用牛刀）；需要严格事务性回滚的多步骤操作（API不提供跨端点事务）

### 核心注意事项

1. **Token安全管理**：Admin Token是"万能钥匙"，严禁提交到代码仓库。使用环境变量或Secret Manager（HashiCorp Vault / K8s Secret）注入
2. **API版本兼容**：Keycloak Admin API不带URL版本号，升级前务必在CI中跑全量API冒烟测试
3. **并发控制**：连接池上限约20-50，生产脚本并发控制在5-10以内，批次间隔200-500ms
4. **删除保护**：Realm删除不可逆，脚本中应检查Realm名称是否包含特定前缀/后缀做二次确认
5. **网络隔离**：Admin API端点不应暴露到公网——通过反向代理加IP白名单或只在内网可达

### 常见踩坑案例

- **Token过期未刷新**：某团队写了个500用户批量导入脚本，未嵌入Token刷新逻辑，导入到第200个时Token过期，剩余300个用户全部401失败。根因：Admin CLI Token默认60秒过期，批量操作必须实现自动刷新。
- **分页数据遗漏**：运维同事用`GET /admin/realms/xxx/users`拉取用户列表用于审计，默认返回100条，误以为Realm中只有100个用户——实际有368个。根因：未传`max`和`first`参数实现分页遍历。
- **生产环境误删Realm**：脚本中Realm名称来自变量，某次变量为空字符串，构造URL变成了`DELETE /admin/realms/`，Keycloak直接拒了（设计上的保护），但教训是删除操作必须有确认机制。

### 思考题

1. 如何使用Keycloak Admin REST API配合Terraform实现Infrastructure as Code？请设计一个CI/CD流水线，实现"PR合并后自动更新Keycloak Realm配置"的完整流程。

2. 如果需要支持"创建Realm+Client+Role+User"的原子事务（要么全部成功，要么全部回滚），Admin REST API层面如何实现？提示：考虑利用Keycloak的Partial Import端点 (`POST /admin/realms/{realm}/partialImport`)，并设计补偿事务（Saga模式）处理创建过程中API调用部分失败后的清理逻辑。

---

> 注：本章完整代码见项目 `keycloak-admin-automation`，含`TenantProvisioner`类、CSV批量导入脚本、Bash备份脚本、crontab任务配置示例。建议下一章阅读《社交登录与身份联合》前，先掌握本章的API自动化能力，便于批量配置身份提供者。
