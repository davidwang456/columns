# 第25章：GitLab API 编程实战

## 1. 项目背景

> **业务场景**：某公司的 DevOps 团队需要管理 200+ 个项目、500+ 用户和 30 个 Group。手动在 GitLab UI 上挨个操作简直是噩梦——创建一个新员工的账号要手动添加到 15 个项目中，新建一个微服务要创建项目、配置 CI 变量、设置分支保护、添加成员，全套操作需要 20 分钟。

更糟的是：运维团队需要每周生成一份"项目健康度报告"——统计每个项目的最近 commit 时间、Pipeline 成功率、Open MR 数量、安全漏洞数量。手动收集这些数据需要花半天时间。如果能把 GitLab 当成"可编程平台"，通过 API 自动化这些操作，效率能提升 10 倍以上。

**痛点放大**：GitLab 提供了极其丰富的 REST API 和 GraphQL API，覆盖项目管理、CI/CD、用户管理、仓库操作等几乎所有功能。学会 GitLab API 编程，你就有了一个"超级管理员控制台"——可以用脚本批量操作、用定时任务自动审计、用 Webhook 打通外部系统。

## 2. 项目设计——剧本式交锋对话

**场景**：运维自动化讨论会，大家讨论如何减少手工操作。

---

**小胖**："我们能不能写个脚本，自动给新人创建账号、加到正确的 Group 里，一次搞定？"

**大师**："GitLab API 就能做这件事。你只需要申请一个 Personal Access Token（或者用 Project Access Token 做自动化），然后调 API 就行了。比如 `POST /api/v4/users` 创建用户，`POST /api/v4/groups/:id/members` 添加用户到 Group。"

**小胖**："那我不如直接用 glab CLI 命令行工具？api 调用还得写代码。"

**大师**："glab 适合临时操作，但脚本化和定时自动化还是得用 API。Python 有 `python-gitlab` 库，Go 有 `go-gitlab`，封装得很好——用户管理用 `gl.users.create()`，项目管理用 `gl.projects.create()`。你可以把自动化脚本放到 GitLab CI 的定时任务中，这样完全不需要人为介入。"

**小白**："GraphQL 和 REST API 有什么区别？GitLab 支持两个。"

**大师**："REST API 是传统的资源导向接口——每个端点返回固定结构的数据。GraphQL 由客户端指定要哪些字段——一次请求可以跨多个资源，减少 N+1 问题。比如你想知道'某个项目的所有 MR 及其审批人信息'，REST 需要多次请求，GraphQL 一次就够。技术映射——REST 就像去餐厅点套餐（固定搭配），GraphQL 就像自助餐（想要什么拿什么）。"

**小胖**："那 Webhook 和 API 有什么区别？"

**大师**："API 是你主动请求 GitLab（pull 模式），Webhook 是 GitLab 在事件发生时通知你（push 模式）。比如你想在 MR 合并时自动更新 Jira 状态——用 Webhook 最合适：GitLab 在合并时自动 POST 到你的接收器。而你想定时拉取所有项目的健康度数据——用 API 最合适。"

---

## 3. 项目实战

### 环境准备

> **目标**：学会使用 GitLab API（REST + GraphQL）、python-gitlab SDK 和 glab CLI，实现批量操作和自动化脚本。

**前置条件**：GitLab CE 17.x，Personal Access Token（`api` + `read_repository` 权限）。

### 分步实现

#### 步骤1：API 认证与基础操作

**目标**：掌握 4 种认证方式和基础 CRUD 操作。

**方式一：Personal Access Token**：

```bash
# 创建 Token：Settings → Access Tokens → 勾选 api 权限
export GITLAB_URL="http://gitlab.local:8929"
export GITLAB_TOKEN="glpat-xxxx"

# 获取当前用户信息
curl --header "PRIVATE-TOKEN: $GITLAB_TOKEN" \
  "$GITLAB_URL/api/v4/user" | python3 -m json.tool

# 列出所有项目
curl --header "PRIVATE-TOKEN: $GITLAB_TOKEN" \
  "$GITLAB_URL/api/v4/projects?per_page=100&owned=true" | \
  python3 -c "
import json, sys
projects = json.load(sys.stdin)
for p in projects:
    print(f'{p[\"id\"]:>5} {p[\"path_with_namespace\"]}')
"
```

**方式二：CI Job Token**（仅在 CI 环境中可用）：

```yaml
# 在 .gitlab-ci.yml 中使用
api-call:
  script:
    - |
      curl --header "JOB-TOKEN: $CI_JOB_TOKEN" \
        "$CI_API_V4_URL/projects/$CI_PROJECT_ID"
```

**方式三：OAuth 2.0**：

```bash
# 适用于第三方应用集成
# 1. 在 GitLab 创建 OAuth Application
# Admin → Applications → New Application
# Redirect URI: http://localhost:8080/callback
# Scopes: api

# 2. 获取授权码
open "http://gitlab.local:8929/oauth/authorize?client_id=APP_ID&redirect_uri=http://localhost:8080/callback&response_type=code"

# 3. 用授权码换 token
curl -X POST "$GITLAB_URL/oauth/token" \
  -d "client_id=APP_ID&client_secret=APP_SECRET&code=AUTH_CODE&grant_type=authorization_code&redirect_uri=http://localhost:8080/callback"
```

**方式四：Project/Group Access Token**（推荐用于自动化）：

```bash
# 比 Personal Access Token 更安全（不绑定个人账号）
# Project → Settings → Access Tokens → Add project access token
# 选择角色和权限范围

curl --header "PRIVATE-TOKEN: $PROJECT_TOKEN" \
  "$GITLAB_URL/api/v4/projects/$PROJECT_ID"
```

#### 步骤2：python-gitlab SDK 实战——批量操作

**目标**：用 python-gitlab 库编写自动化脚本，批量管理项目和用户。

```bash
# 安装
pip install python-gitlab
```

**脚本1：批量创建用户并添加到 Group**：

```python
#!/usr/bin/env python3
"""onboarding.py - 批量创建新员工账号并分配权限"""
import gitlab
import csv
import sys

# 初始化 GitLab 客户端
gl = gitlab.Gitlab(
    url='http://gitlab.local:8929',
    private_token='glpat-xxxx'
)

def onboard_users(csv_file):
    """从 CSV 文件读取用户信息并批量创建"""
    with open(csv_file, 'r') as f:
        reader = csv.DictReader(f)

        for row in reader:
            try:
                # 创建用户（跳过邮箱确认）
                user = gl.users.create({
                    'email': row['email'],
                    'username': row['username'],
                    'name': row['name'],
                    'password': row['password'],
                    'skip_confirmation': True
                })
                print(f"✅ Created user: {user.username}")

                # 添加到指定 Group
                group = gl.groups.get(row['group_id'])
                member = group.members.create({
                    'user_id': user.id,
                    'access_level': int(row['access_level'])
                    # 10=Guest, 20=Reporter, 30=Developer, 40=Maintainer, 50=Owner
                })
                print(f"  → Added to {group.name} as level {row['access_level']}")

            except gitlab.exceptions.GitlabCreateError as e:
                print(f"❌ Failed to create {row['username']}: {e}")

if __name__ == '__main__':
    onboard_users('new_employees.csv')
```

**示例 CSV 文件**：

```csv
email,username,name,password,group_id,access_level
alice@company.com,alice,Alice Wang,Secure123!,42,30
bob@company.com,bob,Bob Li,Secure456!,42,30
```

**脚本2：项目健康度报告**：

```python
#!/usr/bin/env python3
"""health_check.py - 生成项目健康度周报"""
import gitlab
from datetime import datetime, timedelta

gl = gitlab.Gitlab('http://gitlab.local:8929', private_token='glpat-xxxx')

def project_health_report(group_id):
    """为指定 Group 下的所有项目生成健康度报告"""
    group = gl.groups.get(group_id)
    projects = group.projects.list(all=True, include_subgroups=True)

    report = []
    cutoff = datetime.now() - timedelta(days=30)  # 30天未活动 = 不健康

    for project in projects:
        p = gl.projects.get(project.id)

        # 获取最近 commit 时间
        try:
            commits = p.commits.list(per_page=1)
            last_commit = commits[0].committed_date if commits else None
        except:
            last_commit = None

        # 获取 Pipeline 成功率
        pipelines = p.pipelines.list(per_page=20, status='success')
        total_pipelines = p.pipelines.list(per_page=20)
        success_rate = len(pipelines) / max(len(total_pipelines), 1) * 100

        # 获取 Open MR 数量
        open_mrs = p.mergerequests.list(state='opened', per_page=100)

        # 健康度评分
        health_score = 100
        if not last_commit or datetime.fromisoformat(last_commit) < cutoff:
            health_score -= 40  # 30天无活动
        if success_rate < 80:
            health_score -= 20
        if len(open_mrs) > 10:
            health_score -= 10  # MR 积压

        report.append({
            'name': p.path_with_namespace,
            'last_commit': last_commit,
            'pipeline_success_rate': f"{success_rate:.1f}%",
            'open_mrs': len(open_mrs),
            'health_score': health_score
        })

    # 按健康度排序输出
    report.sort(key=lambda x: x['health_score'])

    print(f"{'项目':<40} {'最近提交':<20} {'CI成功率':<12} {'Open MR':<10} {'健康度':<8}")
    print("-" * 95)
    for r in report:
        health_icon = '🟢' if r['health_score'] >= 80 else '🟡' if r['health_score'] >= 60 else '🔴'
        last_commit_str = r['last_commit'][:10] if r['last_commit'] else 'N/A'
        print(f"{r['name']:<40} {last_commit_str:<20} {r['pipeline_success_rate']:<12} {r['open_mrs']:<10} {health_icon} {r['health_score']}")

if __name__ == '__main__':
    project_health_report(group_id=42)
```

#### 步骤3：GraphQL API 实战

**目标**：用 GraphQL 一次性查询关联数据，避免 N+1 请求。

```bash
# GraphQL 端点：/api/graphql

# 查询：获取项目及其 MR 列表（含审批人信息）
curl --request POST \
  --header "PRIVATE-TOKEN: $GITLAB_TOKEN" \
  --header "Content-Type: application/json" \
  --data '{
    "query": "{
      project(fullPath: \"acme-corp/ecommerce/shop-api\") {
        name
        mergeRequests(state: opened, first: 10) {
          nodes {
            iid
            title
            webUrl
            approved
            approvedBy {
              nodes {
                name
              }
            }
          }
        }
      }
    }"
  }' \
  "$GITLAB_URL/api/graphql"
```

#### 步骤4：glab CLI 实战

**目标**：用 glab 命令行高效操作 GitLab。

```bash
# 安装
# macOS: brew install glab
# Linux: https://gitlab.com/gitlab-org/cli/-/releases
# Windows: winget install glab

# 认证
glab auth login --hostname gitlab.local:8929

# 常用命令
glab repo view                    # 查看当前仓库信息
glab mr list                      # 列出 MR
glab mr create --title "feat: xxx" --assignee @me  # 创建 MR
glab mr merge 42                  # 合并 MR #42
glab issue list --label bug       # 列出 bug issue
glab ci status                    # 查看 CI 状态
glab ci trace                     # 查看 CI 日志
glab api projects/:id             # 调用 API（自动认证）

# 在 CI 中使用 glab
# .gitlab-ci.yml
use-glab:
  image: registry.gitlab.com/gitlab-org/cli:latest
  script:
    - glab auth login --hostname gitlab.local --job-token $CI_JOB_TOKEN
    - glab mr create --title "Auto MR" --description "Generated by CI"

# 批量操作
glab mr list --per-page 100 --state opened \
  --output json | jq '.[] | {iid: .iid, title: .title, author: .author.username}'
```

### 完整代码清单

- `onboarding.py`：批量用户创建脚本
- `health_check.py`：项目健康度报告脚本
- GraphQL 查询示例
- glab CI 集成示例

### 测试验证

```bash
# 验证1：API 基础操作
curl --header "PRIVATE-TOKEN: $GITLAB_TOKEN" "$GITLAB_URL/api/v4/version"
# 应返回版本信息

# 验证2：python-gitlab 脚本
python3 health_check.py
# 应输出项目健康度表格

# 验证3：GraphQL 查询
# 执行 GraphQL 查询 → 确认返回了 MR 及其审批人信息

# 验证4：glab 批量操作
glab api "projects?per_page=5" | jq '.[].path'
# 确认列出 5 个项目
```

## 4. 项目总结

### API 使用方式对比

| 方式 | 适用场景 | 优点 | 缺点 |
|------|---------|------|------|
| REST API | 资源操作 | 简单直观 | 多次请求才能关联数据 |
| GraphQL API | 复杂查询 | 一次获取关联数据 | 学习曲线稍高 |
| python-gitlab | Python 脚本 | 高级封装 | 依赖第三方库 |
| glab CLI | 命令行操作 | 零代码操作 | 不适合复杂逻辑 |

### 适用场景

- **批量管理**：创建用户、设置 CI 变量、调整项目权限
- **监控审计**：项目健康度、Pipeline 成功率、MR 积压统计
- **集成开发**：Webhook 接收器、ChatOps Bot、自动 Issue 标签
- **数据迁移**：从 GitHub/Bitbucket 批量导入项目

**不适用场景**：
- 一次性操作（直接 UI 操作更快）
- 需要事务保证的操作（API 不提供事务支持）

### 注意事项

- **Token 权限最小化**：不要给自动化脚本 `api` 全部权限，按需使用 Project Access Token
- **API 限流**：GitLab CE 默认限流 600 requests/min，EE 可配置
- **Pagination**：API 列表默认每页 20 条，务必处理 `page` 和 `per_page` 参数
- **Token 过期**：Personal Access Token 默认不过期，但建议定期轮换

### 常见踩坑经验

1. **API 返回 404 但项目确实存在**：Token 没有访问该项目的权限。根因：Token 的权限不匹配项目可见性。解决：确保 Token 所属用户至少是项目 Reporter。
2. **python-gitlab 创建用户报错 "Email has already been taken"**：邮箱已被占用但用户名不同。根因：GitLab 要求邮箱全局唯一。解决：先用 `gl.users.list(search=email)` 检查是否已存在。
3. **GraphQL 查询返回空数据**：字段名大小写错误。根因：GraphQL 字段名区分大小写。解决：使用 GraphQL Explorer (GitLab UI: `/-/graphql-explorer`) 验证查询。

### 思考题

1. 你有一个 500 人的组织，需要每周自动审计"所有非活跃用户（90 天未登录）并发送提醒邮件"。请设计这个自动化方案的架构，包括定时任务、API 调用和邮件发送。
2. GitLab API 的事件流（Events API）可以获取项目级别的操作审计日志。如何利用这个 API 构建一个"安全事件监控"系统——当检测到异常操作（如大量项目被删除）时自动告警？

> 答案见附录 D。

### 推广计划提示

- **运维**：用 python-gitlab 写日常运维脚本，告别手动点击 GitLab UI
- **开发**：glab CLI 可以显著提升 Code Review 和 MR 管理效率
- **安全**：通过 API 定期审计用户权限和项目设置，预防配置漂移
