# 第 11 章：Spring 测试入门——JUnit 与 Spring Boot Test

> **业务线**：电商 / 订单履约微服务（拟真场景）。本章可独立阅读；与全书案例弱关联。

> **篇章**：基础篇（全书第 1–18 章；核心概念、单机、简单 API、初级实战）

## 上一章思考题回顾

1. **`@MockBean`**：将上下文中的 Bean **替换为 Mockito mock**（用于依赖外部服务）。**`@SpyBean`**：包装**真实 Bean**，部分方法打桩，其余走真实逻辑。  
2. **`@Async` 默认线程池**：`SimpleAsyncTaskExecutor`（每任务新线程，**生产不推荐**）；应配置 **`TaskExecutor` Bean** 或 `spring.task.execution`（Boot）。

---

## 1 项目背景

订单服务依赖 **支付 RPC**。若集成测试打真实环境，**不稳定**；若全 mock，又要保证**与生产配置**一致。**Spring 测试**在「真实 Spring 上下文」与「替身」之间取得平衡。

**痛点**：  
- 测试慢（全量上下文）。  
- **脏数据** 污染共享库。  
- **Flaky**：依赖时间、随机端口未隔离。

---

## 2 项目设计（剧本式对话）

**角色**：小胖 / 小白 / 大师。  
**结构**：测试金字塔 → Spring 测试切片 → Mock 与真实上下文的边界。

**小胖**：为啥不纯单元测试？我 Mockito 一下，跑得飞快。

**大师**：**单元**测算法与纯逻辑；**集成**测 Bean 装配、事务、Web 层、配置绑定——这些是 Mockito **模拟不了**的「胶水层」。两者互补：单元保**速度**，集成保**真相**。

**技术映射**：**@SpringBootTest** 启动完整应用上下文；**@WebMvcTest** 只加载 Web 层切片。

**小白**：`MockMvc` 与 `TestRestTemplate`？

**大师**：`MockMvc` **不真正起端口**，走 **DispatcherServlet 测试桩**；`TestRestTemplate`（配合 `@LocalServerPort` / `RANDOM_PORT`）走 **真实 Servlet 容器**，更贴近网络层（过滤器链、编码、压缩）。

**技术映射**：**MockMvc** 适合 **快速契约**；**TestRestTemplate/WebTestClient** 适合 **端到端 HTTP**。

**小胖**：`@MockBean` 会不会把我都 mock 傻了，上线才发现没集成？

**小白**：会，所以要分层：**外部系统** mock，**本服务装配**尽量真实；契约测试再补一层（第 23 章）。

**大师**：像**彩排**：替身演员（mock）先把走位走完，正式演出（集成）还得来一次。

**小白**：测试数据库用 H2 行不行？

**大师**：行，但要认识 **方言差异**；关键 SQL 用 **Testcontainers** 对齐生产（中级篇常用）。

---

## 3 项目实战

本章做一个「**能跑通 CI**」的最小集成测试：**Actuator 健康检查**（可选）+ **`MockMvc` 打一条 API**。

### 3.1 环境准备

| 项 | 说明 |
|----|------|
| 依赖 | `spring-boot-starter-test`（已含 JUnit 5、AssertJ、Mockito） |
| 可选 | `spring-boot-starter-actuator`（用于 `/actuator/health` 示例） |

```xml
<dependency>
  <groupId>org.springframework.boot</groupId>
  <artifactId>spring-boot-starter-test</artifactId>
  <scope>test</scope>
</dependency>
```

### 3.2 分步实现

**步骤 1 — 目标**：`@SpringBootTest` + `@AutoConfigureMockMvc` 访问 **`/actuator/health`**（若已引入 actuator）。

```java
package com.example.order;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.web.servlet.MockMvc;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@SpringBootTest
@AutoConfigureMockMvc
class OrderApiIT {

    @Autowired
    MockMvc mvc;

    @Test
    void health() throws Exception {
        mvc.perform(get("/actuator/health")).andExpect(status().isOk());
    }
}
```

**步骤 2 — 目标（加深）**：对 **`PaymentClient`** 使用 `@MockBean`，验证 Controller 在 **下游失败** 时返回预期错误体（与第 7 章异常模型衔接）。

**可能遇到的坑**

| 现象 | 原因 | 处理 |
|------|------|------|
| `/actuator/health` 404 | 未引入 actuator | 加依赖或改测其他公开路由 |
| 测试极慢 | `@SpringBootTest` 全量上下文 | 换 `@WebMvcTest` 切片 |
| `@MockBean` 不生效 | Bean 类型不匹配 | 检查限定符与泛型 |

### 3.3 完整代码清单与仓库

`chapter11-test`。

### 3.4 测试验证

**命令**：`mvn -q test`。

**运行结果（文字描述）**：`Tests run: ... Failures: 0`；IDE 中绿色通过。

**可选**：`mvn -q -Dtest=OrderApiIT test` 单测过滤。

---

## 4 项目总结

### 优点与缺点

| 维度 | 集成测试 | 纯 Mock |
|------|----------|---------|
| 置信度 | 高 | 中 |
| 速度 | 慢 | 快 |

### 常见踩坑经验

1. **测试用例间** `@DirtiesContext` 滥用导致极慢。  
2. **随机端口** 未注入 `localServerPort`。  
3. **`@Transactional` 测试** 回滚掩盖集成问题（需场景化）。

---

## 思考题

1. `ApplicationEvent` 与领域事件区别？（第 12 章。）  
2. `@Async` 同类调用为何不异步？（第 12 章。）

---

## 推广协作提示

| 角色 | 建议 |
|------|------|
| **测试** | 划分单元/集成/契约三层。 |

**下一章预告**：`ApplicationEvent`、`@Async`、`@EnableAsync`。
