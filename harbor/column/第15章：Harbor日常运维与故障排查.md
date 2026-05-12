# 第15章：Harbor 日常运维与故障排查

## 1 项目背景

某 600 人规模的互联网公司，Harbor 已上线稳定运行 14 个月——从最初的"能跑就行"正式进入"生产级运维"阶段。随着 Harbor 承载的业务从 15 个项目扩展到 72 个项目、日均 Push/Pull 从 800 次增长到 12000 次，各种生产级问题开始密集浮现。

**痛点一：版本升级引发连锁故障，升级窗口频频溢出。** 运维团队计划在周六凌晨 2:00-4:00 将 Harbor 从 v2.8.0 升级到 v2.12.0。操作按照官方文档进行：备份 → `docker compose down` → 解压新版 → 按旧版修改 `harbor.yml` → `./prepare` → `docker compose up -d`。Portal 页面正常打开，但 45 分钟后第一个 CI Pipeline 运行时报错——所有 `docker push` 返回 `500 Internal Server Error`。排查了整整 3 个小时才发现根因：v2.12 的 `harbor.yml` 新增了必填参数 `trivy.insecure` 和 `trivy.skip_update`，`./prepare` 脚本在没有这些参数时**没有显式报错**，而是静默使用了默认值——但默认值与实际的 Trivy 配置不兼容，导致 Core 在初始化扫描器时 panic。升级窗口从计划的 2 小时延长到 5 小时，早上 7 点才恢复服务——开发团队周一早上一来就发现 CI 全部红灯。

**痛点二：TLS 证书过期导致全站不可用，业务大面积中断。** 周五下午 17:55——大部分人正准备下班——Harbor 的 TLS 证书过期了。影响面迅速扩散：(1) 所有 CI/CD 流水线的 `docker login` 失败，无法推送新镜像；(2) Kubernetes 集群中 120+ 个节点同时出现 `ImagePullBackOff`——因为 kubelet 无法从 Harbor 拉取镜像；(3) 运维人员打开 Portal 浏览器报 `NET::ERR_CERT_DATE_INVALID`，连手动查看状态都做不到。运维小李紧急续签证书 → 更新 `harbor.yml` 中的证书路径 → `./prepare && docker compose up -d`。Portal 恢复了，但 K8s 节点仍然 `ImagePullBackOff`——排查后发现 Harbor Core 容器内部的 CA 信任库（`/etc/ssl/certs/`）仍缓存着旧证书，Core 在与 Registry 的内部通信中也报 TLS 验证错误。修复这个"内伤"又花了 40 分钟。总计影响时间：2 小时 15 分钟，直接导致 3 个业务的周五晚间发布延期。

**痛点三：PostgreSQL 连接池耗尽，高峰时段 API 大面积 502。** 某个周一早 9:30——业务高峰时段，120+ 个微服务同时滚动更新，Harbor Core 的并发连接瞬间达到峰值。运维监控突然报出大批量 Harbor API 返回 `502 Bad Gateway`。查 Core 日志发现大量 `"pq: too many clients for database"` 错误。原来 PostgreSQL 默认 `max_connections=100`，而 Harbor Core 的连接池配置为 `max_open_conns: 900`——Core 尝试打开 900 个数据库连接，但 PostgreSQL 只允许 100 个。连接池配置"宽进严出"，导致 Core 的数据库操作全部阻塞。运维紧急调整了 PostgreSQL 的 `max_connections` 到 300 并重启——但 Redis 和 Core 之间的 Session 缓存也因重启丢失，所有用户被迫重新登录。周一早上的这个故障被记录为"年度 Top 3 运维事故"。

**痛点四：备份不完整，数据恢复时才发现"备份是空的"。** 运维小张入职时接手了 Harbor 运维工作。前运维留下了一个 Weekly CronJob：每周日凌晨 2 点执行 `docker exec harbor-db pg_dump -U postgres registry > /backup/harbor-db.sql`。三个月来，这个备份脚本每周准时运行，生成的文件大小稳定在 8MB 左右。直到有一天——虚拟机磁盘故障，`/data/registry/` 下的 Blob 文件（镜像的实际二进制数据）全部损坏。小张信心满满地执行恢复：`psql < harbor-db.sql` → 数据库恢复成功。但 `docker pull` 时报 `BLOB_UNKNOWN: blob unknown to registry`——因为 Blob 文件没有备份！Harbor 的数据分为两部分：PostgreSQL 中的元数据（项目、仓库、标签、用户）和文件系统中的 Blob 数据（镜像层）。只备份数据库 = 只备份了"字典"，没有备份"书本内容"。87 个镜像全部无法拉取——这个教训代价惨重。

**痛点五：垃圾回收（GC）长期未执行，磁盘使用率达到 95%。** Harbor 的"删除镜像"操作实际上是"软删除"——删除的只是数据库中的标签引用，镜像的 Blob 文件仍然在磁盘上。只有当 GC 执行后，不再被任何标签引用的 Blob 才会被真正删除。运维团队不知道这个机制，6 个月来都在 Portal 上删除旧镜像但从未执行 GC——导致 `/data/registry/` 目录膨胀到 450GB（实际有用数据只有 180GB，其余 270GB 是"僵尸 Blob"）。运维直到收到磁盘告警（使用率 > 90%）才开始排查，第一次 GC 耗时 4.5 小时，期间 Registry 处于只读模式——所有 Push 操作被暂停。

本章将系统总结 Harbor 日常运维的核心场景——版本升级 SOP、证书管理、数据库调优、完整备份恢复、GC 策略、日常巡检——以及每个场景中的高频故障排查方法。

---

## 2 项目设计——剧本式交锋对话

**场景：一个下雨的周二下午，运维组在复盘过去半年 Harbor 的所有故障。白板上贴满了故障工单。**

---

**小胖**（把一叠故障工单拍在桌上，纸张散落一地）："大师，半年踩了这么多坑——升级故障、证书过期、数据库崩溃、备份恢复失败、磁盘写满……我感觉 Harbor 有一万种死法！有没有一份'常见故障速查表'——像医院急诊分诊台那种——看到症状了就指向具体的排查路径？我现在每次故障都像无头苍蝇一样乱查。"

**大师**（站起来，用红笔在白板上画了一张巨大的故障排查流程图）："你的问题本质上是缺少一个**故障排查心智模型**。Harbor 的故障 80% 集中在三个核心组件——Core、Registry、PostgreSQL。任何一个症状，都可以先按'症状 → 排查入口 → 最可能根因'的三步法快速定位。"

```
════════════════════════════════════════════════════════════════════
                  Harbor 故障速查地图
════════════════════════════════════════════════════════════════════

症状                 排查入口            最可能根因（按概率排序）
─────────────────────────────────────────────────────────────────
docker login 401  → Core 日志        ① 密码错误/过期
                                     ② Token 失效（重启后）
                                     ③ OIDC 认证服务不可达
                                     ④ 用户被禁用

docker push 500   → Registry 日志    ① 存储配额超限 (85%)
                  → Core 日志         ② 磁盘空间不足 (10%)
                                     ③ 项目不存在或已删除
                                     ④ Registry 存储驱动故障

docker pull 503   → Nginx 日志       ① Core 服务未就绪
                  → `docker compose ps` ② 健康检查失败
                                     ③ 后端服务连接超时
                                     ④ Nginx 配置错误

Portal 502        → Nginx + Core     ① Core 挂了/重启中
                  → 日志              ② Core URL 配置错误
                                     ③ 数据库连接池耗尽

Portal 白屏/空白  → Portal 容器      ① Core URL 无法解析
                  → 日志              ② Portal 静态资源加载失败
                                     ③ 浏览器缓存了旧的 JS

Core 反复重启     → Core 日志        ① PostgreSQL 未就绪
(loop restart)                       ② Redis 连接失败
                                     ③ harbor.yml 配置错误
                                     ④ 迁移脚本执行失败

证书错误          → 浏览器/openssl   ① 证书已过期 (90%)
                  → Core 日志         ② 证书域名不匹配
                                     ③ Core 内部 CA 库未更新
                                     ④ 中间证书链不完整
```

**技术映射**：Harbor 的微服务架构中，Core 是所有业务逻辑的入口。Nginx 作为反向代理将请求路由到 Core（API 请求）或 Portal（静态资源）。Core 依赖 PostgreSQL（持久化数据）和 Redis（Session 缓存、任务队列）。Registry 处理镜像 Blob 的存储和传输。理解这个依赖链是排障的基础——例如 Core 反复重启，常见根因是 PostgreSQL 还没启动好，Core 连接数据库失败就退出、Docker 又自动重启它——形成重启循环。

---

**小白**（拿着笔记本）："大师，您刚才说的'存储配额超限'是 push 500 的最常见根因——用户怎么知道自己的配额还有多少？我们能不能在配额快满的时候提前告警？"

**大师**："配额检查有两条路径——事前预防和事后诊断。"

**事前预防——API 查询配额：**
```bash
# 查看项目配额使用情况
curl -s -u admin:Harbor12345 \
  "https://harbor.company.com/api/v2.0/projects?name=order-platform" | \
  jq '.[0] | {name, storage_limit_gb: (.storage_limit / 1073741824), 
              storage_used_gb: (.storage_used / 1073741824 | . * 100 | round/100),
              usage_pct: ((.storage_used / .storage_limit * 10000) | round / 100)}'

# 输出示例：
# {
#   "name": "order-platform",
#   "storage_limit_gb": 200,
#   "storage_used_gb": 187.5,
#   "usage_pct": 93.75
# }
```

**事后诊断——当 push 报 500 时的排查步骤：**
```bash
# 1. 确认 push 失败是在哪一步
docker push harbor.company.com/order-platform/order-service:v2.5.0
# The push refers to repository [harbor.company.com/order-platform/order-service]
# 5f70bf18a086: Pushing [========>] 120MB/256MB
# received unexpected HTTP status: 500 Internal Server Error

# 2. 查 Registry 日志（找具体错误信息）
docker logs harbor-registry 2>&1 | grep -i error | tail -20
# 如果看到: "denied: requested access to the resource is denied"
# 或: "http: request failed after upload: quota exceeded"

# 3. 查 Core 日志（找配额拒绝信息）
docker logs harbor-core 2>&1 | grep -i "quota\|storage limit" | tail -10
# 如果看到: "project storage quota exceeded"

# 4. 确认磁盘空间
df -h /data/registry/
# 如果 Use% > 95%，需要立即清理或扩容
```

**小白**（追问）："那如果确实是配额满了，我该怎么处理？直接调大配额吗？"

**大师**："三个选项——要看具体情况："

| 方案 | 适用场景 | 操作 | 风险 |
|------|---------|------|------|
| 调大配额上限 | 存储空间充足，只是初始配额设小了 | 通过 API 或 Portal 修改 `storage_limit` | 可能掩盖"无限增长"的问题 |
| 清理旧镜像释放空间 | 有大量 CI 中间构建或过期版本 | 执行标签保留策略 + 手动删除 + GC | 需确认哪些标签可以安全删除 |
| 扩容磁盘 + 调大配额 | 业务确实需要更多存储 | 扩容 LVM 或云盘 → 调大 Harbor 配额 | 数据迁移过程有风险 |

---

**小胖**（嘟囔着）："配额和证书这些我都差不多明白了。但上次那个升级故障——v2.8 升 v2.12，prepare 不报错但 push 全挂——这种'静默故障'怎么预防？升级有没有标准 SOP？"

**大师**（走到白板前，画了一条时间线）："Harbor 升级是运维中最危险的操作——没有之一。必须有严格的 SOP，分阶段执行，每个阶段都有验证点。跳过任何一个验证点 = 拿生产环境赌博。"

```
Harbor 升级 SOP 时间线
═══════════════════════════════════════════════════════════════

T-7天   阅读 Release Notes → 识别 Breaking Changes
        在 Staging 环境预演升级
        ↓
T-1天   通知业务方升级窗口
        备份：DB + Storage + harbor.yml + 证书
        记录当前版本号
        ↓
T+0min  停止 Harbor (docker compose down)
        解压新版本安装包
        ↓
T+15min diff 新旧 harbor.yml.tmpl → 手工合并配置变更
        【关键】不要直接复制旧 harbor.yml 到新版本！
        而是以新版 harbor.yml.tmpl 为模板，逐个填入自定义参数
        ↓
T+30min ./prepare（检查输出中的 ERROR/WARNING）
        ↓
T+35min docker compose up -d
        ↓
T+40min watch docker compose ps（等待所有容器 Healthy）
        ↓
T+45min 【验证 1】API 健康检查: GET /api/v2.0/health
        【验证 2】版本号: GET /api/v2.0/systeminfo
        ↓
T+50min 【验证 3】docker login + push + pull 测试
        【验证 4】Portal 页面可访问
        【验证 5】Webhook 能收到测试事件
        ↓
T+60min 半小时监控观察：API 响应时间、错误率、DB 连接数
        ↓
T+90min 【升级完成】通知业务方
        【保留旧版本目录至少 7 天，以便回滚】
```

**技术映射**：Harbor 的 `./prepare` 脚本负责根据 `harbor.yml` 生成各容器的配置文件（Nginx 配置、Core 环境变量、Registry 配置等）。`prepare` 脚本使用 Python/Jinja2 模板引擎——`harbor.yml` 中的参数值被注入到 `*.tmpl` 模板文件中。如果 `harbor.yml` 缺少新增的必填参数，模板渲染会使用默认值（通常是空值或不合理的值）——这就是"静默故障"的根因。最佳实践：**总是基于新版 `harbor.yml.tmpl` 创建配置，而不是直接复制旧版 `harbor.yml`。**

---

**大师**（转向所有人）："证书、升级、配额都讲完了。但我今天最想强调的是——**Harbor 的完整备份不是你想象的那么简单**。小张上次为什么恢复失败？因为他只备份了数据库。现在，我给你们一套'备份即使不做也要知道'的方案。"

```bash
#!/bin/bash
# harbor-full-backup.sh — 完整的 Harbor 备份方案
# 
# Harbor 数据分为三个部分，缺一不可：
# ┌──────────────┐ ┌──────────────────┐ ┌─────────────────┐
# │ PostgreSQL   │ │ 文件系统数据      │ │ 配置文件         │
# │ (元数据)     │ │ (Blob/镜像本体)   │ │ (运行参数)       │
# ├──────────────┤ ├──────────────────┤ ├─────────────────┤
# │ 项目信息     │ │ /data/registry/   │ │ harbor.yml      │
# │ 用户/成员    │ │   docker/registry │ │ docker-compose  │
# │ 审计日志     │ │   /v2/blobs/      │ │ 证书文件        │
# │ 复制规则     │ │ /data/secret/     │ │ /etc/ssl/certs/ │
# │ Webhook配置  │ │   密钥/加密文件   │ │                 │
# └──────────────┘ └──────────────────┘ └─────────────────┘
#
# 备份 = 1(数据库) + 2(文件) + 3(配置) = 完整

BACKUP_DIR="/backup/harbor-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP_DIR"

echo "=== Harbor Full Backup ==="
echo "Backup dir: $BACKUP_DIR"
echo "Started at: $(date)"

# ──── 第一步：备份配置文件 ────
echo ""
echo "[1/5] Backing up configuration..."
cp /opt/harbor/harbor.yml "$BACKUP_DIR/"
cp /opt/harbor/docker-compose.yml "$BACKUP_DIR/" 2>/dev/null
cp /opt/harbor/common/config/ "$BACKUP_DIR/common-config/" -r 2>/dev/null

# ──── 第二步：备份数据库 ────
echo "[2/5] Backing up PostgreSQL database..."
docker exec harbor-db pg_dump -U postgres registry \
  > "$BACKUP_DIR/harbor-db-$(date +%Y%m%d).sql"

# 检查备份文件是否有效（不为空且有 SQL 结尾）
if [ -s "$BACKUP_DIR/harbor-db-$(date +%Y%m%d).sql" ]; then
  DB_SIZE=$(du -h "$BACKUP_DIR/harbor-db-$(date +%Y%m%d).sql" | cut -f1)
  echo "  ✓ Database backup OK ($DB_SIZE)"
else
  echo "  ✗ Database backup FAILED (empty file)!"
  exit 1
fi

# ──── 第三步：备份存储数据（Blob 文件） ────
echo "[3/5] Backing up registry storage (this may take a while)..."
# 重要：备份期间 Harbor 应该保持运行，但为避免数据不一致，
# 可以先停止 Registry 容器（使其只读），备份完再启动
tar -czf "$BACKUP_DIR/harbor-registry-data.tar.gz" \
  /data/registry/ 2>/dev/null

REGISTRY_SIZE=$(du -h "$BACKUP_DIR/harbor-registry-data.tar.gz" | cut -f1)
echo "  ✓ Registry data backup OK ($REGISTRY_SIZE)"

# ──── 第四步：备份密钥和敏感文件 ────
echo "[4/5] Backing up secrets and keys..."
tar -czf "$BACKUP_DIR/harbor-secrets.tar.gz" \
  /data/secret/ 2>/dev/null

# ──── 第五步：备份 TLS 证书 ────
echo "[5/5] Backing up TLS certificates..."
cp /etc/ssl/certs/ca.crt "$BACKUP_DIR/" 2>/dev/null
cp /etc/ssl/certs/ca.key "$BACKUP_DIR/" 2>/dev/null
cp /etc/ssl/certs/harbor.crt "$BACKUP_DIR/" 2>/dev/null
cp /etc/ssl/certs/harbor.key "$BACKUP_DIR/" 2>/dev/null

echo ""
echo "============================================"
echo "Backup completed at: $(date)"
echo "Location: $BACKUP_DIR"
echo "Total size: $(du -sh $BACKUP_DIR | cut -f1)"
echo "============================================"

# 生成备份清单
echo ""
echo "=== Backup Manifest ==="
ls -lh "$BACKUP_DIR/"
```

**大师**："记住一个原则：**没有验证过的备份 = 不存在的备份**。每个月至少做一次恢复演练——拿最新的备份在 Staging 环境恢复，确认 `docker pull` 能拉取到完整的镜像。"

---

## 3 项目实战

### 3.1 环境准备

| 组件 | 版本/配置 | 说明 |
|------|----------|------|
| Harbor | v2.12 (Docker Compose) | 生产环境实例 |
| Docker | 24+ | 用于执行容器命令 |
| `openssl` | 1.1+ | 证书检查和生成 |
| `jq` | 1.6+ | API 响应解析 |
| `pg_dump` / `psql` | 14+ | 数据库备份和恢复 |
| `crontab` | — | 定时任务调度 |
| 备机 / Staging 环境 | — | 备份恢复演练 (可选但推荐) |

```bash
# 确认 Harbor 当前状态
echo "=== Harbor Current Status ==="
echo -n "Harbor version: "
curl -sf -u admin:Harbor12345 \
  https://harbor.company.com/api/v2.0/systeminfo | jq -r '.harbor_version'

echo -n "Health status: "
curl -sf -u admin:Harbor12345 \
  https://harbor.company.com/api/v2.0/health | jq -r '.status'

echo ""
echo "Container status:"
docker compose -f /opt/harbor/docker-compose.yml ps \
  --format "table {{.Name}}\t{{.Status}}\t{{.Ports}}"
```

### 3.2 步骤一：Harbor 版本升级 SOP（从 v2.8 → v2.12）

**前置条件检查清单：**

```bash
#!/bin/bash
# upgrade-precheck.sh — 升级前检查脚本

echo "=== Harbor Upgrade Pre-Check ==="
ERRORS=0

# 1. 磁盘空间检查（至少需要 20GB 空闲用于备份）
AVAIL=$(df /data --output=avail | tail -1)
AVAIL_GB=$((AVAIL / 1024 / 1024))
echo "[1] Disk free: ${AVAIL_GB}GB"
if [ "$AVAIL_GB" -lt 20 ]; then
  echo "  ✗ Insufficient disk space (< 20GB)"
  ERRORS=$((ERRORS + 1))
fi

# 2. 检查所有容器是否健康
UNHEALTHY=$(docker compose -f /opt/harbor/docker-compose.yml ps | \
  grep -v "healthy" | grep -v "Name" | wc -l)
echo "[2] Healthy containers: $(docker compose ps | grep healthy | wc -l)"
if [ "$UNHEALTHY" -gt 0 ]; then
  echo "  ✗ $UNHEALTHY containers not healthy — fix before upgrading"
  ERRORS=$((ERRORS + 1))
fi

# 3. 确认当前版本
CUR_VERSION=$(curl -sf -u admin:Harbor12345 \
  https://harbor.company.com/api/v2.0/systeminfo | jq -r '.harbor_version')
echo "[3] Current version: $CUR_VERSION"

# 4. 检查是否有正在运行的任务（复制、GC、扫描）
RUNNING_JOBS=$(curl -sf -u admin:Harbor12345 \
  https://harbor.company.com/api/v2.0/replication/executions?page_size=5 | \
  jq '[.[] | select(.status == "InProgress")] | length')
echo "[4] Running jobs: $RUNNING_JOBS"
if [ "$RUNNING_JOBS" -gt 0 ]; then
  echo "  ⚠  There are running jobs — wait for them to complete"
fi

# 5. 备份关键文件（在正式升级前做一次快速备份）
echo "[5] Quick config backup..."
cp /opt/harbor/harbor.yml /tmp/harbor.yml.pre-upgrade
echo "  ✓ Config backed up to /tmp/harbor.yml.pre-upgrade"

echo ""
if [ "$ERRORS" -eq 0 ]; then
  echo "✓ Pre-check passed — ready for upgrade"
else
  echo "✗ Pre-check found $ERRORS issue(s) — fix before upgrading"
  exit 1
fi
```

**正式升级流程：**

```bash
#!/bin/bash
# upgrade-execute.sh — 执行升级

set -e  # 遇到错误立即退出

NEW_VERSION="v2.12.0"
HARBOR_URL="https://harbor.company.com"

echo "=== Harbor Upgrade: → $NEW_VERSION ==="
echo "Time: $(date)"

# ──── Phase 1: 备份 ────
echo ""
echo "--- Phase 1: Backup ---"
BACKUP_DIR="/backup/harbor-preupgrade-$(date +%Y%m%d-%H%M)"
mkdir -p "$BACKUP_DIR"

# 完整备份（使用之前的完整备份脚本）
echo "Running full backup..."
docker exec harbor-db pg_dump -U postgres registry > "$BACKUP_DIR/harbor-db.sql"
sudo tar -czf "$BACKUP_DIR/harbor-data.tar.gz" /data/registry/ /data/secret/ 2>/dev/null
cp /opt/harbor/harbor.yml "$BACKUP_DIR/"
echo "Backup saved to: $BACKUP_DIR"

# ──── Phase 2: 下载新版本 ────
echo ""
echo "--- Phase 2: Download $NEW_VERSION ---"
cd /opt
wget -q "https://github.com/goharbor/harbor/releases/download/${NEW_VERSION}/harbor-offline-installer-${NEW_VERSION}.tgz"
tar -xzf "harbor-offline-installer-${NEW_VERSION}.tgz"
mv harbor "harbor-${NEW_VERSION}"
echo "New version extracted to: /opt/harbor-${NEW_VERSION}"

# ──── Phase 3: 配置迁移（关键步骤！） ────
echo ""
echo "--- Phase 3: Migrate Configuration ---"
echo "Comparing old harbor.yml with new harbor.yml.tmpl..."

cd "/opt/harbor-${NEW_VERSION}"

# 找出新版 harbor.yml.tmpl 中新增的参数
echo "=== New parameters in $NEW_VERSION ==="
diff /opt/harbor/harbor.yml harbor.yml.tmpl | grep "^>" | head -20

echo ""
echo "IMPORTANT: Do NOT copy old harbor.yml directly!"
echo "Instead, edit the new harbor.yml.tmpl with your custom values."
echo ""

# 从旧配置中提取关键参数（示例）
OLD_HOSTNAME=$(grep "^hostname:" /opt/harbor/harbor.yml | awk '{print $2}')
OLD_CERT=$(grep "^  certificate:" /opt/harbor/harbor.yml | awk '{print $2}')
OLD_KEY=$(grep "^  private_key:" /opt/harbor/harbor.yml | awk '{print $2}')
OLD_ADMIN_PASS=$(grep "^harbor_admin_password:" /opt/harbor/harbor.yml | awk '{print $2}')
OLD_DATA_VOL=$(grep "^data_volume:" /opt/harbor/harbor.yml | awk '{print $2}')

echo "Extracted old parameters:"
echo "  hostname: $OLD_HOSTNAME"
echo "  certificate: $OLD_CERT"
echo "  private_key: $OLD_KEY"
echo "  data_volume: $OLD_DATA_VOL"

# ⚠️ 这里需要人工介入，基于 harbor.yml.tmpl 模板，填入旧参数和新参数
# 以下是一个 sed 辅助迁移脚本（仅供参考，务必人工review）
cp harbor.yml.tmpl harbor.yml

sed -i "s|^hostname:.*|hostname: $OLD_HOSTNAME|" harbor.yml
sed -i "s|^  certificate:.*|  certificate: $OLD_CERT|" harbor.yml
sed -i "s|^  private_key:.*|  private_key: $OLD_KEY|" harbor.yml
sed -i "s|^harbor_admin_password:.*|harbor_admin_password: $OLD_ADMIN_PASS|" harbor.yml
sed -i "s|^data_volume:.*|data_volume: $OLD_DATA_VOL|" harbor.yml

echo ""
echo "⚠️  harbor.yml generated. Please manually review before proceeding!"
echo "    File: /opt/harbor-${NEW_VERSION}/harbor.yml"
echo ""
read -p "Press Enter to continue after reviewing harbor.yml..." 

# ──── Phase 4: 停止旧版本 ────
echo ""
echo "--- Phase 4: Stop Old Harbor ---"
cd /opt/harbor
docker compose down
echo "Old Harbor stopped."

# ──── Phase 5: 启动新版本 ────
echo ""
echo "--- Phase 5: Start New Harbor ---"
cd "/opt/harbor-${NEW_VERSION}"

# Prepare（生成配置文件）
echo "Running ./prepare..."
./prepare 2>&1 | tee /tmp/harbor-prepare.log

# 检查 prepare 是否有 ERROR
if grep -qi "error\|failed" /tmp/harbor-prepare.log; then
  echo "✗ prepare reported errors — please check /tmp/harbor-prepare.log"
  exit 1
fi

# 启动
echo "Starting containers..."
docker compose up -d

# 等待所有容器 Healthy
echo "Waiting for all containers to be healthy..."
for i in $(seq 1 30); do
  UNHEALTHY=$(docker compose ps | grep -v healthy | grep -v Name | wc -l)
  if [ "$UNHEALTHY" -eq 0 ]; then
    echo "All containers healthy!"
    break
  fi
  echo "  Still waiting... ($UNHEALTHY containers not healthy)"
  sleep 10
done

# ──── Phase 6: 验证 ────
echo ""
echo "--- Phase 6: Verification ---"

# 验证 1: API 版本
NEW_VERSION_CHECK=$(curl -sf -u admin:Harbor12345 \
  https://harbor.company.com/api/v2.0/systeminfo | jq -r '.harbor_version')
echo "[1] Version: $NEW_VERSION_CHECK"
if [ "$NEW_VERSION_CHECK" != "$NEW_VERSION" ]; then
  echo "  ✗ Version mismatch!"
  exit 1
fi

# 验证 2: 健康检查
HEALTH=$(curl -sf -u admin:Harbor12345 \
  https://harbor.company.com/api/v2.0/health | jq -r '.status')
echo "[2] Health: $HEALTH"

# 验证 3: Portal 可访问
PORTAL_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
  https://harbor.company.com/)
echo "[3] Portal: HTTP $PORTAL_CODE"

# 验证 4: Docker pull/push
echo "[4] Testing docker pull/push..."
echo "Harbor12345" | docker login harbor.company.com -u admin --password-stdin > /dev/null
docker pull harbor.company.com/library/hello-world:latest > /dev/null 2>&1 && \
  echo "  ✓ Pull OK" || echo "  ✗ Pull FAILED"

# 验证 5: 审计日志可查询
AUDIT_COUNT=$(curl -sf -u admin:Harbor12345 \
  "https://harbor.company.com/api/v2.0/audit-logs?page_size=1" | jq '. | length')
echo "[5] Audit logs accessible: $([ "$AUDIT_COUNT" -gt 0 ] && echo '✓' || echo '✗')"

echo ""
echo "============================================"
echo "Upgrade to $NEW_VERSION completed at $(date)"
echo "Old version kept at: /opt/harbor"
echo "============================================"
```

### 3.3 步骤二：TLS 证书过期紧急修复

```bash
#!/bin/bash
# cert-emergency-fix.sh — 证书过期紧急修复

# ====== Phase 1: 诊断 ======
echo "=== Phase 1: Diagnose ==="
CERT_FILE="/etc/ssl/certs/harbor.crt"

echo "Certificate file: $CERT_FILE"
openssl x509 -in "$CERT_FILE" -noout -dates -subject -issuer 2>/dev/null || {
  echo "✗ Cannot read certificate file!"
  exit 1
}

# 检查到期时间
END_DATE=$(openssl x509 -in "$CERT_FILE" -noout -enddate | cut -d= -f2)
END_EPOCH=$(date -d "$END_DATE" +%s 2>/dev/null || echo 0)
NOW_EPOCH=$(date +%s)
DAYS_LEFT=$(( (END_EPOCH - NOW_EPOCH) / 86400 ))

echo ""
echo "Certificate expires: $END_DATE"
echo "Days remaining: $DAYS_LEFT"

if [ "$DAYS_LEFT" -lt 0 ]; then
  echo "🚨 CERTIFICATE HAS EXPIRED! ($(( -DAYS_LEFT )) days ago)"
elif [ "$DAYS_LEFT" -lt 30 ]; then
  echo "⚠️  Certificate expires in $DAYS_LEFT days — renew now!"
else
  echo "✓ Certificate is valid for $DAYS_LEFT more days"
fi

# ====== Phase 2: 重新签发证书 ======
echo ""
echo "=== Phase 2: Re-issue Certificate ==="

CERT_DIR="/etc/ssl/certs"
CA_CERT="$CERT_DIR/ca.crt"
CA_KEY="$CERT_DIR/ca.key"
NEW_KEY="$CERT_DIR/harbor-new.key"
NEW_CSR="$CERT_DIR/harbor-new.csr"
NEW_CRT="$CERT_DIR/harbor-new.crt"

# 生成新私钥
openssl genrsa -out "$NEW_KEY" 2048
echo "✓ New private key generated"

# 生成 CSR
openssl req -new -key "$NEW_KEY" -out "$NEW_CSR" \
  -subj "/CN=harbor.company.com/O=Company/C=CN"
echo "✓ CSR generated"

# 用自签名 CA 签发（如果使用外部 CA，替换为向 CA 提交 CSR 的流程）
openssl x509 -req -in "$NEW_CSR" \
  -CA "$CA_CERT" -CAkey "$CA_KEY" \
  -CAcreateserial -out "$NEW_CRT" \
  -days 365 -sha256
echo "✓ New certificate issued (valid 365 days)"

# 验证新证书
echo ""
echo "New certificate details:"
openssl x509 -in "$NEW_CRT" -noout -dates -subject

# ====== Phase 3: 更新配置 ======
echo ""
echo "=== Phase 3: Update Harbor Configuration ==="

# 备份旧证书
cp "$CERT_FILE" "$CERT_FILE.bak.$(date +%Y%m%d)"
cp "$CERT_DIR/harbor.key" "$CERT_DIR/harbor.key.bak.$(date +%Y%m%d)"

# 部署新证书
cp "$NEW_CRT" "$CERT_FILE"
cp "$NEW_KEY" "$CERT_DIR/harbor.key"

# 更新 harbor.yml 中的证书路径（如果变了）
# sed -i "s|certificate:.*|certificate: $CERT_FILE|" /opt/harbor/harbor.yml
# sed -i "s|private_key:.*|private_key: $CERT_DIR/harbor.key|" /opt/harbor/harbor.yml

# ====== Phase 4: 【关键】更新 Core 容器内部的 CA 信任库 ======
echo ""
echo "=== Phase 4: Update Core Container CA Trust Store ==="
# 这是最容易被忽略的一步！
# Core 容器内部有自己的 CA 证书库，用于验证与 Registry 的 TLS 通信

docker cp "$CA_CERT" harbor-core:/etc/ssl/certs/ca-certificates.crt
docker exec harbor-core update-ca-certificates 2>/dev/null || \
  docker exec harbor-core sh -c "cat /etc/ssl/certs/ca-certificates.crt >> /etc/ssl/certs/ca-bundle.crt"

echo "✓ Core container CA store updated"

# 同样更新 Registry 容器（如果它也验证 TLS）
docker cp "$CA_CERT" harbor-registry:/etc/ssl/certs/ca-certificates.crt 2>/dev/null
docker exec harbor-registry update-ca-certificates 2>/dev/null || true

# ====== Phase 5: 重新部署 ======
echo ""
echo "=== Phase 5: Redeploy Harbor ==="
cd /opt/harbor
./prepare && docker compose up -d

# 等待 healthy
echo "Waiting for Harbor to be ready..."
sleep 15
for i in $(seq 1 20); do
  if curl -sf -o /dev/null https://harbor.company.com/api/v2.0/health; then
    echo "✓ Harbor is healthy!"
    break
  fi
  echo "  Waiting... ($i/20)"
  sleep 10
done

# ====== Phase 6: 验证 ======
echo ""
echo "=== Phase 6: Verify ==="

# 验证 TLS 证书
echo "[1] TLS Verification:"
echo | openssl s_client -connect harbor.company.com:443 -servername harbor.company.com 2>/dev/null | \
  openssl x509 -noout -dates

# 验证 docker login
echo "[2] Docker Login:"
echo "Harbor12345" | docker login harbor.company.com -u admin --password-stdin 2>&1 | grep -E "Login Succeeded|Error"

# 验证 docker pull
echo "[3] Docker Pull:"
docker pull harbor.company.com/library/hello-world:latest 2>&1 | grep -E "Pull complete|Error|Already"

echo ""
echo "============================================"
echo "Certificate emergency fix completed"
echo "New certificate valid until: $(openssl x509 -in $NEW_CRT -noout -enddate | cut -d= -f2)"
echo "============================================"
```

### 3.4 步骤三：PostgreSQL 连接池调优

```bash
#!/bin/bash
# db-tuning.sh — PostgreSQL 连接池诊断与调优

echo "=== PostgreSQL Connection Pool Tuning ==="

# ---- 诊断 ----
echo ""
echo "--- Diagnose ---"

# 1. 当前 PostgreSQL 最大连接数
echo "[1] PostgreSQL max_connections:"
docker exec harbor-db psql -U postgres -t -c "SHOW max_connections;"

# 2. 当前活跃连接数
echo "[2] Current connections:"
docker exec harbor-db psql -U postgres -d registry -t -c \
  "SELECT 
    COUNT(*) as total,
    COUNT(*) FILTER (WHERE state = 'active') as active,
    COUNT(*) FILTER (WHERE state = 'idle') as idle,
    COUNT(*) FILTER (WHERE state = 'idle in transaction') as idle_in_txn,
    COUNT(*) FILTER (WHERE wait_event IS NOT NULL) as waiting
  FROM pg_stat_activity;"

# 3. 查看连接来源分布
echo "[3] Connection sources:"
docker exec harbor-db psql -U postgres -d registry -t -c \
  "SELECT 
    application_name, 
    client_addr, 
    COUNT(*) as conns
  FROM pg_stat_activity 
  WHERE state IS NOT NULL
  GROUP BY application_name, client_addr
  ORDER BY conns DESC
  LIMIT 10;"

# ---- 调优 ----
echo ""
echo "--- Tuning ---"

# 建议的参数调整（根据实际观测结果调整）
read -p "Current max_connections is low. Increase to 300? (y/n): " CONFIRM
if [ "$CONFIRM" = "y" ]; then
  # 调整 PostgreSQL
  docker exec harbor-db psql -U postgres -c \
    "ALTER SYSTEM SET max_connections = 300;"
  echo "✓ PostgreSQL max_connections set to 300"
  echo "⚠️  Need to restart PostgreSQL for changes to take effect"
fi

# 调整 harbor.yml 中的 Core 连接池参数
echo ""
echo "Recommended harbor.yml database settings:"
cat << 'EOF'
# harbor.yml
database:
  max_idle_conns: 50    # 空闲连接池大小
  max_open_conns: 200   # 最大打开连接数
  
# 经验公式：
# max_open_conns = PostgreSQL max_connections × 0.6 ~ 0.8
# 留下 20-40% 给管理员连接和其他服务
EOF

# 重启 PostgreSQL 使配置生效
read -p "Restart harbor-db now? (y/n): " CONFIRM
if [ "$CONFIRM" = "y" ]; then
  docker restart harbor-db
  echo "Waiting for PostgreSQL to be ready..."
  sleep 15
  
  # 验证新配置
  NEW_MAX=$(docker exec harbor-db psql -U postgres -t -c "SHOW max_connections;")
  echo "New max_connections: $NEW_MAX"
fi
```

### 3.5 步骤四：Harbor 完整备份与恢复演练

```bash
#!/bin/bash
# harbor-disaster-recovery.sh — 灾难恢复演练

echo "=== Harbor Disaster Recovery Drill ==="
echo "Scenario: Complete disk failure — restoring from backup"
echo "Time: $(date)"

BACKUP_DIR="/backup/harbor-20240301-020000"  # 替换为实际的备份目录
RESTORE_DIR="/opt/harbor-restore"

if [ ! -d "$BACKUP_DIR" ]; then
  echo "✗ Backup directory not found: $BACKUP_DIR"
  exit 1
fi

echo ""
echo "Using backup from: $BACKUP_DIR"
ls -lh "$BACKUP_DIR/"

# ====== Step 1: 准备恢复环境 ======
echo ""
echo "--- Step 1: Prepare Restore Environment ---"

# 清理旧的恢复目录
if [ -d "$RESTORE_DIR" ]; then
  echo "Removing old restore directory..."
  docker compose -f "$RESTORE_DIR/docker-compose.yml" down 2>/dev/null || true
  rm -rf "$RESTORE_DIR"
fi

# 重新安装 Harbor（空白实例）
cd /opt
cp -r harbor "$RESTORE_DIR"
cd "$RESTORE_DIR"

echo "✓ Fresh Harbor instance created at $RESTORE_DIR"

# ====== Step 2: 恢复配置文件 ======
echo ""
echo "--- Step 2: Restore Configuration ---"
cp "$BACKUP_DIR/harbor.yml" "$RESTORE_DIR/"
cp "$BACKUP_DIR/docker-compose.yml" "$RESTORE_DIR/" 2>/dev/null
echo "✓ Configuration restored"

# ====== Step 3: 恢复存储数据 ======
echo ""
echo "--- Step 3: Restore Storage Data ---"
# 先备份当前的空数据目录
mv /data/registry /data/registry.empty 2>/dev/null || true
mv /data/secret /data/secret.empty 2>/dev/null || true

# 从备份解压
echo "Extracting registry data (this may take a while)..."
tar -xzf "$BACKUP_DIR/harbor-registry-data.tar.gz" -C /
echo "✓ Storage data restored"

# ====== Step 4: 启动数据库 ======
echo ""
echo "--- Step 4: Start Database Only ---"
docker compose up -d harbor-db
echo "Waiting for PostgreSQL to be ready..."

for i in $(seq 1 30); do
  if docker exec harbor-db pg_isready -U postgres > /dev/null 2>&1; then
    echo "✓ PostgreSQL is ready"
    break
  fi
  echo "  Waiting... ($i/30)"
  sleep 3
done

# ====== Step 5: 恢复数据库 ======
echo ""
echo "--- Step 5: Restore Database ---"
DB_BACKUP="$BACKUP_DIR/harbor-db-20240301.sql"
if [ ! -f "$DB_BACKUP" ]; then
  echo "✗ Database backup not found: $DB_BACKUP"
  exit 1
fi

echo "Restoring database from: $DB_BACKUP"
docker exec -i harbor-db psql -U postgres registry < "$DB_BACKUP"

if [ $? -eq 0 ]; then
  echo "✓ Database restored successfully"
else
  echo "✗ Database restore FAILED!"
  exit 1
fi

# ====== Step 6: 启动全部服务 ======
echo ""
echo "--- Step 6: Start All Services ---"
cd "$RESTORE_DIR"
./prepare
docker compose up -d

echo "Waiting for all containers..."
for i in $(seq 1 30); do
  UNHEALTHY=$(docker compose ps | grep -v healthy | grep -v Name | wc -l)
  if [ "$UNHEALTHY" -eq 0 ]; then
    echo "✓ All containers healthy!"
    break
  fi
  sleep 10
done

# ====== Step 7: 恢复验证 ======
echo ""
echo "--- Step 7: Verification ---"

# 验证 1: API 可用
HEALTH=$(curl -sf -u admin:Harbor12345 \
  https://harbor.company.com/api/v2.0/health | jq -r '.status')
echo "[1] API Health: $HEALTH"

# 验证 2: 项目列表
PROJECT_COUNT=$(curl -sf -u admin:Harbor12345 \
  https://harbor.company.com/api/v2.0/projects?page_size=100 | jq '. | length')
echo "[2] Projects: $PROJECT_COUNT"

# 验证 3: 镜像可拉取（这是最关键的验证！）
echo "[3] Testing docker pull..."
echo "Harbor12345" | docker login harbor.company.com -u admin --password-stdin > /dev/null

# 尝试拉取一个已知镜像
docker pull harbor.company.com/library/hello-world:latest 2>&1 | tail -3

if [ $? -eq 0 ]; then
  echo "  ✓ Docker pull SUCCESS — recovery verified!"
else
  echo "  ✗ Docker pull FAILED — possible BLOB_UNKNOWN error"
  echo "  → Check if registry storage was included in the backup"
fi

# 验证 4: 审计日志
AUDIT_COUNT=$(curl -sf -u admin:Harbor12345 \
  "https://harbor.company.com/api/v2.0/audit-logs?page_size=1" | jq '. | length')
echo "[4] Audit logs: $([ "$AUDIT_COUNT" -gt 0 ] && echo '✓ accessible' || echo '✗ missing')"

echo ""
echo "============================================"
echo "Disaster Recovery Drill Completed"
echo "============================================"
```

### 3.6 步骤五：Harbor 每日健康检查脚本

```bash
#!/bin/bash
# harbor-daily-check.sh — 每日巡检脚本
# 添加到 crontab: 0 9 * * * /opt/scripts/harbor-daily-check.sh

HARBOR="https://harbor.company.com"
AUTH="admin:Harbor12345"
REPORT="/var/log/harbor-daily-check.log"
ALERT_THRESHOLD_DISK=80  # 磁盘告警阈值（百分比）
ALERT_THRESHOLD_QUOTA=90 # 配额告警阈值（百分比）

echo "============================================" > "$REPORT"
echo "Harbor Daily Health Check" >> "$REPORT"
echo "Time: $(date '+%Y-%m-%d %H:%M:%S')" >> "$REPORT"
echo "============================================" >> "$REPORT"
ALERTS=0

# ── 检查 1: 容器健康状态 ──
echo "" >> "$REPORT"
echo "[1] Container Status:" >> "$REPORT"
docker compose -f /opt/harbor/docker-compose.yml ps \
  --format "table {{.Name}}\t{{.Status}}" >> "$REPORT"

UNHEALTHY=$(docker compose -f /opt/harbor/docker-compose.yml ps | \
  grep -v "healthy" | grep -v "Name" | grep -v "Exit" | wc -l)
if [ "$UNHEALTHY" -gt 0 ]; then
  echo "⚠️  ALERT: $UNHEALTHY containers not healthy!" >> "$REPORT"
  ALERTS=$((ALERTS + 1))
fi

# ── 检查 2: API 健康检查 ──
echo "" >> "$REPORT"
echo "[2] API Health:" >> "$REPORT"
HEALTH_RESP=$(curl -sf -u "$AUTH" "$HARBOR/api/v2.0/health" 2>/dev/null)
if [ $? -eq 0 ]; then
  echo "$HEALTH_RESP" | jq '.' >> "$REPORT" 2>/dev/null || echo "$HEALTH_RESP" >> "$REPORT"
else
  echo "🚨 ALERT: API health check FAILED!" >> "$REPORT"
  ALERTS=$((ALERTS + 1))
fi

# ── 检查 3: 磁盘使用率 ──
echo "" >> "$REPORT"
echo "[3] Disk Usage:" >> "$REPORT"
df -h /data >> "$REPORT"
DISK_USAGE=$(df /data --output=pcent | tail -1 | tr -d ' %')
if [ "$DISK_USAGE" -gt "$ALERT_THRESHOLD_DISK" ]; then
  echo "⚠️  ALERT: Disk usage at ${DISK_USAGE}% (threshold: ${ALERT_THRESHOLD_DISK}%)" >> "$REPORT"
  ALERTS=$((ALERTS + 1))
fi

# ── 检查 4: 数据库连接数 ──
echo "" >> "$REPORT"
echo "[4] Database Connections:" >> "$REPORT"
docker exec harbor-db psql -U postgres -d registry -t -c \
  "SELECT 'Active: ' || COUNT(*) FILTER (WHERE state='active') || 
          ', Idle: ' || COUNT(*) FILTER (WHERE state='idle') ||
          ', Max: ' || current_setting('max_connections') AS connection_summary
   FROM pg_stat_activity;" >> "$REPORT" 2>/dev/null || \
  echo "⚠️  Cannot query database" >> "$REPORT"

# ── 检查 5: 最近 24 小时的异常审计日志 ──
echo "" >> "$REPORT"
echo "[5] Recent DELETE Operations (last 24h):" >> "$REPORT"
SINCE=$(date -d '24 hours ago' +%s)
DELETE_COUNT=$(curl -sf -u "$AUTH" \
  "$HARBOR/api/v2.0/audit-logs?page_size=5&begin_timestamp=$SINCE&operation=DELETE" | \
  jq '. | length' 2>/dev/null)

echo "  DELETE operations: ${DELETE_COUNT:-'N/A'}" >> "$REPORT"

if [ "${DELETE_COUNT:-0}" -gt 10 ]; then
  echo "⚠️  ALERT: $DELETE_COUNT DELETE operations in 24h (unusual)" >> "$REPORT"
  ALERTS=$((ALERTS + 1))
fi

# ── 检查 6: 证书过期倒计时 ──
echo "" >> "$REPORT"
echo "[6] TLS Certificate:" >> "$REPORT"
CERT_FILE="/etc/ssl/certs/harbor.crt"
if [ -f "$CERT_FILE" ]; then
  END_DATE=$(openssl x509 -in "$CERT_FILE" -noout -enddate 2>/dev/null | cut -d= -f2)
  END_EPOCH=$(date -d "$END_DATE" +%s 2>/dev/null || echo 0)
  NOW_EPOCH=$(date +%s)
  DAYS_LEFT=$(( (END_EPOCH - NOW_EPOCH) / 86400 ))
  echo "  Expires: $END_DATE ($DAYS_LEFT days remaining)" >> "$REPORT"
  if [ "$DAYS_LEFT" -lt 30 ]; then
    echo "⚠️  ALERT: TLS certificate expires in $DAYS_LEFT days!" >> "$REPORT"
    ALERTS=$((ALERTS + 1))
  fi
else
  echo "⚠️  Certificate file not found!" >> "$REPORT"
fi

# ── 检查 7: 项目配额使用（TOP 5） ──
echo "" >> "$REPORT"
echo "[7] Top 5 Projects by Storage Usage:" >> "$REPORT"
curl -sf -u "$AUTH" "$HARBOR/api/v2.0/projects?page_size=100" | \
  jq -r 'sort_by(.storage_used / .storage_limit) | reverse | .[0:5][] | 
    "  \(.name): \(.storage_used / 1073741824 * 100 | round/100)GB / \(.storage_limit / 1073741824)GB (\(.storage_used / .storage_limit * 10000 | round/100)%)"' \
  >> "$REPORT" 2>/dev/null

# ── 总结 ──
echo "" >> "$REPORT"
echo "============================================" >> "$REPORT"
if [ "$ALERTS" -eq 0 ]; then
  echo "✅ All checks passed — Harbor is healthy" >> "$REPORT"
else
  echo "⚠️  $ALERTS alert(s) found — please investigate" >> "$REPORT"
fi
echo "============================================" >> "$REPORT"

# 如果有告警，发送通知
if [ "$ALERTS" -gt 0 ]; then
  # 发送到企业微信/钉钉/Slack
  curl -sf -X POST "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=KEY" \
    -H "Content-Type: application/json" \
    -d "{\"msgtype\":\"text\",\"text\":{\"content\":\"Harbor Daily Check: $ALERTS alert(s) found. Check $REPORT\"}}" \
    > /dev/null 2>&1
fi

# 输出到控制台
cat "$REPORT"
```

### 3.7 步骤六：磁盘空间清理与 GC 策略

```bash
#!/bin/bash
# harbor-gc-management.sh — GC 管理脚本

HARBOR="https://harbor.company.com"
AUTH="admin:Harbor12345"

echo "=== Harbor Storage Management ==="

# ---- 检查 GC 状态 ----
echo ""
echo "--- Current GC Status ---"
GC_HISTORY=$(curl -sf -u "$AUTH" "$HARBOR/api/v2.0/system/gc" | jq '.')
GC_LAST=$(echo "$GC_HISTORY" | jq -r '.[0] | 
  "Status: \(.job_status), Time: \(.update_time)"')
echo "$GC_LAST"

# ---- 检查 GC 定时任务 ----
echo ""
echo "--- GC Schedule ---"
GC_SCHEDULE=$(curl -sf -u "$AUTH" "$HARBOR/api/v2.0/system/gc/schedule" 2>/dev/null)
if echo "$GC_SCHEDULE" | jq -e '.schedule' > /dev/null 2>&1; then
  echo "$GC_SCHEDULE" | jq '{type: .schedule.type, cron: .schedule.cron}'
else
  echo "GC schedule not configured — setting up now..."
  
  # 创建 GC 定时任务（每周日凌晨 2 点执行）
  curl -sf -u "$AUTH" \
    -X POST \
    -H "Content-Type: application/json" \
    -d '{
      "schedule": {
        "type": "Custom",
        "cron": "0 2 * * 0"
      },
      "parameters": {
        "delete_untagged": true,
        "dry_run": false,
        "workers": 3
      }
    }' \
    "$HARBOR/api/v2.0/system/gc/schedule" | jq '.'
  echo "✓ Weekly GC schedule created (Sunday 2:00 AM)"
fi

# ---- 手动触发 GC（紧急清理） ----
echo ""
read -p "Trigger manual GC now? This will make Registry read-only during GC! (y/n): " CONFIRM
if [ "$CONFIRM" = "y" ]; then
  echo "Starting manual GC..."
  RESPONSE=$(curl -sf -u "$AUTH" \
    -X POST \
    -H "Content-Type: application/json" \
    -d '{
      "schedule": {
        "type": "Manual"
      },
      "parameters": {
        "delete_untagged": true,
        "dry_run": false
      }
    }' \
    "$HARBOR/api/v2.0/system/gc/schedule")
  echo "GC triggered: $RESPONSE"
  echo "⚠️  Registry is now READ-ONLY until GC completes"
fi

# ---- 磁盘空间分析 ----
echo ""
echo "--- Disk Space Analysis ---"
echo "Registry storage:"
du -sh /data/registry/ 2>/dev/null

echo ""
echo "Largest directories under /data/registry/:"
du -h /data/registry/ 2>/dev/null | sort -rh | head -10

echo ""
echo "Docker volumes:"
docker system df 2>/dev/null

# ---- 清理 Docker 构建缓存 ----
echo ""
echo "--- Docker Cache ---"
read -p "Clean Docker build cache? (y/n): " CONFIRM
if [ "$CONFIRM" = "y" ]; then
  docker builder prune -f
  echo "✓ Docker build cache cleaned"
fi
```

### 3.8 可能遇到的坑

**坑1：升级后旧配置模板被覆盖——`harbor.yml` 参数不兼容**

现象：升级到新版后 `./prepare` 不报错，但 Core 反复重启。

根因：新版 `harbor.yml.tmpl` 新增了必填参数，直接复制旧版 `harbor.yml` 会导致新参数使用空默认值。

预防方案：
```bash
# 升级前务必执行差异对比
diff /opt/harbor/harbor.yml /opt/harbor-新版本/harbor.yml.tmpl | grep '^[<>]'

# 重点关注：
# 1. 新增的参数（新文件中有的行，旧文件没有）
# 2. 参数名变化（如 trivy 相关参数从无到有）
# 3. 默认值变化（如数据库连接池默认值调整）

# 正确做法：以新版 harbor.yml.tmpl 为模板，手工填入自定义参数
# 错误做法：直接 cp /opt/harbor/harbor.yml /opt/harbor-v2.12/harbor.yml
```

**坑2：`docker compose restart` 与 `docker compose down && up -d` 的行为差异**

现象：修改了 `harbor.yml` 或 `docker-compose.yml` 后执行 `restart`，配置未生效。

根因：
- `docker compose restart`：只重启容器进程，**不重新读取** `docker-compose.yml` 的变更（如健康检查配置、资源限制、环境变量）
- `docker compose down && up -d`：完全销毁并重建容器，读取最新的 `docker-compose.yml` 和配置文件

```bash
# ✅ 修改配置后务必执行完整的 down && up
cd /opt/harbor
./prepare                    # 重新生成各容器的配置文件
docker compose down          # 销毁所有容器（保留 volumes）
docker compose up -d         # 基于最新配置重建容器

# ❌ 不要这样做
docker compose restart       # 配置变更不会生效！
```

**坑3：证书更新后 Core 仍报 TLS 错误——内部 CA 库未同步**

现象：更新了 Nginx 证书，Portal 正常，但 Core 日志中报 `x509: certificate signed by unknown authority`。

根因：Core 容器内部的 `/etc/ssl/certs/` 信任库独立于宿主机的证书系统。更新宿主机的证书后，Core 容器内部仍然缓存着旧证书。

解决：
```bash
# 不仅更新 Nginx 的证书，还要更新 Core 和 Registry 容器内部的 CA 信任库
docker cp /etc/ssl/certs/ca.crt harbor-core:/usr/local/share/ca-certificates/
docker exec harbor-core update-ca-certificates

docker cp /etc/ssl/certs/ca.crt harbor-registry:/usr/local/share/ca-certificates/
docker exec harbor-registry update-ca-certificates 2>/dev/null || true

# 对于没有 update-ca-certificates 的容器，手动追加
docker exec harbor-core sh -c \
  "cat /usr/local/share/ca-certificates/ca.crt >> /etc/ssl/certs/ca-bundle.crt"
```

**坑4：Harbor admin 默认密码未修改——安全漏洞**

现象：Harbor 安装后 `admin` 密码默认为 `Harbor12345`，扫描工具立刻标记为高危漏洞。

解决：
```bash
# 方法一：Portal 修改
# 登录 Portal → 用户管理 → admin → 修改密码

# 方法二：API 修改
curl -X PUT -u admin:Harbor12345 \
  -H "Content-Type: application/json" \
  -d '{"old_password":"Harbor12345","new_password":"Str0ng!N3wP@ssw0rd"}' \
  "https://harbor.company.com/api/v2.0/users/1/password"

# 方法三：在 harbor.yml 中预设（安装前）
# harbor_admin_password: Str0ng!N3wP@ssw0rd

# 验证密码强度
# ✓ 至少 12 字符
# ✓ 包含大写、小写、数字、特殊字符
# ✓ 不是常见密码字典中的值
```

---

## 4 项目总结

### 4.1 Harbor 常见故障排查入口速查表

| 症状 | 排查容器 | 关键命令 | 最可能根因（Top 3） | 首次修复时间预期 |
|------|---------|---------|-------------------|----------------|
| `docker login` 返回 401 | harbor-core | `docker logs harbor-core \| grep -i "auth\|token\|login"` | ① 密码错误/过期 ② OIDC 服务不可达 ③ 用户被禁用 | 5-15 分钟 |
| `docker push` 返回 500 | harbor-registry + harbor-core | `docker logs registry \| grep -i "error\|quota\|denied"` | ① 存储配额超限 (85%) ② 磁盘空间不足 ③ 项目不存在 | 10-20 分钟 |
| `docker pull` 返回 503 | harbor-core + nginx | `docker logs harbor-core \| grep -i "panic\|fatal\|error"` | ① Core 未就绪 ② 数据库连接池耗尽 ③ Nginx upstream 超时 | 10-30 分钟 |
| Portal 页面 502 | nginx + harbor-core | `docker logs nginx \| grep "upstream"` + `docker compose ps` | ① Core 挂了 ② Core URL 配置错误 ③ Nginx 配置语法错误 | 5-15 分钟 |
| Portal 白屏/空白页 | harbor-portal | `docker logs harbor-portal \| tail -50` | ① Core URL 无法解析 ② JS 资源 404 ③ 浏览器缓存 | 5-10 分钟 |
| Core 反复重启 | harbor-core | `docker logs harbor-core --tail 50 \| grep -i "panic\|fatal\|error\|failed"` | ① PostgreSQL 未就绪 ② Redis 连接失败 ③ harbor.yml 配置语法错误 | 15-45 分钟 |
| TLS/证书错误 | nginx + openssl | `openssl x509 -in <cert> -noout -dates -subject` | ① 证书已过期 (90%) ② 证书域名不匹配 ③ Core 内部 CA 库未同步 | 30-60 分钟 |
| API 返回大量 502 | harbor-core + harbor-db | `docker logs harbor-core \| grep "database\|connection"` | ① 数据库连接池耗尽 ② PostgreSQL 最大连接数不足 ③ Core 内存 OOM | 15-30 分钟 |

### 4.2 Harbor 日常运维 SOP 总表

| 频率 | 操作 | 命令/工具 | 预期结果 | 异常处理 |
|------|------|---------|---------|---------|
| 每日 | 容器健康状态检查 | `docker compose ps` | 所有容器 Status: healthy | 异常容器查日志 → 重启 → 若仍异常则告警 |
| 每日 | 磁盘使用率检查 | `df -h /data` | 使用率 < 80% | 超过 80%：清理旧镜像 + GC；超过 95%：紧急扩容 |
| 每日 | API 健康检查 | `curl /api/v2.0/health` | `{"status":"healthy"}` | 不健康：检查 Core/DB/Redis 状态 |
| 每周 | 审计日志异常检查 | API 查询 DELETE 操作频率 | 周 DELETE < 日常 3σ | 异常：检查是否有未授权的批量删除 |
| 每周 | 证书过期检查 | `openssl x509 -enddate` | 剩余 > 30 天 | < 30 天：启动证书更新流程 |
| 每周 | GC 定时执行（自动） | 已配 CronJob 则自动 | GC 完成，释放空间 | GC 失败：手动触发 + 检查磁盘空间 |
| 每月 | 全量备份 | DB + Storage + 配置 | 备份文件完整性验证通过 | 备份失败：立即检查磁盘空间和权限 |
| 每月 | 备份恢复演练 | 在 Staging 完整恢复 | `docker pull` 成功拉取镜像 | 恢复失败：检查备份完整性，修正备份脚本 |
| 每季度 | Harbor 版本升级评估 | 阅读 Release Notes | 识别 Breaking Changes 和新增功能 | 有重大变更：在 Staging 先验证 |
| 每年 | TLS 证书更新 | 重新签发 + 更新所有容器 CA | 新旧证书平滑切换 | 失败：保留旧证书，回滚配置 |

### 4.3 备份策略对比

| 备份维度 | 最小方案（不推荐） | 标准方案（生产级） | 高级方案（金融级） |
|---------|-----------------|-----------------|-----------------|
| 数据库 | 仅 pg_dump | pg_dump + WAL 归档 | pg_dump + WAL + 流复制 |
| 存储 Blob | 无备份 ❌ | tar 打包备份 | rsync 增量同步 + 快照 |
| 配置文件 | 无备份 | 备份 harbor.yml + docker-compose.yml | Git 版本管理 |
| 备份频率 | 无周期 | 每日增量 + 每周全量 | 实时 WAL + 每日全量 |
| 异地副本 | 无 | 每周 rsync 到异地 | 实时复制 + 跨地域 DR |
| 恢复验证 | 无验证 | 每月恢复演练 | 每季度全链路 DR 演练 |
| RPO (数据丢失) | 100% (无法恢复) | < 7 天 | < 1 小时 |
| RTO (恢复时间) | 无限（无法恢复） | < 4 小时 | < 1 小时 |

### 4.4 常见踩坑经验（故障排查表）

| 故障现象 | 根因分析 | 排查路径 | 解决方案 | 预防措施 |
|---------|---------|---------|---------|---------|
| 升级后 Core 反复重启 | 新旧 harbor.yml 参数不兼容 | `docker logs harbor-core --tail 100` | 以新版 harbor.yml.tmpl 为模板重建配置 | 升级前 diff 对比新旧 tmpl |
| 证书更新后 Core 仍报 TLS 错 | Core 容器内部 CA 库未同步更新 | `docker exec harbor-core ls /etc/ssl/certs/` | `docker cp` 更新容器内 CA 文件 | 更新证书 SOP 中包含容器 CA 同步步骤 |
| 备份恢复后镜像拉取 BLOB_UNKNOWN | 只恢复了数据库，未恢复 Blob 文件 | `ls /data/registry/docker/registry/v2/blobs/` | 使用完整备份（DB + Storage + Config） | 备份脚本同时包含三部分数据 |
| GC 执行期间 Push 被拒绝 | GC 期间 Registry 为只读模式 | 查看 GC Job 状态和预计时间 | 等待 GC 完成或安排在业务低峰期 | GC 定时在凌晨 2:00-5:00 执行 |
| `docker login` 成功但 Pull 失败 | 用户有登录权限但无该项目的拉取权限 | 查用户的项目角色 + API 返回 403 | 为用户添加对应项目的至少访客角色 | 使用机器人账户时检查权限范围 |

### 4.5 思考题

1. **Harbor 的 `harbor.yml` 有 150+ 个配置参数。请设计一个配置验证脚本（`harbor-config-check.sh`），在 `./prepare` 之前自动执行以下检查：**
   - (1) 必填参数是否填写（hostname、certificate、private_key、admin_password 等 10 个关键参数）
   - (2) 证书文件路径是否存在且证书有效期 > 30 天
   - (3) 端口是否被占用（`netstat -tlnp` 检查 80/443/4443 等）
   - (4) 数据目录 `/data/` 是否有足够的可用空间（至少 20GB）
   - (5) 数据库连接参数是否正确（尝试连接 PostgreSQL）
   - (6) 输出 PASS/FAIL 汇总报告，有 FAIL 则阻止后续的 `./prepare` 执行
   要求：脚本必须支持 `--fix` 模式，对于可自动修复的问题（如端口冲突提示更换、证书路径不存在提示创建）给出交互式修复建议。

2. **某周六凌晨 3:15，你被 On-Call 电话叫醒：所有 CI/CD Pipeline 失败，K8s 集群中出现大量 `ImagePullBackOff`，Portal 打不开。根据 Harbor 的架构，请设计一个"Harbor 应急恢复三分钟检查单"：**
   - (1) 按优先级列出前 5 个检查项（从最可能到最不可能）
   - (2) 每个检查项的正常值是什么，异常时的快速恢复动作是什么
   - (3) 如果前 5 个检查都正常，接下来查什么（补充 3 个深度排查项）
   要求：写成一张可打印的 A4 卡片，on-call 人员在迷糊状态下 3 分钟内也能按卡操作。

---

> 下一章预告：第 16 章是基础篇的综合实战——从零搭建企业级私有镜像仓库并接入 GitLab CI/CD，在一个工作日内端到端贯通基础篇全部知识。
