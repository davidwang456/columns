# 第 39 章：自定义 Starter——可复用模块与 SPI

> **业务线**：电商 / 订单履约微服务（拟真场景）。本章可独立阅读；与全书案例弱关联。

> **篇章**：高级篇（全书第 36–50 章；源码、极端场景、扩展、SRE）

## 上一章思考题回顾

1. **Boot 3**：**`META-INF/spring/org.springframework.boot.autoconfigure.AutoConfiguration.imports`** 列出自动配置类；旧式 **`spring.factories`** 逐步淘汰。  
2. **拆分**：**`xxx-spring-boot-starter`**（依赖聚合，几乎无代码）+ **`xxx-autoconfigure`**（条件配置与 Bean）。

---

## 1 项目背景

公司内部 **订单风控组件** 要在多个服务复用。复制粘贴配置易**漂移**；**Starter** 将 **默认 Bean + 可覆盖点** 固化。

---

## 2 项目设计（剧本式对话）

**角色**：小胖 / 小白 / 大师。  
**结构**：从「复制配置」→ starter 分层 → 可覆盖点与版本管理。

**小胖**：Starter 不就是多引几个依赖吗？我自己写 `pom` 也行啊。

**大师**：Starter 的价值是 **依赖 BOM + 自动配置 + 约定默认值 + 文档**；否则每个服务复制一遍，**漂移**不可避免。

**技术映射**：**@ConfigurationProperties** + **`@EnableConfigurationProperties`** + **`AutoConfiguration.imports`**。

**小白**：`xxx-starter` 和 `xxx-autoconfigure` 为什么要拆？

**大师**：**starter** 做依赖聚合（几乎无代码）；**autoconfigure** 放条件装配，避免**传递依赖污染**与**类路径副作用**。

**小胖**：业务团队想覆盖默认 Bean，会不会跟 starter 打架？

**大师**：用 **`@ConditionalOnMissingBean`** 留出扩展点；并在文档写清**推荐覆盖方式**。

---

## 3 项目实战

### 3.1 模块结构

- `risk-starter`：依赖 `risk-autoconfigure`  
- `risk-autoconfigure`：`RiskAutoConfiguration` + `META-INF/spring/...imports`

### 3.2 `AutoConfiguration.imports` 内容

```
com.example.risk.autoconfigure.RiskAutoConfiguration
```

**步骤 3 — 目标**：在业务工程中 **排除**某个自动配置（`spring.autoconfigure.exclude`），验证开关生效。

### 3.3 完整代码清单与仓库

`chapter32-starter`。

### 3.4 测试验证

`ApplicationContextRunner` 断言 **属性绑定** 与 **Bean 条件**。

**命令**：`mvn -q test`。

**可能遇到的坑**

| 现象 | 原因 | 处理 |
|------|------|------|
| Bean 重复 | 业务也定义同名类型 | 用条件与命名规范 |

---

## 4 项目总结

### 常见踩坑经验

1. **循环依赖** starter 与业务模块。  
2. **配置前缀** 冲突。  
3. **版本** 与 Boot BOM 不一致。

---

## 思考题

1. **`RouterFunction`** 与注解 MVC？（第 31 章。）  
2. **背压** 在 Reactor 中如何体现？（第 31 章。）

---

## 推广协作提示

| 角色 | 建议 |
|------|------|
| **架构师** | 建立内部 Starter 评审清单。 |

**下一章预告**：WebFlux、Mono/Flux、背压入门。
