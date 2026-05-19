# 第30章：企业级策略即代码（Policy as Code）体系

> 版本：Trivy v0.50+
> 面向人群：平台工程师、安全架构师、Tech Lead

---

## 1. 项目背景

### 业务场景

云帆科技的安全扫描工具链已经相当完善：CI/CD 门禁、Harbor 镜像扫描、Trivy Operator 持续监控、DefectDojo + JIRA 漏洞闭环、供应链签名验证。但 CTO 在一次季度安全评审会上发现了一个更根本的问题：**安全策略是分散的。**

镜像漏洞策略写在 Trivy 的 `.trivyignore` 中，IaC 安全策略写在 Rego 文件中，K8s 准入控制策略写在 Kyverno Policy 中，CI/CD 的门禁阈值写在 GitHub Actions YAML 中。当一个法规或客户要求变更时——比如「所有面向公网的服务不能有 HIGH 及以上未修复漏洞」——团队需要在至少 4 个地方修改配置。

更致命的是，开发团队并不知道这些策略的存在。当 P0 的镜像因为「存在 CRITICAL 漏洞」被 Harbor 阻止拉取时，开发一头雾水：「为什么我的镜像不能拉？谁定的规则？这个规则在哪？」

安全团队也头疼：他们制定了安全基线，但无法确认是否全部生效——哪些规则在 CI 阶段检查、哪些在 Registry 阶段检查、哪些在 Runtime 检查、哪些只是「建议」不阻断部署。

「我们需要一种机制，让安全策略像代码一样——版本管理、可测试、可审计、可跨团队复用。」CTO 在总结时说，「而且，策略本身要对所有开发者可见。不能是某个团队的『隐藏规则』。」

### 痛点放大

**第一，策略分散且不一致。** 同样是「不能有 CRITICAL 漏洞」这条规则，在 CI 中由 Trivy 的 `--exit-code` 控制，在 Harbor 中由扫描策略控制，在 K8s 中由 Kyverno 的 `verifyImages` 控制——同一语义由三套不同系统实现，极难保持一致性。

**第二，策略对开发者不可见。** 开发者在本地 `docker build` 成功，但推到 Harbor 被拒——他不知道 Harbor 有策略，不知道策略是什么，不知道如何检查自己的镜像是否合规。这种「冷暴力」式的阻断极度伤害开发体验。

**第三，策略变更缺乏治理。** 安全团队修改了一条 Rego 规则，但 3 个月后才发现「生产环境根本没部署这个版本」。策略没有版本号、没有发布记录、没有回滚机制——「改了等于没改」。

**第四，强制 vs 建议分不清。** 有些策略是「硬性要求」——不满足直接阻断 CI 门禁；有些是「建议」——不符合就给个 Warning，不影响发版。当前系统中两者的边界模糊，导致开发者对「阻断」特别敏感——动不动就抱怨「安全又在卡我发版」。

**本章的核心目标是：设计企业级『策略即代码』体系——统一策略仓库、分层策略模型（强制/建议）、策略版本管理与 GitOps 发布、以及合规度量的可视化。**

---

## 2. 项目设计

**场景**：云帆科技的「策略即代码」体系设计会，大师（技术负责人）、小白（平台工程师）、小胖（开发代表）正在讨论如何统一策略管理。

---

**小胖**：「我不理解。为什么要把策略分散到那么多地方？把它们全部写在一个地方不就好了？」

**小白**：「因为策略的执行点不同。有些策略必须在『提交代码前』检查（如代码中是否嵌入了 Secret），有些必须在『镜像构建后』检查（如基础镜像的漏洞），有些必须在『部署时』检查（如 Pod 的 securityContext）。你不能指望 K8s 的准入控制器去检查代码里的 Secret——它根本看不到代码。」

**大师**：「技术映射：策略即代码就像城市交通规则。有些规则是『道路交通安全法』（国家级策略，如必须靠右行驶），有些是『本地禁行规定』（市级策略，如学校门口 8-9 点禁止通行），有些是『停车场管理规则』（园区级策略，如外来车辆需要登记）。策略的执行点不同——有的在高速入口检查（CI 门禁），有的在停车场入口检查（Registry），有的在街道上检查（K8s 准入控制）。

PaC 体系要回答的是：谁在什么时间、什么地点、以什么方式执行什么规则——以及如果违反规则怎么办。」

**小胖**：「那具体怎么做？三个云厂商的 PaC 服务都不通用。」

**大师**：**「我们需要自己的 PaC 体系。核心是三个概念：**

### 一、策略分层模型

| 层级 | 范围 | 约束力 | 示例 |
|------|------|--------|------|
| **组织级 (L0)** | 全公司 | 强制，不可覆盖 | 「所有面向公网的服务必须通过 CVSS ≥ 9.0 CRITICAL 漏洞扫描」 |
| **项目级 (L1)** | 单个微服务/项目 | 强制，可收紧不可放松 | 「支付网关的基础镜像必须是公司维护的 Alpine 定制版」 |
| **环境级 (L2)** | 开发/测试/生产 | 环境差异化 | 「生产环境强制 HTTPS，开发环境允许 HTTP」 |

**关键约束**：下级策略只能「收紧」上级策略，不能「放松」。如果组织级规定「不能有 CRITICAL 漏洞」，项目级不能覆盖为「允许有 CRITICAL 漏洞」。这保证了安全基线的底线不被突破。

### 二、统一策略仓库（GitOps 管理）

```
policies/
├── org/
│   ├── image-compliance.rego          # 组织级镜像策略
│   ├── iac-baseline.rego              # 组织级 IaC 策略
│   └── secret-zero-tolerance.rego     # 组织级 Secret 策略
├── project/
│   ├── payment-gateway/
│   │   ├── base-image.rego            # 支付网关专属策略
│   │   └── network-policy.rego        # 网络隔离策略
│   └── user-service/
│       └── data-encryption.rego       # 用户数据加密策略
├── env/
│   ├── production/
│   │   └── hardening.rego             # 生产环境强化策略
│   └── staging/
│       └── relaxed.rego               # 预发布环境宽松策略
└── policy-config.yaml                 # 策略元数据：级别、强制/建议、责任人
```

### 三、策略的强制 vs 建议

```yaml
# policy-config.yaml
policies:
  - id: IMG-001
    name: "No CRITICAL vulnerabilities"
    level: org
    enforcement: block           # 强制：阻断 CI / 阻止部署
    applies_to: [ci, registry, k8s]

  - id: IAC-002
    name: "Require cost-center label"
    level: org
    enforcement: block
    applies_to: [ci, k8s]

  - id: IMG-003
    name: "Prefer distroless base image"
    level: project
    enforcement: warn            # 建议：不阻断，只告警
    applies_to: [ci]
```

`block` 策略不满足时：CI 门禁红色（阻止合并）、Harbor 阻止拉取、K8s 准入拒绝。
`warn` 策略不满足时：生成 Warning，记录在 `policy-compliance.json` 中，但不阻断流程。」

**小胖**：「那 CI/CD、Harbor、K8s 怎么知道该执行哪些策略呢？」

**小白**：「这是策略分发的核心问题。我们用 GitOps 方式：

1. 策略仓库的主分支是『单一事实来源』。
2. 每个执行点（CI Runner、Harbor、K8s Cluster）通过 Git 同步或 API 拉取最新策略。
3. 执行点读取 `policy-config.yaml`，根据自己的角色（CI / Registry / K8s）筛选适用的策略。
4. 执行后，将合规结果推送到『合规 Dashboard』。

大致的架构图：

```
   ┌──────────────────────────────────┐
   │     Policy Repository (Git)      │
   │  policies/* + policy-config.yaml │
   └──────────────┬───────────────────┘
                  │ Git Push / Pull
      ┌───────────┼───────────┐
      │           │           │
      ▼           ▼           ▼
 ┌─────────┐ ┌─────────┐ ┌─────────┐
 │ CI Gate │ │ Registry│ │ K8s     │
 │ (Trivy) │ │ (Harbor)│ │ (Kyverno│
 └────┬────┘ └────┬────┘ └────┬────┘
      │           │           │
      └───────────┼───────────┘
                  │
      ┌───────────▼───────────┐
      │  Compliance Dashboard │
      │  (Grafana / Web UI)   │
      └───────────────────────┘
```

每个执行点执行完策略后，向 Compliance Dashboard 上报结果。Dashboard 汇总全公司的策略合规率、违规 Top 10 项目、合规趋势。」

---

## 3. 项目实战

### 环境准备

- **GitHub/GitLab 仓库**：策略仓库 `cloud-sail-policies`
- **Trivy**：v0.50+（用于执行 Rego 策略）
- **OPA/Conftest**：v0.50+（用于策略测试和 CI 集成）
- **Grafana**：用于合规 Dashboard

### 步骤一：创建统一策略仓库

**目标**：建立策略即代码的单一事实来源。

创建策略目录结构：

```bash
mkdir -p policies/{org,project/{payment-gateway,user-service},env/{production,staging}}
mkdir -p tests
```

创建 `policies/policy-config.yaml`：

```yaml
# 策略元数据配置
version: "1.2.0"
last_updated: "2024-03-15"

policies:
  # ===== 组织级 - 镜像安全 =====
  - id: IMG-001
    name: "No CRITICAL vulnerabilities"
    description: "All container images must have zero CRITICAL vulnerabilities"
    level: org
    category: image
    enforcement: block
    applies_to: [ci, registry]
    rego_file: org/image-critical-zero.rego
    owner: security-team
    review_date: "2024-06-15"

  - id: IMG-002
    name: "Image must be from approved registries"
    description: "Images must originate from harbor.internal.example.com"
    level: org
    category: image
    enforcement: block
    applies_to: [ci, k8s]
    rego_file: org/image-registry-whitelist.rego
    owner: platform-team

  # ===== 组织级 - IaC =====
  - id: IAC-001
    name: "Containers must not run as root"
    description: "All Pods must have securityContext.runAsNonRoot: true"
    level: org
    category: iac
    enforcement: block
    applies_to: [ci, k8s]
    rego_file: org/iac-non-root.rego
    owner: platform-team

  - id: IAC-002
    name: "Resources must have cost-center and owner labels"
    description: "All K8s resources must have cost-center and owner labels"
    level: org
    category: iac
    enforcement: block
    applies_to: [ci, k8s]
    rego_file: org/iac-mandatory-labels.rego
    owner: finance-team

  # ===== 组织级 - Secret =====
  - id: SEC-001
    name: "No secrets in code or images"
    description: "Zero tolerance for secrets (API keys, tokens, passwords) in any artifact"
    level: org
    category: secret
    enforcement: block
    applies_to: [ci, registry, k8s]
    owner: security-team

  # ===== 项目级 =====
  - id: PAY-001
    name: "Payment gateway must use PCI-DSS certified base image"
    level: project
    applies_to_project: payment-gateway
    category: image
    enforcement: block
    applies_to: [ci, registry]
    rego_file: project/payment-gateway/pci-base-image.rego
    owner: payment-team

  # ===== 建议型策略（warn，不阻断）=====
  - id: IMG-003
    name: "Prefer distroless or slim base images"
    description: "Recommend using distroless or slim images to reduce attack surface"
    level: org
    category: image
    enforcement: warn
    applies_to: [ci]
    rego_file: org/image-distroless-suggestion.rego
    owner: platform-team
```

### 步骤二：编写核心 Rego 策略

**目标**：编写策略仓库中的核心规则。

创建 `policies/org/iac-mandatory-labels.rego`：

```rego
package trivy

# 检查 K8s 资源是否有强制标签
deny[msg] {
    # 只检查 Deployment、StatefulSet、DaemonSet
    input.kind in {"Deployment", "StatefulSet", "DaemonSet"}

    # 检查 Pod template 的 labels
    labels := object.get(input, ["spec", "template", "metadata", "labels"], {})

    required_labels := {"cost-center", "owner", "app"}

    missing := {label | label := required_labels[_]; not labels[label]}

    count(missing) > 0

    msg := sprintf("%s/%s is missing required labels: %v",
        [input.kind, input.metadata.name, missing])
}
```

创建 `policies/org/iac-non-root.rego`：

```rego
package trivy

deny[msg] {
    input.kind in {"Deployment", "StatefulSet", "DaemonSet", "Pod"}
    containers := object.get(input, ["spec", "template", "spec", "containers"], [input.spec.containers])

    container := containers[_]
    securityContext := object.get(container, "securityContext", {})

    # 检查 runAsNonRoot 是否为 true
    not securityContext.runAsNonRoot == true

    msg := sprintf("%s/%s: container '%s' does not specify runAsNonRoot: true",
        [input.kind, input.metadata.name, container.name])
}
```

创建 `tests/test-mandatory-labels.rego`：

```rego
package trivy

test_deployment_missing_labels {
    count(deny) > 0 with input as {
        "kind": "Deployment",
        "apiVersion": "apps/v1",
        "metadata": {"name": "test-deploy", "namespace": "prod"},
        "spec": {
            "template": {
                "metadata": {"labels": {"app": "test"}},
                "spec": {"containers": [{"name": "main", "image": "test:1.0"}]}
            }
        }
    }
}

test_deployment_all_labels_present {
    count(deny) == 0 with input as {
        "kind": "Deployment",
        "metadata": {"name": "test-deploy"},
        "spec": {
            "template": {
                "metadata": {"labels": {"cost-center": "CC001", "owner": "team-a", "app": "test"}},
                "spec": {"containers": [{"name": "main", "image": "test:1.0"}]}
            }
        }
    }
}

test_service_skipped {
    # Service 资源不检查 labels 策略
    count(deny) == 0 with input as {
        "kind": "Service",
        "metadata": {"name": "test-svc"},
        "spec": {"ports": [{"port": 80}]}
    }
}
```

运行策略测试：

```bash
# OPA 测试
opa test policies/org/ tests/ -v

# 输出：
# PASS: 3/3
# test_deployment_missing_labels: PASS
# test_deployment_all_labels_present: PASS
# test_service_skipped: PASS
```

### 步骤三：策略 GitOps 分发机制

**目标**：各执行点自动同步最新策略。

创建 `policy-syncer.sh`（部署在每个执行点）：

```bash
#!/bin/bash
# policy-syncer.sh
# 从 Git 仓库拉取最新策略到本地

POLICY_REPO="${POLICY_REPO:-git@github.com:cloud-sail/policies.git}"
POLICY_DIR="/etc/cloud-sail/policies"
POLICY_VERSION_FILE="$POLICY_DIR/.version"

# 如果本地不存在策略目录，clone；否则 pull
if [ ! -d "$POLICY_DIR/.git" ]; then
    git clone "$POLICY_REPO" "$POLICY_DIR"
else
    cd "$POLICY_DIR"
    git fetch origin
    LOCAL_HASH=$(git rev-parse HEAD)
    REMOTE_HASH=$(git rev-parse origin/main)

    if [ "$LOCAL_HASH" != "$REMOTE_HASH" ]; then
        echo "Policy update available: $LOCAL_HASH -> $REMOTE_HASH"
        git pull origin main
        echo "$(date -Iseconds) Updated to $REMOTE_HASH" >> "$POLICY_VERSION_FILE"
    else
        echo "Policies are up-to-date."
    fi
fi
```

在 CI/CD Runner 中集成策略同步：

```yaml
# .github/workflows/security-gate.yml
jobs:
  policy-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Sync security policies
        run: |
          bash policy-syncer.sh
          echo "Policies version: $(cat /etc/cloud-sail/policies/.version)"

      - name: Run policy checks (blocking)
        run: |
          # 读取 policy-config.yaml，获取所有 enforcement=block 的策略
          cd /etc/cloud-sail/policies
          
          # 执行 image 策略
          trivy image \
            --config-policy ./ \
            --severity HIGH,CRITICAL \
            --exit-code 1 \
            ${{ env.IMAGE_TAG }}

          # 执行 IaC 策略（对 K8s manifests 目录）
          trivy config \
            --config-policy ./policies/org \
            --severity HIGH,CRITICAL \
            --exit-code 1 \
            ./k8s/

      - name: Run warning checks (non-blocking)
        continue-on-error: true
        run: |
          # 只执行 enforcement=warn 的策略，失败不阻断
          trivy config \
            --config-policy ./policies/org/warn-only/ \
            --severity MEDIUM \
            --format json \
            --output policy-warnings.json \
            ./k8s/

      - name: Upload compliance report
        uses: actions/upload-artifact@v4
        with:
          name: policy-compliance
          path: policy-warnings.json
```

### 步骤四：合规 Dashboard 与度量

**目标**：汇总所有项目的策略合规情况，输出可视化报告。

创建 `compliance-collector.py`（部署在中心服务器，接收各执行点上报的合规数据）：

```python
#!/usr/bin/env python3
"""
compliance-collector.py
接收各执行点的策略合规结果，汇总到 PostgreSQL 并暴露 Prometheus 指标
"""

import json
import os
from datetime import datetime

import psycopg2
from flask import Flask, request, jsonify

app = Flask(__name__)
DATABASE_URL = os.environ["DATABASE_URL"]


def init_db():
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS policy_results (
            id BIGSERIAL PRIMARY KEY,
            check_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            project VARCHAR(200) NOT NULL,
            policy_id VARCHAR(50) NOT NULL,
            policy_name VARCHAR(300),
            enforcement VARCHAR(20),
            status VARCHAR(20),       -- pass / fail / warn
            violations INT DEFAULT 0,
            executor VARCHAR(50),     -- ci / registry / k8s
            details JSONB
        );
        CREATE INDEX IF NOT EXISTS idx_policy_results_project 
            ON policy_results(project);
        CREATE INDEX IF NOT EXISTS idx_policy_results_check_time 
            ON policy_results(check_time DESC);
        CREATE INDEX IF NOT EXISTS idx_policy_results_policy 
            ON policy_results(policy_id);
    """)
    conn.commit()
    cur.close()
    conn.close()


@app.route("/api/v1/compliance/report", methods=["POST"])
def receive_report():
    """接收来自 CI/Registry/K8s 的策略合规报告"""
    data = request.json

    project = data.get("project")
    executor = data.get("executor")
    results = data.get("results", [])
    check_time = data.get("timestamp", datetime.now().isoformat())

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    for result in results:
        cur.execute("""
            INSERT INTO policy_results 
                (check_time, project, policy_id, policy_name, 
                 enforcement, status, violations, executor, details)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            check_time, project,
            result.get("policy_id"),
            result.get("policy_name"),
            result.get("enforcement"),
            result.get("status"),       # pass / fail / warn
            result.get("violations", 0),
            executor,
            json.dumps(result.get("details", {})),
        ))

    conn.commit()
    cur.close()
    conn.close()

    return jsonify({"status": "ok", "received": len(results)})


@app.route("/metrics", methods=["GET"])
def prometheus_metrics():
    """暴露 Prometheus 可抓取的合规指标"""
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    # 按项目和策略的合规统计
    cur.execute("""
        SELECT 
            project, policy_id, status, COUNT(*) as cnt
        FROM policy_results
        WHERE check_time > NOW() - INTERVAL '7 days'
        GROUP BY project, policy_id, status
    """)

    lines = []
    for project, policy_id, status, cnt in cur.fetchall():
        metric_name = "trivy_policy_compliance"
        labels = f'project="{project}",policy_id="{policy_id}",status="{status}"'
        lines.append(f"{metric_name}{{{labels}}} {cnt}")

    # 整体合规率
    cur.execute("""
        SELECT 
            ROUND(
                100.0 * SUM(CASE WHEN status = 'pass' THEN 1 ELSE 0 END) / COUNT(*),
                2
            ) as compliance_rate
        FROM policy_results
        WHERE check_time > NOW() - INTERVAL '7 days'
    """)
    rate = cur.fetchone()[0] or 0
    lines.append(f"trivy_overall_compliance_rate {rate}")

    cur.close()
    conn.close()

    return "\n".join(lines) + "\n", 200, {"Content-Type": "text/plain"}


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=9091)
```

Grafana 面板 PromQL 查询示例：

```
# 各项目策略合规率
100 * sum(trivy_policy_compliance{status="pass"}) by (project) 
/ sum(trivy_policy_compliance) by (project)

# 违规最多的策略 Top 5
topk(5, sum(trivy_policy_compliance{status="fail"}) by (policy_id))

# 合规率趋势（7 天滑动窗口）
avg_over_time(trivy_overall_compliance_rate[7d])
```

### 步骤五：策略版本发布与回滚

**目标**：策略变更可追踪、可回滚。

创建 `policy-release.sh`：

```bash
#!/bin/bash
# policy-release.sh
# 策略版本发布与回滚

set -e

ACTION="${1:-release}"
VERSION="${2:-$(date +%Y%m%d-%H%M%S)}"
POLICY_REPO="git@github.com:cloud-sail/policies.git"
RELEASE_BRANCH="releases/${VERSION}"

case $ACTION in
  release)
    echo "=== Releasing policy version: $VERSION ==="
    
    cd /etc/cloud-sail/policies
    
    # 获取当前版本号
    CURRENT_VERSION=$(yq '.version' policy-config.yaml)
    echo "Current version: $CURRENT_VERSION"
    
    # 创建 release 分支
    git checkout -b "$RELEASE_BRANCH"
    
    # 更新版本号
    yq -i ".version = \"$VERSION\"" policy-config.yaml
    yq -i ".last_updated = \"$(date -I)\"" policy-config.yaml
    
    # 运行完整测试
    echo "Running policy tests..."
    opa test policies/org/ tests/ -v
    
    # 提交并打 tag
    git add .
    git commit -m "Release policy version $VERSION"
    git tag -a "v$VERSION" -m "Policy release $VERSION"
    git push origin "$RELEASE_BRANCH" --tags
    
    echo "Policy v$VERSION released successfully."
    ;;
  
  rollback)
    echo "=== Rolling back to version: $VERSION ==="
    
    cd /etc/cloud-sail/policies
    git checkout "v$VERSION"
    
    echo "Rolled back to policy v$VERSION."
    echo "Note: execution points will pick up this change on next sync (within 5 minutes)."
    ;;
  
  list)
    cd /etc/cloud-sail/policies
    echo "Available policy versions:"
    git tag -l "v*" --sort=-version:refname | head -20
    ;;
  
  *)
    echo "Usage: $0 {release|rollback|list} [version]"
    exit 1
    ;;
esac
```

### 测试验证

1. 创建策略仓库，推送 `policies/` 到 Git。用 `policy-syncer.sh` 同步到本地，验证策略文件自动更新。
2. 编写 Rego 策略测试文件（`tests/`），运行 `opa test` 确认所有通过。
3. 在 GitHub Actions 中集成策略检查：对 K8s YAML 执行 `trivy config --config-policy`，验证 `enforcement=block` 的策略正确阻断 CI。
4. 部署 `compliance-collector.py`，用 `curl` 模拟上报合规数据，通过 Grafana 查看 Dashboard。
5. 执行 `policy-release.sh release 1.3.0`，验证版本 tag 创建。再用 `policy-release.sh rollback 1.2.0` 回滚，验证 Git 切换正确。

---

## 4. 项目总结

### 优点 & 缺点

| 维度 | 优点 | 缺点 |
|------|------|------|
| Git 统一管理 | 版本控制、变更审计、协作透明 | 需要所有执行点支持 Git 同步 |
| 分层策略 | 组织底线 + 项目灵活，平衡治理与赋权 | 层级过多可能导致策略碎片化 |
| 强制/建议区分 | 减少开发者的摩擦和抱怨 | 建议策略的效果取决于文化而非工具 |
| 合规 Dashboard | 量化安全治理效果 | 需要各执行点积极配合上报数据 |
| 策略测试框架 | OPA 原生测试，质量有保障 | 测试用例编写需要投入时间 |

### 适用场景

1. **多项目/多团队的中大型企业**：需要统一的安全基线但各项目有差异化需求。
2. **有 GitOps 文化的组织**：团队已习惯用 Git 管理一切，策略即代码是自然的延伸。
3. **合规要求严格**（PCI-DSS / SOC2 / HIPAA）：策略的版本化变更是审计的宝贵证据。
4. **平台工程团队建设**：将安全策略封装为平台能力，开发者无需学习安全细节。
5. **安全团队与开发团队的桥梁**：策略仓库成为双方对安全基线的「共同理解文档」。

**不适用场景**：
1. 团队 < 20 人——统一策略仓库的管理成本高于直接在各执行点配置。
2. 没有 CI/CD 和 Git 基础设施的团队——GitOps 依赖这些基础能力。

### 注意事项

- **策略仓库权限控制**。不是所有人都能修改组织级策略。建议：组织级策略需安全团队 Code Review 批准（通过 GitHub CODEOWNERS）。
- **策略同步频率**。不要过于频繁（如每秒拉取），可能造成 Git 仓库压力。5-15 分钟的同步间隔合理。
- **策略的「适用范围」要明确**。`applies_to: [ci, registry, k8s]` 是核心元数据，执行点据此过滤策略。缺失则策略可能被错误执行或遗漏。
- **策略变更的通知机制**。策略更新后，通过 Slack/钉钉自动通知所有团队的 Tech Lead ——「策略 v1.3.0 已发布，新增 2 条 block 策略，请检查你们的项目是否受影响」。

### 常见踩坑经验

**踩坑案例 1：Rego 策略在生产环境表现与测试不一致**
- **现象**：OPA test 全部通过，但在 CI 中执行时某些边界条件触发了意外行为。
- **根因**：测试用例只覆盖了 Deployment，但没有覆盖 StatefulSet / DaemonSet 的 spec 结构差异。
- **解法**：在测试中增加多种资源类型的边界用例；在 CI 中先跑 `warn` 模式观察一周，确认无误后升级为 `block`。

**踩坑案例 2：策略同步导致 CI 中断**
- **现象**：某次策略更新后，5 个项目的 CI 同时失败——策略有问题。
- **根因**：策略直接在 main 分支修改，没有在 staging 环境验证。
- **解法**：建立策略变更的 PR → Review → Staging Test → Release 的完整流程。PR 合并到 main 后自动在 1-2 个项目上测试，确认无误后才触发全量同步。

**踩坑案例 3：合规 Dashboard 数据不一致**
- **现象**：Dashboard 显示项目 A 合规率 100%，但实际上 Harbor 还在阻止它的镜像拉取。
- **根因**：项目 A 的 CI 上报了数据，但 Harbor 执行点因为网络问题没上报。Dashboard 只反映了部分数据。
- **解法**：每个执行点上报时附带 `executor` 标签，Dashboard 按 executor 分别展示。如果某个 executor 超过 2 小时无上报，触发基础设施告警。

### 思考题

1. 如果你的公司有 100 个项目，每个项目有自己的项目级策略，如何设计一个「策略继承与覆盖」机制——在保证组织底线不被突破的前提下，允许项目自行收紧策略？请考虑策略冲突的检测和解决方案。

2. 合规 Dashboard 显示「公司整体合规率 85%」。但不同消费者可能关心不同的数据——CTO 关心趋势，安全团队关心违规项，项目经理关心自己项目的状态。请设计一个「合规数据多视角」方案，为不同角色提供定制化的合规视图。

> **答案提示**：第 40 章「从零构建企业级安全扫描平台」将讨论企业级安全平台的策略引擎设计和多租户管理。

---

> **推广计划**：本章是中级篇的最后一章，承接第 22 章「自定义 Rego 策略」和第 23 章「高级过滤与报告」，为第 31 章的「中级篇综合实战」做铺垫。建议由安全团队和平台工程团队联合推进，分三阶段落地：第一阶段（1-2 周），建立策略仓库并编写 10 条核心组织级策略；第二阶段（3-4 周），在 CI/CD 和 K8s 中集成策略执行点，跑通 GitOps 自动同步；第三阶段（1-2 月），构建合规 Dashboard 并推动所有项目接入。策略即代码不是一次性项目，而是持续维护的「安全治理基础设施」，需要设立「策略维护负责人」角色，每季度 Review 策略的有效性和必要性。

---

> **版权声明**：本章基于 Trivy 官方开源项目（Apache-2.0 License）编写，OPA/Rego 为 Styra 公司开源项目。所有源码引用均遵循原许可证条款。
