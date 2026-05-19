# 第23章：高级过滤、VEX 应用与报告定制

> 版本：Trivy v0.50+
> 面向人群：安全工程师、DevOps、合规专员

---

## 1. 项目背景

### 业务场景

云帆科技的漏洞扫描体系已经相当完善：Trivy 覆盖了 CI/CD、Harbor、K8s Operator，自定义 Rego 策略也落地了企业安全基线。但安全负责人老李最近被一个「老问题」折腾得焦头烂额——误报管理。

事情是这样的：公司的支付网关镜像基于 Debian 11，Trivy 扫描稳定报出 15 个 HIGH 级别的 OpenSSL 相关 CVE。开发团队每次发版前都要在评审会上解释：「这些漏洞 Debian 官方已经通过 backport 修复了，只是版本号带了 `+deb11u5` 后缀，Trivy 的数据库识别为未修复。」

这种解释重复了三个月后，双方都疲惫了。开发开始质疑扫描的价值：「反正都是误报，扫它干嘛？」安全团队则担心：「万一哪天真报了一个没评估过的漏洞，开发也会当成误报忽略掉。」

更棘手的是客户审计。某金融客户要求云帆科技提供「漏洞豁免清单」，说明每个未修复漏洞的理由、影响评估和缓解措施。老李把 `.trivyignore` 文件发过去，客户直接退回了：「这是什么？只有 CVE 编号，没有标准格式，没有机器可读性，无法导入我们的漏洞管理平台。」

与此同时，公司的报告需求也越来越复杂。管理层想要「一页纸摘要」，技术团队想要「按 Namespace 聚合的详细清单」，合规团队想要「符合 SARIF 标准的结构化报告」，而客户想要「包含 SBOM 和 VEX 的完整安全包」。Trivy 的默认 table 输出显然无法满足这些分层需求。

### 痛点放大

**第一，`.trivyignore` 不够正式。** 它是团队内部的「便利贴」，缺乏标准化格式、没有机器可读性、无法对外交付。当需要向客户、审计机构、合作伙伴证明「我们已评估并接受这些风险」时，`.trivyignore` 拿不出手。

**第二，误报淹没真风险。** 如果扫描报告里 80% 都是已知的「可接受」漏洞，开发和运维会养成「选择性失明」的习惯——直接拉到报告底部看 summary，中间的具体漏洞列表一眼不看。这种「狼来了」效应是安全治理的最大敌人。

**第三，报告格式单一。** Trivy 内置了 table/json/sarif/cyclonedx/spdx 等格式，但每种格式都是「固定模板」，无法按企业需求定制字段、排序、分组、计算逻辑。比如管理层想要「按业务系统分组的漏洞数量趋势」，现有格式无法直接提供。

**第四，VEX 生态不成熟。** VEX（Vulnerability Exploitability eXchange）是业界推出的标准格式，用于声明「某个产品不受某个 CVE 影响」，但团队不知道如何在 Trivy 中生成和使用 VEX，更不知道 VEX 与 SBOM 如何配合使用。

**本章的核心目标是：掌握 Trivy 的高级过滤能力、VEX 的生成与消费、以及 Go Template 的深度定制，建立企业级的漏洞基线管理和分层报告体系。**

---

## 2. 项目设计

**场景**：云帆科技的报告与基线优化专题会，老李（安全负责人）、小胖（开发代表）和大师正在讨论如何让漏洞报告从「噪音」变成「可行动的情报」。

---

**小胖**：「老李，支付网关那个 OpenSSL 的 15 个 HIGH 漏洞，我们到底还要解释多少次？能不能有个『官方认证』的豁免文件，以后扫描时直接过滤掉？」

**老李**：「我也烦。`.trivyignore` 只能我们自己用，给客户看不够正式。而且 `.trivyignore` 是『黑盒』——客户不知道我们为什么忽略，也没法验证我们的理由是否合理。」

**大师**：「技术映射：`.trivyignore` 就像医生手写的『病假条』，只有本院认可；VEX 就像三甲医院盖章的『诊断证明书』，任何医院、保险公司、用人单位都承认。VEX 是机器可读的标准格式（OpenVEX / CycloneDX VEX），包含：

- **产品标识**：哪个软件/镜像/版本。
- **漏洞标识**：哪个 CVE。
- **状态**：受影响、不受影响、已修复、已缓解。
- **理由**：为什么是这个状态（如『组件未启用』、『Vendor Backport』、『已有 WAF 规则』）。
- **时间戳**：声明的创建和更新时间。

客户收到你的 VEX 文件后，可以直接导入他们的漏洞管理平台（如 ServiceNow、Rapid7、Qualys），自动消除已评估的误报。」

**小白**：「Trivy 支持 VEX 吗？」

**大师**：「支持。Trivy 可以：

1. **消费 VEX**：扫描时传入 `--vex vex.json`，Trivy 会根据 VEX 中的声明过滤报告。如果 VEX 说『不受影响』，这个 CVE 就不会出现在输出中。
2. **生成 VEX**：虽然目前 Trivy 不直接生成 VEX，但你可以通过脚本将 `.trivyignore` 转换为 OpenVEX 格式，或者用专门的工具（如 `vexctl`）。」

**小胖**：「那报告定制呢？管理层每周都要我发邮件汇报安全状况，我从 Trivy 的 JSON 里手工复制粘贴，太痛苦了。」

**小白**：「Trivy 的 `--format template` 支持 Go Template，你可以写自己的模板，输出任意格式的报告。比如：

- Markdown 摘要（贴到邮件/Slack）
- HTML 仪表盘（发给管理层）
- CSV 表格（导入 Excel 做分析）
- 自定义 JSON（对接内部系统）

Go Template 的语法和 Jinja2 类似，支持变量、条件、循环、函数。你可以访问 Trivy 报告中的任何字段，甚至做简单的数学计算（如统计百分比）。」

**老李**：「那高级过滤呢？除了 `--severity` 和 `--ignore-unfixed`，还有什么过滤手段？」

**大师**：「Trivy 提供了多层过滤工具箱：

1. **Rego 策略过滤**：用 `--filter-rego` 编写复杂的过滤逻辑，比如『只保留有 PoC 的 HIGH 漏洞』、『排除测试依赖且 CVSS < 7.0 的漏洞』。
2. **VEX 过滤**：用 `--vex` 排除已评估的漏洞。
3. **Package 过滤**：用 `--vuln-type` 只扫 OS 包或语言包；用 `--pkg-types` 精确定位。
4. **时间过滤**：某些版本支持 `--vuln-id` 排除特定 CVE。
5. **自定义模板中的过滤**：在 Go Template 中用 `where` 函数做运行时过滤，输出时只展示感兴趣的子集。」

**小胖**：「听起来我们需要建立一个『漏洞知识库』——每个漏洞的评估结论、理由、状态，都结构化地存起来，然后自动转换成 VEX，再自动应用到扫描中。」

**大师**：「完全正确。这就是『漏洞治理的闭环』：

扫描发现 → 人工评估 → 结论入库 → 生成 VEX → 应用过滤 → 报告降噪 → 管理层看到『干净』的报告 → 真风险不被淹没。」

---

## 3. 项目实战

### 环境准备

- **Trivy**：v0.50+，已安装
- **vexctl**：OpenVEX 命令行工具（可选）
- **测试镜像**：`python:3.4-alpine`（用于测试 VEX 过滤）

### 步骤一：创建 OpenVEX 文件

**目标**：将团队的漏洞评估结论转化为标准 VEX 格式。

创建 `cloud-sail.vex.json`：

```json
{
  "@context": "https://openvex.dev/ns/v0.2.0",
  "@id": "https://cloud-sail.com/vex/payment-gateway-2024",
  "author": "Cloud Sail Security Team <security@cloud-sail.com>",
  "timestamp": "2024-03-15T00:00:00Z",
  "version": 1,
  "statements": [
    {
      "vulnerability": {"name": "CVE-2023-0464"},
      "products": [
        {"@id": "pkg:docker/cloud-sail/payment-gateway@2.3.1"}
      ],
      "status": "not_affected",
      "justification": "vulnerable_code_not_in_execute_path",
      "impact_statement": "Debian has backported the fix to version 1.1.1n-0+deb11u5. The image contains this patched version. Trivy flags it due to upstream version number mismatch, but the vulnerability is not exploitable."
    },
    {
      "vulnerability": {"name": "CVE-2021-23337"},
      "products": [
        {"@id": "pkg:docker/cloud-sail/payment-gateway@2.3.1"}
      ],
      "status": "not_affected",
      "justification": "requires_environment_not_present",
      "impact_statement": "This CVE affects lodash prototype pollution. The lodash instance is only used in unit tests (test scope) and is not included in the production image."
    },
    {
      "vulnerability": {"name": "CVE-2022-0778"},
      "products": [
        {"@id": "pkg:docker/cloud-sail/payment-gateway@2.3.1"}
      ],
      "status": "affected",
      "action_statement": "Upgrade to openssl 1.1.1n-0+deb11u7 in the next sprint. WAF rules have been deployed to block known exploit patterns.",
      "action_statement_timestamp": "2024-03-15T00:00:00Z"
    }
  ]
}
```

**字段解读**：
- `@context`：VEX 规范版本。
- `products.@id`：PURL 格式的产品标识。
- `status`：`not_affected`（不受影响）、`affected`（受影响）、`fixed`（已修复）、`under_investigation`（调查中）。
- `justification`：标准理由码，如 `vulnerable_code_not_in_execute_path`、`requires_environment_not_present`。
- `impact_statement`：人工评估的详细说明。

### 步骤二：用 VEX 过滤扫描报告

**目标**：让 Trivy 扫描时自动排除 VEX 中标记为「不受影响」的漏洞。

```bash
# 不带 VEX 的扫描（会报告所有漏洞）
trivy image --severity HIGH,CRITICAL \
  --format json \
  -o report-without-vex.json \
  cloud-sail/payment-gateway:2.3.1

# 带 VEX 的扫描（自动过滤已评估的漏洞）
trivy image --severity HIGH,CRITICAL \
  --vex cloud-sail.vex.json \
  --format json \
  -o report-with-vex.json \
  cloud-sail/payment-gateway:2.3.1

# 对比差异
cat report-without-vex.json | jq '[.Results[]?.Vulnerabilities?[]?] | length'
cat report-with-vex.json | jq '[.Results[]?.Vulnerabilities?[]?] | length'
```

**预期效果**：`report-with-vex.json` 中的漏洞数量显著减少，`CVE-2023-0464` 和 `CVE-2021-23337` 不再出现。

> **可能遇到的坑**：VEX 中的 `products.@id` 必须与 Trivy 扫描目标精确匹配（包括 PURL 的 scheme、namespace、name、version）。如果镜像 tag 变了（如从 `2.3.1` 变成 `2.3.2`），VEX 不会自动生效，需要更新 VEX 文件或添加版本范围。

### 步骤三：用 Rego 实现高级过滤

**目标**：编写复杂的过滤逻辑，超越简单的 severity 过滤。

创建 `filter-policy.rego`：

```rego
package trivy.filter

# 默认通过所有结果
default allow = true

# 排除测试依赖且 CVSS < 7.0 的漏洞
deny {
  input.PkgType == "node-pkg"
  input.DevDependency == true
  input.CVSS.nvd.V3Score < 7.0
}

# 排除已知无 PoC 且超过 1 年的旧漏洞
deny {
  input.PublishedDate < time.format(time.now_ns() - 31536000000000000, "2006-01-02T15:04:05Z")
  not input.ExploitAvailable
}

# 只保留有修复版本或 CISA KEV 的漏洞
deny {
  not input.FixedVersion
  not input.CisaKeV
}
```

> **注意**：Trivy 的 `--filter-rego` 支持在部分版本中实验性提供，如果当前版本不支持，可以在 Go Template 中实现类似的过滤逻辑。

### 步骤四：定制管理层摘要模板

**目标**：用 Go Template 生成适合不同受众的报告。

创建 `exec-summary.tpl`：

```
{{- /* 管理层执行摘要模板 */ -}}
# 安全扫描执行摘要

**扫描目标**: {{ .ArtifactName }}  
**扫描时间**: {{ now }}  
**数据库版本**: {{ env "TRIVY_DB_VERSION" "unknown" }}  

## 风险概览

{{- $critical := len (where .Results "Vulnerabilities" "severity" "CRITICAL") }}
{{- $high := len (where .Results "Vulnerabilities" "severity" "HIGH") }}
{{- $total := len (where .Results "Vulnerabilities") }}

| 指标 | 数量 | 趋势 |
|------|------|------|
| CRITICAL | {{ $critical }} | {{ if gt $critical 0 }}🔴 上升{{ else }}🟢 清零{{ end }} |
| HIGH | {{ $high }} | {{ if gt $high 5 }}🟡 关注{{ else }}🟢 正常{{ end }} |
| 总漏洞数 | {{ $total }} | - |

## 关键行动项

{{- if gt $critical 0 }}
⚠️ **立即行动**: 存在 {{ $critical }} 个 CRITICAL 漏洞，建议暂停相关服务发版，优先安排修复。
{{- else if gt $high 10 }}
📋 **本周计划**: HIGH 漏洞数量较多（{{ $high }} 个），建议在当前 Sprint 中分配修复资源。
{{- else }}
✅ **状态良好**: 当前风险可控，继续保持常规安全巡检节奏。
{{- end }}

## 按系统分布

{{- range .Results }}
{{- $resultCritical := len (where .Vulnerabilities "severity" "CRITICAL") }}
{{- $resultHigh := len (where .Vulnerabilities "severity" "HIGH") }}
{{- if or (gt $resultCritical 0) (gt $resultHigh 0) }}
- **{{ .Target }}**: CRITICAL={{ $resultCritical }}, HIGH={{ $resultHigh }}
{{- end }}
{{- end }}
```

使用模板：

```bash
trivy image --format template --template @exec-summary.tpl \
  -o exec-summary.md \
  cloud-sail/payment-gateway:2.3.1
```

### 步骤五：定制技术详细报告模板

**目标**：为安全团队生成包含修复指导的详细报告。

创建 `detail-report.tpl`：

```
{{- /* 技术团队详细报告模板 */ -}}
# 安全扫描详细报告

**扫描目标**: {{ .ArtifactName }}  
**扫描时间**: {{ now }}  
**Trivy 版本**: {{ .SchemaVersion }}  

---

## 漏洞清单

| CVE | 包名 | 当前版本 | 修复版本 | 级别 | 状态 | 修复建议 |
|-----|------|----------|----------|------|------|----------|
{{- range .Results }}
{{- range .Vulnerabilities }}
{{- $status := "未修复" }}
{{- if .FixedVersion }}
{{- $status = printf "可修复 → %s" .FixedVersion }}
{{- else }}
{{- $status = "无补丁" }}
{{- end }}
| {{ .VulnerabilityID }} | {{ .PkgName }} | {{ .InstalledVersion }} | {{ .FixedVersion | default "N/A" }} | {{ .Severity }} | {{ $status }} | {{ if .FixedVersion }}升级到 {{ .FixedVersion }}{{ else }}评估缓解措施{{ end }} |
{{- end }}
{{- end }}

---

## 统计汇总

{{- $allVulns := list }}
{{- range .Results }}
{{- range .Vulnerabilities }}
{{- $allVulns = append $allVulns . }}
{{- end }}
{{- end }}

- 总漏洞数: {{ len $allVulns }}
- 可修复: {{ len (where $allVulns "FixedVersion") }}
- 无补丁: {{ sub (len $allVulns) (len (where $allVulns "FixedVersion")) }}
```

### 步骤六：建立自动化报告流水线

**目标**：每日自动生成并分发不同受众的报告。

创建 `daily-report.sh`：

```bash
#!/bin/bash
set -e

IMAGE="cloud-sail/payment-gateway:latest"
DATE=$(date +%Y%m%d)
REPORT_DIR="/var/reports/security/${DATE}"
mkdir -p "$REPORT_DIR"

# 1. 执行扫描（带 VEX 过滤）
trivy image --severity HIGH,CRITICAL \
  --vex /etc/cloud-sail/vex.json \
  --format json \
  -o "$REPORT_DIR/raw.json" \
  "$IMAGE"

# 2. 生成管理层摘要
trivy convert --format template \
  --template @/etc/cloud-sail/templates/exec-summary.tpl \
  --output "$REPORT_DIR/exec-summary.md" \
  "$REPORT_DIR/raw.json"

# 3. 生成技术详细报告
trivy convert --format template \
  --template @/etc/cloud-sail/templates/detail-report.tpl \
  --output "$REPORT_DIR/detail-report.md" \
  "$REPORT_DIR/raw.json"

# 4. 生成 CSV（给合规团队）
trivy convert --format template \
  --template @/etc/cloud-sail/templates/csv-export.tpl \
  --output "$REPORT_DIR/vulns.csv" \
  "$REPORT_DIR/raw.json"

# 5. 发送邮件（假设安装了 mail/mutt）
echo "Daily security report attached." | mail -s "Security Report ${DATE}" \
  -A "$REPORT_DIR/exec-summary.md" \
  -A "$REPORT_DIR/vulns.csv" \
  security-team@cloud-sail.com

# 6. 推送到 Slack
curl -X POST "$SLACK_WEBHOOK" \
  -H "Content-Type: application/json" \
  -d "{\"text\":\"📊 Daily security report for ${IMAGE} generated. Critical: $(cat $REPORT_DIR/raw.json | jq -r '[.Results[]?.Vulnerabilities?[]? | select(.Severity==\"CRITICAL\")] | length')\"}"
```

### 步骤七：VEX 版本管理与自动化更新

**目标**：建立 VEX 文件的生命周期管理。

创建 `vex-manager.py`：

```python
#!/usr/bin/env python3
"""
VEX 文件管理器
功能：验证格式、检查过期声明、同步到扫描节点
"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

def validate_vex(vex_path):
    with open(vex_path) as f:
        vex = json.load(f)
    
    issues = []
    
    # 检查必需字段
    if "@context" not in vex:
        issues.append("Missing @context")
    if "statements" not in vex:
        issues.append("No statements found")
    
    for i, stmt in enumerate(vex.get("statements", [])):
        if "vulnerability" not in stmt:
            issues.append(f"Statement {i}: missing vulnerability")
        if "status" not in stmt:
            issues.append(f"Statement {i}: missing status")
        
        # 检查 'affected' 声明是否有 action_statement
        if stmt.get("status") == "affected" and not stmt.get("action_statement"):
            issues.append(f"Statement {i}: 'affected' status requires action_statement")
    
    return issues

def check_expiry(vex_path, max_age_days=90):
    with open(vex_path) as f:
        vex = json.load(f)
    
    timestamp = vex.get("timestamp", "")
    if not timestamp:
        return ["VEX file has no timestamp"]
    
    vex_date = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    age = datetime.now().astimezone() - vex_date
    
    if age > timedelta(days=max_age_days):
        return [f"VEX file is {age.days} days old, exceeds {max_age_days} day limit"]
    
    return []

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <vex.json>")
        sys.exit(1)
    
    path = sys.argv[1]
    issues = validate_vex(path) + check_expiry(path)
    
    if issues:
        print(f"Found {len(issues)} issue(s):")
        for issue in issues:
            print(f"  - {issue}")
        sys.exit(1)
    else:
        print("VEX file is valid and up-to-date.")
```

### 测试验证

1. 创建 `cloud-sail.vex.json`，验证格式符合 OpenVEX 0.2.0 规范。
2. 执行带 `--vex` 和不带 `--vex` 的扫描，对比漏洞数量差异。
3. 使用 `exec-summary.tpl` 生成管理层报告，确认只展示关键指标和行动建议。
4. 使用 `detail-report.tpl` 生成技术报告，确认包含修复版本和建议。
5. 运行 `daily-report.sh`，验证邮件/Slack 通知正常发送。
6. 运行 `vex-manager.py`，验证能检测格式错误和过期文件。

---

## 4. 项目总结

### 优点 & 缺点

| 维度 | 优点 | 缺点 |
|------|------|------|
| VEX 标准化 | 机器可读、客户认可、审计友好 | 需要维护 VEX 文件的生命周期 |
| 过滤降噪 | 显著减少误报，聚焦真风险 | 过度过滤可能导致漏报 |
| 模板定制 | 一份扫描数据，多份受众报告 | Go Template 学习曲线较陡 |
| 自动化 | 可完全自动化生成和分发 | 邮件/Slack 集成需要额外配置 |
| 合规支撑 | VEX + SBOM 满足多数审计要求 | VEX 生态仍在演进，工具支持不均 |

### 适用场景

1. **客户安全交付**：向金融、政府客户交付「SBOM + VEX」完整安全包。
2. **漏洞基线管理**：将团队已评估的已知风险标准化，减少重复解释成本。
3. **分层报告体系**：管理层看摘要、技术团队看详情、合规团队看 CSV。
4. **误报治理**：对 OS Vendor Backport、开发依赖、未启用功能等场景进行正式声明。
5. **审计举证**：VEX 文件的时间戳和理由，作为「已进行风险评估」的客观证据。

**不适用场景**：
1. 内部工具且无任何外部审计要求的场景——`.trivyignore` 已足够，无需引入 VEX 复杂度。
2. 漏洞数量极少（<10）的小型项目——VEX 的管理成本可能超过收益。

### 注意事项

- **VEX 的版本绑定**：VEX 声明与具体产品版本绑定，版本升级后需重新评估并更新 VEX。
- **PURL 精确匹配**：VEX 中的 `products.@id` 必须与被扫描目标的 PURL 完全匹配，包括版本号。
- **模板的 nil 安全**：Go Template 中访问嵌套字段时，务必做 nil 检查，否则可能导致渲染失败。
- **过滤的平衡**：过度使用 VEX 和自定义过滤可能导致「自我安慰」——团队误以为风险很低，实际上只是被隐藏了。

### 常见踩坑经验

**踩坑案例 1：VEX 文件格式错误导致扫描失败**
- **现象**：`trivy image --vex vex.json` 报错 `invalid vex format`。
- **根因**：VEX 文件缺少必需字段（如 `@context` 或 `statements`）。
- **解法**：使用 `vex-manager.py` 或在线 JSON Schema 验证工具预先检查格式。

**踩坑案例 2：模板中访问 nil 字段导致 panic**
- **现象**：`trivy convert --template` 报错 `nil pointer evaluating`。
- **根因**：Go Template 直接访问了不存在的嵌套字段。
- **解法**：在外层加 `{{ if .Field }}...{{ end }}` 保护，或使用 `default` 函数提供默认值。

**踩坑案例 3：VEX 过滤后报告为空，误以为没有漏洞**
- **现象**：带 VEX 的扫描报告显示 `Total: 0`，但实际存在未评估的漏洞。
- **根因**：VEX 中错误地将所有漏洞标记为 `not_affected`。
- **解法**：定期审计 VEX 文件；保留一份「无过滤」的原始报告作为对照。

### 思考题

1. 假设你的公司维护了 50 个微服务镜像，每个镜像每月发版 2 次。请设计一个「VEX 自动化管理」方案：如何在 CI/CD 中自动生成新版本的 VEX（继承上一版本的评估结论 + 增量评估新漏洞），并确保过期评估不会遗漏新风险？
2. Trivy 的 Go Template 功能强大，但模板文件本身散落在各个项目中。请设计一个「模板即代码」的管理方案：如何像管理 VEX 一样，将报告模板集中管理、版本控制、自动分发到所有扫描节点？

> **答案提示**：第 27 章「软件供应链安全端到端实践」将深入介绍 SBOM + VEX 的签名、存储和全生命周期管理。

---

> **推广计划**：本章是安全运营团队和合规团队的必读内容。建议成立「VEX 治理小组」，负责维护中央 VEX 仓库，所有对外交付的产品都必须附带经审批的 VEX 文件。安全团队将 `exec-summary.tpl` 和 `detail-report.tpl` 纳入标准工具包，确保不同项目输出的报告格式一致。开发团队在收到 VEX 相关培训后，理解「not_affected」不等于「没有漏洞」，而是「经过评估、有正式理由」。合规团队将 VEX 文件与 SBOM 一起归档，作为年度审计的核心证据。
