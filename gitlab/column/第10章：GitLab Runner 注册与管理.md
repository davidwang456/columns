# 第10章：GitLab Runner 注册与管理

## 1. 项目背景

> **业务场景**：团队配置好了 `.gitlab-ci.yml`，Pipeline 却一直显示 "pending"——因为没有可用的 Runner。运维工程师小王匆忙装了个 Shared Runner，结果所有项目的 CI Job 都挤在这一个 Runner 上排队，开发高峰期一个 build 要等 40 分钟。更糟的是，小王用的是 Shell Executor，某个开发在 CI 脚本里写了 `npm install -g`，导致 Runner 机器上全局安装了不该装的东西……

CTO 紧急叫停："我们必须搞清楚 Runner 的分类、Executors 的区别、并发和标签调度机制，不能瞎装一通。"

经过调研，团队发现 Runner 的选型和配置是整个 CI/CD 系统的效率基石——选错了 Executor 会导致安全问题（Shell Executor 无隔离），选错了调度策略会导致资源浪费（所有 job 都打到一个 Runner 上），忽略了标签系统会让 CI 逻辑完全不可控。

**痛点放大**：GitLab Runner 不是"装上去就能用"的东西。Runner 类型（Shared/Group/Project）、Executor 类型（Shell/Docker/Kubernetes）、并发控制（concurrent/limit）、标签调度——每个选择都在深刻影响 CI 的效率、安全和维护成本。不理解这些概念就盲目部署，后续的排错和优化会让人抓狂。

## 2. 项目设计——剧本式交锋对话

**场景**：运维工位，小王面对 GitLab Runner 的官方文档，密密麻麻的 Executor 类型让他眼花缭乱。

---

**小胖**："Runner 不就是个执行脚本的工具吗？装一个不就行了，为什么要区分 Shared、Group、Project 三种？"

**大师**："这是权限和资源隔离的问题。Shared Runner 是所有项目共用的——就像公司的公共会议室，谁都可以预订，但高峰期要排队。Group Runner 是某个部门专用的——就像部门内部的小会议室，只有本部门的人能用。Project Runner 是某个项目独占的——就像项目组的独立办公室，你们想怎么用都行。"

**小白**："那 Executor 呢？Shell、Docker、Kubernetes 怎么选？我看小王装了 Shell Executor 之后，CI 脚本里 `npm cache clean --force` 把 Runner 机器上手动装的全局包都清了。"

**大师**："这就是 Shell Executor 的危险之处——它直接在 Runner 宿主机上执行命令，没有任何隔离。Docker Executor 为每个 job 启动一个独立容器，job 完成后容器销毁，不会污染宿主机。Kubernetes Executor 更进一步，为每个 job 创建一个 Pod，资源隔离和弹性扩缩容能力最强。技术映射：Shell Executor 就像在公共厨房做菜，锅碗瓢盆共享，谁搞砸了影响所有人；Docker Executor 就像每人一个独立的料理包，用完即弃；Kubernetes Executor 就像每人一个独立的厨房，还能根据需要自动增减厨房数量。"

**小胖**："那肯定选 Kubernetes 啊！听起来最高级。"

**大师**："不一定。选择取决于团队规模和基础设施。如果你只有一台机器，装 K8s 来跑 Runner 就是杀鸡用牛刀。Shell Executor 适合简单的 lint 和脚本任务，Docker Executor 是大多数团队的甜点位置，Kubernetes Executor 适合已经容器化、需要弹性扩缩容的大团队。"

**小白**："Tags 标签又是干嘛的？我看 Runner 注册时有 tags 字段。"

**大师**："Tags 是 Runner 调度系统的核心。你可以在 job 中用 `tags` 指定需要的 Runner 类型，比如 `docker`、`linux`、`gpu`。如果没有匹配 tag 的 Runner，job 就永远 pending。技术映射：Tags 就像外卖平台的筛选条件——你点餐时选'中餐'、'30分钟内送达'，只有同时满足这两个标签的餐厅才会接单。"

**小胖**："那 concurrent 和 limit 参数呢？"

**大师**："concurrent 控制一个 Runner 实例最多同时执行几个 job。limit 控制这个 Runner 总共最多接受几个 job（包括排队中的）。如果 concurrent=2, limit=5，这个 Runner 最多同时跑 2 个 job，最多排队 3 个。超过 limit 的 job 不会被这个 Runner 接走，由其他 Runner 处理。"

---

## 3. 项目实战

### 环境准备

> **目标**：安装并注册 3 种不同类型的 Runner——Shared Docker Runner、Group Shell Runner、Project-specific Runner——并配置标签调度。

**前置条件**：已部署 GitLab CE 17.x，有管理员权限。

### 分步实现

#### 步骤1：安装 GitLab Runner

**目标**：在 Linux 服务器或 macOS 上安装 GitLab Runner。

```bash
# ===== 方法A：Linux (Ubuntu/Debian) 原生安装 =====
# 添加官方仓库
curl -L "https://packages.gitlab.com/install/repositories/runner/gitlab-runner/script.deb.sh" | sudo bash

# 安装
sudo apt-get install gitlab-runner

# 验证安装
gitlab-runner --version
# 输出：Version: 17.0.0

# ===== 方法B：Docker 方式运行 Runner =====
docker run -d --name gitlab-runner \
  --restart always \
  -v /srv/gitlab-runner/config:/etc/gitlab-runner \
  -v /var/run/docker.sock:/var/run/docker.sock \
  gitlab/gitlab-runner:alpine-v17.0.0

# 注册 Runner（进入容器操作）
docker exec -it gitlab-runner gitlab-runner register

# ===== 方法C：macOS =====
brew install gitlab-runner
gitlab-runner install  # 安装为系统服务
gitlab-runner start
```

#### 步骤2：注册不同类型的 Runner

**目标**：注册 Shared Docker Runner、Group Shell Runner、Project-specific Runner。

**获取 Registration Token**：

```
Shared Runner Token（管理员）：
  Admin Area → CI/CD → Runners → "Register an instance runner"
  → 复制 registration token

Group Runner Token：
  Group → Settings → CI/CD → Runners → "Register a group runner"
  → 复制 registration token

Project Runner Token：
  Project → Settings → CI/CD → Runners → "Register a project runner"
  → 复制 registration token
```

**注册 Shared Docker Runner**：

```bash
sudo gitlab-runner register \
  --non-interactive \
  --url "http://gitlab.local:8929" \
  --token "<shared-runner-token>" \
  --executor "docker" \
  --docker-image "docker:24-dind" \
  --docker-privileged \
  --docker-volumes "/var/run/docker.sock:/var/run/docker.sock" \
  --docker-volumes "/cache" \
  --description "shared-docker-runner-01" \
  --tag-list "docker,linux,shared" \
  --run-untagged="true" \
  --locked="false" \
  --maximum-timeout="3600"

# 参数说明：
# --executor docker: 使用 Docker Executor
# --docker-image: job 的默认镜像
# --docker-privileged: 允许 job 内使用 Docker（DinD）
# --tag-list: Runner 标签，job 中用 tags 匹配
# --run-untagged: 是否接受没有 tags 的 job
# --locked: 是否锁定为当前项目/Group 专用
# --maximum-timeout: job 最大超时时间（秒）
```

**注册 Group Shell Runner**（仅用于简单的 lint 任务）：

```bash
sudo gitlab-runner register \
  --non-interactive \
  --url "http://gitlab.local:8929" \
  --token "<group-runner-token>" \
  --executor "shell" \
  --description "group-shell-runner-lint" \
  --tag-list "shell,lint,linux" \
  --run-untagged="false" \
  --locked="true"

# Shell Executor 注意：
# - 脚本直接在宿主机执行，无环境隔离
# - 宿主机需要预装 job 所需的所有工具
# - 不适合执行不信任的代码
```

**注册 Project-specific Docker Runner**（为特殊项目定制）：

```bash
sudo gitlab-runner register \
  --non-interactive \
  --url "http://gitlab.local:8929" \
  --token "<project-runner-token>" \
  --executor "docker" \
  --docker-image "node:20-alpine" \
  --description "project-payment-runner" \
  --tag-list "payment,docker,high-memory" \
  --run-untagged="false" \
  --locked="true"

# Project Runner 特点：
# - 只有特定项目可用
# - 适合有特殊需求的项目（大内存、GPU 等）
```

#### 步骤3：配置 Runner 并发和高级参数

**目标**：优化 Runner 的并发控制、资源限制和缓存策略。

**编辑 Runner 配置文件**：

```bash
# Runner 配置文件路径
# Linux: /etc/gitlab-runner/config.toml
# macOS: ~/.gitlab-runner/config.toml

sudo vi /etc/gitlab-runner/config.toml
```

```toml
# config.toml - 完整的 Runner 配置示例
concurrent = 4           # 全局：最多同时运行 4 个 job
check_interval = 3       # 检查新 job 的间隔（秒）
log_level = "info"       # debug | info | warn | error
log_format = "json"      # runner | json

[session_server]
  session_timeout = 1800  # Interactive Terminal 超时时间

# ===== Shared Docker Runner =====
[[runners]]
  name = "shared-docker-runner-01"
  url = "http://gitlab.local:8929"
  id = 1
  token = "xxx"
  token_obtained_at = 2026-05-12T00:00:00Z
  token_expires_at = 0001-01-01T00:00:00Z
  executor = "docker"
  limit = 10             # 该 Runner 最多同时处理 10 个 job
  output_limit = 4096    # job 日志最大 KB
  request_concurrency = 1  # 每次请求获取的 job 数

  [runners.docker]
    tls_verify = false
    image = "docker:24-dind"
    privileged = true
    disable_entrypoint_overwrite = false
    oom_kill_disable = false
    disable_cache = false
    volumes = [
      "/var/run/docker.sock:/var/run/docker.sock",
      "/cache:/cache"
    ]
    # 资源限制
    memory = "2g"
    memory_swap = "4g"
    cpus = "2"
    # 网络模式
    network_mode = "bridge"
    # 拉取策略
    pull_policy = ["if-not-present", "always"]
    shm_size = 256MB          # 共享内存大小（/dev/shm）
    # 额外的宿主机映射
    extra_hosts = ["gitlab.local:192.168.1.100"]
    # 容器环境变量
    environment = [
      "DOCKER_TLS_CERTDIR=",
      "NPM_CONFIG_CACHE=/cache/.npm"
    ]

  [runners.cache]
    Type = "s3"               # 或 "gcs" / "azure"
    Shared = true
    [runners.cache.s3]
      ServerAddress = "s3.amazonaws.com"
      AccessKey = "AKIAXXXXX"
      SecretKey = "xxxxx"
      BucketName = "gitlab-runner-cache"
      Insecure = false

# ===== Group Shell Runner =====
[[runners]]
  name = "group-shell-runner-lint"
  url = "http://gitlab.local:8929"
  id = 2
  token = "yyy"
  executor = "shell"
  limit = 2

  [runners.shell]
    # Shell Executor 特定配置（较少）

# ===== Project-specific Runner =====
[[runners]]
  name = "project-payment-runner"
  url = "http://gitlab.local:8929"
  id = 3
  token = "zzz"
  executor = "docker"
  limit = 3

  [runners.docker]
    image = "node:20-alpine"
    privileged = false       # 无需构建 Docker 镜像时不开启
    memory = "4g"
    cpus = "4"
```

**重启 Runner 使配置生效**：

```bash
# Linux
sudo gitlab-runner restart

# macOS
gitlab-runner restart

# Docker
docker restart gitlab-runner

# 验证配置
sudo gitlab-runner list
# 应列出所有注册的 Runner
```

#### 步骤4：验证 Runner 调度与标签匹配

**目标**：观察不同标签的 job 如何被调度到对应的 Runner。

```bash
# 创建测试 .gitlab-ci.yml，使用不同标签
cat > .gitlab-ci.yml << 'EOF'
stages:
  - build
  - lint
  - test

# 这个 job 会被 "docker" 标签的 Runner 执行
docker-build:
  stage: build
  image: node:20-alpine
  tags:
    - docker
  script:
    - echo "Running on Docker Runner"

# 这个 job 会被 "shell" 标签的 Runner 执行
lint-job:
  stage: lint
  tags:
    - shell
    - lint
  script:
    - echo "Running on Shell Runner"
    - echo "Machine: $(hostname)"
    - echo "User: $(whoami)"

# 这个 job 会被 "payment" 标签的 Runner 执行
payment-test:
  stage: test
  tags:
    - payment
    - docker
  script:
    - echo "Running on Payment Runner"
    - echo "Memory: $(free -h | grep Mem)"

# 这个 job 没有 tags，默认由 run_untagged 的 Runner 执行
untagged-job:
  stage: test
  script:
    - echo "I have no tags, running on any untagged Runner"
EOF

git add .gitlab-ci.yml && git commit -m "ci: test runner tag scheduling" && git push

# 在 GitLab UI 观察 Pipeline：
# 1. 查看每个 job 的 Runner 信息（job 日志顶部）
# 2. 确认 job 被分配到了正确标签的 Runner
# 3. Settings → CI/CD → Runners 查看 Runner 当前状态
```

### 完整代码清单

- `config.toml`：Runner 完整配置（步骤3）
- Runner 注册命令（步骤2）
- 标签调度测试 `.gitlab-ci.yml`（步骤4）

### 测试验证

```bash
# 验证1：查看所有 Runner 状态（管理员 API）
curl --header "PRIVATE-TOKEN: $GITLAB_TOKEN" \
  "$GITLAB_URL/api/v4/runners/all" | \
  python3 -c "
import json, sys
runners = json.load(sys.stdin)
for r in runners:
    status = 'online' if r.get('status') == 'online' else 'offline'
    print(f'Runner #{r[\"id\"]}: {r[\"description\"]} [{status}]')
    print(f'  Tags: {r[\"tag_list\"]}')
    print(f'  Executor: {r.get(\"executor\", \"unknown\")}')
"

# 验证2：检查 Runner 是否在线并能接收 job
curl --header "PRIVATE-TOKEN: $GITLAB_TOKEN" \
  "$GITLAB_URL/api/v4/runners?status=online" | \
  python3 -c "
import json, sys
runners = json.load(sys.stdin)
print(f'Online runners: {len(runners)}')
"

# 验证3：查看 job 的 Runner 分配
curl --header "PRIVATE-TOKEN: $GITLAB_TOKEN" \
  "$GITLAB_URL/api/v4/projects/$PROJECT_ID/jobs?per_page=5" | \
  python3 -c "
import json, sys
jobs = json.load(sys.stdin)
for j in jobs:
    runner = j.get('runner', {})
    print(f'Job {j[\"name\"]}: Runner={runner.get(\"description\", \"none\")}, Status={j[\"status\"]}')
"
```

## 4. 项目总结

### 优点 & 缺点

| Executor | 优点 | 缺点 | 适用场景 |
|----------|------|------|---------|
| Shell | 最简单，无额外依赖，速度快 | 无隔离，环境污染，安全风险 | 简单的 lint/script |
| Docker | 环境隔离，镜像版本可控 | 需要宿主机 Docker，不能运行非 Linux job | 主流选择，90% 场景 |
| Kubernetes | 弹性扩缩容，资源利用率高 | 需要 K8s 集群，配置复杂 | 大规模/容器化团队 |
| Docker Machine | 自动创建/销毁云主机 | 已弃用（GitLab 17.x 移除） | 不推荐新项目使用 |
| Custom | 完全自定义执行环境 | 需要自己实现 | 特殊需求 |

### 适用场景

- **Shared Docker Runner**：大多数团队的标准选择——为所有项目提供统一的构建环境
- **Group Shell Runner**：运维团队的脚本执行、简单的批量操作
- **Project-specific Runner**：有特殊资源需求的单个项目（大内存、GPU）
- **Kubernetes Runner**：已有 K8s 集群，需要弹性扩缩容和精细化资源管理

**不适用场景**：
- 从零开始的小团队（先装 Docker Runner 就够了）
- 没有容器化经验（Shell Executor + 严格脚本规范可以考虑）

### 注意事项

- **不要在生产 Runner 上使用 Shell Executor 执行不可信代码**——它可以访问宿主机文件系统
- **Runner 的 concurrent 和 limit 需要配合 Job 资源消耗设置**：如果一个 job 吃 2GB 内存，concurrent=4 就需要 8GB+ 内存
- **定期清理 Docker Runner 的镜像缓存**：`docker system prune -f` 可防止磁盘爆满
- **Runner token 要定期轮换**：尤其在有人离职后

### 常见踩坑经验

1. **Job 一直 pending，日志没有错误**：Runner 不在线或没有匹配的 tag。根因：`gitlab-runner list` 显示 Runner 在线，但 GitLab UI 显示 offline。解决：检查 Runner 时间是否与 GitLab 服务器同步（NTP），时间偏差过大会导致心跳失败。
2. **Docker Executor 中 `docker build` 失败**：容器内无法连接 Docker daemon。根因：没有挂载 `/var/run/docker.sock` 或没有开启 privileged 模式。解决：确保 config.toml 中配置了 `volumes = ["/var/run/docker.sock:/var/run/docker.sock"]` 且 `privileged = true`。
3. **Cache 上传超时**：S3 缓存的 Access Key 或 Bucket 权限问题。根因：Runner 配置的 S3 凭证没有写权限。解决：检查 S3 bucket policy，或先使用 `Type = "local"` 本地缓存。

### 思考题

1. 如果你的团队有前端（React/Vue）和后端（Go/Java）两类项目，前端 CI 需要 Chrome Headless 做测试，后端需要 Maven/Gradle 构建。你应该如何设计 Runner 的标签策略以实现资源隔离？
2. Docker Executor 有三种 DinD 实现方式：`docker:dind` 服务、挂载 socket、使用 `kaniko`。它们各自的安全性和适用场景是什么？

> 答案见附录 D。

### 推广计划提示

- **运维**：Runner 的选型和配置是 CI/CD 基础设施的核心，需重点理解 Executor 差异和调度策略
- **开发**：理解 tag 调度机制后，可以在 job 中精确控制运行环境，避免 "在我的机器上能跑" 问题
- **测试**：可以通过标签为测试 job 单独配置高性能 Runner（大内存/GitHub Actions 等效资源）
