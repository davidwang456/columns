# 第11章：SBOM 生成、消费与供应链安全初探

> 版本：Trivy v0.50+
> 面向人群：安全工程师、架构师、供应链治理专员

---

## 1. 项目背景

### 业务场景

云帆科技拿下了一个金融行业的大客户。合同签署前，客户的信息安全团队扔过来一份长达 20 页的《第三方软件安全评估问卷》，其中核心要求是：「请提供所有交付软件的完整 SBOM（Software Bill of Materials，软件物料清单），格式必须为 CycloneDX 或 SPDX，并附带漏洞关联报告。」

小胖第一次听说 SBOM 这个词，以为是某种新的配置文件格式。他随手把项目的 `package.json` 打包发了过去，结果被客户退回了三次：「这不是 SBOM，这只是依赖声明文件。我们需要的是符合 NTIA 最小元素标准的结构化清单，包含每个组件的名称、版本、哈希、供应商、许可证、PURL 和 CPE。」

与此同时，公司内部也发生了一次供应链惊魂事件。运维老李在排查一个线上故障时，发现生产环境运行的 `log4j-core` 版本是 `2.14.1`——这正是 Log4Shell（CVE-2021-44228）的 vulnerable 版本。但开发坚称他们已经在 `pom.xml` 里升级到了 `2.17.0`。问题出在哪里？原来，构建服务器缓存了一个旧的 Maven 依赖，而团队的「依赖清单」只记录了 `pom.xml` 里的声明版本，没有记录实际进入镜像的版本。

CTO 在复盘会上拍板：「从下个月起，每个发版的镜像都必须附带 SBOM。我们要知道每个版本里到底有什么，而不是『应该有什么』。这是客户的要求，更是我们自己的保命符。」

### 痛点放大

**第一，「声明依赖」不等于「实际依赖」。** `package.json`、`pom.xml`、`go.mod` 记录的是开发者「声明」要用的包，但由于锁定文件不一致、缓存问题、构建脚本注入等因素，实际进入最终产物的依赖可能与声明完全不同。SBOM 的价值在于它描述的是「实际产物中的组件」，而非「意图」。

**第二，漏洞响应时找不到「受影响范围」。** 当一个新的高危漏洞（如 Log4Shell、Spring4Shell）爆发时，安全团队面临的第一个问题是：「我们的哪些产品、哪些版本、哪些组件受到了影响？」如果没有 SBOM，这个问题可能需要几天甚至几周才能回答——需要逐个团队排查、逐个镜像反编译。而有了 SBOM，只需要在数据库里查一条 PURL（Package URL），几秒钟就能给出完整的受影响清单。

**第三，供应链透明度不足。** 现代软件像俄罗斯套娃——你的依赖的依赖的依赖，可能来自一个个人开发者维护的 GitHub 仓库。当这个上游仓库被投毒（如 colors.js、node-ipc 事件），下游数千个项目同时中招。SBOM 让你至少知道「套娃」里每一层是什么，出了问题知道该找谁。

**第四，合规要求倒逼。** 美国政府 2021 年的行政命令 EO 14028 要求所有向联邦政府供应软件的厂商提供 SBOM。欧盟的 Cyber Resilience Act 也将 SBOM 列为强制要求。即使你的客户在国内，越来越多的大型企业（金融、电信、能源）也开始要求供应商提供 SBOM。

**本章的核心目标是：掌握用 Trivy 生成 CycloneDX 和 SPDX 格式 SBOM 的方法，理解 SBOM 的字段语义，学会从 SBOM 反查漏洞，建立「生成 → 存储 → 审计 → 应急响应」的供应链安全基础流程。**

---

## 2. 项目设计

**场景**：云帆科技的交付准备会，项目经理老王、架构师小白和开发小胖正在讨论如何满足客户的 SBOM 要求。

---

**小胖**：（看着客户的 SBOM 要求文档）「这什么 PURL、CPE、SHA-256 的，比我写代码还复杂。客户要这玩意儿到底能干啥？」

**小白**：「你可以把 SBOM 理解为软件的『配料表』。就像买一包零食，配料表上写着小麦粉、植物油、食用盐——你知道自己吃了什么。SBOM 就是告诉你，这个软件『吃』了哪些开源组件、每个组件是什么版本、来自哪个厂商、有什么许可证。」

**大师**：「技术映射：更准确的比喻是『汽车零件清单』。当某个品牌的刹车片被召回时，4S 店不需要拆开你的车检查，只需要查一下你这款车的 BOM（物料清单），就能知道你的车装没装这个刹车片。SBOM 的作用也是一样——当某个开源组件爆出漏洞时，安全团队不需要反编译你的镜像，只需要查 SBOM 就能判断你是否受影响。」

**小胖**：「那 `package.json` 不就是配料表吗？为什么客户不接受？」

**小白**：「`package.json` 只列出了第一层原料，没写添加剂、没写产地、没写批次号。SBOM 的标准格式（CycloneDX / SPDX）要求包含：

- **组件身份**：名称、版本、供应商、PURL（唯一标识）。
- **完整性校验**：文件的 SHA-256、SHA-512 哈希，防止组件被篡改。
- **许可证信息**：每个组件的开源许可证。
- **依赖关系**：谁依赖谁，形成完整的依赖树。
- **漏洞关联**：与漏洞数据库的映射关系。」

**老王**：「客户还要求『附带漏洞关联报告』。是不是我们把 SBOM 和 Trivy 的漏洞报告一起打包发过去就行？」

**大师**：「可以这么做，但更高效的方式是直接用 `trivy sbom` 命令。Trivy 不仅能生成 SBOM，还能读取 SBOM 文件，从中提取组件列表并反查漏洞。客户收到你的 CycloneDX SBOM 后，自己也可以用 Trivy 或其他工具扫描其中的漏洞。这是一种『可验证的信任』——你提供的 SBOM 是透明的，客户可以独立验证。」

**小胖**：「那 SBOM 应该在什么时候生成？构建前还是构建后？」

**小白**：「必须是构建后。构建前的 `pom.xml` 只是『计划』，构建后的镜像才是『事实』。Trivy 的 `trivy image --format cyclonedx` 就是基于实际镜像的内容生成 SBOM，包括所有实际安装的 OS 包和语言包。」

**大师**：「而且 SBOM 应该和镜像一起『签名』和『存储』。想象一下：你发了一个 v1.2.3 版本的镜像，同时生成了对应的 SBOM。三个月后 v1.2.3 还在生产环境运行，但开发已经改到 v2.0.0 了——你必须确保能随时找到 v1.2.3 的 SBOM，而不是拿 v2.0.0 的 SBOM 凑合。」

**老王**：「存储在哪里？Git 仓库里？」

**小白**：「Git 仓库可以存，但更好的方式是存在镜像仓库的 OCI Artifact 中，或者存在专门的 SBOM 管理系统（如 Dependency-Track、FOSSology）。OCI Registry 从 1.1 规范开始支持存储任意 Artifact，你可以把 SBOM 作为镜像的『附件』一起 push，确保 SBOM 和镜像的生命周期一致。」

**小胖**：「听起来 SBOM 不只是给客户看的，更是我们自己的应急工具？」

**大师**：「对。当 Log4Shell 爆发时，有 SBOM 的团队可以在 5 分钟内回答『我们受影响吗』；没有 SBOM 的团队可能需要 5 天。这 5 天的差距，可能决定了你是成为新闻里的『受害公司』，还是成为『快速响应的标杆』。」

---

## 3. 项目实战

### 环境准备

- **Trivy**：v0.50+，已安装
- **测试镜像**：`python:3.4-alpine`（用于 SBOM 生成）
- **工具**：`jq`（JSON 查询）、`curl`（可选，用于 OCI Artifact 上传）

### 步骤一：生成 CycloneDX 格式的 SBOM

**目标**：从镜像生成符合行业标准的 SBOM。

```bash
# 生成 CycloneDX JSON 格式的 SBOM
trivy image --format cyclonedx --output sbom.cyclonedx.json python:3.4-alpine

# 查看文件大小和结构
ls -lh sbom.cyclonedx.json
head -100 sbom.cyclonedx.json
```

**关键字段解读**：
```json
{
  "bomFormat": "CycloneDX",
  "specVersion": "1.5",
  "components": [
    {
      "type": "library",
      "name": "openssl",
      "version": "1.1.1g-r0",
      "purl": "pkg:apk/alpine/openssl@1.1.1g-r0?arch=x86_64",
      "properties": [
        { "name": "aquasecurity:trivy:PkgType", "value": "alpine" }
      ]
    }
  ]
}
```

- `bomFormat`：标识这是 CycloneDX 格式。
- `components`：镜像中包含的所有组件列表。
- `purl`：Package URL，组件的唯一标识符，格式为 `pkg:类型/命名空间/名称@版本`。
- `properties`：Trivy 附加的元数据，如包类型、Layer ID 等。

### 步骤二：生成 SPDX 格式的 SBOM

**目标**：对比 SPDX 格式与 CycloneDX 的差异。

```bash
# 生成 SPDX JSON 格式
trivy image --format spdx-json --output sbom.spdx.json python:3.4-alpine

# 对比两个文件的大小
ls -lh sbom.*.json
```

**格式对比**：

| 维度 | CycloneDX | SPDX |
|------|-----------|------|
| 设计目标 | 安全 + 供应链 | 许可证合规 + 法律 |
| 漏洞扩展 | 原生支持 vulnerabilities 扩展 | 需通过外部文件关联 |
| 依赖关系 | 依赖图（dependency graph） | 关系断言（relationships） |
| 哈希支持 | SHA-256, SHA-512, MD5 | SHA-1, SHA-256 |
| 工具生态 | Dependency-Track、Sonatype | FOSSology、SW360 |

**选择建议**：
- 如果主要用于**安全漏洞管理**，选 CycloneDX（与 Trivy、Dependency-Track 集成更好）。
- 如果主要用于**法务合规审计**，选 SPDX（法律认可度更高）。

### 步骤三：从 SBOM 反查漏洞

**目标**：验证 SBOM 的「可消费性」——客户收到 SBOM 后，能否独立发现其中的漏洞。

```bash
# 使用 trivy sbom 子命令，从 SBOM 文件反查漏洞
trivy sbom --severity HIGH,CRITICAL sbom.cyclonedx.json
```

**预期输出**：
```
sbom.cyclonedx.json (cyclonedx)
================================
Total: 15 (HIGH: 10, CRITICAL: 5)

┌─────────────┬────────────────┬──────────┬────────┬───────────────────┐
│   Library   │ Vulnerability  │ Severity │ Status │   Fixed Version   │
├─────────────┼────────────────┼──────────┼────────┼───────────────────┤
│ openssl     │ CVE-2022-0778  │ HIGH     │ fixed  │ 1.1.1l-r0         │
│ sqlite      │ CVE-2019-8457  │ CRITICAL │ fixed  │ 3.28.0-r0         │
└─────────────┴────────────────┴──────────┴────────┴───────────────────┘
```

**关键价值**：
- 客户不需要你的镜像，只需要 SBOM 文件，就能独立评估安全风险。
- 这建立了供应链中的「可验证信任」——你不能在 SBOM 里隐瞒组件，因为客户可以自己扫描验证。

### 步骤四：将 SBOM 作为 OCI Artifact 存储

**目标**：把 SBOM 和镜像绑定存储，确保生命周期一致。

```bash
# 安装 oras 工具
# 为镜像生成 SBOM
trivy image --format cyclonedx --output sbom.json myapp:v1.2.3

# 使用 oras 将 SBOM 附加到镜像的 OCI Index
oras attach --artifact-type application/vnd.cyclonedx+json \
  --file sbom.json \
  myregistry.io/cloud-sail/myapp:v1.2.3

# 查看镜像的附件
oras discover myregistry.io/cloud-sail/myapp:v1.2.3
```

**预期输出**：
```
Discovered 1 artifact for myregistry.io/cloud-sail/myapp:v1.2.3
└── application/vnd.cyclonedx+json
    └── sha256:abc123...  sbom.json
```

**优势**：
- SBOM 和镜像在 Registry 中「绑定」，不会丢失或错配。
- 拉取镜像时可以同时拉取 SBOM，便于运行时审计。
- Registry 的权限控制同时保护镜像和 SBOM。

> **可能遇到的坑**：并非所有 Registry 都支持 OCI Artifact 附件。Harbor 2.8+、Docker Hub（有限支持）、AWS ECR（需开启）支持情况各异。在选型 Registry 时需要确认。

### 步骤五：用 Dependency-Track 管理 SBOM

**目标**：搭建企业级 SBOM 管理和漏洞追踪平台。

**部署 Dependency-Track**：

```bash
# 使用 Docker Compose 快速启动
curl -LO https://dependencytrack.org/docker-compose.yml
docker-compose up -d
```

**上传 SBOM**：

```bash
# 获取 API Key 后，上传 SBOM
curl -X POST "http://localhost:8080/api/v1/bom" \
  -H "X-Api-Key: YOUR_API_KEY" \
  -H "Content-Type: multipart/form-data" \
  -F "projectName=payment-gateway" \
  -F "projectVersion=1.2.3" \
  -F "bom=@sbom.cyclonedx.json"
```

**查看效果**：
- 登录 Dependency-Track 控制台，查看项目的组件清单。
- 系统会自动关联漏洞数据库，标记出有已知 CVE 的组件。
- 可以设置告警规则：当新漏洞影响已上传的 SBOM 时，自动发送邮件/Slack 通知。

### 步骤六：建立「生成 → 签名 → 存储 → 审计」的完整链路

**目标**：在 CI/CD 中自动化 SBOM 的全生命周期管理。

**GitLab CI 示例**：

```yaml
stages:
  - build
  - sbom
  - sign

build_image:
  stage: build
  script:
    - docker build -t $CI_REGISTRY_IMAGE:$CI_COMMIT_SHA .
    - docker push $CI_REGISTRY_IMAGE:$CI_COMMIT_SHA

sbom_generation:
  stage: sbom
  image: aquasec/trivy:0.50.0
  script:
    - trivy image --format cyclonedx --output sbom.json $CI_REGISTRY_IMAGE:$CI_COMMIT_SHA
  artifacts:
    paths:
      - sbom.json
    expire_in: 1 year

sign_sbom:
  stage: sign
  image: sigstore/cosign:latest
  script:
    - cosign sign-blob --yes --key env://COSIGN_PRIVATE_KEY sbom.json --output-signature sbom.json.sig
    - oras attach --artifact-type application/vnd.cyclonedx+json --file sbom.json $CI_REGISTRY_IMAGE:$CI_COMMIT_SHA
```

**链路说明**：
1. **Build**：构建镜像并推送到 Registry。
2. **SBOM**：用 Trivy 生成 CycloneDX SBOM。
3. **Sign**：用 cosign 对 SBOM 签名，防止篡改。
4. **Attach**：用 oras 将签名后的 SBOM 附加到镜像。

### 测试验证

1. 执行 `trivy image --format cyclonedx`，确认 `sbom.cyclonedx.json` 生成且包含 `components` 数组。
2. 执行 `trivy sbom sbom.cyclonedx.json`，确认能从 SBOM 反查出漏洞。
3. 对比 CycloneDX 和 SPDX 的输出结构，确认字段差异。
4. 使用 `oras attach` 将 SBOM 附加到镜像，验证 `oras discover` 能正确列出附件。
5. 上传 SBOM 到 Dependency-Track，验证平台能自动识别组件和关联漏洞。

---

## 4. 项目总结

### 优点 & 缺点

| 维度 | 优点 | 缺点 |
|------|------|------|
| 标准兼容 | 原生支持 CycloneDX 和 SPDX 两种主流标准 | 对早期版本（如 CycloneDX 1.3 以下）兼容性有限 |
| 生成来源 | 支持 image/fs/repo/sbom 多种来源 | 对无包管理器的 C/C++ 项目组件识别率较低 |
| 漏洞反查 | `trivy sbom` 可从 SBOM 独立反查漏洞 | 反查结果依赖当时的漏洞数据库版本 |
| OCI 集成 | 支持将 SBOM 作为 Artifact 附加到镜像 | 并非所有 Registry 都支持 OCI Artifact |
| 完整性 | 可结合 cosign 实现 SBOM 签名 | 签名和验证流程增加了 CI 复杂度 |

### 适用场景

1. **客户合规交付**：向金融、政府、大型企业客户交付软件时，提供标准格式的 SBOM。
2. **漏洞应急响应**：新漏洞爆发时，通过查询 SBOM 库快速定位受影响的产品和版本。
3. **供应链透明度**：建立上游组件的可追溯档案，便于在投毒事件发生时快速溯源。
4. **许可证合规审计**：SBOM 中 embedded 的许可证信息，可作为法务审计的依据。
5. **镜像版本管理**：将 SBOM 与镜像绑定存储，确保任意历史版本都能被准确审计。

**不适用场景**：
1. 纯内部工具且无任何外部交付需求的场景——SBOM 的管理成本可能超过收益。
2. 需要精确到源码文件级别的物料清单——SBOM 的粒度是「组件/包」，不是「文件/函数」。

### 注意事项

- **SBOM 的时效性**：SBOM 是「快照」，只反映生成时刻的状态。镜像重新构建后，必须重新生成 SBOM。
- **PURL 的精确性**：Trivy 生成的 PURL 尽可能包含架构、发行版等限定符，但某些特殊包（如私有包、Git 直接依赖）的 PURL 可能不标准，需要人工校对。
- **SBOM 的存储成本**：一个复杂镜像的 SBOM 可能达到数 MB。大规模部署时，SBOM 的存储和检索成本需要纳入架构设计。

### 常见踩坑经验

**踩坑案例 1：SBOM 和镜像版本不匹配**
- **现象**：审计时发现的漏洞和 SBOM 中的组件不一致。
- **根因**：镜像被重新构建（可能依赖版本有微小更新），但团队忘记重新生成 SBOM。
- **解法**：在 CI 中强制「镜像 push → SBOM 生成 → SBOM 附加」的原子操作，禁止单独更新镜像而不更新 SBOM。

**踩坑案例 2：Registry 不支持 OCI Artifact 附件**
- **现象**：`oras attach` 报错 `unsupported media type`。
- **根因**：旧版 Harbor 或某些私有 Registry 未开启 OCI Artifact 支持。
- **解法**：升级 Registry 到支持 OCI 1.1 的版本；或退而求其次，将 SBOM 存储在对象存储（S3/MinIO）中，通过命名约定与镜像关联。

**踩坑案例 3：Dependency-Track 的漏洞数据库滞后**
- **现象**：SBOM 上传到 Dependency-Track 后，某些已知漏洞未被标记。
- **根因**：Dependency-Track 默认使用内置的漏洞数据库，更新频率不如 Trivy 的 `trivy-db`。
- **解法**：在 Dependency-Track 中配置外部漏洞源（如 NVD API、GitHub Advisory），或定期用 `trivy sbom` 做二次验证。

### 思考题

1. 假设 Log4Shell 事件再次爆发，你的公司拥有 200 个微服务镜像，每个镜像都有对应的 SBOM 存储在 OCI Registry 中。请设计一个自动化脚本，在 5 分钟内输出「所有包含 `log4j-core < 2.15.0` 的镜像清单及其部署环境」。
2. SBOM 的生成和存储增加了供应链的「元数据攻击面」——如果攻击者篡改了你上传的 SBOM，隐瞒了某些恶意组件，下游客户如何发现？请设计一个基于签名的 SBOM 可信验证方案。

> **答案提示**：第 27 章「软件供应链安全端到端实践」将深入介绍 SBOM 的签名、验证、存储和全链路追踪方案。

---

> **推广计划**：本章是架构师和交付团队的必读内容。建议所有对外交付的产品在发版时强制生成 SBOM，并纳入发布检查清单。运维团队维护一个中央 SBOM 仓库（如 Dependency-Track 或 S3），所有产品的 SBOM 集中归档。安全团队在新漏洞爆发时，优先通过 SBOM 库进行影响面分析。开发同学了解 SBOM 的基本概念，在引入新依赖时考虑其对 SBOM「清晰度」的影响（优先选择有明确 PURL 和许可证声明的包）。
