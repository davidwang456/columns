# 第 112 章：spring-boot-actuator —— 生产可观测与运维端点

> 对应模块：`spring-boot-actuator`。本章说明 Actuator 端点、`management.*` 命名空间、与安全的配合方式，并区分基础使用与生产加固。

---

## 1 项目背景

某金融科技公司的支付网关上线后，**夜间值班**需要快速判断：进程是否健康、JVM 与数据源是否异常、线程是否打满。若仅有应用日志，**排障链路长**（需登录机器 `jstack`、再查 DB 监控），且无法与 K8s 探针、Prometheus 告警统一。业务方还要求：**对外屏蔽**内部堆栈与配置细节，对内运维可见**聚合健康状态**与**自定义业务探针**（如「渠道清算连接」）。

没有 Actuator 时，团队往往自建 `/status` 接口，字段各自为政，**与 Micrometer 指标割裂**；升级 JDK 或 Spring Boot 后，健康语义又不一致。`spring-boot-actuator` 提供**标准化端点**（health、info、metrics、env 等），并与 Boot 自动配置、Security、Kubernetes **探针扩展**协同，使「可观测性」成为一等公民而非事后补丁。

```mermaid
flowchart LR
  probe[K8s探针] --> health[/actuator/health]
  prom[Prometheus] --> metrics[/actuator/prometheus]
  sre[SRE] --> env[/actuator/env]
```

痛点放大：**安全边界**若未收紧，`/actuator/env` 可能泄露密钥；**健康过于敏感**（下游超时即整体 DOWN）会导致滚动发布失败——需要 `management.endpoint.health.group.readiness` 等分组策略。

---

## 2 项目设计（剧本式交锋对话）

**小胖：** 健康检查不就是 ping 一下吗？为啥还要分 liveness、readiness，跟食堂开不开门有啥区别？

**大师：**  liveness 是「店还开不开」——进程是否卡死；readiness 是「能不能接单」——依赖是否就绪。K8s 用不同探针决定**重启还是摘流量**。  
**技术映射：** Spring Boot Health **分组**对应 K8s **探针语义**。

**小白：** 如果 `/actuator/health` 把数据库也算进去，DB 抖动一下，整个就绪就 false，会不会误杀？

**大师：** 这正是要配置 `group`、权重与**可选依赖**：readiness 只包含关键依赖；非关键可降级为 `CONTRIBUTOR` 级别或独立指标。  
**技术映射：** `HealthIndicator` 组合策略 + **分组** = 控制**爆炸半径**。

**小胖：** 那 metrics 和 tracing 呢？我们已经有 Prometheus 了，Actuator 还多此一举吗？

**大师：** Actuator 暴露的是**统一入口与发现**；Micrometer 注册指标，tracing 由 Micrometer Tracing/OTel 模块衔接（第 117–122 章）。Actuator 让你**少写胶水代码**。  
**技术映射：** Actuator **端点** + Micrometer **注册表** = 可插拔后端。

**小白：** 生产环境如何把敏感端点锁起来？

**大师：** 独立 `management.server.port` 或同端口下用 Spring Security **按路径授权**；禁用 `env`、`beans` 等高危端点或限制角色；配合网络策略。  
**技术映射：** **管理平面与数据平面分离**是安全基线。

---

## 3 项目实战

### 环境准备

```xml
<dependency>
  <groupId>org.springframework.boot</groupId>
  <artifactId>spring-boot-starter-actuator</artifactId>
</dependency>
<dependency>
  <groupId>org.springframework.boot</groupId>
  <artifactId>spring-boot-starter-web</artifactId>
</dependency>
```

可选：`micrometer-registry-prometheus` 以启用 `/actuator/prometheus`。

### 分步实现

**步骤 1 — 目标：** 最小 `application.yml` 暴露 health 与 info。

```yaml
management:
  endpoints:
    web:
      exposure:
        include: health,info,metrics
  endpoint:
    health:
      show-details: when_authorized
      probes:
        enabled: true   # K8s 探针友好（Boot 2.3+ / 3.x 语义以当前版本为准）
```

**运行结果（文字描述）：** 启动后 `GET /actuator/health` 返回 `{"status":"UP"}`；开启探针后可见 `livenessState`、`readinessState` 等分组信息（视版本配置）。

**坑：** `show-details: always` 在公网极其危险——仅限内网或认证后使用。

---

**步骤 2 — 目标：** 自定义 `HealthIndicator`（示例：外部清算 URL）。

```java
@Component
public class ClearingHouseHealthIndicator implements HealthIndicator {
  private final RestClient http = RestClient.create();

  @Override
  public Health health() {
    try {
      // 演示 HEAD 请求，生产应设短超时与熔断
      http.head().uri("https://clearing.example.com/ping").retrieve().toBodilessEntity();
      return Health.up().withDetail("clearing", "reachable").build();
    } catch (Exception ex) {
      return Health.down(ex).withDetail("clearing", "unreachable").build();
    }
  }
}
```

**坑：** 健康检查内**长超时**会拖慢整体响应——务必单独线程池或 Resilience4j 限制。

---

**步骤 3 — 目标：** `curl` 验证与 Prometheus 抓取（若引入 prometheus registry）。

```bash
curl -s http://localhost:8080/actuator/health
curl -s http://localhost:8080/actuator/metrics/jvm.memory.used
```

### 完整代码清单

`Application.java` + 上述配置 + 可选 `SecurityFilterChain` 限制 `/actuator/**`。示例仓库占位：`https://example.com/demo/actuator-gateway.git`。

### 测试验证

使用 `@SpringBootTest` + `MockMvc` 或 `WebTestClient` 请求 `/actuator/health`；或用 Testcontainers 模拟 K8s 探针路径。

```java
@SpringBootTest
@AutoConfigureMockMvc
class ActuatorIT {
  @Autowired MockMvc mvc;
  @Test
  void healthOk() throws Exception {
    mvc.perform(get("/actuator/health")).andExpect(status().isOk());
  }
}
```

---

## 4 项目总结

### 优点与缺点（对比自建状态接口）

| 维度 | Actuator | 自建 /health |
|------|----------|----------------|
| 标准化 | 高，生态对接多 | 低，各自为政 |
| 安全面 | 需正确配置暴露范围 | 依赖实现质量 |
| 学习曲线 | 需理解 management.* | 低但易碎片化 |

### 适用与不适用

- **适用：** 所有需上线运维与 SRE 的 Spring Boot 服务；Kubernetes 环境。
- **不适用：** 极简嵌入式且**严禁任何管理端口**的场景——应彻底禁用或剥离。

### 注意事项

- 生产禁用或保护 `env`、`configprops`、`heapdump` 等。
- 健康检查避免 N+1 外部调用；超时与熔断必备。
- 与 Spring Security、OAuth2 Resource Server 的整合顺序。

### 常见踩坑经验

1. **滚动发布失败：** readiness 将非关键依赖算入——根因是健康分组未裁剪。
2. **指标爆炸：** 高基数 tag——根因是未限制 `MeterFilter`。
3. **502 于网关：** 管理端口未在 Ingress 暴露——根因是网络与端口分层未对齐。

### 思考题

1. 生产暴露 `health`、`metrics` 时，如何与 Spring Security 协同避免匿名访问敏感端点？
2. Kubernetes 探针应优先用哪种 Actuator 端点或扩展，响应语义上要注意什么？

**参考答案：** [附录：思考题参考答案](../appendix/thinking-answers.md)「112 spring-boot-actuator」。

### 推广计划提示

- **开发：** 为关键外部系统编写 `HealthIndicator` 与文档化 SLA。
- **运维：** 接入 Prometheus/Grafana；配置探针与告警阈值。
- **测试：** 契约测试验证 `/actuator/health` JSON schema 与版本升级兼容性。

---

*结构对齐 [template.md](../template.md)。*
