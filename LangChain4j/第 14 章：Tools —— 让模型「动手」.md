# 第 14 章：Tools —— 让模型「动手」

## 1. 项目背景

### 业务场景（拟真）

客服机器人需要做三件事：**查订单状态**（调后端 API）、**算退款金额**（按政策执行计算）、**建工单**（写入数据库）。模型不会凭空执行 Java 代码，它只会输出「用什么样的参数调什么样的函数」——真正执行的是你的 `@Tool` 注解标记的方法。

模式是这样的——模型决定要不要调用一个工具、用什么参数；你的 Java 方法在受控的沙箱和权限内执行；执行的结果返回给模型，模型再用自然语言包装后说给用户。

### 痛点放大

没有工具机制的时候，你只有两条路：一是 **让模型直接输出 SQL 或代码**——「执行 SELECT * FROM orders WHERE id = xxx」——这是灾难性的安全隐患，模型可能输出 `DROP TABLE`。二是 **服务端硬编码路由**——把所有意图的判断和参数提取都写在 if-else 里——每个新场景都要改代码上线，灵活度为零。

`@Tool` 给出的答案是：**模型做消歧和意图识别**——它判断用户想查什么订单、用哪个参数；你的 Java 代码做 **确定性的执行和审计**——不直接暴露数据源，所有调用都有日志。

## 2. 项目设计：小胖、小白与大师的对话

**小胖**：工具是不是给模型装了个机械臂——它能动手抓东西了？但抓错了咋办？比如它自己写 SQL 去查不该查的表？

**大师**：机械臂的比喻非常准确——但关键区别在于：**这个机械臂的控制权在你手里，不在模型手里。** 模型能做的事情范围由你提供的 `@Tool` 方法决定。你没给它「执行任意 SQL」的工具，它就写不了 SQL。你给了它 `queryOrderById(String orderId)`，它就只能用订单号查订单——不能查用户表，不能删数据。工具的本质是：**模型做「决定是否调用 + 决定参数」，你的代码做「验证权限 + 执行逻辑 + 审计记录」**。抓错东西是可能的——模型可能把订单号 `O12345` 传成了 `O67890`（因为它理解错了用户意图），但你的代码可以在方法第一行校验该订单号是否属于当前用户。

**小白**：那参数校验具体谁来做？模型传 `int a` 你可能期望是 5，它传了个字符串 "five" 怎么办？还有——工具和传统的规则引擎（比如 Drools）冲突吗？

**大师**：**参数的合法性校验必须由你的代码在 `@Tool` 方法的第一行完成。** 模型生成的参数不天然合法——它可能把 `int` 字段传成浮点数，把日期传成乱码字符串。所以每个工具方法都应该像写 REST API 一样做参数校验：

```java
@Tool("Processes refund for an order")
String processRefund(String orderId, double amount) {
    // 第 1 行：参数校验
    if (orderId == null || !orderId.matches("ORD-\\d{8}")) {
        return "Invalid order ID format";
    }
    if (amount <= 0 || amount > 10000) {
        return "Invalid refund amount";
    }
    // ....
}
```
和规则引擎的关系是 **互补而非替代**：规则引擎做 **复杂的条件匹配**（比如「如果用户是 VIP 且订单超过 30 天且金额小于 1000」走快速退款通道），工具做 **原子性的执行操作**（查订单、发通知、写流水）。通常的架构是：模型先判断「需要做什么」（调哪个工具），工具内部再调用规则引擎做决策。**技术映射**：**Tool = 带审计痕迹的副作用执行边界——模型决定「要不要做」和「参数是什么」，你的代码决定「能不能做」和「做了之后记哪本账」**。

## 3. 项目实战

### 环境准备

```bash
cd langchain4j-examples/tutorials
export OPENAI_API_KEY="sk-your-key-here"
# 注意：部分 API Key 可能不支持工具调用（需支持 function calling 的模型）
```

### 步骤 1：定义工具类

```java
import dev.langchain4j.agent.tool.Tool;
import dev.langchain4j.service.AiServices;

class Calculator {

    @Tool("Calculates the length of a string")
    int stringLength(String s) {
        return s.length();
    }

    @Tool("Calculates the sum of two numbers")
    int add(int a, int b) {
        return a + b;
    }

    @Tool("Calculates the square root of a number")
    double sqrt(int x) {
        if (x < 0) {
            throw new IllegalArgumentException("Cannot sqrt negative number");
        }
        return Math.sqrt(x);
    }
}

interface Assistant {
    String chat(String userMessage);
}

public class ToolsDemo {

    public static void main(String[] args) {

        ChatModel model = OpenAiChatModel.builder()
                .apiKey(System.getenv("OPENAI_API_KEY"))
                .modelName(GPT_4_O_MINI)
                .build();

        Assistant assistant = AiServices.builder(Assistant.class)
                .chatModel(model)
                .tools(new Calculator())
                .chatMemory(MessageWindowChatMemory.withMaxMessages(10))
                .build();

        // 模型会自动决定是否及何时调用工具
        String answer = assistant.chat(
            "What is the square root of the sum of the numbers of " +
            "letters in \"hello\" and \"world\"?");
        
        System.out.println(answer);
    }
}
```

**预期输出**（模型调用了多个工具链）：
```
The word "hello" has 5 letters, "world" has 5 letters. 
Their sum is 10, and the square root of 10 is approximately 3.162.
```

### 步骤 2：观察工具调用日志

```java
// 在 @Tool 方法中添加日志
@Tool("Calculates the length of a string")
int stringLength(String s) {
    System.out.println("[Tool called] stringLength(\"" + s + "\")");
    return s.length();
}
```

输出应显示工具调用顺序：
```
[Tool called] stringLength("hello")
[Tool called] stringLength("world")
[Tool called] add(5, 5)
[Tool called] sqrt(10)
```

### 步骤 3：参数校验

```java
@Tool("Queries order status by order ID")
String queryOrderStatus(String orderId) {
    // 参数校验——模型传的未必合法
    if (orderId == null || !orderId.matches("ORD-\\d{8}")) {
        return "Invalid order ID format. Expected: ORD-12345678";
    }
    // 真实查询...
    return "Order " + orderId + " is in shipping.";
}
```

### 步骤 4：幂等键设计

```java
@Tool("Processes a refund for the specified order")
String processRefund(String orderId, @ToolMemoryId String idempotencyKey) {
    // 幂等键保证同一笔退款不会执行两次
    if (refundAlreadyProcessed(idempotencyKey)) {
        return "Refund already processed for this request.";
    }
    // 执行退款...
    recordRefund(idempotencyKey, orderId);
    return "Refund processed for order " + orderId;
}
```

### 闯关任务

| 难度 | 动手 | 过关标准 |
|------|------|----------|
| ★ | 在 `stringLength` 里加 println 看调用时机 | 理解模型何时决定调工具 |
| ★★ | 构造一个抛异常的工具，看模型返回的信息形态 | 验证错误摘要设计 |
| ★★★ | 一个非法 UUID 参数，验证校验是否执行 | 工具第一行代码必须做校验 |

### 可能遇到的坑

| 坑 | 表现 | 解法 |
|----|------|------|
| 未校验参数 | 越权/非法操作 | 工具方法第一行校验 |
| 长阻塞 IO 在工具里 | 拖死线程池 | 用异步或加超时 |
| @Tool 描述不清 | 模型误召或错传参数 | 描述要精确到参数含义 |

### 测试验证

```bash
# 测试思路：构造一个需要多步工具调用的 prompt
# 例如："What is (3+5)*(7-2)?" 
# 验证模型是否正确调用了 add 和 sqrt 等工具
# 并且最终答案正确
```

### 完整代码清单

[`_10_ServiceWithToolsExample.java`](../../langchain4j-examples/tutorials/src/main/java/_10_ServiceWithToolsExample.java)

## 4. 项目总结

### 优点与缺点

| 维度 | @Tool + AiServices | 服务端硬编码路由 | 模型内嵌伪代码 |
|------|-------------------|----------------|--------------|
| 确定性 | 高（Java 执行） | 高 | 低 |
| 安全 | 依赖权限设计 | 高 | 低 |
| 调试难度 | 中（链长） | 低 | 高 |

### 适用 / 不适用场景

**适用**：计算、查内部 API、建工单，需要精确数字避免模型心算。

**不适用**：无审批的高危操作（转账、删库）——需人工在环或多重审批。

### 常见踩坑

1. 未校验参数 → 数据越权
2. 同步工具里长阻塞 IO → 线程池耗尽
3. 工具命名含糊 → 模型误召

### 进阶思考题

1. strictTools 与 JSON Schema 在各厂商上的差异如何矩阵测试？
2. 如何为扣款类工具设计幂等键与审计字段？

### 推广计划

| 角色 | 建议阅读顺序 | 协作要点 |
|------|-------------|----------|
| 开发 | 本章 → 第 15、17 章 | 工具白名单按租户 |
| 测试 | 模糊参数、重放 | schema 契约 |
| 运维 | 工具 RT/失败率 | 敏感工具双人审批 |

### 检查清单

- **测试**：契约测试 JSON schema；模糊参数类型；重放重复工具调用
- **运维**：为工具 RT 与失败率单独 dashboard；敏感工具双人审批

### 附录

| 模块 | 说明 |
|------|------|
| `langchain4j` | `@Tool`、`ToolService`、`DefaultToolExecutor` 等 |

推荐阅读：`_10_ServiceWithToolsExample.java`、`AssistantTools.java`（Spring 示例）。
