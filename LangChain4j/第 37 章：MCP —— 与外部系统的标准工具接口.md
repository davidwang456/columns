# 第 37 章：MCP —— 与外部系统的标准工具接口

## 1. 项目背景

**Model Context Protocol（MCP）** 用标准消息把 **工具、资源、提示** 暴露给宿主程序，使 AI 应用以 **插件化** 方式扩展能力而不必每个集成手写 HTTP。LangChain4j 通过 `langchain4j-mcp`、`langchain4j-mcp-docker` 等模块对接；示例仓库含 **`mcp-example`**、**`mcp-github-example`**。

对运维而言，MCP server **又是一个需要凭证、网络策略与进程隔离的服务**；对开发而言，它是 **比临时 shell 工具**更安全边界化的集成方式。

## 2. 项目设计：大师与小白的对话

**小白**：MCP 会取代内部 REST 吗？

**大师**：**不会取代**；它是 **AI 控制平面上** 的互操作层，底层仍常是 REST/GRPC。

**小白**：和 Java `@Tool` 什么关系？

**大师**：`@Tool` 在 **同一 JVM** 内；MCP 多在 **独立进程**；可并存：**宿主**通过 MCP client 调外部能力。

**小白**：安全模型？

**大师：** **最小权限** + **scoped token** + **审计**；**禁止**把 **生产 shell** 直接给模型。

**小白**：Docker 版做什么？

**大师**：**容器隔离**执行环境，限制 **文件系统/网络**。

**小白**：观测？

**大师**：对 **每个 MCP 调用**记录 **latency、status、参数摘要（脱敏）**。

## 3. 项目实战：主代码片段

> **场景入戏**：MCP 把工具封装成 **「带协议的 USB-C」**——好看、标准，但若你在协议背后仍挂载 **`rm -rf` 同义词」**，那就是 **给会议室投影仪接家用 220V 转接头**：**冒烟的不是协议，是权限模型**。

浏览 [`mcp-example`](../../langchain4j-examples/mcp-example)：

1. 目录结构与 **启动方式**（优先读 **`pom.xml`** 与 **`src/main/java`** 中带 `main` 的入口类）。  
2. 找到 **连接到 MCP server** 的配置点（properties / 代码常量）。  
3. **红队桌游**：列出 **愿意暴露 3 条** 与 **绝不开口 3 条**——对照延伸案例 **GHE 读 PR** 的 **allow/deny** 列表，看是否 **同级严格**。

#### 挖深

对比 [`mcp-github-example`](../../langchain4j-examples/mcp-github-example)：多一个 **官方场景**，多一份 **scope 审计**模板。

### 延伸案例（情景演练）：内网「Git 评审助手」的边界

**背景**：研发团队想让助手 **总结 PR 风险**，于是基于 **`mcp-github-example`** 思路接入 **GitHub Enterprise**。**允许列表**：`read PullRequest`、`read diff 前 500 行`、`list comments`。**拒绝列表**：`merge`、`delete branch`、`write webhook`、**任何 `admin:*` scope**。运维把 MCP Server 放在 **独立命名空间**，**NetworkPolicy** 只允许访问 **GHE 内网 VIP**，**Secret** 按 **90 天轮换**。

**插曲**：有一次 PR 摘要里 **意外出现了内部客户名**——源于是 **diff 本身含配置失误**。补救：**(1)** 出站 **PII 扫描**；**(2)** **生成前 redact** 已知客户字典；**(3)** PR **作者**在 UI 上 **二次确认**后才展示给非项目成员。该案例说明：**MCP 不是把 shell 换成 nicer 协议**，仍要 **数据分级 + 人在回路**。

## 4. 项目总结

### 优点

- **生态互通**、边界清晰。**缺点**：**心智与运维成本**。

### 适用场景

- 开发者工具、**内网系统插件化**。

### 注意事项

- **版本协商**与 **向后兼容**。  
- **secret 轮换**。

### 常见踩坑

1. **宽权限 token** 泄露在提示里。  
2. **无超时的长任务**堵塞 worker。

---

### 本期给测试 / 运维的检查清单

**测试**：**危险工具**调用必须 **拒绝**；**并发**压 MCP。  
**运维**：**NetworkPolicy** / **防火墙**；**容器资源限制**。

### 附录

模块：`langchain4j-mcp`、`langchain4j-mcp-docker`。示例：`mcp-example/`、`mcp-github-example/`。
