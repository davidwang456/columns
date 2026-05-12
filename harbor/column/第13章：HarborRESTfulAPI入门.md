# 第13章：Harbor RESTful API 入门

## 1 项目背景

"我们公司有 68 个项目、2000+ 镜像标签，但运维还是靠人点鼠标——这跟石器时代有什么区别？"

某中型互联网公司（员工 500 人，微服务 120+）的 Harbor 运维团队正在经历从"手动 Portal 操作"向"自动化运维"转型的阵痛。Harbor 已经平稳运行了 8 个月，注册用户 85 人，每周新增镜像标签约 350 个——但运维方式几乎没有进化。

**痛点一：批量操作效率极低，人力成本爆炸。** 安全部门在一次等保测评后要求：在所有 68 个项目中统一配置 CVE 阻止策略，阻止 Critical 级别漏洞的镜像被拉取。"一个项目要点 6 次鼠标——选择项目→配置→漏洞阻止→勾选 Critical→保存→返回。68 个项目就是 408 次点击，中途还不能出错。"负责执行的运维小赵在 Portal 上操作了整整一个下午（3.5 小时），同事路过调侃他"今天你是一个人类点击器"。更糟糕的是——第二天安全部门发现漏了 3 个项目（因为 Portal 分页显示，第 3 页的项目被遗漏了），小赵被迫又花了 40 分钟补操作。

**痛点二：运维脚本缺少官方接口，只能逆向工程。** 小赵痛定思痛，尝试用 Python 脚本批量自动化。他打开 Chrome DevTools，在 Harbor Portal 上逐个操作，从 Network 标签中"抓"HTTP 请求来逆向推断 API。结果发现：创建项目用的是 `POST /api/v2.0/projects`（Basic Auth），但添加项目成员却需要 `POST /c/login` 先获取 `_sid` Cookie（Session 认证），而删除镜像又需要 Bearer Token——三种认证方式混在一起，完全没有统一范式。更坑的是，有些请求还携带了从页面 HTML 中提取的 CSRF Token，脚本跑 3 次就失效了。小赵耗时两周才"猜"出了 12 个常用 API，但仍有大量操作无法自动化。

**痛点三：CI/CD Pipeline 集成缺乏可靠 API 入口。** 公司的 Jenkins Pipeline 在每次构建后需要自动获取镜像的漏洞扫描结果——如果扫描通过（Critical=0 且 High≤5），则自动部署到 Staging 环境；否则阻断构建并通知安全团队。这个需求看似简单，但团队卡在了三个问题上：(1) Harbor 有没有提供扫描结果的 API？(2) API 的认证方式是什么，Jenkins 里怎么调用？(3) 扫描需要 3-8 分钟才能完成，API 返回的是最终结果还是中间状态？因为没人搞清楚这三个问题，安全门禁始终没能自动化，每次都是运维人员手动在 Portal 上看扫描结果后决定是否放行——平均每次操作耗时 8 分钟，每天约 25 次构建，累计浪费 3.3 人时/天。

**痛点四：文档海量但缺乏导航。** Harbor 官方提供了 Swagger UI（地址 `https://harbor.company.com/api/v2.0/swagger.json`），这个文件包含 Harbor v2.0 全部 API 定义——总共 8200+ 行 JSON，定义了约 160 个 REST 端点。面对这个"API 字典"，团队普遍反馈："我知道所有 API 都在这里，但我不知道哪些是日常高频使用的、哪些是冷门的、每个 API 的典型使用场景是什么。"一位开发同事花了整整两天通读 Swagger 文件，最后总结道："80% 的时间里我只需要其中 20% 的 API，但我花了两天才找出这 20% 是哪 20%。"

**痛点五：权限模型在 API 层面的映射不透明。** Harbor 的项目角色（项目管理员、维护者、开发者、访客、受限访客）在 Portal 上很直观——但通过 API 操作时，角色 ID 是什么？不同角色允许调用哪些 API？运维小赵曾用访客角色的账号尝试通过 API 删除镜像——API 返回了 403 Forbidden，但返回信息只有一个干巴巴的 `{"errors":[{"code":"FORBIDDEN","message":"unauthorized"}]}`，没有提示"你的角色是访客，需要至少开发者角色才能删除镜像"。这种"黑盒"式的权限反馈让团队在开发自动化脚本时频繁试错。

Harbor 的 RESTful API 覆盖了 Portal 上 95% 的操作功能。本章将系统梳理 Harbor API 的认证机制、核心 API 分类体系和自动化实战，帮读者从"鼠标点击"彻底进化到"脚本驱动"，并深入理解 API 权限模型和最佳实践。

---

## 2 项目设计——剧本式交锋对话

**场景：运维小赵在工位上喊"救命"——大师端着咖啡路过。**

---

**小胖**（从背后探过头来，幸灾乐祸）："小赵，68 个项目的 CVE 策略配完了吗？我看你从下午两点点到五点半了——Portal 页面上那个小菊花转啊转的，你是不是在假装工作啊？"

**小赵**（揉着酸痛的手指，屏幕上 Portal UI 还在加载）："还没到一半！我快吐了。点鼠标点到食指抽筋——我今天点击了超过 300 次！大师，Harbor 难道不提供个命令行工具吗？我要求不高，哪怕有个 `harbor-cli` 也好啊。"

**大师**（放下咖啡，微微一笑）："命令行工具官方确实没有，但有更好的——一套完整的 RESTful API。Portal 上 95% 的操作，本质上就是前端调了一个 API。你每次在 Portal 上点击'创建项目'，实际上就是前端发了一个 `POST /api/v2.0/projects`。你不是没有工具——你是把人当成了工具。"

**技术映射**：Harbor 的 API 全部由 Core 服务暴露，路径前缀统一为 `/api/v2.0/`（v2.x 版本）。API 遵循 RESTful 设计风格——使用标准 HTTP 方法（GET 读、POST 创、PUT 改、DELETE 删），所有请求和响应均使用 JSON 格式。Core 服务基于 Go 语言的 Beego 框架，每个 API handler 对应一个 Controller 方法。Swagger 文档由 Beego 的 `swagger` 插件自动生成，路径为 `/api/v2.0/swagger.json`。

---

**小赵**（拍桌子）："我试过了！上周用 curl 直接调 `/api/v2.0/projects`，返回 401 Unauthorized——认证怎么搞？我浏览器里 Portal 明明已经登录了，怎么 API 就认不出我？我跟它明明是一个人啊！"

**大师**（在键盘上敲了几下）："你的困惑非常典型——很多人以为'浏览器登录了'就等于'API 有权限了'，这是两个完全不同的认证通道。Harbor 支持四种 API 认证方式，但它们是互不干扰的独立体系。"

**方式一：Basic Auth（运维脚本首选）**

```bash
# 每次请求都带上用户名密码——简单、直接、永不"过期"
curl -u admin:Harbor12345 \
  https://harbor.company.com/api/v2.0/projects

# 底层原理：HTTP Header 中添加
# Authorization: Basic YWRtaW46SGFyYm9yMTIzNDU=
# 其中 Basic 后面的字符串就是 "admin:Harbor12345" 的 Base64 编码
```

**方式二：Session Cookie（Portal 登录模式）**

```bash
# 适用于：你已经登录了 Portal，想在浏览器 Console 中临时调 API
# 在浏览器 DevTools → Console 中直接运行：
fetch('/api/v2.0/projects')
  .then(r => r.json())
  .then(console.log)
# 浏览器会自动带上当前页面的 _sid Cookie
```

**方式三：Bearer Token / JWT（CI/CD 推荐）**

```bash
# 适用于：CI/CD Pipeline 或长期运行的自动化系统
# Token 有过期时间（默认 30 天），但可配置
curl -H "Authorization: Bearer eyJhbGciOiJSUzI1NiIs..." \
  https://harbor.company.com/api/v2.0/projects
```

**方式四：机器人账户 Token（生产环境最佳实践）**

```bash
# 最推荐的方式——机器人的 Token 可以精细控制权限
# 创建机器人账户（只授予需要的权限，如仅 push）
# 然后用机器人的用户名+Token 调用 API
curl -u "robot\$order-platform+gitlab-ci:Zpn87KqL2x..." \
  https://harbor.company.com/api/v2.0/projects/order-platform/repositories
```

**大师**："四种方式各有适用场景。但如果你在做生产环境的 CI/CD 集成，我强烈推荐**机器人账户 Token**。不是因为你不能把 admin 密码写在 Jenkins 里——而是因为机器人账户可以做到最小权限原则：一个机器人只给 push 权限，即使 Token 泄露了，攻击者也拉取不到镜像，更删除不了仓库。而如果你用的是 admin 的 Basic Auth——一旦泄露，整个 Harbor 就裸奔了。"

**技术映射**：Harbor Core 服务的认证中间件位于 `src/core/middlewares/` 目录下。请求到达后，中间件按以下顺序检查认证：1) 检查 `Authorization: Bearer <jwt>` header；2) 检查 `Authorization: Basic <base64>` header；3) 检查 `Cookie: _sid=<session_id>`。如果三者都不存在或均无效，返回 401 并附带 `WWW-Authenticate: Bearer realm="https://harbor.company.com/service/token"` header。JWT Token 由 Core 服务签发，签名密钥在 `harbor.yml` 的 `core.secret` 中配置。

---

**小白**（刚从其他部门转岗过来，笔记本上记满了问题）："大师，那 API 的分类和结构是怎样的？我看了下 Swagger 文件——8200 行，160 个端点。我根本不知道从哪看起，感觉像是在看一本没有目录的百科全书。"

**大师**（在小白笔记本上画了一个分类脑图）："别被 8200 行吓到。Harbor API 可以按功能域分成 10 大类，而你日常用到的核心 API 也就 3-4 类。先把这个分类框架记在脑子里，然后按需深挖——这比你通读 8200 行 Swagger 高效得多。"

| API 分类 | 路径前缀 | 使用频率 | 端点数量 | 核心操作 | 典型场景 |
|---------|---------|---------|---------|---------|---------|
| 项目管理 | `/projects` | ⭐⭐⭐⭐⭐ | ~15 | 创建/删除/列表/更新项目、配额、CVE 策略 | 批量创建项目、配置扫描策略 |
| 仓库管理 | `/projects/{name}/repositories` | ⭐⭐⭐⭐⭐ | ~8 | 列仓库、查资源、删除仓库 | CI 查询镜像列表、清理旧镜像 |
| 制品管理 | `.../artifacts` | ⭐⭐⭐⭐ | ~20 | 查 manifest、标签增删、扫描报告 | 获取扫描结果、打 release 标签 |
| 用户管理 | `/users` | ⭐⭐⭐ | ~10 | 创建/查询/更新用户、设置管理员 | 运维批量创建开发者账号 |
| 成员管理 | `/projects/{id}/members` | ⭐⭐⭐⭐ | ~5 | 添加/删除/修改项目成员角色 | 新员工入职统一授权 |
| 复制管理 | `/replication` | ⭐⭐⭐ | ~12 | 创建复制规则、查看执行状态 | 配置跨地域镜像同步 |
| 扫描管理 | `/scanners` | ⭐⭐⭐ | ~6 | 查看扫描器状态、获取扫描报告 | 集成到 CI Pipeline 安全门禁 |
| 审计日志 | `/audit-logs` | ⭐⭐ | ~3 | 按时间/用户/操作过滤查询 | 合规审计、安全事件溯源 |
| 系统管理 | `/system` | ⭐⭐ | ~10 | GC、配置查看、健康检查 | 日常运维监控 |
| 统计信息 | `/statistics` | ⭐ | ~5 | Dashboard 统计数据 | 自定义 Dashboard |

**小白**（眼睛发亮）："这个分类太清晰了！那每个 API 的输入输出规范呢？比如我调用创建项目 API，必填参数是哪些？返回的数据结构是什么？"

**大师**："好问题——这正是 Harbor API 的规范之处。以创建项目为例："

```bash
# 请求
POST /api/v2.0/projects
Content-Type: application/json

{
  "project_name": "my-service",      # 必填：项目名称（唯一）
  "public": false,                    # 可选：是否公开（默认 false）
  "storage_limit": 107374182400,     # 可选：存储配额（字节，默认 -1 无限）
  "registry_id": null,               # 可选：关联的代理缓存 Registry ID
  "metadata": {
    "public": "false",               # 可选：元数据
    "enable_content_trust": "false",
    "auto_scan": "true"              # 开启自动扫描
  }
}

# 成功响应 201 Created
# Headers: Location: /api/v2.0/projects/42
{
  "project_id": 42,
  "name": "my-service",
  "owner_name": "admin",
  "current_user_role_id": 1,         # 1=管理员, 2=开发者, 3=访客
  "repo_count": 0,
  "creation_time": "2024-03-15T08:22:10Z",
  "update_time": "2024-03-15T08:22:10Z"
}
```

**技术映射**：Harbor Core 使用 Beego 框架的 `orm` 包与 PostgreSQL 交互。创建项目的 handler 在 `src/controller/project/controller.go` 中，收到 POST 请求后依次做：参数校验 → 项目名唯一性检查 → 在 `project` 表中插入记录 → 在 `project_member` 表中为创建者添加管理员角色 → 返回 201 和项目详情。事务性操作保证原子性——任何一步失败都会回滚。

---

**小胖**（不耐烦了，敲桌子）："道理我都懂。但大师你直接给我几个'开箱即用'的脚本行不行？我现在就需要——批量创建项目 + 配置扫描策略 + 添加项目成员。别讲架构了，我只要能跑起来的代码！"

**大师**（在白板上飞快写下几个脚本模板）："给你三个最常用的脚本模板——拿回去改几个参数就能用。但我提醒你：这些都是 Bash 脚本，生产环境长期维护建议用 Python 封装成 SDK（后面会讲）。"

```bash
#!/bin/bash
# 模板一：批量创建项目 + 配置元数据
# 场景：新业务线启动，需要创建 15 个项目并配置统一策略

HARBOR="https://harbor.company.com"
AUTH="admin:Harbor12345"

# 项目名数组
PROJECTS=(
  "order-platform" "payment-platform" "user-platform"
  "logistics-platform" "data-platform" "ai-platform"
  "monitoring-platform" "shared-base" "ci-snapshots"
  "security-tools" "message-queue" "api-gateway"
  "search-platform" "analytics-platform" "test-sandbox"
)

for project in "${PROJECTS[@]}"; do
  echo "[$(date +%H:%M:%S)] Creating: $project"
  
  PUBLIC=false
  # 共享基础镜像项目设为公开只读
  [[ "$project" == "shared-base" ]] && PUBLIC=true
  
  RESPONSE=$(curl -s -w "\n%{http_code}" -u "$AUTH" \
    -X POST \
    -H "Content-Type: application/json" \
    -d "{
      \"project_name\": \"$project\",
      \"public\": $PUBLIC,
      \"storage_limit\": 214748364800,
      \"metadata\": {
        \"auto_scan\": \"true\",
        \"enable_content_trust\": \"true\"
      }
    }" \
    "$HARBOR/api/v2.0/projects")
  
  HTTP_CODE=$(echo "$RESPONSE" | tail -1)
  BODY=$(echo "$RESPONSE" | sed '$d')
  
  if [ "$HTTP_CODE" = "201" ]; then
    PROJECT_ID=$(echo "$BODY" | jq -r '.project_id')
    echo "  ✓ Created (ID: $PROJECT_ID)"
  elif [ "$HTTP_CODE" = "409" ]; then
    echo "  ⚠ Already exists (skipped)"
  else
    echo "  ✗ Failed (HTTP $HTTP_CODE): $BODY"
  fi
done
```

**小胖**（记录完代码）："这个脚本我今晚就能用。但大师，如果我需要查一个镜像有没有漏洞、甚至自动根据扫描结果决定要不要部署——这个 API 怎么查？"

**大师**："这是最常见的 CI 集成场景——我给你一个更完整的脚本模板。"

```bash
#!/bin/bash
# 模板二：获取镜像扫描报告并做安全门禁判断
# 场景：CI Pipeline 中调用，判断是否可以继续部署

PROJECT="order-platform"
REPO="order-service"
TAG="v2.4.1"
MAX_WAIT=300  # 最多等 5 分钟

echo "[$(date +%H:%M:%S)] Waiting for scan on $PROJECT/$REPO:$TAG..."

START_TIME=$(date +%s)

while true; do
  SCAN_DATA=$(curl -s -u admin:Harbor12345 \
    "https://harbor.company.com/api/v2.0/projects/$PROJECT/repositories/$REPO/artifacts/$TAG?with_scan_overview=true")
  
  SCAN_STATUS=$(echo "$SCAN_DATA" | jq -r \
    '.scan_overview | to_entries[0].value.scan_status // "Pending"')
  
  case "$SCAN_STATUS" in
    "Success")
      CRITICAL=$(echo "$SCAN_DATA" | jq -r \
        '.scan_overview | to_entries[0].value.summary.summary.Critical // 0')
      HIGH=$(echo "$SCAN_DATA" | jq -r \
        '.scan_overview | to_entries[0].value.summary.summary.High // 0')
      MEDIUM=$(echo "$SCAN_DATA" | jq -r \
        '.scan_overview | to_entries[0].value.summary.summary.Medium // 0')
      
      echo "========================================="
      echo "  Scan Result: $PROJECT/$REPO:$TAG"
      echo "  Critical:  $CRITICAL"
      echo "  High:      $HIGH"
      echo "  Medium:    $MEDIUM"
      echo "========================================="
      
      if [ "$CRITICAL" -gt 0 ]; then
        echo "🚫 BLOCKED: $CRITICAL Critical CVE(s) found"
        exit 1
      fi
      if [ "$HIGH" -gt 5 ]; then
        echo "🚫 BLOCKED: $HIGH High CVE(s) > threshold (5)"
        exit 1
      fi
      echo "✅ PASSED: Deploy allowed"
      exit 0
      ;;
    "Error")
      echo "❌ Scan failed"
      exit 2
      ;;
    *)
      ELAPSED=$(($(date +%s) - START_TIME))
      if [ "$ELAPSED" -gt "$MAX_WAIT" ]; then
        echo "⏰ Scan timed out after ${MAX_WAIT}s"
        exit 3
      fi
      echo "  Scan status: $SCAN_STATUS (waited ${ELAPSED}s)..."
      sleep 10
      ;;
  esac
done
```

**技术映射**：`with_scan_overview=true` 参数告诉 Core 在返回制品信息时附带最新的扫描报告。扫描概述的 key 是 MIME 类型——`application/vnd.scanner.adapter.vuln.report.harbor+json; version=1.0`。这个 key 表示报告由 Harbor 的扫描适配器（如 Trivy）生成。如果项目配置了多个扫描器，`scan_overview` 中会包含多个 key。扫描状态有四种：`Pending`（等待扫描）、`Scanning`（扫描中）、`Success`（扫描成功）、`Error`（扫描失败）。

---

**大师**（转向小白）："小胖要的是快速上手的脚本，但小白你如果想深入——我建议你用 Python 封装一个 HarborClient 类。长期来看，Python SDK 比 Bash 脚本更易维护、更容易做错误处理和重试逻辑。"

```python
#!/usr/bin/env python3
"""
Harbor RESTful API Client
支持 Basic Auth / Robot Token 认证
提供重试机制、分页自动遍历、错误处理
"""

import requests
import time
from typing import Optional, List, Dict, Any
from urllib3.exceptions import InsecureRequestWarning

requests.packages.urllib3.disable_warnings(InsecureRequestWarning)


class HarborClient:
    """Harbor RESTful API 客户端封装"""
    
    def __init__(self, base_url: str, username: str, password: str,
                 verify_ssl: bool = False, timeout: int = 30):
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout
        self.session = requests.Session()
        self.session.auth = (username, password)
        self.session.verify = verify_ssl
        self.session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json"
        })
    
    def _url(self, path: str) -> str:
        return f"{self.base_url}/api/v2.0{path}"
    
    def _request(self, method: str, path: str, retries: int = 3, **kwargs) -> requests.Response:
        """带重试的请求方法"""
        url = self._url(path)
        kwargs.setdefault("timeout", self.timeout)
        
        for attempt in range(retries):
            try:
                resp = self.session.request(method, url, **kwargs)
                # 429 或 5xx 才需要重试
                if resp.status_code >= 500 or resp.status_code == 429:
                    if attempt < retries - 1:
                        wait = 2 ** attempt
                        print(f"  Retry in {wait}s (HTTP {resp.status_code})...")
                        time.sleep(wait)
                        continue
                return resp
            except requests.exceptions.ConnectionError as e:
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise e
        return resp
    
    # ==================== 项目管理 ====================
    
    def list_projects(self, page_size: int = 100) -> List[Dict]:
        """获取所有项目（自动处理分页）"""
        all_projects = []
        page = 1
        while True:
            resp = self._request("GET", f"/projects?page={page}&page_size={page_size}")
            resp.raise_for_status()
            projects = resp.json()
            if not projects:
                break
            all_projects.extend(projects)
            if len(projects) < page_size:
                break
            page += 1
        return all_projects
    
    def get_project(self, name: str) -> Optional[Dict]:
        """按名称查找项目"""
        resp = self._request("GET", f"/projects?name={name}")
        resp.raise_for_status()
        projects = resp.json()
        for p in projects:
            if p['name'] == name:
                return p
        return None
    
    def create_project(self, name: str, public: bool = False,
                       storage_limit: int = -1, auto_scan: bool = True) -> Dict:
        """创建项目"""
        data = {
            "project_name": name,
            "public": public,
            "storage_limit": storage_limit,
            "metadata": {
                "public": str(public).lower(),
                "auto_scan": str(auto_scan).lower()
            }
        }
        resp = self._request("POST", "/projects", json=data)
        if resp.status_code == 409:
            print(f"  Project '{name}' already exists")
            return self.get_project(name)
        resp.raise_for_status()
        return resp.json()
    
    def delete_project(self, project_id: int) -> bool:
        """删除项目"""
        resp = self._request("DELETE", f"/projects/{project_id}")
        return resp.status_code == 200
    
    def update_cve_policy(self, project_id: int, severity: str = "critical",
                          prevent_vul: bool = True, scan_on_push: bool = True) -> Dict:
        """配置 CVE 阻止策略"""
        data = {
            "prevent_vul": prevent_vul,
            "severity": severity,
            "scan_on_push": scan_on_push
        }
        resp = self._request("PUT",
            f"/projects/{project_id}/prevent-vulnerability", json=data)
        resp.raise_for_status()
        return resp.json()
    
    # ==================== 仓库与制品管理 ====================
    
    def list_repositories(self, project: str, page_size: int = 100) -> List[Dict]:
        """列出项目下所有仓库（自动分页）"""
        all_repos = []
        page = 1
        while True:
            resp = self._request("GET",
                f"/projects/{project}/repositories?page={page}&page_size={page_size}")
            resp.raise_for_status()
            repos = resp.json()
            if not repos:
                break
            all_repos.extend(repos)
            if len(repos) < page_size:
                break
            page += 1
        return all_repos
    
    def get_scan_overview(self, project: str, repo: str, tag: str) -> Dict:
        """获取镜像扫描报告"""
        path = f"/projects/{project}/repositories/{repo}/artifacts/{tag}"
        resp = self._request("GET", path,
                             params={"with_scan_overview": "true"})
        resp.raise_for_status()
        return resp.json().get('scan_overview', {})
    
    def delete_artifact(self, project: str, repo: str, tag: str) -> bool:
        """删除镜像标签"""
        path = f"/projects/{project}/repositories/{repo}/artifacts/{tag}/tags/{tag}"
        resp = self._request("DELETE", path)
        return resp.status_code == 200
    
    def add_tag(self, project: str, repo: str, reference: str, tag: str) -> Dict:
        """为镜像打标签（如打 release 标签）"""
        path = f"/projects/{project}/repositories/{repo}/artifacts/{reference}/tags"
        resp = self._request("POST", path, json={"name": tag})
        resp.raise_for_status()
        return resp.json()
    
    # ==================== 成员管理 ====================
    
    def add_project_member(self, project_id: int, username: str,
                           role_id: int = 2) -> Dict:
        """
        添加项目成员
        role_id: 1=项目管理员, 2=开发者, 3=访客, 4=受限访客
        """
        data = {
            "role_id": role_id,
            "member_user": {"username": username}
        }
        resp = self._request("POST",
            f"/projects/{project_id}/members", json=data)
        resp.raise_for_status()
        return resp.json()
    
    def list_project_members(self, project_id: int) -> List[Dict]:
        resp = self._request("GET", f"/projects/{project_id}/members")
        resp.raise_for_status()
        return resp.json()
    
    # ==================== 审计日志 ====================
    
    def get_audit_logs(self, begin_ts: int = None, end_ts: int = None,
                       operation: str = None, username: str = None,
                       page_size: int = 50) -> List[Dict]:
        """获取审计日志"""
        params = {"page_size": page_size, "page": 1}
        if begin_ts:
            params["begin_timestamp"] = begin_ts
        if end_ts:
            params["end_timestamp"] = end_ts
        if operation:
            params["operation"] = operation
        if username:
            params["username"] = username
        
        all_logs = []
        while True:
            resp = self._request("GET", "/audit-logs", params=params)
            resp.raise_for_status()
            logs = resp.json()
            if not logs:
                break
            all_logs.extend(logs)
            if len(logs) < page_size:
                break
            params["page"] += 1
        return all_logs
    
    # ==================== 系统管理 ====================
    
    def health_check(self) -> Dict:
        resp = self._request("GET", "/health")
        resp.raise_for_status()
        return resp.json()
    
    def get_system_info(self) -> Dict:
        resp = self._request("GET", "/systeminfo")
        resp.raise_for_status()
        return resp.json()


# ==================== 使用示例 ====================
if __name__ == "__main__":
    # 初始化客户端
    harbor = HarborClient("https://harbor.company.com", "admin", "Harbor12345")
    
    # 1. 健康检查
    health = harbor.health_check()
    print(f"Harbor Status: {health.get('status')}")
    
    # 2. 获取所有项目
    projects = harbor.list_projects()
    print(f"\nTotal Projects: {len(projects)}")
    for p in projects[:5]:
        print(f"  - {p['name']} (ID: {p['project_id']}, "
              f"Repos: {p['repo_count']}, Public: {p['metadata'].get('public', 'false')})")
    
    # 3. 为所有项目配置 CVE 策略
    print("\nConfiguring CVE policy for all projects...")
    for p in projects:
        try:
            harbor.update_cve_policy(p['project_id'], severity="critical")
            print(f"  ✓ {p['name']}")
        except Exception as e:
            print(f"  ✗ {p['name']}: {e}")
    
    # 4. 获取指定镜像的扫描结果
    scan = harbor.get_scan_overview("order-platform", "order-service", "v2.4.1")
    if scan:
        entry = list(scan.values())[0]
        print(f"\nScan for order-service:v2.4.1:")
        print(f"  Status: {entry.get('scan_status')}")
        print(f"  Severity: {entry.get('severity', 'N/A')}")
        summary = entry.get('summary', {})
        print(f"  Total CVEs: {summary.get('total', 0)}")
        vuln_summary = summary.get('summary', {})
        for level in ['Critical', 'High', 'Medium', 'Low']:
            print(f"  {level}: {vuln_summary.get(level, 0)}")
```

---

## 3 项目实战

### 3.1 环境准备

| 组件 | 版本/配置 | 说明 |
|------|----------|------|
| Harbor | v2.12 (Docker Compose) | Core API v2.0 |
| Python | 3.9+ | 推荐用于 SDK 封装 |
| `jq` | 1.6+ | JSON 命令行处理工具 |
| `curl` | 7.68+ | API 调试工具 |
| Postman / Insomnia | 最新版 | API 可视化调试（可选） |
| Harbor 账号 | admin 或项目管理员 | 需要 API 调用权限 |
| 网络连通性 | 能访问 Harbor HTTPS 端口 | 自签名证书需配置信任 |

```bash
# 环境变量配置
export HARBOR_URL="https://harbor.company.com"
export HARBOR_USER="admin"
export HARBOR_PASS="Harbor12345"

# 验证 API 连通性
curl -sf -u "$HARBOR_USER:$HARBOR_PASS" \
  "$HARBOR_URL/api/v2.0/health" | jq .
# 输出: { "status": "healthy" }

# 验证 API 版本
curl -sf -u "$HARBOR_USER:$HARBOR_PASS" \
  "$HARBOR_URL/api/v2.0/systeminfo" | jq '.harbor_version'
# 输出: "v2.12.0"
```

### 3.2 步骤一：批量创建 15 个项目

**业务背景**：公司启动新业务线，需要为 15 个微服务创建独立的 Harbor 项目，统一配置 200GB 配额、自动扫描。

```bash
#!/bin/bash
# step1-create-projects.sh

HARBOR="https://harbor.company.com"
AUTH="admin:Harbor12345"

declare -A PROJECT_CONFIGS=(
  ["order-platform"]="false"
  ["payment-platform"]="false"
  ["user-platform"]="false"
  ["logistics-platform"]="false"
  ["data-platform"]="false"
  ["ai-platform"]="false"
  ["message-queue"]="false"
  ["api-gateway"]="false"
  ["monitoring-platform"]="false"
  ["search-platform"]="false"
  ["analytics-platform"]="false"
  ["shared-base"]="true"
  ["ci-snapshots"]="false"
  ["security-tools"]="false"
  ["test-sandbox"]="false"
)

SUCCESS=0
SKIPPED=0
FAILED=0

for project in "${!PROJECT_CONFIGS[@]}"; do
  PUBLIC="${PROJECT_CONFIGS[$project]}"
  
  response=$(curl -s -w "\nHTTP_CODE:%{http_code}" -u "$AUTH" \
    -X POST \
    -H "Content-Type: application/json" \
    -d "{
      \"project_name\": \"$project\",
      \"public\": $PUBLIC,
      \"storage_limit\": 214748364800,
      \"metadata\": {
        \"auto_scan\": \"true\",
        \"enable_content_trust\": \"true\"
      }
    }" \
    "$HARBOR/api/v2.0/projects")
  
  http_code=$(echo "$response" | grep "HTTP_CODE:" | cut -d: -f2)
  body=$(echo "$response" | sed '/HTTP_CODE:/d')
  
  case "$http_code" in
    201)
      project_id=$(echo "$body" | jq -r '.project_id')
      echo "✓ $project (ID: $project_id, Public: $PUBLIC)"
      SUCCESS=$((SUCCESS + 1))
      ;;
    409)
      echo "⚠ $project (already exists, skipped)"
      SKIPPED=$((SKIPPED + 1))
      ;;
    *)
      echo "✗ $project (HTTP $http_code): $(echo "$body" | jq -r '.errors[0].message // "unknown"')"
      FAILED=$((FAILED + 1))
      ;;
  esac
done

echo "================================"
echo "Total: $((SUCCESS + SKIPPED + FAILED))"
echo "Created: $SUCCESS | Skipped: $SKIPPED | Failed: $FAILED"
```

**运行输出示例**：
```
✓ order-platform (ID: 1, Public: false)
✓ payment-platform (ID: 2, Public: false)
⚠ shared-base (already exists, skipped)
✓ ci-snapshots (ID: 15, Public: false)
================================
Total: 15
Created: 13 | Skipped: 2 | Failed: 0
```

### 3.3 步骤二：配置所有项目的 CVE 阻止策略

**业务背景**：安全部门要求在 15 个项目中统一配置：阻止 Critical 级别漏洞、启用推送即扫描。

```bash
#!/bin/bash
# step2-cve-policy.sh

HARBOR="https://harbor.company.com"
AUTH="admin:Harbor12345"

# 获取所有项目列表
echo "Fetching all projects..."
projects=$(curl -s -u "$AUTH" \
  "$HARBOR/api/v2.0/projects?page_size=100" | jq -r '.[] | "\(.project_id):\(.name)"')

COUNT=0
while IFS=: read -r pid pname; do
  COUNT=$((COUNT + 1))
  
  response=$(curl -s -o /dev/null -w "%{http_code}" -u "$AUTH" \
    -X PUT \
    -H "Content-Type: application/json" \
    -d '{"prevent_vul":true,"severity":"critical","scan_on_push":true}' \
    "$HARBOR/api/v2.0/projects/$pid/prevent-vulnerability")
  
  if [ "$response" = "200" ]; then
    echo "  ✓ [$COUNT] $pname (ID: $pid) — CVE policy configured"
  else
    echo "  ✗ [$COUNT] $pname (ID: $pid) — HTTP $response"
  fi
done <<< "$projects"

echo "Configured CVE policy for $COUNT projects"
```

**运行输出示例**：
```
Fetching all projects...
  ✓ [1] order-platform (ID: 1) — CVE policy configured
  ✓ [2] payment-platform (ID: 2) — CVE policy configured
  ✓ [3] user-platform (ID: 3) — CVE policy configured
  ...
  ✓ [15] test-sandbox (ID: 15) — CVE policy configured
Configured CVE policy for 15 projects
```

### 3.4 步骤三：批量添加项目成员

**业务背景**：开发团队 8 人需要访问各自负责的项目。使用 API 批量授权，避免手动逐个配置。

```bash
#!/bin/bash
# step3-add-members.sh

HARBOR="https://harbor.company.com"
AUTH="admin:Harbor12345"

# 定义开发者和项目的映射关系
# 格式: "username:project_name:role_id"
# role_id: 1=管理员, 2=开发者, 3=访客, 4=受限访客
declare -a ASSIGNMENTS=(
  "zhangsan:order-platform:2"
  "zhangsan:payment-platform:2"
  "lisi:order-platform:2"
  "lisi:logistics-platform:2"
  "wangwu:data-platform:1"
  "wangwu:ai-platform:1"
  "zhaoliu:user-platform:2"
  "zhaoliu:message-queue:2"
  "sunqi:api-gateway:2"
  "zhouba:monitoring-platform:2"
  "wujiu:search-platform:2"
  "zhengshi:analytics-platform:2"
  "zhengshi:security-tools:1"
)

for assignment in "${ASSIGNMENTS[@]}"; do
  IFS=: read -r username project role_id <<< "$assignment"
  
  # 获取项目 ID
  project_id=$(curl -s -u "$AUTH" \
    "$HARBOR/api/v2.0/projects?name=$project" | jq -r '.[0].project_id')
  
  if [ -z "$project_id" ] || [ "$project_id" = "null" ]; then
    echo "  ✗ Project '$project' not found — skip"
    continue
  fi
  
  # 添加成员
  response=$(curl -s -w "\n%{http_code}" -u "$AUTH" \
    -X POST \
    -H "Content-Type: application/json" \
    -d "{
      \"role_id\": $role_id,
      \"member_user\": {\"username\": \"$username\"}
    }" \
    "$HARBOR/api/v2.0/projects/$project_id/members")
  
  http_code=$(echo "$response" | tail -1)
  
  role_label=""
  case $role_id in
    1) role_label="管理员" ;;
    2) role_label="开发者" ;;
    3) role_label="访客" ;;
    4) role_label="受限访客" ;;
  esac
  
  if [ "$http_code" = "201" ]; then
    echo "  ✓ $username → $project ($role_label)"
  elif [ "$http_code" = "409" ]; then
    echo "  ⚠ $username already in $project"
  else
    echo "  ✗ $username → $project (HTTP $http_code)"
  fi
done
```

**运行输出示例**：
```
  ✓ zhangsan → order-platform (开发者)
  ✓ zhangsan → payment-platform (开发者)
  ✓ lisi → order-platform (开发者)
  ⚠ lisi already in logistics-platform
  ✓ wangwu → data-platform (管理员)
  ...
  ✓ zhengshi → security-tools (管理员)
```

### 3.5 步骤四：清理过期 CI 构建标签

**业务背景**：CI 每次构建都会推送一个带 commit SHA 的标签。一个月下来积累了 800+ 个标签，占用大量存储空间。需要删除 30 天前的非 release 标签。

```bash
#!/bin/bash
# step4-cleanup-old-tags.sh

HARBOR="https://harbor.company.com"
AUTH="admin:Harbor12345"
PROJECT="ci-snapshots"
DAYS_THRESHOLD=30
CUTOFF_DATE=$(date -d "$DAYS_THRESHOLD days ago" +%Y-%m-%d)

echo "=== Cleaning up tags older than $CUTOFF_DATE ==="

# 获取所有仓库
repos=$(curl -s -u "$AUTH" \
  "$HARBOR/api/v2.0/projects/$PROJECT/repositories?page_size=100" | \
  jq -r '.[].name')

TOTAL_DELETED=0
TOTAL_SIZE_FREED=0

while IFS= read -r repo; do
  # 提取仓库的简短名称
  repo_short=$(echo "$repo" | cut -d/ -f2-)
  
  # 获取该仓库下的所有制品（分页）
  page=1
  while true; do
    artifacts=$(curl -s -u "$AUTH" \
      "$HARBOR/api/v2.0/projects/$PROJECT/repositories/$repo_short/artifacts?page=$page&page_size=50&with_tag=true")
    
    count=$(echo "$artifacts" | jq '. | length')
    if [ "$count" -eq 0 ]; then
      break
    fi
    
    # 遍历制品，找到需要删除的标签
    echo "$artifacts" | jq -r '.[] | select(.tags != null) | .tags[]? | 
      select((.name | test("^release-|^latest$") | not) and 
             (.push_time < "'$CUTOFF_DATE'")) | 
      "\(.name)|\(.push_time)"' | while IFS='|' read -r tag push_time; do
      
      if [ -n "$tag" ]; then
        echo "  Deleting: $repo:$tag (pushed $push_time)"
        
        http_code=$(curl -s -o /dev/null -w "%{http_code}" -u "$AUTH" \
          -X DELETE \
          "$HARBOR/api/v2.0/projects/$PROJECT/repositories/$repo_short/artifacts/$tag/tags/$tag")
        
        if [ "$http_code" = "200" ]; then
          TOTAL_DELETED=$((TOTAL_DELETED + 1))
        else
          echo "    ✗ Failed (HTTP $http_code)"
        fi
      fi
    done
    
    if [ "$count" -lt 50 ]; then
      break
    fi
    page=$((page + 1))
  done
done <<< "$repos"

echo "========================================="
echo "Total tags deleted: $TOTAL_DELETED"
echo "Tip: Run GC to reclaim storage: curl -X POST -u \"$AUTH\" \"$HARBOR/api/v2.0/system/gc/schedule\""
```

**运行输出示例**：
```
=== Cleaning up tags older than 2024-01-15 ===
  Deleting: ci-snapshots/order-service:a3f2b1c (pushed 2024-01-10T08:22:00Z)
  Deleting: ci-snapshots/order-service:d5e6f7a (pushed 2024-01-08T14:15:30Z)
  Deleting: ci-snapshots/payment-service:b8c9d0e (pushed 2024-01-05T11:30:00Z)
  ...
=========================================
Total tags deleted: 247
```

### 3.6 步骤五：构建镜像安全门禁流水线

**业务背景**：在 Jenkins Pipeline 中集成 Harbor API，构建镜像后等待扫描完成，根据 CVE 结果决定是否继续部署。

```groovy
// Jenkinsfile — Harbor API 集成安全门禁
pipeline {
    agent any
    
    environment {
        HARBOR_HOST = 'harbor.company.com'
        HARBOR_PROJECT = 'order-platform'
        HARBOR_REPO = 'order-service'
        HARBOR_USER = 'robot$order-platform+jenkins'
        HARBOR_TOKEN = credentials('harbor-jenkins-token')
        IMAGE_TAG = "ci-${env.BUILD_NUMBER}-${env.GIT_COMMIT?.take(7)}"
        SCAN_TIMEOUT = '300'
        CRITICAL_THRESHOLD = '0'
        HIGH_THRESHOLD = '5'
    }
    
    stages {
        stage('Build Image') {
            steps {
                sh """
                    docker build \
                        -t ${HARBOR_HOST}/${HARBOR_PROJECT}/${HARBOR_REPO}:${IMAGE_TAG} \
                        --build-arg GIT_COMMIT=${env.GIT_COMMIT} \
                        .
                """
            }
        }
        
        stage('Push to Harbor') {
            steps {
                sh """
                    echo "\${HARBOR_TOKEN}" | docker login ${HARBOR_HOST} -u ${HARBOR_USER} --password-stdin
                    docker push ${HARBOR_HOST}/${HARBOR_PROJECT}/${HARBOR_REPO}:${IMAGE_TAG}
                """
            }
        }
        
        stage('Security Scan Gate') {
            steps {
                script {
                    def startTime = System.currentTimeMillis()
                    def maxWait = env.SCAN_TIMEOUT.toInteger() * 1000
                    def scanPassed = false
                    
                    while (System.currentTimeMillis() - startTime < maxWait) {
                        def scanJson = sh(
                            script: """
                                curl -sf -u "${HARBOR_USER}:${HARBOR_TOKEN}" \\
                                    "https://${HARBOR_HOST}/api/v2.0/projects/${HARBOR_PROJECT}/repositories/${HARBOR_REPO}/artifacts/${IMAGE_TAG}?with_scan_overview=true"
                            """,
                            returnStdout: true
                        ).trim()
                        
                        def scanStatus = sh(
                            script: "echo '${scanJson}' | jq -r '.scan_overview | to_entries[0].value.scan_status // \"Pending\"'",
                            returnStdout: true
                        ).trim()
                        
                        if (scanStatus == 'Success') {
                            def critical = sh(
                                script: "echo '${scanJson}' | jq -r '.scan_overview | to_entries[0].value.summary.summary.Critical // 0'",
                                returnStdout: true
                            ).trim().toInteger()
                            
                            def high = sh(
                                script: "echo '${scanJson}' | jq -r '.scan_overview | to_entries[0].value.summary.summary.High // 0'",
                                returnStdout: true
                            ).trim().toInteger()
                            
                            echo "Scan Results — Critical: ${critical}, High: ${high}"
                            
                            if (critical > env.CRITICAL_THRESHOLD.toInteger()) {
                                error("🚫 BLOCKED: ${critical} Critical CVEs found!")
                            }
                            if (high > env.HIGH_THRESHOLD.toInteger()) {
                                error("🚫 BLOCKED: ${high} High CVEs exceed threshold!")
                            }
                            
                            echo "✅ Security gate PASSED"
                            scanPassed = true
                            break
                        } else if (scanStatus == 'Error') {
                            error("❌ Scan failed with Error status")
                        }
                        
                        echo "Scan status: ${scanStatus} — waiting 10s..."
                        sleep 10
                    }
                    
                    if (!scanPassed) {
                        error("⏰ Scan timed out after ${env.SCAN_TIMEOUT}s")
                    }
                }
            }
        }
        
        stage('Deploy to Staging') {
            steps {
                sh """
                    kubectl set image deployment/${HARBOR_REPO} \
                        ${HARBOR_REPO}=${HARBOR_HOST}/${HARBOR_PROJECT}/${HARBOR_REPO}:${IMAGE_TAG} \
                        -n staging
                    kubectl rollout status deployment/${HARBOR_REPO} -n staging --timeout=120s
                """
            }
        }
        
        stage('Tag as Release') {
            when {
                branch 'main'
            }
            steps {
                sh """
                    curl -sf -u "${HARBOR_USER}:${HARBOR_TOKEN}" \\
                        -X POST \\
                        -H "Content-Type: application/json" \\
                        -d '{"name": "release-${env.BUILD_NUMBER}"}' \\
                        "https://${HARBOR_HOST}/api/v2.0/projects/${HARBOR_PROJECT}/repositories/${HARBOR_REPO}/artifacts/${IMAGE_TAG}/tags"
                """
                echo "✅ Tagged as release-${env.BUILD_NUMBER}"
            }
        }
    }
    
    post {
        failure {
            script {
                def message = "Pipeline FAILED: ${env.JOB_NAME} #${env.BUILD_NUMBER}"
                // 发送告警到钉钉/企微/Slack
                sh """
                    curl -X POST https://hooks.slack.com/services/XXX/YYY/ZZZ \\
                        -H "Content-Type: application/json" \\
                        -d '{"text": "${message}"}'
                """
            }
        }
    }
}
```

### 3.7 步骤六：配置定时 GC（垃圾回收）

**业务背景**：删除镜像标签后，Blob 数据不会立即从磁盘清除——需要执行 GC 来回收空间。

```bash
#!/bin/bash
# step6-schedule-gc.sh

HARBOR="https://harbor.company.com"
AUTH="admin:Harbor12345"

# 创建每日凌晨 3 点的 GC 定时任务（Cron 表达式）
echo "Creating daily GC schedule (3:00 AM)..."

curl -s -u "$AUTH" \
  -X POST \
  -H "Content-Type: application/json" \
  -d '{
    "schedule": {
      "type": "Custom",
      "cron": "0 3 * * *"
    },
    "parameters": {
      "delete_untagged": true,
      "dry_run": false,
      "workers": 3
    }
  }' \
  "$HARBOR/api/v2.0/system/gc/schedule" | jq .

echo ""
echo "Checking current GC schedule:"
curl -s -u "$AUTH" \
  "$HARBOR/api/v2.0/system/gc/schedule" | jq .
```

**运行输出示例**：
```json
{
  "schedule": {
    "type": "Custom",
    "cron": "0 3 * * *"
  },
  "parameters": {
    "delete_untagged": true,
    "dry_run": false,
    "workers": 3
  }
}
```

### 3.8 步骤七：配置跨地域复制规则（通过 API）

**业务背景**：主 Harbor 在上海，灾备 Harbor 在北京。所有打上 `release-*` 标签的镜像自动复制到灾备 Harbor。

```bash
#!/bin/bash
# step7-replication-rule.sh

HARBOR="https://harbor-sh.company.com"
AUTH="admin:Harbor12345"

# 第一步：创建远端 Registry 连接
echo "Creating remote registry endpoint..."
ENDPOINT_ID=$(curl -s -u "$AUTH" \
  -X POST \
  -H "Content-Type: application/json" \
  -d '{
    "name": "harbor-bj-dr",
    "url": "https://harbor-bj.company.com",
    "type": "harbor",
    "access_key": "admin",
    "access_secret": "Str0ng@DR2024",
    "insecure": false
  }' \
  "$HARBOR/api/v2.0/replication/endpoints" | jq -r '.id')

echo "Endpoint created (ID: $ENDPOINT_ID)"

# 第二步：创建复制规则（只复制 release-* 标签）
echo "Creating replication rule..."

ORDER_ID=$(curl -s -u "$AUTH" \
  "$HARBOR/api/v2.0/projects?name=order-platform" | jq -r '.[0].project_id')

curl -s -u "$AUTH" \
  -X POST \
  -H "Content-Type: application/json" \
  -d "{
    \"name\": \"order-platform-release-replication\",
    \"description\": \"Auto replicate release tags to Beijing DR\",
    \"src_registry\": {\"id\": 0},
    \"dest_registry\": {\"id\": $ENDPOINT_ID},
    \"dest_namespace\": \"order-platform\",
    \"trigger\": {
      \"type\": \"event_based\"
    },
    \"filters\": [
      {\"type\": \"name\", \"value\": \"**\"},
      {\"type\": \"tag\", \"value\": \"release-*\"}
    ],
    \"enabled\": true,
    \"override\": true,
    \"deletion\": false
  }" \
  "$HARBOR/api/v2.0/replication/policies" | jq '{id, name, enabled, trigger}'

echo ""
echo "Replication rule created!"
```

### 3.9 可能遇到的坑

**坑1：API 分页不处理导致数据不全**

Harbor 大部分列表类 API 默认 `page_size=10`。如果你直接用 `GET /projects` 不加分页参数——你只会看到前 10 个项目。项目数超过 10 个会丢失数据。

解决：
```bash
# ❌ 错误方式——只返回前 10 条
curl -u admin:pass https://harbor.company.com/api/v2.0/projects

# ✅ 正确方式——明确指定 page_size 并循环遍历
page=1
while true; do
  data=$(curl -s -u admin:pass \
    "https://harbor.company.com/api/v2.0/projects?page=$page&page_size=100")
  count=$(echo "$data" | jq '. | length')
  if [ "$count" -eq 0 ]; then break; fi
  echo "$data" | jq '.[].name'
  page=$((page + 1))
  if [ "$count" -lt 100 ]; then break; fi
done
```

**坑2：API 返回 405 Method Not Allowed**

根因通常是认证方式不匹配。某些 API（特别是 OIDC 相关）需要 Session Cookie 认证，但你用了 Basic Auth。

经验法则：
- 绝大多数**读操作（GET）**：Basic Auth、Session Cookie、Bearer Token 均可
- **写操作（POST/PUT/DELETE）**：推荐 Basic Auth 或 Robot Token
- **OIDC/用户自服务操作**：需要 Session Cookie

```bash
# 如果 Basic Auth 返回 405，尝试换 Session Cookie
# 第一步：获取 _sid
curl -c /tmp/harbor-cookie -X POST \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "principal=admin&password=Harbor12345" \
  https://harbor.company.com/c/login

# 第二步：使用 Cookie 调用 API
curl -b /tmp/harbor-cookie \
  https://harbor.company.com/api/v2.0/users/current
```

**坑3：API Version 错误——路径前缀不对**

Harbor v1.x 的 API 路径是 `/api/`，v2.x 是 `/api/v2.0/`。两者完全不兼容。如果你使用 v1 的路径格式去调 v2 的服务——返回 404。

解决：
```bash
# 先确认 Harbor 版本
VERSION=$(curl -s -u admin:pass \
  https://harbor.company.com/api/v2.0/systeminfo | jq -r '.harbor_version')
echo "Harbor version: $VERSION"

# v1.x: 使用 /api/
# v2.x: 使用 /api/v2.0/
# 可通过健康检查端点快速判断
curl -I https://harbor.company.com/api/v2.0/health 2>&1 | grep "HTTP/"
# HTTP/1.1 200 OK → API v2.0 可用
```

**坑4：`with_scan_overview=true` 返回空对象**

如果镜像刚推送，扫描可能还没开始或正在进行中。此时 `scan_overview` 为空对象 `{}`，而不是包含 `scan_status: "Pending"` 的结构。

```bash
# ❌ 错误判断：假设 scan_overview 一定有数据
scan_status=$(echo "$data" | jq -r '.scan_overview | to_entries[0].value.scan_status')
# 如果 scan_overview 是 {}，这里会报错或返回 null

# ✅ 正确方式：安全地处理空 scan_overview
scan_status=$(echo "$data" | jq -r \
  '(.scan_overview | to_entries[0].value.scan_status) // "Pending"')
# 使用 jq 的 // 运算符提供默认值
```

---

## 4 项目总结

### 4.1 Harbor API 认证方式深度对比

| 维度 | Basic Auth | Session Cookie | Bearer Token (JWT) | Robot Token |
|------|-----------|----------------|---------------------|-------------|
| 认证方式 | `-u user:pass` | `Cookie: _sid=...` | `Authorization: Bearer <jwt>` | `Authorization: Basic <robot:token>` |
| 有效期 | 永久（密码不失效） | 会话级（浏览器关闭即失效） | 默认 30 天（可配置） | 永久 / 可设过期日 |
| 获取方式 | 直接使用账号密码 | 登录 Portal 自动获取 | 调用 `/service/token` 签发 | 在 Portal 或 API 创建机器人 |
| 权限粒度 | 等同于该用户的全部权限 | 等同于该用户的全部权限 | 等同于该用户的全部权限 | 可精细控制（仅 push / 仅 pull / 仅读） |
| 泄露风险 | 极高——密码泄露 = 全权限被盗 | 中——会话过期自动失效 | 中——可设短期过期 | 低——最小权限 + 可单独吊销 |
| 适用场景 | 运维脚本、手动调试 | Portal 内嵌开发、临时调试 | CI/CD Pipeline、短期自动化 | 生产环境 CI/CD、长期自动化系统 |
| 是否可吊销 | 改密码即吊销 | 退出登录即吊销 | Token 过期后自动失效 | 删除机器人即吊销 |
| CI/CD 集成难度 | 低——一行 curl | 高——需模拟登录 | 中——需管理 Token 刷新 | 低——一次创建，永久使用 |

### 4.2 Harbor API 十大分类使用场景对照

| API 分类 | 最佳使用场景 | 不适合场景 | 典型调用频率 | 注意事项 |
|---------|-------------|-----------|------------|---------|
| 项目管理 | 批量创建/配置项目、自动化初始化 | 单个项目偶发操作（Portal 更快） | 低频（周级） | 项目名不可重名，需做幂等处理 |
| 仓库管理 | CI 查询镜像列表、自动化清理 | 镜像内容查看（用 Registry API） | 中频（日级） | 分页遍历，注意项目名/仓库名编码 |
| 制品管理 | 获取扫描报告、打 release 标签 | 大文件上传/下载 | 高频（每次 CI 构建） | 标签名不能含特殊字符 |
| 成员管理 | 批量授权、新员工入职自动配置 | 单成员偶发调整（Portal 更快） | 低频（月级） | 注意 role_id 映射关系 |
| 复制管理 | 配置跨地域同步、灾备策略 | 实时镜像同步（用事件驱动） | 低频（初始配置） | 需确保远端 Harbor 网络可达 |
| 扫描管理 | CI 安全门禁、定时扫描报告 | 镜像内容深度分析（用 Trivy CLI） | 高频（每次推送） | 轮询扫描状态，设超时兜底 |
| 审计日志 | 合规审计、安全事件溯源 | 实时操作监控（用 Webhook） | 低频（按需查询） | 默认保留 90 天，Pull 日志默认关闭 |
| 系统管理 | GC 调度、配置查询、健康检查 | 性能调优（直接改 harbor.yml） | 低频（日级） | GC 执行期间 Registry 只读 |
| 用户管理 | 自动化创建用户、批量重置密码 | 用户自服务（Portal 自带） | 低频（月级） | 创建用户后需要分配项目权限 |
| 统计信息 | 自定义 Dashboard、容量规划 | 精细化容量分析 | 低频（日级） | 数据有数分钟延迟 |

### 4.3 自动化实践最佳实践总结

| 实践 | 说明 | 反模式 |
|------|------|--------|
| 使用 Robot Token 替代 admin 密码 | 最小权限原则，Token 泄露影响可控 | 把 admin 密码硬编码在 CI 脚本中 |
| 所有 API 调用做重试逻辑 | 网络抖动/临时故障自动恢复 | `curl` 一次失败就退出脚本 |
| 分页遍历所有列表 API | 确保不遗漏数据 | 直接调用不加 page_size 参数 |
| 扫描状态轮询设置超时 | 防止因扫描器故障无限等待 | 无限循环等待扫描结果 |
| 脚本加幂等处理 | 重复执行不会出错 | 二次执行因"资源已存在"报错中断 |
| Token 存在 CI 变量（Masked）中 | 防止泄露到日志 | Token hardcode 在代码仓库 |

### 4.4 常见踩坑经验（故障排查表）

| 故障现象 | 根因分析 | 排查步骤 | 解决方案 |
|---------|---------|---------|---------|
| API 返回空数组但 Portal 有数据 | 忘记分页遍历 | 检查 `page_size` 参数，确认每页返回数 < 总数 | 加 `page` 和 `page_size` 参数循环遍历 |
| `with_scan_overview=true` 返回空 `{}` | 扫描尚未开始或扫描器故障 | 1) 在 Portal 确认扫描器状态 2) 检查 Trivy 容器日志 | 轮询等待 5~10 分钟后再次请求 |
| Swagger 页面浏览器卡死 | 8200+ 行 JSON 解析耗时 | Chrome DevTools 的 Network 标签查看响应大小 | 用 `curl .../swagger.json \| jq '.paths \| keys'` 在终端查看 |
| API 返回 403 Forbidden | 当前用户角色权限不足 | 1) 确认用户项目角色 2) 查看该 API 需要的最小角色 | 提升用户角色或使用有权限的账号 |
| `POST /projects` 返回 409 Conflict | 项目名已存在 | 确认项目名唯一性 | 脚本中先 `GET /projects?name=xxx` 检查是否存在 |
| `DELETE` 操作返回 200 但镜像仍在 | 仅删除了 tag 引用，Blob 未删除 | Harbor 是"软删除"——垃圾回收前 Blob 不释放 | 手动触发 GC 或等待定时 GC 执行 |

### 4.5 思考题

1. **编写一个 Harbor CLI 工具（如 Python 的 `click` 库实现），支持以下命令，替代 80% 的 Portal 日常操作：**
   - `harbor-cli project list` — 列出所有项目（含仓库数、配额使用率）
   - `harbor-cli project create <name> [--public] [--quota 200GB]` — 创建项目
   - `harbor-cli member add <project> <user> <role>` — 添加项目成员
   - `harbor-cli scan check <project>/<repo>:<tag>` — 获取扫描结果并输出门禁判断
   - `harbor-cli cleanup <project> --days 30 --exclude 'release-*'` — 清理过期标签
   要求：支持 `--config` 指定配置文件（含 Harbor URL + 认证信息），支持 `--output json/table` 两种输出格式。

2. **Harbor API 没有"批量删除仓库"的接口。安全部门要求删除 `ci-snapshots` 项目中 3 个月前推送的所有镜像（约 600 个标签）。请设计一个健壮的批量删除方案，要求：**
   - (1) 使用 API 分页 + 时间过滤找出所有目标标签
   - (2) 处理 Harbor API 的速率限制（假设限制为 100 次/分钟）——加入速率控制逻辑
   - (3) 处理中途网络中断的恢复——支持断点续删（记录已删除的标签）
   - (4) 删除完成后自动触发 GC
   - (5) 输出删除报告：总标签数、成功数、失败数、释放空间估算

---

> 下一章预告：第 14 章将深入 Harbor 的日志系统与审计追踪，教你从三层日志中获取可操作的运维洞察，构建实时告警体系。
