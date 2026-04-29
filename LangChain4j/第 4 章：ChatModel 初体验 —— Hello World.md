# 第 4 章：ChatModel 初体验 —— Hello World

## 1. 项目背景

### 业务场景（拟真）

你的任务是让一个 Java 程序说出人生中的第一句 AI 回应：「Say Hello World」。这件事听起来简单，但对于一个每天写 CRUD 的 Java 团队来说，它代表了 **技术栈的一次质变**——从「调数据库」到「调大模型」。老张把任务分给了刚入职的小张：**「今天之内，在本地跑通第一行代码，证明三件事——密钥合法、网络可达、模型名写对。」**

这看起来只需要几十行代码，但小张发现选项很多：直接发 HTTP 请求到 OpenAI？用官方的 Java SDK？用 Spring 的 RestTemplate？如果下周要切换到 Azure 呢？如果本地没外网要走内网代理呢？如果团队想在同一套代码里对比 GPT-4 和国产模型的效果呢？

LangChain4j 在模型层提供的核心入口是 **`ChatModel`**（以及配套的流式 `StreamingChatModel`）。它把「配置密钥、模型名、请求构造、响应解析」收束为一个 Builder 加一个方法的调用——`model.chat("Say Hello World")`。无论底层是 OpenAI、Azure、Bedrock 还是 Ollama，调用形态都一样：用户文本进 → 模型文本出。

### 痛点放大

如果团队选择直接调 HTTP API——比如用 `HttpURLConnection` 发 POST 到 `https://api.openai.com/v1/chat/completions`——确实能在 20 行代码内跑通。但代价是什么？

- **每个集成都得重写认证逻辑**：OpenAI 用 Bearer token，Azure 用 API-Key 头，Ollama 不用认证——这三套代码无法复用。
- **请求/响应格式不同**：OpenAI 的请求体是 `{"model":"gpt-4","messages":[...]}`，Azure 需要 `deployment-id` 参数，Ollama 的格式又不一样。
- **切换模型 = 重写控制器**：产品说「这个月用 GPT-4，下个月试试国产模型」，你就要重写或复制一套新的 HTTP 调用代码。

这些散落在各处的 HTTP 调用，就是一开始 **欠下的技术债**。而 `ChatModel` 接口要做的就是：**让你的业务代码永远只面对 `model.chat(String)` 这一个方法**，切换模型就是换一个 Builder 的事。

## 2. 项目设计：小胖、小白与大师的对话

**小胖**：我就想让程序说一句 Hello World，这跟去便利店买瓶水一样简单吧？扫码付款拿走——为啥还要引一个「LangChain4j」这么大的框架？

**大师**：你说得对，买一瓶水确实不需要研究供应链。但如果下个月你要同时卖三个牌子的水（OpenAI、Azure、国产模型），每个牌子的扫码枪（认证方式、消息格式）都不同——你是每个牌子单独买一台收银机，还是统一用一个收银系统，只换进货渠道？LangChain4j 就是那个统一的收银系统：不管你的水（模型）从哪来，收银员的操作都是一样的 `model.chat("Hello")`。

**小白**：好，统一抽象我理解了。但 Hello World 这个例子里到底统一了什么——我看到的明明就是 `OpenAiChatModel` 这个具体的类名，跟 `OkHttpClient` 调 OpenAI 有多大区别？

**大师**：你看到的 `OpenAiChatModel` 只是 **当前的实现**。关键区别在于你的业务代码 `Assistant` 依赖的是 `ChatModel` **接口**——这个接口定义在 `langchain4j-core` 中，与具体厂商无关。当你的代码写成这样：

```java
public class OrderAssistant {
    private final ChatModel model;  // 面向接口
    
    public OrderAssistant(ChatModel model) {  // 构造注入
        this.model = model;
    }
}
```
就不需要改 `OrderAssistant` 的任何代码。换模型时，你只需要改 **组装的那一行**——调用不同的 Builder。而直连 OpenAI SDK 的话，你的 `OrderAssistant` 里到处都是 `OpenAiClient` 的调用，换模型等于重写。**技术映射**：**`ChatModel` 接口 = 业务代码与模型实现之间的契约；Builder = 厂商专属的适配器工厂——接口保证可替换，Builder 承担差异**。

**小白**：那生产环境里能每个请求都 `new` 一个 `OpenAiChatModel` 对象吗？如果模型调用超时了，库会自动重试吗？

**大师**：**绝对不能每个请求都 `new` 一个新的。** `OpenAiChatModel` 内部包含连接池（默认是 `HttpClient` 的内置连接池）和 Jackson 序列化器——如果每个请求都重新创建，每次都要新建 TCP 连接、初始化序列化器，徒增 GC 压力和延迟。正确的方式是在 **应用生命周期内复用单例 Bean**（Spring 的 `@Bean`、Quarkus 的 `@Singleton`）。关于超时和重试：**Hello World 不负责弹性**——`model.chat()` 默认没有任何重试逻辑，超时了直接抛异常。超时策略、重试退避、熔断降级，这些需要结合 HTTP 客户端的配置（第 9 章）和统一观测（第 36 章）来搭建。**技术映射**：**ChatModel 实例有状态（连接池），生命周期应由 DI 容器管理；弹性策略不属于接口契约，属于基础设施层的职责**。

**小胖**：那如果我测试机上没外网咋办？输出就是一个 `String`，够不够用？

**大师**：没外网就换 `langchain4j-ollama` 或公司的内网兼容端点——只要那个 provider 也实现了 `ChatModel` 接口，你的业务代码一行都不用改。端点的 URL 必须 **配置化**（从 `application.properties` 或环境变量读），绝对不能硬编码在 `main` 方法里。至于 `model.chat()` 返回的 `String`——对初学者和一问一答场景完全够用；后续需要体验打字机效果（流式）、统计 token 用量、调试工具调用时，再换成 `StreamingChatModel` 和 `ChatModelListener`。**技术映射**：**Hello World 验证的只有三件事：密钥有效、网络可达、模型名正确——你不需要在设计模式上过度设计，先让火车跑起来**。

## 3. 项目实战

### 环境准备

```bash
# 确认已进入 tutorials 模块
cd langchain4j-examples/tutorials

# 确认 pom.xml 已引入相关依赖（见第 2 章 BOM 检查）
mvn -q dependency:tree -Dincludes=dev.langchain4j

# 准备 API Key（不要写死在代码里！）
export OPENAI_API_KEY="sk-your-key-here"   # macOS/Linux
# set OPENAI_API_KEY=sk-your-key-here      # Windows
```

### 步骤 1：跑通 Hello World

```java
// 文件路径：tutorials/src/main/java/_00_HelloWorld.java
import dev.langchain4j.model.chat.ChatModel;
import dev.langchain4j.model.openai.OpenAiChatModel;

import static dev.langchain4j.model.openai.OpenAiChatModelName.GPT_4_O_MINI;

public class _00_HelloWorld {

    public static void main(String[] args) {

        ChatModel model = OpenAiChatModel.builder()
                .apiKey(System.getenv("OPENAI_API_KEY"))
                .modelName(GPT_4_O_MINI)
                .build();

        String answer = model.chat("Say Hello World");

        System.out.println(answer);
    }
}
```

```bash
# 运行（在 IDE 中或命令行）
mvn exec:java -Dexec.mainClass="_00_HelloWorld"
```

**预期输出**：
```
Hello! How can I assist you today?
```

（具体文案因模型版本而异，关键是成功返回一段文本而不是报错。）

### 步骤 2：破坏实验——填错 API Key

```java
// 改一行
.apiKey("sk-invalid-key")
```

重新运行，**预期报错**：
```
Exception in thread "main" dev.langchain4j.exception.AuthenticationException: 
  statusCode: 401 Unauthorized
  message: {"error":{"message":"Incorrect API key provided.","type":"invalid_request_error"}}
```

学会读这个错误：**401 = 密钥问题**，不是网络问题也不是模型问题。

### 步骤 3：破坏实验——填错模型名

```java
// 改一行
.modelName("gpt-9999")
```

**预期报错**：
```
Exception in thread "main" dev.langchain4j.exception.ModelNotFoundException: 
  statusCode: 404
  message: {"error":{"message":"The model `gpt-9999` does not exist"}}
```

### 步骤 4：打 fat-jar 并命令行运行

```bash
# 打包
mvn -Pcomplete package -DskipTests

# 命令行运行
java -cp target/tutorials-*-jar-with-dependencies.jar _00_HelloWorld
```

这是一个 **Classpath 地狱预演**——生产 Dockerfile 里也会遇到同样的 jar 依赖问题。

### 三行读懂核心代码

```java
// 1. Builder → 把厂商方言收成链式 Java，GPT_4_O_MINI 来自枚举防手滑
ChatModel model = OpenAiChatModel.builder().apiKey(...).modelName(...).build();

// 2. chat(String) → 语法糖，内部是一轮 UserMessage → 模型 → String
String answer = model.chat("Say Hello World");

// 3. ApiKeys → 教程收纳盒；上线请换 System.getenv / Vault
.apiKey(System.getenv("OPENAI_API_KEY"))
```

### 闯关任务

| 难度 | 动手操作 | 过关标准 |
|------|---------|----------|
| ★ | IDE 直接 Run，故意填错 Key | 学会读 401，别只会说「红了」 |
| ★★ | 提示改成中文绕口令 | 观察时延与是否拒答 |
| ★★★ | 打 fat-jar 命令行运行 | Classpath 地狱预演 |

### 挖深一层

- **依赖方向**：业务握 `ChatModel` 接口（core），实现类在 provider——换 Ollama 时只换 Builder，调用点不动
- **阻塞模型**：`chat()` 在当前线程阻塞到写完最后一个 token；高并发要配线程池隔离（第 7、34 章）
- **冷知识**：`chat("Say Hello World")` 是一次完整 HTTPS JSON round-trip，别在循环里打爆配额

### 可能遇到的坑

| 坑 | 表现 | 解法 |
|----|------|------|
| 版本混用 | NoSuchMethodError | 第 2 章 BOM 检查 |
| 公司代理/TLS 证书 | SSL 握手失败 | 配 JVM truststore |
| Key 写死在代码里 | 提交到 Git 泄露 | 永远用环境变量 |
| Hello World 当生产代码 | 无超时限流 | 上线前加 timeout 和线程池 |

### 测试验证

```bash
# 验证连通性的最快方法
curl -N https://api.openai.com/v1/chat/completions \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"Say Hello World"}]}'
```

如果 curl 能通但 Java 代码不通，问题在你的 Java 代码中；反之亦然。

### 完整代码清单

[`_00_HelloWorld.java`](../../langchain4j-examples/tutorials/src/main/java/_00_HelloWorld.java)

## 4. 项目总结

### 优点与缺点

| 维度 | ChatModel + LangChain4j | 手写 HTTPS + JSON | 仅 curl 测通 |
|------|------------------------|-------------------|-------------|
| 上手速度 | 快 | 慢 | 快但不进工程 |
| 可测性 | 易 mock | 难 | 难纳入 CI |
| 换模型 | 换配置为主 | 大量重写 | 脱节 |
| 典型缺点 | 单字符串表达力有限 | 安全/观测脱节 | 不可维护 |

### 适用 / 不适用场景

**适用**：教学 PoC、连通性验证、多模型 smoke 测试。

**不适用**：仅需一次 curl 证明密钥有效、强依赖某厂商私有流式协议。

### 常见踩坑

1. 版本混用不用 BOM → NoSuchMethodError
2. 公司 MITM 证书导致 TLS 失败 → 误判 ChatModel 损坏
3. Hello World 直接搬上生产 → 无超时限流导致线程池耗尽

### 进阶思考题

1. 若要将 `OpenAiChatModel` 换成 Ollama，依赖坐标与 Builder 配置最少改哪几处？
2. 单测中如何不发起真实 HTTP 仍覆盖 `model.chat(...)` 业务分支？

### 推广计划

| 角色 | 建议阅读顺序 | 协作要点 |
|------|-------------|----------|
| 开发 | 第 2 章 → 本章 → 第 5～7 章 | 禁止在生产每请求 new 模型客户端 |
| 测试 | 本章 + 冒烟用例矩阵 | 错 Key / 超时 / 空提示 三类用例 |
| 运维 | 本章 + 第 9 章 | 出站域名、TLS、Key 轮换 |

### 检查清单

- **测试**：为空提示、超长提示、含敏感词各准备 1 条用例
- **运维**：确认出站域名、TLS 版本与出口防火墙；对首次请求打点耗时（P95）作为基线

### 附录

| 模块 | 说明 |
|------|------|
| `langchain4j-core` | `ChatModel` 接口 |
| `langchain4j-open-ai` | `OpenAiChatModel` 实现 |

推荐阅读：`ChatModel.java`、`OpenAiChatModel.java`、`OpenAiChatModelName`。
