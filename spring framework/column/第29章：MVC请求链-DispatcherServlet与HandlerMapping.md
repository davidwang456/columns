# 第 29 章：MVC 请求链——DispatcherServlet 与 HandlerMapping

> **业务线**：电商 / 订单履约微服务（拟真场景）。本章可独立阅读；与全书案例弱关联。

> **篇章**：中级篇（全书第 19–35 章；架构与分布式、性能、可观测性）

## 上一章思考题回顾

1. **`HandlerMapping`**：根据 **URI + Method** 找到 **HandlerExecutionChain**（Controller 方法 + 拦截器）；**`HandlerAdapter`**：真正 **invoke** 方法（含参数解析、返回值处理）。  
2. **`@RequestBody`**：由 **`RequestResponseBodyMethodProcessor`** 在 **HandlerAdapter** 调用前，通过 **HttpMessageConverter** 读 body。

---

## 1 项目背景

**404** 与 **405** 混淆：映射存在但 **Method 不匹配**；**拦截器** 返回 false 后 **无响应**。需理解 **DispatcherServlet** 主流程。

---

## 2 项目设计（剧本式对话）

**角色**：小胖 / 小白 / 大师。  
**结构**：先画 **DispatcherServlet** 主链路 → 再谈 404/405 → 拦截器返回 false 的坑。

**小胖**：请求进 Tomcat 后，到底谁先谁后？

**大师**：`DispatcherServlet#doDispatch`：**getHandler → getHandlerAdapter → applyPreHandle → handle → applyPostHandle → processDispatchResult**。

**技术映射**：**HandlerMapping** 列表 **顺序** 决定匹配优先级；**HandlerAdapter** 负责真正调用方法（参数解析、返回值处理）。

**小白**：404 和 405 在 Spring MVC 里怎么区分？

**大师**：找不到 handler → **404**；找到 handler 但方法不允许 → **405**（`OPTIONS` 常用来探测 `Allow`）。

**小胖**：拦截器里 `return false` 后为啥客户端像「没响应」？

**大师**：需要 **显式写响应** 或 `response.sendError()`；否则链路可能被**截断**却无 body（依版本与过滤器链而定）。

---

## 3 项目实战

### 3.1 调试

在 `DispatcherServlet.doDispatch` 打断点，观察 **handler** 与 **adapter**。

### 3.2 分步实现

自定义 **`HandlerInterceptor`** 记录耗时。

**步骤 3 — 目标**：用 **`curl -X OPTIONS -i`** 验证 **405** 与 `Allow` 头。

### 3.3 完整代码清单与仓库

`chapter30-mvc-chain`。

### 3.4 测试验证

`MockMvc` 发送 **OPTIONS** 看 **Allow** 头。

**命令**：`mvn -q test`。

**可能遇到的坑**

| 现象 | 原因 | 处理 |
|------|------|------|
| 404 但路由明明存在 | `context-path` / servlet mapping | 检查 `server.servlet.context-path` |

---

## 4 项目总结

### 常见踩坑经验

1. **多个 Controller** 映射冲突。  
2. **consumes/produces** 误配。  
3. **异步请求** `DeferredResult` 与拦截器。

---

## 思考题

1. **`@ConditionalOnClass`** 与 **`@ConditionalOnBean`** 区别？（第 30 章。）  
2. **`@ImportAutoConfiguration`** 用途？（第 30 章。）

---

## 推广协作提示

| 角色 | 建议 |
|------|------|
| **开发** | 理解 404 与 405 的排查路径。 |

**下一章预告**：自动配置、`Condition` 评估、配置类解析。
