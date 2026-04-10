# 第 13 章：REST Client（类型安全调用下游）

## 0. 课程卡片

| 项目 | 说明 |
|------|------|
| **建议课时** | 60 分钟 |
| **学习目标** | 使用 `@RegisterRestClient`；配置超时；WireMock 或 mock 下游 |
| **先修** | 第 3、4 章 |

---

## 1. 项目背景

微服务需要调用下游 HTTP。**类型化客户端**减少字符串 URL 散落，配置可外置到 `application.properties` 与 ConfigMap。本章给出完整 **pom + 接口 + 配置 + 可选 K8s ConfigMap**。

---

## 2. 项目设计：大师与小白的对话

**小白**：「我用 `HttpClient` 一行行拼 Header。」

**大师**：「三人以上团队就会复制出三种错误处理。接口 + 配置才是**可测试边界**。」

**运维**：「下游域名在 staging/prod 不同。」

**大师**：「`quarkus.rest-client.*.url` 用 env 覆盖，与第 3 章一致。」

**测试**：「下游没就绪怎么测？」

**大师**：**WireMock**、`@InjectMock`（若用 Mockito）、或启动 test 专用 stub。」

**架构师**：「同步阻塞客户端在高并发下会怎样？」

**大师**：「线程池耗尽、延迟传递；必要时换响应式客户端或隔离舱（见故障容忍扩展）。」

---

## 3. 知识要点

- `quarkus-rest-client` 或 `quarkus-rest-client-jackson`  
- `configKey` 与 properties 前缀对应  
- 超时：`connect-timeout`、`read-timeout`

---

## 4. 项目实战

### 4.1 `pom.xml`

```xml
<dependency>
  <groupId>io.quarkus</groupId>
  <artifactId>quarkus-rest-client-jackson</artifactId>
</dependency>
<dependency>
  <groupId>io.quarkus</groupId>
  <artifactId>quarkus-rest</artifactId>
</dependency>
```

### 4.2 客户端接口

`src/main/java/org/acme/client/DownstreamApi.java`：

```java
package org.acme.client;

import jakarta.ws.rs.GET;
import jakarta.ws.rs.Path;
import org.eclipse.microprofile.rest.client.inject.RegisterRestClient;

@RegisterRestClient(configKey = "downstream-api")
public interface DownstreamApi {

    @GET
    @Path("/greeting")
    String greeting();
}
```

### 4.3 配置 `application.properties`

```properties
quarkus.rest-client.downstream-api.url=http://localhost:9000
quarkus.rest-client.downstream-api.connect-timeout=2000
quarkus.rest-client.downstream-api.read-timeout=5000
```

### 4.4 门面 Bean

`src/main/java/org/acme/GatewayService.java`：

```java
package org.acme;

import jakarta.enterprise.context.ApplicationScoped;
import jakarta.inject.Inject;
import org.acme.client.DownstreamApi;
import org.eclipse.microprofile.rest.client.inject.RestClient;

@ApplicationScoped
public class GatewayService {

    private final DownstreamApi downstream;

    @Inject
    public GatewayService(@RestClient DownstreamApi downstream) {
        this.downstream = downstream;
    }

    public String load() {
        return downstream.greeting();
    }
}
```

### 4.5 Kubernetes：`ConfigMap` 覆盖 URL

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: gateway-config
data:
  QUARKUS_REST_CLIENT_DOWNSTREAM_API_URL: "http://pricing.prod.svc.cluster.local:8080"
```

---

## 5. 课堂实验

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 本地用 `python -m http.server` 或另一个 Quarkus 起 stub 在 9000 | 返回固定字符串 |
| 2 | 调用暴露 `GatewayService` 的 Resource | 端到端字符串返回 |
| 3 | 停掉下游，观察超时异常 | 理解超时配置 |
| 4 | （选）加 `quarkus-smallrye-fault-tolerance` 重试 | 体验韧性 |

---

## 6. 项目总结

| 维度 | 说明 |
|------|------|
| **优点** | 类型安全；可 mock；配置外置。 |
| **缺点** | 阻塞模型需注意线程；错误需统一映射。 |
| **适用场景** | 同步编排、BFF。 |
| **注意事项** | 超时默认值务必评审。 |
| **常见踩坑** | event loop 上阻塞；URL 未分环境。 |

**延伸阅读**：<https://quarkus.io/guides/rest-client>
