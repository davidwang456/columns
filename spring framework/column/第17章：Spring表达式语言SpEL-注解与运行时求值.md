# 第 17 章：Spring 表达式语言（SpEL）——`spring-expression` 与运行时求值

> **业务线**：电商 / 订单履约微服务（拟真场景）。本章可独立阅读；与全书案例弱关联。  
> **篇章**：基础篇（全书第 1–18 章；核心概念、单机、简单 API、初级实战）

> **定位**：系统掌握 **`spring-expression`** 模块提供的 **SpEL**：在 **`@Value`、`@Cacheable` 的 key**、**`@PreAuthorize`**、**XML/注解中的动态值** 等位置编写 **`#{...}`** 表达式；理解 **`ExpressionParser`**、**EvaluationContext**、**类型转换**与**安全边界**（**不可信输入**勿直接拼进表达式）。

## 上一章思考题回顾

1. **索引与扫描**：若索引**不完整**，实现通常会 **回退到 classpath 扫描**（以 **`ClassPathScanningCandidateComponentProvider`** 当前版本为准）；**不要**依赖「无索引就静默失败」的假设。  
2. **CI 门禁**：在 **`mvn clean package`** 后 **断言 `META-INF/spring.components` 存在**（或对 **大仓** 开启 **fail-if-missing** 策略）。

---

## 1 项目背景

「鲜速达」运营希望在 **不重启** 的情况下，通过 **配置中心** 下发 **促销表达式**（例如「满 100 减 10」的 **规则 DSL**）。团队评估后，一部分 **简单规则** 用 **SpEL** 在 **`EvaluationContext`** 里求值；另一部分复杂规则仍走 **独立规则引擎**。若 **误把 SpEL 当通用脚本**、或 **把用户输入直接拼进表达式**，会带来 **注入与性能** 双重风险。

**痛点**：

- **只会在 `@Value` 里写字面量**：遇到 **Bean 方法调用**、**集合筛选** 时束手无策。  
- **缓存 key 乱写 SpEL**：方法参数改名导致 **缓存雪崩** 或 **串 key**。  
- **安全**：SpEL 可触发 **类型引用**、**方法调用**，**攻击面**大于普通占位符。

**痛点放大**：在 **Spring Security** 的 **`@PreAuthorize("hasRole('ADMIN')")`** 背后也有 **表达式体系**（与 SpEL 相关但 **不**要求本章全覆盖）；若 **不理解求值上下文**，权限表达式一改就 **全员 403**。

```mermaid
flowchart LR
  S[表达式字符串] --> P[ExpressionParser]
  P --> E[Expression]
  E --> C[EvaluationContext]
  C --> V[求值结果]
```

---

## 2 项目设计（剧本式对话）

**角色**：小胖 / 小白 / 大师。  
**结构**：SpEL 用在哪 → 与占位符区别 → 安全。

**小胖**：`${}` 和 `#{}` 我老混，不都是配置吗？

**大师**：**`${}`** 多为 **属性占位符**（**`PropertySourcesPlaceholderConfigurer`**）；**`#{}`** 是 **SpEL**，能写 **`@beanName.method()`**、**`T(Math).random()`** 等。**能力更强，风险更大**。

**技术映射**：**`#{}`** → **`BeanExpressionResolver`**；**`${}`** → **纯字符串替换**（概念上对新手足够）。

**小白**：为啥缓存 key 推荐 SpEL？

**大师**：要把 **多个参数** 组合成 **稳定 key**，例如 **`#userId + ':' + #skuId`**；SpEL 能 **直接访问参数名**（需 **编译参数名** 或 **`@Cacheable` 的 key 表达式**）。

**技术映射**：**`@Cacheable(key="#orderId")`**（第 43 章联动）。

**小胖**：用户传的公式我能 `parseExpression` 吗？

**大师**：**默认不要**。若必须做，需 **严格白名单**、**限制类型**、**超时**、**审计**；更安全的做法是 **自研 DSL** 或 **规则引擎**。SpEL **不是沙箱**。

**技术映射**：**`SimpleEvaluationContext`** vs **`StandardEvaluationContext`** 的能力差异（**只读/受限** 场景优先前者）。

---

## 3 项目实战

本章 **Maven + Java 17**，依赖 **`spring-expression`**（单独演示 SpEL，**可不引**完整 `spring-context`）。

### 3.1 环境准备

| 项 | 说明 |
|----|------|
| 依赖 | `spring-expression` |

**`pom.xml`（节选）**

```xml
<dependency>
  <groupId>org.springframework</groupId>
  <artifactId>spring-expression</artifactId>
  <version>6.1.14</version>
</dependency>
```

### 3.2 分步实现

**步骤 1 — 目标**：使用 **`SpelExpressionParser`** 求值 **算术与属性**。

```java
import org.springframework.expression.Expression;
import org.springframework.expression.ExpressionParser;
import org.springframework.expression.spel.standard.SpelExpressionParser;
import org.springframework.expression.spel.support.StandardEvaluationContext;

public class SpelArithmeticDemo {
    public static void main(String[] args) {
        ExpressionParser parser = new SpelExpressionParser();
        Expression exp = parser.parseExpression("(100 + 20) * #discount");
        StandardEvaluationContext ctx = new StandardEvaluationContext();
        ctx.setVariable("discount", 0.9);
        System.out.println(exp.getValue(ctx)); // 108.0
    }
}
```

**运行结果（文字描述）**：控制台打印 **`108.0`**。

**步骤 2 — 目标**：注册 **根对象**，访问 **JavaBean 属性**。

```java
import org.springframework.expression.Expression;
import org.springframework.expression.ExpressionParser;
import org.springframework.expression.spel.standard.SpelExpressionParser;
import org.springframework.expression.spel.support.StandardEvaluationContext;

class Order {
    private int amount = 200;
    public int getAmount() { return amount; }
}

public class SpelBeanDemo {
    public static void main(String[] args) {
        ExpressionParser parser = new SpelExpressionParser();
        Order order = new Order();
        StandardEvaluationContext ctx = new StandardEvaluationContext(order);
        Expression exp = parser.parseExpression("amount > 100");
        System.out.println(exp.getValue(ctx)); // true
    }
}
```

**步骤 3 — 目标**：在 **Spring 容器** 中使用 **`@Value("#{...}")`**（需 **`spring-context`**，可与第 1 章示例合并）。

```java
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

@Component
public class PricingProps {
    private final boolean vipChannel;

    public PricingProps(@Value("#{systemProperties['user.name'] != null}") boolean vipChannel) {
        this.vipChannel = vipChannel;
    }

    public boolean isVipChannel() {
        return vipChannel;
    }
}
```

**注意**：`systemProperties` 等 **内置变量** 以 **当前 Spring 版本文档**为准；示例仅演示 **语法形态**。

### 3.3 可能遇到的坑

| 现象 | 原因 | 处理 |
|------|------|------|
| **`@Value` 不解析 `#{}`** | 未启用 **EmbeddedValueResolver**（非组件环境） | 在 **容器管理 Bean** 内使用 |
| **参数名在 SpEL 中为 `p0`** | **未保留参数名** | **`-parameters`** 或 **`@Cacheable(key="#p0")`** |
| **表达式注入** | **拼接用户输入** | **禁止**；使用 **受限上下文** |

### 3.4 测试验证

编写 **JUnit 5** 断言 **`Expression.getValue`** 与 **预期**一致；对 **边界**（**null**、**除零**）补充用例。

---

## 4 项目总结

### 优点与缺点

| 维度 | SpEL | 硬编码 Java |
|------|------|----------------|
| 灵活性 | **配置期/运行期**可调 | 需改代码发版 |
| 可观测性 | 表达式 **分散** | 逻辑集中 |
| 风险 | **误用**导致 **注入** | **低**（若无不安全反射） |

### 适用场景

1. **注解属性**中的 **动态默认值**。  
2. **缓存、安全、路由** 中的 **声明式表达式**。  
3. **轻量规则**（在 **安全可控**前提下）。

### 注意事项

- **Native 镜像**（第 40 章）：SpEL 与 **反射**相关路径可能需要 **hint**。  
- **性能**：高频路径避免 **每次** `parseExpression`，可 **缓存 Expression**。

### 常见踩坑经验

1. **现象**：改方法名后缓存全 miss。  
   **根因**：**key 表达式**仍用旧名。  

2. **现象**：**`T(java.lang.Runtime).getRuntime()`** 被恶意利用。  
   **根因**：**未限制** `StandardEvaluationContext` 能力。  

---

## 思考题

1. **`SimpleEvaluationContext.create()`** 与 **`StandardEvaluationContext`** 在 **方法调用、类型引用**上各适合什么场景？  
2. 你会如何对 **SpEL 求值**做 **单测** 与 **突变测试**（property-based）？（下一章：**`spring-instrument`** 与 **Java Agent**。）

---

## 推广协作提示

| 角色 | 建议 |
|------|------|
| **开发** | 团队规范：**禁止**用户输入直达 **`parseExpression`**。 |
| **安全** | **SpEL** 纳入 **安全评审**清单。 |

**下一章预告**：**`spring-instrument`**——**Instrumentation** 与 **LoadTimeWeaver**。
