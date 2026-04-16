# 第 25 章：可观测性：日志、审计与 Actuator

> 本章对齐 [docs/template.md](../template.md)，建议字数 3000–5000。

---

## 1 项目背景（约 500 字）

### 业务场景

安全事件需 **可追溯**：谁在何时访问敏感接口；运维需 **指标**（认证失败率、限流触发）。要求 **TraceId** 贯穿网关与应用日志，**审计日志** 单独管道存储。

### 痛点放大

默认日志 **无主体**；**Actuator** 若暴露 **env/beans** 会成为 **攻击面**。需要 **分级暴露** 与 **强认证**。

### 流程图

```mermaid
flowchart TB
  Log[Logging]
  Audit[AuditEvent]
  Act[Actuator]
```

---

## 2 项目设计：剧本式交锋对话（约 1200 字）

**场景**：是否把 JWT 打日志里「方便排查」？

**小胖**

「打日志不就行了吗？`log.info("user=" + name)`？」

**小白**

「认证失败要不要记密码长度？IP 算不算 PII？」

**大师**

「**禁止**记录 **密码、refresh token、完整 JWT**；可记 **subject、sessionId 哈希、traceId**。」

**技术映射**：日志脱敏；**合规**要求。

**小白**

「`AuthenticationSuccessEvent` 里记审计？」

**大师**

「用 **`ApplicationListener`** 或 **Spring Boot Actuator Audit**（若启用）；**登出** 在 `LogoutHandler`。」

**技术映射**：事件驱动审计。

**小胖**

「`/actuator/prometheus` 要登录吗？」

**大师**

「**生产**通常 **网络隔离 + Basic/mTLS** 或 **仅内网**；**metrics** 与 **敏感端点** 分离。」

**技术映射**：`management.endpoints.web.exposure`；**最小暴露**。

**小白**

「SLO：认证错误率怎么定义？」

**大师**

「**5xx** 与 **401 业务拒绝** 分开；避免把 **正常密码错误** 当事故。」

---

## 3 项目实战（约 1500–2000 字）

### 步骤 1：MDC 注入用户

```java
@Component
public class UserMdcFilter extends OncePerRequestFilter {
  @Override
  protected void doFilterInternal(HttpServletRequest req, HttpServletResponse res, FilterChain chain)
      throws ServletException, IOException {
    try {
      SecurityContextHolder.getContext().getAuthentication();
      Optional.ofNullable(SecurityContextHolder.getContext().getAuthentication())
          .ifPresent(a -> MDC.put("user", a.getName()));
      chain.doFilter(req, res);
    } finally {
      MDC.remove("user");
    }
  }
}
```

### 步骤 2：限制 Actuator

```yaml
management.endpoints.web.exposure.include: health,info,prometheus
```

### 步骤 3：保护敏感端点

```java
http.authorizeHttpRequests(a -> a.requestMatchers("/actuator/**").hasRole("ACTUATOR_ADMIN"));
```

### 步骤 4：Prometheus 告警规则示例

`rate(http_server_requests_seconds_count{status="401"}[5m])` 突增 → 通知。

### 截图说明（供插图或评审时对照）

| 编号 | 建议截图内容 | 预期画面（文字描述） |
|------|----------------|----------------------|
| 图 25-1 | Kibana/Grafana 日志 | 字段含 **user** 与 **traceId**。 |
| 图 25-2 | 访问 `/actuator/env` | **401/403**（生产应不可达）。 |
| 图 25-3 | Prometheus targets | `up` 与 **scrape** 成功。 |
| 图 25-4 | 告警通知 | 认证失败率超阈值。 |

### 可能遇到的坑

| 坑 | 处理 |
|----|------|
| 日志 PII 合规 | 脱敏与保留期 |
| 健康检查被鉴权误伤 | `permitAll` `/actuator/health`（按需） |
| 指标基数爆炸 | 控制 label |

---

## 4 项目总结（约 500–800 字）

### 思考题

1. OpenTelemetry **span** 上应有哪些 Security 属性？
2. **审计日志** 与 **业务操作日志** 分库存储？

### 推广计划提示

- **SRE**：把 **认证类指标** 纳入 **SLO** 与 **错误预算**。

---

*本章完。*
