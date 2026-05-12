# Harbor 源码剖析与实战修炼专栏大纲

> **版本**：Harbor v2.12+（最新稳定版）
> **面向人群**：开发、运维、测试、架构师
> **总章节**：40 章（基础篇 16 章 / 中级篇 15 章 / 高级篇 9 章）
> **每章独立成文件，字数 3000-5000 字**

---

## 专栏定位

以 Harbor v2.12+ 官方源码为骨架，从安装部署到架构设计，从镜像管理到源码实现，从安全合规到生产落地，全链路贯通。每一章均采用「业务痛点 → 三人剧本对话 → 代码实战 → 总结思考」的四段式结构，兼顾趣味性、实战性与深度。实战为主，理论为辅，由浅入深。

---

## 阅读路线建议

| 角色 | 建议阅读顺序 | 重点章节 |
|------|-------------|---------|
| 新人开发/测试 | 基础篇全读 → 中级篇选读 | 第 1-16 章 |
| 核心开发/运维 | 基础篇速读 → 中级篇精读 → 高级篇选读 | 第 17-31、32-40 章 |
| 架构师/资深开发 | 高级篇为主线，按需回溯中级篇 | 第 32-40 章，辅以 17-31 章 |

---

# 基础篇（第 1-16 章）

> **核心目标**：建立 Harbor 核心概念，掌握单机部署、镜像/Chart 管理、漏洞扫描与日常运维。

| 章节 | 主题 | 核心内容 |
|------|------|---------|
| 1 | Harbor 术语全景与云原生镜像仓库架构原理 | 术语词典；六层架构图；Registry/Project/Repository/Artifact/Replication/Scanner/GC；请求全链路 |
| 2 | Harbor 环境准备与安装部署 | Docker Compose 安装、Helm 安装、离线安装、TLS 证书配置、harbor.yml 配置解析 |
| 3 | Harbor Web 控制台总览 | Portal 功能巡览、Dashboard 指标解读、项目/用户/日志/配置入口 |
| 4 | 项目与镜像仓库管理 | 公开/私有项目、镜像仓库、仓库策略（内容信任、漏洞阻止） |
| 5 | Docker/Containerd 镜像推送与拉取实战 | docker login/logout、tag、push、pull；多种容器运行时客户端适配 |
| 6 | Artifact 管理与标签策略 | 多架构镜像（Manifest List）、标签不可变性、标签保留策略、自动清理 |
| 7 | Helm Charts 管理入门 | Chart 上传/下载、helm repo 集成、Chart 版本控制 |
| 8 | 复制规则与镜像同步 | Push/Pull 双向同步、过滤器、异步复制、多 Registry 联动 |
| 9 | 漏洞扫描基础——Trivy 集成 | 镜像漏洞扫描原理、扫描策略、CVE 白名单/黑名单、扫描报告解读 |
| 10 | 用户管理与认证体系 | 本地用户、LDAP/AD 集成、OIDC/OAuth2 集成、用户组 |
| 11 | 仓库角色与 RBAC 权限模型 | 项目角色（访客/开发者/维护者/管理员）、系统角色、机器人账户 |
| 12 | 垃圾回收（GC）机制实战 | GC 组件原理、手动触发 vs 定时触发、空间释放与风险 |
| 13 | Harbor RESTful API 入门 | API 认证（Basic/Token）、Swagger 文档、常用 API 操作脚本 |
| 14 | 日志系统与审计追踪 | 操作日志、审计日志、syslog 转发、日志查询与分析 |
| 15 | Harbor 日常运维与故障排查 | 常见错误码（401/500/503）、证书过期、磁盘满、PG 连接失败、Restart/Upgrade/Backup |
| 16 | 【基础篇综合实战】搭建企业级私仓并接入 CI/CD | 完整链路：部署→项目规划→镜像推送→漏洞扫描→复制同步→GitLab CI 集成 |

---

# 中级篇（第 17-31 章）

> **核心目标**：掌握分布式场景下的架构设计、镜像分发优化、可观测性与容器化实践。

| 章节 | 主题 | 核心内容 |
|------|------|---------|
| 17 | Harbor 微服务架构深度剖析 | Core/Portal/JobService/Registry/Registryctl/Redis/Trivy/Database 服务间调用关系与数据流 |
| 18 | Harbor 认证与鉴权源码框架 | Token Service、JWT 签发/校验、Session 管理、LDAP/OIDC 扩展点 |
| 19 | PostgreSQL 数据模型与 Redis 缓存层 | Schema 全览、Redis Key 设计、数据一致性保障 |
| 20 | 镜像存储后端详解（S3/Swift/OSS/Azure/GCS/Local） | 各类存储驱动架构、性能对比、成本分析、选型决策树 |
| 21 | 复制引擎深度实战 | Replication Adapter 适配器、Filter/Mapper/Scheduler、跨区域复制拓扑设计 |
| 22 | Harbor 高可用集群部署 | 双活/主备模式、PostgreSQL HA、Redis Sentinel/Cluster、LB 层设计 |
| 23 | 漏洞扫描系统架构与自定义策略 | Scanner Adapter 插拔框架（Trivy/Clair/Aqua）、自定义 CVE 策略 |
| 24 | 内容信任——Notary/Cosign 签名验证 | Notary 架构、Cosign 无密钥签名、签名策略强制执行、供应链安全 |
| 25 | OCI 兼容与多架构支持 | OCI Distribution Spec 实现、OCI Artifacts、Index/Manifest List |
| 26 | Harbor 性能调优——十万镜像仓库 | Registry 层优化、Database 查询优化、JobService 并发、核心组件资源规划 |
| 27 | 监控体系与 Prometheus 集成 | Metrics 暴露、核心指标解读、Grafana 大盘、告警规则 |
| 28 | Harbor 于 Kubernetes 生产落地（Helm） | Helm Chart 深度定制、Ingress 路由、PV 持久化、HPA 自动伸缩 |
| 29 | Proxy Cache——Harbor 作为 OCI 代理缓存 | Pull-through 模式、缓存策略、命中率优化、DockerHub 加速实战 |
| 30 | P2P 镜像分发——Dragonfly/Nydus 集成 | P2P 分发原理、Dragonfly 集成 Harbor、Nydus 懒加载加速 |
| 31 | 【中级篇综合实战】多活异地混合云镜像仓库 | 三地部署→异步复制→就近拉取→P2P 加速→统一认证→全局监控 |

---

# 高级篇（第 32-40 章）

> **核心目标**：源码级理解 Harbor 实现原理，掌握自定义扩展开发与极端场景优化。

| 章节 | 主题 | 核心内容 |
|------|------|---------|
| 32 | Harbor 源码架构与工程化实践 | 项目结构总览、依赖管理、构建系统、贡献指南、代码规范 |
| 33 | Core 核心服务源码剖析 | API 路由、请求处理链路、Beego 框架、认证/授权中间件、业务层设计 |
| 34 | JobService 异步任务引擎源码 | Job 调度器、任务队列、重试机制、状态机、Redis 实现 |
| 35 | Registry 层源码——Distribution 适配与存储驱动 | Docker Registry 协议适配、存储驱动接口、Blob 上传/下载链路 |
| 36 | Scanner Adapter 扫描适配器源码 | 可插拔扫描框架设计、Trivy Adapter 实现、扫描结果标准化 |
| 37 | 复制引擎源码深度剖析 | Replication Engine 事件驱动、Scheduler/Execution Manager、失败重试策略 |
| 38 | Harbor 安全模型源码解析 | 配额管理（Quota）、不可变性（Immutability）、CVE 执行策略、Webhook |
| 39 | Harbor 自定义扩展开发实战 | 自定义 Scanner Adapter、自定义 Replication Adapter、自定义认证后端 |
| 40 | 【高级篇综合实战】构建安全高性能企业级 Harbor 平台 | 架构设计→高可用→自定义扩展→安全加固→GitOps 集成→自动化运维→SRE 落地 SOP |

---

## 附录 A：源码阅读路线图

1. 入口：`src/cmd/core/main.go` → Core 服务启动
2. 核心：`src/server/v2.0/handler/` → API 路由与处理
3. 异步：`src/jobservice/` → 任务调度与执行
4. 存储：`src/registry/` → Registry 适配层
5. 扫描：`src/pkg/scan/` → Scanner 框架
6. 复制：`src/controller/replication/` → 复制引擎

## 附录 B：编译调试指南

- Go 版本：1.21+，启用 module
- 编译命令：`make build` / `make compile_core`
- 调试工具：Delve（`dlv`）、Goland 远程调试
- 日志级别：DEBUG / INFO / WARNING / ERROR

## 附录 C：推荐工具链

- 镜像管理：Docker / Buildah / Skopeo / Crane
- 编排调度：Docker Compose / Kubernetes / Helm
- 压测工具：wrk / locust / go-stress-testing
- 抓包分析：tcpdump / Wireshark / mitmproxy
- 安全扫描：Trivy / Grype / Snyk
- 监控告警：Prometheus / Grafana / Alertmanager

## 附录 D：思考题参考答案索引

- 基础篇思考题答案：见各章末尾
- 中级篇思考题答案：见各章末尾
- 高级篇思考题答案：见各章末尾

---

> **版权声明**：本专栏基于 Harbor v2.12+ 官方源码（Apache License 2.0）编写，所有源码引用均遵循原许可证条款。
