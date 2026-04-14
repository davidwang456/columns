# 第 11 章：Few-shot —— 用示例约束输出

## 1. 项目背景

「教模型按某种格式回答」并不一定要训练新模型。**In-context learning** 通过在提示里放若干 **(用户样例 → 期望助理样例)** 对话，让模型模仿风格与动作（例如先给 `Action:` 再给 `Reply:`）。在企业系统里，这常用于：**工单分类**、**客服话术**、**敏感场景的标准操作流程**。相比微调，few-shot **迭代快**、**成本低**；缺点是占用 **上下文 token**，且样例若过时会把错误放大。

教程 `_06_FewShot.java`（`langchain4j-examples/tutorials/src/main/java/_06_FewShot.java`）用 `List<ChatMessage>` 手工构造多组 **正/负反馈** 样例，最后追加真实用户消息，并用 **流式**输出。它与第 10 章衔接：本质是 **同一组消息列表**，只是前半段被当作「教科书」。

## 2. 项目设计：大师与小白的对话

**小白**：few-shot 和「系统提示里写规则」有什么区别？

**大师**：规则适合 **硬约束**；few-shot 适合 **示范边界与语气**。实践中常 **两者并用**：系统提示写政策，few-shot 展示如何处理边缘工单。

**小白**：样例要覆盖多少类？

**大师**：遵循 **质量 > 数量**；覆盖你的 **故障模式**（负面情绪、技术名词、混合语言），而不是堆一百条。

**小白**：样例会不会泄露客户数据？

**大师**：**会**。要用 **合成/脱敏** 样例，并对仓库做 **秘密扫描**。

**小白**：能替代 JSON Schema / 工具调用吗？

**大师**：不能完全替代。需要 **稳定机器可读** 输出时，应使用 **结构化输出**、**工具** 等更强约束（第 14、17 章）。

**小白**：为什么示例用 streaming？

**大师**：与产品形态一致；few-shot 亦可全同步，无本质冲突。

**小白**：如何评测 few-shot 改了有没有更好？

**大师**：固定 **黄金集** + **盲评** + **关键字段命中率**；不要只看「读起来顺不顺」。

**小白**：负例要给几条？

**大师**：至少覆盖 **你最怕的误分类路径**；太少模型学不到边界，太多噪声干扰。

## 3. 项目实战：主代码片段

> **场景入戏**：Few-shot 像给模型看 **三道例题再考期末**——例题若为 **错题集**，它也学错题；例题若 **太长**，考卷（上下文）先爆了。

结构要点：

```java
List<ChatMessage> fewShotHistory = new ArrayList<>();

// 正例
fewShotHistory.add(UserMessage.from("I love the new update! ..."));
fewShotHistory.add(AiMessage.from("Action: forward input to positive feedback storage\nReply: ..."));

// 负例
fewShotHistory.add(UserMessage.from("I am facing frequent crashes ..."));
fewShotHistory.add(AiMessage.from("Action: open new ticket - crash after update Android\nReply: ..."));

// ...

UserMessage customerComplaint = UserMessage.from("How can your app be so slow? ...");
fewShotHistory.add(customerComplaint);

model.chat(fewShotHistory, streamingHandler);
```

**仓库锚点**：[`_06_FewShot.java`](../../langchain4j-examples/tutorials/src/main/java/_06_FewShot.java)（注意整段历史 **当作一次多轮消息列表** 发送）。

#### 闯关任务

| 难度 | 玩法 | 深度收获 |
|------|------|----------|
| ★ | 把 `Action:` 改成枚举码 `POS_FEEDBACK`，下游 **正则截取** | **自然语言路由** → **解析型路由**，便于测试 |
| ★★ | 删掉一半负例，只留正例，看分类是否 **偏乐观** | 理解 **类不均衡** 与客服事故 |
| ★★★ | 把样例译成中文再跑（模型仍用英文 `Action`） | 体会 **多语言 few-shot** 与 **token 膨胀** |

#### 挖深一层

- **与工具调用区别**：Few-shot 是 **软模仿**；`@Tool` 是 **硬执行**——高风险动作应走工具 + **权限**。  
- **streaming**：示例用流式打印「**像真人打字**」；集成测试时改用 **收集完整 `ChatResponse`**。  
- **隐私**：样例勿贴 **客户原话**进 Git——用 **合成数据**。

## 4. 项目总结

### 优点

- **快速迭代**意图与语气。  
- 与现有 **ChatMessage** 模型无缝。

### 缺点

- **token 消耗**随样例线性增加。  
- **样例偏见**会传导到线上。

### 适用场景

- 客服分流、评论情感路由、轻量分类。  
- PoC 阶段 **尚未确定 Schema** 的探索。

### 注意事项

- **多语言**样例一致性与翻译审阅。  
- **样例版本**与提示版本共同变更。

### 常见踩坑

1. 样例 **互相矛盾**。  
2. **未脱敏**粘贴真实对话进 Git。  
3. 把 few-shot 当「安全护栏」—— **无法防对抗样本**。

---

### 本期给测试 / 运维的检查清单

**测试**：对抗性输入绕过 `Action:` 行；度量 **格式有效率**；对样例变更做 **回归集**。  
**运维**：监控 **输入长度分布**；当 few-shot 变长导致 **延迟/成本**跳变时告警。

### 附录：相关 Maven 模块与源码类

| 模块 | 说明 |
|------|------|
| `langchain4j-core` | `UserMessage`、`AiMessage`、`ChatMessage` |

推荐阅读：`_06_FewShot.java`、与第 12 章 `AiServices` 的对比阅读。
