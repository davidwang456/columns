# 第18章：GitLab CI 与 Merge Request 质量治理

## 1. 项目背景

**业务场景**：某制造企业的工业物联网平台使用 GitLab 作为代码仓库和 CI/CD 平台。团队有 40 名开发者在 12 个微服务上并行开发，每天产生 80+ 个 Merge Request。当前的代码审查流程极度依赖人工——审查者需要在 GitLab 的 MR 页面和 SonarQube 的 Web UI 之间来回切换查看质量报告。

更糟糕的是，审查者经常忘记查看 SonarQube——大约 30% 的 MR 在 SonarQube 标记了 Blocker Bug 的情况下仍然被合并。这直接导致了上个月的一个线上故障：一个 NPE 被 SonarQube 标记为 Blocker Bug，但审查者没有注意到，代码进入了生产环境，导致订单服务崩溃 2 小时。

技术负责人提出：能不能在 GitLab 的 MR 页面上直接看到 SonarQube 的质量检查结果？甚至，如果质量门禁不通过，MR 就不能合并？

**痛点放大**：

- **审查上下文断裂**：代码在 GitLab 上，质量报告在 SonarQube 上，信息不在同一个屏幕
- **门禁不强制**：即使 SonarQube 报告了问题，GitLab 的合并按钮仍然可点击
- **MR 和 Branch 扫描混淆**：开发者不清楚 MR 扫描和主分支扫描的区别
- **Community Edition 限制**：GitLab CE 无内置 PR Decoration，需要社区方案弥补

## 2. 项目设计

### 剧本式交锋对话

---

**小胖**（在 GitLab 的 MR 页面看到 SonarQube comment 机器人自动留言）:"大师！我看到 MR 页面多了个 SonarQube 的评论——'Quality Gate Failed: New Bugs > 0'。这是怎么做到的？是 GitLab 官方功能吗？"

**大师**："这是 SonarQube 的 Branch Analysis 功能配合 GitLab 的 Pipeline 实现的。具体流程是这样的：当你创建一个 MR（源分支 → 目标分支），CI Pipeline 触发扫描时，SonarScanner 会自动将分支信息（源分支名、目标分支名）上传给 SonarQube。SonarQube 对比两个分支的代码快照，找出新增的问题，然后在 MR 中发布评论。

这个流程在 GitLab 中需要两部分配合：

1. **SonarScanner 在 MR 分支上执行**：通过 `sonar.branch.name` 和 `sonar.branch.target` 告诉 SonarQube '这是哪个 MR'
2. **SonarQube 通过 GitLab API 发布评论**：需要配置 GitLab Personal Access Token 和 GitLab API 地址"

**小白**："我看到 GitLab 有两种类型的 Pipeline——Branch Pipeline 和 Merge Request Pipeline。SonarQube 应该在哪一种里运行？"

**大师**："推荐使用 **Merge Request Pipeline**（`only: [merge_requests]`）。理由：

- Branch Pipeline 在每次 push 时都触发，包括 push 到 feature 分支早期的 commit——但这些 commit 可能还不完整，扫描它们意义不大。
- MR Pipeline 只在 MR 创建或更新时触发，这通常意味着代码已经准备好被审查了。

简化的 `.gitlab-ci.yml`：

```yaml
sonarqube-check:
  only:
    - merge_requests
  script:
    - mvn verify sonar:sonar
      -Dsonar.branch.name=$CI_MERGE_REQUEST_SOURCE_BRANCH_NAME
      -Dsonar.branch.target=$CI_MERGE_REQUEST_TARGET_BRANCH_NAME
```

GitLab 会自动提供 `$CI_MERGE_REQUEST_SOURCE_BRANCH_NAME` 和 `$CI_MERGE_REQUEST_TARGET_BRANCH_NAME`。"

**小胖**："那 GitLab CE（免费版）能用吗？我们公司用的是 CE。"

**大师**："GitLab CE 的限制是不支持 Pipeline 中的 `only: [merge_requests]` 规则（这是 Premium 特性）。但你可以通过以下方式绕过：

1. 用 `only: [branches]` + 检测当前是否为 MR 环境变量
2. 或者：在非 MR 的分支上也跑扫描（浪费一些 CI 资源，但功能上可行）
3. PR Decoration（在 MR 页面评论 SonarQube 结果）在 CE 中也需要额外实现：要么用 GitLab API 调用 SonarQube Web API 手动发布评论，要么使用社区脚本如 `sonar-gitlab-comment`。

Community Edition 的替代方案我会在实战部分详细演示。"

---

## 3. 项目实战

### 3.1 环境准备

- GitLab 实例（自托管或 GitLab.com）
- SonarQube 实例（Community or Developer Edition）
- GitLab 项目已创建

### 3.2 分步实现

**步骤 1：配置 GitLab CI 变量**

在 GitLab 项目 → Settings → CI/CD → Variables 中添加：

| Key | Value | Type | Protected |
|-----|-------|------|-----------|
| `SONAR_HOST_URL` | `http://your-sonarqube:9000` | Variable | No |
| `SONAR_TOKEN` | `squ_xxxxxxxxxxxxxxx` | Variable | Yes (Masked) |
| `GITLAB_TOKEN` | `glpat-xxxxxxxxxxxx` | Variable | Yes (Masked) |

> `GITLAB_TOKEN` 需要 `api` 权限，用于 SonarQube 通过 GitLab API 发布 MR 评论。

**步骤 2：编写 `.gitlab-ci.yml`（GitLab Premium/Ultimate）**

```yaml
# .gitlab-ci.yml
stages:
  - build
  - test
  - sonarqube
  - deploy

variables:
  MAVEN_OPTS: "-Dmaven.repo.local=$CI_PROJECT_DIR/.m2/repository"

cache:
  paths:
    - .m2/repository

build-and-test:
  stage: test
  image: maven:3.9-eclipse-temurin-17
  script:
    - mvn clean verify -DskipITs
  artifacts:
    paths:
      - target/
    expire_in: 1 hour
  only:
    - merge_requests

sonarqube-check:
  stage: sonarqube
  image: maven:3.9-eclipse-temurin-17
  dependencies:
    - build-and-test
  script:
    - mvn sonar:sonar
      -Dsonar.host.url=$SONAR_HOST_URL
      -Dsonar.token=$SONAR_TOKEN
      -Dsonar.branch.name=$CI_MERGE_REQUEST_SOURCE_BRANCH_NAME
      -Dsonar.branch.target=$CI_MERGE_REQUEST_TARGET_BRANCH_NAME
      -Dsonar.qualitygate.wait=true
  only:
    - merge_requests
  allow_failure: false
```

关键参数：
- `sonar.qualitygate.wait=true`：让 SonarScanner 等待 CE 处理完成再退出。这样 GitLab Pipeline 可以直接通过 Scanner 的退出码判断门禁结果。
- `-Dsonar.branch.name` 和 `-Dsonar.branch.target`：告诉 SonarQube 这是 MR 上下文扫描。

**步骤 3：编写 `.gitlab-ci.yml`（GitLab Community Edition 方案）**

```yaml
# .gitlab-ci.yml for CE
stages:
  - test
  - sonarqube
  - report

build-and-test:
  stage: test
  image: maven:3.9-eclipse-temurin-17
  script:
    - mvn clean verify -DskipITs
  artifacts:
    paths:
      - target/
    expire_in: 1 hour
  only:
    - branches

sonarqube-check:
  stage: sonarqube
  image: maven:3.9-eclipse-temurin-17
  dependencies:
    - build-and-test
  script:
    - |
      # CE 版本：手动构造 branch 参数
      if [ -n "$CI_MERGE_REQUEST_IID" ]; then
        mvn sonar:sonar \
          -Dsonar.host.url=$SONAR_HOST_URL \
          -Dsonar.token=$SONAR_TOKEN \
          -Dsonar.branch.name=$CI_COMMIT_REF_NAME \
          -Dsonar.branch.target=$CI_MERGE_REQUEST_TARGET_BRANCH_NAME \
          -Dsonar.qualitygate.wait=true
      else
        mvn sonar:sonar \
          -Dsonar.host.url=$SONAR_HOST_URL \
          -Dsonar.token=$SONAR_TOKEN \
          -Dsonar.qualitygate.wait=true
      fi
  only:
    - branches

post-sonar-report:
  stage: report
  image: alpine/curl
  script:
    - |
      if [ -n "$CI_MERGE_REQUEST_IID" ]; then
        # 1. 获取 Quality Gate 状态
        QG_STATUS=$(curl -s -u "$SONAR_TOKEN:" \
          "$SONAR_HOST_URL/api/qualitygates/project_status?projectKey=com.company:my-project&branch=$CI_COMMIT_REF_NAME" \
          | python3 -c "import sys,json;print(json.load(sys.stdin)['projectStatus']['status'])")

        # 2. 获取新增 Issue 摘要
        ISSUE_SUMMARY=$(curl -s -u "$SONAR_TOKEN:" \
          "$SONAR_HOST_URL/api/issues/search?projectKeys=com.company:my-project&branch=$CI_COMMIT_REF_NAME&statuses=OPEN&ps=3" \
          | python3 -c "
import sys,json
data = json.load(sys.stdin)
for i in data['issues'][:3]:
    print(f\"- [{i['severity']}] {i['type']}: {i['message'][:80]}\")
")

        # 3. 在 GitLab MR 下添加评论
        curl -X POST \
          "https://gitlab.com/api/v4/projects/$CI_PROJECT_ID/merge_requests/$CI_MERGE_REQUEST_IID/notes" \
          -H "PRIVATE-TOKEN: $GITLAB_TOKEN" \
          -H "Content-Type: application/json" \
          -d "{\"body\": \"## SonarQube Quality Gate: $QG_STATUS\n\n$ISSUE_SUMMARY\n\n[View full report]($SONAR_HOST_URL/dashboard?id=com.company:my-project&branch=$CI_COMMIT_REF_NAME)\"}"
      fi
  only:
    - merge_requests
```

### 3.3 前端项目的 `.gitlab-ci.yml`

```yaml
frontend-sonar:
  stage: sonarqube
  image: node:20-alpine
  cache:
    paths:
      - node_modules/
  script:
    - npm ci
    - npx jest --coverage
    - |
      npx sonar-scanner \
        -Dsonar.host.url=$SONAR_HOST_URL \
        -Dsonar.token=$SONAR_TOKEN \
        -Dsonar.projectKey=com.company:frontend \
        -Dsonar.sources=src \
        -Dsonar.javascript.lcov.reportPaths=coverage/lcov.info \
        -Dsonar.branch.name=$CI_COMMIT_REF_NAME
  only:
    - merge_requests
```

### 3.4 配置 GitLab MR 合并规则

在 GitLab 项目 → Settings → Merge requests → Merge checks：
- ✅ Pipelines must succeed
- ✅ All discussions must be resolved

在 Settings → Merge requests → Merge request approvals：
- 添加 Approval Rule，指定至少 1 人审批
- 如果使用 Premium 以上版本，可添加 MR Approval 与 Quality Gate 联动

### 3.5 验证

```bash
# 创建一个 MR，观察 Pipeline 行为
# 1. git checkout -b feature/test-mr
# 2. 修改代码（引入一个除零风险）
# 3. git push + 创建 MR
# 4. 观察 Pipeline 中 sonarqube-check job 的状态
# 5. 观察 MR 页面是否有 SonarQube 评论
```

---

## 4. 项目总结

### 4.1 GitLab CI vs Jenkins 集成对比

| 维度 | GitLab CI | Jenkins |
|------|----------|---------|
| 配置方式 | `.gitlab-ci.yml` 在仓库中 | Jenkinsfile 在仓库中 |
| MR 上下文 | 原生 `$CI_MERGE_REQUEST_*` 变量 | 需要插件支持 |
| PR Decoration | 需要商业版或手动实现 | 通过插件 |
| 学习成本 | 低（GitLab 内一站式） | 中（需要理解 Jenkins 概念） |
| 灵活性 | 中（受 YAML 语法限制） | 高（Groovy 脚本） |

### 4.2 适用场景

- **GitLab 为主要 DevOps 平台的团队**
- **多项目/多分支的代码审查流程**
- **需要 MR 级别质量检查的场景**

### 4.3 注意事项

1. **`sonar.qualitygate.wait=true` 的性能影响**：Scanner 会阻塞等待 CE 完成（默认最长 300 秒），可能导致 GitLab Pipeline 运行时间增加。
2. **CE 版本的 PR Comment 功能不稳定**：需要维护自定义脚本，且不同 GitLab 版本的 API 签名可能变化。
3. **并发 MR 扫描管理**：多个 MR 同时扫描时注意 CE 和数据库压力。

### 4.4 常见踩坑经验

**故障 1：MR Pipeline 不触发**

根因：`.gitlab-ci.yml` 中 `only: [merge_requests]` 不会在分支 push 时触发，只会随 MR 创建/更新触发。如果是 CE 版本（不支持 `merge_requests` 触发），检查是否正确配置了 `rules` 或替代方案。

**故障 2：SonarQube 报告的分支名称不正确**

根因：`$CI_COMMIT_REF_NAME` 在 Pipeline for Merge Result 场景下指向一个临时 ref（`refs/merge-requests/X/merge`），不是实际分支名。解决：使用 `$CI_MERGE_REQUEST_SOURCE_BRANCH_NAME`（需要 Premium）。

### 4.5 思考题

1. GitLab CI 中，`sonar.qualitygate.wait=true` 和 Pipeline 中的 timeout 设置应该如何配合，防止 Scanner 无限等待？
2. Community Edition 中，如何确保 MR 的 SonarQube 评论不会被重复发布（每次 CI 重新运行都发布一次）？

> **答案提示**：第1题应在 `script` 外设置 `timeout`（GitLab 的 `job:timeout`）。第2题通过检查 MR 已有 comment 的作者和时间戳，或使用幂等的 comment ID。

---

> **推广计划提示**：GitLab CI 集成的关键优势是"开发者不离开 MR 页面就能看到质量结果"。在推广时，重点展示"MR 页面上的 SonarQube 评论"这个效果——这比任何培训都更能体现质量左移的价值。质量负责人应为团队准备 `.gitlab-ci.yml` 模板，降低接入门槛。
