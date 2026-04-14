# 第 10 章：ChatMemory —— 会话如何在内存中折叠

## 1. 项目背景

聊天应用若每次请求都「从零开始」，模型就不知道用户上一句说过什么。把 **历史消息**原封不动全发给模型，既费钱又可能超出 **上下文窗口**。`ChatMemory` 的职责是在「**保留足够上下文**」与「**控制长度**」之间折中：`MessageWindowChatMemory` 按条数截断，`TokenWindowChatMemory` 则按 **估算 token 数**截断，更贴近计费与模型上限。

值得注意的是：官方教程中 `_05_Memory.java`（`langchain4j-examples/tutorials/src/main/java/_05_Memory.java`）实际演示的是 **`TokenWindowChatMemory` + `OpenAiStreamingChatModel` + `StreamingChatResponseHandler`**——比「纯同步、按条数窗口」更进一步。本章以该文件为准，讲清 **memory 与消息列表如何传给 model**。

## 2. 项目设计：大师与小白的对话

**小白**：`MessageWindow` 和 `TokenWindow` 选谁？

**大师**：**短对话、快速 PoC** 用条数简单；**要贴近账单/上限** 用 token。混合超长 system prompt 时，token 窗更不容易「无声爆窗」。

**小白**：`SystemMessage` 会被挤掉吗？

**大师**：取决于实现策略与插入顺序。**务必将关键政策**放在不易被挤出位置，或拆成 **短 system + RAG**，不要指望无限上下文。

**小白**：为什么示例用流式？

**大师**：教学想一次展示 **memory + streaming** 两个高频能力；业务里也可全同步，只是体验不同。

**小白**：`OpenAiTokenCountEstimator` 准吗？

**大师**：是 **估算**，与供应商 tokenizer 可能略有偏差——用于「窗口感知」足够，**不用于财务对账**。

**小白**：多用户怎么隔离 memory？

**大师**：每会话一个 **memory 实例** 或 **memoryId** 映射到外置 store；**不要**单例共享（第 13 章持久化）。

**小白**：截断会不会把「工具刚返回的结果」丢掉？

**大师**：会。Agent 管线要设计 **摘要**或 **把关键结论写回 user/assistant** 的可恢复格式。

**小白**：如何调试「模型忘了约束」？

**大师**：打印 **进入 model 前的消息列表**（脱敏后），先看是 **memory 截断** 还是 **检索失败**。

## 3. 项目实战：主代码片段

> **场景入戏**：`ChatMemory` 像 **Instagram Stories**——只保留最近几幕（条数或 token），旧剧情自动沉底。**Token 窗**比较真实，像 **手机流量**：说没就没。

核心脉络（节选，教学代码略去异常签名）：

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

// 第二轮追问 ...
```

`streamChat` 里本质是 **把整段 memory 序列**交给流式模型：

```java
model.chat(chatMemory.messages(), handler);
```

**仓库锚点**：[`_05_Memory.java`](../../langchain4j-examples/tutorials/src/main/java/_05_Memory.java)（本示例是 **流式 + Token 窗**，别与「纯同步 MessageWindow」搞混）。

#### 闯关任务

| 难度 | 操作 | 你会惊呼 |
|------|------|----------|
| ★ | `maxTokens` 从 `1000` → `50`，跑第二轮追问 | **「我是谁我在哪」**——窗口暴降的 **失忆艺术** |
| ★★ | 在 `SystemMessage` 里塞 **超长**政策（仍 \< 窗），再聊用户短句 | 体会 **系统提示**与 **用户内容** 抢**同一流量包** |
| ★★★ | 打印 **每次 `messages()` 的 JSON 行数**（脱敏） | 为 **第 13 章持久化** 做 **体积预估** |

#### 挖深一层

- **`OpenAiTokenCountEstimator`**：**估算≠tokenizer 真值**，财务对账仍以**厂商账单**为准。  
- **并发会话**：内存实例 **必须每用户隔离**——Spring 里多用 **prototype + session 映射**（第 34 章）。  
- **工具 + 记忆**：若一轮里工具返回大 JSON，**可能瞬间吃掉窗口**——考虑 **摘要**或 **外存**。

## 4. 项目总结

### 优点

- **显式消息序列**，便于单元测试构造对话。  
- **Token 窗**更贴近真实约束。

### 缺点

- **估算误差**存在边界情况。  
- **长任务**需要额外「摘要记忆」方案。

### 适用场景

- 多轮客服、助手迭代澄清。  
- 「短上下文 + RAG」的主流架构。

### 注意事项

- **会话隔离**与 **线程安全**（Web 并发）。  
- **系统提示**与 **合规** 审计。

### 常见踩坑

1. **单例 ChatMemory** 串话。  
2. **只看条数不看 token** 导致隐秘超窗。  
3. **工具链路与 memory** 顺序错误。

---

### 本期给测试 / 运维的检查清单

**测试**：并发场景下每线程独立 memory；对 **截断边界** 构造「长 system + 长 user」用例。  
**运维**：监控 **每次请求消息条数与 token 估算**；为 **会话内存泄漏**（Map 里堆积 memory）设上限与淘汰。

### 附录：相关 Maven 模块与源码类

| 模块 | 说明 |
|------|------|
| `langchain4j-core`、`langchain4j` | `ChatMemory`、`TokenWindowChatMemory`、`MessageWindowChatMemory` |

推荐阅读：`_05_Memory.java`、`ChatMemory`、流式 handler 文档。
