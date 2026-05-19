# 第15章：日常运维、故障排查与 DEBUG 日志

> 版本：Trivy v0.50+
> 面向人群：运维、DevOps、技术支持

---

## 1. 项目背景

### 业务场景

云帆科技的 Trivy 扫描体系已经运行了半年，覆盖了 30 多个项目的 CI/CD 流水线和 Harbor 镜像仓库。整体运转良好，但运维老李的值班手机开始频繁在深夜响起。

第一次告警是凌晨两点：一个 Java 项目的构建突然失败，报错 `java-db download timeout`。值班工程师重启了构建，但问题在三天后再次爆发——这次是另一个项目、另一个节点。

第二次是周五下午：安全团队发现某台 Jenkins Slave 上连续一周的扫描报告都是空的，Total: 0。起初大家以为项目真的「无懈可击」，后来老李手动登录排查，发现是 `trivy.db` 文件损坏了，Trivy 无法读取，但进程没有报错退出，只是静默地输出了空结果。

第三次更诡异：同样的镜像、同样的 Trivy 版本、同样的命令，在 A 节点扫描出 15 个漏洞，在 B 节点扫描出 0 个。排查了两个小时，最后发现 B 节点的系统时间是 2023 年 1 月——虚拟机在重启后 NTP 同步失败，`trivy.db` 的 `DownloadedAt` 校验导致数据库被判定为「未来数据」，Trivy 拒绝使用它，但又没有给出清晰的错误提示。

CTO 把老李叫到办公室：「Trivy 不是装上就完事了，它是生产环境的关键组件。我要一份标准的运维手册，涵盖常见问题、排查 SOP、DEBUG 方法。我不希望再看到『莫名其妙就修好了』的故障。」

### 痛点放大

**第一，Trivy 的错误信息不够友好。** 很多故障的报错含糊不清，比如 `failed to download vulnerability DB` 可能是网络问题、可能是磁盘满了、可能是权限不足、可能是数据库格式不兼容。没有系统性的排查方法，只能靠「猜」。

**第二，静默失败比报错更危险。** 扫描结果为空、扫描时间异常缩短、exit-code 仍然是 0——这些「表面正常」的现象背后，可能是数据库损坏、缓存污染、或者扫描目标根本没被正确解析。

**第三，多环境差异难以定位。** 开发机、CI 节点、Harbor 扫描器、本地容器，每个环境的 Trivy 版本、数据库版本、配置文件、网络权限都可能不同。同一个问题在不同环境表现完全不同，排查时如同盲人摸象。

**第四，缺乏性能和健康监控。** 团队不知道 Trivy 每天扫描了多少次、平均耗时多少、失败率多少、数据库更新成功率多少。所有指标都是「黑盒」，直到出了故障才被动发现。

**本章的核心目标是：建立 Trivy 的日常运维体系——包括健康检查清单、DEBUG 日志解读、五大常见故障的排查 SOP、以及基础的可观测性建设。**

---

## 2. 项目设计

**场景**：云帆科技的运维值班室，老李正在向新入职的值班工程师小胖和资深 SRE 小白传授 Trivy 的排障经验。

---

**小胖**：（看着值班手册）「老李，这手册上写了『Trivy 报错先重启』。这也太敷衍了吧？」

**老李**：「那是上一版手册，确实敷衍。但说实话，Trivy 的很多报错重启真的能『解决』——因为重启后它重新下载了数据库、清空了缓存、或者换了另一个节点执行。但问题根因没找到，三天后还会再来。」

**小白**：「排障的第一步不是重启，是收集信息。Trivy 提供了 `--debug` 参数，可以输出完整的执行流程。但很多人不知道 `--debug` 分很多子类别：`debug_core`、`debug_alloc`、`debug_event`、`debug_http`。不同的问题要开不同的 debug 级别。」

**小胖**：「那常见的问题有哪些？能不能列个『症状 → 根因 → 解法』的对照表？」

**大师**：「技术映射：排障就像看病。你不能说『我头疼』就让医生开药，医生需要知道你是感冒、是高血压、还是睡眠不足。Trivy 的故障也有典型的『症状组合』：

1. **扫描结果为空 + exit-code 0 + 耗时极短** → 可能是缓存损坏、数据库未加载、或扫描目标为空。
2. **卡在某个阶段不动（如 Downloading DB）** → 网络问题、磁盘 IO 瓶颈、或代理配置错误。
3. **同一个镜像不同节点结果不同** → 数据库版本不一致、Trivy 版本不一致、或时间不同步。
4. **OOM / 进程被 kill** → 大镜像扫描、内存限制不足、或并行度过高。
5. **权限相关错误（Permission Denied）** → 缓存目录不可写、docker.sock 无访问权、或 Registry 认证失败。」

**小胖**：「那 DEBUG 日志怎么看？我开过 `--debug`，输出满屏都是 `blob` 和 `layer` 的字样，根本找不到重点。」

**小白**：「DEBUG 日志的关键是『时间戳 + 模块名 + 日志级别』。Trivy 的日志格式是：

```
2024-03-15T10:30:00.123+0800    DEBUG   fanal/artifact/image/image.go:123   analyzing layer: sha256:abc123...
```

你不需要看懂每一行，只需要用 `grep` 抓关键词：

- `ERROR`：直接报错信息。
- `WARN`：警告，不致命但可能有问题。
- `DEBUG.*db`：数据库相关操作。
- `DEBUG.*fanal`：镜像/文件系统分析阶段。
- `DEBUG.*detector`：漏洞检测阶段。
- `DEBUG.*report`：报告生成阶段。

比如扫描卡住时，你可以：

```bash
trivy image --debug alpine:3.14 2>&1 | tee trivy-debug.log
# 然后在另一个终端
tail -f trivy-debug.log | grep -E "ERROR|WARN|timeout|failed"
```

**大师**：「还有几个不太为人知但非常有用的排障技巧：

1. **`trivy version` 的诊断价值**：它不仅显示 Trivy 版本，还显示 `trivy-db` 的 `UpdatedAt` 和 `DownloadedAt`。如果 `UpdatedAt` 是一周前，说明数据库更新失败；如果 `DownloadedAt` 是空，说明数据库根本没下载成功。
2. **`trivy clean` 的精确清理**：`trivy clean --scan-cache` 只清理扫描缓存，保留数据库；`trivy clean --all` 清理所有内容。很多人一遇到问题就 `rm -rf ~/.cache/trivy`，结果把数据库也删了，下次扫描又要重新下载。
3. **环境变量的 dump**：在排障脚本开头加上 `env | grep TRIVY`，可以确认所有 `TRIVY_*` 环境变量是否按预期设置。
4. **strace 追踪系统调用**：对于极端疑难问题，可以用 `strace -e trace=file,network trivy image ...` 查看 Trivy 实际访问了哪些文件和网络地址。」

**老李**：「我补充一个实战案例。上次那个『A 节点 15 个漏洞、B 节点 0 个漏洞』的问题，最后发现是 B 节点的系统时间不对。Trivy 在加载数据库时会检查 `metadata.json` 中的时间戳，如果系统时间早于 `DownloadedAt`，Trivy 会认为数据库来自『未来』，从而拒绝使用。但旧版本 Trivy 在这种情况下不会报错，只是静默跳过检测，输出空结果。」

**小胖**：「这种静默失败太可怕了。有没有办法监控起来？」

**小白**：「有。我们在 CI 中增加了一个『健康检查』步骤：

```bash
# 扫描后验证结果合理性
trivy image --format json --output report.json target:latest
VULN_COUNT=$(cat report.json | jq '[.Results[]?.Vulnerabilities?[]?] | length')
if [ "$VULN_COUNT" -eq 0 ]; then
    # 二次验证：扫描一个已知有漏洞的镜像
    trivy image --format json --output sanity.json python:3.4-alpine
    SANITY_COUNT=$(cat sanity.json | jq '[.Results[]?.Vulnerabilities?[]?] | length')
    if [ "$SANITY_COUNT" -eq 0 ]; then
        echo "FATAL: Sanity check failed. Trivy may be malfunctioning."
        exit 1
    fi
fi
```

这个『合理性校验』用了一个「已知有漏洞的镜像」作为对照。如果连对照镜像都扫不出漏洞，说明 Trivy 本身有问题，而不是目标镜像真的安全。」

**大师**：「技术映射：这就像医院的『质控样本』。每天化验科都会用已知成分的血液样本测试仪器，如果仪器连质控样本都测不准，那当天的所有检测报告都不可信。」

---

## 3. 项目实战

### 环境准备

- **Trivy**：v0.50+，已安装
- **测试镜像**：`python:3.4-alpine`（作为已知有漏洞的对照镜像）
- **工具**：`strace`（Linux）、`jq`、`curl`

### 步骤一：日常健康检查清单

**目标**：建立每日/每周的标准化检查流程。

创建 `trivy-health-check.sh`：

```bash
#!/bin/bash
# Trivy 日常健康检查脚本
# 建议：每日自动运行，输出到监控系统

EXIT_CODE=0
TRIVY_VERSION=$(trivy version 2>/dev/null)

# 检查 1：Trivy 二进制是否正常
echo "=== Check 1: Trivy Binary ==="
if [ -z "$TRIVY_VERSION" ]; then
    echo "FAIL: trivy command not found or failed"
    EXIT_CODE=1
else
    echo "OK: $TRIVY_VERSION"
fi

# 检查 2：数据库是否存在且未过期
echo "=== Check 2: Database Status ==="
DB_DIR="${TRIVY_CACHE_DIR:-$HOME/.cache/trivy}/db"
if [ -f "$DB_DIR/trivy.db" ]; then
    DB_SIZE=$(du -m "$DB_DIR/trivy.db" | awk '{print $1}')
    echo "OK: trivy.db exists (${DB_SIZE}MB)"

    # 检查数据库时间戳
    if [ -f "$DB_DIR/metadata.json" ]; then
        DOWNLOADED_AT=$(cat "$DB_DIR/metadata.json" | jq -r '.DownloadedAt // empty')
        if [ -n "$DOWNLOADED_AT" ]; then
            echo "OK: Database downloaded at $DOWNLOADED_AT"
        else
            echo "WARN: metadata.json missing DownloadedAt"
        fi
    fi
else
    echo "FAIL: trivy.db not found"
    EXIT_CODE=1
fi

# 检查 3：扫描一个已知有漏洞的镜像（ Sanity Check ）
echo "=== Check 3: Sanity Scan ==="
trivy image --skip-db-update --format json --output /tmp/sanity.json python:3.4-alpine 2>/dev/null
if [ -f /tmp/sanity.json ]; then
    VULN_COUNT=$(cat /tmp/sanity.json | jq '[.Results[]?.Vulnerabilities?[]?] | length')
    if [ "$VULN_COUNT" -gt 0 ]; then
        echo "OK: Sanity scan found $VULN_COUNT vulnerabilities (expected > 0)"
    else
        echo "FAIL: Sanity scan found 0 vulnerabilities - Trivy may be malfunctioning"
        EXIT_CODE=1
    fi
else
    echo "FAIL: Sanity scan failed to produce output"
    EXIT_CODE=1
fi

# 检查 4：缓存目录磁盘空间
echo "=== Check 4: Cache Disk Usage ==="
CACHE_DIR="${TRIVY_CACHE_DIR:-$HOME/.cache/trivy}"
CACHE_SIZE=$(du -sm "$CACHE_DIR" | awk '{print $1}')
if [ "$CACHE_SIZE" -gt 10240 ]; then
    echo "WARN: Cache size ${CACHE_SIZE}MB exceeds 10GB"
else
    echo "OK: Cache size ${CACHE_SIZE}MB"
fi

# 检查 5：网络连通性（如果可以访问外网）
echo "=== Check 5: Network Connectivity ==="
if curl -s --max-time 10 https://ghcr.io/v2/ > /dev/null 2>&1; then
    echo "OK: Can reach GitHub Container Registry"
else
    echo "WARN: Cannot reach GitHub Container Registry (may be expected in offline env)"
fi

echo "=== Health Check Complete ==="
exit $EXIT_CODE
```

运行：

```bash
chmod +x trivy-health-check.sh
./trivy-health-check.sh
```

### 步骤二：五大故障排查 SOP

**目标**：针对最常见的五种故障，提供标准化的排查步骤。

**故障 1：扫描结果为空（Sanity Check 失败）**

```bash
# Step 1: 检查数据库
ls -lh ~/.cache/trivy/db/
cat ~/.cache/trivy/db/metadata.json

# Step 2: 检查系统时间
date
# 如果时间异常，同步 NTP
sudo ntpdate pool.ntp.org

# Step 3: 清理并重新下载数据库
trivy clean --all
trivy image --download-db-only

# Step 4: 再次 Sanity Check
trivy image --format json python:3.4-alpine | jq '.Results[]?.Vulnerabilities? | length'
```

**故障 2：数据库下载超时/失败**

```bash
# Step 1: 检查网络连通性
ping ghcr.io
curl -v https://ghcr.io/v2/

# Step 2: 检查代理设置
echo $HTTP_PROXY $HTTPS_PROXY
env | grep -i proxy

# Step 3: 使用镜像加速或内部 Registry
trivy image --db-repository harbor.internal/trivy-db alpine:3.14

# Step 4: 手动下载并导入
# 在外网机器执行 trivy image --download-db-only，打包搬运到内网
```

**故障 3：同一个镜像不同节点结果不同**

```bash
# Step 1: 对比版本
trivy version  # 节点 A
trivy version  # 节点 B

# Step 2: 对比数据库时间
cat ~/.cache/trivy/db/metadata.json  # 节点 A
cat ~/.cache/trivy/db/metadata.json  # 节点 B

# Step 3: 对比配置
trivy image --debug alpine:3.14 2>&1 | grep -i "config\|severity\|ignore"  # 节点 A
# 同上  # 节点 B

# Step 4: 统一版本和配置
# 固定 Trivy 版本、固定数据库版本、使用相同的 trivy.yaml
```

**故障 4：扫描 OOM / 进程被杀**

```bash
# Step 1: 查看系统日志
dmesg | grep -i "killed process\|oom"
journalctl -k | grep -i "out of memory"

# Step 2: 限制资源后重试
GOMAXPROCS=2 trivy image --timeout 10m --skip-dirs /usr/share/doc big-image:latest

# Step 3: 如果仍 OOM，拆分扫描目标
# 分别扫描 OS 包和语言包
trivy image --vuln-type os big-image:latest
trivy image --vuln-type library big-image:latest
```

**故障 5：Registry 认证失败（401/403）**

```bash
# Step 1: 检查 docker login 状态
cat ~/.docker/config.json | jq '.auths'

# Step 2: 检查 Trivy 的 Registry 凭据
echo $TRIVY_USERNAME $TRIVY_PASSWORD

# Step 3: 显式指定凭据
trivy image --username $USER --password $PASS registry.io/image:tag

# Step 4: 检查 Harbor/Registry 的日志，确认认证方式
trivy image --debug registry.io/image:tag 2>&1 | grep -i "auth\|401\|403"
```

### 步骤三：DEBUG 日志的实战解读

**目标**：通过 `--debug` 定位一个真实故障。

```bash
# 执行 DEBUG 扫描，保存完整日志
trivy image --debug --format json python:3.4-alpine 2> debug.log > report.json

# 分析日志结构
echo "=== ERROR/WARN ==="
grep -E "ERROR|WARN" debug.log

echo "=== 数据库相关 ==="
grep -i "db\|database\|download" debug.log

echo "=== 分析阶段耗时 ==="
grep -i "analyzing\|detected\|completed" debug.log

echo "=== 网络请求 ==="
grep -i "http\|request\|response" debug.log
```

**关键 DEBUG 模式**：

```bash
# 仅启用特定模块的 DEBUG（减少噪音）
TRIVY_DEBUG=true trivy image alpine:3.14
# 或
--debug  # 启用全部 DEBUG
```

### 步骤四：建立性能基线与告警

**目标**：监控 Trivy 的扫描健康度。

创建 `trivy-metrics-exporter.sh`：

```bash
#!/bin/bash
# Trivy 指标导出器（适配 Prometheus textfile collector）

TEXTFILE_DIR="/var/lib/node_exporter/textfile_collector"
IMAGE="${1:-python:3.4-alpine}"
START=$(date +%s%N)

# 执行扫描
if trivy image --format json --output /tmp/metrics-scan.json "$IMAGE" 2>/dev/null; then
    STATUS=1
    VULN_COUNT=$(cat /tmp/metrics-scan.json | jq '[.Results[]?.Vulnerabilities?[]?] | length')
else
    STATUS=0
    VULN_COUNT=0
fi

END=$(date +%s%N)
DURATION_MS=$(( (END - START) / 1000000 ))

# 写入 Prometheus 格式文件
cat > "$TEXTFILE_DIR/trivy_metrics.prom" << EOF
# HELP trivy_scan_duration_ms Trivy scan duration in milliseconds
# TYPE trivy_scan_duration_ms gauge
trivy_scan_duration_ms{image="$IMAGE"} $DURATION_MS

# HELP trivy_scan_success Whether the scan succeeded (1) or failed (0)
# TYPE trivy_scan_success gauge
trivy_scan_success{image="$IMAGE"} $STATUS

# HELP trivy_vulnerabilities_total Total vulnerabilities found
# TYPE trivy_vulnerabilities_total gauge
trivy_vulnerabilities_total{image="$IMAGE"} $VULN_COUNT
EOF
```

**Prometheus 告警规则**：

```yaml
- alert: TrivyScanFailure
  expr: trivy_scan_success == 0
  for: 5m
  annotations:
    summary: "Trivy scan is failing"

- alert: TrivyScanTooSlow
  expr: trivy_scan_duration_ms > 300000
  for: 10m
  annotations:
    summary: "Trivy scan taking over 5 minutes"

- alert: TrivySanityCheckFailed
  expr: trivy_vulnerabilities_total == 0
  for: 1h
  annotations:
    summary: "Trivy sanity check failed - possible malfunction"
```

### 测试验证

1. 运行 `trivy-health-check.sh`，确认所有检查项通过。
2. 手动修改系统时间到错误值，运行扫描，验证 Sanity Check 能检测到异常。
3. 模拟数据库损坏（`echo corrupt > ~/.cache/trivy/db/trivy.db`），验证健康检查失败。
4. 对 `python:3.4-alpine` 执行 `--debug` 扫描，验证能正确解读日志中的关键阶段。
5. 运行 `trivy-metrics-exporter.sh`，验证 Prometheus 指标文件生成正确。

---

## 4. 项目总结

### 优点 & 缺点

| 维度       | 优点                       | 缺点                            |
| -------- | ------------------------ | ----------------------------- |
| DEBUG 能力 | `--debug` 输出详细，支持模块级过滤   | 全量 DEBUG 输出量太大，噪音多            |
| 健康检查     | 可通过 Sanity Check 验证工具状态  | 需要额外的脚本和监控基础设施                |
| 故障恢复     | 多数问题可通过清理缓存/重载数据库解决      | 根因分析需要较深的技术知识                 |
| 可观测性     | JSON 输出便于接入监控体系          | 无官方 Prometheus Exporter，需自行开发 |
| 社区支持     | GitHub Issues 活跃，常见故障有文档 | 某些边缘场景的故障缺乏官方指南               |

### 适用场景

1. **生产环境运维**：建立每日健康检查和故障快速恢复能力。
2. **CI/CD 稳定性**：通过 Sanity Check 防止「假阴性」扫描流入生产。
3. **多环境一致性排查**：快速定位不同节点扫描结果差异的根因。
4. **性能优化基线**：收集扫描耗时和内存指标，识别性能退化。
5. **安全审计**：通过 DEBUG 日志追踪扫描的完整执行路径，满足审计要求。

**不适用场景**：

1. 完全没有 Linux/容器运维经验的团队——Trivy 的排障需要一定的系统知识。
2. 追求「零故障」的关键基础设施——Trivy 作为扫描工具，不适合高可用实时性要求极高的场景。

### 注意事项

- **时间同步是隐形杀手**：NTP 不同步会导致数据库校验失败、扫描结果不一致、证书验证错误。务必确保所有扫描节点的系统时间正确。
- **磁盘空间监控**：`~/.cache/trivy/` 可能快速增长，建议设置告警阈值（如单节点 > 5GB）。
- **版本升级的影响**：Trivy 大版本升级时，旧缓存可能不兼容。升级后建议执行 `trivy clean --all` 并重新下载数据库。
- **DEBUG 日志的敏感性**：`--debug` 输出可能包含镜像内容、文件路径等敏感信息，不要在公共渠道分享 DEBUG 日志。

### 常见踩坑经验

**踩坑案例 1：清理缓存时误删数据库**

- **现象**：执行了 `rm -rf ~/.cache/trivy/`，下次扫描耗时 10 分钟。
- **根因**：粗暴删除了整个缓存目录，包括数据库。
- **解法**：使用 `trivy clean --scan-cache` 精准清理扫描缓存，保留数据库；或使用本章的 `trivy-cache-cleanup.sh` 按策略清理。

**踩坑案例 2：DEBUG 日志没找到问题，但实际是内存不足**

- **现象**：扫描进程消失，DEBUG 日志没有 ERROR。
- **根因**：OOM Killer 直接 kill 了进程，来不及写日志。
- **解法**：查看系统日志 `dmesg` 或 `journalctl`，确认是否有 OOM 记录。

**踩坑案例 3：Harbor 的 Trivy Adapter 日志和独立 Trivy 日志混淆**

- **现象**：在 Harbor 节点上执行 `trivy image` 正常，但 Harbor UI 中扫描失败。
- **根因**：Harbor 使用的是 Trivy Adapter 容器，其配置和日志与宿主机独立。
- **解法**：查看 Harbor 的 `trivy-adapter` 容器日志，而非宿主机的 Trivy 日志。

### 思考题

1. 假设你的团队在全球有 5 个办公区，每个办公区有独立的 Jenkins 集群运行 Trivy。请设计一个「全球 Trivy 健康监控 Dashboard」，需要展示哪些核心指标？数据来源和采集频率如何设计？
2. Trivy 的静默失败（如数据库损坏后输出空结果）是最危险的故障模式。请设计一个「熔断机制」：当连续 3 次 Sanity Check 失败时，自动停止所有扫描任务并告警，防止错误的「安全」报告流入决策流程。

> **答案提示**：第 27 章「监控告警与通知体系集成」将深入介绍 Prometheus + Grafana 的完整监控方案。

---

> **推广计划**：本章是运维值班团队和 SRE 的必读内容。建议将 `trivy-health-check.sh` 纳入每日定时任务（如凌晨 3 点），结果推送到监控系统和 Slack。所有 CI Pipeline 增加 Sanity Check 步骤，作为扫描后的质量门禁。运维团队维护一份《Trivy 故障排查手册》，包含本章的五大 SOP 和 DEBUG 日志解读指南。开发同学在遇到扫描异常时，先运行健康检查脚本并收集 DEBUG 日志，再提交工单。
