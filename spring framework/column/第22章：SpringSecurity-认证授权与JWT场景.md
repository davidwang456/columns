# 第 22 章：Spring Security——认证授权与 JWT 场景

> **业务线**：电商 / 订单履约微服务（拟真场景）。本章可独立阅读；与全书案例弱关联。

> **篇章**：中级篇（全书第 19–35 章；架构与分布式、性能、可观测性）

## 上一章思考题回顾

1. **Boot 3 / Security 6**：**`WebSecurityConfigurerAdapter` 已移除**，改为 **`SecurityFilterChain` Bean** + **`HttpSecurity` lambda DSL**。  
2. **JWT**：**Header `Authorization: Bearer`** 常见于 API；**HttpOnly Cookie** 可降低 XSS 窃取风险，但需 **CSRF** 策略配合 Web 场景。

---

## 1 项目背景

订单 API 需 **鉴权**：运营端 **RBAC**，C 端用户 **登录态**。若过滤器手写 JWT 解析，**与 Spring MVC 异常体系**割裂。

**痛点**：  
- **403 vs 401** 语义混乱。  
- **方法级** `@PreAuthorize` 与 **URL 级** `authorizeHttpRequests` 重复或冲突。

---

## 2 项目设计（剧本式对话）

**角色**：小胖 / 小白 / 大师。  
**结构**：先拆 **401/403** → 再讲过滤器链位置 → 最后 JWT 与测试策略。

**小胖**：我过滤器里解析 JWT，不就行了？Spring Security 太重。

**大师**：你能写，但**异常模型**、**MVC 异常处理**、**方法级授权**、**OAuth2 Resource Server** 都会分裂。Security = **认证（你是谁）** + **授权（你能做什么）**；过滤器链在 **DispatcherServlet 之前**。

**技术映射**：**SecurityFilterChain** + **AuthenticationManager** + **JwtAuthenticationFilter**（自定义）。

**小白**：401 和 403 到底差啥？

**大师**：**401** = 未认证；**403** = 已认证但无权限。别用 403 掩盖「没登录」，前端会疯。

**小胖**：JWT 放 Cookie 还是 Header？

**大师**：**Bearer Header** 更适合纯 API；**HttpOnly Cookie** 可降低 XSS 窃取，但 Web 场景要处理 **CSRF** 与 **SameSite**。没有银弹，只有**威胁模型**。

**小白**：`authorizeHttpRequests` 写了，方法上 `@PreAuthorize` 还要吗？

**大师**：URL 级是**粗粒度**；方法级是**细粒度**（同一 URL 不同角色）。重复时要防止**规则打架**，统一在 Code Review 里可见。

---

## 3 项目实战

### 3.1 环境准备

| 项 | 说明 |
|----|------|
| 依赖 | `spring-boot-starter-security` |
| 可选 | `spring-boot-starter-oauth2-resource-server`（JWT 资源服务器） |

```xml
<dependency>
  <groupId>org.springframework.boot</groupId>
  <artifactId>spring-boot-starter-security</artifactId>
</dependency>
```

### 3.2 分步实现

**步骤 1 — 目标**：定义 **SecurityFilterChain**（Boot 3 lambda DSL）。

```java
@Bean
SecurityFilterChain chain(HttpSecurity http) throws Exception {
    return http
        .csrf(csrf -> csrf.disable())
        .authorizeHttpRequests(auth -> auth
            .requestMatchers("/actuator/health").permitAll()
            .requestMatchers("/api/orders/**").authenticated())
        .oauth2ResourceServer(oauth2 -> oauth2.jwt(Customizer.withDefaults()))
        .build();
}
```

（演示可用 **mock JWT** 或 **spring-security-test**；本地开发可用 **issuer-uri** 指向 Keycloak/Auth0 的 well-known。）

**步骤 2 — 目标**：用 **`curl`** 验证匿名访问 `/api/orders` 返回 **401**。

```bash
curl -i http://localhost:8080/api/orders/1
```

**运行结果（文字描述）**：`401 Unauthorized`（无 token 时）。

### 3.3 完整代码清单与仓库

`chapter18-security`。

### 3.4 测试验证

- `@WithMockUser` + `MockMvc`：断言 **200** 与 **403** 场景。  
- `oauth2ResourceServer` 可用 **`@MockBean` JwtDecoder** 做单元测试。

**命令**：`mvn -q test`。

**可能遇到的坑**

| 现象 | 原因 | 处理 |
|------|------|------|
| 所有请求 403 | CSRF / 规则顺序 | 明确 permitAll 与 matcher |
| CORS 预检失败 | OPTIONS 未放行 | CORS 配置与 Security 对齐 |

---

## 4 项目总结

### 常见踩坑经验

1. **CORS** 预检请求被拦截。  
2. **Actuator** 暴露过多端点。  
3. **密码** 明文存储。

---

## 思考题

1. `/actuator/prometheus` 暴露风险？（第 16 章。）  
2. **RED** 指标与 **USE** 指标区别？（第 16 章。）

---

## 推广协作提示

| 角色 | 建议 |
|------|------|
| **测试** | 覆盖匿名/已认证/无权限三类用例。 |

**下一章预告**：Actuator、Health、Metrics、Prometheus。
