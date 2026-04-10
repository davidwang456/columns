# 第 5 章：依赖注入（CDI）与作用域

## 0. 课程卡片

| 项目 | 说明 |
|------|------|
| **建议课时** | 45～60 分钟 |
| **学习目标** | 正确使用 `@ApplicationScoped` / `@RequestScoped`；理解 **ArC** 构建期容器；避免典型循环依赖与错误作用域 |
| **先修** | 第 4 章 |

---

## 1. 项目背景

Quarkus 使用 **ArC**（CDI 实现），倾向在**构建期**确定大量依赖关系，以换取启动性能。与「高度动态」的 Spring 部分习惯相比，某些模式在 Quarkus 中会碰壁。本章用最小示例讲清 **作用域**与**注入点**。

---

## 2. 项目设计：大师与小白的对话

**小白**：「`@Singleton` 和 `@ApplicationScoped` 我随便用？」

**大师**：「业务服务默认 **`@ApplicationScoped`** 更常见；`@Singleton` 是『无客户端代理的单例』，有状态时要格外谨慎。」

**小白**：「`@Inject` 和构造函数注入哪个好？」

**大师**：「**构造函数注入**更利于测试与不可变；字段注入课堂写起来短，生产代码推荐构造器。」

**架构师**：「我们能否用 `@Dependent` 包一切？」

**大师**：「`@Dependent` 生命周期跟随注入点，滥用会导致**隐式频繁创建**与性能意外。」

**测试**：「测试里怎么替换实现？」

**大师**：**`@Alternative` + `@Priority`**，或测试 profile 绑定 mock producer；后续测试章细化。」

**小白**：「报循环依赖怎么办？」

**大师**：「说明设计上有环：拆接口、中间对象、或事件驱动；别指望容器无限兜底。」

---

## 3. 知识要点

- `@ApplicationScoped`：应用级，通常线程安全无状态服务。  
- `@RequestScoped`：每个 HTTP 请求一个实例（注意在非请求线程访问）。  
- **Producer 方法**：集成第三方库实例的常用方式。

---

## 4. 项目实战

### 4.1 `pom.xml`（仅需 arc + rest）

```xml
<dependencies>
  <dependency>
    <groupId>io.quarkus</groupId>
    <artifactId>quarkus-arc</artifactId>
  </dependency>
  <dependency>
    <groupId>io.quarkus</groupId>
    <artifactId>quarkus-rest</artifactId>
  </dependency>
</dependencies>
```

（BOM 与插件同前章，略。）

### 4.2 服务与资源

`src/main/java/org/acme/GreeterService.java`：

```java
package org.acme;

import jakarta.enterprise.context.ApplicationScoped;

@ApplicationScoped
public class GreeterService {

    public String greet(String name) {
        return "Hello, " + name;
    }
}
```

`src/main/java/org/acme/GreetResource.java`：

```java
package org.acme;

import jakarta.inject.Inject;
import jakarta.ws.rs.GET;
import jakarta.ws.rs.Path;
import jakarta.ws.rs.QueryParam;

@Path("/greet")
public class GreetResource {

    private final GreeterService greeter;

    @Inject
    public GreetResource(GreeterService greeter) {
        this.greeter = greeter;
    }

    @GET
    public String hi(@QueryParam("name") String name) {
        return greeter.greet(name != null ? name : "Quarkus");
    }
}
```

### 4.3 Producer 示例（可选）

```java
package org.acme;

import jakarta.enterprise.context.ApplicationScoped;
import jakarta.enterprise.inject.Produces;
import jakarta.inject.Singleton;
import java.time.Clock;

@ApplicationScoped
public class TimeProducers {

    @Produces
    @Singleton
    Clock systemUtc() {
        return Clock.systemUTC();
    }
}
```

---

## 5. 课堂实验

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 运行 `./mvnw quarkus:dev`，访问 `/greet?name=Lab` | 返回问候语 |
| 2 | 将 `GreeterService` 改为 `@Singleton`，观察行为差异（讲师引导讨论代理） | 理解文档描述差异 |
| 3 | **故意**制造 A→B→A 注入，观察启动失败信息 | 记住错误样式 |
| 4 | （选）为 `GreeterService` 写 `@QuarkusTest` 单测（可 mock 吗？讨论） | 形成测试策略 |

---

## 6. 项目总结

| 维度 | 说明 |
|------|------|
| **优点** | 启动快、图清晰、与扩展模型一致。 |
| **缺点** | 动态代理与循环依赖容错弱于部分 Spring 项目。 |
| **适用场景** | 服务层、适配器、基础设施 Bean。 |
| **注意事项** | 有状态 Bean 的作用域与线程安全。 |
| **常见踩坑** | 在 worker 线程误用 `@RequestScoped`；循环依赖。 |

**延伸阅读**：<https://quarkus.io/guides/cdi-reference>
