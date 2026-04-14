# 第 9 章：HTTP 客户端、超时与容错

## 1. 项目背景

无论 `ChatModel` 多么高层，底层几乎都是 **HTTPS 调用**。企业环境里有 **代理、MITM 证书、双向 TLS、HTTP/2、连接池** 与 **限流**。LangChain4j 把 HTTP 细节封装在 **`langchain4j-http-client-*`** 系列实现中（JDK、Apache、OkHttp 等），让你在不 fork 整个库的情况下，尽量对齐公司标准栈。

本章与第 5 章 `timeout`、第 7 章流式、第 36 章观测形成「**可靠生产调用**」闭环：你不仅要 **调得通**，还要在 **抖动网络** 下 **可恢复、可观测、可降级**。仓库的 `langchain4j/pom.xml` 中可检索 `langchain4j-http-client` 相关模块名，选型和版本以 **当前 BOM** 为准。

## 2. 项目设计：大师与小白的对话

**小白**：默认 JDK HttpClient 不够好吗？

**大师**：多数场景够用；若你要 **连接池细调**、**拦截器统一打日志**、或与旧 OkHttp 生态集成，再换实现。

**小白**：429 要不要重试？

**大师**：要 **谨慎**。先 **读 Retry-After**；用 **指数退避 + 抖动**；设 **最大重试 budget**，避免 **retry storm** 放大故障。

**小白**：5xx 呢？

**大师**：可重试的比例通常更高，但仍要区分 **幂等** 与非幂等 HTTP 动词——LLM POST 往往 **不具备传统幂等键**，要在业务上接受「可能重复扣费」风险或做 **客户端去重 id**（若厂商支持）。

**小白**：超时和断路器谁先谁后？

**大师**：**超时**是单请求上限；**断路器**是一段时间窗口的成功率策略。要一起配，而不是只调 Timeout。

**小白**：如何把 trace id 传到供应商日志？

**大师**：在 **HTTP Header** 或 **vendor metadata**（若支持）里带；同时在你侧 **Span** 里关联（第 36 章）。

**小白**：内网代理导致 CONNECT 失败怎么排？

**大师**：先看 **JVM 代理参数**、容器 `/etc/hosts`、公司 **PAC**；再看 **JDK 与 Apache Client** 代理配置差异。

**小白**：连接池耗尽有什么症状？

**大师**：**排队等待**/**线程全部阻塞**；指标上 **活跃连接数** 顶满。要调 **max connections** 与 **lease timeout**。

**小白**：我需要对每家 LLM 配不同客户端吗？

**大师**：常见做法是 **按供应商分 Bean**，因为 endpoint、TLS、代理策略不同。

## 3. 项目实战：主代码片段

> **场景入戏**：LangChain4j 是**豪华游轮**，HTTP 客户端是**舷梯**——舷梯颤，游客（`ChatModel`）再稳也会摔。你要关心：**代理、证书、连接池、HTTP/2、超时、重试**——全是**老派分布式**手艺。

本章**故意不绑死 API 方法名**：`OpenAiChatModel` 上 **HttpClient 定制入口** 随小版本可能微调，**以当前 `langchain4j-open-ai` Javadoc 为准**。

#### 闯关任务（无密钥也能做一半）

| 难度 | 动手 | 实战收获 |
|------|------|----------|
| ★ | 打开 Javadoc，**圈出**与 `http`、`timeout`、`proxy` 相关的 Builder 方法 | 建立**个人速查表**一页纸 |
| ★★ | 配置 **错误 baseUrl** 或 **无效 key**，各抓一次堆栈 | 区分 **DNS / TLS / 401**——SRE 晨会够用 |
| ★★★ | 用下面**伪代码骨架**开团队技术午餐会：谁负责 **TrustStore**？谁负责 **出网白名单**？ | 推动**平台组**与**应用组**责任边界 |

```java
// 伪代码：工厂方法返回统一配置了代理、TLS、日志脱敏的 HttpClient 构建逻辑
// ChatModel model = OpenAiChatModel.builder()
//     .httpClientBuilder(companyHttpClientFactory.forLlmOutbound())
//     .baseUrl(endpointFromConfig)
//     ...
//     .build();
```

#### 挖深一层

- **可重试性**：LLM POST **不一定幂等**；重试要 **配额友好**（读 Retry-After）并设 **ceiling**。  
- **观测对齐**：在拦截器里打 **traceparent**，与网关 **access log** 对得上号才算**真全链路**。  
- **JDK vs OkHttp**：TLS 默认套件、HTTP/2 协商差异能让 **「我这里绿」**与 **「生产挂」**同时成立——**预发镜像**要同质。

## 4. 项目总结

### 优点

- **可插拔** HTTP 层，对接企业基建。  
- 与 **超时、重试、观测** 分层清晰。

### 缺点

- 配置项多，**排障门槛**高。  
- 不同 client 的 **TLS 行为** 差异需要预发验证。

### 适用场景

- 内网出口代理、专有云、双向证书。  
- 需要 **统一拦截器** 打审计日志的平台团队。

### 注意事项

- **不要**在拦截器日志里打印完整 API Key。  
- **连接池**与 Tomcat/Netty 线程池区分监控。

### 常见踩坑

1. **每请求 new Client** 导致句柄泄漏。  
2. 只加大 timeout 不修 **服务端真实故障**。  
3. **重试无上限** 放大供应商事故。

---

### 本期给测试 / 运维的检查清单

**测试**：混沌工程注入 **延迟/503**；断言重试次数与 **退避间隔**；验证 **非幂等** 场景的业务语义。  
**运维**：导出 **连接池、TLS 握手失败率、429 次数**；为每家 endpoint 设 **SLO** 与**升级联系人**。

### 附录：相关 Maven 模块与源码类

| 模块 | 说明 |
|------|------|
| `langchain4j-http-client`、`langchain4j-http-client-jdk` 等 | HTTP 抽象与实现 |
| `langchain4j-open-ai` | `OpenAiChatModel` 构建器上的客户端配置 |

推荐阅读：各 http-client 模块 README、open-ai 集成文档中的 Advanced / HTTP 小节。
