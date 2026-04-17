# 第 12 章：事件与异步入门——ApplicationEvent 与 @Async

> **业务线**：电商 / 订单履约微服务（拟真场景）。本章可独立阅读；与全书案例弱关联。

> **篇章**：基础篇（全书第 1–18 章；核心概念、单机、简单 API、初级实战）

## 上一章思考题回顾

1. **`ApplicationEvent`**：Spring 应用上下文内广播的**基础设施级事件**；**领域事件**通常强调业务语义与限界上下文，可借助 Spring 事件或**消息中间件**（第 17 章）。  
2. **`@Async` 同类调用**：与 AOP 一样，**自调用**不经过代理，**异步不生效**；应拆到另一 Bean 或通过 `AopContext`（不推荐默认开启）。

---

## 1 项目背景

下单成功后需 **发送通知**、**写审计**，若同步执行，**尾延迟**上升。团队希望 **主链路事务提交后再发通知**，且 **不阻塞 HTTP 响应**。

**痛点**：  
- 事务未提交就读到脏数据。  
- 异步线程池**耗尽**拖垮系统。  
- 事件**顺序**与**丢失**无明确策略。

---

## 2 项目设计（剧本式对话）

**角色**：小胖 / 小白 / 大师。  
**结构**：先对比 MQ → 再讲事务边界与监听器相位 → 最后落到线程池。

**小胖**：为啥不用消息队列？我看大厂都 Kafka。

**大师**：进程内 **ApplicationEvent** 轻量、零依赖，适合 **单体/同进程** 的「下单后通知」；跨进程可靠性、削峰、重放，用 **MQ**（第 17 章）。选型看 **失败影响面**：丢了通知 vs 丢了订单。

**技术映射**：**ApplicationEventPublisher** + **@TransactionalEventListener**（相位控制）。

**小白**：事件监听为什么要 `AFTER_COMMIT`？

**大师**：如果 `BEFORE_COMMIT` 或同步监听，可能读到**尚未提交**的数据；通知侧一查库「订单不存在」，典型 **时序 Bug**。

**技术映射**：**TransactionPhase.AFTER_COMMIT** 保证 **提交成功后才对外可见**（同进程语义）。

**小胖**：`@Async` 一加上，我感觉接口返回飞快——这是不是「假成功」？

**小白**：接口返回的是「**受理成功**」；异步任务失败要有 **补偿/重试/告警**，否则只是「把雷埋后台」。

**大师**：默认 **SimpleAsyncTaskExecutor** 像**临时工**：来一单开一个线程，峰值会炸。生产要配 **有界队列 + 拒绝策略 + 监控**。

**技术映射**：**ThreadPoolTaskExecutor** + **Micrometer 指标**（第 19/24 章衔接）。

---

## 3 项目实战

本章用 **最小代码**跑通：**下单写库 → 提交事务 → 异步通知**（`System.out` 代替短信网关）。

### 3.1 环境准备

| 项 | 说明 |
|----|------|
| 依赖 | `spring-boot-starter` + `spring-boot-starter-jdbc`（需要事务） |
| 注解 | `@EnableAsync` + `@EnableTransactionManagement`（Boot 通常自动） |

### 3.2 分步实现

**步骤 1 — 目标**：定义 **事件载荷**（POJO）。

**事件载荷（POJO，Spring 4.2+ 可直接发布）**

```java
public record OrderPlaced(String orderId) {}
```

在应用服务中：`applicationEventPublisher.publishEvent(new OrderPlaced(orderId));`

```java
package com.example.listener;

import org.springframework.context.event.EventListener;
import org.springframework.scheduling.annotation.Async;
import org.springframework.stereotype.Component;
import org.springframework.transaction.event.TransactionPhase;
import org.springframework.transaction.event.TransactionalEventListener;

@Component
public class NotifyListener {

    @TransactionalEventListener(phase = TransactionPhase.AFTER_COMMIT)
    @Async
    public void onCommitted(OrderPlaced event) {
        System.out.println("notify " + event.orderId());
    }
}
```

**启用异步**

```java
package com.example;

import org.springframework.scheduling.annotation.EnableAsync;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.context.annotation.Configuration;

@Configuration
@EnableAsync
public class AsyncConfig { }
```

**步骤 4 — 目标（线程池生产化示例）**

```java
@Bean
TaskExecutor notifyExecutor() {
    ThreadPoolTaskExecutor ex = new ThreadPoolTaskExecutor();
    ex.setCorePoolSize(4);
    ex.setMaxPoolSize(8);
    ex.setQueueCapacity(200);
    ex.setThreadNamePrefix("notify-");
    ex.initialize();
    return ex;
}
```

监听器上可加：`@Async("notifyExecutor")`（Bean 名对齐）。

### 3.3 完整代码清单与仓库

`chapter12-events`。

### 3.4 测试验证

集成测试中 **`@Transactional` 测试默认回滚** 时，`AFTER_COMMIT` 监听可能**不触发**——需使用 **`TransactionTemplate` 手动提交** 或 **独立测试**验证提交后行为。

**推荐**：用 **Awaitility** 等待异步日志 / 计数器；或 **`CountDownLatch`** 在监听器 `countDown()`。

**命令**：`mvn -q test`。

---

## 4 项目总结

### 优点与缺点

| 维度 | 进程内事件 | MQ |
|------|------------|-----|
| 可靠性 | 进程挂则丢 | 高 |
| 复杂度 | 低 | 高 |

### 常见踩坑经验

1. **AFTER_COMMIT** 未配合事务。  
2. **异步异常** 吞掉未观测。  
3. **线程上下文**（用户、租户）丢失。

---

## 思考题

1. 单体「最小可运行订单服务」应包含哪些模块边界？（第 13 章综合。）  
2. 如何将进程内事件演进为 MQ？（第 17 章。）

---

## 推广协作提示

| 角色 | 建议 |
|------|------|
| **运维** | 监控异步队列积压与线程池拒绝。 |

**下一章预告**：基础篇综合实战——可运行订单服务端到端。
