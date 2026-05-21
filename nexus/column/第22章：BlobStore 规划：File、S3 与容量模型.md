# 第22章：BlobStore 规划：File、S3 与容量模型

## 1. 项目背景

云鲸科技的 DevOps 总监拿到了一份令人不安的供应商账单：Nexus 服务器用了 500GB SSD 云盘，每月固定费用 3200 元，但实际利用率只有 35%（175GB 有效数据），剩余的 65% 全是"不知道有没有用"的旧 SNAPSHOT、proxy 缓存、以及被软删除但未回收的 Blob。同时，数据中台团队的新需求更让炮哥头疼——他们的机器学习模型文件每个 2-5GB，预计每年新增 2TB，单台服务器根本扛不住。

炮哥找大师商量："能不能把 Maven 和 npm 的小文件放本地 SSD，把 Docker 镜像放便宜的 HDD，把 ML 模型丢到 S3 兼容的对象存储上？"这触及了 Nexus BlobStore 规划的核心命题——**不同制品对存储的 IOPS、延迟、容量、成本的敏感度完全不同，一刀切地使用同一个 File BlobStore 是容量灾难的根源**。

本章将从 IO 特性分析出发，建立 File BlobStore 与 S3 BlobStore 的选型矩阵，构建包含增量预测、清理效率、备份窗口的三维容量模型，并设计一套从 File 到 S3 的安全迁移方案——让 Nexus 的存储层从"有磁盘就行"升级到"容量可规划、成本可优化、扩展可弹性"。

## 2. 项目设计

炮哥在工位上算了半天，把各团队六个月预估的制品增量表给大师看。

**大师**："你这个预估只算了'会上传什么'，没算'谁不会走'。一个完整的容量模型有三个变量：**新增量**（每天上传多少）、**保留量**（清理策略后留下多少）、**回收延迟**（删除标记到物理回收之间的时间差）。三者叠加才是实际磁盘占用。"

**小胖**："这三个变量能具体点吗？比如说 Docker 和 Maven 的差别？"

**大师**："以云鲸为例——Docker 每天 CI 产生 20 个临时镜像（每个 200MB），新增 4GB/天。Cleanup Policy 只保留 7 天内的标签，但 Delete unused manifests 每周执行一次，Compact 再晚两天——所以实际磁盘上至少保留 9 天的量，约 36GB。Maven SNAPSHOT 每天新增 0.5GB，保留 14 天，但 proxy 缓存的 RELEASE 版本从不清理——它们会持续累积。所以三个月后 Docker 占用约 40GB 达到稳态，但 Maven proxy 缓存在持续线性增长。"

> **技术映射**：容量模型 = 新增速率 × (保留天数 + 回收延迟)。其中回收延迟 = Cleanup Task 间隔 + Compact 间隔 + 执行耗时。

**小白**："那什么时候用 File BlobStore，什么时候用 S3 BlobStore？"

**大师**："从 IO 特性看——**Maven 和 npm** 是小文件为主（1KB-50MB），高并发随机读，对 IOPS 敏感，放本地 SSD 最佳。**Docker** 是大文件（10MB-2GB）为主，layer 共享后随机读取减少，但写入吞吐要求高，可以放 HDD 或 S3。**ML 模型文件**是超大文件（2GB+），低频读取，存储成本 > 访问速度，放 S3 最合适。"

**炮哥**："OSS 版的 Nexus 支持 S3 BlobStore 吗？我怎么没在 UI 里看到？"

**大师**："S3 BlobStore 是 **PRO 版本的功能**，OSS 版只有 File BlobStore。但这不代表 OSS 用户就没办法用对象存储——你可以用 `s3fs` 或 `goofys` 将 S3 Bucket 挂载为本地文件系统，Nexus 把它当作普通的 File BlobStore 使用。不过要注意——对象存储的延迟比本地磁盘高得多，不适合高 IOPS 场景。"

> **技术映射**：OSS 版通过 FUSE 挂载间接使用 S3 = 成本优化但性能折损。PRO 版原生 S3 BlobStore = 性能最优但需要商业许可。选型取决于预算和对性能的要求。

**小胖**："那 BlobStore 之间的迁移怎么做？我们现在的所有仓库都在 default BlobStore 上，怎么把它们拆分到独立的 BlobStore？"

**大师**："BlobStore 迁移是 Nexus 运维中最复杂的操作之一——因为仓库创建后无法直接更改绑定的 BlobStore。迁移路径是：创建新的 BlobStore → 创建新的仓库绑定新 BlobStore → 通过 API 逐组件从旧仓库导出、导入到新仓库 → 更新 group 仓库的成员列表 → 观察期后删除旧仓库和旧 BlobStore。整个流程本质是一次数据搬迁。"

## 3. 项目实战

### 3.1 环境准备

- 已部署 Nexus 实例
- curl、jq
- 如需 S3 测试：MinIO 容器（`docker run -p 9000:9000 minio/minio`）或 AWS 账户

### 3.2 分步实战

#### 步骤一：按 IO 特性设计 BlobStore 拓扑

**目标**：为不同格式规划独立的 BlobStore，绑定到合适的存储介质。

```bash
NEXUS="http://localhost:8081"
AUTH="admin:admin123"

echo "=== 创建分层 BlobStore 拓扑 ==="

# 1. Maven SNAPSHOT — 高 IOPS 小文件 → SSD（独立 BlobStore 便于频繁清理）
curl -u $AUTH -X POST "$NEXUS/service/rest/v1/blobstores/file" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "blob-maven-snap",
    "path": "maven-snap",
    "softQuota": {"type": "spaceRemainingQuota", "limit": 20480}
  }'

# 2. Maven RELEASE — 低 IO 持久存储 → HDD（或 SSD，配额适中）
curl -u $AUTH -X POST "$NEXUS/service/rest/v1/blobstores/file" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "blob-maven-release",
    "path": "maven-release",
    "softQuota": {"type": "spaceRemainingQuota", "limit": 51200}
  }'

# 3. Docker — 大文件吞吐 → HDD 或独立大容量磁盘
curl -u $AUTH -X POST "$NEXUS/service/rest/v1/blobstores/file" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "blob-docker",
    "path": "docker",
    "softQuota": {"type": "spaceRemainingQuota", "limit": 204800}
  }'

# 4. ML 模型 — 超大文件低频 → 独立大容量 BlobStore（或 S3）
curl -u $AUTH -X POST "$NEXUS/service/rest/v1/blobstores/file" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "blob-ml-models",
    "path": "ml-models",
    "softQuota": {"type": "spaceRemainingQuota", "limit": 512000}
  }'

echo "=== BlobStore 拓扑创建完成 ==="
```

#### 步骤二：编写 6 个月容量预测脚本

**目标**：基于当前增长趋势预测未来容量需求。

```bash
#!/bin/bash
# capacity-forecast.sh：6 个月容量预测
NEXUS="http://localhost:8081"
AUTH="admin:admin123"

echo "=== Nexus 6 个月容量预测 ==="
echo ""

# 获取所有 BlobStore
curl -s -u $AUTH "$NEXUS/service/rest/v1/blobstores" | jq -r '.[] | "\(.name) \(.totalSize // 0)"' | while read -r NAME SIZE; do
    SIZE_MB=$((SIZE / 1024 / 1024))
    QUOTA=$(curl -s -u $AUTH "$NEXUS/service/rest/v1/blobstores/$NAME" | jq -r '.softQuota.limit // "N/A"')

    # 按经验分配预估的日增长率（实际应基于历史数据）
    case "$NAME" in
        *maven-snap*) DAILY_GROWTH=500 ;;     # 500MB/天
        *maven-release*) DAILY_GROWTH=400 ;;   # 400MB/天
        *docker*) DAILY_GROWTH=1500 ;;         # 1.5GB/天
        *ml*) DAILY_GROWTH=800 ;;              # 800MB/天
        *) DAILY_GROWTH=200 ;;
    esac

    # 预测 6 个月（180 天）
    PREDICTED_MB=$((SIZE_MB + DAILY_GROWTH * 180))
    PREDICTED_GB=$((PREDICTED_MB / 1024))

    STATUS="✅"
    if [ "$QUOTA" != "N/A" ] && [ "$PREDICTED_MB" -gt "$QUOTA" ]; then
        STATUS="🚨 将超出配额 $((QUOTA / 1024))GB！"
    fi

    printf "%-25s 当前: %4d MB | 日增: %4d MB | 6月后: %4d MB (%3d GB) %s\n" \
      "$NAME" "$SIZE_MB" "$DAILY_GROWTH" "$PREDICTED_MB" "$PREDICTED_GB" "$STATUS"
done

echo ""
echo "=== 建议 ==="
echo "1. 对于预测超出配额的 BlobStore，提前扩容或调整清理策略"
echo "2. 对于 Docker 和 ML 类高频增长，评估是否迁移到 S3"
```

```bash
chmod +x capacity-forecast.sh && ./capacity-forecast.sh
```

#### 步骤三：BlobStore 的安全迁移方案

**目标**：展示从旧 BlobStore 迁移组件到新 BlobStore 的完整流程。

```bash
#!/bin/bash
# blobstore-migrate.sh：BlobStore 迁移工具（仓库级）
# 功能：将源仓库的组件复制到目标仓库（绑定不同 BlobStore）

NEXUS="http://localhost:8081"
AUTH="admin:admin123"

SOURCE_REPO="${1:-maven-snapshots}"
TARGET_REPO="${2:-maven-snapshots-v2}"

echo "=== BlobStore 迁移 ==="
echo "源仓库: $SOURCE_REPO"
echo "目标仓库: $TARGET_REPO"
echo ""

# 此脚本演示迁移思路——对于大规模生产迁移建议使用专用工具
# 1. 分页遍历源仓库所有组件
TOKEN=""
MIGRATED=0
SKIPPED=0
FAILED=0

while true; do
    if [ -z "$TOKEN" ]; then
        RESP=$(curl -s -u $AUTH "$NEXUS/service/rest/v1/search?repository=$SOURCE_REPO")
    else
        ENCODED=$(echo -n "$TOKEN" | jq -sRr @uri)
        RESP=$(curl -s -u $AUTH "$NEXUS/service/rest/v1/search?repository=$SOURCE_REPO&continuationToken=$ENCODED")
    fi

    ITEM_COUNT=$(echo "$RESP" | jq '.items | length')
    if [ "$ITEM_COUNT" = "0" ]; then
        break
    fi

    for ((i=0; i<ITEM_COUNT; i++)); do
        ASSET_PATH=$(echo "$RESP" | jq -r ".items[$i].assets[0].path")
        ASSET_URL=$(echo "$RESP" | jq -r ".items[$i].assets[0].downloadUrl")

        # 2. 检查目标仓库是否已存在相同路径
        CHECK=$(curl -s -o /dev/null -w "%{http_code}" -u $AUTH \
          "$NEXUS/repository/$TARGET_REPO/$ASSET_PATH")

        if [ "$CHECK" = "200" ]; then
            echo "  ⏭️  $ASSET_PATH (已存在，跳过)"
            ((SKIPPED++))
            continue
        fi

        # 3. 下载 → 上传
        TMP_FILE="/tmp/nexus-migrate-$(basename "$ASSET_PATH")"
        curl -s -u $AUTH -o "$TMP_FILE" "$ASSET_URL"

        HTTP=$(curl -s -o /dev/null -w "%{http_code}" -u $AUTH \
          -X PUT "$NEXUS/repository/$TARGET_REPO/$ASSET_PATH" \
          --data-binary @"$TMP_FILE")

        rm -f "$TMP_FILE"

        if [ "$HTTP" = "201" ] || [ "$HTTP" = "200" ]; then
            echo "  ✅ $ASSET_PATH"
            ((MIGRATED++))
        else
            echo "  ❌ $ASSET_PATH (HTTP $HTTP)"
            ((FAILED++))
        fi
    done

    TOKEN=$(echo "$RESP" | jq -r '.continuationToken // empty')
    if [ -z "$TOKEN" ]; then
        break
    fi
done

echo ""
echo "迁移完成: 成功 $MIGRATED  跳过 $SKIPPED  失败 $FAILED"
echo ""
echo "后续步骤:"
echo "1. 验证目标仓库中组件数量与源仓库一致"
echo "2. 更新 group 仓库成员列表（移除旧仓库，加入新仓库）"
echo "3. 通知所有客户端更新配置（如果使用了直接仓库地址而非 group）"
echo "4. 观察 1 周后删除旧仓库和旧 BlobStore"
```

```bash
chmod +x blobstore-migrate.sh
```

#### 步骤四：FUSE 挂载 S3 作为 File BlobStore（OSS 版间接方案）

**目标**：使用 s3fs 将 MinIO/AWS S3 挂载为本地目录供 Nexus 使用。

```bash
# === 使用 s3fs 挂载 S3 兼容存储（以 MinIO 为例） ===

# 1. 安装 s3fs（CentOS 7 示例）
# yum install -y s3fs-fuse

# 2. 配置 S3 认证
# echo "YOUR_ACCESS_KEY:YOUR_SECRET_KEY" > ~/.passwd-s3fs
# chmod 600 ~/.passwd-s3fs

# 3. 创建挂载点
# mkdir -p /mnt/nexus-s3

# 4. 挂载 MinIO Bucket 到本地目录
# s3fs nexus-blobs /mnt/nexus-s3 \
#   -o passwd_file=~/.passwd-s3fs \
#   -o url=http://minio-server:9000 \
#   -o use_path_request_style \
#   -o allow_other \
#   -o umask=000

# 5. 在 Nexus 中创建指向此挂载路径的 File BlobStore
# curl -u admin:admin123 -X POST "http://localhost:8081/service/rest/v1/blobstores/file" \
#   -H "Content-Type: application/json" \
#   -d '{
#     "name": "blob-s3-fuse",
#     "path": "/mnt/nexus-s3"
#   }'

echo "=== 注意 ==="
echo "s3fs 挂载方案适用于低频小文件场景"
echo "高 IOPS / 大文件场景建议直接使用 PRO 版的 S3 BlobStore"
echo "s3fs 延迟比本地磁盘高 5-20ms，可能影响高并发场景的下载速度"
```

### 3.3 常见坑点

| 坑点 | 现象 | 解决方法 |
|------|------|----------|
| s3fs 写入性能极差 | Docker 镜像 push 超时 | s3fs 不适合高吞吐写入，大文件 upload 用原生 S3 API |
| BlobStore path 重复 | 创建时报错 | 每个 File BlobStore 的 path 必须唯一，不可跨 BlobStore 共享 |
| 迁移后 checksum 变化 | 下载的 jar 与原文件 SHA256 不同 | 检查迁移脚本中的文件传输是否完整（用 `--data-binary` 而非 `-d`） |
| BlobStore 超过配额不告警 | 磁盘悄悄写满 | `softQuota` 仅输出日志不阻断写入——需配合外部监控 |

## 4. 项目总结

### 4.1 存储分层推荐

| 制品类型 | 典型文件大小 | IO 特性 | 推荐 BlobStore 类型 | 推荐存储介质 |
|---------|------------|--------|--------------------|------------|
| Maven jar/pom | 1KB-50MB | 高并发小文件随机读 | File BlobStore | SSD |
| npm tarball | 5KB-20MB | 大量小文件随机读 | File BlobStore | SSD |
| Docker layer | 10MB-2GB | 大文件顺序读写 | File BlobStore（可放 HDD） | HDD/SSD |
| ML 模型 | 2GB+ | 低频大块读 | S3 BlobStore（PRO）或 s3fs | 对象存储 |
| proxy 缓存 | 不等 | 热缓存高 IO，冷缓存低 IO | File BlobStore | HDD/SSD（按热度） |

### 4.2 适用场景

1. **多租户存储隔离**：不同团队/格式使用独立 BlobStore，避免相互影响
2. **成本优化**：热数据放 SSD，冷数据放 HDD 或 S3
3. **弹性扩展**：ML 模型等大文件转存 S3，突破单机磁盘限制
4. **存储迁移**：从单机 File 逐步迁移到对象存储

**不适用场景**：
1. 单格式、总数据量 < 50GB——使用 default BlobStore 即可
2. 无对象存储环境且 OSS 版——无法使用 S3 BlobStore

### 4.3 注意事项

- **BlobStore 命名后不可改 `path`**：创建时认真规划，避免后期迁移
- **s3fs 是妥协方案，不是银弹**：高 IOPS 场景下延迟会明显影响 Nexus 响应时间
- **软删除不释放空间**：Compact 的频率要匹配 BlobStore 的写入速率
- **多个仓库可共享同一 BlobStore**：按 IO 特性分组绑定，不必每个仓库一个

### 4.4 思考题

1. 云鲸科技目前有一个 500GB 的 SSD 和一个 2TB 的 HDD，Nexus 部署在这一台服务器上。如何规划 BlobStore 的存储介质分配使得 SSD 承载热数据、HDD 承载冷数据，同时保持所有仓库在线？
2. 如果使用 S3 BlobStore（PRO 版），Docker 镜像的 layer 去重机制在对象存储上是否仍然有效？Push 一个与已有镜像共享 base layer 的新镜像时，Nexus 会向 S3 发送完整的 layer 还是跳过？

（第21章思考题答案：1. 方案：创建一个 maven-public group，其中包含三个 proxy 成员——Maven Central（排在最先）、Aliyun Mirror、Tencent Mirror。三个 proxy 都设置 `autoBlock: true`。当 Maven Central 故障时，autoBlock 熔断后请求自动 fallback 到 Aliyun Mirror；Aliyun 也故障时 fallback 到 Tencent。每个 proxy 独立缓存各自拉取的包——冗余但保证任意两个故障时仍有一个可用。注意：需要设置 `metadataMaxAge` 一致，否则 metadata 版本不一致可能导致冲突。2. 方案：从 pom.xml 中解析所有 `<dependency>` 和 `<plugin>` 声明，提取 GAV；对每个 GAV 检查 Nexus proxy 缓存中是否已存在（HEAD 请求）；对于缺失的包，按批次（每批 10 个）提交缓存预热请求，批次间隔 5 秒保证不压垮远程仓库；失败的包记录日志并在全部完成后重试一次。对于 package.json，解析 dependencies/devDependencies，用 `npm view <pkg> --json` 获取版本信息，预下载对应的 tarball。）

### 4.5 推广计划提示

- **运维部门**：按本章分层拓扑创建 BlobStore，为每个存储池配置独立的磁盘监控
- **架构组**：评估 PRO 版 S3 BlobStore 对 ML 模型和 Docker 镜像存储的成本收益
- **财务部门**：利用容量预测脚本为年度存储预算提供数据支撑
