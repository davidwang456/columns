# 第 31 章：测试进阶（集成、WireMock、CI 加速）

## 0. 课程卡片

| 项目 | 说明 |
|------|------|
| **建议课时** | 75 分钟 |
| **学习目标** | 使用 `@QuarkusIntegrationTest`；WireMock 固定下游；拆分 Maven profile |
| **先修** | 第 11、13 章 |

---

## 1. 项目背景

大规模团队 CI 时间与 **flaky** 是推广阻力。策略：**金字塔**、**容器测试精选**、**WireMock**、**并行 Surefire**。

---

## 2. 项目设计：大师与小白的对话

**测试**：「集成测试要 Docker。」

**大师**：「可以 **Testcontainers** 或 **共用测试集群**；小团队先 WireMock。」

**运维**：「CI runner 资源不够。」

**大师**：**分层 profile**：`fast` 默认 PR，`full` 夜间跑。」

**小白**：「`@QuarkusIntegrationTest` 何时用？」

**大师**：「测**打包产物**、**容器镜像**、与 dev classpath 差异时。」

---

## 3. 知识要点

- `quarkus-junit5` + `quarkus-junit5-internal`（若需要）  
- WireMock Quarkus 扩展或 JUnit 规则  
- `-DskipITs` / `-Pintegration`

---

## 4. 项目实战

### 4.1 `pom.xml` profiles

```xml
<profiles>
  <profile>
    <id>integration</id>
    <build>
      <plugins>
        <plugin>
          <groupId>org.apache.maven.plugins</groupId>
          <artifactId>maven-failsafe-plugin</artifactId>
          <version>3.5.2</version>
          <executions>
            <execution>
              <goals>
                <goal>integration-test</goal>
                <goal>verify</goal>
              </goals>
            </execution>
          </executions>
        </plugin>
      </plugins>
    </build>
  </profile>
</profiles>
```

### 4.2 WireMock 依赖（示例）

```xml
<dependency>
  <groupId>org.wiremock</groupId>
  <artifactId>wiremock-standalone</artifactId>
  <version>3.9.1</version>
  <scope>test</scope>
</dependency>
```

### 4.3 测试类骨架

`src/test/java/org/acme/it/DownstreamWireMockIT.java`：

```java
package org.acme.it;

import com.github.tomakehurst.wiremock.WireMockServer;
import com.github.tomakehurst.wiremock.client.WireMock;
import io.quarkus.test.junit.QuarkusTest;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;

import static com.github.tomakehurst.wiremock.client.WireMock.*;
import static io.restassured.RestAssured.given;

@QuarkusTest
class DownstreamWireMockIT {

    static WireMockServer wm;

    @BeforeAll
    static void start() {
        wm = new WireMockServer(0);
        wm.start();
        WireMock.configureFor(wm.port());
        stubFor(get(urlEqualTo("/greeting")).willReturn(aResponse().withBody("hi-mock")));
        System.setProperty("quarkus.rest-client.downstream-api.url", "http://localhost:" + wm.port());
    }

    @AfterAll
    static void stop() {
        wm.stop();
    }

    @Test
    void gateway() {
        given().when().get("/via-gateway").then().statusCode(200);
    }
}
```

> `/via-gateway` 需学员自行连接 `GatewayService`（第 13 章）；此处为结构示意。

### 4.4 GitHub Actions 片段（可选）

```yaml
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-java@v4
        with:
          distribution: temurin
          java-version: '17'
      - run: ./mvnw -B test
      - run: ./mvnw -B verify -Pintegration
```

---

## 5. 课堂实验

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 仅 `mvn test` 计时 | T1 |
| 2 | 加 WireMock IT | T2 |
| 3 | 讨论：哪些测迁到 nightly | 清单 |
| 4 | （选）Testcontainers PostgreSQL | 讲师演示 |

---

## 6. 项目总结

| 维度 | 说明 |
|------|------|
| **优点** | 稳定、可重复。 |
| **缺点** | 维护成本。 |
| **适用场景** | 发布门禁。 |
| **注意事项** | 端口冲突；清理容器。 |
| **常见踩坑** | IT 里测纯逻辑。 |

**延伸阅读**：<https://quarkus.io/guides/getting-started-testing#testing-the-application-running-in-a-container>
