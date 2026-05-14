# 第11章：Pipeline 流水线可视化与调试

## 1. 项目背景

> **业务场景**：一家公司的 CI/CD 流水线日益复杂——一个微服务项目有 15 个 job，分布在 5 个 stage 中，有些 job 通过 `needs` 实现了 DAG 并行执行。当 Pipeline 失败时，团队花在"定位问题"上的时间比"修复问题"本身还多——因为 Pipeline 没有可视化工具，只能一个个翻 job 日志。

更让人头疼的是，有一次 deploy job 失败了，日志只显示 "Exit code 1"——没有任何上下文。运维和开发一起排查了 2 个小时，最后发现是 deploy 脚本里一条 `curl` 命令访问的 API 端点改了域名，但 curl 的 `--fail` 参数只返回了退出码，没有打印响应内容。如果当时的 job 日志有详细的错误输出，这个问题 2 分钟就能定位。

还有一次，"诡异"的问题：Pipeline 在本地 `gitlab-ci-local` 上能通过，但推到 GitLab 上就失败。排查了半天发现是 Runner 的镜像版本比本地新，某个 npm 包在 Alpine 下缺少编译依赖。如果当时启用了 `CI_DEBUG_TRACE` 或者使用了 Interactive Web Terminal，就能直接在 Runner 环境中调试。

**痛点放大**：流水线调试的本质是"远程环境下的故障定位"——你无法 SSH 到 Runner 上，日志是你唯一的线索。GitLab 提供了一系列调试工具：流水线可视化（DAG 图）、CI_DEBUG_TRACE 模式、Interactive Web Terminal、Job Artifacts 中的中间产物——但大多数团队只用到了其中的 10%。

## 2. 项目设计——剧本式交锋对话

**场景**：凌晨 2 点，开发小刘盯着一个红色的 Pipeline 已经半小时了。

---

**小胖**："又红了？Pipeline 失败真的很难排查，日志一大堆不知道从哪看起。"

**小刘**："是啊，这 Pipeline 有 15 个 job，log 加起来几千行。而且有些 job 是并行的，我不知道哪个才是根因。"

**大师**："排查 Pipeline 问题的第一步不是看日志，而是看流水线图。GitLab 的 Pipeline 视图有两种——传统视图和 DAG 视图。传统视图按 stage 展示每个 job 的状态（绿/红/灰/蓝），DAG 视图展示 job 之间的依赖关系（`needs` 定义的有向无环图）。先找到第一个失败的 job，然后看它的日志。"

**小白**："我注意到日志里有些 job 的输出被截断了——超过 4MB 的日志会被裁剪。有没有办法看完整日志？"

**大师**："有两个办法：一是在 job 页面右上角点下载按钮，下载完整日志文件；二是在 script 中把关键输出重定向到文件，作为 artifacts 保存。技术映射——job 日志就像飞机的黑匣子，通常是有限的；如果你想保存'全程录音'，需要自己配置 artifacts。"

**小胖**："那 CI_DEBUG_TRACE 是什么？我看文档里提过。"

**大师**："CI_DEBUG_TRACE 是一个特殊的 CI 变量——当你把它设为 `true` 时，GitLab Runner 会输出详细的调试信息，包括每个命令执行前的展开结果、环境变量的实际值。这对排查'为什么这个变量没生效'特别有用。但注意——它会把所有 CI 变量（包括 Masked 的）的值打印出来，所以不要在公开的 job 日志中启用。"

**小白**："Interactive Web Terminal 呢？听起来很酷。"

**大师**："当 job 正在运行时（包括 manual job 等待触发时），你可以在 job 页面打开一个 Web Terminal，直接 SSH 到 Runner 的执行环境中。你可以在里面手动执行命令、检查环境、查看中间文件——就像在本机调试一样。唯一的限制是只能连接运行中的 job。技术映射：Interactive Web Terminal 就像你可以在飞机飞行途中进入驾驶舱看一眼仪表盘。"

---

## 3. 项目实战

### 环境准备

> **目标**：排查一个复杂的多 stage Pipeline 失败问题，演练从发现到定位到修复的完整调试流程。

**前置条件**：一个有 3+ 个 stage 的项目 Pipeline，至少 1 个 Docker Runner。

### 分步实现

#### 步骤1：理解 Pipeline 可视化视图

**目标**：学习如何通过 Pipeline 图和 DAG 图快速定位失败根因。

**设计一个故意会失败的复杂 Pipeline**：

```yaml
# .gitlab-ci.yml - 包含 DAG 依赖的复杂流水线
stages:
  - prepare
  - build
  - test
  - security
  - deploy

# ===== Stage 1: Prepare =====
install-deps:
  stage: prepare
  image: node:20-alpine
  script:
    - npm ci
  artifacts:
    paths:
      - node_modules/
    expire_in: 1 hour

# ===== Stage 2: Build =====
build-backend:
  stage: build
  image: node:20-alpine
  script:
    - npm run build:backend
  needs: [install-deps]
  artifacts:
    paths:
      - dist/backend/

build-frontend:
  stage: build
  image: node:20-alpine
  script:
    - npm run build:frontend
  needs: [install-deps]
  artifacts:
    paths:
      - dist/frontend/

# ===== Stage 3: Test（DAG 并行）=====
unit-test-backend:
  stage: test
  image: node:20-alpine
  script:
    - npm run test:unit:backend
  needs: [build-backend]

unit-test-frontend:
  stage: test
  image: node:20-alpine
  script:
    - npm run test:unit:frontend
  needs: [build-frontend]

integration-test:
  stage: test
  image: node:20-alpine
  script:
    - npm run test:integration
  needs: [build-backend, build-frontend]  # 依赖两个 build

# ===== Stage 4: Security =====
sast-scan:
  stage: security
  image: node:20-alpine
  script:
    - echo "Run SAST scan..."
    - npm audit --audit-level=high
  needs: [build-backend]

# ===== Stage 5: Deploy（故意失败）=====
deploy-staging:
  stage: deploy
  image: alpine:latest
  script:
    - echo "Deploying to staging..."
    - NON_EXISTENT_COMMAND  # 故意让命令失败
  needs: [unit-test-backend, unit-test-frontend, integration-test]
  environment:
    name: staging
```

**分析 Pipeline 故障**：

```bash
# 推送到 GitLab 后，Pipeline 会依次执行
# 通过 GitLab UI 的 CI/CD → Pipelines 页面观察：

# 1. Pipeline 概览页：看到 5 个 stage 的执行状态
# 2. 点击 Pipeline ID 进入详情：
#    - 传统视图：每个 stage 的 job 列表（按执行时间排序）
#    - DAG 视图：（点击 "Show dependencies"）直观展示依赖关系
# 3. deploy-staging 显示红色（失败），点击进入查看日志
# 4. 日志最后一行："/bin/sh: NON_EXISTENT_COMMAND: not found"

# 修复方法：删除或修正错误命令
# 然后重新 push，观察之前的 job 是否会重新执行
# 注意：有 needs 依赖的 job，上游重新执行后也会重新执行
```

#### 步骤2：使用 CI_DEBUG_TRACE 排查变量问题

**目标**：启用调试模式，追踪 CI 变量的实际传递过程。

```yaml
# .gitlab-ci.yml
debug-variable-job:
  stage: test
  image: alpine:latest
  variables:
    CI_DEBUG_TRACE: "true"  # 启用调试模式（仅此 job）
    MY_SECRET: "should-be-private"
    DB_HOST: "prod.db.internal"
  script:
    - echo "Normal output..."
    - echo "DB_HOST is $DB_HOST"
```

**观察 CI_DEBUG_TRACE 输出**：

```bash
# 推送后查看 job 日志，会看到额外的调试信息：
# ...
# $ echo "DB_HOST is $DB_HOST"
#  # 实际执行的命令（变量已展开）
# echo "DB_HOST is prod.db.internal"
# DB_HOST is prod.db.internal
# ...

# 注意：CI_DEBUG_TRACE 会输出所有变量，包括 Masked 变量的实际值
# 生产环境务必在排查完成后关闭
```

#### 步骤3：使用 Interactive Web Terminal 实时调试

**目标**：在 job 运行期间通过 Web Terminal 进入容器环境调试。

**创建支持 Web Terminal 的 job**：

```yaml
# .gitlab-ci.yml
debug-interactive:
  stage: test
  image: node:20-alpine
  script:
    - echo "Job started, sleep for 300 seconds..."
    - sleep 300  # 给足够时间打开 Web Terminal
  when: manual
```

**使用步骤**：

```
1. 触发 Pipeline → 当 debug-interactive job 显示 "manual" 时
2. 点击 job → 点击右上角的 "Debug" 按钮（▶️ 图标）
3. 如果配置正确，会弹出 Web Terminal 窗口
4. 在 Terminal 中执行命令：
   $ env | grep CI_           # 查看所有 CI 环境变量
   $ ls -la                   # 查看工作目录文件
   $ node -v                  # 查看 Node 版本
   $ npm list --depth=0       # 查看已安装的包
   $ cat /etc/hosts           # 查看 DNS 配置
   $ curl -v http://internal-api/health  # 测试网络连通性

5. 调试完成后，关闭 Terminal，手动 cancel 或 retry job
```

**注意**：
- Interactive Web Terminal 需要 GitLab 13.3+
- Runner 需要配置 `[session_server]`
- 只在 job 运行期间可用

#### 步骤4：通过 Artifacts 保存中间产物辅助排查

**目标**：在 job 失败时自动保存关键调试信息。

```yaml
# .gitlab-ci.yml
debug-with-artifacts:
  stage: test
  image: node:20-alpine
  script:
    # 收集调试信息
    - env | sort > debug-env.txt
    - npm list --depth=0 > debug-deps.txt
    - df -h > debug-disk.txt
    - free -m > debug-memory.txt
    # 实际的测试脚本
    - npm run test || true  # 即使失败也继续
  after_script:
    - echo "Job finished with status: ${CI_JOB_STATUS}"
    # 检查上一个命令的退出码
    - |
      if [ "${CI_JOB_STATUS}" = "failed" ]; then
        echo "=== DEBUG INFO ==="
        cat debug-env.txt
        cat debug-disk.txt
      fi
  artifacts:
    when: always            # 无论成功失败都保存
    paths:
      - debug-env.txt
      - debug-deps.txt
      - debug-disk.txt
      - debug-memory.txt
    expire_in: 7 days
```

**在 GitLab UI 中查看 Artifacts**：
```
1. 进入失败的 job 页面
2. 右侧 "Job artifacts" 区域
3. 点击 "Download" 或 "Browse" 查看产物
4. 也可以在 MR 的 Pipeline 小部件中直接下载
```

#### 步骤5：构建 Pipeline 状态监控

**目标**：在 README.md 中嵌入 Pipeline 状态徽章，团队可见。

```bash
# 获取 Pipeline 状态徽章的 Markdown 代码
# Project → Settings → CI/CD → General pipelines → Pipeline status

# 添加到 README.md
cat >> README.md << 'EOF'

## Pipeline 状态

| 分支 | 状态 |
|------|------|
| main | [![pipeline status](http://gitlab.local:8929/acme-corp/ecommerce/shop-api/badges/main/pipeline.svg)](http://gitlab.local:8929/acme-corp/ecommerce/shop-api/-/pipelines) |
| develop | [![pipeline status](http://gitlab.local:8929/acme-corp/ecommerce/shop-api/badges/develop/pipeline.svg)](http://gitlab.local:8929/acme-corp/ecommerce/shop-api/-/pipelines) |

## 测试覆盖率

[![coverage report](http://gitlab.local:8929/acme-corp/ecommerce/shop-api/badges/main/coverage.svg)](http://gitlab.local:8929/acme-corp/ecommerce/shop-api/-/pipelines)
EOF
```

### 完整代码清单

- `.gitlab-ci.yml`：包含 DAG 依赖和多 stage 的 Pipeline
- `debug-*.txt`：Artifacts 调试信息文件
- `README.md`：Pipeline 状态徽章

### 测试验证

```bash
# 验证1：Pipeline DAG 可视化
# 在 GitLab UI 中打开 Pipeline 详情页 → Show dependencies
# 确认 job 之间的箭头指向正确

# 验证2：验证 Interactive Web Terminal
# 触发 sleep job → Debug 按钮 → 确认可以执行命令

# 验证3：验证 Artifacts 保存
curl --header "PRIVATE-TOKEN: $GITLAB_TOKEN" \
  "$GITLAB_URL/api/v4/projects/$PROJECT_ID/jobs/<job-id>/artifacts" | \
  python3 -c "import json,sys; [print(f['filename']) for f in json.load(sys.stdin)]"

# 验证4：Pipeline 徽章显示
curl -I http://gitlab.local:8929/acme-corp/ecommerce/shop-api/badges/main/pipeline.svg
# 应返回 200 和 SVG 内容类型
```

## 4. 项目总结

### 优点 & 缺点

| 调试方式 | 优点 | 缺点 |
|---------|------|------|
| Pipeline DAG 图 | 一眼看出失败根因，依赖关系清晰 | 复杂 DAG 可能过于密集 |
| CI_DEBUG_TRACE | 完整变量展开，便于追踪 | 会打印敏感变量值，有安全风险 |
| Interactive Terminal | 真正的交互式调试 | 仅 job 运行期间可用，需要 Runner 支持 |
| Artifacts 保存 | 事后可回溯分析 | 消耗存储空间 |
| 状态徽章 | 一目了然的可见性 | 外部依赖 GitLab 服务可达 |

### 适用场景

- **DAG 图**：复杂微服务 Pipeline（10+ job）
- **CI_DEBUG_TRACE**：变量配置不生效、命令展开异常
- **Web Terminal**：网络连通性测试、环境工具验证
- **Artifacts**：长时间运行的测试、需要离线分析的场景

**不适用场景**：
- 已经通过的 Pipeline（不需要调试）
- 简单的 2-3 job Pipeline（直接用日志就够了）

### 注意事项

- **CI_DEBUG_TRACE 中的敏感信息**：排查后务必移除或设置为 `false`
- **Interactive Web Terminal 的 Session 超时**：默认 30 分钟，可通过 `[session_server] session_timeout` 调整
- **Artifacts 过期清理**：调试用的 artifacts 设置短过期时间（1-7 天），避免浪费存储

### 常见踩坑经验

1. **Pipeline 显示 "blocked" 状态**：某个 job 有 `needs` 依赖但上游 job 被跳过了。根因：上游 job 的 rules 条件不满足。解决：检查 rules，使用 `needs: [{job: xxx, optional: true}]` 允许跳过。
2. **Interactive Terminal 无法连接**：点击 Debug 按钮后一直 loading。根因：Runner 未配置 `[session_server]` 或网络不通。解决：在 config.toml 中添加 `[session_server]` 配置，重启 Runner。
3. **Pipeline 徽章显示 "unknown"**：分支名拼写错误或该分支从未跑过 Pipeline。根因：徽章 URL 中的分支名区分大小写。解决：确认分支名完全匹配。

### 思考题

1. 如果一个 job 因为 OOM 被 kill 了（exit code 137），但日志最后只显示 "Job failed"，你如何确认是 OOM 而不是其他错误？
2. 你的 Pipeline 有 20 个 job，其中 5 个是 `when: manual`。如何通过 GitLab API 批量触发所有 manual job？

> 答案见附录 D。

### 推广计划提示

- **开发**：学会用 DAG 图分析 Pipeline 结构，用 Web Terminal 快速验证环境问题
- **运维**：Artifacts 保存系统信息（env、disk、memory）是故障排查的宝贵数据源
- **测试**：失败 job 的 artifacts 中保存失败上下文，可以在后续做自动化根因分析
