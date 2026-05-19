# Trivy 安全扫描实战修炼专栏大纲

> 版本：Trivy v0.50+
> 面向人群：开发、运维、测试、安全工程师、架构师
> 总章节：40 章（基础篇 16 章 / 中级篇 15 章 / 高级篇 9 章）
> 每章独立成文件，字数 3000-5000 字

---

## 专栏定位

以 Trivy 官方最新版本为骨架，从「一条命令扫镜像」到「十万级镜像企业级安全平台」，覆盖容器镜像、文件系统、IaC、Kubernetes、SBOM 供应链安全全链路。每一章均采用「业务痛点 → 三人剧本对话 → 代码实战 → 总结思考」的四段式结构，**实战为主、理论为辅、由浅入深**，让开发和运维读完就能在产线落地。

---

## 阅读路线建议

| 角色 | 建议阅读顺序 | 重点章节 |
|------|-------------|---------|
| 新人开发/测试 | 基础篇全读 → 中级篇选读 | 第 1-16 章 |
| 核心开发/运维 | 基础篇速读 → 中级篇精读 → 高级篇选读 | 第 17-31、32-40 章 |
| 架构师/资深开发/安全工程师 | 高级篇为主线，按需回溯中级篇 | 第 32-40 章，辅以 17-31 章 |

---

# 基础篇（第 1-16 章）

> **核心目标**：建立 Trivy 核心概念，掌握单机扫描、报告解读与初级安全治理。
> **源码关联**：pkg/commands/、pkg/scanner/、pkg/detector/、pkg/report/ 基础结构。

---

## 第1章：Trivy 术语全景与安全扫描架构原理
**定位**：专栏总览与开篇，建立统一语系。
**核心内容**：
- 术语词典：Scanner、Detector、Target、Artifact、Vulnerability、Misconfiguration、Secret、License、SBOM、VEX、CVSS、EPSS
- Trivy 整体架构图：CLI → Command → Scanner → Detector → DB → Report 的数据流
- 扫描目标矩阵：image / fs / repo / rootfs / vm / k8s / sbom / aws / azure / gcp
- 扫描器类型：vuln / misconfig / secret / license
- 漏洞数据库机制：trivy-db（OS & 语言包）、java-db（Maven Central）的更新与缓存策略
- 源码关联：pkg/commands/app.go、pkg/scanner/scan.go、pkg/detector/library/driver.go
**实战目标**：绘制一张可讲解的 Trivy 数据流架构图，输出到团队 Wiki；完成首次 `trivy image alpine:latest` 并逐行解读报告。

---

## 第2章：多环境安装部署与首个漏洞扫描
**定位**：从 0 到 1 跑通 Trivy，建立信心。
**核心内容**：
- 安装方式全览：brew / apt / yum / Docker / GitHub Release / Go install
- Docker 与宿主机扫描的权限差异：socket 挂载、rootless 模式
- 第一个扫描：`trivy image python:3.4-alpine`，理解 Severity、CVE ID、Fixed Version
- 报告格式对比：table（默认）/ json / sarif / template
- 最小化配置：cache-dir、severity 过滤、exit-code 控制
- 源码关联：pkg/commands/、docs/getting-started/installation.md
**实战目标**：在本地用 Docker 启动 Trivy，扫描一个存在已知漏洞的镜像（如 `python:3.4-alpine`），输出 JSON 报告并用 jq 提取 Critical 级别漏洞列表。

---

## 第3章：容器镜像分层扫描与漏洞原理
**定位**：理解镜像安全扫描的底层逻辑。
**核心内容**：
- 镜像分层（Layer）与 UnionFS：为什么 Trivy 要逐层分析
- OS 包扫描：Alpine（apk）、Debian（dpkg）、RHEL（rpm）的数据源差异
- 语言包扫描：识别 `requirements.txt`、`package-lock.json`、`pom.xml` 等清单文件
- 漏洞匹配原理：PkgName + InstalledVersion vs Advisory 的 FixedVersion
- 假阳性来源：OS Vendor Backport 与上游版本号的差异
- 源码关联：pkg/fanal/artifact/image/、pkg/detector/ospkg/、pkg/detector/library/
**实战目标**：构建一个包含已知漏洞（如旧版 OpenSSL）的多层 Dockerfile，逐层扫描并验证漏洞出现在哪一层；对比 `trivy image` 与 `trivy fs` 对同一项目的扫描结果差异。

---

## 第4章：文件系统与代码仓库扫描实战
**定位**：将安全左移到开发阶段。
**核心内容**：
- `trivy fs` 与 `trivy repo` 的区别与适用场景
- 扫描本地项目：依赖清单、锁定文件、 vendor 目录的处理策略
- Git 仓库远程扫描：无需 clone 即可扫描 GitHub/GitLab 仓库
- 扫描范围控制：`--skip-dirs`、`--skip-files`、`.trivyignore`
- 开发机集成：pre-commit hook、VS Code 插件
- 源码关联：pkg/commands/app.go（fs 与 repo 子命令定义）
**实战目标**：对一个真实的 Java Maven 项目执行 `trivy fs`，发现 Log4j2 类漏洞；配置 `.trivyignore` 排除测试目录，验证扫描时间缩短效果。

---

## 第5章：基础设施即代码（IaC）配置错误扫描入门
**定位**：堵住云原生架构的"配置后门"。
**核心内容**：
- Misconfiguration 扫描原理：将配置文件与 Check 规则库比对
- 支持的 IaC：Dockerfile、Kubernetes YAML、Terraform、CloudFormation、Helm Chart
- 常见高危配置：容器以 root 运行、特权模式、敏感目录挂载、Seccomp/AppArmor 未启用
- Trivy Checks 规则库来源：Aqua 内置规则与 Open Policy Agent（OPA）Rego 的关系
- 严重级别：LOW / MEDIUM / HIGH / CRITICAL 的判定依据
- 源码关联：pkg/misconf/、pkg/iac/
**实战目标**：编写一个包含 5 个高危配置错误的 Kubernetes Deployment YAML，用 `trivy config` 扫描并全部命中；逐条修复后重新扫描验证清零。

---

## 第6章：密钥检测（Secret Scanning）实战
**定位**：防止敏感信息泄露到版本控制与镜像中。
**核心内容**：
- Secret 扫描原理：基于正则与熵值的敏感信息识别
- 内置规则覆盖：AWS Key、GitHub Token、Slack Webhook、Private Key、Database URL
- 自定义规则：通过 `trivy-secret.yaml` 扩展检测模式
- 镜像中的 Secret：构建层残留 `.env`、历史命令中的密码
- 与 GitLeaks、TruffleHog 的对比
- 源码关联：pkg/fanal/secret/、pkg/detector/secret/
**实战目标**：在一个 Git 仓库中故意植入 3 类密钥（AWS AK/SK、GitHub PAT、RSA 私钥），用 `trivy fs --scanners secret` 检测；编写自定义规则检测公司内部的 API Token 格式。

---

## 第7章：漏洞数据库管理与离线扫描
**定位**：解决企业内网、等保环境的扫描刚需。
**核心内容**：
- trivy-db 结构：OS 漏洞、语言包漏洞、漏洞详情、CPE 映射
- java-db：Maven 制品与 CVE 的关联数据
- 数据库更新机制：`--download-db-only`、`--skip-db-update`
-  air-gapped 环境部署：DB 镜像导入、本地 OCI 仓库托管
- 数据库版本回滚与校验
- 源码关联：pkg/db/、pkg/javadb/、pkg/oci/（DB 的 OCI 分发）
**实战目标**：在内网隔离环境中，通过外网导出 `trivy-db` 和 `java-db`，导入内网服务器，完成一次无外网访问的镜像漏洞扫描。

---

## 第8章：报告输出、过滤与基线管理
**定位**：让安全报告从"不可读"到"可行动"。
**核心内容**：
- 输出格式深度解析：table / json / sarif / template / cyclonedx / spdx
- Severity 与 Exploit 双重过滤：`--severity`、`--ignore-unfixed`、`--vuln-type`
- 自定义报告模板：Go template 语法与常用字段
- 基线管理：`--ignorefile`、`.trivyignore` 语法、CVE 白名单策略
- 与 Excel/邮件/工单系统的对接思路
- 源码关联：pkg/report/、pkg/result/
**实战目标**：为团队设计一套「仅展示有 EXP 的 HIGH+ 漏洞」的自定义 Go template 报告；编写 `.trivyignore` 管理 20 个已评估接受的漏洞，验证基线生效。

---

## 第9章：漏洞评估与优先级排序实战
**定位**：安全团队人力有限，必须"打蛇打七寸"。
**核心内容**：
- CVSS v2 / v3 评分体系与 Trivy 的展示逻辑
- EPSS（Exploit Prediction Scoring System）：预测漏洞被利用的概率
- CISA KEV（Known Exploited Vulnerabilities）目录集成
- 漏洞优先级矩阵：Severity × Exploit 可用性 × 资产暴露面
- Trivy 的 `VulnerabilityID`、`SeveritySource`、`VendorSeverity` 字段解读
- 源码关联：pkg/vulnerability/、pkg/types/vulnerability.go
**实战目标**：对一个扫描出 200+ 漏洞的镜像，使用 EPSS 和 CISA KEV 进行优先级排序，输出 Top 10 必须立即修复的漏洞清单，并编写处理 SOP。

---

## 第10章：开源许可证扫描与合规基础
**定位**：法律风险与安全风险同等重要。
**核心内容**：
- 许可证扫描原理：识别依赖包的开源许可证类型
- 高风险许可证：GPL-2.0/3.0、AGPL、SSPL 的传染性分析
- 允许的许可证白名单与禁止名单配置
- 许可证冲突检测：双重许可证、许可证兼容性矩阵
- SBOM 中的许可证信息导出
- 源码关联：pkg/licensing/、pkg/fanal/analyzer/licensing/
**实战目标**：扫描一个企业级 Node.js 项目，生成许可证合规报告；配置仅允许 MIT/Apache-2.0/BSD 策略，验证违规依赖被正确标记。

---

## 第11章：SBOM 生成、消费与供应链安全初探
**定位**：从"扫漏洞"到"管资产"的升级。
**核心内容**：
- SBOM 概念：CycloneDX vs SPDX 标准对比
- `trivy image --format cyclonedx` 与 `trivy image --format spdx-json`
- SBOM 的字段解析：components、dependencies、hashes、licenses、purl
- 从 SBOM 反查漏洞：`trivy sbom` 子命令
- 供应链安全基础：知道用了什么，才能判断风险
- 源码关联：pkg/sbom/、pkg/sbom/cyclonedx/、pkg/sbom/spdx/
**实战目标**：为一个微服务镜像生成 CycloneDX SBOM，上传至 Dependency-Track 或本地解析；再用 `trivy sbom` 从该 SBOM 反推漏洞，对比直接扫描镜像的差异。

---

## 第12章：漏洞忽略策略与误报治理
**定位**：没有完美的扫描器，只有成熟的治理流程。
**核心内容**：
- 误报来源：OS Backport、多版本共存、开发依赖、测试框架
- `.trivyignore` 语法详解：`id`、`pkg-name`、`vuln-type`、`statement`、`expiry`
- 忽略文件的层级管理：项目级、团队级、组织级
- 与 VEX（Vulnerability Exploitability eXchange）的初步结合
- 误报治理流程：发现 → 评估 → 记录 → 定期审计
- 源码关联：pkg/result/filter.go、pkg/types/ignore.go
**实战目标**：建立一个包含 30 条规则的 `.trivyignore`，覆盖常见误报场景；编写 Python 脚本审计忽略文件中的过期条目，防止"永久忽略"导致风险。

---

## 第13章：Trivy 配置体系与缓存优化
**定位**：从"会用"到"用好"的调优起点。
**核心内容**：
- 配置文件：`trivy.yaml` 的完整字段与优先级（CLI Flag > 环境变量 > 配置文件）
- 缓存目录管理：`cache-dir`、BLOB 缓存、磁盘占用分析
- 扫描速度优化：并行度、`--offline-scan`、排除大目录
- 内存与 CPU 占用调优：Go runtime 参数、容器资源限制
- 清理命令：`trivy clean --scan-cache`
- 源码关联：pkg/commands/clean/、pkg/cache/
**实战目标**：对一个 5GB 的大型镜像，对比默认配置与优化配置（缓存预热、并行度调整、跳过不必要的 Scanner）的扫描耗时差异，输出调优前后对比表。

---

## 第14章：私有镜像仓库与 Harbor 集成扫描
**定位**：企业镜像资产的常态化安全检测。
**核心内容**：
- Docker Registry 认证：`docker login` 与 `--username/--password` 的差异
- 私有仓库扫描：Harbor、Nexus、AWS ECR、Azure ACR、Google GCR
- Harbor 内置 Trivy Adapter 的原理与配置
- 镜像签名验证：`cosign` 与 Trivy 的联动
- 扫描时机选择：构建时 vs 推送时 vs 运行时
- 源码关联：pkg/fanal/remote/、pkg/remote/、pkg/fanal/image/
**实战目标**：在本地 Harbor 实例中启用 Trivy 扫描器，推送一个含漏洞的镜像，触发自动扫描并配置扫描策略为"阻止 Critical 漏洞镜像"。

---

## 第15章：日常运维、故障排查与 DEBUG 日志
**定位**：从"能扫"到"稳扫"的运维保障。
**核心内容**：
- 常用诊断命令：`trivy version`、`trivy clean`、`trivy plugin`
- DEBUG 模式：`--debug` 与 `TRIVY_DEBUG` 环境变量
- 常见故障排查：DB 下载失败、内存 OOM、网络超时、权限不足
- 扫描结果不一致排查：缓存过期、DB 版本差异、base image 变化
- 日志分析与性能基线建立
- 源码关联：pkg/log/、pkg/commands/
**实战目标**：模拟 5 种生产常见故障（DB 更新超时/镜像拉取 401/扫描 OOM/报告格式错误/缓存损坏），给出排查 SOP 与恢复命令。

---

## 第16章：【基础篇综合实战】搭建单机版安全扫描工作台
**定位**：融会贯通基础篇知识。
**核心内容**：
- 场景：为一家 20 人研发团队搭建统一的开发机安全扫描环境
- 需求拆解：镜像扫描、代码提交前检查、许可证合规、报告统一输出
- 分步实现：Trivy 安装 → 配置文件标准化 → pre-commit hook → 私有仓库认证 → 基线管理
- 验收标准：扫描 10 个镜像，Critical 漏洞发现率 100%，误报率 < 10%，单次扫描 < 3 分钟

---

# 中级篇（第 17-31 章）

> **核心目标**：掌握 CI/CD 集成、K8s 场景、供应链安全、可观测性与企业级治理。
> **源码关联**：pkg/commands/、pkg/k8s/、pkg/sbom/、pkg/compliance/。

---

## 第17章：GitHub Actions 集成与 Pull Request 安全门禁
**定位**：安全左移到代码合并前的最后一道闸。
**核心内容**：
- `aquasecurity/trivy-action` 的完整参数矩阵
- SARIF 格式报告上传至 GitHub Security Advisories
- PR 评论集成：通过 bot 在 Pull Request 中展示新增漏洞
- 门禁策略：基于 severity 的 `exit-code` 控制与分支保护规则
- 与 Dependabot、CodeQL 的协作边界
**实战目标**：为一个开源项目配置 GitHub Actions 流水线，实现「PR 提交时自动扫描，新增 Critical 漏洞则阻止合并，并在 PR 评论中列出漏洞详情」。

---

## 第18章：GitLab CI 与 Jenkins 流水线集成
**定位**：覆盖企业私有化 CI/CD 平台。
**核心内容**：
- GitLab CI：`.gitlab-ci.yml` 中 Trivy 的 stages 设计（build → scan → deploy）
- Jenkins：Pipeline as Code（Jenkinsfile）与 Trivy 容器化执行
- 产物管理：报告归档、HTML 报告在 Jenkins Blue Ocean 中展示
- 流水线门禁：质量阈值、增量漏洞检测（只关注本次变更引入的漏洞）
- 多环境策略：开发/测试/生产环境的扫描严格度差异化
**实战目标**：编写一套完整的 GitLab CI 配置，实现「构建镜像 → Trivy 扫描 → 生成 HTML 报告 → 上传产物 → Critical 漏洞阻断发布」的完整链路。

---

## 第19章：Harbor 镜像仓库安全治理与 Webhook 联动
**定位**：镜像从"推得上去"到"扫得干净"。
**核心内容**：
- Harbor 扫描器架构：Core → Job Service → Trivy Adapter → Trivy
- 扫描策略配置：定时全量扫描 vs 推送触发扫描
- 漏洞白名单与阻止策略：Project 级别的 CVE 忽略
- Webhook 通知：扫描完成后的企业微信/钉钉/Slack 推送
- 镜像签名与扫描的联动：Notary / cosign 与 Trivy 的协同
**实战目标**：在 Harbor 中配置「推送镜像自动扫描 + Critical 漏洞阻止拉取 + 扫描结果推送到钉钉群」的完整治理链路。

---

## 第20章：Kubernetes 集群安全扫描实战
**定位**：从镜像安全延伸到运行态安全。
**核心内容**：
- `trivy k8s` 的命令结构与资源发现机制
- 集群扫描范围：cluster / namespace / resource（deployment / pod / configmap 等）
- 权限要求：ServiceAccount、ClusterRole、kubeconfig 配置
- 报告解读：Summary vs All 模式、资源层级与漏洞映射
- 与 kube-bench、kube-hunter 的能力对比与互补
- 源码关联：pkg/k8s/、pkg/k8s/commands/
**实战目标**：对一个 Minikube 或 Kind 集群执行 `trivy k8s cluster --report summary`，发现运行态 Pod 中的高危漏洞和配置错误；生成 JSON 报告并按 Namespace 聚合风险评分。

---

## 第21章：Trivy Operator 与 K8s 持续合规
**定位**：将扫描从"一次性动作"变为"持续监控"。
**核心内容**：
- Trivy Operator 架构：Controller + CRD（VulnerabilityReport、ConfigAuditReport、ExposedSecretReport、RbacAssessmentReport、InfraAssessmentReport）
- 安装与配置：Helm Chart 参数、自定义资源调度
- 报告聚合：Metrics → Prometheus → Grafana 的可视化链路
- 自动修复探索：Kyverno / OPA Gatekeeper 与 Trivy Operator 的联动
- 多集群管理：Trivy Operator 在联邦集群中的部署策略
**实战目标**：在 K8s 集群中部署 Trivy Operator，配置自动扫描所有 Namespace 的 Deployment；在 Grafana 中绘制「集群漏洞热力图」Dashboard。

---

## 第22章：IaC 高级合规扫描与自定义 Rego 策略
**定位**：企业安全策略的代码化表达。
**核心内容**：
- OPA / Rego 语言基础：规则、查询、集合操作
- Trivy 的自定义 Check 机制：通过 `--config-data` 加载 Rego 策略
- Terraform Plan 扫描：`trivy terraformplan` 的工作原理
- CloudFormation 与 ARM 模板扫描
- 策略即代码（PaC）在 GitOps 工作流中的位置
- 源码关联：pkg/misconf/scanners/、pkg/iac/
**实战目标**：编写 3 条自定义 Rego 策略（强制标签、禁止公网 IP、强制加密存储），用 `trivy config` 扫描 Terraform 代码，验证策略命中与未命中的边界条件。

---

## 第23章：高级过滤、VEX 应用与报告定制
**定位**：精细化漏洞管理，减少噪音。
**核心内容**：
- 高级过滤表达式：Rego 策略过滤 `--filter-rego`、基于包名的排除
- VEX（Vulnerability Exploitability eXchange）标准：CycloneDX VEX、OpenVEX
- VEX 的应用场景：标记「不受影响」、「已修复」、「可忽略」
- 报告模板进阶：按团队/项目/应用分组的自定义 HTML/Markdown 报告
- 与 JIRA / Azure DevOps 的漏洞工单对接
- 源码关联：pkg/vex/、pkg/result/filter.go
**实战目标**：为一个微服务应用生成 OpenVEX 文件，标记 5 个已评估为「NotAffected」的漏洞；使用 VEX 文件重新运行 `trivy image --vex` 扫描，验证漏洞从报告中消除。

---

## 第24章：性能调优与大规模镜像扫描策略
**定位**：扫描从"小时级"进化到"分钟级"。
**核心内容**：
- 并行扫描原理：`--parallel` 与 Go goroutine 调度
- 大镜像优化：分层缓存、BLOB 复用、skip-dirs 策略
- 增量扫描：基于 SBOM 差异的变更感知扫描
- 资源限制：容器内存限制、swap 策略、tmpfs 优化
- 扫描队列设计：消息队列（Redis/RabbitMQ）驱动的大规模扫描
- 源码关联：pkg/parallel/、pkg/fanal/
**实战目标**：对 50 个微服务镜像进行批量扫描，对比串行、并行（默认）、优化配置（缓存预热 + 分层复用）三种模式的耗时与资源占用，输出性能对比报告。

---

## 第25章：缓存策略、数据库加速与分布式存储
**定位**：构建企业级扫描基础设施。
**核心内容**：
- Trivy 缓存分层：FS 缓存、Redis 缓存、OCI Registry 缓存
- 共享缓存架构：多台扫描节点共用 Redis 缓存，避免重复下载
- trivy-db / java-db 的本地镜像与定时同步策略
- 扫描结果缓存：相同镜像 digest 的二次扫描加速
- 对象存储集成：将报告与 SBOM 持久化至 S3 / MinIO / OSS
**实战目标**：搭建一套「3 台 Trivy 扫描节点 + Redis 共享缓存 + MinIO 报告存储」的分布式扫描环境，验证相同镜像二次扫描时间缩短 80% 以上。

---

## 第26章：多云环境资产扫描（AWS / Azure / GCP）
**定位**：云原生安全的广度延伸。
**核心内容**：
- `trivy aws` / `trivy azure` / `trivy gcp` 的扫描范围与认证方式
- 云实例元数据扫描、容器仓库扫描、存储桶扫描
- IAM 权限最小化原则：云厂商 API 调用所需的最小权限集
- 云安全态势管理（CSPM）与 Trivy 的能力边界
- 与云厂商原生安全服务（AWS Inspector、Azure Defender）的对比
**实战目标**：配置 AWS IAM 角色，使用 `trivy aws` 扫描一个 AWS 账号的 ECR 仓库与 EC2 实例，输出按 Region 聚合的云资产风险报告。

---

## 第27章：软件供应链安全端到端实践
**定位**：从"扫漏洞"到"管供应链"。
**核心内容**：
- 供应链攻击面：依赖投毒、构建劫持、镜像篡改、签名伪造
- SBOM 全生命周期：生成 → 签名 → 存储 → 分发 → 审计
- SLSA 框架与 Trivy 的契合点：Provence、SBOM、VEX
- 镜像签名与验证：`cosign sign` + `trivy image` 的联合验证
- In-toto Attestation：构建 provenance 的生成与验证
- 源码关联：pkg/attestation/、pkg/sbom/
**实战目标**：为一个 CI/CD 流水线设计完整的供应链安全链路：构建镜像 → 生成 SBOM → cosign 签名 → 推送至 Registry → Trivy 扫描并验证签名 → 发布 VEX。

---

## 第28章：监控告警与通知体系集成
**定位**：从"人工巡检"到"自动响应"。
**核心内容**：
- Trivy 指标暴露：扫描结果 Metrics（通过 Trivy Operator 或自定义 Exporter）
- Prometheus + Alertmanager 告警规则设计
- 通知渠道：Slack / Microsoft Teams / 钉钉 / 企业微信 / PagerDuty
- 告警降噪：聚合规则、抑制规则、静默窗口
- 与 SOAR 平台的对接思路
**实战目标**：基于 Trivy Operator 的 Prometheus Metrics，配置 3 条核心告警规则（新增 Critical 漏洞 / 高危配置错误 / Secret 泄露），并推送到 Slack 频道。

---

## 第29章：漏洞管理生命周期与外部系统集成
**定位**：安全扫描必须闭环到修复。
**核心内容**：
- 漏洞管理闭环：发现 → 评估 → 指派 → 修复 → 验证 → 关闭
- DefectDojo 集成：导入 Trivy 报告、漏洞去重、工单流转
- JIRA 自动化：扫描触发创建工单，修复后自动验证关闭
- 漏洞 SLA 管理：Critical 7 天 / High 30 天 / Medium 90 天
- 修复建议生成：Trivy 的 FixedVersion 字段与自动 PR（Renovate / Dependabot）
**实战目标**：搭建 Trivy → DefectDojo → JIRA 的集成链路，实现「扫描发现漏洞 → 自动创建安全工单 → 修复后扫描验证 → 工单自动关闭」的全自动化。

---

## 第30章：企业级策略即代码（Policy as Code）体系
**定位**：统一开发、运维、安全三方的安全标准。
**核心内容**：
- 策略分层模型：组织级 → 项目级 → 环境级
- 策略引擎选型：OPA / Conftest / Trivy 内置 Checks 的对比与组合
- 强制策略与建议策略：阻断构建 vs 仅告警
- 策略版本管理与 GitOps 发布
- 策略合规 Dashboard：合规率趋势、违规项 Top 10
**实战目标**：设计一套覆盖「镜像（无 Critical 漏洞）+ IaC（必须非 root 运行）+ Secret（零容忍）」的企业级策略集，在 CI/CD 中落地并输出月度合规率报告。

---

## 第31章：【中级篇综合实战】构建 DevSecOps 自动化安全网关
**定位**：融会贯通中级篇知识。
**核心内容**：
- 场景：为一个 50+ 微服务的金融科技中台设计完整的安全网关
- 功能需求：镜像准入、代码门禁、K8s 持续监控、漏洞闭环、供应链追踪
- 架构设计：Harbor + Trivy + Trivy Operator + Prometheus + DefectDojo + JIRA
- 分步实现：镜像推送触发扫描 → CI 门禁 → K8s 运行时监控 → 告警通知 → 工单闭环
- 验收标准：镜像漏洞发现率 100%、Critical 漏洞平均修复时间 < 3 天、合规率 > 95%

---

# 高级篇（第 32-40 章）

> **核心目标**：源码级理解 Trivy 实现原理，掌握自定义扩展、极端场景优化与企业级平台构建。
> **源码关联**：pkg/ 全链路源码、cmd/trivy/ 入口。

---

## 第32章：Trivy 源码架构全景与核心模块剖析
**定位**：从用户到贡献者的第一步。
**核心内容**：
- 源码目录结构：cmd/、pkg/commands/、pkg/scanner/、pkg/detector/、pkg/fanal/、pkg/report/
- 核心接口设计：Scanner、Detector、Analyzer、Artifact 的抽象关系
- 命令解析：cobra 框架与 CLI 命令树的构建
- 配置加载：Viper 的多源配置合并机制
- 插件系统：pkg/plugin/ 的加载与执行原理
- 源码关联：cmd/trivy/main.go、pkg/commands/app.go、pkg/scanner/scan.go
**实战目标**：绘制 Trivy 源码模块依赖图；在 `pkg/scanner/scan.go` 的 `ScanArtifact` 函数中插入日志，追踪一次完整扫描的调用链。

---

## 第33章：Artifact 分析与 Fanal 引擎源码
**定位**：理解 Trivy 如何"看透"镜像与文件系统。
**核心内容**：
- Fanal 引擎架构：Artifact → Analyzer → Type → Result 的数据流
- 镜像解析：OCI Layout → Layer Tar → File System 的还原过程
- Analyzer 注册机制：`analyzer.Register` 与类型路由表
- 文件系统虚拟化：mapfs 与内存中的目录树构建
- 多类型 Artifact 的统一抽象：image / fs / repo / vm
- 源码关联：pkg/fanal/artifact/、pkg/fanal/analyzer/、pkg/fanal/applier/
**实战目标**：编写一个自定义 Analyzer，识别 `.nvmrc` 文件中的 Node.js 版本；将其注册到 Fanal 引擎，验证扫描结果中正确出现 Node.js 运行时版本信息。

---

## 第34章：漏洞检测引擎与数据库匹配源码
**定位**：揭开漏洞匹配的算法黑箱。
**核心内容**：
- Detector 接口设计：`Detect` 方法与 `Vulnerability` 结构体
- OS 包漏洞匹配：PkgName + Version vs Advisory 的精确匹配与范围匹配
- 语言包漏洞匹配：PURL（Package URL）与 CVE 的关联
- 版本比较算法：语义化版本、epoch 版本、Debian 版本号的比较逻辑
- trivy-db 的 BoltDB 结构与查询优化
- 源码关联：pkg/detector/、pkg/detector/ospkg/、pkg/detector/library/、pkg/version/
**实战目标**：在 `pkg/detector/library/` 中为一个虚构的包管理器（如公司内部的私有包）实现漏洞检测逻辑，从自定义数据源匹配漏洞。

---

## 第35章：报告系统、输出格式化与模板引擎源码
**定位**：掌控报告生成的每一个细节。
**核心内容**：
- Report 结构体：`pkg/report/core.go` 中的 Result、Vulnerability、Misconfiguration
- 序列化管道：Result → Marshaler → Writer 的抽象层
- JSON / Table / SARIF / CycloneDX / SPDX 的各自实现路径
- Go Template 引擎：`pkg/report/template.go` 的字段映射与函数注入
- 自定义 Marshaler 的注册方式
- 源码关联：pkg/report/、pkg/report/table/、pkg/report/json/、pkg/report/sarif/
**实战目标**：编写一个新的 Marshaler，输出符合企业内部安全平台 API 格式的 JSON 报告；将其注册到 Trivy 的报告系统中，通过 `--format corp` 调用。

---

## 第36章：自定义 Scanner 插件开发实战
**定位**：扩展 Trivy 的扫描能力边界。
**核心内容**：
- 插件机制：`trivy plugin install` 与插件仓库规范
- 插件与 Trivy 的交互：stdin / stdout 的 JSON 协议
- 自定义 Scanner 的设计模式：独立二进制 vs 共享库
- 典型场景：公司内部框架的漏洞扫描、私有 Registry 的元数据扫描
- 插件版本管理与兼容性
- 源码关联：pkg/plugin/
**实战目标**：开发一个 Trivy 插件 `trivy-plugin-custom`，扫描公司内部构建的「基础镜像基线合规性」（检查是否包含公司要求的监控 Agent、安全加固项），集成到 CI/CD 中使用。

---

## 第37章：自定义 Rego 策略与 Secret 规则开发
**定位**：让安全策略贴合企业业务。
**核心内容**：
- Misconfiguration Check 的 Rego 框架：输入 schema、deny 规则、msg 构造
- 规则测试框架：OPA Test Runner 与 Trivy 的集成测试
- Secret 规则引擎：正则模式、熵值计算、allow / deny 列表
- 规则的版本管理与灰度发布
- 性能优化：Rego 编译缓存、规则并行执行
- 源码关联：pkg/misconf/scanners/、pkg/fanal/secret/、pkg/detector/secret/
**实战目标**：开发一套包含 10 条 Rego 规则的「金融级 K8s 安全基线」，覆盖：PodSecurityContext、NetworkPolicy、ResourceQuota、PodDisruptionBudget；编写对应的 OPA Test 用例。

---

## 第38章：极端场景优化：十万级镜像分布式扫描平台
**定位**：突破单机性能天花板。
**核心内容**：
- 瓶颈分析：IO 密集（镜像拉取）vs CPU 密集（解压分析）vs 网络密集（DB 查询）
- 分布式扫描架构：任务队列 → 扫描 Worker → 结果聚合器
- 镜像去重：基于 digest 的全局缓存，避免重复扫描相同 layer
- 数据库分片：按 OS / 语言分片的 trivy-db 裁剪策略
- 性能剖析：pprof、trace、火焰图定位热点
- 源码关联：pkg/parallel/、pkg/fanal/
**实战目标**：设计并实现一个基于 Redis 队列 + Go Worker 的分布式扫描原型，支持 100 个镜像的并发扫描；对比单机串行、单机并行、分布式三种模式的吞吐量。

---

## 第39章：Trivy 作为 Go Library 二次开发与 API 封装
**定位**：将 Trivy 内嵌到企业自有平台。
**核心内容**：
- Trivy 的 Library 化使用：直接调用 `pkg/scanner/`、`pkg/detector/` 的 API
- 绕过 CLI 层：自定义配置、自定义缓存、自定义报告处理
- RESTful API 封装：将 Trivy 能力包装为 HTTP 服务
- gRPC 服务化：跨语言调用的扫描服务
- 并发控制与资源隔离：请求级别的超时、取消、内存限制
- 源码关联：pkg/commands/、pkg/scanner/scan.go
**实战目标**：编写一个 Go HTTP 服务，暴露 `/scan/image` 和 `/scan/fs` 两个 API，接收请求后调用 Trivy Library 执行扫描，返回 JSON 报告；实现请求队列与并发控制。

---

## 第40章：【高级篇综合实战】从零构建企业级安全扫描平台
**定位**：融会贯通高级篇知识，产出可交付的生产级平台。
**核心内容**：
- 场景：为一家拥有 500+ 微服务、多地域 K8s 集群的互联网公司，自研统一安全平台替代散落的扫描脚本
- 架构设计：
  - 扫描引擎层：Trivy Library + 自定义 Analyzer + 自定义 Rego 规则
  - 任务调度层：Redis + Go Worker 的分布式队列
  - 数据存储层：PostgreSQL（漏洞元数据）+ S3（报告/SBOM）
  - 策略引擎层：OPA + 企业策略集
  - 集成层：Harbor / GitLab / K8s / JIRA / 钉钉 Webhook
- 功能实现：
  - 资产发现：自动同步 Harbor、ECR、ACR 中的镜像清单
  - 智能调度：基于镜像变更和优先级自动触发扫描
  - 增量报告：只展示相对于上次扫描的新增/修复漏洞
  - 漏洞闭环：自动创建工单、跟踪修复进度、验证修复结果
- 性能指标：日均扫描 1 万+ 镜像、P99 扫描延迟 < 2 分钟、平台可用性 99.9%
- 部署方案：K8s StatefulSet + HorizontalPodAutoscaler + 多活灾备

---

# 附录与资源

## 附录 A：Trivy 源码阅读路线图
1. 入口：`cmd/trivy/main.go` 的 main 函数
2. 命令解析：`pkg/commands/app.go` 的 cobra 命令树构建
3. 扫描入口：`pkg/scanner/scan.go` 的 `ScanArtifact`
4. Artifact 分析：`pkg/fanal/artifact/` 的镜像/文件系统解析
5. 漏洞检测：`pkg/detector/` 的 OS 包与语言包检测
6. 报告输出：`pkg/report/` 的序列化与格式化
7. 数据持久化：`pkg/db/` 的 trivy-db 查询

## 附录 B：编译调试指南
- 带 debug 的编译：`go build -gcflags="all=-N -l" -o trivy ./cmd/trivy`
- 常用调试断点：`pkg/scanner/scan.go:ScanArtifact`、`pkg/detector/ospkg/...:Detect`
- 日志级别：`--debug`、`--quiet`、环境变量 `TRIVY_DEBUG`
- pprof 启用：在代码中插入 `import _ "net/http/pprof"` 并暴露端口

## 附录 C：推荐工具链
- 镜像构建：Docker、BuildKit、Kaniko、ko
- 镜像仓库：Harbor、Nexus、ECR、ACR、GCR
- CI/CD：GitHub Actions、GitLab CI、Jenkins、Tekton
- K8s 生态：Trivy Operator、Kyverno、OPA Gatekeeper、Falco
- 监控告警：Prometheus、Grafana、Alertmanager
- 漏洞管理：DefectDojo、JIRA、ServiceNow
- SBOM/供应链：Syft、cosign、in-toto、Dependency-Track
- 压测/性能：wrk、 vegeta、Go pprof、bpftrace

## 附录 D：思考题参考答案索引
- 基础篇思考题答案：见各章末尾或本附录对应小节
- 中级篇思考题答案：见各章末尾或本附录对应小节
- 高级篇思考题答案：见各章末尾或本附录对应小节

---

> **版权声明**：本专栏基于 Trivy 官方开源项目（Apache-2.0 License）编写，所有源码引用均遵循原许可证条款。
