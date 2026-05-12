# 第40章：【高级篇综合实战】构建安全高性能企业级 Harbor 平台

## 1 项目背景

某大型金融科技集团"盛融科技"决定全面拥抱云原生，从商业Registry方案（JFrog Artifactory）迁移到自建Harbor平台。项目代号"Haven"——Harbor Advanced Verification & Enterprise Adoption。集团规模大、要求高、时间紧。

**组织规模：**
- 3000+ 开发人员，分布于5个业务线、20个产品团队
- 5000+ 微服务，运行在10000+ K8s Pod上
- 全球5个Region（北京、法兰克福、弗吉尼亚、新加坡、圣保罗）
- 日均镜像推送 1500+ 次，日均镜像拉取 80000+ 次

**SLA硬性要求：**
- 可用性 ≥ 99.99%（全年停机不超过52分35秒）
- P99 API延迟 < 200ms，单Region支持5000+ QPS
- 安全合规：等保三级 + SOC2 Type II + PCI DSS
- 规模：支持全球5 Region、1000+项目、100万+制品

**痛点零（项目启动）——从零到企业级的全景挑战：**
- **架构设计缺失**：没有任何Harbor建设经验，需要从一片空白中设计全球分布式架构
- **安全合规高压**：金融行业监管要求镜像内容必须签名、CVE扫描、操作全审计——缺一不可
- **团队经验断层**：50人DevOps团队中只有3人接触过Harbor（仅限于单机部署）
- **时间窗口紧张**：商业Registry的授权许可3个月后到期，必须在到期前完成迁移和验证

**痛点一：商业Registry授权许可即将到期**——Artifactory企业版每年授权费接近300万元，且最近的续约提案中价格上浮40%。CIO要求3个月内完成替换，否则面临巨额续约成本。

**痛点二：安全合规缺口**——当前商业Registry方案不支持镜像签名（Cosign/Notary），CVE扫描依赖外部工具集成不稳定。等保三级审计时被指出"无法证明镜像内容的完整性和来源可信度"。

**痛点三：全球分发延迟严重影响海外业务**——圣保罗的开发团队拉取基础镜像需要从弗吉尼亚传输——延迟超过300ms，1GB镜像需要约5分钟。双11期间拉取超时导致的部署失败占故障总数的15%。

**痛点四：运维自动化水平低**——当前镜像仓库运维靠"人工巡检+手动修复"——删除旧镜像靠DBA写脚本、权限管理靠Jira工单流程。No SLO（服务水平目标）、No Runbook（故障处理手册）、No Chaos Engineering（混沌工程验证）。

本章将前39章的知识融会贯通，构建一个真正的企业级Harbor平台，从架构设计到SRE落地的完整实战。

---

## 2 项目设计——剧本式交锋对话

**场景：项目启动会。白板前站着架构师老陈（大师），左右坐着小胖（SRE工程师）和小白（安全架构师）。CTO远程视频接入。**

**大师**（在白板上画出五个方框）："Haven项目不是简单地'装一个Harbor'——它是把前39章的知识系统性地应用到一个真实的企业级场景中。我们分五个阶段推进，每个阶段有明确的交付物和验收标准。"

```
┌─ Haven 五阶段蓝图 ────────────────────────────────────────────────┐
│                                                                   │
│  Phase 1: 架构设计    (Week 1-2)                                  │
│  ├── 全球拓扑规划（5 Region Full Mesh 复制拓扑）                   │
│  ├── 存储选型（每个Region的本地对象存储）                          │
│  ├── 网络规划（内网DNS GEO路由 + 跨境专线带宽规划）                │
│  └── 安全架构（mTLS + RBAC矩阵 + CVE门禁 + 签名 + 审计）          │
│                                                                   │
│  Phase 2: 核心部署    (Week 3-4)                                  │
│  ├── 5 Region Harbor via K8s Helm（高可用部署）                    │
│  ├── PostgreSQL HA（Patroni 3节点 + etcd）                        │
│  ├── Redis Sentinel × 3 节点（Sentinel 3 + Redis 2）              │
│  └── 共享存储（AWS S3 / 阿里云OSS / GCP GCS per Region）          │
│                                                                   │
│  Phase 3: 安全加固    (Week 5-6)                                  │
│  ├── LDAP 企业统一认证 + OIDC SSO                                 │
│  ├── 细粒度RBAC（150+项目、800+成员、15+角色模板）                 │
│  ├── CVE门禁（Critical阻塞 + High白名单审批）                      │
│  ├── Cosign 镜像签名 + Rekor 透明日志                             │
│  └── 审计日志 → SIEM（Splunk）集成                                │
│                                                                   │
│  Phase 4: 自动化运维  (Week 7-8)                                  │
│  ├── Prometheus + Grafana + Alertmanager（全组件监控）             │
│  ├── SLO 驱动告警（99.99%可用性 / P99 < 200ms / Error Rate < 0.1%）│
│  ├── 自动化备份（CronJob → S3，每日增量 + 每周全量）               │
│  ├── 灾难恢复演练（每月一次，RPO < 1h, RTO < 30min）              │
│  └── GitOps（ArgoCD 管理 Harbor 配置，配置即代码）                 │
│                                                                   │
│  Phase 5: SRE 落地     (Week 9-10)                                │
│  ├── Runbook（10个常见故障SOP，每季度Review更新）                  │
│  ├── 容量规划（存储/网络增长预测模型，月度review）                 │
│  ├── Chaos Engineering（随机杀Pod、断网、磁盘满——测试自愈）        │
│  └── 持续优化（月度性能Review + 季度架构Review）                  │
│                                                                   │
└───────────────────────────────────────────────────────────────────┘
```

**小胖**："两周一个阶段？总共10周？这也太赶了吧！我们团队才50个人，还得同时维护现在的Artifactory——不可能完成的。"

**大师**："每个阶段的耗时是目标时间，实际执行中Phase 2和Phase 3可以部分并行——Phase 2部署完成后，安全加固可以分Region逐步推进，不用等全部Region部署完才开始。而且我们的策略是'先跑起来再优化'——Phase 1不必写出完美的架构文档，而是产出'可操作的架构决策'——用什么存储、什么拓扑、什么认证方式——做决定而不是分析。"

**小白**："每个阶段的验收标准是什么？CTO要求可量化的交付物，不能是'差不多'就过了。"

**大师**（在白板上画出第二栏）："每个阶段一个'闸门（Gate）'——必须达成的验收标准，过不了闸门不能进入下一阶段："

| 阶段 | 闸门验收标准 | 验证方式 | 责任人 |
|------|------------|---------|--------|
| Phase 1 架构设计 | 架构文档通过安全+运维+开发三方Review签字；存储选型有POC基准测试数据支持 | 邮件审批 + POC测试报告 | 大师 |
| Phase 2 核心部署 | 5 Region Harbor全部绿灯（Health Check OK）；跨Region复制延迟 < 30秒（100MB镜像） | 自动化验收脚本（见3.6） | 小胖 |
| Phase 3 安全加固 | 渗透测试通过（无中危及以上漏洞）；CVE门禁生效（Critical CVE push被阻塞）；审计日志成功写入Splunk | 安全团队PenTest报告 + Splunk Dashboard截图 | 小白 |
| Phase 4 自动化运维 | Grafana大盘全绿连续7天；SLO达标连续7天；DR演练成功（RTO实测 < 30min） | Grafana截图 + DR演练报告 | 小胖 |
| Phase 5 SRE落地 | 10个Runbook全部经过实战验证（人工注入故障→按Runbook执行→恢复成功）；容量预测准确率 > 85% | 故障演练报告 + 容量复盘 | 大师 |

**小胖**："那如果Phase 3渗透测试没通过——比如发现了3个中危漏洞——后面的阶段延迟吗？"

**大师**："不延迟——但漏洞必须在Phase 3修复，Phase 4可以按计划开始（自动化运维和安全加固不互斥）。关键是**闸门严格、进度灵活**——标准不妥协，但执行可以调整并行度。这就是'SRE文化'——不是银弹，而是持续改进的过程。"

**技术映射**：Haven项目的整体架构遵循"全球多活"模式——每个Region的Harbor完整独立（8组件齐全），通过Full Mesh复制拓扑保持镜像一致性。网络层使用GeoDNS将`harbor.company.com`智能解析到最近的Region。K8s部署使用Helm Chart + Region-specific values文件实现"一套模板、多套配置"。

---

## 3 项目实战

### 环境要求

| 组件 | 版本 | 说明 |
|------|------|------|
| Harbor | v2.12.x | 5 Region统一版本 |
| Kubernetes | v1.28+ | 各Region独立K8s集群 |
| Helm | v3.14+ | Harbor Helm Chart部署 |
| PostgreSQL | 14.x (via Patroni) | HA模式（3节点） |
| Redis | 7.x (via Sentinel) | HA模式（3 Sentinel + 2 Redis） |
| MinIO / AWS S3 / 阿里云OSS | 各Region本地选择 | Registry存储后端 |
| Prometheus + Grafana | latest stable | 监控告警 |
| cert-manager | v1.14+ | TLS证书自动管理 |

### 3.1 Phase 1：全球拓扑规划与架构决策

**目标**：产出可执行的架构决策文档，明确全球拓扑、存储选型、网络规划。

**决策一：复制拓扑——Full Mesh vs Hub-Spoke？**

```
Full Mesh（最终选择）:
  优点:
  ✅ 任一Region故障不影响其他Region之间的复制
  ✅ 无中心节点瓶颈（带宽、CPU）
  ✅ 任一Region可独立推送镜像（本地自治）
  ✅ 延迟最优——A→B直连而非A→Hub→B
  缺点:
  ❌ 复制规则数 = 5×4 = 20条（需脚本管理）
  ❌ 网络带宽需求 = Hub-Spoke的2倍
  ❌ 环路检测必须严格验证

Hub-Spoke:
  优点: 仅5条规则
  缺点: Hub故障→全局瘫痪；Hub带宽瓶颈；不是所有Region都能独立推送
```

**选择：Full Mesh。** 理由：盛融科技的5个Region都有独立的CI/CD能力（各Region本地构建镜像），需要本地自治。Full Mesh符合"每Region都是公民而非附属"的设计原则。

**决策二：存储选型——每个Region独立选型**

| Region | 云厂商 | 对象存储 | Shard配置 | 内网延迟 | 月费用/TB |
|--------|--------|---------|----------|---------|----------|
| 北京 | 阿里云 | OSS（内网Endpoint） | Standard + 低频（30天自动降级） | < 5ms | ~100元 |
| 法兰克福 | AWS | S3（eu-central-1） | Standard + IA（30天自动降级） | < 10ms | ~120元 |
| 弗吉尼亚 | AWS | S3（us-east-1） | Standard + IA | < 5ms | ~120元 |
| 新加坡 | GCP | GCS（asia-southeast1） | Standard + Nearline | < 10ms | ~130元 |
| 圣保罗 | Azure | Blob Storage | Hot + Cool（7天自动降级） | < 15ms | ~130元 |

**决策三：网络规划——GeoDNS + 专线**

```
harbor.company.com（GeoDNS）
  ├── 北京用户       → A记录 → 10.0.1.100（北京Harbor VIP）
  ├── 欧洲用户       → A记录 → 10.0.2.100（法兰克福Harbor VIP）
  ├── 北美用户       → A记录 → 10.0.3.100（弗吉尼亚Harbor VIP）
  ├── 东南亚用户     → A记录 → 10.0.4.100（新加坡Harbor VIP）
  └── 南美用户       → A记录 → 10.0.5.100（圣保罗Harbor VIP）

跨Region复制网络：
  北  京 ←→ 新加坡     (专线, 80ms RTT, 100Mbps) 
  法兰克福 ←→ 弗吉尼亚  (专线, 120ms RTT, 500Mbps)
  弗吉尼亚 ←→ 圣保罗   (专线, 140ms RTT, 200Mbps)
  新加坡 ←→ 法兰克福   (专线, 160ms RTT, 200Mbps)
  北京 ←→ 法兰克福     (公网VPN备份, 200ms RTT, 50Mbps)
  其余路径依靠中间节点转发（Ring辅助）
```

### 3.2 Phase 2：全Region核心部署

**目标**：5个Region的Harbor全部通过Helm部署上线，PostgreSQL和Redis实现高可用。

```bash
#!/bin/bash
# deploy-all-regions.sh — 批量部署5个Region的Harbor
set -euo pipefail

REGIONS=("bj" "eu" "us" "sg" "br")
REGION_NAMES=("beijing" "frankfurt" "virginia" "singapore" "sao-paulo")
NAMESPACE="harbor"

for i in "${!REGIONS[@]}"; do
  region="${REGIONS[$i]}"
  region_name="${REGION_NAMES[$i]}"
  
  echo "============================================"
  echo "Deploying Harbor to Region: $region_name ($region)"
  echo "============================================"

  # 1. 切换到目标K8s集群
  kubectl config use-context "k8s-${region}"

  # 2. 创建Namespace
  kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -

  # 3. 创建TLS证书（使用cert-manager）
  kubectl apply -f - <<YAML
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: harbor-tls
  namespace: $NAMESPACE
spec:
  secretName: harbor-tls
  dnsNames:
    - harbor-${region}.company.com
  issuerRef:
    name: letsencrypt-prod
    kind: ClusterIssuer
YAML

  # 4. 部署Harbor（使用Region-specific values覆盖）
  helm upgrade --install harbor harbor/harbor \
    --namespace "$NAMESPACE" \
    --values harbor-values-base.yaml \
    --values "harbor-values-${region}.yaml" \
    --set "expose.ingress.hosts.core=harbor-${region}.company.com" \
    --set "externalURL=https://harbor-${region}.company.com" \
    --timeout 15m \
    --wait

  # 5. 等待所有Pod就绪
  echo "Waiting for all Harbor pods to be ready..."
  kubectl wait --for=condition=Ready pods --all \
    -n "$NAMESPACE" --timeout=10m

  # 6. 健康检查
  HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
    "https://harbor-${region}.company.com/api/v2.0/health")
  
  if [ "$HTTP_CODE" = "200" ]; then
    echo "✅ Harbor $region_name ($region) is HEALTHY"
  else
    echo "❌ Harbor $region_name ($region) health check FAILED (HTTP $HTTP_CODE)"
    exit 1
  fi

  echo ""
done

echo "============================================"
echo "All 5 Regions deployed successfully!"
echo "============================================"
```

```yaml
# harbor-values-base.yaml（通用配置，所有Region共享）
expose:
  type: ingress
  tls:
    enabled: true
    certSource: secret
    secret:
      secretName: harbor-tls
  ingress:
    className: nginx
    annotations:
      cert-manager.io/cluster-issuer: letsencrypt-prod

persistence:
  enabled: true
  imageChartStorage:
    type: s3

database:
  type: external
  external:
    host: postgres-patroni.harbor-db.svc.cluster.local
    port: 5432
    username: harbor
    password: ""  # 从K8s Secret注入
    sslmode: require

redis:
  type: external
  external:
    addr: redis-sentinel.harbor-redis.svc.cluster.local:26379
    sentinelMasterSet: mymaster

metrics:
  enabled: true
  serviceMonitor:
    enabled: true

log:
  level: info
  json: true  # 结构化日志 → Splunk
```

```yaml
# harbor-values-bj.yaml（北京Region特定配置）
externalURL: https://harbor-bj.company.com

imageChartStorage:
  s3:
    region: oss-cn-beijing
    regionendpoint: https://oss-cn-beijing-internal.aliyuncs.com
    bucket: harbor-registry-bj
    accesskey: ""  # 从K8s Secret注入
    secretkey: ""  # 从K8s Secret注入
    secure: true

core:
  replicas: 3
  resources:
    requests: {cpu: 500m, memory: 1Gi}
    limits: {cpu: 2, memory: 4Gi}

jobservice:
  replicas: 1
  workers:
    scan: 3
    replication: 5
    gc: 2
    retention: 1
  resources:
    requests: {cpu: 250m, memory: 512Mi}
    limits: {cpu: 2, memory: 2Gi}

trivy:
  enabled: true
  replicas: 2
  resources:
    requests: {cpu: 100m, memory: 1Gi}
    limits: {cpu: 1, memory: 4Gi}

registry:
  replicas: 2
  resources:
    requests: {cpu: 500m, memory: 512Mi}
    limits: {cpu: 2, memory: 2Gi}
```

### 3.3 Phase 3：安全加固与合规验证

**目标**：部署全套安全组件，满足等保三级和SOC2合规要求。

**关键安全组件部署清单：**

| 组件 | 部署方式 | 用途 | 验证方法 |
|------|---------|------|---------|
| LDAP集成 | Core配置 | 企业统一认证 | `ldapsearch` 测试连接 |
| OIDC集成 | Core配置 | SSO单点登录 | Portal OIDC按钮登录测试 |
| RBAC矩阵 | API批量配置 | 150+项目权限（800+成员分15个角色） | API导出现有权限矩阵对比 |
| CVE门禁 | Core配置 | Critical CVE阻止push | `docker push`含CVE镜像被拒 |
| Cosign签名 | CI/CD集成 | 镜像内容签名 | `cosign verify` 验证签名链 |
| Rekor日志 | 独立部署 | 签名透明性（不可篡改的签名记录） | Rekor API查询签名条目 |
| SIEM集成 | Log Forwarder | 审计日志 → Splunk | Splunk查询 "index=harbor" |

```bash
# 验证CVE门禁生效
# 准备一个包含Critical CVE的测试镜像
docker pull alpine:3.12.0  # 该版本已知有Critical CVE

# 打标签并尝试push到Harbor
docker tag alpine:3.12.0 harbor-bj.company.com/order-platform/vuln-test:v1
docker push harbor-bj.company.com/order-platform/vuln-test:v1 2>&1

# 预期输出（CVE门禁生效）：
# denied: The artifact has 1 critical vulnerabilities.
# Critical CVEs: CVE-2021-36159 (libcrypto1.1)
# Push blocked by CVE admission policy.

# 验证Cosign签名
cosign sign --key cosign.key harbor-bj.company.com/order-platform/order-service:v1.0.0
cosign verify --key cosign.pub harbor-bj.company.com/order-platform/order-service:v1.0.0

# 预期输出：
# Verification for harbor-bj.company.com/order-platform/order-service:v1.0.0 --
# The following checks were performed on each of these signatures:
#   - The cosign claims were validated
#   - The signatures were verified against the specified public key
```

### 3.4 Phase 4：自动化运维与SLO监控

**目标**：建立全组件监控、SLO告警、自动化备份和灾难恢复体系。

```yaml
# SLO配置（Harbor API服务）
# 这些SLO用于Alertmanager告警规则
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: harbor-slo-alerts
  namespace: harbor
spec:
  groups:
  - name: harbor-api-slo
    rules:
    # 可用性告警：30天滚动窗口，Error Budget消耗速率
    - alert: HarborHighErrorBurnRate
      expr: |
        (
          rate(harbor_api_requests_total{status=~"5.."}[1h])
          /
          rate(harbor_api_requests_total[1h])
        ) > 0.02
      for: 3m
      labels:
        severity: critical
        service: harbor-api
      annotations:
        summary: "Harbor API Error Budget burn rate > 14x (2% in 1h)"
        description: "Harbor {{ $labels.instance }} error rate is {{ $value | humanizePercentage }} over last 1h"

    # P99延迟告警
    - alert: HarborHighLatencyP99
      expr: |
        histogram_quantile(0.99,
          rate(harbor_api_request_duration_seconds_bucket[5m])
        ) > 0.2
      for: 10m
      labels:
        severity: warning
        service: harbor-api
      annotations:
        summary: "Harbor API P99 latency exceeds 200ms"
        description: "Current P99 latency: {{ $value }}s for {{ $labels.handler }}"

    # Redis队列积压告警
    - alert: HarborJobQueueBacklog
      expr: |
        max by(queue) (harbor_job_queue_length) > 100
      for: 15m
      labels:
        severity: warning
      annotations:
        summary: "Harbor job queue {{ $labels.queue }} has {{ $value }} pending jobs"
        description: "JobService Workers may be insufficient or overloaded"
```

```bash
# 自动化备份CronJob（K8s Native）
kubectl apply -f - <<'YAML'
apiVersion: batch/v1
kind: CronJob
metadata:
  name: harbor-postgres-backup
  namespace: harbor
spec:
  schedule: "0 2 * * *"  # 每天凌晨2点
  successfulJobsHistoryLimit: 7
  failedJobsHistoryLimit: 3
  jobTemplate:
    spec:
      template:
        spec:
          containers:
          - name: backup
            image: postgres:14-alpine
            command:
            - /bin/sh
            - -c
            - |
              pg_dump -h $PG_HOST -U $PG_USER -d registry \
                --exclude-table=audit_log \
                --format=custom --compress=9 \
                | aws s3 cp - s3://harbor-backups-bj/postgres/$(date +%Y%m%d-%H%M).dump
              echo "Backup completed: $(date)"
            env:
            - name: PG_HOST
              value: postgres-patroni.harbor-db.svc.cluster.local
            - name: PG_USER
              value: harbor
            - name: PGPASSWORD
              valueFrom:
                secretKeyRef:
                  name: harbor-database-secret
                  key: password
          restartPolicy: OnFailure
YAML
```

### 3.5 Phase 5：SRE Runbook 与 混沌工程

**目标**：建立故障处理SOP、通过人为注入故障验证平台韧性。

**故障处理Runbook示例：Harbor Core CrashLoopBackOff**

```bash
#!/bin/bash
# runbook-core-crash.sh
# SOP: Harbor Core 反复重启（CrashLoopBackOff）

REGION=$1
NAMESPACE="harbor"
CONTEXT="k8s-${REGION}"

echo "=== Runbook: Harbor Core CrashLoopBackOff (Region: $REGION) ==="

# Step 1: 确认故障范围
echo "[1/6] Checking pod status..."
kubectl --context "$CONTEXT" get pods -n "$NAMESPACE" | grep core
# 预期输出：harbor-core-xxx 0/1 CrashLoopBackOff

# Step 2: 查看最近日志
echo "[2/6] Fetching recent logs..."
kubectl --context "$CONTEXT" logs -n "$NAMESPACE" \
  --tail=50 --previous \
  $(kubectl --context "$CONTEXT" get pods -n "$NAMESPACE" -l app=harbor,component=core -o name | head -1)

# Step 3: 分类常见根因
# a. PostgreSQL连接失败 → 日志包含 "connect: connection refused"
# b. harbor.yml配置错误 → 日志包含 "invalid configuration"
# c. 内存OOM → Pod events显示 "OOMKilled"
# d. 密钥文件缺失 → 日志包含 "no such file: private_key.pem"

# Step 4: 对应修复
# Case a: PostgreSQL故障
#   → 切换到PostgreSQL Runbook
#   → kubectl --context "$CONTEXT" get pods -n "$NAMESPACE" | grep database
# Case b: 配置错误
#   → kubectl --context "$CONTEXT" get configmap -n "$NAMESPACE" harbor-core -o yaml
#   → 检查harbor.yml语法（./prepare --validate）
# Case c: 内存OOM
#   → kubectl --context "$CONTEXT" edit deployment harbor-core -n "$NAMESPACE"
#   → 增大 resources.limits.memory
# Case d: 密钥缺失
#   → kubectl --context "$CONTEXT" get secrets -n "$NAMESPACE" | grep core
#   → 重新创建缺失的Secret

# Step 5: 验证恢复
echo "[5/6] Verifying recovery..."
sleep 30
HTTP_CODE=$(kubectl --context "$CONTEXT" exec -n "$NAMESPACE" \
  deploy/harbor-core -- curl -s -o /dev/null -w "%{http_code}" \
  http://localhost:8080/api/v2.0/health)
if [ "$HTTP_CODE" = "200" ]; then
  echo "✅ Harbor Core recovered successfully"
else
  echo "❌ Recovery incomplete (HTTP $HTTP_CODE)"
fi

# Step 6: 事后报告
echo "[6/6] Post-incident: Create ticket in #harbor-incidents Slack channel"
echo "  - Incident time window"
echo "  - Root cause identified"
echo "  - Time to resolve (MTTR)"
echo "  - Preventive action"
```

**混沌工程验证：随机杀Pod测试自愈能力**

```bash
#!/bin/bash
# chaos-test.sh
# 混沌工程：随机删除Harbor Pod，验证K8s自愈和Harbor容错

NAMESPACE="harbor"
REGION="bj"
CONTEXT="k8s-${REGION}"

echo "=== Chaos Engineering Test: Kill Random Harbor Pods ==="
echo "Region: $REGION"
echo "WARNING: This test runs in production. Ensure SLO monitoring is active."

# 场景1：随机杀一个Core Pod（应不影响服务——其他Core实例接管）
TARGET_POD=$(kubectl --context "$CONTEXT" get pods -n "$NAMESPACE" \
  -l app=harbor,component=core -o name | shuf -n 1)
echo "[1] Killing: $TARGET_POD"
kubectl --context "$CONTEXT" delete -n "$NAMESPACE" "$TARGET_POD" --grace-period=1

# 立即验证API可用性
sleep 3
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
  "https://harbor-${REGION}.company.com/api/v2.0/health")
echo "  API health after kill: HTTP $HTTP_CODE"
# 预期：200（负载均衡器自动剔除死Pod，转发到健康实例）

# 场景2：杀JobService Pod（应不影响push/pull）
TARGET_JS=$(kubectl --context "$CONTEXT" get pods -n "$NAMESPACE" \
  -l app=harbor,component=jobservice -o name | head -1)
echo "[2] Killing: $TARGET_JS"
kubectl --context "$CONTEXT" delete -n "$NAMESPACE" "$TARGET_JS" --grace-period=1

# 等待新Pod启动
kubectl --context "$CONTEXT" wait --for=condition=Ready pod \
  -l app=harbor,component=jobservice -n "$NAMESPACE" --timeout=5m

# 验证扫描任务恢复
sleep 10
PENDING=$(kubectl --context "$CONTEXT" exec -n "$NAMESPACE" \
  deploy/harbor-core -- curl -s -u admin:pass \
  "https://harbor-${REGION}.company.com/api/v2.0/jobservice/stats" | \
  jq -r '.pending_jobs')
echo "  Pending jobs after recovery: $PENDING"
# 预期：pending_jobs恢复至正常水平

echo "=== Chaos test completed. Verify SLO metrics in Grafana ==="
```

### 3.6 最终验收——Haven Acceptance Test

**目标**：自动化验收测试脚本，一次性验证所有关键SLA指标。

```bash
#!/bin/bash
# haven-acceptance-test.sh
# 全Region验收测试——所有闸门通过的最终验证

set -euo pipefail
REGIONS=("bj" "eu" "us" "sg" "br")

echo "╔══════════════════════════════════════════╗"
echo "║     Haven Acceptance Test Suite v1.0      ║"
echo "╚══════════════════════════════════════════╝"
echo ""

PASSED=0
FAILED=0

# Test 1: 全局可用性（所有Region Health Check）
echo "=== Test 1: Global Availability ==="
for region in "${REGIONS[@]}"; do
  HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
    --connect-timeout 5 --max-time 10 \
    "https://harbor-${region}.company.com/api/v2.0/health")
  if [ "$HTTP_CODE" = "200" ]; then
    echo "  ✅ Region $region: HTTP $HTTP_CODE"
    ((PASSED++))
  else
    echo "  ❌ Region $region: HTTP $HTTP_CODE"
    ((FAILED++))
  fi
done

# Test 2: 跨Region复制延迟（Push到北京 → 60秒后检查所有Region）
echo ""
echo "=== Test 2: Cross-Region Replication Lag ==="
TEST_IMAGE="harbor-bj.company.com/haven-test/acceptance:$(date +%s)"
docker tag alpine:latest "$TEST_IMAGE"
docker push "$TEST_IMAGE" > /dev/null 2>&1

# 获取源digest
SRC_DIGEST=$(curl -s -u admin:Str0ng@Admin2024 \
  "https://harbor-bj.company.com/api/v2.0/projects/haven-test/repositories/acceptance/artifacts/$(echo $TEST_IMAGE | cut -d: -f2)" | \
  jq -r '.digest')

echo "Source digest: ${SRC_DIGEST:0:19}..."

# 等待并检查每个Region
echo "Waiting 60s for replication..."
sleep 60

for region in "${REGIONS[@]}"; do
  if [ "$region" = "bj" ]; then continue; fi
  
  DST_DIGEST=$(curl -s -u admin:Str0ng@Admin2024 \
    "https://harbor-${region}.company.com/api/v2.0/projects/haven-test/repositories/acceptance/artifacts/$(echo $TEST_IMAGE | cut -d: -f2)" | \
    jq -r '.digest')
  
  if [ "$SRC_DIGEST" = "$DST_DIGEST" ] && [ -n "$SRC_DIGEST" ] && [ "$SRC_DIGEST" != "null" ]; then
    echo "  ✅ Region $region: Digest matched (synced in < 60s)"
    ((PASSED++))
  else
    echo "  ❌ Region $region: Digest MISMATCH. SRC=${SRC_DIGEST:0:10}... DST=${DST_DIGEST:0:10}..."
    ((FAILED++))
  fi
done

# Test 3: CVE门禁（push带Critical CVE镜像应被拒绝）
echo ""
echo "=== Test 3: CVE Admission Gate ==="
PUSH_OUTPUT=$(docker push harbor-bj.company.com/haven-test/vuln-check:v1 2>&1 || true)
if echo "$PUSH_OUTPUT" | grep -qi "blocked\|denied\|vulnerabilities"; then
  echo "  ✅ CVE gate BLOCKED vulnerable image"
  ((PASSED++))
else
  echo "  ❌ CVE gate did NOT block vulnerable image"
  ((FAILED++))
fi

# Test 4: SLO监控验证
echo ""
echo "=== Test 4: SLO Monitoring ==="
SLO_CHECK=$(curl -s "https://grafana.company.com/api/dashboards/uid/haven-slo" \
  -H "Authorization: Bearer $GRAFANA_API_KEY" | \
  jq '.dashboard.panels[] | select(.title | contains("Availability")) | .title')
if [ -n "$SLO_CHECK" ]; then
  echo "  ✅ SLO dashboard active"
  ((PASSED++))
else
  echo "  ❌ SLO dashboard not found"
  ((FAILED++))
fi

# 汇总
echo ""
echo "╔══════════════════════════════════════════╗"
echo "║   Results: $PASSED Passed, $FAILED Failed           ║"
if [ "$FAILED" -eq 0 ]; then
  echo "║   ✅ ALL ACCEPTANCE TESTS PASSED          ║"
else
  echo "║   ❌ $FAILED TESTS FAILED - INVESTIGATE    ║"
fi
echo "╚══════════════════════════════════════════╝"
```

---

## 4 项目总结

### 4.1 五阶段交付物总结

| 阶段 | 耗时 | 核心交付物 | 关键技术决策 | 验证方式 | 遗留风险 |
|------|------|----------|------------|---------|---------|
| Phase 1 架构设计 | 2周 | 全球拓扑图、存储选型报告、网络规划图 | Full Mesh拓扑、Region独立存储 | 三方Review签字 | 圣保罗带宽不足——暂用压缩传输 |
| Phase 2 核心部署 | 3周 | 5 Region Harbor全部上线 | K8s Helm + 云对象存储 + Patroni PG HA | Health Check全绿 | 圣保罗延迟略高(15ms) |
| Phase 3 安全加固 | 2周 | 安全审计通过报告 | Cosign替代Notary、Splunk SIEM已接入 | 渗透测试零高危 | Rekor日志保留策略待定 |
| Phase 4 自动化运维 | 2周 | 监控大盘+告警规则+备份+DR方案 | SLO驱动告警、CronJob自动备份 | DR演练成功RTO 22min | 备份恢复自动化待脚本化 |
| Phase 5 SRE落地 | 1周 | 10个Runbook + Chaos Engineering | Chaos每月1次、Runbook季度Review | 全部Runbook验证通过 | 部分Runbook仅验证2/5 Region |
| **总计** | **10周** | **企业级Harbor平台建成** | **SLA 99.99%达标** | **全部闸门通过** | **圣保罗需后续优化** |

### 4.2 全专栏知识映射——前39章在Haven项目中的落地

| 专栏章节 | 章节主题 | 在Haven项目中的具体应用 |
|---------|---------|----------------------|
| 第1-2章 | Harbor介绍与部署 | Phase 2 全Region标准化Helm部署 |
| 第3-4章 | Portal控制台与项目管理 | Phase 2 创建150+项目、800+成员的框架 |
| 第5-7章 | 镜像推送/制品管理/Helm Chart | Phase 2 CI/CD集成规范 |
| 第8章 | 复制引擎 | Phase 1 全Mesh复制拓扑设计 |
| 第9章 | 漏洞扫描 | Phase 3 CVE门禁（Critical阻止+High白名单） |
| 第10-11章 | 用户管理与RBAC | Phase 3 15个角色的权限矩阵 + LDAP/OIDC |
| 第12章 | 垃圾回收 | Phase 4 自动GC策略（定时+阈值触发） |
| 第13-15章 | API/日志/运维 | Phase 4 监控采集 + Phase 5 Runbook |
| 第16-21章 | 中级篇（架构、认证、数据模型、存储、复制） | Phase 1-3 的所有架构决策依据 |
| 第22-31章 | 高级篇上（HA、监控、P2P、扩展） | Phase 2-4 K8s HA + Prometheus + 镜像预热 |
| 第32-39章 | 高级篇下（源码定制、插件开发） | Phase 3 认证扩展 + 安全定制 + Phase 5 源码级故障排查 |

### 4.3 适用场景与不适用场景

**适用场景：**
- **大型企业从商业Registry迁移**：完整的评估→部署→加固→运维→SRE路径，方法论可直接复用
- **全球化多Region部署**：5 Region Full Mesh架构可作为模板扩展——每增加1个Region，只需增加N条复制规则
- **金融行业合规要求**：等保三级+SOC2+PCI DSS的安全加固方案可复用——LDAP+CVE门禁+Cosign签名+SIEM审计
- **从零建设DevOps平台**：10周从零到企业级的路径可作为项目计划模板
- **现有Harbor升级优化**：可选择性地应用某几个Phase（如仅Phase 3安全加固）来增强已有Harbor

**不适场景：**
- **小型团队（<20人、<200个镜像）**：Haven方案是面向3000+人的企业级规模——小团队用单机Docker Compose + Local FS即可，过度设计浪费资源
- **所有Region在同城/同机房**：如果所有服务都在一个数据中心，Full Mesh是过度设计——单Region高可用部署即可

### 4.4 注意事项

1. **Full Mesh拓扑的复制规则数量 = N×(N-1)**——5个Region有20条规则，10个Region有90条。务必用Terraform/脚本管理规则配置，绝不能依赖Portal手动操作
2. **SLO的Error Budget是"安全气囊"而不是"目标"**——99.99%可用性意味着每月最多4.38分钟不可用。团队应把Error Budget当作风险管理工具：消耗了多少Budget = 可承受多少风险
3. **混沌工程不能随机搞**——每次混沌实验必须先通知所有团队、确认SLO监控正常、确保有回滚方案。混沌实验的目的是验证——不是制造故障
4. **安全审计不是一次性**——CVE数据库每天更新，上月标记为安全的镜像本月可能变成高危。每季度重新扫描所有镜像（尤其是Active使用的标签）
5. **Runbook的生命周期和代码一样**——Runbook需要版本管理（Git）、需要Review（每季度）、需要演练（每次发布的回归验证）。过期的Runbook比没有Runbook更危险

### 4.5 常见故障速查表

| 故障现象 | 根因 | 快速解决 |
|---------|------|---------|
| Core CrashLoopBackOff | PG不可达、OOM、密钥缺失 | 按Runbook SOP逐项排查 |
| 跨Region复制延迟超30分钟 | 专线抖动、JobService Worker不够 | 检查网络 + 增加replication Worker数 |
| CVE门禁误拦正常镜像 | CVE数据库更新、白名单过期 | 更新白名单配置 + 审查CVE误报 |
| Grafana大盘缺失数据 | ServiceMonitor标签不匹配 | 检查prometheus.io/scrape标签 |
| DR演练RTO > 目标 | 备份文件过大、S3恢复带宽不足 | 优化增量备份 + 保留本地热备份副本 |
| Chaos杀Pod后API出现抖动 | Core实例数过少、Nginx健康检查延迟 | 增加Core实例 + 降低Nginx探活超时 |

### 4.6 深度思考题

1. **在Haven项目中，如果北京Region的OSS存储出现严重性能降级（P99延迟从5ms飙升到3秒），而法兰克福Region一切正常。如何在5分钟内将所有流量从北京切换到法兰克福？需要哪些前提条件（DNS切换、复制对向、镜像预热）？切换后如何保证数据一致性？**

2. **Haven运行一年后，镜像制品数量从50万增长到200万，GC全量扫描需要8小时——超过了夜间维护窗口（4小时）。设计一个"增量GC"方案——只扫描最近变更的Blob（过去24小时内写入或删除的），将GC时间控制在30分钟内。这对Harbor的Blob引用计数机制有什么影响？需要修改哪些数据模型？**

---

## 结语

从第1章"什么是Harbor"到第40章"构建全球多活企业级Harbor平台"，我们完成了从新手到架构师的完整进阶之路。每一章都在解决一个具体的问题——第3章教你创建第一个项目，第17章让你理解微服务架构的职责边界，第21章带你设计跨Region复制拓扑——而本章将所有知识编织成一个可交付的企业级平台。

Harbor不仅仅是一个镜像仓库——它是云原生制品管理的基石，是DevSecOps的枢纽，是企业容器化转型的关键基础设施。无论你的团队是3人还是3000人，无论你的规模是10个镜像还是100万个镜像——专栏中的每一个决策表、每一个故障排障流程、每一个深度思考题，最终都会在某个深夜的生产故障中为你提供解题的钥匙。

> **"在代码的世界里，安全不是终点，而是起点。镜像的每一层，都承载着团队的信任。"**

> 致谢：感谢Harbor开源社区的所有贡献者。Harbor是CNCF毕业项目，如果你从中受益，欢迎参与社区贡献——无论是提交代码、完善文档还是翻译专栏，每一份贡献都在推动云原生基础设施的进步。
