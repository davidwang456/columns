# 第 2 章：环境、依赖与 BOM —— 从 Maven 到第一个响应

## 1. 项目背景

### 业务场景（拟真）

某跨境电商的 **订单与客服后端**决定统一引入 LangChain4j：多个事业部各自在分支里加了 `langchain4j-open-ai`、`langchain4j-core` 等坐标，**有的手写版本号，有的继承公司 Parent 时被覆盖**。预发环境「**本地可运行、CI 偶发失败**」，直到一次 **`NoSuchMethodError: EmbeddingModel.embed`** 在周四晚构建暴露——复盘发现 **两个子模块解析到的 `langchain4j-core` patch 不一致**。

LangChain4j 采用 **多模块 Maven 工程**：核心接口在 `langchain4j-core`，OpenAI 实现在 `langchain4j-open-ai`，向量库各有独立 artifact。若手动指定版本，极易出现 **`ClassNotFoundException` / `NoSuchMethodError`**——典型于某一依赖仍停在旧 patch，或 **传递依赖**把另一路径升到快照。

### 痛点放大

没有 **统一的 BOM（Bill of Materials）** 与 **依赖治理流程** 时，问题不在「会不会写 `pom.xml`」，而在 **可重复构建与可审计**：同一套业务代码在不同机器上链接到 **不同二进制**，排障会变成 **猜谜**；**性能**上重复加载不同版本的类会放大 Metaspace 与类加载器问题；**可维护性**上平台组无法回答「我们线上到底跑的是哪一版 LangChain4j」。官方通过 **`langchain4j-bom`** 建议「**import BOM，业务模块不写版本号**」。

除依赖外，**运行环境**包含：JDK 版本、HTTP 代理、公司 MITM 证书、出口防火墙，以及 **API Key / 底座 URL** 的配置方式。教程项目用 `ApiKeys.java` 集中放常量，**仅方便入门，不可复制到生产**。

```text
多模块应用                无 BOM 时                    有 BOM 时
─────────                ────────                    ────────
业务A ──► langchain4j-core 0.35.0                  全部坐标
业务B ──► langchain4j-core 0.35.1  ──► 运行时爆炸   由 BOM 锁版本
```

## 2. 项目设计：小胖、小白与大师的对话

**小胖**：BOM 不就是「套餐」吗？我点外卖从来不看供应商编号，能送就行——为啥写代码要锁这么死？

**小白**：BOM 和 **parent POM** 到底差在哪？我们公司有统一 Parent，能不能 **少 import 一个 BOM**？

**大师**：**Parent** 可带插件、属性、甚至子模块结构；**BOM** 专注 **dependencyManagement**，常被 **非子模块** 用 `import` 吃进来。Parent 管「**怎么构建**」，BOM 管「**同一顿饭里各盘菜的版本一起变**」。**技术映射**：**BOM ≈ 仅版本对齐的 dependencyManagement 清单**。

**小白**：Gradle 项目怎么对齐？为什么 CI 里构建失败、本地却能跑？

**大师**：Gradle 7+ 用 **platform** 或 **enforcedPlatform** 引入 BOM。CI 与本地不一致常见因：**SNAPSHOT 解析策略**、**私库镜像**、**JDK 不一致**、或 **缓存了旧构件**。建议 **`.mvn/wrapper` 与 JDK 版本**在流水线里写死。**技术映射**：**可重复构建 = 锁 JDK + 锁 BOM + 锁仓库解析策略**。

**小胖**：密钥放环境变量还是 K8s Secret？我本地 `.env` 行不行？

**小白**：补充：**平台 Parent 强行覆盖 LangChain4j 版本**时，谁说了算？会不会把 **补丁修复**挡在外面？

**大师**：生产密钥走 **秘密管理服务**；本地可用 `.env`（勿入库）或 IDE Run Configuration。版本上应 **单一职责**：由平台组统一管理 **BOM 版本**，业务线不擅自 bump 子坐标；升级走 **变更单 + 依赖树 diff**。**技术映射**：**密钥分层 + BOM 所有者 = 安全与供应链**。

**小胖**：懂了，就像总店定价——我分店不能偷偷改价签。**那我只引 `langchain4j-open-ai` 不引 `langchain4j` 行不行？**

**小白**：这种「半引用」在运行时会不会 **缺类**？怎么在 CI 里拦住？

**大师**：**编译通过、运行失败**的典型就是只引了实现、漏了 core 或版本分叉。CI 里加 **`dependency:tree` / `dependency:list`** 门禁，对比 **允许的坐标集合**。**技术映射**：**依赖完整性 = 图，不是单坐标**。

---

## 3. 项目实战

### 环境准备

- **JDK**：与项目 `pom.xml` / CI 声明一致（建议 17+）。
- **构建**：Maven（可用 wrapper），可访问 Maven Central 或公司镜像。
- **仓库**：已克隆 `langchain4j-examples`，重点关注 `tutorials` 模块。

### 分步实现

#### 步骤 1：在 `pom.xml` 中定位 BOM

**目标**：肉眼确认 **`langchain4j-bom` 被 `import` 进 `dependencyManagement`**。

打开 [`langchain4j-examples/tutorials/pom.xml`](../../langchain4j-examples/tutorials/pom.xml)，搜索 `langchain4j-bom` 或 `dependencyManagement`，标出 **import BOM** 的位置。

**可能遇到的坑**：把 BOM 写进 `dependencies` 而非 `dependencyManagement` 的 `import` scope——**解法**：对照官方示例 [`tutorials/pom.xml`](../../langchain4j-examples/tutorials/pom.xml)。

#### 步骤 2：抄下「能跑」与「能打包」两条命令

**目标**：留存 **classpath 与 fat-jar** 相关命令，便于与运维对齐。

阅读 [`tutorials/README.md`](../../langchain4j-examples/tutorials/README.md)，记下 **fat-jar** 的 `mvn -Pcomplete package` 与 `java -cp ... _00_HelloWorld "..."` **两条命令**（按你本地 README 实际为准）。

**运行结果（文字描述）**：打包成功应产出可执行 jar 或明确 classpath；运行应打印模型回复（需有效 Key 与网络）。

#### 步骤 3：依赖树自检

**目标**：发现 **同一 artifact 多版本**。

在 `tutorials` 模块目录执行：

```text
mvn -q dependency:tree -Dincludes=dev.langchain4j
```

**运行结果（文字描述）**：输出中 **`langchain4j-core` 应只出现同一版本**；若出现多次不同版本，标记为 **风险**。

**深度彩蛋**：**出现多次且版本不同**往往意味着 **灾难的开始**——需回到 BOM 与传递依赖排除策略。

#### 步骤 4：替换密钥的思维练习（勿提交真 Key）

**目标**：建立 **生产密钥** 习惯。

在 `ApiKeys.java` 旁写三行伪代码：**`System.getenv("OPENAI_API_KEY")`** 读取方式，**不**把真 Key 写入仓库。

### 延伸案例（情景演练）

某团队在单体分支里手工为 `langchain4j-core` 写了版本号，又为 `langchain4j-open-ai` 写了另一组 patch。预发 **偶发 `NoSuchMethodError`**，只在 **周四晚构建**出现。复盘：**CI 缓存了旧二方包**，子模块被本地 `install` 成 **不一致** 版本。团队改用 **`import` BOM** 后，流水线增加 **`mvn -q dependency:list` 差异门禁**，任一子坐标脱离 BOM 即失败。

**Gradle** 子项目曾遗漏 `enforcedPlatform`，传递依赖把 `langchain4j-core` 升到 **快照**——症状是 **SPI 加载顺序**在不同机器不同。修复后，平台组发布 **《允许的 BOM 区间》**：小版本自动化 PR，大版本需 **架构评审 + 兼容性矩阵**。

### 测试验证

- 执行 **步骤 3** 的 `dependency:tree`，保留一段 **无版本冲突** 的输出截图或文字记录。
- （可选）在 CI 模板中增加 **依赖列表 diff** 或 **OWASP Dependency-Check** 对 `langchain4j-*` 的非阻塞告警。

### 完整代码清单

- [`langchain4j-examples/tutorials/pom.xml`](../../langchain4j-examples/tutorials/pom.xml)  
- [`ApiKeys.java`](../../langchain4j-examples/tutorials/src/main/java/ApiKeys.java)（示例用，生产勿照搬）

---

## 4. 项目总结

### 优点与缺点（与同类做法对比）

| 维度 | import `langchain4j-bom` | 手写各模块版本号 | 仅公司 Parent 管版本 |
|------|--------------------------|------------------|----------------------|
| 版本一致性 | 高 | 低（易漂移） | 中（取决于谁维护） |
| 升级成本 | 一次 bump BOM | 多坐标逐一改 | 看平台流程 |
| 透明度 | `dependency:tree` 易审计 | 难 | 中 |
| 典型缺点 | 需理解 `import` scope | 易 `NoSuchMethodError` | Parent 与 BOM 职责易混 |

### 适用场景

- 将把 LangChain4j 引入 **多模块企业应用** 的团队。
- 需要 **统一升级窗口**（例如每季度安全更新）的组织。
- **Gradle / Maven 混用** 的公司，需要 **同一 BOM 语言**。

### 不适用场景

- **单模块玩具项目**且永远只有一个依赖——可简化，但仍建议早养成 BOM 习惯。
- **完全不可上网、且无法搭私库** 的环境——需先解决 **构件来源**，再谈 BOM。

### 注意事项

- **依赖树检查**：`mvn dependency:tree` 定期跑，查重复与冲突。
- **镜像仓库**：内网 Artifactory 需代理 Maven Central。
- **JDK 大版本升级**时同步跑集成测试。

### 常见踩坑经验（生产向根因）

1. **只引 `langchain4j-open-ai` 忘引 `langchain4j`（或反之）** → 编译与运行时类路径不一致。  
2. **多 BOM 叠加** 产生隐性覆盖 → **import 顺序**与 **平台规范**必须文档化。  
3. **把教程 `ApiKeys` 原样复制** → 密钥泄露事件。

### 进阶思考题

1. **若安全部门要求「禁止 SNAPSHOT 上生产」**，你如何在 **Gradle `enforcedPlatform`** 与 **Maven CI** 中同时强制？（答案思路：仓库策略 + 构建失败规则。）  
2. **两个业务线分别 import 了不同小版本的 BOM**，合并到单体后如何 **无停机** 收敛？（提示：依赖树 diff + 兼容矩阵；与第 3 章模块边界相关。）

### 推广计划提示（多部门）

| 角色 | 建议阅读顺序 | 协作要点 |
|------|----------------|----------|
| **开发** | 本章 → 第 4 章 Hello World | 新需求 **不手写** LangChain4j 版本号 |
| **运维** | 本章 + 制品库策略 | **允许的 BOM 区间**、升级变更单 |
| **测试** | 本章 + CI 日志 | 构建失败时收集 **`dependency:tree`** 作为附件 |

---

### 本期给测试 / 运维的检查清单

**测试**：在 CI 增加 **依赖漏洞扫描**（OWASP Dependency-Check 等），对 `langchain4j-*` 坐标设置 **非阻塞告警 + 人审**。  
**运维**：建立 **「允许的 BOM 版本区间」** 与升级变更单模板，避免开发私自修改版本号绕过评审。

### 附录：相关 Maven 模块与类

| 模块 | 说明 |
|------|------|
| `langchain4j-bom` | 统一托管版本 |
| `langchain4j-parent` | 插件 / 属性父 POM |

推荐阅读：`tutorials/pom.xml`、`ApiKeys.java`。
