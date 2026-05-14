# 第40章：【高级篇综合实战】从零构建企业内部 DevOps 平台

## 1. 项目背景

> **业务场景**：一家金融科技公司拥有 2000+ 开发人员，分布在 8 个业务部门、120+ 个微服务中。公司每年花费 ¥300 万购买商业 DevOps 套件（代码托管 + CI/CD + 安全扫描 + 项目管理），但随着团队规模从 2000 人向 5000 人的三年扩张计划，商业套件的许可证费用将突破 ¥800 万/年。CTO 在董事会上拍板："自建！全部基于 GitLab！"

CTO 提出了硬性要求：代码托管、MR Review、CI/CD、安全扫描（SAST + 密钥检测 + 依赖扫描）、包仓库（Maven/NPM/PyPI）、容器镜像仓库、监控告警——全部基于 GitLab 统一平台。更关键的是，平台必须提供自服务能力——任何业务团队可以在不联系 DevOps 团队的情况下，自主创建项目、配置 CI、申请资源，5 分钟内开始写代码。

**痛点放大**：本章是全文 40 章的集大成之作。前面 39 章涵盖了从 Git 基础操作（第 3 章）到源码分析（第 32-36 章），从单机 Runner（第 10 章）到 SRE 零停机升级（第 39 章）的全部知识。但真正的挑战不是任何单一技术——而是如何将这些分散的知识编织成一套生产级的、可扩展的、自服务的 DevOps 平台。很多团队的问题是"知识有，但不成体系"：HA 架构搭了但监控没跟上，CI 模板写了但没人用，安全扫描配了但从未处置漏洞。本章要求你在动手之前先想清楚架构、标准化、安全治理、可观测性和 SRE 保障——这五个维度的规划是 40 章全部知识落地的图纸。

## 2. 项目设计——剧本式交锋对话

**场景**：DevOps 平台建设启动会。CTO 在白板上画出目标架构图后，三位核心成员展开了激烈讨论。

---

**小胖**（两眼放光）："既然是最终章，我们把 39 章讲过的所有技术全上一遍！HA 集群、Geo 多活、零停机升级、Prometheus 监控大盘、Grafana 面板、GitLab 扩展开发——一把梭，一个月搞定！"

**大师**（摇头）：“一把梭是你见过的所有项目中失败率最高的策略。2000 人的平台不是靠堆功能堆出来的，是靠分阶段、分优先级、逐步验证出来的。我建议分五阶段推进：第一阶段搭基础设施（HA + Runner 集群 + 备份恢复），确保代码托管和 CI 执行的底座是稳的；第二阶段建 CI/CD 标准化（模板工程 + 包仓库），让 120 个微服务能在统一框架下运行；第三阶段做安全治理（SAST + 合规 + Code Owner），把安全问题拦截在 MR 阶段；第四阶段建可观测性（监控 + 告警 + SLO），让平台从'能用'到'可控'；第五阶段做自服务和培训，把平台能力交给 2000 个开发者，而不是 5 个 DevOps 运维。技术映射——这就像造一座城市：先通水通电（infra），再建标准化厂房（CI/CD），然后装消防系统（security），接着布监控摄像头（observability），最后开放招商（self-service）。”

**小白**（推了推眼镜）："既然是 2000 人起步、5000 人目标，服务器怎么配？一台 2C4G 肯定扛不住吧？"

**大师**：“生产环境的标准配置：3 个 Rails 节点（8C 16G，负责 Web/API），3 个 Gitaly 节点（8C 32G + SSD，Git 仓库存储），PostgreSQL 主库（16C 64G + SSD，带只读副本），Redis 三实例分离部署（Cache + Queue + Shared State），PgBouncer 连接池（2 节点），2 个 GitLab Runner 节点（8C 16G，可按需扩展到 10 节点）。另外 Geo 备用站点同配置减半，用于灾备。自建机房月成本约 ¥2-3 万，公有云约 ¥5-8 万——跟每年 ¥300 万的商业套件相比，6 个月回本。”

**小胖**：“那 120 个微服务的 CI 怎么标准化？我总不能写 120 份不同的 `.gitlab-ci.yml` 吧？”

**大师**：“模板工程（Template Engineering）是核心。创建三个基础模板覆盖公司所有技术栈——`nodejs/build.yml`（Vue/React/NestJS）、`java/spring.yml`（Spring Boot/Maven）、`python/build.yml`（Flask/FastAPI）。每个模板封装了 build → test → security → docker-build 四阶段。业务项目的 `.gitlab-ci.yml` 只需要 6 行：声明 stages，用 `include` 引用模板并指定 `ref` 版本号。版本号是关键——模板用语义版本 `v1.2.0` 发布，业务项目可以锁定 `ref: v1.2` 自动跟随小版本更新，但大版本需要手动升级。技术映射——就像公司统一采购 3 种标准工装，新人来了直接领一套就能上生产线，不需要自己设计工具。”

**小白**：“那自服务怎么控制权限和防止资源滥用？如果任何团队都能无限制地创建项目和 Runner，会不会很快失控？”

**大师**：“自服务不是放手不管，而是'带护栏的自助'（Self-service with guardrails）。通过 Group 层级实现：`公司（Top-level Group）→ 部门（Subgroup）→ 团队（Subgroup）→ 项目`。每层配置默认权限、CI 变量继承和配额上限——团队 Subgroup 内可以自由创建项目但不超过 30 个/团队；Runner 只能使用部门级别的 Shared Runner，禁止自建特权 Runner；CI 分钟数按团队配额分配，超额自动暂停。同时通过 API 自动化 `onboard_team()` 接口——一个新团队入职，调用一次接口，自动创建 Subgroup、从模板初始化 3 个标准仓库（backend / frontend / docs）、添加成员、设置配额。5 分钟完成，零人工介入。技术映射——这就像公司食堂：你可以自由取餐（自服务），但不能把整锅菜端走（配额控制）。”

---

## 3. 项目实战

### 环境准备

| 组件 | 版本/配置 | 用途 | 关联章节 |
|------|---------|------|---------|
| GitLab CE | 17.x HA（3 Rails + 3 Gitaly + PG + Redis×3 + PgBouncer） | DevOps 平台核心 | 第17、39章 |
| GitLab Runner | Docker Executor × 10（small/medium/large 标签）+ K8s Executor × 3 | CI 执行集群 | 第10、18章 |
| Prometheus + Grafana | 独立部署（Docker Compose，不与 GitLab 内建共用） | 监控告警可观测 | 第28章 |
| AlertManager + PagerDuty | AlertManager → PagerDuty（P0）/ Slack（P1） | 告警路由 | 第28章 |
| MinIO | S3 兼容对象存储 | 备份存储 + 包仓库 | 第27、29章 |
| python-gitlab | 4.x + Python 3.12 | 自服务自动化脚本 | 第25章 |

### 第一阶段：基础设施搭建（第 1-2 周）

**目标**：让 GitLab 以 HA 模式稳定运行，Runner 集群可用，备份自动化。

```yaml
# docker-compose.ha.yml —— 关键组件拓扑（示意，实际需拆分为多机部署）
# 关联章节：第17章（HA 架构）、第29章（备份恢复）、第39章（SRE）
version: '3.8'
services:
  # 3 个 Rails 节点由 Nginx 做 TCP 负载均衡
  gitlab-rails-1:
    image: gitlab/gitlab-ce:17.8.0-ce.0
    hostname: rails-1.internal
    volumes:
      - /srv/gitlab-1/config:/etc/gitlab
      - /srv/gitlab-1/logs:/var/log/gitlab
    environment:
      GITALY_SERVER_LIST: "gitaly-1:8075,gitaly-2:8075,gitaly-3:8075"
      POSTGRES_HOST: "pgbouncer"
      REDIS_CACHE_HOST: "redis-cache"
      REDIS_QUEUE_HOST: "redis-queue"
      REDIS_SHARED_HOST: "redis-shared"
    deploy:
      replicas: 3

  gitaly-1:
    image: gitlab/gitaly:17.8.0
    volumes:
      - /srv/git-data-1:/var/opt/gitlab/git-data:rw
    environment:
      GITALY_BOOTSTRAP_TOKEN: "${GITALY_TOKEN}"

  pgbouncer:
    image: edoburu/pgbouncer:1.22
    environment:
      DB_HOST: "pg-primary.internal"
      DB_USER: "gitlab"
      DB_PASSWORD: "${PG_PASSWORD}"
      POOL_MODE: transaction
      MAX_CLIENT_CONN: 2000
      DEFAULT_POOL_SIZE: 50
```

**Runner 集群注册**（关联章节：第 10、18 章）：

```bash
# 注册 10 个 Docker Runner，按资源等级打标签
for i in $(seq 1 5); do
  docker run -d --name runner-small-$i \
    --restart always \
    -v /srv/gitlab-runner/config-$i:/etc/gitlab-runner \
    gitlab/gitlab-runner:alpine-17.8.0
done

# 批量注册（每个 Runner 打不同标签）
for tag in small medium large; do
  gitlab-runner register \
    --non-interactive \
    --url "https://gitlab.acme-corp.com" \
    --token "$RUNNER_TOKEN" \
    --executor "docker" \
    --docker-image "docker:24-dind" \
    --tag-list "$tag,linux,amd64" \
    --run-untagged="false"
done
```

**备份与 S3 上传 cron**（关联章节：第 29 章）：

```bash
# /etc/cron.d/gitlab-backup —— 每日凌晨 2:00 全量备份 + S3 上传
0 2 * * * root /usr/bin/gitlab-backup create STRATEGY=copy \
  SKIP=artifacts,registry,packages \
  && aws s3 sync /var/opt/gitlab/backups/ s3://gitlab-backup-acme/daily/ \
  --storage-class STANDARD_IA && \
  # 清理 30 天前的本地备份
  find /var/opt/gitlab/backups/ -name '*.tar' -mtime +30 -delete
```

### 第二阶段：CI/CD 标准化（第 3-4 周）

**目标**：建立模板仓库，实现 120 个微服务统一 CI 框架。（关联章节：第 19 章 高级 CI、第 23 章 编排、第 27 章 包仓库）

**CI 模板仓库结构** —— `acme-corp/infra/ci-templates`：

```yaml
# ===== templates/nodejs/build.yml =====
# Node.js 项目标准模板（Vue/React/NestJS/Express）
.node-build:
  stage: build
  image: node:${NODE_VERSION:-20-alpine}
  script:
    - npm ci --prefer-offline
    - npm run build --if-present
  cache:
    key:
      files: [package-lock.json]
    paths: [node_modules/]

.node-test:
  stage: test
  image: node:${NODE_VERSION:-20-alpine}
  script:
    - npm ci --prefer-offline
    - npm test -- --coverage --reporters=jest-junit
  artifacts:
    when: always
    reports:
      junit: junit.xml
      coverage_report:
        coverage_format: cobertura
        path: coverage/cobertura-coverage.xml

.node-docker:
  stage: docker-build
  image:
    name: gcr.io/kaniko-project/executor:v1.23.0-debug
    entrypoint: [""]
  script:
    - |
      /kaniko/executor \
        --context ${CI_PROJECT_DIR} \
        --dockerfile Dockerfile \
        --destination ${CI_REGISTRY_IMAGE}:${CI_COMMIT_SHORT_SHA} \
        --destination ${CI_REGISTRY_IMAGE}:latest \
        --cache=true --cache-ttl=168h
  rules: [{ if: '$CI_COMMIT_BRANCH == "main"' }]
  needs:
    - job: node-test
    - job: node-build
```

```yaml
# ===== templates/java/spring.yml =====
# Spring Boot / Maven 项目标准模板
.spring-build:
  stage: build
  image: maven:3.9-eclipse-temurin-17
  script:
    - mvn package -DskipTests -Dmaven.repo.local=.m2/repository
  cache:
    key:
      files: [pom.xml]
    paths: [.m2/repository/]
  artifacts:
    paths: [target/*.jar]

.spring-test:
  stage: test
  image: maven:3.9-eclipse-temurin-17
  script:
    - mvn test
  artifacts:
    when: always
    reports:
      junit: target/surefire-reports/TEST-*.xml

.spring-docker:
  stage: docker-build
  image: gcr.io/kaniko-project/executor:v1.23.0-debug
  entrypoint: [""]
  script:
    - |
      /kaniko/executor --context ${CI_PROJECT_DIR} \
        --dockerfile Dockerfile \
        --destination ${CI_REGISTRY_IMAGE}:${CI_COMMIT_SHORT_SHA} \
        --cache=true
  rules: [{ if: '$CI_COMMIT_BRANCH == "main"' }]
  needs: [{ job: spring-build }, { job: spring-test }]
```

```yaml
# ===== templates/python/build.yml =====
# Python / Flask / FastAPI 项目标准模板
.python-build:
  stage: build
  image: python:${PYTHON_VERSION:-3.12-slim}
  script:
    - pip install --cache-dir .pip-cache -r requirements.txt
  cache:
    key:
      files: [requirements.txt]
    paths: [.pip-cache/]

.python-test:
  stage: test
  image: python:${PYTHON_VERSION:-3.12-slim}
  script:
    - pip install --cache-dir .pip-cache -r requirements.txt
    - pip install pytest pytest-cov pytest-xdist
    - pytest --junitxml=junit.xml --cov=. --cov-report=xml
  artifacts:
    when: always
    reports:
      junit: junit.xml
      coverage_report:
        coverage_format: cobertura
        path: coverage.xml
```

**业务项目使用模板** —— 只需 6-15 行（关联章节：第 19 章 `include` + `extends`）：

```yaml
# payment-service/.gitlab-ci.yml
# 支付服务 —— Spring Boot 微服务
include:
  - project: 'acme-corp/infra/ci-templates'
    ref: v2.3          # 锁定大版本，自动获取 v2.3.x 补丁更新
    file:
      - 'templates/java/spring.yml'
      - 'templates/security/sast.yml'
      - 'templates/security/dependency-scanning.yml'

stages:
  - build
  - test
  - security
  - docker-build
  - deploy

build:
  extends: .spring-build

test:
  extends: .spring-test

sast:
  extends: .sast

dependency-scan:
  extends: .dependency-scanning

deploy-staging:
  stage: deploy
  extends: .spring-deploy
  variables:
    ENV_NAME: staging
  rules: [{ if: '$CI_COMMIT_BRANCH == "main"' }]
```

**包仓库配置**（关联章节：第 27 章 —— Maven / PyPI / NPM）：

```xml
<!-- pom.xml —— Maven 私有仓库推送 -->
<distributionManagement>
  <repository>
    <id>gitlab-maven</id>
    <url>https://gitlab.acme-corp.com/api/v4/projects/${env.CI_PROJECT_ID}/packages/maven</url>
  </repository>
</distributionManagement>
```

```yaml
# .gitlab-ci.yml —— PyPI 包发布 job
publish-pypi:
  stage: publish
  image: python:3.12-slim
  script:
    - pip install twine build
    - python -m build
    - TWINE_PASSWORD=${CI_JOB_TOKEN} TWINE_USERNAME=gitlab-ci-token \
      twine upload --repository-url \
        https://gitlab.acme-corp.com/api/v4/projects/${CI_PROJECT_ID}/packages/pypi dist/*
  rules: [{ if: '$CI_COMMIT_TAG' }]
```

### 第三阶段：安全治理（第 5-6 周）

**目标**：安全扫描集成到 MR 流程，Code Owner 准入控制，100% 漏洞在合并前拦截。（关联章节：第 5 章 分支保护、第 21 章 SAST、第 22 章 合规扫描）

```yaml
# ===== templates/security/sast.yml =====
# MR 创建时自动触发 SAST + Secret Detection + Dependency Scanning
.sast:
  stage: security
  image: registry.gitlab.com/security-products/sast:latest
  variables:
    SAST_EXPERIMENTAL_FEATURES: "true"
  script:
    - /analyzer run
  artifacts:
    reports:
      sast: gl-sast-report.json
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'

.secret-detection:
  stage: security
  image: registry.gitlab.com/security-products/secret-detection:latest
  script:
    - /analyzer run
  artifacts:
    reports:
      secret_detection: gl-secret-detection-report.json
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'

.dependency-scanning:
  stage: security
  image: registry.gitlab.com/security-products/dependency-scanning:latest
  script:
    - /analyzer run
  artifacts:
    reports:
      dependency_scanning: gl-dependency-scanning-report.json
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
```

**Code Owner 文件**（关联章节：第 5 章 保护规则）：

```
# .gitlab/CODEOWNERS —— 关键路径强制审批
# 支付模块 —— 必须支付负责人 + 合规团队双审批
payment/**     @payment-lead @compliance-team
wallet/**      @wallet-lead @compliance-team

# KYC / 用户隐私数据模块
kyc/**         @kyc-lead @privacy-officer

# 基础设施配置 —— 必须 DevOps 审批
.gitlab-ci.yml              @devops-leads
templates/java/**           @devops-leads @java-arch
templates/security/**       @devops-leads @security-team
.Dockerfile                 @devops-leads
```

**Merge Checks 配置**（通过 API 批量开启，关联章节：第 25 章 API）：

```bash
# 批量对所有项目开启 Merge Checks
# Pipelines must succeed + All threads resolved + 最少 2 审批人（关键项目）
curl --request PUT \
  --header "PRIVATE-TOKEN: ${GITLAB_TOKEN}" \
  --header "Content-Type: application/json" \
  --data '{
    "only_allow_merge_if_pipeline_succeeds": true,
    "only_allow_merge_if_all_discussions_are_resolved": true,
    "allow_merge_on_skipped_pipeline": false,
    "approvals_before_merge": 2
  }' \
  "${GITLAB_URL}/api/v4/projects/${PROJECT_ID}/approvals"

# 合规组项目额外要求：禁止 Approver 审批自己的 MR
curl --request POST \
  --header "PRIVATE-TOKEN: ${GITLAB_TOKEN}" \
  --data '{
    "name": "compliance",
    "rule_type": "any_approver",
    "approvals_required": 1,
    "eligible_approvers": [143, 278, 399]
  }' \
  "${GITLAB_URL}/api/v4/projects/${PROJECT_ID}/approval_rules"
```

### 第四阶段：可观测性（第 7-8 周）

**目标**：建 RED 监控面板、分级告警规则、SLO 跟踪体系。（关联章节：第 28 章 Prometheus、第 39 章 SRE 实践）

**SLO 定义**：
- 可用性：99.9%（月度允许宕机 ≤43 分钟）
- API P95 延迟：< 500ms
- CI Pipeline 启动延迟：< 3 秒（从 Push 到 Job Created）
- MR 处理延迟：< 2 秒（从 MR 创建到 Pipeline 触发）

**Prometheus 告警规则**（关联章节：第 28 章）：

```yaml
# /etc/prometheus/rules/gitlab-platform.yml
groups:
  - name: gitlab-slo
    interval: 30s
    rules:
      # ===== P0 告警（5 分钟内触发 → PagerDuty）=====
      - alert: GitLabDown
        expr: up{job="gitlab-rails"} == 0
        for: 1m
        labels:
          severity: P0
          channel: pagerduty
        annotations:
          summary: "GitLab Rails 节点宕机（{{ $labels.instance }}）"
          runbook: "https://wiki.acme-corp.com/sre/gitlab-down"

      - alert: GitLabErrorRate5xx
        expr: rate(gitlab_errors_total{status=~"5.."}[5m]) > 1
        for: 3m
        labels:
          severity: P0
          channel: pagerduty
        annotations:
          summary: "GitLab 5xx 错误率超过 1 req/s"
          description: "当前 5xx 速率: {{ $value }} req/s"

      # ===== P1 告警（15 分钟内触发 → Slack #incidents）=====
      - alert: GitLabSidekiqBacklog
        expr: gitlab_sidekiq_queue_size > 10000
        for: 10m
        labels:
          severity: P1
          channel: slack-incidents
        annotations:
          summary: "Sidekiq 队列积压 > 10000（{{ $value }} jobs）"

      - alert: GitalyLatencyP95
        expr: histogram_quantile(0.95, rate(gitaly_requests_duration_seconds_bucket[5m])) > 0.5
        for: 10m
        labels:
          severity: P1
          channel: slack-incidents
        annotations:
          summary: "Gitaly P95 延迟 > 500ms（{{ $value }}s）"

      - alert: CIRunnerExhaustion
        expr: sum(gitlab_runner_running_jobs) / sum(gitlab_runner_total_jobs_capacity) > 0.85
        for: 10m
        labels:
          severity: P1
          channel: slack-incidents
        annotations:
          summary: "Runner 集群容量使用率 > 85%（{{ $value | humanizePercentage }}）"

      # ===== P2 告警（1 小时内触发 → Slack #ops-info）=====
      - alert: GitLabDiskLow
        expr: disk_used_percent{mountpoint="/var/opt/gitlab"} > 80
        for: 30m
        labels:
          severity: P2
          channel: slack-ops-info
        annotations:
          summary: "GitLab 磁盘使用率 > 80%（{{ $labels.instance }}）"

      - alert: BackupSkipped
        expr: time() - gitlab_backup_last_success_timestamp > 86400
        for: 1h
        labels:
          severity: P2
          channel: slack-ops-info
        annotations:
          summary: "GitLab 备份超过 24 小时未执行"
```

**Grafana RED Dashboard**（Rate / Errors / Duration —— 关联章节：第 28 章）：

```json
{
  "dashboard": {
    "uid": "acme-gitlab-red",
    "title": "GitLab Platform - RED Metrics",
    "tags": ["gitlab", "sre", "platform"],
    "panels": [
      {
        "title": "Request Rate (RPS)",
        "targets": [{
          "expr": "sum(rate(rails_requests_total[1m])) by (controller)",
          "legendFormat": "{{controller}}"
        }],
        "gridPos": {"x": 0, "y": 0, "w": 12, "h": 8}
      },
      {
        "title": "Error Rate (5xx %)",
        "targets": [{
          "expr": "sum(rate(gitlab_errors_total{status=~\"5..\"}[5m])) / sum(rate(rails_requests_total[5m])) * 100",
          "legendFormat": "5xx Error %"
        }],
        "thresholds": [{"value": 0.1, "color": "green"}, {"value": 1, "color": "red"}],
        "gridPos": {"x": 12, "y": 0, "w": 12, "h": 8}
      },
      {
        "title": "API Latency P95",
        "targets": [{
          "expr": "histogram_quantile(0.95, sum(rate(rails_request_duration_seconds_bucket[5m])) by (le, controller))",
          "legendFormat": "{{controller}} P95"
        }],
        "gridPos": {"x": 0, "y": 8, "w": 24, "h": 10}
      },
      {
        "title": "CI Pipeline Startup Latency",
        "targets": [{
          "expr": "histogram_quantile(0.95, rate(gitlab_ci_pipeline_startup_duration_seconds_bucket[5m]))",
          "legendFormat": "Pipeline Startup P95"
        }],
        "thresholds": [{"value": 3, "color": "green"}, {"value": 10, "color": "red"}],
        "gridPos": {"x": 0, "y": 18, "w": 12, "h": 8}
      }
    ]
  }
}
```

**SLO 跟踪 —— Prometheus 记录规则 + GitLab API 月报**（关联章节：第 25 章、第 28 章）：

```yaml
# /etc/prometheus/rules/slo-recording.yml
groups:
  - name: gitlab-slo-recording
    rules:
      - record: job:gitlab_availability:ratio
        expr: |
          avg_over_time(
            (sum(up{job="gitlab-rails"}) / count(up{job="gitlab-rails"}))[30d:]
          ) * 100

      - record: job:gitlab_api_p95_latency:seconds
        expr: |
          histogram_quantile(0.95,
            sum(rate(rails_request_duration_seconds_bucket[30d])) by (le)
          )
```

```bash
# SLO 月报生成脚本（通过 GitLab API 获取 Pipeline 延迟统计）
curl --header "PRIVATE-TOKEN: ${GITLAB_TOKEN}" \
  "${GITLAB_URL}/api/v4/projects/${PLATFORM_PROJECT_ID}/pipelines?per_page=100&updated_after=$(date -d '30 days ago' -I)" | \
  python3 -c "
import json, sys, datetime as dt
pipelines = json.load(sys.stdin)
total = len(pipelines)
succeeded = sum(1 for p in pipelines if p['status'] == 'success')
print(f'SLO 月报 — {dt.date.today().strftime(\"%Y-%m\")}')
print(f'  可用性: Probed via Prometheus (avg_over_time)')
print(f'  Pipeline 成功率: {succeeded}/{total} = {succeeded/total*100:.1f}%')
"
```

### 第五阶段：自服务（第 9-10 周）

**目标**：业务团队通过一次 API 调用完成入职，5 分钟内开始编码。（关联章节：第 25 章 API + python-gitlab）

```python
#!/usr/bin/env python3
"""
onboard_team.py —— 自服务团队入职脚本
用法: python onboard_team.py --team payment --lead david.wang --parent fintech
关联章节: 第25章（API 编程）、第5章（保护规则）、第13章（CI Variables）
"""
import argparse
import sys
import logging
from typing import Optional
import gitlab

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ===== 配置（从环境变量读取，不硬编码 Token）=====
GITLAB_URL = "https://gitlab.acme-corp.com"
GITLAB_TOKEN = "glpat-xxxxxxxx"           # 生产环境从 Vault 或 CI Variable 注入
PARENT_GROUP_ID = 42                        # acme-corp 根 Group ID
TEMPLATE_PROJECT_ID = 100                   # 项目模板仓库 ID
MAX_PROJECTS_PER_TEAM = 30                  # 配额上限
DEFAULT_CI_MINUTES_QUOTA = 10000            # CI 分钟配额（分钟/月）

def connect_gitlab() -> gitlab.Gitlab:
    """连接 GitLab 实例"""
    try:
        gl = gitlab.Gitlab(GITLAB_URL, private_token=GITLAB_TOKEN, ssl_verify=True)
        gl.auth()
        logger.info(f"已连接 GitLab: {gl.user.username}")
        return gl
    except gitlab.exceptions.GitlabAuthenticationError as e:
        logger.fatal(f"GitLab 认证失败: {e}")
        sys.exit(1)

def create_subgroup(gl: gitlab.Gitlab, team_name: str, team_path: str,
                    parent_id: int) -> gitlab.v4.objects.GroupSubgroup:
    """在父 Group 下创建团队 Subgroup"""
    parent = gl.groups.get(parent_id)
    try:
        subgroup = gl.groups.create({
            "name": team_name,
            "path": team_path,
            "parent_id": parent.id,
            "visibility": "internal",
            "description": f"团队 {team_name} 的代码仓库 —— 自服务创建",
            "project_creation_level": "developer",     # 允许开发者自主创建项目
            "subgroup_creation_level": "maintainer",   # 只有 Maintainer 可以创建子 Group
        })
        logger.info(f"Subgroup 创建成功: {subgroup.full_path}")
        return subgroup
    except gitlab.exceptions.GitlabCreateError as e:
        if "has already been taken" in e.error_message:
            logger.warning(f"Subgroup {team_path} 已存在，跳过创建")
            return gl.groups.get(f"{parent.full_path}/{team_path}")
        raise

def apply_quota_policy(subgroup: gitlab.v4.objects.GroupSubgroup,
                       max_projects: int = MAX_PROJECTS_PER_TEAM) -> None:
    """应用配额策略 —— 通过 Webhook 或定时任务定期巡检"""
    current_projects = len(subgroup.projects.list(all=True))
    if current_projects > max_projects:
        logger.warning(
            f"⚠ 团队 {subgroup.full_path} 项目数 {current_projects} 超过配额 {max_projects}！"
            f"项目创建权限已被自动撤销，请联系 DevOps 团队。"
        )
        # 实际实施：调 API 关闭 project_creation_level
        subgroup.project_creation_level = "noone"
        subgroup.save()

def create_project_from_template(gl: gitlab.Gitlab, subgroup, project_name: str,
                                  template_ref: str) -> dict:
    """从模板仓库 fork 创建标准项目，并配置 CI Variables"""
    try:
        # 基于模板仓库创建项目
        project = gl.projects.create({
            "name": project_name,
            "path": project_name,
            "namespace_id": subgroup.id,
            "description": f"标准项目 —— 继承自模板 v{template_ref}",
            "merge_method": "squash",
            "merge_requests_template": "## 变更描述\n\n## 测试验证\n\n## 关联 Issue\n",
        })

        # 配置默认 CI 变量
        for key, value in {
            "DOCKER_REGISTRY": f"registry.acme-corp.com/{subgroup.full_path}",
            "TEMPLATE_REF": template_ref,
            "DEFAULT_ENV": "staging",
        }.items():
            project.variables.create({"key": key, "value": value, "protected": False})

        # 配置分支保护：main 分支禁止直接 push，必须 MR
        project.protectedbranches.create({
            "name": "main",
            "push_access_level": 0,   # No one
            "merge_access_level": 40,  # Maintainers
        })

        logger.info(f"项目 {project.path_with_namespace} 创建完成")
        return {"id": project.id, "url": project.web_url}

    except gitlab.exceptions.GitlabCreateError as e:
        logger.error(f"项目 {project_name} 创建失败: {e}")
        return {"error": str(e)}

def onboard_team(gl: gitlab.Gitlab, team_name: str, lead_username: str,
                 parent_group_id: int = PARENT_GROUP_ID,
                 template_ref: str = "v2.3") -> Optional[dict]:
    """
    核心函数：一键入职新团队
    - 创建 Subgroup
    - 初始化 3 个标准仓库（backend / frontend / docs）
    - 添加团队 Leader 为 Maintainer
    - 设置配额策略
    """
    team_path = team_name.lower().replace(" ", "-")
    logger.info(f"===== 开始入职团队: {team_name} (路径: {team_path}) =====")

    # Step 1: 创建 Subgroup
    subgroup = create_subgroup(gl, team_name, team_path, parent_group_id)

    # Step 2: 初始化标准项目
    results = {}
    for name, lang in [("backend", "java"), ("frontend", "nodejs"), ("docs", "python")]:
        result = create_project_from_template(
            gl, subgroup, f"{team_name}-{name}", template_ref
        )
        results[f"{name}"] = result

    # Step 3: 添加团队 Leader
    try:
        users = gl.users.list(username=lead_username)
        if users:
            member = subgroup.members.create({
                "user_id": users[0].id,
                "access_level": gitlab.const.AccessLevel.MAINTAINER,
            })
            logger.info(f"用户 {lead_username} 已添加为 {subgroup.full_path} 的 Maintainer")
        else:
            logger.error(f"未找到用户 {lead_username}，请确认用户名正确")
    except gitlab.exceptions.GitlabCreateError as e:
        if "already exists" in e.error_message:
            logger.info(f"用户 {lead_username} 已是组成员，跳过")
        else:
            raise

    # Step 4: 配额检查
    apply_quota_policy(subgroup)

    logger.info(f"===== 团队 {team_name} 入职完成 =====")
    return {
        "team": team_name,
        "group_path": subgroup.full_path,
        "projects": results,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="自服务团队入职工具")
    parser.add_argument("--team", required=True, help="团队名称")
    parser.add_argument("--lead", required=True, help="团队 Leader 用户名")
    parser.add_argument("--parent", type=int, default=PARENT_GROUP_ID,
                        help="父 Group ID")
    parser.add_argument("--template-ref", default="v2.3",
                        help="CI 模板版本")

    args = parser.parse_args()
    gl_instance = connect_gitlab()
    result = onboard_team(
        gl_instance,
        team_name=args.team,
        lead_username=args.lead,
        parent_group_id=args.parent,
        template_ref=args.template_ref,
    )
    print(f"\n入职结果: {result}")
```

### 验收标准

| 指标 | 目标值 | 验证方式 | 40 章知识映射 |
|------|--------|---------|-------------|
| 平台可用性 | ≥ 99.9%（月度） | Prometheus SLO recording rule + Grafana | 第17章 HA、第28章 监控、第39章 SRE |
| CI 模板采纳率 | > 90%（≥108/120 项目使用标准模板） | GitLab API 统计 `include` 引用数 | 第19章 模板、第23章 编排 |
| 安全漏洞 MR 拦截率 | 100%（所有漏洞在合并前发现） | Merge Checks + SAST 报告审计 | 第21-22章 安全扫描 |
| CI Runner 排队时间 | P95 < 10 秒 | Runner Analytics API | 第10、18章 Runner 管理 |
| 包仓库命中率 | > 80%（依赖从内部仓库拉取） | Package Registry Analytics | 第27章 包仓库 |
| 备份 RPO | < 24 小时 | cron 日志 + S3 文件时间戳 | 第29章 备份恢复 |
| 团队自服务覆盖率 | > 80%（≥6/8 部门通过 API 自主入职） | onboard_team 调用日志 | 第25章 API 编程、第5章 权限管理 |

### 测试验证

```bash
# ===== 第一阶段验证：基础设施健康 =====
# 检查各组件状态
curl -s https://gitlab.acme-corp.com/-/health | jq .
# {"status":"ok","database":"ok","redis":"ok","gitaly":"ok"}

# 验证 Runner 在线
curl --header "PRIVATE-TOKEN: $GITLAB_TOKEN" \
  "$GITLAB_URL/api/v4/runners/all?scope=online" | jq 'length'
# 预期输出: >= 10

# ===== 第二阶段验证：CI 模板可用 =====
# 在沙箱项目中引用模板，触发 Pipeline
curl --header "PRIVATE-TOKEN: $GITLAB_TOKEN" \
  -X POST "$GITLAB_URL/api/v4/projects/$SANDBOX_PROJECT/pipeline?ref=main" | \
  jq '.status'
# 预期: "created" → Minutes later: "success"

# ===== 第三阶段验证：安全扫描生效 =====
# 提交含漏洞的测试代码，创建 MR，确认 SAST 报告产生
curl --header "PRIVATE-TOKEN: $GITLAB_TOKEN" \
  "$GITLAB_URL/api/v4/projects/$SANDBOX_PROJECT/merge_requests/1/discussions" | \
  jq '.[].notes[] | select(.body | contains("SAST detected"))'
# 预期: 有安全相关的 Discussion

# ===== 第四阶段验证：告警触发演练 =====
# 人为制造故障
ssh gitaly-1 "sudo gitlab-ctl stop gitaly"
sleep 30
# 检查 AlertManager 和 PagerDuty
curl -s http://alertmanager:9093/api/v2/alerts | jq '.[] | select(.labels.alertname == "GitalyDown") | .status'
# 预期: "firing"
ssh gitaly-1 "sudo gitlab-ctl start gitaly"

# ===== 第五阶段验证：自服务入职 =====
python3 onboard_team.py --team quality-assurance --lead alice.li --template-ref v2.3
# 预期: 3 秒内创建 Subgroup + 3 个项目 + 添加成员
curl --header "PRIVATE-TOKEN: $GITLAB_TOKEN" \
  "$GITLAB_URL/api/v4/groups/fintech/quality-assurance" | jq '.path'
# 预期: "quality-assurance"
```

## 4. 项目总结

### 40 章知识图谱

```
┌────────────────────────────────────────────────────────────────┐
│                     第 40 章：平台总装                             │
└────────────────────────────────────────────────────────────────┘
                              ▲
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
   ┌────┴────┐          ┌────┴────┐          ┌────┴────┐
   │ 高级篇   │          │ 中级篇   │          │ 基础篇   │
   │ (32-40) │          │ (17-31) │          │ (1-16)  │
   └────┬────┘          └────┬────┘          └────┬────┘
        │                     │                     │
   ┌────┴──────────┐   ┌─────┴──────────┐   ┌──────┴─────────┐
   │ 源码 (32-36)  │   │ 架构 (17-18)   │   │ 入门 (1-2)     │
   │ 扩展 (37)     │   │ CI进阶 (19-20) │   │ Git (3-5)      │
   │ 调优 (38)     │   │ 安全 (21-22)   │   │ 协作 (6-8)     │
   │ SRE (39)      │   │ 编排 (23-24)   │   │ CI基础 (9-15)  │
   │ 平台 (40)     │   │ 集成 (25-27)   │   │ 综合 (16)      │
   └───────────────┘   │ 监控 (28)       │   └────────────────┘
                       │ 备份 (29-30)    │
                       │ 综合 (31)       │
                       └────────────────┘

三层递进：
  基础篇 (1-16)  → 学会"怎么用" GitLab
  中级篇 (17-31) → 学会"怎么管好" GitLab
  高级篇 (32-40) → 学会"怎么把 GitLab 做成平台"
```

### 自建 vs 商业 DevOps 套件对比

| 维度 | 自建 GitLab 平台 | 商业 DevOps 套件 |
|------|-----------------|-----------------|
| 成本 | 硬件月费 ¥3-8 万，年费 ¥36-96 万 | ¥300 万/年起，5000 人时达 ¥800 万+ |
| 灵活性 | 完全可控：源码可改、扩展可写、流程可定制 | Vendor-locked：功能升级依赖厂商路线图 |
| 维护成本 | 需要 3-5 人 DevOps + SRE 团队 | 厂商负责 SLA，但出了问题需排队等工单 |
| 集成深度 | GitLab 生态内无缝（CI→Security→Deploy→Monitor） | 多产品拼装，数据孤岛常见 |
| 学习曲线 | 陡峭（需掌握 40 章全部知识） | 平缓（单产品 GUI 操作） |
| 适合场景 | 200 人以上，有专职 DevOps 团队 | 50 人以下，无 DevOps 人力的团队 |

### 推广路线图

| 阶段 | 时间 | 受众 | 关键交付 |
|------|------|------|---------|
| 基础篇 | 第 1 月 | 全体开发者 + QA | 全员会用 GitLab 做 Issue / MR / 基础 CI；新人入职手册完成 |
| 中级篇 | 第 2-3 月 | DevOps 工程师 + 安全团队 | HA 上线、CI 模板库 v1.0 发布、安全扫描集成到 MR、Grafana 监控就位 |
| 高级篇 | 第 4-6 月 | 架构师 + SRE + DevOps Lead | 源码级排障手册、自定义扩展上线、99.9% SLO 达成、5 阶段全流程验收 |

### 常见踩坑经验

1. **阶段执行顺序错误——安全治理放在 CI 标准化之前**：某团队急于满足合规要求，在 CI 模板还是一片空白的情况下先推 SAST 扫描。结果每个 MR 都因为"非安全原因"的 Pipeline 失败被阻塞，开发者为了绕过检查开始使用 `allow_failure: true`，安全扫描形同虚设。**教训**：安全治理必须建立在可运行的 CI 流程之上——先让 Pipeline 跑通，再加安全门禁。

2. **自服务开放但未设置配额——资源爆炸**：平台上线第二周，某团队一夜之间创建了 127 个项目，其中 90% 是空仓或"试一下"。Runner 集群被打满，正常业务的 CI 排队超过 30 分钟。**教训**：自服务必须在配额控制下运行。至少配置三项：项目数量上限、CI 分钟月度配额、Runner 标签隔离（核心业务 Runner 与实验项目 Runner 分离）。

3. **监控告警部署了但从未演练——"告警盲区"**：Prometheus + Grafana + AlertManager 全套上线，但告警规则从未被实际验证。一个月后 GitLab 磁盘写满宕机 4 小时，告警没有触发——因为 `disk_used_percent` 指标的 `mountpoint` label 写错了。**教训**：每季度至少一次 Chaos Engineering 演练，人为制造故障（进程停止、磁盘填满、网络分区），逐条验证告警规则是否在预期时间内触发、通知是否送达、值班人员是否响应。

### 最后的思考题

1. 本章 5 个阶段的推进顺序是经过精心设计的——infra → CI/CD → security → observability → self-service。假设 CTO 要求在 6 周内（而非 10 周）完成全平台交付，你会砍掉哪些非核心能力、压缩哪些阶段？优先级决策的依据是什么？

2. 如果请你向一位刚入职的 DevOps 新人推荐 40 章中的"必读前 5 章"（能最快上手干活）和"架构师进阶必读 5 章"（理解 GitLab 的设计哲学），你会分别推荐哪些？为什么？

> 答案见附录 D。

### 推广计划提示

- **开发者**：自服务入职后，开发者 5 分钟即可创建标准项目——关注你的 `.gitlab-ci.yml` 中 `include` 引用的模板版本号是否正确
- **DevOps 工程师**：本章是你 40 章学习的毕业答辩——五个阶段的每一行代码都应该能在前 39 章找到出处
- **安全团队**：关注 Code Owner 和 Merge Checks 的配置——安全不是"发现了漏洞"，而是"阻止有漏洞的代码进入主干"
- **架构师 / SRE**：99.9% 的 SLO 不是口号——Prometheus 记录规则、Grafana 面板、告警分级构成了可验证的可靠性承诺
- **CTO / 技术管理者**：本章提供了一个从 ¥300 万商业套件迁移到自建平台的完整蓝图——6 个月交付，投资回报周期 < 1 年
