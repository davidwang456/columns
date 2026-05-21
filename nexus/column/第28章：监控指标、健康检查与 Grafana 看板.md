# 第28章：监控指标、健康检查与 Grafana 看板

## 1. 项目背景

某周二 18:00，云鲸科技的 IM 群里开始刷屏——"npm install 卡住了"、"mvn compile 超时"、"docker push 推到一半断了"。值班运维浩子登录到 Nexus 服务器一看——JVM 堆内存使用率 98%，Full GC 每分钟 3 次，HTTP 线程池全部占满，BlobStore 的 IO 等待高达 85%。但这些问题是在 16:00 就有了预警信号——16:00 时 JVM 堆使用率升到了 80%，HTTP 请求延迟从 50ms 涨到了 500ms——只是没有人看到这些信号。

更糟的是周一凌晨 3:00 Nexus 自动进入只读模式（frozen），因为磁盘水位超过了 90% 阈值。但由于没有告警，直到早上 9:00 第一个开发者尝试上传时才被发现。整个上午研发团队都在等待运维清理磁盘，2 小时的开发时间白白浪费。

"服务能访问"和"服务健康"之间有一道巨大的鸿沟——可观测性。对 Nexus 来说，可观测性意味着：能看到当前 JVM 的内存和 GC 状态、能看到 HTTP 的吞吐和错误率、能看到 BlobStore 的容量和水位、能看到 proxy 仓库的远程可达性、能在问题发生前收到预警而不是事后才救火。本章将搭建从 JMX 指标采集到 Prometheus 存储再到 Grafana 可视化的完整监控链路，并配置 5 条可落地到值班手机的告警规则。

## 2. 项目设计

浩子把上周故障时间线投到大屏幕上，大师带着运维组做复盘。

**浩子**："大师，我们连 Nexus 的 CPU 和内存都看不到——`docker stats` 只能看容器的整体资源，看不到 JVM 内部的堆使用和 GC 情况。"

**大师**："Nexus 是基于 JVM 的应用，它的真实健康指标在 JMX（Java Management Extensions）里——堆内存（HeapUsed/HeapMax）、GC 次数和时间、活跃线程数、HTTP 请求数、BlobStore 容量。要让这些指标可见，需要一个 JMX 采集器——推荐用 JMX Exporter（Prometheus 官方出品），它是一个 Java Agent，挂载到 Nexus JVM 上，暴露 `/metrics` 端点给 Prometheus 拉取。"

> **技术映射**：Nexus 监控链路 = JMX 指标（Nexus JVM 内部）→ JMX Exporter Agent（标准 Prometheus 格式转换）→ Prometheus（时序存储）→ Grafana（可视化 + 告警）。

**小胖**："那 Prometheus 需要什么配置？要改 Nexus 的启动参数吗？"

**大师**："对。需要在 Nexus 的 JVM 启动参数中加上 `-javaagent:/path/to/jmx_prometheus_javaagent.jar=9107:/path/to/config.yml`。9107 是 JMX Exporter 暴露 metrics 的端口，`config.yml` 定义了哪些 JMX 指标要采集、以什么格式暴露。Nexus 的 `INSTALL4J_ADD_VM_PARAMS` 环境变量就是干这个的。"

**小白**："Grafana 看板怎么设计？几十个指标总不能全堆在一个页面上吧？"

**大师**："按 USE 方法论（Utilization、Saturation、Errors）分层设计。第一行——'金丝雀指标'（服务是否存活：JVM 堆使用率、HTTP 5xx 率、磁盘使用率）。第二行——'性能指标'（吞吐和延迟：HTTP 请求量/QPS、请求延迟 P50/P95/P99、GC 频率）。第三行——'存储指标'（BlobStore 总容量、各仓库增量、proxy 缓存命中率）。第四行——'依赖健康'（proxy 远程可达状态、任务执行成功率）。"

> **技术映射**：Grafana 看板布局 = 金丝雀指标（一眼看出生死）→ 性能指标（深度分析瓶颈）→ 存储指标（容量规划）→ 依赖指标（外部健康）。

**浩子**："告警规则呢？怎么确保既不会漏报又不会被凌晨的误报告警吵醒？"

**大师**："告警三原则——对用户产生实际影响才报、设置持续时间避免抖动、分级通知。P0（5 分钟内必须响应）：Nexus 进程挂了、所有 HTTP 返回 503、磁盘 > 95%。P1（15 分钟内响应）：5xx 错误率 > 5% 持续 5 分钟、blobstore 使用率 > 85%。P2（1 小时内响应）：proxy 远程不可用、任务失败率 > 10%。每个级别对应不同的通知渠道——P0 打电话，P1 发短信，P2 发邮件。"

## 3. 项目实战

### 3.1 环境准备

- 已部署 Nexus 实例（Docker Compose）
- Prometheus 2.x+、Grafana 9.x+
- JMX Exporter（jmx_prometheus_javaagent.jar）

### 3.2 分步实战

#### 步骤一：集成 JMX Exporter 到 Nexus

**目标**：配置 Nexus JVM 暴露 Prometheus metrics 端点。

```bash
# 1. 下载 JMX Exporter jar
wget -O ~/nexus-local/jmx_prometheus_javaagent.jar \
  https://repo1.maven.org/maven2/io/prometheus/jmx/jmx_prometheus_javaagent/0.20.0/jmx_prometheus_javaagent-0.20.0.jar

# 2. 创建 JMX Exporter 配置文件
cat > ~/nexus-local/jmx-config.yml << 'YAML'
---
startDelaySeconds: 0
ssl: false
lowercaseOutputName: true
lowercaseOutputLabelNames: true

rules:
  # JVM 内存指标
  - pattern: "java.lang<type=Memory><>(HeapMemoryUsage|NonHeapMemoryUsage)\.(committed|used|max)"
    name: nexus_jvm_memory_$2_bytes
    type: GAUGE
    attrNameSnakeCase: true

  # JVM GC 指标
  - pattern: "java.lang<type=GarbageCollector, name=(.+)><>(CollectionCount|CollectionTime)"
    name: nexus_jvm_gc_$2
    labels:
      collector: "$1"

  # JVM 线程指标
  - pattern: "java.lang<type=Threading><>(ThreadCount|DaemonThreadCount|PeakThreadCount)"
    name: nexus_jvm_threads

  # HTTP 请求指标（Nexus 定制）
  - pattern: "org.sonatype.nexus<type=extender, name=RequestTiming>(.+)"
    name: nexus_http_$1

  # BlobStore 指标
  - pattern: "org.sonatype.nexus<type=BlobStore, name=(.+), .+>(.+)"
    name: nexus_blobstore_$2
    labels:
      blobstore: "$1"
YAML

# 3. 修改 docker-compose.yml
cat >> ~/nexus-local/docker-compose.yml << 'EOF'
# 在 nexus service 的 environment 中添加：
#   - INSTALL4J_ADD_VM_PARAMS=-Xms1024m -Xmx2048m -javaagent:/opt/jmx_exporter/jmx_prometheus_javaagent.jar=9107:/opt/jmx_exporter/config.yml
#
# 在 nexus service 的 volumes 中添加：
#   - ./jmx_prometheus_javaagent.jar:/opt/jmx_exporter/jmx_prometheus_javaagent.jar
#   - ./jmx-config.yml:/opt/jmx_exporter/config.yml
#
# 在 nexus service 的 ports 中添加：
#   - "9107:9107"  # Prometheus metrics 端点
EOF
```

**重启 Nexus**：

```bash
cd ~/nexus-local
docker compose down && docker compose up -d

# 验证 metrics 端点
sleep 30
curl http://localhost:9107/metrics | head -20
# 预期输出：Prometheus 格式的指标数据
```

#### 步骤二：配置 Prometheus 抓取 Nexus 指标

**目标**：Prometheus 定期抓取 Nexus JMX Exporter 暴露的 metrics。

```yaml
# prometheus.yml — 追加 nexus job
scrape_configs:
  - job_name: 'nexus'
    scrape_interval: 15s
    scrape_timeout: 10s
    static_configs:
      - targets: ['nexus-host:9107']
        labels:
          service: 'nexus'
          env: 'production'
```

**重载 Prometheus 配置**：

```bash
# curl -X POST http://prometheus:9090/-/reload

echo "验证：在 Prometheus UI (http://prometheus:9090) → Targets → nexus job 应为 UP"
echo "查询测试：nexus_jvm_memory_used_bytes"
```

#### 步骤三：设计 Grafana 看板

**目标**：创建分层 Grafana Dashboard——金丝雀 → 性能 → 存储 → 依赖。

```json
// 在 Grafana UI 中创建 Dashboard，添加以下 Panel

// === Row 1: 金丝雀指标 ===

// Panel 1.1 — JVM 堆使用率（Gauge）
// Query: (nexus_jvm_memory_used_bytes{area="heap"} / nexus_jvm_memory_max_bytes{area="heap"}) * 100
// Thresholds: 绿<70%  黄70-85%  红>85%

// Panel 1.2 — HTTP 5xx 率（Stat）
// Query: rate(nexus_http_5xx_total[5m]) / rate(nexus_http_requests_total[5m]) * 100
// Thresholds: 绿<1%  黄1-5%  红>5%

// Panel 1.3 — 磁盘使用率（Gauge）
// Query: nexus_blobstore_totalsize_bytes / nexus_blobstore_quota_bytes * 100
// Thresholds: 绿<70%  黄70-90%  红>90%

// === Row 2: 性能指标 ===

// Panel 2.1 — HTTP QPS（Graph）
// Query: rate(nexus_http_requests_total[1m])

// Panel 2.2 — HTTP 请求延迟 P95（Graph）
// Query: histogram_quantile(0.95, rate(nexus_http_request_duration_seconds_bucket[5m]))

// Panel 2.3 — GC 频率（Graph）
// Query: rate(nexus_jvm_gc_collectioncount[5m])

// === Row 3: 存储指标 ===

// Panel 3.1 — BlobStore 按名称用量（Bar Gauge）
// Query: nexus_blobstore_totalsize_bytes

// Panel 3.2 — BlobStore 日增量（Graph）
// Query: increase(nexus_blobstore_totalsize_bytes[24h])

// === Row 4: 依赖健康 ===

// Panel 4.1 — Proxy 仓库状态（Status）
// Query: nexus_proxy_health_status（需自定义 Groovy 任务暴露）
// Panel 4.2 — 任务成功率（Stat）
// Query: nexus_task_success_total / (nexus_task_success_total + nexus_task_failure_total)
```

**Grafana Dashboard JSON 简化配置**：

通过 Web UI 导入已设计好的 Dashboard JSON：

```bash
# 使用 Grafana API 导入（或手动通过 UI Import）
curl -X POST http://grafana:3000/api/dashboards/db \
  -H "Authorization: Bearer ${GRAFANA_API_KEY}" \
  -H "Content-Type: application/json" \
  -d @nexus-dashboard.json
```

#### 步骤四：配置 5 条生产告警规则

**目标**：在 Prometheus 中配置告警规则，对接 Alertmanager 实现分级通知。

```yaml
# prometheus-alerts.yml — Nexus 告警规则
groups:
  - name: nexus-critical
    rules:
      # 告警 1：Nexus 下线（P0）
      - alert: NexusDown
        expr: up{job="nexus"} == 0
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "Nexus 实例下线"
          description: "Nexus 在 {{ $labels.instance }} 上已 1 分钟无响应"

      # 告警 2：JVM 堆内存使用率过高（P1）
      - alert: NexusHighHeapUsage
        expr: (nexus_jvm_memory_used_bytes{area="heap"} / nexus_jvm_memory_max_bytes{area="heap"}) * 100 > 85
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "Nexus JVM 堆使用率超过 85%"
          description: "当前值: {{ $value | humanize }}%"

      # 告警 3：HTTP 5xx 错误率高（P1）
      - alert: NexusHigh5xxRate
        expr: rate(nexus_http_5xx_total[5m]) / rate(nexus_http_requests_total[5m]) * 100 > 5
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Nexus 5xx 错误率超过 5%"
          description: "当前 5xx 占比: {{ $value | humanize }}%"

      # 告警 4：BlobStore 容量告警（P1）
      - alert: NexusBlobStoreHighUsage
        expr: (nexus_blobstore_totalsize_bytes / nexus_blobstore_quota_bytes) * 100 > 85
        for: 15m
        labels:
          severity: warning
        annotations:
          summary: "BlobStore {{ $labels.blobstore }} 使用率超过 85%"

      # 告警 5：Proxy 远程不可用（P2）
      - alert: NexusProxyUnavailable
        expr: nexus_proxy_health_status == 0
        for: 10m
        labels:
          severity: info
        annotations:
          summary: "Proxy 仓库 {{ $labels.repository }} 远程不可用"
```

**Alertmanager 通知路由**：

```yaml
# alertmanager.yml
route:
  receiver: 'default'
  routes:
    - match:
        severity: critical
      receiver: 'pagerduty'
    - match:
        severity: warning
      receiver: 'wechat'
    - match:
        severity: info
      receiver: 'email'

receivers:
  - name: 'pagerduty'
    pagerduty_configs:
      - routing_key: 'xxx'
  - name: 'wechat'
    wechat_configs:
      - corp_id: 'xxx'
  - name: 'email'
    email_configs:
      - to: 'ops@cloudwhale.com'
```

#### 步骤五：验证端到端监控链路

**目标**：确保指标采集 → 存储 → 可视化 → 告警全链路畅通。

```bash
echo "=== 监控链路验证 ==="

# 1. 验证 Prometheus 抓取成功
echo "[1] 检查 Prometheus targets..."
curl -s http://prometheus:9090/api/v1/targets | jq '.data.activeTargets[] | select(.labels.job=="nexus") | {health, lastScrape}'

# 2. 验证 Grafana 数据源
echo "[2] 检查 Grafana 数据源..."
curl -s http://grafana:3000/api/datasources | jq '.[] | {name, type}'

# 3. 触发一条测试告警（临时修改阈值确认通知渠道可达）
echo "[3] 告警渠道测试..."
# 临时将 NexusHighHeapUsage 的阈值改为 1%，确保触发
# curl -X POST http://alertmanager:9093/api/v1/alerts ...

echo "✅ 监控链路配置完成"
```

### 3.3 常见坑点

| 坑点 | 现象 | 解决方法 |
|------|------|----------|
| JMX Exporter 端口冲突 | Nexus 启动失败 `Address already in use` | 确认 9107 端口未被占用 |
| Prometheus 抓取超时 | Target 显示 DOWN 或 scrape 超时 | Prometheus `scrape_timeout` 需 >10s（Nexus JMX 指标多时响应慢） |
| Grafana 无数据 | 面板显示 No data | 检查 Prometheus 数据源的 URL、指标名拼写 |
| 告警风暴 | 凌晨任务导致短暂 5xx 后被数百条告警轰炸 | 加 `for: 5m` 持续时间，Alertmanager 中 `group_wait: 30s` |

## 4. 项目总结

### 4.1 关键监控指标速查

| 类别 | 指标 | 来源 | 告警阈值 |
|------|------|------|---------|
| JVM 堆使用率 | `nexus_jvm_memory_used_bytes` | JMX | > 85% P1 |
| GC 频率 | `nexus_jvm_gc_collectioncount` | JMX | > 5/min P2 |
| HTTP 5xx 率 | `nexus_http_5xx_total` | JMX | > 5% P1 |
| BlobStore 使用率 | `nexus_blobstore_totalsize_bytes` | JMX | > 85% P1 |
| Proxy 远程状态 | 自定义 Groovy 任务 | JMX | 不可用 P2 |

### 4.2 适用场景

1. **生产环境运维**：实时大盘 + 分级告警，避免"服务挂了还不知道"
2. **容量规划**：通过历史趋势预测磁盘和内存需求
3. **故障复盘**：以 Grafana 面板的时间线还原故障前后的系统状态
4. **SLO 达标监控**：监控 Nexus 的可用性是否满足 SLA（如 99.9%）
5. **优化效果验证**：JVM 参数或配置调整后通过指标对比验证效果

**不适用场景**：
1. 个人开发环境——监控体系的开销超过收益
2. 已有 APM 方案（如 Datadog/New Relic）覆盖——JMX 指标可直接集成

### 4.3 注意事项

- **JMX Exporter 本身消耗资源**：大约 50-100MB JVM 堆外内存
- **Prometheus 抓取间隔不宜过频**：15-30 秒足够，1 秒会显著增加 Nexus CPU
- **Grafana 看板定期维护**：Nexus 版本升级后 JMX 指标名可能变化，需更新面板
- **告警静默期**：计划内维护前先在 Alertmanager 中设置 silence

### 4.4 思考题

1. Nexus 的 JMX 指标中没有直接的"proxy 仓库远程可用性"指标。请设计一个 Groovy 脚本任务，定时检查所有 proxy 仓库的远程状态并暴露为自定义 Prometheus 指标。
2. 如何基于 Prometheus 指标计算 Nexus 的 SLI（Service Level Indicator），并建立 SLO 监控？例如"99.9% 的 HTTP 请求在 500ms 内返回且非 5xx"。

（第27章思考题答案：1. 死信队列实现：在接收端维护一个 `ConcurrentHashMap<String, Integer>` 记录每个 deliveryId 的重试次数。当处理失败时，计数器 +1。如果重试 < 3，返回 HTTP 500 让 Nexus 重试。如果重试 >= 3，将 payload 存入数据库（死信表），返回 HTTP 200 告知 Nexus 已接收，避免继续重试。另起一个定时任务扫描死信表，发送通知给人工处理。2. 上传即审计快照方案：接收 Webhook 的 COMPONENT CREATED 事件 → 解析 component GAV → 调用 Nexus Search API 获取 pom.xml → 解析 pom 中的 dependencies → 对每个依赖查询许可证（通过 Maven Central API 或 ClearlyDefined）→ 用 Apache PDFBox 生成 PDF 报告 → curl 上传到 Raw 仓库 `/audit-reports/<component>/license-audit.pdf`。）

### 4.5 推广计划提示

- **运维部门**：本章是运维值班的核心依赖。72 小时内完成监控链路搭建，将告警对接到值班手机
- **架构组**：审查告警规则的分级是否合理，确保 P0/P1/P2 的定义与公司事件响应流程一致
- **开发团队**：当收到"依赖下载慢"的反馈时，首先看 Grafana 看板确认是 Nexus 负载问题还是客户端网络问题
