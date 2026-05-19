# 第1章：Trivy 术语全景与安全扫描架构原理

> 版本：Trivy v0.50+
> 面向人群：开发、运维、测试、安全工程师

---

## 1. 项目背景

### 业务场景

某中型互联网公司「云帆科技」有 30 人的研发团队，维护着 20 多个微服务。随着业务扩张，公司开始全面容器化，所有服务都打包成 Docker 镜像部署到 Kubernetes 集群。运维负责人老李发现，最近线上频繁出现容器被扫描出高危漏洞的安全工单，甚至有一次因为基础镜像里的 OpenSSL 版本过旧，被客户在安全审计中打了红牌。

CTO 决定在公司内部建立一套容器安全治理体系，要求开发和运维团队对所有镜像进行漏洞扫描。团队第一次尝试使用某商业安全产品时，被昂贵的授权费用劝退；第二次尝试用开源工具 Clair，却发现配置复杂、文档零散，开发人员学了一周还是搞不清楚「什么是 Layer」「什么是 Scanner」；第三次，测试同学用了一个在线扫描网站，结果报告里满屏的 CVE 编号看得人头皮发麻，却没人说得清哪个该优先修、哪个可以暂缓。

### 痛点放大

没有统一的安全扫描工具与术语体系，团队陷入了三重困境：

**第一，沟通成本高。** 开发说「我镜像里没漏洞」，运维说「Clair 扫出来 50 个 CVE」，安全工程师说「那些是误报」——三方对「漏洞」「严重性」「误报」的定义完全不一致，会议开了三小时，问题一个没解决。

**第二，工具选型混乱。**  Clair、Grype、Snyk、Trivy……每个工具的报告格式、严重级别定义、数据库来源都不一样。团队 A 用 Trivy 扫出了 Critical，团队 B 用 Grype 扫同样的镜像只报 High，到底信谁？

**第三，无法建立工程化流程。** 因为没有理解工具的架构原理，团队把扫描当成「一次性动作」——发版前手动扫一下，出问题再救火。没有缓存策略，每次扫描都要重新下载几 GB 的漏洞数据库；没有基线管理，同一个漏洞被反复报出来，开发人员的修复热情很快消磨殆尽。

云帆科技的困境并非个例。对于绝大多数正在容器化转型的团队来说，**缺少的不是工具，而是一套统一的语系和对工具架构的底层理解**。这正是本章的核心目标：在写下第一条 Trivy 命令之前，先把术语和架构建立起来。

---

## 2. 项目设计

**场景**：云帆科技的技术分享会上，CTO 要求三周内把容器安全扫描落地。小胖、小白和大师围坐在白板前，讨论该用什么工具、怎么推进。

---

**小胖**：（嘴里嚼着薯片）「我说各位， security 这事儿不就跟体检一样吗？买个贵点的体检套餐，定期查查不就完了？我看 Snyk 广告挺多的，直接上商业版呗，省事！」

**小白**：（推了推眼镜）「商业版一年几十万，咱们初创公司哪经得起这么烧？而且你仔细看，Snyk 的免费额度对多镜像场景根本不够用。我问你，要是我们 20 个微服务每天构建十次，一天就是 200 次扫描，按它的计费模型，一个月账单一出来 CFO 能直接晕过去。」

**小胖**：「那…… Clair？Red Hat 出的，听起来很靠谱啊。」

**小白**：「Clair 是不错，但它的部署复杂度你试过吗？需要 PostgreSQL、Indexer、Matcher、Notifier 四个组件，光是弄清楚它们之间的 gRPC 调用关系，我前两天就花了一整天。更麻烦的是，Clair 的报告只输出 JSON，开发同学根本看不懂，还得我们运维二次翻译。」

**大师**：（在白板上画了一个圈）「你们说的都对，但忽略了一个关键问题：我们不是缺工具，是缺一张地图。工具是车，术语和架构是地图。没有地图，开法拉利也会在胡同里撞墙。」

**小胖**：「地图？啥地图？」

**大师**：「统一的语系。我先问你们，如果开发同学说『我镜像里只有一个漏洞』，他指的是 CVE、GHSA、还是厂商自己的漏洞编号？严重程度用 CVSS v2 还是 v3？修复版本是上游版本还是 OS Vendor 的回补丁版本？这些定义不统一，扫描结果永远吵不清楚。」

**小白**：（点头）「确实。我上礼拜就遇到一次，Trivy 报了一个 Debian 的 CVE，说修复版本是 1.1.1n-0+deb11u5，但开发看 Docker Hub 官方镜像的 OpenSSL 版本号是 1.1.1n，以为没修复，差点回滚。后来才搞清楚 Debian 自己 backport 了补丁，版本号后面加了个 u5。」

**大师**：「这就是 Vendor Backport 的典型陷阱。很多扫描工具之所以产生大量误报，就是因为不懂 OS 厂商的回补丁机制。Trivy 在这方面做得很好——它会优先采用 OS Vendor 的安全通告，而不是 NVD 的上游版本号。这个设计直接减少了 30% 以上的假阳性。」

**小胖**：「听起来 Trivy 挺聪明的？那它到底怎么工作的？我输入一条命令，它怎么就知道我镜像里有漏洞？」

**大师**：（在白板上画了一条线）「我们可以把 Trivy 的工作流程抽象成五个阶段：**目标发现 → 制品分析 → 漏洞检测 → 结果聚合 → 报告输出**。你输入 `trivy image nginx:latest`，Trivy 先拉取镜像并解析它的分层结构；然后在每一层里找软件包清单——比如 Alpine 的 `installed-db`、Debian 的 `dpkg/status`、Python 的 `requirements.txt`；接着拿这些包名和版本号去查漏洞数据库；最后把匹配到的 CVE 按照严重级别排个序，输出成表格或 JSON。」

**小白**：「等等，你说它查漏洞数据库。那数据库是哪来的？需要我们维护吗？」

**大师**：「Trivy 维护了两套数据库：一个叫 `trivy-db`，涵盖 OS 包和语言包的漏洞数据，每天从 NVD、GitHub Security Advisory、各大 Linux 发行版安全频道自动同步；另一个叫 `java-db`，专门服务 Java Maven 生态。默认情况下，Trivy 每次扫描前会自动检查更新，你也可以手动下载或在内网离线部署。」

**小胖**：「哦！就像导航软件的离线地图包一样，可以先下好，没网也能用？」

**大师**：「技术映射：没错。`trivy-db` 和 `java-db` 就是 Trivy 的『离线地图包』。对于等保要求的内网环境，你可以在外网导出数据库，拿到内网服务器上导入，实现完全离线的漏洞扫描。」

**小白**：「那 Trivy 能扫的东西只有镜像吗？我们还有一些裸机部署的老服务，以及 Terraform 管理的基础设施。」

**大师**：「这就是 Trivy 的另一个优势——**多目标统一引擎**。它的扫描目标覆盖了 image（容器镜像）、fs（本地文件系统）、repo（Git 仓库）、rootfs（根文件系统）、vm（虚拟机镜像）、k8s（Kubernetes 集群）、sbom（SBOM 文件），以及 aws/azure/gcp 云环境。更厉害的是，每种目标可以组合不同的 Scanner：vuln（漏洞）、misconfig（配置错误）、secret（密钥泄露）、license（许可证合规）。你不需要为镜像装一个工具、为代码装另一个工具，Trivy 一条命令全搞定。」

**小胖**：「听起来像瑞士军刀啊！那它的架构长什么样？内部模块怎么组织的？」

**大师**：（在白板上画出架构图）「从源码视角看，Trivy 的核心可以分为四层：

- **CLI 层**（`cmd/trivy/` / `pkg/commands/`）：负责命令解析、参数绑定、配置加载。用的是 Go 生态里标准的 cobra + viper 组合。
- **扫描引擎层**（`pkg/scanner/` / `pkg/fanal/`）：这是 Trivy 的心脏。Fanal 引擎负责解析目标制品——把镜像分层解压成虚拟文件系统，识别里面安装的软件包和依赖清单。
- **检测引擎层**（`pkg/detector/` / `pkg/vulnerability/`）：拿着解析出来的包名和版本，去数据库里匹配漏洞。这里又分为 OS 包检测器（`ospkg/`）和语言包检测器（`library/`）。
- **报告层**（`pkg/report/` / `pkg/types/`）：把检测结果格式化成各种输出——table、json、sarif、cyclonedx、spdx，还支持自定义 Go template。」

**小白**：「这个分层很清晰。那如果我们以后想二开，比如加个公司内部包的检测器，应该动哪一层？」

**大师**：「问得好。自定义检测器属于第三层，你需要实现 `pkg/detector/library/` 下的 Driver 接口；如果你想支持一种新的配置文件扫描，比如公司内部的部署脚本，那就要在第二层 `pkg/fanal/analyzer/` 里注册一个新的 Analyzer。后续章节我们会手把手教你写。」

**小胖**：「我大概听懂了——Trivy 就是个『输入目标 → 解析内容 → 查库比对 → 输出报告』的流水线。那咱们今天是不是可以拍板用 Trivy 了？」

**大师**：「可以拍板，但前提是团队先建立统一语系。我建议第一步：所有人把第一章的术语词典打印出来贴显示器旁边；第二步：我画一张 Trivy 架构图挂 Wiki 上；第三步：运维先搭个单机版跑起来，让开发看看报告长什么样。术语统一了，后面落地就快了。」

---

## 3. 项目实战

### 环境准备

- **操作系统**：Linux / macOS / Windows（WSL2 推荐）
- **Docker**：已安装并运行（用于拉取测试镜像）
- **Trivy**：v0.50 或更高版本
- **网络**：可访问 GitHub（用于首次下载 trivy-db）

### 步骤一：安装 Trivy

**目标**：在本地快速安装 Trivy 并验证版本。

```bash
# macOS
brew install trivy

# Ubuntu/Debian
sudo apt-get install -y wget apt-transport-https gnupg lsb-release
wget -qO - https://aquasecurity.github.io/trivy-repo/deb/public.key | sudo apt-key add -
echo "deb https://aquasecurity.github.io/trivy-repo/deb $(lsb_release -sc) main" | sudo tee -a /etc/apt/sources.list.d/trivy.list
sudo apt-get update
sudo apt-get install -y trivy

# 验证安装
trivy version
```

**预期输出**：

```
Version: 0.50.0
Vulnerability DB:
  Version: 2
  UpdatedAt: 2024-03-15 12:00:00.000000000 +0000 UTC
  NextUpdate: 2024-03-16 00:00:00.000000000 +0000 UTC
  DownloadedAt: 2024-03-15 14:30:00.000000000 +0000 UTC
```

> **可能遇到的坑**：如果 `apt-key` 提示已弃用，可以改用 `gpg --dearmor` 导入密钥。Windows 用户建议直接在 WSL2 中安装，避免原生 PowerShell 的路径问题。

### 步骤二：执行首次扫描

**目标**：扫描一个已知存在漏洞的镜像，理解 Trivy 的输出字段。

```bash
# 扫描一个旧版本 Alpine 镜像（故意选择有漏洞的版本）
trivy image python:3.4-alpine
```

**运行结果**（截取关键部分）：

```
python:3.4-alpine (alpine 3.9.2)
================================
Total: 42 (UNKNOWN: 2, LOW: 10, MEDIUM: 15, HIGH: 12, CRITICAL: 3)

┌─────────────┬────────────────┬──────────┬────────┬───────────────────┐
│   Library   │ Vulnerability  │ Severity │ Status │   Fixed Version   │
├─────────────┼────────────────┼──────────┼────────┼───────────────────┤
│ openssl     │ CVE-2022-0778  │ HIGH     │ fixed  │ 1.1.1n-r0         │
│ busybox     │ CVE-2021-28831 │ HIGH     │ fixed  │ 1.30.1-r5         │
│ sqlite      │ CVE-2019-8457  │ CRITICAL │ fixed  │ 3.28.0-r0         │
└─────────────┴────────────────┴──────────┴────────┴───────────────────┘
```

**逐行解读**：

- `Total: 42`：该镜像共发现 42 个漏洞，按严重级别分类统计。
- `Library`：存在漏洞的软件包名称。
- `Vulnerability`：漏洞的唯一标识符，通常是 CVE（Common Vulnerabilities and Exposures）编号。
- `Severity`：严重程度，分为 UNKNOWN / LOW / MEDIUM / HIGH / CRITICAL。
- `Status`：`fixed` 表示该漏洞已有修复版本；`unfixed` 表示厂商尚未发布补丁。
- `Fixed Version`：修复该漏洞所需的最低版本号。如果镜像中的包版本低于此值，漏洞存在。

### 步骤三：输出 JSON 报告并提取关键信息

**目标**：掌握结构化报告的输出与解析，为后续 CI/CD 集成打基础。

```bash
# 输出 JSON 格式并保存到文件
trivy image --format json --output report.json python:3.4-alpine

# 用 jq 提取所有 CRITICAL 级别的漏洞
cat report.json | jq '.Results[]?.Vulnerabilities?[]? | select(.Severity == "CRITICAL") | {VulnerabilityID, PkgName, InstalledVersion, FixedVersion}'
```

**预期输出**：

```json
{
  "VulnerabilityID": "CVE-2019-8457",
  "PkgName": "sqlite",
  "InstalledVersion": "3.26.0-r3",
  "FixedVersion": "3.28.0-r0"
}
```

> **可能遇到的坑**：如果 `jq` 未安装，可用 `apt-get install jq` 或 `brew install jq` 安装。注意 `.Results[]?` 中的问号是 jq 的安全导航语法，防止 Results 为空时报错。

### 步骤四：理解 Trivy 的漏洞数据库信息

**目标**：查看当前使用的漏洞数据库版本，理解数据新鲜度。

```bash
trivy version
```

观察输出中的 `Vulnerability DB` 段落：

- `UpdatedAt`：数据库最后一次同步时间。
- `NextUpdate`：下一次自动更新时间（Trivy 默认每 12 小时检查一次）。
- `DownloadedAt`：本地缓存的下载时间。

如果处于内网环境，可手动下载并导入数据库：

```bash
# 在外网机器上导出数据库
trivy image --download-db-only
cp ~/.cache/trivy/db/trivy.db /path/to/transfer/

# 在内网机器上放置到缓存目录
mkdir -p ~/.cache/trivy/db/
cp trivy.db ~/.cache/trivy/db/

# 扫描时跳过数据库更新
trivy image --skip-db-update python:3.4-alpine
```

### 步骤五：绘制团队架构图

**目标**：为团队建立统一的 Trivy 架构认知。

使用你喜欢的绘图工具（Draw.io、Excalidraw、Mermaid），绘制以下架构图：

```
┌─────────────────────────────────────────────────────────────┐
│                        用户输入                              │
│              trivy image python:3.4-alpine                  │
└─────────────────────────────────────────────────────────────┘
                             ↓
┌─────────────────────────────────────────────────────────────┐
│                      CLI 解析层 (cobra)                      │
│              命令识别 / Flag 绑定 / 配置加载                   │
└─────────────────────────────────────────────────────────────┘
                             ↓
┌─────────────────────────────────────────────────────────────┐
│                    扫描引擎层 (Fanal)                         │
│   Target → Artifact → Layer → FileSystem → Package List     │
└─────────────────────────────────────────────────────────────┘
                             ↓
┌─────────────────────────────────────────────────────────────┐
│                   检测引擎层 (Detector)                       │
│   OS Package Detector / Library Detector / Secret Detector  │
│                    ↓ 查询 ↓                                  │
│              trivy-db / java-db / checks bundle              │
└─────────────────────────────────────────────────────────────┘
                             ↓
┌─────────────────────────────────────────────────────────────┐
│                    报告层 (Report)                           │
│   Filter → Format (table/json/sarif/cyclonedx/spdx) → Output│
└─────────────────────────────────────────────────────────────┘
```

将这张图输出为 PNG/SVG，上传到团队 Wiki 或 Confluence，作为后续所有 Trivy 相关文档的置顶参考。

### 测试验证

1. 执行 `trivy version`，确认版本号正常输出。
2. 执行 `trivy image python:3.4-alpine`，确认报告中的 Severity 统计总数大于 0。
3. 执行 JSON 输出并用 `jq` 过滤，确认能提取 CRITICAL 漏洞列表。
4. 检查 `~/.cache/trivy/db/` 目录，确认存在 `trivy.db` 文件（证明数据库已缓存）。

---

## 4. 项目总结

### 优点 & 缺点

| 维度  | 优点                                        | 缺点                          |
| --- | ----------------------------------------- | --------------------------- |
| 易用性 | 单二进制文件、零依赖、一条命令开箱即用                       | 高级功能（Rego 策略、插件开发）学习曲线较陡    |
| 覆盖度 | 支持镜像/FS/Repo/K8s/云环境，Scanner 类型丰富         | 某些小众语言或私有包管理器的支持不如商业产品      |
| 准确度 | 优先采用 OS Vendor 数据，减少 Vendor Backport 误报   | 语言包漏洞的 Fixed Version 偶尔存在滞后 |
| 生态  | 与 GitHub Actions、Harbor、K8s Operator 深度集成 | 中文社区资料相对英语社区较少              |
| 成本  | 完全开源免费，无扫描次数限制                            | 企业级支持需要购买 Aqua 商业版          |

### 适用场景

1. **容器镜像安全门禁**：在 CI/CD 流水线中阻断包含 Critical 漏洞的镜像进入生产环境。
2. **开发机安全自检**：开发人员在本地用 `trivy fs` 扫描代码依赖，提前发现风险。
3. **Kubernetes 运行态监控**：通过 Trivy Operator 持续监控集群中的 Pod 漏洞和配置错误。
4. **软件供应链审计**：生成 SBOM 并关联漏洞，满足客户和监管对供应链透明度的要求。
5. **等保/合规内网扫描**：通过离线数据库支持，在无外网环境下完成漏洞检测。

**不适用场景**：

1. 需要动态应用安全测试（DAST）或交互式漏洞扫描（IAST）的场景——Trivy 是静态分析工具，不运行目标程序。
2. 需要 Web 漏洞扫描（如 SQL 注入、XSS）的场景——Trivy 聚焦依赖和配置，不做应用层渗透测试。

### 注意事项

- **数据库时效性**：Trivy 的漏洞检出率高度依赖 `trivy-db` 的新鲜度，建议生产环境每天至少更新一次。
- **OS Vendor 版本号陷阱**：Debian/Ubuntu/RHEL 等发行版会对上游软件打 backport 补丁，版本号可能带有 `+deb11u5` 这类后缀，修复版本与上游不一致是正常现象。
- **语言包识别依赖清单文件**：Trivy 扫描语言包漏洞时，依赖 `go.mod`、`package-lock.json`、`pom.xml` 等锁定文件。如果项目只有 `package.json` 没有锁定文件，版本解析可能不准确。

### 常见踩坑经验

**踩坑案例 1：扫描结果为空**

- **现象**：`trivy image myapp:latest` 输出 `Total: 0`。
- **根因**：镜像基于 scratch 构建，没有包管理器数据库；或者使用的是 Distroless 镜像，Trivy 默认无法解析。
- **解法**：对 Distroless 镜像使用 `--scanners vuln` 并确保应用依赖的锁定文件已包含在镜像中；或改用包含包管理器的 base 镜像。

**踩坑案例 2：Critical 漏洞已修复但 Trivy 仍报**

- **现象**：开发已将 OpenSSL 升级到最新版，Trivy 依然报 CVE。
- **根因**：缓存了旧的 layer，或者 `trivy-db` 未及时更新。
- **解法**：执行 `trivy clean --scan-cache` 清理缓存，并运行 `trivy image --skip-db-update=false` 强制更新数据库。

**踩坑案例 3：内网扫描时数据库下载超时**

- **现象**：首次扫描卡在 `Downloading vulnerability database...`。
- **根因**：Trivy 默认从 GitHub Releases 下载数据库，内网无法访问。
- **解法**：在外网机器执行 `trivy image --download-db-only`，将 `~/.cache/trivy/db/` 目录打包导入内网；或使用自托管的 OCI Registry 作为数据库源。

### 思考题

1. Trivy 的 `trivy-db` 与 `java-db` 为什么要分成两个独立的数据库？如果合并成一个会有什么利弊？
2. 假设你是云帆科技的运维负责人，需要在零外网访问的机房部署 Trivy，请设计一套数据库同步与缓存更新方案（提示：考虑 OCI Registry 作为数据库分发介质）。

> **答案提示**：第 7 章「漏洞数据库管理与离线扫描」将揭晓思考题 2 的完整方案。

---

> **推广计划**：本章建议全公司技术团队通读。开发需要理解术语和架构图，避免后续报告解读时鸡同鸭讲；运维需要掌握安装和数据库缓存机制，为 CI/CD 集成做准备；测试同学重点关注报告字段含义，便于设计验收标准。建议在团队 Wiki 中置顶本章的架构图和术语表。
