# 第24章：内容信任——Notary/Cosign 签名验证

## 1 项目背景

某金融公司的安全部门在年度红蓝对抗中发现了镜像供应链的致命漏洞——攻击者通过中间人攻击成功将恶意镜像注入到了生产环境的拉取链路中。这次事件暴露出公司在镜像来源验证方面的全面空白。

**痛点一：镜像来源不可验证。** 当前架构中，Kubernetes Node从Harbor拉取镜像时只验证了TLS传输加密——确保"传输过程没被窃听"，但没有验证"这个镜像确实是公司CI系统构建的那个原始镜像"。如果Harbor本身被攻破（恶意管理员或APT攻击），攻击者可以直接替换镜像内容而不会被任何机制发现。更可怕的场景：内部人员替换了一个生产镜像，所有Node默默拉取了恶意版本——安全团队可能数月后才从异常流量中发现。

**痛点二：供应链攻击防御为零。** 公司的所有Java服务都基于统一的基础镜像`openjdk:17`构建。如果这个基础镜像在上游被篡改（如Docker Hub账户被盗），所有基于它的300+业务镜像会自动继承攻击载荷。公司目前完全没有能力验证"这个基础镜像的每一层内容是否与构建时一致"——这等同于供应链上的所有环节都默认可信，违背了零信任安全原则。

**痛点三：合规审计强制要求签名。** 银保监会发布的《金融行业容器安全规范》第4.3条明确要求："生产环境中运行的容器镜像必须经过数字签名，部署时进行签名验证，验签记录存档至少1年。"即将到来的等保2.0三级测评也将镜像签名列入检查项。没有签名体系的团队将直接面临合规风险——罚款、业务整改甚至系统关停。

**痛点四：签名方案选型困难。** Harbor同时支持Notary（基于TUF框架的经典方案）和Cosign（基于Sigstore的新兴方案）。两套架构完全不同：Notary需要独立部署签名服务（运维复杂度高但防篡改能力强），Cosign将签名数据直接存储在Harbor中（无需额外服务但有中心化顾虑）。团队花了两周时间对比选型，仍然无法做出决策。更棘手的是——Notary在Harbor社区中已标记为Deprecated，但公司现有Notary签名的5000+镜像如何迁移？

本章将从数字签名的密码学原理出发，对比Notary与Cosign的架构差异，实战部署Cosign签名验证体系，并集成到CI/CD流水线中实现自动签名与验签。

---

## 2 项目设计——剧本式交锋对话

**场景：信息安全部会议室，安全负责人老王、架构师老刘和运维工程师小胖讨论镜像签名技术选型。投影仪上放着Notary和Cosign的架构图。**

**小胖**（运维工程师）："镜像签名不就是拿个私钥对镜像打个签名吗？跟PDF上加个电子签章一样——证明'这个文件是我签的'，有啥难的？"

**老王**（安全负责人）："小胖，你只理解了签名的'认证性'功能。在容器供应链安全中，签名还承担另外两个关键角色：
- **完整性**：证明镜像从签名那一刻至今没有被修改过任何一个字节
- **不可否认性**：签名者事后不能否认'我签过这个镜像'

但这跟PDF签章有两个根本区别：第一，镜像不是单一文件——它是一组Layer的Manifest结构，签名的对象是Manifest的digest，不是文件本身；第二，镜像签名需要考虑Secret管理、密钥轮换、签名过期等生命周期问题——你不能像PDF签章那样一个私钥用十年。"

**小胖**："那Notary和Cosign到底啥区别？我们技术选型纠结了两周了。"

**老王**："Notary和Cosign走的是完全不同的路线。它们的关系就像是'传统银行保险柜'和'区块链公证'。"

**Notary（TUF架构）——传统银行保险柜模型**：
```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│ Docker CLI  │────▶│ Notary Server│────▶│ Notary Signer│
│ trust sign  │     │ (签名数据库)  │     │ (密钥管理)    │
└─────────────┘     └──────────────┘     └─────────────┘
       │                                        │
       │          ┌──────────────┐              │
       └─────────▶│Harbor Registry│◀─────────────┘
                  │ (镜像存储)     │
                  └──────────────┘
```

"Notary是一套完整的**内容信任框架**（基于TUF——The Update Framework）。签名数据、密钥管理、时间戳服务都由Notary Server统一管理。好处是防篡改能力极强——Notary使用Merkle Tree防回滚攻击（攻击者不能在不被发现的情况下将镜像回滚到旧版本）。坏处是运维复杂——需要独立部署Notary Server + Signer两个服务。"

**Cosign（Sigstore架构）——区块链公证模型**：
```
Docker Client → cosign sign → .sig 文件存储在OCI Registry（Harbor）中
                               签名与镜像共存，无需额外服务！
                          
可选的透明日志：
cosign sign → 同时写入 Rekor 透明日志（公开可查、不可篡改）
```

"Cosign的核心设计哲学是**签名与制品共存**——签名作为OCI Artifact的一种（.sig文件），存储在同一个Registry中。这就像房产证和房子本身放在同一个保险柜里——查找方便，不需要额外的存储系统。"

**技术映射**：Notary的架构对应Harbor v1.x时代的原生签名方式（需要额外部署Notary服务）。Cosign从Harbor v2.0开始获得原生支持——Harbor可以直接识别和验证Cosign签名的.sig artifact。Harbor社区已明确将Notary标记为Deprecated，推荐新项目使用Cosign。

**小白**（高级开发）："Cosign的无密钥签名（Keyless Signing）模式我很好奇——不用管理私钥？那靠什么证明签名者的身份？"

**老王**："这正是Cosign最创新的地方。传统数字签名依赖'私钥≈身份'的模型——谁持有私钥，谁就是签名者。这个模型的致命弱点是私钥泄露——一旦私钥泄露，攻击者可以冒充签名者给任意镜像签名。

Cosign的无密钥模式完全颠覆了这个模型——它利用OIDC（OpenID Connect）身份认证：

```
流程详解：
1. 执行: cosign sign --keyless harbor.company.com/order-service:v2.3.0
2. cosign 打开浏览器 → 你通过 GitHub/Gmail/企业SSO 登录
   → 证明"我是 dev01@company.com"
3. OIDC Provider 返回 ID Token（JWT格式，包含你的邮箱等身份信息）
4. cosign 将 ID Token 发送给 Fulcio（Sigstore的CA服务器）
   → Fulcio 验证你的OIDC身份 → 签发一个短期X.509证书（仅10分钟有效）
5. cosign 使用证书中的私钥对镜像Manifest签名
   → 生成 .sig artifact → push到Harbor
6. 同时，签名记录和证书写入 Rekor（透明日志服务器）
   → Rekor返回一个不可篡改的日志条目证明
```

**技术映射**：这套流程的核心是"短期证书+透明日志"。10分钟有效期的证书几乎消除了私钥泄露的威胁（即使泄露，窗口也只有10分钟）。Rekor透明日志提供了不可否认性——任何人都可以查询Rekor确认某次签名确实发生过，且记录不可篡改。"

**小胖**："如果公司在纯内网环境（没有互联网访问），不能用GitHub/OIDC登录，无密钥签名还能用吗？"

**老王**："好问题。纯内网有三种方案：

1. **搭建内网Fulcio+Rekor**：部署Sigstore全套组件在企业内网（运维成本高，适合大型企业）。
2. **传统密钥对签名**：`cosign generate-key-pair`生成公私钥，私钥存HashiCorp Vault/K8s Secret中。虽然回到了密钥管理的模式，但比Notary简单。
3. **使用企业自有OIDC Provider**：如果公司有统一的SSO（如ADFS、Keycloak），可以配置为OIDC Provider，cosign直接对接——不需要公网。

大多数内网场景推荐方案2——密钥对模式。虽然不如无密钥优雅，但运维成本最低。"

**小白**："如果镜像被Harbor的Replication功能同步到了灾备中心，签名数据也会同步吗？灾备中心的镜像能通过签名验证吗？"

**老王**："这又是一个经典问题。答案取决于：签名数据如何存储。

**如果签名数据是Registry的一部分**（Cosign的.sig artifact），理论上Harbor的复制策略如果配置了同步accessories，签名数据会同步过去。但实践中常有坑——复制过滤器如果没有正确配置，.sig artifact可能被漏掉。验证方法：在灾备中心执行`cosign verify`确认。

**如果签名数据存储在外部系统**（Notary的签名数据库），需要额外同步Notary数据库到灾备中心——这是一个更大的运维挑战。

最佳实践：使用Cosign + Rekor透明日志。即使Harbor的.sig artifact丢失了，只要Rekor中有签名记录，就可以通过Rekor重新验证签名的真实性。Rekor天然是分布式公开的——不需要关心'同步'问题。"

---

## 3 项目实战

### 3.1 环境要求

| 组件 | 版本要求 | 说明 |
|------|---------|------|
| Harbor | v2.6.0+ | Cosign支持始于v2.0，内容信任策略始于v2.6 |
| Cosign CLI | v2.2.0+ | 签名工具，推荐使用最新稳定版 |
| Docker | 24.0+ | 镜像构建和推送，需支持`docker trust`或配合cosign |
| crane | v0.16+ | 可选，用于Registry操作调试 |
| GitLab CI / GitHub Actions | 任意版本 | CI/CD集成签名 |
| OIDC Provider（可选） | GitHub / Google / Keycloak | 无密钥签名需要 |
| Rekor Server（可选） | v1.3+ | 如需自建透明日志 |

### 3.2 第一步：安装Cosign并生成密钥对

**目标**：安装Cosign CLI，生成签名密钥对。

```bash
# ══════════════════════════════════════════════
# 安装Cosign
# ══════════════════════════════════════════════

# Linux AMD64
ARCH=$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/')
COSIGN_VERSION="v2.2.3"
curl -LO "https://github.com/sigstore/cosign/releases/download/${COSIGN_VERSION}/cosign-linux-${ARCH}"
sudo install -m 755 "cosign-linux-${ARCH}" /usr/local/bin/cosign

# 验证安装
cosign version
# 预期输出：
#  ______   ______        _______. __    _______ .__   __.
# /      | /  __  \      /       ||  |  /  _____||  \ |  |
# |  ,----'|  |  |  |    |   (----`|  | |  |  __  |   \|  |
# |  |     |  |  |  |     \   \    |  | |  | |_ | |  . `  |
# |  `----.|  `--'  | .----)   |   |  | |  |__| | |  |\   |
#  \______| \______/  |_______/    |__|  \______| |__| \__|
# cosign: A tool for Container Signing, Verification and Storage in an OCI registry.
# GitVersion:    v2.2.3
# GitCommit:     6a74c001e0c9b6106a5e8c8989a61be754533253
# GitTreeState:  clean
# BuildDate:     2024-01-15T18:30:00Z
# GoVersion:     go1.21.6
# Compiler:      gc
# Platform:      linux/amd64

# ══════════════════════════════════════════════
# 生成密钥对（传统模式）
# ══════════════════════════════════════════════
cosign generate-key-pair
# 交互式提示：
# Enter password for private key: ******      (为私钥设置密码)
# Enter password again: ******                (确认密码)
# Private key written to cosign.key           (私钥文件)
# Public key written to cosign.pub            (公钥文件)

# ⚠️ 关键安全实践：
# 1. 私钥密码必须足够复杂（≥16位，含大小写+数字+特殊字符）
# 2. 私钥文件(cosign.key)永远不要提交到Git仓库
# 3. 公钥文件(cosign.pub)可以公开分发
# 4. 生产环境建议将私钥存储在HashiCorp Vault或K8s Secret中

# 查看公钥（可以公开）
cat cosign.pub
# 预期输出：
# -----BEGIN PUBLIC KEY-----
# MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAE...
# -----END PUBLIC KEY-----

# 将私钥转换为Base64（用于CI/CD变量存储）
cat cosign.key | base64 -w0
# 存储此Base64字符串到CI/CD Secret变量 COSIGN_PRIVATE_KEY
```

### 3.3 第二步：对镜像签名与验证

**目标**：使用密钥对对Harbor中的镜像进行签名，并验证签名有效性。

```bash
# ══════════════════════════════════════════════
# 登录Harbor（cosign会复用docker的凭证）
# ══════════════════════════════════════════════
HARBOR_URL="harbor.company.com"
docker login ${HARBOR_URL} -u admin -p Str0ng@Admin2024
# 或使用机器人账户（推荐）
docker login ${HARBOR_URL} -u 'robot$order-platform+push' -p ${ROBOT_TOKEN}

# ══════════════════════════════════════════════
# 对镜像签名
# ══════════════════════════════════════════════
IMAGE="${HARBOR_URL}/order-platform/order-service:v2.3.0"

# 签名（输入私钥密码）
cosign sign --key cosign.key ${IMAGE}
# 预期交互：
# Enter password for private key: ******
# Pushing signature to: harbor.company.com/order-platform/order-service
# 输出：签名已推送到Harbor中的 .sig artifact

# 查看Harbor Portal中的签名信息
# 项目 → order-platform → order-service → v2.3.0
# Accessories 标签页 → 可以看到 cosign 签名 artifact

# ══════════════════════════════════════════════
# 验证签名
# ══════════════════════════════════════════════
cosign verify --key cosign.pub ${IMAGE}
# 预期输出：
# Verification for harbor.company.com/order-platform/order-service:v2.3.0 --
# The following checks were performed on each of these signatures:
#   - The cosign claims were validated
#   - The signatures were verified against the specified public key
#   - Any certificates were verified against the Fulcio roots.
# 
# [
#   {
#     "critical": {
#       "identity": {"docker-reference": "harbor.company.com/order-platform/order-service"},
#       "image": {"docker-manifest-digest": "sha256:abc123..."},
#       "type": "cosign container image signature"
#     },
#     "optional": null
#   }
# ]

# ⚠️ 如果签名验证失败，cosign 会返回非零退出码
```

### 3.4 第三步：无密钥签名（Keyless模式）

**目标**：使用Cosign的无密钥签名模式，通过OIDC身份认证签名。

```bash
# ══════════════════════════════════════════════
# 交互式无密钥签名（本地开发环境）
# ══════════════════════════════════════════════
cosign sign ${IMAGE}
# 1. 浏览器自动打开，重定向到OIDC Provider登录页面
# 2. 选择登录方式（GitHub / Google / Microsoft）
# 3. 登录成功后，Fulcio签发短期证书
# 4. cosign用证书私钥签名 → push .sig到Harbor
# 5. 签名记录写入Rekor透明日志
# 预期输出：
# Generating ephemeral keys...
# Retrieving signed certificate...
# Your browser will now be opened to:
# https://oauth2.sigstore.dev/auth/auth?...
# Successfully verified SCT...
# tlog entry created with index: 12345678
# Pushing signature to: harbor.company.com/order-platform/order-service

# ══════════════════════════════════════════════
# 验证无密钥签名（需要指定签名者身份）
# ══════════════════════════════════════════════
cosign verify \
  --certificate-identity "dev01@company.com" \
  --certificate-oidc-issuer "https://accounts.google.com" \
  ${IMAGE}
# 要求同时匹配签名者邮箱和OIDC Issuer
# 预期输出：同上

# ══════════════════════════════════════════════
# CI/CD环境中的无密钥签名（GitHub Actions）
# ══════════════════════════════════════════════
# GitHub Actions自动提供OIDC Token，无需浏览器交互
```

```yaml
# .github/workflows/build-and-sign.yml
name: Build and Sign
on:
  push:
    branches: [main]

jobs:
  sign:
    runs-on: ubuntu-latest
    permissions:
      id-token: write       # 必需：允许获取OIDC Token
      contents: read
    steps:
      - uses: actions/checkout@v4
      
      - name: Build and Push Image
        run: |
          docker build -t harbor.company.com/order-platform/order-service:${{ github.sha }} .
          docker push harbor.company.com/order-platform/order-service:${{ github.sha }}
      
      - name: Install Cosign
        uses: sigstore/cosign-installer@v3
        with:
          cosign-release: 'v2.2.3'
      
      - name: Sign Image (Keyless)
        run: |
          cosign sign \
            harbor.company.com/order-platform/order-service:${{ github.sha }}
        env:
          COSIGN_EXPERIMENTAL: "true"  # 启用Keyless模式
          # GitHub Actions自动提供ACTIONS_ID_TOKEN_REQUEST_TOKEN
      
      - name: Verify Signature
        run: |
          cosign verify \
            --certificate-identity "https://github.com/${{ github.repository }}/.github/workflows/build-and-sign.yml@${{ github.ref }}" \
            --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
            harbor.company.com/order-platform/order-service:${{ github.sha }}
```

### 3.5 第四步：Harbor中启用内容信任策略

**目标**：在Harbor项目级别启用内容信任，强制只允许拉取已签名的镜像。

```bash
# ══════════════════════════════════════════════
# 在项目配置中启用内容信任
# ══════════════════════════════════════════════
PROJECT_ID=1

# 启用内容信任策略
curl -X PUT -u admin:Str0ng@Admin2024 \
  -H "Content-Type: application/json" \
  -d '{"content_trust_enabled": true}' \
  "${HARBOR_URL}/api/v2.0/projects/${PROJECT_ID}"

# 验证策略已启用
curl -s -u admin:Str0ng@Admin2024 \
  "${HARBOR_URL}/api/v2.0/projects/${PROJECT_ID}" | jq '.content_trust_enabled'
# 预期输出：true

# ══════════════════════════════════════════════
# 测试效果：未签名镜像无法拉取
# ══════════════════════════════════════════════

# 推送一个未签名的镜像
docker tag alpine:3.19 ${HARBOR_URL}/order-platform/unsigned-app:v1.0.0
docker push ${HARBOR_URL}/order-platform/unsigned-app:v1.0.0

# 尝试拉取（预期失败）
docker pull ${HARBOR_URL}/order-platform/unsigned-app:v1.0.0
# ❌ Error response from daemon: 
#    The image is not signed in Notary/Cosign.
#    Please sign it before pulling.

# 对此镜像签名
cosign sign --key cosign.key ${HARBOR_URL}/order-platform/unsigned-app:v1.0.0

# 再次拉取（预期成功）
docker pull ${HARBOR_URL}/order-platform/unsigned-app:v1.0.0
# ✅ Digest: sha256:xyz789...
# ✅ Status: Downloaded newer image

# ══════════════════════════════════════════════
# 配置Cosign公钥到项目中（用于自动验签）
# ══════════════════════════════════════════════

# Harbor支持通过公钥自动验证Cosign签名
# 在项目配置中上传Cosign公钥
PUBKEY_BASE64=$(cat cosign.pub | base64 -w0)
curl -X POST -u admin:Str0ng@Admin2024 \
  -H "Content-Type: application/json" \
  -d "{
    \"project_id\": ${PROJECT_ID},
    \"public_key\": \"${PUBKEY_BASE64}\",
    \"name\": \"cosign-production-key\"
  }" \
  "${HARBOR_URL}/api/v2.0/projects/${PROJECT_ID}/trust-policies"
```

### 3.6 第五步：CI/CD中集成自动签名

**目标**：在GitLab CI流水线中自动对构建的镜像签名。

```yaml
# ══════════════════════════════════════════════
# .gitlab-ci.yml — 自动签名集成
# ══════════════════════════════════════════════
stages:
  - build
  - sign
  - deploy

variables:
  HARBOR_URL: "harbor.company.com"
  IMAGE: "${HARBOR_URL}/${CI_PROJECT_PATH}:${CI_COMMIT_SHORT_SHA}"

build:
  stage: build
  image: docker:24-dind
  script:
    - docker build -t ${IMAGE} .
    - docker push ${IMAGE}
  # Harbor自动扫描 + 签名验证（如果内容信任已开启，push也需要签名？不，push不需要，pull才需要）

sign:
  stage: sign
  image: bitnami/cosign:2.2.3
  before_script:
    # 从CI/CD变量恢复私钥
    - echo "${COSIGN_PRIVATE_KEY_BASE64}" | base64 -d > cosign.key
    - chmod 600 cosign.key
  script:
    - |
      # 对最新构建的镜像签名
      cosign sign --key cosign.key \
        --yes \
        ${IMAGE}
      # --yes: 跳过"是否确认签名"的交互式确认
  after_script:
    # 清理私钥（安全最佳实践）
    - shred -u cosign.key 2>/dev/null || rm -f cosign.key

verify-deploy:
  stage: deploy
  image: bitnami/cosign:2.2.3
  before_script:
    - echo "${COSIGN_PUBLIC_KEY}" > cosign.pub
  script:
    - |
      # 部署前验证签名
      cosign verify --key cosign.pub ${IMAGE}
      if [ $? -eq 0 ]; then
        echo "✅ Signature verified! Proceeding with deployment..."
        kubectl set image deployment/${CI_PROJECT_NAME} \
          app=${IMAGE}
      else
        echo "❌ Signature verification FAILED! Deployment BLOCKED."
        exit 1
      fi
  only:
    - main
    - /^release-.*$/
```

### 3.7 第六步：签名生命周期管理

**目标**：实现签名密钥轮换和多签名者策略。

```bash
# ══════════════════════════════════════════════
# 密钥轮换：为镜像添加新密钥的签名
# ══════════════════════════════════════════════

# 1. 生成新的密钥对
cosign generate-key-pair --output-key-prefix cosign-v2

# 2. 对已有镜像用新密钥签名（保留旧签名）
cosign sign --key cosign-v2.key ${IMAGE}

# 3. Harbor现在同时拥有旧密钥和新密钥的两个签名
# 验证时指定新公钥
cosign verify --key cosign-v2.pub ${IMAGE}

# ══════════════════════════════════════════════
# 多签名者策略：要求镜像同时由CI系统+安全团队签名
# ══════════════════════════════════════════════

# CI系统签名（构建时）
cosign sign --key ci-key.key --annotation "role=ci-builder" ${IMAGE}

# 安全团队签名（审核后）
cosign sign --key security-team.key --annotation "role=security-approver" ${IMAGE}

# 部署时验证：必须有两个签名
cosign verify --key ci-key.pub ${IMAGE} && \
  cosign verify --key security-team.pub ${IMAGE}
# 只有两个签名都通过才允许部署 — 实现了"四眼原则"

# ══════════════════════════════════════════════
# 签名撤销：移除旧的、被泄露密钥的签名
# ══════════════════════════════════════════════

# 注意：Cosign本身不支持"撤销"签名（签名一旦写入Registry就是不可变的）
# 替代方案：
# 1. 快速轮换公钥（在Harbor中更新信任的公钥）
# 2. 删除被泄露密钥签名的镜像版本
# 3. 使用Rekor的"retract"功能标记签名失效
```

### 3.8 可能遇到的坑

**坑1：无密钥签名在CI中无法弹出浏览器**

现象：在GitLab CI Runner中执行`cosign sign`时，cosign尝试打开浏览器进行OIDC认证，但CI环境没有浏览器。

根因：无密钥签名的交互式流程依赖浏览器进行OIDC认证。非交互式环境（CI/CD Runner）中无法弹出浏览器。

解决方案：
```bash
# 方案A：使用GitHub Actions（自动提供OIDC Token）
# GitHub Actions的id-token权限自动生成OIDC Token
# cosign直接使用此Token，无需浏览器

# 方案B：使用密钥对模式（CI中传统方案）
cosign sign --key cosign.key --yes ${IMAGE}

# 方案C：自建OIDC Provider + cosign的 --identity-token 参数
# 先通过API获取OIDC Token，再传给cosign
OIDC_TOKEN=$(curl https://sso.company.com/token ...)
cosign sign --identity-token "${OIDC_TOKEN}" ${IMAGE}
```

**坑2：签名后Harbor Portal仍然显示"该镜像未签名"**

现象：`cosign sign`成功执行（终端显示签名已推送），但在Harbor Portal中查看Artifact时，内容信任状态仍显示为"未签名"。

根因分析（按优先级排查）：
1. **项目内容信任未启用**：在Harbor Portal中检查项目设置 → 配置 → 内容信任（Content Trust）是否勾选。
2. **签名未正确关联到Artifact**：Cosign的签名作为一个独立的OCI Artifact（.sig）存储。如果推送时Registry返回错误或被中间代理修改，签名可能未正确关联。
3. **Harbor版本问题**：Harbor v2.0-v2.4对Cosign的支持有限，部分签名字段可能无法正确解析。v2.6+版本完善。

排查步骤：
```bash
# 本地验证签名是否正确上传
cosign verify --key cosign.pub ${IMAGE}
# 如果本地验证成功 → 问题在Harbor侧

# 查看签名artifact是否存在
oras discover ${IMAGE} -o tree
# 预期输出：
# harbor.company.com/order-platform/order-service:v2.3.0
# └── application/vnd.dev.cosign.simplesigning.v1+json
#     └── sha256:abc123... (.sig签名artifact)

# 如果签名不存在 → 检查push是否有错误（网络、权限等）
# 如果签名存在但Harbor不识别 → 升级Harbor版本
```

**坑3：Cosign复用Docker凭据失败**

现象：执行`cosign sign`时报错`Error: getting remote image: GET https://harbor.company.com/v2/... UNAUTHORIZED`。

根因：Cosign默认尝试复用Docker CLI的认证凭据（读取`~/.docker/config.json`）。如果：
- Docker login的是不同的Registry地址
- Docker config.json的格式为旧版
- 凭据存储使用的是credential helper（如`docker-credential-osxkeychain`）且cosign不支持该helper

解决方案：
```bash
# 方案A：确保docker login使用了正确的Registry地址
docker login harbor.company.com -u admin -p Str0ng@Admin2024

# 方案B：使用COSIGN_PASSWORD环境变量（密钥对模式）
export COSIGN_PASSWORD="your-private-key-password"

# 方案C：显式指定Registry用户名和密码
cosign login harbor.company.com -u admin -p Str0ng@Admin2024

# 方案D：使用机器人账户Token（推荐生产环境）
cosign login harbor.company.com \
  -u 'robot$order-platform+push' \
  -p ${HARBOR_ROBOT_TOKEN}
```

**坑4：签名镜像的digest在后续操作中变化**

现象：对镜像签名成功后，尝试用带有digest的引用`harbor.company.com/image@sha256:abc...`验证时失败，但使用tag引用可以验证成功。

根因：某些Registry操作（如Harbor的tag保留策略、复制）可能会改变镜像的Manifest（如修改annotations），导致digest变化。签名是基于特定Manifest digest的——digest一变，签名就失效。

解决方案：
```bash
# 使用不可变tag或digest引用
# 推荐：使用tag引用并确保镜像不被覆盖
# 或：使用Harbor的不可变tag策略

# 在Harbor项目中启用不可变tag
curl -X PUT -u admin:Str0ng@Admin2024 \
  -H "Content-Type: application/json" \
  -d '{"immutable_tag": true}' \
  "${HARBOR_URL}/api/v2.0/projects/${PROJECT_ID}"
```

---

## 4 项目总结

### 4.1 Notary vs Cosign 对比

| 维度 | Notary (TUF) | Cosign (Sigstore) |
|------|-------------|-------------------|
| 额外服务依赖 | 需要 Notary Server + Signer | 无需（签名存Registry） — 可选Rekor透明日志 |
| 密钥管理模型 | Notary中心化管理（目标/委派/快照多层密钥） | 自管密钥 或 Fulcio短期证书（无密钥模式） |
| 防回滚攻击 | ✅ Merkle Tree（时间戳链防回滚） | 依赖Rekor透明日志（带Merkle Tree） |
| 时间戳服务 | ✅ TUF内置时间戳 | 依赖Rekor提供时间戳 |
| 签名存储位置 | Notary独立数据库 | OCI Artifact（与镜像共存于Registry） |
| 运维复杂度 | ⭐⭐⭐⭐ 高（独立服务 + 数据库） | ⭐⭐ 低（零额外基础设施） |
| 社区活跃度 | ⚠️ Harbor社区已标记Deprecated | ✅ CNCF孵化项目，活跃开发 |
| 生态集成 | Docker CLI原生支持（`docker trust`） | K8s(Gatekeeper/Kyverno)、Tekton、GitHub Actions |
| 推荐场景 | 遗留Notary项目维护 | 新项目首选、CI/CD自动签名 |

### 4.2 适用场景

1. **金融/政务等强合规行业**：满足等保2.0三级和银保监会对镜像签名的强制要求，验签记录存档不少于1年。
2. **供应链安全防护**：验证基础镜像和业务镜像从构建到部署未经任何篡改，防止中间人攻击和内鬼篡改。
3. **多团队协作鉴权**：每个团队使用各自的Cosign密钥签名，部署时通过验签确认镜像来源。配合Kubernetes准入控制器（如Kyverno），可实现"只有特定团队签名的镜像才能部署到生产环境"。
4. **GitOps流水线**：GitOps中所有变更通过Git PR触发——在CI中集成Cosign签名，确保只有经过CI流水线签名的镜像才能被ArgoCD/Flux部署。
5. **审计与溯源**：通过Rekor透明日志实现"不可否认"的镜像发布记录——任何时候都可以证明"某镜像确实在某个时间点由某人签名发布"。

### 4.3 不适用场景

1. **高频临时构建**（每次commit都构建镜像，生命周期<1小时）：签名带来的额外开销（每次构建+签名需要额外5-15秒）和密钥管理复杂度，对于短生命周期镜像不值得。
2. **完全隔离的离线环境且无合规要求**：部署Fulcio+Rekor全套基础设施的成本过高，传统密钥对签名模式又需要管理密钥生命周期，ROI难以证明。

### 4.4 注意事项

1. **签名不是加密**：签名后的镜像仍然可以被任何人读取（内容透明），签名只是验证"镜像确实由声称的构建者构建，且内容未经篡改"。如果需要对镜像内容加密，需要额外的加密方案（如OCI Encryption）。
2. **Cosign无密钥模式的证书有效期仅10分钟**：镜像必须在证书签发后10分钟内完成签名。这意味着大镜像（>5GB）可能因为push时间过长导致证书过期——对大镜像推荐使用密钥对签名模式。
3. **Harbor启用内容信任后，未签名镜像的pull会被完全阻止**（不只是警告）。这意味着所有已有镜像都需要补齐签名——这可能是一个巨大的改造工作量。建议分阶段实施：先对新增镜像签名，再逐步回溯签名历史镜像。
4. **签名私钥的泄露等同于安全体系崩溃**：攻击者获得私钥后可以给恶意镜像签名。必须将私钥存储在企业级密钥管理系统（HashiCorp Vault/AWS KMS/Azure Key Vault）中，而非CI/CD的环境变量。
5. **Rekor透明日志是公开的**：如果你使用公共Rekor实例（`rekor.sigstore.dev`），签名记录中的元数据（如镜像名、时间戳）对所有人可见。如果这是隐私问题，需要自建私有Rekor实例。

### 4.5 常见故障排查表

| 故障现象 | 根因 | 排查命令 | 解决方案 |
|---------|------|---------|---------|
| `cosign verify`返回"no matching signatures" | 签名未上传或使用了错误的公钥 | `oras discover ${IMAGE} -o tree` 查看签名artifact | 重新签名，确认公钥匹配 |
| CI中cosign报"cannot open browser" | 无浏览器环境（CI Runner无GUI） | 检查`COSIGN_EXPERIMENTAL`环境变量 | 使用密钥对模式或OIDC Token直接注入 |
| Harbor Portal中签名状态仍显示"未签名" | 项目内容信任未启用或签名未关联 | 检查项目设置和签名artifact引用关系 | 启用内容信任，重新签名 |
| 灾备中心的镜像签名验证失败 | 复制策略未同步签名artifact | `curl harbor-dr/api/.../artifacts/${TAG}?with_accessory=true` | 修改复制策略包含accessories，重新执行复制 |
| 镜像digest变化导致旧签名失效 | 策略操作（tag保留/版本清理）修改了Manifest | 对比digest：`docker manifest inspect ${IMAGE}` | 使用不可变tag，或digest变化后重新签名 |
| Rekor中查不到签名记录 | 无密钥签名时Rekor服务不可达 | `cosign verify --key cosign.pub ${IMAGE} 2>&1 \| grep tlog` | 检查网络、Rekor服务状态，或使用自建Rekor |

### 4.6 深度思考

1. **Harbor的镜像复制功能将镜像同步到灾备中心时，Cosign签名数据（.sig artifact）是否会同步？如果不会（或复制失败了），如何确保灾备中心的镜像也能通过签名验证？考虑一个折中方案：在灾备中心独立运行签名验证时，通过Rekor透明日志重建信任链，而不依赖.sig artifact的同步。**

2. **设计一个完整的"签名生命周期管理"策略，涵盖以下场景：（1）签名创建——CI构建完成时自动签名；（2）签名更新——密钥轮换时对存量镜像重新签名；（3）签名撤销——密钥泄露时的应急响应（如何快速定位所有受影响的镜像、如何阻止它们被部署）；（4）签名审计——如何设计Rekor查询方式来满足"验签记录存档不少于1年"的合规要求？**

---

> 下一章预告：第25章将深入OCI规范兼容性——Multi-Arch Manifest、OCI Artifacts与Harbor的扩展支持。
