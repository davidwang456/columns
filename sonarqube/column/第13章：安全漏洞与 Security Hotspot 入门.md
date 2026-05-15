# 第13章：安全漏洞与 Security Hotspot 入门

## 1. 项目背景

**业务场景**：某医疗 SaaS 平台的渗透测试团队在季度安全审计中发现了一个严重的 SQL 注入漏洞——攻击者可以通过登录页面的用户名输入框注入恶意 SQL 语句，获取所有患者的隐私数据。开发团队追溯代码时发现，这个问题在 SonarQube 中已经被标记为 Vulnerability 长达 4 个月——状态始终是 "Open"，没有人处理。

为什么会这样？安全团队说："我们不看 SonarQube，我们有专门的渗透测试工具。"开发团队说："我们看不懂安全规则，不知道怎么修，也没人告诉我们要修。"测试团队说："我们只管功能测试，安全测试是安全团队的活。"

三个团队互相推诿，一个 4 个月前就能修复的安全漏洞，最终以数据泄露的代价才被关注。

**痛点放大**：

- **安全所有权的模糊**：Vulnerability 和安全 Hotspot 出现在开发者的代码中，但安全部门负责审核。谁是第一责任人？
- **安全术语鸿沟**：CWE、OWASP、CVSS 评分——这些术语对普通开发者来说像外语。
- **Hotspot vs Vulnerability**：为什么有些安全问题叫 Vulnerability，有些叫 Hotspot？两者的处理流程完全不同，但团队不清楚区别。
- **"没有利用条件"的侥幸**：开发者看到 SQL 注入的报告后说"这个参数在前端做了长度限制，不会被注入"——但实际上攻击者可以直接发 HTTP 请求绕过前端验证。

## 2. 项目设计

### 剧本式交锋对话

---

**小胖**（打开 SonarQube 的安全页，看到一堆红色 Vulnerability）："大师！这 12 个 Vulnerability 里，有 2 个是我写的 SQL 注入。但我写的 SQL 用的是 Hibernate 的 HQL，不是拼接字符串——为什么还会报 SQL 注入？"

**大师**："HQL 同样存在注入风险，尤其是当你拼接 `where` 子句的时候。虽然 Hibernate 的 `setParameter()` 可以防范大部分注入，但如果你用了 `session.createQuery(\"from User where name = '\" + name + \"'\")` 这种拼接方式，和原生 SQL 的注入风险是一样的。

SonarQube 的安全规则检测的是**数据流路径**——它追踪用户输入从进入系统到到达危险 API 的完整路径。如果你的 `name` 参数来自 HTTP 请求，最终到达了 Query 的构造方法——不管中间经过了什么层——就会被标记。这叫做 **Taint Analysis（污点分析）**。"

**小白**："我发现项目中还有一类叫 Security Hotspot 的问题，和 Vulnerability 显示在不同的地方。两者有什么区别？"

**大师**："这是最容易混淆的概念：

- **Vulnerability（漏洞）**：SonarQube **确定**这是一个安全问题——如 SQL 注入、XSS、路径遍历。它基于确定性规则（污点分析、模式匹配），不需要人工判断即可认定。
- **Security Hotspot（安全热点）**：SonarQube **怀疑**这可能是一个安全问题，但**需要人工审核确认**——如密码哈希算法选择、权限配置、敏感信息日志、加密强度选择。

举个例子：
- `stmt.executeQuery(\"select * from users where id = \" + userId)` → Vulnerability（确定是 SQL 注入）
- `MessageDigest.getInstance(\"MD5\")` → Security Hotspot（使用 MD5 不一定是问题，取决于用途——存储密码不行，生成校验码可以）

Hotspot 需要人判断：这个上下文下，使用 MD5 是否安全？"

**小胖**："那 Hotspot 的处理流程是怎样的？"

**大师**："Hotspot 有三个状态：

1. **To Review**（待审核）：初始状态。安全人员或开发者需要审查这段代码。
2. **Reviewed → Safe**：审查后认为安全（如 MD5 用于非安全场景的校验和）。
3. **Reviewed → Fixed**：审查后认为需要修复，已经修复了。

重点是：Hotspot 只有经过人工审核才闭环。这和 Vulnerability 不同——Vulnerability 需要修复代码，Hotspot 需要审查决策。"

**小白**："OWASP Top 10 和 CWE 是什么？为什么 SonarQube 的安全规则都标注了 OWASP 和 CWE 标签？"

**大师**：
- **OWASP Top 10**：Web 应用最严重的 10 类安全风险（注入、失效认证、敏感数据泄露等）。SonarQube 的每条安全规则都会标注对应的 OWASP 类别。
- **CWE（Common Weakness Enumeration）**：通用弱点枚举，更细粒度的安全问题分类。如 CWE-89 是 SQL 注入，CWE-79 是 XSS。

标签的作用：**帮助安全团队和开发团队建立共同语言**。当安全团队说'这个项目有 OWASP A1:2021 注入风险'，开发者可以在 SonarQube 中按 OWASP 标签过滤出所有相关 Issue——不需要翻译。"

---

## 3. 项目实战

### 3.1 环境准备

- SonarQube 实例（Community Edition 支持安全规则）
- 已有 Java 项目，包含可复现的安全问题

### 3.2 分步实现

**步骤 1：编写包含多种安全问题的代码**

```java
package com.example.security;

import java.io.File;
import java.io.FileInputStream;
import java.security.MessageDigest;
import java.sql.Connection;
import java.sql.Statement;

public class InsecureService {

    // Vulnerability: 硬编码密码
    private static final String DB_PASSWORD = "prod123!@#";

    // Security Hotspot: 弱哈希算法
    public String hashPassword(String password) throws Exception {
        MessageDigest md = MessageDigest.getInstance("MD5");
        byte[] digest = md.digest(password.getBytes());
        return bytesToHex(digest);
    }

    // Vulnerability: SQL 注入
    public String getUserByName(String name, Connection conn)
            throws Exception {
        String sql = "SELECT * FROM users WHERE name = '" + name + "'";
        Statement stmt = conn.createStatement();
        return stmt.executeQuery(sql).toString();
    }

    // Vulnerability: 路径遍历
    public String readFile(String userPath) throws Exception {
        // 攻击者输入: ../../etc/passwd
        File file = new File("/var/data/" + userPath);
        FileInputStream fis = new FileInputStream(file);
        byte[] data = fis.readAllBytes();
        fis.close();
        return new String(data);
    }

    // Security Hotspot: 正则表达式 ReDoS
    public boolean isEmailValid(String email) {
        return email.matches(
            "^([a-zA-Z0-9]+)*@[a-zA-Z0-9]+(\\.[a-zA-Z]{2,})+$");
    }

    private String bytesToHex(byte[] bytes) {
        StringBuilder sb = new StringBuilder();
        for (byte b : bytes) {
            sb.append(String.format("%02x", b));
        }
        return sb.toString();
    }
}
```

**步骤 2：扫描并查看安全结果**

```bash
javac src/main/java/com/example/security/InsecureService.java -d target/classes
sonar-scanner
```

访问 Web UI 的 Security 页签：

| 类型 | 规则 | 位置 |
|------|------|------|
| Vulnerability | java:S2068 (硬编码密码) | DB_PASSWORD |
| Vulnerability | java:S3649 (SQL 注入) | getUserByName |
| Vulnerability | java:S2083 (路径遍历) | readFile |
| Security Hotspot | java:S4790 (弱哈希 MD5) | hashPassword |
| Security Hotspot | java:S5852 (ReDoS 正则) | isEmailValid |

**步骤 3：修复 Vulnerability**

**(a) 修复 SQL 注入**：

```java
// 使用参数化查询
public String getUserByName(String name, Connection conn)
        throws Exception {
    String sql = "SELECT * FROM users WHERE name = ?";
    PreparedStatement pstmt = conn.prepareStatement(sql);
    pstmt.setString(1, name);
    return pstmt.executeQuery().toString();
}
```

**(b) 修复路径遍历**：

```java
public String readFile(String userPath) throws Exception {
    // 验证路径，防止目录穿越
    String safePath = Paths.get(userPath)
        .getFileName().toString();
    File baseDir = new File("/var/data/");
    File file = new File(baseDir, safePath);
    if (!file.getCanonicalPath().startsWith(
        baseDir.getCanonicalPath())) {
        throw new SecurityException("Path traversal detected");
    }
    try (FileInputStream fis = new FileInputStream(file)) {
        return new String(fis.readAllBytes());
    }
}
```

**(c) 修复硬编码密码**：

```java
// 从环境变量或密钥管理服务获取
private String getDbPassword() {
    return System.getenv("DB_PASSWORD");
}
```

**步骤 4：处理 Security Hotspot——Review 流程**

对于 MD5 使用的 Hotspot：

1. 在 Web UI 中打开 Hotspot 详情页
2. 审查代码上下文：判断 MD5 的用途
3. 如果是用于密码存储 → 切换为 BCrypt/Argon2（需要修复代码）
4. 如果是用于非安全的校验和 → 标记为 **Reviewed: Safe**
5. 填写 Review Comment："MD5 used only for checksum validation, not for security purposes"

对于 ReDoS 正则的 Hotspot：

1. 审查正则表达式的可被利用性
2. 如果确认存在 ReDoS 风险 → 重构正则表达式 → 标记为 **Reviewed: Fixed**
3. 重新扫描确认 Hotspot 已消除

**步骤 5：安全规则优先级矩阵**

| 严重级别 | 处理时限 | 问责 |
|---------|---------|------|
| Blocker Vulnerability | 立即修复，阻塞合并 | 开发者 + 安全审查 |
| Critical Vulnerability | 24 小时内修复 | 开发者 |
| Major Vulnerability | 本次 Sprint 内修复 | 开发者 |
| Security Hotspot | 本次 Sprint 内审查 | 安全团队或 Tech Lead |

### 3.3 验证

```bash
# 查询安全相关的 Issue
curl -s -u admin:Sonar@2024Admin \
  "http://localhost:9000/api/issues/search?projectKeys=com.example:security-demo&types=VULNERABILITY&statuses=OPEN" \
  | python3 -m json.tool | grep -E '"message"|"severity"|"rule"'

# 按 OWASP 分类统计
curl -s -u admin:Sonar@2024Admin \
  "http://localhost:9000/api/issues/search?projectKeys=com.example:security-demo&owaspTop10=a1" \
  | python3 -m json.tool | grep '"total"'

# 查看 Security Hotspot 审查状态
curl -s -u admin:Sonar@2024Admin \
  "http://localhost:9000/api/hotspots/search?projectKey=com.example:security-demo&status=TO_REVIEW" \
  | python3 -m json.tool
```

---

## 4. 项目总结

### 4.1 Vulnerability vs Hotspot 对比

| 维度 | Vulnerability | Security Hotspot |
|------|--------------|-----------------|
| 确定性 | 确定是安全问题 | 需要人工判断 |
| 触发条件 | 代码模式匹配 + 污点分析 | 安全敏感 API 的使用 |
| 修复方式 | 必须修改代码 | 审核 → 修复 或 标记 Safe |
| 谁负责 | 开发者为主 | 安全团队 / Tech Lead 审核 |
| Quality Gate 影响 | 出现即可能导致 Gate Fail | 出现只是影响 Security Review Rating |

### 4.2 适用场景

- **所有 Web 应用**：OWASP Top 10 覆盖了最普遍的 Web 安全风险
- **有合规要求的行业**：金融、医疗、政务等需要安全审计的行业
- **开放 API 的系统**：暴露在公网的接口需要更严格的安全扫描

**不适用场景**：
- 纯内部使用的批处理脚本（安全威胁面很小）
- 需要深度安全测试的场景（如二进制逆向、侧信道攻击——这超出了 SAST 工具的能力边界）

### 4.3 注意事项

1. **SAST 不是银弹**：SonarQube 的 SAST（静态应用安全测试）只能发现已知模式的安全问题。不能替代渗透测试和安全审计。
2. **安全 Hotspot 不能无限期 "To Review"**：设置 SLA——如 Hotspot 创建后 5 个工作日内必须审查。
3. **安全规则不是越多越好**：过多的安全规则会引发"告警疲劳"，关键是要覆盖 OWASP Top 10 和团队实际面临的安全威胁。
4. **污点分析的限制**：跨服务、跨 JVM、跨进程的数据流可能无法追踪。SonarQube 的污点分析局限在单个应用内。

### 4.4 常见踩坑经验

**故障 1：污点分析报告很多"误报"——用户输入经过校验后仍然被标记**

根因：污点分析没有识别到你的校验/清洗函数。解决方案：在 Quality Profile 中将自定义的清洗函数注册为 "Sanitizer"（需要商业版或自定义插件）。

**故障 2：Hotspot 审查后重新扫描，状态又变回 To Review**

根因：Hotspot 的状态和 Issue 不同——Hotspot 的 Reviewed 状态绑定到代码行。如果代码行变更（即使只是格式化），Hotspot 会被重新创建。

### 4.5 思考题

1. 一个项目有 150 个 Security Hotspot 待审查，安全团队只有 2 人。你如何设计审查策略和优先级？
2. SonarQube 的污点分析（Taint Analysis）在哪些场景下会产生 False Negative（漏报）？如何弥补？

> **答案提示**：第1题建议按暴露面（外部 vs 内部接口）、数据敏感度、代码变更频率三层优先级审查。第2题见第35章。

---

> **推广计划提示**：安全问题的治理需要安全团队和开发团队协作。建议每个月的质量周会中，安全团队分享 1-2 个实际被修复的 Vulnerability 案例——让开发者看到"这些代码修正防止了什么攻击"，比抽象的安全宣讲更有说服力。
