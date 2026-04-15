# 第 14 章：Tools —— 让模型「动手」

## 1. 项目背景

### 业务场景（拟真）

客服机器人需要 **查订单、算退款、建工单**——模型不会凭空执行 Java，除非以 **Tool** 暴露方法，且在模型支持 **function/tool calling** 时由运行时 **解析 invocation、执行、再交回模型** 组织自然语言回答。模式是：**模型决定要不要调用、用什么参数**；你的代码在 **沙箱与权限** 内执行。

### 痛点放大

若 **让模型直接写 SQL** 或 **工具无幂等**：**安全**上越权与重复扣费；**稳定性**上重试导致 **二次执行**；**排障**上链长 **模型→工具→模型**。教程 `_10_ServiceWithToolsExample.java` 用 `@Tool` 声明 `Calculator`；源码 **WARNING**：部分 **demo API Key 不支持 tools**。

## 2. 项目设计：小胖、小白与大师的对话

**小胖**：工具是不是给模型装机械臂？抓错东西咋办？

**小白**：工具和 **规则引擎** 冲突吗？**`strictTools(true)`** 是啥？**参数校验谁做？**

**大师**：可互补：**模型消歧，工具给确定性与审计**；不要让模型直接 SQL。`strictTools` 与 **结构化工具输出** 相关（见厂商文档矩阵）。**参数必须由你的代码校验**——模型参数不天然合法。**技术映射**：**Tool = 带审计的副作用边界**。

**小胖**：工具报错会把堆栈给模型吗？

**小白**：能调 **HTTP** 吗？**安全模型**？

**大师**：错误返回 **简短摘要**；**勿给全栈与内网 IP**。HTTP 应 **封装在带超时/熔断** 的客户端。**最小权限 + 租户白名单 + 审计日志**。**技术映射**：**工具错误面 = 信息泄露风险**。

**小胖**：计算器重复按两次会扣两次钱吗？

**大师**：**强烈建议幂等**；计费类要 **idempotency key**——模型可能重复发起。**技术映射**：**工具幂等 = 对抗重试与模型重复**。

---

## 3. 项目实战

### 环境准备

- [`_10_ServiceWithToolsExample.java`](../../langchain4j-examples/tutorials/src/main/java/_10_ServiceWithToolsExample.java)；**支持 tools 的 Key/模型**。

### 分步实现

```java
static class Calculator {

    @Tool("Calculates the length of a string")
    int stringLength(String s) { ... }

    @Tool("Calculates the sum of two numbers")
    int add(int a, int b) { ... }

    @Tool("Calculates the square root of a number")
    double sqrt(int x) { ... }
}

Assistant assistant = AiServices.builder(Assistant.class)
        .chatModel(model)
        .tools(new Calculator())
        .chatMemory(MessageWindowChatMemory.withMaxMessages(10))
        .build();

String answer = assistant.chat(
        "What is the square root of the sum of the numbers of letters in \"hello\" and \"world\"?");
```

| 步骤 | 目标 | 操作 |
|------|------|------|
| 1 | 观察调用 | `stringLength` 里 println 看何时调用 |
| 2 | 错误摘要 | `badTool` 抛异常，看返回模型的信息形态 |
| 3 | 校验 | `QueryOrderStatus(UUID)` 非法 UUID 直接拒绝 |

**可能遇到的坑**：**未校验参数** 越权；**长阻塞 IO** 在工具里拖死线程池；**@Tool 描述不清** 误召。

### 测试验证

- 契约测试 **JSON schema**；**重复工具调用** 重放。

### 完整代码清单

[`_10_ServiceWithToolsExample.java`](../../langchain4j-examples/tutorials/src/main/java/_10_ServiceWithToolsExample.java)；Spring：`AssistantTools.java`。

---

## 4. 项目总结

### 优点与缺点（与同类做法对比）

| 维度 | @Tool + AiServices | 服务端硬编码路由 | 模型内嵌「伪代码」 |
|------|-------------------|------------------|---------------------|
| 确定性 | 高（Java 执行） | 高 | 低 |
| 安全 | 依赖权限设计 | 高 | 低 |
| 调试难度 | 中（链长） | 低 | 高 |
| 典型缺点 | 滥用风险 | 灵活性低 | 不可执行 |

### 适用场景

- 计算、查内部 API、建工单；需要 **精确数字** 避免心算。

### 不适用场景

- **无审批的高危操作**（转账、删库）——需 **人工在环** 或多重审批，不单靠工具描述。

### 注意事项

- **严格权限**与 **审计**；**Demo Key** 与 **生产模型能力** 对齐。

### 常见踩坑经验（生产向根因）

1. **未校验参数** → 数据越权。  
2. **同步工具里长阻塞 IO** → 线程池耗尽。  
3. **工具命名含糊** → 误召。

### 进阶思考题

1. **strictTools** 与 **JSON Schema** 在各厂商上的差异如何 **矩阵测试**？  
2. 如何为 **扣款类工具** 设计 **幂等键** 与 **审计字段**？

### 推广计划提示（多部门）

| 角色 | 建议阅读顺序 | 协作要点 |
|------|----------------|----------|
| **开发** | 本章 → 第 15、17 章 | **工具白名单** 按租户 |
| **测试** | 模糊参数、重放 | **schema 契约** |
| **运维** | 工具 RT/失败率 | **敏感工具双人审批** |

---

### 本期给测试 / 运维的检查清单

**测试**：契约测试 **JSON schema**；模糊 **参数类型**；重放 **重复工具调用**。  
**运维**：为工具 RT 与失败率 **单独 dashboard**；敏感工具 **双人审批**。

### 附录：相关 Maven 模块与源码类

| 模块 | 说明 |
|------|------|
| `langchain4j` | `@Tool`、`ToolService`、`DefaultToolExecutor` 等 |

推荐阅读：`_10_ServiceWithToolsExample.java`、`AssistantTools.java`（Spring 示例）、官方「OpenAI tools/strict」文档。
