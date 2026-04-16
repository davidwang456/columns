# Spring Security 专栏正文（第 1–38 章）

本目录为 **按章成稿** 的 Markdown，结构对齐 [docs/template.md](../template.md)（四段式：背景、对话、实战、总结）。章节清单与标题见 [spring-security-column-outline-locked.md](./spring-security-column-outline-locked.md)；参考与源码锚点见 [chapter-reference-source-mapping.md](./chapter-reference-source-mapping.md)。

**文件命名**：`第 n 章：主题.md`（使用中文全角冒号 `：`；因 Windows 文件名限制，原标题中的 `/` 已改为「与」「、」等，与下表一致）。

## 基础篇（1–13）

| 章 | 文件 |
|----|------|
| 1 | [第 1 章：企业后台「谁进来了」：认证与授权全景.md](./第 1 章：企业后台「谁进来了」：认证与授权全景.md) |
| 2 | [第 2 章：DelegatingFilterProxy 与 Security 过滤器链入门.md](./第 2 章：DelegatingFilterProxy 与 Security 过滤器链入门.md) |
| 3 | [第 3 章：Boot 集成与安全自动配置心智模型.md](./第 3 章：Boot 集成与安全自动配置心智模型.md) |
| 4 | [第 4 章：SecurityFilterChain 与 HttpSecurity：第一条链.md](./第 4 章：SecurityFilterChain 与 HttpSecurity：第一条链.md) |
| 5 | [第 5 章：表单登录与 UserDetails：从内存用户到业务用户.md](./第 5 章：表单登录与 UserDetails：从内存用户到业务用户.md) |
| 6 | [第 6 章：JDBC UserDetailsService 与密码存储.md](./第 6 章：JDBC UserDetailsService 与密码存储.md) |
| 7 | [第 7 章：密码编码：PasswordEncoder 与迁移策略.md](./第 7 章：密码编码：PasswordEncoder 与迁移策略.md) |
| 8 | [第 8 章：会话、SecurityContext 与线程绑定.md](./第 8 章：会话、SecurityContext 与线程绑定.md) |
| 9 | [第 9 章：匿名用户：公开接口与「未登录」语义.md](./第 9 章：匿名用户：公开接口与「未登录」语义.md) |
| 10 | [第 10 章：记住我：降低登录摩擦与风险平衡.md](./第 10 章：记住我：降低登录摩擦与风险平衡.md) |
| 11 | [第 11 章：登出与会话失效：合规留痕.md](./第 11 章：登出与会话失效：合规留痕.md) |
| 12 | [第 12 章：CSRF 与同源策略：写操作保护.md](./第 12 章：CSRF 与同源策略：写操作保护.md) |
| 13 | [第 13 章：基础篇综合实战：最小「管理后台」脚手架.md](./第 13 章：基础篇综合实战：最小「管理后台」脚手架.md) |

## 中级篇（14–26）

| 章 | 文件 |
|----|------|
| 14 | [第 14 章：URL 授权 vs 方法授权选型.md](./第 14 章：URL 授权 vs 方法授权选型.md) |
| 15 | [第 15 章：@PreAuthorize、@Secured 与 SpEL.md](./第 15 章：@PreAuthorize、@Secured 与 SpEL.md) |
| 16 | [第 16 章：多会话与并发控制：踢人、限登.md](./第 16 章：多会话与并发控制：踢人、限登.md) |
| 17 | [第 17 章：HTTP Basic 与 Digest：脚本与遗留客户端.md](./第 17 章：HTTP Basic 与 Digest：脚本与遗留客户端.md) |
| 18 | [第 18 章：异常体系与 i18n：前后端一致错误体验.md](./第 18 章：异常体系与 i18n：前后端一致错误体验.md) |
| 19 | [第 19 章：自定义 UserDetailsService 与缓存：性能与一致性.md](./第 19 章：自定义 UserDetailsService 与缓存：性能与一致性.md) |
| 20 | [第 20 章：无状态 API：Session vs JWT 选型.md](./第 20 章：无状态 API：Session vs JWT 选型.md) |
| 21 | [第 21 章：OAuth2 Resource Server 与 JWT：资源方最小实践.md](./第 21 章：OAuth2 Resource Server 与 JWT：资源方最小实践.md) |
| 22 | [第 22 章：CORS 与安全头：前后端分离网关.md](./第 22 章：CORS 与安全头：前后端分离网关.md) |
| 23 | [第 23 章：Spring Security Reactive：WebFlux 安全链.md](./第 23 章：Spring Security Reactive：WebFlux 安全链.md) |
| 24 | [第 24 章：X.509 与 客户端证书：双向 TLS 内网调用.md](./第 24 章：X.509 与 客户端证书：双向 TLS 内网调用.md) |
| 25 | [第 25 章：可观测性：日志、审计与 Actuator.md](./第 25 章：可观测性：日志、审计与 Actuator.md) |
| 26 | [第 26 章：测试策略：@WithMockUser、MockMvc、切片测试.md](./第 26 章：测试策略：@WithMockUser、MockMvc、切片测试.md) |

## 高级篇（27–38）

| 章 | 文件 |
|----|------|
| 27 | [第 27 章：AuthenticationManager 与 ProviderManager 源码走读.md](./第 27 章：AuthenticationManager 与 ProviderManager 源码走读.md) |
| 28 | [第 28 章：FilterChain 排序与自定义 Filter 插入点.md](./第 28 章：FilterChain 排序与自定义 Filter 插入点.md) |
| 29 | [第 29 章：投票器与 AccessDecisionManager：复杂授权模型.md](./第 29 章：投票器与 AccessDecisionManager：复杂授权模型.md) |
| 30 | [第 30 章：ACL：细粒度领域对象权限.md](./第 30 章：ACL：细粒度领域对象权限.md) |
| 31 | [第 31 章：多 SecurityFilterChain 与 多 HttpSecurity.md](./第 31 章：多 SecurityFilterChain 与 多 HttpSecurity.md) |
| 32 | [第 32 章：OAuth2 Authorization Server：扩展与边界.md](./第 32 章：OAuth2 Authorization Server：扩展与边界.md) |
| 33 | [第 33 章：LDAP 与 AD 集成要点.md](./第 33 章：LDAP 与 AD 集成要点.md) |
| 34 | [第 34 章：源码阅读方法论：从 issue 到 PR.md](./第 34 章：源码阅读方法论：从 issue 到 PR.md) |
| 35 | [第 35 章：性能与常见瓶颈：Session 固定、过滤器开销.md](./第 35 章：性能与常见瓶颈：Session 固定、过滤器开销.md) |
| 36 | [第 36 章：安全加固清单：依赖漏洞、配置审计、最小权限.md](./第 36 章：安全加固清单：依赖漏洞、配置审计、最小权限.md) |
| 37 | [第 37 章：综合大实战：多租户 SaaS 安全架构.md](./第 37 章：综合大实战：多租户 SaaS 安全架构.md) |
| 38 | [第 38 章：专栏回顾、面试高频与自测题解析.md](./第 38 章：专栏回顾、面试高频与自测题解析.md) |
