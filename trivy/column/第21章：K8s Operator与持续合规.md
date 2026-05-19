# 第21章：K8s Operator 与持续合规

> 版本：Trivy v0.50+ / Trivy Operator v0.18+
> 面向人群：SRE、DevOps、安全工程师、K8s 管理员

---

## 1. 项目背景

### 业务场景

云帆科技的 K8s 集群扫描（`trivy k8s`）已经运行了两个月。每周一凌晨，运维老李会执行一次全集群扫描，生成报告发到安全群。但这个流程有几个明显的缺陷：

第一，扫描是「快照式」的。周一凌晨扫完，周三开发部署了一个新版本，周五有人用 `kubectl edit` 改了一个配置——这些变化在下次扫描前完全不可见。上周的「安全」不等于本周的「安全」。

第二，报告是「人驱动」的。扫描结果躺在 PDF 里，开发不会主动去看。直到有一次，一个带 Log4Shell 漏洞的 Pod 在生产环境运行了 11 天，才被安全团队偶然发现。

第三，多集群管理是「孤岛式」的。北京、上海、新加坡三个集群各自扫描、各自存储报告，没有统一的视图。CTO 问「全公司现在有多少个 Critical 漏洞在运行」，老李需要分别登录三个集群、执行三次扫描、手动汇总Excel——整个过程耗时 2 小时。

第四，配置修复是「手动的」。扫描发现某个 Deployment 缺少 `securityContext`，安全团队发邮件给开发，开发排期修改，测试验证，最后上线——平均耗时 2 周。而在这 2 周里，这个不安全的配置一直在生产环境运行。

CTO 在一次架构评审会上提出：「我们要从『人定期扫描』进化到『系统持续监控』。就像 Prometheus 持续监控指标一样，安全扫描也应该是持续运行的——新 Pod 创建时自动扫描，新漏洞入库时自动重扫，发现高危问题时自动告警。」

### 痛点放大

**第一，静态扫描无法覆盖动态变化。** `trivy k8s` 是「命令行工具」，执行完就退出。K8s 是动态系统——Pod 随时创建、销毁、滚动更新，ConfigMap 随时被修改，镜像 tag 随时被重新指向。没有持续监控，任何一次合法的变更都可能引入新的风险。

**第二，安全与运维的数据割裂。** 运维用 Prometheus + Grafana 监控 CPU、内存、网络，安全用 Trivy 扫漏洞，两套系统、两套界面、两套告警渠道。安全事件无法与运维指标关联分析。

**第三，修复闭环太长。** 发现 → 通知 → 排期 → 修复 → 验证，五个步骤跨越多个团队、多个系统。如果能在发现问题的瞬间就自动通知，甚至自动采取临时缓解措施（如隔离 Pod），风险窗口可以从「周」缩短到「分钟」。

**第四，合规报告的手工负担。** 等保审计需要证明「我们对运行态工作负载进行了持续监控」。每周一次的脚本扫描，无法证明「持续」。审计员要的是「7×24 小时的监控证据」，而不是「每周一的快照」。

**本章的核心目标是：部署 Trivy Operator，建立 K8s 集群的持续安全监控体系，实现「新资源创建即扫描、新漏洞发布即重扫、高危风险即告警」的自动化闭环。**

---

## 2. 项目设计

**场景**：云帆科技的 K8s 架构升级会，老李（SRE 负责人）、小胖（开发代表）和大师正在讨论如何从「人工扫描」进化到「自动监控」。

---

**小胖**：「`trivy k8s` 不是挺好的吗？每周跑一次，报告也挺全的。」

**小白**：「好是好，但它有个本质问题——它是『手电筒』，不是『路灯』。手电筒只能在你按下开关的那一刻照亮一片区域；路灯是 24 小时亮着的，任何时间有人走过，都能被看见。K8s 集群里，Pod 随时在变化，你周一凌晨扫完，周二中午有人部署了一个带漏洞的镜像，谁能发现？」

**大师**：「技术映射：完全正确。Trivy CLI 是『体检』——定期去医院查一次；Trivy Operator 是『可穿戴设备』——24 小时监测心率、血压，异常时立即报警。对于生产环境的 K8s，我们需要的是可穿戴设备，而不是一年一度的体检。」

**小胖**：「那 Operator 具体是怎么工作的？它不会每个 Pod 里都塞一个 Sidecar 吧？」

**小白**：「不塞 Sidecar。Trivy Operator 是 K8s 的『控制平面』组件，它通过 Watch API 监听集群中的所有资源变化。当一个新的 Deployment 被创建、或一个 Pod 被调度、或一个 ConfigMap 被修改时，Operator 会自动生成一个 Scan Job（临时 Pod），用 Trivy 扫描这个新资源，然后把结果写入自定义资源（CRD）——如 `VulnerabilityReport`、`ConfigAuditReport`、`ExposedSecretReport`、`RbacAssessmentReport`。」

**大师**：「而且这些 CRD 是 K8s 原生的对象，可以用 `kubectl get vulnerabilityreports` 查看，可以用 Prometheus 采集，可以用 Grafana 展示，可以用 Kyverno 做策略决策。它们完全融入了 K8s 的生态体系。」

**小胖**：「那性能呢？如果集群有 1000 个 Pod，Operator 会不会一直创建 Scan Job，把集群打挂？」

**小白**：「Operator 内置了多种优化机制：

1. **去重**：同一个 ReplicaSet 的 3 个 Pod，镜像完全相同，Operator 只会扫描一次，结果复用。
2. **缓存**：扫描过的镜像 digest 会被缓存，相同 digest 的 Pod 不再重复扫描。
3. **定时重扫**：可以配置 `scanJobRetryAfter` 和 `vulnerabilityScannerScanOnlyCurrentRevisions`，控制重扫频率。
4. **资源限制**：每个 Scan Job 都有 CPU/内存限制，不会无限消耗资源。」

**老李**：「那告警怎么接？我想在 Prometheus 里看到『当前集群有多少 Critical 漏洞』，并在超过阈值时告警。」

**大师**：「Trivy Operator 内置了 Prometheus Metrics Exporter，暴露的指标包括：

- `trivy_image_vulnerabilities`：按 Severity 分类的漏洞数量
- `trivy_resource_configaudits`：配置错误数量
- `trivy_resource_exposedsecrets`：密钥泄露数量

你可以直接在 Grafana 里绘制『集群漏洞热力图』，并配置 Alertmanager：当 `trivy_image_vulnerabilities{severity="Critical"} > 0` 时，立即发送 PagerDuty/Slack 告警。」

**小胖**：「那自动修复呢？Operator 能自动修复发现的问题吗？」

**小白**：「Trivy Operator 本身不直接修改资源（这是设计上的安全考虑），但它可以与 Kyverno/OPA Gatekeeper 联动。比如：

- Kyverno 策略：禁止创建 `privileged: true` 的 Pod。
- OPA Gatekeeper：强制所有 Deployment 必须设置 `securityContext.runAsNonRoot: true`。
- Trivy Operator：持续审计现有资源，发现违规时生成报告并告警。

这样，『准入控制』阻止新问题进入，『持续审计』发现存量问题，两者配合实现完整的合规闭环。」

**老李**：「多集群怎么统一管理？我们三个集群能不能用一个 Grafana 看全局漏洞态势？」

**大师**：「可以。Trivy Operator 的每个集群暴露各自的 Prometheus Metrics，通过一个全局的 Prometheus Federation 或 Thanos 汇聚，Grafana 里就能看到『北京-生产』、『上海-生产』、『新加坡-生产』三个集群的漏洞对比。更进一步，可以用 Trivy Operator 的 `ClusterVulnerabilityReport` 聚合所有 Namespace 的数据，便于全局视角分析。」

---

## 3. 项目实战

### 环境准备

- **Kubernetes**：v1.25+，至少 3 节点
- **Helm**：v3.10+
- **Prometheus + Grafana**：已安装（或准备安装 kube-prometheus-stack）
- **kubectl**：已配置

### 步骤一：安装 Trivy Operator

**目标**：在集群中部署 Operator 及配套 CRD。

```bash
# 添加 Aqua Helm 仓库
helm repo add aqua https://aquasecurity.github.io/helm-charts/
helm repo update

# 安装 Trivy Operator
helm install trivy-operator aqua/trivy-operator \
  --namespace trivy-system \
  --create-namespace \
  --set="trivy.ignoreUnfixed=true" \
  --set="operator.metricsEnabled=true" \
  --set="operator.webhookBroadcastURL=" \
  --set="trivy.severity=HIGH,CRITICAL" \
  --set="operator.exposedSecretScannerEnabled=true" \
  --set="operator.configAuditScannerEnabled=true" \
  --set="operator.rbacAssessmentScannerEnabled=true" \
  --set="operator.infraAssessmentScannerEnabled=true"
```

**关键参数**：
- `metricsEnabled=true`：暴露 Prometheus 指标。
- `exposedSecretScannerEnabled=true`：启用 Secret 检测。
- `configAuditScannerEnabled=true`：启用配置审计。
- `rbacAssessmentScannerEnabled=true`：启用 RBAC 评估。

验证安装：

```bash
kubectl get pods -n trivy-system
kubectl get crd | grep aquasecurity
```

**预期输出**：
```
vulnerabilityreports.aquasecurity.github.io
configauditreports.aquasecurity.github.io
exposedsecretreports.aquasecurity.github.io
rbacassessmentreports.aquasecurity.github.io
```

### 步骤二：部署测试工作负载并观察扫描

**目标**：验证 Operator 自动扫描新资源。

部署一个故意含漏洞的应用：

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: vulnerable-app
  namespace: default
spec:
  replicas: 2
  selector:
    matchLabels:
      app: vulnerable-app
  template:
    metadata:
      labels:
        app: vulnerable-app
    spec:
      containers:
      - name: app
        image: python:3.4-alpine
        resources:
          limits:
            memory: "256Mi"
            cpu: "500m"
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: app-config
  namespace: default
data:
  config.properties: |
    db.password=SuperSecret123!
    api.key=AKIAIOSFODNN7EXAMPLE
```

```bash
kubectl apply -f vulnerable-workload.yaml
```

等待几分钟后查看扫描结果：

```bash
# 查看漏洞报告
kubectl get vulnerabilityreports -n default
kubectl get vulnerabilityreports -n default -o json | jq '.items[0].report.vulnerabilities[] | {id: .vulnerabilityID, severity: .severity, resource: .resource}'

# 查看配置审计报告
kubectl get configauditreports -n default
kubectl get configauditreports -n default -o yaml

# 查看 Secret 泄露报告
kubectl get exposedsecretreports -n default
```

**预期输出**（VulnerabilityReport 截取）：
```yaml
report:
  artifact:
    repository: library/python
    tag: "3.4-alpine"
  summary:
    criticalCount: 3
    highCount: 8
    mediumCount: 15
  vulnerabilities:
    - vulnerabilityID: CVE-2019-8457
      severity: CRITICAL
      title: sqlite out-of-bounds read
      resource: sqlite
```

### 步骤三：配置 Prometheus 监控与 Grafana 大盘

**目标**：将 Operator 的指标接入可观测性体系。

**ServiceMonitor 配置**（如果 Prometheus Operator 已安装）：

```yaml
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: trivy-operator
  namespace: trivy-system
  labels:
    release: prometheus
spec:
  selector:
    matchLabels:
      app.kubernetes.io/name: trivy-operator
  endpoints:
  - port: metrics
    interval: 60s
    path: /metrics
```

```bash
kubectl apply -f trivy-servicemonitor.yaml
```

**Prometheus 告警规则**：

```yaml
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: trivy-alerts
  namespace: trivy-system
spec:
  groups:
  - name: trivy
    rules:
    - alert: CriticalVulnerabilityInCluster
      expr: sum(trivy_image_vulnerabilities{severity="Critical"}) > 0
      for: 5m
      labels:
        severity: critical
      annotations:
        summary: "Critical vulnerabilities found in running workloads"
        
    - alert: HighConfigAuditFailures
      expr: sum(trivy_resource_configaudits{severity="High"}) > 5
      for: 10m
      labels:
        severity: warning
      annotations:
        summary: "High number of configuration audit failures"
```

**Grafana Dashboard 配置**：

导入 Trivy Operator 官方 Dashboard（ID: 17813）：

```bash
# 在 Grafana UI 中：Create → Import → 输入 17813
```

或手动创建面板：

| 面板 | PromQL |
|------|--------|
| Critical 漏洞总数 | `sum(trivy_image_vulnerabilities{severity="Critical"})` |
| 按 Namespace 的漏洞分布 | `sum by (namespace) (trivy_image_vulnerabilities)` |
| 配置错误趋势 | `sum by (severity) (trivy_resource_configaudits)` |
| Secret 泄露数 | `sum(trivy_resource_exposedsecrets)` |

### 步骤四：与 Kyverno 联动实现自动修复

**目标**：阻止违规资源进入集群，并持续审计存量资源。

安装 Kyverno：

```bash
helm install kyverno kyverno/kyverno --namespace kyverno --create-namespace
```

创建策略：禁止 privileged Pod

```yaml
apiVersion: kyverno.io/v1
kind: ClusterPolicy
metadata:
  name: disallow-privileged
spec:
  validationFailureAction: Enforce
  background: true
  rules:
  - name: check-privileged
    match:
      any:
      - resources:
          kinds:
          - Pod
    validate:
      message: "Privileged containers are not allowed"
      pattern:
        spec:
          containers:
          - securityContext:
              =(privileged): "false"
```

```bash
kubectl apply -f kyverno-policy.yaml
```

**验证联动**：

```bash
# 尝试创建 privileged Pod（应被拒绝）
kubectl run test-privileged --image=nginx --overrides='{"spec":{"containers":[{"name":"test","image":"nginx","securityContext":{"privileged":true}}]}}'
# 预期：Error from server: admission webhook "validate.kyverno.svc-fail" denied the request
```

### 步骤五：多集群统一监控

**目标**：汇聚三个集群的安全指标到统一 Grafana。

**Prometheus Federation 配置**（在全局 Prometheus 中）：

```yaml
# 全局 Prometheus 抓取配置
scrape_configs:
  - job_name: 'trivy-operator-federation'
    honor_labels: true
    metrics_path: '/federate'
    params:
      'match[]':
        - '{job="trivy-operator"}'
    static_configs:
      - targets:
        - 'prometheus-beijing.cloud-sail.internal:9090'
        - 'prometheus-shanghai.cloud-sail.internal:9090'
        - 'prometheus-singapore.cloud-sail.internal:9090'
```

**Grafana 变量配置**：

在 Dashboard 中创建变量 `cluster`，值为：
- `beijing`
- `shanghai`
- `singapore`

所有面板的 PromQL 增加 `cluster` 维度过滤：

```promql
sum by (namespace) (trivy_image_vulnerabilities{cluster="$cluster"})
```

### 步骤六：自动化合规报告

**目标**：每日自动生成集群合规状态报告。

创建 CronJob：

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: compliance-report
  namespace: trivy-system
spec:
  schedule: "0 6 * * *"
  jobTemplate:
    spec:
      template:
        spec:
          serviceAccountName: trivy-scanner
          containers:
          - name: reporter
            image: bitnami/kubectl:latest
            command:
            - /bin/sh
            - -c
            - |
              echo "=== Daily Compliance Report ==="
              echo "Date: $(date)"
              echo ""
              echo "Vulnerabilities by Namespace:"
              kubectl get vulnerabilityreports --all-namespaces -o json | \
                jq -r '.items[] | "\(.metadata.namespace) \(.report.summary.criticalCount) \(.report.summary.highCount)"' | \
                awk '{c[$1]+=$2; h[$1]+=$3} END {for(n in c) print n, c[n], h[n]}'
              echo ""
              echo "Config Audit Issues:"
              kubectl get configauditreports --all-namespaces -o json | \
                jq -r '.items[] | "\(.metadata.namespace) \(.report.summary.criticalCount)"' | \
                awk '{c[$1]+=$2} END {for(n in c) print n, c[n]}'
          restartPolicy: OnFailure
```

### 测试验证

1. 执行 Helm install，确认 `trivy-system` Namespace 下所有 Pod 运行正常。
2. 部署 `vulnerable-workload.yaml`，确认 2 分钟内自动生成 VulnerabilityReport/ConfigAuditReport/ExposedSecretReport。
3. 在 Grafana 中查看 Dashboard，确认漏洞数据正确展示。
4. 触发 `CriticalVulnerabilityInCluster` 告警，验证 Alertmanager 收到通知。
5. 尝试创建 privileged Pod，验证 Kyverno 拒绝创建。
6. 检查 CronJob 输出，确认合规报告包含所有 Namespace 的统计。

---

## 4. 项目总结

### 优点 & 缺点

| 维度 | 优点 | 缺点 |
|------|------|------|
| 持续性 | 7×24 小时监控，实时发现新风险 | 初次部署时会对集群产生一定的扫描负载 |
| 原生集成 | CRD + Prometheus + Grafana，完全融入 K8s 生态 | 需要理解多种 CRD 的关系和查询方式 |
| 自动化 | 新资源创建即扫描，无需人工触发 | 大规模集群（>5000 Pod）下资源消耗显著 |
| 联动能力 | 可与 Kyverno/OPA 配合实现准入 + 审计 | 不直接修改资源，修复仍需外部工具 |
| 多集群 | Metrics 可汇聚到全局 Grafana | CRD 报告本身不自动汇聚，需额外方案 |

### 适用场景

1. **生产集群持续合规**：满足等保/SOC2 对「运行态持续监控」的要求。
2. **配置漂移检测**：发现手工 `kubectl edit` 或 helm upgrade 引入的配置违规。
3. **新漏洞应急响应**：trivy-db 更新后，Operator 自动重扫，发现「老镜像新漏洞」。
4. **多集群安全态势感知**：全局 Grafana 展示各地域集群的安全对比。
5. **DevSecOps 闭环**：扫描 → Metrics → 告警 → 工单 → 修复 → 验证，全链路自动化。

**不适用场景**：
1. 小型测试集群（<10 Pod）——Operator 的部署和维护成本可能超过收益。
2. 需要检测运行时攻击（如内存注入、系统调用异常）——需配合 Falco、Tetragon。

### 注意事项

- **Scan Job 资源限制**：默认 Scan Job 没有资源限制，在大镜像扫描时可能耗尽节点资源。建议在 Helm values 中设置 `scanJobPodTemplateSecurityContext` 和 `scanJobPodTemplateContainerSecurityContext`。
- **报告 CRD 的存储压力**：大量 VulnerabilityReport 会占用 etcd 空间。建议配置 `operator.vulnerabilityScannerReportTTL` 自动清理过期报告。
- **镜像拉取策略**：Scan Job 需要从 Registry 拉取镜像，确保集群节点有网络权限和 Registry 认证。
- **RBAC 最小化**：Trivy Operator 需要较高的集群权限（读取所有 Namespace 的 Secret/Pod/Deployment），部署前需安全团队审批。

### 常见踩坑经验

**踩坑案例 1：Operator 安装后没有生成报告**
- **现象**：部署 Pod 后，没有自动创建 VulnerabilityReport。
- **根因**：Operator 默认只扫描特定 Namespace（如 `default`），或 Scan Job 因资源不足被挂起。
- **解法**：检查 Operator 日志 `kubectl logs -n trivy-system deployment/trivy-operator`；确认 `targetNamespaces` 配置正确；检查 Scan Job 状态。

**踩坑案例 2：Prometheus 抓取不到指标**
- **现象**：Grafana 面板显示 No Data。
- **根因**：ServiceMonitor 的 `release` label 与 Prometheus Operator 的 `serviceMonitorSelector` 不匹配。
- **解法**：确认 Prometheus 的 `serviceMonitorNamespaceSelector` 包含 `trivy-system`；检查 ServiceMonitor 的 label 是否与 Prometheus 配置一致。

**踩坑案例 3：etcd 空间被 CRD 占满**
- **现象**：集群 APIServer 响应变慢，etcd 告警磁盘占用高。
- **根因**：VulnerabilityReport 数量爆炸（每个 Pod 一个报告，且长期不清理）。
- **解法**：设置 `operator.vulnerabilityScannerReportTTL=24h`，让 Operator 自动删除 24 小时前的报告；或使用 CronJob 定期清理。

### 思考题

1. 假设你的生产集群有 3000 个 Pod，Trivy Operator 每天生成 3000 个 VulnerabilityReport。请设计一个「报告归档」方案：如何在不影响 Operator 正常工作的情况下，将历史报告自动导出到外部存储（如 S3）并从 etcd 中清理？
2. Trivy Operator 的 Scan Job 会拉取生产镜像，这可能暴露私有 Registry 的凭据给扫描 Pod。请设计一个「安全扫描隔离」方案：如何确保 Scan Pod 在独立的、受限制的 Namespace 中运行，且无法访问生产网络的敏感资源？

> **答案提示**：第 28 章「监控告警与通知体系集成」将深入介绍 Prometheus + Alertmanager 的高级告警路由；第 38 章「极端场景优化」将探讨大规模集群的 Operator 资源调优。

---

> **推广计划**：本章是 SRE 和 K8s 平台团队的必读内容。建议在所有生产集群部署 Trivy Operator，开发集群可选部署。安全团队负责配置扫描策略基线（Severity 阈值、Scanner 开关）。SRE 团队将 Operator Metrics 接入现有 Prometheus + Grafana，并在值班手册中增加 Trivy 告警的响应流程。开发团队了解 VulnerabilityReport 的查询方式，在收到告警时能快速定位受影响的 Pod。合规团队将 Operator 的持续扫描日志作为审计证据，满足等保 2.0 的持续监控要求。
