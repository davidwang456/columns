# 第1章：Harbor 术语全景与云原生镜像仓库架构原理

## 1 项目背景

"迅捷电商"是一家年 GMV 突破 80 亿的中型电商平台，技术团队 200 余人，业务涵盖 B2C 商城、供应链管理、实时物流调度三大系统。2024 年初，CTO 孙磊拍板启动"凌云计划"——在 6 个月内将全部 47 个微服务从传统虚拟机部署迁移至 Kubernetes 集群。容器镜像仓库的选型和治理，成了整个计划的第一个拦路虎。

迁移初期，架构组采用了最简方案：开发环境直接使用 Docker Hub 公有镜像，内部业务镜像推送到 Docker Hub 私有仓库（Pro 团队版，$7/月/人）。然而，这个"省事"的决定在三个月后集中引爆，几乎让凌云计划夭折：

**场景一：镜像拉取超时导致金丝雀发布卡死。** 运维工程师张磊部署新版订单服务时，高峰期 Docker Hub 拉取速度仅 180KB/s（正常应 15MB/s+），一个包含 JRE 和自研框架的 820MB Java 基础镜像耗时 1 小时 12 分钟。而此时线上正在做 5% 金丝雀流量切换——旧版已摘流、新版镜像还没拉到 Node 上，导致 5000 个并发订单直接报 503。最终运维组全员紧急出动，在 23 台 Node 上手动执行 `docker save/docker load` 绕行，前后耗时 3 小时 40 分钟，直接损失 GMV 约 12 万元。事后统计，仅基础镜像（openjdk:11-jre-slim）的重复下载流量每月就达 2.4TB，浪费出口带宽成本约 ¥8,600/月。

**场景二：离职员工未回收权限导致核心源码泄露风险。** 2024 年 3 月，一名离职的高级开发工程师赵某的 Docker Hub 账号（个人 Pro 账号被加入到了公司团队）未被及时移除。安全审计发现，其离职后第 7 天和第 14 天，其个人笔记本电脑（已离职归还）的 IP 仍从 Docker Hub 拉取了公司核心支付模块 `payment-gateway` 的三个历史版本镜像。虽然未造成实际数据流出（Docker Hub 只有镜像层，不含源码），但该镜像内的 JAR 包可被反编译出业务逻辑。CTO 紧急要求："所有业务镜像必须在公司内部网络闭环流转，镜像层数据不得出公司机房边界。"

**场景三：等保三级合规红线。** 公司最大客户"国兴银行"的合同附件《信息技术服务安全条款》第 7.3 条明确要求：容器镜像仓库必须具备完整的操作审计日志（包含用户、时间、IP、操作类型）、容器镜像漏洞扫描报告（至少覆盖最近 30 天内 CVE）、镜像内容数字签名与防篡改验证。而 Docker Hub Pro 仅提供基础 Pull/Push 记录（无 IP 追踪、无操作类型分类），且漏洞扫描需额外购买 Docker Scout 高级版（$19/月/人，200 人团队年费 $45,600）。合规审计组给出红灯：当前方案无法通过等保三级评测。

**场景四：镜像版本混乱导致测试-生产不一致。** 测试环境的 `order-service:latest` 到底对应哪个 Git Commit？这个问题在 2024 年 4 月的一次"幽灵部署"事故中被放大——运维使用 `order-service:latest` 标签发布，实际推送到生产集群的镜像是三周前开发自测的版本（包含一个未完成的促销活动功能）。结果当天下午 2 点，用户发现下单页面多了一个无法点击的"砍一刀"按钮，且部分订单金额计算错误。回滚后排查发现：测试团队提测时没有给镜像打固定版本标签，CI 流水线每次构建都覆盖 `latest`。受影响订单 327 笔，客服团队花了整整两天逐一核对和退款。

**场景五：多环境镜像同步的运维噩梦。** 公司有三个物理隔离的网络环境——开发测试网（10.0.1.0/24）、预发布网（10.0.2.0/24）、生产网（10.0.3.0/24），三网之间通过防火墙做严格隔离，仅开放指定端口。每次要将测试验证通过的镜像"搬到"生产环境，运维需要：①在测试网 Docker 上 `docker save -o order-service.tar` 导出 1.2GB 镜像包；②通过堡垒机 U 盘拷贝（安全策略禁止 scp 直传）；③在生产网 Docker 上 `docker load -i` 导入；④重新打标签推送到生产 Docker Registry。整个流程需要 4 个审批节点和约 35 分钟人工操作。更糟的是，这个过程完全靠人工记录，没有自动化校验——有一次运维导出了错误的镜像版本（v1.3.2-dev 而非 v1.3.2），导致生产环境跑了两天的开发版，直到业务方报数据异常才发现。

这五个场景指向同一个根因：**企业需要一套私有化部署、安全可治理、支持多环境同步的容器镜像仓库，而非一个单纯的镜像拉取加速器。** Harbor 正是为解决这类企业级镜像管理问题而生。

Harbor 是由 VMware 中国研发中心于 2016 年发起、2020 年成为 CNCF 毕业级项目的开源镜像仓库。本章作为全系列开篇，将建立 Harbor 核心术语体系并逐层解析其六层微服务架构，为后续所有章节奠定统一的认知语系和概念地图。

---

## 2 项目设计——剧本式交锋对话

**场景：周一上午 9:30，凌云计划技术选型会。大会议室里投影仪亮着，参会者包括架构组、运维组、安全组和两个业务线开发代表。桌上摆着油条、豆浆和几盒来不及吃的肠粉。**

**小胖**（左手抓着半根油条，右手划着手机上的架构文档）："大师大师，我不太理解——我们为啥不直接在公司内网用 `docker run -d -p 5000:5000 registry:2` 搭一个 Docker Registry？一条命令就起来了，push/pull 不也跟 Harbor 一样用吗？这就像我在家装个摄像头，能拍到画面不就行了，搞什么人脸识别、红外夜视、云端存储，不是徒增复杂度吗？"

**大师**（放下手中的咖啡杯，用遥控器切到下一页 PPT）："小胖，你这个摄像头比喻恰好说出了本质区别。Docker Registry 就是一个单点摄像头——只能存储画面（镜像 blob）和记录文件名（manifest）。它没有用户体系、没有权限控制、没有录像回放、没有异常告警。你想象一下：如果有人闯进你家（推送了一个带后门的镜像），你的摄像头拍是拍到了，但它既不会报警也不会告诉你'有个不该来的人来了'。"

**小胖**（咽下油条）："那 Harbor 呢？Harbor 就能报警？"

**大师**："Harbor 是一整套智能安防系统。你看这个对比——"

- "**RBAC 权限控制**：相当于给每个房间设了不同级别的门禁卡。保安（项目管理员）能进中控室，住户（开发者）能进自家门但开不了别人家，访客（只读用户）只能在公共区域溜达。你在 Docker Registry 里，所有人拿的都是万能钥匙。"
- "**漏洞扫描（Trivy）**：相当于在门口装了一台 X 光安检机。任何人带包裹（镜像）进来，先过一遍扫描——包里有没有管制刀具（Critical CVE）？有没有易燃易爆品（High CVE）？有的话直接拦截，不让进。"
- "**复制同步**：相当于你家的监控录像自动备份到老家的 NAS——本地一份、异地一份，防火防盗。如果一个机房挂了，另一个机房还能正常拉取镜像。"
- "**内容签名（Cosign/Notary）**：相当于给你的包裹贴了防伪二维码——别人拿到包裹一扫，就能确认'这真的是从你仓库发出来的'，中途没被人掉包。"
- "**操作审计日志**：相当于 24 小时不间断的监控录像。谁、什么时间、从哪个 IP、做了什么操作——增删改查，全部可追溯。出了安全问题，一查录像就知道是哪个环节出的问题。"

**技术映射**：Docker Registry（CNCF Distribution）只实现了 OCI Distribution Spec 定义的 Push/Pull 协议，它是无状态的文件存储。Harbor 在其外部包裹了 API 网关（Core）、认证授权（RBAC）、异步任务引擎（JobService）、漏洞扫描适配器（Trivy）、内容信任（Notary）等，形成了"Registry as a Platform"而非"Registry as a Binary"。

**小胖**（放下油条，掰着手指数）："那这一共是……七个组件？都跑在 Docker 里？万一某个组件挂了怎么办？"

**小白**（一直在安静地翻看 Harbor 架构文档，此时从笔记本后面探出头）："大师，我仔细看了 Harbor 的架构图，确实觉得组件特别多——Core、Portal、JobService、Registry、Registryctl、Trivy、Redis、PostgreSQL，还有 Nginx。如果把这九个组件全部部署，光是容器就 9 个，加上健康检查、日志采集、监控告警，运维复杂度直接翻了两倍不止。为什么不能把功能合并到一两个服务里？比如把 Core 和 JobService 合并，把 Registry 和 Registryctl 合并？微服务拆这么细，是不是过度设计了？"

**大师**（赞许地点头）："小白问到点子上了。这个问题我五年前也问过 Harbor 的架构师。答案其实很简单——**职责分离和独立扩缩**。我给你举个例子。"

"想象你开了一家非常火的餐厅——'大厨小馆'。刚开业时只有你和老婆两个人，你做菜她端盘子，一个 Docker Registry 搞定。但半年后，餐厅每天要出 300 桌——你要同时做川菜、粤菜、日料，还要做外卖、堂食、包间，这时候你必须分区。"

- "**Portal（前台服务员）**：专门接待客人、递菜单、结账。只负责和客人打交道，不进厨房。"
- "**Core（大堂经理）**：所有客人的需求先报到经理这里，经理再分派给后厨、服务员、收银。他是整个餐厅的调度中枢。"
- "**Registry（厨师团队）**：专心做好菜——存储镜像层（blob）、管理菜谱（manifest）。不管客人的事。"
- "**JobService（外卖小哥）**：处理异步任务——打扫后厨（GC）、给分店送菜（复制同步）、食品安全检查（漏洞扫描）。这些活儿不能让厨师干，不然影响出菜速度（Push/Pull 延迟）。"
- "**Trivy（食品安全检测员）**：专门检查每一份食材（镜像层）有没有农药残留（CVE），发现问题立刻通知大堂经理。"
- "**Redis（前台小黑板）**：临时记录外卖单号、等位号码——这些信息不需要永久保存，丢了也能重建。"
- "**PostgreSQL（会计档案室）**：永久存储——谁是厨师、谁是服务员、客人点过什么菜、花过多少钱，全部归档。"
- "**Nginx（传达室门卫）**：所有访客（Docker/Helm Client）先到门卫登记，门卫验明身份后放行到对应的楼层。"

**小白**："那如果厨师（Registry）突然拉肚子请假了，大堂经理（Core）还能继续招呼客人点菜吗？"

**大师**："不能，但大堂经理会立刻告诉所有服务员'今天厨房停气，暂时无法出菜'——这就是 Harbor 内置的健康检查机制返回的 HTTP 503 状态码。前台（Portal）收到 503 后会给客人展示一个友好的'服务暂不可用'页面。关键点在于：**大堂经理和外卖小哥是独立的**——厨师请假了，外卖小哥还是可以继续送之前打包好的外卖（GC 和复制同步不受影响）。如果你把 Core 和 JobService 合在一起，厨师一请假，外卖也停了，这就是雪上加霜。"

**小胖**（突然眼睛一亮）："我懂了！那如果我这家餐厅要做全国连锁——在上海开分店，同步配送食材（跨地域复制镜像），是不是靠那个外卖小哥（JobService）？"

**大师**："完全正确！Harbor 的复制功能就是通过 JobService 触发的异步复制任务。源 Harbor 的后厨（Registry）打包好食材（镜像层），JobService 通知外卖小哥（复制适配器）发往目标 Harbor。这个过程中，主厨（Core）完全不需要参与——他只管下单的那一刻，后续全交给 JobService 和复制适配器。这就是异步解耦的好处。"

**技术映射**：Harbor 的复制机制支持 Push-based（源端推）和 Pull-based（目标端拉）两种模式。底层通过 `replication_adapter` 模块对接目标 Registry，支持 Harbor-to-Harbor、Harbor-to-Docker Hub、Harbor-to-AWS ECR 等多种目标。复制任务的状态由 JobService 写入 Redis 队列，Core 从队列读取后更新 PostgreSQL 中的复制记录。

**小白**（放下笔，眉头紧锁）："大师，我还有三个场景想追问。第一个：如果 Harbor 前面加了一层公司已有的 Nginx 反向代理，Harbor 自己的 Nginx 是不是多余了？第二个：Harbor 的多层级项目结构——项目、仓库、制品、标签——和 Docker Hub 的命名空间 `docker.io/library/nginx:latest` 是怎么对应的？第三个：我们的安全团队要求所有流量必须走 HTTPS，Harbor 内部的组件间通信是 HTTP 还是 HTTPS？"

**大师**（走到白板前，拿起记号笔）："小白，你问得越来越深了。我一个一个回答。"

"**第一个问题：Harbor 内置 Nginx vs 外部 Nginx。** Harbor 内置的 Nginx 不是多余的——它是 Harbor 的'标准门卫'，负责做到以下几点：①统一入口——所有流量（API、Web UI、Registry Push/Pull）都先进这个门卫；②URL 路由——把 `/api/` 转发给 Core，把 `/c/` 转发给 Portal，把 `/v2/` 转发给 Registry；③TLS 终止——如果你没配外部 LB，它就是 SSL 握手的地方。如果你公司已经有了统一的反向代理（比如 Kubernetes Ingress 或 F5），你可以选择只保留 Harbor 自带的 Nginx 做'内部门卫'，让外部 LB 做'外部门卫'——双层门卫更安全。但也可以直接把外部 LB 指向 Core（端口 8080）和 Portal（端口 8080），禁用 Harbor 内置 Nginx。后一种方式更灵活，但需要你自己配置路由规则。"

**技术映射**：Harbor 内置 Nginx 的配置文件在容器内 `/etc/nginx/nginx.conf`，由 `./prepare` 脚本根据 `harbor.yml` 自动生成。如果你用了外部 LB，需要在 `harbor.yml` 中确保 `external_url` 指向 LB 的地址，否则 Docker 客户端 login 时会收到与访问地址不一致的 token 签名地址，导致 401 Unauthorized。

"**第二个问题：命名空间映射。** 这个确实容易搞混。我画个图——"

```
Docker Hub:
  docker.io/library/nginx:1.25
  └── docker.io          → Registry 地址（相当于你的 harbor.company.com）
       └── library       → 项目名（Project）
            └── nginx    → 仓库名（Repository）
                 └── 1.25  → 标签（Tag）

Harbor:
  harbor.company.com/shared-base/nginx:1.25
  └── harbor.company.com → Registry 地址
       └── shared-base   → 项目名（Project）
            └── nginx    → 仓库名（Repository）
                 └── 1.25  → 标签（Tag）
```

"Docker Hub 的 `library` 是一个特殊的命名空间——它是 Docker 官方镜像的组织名。Harbor 中所有的镜像都必须属于某个**项目（Project）**，不存在'无项目的镜像'。项目之下可以有多个**仓库（Repository）**，每个仓库可以有多个**制品（Artifact）**，每个制品可以有多个**标签（Tag）**。所以完整的层级是：`Registry → Project → Repository → Artifact → Tag`。"

**小胖**（恍然大悟）："原来 `library` 就是个项目名！那我把公司基础镜像放 `shared-base` 项目里，跟 Docker Hub 的 `library` 是一个意思？"

**大师**："对！命名规范上，我们推荐用 `shared-base` 存放公共基础镜像（JDK、Nginx、Python 等），用 `proxy-cache` 存放代理缓存镜像（从 Docker Hub 拉取的代理缓存），用业务线名称（如 `order-platform`）存放业务镜像。具体命名规范在第 4 章会详细展开。"

"**第三个问题：组件间通信。** Harbor 内部所有组件之间的通信默认走 **HTTP**，不走 HTTPS。为什么？因为它们在同一个 Docker 网络内部（默认 `harbor_harbor` bridge 网络），彼此之间是安全的。只有外部流量（Docker Client、Web Browser）到 Nginx 这一段走 HTTPS。如果你对安全性要求极高（比如金融行业），可以开启组件间的 mTLS，但这需要自己配置证书分发，相当复杂，一般团队不需要。"

**技术映射**：Harbor 容器通过 Docker 的内部 DNS（`<container_name>`）互相访问，例如 Core 通过 `http://harbor-core:8080` 访问，Registry 通过 `http://registry:5000` 访问。这个内部网络名是在 `docker-compose.yml` 中定义的，修改容器名会影响所有组件间的通信。

**小白**："还有一个我担心的问题——数据持久化。如果 Harbor 的主机磁盘满了或者挂载的 `/data` 目录丢了，我们还能恢复所有镜像吗？数据库存在 PostgreSQL 里，镜像层存在 Registry 的文件系统里，这两个是分开的对吧？"

**大师**："非常对，这恰好引出了 Harbor 数据持久化的核心设计。Harbor 的数据分成两类——"

1. "**元数据（Metadata）**：用户信息、项目结构、仓库名称、标签列表、权限配置、扫描报告、审计日志。这些全部存在 PostgreSQL 里，容器内路径是 `/var/lib/postgresql/data`，宿主机映射到 `{$data_volume}/database/`。"
2. "**镜像层数据（Blob Data）**：Docker 镜像的实际分层 tar 包。存在 Registry 容器管理的文件系统里，宿主机映射到 `{$data_volume}/registry/`。底层用的是 OCI Distribution Spec 定义的 blob 存储格式——一个 `blobs/` 目录下以 sha256 命名子目录。"
3. "**配置文件**：`harbor.yml`、`docker-compose.yml`、Nginx 配置、SSL 证书等，存在宿主机的 `/opt/harbor/` 下。"

"如果把 PostgreSQL 的数据文件和 Registry 的镜像层文件分开备份，你可以在丢失镜像层的情况下重建元数据（虽然镜像不可用，但你知道丢了什么），也可以在丢失元数据的情况下保留镜像层（但你不知道哪个 blob 对应哪个仓库）。生产环境建议两者同时备份——每天凌晨自动执行 `pg_dump` 导出元数据，使用 `rsync` 或对象存储备份镜像层。Harbor 官方文档提供了一个备份脚本模板。"

**小胖**（把最后一口油条塞进嘴里）："那 GC 垃圾回收呢？是不是跟手机清理垃圾一样——把不用的文件删掉释放空间？"

**大师**："你这个比喻基本正确，但有个重要的细节。Harbor 的 GC 分两步——第一步是**标记可达的 blob**（哪些镜像层被至少一个标签引用），第二步是**删除不可达的 blob**（没有任何标签引用的孤立 blob）。关键坑在于：如果你在 GC 的同时有 `docker push` 操作，可能会误删正在推送的 blob。所以 Harbor 官方强烈建议——**GC 期间将 Harbor 设为只读模式**，或者选择业务低峰期执行（凌晨 2-4 点）。"

**技术映射**：Docker Registry 使用 blob 去重存储——同一个 sha256 的镜像层在磁盘上只存一份，无论被多少个仓库引用。当你的 100 个服务都基于同一个 `openjdk:11-jre-slim` 基础镜像时，这 100 个服务共享同一份基础层的磁盘数据。GC 释放的空间来自那些所有标签都不再引用的 blob——比如你删了旧版本的标签，而该版本的唯一层没有被其他镜像引用。

**小白**（合上笔记本，抬头看大师）："大师，最后一个问题——如果我们要在 20 个人的团队中推行 Harbor，从零开始建立镜像管理规范，你觉得最难的是什么？是技术选型还是人的习惯？"

**大师**（沉默了两秒，语气变得郑重）："这个问题问到了所有基础设施项目的核心。我的经验是——**最难的不是技术，是团队的共识和习惯养成。** Harbor 技术本身不难，但要让 20 个开发者改掉'随手推 latest'的习惯，要让运维接受'镜像先扫描再部署'的流程增加 5 分钟，要让架构师放弃'一个 library 项目装所有东西'的便利——这些才是真正的挑战。"

"我给你一个推广路线图——第一阶段（第 1-2 周）：技术骨干先行，建好项目结构，写好命名规范文档。第二阶段（第 3-4 周）：在 CI 流水线中接入 Harbor，让镜像自动推送到正确的项目，开发者不用手动操作。第三阶段（第 5-8 周）：逐步上策略——先开漏洞扫描（不阻止部署），再开标签不可变性，最后上 CVE 阻止门禁。**一刀切全开是推广失败的最快方式。**"

---

## 3 项目实战

### 3.1 环境准备

| 组件 | 版本要求 | 说明 |
|------|---------|------|
| Docker Engine | 20.10.0+（推荐 24.0+） | 容器运行时，`docker --version` 检查 |
| Docker Compose | v2.10.0+（推荐 v2.23+） | 多容器编排，注意是 `docker compose` 非 `docker-compose` |
| Harbor 离线包 | v2.12.0 | 离线安装包 ~750MB，含全部依赖镜像 |
| 操作系统 | Ubuntu 22.04 LTS / CentOS 7.9 / Rocky 9 | 生产环境推荐 Ubuntu 22.04 LTS 或 Rocky 9 |
| CPU | 4 核+（最低 2 核） | GC 和扫描是 CPU 密集型任务 |
| 内存 | 8 GB+（最低 4 GB） | Trivy 扫描时峰值内存可达 3GB |
| 磁盘（/data） | 80 GB+（建议 SSD） | 镜像层去重后仍快速增长，建议 500GB+ |
| 依赖软件 | openssl（自签证书）、curl（API 测试）、jq（JSON 解析） | 前三项可选，但生产排障必备 |

### 3.2 最小化安装与验证

**步骤一：下载并解压离线安装包**

> **目标**：获取 Harbor v2.12 的完整离线安装包，包含所有依赖容器镜像的 tar 文件。

```bash
# 在外网机器上下载离线包（~750MB，含 9 个组件镜像）
# 如果内网机器可联网，也可以用在线安装包（~10KB）
wget https://github.com/goharbor/harbor/releases/download/v2.12.0/harbor-offline-installer-v2.12.0.tgz

# 校验 SHA256（防止下载包损坏或被篡改）
wget https://github.com/goharbor/harbor/releases/download/v2.12.0/harbor-offline-installer-v2.12.0.tgz.asc
sha256sum -c harbor-offline-installer-v2.12.0.tgz.asc 2>/dev/null || sha256sum harbor-offline-installer-v2.12.0.tgz

# 创建安装目录并解压
mkdir -p /opt/harbor
tar -xzf harbor-offline-installer-v2.12.0.tgz -C /opt/
cd /opt/harbor

# 查看解压后的文件结构
ls -la
```

预期输出：

```
总计 750M
-rw-r--r-- 1 root root  6.2K  common.sh
-rw-r--r-- 1 root root  6.8K  harbor.yml.tmpl
-rw-r--r-- 1 root root  5.2K  install.sh
-rwxr-xr-x 1 root root   12M  prepare
-rw-r--r-- 1 root root  730M  harbor.v2.12.0.tar.gz
-rw-r--r-- 1 root root  5.8K  docker-compose.yml
-rw-r--r-- 1 root root  1.5K  LICENSE
```

**步骤二：编写最小化 harbor.yml 配置**

> **目标**：创建一份可运行的 Harbor 配置文件，最小化参数确保安装成功。

```bash
# 复制配置模板
cp harbor.yml.tmpl harbor.yml

# 查看默认模板结构（了解有哪些参数）
grep -E '^[a-z_]+:|^  [a-z_]+:|^# ' harbor.yml
```

编辑 `harbor.yml`，最精简可运行版本（测试环境用 HTTP）：

```yaml
# ==========================================
# Harbor v2.12 最小化配置（测试/学习环境）
# ==========================================
hostname: 192.168.1.100        # 必改：替换为你的服务器 IP 或域名

# HTTP 配置（测试环境先用 HTTP，生产环境必须上 HTTPS）
http:
  port: 8080                    # 如果端口 80 被占用，改用 8080

# HTTPS 配置（生产环境取消注释并填写证书路径）
# https:
#   port: 443
#   certificate: /opt/harbor/certs/harbor.crt
#   private_key: /opt/harbor/certs/harbor.key

# 【必改】管理员初始密码
harbor_admin_password: Harbor12345

# 数据库密码（可自定义）
database:
  password: root123
  max_idle_conns: 100          # 最大空闲连接数
  max_open_conns: 900          # 最大打开连接数（≈ CPU 核数 × 100）

# 数据存储路径（建议挂载独立大容量磁盘）
data_volume: /data

# 日志配置
log:
  level: info                  # 可选 debug / info / warning / error
  rotate_count: 50             # 保留最近 50 个日志文件
  rotate_size: 200M            # 单个日志文件最大 200MB
  location: /var/log/harbor    # 日志存储位置（容器内路径）

# 任务配置
jobservice:
  max_job_workers: 10          # 最大并发任务数（GC、扫描、复制等）
```

**步骤三：执行 prepare 并安装**

> **目标**：生成运行时配置文件并启动所有 Harbor 容器。

```bash
# 第一步：prepare 生成运行时配置
# prepare 会读取 harbor.yml，生成 docker-compose.yml 中各容器的环境变量和挂载配置
sudo ./prepare
```

prepare 成功的预期输出：

```
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
Generated certificate, key file: /opt/harbor/common/config/core/private_key.pem, cert file: /opt/harbor/common/config/registry/root.crt
The configuration files are ready, please use docker compose to start the service.
```

```bash
# 第二步：启动所有 Harbor 容器
# install.sh 实际顺序：docker compose pull（在线模式）→ docker compose up -d
sudo ./install.sh

# 如果想单独控制启动过程（排障用）：
# sudo docker compose up -d    # 直接启动，跳过镜像拉取
```

install.sh 的预期输出：

```
[Step 0]: checking installation environment ...
Docker version 24.0.7          ✓
Docker Compose version v2.23.0 ✓
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

**故障排查**：如果卡在某个 `Creating xxx ...` 超过 60 秒，另开一个终端执行：
```bash
# 查看卡住的容器日志
docker logs harbor-core 2>&1 | tail -50
# 常见原因：PostgreSQL 未完全启动 → 等 30 秒 → docker compose restart harbor-core
```

**步骤四：验证组件健康状态**

> **目标**：确认所有容器正常运行且健康检查通过。

```bash
# 查看所有容器运行状态（含健康状态）
docker compose ps
```

预期输出：

```
NAME                  IMAGE                                  STATUS
harbor-core           goharbor/harbor-core:v2.12.0           Up 35s (healthy)
harbor-db             goharbor/harbor-db:v2.12.0             Up 45s (healthy)
harbor-jobservice     goharbor/harbor-jobservice:v2.12.0     Up 30s (healthy)
harbor-log            goharbor/harbor-log:v2.12.0            Up 55s (healthy)
harbor-portal         goharbor/harbor-portal:v2.12.0         Up 25s (healthy)
nginx                 goharbor/nginx-photon:v2.12.0          Up 20s (healthy)
redis                 goharbor/redis-photon:v2.12.0          Up 46s (healthy)
registry              goharbor/registry-photon:v2.12.0       Up 40s (healthy)
registryctl           goharbor/registryctl:v2.12.0           Up 38s (healthy)
trivy-adapter         goharbor/trivy-adapter-photon:v2.12.0  Up 28s (healthy)
```

如果任何容器状态不是 `(healthy)`，查看其日志：

```bash
docker logs harbor-core 2>&1 | grep -iE 'error|fatal|panic|failed'
docker logs registry 2>&1 | grep -iE 'error|fatal'
```

**步骤五：通过 API 验证 Core 服务**

> **目标**：确认 Harbor Core API 可正常响应，返回系统健康状态。

```bash
# 测试 Core API 健康检查（需要认证）
curl -k -u admin:Harbor12345 https://192.168.1.100:8080/api/v2.0/health

# 或者用 HTTP（如果没配 HTTPS）
curl -u admin:Harbor12345 http://192.168.1.100:8080/api/v2.0/health
```

预期 JSON 响应：

```json
{
  "status": "healthy",
  "components": [
    {"name": "core", "status": "healthy"},
    {"name": "registry", "status": "healthy"},
    {"name": "database", "status": "healthy"},
    {"name": "jobservice", "status": "healthy"},
    {"name": "portal", "status": "healthy"},
    {"name": "trivy", "status": "healthy"},
    {"name": "notary", "status": "healthy"},
    {"name": "chartmuseum", "status": "healthy"}
  ]
}
```

**步骤六：验证 Docker 客户端 Pull/Push 流程**

> **目标**：使用 Docker 命令行完成完整的镜像 Login → Tag → Push → Pull 流程，验证镜像仓库功能正常运行。

```bash
# 1. Docker 登录 Harbor（测试环境需配置 insecure-registries）
# 如果使用 HTTP 或自签证书，先在 /etc/docker/daemon.json 中添加：
# { "insecure-registries": ["192.168.1.100:8080"] }
# 然后重启 Docker：systemctl restart docker

docker login 192.168.1.100:8080
# 输入用户名：admin
# 输入密码：Harbor12345
# 预期：Login Succeeded
```

```bash
# 2. 拉取一个测试镜像并重新打标签
docker pull alpine:3.19
# 预期输出：3.19: Pulling from library/alpine ... Download complete

docker tag alpine:3.19 192.168.1.100:8080/library/alpine:3.19
```

```bash
# 3. 推送到 Harbor（Harbor 会在 push 时自动创建 library 项目和 alpine 仓库）
docker push 192.168.1.100:8080/library/alpine:3.19
# 预期输出：
# The push refers to repository [192.168.1.100:8080/library/alpine]
# 8e5cfa6b2d3a: Pushed
# 3.19: digest: sha256:... size: 528
```

```bash
# 4. 验证拉取（删除本地镜像后重新拉取）
docker rmi 192.168.1.100:8080/library/alpine:3.19
docker pull 192.168.1.100:8080/library/alpine:3.19
# 预期输出：
# 3.19: Pulling from library/alpine
# 8e5cfa6b2d3a: Pull complete
# Digest: sha256:...
# Status: Downloaded newer image for 192.168.1.100:8080/library/alpine:3.19
```

```bash
# 5. 查看已推送的镜像元数据（API 方式）
curl -s -u admin:Harbor12345 \
  http://192.168.1.100:8080/api/v2.0/projects | jq '.[].name'

curl -s -u admin:Harbor12345 \
  http://192.168.1.100:8080/api/v2.0/projects/library/repositories | jq '.[].name'
```

**步骤七：绘制 Harbor 六层架构全景图**

> **目标**：用文本图形式展示 Harbor 的六层架构模型，理解各层职责和数据流向。

```
┌─────────────────────────────────────────────────────────────────┐
│                    Harbor 六层架构全景图                          │
└─────────────────────────────────────────────────────────────────┘

                    ┌──────────────────────┐
                    │   Docker / Helm CLI  │  ← 客户端层
                    └──────┬───────┬───────┘
                           │ HTTPS │ (docker push/pull)
                           │       │ (helm install)
                    ┌──────▼───────▼───────┐
                    │   Nginx (反向代理)   │  ← 接入层 (Port 80/443)
                    │   TLS 终止 & 路由     │     负责：SSL / 路由分发 / 限流
                    └──────┬───────┬───────┘
                           │       │
              ┌────────────▼─┐   ┌─▼──────────────┐
              │   Portal      │   │   Core (API)    │  ← 前端层
              │  (Angular)    │   │   端口:8080     │     负责：用户界面 / API 网关 / 认证鉴权
              │   Web UI      │   │   权限 & 调度    │
              └───────────────┘   └─┬───┬───┬──────┘
                                    │   │   │
              ┌─────────────────────┘   │   └──────────────────┐
              ▼                         ▼                       ▼
    ┌─────────────────┐    ┌─────────────────────┐    ┌─────────────────┐
    │ PostgreSQL (DB) │    │  Redis (缓存/队列)   │    │  RegistryCtl     │  ← 数据层
    │  用户/项目/审计  │    │  任务状态/会话       │    │   Registry 配置   │     负责：持久化存储
    └─────────────────┘    └─────────────────────┘    │   垃圾回收控制    │           / 缓存 / 配置管理
                                                      └─────────────────┘
              ┌──────────────────────┐                       │
              │   JobService         │◄──────────────────────┘
              │   (异步任务引擎)      │  ← 任务层
              │   GC / 复制 / 扫描    │     负责：异步执行所有耗时任务
              └─┬────────┬────────┬──┘
                │        │        │
    ┌───────────▼──┐ ┌──▼──────┐ ┌▼──────────────┐
    │   Registry    │ │ Trivy   │ │ Replication   │  ← 存储/安全层
    │  (镜像存储)    │ │(漏洞扫描)│ │  Adapter      │     负责：镜像 blob 存储
    │ OCI Distribution│ │ CVE 报告│ │  (复制适配器) │           / 安全扫描 / 跨地域同步
    └───────────────┘ └─────────┘ └───────────────┘
```

**六层模型说明**：

| 层级 | 名称 | 核心组件 | 职责 |
|------|------|---------|------|
| 第 1 层 | 客户端层 | Docker CLI / Helm CLI | 发起镜像 Push/Pull 请求 |
| 第 2 层 | 接入层 | Nginx | TLS 终止、路由分发、限流 |
| 第 3 层 | 前端层 | Portal + Core | Web UI 交互、API 网关、认证鉴权 |
| 第 4 层 | 数据层 | PostgreSQL + Redis + Registryctl | 元数据存储、任务状态缓存、存储配置 |
| 第 5 层 | 任务层 | JobService | 异步执行：GC、Replication、Scanning |
| 第 6 层 | 存储/安全层 | Registry + Trivy + Replication Adapter | 镜像 blob 存储、CVE 扫描、跨地域同步 |

**步骤八：配置自签名证书启用 HTTPS**

> **目标**：为 Harbor 配置自签名 SSL 证书（测试/开发环境用），生产环境请使用 CA 签发的证书。

```bash
# 1. 生成自签名 CA 证书和 Harbor 证书
mkdir -p /opt/harbor/certs && cd /opt/harbor/certs

# 生成 CA 私钥
openssl genrsa -out ca.key 4096

# 生成 CA 自签名证书（有效期 10 年）
openssl req -x509 -new -nodes -key ca.key -sha256 -days 3650 \
  -subj "/C=CN/ST=Shanghai/L=Shanghai/O=MyCompany/CN=MyCompany-CA" \
  -out ca.crt

# 生成 Harbor 服务器私钥
openssl genrsa -out harbor.key 4096

# 生成证书签名请求（CSR）
openssl req -new -key harbor.key \
  -subj "/C=CN/ST=Shanghai/L=Shanghai/O=MyCompany/CN=harbor.mycompany.com" \
  -out harbor.csr

# 用 CA 签发 Harbor 证书
openssl x509 -req -in harbor.csr -CA ca.crt -CAkey ca.key \
  -CAcreateserial -out harbor.crt -days 365 -sha256

# 2. 修改 harbor.yml 开启 HTTPS
vim /opt/harbor/harbor.yml
# 取消注释 https 块并填写：
# https:
#   port: 443
#   certificate: /opt/harbor/certs/harbor.crt
#   private_key: /opt/harbor/certs/harbor.key

# 3. 重新生成配置并重启 Harbor
cd /opt/harbor
sudo ./prepare
sudo docker compose down
sudo docker compose up -d

# 4. 将 CA 证书分发到 Docker 客户端机器
# 在每台需要访问 Harbor 的机器上执行：
mkdir -p /etc/docker/certs.d/harbor.mycompany.com/
cp ca.crt /etc/docker/certs.d/harbor.mycompany.com/
systemctl restart docker

# 5. 验证 HTTPS 登录
docker login harbor.mycompany.com
# 输入账号密码，预期：Login Succeeded
```

### 3.3 可能遇到的坑

**坑1：端口 80/443 被宿主机已有 Nginx 或 Apache 占用**

- **现象**：执行 `docker compose up -d` 后 Nginx 容器启动失败，日志报 `bind: address already in use`。
- **根因**：宿主机上已运行了 Nginx/Apache 服务占用了 80 或 443 端口，Harbor 内置 Nginx 容器试图绑定相同端口时被拒绝。
- **解决方法**：
  1. 方案一（推荐）——关停宿主机原有 Web 服务，全部流量由 Harbor 内置 Nginx 接管：`systemctl stop nginx && systemctl disable nginx`
  2. 方案二——修改 `harbor.yml` 中 `http.port` 和 `https.port` 为非标准端口（如 8080/8443），然后通过宿主机 Nginx 反向代理到 Harbor 内置 Nginx。
  3. 方案三——禁用 Harbor 内置 Nginx，在 `harbor.yml` 中设置 `nginx.enabled: false`，完全由外部负载均衡器接管。此时需要在外部 LB 上手动配置 Core（8080）和 Portal（8080）的路由规则。

**坑2：Docker Compose 版本不兼容（v1 vs v2）**

- **现象**：执行 `./install.sh` 时提示 `docker-compose: command not found` 或 `unsupported compose file version`。
- **根因**：Harbor v2.12+ 要求 Docker Compose v2（`docker compose`，无横线）。旧系统可能安装了 Docker Compose v1（`docker-compose`，带横线），两个版本的 compose 文件格式和命令行语法不兼容。
- **解决方法**：
  ```bash
  # 检查当前版本
  docker compose version    # 正确格式（v2）
  docker-compose version    # 旧格式（v1），如果是这个就需要升级

  # Ubuntu/Debian 升级到 v2
  sudo apt update && sudo apt install docker-compose-v2

  # CentOS/RHEL 升级到 v2
  sudo yum remove docker-compose
  sudo curl -SL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64 \
    -o /usr/local/lib/docker/cli-plugins/docker-compose
  sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose

  # 验证升级
  docker compose version
  ```

**坑3：自签名证书被 Docker 客户端拒绝**

- **现象**：`docker login` 返回 `Error response from daemon: Get "https://harbor.mycompany.com/v2/": x509: certificate signed by unknown authority`。
- **根因**：Harbor 使用的是自签名证书，Docker 客户端的 TLS 验证不信任该证书的签发者（自签 CA）。注意：不能简单地加 `--insecure-registry` 就了事——那会让你的 Docker Daemon 接受任意不安全的连接，是一种陋习。
- **解决方法**：
  ```bash
  # 正确的做法：将自签名 CA 证书添加到 Docker 的信任 CA 列表
  DOMAIN="harbor.mycompany.com"

  # 1. 创建证书信任目录
  sudo mkdir -p /etc/docker/certs.d/$DOMAIN/

  # 2. 将 CA 证书复制到信任目录（注意：是 ca.crt 不是 harbor.crt！）
  sudo cp /opt/harbor/certs/ca.crt /etc/docker/certs.d/$DOMAIN/

  # 3. 重启 Docker Daemon 使配置生效
  sudo systemctl restart docker

  # 4. 验证登录
  docker login $DOMAIN
  ```

**坑4：`./prepare` 静默失败但未报明显错误**

- **现象**：执行 `./prepare` 后显示 `Prepared successfully`，但实际 `docker compose up -d` 后 Nginx 或 Core 容器反复重启，日志中找不到明确报错信息。
- **根因**：
  1. `https.port` 配置了数值，但 `certificate` 和 `private_key` 路径为空或路径不存在——prepare 不会对证书路径做严格校验。
  2. `hostname` 和 `external_url` 不一致，导致 Core 生成的 Token 签名地址与客户端访问地址不匹配。
  3. `data_volume` 路径磁盘空间不足——prepare 不会检查磁盘剩余空间。
- **解决方法**：
  ```bash
  # 1. 逐项校验 harbor.yml 中的关键参数
  grep -E '^hostname:|external_url:|^https:|certificate:|private_key:|data_volume:' \
    /opt/harbor/harbor.yml

  # 2. 确保证书文件存在且权限正确
  ls -la /opt/harbor/certs/harbor.crt /opt/harbor/certs/harbor.key
  sudo chmod 644 /opt/harbor/certs/harbor.crt
  sudo chmod 600 /opt/harbor/certs/harbor.key

  # 3. 确保 data_volume 磁盘剩余空间 > 10GB
  df -h $(grep '^data_volume:' /opt/harbor/harbor.yml | awk '{print $2}')

  # 4. 排查 Core 容器详细日志
  docker logs harbor-core --tail 100 2>&1
  ```

---

## 4 项目总结

### 4.1 Harbor 与主流镜像仓库全方位对比

| 对比维度 | Harbor | Docker Registry | Docker Hub Pro | Quay (Red Hat) | AWS ECR | JFrog Artifactory |
|---------|--------|----------------|----------------|----------------|---------|-------------------|
| 部署模式 | 私有化 | 私有化 | SaaS 托管 | 私有化/SaaS | SaaS 托管 | 私有化 |
| 部署复杂度 | ⭐⭐⭐ 中等 | ⭐ 极低 | 无需部署 | ⭐⭐⭐⭐ 较高 | 无需部署 | ⭐⭐⭐⭐⭐ 极高 |
| RBAC 权限粒度 | ✅ 系统角色+项目四级角色 | ❌ 无 | ❌ 仅组织 Owner/Member | ✅ 团队级+仓库级 | ✅ IAM 策略级 | ✅ 按仓库路径 RBAC |
| 漏洞扫描 | ✅ 内置 Trivy（开源） | ❌ 无 | ❌ 需 Docker Scout（$19/月/人） | ✅ 内置 Clair | ✅ 增强扫描（$1.5/镜像） | ✅ 内置 JFrog Xray |
| 跨地域复制 | ✅ Push/Pull 模式原生支持 | ❌ 无 | ❌ 无 | ✅ Geo-replication | ✅ 跨账户/跨区域复制 | ✅ 多数据中心同步 |
| 垃圾回收 (GC) | ✅ 在线/离线 GC，可定时 | ❌ 需手动执行 `bin/registry` | 透明无感知 | ✅ 自动 GC | 透明无感知 | ✅ 自动/手动 GC |
| 内容签名 | ✅ Cosign / Notary v1 | ❌ 无 | ❌ 无 | ❌ 有限支持 | ❌ 无 | ✅ GPG 签名+密钥管理 |
| P2P 分发 | ✅ Dragonfly 集成 | ❌ 无 | ❌ 无 | ❌ 无 | ❌ 无 | ❌ 无 |
| Helm Chart 仓库 | ✅ 内置 ChartMuseum | ❌ 无 | ❌ 无 | ❌ 无 | ❌ 无 | ✅ 多格式制品仓库 |
| 审计日志 | ✅ 完整（操作/用户/IP/时间） | ❌ 仅 Pull/Push 日志 | ❌ 基础日志 | ✅ 审计日志 | ✅ CloudTrail 集成 | ✅ 完整审计 |
| 镜像代理缓存 | ✅ Proxy Cache 项目类型 | ✅ 内置 pull-through cache | ❌ 无 | ❌ 无 | ✅ pull-through cache | ✅ Remote Repository |
| 配额管理 | ✅ 按项目设置存储上限 | ❌ 无 | ❌ 无 | ✅ 按组织配额 | ❌ 按仓库 10K 限制 | ✅ 按仓库/目录配额 |
| 社区活跃度 | ⭐⭐⭐⭐⭐ CNCF 毕业 | ⭐⭐⭐ 稳定但缓慢 | ⭐⭐⭐⭐⭐ 极高 | ⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ 商业支持 |
| 许可类型 | Apache 2.0 (开源免费) | Apache 2.0 (开源免费) | 订阅制 ($7+/月/人) | Apache 2.0 (部分功能收费) | 按存储+流量计费 | 商业许可 ($$$$) |
| 适合团队规模 | 10-5000 人企业 | 1-5 人小团队 | 1-50 人团队 | 50-2000 人企业 | 任意规模 AWS 用户 | 200-10000 人大型企业 |

### 4.2 优点 & 缺点分析

| 优点 | 详细说明 |
|------|---------|
| 一站式镜像治理 | 镜像存储 + 权限 + 扫描 + 复制 + 签名 + 审计，无需拼凑多个工具，运维面窄 |
| 开源免费无锁定 | Apache 2.0 协议，无社区版 vs 企业版功能阉割，CNCF 毕业级项目可持续性有保障 |
| 微服务架构可扩展 | 组件独立部署，单点故障不影响全局，异步任务可独立水平扩展 |
| 中国本土化 | 原 VMware 中国团队发起，中文文档齐全，国内社区活跃（goharbor.io 有中文站） |
| 等保合规就绪 | 审计日志 + 漏洞扫描 + 内容签名三件套，满足等保三级对镜像仓库的要求 |
| 混合云镜像分发 | 原生跨 Registry 复制，支持 Harbor↔Harbor、Harbor↔Docker Hub、Harbor↔ECR 等场景 |

| 缺点 | 详细说明 |
|------|---------|
| 部署运维有门槛 | 9 个容器组件，需要理解 Docker Compose/Helm 和基础网络/存储知识 |
| 升级需停机 | 跨大版本升级（如 v1.10 → v2.x）可能需要数据迁移，有停机窗口 |
| 资源消耗较高 | 空闲状态下 9 个容器约占用 2-3GB 内存，Trivy 扫描时额外消耗 1-3GB |
| 依赖 PostgreSQL/Redis | 内置的 DB 和缓存是单实例（社区版），生产高可用需要额外部署外部数据库集群 |
| GC 触发需手动 | 社区版不支持定时自动 GC，需要定时任务或手动触发 |
| Web UI 功能弱于 API | 部分高级功能（批量创建项目、批量修改策略）Portal 不支持，必须通过 API |

### 4.3 适用场景

- **企业内部私有镜像仓库**：核心业务容器镜像不出公司网络，满足安全合规（金融、政务、军工等敏感行业）。
- **混合云/多云镜像统一分发**：开发测试在私有云、生产在公有云，Harbor 的复制功能实现镜像跨云同步，分发延迟从数小时缩短到分钟级。
- **CI/CD 制品存储与安全门禁**：构建产物统一存入 Harbor，Trivy 扫描结果驱动部署决策——有 Critical CVE 的镜像自动阻止部署到生产环境。
- **容器化技术转型第一步**：团队从"无镜像管理"到"有镜像仓库"的关键里程碑，是后续 K8s、Helm、服务网格等技术栈的基础设施底座。
- **等保/ISO27001 合规镜像仓库**：完整审计日志 + CVE 扫描报告 + 镜像签名验签，可直接作为等保评测的镜像管理凭证。

### 4.4 不适用场景

- **个人开发者或 3 人以下小团队**：Harbor 的部署和维护成本（一台 4C8G 服务器 ≈ ¥3000/年云成本）高于直接使用 Docker Hub（免费版 1 个私有仓库）或 GitHub Container Registry（公开免费）。
- **纯静态文件/非 OCI 制品存储**：Harbor 专为 OCI 容器镜像和 Helm Chart 设计，不适合作为通用 Artifactory（如 Maven JAR、npm 包、Python Wheel）。如需统一制品管理，考虑 JFrog Artifactory 或 Sonatype Nexus。
- **极端高并发拉取（>10000 QPS）**：Harbor 社区版单 Registry 实例的处理能力约为 2000-3000 concurrent push/pull。超过此量级需要配合 P2P 分发（Dragonfly）或多实例 Registry 部署（企业版功能）。

### 4.5 常见踩坑经验（生产故障案例）

| 故障案例 | 故障现象 | 根因分析 | 修复措施与教训 |
|---------|---------|---------|--------------|
| **"消失的镜像层"**：某金融公司生产 Harbor 运行 6 个月后，部分旧版本镜像（>30 天前推送）突然拉取失败，报 `MANIFEST_UNKNOWN` 错误 | 开发者能查询到标签存在，但 `docker pull` 返回 manifest unknown | 运维在配置标签保留策略时设了`保留最近 5 个制品`，且 GC 定时执行（每周日 3:00）。旧版本的 blob 在标签被清理后，GC 回收了其对应的 blob 数据。但 Kubernetes 中有两个服务的 `imagePullPolicy: IfNotPresent` 仍在使用被清理的旧镜像——Node 上本地有缓存所以没报错，但新 Node 扩容时拉不到。 | ①生产环境镜像使用不可变性规则（`release-*` 标签禁止覆盖和自动清理）；②任何标签清理策略前，先在预发布环境验证；③GC 执行前设置 Harbor 为只读模式，避免清理中 Push 操作导致 blob 损坏。 |
| **"HTTPS 配置不一致导致的 Token 401"**：某电商公司运维在 harbor.yml 中配置了 `hostname` 为内网 IP `10.0.1.50`，但 `external_url` 配置为外网域名 `harbor.example.com` | 开发从办公室（走外网域名）`docker login harbor.example.com` 成功，但 `docker push` 返回 401 Unauthorized | Docker Client 登录时用的是 `external_url`（harbor.example.com）发起请求。但 Core 服务在签发 Bearer Token 时使用的是 `hostname` 配置（10.0.1.50）作为 Token 的 `access` 字段的 `realm` 地址。Docker Client 拿到 Token 后，发现 Token 签发的 realm 地址（10.0.1.50）与它实际访问的地址（harbor.example.com）不匹配，认为 Token 非法。 | ①`hostname` 必须是 Docker 客户端能解析和访问的地址——如果客户端走外网，hostname 就必须是外网域名；②`external_url` 用于 Portal 和通知邮件中的链接；③测试环境最简单的做法：`hostname` = `external_url` = 服务器的内网 IP，不用域名。 |
| **"GC 期间镜像损坏"**：某物流公司运维在 Harbor 高峰时段（上午 10:30）手动触发了 GC，未设置只读模式 | GC 执行约 8 分钟后，运维发现部分正在被 Jenkins CI 推送到 Harbor 的镜像在 push 完成后立即 `docker pull` 报错 `unknown blob` | GC 执行过程中会先标记可达 blob（被至少一个 manifest 引用的 blob），然后删除未标记的 blob。如果此时有新的 Push 操作正在写入 blob，GC 可能在 blob 写入完成但 manifest 尚未关联的时刻将其标记为"不可达"并删除。这是 OCI Distribution Spec 的"时间窗口"问题。 | ①**GC 前必须将 Harbor 设为只读模式**（系统管理 → 配置 → 仓库只读 ✔）；②**永远不要在工作时间执行 GC**，设置为凌晨 2:00-4:00 自动执行；③GC 执行期间监控 Registry 错误日志 `docker logs registry --tail 50 -f`；④如果 GC 导致损坏，从备份恢复 —— 必须有备份机制。 |

### 4.6 注意事项

1. **`hostname` 必须可解析**：这个值会用于签发内部 Token 的域名，如果是 IP 地址，请确保所有 Docker 客户端能 ping 通。如果用域名，请配置 DNS 或 `/etc/hosts`。
2. **`harbor_admin_password` 仅在首次安装时生效**：安装完成后，如果通过 Portal 修改了 admin 密码，再次执行 `./prepare` 和重启不会自动覆盖。如果忘记了 admin 密码，需要直接操作 PostgreSQL：`UPDATE harbor_user SET password='...' WHERE username='admin';`。
3. **`data_volume` 目录的权限必须是 10000:10000**：Registry 容器内以 uid 10000 运行，如果宿主机目录权限不对，push 时会报 `500 Internal Server Error`。执行 `chown -R 10000:10000 /data/registry`。
4. **自签证书不可用于 3 台以上机器**：自签 CA 证书需要分发到每台 Docker 客户端机器的 `/etc/docker/certs.d/` 目录。机器多了之后，证书轮换（每年过期）会变成运维噩梦。生产环境推荐使用 Let's Encrypt 自动化免费证书，或公司内部 CA 签发的证书。
5. **PostgreSQL 密码变更需同步**：如果你改了 `harbor.yml` 中的 `database.password`，直接执行 `./prepare` 不会自动更新 PostgreSQL 中已存在的密码。需要手动登入 PostgreSQL 执行 `ALTER ROLE postgres WITH PASSWORD 'new_password';` 同步修改，否则 Core 连接 DB 会持续失败。
6. **Helm 安装和 Docker Compose 安装的配置文件不通用**：`harbor.yml` 只用于 Docker Compose 安装。Helm 安装使用独立的 `values.yaml`，参数名称和结构完全不同。从 Docker Compose 迁移到 Helm 时，不能直接复用配置文件。
7. **升级跨大版本前务必查看 Migration Guide**：Harbor 的大版本升级（如 v1.10→v2.0→v2.12）可能涉及数据库 schema 迁移、配置参数重命名、废弃功能移除等破坏性变更。官方提供了详细的 Migration Guide，升级前务必逐条阅读。

### 4.7 思考题

**问题 1**：Harbor 的 Core 服务进程挂了（容器停止），已经推送到 Registry 的镜像还能被 `docker pull` 拉取吗？如果可以，为什么？如果不可以，拉取流程在哪个环节被阻断？请结合 Harbor 的六层架构模型分析。（提示：Core 在 Pull/Push 流程中主要承担什么角色？Docker Client 与 Registry 之间是否必然经过 Core？）

**问题 2**：公司已有 LDAP/AD 系统管理 500 人的组织架构（按部门分 OU），如何让 Harbor 直接复用这套认证体系，避免在 Harbor 中手动创建 500 个用户账号？如果 LDAP 配置错误导致 Harbor 无法认证，管理员（admin）还能通过本地数据库账号登录 Portal 吗？（提示：Harbor 支持多种 Auth Mode——`db_auth`、`ldap_auth`、`oidc_auth`，在 `harbor.yml` 的 `auth_mode` 参数中切换，但 admin 账号永远走本地数据库验证。）

---

> **下一章预告**：第 2 章将手把手带你完成 Harbor 的三种安装模式（在线/离线/Helm），深度解析 `harbor.yml` 中 50+ 个关键参数的底层含义、调优策略和生产环境 Checklist，并解决"端口冲突""证书信任""Docker Compose 兼容性"等高频部署故障。
