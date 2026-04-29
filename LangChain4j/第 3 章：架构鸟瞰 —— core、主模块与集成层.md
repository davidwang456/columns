# 第 3 章：架构鸟瞰 —— core、主模块与集成层

## 1. 项目背景

### 业务场景（拟真）

平台组要把 LangChain4j 纳入公司 **标准技术栈**，下周三的架构评审会上，首席架构师会问三个问题：**「报错时我该去看哪个模块的源码？」「实验特性能不能进核心交易链路？」「为什么不用一个 jar 包搞定而是要引十几个模块？」**

新人小张入职第一天，打开 `langchain4j/pom.xml`，看到 `<modules>` 标签下列了 **四十多个子模块**——`langchain4j-core`、`langchain4j`、`langchain4j-open-ai`、`langchain4j-ollama`、`langchain4j-pgvector`、`langchain4j-milvus`、`langchain4j-http-client-jdk`、`langchain4j-mcp`、`langchain4j-guardrails`……他完全分不清这些模块的职责边界是什么。排障的时候，只能在整个仓库里全局 grep 搜索关键字——而不是根据「这个问题出在哪个抽象层」来快速定位。

### 痛点放大

没有分层心智时，会出现三种典型困境：

- **性能误判**：某个接口响应慢，一线开发直觉认为「是模型慢」，实际上是因为 HTTP 客户端连接池耗尽导致请求排队——但因为他分不清 `ChatModel` 调用链路经过了 core（接口）→ open-ai（序列化）→ http-client-jdk（传输），所以定位错了方向。
- **一致性失控**：业务 A 引了 `langchain4j` 主模块加 `langchain4j-mcp`，业务 B 只引了 `langchain4j-core` 加 `langchain4j-open-ai`，结果 BOM 混引导致 `NoSuchMethodError`——但因为没人清楚各模块之间的 BOM 依赖关系，排查了两天。
- **可维护性陷阱**：一个金融科技团队把 `langchain4j-guardrails`（稳定）和 `langchain4j-mcp`（实验）同时塞进同一个 war 包——结果 MCP 的实验 API 在 MINOR 版本升级时做了 breaking change，导致整个支付服务发布阻塞。

```text
业务代码 → langchain4j（AiServices 等组合层）→ langchain4j-core（接口层）
                ↑                                      ↑
                └── provider / http-client / embedding-store 适配层
```

## 2. 项目设计：小胖、小白与大师的对话

**小胖**：这仓库模块比商场楼层还多，我就写个聊天功能，为啥不能一个 jar 搞定？就像快餐套餐一样，一个 jar 啥都有。

**小白**：我最该先读哪个模块？core 为啥不直接依赖 OkHttp——它是最常用的 HTTP 客户端啊。integration-tests 那个模块我该什么时候看？

**大师**：先说阅读顺序：应用开发先读 **`langchain4j` 主模块 + 你选择的 provider**（比如 `langchain4j-open-ai`）；想深入理解接口设计再下钻 **`langchain4j-core`**；遇到网络问题再去翻 **`langchain4j-http-client-*`**。关于 core 不绑 OkHttp——这是一个刻意的 **最小传递依赖** 设计。如果 core 直接依赖了 OkHttp，那每个使用 core 的人（哪怕只是引用来定义接口）都会被迫拉进 OkHttp 的所有传递依赖。通过 SPI 机制把 HTTP 实现抽到 `langchain4j-http-client-*`，用户自己选择用 JDK 内置的、OkHttp 的还是 Apache 的——**不绑架用户的依赖栈**。`integration-tests` 是多模块组合回归测试，学兼容矩阵时翻阅，不必第一周啃。**技术映射**：**core = 纯契约层，零运行时依赖；http-client-* = 可替换的传输实现层——这种分离保证了「定义接口」的人不需要关心「谁在传输」**。

**小胖**：那实验模块——就是名字里带 `experimental` 或版本号还是 `0.x` 的那些——能直接加进我们支付服务的 pom.xml 吗？

**大师**：**绝对不要**。实验模块的 API 不受语义化版本保护，可能在 MINOR 甚至 PATCH 升级中做 breaking change。如果非要用——三个条件必须同时满足：① **锁死精确版本号**（不用版本范围，不用 SNAPSHOT）；② **有降级方案**（如果实验模块出了兼容问题，能秒级切回稳定版本）；③ **接受 API 变更**（上线前必须跑完整回归集）。多数情况下，建议把实验特性放在 **独立服务** 里灰度试点，而不是塞进核心交易服务。**技术映射**：**实验模块的隔离原则 = 独立进程 + 精确版本 + 熔断降级——别让它成为全公司隐形父 POM 里的那颗定时炸弹**。

**小白**：假设周五晚高峰 `NoSuchMethodError` 的堆栈落在了 `dev.langchain4j.model.openai` 包里，第一反应是不是该骂库有 bug？然后该怎么排查？

**大师**：不要一上来就怀疑库有 bug——90% 的情况是版本问题。正确的排查顺序是 **分层定位**：第一层查 **BOM 是否混版本**（`dependency:tree` 看 core 是不是出现了两个版本）；第二层查 **是否引入了 SNAPSHOT**；第三层查 **厂商的 REST API 响应格式是否变更**（模型升级可能导致 SDK 不兼容）；第四层查 **native 镜像是否缺了 SPI/反射配置**。把这几层画在纸上，通常 30 分钟内能收窄到一层。**技术映射**：**排障顺序 = BOM 版本 → 传输层 → provider 适配层 → 业务代码，从底层往上排查，而不是从堆栈第一行开始猜**。

## 3. 项目实战

### 环境准备

```bash
# 克隆仓库（如已克隆则跳过）
git clone https://github.com/langchain4j/langchain4j.git
cd langchain4j

# 查看聚合 pom 的模块列表
head -50 pom.xml
```

### 任务 1：标注模块地图

```bash
# 列出所有子模块
grep "<module>" pom.xml
```

输出示例：
```xml
<module>langchain4j-core</module>
<module>langchain4j</module>
<module>langchain4j-open-ai</module>
<module>langchain4j-ollama</module>
<module>langchain4j-pgvector</module>
<module>langchain4j-milvus</module>
<module>langchain4j-mcp</module>
<module>langchain4j-guardrails</module>
<!-- ... 等数十个 -->
```

用三种颜色标注：**蓝色**=模型提供商、**绿色**=向量存储、**红色**=横切（http/observation/guardrails）。在团队 Wiki 里贴一张你标注后的截图。

### 任务 2：建立个人 cheat sheet

从 README 中为以下每类各抄一个你关心的 `artifactId`：

| 类别 | 你选择的 artifactId | 用途 |
|------|-------------------|------|
| 模型提供商 | `langchain4j-open-ai` | 对接 OpenAI |
| 向量存储 | `langchain4j-pgvector` | PostgreSQL 向量检索 |
| 横切 | `langchain4j-http-client-jdk` | JDK HttpClient |

### 任务 3：画请求路径

在纸或白板上画箭头：
```
用户 HTTPS 请求
  → Controller（你的代码）
    → AiServices 或 直接 ChatModel（langchain4j）
      → ChatModel 接口（langchain4j-core）
        → OpenAiChatModel 实现（langchain4j-open-ai）
          → HTTP 客户端（langchain4j-http-client-jdk）
            → OpenAI API
      ← 返回 String 响应
  ← 返回给用户
```

**故意画错一箭**，让同事帮你纠正——第 4 章再回来修正。

### 可能遇到的坑

| 坑 | 表现 | 解法 |
|----|------|------|
| 把 aggregator 整体依赖进业务 | fat jar 冲突 | 只引需要的 leaf + BOM |
| 以为 core 包含 OpenAI 实现 | 编译或运行期缺类 | core 只有接口，实现需额外引 provider |
| 复制别人 pom 一大坨依赖 | 版本分叉 + NoSuchMethodError | 只引必要的 leaf 模块 |

### 延伸案例：NoSuchMethodError 排障

客服系统周五晚高峰抛出 `NoSuchMethodError`，堆栈落在 `dev.langchain4j.model.openai`。正确排查顺序：

1. 检查 BOM 是否混版本（`dependency:tree`）
2. 检查是否引了 SNAPSHOT
3. 检查厂商响应是否变更
4. 检查 native 镜像反射配置

### 测试验证

- 向同事口述：core / langchain4j 主模块 / provider 各解决什么问题
- 在纸上画部署拓扑：哪些 jar 同 JVM，哪些是 sidecar

## 4. 项目总结

### 优点与缺点

| 维度 | 多模块 + core 分离 | 单胖 jar 全家桶 | 自研胶水封装 |
|------|-------------------|----------------|-------------|
| 边界清晰度 | 高 | 低 | 视团队 |
| 新人导航成本 | 中（需地图） | 低 | 高 |
| 典型缺点 | 模块名需记忆 | 传递依赖难控 | 无社区对齐 |

### 适用 / 不适用场景

**适用**：架构评审、技术选型、平台组封装前的现状分析。

**不适用**：个人脚本级一次性调用且永不扩展、完全不想理解模块且拒绝 BOM。

### 常见踩坑

1. 在业务模块直接依赖 `integration-tests` → 测试代码进生产路径
2. 误以为 core 含 OpenAI 实现 → 编译或运行期缺类
3. 复制别人 pom 一大坨依赖 → 版本分叉

### 进阶思考题

1. 若 native-image 下 ServiceLoader 未加载某 HTTP 客户端实现，从哪一层开始加 reflect-config？
2. 护栏稳定、MCP 实验时，如何用发布列车解耦版本？

### 推广计划

| 角色 | 建议阅读顺序 | 协作要点 |
|------|-------------|----------|
| 开发 | 本章 → 第 4、12 章 | 新需求只引必要 leaf |
| 架构 | 本章 + BOM 策略 | 输出受支持组合表（JDK × Boot × BOM） |
| 运维 | 本章 + 第 9 章 | 向量库与模型 HTTP 分探针 |

### 检查清单

- **测试**：要求架构组输出受支持组合表（JDK × Spring Boot × LangChain4j BOM）
- **运维**：对向量库与模型 HTTP 端点分别建可用性探针

### 附录

推荐阅读：根 `pom.xml`、`langchain4j-core` 包根、`AiServices.java`（预习）。
