# 第21章：安全扫描——SAST、Secret Detection 与 DAST

## 1. 项目背景

> **业务场景**：一家金融科技公司通过了 SOC2 审计，但安全团队在一次内部渗透测试中发现：CI/CD 流水线完全没有安全扫描——代码中的 SQL 注入漏洞、硬编码的 API Key、第三方依赖的已知 CVE 漏洞，全部畅通无阻地进入了生产环境。

最惊险的一次：某开发者为了方便调试，把生产环境的 AWS Access Key 直接写在代码里提交了。这个密钥在代码库里躺了 3 个月，直到被安全团队偶然发现。幸运的是该密钥只有只读权限，但如果是写权限，后果不堪设想。

安全总监拍板："CI/CD 流水线必须加入三道安全防线——SAST（静态代码扫描）、Secret Detection（密钥检测）、Dependency Scanning（依赖漏洞扫描）。任何 MR 必须通过安全扫描才能合并。"

**痛点放大**：传统的安全扫描是在上线前由安全团队手动跑一次——这意味漏洞从引入到发现可能间隔数周，修复成本呈指数级增长。GitLab 的安全扫描将防线左移到 MR 阶段——当你提交代码时，漏洞就已被发现，修复只需改几行代码，而不是在上线前匆忙打补丁。

## 2. 项目设计——剧本式交锋对话

**场景**：安全培训会上，安全工程师正在演示漏洞检测过程。

---

**安全工程师**："上个月的渗透测试发现了 47 个漏洞，其中 12 个是 SQL 注入。如果我们的 SAST 扫描在 MR 阶段就发现了，根本不会出现在渗透测试报告中。"

**小胖**："SAST 是什么？跟我们用的 ESLint 有什么区别？"

**大师**："ESLint 是代码风格检查——检查你有没有用 `var` 而不是 `const`，有没有未使用的变量。SAST（Static Application Security Testing）是代码安全检查——检查你的 SQL 拼接是否存在注入风险、你的代码里有没有硬编码的密码、你的加密函数用的是不是已被破解的 MD5。技术映射——ESLint 是交规检查（你的车灯亮不亮），SAST 是安检（你车上有没有炸弹）。"

**小白**："Secret Detection 听起来很简单——不就是扫一下有没有 `password=` 之类的字符串吗？"

**大师**："比你想的复杂得多。GitLab 的 Secret Detection 会识别 100+ 种密钥格式——AWS Key、GCP Service Account、GitHub Token、SSH Private Key、JWT Token，每种都有独特的正则模式和熵特征。它还支持自定义规则——比如你们公司的内部 API Key 前缀。而且它不仅在 MR 阶段检查新代码，还支持定时全量扫描仓库历史。"

**小胖**："那 DAST 呢？我听说是对运行中的应用做扫描。"

**大师**："DAST（Dynamic Application Security Testing）是对正在运行的应用做黑盒扫描——模拟攻击者的行为，发送 XSS payload、SQL 注入 payload、目录遍历攻击。它能发现 SAST 发现不了的运行时漏洞——比如错误配置导致的敏感信息泄露、认证绕过、CSRF 等。技术映射：SAST 是安检你写的代码，DAST 是模拟小偷偷你的应用。"

---

## 3. 项目实战

### 环境准备

> **目标**：为一个 Web 项目配置 SAST、Secret Detection 和 DAST 流水线，验证扫描效果并修复漏洞。

**前置条件**：GitLab CE/EE 17.x，有维护者权限。部分高级功能需要 GitLab Ultimate。

### 分步实现

#### 步骤1：配置 SAST 扫描

**目标**：在 CI 中集成 SAST，自动扫描 SAST 并生成报告。

```yaml
# .gitlab-ci.yml - SAST 配置
stages:
  - test
  - security

include:
  # 引用 GitLab 官方 SAST 模板（CE 和 EE 通用）
  - template: Jobs/SAST.gitlab-ci.yml

# 也可以自定义 SAST 参数
variables:
  SAST_EXCLUDED_PATHS: "spec, test, tests, tmp, vendor, node_modules"
  SAST_EXCLUDED_ANALYZERS: ""       # 留空 = 使用所有语言分析器
  SAST_BRAKEMAN_LEVEL: 2            # Brakeman 漏洞等级阈值（1=低 2=中 3=高）
  SAST_SEMGREP_METRICS: "false"
  SECURE_LOG_LEVEL: "info"

# SAST job 默认行为：
# - 在 MR 和 main 分支运行
# - 生成 gl-sast-report.json
# - GitLab 自动解析并在 MR 页面显示漏洞
# - 可配置为阻断合并（需要 EE）

# 自定义 SAST 规则（可选）
sast-custom:
  stage: security
  image: node:20-alpine
  script:
    - npm install -g eslint eslint-plugin-security
    - eslint --plugin security --format gitlab src/
  artifacts:
    reports:
      sast: gl-sast-report.json
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
```

**查看 SAST 报告**：

```bash
# 1. MR 页面 → Security tab → SAST 分析结果
# 2. 每个漏洞显示：严重级别（Critical/High/Medium/Low）、文件路径、行号
# 3. 点击漏洞 → 查看详情、修复建议、CWE 编号

# 常见 SAST 发现的漏洞示例：
# - SQL Injection: 字符串拼接 SQL 查询
# - XSS: innerHTML、dangerouslySetInnerHTML
# - Hardcoded credentials: 代码中的硬编码密码
# - Weak cryptography: MD5/SHA1 用于安全目的
# - Path traversal: 用户输入直接用于文件路径
```

#### 步骤2：配置 Secret Detection（密钥检测）

**目标**：配置密钥扫描，拦截代码中的 API Key、Token、私钥。

```yaml
# .gitlab-ci.yml - Secret Detection
include:
  - template: Jobs/Secret-Detection.gitlab-ci.yml

variables:
  SECRET_DETECTION_HISTORIC_SCAN: "true"  # 扫描全量历史

# 自定义排除规则（.gitlab/secret-detection.yml）
```

**创建自定义排除规则**：

```yaml
# .gitlab/secret-detection.yml
# 排除测试用的假密钥
stages:
  - build
  - test

secret_detection:
  variables:
    SECRET_DETECTION_EXCLUDED_PATHS: "tests/, spec/, fixtures/"
    SECRET_DETECTION_COMMIT_FROM: ""   # 从头扫描
    SECRET_DETECTION_COMMIT_TO: ""

# 在代码中用注释标记排除（行内忽略）
# const TEST_KEY = "sk-test-12345"; // gitlab-secret-detection:ignore
```

**Secret Detection 检查的密钥类型**：

| 类别 | 示例 |
|------|------|
| Cloud 凭证 | AWS Access Key, GCP SA Key, Azure Connection String |
| API Keys | GitHub Token, GitLab PAT, Slack Webhook |
| 私钥 | SSH Private Key, PGP Private Key, SSL Private Key |
| 数据库密码 | JDBC URL, MongoDB URI, PostgreSQL URI |
| Token | JWT, OAuth Token, Bearer Token |

**如果密钥已提交到 Git 历史中**：

```bash
# 1. Secret Detection 会标记历史 commit
# Security → Vulnerability Report → 按 "Commit SHA" 过滤

# 2. 清理历史（BFG Repo-Cleaner，参考第13章）

# 3. 立即轮换已泄露的密钥！
# 不要只清理 Git 历史——密钥可能已被人复制
```

#### 步骤3：配置 DAST 动态扫描

**目标**：对运行中的应用进行动态安全扫描。

```yaml
# .gitlab-ci.yml - DAST 配置
stages:
  - deploy-review
  - dast
  - stop-review

include:
  - template: Jobs/DAST.gitlab-ci.yml

# 1. 先部署一个 Review 环境
deploy-review:
  stage: deploy-review
  image: alpine:latest
  script:
    - echo "Deploying to http://$CI_ENVIRONMENT_SLUG.review.example.com"
  environment:
    name: review/$CI_COMMIT_REF_SLUG
    url: http://$CI_ENVIRONMENT_SLUG.review.example.com
    on_stop: stop-review
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'

# 2. DAST 扫描 Review 环境
dast:
  variables:
    DAST_WEBSITE: http://$CI_ENVIRONMENT_SLUG.review.example.com
    DAST_FULL_SCAN_ENABLED: "true"         # 全量扫描（包括被动+主动）
    DAST_BROWSER_SCAN: "true"              # 启用浏览器模拟扫描
    DAST_SKIP_TARGET_CHECK: "false"        # 验证目标可达
    DAST_API_SPECIFICATION: ""             # 如有 OpenAPI spec，可指定路径
    DAST_AUTH_URL: ""                      # 如有登录表单，可配置认证
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'

# 3. 扫描后停止 Review 环境
stop-review:
  stage: stop-review
  image: alpine:latest
  script: echo "Stopping review env"
  environment:
    name: review/$CI_COMMIT_REF_SLUG
    action: stop
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
  when: manual
```

#### 步骤4：设置 MR 安全门禁（EE）

**目标**：当 MR 引入高危漏洞时，阻止合并。

```yaml
# .gitlab-ci.yml - 安全门禁
# 注意：此功能需要 GitLab Ultimate（EE）许可证

# 在 Merge Request 设置中：
# Settings → Merge requests → Merge checks
# → ✅ Security approvals are required
# → 选择漏洞严重级别阈值（Critical / High / Medium）

# 在 .gitlab-ci.yml 中配合使用：
include:
  - template: Jobs/SAST.gitlab-ci.yml
  - template: Jobs/Secret-Detection.gitlab-ci.yml
  - template: Jobs/Dependency-Scanning.gitlab-ci.yml

# 关键变量：
variables:
  SAST_DISABLED: "false"
  SECRET_DETECTION_DISABLED: "false"
  # 设置阻断阈值（EE only）
  SAST_EXCLUDED_PATHS: "spec, test, tests, tmp"

# MR 的安全审批逻辑：
# 1. MR target 是 main（保护分支）
# 2. SAST/Secret Detection 发现 new vulnerabilities
# 3. 新漏洞的严重级别 >= 配置的阈值
# 4. → Merge 按钮变灰，要求审批
```

### 完整代码清单

- `.gitlab-ci.yml`：SAST + Secret Detection + DAST 完整配置
- `.gitlab/secret-detection.yml`：自定义密钥检测规则
- DAST 认证配置（如需要）

### 测试验证

```bash
# 验证1：SAST 扫出漏洞
# 在代码中故意写一个 SQL 注入漏洞：
cat > src/db.js << 'EOF'
function getUserByName(name) {
  const query = "SELECT * FROM users WHERE name = '" + name + "'";
  return db.query(query);
}
EOF
# 提交 MR → 查看 Security widget → 应显示 SQL Injection 漏洞

# 验证2：Secret Detection 扫出密钥
# 在代码中写一个假密钥：
echo 'AWS_ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"' >> config.js
# 提交 MR → 应被 Secret Detection 标记

# 验证3：DAST 扫出 XSS
# 部署一个简单 Web 应用 → 运行 DAST
# Pipeline → Security tab → 应显示 XSS 等动态扫描结果

# 验证4：查看安全 Dashboard
# Security → Vulnerability Report
# 按严重级别、状态（Detected/Confirmed/Dismissed/Resolved）筛选
```

## 4. 项目总结

### 扫描类型对比

| 扫描类型 | 阶段 | 检测内容 | 速度 | 必需环境 |
|---------|------|---------|------|---------|
| SAST | 代码提交时 | 源码中的安全漏洞 | 快（分钟级） | 无 |
| Secret Detection | 代码提交时 | 硬编码密钥/Token | 快（分钟级） | 无 |
| Dependency Scanning | 代码提交时 | 第三方依赖漏洞 | 中 | 无 |
| Container Scanning | 镜像构建后 | 容器镜像层漏洞 | 中 | 镜像 |
| DAST | 部署后 | 运行时漏洞 | 慢（小时级） | 运行中的应用 |

### 适用场景

- **SAST + Secret Detection**：所有项目都应启用——零成本，高收益
- **Dependency Scanning**：有大量第三方依赖的项目（npm、pip、maven）
- **Container Scanning**：使用 Docker 部署的项目
- **DAST**：Web 应用、有用户输入的 API（成本较高，部分 EE）

**不适用场景**：
- 纯库/CLI 工具（DAST 无法扫描）
- 没有第三方依赖的小型内部脚本

### 注意事项

- **SAST 有误报**：不要一看到漏洞就 panic，需要人工确认——GitLab 支持 "Dismiss" 和 "Create Issue" 操作
- **Secret Detection 不是 100% 准确**：某些密钥格式无法识别，建议配合 `.gitignore` + Pre-commit Hook
- **DAST 扫描会对目标产生实际请求**：不要在 Production 环境直接跑 DAST！用 Review/Staging 环境
- **定期全量扫描**：SAST 和 Secret Detection 应该同时配置定时 Pipeline（`schedule`），不仅限于 MR

### 常见踩坑经验

1. **SAST 扫描覆盖不到某些文件**：误以为 SAST 会自动扫描所有代码。根因：`SAST_EXCLUDED_PATHS` 配置过宽或 SAST analyzer 的语言支持不完整。解决：检查排除列表，确保覆盖了所有主要语言的分析器。
2. **Secret Detection 在已清理的历史中仍报警**：使用 BFG 清理密钥后，Secret Detection 报告的漏洞还在。根因：Vulnerability Report 中的状态不会自动更新。解决：手动将旧漏洞标记为 "Resolved" 或 "Dismissed"。
3. **DAST 扫描超时或结果为空**：DAST 挂起很久没有结果。根因：目标 URL 不可达或 DAST 脚本被 WAF/防火墙拦截。解决：检查 `DAST_WEBSITE` 变量，确保从 GitLab Runner 可以访问目标 URL。

### 思考题

1. GitLab SAST 支持 20+ 种语言的安全分析器。如果在一个多语言项目中（Go 后端 + TypeScript 前端），如何确保两种语言的代码都被扫描到？
2. Secret Detection 的 `SECRET_DETECTION_HISTORIC_SCAN` 会扫描所有历史 commit，对大仓库可能非常耗时。如何在效率和覆盖度之间权衡？

> 答案见附录 D。

### 推广计划提示

- **开发**：SAST 和 Secret Detection 应该成为代码提交的"基础设施"，就像 ESLint 一样自然
- **安全团队**：GitLab 的 Vulnerability Report 可以替代部分商业安全扫描工具的工作
- **管理**：安全扫描报告是合规审计（SOC2/ISO27001）的重要证据来源
