# 第 19 章：可观测性——Actuator 指标与健康检查

> **业务线**：电商 / 订单履约微服务（拟真场景）。本章可独立阅读；与全书案例弱关联。

## 上一章思考题回顾

1. **`/actuator/prometheus`**：若 **未鉴权** 暴露于公网，会泄露**内部指标**与拓扑信息 → 应 **网络隔离** 或 **加认证**、**限制端点**。  
2. **RED**（Rate、Errors、Duration）面向**请求**；**USE**（Utilization、Saturation、Errors）面向**资源**（CPU、磁盘）。

---

## 1 项目背景

大促时订单服务 **延迟抖动**，需 **健康检查** 做 K8s 探针，**指标**对接 Prometheus/Grafana。若只有日志，**滞后**且难聚合。

**痛点**：  
- **Liveness** 与 **Readiness** 混用导致**误杀 Pod**。  
- **指标基数爆炸**（高基数 label）。  
- **敏感信息** 进 `/env`。

---

## 2 项目设计（剧本式对话）

**角色**：小胖 / 小白 / 大师。  
**结构**：日志 vs 指标 → 探针语义 → 指标基数与成本。

**小胖**：我日志打满 `INFO`，不就够观测了吗？

**大师**：日志是**事后叙事**；指标是**可聚合的时序信号**。SRE 看 **RED**（请求率/错误率/延迟），不是翻日志。

**技术映射**：**Actuator** 暴露 **health/info/metrics**；**Micrometer** 作为门面适配 **Prometheus**。

**小白**：Liveness 和 Readiness 为啥要两个？

**大师**：**Liveness** 问「进程要不要重启」；**Readiness** 问「能不能接流量」。把下游 DB 挂掉放进 liveness，会导致**无限重启**。

**技术映射**：**Kubernetes probes** 与 Spring Boot 3.x **`management.endpoint.health.probes.enabled`**。

**小胖**：`/actuator/prometheus` 打开就能上 Grafana，是不是零成本？

**大师**：**暴露面**也是成本：拓扑、版本、业务指标可能泄露；高基数 label 还会**拖垮 TSDB**。

---

## 3 项目实战

### 3.1 环境准备

| 项 | 说明 |
|----|------|
| 依赖 | `actuator` + `micrometer-registry-prometheus` |
| 工具 | `curl` 或浏览器访问 `/actuator/prometheus` |

```xml
<dependency>
  <groupId>org.springframework.boot</groupId>
  <artifactId>spring-boot-starter-actuator</artifactId>
</dependency>
<dependency>
  <groupId>io.micrometer</groupId>
  <artifactId>micrometer-registry-prometheus</artifactId>
</dependency>
```

### 3.2 分步实现

**`application.yml`**

```yaml
management:
  endpoints:
    web:
      exposure:
        include: health,info,prometheus
  endpoint:
    health:
      probes:
        enabled: true
```

**步骤 1 — 目标**：实现 `HealthIndicator`（例如检查下游 WMS 是否可达，用 **超时**）。

**步骤 2 — 目标**：本地启动后访问：

```bash
curl -s http://localhost:8080/actuator/health | jq
curl -s http://localhost:8080/actuator/prometheus | head
```

**运行结果（文字描述）**：`health` 返回 `status` 与 `components`；`prometheus` 输出 **`HELP/TYPE`** 与样本行。

### 3.3 完整代码清单与仓库

`chapter19-actuator`。

### 3.4 测试验证

`@SpringBootTest` + `TestRestTemplate` GET `/actuator/health`；对 `prometheus` 端点做 **冒烟**（注意 Security 是否拦截）。

**命令**：`mvn -q test`。

**可能遇到的坑**

| 现象 | 原因 | 处理 |
|------|------|------|
| 指标缺失 | 未注册 MeterRegistry | 检查依赖与配置 |
| 健康检查过慢 | 同步下游 | 异步/超时/缓存 |

---

## 4 项目总结

### 常见踩坑经验

1. **生产** 开启全部 endpoints。  
2. **指标名** 与 Grafana 面板不匹配。  
3. **探针** 依赖下游导致**级联不健康**。

---

## 思考题

1. `@Scheduled` 默认单线程？（第 20 章。）  
2. 定时任务 **幂等键** 设计？（第 20 章。）

---

## 推广协作提示

| 角色 | 建议 |
|------|------|
| **运维** | 定义 SLO 与告警阈值。 |

**下一章预告**：`@Scheduled`、批处理、幂等与补偿。
