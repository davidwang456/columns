# 第 04 章：配置体系——Properties、YAML 与多环境 Profile

> **业务线**：电商 / 订单履约微服务（拟真场景）。本章可独立阅读；与全书案例弱关联。

## 上一章思考题回顾

1. **@Configuration 增强**：`@Configuration`（默认 `proxyBeanMethods=true`）中 `@Bean` 方法互相调用会走**代理**，保证单例语义；`@Configuration(proxyBeanMethods=false)` 为 **lite** 模式，类似纯 `@Bean` 工厂，**不**保证同类内多次调用返回同一实例。  
2. **配置加载顺序**：`application.yml` 为基础；`spring.profiles.active` 激活后合并 `application-{profile}.yml`；`spring.config.import` 可引入额外文件或配置中心，**顺序**以官方文档为准（一般 import 在指定阶段解析）。本章用 **Spring Boot** 演示最贴近生产（与第 10 章衔接）。

---

## 1 项目背景

「鲜速达」在双环境并行：**开发**连本地 Redis、**预发**连集群，**生产**开启严格 TLS。若把连接串写死在代码里，**泄密**与**误连**风险高；若散落在 shell 变量，又难以**审计**与**回滚**。

**痛点**：  
- 配置键名不一致（`redis.host` vs `spring.data.redis.host`）。  
- Profile 未对齐导致「本地能跑、生产缺配置」。  
- YAML 缩进错误导致**整段解析失败**。

---

## 2 项目设计（剧本式对话）

**角色**：小胖 / 小白 / 大师。  
**结构**：从「偷懒写法」切入 → 追问可运维性 → 给出 Spring 的外部化模型。

**小胖**：为啥不直接 `System.getenv()`？多简单，运维在 K8s 里配一下不就完了？

**大师**：环境变量适合**部署时注入**；**YAML** 适合**结构化默认值 + 分组**，还能进 Git 做 **Code Review**（脱敏后）。Spring 把**外部化配置**统一抽象成 `Environment`，**优先级**清晰：命令行 > 环境变量 > 配置文件。

**技术映射**：**PropertySource** 链 + **Profile** 条件。

**小白**：那 `.properties` 和 `.yml` 哪个更「正统」？

**大师**：Boot 社区更常见 **YAML** 写分层配置；**properties** 在简单场景与某些遗留系统仍大量存在。别在团队里搞「两种格式混在一个文件里」的玄学。

**技术映射**：**ConfigData API**（Boot 2.4+）统一加载路径与优先级。

**小白**：`@Value` 和 `@ConfigurationProperties` 怎么选？

**大师**：**单点**、**简单类型**用 `@Value`；**一组**、**强类型绑定**用 `@ConfigurationProperties`（带校验、IDE 提示）。

**技术映射**：**类型安全绑定**降低拼写错误；`@ConfigurationProperties` 可配合 **`@Validated`** 做 **Bean Validation**。

**小胖**：`application-dev.yml` 会覆盖 `application.yml` 吗？会不会两个合并成「左右互搏」？

**大师**：**同键**后加载的覆盖先加载的；Profile 文件在**激活**后合并进 `Environment`。感觉像**先铺底味（公共配置）**，再按环境**加辣/减盐**。

**技术映射**：**spring.profiles.active** 与 **spring.profiles.include** 可组合；注意 **include** 引入的 profile 也会参与覆盖链。

**小白**：敏感信息放 YAML 里安全吗？

**大师**：**不进 Git** 是底线：用 **`${VAULT_URI}`**、**K8s Secret**、**CI 注入**；本地开发可用 **`.env` + spring-dotenv** 或 IDE Run Configuration，别把生产密钥贴进专栏截图。

**技术映射**：**spring.config.import** 可拉取 **vault://**、**configtree:**（K8s 挂载目录）等。

---

## 3 项目实战

本章给一条「能跑、能测、能改 Profile」的最小闭环；与第 10 章 Boot 深入互为前后篇。

### 3.1 环境准备

- Spring Boot 3.2.x（依赖 BOM）  
- Java 17  

```xml
<parent>
  <groupId>org.springframework.boot</groupId>
  <artifactId>spring-boot-starter-parent</artifactId>
  <version>3.2.5</version>
</parent>
<dependencies>
  <dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter</artifactId>
  </dependency>
  <dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-configuration-processor</artifactId>
    <optional>true</optional>
  </dependency>
</dependencies>
```

### 3.2 分步实现

**`src/main/resources/application.yml`**

```yaml
spring:
  application:
    name: order-service
app:
  inventory:
    timeout-ms: 1000
```

**`application-dev.yml`**

```yaml
app:
  inventory:
    timeout-ms: 3000
```

**配置类**

```java
package com.example.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "app.inventory")
public record InventoryProps(int timeoutMs) { }
```

**启动类（节选）**

```java
package com.example;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.ConfigurationPropertiesScan;

@SpringBootApplication
@ConfigurationPropertiesScan
public class ConfigDemoApplication {
    public static void main(String[] args) {
        SpringApplication.run(ConfigDemoApplication.class, args);
    }
}
```

**步骤 — 验证绑定**：新增一个 `Runner` 打印 `InventoryProps.timeoutMs()`，分别用 **`dev`** 与 **默认** profile 启动。

**启动命令（示例）**

```bash
mvn -q -DskipTests spring-boot:run -Dspring-boot.run.arguments=--spring.profiles.active=dev
```

**运行结果（文字描述）**：控制台应打印 `timeoutMs=3000`（激活 `dev` 时）；不加 profile 时为 `1000`。

**可能遇到的坑**

| 现象 | 原因 | 处理 |
|------|------|------|
| 绑定失败 | `prefix`、kebab-case 与 Java 字段映射不一致 | 用 relaxed binding 规则自查 |
| Profile 未生效 | `spring.profiles.active` 拼写错误或放在错误文件 | 用 `debug=true` 看 **Condition 报告** |
| IDE 运行与 jar 运行不一致 | Working Directory 不同 | 统一用 `src/main/resources` 为 classpath 根 |

### 3.3 完整代码清单与仓库

`chapter04-config`。

### 3.4 测试验证

`@SpringBootTest` + `@ActiveProfiles("dev")` 断言 `InventoryProps`；或 `@DynamicPropertySource` 动态注入：

```java
@DynamicPropertySource
static void props(DynamicPropertyRegistry r) {
    r.add("app.inventory.timeout-ms", () -> "5000");
}
```

**命令**：`mvn -q test`。

---

## 4 项目总结

### 优点与缺点

| 维度 | YAML + Profile | 纯 env |
|------|----------------|--------|
| 可读性 | 结构化好 | 扁平 |
| 风险 | 缩进错误 | 键名分散 |

### 适用场景

1. 多环境部署。  
2. 与 K8s ConfigMap/Secret 对接。  
3. 团队统一配置键规范。  

### 常见踩坑经验

1. **敏感信息进 Git** → 用 Secret 外部注入。  
2. **大小写**与 relaxed binding 不一致。  
3. **多 Profile 叠加**顺序误解。

---

## 思考题

1. `@PropertySource` 能否加载 YAML？（需额外处理器或 Boot 机制。）  
2. Spring MVC 中 `@Controller` 与 `@RestController` 区别？（第 05 章。）

---

## 推广协作提示

| 角色 | 建议 |
|------|------|
| **运维** | 用 `SPRING_PROFILES_ACTIVE` 控制环境。 |
| **测试** | 用 `test` profile 隔离外部依赖。 |

**下一章预告**：`DispatcherServlet`、映射、视图与异常处理。
