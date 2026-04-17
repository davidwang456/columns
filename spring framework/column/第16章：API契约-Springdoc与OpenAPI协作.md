# 第 16 章：API 契约——Springdoc 与 OpenAPI 协作

> **业务线**：电商 / 订单履约微服务（拟真场景）。本章可独立阅读；与全书案例弱关联。

> **篇章**：基础篇（全书第 1–18 章；核心概念、单机、简单 API、初级实战）

## 上一章思考题回顾

1. **同步**：**springdoc-openapi** 从 **Controller + 注解** 生成 **`openapi.yaml`**；也可用 **代码优先** 与 **契约优先**（先写 YAML 再生成接口）双向，但需流程约束。  
2. **契约测试**：**Spring Cloud Contract** 或 **Pact**；**消费者驱动**（CDC）由前端/调用方定义期望。

---

## 1 项目背景

多端并行开发需要 **稳定的 API 文档** 与 **Mock**。Word/飞书文档易与代码**漂移**。

**痛点**：  
- **枚举**、**错误码** 文档不全。  
- **版本** `/v1` 与 **兼容策略** 不清。

---

## 2 项目设计（剧本式对话）

**角色**：小胖 / 小白 / 大师。  
**结构**：从「飞书文档」→ 契约优先 → 与前端/测试协作。

**小胖**：OpenAPI 不就是自动生成文档吗？我注释写细点不行吗？

**大师**：OpenAPI 是 **机器可读契约**：可生成 **客户端 SDK、Mock、契约测试、网关校验**。注释写再细，**编译器不帮你**。

**技术映射**：**GroupedOpenApi** + **`@Operation` / `@Schema`** + **springdoc**。

**小白**：契约以代码为准还是以 YAML 为准？

**大师**：团队要定 **单一真相源**：常见是 **代码优先**（springdoc 导出）；若强契约组织，**契约优先** + 生成接口（成本更高）。

**小胖**：`/v3/api-docs` 暴露给外网行不行？

**小白**：行，但等于把**内部模型**摊开；至少应 **鉴权** 或 **内网**。

---

## 3 项目实战

### 3.1 环境准备

| 项 | 说明 |
|----|------|
| 依赖 | springdoc（WebMVC） |
| 安全 | 若启用 Security，需放行 docs 端点 |

```xml
<dependency>
  <groupId>org.springdoc</groupId>
  <artifactId>springdoc-openapi-starter-webmvc-ui</artifactId>
  <version>2.5.0</version>
</dependency>
```

### 3.2 分步实现

**步骤 1 — 目标**：启动后访问 `http://localhost:8080/swagger-ui.html`（路径以版本为准）。

**步骤 2 — 目标**：导出文档：

```bash
curl -s http://localhost:8080/v3/api-docs > openapi.json
```

**运行结果（文字描述）**：`openapi.json` 含 `paths` 与 `components/schemas`。

### 3.3 完整代码清单与仓库

`chapter23-openapi`。

### 3.4 测试验证

**Snapshot** 测试比对 `api-docs` JSON；CI 中 **失败即契约变更**。

**命令**：`mvn -q test`。

**可能遇到的坑**

| 现象 | 原因 | 处理 |
|------|------|------|
| 文档 404 | Security 拦截 | permitAll |
| Schema 不准 | 泛型擦除 | `@Schema` 显式标注 |

---

## 4 项目总结

### 常见踩坑经验

1. **安全配置** 拦截 `/v3/api-docs`。  
2. **泛型** 擦除导致 schema 不准。  
3. **敏感字段** 未隐藏。

---

## 思考题

1. **HikariCP** 最大连接数与线程池关系？（第 24 章。）  
2. **`@Lazy` 注入** 副作用？（第 24 章。）

---

## 推广协作提示

| 角色 | 建议 |
|------|------|
| **测试** | 从 OpenAPI 生成用例矩阵。 |

**下一章预告**：连接池、线程池、懒加载与启动优化。
