# 第4章：Maven 项目扫描实战

## 1. 项目背景

**业务场景**：某互联网金融公司的用户中心微服务是一个典型的 Spring Boot + Maven 多模块项目，包含 user-api（接口定义）、user-service（业务逻辑）、user-dao（数据访问）三个子模块。团队在日常开发中面临以下质量痛点：

- **多模块聚合问题**：每次代码审查时，不同模块的代码混合在一起，评审者很难判断哪些修改属于哪个模块。
- **覆盖率黑洞**：单元测试覆盖率报告分散在各个模块的 `target` 目录下，没有人能把它们聚合在一起形成一个全局视图。
- **依赖扫描盲区**：Maven 引入了 50+ 个第三方依赖，其中 3 个存在已知安全漏洞（CVE），但团队完全不知情。

技术 Leader 希望通过 SonarQube 的 Maven 集成能力，在每次 `mvn verify` 时自动完成多模块聚合扫描，覆盖静态分析、单元测试覆盖率、依赖漏洞检查三个方面，并将结果统一展示在 SonarQube 面板上。

**痛点放大**：在没有 SonarQube Maven 集成时，团队面临的具体困境：

```
现状流程：
mvn test → 生成覆盖率报告（分散在各模块 target/）→ 人工汇总 → 
mvn dependency:analyze → 人工检查 → run SpotBugs → 人工看 HTML 报告

期望流程：
mvn verify sonar:sonar → 自动扫描 → 3 分钟后在 Web UI 看到全局质量视图
```

- **重复劳动**：同样的检查在本地 IDE、CI、SonarQube 各做一遍，浪费时间且结果不一致
- **覆盖率碎片化**：每个模块的 `jacoco.csv` 分散在各处，全局覆盖率全靠 Excel 手工合计
- **版本不一致**：本地的 SpotBugs 规则版本和 CI 上的可能不同，导致"本地没问题，CI 报一堆"

## 2. 项目设计

### 剧本式交锋对话

---

**小胖**（对着 Maven 的 pom.xml 发愁）："大师，我之前用 SonarScanner CLI 扫得好好的，为什么还要搞 Maven 插件？`mvn sonar:sonar` 和 `sonar-scanner` 有什么不一样？"

**大师**："你用 SonarScanner CLI 的时候，是不是先要手动运行 `javac` 编译，然后把 `sonar.java.binaries` 指向编译目录？如果忘了编译，扫描结果就不完整。Maven 插件解决了这个痛点——它直接嵌入 Maven 生命周期，自动获取编译输出、测试报告、依赖信息，你不需要手动指定任何路径。"

**小白**："那 Maven 插件在什么时候执行的？它在 `mvn compile` 时扫描还是 `mvn test` 时扫描？"

**大师**："关键是 `sonar:sonar` 这个 goal。它会触发一次完整的分析，但在此之前你必须确保项目已经编译和测试过（否则覆盖率报告为空）。通常的做法是：

```bash
mvn clean verify sonar:sonar
```

`verify` 阶段会执行编译、测试、生成覆盖率报告（如果配置了 JaCoCo）。`sonar:sonar` 读取这些产出物，上传到 SonarQube。如果你只跑 `mvn sonar:sonar`，它会触发编译，但不会自动跑测试——所以覆盖率会是 0%。"

**小胖**："那多模块项目怎么办？我有 3 个子模块，要每个目录都执行一次扫描吗？"

**大师**："不需要。在父模块的根目录执行一次 `mvn sonar:sonar` 就行。Maven 插件会自动遍历所有子模块，汇总分析结果。在 SonarQube 里，子模块会以'目录'形式展示在项目页面的 Code 页签下——不是独立项目，而是同一个项目下的分层视图。"

**小白**："这和把每个子模块单独创建为 SonarQube 项目有什么区别？"

**大师**："单独创建项目时，每个模块有独立的 Quality Gate、独立的指标趋势、独立的 Issue 列表。聚合为一个项目时，所有模块共享 Quality Gate，但你也可以看到每个模块各自的指标。一般原则是：如果是同一个产品/同一个仓库的不同模块，聚合为一个大项目；如果是独立的微服务或单独的仓库，设为独立项目。"

**小胖**："说到覆盖率，我听说 SonarQube 需要 JaCoCo 报告。我项目里根本没用 JaCoCo，用的是 Surefire，能行吗？"

**大师**："Surefire 是单元测试执行器，它只告诉 SonarQube '跑了多少测试、通过多少'。但**代码覆盖率**需要专门的工具。JaCoCo 是 Java 生态的标准覆盖率工具，SonarQube 和它有原生集成。你需要在 `pom.xml` 中配置 `jacoco-maven-plugin`，然后 SonarQube Maven 插件会自动找到 `target/jacoco.exec` 或 `target/site/jacoco/jacoco.xml` 并上传。

具体来说，SonarQube 读取覆盖率数据时有三条路径：
1. 如果有 `jacoco.exec` 二进制文件 → 自动读取
2. 如果有 `jacoco.xml` XML 报告 → 自动读取
3. 如果都没有 → 覆盖率显示 0%

推荐方式是在 `mvn verify` 阶段通过 JaCoCo 插件自动生成报告，SonarQube 会自动发现。"

**小胖**："那依赖安全漏洞呢？我发现 SonarQube 没有自动扫描我的 `pom.xml`。"

**大师**："依赖安全扫描在社区版中不是默认功能——你需要安装 'Dependency-Check' 插件，或者使用外部的 OWASP Dependency-Check 工具，将结果导入 SonarQube。商业版（Developer+）自带依赖安全分析功能，可以直接识别 Maven 依赖中的已知 CVE 漏洞。如果你用社区版，一个常见的组合是：

```bash
# 用 OWASP Dependency-Check 生成报告
mvn dependency-check:check
# 生成的 HTML 报告在 target/dependency-check-report.html
# 可以单独分享给安全团队
```

或者用 `dependency:analyze` 检查未使用/未声明的依赖，虽然不是安全扫描，但对减少攻击面很有帮助。"

**小白**："最后一个问题——私有仓库和代理设置。我们公司有内部 Nexus 私服，Maven 下载依赖要走代理。SonarScanner 或 Maven 插件会受影响吗？"

**大师**："SonarScanner CLI 启动时会从 SonarQube 服务器下载语言分析器——这个过程不走 Maven 代理。你需要在 SonarScanner 的 `sonar-scanner.properties` 中配置代理：

```properties
sonar.scanner.proxyHost=proxy.company.com
sonar.scanner.proxyPort=8080
```

而 Maven 插件在执行扫描时，分析器是通过 Maven 的依赖解析机制下载的——所以它会自动使用 `settings.xml` 中配置的代理和私服。这就是 Maven 集成的一个优势：网络配置继承 Maven 已有的设置。"

---

## 3. 项目实战

### 3.1 环境准备

- JDK 17+
- Maven 3.8+
- SonarQube 10.7 Community Edition（已运行）
- 项目 Token（已生成，有 Execute Analysis 权限）

### 3.2 分步实现

**步骤 1：创建多模块 Maven 项目**

```bash
mkdir maven-sonarqube-demo && cd maven-sonarqube-demo
```

**父 pom.xml**（项目根目录）：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0
         http://maven.apache.org/xsd/maven-4.0.0.xsd">
    <modelVersion>4.0.0</modelVersion>

    <groupId>com.example</groupId>
    <artifactId>user-center</artifactId>
    <version>1.0.0</version>
    <packaging>pom</packaging>

    <name>User Center</name>
    <description>用户中心微服务 - Maven 多模块项目</description>

    <modules>
        <module>user-api</module>
        <module>user-service</module>
        <module>user-dao</module>
    </modules>

    <properties>
        <java.version>17</java.version>
        <maven.compiler.source>17</maven.compiler.source>
        <maven.compiler.target>17</maven.compiler.target>
        <project.build.sourceEncoding>UTF-8</project.build.sourceEncoding>
        <!-- SonarQube 配置 -->
        <sonar.host.url>http://localhost:9000</sonar.host.url>
        <sonar.projectKey>com.example:user-center</sonar.projectKey>
        <sonar.projectName>User Center Microservice</sonar.projectName>
        <!-- JaCoCo 版本 -->
        <jacoco.version>0.8.12</jacoco.version>
    </properties>

    <build>
        <plugins>
            <!-- JaCoCo 覆盖率插件 -->
            <plugin>
                <groupId>org.jacoco</groupId>
                <artifactId>jacoco-maven-plugin</artifactId>
                <version>${jacoco.version}</version>
                <executions>
                    <execution>
                        <goals>
                            <goal>prepare-agent</goal>
                        </goals>
                    </execution>
                    <execution>
                        <id>report</id>
                        <phase>verify</phase>
                        <goals>
                            <goal>report</goal>
                        </goals>
                    </execution>
                </executions>
            </plugin>
        </plugins>
    </build>
</project>
```

**步骤 2：创建子模块**

**user-api/pom.xml**：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0
         http://maven.apache.org/xsd/maven-4.0.0.xsd">
    <modelVersion>4.0.0</modelVersion>
    <parent>
        <groupId>com.example</groupId>
        <artifactId>user-center</artifactId>
        <version>1.0.0</version>
    </parent>
    <artifactId>user-api</artifactId>
</project>
```

**user-api/src/main/java/com/example/user/api/UserDto.java**：

```java
package com.example.user.api;

public class UserDto {
    private String id;
    private String name;
    private String email;
    private String phone;

    public UserDto() {}

    public UserDto(String id, String name, String email) {
        this.id = id;
        this.name = name;
        this.email = email;
    }

    public String getId() { return id; }
    public void setId(String id) { this.id = id; }
    public String getName() { return name; }
    public void setName(String name) { this.name = name; }
    public String getEmail() { return email; }
    public void setEmail(String email) { this.email = email; }
    public String getPhone() { return phone; }
    public void setPhone(String phone) { this.phone = phone; }

    @Override
    public String toString() {
        return "UserDto{id='" + id + "', name='" + name + "'}";
    }
}
```

**user-dao/pom.xml**（略，结构同 user-api，artifactId 改为 user-dao）

**user-dao/src/main/java/com/example/user/dao/UserRepository.java**：

```java
package com.example.user.dao;

import com.example.user.api.UserDto;
import java.sql.*;
import java.util.ArrayList;
import java.util.List;

public class UserRepository {
    private String dbPassword = "root123"; // 硬编码密码

    public List<UserDto> findByName(String name, Connection conn)
            throws SQLException {
        String sql = "SELECT * FROM users WHERE name = '"
                     + name + "'"; // SQL 注入
        Statement stmt = conn.createStatement();
        ResultSet rs = stmt.executeQuery(sql);
        List<UserDto> users = new ArrayList<>();
        while (rs.next()) {
            UserDto dto = new UserDto();
            dto.setId(rs.getString("id"));
            dto.setName(rs.getString("name"));
            dto.setEmail(rs.getString("email"));
            users.add(dto);
        }
        // 资源未关闭：rs 和 stmt 泄漏
        return users;
    }
}
```

**user-service/pom.xml**：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0
         http://maven.apache.org/xsd/maven-4.0.0.xsd">
    <modelVersion>4.0.0</modelVersion>
    <parent>
        <groupId>com.example</groupId>
        <artifactId>user-center</artifactId>
        <version>1.0.0</version>
    </parent>
    <artifactId>user-service</artifactId>
    <dependencies>
        <dependency>
            <groupId>com.example</groupId>
            <artifactId>user-api</artifactId>
            <version>${project.version}</version>
        </dependency>
        <dependency>
            <groupId>com.example</groupId>
            <artifactId>user-dao</artifactId>
            <version>${project.version}</version>
        </dependency>
    </dependencies>
</project>
```

**user-service/src/main/java/com/example/user/service/UserService.java**：

```java
package com.example.user.service;

import com.example.user.api.UserDto;
import com.example.user.dao.UserRepository;
import java.sql.Connection;
import java.sql.DriverManager;
import java.util.Comparator;
import java.util.List;

public class UserService {
    private UserRepository repo = new UserRepository();

    public List<UserDto> searchUsers(String keyword) throws Exception {
        Connection conn = DriverManager.getConnection(
            "jdbc:h2:mem:test", "sa", "");
        List<UserDto> users = repo.findByName(keyword, conn);
        // 硬编码排序 + 异常被吞
        users.sort(new Comparator<UserDto>() {
            @Override
            public int compare(UserDto a, UserDto b) {
                if (a == null || b == null) return 0;
                return a.getName().compareTo(b.getName());
            }
        });
        conn.close();
        return users;
    }
}
```

**步骤 3：添加单元测试（覆盖 JaCoCo 路径）**

**user-api/src/test/java/com/example/user/api/UserDtoTest.java**：

```java
package com.example.user.api;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class UserDtoTest {
    @Test
    void constructorShouldSetFields() {
        UserDto dto = new UserDto("1", "Alice", "alice@example.com");
        assertEquals("1", dto.getId());
        assertEquals("Alice", dto.getName());
        assertEquals("alice@example.com", dto.getEmail());
        assertNull(dto.getPhone());
    }

    @Test
    void setPhoneShouldWork() {
        UserDto dto = new UserDto();
        dto.setPhone("1234567890");
        assertEquals("1234567890", dto.getPhone());
    }
}
```

**步骤 4：配置 SonarQube Token**

将 Token 配置到 Maven 全局或项目配置中。**推荐方式**：使用 Maven `settings.xml`（`~/.m2/settings.xml`）：

```xml
<settings>
  <servers>
    <server>
      <id>sonarqube</id>
      <username>YOUR_TOKEN</username>
      <password></password>
    </server>
  </servers>
</settings>
```

然后在 `pom.xml` 中通过 `<sonar.login>` 引用：

```xml
<properties>
    <sonar.login>${env.SONAR_TOKEN}</sonar.login>
</properties>
```

或者直接在命令行传入：

```bash
mvn sonar:sonar -Dsonar.token=squ_xxxxxxxxxxxxxxxxxxxx
```

> **安全提醒**：永远不要将 Token 硬编码在 `pom.xml` 中！Git 历史会永久保留它。

**步骤 5：执行完整构建和扫描**

```bash
# 在项目根目录（user-center/）执行
mvn clean verify sonar:sonar \
  -Dsonar.token=squ_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

命令执行顺序：
1. `clean` → 清空 target 目录
2. `compile` → 编译源码
3. `test` → 运行单元测试 + JaCoCo agent 收集覆盖率
4. `verify` → JaCoCo 生成覆盖率报告（jacoco.xml）
5. `sonar:sonar` → SonarQube 扫描并上传结果

**步骤 6：查看聚合结果**

访问 `http://localhost:9000/dashboard?id=com.example:user-center`。

你会看到一个**聚合视图**，包含所有模块的全局指标：

| 指标 | 说明 |
|------|------|
| Issues | 包含 user-api、user-dao、user-service 的全部 Issue |
| Coverage | 全局覆盖率（user-api 有测试 => 部分覆盖，user-dao 无测试 => 0%） |
| Lines of Code | 三模块总行数 |

点击 **Code** 页签，可以看到子模块的目录结构：

```
user-center
├── user-api/
│   └── src/main/java/com/example/user/api/UserDto.java
├── user-dao/
│   └── src/main/java/com/example/user/dao/UserRepository.java
└── user-service/
    └── src/main/java/com/example/user/service/UserService.java
```

**步骤 7：分析 Issue 分布**

user-dao 模块会有：
- 🔴 Bug：资源未关闭（ResultSet、Statement）
- 🔴 Vulnerability：SQL 注入、硬编码密码

user-service 模块会有：
- 🟡 Code Smell：匿名 Comparator 可用 Lambda 替代

### 3.3 高级配置：排除模块

如果某模块不需要扫描（如自动化生成的代码模块），在子模块 `pom.xml` 中添加：

```xml
<properties>
    <sonar.skip>true</sonar.skip>
</properties>
```

### 3.4 验证

```bash
# 验证 Quality Gate 状态
curl -s -u admin:Sonar@2024Admin \
  "http://localhost:9000/api/qualitygates/project_status?projectKey=com.example:user-center" \
  | python3 -m json.tool

# 验证覆盖率信息
curl -s -u admin:Sonar@2024Admin \
  "http://localhost:9000/api/measures/component?component=com.example:user-center&metricKeys=coverage,lines_to_cover,uncovered_lines" \
  | python3 -m json.tool
```

### 3.5 完整代码清单

```
maven-sonarqube-demo/
├── pom.xml                      # 父 POM（JaCoCo + Sonar 配置）
├── user-api/
│   ├── pom.xml
│   └── src/
│       ├── main/java/.../UserDto.java
│       └── test/java/.../UserDtoTest.java
├── user-dao/
│   ├── pom.xml
│   └── src/main/java/.../UserRepository.java
└── user-service/
    ├── pom.xml
    └── src/main/java/.../UserService.java
```

---

## 4. 项目总结

### 4.1 优点与缺点

| 维度 | Maven 插件 (`sonar:sonar`) | SonarScanner CLI |
|------|--------------------------|------------------|
| 自动发现编译输出 | ✅ 自动 | ❌ 需手动指定 `sonar.java.binaries` |
| 自动发现覆盖率报告 | ✅ 自动读取 jacoco.xml | ❌ 需手动指定 `sonar.coverage.jacoco.xmlReportPaths` |
| 自动发现测试报告 | ✅ 自动 | ❌ 需手动指定路径 |
| 子模块聚合 | ✅ 一次扫描全部汇总 | ✅ 通过 `sonar.modules` 手动指定 |
| 额外依赖 | ❌ 需引入 Maven 插件 | ✅ 独立工具，零依赖 |
| Gradle/非 Maven 项目 | ❌ 不适用 | ✅ 通用方案 |
| CI 集成复杂度 | 低（一条 Maven 命令） | 高（需独立安装 Scanner） |

### 4.2 适用场景

- **所有 Maven 管理的 Java/Kotlin/Scala 项目**：原生集成，成本最低
- **多模块项目**：自动聚合，模块级指标透明
- **已有 JaCoCo/Surefire 的团队**：无缝接入，无需额外配置覆盖率路径
- **需要依赖分析的项目**：结合 `dependency-check-maven` 可获得依赖安全视图

**不适用场景**：
- 非 Maven 项目（用 Gradle、Bazel 或其他构建工具）
- 需要离线扫描的环境（Maven 插件需要从 SonarQube 下载分析器 JAR）

### 4.3 注意事项

1. **JaCoCo 版本兼容**：SonarQube 10.x 兼容 JaCoCo 0.8.x 的所有版本。如果覆盖率显示异常，检查 JaCoCo 版本和 SonarQube 版本兼容性。
2. **`sonar.coverage.exclusions`**：如果某些包（如 DTO、常量类）不需要覆盖，在 `pom.xml` 中配置排除。
3. **私有仓库**：`sonar-scanner` 下载插件时使用 `sonar.scanner.sonarcloudUrl` 或默认的 SonarSource 仓库，不走 Maven 私服。
4. **并发扫描**：多个 Maven 模块可能同时触发扫描，需确保数据库和 CE 能承受并发。

### 4.4 常见踩坑经验

**故障 1：覆盖率始终显示 0%**

根因：JaCoCo 插件未在 `test` 阶段之前执行 `prepare-agent`。确保 `jacoco-maven-plugin` 的 `<executions>` 中 `prepare-agent` goal 在 `test` 之前执行（JaCoCo 默认行为就是绑定到 `initialize` 阶段，无需特殊配置，但显式声明更安全）。

**故障 2：子模块在 SonarQube 中不显示**

根因：`sonar.modules` 属性被手动覆盖了。Maven 插件会自动发现模块，不要在 `properties` 中手动设置 `sonar.modules`。

**故障 3：401 认证错误，但 Token 在别的地方能用**

根因：Maven `settings.xml` 中 `<id>sonarqube</id>` 和 SonarQube 需要的 Server ID 不匹配。确认 `<server><id>` 的值。

### 4.5 思考题

1. 如果一个 Maven 多模块项目中有 20 个子模块，但只有 5 个需要代码覆盖率检查，你如何配置？
2. `mvn sonar:sonar` 和 `mvn verify sonar:sonar` 的扫描结果会有什么不同？为什么？

> **答案提示**：第1题通过子模块 `pom.xml` 的 `<sonar.coverage.exclusions>` 或跳过测试来实现。第2题区别在于 `verify` 阶段执行了测试和 JaCoCo 报告生成，所以覆盖率会有数据。

---

> **推广计划提示**：Maven 项目扫描是 Java 后端团队的"标准接入方式"。建议先在 1-2 个团队试点，将 SonarQube 配置写入团队共享的父 POM 中，新项目继承即可获得扫描能力。质量负责人应和架构师一起评审父 POM 中的覆盖率阈值和排除规则。
