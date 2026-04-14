# 第 4 章：ChatModel 初体验 —— Hello World

## 1. 项目背景

在企业里第一次落地大语言模型（LLM）时，最常见的诉求不是「立刻做出 Agent」，而是：**用 Java 发一条用户消息，拿回一段自然语言回答**，并把它接进现有订单系统、知识库或内部门户的后端。直连某家云厂商的 HTTP API 当然可行，但后续一旦需要切换模型、统一观测或接入记忆与工具，分散在各处的客户端代码会迅速变成「一坨胶水」。

LangChain4j 在模型层提供的核心入口之一是 **`ChatModel`**（以及与之配套的流式 `StreamingChatModel`）。它把「配置密钥、模型名、请求构造、响应解析」收束为少量 Builder 与同步方法，让业务代码保持面向领域问题，而不是面向 HTTP 字段名。

官方教程中的 `_00_HelloWorld` 位于：

- `langchain4j-examples/tutorials/src/main/java/_00_HelloWorld.java`

本章定位在**基础篇的最小闭环**：在本地或 CI 中能稳定打印一次模型回复，为后续章节中的 Prompt、Memory、Tools、RAG 打下基础。

## 2. 项目设计：大师与小白的对话

**小白**：我看了 README，说 LangChain4j 有「统一 API」。那 Hello World 里我到底统一了什么？

**大师**：你统一的首先是「**聊天抽象**」。无论底层是 OpenAI、Azure、Bedrock 还是 Ollama，你在业务里主要面对的都是「给我一段用户文本 → 你给我模型生成的回答」。具体用什么 wire protocol、什么 JSON 字段，被下推到各个 `langchain4j-*` 模块里。

**小白**：我看到示例里用的是 `OpenAiChatModel`，这跟「统一」不矛盾吗？

**大师**：不矛盾。统一体现在**调用形态**：你先写 `OpenAiChatModel.builder()...build()`，将来若换成别的 provider，通常只需要换 Builder 类型与依赖坐标，**外层业务仍然拿 `ChatModel` 接口做事**。当然，不同模型能力（上下文长度、工具调用、图像）仍有差异，那是能力矩阵问题，不是语法胶水问题。

**小白**：`ChatModel` 和以前文档里写的 `ChatLanguageModel` 是同一个东西吗？

**大师**：在较新的 API 演进中，项目把面向聊天的主接口命名收敛为 `ChatModel`。你在老文章或旧版本示例里可能看到 `ChatLanguageModel`——阅读源码或迁移时，把它当作**同一条产品线的前后命名**，以当前版本 Javadoc 为准。

**小白**：`model.chat("Say Hello World")` 会带上系统提示词吗？

**大师**：这一发是**最简单的单轮用户消息**封装。系统提示（system）、多轮 `UserMessage`/`AiMessage` 会在后续「Memory」「Messages API」中展开。Hello World 刻意保持最薄的一层，让你先确认：**网络、密钥、模型名**三件事无误。

**小白**：那我在生产里可以每个请求 `new` 一个 `OpenAiChatModel` 吗？

**大师**：技术上可以，但不值得。客户端内部通常持有 HTTP 连接池与序列化器，**应在应用生命周期内复用单例 Bean**（Spring/Quarkus/CDI），按需按租户或多模型拆多个 Bean。否则你会在高并发下徒增连接开销与 GC 压力。

**小白**：如果接口超时，LangChain4j 会重试吗？

**大师**：Hello World 不会自动替你完成「完整的弹性策略」。超时、重试、熔断往往需要结合 HTTP 客户端实现与观测模块配置 —— 这正是第 9 章、第 36 章要接上的内容。

**小白**：输出 `String` 够不够用？

**大师**：对演示与简单场景够用。若要**流式输出 token**、需要 **usage/token 统计**、要 **tool call**，会换用其它 API（如 `StreamingChatModel`、监听 `ChatModelListener`）。本章先把同步字符串跑通。

**小白**：测试环境没有外网怎么办？

**大师**：可以换成本地 **`langchain4j-ollama`** 或公司内部托管的兼容端点，只要对应模块提供 `ChatModel` 实现即可；关键是**把「模型端点」变成配置项**，不要把 URL 写死在 Hello World 的 main 里。

## 3. 项目实战：主代码片段

> **场景入戏**：这是你的 **「第一声啼哭」**——还不涉及记忆、工具和向量库，只证明三件事：**密钥合法**、**网络可达**、**模型名字写对**。能跑通这一天，后面所有花哨章节才有地基。

下面是与仓库一致的核心片段（类名 `_00_HelloWorld` 仅为教程命名，生产请换成 `HelloWorldApplication` 一类可读名）：

```java
import dev.langchain4j.model.chat.ChatModel;
import dev.langchain4j.model.openai.OpenAiChatModel;

import static dev.langchain4j.model.openai.OpenAiChatModelName.GPT_4_O_MINI;

public class _00_HelloWorld {

    public static void main(String[] args) {

        ChatModel model = OpenAiChatModel.builder()
                .apiKey(ApiKeys.OPENAI_API_KEY)
                .modelName(GPT_4_O_MINI)
                .build();

        String answer = model.chat("Say Hello World");

        System.out.println(answer);
    }
}
```

**仓库锚点**：[`langchain4j-examples/tutorials/src/main/java/_00_HelloWorld.java`](../../langchain4j-examples/tutorials/src/main/java/_00_HelloWorld.java)。打包与命令行传参见同目录 [`tutorials/README.md`](../../langchain4j-examples/tutorials/README.md)。

#### 三行读懂（别跳过）

1. **`OpenAiChatModel.builder()`**：把厂商方言收成 **链式 Java**；`GPT_4_O_MINI` 来自枚举，防手滑拼错模型代号。  
2. **`model.chat(String)`**：**甜腻语法糖**——内部仍是一轮 `UserMessage` → 模型 → `String`，调试复杂会话时要会「脑补」成消息列表。  
3. **`ApiKeys.*`**：教程收纳盒；**上线请换成** `System.getenv` / Vault ——否则下一个登上公司内部耻辱柱的就是你。

#### 闯关任务（由浅入深）

| 难度 | 动手 | 过关标准 |
|------|------|----------|
| ★ | IDE 直接 Run，再故意填错 API Key | 学会读 **401 / invalid_api_key**，别只会说「红了」 |
| ★★ | 把提示从 English 换成一句中文绕口令 | 观察 **时延**与 **是否拒答**（模型策略差异） |
| ★★★ | 按 README 打 fat-jar，`java -cp ... _00_HelloWorld "你的 args"` | ** Classpath 地狱**预演——生产 Dockerfile 也会遇见 |

#### 挖深一层（原理与边界）

- **依赖方向**：业务只握 `ChatModel` 接口（`langchain4j-core`），实现类在 **`langchain4j-open-ai`**——换 Ollama 时换 Builder，**调用点可不动**。  
- **阻塞模型**：`chat` 在 **当前线程**阻塞到写完最后一个 token；高并发 Web 里要配 **线程池隔离**（第 7、34 章收回这个伏笔）。  
- **Listeners**：若要在不改业务代码的情况下统一打点和脱敏，请预习 `ChatModelListener`（`MyChatModelListener` 在 Spring 示例中）。  
- **趣味冷知识**：`chat("Say Hello World")` 比你想象得更「重」——底下是一次完整 HTTPS JSON round-trip；**别在循环里 accidentally 打爆配额**。

## 4. 项目总结

### 优点

- **上手路径极短**：数行代码即可验证账号与网络，适合作为部门内「第一条 LLM 链路」冒烟测试。
- **面向接口**：业务依赖 `ChatModel`，便于单元测试中用 fake/stub 替换真实 HTTP。
- **与生态对齐**：同一抽象可衔接到 `AiServices`、记忆、工具与 RAG，不卡在「裸 HTTP」层。

### 缺点

- **能力暴露最简**：单字符串 `chat` 不便表达复杂多轮与系统指令。
- **缺少开箱即用的弹性**：重试、限速、隔离需要你自己拼基础设施层。
- **供应商锁定风险仍存在**：即便接口统一，`OpenAiChatModel` 的配置项仍带有厂商特性，迁移时要评估参数映射。

### 适用场景

- 教学、PoC、网关后的「简单问答」微服务。
- 在引入 `AiServices` 之前的底层连通性验证。

### 注意事项

- **密钥不可入仓**：`ApiKeys` 仅用于示例；生产请使用秘密管理。
- **模型与数据合规**：输入是否出网、是否允许境外模型，需法务与运维前置确认。
- **成本可见性**：同步 `String` 不直观展示 token 用量，应配合 `ChatModelListener` 或观测指标（第 36 章）。

### 常见踩坑

1. **版本混用**：Aggregator 多模块时，务必用 **BOM** 对齐 `langchain4j` 与 `langchain4j-open-ai` 版本（第 2 章）。
2. **代理与公司证书**：内网 MITM 导致 TLS 失败时，需要在 JVM 或 HTTP 客户端层 truststore，而不是怀疑 `ChatModel`「坏了」。
3. **把 Hello World 直接搬上生产 main**：缺少超时与限流，会在流量尖峰把线程池打满。

---

### 本期给测试 / 运维的检查清单

**测试**：为「空提示」「超长提示」「含敏感词」各准备 1 条用例，断言返回非空且应用层有兜底文案；在 mock `ChatModel` 的稳定测试与「沙箱环境真实调用」冒烟测试之间划清分层。

**运维**：确认应用出站域名、TLS 版本与出口防火墙；将 API Key 轮换流程写成 Runbook；对首次全链路成功请求打点耗时（P95）作为基线，后续变更只比对 delta。

### 附录：相关 Maven 模块与源码类

| 模块 | 说明 |
|------|------|
| `langchain4j-core` | `ChatModel` 接口 |
| `langchain4j-open-ai` | `OpenAiChatModel` 实现 |

推荐阅读（3～5 个）：`ChatModel.java`、`OpenAiChatModel.java`、`OpenAiChatModelName`。
