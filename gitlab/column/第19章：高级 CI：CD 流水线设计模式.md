# 第19章：高级 CI/CD 流水线设计模式

## 1. 项目背景

> **业务场景**：一家公司从单仓库单体应用拆分为 20+ 个微服务仓库后，CI/CD 变成了噩梦——每个仓库的 `.gitlab-ci.yml` 都是独立维护的，80% 的配置是重复的。更糟的是，微服务之间有依赖关系——前端改了接口，需要触发后端重新部署；公共库更新了，需要通知所有下游仓库。传统的线性 Pipeline（build → test → deploy）已经无法满足需求。

有一个典型的场景：前端团队改了 API 请求格式，向后兼容的，所以后端不需要改动。但因为 Pipeline 是线性的——后端 Job 必须在前端 Job 之后运行——每次前端的小改动都要等后端的所有测试跑完才能部署。前端团队抱怨"一个样式改动要等 20 分钟才能看到效果"。

另一个场景：CI 模板库更新了一个安全扫描规则，但下游 20 个仓库采用了不同的引用方式——有的 `include:local`，有的 `include:project`，有的直接 copy paste——更新根本传不下去。

**痛点放大**：当项目规模增长到多仓库、多服务时，Pipeline 设计需要考虑的不再是"怎么让代码跑起来"，而是"怎么让配置复用和变更传播可控"。DAG（有向无环图）、父子流水线（multi-project pipeline）、动态子流水线（dynamic child pipelines）、CI 模板工程化——这些都是微服务规模和 CI 复杂度增长后的必修课。

## 2. 项目设计——剧本式交锋对话

**场景**：CI/CD 架构改造讨论会，白板上画满了微服务之间的依赖关系图。

---

**小胖**："我们现在 20 个微服务仓库，每个都有自己的一份 `.gitlab-ci.yml`。我想统一加个 SAST 扫描，难道要改 20 个文件？"

**大师**："这就是 CI 模板工程化的作用。你应该创建一个公共 CI 模板仓库，把通用的 job 定义放在里面——lint、SAST、Docker build、Kubernetes deploy——然后各个微服务通过 `include` 引用。这样你改一次模板，所有下游仓库自动生效。"

**小白**："那 `include` 和 `extends` 有什么区别？我看文档两个都有。"

**大师**："`include` 是从外部文件引入配置内容——可以引入本仓库的其他 YAML 文件（local）、其他项目的文件（project）、远程 URL（remote）、GitLab 预置模板（template）。`extends` 是在当前文件内继承一个已定义的 job 模板——类似于代码中的'继承'。技术映射：`include` 像是从其他书里摘抄了一章过来，`extends` 像是大纲的二级目录继承了一级目录的格式。"

**小胖**："那父子流水线呢？我们微服务之间有依赖。"

**大师**："父子流水线解决的是跨仓库 CI 编排问题。比如你有一个是公共库 `common-utils`，被 5 个微服务依赖。当 `common-utils` 更新后，可以用 `trigger` 关键字在 Pipeline 中触发下游仓库的 Pipeline。这样下游仓库会自动验证新版本的公共库是否兼容。技术映射——这就像生产线：上游零件车间生产了新螺丝（公共库更新），自动通知下游装配车间去测试新螺丝是否合适。"

**小白**："那 DAG 流水线跟父子流水线有什么不同？"

**大师**："DAG（有向无环图）是同一条 Pipeline 内 job 之间的依赖关系——通过 `needs` 关键字定义。传统 stage 模型是串行的——Stage 2 的所有 job 必须等 Stage 1 全部完成才能开始。但 DAG 可以跳过不必要的等待：Stage 2 的 lint job 不需要等 Stage 1 的 Docker build，通过 `needs: []` 可以直接开始。"

---

## 3. 项目实战

### 环境准备

> **目标**：搭建一个包含 CI 模板工程化、DAG 流水线、父子流水线、动态子流水线的完整 CI/CD 体系。

**前置条件**：GitLab CE 17.x，至少 2 个项目（模板项目 + 业务项目）。

### 分步实现

#### 步骤1：CI 模板工程化——创建公共模板仓库

**目标**：创建一个公共 CI 模板项目，将所有通用的 job 定义集中管理。

```bash
# 创建公共模板项目
# Group: acme-corp/infra
# Project: ci-templates

git clone http://gitlab.local:8929/acme-corp/infra/ci-templates.git
cd ci-templates

# 创建模板目录结构
mkdir -p templates/{docker,nodejs,java,security}
```

**Node.js 项目模板**：

```yaml
# templates/nodejs/build.yml
.spec:
  image: node:20-alpine
  cache:
    key:
      files:
        - package-lock.json
    paths:
      - node_modules/

.node-build:
  extends: .spec
  stage: build
  script:
    - npm ci --prefer-offline
    - npm run build
  artifacts:
    paths:
      - dist/
    expire_in: 1 week

.node-test:
  extends: .spec
  stage: test
  script:
    - npm ci --prefer-offline
    - npm run test
  coverage: '/All files[^|]*\|[^|]*\s+([\d\.]+)/'
  artifacts:
    when: always
    reports:
      junit: junit.xml
      coverage_report:
        coverage_format: cobertura
        path: coverage/cobertura-coverage.xml

.node-lint:
  extends: .spec
  stage: quality
  script:
    - npm ci --prefer-offline
    - npm run lint
```

**安全扫描模板**：

```yaml
# templates/security/sast.yml
.sast:
  stage: security
  image: registry.gitlab.com/security-products/sast:latest
  variables:
    SAST_EXCLUDED_PATHS: "spec, test, tests, tmp"
  script:
    - /analyzer run
  artifacts:
    reports:
      sast: gl-sast-report.json
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
    - if: '$CI_COMMIT_BRANCH == "main"'
```

**Docker 构建模板**：

```yaml
# templates/docker/build.yml
.docker-build:
  stage: build-image
  image: 
    name: gcr.io/kaniko-project/executor:v1.19.0-debug
    entrypoint: [""]
  script:
    - |
      /kaniko/executor \
        --context $CI_PROJECT_DIR \
        --dockerfile $CI_PROJECT_DIR/Dockerfile \
        --destination $CI_REGISTRY_IMAGE:$CI_COMMIT_SHORT_SHA \
        --destination $CI_REGISTRY_IMAGE:latest \
        --cache=true \
        --cache-ttl=168h
  rules:
    - if: '$CI_COMMIT_BRANCH == "main"'
    - if: '$CI_COMMIT_TAG'
```

#### 步骤2：在业务项目中引用 CI 模板

**目标**：微服务项目通过 `include` 引用公共模板，最小化 `.gitlab-ci.yml`。

```yaml
# 业务项目的 .gitlab-ci.yml
# 只需定义项目特定的配置，其余全部从模板继承

stages:
  - quality
  - test
  - build
  - build-image
  - security
  - deploy

# 引用公共模板
include:
  - project: 'acme-corp/infra/ci-templates'
    ref: main
    file: 'templates/nodejs/build.yml'
  - project: 'acme-corp/infra/ci-templates'
    ref: main
    file: 'templates/security/sast.yml'
  - project: 'acme-corp/infra/ci-templates'
    ref: main
    file: 'templates/docker/build.yml'

# 项目特定的 job（复用模板 + 少量定制）
lint:
  extends: .node-lint

unit-test:
  extends: .node-test
  variables:
    DB_HOST: test-db.internal    # 项目特定变量

build-app:
  extends: .node-build

security-scan:
  extends: .sast

docker-image:
  extends: .docker-build

# 项目独有的 job
deploy-staging:
  stage: deploy
  image: alpine:latest
  script:
    - echo "Custom deploy logic for this project"
  environment:
    name: staging
  rules:
    - if: '$CI_COMMIT_BRANCH == "main"'
```

#### 步骤3：DAG 流水线——用 needs 优化并行度

**目标**：用 `needs` 实现跨 stage 并行，消除不必要的串行等待。

```yaml
# .gitlab-ci.yml - DAG 流水线示例
stages:
  - prepare
  - build
  - test
  - scan
  - deploy

# ===== 传统方式（串行，慢）=====
# Stage 1: install → (等待全部完成)
# Stage 2: build-backend, build-frontend → (等待全部完成)
# Stage 3: test, lint, scan → (等待全部完成)
# Stage 4: deploy

# ===== DAG 方式（并行，快）=====
install-deps:
  stage: prepare
  script: npm ci
  artifacts:
    paths: [node_modules/]

build-backend:
  stage: build
  script: npm run build:backend
  needs: [install-deps]
  artifacts:
    paths: [dist/backend/]

build-frontend:
  stage: build
  script: npm run build:frontend
  needs: [install-deps]
  artifacts:
    paths: [dist/frontend/]

# lint 不依赖任何 build——直接开始！
lint-job:
  stage: test
  script: npm run lint
  needs: []              # 空数组 = 不等待任何 job，立即开始

# 单元测试只依赖对应的 build
test-backend:
  stage: test
  script: npm run test:backend
  needs: [build-backend]

test-frontend:
  stage: test
  script: npm run test:frontend
  needs: [build-frontend]

# 安全扫描和部署可以并行（如果不冲突的话）
security-scan:
  stage: scan
  script: npm audit
  needs: [build-backend]

deploy-staging:
  stage: deploy
  script: echo "deploying"
  needs: [test-backend, test-frontend]
  # 不需要等 security-scan 完成！
  # 但可以依赖它（optional）：
  # needs: [{job: security-scan, optional: true}]

# 执行时间对比：
# 传统方式：install(2min) + build(3min) + test(2min) + scan(1min) + deploy(1min) = 9min
# DAG 方式：max(install(2min) → build(3min) → test(2min), lint(1min)) → deploy(1min) = 6min
# 节省 33% 时间！
```

#### 步骤4：父子流水线——跨仓库触发

**目标**：公共库更新后自动触发下游微服务的验证 Pipeline。

```yaml
# 公共库 common-utils 的 .gitlab-ci.yml
stages:
  - build
  - trigger-downstream

build-and-test:
  stage: build
  script:
    - npm ci && npm test

# 触发下游微服务 A 的 Pipeline
trigger-service-a:
  stage: trigger-downstream
  trigger:
    project: acme-corp/ecommerce/service-a
    branch: main
    strategy: depend    # 下游 Pipeline 的状态会影响上游
  rules:
    - if: '$CI_COMMIT_BRANCH == "main"'
  variables:
    UPSTREAM_COMMIT: $CI_COMMIT_SHORT_SHA

# 触发下游微服务 B 的 Pipeline
trigger-service-b:
  stage: trigger-downstream
  trigger:
    project: acme-corp/ecommerce/service-b
    branch: main
    strategy: depend

# 并行触发多个下游
trigger-all-services:
  stage: trigger-downstream
  parallel:
    matrix:
      - PROJECT: [service-a, service-b, service-c]
  trigger:
    project: acme-corp/ecommerce/${PROJECT}
    branch: main
  rules:
    - if: '$CI_COMMIT_BRANCH == "main"'
```

**下游服务的 `.gitlab-ci.yml`（接收上游触发）**：

```yaml
# 下游项目 service-a 的 .gitlab-ci.yml
verify-upstream:
  stage: test
  script:
    - echo "Triggered by upstream commit: $UPSTREAM_COMMIT"
    - npm ci
    - npm run test:integration
  rules:
    - if: '$CI_PIPELINE_SOURCE == "pipeline"'   # 由上游触发
    - if: '$CI_COMMIT_BRANCH == "main"'           # 或主分支推送
```

#### 步骤5：动态子流水线——按需生成下游 Pipeline

**目标**：根据代码变更动态决定触发哪些下游操作。

```yaml
# .gitlab-ci.yml
stages:
  - generate
  - child-trigger

# 生成配置文件（检测哪些微服务发生了变化）
generate-config:
  stage: generate
  image: alpine:latest
  script:
    - |
      echo "Detecting changes..."
      # 检查哪些目录有变更
      CHANGED_DIRS=$(git diff --name-only $CI_COMMIT_BEFORE_SHA $CI_COMMIT_SHA | cut -d'/' -f1 | sort -u)
      echo "Changed: $CHANGED_DIRS"

      # 生成动态子流水线配置
      cat > child-pipeline.yml << 'CHILD_PIPELINE'
      stages:
        - build
        - deploy
      CHILD_PIPELINE

      for dir in $CHANGED_DIRS; do
        if [ -f "$dir/Dockerfile" ]; then
          cat >> child-pipeline.yml << EOF
      build-${dir}:
        stage: build
        script: echo "Building ${dir}..."
        rules:
          - changes:
              - ${dir}/*
      EOF
        fi
      done

  artifacts:
    paths:
      - child-pipeline.yml

# 执行动态生成的子流水线
run-child-pipeline:
  stage: child-trigger
  trigger:
    include:
      - artifact: child-pipeline.yml
        job: generate-config
    strategy: depend
```

### 完整代码清单

- CI 模板仓库结构（步骤1）
- `templates/nodejs/build.yml`、`templates/security/sast.yml`
- 业务项目 `.gitlab-ci.yml`（步骤2）
- DAG 流水线示例（步骤3）
- 父子流水线示例（步骤4）

### 测试验证

```bash
# 验证1：模板引用正确加载
# 查看 Pipeline 页面 → 展开 job 详情 → 确认 job 来自正确的模板文件

# 验证2：DAG 并行执行
# Pipeline 图中 lint-job 应该在 install-deps 之前或同时运行
# 点击 "Show dependencies" 确认依赖关系

# 验证3：父子流水线连锁触发
# 推送到 common-utils 后
# 查看 service-a 和 service-b 的 Pipeline 列表
# 确认有来自上游触发的 Pipeline

# 验证4：动态子流水线
# 只修改 service-a/Dockerfile 并推送
# 确认子流水线只包含 service-a 的 job，不包含 service-b
```

## 4. 项目总结

### 设计模式对比

| 模式 | 适用场景 | 优点 | 缺点 |
|------|---------|------|------|
| 模板工程化 | 多项目统一 CI | 一处修改全局生效 | 模板变更影响面大 |
| DAG (needs) | 单 Pipeline 内并行 | 减少串行等待 | 依赖关系复杂时难维护 |
| 父子流水线 | 跨仓库依赖触发 | 自动化依赖传递 | 调试需要跨仓库追踪 |
| 动态子流水线 | 按变更生成下游 | 精确控制，减少无效 job | 实现复杂度高 |

### 适用场景

- **模板工程化**：任何有 3+ 个项目的组织都应采用
- **DAG**：Pipeline 有 5+ job 且 job 间有自然并行关系时
- **父子流水线**：微服务之间有代码依赖（公共库 → 下游服务）
- **动态子流水线**：Monorepo 中按变更来定向触发

### 注意事项

- **模板 `include` 的 `ref` 建议固定版本号或 tag**，避免模板变更影响已有项目
- **`needs` 破坏 stage 顺序约束**——确保被需要的 job 不会因为 stage 顺序问题而跳过
- **父子 Pipeline 的 `strategy: depend`**：父 Pipeline 的状态会被子 Pipeline 影响

### 常见踩坑经验

1. **模板 include 后 job 重复**：include 的模板中定义了 `stages`，主文件中也定义了。根因：`stages` 不会自动合并。解决：只在主文件中定义 `stages`，模板中不定义。
2. **`needs` 引用的 job 不存在**：写 `needs: [build]` 但没有名为 `build` 的 job。根因：job 名称在不同 include 文件中可能不一致。解决：统一命名规范，或使用 `needs: [{job: xxx, optional: true}]`。
3. **父子 Pipeline 无限循环触发**：B 在 API 变更时触发 A，A 又在成功时触发 B。根因：`trigger` + `CI_PIPELINE_SOURCE` 未设置终止条件。解决：检查 `$CI_PIPELINE_SOURCE`，避免 pipeline 类型的触发再次触发下游。

### 思考题

1. 如果 CI 模板工程的模板仓库有 100+ 个下游项目引用，现在需要对模板做一次 breaking change。如何设计发布策略来最小化影响？考虑版本化、灰度发布等方案。
2. 在 Monorepo 中使用动态子流水线时，如何避免重复构建公共依赖？比如 3 个子项目都依赖 `common` 包，每次 common 变更应该只构建一次。

> 答案见附录 D。

### 推广计划提示

- **开发**：模板工程化是降低 CI 维护成本的最高 ROI 投入
- **运维**：父子流水线的跨仓库关联让变更影响分析变得可追溯
- **架构师**：DAG + 父子流水线 + 动态生成的组合拳是微服务 CI 编排的成熟范式
