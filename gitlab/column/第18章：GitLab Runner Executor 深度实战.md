# 第18章：GitLab Runner Executor 深度实战

## 1. 项目背景

> **业务场景**：某公司从"所有 job 用一个 Shell Runner"升级到多 Executor 混合使用后，遇到了各种奇怪问题。Docker Executor 构建 Java 项目时内存爆炸，Kubernetes Executor 的 Pod 随机被驱逐，Shell Executor 的环境不一致导致"在我机器上能跑"的问题反复出现。团队发现：选对 Executor 不够，还得配对参数。

最惨的一次：团队为了省事，把 Docker Executor 的 `pull_policy` 设为 `always`——每次 CI 都从 Docker Hub 重新拉取 `maven:3.9` 镜像（800MB）。结果 Docker Hub 限速后，每天的 CI 都卡在拉取镜像的阶段。切换到 `if-not-present` 后，速度提升 80%。

另一个问题：Kubernetes Executor 的 CI Pod 经常因为内存超出 limit 被 kill（exit code 137），但开发看不懂这个错误码，以为是 CI 脚本写错了，反复修改浪费时间。

**痛点放大**：Executor 不是选了就完事，每个 Executor 都有一组关键参数——Docker 的 `volumes`、`pull_policy`、`memory`；Kubernetes 的 `resource_requests`、`node_selector`、`service_account`；Shell 的 `pre_build_script`——这些参数直接影响 CI 的速度、稳定性、安全性和成本。

## 2. 项目设计——剧本式交锋对话

**场景**：CI 效率专题讨论会，白板上贴着三种 Executor 的对比表。

---

**小胖**："上次你说 Executor 有三种，那我们全部用 Docker 不就行了？Java 项目、Node 项目、Python 项目都能跑 Docker 镜像，完美兼容。"

**大师**："Docker Executor 确实是万能选择，但它也有局限性。第一个问题是 Docker-in-Docker（DinD）——当你的 CI job 需要构建 Docker 镜像时，你需要在一个 Docker 容器里再跑 Docker。这有两种做法：挂载宿主机的 `docker.sock` 或启动 `docker:dind` 服务。"

**小白**："这两种做法有什么区别？"

**大师**："挂载 socket 的方式最简单——容器内的 Docker 客户端通过 socket 直接操作宿主机的 Docker daemon。但这也是最不安全的——容器内的任何人只要拿到 socket 就能控制宿主机的所有容器。dind 服务方式更安全——每个 job 有自己的独立 Docker daemon，但需要 privileged 模式。技术映射——Socket 挂载就像把你的家门钥匙给每个访客，dind 就像给每个访客一个独立的临时房间，他们在房间里做什么不会影响到别人。"

**小胖**："那 Kubernetes Executor 是不是最安全的？每个 job 一个 Pod，完全隔离。"

**大师**："隔离性是最好，但复杂度也最高。Kubernetes Executor 的坑通常和资源管理有关——Pod 的 request 设大了浪费集群资源，设小了被 OOM kill。还要考虑 Pod 调度策略（node selector、亲和性）、镜像拉取凭证（imagePullSecrets）、Service Account 权限——这些都是 Docker Executor 不需要考虑的问题。"

**小白**："那 Shell Executor 还有存在的价值吗？"

**大师**："有限的场景下可以。比如你需要访问宿主机的特殊硬件（GPU、USB 设备），或者执行超轻量的脚本（lint、简单的 curl 测试）。但绝对不要在 Shell Executor 上执行不信任的代码——它和宿主机之间没有隔离层。"

---

## 3. 项目实战

### 环境准备

> **目标**：分别配置 Docker、Kubernetes、Shell 三种 Executor，覆盖各自的参数调优和常见问题。

**前置条件**：已安装 GitLab Runner，有 Docker 环境和 K8s 集群。

### 分步实现

#### 步骤1：Docker Executor 深度配置

**目标**：解决 DinD、layer cache、资源限制等核心问题。

```toml
# /etc/gitlab-runner/config.toml - Docker Executor 最佳配置
[[runners]]
  name = "docker-executor-optimized"
  url = "http://gitlab.local:8929"
  token = "xxx"
  executor = "docker"
  limit = 4                              # 最多 4 个并发 job
  output_limit = 8192                    # 日志限制 8MB

  [runners.docker]
    image = "alpine:latest"              # 默认镜像（job 可覆盖）
    privileged = false                    # 非必要不开启
    disable_entrypoint_overwrite = false

    # 镜像拉取策略（关键性能参数！）
    pull_policy = ["if-not-present"]      # 优先用本地缓存
    # 备选：["always"] 每次都拉（浪费）、["never"] 只用本地（可能过期）

    # Docker-in-Docker 配置
    volumes = [
      "/cache:/cache",            # 全局缓存目录
      "/var/run/docker.sock:/var/run/docker.sock:ro"  # 只读挂载（更安全）
    ]

    # 仅当需要构建 Docker 镜像时才启动 dind 服务
    # 在 job 中配置：
    # services:
    #   - docker:24-dind

    # 资源限制
    memory = "4g"
    memory_swap = "8g"
    cpus = "2"

    # 共享内存（Chromium/Puppeteer 测试需要）
    shm_size = 256000000  # 256MB

    # DNS 和主机映射
    dns = ["8.8.8.8", "1.1.1.1"]
    extra_hosts = ["gitlab.local:192.168.1.100"]

    # 容器环境变量
    environment = [
      "DOCKER_TLS_CERTDIR=",              # 禁用 TLS（内网环境）
      "BUILDKIT_PROGRESS=plain",
      "NPM_CONFIG_CACHE=/cache/.npm",
      "PIP_CACHE_DIR=/cache/.pip",
      "GRADLE_USER_HOME=/cache/.gradle"
    ]

    # 安全选项
    cap_add = []                          # 不添加额外 Linux capability
    cap_drop = ["ALL"]                    # 默认全去掉

    # 网络模式
    network_mode = "bridge"

    # OOM 处理
    oom_kill_disable = false

  [runners.cache]
    Type = "s3"                           # 或 "gcs"
    Shared = true
    Path = "cache"
    [runners.cache.s3]
      ServerAddress = "s3.internal.com"
      AccessKey = "cache-access-key"
      SecretKey = "cache-secret-key"
      BucketName = "gitlab-runner-cache"
      Insecure = false
```

#### 步骤2：Kubernetes Executor 配置

**目标**：在 K8s 集群中配置 Runner，实现弹性 CI 执行。

```bash
# 1. 安装 GitLab Runner 到 K8s（Helm 方式）
helm repo add gitlab https://charts.gitlab.io
helm upgrade --install gitlab-runner gitlab/gitlab-runner \
  --namespace gitlab-runner --create-namespace \
  --set gitlabUrl=http://gitlab.local:8929 \
  --set runnerRegistrationToken="<runner-token>" \
  --set rbac.create=true \
  --set runners.privileged=false \
  --set runners.tags="kubernetes" \
  --set runners.config="
    [[runners]]
      [runners.kubernetes]
        namespace = \"{{.Release.Namespace}}\"
        image = \"alpine:latest\"
        pull_policy = \"if-not-present\"
        cpu_request = \"500m\"
        cpu_limit = \"2\"
        memory_request = \"512Mi\"
        memory_limit = \"4Gi\"
        helper_cpu_request = \"100m\"
        helper_memory_request = \"64Mi\"
        service_cpu_request = \"100m\"
        service_memory_request = \"64Mi\"
        # 使用 node selector 调度到特定节点
        node_selector = { \"ci\" = \"true\" }
        # Pod 安全上下文
        [runners.kubernetes.pod_security_context]
          run_as_non_root = true
          run_as_user = 1000
          fs_group = 1000
  "
```

**Kubernetes Executor 的 .gitlab-ci.yml 用法**：

```yaml
# .gitlab-ci.yml - K8s Executor 专项配置
k8s-job:
  stage: test
  image: node:20-alpine
  tags:
    - kubernetes
  variables:
    KUBERNETES_MEMORY_REQUEST: "1Gi"
    KUBERNETES_MEMORY_LIMIT: "2Gi"
    KUBERNETES_CPU_REQUEST: "500m"
    KUBERNETES_CPU_LIMIT: "1"
  script:
    - node -v
    - echo "Running on K8s pod: $CI_RUNNER_TAGS"
```

#### 步骤3：DinD vs Socket vs Kaniko——Docker 构建的三种方案

**目标**：对比 Docker 构建的三种实现方式，选择最适合的方案。

```yaml
# .gitlab-ci.yml - 三种 Docker 构建方案对比

# ===== 方案A：Docker Socket 挂载（最简单，安全性最差）=====
docker-build-socket:
  stage: build
  image: docker:24
  script:
    - docker build -t my-app:$CI_COMMIT_SHORT_SHA .
    - docker push my-app:$CI_COMMIT_SHORT_SHA
  # 前提：Runner config.toml 中 volumes 包含 docker.sock

# ===== 方案B：Docker-in-Docker 服务（推荐，需要 privileged）=====
docker-build-dind:
  stage: build
  image: docker:24
  services:
    - docker:24-dind                # 独立的 Docker daemon
  variables:
    DOCKER_HOST: tcp://docker:2375  # 指向 dind 服务
    DOCKER_TLS_CERTDIR: ""          # 禁用 TLS（内网）
    DOCKER_DRIVER: overlay2
  before_script:
    - until docker info; do sleep 1; done  # 等待 dind 就绪
  script:
    - docker build -t my-app:$CI_COMMIT_SHORT_SHA .
    - docker push my-app:$CI_COMMIT_SHORT_SHA

# ===== 方案C：Kaniko（无需 privileged，最推荐）=====
docker-build-kaniko:
  stage: build
  image:
    name: gcr.io/kaniko-project/executor:v1.19.0-debug
    entrypoint: [""]
  script:
    - /kaniko/executor
        --context $CI_PROJECT_DIR
        --dockerfile $CI_PROJECT_DIR/Dockerfile
        --destination $CI_REGISTRY_IMAGE:$CI_COMMIT_SHORT_SHA
        --cache=true
        --cache-ttl=168h
        --build-arg BUILDKIT_INLINE_CACHE=1
  # Kaniko 不需要 privileged 模式！
  # 它在用户空间构建镜像，逐层推送到 Registry
```

**三种方案对比**：

| 方案 | 安全性 | 速度 | 复杂度 | 推荐场景 |
|------|--------|------|--------|---------|
| Socket 挂载 | ❌ 低 | ⚡ 快 | 低 | 受信任的内网环境 |
| DinD 服务 | ⚠️ 中 | ⚡ 快 | 中 | 需要完整 Docker 功能 |
| Kaniko | ✅ 高 | 🐢 稍慢 | 中 | **生产环境推荐** |

#### 步骤4：Shell Executor 适用场景和陷阱

**目标**：在合适的场景使用 Shell Executor，避免常见陷阱。

```toml
# config.toml - Shell Executor 配置
[[runners]]
  name = "shell-executor-simple"
  executor = "shell"
  limit = 1                    # Shell Executor 不允许并发！
  output_limit = 4096

  [runners.shell]
    # 默认 shell（Linux: bash, Windows: powershell）
    # shell = "bash"

  [runners.custom_build_dir]
    enabled = true
```

```yaml
# .gitlab-ci.yml - Shell Executor 安全用法
# 适用场景：简单的 lint、curl 检查、系统级脚本

cleanup-logs:
  stage: cleanup
  tags:
    - shell
  script:
    - echo "Starting cleanup on $(hostname)"
    - find /var/log -name "*.log" -mtime +7 -delete
    - echo "Cleanup completed"
  # 注意：Shell Executor 没有容器隔离
  # 任何 script 都直接在宿主机执行
  # 务必控制权限和命令范围！
```

### 完整代码清单

- Docker Executor config.toml（步骤1）
- Kubernetes Executor Helm values（步骤2）
- 三种 Docker 构建方案 `.gitlab-ci.yml`（步骤3）
- Shell Executor 安全配置（步骤4）

### 测试验证

```bash
# 验证1：Docker Executor 拉取策略
# 第一次运行 job → 日志显示 "Pulling docker image"
# 第二次运行 → 日志显示 "Using docker image"（从缓存）

# 验证2：K8s Executor 资源限制
kubectl describe pod -n gitlab-runner -l job-name=<job-hash>
# 确认 CPU/Memory request 和 limit 符合配置

# 验证3：Kaniko 构建无需 privileged
grep "privileged" /etc/gitlab-runner/config.toml
# 应显示 "privileged = false"
# 但 Kaniko 构建仍应成功

# 验证4：并发控制
# 同时触发 5 个 pipeline
# 观察 Runner 的 limit 参数是否生效（最多同时跑 limit 个）
```

## 4. 项目总结

### Executor 选择决策矩阵

| 需求 | Docker | Kubernetes | Shell | Docker Machine (已弃用) |
|------|--------|------------|-------|------------------------|
| 构建 Docker 镜像 | ✅ (DinD/Kaniko) | ✅ (DinD/Kaniko) | ❌ | - |
| GPU / 特殊硬件 | ✅ (--device) | ✅ (device plugin) | ✅ | - |
| 环境隔离 | ✅ (容器) | ✅ (Pod) | ❌ | - |
| 弹性扩缩容 | ❌ (固定机器) | ✅ (自动) | ❌ | - |
| 简单部署 | ✅ | ❌ (需要 K8s) | ✅ | - |
| 安全性 | ⚠️ | ✅ (Pod 隔离) | ❌ | - |

### 适用场景

- **Docker Executor**：90% 团队的默认选择——构建、测试、部署全覆盖
- **Kubernetes Executor**：已有 K8s 集群、需要弹性扩缩容的团队
- **Shell Executor**：系统级脚本、简单 lint、特殊硬件访问
- **Kaniko**（Build Tool）：替代 DinD，无需 privileged 更安全

### 注意事项

- **Docker Executor 的 privileged 不是默认开启的**——只在需要 DinD 时按需开启
- **Kubernetes Executor 的 Pod 会被 CI 的 `variables` 覆盖资源限制**——注意 `KUBERNETES_CPU_REQUEST` 等变量
- **Shell Executor 绝不用于多租户环境**——没有隔离，一个 job 可以影响其他 job 甚至宿主机
- **Runner 的 concurrent vs limit**：concurrent 是全局并发，limit 是单 Runner 上限

### 常见踩坑经验

1. **DinD 构建时 `docker push` 权限错误**：CI job token 不被 GCR/ECR 接受。根因：`$CI_JOB_TOKEN` 只能推送 GitLab Registry。解决：对 GCR 用 Service Account Key（File 类型 CI Variable）。
2. **K8s Executor Pod 被 OOM Kill（exit 137）**：内存 limit 低于实际使用。根因：开发不了解 CI job 的实际内存消耗。解决：先用宽松的 limit 跑一次，查看实际消耗再调整。
3. **Shell Executor 每次执行后残留文件**：GitLab Runner 自动清理只在 Git 目录范围，但 script 中创建的其他文件不清理。根因：Shell Executor `builds_dir` 内的文件在 job 结束后会被清理，但写入其他路径的不会。解决：在 `after_script` 中显式清理。

### 思考题

1. 如果你的 CI 需要同时构建 amd64 和 arm64 的 Docker 镜像（用于混合架构 Kubernetes 集群），使用哪种 Executor 和构建工具最合适？
2. GitLab Runner 的 `docker+machine` executor 在 17.x 中被移除。如果需要动态创建 CI worker，有哪些替代方案？

> 答案见附录 D。

### 推广计划提示

- **运维**：Executor 选型直接影响 CI 基础设施的 TCO（总拥有成本）
- **开发**：了解不同 Executor 的限制后，在写 `.gitlab-ci.yml` 时能预判哪些配置不兼容
- **安全**：Docker Socket 挂载是最常见的安全风险来源，优先推广 Kaniko
