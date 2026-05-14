# 第31章：【中级篇综合实战】企业级 DevOps 平台搭建

## 1. 项目背景

> **业务场景**：一家 200 人的软件公司在过去半年里逐步引入了 GitLab 的各项中级功能——HA 架构、多 Executor Runner、安全扫描、包仓库、Prometheus 监控。但这些功能是"散装"的，各自独立运行，没有形成一条完整的 DevOps 链路。开发提交代码后需要手动触发安全扫描，CI 构建的镜像推送到 Registry 后没有人知道下一步该做什么，监控大盘挂了也没人发现——因为告警规则从来没有被真正验证过。

CTO 在季度总结会上拍桌子："我们买了这么多模块，每个模块都在用，但为什么研发效率反而下降了？因为没有人把这些东西串起来！"他举了一个例子：一个简单的 Bug 修复从代码提交到生产部署要经过 7 个环节，每个环节都需要人工确认——提交代码 → 等 Code Review → 手动触发安全扫描 → 手动构建镜像 → 手动推送 → 手动部署 → 手动更新 Jira。而理论上，除了 Code Review，其余 6 个环节全部可以自动化。

**痛点放大**：中级篇覆盖了架构设计（第17章）、Runner 深度配置（第18章）、高级 CI/CD 流水线（第19章）、Auto DevOps（第20章）、安全扫描（第21-22章）、跨仓库编排（第23章）、容器镜像（第24章）、API 编程（第25章）、Webhooks 集成（第26章）、包仓库（第27章）、Prometheus 监控（第28章）、备份恢复（第29章）、Pages 发布（第30章）。这 14 个模块单独看每个都很有用，但不串联起来，它们只是 14 个"孤岛"。本章的目标就是把它们全部打通，构建一条完整的自动化 DevOps 流水线。

## 2. 项目设计——剧本式交锋对话

**场景**：技术评审会，白板上贴着 14 个功能模块的便利贴。CTO、运维、开发坐在一起，讨论如何把这些散装模块拼成一个完整的 DevOps 平台。

---

**小胖**（指着白板上密密麻麻的便利贴）："大师，我们不是已经把 GitLab 的功能用上了吗？HA 架构有了，安全扫描配了，Registry 也在用——这不就挺好了吗？为什么还要做一个'平台'？"

**大师**："小胖，你家买了很多智能家电——智能灯、智能空调、智能窗帘——但如果你没有把它们接入同一个智能家居系统，那你每天还是要手动开灯、手动拉窗帘。我们现在的 GitLab 就是这个状态——功能模块都有，但它们是'手动'的。你要的是一套'自动化场景'——比如'出门模式'：一键关灯、关空调、锁门。"

**小胖**："哦，我懂了！那我们的'自动化场景'是什么？"

**大师**："核心场景就一个：**代码提交 → 生产部署，全自动**。具体来说，一个开发 push 了代码之后，应该自动触发这条链：MR 创建 → CI lint + test 自动跑 → SAST + Secret Detection 自动扫描 → Reviewer 审批 → 合并到 main → 自动构建 Docker 镜像 → 推送 Registry → 自动部署到 Staging → 自动部署到 Production（可选手动确认）→ 更新 Jira → 发飞书通知。整个过程除了 Code Review 那一环，其余全部自动。"

**小白**（推了推眼镜）："这个链路我画了一下，有 10 个节点。我的问题是——如果中间任何一个节点挂了，整个链路会断吗？比如安全扫描超时了，会阻塞后面的镜像构建吗？"

**大师**："好问题。这就是 DAG 流水线的价值——不是所有节点都是强依赖的。比如安全扫描和镜像构建可以并行——SAST 失败了不应该阻塞部署（只要不是 Critical 级别的漏洞）。所以我们用 `needs` 来定义精确的依赖关系，而不是让所有 job 在同一个 stage 里串行等待。技术映射——这就像外卖配送：骑手取餐（CI build）和餐厅出餐（SAST scan）可以同时进行，不需要骑手等厨师炒完菜再出发。"

**小胖**："那监控告警呢？我们现在有 Prometheus，但 Grafana 上的大盘从来没人看——告警规则也没验证过到底能不能触发。"

**大师**："告警规则必须通过'演练'来验证。我的建议是——每季度做一次 Chaos Engineering：人为制造故障（比如 stop puma、kill sidekiq、fill disk），然后观察告警是否在预期时间内触发、通知是否送达、值班人员是否响应。如果告警规则不演练，就等于没有告警——因为真正出问题的时候你会发现它根本没配对。"

**小白**："还有一个问题——这么多模块，全部串起来之后，我们怎么衡量这个 DevOps 平台的'效果'？有没有可量化的指标？"

**大师**："业界常用的衡量框架叫 DORA 四大指标：部署频率（Deployment Frequency）、变更前置时间（Lead Time for Changes）、变更失败率（Change Failure Rate）、故障恢复时间（Time to Restore Service）。我们可以在 GitLab CI Analytics 和 Prometheus 中直接取这些指标。技术映射——DORA 指标就像体检报告，告诉你 DevOps 平台是'健康'还是'亚健康'。"

**小胖**："那我们这 200 人的公司，DORA 指标应该定多少？"

**大师**："200 人团队的合理目标是：部署频率 ≥ 每天 1 次，变更前置时间 < 1 小时（从 commit 到 deploy），变更失败率 < 5%，恢复时间 < 15 分钟。达到这个标准，你们就是 DORA 定义的'高性能团队'。"

---

## 3. 项目实战

### 环境准备

| 组件 | 版本/配置 | 用途 |
|------|---------|------|
| GitLab CE | 17.x HA（3 节点） | DevOps 平台核心 |
| Docker Runner | 3 节点（small/medium/large 标签） | CI 执行器 |
| Prometheus + Grafana | 独立部署（Docker Compose） | 监控告警 |
| Node.js 项目 | Express API | 被 DevOps 化的示例项目 |
| Docker Registry | GitLab 内建 | 容器镜像存储 |

### 分步实现

#### 步骤1：搭建五层架构的自动化 CI/CD 流水线

**目标**：编写一条覆盖质量检查、安全扫描、镜像构建、自动部署、通知的完整流水线。

```yaml
# .gitlab-ci.yml - 五层完整流水线
stages:
  - quality            # 第1层：代码质量
  - security           # 第2层：安全扫描
  - build-image        # 第3层：镜像构建
  - deploy-staging     # 第4层：部署（Staging自动，Production手动）
  - notify             # 第5层：通知

# ===== 公共模板 =====
include:
  - project: 'acme-corp/infra/ci-templates'
    ref: v2.0.0
    file:
      - 'templates/nodejs/build.yml'
      - 'templates/security/sast.yml'
      - 'templates/docker/build.yml'

variables:
  NODE_VERSION: "20-alpine"
  IMAGE_TAG: $CI_REGISTRY_IMAGE:$CI_COMMIT_SHORT_SHA

# ===== 第1层：代码质量 =====
lint:
  extends: .node-lint
  stage: quality

unit-test:
  extends: .node-test
  stage: quality
  artifacts:
    reports:
      junit: junit.xml
      coverage_report:
        coverage_format: cobertura
        path: coverage/cobertura-coverage.xml

# ===== 第2层：安全扫描 =====
sast:
  extends: .sast
  stage: security

secret-detection:
  stage: security
  extends: .secret-detection

dependency-scan:
  extends: .dependency-scanning
  stage: security

# ===== 第3层：镜像构建 =====
docker-build:
  stage: build-image
  image:
    name: gcr.io/kaniko-project/executor:v1.19.0-debug
    entrypoint: [""]
  script:
    - |
      /kaniko/executor \
        --context $CI_PROJECT_DIR \
        --dockerfile Dockerfile \
        --destination $IMAGE_TAG \
        --destination $CI_REGISTRY_IMAGE:latest \
        --cache=true --cache-ttl=168h
  rules:
    - if: '$CI_COMMIT_BRANCH == "main"'
  needs:
    - job: unit-test
    - job: sast
      optional: true    # SAST 失败不阻塞构建
    - job: secret-detection

# ===== 第4层：部署 =====
deploy-staging:
  stage: deploy-staging
  image: alpine:latest
  before_script:
    - apk add --no-cache openssh-client curl
  script:
    - |
      echo "Deploying $IMAGE_TAG to staging..."
      # 通过 SSH 远程执行 Docker 部署
      ssh -o StrictHostKeyChecking=no deploy@staging.internal "
        docker pull $IMAGE_TAG &&
        docker stop express-api || true &&
        docker rm express-api || true &&
        docker run -d --name express-api -p 3000:3000 $IMAGE_TAG
      "
      # 等待服务健康检查
      sleep 10
      curl -f --retry 5 --retry-delay 3 http://staging.internal:3000/health
  environment:
    name: staging
    url: http://staging.internal:3000
  rules:
    - if: '$CI_COMMIT_BRANCH == "main"'
  needs:
    - docker-build

deploy-production:
  stage: deploy-staging
  image: alpine:latest
  script:
    - echo "Deploying $IMAGE_TAG to production..."
    - ssh deploy@prod.internal "docker pull $IMAGE_TAG && docker service update --image $IMAGE_TAG express-api"
  environment:
    name: production
    url: https://api.acme.com
  rules:
    - if: '$CI_COMMIT_TAG'
  when: manual    # 生产部署需手动点击
  needs:
    - deploy-staging

# ===== 第5层：通知 =====
notify-deploy:
  stage: notify
  image: alpine:latest
  variables:
    GIT_STRATEGY: none
  script:
    - |
      curl -X POST "$SLACK_WEBHOOK" \
        -H "Content-Type: application/json" \
        -d @- << EOF
      {
        "text": "✅ ${CI_PROJECT_NAME} deployed to ${CI_ENVIRONMENT_NAME}\nVersion: ${CI_COMMIT_SHORT_SHA}\nPipeline: ${CI_PIPELINE_URL}"
      }
      EOF
  needs:
    - job: deploy-staging
    - job: deploy-production
      optional: true
  when: on_success
```

#### 步骤2：配置 DORA 指标监控

**目标**：通过 GitLab CI Analytics 和 Prometheus 采集 DORA 四大指标。

```bash
# 1. 部署频率
# GitLab CI/CD → Analytics → CI/CD Analytics
# 查看过去 30 天的 Pipeline 成功次数 = 部署频率

# 或通过 API 查询：
curl --header "PRIVATE-TOKEN: $GITLAB_TOKEN" \
  "$GITLAB_URL/api/v4/projects/$PROJECT_ID/pipelines?status=success&updated_after=$(date -d '30 days ago' +%Y-%m-%d)" \
  | python3 -c "import json,sys; print(f'30天部署次数: {len(json.load(sys.stdin))}')"

# 2. 变更前置时间（Lead Time）
# = MR 从创建到合并的时间
# GitLab Analytics → Merge Request Analytics → 查看平均合并时间

# 3. 变更失败率
# = (失败的 Pipeline / 总 Pipeline) × 100%
# CI/CD Analytics → Pipeline 成功率

# 4. 故障恢复时间（MTTR）
# = 从告警触发到 Pipeline 恢复 Green 的时间
# Prometheus 查询：
# gitlab_failed_pipelines_total - offset 告警时间
```

**Grafana DORA Dashboard 配置**：

```json
{
  "dashboard": {
    "title": "GitLab DORA Metrics",
    "panels": [
      {
        "title": "部署频率（过去30天）",
        "targets": [
          {
            "expr": "sum(increase(gitlab_pipelines_total{status=\"success\",ref=\"main\"}[30d]))"
          }
        ]
      },
      {
        "title": "变更失败率",
        "targets": [
          {
            "expr": "sum(rate(gitlab_pipelines_total{status=\"failed\"}[30d])) / sum(rate(gitlab_pipelines_total[30d])) * 100"
          }
        ]
      }
    ]
  }
}
```

#### 步骤3：全链路故障演练

**目标**：人为制造故障，验证告警规则和恢复流程。

```bash
# 演练脚本 —— 不依赖手动操作
#!/bin/bash
echo "=== Chaos Engineering 演练开始 ==="

# 故障1：模拟 Sidekiq 队列积压
echo "1. 停止 Sidekiq（模拟积压）..."
sudo gitlab-ctl stop sidekiq
sleep 30

echo "2. 检查 Prometheus 是否产生告警..."
ALERT=$(curl -s http://prometheus:9090/api/v1/alerts | \
  python3 -c "import json,sys; alerts=json.load(sys.stdin); print(any(a['labels'].get('alertname')=='GitLabSidekiqQueueBacklog' and a['state']=='firing' for a in alerts['data']['alerts']))")
if [ "$ALERT" = "True" ]; then
  echo "✅ 告警已触发"
else
  echo "❌ 告警未触发，检查告警规则配置"
fi

echo "3. 恢复 Sidekiq..."
sudo gitlab-ctl start sidekiq
echo "=== 演练结束 ==="
```

### 完整代码清单

- `.gitlab-ci.yml`：五层完整流水线（步骤1）
- `chaos-exercise.sh`：故障演练脚本（步骤3）
- Grafana DORA Dashboard JSON（步骤2）

### 测试验证

```bash
# 验证1：全链路自动化
# 在项目 main 分支做一次代码修改并 push
git checkout -b test/full-pipeline && echo "// test" >> src/index.js
git add . && git commit -m "test: verify full pipeline" && git push origin test/full-pipeline
# 在 GitLab 创建 MR → 观察：
# ✅ lint 自动运行 + test 自动运行 + SAST 自动运行
# ✅ 合并后自动触发 docker-build → deploy-staging → Slack 通知

# 验证2：DORA 指标可获取
curl -s --header "PRIVATE-TOKEN: $GITLAB_TOKEN" \
  "$GITLAB_URL/api/v4/projects/$PROJECT_ID/merge_requests?state=merged&per_page=100" | \
  python3 -c "
import json,sys
mrs=json.load(sys.stdin)
times=[(m['merged_at'],m['created_at']) for m in mrs]
print(f'Merged MRs: {len(mrs)}')
"

# 验证3：告警规则触发演练
bash chaos-exercise.sh
# 检查 Slack 是否收到告警通知
```

## 4. 项目总结

### 优点 & 缺点

| 维度 | 优点 | 缺点 |
|------|------|------|
| 流程完整性 | 从代码到部署全自动化，减少人工环节 | 前期配置投入大（5 层流水线需要约 2 天搭建） |
| 可观测性 | DORA 指标量化 DevOps 效率 | 需要额外维护 Grafana Dashboard |
| 安全性 | SAST + Secret Detection 集成到 MR 流程 | SAST 误报可能阻碍开发（需要定期 dismiss） |
| 运维 | 故障演练验证了告警有效性 | Chaos Engineering 需要专门的演练窗口 |
| 团队协作 | 飞书自动通知减少沟通成本 | 通知过多可能变成"狼来了" |

### 适用场景

- **100-500 人团队**：多项目 DevOps 平台标准化
- **微服务架构**：跨仓库编排需求
- **有合规要求的行业**（金融、医疗）：安全扫描 + 审计日志

**不适用场景**：
- 5 人以下微型团队（维护成本 > 收益）
- 项目迭代极慢（月更一次），全自动化 ROI 不高

### 注意事项

- **DORA 指标不是越高越好**：部署频率从『周更』提升到『日更』需要组织文化配合，不是技术问题
- **Chaos Engineering 要控制爆炸半径**：在 Staging 环境演练，不要在生产环境搞
- **飞书/Slack 通知控制**：只通知关键事件（部署成功/失败），不要每个 lint 通过都发一条

### 常见踩坑经验

1. **全链路 Pipeline 经常在 SAST 阶段失败**：分析器版本与代码语言版本不兼容。根因：SAST 镜像没有及时更新。解决：定期更新 SAST 模板引用，或锁定分析器版本。
2. **DORA 指标数据不准**：因为有些项目用 CI 做定时任务（非部署）。根因：没有区分『部署 Pipeline』和『CI Pipeline』。解决：在 deploy job 中加入特定标签（如 `CI_ENVIRONMENT_NAME`），查询时按标签过滤。
3. **告警风暴**：一个底层组件故障导致上百条告警同时涌出。根因：没有配置告警分组抑制。解决：AlertManager 中配置 `group_by: ['alertname'] + group_wait: 30s`，相同类型告警合并发送。

### 思考题

1. 五层 DevOps 流水线中，哪一层最适合做"质量门禁"（质量不达标直接阻断后续流程）？为什么不是安全扫描层？
2. DORA 指标中，"变更失败率"低于 5% 被认为是高性能团队。但如果你的团队刚刚开始做自动化部署，失败率可能高达 30%。你应该如何平衡『持续优化』和『不打击团队信心』？

> 答案见附录 D。

### 推广计划提示

- **开发**：全链路流水线落地后，开发只需关注 MR Review，其余全自动——开发体验质的飞跃
- **运维**：本章的告警演练和 DORA 指标应该纳入运维团队的月报
- **测试**：测试团队应介入 SAST + Dependency Scanning 的结果审核（标记误报/确认漏洞）
- **管理**：DORA 指标是向管理层展示 DevOps 投资回报率的最佳数据
