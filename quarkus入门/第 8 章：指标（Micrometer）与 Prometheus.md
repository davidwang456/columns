# 第 8 章：指标（Micrometer）与 Prometheus

## 0. 课程卡片

| 项目 | 说明 |
|------|------|
| **建议课时** | 50～60 分钟 |
| **学习目标** | 启用 Prometheus 导出；编写自定义指标；理解 **高基数 label** 风险 |
| **先修** | 第 2、7 章 |

---

## 1. 项目背景

**SLO**（可用性、延迟分位、错误率）依赖指标。Prometheus 通过拉取 `/q/metrics`（默认路径以文档为准）采集时间序列。本章完成：**依赖配置 + 业务计数器 + ServiceMonitor 可选清单**。

---

## 2. 项目设计：大师与小白的对话

**小白**：「有日志为什么还要 metrics？」

**大师**：「日志回答『这次发生了什么』；指标回答『**整体**多快、多稳、多忙』。告警与 HPA 往往基于指标。」

**运维**：「label 爆炸我们吃过亏。」

**大师**：「**永远不要**把 `userId`、`orderId` 做成 label。用直方图汇总延迟即可。」

**测试**：「压测报告里的 P99 怎么和 metrics 对齐？」

**大师**：「同一套 histogram 配置 + 同一压测场景；注意 **JVM 预热**。」

**架构师**：「业务指标命名谁定？」

**大师**：「平台出**命名规范**：前缀、单位、`_total` 后缀等。」

---

## 3. 知识要点

- `quarkus-micrometer-registry-prometheus`  
- 使用 `MeterRegistry` 注册 `counter` / `timer` / `gauge`  
- `ServiceMonitor` 仅在使用 Prometheus Operator 时需要

---

## 4. 项目实战

### 4.1 `pom.xml`

```xml
<dependency>
  <groupId>io.quarkus</groupId>
  <artifactId>quarkus-micrometer-registry-prometheus</artifactId>
</dependency>
<dependency>
  <groupId>io.quarkus</groupId>
  <artifactId>quarkus-rest</artifactId>
</dependency>
```

### 4.2 `application.properties`

```properties
quarkus.micrometer.export.prometheus.enabled=true
quarkus.micrometer.binder.http-server.enabled=true
```

### 4.3 业务指标

`src/main/java/org/acme/OrderMetrics.java`：

```java
package org.acme;

import io.micrometer.core.instrument.MeterRegistry;
import jakarta.enterprise.context.ApplicationScoped;
import jakarta.inject.Inject;

@ApplicationScoped
public class OrderMetrics {

    private final MeterRegistry registry;

    @Inject
    public OrderMetrics(MeterRegistry registry) {
        this.registry = registry;
    }

    public void orderPlaced() {
        registry.counter("acme_orders_placed_total", "channel", "web").increment();
    }
}
```

`src/main/java/org/acme/DemoResource.java`：

```java
package org.acme;

import jakarta.inject.Inject;
import jakarta.ws.rs.POST;
import jakarta.ws.rs.Path;

@Path("/orders")
public class DemoResource {

    @Inject
    OrderMetrics metrics;

    @POST
    public String place() {
        metrics.orderPlaced();
        return "accepted";
    }
}
```

### 4.4 Kubernetes：`Service` + `ServiceMonitor`（Prometheus Operator）

`k8s/metrics-service.yaml`：

```yaml
apiVersion: v1
kind: Service
metadata:
  name: metrics-lab
  labels:
    app: metrics-lab
spec:
  selector:
    app: metrics-lab
  ports:
    - name: http
      port: 8080
      targetPort: 8080
```

`k8s/servicemonitor.yaml`（若集群已装 kube-prometheus-stack）：

```yaml
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: metrics-lab
  labels:
    release: prometheus
spec:
  selector:
    matchLabels:
      app: metrics-lab
  endpoints:
    - port: http
      path: /q/metrics
      interval: 30s
```

配套 `Deployment` 可复用第 7 章模板，替换镜像名与 label。

---

## 5. 课堂实验

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | `quarkus:dev`，`curl -s localhost:8080/q/metrics | head` | 出现 Prometheus 文本格式 |
| 2 | `curl -X POST localhost:8080/orders`，再 grep `acme_orders_placed_total` | counter 递增 |
| 3 | （讨论）若给 counter 加 `user` label 会怎样？ | 理解基数爆炸 |
| 4 | （可选）在测试集群 apply ServiceMonitor，Prometheus UI 中查询 | 端到端验证 |

---

## 6. 项目总结

| 维度 | 说明 |
|------|------|
| **优点** | 生态成熟；与 HTTP 指标绑定简单。 |
| **缺点** | 指标治理差会拖垮 Prometheus。 |
| **适用场景** | 生产 SLO、容量、告警。 |
| **注意事项** | 命名规范；histogram buckets。 |
| **常见踩坑** | 高基数 label；生产暴露未鉴权 metrics。 |

**延伸阅读**：<https://quarkus.io/guides/telemetry-micrometer>
