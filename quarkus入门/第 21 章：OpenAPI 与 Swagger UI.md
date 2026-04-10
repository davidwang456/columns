# 第 21 章：OpenAPI 与 Swagger UI

## 0. 课程卡片

| 项目 | 说明 |
|------|------|
| **建议课时** | 45 分钟 |
| **学习目标** | 生成 OpenAPI；配置 Swagger UI；生产关闭 UI |
| **先修** | 第 4 章 |

---

## 1. 项目背景

**契约驱动**协作需要单一真相。OpenAPI 描述 HTTP API，可对接 **Mock、契约测试、网关**。生产环境应限制 **Swagger UI** 暴露。

---

## 2. 项目设计：大师与小白的对话

**测试**：「接口变了文档没变，谁负责？」

**大师**：「CI 上做 **OpenAPI diff** 或从代码生成后校验。」

**运维**：「生产 `/q/swagger-ui` 对外开放？」

**大师**：「默认**关**；或仅 VPN 内网。」

**前端**：「能导出 `openapi.yaml` 吗？」

**大师**：「`/q/openapi`（路径以版本为准）或构建时生成。」

---

## 3. 知识要点

- `quarkus-smallrye-openapi`  
- `quarkus.swagger-ui.*`  
- `@Tag` / `@Operation` 注解增强文档

---

## 4. 项目实战

### 4.1 `pom.xml`

```xml
<dependency>
  <groupId>io.quarkus</groupId>
  <artifactId>quarkus-smallrye-openapi</artifactId>
</dependency>
<dependency>
  <groupId>io.quarkus</groupId>
  <artifactId>quarkus-swagger-ui</artifactId>
</dependency>
<dependency>
  <groupId>io.quarkus</groupId>
  <artifactId>quarkus-rest-jackson</artifactId>
</dependency>
```

### 4.2 `application.properties`

```properties
%dev.quarkus.swagger-ui.always-include=true
%prod.quarkus.swagger-ui.always-include=false
mp.openapi.extensions.smallrye.operationIdStrategy=METHOD
```

### 4.3 注解示例

```java
package org.acme;

import jakarta.ws.rs.GET;
import jakarta.ws.rs.Path;
import org.eclipse.microprofile.openapi.annotations.Operation;
import org.eclipse.microprofile.openapi.annotations.tags.Tag;

@Path("/api/v1/demo")
@Tag(name = "Demo")
public class OpenApiDemoResource {

    @GET
    @Operation(summary = "Ping")
    public String ping() {
        return "pong";
    }
}
```

### 4.4 Kubernetes：`Ingress` 限制（NetworkPolicy 思路说明）

生产可对 Ingress 加 annotation 禁止 `/q/swagger-ui`，或使用 **NetworkPolicy** 仅允许运维网段（示意）：

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: deny-q-to-world
  namespace: prod
spec:
  podSelector:
    matchLabels:
      app: api
  policyTypes:
    - Ingress
  ingress:
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: ingress-nginx
      ports:
        - port: 8080
```

> 具体策略依 CNI 与平台；课堂以讨论为主。

---

## 5. 课堂实验

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | dev 模式打开 Swagger UI | 可见操作列表 |
| 2 | 下载 OpenAPI JSON/YAML | 文件可用于 Mock |
| 3 | `prod` profile 验证 UI 不可达 | 安全基线 |
| 4 | 给资源加 `@Tag`，刷新文档 | 分组出现 |

---

## 6. 项目总结

| 维度 | 说明 |
|------|------|
| **优点** | 联调效率高。 |
| **缺点** | 生产暴露风险。 |
| **适用场景** | 开发、联调、对外 SDK。 |
| **注意事项** | 版本化 URL。 |
| **常见踩坑** | DTO 漂移；生产误开 UI。 |

**延伸阅读**：<https://quarkus.io/guides/openapi-swaggerui>
