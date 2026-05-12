# 第5章：Docker/Containerd 镜像推送与拉取实战

## 1 项目背景

**智联物流科技**（智联物流，一家估值百亿的物流 SaaS 独角兽）在 2025 年全面启动容器化改造。CTO 老梁拍板：用 Harbor 替换用了三年的 Docker Hub 付费订阅，自建私有镜像仓库。三个月内，从 Dev、Staging 到 Prod 全线迁移完毕。然而，在看似平滑的迁移之后，镜像推送与拉取环节暴露出一连串棘手问题。

**痛点一：docker login 的三重认证地狱。** 开发小陈在一台全新的 Docker Desktop（macOS）工作站上执行 `docker login harbor.zl-logistics.com`，输入正确的用户名密码后，终端冰冷地返回：`x509: certificate signed by unknown authority`。换成 `--tls-verify=false` 再试，又报 `server gave HTTP response to HTTPS client`。小陈折腾两个半小时后终于搞明白——自签名的 CA 根证书没有安装到 macOS 的 Keychain 里，而 Docker Desktop 不会像浏览器一样弹出"是否信任此网站"。更要命的是，同一个 Harbor 地址在生产环境 K8s Node 上用的是另一套证书（通配符 `*.zl-logistics.com`），Node 上的 Containerd 配置又完全不同。一台 Dev 机器、一个 K8s 集群、一台 CI Runner —— 三台机器的证书信任链配置各不相同，小陈花了整整一天才统一打通。

**痛点二：推送 1.2GB Java 镜像时的"80% 诅咒"。** 基础架构团队构建了一个包含 Spring Boot 应用 + JRE 17 + AI 模型文件的镜像 `order-prediction-service:v1.5.0`，总大小 1.2GB。CI 流水线在 Jenkins Runner 上构建完成后，`docker push` 传输到 60%-80% 区间时反复失败。日志显示三种错误交替出现：`blob upload unknown: 413 Request Entity Too Large`（WAF 网关的请求体限制）、`connection reset by peer`（公司自研的 Nginx 中间代理 `client_body_timeout` 仅为 60 秒）、`unexpected EOF`（某次推送时上游网络闪断 0.5 秒导致 TCP 连接断开，但 Docker 客户端没有断点续传机制）。最终排查出三个节点需要同时调整：WAF 的 `request_body_max_size`、中间 Nginx 的 `proxy_read_timeout` 和 `client_max_body_size`、以及 Harbor 自身 Nginx 容器的对应参数。

**痛点三：Mac ARM + Linux x86 混合环境的"标签灾难"。** 物流调度团队 40 名开发者中有 15 人使用 MacBook Pro M3（ARM64），其余使用 Ubuntu 工作站（x86_64）。同一个 `dispatch-service:latest` 标签在 Mac 上构建推送的镜像是 ARM64 架构，部署到 x86 服务器上直接 `exec format error`。更诡异的是，CI 流水线（运行在 x86 Runner 上）构建的 `dispatch-service:latest` 是 AMD64 版本，但 Mac 开发者在 CI 之前手动推送了一份 ARM64 版的 `latest`，时间戳更晚，结果覆盖了 CI 构建的正确版本。团队陷入了"谁最后 push 谁就决定 latest 的架构"的混乱局面——ARM 节点和 x86 节点各取各的 `latest`，谁也搞不清哪个架构是"真正的最新版"。

**痛点四：内网机器拉取镜像的"最后一公里"。** 智联物流在机场物流中心部署了 15 台离线 K8s Node（无公网访问能力），这些机器通过公司内网专线连接到总部的 Harbor，但是 Containerd 的 `config.toml` 中配置了 `pause:3.9` 镜像从 `registry.k8s.io` 拉取，而该域名在离线环境无法解析。运维曾试图在 Harbor 中创建一个 Proxy Cache 项目来代理 `registry.k8s.io`，但发现 Harbor 的 Proxy Cache 特性在早期版本中有 Bug——对于 Manifest List 类型的多架构镜像，Cache 仅缓存了默认架构的版本，导致 ARM64 节点拉取到 x86 版本的 pause 容器。

本章将完整覆盖 Docker 和 Containerd 两种主流容器运行时与 Harbor 的认证、推送、拉取全链路，并深入多架构镜像（Manifest List / OCI Image Index）的构建、管理与分发，确保从开发机到 K8s 生产集群的全链路贯通。

---

## 2 项目设计——剧本式交锋对话

**场景：智联物流科技 4 楼茶水间，小陈桌上的第四杯美式已经凉了。小胖端着一碗螺蛳粉路过。**

**小胖**（拉过椅子坐下）："小陈，你这黑眼圈比我们食堂的红烧肉还重。Docker login 还没搞定？不就是输个用户名密码吗？怎么比我在拼多多抢券还费劲？"

**小陈**（苦笑）："真不是我想复杂。同一个 Harbor，我的 Mac 登录不上，张哥的 Ubuntu 能登，CI 的 Runner 也能登。我怀疑 Harbor 是不是针对我的机器有偏见。"

**小白**（从门口走进来，手里拿着 iPad Pro）："我查了一下，你的 Mac 上 Docker Desktop 走的是 `/etc/docker/certs.d/` 的 Linux 路径逻辑，但实际上 macOS 上 Docker Desktop 跑在一个轻量级 Linux VM 里，证书路径虽然一样，但证书信任链还依赖 macOS Keychain。你是不是既没导入 Keychain，也没往 VM 里拷证书？"

**小胖**（吸了一口螺蛳粉）："等等，你们说的证书信任链是什么？我理解的 HTTPS 就是浏览器地址栏那个绿色小锁。怎么到了 Docker 这里就变成'地狱难度'了？"

**大师**（端着一杯龙井走进来）："小胖，你这个问题问到了根上。Docker 的证书信任模型和浏览器是完全一样的原理——都是 X.509 PKI 体系——但 Docker 给你留了更灵活的配置入口。"

"来，我把 Harbor 的信任链模型重新画一遍，这次加上你们踩的坑。"

```
                    ┌─────────────────────────────────────┐
                    │          信任链全景                  │
                    └─────────────────────────────────────┘

Docker Client ──HTTPS──►  Harbor Nginx (TLS Terminator) ──HTTP──► Harbor Core
      │                          │
      │  ① 证书验证路径          │  ② Nginx 证书配置
      │  /etc/docker/certs.d/    │  harbor.yml 中定义
      │     <registry-host>/     │  certificate + private_key
      │     ├─ ca.crt (CA 根)    │
      │     ├─ <host>.cert       │  harbor.yml 同时支持:
      │     └─ <host>.key        │  https.ssl_cert + ssl_cert_key
      │                          │
      │  ③ 非标准端口处理         │  ④ 中间代理证书
      │  若 Harbor 在非 443 端口  │  中间 Nginx/WAF 也需要
      │  目录名为 host:port       │  同一张证书或通配符证书
```

"Docker 客户端的证书验证逻辑其实很聪明：当你 `docker pull harbor.zl-logistics.com/myimage` 时，Docker 首先看 `/etc/docker/certs.d/harbor.zl-logistics.com/` 目录下有没有 `ca.crt`——如果有，就用它作为信任锚点，完全不走系统级别的 CA Bundle。这就是为什么你的 Mac 系统证书是信任的，但 Docker 不认——因为 Docker 走了自己的证书目录。"

**技术映射**：Docker 客户端遵循 X.509 标准的证书验证链。`/etc/docker/certs.d/<registry-host>/` 目录中的证书文件命名规则如下：`ca.crt` 作为信任的 CA 根证书（trust anchor），`<host>.cert` 作为客户端证书（用于双向 TLS / mTLS，Harbor 默认不要求），`<host>.key` 为对应的私钥。如果 Harbor 监听在非标准端口（如 8443），则目录名应包含端口：`harbor.zl-logistics.com:8443`。

**小胖**（放下筷子）："那我把 ca.crt 往那个目录一丢就完事了？怎么听起来比我想象的简单？"

**大师**："那是方案一——推荐方案。但你还需要知道另外两种方案，因为实际场景千变万化："

"**方案 A（生产级——证书信任）**：把你的自签名 CA 根证书放到 Docker 的证书信任目录中。这是最安全的做法，完整保留了 TLS 的证书校验能力。"

```bash
# Linux 生产环境——标准路径
sudo mkdir -p /etc/docker/certs.d/harbor.zl-logistics.com/
sudo cp /data/certs/zl-logistics-ca.crt /etc/docker/certs.d/harbor.zl-logistics.com/ca.crt
sudo systemctl daemon-reload && sudo systemctl restart docker

# macOS Docker Desktop——两步到位
# 步骤1：导入 macOS Keychain
sudo security add-trusted-cert -d -r trustRoot \
  -k /Library/Keychains/System.keychain /tmp/zl-logistics-ca.crt

# 步骤2：同时拷贝到 Docker Desktop 的 Linux VM 内部
# Docker Desktop 4.x+ 在 Settings → Docker Engine 中配置：
# {
#   "registry-mirrors": [],
#   "insecure-registries": []  // 留空，用证书方案
# }
# 然后从 Mac 端执行：
mkdir -p ~/.docker/certs.d/harbor.zl-logistics.com/
cp /tmp/zl-logistics-ca.crt ~/.docker/certs.d/harbor.zl-logistics.com/ca.crt
```

"**方案 B（仅测试/离线环境——跳过验证）**：直接配置 Insecure Registry。Docker 不会验证 Harbor 的 TLS 证书，相当于浏览器里点了'仍然继续'。"

```json
// /etc/docker/daemon.json
{
  "insecure-registries": [
    "harbor.zl-logistics.com",
    "harbor.zl-logistics.com:8443"
  ]
}
// 修改后必须执行：sudo systemctl daemon-reload && sudo systemctl restart docker
```

"**方案 C（企业级——企业 CA 统一签发）**：如果你们公司有内部的 CA 服务器（如通过 HashiCorp Vault 的 PKI 引擎或 EJBCA），Harbor 的 Nginx 证书由企业 CA 签发，所有客户端只要信任企业 CA 根证书，任何 Harbor 实例的证书都自动被信任——一劳永逸。"

**小白**（在 iPad 上快速记笔记）："我有个 edge case —— Harbor 部署在 Kubernetes 里，前端挂了公司的 Istio Ingress Gateway，TLS 终结在 Ingress 上，Harbor Core 收到的是 HTTP 明文流量。这时候 Docker 客户端验证的是 Ingress Gateway 的证书，而不是 Harbor Nginx 的证书。如果两个证书不是一个 CA 签发的呢？"

**大师**："小白你这个问题非常好，这是实际中经常被忽略的。TLS 终结在哪里，Docker 客户端就验证哪里的证书。在 Istio Ingress 场景下，流程是这样的："

```
Docker Client ──TLS──► Istio Ingress (TLS Terminator) ──HTTP──► Harbor Core Service
      │                          │
      │  验证的是 Ingress 的      │  证书由 cert-manager
      │  TLS 证书（非 Harbor 的） │  通过 Let's Encrypt 签发
      │                          │  或者由企业 CA 签发
      └──────────────────────────┘
```

"结论：**Docker 客户端需要信任 Ingress 的 CA，而不是 Harbor harbor.yml 中的 CA。** 很多人在这里搞混，拿着 Harbor 的证书去配 Docker，结果发现还是登录不上去。"

**技术映射**：TLS 终结（TLS Termination）是指 TLS 加密连接在进入内网前被解密，之后以明文 HTTP 在内部网络中传输。在 Kubernetes 中，Ingress Controller 充当 TLS Terminator。Docker 客户端的证书验证仅与 TLS 终结点（通常是反向代理）交互，不会穿透到后端服务。这意味着：`/etc/docker/certs.d/harbor.zl-logistics.com/ca.crt` 中放的应该是 Ingress 证书的 CA，不是 Harbor 内部 Nginx 的 CA。

**小胖**（擦了擦嘴）："好家伙，学废了学废了。那现在我的 Mac 能登录了，但是 Containerd 呢？咱们的 K8s 集群不是全切了 Containerd 吗？那个玩意儿的配置怎么又不一样了？"

**大师**："Containerd 的配置——这确实是个'新手杀手'。它不像 Docker 有 `insecure-registries` 这种偷懒选项，Containerd 强制你必须正确地配置证书。"

"来看 Containerd 的配置文件结构——它从 v1.5 到 v2.0 配置格式还变过，更加容易搞混："

**Containerd v2 配置格式（推荐，containerd 1.6+）：**

```toml
# /etc/containerd/config.toml
version = 2

# 方式一：集中式目录配置（推荐）
[plugins."io.containerd.grpc.v1.cri".registry]
  config_path = "/etc/containerd/certs.d"

# 然后在 /etc/containerd/certs.d/harbor.zl-logistics.com/ 目录下创建 hosts.toml：
# server = "https://harbor.zl-logistics.com"
# [host."https://harbor.zl-logistics.com"]
#   capabilities = ["pull", "resolve"]
#   ca = "/etc/containerd/certs.d/harbor.zl-logistics.com/ca.crt"
#   [host."https://harbor.zl-logistics.com".header]
#     authorization = ["Basic xxxxxx"]
```

**Containerd v1 配置格式（旧版，但仍广泛使用）：**

```toml
[plugins."io.containerd.grpc.v1.cri".registry.configs]
  [plugins."io.containerd.grpc.v1.cri".registry.configs."harbor.zl-logistics.com".tls]
    ca_file = "/etc/containerd/certs.d/harbor.zl-logistics.com/ca.crt"
    insecure_skip_verify = false  # 生产环境绝不要设 true
  [plugins."io.containerd.grpc.v1.cri".registry.configs."harbor.zl-logistics.com".auth]
    username = "robot$order-platform+ci-builder"
    password = "eyJhbGciOi..."
```

"注意一个关键区别：**Docker 的证书目录是按 `<host>/ca.crt` 组织，Containerd v2 是按 `<host>/hosts.toml` 组织**。两种格式完全不可互换。很多人在 Containerd 的证书目录放了一个 `ca.crt` 文件就以为万事大吉了，但实际上 Containerd v2 根本不读取这个文件——它读 `hosts.toml`。"

**小胖**："那还有没有更简单的？我就想在本地开发的时候快速验证，不要搞这么复杂的证书配置行不行？"

**大师**："有——**nerdctl**。它是 Containerd 的 Docker 兼容 CLI，用法几乎和 Docker 一模一样，但底层直接操作 Containerd。对于本地开发和调试来说是最友好的。"

```bash
# nerdctl 和 Docker 命令对比——几乎相同
nerdctl login harbor.zl-logistics.com -u admin -p Harbor12345
nerdctl pull harbor.zl-logistics.com/order-platform/hello-app:v1.0.0
nerdctl build -t harbor.zl-logistics.com/order-platform/hello-app:v1.0.0 .
nerdctl push harbor.zl-logistics.com/order-platform/hello-app:v1.0.0
```

"但 nerdctl 有个陷阱：它读取的凭证存储位置和 Docker 不同。Docker 存在 `~/.docker/config.json`，而 nerdctl 默认存在 `~/.config/nerdctl/config.json`。如果你同时用 Docker 和 nerdctl 操作同一个 Harbor，要注意登录两次。"

**小白**："那多架构镜像呢？我们现在的混合架构集群，同一个 `latest` 标签在 x86 和 ARM 节点上拉到的架构不同，这个怎么统一解决？"

**大师**："这就要请出 **Docker Manifest List**（OCI 术语叫 **Image Index**）——这是多架构镜像的'总菜单'。"

"你可以把它想象成一个智能餐厅套餐——客⼈来了，服务员（Docker Client）看一眼客人的需求（CPU 架构），自动从后厨（Registry）端出适合的那道菜（对应架构的镜像）。"

```
order-service:latest (Manifest List / OCI Image Index)
├── linux/amd64  → sha256:a1b2c3d4e5... (Intel/AMD 64 位)
│   ├── Layer 1: alpine 基础层
│   ├── Layer 2: JRE 17 (x86_64)
│   └── Layer 3: Spring Boot JAR
│
├── linux/arm64  → sha256:f6e7d8c9b0... (Apple M1/M2/M3, ARM 服务器)
│   ├── Layer 1: alpine 基础层 (ARM64)
│   ├── Layer 2: JRE 17 (ARM64)
│   └── Layer 3: Spring Boot JAR
│
└── linux/s390x  → sha256:... (IBM Z 大型机，金融行业可能用到)
```

"**推送 Manifest List 到 Harbor 后的实际存储行为**：Harbor 会把 Manifest List 元数据和两个子 Manifest（amd64 + arm64）以及它们引用的所有 Layer Blob 一并存储。在 Harbor 的数据库中，Manifest List 是一个独立行，两个子 Manifest 也是独立行，通过 `subject_artifact_id` 外键关联。"

**技术映射**：OCI Image Index（即 Manifest List）是 OCI 分发规范的核心概念。索引文件中包含一个 `manifests` 数组，每个元素描述一个平台（OS + Architecture），并指向该平台对应的 Manifest Digest。Harbor 从 v2.0 开始原生支持 Manifest List 的存储和分发——`docker buildx` 推送时，Harbor 会同时接收 Manifest List 和子 Manifest。`docker pull` 时，Harbor 根据请求头中的 `Accept` 优先级返回最匹配的架构。

**小白**："但我有一个更棘手的问题——如果我们的 CI 在 x86 Runner 上构建了 `order-service:v1.5.0` 的 AMD64 版本，同时也构建了 ARM64 版本并生成了 Manifest List，但后来运维在 Mac 上手动构建并推送了一个 ARM64 版本的 `order-service:v1.5.0`（覆盖了 CI 生成的 ARM64 子 Manifest），那么 Manifest List 中的 ARM64 引用会怎样？"

**大师**："这个场景很真实。答案是：**Harbor 的 Manifest List 指向的是特定 digest，而不是最新版本的 ARM64 镜像。** 运维手动推送的 ARM64 镜像 digest 跟 CI 生成的 digest 完全不同，所以 Manifest List 仍然指向旧的 ARM64 digest。但是——如果你开启了'标签不可变性'规则（第 6 章会详解），这个覆盖行为会在 push 时就被拒绝了。所以这是两个机制的组合防御。"

---

## 3 项目实战

### 3.1 环境准备

| 组件 | 版本 | 说明 |
|------|------|------|
| Harbor | v2.12 | 已部署，HTTPS 启用（自签名证书） |
| Docker Engine | 24.0+ | 包含 BuildKit 和 buildx 插件 |
| Docker Compose | v2.20+ | 用于 Harbor 本地部署管理 |
| Containerd | 1.7+ | K8s 1.28+ 默认容器运行时 |
| crictl | v1.28+ | Containerd 的 CRI 调试工具 |
| nerdctl | 1.7+ | Containerd 的 Docker 风格 CLI |
| buildx | v0.12+ | 多架构构建插件 |
| QEMU | 8.0+ | 非本机架构的模拟器（buildx 依赖） |

### 3.2 Docker 客户端认证全流程

**步骤一：生成自签名 CA 与 Harbor 服务器证书**

> **步骤目标**：创建一套完整的自签名证书体系，使 Harbor 能以 HTTPS 提供服务。

```bash
# ================================================================
# 第一步：创建 CA 根证书（整个组织的信任锚点）
# ================================================================
# 生成 CA 私钥（4096 位 RSA）
openssl genrsa -out zl-ca.key 4096

# 生成自签名 CA 根证书（有效期 10 年）
openssl req -x509 -new -nodes \
  -key zl-ca.key \
  -sha256 -days 3650 \
  -out zl-ca.crt \
  -subj "/C=CN/ST=Guangdong/L=Shenzhen/O=ZhiLianLogistics/OU=Platform/CN=ZhiLian Root CA"

# 验证 CA 证书内容
openssl x509 -in zl-ca.crt -text -noout | head -15
# 预期输出：
# Certificate:
#     Data:
#         Version: 3 (0x2)
#         Serial Number: ...
#         Signature Algorithm: sha256WithRSAEncryption
#         Issuer: C=CN, ST=Guangdong, L=Shenzhen, O=ZhiLianLogistics, CN=ZhiLian Root CA

# ================================================================
# 第二步：生成 Harbor 服务器证书（含 SAN）
# ================================================================
# 注：SAN (Subject Alternative Name) 是关键——Docker 验证的正是 SAN 中的域名
# 如果 Harbor 有多个域名或 IP，必须全部写入 SAN

cat > harbor.ext <<'EOF'
authorityKeyIdentifier=keyid,issuer
basicConstraints=CA:FALSE
keyUsage = digitalSignature, nonRepudiation, keyEncipherment, dataEncipherment
extendedKeyUsage = serverAuth
subjectAltName = @alt_names

[alt_names]
DNS.1 = harbor.zl-logistics.com
DNS.2 = harbor-registry.zl-logistics.com
DNS.3 = *.zl-logistics.com
IP.1 = 192.168.10.50
EOF

# 生成 Harbor 服务器私钥
openssl genrsa -out harbor.zl-logistics.com.key 2048

# 生成证书签名请求 (CSR)
openssl req -new \
  -key harbor.zl-logistics.com.key \
  -out harbor.zl-logistics.com.csr \
  -subj "/C=CN/ST=Guangdong/L=Shenzhen/O=ZhiLianLogistics/OU=Platform/CN=harbor.zl-logistics.com"

# 用 CA 签署服务器证书（有效期 2 年）
openssl x509 -req \
  -in harbor.zl-logistics.com.csr \
  -CA zl-ca.crt \
  -CAkey zl-ca.key \
  -CAcreateserial \
  -out harbor.zl-logistics.com.crt \
  -days 730 \
  -sha256 \
  -extfile harbor.ext

# 验证服务器证书的 SAN 字段
openssl x509 -in harbor.zl-logistics.com.crt -text -noout | grep -A 3 "Subject Alternative Name"
# 预期输出：
# X509v3 Subject Alternative Name:
#     DNS:harbor.zl-logistics.com, DNS:harbor-registry.zl-logistics.com,
#     DNS:*.zl-logistics.com, IP Address:192.168.10.50

# ================================================================
# 第三步：配置 harbor.yml 使用自签名证书
# ================================================================
# 编辑 harbor.yml：
# hostname: harbor.zl-logistics.com
# https:
#   port: 443
#   certificate: /data/certs/harbor.zl-logistics.com.crt
#   private_key: /data/certs/harbor.zl-logistics.com.key
```

**步骤二：Docker 客户端信任 Harbor 自签名 CA**

> **步骤目标**：让 Docker 引擎信任 Harbor 的自签名 CA 证书，实现 HTTPS 安全连接。

```bash
# ==================== Linux 环境 ====================
# 创建 Docker 的 CA 信任目录（目录名必须与 Harbor hostname 精确匹配）
sudo mkdir -p /etc/docker/certs.d/harbor.zl-logistics.com/

# 复制 CA 根证书（注意：是 ca.crt 不是 server.crt）
sudo cp zl-ca.crt /etc/docker/certs.d/harbor.zl-logistics.com/ca.crt

# 重启 Docker 守护进程加载配置
sudo systemctl daemon-reload
sudo systemctl restart docker

# 验证 Docker 状态
sudo systemctl status docker | head -5
# 预期输出：Active: active (running) since ...

# ==================== macOS Docker Desktop ====================
# 方案一：通过 Keychain 信任
sudo security add-trusted-cert -d -r trustRoot \
  -k /Library/Keychains/System.keychain zl-ca.crt

# 方案二：通过 Docker Desktop GUI
# Settings → Resources → File Sharing → 添加 ca.crt 所在目录
# 然后拷贝到 Docker Desktop 内部的 Linux VM：
# 在 Mac 终端执行：
mkdir -p ~/.docker/certs.d/harbor.zl-logistics.com/
cp zl-ca.crt ~/.docker/certs.d/harbor.zl-logistics.com/ca.crt

# ==================== Windows WSL2 + Docker Desktop ====================
# WSL2 内：
sudo mkdir -p /etc/docker/certs.d/harbor.zl-logistics.com/
sudo cp /mnt/c/Users/$USER/zl-ca.crt /etc/docker/certs.d/harbor.zl-logistics.com/ca.crt
```

**步骤三：docker login 验证与凭证持久化**

> **步骤目标**：完成 Docker 到 Harbor 的认证登录，验证凭证存储和 Token 机制。

```bash
# 执行登录（输入 Harbor 用户名和密码）
docker login harbor.zl-logistics.com
# 交互式输入：
# Username: admin
# Password:
# Login Succeeded

# 查看存储的凭证（Base64 编码，不是明文）
cat ~/.docker/config.json
```

预期输出：

```json
{
  "auths": {
    "harbor.zl-logistics.com": {
      "auth": "YWRtaW46SGFyYm9yMTIzNDU="
    }
  },
  "credsStore": "desktop"
}
```

```bash
# 验证凭证是否有效——尝试查询 Harbor API
curl -u admin:Harbor12345 https://harbor.zl-logistics.com/api/v2.0/projects | jq '.[].name'
# 预期输出：
# "order-platform"
# "payment-platform"
# "shared-base"

# 查看 Docker 登录后使用的 Token 类型
docker logout harbor.zl-logistics.com
# 输出：Removing login credentials for harbor.zl-logistics.com

# 使用机器人账户登录（推荐用于 CI/CD）
docker login harbor.zl-logistics.com \
  -u 'robot$order-platform+ci-builder' \
  -p 'eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9...'
# 输出：Login Succeeded
# 注意：机器人账户名格式为 "robot$<project>+<name>"，$ 和 + 是组成部分
```

**步骤四：构建、标记并推送镜像**

> **步骤目标**：从零构建测试应用镜像，推送到 Harbor 并验证全过程。

```bash
# ================================================================
# 创建测试应用
# ================================================================
mkdir -p /tmp/harbor-demo && cd /tmp/harbor-demo

cat > Dockerfile <<'DOCKERFILE'
# 使用特定版本的 Alpine 基础镜像（而非 latest，保证构建可复现）
FROM alpine:3.19.1

# 添加构建元数据
LABEL org.opencontainers.image.title="Harbor Demo App"
LABEL org.opencontainers.image.version="1.0.0"
LABEL org.opencontainers.image.description="A demo application for Harbor push/pull testing"
LABEL org.opencontainers.image.vendor="ZhiLian Logistics"
LABEL org.opencontainers.image.created="2025-03-15T10:00:00+08:00"

# 安装 curl 用于健康检查
RUN apk add --no-cache curl ca-certificates tzdata

# 创建应用目录和非 root 用户
RUN addgroup -S appgroup && adduser -S appuser -G appgroup
WORKDIR /app

# 创建健康检查脚本
RUN echo '#!/bin/sh' > /healthcheck.sh && \
    echo 'curl -f http://localhost:8080/health || exit 1' >> /healthcheck.sh && \
    chmod +x /healthcheck.sh

# 创建启动脚本
RUN echo '#!/bin/sh' > /entrypoint.sh && \
    echo 'echo "Hello from Harbor Demo App v1.0.0"' >> /entrypoint.sh && \
    echo 'echo "Hostname: $(hostname)"' >> /entrypoint.sh && \
    echo 'echo "Architecture: $(uname -m)"' >> /entrypoint.sh && \
    echo 'echo "OS: $(cat /etc/os-release | head -1)"' >> /entrypoint.sh && \
    echo 'while true; do sleep 3600; done' >> /entrypoint.sh && \
    chmod +x /entrypoint.sh

HEALTHCHECK --interval=30s --timeout=3s --retries=3 CMD /healthcheck.sh
EXPOSE 8080
USER appuser
ENTRYPOINT ["/entrypoint.sh"]
DOCKERFILE

# ================================================================
# 构建镜像
# ================================================================
# 使用 --no-cache 确保完全从零构建（生产环境 CI 建议不加此参数以利用缓存层）
docker build --no-cache \
  -t harbor.zl-logistics.com/order-platform/hello-app:v1.0.0 \
  .

# 预期构建输出（关键信息）：
# [+] Building 12.3s (11/11) FINISHED
#  => [1/6] FROM docker.io/library/alpine:3.19.1@sha256:...
#  => [2/6] RUN apk add --no-cache curl ca-certificates tzdata
#  => [3/6] RUN addgroup -S appgroup && adduser -S appuser -G appgroup
#  => [4/6] WORKDIR /app
#  => [5/6] COPY healthcheck.sh /healthcheck.sh
#  => [6/6] COPY entrypoint.sh /entrypoint.sh
#  => exporting to image
#  => => naming to harbor.zl-logistics.com/order-platform/hello-app:v1.0.0

# 查看构建的镜像详情
docker images harbor.zl-logistics.com/order-platform/hello-app:v1.0.0
# 预期输出：
# REPOSITORY                                          TAG      IMAGE ID       SIZE
# harbor.zl-logistics.com/order-platform/hello-app    v1.0.0   d4e5f6a7b8c9   12.5MB

# 查看镜像的层结构
docker image history harbor.zl-logistics.com/order-platform/hello-app:v1.0.0
# 预期输出（摘要）：
# IMAGE          CREATED          CREATED BY                                      SIZE
# d4e5f6a7b8c9   2 minutes ago    ENTRYPOINT ["/entrypoint.sh"]                   0B
# ...            2 minutes ago    HEALTHCHECK CMD /healthcheck.sh                 0B
# ...            2 minutes ago    RUN echo '#!/bin/sh' > /entrypoint.sh          371B
# ...            2 minutes ago    RUN apk add --no-cache curl ca-certificates     8.2MB
# ...            2 weeks ago      /bin/sh -c #(nop) CMD ["/bin/sh"]               0B
# ...            2 weeks ago      /bin/sh -c #(nop) ADD file:...                  4.3MB

# ================================================================
# 打标签并推送
# ================================================================
# 打上 latest 标签
docker tag harbor.zl-logistics.com/order-platform/hello-app:v1.0.0 \
           harbor.zl-logistics.com/order-platform/hello-app:latest

# 推送指定版本
docker push harbor.zl-logistics.com/order-platform/hello-app:v1.0.0
```

推送过程详细输出：

```
The push refers to repository [harbor.zl-logistics.com/order-platform/hello-app]
d4e5f6a7b8c9: Preparing
a1b2c3d4e5f6: Preparing
7a8b9c0d1e2f: Preparing
d4e5f6a7b8c9: Pushed
a1b2c3d4e5f6: Pushed
7a8b9c0d1e2f: Layer already exists
v1.0.0: digest: sha256:8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9 size: 942
```

```bash
# 推送 latest
docker push harbor.zl-logistics.com/order-platform/hello-app:latest
# 到 Harbor Portal 中查看 → Projects → order-platform → Repositories → hello-app
# 应该看到两个标签指向同一个 digest
```

**步骤五：拉取验证与镜像运行**

> **步骤目标**：从不同机器拉取 Harbor 中的镜像并运行，验证完整的镜像分发链路。

```bash
# ================================================================
# 场景一：本地验证（删除本地镜像后重新拉取）
# ================================================================
# 先删除本地镜像
docker rmi harbor.zl-logistics.com/order-platform/hello-app:v1.0.0
docker rmi harbor.zl-logistics.com/order-platform/hello-app:latest

# 确认已删除
docker images | grep hello-app
# 预期输出：（空，表示已删除）

# 重新拉取
docker pull harbor.zl-logistics.com/order-platform/hello-app:v1.0.0
# 输出：
# v1.0.0: Pulling from order-platform/hello-app
# d4e5f6a7b8c9: Pull complete
# a1b2c3d4e5f6: Pull complete
# 7a8b9c0d1e2f: Already exists
# Digest: sha256:8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9
# Status: Downloaded newer image for ...

# 运行拉取的镜像
docker run --rm harbor.zl-logistics.com/order-platform/hello-app:v1.0.0
# 预期输出：
# Hello from Harbor Demo App v1.0.0
# Hostname: a1b2c3d4e5f6
# Architecture: x86_64
# OS: NAME="Alpine Linux"

# ================================================================
# 场景二：查看 Harbor API 确认镜像存储
# ================================================================
# 列出仓库中的所有 Artifacts
curl -s -u admin:Harbor12345 \
  "https://harbor.zl-logistics.com/api/v2.0/projects/order-platform/repositories/hello-app/artifacts" | \
  jq '.[] | {digest: .digest, tags: [.tags[].name], size: .size, type: .type}'
# 预期输出：
# {
#   "digest": "sha256:8a9b0c1d2e...",
#   "tags": ["v1.0.0", "latest"],
#   "size": 12500000,
#   "type": "IMAGE"
# }

# ================================================================
# 场景三：模拟生产拉取（通过 HTTP API 验证镜像存在）
# ================================================================
# 检查镜像 manifest 是否可达（Docker Registry V2 API）
curl -s -u admin:Harbor12345 \
  -H "Accept: application/vnd.docker.distribution.manifest.v2+json" \
  "https://harbor.zl-logistics.com/v2/order-platform/hello-app/manifests/v1.0.0" | \
  jq '.schemaVersion, .mediaType'
# 预期输出：
# 2
# "application/vnd.docker.distribution.manifest.v2+json"
```

### 3.3 多架构镜像（Manifest List）构建与推送实战

**步骤一：启用并配置 buildx 多架构构建环境**

> **步骤目标**：搭建支持跨平台（AMD64 + ARM64 + 更多）的 Docker buildx 构建器。

```bash
# ================================================================
# 检查 buildx 是否安装与版本
# ================================================================
docker buildx version
# 预期输出（示意）：
# github.com/docker/buildx v0.12.1

# ================================================================
# 创建新的多平台构建器实例
# ================================================================
# 查看现有构建器
docker buildx ls
# 预期输出：
# NAME/NODE       DRIVER/ENDPOINT  STATUS  BUILDKIT  PLATFORMS
# default *       docker
#   default       default          running v0.12.1   linux/amd64, linux/amd64/v2, ...

# 创建支持多架构的构建器（使用 docker-container 驱动以支持 QEMU）
docker buildx create \
  --name multiplatform-builder \
  --driver docker-container \
  --driver-opt network=host \
  --use

# 验证新构建器状态
docker buildx inspect --bootstrap
# 预期输出（关键信息）：
# Name:          multiplatform-builder
# Driver:        docker-container
# Nodes:
# Name:          multiplatform-builder0
# Endpoint:      unix:///var/run/docker.sock
# Platforms:     linux/amd64, linux/amd64/v2, linux/386, linux/arm64, linux/arm/v7, linux/arm/v6

# ================================================================
# 可选：添加远程 ARM64 原生节点（避免 QEMU 模拟的性能损失）
# ================================================================
# SSH 到 ARM64 机器，添加为 buildx 远程节点
# docker buildx create --name multiplatform-builder \
#   --append \
#   --node arm64-native-node \
#   --platform linux/arm64 \
#   ssh://user@192.168.10.200
```

**步骤二：构建多架构镜像并推送**

> **步骤目标**：单条命令完成 AMD64 + ARM64 双架构构建，生成 Manifest List 并推送到 Harbor。

```bash
# ================================================================
# 创建支持多架构的 Dockerfile
# ================================================================
mkdir -p /tmp/multiarch-demo && cd /tmp/multiarch-demo

cat > Dockerfile <<'DOCKERFILE'
# 多架构兼容 Dockerfile
# --platform=$BUILDPLATFORM 和 $TARGETARCH 是 buildx 提供的自动变量
FROM --platform=$BUILDPLATFORM alpine:3.19.1

# ARG 在 FROM 后重新声明才能在该阶段中使用
ARG TARGETOS
ARG TARGETARCH

LABEL org.opencontainers.image.title="Multi-Arch Demo"
LABEL org.opencontainers.image.version="1.0.0"

# 条件安装：根据目标架构选择不同的依赖
RUN echo "Building for ${TARGETOS}/${TARGETARCH}" && \
    if [ "$TARGETARCH" = "arm64" ]; then \
      echo "ARM64 specific setup..."; \
    elif [ "$TARGETARCH" = "amd64" ]; then \
      echo "AMD64 specific setup..."; \
    fi

# 使用 TARGETARCH 决定下载哪个架构的二进制文件
RUN case ${TARGETARCH} in \
      "amd64") ARCH="x86_64" ;; \
      "arm64") ARCH="aarch64" ;; \
      *) ARCH="unknown" ;; \
    esac && \
    echo "Selected architecture: $ARCH"

ENV ARCH=${TARGETARCH:-unknown}
CMD echo "Running on $ARCH CPU" && sleep infinity
DOCKERFILE

# ================================================================
# 构建并推送多架构镜像
# ================================================================
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  --tag harbor.zl-logistics.com/order-platform/multiarch-demo:v1.0.0 \
  --tag harbor.zl-logistics.com/order-platform/multiarch-demo:latest \
  --push \
  --progress=plain \
  .

# 构建过程详细输出：
# #1 [internal] booting buildkit
# #1 pulling image moby/buildkit:buildx-stable-1
# #1 pulling image moby/buildkit:buildx-stable-1 1.2s done
# #1 creating container buildx_buildkit_multiplatform-builder0
# #1 DONE 2.4s
# 
# #2 [linux/amd64 internal] load build definition from Dockerfile
# #2 transferring dockerfile: 821B done
# 
# #3 [linux/arm64 internal] load build definition from Dockerfile
# #3 transferring dockerfile: 821B done
# 
# #4 [linux/amd64 1/3] FROM docker.io/library/alpine:3.19.1@sha256:...
# #4 resolve docker.io/library/alpine:3.19.1@sha256:... done
# 
# #5 [linux/arm64 1/3] FROM docker.io/library/alpine:3.19.1@sha256:...
# #5 resolve docker.io/library/alpine:3.19.1@sha256:... done
# 
# #6 [linux/amd64 2/3] RUN echo "Building for linux/amd64" ...
# #6 0.512 Building for linux/amd64
# #6 DONE 0.6s
# 
# #7 [linux/arm64 2/3] RUN echo "Building for linux/arm64" ...
# #7 0.723 Building for linux/arm64
# #7 DONE 0.8s
# 
# #8 exporting to image
# #8 exporting layers
# #8 exporting layers 0.5s done
# #8 exporting manifest sha256:...
# #8 exporting manifest list sha256:...
# #8 pushing layers
# #8 pushing layers 3.2s done
# #8 pushing manifest for harbor.zl-logistics.com/order-platform/multiarch-demo:v1.0.0@sha256:...
# #8 pushing manifest for harbor.zl-logistics.com/order-platform/multiarch-demo:latest@sha256:...
# #8 DONE 4.1s
```

**步骤三：验证多架构 Manifest List 结构**

> **步骤目标**：查看推送到 Harbor 的 Manifest List 内容，确认各架构子 Manifest 完整。

```bash
# ================================================================
# 方式一：docker manifest inspect（查看 Manifest List 结构）
# ================================================================
docker manifest inspect harbor.zl-logistics.com/order-platform/multiarch-demo:v1.0.0
```

预期输出：

```json
{
  "schemaVersion": 2,
  "mediaType": "application/vnd.docker.distribution.manifest.list.v2+json",
  "manifests": [
    {
      "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
      "digest": "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
      "size": 528,
      "platform": {
        "architecture": "amd64",
        "os": "linux"
      }
    },
    {
      "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
      "digest": "sha256:d3b07384d113edec49eaa6238ad5ff00a1b0c44298fc1c149afbf4c8996fb924",
      "size": 528,
      "platform": {
        "architecture": "arm64",
        "os": "linux"
      }
    }
  ]
}
```

```bash
# ================================================================
# 方式二：通过 Harbor API 查看 Artifact 详情
# ================================================================
curl -s -u admin:Harbor12345 \
  "https://harbor.zl-logistics.com/api/v2.0/projects/order-platform/repositories/multiarch-demo/artifacts?with_tag=true" | \
  jq '.[] | {
    digest: .digest[:20],
    tags: [.tags[].name],
    type: .type,
    references: .references | map({child_digest: .child_digest[:20], platform: .platform})
  }'
# 预期输出：
# {
#   "digest": "sha256:abc123...",
#   "tags": ["v1.0.0", "latest"],
#   "type": "IMAGE",
#   "references": [
#     { "child_digest": "sha256:e3b0c4...", "platform": { "architecture": "amd64", "os": "linux" } },
#     { "child_digest": "sha256:d3b073...", "platform": { "architecture": "arm64", "os": "linux" } }
#   ]
# }
# 注：type: "IMAGE" 且 references 非空说明这是一个 Manifest List（索引）

# ================================================================
# 方式三：在不同架构机器上拉取验证
# ================================================================
# 在 AMD64 机器上：
docker pull harbor.zl-logistics.com/order-platform/multiarch-demo:v1.0.0
docker inspect harbor.zl-logistics.com/order-platform/multiarch-demo:v1.0.0 --format '{{.Os}}/{{.Architecture}}'
# 预期输出：linux/amd64

# 在 ARM64 (Mac M3) 机器上：
docker pull harbor.zl-logistics.com/order-platform/multiarch-demo:v1.0.0
docker inspect harbor.zl-logistics.com/order-platform/multiarch-demo:v1.0.0 --format '{{.Os}}/{{.Architecture}}'
# 预期输出：linux/arm64
```

**步骤四：手动创建并推送 Manifest List（高级用法）**

> **步骤目标**：在无法使用 buildx 的受限环境下，通过 docker manifest 命令手动组装 Manifest List。

```bash
# ================================================================
# 场景：已有 AMD64 和 ARM64 镜像分别推送，需要手动创建 Manifest List
# ================================================================

# 假设已有两个独立镜像：
# - harbor.zl-logistics.com/order-platform/order-svc:v1.5.0-amd64
# - harbor.zl-logistics.com/order-platform/order-svc:v1.5.0-arm64

# 创建 Manifest List
docker manifest create \
  harbor.zl-logistics.com/order-platform/order-svc:v1.5.0 \
  --amend harbor.zl-logistics.com/order-platform/order-svc:v1.5.0-amd64 \
  --amend harbor.zl-logistics.com/order-platform/order-svc:v1.5.0-arm64

# 查看创建的 Manifest List
docker manifest inspect harbor.zl-logistics.com/order-platform/order-svc:v1.5.0
# 预期输出：
# (Manifest List 结构，包含两个 platform 条目)

# 添加注解（可选但推荐）
docker manifest annotate \
  harbor.zl-logistics.com/order-platform/order-svc:v1.5.0 \
  harbor.zl-logistics.com/order-platform/order-svc:v1.5.0-arm64 \
  --os linux --arch arm64 --variant v8

# 推送到 Harbor
docker manifest push harbor.zl-logistics.com/order-platform/order-svc:v1.5.0 --purge
# --purge: 推送成功后从本地删除，减少本地存储占用

# 验证推送后的 Manifest List
docker manifest inspect harbor.zl-logistics.com/order-platform/order-svc:v1.5.0 | \
  jq '.manifests[].platform'
# 预期输出：
# {
#   "architecture": "amd64",
#   "os": "linux"
# }
# {
#   "architecture": "arm64",
#   "os": "linux"
# }
```

### 3.4 Containerd 客户端配置与完整使用流程

**步骤一：配置 Containerd 信任 Harbor 证书**

> **步骤目标**：在 K8s Node 上配置 Containerd，使其能通过 HTTPS 安全拉取 Harbor 中的镜像。

```bash
# ================================================================
# Containerd 证书配置（v2 格式，Containerd 1.7+）
# ================================================================

# 创建证书目录
sudo mkdir -p /etc/containerd/certs.d/harbor.zl-logistics.com/

# 复制 CA 根证书
sudo cp zl-ca.crt /etc/containerd/certs.d/harbor.zl-logistics.com/ca.crt

# 创建 hosts.toml 文件（Containerd v2 的核心配置文件）
sudo tee /etc/containerd/certs.d/harbor.zl-logistics.com/hosts.toml > /dev/null <<'TOML'
# Containerd v2 registry 主机配置
server = "https://harbor.zl-logistics.com"

[host."https://harbor.zl-logistics.com"]
  capabilities = ["pull", "resolve", "push"]
  ca = "/etc/containerd/certs.d/harbor.zl-logistics.com/ca.crt"
  skip_verify = false
  
  # 如果使用双向 TLS (mTLS)，还需要配置客户端证书：
  # client = [["/etc/containerd/certs.d/harbor.zl-logistics.com/client.cert",
  #            "/etc/containerd/certs.d/harbor.zl-logistics.com/client.key"]]
TOML

# 验证 hosts.toml 语法
cat /etc/containerd/certs.d/harbor.zl-logistics.com/hosts.toml
```

```bash
# ================================================================
# Containerd v1 格式配置（兼容旧版，作为备用方案）
# ================================================================
# 编辑 /etc/containerd/config.toml
sudo tee -a /etc/containerd/config.toml > /dev/null <<'TOML'

# 配置 Harbor 的证书和认证信息
[plugins."io.containerd.grpc.v1.cri".registry.configs."harbor.zl-logistics.com".tls]
  ca_file = "/etc/containerd/certs.d/harbor.zl-logistics.com/ca.crt"
  cert_file = ""
  key_file = ""
  insecure_skip_verify = false

[plugins."io.containerd.grpc.v1.cri".registry.configs."harbor.zl-logistics.com".auth]
  username = "robot$order-platform+k8s-puller"
  password = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9..."
# 注意：生产环境不要直接在 config.toml 中写密码！
# 应使用 imagePullSecret 或 K8s ServiceAccount 来管理凭证
TOML
```

**步骤二：使用 crictl 拉取和验证镜像**

> **步骤目标**：通过 crictl（CRI 标准命令行工具）从 Harbor 拉取镜像并验证。

```bash
# ================================================================
# 配置 crictl 指向 Containerd
# ================================================================
sudo tee /etc/crictl.yaml > /dev/null <<'EOF'
runtime-endpoint: unix:///run/containerd/containerd.sock
image-endpoint: unix:///run/containerd/containerd.sock
timeout: 30
debug: false
pull-image-on-create: false
EOF

# 重启 Containerd 加载配置
sudo systemctl daemon-reload
sudo systemctl restart containerd

# 验证 Containerd 服务状态
sudo systemctl status containerd --no-pager -l | head -10
# 预期输出：
# ● containerd.service - containerd container runtime
#    Loaded: loaded (/lib/systemd/system/containerd.service; enabled)
#    Active: active (running) since ...

# ================================================================
# 拉取镜像（crictl）
# ================================================================
# 注意：crictl pull 的镜像引用格式
sudo crictl pull harbor.zl-logistics.com/order-platform/hello-app:v1.0.0
# 预期输出：
# Image is up to date for sha256:d4e5f6a7b8c9...

# 查看已拉取的镜像
sudo crictl images | grep hello-app
# 预期输出：
# harbor.zl-logistics.com/order-platform/hello-app   v1.0.0    d4e5f6a7b8c9d   12.5MB

# 查看镜像详细信息
sudo crictl inspecti d4e5f6a7b8c9d | jq '.status.repoTags, .info.imageSpec.architecture'
# 预期输出：
# [
#   "harbor.zl-logistics.com/order-platform/hello-app:v1.0.0"
# ]
# "amd64"

# ================================================================
# 场景：验证多架构镜像在 Containerd 中的拉取
# ================================================================
sudo crictl pull harbor.zl-logistics.com/order-platform/multiarch-demo:v1.0.0
sudo crictl inspecti harbor.zl-logistics.com/order-platform/multiarch-demo:v1.0.0 | \
  jq '.info.imageSpec.architecture'
# 预期输出（取决于当前机器架构）："amd64" 或 "arm64"
```

**步骤三：使用 nerdctl（完整 Docker 风格工作流）**

> **步骤目标**：在 Containerd 环境中使用 nerdctl 提供与 Docker 几乎一致的开发体验。

```bash
# ================================================================
# 安装 nerdctl（如未安装）
# ================================================================
NERDCTL_VERSION=1.7.6
wget -q "https://github.com/containerd/nerdctl/releases/download/v${NERDCTL_VERSION}/nerdctl-${NERDCTL_VERSION}-linux-amd64.tar.gz"
sudo tar -xzf "nerdctl-${NERDCTL_VERSION}-linux-amd64.tar.gz" -C /usr/local/bin/
nerdctl version
# 预期输出：
# Client:
#  Version:       v1.7.6
#  ...
# Server:
#  containerd:
#   Version:      v1.7.13

# ================================================================
# nerdctl 工作流（与 Docker 命令几乎一模一样）
# ================================================================

# 登录 Harbor（nerdctl 有自己的凭证存储，与 Docker 独立）
nerdctl login harbor.zl-logistics.com -u admin -p Harbor12345
# 输出：Login Succeeded

# 查看 nerdctl 的凭证存储位置
cat ~/.config/nerdctl/config.json 2>/dev/null || echo "凭证存储在默认位置"

# 拉取镜像
nerdctl pull harbor.zl-logistics.com/order-platform/hello-app:v1.0.0

# 查看镜像
nerdctl images | grep hello-app
# 预期输出：
# REPOSITORY                                          TAG       IMAGE ID        CREATED        SIZE
# harbor.zl-logistics.com/order-platform/hello-app    v1.0.0    d4e5f6a7b8c9    10 min ago     12.5 MB

# 运行容器
nerdctl run --rm harbor.zl-logistics.com/order-platform/hello-app:v1.0.0
# 预期输出：
# Hello from Harbor Demo App v1.0.0
# Hostname: ...

# 构建镜像（nerdctl 内置 BuildKit 集成）
nerdctl build -t harbor.zl-logistics.com/order-platform/hello-app:v1.1.0 .

# 推送镜像
nerdctl push harbor.zl-logistics.com/order-platform/hello-app:v1.1.0

# ================================================================
# nerdctl 与 Docker 的关键差异点
# ================================================================
# 1. network 命名空间隔离：nerdctl run 默认不共享主机网络
# 2. volume 驱动：nerdctl 默认使用 containerd 的 snapshotter，而非 Docker 的 overlay2
# 3. compose 支持：nerdctl compose up（需要额外安装）
# 4. 凭证存储：~/.config/nerdctl/config.json vs ~/.docker/config.json
```

### 3.5 可能遇到的坑

**坑1：docker login 成功但 docker push 报 401 Unauthorized**

| 维度 | 详情 |
|------|------|
| **现象** | `docker login harbor.zl-logistics.com` 显示 "Login Succeeded"，但 `docker push` 立即返回 `unauthorized: authentication required` |
| **根因** | 这是典型的 **TLS 证书 SAN 不匹配** 问题。Docker 登录时的主机名验证和 push 时的主机名解析可能走了不同的路径——最常见的是：登录时用域名 `harbor.zl-logistics.com`，但 DNS 解析或 `/etc/hosts` 将其指向了 IP，而该 IP 上的证书 SAN 中不包含这个域名。另一种可能：企业代理（如 Zscaler）拦截了 HTTPS 流量并使用自己的证书重新加密，导致 Docker 收到的证书不是 Harbor Nginx 发出的证书 |
| **解决方法** | ① 验证证书 SAN 是否包含你使用的所有域名/IP：`openssl s_client -connect harbor.zl-logistics.com:443 -showcerts \| openssl x509 -text -noout \| grep -A 3 "Subject Alternative Name"`；② 如果通过代理访问，在 Docker 守护进程配置中排除该域名：`"proxies": {"no-proxy": "harbor.zl-logistics.com"}`；③ 确认 `/etc/hosts` 中的映射与证书一致 |

**坑2：大镜像 push 到 60%-80% 时频繁超时中断**

| 维度 | 详情 |
|------|------|
| **现象** | 推送 1GB 以上的镜像时，进度到 60%-80% 区间反复失败，返回 `connection reset by peer`、`unexpected EOF` 或 `i/o timeout` |
| **根因** | 此问题通常是**三重超时参数叠加**引起的：① Harbor 前端 Nginx 容器的 `client_max_body_size`（默认 1m）、`proxy_read_timeout`（默认 60s）、`proxy_request_buffering`（默认 on）三者联合作用；② 如果 Harbor 前面有额外的负载均衡器或 WAF，这些组件也有自己的超时设置；③ Docker 客户端在分块上传时，每个 chunk 之间可能有较长间隔（取决于网络条件），而服务器端在超时后主动断开了连接 |
| **解决方法** | ① 修改 Harbor 的 `common/config/nginx/nginx.conf`：`client_max_body_size 0;`（无限制）、`proxy_read_timeout 600s;`、`proxy_request_buffering off;`（关闭请求缓冲以支持分块上传）；② 执行 `docker compose down && docker compose up -d` 使配置生效；③ 检查所有中间代理的超时设置；④ 对于极不稳定的网络，考虑使用 `docker save -o img.tar image:tag` 导出文件后 rsync 传输，再 `docker load`，作为兜底方案 |

**坑3：docker buildx 多架构构建速度极慢**

| 维度 | 详情 |
|------|------|
| **现象** | 使用 `buildx build --platform linux/amd64,linux/arm64` 构建镜像时，非本机架构的构建步骤耗时是本机架构的 10-20 倍。一个在 x86 机器上 30 秒完成的构建，加入 ARM64 后总耗时超过 8 分钟 |
| **根因** | `buildx` 使用 QEMU 用户态模拟来执行非本机架构的指令。QEMU 模拟的性能损耗在 CPU 密集型构建（如 `pip install`、`apt-get` 等包管理操作）中尤为显著。此外，`docker-container` 驱动（默认）的 BuildKit 在每个构建会话后数据丢失，下次构建需要重新拉取基础镜像 |
| **解决方法** | ① 预安装 QEMU binfmt 支持（一次性操作）：`docker run --privileged --rm tonistiigi/binfmt --install all`；② 对于持续集成的场景，添加远程原生 ARM 构建节点：`docker buildx create --append --node arm-node --platform linux/arm64 ssh://arm-builder-host`；③ 使用 `--cache-from` 和 `--cache-to` 参数在不同构建之间共享缓存；④ 对于简单的多架构场景（如 Go 交叉编译），在 Dockerfile 中使用 `--platform=$BUILDPLATFORM` 构建，用 Go 的 `GOARCH` 环境变量而非 QEMU 模拟来实现跨架构 |

**坑4：Containerd 配置后 `crictl pull` 始终超时**

| 维度 | 详情 |
|------|------|
| **现象** | Containerd 的 `config.toml` 和证书目录配置完成后，`crictl pull` 始终挂起直到超时，`systemctl status containerd` 显示正常，但 `journalctl -u containerd` 显示 `failed to resolve reference "harbor.zl-logistics.com/..."` |
| **根因** | 三种常见原因：① Containerd 使用的 DNS 解析器没有正确解析 Harbor 域名（尤其是在 systemd-resolved 或 CoreDNS 环境中）；② `config.toml` 中存在语法错误导致整个 registry 配置段被忽略——但 Containerd 不会抛出明显的错误日志；③ 配置了 `[plugins."io.containerd.grpc.v1.cri".registry.mirrors]` 但 endpoint 配置错误，Containerd 尝试从 mirrors 拉取而非直接访问 Harbor |
| **解决方法** | ① 在 Containerd 所在的机器上使用相同的 DNS 解析验证域名可达：`nslookup harbor.zl-logistics.com`、`curl -v --cacert ca.crt https://harbor.zl-logistics.com/v2/`；② 使用 `containerd config dump` 查看 Containerd 实际生效的完整配置（这比直接读 `config.toml` 更可靠）：`sudo containerd config dump \| grep -A 20 "harbor.zl-logistics.com"`；③ 删除 `mirrors` 配置段让 Containerd 直连 Harbor，逐步排查 |

---

## 4 项目总结

### 4.1 Docker vs Containerd vs nerdctl 全维度对比

| 维度 | Docker CLI (docker) | Containerd CLI (crictl) | nerdctl | 说明 |
|------|---------------------|------------------------|---------|------|
| 底层运行时 | dockerd（内部调用 containerd） | containerd（直接操作） | containerd（直接操作） | Docker 多一层 dockerd 抽象 |
| 证书信任路径 | `/etc/docker/certs.d/<host>/ca.crt` | `/etc/containerd/certs.d/<host>/hosts.toml` | 同 Containerd 配置 | 路径格式不同，不可互换 |
| 认证命令 | `docker login <host>` | 不支持（需手动编辑 config.toml） | `nerdctl login <host>` | crictl 是为 Kubelet 设计的，不是为人类设计的 |
| 凭证存储路径 | `~/.docker/config.json` | `/etc/containerd/config.toml`（auth 段） | `~/.config/nerdctl/config.json` | 三套独立的凭证存储 |
| 镜像构建 | `docker build` / `buildx build` | 不支持（需外挂 BuildKit） | `nerdctl build`（内置 BuildKit） | nerdctl 兼容性最好 |
| 多架构支持 | buildx 原生支持 Manifest List | 不适用（仅拉取侧自动选架构） | 实验性支持（buildx wrapper） | Docker 在企业级多架构方案中更成熟 |
| Insecure Registry | `insecure-registries` 白名单（daemon.json） | 仅 TLS 证书方案（`insecure_skip_verify`） | 基于 Containerd 配置 | Containerd 强制证书验证，杜绝裸奔 |
| K8s 集成度 | 需 cri-dockerd 适配器 | 原生 CRI 插件（Kubelet 默认） | 原生 CRI 插件 | K8s 1.24+ 不再默认支持 Docker |
| 日志格式 | Docker 风格（短 JSON） | CRI 标准日志（需 `crictl logs`） | 类 Docker 风格 | crictl 日志查看不便 |
| 推荐场景 | 本地开发、CI/CD 构建节点 | K8s Worker 节点（运维调试） | 替代 Docker 的 Containerd 开发环境 | 三者各有定位，不是替代关系 |

### 4.2 三种证书配置方案对比

| 方案 | 安全性 | 复杂度 | 适用环境 | 是否支持 Harbor 复制 | 维护成本 |
|------|--------|--------|---------|---------------------|---------|
| 企业 CA 签发证书 | ★★★★★ | 中（仅需分发 CA 根证书到所有客户端） | 企业内网生产环境 | ✅ 原生支持 | 低（CA 证书长期有效） |
| 自签名证书 + 客户端信任 | ★★★☆☆ | 高（每个客户端需手动配置证书） | 中小团队测试/生产 | ✅ 需各客户端信任 | 中（需管理证书过期轮换） |
| Insecure Registry（跳过验证） | ★☆☆☆☆ | 低（一行配置） | 仅限本地单机测试 | ❌ 安全团队不会批准 | 极低（风险极高） |

### 4.3 多架构镜像构建方案对比

| 方案 | 构建速度 | 复杂度 | 基础设施要求 | 产出 |
|------|---------|--------|------------|------|
| `docker buildx` + QEMU 模拟 | 慢（10-20x 非本机） | 低 | 单台机器 | Manifest List |
| `docker buildx` + 远程原生节点 | 快（接近本机构建） | 中 | 需要两台不同架构的机器 | Manifest List |
| 分开构建 + `docker manifest create` 手动合并 | 中 | 高（手动步骤多） | 两台不同架构的机器 | Manifest List |
| CI 并行构建（不同 Runner 不同架构） | 快 | 中（需 CI 平台支持多架构 Runner） | CI 平台（GitHub Actions / GitLab CI 多架构 Runner） | Manifest List |

### 4.4 适用场景

- **CI/CD 流水线自动推送**：Jenkins/GitLab CI/GitHub Actions 构建完成后自动 push 到 Harbor，使用机器人账户凭证（永久有效），避免 Token 过期导致 CI 中断
- **Kubernetes 集群镜像预缓存**：通过 DaemonSet 在 Node 上预拉取高频使用的镜像到本地 Containerd，加速 Pod 启动（尤其适合大促前的弹性扩容）
- **混合架构（AMD64 + ARM64）生产集群**：通过 Manifest List 实现同一标签自动匹配架构，Mac 开发者推送 ARM64，CI 云端构建 AMD64，K8s 各架构 Node 各取所需
- **离线/气隙环境镜像分发**：`docker save` 导出 → 物理介质传输 → `docker load` 导入到离线 Harbor，再通过本地网络分发
- **跨云/跨基础设施镜像统一存储**：所有不同云供应商的节点都从同一个 Harbor 拉取基础镜像，避免各云供应商的 Registry 版本不一致

### 4.5 不适用场景

- **极大型镜像（> 10GB）**：Docker/Containerd 的单流传输协议在高延迟网络上效率极低，建议使用 Nydus/eStarglz 等懒加载技术或 Dragonfly/P2P 分发方案（后续章节详述）
- **GPU 驱动高度敏感场景**：CUDA 版本与主机驱动必须精确匹配时，不建议用 Manifest List 统一标签，应为每种 CUDA 版本创建独立标签
- **极低延迟要求的边缘计算**：如果边缘节点距离 Harbor 300ms+ RTT，建议在每个边缘站点部署 Harbor 实例并通过复制规则同步（第 8 章），而非直接跨地域拉取

### 4.6 注意事项

1. **登录凭证的时效性与存储安全**：Harbor 管理员账户的 Token 默认 30 天过期，机器人账户 Token 永久有效。CI/CD 推荐使用机器人账户，且凭证应存储在 CI 平台的 Secret 管理器（如 GitHub Secrets、GitLab CI Variables）中，不要直接写在代码仓库中
2. **K8s 的 imagePullSecret 与 Docker login 互相独立**：Docker Desktop 上的 `docker login` 只影响本地 Docker，K8s 集群内的 Node 需要通过 `kubectl create secret docker-registry` 单独创建 imagePullSecret，并在 Pod Spec 或 ServiceAccount 中引用
3. **buildx 本地缓存磁盘占用**：多架构构建后本地 BuildKit 缓存会快速增长（轻松超过 20GB），定期执行 `docker buildx prune --all --force` 清理，或在 CI 中使用 `--cache-from type=registry --cache-to type=registry` 将缓存存储到 Harbor 中
4. **证书到期前的监控与轮换**：至少提前 30 天设置告警（通过 Prometheus Blackbox Exporter 监控证书过期时间），因为 Harbor 证书过期会导致全集群所有 Node 无法拉取镜像（K8s 的 `ImagePullBackOff` 会同时出现数百个 Pod 故障）
5. **Containerd 配置热更新 vs 冷重启**：Containerd 的大部分配置支持热加载（`sudo systemctl reload containerd`），但 TLS 证书变更和 registry 相关配置通常需要完全重启（`sudo systemctl restart containerd`），重启期间该 Node 上的所有 Pod 不受影响，但无法创建新 Pod

### 4.7 常见踩坑经验

| 故障现象 | 根因分析 | 解决方案 | 影响范围 |
|---------|---------|---------|---------|
| `docker pull` 返回 `manifest unknown` | Harbor 项目名或仓库名不存在，或标签已被人删除。也可能是项目名大小写错误——Harbor 中项目名和仓库名是**大小写不敏感**的，但 Docker 引用是大小写敏感的 | `curl -s https://harbor.domain/v2/<project>/<repo>/tags/list` 确认标签存在；在 Portal 中搜索仓库名（使用小写） | 单仓库 |
| K8s Pod 全部 `ImagePullBackOff` | Node 上 Containerd 的证书信任配置缺失或证书过期。常见场景：运维更新了 Harbor 证书但忘记更新所有 K8s Node 的 Containerd 配置 | 使用 Ansible 批量更新所有 Node 的 `/etc/containerd/certs.d/` 目录，并重启 Containerd。建议使用 DaemonSet + initContainer 自动同步证书 | 整个集群 |
| `buildx build --push` 长时间 Pending | 网络代理（HTTP_PROXY/HTTPS_PROXY 环境变量）干扰了 BuildKit 的去中心化节点通信 | `docker buildx create --driver-opt network=host --use` 使用主机网络模式，或配置 BuildKit 代理白名单 | 单次构建 |
| CI 流水线中 Docker 登录后 push 仍然 401 | CI Runner 的多个并行 Job 共享同一个 `~/.docker/config.json`，Job A 的 `docker logout` 清除了 Job B 刚写入的凭证 | 使用 `docker login --password-stdin` 并在每个 Job 中独立登录，或为每个 Job 使用独立的 `DOCKER_CONFIG` 环境变量指定不同的凭证目录 | 并行 CI Job |
| `nerdctl compose up` 报 `network not found` | nerdctl 的网络驱动与 Docker Compose 不完全兼容——nerdctl 默认创建 `bridge` 网络，而项目中的 docker-compose.yml 可能引用了 `external: true` 的网络 | 使用 `nerdctl network create <name>` 手动预创建所需网络，或者在 compose 文件中去掉 `external: true` 让 nerdctl 自动创建 | 单项目 |

### 4.8 思考题

1. **智联物流在 3 个城市（北京、上海、广州）各有 K8s 集群。北京是主建设中心，所有镜像由北京的 CI 流水线构建并推送到北京 Harbor。上海和广州集群需要拉取北京 Harbor 中的基础镜像。但考虑到网络延迟和带宽成本，CTO 要求每个城市在本地缓存常用的基础镜像。请设计一个三层缓存架构：北京 Harbor（源）→ 各城市本地 Registry 镜像缓存 → Containerd Node 本地预缓存。并分析哪些镜像适合全局同步，哪些适合按需拉取。**

2. **一家使用 Harbor 的企业发现其 CI 流水线每天产生约 200 个 `build-<git-short-sha>-<timestamp>` 格式的标签。一个月后标签总数突破 6000 个，Harbor Portal 的仓库页面加载需要 15 秒以上。CI 团队决定实施自动清理策略：每个仓库保留最近 5 个 CI 构建标签 + 所有 `release-*` 标签 + 所有 `latest` 标签，其余自动清除。请用 Harbor API 设计一个完整的自动化清理脚本（Shell 或 Python），要求：① 支持 Dry Run 模式（仅列出将被清理的标签，不实际执行）；② 记录每次清理的操作日志（清理时间、清理数量、释放空间预估值）；③ 如果清理后仓库中仅剩 0 个标签（所有标签都被清理了），脚本应报警并跳过该仓库。**

---

> 下一章预告：第6章将深入 Harbor 的 Artifact 制品管理模型，包括标签不可变性规则、保留策略的精细配置，以及多架构镜像的底层存储机制——从"存镜像"升级为"管制品"。

