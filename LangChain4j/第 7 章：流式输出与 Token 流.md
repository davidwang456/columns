# 第 7 章：流式输出与 Token 流

## 1. 项目背景

### 业务场景（拟真）

产品侧要求聊天界面 **「打字机效果」**：首字时间（TTFT）比总耗时更影响留存。同步 `ChatModel.chat(String)` 必须等整段生成完才返回；**流式 API** 通过 `StreamingChatResponseHandler`（或 WebFlux `Flux`）推送 **partial response**。**Token 估算** 则帮助评估费用与是否逼近上下文窗口。

### 痛点放大

若全线用同步接口：**体验**上用户长时间盯转圈；**架构**上在 Servlet 线程里错误 **阻塞** 会拖垮吞吐；**网关**若未调 **SSE/长连接超时**，「流式」会变成 **缓冲后一次性吐出**。教程 `_04_Streaming.java` 演示 `OpenAiStreamingChatModel` + `onPartialResponse` + `onCompleteResponse`，是理解 **流式生命周期** 的第一块拼图。

## 2. 项目设计：小胖、小白与大师的对话

**小胖**：流式是不是网速更快？像 5G 下载？

**小白**：流式总时间是不是更短？`CompletableFuture` 在这里干什么？**部分响应要拼接吗？**

**大师**：**总时间往往相近**；快的是 **感知**——边算边推。`CompletableFuture` 在教程里把异步回调 **join 成同步 main**；Web 里通常 **join 在请求线程** 是反模式。**onPartialResponse** 多为 **增量片段**（以 provider 为准）；展示可直接 print；**持久化全文**以 `onCompleteResponse` 的 `ChatResponse` 为准。**技术映射**：**流式优化 TTFT，不保证总时长下降**。

**小胖**：流一半红了咋办？网关会搞黄流式吗？

**小白**：为何要打印 token 估计？**测试流式怎么断言？**

**大师**：`onError` 要 **关 UI、打点**；网关常见 **超时、缓冲、HTTP/2** 问题——运维要调 **`proxy_read_timeout`** 等（第 34 章）。token 估计建立 **代价直觉**。测试收集 **完整响应与分块次数**，对分块 **只设上下界**，不逐字快照。**技术映射**：**观测 = TTFT + 失败率 + 分块数**。

**小胖**：那我在 **响应式线程** 上 `join()` 呢？

**大师**：易 **阻塞事件循环**；应把阻塞模型隔离到 **worker** 或 **背压队列**。**技术映射**：**线程模型与流式消费要匹配**。

---

## 3. 项目实战

### 环境准备

- [`_04_Streaming.java`](../../langchain4j-examples/tutorials/src/main/java/_04_Streaming.java)；有效 Key。  
- 可选：`spring-boot-example` 的 `StreamingAssistant` + `curl -N`。

### 分步实现

```java
OpenAiStreamingChatModel model = OpenAiStreamingChatModel.builder()
        .apiKey(ApiKeys.OPENAI_API_KEY)
        .modelName(GPT_4_O_MINI)
        .build();

CompletableFuture<ChatResponse> futureChatResponse = new CompletableFuture<>();

model.chat(prompt, new StreamingChatResponseHandler() {

    @Override
    public void onPartialResponse(String partialResponse) {
        System.out.print(partialResponse);
    }

    @Override
    public void onCompleteResponse(ChatResponse completeResponse) {
        System.out.println("\n\nDone streaming");
        futureChatResponse.complete(completeResponse);
    }

    @Override
    public void onError(Throwable error) {
        futureChatResponse.completeExceptionally(error);
    }
});

futureChatResponse.join();
```

```java
new OpenAiTokenCountEstimator(GPT_4_O_MINI).estimateTokenCountInText(prompt)
```

| 步骤 | 目标 | 操作 |
|------|------|------|
| 1 | 认识 chunk | `onPartialResponse` 里 **count++** |
| 2 | 输入成本 | 超长 prompt 对比 **估算 token** 与延迟 |
| 3 | 网关 | `curl -N` 调 SSE，对比 **缓冲 vs 真流** |

**可能遇到的坑**：**背压**——partial 里写 DB 慢于吐字；**用 partial 拼接做审计**——编码边界可能错，应以 **complete** 为准。

### 测试验证

- 契约测试 **error 路径** 有用户可见反馈；负载测 **首包 P95** 与 **完整成功率**。

### 完整代码清单

[`_04_Streaming.java`](../../langchain4j-examples/tutorials/src/main/java/_04_Streaming.java)；Spring：`StreamingAssistant.java`、`AssistantController`。

---

## 4. 项目总结

### 优点与缺点（与同类做法对比）

| 维度 | Streaming + Handler | 同步 chat | SSE 手写 HTTP |
|------|----------------------|-----------|-----------|
| TTFT 体验 | 优 | 差 | 视实现 |
| 复杂度 | 中 | 低 | 高 |
| 测试 | 需上下界 | 易 | 中 |
| 典型缺点 | 网关/线程坑 | 体验差 | 重复造轮子 |

### 适用场景

- 聊天 UI、实时写作、长报告草稿；**边生成边展示引用**（配合 RAG）。

### 不适用场景

- **批处理离线生成**、无需交互——同步更简单。  
- **强依赖严格逐 token 断句**（某些语言模型 chunk 边界非自然语言边界）——产品勿假设 chunk=语义单元。

### 注意事项

- **背压**与下游消费；**审计**存 **最终全文**。

### 常见踩坑经验（生产向根因）

1. **未处理 onError** → 静默失败。  
2. **网关默认缓冲** → 「假流式」。  
3. **在响应式线程阻塞 `join()`** → 拖垮吞吐。

### 进阶思考题

1. 如何用 **`ChatResponse`** 统一拿到 **usage**，与 **流式 partial** 对齐计费？  
2. **Reactor Netty** 上调用阻塞 `ChatModel` 的推荐隔离模式？（提示：第 34 章。）

### 推广计划提示（多部门）

| 角色 | 建议阅读顺序 | 协作要点 |
|------|----------------|----------|
| **开发** | 本章 → 第 34 章 | **禁止**在请求线程错误阻塞 |
| **运维** | 本章 + 网关 | **SSE 超时、限流、断开日志** |
| **测试** | 本章 | **error 路径** 契约 + 分块上下界 |

---

### 本期给测试 / 运维的检查清单

**测试**：契约测试验证 **error 路径** 有用户可见反馈；负载测试关注 **首包时间 P95** 与 **完整响应成功率**。  
**运维**：为 **SSE/流式路由** 单独配置超时、限流与 **断开连接日志**；监控 **中途失败率** 与厂商侧 5xx 关联。

### 附录：相关 Maven 模块与源码类

| 模块 | 说明 |
|------|------|
| `langchain4j-open-ai` | `OpenAiStreamingChatModel` |
| `langchain4j-core` | `StreamingChatResponseHandler`、`ChatResponse` |

推荐阅读：`_04_Streaming.java`、`StreamingAssistant.java`（Spring 示例）、`AssistantController` 流式路由。
