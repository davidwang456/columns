# 第 15 章：Vert.x 事件循环与阻塞纪律

## 0. 课程卡片

| 项目 | 说明 |
|------|------|
| **建议课时** | 45～55 分钟 |
| **学习目标** | 识别阻塞点；正确使用 `@Blocking`；解释 K8s 上「延迟放大」 |
| **先修** | 第 14 章 |

---

## 1. 项目背景

许多 Quarkus HTTP 路径基于 **Vert.x**。在 **event loop 线程**上执行阻塞调用（JDBC、`sleep`、大计算）会拖累整组连接。K8s 中表现为延迟上升 → HPA 扩容 → **成本上升**。

---

## 2. 项目设计：大师与小白的对话

**小白**：「我怎么知道当前在 event loop？」

**大师**：「若资源方法返回普通类型且未标 `@Blocking`，很多栈默认在 loop 上执行——**查所用 REST 扩展文档**。」

**运维**：「CPU 不高但延迟高？」

**大师**：「可能是**线程阻塞**或 **GC**，别只会加副本。」

**架构师**：「新系统全用 reactive DB 吗？」

**大师**：「视团队能力；**blocking 栈 + worker 池**也是合法工程选择。」

**测试**：「压测要测 blocking 吗？」

**大师**：「要。对比 `@Blocking` 前后 **P99**。」

---

## 3. 知识要点

- `@Blocking` 将方法放到 worker 线程池  
- 阻塞 JDBC + reactive 入口 = 反模式  
- `quarkus.thread-pool.*`（属性以文档为准）

---

## 4. 项目实战

### 4.1 `pom.xml`

```xml
<dependency>
  <groupId>io.quarkus</groupId>
  <artifactId>quarkus-rest</artifactId>
</dependency>
```

### 4.2 对比资源（教学用）

`src/main/java/org/acme/BlockingDemoResource.java`：

```java
package org.acme;

import io.smallrye.common.annotation.Blocking;
import jakarta.ws.rs.GET;
import jakarta.ws.rs.Path;

@Path("/demo-block")
public class BlockingDemoResource {

    @GET
    @Path("/bad-hint")
    public String bad() throws InterruptedException {
        Thread.sleep(200); // 课堂：说明在错误线程上极其危险
        return "done";
    }

    @GET
    @Path("/ok")
    @Blocking
    public String ok() throws InterruptedException {
        Thread.sleep(200);
        return "done";
    }
}
```

> **警告**：`bad-hint` 仅用于短时演示，演示完应删除或禁用。

### 4.3 `application.properties`（worker 池示例）

```properties
quarkus.thread-pool.core-threads=4
quarkus.thread-pool.max-threads=25
```

### 4.4 Kubernetes：`Deployment` 注释（给运维）

在 YAML 中加 `annotations` 说明：

```yaml
metadata:
  annotations:
    training.acme/notes: "若 P99 升高，先查阻塞与 JDBC，勿先加副本"
```

---

## 5. 课堂实验

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 用 `wrk` 或 `hey` 压 `/demo-block/ok` 与（若保留）bad 路径 | 对比延迟与错误 |
| 2 | `jstack` 或 Quarkus 线程 dump（讲师演示） | 看到 worker 与 event loop 名称差异 |
| 3 | 小组总结：**三条规则**写上海报 | 课堂产出 |

---

## 6. 项目总结

| 维度 | 说明 |
|------|------|
| **优点** | 非阻塞潜力大。 |
| **缺点** | 纪律要求高。 |
| **适用场景** | 高并发 I/O。 |
| **注意事项** | 池大小与下游匹配。 |
| **常见踩坑** | reactive 入口 + JDBC。 |

**延伸阅读**：<https://quarkus.io/guides/vertx>
