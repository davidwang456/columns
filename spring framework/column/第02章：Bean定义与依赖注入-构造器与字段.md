# 第 02 章：Bean 定义与依赖注入——构造器与字段

> **业务线**：电商 / 订单履约微服务（拟真场景）。本章可独立阅读；与全书案例弱关联。

> **篇章**：基础篇（全书第 1–18 章；核心概念、单机、简单 API、初级实战）

## 上一章思考题回顾

1. **构造器注入**让依赖在创建对象时就绪，`final` 字段可保证不可变，测试时直接 `new` 被测类并传入 mock；**字段 `@Autowired`** 依赖反射注入，子类/测试若不启动容器较难手动赋值，且隐藏了必填依赖。反射成本在现代 JVM 上通常可接受，但构造器注入更符合「显式依赖契约」。  
2. 两个 `InventoryService` 实现同时标注 `@Component` 且注入点按**类型**解析时，启动会报 **`NoUniqueBeanDefinitionException`**，不会静默选其一。可用 **`@Primary`** 标明默认实现，或用 **`@Qualifier("beanName")`** / 自定义限定符与业务语义对齐。

---

## 1 项目背景

「鲜速达」的库存能力正在从「内存假数据」演进到「对接仓储 WMS」。短期内存在两套实现：**本地 Stub**（联调前）与 **HTTP 客户端**（联调后）。若团队仍靠 `new` 切换，会出现分支爆炸；若全部交给容器装配，又必须回答：**同一接口多个 Bean 时，Spring 怎么知道注入哪一个？**

没有清晰规则时，典型痛点包括：

- **启动期失败**：多实现冲突，本地能跑只因「碰巧只有一个实现」。  
- **隐式默认**：有人删掉一个实现后，注入「默默」指向剩余实现，行为变化却无编译提示。  
- **字段注入泛滥**：`@Autowired` 贴在字段上，单元测试不得不拉 `@SpringBootTest` 或反射，**反馈变慢**。

本章围绕 **Bean 的注册方式**（`@Component` / `@Bean`）、**注入点选择**（构造器 / 字段 / 方法）、以及 **`@Primary` / `@Qualifier`** 的语义，把装配规则讲透。

```mermaid
flowchart TD
  iface[InventoryService 接口]
  stub[LocalInventoryStub]
  remote[WmsInventoryClient]
  order[OrderService 注入点]
  iface --> stub
  iface --> remote
  stub -->|@Primary 或 @Qualifier| order
  remote -->|@Primary 或 @Qualifier| order
```

**痛点放大**：当促销、预售、跨境仓分别绑定不同库存策略时，「谁默认、谁按场景选」必须**可文档化、可 Code Review**，而不是依赖同事记忆。

---

## 2 项目设计（剧本式对话）

**角色**：小胖（生活化抛问题）、小白（追问原理与边界）、大师（选型与由浅入深打比方）。  
**结构**：小胖开球 → 小白质疑/追问 → 大师解答并引出下一子话题；循环多轮；关键处给出「技术映射」。

**小胖**：为啥不直接给实现类起不同名字，注入具体类？还省得搞 `@Qualifier`。

**小白**：那接口的意义就弱了；而且高层模块会重新依赖具体实现，换 WMS 厂商时改动面大。

**大师**：接口隔离的是**变化方向**；`@Qualifier` 解决的是**同一接口多实现时的路由**。你可以把它想成外卖 App 里「默认店铺」和「指定店铺」——`@Primary` 是平台默认推荐，`@Qualifier` 是用户点名。

**技术映射**：**@Primary** = 默认候选；**@Qualifier** = 显式指定 Bean 名称或自定义限定符。

**小胖**：那我搞两个类 `LocalInventory` 和 `WmsInventory`，`OrderService` 里写死类型，不是更直观？

**大师**：直观是**当下**直观；明天采购说「再加一个香港保税仓实现」，你就要改构造器签名、改所有调用方 import。**依赖接口 + 路由规则**才能把变化关在装配层。

**技术映射**：**依赖倒置（DIP）** 在 Spring 里靠 **接口注入 + 条件/限定符** 落地。

**小白**：构造器注入和字段注入，在 Spring 里解析顺序有差别吗？循环依赖时呢？

**大师**：构造器注入**更利于不可变**与**明确必填依赖**；循环依赖时，纯构造器链路 Spring 可能无法完成（需要调整设计或懒加载）。字段/方法注入有时能「拖后」解析，但容易掩盖设计问题。

**技术映射**：**构造器注入**优先于字段注入；团队规范常推荐构造器 + `final` 字段。

**小胖**：字段 `@Autowired` 多省事啊，少写好几行构造器，为啥团队老怼我？

**小白**：省事的是**打字**，费事的是**读代码的人**：字段注入让「我到底依赖谁」变成隐式清单，测试还要反射塞值。构造器一眼看穿，还能用 `final` 锁死引用。

**大师**：可以打个比方：**构造器注入像登机前核对护照和机票**——少一样都走不了；**字段注入像到了登机口才从口袋里掏证件**——有时掏得出来，有时掏错口袋。

**技术映射**：**AutowiredAnnotationBeanPostProcessor** 对字段/方法做注入；构造器注入走 **`ConstructorResolver`**，失败更早暴露。

**小胖**：`@Bean` 方法和 `@Component` 有啥分工？我能不能全用 `@Bean`？

**大师**：`@Component` 适合**自己的类**——源码在手，直接打注解。`@Bean` 适合**第三方库类**——你不能改 `new RestTemplate()` 的源码，就在 `@Configuration` 里用方法包装。两者都会被注册成 BeanDefinition，只是声明位置不同。

**技术映射**：**@Bean** = 工厂方法注册；**@ComponentScan** = 类路径扫描注册。

**小白**：如果一个类既被 `@Bean` 返回，又被 `@Component` 扫到，会怎样？

**大师**：可能**重复定义**或**后者覆盖**，取决于是否允许 Bean 覆盖（Boot 2.1+ 默认禁止同名覆盖）。工程上应避免同一逻辑注册两次。

**技术映射**：**Bean 名称**默认是方法名或类名首字母小写；冲突时用 `@Qualifier` 显式对齐。

**小胖**：`@Qualifier("wms")` 写字符串，我手滑打错字母咋办？

**大师**：生产里常用 **自定义 `@interface` + `@Qualifier` 元注解**（类型安全限定符），或统一常量化；字符串适合 demo，团队规范里要配 **IDE 检查/ArchUnit**。

**技术映射**：**@Qualifier** 可与 **自定义组合注解** 绑定，减少魔法字符串。

**小白**：`Optional<InventoryService>` 能注入吗？

**大师**：可以，但**语义要清楚**：是「允许没有」还是「容器的可选」？多数业务依赖不建议用 `Optional` 糊弄「可能没有」，容易把配置错误拖成 NPE。

**技术映射**：**依赖可选** 更常见用 **`ObjectProvider<T>`** 或 **`@Autowired(required=false)`**（谨慎）。

---

## 3 项目实战

延续第 1 章的 **Maven + `spring-context` + Java 17**；本章展示**多实现**与**限定符**，并刻意走一遍「**先失败、再修对**」的排错体验，贴近真实联调。

### 3.1 环境准备

| 项 | 说明 |
|----|------|
| JDK | 17+ |
| 构建 | Maven 3.9+ |
| 依赖 | `spring-context`（与第 1 章同版本，如 `6.1.14`） |
| 可选 | `spring-test`、`junit-jupiter`（用于 3.4） |

**推荐目录结构**

```text
chapter02-di/
├── pom.xml
└── src/main/java/com/example/order/
    ├── OrderApplication.java
    ├── InventoryService.java
    ├── LocalInventoryStub.java
    ├── WmsInventoryClient.java
    ├── OrderService.java
    ├── WholesaleOrderService.java
    └── Main.java
└── src/test/java/com/example/order/
    └── WiringSmokeTest.java
```

**命令行**：`mvn -q -DskipTests compile` 应输出 `BUILD SUCCESS`。

### 3.2 分步实现

**步骤 0 — 目标（刻意踩坑）**：先只保留 `LocalInventoryStub` 与 `WmsInventoryClient` 两个 `@Component`，**都去掉** `@Primary`，观察启动或注入失败信息（`NoUniqueBeanDefinitionException`），截图/复制栈贴进团队 Wiki——这是新人最常见的「第二天就炸」问题。

**步骤 1 — 目标**：同一接口两个实现，默认 Stub，远程实现用限定符名。

```java
package com.example.order;

public interface InventoryService {
    boolean reserve(String skuId, int qty);
}
```

```java
package com.example.order;

import org.springframework.context.annotation.Primary;
import org.springframework.stereotype.Component;

@Primary
@Component
public class LocalInventoryStub implements InventoryService {
    @Override
    public boolean reserve(String skuId, int qty) {
        return qty > 0;
    }
}
```

```java
package com.example.order;

import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.stereotype.Component;

@Component
@Qualifier("wms")
public class WmsInventoryClient implements InventoryService {
    @Override
    public boolean reserve(String skuId, int qty) {
        return skuId != null && qty > 0;
    }
}
```

**步骤 2 — 目标**：`OrderService` 默认走 `@Primary`；另一个服务显式要 WMS。

```java
package com.example.order;

import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.stereotype.Service;

@Service
public class OrderService {
    private final InventoryService inventory;

    public OrderService(InventoryService inventory) {
        this.inventory = inventory;
    }

    public String placeDefault(String skuId, int qty) {
        return inventory.reserve(skuId, qty) ? "OK" : "FAIL";
    }
}

@Service
public class WholesaleOrderService {
    private final InventoryService wmsInventory;

    public WholesaleOrderService(@Qualifier("wms") InventoryService wmsInventory) {
        this.wmsInventory = wmsInventory;
    }

    public String placeWholesale(String skuId, int qty) {
        return wmsInventory.reserve(skuId, qty) ? "WMS_OK" : "WMS_FAIL";
    }
}
```

**步骤 3 — 目标**：配置类扫描。

```java
package com.example.order;

import org.springframework.context.annotation.ComponentScan;
import org.springframework.context.annotation.Configuration;

@Configuration
@ComponentScan(basePackageClasses = OrderApplication.class)
public class OrderApplication { }
```

**步骤 4 — 目标**：`main` 验证两个服务注入的 Bean 不同。

```java
package com.example.order;

import org.springframework.context.annotation.AnnotationConfigApplicationContext;

public class Main {
    public static void main(String[] args) {
        try (var ctx = new AnnotationConfigApplicationContext(OrderApplication.class)) {
            System.out.println(ctx.getBean(OrderService.class).placeDefault("A", 1));
            System.out.println(ctx.getBean(WholesaleOrderService.class).placeWholesale("A", 1));
        }
    }
}
```

**运行结果（文字描述）**：控制台依次输出：

```text
OK
WMS_OK
```

说明：默认注入命中 `@Primary` 的 `LocalInventoryStub`；批发订单服务通过 `@Qualifier("wms")` 显式绑定 `WmsInventoryClient`。

**步骤 5 — 目标（可选加深）**：在 `main` 里打印 `ctx.getBean("wms", InventoryService.class).getClass().getName()`，验证 Bean 名称与限定符一致；再对比 `ctx.getBean(InventoryService.class)` 的类型（应落在 `@Primary` 实现）。

**可能遇到的坑**

| 现象 | 原因 | 处理 |
|------|------|------|
| `NoUniqueBeanDefinitionException` | 多实现且无 `@Primary`/无 `@Qualifier` | 增加 `@Primary` 或注入点加 `@Qualifier` |
| `@Qualifier` 与 `@Bean` 方法名不一致 | 限定符字符串与 Bean 名称对齐 | 统一命名或改用类型安全限定符 |
| 去掉 `@Primary` 后 `OrderService` 无法注入 | 构造器按类型解析仍不唯一 | 构造器参数加 `@Qualifier` 或只保留一个实现 |
| 测试绿、生产红 | 测试 `@ContextConfiguration` 只扫了子包 | 对齐 `@ComponentScan` 与生产入口 |

### 3.3 完整代码清单与仓库

目录：`src/main/java/com/example/order/`。示例仓库占位：`https://github.com/<your-org>/spring-column-samples/tree/main/chapter02-di`。

### 3.4 测试验证

在 `pom.xml` 增加 `spring-test`、`junit-jupiter`（`test` scope），并建议显式指定 **Surefire 3.x** 以稳定识别 JUnit 5。

**示例（烟测）**

```java
package com.example.order;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.context.ApplicationContext;
import org.springframework.test.context.junit.jupiter.SpringJUnitConfig;

import static org.junit.jupiter.api.Assertions.assertTrue;

@SpringJUnitConfig(OrderApplication.class)
class WiringSmokeTest {

    @Autowired
    ApplicationContext ctx;

    @Test
    void primary_is_local_and_wms_is_named() {
        InventoryService primary = ctx.getBean(InventoryService.class);
        InventoryService wms = ctx.getBean("wms", InventoryService.class);
        assertTrue(primary instanceof LocalInventoryStub);
        assertTrue(wms instanceof WmsInventoryClient);
    }
}
```

**命令**：`mvn -q test`，期望 **BUILD SUCCESS**；若失败，先看是否未加测试依赖或 Surefire 版本过旧。

---

## 4 项目总结

### 优点与缺点

| 维度 | 构造器 + `@Qualifier` | 字段 `@Autowired` |
|------|------------------------|-------------------|
| 可读性 | 依赖一眼可见 | 依赖分散 |
| 测试 | 易手动 new | 依赖容器或反射 |
| 循环依赖 | 暴露设计问题 | 有时掩盖问题 |
| 团队规范 | 易在 Code Review 约束 | 易泛滥 |

### 适用场景

1. 多数据源、多支付渠道、多库存源需要路由。  
2. 第三方库 Bean 用 `@Bean` 集中声明。  
3. 需要默认实现 + 特例注入。  

**不适用**：无多实现、无第三方装配的极简 demo 可继续单实现注入。

### 注意事项

- Bean 名称与 `@Qualifier` 字符串一致（注意拼写）。  
- Spring Framework 6 使用 `jakarta.inject` 可选与 `@Named` 互通（需依赖）。  
- 避免同名 `@Bean` 与方法重载导致歧义。

### 常见踩坑经验

1. **测试通过、生产失败**：测试上下文只扫了部分包，多实现未加载。  
2. **@Primary 滥用**：全局默认过多，业务语义不清。  
3. **接口注入点未加限定符**：新增第二实现后全链路爆炸。

---

## 思考题

1. `@Scope("prototype")` 的 Bean 注入到单例服务时，不加代理会出现什么现象？（第 3 章解答。）  
2. `application-dev.yml` 与 `application-prod.yml` 在 Spring 中如何与 `spring.profiles.active` 联动？（第 4 章。）

---

## 推广协作提示

| 角色 | 建议 |
|------|------|
| **开发** | 在团队规范中明确「构造器优先」与多实现路由约定。 |
| **测试** | 为每种 `@Qualifier` 路径设计最小用例，避免只测默认 Bean。 |
| **运维** | 关注不同 Profile 下是否误激活多实现导致行为漂移。 |

**下一章预告**：Bean 作用域、生命周期回调、`BeanPostProcessor` 初识。
