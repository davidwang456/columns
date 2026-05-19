# 第17章：GitHub Actions 集成与 Pull Request 安全门禁

> 版本：Trivy v0.50+
> 面向人群：开发、DevOps、安全工程师

---

## 1. 项目背景

### 业务场景

云帆科技的基础篇安全体系落地后，CTO 开始关注一个问题：「本地扫描做得再好，也只能管住『愿意扫』的人。如果有人在提交代码时故意绕过检查，或者干脆忘记扫描，怎么办？」

这个问题很快得到了验证。前端工程师小张为了赶一个紧急需求，直接修改了 `package.json` 引入了一个新依赖，本地测试通过后 Push 到主分支。三天后，安全团队发现这个依赖的间接依赖里包含一个已知的原型污染漏洞（CVE-2021-23337）。更尴尬的是，这个漏洞本可以在合并前被发现——PR 的 diff 里清楚地显示了 `package-lock.json` 的变更。

与此同时，公司有一个开源项目在 GitHub 上维护。某天早上，社区贡献者提交了一个 PR，修改了 Dockerfile 的基础镜像。维护者小胖看了一眼觉得「改动不大」，直接点了 Merge。结果这个「改动不大」的变更是将基础镜像从 `node:18-alpine` 换成了 `node:16-alpine`，后者包含一个 HIGH 级别的 OpenSSL 漏洞。

CTO 在复盘会上提出明确要求：「从代码变更到合并入库，中间必须有一道自动化的安全闸门。任何人——包括我自己——都不能绕过这道门。闸门要在 Pull Request 阶段运行，发现 Critical 漏洞就阻止合并，发现新增漏洞就评论到 PR 里。」

### 痛点放大

**第一，「人治」不可靠。** 靠代码审查者肉眼发现安全问题，既不高效也不稳定。审查者的安全知识参差不齐，面对几百行 lockfile 的变更，很难识别其中是否引入了有漏洞的版本。

**第二，反馈闭环太长。** 如果在合并后才发现漏洞，修复需要走「提修 → 测试 → 发版 → 部署」的完整流程，平均耗时一周以上。如果在 PR 阶段就发现，开发者可以在上下文还在脑子里的时候立即修复，平均耗时不到 30 分钟。

**第三，开源项目的信任危机。** 对于公开仓库，任何社区贡献者都可能提交带漏洞的代码。如果没有自动化检查，维护者要么「全部相信」（风险高），要么「全部怀疑」（伤害社区积极性）。

**第四，审计和举证困难。** 当安全事件发生后，团队需要回答「这个漏洞是什么时候引入的？」「谁批准的合并？」「当时为什么没有发现？」。没有自动化的门禁记录，这些问题只能依赖人工回忆，举证能力几乎为零。

**本章的核心目标是：在 GitHub Actions 中建立完整的 Trivy 安全门禁体系，实现「PR 提交即扫描、新增漏洞即评论、Critical 即阻断」的自动化闭环。**

---

## 2. 项目设计

**场景**：云帆科技的 GitHub 仓库设置会议，小胖（仓库维护者）、小白（DevOps）和大师（技术负责人）正在设计 PR 安全门禁。

---

**小胖**：「GitHub 上有个 Dependabot，不是能自动检测漏洞依赖吗？我们直接开那个不就行了？」

**小白**：「Dependabot 只能扫依赖版本，不能扫镜像漏洞、不能扫密钥泄露、不能扫配置错误。而且 Dependabot 对私有 Registry 和内部包的支持很差。Trivy 的优势是『全能』——一条流水线同时覆盖漏洞、密钥、配置。」

**大师**：「技术映射：Dependabot 就像小区的「门禁卡系统」，只能识别业主；Trivy 就像「安检门」，除了识别身份，还要检查有没有带违禁品。对于安全要求高的仓库，安检门是必不可少的。」

**小胖**：「那 Trivy 怎么接入 GitHub Actions？是每个仓库都要写一套 YAML 吗？」

**小白**：「Aqua 官方提供了 `aquasecurity/trivy-action`，这是一个封装好的 GitHub Action。你只需要在 `.github/workflows/` 里写几行配置，就能调用 Trivy。更妙的是，它支持 SARIF 格式输出，可以直接上传到 GitHub Security Advisories，在 GitHub 的「Security」Tab 里统一展示漏洞。」

**小胖**：「那评论功能呢？我想让 Trivy 在 PR 里直接告诉开发者『你引入了 3 个新漏洞』。」

**大师**：`trivy-action` 本身不直接发 PR 评论，但你可以结合 `github-script` 或专门的 Action（如 `actions/github-script`、`marocchino/sticky-pull-request-comment`）来实现。扫描完成后，解析 JSON 报告，提取新增漏洞，用 GitHub API 发到 PR 评论区。」

**小白**：「还有一个更优雅的方案：用 `reviewdog`。它可以对接 Trivy 的输出，把每个漏洞当作一条代码 review comment，精准定位到引入漏洞的那一行代码。比如 `package-lock.json` 里把 `lodash` 从 `4.17.20` 改成了 `4.17.15`，reviewdog 会在那一行评论「此版本存在 CVE-2021-23337」。」

**小胖**：「那阻断合并呢？如果扫描发现 Critical，怎么阻止 PR 被合并？」

**大师**：「技术映射：GitHub 有『分支保护规则』（Branch Protection Rules）。你可以设置：

1. **Require status checks to pass**：让 Trivy 扫描的 GitHub Actions Job 成为「必需状态检查」，Job 失败则合并按钮变灰。
2. **Require pull request reviews**：强制至少一个 reviewer 批准。
3. **Require conversation resolution**：要求所有 PR 评论（包括 Trivy 的评论）被标记为 Resolved 才能合并。

三重保护下，即使开发者想强行合并，也绕不过系统限制。」

**小胖**：「那如果我们确实需要紧急合并一个带漏洞的 PR 呢？比如线上故障回滚。」

**小白**：「分支保护规则允许「仓库管理员绕过」。但 GitHub 会记录「谁、在什么时间、以什么理由绕过」，这个记录本身就是审计证据。建议团队约定：只有 CTO 或值班负责人有权限紧急绕过，且必须在事后 24 小时内提交书面说明。」

**大师**：「最后一点：增量扫描。全量扫描每次都要分析所有依赖，对大型项目可能很慢。但 PR 阶段我们其实只关心『这次变更引入了什么新风险』。可以用 `git diff` 找出变更的文件，只对变更的 Dockerfile、lockfile、YAML 做定向扫描，速度提升 5 倍以上。」

---

## 3. 项目实战

### 环境准备

- **GitHub 仓库**：一个真实的代码仓库（公开或私有均可）
- **GitHub Token**：用于 PR 评论（需要 `pull-requests: write` 权限）
- **测试镜像/Dockerfile**：用于验证扫描效果

### 步骤一：基础 Trivy 扫描 Workflow

**目标**：在 PR 提交时自动触发 Trivy 扫描。

创建 `.github/workflows/trivy-pr-scan.yml`：

```yaml
name: Trivy PR Security Scan
on:
  pull_request:
    branches: [main, master]
    paths:
      - 'Dockerfile'
      - 'docker/**'
      - 'package*.json'
      - 'pom.xml'
      - 'go.mod'
      - 'requirements*.txt'
      - 'k8s/**'
      - '.github/workflows/**'

jobs:
  trivy-scan:
    name: Security Scan
    runs-on: ubuntu-latest
    permissions:
      contents: read
      security-events: write
      actions: read
    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Run Trivy vulnerability scanner
        uses: aquasecurity/trivy-action@master
        with:
          scan-type: 'fs'
          scanners: 'vuln,secret,misconfig'
          format: 'sarif'
          output: 'trivy-results.sarif'
          severity: 'HIGH,CRITICAL'
          exit-code: '1'

      - name: Upload SARIF results
        uses: github/codeql-action/upload-sarif@v2
        if: always()
        with:
          sarif_file: 'trivy-results.sarif'
```

**关键配置解读**：
- `on.pull_request.paths`：只在特定文件变更时触发，减少不必要的扫描。
- `permissions.security-events: write`：允许上传 SARIF 到 GitHub Security 面板。
- `exit-code: '1'`：发现 HIGH/CRITICAL 时 Job 失败，阻断合并。
- `if: always()`：即使扫描失败，也上传已发现的结果。

### 步骤二：PR 评论集成

**目标**：将扫描结果以评论形式发布到 PR。

```yaml
name: Trivy PR Comment
on:
  pull_request:
    branches: [main]

jobs:
  trivy-comment:
    runs-on: ubuntu-latest
    permissions:
      pull-requests: write
      contents: read
    steps:
      - uses: actions/checkout@v4

      - name: Run Trivy
        uses: aquasecurity/trivy-action@master
        with:
          scan-type: 'fs'
          scanners: 'vuln'
          format: 'json'
          output: 'trivy.json'
          severity: 'HIGH,CRITICAL'

      - name: Parse and comment
        uses: actions/github-script@v7
        with:
          script: |
            const fs = require('fs');
            const report = JSON.parse(fs.readFileSync('trivy.json', 'utf8'));
            
            let critical = 0, high = 0;
            const details = [];
            
            for (const result of report.Results || []) {
              for (const vuln of result.Vulnerabilities || []) {
                if (vuln.Severity === 'CRITICAL') critical++;
                if (vuln.Severity === 'HIGH') high++;
                details.push(`| ${vuln.VulnerabilityID} | ${vuln.PkgName} | ${vuln.InstalledVersion} | ${vuln.Severity} |`);
              }
            }
            
            const body = `## 🛡️ Trivy 安全扫描结果
            
            | 级别 | 数量 |
            |------|------|
            | CRITICAL | ${critical} |
            | HIGH | ${high} |
            
            ${details.length > 0 ? '### 详情\n\n| CVE | 包名 | 版本 | 级别 |\n|-----|------|------|------|\n' + details.slice(0, 20).join('\n') : '✅ 未发现 HIGH/CRITICAL 级别漏洞'}
            
            ${details.length > 20 ? '\n*... 仅展示前 20 条，完整报告请查看 Actions 产物。*' : ''}
            `;
            
            github.rest.issues.createComment({
              issue_number: context.issue.number,
              owner: context.repo.owner,
              repo: context.repo.repo,
              body: body
            });
```

### 步骤三：镜像扫描与 Dockerfile 变更检测

**目标**：当 Dockerfile 变更时，自动构建镜像并扫描。

```yaml
name: Trivy Image Scan on PR
on:
  pull_request:
    paths:
      - 'Dockerfile'
      - 'docker/**'

jobs:
  scan-image:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Build image
        run: docker build -t test-image:${{ github.sha }} .

      - name: Scan image with Trivy
        uses: aquasecurity/trivy-action@master
        with:
          image-ref: 'test-image:${{ github.sha }}'
          format: 'sarif'
          output: 'trivy-image-results.sarif'
          severity: 'HIGH,CRITICAL'
          exit-code: '1'

      - name: Upload results
        uses: github/codeql-action/upload-sarif@v2
        if: always()
        with:
          sarif_file: 'trivy-image-results.sarif'
```

### 步骤四：增量扫描优化

**目标**：只扫描变更引入的新风险，而非全量扫描。

```yaml
name: Trivy Incremental Scan
on:
  pull_request:
    branches: [main]

jobs:
  incremental:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Get changed files
        id: changed
        run: |
          echo "files=$(git diff --name-only origin/${{ github.base_ref }}...HEAD | tr '\n' ' ')" >> $GITHUB_OUTPUT

      - name: Scan changed files only
        uses: aquasecurity/trivy-action@master
        with:
          scan-type: 'fs'
          scan-ref: '.'
          scanners: 'vuln,secret,misconfig'
          severity: 'HIGH,CRITICAL'
          exit-code: '1'
          # Trivy 暂不支持单文件扫描，可配合 --skip-dirs 排除未变更目录
```

> **可能遇到的坑**：Trivy 的 `fs` 扫描目前不支持「只扫特定文件列表」。更精确的增量扫描需要配合自定义脚本，提取变更的 lockfile 行，用 `npm audit` / `pip-audit` 等工具做定向检查。

### 步骤五：配置分支保护规则

**目标**：确保 Trivy 扫描成为合并的硬性门槛。

**在 GitHub 仓库设置中操作**：

1. Settings → Branches → Add rule
2. Branch name pattern: `main`
3. 勾选：
   - 「Require a pull request before merging」
   - 「Require status checks to pass before merging」
   - 搜索并勾选 `trivy-scan`（或你的 Job 名称）
   - 「Require conversation resolution before merging」
4. 勾选：
   - 「Include administrators」（管理员也必须遵守）
   - 「Restrict pushes that create files larger than 100MB」（可选）

### 步骤六：SARIF 与 GitHub Security 面板集成

**目标**：让漏洞在 GitHub Security Tab 中可视化展示。

上传 SARIF 后，访问仓库的「Security → Code scanning alerts」，可以看到：

- 漏洞列表（按 Severity 排序）
- 漏洞详情（描述、修复版本、CVSS）
- 受影响的分支和提交
- 自动修复建议（如果可用）

**批量关闭已修复的告警**：

```bash
# 在 PR 合并后，GitHub 会自动重新扫描主分支
# 已修复的漏洞会被标记为 Closed
```

### 测试验证

1. 提交一个包含漏洞依赖的 PR，验证 Trivy Job 失败并阻断合并。
2. 验证 PR 评论区出现 Trivy 扫描结果摘要。
3. 修改 Dockerfile 后提交 PR，验证镜像构建和扫描被触发。
4. 修复漏洞后提交 PR，验证 Job 通过，合并按钮可用。
5. 检查 GitHub Security Tab，确认 SARIF 漏洞正确展示。

---

## 4. 项目总结

### 优点 & 缺点

| 维度 | 优点 | 缺点 |
|------|------|------|
| 集成深度 | 原生支持 SARIF 上传和 Security 面板 | 对超大仓库（>10GB）的扫描性能有限 |
| 自动化 | PR 提交即扫描，无需人工干预 | 误报可能导致正常 PR 被频繁阻断 |
| 反馈闭环 | 评论直接定位到引入漏洞的代码行 | 评论过多时可能淹没其他 review 意见 |
| 可审计 | GitHub Actions 日志完整保留 | 日志保留期有限（默认 90 天） |
| 开源友好 | 公开仓库免费使用所有功能 | 私有仓库的 Actions 分钟数有额度限制 |

### 适用场景

1. **代码合并安全门禁**：任何进入主分支的代码都必须通过安全扫描。
2. **依赖变更监控**：lockfile 变更时自动检测是否引入了新漏洞。
3. **镜像构建检查**：Dockerfile 变更时自动构建并扫描镜像。
4. **开源项目治理**：社区贡献者的 PR 自动接受安全检查，降低维护者负担。
5. **安全审计举证**：GitHub Actions 日志作为「何时发现漏洞」的客观证据。

**不适用场景**：
1. 需要扫描私有镜像仓库但无法配置 Registry 认证的场景——GitHub Actions 的 secrets 管理可以解决这个问题，但配置较复杂。
2. 需要亚秒级扫描反馈的实时协作场景——GitHub Actions 的启动和运行有分钟级延迟。

### 注意事项

- ** Secrets 安全**：不要在 workflow 中硬编码密码、Token。使用 GitHub Secrets（`secrets.XXX`）存储敏感信息。
- **SARIF 大小限制**：GitHub 对单次上传的 SARIF 文件有大小限制（约 100MB）。超大型项目的报告可能需要拆分或精简。
- **Actions 版本锁定**：`aquasecurity/trivy-action@master` 会自动跟随最新版本，可能导致行为突变。建议锁定到具体版本，如 `@0.20.0`。
- **并发执行限制**：GitHub Free 账户的并发 Actions Job 有限，大型团队可能需要付费升级。

### 常见踩坑经验

**踩坑案例 1：PR 评论重复发送**
- **现象**：每次 Push 到 PR 都新增一条 Trivy 评论，导致评论区刷屏。
- **根因**：Workflow 在每次 commit 时都触发 `createComment`。
- **解法**：使用 `marocchino/sticky-pull-request-comment` Action，它会更新同一条评论而不是新建。

**踩坑案例 2：SARIF 上传失败**
- **现象**：`upload-sarif` 步骤报错 `Invalid SARIF format`。
- **根因**：Trivy 生成的 SARIF 版本与 GitHub 期望的版本不兼容。
- **解法**：确保 Trivy 版本 >= 0.45，且 `codeql-action/upload-sarif` 使用 v2 版本。

**踩坑案例 3：私有 Registry 镜像扫描认证失败**
- **现象**：镜像扫描步骤报 `authentication required`。
- **根因**：GitHub Actions 没有登录私有 Registry。
- **解法**：在扫描前添加 `docker login` 步骤，使用 GitHub Secrets 存储 Registry 凭据。

### 思考题

1. 假设你的团队有 50 个 GitHub 仓库，每个仓库都需要配置类似的 Trivy Workflow。如何设计一个「中央模板」机制，使得安全团队更新扫描策略时，不需要逐个修改 50 个仓库的 YAML 文件？
2. GitHub Actions 的 `exit-code: 1` 会阻断所有带 HIGH/CRITICAL 漏洞的 PR。但在实际业务中，某些 HIGH 漏洞可能需要 3 天才能修复（如等待上游发布补丁）。请设计一个「渐进式门禁」方案：P0 立即阻断，P1 允许在获得安全团队批准后合并。

> **答案提示**：第 30 章「企业级策略即代码体系」将介绍跨仓库的策略统一管理和渐进式门禁方案。

---

> **推广计划**：本章是前端/后端开发组长和 DevOps 的必读内容。建议所有 GitHub 仓库统一启用 Trivy PR 扫描，将 `trivy-pr-scan.yml` 纳入团队仓库模板。安全团队负责维护 Workflow 版本和扫描策略，开发团队只需知道「提 PR → 等扫描 → 修漏洞 → 合并」的流程。开源项目维护者重点关注社区贡献者的 PR 安全评论，建立「自动化检查 + 人工 Review」的双重保障。
