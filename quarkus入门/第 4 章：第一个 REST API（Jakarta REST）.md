# 第 4 章：第一个 REST API（Jakarta REST）

## 0. 课程卡片

| 项目 | 说明 |
|------|------|
| **建议课时** | 45～60 分钟 |
| **学习目标** | 编写标准 **Jakarta REST** 资源；理解 `@Path`/`@GET`/媒体类型；完成带测试的 API |
| **先修** | 第 1～2 章 |

---

## 1. 项目背景

HTTP API 是微服务与 BFF 最常见的边界。采用 **Jakarta REST（原 JAX-RS）** 有利于：

- 与行业书籍、培训材料对齐；
- 与 OpenAPI、安全注解等扩展协同；
- 减少「框架私有 Controller」带来的供应商锁定感。

本章交付：**资源类 + JSON DTO + 基础测试**。

---

## 2. 项目设计：大师与小白的对话

**小白**：「我用 Spring 的 `@GetMapping` 习惯了，Jakarta REST 别扭吗？」

**大师**：「概念映射很直接：`@Path` + HTTP 方法注解。别扭往往来自**包名从 javax 迁到 jakarta**，复制旧代码时要改 import。」

**测试**：「我们怎么约定 URL 前缀？」

**大师**：「建议统一 **`/api/v1`**，网关与契约测试都省事。别让每个服务自创一套。」

**运维**：「需要暴露管理端口分离吗？」

**大师**：「视安全基线而定。Quarkus 常用 `/q/*` 作开发与健康；生产应对外网关做路径控制。」

**小白**：「`@Produces` 不写行不行？」

**大师**：「有时能推断，但**显式声明**减少歧义，尤其多内容协商时。」

**架构师**：「DTO 和实体要不要分开？」

**大师**：「对外契约用 DTO，避免 ORM 实体直接泄露字段演进风险——数据访问章再展开。」

---

## 3. 知识要点

- 资源类**不必**继承框架基类；POJO + 注解即可。  
- JSON 需 `quarkus-rest-jackson` 或 `quarkus-rest-jsonb`。  
- 返回 `Response` 可精细控制状态码与头。

---

## 4. 项目实战

### 4.1 `pom.xml`（REST + Jackson）

```xml
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 https://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>
  <groupId>org.acme</groupId>
  <artifactId>rest-lab</artifactId>
  <version>1.0.0-SNAPSHOT</version>
  <properties>
    <maven.compiler.release>17</maven.compiler.release>
    <quarkus.platform.version>3.19.2</quarkus.platform.version>
  </properties>
  <dependencyManagement>
    <dependencies>
      <dependency>
        <groupId>io.quarkus.platform</groupId>
        <artifactId>quarkus-bom</artifactId>
        <version>${quarkus.platform.version}</version>
        <type>pom</type>
        <scope>import</scope>
      </dependency>
    </dependencies>
  </dependencyManagement>
  <dependencies>
    <dependency>
      <groupId>io.quarkus</groupId>
      <artifactId>quarkus-rest</artifactId>
    </dependency>
    <dependency>
      <groupId>io.quarkus</groupId>
      <artifactId>quarkus-rest-jackson</artifactId>
    </dependency>
    <dependency>
      <groupId>io.quarkus</groupId>
      <artifactId>quarkus-junit5</artifactId>
      <scope>test</scope>
    </dependency>
    <dependency>
      <groupId>io.rest-assured</groupId>
      <artifactId>rest-assured</artifactId>
      <scope>test</scope>
    </dependency>
  </dependencies>
  <build>
    <plugins>
      <plugin>
        <groupId>io.quarkus.platform</groupId>
        <artifactId>quarkus-maven-plugin</artifactId>
        <version>${quarkus.platform.version}</version>
        <extensions>true</extensions>
      </plugin>
    </plugins>
  </build>
</project>
```

### 4.2 资源与 DTO

`src/main/java/org/acme/api/v1/ItemDto.java`：

```java
package org.acme.api.v1;

public record ItemDto(String id, String name) {}
```

`src/main/java/org/acme/api/v1/ItemResource.java`：

```java
package org.acme.api.v1;

import jakarta.ws.rs.GET;
import jakarta.ws.rs.Path;
import jakarta.ws.rs.PathParam;
import jakarta.ws.rs.Produces;
import jakarta.ws.rs.core.MediaType;
import jakarta.ws.rs.core.Response;

@Path("/api/v1/items")
public class ItemResource {

    @GET
    @Path("/{id}")
    @Produces(MediaType.APPLICATION_JSON)
    public Response get(@PathParam("id") String id) {
        if ("404".equals(id)) {
            return Response.status(Response.Status.NOT_FOUND).build();
        }
        return Response.ok(new ItemDto(id, "demo-" + id)).build();
    }
}
```

### 4.3 测试

`src/test/java/org/acme/api/v1/ItemResourceTest.java`：

```java
package org.acme.api.v1;

import io.quarkus.test.junit.QuarkusTest;
import org.junit.jupiter.api.Test;

import static io.restassured.RestAssured.given;
import static org.hamcrest.CoreMatchers.is;

@QuarkusTest
class ItemResourceTest {

    @Test
    void ok() {
        given()
            .when().get("/api/v1/items/1")
            .then()
            .statusCode(200)
            .body("id", is("1"))
            .body("name", is("demo-1"));
    }

    @Test
    void notFound() {
        given()
            .when().get("/api/v1/items/404")
            .then()
            .statusCode(404);
    }
}
```

---

## 5. 课堂实验

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 创建工程并粘贴上述类 | `./mvnw test` 绿 |
| 2 | `quarkus:dev`，用浏览器访问 `/api/v1/items/1` | JSON 正常 |
| 3 | 新增 `POST` 创建接口，body 为 JSON | 学员互测 201 + Location 头（可选） |
| 4 | （讨论）若部署在 Ingress 后，路径是否保留 `/api/v1` | 统一前缀策略 |

**验收**：全员 `mvn test` 通过；至少一组完成 POST。

---

## 6. 项目总结

| 维度 | 说明 |
|------|------|
| **优点** | 标准、可测试、与 OpenAPI 衔接顺。 |
| **缺点** | 需注意 jakarta 包名；错误处理要统一。 |
| **适用场景** | 对外 API、BFF、内部 HTTP。 |
| **注意事项** | 版本化 URL；生产关闭调试接口。 |
| **常见踩坑** | Native 反射；媒体类型省略导致 406。 |

**延伸阅读**：<https://quarkus.io/guides/rest>
