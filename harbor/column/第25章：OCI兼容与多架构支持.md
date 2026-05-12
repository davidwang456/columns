# 第25章：OCI 兼容与多架构支持

## 1 项目背景

某公司技术栈在两年内经历了深刻多元化——后端从纯x86架构的Java服务扩展到ARM服务器上的Go/Rust高性能服务，前端引入了WASM（WebAssembly）边缘计算模块。Harbor作为统一的制品仓库，面临着传统Docker Image模式无法覆盖的多样化制品管理需求。

**痛点一：ARM64镜像管理陷入混乱。** 团队中30%的开发者使用Apple M1/M2/M3 MacBook进行本地开发。他们在本地构建的镜像是ARM64架构，直接推送到Harbor。然而，生产环境的所有服务器都是x86架构——部署时发现Harbor中同一个`order-service:latest`标签下只有ARM64的Manifest，x86服务器无法运行，导致Pod进入`ImagePullBackOff`。团队只能手动维护两份标签（`v2.3.0-amd64`和`v2.3.0-arm64`），版本管理复杂度翻倍。更糟糕的是，测试环境使用ARM服务器、预发布使用x86、生产也是x86——同一个版本要在三个不同架构间手动协调，经常出现"测试通过的镜像在生产上运行失败是因为架构不匹配"的诡异Bug。

**痛点二：WASM模块无处可存。** 前端团队将部分计算密集的图片处理逻辑编译为WebAssembly（.wasm文件），希望通过Harbor统一分发给边缘CDN节点。但Harbor默认只接受Docker Image格式的制品——一个.wasm文件无法通过`docker push`上传。团队最初把WASM文件存在S3上，版本管理靠文件命名约定（`image-processor-v1.2.3.wasm`），结果不出意外地出现了版本混乱——边缘节点跑了v1.2.0的WASM，但前端代码期望v1.2.3的接口，导致了3小时的生产故障。

**痛点三：SBOM与镜像管理脱节。** 安全合规要求每个生产镜像附带SBOM（Software Bill of Materials，软件物料清单）——列出所有依赖包及其版本。团队目前的流程是：CI构建镜像 → 手动生成SBOM → 把SBOM JSON文件上传到Confluence附件。镜像和SBOM的关联关系完全靠人工维护。结果：审计时发现`order-service:v2.3.0`在生产跑了3个月，但附件里的SBOM是`v2.2.0`的——安全团队无法确认v2.3.0引入了哪个版本的log4j，整个合规审计过程陷入混乱。

**痛点四：OCI Artifacts概念模糊，踩坑不断。** 团队听说过"OCI Artifacts"可以存储任意类型的制品（Helm Chart、SBOM、WASM等），但对其工作原理一知半解。尝试使用`oras push`上传WASM模块时，不清楚media type应该设什么值；看到Harbor Portal中WASM模块显示为"Unknown"类型时，担心Harbor不能正确管理这些非镜像制品。更重要的是——团队不清楚OCI Artifact与Docker Image的本质区别：它们共享同一个Registry地址空间吗？Artifact的push会覆盖镜像的Manifest吗？垃圾回收会误删Artifact吗？

本章将从OCI规范的三层架构出发，详解Manifest List（多架构索引）的实现原理、OCI Artifact的存储和引用机制，以及Harbor如何在一套基础设施上统一管理镜像、Helm Chart、SBOM、WASM模块等多种制品。

---

## 2 项目设计——剧本式交锋对话

**场景：平台架构组周会，架构师老刘给团队科普OCI规范和Harbor的多制品管理能力。白板上画着OCI三层规范和Manifest List的树状结构。**

**小胖**（运维工程师，思维直观）："OCI是啥？跟Docker Image到底啥关系？我们一直用Docker——`docker build`、`docker push`、`docker pull`——也没见哪里用到了OCI啊？"

**老刘**（平台架构师）："小胖，你用Docker其实一直在用OCI——只是你自己没意识到。它就像你每天用微信发消息，底层用的是TCP/IP协议——你感知不到TCP/IP的存在，但它确实在工作。"

"OCI（Open Container Initiative）是Linux基金会旗下的开放标准组织，定义了容器生态的三个核心规范：

**OCI Image Spec**（镜像格式规范）：定义了容器镜像的内部结构——包括Config（元数据）、Layers（文件系统层）和Manifest（层索引）。Docker Image格式就是这个规范的一个实现——`docker push`推送的镜像完全符合OCI Image Spec。

**OCI Distribution Spec**（分发协议规范）：定义了Registry的HTTP API——如何Push、Pull、Discover制品。Harbor完全实现了这个规范——所以不仅`docker push/pull`能用，任何实现了OCI Distribution Spec的客户端（buildah、skopeo、oras、helm、cosign）都能与Harbor交互。

**OCI Runtime Spec**（运行时规范）：定义了如何运行一个容器——这个跟Harbor关系不大，主要是containerd、CRI-O等运行时的工作。"

**技术映射**：Harbor的Registry组件基于CNCF Distribution项目（原Docker Distribution），从v2.0开始完全实现OCI Distribution Spec。这意味着Harbor本质上是一个**OCI兼容的制品仓库**——不只是"Docker镜像仓库"，而是任何OCI兼容制品都可以存储的通用制品仓库。

**小白**（高级开发，喜欢深挖原理）："多架构Manifest List我一直没有完全理解。同一个`order-service:latest`标签，在x86服务器和ARM Mac上pull到的是不同的东西——Harbor内部是怎么实现的？两个架构的镜像用了同一个tag不会冲突吗？"

**老刘**："好问题。我给你画出Manifest List的内部数据结构。Manifest List本身是一个'目录'文件（media_type为`application/vnd.oci.image.index.v1+json`），它不包含任何镜像层数据。它内部列出了多个平台的子Manifest引用：

```
order-service:latest (Manifest List / OCI Image Index)

  linux/amd64 -> 子Manifest (digest: sha256:aaa111...)
    Config (JSON)    sha256:cfg-aaa...  (环境变量、入口点等)
    Layer 0 (tar)    sha256:111...      (基础文件系统层)
    Layer 1 (tar)    sha256:222...      (应用代码层)
    Layer 2 (tar)    sha256:333...      (配置文件层)

  linux/arm64 -> 子Manifest (digest: sha256:bbb222...)
    Config (JSON)    sha256:cfg-bbb...
    Layer 0 (tar)    sha256:444...      (ARM架构的基础层)
    Layer 1 (tar)    sha256:555...      (ARM架构的应用层)
    Layer 2 (tar)    sha256:666...

  linux/riscv64 -> 子Manifest (未来扩展)
    digest: sha256:ccc333...
```

**技术映射**：在Harbor数据库中，Manifest List存储在`artifact`表中。子Manifest也存储在`artifact`表中，通过`artifact_reference`表与Manifest List关联。当你执行`docker pull`时，Docker Client会先GET `/v2/<repo>/manifests/latest`，Harbor返回Manifest List。Docker Client解析Manifest List，找到最匹配本机架构的子Manifest（比如`linux/amd64`），然后用子Manifest的digest请求实际的镜像层数据。整个过程对用户透明——你只需要`docker pull`，架构选择是自动的。"

**小胖**："那WASM模块怎么存进Harbor？`.wasm`文件不是镜像啊——没有Dockerfile，没有Layer。"

**老刘**："这就轮到OCI Artifacts登场了。OCI Artifacts的核心思想是——**Registry不应该只关心镜像，而应该成为任意类型制品的存储和分发平台**。"

"OCI Artifacts没有定义新的API——它复用了OCI Distribution Spec的现有API（Push/Pull/Discover），但放宽了`manifest.config.mediaType`的限制。对于非容器镜像的制品（如WASM、配置文件、SBOM），你只需要：

1. 创建一个Manifest，其中`config.mediaType`设为你自定义的类型（如`application/wasm`）
2. 将你的文件作为唯一的Layer
3. 将Manifest和Layer push到Registry

这个操作有专门的工具——**oras CLI**（OCI Registry As Storage）：

```bash
# 推送WASM模块到Harbor（使用oras）
oras push harbor.company.com/order-platform/wasm-module:v1.0.0 \
  --artifact-type application/wasm \
  module.wasm

# oras背后做的事情：
# 1. 计算 module.wasm 的sha256
# 2. 将 module.wasm 作为一个Layer push到Registry
# 3. 创建Manifest（config.mediaType=application/wasm, layers=[sha256:module.wasm]）
# 4. Push Manifest 并打tag v1.0.0

# 推送SBOM到Harbor（作为镜像的附属品）
oras attach harbor.company.com/order-platform/order-service:v2.3.0 \
  --artifact-type application/spdx+json \
  sbom.spdx.json

# oras attach 做的事情：
# 1. Push sbom.spdx.json 作为独立Layer
# 2. 创建Manifest（config.mediaType=application/spdx+json）
# 3. 创建Artifact引用关系：order-service:v2.3.0 -> sbom artifact
# 4. Harbor在数据库中记录：artifact_id -> subject_artifact_id(指向镜像)
```

**技术映射**：Harbor的`artifact`表中，每个OCI Artifact都有一个`media_type`字段标识其类型。Harbor已知的类型包括：
- Docker Image：`application/vnd.docker.distribution.manifest.v2+json`
- OCI Image：`application/vnd.oci.image.manifest.v1+json`
- Manifest List：`application/vnd.oci.image.index.v1+json`
- Helm Chart：`application/vnd.cncf.helm.config.v1+json`
- Cosign签名：`application/vnd.dev.cosign.simplesigning.v1+json`
- 自定义media type → 其他任何OCI Artifact

**小白**："我注意到`oras attach`不是创建了一个新的tag，而是创建了一个'附属品（Accessory）'关系。这个关系在Harbor数据库里是怎么存储的？垃圾回收的时候，如果镜像被删了，附属的SBOM也会被删吗？"

**老刘**："好问题，这个涉及到Harbor的数据模型设计。附属品关系存储在`artifact_reference`表中。每个附属品（Accessory）都有一个`subject_artifact_id`指向它附属的主Artifact。垃圾回收时有一个关键设计：当一个镜像被删除时，如果它的附属品（SBOM、签名）没有其他引用者，GC会一并清理。但如果有其他镜像也引用了同一个SBOM，GC会保留SBOM。"

"这在实际场景中很有意义——比如一个基础镜像`openjdk:17`的SBOM被100个业务镜像引用，删除一个业务镜像时，GC不会删除这个SBOM（因为它还被其他99个镜像引用）。只有最后一个引用者也被删除时，SBOM才会被GC清理。"

**小胖**："既然Harbor支持这么多制品类型，那漏洞扫描对所有制品类型都有效吗？比如WASM模块能扫描吗？Helm Chart能扫描吗？"

**老刘**："好问题。Harbor的漏洞扫描（Trivy/Clair等）**只对容器镜像类型的制品有效**——因为扫描器需要解压镜像文件系统层的tar包来分析操作系统包和语言依赖。WASM模块是单一二进制文件，没有文件系统层的概念；Helm Chart只是Kubernetes的部署描述文件（YAML模板），也没有需要扫描的操作系统包。所以Harbor Portal上，这些非镜像类型的Artifact不会显示扫描结果。"

---

## 3 项目实战

### 3.1 环境要求

| 组件 | 版本要求 | 说明 |
|------|---------|------|
| Harbor | v2.8.0+ | OCI Artifacts完整支持始于v2.8 |
| Docker | 24.0+ | 提供`docker buildx`用于多架构构建 |
| buildx | v0.12+ | Docker的多架构构建插件 |
| oras CLI | v1.1.0+ | OCI Artifact推送工具 |
| syft | v0.100+ | SBOM生成工具（Anchore） |
| crane | v0.19+ | 可选：Registry操作和Manifest调试工具 |
| QEMU | v8.0+ | 在x86上模拟ARM架构构建（多架构构建必需） |

### 3.2 第一步：构建并推送多架构镜像

**目标**：使用`docker buildx`构建同时支持amd64和arm64的镜像，推送到Harbor。

```bash
# 步骤1：创建并启动buildx构建器（支持多架构）
docker buildx create --name multiarch-builder \
  --driver docker-container \
  --platform linux/amd64,linux/arm64 \
  --use

docker buildx inspect --bootstrap
# 预期输出：
# Name:   multiarch-builder
# Driver: docker-container
# Nodes:
# Name:      multiarch-builder0
# Platforms: linux/amd64, linux/arm64, linux/arm/v7, linux/arm/v6

# 步骤2：准备一个简单的多阶段Dockerfile
cat > Dockerfile << 'EOF'
FROM alpine:3.19
RUN apk add --no-cache curl ca-certificates
COPY hello.sh /hello.sh
RUN chmod +x /hello.sh
ENTRYPOINT ["/hello.sh"]
EOF

cat > hello.sh << 'EOF'
#!/bin/sh
echo "Hello from $(uname -m) architecture!"
echo "Running on: $(cat /etc/os-release | grep PRETTY_NAME)"
EOF

# 步骤3：使用buildx构建并直接推送多架构镜像
HARBOR_URL="harbor.company.com"
PROJECT="order-platform"
IMAGE="hello-app"
TAG="v2.0.0"

docker buildx build \
  --platform linux/amd64,linux/arm64 \
  --tag ${HARBOR_URL}/${PROJECT}/${IMAGE}:${TAG} \
  --push \
  .

# 预期输出：
#  => [internal] booting buildkit
#  => [linux/amd64 1/2] FROM docker.io/library/alpine:3.19@sha256:...
#  => [linux/arm64 1/2] FROM docker.io/library/alpine:3.19@sha256:...
#  => [linux/amd64 2/2] RUN apk add --no-cache curl ca-certificates
#  => [linux/arm64 2/2] RUN apk add --no-cache curl ca-certificates
#  => exporting to image
#  => pushing layers
#  => pushing manifest for harbor.company.com/order-platform/hello-app:v2.0.0

# 步骤4：在Harbor Portal中验证多架构
# 项目 -> order-platform -> hello-app -> v2.0.0
# 预期：显示 "Multi-arch" 标签，展开可见 amd64 和 arm64 两个子Manifest
```

### 3.3 第二步：验证多架构自动选择

**目标**：验证在不同架构的机器上pull同一个tag，自动获取正确的架构镜像。

```bash
# 在x86服务器上验证（预期自动选择amd64）
docker pull harbor.company.com/order-platform/hello-app:v2.0.0
docker inspect harbor.company.com/order-platform/hello-app:v2.0.0 --format '{{.Architecture}}'
# 预期输出：amd64

docker run --rm harbor.company.com/order-platform/hello-app:v2.0.0
# 预期输出：Hello from x86_64 architecture!

# 在ARM Mac上验证（预期自动选择arm64）
docker pull harbor.company.com/order-platform/hello-app:v2.0.0
docker inspect harbor.company.com/order-platform/hello-app:v2.0.0 --format '{{.Architecture}}'
# 预期输出：arm64

docker run --rm harbor.company.com/order-platform/hello-app:v2.0.0
# 预期输出：Hello from aarch64 architecture!

# 查看Manifest List的完整结构
docker manifest inspect harbor.company.com/order-platform/hello-app:v2.0.0
# 预期输出：
# {
#   "schemaVersion": 2,
#   "mediaType": "application/vnd.docker.distribution.manifest.list.v2+json",
#   "manifests": [
#     {
#       "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
#       "digest": "sha256:aaa111...",
#       "platform": {"architecture": "amd64", "os": "linux"}
#     },
#     {
#       "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
#       "digest": "sha256:bbb222...",
#       "platform": {"architecture": "arm64", "os": "linux"}
#     }
#   ]
# }
```

### 3.4 第三步：推送OCI Artifact（WASM模块）

**目标**：使用`oras`CLI将WASM模块推送到Harbor。

```bash
# 步骤1：安装oras CLI
ORAS_VERSION="1.1.0"
curl -LO "https://github.com/oras-project/oras/releases/download/v${ORAS_VERSION}/oras_${ORAS_VERSION}_linux_amd64.tar.gz"
mkdir -p /usr/local/oras
tar -xzf "oras_${ORAS_VERSION}_linux_amd64.tar.gz" -C /usr/local/oras/
sudo mv /usr/local/oras/oras /usr/local/bin/
oras version
# 预期输出：
# Version:        1.1.0
# Go version:     go1.21.6

# 步骤2：登录Harbor
oras login harbor.company.com -u admin -p Str0ng@Admin2024
# 预期输出：Login Succeeded

# 步骤3：准备一个示例WASM文件
cat > index.ts << 'EOF'
export function add(a: i32, b: i32): i32 {
    return a + b;
}
EOF
# 编译为WASM（需要AssemblyScript，这里用mock文件演示）
echo "WASM_BINARY_CONTENT" > module.wasm

# 步骤4：推送WASM模块到Harbor
oras push harbor.company.com/order-platform/wasm-module:v1.0.0 \
  --artifact-type application/wasm \
  --annotation "org.opencontainers.image.title=Image Processor WASM" \
  --annotation "org.opencontainers.image.description=WebAssembly module for edge image processing" \
  module.wasm

# 预期输出：
# Uploading  module.wasm
# Pushed harbor.company.com/order-platform/wasm-module:v1.0.0
# Digest: sha256:def456...

# 步骤5：从Harbor拉取WASM模块
oras pull harbor.company.com/order-platform/wasm-module:v1.0.0
# 预期输出：
# Downloading module.wasm
# Pulled harbor.company.com/order-platform/wasm-module:v1.0.0
# Digest: sha256:def456...

# 步骤6：在Harbor Portal中查看
# 项目 -> order-platform -> wasm-module -> v1.0.0
# 显示为非容器镜像类型的Artifact
```

### 3.5 第四步：给镜像附加SBOM

**目标**：生成SBOM并使用`oras attach`将其作为镜像的附属品存储。

```bash
# 步骤1：安装syft（SBOM生成工具）
curl -sSfL https://raw.githubusercontent.com/anchore/syft/main/install.sh | sh -s -- -b /usr/local/bin

# 步骤2：为镜像生成SPDX格式的SBOM
syft harbor.company.com/order-platform/order-service:v2.3.0 \
  -o spdx-json > sbom.spdx.json

# 查看SBOM内容（前30行）
head -30 sbom.spdx.json
# 预期输出（SPDX格式JSON）：
# {
#   "SPDXID": "SPDXRef-DOCUMENT",
#   "spdxVersion": "SPDX-2.3",
#   "name": "order-service:v2.3.0",
#   "packages": [
#     {
#       "name": "alpine-baselayout",
#       "versionInfo": "3.4.3-r1",
#       "SPDXID": "SPDXRef-Package-1"
#     },
#     ...
#   ]
# }

# 步骤3：将SBOM附加到镜像（作为Accessory）
oras attach harbor.company.com/order-platform/order-service:v2.3.0 \
  --artifact-type application/spdx+json \
  --annotation "org.opencontainers.image.title=SBOM" \
  --annotation "generated_by=syft" \
  sbom.spdx.json

# 预期输出：
# Uploading  sbom.spdx.json
# Attached to harbor.company.com/order-platform/order-service:v2.3.0
# Digest: sha256:ghi789...

# 步骤4：查看镜像关联的附属品
oras discover harbor.company.com/order-platform/order-service:v2.3.0 -o tree
# 预期输出：
# harbor.company.com/order-platform/order-service@sha256:abc123...
# ├── application/spdx+json
# │   └── sha256:ghi789... (SBOM)
# └── application/vnd.dev.cosign.simplesigning.v1+json
#     └── sha256:jkl012... (Cosign签名)

# 步骤5：从Harbor API获取附件的SBOM数据
curl -s -u admin:Str0ng@Admin2024 \
  "https://harbor.company.com/api/v2.0/projects/order-platform/repositories/order-service/artifacts/v2.3.0/additions/sbom" | jq '.'

# 步骤6：在Harbor Portal中查看
# 项目 -> order-platform -> order-service -> v2.3.0
# Accessories标签页 -> 可看到SBOM附件
```

### 3.6 第五步：自定义OCI Artifact类型

**目标**：推送自定义media type的制品（如公司内部的配置文件）。

```bash
# 步骤1：创建自定义配置文件
cat > app-config-v1.0.0.json << 'EOF'
{
  "version": "1.0.0",
  "service": "order-platform",
  "dependencies": {
    "libA": "2.1.0",
    "libB": "3.0.5"
  },
  "config": {
    "max_connections": 100,
    "timeout_ms": 5000
  }
}
EOF

# 步骤2：推送为OCI Artifact（使用自定义media type）
oras push harbor.company.com/order-platform/app-config:v1.0.0 \
  --artifact-type application/vnd.company.config.v1+json \
  --annotation "org.opencontainers.image.title=Order Service Config" \
  --annotation "environment=production" \
  app-config-v1.0.0.json:application/json

# 步骤3：拉取配置
oras pull harbor.company.com/order-platform/app-config:v1.0.0
cat app-config-v1.0.0.json

# 步骤4：查看Artifact的完整Manifest
oras manifest fetch harbor.company.com/order-platform/app-config:v1.0.0 | jq '.'
# 预期输出：
# {
#   "schemaVersion": 2,
#   "mediaType": "application/vnd.oci.image.manifest.v1+json",
#   "config": {
#     "mediaType": "application/vnd.company.config.v1+json",
#     "size": 2,
#     "digest": "sha256:empty-config..."
#   },
#   "layers": [
#     {
#       "mediaType": "application/json",
#       "size": 234,
#       "digest": "sha256:actual-content..."
#     }
#   ],
#   "annotations": {
#     "org.opencontainers.image.title": "Order Service Config",
#     "environment": "production"
#   }
# }
```

### 3.7 可能遇到的坑

**坑1：Manifest List缺少某个架构导致ARM节点部署失败**

现象：ARM节点部署时`ImagePullBackOff`，日志显示`no matching manifest for linux/arm64`，但x86节点正常。

根因：构建时`--platform`参数只设置了`linux/amd64`，遗漏了`linux/arm64`。或者CI流水线中某次构建只推了amd64的Manifest，覆盖了之前的Manifest List。

排查步骤：
```bash
# 检查Manifest List包含哪些架构
docker manifest inspect harbor.company.com/order-platform/hello-app:v2.0.0 | \
  jq '.manifests[].platform'
# 预期输出：
# {"architecture":"amd64","os":"linux"}
# {"architecture":"arm64","os":"linux"}
# 如果只输出一个架构 -> 重新构建多架构版本

# 使用crane工具检查
crane manifest harbor.company.com/order-platform/hello-app:v2.0.0 | \
  jq '.manifests[] | {digest, platform}'
```

**坑2：oras push的Artifact在Harbor Portal中类型显示为"Unknown"**

现象：Artifact成功push，Harbor中可以查看和pull，但Portal中类型显示为"Unknown"而非Expected类型。

根因：`--artifact-type`的值不在Harbor已知的media type映射表中。Harbor的Portal前端有一个media type到显示名称的映射表（如`application/spdx+json`映射为"SBOM"），未知的media type会fallback到"Unknown"。

解决方案：
```bash
# Harbor识别的已知media type（v2.12）：
# application/vnd.cncf.helm.config.v1+json -> Helm Chart
# application/vnd.dev.cosign.simplesigning.v1+json -> Cosign Signature
# application/vnd.sylabs.sif.config.v1+json -> Singularity
# application/spdx+json -> SBOM (SPDX)
# application/vnd.cyclonedx+json -> SBOM (CycloneDX)

# 如果使用自定义media type -> Portal会显示"Unknown"
# 这不影响Artifact的存储和分发（pull/push正常工作）
# 如果需要Portal正确显示 -> 使用上述已知media type之一，或等待Harbor版本更新
```

**坑3：oras attach后的SBOM没有自动关联镜像漏洞扫描**

现象：使用`oras attach`附加了SBOM到镜像，期望Harbor基于SBOM中的依赖信息进行更精确的漏洞分析，但扫描结果没有变化。

根因：Harbor当前的漏洞扫描引擎（Trivy/Clair）扫描镜像文件系统层来识别漏洞，**不使用附加的SBOM作为输入**。SBOM作为Accessory存储后，只是随镜像一起存储和分发（方便合规审计），不会影响扫描逻辑。

解决方案：
```bash
# 当前Harbor不自动使用附加SBOM进行扫描
# 如果需要基于SBOM的漏洞分析，需要自建流程：
# 1. 从Harbor API获取SBOM
# 2. 将SBOM输入到独立的漏洞分析工具（如grype sbom:sbom.json）
# 3. 将结果写回Harbor的自定义扫描器

# 集成示例：
curl -s -u admin:Str0ng@Admin2024 \
  "${HARBOR_URL}/api/v2.0/projects/${PROJECT}/repositories/${REPO}/artifacts/${TAG}/additions/sbom" \
  > sbom.json
grype sbom:sbom.json -o json > scan-result.json
```

**坑4：多架构镜像push覆盖了已有tag的单架构Manifest**

现象：之前用`docker push`推送了amd64镜像到`v2.0.0`标签。后来用`docker buildx --push`构建多架构并推送同一个`v2.0.0`标签后，amd64的镜像layer被重新上传（即使内容没变），导致存储翻倍。

根因：`docker buildx`无法复用已有的单架构Manifest。它会重新构建所有架构的镜像，即使某个架构的layer内容与之前完全一致。Registry虽然能通过content-addressable存储去重，但Manifest List是新建的，会创造新的引用链。

解决方案：
```bash
# 方案A：使用crane手动组装Manifest List（复用已有Manifest）
# 前提：已有 amd64 和 arm64 的独立Manifest
crane index append \
  -m harbor.company.com/order-platform/hello-app@sha256:amd64-digest \
  -m harbor.company.com/order-platform/hello-app@sha256:arm64-digest \
  -t v2.0.0

# 方案B：从最开始就使用 docker buildx（避免单架构push）
# 统一使用 buildx 构建和推送，不要混合 docker push 和 buildx push
```

**坑5：oras push的Artifact被Harbor垃圾回收误删**

现象：使用`oras push`上传的配置Artifact在一段时间后消失，Harbor日志显示GC删除了它们。

根因：Harbor的GC（垃圾回收）在特定条件下检查Artifact是否"被引用"。如果Artifact没有任何tag指向它、也没有被其他Artifact引用（作为Accessory），GC会认为它是"孤立的"并删除。

解决方案：
```bash
# 1. 确保oras push时带tag（不要只push而不打tag）
oras push harbor.company.com/app-config:v1.0.0 ...  # 有tag -> 安全

# 2. 使用oras attach（创建引用关系）
oras attach harbor.company.com/image:v1.0.0 ...     # 有subject引用 -> 安全

# 3. 在Harbor中禁用项目的自动GC（不推荐）
# Harbor Portal -> 项目 -> 配置 -> 垃圾回收策略
```

---

## 4 项目总结

### 4.1 OCI Artifact类型一览

| Artifact类型 | Media Type | 推送工具 | Harbor Portal显示 | Harbor扫描支持 | Tag方式 |
|-------------|------------|---------|------------------|--------------|---------|
| Docker Image | docker.v2+json | docker push | Container Image | ✅ 完整支持 | tag |
| OCI Image | oci.image.v1+json | buildah push | Container Image | ✅ 完整支持 | tag |
| Manifest List | oci.index.v1+json | docker buildx | Multi-arch | ✅ 完整支持 | tag |
| Helm Chart | helm.config.v1+json | helm push --oci | Helm Chart | ❌ 不支持 | tag |
| Cosign Signature | cosign.simplesigning | cosign sign | Cosign Signature | ❌ 不支持 | 自动（Accessory） |
| SBOM (SPDX) | spdx+json | oras attach | SBOM | ❌ 不支持 | 自动（Accessory） |
| WASM Module | application/wasm | oras push | Unknown | ❌ 不支持 | tag |
| 自定义配置 | 自定义media type | oras push | Unknown | ❌ 不支持 | tag |

### 4.2 适用场景

1. **多架构集群**：AMD64/ARM64混合部署（如x86云服务器 + ARM Mac开发机 + ARM边缘节点），统一使用Manifest List管理版本，消除"各架构各一个tag"的混乱。
2. **软件供应链安全**：SBOM + 签名凭证与镜像在同一个Harbor中存储和分发——合规审计时不需要去多个系统拼凑证据链。
3. **WASM边缘计算**：前端/边缘团队通过Harbor统一分发WASM模块，与容器镜像共用同一套权限、审计、复制机制。
4. **制品统一管理平台**：所有OCI兼容制品（镜像、Helm Chart、WASM、配置、SBOM、签名）在一个平台上统一权限、统一复制、统一审计。
5. **GitOps配置分发**：将Kubernetes的自定义CRD配置以OCI Artifact形式存储在Harbor中，通过Flux/ArgoCD的OCI源直接引用。

### 4.3 不适用场景

1. **超大非结构化文件**：如AI模型文件（数十GB的`.bin`/`.safetensors`），OCI Artifact的Layer模型要求全文件hash和一次性上传，不适合超大文件的增量更新和断点续传。推荐使用HuggingFace/DVC等专用工具。
2. **需要高频随机读写的制品**：如日志文件、数据库备份，OCI Artifact是immutable（不可变）的，每次更新需要重新push完整内容。不适合高频变更场景。

### 4.4 注意事项

1. **Manifest List本身不包含镜像层数据**：它是多个子Manifest的索引。删除Manifest List不会自动删除子Manifest的layer数据（如果子Manifest仍被其他tag引用）。
2. **OCI Artifact的push操作创建新Artifact**：不会覆盖现有镜像的Manifest（即使名字相同）。从v2.8开始，Harbor支持同名但不同media type的Artifact共存。
3. **Harbor对非容器镜像类型的制品不支持漏洞扫描**：扫描器（Trivy/Clair）仅能分析容器镜像的文件系统层。WASM、Helm Chart、配置文件等类型的Artifact不触发扫描。
4. **oras attach的Accessory关系是单向的**：镜像知道自己有哪些附属品，但附属品不知道自己被哪些镜像引用。如果需要反向查询，需要通过Harbor API的`/additions`端点遍历。
5. **Media type大小写敏感**：在Harbor的`artifact`表和Manifest中，media type是精确匹配的。`application/SPDX+JSON`和`application/spdx+json`被视为不同值，前者可能不被Harbor的Portal正确识别。

### 4.5 常见故障排查表

| 故障现象 | 根因 | 排查命令 | 解决方案 |
|---------|------|---------|---------|
| ARM节点ImagePullBackOff但x86正常 | Manifest List缺少ARM架构 | `docker manifest inspect` 检查架构列表 | 重新用buildx构建多架构镜像 |
| Portal中Artifact类型显示Unknown | media type不在Harbor已知列表中 | `oras manifest fetch` 检查mediaType字段 | 使用已知media type或等待版本更新 |
| 附加SBOM后扫描结果未变化 | Harbor不使用附加SBOM作为扫描输入 | 检查扫描报告时间戳 | 使用grype基于SBOM独立扫描 |
| 推送多架构后存储空间翻倍 | buildx重新构建了已有架构的layer | `docker buildx du` 检查cache | 使用crane手动组装Manifest List |
| GC删除了oras push的Artifact | Artifact无tag且无引用关系 | 检查Artifact的tag和引用列表 | 确保push时带tag，或使用attach创建引用 |
| oras pull失败但docker pull正常 | oras使用了不同认证方式 | `oras login` 确认credentials | 确保使用与docker login相同的Registry地址和凭据 |

### 4.6 深度思考

1. **为公司的所有生产镜像设计一个"SBOM自动附加"流程：CI构建完成后自动使用syft生成SBOM，通过`oras attach`附加到Harbor中的对应镜像。考虑失败场景——如果SBOM生成成功但attach失败（网络闪断），如何处理？是重试attach还是让CI流水线失败？attach操作是否是幂等的？**

2. **假设公司在Harbor中存储了大量WASM模块，通过CDN分发到全球300+边缘节点。Harbor是否适合作为CDN源站？考虑以下几点：（1）Harbor的Registry API是否高效支持CDN的回源请求（Range请求、条件GET等）？（2）Harbor的认证机制（Bearer Token）如何与CDN的公共访问需求兼容？（3）如果要支持<100ms的全球拉取延迟，是否需要像Dragonfly这样的P2P分发层？还是说Harbor的Proxy Cache + Registry复制就足够？**

---

> 下一章预告：第26章将重点攻克Harbor性能调优——百万级镜像仓库的优化策略。
