# 第 9 章：HTTP 客户端、超时与容错

## 1. 项目背景

### 业务场景（拟真）

某金融科技公司的 Java 后端部署在私有云上，所有对外出站的 HTTP 请求——包括调用 OpenAI 等大模型 API——必须经过 **公司统一的 HTTP 代理**。代理有 MITM 证书、有双向 TLS 要求、有连接池限制（最大 200 个并发连接）。平台组要求：**所有 LLM 调用必须使用平台统一的 HTTP 客户端配置，traceId 要打到 OpenAI 侧，以便两边对账**。

团队拿到 `OpenAiChatModel.builder()` 后发现：默认的 HTTP 客户端用的是 JDK 内置的 `HttpClient`——它能通过代理出站吗？证书怎么配？连接池大小能改吗？traceId 传到哪个 Header 里？

### 痛点放大

如果团队不统一管理 HTTP 层，会出现：

- **连接池各自为政**：订单服务用 `OkHttp` 配了 50 个连接池、客服服务用 `HttpClient` 配了 200 个、退款服务没配用的默认——某个模型 API 响应慢了，每个服务的表现都不一样，排查时说不清是连接池问题还是模型问题。
- **重试风暴**：某个模型响应变慢了（比如 5xx 或超时），每个服务独立做了重试——结果一个用户请求触发了 3 次重试，100 个并发用户导致模型 API 收到了 400 个请求（100×3+100 原始），给模型 API 造成了更大的压力，形成正反馈恶性循环。
- **TLS 问题排查极困难**：「本地能跑，容器里报 SSL 握手错误」——80% 的情况是因为容器里没配公司的根证书信任链，或者代理设置不对。

本章与第 5 章、第 7 章、第 36 章共同组成 LLM 调用的 **生产可靠闭环**。

## 2. 项目设计：小胖、小白与大师的对话

**小胖**：JDK 自带的 HttpClient 不够好吗？为啥要搞 OkHttp 或者 Apache HttpClient 那么多选择？

**大师**：JDK 的 `HttpClient`（Java 11+）对大多数场景都够用——HTTP/2、连接池、异步调用都有。但也有它做不到的事情：① **细粒度的连接池调优**——比如设置每个路由的最大连接数、空闲连接的淘汰策略，JDK 原生的暴露不够灵活；② **拦截器机制**——你需要在每个请求前后做统一日志、注入 traceId、脱敏敏感头，JDK 的 `HttpClient` 没有 `Interceptor` 概念，要么用 `Filter`（有限），要么自己包装；③ **旧的 OkHttp 生态**——如果团队已有的基础设施（如监控、熔断器）是基于 OkHttp 拦截器构建的，迁移成本高。所以选择策略是：**默认用 JDK 的 HttpClient，只有当你明确需要以上三点之一时，再切换到 `langchain4j-http-client-okhttp` 或 `langchain4j-http-client-apache`**。

**小白**：那遇到 429（限流）要不要自动重试？5xx 呢？超时和断路器——这两个概念感觉有点重叠，谁先谁后？

**大师**：这是生产中最容易被忽视的问题。先说 **超时 vs 断路器**：超时是 **单个请求的约束**——「这个请求最多等 30 秒，超过就放弃」；断路器是 **一段窗口内成功率的约束**——「过去 1 分钟内，超过 50% 请求失败了，接下来 30 秒直接拒掉所有请求，不让它们发出」。职责明确：超时保护单次请求不被长尾拖死，断路器保护整个系统不被雪崩压垮。两者要一起配。再说 **重试策略**：**429（限流）和 5xx（服务端错误）要区别对待**。429 表示你发太快了，重试前必须读取 `Retry-After` 响应头，按照它指定的时间等待，再配合指数退避 + 随机抖动（jitter）。5xx 可以重试，但 LLM 的 POST 请求天然 **弱幂等**——同样一个「给我写一首诗」的请求重试两次，模型会生成两首不同的诗，但账单上记了两次调用。所以 **重试次数必须设上限**（推荐最多 2-3 次），并且要有一个「最大重试预算」的概念——如果系统里所有请求都在重试，重试本身的流量可能超过正常流量。**技术映射**：**超时 vs 断路器 = 单请求层级 vs 窗口层级的保护策略，两者是互补关系不是替代关系；重试策略必须友好对待 API 配额，且接受业务可接受的重复风险**。

**小胖**：我们公司内网有代理——Java 代码里配了 `-Dhttp.proxyHost` 但有时候 CONNECT 就是失败。这种问题该找谁？是 Java 的问题还是网络的问题？

**大师**：代理问题三板斧：第一板斧——**确认 JVM 参数生效了**。JDK 的代理参数区分 HTTP 和 HTTPS：`-Dhttp.proxyHost` 只对 HTTP 生效，HTTPS 请求用的是 `-Dhttps.proxyHost` 和 `-Dhttps.proxyPort`。第二板斧——**确认容器/服务器 hosts 配置**。很多代理问题不是 Java 代码层面的，是容器里 `/etc/hosts` 没有配全，或者 `NO_PROXY` 环境变量包含了目标域名。第三板斧——**确认公司的 PAC 文件或代理自动配置脚本**。有些公司用的是 WPAD 自动代理发现，Java 默认不支持，需要显式指定代理。关于连接池耗尽的症状：表现就是请求一直在排队等连接、大量 `TimeoutException` 说「连接超时」——但 curl 却能在命令行秒开。解法是调大连接池的 `maxConnections` 和 `leaseTimeout`。**技术映射**：**HTTP 层是企业网络现实与 LLM 优雅抽象之间最粗糙的接缝——这个地方出了问题，OpenAiChatModel 的 Builder 写得再优雅也没用。排障时不要只盯着代码，要看网络拓扑**。

## 3. 项目实战

### 环境准备

```bash
# 查看项目中使用的是哪种 HTTP 客户端
cd langchain4j
grep -r "http-client" pom.xml | head -10
```

### 步骤 1：建立 Builder 速查表

查阅 `OpenAiChatModel` Builder 的 Javadoc，圈出以下方法：

```java
OpenAiChatModel.builder()
    // HTTP 相关配置
    .timeout(Duration.ofSeconds(60))        // 请求超时
    .maxRetries(3)                           // 最大重试次数
    .proxy(ProxyOptions.builder()            // 代理配置
        .host("proxy.company.com")
        .port(8080)
        .build())
    .httpClientBuilder(httpClientBuilder)    // 自定义 HTTP 客户端
    .build();
```

把这几个方法抄到你的 cheat sheet。

### 步骤 2：区分三类错误

```bash
# 1. DNS 错误——填一个不存在的域名故意触发
curl https://api-nonexistent.openai.com/v1/chat/completions
# 错误特征：Name or service not known

# 2. TLS 错误——用 HTTP 而非 HTTPS
curl http://api.openai.com/v1/chat/completions
# 错误特征：SSL handshake failed / 301 redirect

# 3. 401 认证错误——用错误 Key
curl -H "Authorization: Bearer sk-invalid" https://api.openai.com/v1/chat/completions
# 错误特征：401 Unauthorized / Incorrect API key
```

```bash
# Java 端的对应：
# DNS 错误 → UnknownHostException
# TLS 错误 → SSLHandshakeException
# 400/401 → dev.langchain4j.exception.AuthenticationException
```

### 步骤 3：模拟重试行为（伪代码）

```java
// 生产环境不要这样写——这只是理解重试逻辑
public class RetryDemo {
    
    private static final int MAX_RETRIES = 3;
    
    public static void main(String[] args) {
        ChatModel model = OpenAiChatModel.builder()
                .apiKey(System.getenv("OPENAI_API_KEY"))
                .modelName(GPT_4_O_MINI)
                .maxRetries(0)  // 关掉框架自动重试，手动演示
                .build();
        
        int attempt = 0;
        while (attempt < MAX_RETRIES) {
            try {
                String answer = model.chat("Say Hello");
                System.out.println("Success: " + answer);
                break;
            } catch (Exception e) {
                attempt++;
                System.out.println("Attempt " + attempt + " failed: " + e.getMessage());
                if (attempt == MAX_RETRIES) {
                    System.out.println("All retries exhausted");
                } else {
                    // 退避等待（指数退避：1s, 2s, 4s）
                    Thread.sleep((long) Math.pow(2, attempt) * 1000);
                }
            }
        }
    }
}
```

### 步骤 4：区分四种超时

| 超时类型 | 设置位置 | 作用 |
|---------|---------|------|
| 连接超时 | `connectTimeout()` | TCP 握手最长期限 |
| 读取超时 | `timeout()` 或 `readTimeout()` | 等待响应的最长期限 |
| 网关超时 | Nginx `proxy_read_timeout` | 反向代理侧等待 |
| 厂商超时 | OpenAI 侧设置（不公开） | 厂商侧请求时长 |

```bash
# 生产排查：你的超时链
# [用户] → [网关/Nginx] → [Java 服务] → [LLM API]
# 确保：连接超时 < 读取超时 < 网关超时
```

### 可能遇到的坑

| 坑 | 表现 | 解法 |
|----|------|------|
| 每请求 new Client | 句柄泄漏 | 复用 Bean |
| 只加大 timeout 不修根因 | 掩盖了连接池/网络问题 | 先定位根因再调参 |
| 重试无上限 | retry storm 拖垮后端 | 设 maxRetries + 退避 |
| 拦截器打印 API Key | 日志中密钥泄露 | 脱敏后再写日志 |

### 测试验证

```bash
# 混沌工程：模拟延迟
curl -H "Authorization: Bearer $OPENAI_API_KEY" \
  --max-time 1 \
  https://api.openai.com/v1/chat/completions \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"hi"}]}'

# 如果 --max-time 1 就超时，说明 1s 不够
# 调整你的 timeout 配置
```

### 完整代码清单

Builder 文档：`OpenAiChatModel`、`langchain4j-http-client-*` 各模块 README。

## 4. 项目总结

### 优点与缺点

| 维度 | 可插拔 http-client | 默认 JDK | 侧车代理 |
|------|-------------------|---------|----------|
| 与企业栈对齐 | 高 | 中 | 高 |
| 配置复杂度 | 中 | 低 | 中 |
| 典型缺点 | TLS/行为差异 | 无重试治理 | 额外 hop |

### 适用 / 不适用场景

**适用**：内网代理、专有云、双向证书、统一拦截器审计。

**不适用**：纯公网 Demo 无合规要求（可先默认客户端）、厂商强制指定 SDK 不可注入 HTTP。

### 常见踩坑

1. 每请求 new Client → 句柄泄漏
2. 只加大 timeout 不修根因 → 掩盖连接池问题
3. 重试无上限 → retry storm

### 进阶思考题

1. 如何为 LLM 请求设计幂等或去重键（若厂商支持）？
2. HTTP/2 协商失败回退 HTTP/1.1 的观测信号？

### 推广计划

| 角色 | 建议阅读顺序 | 协作要点 |
|------|-------------|----------|
| 开发 | 第 5 章 → 本章 → 第 36 章 | 拦截器脱敏 |
| 运维 | 本章 | 连接池、TLS 握手失败、429 SLO |
| SRE | 混沌 + 本章 | retry budget 与升级联系人 |

### 检查清单

- **测试**：混沌工程注入延迟/503；断言重试次数与退避间隔
- **运维**：导出连接池、TLS 握手失败率、429 次数；为每家 endpoint 设 SLO

### 附录

| 模块 | 说明 |
|------|------|
| `langchain4j-http-client`、`langchain4j-http-client-jdk` 等 | HTTP 抽象与实现 |
| `langchain4j-open-ai` | `OpenAiChatModel` Builder 上的客户端配置 |

推荐阅读：各 http-client 模块 README。
