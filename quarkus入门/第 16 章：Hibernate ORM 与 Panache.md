# 第 16 章：Hibernate ORM 与 Panache

## 0. 课程卡片

| 项目 | 说明 |
|------|------|
| **建议课时** | 75～90 分钟（含数据库） |
| **学习目标** | 配置 DataSource；编写 Panache 实体；理解事务与 N+1 |
| **先修** | 第 3 章；PostgreSQL 或 Dev Services |

---

## 1. 项目背景

业务系统多数需要 **ORM**。Panache 压缩样板代码，但 **JPA 陷阱**（懒加载、N+1、事务边界）仍在。K8s 多副本下 **连接池 × 副本数** 必须纳入容量规划。

---

## 2. 项目设计：大师与小白的对话

**小白**：「Panache 是不是不用写 Repository？」

**大师**：「常见 CRUD 更短；复杂查询仍要 JPQL/SQL。」

**运维**：「20 个 Pod × 10 连接 = 200，DB 上限多少？」

**大师**：「上线前**算公式**，HPA maxReplicas 要进计算。」

**测试**：「集成测试数据库哪来？」

**大师**：**Dev Services** 或 **Testcontainers**——第 11、31 章衔接。」

**架构师**：「读写分离怎么做？」

**大师**：「多数据源或中间层；超出本章，给延伸阅读。」

---

## 3. 知识要点

- `quarkus-hibernate-orm-panache` + JDBC 驱动  
- `@Transactional`  
- `quarkus.datasource.*`

---

## 4. 项目实战

### 4.1 `pom.xml`

```xml
<dependency>
  <groupId>io.quarkus</groupId>
  <artifactId>quarkus-hibernate-orm-panache</artifactId>
</dependency>
<dependency>
  <groupId>io.quarkus</groupId>
  <artifactId>quarkus-jdbc-postgresql</artifactId>
</dependency>
<dependency>
  <groupId>io.quarkus</groupId>
  <artifactId>quarkus-rest-jackson</artifactId>
</dependency>
```

### 4.2 `application.properties`

```properties
quarkus.datasource.db-kind=postgresql
quarkus.datasource.username=quarkus
quarkus.datasource.password=quarkus
quarkus.datasource.jdbc.url=jdbc:postgresql://localhost:5432/quarkus
quarkus.hibernate-orm.database.generation=drop-and-create
quarkus.datasource.jdbc.max-size=8
```

### 4.3 实体与资源

`src/main/java/org/acme/Book.java`：

```java
package org.acme;

import io.quarkus.hibernate.orm.panache.PanacheEntity;
import jakarta.persistence.Entity;

@Entity
public class Book extends PanacheEntity {
    public String title;
}
```

`src/main/java/org/acme/BookResource.java`：

```java
package org.acme;

import jakarta.transaction.Transactional;
import jakarta.ws.rs.*;
import jakarta.ws.rs.core.MediaType;
import java.util.List;

@Path("/books")
@Produces(MediaType.APPLICATION_JSON)
@Consumes(MediaType.APPLICATION_JSON)
public class BookResource {

    @GET
    public List<Book> list() {
        return Book.listAll();
    }

    @POST
    @Transactional
    public Book create(Book b) {
        b.persist();
        return b;
    }
}
```

### 4.4 Kubernetes：`Secret` + `Deployment` 注入数据库

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: db-secret
type: Opaque
stringData:
  QUARKUS_DATASOURCE_USERNAME: "app"
  QUARKUS_DATASOURCE_PASSWORD: "supersecret"
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: orm-lab
spec:
  template:
    spec:
      containers:
        - name: app
          image: registry.example.com/acme/orm-lab:1.0.0
          envFrom:
            - secretRef:
                name: db-secret
          env:
            - name: QUARKUS_DATASOURCE_JDBC_URL
              value: "jdbc:postgresql://postgres.default.svc:5432/app"
```

---

## 5. 课堂实验

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 启动 PostgreSQL（Docker：`docker run -e POSTGRES_PASSWORD=quarkus -p 5432:5432 postgres:16`） | 端口可连 |
| 2 | `./mvnw quarkus:dev`，`POST /books` JSON `{"title":"Guide"}` | 201 或返回带 id |
| 3 | `GET /books` | 列表含刚插入 |
| 4 | `EXPLAIN` 或打开 SQL 日志（`quarkus.hibernate-orm.log.sql=true`）观察语句 | 理解 N+1 风险 |
| 5 | （讨论）HPA max=50 时连接数上限 | 数值演算 |

---

## 6. 项目总结

| 维度 | 说明 |
|------|------|
| **优点** | 开发效率高；生态成熟。 |
| **缺点** | JPA 陷阱；Native 需额外关注。 |
| **适用场景** | OLTP CRUD。 |
| **注意事项** | 连接池；事务边界。 |
| **常见踩坑** | LazyInitialization；大事务。 |

**延伸阅读**：<https://quarkus.io/guides/hibernate-orm-panache>
