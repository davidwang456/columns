# 第39章：GitLab SRE 实践——零停机升级与多活架构

## 1. 项目背景

> **业务场景**：某金融科技公司的 GitLab 平台支撑着 2000 名开发人员的日常工作，代码库超过 5 万个仓库。合规部门要求系统 7×24 可用——任何计划内停机都必须提前 14 天申请，且每月累计不得超过 15 分钟。CTO 在董事会上立了军令状："GitLab 版本升级不能停机，主站点故障必须在 5 分钟内完成灾切。"

团队第一次尝试零停机升级时踩了一个经典坑——他们想当然地认为"滚动升级 = 没停机"，于是把 Nginx 负载均衡器下的 3 台 Rails 节点同时摘除，导致 3 分钟的完全停机。更致命的是，先升级的那个节点执行了 `db:migrate`，数据库 schema 变了——新代码兼容新 schema，但另外两个还没升级的旧节点完全不兼容，用户请求路由到旧节点时直接爆 500 错误。第二次尝试 Geo 多活架构时，主站点（北京）突发磁盘故障宕机，团队紧急将备用站点（上海） Promote 为主站点，但发现备用站点上的 Issue 数据比主站点晚了 5 分钟——最近创建的 12 个 Issue 和 3 个 MR 全部丢失，业务方直接投诉到 CIO。

**痛点放大**：SRE 不是"把系统装起来，配通就行"，而是"保证系统在任何条件下都可用"。零停机升级有八个关键控制点——少一个就是停机事故；Geo 多活不是配完就高枕无忧——同步延迟、脑裂防护、回切策略每个环节都可能成为故障点。GitLab SRE 的核心能力就两个字：**可控**——升级过程可控、故障切换可控、数据一致性可控。

## 2. 项目设计——剧本式交锋对话

**场景**：季度 SRE 演练复盘会。运维团队刚完成一轮零停机升级和 Geo 故障切换的实战演练，发现了三个严重问题。小胖把问题清单投影到大屏上，等着大师来"审判"。

---

**小胖**（指着大屏上的第一个问题）："大师，零停机升级不就是一台一台升级吗？为什么我们上次演练还出现了 3 分钟的 500 错误？这和直接停机升级有什么区别？"

**大师**："因为你们搞错了 migration 的执行时机。GitLab 有两类数据库迁移：常规迁移（regular migration）放在 `db/migrate/` 目录下，部署后迁移（post-deployment migration）放在 `db/post_migrate/` 目录下。常规迁移在升级时就执行，但 post-deployment migration 如果提前执行了，数据库 schema 就变了——新的 Rails 代码认识新字段，但还没升级的旧节点不认识，请求路由过去就是 500。正确的做法是：升级过程中设置 `SKIP_POST_DEPLOYMENT_MIGRATIONS=true`，跳过 post-deployment migration，等所有节点都升级完新代码后，再单独执行一次 `gitlab-rake db:migrate`。技术映射——这就像给一栋 30 层的写字楼换电梯：你不能先把 1 楼的电接通（执行 migration），然后让 2-30 楼的旧电梯停在半空——你得先把所有楼层的新电梯轿厢都装好（升级所有节点的代码），最后再统一通电调试（执行 migration）。"

**小胖**（恍然大悟）："所以 post-deployment migration 是一个'最后一步'的操作？"

**大师**："没错。GitLab 设计 post-deployment migration 的初衷就是——这类 migration 可能会改变现有数据的行为（比如重命名列、添加带默认值的新列），如果新旧代码同时运行，数据一致性就崩了。所以 GitLab 的零停机升级的本质是：**代码先行，schema 后行**。"

---

**小白**（推了推眼镜，翻出监控截图）："Geo 的同步延迟呢？Prometheus 显示 PostgreSQL 的流复制延迟只有 200 毫秒，但 Git 仓库的同步延迟却有 5 分钟——为什么差距这么大？"

**大师**："好问题，这涉及到 Geo 的两套同步机制。PostgreSQL 走的是 WAL（Write-Ahead Log）流复制——主库每写入一条记录，WAL 日志几乎实时传输到备库并重放，延迟是秒级的。但 Git 仓库的同步走的是另一条路——Geo 的 RepositorySyncWorker 每隔一段时间（默认是 10 分钟一个批次）才会从主站点拉取仓库的变化。如果某个仓库刚好在你拉取的前一秒收到了一个大 push（比如 2GB 的 LFS 文件），那等它同步完自然就是 5 分钟之后了。另外，Git 仓库的数据量级和数据库不是一个概念——一个仓库可能上百 GB，而数据库的增量 WAL 每秒只有几 KB 到几 MB。技术映射——PostgreSQL 的同步像微信消息，发出去对方几乎秒收；Git 仓库的同步像快递包裹，得凑满一车再发，碰上大件还要多等。"

**小白**："那怎么降低仓库同步的延迟？"

**大师**："三个方向：一是提高 `geo_file_download_dispatch_worker_cron` 的 cron 频率，把默认的每 10 分钟改成每 1 分钟；二是对 Geo 追踪数据库的 `file_registry` 表加索引，加快待同步文件的查询速度；三是针对超大仓库启用 Geo 的 selective sync，只同步核心仓库，非核心仓库走异步备份。但注意——频率越高，主站点的出口带宽压力越大，需要做一个平衡。"

---

**小胖**："故障切换的时候怎么防止脑裂？我听说有个公司 Geo Promote 之后，旧主站点网络恢复了，两个站点同时对外提供写入服务——结果两边各产生了一批 Issue，数据完全没法合并。"

**大师**："这是 Geo 最经典的故障模式——网络分区导致的脑裂（Split-Brain）。Geo 是'单主写'架构——任何时候只有一个 Primary 能接受写入请求，其他 Secondary 都是只读的。Promote 操作的本质是把一个 Secondary 升级为 Primary，这个过程有三道防线。第一道：Promote 之前必须手动确认旧 Primary 已经彻底不可用——不是 ping 不通就 Promote，而是要验证 `gitlab-rake gitlab:check` 全面失败后才操作。第二道：Promote 完成后立即修改 DNS 或 LB 配置，让所有流量指向新 Primary，旧 Primary 即使恢复了也没有流量进来。第三道：旧 Primary 恢复后绝不能直接启动服务——必须先用 `gitlab-rake geo:set_secondary_as_primary --force` 把它降级为 Secondary，或者直接重建。这个思路在分布式系统里有个更形象的名字叫 STONITH（Shoot The Other Node In The Head）——当你不确定一个节点是否真的死了，最安全的做法是'一枪毙了它'，确保它绝对不会再写入数据。技术映射——这道防线就像飞机驾驶舱的'双人确认'机制：副驾驶说'我来接管'之前，必须先确认机长已经失能，而不是两个人同时拉操纵杆。"

**小胖**："所以 Promote 不像数据库的自动故障切换，必须有人工介入？"

**大师**："对。Geo 的设计哲学就是**不要自动 Promote**。因为自动切换的误判成本太高了——一次网络抖动如果被误判为主站点故障，自动 Promote 后会制造一对数据不一致的主站点，恢复起来比停机还痛苦。人工确认虽然多花 30 秒，但能避免 99% 的脑裂事故。记住一句话：**SRE 的最高境界不是自动化一切，而是在自动化和可控性之间找到平衡点**。"

---

## 3. 项目实战

### 环境准备

| 组件 | 规格/版本 | 用途 |
|------|----------|------|
| GitLab 17.x HA 集群 | 3 个 Rails 节点（rail-1, rail-2, rail-3） | 零停机升级目标 |
| Nginx 负载均衡器 | lb.internal，upstream 指向 3 个 Rails 节点 | 流量摘除/加回 |
| PostgreSQL 17 | 独立部署，流复制主备 | Rails 数据库 |
| Redis 7.x | 3 实例分离（cache/queues/shared_state） | 缓存与队列 |
| Geo 主站点（北京） | beijing-gitlab.internal | Geo Primary |
| Geo 备站点（上海） | shanghai-gitlab.internal | Geo Secondary |
| Prometheus + Grafana | 独立部署 | SRE 指标监控 |

### 分步实现

#### 步骤1：零停机升级完整 SOP

**目标**：将 GitLab 从 17.0 滚动升级到 17.1，期间用户请求 5xx 错误为 0。

```bash
#!/bin/bash
# ============================================================
# GitLab 零停机升级 SOP：17.0 → 17.1（3 节点 HA 集群）
# 核心原则：
#   1. 代码先行，schema 后行（SKIP_POST_DEPLOYMENT_MIGRATIONS=true）
#   2. 节点逐一摘除，绝不批量操作
#   3. 每个节点加回后观察 5 分钟再处理下一个
# ============================================================
set -euo pipefail

TARGET_VERSION="17.1.0-ce.0"
NODES=(rail-1 rail-2 rail-3)
LB_HOST="lb.internal"
NGINX_CONF="/etc/nginx/sites-enabled/gitlab"
HEALTH_URL="http://localhost/-/health"
OBSERVE_SECONDS=300  # 每节点观察 5 分钟

# ---- 前置检查 ----
echo "=== 前置检查 ==="
for node in "${NODES[@]}"; do
  echo "检查 $node 是否在线..."
  ssh "$node" "curl -sf $HEALTH_URL | grep -q 'GitLab OK'" || {
    echo "错误: $node 不健康，请先修复再升级！"; exit 1;
  }
done
echo "所有节点健康，开始备份..."

# ---- Step 1: 全量备份（必须！） ----
ssh rail-1 "sudo gitlab-backup create STRATEGY=copy SKIP=artifacts"
echo "备份完成。备份路径：/var/opt/gitlab/backups/"

# ---- Step 2-5: 逐个节点滚动升级 ----
for i in "${!NODES[@]}"; do
  node="${NODES[$i]}"
  echo "=========================================="
  echo "升级节点 [$((i+1))/3]：$node"
  echo "=========================================="

  # 摘除节点——Nginx upstream 标记为 down
  echo ">>> 摘除 $node 从 LB..."
  ssh "$LB_HOST" "sudo sed -i 's/server $node:8080;/server $node:8080 down;/' $NGINX_CONF"
  ssh "$LB_HOST" "sudo nginx -s reload"
  sleep 10  # 等待现有连接排空

  # 升级 GitLab 包（跳过 post-deployment migration）
  echo ">>> 升级 $node 到 $TARGET_VERSION..."
  ssh "$node" "sudo SKIP_POST_DEPLOYMENT_MIGRATIONS=true \
    apt-get install -y gitlab-ce=$TARGET_VERSION"

  ssh "$node" "sudo gitlab-ctl reconfigure"
  sleep 30

  # 健康检查
  echo ">>> 检查 $node 健康状态..."
  for attempt in {1..12}; do
    if ssh "$node" "curl -sf $HEALTH_URL | grep -q 'GitLab OK'"; then
      echo "  $node 健康！"
      break
    fi
    echo "  等待中...（$attempt/12）"
    sleep 10
  done

  # 加回节点——Nginx upstream 恢复正常
  echo ">>> 加回 $node 到 LB..."
  ssh "$LB_HOST" "sudo sed -i 's/server $node:8080 down;/server $node:8080;/' $NGINX_CONF"
  ssh "$LB_HOST" "sudo nginx -s reload"

  # 观察期：监控 5 分钟确认无异常
  echo ">>> 观察 $node 运行 ${OBSERVE_SECONDS}s..."
  sleep $OBSERVE_SECONDS

  # 观察期内的快速验证
  error_count=$(ssh "$node" "sudo tail -500 /var/log/gitlab/nginx/gitlab_access.log \
    | grep -cE '\" 5[0-9][0-9] '")
  if [ "$error_count" -gt 10 ]; then
    echo "警告: $node 在观察期内产生了 $error_count 个 5xx 错误！请排查！"
  else
    echo "  $node 观察期通过（5xx 错误: $error_count）"
  fi
done

# ---- Step 6: 所有节点升级完后，执行 post-deployment migration ----
echo "=========================================="
echo "所有节点升级完成，执行 post-deployment migration..."
echo "=========================================="
ssh rail-1 "sudo gitlab-rake db:migrate"

# 验证 migration 全部执行
pending=$(ssh rail-1 "sudo gitlab-rake db:migrate:status | grep -c down" || echo 0)
if [ "$pending" -ne 0 ]; then
  echo "错误: 仍有 $pending 个 migration 未执行！"
  exit 1
fi

echo ""
echo "✅ 零停机升级完成！GitLab 已升级到 $TARGET_VERSION"
echo "请在 Grafana 确认 5xx 错误曲线为 0 后，通知业务方升级成功。"
```

#### 步骤2：Geo 多活站点配置

**目标**：配置北京（Primary）与上海（Secondary）双站点 Geo 架构，实现数据库流复制 + Git 仓库异步同步。

**北京主站点 `gitlab.rb` 关键配置**：

```ruby
# ---- Geo Primary 配置 ----
geo_primary_role['enable'] = true
gitlab_rails['geo_node_name'] = 'beijing-primary'

# 必须配置内部 URL，Secondary 通过此地址访问 Primary
gitlab_rails['geo_primary_internal_url'] = 'https://beijing-gitlab.internal'

# 允许 Secondary 通过 API 拉取数据
gitlab_rails['geo_primary_allow_secondary_to_pull'] = true
```

**上海备用站点 `gitlab.rb` 关键配置**：

```ruby
# ---- Geo Secondary 配置 ----
geo_secondary_role['enable'] = true
gitlab_rails['geo_node_name'] = 'shanghai-secondary'

# 指向 Primary 的内部地址
gitlab_rails['geo_secondary']['primary_internal_url'] = 'https://beijing-gitlab.internal'

# 数据库流复制（PostgreSQL standby）
# postgresql.conf 需配置:
#   primary_conninfo = 'host=beijing-gitlab.internal port=5432 user=replicator password=xxx'
#   hot_standby = on
```

**数据库复制初始化**：

```bash
# 在 Secondary 上执行——从 Primary 拉取基础备份
sudo gitlab-ctl stop
sudo rm -rf /var/opt/gitlab/postgresql/data/*
sudo -u gitlab-psql pg_basebackup \
  -h beijing-gitlab.internal -U replicator -D /var/opt/gitlab/postgresql/data \
  -X stream -P -R
sudo gitlab-ctl start
sudo gitlab-ctl reconfigure

# 验证流复制状态
sudo gitlab-psql -c "SELECT pg_is_in_recovery();"  # 应返回 t
sudo gitlab-psql -c "SELECT now() - pg_last_xact_replay_timestamp() AS replication_lag;"
# replication_lag 应 < 1s
```

**Geo 同步状态验证**：

```bash
# 检查 Geo 追踪数据库中的同步状态
sudo gitlab-rails runner "
  puts '数据库同步: ' + GeoNode.secondary_nodes.first.status
  puts '仓库已同步: ' + Geo::ProjectRegistry.synced.count.to_s
  puts '仓库总数: ' + Geo::ProjectRegistry.count.to_s
  puts '同步率: ' + (Geo::ProjectRegistry.synced.count * 100.0 / Geo::ProjectRegistry.count).round(2).to_s + '%'
"
```

#### 步骤3：Geo 故障切换演练脚本

**目标**：主站点故障时，5 分钟内完成 Secondary Promote + DNS 切换 + 用户通知。

```bash
#!/bin/bash
# ============================================================
# Geo 故障切换 SOP（Promote Secondary → Primary）
# 场景：北京主站点不可用，上海备用站点接管
# RTO 目标：< 5min
# ============================================================
set -euo pipefail

PRIMARY_HOST="beijing-gitlab.internal"
SECONDARY_HOST="shanghai-gitlab.internal"
DNS_API_URL="https://dns-api.company.com/records"
SLACK_WEBHOOK="https://hooks.slack.com/services/xxx"
FEISHU_WEBHOOK="https://open.feishu.cn/open-apis/bot/v2/hook/xxx"

# ---- Phase 1: 确认 Primary 不可用 ----
echo "[Phase 1] 确认 Primary 状态..."
if ssh -o ConnectTimeout=5 "$PRIMARY_HOST" "sudo gitlab-rake gitlab:check" &>/dev/null; then
  echo "警告: Primary ($PRIMARY_HOST) 仍然在线！"
  echo "如果 Primary 正常，请勿 Promote——否则会造成脑裂！"
  echo "要继续强制 Promote 吗？输入 yes 确认："
  read -r confirm
  if [ "$confirm" != "yes" ]; then
    echo "切换取消。"
    exit 0
  fi
fi

# ---- Phase 2: 检查 Secondary 同步延迟 ----
echo "[Phase 2] 检查同步延迟..."
LAG_SECONDS=$(ssh "$SECONDARY_HOST" \
  "sudo gitlab-psql -t -c \"SELECT EXTRACT(EPOCH FROM (now() - pg_last_xact_replay_timestamp()))::int;\"")

echo "  数据库延迟: ${LAG_SECONDS}s"

# 获取仓库同步状态
SYNCED=$(ssh "$SECONDARY_HOST" \
  "sudo gitlab-rails runner 'puts Geo::ProjectRegistry.synced.count'")
TOTAL=$(ssh "$SECONDARY_HOST" \
  "sudo gitlab-rails runner 'puts Geo::ProjectRegistry.count'")
echo "  仓库同步: $SYNCED / $TOTAL"

if [ "$LAG_SECONDS" -gt 30 ]; then
  echo "警告: 数据库延迟超过 30s，可能有数据丢失风险！"
  echo "继续 Promote 吗？输入 yes 确认："
  read -r confirm
  if [ "$confirm" != "yes" ]; then
    echo "切换取消。"
    exit 0
  fi
fi

# ---- Phase 3: Promote Secondary ----
echo "[Phase 3] 执行 Promote..."
PROMOTE_START=$(date +%s)

# 先停止写入服务，防止 Promote 过程中有新数据进入
ssh "$SECONDARY_HOST" "sudo gitlab-ctl stop sidekiq puma"

# 执行 Promote
ssh "$SECONDARY_HOST" "sudo gitlab-rake geo:set_secondary_as_primary"

# 重新配置并启动
ssh "$SECONDARY_HOST" "sudo gitlab-ctl reconfigure"
ssh "$SECONDARY_HOST" "sudo gitlab-ctl start"

# 验证 Promote 结果
if ssh "$SECONDARY_HOST" "sudo gitlab-rails runner 'puts GeoNode.current.primary?'" | grep -q true; then
  echo "  ✅ Promote 成功！$SECONDARY_HOST 现在是 Primary。"
else
  echo "  ❌ Promote 失败！请检查日志。"
  exit 1
fi

PROMOTE_END=$(date +%s)
echo "  Promote 耗时: $(( PROMOTE_END - PROMOTE_START ))s"

# ---- Phase 4: DNS 切换 ----
echo "[Phase 4] DNS 切换..."
NEW_IP=$(ssh "$SECONDARY_HOST" "hostname -I | awk '{print \$1}'")
curl -s -X PUT "$DNS_API_URL/gitlab.company.com" \
  -H "Authorization: Bearer $DNS_API_TOKEN" \
  -d "{\"type\":\"A\",\"value\":\"$NEW_IP\",\"ttl\":60}" > /dev/null
echo "  DNS: gitlab.company.com → $NEW_IP (TTL=60s)"

# 等待 DNS 部分生效
echo "  等待 DNS 传播（60s）..."
sleep 60

# 验证 DNS
RESOLVED_IP=$(dig +short gitlab.company.com)
echo "  当前解析结果: $RESOLVED_IP"

# ---- Phase 5: 用户通知 ----
echo "[Phase 5] 发送用户通知..."
FAILOVER_MSG=$(cat <<EOF
⚠️ **GitLab 故障切换通知**
- 切换时间：$(date '+%Y-%m-%d %H:%M:%S')
- 原因：北京主站点不可用
- 新主站点：上海（$NEW_IP）
- 数据延迟：数据库 ${LAG_SECONDS}s，仓库同步 ${SYNCED}/${TOTAL}
- 影响：最近 ${LAG_SECONDS}s 内的数据库写入可能丢失
- 如有问题请联系 SRE 值班 @sre-oncall
EOF
)

# 发送至 Slack
curl -s -X POST "$SLACK_WEBHOOK" \
  -H "Content-Type: application/json" \
  -d "{\"text\":\"$FAILOVER_MSG\"}" > /dev/null

# 发送至飞书
curl -s -X POST "$FEISHU_WEBHOOK" \
  -H "Content-Type: application/json" \
  -d "{\"msg_type\":\"text\",\"content\":{\"text\":\"$FAILOVER_MSG\"}}" > /dev/null

echo "  ✅ 通知已发送。"

RTO_END=$(date +%s)
RTO_TOTAL=$(( RTO_END - PROMOTE_START ))
echo ""
echo "=========================================="
echo "✅ Geo 故障切换完成！"
echo "  总 RTO: ${RTO_TOTAL}s（目标 < 300s）"
echo "  新 Primary: $SECONDARY_HOST ($NEW_IP)"
echo "=========================================="
```

#### 步骤4：SRE 指标监控大盘

**目标**：定义并监控四个核心 SLO 指标，用 Grafana 大盘可视化。

**SLO / SLI 定义**：

| SLI 指标 | SLO 目标 | 测量方式 | 告警阈值 |
|----------|---------|---------|---------|
| **可用性** | 99.9%（月故障 ≤ 43min） | `gitlab_ok` health endpoint → Prometheus `probe_success` | < 99.9% 触发 P2，< 99.5% 触发 P1 |
| **API P95 延迟** | < 500ms | `gitlab_rails_request_duration_seconds` 的 histogram_quantile(0.95) | > 500ms 持续 5min 触发 P2 |
| **Geo 故障切换 RTO** | < 5min | 演练脚本中的 `$RTO_TOTAL` 写入 Prometheus Pushgateway | > 300s 触发改进工单 |
| **备份 RPO** | < 24h | `gitlab_backup_timestamp` 与当前时间之差 | > 24h 触发 P1 |

**Prometheus 记录规则（GitLab SRE 指标）**：

```yaml
# /etc/prometheus/rules/gitlab_sre.yml
groups:
  - name: gitlab_sre_slo
    interval: 30s
    rules:
      # 可用性 SLI —— 基于 health 探测
      - record: job:gitlab_availability:ratio
        expr: |
          sum(rate(probe_success{job="gitlab-health"}[30d])) 
          / sum(rate(probe_success{job="gitlab-health"}[30d]) + rate(probe_failed{job="gitlab-health"}[30d]))

      # API P95 延迟
      - record: job:gitlab_api_p95_latency_seconds
        expr: |
          histogram_quantile(0.95, 
            sum(rate(gitlab_rails_request_duration_seconds_bucket{controller!~"health|metrics"}[5m])) 
            by (le))

      # 备份 RPO —— 距离上次成功备份的小时数
      - record: gitlab:backup_hours_since_last_success
        expr: |
          (time() - gitlab_backup_last_success_timestamp) / 3600

  - name: gitlab_sre_alerts
    rules:
      - alert: GitLabAvailabilityLow
        expr: job:gitlab_availability:ratio < 0.999
        for: 5m
        labels:
          severity: p2
        annotations:
          summary: "GitLab 可用性低于 99.9%（当前: {{ $value | humanizePercentage }}）"

      - alert: GitLabApiP95High
        expr: job:gitlab_api_p95_latency_seconds > 0.5
        for: 5m
        labels:
          severity: p2
        annotations:
          summary: "API P95 延迟 > 500ms（当前: {{ $value }}s）"

      - alert: GitLabBackupRPOExceeded
        expr: gitlab:backup_hours_since_last_success > 24
        labels:
          severity: p1
        annotations:
          summary: "备份 RPO 超过 24h（上次成功: {{ $value }}h 前）"
```

### 测试验证

```bash
# ===== 验证1：零停机升级——ab 持续请求 =====
# 在执行升级 SOP 的同时，从另一台机器持续发压
ab -n 100000 -c 50 -t 600 \
  -H "PRIVATE-TOKEN: $GITLAB_TOKEN" \
  "https://gitlab.company.com/api/v4/projects" 2>&1 | tee ab_result.txt

# 升级完成后检查 5xx 数量
grep "Non-2xx" ab_result.txt
# 预期：Non-2xx responses: 0（零停机目标达成）

# 也可用 wrk
wrk -t 4 -c 50 -d 600s \
  -H "PRIVATE-TOKEN: $GITLAB_TOKEN" \
  "https://gitlab.company.com/api/v4/projects"

# ===== 验证2：Geo 同步状态 =====
curl -s --header "PRIVATE-TOKEN: $GITLAB_TOKEN" \
  "https://shanghai-gitlab.internal/api/v4/geo/status" | jq '{
    db_replication_lag: .db_replication_lag_seconds,
    repositories_synced: .repositories_synced_count,
    repositories_failed: .repositories_failed_count,
    repositories_total: .repositories_count
  }'
# 预期：db_replication_lag < 5s, repositories_failed = 0

# ===== 验证3：故障切换 RTO 计时 =====
# 在 Promote 脚本中已内置计时，从 PROMOTE_START 到 DNS 验证通过
# 预期：RTO < 300s

# ===== 验证4：SRE 大盘验证 =====
# 查询 Prometheus 确认 SLO 指标正常
curl -s "http://prometheus:9090/api/v1/query?query=job:gitlab_availability:ratio" | jq '.data.result[0].value[1]'
# 预期：> 0.999

curl -s "http://prometheus:9090/api/v1/query?query=job:gitlab_api_p95_latency_seconds" | jq '.data.result[0].value[1]'
# 预期：< 0.5
```

## 4. 项目总结

### 三种可用性方案对比

| 维度 | Backup（冷备） | HA（热备集群） | Geo（异地多活） |
|------|--------------|--------------|---------------|
| **RTO（恢复时间）** | 2-6 小时 | 30s-5min（自动故障切换） | 3-9min（半自动 Promote） |
| **RPO（数据丢失）** | < 24h（上次备份） | 0（共享存储，无丢失） | 0-5min（异步同步延迟） |
| **抵御机房级故障** | ❌ 同机房 | ❌ 同机房 | ✅ 异地容灾 |
| **抵御误删数据** | ✅ 可回滚到任意时间点 | ❌ 实时同步意味着误删也会同步 | ⚠️ 有延迟窗口回滚 |
| **运维复杂度** | 低 | 中 | 高 |
| **年度成本（估算）** | 1× | 2-3× | 4-6× |
| **适用规模** | < 100 用户 | 100-1000 用户 | 1000+ 用户、多地域团队 |

### 零停机升级五步关键控制点

| 步骤 | 操作 | 常见错误 | 正确做法 |
|------|------|---------|---------|
| ① 备份 | `gitlab-backup create` | 跳过备份直接升级 | 备份是回滚的唯一保障，必须为第一步 |
| ② 摘除节点 | LB drain，标记 `down` | 直接 `gitlab-ctl stop` 导致连接中断 | 用 LB drain 让连接自然排空（graceful shutdown） |
| ③ 升级代码 | `apt-get install` + `reconfigure` | 忘记设置 `SKIP_POST_DEPLOYMENT_MIGRATIONS` | 必须显式设置该环境变量 |
| ④ 加回节点 | LB 解除 `down` 标记 | 加回后立即处理下一个节点 | 观察至少 5 分钟，确认 5xx 为零再继续 |
| ⑤ 执行 migration | `gitlab-rake db:migrate` | 提前在某节点执行 | 必须在所有节点升级完成后单独执行，且只在一个节点执行 |

### 适用场景

- ✅ **金融、医疗等强合规行业**：零停机升级是合规硬需求，Geo 多活满足异地容灾审计要求
- ✅ **跨地域研发团队**（如北京+上海+深圳）：Geo Secondary 可做本地只读加速，减少跨地域 clone 延迟
- ✅ **500+ 用户且要求 99.9% 可用性**：单机 HA 无法满足，必须上 Geo 或等效多活方案
- ✅ **季度大版本升级频繁**：零停机 SOP 可将升级窗口从"周末通宵"降为"工作日白天无感升级"
- ✅ **SRE 团队能力成熟**：有专门的演练文化和回滚预案，不是"配好就再也不敢动"
- ❌ **团队少于 50 人且无合规要求**：Geo 的运维成本（双倍资源 + 同步监控）远超收益，Backup 即可
- ❌ **网络条件不稳定（跨国高延迟链路）**：Geo 同步需要稳定的网络，跨太平洋延迟 200ms+ 会导致仓库同步积压严重

### 常见踩坑经验

**1. Post-deployment migration 执行到一半失败，如何无停机回滚？**

> **根因**：迁移脚本中有 DDL 操作（如 `ALTER TABLE` 加列），中途因锁超时或磁盘满而中断，数据库处于半迁移状态。**回滚方法**：（1）立即将执行 migration 的节点从 LB 摘除——该节点已跑了一半的 migration，数据可能不一致；（2）从备份恢复数据库到一个临时库，将失败的表 `pg_dump` 出来覆盖主库的对应表；（3）手动补完剩余的 migration。关键原则：**只让一个节点执行 migration，出问题只隔离这一个节点**。

**2. Geo Promote 后旧 Primary 恢复上线，导致双主脑裂**

> **根因**：旧 Primary 故障恢复后自动启动服务（`gitlab-ctl start` 被写入了 systemd 的 `Restart=always`），而 DNS 尚未完全切走，部分流量（如 Git over SSH 直连）仍路由到旧 Primary——两边同时接受了写入。**解决方案**：（1）旧 Primary 恢复后第一件事不是启动 GitLab，而是执行 `gitlab-rake geo:set_secondary_as_primary --force` 将其强降为 Secondary；（2）或在 DNS 层面将旧 Primary IP 指向一个维护页面，确保零流量进入；（3）终极方案：Promote 后立即在旧 Primary 执行 `systemctl mask gitlab-runsvdir` 禁止自动启动。

**3. DNS TTL 设置过高（1 小时），导致故障切换后长时间无法完全生效**

> **根因**：部分客户端或中间 DNS 缓存了旧 IP，在 TTL 过期前持续访问已经宕机的旧 Primary。**解决方案**：（1）将 GitLab 域名的 TTL 调到 60s——增加 DNS 查询量约 0.1%，但 RTO 从"最长 1 小时"降到"最长 60s"；（2）配合 Nginx LB 做双写——切换瞬间同时将新 IP 更新到 DNS 和 CDN/反向代理层；（3）切换后在新 Primary 上临时监听旧 Primary 的 IP（VIP 漂移），覆盖 TTL 缓存窗口。

### 思考题

1. Geo Promote 后原主站点计划恢复为新的 Secondary——在此过程中，如何安全地"回切"到北京站点而不引发二次脑裂？需要考虑数据回灌、用户通知、DNS 回切三个环节。
2. 零停机升级 SOP 中的"观察 5 分钟"策略——如果你的监控系统本身也在升级的 GitLab 上（自举问题），这个阶段如何设计替代的健康检查手段？

> 答案见附录 D。

### 推广计划提示

- **运维 / SRE 团队**：零停机升级 SOP 和 Geo 切换演练应纳入每季度强制演习——SRE 的能力不是来自文档，而是来自反复的实战操作。每年至少两次全流程演练（包含 DNS 切换和用户通知）。
- **管理层**：将 SRE 四大指标（可用性 99.9%、API P95 < 500ms、故障切换 RTO < 5min、备份 RPO < 24h）设为运维 OKR——这些指标直接量化了"系统可靠性"而非"做了多少工单"。
- **开发团队**：Geo Secondary 不仅是灾备，更是天然的只读加速节点——就近的开发团队可以将 clone/fetch 指向 Secondary，减少跨地域网络延迟。
