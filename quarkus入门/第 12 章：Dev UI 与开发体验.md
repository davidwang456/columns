# 第 12 章：Dev UI 与开发体验

## 0. 课程卡片

| 项目 | 说明 |
|------|------|
| **建议课时** | 35～45 分钟 |
| **学习目标** | 使用 Dev UI 辅助开发；**生产禁用** Dev UI 与相关端点 |
| **先修** | 第 1～2 章 |

---

## 1. 项目背景

Quarkus **开发模式**提供 Dev UI（路径随版本变化，常见 `/q/dev`），用于查看扩展、配置、部分诊断页面。推广期可降低「文档恐惧症」；生产必须防止信息泄露。

---

## 2. 项目设计：大师与小白的对话

**小白**：「Dev UI 能代替官方文档吗？」

**大师**：「**不能代替**，但能加速『我装了这个扩展后到底多了什么』的认知。」

**安全**：「生产若暴露 /q/dev 会怎样？」

**大师**：「攻击面扩大：配置泄露、端点探测。必须 **profile 关闭 + 网络策略**。」

**运维**：「我们 Ingress 默认转发所有路径。」

**大师**：「要对 `/q/*` 做 **WAF 或网关黑名单**（按安全基线）。」

**测试**：「E2E 要不要测 Dev UI？」

**大师**：「一般**不测**；测业务 API。」

---

## 3. 知识要点

- `quarkus:dev` 自动开启开发特性  
- `%prod` 关闭 Dev UI（属性名查当前版本 **Configuration Reference**）  
- 与 Continuous Testing（若启用）配合可本地快速反馈

---

## 4. 项目实战

### 4.1 `pom.xml`

使用第 2 章「REST + Health + OpenAPI」组合即可。

### 4.2 `application.properties`

```properties
%dev.quarkus.swagger-ui.always-include=true
# 生产务必关闭（示例属性名请以版本文档为准）
%prod.quarkus.swagger-ui.always-include=false
```

Dev UI 相关开关示例（若文档提供）：

```properties
%prod.quarkus.dev-ui.enabled=false
```

### 4.3 操作步骤（无代码）

```bash
./mvnw quarkus:dev
# 浏览器打开 http://localhost:8080/q/dev
```

浏览：已安装扩展、配置项链接、（若有）Continuous Testing 面板。

### 4.4 Kubernetes：生产 `Deployment` 片段（环境强制 prod）

```yaml
env:
  - name: QUARKUS_PROFILE
    value: "prod"
```

---

## 5. 课堂实验

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | dev 模式打开 Dev UI，截图扩展列表 | 留存培训资料 |
| 2 | 切换到 `prod` profile 本地运行 jar，确认 `/q/dev` 不可访问或返回 404 | 安全基线体感 |
| 3 | 小组讨论：公司 Ingress 对 `/q/*` 的策略 | 输出行动项 |

---

## 6. 项目总结

| 维度 | 说明 |
|------|------|
| **优点** | 上手快；培训友好。 |
| **缺点** | 误开有泄露风险。 |
| **适用场景** | 本地、内训、POC。 |
| **注意事项** | 版本差异；文档为准。 |
| **常见踩坑** | prod 误带 dev 配置。 |

**延伸阅读**：<https://quarkus.io/guides/dev-ui>
