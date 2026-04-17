# 第 25 章：消息驱动——Spring 对 MQ 的抽象与事务消息

> **业务线**：电商 / 订单履约微服务（拟真场景）。本章可独立阅读；与全书案例弱关联。

> **篇章**：中级篇（全书第 19–35 章；架构与分布式、性能、可观测性）

## 上一章思考题回顾

1. **抽象共性**：**`MessagingTemplate`** 风格与 **`@RabbitListener` / `@JmsListener`** 监听模型；Spring Integration 提供**统一消息**模型。  
2. **一致性**：**事务性发件箱（Outbox）** 或 **本地消息表** + **可靠投递**；**Kafka 事务** 与 DB 两阶段需权衡。

---

## 1 项目背景

订单支付成功后需 **异步通知仓储出库**。HTTP 回调不可靠；**消息**解耦与**削峰**。团队希望 **Spring 抽象** 切换 **RabbitMQ/Kafka** 时少改业务。

**痛点**：  
- **消息丢失**（生产者未确认）。  
- **重复消费**（无幂等）。  
- **顺序性**（同用户订单乱序）。

---

## 2 项目设计（剧本式对话）

**角色**：小胖 / 小白 / 大师。  
**结构**：先对齐「至少一次」→ Outbox vs 本地事务 → 幂等与顺序。

**小胖**：我 HTTP 回调仓储系统，失败就重试，不就行了吗？

**大师**：回调**耦合**、**背压**、**超时**难控；消息把**峰值**与**失败重试**从主链路剥离。多数中间件是 **至少一次** 投递——所以消费端必须 **幂等**。

**技术映射**：**@TransactionalEventListener** + **Outbox** 表 或 **Kafka tx**。

**小白**：Outbox 是不是「为了发消息再写一张表」，好烦？

**大师**：烦的是**一致性**：要么 **DB 事务与消息发送**同事务（Outbox），要么接受 **丢消息/重复消息** 并在业务上补。**没有免费午餐**。

**小白**：同用户消息要保证顺序吗？

**大师**：强顺序要 **分区键**（`userId`）+ **单分区顺序**；全局顺序会牺牲吞吐。

**大师**：**JmsTemplate / RabbitTemplate / KafkaTemplate** 都是 **MessagingTemplate** 家族风格；监听端用 **`@RabbitListener` / `@KafkaListener`**。

---

## 3 项目实战

### 3.1 环境准备

| 项 | 说明 |
|----|------|
| 本地 | Docker 起 RabbitMQ，或 **Testcontainers** |
| 依赖 | `spring-boot-starter-amqp`（示例） |

### 3.2 分步实现

**RabbitMQ 示例（概念）**

```java
@Component
public class OrderPaidPublisher {
    private final RabbitTemplate rabbit;

    public void publish(OrderPaid evt) {
        rabbit.convertAndSend("orders.exchange", "order.paid", evt);
    }
}
```

**消费者**

```java
@RabbitListener(queues = "wms.ship.queue")
public void onMessage(OrderPaid evt) { /* idempotent ship */ }
```

**步骤 3 — 目标**：在 `application.yml` 配置 **host/port/user**，并用 `rabbitmqctl` 或管理台确认 **exchange/queue** 绑定成功。

**步骤 4 — 目标（故障注入）**：手动停 consumer，观察队列堆积；恢复后验证 **重复投递** 下幂等仍成立（`orderId` 幂等表）。

### 3.3 完整代码清单与仓库

`chapter21-messaging`。

### 3.4 测试验证

**Testcontainers** 起 RabbitMQ；集成测试发收消息。

**命令**：`mvn -q test`。

**可能遇到的坑**

| 现象 | 原因 | 处理 |
|------|------|------|
| 消息丢失 | 未 publisher confirm | 开启 confirm 与重试策略 |
| 无限重试 | 消费异常未进 DLQ | **死信队列** + 告警 |

---

## 4 项目总结

### 常见踩坑经验

1. **手动 ACK** 与 **重试** 死循环。  
2. **大消息** 撑爆 broker。  
3. **Schema 演进** 不兼容。

---

## 思考题

1. **AbstractRoutingDataSource** 路由键放哪？（第 18 章。）  
2. **只读事务** 路由到从库注意点？（第 18 章。）

---

## 推广协作提示

| 角色 | 建议 |
|------|------|
| **运维** | 监控堆积与 DLQ。 |

**专栏延伸**：**传统 JMS**（**`JmsTemplate` / `@JmsListener`**）专章见 **第 34 章**。

**下一章预告**：多数据源、读写分离、`@Transactional` 路由。
