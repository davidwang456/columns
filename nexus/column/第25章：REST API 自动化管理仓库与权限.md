# 第25章：REST API 自动化管理仓库与权限

## 1. 项目背景

云鲸科技的 DevOps 团队面临一个新的挑战——"Nexus 即代码"（Nexus as Code）。公司从 3 个团队扩展到 8 个团队，每个团队都需要一套独立的仓库、角色、用户、清理策略和维护任务。炮哥和他的团队每次新团队接入时，都在 Web UI 上手工点 20 分钟——创建仓库、配权限、建用户、关联策略、排任务——偶尔手滑漏掉某个步骤，还得事后补救。

更严重的是版本管理问题——预发布环境（staging）的 Nexus 配置和生产环境（prod）的配置不完全一致。"我在 staging 上测过没问题，为什么生产上不行？"这个问题最终追溯到一个差异：staging 上某个仓库的 `writePolicy` 是 `ALLOW`，生产上是 `ALLOW_ONCE`。这种配置漂移（configuration drift）正是手工管理的天然缺陷。

解决思路很明确——**用代码管理 Nexus，就像用 Terraform 管理云资源**。Nexus 的 REST API 已经覆盖了 90% 以上的管理操作，关键在于如何设计幂等的初始化脚本、如何处理 API 变更兼容性、如何建立从代码到环境的部署流水线。本章将编写一个完整的 `nexus-as-code.sh` 脚本，实现"新团队接入一键初始化"——从仓库矩阵到权限分配到清理策略到维护任务，全程 API 调用，具备幂等性和 dry-run 检查能力。

## 2. 项目设计

炮哥把新团队接入的 SOP 文档投影出来，整整 12 页。大师决定用代码取代它。

**炮哥**："大师，每次接新团队我都要创建至少 8 个仓库、3 个角色、5 个用户、2 条清理策略、4 个定时任务。有没有办法一秒钟搞定？"

**大师**："有，而且不止一秒钟——可以做成一个声明式的 YAML 配置文件。你定义'要什么'——仓库矩阵、角色列表、用户清单、策略集合、任务排期——然后脚本读取 YAML，调 API 把它变成现实。这里的关键是**幂等性**——脚本可以反复执行而不产生副作用。如果仓库已经存在，跳过创建；如果用户已经存在，先对比角色差异，更新而不是重建。"

**小胖**："这跟 Kubernetes 的 declarative API 很像——'我声明期望状态，系统负责达到它'？"

**大师**："完全正确。这也是 GitOps 的思想——YAML 文件存在 Git 仓库中，任何变更通过 PR 审批，审批合并后自动应用到 Nexus。配置漂移问题被彻底解决——Git 中的 YAML 就是唯一的真实来源（Single Source of Truth）。"

> **技术映射**：Nexus as Code = 声明式配置（YAML）+ 幂等 API 脚本 + GitOps 流程。声明期望状态，脚本负责调和（reconcile）到实际状态。

**小白**："Nexus API 在不同的版本之间兼容性好吗？如果升级了 Nexus 版本，脚本会不会全挂？"

**大师**："Nexus API 以 `/service/rest/v1/` 为前缀，v1 意味着 API 相对稳定但并非不可变。实践中遇到了三次 API 变化：一次是 `search` API 的 `version` 参数语义变更，一次是 `blobstores` API 的响应字段名调整，一次是 `cleanup-policies` 的 `criteriaLastBlobUpdated` 重命名。应对策略是：脚本中用函数封装 API 调用，升级后只需修改函数体内的 URL 或参数，不需要修改所有调用方。"

**炮哥**："那 dry-run 模式怎么做？我想先看看脚本会创建什么东西，确认无误再执行。"

**大师**："dry-run 的核心逻辑是——GET 检查资源是否存在，如果不存在则输出'将会创建 XXX'而不是实际 POST。实现时加一个全局变量 `DRY_RUN=true`，所有写操作（POST/PUT/DELETE）在 dry-run 模式下跳过 HTTP 调用，只打印日志。"

> **技术映射**：脚本三大工程特性——幂等性（重复执行无副作用）、dry-run（预览变更不执行）、error handling（单步失败不中断，收集所有错误后统一报告）。

**小胖**："那环境差异怎么办？staging 和 prod 的仓库矩阵一样但配额不同。"

**大师**："用环境变量覆盖。脚本读取 `${ENV}` 变量，不同环境加载不同的配置文件：`configs/staging.yaml`、`configs/prod.yaml`。相同部分放在 `configs/base.yaml` 作为默认值。就像 Helm 的 values.yaml 分层——base + overlay = 最终配置。"

## 3. 项目实战

### 3.1 环境准备

- 已部署 Nexus 实例
- curl、jq、yq（YAML 处理工具）
- 可选：`yq` 安装方式 `pip install yq` 或使用 Python 替代

### 3.2 分步实战

#### 步骤一：设计声明式配置文件

**目标**：创建一个 YAML 配置文件定义团队的全部 Nexus 资源。

```yaml
# configs/team-trade.yaml
# 交易团队 Nexus 资源配置

team:
  name: trade
  display: 交易中台

blobstores:
  - name: blob-maven-trade
    type: file
    path: maven-trade
    softQuota: 20480  # 20GB

  - name: blob-npm-trade
    type: file
    path: npm-trade
    softQuota: 10240  # 10GB

repositories:
  # Maven 仓库
  - name: maven-trade-snapshots
    format: maven2
    type: hosted
    blobStore: blob-maven-trade
    writePolicy: ALLOW
    maven:
      versionPolicy: SNAPSHOT
      layoutPolicy: STRICT

  - name: maven-trade-releases
    format: maven2
    type: hosted
    blobStore: blob-maven-trade
    writePolicy: ALLOW_ONCE
    maven:
      versionPolicy: RELEASE
      layoutPolicy: STRICT

  - name: maven-trade-public
    format: maven2
    type: group
    blobStore: blob-maven-trade
    members:
      - maven-trade-releases
      - maven-trade-snapshots
      - maven-central

  # npm 仓库
  - name: npm-trade-hosted
    format: npm
    type: hosted
    blobStore: blob-npm-trade
    writePolicy: ALLOW_ONCE

  - name: npm-trade-public
    format: npm
    type: group
    blobStore: blob-npm-trade
    members:
      - npm-trade-hosted
      - npm-proxy

roles:
  - id: role-trade-developer
    name: 交易团队开发者
    privileges:
      - nx-repository-view-maven2-maven-trade-snapshots-add
      - nx-repository-view-maven2-maven-trade-snapshots-read
      - nx-repository-view-maven2-maven-trade-releases-read
      - nx-repository-view-npm-npm-trade-hosted-add
      - nx-repository-view-npm-npm-trade-hosted-read

users:
  - userId: zhangsan
    firstName: San
    lastName: Zhang
    email: zhangsan@cloudwhale.com
    password: "ChangeMe123!"
    roles:
      - role-trade-developer

  - userId: ci-trade-bot
    firstName: CI
    lastName: TradeBot
    email: ci-trade@cloudwhale.com
    password: "B0tP@ssw0rd!"
    roles:
      - role-trade-developer

cleanup:
  - name: cleanup-trade-snapshots
    format: maven2
    criteriaLastBlobUpdated: 14
    criteriaReleaseType: SNAPSHOT
    policyNames: [cleanup-trade-snapshots]
    applyTo:
      - maven-trade-snapshots

tasks:
  - name: 交易团队每周清理
    typeId: repository.cleanup
    schedule: weekly
    properties:
      repositoryName: maven-trade-*
    alertEmail: trade-dev@cloudwhale.com
```

#### 步骤二：实现幂等初始化引擎

**目标**：编写读取 YAML 并幂等执行的核心脚本。

```bash
#!/bin/bash
# nexus-as-code.sh：Nexus 声明式配置引擎
set -e

NEXUS="${NEXUS_URL:-http://localhost:8081}"
AUTH="${NEXUS_AUTH:-admin:admin123}"
CONFIG_FILE="${1:-configs/team-trade.yaml}"
DRY_RUN="${DRY_RUN:-false}"

# 解析 YAML（使用 jq + yq，或直接用 Python 替代）
# 这里使用简化方法：将 YAML 转换为环境变量
# 实际生产建议用 Python + PyYAML 读取

_curl() {
    local method="$1"; local url="$2"; local data="${3:-}"
    if [ "$DRY_RUN" = "true" ] && [ "$method" != "GET" ]; then
        echo "  [DRY-RUN] $method $url"
        return 0
    fi
    if [ -n "$data" ]; then
        curl -s -u "$AUTH" -X "$method" "$url" -H "Content-Type: application/json" -d "$data"
    else
        curl -s -u "$AUTH" -X "$method" "$url"
    fi
}

resource_exists() {
    local type="$1" name="$2" endpoint="$3"
    local code
    code=$(curl -s -o /dev/null -w "%{http_code}" -u "$AUTH" "$NEXUS/$endpoint/$name" 2>/dev/null)
    [ "$code" = "200" ]
}

echo "=== Nexus as Code ==="
echo "配置文件: $CONFIG_FILE"
echo "DRY_RUN: $DRY_RUN"
echo "Nexus: $NEXUS"
echo ""

# === 1. BlobStore ===
echo "--- 同步 BlobStore ---"
# (简化示例：实际应解析 YAML)
BLOBS=("blob-maven-trade" "blob-npm-trade")
for BS in "${BLOBS[@]}"; do
    if resource_exists "blobstore" "$BS" "service/rest/v1/blobstores/file"; then
        echo "  ✅ BlobStore $BS 已存在"
    else
        echo "  → 创建 BlobStore $BS..."
        _curl POST "$NEXUS/service/rest/v1/blobstores/file" \
          "{\"name\":\"$BS\",\"path\":\"$(echo $BS | sed 's/blob-//')\"}"
    fi
done

# === 2. Repositories ===
echo "--- 同步 Repositories ---"
declare -A REPOS=(
    ["maven-trade-snapshots"]="maven/hosted"
    ["maven-trade-releases"]="maven/hosted"
    ["npm-trade-hosted"]="npm/hosted"
)

for REPO in "${!REPOS[@]}"; do
    TYPE="${REPOS[$REPO]}"
    if resource_exists "repository" "$REPO" "service/rest/v1/repositories"; then
        echo "  ✅ $REPO 已存在"
    else
        echo "  → 创建 $REPO (类型: $TYPE)..."
        # 简化：实际应根据 YAML 中的 format/writePolicy 等构建完整 body
        _curl POST "$NEXUS/service/rest/v1/repositories/$TYPE" \
          "{\"name\":\"$REPO\",\"online\":true,\"storage\":{\"blobStoreName\":\"default\",\"writePolicy\":\"ALLOW\"}}"
    fi
done

# === 3. Roles ===
echo "--- 同步 Roles ---"
ROLES=("role-trade-developer")
for ROLE in "${ROLES[@]}"; do
    if resource_exists "role" "$ROLE" "service/rest/v1/security/roles"; then
        echo "  ✅ $ROLE 已存在"
    else
        echo "  → 创建 $ROLE..."
        _curl POST "$NEXUS/service/rest/v1/security/roles" \
          "{\"id\":\"$ROLE\",\"name\":\"$ROLE\",\"privileges\":[\"nx-repository-view-maven2-*-read\"],\"roles\":[]}"
    fi
done

# === 4. Users ===
echo "--- 同步 Users ---"
USERS=("zhangsan" "ci-trade-bot")
for USER in "${USERS[@]}"; do
    if resource_exists "user" "$USER" "service/rest/v1/security/users?userId"; then
        echo "  ✅ $USER 已存在"
    else
        echo "  → 创建 $USER..."
        _curl POST "$NEXUS/service/rest/v1/security/users" \
          "{\"userId\":\"$USER\",\"firstName\":\"User\",\"lastName\":\"$USER\",\"email\":\"$USER@test.com\",\"password\":\"Pass123!\",\"status\":\"active\",\"roles\":[\"role-trade-developer\"]}"
    fi
done

# === 5. Cleanup Policies ===
echo "--- 同步 Cleanup Policies ---"
POLICIES=("cleanup-trade-snapshots")
for POLICY in "${POLICIES[@]}"; do
    # 检查策略是否存在
    EXISTS=$(curl -s -u "$AUTH" "$NEXUS/service/rest/v1/cleanup-policies" | jq -r ".[] | select(.name==\"$POLICY\") | .name")
    if [ -n "$EXISTS" ]; then
        echo "  ✅ $POLICY 已存在"
    else
        echo "  → 创建 $POLICY..."
        _curl POST "$NEXUS/service/rest/v1/cleanup-policies" \
          "{\"name\":\"$POLICY\",\"format\":\"maven2\",\"criteriaLastBlobUpdated\":14,\"criteriaReleaseType\":\"SNAPSHOT\"}"
    fi
done

# === 6. Tasks ===
echo "--- 同步 Tasks ---"
TASK_EXISTS=$(curl -s -u "$AUTH" "$NEXUS/service/rest/v1/tasks" | jq -r '.items[] | select(.name=="交易团队每周清理") | .name')
if [ -n "$TASK_EXISTS" ]; then
    echo "  ✅ 维护任务已存在"
else
    echo "  → 创建维护任务..."
    _curl POST "$NEXUS/service/rest/v1/tasks" \
      "{\"action\":\"repository.cleanup\",\"name\":\"交易团队每周清理\",\"typeId\":\"repository.cleanup\",\"schedule\":\"weekly\",\"properties\":{\"repositoryName\":\"maven-trade-*\"}}"
fi

echo ""
echo "=== 同步完成 ==="
if [ "$DRY_RUN" = "true" ]; then
    echo "⚠️  DRY-RUN 模式：未执行任何实际变更"
fi
```

```bash
chmod +x nexus-as-code.sh

# Dry-run 预览
DRY_RUN=true ./nexus-as-code.sh configs/team-trade.yaml

# 实际执行
./nexus-as-code.sh configs/team-trade.yaml
```

#### 步骤三：配置漂移检测脚本

**目标**：定期检查实际 Nexus 配置是否与 YAML 定义一致。

```bash
#!/bin/bash
# drift-detect.sh：配置漂移检测
NEXUS="http://localhost:8081"
AUTH="admin:admin123"

echo "=== Nexus 配置漂移检测 ==="

DRIFT_FOUND=0

# 检查仓库是否存在且在线
EXPECTED_REPOS=("maven-trade-snapshots" "maven-trade-releases" "npm-trade-hosted")
for REPO in "${EXPECTED_REPOS[@]}"; do
    STATUS=$(curl -s -u $AUTH "$NEXUS/service/rest/v1/repositories/$REPO" | jq -r '.online // "NOT_FOUND"')
    if [ "$STATUS" != "true" ]; then
        echo "🚨 $REPO: 预期在线，实际状态=$STATUS"
        DRIFT_FOUND=1
    fi
done

# 检查用户是否存在
EXPECTED_USERS=("zhangsan" "ci-trade-bot")
for USER in "${EXPECTED_USERS[@]}"; do
    CODE=$(curl -s -o /dev/null -w "%{http_code}" -u $AUTH \
      "$NEXUS/service/rest/v1/security/users?userId=$USER")
    if [ "$CODE" != "200" ]; then
        echo "🚨 用户 $USER: 预期存在，实际 HTTP $CODE"
        DRIFT_FOUND=1
    fi
done

# 检查清理策略是否存在并关联到仓库
POLICY_COUNT=$(curl -s -u $AUTH "$NEXUS/service/rest/v1/cleanup-policies" | \
  jq '[.[] | select(.name == "cleanup-trade-snapshots")] | length')
if [ "$POLICY_COUNT" = "0" ]; then
    echo "🚨 cleanup-trade-snapshots: 策略丢失"
    DRIFT_FOUND=1
fi

if [ "$DRIFT_FOUND" = "1" ]; then
    echo ""
    echo "⚠️  检测到配置漂移！建议重新执行 nexus-as-code.sh 进行修复"
    exit 1
else
    echo "✅ 所有配置与预期一致，无漂移"
fi
```

```bash
chmod +x drift-detect.sh
# 加入 cron 每周自动检测
# 0 8 * * 1 /opt/scripts/drift-detect.sh || /opt/scripts/nexus-as-code.sh
```

#### 步骤四：多环境适配

**目标**：通过环境变量实现 staging/prod 的差异化配置。

```bash
#!/bin/bash
# deploy-env.sh：按环境部署 Nexus 配置
ENV="${1:-staging}"

case "$ENV" in
    staging)
        export NEXUS_URL="http://nexus-staging.internal:8081"
        export NEXUS_AUTH="${STAGING_ADMIN_USER}:${STAGING_ADMIN_PASS}"
        export BLOB_QUOTA_MULTIPLIER=0.5  # staging 配额减半
        ;;
    prod)
        export NEXUS_URL="http://nexus-prod.internal:8081"
        export NEXUS_AUTH="${PROD_ADMIN_USER}:${PROD_ADMIN_PASS}"
        export BLOB_QUOTA_MULTIPLIER=1.0  # prod 正常配额
        ;;
    *)
        echo "未知环境: $ENV (可用: staging, prod)"
        exit 1
        ;;
esac

echo "=== 部署到 $ENV 环境 ==="
echo "Nexus: $NEXUS_URL"
echo "配额系数: $BLOB_QUOTA_MULTIPLIER"

# Dry-run 先预览
DRY_RUN=true ./nexus-as-code.sh "configs/team-trade-${ENV}.yaml"

# 确认后执行
read -p "确认部署？(yes/no) " CONFIRM
if [ "$CONFIRM" = "yes" ]; then
    DRY_RUN=false ./nexus-as-code.sh "configs/team-trade-${ENV}.yaml"
    echo "部署完成"
else
    echo "已取消"
fi
```

### 3.3 常见坑点

| 坑点 | 现象 | 解决方法 |
|------|------|----------|
| API 创建幂等但返回不同状态码 | 第一次返回 201，重复执行返回 400（已存在） | 脚本中先 GET 检查存在性，已存在跳过 |
| 仓库创建后不可更改 BlobStore | 想修改仓库绑定的 BlobStore | 仓库创建后 `blobStoreName` 不可变，需要删除重建 |
| YAML 中 format 名称与 API 不一致 | `format: maven` 实际 API 需要 `maven2` | 严格按 Nexus API 文档中的 format 名称填写 |
| 并发执行导致冲突 | 两个脚本同时创建同一仓库 | 使用资源锁（如文件锁 `flock`）或确保脚本单实例运行 |

## 4. 项目总结

### 4.1 Nexus as Code 能力矩阵

| 资源类型 | API 端点前缀 | 幂等检查 | 更新支持 | 删除支持 |
|---------|-------------|---------|---------|---------|
| BlobStore | `/service/rest/v1/blobstores/` | GET 检查 | ❌ | ✅ |
| Repository | `/service/rest/v1/repositories/` | GET 检查 | ✅（PUT） | ✅ |
| Role | `/service/rest/v1/security/roles/` | GET 检查 | ✅（PUT） | ✅ |
| User | `/service/rest/v1/security/users/` | GET 检查 | ✅（PUT） | ✅ |
| Cleanup Policy | `/service/rest/v1/cleanup-policies/` | 遍历搜索 | ✅（PUT） | ✅ |
| Task | `/service/rest/v1/tasks/` | 遍历搜索 | ❌ | ✅ |

### 4.2 适用场景

1. **新团队批量接入**：一键创建完整的环境资源
2. **多环境一致性保证**：staging/prod 配置从同一 YAML 生成
3. **灾难恢复**：Nexus 重建后快速恢复到已知配置状态
4. **配置变更审计**：所有配置变更通过 Git PR 审批
5. **临时环境快速搭建**：为演示/测试创建短期 Nexus 环境

**不适用场景**：
1. 高度定制化的一次性操作——脚本化的收益低于直接 API 调用
2. 配置极少的团队（< 3 个仓库）——手动管理比脚本更快

### 4.3 注意事项

- **先 GET 后 POST**：幂等性靠检查已存在性保证
- **YAML 是唯一真实来源**：不要同时手工改 Web UI 和脚本配置
- **API 令牌安全**：`NEXUS_AUTH` 使用环境变量而非硬编码
- **配置变更需要审批**：Git PR → Review → Merge → 自动部署

### 4.4 常见踩坑经验

**故障一：API 返回 415 Unsupported Media Type**

创建仓库时忘记加 `Content-Type: application/json` 头。根因：curl 默认没有 Content-Type，Nexus 无法解析请求体。解决：所有 POST/PUT 请求加 `-H "Content-Type: application/json"`。

**故障二：Repository 名称已被占用**

同名仓库已存在（可能之前手工创建过），脚本因 400 报错退出。根因：脚本没有做"已存在则跳过"的检查。解决：先 GET 检查资源存在性，已存在则记录日志并跳过创建。

**故障三：User 更新后丢失了原有角色**

用 PUT 更新用户信息时，roles 字段覆盖了原有角色——如果 PUT body 中 roles 为空，原有角色全丢。根因：Nexus User API 的 PUT 是完整替换而非部分更新。解决：PUT 前先 GET 获取当前用户信息，合并 roles 后再 PUT。

### 4.5 思考题

1. 如果需要在多个 Nexus 实例之间保持配置同步（如主备模式），将 `nexus-as-code.sh` 扩展为支持"配置变更事件驱动自动部署"的方案。如何保证两个实例的配置最终一致？
2. 设计一个"配置回滚"方案——如果最新一次 `nexus-as-code.sh` 执行后发现问题，如何快速回滚到上一个已知良好的配置版本？

（第24章思考题答案：1. 方案：在 CI 流水线中引入"变更检测"步骤——通过 `git diff` 分析本次提交变更了哪些模块的 pom.xml，只对有变更的模块执行 `mvn deploy`。对于 Gradle 项目使用 `gradle :changed-module:publish`。Nexus 端无需特殊处理——每个模块独立发布。2. 金丝雀发布方案：不使用额外的仓库，而是通过 version 标签策略实现——发布 `my-app:1.2.3-canary` 给 10% 的消费者（通过部署工具的路由权重控制），验证通过后，执行 `docker tag my-app:1.2.3-canary my-app:1.2.3 && docker push my-app:1.2.3`，然后逐步提升正式 tag 的消费者比例。清理策略保留 `-canary` 标签 7 天，确保回滚有据。）

### 4.5 推广计划提示

- **DevOps 团队**：将 `nexus-as-code.sh` + YAML 配置文件纳入 Git 仓库，建立 PR 审批流程
- **架构组**：定义标准化的 YAML 配置规范，作为新团队接入的模板
- **运维团队**：将配置漂移检测集成到监控系统，漂移时发送告警
