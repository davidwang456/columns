# 第 03 章：Bean 作用域与生命周期——从创建到销毁

> **业务线**：电商 / 订单履约微服务（拟真场景）。本章可独立阅读；与全书案例弱关联。

> **篇章**：基础篇（全书第 1–18 章；核心概念、单机、简单 API、初级实战）

## 上一章思考题回顾

1. **prototype Bean 注入单例**：每次 `getBean` 会得到新实例，但若通过**单例构造器只注入一次**，容器只创建**一个** prototype 实例并长期持有，**不会**「每请求一个新对象」。需要 **lookup 注入**、`ObjectFactory`/`Provider`、或 **scoped-proxy**（中级篇会再展开）。  
2. **Profile 与 YAML**：`spring.profiles.active=dev` 时，会加载 `application-dev.yml`（及 `application.yml` 公共部分）；多环境通过 **Profile** 切配置，避免手工改文件。

---

## 1 项目背景

订单服务里有一类对象天然「带状态」：**购物车上下文**、**请求级 traceId**。若全部做成**单例**，多线程会互相覆盖；若全部 **prototype**，又和 Spring MVC 默认单例 Controller 的协作方式冲突。

**痛点**：新人常把「单例 + 可变字段」写进服务类，导致线上偶发库存扣错用户；或用 prototype 注入单例，却以为「每次请求都会新建」——**生命周期与作用域不匹配**是隐蔽 Bug 源。

```mermaid
flowchart LR
  singleton[Singleton Service]
  proto[Prototype Helper]
  singleton -->|一次注入一个实例| proto
```

**本章目标**：讲清 **singleton / prototype** 默认语义、**初始化/销毁回调**（`@PostConstruct`、`DisposableBean` / `destroyMethod`），以及「何时该用哪种作用域」。

---

## 2 项目设计（剧本式对话）

**角色**：小胖 / 小白 / 大师。  
**结构**：生活化类比 → 追问边界 → 技术映射；至少 **三轮**「交锋」。

**小胖**：Bean 不就是单例吗？还有别的？我脑子里就一张「全公司共用一个对象」的图。

**大师**：默认 **singleton** 是容器里**一份**；**prototype** 是「每次**向容器要**」时更像**现做现卖**——但注意：如果是**注入进别的单例**，那只会在注入那一刻做一份，并不是你想象的「每个请求一份」。

**技术映射**：**Scope** 控制实例个数与生命周期；**singleton** 由容器创建并缓存；**prototype** 创建权仍在容器，但**销毁**默认不交容器。

**小白**：销毁回调对 prototype 生效吗？

**大师**：默认**不**完整管理 prototype 的销毁——创建交给你，销毁要你自己管。singleton 则由容器在关闭时统一销毁。

**技术映射**：**DisposableBean / destroyMethod** 主要作用于 **singleton**；prototype 需自行管理资源释放。

**小胖**：`@PostConstruct` 和构造器有啥区别？我能不能在构造器里调 Redis 预热？

**大师**：构造器只做**对象诞生**，此时依赖可能还没注入完；`@PostConstruct` 在**依赖注入完成之后**执行，适合做**校验、缓存预热、注册资源**。顺序：构造 → 注入 → `@PostConstruct`。

**技术映射**：**Initialization lifecycle** 中，`@PostConstruct` 位于 **`postProcessBeforeInitialization`** 阶段前后（与 `BeanPostProcessor` 交织）；不要在构造器里访问尚未注入的 `@Autowired` 字段。

**小白**：业务上什么时候用 prototype？

**大师**：有**非线程安全**、**短生命周期**、或**每次调用必须新实例**的组件；但**优先**通过**无状态设计**或**局部变量**解决，prototype 是最后手段。

**小胖**：那我把「用户购物车」做成单例 Bean，里面放 `HashMap` 存用户 ID，行不？

**小白**：行个鬼……这是**并发串号**经典事故：两个请求线程互相 `put`，A 用户看到 B 的购物车。

**大师**：这就像**共享单车车筐**——车是共享的（单例服务），但**私人物品**不能长期放筐里；请求级状态要 **request scope + 代理**、**ThreadLocal（谨慎）**、或**显式传参**。

**技术映射**：**有状态单例** = 高危；Web 场景用 **`@RequestScope`** + **`@Scope(proxyMode = TARGET_CLASS)`** 或 **`ObjectFactory`** 延迟取 prototype。

**小白**：`InitializingBean.afterPropertiesSet` 和 `@PostConstruct` 二选一？

**大师**：多数团队优先 **`@PostConstruct`（标准注解）**；`InitializingBean` 是 Spring 早期接口，会让类**耦合 Spring API**，测试略烦。

**技术映射**：**JSR-250** 生命周期注解与 Spring 原生接口**可共存**，顺序需查文档，避免双重初始化。

---

## 3 项目实战

### 3.1 环境准备

| 项 | 说明 |
|----|------|
| JDK | 17+ |
| Maven | 3.9+ |
| 依赖 | `spring-context`（与第 1 章同版本） |
| 包名 | `com.example.lifecycle` |

**目录结构（建议）**

```text
chapter03-lifecycle/
├── pom.xml
└── src/main/java/com/example/lifecycle/
    ├── LifecycleApplication.java
    ├── InventoryWarmup.java
    ├── RequestCounter.java
    ├── OrderFacade.java
    └── Main.java
```

### 3.2 分步实现

**步骤 1 — 目标**：定义带 `@PostConstruct` / `@PreDestroy` 的 Bean（singleton）。

```java
package com.example.lifecycle;

import jakarta.annotation.PostConstruct;
import jakarta.annotation.PreDestroy;
import org.springframework.stereotype.Component;

@Component
public class InventoryWarmup {
    @PostConstruct
    public void warm() {
        System.out.println("warmup: inventory cache");
    }

    @PreDestroy
    public void shutdown() {
        System.out.println("destroy: release resources");
    }
}
```

> 注：`jakarta.annotation-api` 由 `spring-context` 传递依赖提供；若 IDE 报错可显式添加 `jakarta.annotation:jakarta.annotation-api`。

**步骤 2 — 目标**：演示 prototype 作用域。

```java
package com.example.lifecycle;

import org.springframework.context.annotation.Scope;
import org.springframework.stereotype.Component;

@Component
@Scope("prototype")
public class RequestCounter {
    private int n;
    public int next() { return ++n; }
}
```

**步骤 3 — 目标**：单例服务注入 prototype（观察「只创建一次」）。

```java
package com.example.lifecycle;

import org.springframework.stereotype.Service;

@Service
public class OrderFacade {
    private final RequestCounter counter;

    public OrderFacade(RequestCounter counter) {
        this.counter = counter;
    }

    public int tick() {
        return counter.next();
    }
}
```

**配置入口（扫描 `com.example.lifecycle`）**

```java
package com.example.lifecycle;

import org.springframework.context.annotation.ComponentScan;
import org.springframework.context.annotation.Configuration;

@Configuration
@ComponentScan(basePackageClasses = LifecycleApplication.class)
public class LifecycleApplication { }
```

**步骤 4 — 目标**：编写 `main`，打印两次 `tick()`，**复现**「prototype 注入单例只创建一次」。

```java
package com.example.lifecycle;

import org.springframework.context.annotation.AnnotationConfigApplicationContext;

public class Main {
    public static void main(String[] args) {
        try (var ctx = new AnnotationConfigApplicationContext(LifecycleApplication.class)) {
            OrderFacade facade = ctx.getBean(OrderFacade.class);
            System.out.println("tick1=" + facade.tick());
            System.out.println("tick2=" + facade.tick());
        }
    }
}
```

**运行结果（文字描述）**

```text
warmup: inventory cache
tick1=1
tick2=2
destroy: release resources
```

说明：`tick` 连续递增，证明 `RequestCounter` **只注入了一份**；若业务误以为「每次请求新建」，这里会**当场打脸**——这正是本章要刻进肌肉记忆的点。

**步骤 5 — 目标（加深）**：再向容器 **直接** `getBean(RequestCounter.class)` 两次，比较 `==`（通常为 **false**），对比「注入进单例」与「每次 getBean」的差异。

**可能遇到的坑**

| 现象 | 原因 | 处理 |
|------|------|------|
| `@PreDestroy` 未调 | prototype 默认不销毁 | 自行管理资源；或改 singleton |
| Web scope 报错 | 非 Web 环境无 request 上下文 | 用 Boot + Web 或别用 request scope |
| 日志里 `destroy` 未出现 | 未 `close` 上下文或未 `registerShutdownHook` | `try-with-resources` 或 JVM 退出钩 |

### 3.3 完整代码清单与仓库

占位：`chapter03-lifecycle`。

### 3.4 测试验证

1. **`AnnotationConfigApplicationContext` + `registerShutdownHook()`**：观察 JVM 退出前是否打印 `destroy: release resources`。  
2. **断言型测试（示例思路）**：启动上下文 → `getBean(OrderFacade.class)` 连续 `tick()` → `assertEquals(1, tick1); assertEquals(2, tick2);` → `ctx.close()` 后检查副作用（若需严格验证销毁，可改用 `DisposableBean` + 原子变量计数）。

**命令**：`mvn -q test`（若编写 JUnit 集成测试）；`mvn -q exec:java -Dexec.mainClass=com.example.lifecycle.Main`（若配置 exec 插件）直接看控制台输出。

---

## 4 项目总结

### 优点与缺点

| 维度 | 生命周期回调 | 在业务里写大量回调 |
|------|----------------|---------------------|
| 可维护性 | 集中初始化 | 分散难跟踪 |
| 风险 | 忘记销毁泄漏资源 | 手动 finally 易遗漏 |

### 适用场景

1. 连接池、缓存预热。  
2. 注册监听器、Metrics。  
3. 优雅下线释放句柄。  

**不适用**：能用简单 `try-with-resources` 的局部资源不必上升为 Bean 生命周期。

### 常见踩坑经验

1. **单例里保存请求级状态** → 并发串数据。  
2. **prototype 注入单例** → 误以为「每请求新实例」。  
3. **Shutdown 阶段** 仍异步提交任务 → 进程被强杀。

---

## 思考题

1. `@Configuration` 类里的 `@Bean` 方法为何默认被 CGLIB 增强？与 `lite` 模式有何区别？（第 10/31 章会深入。）  
2. `application.yml` 中 `spring.config.import` 与 `spring.profiles.active` 的加载顺序？（第 4 章。）

---

## 推广协作提示

| 角色 | 建议 |
|------|------|
| **开发** | 代码评审检查单例可变字段。 |
| **测试** | 集成测试验证 `@PreDestroy` 在上下文 close 时是否调用。 |
| **运维** | 滚动发布时关注优雅停机与 Bean 销毁日志。 |

**下一章预告**：`Properties`、`YAML`、`Profile`、多环境配置。
