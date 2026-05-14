# 第20章：Auto DevOps 进阶与定制

## 1. 项目背景

> **业务场景**：一家公司的 CTO 希望把所有项目都配上 CI/CD，但团队只有 3 个 DevOps 工程师，而公司有 40+ 个项目。手工为每个项目编写 `.gitlab-ci.yml` 根本不可能。CTO 听说 GitLab 有 Auto DevOps 功能——"推送代码，自动完成一切"——但实际启用后发现默认配置并不适用：自动部署到了 Kubernetes 但公司用的是 Docker Compose，自动启用了 Review Apps 但公司没有动态 DNS 配置，自动选的语言版本和公司标准不一致。

团队面临"全自动还是全定制"的困境：全用 Auto DevOps 默认值不够灵活，全手工定制又回到了人力不足的死循环。正确的路径是：理解 Auto DevOps 的原理和模板结构，然后对不适用的步骤做覆盖和定制。

**痛点放大**：Auto DevOps 不是"自动魔法的黑盒"，而是一整套预定义的 CI 模板，按语言自动检测并生成完整的 Pipeline。它的每一个阶段都可以被禁用、替换或扩展——前提是你理解它的模板继承机制和覆盖方式。会用了 Auto DevOps，你可以在 5 分钟内给一个新项目配上构建、测试、安全扫描、部署全流程。

## 2. 项目设计——剧本式交锋对话

**场景**：DevOps 团队讨论是否启用 Auto DevOps，有人力挺有人反对。

---

**小胖**："Auto DevOps 听起来太香了吧——不用写 `.gitlab-ci.yml`，自动检测语言，自动构建测试部署全搞定。我们是不是该全部切过去？"

**大师**："Auto DevOps 是好东西，但它的默认配置是为'最佳实践'场景设计的——自动检测到 Python 项目就用 Herokuish buildpack，部署默认推到 Kubernetes。如果你的项目技术栈和基础设施匹配这些默认值，确实可以用最低成本跑起来。但如果不匹配——比如你用的是 Docker Compose 部署，Auto DevOps 的 deploy 阶段就完全不适用。"

**小白**："那怎么知道哪些阶段适用，哪些不适用？"

**大师**："Auto DevOps 包含 13 个阶段：build → test → code_quality → SAST → dependency_scanning → license_scanning → container_scanning → dast → review → deploy → monitoring → defsec。每个阶段都可以通过 CI 变量 `AUTO_DEVOPS_<STAGE>_DISABLED` 来关闭。比如你不想要 Kubernetes deploy，就在 CI 变量里设 `AUTO_DEVOPS_DEPLOY_DISABLED: 'true'`。"

**小胖**："那如果我只想要用它的 test 和 SAST，但 build 和 deploy 用我自己的定制逻辑呢？"

**大师**："这正是 Auto DevOps 的精髓——你可以叠加自定义。在你的 `.gitlab-ci.yml` 中先 `include` Auto DevOps 模板，然后定义同名的 job 覆盖默认实现，或者用 `extends` 继承默认 job 并添加你自己的步骤。技术映射——Auto DevOps 就像一套乐高基础积木套装，你可以用所有积木（全自动），也可以只拿其中的测试和安全积木，用自己的构建和部署积木替换。"

**小白**："Auto DevOps 和 CI 模板工程化（第19章）有什么区别？感觉功能重叠了。"

**大师**："定位不同。Auto DevOps 是 GitLab 官方提供的'零配置'方案，适合新项目快速上 CI/CD，或对 CI/CD 要求简单的项目。CI 模板工程化是你自己构建的'有配置'方案，适合已有成熟 CI/CD 标准的团队。技术映射——Auto DevOps 是酒店自助早餐（固定菜单，但可以跳过不喜欢的菜），CI 模板工程化是你自己买菜做早餐（完全自定义，但需要会做饭）。"

---

## 3. 项目实战

### 环境准备

> **目标**：为一个 Node.js 项目启用 Auto DevOps，关闭不适用的阶段，自定义部署方式。

**前置条件**：GitLab CE 17.x，Kubernetes 集群（可选，用于默认部署）。

### 分步实现

#### 步骤1：启用 Auto DevOps 并观察默认行为

**目标**：查看 Auto DevOps 默认生成的完整 Pipeline。

```bash
# 方式A：通过 GitLab UI 启用
# Project → Settings → CI/CD → Auto DevOps
# → 勾选 "Default to Auto DevOps pipeline"
# → 部署策略：选择 "Automatic deployment to staging, manual deployment to production"
# → Save changes

# 方式B：通过 API 启用
curl --request PUT \
  --header "PRIVATE-TOKEN: $GITLAB_TOKEN" \
  --header "Content-Type: application/json" \
  --data '{"enabled_auto_devops": true}' \
  "$GITLAB_URL/api/v4/projects/$PROJECT_ID"

# 方式C：在项目中创建 .gitlab-ci.yml（最小化）
cat > .gitlab-ci.yml << 'EOF'
# 启用 Auto DevOps（包含所有默认阶段）
include:
  - template: Auto-DevOps.gitlab-ci.yml

# 可选：只定义你需要的变量
variables:
  AUTO_DEVOPS_DEPLOY_DISABLED: "true"   # 先禁用部署
EOF

# 提交后观察 Pipeline
# 会自动检测 Node.js 并执行 build → test → code_quality → SAST
```

#### 步骤2：禁用不适用的阶段

**目标**：保留构建、测试、安全扫描；禁用 Kubernetes 部署、Review Apps、监控。

```yaml
# .gitlab-ci.yml - Auto DevOps 定制版
include:
  - template: Auto-DevOps.gitlab-ci.yml

variables:
  # ===== 启用的阶段 =====
  # build, test, code_quality, SAST —— 默认启用

  # ===== 禁用的阶段 =====
  # 不用 Kubernetes 部署
  AUTO_DEVOPS_DEPLOY_DISABLED: "true"
  # 不用 Review Apps
  AUTO_DEVOPS_REVIEW_DISABLED: "true"
  # 不用监控（Prometheus）
  AUTO_DEVOPS_MONITORING_DISABLED: "true"
  # 不用动态安全测试（需要运行中的应用）
  AUTO_DEVOPS_DAST_DISABLED: "true"
  # 不用许可证合规扫描
  AUTO_DEVOPS_LICENSE_SCANNING_DISABLED: "true"
  # 不用依赖扫描（SAST 已覆盖）
  AUTO_DEVOPS_DEPENDENCY_SCANNING_DISABLED: "true"

  # ===== 构建参数定制 =====
  # 指定 Node.js 版本（覆盖自动检测）
  AUTO_DEVOPS_BUILD_IMAGE_CNB_ENABLED: "false"  # 不用 Cloud Native Buildpacks
  AUTO_DEVOPS_BUILD_IMAGE: "node:20-alpine"       # 使用自己的构建镜像

  # ===== 测试参数定制 =====
  TEST_DISABLED: "false"
  # 关闭代码质量（ESLint 单独跑，不走 CodeClimate）
  CODE_QUALITY_DISABLED: "true"

  # ===== 安全扫描定制 =====
  SAST_DISABLED: "false"
  SAST_EXCLUDED_PATHS: "spec, test, tests, tmp, vendor"
  SAST_BRAKEMAN_LEVEL: 2
```

#### 步骤3：自定义构建和部署——叠加定制 job

**目标**：保留 Auto DevOps 的测试和扫描，用自己的 Docker 构建和 Docker Compose 部署替换默认实现。

```yaml
# .gitlab-ci.yml - 混合模式：Auto DevOps + 自定义
stages:
  - build
  - test
  - code_quality
  - security
  - deploy

include:
  - template: Auto-DevOps.gitlab-ci.yml

variables:
  # 禁用 Auto DevOps 的自带 build 和 deploy
  AUTO_DEVOPS_BUILD_DISABLED: "true"     # 关闭自动 build
  AUTO_DEVOPS_DEPLOY_DISABLED: "true"    # 关闭自动 deploy
  AUTO_DEVOPS_REVIEW_DISABLED: "true"
  AUTO_DEVOPS_MONITORING_DISABLED: "true"
  AUTO_DEVOPS_DAST_DISABLED: "true"
  AUTO_DEVOPS_LICENSE_SCANNING_DISABLED: "true"

  # Auto DevOps 的 test 和 SAST 保留
  TEST_DISABLED: "false"

  # 自定义变量
  DOCKER_IMAGE: $CI_REGISTRY_IMAGE:$CI_COMMIT_SHORT_SHA

# ===== 自定义构建（替换 Auto DevOps 的 build）=====
docker-build:
  stage: build
  image:
    name: gcr.io/kaniko-project/executor:debug
    entrypoint: [""]
  script:
    - /kaniko/executor --context $CI_PROJECT_DIR --dockerfile Dockerfile --destination $DOCKER_IMAGE

# ===== 自定义部署（替换 Auto DevOps 的 deploy）=====
deploy-staging:
  stage: deploy
  image: alpine:latest
  before_script:
    - apk add --no-cache openssh-client
  script:
    - ssh deploy@staging-server "docker pull $DOCKER_IMAGE && docker-compose up -d"
  environment:
    name: staging
  rules:
    - if: '$CI_COMMIT_BRANCH == "main"'

deploy-production:
  stage: deploy
  image: alpine:latest
  script:
    - echo "Deploying $DOCKER_IMAGE to production"
  environment:
    name: production
  rules:
    - if: '$CI_COMMIT_TAG'
  when: manual
```

#### 步骤4：Auto DevOps 语言检测覆盖

**目标**：当自动检测出错时，手动指定项目语言和版本。

```yaml
# .gitlab-ci.yml - 强制指定语言
include:
  - template: Auto-DevOps.gitlab-ci.yml

variables:
  # 覆盖语言检测——强制使用 Node.js 构建策略
  AUTO_DEVOPS_BUILD_IMAGE_FORWARDED_CI_VARIABLES: "false"
  AUTO_DEVOPS_BUILD_IMAGE_CNB_ENABLED: "false"

  # 指定构建镜像（而不是让 Auto DevOps 自动选）
  # Auto DevOps 会根据项目文件自动选择：
  # - Dockerfile → Docker
  # - package.json → Node.js (Herokuish/CNB)
  # - requirements.txt → Python
  # - pom.xml → Java (Maven)
  # - build.gradle → Java (Gradle)
  # - Gemfile → Ruby
```

**完整 Auto DevOps 可配置变量清单**：

| 变量 | 作用 | 默认值 |
|------|------|--------|
| `AUTO_DEVOPS_BUILD_IMAGE` | 构建使用的 Docker 镜像 | 自动检测 |
| `AUTO_DEVOPS_BUILD_IMAGE_CNB_ENABLED` | 是否用 CNB 构建 | true |
| `TEST_DISABLED` | 是否跳过测试 | false |
| `CODE_QUALITY_DISABLED` | 是否跳过代码质量 | false |
| `SAST_DISABLED` | 是否跳过 SAST | false |
| `DEPENDENCY_SCANNING_DISABLED` | 是否跳过依赖扫描 | false |
| `CONTAINER_SCANNING_DISABLED` | 是否跳过容器扫描 | false |
| `DAST_DISABLED` | 是否跳过动态测试 | false |
| `LICENSE_SCANNING_DISABLED` | 是否跳过合规 | false |
| `REVIEW_DISABLED` | 是否跳过 Review Apps | false |
| `DEPLOY_DISABLED` | 是否跳过部署 | false |
| `MONITORING_DISABLED` | 是否跳过监控 | false |

### 完整代码清单

- Auto DevOps 全自动配置（步骤1）
- 定制禁用阶段配置（步骤2）
- 混合模式配置模板（步骤3）

### 测试验证

```bash
# 验证1：确认 Auto DevOps 自动生成的 Pipeline
# 创建新项目 + push 代码 → 检查 CI/CD Pipelines
# 应自动出现 build、test、code_quality、sast 等 job

# 验证2：验证禁用 stage 生效
# 检查 Pipeline 是否缺省了 deploy、review 等阶段

# 验证3：验证自定义 job 覆盖
# 自定义的 docker-build 是否替代了 Auto DevOps 的 build

# 验证4：查看 SAST 报告
# MR 页面 → Security tab → 查看扫描结果
```

## 4. 项目总结

### 优点 & 缺点

| 特性 | 优点 | 缺点 |
|------|------|------|
| 零配置启动 | 5 分钟给新项目配上 CI/CD | 默认值不总是适用 |
| 语言自动检测 | 支持 10+ 种语言 | 多语言混合项目检测不准 |
| 阶段可禁用 | 灵活选择需要的功能 | 变量记忆量大，容易配错 |
| 可叠加自定义 | 保留自动化 + 添加定制 | 自定义和默认的交互可能意外冲突 |

### Auto DevOps vs 全手动 vs CI 模板工程化

| 维度 | Auto DevOps | 全手动 | CI 模板工程化 |
|------|------------|--------|-------------|
| 启动速度 | ⚡ 5 分钟 | 🐢 数小时 | 🏃 30 分钟 |
| 灵活度 | ⚠️ 中 | ✅ 高 | ✅ 高 |
| 维护成本 | ✅ 低（GitLab 维护） | ❌ 高 | ⚠️ 中 |
| 适用场景 | 标准项目、新手项目 | 特殊需求项目 | 有成熟标准的团队 |

### 注意事项

- **Auto DevOps 默认部署到 Kubernetes**：如果不用 K8s，务必设置 `AUTO_DEVOPS_DEPLOY_DISABLED`
- **SAST 和依赖扫描均为 GitLab 内置**：CE 和 EE 版本可用，某些高级安全功能仅 EE 支持
- **语言检测基于项目根目录文件**：如果项目是 Monorepo，检测可能出错

### 常见踩坑经验

1. **Auto DevOps 自动构建失败**：项目根目录同时有 `Dockerfile` 和 `package.json`，Auto DevOps 不确定用哪个。根因：语言检测优先级冲突。解决：显式指定 `AUTO_DEVOPS_BUILD_IMAGE_CNB_ENABLED: "false"` 让 Auto DevOps 使用 Dockerfile。
2. **Review Apps 创建后 DNS 无法解析**：Auto DevOps 使用 `*.review.example.com` 模式，但 DNS 没有配置通配符记录。根因：DNS 基础设施不支持动态子域名。解决：配置通配符 DNS 记录，或禁用 Review Apps。
3. **SAST 扫描超时**：大型 Monorepo 中 SAST 扫描所有文件耗时过长。根因：`SAST_EXCLUDED_PATHS` 未配置。解决：排除 `node_modules`、`vendor` 等第三方目录。

### 思考题

1. Auto DevOps 默认使用 Herokuish buildpacks 而不是 Dockerfile。这有什么优缺点？什么场景下应该切换到 Dockerfile 构建？
2. 如果你有一个 Monorepo，里面同时有 Python 后端和 React 前端，Auto DevOps 会如何检测语言？你如何配置来确保两个部分都被正确构建？

> 答案见附录 D。

### 推广计划提示

- **开发**：对快速验证的原型项目直接启用 Auto DevOps，不需要写 CI 配置
- **运维**：Auto DevOps 的 SAST 和依赖扫描可以作为安全合规的基线
- **管理**：Auto DevOps 可以大幅降低新项目的 CI/CD 上手门槛
