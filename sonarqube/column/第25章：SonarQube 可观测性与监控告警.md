# 第25章：SonarQube 可观测性与监控告警

## 1. 项目背景

**业务场景**：某大型零售商的 SonarQube 平台管理着 300+ 项目和 400 名开发者。某个周六凌晨 3 点，SonarQube 网页无法访问——但运维团队直到周一早上才收到开发团队的投诉。排查发现：Elasticsearch 因为磁盘满进入 Read-Only 模式（已在第15章讨论过），但整个周末没有任何告警——因为 SonarQube"不属于生产基础设施"，没有被纳入监控系统。

更糟糕的是，在故障发生前的周五下午，CE 队列已经开始积压（从正常的 5 个任务排队飙升到 200+），如果当时有人注意到这个趋势，就能提前干预。但因为没有 SonarQube 的可观测性配置，这个预警信号被完全忽略了。

**痛点放大**：

- **SonarQube 被排除在监控体系之外**：运维团队认为它是"开发工具"，开发团队认为它是"运维的事"
- **没有 Service Level Objective (SLO)**：没人知道 SonarQube 的可用性目标是什么；没人定义"什么是正常、什么是异常"
- **CE 队列积压是慢性的**：从 5 个排队到 50 个再到 200 个，是渐进式恶化，没有告警阈值
- **扫描失败率无人关心**：当扫描失败率从 2% 升至 15% 时，如果不主动监控，开发者只会默默重试——直到 CI 彻底堵死

## 2. 项目设计

### 剧本式交锋对话

---

**小胖**（周一早上 9:05 发现 SonarQube 打不开，已经堵了 15 个 PR）："大师！SonarQube 挂了！整个周末都挂了！为什么没有人知道？！"

**大师**："因为你们没有监控。你想想，你们的数据库有监控吗？Redis 有监控吗？Kubernetes 有监控吗？"

**小胖**："都有啊——Prometheus + Grafana，还有 PagerDuty 告警。"

**大师**："那 SonarQube 为什么没有？SonarQube 也是一个需要 24x7 运行的服务——只不过它的用户是开发者，不是终端客户。但如果 SonarQube 不可用，开发者的 CI Pipeline 就全堵了——这同样是生产事故。"

**小白**："SonarQube 有 Prometheus 集成吗？我怎么配置？"

**大师**："SonarQube 自带了 Prometheus 集成。你可以开启内置的 Prometheus Exporter，它暴露在 `/api/monitoring/metrics` 端点。然后配置 Prometheus scrape 这个端点，指标就能进入你的监控体系。

除此之外，还有三条监控线要覆盖：

1. **系统健康**：Web、CE、ES、DB 四个组件的 up/down 状态
2. **CE 任务队列**：Pending 任务数、处理速率、失败率
3. **应用日志**：`ce.log`、`web.log`、`es.log` 中的 ERROR 和 WARN"

**小胖**："这些指标里，哪些是最关键的告警指标？"

**大师**："5 个关键告警：

| 告警 | 指标 | 阈值 | 严重级别 |
|------|------|------|---------|
| SonarQube 不可用 | 应用进程或健康 API | 持续 2 分钟 | P1 |
| ES 异常 | ES 集群状态 | 非 Green | P1 |
| CE 队列积压 | Pending Tasks | > 50 | P2 |
| 磁盘 > 85% | 磁盘使用率 | > 85% | P2 |
| 扫描失败率突增 | 失败扫描占比 | > 20%（5 分钟内） | P2 |

这 5 个覆盖了 95% 的生产故障。"

---

## 3. 项目实战

### 3.1 环境准备

- SonarQube 实例，管理员权限
- Prometheus 2.x 和 Grafana 9.x 实例
- 日志收集工具（ELK / Loki / 企业日志平台）

### 3.2 分步实现

**步骤 1：开启 SonarQube Prometheus 集成**

进入 **Administration → Configuration → Monitoring**：
- 勾选 "Enable Prometheus monitoring"
- 点击 Save

验证端点可用：

```bash
curl -s -u admin:Sonar@2024Admin \
  "http://localhost:9000/api/monitoring/metrics" | head -30
```

预期输出（部分指标）：
```
# HELP sonarqube_db_pool_active_connections Database Pool Active Connections
# TYPE sonarqube_db_pool_active_connections gauge
sonarqube_db_pool_active_connections 2.0
# HELP sonarqube_compute_engine_pending_tasks Compute Engine Pending Tasks
# TYPE sonarqube_compute_engine_pending_tasks gauge
sonarqube_compute_engine_pending_tasks 3.0
# HELP sonarqube_compute_engine_processing_time Compute Engine Task Processing Time
```

**步骤 2：配置 Prometheus 抓取**

在 `prometheus.yml` 中添加：

```yaml
scrape_configs:
  - job_name: 'sonarqube'
    metrics_path: '/api/monitoring/metrics'
    basic_auth:
      username: 'admin'
      password: 'Sonar@2024Admin'  # 或使用 Token
    static_configs:
      - targets: ['sonarqube.company.com:9000']
    scrape_interval: 60s
```

如果 SonarQube 使用 HTTPS：

```yaml
  - job_name: 'sonarqube'
    scheme: 'https'
    tls_config:
      insecure_skip_verify: false
    metrics_path: '/api/monitoring/metrics'
    basic_auth:
      username: 'monitor'
      password: '${SONAR_PROMETHEUS_PASSWORD}'
    static_configs:
      - targets: ['sonarqube.company.com']
```

**步骤 3：设计 Grafana 大盘**

创建一个 "SonarQube Overview" Dashboard，包含以下面板：

**Panel 1：系统健康状态**

使用 `sonarqube_health` 指标：

```promql
# 各组件状态
sonarqube_health{component="web"}     # 1 = OK, 0 = Down
sonarqube_health{component="ce"}       # 1 = OK, 0 = Down
sonarqube_health{component="db"}       # 1 = OK, 0 = Down
sonarqube_health{component="es"}       # 1 = OK, 0 = Down
```

**Panel 2：CE 任务队列**

```promql
# CE Pending 任务数
sonarqube_compute_engine_pending_tasks

# CE 处理速率（每秒）
rate(sonarqube_compute_engine_processed_tasks_total[5m])
```

**Panel 3：数据库连接池**

```promql
# 活跃连接数
sonarqube_db_pool_active_connections

# 空闲连接数
sonarqube_db_pool_idle_connections
```

**Panel 4：磁盘使用率**

```promql
# 如果 node_exporter 已部署
100 - (node_filesystem_avail_bytes{mountpoint="/data"} / 
       node_filesystem_size_bytes{mountpoint="/data"} * 100)
```

**步骤 4：配置 Prometheus 告警规则**

创建 `sonarqube-alerts.yml`：

```yaml
groups:
  - name: sonarqube
    rules:
      - alert: SonarQubeDown
        expr: sonarqube_health{component="web"} == 0
        for: 2m
        labels:
          severity: critical
          team: devops
        annotations:
          summary: "SonarQube Web Server is down"
          description: "SonarQube Web has been down for more than 2 minutes."

      - alert: SonarQubeCEQueueBacklog
        expr: sonarqube_compute_engine_pending_tasks > 50
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "CE queue has {{ $value }} pending tasks"
          description: "Compute Engine task queue is backing up.

      - alert: SonarQubeESNotGreen
        expr: sonarqube_health{component="es"} == 0
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "Elasticsearch is not healthy"
```

**步骤 5：日志监控**

配置 Filebeat 或企业日志平台收集关键日志：

```yaml
# filebeat.yml (简化版)
filebeat.inputs:
  - type: log
    enabled: true
    paths:
      - /opt/sonarqube/logs/ce.log
      - /opt/sonarqube/logs/web.log
      - /opt/sonarqube/logs/es.log
    multiline.pattern: '^\d{4}\.\d{2}\.\d{2}'
    multiline.negate: true
    multiline.match: after
```

在日志平台中创建告警规则：

- `ce.log` 中出现 "OutOfMemoryError" → P1 告警
- `ce.log` 中出现 "Failed to execute task" → P2 告警
- `es.log` 中出现 "read-only" 或 "disk exceeded" → P1 告警

**步骤 6：创建综合健康检查脚本**

```bash
#!/bin/bash
# sonarqube-healthcheck.sh - 综合健康检查

SONAR_URL="http://localhost:9000"
TOKEN="squ_xxx"

check_failed=0

check() {
    local name=$1
    local cmd=$2
    if eval "$cmd"; then
        echo "✅ $name"
    else
        echo "❌ $name"
        check_failed=1
    fi
}

check "系统健康 API" \
    "curl -sf -u admin:Sonar@2024Admin '$SONAR_URL/api/system/health' > /dev/null"

check "CE 队列 < 50" \
    'test $(curl -sf -u admin:Sonar@2024Admin "$SONAR_URL/api/ce/activity?statuses=PENDING&ps=1" | python3 -c "import sys,json;print(json.load(sys.stdin)[\"paging\"][\"total\"])") -lt 50'

check "磁盘使用 < 85%" \
    'test $(df /opt/sonarqube/data --output=pcent | tail -1 | tr -d " %") -lt 85'

check "ES 状态健康" \
    'test "$(curl -sf -u admin:Sonar@2024Admin "$SONAR_URL/api/system/info" | python3 -c "import sys,json;print(json.load(sys.stdin).get(\"System\",{}).get(\"Elasticsearch\",{}).get(\"State\",\"\"))")" = "GREEN"'

if [ $check_failed -eq 0 ]; then
    echo "✅ SonarQube is healthy"
else
    echo "❌ Some checks failed"
    exit 1
fi
```

### 3.3 验证

```bash
# 验证 Prometheus 端点
curl -s -u admin:Sonar@2024Admin \
  "http://localhost:9000/api/monitoring/metrics" | wc -l
# 预期输出：> 50 行（表示有足够多的指标）

# 在 Prometheus UI 中验证目标状态
# Targets → sonarqube → State: UP
```

---

## 4. 项目总结

### 4.1 关键监控指标速查

| 指标类别 | Prometheus 指标 | 告警阈值 |
|---------|----------------|---------|
| Web 健康 | `sonarqube_health{component="web"}` | = 0 持续 2 分钟 |
| CE 健康 | `sonarqube_health{component="ce"}` | = 0 持续 2 分钟 |
| ES 健康 | `sonarqube_health{component="es"}` | = 0 持续 1 分钟 |
| CE 队列数 | `sonarqube_compute_engine_pending_tasks` | > 50 |
| CE 处理时间 | `sonarqube_compute_engine_processing_time_seconds` | P99 > 300s |
| DB 活跃连接 | `sonarqube_db_pool_active_connections` | > 50 |

### 4.2 注意事项

1. **Prometheus 端点需要认证**：`/api/monitoring/metrics` 要求认证。使用专用监控账号 + Token。
2. **不要对 SonarQube 做高频轮询**：60 秒抓取间隔是合理值，1 秒一次会影响数据库性能。
3. **日志不要无脑全量采集**：SonarQube 日志量可能很大（尤其是开启了 DEBUG 模式）。只采集 ERROR 和 WARN 级别，或者配置日志平台的过滤规则。

### 4.3 思考题

1. SonarQube 的 CE 队列积压到 100 个时，你应该优先扩容什么资源——CE Worker 数量、数据库连接池、还是 ES 内存？
2. 如果你需要在 Slack/钉钉 中接收 SonarQube 告警，如何设计告警路由？哪些告警走即时 IM，哪些走邮件？

> **答案提示**：第1题先看 CE 日志确认瓶颈（CPU 还是 IO？），通常数据库连接池不够是先扩容的首选。第2题 P1 走即时 IM + PagerDuty，P2 走邮件或群聊。

---

> **推广计划提示**：第一步不是搭一整套监控体系——第一步是把 SonarQube 的健康检查脚本加入团队的 CronJob，每 5 分钟执行一次，失败时发送邮件。这个"最小可行监控"只需 30 分钟就能搭建完。Prometheus + Grafana 可以后续逐步引入。
