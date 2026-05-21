# 第24章：CI/CD 集成：Jenkins、GitLab CI 与制品晋级

## 1. 项目背景

云鲸科技的发布流程正处在一个混乱的过渡期：Java 组的 CI 用 Jenkins，在流水线中 `mvn deploy` 把 SNAPSHOT 发到 Nexus，然后手动改版本号再发 RELEASE；前端组用 GitLab CI，在 `.gitlab-ci.yml` 中 `npm publish` 到 Nexus，但经常忘记更新版本号导致 403 覆盖错误；Docker 组在 Jenkins 里 build 镜像然后 `docker push`，但镜像 tag 用的是 `$CI_COMMIT_SHORT_SHA`，导致 Nexus 里堆积了几百个临时镜像。

更致命的是，上周三一次紧急线上发布出了生产事故——交易团队需要把 `order-service` 从 `1.5.2-SNAPSHOT` 升级到 `1.5.2`（正式版），但 CI 流水线的"发布"阶段直接跳过了——因为 Jenkinsfile 中写死了 `if (branch == 'main')` 才执行 RELEASE 发布，而紧急修复是从 `hotfix/1.5.2` 分支切出来的。半夜三更没人注意到跳过，手动 `mvn deploy` 到生产仓库时又打错了版本号（`1.5.3` 而非 `1.5.2`），导致生产环境引用了一个空组件，服务全部报 `NoClassDefFoundError`。

**CI/CD 与 Nexus 的集成不是简单的"把 mvn deploy 放进流水线"，而是一条从代码提交到制品发布的完整供应链**——需要解决凭据安全、版本校准、制品晋级、构建幂等、失败重试、审计追溯六个核心问题。本章将以 Jenkins 和 GitLab CI 为例，实现一条完整的 SNAPSHOT → RELEASE → Docker 镜像的自动化晋级流水线。

## 2. 项目设计

大师和 CI/CD 组的运维工程师周原在讨论 Jenkinsfile 的重构方案。

**周原**："大师，现在每个项目的 Jenkinsfile 里都硬编码了 Nexus 的用户名和密码——`mvn deploy -Dnexus.user=admin -Dnexus.pass=admin123`。安全团队昨天刚下了最后通牒，要求这周内整改。"

**大师**："三件事。第一，把凭据从 Jenkinsfile 中移除，存到 Jenkins 的 Credentials Manager 里。第二，Nexus 端创建独立的 CI 机器人账号——每个 CI 项目一个，而不是所有项目共享一个 `ci-bot`。第三，考虑用 Nexus User Token 而不是明文密码——Token 可以独立吊销，不影响主密码。"

> **技术映射**：CI 凭据管理三段式——凭据存储（Jenkins Credentials/Vault）→ 环境变量注入 → Nexus User Token 替代明文密码。

**小胖**："那版本号怎么管理？现在每次发 RELEASE 都要人工改 version，经常忘记或改错。"

**大师**："版本号应该由 CI 流水线自动管理，而不是人工手改。标准流程是：开发期间版本保持为 `x.y.z-SNAPSHOT`（在 `pom.xml` 中固定），CI 每次构建用 `mvn deploy` 发 SNAPSHOT。当需要发布 RELEASE 时，通过 CI 参数（或 Git Tag）触发发布流水线——流水线先执行 `mvn versions:set -DnewVersion=x.y.z`（去除 -SNAPSHOT），deploy RELEASE 后，再执行 `mvn versions:set -DnewVersion=x.y.(z+1)-SNAPSHOT` 并提交到 Git——确保版本号持续前进。"

**周原**："那 Docker 镜像的版本号呢？现在用 commit hash 做 tag，历史镜像没法追溯。"

**大师**："Docker 的三层 tag 策略——`<version>-<env>-<hash>`。开发环境：`1.5.2-dev-abc1234`；测试环境：`1.5.2-rc1`；生产环境：`1.5.2`。hash 只在开发阶段使用，一旦打出 RC 标签，就以语义化版本为准。清理策略按 tag 前缀分别处理——`*-dev-*` 保留 7 天，`*-rc*` 保留 30 天，正式版本永不清理。"

> **技术映射**：制品晋级路径 = SNAPSHOT（任意覆盖）→ RELEASE Candidate（锁定标签）→ RELEASE（永久锁定）。版本号从包含时间戳/哈希的临时标识逐渐收敛为语义化版本。

**小白**："幂等发布怎么保证？如果 CI 因为网络问题执行了两次 deploy，会不会产生重复的组件？"

**大师**："对于 RELEASE 版本——Nexus hosted 仓库的 `ALLOW_ONCE` 策略天然保证了幂等性。第二次 deploy 同一个 GAV 会返回 400 错误，CI 应该捕获这个错误并判断为'已发布，跳过'。对于 SNAPSHOT 版本——每次 deploy 都产生新的时间戳版本，两次 deploy 会产生两个不同的时间戳版本（如 `-1` 和 `-2`），但 metadata 会指向最新的。对于 npm 和 Docker，情况类似——npm 的 `ALLOW_ONCE` 阻止覆盖，Docker 的 tag 覆盖行为取决于 `forceBasicAuth` 和权限设置。"

**周原**："整个流水线失败了怎么处理？比如 Maven deploy 成功了，但 Docker push 超时了。"

**大师**："这是'发布事务'问题——跨步骤的发布不具备原子性。补偿策略是：先执行最可能失败的操作——通常是 Docker push（网络依赖最强）。如果 Docker push 失败，整条流水线失败，Maven 还没发，可以安全重试。如果 Docker push 成功但 Maven deploy 失败——回滚 Docker 镜像（删除 tag）后重试。**发布顺序从脆弱的到稳定的**，降低回滚成本。"

## 3. 项目实战

### 3.1 环境准备

- 已部署 Nexus 实例
- Jenkins 或 GitLab CI 环境
- Maven 项目、npm 项目（测试用）

### 3.2 分步实战

#### 步骤一：创建 CI 机器人账号和 Nexus User Token

**目标**：为 CI 创建专用账号，生成 User Token 替代明文密码。

```bash
NEXUS="http://localhost:8081"
AUTH="admin:admin123"

# 1. 创建 CI 机器人用户（每个项目独立）
curl -u $AUTH -X POST "$NEXUS/service/rest/v1/security/users" \
  -H "Content-Type: application/json" \
  -d '{
    "userId": "ci-order-service",
    "firstName": "CI",
    "lastName": "OrderService",
    "email": "ci-order@cloudwhale.com",
    "password": "C1S3cur3P@ss!",
    "status": "active",
    "roles": ["role-ci-java"]
  }'

# 2. 生成 User Token（在 Web UI 中操作：登录 → 右上角头像 → User Token）
# 或通过 API 模拟（实际 Nexus API 中 Token 获取需要登录后访问 /service/rest/v1/security/users/{userId}/user-token）
echo "=== CI 凭据配置 ==="
echo "在 Jenkins Credentials Manager 中添加:"
echo "  Type: Username with password"
echo "  ID: nexus-ci-order-service"
echo "  Username: ci-order-service"
echo "  Password: (Nexus User Token, 非登录密码)"
echo ""
echo "在 Jenkinsfile 中引用:"
echo "  withCredentials([usernamePassword("
echo "    credentialsId: 'nexus-ci-order-service',"
echo "    usernameVariable: 'NEXUS_USER',"
echo "    passwordVariable: 'NEXUS_PASS'"
echo "  )]) {"
echo "    sh 'mvn deploy -DaltDeploymentRepository=...'"
echo "  }"
```

#### 步骤二：Maven 制品晋级 Jenkins Pipeline

**目标**：编写完整的 Jenkinsfile 实现 SNAPSHOT 自动构建和 RELEASE 晋级。

```groovy
// Jenkinsfile — Maven 制品晋级流水线
pipeline {
    agent any
    
    environment {
        NEXUS_URL = 'http://nexus.internal:8081'
        NEXUS_REPO_SNAPSHOTS = "${NEXUS_URL}/repository/maven-trade-snapshots/"
        NEXUS_REPO_RELEASES = "${NEXUS_URL}/repository/maven-trade-releases/"
    }
    
    parameters {
        choice(name: 'BUILD_TYPE', choices: ['snapshot', 'release'], description: '构建类型')
        string(name: 'RELEASE_VERSION', defaultValue: '', description: '发布版本号（仅 release 需要，如 1.5.2）')
    }
    
    stages {
        stage('Build & Test') {
            steps {
                sh 'mvn clean compile test -B'
            }
        }
        
        stage('Publish SNAPSHOT') {
            when { expression { params.BUILD_TYPE == 'snapshot' } }
            steps {
                withCredentials([usernamePassword(
                    credentialsId: 'nexus-ci-order-service',
                    usernameVariable: 'NEXUS_USER',
                    passwordVariable: 'NEXUS_PASS'
                )]) {
                    sh """
                        mvn deploy \
                          -DaltSnapshotDeploymentRepository=nexus-snapshots::default::${NEXUS_REPO_SNAPSHOTS} \
                          -DskipTests \
                          -B
                    """
                }
            }
        }
        
        stage('Release: Set Version') {
            when { expression { params.BUILD_TYPE == 'release' } }
            steps {
                script {
                    if (!params.RELEASE_VERSION) {
                        error "发布版本号不能为空"
                    }
                }
                sh """
                    mvn versions:set -DnewVersion=${params.RELEASE_VERSION} -DgenerateBackupPoms=false
                    git add pom.xml
                    git commit -m "[CI] Prepare release ${params.RELEASE_VERSION}"
                    git tag v${params.RELEASE_VERSION}
                """
            }
        }
        
        stage('Release: Deploy') {
            when { expression { params.BUILD_TYPE == 'release' } }
            steps {
                withCredentials([usernamePassword(
                    credentialsId: 'nexus-ci-order-service',
                    usernameVariable: 'NEXUS_USER',
                    passwordVariable: 'NEXUS_PASS'
                )]) {
                    sh """
                        mvn deploy \
                          -DaltReleaseDeploymentRepository=nexus-releases::default::${NEXUS_REPO_RELEASES} \
                          -DskipTests \
                          -B
                    """
                }
            }
        }
        
        stage('Release: Bump Next Version') {
            when { expression { params.BUILD_TYPE == 'release' } }
            steps {
                script {
                    def parts = params.RELEASE_VERSION.tokenize('.')
                    def nextPatch = (parts[2] as int) + 1
                    def nextVersion = "${parts[0]}.${parts[1]}.${nextPatch}-SNAPSHOT"
                    sh """
                        mvn versions:set -DnewVersion=${nextVersion} -DgenerateBackupPoms=false
                        git add pom.xml
                        git commit -m "[CI] Prepare next development version ${nextVersion}"
                        git push origin HEAD:main --tags
                    """
                }
            }
        }
    }
    
    post {
        failure {
            echo '流水线失败，请检查日志并修复后重试'
        }
    }
}
```

#### 步骤三：GitLab CI 实现 npm 制品发布

**目标**：编写 `.gitlab-ci.yml`，实现 npm 包的自动化发布。

```yaml
# .gitlab-ci.yml — npm 制品发布
variables:
  NEXUS_URL: "http://nexus.internal:8081"
  NEXUS_NPM_PUBLIC: "${NEXUS_URL}/repository/npm-public/"
  NEXUS_NPM_HOSTED: "${NEXUS_URL}/repository/npm-hosted/"

stages:
  - build
  - publish-snapshot
  - publish-release

# 构建和测试（所有分支）
build:
  stage: build
  image: node:18
  script:
    - npm ci --registry=${NEXUS_NPM_PUBLIC}
    - npm run build
    - npm test

# 发布 SNAPSHOT（非 main 分支）
publish-snapshot:
  stage: publish-snapshot
  image: node:18
  rules:
    - if: '$CI_COMMIT_BRANCH != "main"'
  script:
    - |
      # 生成唯一的 pre-release 版本号
      SNAPSHOT_VERSION=$(node -p "require('./package.json').version")-dev.${CI_COMMIT_SHORT_SHA}
      npm version ${SNAPSHOT_VERSION} --no-git-tag-version
      echo "//${NEXUS_URL}/repository/npm-hosted/:_authToken=${NEXUS_AUTH_TOKEN}" > .npmrc
      npm publish --registry=${NEXUS_NPM_HOSTED} --tag dev

# 发布 RELEASE（main 分支 + Git Tag）
publish-release:
  stage: publish-release
  image: node:18
  rules:
    - if: '$CI_COMMIT_TAG =~ /^v\d+\.\d+\.\d+$/'
  script:
    - echo "//${NEXUS_URL}/repository/npm-hosted/:_authToken=${NEXUS_AUTH_TOKEN}" > .npmrc
    - npm publish --registry=${NEXUS_NPM_HOSTED}
```

**运行说明**：
- `NEXUS_AUTH_TOKEN` 配置在 GitLab CI Variables 中（Settings → CI/CD → Variables）
- 开发分支自动发布带 commit hash 的 pre-release 版本
- 正式发布通过打 Git Tag（`v1.2.3`）触发

#### 步骤四：Docker 镜像的 CI 集成

**目标**：在 CI 中构建 Docker 镜像并推送到 Nexus。

```bash
#!/bin/bash
# ci-docker-push.sh：CI 中 Docker 构建和推送
set -e

NEXUS_DOCKER_HOST="${NEXUS_DOCKER_HOST:-nexus.internal:5000}"
IMAGE_NAME="${CI_PROJECT_NAME:-my-app}"
GIT_SHA="${CI_COMMIT_SHORT_SHA:-$(git rev-parse --short HEAD)}"
BRANCH="${CI_COMMIT_BRANCH:-$(git rev-parse --abbrev-ref HEAD)}"

echo "=== Docker CI 构建 ==="

# 根据分支决定 tag 策略
if [ "$BRANCH" = "main" ] && [ -n "${CI_COMMIT_TAG}" ]; then
    # 正式发布 tag → 语义化版本
    VERSION="${CI_COMMIT_TAG#v}"
    TAGS=("${NEXUS_DOCKER_HOST}/${IMAGE_NAME}:${VERSION}" "${NEXUS_DOCKER_HOST}/${IMAGE_NAME}:latest")
elif [ "$BRANCH" = "main" ]; then
    # main 分支 → RC 标签
    TAGS=("${NEXUS_DOCKER_HOST}/${IMAGE_NAME}:rc-${GIT_SHA}")
else
    # 开发分支 → 开发标签
    TAGS=("${NEXUS_DOCKER_HOST}/${IMAGE_NAME}:dev-${GIT_SHA}")
fi

# 构建
docker build -t "${TAGS[0]}" .

# 推送所有标签
for TAG in "${TAGS[@]}"; do
    echo "推送: $TAG"
    docker push "$TAG"
done

echo "=== Docker CI 完成 ==="
```

#### 步骤五：幂等发布验证

**目标**：在 CI 中添加幂等检查，防止重复发布导致流水线失败。

```bash
#!/bin/bash
# check-already-published.sh：检查版本是否已发布
NEXUS="http://localhost:8081"
AUTH="admin:admin123"

GROUP="$1"
ARTIFACT="$2"
VERSION="$3"
REPO="$4"

if [ -z "$VERSION" ]; then
    echo "用法: $0 <groupId> <artifactId> <version> <repo>"
    exit 1
fi

echo "检查 $GROUP:$ARTIFACT:$VERSION 是否已存在于 $REPO..."

RESULT=$(curl -s -u "$AUTH" \
  "$NEXUS/service/rest/v1/search?repository=$REPO&group=$GROUP&name=$ARTIFACT&version=$VERSION")

COUNT=$(echo "$RESULT" | jq '.items | length')

if [ "$COUNT" -gt "0" ]; then
    echo "✅ 版本 $VERSION 已存在于 $REPO（发布幂等，跳过）"
    exit 0
else
    echo "→ 版本 $VERSION 不存在，将执行发布"
    exit 1  # 返回非 0 让 CI 继续执行发布步骤
fi
```

在 Jenkinsfile/GitLab CI 中集成：

```groovy
// 在 Release: Deploy 步骤前添加
stage('Check Already Published') {
    steps {
        script {
            def checkResult = sh(
                script: "./check-already-published.sh com.cloudwhale.order order-service ${params.RELEASE_VERSION} maven-releases",
                returnStatus: true
            )
            if (checkResult == 0) {
                currentBuild.result = 'NOT_BUILT'
                error "版本 ${params.RELEASE_VERSION} 已发布，跳过"
            }
        }
    }
}
```

### 3.3 常见坑点

| 坑点 | 现象 | 解决方法 |
|------|------|----------|
| CI 凭据泄露 | Jenkinsfile 中硬编码密码被提交到 Git | 使用 Credentials Manager + 环境变量注入 |
| mvn deploy 401 | CI 中认证失败 | 检查 `server.id` 与 `distributionManagement.id` 一致性 |
| SNAPSHOT 覆盖率冲突 | CI 发的 SNAPSHOT 意外覆盖了其他人的版本 | 为每个 CI 分支生成不同的 SNAPSHOT 标识 |
| npm publish 403 | 版本号重复 | 发布前检查版本是否已存在（幂等脚本） |

## 4. 项目总结

### 4.1 CI/CD 集成检查清单

| 检查项 | Maven | npm | Docker |
|--------|-------|-----|--------|
| CI 专用机器人账号 | ✅ | ✅ | ✅ |
| 凭据存于 Secret Manager | ✅ | ✅ | ✅ |
| 版本号自动管理 | versions:set | npm version | git tag → tag |
| 幂等发布保护 | ALLOW_ONCE | ALLOW_ONCE | tag 已存在跳过 |
| 失败重试与回滚 | mvn deploy 支持重试 | npm publish 需删除后重试 | docker push 支持重试 |
| 发布后自动升级开发版本 | versions:set next | npm version prepatch | — |

### 4.2 适用场景

1. **标准化发布流程**：所有团队使用统一的 CI 模板，减少人工失误
2. **多环境制品晋级**：dev → test → stage → prod 逐级自动化
3. **安全合规**：凭据不落地代码仓库，发布记录可追溯
4. **Monorepo 多模块发布**：按模块独立版本号管理和发布

### 4.3 注意事项

- **凭据永远不入 Git**：Jenkins/GitLab CI 使用内置的 Secret Manager
- **发布前检查版本号唯一性**：防止流水线重入导致的重复发布
- **Docker 标签清理配合 CI 节奏**：高频构建的 tag 需要高频清理
- **发布顺序从脆弱到稳定**：先执行最容易失败的操作

### 4.4 思考题

1. 某大型项目包含 20 个子模块，每次发布 RELEASE 时只有 3 个模块有变更。如何设计 CI 流水线使得只发布变更的模块，而非全量重新构建和发布？
2. 设计一个"金丝雀发布"的制品晋级方案——先在 10% 的消费者中验证新版本，确认无误后再全量推广。在不改变 Nexus 仓库结构的前提下，如何利用 tag/版本号策略实现？

（第23章思考题答案：1. A 团队角色绑定 `priv-raw-teamA-addonly`（Content Selector `cs-teamA` + actions `["ADD","READ"]`），`writePolicy: ALLOW_ONCE` 阻止覆盖。B 团队角色绑定 `priv-raw-teamB-full`（Content Selector `cs-teamB` + actions `["ADD","READ","DELETE"]`），`writePolicy: ALLOW` 允许覆盖。通过 Content Selector 隔离路径，通过 Privilege 的 actions 区分能力，通过仓库 writePolicy 控制覆盖行为。2. OSS 版不能完全阻止下载——READ 权限本身就包含下载。变通方案：① 使用 Nginx 反向代理，对审计账号的请求返回不含制品二进制内容的 JSON 元数据；② 创建一个特殊的 Raw 仓库路径映射，审计账号访问时返回的是元数据的引用地址而非实际文件；③ 通过 Webhook 记录所有该账号的请求日志用于事后审计，而非实时阻止。）

### 4.5 推广计划提示

- **CI/CD 团队**：将本章的 Jenkinsfile 和 `.gitlab-ci.yml` 模板推广为团队标准
- **开发团队**：统一版本号和 tag 命名规范，新人通过 CI 模板自动遵守
- **安全团队**：每季度审查 CI 凭据管理策略和机器人账号权限
