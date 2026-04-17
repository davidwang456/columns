# 第 077 章：spring-boot-graphql-test —— GraphQL 测试

> 对应模块：`spring-boot-graphql-test`。本章定位为**测试基础设施与切片实践**：与「GraphQL 测试」配套的主线模块章节共用业务故事，此处侧重 `@…Test`、Mock、Testcontainers 与 CI 中的稳定复现。

---

## 1 项目背景

团队在联调「GraphQL 测试」相关能力时，最怕**本地能通过、CI 偶发失败**：数据库版本漂移、嵌入式中间件与生产不一致、切片上下文漏载 Security/Web 配置。`{slug}` 模块把测试侧自动配置与可选依赖收敛到可重复的组合里。

若没有专用测试模块与约定，测试代码会复制粘贴 `@Import` 与 `MockBean`，维护成本逼近业务代码。结合本仓库 `module/spring-boot-graphql-test` 的 `build.gradle`，可以把**最小失败用例**固定成团队模板。

```mermaid
flowchart LR
  boot[SpringBoot应用] --> mod["spring-boot-graphql-test"]
  mod --> ext[外部依赖或运行时]
```

**小结：** 没有 `spring-boot` 对该能力的自动装配与属性命名空间时，团队要在**依赖对齐**、**Bean 生命周期**与**运维接口**上重复投入；引入 `spring-boot-graphql-test` 后，可以把讨论焦点收束到业务约束与 SLA。

---

## 2 项目设计（剧本式交锋对话）

**场景：** 测试架构周会，议题是「如何把 `GraphQL 测试` 测得又快又真」。

**小胖：** 不就写个 `@Test` 吗？为啥还要单独一个模块？跟多打一份外卖有啥区别？

**大师：** 单元测试像「试吃一口」；切片测试像「同一厨房出餐流程」——只启动 MVC 或 JPA 子上下文，速度才上得来。  
**技术映射：** `spring-boot-graphql-test` 提供测试侧自动配置与 Testcontainers 可选集成。

**小白：** 那如果我要测 Security 呢？只开 `@WebMvcTest` 会不会缺过滤器链？

**大师：** 用 `@Import` / `@AutoConfigureMockMvc(addFilters = …)` 或改用 `@SpringBootTest` 分层；关键是**声明你依赖的切片**。  
**技术映射：** 测试上下文是**显式契约**，不是全家桶。

**小胖：** CI 里 Docker 起不来咋办？

**大师：** 用 Testcontainers 的 Ryuk 与重用策略，或降级嵌入式（H2/embedded-kafka 等）并标明**环境差异风险**。  
**技术映射：** 测试金字塔底座要**可移植**。

**小白：** 和上一章业务代码重复叙事怎么办？

**大师：** 业务故事共用一条线，本章只换镜头：**Given/When/Then** 与失败注入。  
**技术映射：** 文档结构遵循专栏计划——主模块讲能力，`*-test` 讲验证。

---

## 3 项目实战

### 环境准备

- JDK 17+，Spring Boot 3.x（与当前仓库 `spring-boot-main` 版本族一致）。
- 构建：Maven 或 Gradle；依赖以官方 **Starter** 或模块文档为准，源码对照 `spring-boot-main/module/spring-boot-graphql-test/build.gradle`。

**Maven 依赖示例：**

```xml
<dependency>
  <groupId>org.springframework.boot</groupId>
  <artifactId>spring-boot-starter-graphql-test</artifactId>
</dependency>
```

**`application.yml` 片段：**

```yaml
spring:
  application:
    name: demo-graphql-test
# 测试常用：随机端口、简化日志
server:
  port: 0
logging:
  level:
    org.springframework: INFO
```

### 分步实现

**步骤 1 — 目标：** 创建可启动应用骨架。

```java
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

@SpringBootApplication
public class DemoApplication {
  public static void main(String[] args) {
    SpringApplication.run(DemoApplication.class, args);
  }
}
```

**运行结果（文字描述）：** 控制台出现 Spring Boot Banner，`Started DemoApplication` 表示上下文就绪；若缺中间件或配置，异常信息应指向具体 `*Properties`。

**可能遇到的坑：** 类路径上同时存在互斥实现（例如两个 Web 引擎）导致条件装配失败——使用 `spring.autoconfigure.exclude` 精确排除。

---

**步骤 2 — 目标：** 编写与「GraphQL 测试」相关的最小业务或基础设施代码。

```java
// 示例：REST 入口（按模块替换为 RouterFunction / Controller）
// @RestController @RequestMapping("/api") class DemoController {}
```

**可能遇到的坑：** Profile 未激活、或测试与主应用包扫描路径不一致导致 Bean 未注册。

---

**步骤 3 — 目标：** 验证（HTTP / 消息 / 批任务视领域选择）。

```bash
# Web/运维类：健康检查
curl -s http://localhost:8080/actuator/health

# 或执行测试
./mvnw -q test
```

### 完整代码清单

建议新建示例工程 `demo-graphql-test`，附 `README` 说明外部依赖（Docker Compose / 本地中间件）。**仓库占位：** `https://example.com/demo/spring-boot-graphql-test.git`

### 测试验证

- **切片测试：** 按领域选用 `@WebMvcTest`、`@DataJpaTest`、`@JsonTest` 等；`*-test` 模块章节优先对齐本仓库测试基类。
- **集成测试：** `@SpringBootTest` + Testcontainers（模块已可选依赖时常用）。

---

## 4 项目总结

### 优点与缺点

| 优点 | 缺点 |
|------|------|
| 与 Spring Boot BOM 对齐，降低依赖地狱 | 默认值未必覆盖极端吞吐或合规要求 |
| 自动配置缩短从 0 到可运行的时间 | 需要团队规范 exclude/覆盖策略 |
| 与 Actuator/Micrometer/Security 等同栈协同 | 大版本升级需阅读发行说明与迁移工具 |

### 适用场景与不适用场景

- **适用：** 需要与 Spring 生态一致默认、并希望缩短联调周期的服务；已有 Spring Boot 基线的团队。
- **不适用：** 目标运行环境禁止引入相关依赖；或已有非 Spring 技术栈且迁移成本高于收益。

### 注意事项

- 对照 `spring-boot-main/module/spring-boot-graphql-test/README.adoc`（若存在）与 `*Properties`。
- 生产环境避免开启调试级日志；密钥走密钥管理而非 YAML 明文。

### 常见踩坑经验（根因分析）

1. **配置写了不生效：** relaxed binding 与 `{prefix}` 层级写错——根因是属性元数据与 YAML 结构不一致。
2. **本地与 CI 行为不一致：** 环境变量/Profile 未对齐——根因是配置来源未收口到配置中心或 `.env` 约定。
3. **与其它自动配置冲突：** 两个 `DataSource` 或两套 Web 栈并存——根因是条件装配边界未用 `@ConditionalOn*` 或 Profile 切开。

### 思考题

1. 若要在不修改 `spring-boot-graphql-test` 自带 `AutoConfiguration` 的情况下替换其中一个 Bean，你会用 `@Bean @Primary`、`@Qualifier` 还是 `BeanDefinitionRegistryPostProcessor`？各自适用什么顺序风险？
2. 与「GraphQL 测试」最相关的生产指标与健康检查项是什么？如何在预发用 Actuator 与 Micrometer 验证？

**参考答案：** 见 [附录：思考题参考答案](../appendix/thinking-answers.md)（可按序号 `077` 维护）。

### 推广计划提示

- **开发：** 与架构组约定本模块的覆盖/排除策略；Code Review 检查是否误用默认线程池与超时。
- **测试：** 固化切片模板；对 `*-test` 模块与主模块章节交叉评审，避免重复长文。
- **运维：** 将 `management.*`、`spring.*` 关键项纳入配置审计与告警基线。

---

*本章结构与篇幅对齐专栏模板 [template.md](../template.md)；深度参照试点 [第 027 章：spring-boot-data-jpa —— Spring Data JPA 与仓库.md](第 027 章：spring-boot-data-jpa —— Spring Data JPA 与仓库.md)、[第 054 章：spring-boot-webmvc —— Spring MVC 与 Boot.md](第 054 章：spring-boot-webmvc —— Spring MVC 与 Boot.md)、[第 112 章：spring-boot-actuator —— Actuator 端点与运维 API.md](第 112 章：spring-boot-actuator —— Actuator 端点与运维 API.md)。*
