# 第 10 章：ChatMemory —— 会话如何在内存中折叠

## 1. 项目背景

### 业务场景（拟真）

多轮客服场景中，若每次请求都从零开始，模型不知道用户上一句说过什么；若 **历史原样全发**，则 **费钱** 且可能超出 **上下文窗口**。产品要求「**记得刚才的订单号**」，但 **不能无限堆消息**。

### 痛点放大

`ChatMemory` 在 **保留上下文** 与 **控制长度** 之间折中：`MessageWindowChatMemory` 按条数；`TokenWindowChatMemory` 按 **估算 token**，更贴近计费与上限。没有记忆策略时：**成本**不可控；**体验**上模型「失忆」；**调试**困难——不知道是 **截断** 还是 **检索失败**。教程 `_05_Memory.java` 演示 **`TokenWindowChatMemory` + 流式**，以该文件为准。

## 2. 项目设计：小胖、小白与大师的对话

**小胖**：记忆像朋友圈只显示最近三天？

**小白**：`MessageWindow` 和 `TokenWindow` 选谁？**SystemMessage 会被挤掉吗？**

**大师**：短对话 PoC 用条数；要贴近 **账单/上限** 用 token。混合超长 system 时 **token 窗**更不易「无声爆窗」。关键政策 **不要指望无限上下文**——可 **短 system + RAG**。**技术映射**：**窗口策略 = 条数近似 vs token 近似**。

**小胖**：示例为啥用流式？

**小白**：`OpenAiTokenCountEstimator` 准吗？**多用户怎么隔离？截断会丢掉工具结果吗？**

**大师**：教学一次展示 **memory + streaming**；业务可全同步。估计是 **估算**，**财务对账以厂商账单为准**。每会话 **独立 memory 实例或 memoryId**；**勿单例共享**（第 13 章）。工具返回的大 JSON 可能 **瞬间吃窗**——要 **摘要或外存**。**技术映射**：**记忆与工具链顺序 = Agent 可靠性关键**。

**小胖**：模型「忘了约束」咋 debug？

**大师**：**脱敏打印**进入 model 前的 `messages()`，区分 **截断 vs 检索失败**。**技术映射**：**可观测进模型前的消息列表**。

---

## 3. 项目实战

### 环境准备

- [`_05_Memory.java`](../../langchain4j-examples/tutorials/src/main/java/_05_Memory.java)。

### 分步实现

```java
OpenAiStreamingChatModel model = OpenAiStreamingChatModel.builder()
        .apiKey(ApiKeys.OPENAI_API_KEY)
        .modelName(GPT_4_O_MINI)
        .build();

ChatMemory chatMemory = TokenWindowChatMemory.withMaxTokens(1000, new OpenAiTokenCountEstimator(GPT_4_O_MINI));

chatMemory.add(SystemMessage.from("You are a senior developer ..."));

UserMessage userMessage1 = userMessage("How do I optimize database queries ...?");
chatMemory.add(userMessage1);

AiMessage aiMessage1 = streamChat(model, chatMemory);
chatMemory.add(aiMessage1);
```

```java
model.chat(chatMemory.messages(), handler);
```

| 步骤 | 目标 | 操作 |
|------|------|------|
| 1 | 感受截断 | `maxTokens` 1000 → 50，跑第二轮追问 |
| 2 | system 与用户抢窗 | 超长 system + 短 user |
| 3 | 体积预估 | 打印每次 `messages()` 行数（脱敏） |

**可能遇到的坑**：**单例 ChatMemory 串话**；**只看条数不看 token**；**工具 JSON 撑爆窗口**。

### 测试验证

- 并发 **每线程独立 memory**；截断边界 **长 system + 长 user**。

### 完整代码清单

[`_05_Memory.java`](../../langchain4j-examples/tutorials/src/main/java/_05_Memory.java)（**流式 + Token 窗**）。

---

## 4. 项目总结

### 优点与缺点（与同类做法对比）

| 维度 | Token/Message 窗口 | 全量历史 | 外部摘要服务 |
|------|---------------------|----------|--------------|
| 成本可控 | 高 | 低 | 中 |
| 实现复杂度 | 中 | 低 | 高 |
| 可测性 | 高 | 中 | 中 |
| 典型缺点 | 估算误差 | 超窗/贵 | 一致性 |

### 适用场景

- 多轮客服、迭代澄清；**短上下文 + RAG** 主流架构。

### 不适用场景

- **单轮 FAQ**、无需上下文——可不用 memory。  
- **强依赖精确 token 窗对账**——估算不能替代 **厂商账单**。

### 注意事项

- **会话隔离**与 **线程安全**；**合规审计** system 内容。

### 常见踩坑经验（生产向根因）

1. **单例 ChatMemory** 串话。  
2. **只看条数** 导致隐秘超窗。  
3. **工具链路与 memory** 顺序错误。

### 进阶思考题

1. Spring 中 **prototype Bean + session 映射** 如何避免 **内存 Map 泄漏**？（提示：第 34 章。）  
2. 何时引入 **滚动摘要** 而非单纯截断？（提示：第 13 章。）

### 推广计划提示（多部门）

| 角色 | 建议阅读顺序 | 协作要点 |
|------|----------------|----------|
| **开发** | 本章 → 第 13 章 | **勿共享** memory 实例 |
| **运维** | 监控 token 估算与消息条数 | **会话 Map** 上限与淘汰 |
| **测试** | 并发 + 截断边界 | **长 system + 长 user** |

---

### 本期给测试 / 运维的检查清单

**测试**：并发场景下每线程独立 memory；对 **截断边界** 构造「长 system + 长 user」用例。  
**运维**：监控 **每次请求消息条数与 token 估算**；为 **会话内存泄漏**（Map 里堆积 memory）设上限与淘汰。

### 附录：相关 Maven 模块与源码类

| 模块 | 说明 |
|------|------|
| `langchain4j-core`、`langchain4j` | `ChatMemory`、`TokenWindowChatMemory`、`MessageWindowChatMemory` |

推荐阅读：`_05_Memory.java`、`ChatMemory`、流式 handler 文档。
