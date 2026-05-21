# 第7章：Raw 仓库与通用文件分发

## 1. 项目背景

云鲸科技的运维自动化平台需要下发一批 Agent 安装包到 200 台生产服务器，包含 `.rpm`、`.deb`、`.tar.gz`、`.msi` 等格式，版本从 2.1.0 到 3.4.2 共 18 个版本。当前做法是在一台 NFS 服务器上建了 `software/agents/` 目录，按版本号建子文件夹，脚本里写 `wget http://files.internal/software/agents/3.4.2/agent-linux-amd64.tar.gz`。

这种方式**已经凑合了两年，直到上周一次事故彻底打破了运维组的容忍**：一个新来的运维实习生误删了 `software/agents/2.1.0/` 整个目录——2.1.0 是一个大客户的遗留版本，仍在 12 台老服务器上运行。恢复时发现这台 NFS 服务器既没有快照、也没有备份（"文件服务器又不是数据库，谁会做备份呢？"）。最终靠着一个老运维的笔记本上还残存着 `agent-2.1.0.tar.gz` 才勉强救回来。

更大的问题是，除了 Agent 安装包，团队还有机器学习模型文件（500MB-2GB）、CI 离线依赖包（node_modules.tar.gz）、数据库初始化脚本（SQL dump）、iOS/Android 测试包（ipa/apk）——这些文件的共性是：**不属于 Maven/npm/Docker 任何一种标准格式，但同样需要版本管理、权限控制、下载历史和审计追踪**。

Nexus Raw 仓库正是为这个场景设计的：将任意二进制文件当作"制品"管理，享受和 Maven jar 一样的存储、版本、权限、缓存和清理能力——只是少了格式特定的元数据解析。

## 2. 项目设计

运维组的实习生小赵正在被浩子训话，大师走进运维办公室正好撞见。

**浩子**："小赵，你删文件前不先确认的吗？那个 2.1.0 是大客户 XX 银行的专用版本！"

**小赵**（沮丧）："我看文件夹名写着 2.1.0，心想这么老的版本肯定不用了啊...而且共享目录连个回收站都没有。"

**大师**："浩子，这种事不能全怪小赵。根本问题是我们用 NFS 管理二进制文件——它只是存储，不是制品管理。Nexus 的 Raw 仓库可以彻底解决这类问题。"

**小胖**（也在旁听）："Raw 仓库？这名字听起来就像'什么都往里塞的杂物间'。"

**大师**："这个比喻倒挺准，但 Raw 仓库是'有门禁、有标签、有清单的智能杂物间'。你把任何文件上传到 Raw 仓库后，它能做到三件事：第一，文件作为一个 Asset 被管理，有 checksum、有上传时间、有上传者、有路径；第二，受权限控制——小赵这样的角色可以设为只读，删不了文件；第三，删除是软删除，配合 BlobStore 的回收机制，误删了还能找回。"

> **技术映射**：Raw 仓库 = 通用二进制制品仓库。它不是"把文件当文件存"，而是"把文件当制品管"——赋予每个文件 Maven artifact 同级别的管理能力。

**小白**："但 Raw 文件和 Maven 包有个本质区别——Maven 有 GAV 坐标，npm 有 scope+name，Docker 有 repo+tag。Raw 文件就一个路径，怎么做版本管理？"

**大师**："通过路径规范。比如 Agent 安装包可以组织为 `/agent/linux/amd64/3.4.2/agent.tar.gz`，版本号内置在路径中。或者用 `/agent/3.4.2/agent-linux-amd64.tar.gz`。这里的关键不是 Nexus 能不能解析版本——而是**你的团队约定好路径规范后，Nexus 来执行和管理这份约定**。"

**小胖**："那 Raw hosted、Raw proxy、Raw group 又是啥？给文件也做代理缓存？"

**大师**："Raw proxy 反而用得不多。但 Raw hosted 和 Raw group 非常实用。hosted 仓库就是你自己的"发布区"，你通过 curl 或 API 把文件推上去。group 仓库可以把多个 hosted 合并成一个虚拟路径树——例如你把 Agent 放 `raw-agents` 仓库，把数据库脚本放 `raw-db-scripts` 仓库，然后通过 `raw-public` 这个 group 对外统一暴露。对外部消费者来说，所有文件似乎都在同一个目录下。"

> **技术映射**：Raw group 本质是虚拟路径聚合——多个 hosted 仓库的文件在 group 下呈现为统一的目录树，简化客户端配置。

**浩子**："那 Raw 仓库的搜索能力呢？我想找'所有 3.x 版本的 agent'，或者'上周上传的所有文件'。"

**大师**："Raw 仓库支持按路径和名称搜索。API 提供了 `search` 接口，可以按仓库+名称（name）+分组（group）过滤。虽然没有 Maven 的坐标级搜索强，但配合统一的路径规范，已经能覆盖大部分运维场景。"

**小白**："权限控制呢？如果我只想让 Linux 运维看到 `/agent/linux/*`，Windows 运维只看到 `/agent/windows/*`，能做吗？"

**大师**："这正是 Content Selector 压轴登场的场景——通过表达式（如 `path =^ "/agent/linux/"`）创建权限，然后绑定到相应的角色上。我们将在第 8 章详细讲，这里先建立概念。"

## 3. 项目实战

### 3.1 环境准备

- 已按第 2 章部署好 Nexus 实例
- curl

### 3.2 分步实战

#### 步骤一：创建 Raw 仓库套件

**目标**：创建 Raw hosted 仓库用于文件上传和分发。

```bash
# 1. 创建 Raw hosted 仓库（存放 Agent 安装包等文件）
curl -u admin:admin123 -X POST \
  "http://localhost:8081/service/rest/v1/repositories/raw/hosted" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "raw-agents",
    "online": true,
    "storage": {
      "blobStoreName": "default",
      "strictContentTypeValidation": false,
      "writePolicy": "ALLOW"
    }
  }'

# 2. 创建 Raw hosted 仓库（存放数据库脚本）
curl -u admin:admin123 -X POST \
  "http://localhost:8081/service/rest/v1/repositories/raw/hosted" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "raw-db-scripts",
    "online": true,
    "storage": {
      "blobStoreName": "default",
      "strictContentTypeValidation": false,
      "writePolicy": "ALLOW"
    }
  }'

# 3. 创建 Raw group 仓库（统一入口）
curl -u admin:admin123 -X POST \
  "http://localhost:8081/service/rest/v1/repositories/raw/group" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "raw-public",
    "online": true,
    "storage": {
      "blobStoreName": "default",
      "strictContentTypeValidation": false
    },
    "group": {
      "memberNames": ["raw-agents", "raw-db-scripts"]
    }
  }'
```

**运行结果**：三个 Raw 仓库创建成功，`raw-public` group 统一聚合了两个 hosted 仓库。

#### 步骤二：上传文件到 Raw 仓库

**目标**：用 curl 上传 Agent 安装包和数据库脚本。

```bash
# 创建测试文件
mkdir -p ~/nexus-raw-demo && cd ~/nexus-raw-demo

# 模拟 Agent 安装包
echo "#!/bin/bash\necho 'Agent v3.4.2 installed'" > agent-install.sh
chmod +x agent-install.sh

# 模拟数据库初始化脚本
echo "-- CloudWhale DB Init v2.1.0\nCREATE DATABASE IF NOT EXISTS cloudwhale;" > db-init.sql

# 模拟配置文件
echo "MAX_CONNECTIONS=100\nCACHE_TTL=3600" > config.properties

# 上传 Agent 脚本（路径含版本号）
curl -u admin:admin123 -X PUT \
  "http://localhost:8081/repository/raw-agents/agent/linux/amd64/3.4.2/agent-install.sh" \
  --data-binary @agent-install.sh

# 预期输出: 无（HTTP 201 Created，无 body）

# 上传 db 脚本
curl -u admin:admin123 -X PUT \
  "http://localhost:8081/repository/raw-db-scripts/schema/cloudwhale/v2.1.0/db-init.sql" \
  --data-binary @db-init.sql

# 上传配置文件
curl -u admin:admin123 -X PUT \
  "http://localhost:8081/repository/raw-agents/config/prod/config.properties" \
  --data-binary @config.properties
```

**验证上传**：

```bash
# 查看 raw-agents 仓库中的所有资产
curl -u admin:admin123 \
  "http://localhost:8081/service/rest/v1/search?repository=raw-agents" | jq '.items[] | {name: .name, path: .assets[0].path}'

# 预期输出（示例）：
# {"name": "agent-install.sh", "path": "agent/linux/amd64/3.4.2/agent-install.sh"}
# {"name": "config.properties", "path": "config/prod/config.properties"}
```

#### 步骤三：通过 group 仓库统一下载

**目标**：验证 group 仓库可以透明访问两个 hosted 仓库的文件。

```bash
# 通过 group 下载（路径与 hosted 中完全一致）
curl -u admin:admin123 -O \
  "http://localhost:8081/repository/raw-public/agent/linux/amd64/3.4.2/agent-install.sh"

# 通过 group 下载 db 脚本
curl -u admin:admin123 -O \
  "http://localhost:8081/repository/raw-public/schema/cloudwhale/v2.1.0/db-init.sql"

# 验证下载内容
cat agent-install.sh
# 预期输出:
# #!/bin/bash
# echo 'Agent v3.4.2 installed'
```

**运行结果**：通过 `raw-public` 这个 group 地址，可以透明下载分别存储在 `raw-agents` 和 `raw-db-scripts` 两个 hosted 仓库中的文件。

#### 步骤四：批量上传——用脚本实现版本化文件管理

**目标**：编写一个脚本，将本地某个目录按路径规范上传到 Raw 仓库。

创建 `upload-to-nexus.sh`：

```bash
#!/bin/bash
# 功能：将指定文件上传到 Nexus Raw 仓库
# 用法：./upload-to-nexus.sh <local-file> <nexus-path>

NEXUS_URL="${NEXUS_URL:-http://localhost:8081}"
NEXUS_USER="${NEXUS_USER:-admin}"
NEXUS_PASS="${NEXUS_PASS:-admin123}"
REPO="${REPO:-raw-agents}"

LOCAL_FILE="$1"
NEXUS_PATH="$2"

if [ -z "$LOCAL_FILE" ] || [ -z "$NEXUS_PATH" ]; then
    echo "用法: $0 <local-file> <nexus-path>"
    echo "示例: $0 ./agent.tar.gz agent/linux/amd64/3.4.3/agent.tar.gz"
    exit 1
fi

if [ ! -f "$LOCAL_FILE" ]; then
    echo "错误: 文件不存在 - $LOCAL_FILE"
    exit 1
fi

# 计算 SHA256
SHA256=$(sha256sum "$LOCAL_FILE" | awk '{print $1}')

echo "上传: $LOCAL_FILE -> $NEXUS_PATH"
HTTP_CODE=$(curl -u "${NEXUS_USER}:${NEXUS_PASS}" \
  -X PUT \
  -w "%{http_code}" \
  -o /dev/null \
  --data-binary "@${LOCAL_FILE}" \
  "${NEXUS_URL}/repository/${REPO}/${NEXUS_PATH}")

if [ "$HTTP_CODE" = "201" ]; then
    echo "上传成功 (HTTP $HTTP_CODE)"
    echo "SHA256: $SHA256"
else
    echo "上传失败 (HTTP $HTTP_CODE)"
    exit 1
fi
```

```bash
chmod +x upload-to-nexus.sh

# 使用脚本上传新版本
echo "#!/bin/bash\necho 'Agent v3.4.3 installed'" > agent-v343.sh
./upload-to-nexus.sh agent-v343.sh agent/linux/amd64/3.4.3/agent-install.sh
```

#### 步骤五：实现文件版本列表查询

**目标**：查询某个 Agent 的所有可用版本。

```bash
# 查询 raw-agents 仓库中 agent/linux/amd64/ 路径下的所有文件
curl -u admin:admin123 \
  "http://localhost:8081/service/rest/v1/search?repository=raw-agents&name=agent-install.sh" | \
  jq '.items[] | .assets[] | {path: .path, lastModified: .lastModified, sha256: .checksum.sha256}'

# 预期输出（示例）：
# {"path": "agent/linux/amd64/3.4.2/agent-install.sh", "lastModified": "2025-01-15T...", "sha256": "abc123..."}
# {"path": "agent/linux/amd64/3.4.3/agent-install.sh", "lastModified": "2025-01-16T...", "sha256": "def456..."}
```

**运行结果**：通过路径规范+搜索 API，实现了类似"制品版本列表"的效果。虽然 Raw 仓库不解析版本号，但标准化的路径结构使版本信息可查询。

#### 步骤六：大文件分片上传（可选）

```bash
# 对于超过 1GB 的文件，建议使用分片上传
# 生成一个 500MB 测试文件（可选，仅用于验证大文件上传能力）
# dd if=/dev/zero of=large-file.bin bs=1M count=500

# 上传大文件（curl 支持大文件流式上传）
curl -u admin:admin123 -X PUT \
  "http://localhost:8081/repository/raw-agents/models/ml-model/v1.0/model.bin" \
  --data-binary @large-file.bin \
  --limit-rate 10M \
  --connect-timeout 60 \
  --max-time 3600
```

### 3.3 常见坑点

| 坑点 | 现象 | 解决方法 |
|------|------|----------|
| PUT 被当作 POST | 上传文件内容被当作请求体参数 | 必须使用 `-X PUT`，`--data-binary @file` 而非 `-F`（-F 会变成 multipart） |
| 路径编码问题 | 带特殊字符的路径 404 | URL 中的路径需做 `encodeURIComponent`，如空格变成 `%20` |
| 大文件超时 | 上传超过 1GB 的文件失败 | 调大 `--max-time`，或在 Nginx 反向代理中设置 `proxy_read_timeout` |
| 写入策略冲突 | 覆盖已有文件被拒绝 | hosted 仓库 `writePolicy: ALLOW` 允许覆盖，`ALLOW_ONCE` 允许一次，`DENY` 禁止所有写入 |
| 路径尾部斜杠 | `/path/file` 和 `/path/file/` 行为不同 | Nexus 将前者视为文件资源，后者视为目录请求（返回该路径下的列表） |

## 4. 项目总结

### 4.1 优缺点对比

| 维度 | Nexus Raw 仓库 | NFS / 文件服务器 | 对象存储 (S3/MinIO) |
|------|---------------|-----------------|---------------------|
| 权限管理 | ✅ 基于角色的细粒度权限 | ❌ POSIX 权限（有限） | ✅ IAM / Bucket Policy |
| 版本管理 | ⚠️ 依赖路径规范 | ❌ 无 | ✅ 原生版本控制 |
| 审计日志 | ✅ 内置 Audit | ❌ 需自行采集 | ✅ CloudTrail / 审计日志 |
| 上传/下载方式 | ✅ REST API + curl + HTTPie | ⚠️ NFS 挂载 / scp / FTP | ✅ S3 API + CLI |
| 大文件支持 | ✅ 流式上传 | ✅ 文件系统级 | ✅ 原生支持 |
| CDN 加速 | ❌ 无 | ❌ 无 | ✅ CloudFront / CDN |
| 多格式统一 | ✅ 与 Maven/npm/Docker 同实例管理 | ❌ 各管各的 | ❌ 独立的存储层 |

### 4.2 适用场景

1. **运维自动化发包**：Agent、安装包、升级补丁按版本组织到 Raw 仓库，配合下载脚本实现自动化分发
2. **ML 模型文件管理**：`.h5`、`.onnx`、`.pkl` 等模型文件按 `模型名/版本/` 路径管理，支持回滚
3. **离线依赖缓存**：将 `node_modules.tar.gz`、`vendor.tar.gz` 等打包后的依赖上传到 Nexus，内网 CI 快速下载
4. **配置文件模板分发**：应用配置文件模板（nginx.conf、application.yml）集中管理、按环境版本化
5. **移动端测试包分发**：iOS `.ipa`、Android `.apk` 的测试版本统一存储和权限管理，替代蒲公英/fir.im

**不适用场景**：
1. PB 级非结构化数据长期存储——应使用对象存储（S3/MinIO），Nexus 的 BlobStore 不是设计用来存海量文件的
2. 需要 CDN 加速的全球分发——Nexus 没有 CDN 能力，应配合 CDN 或使用对象存储

### 4.3 注意事项

- **路径即约定**：Raw 仓库的强大与否取决于团队的路径规范。建议在 Wiki 中明确规范：`/<项目>/<平台>/<版本>/<文件名>`
- **文件大小上限**：Nexus 默认不限制上传大小，但 JVM 直接内存和 HTTP 超时是实际瓶颈。超 5GB 的文件建议评估对象存储方案
- **`strictContentTypeValidation`**：对 Raw 仓库应设为 `false`，因为二进制文件的 Content-Type 各异
- **安全性**：Raw 文件同样可被访问 URL 直接下载，确保权限配置正确，不要公开暴露 Raw 仓库地址

### 4.4 常见踩坑经验

**故障一：下载的二进制文件损坏**

运维团队用 `curl` 下载了 Raw 仓库中的 `.tar.gz` 文件，解压时报 `unexpected end of file`。排查发现：脚本中用了 `curl -o` 覆盖写入时进程被杀，残留了部分内容。解决：下载后对比 `sha256sum`，配合 `Content-Disposition` 响应头使用 `wget -c` 断点续传。

**故障二：覆盖上传后消费者下载到旧版本**

某运维上传了新版本 `agent/3.5.0/agent.tar.gz`，但部分节点的缓存代理（Squid）缓存了旧版本。解决：上传后使用 Query String（如 `?v=3.5.0`）破坏缓存，或使用不同的完整路径而非覆盖同一路径。

**故障三：路径末尾斜杠的"幽灵目录"**

团队成员用 `http://nexus:8081/repository/raw-agents/agent/` 访问，浏览器返回了一个奇怪的目录列表。根因：Nexus 对路径末尾带 `/` 的请求尝试"构建目录列表"，但在 Raw 仓库中并不总是可靠。解决：明确规范：访问文件时不带末尾斜杠，API 查询使用 `/service/rest/v1/search`。

### 4.5 思考题

1. 如果你需要用 Raw 仓库管理 500 个节点的配置文件，每个节点的配置只有主机名不同。你会设计怎样的路径结构和上传/下载脚本？（提示：模板化 + 变量替换）
2. Raw 仓库没有内置的版本比较机制（不像 Maven 能比 1.0 < 1.1）。如果需要在 Raw 仓库中实现"拉取最新版本的 Agent 安装包"，设计方案是什么？

（第6章思考题答案：1. group 仓库按成员顺序查找，`docker-hosted` 排在 `docker-hub-proxy` 前面时返回 hosted 中的版本。这意味着如果 hosted 中有 `library/nginx:1.25`，即使 proxy 缓存了不同的 `library/nginx:1.25`，客户端也只会拿到 hosted 的版本。这个特性可用于"内部镜像覆盖公共镜像"。2. `Delete unused manifests` 负责标记/删除没有任何 tag 引用的 manifest 和与之关联的 layer；`Compact BlobStore` 负责回收已删除 Blob 占用的物理磁盘空间。只删 tag 时，manifest 和 layer blob 仍然存在，只是没有 tag 指向它们——这称为"悬空 blob"，必须两个任务配合才能完整回收空间。）

### 4.6 推广计划提示

- **运维部门**：本章是运维团队的必修课。建议立即将 Agent 安装包、配置文件、数据库脚本迁移到 Raw 仓库，并制定路径命名规范
- **开发部门**：了解 Raw 仓库可用于分发非 Maven/npm 的二进制依赖（如 JNI `.so` 文件、编译好的 protobuf stub）
- **测试部门**：可以利用 Raw 仓库分发测试数据集、压测脚本和自动化测试工具包
