# 第19章：GitHub Actions 与 Pull Request 装饰

## 1. 项目背景

**业务场景**：某开源项目的维护者管理着一个拥有 200+ 贡献者的微服务框架。每天有 15-20 个 Pull Request 提交，维护者团队只有 5 人。审查每个 PR 平均需要 45 分钟——其中 20 分钟用在"手动运行 SonarQube 检查并比对结果"上。

更棘手的是：项目代码托管在 GitHub 上，但 SonarQube 部署在公司内网。外部贡献者的 PR 无法触发内网的 SonarQube 扫描——这意味着所有 PR 的代码质量完全依赖维护者的肉眼审查。

维护者决定将 SonarQube 迁移到公网可访问的 SonarCloud（公有云版），并将质量检查嵌入 GitHub Actions 工作流。目标很明确：每个 PR 提交后自动触发扫描，扫描结果直接展示在 PR 页面——如果质量不达标，PR 不能被合并。

**痛点放大**：

- **外部贡献者无法触发扫描**：Fork 的 PR 不能访问内网 SonarQube
- **审查者负担过重**：需要手动比对和判断代码质量问题
- **Token 安全敏感**：GitHub Actions 的 Secret 需要安全配置，避免泄露给 Fork PR
- **SonarCloud vs 自建 SonarQube**：公网 vs 内网的选择和配置差异

## 2. 项目设计

### 剧本式交锋对话

---

**小胖**（在 GitHub 上看到一个 PR 页面底部自动展示了 SonarQube 的质量检查结果）："大师！这个 PR 下面有个绿色勾勾写着 'SonarCloud Code Analysis — Quality Gate passed'。这怎么做到的？是不是要装什么 App？"

**大师**："这是 SonarCloud 的 GitHub App 集成。SonarCloud 提供了一个 GitHub App——你把它安装到你的 GitHub 组织后，它就会监听 PR 事件、自动运行扫描、并把结果回写到 PR 的 Checks 区域。你在 PR 页面看到的那个绿色勾勾就是 GitHub Checks API 的产物。"

**小白**："如果我用的是自建的 SonarQube 而不是 SonarCloud，能实现同样的效果吗？"

**大师**："能，但需要一些手动配置。SonarQube 自建实例需要配置 GitHub App 或 Personal Access Token，然后通过 GitHub Actions 工作流中的 Action 来协调扫描和结果回写。流程是：

1. GitHub Actions 触发 → 执行 SonarScanner（使用 `sonar-scanner` 或 Maven/Gradle 插件）
2. SonarScanner 上传报告 → SonarQube CE 处理 → 返回 Quality Gate 状态
3. SonarQube 通过 GitHub API 将结果回写到 PR 的 Checks 区域
4. 在 GitHub 的 Branch Protection 规则中配置 'Require status checks to pass before merging'"

**小胖**："但我听说 GitHub Actions 对 Fork PR 有 Token 限制——外部贡献者的 PR 可能访问不到我们配置的 Secret？"

**大师**："对。这是 GitHub Actions 的安全设计——Fork PR 的 `GITHUB_TOKEN` 只有只读权限，且 Actions Secrets 不会传递给 Fork PR 的工作流。这意味着：

- **Fork PR 不能直接访问你的 SONAR_TOKEN**
- 需要设计安全的扫描触发方式

两种解决方案：
1. **使用 SonarCloud GitHub App**（最简单）：SonarCloud 的 App 在 PR 中作为一个外部 Check 运行，不需要访问你的 Secret。
2. **使用 `pull_request_target` 事件**（高级）：在安全的工作流上下文中运行扫描，但需要非常小心配置，避免 Token 泄露。

对于开源项目，方案 1 是标准答案。"

---

## 3. 项目实战

### 3.1 环境准备

- GitHub 仓库
- SonarQube 实例（公网可访问）或 SonarCloud 账号
- GitHub 仓库的管理员权限

### 3.2 分步实现

**方案 A：使用 SonarCloud GitHub App（推荐用于开源项目）**

**步骤 A1：将 SonarCloud App 安装到 GitHub 组织**

1. 登录 [SonarCloud](https://sonarcloud.io/)
2. Import organization（选择你的 GitHub 组织）
3. 授权 SonarCloud 安装 GitHub App
4. 在 SonarCloud 中选择要分析的项目

5. 在 GitHub 仓库 → Settings → Branches → Branch protection rules
6. 添加规则，勾选 "Require status checks to pass before merging"
7. 搜索 "SonarCloud Code Analysis"，选中

**步骤 A2：配置 `.github/workflows/build.yml`（SonarCloud）**

```yaml
name: SonarCloud Analysis
on:
  push:
    branches: [ main ]
  pull_request:
    types: [opened, synchronize, reopened]

jobs:
  sonarcloud:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Set up JDK 17
        uses: actions/setup-java@v4
        with:
          java-version: '17'
          distribution: 'temurin'

      - name: Cache Maven packages
        uses: actions/cache@v4
        with:
          path: ~/.m2
          key: ${{ runner.os }}-m2-${{ hashFiles('**/pom.xml') }}

      - name: Build and analyze
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}
        run: mvn -B verify sonar:sonar -Dsonar.organization=my-org -Dsonar.projectKey=my-project
```

**方案 B：自建 SonarQube + GitHub Actions（内网场景）**

**步骤 B1：配置 GitHub Secrets**

GitHub 仓库 → Settings → Secrets and variables → Actions → New repository secret：

| Secret Name | Value |
|------------|-------|
| `SONAR_HOST_URL` | `https://sonarqube.company.com` |
| `SONAR_TOKEN` | `squ_xxxxxxxxxxxxxx` |

**步骤 B2：创建 `.github/workflows/sonarqube.yml`**

```yaml
name: SonarQube Analysis

on:
  push:
    branches: [ main ]
  pull_request:
    types: [opened, synchronize, reopened]

jobs:
  analysis:
    name: SonarQube Scan
    runs-on: ubuntu-latest

    steps:
      - name: Checkout
        uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Set up JDK 17
        uses: actions/setup-java@v4
        with:
          java-version: '17'
          distribution: 'temurin'

      - name: Cache Maven dependencies
        uses: actions/cache@v4
        with:
          path: ~/.m2/repository
          key: ${{ runner.os }}-maven-${{ hashFiles('**/pom.xml') }}

      - name: Build & Test
        run: mvn -B clean verify -DskipITs

      - name: SonarQube Scan
        env:
          SONAR_HOST_URL: ${{ secrets.SONAR_HOST_URL }}
          SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}
        run: |
          mvn sonar:sonar \
            -Dsonar.host.url=$SONAR_HOST_URL \
            -Dsonar.token=$SONAR_TOKEN \
            -Dsonar.qualitygate.wait=true

      - name: Check Quality Gate
        id: quality-gate
        if: always()
        env:
          SONAR_HOST_URL: ${{ secrets.SONAR_HOST_URL }}
          SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}
        run: |
          # 等待 CE 处理完成
          sleep 10
          # 查询 Quality Gate 状态
          RESULT=$(curl -s -u "$SONAR_TOKEN:" \
            "$SONAR_HOST_URL/api/qualitygates/project_status?projectKey=com.company:my-project&branch=${{ github.head_ref }}")
          STATUS=$(echo $RESULT | python3 -c "import sys,json;print(json.load(sys.stdin)['projectStatus']['status'])")

          if [ "$STATUS" != "OK" ]; then
            echo "Quality Gate Failed: $STATUS"
            echo "## Quality Gate: $STATUS" >> $GITHUB_STEP_SUMMARY
            exit 1
          else
            echo "Quality Gate Passed"
            echo "## Quality Gate: Passed" >> $GITHUB_STEP_SUMMARY
          fi
```

**步骤 B3：配置 Branch Protection 规则**

GitHub 仓库 → Settings → Branches → Add rule：

- Branch name pattern: `main`
- ✅ Require a pull request before merging
- ✅ Require status checks to pass before merging
  - 搜索 "SonarQube Scan"，选中
- ✅ Require branches to be up to date before merging

**步骤 3：前端项目的 GitHub Actions**

`.github/workflows/frontend-sonar.yml`：

```yaml
name: Frontend SonarQube

on:
  push:
    branches: [ main ]
  pull_request:
    types: [opened, synchronize, reopened]

jobs:
  sonarqube:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - uses: actions/setup-node@v4
        with:
          node-version: '20'
          cache: 'npm'

      - name: Install dependencies
        run: npm ci

      - name: Run tests with coverage
        run: npx jest --coverage

      - name: SonarQube Scan
        uses: sonarsource/sonarqube-scan-action@v2
        env:
          SONAR_HOST_URL: ${{ secrets.SONAR_HOST_URL }}
          SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}
        with:
          args: >
            -Dsonar.projectKey=com.company:frontend
            -Dsonar.sources=src
            -Dsonar.javascript.lcov.reportPaths=coverage/lcov.info

      - name: Quality Gate Check
        uses: sonarsource/sonarqube-quality-gate-action@v1
        timeout-minutes: 5
        env:
          SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}
```

### 3.3 PR Decoration 配置

对于自建 SonarQube（Developer Edition 以上），配置 GitHub PR Decoration：

1. SonarQube → Administration → Configuration → General → Pull Requests
2. 选择 GitHub，填写：
   - GitHub API URL: `https://api.github.com`
   - GitHub App ID / Private Key（建议使用 GitHub App）
   - 或使用 Personal Access Token
3. 点击 Save

配置后在 PR 页面的 Files Changed 中，SonarQube 会在新增 Issue 所在代码行添加内联评论。

### 3.4 验证

1. 创建一个 PR（包含一个除零风险的代码）
2. 观察 GitHub Actions 运行结果
3. 确认 PR 的 Checks 区域出现 "SonarQube Scan — Failed"
4. 确认 Merge 按钮变灰（Branch Protection 生效）

---

## 4. 项目总结

### 4.1 SonarCloud vs 自建 SonarQube + GitHub Actions

| 维度 | SonarCloud | 自建 SonarQube + Actions |
|------|-----------|-------------------------|
| 部署成本 | 零（云端开箱即用） | 需要维护服务器 |
| 开源项目支持 | ✅ 免费（public repo） | ❌ 需要公网暴露 |
| PR Decoration | ✅ 原生 App 支持 | 🟡 需要 Developer+ 版本 |
| 规则定制 | ✅ 支持 | ✅ 支持 |
| 数据控制 | ☁️ 云端存储 | 🏠 本地存储 |
| 适用场景 | 开源项目、小型团队 | 企业内网、合规要求 |

### 4.2 适用场景

- **所有 GitHub 托管的项目**
- **开源项目的免费质量检查**
- **需要 Branch Protection 强制执行质量门禁的团队**

### 4.3 注意事项

1. **Fork PR 安全**：`pull_request` 事件对 Fork PR 不暴露 Secrets，需要改用 `pull_request_target`（需极谨慎配置）。
2. **`fetch-depth: 0`**：必须设置，确保 SonarQube 有完整的 Git 历史来计算 New Code。
3. **Job 超时**：SonarScanner 下载分析器可能耗时，建议设置 `timeout-minutes: 15`。
4. **官方 Action**：推荐使用 `sonarsource/sonarqube-scan-action` 和 `sonarsource/sonarqube-quality-gate-action`，它们比手写 shell 脚本更可靠。

### 4.4 常见踩坑经验

**故障 1：GitHub Actions 中 SonarScanner 提示 "Not authorized"**

根因：`SONAR_TOKEN` 未正确传递。检查 Secrets 是否在正确的级别配置（Repository secrets vs Organization secrets），以及 Fork PR 是否无法访问 Secrets。

**故障 2：PR 页面看不到 SonarQube comment，但 Actions 运行成功了**

根因：PR Decoration 需要 SonarQube Developer Edition 以上。Community Edition 不支持 PR Decoration——但可以使用本章 B 方案中手写的 comment 发布脚本。

### 4.5 思考题

1. GitHub Actions 中 `pull_request` 和 `pull_request_target` 事件的 Token 权限差异是什么？如何为 Fork PR 安全地实现 SonarQube 扫描？
2. 如果一个 PR 修改了 10 个文件，但只有 2 个文件新增了 Issue，如何在 GitHub PR 的 Files Changed 页面精确显示这 2 个文件的 Issue？

> **答案提示**：第1题 `pull_request_target` 使用 Target 分支的 Secrets 权限，可用于 Fork PR 但需极其小心（防止恶意代码执行）。第2题需要 PR Decoration 功能（商业版）。

---

> **推广计划提示**：GitHub Actions 集成是开源项目和 GitHub 优先团队的"标准配置"。如果团队使用 GitHub，强烈建议将 `.github/workflows/sonarqube.yml` 作为项目模板的一部分，新建项目时自动包含。质量负责人可以利用 GitHub 的 "Required status checks" 功能，确保没有任何项目能绕过 SonarQube 门禁。
