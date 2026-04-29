# 第 26 章：高级 RAG —— 何时跳过检索（Skip Retrieval）

## 1. 项目背景

### 业务场景（拟真）

用户进入客服对话的第一句话是「你好」——如果这个请求也要走一遍向量检索（embed query → 查 store → 拼上下文），浪费了时间和 token。类似还有「谢谢」「你是谁」「你能做什么」等无检索价值的问题。**Skip Retrieval** 在决定「要不要进向量库」这一层做二元判断——不需要检索就直接走纯 ChatModel 聊天。

### 痛点放大

一次不必要的向量检索的成本：embed query 的 token 消耗 + 数据库查询的延迟（通常是几十到几百毫秒）+ 召回的片段占据上下文窗口（浪费了空间）。在低并发时看起来不多，但日活百万时，20% 的问候语请求浪费的检索成本相当可观。核心风险在于 **漏掉业务意图**——用户说「嗨，上次那个订单怎么还没发货」——开头是「嗨」但核心意图是查订单，如果跳过检索，模型就看不到订单相关的文档。

## 2. 项目设计：小胖、小白与大师的对话

**小胖**：跳过检索——是不是就像闲聊的时候不去档案室翻资料？人家就说了句「你好」，你还去档案室翻一遍？

**大师**：就是这个意思。但关键在于判断 **哪些该跳过**。最怕的就是「你好，帮我查一下订单 #12345」——开头是「你好」但后面跟着真实业务请求。如果只靠简单的关键词规则（以「你好」开头就跳过），就会漏掉这个业务。所以冷启动阶段的规则要保守——宁可多检索几次浪费一点费用，也不要漏掉业务。积累了足够多的误判样本后，再上轻量二分类模型提升精准率。

**小白**：跳过 RAG 了，但那个请求还应该执行工具调用吗？比如「谢谢，顺便查一下余额」——跳过检索但还是需要调余额查询工具。

**大师**：这是一个很重要的区分：**跳过 RAG ≠ 跳过工具调用**。检索和工具调用是两条独立的决策路径。检索是否跳过只影响「要不要从向量库拿知识文档」，不影响「要不要调余额查询工具」。所以设计上，即使 skip retrieval 判断为「跳过」，后续的工具调用逻辑仍然正常执行。**技术映射**：**skip 决策 = 成本与噪声的开关——省下来的检索费用是 ROI，但漏掉的业务问题就是隐形成本；通过日志回放 + 误跳过标注 + 强制检索按钮来控制风险**。

---

## 3. 项目实战

### 环境准备

```bash
cd langchain4j-examples/rag-examples
export OPENAI_API_KEY="sk-your-key-here"
```

### 分步实现

#### 步骤 1：关键词规则跳过检索

```java
import java.util.regex.Pattern;

public class SkipRetrievalRule {

    // 明显不需要检索的模式
    private static final Pattern SKIP_PATTERNS = Pattern.compile(
        "^(你好|您好|hello|hi|谢谢|再见|bye|help|你是谁|你能做什么|)$",
        Pattern.CASE_INSENSITIVE
    );

    public boolean shouldSkip(String query) {
        return SKIP_PATTERNS.matcher(query.trim()).matches();
    }
}

// 测试
SkipRetrievalRule rule = new SkipRetrievalRule();
System.out.println(rule.shouldSkip("你好"));          // true
System.out.println(rule.shouldSkip("谢谢"));          // true
System.out.println(rule.shouldSkip("帮我查订单"));    // false
System.out.println(rule.shouldSkip("你好，查订单"));  // false（含业务意图）
```

#### 步骤 2：跳过时跳过检索但保留工具

```java
String userQuery = "谢谢，顺便查一下余额";

// Step 1: 判断是否跳过检索
boolean skipRetrieval = rule.shouldSkip(userQuery);  // false，"顺便查余额"含业务

// 但如果是纯粹的"谢谢"
if (rule.shouldSkip("谢谢")) {
    // 跳过检索，但仍然可以调工具（如余额查询）
    String intent = detectIntent("谢谢");
    if (intent.equals("check_balance")) {
        String balance = callBalanceApi();  // 仍然调工具
        return "您的余额为 " + balance;
    }
    // 如果无意图，纯聊天回复
    return "不客气，随时为您服务！";
}
```

#### 步骤 3：对抗测试——闲聊中藏业务

```java
// 构造对抗样本
String[] testCases = {
    "你好",                                          // 纯问候 → skip
    "你好，帮我查一下订单 #12345",                    // 含业务 → no-skip
    "谢谢",                                          // 纯感谢 → skip
    "谢谢，顺便看下我的退款到哪了",                    // 含业务 → no-skip
    "hi",                                             // 问候 → skip
    "在吗？我的订单怎么还没发货",                      // 含业务 → no-skip
};

for (String test : testCases) {
    boolean skip = rule.shouldSkip(test);
    System.out.println((skip ? "SKIP  " : "QUERY ") + " → " + test);
}
```

### 可能遇到的坑

| 坑 | 表现 | 解法 |
|----|------|------|
| 仅靠关键词误判 | 「在吗」后含业务 | 冷启动时宁多检不漏检 |
| 无观测 | 跳过率异常不知原因 | 监控跳过率+满意度关联 |
| 跳过与工具冲突 | 跳过 RAG 但误跳过工具 | 明确分离两条决策链 |

### 测试验证

```bash
# 对抗注入：闲聊里藏政策号码 → 应 no-skip
# 强制检索：用户点击「强制检索」按钮 → 强制走检索
```

### 完整代码清单

`_06_Advanced_RAG_Skip_Retrieval_Example.java`

---

## 4. 项目总结

### 优点与缺点

| 维度 | Skip retrieval | 始终 RAG | 仅关键词 |
|------|--------------|---------|--------|
| 成本 | 低 | 高 | 低 |
| 误判风险 | 中（可能漏业务） | 低（无漏判） | 高（泛化差） |
| 典型缺点 | 专业问题被跳过 | 浪费问候语检索 | 无法处理带业务的问候 |

### 适用场景

- 通用助理+垂直知识库混合场景
- 高并发场景需要节省检索成本

### 不适用场景

- 全专业问答入口（几乎不需要跳过）
- 对召回零漏率有硬性要求

### 常见踩坑

1. **仅靠关键词误判省略主语的专业问题**——「在吗」被跳过但后面有订单号
2. **无观测**——跳过率突降但没人发现（可能新规则漏了大量业务）
3. **跳过与工具冲突**——跳过 RAG 无意中也跳过了工具调用

### 进阶思考题

1. `skip_decision` 日志如何进入周期性重训？每周用误判样本更新分类模型？
2. 强制检索按钮是否应该有权限控制？谁能点、点了之后是否记录审计？

### 推广计划提示

| 角色 | 建议阅读顺序 | 协作要点 |
|------|-------------|----------|
| 开发 | 第 22 章 → 本章 | 分层决策（检索 vs 工具独立判断） |
| 运营 | 误判样本标注 | NPS 面板关联跳过率 |
| 运维 | 配置热更新 | 版本与回滚 |