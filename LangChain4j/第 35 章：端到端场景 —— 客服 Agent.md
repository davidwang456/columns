# 第 35 章：端到端场景 —— 客服 Agent

## 1. 项目背景

### 业务场景（拟真）

教程各教一个点；真实 **客服域** 要同时：**理解诉求**、**调订单/票务 API**、**引用政策文档**、**多轮记忆**。`customer-support-agent-example` 将能力 **收口在 Spring Boot** 中，适合 **内训从点到线**。

### 痛点放大

入口：`CustomerSupportAgentApplication.java`；**集成测试**（`CustomerSupportAgentIT`）与 **判题断言**（`JudgeModelAssertions`）提示 **生产级也要自动化测试**。若 **工具无鉴权**、**无幂等**、**无预算**——**资金与合规** 双爆。

## 2. 项目设计：小胖、小白与大师的对话

**小胖**：这比普通 Spring 示例牛在哪？

**小白**：和 `spring-boot-example` 差别？**Agent 一定比单 RAG 好**？**人工接管**？

**大师**：客服示例贴近 **业务名词、工具分层、领域异常**。**Agent 能行动** 但 **故障面更大**——**工具权限与提示** 是第一道门。**升级人工**：工具 `escalateToHuman` 或 **置信度/政策关键词** 中断。**技术映射**：**系统提示 + 工具事实 > 模型嘴炮**。

**小胖**：`BookingTools` 干啥的？

**小白**：测试里 **判题模型** 干啥？**防乱承诺退款**？

**大师**：演示 **领域服务** 被模型驱动；真实应对齐 **核心聚合根**。**开放式输出** 用 **弱断言 + LLM judge**（注意成本与偏见）。**金额/期限** **代码校验** 后再展示。**traceId + 结构化工具日志**（第 36 章）。**技术映射**：**多语言 = 检测语言 → 路由知识库 → 同语言回应**。

---

## 3. 项目实战

### 环境准备

- [`customer-support-agent-example`](../../langchain4j-examples/customer-support-agent-example)。

### 分步阅读顺序

1. [`CustomerSupportAgent.java`](../../langchain4j-examples/customer-support-agent-example/src/main/java/dev/langchain4j/example/CustomerSupportAgent.java)  
2. [`CustomerSupportAgentConfiguration.java`](../../langchain4j-examples/customer-support-agent-example/src/main/java/dev/langchain4j/example/CustomerSupportAgentConfiguration.java)  
3. [`BookingTools.java`](../../langchain4j-examples/customer-support-agent-example/src/main/java/dev/langchain4j/example/BookingTools.java)  
4. [`CustomerSupportAgentController.java`](../../langchain4j-examples/customer-support-agent-example/src/main/java/dev/langchain4j/example/CustomerSupportAgentController.java)  
5. [`CustomerSupportAgentIT.java`](../../langchain4j-examples/customer-support-agent-example/src/test/java/dev/langchain4j/example/CustomerSupportAgentIT.java)

画时序图：`User HTTP` → `Controller` → `Agent` → (`ChatModel` / `Tools` / `Memory`)。

| 闯关 | 任务 |
|------|------|
| ★ | 圈出 `@SystemMessage` / `@UserMessage` |
| ★★ | `BookingNotFoundException` → **HTTP 状态码** |
| ★★★ | 真航司 API 时 **idempotency key** 放哪层 |

### 测试验证

- **e2e** + **工具失败注入**；评估 **LLM judge** 成本。

### 完整代码清单

见 `customer-support-agent-example` 仓库。

---

## 4. 项目总结

### 优点与缺点（与同类做法对比）

| 维度 | 客服 Agent 示例 | 仅 RAG | 仅 Tools |
|------|-------------------|--------|----------|
| 全景 | 高 | 偏一 | 偏一 |
| 复杂度 | 高 | 中 | 中 |
| 典型缺点 | 需改写域模型 | 不能行动 | 无知识 |

### 适用场景

- PoC 验收模板；**跨职能工作坊**。

### 不适用场景

- **与贵司限界上下文完全不同**——勿复制粘贴。

### 注意事项

- **PII**：日志脱敏；**外部模型条款** 是否允许生产客服。

### 常见踩坑经验（生产向根因）

1. **直连接口无鉴权** 对外开放。  
2. **工具未幂等** → 重复扣款。  
3. **无预算上限** 上线。

### 进阶思考题

1. **JudgeModel** 的 **偏见** 如何 **监控**？  
2. **政策文档版本** 与 **BookingTools** 返回 **如何仲裁**？

### 推广计划提示（多部门）

| 角色 | 建议阅读顺序 | 协作要点 |
|------|----------------|----------|
| **开发** | 第 12～14 章 → 本章 | **领域边界** |
| **测试** | IT + judge | **成本** |
| **运维** | 限流熔断 | **人工队列** |

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
