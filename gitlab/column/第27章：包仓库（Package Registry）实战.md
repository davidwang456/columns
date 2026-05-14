# 第27章：包仓库（Package Registry）实战

## 1. 项目背景

> **业务场景**：某公司使用 Artifactory 管理内部 Java/Maven 制品，用 Verdaccio 管理 npm 私有包，用 PyPI Server 管 Python 包——一共 3 个制品仓库，需要维护 3 套认证、3 套备份、3 份运维文档。更糟的是，这些仓库和 GitLab 之间没有关联——你不知道某个 npm 包的哪个版本对应哪个 Git commit，出问题时无从溯源。

运维团队也头疼：Artifactory 的许可证费用每年 20 万，Verdaccio 的社区版功能有限（没有访问控制），PyPI Server 连 Web UI 都没有。团队希望能把制品管理统一到一个平台上——最好和代码在同一个 GitLab 实例上。

**痛点放大**：GitLab Package Registry 支持 Maven、npm、PyPI、NuGet、Composer、Conan、Helm、Go 等 10+ 种包格式。它和 GitLab 项目天然关联——每个包都知道自己来自哪个项目、哪个 commit、哪条 Pipeline。你不需要维护独立的认证体系和存储，一切和 GitLab 的权限模型无缝衔接。

## 2. 项目设计——剧本式交锋对话

**场景**：技术采购评审会，讨论下一年的制品仓库方案。

---

**采购经理**："Artifactory 又涨价了——从 18 万涨到 25 万一年。我们是不是该找替代方案？"

**大师**："GitLab Package Registry 就能替代 Artifactory 的大部分功能——Maven、npm、PyPI、Helm 都支持。而且它是 GitLab 原生集成的——包和代码在同一个项目下，同一个权限模型。你不需要额外管理一套认证。"

**小胖**："但我听说 GitLab Package Registry 功能比 Artifactory 弱很多？"

**大师**："如果你需要高级功能——比如跨仓库的制品清理策略、复杂的 LDAP 权限模型、海量制品的原数据检索——Artifactory 确实更强。但对于 90% 的团队来说，GitLab Package Registry 的功能完全够用。技术映射——Artifactory 就像专业的物流仓库（全方位管理），GitLab Package Registry 就像商店的货架（和你的产品展示在同一空间）。"

**小白**："那 CI 中怎么自动发布包？比如 Maven 项目构建好后自动发布到 Package Registry？"

**大师**："极其简单。GitLab CI 环境自动注入了认证信息——`CI_JOB_TOKEN` 可以直接用来推送包。Maven 用 `settings.xml` 配置，npm 用 `.npmrc` 配置，PyPI 用 `twine` 上传。加上依赖代理（Dependency Proxy），还可以把外部包的引用也收敛到 GitLab。"

---

## 3. 项目实战

### 环境准备

> **目标**：配置 Maven、npm 和 PyPI 三种包仓库，在 CI 中自动构建和发布。

**前置条件**：GitLab CE 17.x，Package Registry 默认启用。

### 分步实现

#### 步骤1：Maven 包仓库配置

**目标**：Java 项目在 CI 中构建后自动发布到 GitLab Package Registry。

**Maven `settings.xml` 配置**：

```xml
<!-- ~/.m2/settings.xml 或在 CI 中使用 -->
<settings>
  <servers>
    <server>
      <id>gitlab-maven</id>
      <configuration>
        <httpHeaders>
          <property>
            <name>Job-Token</name>
            <value>${env.CI_JOB_TOKEN}</value>
          </property>
        </httpHeaders>
      </configuration>
    </server>
  </servers>
</settings>
```

**Maven `pom.xml` 配置**：

```xml
<project>
  <groupId>com.acme</groupId>
  <artifactId>common-utils</artifactId>
  <version>${revision}</version>

  <properties>
    <revision>1.0.0-SNAPSHOT</revision>
  </properties>

  <!-- GitLab Package Registry 发布地址 -->
  <distributionManagement>
    <repository>
      <id>gitlab-maven</id>
      <url>${env.CI_API_V4_URL}/projects/${env.CI_PROJECT_ID}/packages/maven</url>
    </repository>
    <snapshotRepository>
      <id>gitlab-maven</id>
      <url>${env.CI_API_V4_URL}/projects/${env.CI_PROJECT_ID}/packages/maven</url>
    </snapshotRepository>
  </distributionManagement>

  <!-- 从 GitLab Package Registry 拉取依赖 -->
  <repositories>
    <repository>
      <id>gitlab-maven</id>
      <url>${env.CI_API_V4_URL}/packages/maven</url>
    </repository>
  </repositories>
</project>
```

**CI 配置**：

```yaml
# .gitlab-ci.yml
maven-publish:
  stage: publish
  image: maven:3.9-eclipse-temurin-17
  script:
    # SNAPSHOT 版本（非 main 分支）
    - |
      if [ "$CI_COMMIT_BRANCH" != "main" ]; then
        mvn deploy -Drevision=1.0.0-SNAPSHOT
      fi
    # RELEASE 版本（main 分支打 tag 触发）
    - |
      if [ -n "$CI_COMMIT_TAG" ]; then
        mvn deploy -Drevision=$CI_COMMIT_TAG
      fi
  rules:
    - if: '$CI_COMMIT_BRANCH || $CI_COMMIT_TAG'
  cache:
    key:
      files:
        - pom.xml
    paths:
      - .m2/repository/
```

**拉取依赖**：

```bash
# 其他项目使用此包
# 同样配置 settings.xml，然后在 pom.xml 中添加依赖：
# <dependency>
#   <groupId>com.acme</groupId>
#   <artifactId>common-utils</artifactId>
#   <version>1.0.0</version>
# </dependency>

# 也可以在 Group 级别拉取所有项目共享的包
# Group → Packages & Registries → Package Registry
```

#### 步骤2：npm 包仓库配置

**目标**：Node.js 项目在 CI 中构建和发布 npm 包。

**`.npmrc` 配置**：

```
# 项目根目录 .npmrc（CI 中用于发布）
@acme:registry=${CI_API_V4_URL}/packages/npm/
//${CI_SERVER_HOST}/api/v4/packages/npm/:_authToken=${CI_JOB_TOKEN}
```

**`package.json` 配置**：

```json
{
  "name": "@acme/common-utils",
  "version": "1.0.0",
  "publishConfig": {
    "@acme:registry": "https://gitlab.local/api/v4/packages/npm/"
  }
}
```

**CI 配置**：

```yaml
# .gitlab-ci.yml
npm-publish:
  stage: publish
  image: node:20-alpine
  script:
    - |
      # 自动更新版本号（基于 CI 环境）
      if [ -n "$CI_COMMIT_TAG" ]; then
        npm version $CI_COMMIT_TAG --no-git-tag-version
      else
        npm version prerelease --preid=dev-${CI_COMMIT_SHORT_SHA}
      fi
    - npm publish
  rules:
    - if: '$CI_COMMIT_BRANCH == "main"'
    - if: '$CI_COMMIT_TAG'
```

**拉取和使用**：

```bash
# 其他项目中使用
# 1. 配置项目的 .npmrc
echo "@acme:registry=https://gitlab.local/api/v4/packages/npm/" >> .npmrc
echo "//gitlab.local/api/v4/packages/npm/:_authToken=${CI_JOB_TOKEN}" >> .npmrc

# 2. 安装
npm install @acme/common-utils

# 3. 使用
const utils = require('@acme/common-utils');
```

#### 步骤3：PyPI 包仓库配置

**目标**：Python 项目在 CI 中发布 pip 包。

**CI 配置**：

```yaml
# .gitlab-ci.yml
pypi-publish:
  stage: publish
  image: python:3.12-alpine
  before_script:
    - pip install build twine
  script:
    - python -m build
    - |
      TWINE_PASSWORD=${CI_JOB_TOKEN} \
      TWINE_USERNAME=gitlab-ci-token \
      python -m twine upload \
        --repository-url ${CI_API_V4_URL}/projects/${CI_PROJECT_ID}/packages/pypi \
        dist/*
  rules:
    - if: '$CI_COMMIT_TAG'
```

**安装 Python 包**：

```bash
# 其他项目中使用
pip install package-name \
  --index-url https://gitlab-ci-token:${CI_JOB_TOKEN}@gitlab.local/api/v4/projects/123/packages/pypi/simple
```

#### 步骤4：包仓库运维管理

**目标**：管理包的生命周期——查看、清理、设置权限。

```bash
# 查看项目包列表
curl --header "PRIVATE-TOKEN: $GITLAB_TOKEN" \
  "$GITLAB_URL/api/v4/projects/$PROJECT_ID/packages" | \
  python3 -c "
import json, sys
for p in json.load(sys.stdin):
    print(f'{p[\"name\"]}@{p[\"version\"]} ({p[\"package_type\"]}) - {p[\"created_at\"]}')
"

# 删除指定包版本
curl --request DELETE \
  --header "PRIVATE-TOKEN: $GITLAB_TOKEN" \
  "$GITLAB_URL/api/v4/projects/$PROJECT_ID/packages/$PACKAGE_ID"

# 清理旧包版本（保留最新 5 个）
# Settings → Packages & Registries → Package Registry cleanup
# → ✅ Enabled
# → Number of packages to keep: 5

# 通过 API 配置清理策略
curl --request PUT \
  --header "PRIVATE-TOKEN: $GITLAB_TOKEN" \
  --header "Content-Type: application/json" \
  --data '{
    "packages_cleanup_policy_attributes": {
      "keep_n_duplicated_package_files": "5"
    }
  }' \
  "$GITLAB_URL/api/v4/projects/$PROJECT_ID"
```

### 完整代码清单

- Maven `settings.xml` + `pom.xml`
- npm `.npmrc` + CI 配置
- PyPI `twine` CI 配置
- 包清理 API

### 测试验证

```bash
# 验证1：Maven 包发布与拉取
# 触发 CI → 检查 Package Registry UI 是否有新包
# 另一个项目中配置依赖 → mvn compile 确认可以拉取

# 验证2：npm 包发布
# npm publish → 访问 Project → Packages & Registries
# 检查 @scope 包是否正确显示

# 验证3：权限验证
# 用 Reporter 权限用户尝试推送包 → 应被拒绝
# Developer 权限 → 应成功

# 验证4：清理策略
# 推送 10 个包版本 → 等清理策略执行 → 确认只剩 5 个
```

## 4. 项目总结

### 支持的包格式一览

| 包格式 | 适用语言 | CI 工具 | 特性 |
|--------|---------|---------|------|
| Maven | Java/Kotlin | `mvn deploy` | SNAPSHOT/RELEASE 自动识别 |
| npm | Node.js | `npm publish` | Scope 支持，Private 包 |
| PyPI | Python | `twine upload` | 标准 PyPI API 兼容 |
| NuGet | .NET | `dotnet nuget push` | 私有 NuGet 源 |
| Composer | PHP | `composer publish` | Packagist 兼容 |
| Conan | C/C++ | `conan upload` | C/C++ 二进制包 |
| Helm | Kubernetes | `helm push` | Helm Chart 仓库 |
| Go | Go | `go get` | Go Module 代理 |

### 适用场景

- **内部库共享**：多个团队/项目共享公共库（如 common-utils）
- **私有包管理**：商业软件的私有组件分发
- **版本关联追溯**：需要知道每个包版本对应的 Git commit 和 CI Pipeline

**不适用场景**：
- 需要全球 CDN 分发的公共开源包（用 npmjs.com / Maven Central）
- 超大二进制包（>5GB）——存储和传输性能可能不够

### 注意事项

- **CI_JOB_TOKEN 的权限**：默认只能推送当前项目，跨项目拉取需要配置 `CI_JOB_TOKEN` 的 allowlist
- **Package Registry 的存储计入项目配额**：定期清理旧版本
- **语义化版本**：Maven 的 SNAPSHOT 和 RELEASE 有明确语义，勿混用

### 常见踩坑经验

1. **npm publish 报 403**：即使配置了 Token 也报 forbidden。根因：`.npmrc` 中的 registry URL 格式不对。解决：URL 格式为 `https://gitlab.local/api/v4/packages/npm/`（注意结尾斜杠）。
2. **Maven deploy 报 401**：认证失败。根因：`settings.xml` 中 `id` 没有和 `pom.xml` 的 `id` 对应。解决：确保两处的 `<id>` 一致。
3. **PyPI 包名冲突**：push 报错 "Package already exists"。根因：PyPI 不允许覆盖已发布的版本。解决：升级版本号或先删除旧版本。

### 思考题

1. 如果公司有 3 个独立的产品线，都使用 Java 开发，且需要共享一些公共库。如何设计 Package Registry 的 Group/Project 结构来管理这些包？
2. npm 包的 Scope（@acme/xxx）和 GitLab 的 Group 有什么天然的对应关系？如何利用 Scope 来隔离不同团队的包？

> 答案见附录 D。

### 推广计划提示

- **开发**：内部库发布到 GitLab Package Registry 是"代码即制品"理念的最佳实践
- **运维**：包仓库的清理策略和存储配额管理需要定期关注
- **管理**：统一使用 GitLab Package Registry 可以节省商业制品仓库的许可证费用
