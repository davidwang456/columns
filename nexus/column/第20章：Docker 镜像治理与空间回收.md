# 第20章：Docker 镜像治理与空间回收

## 1. 项目背景

浩子周一早上收到一条磁盘告警："Nexus 服务器 /data 分区使用率 97%，预计 48 小时后写满。"他登录检查发现——`blob-docker` 占用了 156GB，是所有其他 BlobStore 总和的 3 倍。但回过头看各团队的 Docker 仓库，前端组只有 12 个镜像，Java 组只有 8 个，总共不过 20 个 tag——怎么 20 个镜像吃掉了 156GB？

浩子深入排查后真相大白：
- Java 组的 CI 每次构建都生成 `my-service:commit-abc1234` 和 `my-service:latest` 两个 tag，累计推了 300+ 个 commit-hash tag
- 这些 tag 之间共享同一个 base image（`openjdk:17-slim`），但也有 15 个不同的 base image 版本被缓存（每个 200MB）
- 有些 commit-hash tag 被删除了，但 tag 引用的 manifest 和共同引用的 layer 没有被回收——这就是"僵尸层"（dangling layers）
- `Cleanup Policy` 上个月就过期了但没重新关联，`Delete unused manifests` 任务从来没创建过

Docker 镜像的存储和回收比 Maven/npm 复杂得多——一层 content-addressable storage（CAS）之上，manifests 引用 layers，tags 引用 manifests，删除一个 tag 不等于释放任何空间。本章将拆解 Docker 镜像在 Nexus 中的存储模型、设计 dev→test→prod 三级镜像晋级策略、配置"Cleanup + Delete unused manifests + Compact BlobStore"三段式空间回收链路，从根本上杜绝 Docker 仓库的"磁盘爆炸"问题。

## 2. 项目设计

浩子把 156GB 的排查报告投屏，大师逐层解剖。

**浩子**："大师，我删了 80 多个 commit-hash tag，为什么空间一点没减少？"

**大师**："因为在 Docker 里，'删除 tag'和'释放空间'之间隔了两层。tag 只是一张贴纸——贴在 manifest 上。你撕掉贴纸，manifest 还在，manifest 引用的 layer blob 也还在。就像图书馆里你删了书目索引中的一条记录，书本身还在书架上。释放空间需要三个步骤——第一步 Cleanup Policy 删除过期 tag；第二步 `Delete unused manifests` 找出没有任何 tag 引用的'悬空 manifest'和只有它引用的 layer，执行删除；第三步 `Compact BlobStore` 物理回收 blob 文件。"

> **技术映射**：Docker 存储四层模型：tag → manifest → layer blob（.bytes 文件）。删除 tag 只是删了第一层指针，真正需要追溯到 layer blob 才能释放磁盘。

**小胖**："为什么 Docker 比 Maven 复杂这么多？Maven 删了 component 就没事了。"

**大师**："因为 Docker 有**内容寻址存储**（CAS）和**层间共享**。10 个 `FROM openjdk:17` 的镜像，都引用同一个 base layer blob——它的 SHA256 摘要相同。如果你随便删其中一个镜像就删 base layer，其他 9 个镜像全炸。Nexus 需要遍历所有 Docker 仓库中所有 manifest 的引用关系，确认某个 layer 已经没有任何人引用，才能安全删除。"

**小白**："`Delete unused manifests and images` 这个任务到底做了什么？会误删有用的镜像吗？"

**大师**："它做三件事：① 扫描所有 Docker hosted 仓库中所有 manifest；② 找出没有被任何 tag 直接或间接引用的 manifest；③ 对每个悬空 manifest，找出**只被它**引用的 layer（如果某个 layer 也被其他活跃 tag 引用，就跳过）。所以它不会误删被 tag 引用的镜像——前提是你的 tag 是正确的。"

> **技术映射**：`Delete unused manifests` = 引用计数垃圾回收（GC）。只有引用计数为 0 的对象才会被清理。活跃 tag 的引用计数永远 >= 1。

**浩子**："那 Dev → Test → Prod 的镜像晋级怎么做？每个环境各建一个 hosted 仓库吗？"

**大师**："两个方案。方案一：按环境建仓库（`docker-dev-hosted`、`docker-prod-hosted`），镜像从 dev 环境 push 到 test 验证后，重新 tag 再 push 到 prod 仓库。方案二：单仓库 + tag 命名策略——同一个 `docker-hosted` 中，`1.0.0-dev-xxx` 表示开发版，`1.0.0-rc1` 表示候选版，`1.0.0` 表示生产版。方案一隔离更彻底但镜像需要重新推；方案二部署简单但需要严格的 tag 规范和清理策略配合。"

**小胖**："现在流行的多架构镜像（multi-arch manifest）在 Nexus 里怎么存的？"

**大师**："多架构镜像是通过 manifest list（也叫 fat manifest）实现的——一个 manifest list 指向多个平台特定的 image manifest（如 linux/amd64、linux/arm64）。在 Nexus 中，每个平台的 image manifest 是一个 Component，manifest list 是一个特殊类型的 Component。`Delete unused manifests` 任务会正确处理这种引用关系——不会因为某个平台没被引用就误删。"

## 3. 项目实战

### 3.1 环境准备

- 已部署 Nexus 实例，Docker 仓库套件已创建
- Docker CLI
- 已上传多个测试镜像（包括多 tag 的同一镜像）

### 3.2 分步实战

#### 步骤一：观察 Docker manifest 和 layer 的存储

**目标**：理解 Nexus 中 Docker 镜像的存储结构和引用关系。

```bash
NEXUS="http://localhost:8081"
AUTH="admin:admin123"

# 查看 Docker hosted 仓库中的组件
COMPONENTS=$(curl -s -u $AUTH \
  "$NEXUS/service/rest/v1/search?repository=docker-hosted")

echo "=== Docker 仓库中的组件 ==="
echo "$COMPONENTS" | jq '.items[] | {name, version, format, assets_count: (.assets | length)}'

# 查看某个镜像的 manifest 和 layer 详情
# 选取第一个组件
COMP_ID=$(echo "$COMPONENTS" | jq -r '.items[0].id')
echo ""
echo "=== 组件 $COMP_ID 的 Assets ==="
curl -s -u $AUTH "$NEXUS/service/rest/v1/components/$COMP_ID" | \
  jq '.assets[] | {id, path, contentType, fileSize: (.fileSize / 1024 / 1024 | round | tostring + " MB")}'
```

**运行结果**：每个 Docker tag 对应一个 Component，其 Assets 包含 manifest JSON 和各个 layer 的 Blob 引用。

#### 步骤二：配置"三段式"Docker 空间回收

**目标**：创建完整的 Docker 清理任务链——Cleanup Policy → Cleanup Task → Delete unused manifests → Compact。

```bash
NEXUS="http://localhost:8081"
AUTH="admin:admin123"

echo "=== Docker 空间回收三段式配置 ==="

# 第一段：Cleanup Policy（删除过期 tag）
echo "[1/4] 创建 Docker Cleanup Policy..."

# Policy 1: 保留最近 5 个版本（按发布时间排序），删除 14 天前的临时 tag
curl -u $AUTH -X POST "$NEXUS/service/rest/v1/cleanup-policies" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "cleanup-docker-retain-5",
    "format": "docker",
    "notes": "Docker 保留最近5个tag,删除14天前的commit-hash临时标签",
    "criteriaLastBlobUpdated": 14,
    "criteriaLastDownloaded": 0,
    "criteriaAssetRegex": ".*"
  }'

# Policy 2: 删除所有 alpha/beta/dev 标签
curl -u $AUTH -X POST "$NEXUS/service/rest/v1/cleanup-policies" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "cleanup-docker-prerelease",
    "format": "docker",
    "notes": "删除 30 天未下载的 pre-release 镜像",
    "criteriaLastBlobUpdated": 0,
    "criteriaLastDownloaded": 30,
    "criteriaAssetRegex": ".*-(alpha|beta|rc|dev).*"
  }'

# 第二段：Cleanup Task（执行策略关联仓库的清理）
echo "[2/4] 创建 Cleanup Task..."
curl -u $AUTH -X POST "$NEXUS/service/rest/v1/tasks" \
  -H "Content-Type: application/json" \
  -d '{
    "action": "repository.cleanup",
    "name": "每周 Docker 清理",
    "typeId": "repository.cleanup",
    "schedule": "weekly",
    "properties": {
      "repositoryName": "docker-hosted",
      "preview": "false"
    },
    "alertEmail": "ops@cloudwhale.com"
  }'

# 第三段：Delete unused manifests（清理悬空层）
echo "[3/4] 创建 Delete unused manifests 任务..."
curl -u $AUTH -X POST "$NEXUS/service/rest/v1/tasks" \
  -H "Content-Type: application/json" \
  -d '{
    "action": "docker.delete-unused",
    "name": "每周清理无引用的 Docker manifest",
    "typeId": "docker.delete-unused",
    "schedule": "weekly",
    "properties": {
      "repositoryName": "*"
    },
    "alertEmail": "ops@cloudwhale.com"
  }'

# 第四段：Compact BlobStore（物理回收）
echo "[4/4] 创建 Compact BlobStore 任务..."
curl -u $AUTH -X POST "$NEXUS/service/rest/v1/tasks" \
  -H "Content-Type: application/json" \
  -d '{
    "action": "blobstore.compact",
    "name": "每周压缩 Docker BlobStore",
    "typeId": "blobstore.compact",
    "schedule": "weekly",
    "properties": {
      "blobstoreName": "blob-docker"
    },
    "alertEmail": "ops@cloudwhale.com"
  }'

echo ""
echo "=== 配置完成 ==="
echo "任务执行顺序（建议 cron 设置）:"
echo "  周六 3:00 → Cleanup Task (删除过期 tag)"
echo "  周六 4:00 → Delete unused manifests (清理悬空 manifest/layer)"
echo "  周日 3:00 → Compact BlobStore (物理回收磁盘空间)"
echo ""
echo "注意：上述三个任务必须按顺序执行，间隔至少 1 小时"
```

#### 步骤三：验证 tag 删除 ≠ 空间释放

**目标**：演示删除 tag 后磁盘空间不减少，执行完整三段式后才释放。

```bash
#!/bin/bash
# docker-space-verify.sh：验证 Docker 空间回收的四步效果
NEXUS="http://localhost:8081"
DOCKER_HOST="localhost:5000"

echo "=== Docker 空间回收验证实验 ==="

# 1. 记录初始 BlobStore 大小
echo "[0] 初始 BlobStore 大小:"
docker compose exec nexus du -sh /nexus-data/blobs/docker/

# 2. 推送一个测试镜像
echo "[1] 推送测试镜像..."
echo "FROM alpine:3.19" > /tmp/Dockerfile.test
docker build -t ${DOCKER_HOST}/space-test:v1 -f /tmp/Dockerfile.test /tmp
docker push ${DOCKER_HOST}/space-test:v1
docker build -t ${DOCKER_HOST}/space-test:v2 -f /tmp/Dockerfile.test /tmp
docker push ${DOCKER_HOST}/space-test:v2

echo "[2] 推送后 BlobStore 大小:"
docker compose exec nexus du -sh /nexus-data/blobs/docker/

# 3. 删除一个 tag（通过 API 删除 component）
echo "[3] 删除 tag v1（仅删除 manifest）..."
COMP_ID=$(curl -s -u admin:admin123 \
  "$NEXUS/service/rest/v1/search?repository=docker-hosted&name=space-test&version=v1" | \
  jq -r '.items[0].id')
curl -u admin:admin123 -X DELETE "$NEXUS/service/rest/v1/components/$COMP_ID"

echo "[4] 删除 tag 后 BlobStore 大小（应不变！）:"
docker compose exec nexus du -sh /nexus-data/blobs/docker/

echo ""
echo "✅ 验证完成：删除 tag 不释放空间！"
echo "   因为 layer 被 v2 和 v1 共享，v2 仍在使用中"
```

#### 步骤四：Dev → Test → Prod 镜像晋级方案

**目标**：实现镜像从开发到生产的晋级流程。

```bash
#!/bin/bash
# docker-promote.sh：Docker 镜像晋级脚本
# 用法: ./docker-promote.sh my-app 1.2.3-rc1 1.2.3

DOCKER_HOST="${DOCKER_REGISTRY:-localhost:5000}"
IMAGE="$1"
FROM_TAG="$2"
TO_TAG="$3"

if [ -z "$IMAGE" ] || [ -z "$FROM_TAG" ] || [ -z "$TO_TAG" ]; then
    echo "用法: $0 <image> <from-tag> <to-tag>"
    echo "示例: $0 my-app 1.2.3-rc1 1.2.3"
    exit 1
fi

echo "=== 镜像晋级: ${IMAGE}:${FROM_TAG} → ${IMAGE}:${TO_TAG} ==="

# 步骤1：拉取源镜像
echo "[1/4] 拉取 ${IMAGE}:${FROM_TAG}..."
docker pull ${DOCKER_HOST}/${IMAGE}:${FROM_TAG}

# 步骤2：打上生产标签
echo "[2/4] 标记为 ${TO_TAG}..."
docker tag ${DOCKER_HOST}/${IMAGE}:${FROM_TAG} ${DOCKER_HOST}/${IMAGE}:${TO_TAG}

# 步骤3：推送到同一仓库（新的 tag 指向同一个 manifest）
echo "[3/4] 推送生产 tag..."
docker push ${DOCKER_HOST}/${IMAGE}:${TO_TAG}

# 步骤4：验证
echo "[4/4] 验证晋级结果..."
echo "  docker pull ${DOCKER_HOST}/${IMAGE}:${TO_TAG}"
echo ""
echo "=== 晋级完成 ==="
echo "注意：${FROM_TAG} 和 ${TO_TAG} 共享同一组 layer，不额外占用空间"
echo "建议：晋级后根据需要清理 RC 标签（cleanup policy 配置）"
```

```bash
chmod +x docker-promote.sh
# ./docker-promote.sh my-app 1.2.3-rc1 1.2.3
```

### 3.3 常见坑点

| 坑点 | 现象 | 解决方法 |
|------|------|----------|
| Cleanup Policy 没关联到仓库 | 每周清理任务执行但什么都没删 | 仓库设置中关联 `cleanup.policyNames` |
| `Delete unused manifests` 没创建 | tag 删了但空间不释放 | 创建该任务并定期执行 |
| 多架构镜像的 manifest list | 删除单个平台的 tag 后其他平台仍有残留 | 需确认所有平台的 manifest 都无引用后才能被回收 |
| `latest` tag 被清理 | 生产环境回滚时 `latest` 指向的镜像消失 | 在 cleanup policy 中排除 `latest` 标签 |

## 4. 项目总结

### 4.1 Docker 清理决策树

```
磁盘告警 → 检查 BlobStore 用量
  ├── Docker 仓库占比最大？
  │   ├── 是 → 执行 Cleanup Task（删除过期 tag）
  │   │   └── 完成 → 执行 Delete unused manifests
  │   │       └── 完成 → 执行 Compact BlobStore
  │   └── 否 → 检查其他 BlobStore
  └── 空间仍未释放？
      ├── 检查是否有活跃 tag 引用了大 layer
      ├── 检查 Compact 任务是否成功完成
      └── 检查是否有"幽灵"blob（需数据库级清理）
```

### 4.2 适用场景

1. **CI 密集构建**：每次 commit 都 build 镜像的团队（需要严格的临时 tag 清理）
2. **多版本并行维护**：同时维护 1.x、2.x 两个大版本的镜像
3. **镜像晋级治理**：dev → test → staging → prod 逐级晋升
4. **多团队共享 base image**：多个项目共享同一个基础镜像，删除时需保护共享层
5. **合规归档**：生产镜像永久保留，可追溯

**不适用场景**：
1. 镜像数量 < 5 个的静态项目（不需要自动化清理）
2. 已有 Harbor 专门管理 Docker 镜像的团队

### 4.3 注意事项

- **三段式顺序不可颠倒**：Cleanup 先、Delete unused manifests 中、Compact 最后
- **`*` 通配符的范围**：`repositoryName: "*"` 作用于所有 Docker 仓库，确保测试仓库也受益
- **多架构镜像的删除原子性**：删除一个 manifest list 时会级联删除其引用的所有平台的 manifest
- **升级 Nexus 版本前检查**：不同版本的 `docker.delete-unused` 任务行为可能有差异

### 4.4 思考题

1. 一个 Java 微服务的镜像从 `FROM openjdk:17-slim` 构建。半年后 `openjdk:17-slim` 在 Docker Hub 上有了 6 个不同的 patch 版本，Nexus proxy 缓存了所有 6 个版本。hosted 仓库中有 50 个服务镜像引用了不同版本的 base image。如果要清理最旧的 4 个 base image 版本，如何安全地识别"哪些服务镜像仍在引用旧版本"？
2. 设计一个实现"保留最近 N 个版本 + 保留所有打了 `release-*` 前缀的 tag + 清理所有 `dev-*` 超过 30 天的标签"的 Cleanup Policy 组合——但 Docker 的 Cleanup Policy 不支持复合条件。如何用多个策略 + 标签命名规范 + 任务编排解决？

（第19章思考题答案：1. `npm unpublish` 会彻底删除包的所有版本及 metadata——在 Nexus OSS 中，这意味着删除 Component 及其所有 Assets，且不可恢复。`npm deprecate` 只是修改 metadata 中该版本的 `deprecated` 字段和显示消息，不删除任何文件——下游项目已锁定的版本仍可安装。后者更安全，它告知使用者"不要用这个版本"，但不破坏已有构建。生产环境强烈推荐 `deprecate` 而非 `unpublish`。2. 利用 Nexus search API 遍历 `npm-hosted` 仓库中的所有 scope 包，获取每个包的所有版本；对每个版本获取 asset 的 downloadCount；结合 CI 日志和 Git 仓库中的 package.json 分析哪些项目引用了哪些版本；将数据导入 Grafana Dashboard（使用 JSON API data source）；并集成 npm audit API 获取漏洞信息。建议用 Node.js 脚本而非 Shell 处理复杂的 JSON 数据分析。）

### 4.5 推广计划提示

- **DevOps 团队**：本章是 Docker 镜像管理的核心章节。立即配置三段式清理链路，纳入每周维护计划
- **开发团队**：了解 tag 命名规范的重要性，在 CI 中统一 commit-hash 标签格式
- **运维团队**：将 Docker BlobStore 的监控阈值设置为 80%，配合自动告警触发清理
