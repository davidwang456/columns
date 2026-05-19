# 第26章：多云环境资产扫描（AWS / Azure / GCP）

> 版本：Trivy v0.50+
> 面向人群：云架构师、安全工程师、DevSecOps

---

## 1. 项目背景

### 业务场景

云帆科技的业务从单一的云厂商发展到多云架构——核心交易系统在 AWS（us-east-1 和 ap-southeast-1），数据分析平台在 GCP（us-central1），办公协同系统在 Azure（East Asia）。安全负责人老李在一次季度审计复盘时发现了一个令人不安的事实：**他的团队有完整的容器镜像扫描、K8s 集群扫描，但对云基础设施本身的扫描几乎是零。**

这个隐患的暴露始于一次不愉快的经历。财务团队在 AWS 上创建了一个公开读写的 S3 Bucket 用于存储客户账单，被第三方安全公司扫描到了。AWS Trusted Advisor 也报了警，但因为没有自动化扫描机制，等安全团队注意到时，这个 Bucket 已经暴露了 17 天。

与此同时，Azure 侧有个数据库实例（Azure Database for PostgreSQL）的「强制 SSL 连接」被运维不小心关掉了。GCP 侧有 3 台 Compute Engine 实例的 OS 登录未启用，依赖密码认证。这些配置问题如果被 Trivy 的 `misconfig` 扫描器覆盖，完全可以提前发现——但前提是要告诉 Trivy「去哪里扫」。

更复杂的是，三个云厂商的认证方式各不相同——AWS 用 IAM Role + Access Key，Azure 用 Service Principal + Client Secret，GCP 用 Service Account Key。团队的扫描脚本散落在各处，有的用 AWS CLI 拼凑，有的用 Python SDK 手写，没有统一的入口。

### 痛点放大

**第一，云资产不可见。** 容器镜像和 K8s 集群是「我们主动创建的」，但云资源（S3、RDS、Security Group、VPC）可能是「任何人按需创建的」。没有周期性的自动化盘点，安全团队根本不知道公司有多少云资源、哪些有配置风险。

**第二，多云认证碎片化。** 每个云厂商都有自己的一套认证机制，学习成本高、管理困难。如果为每个云写一套扫描脚本，维护负担呈线性增长。

**第三，CSPM（Cloud Security Posture Management）工具要么太贵，要么太弱。** AWS Inspector / Azure Defender / GCP Security Command Center 各有侧重，但都不支持跨云统一视图。第三方 CSPM（如 Prisma Cloud、Wiz）功能强大但价格动辄百万级，对中小企业不友好。

**第四，配置漂移难以追踪。** 今天「安全」的配置，明天可能因为某个运维操作变「不安全」。没有持续的配置扫描，安全状态随时间推移必然恶化。

**本章的核心目标是：用 Trivy 的 `aws` / `azure` / `gcp` 子命令，建立多云环境的统一安全扫描能力，实现云资产的自动化发现与配置合规检查。**

---

## 2. 项目设计

**场景**：云帆科技的多云安全方案讨论会，老李（安全负责人）、小胖（开发）、大师在评估方案。

---

**小胖**：「云厂商不是都自带了安全扫描服务吗？AWS 有 Inspector，Azure 有 Defender，直接用不就行了？」

**小白**：「问题在于没有一个工具能横跨 AWS、Azure、GCP 给出统一视图。你用 Inspector 看 AWS 的 EC2 漏洞，用 Defender 看 Azure 的 SQL 配置，用 Security Command Center 看 GCP 的 IAM——三个不同的 Dashboard、三种不同的告警格式、三套不同的术语。如果公司被要求出一个『全公司云安全月度报告』，你得从三个控制台手工拼数据。」

**大师**：「技术映射：Trivy 的云扫描就像『通用电源适配器』。AWS Inspector 是『美规插头』——只能插美规插座；Azure Defender 是『欧规插头』——只能插欧规插座。而 Trivy 同时支持 AWS、Azure、GCP 的认证，输出的报告格式完全统一（都是 JSON/Table/SARIF），你只需要学一套命令、看一种报告。」

**小胖**：「那 Trivy 的云扫描到底扫了什么？和 `trivy image` 有什么区别？」

**小白**：「区别很大。`trivy image` 扫的是容器镜像内部的文件系统和依赖包。`trivy aws` 扫的是 AWS 云上的基础设施配置。以 AWS 为例，它能扫描：

- **S3**：Bucket 是否公开、是否启用加密、是否启用日志。
- **EC2**：安全组是否开放 0.0.0.0/0、实例的 AMI 漏洞、是否使用 IMDSv2。
- **RDS**：是否启用加密、是否启用自动备份、multi-AZ 状态。
- **IAM**：访问密钥是否过期、MFA 是否启用、策略是否过于宽松。
- **EKS**：集群的配置合规性、Node Group 的安全设置。
- **Lambda**：运行环境和超时配置。

Azure 和 GCP 也有对应的资源扫描覆盖。」

**大师**：「但这里有个关键区别。Trivy 的云扫描重『配置错误（misconfiguration）』，而不是『漏洞（vulnerability）』。它检查的不是『EC2 上装的 OpenSSL 有没有 CVE』——那个是 `trivy vm` 干的——而是『EC2 的安全组是不是对公网开了 22 端口』。两者的关注点完全不同。」

**小胖**：「那认证怎么办？三个云厂商的认证机制不一样啊。」

**大师**：「Trivy 遵循各云厂商的标准认证方法，不需要特殊适配：

- **AWS**：支持环境变量（`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`）、IAM Role（EC2/ECS 上自动获取）、`~/.aws/credentials` 文件。推荐用 IAM Role + 临时凭证（STS）。
- **Azure**：支持环境变量（AZURE_TENANT_ID / AZURE_CLIENT_ID / AZURE_CLIENT_SECRET）、`az login` 的缓存、Managed Identity（在 Azure VM 上运行时自动获取）。
- **GCP**：通过 `GOOGLE_APPLICATION_CREDENTIALS` 指定 Service Account Key 文件，或使用 GCE 默认服务账号。

简单说：按各云厂商的标准方式配认证，Trivy 自动识别，不需要额外配置。」

**小白**：「还有一个重要的安全设计：最小权限原则。不要给 Trivy 一个 `AdministratorAccess` 的角色——它只需要只读权限。以 AWS 为例，最小权限是：

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "ec2:Describe*",
                "s3:Get*",
                "s3:List*",
                "rds:Describe*",
                "iam:Get*",
                "iam:List*",
                "eks:Describe*",
                "lambda:Get*",
                "lambda:List*",
                "cloudtrail:Describe*",
                "cloudtrail:Get*"
            ],
            "Resource": "*"
        }
    ]
}
```

都是 `Describe*`、`Get*`、`List*` 这类只读操作。不存在『操作漏洞』——Trivy 不会帮你去修复，只会告诉你问题在哪。」

---

## 3. 项目实战

### 环境准备

- **Trivy**：v0.50+，确保包含云扫描能力
- **AWS IAM 角色/用户**：具有上述最小权限
- **Azure Service Principal**：具有 Reader 角色
- **GCP Service Account**：具有 Viewer 角色

### 步骤一：AWS 环境扫描

**目标**：配置 AWS 认证，扫描一个 AWS 账号的 EC2、S3、RDS、IAM 配置。

```bash
# 方法一：使用 Access Key（临时测试）
export AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE
export AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
export AWS_DEFAULT_REGION=us-east-1

# 方法二：使用 AWS Profile（推荐生产环境）
export AWS_PROFILE=cloud-sail-security

# 扫描整个 AWS 账号（所有支持的服务）
trivy aws \
  --severity HIGH,CRITICAL \
  --region us-east-1 \
  --region ap-southeast-1 \
  --format json \
  --output aws-scan-result.json

# 扫描特定服务
trivy aws --service s3    # 仅扫描 S3
trivy aws --service ec2   # 仅扫描 EC2
trivy aws --service rds   # 仅扫描 RDS

# 查看扫描报告（table 格式，默认输出）
trivy aws --region us-east-1 --severity HIGH,CRITICAL
```

**输出示例**：

```
trivy aws (ap-southeast-1)

S3 (cloud-sail-logs-bucket)
├───────────────────────┬───────────────────────┬──────────┬───────────────────┐
│       Misconf ID      │        Check          │ Severity │     Message       │
├───────────────────────┼───────────────────────┼──────────┼───────────────────┤
│ AVD-AWS-0088          │ Bucket logging        │   HIGH   │ Bucket logging    │
│                       │ disabled              │          │ is not enabled    │
├───────────────────────┼───────────────────────┼──────────┼───────────────────┤
│ AVD-AWS-0089          │ Bucket public access  │ CRITICAL │ Bucket allows     │
│                       │ not blocked           │          │ public access     │
└───────────────────────┴───────────────────────┴──────────┴───────────────────┘

EC2 (i-0a1b2c3d4e5f67890)
├───────────────────────┬───────────────────────┬──────────┬───────────────────┤
│ AVD-AWS-0107          │ IMDSv2 not required   │   HIGH   │ Instance metadata │
│                       │                       │          │ v1 is enabled     │
└───────────────────────┴───────────────────────┴──────────┴───────────────────┘

RDS (cloud-sail-payment-db)
├───────────────────────┬───────────────────────┬──────────┬───────────────────┤
│ AVD-AWS-0080          │ Encryption not        │   HIGH   │ Storage encryption│
│                       │ enabled               │          │ is not enabled    │
└───────────────────────┴───────────────────────┴──────────┴───────────────────┘
```

```bash
# 用 jq 聚合各服务的风险数量
jq '[.Results[] | {Service: .Target, Misconfigs: [.Misconfigurations[] | select(.Severity=="CRITICAL" or .Severity=="HIGH")] | length}]' aws-scan-result.json
```

### 步骤二：Azure 环境扫描

**目标**：配置 Azure 认证，扫描 Azure 资源的配置安全。

```bash
# 方法一：使用环境变量
export AZURE_TENANT_ID="00000000-0000-0000-0000-000000000000"
export AZURE_CLIENT_ID="00000000-0000-0000-0000-000000000000"
export AZURE_CLIENT_SECRET="your-client-secret"

# 方法二：使用 az login（交互式）
az login
trivy azure

# 扫描 Azure 资源（指定订阅）
trivy azure \
  --subscription-id "your-subscription-id" \
  --severity HIGH,CRITICAL \
  --format json \
  --output azure-scan.json

# Azure 支持的服务范围
trivy azure --service storage    # Storage Account
trivy azure --service database   # SQL Database / Cosmos DB
trivy azure --service compute    # Virtual Machines
trivy azure --service network    # Network Security Groups
```

> **坑点**：Azure 云扫描要求 Service Principal 至少具有目标订阅的 `Reader` 角色。创建 SP 时注意不要给 `Contributor` 以上的权限。

### 步骤三：GCP 环境扫描

**目标**：配置 GCP 认证，扫描 GCP 项目的配置安全。

```bash
# 配置 GCP 认证
export GOOGLE_APPLICATION_CREDENTIALS="/path/to/service-account-key.json"

# 激活 Service Account
gcloud auth activate-service-account \
  --key-file=$GOOGLE_APPLICATION_CREDENTIALS

# 扫描 GCP 项目
trivy gcp \
  --project-id "your-gcp-project-id" \
  --severity HIGH,CRITICAL \
  --format table

# 扫描特定区域
trivy gcp --region us-central1
```

### 步骤四：编写多云统一扫描脚本

**目标**：一个脚本同时扫描 AWS、Azure、GCP，输出统一格式报告。

创建 `multi-cloud-scan.sh`：

```bash
#!/bin/bash
set -e

OUTPUT_DIR="/var/reports/multi-cloud/$(date +%Y%m%d_%H%M%S)"
mkdir -p "$OUTPUT_DIR"

REPORT_SUMMARY="$OUTPUT_DIR/summary.md"

echo "# 多云安全扫描报告" > "$REPORT_SUMMARY"
echo "**扫描时间**: $(date '+%Y-%m-%d %H:%M:%S')" >> "$REPORT_SUMMARY"
echo "" >> "$REPORT_SUMMARY"

# ===== AWS =====
if [ -n "$AWS_ACCESS_KEY_ID" ] || [ -n "$AWS_PROFILE" ]; then
    echo "### AWS 扫描" >> "$REPORT_SUMMARY"
    echo "" >> "$REPORT_SUMMARY"

    aws_regions=("us-east-1" "ap-southeast-1" "eu-west-1")
    aws_total_critical=0
    aws_total_high=0

    for region in "${aws_regions[@]}"; do
        echo ">>> Scanning AWS region: $region"

        trivy aws \
            --region "$region" \
            --severity HIGH,CRITICAL \
            --format json \
            --output "$OUTPUT_DIR/aws-${region}.json" \
            2>&1 | tail -1

        if [ -f "$OUTPUT_DIR/aws-${region}.json" ]; then
            critical=$(jq '[.Results[]?.Misconfigurations[]? | select(.Severity=="CRITICAL")] | length' "$OUTPUT_DIR/aws-${region}.json" 2>/dev/null || echo 0)
            high=$(jq '[.Results[]?.Misconfigurations[]? | select(.Severity=="HIGH")] | length' "$OUTPUT_DIR/aws-${region}.json" 2>/dev/null || echo 0)

            aws_total_critical=$((aws_total_critical + critical))
            aws_total_high=$((aws_total_high + high))

            echo "- **$region**: CRITICAL=$critical, HIGH=$high" >> "$REPORT_SUMMARY"
        fi
    done

    echo "- **总计**: CRITICAL=$aws_total_critical, HIGH=$aws_total_high" >> "$REPORT_SUMMARY"
    echo "" >> "$REPORT_SUMMARY"
else
    echo ">>> AWS: Skipped (no credentials)" >> "$REPORT_SUMMARY"
fi

# ===== Azure =====
if [ -n "$AZURE_CLIENT_ID" ]; then
    echo "### Azure 扫描" >> "$REPORT_SUMMARY"
    echo "" >> "$REPORT_SUMMARY"
    echo ">>> Scanning Azure..."

    trivy azure \
        --severity HIGH,CRITICAL \
        --format json \
        --output "$OUTPUT_DIR/azure.json" \
        2>&1 | tail -1

    if [ -f "$OUTPUT_DIR/azure.json" ]; then
        azure_critical=$(jq '[.Results[]?.Misconfigurations[]? | select(.Severity=="CRITICAL")] | length' "$OUTPUT_DIR/azure.json")
        azure_high=$(jq '[.Results[]?.Misconfigurations[]? | select(.Severity=="HIGH")] | length' "$OUTPUT_DIR/azure.json")
        echo "- CRITICAL=$azure_critical, HIGH=$azure_high" >> "$REPORT_SUMMARY"
    fi
    echo "" >> "$REPORT_SUMMARY"
else
    echo ">>> Azure: Skipped (no credentials)" >> "$REPORT_SUMMARY"
fi

# ===== GCP =====
if [ -n "$GOOGLE_APPLICATION_CREDENTIALS" ]; then
    echo "### GCP 扫描" >> "$REPORT_SUMMARY"
    echo "" >> "$REPORT_SUMMARY"
    echo ">>> Scanning GCP..."

    trivy gcp \
        --severity HIGH,CRITICAL \
        --format json \
        --output "$OUTPUT_DIR/gcp.json" \
        2>&1 | tail -1

    if [ -f "$OUTPUT_DIR/gcp.json" ]; then
        gcp_critical=$(jq '[.Results[]?.Misconfigurations[]? | select(.Severity=="CRITICAL")] | length' "$OUTPUT_DIR/gcp.json")
        gcp_high=$(jq '[.Results[]?.Misconfigurations[]? | select(.Severity=="HIGH")] | length' "$OUTPUT_DIR/gcp.json")
        echo "- CRITICAL=$gcp_critical, HIGH=$gcp_high" >> "$REPORT_SUMMARY"
    fi
    echo "" >> "$REPORT_SUMMARY"
else
    echo ">>> GCP: Skipped (no credentials)" >> "$REPORT_SUMMARY"
fi

# ===== 输出汇总 =====
echo "" >> "$REPORT_SUMMARY"
echo "---" >> "$REPORT_SUMMARY"
echo "报告文件位置: $OUTPUT_DIR" >> "$REPORT_SUMMARY"

cat "$REPORT_SUMMARY"
echo ""
echo "Full report saved to: $OUTPUT_DIR"
```

### 步骤五：配置定期云扫描（CronJob / GitHub Actions）

**目标**：每周自动执行多云扫描并推送报告。

**方案 A：K8s CronJob + Trivy 容器**

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: multi-cloud-scan
spec:
  schedule: "0 8 * * 1"   # 每周一早上 8 点
  jobTemplate:
    spec:
      template:
        spec:
          containers:
          - name: trivy-scanner
            image: aquasec/trivy:0.50.0
            command: ["/bin/sh", "/scripts/multi-cloud-scan.sh"]
            env:
            - name: AWS_ACCESS_KEY_ID
              valueFrom:
                secretKeyRef:
                  name: cloud-credentials
                  key: aws-access-key
            - name: AWS_SECRET_ACCESS_KEY
              valueFrom:
                secretKeyRef:
                  name: cloud-credentials
                  key: aws-secret-key
            - name: AZURE_CLIENT_ID
              valueFrom:
                secretKeyRef:
                  name: cloud-credentials
                  key: azure-client-id
            - name: AZURE_CLIENT_SECRET
              valueFrom:
                secretKeyRef:
                  name: cloud-credentials
                  key: azure-client-secret
            - name: AZURE_TENANT_ID
              valueFrom:
                secretKeyRef:
                  name: cloud-credentials
                  key: azure-tenant-id
            - name: GOOGLE_APPLICATION_CREDENTIALS
              value: /secrets/gcp-key.json
            volumeMounts:
            - name: scripts
              mountPath: /scripts
            - name: gcp-key
              mountPath: /secrets
          volumes:
          - name: scripts
            configMap:
              name: scan-scripts
          - name: gcp-key
            secret:
              secretName: gcp-credentials
          restartPolicy: OnFailure
```

**方案 B：GitHub Actions**

```yaml
name: Weekly Multi-Cloud Scan
on:
  schedule:
    - cron: '0 8 * * 1'  # 每周一早 8 点
  workflow_dispatch:       # 支持手动触发

jobs:
  aws-scan:
    runs-on: ubuntu-latest
    steps:
      - uses: aquasecurity/trivy-action@master
        with:
          scan-type: 'config'
          format: 'json'
          output: 'aws-scan.json'
          trivy-command: 'aws'
          trivy-args: '--region us-east-1 --severity HIGH,CRITICAL'
        env:
          AWS_ACCESS_KEY_ID: ${{ secrets.AWS_ACCESS_KEY_ID }}
          AWS_SECRET_ACCESS_KEY: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
```

> **坑点**：云凭证是最高安全级别的资产。Never 硬编码在代码中。Always 使用 GitHub Secrets / K8s Secrets / Vault。审计日志中必须记录「谁在什么时间使用了谁的凭证」。

### 步骤六：结果聚合与可视化

**目标**：将多次扫描结果整合到趋势 Dashboard。

```python
#!/usr/bin/env python3
"""
从多次扫描的 JSON 结果生成趋势数据，导入 Grafana
"""

import json
import os
from collections import defaultdict
from datetime import datetime
from glob import glob


def aggregate_multi_cloud_reports(report_dir):
    """聚合多云扫描报告，输出 Prometheus 可抓取的指标文件"""
    trend = defaultdict(lambda: {"critical": 0, "high": 0, "medium": 0})

    for report_file in glob(os.path.join(report_dir, "*.json")):
        with open(report_file) as f:
            report = json.load(f)

        # 提取日期和云厂商
        filename = os.path.basename(report_file)  # e.g. aws-us-east-1.json
        parts = filename.replace(".json", "").split("-")
        cloud = parts[0]  # aws / azure / gcp
        date = datetime.now().strftime("%Y-%m-%d")  # 可从文件名/创建时间获取

        for result in report.get("Results", []):
            for misconf in result.get("Misconfigurations", []):
                sev = misconf.get("Severity", "").lower()
                if sev in ["critical", "high", "medium"]:
                    trend[f"{cloud}"][sev] += 1

    # 输出 Prometheus textfile 格式
    prom_file = os.path.join(report_dir, "trivy_cloud.prom")
    with open(prom_file, "w") as f:
        for cloud, counts in trend.items():
            for sev, count in counts.items():
                f.write(
                    f'trivy_cloud_misconfigurations{{cloud="{cloud}",severity="{sev}"}} {count}\n'
                )
        f.write(f"trivy_cloud_last_scan_timestamp {int(datetime.now().timestamp())}\n")

    print(f"Metrics written to: {prom_file}")
    return trend


if __name__ == "__main__":
    stats = aggregate_multi_cloud_reports("/var/reports/multi-cloud/")
    for cloud, sevs in stats.items():
        print(f"{cloud}: {dict(sevs)}")
```

### 测试验证

1. 配置 AWS IAM 只读角色，执行 `trivy aws --region us-east-1 --severity HIGH,CRITICAL`，验证能正常扫描并输出报告。
2. 故意创建一个公开的 S3 Bucket（测试环境），确认 Trivy 能检测到并标记为 CRITICAL。
3. 配置 Azure Service Principal，执行 `trivy azure`，确认能扫描到 Storage Account 和 SQL Database 的配置问题。
4. 运行聚合脚本，确认 Prometheus 指标文件格式正确，可被 Node Exporter textfile collector 抓取。
5. 在 GitHub Actions 中配置 Weekly Cron，观察是否能成功调用云凭证并生成扫描报告。

---

## 4. 项目总结

### 优点 & 缺点

| 维度 | 优点 | 缺点 |
|------|------|------|
| 统一入口 | 一条命令扫多云；减少工具碎片化 | 每种云仍需独立配置认证 |
| 报告统一 | Table/JSON/SARIF 格式一致 | 各云厂商的资源类型和字段不完全相同 |
| 最小权限 | 仅需只读 API 权限；无安全风险 | 需要为每个云单独创建 IAM 角色/SP |
| 自动化友好 | 可嵌入 CronJob / GitHub Actions | 云 API 限流可能影响大规模扫描 |
| 免费开源 | 零许可费用 | 功能覆盖不如商业 CSPM 全面 |

### 适用场景

1. **多云/混合云架构**：统一 AWS/Azure/GCP 的安全视图，减少云厂商锁定。
2. **云配置漂移监控**：定期扫描识别配置变更（如安全组规则被意外开放）。
3. **合规基线检查**：对 CIS AWS/Azure/GCP Benchmark 的自动检查。
4. **云迁移安全评估**：迁移前的全面配置扫描，发现安全隐患。
5. **成本敏感型安全团队**：预算有限但需要 CSPM 能力，Trivy 是理想的起点。

**不适用场景**：
1. 纯本地部署（On-Premise）——`trivy aws/azure/gcp` 不适用于私有数据中心。
2. 需要高级威胁检测的场景（如异常行为分析、用户实体行为分析 UEBA）——这超出了 Trivy 的能力范围。

### 注意事项

- **云 API 限流**：AWS、Azure、GCP 都有 API 调用频率限制。大规模扫描（如跨多个 Region/Subscription/Project 的大量资源）可能触发限流。建议分批扫描，间隔 30 秒以上。
- **凭证泄漏是最严重的安全威胁**。云凭证绝不应出现在日志、配置文件、Git 仓库中。使用 Secret Manager（AWS Secrets Manager / Azure Key Vault / GCP Secret Manager）管理凭证。
- **只读权限并不意味着零风险**。虽然 Trivy 只做读操作，但误用凭证（如被恶意窃取）仍可被用于信息窃取。凭证应定期轮换。

### 常见踩坑经验

**踩坑案例 1：AWS 多 Region 扫描遗漏**
- **现象**：只扫了 `us-east-1`，但重要的 RDS 实例在 `ap-southeast-1`，结果被漏扫了。
- **根因**：AWS 资源是 Region 级别的，Trivy 的 `--region` 参数必须显式指定每个区域。
- **解法**：用 `aws ec2 describe-regions --all-regions` 获取可用区域列表，逐一传入 Trivy。

**踩坑案例 2：Azure 订阅 ID 混淆**
- **现象**：配置了 Service Principal 但扫描结果为空。
- **根因**：Service Principal 被授予了订阅 A 的 Reader 角色，但 `trivy azure` 默认扫描的是 `az account show` 的当前订阅（可能是订阅 B）。
- **解法**：显式指定 `--subscription-id`；或用 `az account set --subscription <id>` 切换上下文。

**踩坑案例 3：GCP 项目太多导致超时**
- **现象**：公司有 50+ 个 GCP 项目，`trivy gcp` 超过 30 分钟超时。
- **根因**：`trivy gcp` 默认扫描 Service Account 有权访问的所有项目，资源量巨大。
- **解法**：用 `--project-id` 分批指定；或用 shell 脚本循环处理多个项目，每个项目独立输出报告。

### 思考题

1. Trivy 的云扫描只支持 AWS、Azure、GCP。如果你的公司还使用阿里云（Alibaba Cloud）和华为云（Huawei Cloud），你会如何扩展 Trivy 的云扫描能力？是否可以通过 Trivy 插件机制（`trivy plugin`）实现？

2. 云扫描通常需要较高的 API 调用频次。请设计一个「增量云扫描」方案：只扫描上次扫描后有变更的云资源（利用 CloudTrail / Azure Activity Log / GCP Audit Logs），而不是每次都全量扫描。

> **答案提示**：第 40 章「从零构建企业级安全扫描平台」将讨论如何将多云扫描能力嵌入到自研安全平台中。

---

> **推广计划**：本章建议安全团队和 DevOps 团队协作落地。安全团队负责定义云扫描的检查范围、严重级别和告警阈值；DevOps 负责配置 IAM 角色/Service Principal/Service Account 并维护凭证轮换；平台工程负责将 `multi-cloud-scan.sh` 嵌入 CronJob 调度并对接 Prometheus 监控。首次落地建议从单一云厂商（如 AWS us-east-1）开始，跑通后逐步扩展到所有云和区域。

---

> **版权声明**：本章基于 Trivy 官方开源项目（Apache-2.0 License）编写，所有源码引用均遵循原许可证条款。
