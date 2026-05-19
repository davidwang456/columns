# 第20章：Kubernetes 集群安全扫描实战

> 版本：Trivy v0.50+
> 面向人群：运维、DevOps、SRE、安全工程师

---

## 1. 项目背景

### 业务场景

云帆科技的容器化转型进入了深水区。50 多个微服务全部跑在 Kubernetes 上，Harbor 的镜像扫描和 CI 的门禁也运转良好。但 CTO 在一次攻防演练后提出了一个尖锐的问题：「我们的安全扫描全都集中在『构建时』和『入库时』，但镜像运行起来之后呢？如果一个 Pod 今天被注入了恶意配置，或者一个 ConfigMap 里突然出现了明文密码，我们的扫描体系能发现吗？」

运维老李被问住了。他确实每天扫描新构建的镜像，也确实阻止了带漏洞的镜像进入 Harbor，但他从未扫描过「正在运行」的集群。攻击者如果通过某种方式（如供应链投毒、配置漂移、权限提升）在运行时引入了风险，现有的体系完全无感知。

更现实的威胁来自内部。开发小王为了调试一个线上问题，临时给一个 Deployment 加了 `hostPID: true`——这样他就能从容器里看到宿主机的进程。调试完后他忘了改回去，这个危险的配置就留在了生产环境。一周后，安全团队做配置审计时才发现，但此时该 Deployment 已经运行了 7 天。

与此同时，公司正在准备等保 2.0 三级认证。审计机构明确要求：「必须提供 Kubernetes 集群的漏洞扫描报告和配置基线检查报告，覆盖所有 Namespace 的所有工作负载。」老李拿着 Trivy 的文档看了半天，发现 `trivy k8s` 子命令确实能扫集群，但具体操作、权限配置、报告解读、与现有体系的对接，都缺乏实践经验。

### 痛点放大

**第一，「运行时盲区」。** 构建时安全的镜像，在运行过程中可能因为 sidecar 注入、配置挂载、环境变量变更而引入新的风险。静态扫描无法覆盖运行动态。

**第二，配置漂移。** K8s 的配置是声明式的，但实际操作中经常有人用 `kubectl edit` 或 `kubectl patch` 做临时修改。这些修改不会被 Git 记录，也不会触发 CI 扫描，形成「幽灵配置」。

**第三，权限管理混乱。** 不同团队的 ServiceAccount、RBAC 规则、NetworkPolicy 散落在各个 Namespace 中，没有统一的安全基线。某些 Pod 以 root 运行、某些 Pod 挂载了宿主机的 `/var/run/docker.sock`、某些 Namespace 完全没有 NetworkPolicy——这些问题在集群规模较小时可以人工发现，但当集群有 500+ Pod 时，人工检查完全不现实。

**第四，多集群管理。** 云帆科技有开发集群、测试集群、预发布集群、生产集群，还有一个给大数据团队用的独立集群。每个集群的安全状态如何？是否存在某个漏洞只影响生产集群但不影响测试集群？没有集中化的扫描和报告，这些问题无从回答。

**本章的核心目标是：掌握 `trivy k8s` 的完整使用流程，建立覆盖漏洞扫描、配置审计、Secret 检测的 K8s 运行态安全基线，并学会多集群的统一扫描和报告。**

---

## 2. 项目设计

**场景**：云帆科技的 K8s 安全治理启动会，老李（运维负责人）、小胖（开发代表）和大师正在讨论如何扫描运行中的集群。

---

**小胖**：「`trivy image` 扫的是镜像文件系统，`trivy k8s` 扫的是什么？正在运行的容器里面的实时文件？」

**小白**：「不完全是。`trivy k8s` 的工作原理是：

1. **资源发现**：通过 Kubernetes API 获取你指定的资源列表（如所有 Deployment、所有 Pod、所有 ConfigMap）。
2. **镜像提取**：从 Pod Spec 中提取容器镜像的引用。
3. **镜像扫描**：拉取镜像（如果本地没有），然后像 `trivy image` 一样扫描镜像的漏洞。
4. **配置扫描**：分析 K8s 资源的 YAML 定义，检查是否存在危险的配置（如 `privileged: true`、缺少 `securityContext`）。
5. **Secret 检测**：扫描 ConfigMap、Secret、环境变量中是否包含硬编码的密钥。

所以它不是扫『运行时内存』，而是扫『运行态配置 + 运行态镜像』。攻击者如果通过内存注入木马，`trivy k8s` 是发现不了的——那需要 Falco、Tracee 等运行时安全工具。」

**大师**：「技术映射：你可以把 `trivy image` 想象成『出厂质检』，`trivy k8s` 想象成『在路上抽查』。出厂合格的车，上路后可能被非法改装（危险配置）、可能使用了过期配件（镜像层未更新）、可能藏了违禁品（Secret 泄露）。抽查不能阻止所有犯罪，但能发现大部分明显的违规行为。」

**小胖**：「那扫描集群需要什么权限？给 Trivy 绑个 cluster-admin 是不是太危险了？」

**小白**：「cluster-admin 确实过度授权。Trivy 只需要读取权限，而且按最小权限原则，可以分两种模式：

- **Namespace 级别**：只扫描特定 Namespace，绑定 `view` + `get/list` Secret 的 Role。
- **Cluster 级别**：扫描全集群，绑定 `cluster-reader` + 自定义的 Secret 读取权限。

具体权限需求取决于你要扫描的内容：
- 漏洞扫描：需要读取 Pod（获取镜像名）。
- 配置扫描：需要读取 Deployment、DaemonSet、StatefulSet 等工作负载。
- Secret 检测：需要读取 Secret 和 ConfigMap（这是敏感操作，需严格审批）。」

**老李**：「扫描生产集群会不会有影响？拉取镜像、大量 API 调用，会不会把 APIServer 打挂？」

**大师**：「合理控制扫描范围和频率就不会有太大影响。建议：

1. **错峰扫描**：生产集群在业务低峰期（如凌晨 2-4 点）扫描。
2. **限流**：Trivy 支持 `--parallel` 参数限制并发，减少对 APIServer 的压力。
3. **镜像缓存**：Trivy 会复用本地已有的镜像层，不会每次都重新拉取完整镜像。
4. **增量扫描**：只对新增或变更的 Pod 做扫描，而不是每次都全量扫所有 Pod。」

**小胖**：「多集群怎么管理？我们有 5 个集群，难道要登到每个集群上执行 `trivy k8s`？」

**小白**：「可以用 kubeconfig 的 context 切换，或者用脚本批量执行。更高级的方案是用 Trivy Operator（第 21 章详细介绍），它会作为 Agent 常驻在每个集群中，自动扫描并上报结果到中央 Grafana Dashboard。」

**大师**：「本章我们先聚焦手动扫描和脚本化扫描，建立对 `trivy k8s` 的完整认知。Trivy Operator 是下一章的内容。」

---

## 3. 项目实战

### 环境准备

- **Kubernetes 集群**：v1.25+，访问权限正常
- **kubectl**：已配置并能访问目标集群
- **Trivy**：v0.50+，已安装
- **测试 Namespace**：创建一个包含多种工作负载的测试环境

### 步骤一：配置 Trivy 的 K8s 访问权限

**目标**：以最小权限原则创建 ServiceAccount 和 RBAC。

创建 `trivy-scanner-rbac.yaml`：

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: trivy-scanner
  namespace: security
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: trivy-scanner
rules:
  # 读取工作负载（用于漏洞扫描和配置审计）
  - apiGroups: [""]
    resources: ["pods", "configmaps", "secrets", "nodes"]
    verbs: ["get", "list"]
  - apiGroups: ["apps"]
    resources: ["deployments", "daemonsets", "statefulsets", "replicasets"]
    verbs: ["get", "list"]
  - apiGroups: ["batch"]
    resources: ["jobs", "cronjobs"]
    verbs: ["get", "list"]
  - apiGroups: ["rbac.authorization.k8s.io"]
    resources: ["roles", "rolebindings", "clusterroles", "clusterrolebindings"]
    verbs: ["get", "list"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: trivy-scanner
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: trivy-scanner
subjects:
  - kind: ServiceAccount
    name: trivy-scanner
    namespace: security
```

部署：

```bash
kubectl create namespace security
kubectl apply -f trivy-scanner-rbac.yaml
```

获取 Token：

```bash
# 创建长期有效的 Token（K8s 1.24+ 需要手动创建 Secret）
kubectl apply -f - <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: trivy-scanner-token
  namespace: security
  annotations:
    kubernetes.io/service-account.name: trivy-scanner
type: kubernetes.io/service-account-token
EOF

# 获取 Token
TOKEN=$(kubectl get secret trivy-scanner-token -n security -o jsonpath='{.data.token}' | base64 -d)
echo $TOKEN
```

配置 kubeconfig：

```bash
kubectl config set-credentials trivy-scanner --token=$TOKEN
kubectl config set-context trivy-scan --cluster=$(kubectl config view -o jsonpath='{.contexts[0].context.cluster}') --user=trivy-scanner
```

### 步骤二：执行集群级扫描

**目标**：扫描整个集群的所有资源。

```bash
# 切换到扫描上下文
kubectl config use-context trivy-scan

# 集群级扫描（Summary 模式）
trivy k8s --report summary cluster

# 集群级扫描（All 模式，输出所有漏洞详情）
trivy k8s --report all --format json -o k8s-scan-full.json cluster
```

**Summary 模式输出示例**：
```
Summary Report for cluster
===========================

Workload Assessment:
┌─────────────┬───────────────────┬───────────────────┬───────────────────┬───────────────────┐
│  Namespace  │     Vulnerabilities    │  Misconfigurations  │      Secrets       │      RBAC          │
│             ├──────┬──────┬──────┼─────────┬──────────┼──────┬──────┬──────┼───────────────────┤
│             │ CRIT │ HIGH │ TOTAL│ CRIT    │ HIGH     │ CRIT │ HIGH │ TOTAL│                   │
├─────────────┼──────┼──────┼──────┼─────────┼──────────┼──────┼──────┼──────┼───────────────────┤
│ production  │   5  │  23  │  128 │    2    │    8     │   0  │   3  │   5  │  2 warnings       │
│ staging     │   3  │  15  │   89 │    1    │    5     │   0  │   1  │   2  │  1 warning        │
│ dev         │  12  │  45  │  256 │    5    │   12     │   1  │   5  │  12  │  5 warnings       │
└─────────────┴──────┴──────┴──────┴─────────┴──────────┴──────┴──────┴──────┴───────────────────┘
```

### 步骤三：Namespace 级定向扫描

**目标**：对特定 Namespace 做深入扫描。

```bash
# 只扫描 production namespace
trivy k8s --namespace production --report all -o prod-scan.json cluster

# 只扫描特定资源类型
trivy k8s --namespace production --resource kind=Deployment cluster

# 扫描特定 Pod
trivy k8s --namespace production pod/my-app-xxx cluster
```

### 步骤四：配置错误扫描（Misconfiguration）

**目标**：发现 K8s 资源定义中的高危配置。

创建测试用的危险配置：

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: insecure-app
  namespace: test
spec:
  replicas: 1
  selector:
    matchLabels:
      app: insecure-app
  template:
    metadata:
      labels:
        app: insecure-app
    spec:
      hostPID: true
      hostNetwork: true
      containers:
      - name: app
        image: nginx:latest
        securityContext:
          privileged: true
          runAsRoot: true
          allowPrivilegeEscalation: true
          readOnlyRootFilesystem: false
          capabilities:
            add:
              - NET_ADMIN
              - SYS_ADMIN
        volumeMounts:
        - name: docker-sock
          mountPath: /var/run/docker.sock
        - name: host-root
          mountPath: /host
      volumes:
      - name: docker-sock
        hostPath:
          path: /var/run/docker.sock
      - name: host-root
        hostPath:
          path: /
```

部署并扫描：

```bash
kubectl apply -f insecure-deployment.yaml
trivy k8s --namespace test --scanners misconfig -o misconfig-scan.json cluster
```

**预期发现的 Misconfiguration**：
- `AVD-KSV-0010`：容器挂载了敏感的 host 路径（`/var/run/docker.sock`）
- `AVD-KSV-0017`：容器以 privileged 模式运行
- `AVD-KSV-0003`：Pod 使用了 hostPID
- `AVD-KSV-0011`：容器没有设置 resource limits
- `AVD-KSV-0106`：容器 capabilities 添加了 SYS_ADMIN

### 步骤五：Secret 检测

**目标**：扫描 ConfigMap 和 Secret 中的硬编码密钥。

创建测试 ConfigMap：

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: app-config
  namespace: test
data:
  database.properties: |
    db.url=jdbc:mysql://db:3306/app
    db.user=admin
    db.password=************
  api.keys: |
    aws.access.key=AKIAIOSFODNN7EXAMPLE
    stripe.key=************
```

扫描：

```bash
trivy k8s --namespace test --scanners secret -o secret-scan.json cluster
```

### 步骤六：多集群批量扫描脚本

**目标**：对多个集群执行统一扫描并汇总报告。

创建 `scan-all-clusters.sh`：

```bash
#!/bin/bash
CLUSTERS=("dev-k8s" "staging-k8s" "prod-k8s" "bigdata-k8s")
REPORT_DIR="/var/reports/k8s/$(date +%Y%m%d)"
mkdir -p "$REPORT_DIR"

for ctx in "${CLUSTERS[@]}"; do
    echo "=== Scanning cluster: $ctx ==="
    
    kubectl config use-context "$ctx"
    
    # Summary scan
    trivy k8s --report summary \
        -o "${REPORT_DIR}/${ctx}-summary.json" \
        cluster 2>"${REPORT_DIR}/${ctx}.log"
    
    # 提取关键指标
    CRIT_VULN=$(cat "${REPORT_DIR}/${ctx}-summary.json" | jq -r '.Vulnerabilities[]? | select(.Severity=="CRITICAL") | .Count' | awk '{s+=$1} END {print s+0}')
    CRIT_MISCONFIG=$(cat "${REPORT_DIR}/${ctx}-summary.json" | jq -r '.Misconfigurations[]? | select(.Severity=="CRITICAL") | .Count' | awk '{s+=$1} END {print s+0}')
    
    echo "Cluster: $ctx | Critical Vulns: $CRIT_VULN | Critical Misconfigs: $CRIT_MISCONFIG"
    
    # 如果生产集群有 Critical，立即告警
    if [[ "$ctx" == "prod-k8s" && "$CRIT_VULN" -gt 0 ]]; then
        curl -X POST "$SLACK_WEBHOOK" \
            -H "Content-Type: application/json" \
            -d "{\"text\":\"🚨 PROD CLUSTER ALERT: $CRIT_VULN Critical vulnerabilities found\"}"
    fi
done

# 生成汇总报告
cat > "${REPORT_DIR}/index.html" << EOF
<html><head><title>K8s Security Report</title></head><body>
<h1>K8s Security Scan Report - $(date +%Y-%m-%d)</h1>
<table border="1">
<tr><th>Cluster</th><th>Critical Vulns</th><th>High Vulns</th><th>Critical Misconfigs</th><th>High Misconfigs</th></tr>
EOF

for ctx in "${CLUSTERS[@]}"; do
    DATA=$(cat "${REPORT_DIR}/${ctx}-summary.json" | jq -r '
        [
            (.Vulnerabilities[]? | select(.Severity=="CRITICAL") | .Count) // 0 | tonumber,
            (.Vulnerabilities[]? | select(.Severity=="HIGH") | .Count) // 0 | tonumber,
            (.Misconfigurations[]? | select(.Severity=="CRITICAL") | .Count) // 0 | tonumber,
            (.Misconfigurations[]? | select(.Severity=="HIGH") | .Count) // 0 | tonumber
        ] | @tsv
    ' | awk '{c+=$1; h+=$2; cm+=$3; hm+=$4} END {print c"\t"h"\t"cm"\t"hm}')
    IFS=$'\t' read -r CV HV CM HM <<< "$DATA"
    echo "<tr><td>$ctx</td><td>$CV</td><td>$HV</td><td>$CM</td><td>$HM</td></tr>" >> "${REPORT_DIR}/index.html"
done

echo "</table></body></html>" >> "${REPORT_DIR}/index.html"
echo "Report generated: ${REPORT_DIR}/index.html"
```

### 测试验证

1. 部署 `trivy-scanner-rbac.yaml`，确认 ServiceAccount 能正确列出所有资源。
2. 执行 `trivy k8s --report summary cluster`，确认输出包含所有 Namespace 的统计。
3. 部署 `insecure-deployment.yaml`，确认扫描发现 `hostPID`、`privileged` 等高危配置。
4. 部署含密钥的 ConfigMap，确认 Secret Scanner 正确识别。
5. 运行 `scan-all-clusters.sh`，验证多集群报告生成和 Slack 告警。

---

## 4. 项目总结

### 优点 & 缺点

| 维度 | 优点 | 缺点 |
|------|------|------|
| 覆盖范围 | 同时扫描漏洞、配置错误、Secret、RBAC | 不覆盖运行时行为（如内存注入） |
| 操作简便 | 一条命令扫描整个集群，无需安装 Agent | 大规模集群扫描可能较慢 |
| 权限可控 | 可用 ServiceAccount 实现最小权限 | Secret 扫描需要读取 Secret，敏感度高 |
| 报告丰富 | Summary/All 两种模式，JSON 便于集成 | 默认 table 输出在集群规模大时不友好 |
| 多集群支持 | 通过 kubeconfig context 切换 | 需要手动管理多个集群的凭证 |

### 适用场景

1. **集群安全基线审计**：等保、SOC2、PCI-DSS 等合规要求的 K8s 配置检查。
2. **发版前巡检**：新版本上线前，扫描目标 Namespace 确认无新增风险。
3. **配置漂移检测**：发现通过 `kubectl edit` 等手工方式引入的危险配置。
4. **Secret 泄露排查**：扫描 ConfigMap 和 Environment Variables 中的硬编码密钥。
5. **多集群态势感知**：定期扫描所有集群，汇总安全风险热力图。

**不适用场景**：
1. 需要检测容器逃逸、内核漏洞利用等运行时攻击——需配合 Falco、Tetragon。
2. 需要实时（秒级）安全监控——`trivy k8s` 是定期扫描，不是持续监控。

### 注意事项

- **APIServer 压力**：全集群扫描会产生大量 LIST API 调用，建议在业务低峰期执行。
- **镜像拉取带宽**：扫描会拉取集群中引用的所有镜像，确保节点有足够的磁盘和网络带宽。
- **Secret 读取审计**：由于 Secret 扫描需要读取 Secret 资源，所有访问会被 K8s Audit Log 记录，需告知安全团队。
- **RBAC 最小化**：不要给 Trivy 绑定 cluster-admin，严格按本章的 RBAC 模板授权。

### 常见踩坑经验

**踩坑案例 1：扫描报 `Unauthorized`**
- **现象**：`trivy k8s` 报错 `User "system:serviceaccount:security:trivy-scanner" cannot list resource`。
- **根因**：RBAC 权限不足，或 kubeconfig 使用了错误的上下文。
- **解法**：检查 ClusterRole 是否包含目标资源类型；确认 kubectl context 正确。

**踩坑案例 2：扫描结果为空但集群明显有漏洞**
- **现象**：Summary 显示所有计数为 0。
- **根因**：Trivy 使用的是本地缓存的旧数据库，或镜像拉取失败。
- **解法**：执行 `trivy image --download-db-only` 更新数据库；检查节点是否能拉取镜像。

**踩坑案例 3：大规模集群扫描超时**
- **现象**：扫描 500+ Pod 的集群时，Trivy 运行 30 分钟后被 kill。
- **根因**：默认超时 5 分钟不够，或节点资源不足。
- **解法**：增加 `--timeout 60m`；按 Namespace 分批扫描；限制 `--severity` 减少输出量。

### 思考题

1. 假设你的生产集群有 2000 个 Pod，分布在 50 个 Namespace 中。`trivy k8s cluster` 全量扫描需要 2 小时。请设计一个「分层扫描」策略：如何根据 Namespace 的业务重要性、变更频率、历史漏洞数来分配不同的扫描周期（如核心 Namespace 每天扫、边缘 Namespace 每周扫）？
2. `trivy k8s` 扫描发现某个 Deployment 存在 HIGH 级别的配置错误，但开发团队认为「这个配置是业务必需的，不能改」。请设计一个「配置豁免」工作流：如何在保持扫描自动化的同时，允许经过审批的例外配置存在，并确保审计可追溯？

> **答案提示**：第 21 章「Trivy Operator 与 K8s 持续合规」将介绍如何通过 CRD 实现持续扫描和自动修复；第 30 章「企业级策略即代码」将深入讲解 K8s 配置的安全基线管理。

---

> **推广计划**：本章是 K8s 运维团队和安全团队的必读内容。建议所有集群部署 `trivy-scanner-rbac.yaml`，建立最小权限的扫描账号。SRE 团队将 `scan-all-clusters.sh` 纳入每周定时任务，报告汇总到安全 Dashboard。开发团队了解 `trivy k8s` 的扫描范围，在收到配置错误告警时优先修复 `privileged`、`hostPID` 等 P0 级问题。安全团队将 K8s 扫描报告纳入等保审计材料，定期向管理层汇报集群安全态势。
