# 第 12 章：AiServices —— 接口即「智能服务」

## 1. 项目背景

### 业务场景（拟真）

业务团队厌倦手写「拼消息 → 调模型 → 解析」：`AiServices` 允许定义 **`interface Assistant`**，用注解描述系统/用户消息，框架 **生成代理** 并注入 **`ChatModel`、`ChatMemory`、工具、检索器**。领域代码读如 **普通 Java 服务**，测试可 **Mock 接口**——适合 **BFF / 领域服务** 封装「带记忆的单接口」。

### 痛点放大

若 **魔法当确定性 RPC**：会忽略 **超时、重试、观测、降级**；若 **单接口多方法** 却 **共享污染提示**：**可维护性**下降。教程 `_08_AIServiceExamples.java` 是进入 **第 14 章工具**、**第 16/18 章 RAG** 的主入口。

## 2. 项目设计：小胖、小白与大师的对话

**小胖**：这跟 Spring Data 一样「接口一写就完事」？

**小白**：下游是 **概率系统**——和 RPC 有啥本质不同？**同一接口多方法** 要注意啥？

**大师**：形态相似，但 **LLM 非确定性**——必须保留 **超时、重试、观测、降级**。多方法要关注 **是否共享 ChatMemory/工具**——不同业务线可 **拆分接口** 降污染。**技术映射**：**AiServices = 语法糖 + 横切织入，不是魔法 RPC**。

**小胖**：注解一多是不是比 XML 还难？

**小白**：怎么单测？**Kotlin** 呢？

**大师**：**接口 + Mockito** 或 **假 ChatModel**；集成测 WireMock/Testcontainers。长系统提示 **外置资源/配置中心**，注解只留 key。Kotlin 有 **`langchain4j-kotlin`**，抽象一致。**技术映射**：**可测试性靠接口边界**。

**小胖**：复杂多分支路由也全写 AiServices？

**大师**：需要 **完全控制消息列表** 时直接调 `ChatModel`；**简单 CRUD 式对话 → AiServices**；**复杂 orchestration → 手写状态机**。**技术映射**：**选型 = 控制流复杂度**。

---

## 3. 项目实战

### 环境准备

- [`_08_AIServiceExamples.java`](../../langchain4j-examples/tutorials/src/main/java/_08_AIServiceExamples.java)。  
- 对照 [`spring-boot-example/.../Assistant.java`](../../langchain4j-examples/spring-boot-example/src/main/java/dev/langchain4j/example/aiservice/Assistant.java)（`@AiService`）。

### 分步任务

| 步骤 | 目标 | 操作 |
|------|------|------|
| 1 | Builder 最小集 | `Ctrl+F` `AiServices.builder`，圈出除 `chatModel` 外可接受的默认 |
| 2 | 参数绑定 | `@UserMessage` / `@SystemMessage` 的 `{{var}}` 与方法参数连线 |
| 3 | 依赖裁剪 | 若含 memory/tools/retriever，标注「删掉它系统还工作吗？」 |

**可能遇到的坑**：**线程安全**——共享 Assistant Bean 与 **ChatMemory** 串话；**魔法感**——新人不会断点代理。

### 测试验证

- 契约测试 **方法签名 ↔ 模板变量**；**Spring 上下文** 加载 `AiService` Bean。

### 完整代码清单

[`_08_AIServiceExamples.java`](../../langchain4j-examples/tutorials/src/main/java/_08_AIServiceExamples.java)；Spring：`Assistant.java`。

---

## 4. 项目总结

### 优点与缺点（与同类做法对比）

| 维度 | AiServices | 手写 ChatModel | 其他框架 Assistant API |
|------|------------|----------------|-------------------------|
| 可读性 | 高（声明式） | 中 | 视框架 |
| 调试 | 代理层需学习 | 直链 | 视框架 |
| 复杂控制流 | 中（不如状态机直观） | 灵活 | 视框架 |
| 典型缺点 | 魔法感 | 样板多 | 生态绑定 |

### 适用场景

- BFF/领域服务封装对话；快速 PoC 与后续拆分微服务。

### 不适用场景

- **极复杂多段编排**——显式状态机或工作流引擎更合适。  
- **必须逐条操纵消息** 的底层实验——直接 `ChatModel`。

### 注意事项

- **版本升级** 注解处理器行为；**类加载器** 在特殊插件环境。

### 常见踩坑经验（生产向根因）

1. **未配 ChatMemory** → 每轮失忆。  
2. **工具 + RAG** 同开 → 提示超长。  
3. **多租户** 单 Assistant 实例 **串数据**。

### 进阶思考题

1. **`@AiService`（Spring）与手动 `AiServices.builder`** 在 **Listener 注入** 上差在哪？  
2. 如何用 **字节码断点** 或日志定位 **生成的方法入口**？

### 推广计划提示（多部门）

| 角色 | 建议阅读顺序 | 协作要点 |
|------|----------------|----------|
| **开发** | 第 10 章 → 本章 → 第 14 章 | **接口拆分** 降提示污染 |
| **测试** | 契约 + 上下文测试 | **签名与模板变量** 一致 |
| **运维** | 第 34 章 | **首次调用冷启动**、原生镜像 |

---

### 本期给测试 / 运维的检查清单

**测试**：契约测试关注 **方法签名 ↔ 提示模板变量** 一致性；对 **`AiService` Bean** 做上下文加载测试。  
**运维**：把 **`AiServices` 创建耗时** 与 **首次调用冷启动** 纳入发布检查（尤其原生镜像场景）。

### 附录：相关 Maven 模块与源码类

| 模块 | 说明 |
|------|------|
| `langchain4j` | `AiServices`、`AiService` 相关注解 |
| `langchain4j-spring-*` | Spring 专用 `@AiService`（见示例） |

推荐阅读：`_08_AIServiceExamples.java`、`AiServices` 源码入口、`Assistant.java`（Spring 示例）。
