# 第 29 章：gRPC 与 GraphQL（多协议选型）

## 0. 课程卡片

| 项目 | 说明 |
|------|------|
| **建议课时** | 90 分钟（二选一深入 + 另一概览） |
| **学习目标** | 理解 gRPC vs GraphQL 取舍；完成最小 proto 或 GraphQL API |
| **先修** | 第 4、14 章 |

---

## 1. 项目背景

- **gRPC**：二进制、强类型，适合集群内东西向流量。  
- **GraphQL**：聚合查询，BFF 常见；需 **复杂度限制、DataLoader**。

---

## 2. 项目设计：大师与小白的对话

**小白**：「统一 GraphQL？」

**大师**：「可以，但要付 **N+1、授权、缓存** 治理成本。」

**运维**：「gRPC 调试难？」

**大师**：**grpcurl**、良好 proto 版本策略、服务网格可观测。」

**测试**：「契约测怎么写？」

**大师**：**Buf**、**Pact** 或 proto diff；GraphQL 用 schema 测试。」

---

## 3. 知识要点

- `quarkus-grpc`：`.proto` + 生成代码  
- `quarkus-smallrye-graphql`：`@GraphQLApi`

---

## 4. 项目实战

### 4.1 GraphQL 路径：`pom.xml`

```xml
<dependency>
  <groupId>io.quarkus</groupId>
  <artifactId>quarkus-smallrye-graphql</artifactId>
</dependency>
```

### 4.2 GraphQL API

`src/main/java/org/acme/graphql/BookApi.java`：

```java
package org.acme.graphql;

import org.eclipse.microprofile.graphql.GraphQLApi;
import org.eclipse.microprofile.graphql.Query;

@GraphQLApi
public class BookApi {

    public record Book(String id, String title) {}

    @Query
    public Book book(String id) {
        return new Book(id, "Demo " + id);
    }
}
```

### 4.3 GraphiQL（dev）

```properties
quarkus.smallrye-graphql.ui.always-include=true
```

访问 `/q/graphql-ui`（以版本文档为准）。

### 4.4 gRPC 路径（结构说明，完整见官方 guide）

1. `src/main/proto/hello.proto` 定义 `service Greeter`。  
2. `pom` 配置 `quarkus-maven-plugin` 与 `protobuf-maven-plugin`（按 guide）。  
3. 实现生成的 `MutinyGreeterGrpc` 基类。

### 4.5 Kubernetes：`Service` gRPC

```yaml
apiVersion: v1
kind: Service
metadata:
  name: grpc-backend
spec:
  ports:
    - name: grpc
      port: 9000
      targetPort: 9000
  selector:
    app: grpc-backend
```

---

## 5. 课堂实验

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| A | GraphQL `book` 查询 | JSON 返回 |
| B | 故意深度嵌套查询（无限制时） | 讨论 DoS 风险 |
| C | （选）grpcurl 调用 Greeter | 体验二进制栈 |
| D | 填选型表：延迟、团队技能、治理 | 组内决策记录 |

---

## 6. 项目总结

| 维度 | 说明 |
|------|------|
| **优点** | gRPC 高效；GraphQL 聚合强。 |
| **缺点** | 各自治理成本。 |
| **适用场景** | 内网 RPC；BFF。 |
| **注意事项** | 版本与限流。 |
| **常见踩坑** | GraphQL N+1；忽略 deadline。 |

**延伸阅读**：<https://quarkus.io/guides/grpc-getting-started> 、 <https://quarkus.io/guides/smallrye-graphql>
