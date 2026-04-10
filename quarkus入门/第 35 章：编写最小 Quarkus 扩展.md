# 第 35 章：编写最小 Quarkus 扩展

## 0. 课程卡片

| 项目 | 说明 |
|------|------|
| **建议课时** | 90～120 分钟 |
| **学习目标** | 理解 runtime + deployment 两模块；注册 `FeatureBuildItem`；发布到内网 Maven |
| **先修** | 第 2、26 章 |

---

## 1. 项目背景

企业横切能力（审计、租户上下文、统一错误体）复制三次应 **平台化**。最小扩展两模块：**deployment**（构建期）+ **runtime**（运行期）。

---

## 2. 项目设计：大师与小白的对话

**小白**：「公司 starter jar 不够吗？」

**大师**：「无构建期参与时 jar 够；需要 bytecode / 资源生成时上 **扩展**。」

**架构师**：「扩展里写业务吗？」

**大师**：「**禁止**。只放技术横切。」

**运维**：「扩展版本怎么跟 Quarkus 主版本？」

**大师**：「矩阵表；升级窗口一起测。」

---

## 3. 知识要点

- `quarkus-extension-maven-plugin`  
- `META-INF/quarkus-extension.properties`  
- 官方 **writing-extensions** 指南

---

## 4. 项目实战

### 4.1 目录结构

```
my-corp-extension/
  pom.xml
  deployment/
    pom.xml
    src/main/java/.../MyCorpProcessor.java
  runtime/
    pom.xml
    src/main/resources/META-INF/quarkus-extension.properties
```

### 4.2 父 `pom.xml`

```xml
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 https://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.mycompany.quarkus</groupId>
  <artifactId>my-corp-extension-parent</artifactId>
  <version>1.0.0-SNAPSHOT</version>
  <packaging>pom</packaging>
  <modules>
    <module>deployment</module>
    <module>runtime</module>
  </modules>
  <properties>
    <quarkus.version>3.19.2</quarkus.version>
  </properties>
</project>
```

### 4.3 `deployment/pom.xml`（节选）

```xml
<parent>
  <groupId>com.mycompany.quarkus</groupId>
  <artifactId>my-corp-extension-parent</artifactId>
  <version>1.0.0-SNAPSHOT</version>
</parent>
<artifactId>my-corp-extension-deployment</artifactId>
<dependencies>
  <dependency>
    <groupId>io.quarkus</groupId>
    <artifactId>quarkus-core-deployment</artifactId>
  </dependency>
  <dependency>
    <groupId>com.mycompany.quarkus</groupId>
    <artifactId>my-corp-extension</artifactId>
    <version>${project.version}</version>
  </dependency>
</dependencies>
```

### 4.4 `MyCorpProcessor.java`（骨架）

```java
package com.mycompany.quarkus.deployment;

import io.quarkus.deployment.annotations.BuildStep;
import io.quarkus.deployment.feature.FeatureBuildItem;

public class MyCorpProcessor {

    @BuildStep
    FeatureBuildItem feature() {
        return new FeatureBuildItem("my-corp-audit");
    }
}
```

### 4.5 `runtime/.../quarkus-extension.properties`

```properties
name=My Corp Audit
```

### 4.6 消费方 `pom.xml`

```xml
<dependency>
  <groupId>com.mycompany.quarkus</groupId>
  <artifactId>my-corp-extension</artifactId>
  <version>1.0.0-SNAPSHOT</version>
</dependency>
```

### 4.7 Kubernetes

扩展本身不直接对应 YAML；业务应用镜像构建流程同第 9 章。

---

## 5. 课堂实验

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 跟随官方 codestart 生成扩展或手写骨架 | `mvn install` 成功 |
| 2 | 新建消费工程依赖 runtime 模块 | 构建日志出现 Feature 名 |
| 3 | 讨论：下一步用 `BytecodeRecorder` 生成什么 | 路线图 |
| 4 | （作业）实现一个 CDI `@Interceptor` 绑定 | 审计日志 |

---

## 6. 项目总结

| 维度 | 说明 |
|------|------|
| **优点** | 可版本化、可测试。 |
| **缺点** | 维护成本高。 |
| **适用场景** | 平台组。 |
| **注意事项** | deployment 勿泄漏到用户编译 classpath。 |
| **常见踩坑** | Native hint 遗漏。 |

**延伸阅读**：<https://quarkus.io/guides/writing-extensions>
