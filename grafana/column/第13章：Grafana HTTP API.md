# 第13章：Grafana HTTP API

## 1. 项目背景

"我们有200多个Dashboard，现在需要批量修改每个Dashboard里的某个Panel配置。一个个点进去改至少要改两个小时，而且手工操作容易出错。有没有自动化方式？"

DevOps工程师小马正在经历"Grafana管理的规模化阵痛"。Dashboard数量从最初的5个增长到200+个，DataSource从1个Prometheus增加到包含10个不同集群的数据源。管理这些资源不能再靠UI手工操作了——需要一种编程化的管理方式。

Grafana全面API化是其架构设计的核心特色之一。Dashboard、DataSource、Alert、Org、User、Team——几乎所有你在UI上能做的操作，都能通过RESTful API完成。这意味着你可以用Python/Go/Shell脚本批量管理Grafana资源，也可以将Grafana配置纳入CI/CD流水线、写自动化测试、构建自定义管理工具。

但API的使用之路充满"暗坑"：认证方式的正确选择、API版本的兼容性、分页处理、错误响应解析、Rate Limit限制——这些都需要系统掌握。本章将通过5个典型自动化场景，建立起用API管理Grafana的实战能力。

## 2. 项目设计

**小胖**（揉着酸痛的手腕）：大师，我今天干了一件蠢事——花了两个半小时手工把50个Dashboard从旧Prometheus数据源迁移到新Prometheus数据源。每个Dashboard要改十几个Panel的数据源引用，改到最后手都抖了。

**大师**（哭笑不得）：你知道Grafana有API吗？你这个需求，写一个Python脚本，5分钟搞定。

**小胖**（震惊）：什么API？官方文档在哪？

**大师**：每个Grafana实例都有一个完整的OpenAPI Swagger文档，地址是`http://your-grafana:3000/swagger-ui`。所有操作都能通过API完成。

**小白**（快速打开终端）：API认证方式有哪些？

**大师**：三种主流方式：

**API Key**（已逐步被Service Account Token替代）。创建方式：Administration → API Keys → Add API Key。请求时在Header中带：`Authorization: Bearer <api_key>`。API Key不绑定到具体的Service Account，属于旧方式。

**Service Account Token**（推荐）。创建Service Account时自动生成。权限可精细控制（Org Role + Dashboard Permission），是最推荐的机器认证方式。

**Basic Auth**。用用户名+密码进行HTTP基本认证。不推荐——密码泄露风险高，且无法单独吊销。

**小胖**：那Dashboard的批量操作具体怎么用API做？

**大师**：Dashboard API的核心端点：

- `GET /api/search?type=dash-db`：列出所有Dashboard
- `GET /api/dashboards/uid/:uid`：获取Dashboard的JSON定义
- `POST /api/dashboards/db`：创建或更新Dashboard
- `DELETE /api/dashboards/uid/:uid`：删除Dashboard

批量迁移数据源的话，流程是：
1. 获取所有Dashboard的UID列表
2. 逐个获取Dashboard JSON
3. 在JSON中替换旧的`datasource`引用为新的
4. 逐个POST更新回去

**小白**（追问）：那Dashboard JSON中，数据源是具体怎么引用的？

**大师**：在Dashboard JSON中，每个Panel的`targets`数组里包含数据源引用。有两种引用方式：

旧格式（按名称）：
```json
{"datasource": "Prometheus-Prod"}
```

新格式（按UID，推荐）：
```json
{"datasource": {"type": "prometheus", "uid": "abc123"}}
```

按UID引用更可靠——因为数据源改名不会影响Dashboard。要批量迁移，就是全局替换UID。

**小胖**：API的分页呢？我有1000个Dashboard，一次API请求能全返回吗？

**大师**：默认最多返回1000条。如果超过，响应头会有`Link`指示下一页。但实际的API设计——

`/api/search` 支持`limit`参数，最大5000。也支持`page`参数分页。

更好的做法是带过滤条件缩小范围：
```
GET /api/search?type=dash-db&tag=production&limit=5000
```

**小白**：API有没有Rate Limit？

**大师**：Grafana OSS版本没有内置Rate Limit，但反向代理（Nginx/ALB）可以加。大量API调用时建议加上适度的`sleep`间隔（如0.5秒），避免对数据库造成冲击。

**技术映射**：API Key = 专用钥匙（只能开门不能换锁），Dashboard JSON = 蓝图（拿到蓝图就能重建整套系统），Swagger = 餐厅菜单（列出了所有能做的操作）。

## 3. 项目实战

**环境准备**

需要：Grafana实例（已有）、Python 3.x、`requests`库（`pip install requests`）。

**步骤一：获取API认证**

首先创建Service Account和Token：

```bash
# 方式1：通过UI创建
# Administration → Service accounts → Add service account → 保存Token

# 方式2：通过API创建（需要已有Admin Token）
curl -X POST -H "Authorization: Bearer <ADMIN_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"name": "api-bot", "role": "Admin"}' \
  http://localhost:3000/api/serviceaccounts

# 为Service Account创建Token
curl -X POST -H "Authorization: Bearer <ADMIN_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"name": "script-token"}' \
  http://localhost:3000/api/serviceaccounts/<sa_id>/tokens
```

保存返回的`key`值（只返回一次！）。

**步骤二：批量Dashboard管理脚本**

编写Python脚本 `manage_dashboards.py`：

```python
import requests
import json
import time

GRAFANA_URL = "http://localhost:3000"
TOKEN = "glsa_your_token_here"
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json"
}

def get_all_dashboards():
    """获取所有Dashboard列表"""
    resp = requests.get(
        f"{GRAFANA_URL}/api/search",
        params={"type": "dash-db", "limit": 5000},
        headers=HEADERS
    )
    resp.raise_for_status()
    return resp.json()

def get_dashboard(uid):
    """获取Dashboard完整JSON"""
    resp = requests.get(
        f"{GRAFANA_URL}/api/dashboards/uid/{uid}",
        headers=HEADERS
    )
    return resp.json()

def update_dashboard(dashboard_data):
    """更新Dashboard"""
    dashboard_data["dashboard"]["version"] += 1  # 版本号递增
    dashboard_data["overwrite"] = True
    resp = requests.post(
        f"{GRAFANA_URL}/api/dashboards/db",
        headers=HEADERS,
        json=dashboard_data
    )
    return resp.json()

def migrate_datasource(old_uid, new_uid):
    """批量迁移数据源UID"""
    dashboards = get_all_dashboards()
    print(f"找到 {len(dashboards)} 个Dashboard")

    for i, dash_info in enumerate(dashboards):
        uid = dash_info["uid"]
        title = dash_info["title"]
        print(f"[{i+1}/{len(dashboards)}] 处理: {title}")

        dash = get_dashboard(uid)
        json_str = json.dumps(dash)

        # 替换数据源引用
        if old_uid in json_str:
            json_str = json_str.replace(old_uid, new_uid)
            updated = json.loads(json_str)
            update_dashboard(updated)
            print(f"  -> 已迁移")
            time.sleep(0.5)  # 避免请求过快
        else:
            print(f"  -> 无需迁移")

# 执行迁移
migrate_datasource(
    old_uid="old-prometheus-uid",
    new_uid="new-prometheus-uid"
)
```

**步骤三：Dashboard批量导出为文件（备份）**

```python
import os

def backup_all_dashboards(output_dir="./dashboards_backup"):
    """导出所有Dashboard JSON到本地文件"""
    os.makedirs(output_dir, exist_ok=True)

    dashboards = get_all_dashboards()
    for dash_info in dashboards:
        uid = dash_info["uid"]
        title = dash_info["title"].replace("/", "_")  # 文件名不能含/

        dash = get_dashboard(uid)
        filename = f"{output_dir}/{uid}_{title}.json"
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(dash["dashboard"], f, indent=2, ensure_ascii=False)

        print(f"导出: {filename}")

backup_all_dashboards()
```

**步骤四：批量创建用户与Team**

```python
def create_users_from_csv(csv_file):
    """从CSV文件批量创建用户"""
    import csv

    with open(csv_file, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            payload = {
                "name": row["name"],
                "email": row["email"],
                "login": row["login"],
                "password": row.get("password", "ChangeMe123!"),
                "OrgId": int(row.get("org_id", 1))
            }
            resp = requests.post(
                f"{GRAFANA_URL}/api/admin/users",
                headers=HEADERS,
                json=payload
            )
            if resp.status_code == 200:
                print(f"创建用户: {row['login']} - 成功")
            elif resp.status_code == 412:
                print(f"用户已存在: {row['login']}")
            else:
                print(f"创建失败: {row['login']} - {resp.text}")

# CSV格式：name,email,login,password
# 张三,zhangsan@example.com,zhangsan,Pass123
```

**步骤五：数据源管理**

```python
def create_prometheus_datasource(name, url, org_id=1):
    """通过API创建Prometheus数据源"""
    payload = {
        "name": name,
        "type": "prometheus",
        "url": url,
        "access": "proxy",
        "orgId": org_id,
        "isDefault": False,
        "jsonData": {
            "timeInterval": "15s",
            "httpMethod": "POST"
        },
        "secureJsonData": {
            # "httpHeaderValue1": "Bearer xxx"  # 如有认证需要
        }
    }
    resp = requests.post(
        f"{GRAFANA_URL}/api/datasources",
        headers=HEADERS,
        json=payload
    )
    return resp.json()

# 创建多个集群的Prometheus数据源
for cluster in ["cluster-beijing", "cluster-shanghai", "cluster-guangzhou"]:
    result = create_prometheus_datasource(
        name=f"Prometheus-{cluster}",
        url=f"http://prometheus.{cluster}.internal:9090"
    )
    print(f"数据源创建: {result.get('datasource', {}).get('name')}")
```

**步骤六：Dashboard Provisioning API**

Dashboard Provisioning API是一种特殊的API——它不需要先获取Dashboard再修改，而是直接把JSON文件放入指定目录，Grafana自动检测并导入。

```bash
# 把Dashboard JSON放入provisioning目录
cp my-dashboard.json /etc/grafana/provisioning/dashboards/

# 配合provisioning配置
cat > /etc/grafana/provisioning/dashboards/dashboards.yaml <<EOF
apiVersion: 1
providers:
  - name: 'automated'
    orgId: 1
    folder: ''
    type: file
    updateIntervalSeconds: 30
    options:
      path: /etc/grafana/provisioning/dashboards
EOF
```

这种方式是GitOps的最佳实践——Dashboard JSON存在Git仓库，CI/CD部署到Grafana实例。

**常见坑点**
1. **Dashboard更新需要递增version**：Dashboard JSON中有一个`version`字段，每次更新必须递增，否则API返回412 Precondition Failed。
2. **UID不能重复**：Dashboard和DataSource的UID是唯一标识，创建时必须确保UID不重复。
3. **Authorization Header中的Token格式**：Bearer后面有一个空格，`Authorization: Bearer xxx`。少了空格或者用`Basic`认证会直接401。
4. **API分页不完整**：某些列表API（如team members）需要处理分页，否则只返回第一页数据。
5. **Dashboard JSON的schemaVersion**：不同Grafana版本的Dashboard JSON schemaVersion不同（如v9=36, v10=38），导入时注意兼容性。

**Grafana API常用端点速查**

| 功能 | 端点 | 方法 |
|------|------|------|
| 列出Dashboard | `/api/search?type=dash-db` | GET |
| 获取Dashboard | `/api/dashboards/uid/:uid` | GET |
| 创建/更新Dashboard | `/api/dashboards/db` | POST |
| 删除Dashboard | `/api/dashboards/uid/:uid` | DELETE |
| 列出数据源 | `/api/datasources` | GET |
| 创建数据源 | `/api/datasources` | POST |
| 删除数据源 | `/api/datasources/:id` | DELETE |
| 列出用户 | `/api/users` | GET |
| 列出团队 | `/api/teams/search` | GET |
| 列出告警规则 | `/api/v1/provisioning/alert-rules` | GET |
| 健康检查 | `/api/health` | GET |
| Grafana自身指标 | `/metrics` | GET |

## 4. 项目总结

**优点**
| 特性 | 说明 |
|------|------|
| 全面的REST API | Dashboard/DataSource/Alert/User/Org全覆盖 |
| API版本稳定 | 向后兼容性好，Deprecation有过渡期 |
| Swagger文档 | 在线可交互的API文档 |
| 多种认证 | Bearer Token / Basic Auth / API Key / Service Account |
| Provisioning API | Dashboard/DataSource的声明式管理 |

**缺点**
| 特性 | 说明 |
|------|------|
| 无批量操作 | 删除多个Dashboard需要逐个调用API |
| 无事务 | 多个API操作不能原子化 |
| 无Webhook | 资源变化不能主动推送通知 |
| 速率限制缺失 | OSS版无内置Rate Limit |

**适用场景**
1. Dashboard批量管理：批量修改、批量备份、批量迁移
2. CI/CD集成：代码提交→Jenkins→API导入Dashboard
3. 自动化监控：脚本定期检查Grafana健康状态
4. 用户生命周期管理：入职自动创建账号、离职自动删除
5. 数据平台集成：从CMDB自动同步监控配置到Grafana

**注意事项**
1. API调用需要在同源策略允许的范围内（CORS配置需正确）
2. Token泄漏后应立即在UI中删除重新生成
3. 大量API操作建议加sleep间隔保护Grafana数据库
4. 使用API前确认Grafana版本——某些端点在新版本中可能变化

**常见踩坑经验**
1. **Dashboard JSON太大导致POST失败**：部分HTTP库有默认请求大小限制（如Nginx的client_max_body_size），大Dashboard需要调整。
2. **API删除Dashboard后无法恢复**：Grafana没有回收站，删除是物理删除。建议操作前先用API备份。
3. **401 Unauthorized但Token正确**：检查Header名称是`Authorization`不是`Authorisation`或`Auth`。

**思考题**
1. 如何利用Grafana API实现"每天凌晨自动备份所有Dashboard到Git仓库"？
2. 如果想限制某个Service Account只能操作特定Folder下的Dashboard（不能操作其他Folder），API层面应该怎么做？
