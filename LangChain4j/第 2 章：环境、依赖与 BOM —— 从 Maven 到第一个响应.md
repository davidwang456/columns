# 第 2 章：环境、依赖与 BOM —— 从 Maven 到第一个响应

## 1. 项目背景

### 业务场景（拟真）

某跨境电商公司有 5 个后端事业部，分别维护订单、客服、商品、支付、物流系统。2024 年初平台组决定统一引入 LangChain4j，各团队在分支里各自加了 `langchain4j-open-ai`、`langchain4j-core` 等坐标——**有的手写版本号写死了 `0.33.0`，有的没写版本号靠传递依赖继承，有的被公司统一的 parent POM 覆盖成了别的版本**。

预发环境出现了一个诡异的现象：**订单服务本地能运行，但 CI 里偶发失败，而且只在周四晚上的构建出现**。凌晨 2 点，值班的运维被告警吵醒——**`NoSuchMethodError: EmbeddingModel.embed`** 在生产环境爆发，订单查询接口全部 500。复盘时发现：订单子模块依赖的 `langchain4j-core` 解析到了 `0.35.0`，但客服子模块的传递依赖把 `langchain4j-core` 升到了 `0.35.1-SNAPSHOT`——两个类加载器各加载了一版，运行时调用了一个在新版已被删除的方法。

### 痛点放大

LangChain4j 采用 **多模块 Maven 工程**：核心接口在 `langchain4j-core`，OpenAI 实现在 `langchain4j-open-ai`，Ollama 实现在 `langchain4j-ollama`，向量库各有独立 artifact。如果每个团队手动指定版本号，极易出现以下场景：

- **ClassNotFoundException**：业务代码只引了 `langchain4j-open-ai` 但忘了引 `langchain4j` 或 `langchain4j-core`，编译能过（因为 IDE 缓存了类），运行时报错。
- **NoSuchMethodError**：不同模块解析到的 core 版本 patch 号不同，A 模块用新版调新方法，B 模块用旧版没有这个方法。
- **传递依赖被 SNAPSHOT 覆盖**：某个依赖引入了 SNAPSHOT 版的 core，覆盖了 BOM 锁定的 release 版本。

官方通过 **`langchain4j-bom`** 提供了一个标准做法：**import BOM 进 dependencyManagement，业务模块中所有 langchain4j 坐标不写版本号**。这样所有模块——不管是订单还是客服——都用同一份版本清单。

```text
多模块应用                无 BOM 时                    有 BOM 时
─────────                ────────                    ────────
订单服务 → core 0.35.0                           全部坐标
客服服务 → core 0.35.1  → 运行时 NoSuchMethodError 由 BOM 锁版本
```

除依赖外，运行环境还涉及 **JDK 版本、HTTP 代理、公司 MITM 证书、出口防火墙、API Key/底座 URL 的配置方式**。教程项目里用 `ApiKeys.java` 集中放常量——这只方便入门，**绝对不能复制到生产**。

## 2. 项目设计：小胖、小白与大师的对话

**小胖**：BOM 不就是「套餐」吗？我点外卖从来不看供应商编号，能送就行——为啥写代码要锁这么死？今天用 `0.35.0`，明天用 `0.35.1`，不都是 core 吗？

**小白**：BOM 和 parent POM 到底差在哪？我们公司发布平台已经有统一的 Parent POM 了，能不能不额外 import 一个 BOM？两个一起用会不会打架？

**大师**：**Parent POM 和 BOM 是两件不同的事**。Parent 管的是 **怎么构建**——插件版本、属性配置、子模块结构。BOM 管的是 **版本对齐**——同一顿饭里各盘菜的版本一起变。Parent 是「这间屋子怎么装修」，BOM 是「屋子里所有调料的出厂日期一致」。举例：如果公司 Parent 里锁了 `junit.version` 为 5.10，但你团队想用 5.11——你在子 POM 里单独指定就能覆盖。BOM 同理：Parent 可能锁了某个版本，但你 import 的 BOM 可以覆盖它。两条建议：**Parent 不锁第三方坐标版本**（只锁插件），依赖版本统一由 BOM 管理。**技术映射**：**BOM = 仅做版本对齐的 dependencyManagement 清单，与 Parent POM 是正交的——Parent 管构建，BOM 管版本**。

**小白**：那如果我们是 Gradle 项目呢？还有——为什么 CI 里构建失败，本地却能跑？这个问题已经排查过三次了。

**大师**：Gradle 项目用 **`platform`** 或 **`enforcedPlatform`** 来引入 Maven BOM——这是 Gradle 7+ 的标准做法。关于 CI 和本地不一致的问题：最典型的根因有四个——**SNAPSHOT 解析策略不同**（本地可能缓存了旧 SNAPSHOT）、**私库镜像不同**（CI 用公司的 Artifactory，本地直接连 Maven Central）、**JDK 版本不一致**（本地 JDK 17、CI JDK 21 导致行为差异）、**缓存了旧构件**（本地 `~/.m2/repository` 里有旧包，CI 是干净的）。建议把 `.mvn/wrapper` 里锁定的 Maven 版本和流水线里的 JDK 版本 **写死**，不要在配置文件里用「latest」。**技术映射**：**可重复构建 = 锁 JDK 大版本 + 锁 BOM 版本 + 锁仓库解析策略（禁止 SNAPSHOT）+ 锁 Maven wrapper 版本——五个锁少一个都可能出幺蛾子**。

**小胖**：那我省事点，`pom.xml` 里只引 `langchain4j-open-ai` 一个坐标行不行？反正 OpenAI 类都在那包里了。

**小白**：这种「半引用」在运行时会不会缺类？编译能过吗？怎么在 CI 里自动拦住？

**大师**：**编译通过、运行失败**——这就是最典型的 `NoClassDefFoundError` 场景。因为你只引了 `langchain4j-open-ai`，这个包确实包含 `OpenAiChatModel` 类，编译期 IDEA 能找到这个类所以不报错。但运行时 `OpenAiChatModel` 在构造时调用了 `langchain4j-core` 里的 `ChatModel` 接口——core 不在 classpath 上，于是 JVM 报 `NoClassDefFoundError`。解法：CI 里加一条 **`dependency:tree` 门禁**，用脚本对比 `允许的 langchain4j 坐标集合`——如果一个依赖都不缺但又出现了版本分叉，`dependency:tree -Dincludes=dev.langchain4j` 可以让你一眼看到两个不同版本的 core 共存。**技术映射**：**依赖完整性是一个传递闭包图问题，不是单坐标问题——你引的每个包都有自己的传递依赖，这些依赖凑在一起才是真正的 classpath**。

## 3. 项目实战

### 环境准备

```bash
# 确认 JDK 版本
java -version
# 预期输出：openjdk version "17" 或更高

# 进入 tutorials 模块目录
cd langchain4j-examples/tutorials
```

### 步骤 1：在 pom.xml 中定位 BOM

```bash
# 查看 pom.xml 中的 dependencyManagement 部分
grep -A 5 "dependencyManagement" pom.xml
# 或直接搜索 langchain4j-bom
grep "langchain4j-bom" pom.xml
```

预期输出应显示：
```xml
<dependency>
    <groupId>dev.langchain4j</groupId>
    <artifactId>langchain4j-bom</artifactId>
    <version>${langchain4j.version}</version>
    <type>pom</type>
    <scope>import</scope>
</dependency>
```

**注意**：`<scope>import</scope>` 必须放在 `<dependencyManagement>` 里，不能直接放在 `<dependencies>` 中。把 BOM 写进 `<dependencies>` 会导致版本号不生效。

### 步骤 2：记录能跑和能打包的命令

```bash
# 查看 README 中的打包命令
cat README.md | grep -A 2 "package"
```

记录以下两条命令（以你本地 README 为准）：
```bash
# 打 fat-jar
mvn -Pcomplete package

# 运行
java -cp target/tutorials-xxx-jar-with-dependencies.jar _00_HelloWorld "你的消息"
```

### 步骤 3：依赖树自检

```bash
# 在 tutorials 目录执行
mvn -q dependency:tree -Dincludes=dev.langchain4j
```

**预期输出示例**：
```
dev.langchain4j:langchain4j-core:jar:0.35.0:compile
dev.langchain4j:langchain4j-open-ai:jar:0.35.0:compile
dev.langchain4j:langchain4j:jar:0.35.0:compile
```

`langchain4j-core` 应只出现一个版本。如果出现多个不同版本，标记为风险，说明有依赖冲突：

```
dev.langchain4j:langchain4j-core:jar:0.35.0:compile
dev.langchain4j:langchain4j-core:jar:0.34.0:compile  ← 冲突！
```

**排查方法**：用 `mvn dependency:tree -Dincludes=dev.langchain4j -Dverbose` 查看传递依赖路径。

### 步骤 4：密钥的安全读取（伪造代码，不要真写 Key）

在 `ApiKeys.java` 旁写三行伪代码：

```java
// 不要这样写：
// String key = "sk-xxx";  // ❌ 密钥永不进仓库

// 应该这样读：
String apiKey = System.getenv("OPENAI_API_KEY");   // 环境变量
// 或：String apiKey = vaultService.getSecret("llm/openai-key");  // 密钥管理服务
// 或：String apiKey = decrypt(config.get("cipher.apiKey"));       // 解密配置
```

### 步骤 5：模拟版本冲突（破坏实验）

```bash
# 在 pom.xml 中故意写死一个旧版本的 core
# 然后运行依赖树检查
mvn -q dependency:tree -Dincludes=dev.langchain4j
```

观察是否出现**两个版本**的 langchain4j-core。生产排障时，这个命令是第一排查步骤。

### 可能遇到的坑

| 坑 | 表现 | 解法 |
|----|------|------|
| BOM 写在 `dependencies` 而不是 `dependencyManagement` | 版本号不生效 | 对照 `tutorials/pom.xml` 修正 |
| 只引 provider 忘了引 core | 编译通过、运行期 NoClassDefFoundError | 加 `dependency:tree` 门禁 |
| Gradle 没用 enforcedPlatform | 传递依赖把版本升到 SNAPSHOT | 用 `enforcedPlatform("dev.langchain4j:langchain4j-bom:0.35.0")` |
| 多个 BOM 叠加 | 版本被非预期的 BOM 覆盖 | 文档化 import 顺序 |

### 测试验证

```bash
# 验证方案：运行 dependency:tree，确认无版本冲突
mvn -q dependency:tree -Dincludes=dev.langchain4j | grep -c "langchain4j-core"
# 应输出 1，代表 core 只出现一次
```

### 完整代码清单

- [`tutorials/pom.xml`](../../langchain4j-examples/tutorials/pom.xml)
- [`ApiKeys.java`](../../langchain4j-examples/tutorials/src/main/java/ApiKeys.java)（示例，生产勿照搬）

## 4. 项目总结

### 优点与缺点

| 维度 | import BOM | 手写版本号 | 仅公司 Parent |
|------|-----------|-----------|--------------|
| 版本一致性 | 高 | 低 | 中 |
| 升级成本 | 一次 bump | 多坐标逐一改 | 看平台流程 |
| 典型缺点 | 需理解 import scope | 易 NoSuchMethodError | Parent 与 BOM 职责易混 |

### 适用 / 不适用场景

**适用**：多模块企业应用、需要统一升级窗口、Gradle/Maven 混用。

**不适用**：单模块玩具项目（但仍建议养成 BOM 习惯）、完全不可上网且无法搭私库的环境。

### 常见踩坑

1. 只引 `langchain4j-open-ai` 忘引 `langchain4j` → 运行时缺类
2. 多 BOM 叠加产生隐性覆盖 → import 顺序必须文档化
3. 把教程 `ApiKeys.java` 原样复制到生产 → 密钥泄露

### 进阶思考题

1. 若安全部门要求禁止 SNAPSHOT 上生产，如何在 Gradle enforcedPlatform 与 Maven CI 中同时强制？
2. 两个业务线分别 import 了不同小版本的 BOM，合并到单体后如何无停机收敛？

### 推广计划

| 角色 | 建议阅读顺序 | 协作要点 |
|------|-------------|----------|
| 开发 | 本章 → 第 4 章 | 新需求不手写 LangChain4j 版本号 |
| 运维 | 本章 + 制品库策略 | 允许的 BOM 区间、升级变更单 |
| 测试 | 本章 + CI 日志 | 构建失败时收集 `dependency:tree` 作为附件 |

### 检查清单

- **测试**：在 CI 增加依赖漏洞扫描，对 `langchain4j-*` 设置非阻塞告警 + 人审
- **运维**：建立允许的 BOM 版本区间与升级变更单模板

### 附录

| 模块 | 说明 |
|------|------|
| `langchain4j-bom` | 统一托管版本 |
| `langchain4j-parent` | 插件/属性父 POM |

推荐阅读：`tutorials/pom.xml`、`ApiKeys.java`。
