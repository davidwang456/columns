# 第 14 章：Tools —— 让模型「动手」

## 1. 项目背景

语言模型不会「真的」执行 Java 代码，除非你把能力以 **Tool** 形式暴露，并在模型支持 **function/tool calling** 时由运行时解析返回的 **tool invocation** 并执行本地方法。模式是：**模型决定要不要调用**、**用什么参数**；你的代码 **在沙箱边界内**执行并返回结果，再由模型组织自然语言回答。

教程 `_10_ServiceWithToolsExample.java`（`langchain4j-examples/tutorials/src/main/java/_10_ServiceWithToolsExample.java`）用 `@Tool` 注解在 `Calculator` 上声明三个函数，并在 `AiServices` 上 `.tools(new Calculator())`。注意文件内警告：**部分演示 API Key 不支持 tools**——这是上线前必读的一类坑。

## 2. 项目设计：大师与小白的对话

**小白**：工具和传统的「规则引擎」冲突吗？

**大师**：可互补：**模型负责意图与消歧**，工具负责 **确定性与审计**。不要让模型「直接写 SQL」。

**小白**：`strictTools(true)` 是什么？

**大师**：与 **结构化工具输出** 能力相关（见官方 OpenAI 集成文档链接于源码注释）；上线前要对 **各家模型差异**做矩阵。

**小白**：工具方法要有幂等吗？

**大师**：**强烈建议**。模型可能 **重复发起** 同类调用；计费类工具要有 **idempotency key**。

**小白**：参数校验谁做？

**大师**：**你的代码**必须校验；不要相信模型给的参数「天然合法」。

**小白**：工具报错怎么反馈给模型？

**大师**：把 **简短错误摘要** 作为 observation 返回；**不要**把完整堆栈给模型，也少给 **内部 IP**。

**小白**：能调用 HTTP 吗？

**大师**：可以，但应 **封装在带超时与熔断** 的客户端里，而不是裸 `HttpURLConnection`。

**小白**：安全模型？

**大师**：**最小权限** + **每租户工具白名单** + **审计日志**（谁、何时、调了什么）。

## 3. 项目实战：主代码片段

> **场景入戏**：工具调用像 **给模型配乐高机械臂**——它能抓住 **精确数字**（`sqrt`），也能误触 **自毁按钮**（如果你暴露了 `deleteAll()`）。**`@Tool` 描述字符串**就是说明书：写得不清楚，模型 **乱抓**。

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

**仓库锚点**：[`_10_ServiceWithToolsExample.java`](../../langchain4j-examples/tutorials/src/main/java/_10_ServiceWithToolsExample.java)。源码中 **WARNING**：部份 **demo API Key 不支持 tools**——第一次跑通失败，先换 **真 key** 或 **支持 tool 的模型**。

#### 闯关任务

| 难度 | 操作 | 你会笑出声的时刻 |
|------|------|---------------------|
| ★ | 在 `stringLength` 里 **println** 参数，看模型何时调用 | 「模型居然真的在**数数**」 |
| ★★ | 新增 `badTool(int x)` 故意 **抛 RuntimeException**，看错误如何回传给模型 | 体会 **错误摘要** 与 **全栈 trace** 的区别 |
| ★★★ | 实现 `QueryOrderStatus(UUID id)`，非法 UUID **直接抛** | **防御性编程** 是工具第一公民 |

#### 挖深一层

- **`strictTools(true)`**（若启用）：与 **JSON Schema** 约束相关，见官方 OpenAI 集成小节——**版本差异**要矩阵测试。  
- **幂等**：同一问题重试可能 **二次执行**——扣款类接口必须 **业务幂等键**。  
- **副作用**：工具里的 **日志和审计** 是 **合规生命线**。

## 4. 项目总结

### 优点

- 将 **LLM 推理**与 **事务系统** cleanly 分离。  
- Java 注解让能力 **自描述**（供模型生成 JSON schema）。

### 缺点

- **调试链路长**：模型→工具→再模型。  
- **安全与滥用**风险显著高于纯问答。

### 适用场景

- 计算、查询内部 API、创建工单。  
- 需要 **精确数字** 时避免模型心算。

### 注意事项

- **严格权限**与 **审计**。  
- **Demo Key** 与 **生产模型能力** 对齐。

### 常见踩坑

1. **未校验参数**导致数据越权。  
2. 把 **长时间阻塞 IO** 放在同步工具方法里拖死线程池。  
3. 工具 **命名含糊** 让模型误召。

---

### 本期给测试 / 运维的检查清单

**测试**：契约测试 **JSON schema**；模糊 **参数类型**；重放 **重复工具调用**。  
**运维**：为工具 RT 与失败率 **单独 dashboard**；敏感工具 **双人审批**。

### 附录：相关 Maven 模块与源码类

| 模块 | 说明 |
|------|------|
| `langchain4j` | `@Tool`、`ToolService`、`DefaultToolExecutor` 等 |

推荐阅读：`_10_ServiceWithToolsExample.java`、`AssistantTools.java`（Spring 示例）、官方「OpenAI tools/strict」文档。
