# 第 14 章：URL 授权 vs 方法授权选型

> 本章对齐 [docs/template.md](../template.md)，建议字数 3000–5000。

---

## 1 项目背景（约 500 字）

### 业务场景

订单中心既有 **REST 控制器**（`/orders/{id}`），又有 **Service** 被定时任务、消息消费者、批处理调用。产品规则：**用户只能查看自己的订单，管理员可看全部**。架构要求：**网关已做粗粒度鉴权**，应用内仍要防止 **水平越权**。

### 痛点放大

纯 URL 授权 **快、集中**，但无法表达 **「资源属主」**；纯方法授权 **细**，但配置分散、SpEL 复杂。需要 **分层策略**：`authorizeHttpRequests` 挡 **未登录/明显无角色**；`@PreAuthorize` 表达 **订单属主** 等业务不变量。

### 流程图

```mermaid
flowchart TB
  URL[authorizeHttpRequests]
  Method[@PreAuthorize]
  URL --> Method
```

---

## 2 项目设计：剧本式交锋对话（约 1200 字）

**场景**：是否删除所有 URL 规则，只留方法注解？

**小胖**

「都在 Controller 写 `hasRole` 不就完了？注解多好看。」

**小白**

「如果 `OrderService.cancel` 被消息队列调用，URL 规则管得着吗？`SecurityContext` 里还有用户吗？」

**大师**

「**URL 层**像大院门禁：挡 **未登录、明显角色不对**。**方法层**像办公室二次核验：即使 **非 HTTP 入口**，只要进入 Spring 管理 Bean 且 **启用方法安全**，AOP 仍会拦截。」

**技术映射**：`HttpSecurity` → Web 路径；`@EnableMethodSecurity` → 方法代理。

**小白**

「那 URL 层是不是可以全 `authenticated()`，细节全放方法？」

**大师**

「可以，但要评估 **静态资源与 actuator** 的暴露面；通常仍建议 URL 层 **denyAll 兜底 + 显式放行**。」

**技术映射**：**纵深防御**；非 Web 入口 **必须** 方法层或领域服务校验。

**小胖**

「性能：方法拦截每个调用都有 AOP 开销吧？」

**小白**

「订单热点 QPS 上万时呢？」

**大师**

「相对 DB 与网络 IO，AOP 通常可忽略；真要优化，**热点读**可走缓存授权结果或 **SQL 层谓词**（`WHERE owner_id=?`），但 **不能跳过授权思考**。」

**技术映射**：性能 vs 正确性；**对象级** 仍建议 **查询带租户/属主条件**。

**小白**

「`@RolesAllowed`（JSR-250）和 `@PreAuthorize` 怎么选？」

**大师**

「**SpEL** 强表达用 `@PreAuthorize`；简单角色且想 **标准注解** 可用 `@RolesAllowed`（需启用相应配置）。」

---

## 3 项目实战（约 1500–2000 字）

### 环境准备

- `spring-boot-starter-security`，`@EnableMethodSecurity`。
- 示例：`OrderController` + `OrderService`。

### 步骤 1：URL 层「粗」规则

```java
http.authorizeHttpRequests(a -> a
    .requestMatchers("/admin/**").hasRole("ADMIN")
    .requestMatchers("/orders/**").authenticated());
```

### 步骤 2：方法层「细」规则

```java
@Service
public class OrderService {
  @PreAuthorize("hasRole('ADMIN') or @orderAuth.canRead(#id)")
  public Order getOrder(Long id) { ... }
}
```

```java
@Component("orderAuth")
public class OrderAuthorization {
  public boolean canRead(Long orderId) {
    String name = SecurityContextHolder.getContext().getAuthentication().getName();
    return orderRepository.isOwner(orderId, name);
  }
}
```

### 步骤 3：非 Web 入口测试

```java
@SpringBootTest
class OrderServiceIT {
  @Test
  @WithMockUser("alice")
  void denyOthersOrder() {
    assertThatThrownBy(() -> orderService.getOrder(999L))
        .isInstanceOf(AccessDeniedException.class);
  }
}
```

### 步骤 4：自调用陷阱演示（负例）

同一类内 `this.getOrder()` **可能绕过代理**；正例：拆类或注入自身代理（谨慎）。

### 截图说明（供插图或评审时对照）

| 编号 | 建议截图内容 | 预期画面（文字描述） |
|------|----------------|----------------------|
| 图 14-1 | 架构白板 / Confluence | 「URL=粗、方法=细」分层示意图。 |
| 图 14-2 | IDEA AOP 切面 | `@PreAuthorize` 对应 Advisor 绑定到 `OrderService`。 |
| 图 14-3 | 测试失败栈 | `AccessDeniedException` 栈顶含 `MethodSecurityInterceptor`（类名以版本为准）。 |
| 图 14-4 | API 403 响应体 | JSON 含统一错误码（若自定义 `AccessDeniedHandler`）。 |

### 可能遇到的坑

| 坑 | 处理 |
|----|------|
| 同类自调用绕过代理 | 拆类或 `ApplicationContext` 获取代理 |
| SpEL 拼字符串注入 | 使用 `#id` 参数绑定，勿拼接用户输入进表达式 |
| 消息监听无 `SecurityContext` | 使用 `runAs` 或 **显式传业务身份** |

---

## 4 项目总结（约 500–800 字）

### 优点与缺点

| 维度 | 分层 URL+方法 | 仅 URL |
|------|----------------|--------|
| 覆盖非 Web 入口 | 好 | 差 |
| 配置复杂度 | 中 | 低 |

### 适用场景

- 多入口（HTTP、MQ、批处理）共享领域服务。

### 不适用场景

- 极简 CRUD 且无水平越权风险（仍建议 **属主 SQL**）。

### 思考题

1. 何时 **只用 URL** 就够？
2. `PermissionEvaluator` 与 `@orderAuth` Bean 的取舍？

### 推广计划提示

- **架构**：维护「入口 × 授权层」矩阵；评审每个新入口。

---

*本章完。*
