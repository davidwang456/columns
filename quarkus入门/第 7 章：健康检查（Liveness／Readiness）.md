# 第 7 章：健康检查（Liveness／Readiness）

## 0. 课程卡片

| 项目 | 说明 |
|------|------|
| **建议课时** | 50～60 分钟 |
| **学习目标** | 配置 SmallRye Health；编写自定义检查；写出与 Quarkus 路径一致的 **K8s 探针** |
| **先修** | 第 1、3 章 |

---

## 1. 项目背景

Kubernetes 用探针决定：**是否重启容器**（liveness）、**是否接收流量**（readiness）。语义错误会导致：

- 无限重启（liveness 过严）；
- 流量打到未就绪实例（readiness 过松或路径错误）。

Quarkus 通过 **`quarkus-smallrye-health`** 暴露标准端点，通常位于 `/q/health` 下。

---

## 2. 项目设计：大师与小白的对话

**小白**：「一个 `/health` 返回 200 不够吗？」

**大师**：「不够表达**两种语义**。活着 ≠ 能接流量（依赖可能还没好）。」

**运维**：「我们 rolling update 总卡住。」

**大师**：「先看 **readiness** 是否永远失败：数据库连不上却写进了 readiness，就会一直 NotReady。」

**测试**：「我能在集成测试里调健康接口吗？」

**大师**：「可以，`@QuarkusTest` 下 GET `/q/health/ready` 应稳定 200（除非故意模拟失败）。」

**架构师**：「下游慢，要不要放进 liveness？」

**大师**：「**不要**。liveness 应轻量；重依赖放 readiness 或 startup（K8s 1.16+）。」

**小白**：「startup 探针和 readiness 区别？」

**大师**：「**startup** 解决慢启动被 liveness 误杀；只在启动阶段用。」

---

## 3. 知识要点

- `/q/health/live`：liveness  
- `/q/health/ready`：readiness  
- `/q/health/started`：startup（若启用）  
- 自定义检查：实现 `HealthCheck` 接口并使用 `@Readiness` / `@Liveness`

---

## 4. 项目实战

### 4.1 `pom.xml`

```xml
<dependency>
  <groupId>io.quarkus</groupId>
  <artifactId>quarkus-smallrye-health</artifactId>
</dependency>
<dependency>
  <groupId>io.quarkus</groupId>
  <artifactId>quarkus-rest</artifactId>
</dependency>
```

### 4.2 `application.properties`

```properties
quarkus.smallrye-health.root-path=/q/health
```

### 4.3 自定义 Readiness（示例）

`src/main/java/org/acme/health/CustomReadinessCheck.java`：

```java
package org.acme.health;

import jakarta.enterprise.context.ApplicationScoped;
import org.eclipse.microprofile.health.HealthCheck;
import org.eclipse.microprofile.health.HealthCheckResponse;
import org.eclipse.microprofile.health.Readiness;

@Readiness
@ApplicationScoped
public class CustomReadinessCheck implements HealthCheck {

    @Override
    public HealthCheckResponse call() {
        boolean ok = true; // 课堂：替换为真实依赖探测
        return ok
            ? HealthCheckResponse.up("custom-ready")
            : HealthCheckResponse.down("custom-ready");
    }
}
```

### 4.4 完整 Kubernetes `Deployment`（含三种探针）

`k8s/health-deployment.yaml`：

```yaml
apiVersion: v1
kind: Service
metadata:
  name: health-lab
spec:
  selector:
    app: health-lab
  ports:
    - name: http
      port: 80
      targetPort: 8080
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: health-lab
spec:
  replicas: 1
  selector:
    matchLabels:
      app: health-lab
  template:
    metadata:
      labels:
        app: health-lab
    spec:
      containers:
        - name: app
          image: registry.example.com/acme/health-lab:1.0.0
          imagePullPolicy: IfNotPresent
          ports:
            - name: http
              containerPort: 8080
          startupProbe:
            httpGet:
              path: /q/health/started
              port: http
            failureThreshold: 30
            periodSeconds: 5
          readinessProbe:
            httpGet:
              path: /q/health/ready
              port: http
            initialDelaySeconds: 5
            periodSeconds: 10
            timeoutSeconds: 2
            failureThreshold: 3
          livenessProbe:
            httpGet:
              path: /q/health/live
              port: http
            initialDelaySeconds: 20
            periodSeconds: 20
            timeoutSeconds: 2
            failureThreshold: 3
          resources:
            requests:
              cpu: "100m"
              memory: "128Mi"
            limits:
              cpu: "500m"
              memory: "256Mi"
```

> **说明**：若应用未显式启用 `started` 端点，可将 `startupProbe` 临时指向 `ready` 或关闭 startup，以集群与 Quarkus 版本为准。

---

## 5. 课堂实验

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | `./mvnw quarkus:dev`，`curl -i http://localhost:8080/q/health/ready` | HTTP 200，JSON UP |
| 2 | 临时将自定义检查改为 `down()`，重启 | readiness 返回 503 或 DOWN（观察行为） |
| 3 | `kubectl apply -f k8s/health-deployment.yaml`（讲师预构建镜像或本地 kind） | `kubectl describe pod` 中探针状态正常 |
| 4 | 故意改错 `path`，观察 `kubectl get pods` 与 `kubectl describe pod` 事件 | 看到 Unhealthy / Back-off |

**清理**：`kubectl delete deploy,svc health-lab --ignore-not-found`。

---

## 6. 项目总结

| 维度 | 说明 |
|------|------|
| **优点** | 标准、与 K8s 语义对齐。 |
| **缺点** | 自定义检查设计不当会误杀或永不就绪。 |
| **适用场景** | 所有集群工作负载。 |
| **注意事项** | startup 与慢启动；探针超时与重试。 |
| **常见踩坑** | 路径/端口错误；重 IO 放进 liveness。 |

**延伸阅读**：<https://quarkus.io/guides/smallrye-health>
