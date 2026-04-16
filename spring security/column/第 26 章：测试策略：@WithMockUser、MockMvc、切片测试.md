# 第 26 章：测试策略：@WithMockUser、MockMvc、切片测试

> 本章对齐 [docs/template.md](../template.md)，建议字数 3000–5000。

---

## 1 项目背景（约 500 字）

### 业务场景

CI 需 **回归权限规则**；团队希望 **分层测试**：`@WebMvcTest` 快速反馈，**集成测试** 覆盖 **真实 `UserDetailsService`** 与 **SpEL**。

### 痛点放大

`@WithMockUser` **不经过** 真实登录；与 **`@WithUserDetails`**、**JWT `jwt()`** 的适用边界不清会导致 **假绿**。

### 流程图

```mermaid
flowchart LR
  Slice[@WebMvcTest]
  MockMvc[MockMvc]
  Slice --> MockMvc
```

---

## 2 项目设计：剧本式交锋对话（约 1200 字）

**场景**：为什么「测试全绿」仍线上越权？

**小胖**

「`@WithMockUser` 万能吗？」

**小白**

「它加载的是真实权限还是假的？」

**大师**

「**默认** 是 **合成 `UserDetails`**，**不跑数据库**；能测 **Controller + Security 配置** 的组合，但 **测不了** `UserDetailsService` SQL 错误。」

**技术映射**：`@WithMockUser` vs `@WithUserDetails`。

**小白**

「`@WebMvcTest` 为何有时起不来 Security？」

**大师**

「需 **`@Import(SecurityConfig.class)`** 或 **`@AutoConfigureMockMvc(addFilters = false)`**（慎用）等；**切片** 要自己 **装配依赖**。」

**技术映射**：`@Import`；测试 **最小上下文**。

**小胖**

「Resource Server JWT 怎么测？」

**大师**

「`SecurityMockMvcRequestPostProcessors.jwt()` **构造 JWT**；或 **@MockBean JwtDecoder**（隔离单元）。」

**技术映射**：`jwt()`；`MockJwt`（以版本 API 为准）。

**小白**

「E2E 还要 Playwright 吗？」

**大师**

「**契约** 分层：单元/切片 **快**；E2E **少而关键**（登录、主路径、权限拒绝）。」

---

## 3 项目实战（约 1500–2000 字）

### 步骤 1：`@WebMvcTest` + `@WithMockUser`

```java
@WebMvcTest(OrderController.class)
@Import(SecurityConfig.class)
class OrderControllerTest {
  @Autowired MockMvc mvc;

  @Test
  @WithMockUser(roles = "USER")
  void ok() throws Exception {
    mvc.perform(get("/orders/1")).andExpect(status().isOk());
  }
}
```

### 步骤 2：CSRF

```java
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.csrf;

mvc.perform(post("/orders").with(csrf())).andExpect(status().isOk());
```

### 步骤 3：JWT

```java
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.jwt;

mvc.perform(get("/api/me").with(jwt().authorities(() -> new SimpleGrantedAuthority("SCOPE_read"))))
    .andExpect(status().isOk());
```

### 步骤 4：集成测试 `@SpringBootTest`

覆盖 **真实用户加载** 与 **方法安全**。

### 步骤 5：测试数据

`@Sql` 或 Testcontainers 初始化用户/角色。

### 截图说明（供插图或评审时对照）

| 编号 | 建议截图内容 | 预期画面（文字描述） |
|------|----------------|----------------------|
| 图 26-1 | IDE 测试树 | **绿**；分类 **slice / integration**。 |
| 图 26-2 | 失败用例详情 | **403** 断言清晰。 |
| 图 26-3 | CI 报告 | 覆盖率 **Security 包** 行覆盖（可选）。 |
| 图 26-4 | JaCoCo 报告 | Controller 与 **SecurityConfig** 覆盖情况。 |

### 可能遇到的坑

| 坑 | 处理 |
|----|------|
| Security 配置未导入 | `@Import` |
| CSRF POST 403 | `csrf()` |
| JWT 测试与生产密钥不一致 | 使用测试 `JwtDecoder` |

---

## 4 项目总结（约 500–800 字）

### 思考题

1. `@WithSecurityContext` 自定义工厂？
2. WebFlux `WebTestClient` **等价** 写法？

### 推广计划提示

- **质量**：PR **必须** 含权限相关测试变更。

---

*本章完。*
