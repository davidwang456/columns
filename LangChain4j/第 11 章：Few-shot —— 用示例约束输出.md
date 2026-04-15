# 第 11 章：Few-shot —— 用示例约束输出

## 1. 项目背景

### 业务场景（拟真）

工单系统要把用户留言 **自动分流**：表扬走反馈库，崩溃走缺陷工单，辱骂走风控。产品希望 **不改模型、不微调**，一周内上线——**In-context learning** 通过在提示里放若干 **(用户 → 助理)** 样例，让模型模仿 **格式与动作**（如先 `Action:` 再 `Reply:`）。

### 痛点放大

Few-shot **迭代快、成本低**，但占用 **上下文 token**；样例 **过时或含偏见** 会放大错误。与「仅系统规则」相比：规则适合 **硬约束**，样例适合 **边界与语气**。教程 `_06_FewShot.java` 用 `List<ChatMessage>` 构造正/负样例，再追加真实用户消息，与第 10 章衔接——**前半段是教科书**。

## 2. 项目设计：小胖、小白与大师的对话

**小胖**：这跟背作文模板一样？多背几篇分数高？

**小白**：few-shot 和 **系统规则** 啥区别？样例要几条？**能替代 JSON Schema 吗？**

**大师**：规则 **硬约束**；few-shot **示范边界与语气**——常 **两者并用**。遵循 **质量 > 数量**；覆盖 **故障模式** 而非堆一百条。需要 **稳定机器可读** 输出时，用 **结构化输出、工具**（第 14、17 章），few-shot **不能替代**。**技术映射**：**few-shot = 软模仿，非协议保证**。

**小胖**：样例里能贴真实客户原话吗？

**小白**：为啥示例用 streaming？**怎么评测** 改了有没有更好？负例要几条？

**大师**：**会泄露**——用 **合成/脱敏** + **秘密扫描**。streaming 与产品一致；评测用 **黄金集 + 盲评 + 字段命中率**。负例至少覆盖 **最怕的误分类路径**。**技术映射**：**数据治理与评测集 = few-shot 上线门槛**。

---

## 3. 项目实战

### 环境准备

- [`_06_FewShot.java`](../../langchain4j-examples/tutorials/src/main/java/_06_FewShot.java)。

### 分步实现

```java
List<ChatMessage> fewShotHistory = new ArrayList<>();

fewShotHistory.add(UserMessage.from("I love the new update! ..."));
fewShotHistory.add(AiMessage.from("Action: forward input to positive feedback storage\nReply: ..."));

fewShotHistory.add(UserMessage.from("I am facing frequent crashes ..."));
fewShotHistory.add(AiMessage.from("Action: open new ticket - crash after update Android\nReply: ..."));

UserMessage customerComplaint = UserMessage.from("How can your app be so slow? ...");
fewShotHistory.add(customerComplaint);

model.chat(fewShotHistory, streamingHandler);
```

| 步骤 | 目标 | 操作 |
|------|------|------|
| 1 | 解析型路由 | `Action:` 改枚举码，下游正则截取 |
| 2 | 类不均衡 | 删一半负例，看分类是否偏乐观 |
| 3 | 多语言 | 样例译中文，观察 token 与效果 |

**可能遇到的坑**：样例 **矛盾**；**未脱敏**进 Git；把 few-shot 当 **安全护栏**（无法防对抗）。

### 测试验证

- 对抗输入绕过 `Action:`；**格式有效率**；样例变更 **回归集**。

### 完整代码清单

[`_06_FewShot.java`](../../langchain4j-examples/tutorials/src/main/java/_06_FewShot.java)。

---

## 4. 项目总结

### 优点与缺点（与同类做法对比）

| 维度 | Few-shot | 仅系统提示 | 微调 / LoRA |
|------|----------|------------|-------------|
| 迭代速度 | 快 | 快 | 慢 |
| token 成本 | 随样例升 | 低 | 推理外另有训练 |
| 边界稳定性 | 中 | 中 | 高（视数据） |
| 典型缺点 | 偏见传导 | 缺示范 | 运维复杂 |

### 适用场景

- 客服分流、情感路由、轻量分类；PoC **未定 Schema** 时。

### 不适用场景

- **高风险金融指令**仅靠 few-shot——需 **工具 + 权限**。  
- **样例无法脱敏** 的行业——禁止进仓库。

### 注意事项

- **多语言** 样例一致性；**样例版本** 与提示 **共同变更**。

### 常见踩坑经验（生产向根因）

1. 样例 **互相矛盾**。  
2. **未脱敏** 真实对话进 Git。  
3. 把 few-shot 当 **安全护栏**。

### 进阶思考题

1. 何时将 **Action 行** 收敛为 **枚举 + 工具调用**？（提示：第 14 章。）  
2. **盲评** 如何设计避免 **标注者看到模型名** 产生偏见？

### 推广计划提示（多部门）

| 角色 | 建议阅读顺序 | 协作要点 |
|------|----------------|----------|
| **开发** | 本章 → 第 12 章 | 样例与 **提示版本** 同事务 |
| **测试** | 对抗 + 格式断言 | **回归集** 随样例变 |
| **运维** | 监控输入长度 | 样例变长导致 **成本/延迟** 跳变告警 |

---

### 本期给测试 / 运维的检查清单

**测试**：对抗性输入绕过 `Action:` 行；度量 **格式有效率**；对样例变更做 **回归集**。  
**运维**：监控 **输入长度分布**；当 few-shot 变长导致 **延迟/成本**跳变时告警。

### 附录：相关 Maven 模块与源码类

| 模块 | 说明 |
|------|------|
| `langchain4j-core` | `UserMessage`、`AiMessage`、`ChatMessage` |

推荐阅读：`_06_FewShot.java`、与第 12 章 `AiServices` 的对比阅读。
