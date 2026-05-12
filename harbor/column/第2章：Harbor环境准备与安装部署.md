# 第2章：Harbor 环境准备与安装部署

## 1 项目背景

2024 年 4 月，运维工程师王浩接到一项紧急任务——"凌云计划"投资人 Demo 演示定在 4 月 18 日（周三），而今天是 4 月 10 日（周二）。CTO 在周会上当众点名："王浩，这周三下班前 Harbor 要装好，下周一我要看到完整的镜像 Push/Pull 流程跑通，这是投资人 Demo 中最基础的一环。"

王浩之前在 Docker Hub 上 push/pull 过镜像，但对 Harbor 完全陌生。他打开官方文档，面对"在线安装""离线安装""Helm 安装"三种模式，以及 `harbor.yml` 中密密麻麻的参数，陷入了深深的技术焦虑。

**痛点一：三种安装方式的选型困惑。** Harbor 官方提供在线安装（联网从 Docker Hub/GHCR 拉镜像）、离线安装（本地 tar 包包含全部依赖镜像）和 Helm 安装（以 Kubernetes 服务形式部署）三种模式。公司的测试环境（10.0.1.0/24 网段）虽然能通过 HTTP 代理联网，但带宽仅 100Mbps 且出口限速；而预发布环境（10.0.2.0/24）和生产环境（10.0.3.0/24）是纯物理隔离内网，完全无互联网访问。王浩纠结的核心问题是：在线安装和离线安装的配置文件 `harbor.yml` 是否通用？如果在测试环境用在线安装做好了配置，能不能直接把同一份配置文件拿到生产环境用离线包启动？

**痛点二：`harbor.yml` 的 50+ 参数地狱。** 打开 `harbor.yml.tmpl`，王浩看到从 `hostname` 到 `log.rotate_size` 一共 53 个可配置参数。哪些是必填项、哪些可以用默认值、哪些一改就会导致整个 Harbor 无法启动？更麻烦的是，不同版本的 `harbor.yml` 参数结构不同——王浩在网上找了一份 Harbor v1.10 的配置教程，照着改了参数，结果 `./prepare` 直接报错：`check_nginx_config failed`——但日志并没有提示是哪一行配置有问题。王浩花了整整一个下午逐行对比，才发现 v1.10 和 v2.12 在 HTTPS 证书路径、数据库连接参数、日志轮转配置等 6 个位置的结构完全不同。

**痛点三：依赖组件的版本地狱与兼容性冲突。** Harbor v2.12 的依赖清单如下：Docker 20.10+、Docker Compose v2、PostgreSQL 15、Redis 7。但公司测试机装的是 CentOS 7.6（内核 3.10），默认 yum 源的 Docker 版本是 1.13（已停止维护超过 5 年）。王浩尝试用 `curl -sSL https://get.docker.com/ | sh` 升级 Docker，结果触发了 SELinux 冲突（容器无法写入挂载卷）、firewalld 规则丢失（Nginx 端口不通）、以及 OverlayFS 内核模块加载失败等一系列连锁问题。升级 Docker 的过程中还意外升级了 glibc，导致一台机器的 SSH 服务启动失败——王浩不得不通过 KVM 远程控制台重新连接修复。

**痛点四：生产环境高可用的隐性需求。** 王浩最初的想法很简单——在预发布和生产环境各装一台 Harbor。但安全组长在架构评审时提出："如果生产 Harbor 挂了，K8s 集群的 23 个 Node 都拉不到镜像，所有新 Pod 调度全部阻塞。单点故障的影响面有多大你算过吗？"架构组给出的要求是：至少需要 Harbor 的 99.9% 可用性（月停机 < 43 分钟）。这意味着需要考虑 PostgreSQL 主从复制、Registry 镜像存储的冗余备份、以及 Harbor Core 的多实例 + 负载均衡。但这显然超出了单机 `docker compose up -d` 的能力范围。

**痛点五：镜像存储增长的无感知膨胀。** 王浩估算当前 47 个微服务的镜像总大小约 50GB（去重后），分配了 200GB 磁盘给 `/data/harbor`。但在非正式配置中他漏了一个参数——日志轮转的 `rotate_size` 上限。Harbor 默认每个容器日志文件保留 50 个（`log.rotate_count: 50`），每个最大 200MB——仅日志就可能占用 10GB。加上 Trivy 的 CVE 数据库文件（每次更新约 500MB）和 PostgreSQL 的 WAL 日志（高写入场景下可能 20GB+），实际存储消耗远超估计。王浩在第 47 天被监控告警叫醒——`/data` 分区使用率超过 95%。

本章将从零开始，手把手带你完成 Harbor 三种安装模式的完整部署流程，深度解析 `harbor.yml` 中每个关键参数的底层含义和调优策略，并给出生产环境的完整 Checklist 和自动化脚本。

---

## 2 项目设计——剧本式交锋对话

**场景：4 月 10 日 14:30，王浩顶着黑眼圈找到大师的工位。桌面上摆着三份文档：《Harbor Installation Guide v2.12》、《harbor.yml Reference》、《Troubleshooting Common Issues》。王浩的终端上，`./prepare` 报了第三次 `check_nginx_config failed`。**

**小胖**（嘴里嚼着麻辣豆干，探头看王浩的屏幕）："浩哥，你这是在搞啥？装个 Harbor 搞了两天了？我昨天看 Docker Hub 上有人分享——一行 `docker run -d -p 5000:5000 registry:2` 就起来了啊！Harbor 不会也是 `docker compose up -d` 一行搞定吧？你看这文档上不就这么写的吗？"

**大师**（放下手中的枸杞茶，走过来站到王浩身后）："小胖，你这个想法恰好是 Harbor 新手最常见的误区。Docker Registry 一行命令跑起来，是因为它只做两件事——在文件系统上存镜像 blob 和存 manifest JSON。它没有用户体系、没有 Web 界面、没有漏洞扫描、没有复制同步、没有垃圾回收。Harbor 呢？它光是 Docker 容器就启动了 9 个——你把你自己想象成 Harbor，你得先开个门卫（Nginx），再支个前台（Portal），调度中心（Core）、后厨（Registry）、账房（PostgreSQL）、小黑板（Redis）、外卖小哥（JobService）、食品安全员（Trivy）——哪个都不能少。一行命令能把这个整套班子都支起来吗？"

**小胖**（手里的豆干停在半空中）："9 个容器？我感觉我的小破笔记本说不定就跑不动……那在线装和离线装到底有啥本质区别？不就是在线等下载、离线自己带包吗？"

**小白**（从对面工位转过身来，屏幕上正滚动着 Harbor 的 GitHub Issues）："我昨天试了在线安装——在测试环境执行 `./install.sh` 后，卡在 'Pulling harbor-core:v2.12.0' 这一步等了 43 分钟。我查了下公司网络——出口走的是电信商务宽带 100M，但 Docker Hub 的 CDN 节点在海外，实际拉镜像的稳定速度只有 280KB/s。8 个镜像一共 2.1GB，按这个速度要拉快两个小时。更恶心的是，GitHub Issues #19672 提到——`docker compose pull` 在某些网络环境下没有超时重试机制，如果某个镜像层传输中断了，它会一直挂着不重试也不报错。离线包是不是直接从 tar 导入，就绕过了拉取慢的问题？"

**大师**："小白抓到关键点了。在线安装和离线安装的选择，本质上不是'个人偏好'问题，而是'网络拓扑和镜像分发的工程问题'。我给你详细拆一下——"

"**在线安装（harbor-online-installer）**：就像去 24 小时便利店买便当——包装盒本身只有 10KB（安装脚本和配置文件），你到了便利店，店员帮你从冷冻柜（Docker Hub/GHCR）拿出来现场微波加热。好处是包装轻、版本灵活——你想换哪个版本的镜像就换哪个；坏处是如果你家离便利店特别远（网络带宽低）、或者便利店在装修（Docker Hub 限速/被墙），你就得空着肚子等。在线安装包只有 ~10KB，安装时执行 `docker compose pull` 从远端拉取 9 个镜像（合计约 2.1GB，压缩后 ~750MB）。

"**离线安装（harbor-offline-installer）**：就像你提前在沃尔玛买好了一周的微波炉便当——一个 750MB 的大包裹，里面有所有需要的容器镜像的 tar 文件。你只需要在外网环境下载一次这个包裹，之后无论多少个内网环境、无论网络多差，解压→导入→启动就完了。唯一的缺点是：如果你想升级到新版本（比如 v2.12.1），你需要重新下载一个 750MB 的包裹，不能只更新单个组件。"

"**Helm 安装**：就像你在商场里租了一个档口（Kubernetes 集群），Harbor 以 Pod 的形式运行在集群里，而不是单独的 Docker 容器。Helm Chart 本身 ~5KB，通过 `helm install` 自动拉取镜像并创建 Deployment、Service、Ingress、PVC 等 K8s 资源。好处是可以利用 K8s 的原生能力——HPA 自动扩缩容、Node 故障自动迁移、Rolling Update 平滑升级；坏处是你必须先有一个 K8s 集群，而且需要理解 PV/PVC、StorageClass、Ingress Controller 等 K8s 概念。"

**技术映射**：在线安装通过 `install.sh` 调用 `docker compose pull && docker compose up -d`；离线安装包的 `install.sh` 首先执行 `docker load -i harbor.v2.12.0.tar.gz` 导入所有镜像到本地 Docker 镜像库，然后执行 `docker compose up -d`；Helm 安装走 `helm install harbor/harbor -f values.yaml`，底层创建 K8s 原生资源。

**小胖**（嚼着豆干若有所思）："那是不是说——我可以在家里的外网电脑上下载离线包，然后拷到公司内网用？这样就不用等公司那个破网了？"

**大师**："完全正确。这也是离线安装最典型的场景——在任意一台可联网的电脑上下载 `harbor-offline-installer-v2.12.0.tgz`，通过 U 盘、内网文件服务器、或者堡垒机的文件传输功能（如果安全策略允许），拷贝到目标服务器上。解压后就等于在本地有了所有依赖镜像，无需任何外网访问。注意一点：离线包是针对特定 Harbor 版本的，v2.12 的离线包不能用于安装 v2.11。"

**小白**（把 GitHub Issues 页面放大）："大师，我看到了很多 `harbor.yml` 相关的报错 issue——'check_nginx_config failed''harbor-core restart loop''push 401 after changing https config'。你能不能给一个'必改参数 vs 可选参数 vs 雷区参数'的分类？我想知道哪些参数改错了会导致整个 Harbor 炸掉。"

**大师**（拉过王浩桌上的便签纸，开始画）："好，我把 harbor.yml 的参数分成四类——"

"**第一类——必改参数（不改装不上或不安全）**："
1. "`hostname`：必须换成你服务器的实际 IP 或 DNS 可解析的域名。默认值 `reg.mydomain.com` 不改的话，Docker 客户端根本无法访问。"
2. "`harbor_admin_password`：安装后立即生效，不要留 `Harbor12345`。生产环境要求至少 16 位，含大小写字母、数字和特殊字符。"
3. "`data_volume`：换成你的大容量数据目录。如果是云服务器，建议单独挂载一块 500GB+ 的云盘到这个路径。"
4. "`https` 配置（生产环境）：`port`、`certificate`、`private_key` 三个参数必须同时配置且路径有效，否则 Nginx 容器起不来。"

"**第二类——建议按需修改参数**："
- "`database.max_open_conns`：默认 900，如果 Harbor 并发用户 >50，建议调大为 CPU 核数 × 150。"
- "`jobservice.max_job_workers`：默认 10，控制同时执行的异步任务数（GC、扫描、复制）。如果你有 20 个项目都开了自动扫描，建议调到 20。"
- "`log.level`：默认 info，排障时可临时改为 debug，但生产环境保持 info——debug 日志量很大，一天可能产生 30GB+。"

"**第三类——雷区参数（改错一个全局炸）**："

"**雷区一**：`https.port` 配置了 443，但 `certificate` 和 `private_key` 路径为空或路径不存在。`./prepare` 不会报错，但生成出来的 Nginx 配置引用了不存在的证书文件，Nginx 启动失败，进而整个 Harbor 的流量入口瘫痪。"

"**雷区二**：`external_url` 和 `hostname` 不一致。`external_url` 用于 Portal 前端生成链接（如邮件通知里的 URL），而 `hostname` 会用于 Core 签发 Token 的 audience/realm 字段。Docker 客户端用 `external_url` 登录，但后端签发的 Token 里的 realm 是 `hostname`——两个地址不匹配，Docker 客户端收到 Token 后会报 401 Unauthorized。我在 GitHub Issues 上统计过，'hostname/external_url mismatch' 是 Harbor 安装问题中排名第二的高频问题——仅次于证书配置问题。"

"**雷区三**：`data_volume` 指向一个小的系统分区（如 `/` 根分区）。Harbor 运行 6 个月后镜像层和数据库文件可能膨胀到 200GB+。如果 `/` 分区被写满，不仅 Harbor 挂掉，整个操作系统的日志写入、SSH 登录、systemd 管理都可能受影响。"

"**雷区四**：修改了 `database.password`，但直接重启——PostgreSQL 容器内的用户密码还是旧的，Core 连接 DB 会持续失败。你需要同步改 PostgreSQL 中的密码：`docker exec -it harbor-db psql -U postgres -c "ALTER ROLE postgres WITH PASSWORD 'new_password';"`，然后再重启。"

**小胖**（挠了挠头）："这么多雷区……那有没有什么'黄金配置模板'能直接抄？我就想先装起来跑通，后面再慢慢调。"

**大师**："当然有。给你一个'最小可跑 + 生产就绪'的对比模板。"

"**测试/学习环境最小配置模板**（只求跑通、不求完美）："
```yaml
hostname: 192.168.1.100       # 改你的 IP
http:
  port: 8080                   # 如果 80 被占用
harbor_admin_password: Test12345
data_volume: /data
```

"**生产环境推荐配置模板**（考虑了安全和运维）："
```yaml
hostname: harbor.yourcompany.com
https:
  port: 443
  certificate: /opt/harbor/certs/harbor.crt
  private_key: /opt/harbor/certs/harbor.key
harbor_admin_password: Prod!H@rbor2024$
database:
  password: ProdDB!P@ss2024
  max_idle_conns: 100
  max_open_conns: 900
data_volume: /data/harbor        # 建议独立挂载 500GB+ SSD
log:
  level: info
  rotate_count: 30
  rotate_size: 200M
jobservice:
  max_job_workers: 10
```

**小白**（认真记完笔记后抬头）："大师，安装完之后，怎么验证 Harbor 是真的'装好了'而不是'看起来装好了'？我担心出现那种——容器都是 running 状态，但实际 push 镜像会 500 错误的情况。"

**大师**："这个问题问得好。验证分四个层级——**

"**第一层——容器状态检查**：`docker compose ps` 或 `docker ps --filter name=harbor`，看所有容器 STATUS 是否是 `(healthy)`。如果有容器是 `(unhealthy)` 或 `(health: starting)` 持续超过 2 分钟，说明有问题。"

"**第二层——API 健康检查**：`curl -k -u admin:password https://harbor.yourcompany.com/api/v2.0/health`，返回 `{"status":"healthy"}` 且所有 `components` 的 status 全是 `healthy`。"

"**第三层——Docker 客户端端到端测试**：执行完整的 `docker login → docker tag → docker push → docker pull` 流程。如果 push 返回 `500 Internal Server Error`，大概率是 Registry 容器的共享存储权限不对——`/data/registry` 的 owner 必须是 10000:10000。"

"**第四层——Web Portal 页面加载测试**：浏览器访问 `https://harbor.yourcompany.com`，用 admin 账号登录，确认所有菜单页面加载正常、Dashboard 数据显示正确。"

"这四层全部通过，才能说 Harbor 安装成功。"

**小胖**（突然举手）："等一下等一下！我突然想到一个问题——如果我用离线包在测试环境装好了 Harbor，然后要迁移到生产环境，能不能直接把整个 `/opt/harbor` 和 `/data/harbor` 目录打包过去？"

**大师**："理论上可以，但有一个致命坑——PostgreSQL 的数据文件与机器的内核版本、PostgreSQL 子版本强绑定。从 CentOS 7.6（内核 3.10）迁移到 Rocky 9（内核 5.14），直接拷贝 PG 数据文件可能导致数据库无法启动。正确的做法是：在源机器上执行 `pg_dump -U postgres > harbor_metadata.sql` 导出元数据，在新机器上先 `docker compose up -d` 初始化空数据库，再 `psql -U postgres < harbor_metadata.sql` 导入。"

**技术映射**：Harbor 的 PostgreSQL 容器基于 Photon OS，内置 PostgreSQL 15。PostgreSQL 的数据文件格式在不同操作系统的 glibc 版本、内核页面大小（4KB vs 64KB）上可能不兼容。迁移方案推荐用 `pg_dump` 逻辑备份而非物理文件拷贝。

**小白**："最后一个问题——Helm 安装比 Docker Compose 安装多了什么能力？少了什么限制？我们的 K8s 集群已经在跑了，但不确定 Harbor 要不要也放进集群里。"

**大师**："好问题，这牵涉到'基础设施依赖'的问题。Helm 安装的优势：①Harbor 享受 K8s 的自愈——Pod 挂了自动拉起来；②HPA 自动扩缩容——高峰期 Registry 实例可以从 1 个扩到 3 个；③Ingress 统一管理——不需要单独维护 Nginx 容器的 TLS 证书。"

"但 Helm 安装也有代价：①如果你的 K8s 集群只有一个（生产集群），而 Harbor 本身是 K8s 的核心依赖——K8s 的 Node 启动时需要从 Harbor 拉 pause/sandbox 等基础镜像。如果 Harbor 也跑在 K8s 里，就形成了循环依赖——K8s 需要 Harbor 才能启动 Node，但 Harbor 需要 K8s 才能运行。解决方案是……"

**小胖**（抢答）："在集群外面单独跑个小的 Docker Registry 存放 K8s 基础镜像！"

**大师**（赞许地点头）："小胖这次说对了。或者你可以选择 Hybrid 架构——核心的 Registry 组件跑在 K8s 外（用独立的 Docker Compose 管理），其他管理组件（Core、Portal、JobService）跑在 K8s 内。不过这个架构复杂度较高，一般推荐方案是：生产 Harbor 跑在独立 VM 上（Docker Compose 管理），不与生产 K8s 集群共存。"

---

## 3 项目实战

### 3.1 环境准备

| 组件 | 版本要求 | 验证命令 | 说明 |
|------|---------|---------|------|
| Docker Engine | 20.10.0+ | `docker --version` | 容器运行时，低于此版本不支持 Compose v2 |
| Docker Compose | v2.10.0+ | `docker compose version` | 注意命令格式：`docker compose`（有空格） |
| Harbor 安装包 | v2.12.0 | `ls /opt/harbor/harbor.yml.tmpl` | 本章以离线包为主演示 |
| 操作系统 | Ubuntu 22.04 / Rocky 9 / CentOS 7.9 | `cat /etc/os-release` | 内核版本需 >= 3.10（OverlayFS 支持） |
| CPU | 4 核+ | `nproc` | GC/扫描是 CPU 密集型操作 |
| 内存 | 8 GB+ | `free -h` | Trivy 扫描内存峰值可达 3GB |
| 磁盘 | 80 GB+（推荐 500GB+ SSD） | `df -h /data` | 镜像层快速增长，建议独立挂载 |
| 网络 | 端口 80/443 可用 | `ss -tlnp \| grep -E ':80\|:443'` | 检查是否被 Nginx/Apache 占用 |
| OpenSSL | 1.1.1+ | `openssl version` | 用于自签证书生成（测试环境） |

### 3.2 安装前置依赖检查

> **步骤零**：在安装 Harbor 之前，先检查并修复系统环境中的所有潜在问题。

```bash
#!/bin/bash
# =============================================
# Harbor 安装环境预检脚本 (pre-flight check)
# =============================================

echo "=== 1. 操作系统版本检查 ==="
cat /etc/os-release | head -3

echo ""
echo "=== 2. 内核版本检查（需要 >= 3.10 支持 OverlayFS）==="
uname -r

echo ""
echo "=== 3. Docker 版本检查 ==="
docker --version
# 如果 version < 20.10，执行：
# curl -sSL https://get.docker.com/ | sh

echo ""
echo "=== 4. Docker Compose 版本检查 ==="
docker compose version
# 如果返回 'command not found'，说明需要安装 Compose v2

echo ""
echo "=== 5. 端口占用检查 ==="
ss -tlnp | grep -E ':80\b|:443\b|:8080\b'
# 如果 80/443 被占用（如已有 Nginx），修改 harbor.yml 使用其他端口

echo ""
echo "=== 6. 磁盘空间检查（/data 需 > 20GB 空闲）==="
df -h /data 2>/dev/null || echo "/data 目录不存在，将在安装时创建"

echo ""
echo "=== 7. SELinux 状态检查（CentOS/RHEL）==="
if command -v getenforce &>/dev/null; then
    echo "SELinux: $(getenforce)"
    if [ "$(getenforce)" = "Enforcing" ]; then
        echo "⚠  SELinux 可能导致容器无法写入挂载卷"
    fi
fi

echo ""
echo "=== 8. Firewalld 状态检查 ==="
if command -v firewall-cmd &>/dev/null; then
    firewall-cmd --state 2>/dev/null || echo "Firewalld 未运行"
fi

echo ""
echo "=== 预检完成 ==="
```

### 3.3 方式一：离线安装（推荐：内网/生产环境首选）

> **背景**：离线安装包包含了所有 9 个 Harbor 组件容器镜像的 tar 文件（~750MB 压缩后），安装过程无需任何外网访问，是纯内网环境的最佳选择。

**步骤一：在外网机器下载离线安装包**

```bash
# 1. 下载 Harbor 离线安装包（~750MB）
cd /tmp
wget https://github.com/goharbor/harbor/releases/download/v2.12.0/harbor-offline-installer-v2.12.0.tgz

# 2. 同时下载校验文件（推荐）
wget https://github.com/goharbor/harbor/releases/download/v2.12.0/harbor-offline-installer-v2.12.0.tgz.asc

# 3. 校验文件完整性
sha256sum -c harbor-offline-installer-v2.12.0.tgz.asc 2>/dev/null || sha256sum harbor-offline-installer-v2.12.0.tgz
# 预期输出：<hash>  harbor-offline-installer-v2.12.0.tgz  (OK 或无报错)

# 4. 将安装包传输到内网目标服务器（选一种方式）
# 方式 A：通过 U 盘/移动硬盘直接拷贝
# 方式 B：通过堡垒机中转（如果堡垒机有文件传输功能）
# 方式 C：上传到内网 NAS/对象存储，目标服务器从 NAS 下载
scp harbor-offline-installer-v2.12.0.tgz user@192.168.1.100:/opt/
```

**步骤二：在内网目标服务器解压安装**

```bash
# 1. 切换到 root（Harbor 需要 root 权限执行 prepare 和 compose）
sudo su -

# 2. 解压离线包
tar -xzf harbor-offline-installer-v2.12.0.tgz -C /opt/
cd /opt/harbor

# 3. 查看解压后的文件结构
ls -lah
# 关键文件说明：
#   harbor.yml.tmpl        - 配置模板（核心参数都在这里）
#   prepare                - Go 编译的二进制文件，将 harbor.yml 转换为各组件运行时配置
#   install.sh             - 安装脚本（docker load → docker compose up）
#   docker-compose.yml     - 容器编排文件（一般不需要手动改）
#   common.sh              - 公共 bash 函数（install.sh 会 source 它）
#   harbor.v2.12.0.tar.gz  - 全部依赖镜像的 tar 包（安装时自动 load 到 Docker）
```

**步骤三：创建数据目录并配置 harbor.yml**

```bash
# 1. 创建 Harbor 数据持久化目录
mkdir -p /data/harbor
# 如果是 CentOS/RHEL 且 SELinux 启用：
# chcon -Rt svirt_sandbox_file_t /data/harbor

# 2. 复制配置模板
cp harbor.yml.tmpl harbor.yml

# 3. 编辑配置文件（vim/nano）
vim harbor.yml
```

生产环境 `harbor.yml` 推荐配置：

```yaml
# ==========================================
# Harbor v2.12 生产环境配置模板
# ==========================================

# 【必改】服务器的主机名——Docker 客户端用它来访问 Harbor
# 如果是 IP：确保所有客户端能 ping 通
# 如果是域名：确保 DNS 解析已配置 或 /etc/hosts 已写入
hostname: harbor.yourcompany.com

# -----------------------------------------------------------
# HTTP 配置（建议生产环境不配置 HTTP，仅保留 HTTPS）
# -----------------------------------------------------------
# http:
#   port: 80

# -----------------------------------------------------------
# HTTPS 配置（生产环境必须启用）
# -----------------------------------------------------------
https:
  port: 443
  # 证书文件路径（可以是 Let's Encrypt / 企业内部 CA / 商业 CA 签发）
  certificate: /opt/harbor/certs/harbor.crt
  # 私钥文件路径（权限应为 600，owner 为 root）
  private_key: /opt/harbor/certs/harbor.key

# 【必改】管理员初始密码（安装后立即通过 Portal 修改）
# 密码要求：>= 12 位，含大写、小写、数字、特殊字符中的至少 3 类
harbor_admin_password: MyStr0ngP@ssw0rd!2024

# -----------------------------------------------------------
# 数据库配置
# -----------------------------------------------------------
database:
  # 数据库 root 密码（内部使用，非应用连接用户）
  password: db_secure_pass_2024
  # 最大空闲连接数（一般保持默认 100）
  max_idle_conns: 100
  # 最大打开连接数（高并发场景：CPU 核数 × 100-150）
  max_open_conns: 900

# -----------------------------------------------------------
# 数据存储路径
# -----------------------------------------------------------
# 建议：单独挂载一块 500GB+ SSD 云盘或本地磁盘到此路径
# 此路径下会自动创建以下子目录：
#   /data/harbor/database/    - PostgreSQL 数据文件
#   /data/harbor/redis/       - Redis 数据文件
#   /data/harbor/registry/    - Docker 镜像 blob 存储（主要磁盘消耗）
#   /data/harbor/job_logs/    - JobService 任务日志
#   /data/harbor/trivy/       - Trivy 漏洞数据库缓存
data_volume: /data/harbor

# -----------------------------------------------------------
# 日志配置
# -----------------------------------------------------------
log:
  # 日志级别：debug（排查）/ info（生产）/ warning（仅告警）/ error（仅错误）
  level: info
  # 保留最近 N 个日志文件（按天轮转）
  rotate_count: 30
  # 单个日志文件最大大小（超过后触发轮转）
  rotate_size: 200M
  # 日志文件在容器内的存储路径（一般不需要修改）
  location: /var/log/harbor

# -----------------------------------------------------------
# 异步任务引擎配置
# -----------------------------------------------------------
jobservice:
  # 最大并发任务数——控制同时执行多少个 GC / 扫描 / 复制任务
  # 建议：如果开了 20+ 项目的自动扫描，调到 20
  # 如果只有 3-5 个项目，保持 10 即可
  max_job_workers: 10

# -----------------------------------------------------------
# 外部 URL（可选）
# -----------------------------------------------------------
# 如果 Harbor 前面有反向代理/LB，这里填代理的对外地址
# 注意：external_url 和 hostname 必须一致！
# external_url: https://harbor.yourcompany.com
```

**步骤四：生成运行时配置并启动 Harbor**

```bash
# 1. 生成运行时配置文件（prepare 读取 harbor.yml → 生成各组件的 env/app.conf）
./prepare
```

预期输出：

```
prepare base dir is set to /opt/harbor
Generated configuration file: /opt/harbor/common/config/core/env
Generated configuration file: /opt/harbor/common/config/core/app.conf
Generated configuration file: /opt/harbor/common/config/registry/config.yml
Generated configuration file: /opt/harbor/common/config/registryctl/env
Generated configuration file: /opt/harbor/common/config/registryctl/config.yml
Generated configuration file: /opt/harbor/common/config/db/env
Generated configuration file: /opt/harbor/common/config/jobservice/env
Generated configuration file: /opt/harbor/common/config/jobservice/config.yml
Generated configuration file: /opt/harbor/common/config/log/logrotate.conf
Generated configuration file: /opt/harbor/common/config/nginx/nginx.conf
Generated configuration file: /opt/harbor/common/config/adminserver/env
loaded secret from file: /opt/harbor/common/config/secretkey
Generated certificate, key file: /opt/harbor/common/config/core/private_key.pem,
  cert file: /opt/harbor/common/config/registry/root.crt
The configuration files are ready, please use docker compose to start the service.
```

**故障排查**：如果 `prepare` 报 `check_nginx_config failed`：

```bash
# 1. 确认 https 块中的证书路径是否正确且文件存在
ls -la /opt/harbor/certs/harbor.crt /opt/harbor/certs/harbor.key

# 2. 确认证书文件权限正确
sudo chmod 644 /opt/harbor/certs/harbor.crt
sudo chmod 600 /opt/harbor/certs/harbor.key

# 3. 如果暂时没有证书，注释掉整个 https 块，先用 HTTP 测试
# 编辑 harbor.yml，在 https 行前加 #，port/certificate/private_key 也全注释
# 取消注释 http 块，重新 ./prepare
```

```bash
# 2. 离线安装：先 load 所有镜像（在线安装会自动 pull，离线包需要手动 load）
cd /opt/harbor
# install.sh 会自动从 harbor.v2.12.0.tar.gz load 镜像，然后 compose up
# 如果你想分步执行（便于排障）：
docker load -i harbor.v2.12.0.tar.gz
# 预期输出：Loaded image: goharbor/harbor-core:v2.12.0（共 9 个）

# 3. 启动所有容器
sudo ./install.sh
```

`install.sh` 的完整执行过程：

```
[Step 0]: checking installation environment ...
Docker version 24.0.7                        ✓
Docker Compose version v2.23.0               ✓
Note: docker version: 24.0.7
Note: docker-compose version: v2.23.0

[Step 1]: loading Harbor images ...
Loaded image: goharbor/harbor-core:v2.12.0
Loaded image: goharbor/harbor-portal:v2.12.0
Loaded image: goharbor/harbor-jobservice:v2.12.0
Loaded image: goharbor/harbor-log:v2.12.0
Loaded image: goharbor/harbor-db:v2.12.0
Loaded image: goharbor/redis-photon:v2.12.0
Loaded image: goharbor/nginx-photon:v2.12.0
Loaded image: goharbor/registry-photon:v2.12.0
Loaded image: goharbor/trivy-adapter-photon:v2.12.0

[Step 2]: preparing environment ...
Generated and saved secret to file: /opt/harbor/common/config/secretkey
The configuration files are ready.

[Step 3]: starting Harbor ...
Creating network "harbor_harbor" with driver "bridge"
Creating harbor-log ... done
Creating harbor-db  ... done
Creating redis      ... done
Creating registry   ... done
Creating registryctl... done
Creating harbor-core... done
Creating nginx      ... done
Creating harbor-portal... done
Creating harbor-jobservice... done
Creating trivy-adapter... done
✔ ---- Harbor has been installed and started successfully. ----
```

**步骤五：四层验证——确认安装成功**

```bash
# ==========================================
# 验证 Harbor 安装的四层检查
# ==========================================

echo "=== 第一层：容器状态检查 ==="
docker compose ps
# 预期：所有 10 个容器 STATUS 为 (healthy)
# 如果任何容器不是 (healthy)，等待 60 秒再检查（组件启动有先后依赖）

echo ""
echo "=== 第二层：API 健康检查 ==="
curl -s -k -u admin:MyStr0ngP@ssw0rd!2024 \
  https://harbor.yourcompany.com/api/v2.0/health | jq .
# 预期：{"status":"healthy","components":[...]}，所有组件 status 为 "healthy"

echo ""
echo "=== 第三层：Docker Push/Pull 端到端测试 ==="
docker login harbor.yourcompany.com -u admin -p MyStr0ngP@ssw0rd!2024
# 预期：Login Succeeded

docker pull alpine:3.19
docker tag alpine:3.19 harbor.yourcompany.com/library/alpine:3.19
docker push harbor.yourcompany.com/library/alpine:3.19
# 预期：The push refers to repository [harbor.yourcompany.com/library/alpine]
#       3.19: digest: sha256:abcdef... size: 528

docker rmi harbor.yourcompany.com/library/alpine:3.19
docker pull harbor.yourcompany.com/library/alpine:3.19
# 预期：3.19: Pulling from library/alpine
#       Digest: sha256:abcdef...
#       Status: Downloaded newer image

echo ""
echo "=== 第四层：Portal 页面访问测试 ==="
curl -s -o /dev/null -w "HTTP Status: %{http_code}\n" \
  https://harbor.yourcompany.com
# 预期：HTTP Status: 200

echo ""
echo "=== 四层验证全部通过 ✅ ==="
```

### 3.4 方式二：在线安装（测试/开发环境适用）

> **背景**：网络能稳定访问 Docker Hub 和 GitHub Container Registry，安装速度快（取决于带宽）。

```bash
# 1. 下载在线安装包（仅 ~10KB，不含镜像）
wget https://github.com/goharbor/harbor/releases/download/v2.12.0/harbor-online-installer-v2.12.0.tgz
tar -xzf harbor-online-installer-v2.12.0.tgz -C /opt/
cd /opt/harbor

# 2. 配置 harbor.yml（同离线安装）
cp harbor.yml.tmpl harbor.yml
vim harbor.yml

# 3. 在线安装（install.sh 会先 docker compose pull 拉取镜像）
# 如果网络慢，可以设置 Docker 镜像加速器：
# 编辑 /etc/docker/daemon.json：
# { "registry-mirrors": ["https://<your-mirror>.mirror.aliyuncs.com"] }
# systemctl restart docker

sudo ./prepare
sudo ./install.sh
# install.sh 执行流程：
#   ① docker compose pull  → 从远端拉取 9 个组件镜像（约 2.1GB）
#   ② docker compose up -d → 后台启动所有容器

# 4. 验证（同离线安装的四层验证）

# 5. 如果 docker compose pull 卡住超时，单独指定镜像拉取
docker pull goharbor/harbor-core:v2.12.0
docker pull goharbor/harbor-portal:v2.12.0
# ... 逐个拉取其他 7 个镜像
# 全部拉取完毕后：
sudo ./prepare && sudo docker compose up -d
```

### 3.5 方式三：Helm 安装（已有 Kubernetes 集群）

```bash
# 1. 添加 Harbor Helm 仓库
helm repo add harbor https://helm.goharbor.io
helm repo update

# 2. 查看可用版本
helm search repo harbor/harbor --versions | head -10

# 3. 创建独立的 Namespace
kubectl create namespace harbor-system

# 4. 创建生产环境 values.yaml
cat <<EOF > harbor-values.yaml
# ==========================================
# Harbor Helm Chart 生产环境 values.yaml
# ==========================================

# 暴露方式：使用 Ingress（生产推荐）或 NodePort（测试）
expose:
  type: ingress
  tls:
    enabled: true
    certSource: auto          # 自动使用 cert-manager，或 manual 指定已有 Secret
    secret:
      secretName: "harbor-tls-secret"
  ingress:
    hosts:
      core: harbor.k8s.yourcompany.com
    className: nginx          # 指定 Ingress Controller 类型
    annotations:
      nginx.ingress.kubernetes.io/proxy-body-size: "0"  # 允许大镜像上传

# 外部访问地址（与 Ingress host 保持一致）
externalURL: https://harbor.k8s.yourcompany.com

# 管理员密码
harborAdminPassword: "K8sHarbor@Admin2024$"

# 持久化存储配置
persistence:
  enabled: true
  resourcePolicy: "keep"     # Helm 卸载时保留 PVC（防止误删数据）
  persistentVolumeClaim:
    registry:
      size: 500Gi            # 镜像存储（根据业务规模调整）
      storageClass: "ssd-fast"  # 使用 SSD 高性能存储类
    database:
      size: 50Gi
      storageClass: "ssd-fast"
    redis:
      size: 20Gi
      storageClass: "ssd-standard"
    trivy:
      size: 20Gi
      storageClass: "ssd-standard"

# 数据库配置
database:
  type: internal             # internal（内置 PG）或 external（外部 PG 集群）
  internal:
    password: "db_internal_pass_2024"
    # 如果是生产环境，推荐初始密码通过 Kubernetes Secret 注入

# 镜像拉取策略
imagePullPolicy: IfNotPresent

# 各组件资源限制
core:
  replicas: 2                # 生产环境 Core 至少 2 副本
  resources:
    requests:
      memory: 256Mi
      cpu: 100m
    limits:
      memory: 512Mi
      cpu: 500m

registry:
  replicas: 2                # Registry 2 副本（需共享存储支持）
  resources:
    requests:
      memory: 512Mi
      cpu: 200m
    limits:
      memory: 1Gi
      cpu: 1000m

jobservice:
  replicas: 1
  resources:
    requests:
      memory: 256Mi
      cpu: 100m
    limits:
      memory: 512Mi
      cpu: 500m

trivy:
  enabled: true
  replicas: 1
  resources:
    requests:
      memory: 512Mi
      cpu: 200m
    limits:
      memory: 2Gi
      cpu: 1000m
EOF

# 5. 安装 Harbor
helm install harbor harbor/harbor \
  -f harbor-values.yaml \
  -n harbor-system \
  --timeout 10m0s

# 6. 监控 Pod 启动状态
kubectl get pods -n harbor-system -w
# 预期：所有 Pod 状态变为 Running

# 7. 验证 Ingress 和 Service
kubectl get ingress -n harbor-system
kubectl get svc -n harbor-system

# 8. 查看各组件的日志（排查启动问题）
kubectl logs -n harbor-system deployment/harbor-core --tail 50
kubectl logs -n harbor-system deployment/harbor-registry --tail 50
```

### 3.6 配置自签名证书并启用 HTTPS（全三种方式共用）

```bash
#!/bin/bash
# ==========================================
# Harbor 自签名证书生成脚本
# 适用场景：测试/开发环境，或使用自己信任的 CA
# ==========================================

DOMAIN="harbor.yourcompany.com"
CERT_DIR="/opt/harbor/certs"

mkdir -p $CERT_DIR

# 1. 生成 CA 私钥和自签名根证书
openssl genrsa -out $CERT_DIR/ca.key 4096
openssl req -x509 -new -nodes -key $CERT_DIR/ca.key -sha256 -days 3650 \
  -subj "/C=CN/ST=Shanghai/L=Shanghai/O=YourCompany/CN=YourCompany-Root-CA" \
  -out $CERT_DIR/ca.crt

# 2. 生成 Harbor 服务器私钥
openssl genrsa -out $CERT_DIR/harbor.key 4096

# 3. 生成证书签名请求 (CSR)
openssl req -new -key $CERT_DIR/harbor.key \
  -subj "/C=CN/ST=Shanghai/L=Shanghai/O=YourCompany/CN=$DOMAIN" \
  -out $CERT_DIR/harbor.csr

# 4. 用 CA 根证书签发 Harbor 服务器证书
# extfile 配置 SAN（Subject Alternative Name）——浏览器要求必须有
cat > $CERT_DIR/extfile.cnf <<EOF
authorityKeyIdentifier=keyid,issuer
basicConstraints=CA:FALSE
keyUsage = digitalSignature, nonRepudiation, keyEncipherment, dataEncipherment
subjectAltName = @alt_names

[alt_names]
DNS.1 = $DOMAIN
DNS.2 = harbor
IP.1 = 10.0.0.50         # 如果也通过 IP 访问，加上 IP SAN
EOF

openssl x509 -req -in $CERT_DIR/harbor.csr \
  -CA $CERT_DIR/ca.crt -CAkey $CERT_DIR/ca.key \
  -CAcreateserial -out $CERT_DIR/harbor.crt \
  -days 365 -sha256 -extfile $CERT_DIR/extfile.cnf

# 5. 设置正确的文件权限
chmod 644 $CERT_DIR/ca.crt $CERT_DIR/harbor.crt $CERT_DIR/harbor.csr
chmod 600 $CERT_DIR/ca.key $CERT_DIR/harbor.key

# 6. 查看证书内容确认
openssl x509 -in $CERT_DIR/harbor.crt -text -noout | grep -A1 "Subject:\|DNS:\|IP Address:"

echo "证书生成完成 ✓"
echo "CA 证书: $CERT_DIR/ca.crt      (需要分发到所有 Docker 客户端)"
echo "服务器证书: $CERT_DIR/harbor.crt (Harbor Nginx 使用)"
echo "服务器私钥: $CERT_DIR/harbor.key (Harbor Nginx 使用)"
```

### 3.7 Docker 客户端配置自签名证书信任

```bash
# ==========================================
# 在每台需要访问 Harbor 的 Docker 客户端机器上执行
# ==========================================

# 1. 从 Harbor 服务器获取 CA 证书
# scp user@harbor-server:/opt/harbor/certs/ca.crt /tmp/ca.crt

# 2. 创建 Docker 证书信任目录
# 注意：目录名必须是 harbor 的 hostname（含端口，如果是非标准端口）
HARBOR_HOST="harbor.yourcompany.com"
# 如果 Harbor 使用非标准端口（如 8443），目录名需含端口：
# HARBOR_HOST="harbor.yourcompany.com:8443"

sudo mkdir -p /etc/docker/certs.d/$HARBOR_HOST/

# 3. 将 CA 证书放入信任目录
sudo cp /tmp/ca.crt /etc/docker/certs.d/$HARBOR_HOST/ca.crt

# 4. 重启 Docker 使配置生效
sudo systemctl restart docker

# 5. 验证登录
docker login $HARBOR_HOST -u admin
# 输入密码后，预期：Login Succeeded
# 如果仍然报 x509 错误，检查：
#   - 目录名是否与 docker login 时使用的 hostname 完全一致
#   - CA 证书文件是否名为 ca.crt（不是 harbor.crt！）
#   - 证书是否在有效期内：openssl x509 -in ca.crt -noout -dates
```

### 3.8 可能遇到的坑

**坑1：`./prepare` 报 `check_nginx_config failed` 且不提示具体原因**

- **现象**：执行 `./prepare` 后终端显示 `check_nginx_config failed`，但没有提示是哪一行配置有问题。
- **根因**：
  1. `https.port` 配置了，但 `certificate` 或 `private_key` 路径为空、路径不存在或文件权限不正确。
  2. `hostname` 中包含特殊字符（如 `_`）导致 Nginx 配置生成的 `server_name` 语法错误。
  3. `external_url` 格式不正确（缺少协议前缀 `https://` 或 `http://`）。
- **解决方法**：
  ```bash
  # 方案一：临时绕过——注释掉 harbor.yml 中整个 https 块，用 HTTP 先跑起来
  # 方案二：逐项排查证书路径
  grep -A3 '^https:' /opt/harbor/harbor.yml
  ls -la $(grep 'certificate:' /opt/harbor/harbor.yml | awk '{print $2}')
  ls -la $(grep 'private_key:' /opt/harbor/harbor.yml | awk '{print $2}')
  # 方案三：查看生成的 Nginx 配置是否有语法错误
  cat /opt/harbor/common/config/nginx/nginx.conf | head -30
  ```

**坑2：`docker compose up -d` 后 Core 容器反复重启（restart loop）**

- **现象**：`docker compose ps` 显示 `harbor-core` 的 STATUS 在 `(health: starting)` 和 `(unhealthy)` 之间反复切换。
- **根因**：
  1. PostgreSQL 容器启动但数据库初始化未完成（需等待 10-30 秒），Core 在数据库 ready 之前就连上去，连接被拒绝后退出。
  2. Core 的内部密钥文件 `/opt/harbor/common/config/secretkey` 不存在或损坏。
  3. `data_volume` 路径没有写入权限（宿主机目录 owner 不是容器的运行用户 ID）。
- **解决方法**：
  ```bash
  # 1. 查看 Core 日志定位具体错误
  docker logs harbor-core --tail 100 2>&1 | grep -iE 'error|fatal|panic|refused|timeout'

  # 2. 如果是 DB 未就绪，增加 Core 的重试次数
  # 编辑 docker-compose.yml 中 core 服务的 healthcheck：
  #   retries: 10        # 从默认重试次数增加到 10
  #   start_period: 60s  # 首次健康检查等待 60 秒（给 DB 充足的初始化时间）

  # 3. 重新生成密钥文件
  cd /opt/harbor
  sudo ./prepare --with-notary --with-trivy  # 强制重新生成所有配置

  # 4. 检查数据目录权限
  ls -la /data/harbor/
  # postgres 目录 owner 应为 999:999
  # registry 目录 owner 应为 10000:10000
  sudo chown -R 999:999 /data/harbor/database/
  sudo chown -R 10000:10000 /data/harbor/registry/
  ```

**坑3：Docker 客户端 `docker login` 成功但 `docker push` 报 401 Unauthorized**

- **现象**：`docker login` 返回 `Login Succeeded`，但紧随其后的 `docker push` 返回 `unauthorized: authentication required` 或 `denied: requested access to the resource is denied`。
- **根因**：
  1. 登录的用户没有被添加到目标项目的成员列表中（或角色权限不足）。
  2. `hostname` 和 `external_url` 不一致导致 Token realm 地址不匹配（见本章 2 节"小白"追问的大师解答）。
  3. 项目配额已满——用户有 push 权限但项目存储达到上限。
- **解决方法**：
  ```bash
  # 1. 检查项目成员——登录 Portal → 项目 → 成员
  # 或通过 API 检查
  curl -s -u admin:password \
    https://harbor.yourcompany.com/api/v2.0/projects/1/members | jq .

  # 2. 检查 hostname 和 external_url 是否一致
  grep -E '^hostname:|^external_url:' /opt/harbor/harbor.yml

  # 3. 如果 Docker 客户端在另一台机器上，确保该机器的 Docker Daemon
  #    信任 Harbor 的证书（或已配置 insecure-registries）
  ```

**坑4：Helm 安装后 Ingress 不生效或 Portal 页面加载 502**

- **现象**：`helm install` 成功，Pod 全部 Running，但浏览器访问 Ingress 地址返回 502 Bad Gateway。
- **根因**：
  1. Ingress Controller（如 nginx-ingress）未安装或与 Harbor Chart 的 Ingress class 不匹配。
  2. Core Service 的端口与 Ingress 后端 Service 端口不一致。
  3. TLS Secret 未创建或 cert-manager 未完成证书签发。
- **解决方法**：
  ```bash
  # 1. 确认 Ingress Controller 已安装且运行
  kubectl get pods -n ingress-nginx

  # 2. 检查 Ingress 资源配置
  kubectl describe ingress -n harbor-system harbor-harbor-ingress

  # 3. 检查 Core Service 是否存在并端口正确
  kubectl get svc -n harbor-system harbor-harbor-core

  # 4. 如果使用 cert-manager 自动签发证书
  kubectl get certificate -n harbor-system
  kubectl describe certificate -n harbor-system harbor-tls-cert
  ```

---

## 4 项目总结

### 4.1 三种安装方式全方位对比

| 对比维度 | 在线安装 | 离线安装 | Helm 安装 (K8s) |
|---------|---------|---------|----------------|
| 安装包大小 | ~10KB（仅脚本和配置） | ~750MB（含全部镜像 tar） | ~5KB（Helm Chart tgz） |
| 依赖网络 | 必须能访问 Docker Hub/GHCR | 安装时无任何外网依赖 | 需能访问镜像仓库 |
| 部署速度 | 取决于带宽（100Mbps 约 3-5 分钟） | 5 分钟内（只做 docker load+compose up） | 分钟级（取决于镜像拉取和 PVC 创建） |
| 适用环境 | 可联网的开发/测试环境 | 纯物理隔离内网/生产环境 | 已有 Kubernetes 集群的环境 |
| 运维复杂度 | ⭐⭐ 中等（需理解 Docker Compose） | ⭐⭐ 中等（同上） | ⭐⭐⭐⭐ 较高（需理解 K8s 资源管理） |
| 高可用支持 | ❌ 单机，需额外配置 Keepalived+HAProxy | ❌ 单机，同上 | ✅ K8s 原生多副本 + 自愈 |
| 升级方式 | 下载新脚本 → `./install.sh` | 下载新离线包 → 解压 → `./install.sh` | `helm upgrade harbor harbor/harbor -f values.yaml` |
| 回滚难度 | 停止后恢复旧 compose 文件 | 停止后恢复旧 compose 文件 | `helm rollback` 一键回滚 |
| 备份难度 | `pg_dump` + `rsync /data` | 同左 | `pg_dump` + PVC 快照 |
| 自动扩缩容 | ❌ 不支持 | ❌ 不支持 | ✅ HPA 按 CPU/内存自动扩缩 |
| 推荐场景 | 个人学习、开发环境 PoC | 生产内网、银行/政务等断网机房 | 已有 K8s 集群、需高可用 |
| 不推荐场景 | 内网环境、网络不稳定 | 频繁升级的场景 | 无 K8s 经验的团队 |
| 最低资源 | 2C4G (测试) / 4C8G (生产) | 2C4G / 4C8G | 4C8G + K8s 集群固有资源开销 |

### 4.2 安装后立即执行的五项安全加固

| 序号 | 操作项 | 操作方式 | 风险等级 |
|------|--------|---------|---------|
| 1 | 修改 admin 密码 | Portal → 用户管理 → admin → 修改密码 | 🔴 高危 |
| 2 | 删除或禁用 `Harbor12345` 旧密码 | 确认新密码登录后，确保旧密码失效 | 🔴 高危 |
| 3 | 确认 HTTPS 已启用 | 检查 `docker login` 是否走 TLS（无警告） | 🟡 中危 |
| 4 | 检查 Harbor 版本是否有已知 CVE | 访问 https://github.com/goharbor/harbor/security/advisories | 🟡 中危 |
| 5 | 限制 Docker Socket 暴露 | Harbor 容器不应挂载 `/var/run/docker.sock`（除非特殊需求） | 🔴 高危 |

### 4.3 配置文件关键参数速查表

| 参数路径 | 类型 | 默认值 | 生产建议值 | 修改后是否需要 `./prepare` | 说明 |
|---------|------|--------|-----------|--------------------------|------|
| `hostname` | 必改 | `reg.mydomain.com` | 实际域名/生产 IP | ✅ 是 | Token 签发的 realm 地址，必须客户端可达 |
| `http.port` | 可选 | 80 | 测试用 8080 | ✅ 是 | 生产环境建议禁用 HTTP，仅保留 HTTPS |
| `https.port` | 生产必改 | 注释 | 443 | ✅ 是 | 与 `certificate`/`private_key` 同时配 |
| `https.certificate` | 生产必改 | 注释 | `/opt/harbor/certs/harbor.crt` | ✅ 是 | 证书文件路径，prepare 会检查存在性 |
| `https.private_key` | 生产必改 | 注释 | `/opt/harbor/certs/harbor.key` | ✅ 是 | 私钥文件路径，权限应为 600 |
| `harbor_admin_password` | 必改 | `Harbor12345` | 16 位+ 强密码 | ⚠️ 仅首次 | 只在第一次安装时生效，之后需通过 Portal 改 |
| `database.password` | 建议改 | `root123` | 强密码 | ⚠️ 需同步 PG | 改了 harbor.yml 后还需手动改 PG 内密码 |
| `database.max_open_conns` | 可选 | 900 | CPU 核数×150 | ❌ 否 | 高并发场景（>50 人）建议调大 |
| `data_volume` | 必改 | `/data` | 独立挂载磁盘路径 | ✅ 是 | 建议至少 500GB，SSD |
| `log.level` | 可选 | `info` | `info`（生产）/ `debug`（排障） | ❌ 否 | debug 日志量极大（>30GB/天） |
| `log.rotate_count` | 可选 | 50 | 30（生产） | ❌ 否 | 避免占用太多磁盘空间 |
| `jobservice.max_job_workers` | 可选 | 10 | 10-20 | ❌ 否 | 项目多/扫描频繁时调大到 20 |
| `external_url` | 可选 | 空 | 外网 LB 地址 | ❌ 否 | 必须与 hostname 保持一致 |
| `auth_mode` | 可选 | `db_auth` | `ldap_auth`（如有 LDAP） | ✅ 是 | 切换认证模式必须 re-prepare |

### 4.4 注意事项（配置陷阱与安全边界）

1. **`hostname` 和 `external_url` 必须一致**：不一致会导致 Docker Client 登录成功但 push 报 401。这是 GitHub Issues 中排名前 3 的高频安装问题。
2. **`./prepare` 不会校验磁盘剩余空间**：如果 `data_volume` 指向的磁盘已满（< 5GB 剩余），prepare 不会警告，但 Registry 启动后写 blob 时会 500 错误。安装前务必 `df -h` 确认。
3. **离线包版本强绑定**：v2.12 的离线包不能用于安装 v2.11 或 v2.13。如果从离线包升级到新版本，需要重新下载对应版本的离线包。
4. **Docker Compose 命令格式陷阱**：v2 是 `docker compose`（有空格），v1 是 `docker-compose`（有横线）。Harbor v2.12+ 要求 v2。CentOS 7 默认 yum 源安装的是 v1。
5. **SELinux 会影响容器的卷挂载写入**：在 CentOS/RHEL 系统上，如果 SELinux 是 Enforcing 模式，Harbor 数据卷可能无法正常写入。解决方案：`chcon -Rt svirt_sandbox_file_t /data/harbor` 或（不推荐）`setenforce 0`。
6. **PostgreSQL 密码修改必须数据库同步**：修改 `harbor.yml` 中的 `database.password` 后，必须手动进入 PostgreSQL 容器执行 `ALTER ROLE postgres WITH PASSWORD 'new_pass';`，否则 Core 无法连接数据库。
7. **备份策略必须在安装后立即建立**：不要等到出问题才想备份。安装完成后立即配置：①PostgreSQL 自动备份脚本（pg_dump，每日凌晨）；②`/data/harbor/registry/` 目录的 rsync 或对象存储备份。
8. **Helm 安装的 PVC 删除策略**：在 `values.yaml` 中设置 `persistence.resourcePolicy: "keep"`，这样即使 `helm uninstall`，PVC 和底层数据也不会被删除。生产数据保护的底线配置。

### 4.5 常见踩坑经验（生产部署故障案例）

| 故障案例 | 故障现象 | 根因诊断 | 修复措施 | 教训总结 |
|---------|---------|---------|---------|---------|
| **"docker load 后镜像消失"** | 在离线安装环境，执行 `docker load -i harbor.v2.12.0.tar.gz` 后 `docker images` 能看到镜像，但 10 分钟后镜像不见了 | 机器的 Docker Root Dir（默认 `/var/lib/docker`）磁盘空间不足。Docker load 成功导入镜像，但后续 Docker Daemon 的 GC 或 overlay2 清理操作删除了部分层 | ①检查 Docker Root Dir 磁盘空间 `df -h /var/lib/docker`（需要 > 10GB 空闲）；②如果空间不足，修改 `/etc/docker/daemon.json` 中的 `data-root` 到更大的磁盘分区，重启 Docker 后重新 load | Docker Root Dir 和数据存储路径（data_volume）是两个不同的路径，都需要充足的磁盘空间。离线包 load 后镜像占用约 2.4GB（解压后）。 |
| **"准备脚本权限问题导致 install.sh 静默失败"** | 执行 `./install.sh` 后脚本卡在 `[Step 2]: preparing environment ...` 超过 5 分钟，没有错误输出 | `prepare` 二进制文件缺少执行权限（在 Windows 下载后通过 SCP 传到 Linux，失去 x 权限）。install.sh 中的 `$dir/prepare` 执行失败但没有 set -e，继续执行后续步骤 | `chmod +x /opt/harbor/prepare` 后重新执行 `./install.sh`。另外检查 `common.sh` 是否也有执行权限 | 从 Windows 用 WinSCP 或其他工具传输文件到 Linux 时，二进制文件可能丢失执行权限。安装前务必 `ls -la` 检查所有可执行文件的权限。 |
| **"Helm 安装 PVC Pending 导致所有 Pod 卡住"** | `helm install` 后所有 Pod 状态为 Pending，`kubectl describe pvc` 显示 `no persistent volumes available for this claim` | K8s 集群中未配置默认的 StorageClass。Harbor Helm Chart 需要 4 个 PVC（registry/database/redis/trivy），但没有指定 storageClassName，K8s 使用默认 StorageClass——而集群没有设置默认 SC | ①检查集群 StorageClass `kubectl get sc`；②如果没有默认 SC，给一个 SC 打 annotation：`kubectl annotate sc <sc-name> storageclass.kubernetes.io/is-default-class=true`；③或在 values.yaml 中显式设置 `persistence.persistentVolumeClaim.registry.storageClass: "xxx"` | Helm 安装前必须先确认集群 Storage 就绪——是否有可用的 StorageClass、是否有足够的 PV 配额。建议在 values.yaml 中显式指定 storageClass 而非依赖默认值。 |

### 4.6 思考题

**问题 1**：假设公司在 5 个物理隔离的内网机房（北京、上海、深圳、成都、武汉）各部署了一套 Harbor，各机房之间没有互联网连接。现有一种方案——在内网找一个"中转机器"通过卫星链路下载离线包（~750MB），然后通过内部专线分发到各机房。请问：（1）这个方案中，离线包能不能在所有 5 个机房通用？（2）如果 6 个月后需要升级到 v2.13，如何最小化中转流量——是否需要重新下载完整的 750MB 安装包？（提示：考虑 Harbor 的组件镜像是否支持增量更新，`docker save`/`docker load` 是否支持增量传输。）

**问题 2**：在一次 `./install.sh` 执行过程中，`[Step 1]: loading Harbor images ...`（`docker load`）阶段全部成功，但在 `[Step 3]: starting Harbor ...`（`docker compose up -d`）时，因为 `harbor.yml` 中配置了错误的证书路径导致 Nginx 容器启动失败。此时执行 `docker compose down` 后，再次修正配置执行 `./install.sh` 还需要重新 `docker load` 吗？如果不重新 load，Docker 存储中的已加载镜像会一直占用磁盘空间吗？（提示：`docker load` 和 `docker compose up` 是两个独立的操作，镜像 load 到 Docker 存储后是持久化的，不随容器停止而消失。）

---

> **下一章预告**：第 3 章将带你深入 Harbor Web 控制台，从 Dashboard 的每一个指标数字（存储消耗、漏洞总数、项目分布）到六个一级菜单（仪表盘、项目、日志、系统管理、分发、垃圾回收）的完整功能巡览，建立 Harbor Portal 的全景操作地图。
