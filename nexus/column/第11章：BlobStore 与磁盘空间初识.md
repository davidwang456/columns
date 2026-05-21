# 第11章：BlobStore 与磁盘空间初识

## 1. 项目背景

云鲸科技的 Nexus 平稳运行了两个月。上午 10 点，Java 组老王在群里发了一条消息："mvn deploy 失败了，报什么 507 Insufficient Storage。"炮哥登录服务器一看：`/nexus-data` 所在的磁盘使用率 100%。他心头一紧——上次清理 NFS 时实习生小赵的失误还历历在目，这次可别再出幺蛾子。

炮哥粗略估算：最近两个月总共上传了约 400 个 Maven 组件、150 个 npm 包、80 个 Docker 镜像，就算每个平均 10MB，满打满算也就 6GB 出头。可磁盘管理器显示 `/nexus-data/blobs/` 目录占用了 68GB。"这 68GB 到底存了些什么？为什么比我算的多十倍？哪个仓库吃掉了最多的磁盘？我能直接删掉 blobs 目录里的某些文件来腾空间吗？"

这正中 Nexus 新手最危险的盲区——**把 BlobStore 当普通文件系统**。在 NFS 上删文件只是 `rm -rf`，但在 Nexus 里直接删除 Blob 文件等于"撕掉图书馆书目卡片"——数据确实没了，但数据库里的索引仍然指向这个 Blob ID，后续请求命中该 Blob 时就会返回 404。68GB 里除了有效制品，还包含旧版本的 Docker layer、SNAPSHOT 时间戳版本的多份拷贝、以及被软删除但尚未物理回收的"僵尸 Blob"。

本章将揭下 BlobStore 的神秘面纱，让你看清制品落盘后的真实面目——从 Blob 的目录结构到软删除机制，从存储去重到压缩回收，并建立"绝不手工动 Blob 文件"的铁律。

## 2. 项目设计

炮哥把磁盘满的告警截图投到大屏幕上，大师召集了各组代表。

**炮哥**："大师，我算了一下实际上传量最多 6GB，但 blobs 目录占了 68GB。多出来的 60GB 是哪来的？"

**大师**："三个来源。第一，**SNAPSHOT 膨胀**——每个 SNAPSHOT 每次 deploy 都会生成一个带时间戳的唯一版本，比如 `common-utils-1.0-20250115.143210-1.jar`、`common-utils-1.0-20250115.151230-2.jar`。旧版本不会自动删除，你一天发布 10 次就留下 9 个无用版本。第二，**Docker layer 冗余**——虽然 Nexus 在 layer 级别做了去重，但不同版本的镜像可能引用了不同版本的 base image，每个版本的 base layer 都占一份空间。第三，**软删除**——你在 Web UI 里删除了组件，但 BlobStore 里的 blob 文件并没有被物理删除，只是标记为'已删除'，等待压缩任务回收。"

> **技术映射**：BlobStore 的空间膨胀 = SNAPSHOT 版本累积 + Docker 基础镜像版本迭代 + 软删除标记待回收。三者叠加的效果远超直觉估算。

**小胖**："软删除？这跟回收站一样？那我文件删了不是应该直接释放空间吗？"

**大师**："这是数据库设计里的经典权衡——立即物理删除需要锁表、重建索引，在高并发仓库里可能阻塞其他用户的正常上传下载。Nexus 的做法是：先标记删除（更新数据库中的 `deleted=true`），让查询时自动过滤掉，再在后台用 Compact BlobStore 任务异步回收。就像食堂收盘子——阿姨不是每吃完一盘就冲去洗，而是先堆在回收车上，等攒够了一车再拉走。"

**小白**："那 blobs 目录到底是什么结构？我想看看到底哪些仓库占了最多空间。"

**大师**："File BlobStore 默认在 `/nexus-data/blobs/<blobstore-name>/` 下。目录结构是两层哈希分桶：`content/vol-{01..99}/chap-{00..FF}/blob-id.properties` 和 `blob-id.bytes`。`.properties` 文件存元数据（格式、创建时间、checksum），`.bytes` 是实际的二进制内容。你看到的 68GB 里大部分是 `.bytes` 文件。"

> **技术映射**：Nexus 用两层哈希目录分散 blob 文件，避免单一目录下文件数过多导致的文件系统性能下降。这是典型的"分桶"（sharding）存储策略。

**炮哥**："那我能直接把旧的 `.bytes` 文件删了吗？反正看文件名也不知道是啥——"

**大师**："千万不能！你手动删除 blob 文件后，数据库里 index 仍然指向这个 blob ID，下次有人请求时 Nexus 会在 BlobStore 里找这个 ID 的文件——找到了返回内容，找不到返回 404。更糟的是，你不知道哪些 blob 被多个组件引用——比如一个 Docker base layer 可能被 20 个镜像引用，你删了这一个文件就炸了 20 个镜像。"

**小白**："那 BlobStore 和 Repository 的关系是什么？一个仓库可以绑多个 BlobStore？一个 BlobStore 可以被多个仓库共享？"

**大师**："一个仓库绑定一个 BlobStore（创建时选择），但多个仓库可以共享同一个 BlobStore。当前你所有仓库都绑定在 `default` 这个 BlobStore 上，所以混在一起根本分不清谁占了多少。解决的办法是：为不同用途的仓库创建独立的 BlobStore——比如 `blob-maven`、`blob-npm`、`blob-docker`，然后各自绑定。这样磁盘空间就可以按仓库类别分别监控和清理。"

**小胖**："那 Nexus 的软删除最终什么时候会释放磁盘？直接重启 Nexus 行不行？"

**大师**："重启 Nexus 不会释放磁盘。物理回收必须通过定时任务 `Admin - Compact blob store`，它会扫描数据库中被标记为 deleted 的 blob，删除对应的 `.bytes` 和 `.properties` 文件，释放磁盘空间。建议设置为每周执行一次，时间选在凌晨业务低峰期。"

## 3. 项目实战

### 3.1 环境准备

- 已按第 2 章部署好 Nexus 实例
- 有已上传制品的仓库（Maven、npm、Docker 等）
- curl、jq

### 3.2 分步实战

#### 步骤一：创建独立的 BlobStore

**目标**：为不同格式创建独立的 BlobStore，实现存储隔离。

```bash
NEXUS="http://localhost:8081"
AUTH="admin:admin123"

# 1. 创建 Maven 专用 BlobStore
curl -u $AUTH -X POST "$NEXUS/service/rest/v1/blobstores/file" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "blob-maven",
    "path": "maven",
    "softQuota": {
      "type": "spaceRemainingQuota",
      "limit": 51200
    }
  }'

# 2. 创建 npm 专用 BlobStore
curl -u $AUTH -X POST "$NEXUS/service/rest/v1/blobstores/file" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "blob-npm",
    "path": "npm",
    "softQuota": {
      "type": "spaceRemainingQuota",
      "limit": 20480
    }
  }'

# 3. 创建 Docker 专用 BlobStore（空间配额更大）
curl -u $AUTH -X POST "$NEXUS/service/rest/v1/blobstores/file" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "blob-docker",
    "path": "docker",
    "softQuota": {
      "type": "spaceRemainingQuota",
      "limit": 102400
    }
  }'

# 4. 创建 Raw 专用 BlobStore
curl -u $AUTH -X POST "$NEXUS/service/rest/v1/blobstores/file" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "blob-raw",
    "path": "raw",
    "softQuota": {
      "type": "spaceRemainingQuota",
      "limit": 20480
    }
  }'
```

**运行结果**：四个 BlobStore 创建成功。在 Web UI `Administration → Repository → Blob Stores` 中可见 `blob-maven`、`blob-npm`、`blob-docker`、`blob-raw` 四个条目。

**参数说明**：
- `softQuota.limit`: 5GB 单位（51200MB = 50GB），当 BlobStore 使用量超过此值时会触发警告但不会阻止写入
- `path`: BlobStore 在 `/nexus-data/blobs/` 下的子目录名称

#### 步骤二：将已有仓库绑定到新 BlobStore

**目标**：验证仓库和 BlobStore 的绑定关系。

```bash
# 查看某个仓库当前绑定的 BlobStore
curl -u $AUTH "$NEXUS/service/rest/v1/repositories/maven-releases" | \
  jq '{name, blobStoreName: .storage.blobStoreName}'

# 预期输出：
# {"name": "maven-releases", "blobStoreName": "default"}

# 注意：已创建的仓库不能直接更改 BlobStore 绑定
# 需要删除仓库重建（数据会丢失），或在创建时指定
# 以下演示创建绑定到新 BlobStore 的仓库：

# 创建绑定到 blob-maven 的新 Maven hosted 仓库
curl -u $AUTH -X POST "$NEXUS/service/rest/v1/repositories/maven/hosted" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "maven-releases-v2",
    "online": true,
    "storage": {
      "blobStoreName": "blob-maven",
      "strictContentTypeValidation": true,
      "writePolicy": "ALLOW_ONCE"
    },
    "maven": {
      "versionPolicy": "RELEASE",
      "layoutPolicy": "STRICT"
    }
  }'
```

#### 步骤三：观察 BlobStore 磁盘目录结构

**目标**：深入了解 File BlobStore 的物理存储结构。

```bash
# 进入 Nexus 容器查看 BlobStore 目录
docker compose exec nexus ls -la /nexus-data/blobs/

# 预期输出：
# drwxr-xr-x 2 nexus nexus 4096 Jan 15 10:00 default
# drwxr-xr-x 2 nexus nexus 4096 Jan 15 14:20 maven
# drwxr-xr-x 2 nexus nexus 4096 Jan 15 14:20 npm
# drwxr-xr-x 2 nexus nexus 4096 Jan 15 14:20 docker
# drwxr-xr-x 2 nexus nexus 4096 Jan 15 14:20 raw

# 查看 default BlobStore 的内容目录结构
docker compose exec nexus ls -la /nexus-data/blobs/default/content/

# 预期输出（两层哈希目录）：
# drwxr-xr-x ... vol-01/
# drwxr-xr-x ... vol-02/
# ...
# drwxr-xr-x ... vol-99/

# 深入一层
docker compose exec nexus ls /nexus-data/blobs/default/content/vol-01/

# 预期输出：
# chap-00/ chap-01/ ... chap-FF/

# 查看某个实际 blob
docker compose exec nexus ls /nexus-data/blobs/default/content/vol-01/chap-00/ | head -5

# 预期输出（示例）：
# a1b2c3d4-xxxx.properties
# a1b2c3d4-xxxx.bytes
```

**运行结果**：每个 blob 由 `.properties`（元数据）和 `.bytes`（二进制内容）两个文件组成，分散在两层哈希目录中。

**查看 Blob 元数据**：

```bash
# 随机选取一个 .properties 文件查看
docker compose exec nexus cat /nexus-data/blobs/default/content/vol-01/chap-00/a1b2c3d4-xxxx.properties 2>/dev/null || echo "(替换为实际 blob ID)"

# properties 内容示例：
# #Blob Property
# #Tue Jan 15 14:32:10 CST 2025
# @BlobStore.created-by=admin
# @BlobStore.created-by-ip=192.168.1.100
# @BlobStore.content-type=application/java-archive
# @BlobStore.blob-name=common-utils-1.0.0.jar
# @BlobStore.blob-ref=default@a1b2c3d4-xxxx
# size=245760
# sha1=abc123...
```

#### 步骤四：检查 BlobStore 空间使用

**目标**：通过 API 检查每个 BlobStore 的空间占用。

```bash
# 查看所有 BlobStore 的配额和使用情况
curl -u $AUTH "$NEXUS/service/rest/v1/blobstores" | jq '.[] | {name, type, totalSize, blobCount, availableSpace}'

# 如果支持，查看具体配额（部分版本 API 可能略有差异）
curl -u $AUTH "$NEXUS/service/rest/v1/blobstores/default" | jq '{name, blobCount, totalSize, softQuota}'

# 计算各 BlobStore 的磁盘占用（在宿主机上）
docker compose exec nexus du -sh /nexus-data/blobs/*/
```

**运行结果**：各 BlobStore 的磁盘占用清晰可见，便于后续做容量规划。

#### 步骤五：执行软删除并观察空间不释放

**目标**：证明软删除不立即释放磁盘空间。

```bash
# 1. 上传一个测试文件
echo "test content for blob demo" > /tmp/test-blob.txt
curl -u $AUTH -X PUT \
  "http://localhost:8081/repository/raw-agents/test/blob-demo.txt" \
  --data-binary @/tmp/test-blob.txt

# 2. 查看上传前后的 default BlobStore 大小
docker compose exec nexus du -sh /nexus-data/blobs/default/

# 3. 通过 API 搜索并删除
COMP_ID=$(curl -s -u $AUTH \
  "$NEXUS/service/rest/v1/search?repository=raw-agents&name=blob-demo.txt" | \
  jq -r '.items[0].id')

curl -u $AUTH -X DELETE "$NEXUS/service/rest/v1/components/$COMP_ID"

# 4. 立即检查磁盘空间——应该不变
docker compose exec nexus du -sh /nexus-data/blobs/default/
echo "注意：软删除后磁盘空间未释放，需等待 Compact BlobStore 任务执行"

# 5. 手动触发 Compact BlobStore 任务（模拟回收）
curl -u $AUTH -X POST \
  "$NEXUS/service/rest/v1/tasks" \
  -H "Content-Type: application/json" \
  -d '{
    "action": "blobstore.compact",
    "name": "手动压缩 default",
    "typeId": "blobstore.compact",
    "schedule": "manual",
    "properties": {
      "blobstoreName": "default"
    }
  }'

# 手动运行（Web UI: Tasks → 选中任务 → Run）
echo "请在 Web UI: System → Tasks 中手动运行刚创建的任务，然后再次检查磁盘"
```

#### 步骤六（可选）：监控脚本——BlobStore 容量预警

```bash
#!/bin/bash
# blobstore-monitor.sh：监控所有 BlobStore 的容量
NEXUS="http://localhost:8081"
AUTH="admin:admin123"
WARN_PERCENT=75
CRIT_PERCENT=90

echo "=== Nexus BlobStore 容量监控 ==="
echo "时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo ""

# 获取所有 BlobStore
curl -s -u $AUTH "$NEXUS/service/rest/v1/blobstores" | jq -r '.[] | "\(.name) \(.totalSize // 0) \(.blobCount // 0)"' | while read -r NAME SIZE COUNT; do
    QUOTA=$(curl -s -u $AUTH "$NEXUS/service/rest/v1/blobstores/$NAME" | jq -r '.softQuota.limit // 0')

    if [ "$QUOTA" != "0" ] && [ "$QUOTA" != "null" ]; then
        USAGE_PCT=$(( SIZE * 100 / (QUOTA * 1024 * 1024) ))  # QUOTA 单位 MB
        STATUS="✅"
        [ "$USAGE_PCT" -ge "$WARN_PERCENT" ] && STATUS="⚠️"
        [ "$USAGE_PCT" -ge "$CRIT_PERCENT" ] && STATUS="🚨"
        echo "$STATUS $NAME: $((SIZE / 1024 / 1024))MB / $((QUOTA / 1024))GB (${USAGE_PCT}%) - $COUNT 个 blobs"
    else
        echo "📦 $NAME: $((SIZE / 1024 / 1024))MB - $COUNT 个 blobs (无配额限制)"
    fi
done
```

```bash
chmod +x blobstore-monitor.sh && ./blobstore-monitor.sh
```

### 3.3 常见坑点

| 坑点 | 现象 | 解决方法 |
|------|------|----------|
| 手动删除 `.bytes` 文件 | Nexus 中对应组件下载返回 404 | **绝对不要直接操作 Blob 文件**。通过 API 或 UI 删除组件，由 Compact BlobStore 任务回收 |
| 默认 BlobStore 承载所有仓库 | 磁盘满后无法定位哪个仓库是元凶 | 创建独立的 BlobStore 并按仓库用途绑定 |
| 移动或重命名 BlobStore 目录 | Nexus 启动后所有绑定该 BlobStore 的仓库不可用 | BlobStore 的 `path` 一经设定不可修改 |
| 软删除后立即检查磁盘 | 空间未释放引发困惑 | 软删除是标记操作，物理回收依赖 Compact BlobStore 定时任务 |
| `softQuota` 不阻止写入 | 超出配额后仍能上传 | `softQuota` 仅触发警告日志，不阻断写入；PRO 版支持 `hardQuota` |

## 4. 项目总结

### 4.1 BlobStore 类型对比

| 类型 | 存储后端 | 适用场景 | 优缺点 |
|------|---------|---------|--------|
| File BlobStore | 本地文件系统 | 单机部署、中小团队 | ✅ 配置简单 ❌ 容量受本地磁盘限制 |
| S3 BlobStore (PRO) | AWS S3 兼容对象存储 | 大规模、跨地域、HA | ✅ 弹性容量、多 AZ ❌ OSS 版不可用、延迟略高 |
| Azure BlobStore (PRO) | Azure Blob Storage | Azure 云环境 | ✅ Azure 生态集成 ❌ OSS 版不可用 |

### 4.2 适用场景

1. **存储容量隔离**：为不同格式的仓库绑定独立 BlobStore，方便独立监控和清理
2. **容量规划**：通过 `softQuota` 设置预警阈值，在磁盘满之前主动发现问题
3. **性能优化**：将高 IO 的 Docker BlobStore 放在 SSD 上，将低频的 Maven BlobStore 放在 HDD 上
4. **备份分层**：按 BlobStore 粒度做备份，核心仓库的 BlobStore 每日备份，非核心每周备份
5. **合规审计**：通过 Blob 元数据追溯制品的创建时间、创建者、checksum

**不适用场景**：
1. 需要跨地域自动复制的场景——BlobStore 本身不具备复制能力（需 PRO 版 Repository Replication）
2. PB 级海量小文件存储——考虑对象存储方案而非 File BlobStore 的单机磁盘

### 4.3 注意事项

- **BlobStore 的 `path` 不可修改**：创建时慎重命名，后续无法更改
- **`softQuota` 仅是软限制**：不阻止写入，仅向日志输出警告；需要配合监控脚本实现主动告警
- **BlobStore 不能直接迁移**：如需更换存储后端，需通过仓库导出→导入的方式间接实现
- **direct memory 与 Blob IO 的关系**：Nexus 使用 Direct ByteBuffer 进行大文件 IO，`-XX:MaxDirectMemorySize` 需大于最大可上传文件的两倍

### 4.4 常见踩坑经验

**故障一：Docker BlobStore 增长失控**

运维发现 `blob-docker` 每天增长 15GB，远超预期。排查发现 CI 流水线每次构建都产生新的 `latest` tag，但旧的 `latest` 引用从未删除——Nexus 保留了所有历史 manifest，对应的 layer 也未回收。解决：配置 Docker 的 Cleanup Policy 保留最近 5 个版本的 tag，并通过 `Delete unused manifests` 任务清理悬空 manifest。

**故障二：BlobStore 迁移失败**

某团队将 Nexus 的 `/nexus-data` 从 SSD 迁移到更大的 HDD，直接用 `cp -r` 拷贝了整个 blobs 目录。重启后部分仓库报 blob 缺失。根因：拷贝过程中部分 `.properties` 文件损坏（cp 时磁盘 IO 繁忙）。解决：迁移时必须先停止 Nexus 服务，使用 `rsync -avz --checksum` 保证文件完整性。

**故障三：BlobStore 的 `content` 目录中出现大量 0 字节文件**

某日巡检发现 `default/content/` 下有数百个 0 字节的 `.bytes` 文件。根因：上传请求在 Nexus 写入 blob 时客户端连接中断，Nexus 创建了空 blob 文件但未完成写入。这些文件占用 inode 但不占空间，长期积累可能影响文件系统性能。解决：通过 Compact BlobStore 任务清理无引用关系的尸体 blob。

### 4.5 思考题

1. 如果一个 Blob 被 3 个不同的 Component 引用（如 Docker 的 base layer），当其中一个 Component 被删除时，这个 Blob 会被删除吗？为什么？（提示：引用计数 vs 软删除标记）
2. 现在需要将 `default` BlobStore 从 SSD 迁移到 HDD，同时保持所有仓库正常工作。设计一套零停机的迁移方案，并指出其中的风险点。

（第10章思考题答案：1. 会返回两个结果——一个来自 `maven-releases`（hosted），一个来自 `maven-central-proxy`（proxy）。它们的 `repository` 字段分别指向各自的仓库名。因为搜索 API 是在所有匹配的仓库中做并集查询，同一个坐标在不同仓库中是独立的 Component。2. 方案：① 通过 Search API 全局搜索 `logback-core`，获取所有版本列表；② 用 `artifact-info.sh` 对每个版本获取详情及仓库位置；③ 在 CI 流水线的构建日志中 grep `logback-core` 定位使用该依赖的项目；④ 对未通过 CI 构建的项目（如遗留项目），扫描各项目 Git 仓库中的 `pom.xml`/`build.gradle`/`package.json` 等依赖声明文件；⑤ 输出受影响服务清单，按紧急程度排序，邮件通知各项目负责人。）

### 4.6 推广计划提示

- **运维部门**：本章是运维团队的必修课。立即为各格式创建独立 BlobStore，配置容量监控脚本并纳入告警体系
- **开发部门**：理解 SNAPSHOT 版本的累积对磁盘的影响，主动清理过期的开发版本
- **安全部门**：Blob 的元数据（`.properties`）包含创建者和 IP 信息，可用于安全审计
