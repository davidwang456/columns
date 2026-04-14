# 第 35 章：端到端场景 —— 客服 Agent

## 1. 项目背景

分散的教程各教一个「点」：`ChatModel`、`Tools`、`Memory`、RAG……而真实 **客服域**往往要 **同时** 完成：理解用户诉求、**调用内部订单/票务 API**、必要时 **引用政策文档**、在多轮对话中 **维持上下文**。`customer-support-agent-example` 将这些能力 **收口在一个 Spring Boot 应用**里，是部门内训时 **从点到线** 的最佳演示项目之一。

入口类：`langchain4j-examples/customer-support-agent-example/src/main/java/dev/langchain4j/example/CustomerSupportAgentApplication.java`。配套还有 **集成测试**（如 `CustomerSupportAgentIT`）与 **判题式断言**（`JudgeModelAssertions`），提示你「**生产级 LLM 应用也要自动化测试**」。

## 2. 项目设计：大师与小白的对话

**小白**：这和 `spring-boot-example` 差别在哪？

**大师**：后者偏 **通用 AiService/流式**演示；客服示例更贴近 **业务名词、工具分层、领域异常**。

**小白**：Agent 一定比单一 RAG 好吗？

**大师**：**能处理更多行动**，但 **故障面更大**——工具权限与提示策略是第一道门。

**小白**：人工接管怎么设计？

**大师**：在 **工具层** 暴露 `escalateToHuman(ticketId)` 或在 **置信度/政策关键词** 出现时 **中断自动回复**。

**小白**：为何有 `BookingTools`？

**大师**：演示 **领域服务**被模型驱动调用；真实系统应对齐 **你的核心聚合根**（订单/合同）。

**小白**：测试里为何要用「判题模型」？

**大师**：对开放式自然语言输出，传统 `assertEquals` 不稳；可用 **弱断言** + **LLM judge**（注意成本与 **偏见**）。

**小白**：如何防止模型乱承诺退款？

**大师**：**系统提示 + 工具返回的事实**为准；对 **金额/期限** 用 **代码校验**后再展示。

**小白**：可观测性？

**大师**：把 **每次会话**关联 **业务 traceId**；工具调用 **结构化日志**（第 36 章）。

**小白**：扩展到多语言客服？

**大师**：**检测语言**→ **路由到对应知识库**→ **回应同语言**；注意 **政策文档** 译文版本号。

## 3. 项目实战：主代码片段

> **场景入戏**：这是 **「前台 + 工具间 + 黑板记忆」** 的一条龙示例：**Controller** 是接待台，**AiService Agent** 是值班经理，**@Tool** 是 **「只能走审批单」的仓库钥匙**——乱给钥匙，等于把 **生产库 merge 权限**塞给实习生。

请按顺序阅读（路径以 [`customer-support-agent-example`](../../langchain4j-examples/customer-support-agent-example) 为准）：

1. [`CustomerSupportAgent.java`](../../langchain4j-examples/customer-support-agent-example/src/main/java/dev/langchain4j/example/CustomerSupportAgent.java) —— **对外接口**与注解。  
2. [`CustomerSupportAgentConfiguration.java`](../../langchain4j-examples/customer-support-agent-example/src/main/java/dev/langchain4j/example/CustomerSupportAgentConfiguration.java) —— **Bean 装配**。  
3. [`BookingTools.java`](../../langchain4j-examples/customer-support-agent-example/src/main/java/dev/langchain4j/example/BookingTools.java) —— `@Tool` 与 **领域异常**。  
4. [`CustomerSupportAgentController.java`](../../langchain4j-examples/customer-support-agent-example/src/main/java/dev/langchain4j/example/CustomerSupportAgentController.java) —— HTTP 映射。  
5. [`CustomerSupportAgentIT.java`](../../langchain4j-examples/customer-support-agent-example/src/test/java/dev/langchain4j/example/CustomerSupportAgentIT.java) —— **集成测试**如何起上下文。

在笔记本绘制 **时序图**：`User HTTP` → `Controller` → `Agent` → (`ChatModel` / `Tools` / `Memory`)。

| 闯关 | 任务 |
|------|------|
| ★ | 圈出 **`@SystemMessage` / `@UserMessage`** 各一处。 |
| ★★ | `BookingNotFoundException` 最终如何 **变成 HTTP 状态码**？ |
| ★★★ | 若 **`BookingTools` 直连真实航司 API**，你会加 **idempotency key** 放在哪一层？ |

## 4. 项目总结

### 优点

- **全景示例**，利于 **架构评审与估时**。  
- 含 **测试**雏形，促进 **质量文化**。

### 缺点

- **复杂度高**，新人需配合前序章节。  
- **演示域**与贵司 **限界上下文** 不同，需 **改写**而非复制。

### 适用场景

- PoC 验收标准模板。  
- **跨职能工作坊**（产品/研发/运维）对齐语言。

### 注意事项

- **PII** 日志脱敏。  
- **外部模型**条款允许 **生产客服**与否。

### 常见踩坑

1. **直连接口无鉴权**对外开放。  
2. **工具实现**未 **幂等**导致重复扣款。  
3. **无预算上限**上线。

---

### 本期给测试 / 运维的检查清单

**测试**：**e2e** 覆盖典型路径 + **工具失败注入**；评估 **LLM judge** 成本与 **稳定性**。  
**运维**：**限流**、**熔断**、**人工升级**队列监控；**密钥**分环境。

### 附录：相关 Maven 模块与源码类

| 模块 | 说明 |
|------|------|
| `spring-boot-example` 对比 | 通用模式 |
| `customer-support-agent-example` | 领域 Agent |

推荐阅读：`CustomerSupportAgent`、`BookingTools`、`CustomerSupportAgentIT`。
