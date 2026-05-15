# 第14章：Web API 初体验：用脚本读取质量数据

## 1. 项目背景

**业务场景**：某互联网公司的质量团队每周需要手动登录 SonarQube，为 25 个项目分别截图质量仪表盘，然后在 Excel 中汇总成"质量周报"。这个过程每次消耗质量工程师 3 个小时，而且数据容易错漏——上周某项目的覆盖率 78% 被误写成了 87%，导致团队 Leader 错误地认为覆盖率达标了。

另一个场景：CI 流水线中，Jenkins 只检查了 Quality Gate 的通过/失败，但没有记录详细的数值变化——比如"这个 PR 让覆盖率下降了 2.3%，新增了 1 个 Blocker Bug"。当出现线上问题时，没有历史数据可以回溯。

这些痛点的共同根源是：团队依赖人工从 Web UI 获取数据，而不是通过 API 程序化地提取、聚合和分析 SonarQube 中的质量数据。

**痛点放大**：

- **手工操作低效**：25 个项目 × 5 个指标 = 125 次点选复制，每周浪费数小时
- **数据可信度低**：人为转录错误导致决策基于错误的数据
- **集成断裂**：SonarQube 是信息孤岛，企业微信/钉钉/飞书、Jira、Confluence 等协作工具无法直接获取质量数据
- **历史趋势不可见**：Web UI 只能看到最近 5 次扫描的趋势，更长的历史需要 API 导出

## 2. 项目设计

### 剧本式交锋对话

---

**小胖**（盯着 Excel 表格，一个数字一个数字地核对 SonarQube 页面）："大师，我每周围着这 25 个项目抄数据，抄完还要画图表。能不能让程序自己干这个？"

**大师**："SonarQube 有完整的 Web API——所有你在 Web UI 上能看到的数据，都可以通过 API 拿到。你在页面上看到的指标数字、Issue 列表、Quality Gate 状态，底层都是 REST API 查询的结果。你直接调用 API，数据就自己出来了。"

**小白**（打开浏览器，访问 `http://localhost:9000/web_api`）："这个 API 文档真全——光 `/api/issues` 下面就有一堆接口：search、show、add_comment、assign、bulk_change……但哪些是日常最常用的？"

**大师**："五大核心 API 你记住就够了：

1. **`/api/measures/component`**：查询项目的指标数据（覆盖率、Bug 数、代码行数等）
2. **`/api/issues/search`**：查询 Issue 列表，支持按类型、严重级别、状态、标签过滤
3. **`/api/qualitygates/project_status`**：查询项目的 Quality Gate 状态
4. **`/api/projects/search`**：搜索项目列表
5. **`/api/ce/activity`**：查询 Compute Engine 任务状态

掌握这 5 个，你就能覆盖 90% 的日常自动化需求。"

**小胖**："那认证呢？我不想在脚本里写明文密码。"

**大师**："两种认证方式：

1. **Basic Auth**：`curl -u admin:password`，但密码在命令行历史中会暴露
2. **Bearer Token**（推荐）：`curl -H 'Authorization: Bearer squ_xxxx'`，Token 可以随时吊销，泄露后影响范围可控

脚本中通过环境变量注入 Token：

```bash
export SONAR_TOKEN=squ_xxxxxxxxxxxx
curl -H "Authorization: Bearer $SONAR_TOKEN" \
  "http://localhost:9000/api/projects/search"
```

不要将 Token 硬编码在脚本中，不要将 Token 提交到 Git。"

**小白**："API 分页怎么处理？有些项目有 500+ 个 Issue，一次查询只能返回 100 条。"

**大师**："分页参数是 `ps`（pageSize，最大 500）和 `p`（page，从 1 开始）。如果 Issue 数量超过 500，需要循环翻页。更好的做法是使用 `facets` 参数做聚合统计——比如统计每个严重级别的 Issue 数量——这样可以避免拉取全量数据。"

**小胖**："我能用 API 做点什么实际有用的？"

**大师**："三个最常见的自动化场景：

1. **质量周报自动生成**：脚本遍历所有项目，取关键指标，生成 Markdown 或 HTML 报告。
2. **CI 后质量通知**：扫描完成后通过 API 查询 Gate 状态和指标变化，发送到企业微信/钉钉。
3. **质量数据大盘**：定时采集数据存入时序数据库（如 InfluxDB），用 Grafana 展示长期趋势。"

---

## 3. 项目实战

### 3.1 环境准备

- SonarQube 实例，已有项目完成扫描
- 生成一个 Token（My Account → Security → Generate Token）
- Python 3 或 curl 可用

### 3.2 分步实现

**步骤 1：验证 API 连接**

```bash
# 测试认证和基本连接
curl -u admin:Sonar@2024Admin \
  "http://localhost:9000/api/system/health"
```

预期输出：
```json
{"health": "GREEN", "causes": [], "nodes": [{"health": "GREEN", ...}]}
```

**步骤 2：查询项目基本指标**

```bash
curl -u admin:Sonar@2024Admin \
  "http://localhost:9000/api/measures/component?component=com.example:order-service&metricKeys=bugs,vulnerabilities,code_smells,coverage,ncloc,duplicated_lines_density" \
  | python3 -m json.tool
```

输出解读：

| metric | 含义 | 示例值 |
|--------|------|--------|
| bugs | Bug 数量 | 5 |
| vulnerabilities | 安全漏洞数量 | 2 |
| code_smells | 代码异味数量 | 34 |
| coverage | 行覆盖率（%） | 78.5 |
| ncloc | 非注释代码行数 | 12540 |
| duplicated_lines_density | 重复率（%） | 4.2 |

**步骤 3：查询 Quality Gate 状态**

```bash
curl -u admin:Sonar@2024Admin \
  "http://localhost:9000/api/qualitygates/project_status?projectKey=com.example:order-service"
```

输出：
```json
{
  "projectStatus": {
    "status": "ERROR",
    "conditions": [
      {
        "status": "ERROR",
        "metricKey": "new_coverage",
        "comparator": "LT",
        "errorThreshold": "80.0",
        "actualValue": "72.5"
      },
      {
        "status": "OK",
        "metricKey": "new_bugs",
        "comparator": "GT",
        "errorThreshold": "0",
        "actualValue": "0"
      }
    ]
  }
}
```

关键字段：
- `status`: OK（通过）/ ERROR（失败）/ WARN（警告）
- `conditions[].status`: 每个条件的独立状态
- `actualValue` vs `errorThreshold`: 实际值 vs 阈值

**步骤 4：搜索 Issue**

```bash
# 查询项目所有 OPEN 状态的 Blocker Issue
curl -u admin:Sonar@2024Admin \
  "http://localhost:9000/api/issues/search?projectKeys=com.example:order-service&severities=BLOCKER&statuses=OPEN&ps=10" \
  | python3 -m json.tool
```

查询参数说明：

| 参数 | 说明 | 示例值 |
|------|------|--------|
| projectKeys | 项目 Key（逗号分隔多个） | com.example:order-service |
| severities | 严重级别筛选 | BLOCKER,CRITICAL,MAJOR |
| types | 类型筛选 | BUG,VULNERABILITY,CODE_SMELL |
| statuses | 状态筛选 | OPEN,CONFIRMED,REOPENED |
| tags | 标签筛选 | security,cwe |
| createdAfter | 创建时间筛选 | 2024-01-01 |
| p / ps | 分页 | p=1, ps=100 |
| facets | 聚合统计 | severities,types,resolutions |

**步骤 5：编写 Python 质量数据采集脚本**

`collect_quality_data.py`：

```python
#!/usr/bin/env python3
"""SonarQube 质量数据采集脚本"""
import requests
import json
import os
from datetime import datetime

SONAR_URL = os.getenv("SONAR_URL", "http://localhost:9000")
SONAR_TOKEN = os.getenv("SONAR_TOKEN", "")

HEADERS = {"Authorization": f"Bearer {SONAR_TOKEN}"}
AUTH = ("admin", "Sonar@2024Admin") if not SONAR_TOKEN else None

# 关注的指标
METRICS = [
    "bugs", "vulnerabilities", "code_smells",
    "coverage", "ncloc", "duplicated_lines_density",
    "sqale_rating", "reliability_rating", "security_rating"
]

def get_all_projects():
    """获取所有项目列表"""
    resp = requests.get(f"{SONAR_URL}/api/projects/search",
                        params={"ps": 500},
                        headers=HEADERS, auth=AUTH)
    return resp.json().get("components", [])

def get_project_measures(project_key):
    """获取单个项目的指标"""
    resp = requests.get(
        f"{SONAR_URL}/api/measures/component",
        params={
            "component": project_key,
            "metricKeys": ",".join(METRICS)
        },
        headers=HEADERS, auth=AUTH
    )
    measures = {}
    for m in resp.json().get("component", {}).get("measures", []):
        measures[m["metric"]] = m.get("value", "N/A")
    return measures

def get_quality_gate(project_key):
    """获取 Quality Gate 状态"""
    resp = requests.get(
        f"{SONAR_URL}/api/qualitygates/project_status",
        params={"projectKey": project_key},
        headers=HEADERS, auth=AUTH
    )
    return resp.json().get("projectStatus", {})

def generate_report(projects):
    """生成质量周报"""
    report_lines = [
        f"# 代码质量周报 ({datetime.now().strftime('%Y-%m-%d')})",
        "",
        "| 项目 | Bugs | Vuln | Smells | 覆盖率 | 代码行 | 重复率 | Gate |",
        "|------|------|------|--------|--------|--------|--------|------|"
    ]

    for proj in projects:
        key = proj["key"]
        name = proj["name"]
        measures = get_project_measures(key)
        gate = get_quality_gate(key)
        gate_status = gate.get("status", "?")
        report_lines.append(
            f"| {name} | {measures.get('bugs','-')} | "
            f"{measures.get('vulnerabilities','-')} | "
            f"{measures.get('code_smells','-')} | "
            f"{measures.get('coverage','-')}% | "
            f"{measures.get('ncloc','-')} | "
            f"{measures.get('duplicated_lines_density','-')}% | "
            f"{gate_status} |"
        )

    return "\n".join(report_lines)

if __name__ == "__main__":
    projects = get_all_projects()
    print(f"采集 {len(projects)} 个项目的数据...")
    report = generate_report(projects)
    print(report)

    # 保存到文件
    report_file = f"quality-report-{datetime.now().strftime('%Y%m%d')}.md"
    with open(report_file, "w") as f:
        f.write(report)
    print(f"\n✅ 报告已保存到 {report_file}")
```

**步骤 6：执行数据采集**

```bash
export SONAR_URL=http://localhost:9000
export SONAR_TOKEN=squ_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

python3 collect_quality_data.py
```

### 3.3 高级 API 使用：查询历史趋势

```bash
# 查询某个指标的历史值（timeseries）
curl -u admin:Sonar@2024Admin \
  "http://localhost:9000/api/measures/search_history?component=com.example:order-service&metrics=coverage,bugs&ps=10" \
  | python3 -m json.tool
```

### 3.4 验证

```bash
# 验证 API 分页
curl -u admin:Sonar@2024Admin \
  "http://localhost:9000/api/issues/search?projectKeys=com.example:order-service&ps=500&p=1" \
  | python3 -c "
import sys, json
data = json.load(sys.stdin)
total = data.get('total', 0)
paging = data.get('paging', {})
print(f\"总Issue数: {total}\")
print(f\"总页数: {(total + 499) // 500 if total else 0}\")
print(f\"当前页: {paging.get('pageIndex', 1)} / {paging.get('total', 1)}\")
"
```

---

## 4. 项目总结

### 4.1 API 使用最佳实践

| 实践 | ✅ 推荐 | ❌ 避免 |
|------|---------|---------|
| 认证 | Bearer Token + 环境变量 | 硬编码密码 + URL 参数 |
| 频率控制 | 每分钟不超过 10 次调用 | 毫秒级轮询，影响服务性能 |
| 数据量 | 使用 facets 做聚合统计 | 拉取全量 Issue 列表 |
| 错误处理 | 检查 HTTP 状态码和响应结构 | 假设请求永远成功 |
| Token 管理 | 定期轮换，最小权限 | 一个 Token 用到底 |

### 4.2 适用场景

- **质量周报/月报自动化**：采集指标生成报告
- **CI/CD 通知**：扫描完成后获取 Gate 状态和变化摘要，发送到 IM
- **质量大盘/BI**：定时采集数据，导入 BI 工具
- **项目健康度巡检**：定期检查所有项目的 Gate 状态和 Issue 趋势

### 4.3 注意事项

1. **API 速率限制**：SonarQube 默认没有硬限流，但高频调用会影响 CE 和数据库性能。建议脚本间隔 ≥ 1 秒。
2. **Token 权限**：只读查询用 "Browse" 权限的 Token，需要修改 Issue 的才用更高权限。
3. **指标名称**：`metricKeys` 的值必须和 Web API 文档中一致。常用指标名：`bugs`, `vulnerabilities`, `code_smells`, `coverage`, `new_coverage`, `ncloc`, `sqale_index`（技术债务），`sqale_debt_ratio`。

### 4.4 常见踩坑经验

**故障 1：API 返回空数据，但 Web UI 上能看到**

根因：Token 权限不足。Token 所属用户没有项目的浏览权限。解决：在 Administration → Projects → Management 中给用户授权。

**故障 2：`measures/component` 返回的指标值为空**

根因：项目还没有完成首次分析（CE 还在处理），或者指定的 `metricKey` 不存在。用 `metricKeys=bugs,coverage` 先验证基本指标。

### 4.5 思考题

1. 如何通过 API 获取一个项目在过去 6 个月内的 Bug 数量趋势？API 一次返回多少条历史数据点？
2. 如果你想为 100 个项目生成质量周报，如何优化 API 调用次数（从 100 次降低到 1 次）？

> **答案提示**：第1题使用 `measures/search_history` 接口。第2题：`measures/component` 不支持批量查询，但可通过自定义 Dashboard 指标聚合或采集 ES 数据实现一次查询。

---

> **推广计划提示**：API 自动化是推广 SonarQube 的"催化剂"——当团队看到机器人自动推送质量周报到群聊时，对 SonarQube 的使用意愿会显著提升。质量负责人应优先建设"质量通知机器人"（扫描完成 → API 查询 → 群聊通知），让质量数据主动触达团队。
