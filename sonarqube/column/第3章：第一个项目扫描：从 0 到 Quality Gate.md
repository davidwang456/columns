# 第3章：第一个项目扫描：从 0 到 Quality Gate

## 1. 项目背景

**业务场景**：某 SaaS 公司的支付服务模块最近连续发生 3 起线上事故——第一起是 BigDecimal 精度丢失导致结算金额偏差 0.01 元，第二起是日期格式化的线程安全问题，第三起是异常被"吞掉"导致故障无法溯源。团队复盘时发现，这些问题在代码审查中都没有被发现——因为审查者也是人，同样会疲劳和遗漏。

CTO 决定引入 SonarQube 进行自动化代码质量检查。但团队中有资深开发者质疑："我们的 IDE 已经装了 Checkstyle 和 SpotBugs，为什么还要 SonarQube？" 另一位开发者补充："而且我不知道怎么开始——是把整个项目丢进去扫一遍吗？扫完了怎么判断过还是没过？"

**痛点放大**：团队从"0"到第一次完成 Quality Gate 检查的过程中，通常会遇到以下问题：

- **不知道扫描什么**：整个项目 10 万行代码，是扫全部还是扫部分？源码目录和测试目录怎么区分？
- **不知道参数怎么填**：`sonar.projectKey` 是什么？`sonar.host.url` 填什么？Token 从哪里生成？
- **不知道结果怎么看**：扫描完成后 Web UI 上有一堆数字和图表，哪个重要？Bug 和 Code Smell 优先修哪个？
- **不知道过还是没过**：Quality Gate 显示 "Failed"，但看起来没什么严重问题，这个结果能忽略吗？

```
没有 SonarQube 时：
提交代码 → 人工 Code Review（2小时）→ 凭经验判断 → 合并代码 → 3天后线上出 Bug

有 SonarQube 后：
提交代码 → 自动扫描（5分钟）→ 门禁判断 → 不合规代码自动阻断 → 修复后合并
```

本章将带领你完成从零到第一次 Quality Gate 检查的完整旅程——不仅是"跑通"一条命令，而是理解每一个参数的含义、每一步发生了什么、以及扫描结果怎么指导你修复代码。

## 2. 项目设计

### 剧本式交锋对话

---

**小胖**（把腿翘在桌子上，啃着煎饼果子）："大师，SonarQube 我已经装好了，登录页面也看到了。但是接下来怎么用？我是不是要像考试一样把我写的代码全部丢进去让它打分？满分 100 分，我至少得个 80 吧？"

**大师**："小胖，你这个心态很危险。SonarQube 不是'判分老师'，它是'医生'——它告诉你身体哪里有问题，但不会因为你有一堆问题就给你的健康打个 59 分。你应该关心的是：有没有致命的 Bug？有没有安全漏洞？代码是不是越来越难维护？"

**小胖**："好吧，那医生怎么给我做检查？我先得挂个号吧？"

**大师**："挂号就是配置 `sonar-project.properties`。这个文件是 SonarScanner 的"挂号单"，告诉 SonarQube 你要检查哪个项目、源码在哪里、怎么连接服务器。"

**小白**（打开笔记本，开始敲键盘）："我看了官方文档，`sonar-project.properties` 里最关键的参数有这么几个：

```properties
sonar.projectKey=com.example:my-project    # 项目唯一标识
sonar.projectName=My Project               # 显示名称
sonar.sources=src/main/java                # 源码路径
sonar.tests=src/test/java                  # 测试路径
sonar.host.url=http://localhost:9000       # SonarQube 地址
sonar.token=squ_xxxx                       # 认证令牌
```

这些参数中 `projectKey` 为什么要写成 `com.example:my-project` 这样的格式？直接叫 `my-project` 不行吗？"

**大师**："`projectKey` 是项目的唯一身份证号，一旦创建就不能改。写成 `com.example:my-project` 的格式是约定俗成——前面部分代表组织或团队，后面部分是项目名，和 Java 的包名约定类似。这样做是为了在有几百个项目时不会重名。你想想，如果全公司都叫 `utils` 或 `common`，Web UI 里的项目列表就变成'解密游戏'了。"

**小胖**："那 Token 呢？我上章的 Token 是 `squ_` 开头的，这个格式有什么特殊含义吗？"

**大师**："`squ_` 是 SonarQube 10.x 新增的 Token 前缀格式（`sqp_` 为项目分析 Token，`squ_` 为用户 Token，`sqr_` 为注册 Token）。前缀让 Token 的类型一目了然，也方便做内部审计——哪天日志里发现了 Token 泄露，可以根据前缀立刻知道是哪类 Token、去吊销哪类。"

**小白**："配置好了，执行 `sonar-scanner` 就完了吧？扫描过程中发生了什么？"

**大师**："扫描分四步：

1. **Bootstrap**：Scanner 连接 SonarQube 服务器，下载当前启用的规则集（Quality Profile）、验证 Token 权限、检查插件版本。
2. **Index Files**：Scanner 遍历 `sonar.sources` 下所有文件，根据文件扩展名识别语言，建立文件索引。
3. **Analyze**：对每个文件执行对应语言的规则——包括语法树分析、数据流分析、代码度量（复杂度、重复率等）。
4. **Report**：将所有分析结果打包成 zip 报告，上传到 SonarQube 服务器。

服务器收到报告后，Compute Engine 进行二次计算：评估 Quality Gate、计算技术债务、更新指标趋势。"

**小胖**："那为什么我有时候扫描只需要 30 秒，有时候要 5 分钟？"

**小白**（抢先回答）："我猜是因为增量扫描！SonarScanner 会对比文件哈希值，只分析变更过的文件。如果你只改了 1 个文件，就只分析那 1 个——这叫'增量分析'。但如果第一次扫描，所有文件都要分析，所以会慢很多。"

**大师**："完全正确。首次扫描是全量，后续扫描是增量。另外，规则复杂度也影响速度——比如'污点分析'规则比简单的命名规范检查慢很多。"

**小胖**："扫描完了，Web UI 上 Quality Gate 显示一个大红叉 Failed。这意味着我的代码不能合并了？但是老板催着上线呢，我能不能先忽略？"

**大师**："这正是 Quality Gate 的价值——给你一个不可忽略的红线。如果你现在忽略了，三个月后就是 500 个被忽略的问题。门禁不是为难你，是保护团队。但是，门禁也有灵活性——比如只有 New Code 上的新问题才会触发门禁，历史问题可以用'Accept'标记为已知风险。这就是'先止血再还债'策略的技术基础。"

---

## 3. 项目实战

### 3.1 环境准备

**前置条件**：SonarQube 实例已启动（参考第2章），浏览器可访问 `http://localhost:9000`。

**创建测试项目**：

```bash
mkdir -p ~/sonarqube-demo/java-demo/src/main/java/com/demo
cd ~/sonarqube-demo/java-demo
```

### 3.2 分步实现

**步骤 1：编写包含多种质量问题的示例代码**

创建 `src/main/java/com/demo/OrderService.java`：

```java
package com.demo;

import java.sql.Connection;
import java.sql.Statement;
import java.util.ArrayList;
import java.util.List;

public class OrderService {
    private String dbPassword = "admin123"; // 硬编码密码

    public double calculateTotal(List<Double> prices, double discountRate) {
        if (prices == null) {
            System.out.println("Prices is null"); // 用 System.out 而不是 Logger
        }
        double total = 0;
        for (int i = 0; i < prices.size(); i++) {
            total += prices.get(i);
        }
        // 除零风险
        total = total / discountRate;
        return total;
    }

    public List<String> getOrdersByUser(String userId, Connection conn)
            throws Exception {
        Statement stmt = conn.createStatement();
        // SQL 注入
        stmt.executeQuery(
            "SELECT * FROM orders WHERE user_id = '" + userId + "'");
        // 资源未释放
        return new ArrayList<>();
    }

    public int complexLogic(int a, int b, int c, int d) {
        int result = 0;
        if (a > 0) {
            if (b > 0) { result = 1; }
            else if (b == 0) {
                if (c > 0) { result = 2; }
                else {
                    if (d > 0) { result = 3; }
                    else { result = 4; }
                }
            }
            else {
                if (c > 0) {
                    if (d > 0) { result = 5; }
                    else { result = 6; }
                }
                else { result = 7; }
            }
        } else if (a == 0) { result = 8; }
        else { result = 9; }
        return result;
    }
}
```

这份代码故意植入了常见问题：硬编码密码、SQL 注入、资源泄露、使用 System.out、除零风险、过高复杂度。

**步骤 2：生成扫描 Token**

登录 SonarQube，进入 **My Account → Security → Generate Tokens**，Token 名称输入 `scanner-demo`，点击 Generate。

复制 Token 值（如 `squ_abcdef1234567890abcdef1234567890abcdef12`）。

**步骤 3：编写 sonar-project.properties**

在项目根目录（`java-demo/`）创建 `sonar-project.properties`：

```properties
# 项目唯一标识（必须）
sonar.projectKey=com.demo:order-service

# 在 Web UI 中显示的名称
sonar.projectName=Order Service Demo

# 项目版本（可选，但建议填写）
sonar.projectVersion=1.0.0

# SonarQube 服务器地址
sonar.host.url=http://localhost:9000

# 认证 Token
sonar.token=squ_abcdef1234567890abcdef1234567890abcdef12

# 源码目录（相对于 sonar-project.properties 的路径）
sonar.sources=src/main/java

# 源码文件编码
sonar.sourceEncoding=UTF-8

# 编译后的 class 文件路径（Java 项目必须，用于字节码分析）
sonar.java.binaries=target/classes
```

> **重要**：Java 项目必须提供 `sonar.java.binaries`，否则 SonarScanner 无法执行字节码级别的分析（如检查未使用的依赖）。如果没有编译过，先编译：
> ```bash
> mkdir -p target/classes
> javac src/main/java/com/demo/OrderService.java -d target/classes
> ```

**步骤 4：安装 SonarScanner 并执行扫描**

**Linux/macOS：**

```bash
# 下载 SonarScanner CLI
export SCANNER_VERSION=6.2.1.4610
wget https://binaries.sonarsource.com/Distribution/sonar-scanner-cli/sonar-scanner-cli-${SCANNER_VERSION}-linux-x64.zip
unzip -q sonar-scanner-cli-${SCANNER_VERSION}-linux-x64.zip

# 设置 PATH
export PATH=$PATH:$(pwd)/sonar-scanner-${SCANNER_VERSION}-linux-x64/bin

# 执行扫描
sonar-scanner
```

**Windows (PowerShell)：**

```powershell
# 下载并解压 sonar-scanner-cli-6.2.1.4610-windows-x64.zip
# 将解压后的 bin 目录添加到 PATH
# 然后在项目目录执行：
sonar-scanner.bat
```

**步骤 5：观察扫描输出**

扫描过程中的关键日志：

```
INFO: Scanner configuration file: .../sonar-project.properties
INFO: Project root configuration file: .../sonar-project.properties
INFO: SonarScanner 6.2.1.4610
INFO: Java 17.0.x Oracle Corporation (64-bit)
INFO: Linux 5.15.0-91-generic amd64
INFO: User cache: /home/user/.sonar/cache
INFO: Analyzing on SonarQube server 10.7.0
INFO: Default locale: "en_US", source code encoding: "UTF-8"
INFO: Load global settings
INFO: Load global settings (done) | time=150ms
INFO: Server id: AEA1475C-AY-vmT8IRwGpe5QUQ5di
INFO: User cache: /home/user/.sonar/cache
INFO: Load/download plugins
INFO: Load plugins index
INFO: Load plugins index (done) | time=80ms
INFO: Load/download plugins (done) | time=120ms
INFO: Process project properties
INFO: Execute project builders
INFO: Execute project builders (done) | time=10ms
INFO: Project key: com.demo:order-service
INFO: Base dir: /home/user/sonarqube-demo/java-demo
INFO: Working dir: /home/user/sonarqube-demo/java-demo/.scannerwork
INFO: Load project settings for component key: 'com.demo:order-service'
INFO: Load quality profiles
INFO: Load quality profiles (done) | time=200ms
INFO: Load active rules
INFO: Load active rules (done) | time=1500ms
INFO: Indexing files...
INFO: Project configuration:
INFO:   Excluded sources: **/build-wrapper-dump.json
INFO: 1 file indexed
INFO: Quality profile for java: Sonar way
INFO: Sensor JavaSensor [java]
INFO: Sensor JavaSensor [java] (done) | time=2500ms
INFO: ------------- Run sensors on project
...
INFO: Analysis report generated in 125ms
INFO: Analysis report compressed in 30ms
INFO: Analysis report uploaded in 500ms
INFO: ANALYSIS SUCCESSFUL, you can find the results at:
INFO: http://localhost:9000/dashboard?id=com.demo:order-service
INFO: Note that you will be able to access the dashboard only once the
INFO: server has processed the submitted analysis report
INFO: EXECUTION SUCCESS
```

关键信息解读：
- `Quality profile for java: Sonar way` → 使用的是 Sonar way 默认规则集
- `1 file indexed` → 共扫描 1 个文件
- `ANALYSIS SUCCESSFUL` → 扫描上传成功
- 注意最后一句提示：需要等待 Compute Engine 处理完毕才能在 Web UI 看到结果

**步骤 6：在 Web UI 查看结果**

等待 30-60 秒后，访问 `http://localhost:9000/dashboard?id=com.demo:order-service`。

你将看到：

| 指标 | 值 | 说明 |
|------|----|------|
| Bugs | 🟡 2 | 除零风险 + 资源未关闭 |
| Vulnerabilities | 🔴 2 | SQL 注入 + 硬编码密码 |
| Code Smells | 🟡 3 | System.out、过时方法、复杂度过高 |
| Coverage | 0.0% | 未配置单元测试 |
| Duplications | 0.0% | 单文件无重复 |

Quality Gate 状态应为 **Failed**。

点击 Quality Gate 旁的 "See details"，查看具体哪些条件未通过。

### 3.3 修复与重新扫描

**步骤 7：修复 Block 级别的问题**

Bug 和 Vulnerability 是阻止 Quality Gate 通过的主要原因。最小化修复示例：

```java
package com.demo;

import java.sql.Connection;
import java.sql.PreparedStatement;
import java.util.List;
import java.util.logging.Logger;

public class OrderService {
    private static final Logger LOG =
        Logger.getLogger(OrderService.class.getName());

    public double calculateTotal(List<Double> prices, double discountRate) {
        if (prices == null) {
            LOG.warning("Prices is null");
            return 0.0;
        }
        if (discountRate == 0) {
            return 0.0; // 防御除零
        }
        double total = prices.stream()
            .mapToDouble(Double::doubleValue).sum();
        return total / discountRate;
    }

    public List<String> getOrdersByUser(String userId, Connection conn)
            throws Exception {
        String sql = "SELECT * FROM orders WHERE user_id = ?";
        try (PreparedStatement ps = conn.prepareStatement(sql)) {
            ps.setString(1, userId); // 参数化查询防注入
            ps.executeQuery();
        } // try-with-resources 自动关闭
        return List.of();
    }
    // ... 其余方法省略
}
```

**步骤 8：重新编译并扫描**

```bash
javac src/main/java/com/demo/OrderService.java -d target/classes
sonar-scanner
```

刷新 Web UI，观察 Quality Gate 状态变化。Bug 和 Vulnerability 应该消除（或降级为 Info 级别）。

### 3.4 验证

```bash
# 查询 Quality Gate 状态
curl -s -u admin:Sonar@2024Admin \
  "http://localhost:9000/api/qualitygates/project_status?projectKey=com.demo:order-service" \
  | python3 -m json.tool

# 查询 Issue 按严重级别统计
curl -s -u admin:Sonar@2024Admin \
  "http://localhost:9000/api/issues/search?projectKeys=com.demo:order-service&statuses=OPEN" \
  | python3 -m json.tool | grep -E '"severity"|"type"'
```

---

## 4. 项目总结

### 4.1 优点与缺点

| 维度 | SonarQube 扫描 | 人工 Code Review |
|------|---------------|-----------------|
| 检测速度 | 秒级到分钟级 | 小时级 |
| 覆盖规则 | 5000+ 条，无遗漏 | 取决于审查者经验 |
| 一致性 | 100% 一致 | 随审查者状态波动 |
| 误报率 | 约 5-10%（语言差异） | 因人而异 |
| 修复建议 | 每条 Issue 附带修复示例 | 审查者需要口头/文字解释 |
| 上下文理解 | 无法理解业务逻辑意图 | 能理解业务上下文 |
| 成本 | 平台运维成本 | 人力时间成本 |

**核心结论**：两者不是替代关系，而是互补。SonarQube 负责"穷举式规则检查"，人工审查负责"业务逻辑正确性和设计合理性"。

### 4.2 适用场景

- **所有 Java/JS/Python/C# 项目**：任何有编译/构建流程的项目均可接入
- **CI 流水线门禁**：自动阻断不合格代码的合并
- **开源项目质量管理**：通过 SonarCloud 免费为开源项目提供质量分析
- **技术债务摸底**：扫描历史项目，量化技术债务

**不适用场景**：
- 需要业务逻辑验证的场景（如"这个 if 条件是否覆盖了正确的业务分支"）
- 没有源码的二进制依赖分析

### 4.3 注意事项

1. **`sonar.token` 不要提交到 Git**：应通过 CI 环境变量注入。泄露的 Token 可能导致恶意扫描或数据篡改。
2. **Java 项目必须有 `sonar.java.binaries`**：否则无法执行字节码级别分析，会丢失部分规则检查。
3. **首次扫描关注 New Code**：历史代码的问题量可能大到让人绝望，从 New Code 开始治理是唯一可行的路径。
4. **quality gate 不是"满分"**：通过 Quality Gate 不意味着代码完美，只意味着达到团队商定的最低标准。

### 4.4 常见踩坑经验

**故障 1：扫描成功但 Web UI 看不到结果，等了 10 分钟也没有**

根因：Compute Engine 任务处理失败。进入 Administration → Projects → Background Tasks，查看任务状态。常见原因：ES 内存不足、数据库连接池耗尽。

**故障 2：`sonar-scanner` 报错 "No files nor directories matching 'src/main/java'"**

根因：`sonar-project.properties` 中的路径是相对于 properties 文件位置的。确认目录结构，在项目根目录执行 `sonar-scanner`。

**故障 3：扫描结果全是"行数为 0"，没有 Issue**

根因：Java 项目没有配置 `sonar.java.binaries` 或字节码目录为空。必须先编译再扫描。

### 4.5 思考题

1. 如果一个项目有 5000 个历史 Issue，你如何设置 Quality Gate 让团队既能接受这个工具又不被历史债压垮？
2. `sonar.exclusions` 参数可以用来排除哪些文件？如果排除了第三方代码（如 vendor 目录），会有什么利弊？

> **答案提示**：第1题核心是"New Code Only"门禁策略，详见第20章。第2题见第22章。

---

> **推广计划提示**：本章适合所有开发人员。建议团队在集体学习时准备一个"有代表性"的示例项目（包含常见问题），让每个人亲手完成一次从扫描到修复的闭环。这比看 3 小时文档效果好 10 倍。
