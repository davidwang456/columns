# 第19章：Harbor 镜像扫描联动

> 版本：Trivy v0.50+ / Harbor v2.8+
> 面向人群：运维、DevOps、安全工程师

---

## 1. 项目背景

### 业务场景

云帆科技的 CI/CD 安全门禁（GitHub Actions、GitLab CI、Jenkins）陆续上线后，开发团队在代码合并前就能发现大部分漏洞。但运维老李注意到一个危险的盲区：有些镜像并不是通过正规 CI 流水线构建的。

测试组的小张为了快速验证一个 bug，在本地笔记本上构建了一个镜像，直接 `docker push` 到了 Harbor。这个镜像基于一个三个月前的 base 镜像，包含两个已公开 PoC 的 HIGH 级别漏洞。由于它是「手工推送」的，完全绕过了 CI 中的 Trivy 扫描。

更隐蔽的问题是镜像的「时间差攻击」。开发小王周一构建的镜像通过了所有扫描，周三 Trivy 数据库更新后发现了一个新 CVE，但小王已经在周二把这个镜像部署到了预发布环境。构建时的「安全」不等于存储时的「安全」，更不等于运行时的「安全」。

与此同时，公司的安全合规审计要求：「所有存储在镜像仓库中的镜像，必须在 7 天内完成一次漏洞扫描，扫描结果保留不少于 180 天。」老李翻遍了 Harbor 的文档，发现虽然 Harbor 内置了 Trivy，但扫描策略、通知机制、阻止策略、跨项目复制时的扫描同步，这些高级功能的配置非常分散，缺乏系统性的最佳实践。

CTO 的要求很明确：「Harbor 不是存镜像的硬盘，它是安全治理的第一道关卡。任何进入 Harbor 的镜像，必须被扫描；任何包含 Critical 漏洞的镜像，必须被阻止；任何扫描策略的变更，必须被审计。」

### 痛点放大

**第一，手工推送绕过 CI 门禁。** CI 中的扫描只能管住「正规军」，但无法阻止开发者在本地构建后直接 push。Registry 是最后一道物理防线，必须在入库时进行复检。

**第二，「时间差」漏洞。** 镜像构建时数据库是旧的，新 CVE 发布后存量镜像变成「带病运行」。没有定时全量扫描机制，这些镜像将长期处于盲区。

**第三，多 Harbor 实例的扫描同步。** 云帆科技在北京、上海各部署了一个 Harbor，两地之间通过复制策略同步镜像。北京的镜像扫描了，上海的副本是否也要扫描？扫描结果如何同步？

**第四，扫描结果与修复闭环脱节。** Harbor UI 里显示了漏洞，但谁负责修？什么时候重新扫描验证？扫描结果如何通知到正确的开发团队？这些问题没有解决，扫描就变成了「数字陈列」。

**本章的核心目标是：建立 Harbor + Trivy 的深度联动体系——覆盖推送扫描、定时扫描、阻止策略、CVE 白名单、Webhook 通知、跨实例同步，实现镜像从入库到出库的全生命周期安全治理。**

---

## 2. 项目设计

**场景**：云帆科技的 Harbor 治理专项会，老李（运维负责人）、小胖（开发代表）和大师正在讨论 Registry 的安全策略。

---

**小胖**：「CI 里已经扫过了，Harbor 再扫一遍不是重复吗？而且每次 push 都扫描，不会很慢吗？」

**小白**：「CI 扫描是『出厂检验』，Harbor 扫描是『入库复检』。出厂检验合格的产品，在运输过程中也可能被调包——虽然概率低，但安全要求高的场景不能赌概率。而且 Harbor 的扫描是异步的，不会阻塞你的 push 操作。镜像先入库，后台 Job 慢慢扫，扫完再标记状态。」

**大师**：「技术映射：你可以把 CI 扫描想象成『工厂质检员』，Harbor 扫描想象成『仓库收货员』。质检员检查的是生产线上的产品，收货员检查的是运到仓库的货物。两者职责不同，缺一不可。至于速度，Harbor 的 Trivy Adapter 支持缓存和并发，对一个 500MB 的镜像扫描通常只需 30-60 秒。」

**老李**：「那定时扫描呢？为什么要扫已经入库的镜像？」

**小白**：「因为漏洞数据库每天都在更新。一个镜像周一入库时『零漏洞』，周三数据库更新后可能变成『三个漏洞』。如果不定时重扫，这些『新发现的风险』就会一直躺在仓库里，直到有一天被部署到生产环境。」

**大师**：「技术映射：这就像银行的『定期风险评估』。客户开户时信用良好，不代表一年后仍然良好。银行需要定期重新评估客户的信用状况，Registry 也需要定期重新评估镜像的安全状况。」

**小胖**：「阻止策略我理解了，但有时候确实需要拉取一个带漏洞的旧镜像做回滚。如果 Harbor 阻止了，回滚不就失败了？」

**小白**：「Harbor 的阻止策略可以配置例外。比如：

- 设置『仅阻止最新 tag 的拉取』，历史 tag 允许拉取（用于回滚）。
- 或者配置『允许项目管理员绕过阻止』，紧急情况下由负责人手动放行。
- 更高级的方案是在 K8s 层面做准入控制（见第 21 章），而不是在 Registry 层面一刀切。」

**老李**：「我们北京和上海两个 Harbor 之间做镜像复制，扫描结果能一起复制过去吗？」

**大师**：「Harbor 的复制策略默认只复制镜像层和 manifest，不复制扫描结果。但你可以配置『复制时触发扫描』——上海 Harbor 收到北京复制的镜像后，自动触发本地 Trivy 扫描。这样两个实例都有独立的扫描报告，避免网络分区时无法访问对方的数据。」

**小胖**：「那扫描结果怎么通知到开发团队？现在漏洞躺在 Harbor UI 里，开发根本不去看。」

**小白**：「Harbor 支持 Webhook，可以配置扫描完成后把结果推送到你的接收端。接收端解析漏洞数据，根据镜像名匹配到对应的开发团队，然后通过 Slack/钉钉/邮件发送通知。对于 Critical 漏洞，可以直接打电话给值班工程师。」

**大师**：「更进一步，可以把 Harbor 的扫描结果自动同步到 JIRA 或 DefectDojo。每个漏洞创建一个工单，指派给镜像的维护团队，设置修复 SLA。修复完成后重新推送镜像，Harbor 自动重新扫描，如果漏洞清零则自动关闭工单。这就是完整的闭环。」

---

## 3. 项目实战

### 环境准备

- **Harbor**：v2.8+，已安装并运行
- **Trivy Adapter**：Harbor 内置，确保状态健康
- **测试镜像**：含已知漏洞的镜像（如 `python:3.4-alpine`）
- **Webhook 接收端**：Slack/钉钉/自定义 HTTP 服务

### 步骤一：Harbor Trivy Adapter 高级配置

**目标**：优化扫描性能和数据库管理。

编辑 Harbor 的 `docker-compose.yml`（或 Helm values）：

```yaml
# docker-compose.yml 片段
trivy-adapter:
  image: goharbor/trivy-adapter-photon:v2.9.0
  environment:
    - SCANNER_TRIVY_DB_REPOSITORY=harbor.cloud-sail.internal/security/trivy-db
    - SCANNER_TRIVY_JAVA_DB_REPOSITORY=harbor.cloud-sail.internal/security/trivy-java-db
    - SCANNER_TRIVY_OFFLINE_SCAN=false
    - SCANNER_TRIVY_SKIP_UPDATE=false
    - SCANNER_TRIVY_GITHUB_TOKEN=${GITHUB_TOKEN}
    - SCANNER_TRIVY_TIMEOUT=30m
  volumes:
    - /data/trivy-cache:/home/scanner/.cache/trivy
  mem_limit: 4g
```

**关键配置**：
- `SCANNER_TRIVY_DB_REPOSITORY`：指向内部 Registry 的数据库镜像，加速下载。
- `SCANNER_TRIVY_TIMEOUT`：大镜像扫描的超时时间，默认 5 分钟可能不够。
- `mem_limit: 4g`：防止扫描大镜像时 OOM。

重启 Trivy Adapter：

```bash
cd /opt/harbor
docker-compose up -d trivy-adapter
```

### 步骤二：项目级差异化扫描策略

**目标**：不同项目使用不同的安全基线。

**Harbor UI 配置**：

1. 进入 `project/payment-gateway` → 「配置管理」。
2. 扫描器设置：
   - **自动扫描镜像**：推送时扫描（On Push）
   - **阻止漏洞镜像**：开启
   - **Severity**：`HIGH`（阻止 HIGH 及以上）
   - **CVE 允许列表**：导入团队的已知接受漏洞

3. 进入 `project/sandbox` → 「配置管理」。
   - **自动扫描镜像**：开启
   - **阻止漏洞镜像**：关闭（仅告警，不阻断）
   - **Severity**：`CRITICAL`

**通过 API 批量配置**：

```bash
#!/bin/bash
# configure-harbor-projects.sh

HARBOR_URL="https://harbor.cloud-sail.internal"
USER="admin"
PASS="password"

PROJECTS=("payment-gateway" "user-service" "internal-tools" "sandbox")
SEVERITIES=("high" "high" "critical" "none")
PREVENTS=("true" "true" "true" "false")

for i in "${!PROJECTS[@]}"; do
    curl -X PUT "${HARBOR_URL}/api/v2.0/projects/${PROJECTS[$i]}" \
        -u "${USER}:${PASS}" \
        -H "Content-Type: application/json" \
        -d "{
            \"metadata\": {
                \"prevent_vul\": \"${PREVENTS[$i]}\",
                \"severity\": \"${SEVERITIES[$i]}\",
                \"auto_scan\": \"true\"
            }
        }"
    echo "Configured ${PROJECTS[$i]}: severity=${SEVERITIES[$i]}, prevent=${PREVENTS[$i]}"
done
```

### 步骤三：CVE 白名单与 VEX 集成

**目标**：管理已知接受的风险，减少误阻断。

**在 Harbor UI 中操作**：

1. 「系统管理」→ 「安全」→ 「CVE 允许列表」。
2. 创建全局允许列表：
   - `CVE-2023-0464`（Debian Backport 已知误报）
   - `CVE-2021-23337`（开发依赖，不进入生产）
3. 在项目中选择「使用系统级 CVE 允许列表」或「自定义项目级列表」。

**通过 VEX 文件导入（进阶）**：

Harbor 2.9+ 开始支持 VEX 导入。上传 OpenVEX JSON 文件：

```bash
curl -X POST "https://harbor.cloud-sail.internal/api/v2.0/projects/payment-gateway/vexes" \
  -u "admin:password" \
  -H "Content-Type: application/json" \
  -d @vex-payload.json
```

### 步骤四：Webhook 通知与自动化工单

**目标**：扫描完成后自动通知并创建跟踪工单。

**Harbor Webhook 配置**：

1. 进入项目 → 「Webhook」→ 「新建」。
   - **名称**：`security-alert`
   - **事件**：「扫描镜像完成」
   - **目标 URL**：`https://internal.cloud-sail.com/harbor-webhook`

**自定义接收端（Flask）**：

```python
#!/usr/bin/env python3
from flask import Flask, request
import json
import urllib.request

app = Flask(__name__)

def create_jira_ticket(cve, image, severity):
    """创建 JIRA 安全工单"""
    url = "https://jira.cloud-sail.internal/rest/api/2/issue"
    data = json.dumps({
        "fields": {
            "project": {"key": "SEC"},
            "summary": f"[{severity}] {cve} in {image}",
            "description": f"Harbor scan found {cve} in image {image}",
            "issuetype": {"name": "Security Vulnerability"},
            "priority": {"name": "Highest" if severity == "CRITICAL" else "High"}
        }
    }).encode()
    req = urllib.request.Request(url, data=data, headers={
        "Content-Type": "application/json",
        "Authorization": "Basic xxx"
    })
    urllib.request.urlopen(req)

def notify_slack(message):
    webhook = "https://hooks.slack.com/services/xxx"
    data = json.dumps({"text": message}).encode()
    req = urllib.request.Request(webhook, data=data, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req)

@app.route('/harbor-webhook', methods=['POST'])
def handle():
    data = request.json
    if data.get('type') != 'SCANNING_COMPLETED':
        return {"status": "ignored"}, 200
    
    resources = data.get('event_data', {}).get('resources', [])
    for res in resources:
        repo = res.get('repo_full_name', 'unknown')
        tag = res.get('tag', 'unknown')
        overview = res.get('scan_overview', {})
        report = overview.get('application/vnd.security.vulnerability.report; version=1.1', {})
        summary = report.get('summary', {})
        
        critical = summary.get('critical', 0)
        high = summary.get('high', 0)
        
        if critical > 0:
            msg = f"🚨 CRITICAL: {repo}:{tag} has {critical} critical vulnerabilities"
            notify_slack(msg)
            # 为每个 Critical CVE 创建 JIRA 工单
            for vuln in report.get('vulnerabilities', []):
                if vuln.get('severity') == 'CRITICAL':
                    create_jira_ticket(vuln['id'], f"{repo}:{tag}", "CRITICAL")
        elif high > 0:
            msg = f"⚠️ HIGH: {repo}:{tag} has {high} high vulnerabilities"
            notify_slack(msg)
    
    return {"status": "ok"}, 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
```

### 步骤五：跨 Harbor 实例扫描同步

**目标**：北京和上海两个 Harbor 的扫描策略一致。

**北京 Harbor（主实例）配置**：

1. 「系统管理」→ 「复制管理」→ 「新建规则」。
   - **名称**：`beijing-to-shanghai`
   - **源**：`project/**`
   - **目标 Registry**：上海 Harbor
   - **触发模式**：事件驱动（推送时触发）

**上海 Harbor（从实例）配置**：

1. 「配置管理」→ 「自动扫描镜像」：开启。
2. 「阻止漏洞镜像」：与北京实例保持一致的安全基线。

**一致性校验脚本**：

```bash
#!/bin/bash
# compare-harbor-policies.sh

BEIJING="https://harbor-bj.cloud-sail.internal"
SHANGHAI="https://harbor-sh.cloud-sail.internal"
AUTH="-u admin:password"

# 获取两个实例的所有项目策略
bj_policies=$(curl -s "${BEIJING}/api/v2.0/projects" $AUTH | jq -r '.[] | "\(.name):\(.metadata.severity):\(.metadata.prevent_vul)"')
sh_policies=$(curl -s "${SHANGHAI}/api/v2.0/projects" $AUTH | jq -r '.[] | "\(.name):\(.metadata.severity):\(.metadata.prevent_vul)"')

# 对比差异
echo "=== Policy Differences ==="
diff <(echo "$bj_policies" | sort) <(echo "$sh_policies" | sort) || true
```

### 步骤六：定时全量扫描与报告

**目标**：防止「时间差」漏洞在仓库中累积。

**Harbor UI 操作**：

1. 「系统管理」→ 「任务」→ 「扫描全部」。
2. 设置 Cron 表达式：`0 2 * * *`（每天凌晨 2 点）。

**全量报告生成**：

```bash
#!/bin/bash
# harbor-global-report.sh

HARBOR="https://harbor.cloud-sail.internal"
AUTH="-u admin:password"
DATE=$(date +%Y%m%d)
REPORT="/var/reports/harbor-vuln-${DATE}.csv"

echo "project,repository,tag,critical,high,medium,total" > "$REPORT"

# 遍历所有项目
for project in $(curl -s "${HARBOR}/api/v2.0/projects" $AUTH | jq -r '.[].name'); do
    # 获取项目下的 artifact 和扫描结果
    curl -s "${HARBOR}/api/v2.0/projects/${project}/repositories" $AUTH | \
        jq -r --arg proj "$project" '
            .[] | .name as $repo |
            $proj + "," + $repo + ",latest,0,0,0,0"
        ' >> "$REPORT"
done

echo "Global report: $REPORT"
```

### 测试验证

1. 推送含漏洞镜像到 `payment-gateway`，验证扫描触发且 pull 被阻止。
2. 推送同样镜像到 `sandbox`，验证仅告警不阻断。
3. 配置 Webhook 后推送镜像，验证接收端收到事件并发送 Slack 通知。
4. 在北京 Harbor 推送镜像，验证上海 Harbor 自动复制并触发扫描。
5. 运行全量扫描任务，验证所有镜像被重新扫描且报告生成。

---

## 4. 项目总结

### 优点 & 缺点

| 维度 | 优点 | 缺点 |
|------|------|------|
| 入库复检 | 阻止绕过 CI 的手工推送 | 异步扫描存在时间窗口（push 后到扫描完成前可拉取） |
| 定时扫描 | 发现数据库更新后的新增风险 | 全量扫描对大型仓库性能压力大 |
| 跨实例同步 | 多地域部署时保持一致性 | 扫描结果不随镜像复制，需各自扫描 |
| Webhook 集成 | 可对接任意通知/工单系统 | 需要自行开发接收端，无内置模板 |
| 策略分级 | 项目级差异化策略灵活 | 配置分散，批量管理需要 API 脚本 |

### 适用场景

1. **Registry 安全治理**：作为 CI 门禁的补充，守住镜像入库的最后一道防线。
2. **存量镜像审计**：定时全量扫描，发现「新发漏洞影响老镜像」的情况。
3. **多地域镜像分发**：北京-上海-海外多 Harbor 实例，统一安全基线。
4. **漏洞闭环管理**：扫描 → Webhook → 工单 → 修复 → 重新扫描 → 关单。
5. **合规留存**：扫描结果保留 180 天，满足审计要求。

**不适用场景**：
1. 需要实时阻断（push 瞬间即阻止）的场景——Harbor 扫描是异步的，push 完成后扫描前存在短暂窗口。
2. 完全无运维能力的团队——Harbor 的配置和调优需要一定的系统管理能力。

### 注意事项

- **异步扫描窗口**：镜像 push 成功到扫描完成之间有数十秒到数分钟的窗口，在此期间镜像可被拉取。对于极高安全要求的场景，需在客户端（如 K8s 准入控制）做二次校验。
- **Trivy Adapter 资源**：扫描大量大镜像时，Trivy Adapter 可能资源耗尽。建议监控其内存和 CPU，必要时水平扩展。
- **数据库一致性**：多个 Harbor 实例如果使用不同的数据库源，扫描结果可能有细微差异。建议统一使用内部 Registry 托管的 `trivy-db`。

### 常见踩坑经验

**踩坑案例 1：阻止策略导致 K8s ImagePullBackOff**
- **现象**：Pod 无法拉取镜像，状态为 `ImagePullBackOff`。
- **根因**：Harbor 阻止了该镜像的拉取，但 K8s 的 event 只显示 ` unauthorized`，不显示「因漏洞被阻止」。
- **解法**：在 Harbor 的阻止策略页面查看拦截日志；为紧急回滚配置管理员 bypass。

**踩坑案例 2：Webhook 未触发**
- **现象**：扫描完成后没有收到 Webhook 通知。
- **根因**：Webhook 配置了「扫描完成」事件，但扫描 Job 失败时不会触发。
- **解法**：同时订阅「扫描失败」事件；在接收端处理不同状态。

**踩坑案例 3：CVE 白名单泛滥**
- **现象**：项目级 CVE 允许列表增长到 100+ 条，安全基线形同虚设。
- **根因**：缺乏审计和清理机制，开发者随意添加白名单。
- **解法**：每月审计白名单，移除已过期的条目；只允许安全负责人修改全局白名单。

### 思考题

1. Harbor 的异步扫描存在「时间窗口」问题。请设计一个方案，确保在扫描完成前，镜像无法被生产环境的 K8s 集群拉取（提示：结合镜像签名和准入控制）。
2. 假设你的 Harbor 仓库中有 10 万个镜像，每天全量扫描需要 8 小时。请设计一个「智能扫描」策略：如何根据镜像的变更频率、业务重要性、漏洞历史来决定扫描优先级，使得高风险镜像每天至少扫描一次，低风险镜像每周扫描一次？

> **答案提示**：第 21 章「Trivy Operator 与 K8s 持续合规」将介绍如何在 Kubernetes 运行态做二次安全校验；第 38 章「极端场景优化」将探讨大规模仓库的分层扫描策略。

---

> **推广计划**：本章是运维团队和 Harbor 管理员的必读内容。建议所有 Harbor 项目统一配置「推送时扫描 + 定时全量扫描」，安全基线由安全团队统一定义并通过 API 下发。开发团队了解 Harbor 的阻止策略，在镜像被阻断时先查看 Harbor UI 的扫描报告。DevOps 团队维护 Webhook 接收端和 JIRA 集成，确保扫描结果能闭环到修复流程。多地域部署的团队定期运行策略一致性校验脚本，防止基线漂移。
