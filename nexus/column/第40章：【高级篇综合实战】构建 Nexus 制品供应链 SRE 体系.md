# 第40章：【高级篇综合实战】构建 Nexus 制品供应链 SRE 体系

## 1. 项目背景

2025 年末，云鲸科技的 CTO 在年度技术峰会上向全公司宣布了一个目标：**"Nexus 的年度可用性达到 99.95%——全年只允许 4.4 小时的非计划停机"**。这个数字让炮哥手中的咖啡差点洒出来——过去 12 个月 Nexus 的累计不可用时间超过了 60 个小时，离 SLO 差了整整一个数量级。

CTO 的决策是有数据支撑的——研发效能报告显示，每当 Nexus 不可用时，每位开发者平均浪费 45 分钟（判断是 Nexus 的问题、手动搭建临时方案、或等待恢复）。200 位开发者 × 全年 8 次故障 × 45 分钟 = 1200 人时——按每小时 300 元的研发成本计算，仅在 Nexus 故障上的直接损失就超过 36 万元。

"我们需要的不是一个'管管仓库的工具'，而是一个**可量化的 SRE 运行体系**——有明确的 SLO、有自动化的容量治理、有故障自愈能力、有事件驱动的安全保障、有按季度的混沌工程验证。"CTO 在全员邮件中写道。

本章作为全专栏的收官之作，将融会贯通基础篇（操作使用）、中级篇（治理架构）、高级篇（源码与扩展）的全部知识，构建一套生产级的 Nexus SRE 运行体系——从 SLO 定义到巡检自动化，从容量自愈到事件驱动安全，从混沌工程到升级灰度，最终交付一份可直接落地到任何企业的 Nexus SRE 运行手册。

## 2. 项目设计

大师召集了全公司技术骨干——炮哥（运维）、老周（架构）、阿玲（前端）、小孙（安全）、小李（核心开发）参加最终评审。

**大师**："SRE 体系的起点不是工具，是 **SLO（Service Level Objective）**。对于 Nexus 来说，我们定义四个 SLO。"

**大师**在白板上写下：

```
SLO-1: 可用性 ≥ 99.95%（全年非计划停机不超过 4.38 小时）
SLO-2: P95 下载延迟 ≤ 500ms（从用户发起请求到收到第一个 byte）
SLO-3: 写入成功率 ≥ 99.99%（upload/deploy/push 操作返回 201 的比例）
SLO-4: 审计日志完整性 = 100%（所有写操作在 audit.log 中有对应记录）
```

**老周**："SLO-1 的 99.95% 怎么算的？"

**大师**："基于 Prometheus 的 `up` 指标。`avg(up{job="nexus"}[365d])` 就是过去一年的可用性。如果低于 99.95%，年度复盘时必须有改进计划。"

**炮哥**："容量自愈呢？磁盘满了 Nexus 自动清理？"

**大师**："分三层。**第一层——预警**：BlobStore 使用率 > 75% 时 Grafana 显示黄色预警。**第二层——自动清理**：使用率 > 85% 时自动触发 Cleanup Task（删除过期 SNAPSHOT 和临时 Docker tag）。**第三层——紧急止血**：使用率 > 95% 且清理不足以释放空间时，进入只读模式（frozen），同时 PagerDuty 告警通知值班人员手动介入。"

**小孙**："事件驱动的安全治理呢？"

**大师**："基于第 27 章的 Webhook + 第 38 章的插件体系。链式防御链——制品上传 → Webhook 触发安全扫描（Snyk/Trivy）→ 扫描结果写入 Nexus 的 asset attribute → 如果发现高危漏洞 → 自动标记组件为 deprecated 并通知维护者。再加上——权限变更实时告警、删除生产组件需要二次确认、异常行为（如凌晨 3 点大量删除）自动熔断。"

**小李**："混沌工程怎么做？万一演练把生产环境搞挂了怎么办？"

**大师**："混沌工程永远在**隔离的演练环境**中执行——不是生产环境。第 30 章的 6 个故障演练剧本可以作为混沌实验的基础。额外加三个——**网络延迟注入**（用 `tc` 命令模拟 500ms 延迟）、**数据库压力注入**（大量并发搜索压测 OrientDB）、**BlobStore 故障注入**（临时将 BlobStore 目录改为只读）。每周随机选取一个实验执行，观察系统的自愈和告警是否按预期工作。"

## 3. 项目实战

### 3.1 环境准备

- 全技术栈就绪：Nexus + Prometheus/Grafana + ELK + Jenkins/GitLab CI + Webhook 接收端
- 前 39 章所有脚本和工具就绪
- 独立演练环境

### 3.2 分步实战

#### 步骤一：定义 SLO 并配置 Burn Rate 告警

**目标**：在 Prometheus 中配置 SLO 指标和错误预算消耗告警。

```yaml
# prometheus-slo.yml — SLO 指标和告警
groups:
  - name: nexus-slo
    rules:
      # SLI: 可用性 = up 指标的平均值
      - record: nexus_sli_availability
        expr: avg_over_time(up{job="nexus"}[28d])

      # SLI: 下载成功率 = 非 5xx 响应占比
      - record: nexus_sli_download_success_rate
        expr: >
          sum(rate(nexus_http_requests_total{status!~"5.."}[28d]))
          / sum(rate(nexus_http_requests_total[28d]))

      # SLO 告警: 错误预算消耗过快
      # 错误预算 = 1 - SLO（如 1 - 99.95% = 0.05% = 0.0005）
      - alert: NexusErrorBudgetBurn
        expr: >
          (1 - nexus_sli_availability) / 0.0005 > 2
        for: 1h
        labels:
          severity: critical
        annotations:
          summary: "Nexus 错误预算消耗过快——当前消耗速度是配额的 2 倍"
          description: "过去 1 小时错误预算消耗率 > 2x，如果持续下去将提前耗尽季度预算"
```

**SLO Dashboard（Grafana 面板）**：

```
Panel: Nexus SLO 仪表盘
  Row 1: 当前 SLO 达标状态（绿色/黄色/红色交通灯）
    - SLO-1 可用性: 99.97% ✅ (目标: 99.95%)
    - SLO-2 P95 延迟: 420ms ✅ (目标: 500ms)
    - SLO-3 写入成功率: 99.993% ✅ (目标: 99.99%)
    - SLO-4 审计完整性: 100% ✅ (目标: 100%)
  
  Row 2: 错误预算消耗趋势（Burn Rate Chart）
    - 本季度总预算: 4.38 小时
    - 已消耗: 1.2 小时 (27%)
    - 消耗速度: 正常
  
  Row 3: SLI 趋势（过去 90 天）
```

#### 步骤二：容量自愈流水线

**目标**：实现 BlobStore 使用率 > 85% 时的自动清理。

```bash
#!/bin/bash
# auto-capacity-healing.sh：容量自愈脚本（由 Prometheus Alertmanager 触发）
NEXUS="http://localhost:8081"
AUTH="admin:admin123"
BLOBSTORE="$1"  # 由告警传入
THRESHOLD="${2:-85}"

LOGFILE="/var/log/nexus-auto-heal.log"

log() { echo "[$(date '+%F %T')] $1" | tee -a "$LOGFILE"; }

log "=== 容量自愈启动 ==="
log "BlobStore: $BLOBSTORE  阈值: ${THRESHOLD}%"

# 1. 检查当前使用率
USAGE=$(curl -s -u "$AUTH" "$NEXUS/service/rest/v1/blobstores/$BLOBSTORE" | \
  jq -r '.softQuota // empty')
log "当前使用率: $USAGE"

# 2. 执行 Cleanup Task（清理过期组件）
log "步骤1: 执行 Cleanup Task..."
TASK_RESP=$(curl -s -u "$AUTH" -X POST "$NEXUS/service/rest/v1/tasks" \
  -H "Content-Type: application/json" \
  -d "{
    \"action\": \"repository.cleanup\",
    \"name\": \"自愈-清理 $BLOBSTORE\",
    \"typeId\": \"repository.cleanup\",
    \"schedule\": \"manual\",
    \"properties\": {\"repositoryName\": \"*\", \"preview\": \"false\"}
  }")

TASK_ID=$(echo "$TASK_RESP" | jq -r '.id')
log "  Cleanup Task 已创建: $TASK_ID"

# 等 Cleanup 完成后执行 Compact
sleep 600  # 等待 10 分钟让 Cleanup 完成

# 3. 执行 Compact BlobStore
log "步骤2: 执行 Compact BlobStore..."
curl -s -u "$AUTH" -X POST "$NEXUS/service/rest/v1/tasks" \
  -H "Content-Type: application/json" \
  -d "{
    \"action\": \"blobstore.compact\",
    \"name\": \"自愈-压缩 $BLOBSTORE\",
    \"typeId\": \"blobstore.compact\",
    \"schedule\": \"manual\",
    \"properties\": {\"blobstoreName\": \"$BLOBSTORE\"}
  }"

log "=== 容量自愈完成 ==="
```

**Alertmanager 集成**：

```yaml
# alertmanager.yml 中添加 webhook receiver
receivers:
  - name: 'auto-heal'
    webhook_configs:
      - url: 'http://auto-heal-runner:8080/trigger'
        send_resolved: false
```

#### 步骤三：自动化巡检脚本

**目标**：每日自动巡检 Nexus 核心健康指标并生成报告。

```bash
#!/bin/bash
# daily-inspection.sh：每日全自动巡检
NEXUS="http://localhost:8081"
AUTH="admin:admin123"
REPORT="/var/log/nexus-inspection-$(date +%Y%m%d).txt"

{
    echo "=== Nexus 每日巡检 ==="
    echo "时间: $(date)"
    echo ""

    # 1. 服务状态
    echo "--- 1. 服务状态 ---"
    curl -s -u "$AUTH" "$NEXUS/service/rest/v1/status" | jq '{status, frozen, writable}'

    # 2. 所有仓库在线状态
    echo ""
    echo "--- 2. 仓库在线状态（含 proxy remote status） ---"
    curl -s -u "$AUTH" "$NEXUS/service/rest/v1/repositories" | \
      jq -r '.[] | "\(.name): \(if .online then "在线" else "离线⚠️" end)"'

    # 3. BlobStore 容量
    echo ""
    echo "--- 3. BlobStore 容量 ---"
    curl -s -u "$AUTH" "$NEXUS/service/rest/v1/blobstores" | \
      jq -r '.[] | "\(.name): \(.totalSize / 1024 / 1024 | floor) MB, \(.blobCount) blobs"'

    # 4. 任务执行状态
    echo ""
    echo "--- 4. 最近失败的任务 ---"
    curl -s -u "$AUTH" "$NEXUS/service/rest/v1/tasks" | \
      jq -r '.items[] | select(.lastRunResult | contains("error") or contains("fail")) | "\(.name): \(.lastRunResult)"'

    # 5. 用户和权限（检测异常）
    echo ""
    echo "--- 5. 用户审计 ---"
    USERS=$(curl -s -u "$AUTH" "$NEXUS/service/rest/v1/security/users" | jq 'length')
    echo "总用户数: $USERS"
    ADMIN_USERS=$(curl -s -u "$AUTH" "$NEXUS/service/rest/v1/security/users" | \
      jq '[.[] | select(.roles[] | contains("nx-admin"))] | length')
    echo "管理员用户数: $ADMIN_USERS"

} > "$REPORT"

echo "巡检报告: $REPORT"
# 如果检测到异常，发送告警
grep -q "⚠️" "$REPORT" && echo "ALERT: 巡检发现异常！"
```

#### 步骤四：升级灰度策略

**目标**：制定 Nexus 版本的灰度升级流程。

```bash
#!/bin/bash
# nexus-canary-upgrade.sh：灰度升级脚本
NEXUS_VERSION="${1:-3.71.0}"

echo "=== Nexus 灰度升级: ${NEXUS_VERSION} ==="
echo ""

echo "Phase 0: 升级前准备"
echo "  [ ] 阅读 Release Notes: https://help.sonatype.com/"
echo "  [ ] 执行全量备份: ./nexus-backup.sh"
echo "  [ ] 在演练环境中完成一次升级演练"
echo "  [ ] 确认所有插件与新版本兼容"
echo ""

echo "Phase 1: 金丝雀升级（10% 流量）"
echo "  [ ] 部署新版 Nexus 实例（金丝雀节点）"
echo "  [ ] Nginx 配置将 10% 的 /repository/ 流量路由到金丝雀节点"
echo "  [ ] 观察 24 小时——监控 SLO 指标和错误日志"
echo "  [ ] 如无异常，进入 Phase 2"
echo ""

echo "Phase 2: 半量升级（50% 流量）"
echo "  [ ] 增加金丝雀节点的流量权重到 50%"
echo "  [ ] 观察 48 小时"
echo ""

echo "Phase 3: 全量升级"
echo "  [ ] 升级所有节点到新版本"
echo "  [ ] 关闭金丝雀节点"
echo "  [ ] 全量监控 1 周"
echo ""

echo "回滚方案（任一步骤出问题）:"
echo "  1. 将流量 100% 切回旧版本节点"
echo "  2. 从备份恢复数据到旧版本"
echo "  3. 复盘问题 → 修复 → 重新执行灰度升级"
```

#### 步骤五：输出 SRE 运行手册

**目标**：将所有规范、脚本、流程整合为一份可交付的 SRE 手册。

```markdown
# Nexus 制品供应链 SRE 运行手册 v1.0

## 手册结构
├── 第1章: SLO 定义与错误预算
│   ├── nexus-slo.yml (4 个 SLO 的 Prometheus 规则)
│   └── slo-dashboard.json (Grafana 面板)
├── 第2章: 日常运维
│   ├── daily-inspection.sh (每日巡检)
│   ├── weekly-maintenance.sh (每周维护)
│   └── capacity-forecast-advanced.sh (容量预测)
├── 第3章: 容量自愈
│   ├── auto-capacity-healing.sh (自动清理)
│   └── blobstore-monitor.sh (存储监控)
├── 第4章: 安全事件响应
│   ├── webhook-alert-rules.yaml (实时告警规则)
│   └── incident-response-playbook.md (事件响应剧本)
├── 第5章: 混沌工程
│   ├── chaos-experiments/ (6+3 个故障剧本)
│   └── drill-scheduler.sh (季度演练计划)
├── 第6章: 升级与回滚
│   ├── nexus-canary-upgrade.sh (灰度升级)
│   └── pre-upgrade-check.sh (升级前检查)
└── 附录: 工具链索引
    ├── nexus-as-code.sh
    ├── perm-check.sh
    ├── nexus-diag.sh
    └── ...
```

### 3.3 常见坑点

| 坑点 | 现象 | 解决方法 |
|------|------|----------|
| SLO 指标计算有误 | 可用性 100% 但用户反馈有中断 | 检查 Prometheus 的 `up` 指标抓取间隔——5 分钟的 scrape_interval 会遗漏 < 5 分钟的故障 |
| 自愈脚本触发过于频繁 | 每天收到 10+ 条自动清理告警 | 在 Alertmanager 中设置 `repeat_interval: 4h` 避免重复发送 |
| 灰度升级回滚时数据不兼容 | 新版 Nexus 写入的 OrientDB 格式旧版无法读取 | 升级前必须全量备份；灰度期间"读写分离"——旧版主节点承担写、新版只读 |

## 4. 项目总结

### 4.1 SRE 体系核心指标

| 维度 | 指标 | 目标 | 当前值 | 状态 |
|------|------|------|--------|------|
| 可靠性 | 可用性 | ≥ 99.95% | 99.97% | ✅ |
| 性能 | P95 下载延迟 | ≤ 500ms | 420ms | ✅ |
| 写入 | 上传成功率 | ≥ 99.99% | 99.993% | ✅ |
| 安全 | 审计完整性 | 100% | 100% | ✅ |
| 容量 | 磁盘告警预警 | > 75% | 72% | ✅ |
| 恢复 | RTO (磁盘满) | ≤ 15min | 12min | ✅ |

### 4.2 全专栏知识体系回顾

| 级别 | 章节范围 | 核心能力 | 角色 |
|------|---------|---------|------|
| 基础篇 | 1-16 | 搭建、配置、权限、API、故障排查 | 新人开发/测试 |
| 中级篇 | 17-31 | 治理架构、CI/CD、可观测性、性能、应急 | 核心开发/运维 |
| 高级篇 | 32-40 | 源码剖析、插件开发、极端优化、SRE 体系 | 架构师/资深开发 |

### 4.3 专栏到此结束

从第 1 章"术语全景与制品仓库工作原理"到第 40 章"构建 SRE 体系"，我们一路走完了——

- 🏗️ **搭建期**（1-7 章）：Docker 部署 → Maven/npm/Docker/Raw 私服 → 格式全通
- 🔐 **治理期**（8-16 章）：权限 → API → 搜索 → 存储 → 清理 → 备份 → 排障 → 综合实战
- 🏢 **平台期**（17-31 章）：仓库规划 → 元数据深水区 → 供应链治理 → 代理缓存 → 存储分层 → 细粒度权限 → CI/CD → IaC → 审计 → Webhook → 监控 → 性能 → 故障演练 → 治理平台
- 🔬 **源码期**（32-36 章）：模块启动 → Format/Facet → 上传链路 → 下载链路 → EventBus/Audit
- 🚀 **SRE 期**（37-40 章）：安全模型 → 插件开发 → 极端优化 → SRE 体系

这不是终点，而是起点。Nexus 版本会持续迭代，企业的制品治理需求会不断演化，但本专栏建立的方法论——**从业务痛点出发 → 理解原理 → 实战落地 → 总结抽象**——将始终适用。

### 4.4 思考题

1. 在 SRE 体系中，"错误预算"是一个核心概念——如果本季度的错误预算在第一个月就消耗了 80%，SRE 团队应该采取什么措施？从技术和管理两个维度回答。
2. 如果你要将本专栏的 SRE 体系从云鲸科技复制到一家新的公司（200 人规模，Java + Node.js 技术栈），哪些部分可以直接复用，哪些需要根据新公司的实际情况调整？请设计一个"3 个月落地路线图"。

（第39章思考题答案：1. 联邦式 Nexus 架构设计：每个地域各部署一个 Nexus 实例（深圳-主、新加坡-从），使用 PRO 版的 Repository Replication 功能实现双向同步——深圳的 `maven-shared-releases` 和新加坡的 `npm-shared-hosted` 互相同步。OSS 版的替代方案：利用 Webhook 监听仓库变更事件，在接收端触发 `curl` 跨实例复制组件（下载 → 上传）。为避免全量复制——只同步 release 仓库，snapshot 和 proxy 缓存不同步；通过 Content Selector 按路径前缀过滤同步范围。2. 搜索迁移到 ES 的同步方案：① 创建 ES 索引模板映射 Component 字段（id、group、name、version、format、repository、timestamp）。② 全量同步——用搜索 API 分页遍历所有 Component，批量写入 ES（初次可能需要数小时）。③ 增量同步——基于第 36 章的 EventBus，编写一个订阅者订阅 `ComponentCreatedEvent` 和 `ComponentDeletedEvent`，收到事件后同步更新 ES。④ 搜索 API 改造——将前端的 Search 请求改为先查 ES（< 50ms），ES 中不存在时 fallback 到 Nexus 原生搜索。⑤ 一致性保证——ES 和 Nexus 数据库之间是最终一致性（< 5 秒），通过定时对账任务检查差异。）

### 4.5 专栏后记

感谢你读到这里。40 章、15 万字、从零到 SRE，我们一起构建了一个企业级制品治理的完整认知体系。如果你在实践中有新的发现、踩了新的坑、或者写了有用的工具——欢迎分享到社区。制品治理之路，永远有优化的空间。

> **全专栏代码与配置仓库**：建议将所有脚本、YAML 配置、Dashboard JSON 整合到公司内部 Git 仓库中，作为团队的知识资产持续维护。
