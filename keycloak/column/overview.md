# Keycloak 身份认证与授权实战修炼专栏大纲

> **版本**：Keycloak 26.x | **总章节**：40章（基础篇16 / 中级篇16 / 高级篇8）
> **面向人群**：开发、测试、运维、架构师 | **字数**：3000-5000字/章

---

## 专栏定位

以Keycloak 26.x为蓝本，从概念原理到部署实战，从SPI扩展到源码剖析，从性能调优到生产落地。每一章均采用「业务痛点 → 三人剧本对话 → 项目实战 → 总结思考」的四段式结构，兼顾趣味性、实战性与深度。**实战为主，理论为辅，由浅入深。**

---

## 阅读路线建议

| 角色 | 路线 | 重点章节 |
|------|------|---------|
| 新人开发/测试 | 基础篇全读 → 中级篇选读 | 第1-16章 |
| 核心开发/运维 | 基础篇速读 → 中级篇精读 → 高级篇选读 | 第17-32、33-40章 |
| 架构师/资深开发 | 高级篇为主线 → 按需回溯中级篇 | 第33-40章，辅以17-32章 |

---

## 角色设定（贯穿全专栏）

| 角色 | 性格标签 | 职责 | 话风示例 |
|------|---------|------|---------|
| 小胖 | 爱吃爱玩、不求甚解 | 用生活化比喻抛出问题，引发讨论 | "这不就跟公司门禁刷卡一样吗？为啥要搞那么复杂？" |
| 小白 | 喜静、喜深入 | 追问原理、边界条件、风险、备选方案 | "那如果Token被窃取了怎么办？有没有比JWT更安全的方案？" |
| 大师 | 资深技术Leader | 讲透业务约束与选型，由浅入深打比方 | "你可以把Realm想象成写字楼里的不同公司——同一栋楼，但各自的门禁系统是独立的。" |

---

# 基础篇（第1-16章）

> **核心目标**：建立Keycloak核心概念，掌握单机版部署、客户端与用户管理、常用协议与集成、初级故障排查。
> **实战关联**：Docker部署、Realm配置、Spring Boot集成、主题定制、社交登录。

| 章节 | 主题 | 核心内容 |
|------|------|---------|
| 1 | 术语全景与认证授权工作原理 | 术语词典（Realm/Client/Role/Session/Token）、OAuth2/OIDC/SAML定位、SSO原理、架构全景图 |
| 2 | 安装部署与初次体验 | Docker/裸机部署三种方式、Quarkus启动参数、管理控制台总览、创建第一个Realm和用户 |
| 3 | Realm详解——多租户边界与配置体系 | Realm隔离模型、Settings全览、Realm角色vs客户端角色、导入导出、多环境隔离实战 |
| 4 | 客户端管理——从注册到信任 | 客户端类型（confidential/public/bearer-only）、OIDC配置、认证方式、Client Scopes |
| 5 | 用户管理与凭证体系 | 用户CRUD、密码策略、OTP/WebAuthn、Group层级结构、Required Actions、批量导入 |
| 6 | 角色体系——从RBAC到细粒度授权 | Realm/客户端角色、复合角色、角色映射、RBAC实战设计 |
| 7 | OAuth 2.0授权码流程深度实战 | 四大角色、授权码+PKCE完整时序、curl/Postman模拟全流程、弃用Implicit Flow原因 |
| 8 | OpenID Connect——Token体系与JWT揭秘 | ID/ Access/ Refresh Token对比、JWT结构解剖、JWKS端点、手动校验签名 |
| 9 | 密码策略与暴力破解防护 | Password Policies全解、Brute Force Detection机制、密码哈希算法对比、OTP策略 |
| 10 | 会话管理与单点登录深度解析 | User/Client Session模型、SSO Cookie、SLO单点注销、并发会话控制、跨域SSO方案 |
| 11 | 主题定制——打造企业品牌化登录页 | 主题体系（Base/Keycloak/自定义）、FreeMarker模板、登录页/邮件模板定制、国际化 |
| 12 | 社交登录集成实战 | IdP概念、Google/GitHub内置接入、微信扫码登录、首次登录自动创建用户与角色分配 |
| 13 | 用户联邦——LDAP/AD集成 | User Federation机制、OpenLDAP对接、AD Kerberos认证、组同步、双写共存方案 |
| 14 | Admin REST API自动化管理实战 | API全景、认证方式、批量用户创建/角色分配、Shell脚本自动化运维 |
| 15 | Spring Boot + Keycloak安全集成 | OAuth2 Client/Resource Server配置、标准方案替代Adapter迁移、前后端分离三端联动 |
| 16 | 【综合实战】搭建企业级SSO统一认证平台 | 5系统统一登录、LDAP+社交双通道、RBAC菜单权限、品牌主题、密码安全策略全覆盖 |

---

# 中级篇（第17-32章）

> **核心目标**：掌握分布式场景下的架构设计、集群部署、SPI扩展、性能调优与可观测性。
> **实战关联**：K8s部署、自定义SPI、授权服务、性能压测、监控告警。

| 章节 | 主题 | 核心内容 |
|------|------|---------|
| 17 | 集群架构与高可用设计 | 负载均衡+多节点+共享DB+分布式缓存、集群发现、Sticky Session、多活拓扑、故障转移 |
| 18 | Infinispan分布式缓存深度实战 | 缓存域模型、Distributed/Replicated拓扑、owners配置、L1缓存、一致性保证、脑裂恢复 |
| 19 | Docker Compose多节点集群生产实践 | 3节点编排、健康检查、DB连接池、环境变量配置模板化、日志收集 |
| 20 | Kubernetes上部署Keycloak集群 | Operator+Helm部署、KUBE_PING发现、StatefulSet、PVC持久化、cert-manager自动TLS |
| 21 | 数据库选型、调优与多数据中心架构 | PostgreSQL/MySQL对比、PgBouncer连接池、主从复制、跨数据中心Geo-Cluster模式 |
| 22 | Authorization Services——细粒度授权 | Resource/Scope/Policy/Permission模型、UMA协议、RPT令牌、规则策略、文档系统权限实战 |
| 23 | 自定义SPI——Required Action扩展 | SPI体系入门、RequiredAction接口、FTL模板渲染、JAR打包部署、强制完善个人信息实战 |
| 24 | 自定义SPI——用户存储提供者 | UserStorageProvider接口、REST API后端对接、密码校验、缓存刷新策略、HR系统联邦实战 |
| 25 | 自定义SPI——认证流程编织 | Authentication Flow模型、自定义Authenticator、多因素编排、自适应认证流程实战 |
| 26 | 自定义SPI——自定义事件监听器 | Event体系、Kafka登录事件上报、钉钉异常登录实时告警、ELK行为分析 |
| 27 | 自定义SPI——自定义协议映射器 | Protocol Mapper体系、部门组织树注入Token、动态Claims选择、Token体积控制 |
| 28 | 微服务架构深度集成 | API Gateway统一认证、Token Relay模式、服务间Client Credentials认证、OpenTelemetry全链路追踪 |
| 29 | 性能压测与JVM调优实战 | k6压测方案、OAuth2登录/Token校验场景、JVM堆/GC调优、Quarkus Native对比、压测报告 |
| 30 | 可观测性——Prometheus+Grafana+Loki监控 | /metrics端点、核心指标采集、三大Dashboard设计、5条告警规则、JSON日志接入 |
| 31 | 多租户SaaS架构与Realm设计 | 共享vs独立Realm选型、租户自动创建/销毁、配额管理、100+租户SaaS认证平台设计 |
| 32 | 【综合实战】构建高可用企业统一认证中心 | K8s集群+PostgreSQL主从+自研MFA+LDAP联邦+审计SIEM+5000并发P99<3s |

---

# 高级篇（第33-40章）

> **核心目标**：源码级理解Keycloak的核心实现，掌握复杂场景的架构设计与极致优化。
> **实战关联**：源码剖析、核心模块改造、极端场景压测、SRE落地、自研IAM组件。

| 章节 | 主题 | 核心内容 |
|------|------|---------|
| 33 | 核心架构源码剖析 | 项目模块全景、Quarkus启动链路、SPI加载机制、Model API设计、JPA/Cache层抽象、本地调试环境搭建 |
| 34 | 请求生命周期——从HTTP到认证结果全链路 | RealmsResource入口、AuthenticationManager、Flow引擎、Token端点、完整调用链路断点追踪 |
| 35 | Token签发与JWT签名校验源码深度剖析 | TokenManager/JWSBuilder、密钥管理轮换、JWKS端点、RS256/ES256对比、自定义Claims注入 |
| 36 | Authentication Flow引擎源码剖析 | 递归Flow执行引擎、状态机决策、ConditionalAuthenticator、嵌套SubFlow、复杂流调试追踪 |
| 37 | 自定义Authenticator深度开发——多因素认证 | 短信OTP Authenticator、微信扫码登录、状态保持、配置UI、国际化、全套生产级组件开发 |
| 38 | Keycloak性能极致优化与C10K挑战 | 火焰图热点定位、DB慢查询优化、Infinispan Off-Heap、TLS 0-RTT、GraalVM Native编译、C10K压测 |
| 39 | 自定义SPI高级实战——TokenExchange服务 | RFC 8693协议、自定义Endpoint SPI、JWT↔API Key置换、白名单控制、安全审计 |
| 40 | 【综合实战】从零构建企业级IAM中台 | 金融科技场景、自研MFA+风险评估引擎+TokenExchange网关+全链路审计、10万QPS、99.99%可用性 |

---

## 附录

- **A**：源码阅读路线图（模块入口、关键类定位）
- **B**：开发环境搭建与调试指南（Maven编译、远程调试、单元测试）
- **C**：推荐工具链（k6/Docker/K8s/Prometheus/Grafana/Jaeger/IDEA）
- **D**：思考题参考答案索引

---

> **版权声明**：本专栏大纲仅供学习参考，Keycloak为Apache 2.0开源协议，所有源码引用均遵循原许可证条款。
