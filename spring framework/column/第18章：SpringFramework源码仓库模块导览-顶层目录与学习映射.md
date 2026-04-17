# 第 18 章：Spring Framework 源码仓库模块导览——顶层目录与学习映射

> **业务线**：电商 / 订单履约微服务（拟真场景）。本章可独立阅读；与全书案例弱关联。  
> **篇章**：基础篇（全书第 1–18 章；核心概念、单机、简单 API、初级实战）

> **定位**：对照官方 `spring-framework` 仓库根目录下**各顶层模块/目录**，说明职责、与 Maven 坐标的对应关系，并映射到本专栏已有章节；对此前专栏中**未单独成篇**的模块给出**最小学习抓手**，避免「克隆了源码却不知从哪读起」。

## 上一章思考题回顾

1. **MVP 路径**：可按 **IoC（第 1–3 章）→ 数据与事务（第 9、19 章）→ Web（第 5–7 章）→ Boot 与观测（第 10、23 章）** 裁剪；其余按岗位增量。  
2. **大版本回归**：锁定 **Release Notes**、**Jakarta 命名空间**、**Baseline Java**；对 **Native（第 40 章）**、**Security（第 22 章）** 做矩阵冒烟。

---

## 1 项目背景

团队在本地克隆了 **`spring-framework`** 仓库，IDE 里展开根目录看到一长串 **`spring-*` 文件夹**与 **`framework-bom`**、**`gradle`** 等，新人容易混淆两类问题：

- **「我业务项目要引哪个依赖？」**——对应 **Maven/Gradle 坐标**与 **BOM 管理**。  
- **「我读源码该打开哪个模块？」**——对应 **源码树分层**与**边界**。

若缺少一张**总览表**，学习容易变成「点进 `spring-context` 盲读」，既费时间又抓不住**横切能力**（AOP、事务、消息）究竟落在哪些 jar 里。

**痛点放大**：

- **依赖漂移**：不用 BOM 时，各模块版本不一致，运行时出现「类存在但方法 NoSuchMethod」类问题。  
- **源码阅读路径混乱**：不知道 **`spring-web`** 与 **`spring-webmvc`** 分工，会在错误目录里搜 `DispatcherServlet`。  
- **能力遗漏**：例如 **`spring-context-indexer`** 能缩短启动扫描，但从未听说则一直吃默认启动成本。

下面用一张**与仓库顶层目录一致**的对照表，把截图中的**每一个**主题都落到「一句话职责 + 专栏延伸」。

---

## 2 项目设计（剧本式对话）

**角色**：小胖 / 小白 / 大师。  
**结构**：先对齐「目录 ≠ 业务包名」→ BOM/平台 → 再扫 `spring-*` 分工。

**小胖**：我数了数，根目录几十个文件夹，跟我在应用里 `pom.xml` 里写的 `spring-context` 对不上号啊？

**大师**：**仓库目录名**大致对应 **Gradle 子项目 / Maven artifact**，但 **`framework-docs`**、**`integration-tests`** 这类是**文档与测试**，不会进你业务 jar。**应用依赖**看 **`spring-*` + BOM**。

**技术映射**：**`framework-bom`** 聚合**推荐版本矩阵**；业务工程 `dependencyManagement` 导入 BOM 后，子依赖不写版本也可对齐。

**小白**：那 **`gradle/`** 和 **`framework-platform`** 我也要学吗？

**大师**：**`gradle/`** 是 Spring 团队**构建本仓库**用的封装（约定、插件版本），**不是你的应用必学项**；除非你给公司写**多模块统一构建**可参考。**`framework-platform`** 与 **BOM** 同属**版本治理**，读 **第 10 章** 的依赖管理心智即可。

**技术映射**：**平台/BOM** = **可传递的版本一致性**；与 **Spring Boot BOM** 是同一类问题。

**小胖**：截图里那些 **`spring-websocket`**、**`spring-oxm`**，以前专栏好像没专门一章？

**大师**：仓库扩展主题已按 **第 17、44–50 章** 等专章展开（结构与第 1 章同型；**WebSocket** 见 **第 32 章**）。本章表仍保留 **总览**；深入读对应章节即可。

**技术映射**：**模块 = 能力边界**；**章节 = 学习路径**；**仓库顶层**与 **专章** 一一对照见下表「专栏延伸」列。

---

## 3 项目实战：顶层目录与专栏映射总表

下列顺序与 **`spring-framework` 仓库根目录**常见排列一致（与 IDE 截图一致）。**职责**一句话；**专栏**列为本专栏**主要**覆盖章节（未专章则标「—」并见说明）。

| 顶层目录/模块 | 职责（一句话） | 专栏延伸 |
|----------------|----------------|----------|
| **framework-bom** | 提供 **Bill of Materials**，统一 Spring Framework 各模块**推荐版本**。 | **第 15 章**；第 4、10、39 章 |
| **framework-docs** | 本仓库 **Asciidoc 文档**源码与生成管线，**非运行时依赖**。 | **第 44 章**；在线 **Reference** 即对应此产出 |
| **framework-platform** | **平台版本**声明，与 BOM 协同做**版本对齐**（Gradle 生态常用）。 | **第 15 章**；第 10 章 |
| **gradle** | 构建 **Spring Framework 自身** 的 Gradle 配置与约定，**非业务应用必引**。 | **第 45 章**；业务侧另见 **Gradle 依赖与 BOM 导入**（第 10 章） |
| **integration-tests** | 框架级 **集成测试**与场景复现，**非业务 jar**。 | **第 46 章**；与 **第 11 章**「业务测试分层」对照 |
| **spring-aop** | **AOP 抽象**、Advice/Advisor、与容器集成。 | 第 8、20、28 章 |
| **spring-aspects** | **AspectJ 切面**与常用切面（如 `@Configurable`）的**配套实现**。 | **第 50 章**；第 8、20、28 章 |
| **spring-beans** | **BeanDefinition**、**BeanFactory**、属性编辑与合并，是容器的**核心 Bean 元模型**。 | 第 2、3、36、37 章 |
| **spring-context** | **ApplicationContext**、事件、资源、国际化、**组件扫描**等**应用上下文**。 | 第 1、4、12、36 章等 |
| **spring-context-indexer** | 编译期生成 **`META-INF/spring.components`**，**缩短**组件扫描/条件解析成本。 | **第 47 章** |
| **spring-context-support** | **调度、缓存、邮件、UI** 等「常用集成」的 **support** 包（如 `CacheManager` 抽象的部分实现）。 | 第 21（缓存）、24（定时）章 |
| **spring-core** | **核心工具**、**IoC 基础**、**资源与类型转换**，被几乎所有模块依赖。 | 第 1、4、41 章等 |
| **spring-core-test** | 框架与测试用的 **core 侧工具**（非 `spring-test` 的全部）。 | 第 11 章（部分工具类可能间接接触） |
| **spring-expression** | **SpEL**（Spring Expression Language），用于注解属性、缓存 key、安全表达式等。 | **第 17 章**；第 40 章（Native 与 SpEL hint） |
| **spring-instrument** | **Java Agent** 与 **Instrumentation** 相关类，用于类加载期增强/检测（高级场景）。 | **第 48 章** |
| **spring-jdbc** | **JdbcTemplate**、**NamedParameterJdbcTemplate**、异常层次与 **DataSource** 协作。 | 第 9、26 章 |
| **spring-jms** | **JMS 1.x/2.x** 抽象、**JmsTemplate**、监听器容器，对接传统消息中间件。 | **第 34 章**；第 25 章（**Kafka/Rabbit** 抽象对照） |
| **spring-messaging** | **消息模型**、**STOMP**、**WebSocket 消息**子协议与 **@MessageMapping** 基础设施。 | 第 25 章（消息模型）；与 **WebSocket** 联用时见下行 |
| **spring-orm** | 与 **Hibernate / JPA** 等 ORM 框架的**集成**（`SessionFactory`、事务同步等）。 | 第 14 章（Spring Data JPA） |
| **spring-oxm** | **XML** 与对象 **OXM**（Marshaller/Unmarshaller），如 JAXB、JiBX 等。 | **第 49 章**；JSON 见第 6 章 |
| **spring-r2dbc** | **响应式关系型数据库**访问（R2DBC），与 **WebFlux** 搭配避免阻塞。 | **第 33 章**；第 31 章（**WebFlux** 总览） |
| **spring-test** | **TestContext**、**`@ContextConfiguration`**、**MockMvc**、**Testcontainers** 协作等测试支持。 | 第 11 章 |
| **spring-tx** | **事务抽象**、**PlatformTransactionManager**、**声明式事务**基础设施。 | 第 9、19、38 章 |
| **spring-web** | **Web 根模块**：HTTP 抽象、客户端 **RestTemplate/WebClient** 基础、通用过滤器模型等。 | 第 5、6、29 章 |
| **spring-webflux** | **响应式 Web**：**WebFlux**、**RouterFunction**、**Reactive Streams**。 | 第 31 章 |
| **spring-webmvc** | **Servlet 栈 MVC**：**`DispatcherServlet`**、**`@Controller`**、视图解析与拦截器。 | 第 5、7、29 章 |
| **spring-websocket** | **Servlet 环境 WebSocket**：**握手**、**SockJS**、与 **Messaging** 组合 **STOMP**。 | **第 32 章** |

### 3.1 自测：依赖与目录能否对上？

在业务 `pom.xml` 中若只写：

```xml
<dependency>
  <groupId>org.springframework</groupId>
  <artifactId>spring-context</artifactId>
</dependency>
```

**不会**自动带上 **`spring-webmvc`**——Web 能力需显式增加 **`spring-webmvc`** 或 **Boot 的 `spring-boot-starter-web`**。这与上表「**分层 jar**」一致，也是阅读源码时**分模块打开**的依据。

---

## 4 项目总结

### 优点 & 缺点

| | **按仓库模块学习** | **按专栏主题学习** |
|---|-------------------|-------------------|
| **优点** | 与 **源码/Javadoc/Maven 坐标**一一对应，查 issue 快 | 按 **业务链路**递进，上手快 |
| **缺点** | 容易陷入 **Gradle 构建细节**与**非业务目录** | 需 **本章总表**对齐「缺专章模块」 |

### 适用场景

- **贡献源码 / 提 PR**：优先熟悉 **`spring-*` 边界**与本表。  
- **日常业务开发**：以 **Boot Starter + 本专栏章节**为主，**BOM** 管版本。  
- **性能调优 / 启动优化**：关注 **`spring-context-indexer`**、**懒加载**（第 27 章）、**AOP 切点**（第 20 章）。

### 注意事项

- **`framework-docs` / `integration-tests` / `gradle`**：不等于生产依赖，**不要**抄进业务 BOM。  
- **`spring-instrument`**：与 **链路追踪 Agent** 相关，变更需慎之又慎。  
- **WebSocket / OXM / SpEL**：见 **第 17、32、49 章**；更细行为仍以 **Reference** 与 **模块 Javadoc** 为准。

### 常见踩坑经验

1. **在 `spring-web` 里找 `DispatcherServlet`**：它在 **`spring-webmvc`**。  
2. **WebFlux 项目仍引 `spring-webmvc`**：栈混用，**依赖与线程模型**易失控。  
3. **忽略 BOM**：多模块工程**子依赖版本漂移**，排查成本指数上升。

---

## 思考题

1. 你的业务若 **XML 与 JSON 并存**，会如何在 **`spring-oxm`** 与 **Jackson** 之间划边界？  
2. 若启动扫描过慢，你会如何验证 **`spring-context-indexer`** 是否生效（看生成文件与耗时）？

---

## 推广协作提示

| 角色 | 建议 |
|------|------|
| **架构师** | 将 **BOM/平台** 纳入公司父 POM；对齐 **Servlet vs Reactive** 栈选型。 |
| **新人** | 先读本表，再打开 **第 1、5、9、10 章**，最后按需点进 **`spring-*` 源码**。 |

**与全专栏关系**：全书 **50 章** 按 **基础篇（第 1–18 章）→ 中级篇（第 19–35 章）→ 高级篇（第 36–50 章）** 递进；本章为 **仓库模块总览**；**BOM、源码构建、集成测、Indexer、SpEL、Instrument、OXM、WebSocket、AspectJ** 等见 **第 15、44–50、17、32** 章等对应专章。
