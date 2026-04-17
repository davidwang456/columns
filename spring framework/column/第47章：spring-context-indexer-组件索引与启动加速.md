# 第 47 章：`spring-context-indexer`——组件索引与启动加速

> **业务线**：电商 / 订单履约微服务（拟真场景）。本章可独立阅读；与全书案例弱关联。  
> **篇章**：高级篇（全书第 36–50 章；源码、极端场景、扩展、SRE）

> **定位**：掌握 **`spring-context-indexer`** 注解处理器如何在编译期生成 **`META-INF/spring.components`**，使 **`ClassPathScanningCandidateComponentProvider`** 在启动时 **优先走索引**而非全盘扫描 classpath，从而 **缩短大型项目/测试套件**的启动时间；了解 **局限**（增量编译、条件注解、Native 等）。

## 上一章思考题回顾

1. **`integration-tests` vs `spring-test`**：前者是 **仓库中的 Gradle 子项目**，承载 **框架级集成用例**；**`spring-test`** 是 **可复用的测试支持 jar**（`TestContext` 等），业务 **`test` scope** 引用。  
2. **事务相关检索**：关键词如 **`@Transactional`**、**`rollback`**、**`PlatformTransactionManager`**，在 **`spring-tx` / `integration-tests`** 中组合搜索。

---

## 1 项目背景

「鲜速达」**单体**经过多年演进，**组件扫描**包路径较宽，**冷启动**在 **CI** 里成为瓶颈：每个 **`@SpringBootTest`** 都要拉起完整上下文。团队听说 **Spring** 提供 **「类路径组件索引」**，希望在 **不改业务注解习惯**的前提下 **降低扫描成本**。

**痛点**：

- **误以为索引「自动生效」**：必须 **编译期**引入 **processor** 并 **成功生成** `spring.components`。  
- **索引与条件 Bean**：**`@ConditionalOnClass`** 等 **运行时条件** 仍要评估，索引 **不是**万能。  
- **Native 镜像**：索引与 **GraalVM** 的 **静态分析**关系需与 **第 40 章** 对照，避免 **重复配置**认知混乱。

**痛点放大**：若 **仅**在 IDE 里「增量编译」失败写出 **空/旧索引**，可能出现 **Bean 找不到** 或 **幽灵 Bean**——需要明确 **clean 构建**与 **CI** 策略。

```mermaid
flowchart LR
  A[编译期 CandidateComponentsIndexer] --> B[META-INF/spring.components]
  B --> C[启动时读取索引]
  C --> D[减少 classpath 扫描]
```

---

## 2 项目设计（剧本式对话）

**角色**：小胖 / 小白 / 大师。  
**结构**：索引是什么 → 何时有效 → 与扫描关系。

**小胖**：我不加这个 jar，Spring 不就照样跑吗？

**大师**：对 **正确性**通常无影响；加 **indexer** 是为了 **性能**——**大 classpath**、**多 `@Component`** 时 **省扫描**。小项目收益 **不明显**，甚至可能 **编译期略慢**。

**技术映射**：**`CandidateComponentsIndexer`**（见 **`spring-context-indexer`** 模块）生成 **`Metadata`** 存于 **`META-INF/spring.components`**。

**小白**：和 **`spring.factories`** / **`AutoConfiguration.imports`** 有啥区别？

**大师**：**不同层**：**Boot 自动配置**走 **`META-INF/spring/`** 下 **imports**；**组件索引**服务的是 **core 容器**的 **类路径扫描**。**不要混为一谈**。

**技术映射**：**Indexer** ≈ **扫描的编译期缓存**；**自动配置** ≈ **Boot 扩展机制**。

**小胖**：我加了依赖咋没生成文件？

**大师**：**`spring-context-indexer`** 应以 **annotation processor** 方式参与编译；**Maven** 需 **compiler plugin** 配置 **`annotationProcessorPaths`** 或依赖 **`optional` processor**（视版本与文档）；生成后 **检查 `target/classes/META-INF/spring.components`**。

**技术映射**：**无文件 = 未运行 processor** 或 **未触发增量编译**。

---

## 3 项目实战

本章使用 **Maven + Java 17**，引入 **`spring-context`** 与 **`spring-context-indexer`**（**optional**，仅编译期）。

### 3.1 环境准备

| 项 | 说明 |
|----|------|
| JDK | 17+ |
| 构建 | Maven 3.9+ |
| 依赖 | `spring-context`、`spring-context-indexer`（processor） |

**`pom.xml`（节选）**

```xml
<properties>
  <java.version>17</java.version>
  <spring.version>6.1.14</spring.version>
</properties>

<dependencies>
  <dependency>
    <groupId>org.springframework</groupId>
    <artifactId>spring-context</artifactId>
    <version>${spring.version}</version>
  </dependency>
  <!-- 编译期索引：不要打进生产 fat jar 的可传递依赖时，请配合 optional + 打包插件排查 -->
  <dependency>
    <groupId>org.springframework</groupId>
    <artifactId>spring-context-indexer</artifactId>
    <version>${spring.version}</version>
    <optional>true</optional>
  </dependency>
</dependencies>
```

### 3.2 分步实现

**步骤 1 — 目标**：定义带 **`@Component`** 的类（放在 **较深包** 下以体现扫描成本，此处仅演示）。

```java
package com.example.indexer.demo;

import org.springframework.stereotype.Component;

@Component
public class IndexedDemoBean {
}
```

**步骤 2 — 目标**：配置 **`AnnotationConfigApplicationContext`**，**开启索引**（Spring 5.2+ 常用 **`setBeanNameGenerator`** 等与索引配合；**实际 API** 以当前版本 **Javadoc** 为准——关键是 **存在 `spring.components` 时容器会利用**）。

```java
package com.example.indexer.demo;

import org.springframework.context.annotation.AnnotationConfigApplicationContext;
import org.springframework.context.annotation.ComponentScan;
import org.springframework.context.annotation.Configuration;

@Configuration
@ComponentScan(basePackageClasses = IndexedDemoBean.class)
public class IndexerDemoApplication {
}

public class Main {
    public static void main(String[] args) {
        try (AnnotationConfigApplicationContext ctx =
                     new AnnotationConfigApplicationContext(IndexerDemoApplication.class)) {
            System.out.println(ctx.getBean(IndexedDemoBean.class));
        }
    }
}
```

**步骤 3 — 目标**：**全量编译**并检查生成文件。

```text
mvn -q clean compile
```

**期望**：在 **`target/classes/META-INF/`** 下出现 **`spring.components`**（二进制/自定义格式，**勿手改**）。

**步骤 4 — 目标**（验证）：用 **`jar tf`** 或资源管理器确认 **`spring.components`** 已 **打进 jar**（打包章节略）。

### 3.3 可能遇到的坑

| 现象 | 原因 | 处理 |
|------|------|------|
| **无 `spring.components`** | **processor 未运行** | 配置 **`maven-compiler-plugin`** 的 **annotationProcessorPaths** 指向 **`spring-context-indexer`** |
| **Bean 缺失** | **索引过期** 与源码不一致 | **`clean compile`** |
| **Boot 项目** | **父 POM 管理** 漏 processor | 使用 **Spring Boot 文档** 推荐配置 |

### 3.4 测试验证

**JUnit 5** 拉起上下文（见第 1 章 **`@SpringJUnitConfig`**），对比 **`clean`** 前后 **启动耗时**（需 **JFR** 或日志级 **粗略**对比，**非**严谨基准）。

---

## 4 项目总结

### 优点与缺点

| 维度 | 启用 Context Indexer | 默认扫描 |
|------|----------------------|----------|
| 启动（大项目） | **可能更快** | **更慢** |
| 构建 | **需 processor** | **简单** |
| 踩坑 | **索引过期** | **扫描边界**配置 |

### 适用场景

1. **大型单体**、**海量 `@Component`**。  
2. **测试套件**频繁冷启动。  
3. **多模块** classpath 极长。

### 注意事项

- **Spring Boot** 有 **官方文档专节**，请以 **当前 Boot 版本**为准配置 **Maven/Gradle**。  
- **条件注解**、**Profile** 行为 **不因索引而消失**。

### 常见踩坑经验

1. **现象**：生产缺 Bean，本地有。  
   **根因**：**索引未随发布物更新**或 **分支合并**漏 **`clean`**。  

2. **现象**：Native 镜像仍提示缺类。  
   **根因**：**索引 ≠ Native hint**（第 40 章）。  

---

## 思考题

1. **`META-INF/spring.components`** 与 **`@ComponentScan` 的 basePackages** 不一致时，容器行为以谁为准？（需结合 **CandidateComponentsProvider** 实现与 **回退扫描**逻辑查阅源码。）  
2. 你会如何把 **「索引存在」** 纳入 **CI** 门禁？（下一章：**SpEL** 与 **`spring-expression`**。）

---

## 推广协作提示

| 角色 | 建议 |
|------|------|
| **开发** | **大仓**优先试点 indexer；小服务 **不折腾**。 |
| **运维** | 关注 **镜像构建时间** 是否因 **processor** 增加。 |

**下一章预告**：**`spring-expression`（SpEL）**——**`@Value("#{...}")`** 与 **`ExpressionParser`**。
