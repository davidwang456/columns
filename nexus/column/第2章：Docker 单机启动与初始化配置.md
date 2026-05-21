# 第2章：Docker 单机启动与初始化配置

## 1. 项目背景

云鲸科技的架构师大师拍板引入 Nexus 后，第一步就是搭环境。运维组的"炮哥"接到任务："下班前把 Nexus 跑起来，明天早上 Java 组要配 Maven 私服。"炮哥心想，一个 Java Web 应用而已，docker run 一下分分钟的事。结果 30 分钟后他在群里@大师："这破玩意儿起不来啊！端口占用了、数据目录权限不对、容器一重启数据全丢、admin 密码藏哪儿了、匿名访问关不掉……"

这几乎是每个 Nexus 新手的必经之路。表面上看，Nexus 只是一个 Java 应用，但它承载了企业所有依赖的可靠性和持久性——如果数据目录挂载错误，某天容器重启后所有缓存的制品灰飞烟灭，上百个开发同学同时报"依赖下载失败"，那个下午的 IM 小红点会让你终生难忘。

本章从零出发——不预设任何前置知识，用 Docker Compose 一键启动一个可登录、数据可持久化、安全门已上锁的 Nexus 实例。你将掌握"装好即能投入生产测试"级别的部署能力，并为后续所有章节的实战提供一个可复现的本地环境。

## 2. 项目设计

大师带着小胖和小白守在大屏幕前，准备远程指导炮哥部署。

**小胖**："大师，我在家用 docker run 起过 MySQL，不就三行命令嘛：pull、run、访问。Nexus 难道不一样？"

**炮哥**（对话框弹出来）："不一样！我 run 了，8081 端口在容器里是通的，但浏览器访问不了。还有，/nexus-data 目录挂载后，容器启动直接崩溃，日志里写什么 Permission denied。"

**大师**："小胖，你在家用的是 Linux 吧？云鲸的测试机是 CentOS 7，SELinux 开着的。炮哥遇到的第一个问题就是权限——Nexus 容器内以 `nexus` 用户（UID 200）运行，而宿主机挂载目录的属主是 root。Nexus 进程写不进 /nexus-data，直接崩溃。"

> **技术映射**：Docker 容器内进程的 UID 和宿主机文件的 UID 必须匹配，否则写操作失败。Nexus 官方镜像以 UID 200 运行。

**小白**："那为什么我的机器上就没事？"

**大师**："因为你用的是 macOS，Docker Desktop 在虚拟机里运行，文件权限层做了适配。Linux 原生 Docker 下这个坑人人踩过。"

**炮哥**："我 chown 200 /nexus-data 后能启动了，但还有个问题——admin 密码到底在哪里？官网文档说在 `/nexus-data/admin.password`，我进去找了半天找不到。"

**大师**："炮哥你把容器停了重新启动了对吧？admin.password 文件只在 Nexus 首次初始化时生成，并且首次登录后 Nexus 会自动删除它。所以密码文件是一次性的——用完就没了。"

**小胖**："这设计太反人类了吧？万一运维忘了记密码怎么办？"

**大师**："这叫安全设计。和酒店房卡一样——入住时前台给你一张卡，你进门后卡还在，但前台系统已经把你标记为已入住。Nexus 也一样：密码在首次启动时随机生成，写到文件里给你看一次，你登录后就让你立刻改掉。如果丢了密码怎么办？有应急方案——删掉数据库目录下的 `config` 和 `security` 表，可以重新初始化 admin 密码，但这也意味着所有用户和权限配置都会丢失。"

> **技术映射**：admin.password 是一次性随机密码文件，首次登录后 Nexus 会自动删除。生产环境必须立即修改并妥善保存。

**小白**："那 Docker Compose 和纯 docker run 有什么区别？炮哥为什么不用 Compose？"

**大师**："docker run 适合快速验证，但生产环境你需要文档化部署参数。Compose 把端口、挂载、重启策略、内存限制、健康检查都写进 YAML，你的部署方案就是一份可版本控制、可审批、可复现的文档。新人入职时，他不需要知道 UID 200 这个坑——git clone 仓库，docker compose up -d，结束。"

**小胖**："我懂了！就像食堂的标准化菜谱——新手照着做也能八九不离十，比老师傅口口相传靠谱。"

> **技术映射**：Docker Compose 是基础设施即代码（IaC）的最小实践——把运维经验固化为可复现的配置。

**炮哥**："好，那我用 Compose。还有个问题——容器重新创建后数据会丢吗？"

**大师**："Docker 容器是无状态的，说死就死。数据持久化靠 volume 或 bind mount。Compose 里用 bind mount 把宿主机的 `./nexus-data` 目录挂载到容器的 `/nexus-data`。只要这个目录在，就算你把容器删了重建一百次，数据都还在。但切记——这个目录必须定期备份，整台机器挂了，数据还是会丢。"

**炮哥**："那 JVM 内存怎么配？容器默认 512MB 够吗？"

**大师**："Nexus 默认 JVM 最大堆 2703MB，Docker 容器必须给够。`-Xmx` 和容器的 `--memory` 都要显式设置。JVM 堆太小会频繁 Full GC 导致服务不可用；堆太大又浪费宿主机内存。经验值：单机版 Nexus 内存给 2~4GB，JVM 堆设物理内存的 70% 左右。"

## 3. 项目实战

### 3.1 环境准备

| 依赖 | 版本要求 | 用途 |
|------|---------|------|
| Docker | 20.10+ | 容器运行时 |
| Docker Compose | v2.0+ | 编排工具 |
| curl | 任意版本 | API 测试 |
| jq | 1.6+ | JSON 格式化（可选） |

硬件建议：2 核 CPU、4GB 可用内存、20GB 可用磁盘。

### 3.2 分步实战

#### 步骤一：创建项目目录和 Compose 文件

**目标**：建立可复现的 Nexus 部署目录结构。

```bash
# 在工作目录下创建 nexus 部署目录
mkdir -p ~/nexus-local/data
mkdir -p ~/nexus-local/etc
cd ~/nexus-local
```

创建 `docker-compose.yml`：

```yaml
version: "3.8"
services:
  nexus:
    image: sonatype/nexus3:latest
    container_name: nexus
    restart: unless-stopped
    ports:
      - "8081:8081"          # Web UI 和 REST API
      # 如需 Docker Registry 功能，添加：
      # - "5000:5000"        # Docker hosted (HTTP)
      # - "5001:5001"        # Docker group (HTTP)
    volumes:
      - ./data:/nexus-data   # 数据持久化（制品、配置、数据库）
    environment:
      # 安装时可选：指定 JVM 堆内存大小
      - INSTALL4J_ADD_VM_PARAMS=-Xms1024m -Xmx2048m -XX:MaxDirectMemorySize=512m
    healthcheck:
      test: ["CMD-SHELL", "curl -f http://localhost:8081/service/rest/v1/status || exit 1"]
      interval: 30s
      timeout: 10s
      retries: 10
      start_period: 120s
    ulimits:
      nofile:
        soft: 65536
        hard: 65536
```

#### 步骤二：处理数据目录权限

**目标**：解决 Linux 原生 Docker 下最常见的权限问题。

```bash
# 检查当前宿主机用户
whoami
# 假设输出: david

# 方法一：直接设置目录属主为 UID 200（推荐）
# Nexus 容器内以 UID 200 (nexus) 运行
sudo chown -R 200:200 ~/nexus-local/data

# 方法二：放宽目录权限（不推荐用于生产）
# chmod -R 777 ~/nexus-local/data

# 验证权限
ls -ld ~/nexus-local/data
# 预期输出: drwxr-xr-x 2 200 200 4096 ...
```

#### 步骤三：启动 Nexus 并观察日志

**目标**：启动容器，确认服务完全就绪。

```bash
# 启动容器（-d 后台运行）
docker compose up -d

# 预期输出：
# [+] Running 1/1
#  ✔ Container nexus  Started

# 查看启动日志（Nexus 初始化可能需要 2~5 分钟）
docker compose logs -f nexus

# 关键日志行（表示启动成功）：
# -------------------------------------------------
# Started Sonatype Nexus OSS 3.xx.x-xx
# -------------------------------------------------
```

**运行结果说明**：
- 启动过程中会看到 `Initializing`, `Configuring`, `Starting` 等阶段
- 首次启动时会自动创建 OrientDB 数据库到 `/nexus-data/db`
- 如果看到 `Permission denied` 报错，回到步骤二检查目录权限
- 健康检查通过后，Nexus 即可访问

#### 步骤四：获取 admin 密码并登录

**目标**：读取一次性初始密码，完成管理员首次登录。

```bash
# 读取 admin 初始密码文件
docker compose exec nexus cat /nexus-data/admin.password

# 预期输出（示例）：
# a1b2c3d4-e5f6-7890-abcd-ef1234567890

# 在浏览器中打开 http://localhost:8081
# 用户名: admin
# 密码: 上述 UUID 字符串
```

**执行结果**：浏览器跳转到 Setup 向导页面。
1. 系统要求立刻修改 admin 密码（新密码至少 8 位，含字母和数字）
2. 设置是否**允许匿名访问**：选择"禁用匿名访问"（生产环境强烈建议）
3. 完成初始化，进入 Nexus 管理控制台首页

#### 步骤五：验证基础配置

**目标**：确认核心功能可用。

```bash
# 1. 验证 Nexus 状态 API（无需认证）
curl -s http://localhost:8081/service/rest/v1/status | jq .

# 预期输出：
# {
#   "status": "running",
#   "frozen": false,
#   "initialized": true,
#   "nodeId": "xxx",
#   "version": "3.xx.x"
# }

# 2. 验证认证（使用新密码替换 <your-new-password>）
curl -u admin:<your-new-password> http://localhost:8081/service/rest/v1/status/writable

# 预期输出：
# {
#   "writable": true
# }
```

#### 步骤六：编写一键初始化脚本

**目标**：将以上步骤固化为可重复执行的脚本。

创建 `init-nexus.sh`：

```bash
#!/bin/bash
set -e

NEXUS_HOME="${NEXUS_HOME:-$HOME/nexus-local}"
NEXUS_URL="${NEXUS_URL:-http://localhost:8081}"
ADMIN_USER="admin"
ADMIN_PASSWORD=""  # 从文件读取或环境变量获取

echo "=== Nexus 环境初始化 ==="

# 1. 准备数据目录
echo "[1/4] 准备数据目录..."
mkdir -p "$NEXUS_HOME/data"
chown -R 200:200 "$NEXUS_HOME/data" 2>/dev/null || echo "  注意：权限修改可能需 sudo"

# 2. 启动容器
echo "[2/4] 启动 Nexus 容器..."
cd "$NEXUS_HOME"
docker compose up -d

# 3. 等待服务就绪
echo "[3/4] 等待 Nexus 就绪（最多 5 分钟）..."
for i in $(seq 1 60); do
    if curl -sf "$NEXUS_URL/service/rest/v1/status" > /dev/null 2>&1; then
        echo "  Nexus 已就绪！"
        break
    fi
    echo "  等待中... ($i/60)"
    sleep 5
done

# 4. 获取初始密码（仅在首次初始化时存在）
echo "[4/4] 获取 admin 初始密码..."
if PASS=$(docker compose exec -T nexus cat /nexus-data/admin.password 2>/dev/null); then
    echo "  初始密码: $PASS"
    echo "  请立即登录 $NEXUS_URL 修改密码并禁用匿名访问。"
else
    echo "  admin.password 已消失（已初始化过），使用已有密码登录。"
fi

echo "=== 初始化完成 ==="
```

```bash
# 赋予执行权限并运行
chmod +x init-nexus.sh
./init-nexus.sh
```

### 3.3 常见坑点及解决

| 坑点 | 现象 | 根因 | 解决 |
|------|------|------|------|
| 权限拒绝 | 容器日志出现 `Permission denied: /nexus-data/...` | Linux 下挂载目录属主不是 UID 200 | `chown -R 200:200 ./data` |
| 端口冲突 | `Error starting userland proxy: Bind for 0.0.0.0:8081 failed: port is already allocated` | 8081 已被占用（如 Jenkins、Artifactory） | 修改 ports 映射为 `"9081:8081"` |
| 内存不足 | JVM 无法启动，日志出现 `Could not reserve enough space` | Docker 容器内存限制 < JVM 最大堆 | 在 Compose 中添加 `mem_limit: 4g` 或调低 `-Xmx` |
| 磁盘不足 | 启动缓慢、最终失败 | `/nexus-data` 所在磁盘空间不足（Nexus 需要约 100MB+ 初始空间） | `df -h` 检查磁盘，确保至少 5GB 可用 |
| 数据丢失 | 容器重建后所有制品/配置消失 | 未正确挂载 volume | 检查 Compose 中 volumes 配置是否正确绑定到宿主机目录 |
| 启动超时 | 健康检查一直失败 | 防火墙/安全组拦截、JVM 参数不当 | 确认 8081 端口可访问，检查 `docker compose logs` 中的错误 |

## 4. 项目总结

### 4.1 优点与缺点

| 维度 | Docker Compose 部署 | 裸机部署（解压 tar.gz） | K8s Helm 部署 |
|------|---------------------|------------------------|--------------|
| 部署速度 | ✅ 3 分钟就绪 | ⚠️ 需安装 JDK、配置服务 | ⚠️ 需编写 values.yaml |
| 环境一致性 | ✅ Compose YAML 即文档 | ❌ 依赖运维手册，易遗漏 | ✅ Helm Chart 即文档 |
| 数据持久化 | ✅ bind mount 简单直观 | ✅ 直接写在本地目录 | ⚠️ 依赖 PVC 和 StorageClass |
| 升级与回滚 | ✅ 改 image tag，重建容器 | ⚠️ 手动替换安装包 | ✅ Helm rollback |
| 资源隔离 | ⚠️ 单 Docker daemon 共享 | ❌ 无隔离 | ✅ Pod 级 cgroup 隔离 |
| 适用规模 | ✅ 单机、团队级（<100人） | ✅ 单机 | ✅ 企业级、多租户 |
| 学习成本 | ✅ 低（会 docker compose 即可） | ⚠️ 中等 | ⚠️ 需掌握 K8s 基础 |

### 4.2 适用场景

1. **个人开发环境**：本地调试 Nexus API、验证插件开发，Docker Compose 足以
2. **团队私服起点**：30 人以下小团队，一台 4C8G 云主机跑 Nexus + GitLab Runner
3. **离线演示环境**：笔记本上跑 Nexus 给客户展示制品治理方案
4. **CI/CD 测试桩**：在流水线中用 `docker compose up` 临时起 Nexus 用于集成测试
5. **学习与培训**：新人上手、团队内训的标准化实验环境

**不适用场景**：
1. 300 人以上大型研发组织——建议规划 K8s 部署 + 分布式对象存储
2. 需要 99.95% 以上 SLA 的生产环境——OSS 版不支持 HA，需 PRO 版或配合外部负载方案

### 4.3 注意事项

- `sonatype/nexus3:latest` 标签可能拉取大版本变更（如 3.x → 3.y），生产环境务必固定版本号如 `sonatype/nexus3:3.70.0`
- 升级前必须备份 `/nexus-data` 整个目录，升级路径可能不可逆
- `INSTALL4J_ADD_VM_PARAMS` 中 `-XX:MaxDirectMemorySize` 建议设为堆大小的 1/4~1/2，Nexus 用直接内存处理大文件 IO
- 匿名访问关闭后，Maven/npm/Docker 客户端需配置认证信息，否则全部请求返回 401

### 4.4 常见踩坑经验

**故障一：数据目录挂载被 SELinux 拦截**

某金融行业团队在 RHEL 8 上部署，容器启动后 `/nexus-data` 始终为空。根因：SELinux Enforcing 模式禁止容器写入 bind mount 目录。解决：`chcon -Rt svirt_sandbox_file_t ./data` 或临时 `setenforce 0`（不推荐生产）。建议：Compose 中 volumes 添加 `:Z` 后缀自动重新标记。

**故障二：内存限制导致 OOM Kill**

开发环境 Nexus 频繁重启，`docker inspect` 发现 `"OOMKilled": true`。根因：Compose 未设 `mem_limit`，JVM 堆 2.7G + 直接内存 + 操作系统开销超过宿主机可用内存。解决：`deploy.resources.limits.memory: 4G`，适当调低 `-Xmx`。

**故障三：admin.password 被安全扫描工具告警**

某公司的安全团队扫描发现 `/nexus-data/admin.password` 文件包含明文密码，要求整改。根因：保留密码文件用于自动化获取密码。解决：改用 API 首次登录后立即修改密码，密码管理统一接入公司的 Vault 方案，脚本中通过环境变量传参而非硬编码。

### 4.5 思考题

1. 如果需要在同一台机器上跑两个 Nexus 实例（一个 dev 环境、一个 test 环境），Docker 层面需要做哪些隔离？（提示：端口、volume、容器名）
2. Nexus 的健康检查端点 `/service/rest/v1/status` 返回 `"frozen": true` 时意味着什么？什么操作可能触发冻状态？

（第1章思考题答案：1. group 仓库按成员排列顺序依次查找，第一个命中即返回；因此客户端会下载到 group 成员列表中排在最前面的那个 hosted 仓库中的版本。2. Harbor 专为 Docker/OCI Registry 设计，具备镜像扫描、签名、复制等原生能力；Nexus 是多格式制品仓库，适合 Java/npm/Docker/Raw 必须统一管理的场景，但 Docker 专项能力弱于 Harbor。若团队只需要 Docker 仓库，Harbor 更合适；若需要统一多种格式制品，Nexus 是更好的选择。）

### 4.6 推广计划提示

- **开发部门**：按本章 Compose 文件在本地启动一个 Nexus，验证可以用 `curl` 调通 API
- **测试部门**：重点掌握数据目录的备份和恢复操作，为后续测试环境的制品管理打基础
- **运维部门**：将 Compose 模板转化为生产环境的部署方案（K8s/裸机），补充监控和告警
