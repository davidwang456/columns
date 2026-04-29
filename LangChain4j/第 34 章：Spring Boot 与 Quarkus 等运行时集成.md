# 第 34 章：Spring Boot 与 Quarkus 等运行时集成

## 1. 项目背景

### 业务场景（拟真）

90% 的 Java 企业后端已经标准化在 Spring Boot 或 Quarkus 上。团队需要把 ChatModel、记忆、检索、工具纳入 DI 容器——通过 `@Bean` 管理生命周期、通过 `application.properties` 外置配置、通过 Actuator 做健康检查和指标暴露。之前几章的所有 `main` 方法都不适用于生产——生产需要的是 `@AiService`、`@Component`、`@Configuration`。

### 痛点放大

直接把 `ChatModel` 写在 `main` 方法里调用——两个问题：① 无法纳入 DI 容器的生命周期管理（每次重启都重新创建连接池）；② 无法做配置外置（模型名、API Key 写死在代码里）。`@AiService` 注解自动化了 `AiServices.builder()` 的组装过程；`ChatModelListener` 提供了统一的观测切面。但 Spring 集成也带来了新的陷阱——记忆作用域配错了会导致串话、流式经过网关会被缓冲。

## 2. 项目设计：小胖、小白与大师的对话

**小胖**：不用 Spring 就不能上生产吗？我写个 main 方法一直跑着不也行？

**大师**：技术上确实可以——`java -jar` 一个只有 `main` 方法的 jar 确实能跑。但你很快会发现需要自己实现：配置文件的加载与多环境切换、Health Check 端点、指标暴露（给 Prometheus）、Bean 的生命周期管理（ChatModel 和连接池应该在何时创建何时销毁）。这些 Spring Boot 都帮你做好了。简单说：没有 Spring 也能上生产，但 **有 Spring 你上生产的速度快 10 倍**。

**小白**：`@AiService` 注解和手写 `AiServices.builder()` 到底差在哪？ChatMemory 为什么在 Spring 里要配成 `PROTOTYPE` 作用域？

**大师**：`@AiService` 是 **声明式**——Spring 在启动时自动扫描带 `@AiService` 的接口，从 ApplicationContext 中获取 `ChatModel`、`ChatMemory`、`ChatModelListener` 等 Bean，为你生成代理并注册为 Spring Bean。手写 `builder()` 是 **组装式**——你自己控制每一块的创建和注入。两者殊途同归。ChatMemory 配成 `PROTOTYPE` 的原因：**如果 memory 是 Singleton（默认），所有用户共用同一个记忆实例——用户 A 说「我要退款」，用户 B 也看到这条消息了，这就是串话事故**。每个用户或每个会话需要独立的 memory 实例，所以要用 `PROTOTYPE` 加会话 ID 映射。**技术映射**：**Spring 的 @AiService 把 AiServices.builder() 的样板代码收进了容器自动装配；ChatModelListener 是统一观测的自然切面——你通过它打 token 用量、记延迟、脱敏日志，而不需要侵入业务代码**。

---

## 3. 项目实战

### 环境准备

```bash
# Spring Boot 项目的 pom.xml 应包含：
# langchain4j-spring-boot-starter
# langchain4j-open-ai
```

### 分步实现

#### 步骤 1：Spring Boot 最小配置

```java
// Assistant.java
@AiService
public interface Assistant {
    @SystemMessage("You are a polite assistant")
    String chat(String userMessage);
}

// AssistantConfiguration.java
@Configuration
public class AssistantConfig {
    
    @Bean
    ChatModel chatModel() {
        return OpenAiChatModel.builder()
                .apiKey(System.getenv("OPENAI_API_KEY"))
                .modelName(GPT_4_O_MINI)
                .build();
    }
    
    @Bean
    @Scope("prototype")
    ChatMemory chatMemory() {
        return MessageWindowChatMemory.withMaxMessages(10);
    }
    
    @Bean
    ChatModelListener myListener() {
        return new MyChatModelListener();
    }
}

// Controller.java
@RestController
public class AssistantController {
    
    @Autowired
    Assistant assistant;
    
    @GetMapping("/chat")
    public String chat(@RequestParam String message) {
        return assistant.chat(message);
    }
}
```

#### 步骤 2：流式端点

```java
@GetMapping(value = "/stream", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
public Flux<String> stream(@RequestParam String message) {
    return streamingAssistant.chat(message);
}
```

```bash
# 验证流式
curl -N http://localhost:8080/stream?message=hello
```

### 测试验证

```bash
# WebTestClient 测试
# 错误模型配置时的 5xx 行为
```

### 完整代码清单

`spring-boot-example`、`quarkus-example`

---

## 4. 项目总结

### 优点与缺点

| 维度 | Spring/Quarkus 集成 | 纯 main | 侧车独立服务 |
|------|-------------------|--------|-------------|
| 工程化 | 高 | 低 | 中 |
| 典型缺点 | 框架+LLM 双栈 | 无标准观测 | 多进程运维 |

### 适用场景

- 企业 REST/gRPC 已 Spring/Quarkus

### 不适用场景

- 极简脚本无 DI

### 常见踩坑

1. 单例记忆当多用户 → 串话
2. 网关未配超时 → SSE 假死
3. 仅 IDE 验证 → TLS/代理生产才暴露

### 进阶思考题

1. 多 @AiService 与共享 ChatMemory 的边界？
2. 原生镜像下 ChatModel 与反射配置的注意点？

### 推广计划提示

| 角色 | 建议阅读顺序 | 协作要点 |
|------|-------------|----------|
| 开发 | 第 12 章 → 本章 → 第 36 章 | 记忆作用域 |
| 运维 | 网关+SSE | 超时、熔断、首 token 基线 |
| 测试 | WebTestClient | 脱敏断言 |