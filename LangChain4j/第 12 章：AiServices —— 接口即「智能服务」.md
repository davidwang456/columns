# 第 12 章：AiServices —— 接口即「智能服务」

## 1. 项目背景

手写「拼消息 → 调模型 → 解析」可行，但不scale：`AiServices` 让你定义 **`interface Assistant`**，用注解描述系统/用户消息，由框架 **生成代理实现**，并把 **`ChatModel`、`ChatMemory`、工具、检索器** 注入调用链。团队得到的收益是：领域代码读起来像 **普通 Java 服务**，测试可以 **Mock 接口**，运行时则由 LangChain4j 织入横切逻辑。

教程 `_08_AIServiceExamples.java`（`langchain4j-examples/tutorials/src/main/java/_08_AIServiceExamples.java`）集中演示 `@SystemMessage`、`@UserMessage`、方法参数绑定等——是进入 **第 14 章工具**、第 **16/18 章 RAG** 的主入口。

## 2. 项目设计：大师与小白的对话

**小白**：这不就是 Spring Data 那种魔法吗？

**大师**：形态相似，但**下游是概率系统**——你必须保留 **超时、重试策略、观测、降级**，不能把魔法当确定性 RPC。

**小白**：同一个接口多个方法可以吗？

**大师**：可以；要关注 **是否共享 ChatMemory** 与 **工具集合**——不同业务线建议拆分接口以降低提示词污染。

**小白**：怎么单测？

**大师**：**接口 + Mockito** 或在测试里提供 **假 ChatModel**（不发起网络）。集成测再用 Testcontainers / WireMock 录响应。

**小白**：注解太多会不会难维护？

**大师**：把**长系统提示**外置为资源文件或配置中心；注解只留「引用 key」。

**小白**：与 `AiServices` 并行的还有别的入口吗？

**大师**：需要 **完全控制消息列表** 时可直接调 `ChatModel`；Route 是：**简单 CRUD 式对话 → AiServices**；**复杂多段 orchestration → 手写**。

**小白**：性能呢？

**大师**：代理层开销相对 LLM **可忽略**；热点在 IO 与模型推理。

**小白**：Kotlin 呢？

**大师**：仓库有 `langchain4j-kotlin` 模块， idiomatic 写法略不同但抽象一致。

## 3. 项目实战：主代码片段

> **场景入戏**：`AiServices` 像 **给接口施魔法的魔杖**——你写 `interface Assistant { String chat(String m); }`，运行时蹦出来一个 **会调 LLM 的代理对象**。**危险**在于：代理 **太顺**，团队会忘了底下仍是 **网络 IO**。

**本节以「导览任务」代替贴全书代码**（仓库迭代快，行号易漂）。请打开 [`_08_AIServiceExamples.java`](../../langchain4j-examples/tutorials/src/main/java/_08_AIServiceExamples.java)：

#### 寻宝清单（建议 30 分钟）

1. **Ctrl+F** `AiServices.builder`，找到 **最少参数**能跑起来的 builder 链——圈出 **`chatModel` 以外** 你还愿意接受哪几个默认。  
2. 找到带 **`@UserMessage` / `@SystemMessage`** 的接口方法，把 **`{{var}}`** 与方法参数 **连线**——像 **MyBatis 注解 SQL**，但下游是 **概率模型**。  
3. 若示例含 **memory / tools / retriever** 其中任一，在页边注：**删掉它系统还工作吗？** ——练习 **依赖可裁剪** 思维。

#### 与 Spring 的生产对照

打开 [`spring-boot-example/.../Assistant.java`](../../langchain4j-examples/spring-boot-example/src/main/java/dev/langchain4j/example/aiservice/Assistant.java)（`@AiService`），对比 **手动 `AiServices.builder`**：**DI 容器**替你管 **作用域与 Listener**。

#### 挖深一层

- **代理成本**：JDK `Proxy` / ByteBuddy 差异不必纠结，**热点在 HTTP**。  
- **调试**：IDE **Evaluate** 代理实例类名常为 `$ProxyN` ——学会 **断点打在生成方法入口**（或开启 **字节码插件**）。  
- **线程安全**：同一接口 Bean **是否共享 ChatMemory** 决定 **是否串话**。

## 4. 项目总结

### 优点

- **声明式**：业务代码清爽。  
- **可测试**：接口边界清晰。

### 缺点

- **魔法感**：新人需调试生成逻辑。  
- **复杂控制流**（多分支路由）不如显式状态机直观。

### 适用场景

- BFF / 领域服务里封装「带记忆的单接口」。  
- 快速 PoC，与后续拆分微服务兼容。

### 注意事项

- **版本升级**检查注解处理器行为变化。  
- **类加载器**在隔离容器（OSGi/特殊插件）中可能有坑。

### 常见踩坑

1. **忘记配置 ChatMemory** 导致「每轮失忆」。  
2. **工具与 RAG** 同时启用导致 **提示超长**。  
3. 在 **多租户**场景下单一 Assistant 实例串数据。

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
