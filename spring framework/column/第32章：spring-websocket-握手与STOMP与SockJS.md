# 第 32 章：`spring-websocket`——握手、SockJS 与 STOMP 消息

> **业务线**：电商 / 订单履约微服务（拟真场景）。本章可独立阅读；与全书案例弱关联。  
> **篇章**：中级篇（全书第 19–35 章；架构与分布式、性能、可观测性）

> **定位**：在 **Servlet 栈**（**`spring-webmvc` + `spring-websocket`**）下建立 **WebSocket** 实时通道；理解 **`spring-messaging`** 提供的 **STOMP** 子协议与 **`@MessageMapping`**；用 **SockJS** 处理 **代理/老旧浏览器** 的 **降级**；与 **第 31 章 WebFlux** 的 **响应式 WebSocket** **二选一**，避免 **混栈**。

## 上一章思考题回顾

1. **`OxmTemplate`**：与 **`JdbcTemplate`** 类似，提供 **统一异常翻译**入口（**`OxmException`** 体系）、**模板方法**减少样板代码；**重试**仍应在 **业务层**显式策略。  
2. **XSD 变更契约测试**：**XML 样本** + **XSD 校验** + **对方约定**的 **兼容性策略**（**版本号/命名空间**）。

---

## 1 项目背景

「鲜速达」用户下单后希望在 **App** 内 **实时看到骑手位置与订单状态**，而不是 **轮询 HTTP**。团队决定在 **单体 Servlet 应用**上增加 **WebSocket** 推送；**移动端网络**环境复杂，需 **SockJS** 在 **WebSocket 不可达**时 **降级为流式 HTTP**；**多端订阅**（用户、骑手、客服）希望用 **STOMP** 统一 **目的地（destination）** 与 **订阅模型**。

**痛点**：

- **只会上 HTTP**：把 **长轮询** 当「准实时」，**延迟与负载**双高。  
- **直连裸 WebSocket**：**企业代理**掐断 **Upgrade**；**无 STOMP** 时 **路由与鉴权**全自己造。  
- **与 WebFlux 混淆**：**Servlet WebSocket** 与 **Reactive WebSocket** **线程模型**不同，**勿**在同一应用 **混用两套栈**。

**痛点放大**：若 **未在握手阶段做认证**（**`HandshakeInterceptor` / 子协议头**），**通道建立后**再补鉴权，容易出现 **匿名长连接** **刷爆** 业务推送。

```mermaid
flowchart TB
  C[浏览器/App] --> S[SockJS client 可选]
  S --> W[WebSocket 或 HTTP 流]
  W --> ST[STOMP over WebSocket]
  ST --> M[@MessageMapping]
```

---

## 2 项目设计（剧本式对话）

**角色**：小胖 / 小白 / 大师。  
**结构**：为何 STOMP → 安全 → 与 MQ 关系。

**小胖**：我 `websocket` 一连上不就随便收消息吗？还要啥 STOMP？

**大师**：**裸 WebSocket** 只有 **帧**；**STOMP** 提供 **destination、ack、事务语义（简化）**，和 **`spring-messaging`** 的 **`SimpMessagingTemplate`** 对齐，**业务代码**像写 **消息控制器**。

**技术映射**：**`@EnableWebSocketMessageBroker`** + **`@MessageMapping`**。

**小白**：这和 **第 17 章 MQ** 有啥关系？

**大师**：**MQ** 是 **进程间**；**STOMP broker** 多在 **内存**（**简单广播**）或 **可插拔**；**规模上来**往往 **桥接**到 **真实 MQ**（**架构级**决策）。

**技术映射**：**`convertAndSend("/topic/orders", payload)`**。

**小胖**：**SockJS** 是不是「假的 WebSocket」？

**大师**：优先 **真 WebSocket**；不行则 **降级**，对用户 **尽量透明**。**移动端 WebView** 兼容性差异大，**SockJS** 常能救命。

**技术映射**：**`registry.addEndpoint("/ws").withSockJS()`**。

---

## 3 项目实战

本章采用 **Spring Boot 3.2.x** 简化 **Servlet 容器** 与 **依赖对齐**（**`spring-websocket`** 由 **`spring-boot-starter-websocket`** **传递引入**）。

### 3.1 环境准备

| 项 | 说明 |
|----|------|
| JDK | 17+ |
| 依赖 | `spring-boot-starter-websocket` |

**`pom.xml`（节选）**

```xml
<parent>
  <groupId>org.springframework.boot</groupId>
  <artifactId>spring-boot-starter-parent</artifactId>
  <version>3.2.5</version>
</parent>

<dependencies>
  <dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-web</artifactId>
  </dependency>
  <dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-websocket</artifactId>
  </dependency>
</dependencies>
```

### 3.2 分步实现

**步骤 1 — 目标**：启用 **消息代理** 与 **STOMP 端点**（**SockJS**）。

```java
package com.example.ws;

import org.springframework.context.annotation.Configuration;
import org.springframework.messaging.simp.config.MessageBrokerRegistry;
import org.springframework.web.socket.config.annotation.EnableWebSocketMessageBroker;
import org.springframework.web.socket.config.annotation.StompEndpointRegistry;
import org.springframework.web.socket.config.annotation.WebSocketMessageBrokerConfigurer;

@Configuration
@EnableWebSocketMessageBroker
public class WebSocketConfig implements WebSocketMessageBrokerConfigurer {

    @Override
    public void configureMessageBroker(MessageBrokerRegistry registry) {
        registry.enableSimpleBroker("/topic");
        registry.setApplicationDestinationPrefixes("/app");
    }

    @Override
    public void registerStompEndpoints(StompEndpointRegistry registry) {
        registry.addEndpoint("/ws/order-events").setAllowedOriginPatterns("*").withSockJS();
    }
}
```

**注意**：生产应 **收紧 `setAllowedOriginPatterns`**；示例为 **本地演示**。

**步骤 2 — 目标**：**`@MessageMapping`** 处理客户端 **发送**，**`SimpMessagingTemplate`** **广播**。

```java
package com.example.ws;

import org.springframework.messaging.handler.annotation.MessageMapping;
import org.springframework.messaging.handler.annotation.SendTo;
import org.springframework.stereotype.Controller;

@Controller
public class OrderEventController {

    @MessageMapping("/order/ping")
    @SendTo("/topic/order-events")
    public String ping(String body) {
        return "echo:" + body;
    }
}
```

**步骤 3 — 目标**：**应用主类** 启动 **嵌入式 Tomcat**（Boot 默认）。

```java
package com.example.ws;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

@SpringBootApplication
public class WsApplication {
    public static void main(String[] args) {
        SpringApplication.run(WsApplication.class, args);
    }
}
```

**步骤 4 — 目标**：用 **浏览器 SockJS + STOMP.js** 或 **`wscat`**（**裸 WebSocket** 需 **非 SockJS 端点**）验证；**简易验证**可临时增加 **第二端点** **无 SockJS** 便于工具测试。

### 3.3 可能遇到的坑

| 现象 | 原因 | 处理 |
|------|------|------|
| **403 on handshake** | **CORS** 或 **Security** 拦截 | **Spring Security** 放行 **`/ws/**`** 并配置 **CSRF**（**WebSocket** 专节） |
| **连上无消息** | **destination** 拼错 | 对齐 **`/topic`** 前缀 |
| **与 WebFlux 冲突** | **依赖混引** | **移除** `spring-boot-starter-webflux`（**Servlet 栈**场景） |

### 3.4 测试验证

- **集成测试**：**`@SpringBootTest` + `MockMvc`** 对 **HTTP**；**WebSocket** 常用 **`@WebSocketTest`**（**Boot 测试切片**）或 **Testcontainers**（**较重**）。  
- **手工**：浏览器控制台 **SockJS** 客户端订阅 **`/topic/order-events`**，向 **`/app/order/ping`** **send**。

**curl 说明**：**STOMP** 非简单 HTTP，**curl** 不适用；可用 **HTTP 降级流**调试 **SockJS**（进阶）。

---

## 4 项目总结

### 优点与缺点

| 维度 | Servlet + STOMP + SockJS | 长轮询 HTTP |
|------|---------------------------|-------------|
| 实时性 | **高** | **差** |
| 复杂度 | **中** | **低** |
| 运维 | **长连接**与 **负载均衡 sticky** | **简单** |

### 适用场景

1. **站内通知**、**配送进度**、**客服会话**。  
2. **代理环境不确定** 的 **H5**。

### 注意事项

- **第 33 章 Security**：**WebSocket 授权** 需单独策略。  
- **背压**：**海量推送**考虑 **分片**、**MQ**、**限流**。

### 常见踩坑经验

1. **现象**：**网关**超时断开。  
   **根因**：**Idle timeout** 小于 **心跳**。  

2. **现象**：**集群**下消息 **乱序/丢失**。  
   **根因**：**内存 broker** 非集群；需 **外置消息中间件** 或 **粘性会话** 策略。  

---

## 思考题

1. **`/topic` 与 `/queue`** 在 **STOMP** 语义中分别适合 **广播**还是 **点对点**？  
2. 若迁移到 **WebFlux**（第 31 章），**WebSocket API** 与本章 **Servlet STOMP** 的 **迁移成本**主要在哪些层？

---

## 推广协作提示

| 角色 | 建议 |
|------|------|
| **开发** | **握手鉴权**与 **订阅鉴权**分层设计。 |
| **运维** | **Nginx/网关** 开启 **WebSocket** 相关 **超时与 header**。 |

**专栏延伸**：本章对应 **`spring-websocket` + `spring-messaging`**；**全栈实时** 需与 **第 17 章**（**后端异步**）及 **前端** 协同设计。

**下一章预告**：**第 33 章**——**R2DBC** 与 **WebFlux** 协同（**第 31 章** WebFlux 总览在前）。
