# 第13章：Trivy 配置体系与缓存优化

> 版本：Trivy v0.50+
> 面向人群：运维、DevOps、CI/CD 工程师

---

## 1. 项目背景

### 业务场景

云帆科技的 CI/CD 流水线每天执行超过 300 次 Trivy 扫描。运维老李最近收到了一份云账单，发现 Jenkins 集群的存储费用在过去三个月里暴涨了 40%。排查后发现，每台 Jenkins Slave 的 `/var/cache/trivy/` 目录都膨胀到了 8GB 以上——漏洞数据库、Java 数据库、镜像 layer 缓存、扫描结果缓存，各种文件堆积如山，却没有人清理。

更麻烦的是，某次大促前的压测期间，Trivy 扫描突然 OOM（Out of Memory）被杀。一个 12GB 的大数据镜像在扫描时耗尽了 16GB 内存，导致整个构建节点崩溃，连锁反应让后续 20 个构建任务排队等待。事后复盘发现，Trivy 默认会同时在内存中解压和分析多个 layer，而这个镜像有 50 多层，每层都包含大量小文件。

与此同时，开发小王也遇到了诡异的问题。他在本地用 Trivy 扫描一个项目，结果是 15 个漏洞；提交到 CI 后，同样的命令扫出了 18 个漏洞。排查了一整天，最后发现是 CI 的 `trivy.yaml` 和本地的 `trivy.yaml` 内容不一致——CI 用的是三个月前的旧版本配置文件，里面少了 `--ignore-unfixed` 参数。

CTO 在运维周会上发话：「Trivy 不是装上就能跑，要配好、调好、管好。我要一个统一的配置中心，所有环境的 Trivy 行为一致；我要缓存有生命周期，不能无限膨胀；我要大镜像扫描时不会把机器搞挂。」

### 痛点放大

**第一，配置碎片化。** Trivy 支持命令行参数、环境变量、配置文件（`trivy.yaml`）、项目级 `.trivyignore` 四种配置方式，优先级关系复杂。不同环境（开发机、CI、生产巡检）使用不同的配置组合，导致「同样的命令，不同的结果」。

**第二，缓存失控。** Trivy 的默认缓存目录是用户主目录下的 `.cache/trivy/`，在 CI 容器中这个目录每次构建都被销毁；在 Jenkins Slave 上这个目录永久保留。没有统一策略的结果是：要么每次重新下载数据库浪费时间，要么缓存无限膨胀浪费磁盘。

**第三，资源消耗不可预测。** 扫描一个普通 Alpine 镜像只需要 200MB 内存，但扫描一个包含数十万文件的 Ubuntu 镜像可能需要 8GB 以上。团队不知道该如何设置资源限制，也不知道哪些参数可以优化扫描性能。

**第四，缺乏性能基线。** 没有人知道「正常」的扫描应该花多长时间。当扫描时间从 30 秒变成 10 分钟时，是数据库变大了？镜像变胖了？还是网络卡顿了？没有监控和基线，性能问题只能靠猜。

**本章的核心目标是：掌握 Trivy 的配置优先级体系、建立统一的配置模板、优化缓存策略、控制扫描资源消耗，让 Trivy 从「能跑」进化到「跑得稳、跑得快」。**

---

## 2. 项目设计

**场景**：云帆科技的运维优化专项会，老李、小胖（开发代表）和小白（DevOps）正在讨论 Trivy 的性能问题。

---

**小胖**：「老李，我本地扫一个镜像只要 20 秒，CI 上同样的命令要 3 分钟。CI 的机器配置比我的 MacBook 还高啊，怎么会更慢？」

**小白**：「CI 节点是共享的，同时跑十几个构建任务，磁盘 IO 和网络带宽被抢光了。而且你本地的数据库是昨天下的，CI 节点可能是今天第一次启动，要先下载 300MB 的 `trivy.db`。」

**老李**：「更惨的是，有些 Slave 节点的磁盘快满了，缓存写不进去，Trivy 就报错。我已经收到三次工单了。」

**大师**：「这些都是典型的『配置和缓存治理』问题。我们先理清 Trivy 的配置体系。Trivy 的参数来源有四个，优先级从高到低：

1. **命令行参数**：如 `--severity HIGH,CRITICAL`，优先级最高。
2. **环境变量**：如 `TRIVY_SEVERITY=HIGH,CRITICAL`。
3. **配置文件**：如 `trivy.yaml` 或 `~/.config/trivy/trivy.yaml`。
4. **默认值**：Trivy 内置的默认行为。

问题就出在这里——你们团队没有统一的配置入口。开发用命令行参数，CI 用环境变量，运维用配置文件，三方互相覆盖，结果当然不一致。」

**小胖**：「那最佳实践是什么？全用命令行？全用配置文件？」

**大师**：「技术映射：配置文件是『宪法』，规定团队的基本行为；环境变量是『地方法规』，适应不同环境的特殊需求；命令行参数是『临时指令』，只用于一次性调试。**最佳实践是：把团队的通用策略写进 `trivy.yaml`，提交到 Git 仓库；环境差异通过环境变量微调；禁止在生产/CI 环境中使用命令行参数覆盖核心策略。**」

**小白**：「那缓存怎么管？现在有的 Slave 节点缓存 8GB，有的节点每次从零下载。」

**大师**：「缓存分三类：

1. **漏洞数据库缓存**（`db/trivy.db`、`db/trivy-java.db`）：这是最大的文件，总共约 800MB。应该放在共享存储或预置在 CI 基础镜像中。
2. **镜像 Layer 缓存**（`fanal/` 目录）：扫描过的镜像 layer 的解压结果。对于频繁构建相同 base 镜像的团队，这个缓存能大幅加速二次扫描。
3. **扫描结果缓存**：某些版本的 Trivy 会缓存扫描结果，相同 digest 的镜像二次扫描时直接返回缓存。

我的建议是：在 CI 中配置 `TRIVY_CACHE_DIR` 指向一个持久化卷（如 NFS、EBS、HostPath），并在 Job 结束时执行 `trivy clean --scan-cache` 清理过期的 layer 缓存，但保留数据库文件。」

**老李**：「那个 OOM 的问题呢？12GB 的镜像把节点搞挂了。」

**大师**：「大镜像扫描的内存消耗主要来自 layer 解压和文件系统遍历。优化策略有：

1. **限制并行度**：Trivy 默认会并行分析多个 layer，大镜像时可以降低并行度（通过 Go runtime 的 `GOMAXPROCS` 或 Trivy 的 `--parallel` 参数）。
2. **跳过不必要的 scanner**：如果只需要漏洞扫描，关闭 secret、config、license scanner，减少内存占用。
3. **排除大目录**：用 `--skip-dirs` 跳过镜像中不需要分析的大目录（如 `/usr/share/doc`、日志目录）。
4. **容器资源限制**：给 Trivy 容器设置内存限制（如 `-m 4g`），超限时优雅退出而不是拖垮节点。

**小胖**：「那性能基线怎么建？」

**小白**：「可以在每次扫描时记录耗时和内存峰值，输出到 Prometheus。比如：

```bash
/usr/bin/time -v trivy image --format json myapp:latest 2> scan-metrics.txt
```

然后解析 `scan-metrics.txt` 中的 `Elapsed (wall clock) time` 和 `Maximum resident set size`，推送到监控系统。」

**大师**：「技术映射：性能基线就像汽车的油耗表。你不看油耗表，就不知道什么时候该保养。记录每次扫描的『时间-内存-镜像大小』三元组，画出趋势图，异常波动一目了然。」

---

## 3. 项目实战

### 环境准备

- **Trivy**：v0.50+，已安装
- **Docker**：用于构建测试镜像
- **监控工具**：可选（Prometheus、Grafana）
- **测试镜像**：一个大镜像（如 `ubuntu:22.04` 安装大量包）用于压力测试

### 步骤一：理解配置优先级

**目标**：验证四种配置来源的覆盖关系。

创建 `trivy.yaml`：

```yaml
severity:
  - HIGH
  - CRITICAL
format: table
exit-code: 0
scanners:
  - vuln
```

测试优先级：

```bash
# 测试 1：配置文件生效
trivy image python:3.4-alpine
# 预期：只输出 HIGH/CRITICAL，table 格式

# 测试 2：环境变量覆盖配置文件
export TRIVY_FORMAT=json
trivy image python:3.4-alpine
# 预期：输出 JSON 格式（环境变量覆盖配置文件的 table）

# 测试 3：命令行参数覆盖环境变量和配置文件
trivy image --format sarif python:3.4-alpine
# 预期：输出 SARIF 格式（命令行优先级最高）
```

**优先级结论**：`CLI Flag > 环境变量 > 配置文件 > 默认值`。

### 步骤二：建立团队统一的 `trivy.yaml`

**目标**：创建一份覆盖全团队的标准配置。

创建 `trivy-team.yaml`：

```yaml
# 云帆科技 - Trivy 团队标准配置
# 放置路径：项目根目录 或 ~/.config/trivy/trivy.yaml

# 严重级别过滤：只关注 HIGH 及以上
severity:
  - HIGH
  - CRITICAL

# 扫描器：精简以提升性能
scanners:
  - vuln
  - secret

# 漏洞类型：默认全部，可按需调整
# vuln-type: os,library

# 报告格式：默认 table，CI 中可覆盖为 json
format: table

# 缓存目录：CI 环境中需确保此目录持久化或预置
cache-dir: /var/cache/trivy

# 跳过未修复的漏洞：减少噪音（根据团队策略决定是否启用）
ignore-unfixed: false

# 退出码策略：发现 HIGH/CRITICAL 时返回 1，用于 CI 门禁
exit-code: 1

# 超时设置：防止大镜像扫描无限挂起
timeout: 10m

# 扫描配置
scan:
  # 跳过特定目录（根据项目调整）
  skip-dirs:
    - ./node_modules
    - ./vendor
    - ./.git
    - /usr/share/doc
    - /var/log

# 数据库配置
db:
  # 跳过数据库更新（离线环境使用）
  # skip-update: true
  # 自定义数据库仓库（内网环境使用）
  # repository: harbor.cloud-sail.internal/security/trivy-db

# Secret 扫描配置
secret:
  # 启用 Git 历史扫描（定期执行，非每次 CI）
  # scan-git-history: true
```

**使用方式**：

```bash
# 方式 1：放在项目根目录，Trivy 自动识别
cp trivy-team.yaml trivy.yaml

# 方式 2：显式指定配置文件
trivy image --config trivy-team.yaml python:3.4-alpine

# 方式 3：放在用户配置目录
mkdir -p ~/.config/trivy
cp trivy-team.yaml ~/.config/trivy/trivy.yaml
```

### 步骤三：缓存优化与清理策略

**目标**：建立缓存的生命周期管理，防止磁盘膨胀。

查看当前缓存占用：

```bash
du -sh ~/.cache/trivy/*
```

**预期输出**：
```
300M    ~/.cache/trivy/db
5.2G    ~/.cache/trivy/fanal
100M    ~/.cache/trivy/java-db
```

清理策略脚本 `trivy-cache-cleanup.sh`：

```bash
#!/bin/bash
# Trivy 缓存清理脚本
# 建议：每周执行一次

CACHE_DIR="${TRIVY_CACHE_DIR:-$HOME/.cache/trivy}"

# 保留数据库，清理 layer 缓存（超过 7 天未访问的）
find "$CACHE_DIR/fanal" -type f -atime +7 -delete

# 清理空目录
find "$CACHE_DIR/fanal" -type d -empty -delete

# 输出清理后的占用
echo "Cache cleaned. Current usage:"
du -sh "$CACHE_DIR"/*

# 如果总占用超过 10GB，发送告警
TOTAL_KB=$(du -sk "$CACHE_DIR" | awk '{print $1}')
TOTAL_GB=$((TOTAL_KB / 1024 / 1024))
if [ "$TOTAL_GB" -gt 10 ]; then
    echo "WARNING: Trivy cache exceeds 10GB ($TOTAL_GB GB)"
    # 可接入告警系统
fi
```

加入 crontab：

```bash
# 每周日凌晨 2 点清理
0 2 * * 0 /opt/scripts/trivy-cache-cleanup.sh >> /var/log/trivy-cleanup.log 2>&1
```

### 步骤四：大镜像扫描优化

**目标**：通过参数调整，降低大镜像扫描的资源消耗。

构建一个大镜像用于测试：

```dockerfile
FROM ubuntu:22.04
RUN apt-get update && apt-get install -y \
    build-essential python3 python3-pip \
    openjdk-11-jdk nodejs npm \
    libxml2-dev libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*
RUN pip3 install numpy pandas scipy scikit-learn
RUN npm install -g @angular/cli react react-dom
```

```bash
docker build -t big-image:latest .
docker images big-image:latest
# 预期镜像大小：> 2GB
```

**基准测试**：

```bash
# 测试 1：默认参数扫描（记录耗时和内存）
/usr/bin/time -v trivy image --format json big-image:latest > /dev/null 2> baseline.txt

# 查看结果
grep -E "Elapsed|Maximum resident" baseline.txt
```

**优化后扫描**：

```bash
# 优化 1：限制 Go 并行度
GOMAXPROCS=2 /usr/bin/time -v trivy image --format json big-image:latest > /dev/null 2> opt1.txt

# 优化 2：跳过文档和日志目录
trivy image --format json --skip-dirs /usr/share/doc --skip-dirs /var/log \
  big-image:latest > /dev/null 2> opt2.txt

# 优化 3：仅扫描 OS 包（如果不需要语言包分析）
trivy image --format json --vuln-type os big-image:latest > /dev/null 2> opt3.txt

# 优化 4：组合优化
GOMAXPROCS=2 trivy image --format json --vuln-type os \
  --skip-dirs /usr/share/doc --skip-dirs /var/log \
  --timeout 10m big-image:latest > /dev/null 2> opt4.txt
```

**对比结果**：

```bash
echo "=== 基准 ==="
grep -E "Elapsed|Maximum resident" baseline.txt
echo "=== 优化 4 ==="
grep -E "Elapsed|Maximum resident" opt4.txt
```

**预期效果**：组合优化后，内存占用降低 30-50%，扫描时间根据场景缩短 20-40%。

### 步骤五：容器资源限制

**目标**：防止 Trivy 容器消耗过多资源导致节点崩溃。

```bash
# 限制 CPU 2 核、内存 4GB、交换分区 1GB
docker run --rm \
  --cpus="2.0" \
  --memory="4g" \
  --memory-swap="5g" \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v $HOME/.cache/trivy:/root/.cache/trivy \
  aquasec/trivy:0.50.0 image big-image:latest
```

**Kubernetes 中的资源限制**：

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: trivy-scan
spec:
  template:
    spec:
      containers:
      - name: trivy
        image: aquasec/trivy:0.50.0
        command: ["trivy", "image", "target-image:latest"]
        resources:
          limits:
            cpu: "2"
            memory: "4Gi"
          requests:
            cpu: "500m"
            memory: "1Gi"
      restartPolicy: Never
```

### 步骤六：性能监控与基线建立

**目标**：记录每次扫描的性能指标，建立可视化监控。

创建 `scan-with-metrics.sh`：

```bash
#!/bin/bash
# 带性能指标采集的 Trivy 扫描

IMAGE="$1"
METRICS_FILE="/var/log/trivy-metrics.log"

# 记录开始时间
START_TIME=$(date +%s)
START_ISO=$(date -Iseconds)

# 执行扫描并采集资源使用
/usr/bin/time -v trivy image --format json "$IMAGE" > /tmp/scan-result.json 2> /tmp/scan-stderr.txt
EXIT_CODE=$?

# 解析指标
END_TIME=$(date +%s)
ELAPSED=$((END_TIME - START_TIME))
MAX_RSS=$(grep "Maximum resident set size" /tmp/scan-stderr.txt | awk '{print $6}')
VULN_COUNT=$(cat /tmp/scan-result.json | jq '[.Results[]?.Vulnerabilities?[]?] | length')
IMAGE_SIZE=$(docker images --format "{{.Size}}" "$IMAGE" | head -1)

# 输出 Prometheus 格式指标
cat >> "$METRICS_FILE" << EOF
trivy_scan_duration_seconds{image="$IMAGE"} $ELAPSED
trivy_scan_memory_rss_bytes{image="$IMAGE"} ${MAX_RSS:-0}
trivy_scan_vulnerabilities_total{image="$IMAGE"} ${VULN_COUNT:-0}
EOF

echo "[$START_ISO] Scanned $IMAGE: ${ELAPSED}s, ${MAX_RSS}KB RSS, ${VULN_COUNT} vulns"

exit $EXIT_CODE
```

**Prometheus 采集配置**：

```yaml
# node-exporter textfile collector 配置
- job_name: 'trivy-metrics'
  static_configs:
    - targets: ['jenkins-slave-01:9100']
  metric_relabel_configs:
    - source_labels: [__name__]
      regex: 'trivy_.*'
      action: keep
```

### 测试验证

1. 创建 `trivy-team.yaml` 并验证配置优先级（CLI > Env > YAML > Default）。
2. 查看 `~/.cache/trivy/` 目录结构，确认数据库和 fanal 缓存的位置。
3. 运行 `trivy-cache-cleanup.sh`，验证超过 7 天的文件被清理。
4. 对 `big-image:latest` 执行基准测试和组合优化测试，验证内存和耗时下降。
5. 运行 `scan-with-metrics.sh`，确认性能指标被正确记录到日志文件。

---

## 4. 项目总结

### 优点 & 缺点

| 维度 | 优点 | 缺点 |
|------|------|------|
| 配置体系 | 四层优先级灵活，适应多种场景 | 优先级关系复杂，新手容易混淆 |
| 缓存机制 | 数据库和 layer 分层缓存，二次扫描快 | 默认无自动清理，需要运维干预 |
| 资源可控 | 可通过 GOMAXPROCS、skip-dirs、timeout 调优 | 大镜像扫描仍有 OOM 风险 |
| 容器友好 | 支持 Docker/K8s 运行，资源限制明确 | 需要正确挂载 docker.sock 和缓存卷 |
| 可观测性 | JSON 输出便于解析和监控 | 无内置 Prometheus exporter，需自行采集 |

### 适用场景

1. **CI/CD 性能优化**：通过缓存共享和参数调优，将扫描时间控制在 1 分钟以内。
2. **大规模镜像扫描**：对 >5GB 的镜像进行资源受限的扫描，避免拖垮构建节点。
3. **离线环境配置**：通过统一配置文件和预置数据库，实现无外网的标准化扫描。
4. **多团队协作**：通过 Git 管理的 `trivy.yaml`，确保所有项目的扫描行为一致。
5. **成本治理**：定期清理缓存、监控存储增长，降低云存储费用。

**不适用场景**：
1. 实时性要求极高的扫描场景（如每次文件保存都触发扫描）——Trivy 的启动开销不适合亚秒级响应。
2. 资源极其受限的边缘设备（如 256MB 内存的 IoT 网关）——需要考虑更轻量的扫描方案。

### 注意事项

- **配置文件的搜索路径**：Trivy 按以下顺序查找配置文件：当前目录的 `trivy.yaml` → `~/.config/trivy/trivy.yaml` → 系统配置目录。确保团队知道哪个文件在生效。
- **环境变量的命名**：Trivy 的环境变量以 `TRIVY_` 为前缀，且需要将配置项转为大写和下划线（如 `cache-dir` → `TRIVY_CACHE_DIR`）。
- **超时设置**：`--timeout` 的默认值为 5 分钟，对大镜像可能不够。建议在 CI 中显式设置为 10-30 分钟。
- **缓存目录权限**：CI 容器以非 root 运行时，需确保 `cache-dir` 对运行用户可写。

### 常见踩坑经验

**踩坑案例 1：配置文件不生效**
- **现象**：`trivy.yaml` 中配置了 `severity: [HIGH, CRITICAL]`，但扫描仍然输出所有级别。
- **根因**：当前目录下有另一个 `.trivyignore` 或环境变量 `TRIVY_SEVERITY` 覆盖了配置。
- **解法**：使用 `trivy image --debug` 查看启动日志中的配置加载路径和最终生效值。

**踩坑案例 2：CI 缓存未命中导致重复下载数据库**
- **现象**：每次 CI 构建都要花 2 分钟下载数据库。
- **根因**：GitLab CI 的 cache 路径配置错误，或 Runner 使用的是「一次性」容器。
- **解法**：确认 `TRIVY_CACHE_DIR` 指向的目录在 cache 配置中；对于 Kubernetes Runner，使用 HostPath 或 PVC 持久化缓存。

**踩坑案例 3：大镜像扫描 OOM**
- **现象**：扫描 10GB 镜像时容器被 K8s OOMKilled。
- **根因**：没有设置内存限制，Trivy 的 layer 解压消耗了全部可用内存。
- **解法**：设置 `--memory` 限制和 `--timeout`；用 `--skip-dirs` 排除大目录；必要时拆分成多个小镜像扫描。

### 思考题

1. 假设你的团队有 100 个微服务，每个服务每天构建 10 次，每次扫描平均需要 300MB 内存和 30 秒。请设计一个资源模型：需要多少台 8C16G 的 Jenkins Slave 才能满足峰值吞吐？缓存命中率从 0% 提升到 80% 后，资源需求如何变化？
2. Trivy 的配置优先级（CLI > Env > YAML > Default）虽然灵活，但在大型团队中容易导致「配置来源不可追溯」的问题。请设计一个「配置溯源」方案，使得任何一次扫描都能输出「最终生效的每个参数来自哪里」。

> **答案提示**：第 24 章「性能调优与大规模镜像扫描策略」将深入探讨分布式扫描和资源调度方案。

---

> **推广计划**：本章是运维和 DevOps 团队的必读内容。建议将 `trivy-team.yaml` 纳入团队 Git 仓库模板，所有新项目初始化时自动复制。CI/CD 负责人将缓存策略和清理脚本纳入 Pipeline 标准配置。监控团队将 `scan-with-metrics.sh` 的输出接入 Prometheus，建立扫描性能的告警基线（如「单次扫描超过 5 分钟」或「内存占用超过 4GB」）。开发团队了解配置优先级规则，避免本地命令行参数与团队规范冲突。
