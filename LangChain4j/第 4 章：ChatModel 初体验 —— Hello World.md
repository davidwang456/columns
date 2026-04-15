# 第 4 章：ChatModel 初体验 —— Hello World

## 1. 项目背景

在企业里第一次落地大语言模型（LLM）时，最常见的诉求不是「立刻做出 Agent」，而是：**用 Java 发一条用户消息，拿回一段自然语言回答**，并把它接进现有订单系统、知识库或内部门户的后端。直连某家云厂商的 HTTP API 当然可行，但后续一旦需要切换模型、统一观测或接入记忆与工具，分散在各处的客户端代码会迅速变成「一坨胶水」。

LangChain4j 在模型层提供的核心入口之一是 **`ChatModel`**（以及与之配套的流式 `StreamingChatModel`）。它把「配置密钥、模型名、请求构造、响应解析」收束为少量 Builder 与同步方法，让业务代码保持面向领域问题，而不是面向 HTTP 字段名。

官方教程中的 `_00_HelloWorld` 位于：

- `langchain4j-examples/tutorials/src/main/java/_00_HelloWorld.java`

本章定位在**基础篇的最小闭环**：在本地或 CI 中能稳定打印一次模型回复，为后续章节中的 Prompt、Memory、Tools、RAG 打下基础。

## 2. 项目设计：小胖、小白与大师的对话

**小胖**：我就想让程序说一句 Hello World，这跟去便利店买瓶水一样简单吧？为啥还要引一个「LangChain」？

**小白**：README 里说「统一 API」——Hello World 里到底 **统一了什么**？我看到的明明是 `OpenAiChatModel` 啊。

**大师**：你统一的首先是「**聊天抽象**」：业务面对 **`ChatModel` 接口**（`langchain4j-core`），而不是某家厂商的 JSON 字段名。`OpenAiChatModel` 只是当前选中的 **实现与 Builder**。无论底层是 OpenAI、Azure、Bedrock 还是 Ollama，调用形态都是「用户文本进 → 模型文本出」。**技术映射**：**接口 = 契约，Builder = 厂商适配器**。

**小胖**：哦……那不就跟点外卖：App 上都是「下单」，后面是美团还是饿了么骑手我不管？

**小白**：补充：**老文档里的 `ChatLanguageModel` 和现在的 `ChatModel` 啥关系？** `model.chat("Say Hello World")` 会带 **system prompt** 吗？

**大师**：命名演进里，面向聊天的主接口已收敛为 **`ChatModel`**；旧文里的 `ChatLanguageModel` 当作 **同产品线的前后名**，以当前 Javadoc 为准。`chat(String)` 是 **最薄单轮用户消息**，不含 system；多轮与 system 在 **Memory / Messages API** 展开。**技术映射**：**语法糖背后是 `UserMessage` → 模型 → 文本**，调试复杂会话时要会「脑补」消息列表。

**小白**：生产里能 **每个请求 `new` 一个 `OpenAiChatModel`** 吗？超时了库会 **自动重试** 吗？

**大师**：客户端里常有 **连接池与序列化器**，应在 **应用生命周期内复用 Bean**（Spring / Quarkus / CDI），多租户可多 Bean；每请求 `new` 会徒增连接与 GC。**Hello World 不负责完整弹性**——超时、重试、熔断要结合 HTTP 客户端与第 9、36 章。**技术映射**：**对象生命周期与弹性策略属于基础设施层**。

**小胖**：那我测试机没外网咋办？输出就一个 `String`，够不够啊？

**大师**：没外网可换 **`langchain4j-ollama`** 或内网兼容端点，只要模块提供 `ChatModel`；**端点必须配置化**，勿写死在 `main`。同步 `String` 对演示够用；要 **流式 / token 统计 / tool call** 再换 `StreamingChatModel`、`ChatModelListener` 等。**技术映射**：**Hello World 只验证连通性三要素：密钥、网络、模型名**。

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

### 优点与缺点（与同类方式对比）

| 维度 | `ChatModel` + LangChain4j | 手写 HTTPS + JSON | 仅脚本 / curl 测通 |
|------|---------------------------|---------------------|----------------------|
| 上手速度 | 快（Builder + 枚举模型名） | 慢（字段与序列化自管） | 快但不进工程体系 |
| 可测性 | 易 mock `ChatModel` | 难（需桩 HTTP） | 难纳入 CI |
| 演进（换模型） | 换实现类与配置为主 | 大量胶水重写 | 与业务代码脱节 |
| 典型缺点 | 单字符串 API 表达力有限 | 易与安全/观测脱节 | 不可维护 |

**文字补充（优点）**：**上手路径极短**；**面向接口**便于单测替换；**与 AiServices / RAG 同轨**。

**文字补充（缺点）**：**能力暴露最简**；**弹性与限流**需自拼；**厂商配置项**仍在 Builder 上，迁移要对照能力矩阵。

### 适用场景

- 教学、PoC、网关后的「简单问答」微服务。
- 在引入 `AiServices` 之前的 **底层连通性验证**。
- 需要 **同一套 Java 工程** 内做多模型 smoke 测试。

### 不适用场景

- **仅需一次性 curl 证明密钥有效**、且无意维护 Java 服务——不必引入完整抽象。
- **强依赖某厂商独有流式协议细节**且不接受任何封装——可能需更底层客户端（仍建议观测层统一）。

### 注意事项

- **密钥不可入仓**：`ApiKeys` 仅用于示例；生产请使用秘密管理。
- **模型与数据合规**：输入是否出网、是否允许境外模型，需法务与运维前置确认。
- **成本可见性**：同步 `String` 不直观展示 token 用量，应配合 `ChatModelListener` 或观测指标（第 36 章）。

### 常见踩坑经验（生产向根因）

1. **版本混用**：多模块时未用 **BOM** 对齐 `langchain4j` 与 `langchain4j-open-ai`（第 2 章）→ `NoSuchMethodError`。
2. **代理与公司证书**：内网 MITM 导致 TLS 失败 → 需在 JVM / HTTP 客户端配置 truststore，而非误判 `ChatModel` 损坏。
3. **把 Hello World 直接搬上生产**：无超时与限流 → 流量尖峰 **线程池耗尽**。

### 进阶思考题

1. **若要将 `OpenAiChatModel` 换成 Ollama**，在 **依赖坐标** 与 **Builder 配置** 上最少改哪几处？（答案线索：第 2 章 BOM + 对应 provider 模块。）  
2. **单测中如何不发起真实 HTTP** 仍覆盖 `model.chat(...)` 业务分支？（提示：mock / fake `ChatModel`；与第 12 章 AiServices 可组合。）

### 推广计划提示（多部门）

| 角色 | 建议阅读顺序 | 协作要点 |
|------|----------------|----------|
| **开发** | 第 2 章 → 本章 → 第 5～7 章 | **禁止**在生产每请求 `new` 模型客户端 |
| **测试** | 本章 + 冒烟用例矩阵 | **错 Key / 超时 / 空提示** 三类用例 |
| **运维** | 本章 + 第 9 章 | 出站域名、TLS、API Key 轮换 Runbook |

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
