# 第12章：Artifacts 制品与 Cache 缓存

## 1. 项目背景

> **业务场景**：一家公司的前端项目 CI 流水线每次都要跑 12 分钟才能完成——其中 8 分钟浪费在重复安装 npm 依赖上。后端 Java 项目更离谱——每次 CI 都重新下载 500MB 的 Maven 依赖，月流量费用超过 2000 元。团队尝试过把 `node_modules` 放到 Artifacts 里传递，结果每个 job 的 artifacts 都有 500MB，存储配额一个月就爆了。

更严重的问题发生在一次故障排查中：某个 job 生成的测试报告被放在 artifacts 中，但 `expire_in` 设置成了 1 小时。故障发生 2 小时后运维想回溯测试结果，发现 artifacts 已经过期被清理——关键证据丢失。

**痛点放大**：Artifacts 和 Cache 是 CI/CD 中最容易被混淆的两个概念。Artifacts 的作用是"传递构建结果"（从 job A 给 job B 用），Cache 的作用是"加速重复操作"（避免重复下载依赖）。但很多团队把 Cache 当 Artifacts 用，或者反过来——结果不是性能差就是存储爆。正确理解两者的语法、生命周期和使用场景，能让你在保持 CI 效率的同时控制好存储成本。

## 2. 项目设计——剧本式交锋对话

**场景**：月中的存储账单出来后，运维小王脸色铁青地找到开发组。

---

**运维小王**："你们上个月 CI Artifacts 存储用了 500GB！GitLab 默认的 5GB 配额早就超了，现在所有新 Pipeline 都不能保存 artifacts 了！"

**小胖**："没道理啊，我们就几个 Node.js 项目，npm 依赖能有多大？"

**大师**："问题就出在——你们是不是把 `node_modules` 放到 artifacts 里了？Artifacts 的设计目的是传递构建产物（如编译后的 jar 包、dist 目录），而不是传递可以被缓存重建的内容。"

**小胖**："那有什么区别？不都是把一个 job 的文件给另一个 job 吗？"

**大师**："完全不同的语义。Artifacts 是'制品'，代表构建流水线的有价值输出——比如 WAR 包、测试报告、覆盖率文件。它们应该被保留和归档，因为部署和审计需要。Cache 是'缓存'，代表可以被重新生成的内容——比如依赖包、编译中间文件。技术映射——Artifacts 就像工厂生产出来的成品，需要入库保存；Cache 就像工厂的工具箱，工人拿来就用，但丢了可以再买一套。"

**小白**："那配置语法上怎么区分？我看到 artifacts 有 `paths`、`expire_in`、`reports`，cache 有 `key`、`paths`、`policy`。"

**大师**："Artifacts 的核心参数是 `paths`（哪些文件是制品）和 `expire_in`（保留多久）。注意 artifacts 默认会传递给同一 Pipeline 后续的所有 job——这是它最大的价值，但也是存储消耗的来源。Cache 的核心参数是 `key`（缓存标识，通常用 `package-lock.json` 的 hash）和 `policy`（pull/push/pull-push，控制缓存的读写方向）。"

**小胖**："那我把 `node_modules` 放到 cache 不就行了？"

**大师**："对。但要注意——Cache 不是保证可用的。GitLab 官方文档明确说'Cache is an optimization, not an artifact'——缓存可能不命中（被清理、被覆盖、key 不匹配），你的 job 必须能处理缓存缺失的情况。最佳实践是在 job 开头用 `npm ci`，即使缓存没命中也能正常安装。"

**运维小王**："那 report artifacts 是什么？我看文档里提到 `artifacts:reports`。"

**大师**："Report artifacts 是特殊类型的 artifacts——GitLab 会自动解析它们的结构并在 UI 中展示。比如 `junit` 类型的 report 会自动生成测试报告和失败详情，`cobertura` 类型会生成覆盖率图表，`sast` 类型会显示安全漏洞列表。技术映射——普通 artifacts 就像你提交的 Word 文档，需要手动下载查看；report artifacts 就像在线表单，GitLab 直接帮你解析成可视化图表。"

---

## 3. 项目实战

### 环境准备

> **目标**：为一个前端+后端项目配置最优的 artifacts 和 cache 策略，让 CI 速度提升 50%。

**前置条件**：Node.js 项目 + 1 个 Docker Runner。

### 分步实现

#### 步骤1：配置最优的 npm Cache 策略

**目标**：用 cache + `npm ci --prefer-offline` 大幅减少依赖安装时间。

```yaml
# .gitlab-ci.yml - Cache 最佳实践
stages:
  - install
  - test
  - build

variables:
  NODE_VERSION: "20-alpine"

# 使用 YAML 锚点定义共用的 cache 配置
.npm_cache: &npm_cache
  cache:
    key:
      files:
        - package-lock.json     # 按 lock 文件内容 hash 作为 cache key
    paths:
      - node_modules/
    # 默认 policy: pull-push（读写）
    unprotect: false            # 保护分支的缓存不被非保护分支覆盖

# Job 1: 安装依赖（读缓存 + 写入新缓存）
install-deps:
  stage: install
  image: node:${NODE_VERSION}
  <<: *npm_cache
  script:
    # npm ci 比 npm install 更快且更严格（按 lock 文件精确安装）
    # --prefer-offline 优先使用本地缓存
    - npm ci --prefer-offline --no-audit
  # 如果 lock 文件没变，cache 命中，npm ci 几乎瞬间完成
  artifacts:
    paths:
      - node_modules/    # 仍然需要 artifacts 传给后续 job
    expire_in: 1 hour    # 短过期——仅用于同一次 Pipeline

# Job 2: 测试（只读缓存）
test-job:
  stage: test
  image: node:${NODE_VERSION}
  cache:
    key:
      files:
        - package-lock.json
    paths:
      - node_modules/
    policy: pull               # 只读：不写回缓存
  script:
    - npm run test
  needs:
    - install-deps

# Job 3: 构建（只读缓存）
build-job:
  stage: build
  image: node:${NODE_VERSION}
  cache:
    <<: *npm_cache              # 继承基础配置
    policy: pull                # 但修改 policy
  script:
    - npm run build
  artifacts:
    paths:
      - dist/
    expire_in: 1 week
  needs:
    - install-deps
```

**Cache 效果对比**：

```bash
# 第一次运行（Cache 未命中）：
# install-deps: 2 分 30 秒（实际下载依赖）
# test-job: 跳过 npm ci（从 artifacts 拿到 node_modules）
# build-job: 跳过 npm ci

# 第二次运行（Cache 命中，lock 文件未变）：
# install-deps: 15 秒（npm ci 检测到 node_modules 完整，瞬间完成）
# test-job: 20 秒
# build-job: 30 秒
# 总耗时从 5 分钟降到 1 分钟！
```

#### 步骤2：配置 report artifacts 实现自动测试报告

**目标**：为测试 job 配置 JUnit 和覆盖率 report 类型，GitLab 自动解析和展示。

```yaml
# .gitlab-ci.yml - Report artifacts
unit-tests:
  stage: test
  image: node:${NODE_VERSION}
  script:
    # Jest 配置输出 JUnit XML（需要在 jest.config.js 中配置）
    - npx jest --ci --reporters=default --reporters=jest-junit
    # 生成覆盖率报告（cobertura 格式）
    - npx jest --ci --coverage --coverageReporters=cobertura
  artifacts:
    when: always               # 无论测试是否通过都保存报告
    reports:
      junit: junit.xml         # GitLab 解析 JUnit 格式
      coverage_report:
        coverage_format: cobertura
        path: coverage/cobertura-coverage.xml  # GitLab 解析覆盖率
  coverage: '/All files[^|]*\|[^|]*\s+([\d\.]+)/'  # 从日志中提取覆盖率数字
```

**在 GitLab UI 中查看报告**：

```bash
# 1. MR 页面 → "Test summary" → 可展开查看每个测试用例的结果
# 2. MR 页面 → "Code coverage" → 显示覆盖率变化趋势图
# 3. CI/CD → Jobs → 点击 job → "Tests" 选项卡 → 失败用例详情
# 4. Analytics → Repository → Code coverage → 整体覆盖率趋势
```

#### 步骤3：配置 Maven/Gradle 的 Cache（Java 项目示例）

**目标**：为 Java 项目配置 Maven 本地仓库缓存。

```yaml
# .gitlab-ci.yml - Java Maven Cache
variables:
  MAVEN_OPTS: >-
    -Dhttps.protocols=TLSv1.2
    -Dmaven.repo.local=$CI_PROJECT_DIR/.m2/repository
    -Dorg.slf4j.simpleLogger.showDateTime=true
    -Djava.awt.headless=true
  MAVEN_CLI_OPTS: >-
    --batch-mode
    --errors
    --fail-at-end
    --show-version
    -DinstallAtEnd=true
    -DdeployAtEnd=true

maven-build:
  stage: build
  image: maven:3.9-eclipse-temurin-17
  script:
    - mvn $MAVEN_CLI_OPTS clean package
  cache:
    key:
      files:
        - pom.xml
    paths:
      - .m2/repository/    # Maven 本地仓库
  artifacts:
    paths:
      - target/*.jar       # 只保留最终的 jar 包
    expire_in: 1 week
```

#### 步骤4：配置 Artifacts 过期清理策略

**目标**：按 artifacts 的重要程度设置不同的过期时间。

```yaml
# .gitlab-ci.yml - Artifacts 过期策略
build:
  stage: build
  script: npm run build
  artifacts:
    paths:
      - dist/
    expire_in: 1 week    # 构建产物：保留 1 周

test-reports:
  stage: test
  script: npm test
  artifacts:
    reports:
      junit: junit.xml
    expire_in: 30 days   # 测试报告：保留 1 个月

deploy-logs:
  stage: deploy
  script: deploy.sh
  artifacts:
    paths:
      - deploy.log
    expire_in: 90 days   # 部署日志：保留 3 个月（审计需要）

tmp-debug:
  stage: test
  script: npm run debug
  artifacts:
    paths:
      - debug/
    expire_in: 1 hour    # 调试文件：1 小时后清理
```

**GitLab 管理端清理（管理员）**：

```bash
# 设置项目级别默认过期时间
# Project → Settings → CI/CD → Artifacts → Set default expiration

# 管理员全局清理过期 artifacts
sudo gitlab-rake gitlab:cleanup:orphan_job_artifact_files

# 查看存储用量
curl --header "PRIVATE-TOKEN: $GITLAB_TOKEN" \
  "$GITLAB_URL/api/v4/projects/$PROJECT_ID/statistics" | \
  python3 -c "import json,sys; s=json.load(sys.stdin); print(f'Artifacts: {s[\"build_artifacts_size\"]/1024/1024:.1f} MB')"
```

### 完整代码清单

- `.gitlab-ci.yml`：完整 Cache + Artifacts + Reports 配置
- `jest.config.js`（参考）：JUnit 和 cobertura 输出配置
- Maven `.m2/settings.xml`（参考）：Maven 本地仓库配置

### 测试验证

```bash
# 验证1：测试 Cache 命中率
# 查看 job 日志中是否有 "cache is up-to-date" 或 "No cache found"
# 期望：第二次运行时看到 "cache is up-to-date"

# 验证2：验证 artifacts 传递
# 在后续 job 中确认能访问到上游 job 的 artifacts
# build-job 脚本中加: ls -la dist/ && echo "Artifacts from install: $CI_JOB_ID"

# 验证3：测试报告展示
# MR 页面 → 应出现 Test summary 和 Coverage 报告
# 点击 job 详情 → Tests 选项卡显示每个用例的通过/失败状态

# 验证4：缓存 key 不同时隔离
# 修改 package-lock.json（添加一个新依赖）
# 新 Pipeline 应显示 "No cache found"（因为 key 变了）
# 同时旧 key 的缓存不影响新的安装
```

## 4. 项目总结

### 优点 & 缺点

| 机制 | 优点 | 缺点 |
|------|------|------|
| Artifacts | 保证传递，有版本，可回溯 | 存储开销大，需管理过期 |
| Cache | 大幅加速 CI，存储开销小 | 不保证命中，可能被清理 |
| Report Artifacts | UI 自动解析，可视化友好 | 格式支持有限（JUnit/cobertura/sast 等） |
| S3/GCS Cache | 分布式缓存，跨 Runner 共享 | 需要额外配置对象存储 |

### Artifacts vs Cache 对比表

| 维度 | Artifacts | Cache |
|------|-----------|-------|
| 目的 | 传递构建产物 | 加速依赖安装 |
| 保证 | 保证可用（只要未过期） | 不保证可用 |
| 传递 | 自动传给后续所有 job | 需要配置 key 和 policy |
| 存储 | 计入项目存储配额 | 独立缓存存储（通常更便宜） |
| 生命周期 | expire_in 控制 | LRU 策略自动清理 |
| 典型内容 | dist/、*.jar、junit.xml | .m2/、node_modules/、~/.cache |

### 适用场景

- **Artifacts**：部署包、测试报告、覆盖率文件——需要保留和追溯的内容
- **Cache**：依赖包、编译中间文件——可以被重建的内容
- **Report Artifacts**：JUnit 测试结果、覆盖率、SAST 扫描结果——GitLab UI 自动展示

**不适用场景**：
- 用 artifacts 传递 `node_modules`——存储会炸（改用 cache）
- 用 cache 传递部署包——可能不命中导致部署失败（改用 artifacts）

### 注意事项

- **Cache key 要包含精确的依赖标识**：`package-lock.json` vs `package.json`——lock 文件才代表精确的依赖版本
- **Artifacts 默认在所有 stage 间传递**：如果大 artifacts 不需要在 stage 间传递，用 `dependencies: []` 阻止自动继承
- **`expire_in` 不等于立即删除**：GitLab 会在过期后定期清理（不是实时），存储配额可能在清理前超限
- **保护分支的 cache 默认 protected**：非保护分支无法读取保护分支的 cache（`unprotect: false`）

### 常见踩坑经验

1. **Artifacts 太大导致作业超时**：上传 500MB artifacts 到 GitLab 需要几分钟。根因：artifacts 包含了不必要的大量文件（如 `node_modules`）。解决：只 artifacts 必须的产物（如 `dist/`、`*.jar`），依赖用 cache。
2. **Cache 不命中导致 CI 变慢**：每次 Pipeline 都 "No cache found"。根因：cache key 设计不合理（如用了 `${CI_PIPELINE_ID}` 每次都变）。解决：cache key 应基于依赖文件（`package-lock.json`、`pom.xml`）。
3. **Report Artifacts 不解析**：上传了 JUnit XML 但 GitLab 不解析。根因：XML 文件路径不对或格式不标准。解决：确认 `artifacts:reports:junit: junit.xml` 路径正确，且 XML 符合 JUnit schema。

### 思考题

1. 如果项目同时使用 npm 和 pip，一个 Pipeline 中需要两种语言的依赖缓存。如何设计 cache key 避免相互覆盖？
2. 每次 CI 构建都会产生新的 artifacts，但旧 artifacts 还没过期。GitLab 如何决定保留哪些 artifacts？如果要在所有 artifacts 中只保留最近 3 次的构建产物，怎么配置？

> 答案见附录 D。

### 推广计划提示

- **开发**：正确使用 cache 可让 CI 速度提升 3-10 倍，这是最直接的开发体验改善
- **运维**：artifacts 过期策略需要根据项目的发布频率来调整，避免存储超限
- **测试**：report artifacts 是测试结果可视化的基础，务必在测试 job 中配置
