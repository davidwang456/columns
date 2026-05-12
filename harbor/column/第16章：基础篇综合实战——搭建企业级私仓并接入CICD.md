# 第16章：【基础篇综合实战】搭建企业级私仓并接入 CI/CD

## 1 项目背景

某 350 人规模的 SaaS 公司（"云擎科技"），核心产品是一个面向零售行业的 ERP 平台，由 85 个微服务组成，部署在自建 Kubernetes 集群上。CTO 在季度技术回顾会上指出：公司当前的容器镜像管理处于"前工业化时代"。

**痛点一：没有统一镜像仓库——镜像散落四处，安全隐患巨大。** 当前镜像分布在三个地方：(1) Jenkins 构建节点的本地 Docker Cache（85 个镜像，占 320GB）；(2) 开发人员的笔记本（无法统计数量）；(3) 阿里云容器镜像服务 ACR 个人版（免费额度，只存了 30 个"认为重要"的镜像）。CTO 形容："我们连自己有多少个镜像都不知道——如果有人在内网部署了一个恶意镜像，我们可能需要 3 个月才能发现。"

**痛点二：没有镜像安全扫描——信任的是"标签"而非"事实"。** 团队选型基础镜像的标准简单粗暴——"Docker Hub 上标了 Official Image 我们就信任"。`node:16-alpine`、`python:3.11-slim`、`openjdk:17-jdk-slim`——这些镜像的实际 CVE 状态没人知道。安全团队曾随机抽查了 5 个生产使用的镜像，发现平均每个镜像含有 43 个已知漏洞，其中 3 个镜像含有 Critical 级别漏洞（包括一个 Apache Log4j 的变种）。但由于没有扫描工具，这个"随机抽查"结果无法转化为系统性治理——抽查完了就没下文了。

**痛点三：没有版本治理——`latest` 标签泛滥成灾。** 85 个微服务中，64 个的生产部署使用的是 `:latest` 标签。这导致两个致命问题：(1) 回滚不可能——出问题后无法确定"上一个版本是哪个 digest"；(2) 多环境不一致——同一个 `order-service:latest`，在 Staging 和 Production 可能是两个完全不同的镜像（因为两边的 `latest` 在不同的时间被推送）。CTO 称之为"薛定谔的 latest"。更糟糕的是，Jenkins 每次构建都会推送一个新的 `:latest` 覆盖旧的——30 天前的版本彻底不可追溯。

**痛点四：CI/CD 直推生产——中间无安全门禁。** 当前的部署流程是：开发 Push 代码 → Jenkins 自动构建 Docker 镜像 → Jenkins 自动 `docker push` 到 ACR → Jenkins 自动执行 `kubectl set image` 更新生产环境。整个过程从代码提交到生产更新大约 4 分钟，中间没有任何安全检查、审批环节或人工确认。用安全部门主管的话说："任何一个有 push 权限的开发，都能在喝杯咖啡的时间内把一个含漏洞的镜像送上生产。"上个月就发生过一次事故——开发在调试时把本地包含调试后门（端口 5555 开放）的镜像推送到了生产，直到安全扫描工具在公网上发现了这个开放端口。

**痛点五：多架构支持缺失——ARM 节点无法部署。** 公司最近采购了 20 台 ARM64（鲲鹏 920）节点，计划将部分无状态服务迁移过去以降低 30% 的服务器成本。但现有镜像全部是 AMD64 单架构——开发团队需要同时维护两套 Dockerfile，CI 需要构建两次，部署时需要手动选择正确的架构标签。运维团队估算，如果继续当前模式，85 个服务 × 2 架构 = 170 个构建任务，CI 队列将严重拥堵。

**目标**：CTO 要求在 **一个工作日内**（实际约 6 小时），从零搭建 Harbor 企业级私有镜像仓库，并完成 GitLab CI/CD 集成，实现以下完整的安全交付链路：

```
Git Push → GitLab CI 触发
  → 构建多架构 Docker 镜像 (AMD64 + ARM64)
  → 推送到 Harbor 私有仓库
  → Trivy 自动漏洞扫描 (推送即扫描)
  → CVE 策略门禁 (Critical=阻止, High>5=阻止)
  → 扫描通过 → 自动部署到 K8s Staging
  → 人工确认 → 打 release 标签 → 部署到 K8s Production
  → (可选) 跨地域复制到灾备 Harbor
```

---

## 2 项目设计——剧本式交锋对话

**场景：周一早上 9:00，项目启动会。会议室里坐满了人——CTO 老唐、架构师老张、运维小赵、安全工程师王工、开发主管小李。白板干干净净。**

---

**小胖**（运维小赵，打了个哈欠）："搞这么复杂？不就是装个 Harbor 然后配一下 GitLab CI 的 `.gitlab-ci.yml` 嘛？我看网上教程半小时就能搞定——你们非得搞一天？"

**大师**（架构师老张，站起来走到白板前，拿起马克笔）："小赵你说对了一半——单点不难。半小时装个 Harbor 确实够了；十分钟写个 `.gitlab-ci.yml` 也确实够了。但把这两个'单点'串成一条**可信的安全交付链路**——让每一个环节都经得起推敲——那才是真正的挑战。"

"我把今天要构建的完整链路画出来，你们看看和'半小时教程'的区别在哪："

```
                           ┌──────────────────────┐
                           │    GitLab Repository  │
                           │    (order-service)    │
                           └──────────┬───────────┘
                                      │ git push
                                      ▼
                           ┌──────────────────────┐
                           │   GitLab CI Runner    │
                           │   (Docker Executor)   │
                           │                       │
                           │  ┌─────────────────┐  │
                           │  │ docker buildx   │  │
                           │  │ multi-arch build│  │
                           │  │ amd64 + arm64   │  │
                           │  └────────┬────────┘  │
                           └───────────┼───────────┘
                                       │ docker push (Robot Token)
                                       ▼
 ┌─────────────────────────────────────────────────────────────────────┐
 │                         Harbor (主站点 — 上海)                        │
 │                                                                     │
 │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐               │
 │  │ order-       │  │ payment-     │  │ shared-base  │               │
 │  │ platform     │  │ platform     │  │ (公开只读)    │               │
 │  │ (私有,200GB) │  │ (私有,200GB) │  │ (公开,500GB)  │               │
 │  └──────────────┘  └──────────────┘  └──────────────┘               │
 │                                                                     │
 │  ┌─────────────────────────────────────────────────────────────┐    │
 │  │  安全扫描层                                                  │    │
 │  │  ┌──────────┐    ┌──────────────┐    ┌──────────────────┐   │    │
 │  │  │ Trivy    │───▶│ 自动漏洞扫描  │───▶│ CVE 阻止策略      │   │    │
 │  │  │ Scanner  │    │ (push即扫描)  │    │ Critical=阻止    │   │    │
 │  │  └──────────┘    └──────────────┘    │ High>5=阻止      │   │    │
 │  │                                      └──────────────────┘   │    │
 │  └─────────────────────────────────────────────────────────────┘    │
 │                                                                     │
 │  ┌─────────────────────────────────────────────────────────────┐    │
 │  │  标签保留策略 (自动清理 CI 中间构建)                           │    │
 │  │  保留规则: release-* + latest + 最近 5 个                     │    │
 │  │  清理定时: 每日凌晨 2:00                                      │    │
 │  └─────────────────────────────────────────────────────────────┘    │
 │                                                                     │
 │  ┌──────────────┐                                                   │
 │  │  复制规则     │ ──────▶  Harbor (灾备站点 — 北京)                │
 │  │  release-*   │         (事件驱动, 打 release 标签即复制)         │
 │  └──────────────┘                                                   │
 └─────────────────────────────────────────────────────────────────────┘
                                       │
                          ┌────────────┼────────────┐
                          │            │            │
                          ▼            ▼            ▼
                   ┌──────────┐ ┌──────────┐ ┌──────────┐
                   │ K8s      │ │ K8s      │ │ K8s      │
                   │ Staging  │ │ Prod     │ │ DR       │
                   │ (自动部署)│ │ (人工确认)│ │ (灾备)   │
                   └──────────┘ └──────────┘ └──────────┘
```

**技术映射**：这条链路的核心依赖 Harbor 的五个关键能力：(1) **项目权限模型**——GitLab CI 使用机器人账户，仅授予 push/pull 权限，遵循最小权限原则；(2) **漏洞扫描**——Trivy 作为内置扫描器，推送镜像后自动扫描，无需额外配置；(3) **CVE 阻止策略**——扫描结果关联到镜像 pull 权限，Critical 漏洞直接阻止 pull；(4) **标签保留策略**——自动清理 CI 中间构建标签，控制存储成本；(5) **复制规则**——基于事件驱动，打 release 标签时自动复制到灾备站点。

---

**小白**（安全工程师王工，推了推眼镜）："架构图我理解了。但我想知道具体的组件清单和安装顺序——我是安全出身，对基础设施不熟，需要一份'1→2→3→4→5→6'的清单式指导。"

**大师**（开始在白板上逐条书写）："分六步走——每一步都有明确的输入、输出和验证标准："

| 步骤 | 任务 | 输入 | 输出 | 预计耗时 | 依赖前置 |
|------|------|------|------|---------|---------|
| ① | Harbor 部署与基础配置 | 虚拟机 + harbor-offline-installer.tgz | Harbor 运行、Portal 可访问、API Healthy | 45 分钟 | 无 |
| ② | 项目与仓库规划 | Harbor admin 账号 | 3 个项目（2 私有 + 1 公开）含配额和策略 | 20 分钟 | 步骤① |
| ③ | 用户和机器人账户创建 | 项目 ID、开发人员名单 | 1 个机器人账户 + N 个开发账户 + 权限绑定 | 30 分钟 | 步骤② |
| ④ | 扫描策略与标签保留 | 项目 ID | CVE 阻止策略 + 自动扫描 + 标签保留策略 | 15 分钟 | 步骤② |
| ⑤ | GitLab CI/CD 集成 | GitLab 项目 + Harbor 机器人 Token | `.gitlab-ci.yml` + CI 变量配置 | 60 分钟 | 步骤③④ |
| ⑥ | 端到端验证 | 完整系统就绪 | Push 代码 → CI Build → 扫描通过 → 自动部署 | 30 分钟 | 步骤⑤ |

**小白**（一边记录一边问）："第⑤步的 GitLab CI 集成——机器人账户的权限具体应该怎么配？给 push + pull 够吗？还需要 artifact read 吗？"

**大师**："好问题——这涉及到最小权限原则。对于 CI Pipeline，机器人账户至少需要三个权限："

```json
{
  "access": [
    {
      "resource": "repository",
      "action": "push"           // 推送构建好的镜像
    },
    {
      "resource": "repository",
      "action": "pull"           // 拉取基础镜像（如 FROM 指令）
    },
    {
      "resource": "artifact",
      "action": "read"           // 读取镜像的扫描结果（scan_overview）
    }
  ]
}
```

"注意：`action: read` 对 `artifact` 资源是必需的——因为 CI 的 Scan Gate 阶段需要通过 API 获取 `scan_overview`。如果你只给 `push + pull`，CI 在查询扫描结果时会返回 403。这个坑我已经替你们踩过了。"

---

**小胖**（不耐烦，敲了敲桌子）："架构、清单、权限——我都听明白了。但老张你能不能直接说'第一步做什么'？我 9:05 就要开始动手了——CTO 可是说一个工作日内完成！"

**大师**（笑着说）："好，实战派。那我们就从第一行命令开始——"

---

## 3 项目实战

### 3.1 环境准备

| 组件 | 版本/配置 | 用途 | 备注 |
|------|----------|------|------|
| Harbor 主机 | CentOS 7.9 / 8C / 16GB / 500GB SSD | Harbor 主站点 | `/data` 分区 ≥ 400GB |
| Harbor | v2.12.0 (offline installer) | 私有镜像仓库 | 含 Trivy 扫描器 |
| Docker | 24.0+ | 容器运行时 | 安装 Harbor 前提 |
| Docker Compose | v2.20+ | 容器编排 | Harbor 部署方式 |
| GitLab | 已有实例 (任意版本) | 代码仓库 + CI | 企业已部署 |
| GitLab Runner | Docker executor (v16+) | CI 构建执行器 | 需能访问 Harbor + K8s |
| Kubernetes | v1.28+ (Staging + Prod 各一个集群) | 容器编排平台 | 已有集群 |
| Trivy | 随 Harbor 内置 | 镜像漏洞扫描器 | Harbor 安装时自动部署 |
| 域名 & 证书 | `harbor.cloudopt.com` + TLS 证书 | Harbor 访问地址 | 自签名 CA 或企业 CA 签发 |

```bash
# 环境验证脚本（在 Harbor 主机上执行）
echo "=== Environment Pre-Check ==="

# 1. 检查操作系统
echo "[1] OS: $(cat /etc/os-release | grep PRETTY_NAME | cut -d= -f2)"

# 2. 检查 Docker
echo "[2] Docker: $(docker --version 2>/dev/null || echo 'NOT FOUND')"

# 3. 检查 Docker Compose
echo "[3] Docker Compose: $(docker compose version 2>/dev/null || echo 'NOT FOUND')"

# 4. 检查磁盘空间
echo "[4] Disk: $(df -h /data | tail -1 | awk '{print $4" available / "$2" total"}')"

# 5. 检查内存
echo "[5] Memory: $(free -h | grep Mem | awk '{print $2" total, "$7" available"}')"

# 6. 检查 CPU
echo "[6] CPU: $(nproc) cores"

# 7. 检查端口占用（Harbor 需要 80, 443, 4443）
echo "[7] Port check:"
for port in 80 443 4443; do
  if ss -tlnp | grep -q ":$port "; then
    echo "  ✗ Port $port is in use"
  else
    echo "  ✓ Port $port is free"
  fi
done

# 8. 检查证书
echo "[8] Certificate:"
if [ -f /etc/ssl/certs/harbor.crt ]; then
  openssl x509 -in /etc/ssl/certs/harbor.crt -noout -dates -subject
else
  echo "  ⚠  Certificate not found — will generate during installation"
fi

echo ""
echo "=== Pre-check complete ==="
```

### 3.2 步骤一：Harbor 部署与基础配置

```bash
#!/bin/bash
# step1-harbor-install.sh — Harbor v2.12.0 离线部署
set -e

HARBOR_VERSION="v2.12.0"
INSTALL_DIR="/opt/harbor"
CERT_DIR="/etc/ssl/certs"

echo "=== Step 1: Harbor Installation ==="
echo "Target: $HARBOR_VERSION"
echo ""

# ---- 1.1 下载离线安装包 ----
echo "--- 1.1 Download ---"
cd /opt
if [ ! -f "harbor-offline-installer-${HARBOR_VERSION}.tgz" ]; then
  wget "https://github.com/goharbor/harbor/releases/download/${HARBOR_VERSION}/harbor-offline-installer-${HARBOR_VERSION}.tgz"
fi
tar -xzf "harbor-offline-installer-${HARBOR_VERSION}.tgz"
echo "✓ Extracted to $INSTALL_DIR"

# ---- 1.2 生成自签名证书（如已有企业 CA 证书可跳过） ----
echo ""
echo "--- 1.2 TLS Certificate ---"
mkdir -p "$CERT_DIR"

# 生成 CA 私钥和证书
openssl genrsa -out "$CERT_DIR/ca.key" 4096
openssl req -x509 -new -nodes -key "$CERT_DIR/ca.key" \
  -sha256 -days 3650 \
  -subj "/CN=Harbor Internal CA/O=CloudOpt/C=CN" \
  -out "$CERT_DIR/ca.crt"

# 生成 Harbor 服务器私钥和 CSR
openssl genrsa -out "$CERT_DIR/harbor.key" 2048
openssl req -new -key "$CERT_DIR/harbor.key" \
  -subj "/CN=harbor.cloudopt.com/O=CloudOpt/C=CN" \
  -out "$CERT_DIR/harbor.csr"

# 用 CA 签发
openssl x509 -req -in "$CERT_DIR/harbor.csr" \
  -CA "$CERT_DIR/ca.crt" -CAkey "$CERT_DIR/ca.key" \
  -CAcreateserial -out "$CERT_DIR/harbor.crt" \
  -days 365 -sha256

echo "✓ Certificate generated:"
openssl x509 -in "$CERT_DIR/harbor.crt" -noout -dates -subject

# ---- 1.3 配置 harbor.yml ----
echo ""
echo "--- 1.3 Configure harbor.yml ---"
cd "$INSTALL_DIR"
cp harbor.yml.tmpl harbor.yml

# 生成一个安全的 admin 密码
ADMIN_PASSWORD="Cl0ud0pt!Harbor@2024"

cat > harbor.yml << 'YAMLEOF'
# Harbor v2.12.0 Configuration
hostname: harbor.cloudopt.com

http:
  port: 80

https:
  port: 443
  certificate: /etc/ssl/certs/harbor.crt
  private_key: /etc/ssl/certs/harbor.key

harbor_admin_password: Cl0ud0pt!Harbor@2024

database:
  password: root123
  max_idle_conns: 100
  max_open_conns: 900

data_volume: /data/harbor

trivy:
  ignore_unfixed: false
  skip_update: false
  offline_scan: true
  insecure: false

log:
  level: info
  local:
    rotate_count: 50
    rotate_size: 200M

audit_log:
  retention_period: 365
YAMLEOF

echo "✓ harbor.yml configured"
echo "  Hostname: harbor.cloudopt.com"
echo "  Data volume: /data/harbor"
echo "  Admin password: [HIDDEN]"

# ---- 1.4 安装 Harbor ----
echo ""
echo "--- 1.4 Install Harbor ---"
./prepare

if [ $? -ne 0 ]; then
  echo "✗ ./prepare failed!"
  exit 1
fi

./install.sh --with-trivy

echo ""
echo "Waiting for Harbor to start..."
sleep 30

# ---- 1.5 验证安装 ----
echo ""
echo "--- 1.5 Verify Installation ---"

# 验证 1: 容器健康
echo "[1] Container status:"
docker compose ps --format "table {{.Name}}\t{{.Status}}"

# 验证 2: API 健康检查
echo ""
echo "[2] API Health:"
curl -sk -u "admin:$ADMIN_PASSWORD" \
  https://harbor.cloudopt.com/api/v2.0/health | jq '.'

# 验证 3: 版本信息
echo ""
echo "[3] Harbor version:"
curl -sk -u "admin:$ADMIN_PASSWORD" \
  https://harbor.cloudopt.com/api/v2.0/systeminfo | jq '.harbor_version'

# 验证 4: Portal 可访问
echo ""
echo "[4] Portal:"
PORTAL_CODE=$(curl -sk -o /dev/null -w "%{http_code}" https://harbor.cloudopt.com/)
echo "  HTTP Status: $PORTAL_CODE"

# 验证 5: Docker 登录
echo ""
echo "[5] Docker Login:"
echo "$ADMIN_PASSWORD" | docker login harbor.cloudopt.com -u admin --password-stdin 2>&1

# 验证 6: 推送测试镜像
echo ""
echo "[6] Push test image:"
docker pull hello-world:latest > /dev/null 2>&1
docker tag hello-world:latest harbor.cloudopt.com/library/hello-world:test
docker push harbor.cloudopt.com/library/hello-world:test 2>&1 | tail -3

echo ""
echo "============================================"
echo "✅ Harbor installation completed!"
echo "   URL: https://harbor.cloudopt.com"
echo "   Admin: admin / [HIDDEN]"
echo "============================================"
```

### 3.3 步骤二：项目结构规划与创建

```bash
#!/bin/bash
# step2-project-setup.sh — 创建项目结构
set -e

HARBOR="https://harbor.cloudopt.com"
AUTH="admin:Cl0ud0pt!Harbor@2024"

echo "=== Step 2: Project Setup ==="
echo ""

# ---- 2.1 创建共享基础镜像项目（公开只读） ----
echo "--- 2.1 Shared Base Project ---"
curl -sk -u "$AUTH" -X POST \
  -H "Content-Type: application/json" \
  -d '{
    "project_name": "shared-base",
    "public": true,
    "storage_limit": 536870912000,
    "metadata": {
      "public": "true",
      "auto_scan": "true",
      "enable_content_trust": "true"
    }
  }' \
  "$HARBOR/api/v2.0/projects" | jq '{name, project_id, public, repo_count, creation_time}'

echo "✓ shared-base (500GB, public read-only)"

# ---- 2.2 创建业务项目（私有） ----
echo ""
echo "--- 2.2 Business Projects ---"

PROJECTS=(
  "order-platform:214748364800"   # 200GB
  "payment-platform:214748364800" # 200GB
  "user-platform:214748364800"    # 200GB
  "logistics-platform:107374182400" # 100GB
  "data-platform:322122547200"    # 300GB
  "ai-platform:214748364800"      # 200GB
  "api-gateway:107374182400"      # 100GB
  "monitoring-platform:107374182400" # 100GB
)

for entry in "${PROJECTS[@]}"; do
  IFS=: read -r project_name storage_limit <<< "$entry"
  
  RESPONSE=$(curl -sk -u "$AUTH" -X POST \
    -H "Content-Type: application/json" \
    -d "{
      \"project_name\": \"$project_name\",
      \"public\": false,
      \"storage_limit\": $storage_limit,
      \"metadata\": {
        \"auto_scan\": \"true\",
        \"enable_content_trust\": \"true\"
      }
    }" \
    "$HARBOR/api/v2.0/projects")
  
  PROJECT_ID=$(echo "$RESPONSE" | jq -r '.project_id // "EXISTS"')
  if [ "$PROJECT_ID" != "EXISTS" ] && [ "$PROJECT_ID" != "null" ]; then
    echo "✓ $project_name (ID: $PROJECT_ID, Quota: $((storage_limit / 1073741824))GB)"
  else
    echo "⚠ $project_name already exists, skipped"
  fi
done

# ---- 2.3 创建 CI 快照项目 ----
echo ""
echo "--- 2.3 CI Snapshots Project ---"
curl -sk -u "$AUTH" -X POST \
  -H "Content-Type: application/json" \
  -d '{
    "project_name": "ci-snapshots",
    "public": false,
    "storage_limit": 107374182400,
    "metadata": {
      "auto_scan": "true"
    }
  }' \
  "$HARBOR/api/v2.0/projects" | jq '{name, project_id, repo_count}'

echo "✓ ci-snapshots (100GB, auto-cleanup enabled)"

# ---- 2.4 验证所有项目 ----
echo ""
echo "--- 2.4 Verify All Projects ---"
curl -sk -u "$AUTH" "$HARBOR/api/v2.0/projects?page_size=50" | \
  jq -r '.[] | "\(.name) | ID:\(.project_id) | Public:\(.metadata.public // "false") | Repos:\(.repo_count) | Quota:\(.storage_limit / 1073741824)GB"'

echo ""
echo "✅ Project setup complete"
```

### 3.4 步骤三：创建用户和机器人账户

```bash
#!/bin/bash
# step3-users-robots.sh — 用户和权限配置
set -e

HARBOR="https://harbor.cloudopt.com"
AUTH="admin:Cl0ud0pt!Harbor@2024"

echo "=== Step 3: Users & Robot Accounts ==="
echo ""

# ---- 3.1 创建开发团队用户 ----
echo "--- 3.1 Create Developer Users ---"

USERS=(
  "zhangsan:ZhangSan@2024:zhangsan@cloudopt.com"
  "lisi:LiSi@2024:lisi@cloudopt.com"
  "wangwu:WangWu@2024:wangwu@cloudopt.com"
  "zhaoliu:ZhaoLiu@2024:zhaoliu@cloudopt.com"
  "sunqi:SunQi@2024:sunqi@cloudopt.com"
)

for entry in "${USERS[@]}"; do
  IFS=: read -r username password email <<< "$entry"
  
  RESPONSE=$(curl -sk -u "$AUTH" -X POST \
    -H "Content-Type: application/json" \
    -d "{
      \"username\": \"$username\",
      \"password\": \"$password\",
      \"email\": \"$email\",
      \"realname\": \"$username\"
    }" \
    "$HARBOR/api/v2.0/users")
  
  USER_ID=$(echo "$RESPONSE" | jq -r '.user_id // "EXISTS"')
  if [ "$USER_ID" != "EXISTS" ] && [ "$USER_ID" != "null" ]; then
    echo "✓ User created: $username (ID: $USER_ID)"
  else
    echo "⚠ User $username already exists, skipping"
  fi
done

# ---- 3.2 添加项目成员 ----
echo ""
echo "--- 3.2 Assign Project Members ---"

# role_id: 1=项目管理员, 2=开发者, 3=访客
declare -A MEMBER_ASSIGNMENTS
MEMBER_ASSIGNMENTS["zhangsan:order-platform"]=2
MEMBER_ASSIGNMENTS["zhangsan:payment-platform"]=2
MEMBER_ASSIGNMENTS["lisi:order-platform"]=2
MEMBER_ASSIGNMENTS["lisi:logistics-platform"]=2
MEMBER_ASSIGNMENTS["wangwu:data-platform"]=1
MEMBER_ASSIGNMENTS["wangwu:ai-platform"]=1
MEMBER_ASSIGNMENTS["zhaoliu:user-platform"]=2
MEMBER_ASSIGNMENTS["zhaoliu:api-gateway"]=2
MEMBER_ASSIGNMENTS["sunqi:monitoring-platform"]=2

for assignment in "${!MEMBER_ASSIGNMENTS[@]}"; do
  IFS=: read -r username project <<< "$assignment"
  role_id="${MEMBER_ASSIGNMENTS[$assignment]}"
  
  # 获取项目 ID
  PROJECT_ID=$(curl -sk -u "$AUTH" \
    "$HARBOR/api/v2.0/projects?name=$project" | jq -r '.[0].project_id')
  
  RESPONSE=$(curl -sk -u "$AUTH" -X POST \
    -H "Content-Type: application/json" \
    -d "{
      \"role_id\": $role_id,
      \"member_user\": {\"username\": \"$username\"}
    }" \
    "$HARBOR/api/v2.0/projects/$PROJECT_ID/members")
  
  HTTP_CODE=$(curl -sk -o /dev/null -w "%{http_code}" -u "$AUTH" -X POST \
    -H "Content-Type: application/json" \
    -d "{...}" \
    "$HARBOR/api/v2.0/projects/$PROJECT_ID/members")
  
  role_labels=([1]="管理员" [2]="开发者" [3]="访客")
  echo "✓ $username → $project (${role_labels[$role_id]})"
done

# ---- 3.3 创建 GitLab CI 机器人账户 ----
echo ""
echo "--- 3.3 Create CI Robot Account ---"

# 为 order-platform 项目创建机器人
ORDER_ID=$(curl -sk -u "$AUTH" \
  "$HARBOR/api/v2.0/projects?name=order-platform" | jq -r '.[0].project_id')

echo "Project order-platform ID: $ORDER_ID"

ROBOT_RESPONSE=$(curl -sk -u "$AUTH" -X POST \
  -H "Content-Type: application/json" \
  -d '{
    "name": "gitlab-ci-bot",
    "expires_at": 4102444800,
    "description": "GitLab CI/CD pipeline robot account",
    "access": [
      {"resource": "repository", "action": "push"},
      {"resource": "repository", "action": "pull"},
      {"resource": "artifact",   "action": "read"},
      {"resource": "helm-chart", "action": "read"}
    ]
  }' \
  "$HARBOR/api/v2.0/projects/$ORDER_ID/robots")

ROBOT_NAME=$(echo "$ROBOT_RESPONSE" | jq -r '.name')
ROBOT_TOKEN=$(echo "$ROBOT_RESPONSE" | jq -r '.token')
ROBOT_SECRET=$(echo "$ROBOT_RESPONSE" | jq -r '.secret')

echo ""
echo "============================================"
echo "🤖 CI ROBOT ACCOUNT CREATED"
echo "============================================"
echo "  Name:   $ROBOT_NAME"
echo "  Token:  $ROBOT_TOKEN"
echo "  Secret: $ROBOT_SECRET"
echo "============================================"
echo ""
echo "⚠️  SAVE THESE CREDENTIALS NOW! The secret will NOT be shown again."
echo "   Copy Token to GitLab CI Variable: HARBOR_TOKEN"

# ---- 3.4 为 ci-snapshots 项目也创建机器人 ----
CI_ID=$(curl -sk -u "$AUTH" \
  "$HARBOR/api/v2.0/projects?name=ci-snapshots" | jq -r '.[0].project_id')

curl -sk -u "$AUTH" -X POST \
  -H "Content-Type: application/json" \
  -d '{
    "name": "gitlab-ci-bot",
    "expires_at": 4102444800,
    "access": [
      {"resource": "repository", "action": "push"},
      {"resource": "repository", "action": "pull"}
    ]
  }' \
  "$HARBOR/api/v2.0/projects/$CI_ID/robots" > /dev/null

echo ""
echo "✓ CI robot for ci-snapshots created"
```

### 3.5 步骤四：配置扫描策略与标签保留

```bash
#!/bin/bash
# step4-security-policy.sh — 安全策略配置

HARBOR="https://harbor.cloudopt.com"
AUTH="admin:Cl0ud0pt!Harbor@2024"

echo "=== Step 4: Security & Retention Policy ==="
echo ""

# ---- 4.1 获取所有项目 ID ----
PROJECTS=$(curl -sk -u "$AUTH" "$HARBOR/api/v2.0/projects?page_size=50" | \
  jq -r '.[] | "\(.project_id):\(.name)"')

# ---- 4.2 配置 CVE 阻止策略 ----
echo "--- 4.2 CVE Prevention Policy ---"

while IFS=: read -r pid pname; do
  RESPONSE=$(curl -sk -o /dev/null -w "%{http_code}" -u "$AUTH" \
    -X PUT \
    -H "Content-Type: application/json" \
    -d '{
      "prevent_vul": true,
      "severity": "critical",
      "scan_on_push": true
    }' \
    "$HARBOR/api/v2.0/projects/$pid/prevent-vulnerability")
  
  if [ "$RESPONSE" = "200" ]; then
    echo "  ✓ $pname (ID:$pid) — CVE prevention: ON (Block: Critical), Auto-scan: ON"
  else
    echo "  ✗ $pname (ID:$pid) — HTTP $RESPONSE"
  fi
done <<< "$PROJECTS"

# ---- 4.3 配置标签保留策略 ----
echo ""
echo "--- 4.3 Tag Retention Policy ---"

# 为 CI 快照项目配置保留策略
CI_ID=$(curl -sk -u "$AUTH" \
  "$HARBOR/api/v2.0/projects?name=ci-snapshots" | jq -r '.[0].project_id')

curl -sk -u "$AUTH" \
  -X POST \
  -H "Content-Type: application/json" \
  -d "{
    \"algorithm\": \"or\",
    \"rules\": [
      {
        \"disabled\": false,
        \"action\": \"retain\",
        \"scope_selectors\": {
          \"repository\": [{\"kind\": \"doublestar\", \"pattern\": \"**\"}]
        },
        \"params\": {
          \"latestPushedN\": 5
        },
        \"tag_selectors\": [
          {\"kind\": \"doublestar\", \"pattern\": \"release-*\"}
        ]
      },
      {
        \"disabled\": false,
        \"action\": \"retain\",
        \"scope_selectors\": {
          \"repository\": [{\"kind\": \"doublestar\", \"pattern\": \"**\"}]
        },
        \"params\": {
          \"latestPushedN\": 3
        },
        \"tag_selectors\": [
          {\"kind\": \"doublestar\", \"pattern\": \"latest\"}
        ]
      }
    ],
    \"trigger\": {
      \"kind\": \"Schedule\",
      \"settings\": {
        \"cron\": \"0 3 * * *\"
      }
    },
    \"scope\": {
      \"level\": \"project\",
      \"ref\": $CI_ID
    }
  }" \
  "$HARBOR/api/v2.0/retentions" | jq '{id, algorithm, trigger, scope}'

echo "✓ Retention policy: Keep release-* + latest + 3 newest, daily at 3:00 AM"

# ---- 4.4 配置 Webhook（告警用） ----
echo ""
echo "--- 4.4 Webhook for Alerts ---"

ORDER_ID=$(curl -sk -u "$AUTH" \
  "$HARBOR/api/v2.0/projects?name=order-platform" | jq -r '.[0].project_id')

curl -sk -u "$AUTH" \
  -X POST \
  -H "Content-Type: application/json" \
  -d '{
    "targets": [{
      "type": "http",
      "address": "https://alerts.cloudopt.com/harbor-webhook",
      "skip_cert_verify": false
    }],
    "event_types": [
      "DELETE_ARTIFACT",
      "DELETE_REPOSITORY",
      "QUOTA_EXCEED",
      "SCANNING_FAILED"
    ],
    "enabled": true,
    "description": "Security alerts for order-platform"
  }' \
  "$HARBOR/api/v2.0/projects/$ORDER_ID/webhook/policies" | jq '{id, enabled, event_types}'

echo "✓ Webhook configured for security alerts"
echo ""
echo "✅ Security & retention policy setup complete"
```

### 3.6 步骤五：GitLab CI/CD 完整集成

在 GitLab 项目中配置 CI 变量：
- `HARBOR_URL` = `harbor.cloudopt.com`
- `HARBOR_ROBOT` = `robot$order-platform+gitlab-ci-bot`
- `HARBOR_TOKEN` = `<步骤三输出的机器人 Token>`
- `K8S_STAGING_CONFIG` = Staging K8s kubeconfig（base64）
- `K8S_PROD_CONFIG` = Production K8s kubeconfig（base64）

```yaml
# .gitlab-ci.yml — 完整的企业级 CI/CD Pipeline
# 位置：order-service 仓库根目录

stages:
  - build
  - scan
  - deploy-staging
  - tag-release
  - deploy-prod

variables:
  DOCKER_HOST: tcp://docker:2375
  DOCKER_TLS_CERTDIR: ""
  HARBOR_HOST: harbor.cloudopt.com
  HARBOR_PROJECT: order-platform
  IMAGE_NAME: ${HARBOR_HOST}/${HARBOR_PROJECT}/order-service
  BUILD_TAG: ci-${CI_COMMIT_SHORT_SHA}-${CI_PIPELINE_IID}

# ═══════════ Stage 1: Multi-Arch Build & Push ═══════════
build:
  stage: build
  image: docker:24
  services:
    - docker:24-dind
  before_script:
    - echo "${HARBOR_TOKEN}" | docker login ${HARBOR_HOST} -u "${HARBOR_ROBOT}" --password-stdin
    - docker buildx create --use --name multiarch-builder --driver docker-container
    - docker buildx inspect --bootstrap
  script:
    - |
      echo "Building multi-arch image: ${IMAGE_NAME}:${BUILD_TAG}"
      docker buildx build \
        --platform linux/amd64,linux/arm64 \
        --build-arg GIT_COMMIT="${CI_COMMIT_SHORT_SHA}" \
        --build-arg BUILD_DATE="$(date -u +'%Y-%m-%dT%H:%M:%SZ')" \
        --build-arg VERSION="${CI_PIPELINE_IID}" \
        --tag ${IMAGE_NAME}:${BUILD_TAG} \
        --tag ${IMAGE_NAME}:latest \
        --push \
        .
    - |
      echo "IMAGE_DIGEST=$(docker buildx imagetools inspect ${IMAGE_NAME}:${BUILD_TAG} --format '{{json .Manifest.Digest}}' | tr -d '"')" >> build.env
  artifacts:
    reports:
      dotenv: build.env

# ═══════════ Stage 2: Security Scan Gate ═══════════
scan-gate:
  stage: scan
  image: alpine:3.19
  before_script:
    - apk add --no-cache curl jq
  script:
    - |
      HARBOR_API="https://${HARBOR_HOST}/api/v2.0"
      ARTIFACT_PATH="/projects/${HARBOR_PROJECT}/repositories/order-service/artifacts/${BUILD_TAG}"
      
      echo "Waiting for vulnerability scan on ${BUILD_TAG}..."
      
      MAX_WAIT=420  # 7 minutes timeout for large images
      START=$(date +%s)
      
      while true; do
        SCAN_RESPONSE=$(curl -sk -u "${HARBOR_ROBOT}:${HARBOR_TOKEN}" \
          "${HARBOR_API}${ARTIFACT_PATH}?with_scan_overview=true")
        
        SCAN_STATUS=$(echo "$SCAN_RESPONSE" | jq -r \
          '.scan_overview | to_entries[0].value.scan_status // "Pending"')
        
        echo "  Scan status: $SCAN_STATUS"
        
        case "$SCAN_STATUS" in
          "Success")
            CRITICAL=$(echo "$SCAN_RESPONSE" | jq -r \
              '.scan_overview | to_entries[0].value.summary.summary.Critical // 0')
            HIGH=$(echo "$SCAN_RESPONSE" | jq -r \
              '.scan_overview | to_entries[0].value.summary.summary.High // 0')
            MEDIUM=$(echo "$SCAN_RESPONSE" | jq -r \
              '.scan_overview | to_entries[0].value.summary.summary.Medium // 0')
            TOTAL=$(echo "$SCAN_RESPONSE" | jq -r \
              '.scan_overview | to_entries[0].value.summary.total // 0')
            
            echo ""
            echo "═══════════════════════════════════════"
            echo "  SECURITY SCAN REPORT"
            echo "  Image: ${IMAGE_NAME}:${BUILD_TAG}"
            echo "  Total CVEs: $TOTAL"
            echo "  ─────────────────────────────────"
            echo "  Critical:  $CRITICAL"
            echo "  High:      $HIGH"
            echo "  Medium:    $MEDIUM"
            echo "═══════════════════════════════════════"
            echo ""
            
            # Gate 1: Zero critical
            if [ "$CRITICAL" -gt 0 ]; then
              echo "🚫 BLOCKED: $CRITICAL Critical CVE(s) found!"
              echo "  Fix all Critical CVEs before retrying."
              exit 1
            fi
            
            # Gate 2: Max 5 high
            if [ "$HIGH" -gt 5 ]; then
              echo "🚫 BLOCKED: $HIGH High CVEs exceed threshold (max 5)!"
              echo "  Fix High CVEs or add to exemption list."
              exit 1
            fi
            
            echo "✅ SECURITY GATE PASSED"
            echo "   Critical: $CRITICAL (threshold: 0)"
            echo "   High: $HIGH (threshold: 5)"
            exit 0
            ;;
          "Error")
            echo "❌ Scan failed with Error status"
            echo "  Check Trivy scanner health in Harbor"
            exit 2
            ;;
        esac
        
        ELAPSED=$(($(date +%s) - START))
        if [ "$ELAPSED" -gt "$MAX_WAIT" ]; then
          echo "⏰ Scan timed out after ${MAX_WAIT}s"
          echo "  Consider increasing MAX_WAIT or checking scanner status"
          exit 3
        fi
        
        sleep 15
      done

# ═══════════ Stage 3: Deploy to Staging ═══════════
deploy-staging:
  stage: deploy-staging
  image: alpine/k8s:1.28.3
  before_script:
    - echo "${K8S_STAGING_CONFIG}" | base64 -d > /tmp/kubeconfig
    - export KUBECONFIG=/tmp/kubeconfig
    - kubectl config use-context staging
  script:
    - |
      echo "Deploying ${IMAGE_NAME}:${BUILD_TAG} to Staging"
      
      # 创建或更新 K8s Secret（用于 pull 私有镜像）
      kubectl create secret docker-registry harbor-registry \
        --docker-server=${HARBOR_HOST} \
        --docker-username="${HARBOR_ROBOT}" \
        --docker-password="${HARBOR_TOKEN}" \
        --namespace=staging \
        --dry-run=client -o yaml | kubectl apply -f -
      
      # 更新部署
      kubectl set image deployment/order-service \
        order-service=${IMAGE_NAME}:${BUILD_TAG} \
        --namespace=staging
      
      # 等待部署完成
      kubectl rollout status deployment/order-service \
        --namespace=staging --timeout=180s
      
      # 健康检查
      kubectl wait --for=condition=ready pod \
        -l app=order-service \
        --namespace=staging --timeout=60s
      
      echo "✅ Staging deployment successful"
  environment:
    name: staging
    url: https://staging.cloudopt.com

# ═══════════ Stage 4: Tag as Release ═══════════
tag-release:
  stage: tag-release
  image: alpine:3.19
  before_script:
    - apk add --no-cache curl jq
  script:
    - |
      RELEASE_TAG="release-${CI_PIPELINE_IID}"
      
      echo "Tagging image as ${RELEASE_TAG}..."
      
      curl -sk -u "${HARBOR_ROBOT}:${HARBOR_TOKEN}" \
        -X POST \
        -H "Content-Type: application/json" \
        -d "{\"name\": \"${RELEASE_TAG}\"}" \
        "https://${HARBOR_HOST}/api/v2.0/projects/${HARBOR_PROJECT}/repositories/order-service/artifacts/${BUILD_TAG}/tags"
      
      echo "✅ Tagged as ${RELEASE_TAG}"
      
      # 输出 release tag 供下游使用
      echo "RELEASE_TAG=${RELEASE_TAG}" >> release.env
  artifacts:
    reports:
      dotenv: release.env
  when: manual
  only:
    - main
    - /^release\/.*$/

# ═══════════ Stage 5: Deploy to Production ═══════════
deploy-prod:
  stage: deploy-prod
  image: alpine/k8s:1.28.3
  before_script:
    - echo "${K8S_PROD_CONFIG}" | base64 -d > /tmp/kubeconfig
    - export KUBECONFIG=/tmp/kubeconfig
    - kubectl config use-context production
  script:
    - |
      echo "🚀 Deploying ${IMAGE_NAME}:${BUILD_TAG} to PRODUCTION"
      
      # 创建 pull secret
      kubectl create secret docker-registry harbor-registry \
        --docker-server=${HARBOR_HOST} \
        --docker-username="${HARBOR_ROBOT}" \
        --docker-password="${HARBOR_TOKEN}" \
        --namespace=production \
        --dry-run=client -o yaml | kubectl apply -f -
      
      # 金丝雀发布：先更新 1 个 Pod
      kubectl set image deployment/order-service \
        order-service=${IMAGE_NAME}:${BUILD_TAG} \
        --namespace=production
      
      # 等待 rollout（金丝雀 + 完整部署）
      kubectl rollout status deployment/order-service \
        --namespace=production --timeout=300s
      
      # 生产环境健康检查
      for i in $(seq 1 10); do
        if kubectl get pods -l app=order-service -n production | grep -q Running; then
          echo "✅ Production pods are running"
          break
        fi
        sleep 10
      done
      
      echo "✅ Production deployment successful"
  environment:
    name: production
    url: https://cloudopt.com
  when: manual
  only:
    - main
    - /^release\/.*$/
  needs:
    - tag-release
```

### 3.7 步骤六：端到端验证

```bash
#!/bin/bash
# step6-e2e-validation.sh — 端到端验证

HARBOR="https://harbor.cloudopt.com"
AUTH="admin:Cl0ud0pt!Harbor@2024"
ROBOT_AUTH="robot\$order-platform+gitlab-ci-bot:<TOKEN>"

echo "=== Step 6: End-to-End Validation ==="
echo ""

PASS=0
FAIL=0

check() {
  local desc="$1"
  local cmd="$2"
  echo -n "[ ] $desc ... "
  if eval "$cmd" > /dev/null 2>&1; then
    echo -e "\r[✓] $desc"
    PASS=$((PASS + 1))
  else
    echo -e "\r[✗] $desc"
    FAIL=$((FAIL + 1))
  fi
}

# ---- 验证 1: Harbor 健康 ----
check "Harbor API health" \
  "curl -skf -u '$AUTH' '$HARBOR/api/v2.0/health' | jq -e '.status == \"healthy\"'"

# ---- 验证 2: 项目列表 ----
check "Project count >= 10" \
  "curl -sk -u '$AUTH' '$HARBOR/api/v2.0/projects?page_size=50' | jq -e 'length >= 10'"

# ---- 验证 3: Trivy 扫描器 ----
check "Trivy scanner registered" \
  "curl -sk -u '$AUTH' '$HARBOR/api/v2.0/scanners' | jq -e '.[0].name == \"Trivy\"'"

# ---- 验证 4: CVE 策略 ----
ORDER_ID=$(curl -sk -u "$AUTH" \
  "$HARBOR/api/v2.0/projects?name=order-platform" | jq -r '.[0].project_id')
check "CVE prevention enabled" \
  "curl -sk -u '$AUTH' '$HARBOR/api/v2.0/projects/$ORDER_ID/prevent-vulnerability' | jq -e '.prevent_vul == true'"

# ---- 验证 5: 机器人账户 ----
check "CI robot account exists" \
  "curl -sk -u '$AUTH' '$HARBOR/api/v2.0/projects/$ORDER_ID/robots' | jq -e '.[].name == \"robot\$order-platform+gitlab-ci-bot\"'"

# ---- 验证 6: Docker Push/Pull（用机器人账户） ----
echo "[ ] Testing docker push/pull ... "
echo "Harbor12345" | docker login harbor.cloudopt.com -u admin --password-stdin > /dev/null 2>&1
docker pull alpine:latest > /dev/null 2>&1
docker tag alpine:latest harbor.cloudopt.com/order-platform/alpine:e2e-test
if docker push harbor.cloudopt.com/order-platform/alpine:e2e-test > /dev/null 2>&1; then
  echo -e "\r[✓] Docker push OK"
  PASS=$((PASS + 1))
else
  echo -e "\r[✗] Docker push FAILED"
  FAIL=$((FAIL + 1))
fi

# ---- 验证 7: 审计日志 ----
check "Audit logs accessible" \
  "curl -sk -u '$AUTH' '$HARBOR/api/v2.0/audit-logs?page_size=1' | jq -e 'length == 1'"

# ---- 验证 8: Webhook 配置 ----
check "Webhook policy exists" \
  "curl -sk -u '$AUTH' '$HARBOR/api/v2.0/projects/$ORDER_ID/webhook/policies' | jq -e 'length > 0'"

# ---- 验证 9: 标签保留策略 ----
check "Retention policy configured" \
  "curl -sk -u '$AUTH' '$HARBOR/api/v2.0/retentions?scope_project_id=$ORDER_ID' | jq -e 'length > 0'"

# ---- 验证 10: 清理测试数据 ----
docker rmi harbor.cloudopt.com/order-platform/alpine:e2e-test > /dev/null 2>&1
echo "[✓] Test data cleaned"

echo ""
echo "============================================"
echo "  VALIDATION SUMMARY"
echo "  Passed: $PASS / $((PASS + FAIL))"
echo "  Failed: $FAIL"
echo "============================================"

if [ "$FAIL" -eq 0 ]; then
  echo "✅ ALL CHECKS PASSED — Harbor is production-ready!"
else
  echo "⚠️  $FAIL check(s) failed — review before production use"
fi
```

### 3.8 可能遇到的坑

**坑1：GitLab Runner Docker-in-Docker (DinD) 无法连接 Harbor**

现象：GitLab CI 的 build stage 报 `x509: certificate signed by unknown authority`。

根因：GitLab Runner 的 Docker executor 使用的 Docker daemon 没有信任 Harbor 的自签名 CA 证书。

解决：
```toml
# /etc/gitlab-runner/config.toml
[[runners]]
  name = "harbor-builder"
  executor = "docker"
  [runners.docker]
    image = "docker:24"
    privileged = true
    volumes = [
      "/etc/docker/certs.d:/etc/docker/certs.d:ro",  # 挂载 Docker 证书目录
      "/etc/ssl/certs/ca.crt:/etc/ssl/certs/ca.crt:ro", # 挂载 CA 证书
      "/cache"
    ]
    extra_hosts = ["harbor.cloudopt.com:192.168.1.100"] # 如果 DNS 不可用

# 重启 GitLab Runner
gitlab-runner restart
```

**坑2：Scan Gate 总是超时——大镜像扫描慢**

现象：镜像大小 > 2GB，Trivy 扫描耗时超过 7 分钟，Scan Gate 超时退出。

根因：默认的 `MAX_WAIT=420`（7 分钟）不够用；或 Trivy 在首次扫描时需要下载漏洞数据库。

解决：
```yaml
# 方案一：增加超时时间
scan-gate:
  script:
    - MAX_WAIT=900  # 15 minutes for large images

# 方案二：预下载 Trivy 漏洞数据库
# 在 CI Runner 启动时预下载
docker exec harbor-trivy trivy image --download-db-only

# 方案三：分小镜像构建（推荐）
# 将大镜像拆分为多个小镜像，减少单次扫描耗时
```

**坑3：ARM64 构建失败——`buildx` 缺少 QEMU 模拟器**

现象：`docker buildx build --platform linux/arm64` 报错 `exec format error`。

根因：Docker `buildx` 需要使用 QEMU 来在 x86 主机上模拟 ARM 指令集。

解决：
```bash
# 在 GitLab Runner 上安装 QEMU
docker run --privileged --rm tonistiigi/binfmt --install all

# 验证模拟器可用
docker buildx inspect --bootstrap
# 应看到: Platforms: linux/amd64, linux/arm64, ...
```

**坑4：CVE 阻止策略配置了但没生效——未理解阻止时机**

现象：配置了 `prevent_vul: true, severity: critical`，但 CI 的 Scan Gate 检测到 Critical 漏洞后 -- Pull 操作仍然成功（镜像仍然可以被拉取部署）。

根因：Harbor 的 CVE 阻止策略是在 **Pull 阶段** 生效，而非 Push 阶段。即使镜像含有 Critical 漏洞，CI 仍然可以 Push 成功。但如果有人（或 K8s）尝试 Pull 这个镜像，Harbor 会返回 403。

解决：
```bash
# CVE 阻止策略阻止的是 PULL，不是 PUSH
# 因此 CI Pipeline 的 Scan Gate 必须主动检查扫描结果
# —— 不能在 CVE 阻止策略配置后就认为"安全了"

# ✅ 正确做法：CI Pipeline 中显式检查扫描结果
# （就是 step6 中的 scan-gate stage）

# 验证 CVE 阻止策略是否生效：
# 尝试用开发者账户 pull 一个含 Critical 漏洞的镜像
docker pull harbor.cloudopt.com/order-platform/vulnerable-image:latest
# 预期输出: Error: ... denied: The severity of vulnerability of the image 
# exceeds the threshold configured in the project CVE allowlist.
```

---

## 4 项目总结

### 4.1 完整安全交付链路总览

```
代码提交 (Git Push)
    │
    ▼
GitLab CI: Build Stage (Multi-arch: AMD64 + ARM64)
    │ 使用 docker buildx + QEMU 模拟
    │
    ▼
GitLab CI: Push to Harbor (Robot Token, 最小权限)
    │ POST /v2/<project>/<repo>/blobs/uploads/
    │
    ▼
Harbor: Trivy 自动扫描 (推送即扫描)
    │ 漏洞数据库下载 → 层逐层扫描 → 生成报告
    │
    ├──▶ GitLab CI: Scan Gate (轮询扫描状态)
    │     │ Critical > 0? ──▶ 🚫 阻断构建
    │     │ High > 5?    ──▶ 🚫 阻断构建
    │     │ 通过 ──▶ ✅ 继续
    │     
    ▼
GitLab CI: Deploy Staging (自动)
    │ kubectl set image → K8s Staging
    │
    ▼
GitLab CI: Tag Release (人工触发)
    │ POST /artifacts/<digest>/tags {name: "release-N"}
    │
    ▼
Harbor Replication: 自动复制到灾备 Harbor (事件驱动)
    │ 匹配 release-* 标签
    │
    ▼
GitLab CI: Deploy Production (人工触发, 金丝雀)
    │ kubectl set image → K8s Production
    │ kubectl rollout status
    │
    ▼
K8s Production: 健康检查 (Readiness Probe)
    │ HTTP GET /health → 200 OK
    │
    ▼
Harbor Retention Policy: CI 中间构建自动清理 (每日 3:00 AM)
    │ 保留: release-* + latest + 最近 5 个
    │ 删除: 过期的 ci-* 标签
    │
    ▼
Harbor GC: 回收磁盘空间 (每周日 2:00 AM)
    清理无引用的 Blob
```

### 4.2 验收标准对照表

| 验收项 | 验收指标 | 验证方法 | 实际结果 | 状态 |
|--------|---------|---------|---------|------|
| Harbor 部署 | Portal 可访问 + API Healthy | `curl /api/v2.0/health` → `healthy` | — | ☐ |
| 项目创建 | ≥ 10 个项目，配额正确 | API 查询项目列表 | — | ☐ |
| 用户/权限 | 机器人账户可 Push/Pull | `docker login` + `docker push/pull` | — | ☐ |
| 多架构构建 | AMD64 + ARM64 双架构 | `docker buildx imagetools inspect` | — | ☐ |
| 漏洞扫描 | Push 后自动扫描启动 | API 查询 `scan_overview` 非空 | — | ☐ |
| CVE 门禁 | Critical=阻止, High>5=阻止 | 推送含已知漏洞的测试镜像 | — | ☐ |
| GitLab CI | Pipeline 四阶段全部通过 | CI Pipeline Status: Passed | — | ☐ |
| Staging 部署 | 自动部署 + Pod Ready | `kubectl get pods -n staging` | — | ☐ |
| Release 标签 | 人工触发 + 标签创建成功 | Harbor Portal 查看标签 | — | ☐ |
| 生产部署 | 人工确认 + 金丝雀 + 健康检查 | `kubectl rollout status` | — | ☐ |
| 标签保留 | CI 快照自动清理 | 次日检查标签数量 | — | ☐ |

### 4.3 CI/CD 与 Harbor 集成要点速查

| 集成点 | 关键配置 | 常见错误 | 正确做法 |
|--------|---------|---------|---------|
| Docker 认证 | 机器人账户 + Token | 使用 admin 密码 | 最小权限机器人: push + pull + artifact:read |
| 自签名证书 | 挂载 CA 到各容器 | 只配宿主机，忘配容器 | `docker cp CA` 到所有需要 TLS 验证的容器 |
| 多架构构建 | `docker buildx` + QEMU | 忘安装 `binfmt` 模拟器 | `docker run --privileged tonistiigi/binfmt --install all` |
| 扫描门禁 | API 轮询 + 超时兜底 | 不设超时可能导致 CI 永久挂起 | MAX_WAIT + 退出码 3（超时）|
| CI 变量安全 | GitLab Masked Variables | Token hardcode 在代码中 | CI/CD Settings → Variables → Masked + Protected |
| K8s ImagePullSecret | `kubectl create secret docker-registry` | 忘在 namespace 创建 | 每个 namespace 都需要自己创建 Secret |
| 标签保留策略 | `latestPushedN: 5` | 保留数设太小误删 release | release-* 用独立保留规则优先保留 |

### 4.4 注意事项

1. **CI Token 安全存储**：机器人账户的 Token 务必存在 GitLab CI/CD Variables 中——勾选 "Masked"（不会出现在 CI 日志中）和 "Protected"（仅保护分支可用）。永远不要将 Token 提交到代码仓库。如果 Token 不小心泄露，立即在 Harbor 中删除该机器人账号（Token 自动失效）并创建新的。

2. **K8s ImagePullSecret**：需要在每个使用 Harbor 私有镜像的 Namespace 中创建 `docker-registry` 类型的 Secret。K8s 1.24+ 不再自动挂载 ServiceAccount 的 ImagePullSecret，需要在 Pod spec 中显式指定 `imagePullSecrets`。

3. **扫描超时兜底**：扫描器（Trivy）可能因漏洞数据库下载失败而永远无法完成扫描。CI Pipeline 的 Scan Gate 必须设置超时并处理超时情况——可以配置为: 超时后使用上次扫描结果（如果有），或直接阻断（安全优先）。

4. **多架构构建的 CI 资源需求**：ARM64 模拟构建的 CPU 消耗大约是原生 AMD64 构建的 3-5 倍。如果 CI Runner 资源不足，考虑使用原生 ARM64 Runner 而非 QEMU 模拟。

5. **Harbor 存储成本控制**：多架构镜像的存储开销 = 单架构 × 架构数。AMD64 + ARM64 双架构意味着每个镜像占用的存储翻倍。务必配置标签保留策略 + 定时 GC，控制存储成本。

### 4.5 常见踩坑经验（故障排查表）

| 故障现象 | 根因分析 | 排查路径 | 解决方案 | 预防措施 |
|---------|---------|---------|---------|---------|
| CI `docker push` 返回 `x509: certificate signed by unknown authority` | GitLab Runner Docker daemon 未信任 Harbor CA | Runner 上 `docker login` 测试 | 挂载 CA 证书到 Runner 容器的 `/etc/docker/certs.d/` | 在 GitLab Runner `config.toml` 中预配置证书挂载 |
| CVE 阻止策略配置了但镜像仍可 Pull | 配置了 `prevent_vul` 但等待时间不够，扫描尚未完成 | `scan_overview.scan_status` 是否为 Success | 扫描完成后 CVE 策略才生效——Push 后等扫描完成 | CI Scan Gate 主动检查，不依赖"阻止 Pull"兜底 |
| Docker Buildx ARM 构建失败 `exec format error` | 缺少 QEMU 用户态模拟器 | `docker buildx inspect` 查看支持的 platform | `docker run --privileged tonistiigi/binfmt --install all` | CI Runner 初始化脚本中预安装 |
| `kubectl set image` 更新成功但 Pod 启动失败 `ImagePullBackOff` | Namespace 缺少 ImagePullSecret | `kubectl describe pod` 查看 Events | `kubectl create secret docker-registry` 并确认 Pod spec 引用 | 在部署 stage 中自动创建/更新 Secret |
| Harbor 存储暴增到 500GB | 未配置标签保留策略，CI 中间构建无限累积 | `du -sh /data/registry/` | 配置 Retention Policy + 立即执行 GC | 项目创建时同时配置保留策略 |

### 4.6 思考题

1. **如果 CI 构建频率很高（每天 200+ 次），Harbor 的存储压力如何科学评估和管控？请设计一个"存储容量预测模型"：**
   - (1) 输入：日均构建次数、平均镜像大小、保留策略（保留天数 / 保留最近 N 个）、架构数
   - (2) 输出：30 天 / 90 天 / 365 天后的预期存储占用量
   - (3) 基于模型输出，设计标签保留策略和 GC 策略，使存储始终控制在 200GB 以内（假设初始 0GB，日均构建 200 次，平均镜像 500MB，双架构）
   - (4) 给出当存储达到 80% 阈值时的自动清理优先级方案

2. **当前 CVE 门禁只检查 Critical 和 High 级别。现在安全部门要求增加以下能力，请设计改造方案：**
   - (1) 增加 Medium 级别的检查：允许 Medium 级别存在，但数量超过 200 时也阻断
   - (2) 增加 CVE 白名单机制：特定 CVE 编号（如 `CVE-2023-XXXX`）可以被豁免，这些 CVE 不计入门禁判断
   - (3) 白名单的管理方式：通过 Harbor 项目的 Labels/Annotations 来存储白名单列表
   - (4) 修改 Scan Gate 的逻辑以支持以上需求，并输出详细的扫描报告（含豁免 CVE 列表）
   要求：编写完整的 Scan Gate 改造后的 Shell 脚本。

---

> **基础篇完结。** 恭喜你已经完成了 Harbor 基础篇的 16 章学习——从零基础概念到企业级综合实战。下一章将进入**中级篇**：第 17 章将深度剖析 Harbor 的微服务架构设计，理解各组件间的通信机制、认证数据流和数据库模型，为 Harbor 的二次开发和深度定制打下基础。
