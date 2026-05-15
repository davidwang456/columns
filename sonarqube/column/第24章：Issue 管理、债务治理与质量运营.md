# 第24章：Issue 管理、债务治理与质量运营

## 1. 项目背景

**业务场景**：某公司在接入 SonarQube 6 个月后，Issue 总数从 1,200 个增长到了 4,800 个。不是因为代码质量变差了——而是因为团队在持续写新代码（每天 10+ 个合并），但历史 Issue 几乎没人修。质量负责人每周发出 Issue 周报，但开发者根本不看——"修了 5 个 Bug 结果又扫出 8 个新的，永远修不完，有什么意义？"

这就是典型的"工具疲劳"现象：扫描结果从"有价值的反馈"变成了"背景噪音"。团队需要从"被动的 Issue 积累"转为"主动的债务治理"——通过分级治理、定期复盘、可视化趋势，让 Issue 管理成为工程管理的一部分，而不是质量负责人一个人的焦虑。

**痛点放大**：

- **问题堆积不修**：没有明确的修复节奏和责任人，Issue 只是数字
- **修复优先级混乱**：不知道先修哪个，所有 Issue 一视同仁反而一个都不修
- **工具疲劳**：扫描结果很多但开发者不行动，工具变成了摆设
- **质量债不可见**：技术债务（Remediation Cost）是抽象概念，管理层无法理解

## 2. 项目设计

### 剧本式交锋对话

---

**小胖**（打开 SonarQube，Issue 列表 4,800 条，翻了三页就关了）："大师，4800 个 Issue，我能怎么办？躺平算了。"

**大师**："小胖，4800 个是总量。但你知道真正需要你立即修的有几个吗？我们来分级看一下。"

**小胖**："怎么分？"

**大师**："四层分类法：

| 层级 | 类别 | 筛选条件 | 预期数量 | 处理策略 |
|------|------|---------|---------|---------|
| L0 阻断 | New Code Blocker Bug+ Vuln | `new_bugs>0 AND new_vuln>0` | 通常 0-3 个 | **立即修复，阻塞合并** |
| L1 紧急 | Overall Blocker Bug | `severities=BLOCKER,types=BUG` | 通常 5-15 个 | **本周 Sprint 内修复** |
| L2 重要 | Overall Critical Bug+Vuln | `severities=CRITICAL,types=BUG,VULN` | 通常 20-50 个 | **本月内修复** |
| L3 常规 | 其余所有 Issue | 其他 | 通常数千个 | **逐步治理，不抢工期** |

按这个分类，你只需要关心 L0（0-3 个）+ L1（5-15 个）——大约 20 个 Issue。不是 4800 个。"

**小白**："那 remaining 4780 个 Issue怎么办？就不管了吗？"

**大师**："不是不管，是分阶段管。优先级策略：

- **L1 优先级 1.5**：核心模块的 Top 风险 Issue。比如你负责的支付模块有 3 个 Critical Bug，虽然不在 New Code 范围内，但因为是核心模块，也要优先修。
- **L2 优先级 2**：其他模块的 Bug 和 Vulnerability。每个月 Sprint 中预留 10-15% 的时间做债务治理。
- **L3 优先级 3**：Code Smell。在重构相关模块时附带着修——不要为修 Code Smell 专门开 Task。"

**小胖**："那技术债务（Technical Debt）是个什么概念？SonarQube 上那个 `17d` 是什么？"

**大师**："`17d` 的意思是：以一个人全职修复的速度，需要 17 个工作日才能修完所有 Issue。SonarQube 给每条 Issue 都分配了一个预估修复时间（Remediation Cost，基于规则复杂度）：

- Trivial（如重命名变量）：5 分钟
- Minor（如简化 if-else）：20 分钟
- Major（如减少复杂度）：1 小时
- Critical（如修复 SQL 注入）：2 小时
- Blocker（如修复除零）：4 小时

`17d` 就是所有 Issue * 各自修复时间的总和。这个数字本身不精确——但它是一个很好的 **沟通工具**。当你需要向管理层申请时间做债务治理时，'我们的技术债务是 17 个工作日'比'我们有很多 Issue'有说服力得多。"

---

## 3. 项目实战

### 3.1 环境准备

- SonarQube 实例，已有项目分析数据
- 用于数据聚合的脚本环境

### 3.2 分步实现

**步骤 1：构建 Issue 分级的 API 查询**

```bash
#!/bin/bash
# issue-triage.sh - Issue 分级统计

SONAR_URL="http://localhost:9000"
TOKEN="squ_xxx"
PROJECT="com.company:order-service"

echo "=== Issue 分级统计: $PROJECT ==="

# L0: New Code Blocker Bug + Vulnerability
echo -e "\n[L0 阻断] New Code Blocker Bug+Vuln:"
L0=$(curl -s -u "$TOKEN:" \
  "$SONAR_URL/api/issues/search?projectKeys=$PROJECT&severities=BLOCKER&types=BUG,VULNERABILITY&onComponentOnly=false&createdAfter=2024-01-01&ps=1" \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['total'])")
echo "  数量: $L0"

# L1: Overall Blocker Bug
echo -e "\n[L1 紧急] Overall Blocker Bug:"
L1=$(curl -s -u "$TOKEN:" \
  "$SONAR_URL/api/issues/search?projectKeys=$PROJECT&severities=BLOCKER&types=BUG&ps=1" \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['total'])")
echo "  数量: $L1"

# L2: Overall Critical Bug+Vulnerability
echo -e "\n[L2 重要] Overall Critical Bug+Vuln:"
L2=$(curl -s -u "$TOKEN:" \
  "$SONAR_URL/api/issues/search?projectKeys=$PROJECT&severities=CRITICAL&types=BUG,VULNERABILITY&ps=1" \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['total'])")
echo "  数量: $L2"

# 技术债务
echo -e "\n[技术债务]:"
DEBT=$(curl -s -u "$TOKEN:" \
  "$SONAR_URL/api/measures/component?component=$PROJECT&metricKeys=sqale_index,sqale_debt_ratio" \
  | python3 -c "
import sys,json
for m in json.load(sys.stdin)['component']['measures']:
    v = m.get('value','N/A')
    if m['metric'] == 'sqale_index': print(f'  债务时间: {v} 分钟')
    elif m['metric'] == 'sqale_debt_ratio': print(f'  债务比率: {v}%')")
```

**步骤 2：建立团队 Issue 修复节奏**

**每周 Sprint Plan 示例**：

```
Sprint #23 Issue 治理目标：

L0 (New Code 阻断):
  - 修复 PR #442 的除零 Bug → 张三 (done)
  - 修复 PR #445 的 SQL 注入 → 李四 (in progress)

L1 (紧急修复):
  - 修复 OrderService.java:155 的 NPE risk → 张三 (in progress)
  - 修复 PaymentGateway.java:89 的资源泄漏 → 王五 (pending)

L2 (重要修复 - 本月):
  - 修复 DiscountCalculator 关键路径的并发 Bug → 李四

L3 (常规治理 - 技术债 Timebox: 4h):
  - 每人分配 1 小时修复 Code Smell
```

**步骤 3：生成团队质量健康报告**

`quality-health-report.py`：

```python
#!/usr/bin/env python3
"""团队质量健康周报"""

import requests
import os

SONAR_URL = os.getenv("SONAR_URL", "http://localhost:9000")
TOKEN = os.getenv("SONAR_TOKEN", "")
PROJECTS = [
    "com.company:order-service",
    "com.company:payment-service",
    "com.company:user-service",
]

AUTH = (TOKEN, "") if TOKEN else ("admin", "Sonar@2024Admin")
HEADERS = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}

def get_metrics(project, metrics):
    resp = requests.get(
        f"{SONAR_URL}/api/measures/component",
        params={"component": project, "metricKeys": ",".join(metrics)},
        headers=HEADERS, auth=AUTH if not TOKEN else None
    )
    if resp.status_code != 200:
        return {}
    return {m["metric"]: m.get("value", "N/A")
            for m in resp.json().get("component", {}).get("measures", [])}

def get_new_issues(project):
    resp = requests.get(
        f"{SONAR_URL}/api/issues/search",
        params={"projectKeys": project, "statuses": "OPEN",
                "createdAfter": "2024-05-01", "ps": 1},
        headers=HEADERS, auth=AUTH if not TOKEN else None
    )
    return resp.json().get("total", 0)

# 生成报告
print("# 质量健康周报\n")
print(f"报告时间: {__import__('datetime').datetime.now().strftime('%Y-%m-%d')}\n")

for proj in PROJECTS:
    metrics = get_metrics(proj, [
        "bugs", "new_bugs", "vulnerabilities", "new_vulnerabilities",
        "coverage", "sqale_index", "sqale_debt_ratio"
    ])
    new_issues = get_new_issues(proj)

    # 健康度评分
    bugs = float(metrics.get("bugs", 0))
    new_bugs = float(metrics.get("new_bugs", 0))
    coverage = float(metrics.get("coverage", 0))
    debt_ratio = float(metrics.get("sqale_debt_ratio", 0))

    # 评分逻辑
    score = 100
    if coverage < 60: score -= 30
    elif coverage < 80: score -= 15
    if new_bugs > 0: score -= 20
    if debt_ratio > 10: score -= 10
    if bugs > 100: score -= 10

    status = "🟢" if score >= 80 else ("🟡" if score >= 60 else "🔴")
    print(f"## {status} {proj} (健康度: {score}/100)")
    print(f"- 总 Bug: {metrics.get('bugs','-')} (新增 {metrics.get('new_bugs','-')})")
    print(f"- 总漏洞: {metrics.get('vulnerabilities','-')} (新增 {metrics.get('new_vulnerabilities','-')})")
    print(f"- 覆盖率: {metrics.get('coverage','-')}%")
    print(f"- 技术债务: {metrics.get('sqale_index','-')} 分钟 (占比 {metrics.get('sqale_debt_ratio','-')}%)")
    print()
```

**步骤 4：Issue 修复日（Fix Day）**

每月一次，持续 2-4 小时，团队集中修复 Issue：

```bash
#!/bin/bash
# fixday.sh - 修复日前检查

echo "=== Issue Fix Day 准备 ==="
date

# 生成本次修复目标（Top 10 最易修复的 Issue）
TARGET_ISSUES=$(curl -s -u "$TOKEN:" \
  "$SONAR_URL/api/issues/search?projectKeys=com.company:order-service&statuses=OPEN&types=CODE_SMELL&s=CREATION_DATE&asc=false&ps=10" \
  | python3 -c "
import sys, json
for i in json.load(sys.stdin)['issues']:
    print(f\"  - {i['rule']} | {i['message'][:80]} | {i.get('debt','?')}min\")
")

echo "本次修复目标："
echo "$TARGET_ISSUES"

# 记录修复前的基线
echo ""
echo "修复前基线："
curl -s -u "$TOKEN:" \
  "$SONAR_URL/api/measures/component?component=com.company:order-service&metricKeys=bugs,code_smells,sqale_index"
```

### 3.3 验证

```bash
# 修复日前后对比
echo "修复前 Bug 数:" && curl -s -u admin:Sonar@2024Admin \
  "http://localhost:9000/api/measures/component?component=com.company:order-service&metricKeys=bugs" \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['component']['measures'][0]['value'])"

# ... 修复代码并重新扫描 ...

echo "修复后 Bug 数:" && curl -s -u admin:Sonar@2024Admin \
  "http://localhost:9000/api/measures/component?component=com.company:order-service&metricKeys=bugs" \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['component']['measures'][0]['value'])"
```

---

## 4. 项目总结

### 4.1 Issue 治理策略框架

| 层级 | 目标 | 频率 | 指标 |
|------|------|------|------|
| New Code 门禁 | 新增代码零阻断 | 每次 PR | New Blocker Bug = 0 |
| 每日清零 | 当日新增 Issue 当日关 | 每日 | New Issues = 0 |
| 每周治理 | 减少 High Priority 历史债 | 每周 | Blocker + Critical 减少 ≥ 5 |
| 每月复盘 | 趋势分析和策略调整 | 每月 | Debt Ratio 下降 ≥ 1% |

### 4.2 注意事项

1. **不要把 Issue 修复和绩效强绑定**：一旦和绩效挂钩，"标记误报"就会变成"刷数据"的工具。
2. **技术债务时间（sqale_index）是估算，不是精确值**：它更适合做趋势对比（本月 vs 上月），而不是绝对的考核标准。
3. **定期清理 Won't Fix 和 False Positive**：大量历史关闭 Issue 会干扰统计数据，建议每季度做一次清理。

### 4.3 思考题

1. 如果你的团队每月新增 30 个 Issue，但只修复 20 个历史上的 Issue（净增 10 个/月），你认为这个策略是成功的还是失败的？
2. "Fix Day"（集中修复日）和"Continuous Fixing"（持续修复），哪种方式更适合你的团队？为什么？

> **答案提示**：第1题看修复的是哪类的 Issue——如果修复的是 Blocker/Critical 级别，即使数字净增也可以接受（优先灭大问题）。第2题连续修复更适合成熟团队，集中修复日更适合刚接入 SonarQube 的团队（建立习惯）。

---

> **推广计划提示**：质量周报是推动 Issue 修复的最有效工具——不是发给每个人，而是发给每个团队的 Tech Leader。建议周报包含四个核心板块：New Code 的问题、本周修复量、团队排名、重点关注 Issue。保持简洁——1 页 A4 纸足矣。
