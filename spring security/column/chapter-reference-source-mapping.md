# 章节 ↔ docs/reference ↔ 本仓库源码核对清单

专栏正文文件已统一命名为 **`第 n 章：主题.md`**，索引见 [README.md](./README.md)。

撰稿时以 **当前分支 Spring Security 源码与 Javadoc** 为准；`docs/reference` 中文章多为早期版本叙事，仅作概念与故事线参考。下列「源码锚点」均为本仓库内路径（除非注明为 Spring Framework）。

**图例**：`[ref]` = 参考文档；`[src]` = 源码；`—` = 无直接对应参考文，需结合官方文档与模块测试。

---

## 基础篇

| 章 | 主题 | [ref] docs/reference | [src] 建议阅读入口 |
|----|------|----------------------|---------------------|
| 01 | 认证与授权全景 | [带你一步步走进Spring security本质filter chain.md](../reference/带你一步步走进Spring security本质filter chain.md)（背景与链式心智） | `web/.../FilterChainProxy.java`；`web/.../SecurityFilterChain.java` |
| 02 | DelegatingFilterProxy 入门 | 同上 | **注意**：`DelegatingFilterProxy` 位于 **Spring Framework** `spring-web`，本仓库可看注册关系：`web/.../AbstractSecurityWebApplicationInitializer.java`、测试中 `springSecurityFilterChain` 过滤器名 |
| 03 | Boot 自动配置 | — | `config/.../spring-security-config.gradle` 所在模块；Spring Boot 侧见各版本 `spring-boot-starter-security`（**不在本仓库**，撰稿时写依赖坐标即可） |
| 04 | 第一条 SecurityFilterChain | [带你一步步走进Spring security认证的核心原理.md](../reference/带你一步步走进Spring security认证的核心原理.md) | `web/.../SecurityFilterChain.java`；`config/.../HttpSecurity.java`；`config/.../SecurityFilterChain` Bean 构建（`HttpSecurity` → `SecurityFilterChain`） |
| 05 | 表单登录与 UserDetails | 同上 + [spring security源码分析之core包小结.md](../reference/spring%20security源码分析之core包小结.md) | `core/.../UserDetails.java`；`core/.../UserDetailsService.java`；`config/.../FormLoginConfigurer.java` |
| 06 | JDBC 与密码 | [深入揭秘spring security之jdbc方式Basic认证原理.md](../reference/深入揭秘spring%20security之jdbc方式Basic认证原理.md) | `core/.../provisioning/JdbcUserDetailsManager.java` |
| 07 | PasswordEncoder | [Spring-security-crypto加密模块探秘.md](../reference/Spring-security-crypto加密模块探秘.md) | `crypto/` 模块；`crypto/.../bcrypt/BCryptPasswordEncoder.java` 等 |
| 08 | SecurityContext 与线程 | [spring security源码分析之core包小结.md](../reference/spring%20security源码分析之core包小结.md) | `core/.../SecurityContextHolder.java`；`core/.../SecurityContext.java` |
| 09 | 匿名认证 | [Spring Security匿名认证揭秘.md](../reference/Spring%20Security匿名认证揭秘.md) | `web/.../authentication/AnonymousAuthenticationFilter.java` |
| 10 | 记住我 | [“勿忘我” --Spring Security让你无法忘怀的密钥.md](../reference/“勿忘我”%20%20--Spring%20Security让你无法忘怀的密钥.md) | `web/.../rememberme/` 下过滤器与 `TokenRepository` 相关实现 |
| 11 | 登出 | [上得厅堂下得厨房：spring security5使用mvc实现退出功能.md](../reference/上得厅堂下得厨房：spring%20security5使用mvc实现退出功能.md) | `config/.../LogoutConfigurer.java`；`web/.../authentication/logout/` |
| 12 | CSRF | — | `web/.../csrf/CsrfFilter.java` 及 `CsrfTokenRepository` |
| 13 | 综合脚手架 | [我是Spring Security5：“恭喜发财，钱包拿来！”.md](../reference/我是Spring%20Security5：“恭喜发财，钱包拿来！”.md)（趣味叙事参考） | 串联 04–11；样例可参考仓库 `samples` 或 `itest`（若存在） |

---

## 中级篇

| 章 | 主题 | [ref] docs/reference | [src] 建议阅读入口 |
|----|------|----------------------|---------------------|
| 14 | URL vs 方法授权 | [带你一步步走进Spring security授权的核心原理.md](../reference/带你一步步走进Spring%20security授权的核心原理.md) | `config/.../web/builders/HttpSecurity.java`（`authorizeHttpRequests`）；`method/MethodSecurityInterceptor`（若使用方法安全） |
| 15 | 方法安全与 SpEL | [Spring Security方法级别的权限控制：我可以做的更好.md](../reference/Spring%20Security方法级别的权限控制：我可以做的更好.md) | `core/.../access/prepost/`；`config/.../method/configuration/` |
| 16 | 并发会话 | [如何允许一个用户同时登录？Spring Security一个配置搞定！.md](../reference/如何允许一个用户同时登录？Spring%20Security一个配置搞定！.md) | `web/.../session/` 下 `SessionManagementFilter`、`ConcurrentSessionControlAuthenticationStrategy` 等 |
| 17 | Basic/Digest | [Spring Security摘要认证实战及原理.md](../reference/Spring%20Security摘要认证实战及原理.md) | `web/.../authentication/www/BasicAuthenticationFilter.java`；Digest 若仍支持则搜 `DigestAuthenticationFilter` |
| 18 | 异常与 i18n | [Spring Security异常信息本地化.md](../reference/Spring%20Security异常信息本地化.md) | `web/.../access/ExceptionTranslationFilter.java`；`core/.../AuthenticationException` 层次 |
| 19 | 缓存与自定义 UserDetails | [Spring security用户认证授权心随意动，nosql随你所爱.md](../reference/Spring%20security用户认证授权心随意动，nosql随你所爱.md) | `UserDetailsService` 自定义实现 + 缓存由 Spring Cache 提供（本仓库无业务代码，讲模式即可） |
| 20 | Session vs JWT 概念 | — | `oauth2/oauth2-resource-server/` 模块 README 与 `JwtAuthenticationProvider` 等 |
| 21 | Resource Server + JWT | — | `oauth2/oauth2-resource-server/` 全模块 |
| 22 | CORS 与安全头 | — | `config/.../CorsConfigurer.java`；`web/.../header/` |
| 23 | Reactive | [Spring Security 5 for Reactive Applications.md](../reference/Spring%20Security%205%20for%20Reactive%20Applications.md) | `config/.../web/server/ServerHttpSecurity.java`；`web/.../server/WebFilterChainProxy.java`（对照 Servlet 侧 `FilterChainProxy`） |
| 24 | X.509 | [spring security X.509认证.md](../reference/spring%20security%20X.509认证.md) | `web/.../authentication/preauth/x509/`（包名以源码为准） |
| 25 | 可观测性 | — | `logging/`；应用侧 MDC 与 Spring Boot Actuator（**非**本仓库核心） |
| 26 | 测试 | — | `test/` 模块：`@WithMockUser`、`SecurityMockMvcRequestPostProcessors` 等 |

---

## 高级篇

| 章 | 主题 | [ref] docs/reference | [src] 建议阅读入口 |
|----|------|----------------------|---------------------|
| 27 | ProviderManager | [spring security源码分析之core包小结.md](../reference/spring%20security源码分析之core包小结.md) | `core/.../authentication/ProviderManager.java`；`AuthenticationProvider` |
| 28 | Filter 顺序与扩展 | [带你一步步走进Spring security本质filter chain.md](../reference/带你一步步走进Spring%20security本质filter%20chain.md) | `web/.../FilterChainProxy.java`；`web/.../SecurityFilterChain.java`；`Filter` 排序与 `Order` |
| 29 | Voter 与决策 | [带你一步步走进Spring security授权的核心原理.md](../reference/带你一步步走进Spring%20security授权的核心原理.md) | `core/.../access/vote/`；`AffirmativeBased` 等 |
| 30 | ACL | [Spring Security acl 如何控制更细粒度的权限？.md](../reference/Spring%20Security%20acl%20如何控制更细粒度的权限？.md) | `acl/` 模块 |
| 31 | 多 HttpSecurity | [Spring Security 如何配置多个HttpSecurity？.md](../reference/Spring%20Security%20如何配置多个HttpSecurity？.md) | `config/.../SecurityFilterChain` 多 Bean；`HttpSecurity` 与 `@Order` |
| 32 | Authorization Server | — | `oauth2/oauth2-authorization-server/` |
| 33 | LDAP | — | `ldap/` 模块 |
| 34 | 源码方法论 | [spring security源码分析心得.md](../reference/spring%20security源码分析心得.md) | 贡献指南：`CONTRIBUTING.adoc`（若存在）；Issue/PR 流程 |
| 35 | 性能 | — | `itest/context/.../filter-chain-performance-app-context.xml`（线索）；`FilterChainProxy` 基准可自建 JMH |
| 36 | 加固清单 | [spring security常见面试题目集萃.md](../reference/spring%20security常见面试题目集萃.md) | 依赖：`gradle/libs.versions.toml` 或 BOM；配置：`HttpSecurity` 默认项 |
| 37 | SaaS 综合 | 串联 27–32 参考文 | 多模块组合，无单一路径 |
| 38 | 回顾与面试 | [spring security常见面试题目集萃.md](../reference/spring%20security常见面试题目集萃.md) | — |

---

## 撰稿前快速核对（命令线索）

在本仓库根目录可用下列方式**自检类名是否仍存在**（随版本可能重命名）：

```bash
# 示例：确认 FilterChainProxy 包路径
rg -n "class FilterChainProxy" --glob "*.java"

# 示例：查找 Remember Me 相关过滤器
rg -n "RememberMe" web/src/main/java --glob "*.java" | head
```

若某 `[src]` 路径与当前分支不一致，以 **搜索结果为准** 更新本表下一版（本文件允许随专栏撰稿迭代）。
