# 第 2 章：Quarkus 扩展（Extension）与 BOM

## 0. 课程卡片

| 项目 | 说明 |
|------|------|
| **建议课时** | 45～60 分钟（含实验） |
| **学习目标** | 区分「普通依赖」与 Quarkus **扩展**；掌握 **BOM** 导入与版本对齐；会用 CLI/Maven 增删扩展 |
| **先修** | 完成第 1 章或同等可运行工程 |
| **课堂材料** | 联网环境或离线扩展清单打印件 |

---

## 1. 项目背景

在 Quarkus 中，能力主要通过 **扩展（Extension）** 接入。扩展往往包含：

- **runtime**：运行时可用的 API 与实现；
- **deployment**：在 **Maven compile 阶段**参与 Quarkus 的 *augmentation*（生成字节码、资源、Native 元数据等）。

因此「引了一个 jar」在 Quarkus 里可能意味着构建期行为变化——这是与许多「仅运行时装配」框架的重要差异。  
**BOM（Bill of Materials）** 把一组扩展的**兼容版本**锁在一起，避免学员在培训现场陷入「NoClassDefFoundError 马拉松」。

---

## 2. 项目设计：大师与小白的对话

**小白**：「扩展和 Spring Boot Starter 是不是一回事？」

**大师**：「**形似神不似**。Starter 常常是依赖捆绑；Quarkus 扩展更强调**构建期处理器**参与。你看到的『魔法』很多在 `deployment` 模块里。」

**小白**：「那我能不能随便从 Maven Central 拉一个库用？」

**大师**：「可以，但要自己负责**反射、资源文件、Native 兼容性**。有官方扩展时优先用扩展。」

**架构师**：「我们内网要不要自建 BOM？」

**大师**：「可以基于 `quarkus-bom` 再包一层 **公司 BOM**，只暴露允许的扩展与版本；平台组在后续『编写扩展』章会展开。」

**运维**：「扩展选多了，镜像会不会胖？」

**大师**：「会。生产镜像应 **按需最小集**；培训工程可以宽一点，但要在文档里写清『生产裁剪清单』。」

**测试**：「扩展多了，测试变慢怎么办？」

**大师**：「单元测试不启容器；只有需要扩展行为时才 `@QuarkusTest`。第 11 章会系统化。」

**小白**：「我怎么查某个能力对应哪个扩展名？」

**大师**：「官网 Extensions 目录、`quarkus extension list`、或在工程里 `quarkus:add-extension`。养成查官方 guide 的习惯。」

---

## 3. 知识要点

- **Platform BOM**：`io.quarkus.platform:quarkus-bom` 与 `quarkus-maven-plugin` 版本对齐。  
- **不要手写碎片版本**：同一能力域的 artifact 版本应来自 BOM。  
- **扩展坐标**：一般为 `io.quarkus:quarkus-<name>`。  
- **本仓库**：扩展源码位于 `extensions/`（讲师现场可打开一个 `*Processor.java` 示意，无需通读）。

---

## 4. 项目实战

### 4.1 完整 `pom.xml`（多扩展示例）

在单模块教学中，可在第 1 章 `pom.xml` 基础上增加依赖；下例展示 **REST + JSON + Health** 的常见组合（版本号请与培训统一）。

```xml
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 https://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>

  <groupId>org.acme</groupId>
  <artifactId>extensions-lab</artifactId>
  <version>1.0.0-SNAPSHOT</version>
  <packaging>jar</packaging>

  <properties>
    <maven.compiler.release>17</maven.compiler.release>
    <project.build.sourceEncoding>UTF-8</project.build.sourceEncoding>
    <quarkus.platform.version>3.19.2</quarkus.platform.version>
  </properties>

  <dependencyManagement>
    <dependencies>
      <dependency>
        <groupId>io.quarkus.platform</groupId>
        <artifactId>quarkus-bom</artifactId>
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
      <artifactId>quarkus-rest-jackson</artifactId>
    </dependency>
    <dependency>
      <groupId>io.quarkus</groupId>
      <artifactId>quarkus-smallrye-health</artifactId>
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
        <groupId>io.quarkus.platform</groupId>
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
    </plugins>
  </build>
</project>
```

### 4.2 命令行增删扩展

```bash
# 列出可安装扩展（需网络或缓存）
./mvnw quarkus:add-extension -Dextensions=smallrye-openapi

# 或使用 Quarkus CLI
quarkus extension add smallrye-openapi
```

### 4.3 验证 JSON 序列化（证明扩展生效）

`src/main/java/org/acme/BookResource.java`：

```java
package org.acme;

import jakarta.ws.rs.GET;
import jakarta.ws.rs.Path;
import jakarta.ws.rs.Produces;
import jakarta.ws.rs.core.MediaType;

@Path("/books")
public class BookResource {

    public record Book(String title, int pages) {}

    @GET
    @Produces(MediaType.APPLICATION_JSON)
    public Book first() {
        return new Book("Quarkus in Action", 320);
    }
}
```

访问 `GET /books` 应返回 JSON（依赖 `quarkus-rest-jackson`）。

---

## 5. 课堂实验

### 实验 1：BOM 对齐校验（约 15 分钟）

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 故意在 `pom.xml` 中给某个 `io.quarkus` 依赖写**错误版本号**（与 BOM 不一致） | `mvn -q validate` 或编译可能失败或 Quarkus 插件报错 |
| 2 | 删除显式版本，交给 BOM | `./mvnw clean compile` 成功 |
| 3 | 执行 `./mvnw dependency:tree -Dincludes=io.quarkus` | 树中 Quarkus 组件版本一致 |

### 实验 2：添加 OpenAPI 扩展（约 15 分钟）

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 执行 `./mvnw quarkus:add-extension -Dextensions=smallrye-openapi` | `pom.xml` 出现新依赖 |
| 2 | `./mvnw quarkus:dev`，浏览器打开 `/q/swagger-ui`（若需配置见第 21 章） | 能看到 API 文档页 |
| 3 | 在组内讨论：该扩展主要影响 **构建期** 还是 **运行期**？ | 讲师揭晓：两者皆有，但以构建期注册能力为主 |

### 实验 3：「扩展选型」评审（约 10 分钟）

给一张假需求卡（「REST + DB + 缓存 + Kafka」），小组用 5 分钟写出**最小扩展列表**，讲师对照官方推荐组合点评。

**清理**：无；保留工程供后续章节使用。

---

## 6. 项目总结

| 维度 | 说明 |
|------|------|
| **优点** | 版本矩阵清晰；能力模块化；构建期优化可预期。 |
| **缺点** | 扩展组合错误时排障需看日志；第三方库需评估 Native。 |
| **适用场景** | 一切 Quarkus 项目的依赖治理。 |
| **注意事项** | 公司镜像源需预同步 BOM 指向的仓库。 |
| **常见踩坑** | 绕过 BOM 手写版本；把非扩展库当扩展用却不登记反射。 |

**延伸阅读**：官方 <https://quarkus.io/guides/extension-codestart>；本仓库 `extensions/`。
