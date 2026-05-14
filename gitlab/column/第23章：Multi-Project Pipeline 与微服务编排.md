# 第23章：Multi-Project Pipeline 与微服务编排

## 1. 项目背景

> **业务场景**：某公司有 20+ 个微服务，它们之间存在复杂的依赖链——前端依赖网关、网关依赖订单服务、订单服务依赖支付服务和用户服务。每次公共基础库（common-lib）更新后，需要手动通知所有下游团队"你们该升级了"。但实际情况是：通知发出去后，有的团队一周后才升级，有的团队根本不知道要升级，导致不同微服务运行着不同版本的公共库，出现各种奇奇怪怪的兼容性问题。

更棘手的是跨服务发布流程：一个新功能需要同时修改前端、网关和订单服务。没有跨仓库 Pipeline 编排的话，开发需要按顺序操作——先发订单服务，等它部署完，再发网关，再发前端。整个流程需要 2-3 个小时，而且全靠人工协调。

**痛点放大**：微服务不是"拆了就完事"，CI/CD 也需要对应的编排能力。Multi-Project Pipeline 让你可以定义仓库间的依赖和触发关系——公共库更新自动通知下游、多服务变更按依赖顺序自动部署。这不再是"CI/CD 流水线"，而是"CI/CD 管道网络"。

## 2. 项目设计——剧本式交锋对话

**场景**：微服务架构升级讨论会，白板上画着 20+ 个微服务之间的连线。

---

**小胖**："我们是不是可以用父子流水线来解决问题？common-lib 更新后触发下游？"

**大师**："基本思路是对的，但真实的微服务场景比父子流水线更复杂。比如有的服务需要等待多个上游完成后才能部署（订单服务需要等支付和用户服务都就绪），有的跨仓库发布需要按严格的顺序执行（数据库迁移先跑 → 后端部署 → 网关刷新路由 → 前端部署）。"

**小白**："这听起来像 DAG 跨仓库版本——Pipeline 的 DAG 只能在一个仓库内编排 job，跨仓库的话怎么定义依赖关系？"

**大师**："跨仓库的依赖关系通过 `trigger` + `needs` + `strategy: depend` 组合实现。上游 pipeline 中 `trigger` 下游项目，下游项目在自己的 Pipeline 中检查上游的触发信息，然后决定执行什么。你还可以用 `parallel:matrix` 一次性触发多个下游，或用 `downstream_pipeline_id` 做状态追踪。"

**小胖**："那如果下游的 Pipeline 正在进行中，我上游又做了一个新的变更——会不会出现竞争条件？"

**大师**："这确实是需要处理的问题。GitLab 提供了几种策略：`strategy: depend` 会让上游等待下游完成；你可以设置 `interruptible: true` 让新 Pipeline 自动取消旧的；或者通过环境变量传递上游 commit hash，下游检查后决定是否跳过。技术映射——这就像快递公司分拨中心：上游货到了要通知下游，但如果下游正在处理上一批货，需要决定是等它处理完还是直接覆盖。"

**小白**："感觉跨仓库编排很复杂，有没有简化方案？"

**大师**："从简单到复杂是：第一步，下游用 `CI_PIPELINE_SOURCE` 检查是否由上游触发，只执行必要步骤。第二步，上游用 `parallel:matrix` 批量触发。第三步，引入中间协调服务（如 Consul/etcd）跟踪各服务的部署版本。第四步，用 Service Mesh 做更精细的流量控制。别急着到第四步——先把前两步做好。"

---

## 3. 项目实战

### 环境准备

> **目标**：搭建一个 3 个微服务 + 1 个公共库的跨仓库 Pipeline 编排体系。

**前置条件**：GitLab CE 17.x，至少 4 个项目。

### 分步实现

#### 步骤1：创建示例微服务结构

**目标**：创建前端（frontend）、网关（gateway）、订单服务（order-service）、公共库（common-lib）4 个项目。

```bash
# 项目结构：
# acme-corp/
#   infra/
#     common-lib      ← 公共库（被其他 3 个服务依赖）
#   ecommerce/
#     order-service   ← 订单服务
#     gateway         ← API 网关
#     frontend        ← 前端应用

# 依赖关系：
# common-lib 被 order-service, gateway 依赖
# frontend 依赖 gateway 的 API 版本
# gateway 依赖 order-service 的接口
```

#### 步骤2：上游公共库的 Pipeline——触发下游

**目标**：common-lib 更新后，自动触发所有下游服务的验证 Pipeline。

```yaml
# common-lib 的 .gitlab-ci.yml
stages:
  - build
  - test
  - publish
  - trigger-downstream

# 下游服务列表
.downstream_services: &downstream_services
  - order-service
  - gateway

build-lib:
  stage: build
  image: node:20-alpine
  script:
    - npm ci && npm run build
    - npm pack  # 生成 .tgz 包
  artifacts:
    paths:
      - "*.tgz"
    expire_in: 1 hour

test-lib:
  stage: test
  image: node:20-alpine
  script:
    - npm ci && npm test

publish-lib:
  stage: publish
  image: node:20-alpine
  script:
    - echo "Publishing to private npm registry..."
    - npm publish --registry https://npm.internal
  rules:
    - if: '$CI_COMMIT_TAG'   # 只有打 tag 才正式发布

# 触发下游服务验证
trigger-downstream:
  stage: trigger-downstream
  parallel:
    matrix:
      - SERVICE: *downstream_services
  trigger:
    project: acme-corp/ecommerce/${SERVICE}
    branch: main
    strategy: depend
  rules:
    - if: '$CI_COMMIT_BRANCH == "main"'
  variables:
    UPSTREAM_PROJECT: $CI_PROJECT_NAME
    UPSTREAM_COMMIT: $CI_COMMIT_SHORT_SHA
    UPSTREAM_VERSION: $CI_COMMIT_TAG
```

#### 步骤3：下游服务的 Pipeline——接收上游触发

**目标**：下游服务收到上游触发后，自动验证新版本公共库的兼容性。

```yaml
# order-service 的 .gitlab-ci.yml
stages:
  - verify-upstream
  - build
  - test
  - deploy

# 验证上游公共库是否兼容
verify-common-lib:
  stage: verify-upstream
  image: node:20-alpine
  script:
    - echo "Verifying compatibility with common-lib"
    - echo "Triggered by: $UPSTREAM_PROJECT"
    - echo "Upstream commit: $UPSTREAM_COMMIT"
    - npm install @acme/common-lib@$UPSTREAM_VERSION
    - npm test      # 跑兼容性测试
    - npm run lint
  rules:
    - if: '$CI_PIPELINE_SOURCE == "pipeline"'  # 由上游触发
  allow_failure: false  # 不兼容则阻止后续部署

# 常规 CI（非上游触发时）
build-service:
  stage: build
  image: node:20-alpine
  script:
    - npm ci && npm run build
  rules:
    - if: '$CI_PIPELINE_SOURCE != "pipeline"'  # 非上游触发时运行
    - if: '$CI_COMMIT_BRANCH == "main"'
```

#### 步骤4：跨服务顺序发布——编排式 Pipeline

**目标**：前端新功能需要同时发布后端、网关和前端时，按顺序执行。

```yaml
# 协调项目 coordinator 的 .gitlab-ci.yml
# 这个项目负责编排跨服务的发布顺序

stages:
  - pre-check
  - deploy-backend
  - deploy-gateway
  - deploy-frontend

variables:
  RELEASE_VERSION: $CI_COMMIT_TAG

pre-check:
  stage: pre-check
  script:
    - echo "Release $RELEASE_VERSION starting..."
    - echo "1. order-service"
    - echo "2. gateway"
    - echo "3. frontend"

# Step 1: 部署订单服务
deploy-order-service:
  stage: deploy-backend
  trigger:
    project: acme-corp/ecommerce/order-service
    branch: main
    strategy: depend
  variables:
    DEPLOY_ENV: production
    RELEASE_TAG: $RELEASE_VERSION

# Step 2: 等待订单服务就绪后部署网关
deploy-gateway:
  stage: deploy-gateway
  trigger:
    project: acme-corp/ecommerce/gateway
    branch: main
    strategy: depend
  needs:
    - deploy-order-service  # 必须等待订单服务部署完成
  variables:
    DEPLOY_ENV: production
    ORDER_SERVICE_VERSION: $RELEASE_VERSION

# Step 3: 等待网关就绪后部署前端
deploy-frontend:
  stage: deploy-frontend
  trigger:
    project: acme-corp/ecommerce/frontend
    branch: main
    strategy: depend
  needs:
    - deploy-gateway
  variables:
    DEPLOY_ENV: production
    GATEWAY_VERSION: $RELEASE_VERSION

# 通知所有下游完成
notify-complete:
  stage: deploy-frontend
  script:
    - echo "Release $RELEASE_VERSION completed!"
  needs:
    - deploy-frontend
```

#### 步骤5：下游 Pipeline 状态追踪与告警

**目标**：上游 Pipeline 中实时追踪下游运行状态，失败时告警。

```yaml
# 在上游 Pipeline 中添加状态监控
monitor-downstream:
  stage: trigger-downstream
  image: alpine:latest
  before_script:
    - apk add --no-cache curl jq
  script:
    - |
      # 获取子 Pipeline 的状态
      # GitLab 会自动在 trigger job 中设置 CI_JOB_TOKEN
      echo "Monitoring downstream pipelines..."

      # 通过 API 查询下游 Pipeline 状态
      for project in order-service gateway; do
        echo "Checking ${project}..."
        PIPELINE_STATUS=$(curl -s --header "PRIVATE-TOKEN: $GITLAB_API_TOKEN" \
          "$CI_API_V4_URL/projects/acme-corp%2Fecommerce%2F${project}/pipelines?per_page=1" \
          | jq -r '.[0].status')
        echo "  ${project}: ${PIPELINE_STATUS}"

        if [ "$PIPELINE_STATUS" = "failed" ]; then
          echo "❌ ${project} pipeline failed!"
          # 发送告警通知
          curl -X POST "$SLACK_WEBHOOK" \
            -H "Content-Type: application/json" \
            -d "{\"text\": \"❌ ${project} downstream pipeline failed!\"}"
        fi
      done
  needs:
    - trigger-downstream
  when: always  # 即使 trigger 失败也执行
```

### 完整代码清单

- `common-lib/.gitlab-ci.yml`：上游公共库 Pipeline
- `order-service/.gitlab-ci.yml`：下游服务 Pipeline
- `coordinator/.gitlab-ci.yml`：跨服务编排 Pipeline

### 测试验证

```bash
# 验证1：公共库更新触发下游
# common-lib push 到 main → 查看 common-lib Pipeline
# 应看到 trigger-downstream stage 触发 order-service 和 gateway 的 Pipeline

# 验证2：下游验证失败时上游感知
# 在 common-lib 中引入一个 breaking change → push
# downstream Pipeline 的 verify-common-lib 应该失败
# upstream Pipeline 中的 trigger-downstream 应显示 failed

# 验证3：顺序发布
# coordinator 打 tag → 触发 Pipeline
# 确认 deploy-order-service → deploy-gateway → deploy-frontend 按顺序执行
# 前端部署必须在网关部署完成后才开始

# 验证4：API 查询跨项目 Pipeline 关系
curl --header "PRIVATE-TOKEN: $GITLAB_TOKEN" \
  "$GITLAB_URL/api/v4/projects/$PROJECT_ID/pipelines/1/bridges"
```

## 4. 项目总结

### 跨仓库编排策略对比

| 策略 | 复杂度 | 适用场景 | 优点 | 缺点 |
|------|--------|---------|------|------|
| CI 变量传递 | 低 | 公共库 → 下游 | 简单粗暴 | 缺乏状态追踪 |
| trigger + parallel | 中 | 一对多触发 | GitLab 原生支持 | 下游独立运行，无协调 |
| coordinator 项目 | 中 | 顺序发布 | 发布流程可审计 | 增加一个项目维护 |
| 外部协调器（Consul/etcd） | 高 | 大规模微服务 | 实时状态同步 | 需要额外基础设施 |

### 适用场景

- **公共库 → 多下游**：`trigger` + `parallel:matrix`
- **多服务顺序发布**：coordinator 项目 + `needs` 顺序控制
- **Monorepo 内部依赖**：`trigger` + `rules:changes`
- **大规模微服务（50+）**：考虑 Service Mesh + GitOps

**不适用场景**：
- 微服务之间没有代码依赖（不需要跨仓库触发）
- 3 个以下微服务的小项目（手动协调成本更低）

### 注意事项

- **`trigger:strategy: depend` 的上游 Pipeline 状态会被下游影响**——下游失败会导致上游也显示失败
- **避免循环触发**：A → B → A 会形成无限循环，务必在 CI 变量中设置终止条件
- **下游 Pipeline 的触发是异步的**：即使 `strategy: depend`，上游 job 也会在触发动作完成后立即结束（等待发生在 Pipeline 层面）
- **跨项目 CI 变量继承**：通过 `trigger:variables` 显式传递，不会自动继承

### 常见踩坑经验

1. **下游 Pipeline 无限触发**：配置了 `trigger` 但没设置 `rules:if`，导致下游 Pipeline 自身又触发了自己。根因：没有用 `CI_PIPELINE_SOURCE` 做条件判断。解决：在下游 `.gitlab-ci.yml` 中用 `rules: - if: '$CI_PIPELINE_SOURCE == "pipeline"'` 限制。
2. **下游找不到上游定义的变量**：在 trigger job 的 `variables` 中定义了变量，但下游读不到。根因：trigger job 的 variables 只向下游传递，不向上游回溯。解决：确认变量在 trigger 配置块中定义。
3. **并行触发太多下游导致 API 限流**：10 个 `parallel:matrix` 同时触发，部分失败。根因：GitLab API 有每分钟请求数限制。解决：分批触发或增加 `trigger` 之间的延迟。

### 思考题

1. 如果你需要在触发下游前，先检查下游当前是否有正在运行的 Pipeline，如果有则取消旧的再触发新的，应该如何实现？
2. 假如公司有 30 个微服务，其中 10 个依赖 common-lib。如何设计跨仓库 Pipeline 策略，避免 common-lib 一次小改动就触发所有 10 个服务的全量 CI（浪费资源）？

> 答案见附录 D。

### 推广计划提示

- **开发**：跨仓库触发让依赖升级的影响变得立即可见，大幅减少"依赖过期"问题
- **运维**：coordinator 项目可以作为所有发布操作的审计中心，每次跨服务发布都有完整记录
- **架构师**：跨仓库 Pipeline 的设计直接影响微服务架构的可维护性
