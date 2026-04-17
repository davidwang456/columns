# 第 30 章：自动配置原理——Condition 与配置类解析

> **业务线**：电商 / 订单履约微服务（拟真场景）。本章可独立阅读；与全书案例弱关联。

> **篇章**：中级篇（全书第 19–35 章；架构与分布式、性能、可观测性）

## 上一章思考题回顾

1. **`@ConditionalOnClass`**：类路径 **存在** 某类才装配；**`@ConditionalOnBean`**：容器**已有**某 Bean 才装配（顺序敏感）。  
2. **`@ImportAutoConfiguration`**：在测试中 **精确导入** 自动配置子集，**加速** 切片测试。

---

## 1 项目背景

引入依赖后 **自动出现 Bean**，排查需看 **`META-INF/spring/...AutoConfiguration.imports`** 与 **条件评估报告**。

**痛点**：  
- **`@ConditionalOnMissingBean`** 被自定义 Bean **意外覆盖**。  
- **顺序** `AutoConfigureBefore/After` 误用。

---

## 2 项目设计（剧本式对话）

**角色**：小胖 / 小白 / 大师。  
**结构**：从「加依赖就多 Bean」→ Condition 语义 → 顺序与 `@ConditionalOnBean` 陷阱。

**小胖**：我明明没写配置，为啥一引入 starter 就自动有 Bean？

**大师**：Boot 自动配置是 **候选配置类 + 条件注解**；**`Environment`** 与 **类路径** 决定生效集合。你不是没配置，而是**默认配置已经生效**。

**技术映射**：**AutoConfigurationImportSelector** + **`ConditionEvaluationReport`**。

**小白**：`@ConditionalOnClass` 和 `@ConditionalOnBean` 谁更「强」？

**大师**：语义不同：**OnClass** 看 classpath；**OnBean** 看容器现状且**顺序敏感**——先创建的 Bean 会影响后评估的条件。

**小胖**：`debug=true` 打印一大坨，我看不懂咋办？

**大师**：只看两段：**Positive matches**（为什么生效）与 **Negative matches**（为什么没生效）。像**体检报告**，别从头到尾背下来。

---

## 3 项目实战

### 3.1 分步实现

`debug=true` 启动，阅读 **CONDITIONS EVALUATION REPORT**。

### 3.2 自定义条件

```java
public class OnOrderFeatureCondition implements Condition {
    @Override
    public boolean matches(ConditionContext context, AnnotatedTypeMetadata metadata) {
        return context.getEnvironment().getProperty("app.order.enabled", Boolean.class, false);
    }
}
```

**步骤 3 — 目标**：用 **`ApplicationContextRunner`** 切换 `app.order.enabled=true/false`，观察 Bean 是否出现（比手工启动更快）。

### 3.3 完整代码清单与仓库

`chapter31-auto-config`。

### 3.4 测试验证

`ApplicationContextRunner` 断言配置类是否加载。

**命令**：`mvn -q test`。

**可能遇到的坑**

| 现象 | 原因 | 处理 |
|------|------|------|
| 测试绿生产红 | 测试未加载自动配置 | 对齐 `@Import` / `@SpringBootTest` |

---

## 4 项目总结

### 常见踩坑经验

1. **条件** 与 **Profile** 混用难理解。  
2. **测试** 未加载自动配置导致「测试绿、生产红」。  
3. **类顺序** 导致 `@ConditionalOnBean` 误判。

---

## 思考题

1. **`spring.factories` 与 `AutoConfiguration.imports` 迁移**？（第 39 章。）  
2. **Starter 与自动配置模块拆分**？（第 39 章。）

---

## 推广协作提示

| 角色 | 建议 |
|------|------|
| **开发** | 维护团队内部「排除清单」文档。 |

**下一章预告**：自定义 Starter、SPI、`spring-boot-autoconfigure`。
