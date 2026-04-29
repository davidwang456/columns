# 第 37 章：MCP —— 与外部系统的标准工具接口

## 1. 项目背景

### 业务场景（拟真）

前面第 14 章的 `@Tool` 要求工具代码和调用代码运行在 **同一个 JVM** 里。但现实中，很多工具不在你的 JVM 里——它们可能是另一个团队维护的 Python 微服务、一个第三方的 REST API、或者一个需要隔离执行环境的 shell 脚本。**MCP（Model Context Protocol）** 定义了一套标准协议，让 AI 应用以插件化的方式跨进程调用外部能力。

### 痛点放大

没有 MCP 时，每个外部工具的集成都要手写 HTTP 客户端 + 认证 + 重试 + 错误处理——跟第 1 章讲的「直连每家不同的 SDK」一样的问题。MCP 的答案是提供一个 USB-C 式的统一接口：不管背后是 GitHub API、本地文件系统、还是 Docker 容器，接入方都走同一套协议。

## 2. 项目设计：小胖、小白与大师的对话

**小胖**：MCP 是不是就像 USB-C 接口——手机、硬盘、显示器、耳机都插同一个口，但背后干什么各不相同？

**大师**：这个比喻非常精确。USB-C 标准定义了物理接口的形态和协议——无论你插的是显示器还是硬盘，系统都通过同一套协议跟它通信。MCP 做的是同一件事：它定义了 AI 应用与外部工具之间的标准通信协议。不管背后的工具是读 GitHub PR、查数据库还是执行 shell 脚本——接入方都通过 MCP 的 `ToolProvider` 接口暴露、调用方通过标准的 `CallTool` 请求调用。

**小白**：MCP 和我们之前学的 `@Tool` 注解是什么关系？会取代它吗？

**大师**：两者是 **互补关系，不是替代关系**。`@Tool` 是 **同 JVM 内的方法调用**——代码在同一个进程里，调用就是普通的 Java 方法调用，延迟最低（微秒级）。MCP 是 **跨进程的标准化协议**——工具可能在另一个容器甚至另一台机器上，走网络调用（毫秒级）。给你的架构选择是：和主服务关系紧密、需要低延迟的工具，用 `@Tool`；需要隔离、跨团队维护、或者由非 Java 编写的外部工具，用 MCP。**技术映射**：**MCP 的安全原则和 @Tool 一样——最小权限 + scoped token + 每次调用审计日志；只是隔离性要求更高，因为工具在进程之外，你失去了 JVM 安全沙箱的保护**。

**小白**：那个 Docker 版的 MCP 是干什么用的？它和普通 MCP 的区别是什么？

**大师**：Docker 版 MCP 是把工具的执行环境封装在 **独立的 Docker 容器** 中。好处是：① **文件系统隔离**——工具能够访问的文件范围被容器限制；② **网络隔离**——工具只能访问容器网络允许的地址；③ **资源限制**——可以对容器做 CPU/内存限制，防止工具意外消耗过多资源。举例：如果你想暴露一个「执行 shell 脚本」的工具，你应该用 Docker 版 MCP，在容器内执行，而不是直接在宿主机的 JVM 里执行——这样即使脚本有问题，也不会影响主服务。**技术映射**：**无论用哪种方式暴露工具，禁止生产环境给模型暴露原始 shell 或任意文件写权限——所有外部工具调用必须经过权限校验和审计日志**。

---

## 3. 项目实战

### 环境准备

```bash
# MCP 示例仓库
cd langchain4j-examples/mcp-example
```

### 分步实现

#### 步骤 1：MCP 客户端接入

```java
McpToolProvider mcpProvider = McpToolProvider.builder()
        .serverUrl("http://localhost:8081/mcp")
        .toolBlacklist(List.of("dangerous_tool"))  // 黑名单
        .timeout(Duration.ofSeconds(30))
        .build();

// MCP 工具与本地 @Tool 共存
Assistant assistant = AiServices.builder(Assistant.class)
        .chatModel(model)
        .tools(mcpProvider, new LocalTools())
        .build();
```

#### 步骤 2：Allow/Deny 清单

```java
// 安全策略
public class McpSecurityPolicy {
    
    private static final Set<String> ALLOW = Set.of(
        "read_pr", "list_files", "search_code");
    
    private static final Set<String> DENY = Set.of(
        "merge_pr", "delete_branch", "execute_shell");
    
    public boolean isAllowed(String toolName) {
        return !DENY.contains(toolName) && 
            (ALLOW.isEmpty() || ALLOW.contains(toolName));
    }
}
```

### 测试验证

```bash
# 危险工具必须拒绝
# 并发压 MCP Server
```

### 完整代码清单

`mcp-example`、`mcp-github-example`

---

## 4. 项目总结

### 优点与缺点

| 维度 | MCP | 手写集成 | 同 JVM @Tool |
|------|-----|---------|-------------|
| 边界清晰度 | 高（进程级隔离） | 散落在各处 | 紧（同进程） |
| 运维复杂度 | 高（多进程管理） | 中（依赖代码质量） | 低 |
| 跨语言支持 | 强（协议层统一） | 弱 | 不支持 |
| 典型缺点 | 心智成本与延迟 | 重复代码、难复用 | 无隔离、无法跨语言 |

### 适用场景

- 需要跨团队维护的工具（Python 团队提供、Java 团队调用）
- 需要严格隔离的工具执行环境（shell 脚本、文件操作）
- 插件化 AI 应用架构——主服务 + 多个 MCP 工具 Server

### 不适用场景

- 无进程隔离能力的团队——应先补平台基础设施
- 延迟要求在微秒级别的工具——MCP 有网络开销

### 注意事项

- **版本协商**——MCP Client 和 Server 的协议版本必须一致
- **Secret 轮换**——MCP 使用的 token 要定期轮换，并写入密钥管理服务
- **NetworkPolicy**——在 K8s 中限制 MCP Server 的网络出入规则

### 常见踩坑

1. **宽权限 token 泄露进提示**——MCP 的认证 token 被模型返回给了用户
2. **无超时**——MCP 工具执行时间过长，堵住了 worker 线程池
3. **审计缺失**——MCP 调用没有日志，出了问题无法回溯是谁调了什么工具

### 进阶思考题

1. MCP + 第 15 章动态工具组合——在 MCP 层面做动态工具暴露与在同 JVM 做有何不同？
2. diff 含客户名的出站 redact 策略——MCP 的响应内容如何做 PII 过滤？

### 推广计划提示

| 角色 | 建议阅读顺序 | 协作要点 |
|------|-------------|----------|
| 开发 | 第 14 章 → 本章 | allow/deny 工具列表 |
| 运维 | NetworkPolicy 配置 | 容器资源限制与监控 |
| 安全 | 审计 scope 设计 | 90 天 token 强制轮换 |