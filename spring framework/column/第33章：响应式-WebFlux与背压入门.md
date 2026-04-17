# 第 33 章：响应式——WebFlux 与背压入门

> **业务线**：电商 / 订单履约微服务（拟真场景）。本章可独立阅读；与全书案例弱关联。

## 上一章思考题回顾

1. **`RouterFunction`**：**函数式** 路由注册，等价于 **`@RequestMapping`** 风格；适合 **流式** 与 **组合**。  
2. **背压**：**Reactor** 中 **订阅者** 通过 **`request(n)`** 控制上游推送速率；**Mono/Flux** 操作符链式传递。

---

## 1 项目背景

**SSE 推送订单状态**、**高并发 IO** 场景下，线程模型从 **一请求一线程** 转向 **事件循环 + 非阻塞 IO**。

**痛点**：  
- **阻塞调用** 混入 WebFlux **拖垮** Reactor 线程。  
- **错误信号** 与 **HTTP 状态** 映射复杂。

---

## 2 项目设计（剧本式对话）

**角色**：小胖 / 小白 / 大师。  
**结构**：线程模型对比 → 阻塞调用禁忌 → 背压直觉。

**小胖**：WebFlux 不就是异步吗？我把 `JdbcTemplate` 往里一塞，照样跑。

**大师**：WebFlux 基于 **Netty** 的事件循环；**阻塞 JDBC** 会占满 event loop 线程，**拖垮**整个服务。数据访问需 **R2DBC/Mongo reactive** 或 **隔离线程池**。

**技术映射**：**ServerHttpResponse** + **`Flux` as body**；**Schedulers.boundedElastic()** 仅作迁移期权宜。

**小白**：背压是不是「别推太快」？

**大师**：对，**订阅者**通过 `request(n)` 控制上游；没有背压，队列会无限涨。

**小胖**：错误怎么传到客户端？

**大师**：**onError** 映射到 HTTP 状态；WebFlux 里要注意 **全局 `WebExceptionHandler`** 与 **ProblemDetail**。

---

## 3 项目实战

### 3.1 环境准备

| 项 | 说明 |
|----|------|
| 依赖 | `spring-boot-starter-webflux` |
| 验证 | `curl` 订阅 SSE |

```xml
<dependency>
  <groupId>org.springframework.boot</groupId>
  <artifactId>spring-boot-starter-webflux</artifactId>
</dependency>
```

### 3.2 分步实现

```java
@RestController
@RequestMapping("/api/stream")
public class OrderStreamController {
    @GetMapping(value = "/{id}", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public Flux<String> stream(@PathVariable String id) {
        return Flux.interval(Duration.ofSeconds(1)).map(t -> "tick-" + id + "-" + t);
    }
}
```

**步骤 3 — 目标**：`curl -N` 观察 **SSE** 流式输出是否持续。

### 3.3 完整代码清单与仓库

`chapter33-webflux`。

### 3.4 测试验证

`WebTestClient` 订阅 **SSE**。

**命令**：`mvn -q test`。

**可能遇到的坑**

| 现象 | 原因 | 处理 |
|------|------|------|
| 阻塞调用卡死 | JDBC/RPC 阻塞 | 换响应式驱动或隔离线程池 |
| SSE 断连 | 代理超时 | 调整网关与 `keep-alive` |

---

## 4 项目总结

### 常见踩坑经验

1. **混用 MVC + WebFlux** 同一端口（需分离或网关）。  
2. **阻塞 JDBC** 在默认线程池。  
3. **调试** 异步栈困难。

---

## 思考题

1. **GraalVM Native Image** 与 **反射配置**？（第 34 章。）  
2. **`reachability`** 与 **Spring AOT**？（第 34 章。）

---

## 推广协作提示

| 角色 | 建议 |
|------|------|
| **运维** | 调整 Netty 工作线程与背压监控。 |

**下一章预告**：Spring Native、GraalVM、`native-image`。
