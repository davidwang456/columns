# 第 20 章：安全（OIDC／JWT）

## 0. 课程卡片

| 项目 | 说明 |
|------|------|
| **建议课时** | 75～90 分钟 |
| **学习目标** | 配置 `quarkus-oidc` Bearer 场景；保护 JAX-RS 资源；理解网关与应用的信任边界 |
| **先修** | 第 4 章；可选 Keycloak 容器 |

---

## 1. 项目背景

企业常用 **OIDC Provider**（Keycloak、Azure AD 等）。服务需校验 **JWT**（issuer、audience、时钟偏移）。K8s 上 Secret 存放 client secret，**勿**提交 Git。

---

## 2. 项目设计：大师与小白的对话

**小白**：「网关验过了，应用还验吗？」

**大师**：「取决于**威胁模型**；零信任下服务内仍可能校验。」

**运维**：「证书与 JWKS 轮换怎么做？」

**大师**：「监控 IdP 状态；应用缓存 JWKS 要有失败降级策略（查文档）。」

**测试**：「自动化怎么拿 token？」

**大师**：**password 或 client_credentials** 仅用于测试客户端；生产用授权码流。」

**安全**：「日志能打完整 JWT 吗？」

**大师**：「**不能**。」

---

## 3. 知识要点

- `quarkus.oidc.auth-server-url`  
- `@RolesAllowed`  
- `quarkus.oidc.application-type=service`（Bearer）

---

## 4. 项目实战

### 4.1 `pom.xml`

```xml
<dependency>
  <groupId>io.quarkus</groupId>
  <artifactId>quarkus-oidc</artifactId>
</dependency>
<dependency>
  <groupId>io.quarkus</groupId>
  <artifactId>quarkus-rest</artifactId>
</dependency>
```

### 4.2 `application.properties`

```properties
quarkus.oidc.auth-server-url=http://localhost:8180/realms/quarkus
quarkus.oidc.client-id=backend-service
quarkus.oidc.credentials.secret=change-me
quarkus.oidc.application-type=service
```

### 4.3 受保护资源

`src/main/java/org/acme/SecureResource.java`：

```java
package org.acme;

import jakarta.annotation.security.RolesAllowed;
import jakarta.ws.rs.GET;
import jakarta.ws.rs.Path;

@Path("/secure")
public class SecureResource {

    @GET
    @RolesAllowed("user")
    public String hello() {
        return "ok";
    }
}
```

### 4.4 Kubernetes：`Secret` + `Deployment`

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: oidc-secret
type: Opaque
stringData:
  QUARKUS_OIDC_CREDENTIALS_SECRET: "from-vault"
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: secure-app
spec:
  template:
    spec:
      containers:
        - name: app
          image: registry.example.com/acme/secure-app:1.0.0
          envFrom:
            - secretRef:
                name: oidc-secret
          env:
            - name: QUARKUS_OIDC_AUTH_SERVER_URL
              value: "https://idp.example.com/realms/prod"
```

---

## 5. 课堂实验

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | `docker run` Keycloak 或使用公司 IdP 沙箱 | realm/client 就绪 |
| 2 | 获取 access token（讲师脚本） | JWT 字符串 |
| 3 | `curl -H "Authorization: Bearer $TOKEN" localhost:8080/secure` | 200 |
| 4 | 无 token 或错误 audience | 401 |
| 5 | 讨论：Ingress OAuth2 Proxy vs 应用内 OIDC | 边界清晰 |

---

## 6. 项目总结

| 维度 | 说明 |
|------|------|
| **优点** | 标准协议；扩展成熟。 |
| **缺点** | 配置维度高。 |
| **适用场景** | SSO、服务间 JWT。 |
| **注意事项** | issuer/audience；secret 轮转。 |
| **常见踩坑** | issuer URL 错；日志泄露 token。 |

**延伸阅读**：<https://quarkus.io/guides/security-oidc-bearer-token-authentication>
