# Spring Security 专栏：章节锁定说明

本文档落实专栏「38 章、三级结构」的最终定稿，**不修改**计划文件，仅作为撰稿与排期的唯一章节清单。

- **总章数**：38 章（在模板要求的 35–40 章范围内）
- **结构**：基础篇 13 章 + 中级篇 13 章 + 高级篇 12 章
- **单章规范**：对齐 [docs/template.md](../template.md)（四段式结构、小胖/小白/大师对话、每章 3000–5000 字）
- **成稿文件名**：`第 n 章：主题.md`（中文全角冒号 `：`；与 [README.md](./README.md) 一致）

---

## 基础篇（13 章）

| 章 | 文件名（与仓库一致） | 中文标题 |
|----|----------------------|----------|
| 01 | `第 1 章：企业后台「谁进来了」：认证与授权全景.md` | 企业后台「谁进来了」：认证与授权全景 |
| 02 | `第 2 章：DelegatingFilterProxy 与 Security 过滤器链入门.md` | DelegatingFilterProxy 与 Security 过滤器链入门 |
| 03 | `第 3 章：Boot 集成与安全自动配置心智模型.md` | Boot 集成与安全自动配置心智模型 |
| 04 | `第 4 章：SecurityFilterChain 与 HttpSecurity：第一条链.md` | SecurityFilterChain 与 HttpSecurity：第一条链 |
| 05 | `第 5 章：表单登录与 UserDetails：从内存用户到业务用户.md` | 表单登录与 UserDetails：从内存用户到业务用户 |
| 06 | `第 6 章：JDBC UserDetailsService 与密码存储.md` | JDBC UserDetailsService 与密码存储 |
| 07 | `第 7 章：密码编码：PasswordEncoder 与迁移策略.md` | 密码编码：PasswordEncoder 与迁移策略 |
| 08 | `第 8 章：会话、SecurityContext 与线程绑定.md` | 会话、SecurityContext 与线程绑定 |
| 09 | `第 9 章：匿名用户：公开接口与「未登录」语义.md` | 匿名用户：公开接口与「未登录」语义 |
| 10 | `第 10 章：记住我：降低登录摩擦与风险平衡.md` | 记住我：降低登录摩擦与风险平衡 |
| 11 | `第 11 章：登出与会话失效：合规留痕.md` | 登出与会话失效：合规留痕 |
| 12 | `第 12 章：CSRF 与同源策略：写操作保护.md` | CSRF 与同源策略：写操作保护 |
| 13 | `第 13 章：基础篇综合实战：最小「管理后台」脚手架.md` | 基础篇综合实战：最小「管理后台」脚手架 |

---

## 中级篇（13 章）

| 章 | 文件名（与仓库一致） | 中文标题 |
|----|----------------------|----------|
| 14 | `第 14 章：URL 授权 vs 方法授权选型.md` | URL 授权 vs 方法授权选型 |
| 15 | `第 15 章：@PreAuthorize、@Secured 与 SpEL.md` | @PreAuthorize/@Secured 与 SpEL |
| 16 | `第 16 章：多会话与并发控制：踢人、限登.md` | 多会话与并发控制：踢人、限登 |
| 17 | `第 17 章：HTTP Basic 与 Digest：脚本与遗留客户端.md` | HTTP Basic/Digest：脚本与遗留客户端 |
| 18 | `第 18 章：异常体系与 i18n：前后端一致错误体验.md` | 异常体系与 i18n：前后端一致错误体验 |
| 19 | `第 19 章：自定义 UserDetailsService 与缓存：性能与一致性.md` | 自定义 UserDetailsService 与缓存：性能与一致性 |
| 20 | `第 20 章：无状态 API：Session vs JWT 选型.md` | 无状态 API：Session vs JWT 选型 |
| 21 | `第 21 章：OAuth2 Resource Server 与 JWT：资源方最小实践.md` | OAuth2 Resource Server + JWT：资源方最小实践 |
| 22 | `第 22 章：CORS 与安全头：前后端分离网关.md` | CORS 与安全头：前后端分离网关 |
| 23 | `第 23 章：Spring Security Reactive：WebFlux 安全链.md` | Spring Security Reactive：WebFlux 安全链 |
| 24 | `第 24 章：X.509 与 客户端证书：双向 TLS 内网调用.md` | X.509/客户端证书：双向 TLS 内网调用 |
| 25 | `第 25 章：可观测性：日志、审计与 Actuator.md` | 可观测性：日志、审计与 Actuator |
| 26 | `第 26 章：测试策略：@WithMockUser、MockMvc、切片测试.md` | 测试策略：@WithMockUser、MockMvc、切片测试 |

---

## 高级篇（12 章）

| 章 | 文件名（与仓库一致） | 中文标题 |
|----|----------------------|----------|
| 27 | `第 27 章：AuthenticationManager 与 ProviderManager 源码走读.md` | AuthenticationManager/ProviderManager 源码走读 |
| 28 | `第 28 章：FilterChain 排序与自定义 Filter 插入点.md` | FilterChain 排序与自定义 Filter 插入点 |
| 29 | `第 29 章：投票器与 AccessDecisionManager：复杂授权模型.md` | 投票器与 AccessDecisionManager：复杂授权模型 |
| 30 | `第 30 章：ACL：细粒度领域对象权限.md` | ACL：细粒度领域对象权限 |
| 31 | `第 31 章：多 SecurityFilterChain 与 多 HttpSecurity.md` | 多 SecurityFilterChain/多 HttpSecurity |
| 32 | `第 32 章：OAuth2 Authorization Server：扩展与边界.md` | OAuth2 Authorization Server：扩展与边界 |
| 33 | `第 33 章：LDAP 与 AD 集成要点.md` | LDAP/AD 集成要点 |
| 34 | `第 34 章：源码阅读方法论：从 issue 到 PR.md` | 源码阅读方法论：从 issue 到 PR |
| 35 | `第 35 章：性能与常见瓶颈：Session 固定、过滤器开销.md` | 性能与常见瓶颈：Session 固定、过滤器开销 |
| 36 | `第 36 章：安全加固清单：依赖漏洞、配置审计、最小权限.md` | 安全加固清单：依赖漏洞、配置审计、最小权限 |
| 37 | `第 37 章：综合大实战：多租户 SaaS 安全架构.md` | 综合大实战：多租户 SaaS 安全架构 |
| 38 | `第 38 章：专栏回顾、面试高频与自测题解析.md` | 专栏回顾、面试高频与自测题解析 |

---

## 变更说明

- 本清单与计划中的主题表一致；**文件名** 为 Windows 兼容写法（全角 `：`；`/` 在文件名中改为「与」等）。
- 若后续合并或拆分章节，应保持 **总章数仍在 35–40** 内，并同步更新 [chapter-reference-source-mapping.md](./chapter-reference-source-mapping.md)。
