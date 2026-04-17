# 第 10 章：Spring Boot 快速起步——依赖管理与自动配置初探

> **业务线**：电商 / 订单履约微服务（拟真场景）。本章可独立阅读；与全书案例弱关联。

> **篇章**：基础篇（全书第 1–18 章；核心概念、单机、简单 API、初级实战）

## 上一章思考题回顾

1. **`spring.factories`（Boot 2.x）** 与 **`META-INF/spring/org.springframework.boot.autoconfigure.AutoConfiguration.imports`（Boot 3）**：后者为 **显式 imports 列表**，替代大量 `spring.factories` 中的 `EnableAutoConfiguration`，**更清晰**。  
2. **`@SpringBootTest`** 加载**完整**应用上下文；**`@DataJpaTest`** 是**切片测试**，只加载 JPA 相关，速度快。

---

## 1 项目背景

团队要从「手工拼 XML」迁到 **Boot**：**起步依赖**一键引入 Web/JDBC/Actuator，**约定大于配置**。若不理解 **自动配置** 边界，会出现「加了个依赖行为就变了」的困惑。

**痛点**：  
- 版本冲突（**BOM** 解决；**Maven/Gradle 对齐与 `dependency:tree` 实操**见 **第 15 章**）。  
- **排除**某个自动配置的需求（如安全默认拦截）。  
- **main 方法**与 **`SpringApplication.run`** 启动流程不熟。

---

## 2 项目设计（剧本式对话）

**角色**：小胖 / 小白 / 大师。  
**结构**：澄清 Boot 与 Framework 边界 → 自动配置如何「少写 XML」→ 如何排错。

**小胖**：Boot 是不是把 Spring 包了一层？我感觉就是魔法。

**大师**：Boot 提供 **starter**、**自动配置**、**可执行 jar**、**Actuator**；核心仍是 Spring Framework。魔法=**条件装配 + 约定**，不是黑盒；`debug=true` 能把「为什么加载了 DataSource」讲清楚。

**技术映射**：**@SpringBootApplication** = `@Configuration` + `@EnableAutoConfiguration` + `@ComponentScan`。

**小白**：如何关闭 DataSource 自动配置？

**大师**：`@SpringBootApplication(exclude = {DataSourceAutoConfiguration.class})` 或配置文件 `spring.autoconfigure.exclude`。

**技术映射**：**自动配置类**都是普通 `@Configuration`，只是被 **imports** 机制批量装载。

**小胖**：我依赖加多了，启动变慢，算不算 Boot 的锅？

**小白**：算**选择的代价**：starter 引入传递依赖；要用 **dependency:tree** 看引入了谁，再按需 `exclude`。

**大师**：像**自助餐**：拿得多当然沉；生产应用要做 **starter 清单评审** 与 **懒加载/按需启用**。

---

## 3 项目实战

### 3.1 环境准备

| 项 | 说明 |
|----|------|
| 生成 | [start.spring.io](https://start.spring.io)：**Java 17 + Maven + Spring Boot 3.2+** |
| 依赖 | 先选 **Spring Web**（后续章节再叠加 JDBC 等） |

**目录结构（核心）**

```text
src/main/java/com/example/OrderApplication.java
src/main/resources/application.properties
src/test/java/com/example/OrderApplicationTests.java
```

### 3.2 分步实现

**入口**

```java
package com.example;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

@SpringBootApplication
public class OrderApplication {
    public static void main(String[] args) {
        SpringApplication.run(OrderApplication.class, args);
    }
}
```

**查看自动配置报告（调试）**

```yaml
debug: true
```

启动日志输出 **CONDITIONS EVALUATION REPORT**。

**运行结果（文字描述）**：日志中出现 `Positive matches:` / `Negative matches:`，可定位 **哪些自动配置生效**（例如 `ServletWebServerFactoryAutoConfiguration`）。

**步骤 3 — 目标（命令行覆盖）**

```bash
java -jar target/app.jar --spring.main.banner-mode=off --logging.level.org.springframework.boot.autoconfigure=INFO
```

观察启动参数如何进入 **Environment**（与第 4 章呼应）。

### 3.3 完整代码清单与仓库

`chapter10-boot`。

### 3.4 测试验证

`@SpringBootTest` 的 `contextLoads()`；再加一条：断言 **Environment** 里 `spring.application.name`（若配置）。

**命令**：`mvn -q test`。

**可能遇到的坑**

| 现象 | 原因 | 处理 |
|------|------|------|
| 端口占用 | 8080 被占用 | `server.port=0` 随机端口 |
| 自动配置不符合预期 | 条件未满足 | 看 `CONDITIONS` 报告 |
| 测试慢 | 全量上下文 | 后续用切片测试（第 11 章） |

---

## 4 项目总结

### 优点与缺点

| 维度 | Boot | 裸 Framework |
|------|------|----------------|
| 上手速度 | 快 | 慢 |
| 透明度 | 需学自动配置 | 全手动清晰 |

### 常见踩坑经验

1. **同名 Bean** 与自动配置冲突。  
2. **Profile** 未激活导致 Bean 缺失。  
3. **DevTools** 类加载器双份问题。

---

## 思考题

1. `@MockBean` 与 `@SpyBean` 区别？（第 11 章。）  
2. `@Async` 默认线程池？（第 12 章。）

---

## 推广协作提示

| 角色 | 建议 |
|------|------|
| **运维** | 用 `spring-boot-maven-plugin` 打可执行 jar。 |

**下一章预告**：JUnit 5、`@SpringBootTest`、`Testcontainers` 初识。
