# 第 34 章：Spring Boot 与 Quarkus 等运行时集成

## 1. 项目背景

### 业务场景（拟真）

企业后端已标准化在 **Spring Boot 或 Quarkus**；团队要把 `ChatModel`、记忆、检索、工具 **纳入 DI**、**配置外置**、**健康检查与指标**，并与 **网关、SSE/流式** 对齐。

### 痛点放大

把 `ChatModel` 写在 `main` 适合学习；生产需要 **Bean 生命周期**、**请求作用域记忆隔离**、**Micrometer**。若 **单例 ChatMemory** 串话、**网关缓冲** 吃掉流式、**仅 IDE 验证**——**TLS/代理** 在生产才爆。LangChain4j 提供 `langchain4j-spring-*`、`quarkus-langchain4j` 等。

**心智图**：**模型与内存 →（可选）检索器与工具 → HTTP**。示例：

- Spring：`langchain4j-examples/spring-boot-example/`
- Quarkus：`langchain4j-examples/quarkus-example/`

## 2. 项目设计：小胖、小白与大师的对话

**小胖**：不用 Spring 就不能上生产？

**小白**：`@AiService` 和手写 `AiServices.builder` 差在哪？**ChatMemory 为啥 PROTOTYPE**？

**大师**：**不是必须** Spring，但极常见；容器负责 **配置分层、可替换 Bean、Actuator**。`@AiService` **自动装配** 模型与记忆，生成代理——**契约 vs 组装**。**单例 memory** 会 **串话**——需 **prototype + 会话映射**（第 13 章）。**技术映射**：**Listener = 统一观测切面（第 36 章）**。

**小胖**：两个模型（快/慢）怎么挂？

**小白**：**Gateway 后面 SSE** 注意啥？**Quarkus** 不一样？

**大师**：**双 `ChatModel` Bean + @Qualifier** 或拆分接口；**勿静态 `new`**。SSE 经网关要 **超时、缓冲、HTTP/2**。**Quarkus** 模式一致：**模型 + 声明式服务 + 路由**——读三类入口即可。**技术映射**：**生产同构试跑**（`java -jar`/镜像）避免 **IDE 绿、生产红**。

---

## 3. 项目实战

### 环境准备

- JDK、Maven/Gradle；可选 Docker 做与生产同构试跑。

### 分步实现（主代码片段）

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

### 测试验证

- `/assistant` 与 `/streamingAssistant` **契约 + 负载冒烟**；错误模型配置 **5xx** 行为；`ChatModelListener` 字段 **脱敏**。

### 完整代码清单

[`spring-boot-example`](../../langchain4j-examples/spring-boot-example)、[`quarkus-example`](../../langchain4j-examples/quarkus-example)。

---

## 4. 项目总结

### 优点与缺点（与同类做法对比）

| 维度 | Spring/Quarkus 集成 | 纯 main + 自建生命周期 | 侧车独立服务 |
|------|---------------------|------------------------|--------------|
| 工程化 | 高 | 低 | 中 |
| 可测性 | 高（Mock Bean） | 中 | 中 |
| 排障 | 需分层 | 简单 | 需联调 |
| 典型缺点 | 框架+LLM 双栈 | 无标准观测 | 运维多进程 |

**文字补充（优点）**：**Bean 化**、**Listener 横切**、**Flux 流式** 路径。

**文字补充（缺点）**：**学习曲线叠加**；**记忆作用域** 误用；**版本与 BOM** 联合验证。

### 适用场景

- 企业 REST/gRPC 已 **Spring/Quarkus**；**统一网关 + 指标** 纳现网。

### 不适用场景

- **无 DI 的极简脚本**——不必引入。

### 注意事项

- **响应式栈** 阻塞调用需 **`subscribeOn`/弹性线程池**；**多实例** 记忆需 **粘性或外置 ChatMemoryStore**。

### 常见踩坑经验（生产向根因）

1. **单例记忆当多用户** → 串话。  
2. **网关未配超时** → SSE 假死。  
3. **仅 IDE 验证** → TLS/代理生产才暴露。

### 进阶思考题

1. **多 `@AiService`** 与 **共享 ChatMemory** 的边界如何设计？  
2. **原生镜像** 下 `ChatModel` 与 **反射配置** 的注意点？

### 推广计划提示（多部门）

| 角色 | 建议阅读顺序 | 协作要点 |
|------|----------------|----------|
| **开发** | 第 12 章 → 本章 → 第 36 章 | **记忆作用域** |
| **运维** | 网关 + SSE | **超时、熔断、首 token 基线** |
| **测试** | WebTestClient | **脱敏断言** |

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
