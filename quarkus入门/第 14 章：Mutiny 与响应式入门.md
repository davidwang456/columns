# 第 14 章：Mutiny 与响应式入门

## 0. 课程卡片

| 项目 | 说明 |
|------|------|
| **建议课时** | 50～60 分钟 |
| **学习目标** | 使用 `Uni`/`Multi` 组合异步；理解 `subscribe` 与失败传播 |
| **先修** | 第 4 章 |

---

## 1. 项目背景

I/O 密集场景下，**非阻塞**可提升资源利用率。Quarkus 生态广泛使用 **Mutiny**。本章不追求理论完备，而是让学员能读懂链式代码并避免 `block()` 滥用。

---

## 2. 项目设计：大师与小白的对话

**小白**：「响应式 = 更快？」

**大师**：「不一定。**更好地叠等待**不等于 CPU 算得更快。」

**小白**：「`Uni` 和 `Multi` 啥区别？」

**大师**：「**Uni**：0 或 1 个结果；**Multi**：流。HTTP 请求常像 Uni。」

**运维**：「线程数少了，CPU 降了，但栈难懂。」

**大师**：「所以要有**纪律**与**可观测**：链上打点、超时。」

**测试**：「怎么断言异步？」

**大师**：「测试中 `.await().atMost(Duration.ofSeconds(2))` 或 `UniAssertSubscriber`。」

---

## 3. 知识要点

- `transform` / `chain` / `onFailure`  
- 禁止在 event loop 上 `block()`（第 15 章强化）

---

## 4. 项目实战

### 4.1 `pom.xml`

```xml
<dependency>
  <groupId>io.quarkus</groupId>
  <artifactId>quarkus-rest</artifactId>
</dependency>
<!-- Mutiny 常随 reactive 栈引入；显式依赖 smallrye-mutiny 若需要 -->
<dependency>
  <groupId>io.smallrye.reactive</groupId>
  <artifactId>mutiny</artifactId>
</dependency>
```

（若使用 `quarkus-rest` 的 reactive 变体，以官方 guide 为准合并依赖。）

### 4.2 示例服务

`src/main/java/org/acme/ReactiveGreeting.java`：

```java
package org.acme;

import io.smallrye.mutiny.Uni;
import jakarta.enterprise.context.ApplicationScoped;

import java.time.Duration;

@ApplicationScoped
public class ReactiveGreeting {

    public Uni<String> greet(String name) {
        return Uni.createFrom().item(name)
            .onItem().transformToUni(n ->
                Uni.createFrom().item("Hello, " + n)
                    .onItem().delayIt().by(Duration.ofMillis(50))
            );
    }
}
```

### 4.3 JAX-RS 返回 `Uni`（若使用 RESTEasy Reactive 风格）

```java
package org.acme;

import io.smallrye.mutiny.Uni;
import jakarta.inject.Inject;
import jakarta.ws.rs.GET;
import jakarta.ws.rs.Path;
import jakarta.ws.rs.QueryParam;

@Path("/rx")
public class RxResource {

    @Inject
    ReactiveGreeting greeting;

    @GET
    public Uni<String> hi(@QueryParam("name") String name) {
        return greeting.greet(name != null ? name : "World");
    }
}
```

> **说明**：若当前栈为 blocking REST，可将 `Uni` 在资源里 `await()` **仅用于课堂对比**，并强调生产勿用。

---

## 5. 课堂实验

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 为 `ReactiveGreeting` 编写单元测试，`await` 结果 | 通过 |
| 2 | 故意抛异常于链中，用 `onFailure().recoverWithItem` 恢复 | 理解失败策略 |
| 3 | 讨论：若下游是 JDBC，链应如何接？ | 引出第 15～16 章 |

---

## 6. 项目总结

| 维度 | 说明 |
|------|------|
| **优点** | 组合清晰；与消息/Vert.x 一致。 |
| **缺点** | 学习曲线；调试难。 |
| **适用场景** | 异步 I/O、流处理。 |
| **注意事项** | 线程模型；超时。 |
| **常见踩坑** | 随意 `block()`；吞异常。 |

**延伸阅读**：<https://quarkus.io/guides/mutiny-primer>
