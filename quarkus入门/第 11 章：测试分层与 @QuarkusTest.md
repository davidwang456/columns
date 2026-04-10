# 第 11 章：测试分层与 @QuarkusTest

## 0. 课程卡片

| 项目 | 说明 |
|------|------|
| **建议课时** | 60 分钟 |
| **学习目标** | 划分单元/集成测试；编写 `@QuarkusTest`；配置 Surefire |
| **先修** | 第 4 章 |

---

## 1. 项目背景

测试部门需要**稳定、可并行、可 CI** 的流水线。`@QuarkusTest` 启动真实扩展行为，成本高于纯 JUnit。本章建立：**金字塔**——大量纯单测 + 关键路径 Quarkus 测试。

---

## 2. 项目设计：大师与小白的对话

**测试**：「全用 `@QuarkusTest` 可以吗？」

**大师**：「可以，但你会得到**慢 CI** 和**难并行**。算法与纯函数请用普通 JUnit。」

**小白**：「`@QuarkusTest` 和 `@QuarkusIntegrationTest`？」

**大师**：「集成测试常配合**打包后 jar/容器**；课堂先掌握 `@QuarkusTest`。」

**运维**：「CI 里要装 Docker 吗？」

**大师**：「若用 Testcontainers/Dev Services，需要；否则仅 JVM 测试可免。」

**架构师**：「覆盖率门禁放哪一层？」

**大师**：「单元 + 集成合并看，但**阈值别只刷单元**糊弄。」

---

## 3. 知识要点

- `quarkus-junit5`、`rest-assured`  
- `maven-surefire-plugin` 的 `LogManager` 系统属性（见官方模板）  
- `@TestProfile` 切换测试配置

---

## 4. 项目实战

### 4.1 `pom.xml`（测试片段完整）

```xml
<dependencies>
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
      <groupId>org.apache.maven.plugins</groupId>
      <artifactId>maven-surefire-plugin</artifactId>
      <version>3.5.2</version>
      <configuration>
        <systemPropertyVariables>
          <java.util.logging.manager>org.jboss.logmanager.LogManager</java.util.logging.manager>
        </systemPropertyVariables>
      </configuration>
    </plugin>
  </plugins>
</build>
```

### 4.2 纯单元测试（不启 Quarkus）

`src/test/java/org/acme/PricingTest.java`：

```java
package org.acme;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;

class PricingTest {

    @Test
    void discount() {
        assertEquals(90, Pricing.applyDiscount(100, 10));
    }
}
```

`src/main/java/org/acme/Pricing.java`：

```java
package org.acme;

public final class Pricing {
    private Pricing() {}

    public static int applyDiscount(int price, int percent) {
        return price - price * percent / 100;
    }
}
```

### 4.3 `@QuarkusTest` REST 测试

（复用第 4 章 `ItemResourceTest` 或等价类。）

### 4.4 `src/test/resources/application.properties`（测试 profile）

```properties
%test.quarkus.log.level=WARN
```

---

## 5. 课堂实验

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | `./mvnw test` | 单元 + Quarkus 测试均通过 |
| 2 | 注释 `PricingTest`，观察时间差异 | 体会分层 |
| 3 | 故意删掉 `LogManager` surefire 配置再跑（备份后） | 可能告警或失败，理解原因 |
| 4 | （选）引入 Testcontainers PostgreSQL 写一个 `@QuarkusTest` | 讲师演示或作业 |

---

## 6. 项目总结

| 维度 | 说明 |
|------|------|
| **优点** | 接近真实行为；易测 HTTP 与安全。 |
| **缺点** | 慢；资源占用高。 |
| **适用场景** | API、持久化、消息集成。 |
| **注意事项** | 数据隔离；并行 CI。 |
| **常见踩坑** | 在 QuarkusTest 测纯数学；flaky 外部依赖。 |

**延伸阅读**：<https://quarkus.io/guides/getting-started-testing>
