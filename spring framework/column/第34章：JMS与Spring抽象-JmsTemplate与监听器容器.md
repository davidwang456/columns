# 第 34 章：JMS 与 Spring 抽象——`JmsTemplate` 与监听器容器

> **业务线**：电商 / 订单履约微服务（拟真场景）。本章可独立阅读；与全书案例弱关联。  
> **篇章**：中级篇（全书第 19–35 章；架构与分布式、性能、可观测性）

> **定位**：在 **`spring-jms`** 模块视角下掌握 **Jakarta JMS API** 的 **Spring 封装**：**`JmsTemplate`**（同步发送/同步接收辅助）、**`DefaultMessageListenerContainer`**（异步监听）、**`@JmsListener`**；与 **第 17 章**（**Kafka/Rabbit 抽象**、Outbox）对照——**JMS** 仍是 **传统企业 MQ**（**ActiveMQ Classic/Artemis、IBM MQ** 等）的常见协议；**云原生 Kafka** 场景可跳过本章或仅作 **互通**。

## 上一章思考题回顾

1. **`DatabaseClient`**：**任意 SQL**、**动态拼接**；**`ReactiveCrudRepository`**：**CRUD** 与 **简单派生查询**。  
2. **存储过程 + 强一致**：多数团队 **阻塞栈 + JDBC** 或 **独立作业服务**；**R2DBC** 对 **过程**支持需 **逐驱动**评估，**不宜**硬绑在 **WebFlux 热路径**。

---

## 1 项目背景

「鲜速达」与 **区域仓 WMS** 对接时，对方只提供 **JMS 队列**（**订单下发/回执**）。团队在 **Kafka** 已标准化（第 17 章）的前提下，仍需 **单独 Broker 连接** 与 **JMS 语义**（**Queue、Session、事务会话**）。若不用 **`JmsTemplate`** 封装，业务代码会充斥 **`Connection`、`Session`、`MessageProducer`** 样板，**异常与重连** 难以统一。

**痛点**：

- **混淆 JMS 与 Kafka API**：**消息模型**（**Queue vs Topic**、**消费语义**）不同。  
- **事务消息**：**JMS Session** 与 **DB 事务** 的 **XA** 或 **本地事务会话** 成本高。  
- **监听器并发**：**`concurrency`** 与 **prefetch** 误配导致 **积压或乱序**。

**痛点放大**：**双协议**（**Kafka 对内、JMS 对外**）若 **无防腐层**，**错误处理** 与 **幂等** 会在 **两套监听器**里 **复制漂移**。

```mermaid
flowchart LR
  APP[Spring 应用] --> JT[JmsTemplate]
  JT --> BROKER[(JMS Broker)]
  BROKER --> L[@JmsListener]
  L --> SVC[业务处理]
```

---

## 2 项目设计（剧本式对话）

**角色**：小胖 / 小白 / 大师。  
**结构**：JMS 定位 → 与 Kafka 选型 → 事务边界。

**小胖**：有 Kafka 了还要 JMS 干啥？

**大师**：**历史系统**、**厂商设备**、**监管域** 仍用 **JMS**；**新系统对内** 可 **Kafka**，**边界** 用 **适配器** 或 **独立进程** 消费 JMS **再转发**。

**技术映射**：**`spring-jms`** = **Jakarta JMS** + **Spring 资源管理**。

**小白**：**`JmsTemplate`** 和 **`RabbitTemplate`** 像吗？

**大师**：**模板模式**相似；**底层协议**不同。别 **混用监听器注解**（**`@JmsListener`** vs **`@RabbitListener`**）。

**技术映射**：**抽象共性**在 **第 17 章** 已概括；本章 **专精 JMS**。

**小胖**：**消息事务**和 **DB @Transactional** 怎么一起？

**大师**：**理想**是 **Outbox**（第 17 章）或 **最终一致**；**强一致 XA** **重**，**慎用**。**JMS 本地事务会话** 与 **DB** **两阶段** 需 **中间件与驱动**支持。

**技术映射**：**`sessionTransacted`**、**`JmsTransactionManager`**（与 **`DataSourceTransactionManager`** **链式**需设计）。

---

## 3 项目实战

### 3.1 环境准备

| 项 | 说明 |
|----|------|
| Boot | 3.2.x |
| Broker | **嵌入式**（**测试**）或 **Artemis/ActiveMQ**（**本地/容器**） |

**`pom.xml`（节选，Artemis 起步）**

```xml
<parent>
  <groupId>org.springframework.boot</groupId>
  <artifactId>spring-boot-starter-parent</artifactId>
  <version>3.2.5</version>
</parent>

<dependencies>
  <dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-artemis</artifactId>
  </dependency>
  <dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-web</artifactId>
  </dependency>
</dependencies>
```

**说明**：**`spring-boot-starter-artemis`** 传递引入 **`spring-jms`** 与 **客户端**；**嵌入式** 开发可用 **`spring-boot-starter-activemq`**（**场景二选一**）。

### 3.2 配置（`application.yml` 示例）

```yaml
spring:
  artemis:
    mode: embedded
  jms:
    template:
      default-destination: order.queue
```

（**嵌入式** 仅 **开发演示**；**生产**填 **broker URL** 与 **凭证**。）

### 3.3 分步实现：发送

```java
package com.example.jms;

import org.springframework.jms.core.JmsTemplate;
import org.springframework.stereotype.Service;

@Service
public class OrderJmsPublisher {

    private final JmsTemplate jmsTemplate;

    public OrderJmsPublisher(JmsTemplate jmsTemplate) {
        this.jmsTemplate = jmsTemplate;
    }

    public void publishOrderCreated(String payload) {
        jmsTemplate.convertAndSend("order.queue", payload);
    }
}
```

### 3.4 分步实现：监听

```java
package com.example.jms;

import org.springframework.jms.annotation.JmsListener;
import org.springframework.stereotype.Component;

@Component
public class OrderJmsConsumer {

    @JmsListener(destination = "order.queue", concurrency = "1-3")
    public void onMessage(String body) {
        System.out.println("JMS received: " + body);
    }
}
```

### 3.5 可能遇到的坑

| 现象 | 原因 | 处理 |
|------|------|------|
| **重复消费** | **至少一次** 语义 | **业务幂等**（第 17 章） |
| **监听不启动** | **Broker 未连上** | 查 **健康检查**与**日志** |
| **类型转换失败** | **MessageConverter** 不匹配 | 配置 **JSON** 或 **自定义** |

### 3.6 测试验证

**`@SpringBootTest`** + **嵌入式 Broker** 或 **Testcontainers**；**发一条** 断言 **监听**被调用（**Mockito spy** 或 **日志断言**）。

---

## 4 项目总结

### 优点与缺点

| 维度 | JMS + Spring | Kafka 原生客户端 |
|------|----------------|------------------|
| 企业对接 | **常见标准** | **需对方支持** |
| 云原生生态 | **弱于 Kafka** | **强** |

### 适用场景

1. **WMS/ESB/金融** 等 **JMS 强制** 对接。  
2. **ActiveMQ/Artemis** 已存在的企业总线。

### 注意事项

- **`spring-jms`** 模块在 **Framework 仓库** 中；**Boot** 用 **starter** 拉齐 **版本**（**第 15 章 BOM**）。  
- **Jakarta EE 9+** 后包名为 **`jakarta.jms`**。

### 常见踩坑经验

1. **现象**：**消息乱序**。  
   **根因**：**并发 > 1** 且 **队列无序保证**。  

2. **现象**：**大消息 OOM**。  
   **根因**：**blob** 应走 **对象存储**，**队列**只传 **引用**。  

---

## 思考题

1. **`JmsTemplate` 同步接收** 在 **Web 请求线程**里调用，会带来什么 **风险**？  
2. 若 **Kafka 与 JMS** 同时存在，你会如何 **统一幂等与死信** 模型？

---

## 推广协作提示

| 角色 | 建议 |
|------|------|
| **集成架构师** | **边界服务** 专责 **协议转换**，**内核** 保持 **Kafka** 统一。 |
| **运维** | **Broker 磁盘**、**DLQ**、**积压告警** 与 **Kafka** 指标 **分面板**。 |

**专栏延伸**：**`spring-jms`** 与 **第 25 章** 消息抽象、**第 15 章** BOM、**第 31/33 章** 响应式栈 **并列** 为 **技术选型工具箱**；**无更多「下一章」**，可按岗位 **裁剪学习路径**。
