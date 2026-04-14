# 第 2 章：环境、依赖与 BOM —— 从 Maven 到第一个响应

## 1. 项目背景

LangChain4j 采用多模块 Maven 工程：核心接口在 `langchain4j-core`，具体 OpenAI 实现在 `langchain4j-open-ai`，向量库各有独立 artifact。若手动指定版本，极易出现 **`ClassNotFoundException` / `NoSuchMethodError`**—— 典型于某一依赖仍停在旧 patch。官方通过 **`langchain4j-bom`**（Bill of Materials）建议「**import BOM，不写版本号**」。

除依赖外，**运行环境**包含：JDK 版本、HTTP 代理、公司 MITM 证书、出口防火墙、以及 **API Key/底座 URL** 的配置方式。教程项目用 `ApiKeys.java` 集中放常量，**仅方便入门，不可复制到生产**。

## 2. 项目设计：大师与小白的对话

**小白**：BOM 和 parent POM 区别？

**大师**：**Parent** 可带插件与属性；**BOM** 专注 **dependencyManagement**，常被非子模块项目 `import` 进来。

**小白**：Gradle 怎么用 BOM？

**大师**：Gradle 7+ 可用 **platform** 或 **enforcedPlatform** 引入 BOM，效果类似。

**小白**：为什么 CI 里构建失败本地可运行？

**大师**：常见因 **SNAPSHOT 解析策略**、**私库镜像**、或 **JDK 不一致**；建议 CI 与本地 `.mvn/wrapper` 对齐。

**小白**：密钥放环境变量还是 K8s Secret？

**大师**：生产用 **秘密管理服务**；本地开发可用 `.env`（勿入库）或 IDE Run Configuration。

**小白**：能否用公司统一 Parent 覆盖 LangChain4j 版本？

**大师**：可以，但要 **单一职责**：由平台组统一管理 BOM 版本，业务线不擅自 bump 子模块。

## 3. 项目实战：主代码片段

> **场景入戏**：BOM 像餐厅的 **固定套餐价签**——业务模块点菜时说「来一份 LangChain4j」，**不标单价**也由总店锁价；你若在子模块又手写版本号，就像 **偷偷改价签**，结账（运行时）才炸。

#### 动手路线（照表勾选）

1. 打开 [`langchain4j-examples/tutorials/pom.xml`](../../langchain4j-examples/tutorials/pom.xml)，**搜索** `langchain4j-bom` 或 `dependencyManagement`，用笔（或 IDE 书签）标出 **import BOM** 的位置。  
2. 读 [`tutorials/README.md`](../../langchain4j-examples/tutorials/README.md)，在笔记本抄下 **fat-jar** 的 [`mvn -Pcomplete package`](../../langchain4j-examples/tutorials/README.md) 与 `java -cp ... _00_HelloWorld "..."` **两行命令**——未来和运维扯皮 classpath 时你会感谢自己。  
3. 在 `ApiKeys.java` 旁贴便签：**替换为** `System.getenv("OPENAI_API_KEY")` 的**三行伪代码**（勿提交真 key）。

**深度彩蛋**：终端执行一次（在 `tutorials` 模块目录）：

```text
mvn -q dependency:tree -Dincludes=dev.langchain4j
```

把输出里 **`langchain4j-core` 版本出现次数**数清楚——**出现多次**往往意味着 **灾难的开始**。

### 延伸案例（情景演练）：从「能跑」到「能发版」

某跨境电商团队在单体分支里手工为 `langchain4j-core` 写了版本号，又为 `langchain4j-open-ai` 写了另一组 patch。预发环境偶发 **`NoSuchMethodError: EmbeddingModel.embed`**，只在 **周四晚构建**出现。排障复盘发现：CI 缓存了 **`langchain4j` 主 artifact** 的旧二方包，而子模块已被同事在本地 `install` 成 **不一致** 版本。团队改用 **`import` BOM** 后，业务模块的 `dependencyManagement` 只保留一条 `langchain4j-bom`；流水线增加 **`mvn -q dependency:list -DincludeArtifactIds=langchain4j` 的差异门禁**，任一子坐标脱离 BOM 即失败。

第二个插曲：**Gradle 子项目**遗漏 `enforcedPlatform`，导致传递依赖把 `langchain4j-core` 升到 **快照**，线上却钉死 **发布版**——症状是 **`ServiceHelper` SPI 加载顺序**在不同机器不同。修复后，平台组发布 **《允许的 BOM 区间》**：小版本由自动化 PR 提议，大版本需 **架构评审 + 兼容性矩阵跑通**。你可以把本案例改写成你们公司模板里的 **两段 RCA（根因分析）**，并附上 **`mvn dependency:tree -Dverbose`** 的对比截图作为培训材料。

## 4. 项目总结

### 优点

- **BOM** 降低组合爆炸带来的二进制不兼容。  
- 示例工程已演示 **Runnable 的 main** 与依赖打包路径。

### 缺点

- 初次接触 Maven BOM 的开发者需 **5～10 分钟** 建立心智模型。  
- **SNAPSHOT** 适合贡献者，不适合业务上线制品。

### 适用场景

- 任何将把 LangChain4j 引入 **多模块企业应用** 的团队。  
- 需要 **统一升级窗口**（例如每季度一次安全更新）的组织。

### 注意事项

- **依赖树检查**：`mvn dependency:tree` 定期跑一次，查重复与冲突。  
- **镜像仓库**：内网 Artifactory 需代理 Maven Central。  
- **JDK 预告**：升级 JDK 大版本时同步跑集成测试。

### 常见踩坑

1. **只引 `langchain4j-open-ai` 忘引 `langchain4j`**（或反之）导致编译通过运行失败。  
2. **多 BOM 叠加** 产生隐性覆盖，需明确 import 顺序。  
3. **把教程 `ApiKeys` 原样复制** 导致密钥泄露事件。

---

### 本期给测试 / 运维的检查清单

**测试**：在 CI 增加 **依赖漏洞扫描**（OWASP Dependency-Check 等），对 `langchain4j-*` 坐标设置 **非阻塞告警 + 人审**。  
**运维**：建立 **「允许的 BOM 版本区间」** 与升级变更单模板，避免开发私自修改版本号绕过评审。

### 附录：相关 Maven 模块与类

| 模块 | 说明 |
|------|------|
| `langchain4j-bom` | 统一托管版本 |
| `langchain4j-parent` | 插件/属性父 POM |

推荐阅读：`tutorials/pom.xml`、`ApiKeys.java`。
