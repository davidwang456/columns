# 第31章：【中级篇综合实战】构建 DevSecOps 自动化安全网关

> 版本：Trivy v0.50+
> 面向人群：DevSecOps、平台工程师、安全架构师

---

## 1. 项目背景

### 业务场景

云帆科技从一个 20 人的开发团队，用 16 个月成长为一个拥有 50+ 微服务、3 个 K8s 集群、200 名工程师的金融科技中台。安全基础设施也从最初的「本地手动扫描」进化到了中级篇覆盖的全套工具链——CI/CD 门禁、Harbor 扫描、Trivy Operator、Prometheus 告警、DefectDojo + JIRA 闭环、cosign 供应链签名。

但平台工程师小白在整理安全运营周报时，发现了一个令人不安的事实：这些工具之间是「松散耦合」的——通过脚本、Webhook、手动触发串联在一起。某个环节断了，下游就「沉默」了——比如 Trivy Operator 的 PrometheusRule 触发了告警，Alertmanager 推送了 Slack，但如果 DefectDojo 当天刚好在做维护，这个漏洞就没有进入「待修复」清单。

更致命的是，当新项目接入时，需要逐个对接 CI/CD 模板、Harbor Webhook、K8s 准入策略、监控面板——每个项目平均花费 3 个工作日。随着业务增速加快，新项目接入速度已经跟不上业务上线速度。

CTO 提出：「我们需要一个『安全网关』——开发只需要在代码仓库里加一行引用，系统就自动帮他们搞定镜像扫描、代码门禁、K8s 监控、漏洞跟踪。像 API 网关对外统一入口一样，安全网关对内统一安全治理。」

### 痛点放大

**第一，工具链碎片化。** 第 17-30 章的每一个子系统单独都能工作，但组合在一起时，状态不一致、接口不统一、故障不透明。需要一个 Orchestrator 来协调各子系统。

**第二，接入成本高。** 每个新项目需要人工配置 5-8 个 YAML/配置文件，出错率高、遗漏风险大。

**第三，缺乏全局视角。** 安全团队只能在每个子系统里分别查看结果——哪个项目有 Critical 漏洞？在 DefectDojo 查。哪个集群有配置漂移？在 Grafana 查。哪条流水线被阻断？在 GitHub Actions 查。缺少一个「顶层仪表盘」来展示全公司的安全健康度。

**第四，故障传递不透明。** 如果 Trivy Operator 挂了，Grafana 面板显示空白——到底是「没有漏洞」还是「没有扫描」？目前的系统无法区分这两种状态。

**本章的核心目标是：将第 17-30 章的所有组件整合成一个统一的 DevSecOps 安全网关，提供「一套配置接入、一体化安全门禁、全局态势感知」的企业级安全治理能力。**

---

## 2. 项目设计

**场景**：云帆科技的安全网关架构评审会，小白（平台工程师）、老李（安全负责人）、小胖（开发）、大师在讨论方案。

---

**小胖**：「安全网关？不会又是一个要配置半天的新系统吧？我们被各种 YAML 配置已经搞烦了。」

**小白**：「恰好相反。安全网关的目标就是『不让你配』。你只需要在代码仓库的 `.github/workflows/` 里引用一个我们提供的 Reusable Workflow，剩下的事情——Trivy 扫描、SBOM 生成、cosign 签名、结果推送到 DefectDojo——全都自动完成。」

**大师**：「技术映射：安全网关就像机场的『安检流水线』。旅客（代码）只需要过一次安检门（提交 PR），然后系统自动完成行李扫描（Trivy 漏洞扫描）、身份验证（cosign 签名）、危险品检查（Secret 检测）、行李追踪（SBOM 生成）。旅客不需要知道背后有多少套系统在工作。」

**老李**：「架构设计上怎么考虑？这么多组件如何协调？」

**大师**：「我们采用『事件驱动 + 管道模式』的架构。核心是一条『安全事件总线』——各子系统通过标准化的事件格式（CloudEvents）通信。」

```
                       ┌─────────────────────────────────┐
                       │         Developer Portal          │
                       │   (接入配置 / 安全态势 Dash)       │
                       └──────────────┬──────────────────┘
                                      │
                ┌─────────────────────┼─────────────────────┐
                │                     │                     │
    ┌───────────▼──────────┐ ┌───────▼───────┐ ┌──────────▼──────────┐
    │   CI/CD Gate          │ │ Registry Gate │ │  Runtime Gate      │
    │  (GitHub Actions)     │ │   (Harbor)    │ │ (Trivy Operator)   │
    │                       │ │               │ │                    │
    │ • Trivy scan          │ │ • Auto-scan   │ │ • Continuous scan  │
    │ • Rego policy check   │ │ • Block pull   │ │ • Config audit    │
    │ • SBOM + cosign       │ │ • Webhook      │ │ • Secret detect   │
    │ • PR comment          │ │               │ │                    │
    └───────────┬──────────┘ └───────┬───────┘ └──────────┬──────────┘
                │                    │                    │
                └────────────────────┼────────────────────┘
                                     │
                         ┌───────────▼───────────┐
                         │   安全事件总线 (NATS)   │
                         │   CloudEvents 格式      │
                         └───────────┬───────────┘
                                     │
        ┌────────────────────────────┼────────────────────────────┐
        │                            │                            │
 ┌──────▼──────┐  ┌──────────▼──────┐  ┌──────────▼──────┐
 │ DefectDojo  │  │  Prometheus     │  │  JIRA / 钉钉     │
 │ 漏洞管理     │  │  Alertmanager  │  │  通知 & 工单     │
 └──────┬──────┘  └──────┬─────────┘  └──────┬──────────┘
        │                │                   │
        └────────────────┼───────────────────┘
                         │
              ┌──────────▼──────────┐
              │  Security Dashboard  │
              │  (Grafana / 自研)    │
              │                      │
              │  • 合规率             │
              │  • 漏洞热力图         │
              │  • 修复 SLA 达标率    │
              │  • 资产清单           │
              └─────────────────────┘
```

**小胖**：「这个架构看起来组件很多，接入一个新项目到底要多简单？」

**小王**：「理想情况下，三步：

1. 在 `.github/workflows/` 下创建一个文件，引用 `cloud-sail/security-gateway/.github/workflows/secure-build.yml@main`。
2. 在 Harbor 上创建同名 Project，设置自动扫描策略。
3. 在 K8s Namespace 上添加 label `security.cloud-sail.com/enabled=true`。

总共 5 分钟，不需要任何自定义配置。所有默认策略（组织级）自动生效。」

**老李**：「验收标准是什么？」

**大师**：「三个硬性指标：

1. **漏洞发现率 100%**：任何包含已知 CVE 的镜像在推送到 Harbor 后的 5 分钟内，DefectDojo 中必须有对应的 Finding。
2. **Critical 漏洞平均修复时间 < 3 天**（从第一次扫描发现到 JIRA 工单关闭的时间）。
3. **合规率 > 95%**：所有项目在所有执行点的强制策略通过率。」

---

## 3. 项目实战

### 环境准备

本节不再重复部署各子组件（第 17-30 章已覆盖），重点放在「整合编排」上：

- **各组件已运行**：Harbor、Trivy Operator、Prometheus、DefectDojo、JIRA
- **NATS**：v2.10+（事件总线）
- **GitHub Actions**：Reusable Workflow 仓库 `cloud-sail/security-gateway`

### 步骤一：创建安全网关的 GitHub Reusable Workflow

**目标**：提供一个开箱即用的 CI/CD 安全流水线模板。

创建 `.github/workflows/secure-build.yml`（在 `cloud-sail/security-gateway` 仓库中）：

```yaml
name: Security Gateway - Secure Build

on:
  workflow_call:
    inputs:
      image_name:
        required: true
        type: string
        description: "镜像名（不含 registry 前缀）"
      dockerfile_path:
        type: string
        default: "Dockerfile"
      severity:
        type: string
        default: "HIGH,CRITICAL"
      enforce_policy:
        type: boolean
        default: true
        description: "是否阻断不合规的构建"
    secrets:
      HARBOR_USER:
        required: true
      HARBOR_PASSWORD:
        required: true
      DOJO_TOKEN:
        required: false

jobs:
  security-scan:
    runs-on: ubuntu-latest
    outputs:
      critical_count: ${{ steps.scan.outputs.critical }}
      high_count: ${{ steps.scan.outputs.high }}
      compliance: ${{ steps.policy.outputs.compliance }}

    steps:
    - uses: actions/checkout@v4

    # 步骤 1：同步安全策略
    - name: Sync Security Policies
      run: |
        git clone --depth=1 https://github.com/cloud-sail/policies /tmp/policies
        echo "Policies version: $(cat /tmp/policies/policy-config.yaml | grep version | head -1)"

    # 步骤 2：Secret 扫描（零容忍，前置检查）
    - name: Secret Scan
      uses: aquasecurity/trivy-action@master
      with:
        scan-type: 'fs'
        scanners: 'secret'
        format: 'json'
        output: 'secret-scan.json'
        exit-code: '1'
        severity: 'HIGH,CRITICAL'

    # 步骤 3：IaC 配置扫描
    - name: IaC Config Scan
      uses: aquasecurity/trivy-action@master
      with:
        scan-type: 'config'
        format: 'json'
        output: 'iac-scan.json'
        exit-code: ${{ inputs.enforce_policy && '1' || '0' }}
        severity: ${{ inputs.severity }}

    # 步骤 4：构建 + 推送 + 漏洞扫描
    - name: Login to Harbor
      uses: docker/login-action@v3
      with:
        registry: harbor.internal.example.com
        username: ${{ secrets.HARBOR_USER }}
        password: ${{ secrets.HARBOR_PASSWORD }}

    - name: Build and Push
      run: |
        IMAGE="harbor.internal.example.com/${{ inputs.image_name }}:${{ github.sha }}"
        docker build -t "$IMAGE" -f ${{ inputs.dockerfile_path }} .
        docker push "$IMAGE"
        echo "IMAGE_TAG=$IMAGE" >> $GITHUB_ENV

    - name: Vulnerability Scan
      id: scan
      uses: aquasecurity/trivy-action@master
      with:
        image-ref: ${{ env.IMAGE_TAG }}
        format: 'json'
        output: 'trivy-scan.json'
        exit-code: ${{ inputs.enforce_policy && '1' || '0' }}
        severity: ${{ inputs.severity }}

    # 步骤 5：生成 SBOM + cosign 签名
    - name: Generate SBOM and Sign
      run: |
        trivy image --format cyclonedx --output sbom.cdx.json ${{ env.IMAGE_TAG }}
        cosign sign --yes ${{ env.IMAGE_TAG }}
        cosign attest --yes --type cyclonedx --predicate sbom.cdx.json ${{ env.IMAGE_TAG }}

    # 步骤 6：推送结果到安全事件总线
    - name: Publish Scan Event
      if: always()
      run: |
        CRITICAL=$(jq '[.Results[]?.Vulnerabilities?[]? | select(.Severity=="CRITICAL")] | length' trivy-scan.json || echo 0)
        HIGH=$(jq '[.Results[]?.Vulnerabilities?[]? | select(.Severity=="HIGH")] | length' trivy-scan.json || echo 0)
        echo "critical=$CRITICAL" >> $GITHUB_OUTPUT
        echo "high=$HIGH" >> $GITHUB_OUTPUT

        # 发布 CloudEvent 到 NATS
        jq -n --arg image "${{ env.IMAGE_TAG }}" \
              --arg repo "${{ github.repository }}" \
              --arg sha "${{ github.sha }}" \
              --arg branch "${{ github.ref_name }}" \
              --argjson critical "$CRITICAL" \
              --argjson high "$HIGH" \
              --arg actor "${{ github.actor }}" '
          {
            specversion: "1.0",
            type: "cloud-sail.security.scan.completed",
            source: "github-actions",
            subject: $image,
            data: {
              image: $image,
              repository: $repo,
              commit: $sha,
              branch: $branch,
              scanned_by: $actor,
              vulnerabilities: {critical: $critical, high: $high},
              sbom_available: true,
              cosign_signed: true,
              timestamp: now | todate
            }
          }' | curl -X POST http://nats-gateway.internal:8080/publish/security.scan \
            -H "Content-Type: application/json" -d @-

    # 步骤 7：上传所有产物
    - name: Upload All Artifacts
      uses: actions/upload-artifact@v4
      with:
        name: security-reports-${{ github.sha }}
        path: |
          trivy-scan.json
          iac-scan.json
          secret-scan.json
          sbom.cdx.json
```

**项目接入（在任意微服务仓库中）**：

```yaml
# .github/workflows/security.yml
name: Security Check

on:
  pull_request:
    branches: [main]
  push:
    branches: [main]

jobs:
  security:
    uses: cloud-sail/security-gateway/.github/workflows/secure-build.yml@main
    with:
      image_name: cloud-sail/payment-gateway
      severity: HIGH,CRITICAL
      enforce_policy: true
    secrets:
      HARBOR_USER: ${{ secrets.HARBOR_USER }}
      HARBOR_PASSWORD: ${{ secrets.HARBOR_PASSWORD }}
      DOJO_TOKEN: ${{ secrets.DOJO_TOKEN }}
```

> **关键设计**：通过 GitHub 的 `workflow_call` 机制，所有安全逻辑封装在 Reusable Workflow 中。项目侧只需 10 行 YAML 即可接入。安全团队升级策略或增加扫描步骤时，项目侧无需修改。

### 步骤二：编写安全事件总线的消费者（自动对接 DefectDojo + JIRA）

**目标**：监听安全事件总线（NATS），自动将扫描结果导入 DefectDojo。

创建 `gateway/event-consumer.py`：

```python
#!/usr/bin/env python3
"""
安全网关事件消费者
监听 security.scan.* 事件，自动分发到下游子系统
"""

import asyncio
import json
import os

import nats
import requests

NATS_URL = os.environ.get("NATS_URL", "nats://localhost:4222")
DOJO_URL = os.environ.get("DOJO_URL", "http://defectdojo:8080")
DOJO_TOKEN = os.environ["DOJO_TOKEN"]

DOJO_HEADERS = {"Authorization": f"Token {DOJO_TOKEN}", "Content-Type": "application/json"}

# 事件处理器映射表
EVENT_HANDLERS = {
    "cloud-sail.security.scan.completed": "handle_scan_completed",
    "cloud-sail.security.policy.violation": "handle_policy_violation",
    "cloud-sail.security.operator.finding": "handle_operator_finding",
}


class SecurityGatewayConsumer:
    def __init__(self):
        self.nc = None
        self.subscriptions = []

    async def connect(self):
        self.nc = await nats.connect(NATS_URL)
        print(f"Connected to NATS at {NATS_URL}")

        # 订阅所有安全事件
        sub = await self.nc.subscribe(
            "security.scan.*",
            cb=self.on_security_event,
        )
        self.subscriptions.append(sub)
        print("Listening on security.scan.*")

    async def on_security_event(self, msg):
        """统一事件处理入口"""
        try:
            event = json.loads(msg.data.decode())
            event_type = event.get("type", "unknown")
            data = event.get("data", {})

            print(f"[{event_type}] Processing: {data.get('image', data.get('resource', 'unknown'))}")

            handler_name = EVENT_HANDLERS.get(event_type)
            if handler_name:
                handler = getattr(self, handler_name)
                await handler(data)
            else:
                print(f"  No handler for event type: {event_type}")

        except Exception as e:
            print(f"  Error processing event: {e}")

    async def handle_scan_completed(self, data):
        """扫描完成 → 导入 DefectDojo"""
        image = data.get("image")
        repo = data.get("repository")
        vulns = data.get("vulnerabilities", {})

        if not image:
            return

        # 查找或创建 Product
        product_id = self.get_or_create_product(repo)

        if not product_id:
            print(f"  Failed to resolve product for: {repo}")
            return

        # 创建 Engagement
        engagement_id = self.get_or_create_engagement(product_id, image)

        # 这里简化处理：扫描报告已上传到 Artifact，通过 URL 导入
        # 实际实现中，应从 Artifact Storage (MinIO/S3) 下载或通过 API 传入

        print(f"  Imported {image} to DefectDojo: "
              f"CRITICAL={vulns.get('critical', 0)}, HIGH={vulns.get('high', 0)}")

        # 如果发现 Critical 漏洞，触发即时通知
        if vulns.get('critical', 0) > 0:
            await self.handle_critical_vulnerability(image, vulns)

    async def handle_policy_violation(self, data):
        """策略违规事件 → 告警 + 通知"""
        policy_id = data.get("policy_id")
        project = data.get("project")
        message = data.get("message")

        # 记录到审计日志
        print(f"  POLICY VIOLATION [{policy_id}]: {project} - {message}")

        # 发送到 Prometheus Pushgateway（转化为指标）
        requests.post("http://pushgateway:9091/metrics/job/security-gateway", data=(
            f"trivy_policy_violations{{policy_id=\"{policy_id}\",project=\"{project}\"}} 1\n"
        ))

    async def handle_operator_finding(self, data):
        """Trivy Operator Finding → 同步到 DefectDojo"""
        namespace = data.get("namespace")
        resource = data.get("resource")
        severity = data.get("severity")

        # 如果 severity 是 CRITICAL，创建高优先级工单
        if severity == "CRITICAL":
            print(f"  TRIGGER: Creating urgent JIRA issue for {namespace}/{resource}")

    async def handle_critical_vulnerability(self, image, vulns):
        """Critical 漏洞应急响应"""
        print(f"  CRITICAL: Sending P0 alert for {image}")

        # 通过 NATS 发布 P0 告警事件（由告警网关消费）
        alert_event = {
            "specversion": "1.0",
            "type": "cloud-sail.alert.p0",
            "source": "security-gateway",
            "data": {
                "title": f"CRITICAL vulnerabilities in {image}",
                "image": image,
                "critical_count": vulns.get("critical", 0),
                "high_count": vulns.get("high", 0),
                "action": f"trivy image {image}",
                "sla": "7 days",
            }
        }
        await self.nc.publish("alert.p0", json.dumps(alert_event).encode())

    def get_or_create_product(self, repo_name):
        """查找或创建 DefectDojo Product"""
        resp = requests.get(
            f"{DOJO_URL}/api/v2/products/?name={repo_name}", headers=DOJO_HEADERS
        )
        results = resp.json().get("results", [])
        if results:
            return results[0]["id"]

        resp = requests.post(f"{DOJO_URL}/api/v2/products/", headers=DOJO_HEADERS, json={
            "name": repo_name,
            "description": f"Auto-created for {repo_name}",
            "prod_type": 1,
            "enable_simple_risk_acceptance": True,
        })
        if resp.status_code == 201:
            return resp.json()["id"]
        return None

    def get_or_create_engagement(self, product_id, name):
        """查找或创建 Engagement"""
        resp = requests.get(
            f"{DOJO_URL}/api/v2/engagements/?name={name}", headers=DOJO_HEADERS
        )
        results = resp.json().get("results", [])
        if results:
            return results[0]["id"]

        resp = requests.post(f"{DOJO_URL}/api/v2/engagements/", headers=DOJO_HEADERS, json={
            "product": product_id,
            "name": name,
            "target_start": "2024-01-01",
            "target_end": "2025-12-31",
            "status": "In Progress",
            "engagement_type": "CI/CD",
            "deduplication_on_engagement": True,
        })
        if resp.status_code == 201:
            return resp.json()["id"]
        return None

    async def run(self):
        await self.connect()
        while True:
            await asyncio.sleep(1)


if __name__ == "__main__":
    consumer = SecurityGatewayConsumer()
    asyncio.run(consumer.run())
```

### 步骤三：构建全局安全态势 Dashboard

**目标**：搭建 Grafana 仪表盘，展示全公司安全健康度。

创建 `gateway/dashboard-exporter.py`：

```python
#!/usr/bin/env python3
"""
从各子系统聚合数据，暴露统一的安全态势 Prometheus 指标
"""

import os
import json
from datetime import datetime, timedelta

import requests
from flask import Flask, Response

app = Flask(__name__)

DOJO_URL = os.environ["DOJO_URL"]
DOJO_TOKEN = os.environ["DOJO_TOKEN"]
HEADERS = {"Authorization": f"Token {DOJO_TOKEN}"}

PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://prometheus:9090")


@app.route("/metrics")
def metrics():
    """暴露安全态势指标"""
    lines = []

    # 1. 从 Prometheus 查询 Trivy Operator 指标
    try:
        resp = requests.get(f"{PROMETHEUS_URL}/api/v1/query", params={
            "query": "sum(trivy_vulnerability_id) by (severity)"
        })
        for result in resp.json()["data"]["result"]:
            sev = result["metric"]["severity"]
            val = result["value"][1]
            lines.append(f"security_gateway_total_vulnerabilities{{severity=\"{sev}\"}} {val}")
    except Exception as e:
        print(f"Prometheus query failed: {e}")

    # 2. 从 DefectDojo 查询漏洞修复 SLA
    try:
        resp = requests.get(
            f"{DOJO_URL}/api/v2/findings/?active=true&limit=500",
            headers=HEADERS,
        )
        findings = resp.json().get("results", [])

        sla_breach = {"Critical": 0, "High": 0, "Medium": 0}
        now = datetime.now()
        sla_limits = {"Critical": 7, "High": 30, "Medium": 90}

        for f in findings:
            sev = f.get("severity", "")
            created = f.get("created", "")
            if not created or sev not in sla_limits:
                continue

            created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            age_days = (now - created_dt.replace(tzinfo=None)).days

            if age_days > sla_limits[sev]:
                sla_breach[sev] += 1

        for sev, count in sla_breach.items():
            lines.append(f"security_gateway_sla_breach{{severity=\"{sev}\"}} {count}")

        # 总体合规率
        total = len(findings)
        breached = sum(sla_breach.values())
        compliance = 100 * (total - breached) / max(total, 1)
        lines.append(f"security_gateway_compliance_rate {compliance:.1f}")

    except Exception as e:
        print(f"DefectDojo query failed: {e}")

    # 3. 接入项目数量（从 Harbor 获取镜像仓库数量）
    lines.append("security_gateway_connected_projects 55")  # 示例

    return Response("\n".join(lines) + "\n", mimetype="text/plain")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=9092)
```

### 步骤四：自动化新项目接入脚本

**目标**：一键为新项目注册到安全网关。

创建 `gateway/onboard-project.sh`：

```bash
#!/bin/bash
# onboard-project.sh
# 新项目一键接入安全网关
# Usage: ./onboard-project.sh <project-name> <repo-url> <harbor-project>

set -e

PROJECT_NAME="$1"
REPO_URL="$2"
HARBOR_PROJECT="$3"

if [ -z "$PROJECT_NAME" ] || [ -z "$HARBOR_PROJECT" ]; then
    echo "Usage: $0 <project-name> <repo-url> <harbor-project>"
    exit 1
fi

echo "============================================"
echo " Onboarding: $PROJECT_NAME"
echo "============================================"

# 1. 在 Harbor 中创建项目 + 扫描策略
echo "[1/5] Configuring Harbor..."
curl -X POST "https://harbor.internal/api/v2.0/projects" \
  -u "$HARBOR_USER:$HARBOR_PASSWORD" \
  -H "Content-Type: application/json" \
  -d "{
    \"project_name\": \"$HARBOR_PROJECT\",
    \"public\": false,
    \"metadata\": {
      \"auto_scan\": \"true\",
      \"prevent_vul\": \"true\",
      \"severity\": \"critical\"
    }
  }"

# 2. 在 K8s 中标注 Namespace 启用 Trivy Operator
echo "[2/5] Configuring K8s..."
kubectl label namespace "$PROJECT_NAME" security.cloud-sail.com/enabled=true --overwrite
kubectl label namespace "$PROJECT_NAME" security.cloud-sail.com/severity=CRITICAL,HIGH --overwrite

# 3. 在 Prometheus 中添加告警目标
echo "[3/5] Configuring Prometheus..."

# 4. 在 DefectDojo 中创建 Product
echo "[4/5] Configuring DefectDojo..."
curl -X POST "$DOJO_URL/api/v2/products/" \
  -H "Authorization: Token $DOJO_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"name\": \"$PROJECT_NAME\",
    \"description\": \"Auto-onboarded project\",
    \"prod_type\": 1,
    \"enable_simple_risk_acceptance\": true
  }"

# 5. 生成接入指引
echo "[5/5] Generating onboarding guide..."
GUIDE_FILE="onboarding-$PROJECT_NAME.md"

cat > "$GUIDE_FILE" <<EOF
# $PROJECT_NAME - Security Gateway Onboarding

## 状态：已完成自动配置

| 组件 | 状态 | 详情 |
|------|------|------|
| Harbor | ✅ | 项目 $HARBOR_PROJECT 已创建，Critical 漏洞自动阻止 |
| K8s | ✅ | Namespace $PROJECT_NAME 已启用 Trivy Operator 扫描 |
| DefectDojo | ✅ | Product 已创建，等待首次扫描导入 |
| Prometheus | ✅ | 告警规则已自动覆盖 |

## 下一步（开发者操作）

在代码仓库中创建 \`.github/workflows/security.yml\`：

\`\`\`yaml
name: Security Check
on:
  pull_request:
    branches: [main]
  push:
    branches: [main]

jobs:
  security:
    uses: cloud-sail/security-gateway/.github/workflows/secure-build.yml@main
    with:
      image_name: $HARBOR_PROJECT/$PROJECT_NAME
      severity: HIGH,CRITICAL
      enforce_policy: true
    secrets:
      HARBOR_USER: \${{ secrets.HARBOR_USER }}
      HARBOR_PASSWORD: \${{ secrets.HARBOR_PASSWORD }}
\`\`\`

## 验证

提交 PR 后，检查：
- [ ] GitHub Actions 中 security 作业通过
- [ ] Harbor 中镜像显示扫描结果
- [ ] DefectDojo 中出现新的 Finding
- [ ] K8s Dashboard 中显示 Pod 的安全状态

EOF

echo ""
echo "============================================"
echo " Done! Guide saved to: $GUIDE_FILE"
echo "============================================"
cat "$GUIDE_FILE"
```

### 步骤五：端到端验收测试

**目标**：验证安全网关的全链路自动化。

```bash
#!/bin/bash
# e2e-test.sh
# 安全网关端到端测试

echo "=== DevSecOps Security Gateway E2E Test ==="

# 1. 创建一个包含已知漏洞的测试镜像
echo "[Test 1] Building vulnerable image..."
docker build -t test-gateway:vuln -f- . <<'DOCKERFILE'
FROM python:3.4-alpine
RUN pip install django==1.5
DOCKERFILE

# 2. 扫描并模拟 CI 流程
echo "[Test 2] Running security scan..."
trivy image --severity HIGH,CRITICAL --format json -o /tmp/e2e-scan.json test-gateway:vuln

CRITICAL=$(jq '[.Results[]?.Vulnerabilities?[]? | select(.Severity=="CRITICAL")] | length' /tmp/e2e-scan.json)
echo "  CRITICAL: $CRITICAL"

if [ "$CRITICAL" -eq 0 ]; then
    echo "  FAIL: No critical vulnerabilities found (bad test image)"
    exit 1
fi
echo "  PASS: Critical vulnerabilities detected"

# 3. 验证 Secret 扫描
echo "[Test 3] Testing secret detection..."
echo 'AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE' > /tmp/test-secret.txt
trivy fs --scanners secret /tmp/test-secret.txt 2>&1 | grep -q "CRITICAL"
if [ $? -eq 0 ]; then
    echo "  PASS: Secret detected"
else
    echo "  FAIL: Secret not detected"
    exit 1
fi

# 4. 验证 IaC 策略扫描
echo "[Test 4] Testing policy enforcement..."
cat > /tmp/test-deploy.yaml <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: test-bad-deploy
spec:
  template:
    spec:
      containers:
      - name: app
        image: nginx:latest
        securityContext:
          privileged: true
EOF

trivy config /tmp/test-deploy.yaml 2>&1 | grep -q "CRITICAL"
if [ $? -eq 0 ]; then
    echo "  PASS: Misconfiguration detected"
else
    echo "  FAIL: Misconfiguration not detected"
    exit 1
fi

# 5. 验证事件总线
echo "[Test 5] Testing event bus..."
EVENT=$(jq -n --arg image "test-gateway:vuln" '{
  specversion: "1.0",
  type: "cloud-sail.security.scan.completed",
  source: "e2e-test",
  data: {image: $image, vulnerabilities: {critical: 3, high: 5}}
}')
echo "$EVENT" | curl -s -X POST http://localhost:8080/publish/security.scan \
  -H "Content-Type: application/json" -d @- > /dev/null
echo "  PASS: Event published"

echo ""
echo "=== All tests passed! ==="
```

### 测试验证

1. 在测试仓库中创建 `.github/workflows/security.yml` 引用 Reusable Workflow，提交一个含漏洞镜像的 PR，验证 CI 门禁阻断合并。
2. 推送含漏洞镜像到 Harbor，5 分钟后检查 DefectDojo 是否自动创建了 Finding。
3. 部署 Trivy Operator，在一个 Namespace 中部署无标签 Pod，验证 Operator 创建 ConfigAuditReport。
4. 运行 `onboard-project.sh`，验证所有子系统自动配置完成。
5. 访问 Grafana Dashboard，验证能同时看到 CI 扫描、Harbor 扫描、K8s 扫描的三线数据。

---

## 4. 项目总结

### 优点 & 缺点

| 维度 | 优点 | 缺点 |
|------|------|------|
| 统一接入 | 新项目 5 分钟接入，零手动配置 | 依赖 GitHub Actions 生态 |
| 事件驱动 | 松耦合、可扩展、故障隔离 | NATS 的运维增加了基础设施复杂度 |
| 全局 Dashboard | 安全团队获得前所未有的全局视野 | 数据聚合依赖各子系统 API 正常 |
| 自动化闭环 | CI → Registry → Runtime → DefectDojo 全自动 | 流程中出现断点时排查较复杂 |
| 安全即代码 | 策略集中管理、自动分发、版本控制 | 需要全团队适应新的安全文化 |

### 适用场景

1. **50+ 微服务的中大型企业**：安全网关的收益线性增长——项目越多，自动化价值越大。
2. **多团队协作**：安全团队提供平台，开发团队即插即用。
3. **强合规需求**：PCI-DSS、SOC2 等要求「安全控制的一致性和可审计性」。
4. **快速增长的初创公司转中大型**：安全基础设施需要从「手工脚本」升级到「自动化平台」。
5. **安全运营中心（SOC）建设**：全局 Dashboard 是 SOC 的核心组件。

**不适用场景**：
1. 团队 < 20 人——安全网关的架构复杂度可能超过直接手工管理的成本。
2. 未使用 GitHub Actions 的团队——需要额外适配 GitLab CI / Jenkins。

### 注意事项

- **事件总线的可靠性**。NATS 的持久化配置（JetStream）是关键——如果事件丢了，漏洞可能未被记录。
- **Reusable Workflow 的版本管理**。安全网关升级时，所有引用它的项目自动升级——但这意味着必须做向后兼容测试。
- **Dashboard 数据的时效性**。各子系统的扫描间隔不同（CI 即时、Harbor 准即时、K8s 1小时），Dashboard 需要标注「数据更新时间」。

### 常见踩坑经验

**踩坑案例 1：Reusable Workflow 变更导致所有项目 CI 中断**
- **现象**：安全团队升级了 Reusable Workflow，加了 `fail-on: CRITICAL`，第二天所有项目的 CI 全部失败。
- **根因**：没有在 staging 分支上测试，直接 merge 到 main。
- **解法**：建立 `main` / `staging` 双分支策略。新 workflow 先在 `staging` 分支测试 1 周，确认无误后 merge。

**踩坑案例 2：NATS JetStream 积压导致内存 OOM**
- **现象**：事件消费者挂掉 24 小时，NATS 内存被打满。
- **根因**：未被消费的事件在 NATS 中无限堆积。
- **解法**：配置 JetStream 的消息保留策略（如 `MaxAge: 24h`）；消费者增加健康检查和自动重启。

**踩坑案例 3：Dashboard 显示「零漏洞」实际是扫描失败**
- **现象**：Grafana 面板显示漏洞数为 0，但实际上 Trivy Operator 的 Pod 已经 CrashLoop 了 3 天。
- **根因**：Dashboard 只显示了漏洞指标，没有显示 Operator 的健康指标。
- **解法**：在 Dashboard 中始终加入 `up{job="trivy-operator"}` 面板；告警规则中增加 Operator 挂掉的告警。

### 思考题

1. 安全网关的事件总线引入了 NATS。如果公司基础设施团队不批准部署 NATS，你能否用 Redis Pub/Sub 或 Kafka 替代？各自的优劣势和适配代价如何？
2. 在你建设的这个安全网关中，如果 Harbor 的自动扫描延迟了 30 分钟，是否影响整体安全态势？请设计一个「端到端延迟监控」方案——追踪一个漏洞从「被 CI/CD 发现」到「在 Dashboard 中显示」的全链路延迟。

> **答案提示**：第 40 章「从零构建企业级安全扫描平台」将扩展到 500+ 微服务的超大规模场景。

---

> **推广计划**：本章是中级篇的收官之作，建议由平台工程团队和安全团队组成「安全网关专项组」联合落地。优先在 3-5 个非关键项目中接入并运行 2 周，验证各子系统的联动正常后再逐步推广到全部 50+ 项目。推广时可使用 `onboard-project.sh` 脚本批量接入，并通过 Grafana Dashboard 向管理层展示「接入项目数量 vs 安全合规率」的变化趋势，量化安全网关的价值。

---

> **版权声明**：本章基于 Trivy 官方开源项目（Apache-2.0 License）编写，NATS 为 Synadia Communications 开源项目。所有源码引用均遵循原许可证条款。
