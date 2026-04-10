# 第 1 章：为什么 Java 要上 Kubernetes 还要选 Quarkus？

## 0. 课程卡片

| 项目 | 说明 |
|------|------|
| **建议课时** | 45～60 分钟（含实验） |
| **学习目标** | 说清 K8s 对 Java 的约束；理解 Quarkus 的 *Container First* 与 *build time over runtime*；完成首个可运行工程 |
| **先修** | JDK 17+、已安装 Maven 或 Quarkus CLI、可选 Docker |
| **课堂材料** | 投影仪、统一 JDK 版本清单、内网 Maven 仓库（如有） |

---

## 1. 项目背景

企业应用从「少数长驻虚拟机」迁移到 **Kubernetes** 后，约束从「能跑」变成「**跑得省、起得快、死得明白**」：

- **弹性**：HPA 按 CPU/内存或自定义指标扩缩，Pod 会频繁创建与销毁。
- **调度**：集群调度器关心请求（requests）与限制（limits），Java 进程若启动慢，就绪探针窗口内起不来会被反复杀死。
- **可观测**：日志走 stdout、指标被 Prometheus 抓取、链路需要 traceId——平台假设应用是 **12-factor** 友好的。
- **成本**：同样 QPS，镜像体积、内存工作集、副本数直接进账单。

**Quarkus** 在本仓库根目录 `README.md` 中自述为 *Cloud Native, (Linux) Container First*，强调在**构建期**做更多工作以换取**更快启动**与更小运行时负担，并同时支持 **JVM 模式**与 **GraalVM Native Image**。本章用一次「从 0 到 dev 模式跑起来」的实验，让开发、运维、测试在同一事实基础上对话。

---

## 2. 项目设计：大师与小白的对话

**小白**：「我们 Spring Boot 用了很多年，业务也能上 K8s，为什么要谈 Quarkus？」

**大师**：「不是『不能上』，而是**边际成本**不同。K8s 假设实例可以频繁替换；若单次启动要几十秒、反射扫描一大堆、镜像层缓存差，你的发布窗口、HPA 反应、故障恢复都会被拖长。」

**测试**：「那对我们测试有什么要求？」

**大师**：「你们会更常遇到**多环境配置**、**探针与就绪**、**集成测试要不要起容器**。框架若把『环境』一等公民化（profile、SmallRye Config），测试脚本和流水线更好写。」

**运维**：「我关心三件事：镜像别太大、日志别乱打、健康检查路径稳定别老改。」

**大师**：「Quarkus 在生态上对齐 Micrometer、SmallRye Health、JSON 日志等，路径可通过配置固定。但**约定需要团队文档化**，不能每人一个自定义端口。」

**小白**：「Quarkus 比 Spring Boot『快』是指什么快？」

**大师**：「至少有三层：**开发反馈**（`quarkus:dev`）、**进程启动**（尤其 Native 场景）、**云账单隐含的时间**（同样副本数能否扛住突发）。不要混成一句口号。」

**架构师**：「我们老系统 millions 行代码，能一刀切吗？」

**大师**：「不能。本章是**动机与试点**；演进路线在后续『模块化单体 / 落地路线图』章展开。试点选边界清晰的新服务或读多写少的 API。」

**小白**：「Native 是不是必选项？」

**大师**：「**不是**。很多团队 JVM 模式 + 合理堆与容器 limit 已经满足 SLO。Native 是工具箱：冷启动极敏感或密度要求极高时再上，代价是构建时间与动态特性约束。」

---

## 3. 知识要点（板书）

1. **12-factor**：配置与代码分离；端口绑定；日志流；管理进程与业务进程同生命周期等。  
2. **build time over runtime**：扩展在构建期参与 augmentation，减少运行期反射与猜测。  
3. **双模式**：同一套业务代码，按构建参数选择 JVM jar 或 Native 可执行文件。  
4. **干系人语言**：开发谈扩展与 CDI，运维谈探针与 limit，测试谈 `@QuarkusTest` 与环境——后续章节逐章对齐。

---

## 4. 项目实战

### 4.1 方式 A：Quarkus CLI 创建（推荐课堂演示）

> **版本**：将下文 `3.19.2` 替换为培训统一采用的 Quarkus Platform 版本（与内网 BOM 一致）。

```bash
quarkus create app org.acme:getting-started \
  --extension=rest \
  --java-version=17
cd getting-started
./mvnw quarkus:dev
```

默认开发模式会监听变更；控制台出现 `Listening on: http://0.0.0.0:8080` 即表示成功。

### 4.2 方式 B：完整 `pom.xml`（便于离线或内训归档）

以下等价于 `quarkus create` 生成的 **Maven + JVM** 最小 Web 应用骨架（`artifactId` 可改为贵司规范）。

```xml
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 https://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>

  <groupId>org.acme</groupId>
  <artifactId>getting-started</artifactId>
  <version>1.0.0-SNAPSHOT</version>
  <packaging>jar</packaging>
  <name>getting-started</name>

  <properties>
    <maven.compiler.release>17</maven.compiler.release>
    <project.build.sourceEncoding>UTF-8</project.build.sourceEncoding>
    <quarkus.platform.version>3.19.2</quarkus.platform.version>
    <quarkus.platform.group-id>io.quarkus.platform</quarkus.platform.group-id>
    <quarkus.platform.artifact-id>quarkus-bom</quarkus.platform.artifact-id>
    <skipITs>true</skipITs>
  </properties>

  <dependencyManagement>
    <dependencies>
      <dependency>
        <groupId>${quarkus.platform.group-id}</groupId>
        <artifactId>${quarkus.platform.artifact-id}</artifactId>
        <version>${quarkus.platform.version}</version>
        <type>pom</type>
        <scope>import</scope>
      </dependency>
    </dependencies>
  </dependencyManagement>

  <dependencies>
    <dependency>
      <groupId>io.quarkus</groupId>
      <artifactId>quarkus-arc</artifactId>
    </dependency>
    <dependency>
      <groupId>io.quarkus</groupId>
      <artifactId>quarkus-rest</artifactId>
    </dependency>
    <dependency>
      <groupId>io.quarkus</groupId>
      <artifactId>quarkus-junit5</artifactId>
      <scope>test</scope>
    </dependency>
    <dependency>
      <groupId>io.rest-assured</groupId>
      <artifactId>rest-assured</artifactId>
      <scope>test</scope>
    </dependency>
  </dependencies>

  <build>
    <plugins>
      <plugin>
        <groupId>${quarkus.platform.group-id}</groupId>
        <artifactId>quarkus-maven-plugin</artifactId>
        <version>${quarkus.platform.version}</version>
        <extensions>true</extensions>
        <executions>
          <execution>
            <goals>
              <goal>build</goal>
              <goal>generate-code</goal>
              <goal>generate-code-tests</goal>
            </goals>
          </execution>
        </executions>
      </plugin>
      <plugin>
        <groupId>org.apache.maven.plugins</groupId>
        <artifactId>maven-compiler-plugin</artifactId>
        <version>3.14.0</version>
        <configuration>
          <parameters>true</parameters>
        </configuration>
      </plugin>
      <plugin>
        <groupId>org.apache.maven.plugins</groupId>
        <artifactId>maven-surefire-plugin</artifactId>
        <version>3.5.2</version>
        <configuration>
          <systemPropertyVariables>
            <java.util.logging.manager>org.jboss.logmanager.LogManager</java.util.logging.manager>
            <maven.home>${maven.home}</maven.home>
          </systemPropertyVariables>
        </configuration>
      </plugin>
    </plugins>
  </build>
</project>
```

### 4.3 示例资源类与配置

`src/main/java/org/acme/GreetingResource.java`：

```java
package org.acme;

import jakarta.ws.rs.GET;
import jakarta.ws.rs.Path;
import jakarta.ws.rs.Produces;
import jakarta.ws.rs.core.MediaType;

@Path("/hello")
public class GreetingResource {

    @GET
    @Produces(MediaType.TEXT_PLAIN)
    public String hello() {
        return "Hello from Quarkus";
    }
}
```

`src/main/resources/application.properties`（课堂可先留空或只加一行注释）：

```properties
# 培训第 1 章：后续章节将在此叠加 profile 与健康检查等配置
```

### 4.4 构建可运行 jar（非实验必须，讲师演示）

```bash
./mvnw package -DskipTests
java -jar target/quarkus-app/quarkus-run.jar
```

---

## 5. 课堂实验

### 实验 1：环境与首跑（必做，约 15 分钟）

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 学员执行 `java -version`，确认与培训要求一致（如 17+） | 输出版本无报错 |
| 2 | 使用 CLI 或解压讲师提供的 `getting-started.zip` | 工程目录结构完整，含 `mvnw` |
| 3 | 执行 `./mvnw quarkus:dev`（Windows：`mvnw.cmd quarkus:dev`） | 控制台无 ERROR，提示监听 8080 |
| 4 | 浏览器或 `curl http://localhost:8080/hello` | 返回 `Hello from Quarkus` 或项目模板等价字符串 |
| 5 | 修改 `GreetingResource` 返回字符串并保存 | 控制台出现热重载日志，刷新页面可见新内容 |

**验收标准**：全员完成步骤 4；至少一半学员完成步骤 5。

### 实验 2：观察启动日志（选做，约 10 分钟）

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 停止 dev，执行 `./mvnw package -DskipTests` 后 `java -jar target/quarkus-app/quarkus-run.jar` | 进程启动，接口可访问 |
| 2 | 记录从执行 jar 到首次成功响应的大致耗时 | 每人写在共享表格，供后续「Native / 调优」章对比 |

### 实验 3：角色换位思考（讨论，约 10 分钟）

分组回答：「若此服务部署在 K8s，**运维**要配哪三条 YAML 字段与此应用强相关？」（提示：端口、探针、资源——不要求写对语法，只要求说对关注点。）

**清理**：`Ctrl+C` 结束 dev 或 jar 进程；无需集群资源。

---

## 6. 项目总结

| 维度 | 说明 |
|------|------|
| **优点** | 与 12-factor、容器交付叙事一致；`quarkus:dev` 利于培训破冰；JVM / Native 双路径可选。 |
| **缺点** | 团队学习曲线；需从「Spring 习惯」迁移到扩展与 profile 思维。 |
| **适用场景** | 新服务、API、消费者；对冷启动敏感时可评估 Native。 |
| **注意事项** | 统一平台版本号；内网需提前校验 BOM 与插件可下载。 |
| **常见踩坑** | 把 Quarkus 当 Spring Boot 抄配置；JDK 版本不一致导致本地能跑、CI 失败。 |

**延伸阅读**：本仓库根目录 `README.md`；官方 <https://quarkus.io/guides/getting-started>。
