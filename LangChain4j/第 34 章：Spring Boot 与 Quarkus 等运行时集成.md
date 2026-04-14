# 第 34 章：Spring Boot 与 Quarkus 等运行时集成

## 1. 项目背景

把 `ChatModel` 写在 `main` 里适合学习，但在企业里几乎总会落在**某种 DI 容器**里：Spring Boot、Quarkus、Micronaut、Helidon 或 Jakarta EE。容器负责：**Bean 生命周期**、**配置外置**、**请求作用域内的记忆隔离**、**与 Micrometer/健康检查对齐**。LangChain4j 对 Spring 提供了 `dev.langchain4j:langchain4j-spring-*` 系列集成（在示例中以注解 `@AiService` 等形式出现），Quarkus 则通过 **`quarkus-langchain4j`** 扩展把声明式 AI 服务纳入 CDI。

本章合并原「仅 Spring」与「仅 Quarkus」两条线，给出**同一张心智图**：**模型与内存 →（可选）检索器与工具 → 对外暴露 HTTP**。示例锚点：

- Spring：`langchain4j-examples/spring-boot-example/`
- Quarkus：`langchain4j-examples/quarkus-example/`（基于 Quarkiverse 扩展的范例）

## 2. 项目设计：大师与小白的对话

**小白**：我必须用 Spring Boot 才能让 LangChain4j 上生产吗？

**大师**：不是必须，但**极常见**。你可以在任何能托管单例与配置的地方使用库本身。Spring/Quarkus 的价值在于**工程化惯例**：配置分层、`@Bean` 可替换实现、与 Actuator/Metrics 生态握手。

**小白**：`@AiService` 接口上的 `Assistant` 和手写 `AiServices.builder` 差在哪？

**大师**：在 Spring 场景，`@AiService`（`dev.langchain4j.service.spring.AiService`）让容器**自动装配** `ChatModel`、记忆等依赖，并生成接口代理 Bean。你写的是「契约」，框架负责「组装」—— 这与手写 builder 等价，但更易测试与模块化。

**小白**：`ChatMemory` 为什么要 `@Scope(SCOPE_PROTOTYPE)`？

**大师**：典型 Web 应用里，每个用户会话或每次请求需要**独立的记忆窗口**。单例的 `ChatMemory` 会让所有用户共享上下文 —— 「张三的订单」跑到「李四的对话里」。Prototype 让容器「每次注入新的记忆实例」成为可能（仍要结合你的会话映射策略）。

**小白**：`ChatModelListener` 说是能接到所有 `ChatModel`，这对运维有什么用？

**大师**：你可以在监听器里**统一记日志、打点、脱敏**，而不必在每个 `@AiService` 方法一一复制。对排障与计费（token）尤其关键（与第 36 章观测主题衔接）。

**小白**：Quarkus 示例看起来不像 Spring 那个 `@AiService` 啊？

**大师**：命名与包名会随扩展版本迭代，但模式一致：**声明式服务接口 + CDI 生成实现 + JAX-RS/Vert.x 暴露端点**。读 Quarkus 样例时抓住「**哪定义模型**、**哪声明生成式服务**、**哪发布路由**」三件事即可。

**小白**：我想在一个应用里挂两个模型（快/慢、便宜/贵）怎么办？

**大师**：定义**两个 `ChatModel` Bean** 并用 `@Qualifier` 或在 `AiServices` 装配处区分；或对不同接口拆分两个 `@AiService`。关键是**不要**在静态工具类里硬编码全局 `new`。

**小白**：Spring Cloud Gateway 后面挂 LangChain4j 时要注意什么？

**大师**：SSE 流式端点（`TEXT_EVENT_STREAM`）经过网关时要确认**超时、缓冲、HTTP/2**。否则表现为「首 token 迟迟不来」或连接被中间件掐断。

**小白**：本机开发用 IDE 直接 Run，和生产 Jar 运行有差异吗？

**大师**：差异在**类路径、配置优先级、JVM 标志**。至少做一次「与生产同构的」容器镜像或 `java -jar` 试跑，避免只在 IDE 里绿。

## 3. 项目实战：主代码片段

### 3.1 Spring Boot：`Assistant` 与控制器

接口定义（仓库原文）：

```java
import dev.langchain4j.service.SystemMessage;
import dev.langchain4j.service.spring.AiService;

@AiService
public interface Assistant {

    @SystemMessage("You are a polite assistant")
    String chat(String userMessage);
}
```

路径：`langchain4j-examples/spring-boot-example/src/main/java/dev/langchain4j/example/aiservice/Assistant.java`。

控制器暴露同步与流式两种路由：

```java
@GetMapping("/assistant")
public String assistant(@RequestParam(value = "message", defaultValue = "What is the current time?") String message) {
    return assistant.chat(message);
}

@GetMapping(value = "/streamingAssistant", produces = TEXT_EVENT_STREAM_VALUE)
public Flux<String> streamingAssistant(
        @RequestParam(value = "message", defaultValue = "What is the current time?") String message) {
    return streamingAssistant.chat(message);
}
```

`AssistantConfiguration` 中示范了 **`MessageWindowChatMemory` 原型作用域**与全局 **`ChatModelListener`** Bean，可直接对照阅读：

- `.../aiservice/AssistantConfiguration.java`

低层对照：`ChatModelController` 演示不通过 `@AiService`、直接注入 `ChatModel` 的用法 —— 适合需要完全手动掌控消息构造的团队。

### 3.2 Quarkus 侧建议阅读顺序

打开 `quarkus-example/src/main/java/io/quarkiverse/langchain4j/sample/`，优先阅读：

1. 声明 AI 服务与 triage 逻辑的类（如 `TriageService`）。
2. JAX-RS `Resource` 如何把用户输入交给服务。
3. `application.properties` 中模型与扩展配置（若有）。

与 Spring 的差异多在**配置文件格式**与**原生镜像构建**，LangChain4j 核心 API 保持一致。

### 3.3 配置外置清单（两栈通用）

- API Key / 底座 URL：环境变量或密钥管理。
- 模型名、温度、超时：按环境拆分 `application-*.yml` / `profile`。
- 观测开关：采样率、日志脱敏正则。

### 3.4 闯关与「梗图」式排障（趣味 + 实战）

| 症状 | 先怀疑谁 | 一句人话 |
|------|----------|----------|
| 两个用户对话 **串台** | `ChatMemory` **作用域** 配成 singleton | 「记忆灌进公共饮水机了」 |
| 流式接口 **首字慢但后端快** | **网关 / Nginx buffer** | 「中间商在攒字幕」 |
| 本地绿、Docker 红 | **JVM 代理与证书** | 「容器里没 trust 公司.crt」 |

**深度练习**：用 `curl -N` 打 `streamingAssistant`，同时在 **`MyChatModelListener`**（若保留）里打点——对齐 **首包时间** 与 **模型 TTFB**，形成 **链路共识**。

## 4. 项目总结

### 优点

- **Bean 化**使 `ChatModel`、记忆、检索器可被 Mock，方便切片测试。
- **Listener / 拦截模型**把横切关注点从业务方法剥离。
- **Web 层示例**展示同步与 **Reactor `Flux` 流式**集成路径。

### 缺点

- **学习曲线叠加**：既要懂框架又要懂 LLM 语义；排障时需分层定位。
- **作用域误用风险**：记忆 Bean 生命周期与用户会话映射若设计错误，事故难查。
- **版本节奏**：Spring/Quarkus 主版本升级要与 `langchain4j` BOM 联合验证。

### 适用场景

- 企业 REST/gRPC 后端已标准化在 Spring 或 Quarkus。
- 要把 AI 能力以**统一网关 + 指标**方式纳入现网。

### 注意事项

- **线程模型**：响应式栈中阻塞式模型调用需显式 `subscribeOn` 或使用弹性线程池。
- **会话亲和**：多实例部署时，会话记忆若存内存，需要粘性会话或外置 `ChatMemoryStore`。

### 常见踩坑

1. **将单例记忆当多用户记忆**：数据串话。
2. **未配置网关超时**导致 SSE 假死。
3. **仅在 IDE 运行验证**：遗漏 `-D` JVM 参数导致 TLS/代理问题在生产才暴露。

---

### 本期给测试 / 运维的检查清单

**测试**：对 `/assistant` 与 `/streamingAssistant` 分别做契约测试与负载冒烟；使用 `WebTestClient`/`RestAssured` 验证错误模型配置时的 5xx 行为是否符合内部规范；对 `ChatModelListener` 记录的关键字段做脱敏断言。

**运维**：把模型供应商 SLA 与内部 SLO 区分展示；容器镜像中禁止写死密钥；为流式端点单独配置超时、并发与熔断；发布前在预发环境比对「冷启动→首 token 延迟」基线。

### 附录：相关 Maven 模块与源码类

| 模块 | 说明 |
|------|------|
| `langchain4j-spring-boot-starter` 等 | Spring 起步依赖（以团队实际引入为准） |
| `ExampleApplication.java` | 启动入口 |
| `AssistantConfiguration.java` | 记忆与 Listener |

推荐阅读：`Assistant.java`、`AssistantController.java`、`AssistantConfiguration.java`、`ChatModelController.java`。
