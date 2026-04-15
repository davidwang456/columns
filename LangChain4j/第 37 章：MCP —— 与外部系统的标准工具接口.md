# 第 37 章：MCP —— 与外部系统的标准工具接口

## 1. 项目背景

### 业务场景（拟真）

**MCP（Model Context Protocol）** 用标准消息暴露 **工具、资源、提示**，使 AI 应用 **插件化** 扩展而无需每个集成手写 HTTP。LangChain4j 提供 `langchain4j-mcp`、`langchain4j-mcp-docker` 等；示例 **`mcp-example`、`mcp-github-example`**。

### 痛点放大

MCP server **又一进程**：**凭证、网络策略、隔离**；开发侧 **比临时 shell** 更安全边界。**不替代 REST**——底层常仍是 REST/gRPC。

## 2. 项目设计：小胖、小白与大师的对话

**小胖**：MCP 像 USB-C 统一充电口？

**小白**：会 **取代内部 REST** 吗？和 **`@Tool`** 啥关系？

**大师**：**不取代**——是 **AI 控制平面互操作**；底层仍 REST/GRPC。**@Tool** 同 JVM；**MCP** 多 **独立进程**——可 **并存**。**技术映射**：**最小权限 + scoped token + 审计**。

**小胖**：Docker 版干啥？

**小白**：**观测** 记啥？

**大师**：**容器隔离** 文件系统/网络。**每 MCP 调用**：**latency、status、参数摘要（脱敏）**。**技术映射**：**禁止生产 shell 给模型**。

---

## 3. 项目实战

### 环境准备

- [`mcp-example`](../../langchain4j-examples/mcp-example)、[`mcp-github-example`](../../langchain4j-examples/mcp-github-example)。

### 分步任务

1. 目录结构与 **启动方式**（`pom.xml`、main）。  
2. **连接 MCP server** 的配置点。  
3. **红队**：**允许 3 条** vs **绝不开口 3 条**——对照 **GHE 读 PR** 的 allow/deny。

**延伸**：**GHE PR 摘要**——allow：`read PR`、`diff 前 500 行`；deny：`merge`、`admin`。**NetworkPolicy** 仅 **GHE VIP**；**出站 PII 扫描**。

### 测试验证

- **危险工具** 必须拒绝；**并发** 压 MCP。

### 完整代码清单

模块：`langchain4j-mcp`、`langchain4j-mcp-docker`；示例目录见上。

---

## 4. 项目总结

### 优点与缺点（与同类做法对比）

| 维度 | MCP | 手写集成 | 同 JVM @Tool |
|------|-----|----------|--------------|
| 边界 | 清晰 | 散 | 紧 |
| 运维 | 多进程 | 中 | 低 |
| 典型缺点 | 心智成本 | 重复代码 | 难隔离 |

### 适用场景

- 开发者工具、**内网插件化**。

### 不适用场景

- **无进程隔离能力** 的团队——先补平台。

### 注意事项

- **版本协商**；**secret 轮换**。

### 常见踩坑经验（生产向根因）

1. **宽权限 token** 泄露进提示。  
2. **无超时** 长任务堵 worker。

### 进阶思考题

1. **MCP + 第 15 章动态工具** 的组合边界？  
2. **diff 含客户名** 的 **出站 redact** 策略？

### 推广计划提示（多部门）

| 角色 | 建议阅读顺序 | 协作要点 |
|------|----------------|----------|
| **开发** | 第 14 章 → 本章 | **allow/deny 列表** |
| **运维** | NetworkPolicy | **容器资源限制** |
| **安全** | 审计 scope | **90 天轮换** |

---

### 本期给测试 / 运维的检查清单

**测试**：**危险工具**调用必须 **拒绝**；**并发**压 MCP。  
**运维**：**NetworkPolicy** / **防火墙**；**容器资源限制**。

### 附录

模块：`langchain4j-mcp`、`langchain4j-mcp-docker`。示例：`mcp-example/`、`mcp-github-example/`。
