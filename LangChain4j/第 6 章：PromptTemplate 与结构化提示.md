# 第 6 章：PromptTemplate 与结构化提示

## 1. 项目背景

### 业务场景（拟真）

某 B 端产品在迭代「**政策 + 用户问题**」类提示时，开发用字符串拼接：`"你是客服。" + userName + "的问题是..."`。需求一变就要 **全仓库 grep**；多语言、敏感词审查、**政策段落与用户输入隔离** 无法版本化。维护成本与 **prompt injection** 风险同步上升。

### 痛点放大

**模板**把可变部分变量化，把固定政策放进 **可 Git 管理**的文本。LangChain4j 提供 **`PromptTemplate`**（KV 变量）与 **`@StructuredPrompt` + `StructuredPromptProcessor`**（类字段即变量）。没有模板时：**一致性**上同一政策在多服务 **拷贝漂移**；**安全**上不可信输入与系统指令 **边界模糊**；**可测试性**上难以对 **渲染结果** 做稳定断言。

教程 `_03_PromptTemplate.java` 在同一文件内用**两个静态内部类**示范两种风格，是对照学习的最佳入口。

## 2. 项目设计：小胖、小白与大师的对话

**小胖**：`{{dishType}}` 像填空题——用户填「黑暗料理」会不会把系统搞崩？

**小白**：语法是 Mustache 吗？用户输入里有 **花括号** 怎么办？`StructuredPrompt` 比 Map 好在哪里？

**大师**：占位符语法以 **`PromptTemplate` Javadoc 与单测** 为准，勿假设与某前端引擎 100% 兼容。不可信输入要 **转义**或 **单独变量**并由模板边界约束；勿把长文直接拼进 **系统策略**。**技术映射**：**模板语法 = 实现细节，以官方为准**。

**小胖**：模板放数据库还是 Git？

**小白**：这和 **SQL 注入** 是一回事吗？多语言怎么组织？

**大师**：**Git** 适合审查与 diff；数据库适合 **运营即时改文案**，但要 **审计、灰度、回滚**。精神类似注入：**不可信输入不应改变系统指令边界**——测试需 **恶意指令样例**（第 14、36 章）。多语言用 **资源文件 + locale** 或 **DB 按语言 key**。**技术映射**：**Prompt injection ≈ 指令边界问题**。

**小胖**：变量缺了会怎样？

**大师**：`apply` 时常 **抛错或未替换占位符**——比静默失败好；集成测试要覆盖。**结构化提示**字段有类型，IDE 可重构——适合 **强类型团队**。**技术映射**：**StructuredPrompt = 以类为 schema 的提示**。

---

## 3. 项目实战

### 环境准备

- `langchain4j-examples/tutorials`；[`_03_PromptTemplate.java`](../../langchain4j-examples/tutorials/src/main/java/_03_PromptTemplate.java)。

### 分步实现

**3.1 `PromptTemplate` + Map**

```java
String template = "Create a recipe for a {{dishType}} with the following ingredients: {{ingredients}}";
PromptTemplate promptTemplate = PromptTemplate.from(template);

Map<String, Object> variables = new HashMap<>();
variables.put("dishType", "oven dish");
variables.put("ingredients", "potato, tomato, feta, olive oil");

Prompt prompt = promptTemplate.apply(variables);

String response = model.chat(prompt.text());
```

**3.2 `@StructuredPrompt` → `StructuredPromptProcessor`**

```java
@StructuredPrompt({
        "Create a recipe of a {{dish}} that can be prepared using only {{ingredients}}.",
        "Structure your answer in the following way:",
        // ...
})
static class CreateRecipePrompt {
    String dish;
    List<String> ingredients;
}

Prompt prompt = StructuredPromptProcessor.toPrompt(createRecipePrompt);
String recipe = model.chat(prompt.text());
```

| 步骤 | 目标 | 操作 |
|------|------|------|
| 1 | 占位符失败行为 | **故意漏掉** Map 里一个 key，观察 `apply` 异常 |
| 2 | 政策与变量分层 | 模板顶部追加固定合规句，不涉及变量 |
| 3 | 结构化 + 外部配置 | `StructuredPrompt` + 自 JSON 反序列化对象再 `toPrompt` |

**可能遇到的坑**：**注入对抗**——用户塞「忽略上文」；**注解过长**可读性下降，可外置 `.txt` + CI 校验占位符。

### 测试验证

- 至少测 **`apply` 后文本形态**：关键政策句仍在、变量位置正确。  
- 等价类：**缺变量、空串、极长 ingredients、含尖括号**。

### 完整代码清单

**仓库锚点**：[`langchain4j-examples/tutorials/src/main/java/_03_PromptTemplate.java`](../../langchain4j-examples/tutorials/src/main/java/_03_PromptTemplate.java)（两个 `static` 内部类各有一个 `main`）。

---

## 4. 项目总结

### 优点与缺点（与同类做法对比）

| 维度 | PromptTemplate / StructuredPrompt | 字符串拼接 | 外部 Python 模板服务 |
|------|-----------------------------------|------------|----------------------|
| 可维护性 | 高（变量与政策分离） | 低 | 中（跨语言） |
| 类型安全 | Structured 强 / Map 弱 | 无 | 视接口 |
| 版本治理 | Git 友好 | 差 | 需审计表 |
| 典型缺点 | 注解过长难读 | 注入与漂移 | 运维与延迟 |

### 适用场景

- 多租户同骨架不同语气；**强约束输出格式** 的报告/抽取。  
- 需要 **Code Review** 能 diff 提示词时。

### 不适用场景

- **极短一次性 demo**——直接字符串即可。  
- **运营每分钟改提示且无法走发布流程**——需配套 **审计与回滚**，否则不用 DB 也会失控。

### 注意事项

- **PII** 在日志中脱敏；模板变更与 **模型降级** 同事务单。

### 常见踩坑经验（生产向根因）

1. **变量名与 Map key 不一致**。  
2. **不可信长文** 与 `SystemMessage` 政策混写。  
3. **多环境法规不同** 未分支。

### 进阶思考题

1. 如何在 **CI** 中校验 **外置 `.txt` 模板** 的占位符与 **代码中的变量集** 一致？  
2. **用户输入进入 `{{ingredients}}`** 时，除模板外还需哪一层 **护栏**？（提示：第 36 章。）

### 推广计划提示（多部门）

| 角色 | 建议阅读顺序 | 协作要点 |
|------|----------------|----------|
| **开发** | 本章 → 第 17 章 | 模板 **版本号** 与模型 **版本** 绑定发布 |
| **测试** | 本章 | 快照 **只锁渲染结果**，不锁模型自然语言 |
| **运维** | 配置中心托管模板时 | **变更审批 + 回滚上一版本** |

---

### 本期给测试 / 运维的检查清单

**测试**：等价类覆盖「缺变量」「空串」「极长 ingredients」「含 HTML/尖括号」；快照测试只锁定 **模板渲染结果**，不锁定模型自然语言输出。  
**运维**：若模板外置到配置中心，建立 **变更审批 + 仅运维发布** 流程，并保留 **回滚上一个版本** 的能力。

### 附录：相关 Maven 模块与源码类

| 模块 | 说明 |
|------|------|
| `langchain4j-core` | `PromptTemplate`、`Prompt`、`StructuredPromptProcessor` |

推荐阅读：`_03_PromptTemplate.java`、`PromptTemplate`、`StructuredPrompt` 注解定义。
