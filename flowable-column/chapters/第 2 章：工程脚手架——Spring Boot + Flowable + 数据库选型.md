# 第 2 章：工程脚手架——Spring Boot + Flowable + 数据库选型

> 版本：矩阵 A，JDK 17+，Spring Boot 3.2.x/3.3.x，Flowable 7.x。数据库示例以 H2 本地与 PostgreSQL 说明并行。

---

## 1）项目背景

星云科技的采购流程要从 PPT 走向可运行服务。立项会上产品提出：「我们要一个能跑起来的最小后端，开发能本地一键起，测试能连 H2 做自动化，预发用 PostgreSQL。」架构师强调：**工程骨架一次搭对**，后面几十章都在同一约定上叠流程。

本章技术对象：`ProcessEngine` 的启动方式、Flowable 与 Spring 的集成方式、数据库自动建表策略与配置项含义，以及「为什么不要混用多套引擎配置」。

---

## 2）项目设计（小胖 × 小白 × 大师）

> **角色分工**：**小胖**（爱吃爱玩、不求甚解）用生活化、口语化的方式把话题「开球」，先把问题问出来；**小白**（喜静、喜深入）负责追问原理、边界、风险与可比方案；**大师**（资深技术 Leader）把业务约束与技术选型说透，善打比方、由浅入深。

**小胖**：直接把 Flowable jar 丢进老项目行不？

**大师**：行，但你会得到一团手动 `new StandaloneProcessEngineConfiguration()` 和 Spring 管理Bean的打架。**推荐**用官方 **Spring Boot Starter**，由自动配置创建 `ProcessEngine` Bean，事务走 Spring，`DataSource` 共用或隔离都可，但要有意识。

**小白**：数据库用 H2 还是 MySQL？

**大师**：**本地与 CI 用 H2** 最省事；**联调与生产倾向 PostgreSQL 或 MySQL 8**。关键是**方言与驱动**别配错。H2 注意 Mode 与大小写敏感问题，别在生产用 file H2 冒充关系库。

**小胖**：表是自动建吗？

**大师**：开发期常用 `flowable.database-schema-update=true`（或等价配置）**自动建/更新**。生产要有**显式脚本与变更评审**，别指望「自动」背锅。

**小白**：REST 要不要一开始就开？

**大师**：看团队。前后端分离且要 Mock 联调，可开 `flowable-spring-boot-starter-rest` 或单独部署 REST 应用。安全要单独做（OAuth2、网关），别裸奔。

**小胖**：我们需要 Camunda 那种 Cockpit 吗？

**大师**：Flowable 有 UI（Modeler/Admin/Task/App），本章只管**引擎嵌入**。运维可视化选商业或自研；开发期 Modeler 够用。

**小白**：多数据源怎么办？

**大师**：可以，但**流程库独立一个 DataSource** 最清晰。共用也行，要理清事务边界（见第 23 章）。

**小胖**：JDK 8 还能用吗？

**大师**：能，但走矩阵 B。新专栏默认矩阵 A，省得和 Jakarta EE 迁移打架。

**小白**：日志级别怎么打？

**大师**：`org.flowable` 默认别全 TRACE，SQL 日志用 datasource 代理或慢查询监控，别把生产日志盘打爆。

**小胖**：那监控呢？

**大师**：至少三件事：**作业队列深度**、**数据库连接池等待**、**流程 API 调用的耗时分布**。引擎本身不是孤岛，要和 Spring MVC、数据源、线程池指标一起看。

**小白**：本地开发每次重启都重建表，数据没了很烦。

**大师**：两种取向：要持久就切 file H2 或本地 PG，要极致清爽就用内存 H2。团队统一一种，别「有人能复现、有人不能」。

**小胖**：Flowable 和 JPA/Hibernate 混一起会不会乱？

**大师**：能混，但要清楚**谁管哪些表**。引擎表由 Flowable 管理；业务实体走 JPA。别在同一事务里既手动改引擎表又用原生 SQL 拍脑袋。

---

### 一页纸决策表

| 输入 | 输出 | 适用场景 | 不适用/慎用 | 与后续章节关系 |
|------|------|----------|-------------|----------------|
| Spring Boot 单体或内部微服务 | 可注入的 `ProcessEngine`、自动建表、可切换的 DB | 团队已统一 Spring 技术栈 | 非 JVM 栈需 REST/消息集成而非嵌入式 | 第 3 章首次部署；第 11 章 REST |

---

## 3）项目实战（主代码片段）

### 3.1 Maven 依赖（父 POM 节选）

```xml
<properties>
  <java.version>17</java.version>
  <spring-boot.version>3.3.6</spring-boot.version>
</properties>

<dependencyManagement>
  <dependencies>
    <dependency>
      <groupId>org.springframework.boot</groupId>
      <artifactId>spring-boot-dependencies</artifactId>
      <version>${spring-boot.version}</version>
      <type>pom</type>
      <scope>import</scope>
    </dependency>
  </dependencies>
</dependencyManagement>

<dependencies>
  <dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-web</artifactId>
  </dependency>
  <dependency>
    <groupId>org.flowable</groupId>
    <artifactId>flowable-spring-boot-starter</artifactId>
    <version>${flowable.version}</version>
  </dependency>
  <dependency>
    <groupId>com.h2database</groupId>
    <artifactId>h2</artifactId>
    <scope>runtime</scope>
  </dependency>
  <dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-test</artifactId>
    <scope>test</scope>
  </dependency>
</dependencies>
```

> `${flowable.version}` 请替换为与 Spring Boot 3 兼容的发行说明中推荐版本，并在团队 BOM 中集中锁定。

### 3.2 application.yaml（开发默认）

```yaml
spring:
  datasource:
    url: jdbc:h2:mem:flowable-column;DB_CLOSE_DELAY=-1;MODE=PostgreSQL
    driver-class-name: org.h2.Driver
    username: sa
    password:

flowable:
  database-schema-update: true
  async-executor-activate: true
```

### 3.3 启动类（最小）

```java
package com.neuratech.column;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

@SpringBootApplication
public class ColumnApplication {
    public static void main(String[] args) {
        SpringApplication.run(ColumnApplication.class, args);
    }
}
```

### 3.4 验证引擎已注册（测试）

```java
package com.neuratech.column;

import org.flowable.engine.ProcessEngine;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

import static org.assertj.core.api.Assertions.assertThat;

@SpringBootTest
class EngineSmokeTest {

    @Autowired
    private ProcessEngine processEngine;

    @Test
    void processEngineBeanShouldExist() {
        assertThat(processEngine).isNotNull();
        assertThat(processEngine.getName()).isNotBlank();
    }
}
```

### 3.5 curl（若已引入 REST starter）

未启用 REST 时跳过。启用后可用健康检查确认进程：

```bash
curl -s http://localhost:8080/actuator/health
```

---

## 4）项目总结

### 优点

- Starter 把**引擎生命周期**与 **Spring** 绑定，减少样板代码。
- 与 Spring 事务统一，利于业务服务与流程编排同库写入。
- H2 让 CI **快速、可重复**。

### 缺点

- 自动配置「魔法」多，出问题时需会读条件装配与属性前缀 `flowable.*`。
- 默认开启异步执行器时，本地调试要理解**异步线程**与主线程断点差异。

### 典型使用场景

- 企业内部审批、集成平台中的流程编排服务。
- 与现有 Spring 单体或模块化单体共存。

### 注意事项

- **同一环境**固定 Flowable 小版本，避免依赖传递冲突。
- 生产关闭过度宽松的 `database-schema-update`，改为受控迁移。

### 反例

拷贝网上「Activiti 5」古早配置到 Spring Boot 3 项目，启动报类找不到或 javax/jakarta 混用。**纠正**：按当前 Flowable 官方 Spring Boot 文档选 starter 与版本组合。

### 常见踩坑

| 现象 | 根因 | 处理 |
|------|------|------|
| 启动报 Liquibase/MyBatis 相关错 | 依赖冲突或版本不匹配 | 用 `mvn dependency:tree` 查冲突，对齐 BOM |
| H2 与生产 SQL 行为不一致 | MODE、关键字、大小写 | CI 增加一次 PG 容器集成测试 |
| 表建了但连接错库 | 多 Profile 切换失误 | `spring.profiles.active` 显式化，配置中心覆盖可审计 |
| 本地快、线上慢 | 异步线程池/数据源池未调 | 按第 17、25、27 章调参与监控 |

### 给测试的一句话

**同一套 `application-test.yaml`** 固定随机端口与内存 H2，让集成测试可并行、无脏数据。

### 给运维的一句话

把 **`flowable.async`-相关线程名前缀** 与 **连接池指标** 接进监控；引擎升级前先在预发跑**迁移脚本对比**。

---

### 附：PostgreSQL 本地 Profile 思路（扩写时可展开）

生产形态下建议尽早引入 **Profile：`pg`**：`spring.datasource.url` 指向开发组共用实例或 Testcontainers 中单次 PG。H2 仍保留给**纯单元/快速失败**的测试。两种库在**排序规则、布尔类型、JSON 函数**上存在差异：**关键业务回归**跑在 PG 上，避免上线前一刻才炸。

---

*（成稿时可补充：团队选定 Flowable 精确版本号、Dockerfile 片段、`application-{env}.yaml` 完整样本与依赖 BOM 坐标。）*
