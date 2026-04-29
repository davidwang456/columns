# 第 36 章：可观测性与 Guardrails（合并章）

## 1. 项目背景

### 业务场景（拟真）

**可观测性** 回答三个问题：多慢（P95 延迟）、多贵（token 消耗）、错在哪（429 错误率、空检索率）。**Guardrails** 回答另一个问题：该不该回（提示注入拦截、PII 泄露检查、敏感内容过滤）。两者合并为一章——因为它们都是 LLM 应用在生产环境运行时的「保障层」。

### 痛点放大

无指标时：第三周账单暴涨才发现某个接口的 token 消耗翻了三倍；服务变慢了但分不清是模型变慢还是网络变慢。无护栏时：用户举报 AI 客服生成了包含敏感信息的回答——但因为没有日志和脱敏，甚至连「是谁的什么问题触发的」都查不到。

## 2. 项目设计：小胖、小白与大师的对话

**小胖**：Micrometer 和 OpenTelemetry——两个都接会不会打架？我到底应该选哪个？

**大师**：它们不打架，而且可以共存。OpenTelemetry 做 **链路追踪**——记录单次请求的完整调用链（HTTP 进来→调模型→调工具→返回）。Micrometer 做 **指标聚合**——统计过去 1 分钟内所有请求的 p99 延迟、token 总消耗、错误率。实践中是：OTel 告诉你「这个请求慢在哪一步」，Micrometer 告诉你「整体系统是不是在变慢」。最佳方案是共用一个 trace ID 串联。

**小白**：护栏应该放在模型调用之前还是之后？如果护栏误杀了正常请求怎么办？

**大师**：**两边都要放。** 入站护栏（调用模型之前）做输入检查——脱敏 PII、拦截提示注入、校验输入长度。出站护栏（模型返回之后）做输出审查——检查模型是否生成了敏感内容、是否包含不该出现的实体名。高风险操作（如涉及资金或个人信息）还要走人工审核。误杀的解决方案是 **灰度上线 + 申诉机制**：新护栏规则先让 1% 流量通过，对比误杀率后再全量上线；用户对拦截有异议可以申诉，申诉样本反过来优化护栏规则。**技术映射**：**指标 tag 基数爆炸是常见的观测坑——不要按 userId 打 tag（几百万用户就几百万个 tag），按 tenantId 或 model 这种低基数维度打；结构化的字段级脱敏（在打日志那一刻就脱敏）比事后扫日志删除更可靠**。

---

## 3. 项目实战

### 环境准备

```bash
cd langchain4j-examples/spring-boot-example
export OPENAI_API_KEY="sk-your-key-here"
```

### 分步实现

#### 步骤 1：实现 ChatModelListener

```java
public class MetricsChatModelListener implements ChatModelListener {

    private final MeterRegistry registry;

    public MetricsChatModelListener(MeterRegistry registry) {
        this.registry = registry;
    }

    @Override
    public void onRequest(ChatModelRequestContext context) {
        context.attributes().put("startTime", System.nanoTime());
    }

    @Override
    public void onResponse(ChatModelResponseContext context) {
        long durationMs = (System.nanoTime() - 
            (long) context.attributes().get("startTime")) / 1_000_000;

        // 记录延迟
        registry.timer("llm.request.duration", 
            "model", context.request().model())
            .record(Duration.ofMillis(durationMs));

        // 记录 token
        if (context.response().tokenUsage() != null) {
            registry.counter("llm.tokens.total",
                "model", context.request().model())
                .increment(context.response().tokenUsage().totalTokenCount());
        }
    }

    @Override
    public void onError(ChatModelErrorContext context) {
        registry.counter("llm.request.errors",
            "model", context.request().model(),
            "error", context.error().getClass().getSimpleName())
            .increment();
    }
}
```

#### 步骤 2：护栏策略表

```java
// 入站护栏
public String inboundGuard(String userInput) {
    // 脱敏 PII：手机号、身份证、邮箱
    userInput = userInput.replaceAll("1[3-9]\\d{9}", "[PHONE]");
    userInput = userInput.replaceAll("\\d{17}[\\dXx]", "[ID]");
    userInput = userInput.replaceAll("\\w+@\\w+\\.\\w+", "[EMAIL]");
    return userInput;
}

// 出站护栏
public String outboundGuard(String modelOutput) {
    // 检查是否包含敏感内容
    if (containsSensitiveContent(modelOutput)) {
        return "抱歉，我无法提供此信息。";
    }
    return modelOutput;
}
```

### 测试验证

```bash
# 对抗样本集定期跑
# 指标告警与发布联动验证
```

### 完整代码清单

`MyChatModelListener.java`（Spring 示例）、`langchain4j-guardrails` 模块

---

## 4. 项目总结

### 优点与缺点

| 维度 | Listener + Guardrails | 仅 HTTP 日志 | 仅网关 |
|------|---------------------|------------|-------|
| 可观测 | 全链路 LLM | 缺 token | 缺语义 |
| 安全 | 语义层 | 无 | 传统 |

### 适用场景

- 所有对公 Bot、金融/未成年人相关

### 不适用场景

- 内网无敏感且无计费（可极简）

### 常见踩坑

1. 指标 tag 基数爆炸
2. 护栏日志泄露更多隐私
3. 只看黄金指标忽略解析失败

### 进阶思考题

1. Grafana dashboard as code 与发布的联动？
2. 高敏渠道分级规则如何灰度？
