# 第 9 章：HTTP 客户端、超时与容错

## 1. 项目背景

### 业务场景（拟真）

企业内网出口 **代理、MITM 证书、双向 TLS、HTTP/2、连接池** 与 **限流** 与「家里直连 OpenAI」完全不同。平台组要求 **统一 HTTP 栈** 与 **可观测性**：traceId 要打到供应商侧可关联。LangChain4j 将 HTTP 封装在 **`langchain4j-http-client-*`**（JDK、Apache、OkHttp 等），在不 fork 整个库的前提下对齐公司标准。

### 痛点放大

若 **每请求 new Client** 或 **只调大 timeout**：**性能**上连接池耗尽、线程阻塞；**可用性**上 **retry storm** 放大供应商事故；**排障**上 **「本地绿、生产挂」** 常源于 **TLS 套件/HTTP/2 差异**。本章与第 5 章 `timeout`、第 7 章流式、第 36 章观测组成 **可靠生产调用** 闭环。

## 2. 项目设计：小胖、小白与大师的对话

**小胖**：默认 JDK HttpClient 不够好吗？跟 OkHttp 比呢？

**小白**：**429 要不要重试？5xx 呢？** **超时和断路器谁先谁后？**

**大师**：JDK 多数场景够用；要 **细调连接池、拦截器、旧 OkHttp 生态** 再换实现。**429** 要谨慎：读 **Retry-After**、**指数退避 + 抖动**、设 **最大重试 budget**；LLM POST **幂等性弱**，重复可能 **重复扣费**。**超时** 是单请求上限；**断路器** 是窗口成功率策略——**要一起配**。**技术映射**：**重试策略 = 配额友好 + 业务可接受重复风险**。

**小胖**：内网代理 **CONNECT 失败** 找谁？

**小白**：**连接池耗尽** 啥症状？**每家 LLM 要不同 client Bean 吗？**

**大师**：排障：**JVM 代理参数**、容器 hosts、公司 PAC；**JDK vs Apache** 代理配置差异。**耗尽**：排队、活跃连接顶满——调 **max connections** 与 **lease timeout**。常见 **按供应商分 Bean**（endpoint、TLS、代理不同）。**技术映射**：**HTTP 层 = 企业网络现实与 LLM 抽象的接缝**。

**小胖**：trace id 怎么传到供应商？

**大师**：**HTTP Header** 或 **vendor metadata**（若支持），与 **Span** 关联（第 36 章）。**技术映射**：**可观测 = 全链路 ID 对齐**。

---

## 3. 项目实战

### 环境准备

- 查阅 `langchain4j-open-ai` **当前 Javadoc** 中 `http`、`timeout`、`proxy` 相关 Builder（API 随版本微调）。  
- 仓库 `langchain4j/pom.xml` 检索 `langchain4j-http-client`。

### 分步任务

| 步骤 | 目标 | 操作 |
|------|------|------|
| 1 | 建立速查表 | Javadoc 圈出 **http/timeout/proxy** 方法 |
| 2 | 区分错误类型 | 错误 **baseUrl** / 无效 key，抓堆栈区分 **DNS/TLS/401** |
| 3 | 责任边界 | 伪代码讨论「TrustStore 谁维护？出网白名单谁批？」 |

```java
// 伪代码：工厂返回统一代理、TLS、日志脱敏的 HttpClient 构建逻辑
// ChatModel model = OpenAiChatModel.builder()
//     .httpClientBuilder(companyHttpClientFactory.forLlmOutbound())
//     .baseUrl(endpointFromConfig)
//     ...
//     .build();
```

**可能遇到的坑**：**可重试性**误判；拦截器打印 **完整 API Key**；**预发与生产 TLS 行为** 不一致。

### 测试验证

- **混沌**：延迟/503；断言 **重试次数与退避**；**非幂等** 场景业务语义。

### 完整代码清单

以当前 **`OpenAiChatModel` Builder** 与 `langchain4j-http-client-*` 文档为准。

---

## 4. 项目总结

### 优点与缺点（与同类做法对比）

| 维度 | 可插拔 http-client | 默认 JDK 仅调大 timeout | 侧车代理统一出口 |
|------|-------------------|-------------------------|------------------|
| 与企业栈对齐 | 高 | 中 | 高 |
| 配置复杂度 | 中 | 低 | 中 |
| 排障 | 需分层 | 易误判 | 依赖侧车质量 |
| 典型缺点 | TLS/行为差异 | 无重试治理 | 额外 hop |

### 适用场景

- 内网代理、专有云、双向证书；**统一拦截器** 审计日志。

### 不适用场景

- **纯公网 Demo**、无合规要求——可先默认客户端。  
- **厂商强制指定 SDK** 且不可注入 HTTP——需评估是否绕过 LangChain4j。

### 注意事项

- 拦截器 **勿打全量 Key**；**连接池** 与 Tomcat/Netty **分开监控**。

### 常见踩坑经验（生产向根因）

1. **每请求 new Client** → 句柄泄漏。  
2. **只加大 timeout** 不修根因。  
3. **重试无上限** → retry storm。

### 进阶思考题

1. 如何为 **LLM 请求** 设计 **幂等或去重键**（若厂商支持）？  
2. **HTTP/2 协商失败** 回退 **HTTP/1.1** 的观测信号？（提示：连接日志与 ALPN。）

### 推广计划提示（多部门）

| 角色 | 建议阅读顺序 | 协作要点 |
|------|----------------|----------|
| **开发** | 第 5 章 → 本章 → 第 36 章 | **拦截器脱敏** |
| **运维** | 本章 | **连接池、TLS 握手失败率、429**、SLO |
| **SRE** | 混沌 + 本章 | **retry budget** 与 **升级联系人** |

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
