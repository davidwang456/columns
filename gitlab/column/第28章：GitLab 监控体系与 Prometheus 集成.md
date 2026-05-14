# 第28章：GitLab 监控体系与 Prometheus 集成

## 1. 项目背景

> **业务场景**：某公司 GitLab 实例上个月发生了两次重大故障——一次是 Puma worker 全部 busy 导致 502，另一次是 Gitaly 延迟飙升导致 `git push` 超时。但两次故障都是用户报告后运维才知道——没有监控告警，没有历史指标可以回溯分析。

故障复盘会上，CTO 质问："我们怎么知道 GitLab 现在的健康状态？当前有多少活跃用户？Puma 队列长度是多少？Gitaly 的 P99 延迟是多少？"运维答不上来——因为根本没有监控体系。

事后团队花了 2 周时间搭建了监控体系——Prometheus 采集指标、Grafana 可视化、AlertManager 告警。CTO 要求："GitLab 的所有核心指标都必须可见、可告警、可回溯。"

**痛点放大**：GitLab 内置了丰富的 Prometheus 指标——Rails Controller 的请求延迟、Sidekiq 队列长度、Gitaly RPC 延迟、PostgreSQL 连接池状态、Redis 命中率等等。但这些指标默认只是暴露在 `/metrics` 端点上，没有人去采、去看、去告警，就等于没有监控。不要等到故障发生才想起监控——提前搭建好监控体系，让 GitLab 的运行状态一目了然。

## 2. 项目设计——剧本式交锋对话

**场景**：故障复盘会后的第二周，运维团队在搭建监控系统。

---

**小胖**："Prometheus + Grafana 这套我熟——装上就能看 CPU、内存、磁盘。但 GitLab 自带的指标有啥用？"

**大师**："系统指标（CPU、内存）只是第一层。GitLab 应用层面的指标才是真正有诊断价值的——比如 Rails Controller 的 P99 延迟如果突然从 200ms 涨到 2 秒，说明某个页面的数据库查询或 Gitaly 调用变慢了。Sidekiq 队列长度如果持续增长，说明异步任务处理不过来。这些指标能在用户感受到问题之前就发出预警。"

**小白**："指标那么多，怎么看哪些是关键的？"

**大师**："用 RED 方法——Rate（请求速率）、Errors（错误率）、Duration（延迟）。对 GitLab 来说：Rate = API 请求数/秒、Errors = 5xx/4xx 比例、Duration = P95/P99 延迟。这三个维度覆盖了 90% 的故障发现场景。"

**小胖**："Grafana 的 Dashboard 怎么设计？官方有现成的吗？"

**大师**："GitLab 官方提供了一整套 Grafana Dashboard JSON——包括 Rails Controller、Sidekiq、Gitaly、PostgreSQL、Redis 的专属面板。你只需要导入并调整即可，不需要从零画图。技术映射——Grafana Dashboard 就像汽车的仪表盘：速度表（QPS）、油量表（内存）、引擎温度表（CPU）、故障指示灯（错误率）。"

---

## 3. 项目实战

### 环境准备

> **目标**：搭建 Prometheus + Grafana + AlertManager 监控体系，接入 GitLab 指标，配置核心告警规则。

**前置条件**：GitLab CE 17.x（已启用 Prometheus），独立的 Prometheus/Grafana 服务器或容器。

### 分步实现

#### 步骤1：启用 GitLab 内置 Prometheus 指标

**目标**：确保 GitLab 各组件暴露 `/metrics` 端点。

```bash
# 检查 Prometheus 是否启用
sudo gitlab-rails runner "puts Gitlab.config.prometheus.enabled"

# 如果未启用，编辑 gitlab.rb：
sudo vi /etc/gitlab/gitlab.rb

# Omnibus 版本中 Prometheus 默认随安装启用，但可以禁用内置版
# 用外部 Prometheus（推荐）：

# 保留 GitLab 本身的指标导出，但不启用内置 Prometheus Server
prometheus_monitoring['enable'] = false
prometheus['enable'] = false
node_exporter['enable'] = true          # 导出系统指标
gitlab_exporter['enable'] = true        # 导出 GitLab 应用指标
redis_exporter['enable'] = true         # 导出 Redis 指标
postgres_exporter['enable'] = true      # 导出 PostgreSQL 指标
gitaly['prometheus_listen_addr'] = "0.0.0.0:9236"  # Gitaly 指标

# 应用配置
sudo gitlab-ctl reconfigure

# 验证指标端点
curl -s http://localhost:9168/metrics | head -20   # GitLab Exporter
curl -s http://localhost:9236/metrics | head -20   # Gitaly
curl -s http://localhost:9100/metrics | head -20   # Node Exporter
```

#### 步骤2：部署外部 Prometheus 并配置采集

**目标**：用 Docker Compose 部署 Prometheus，配置采集 GitLab 指标。

```yaml
# docker-compose-monitoring.yml
version: '3.8'

services:
  prometheus:
    image: prom/prometheus:v2.51.0
    container_name: prometheus
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - prometheus_data:/prometheus
    command:
      - '--config.file=/etc/prometheus/prometheus.yml'
      - '--storage.tsdb.path=/prometheus'
      - '--web.enable-lifecycle'
      - '--storage.tsdb.retention.time=30d'
    ports:
      - "9090:9090"
    restart: always

  grafana:
    image: grafana/grafana:10.4.0
    container_name: grafana
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=admin
      - GF_INSTALL_PLUGINS=grafana-piechart-panel
    volumes:
      - grafana_data:/var/lib/grafana
      - ./grafana/dashboards:/etc/grafana/provisioning/dashboards:ro
      - ./grafana/datasources:/etc/grafana/provisioning/datasources:ro
    ports:
      - "3000:3000"
    restart: always
    depends_on:
      - prometheus

  alertmanager:
    image: prom/alertmanager:v0.27.0
    container_name: alertmanager
    volumes:
      - ./alertmanager.yml:/etc/alertmanager/alertmanager.yml:ro
    ports:
      - "9093:9093"
    restart: always

volumes:
  prometheus_data:
  grafana_data:
```

**Prometheus 采集配置**：

```yaml
# prometheus.yml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

# GitLab 专用告警规则
rule_files:
  - 'gitlab-alerts.yml'

scrape_configs:
  # GitLab 服务器系统指标（节点 Exporter）
  - job_name: 'gitlab-node'
    static_configs:
      - targets: ['gitlab-server:9100']
        labels:
          instance: 'gitlab-prod'

  # GitLab 应用指标（GitLab Exporter）
  - job_name: 'gitlab-app'
    metrics_path: '/metrics'
    static_configs:
      - targets: ['gitlab-server:9168']

  # Gitaly 指标
  - job_name: 'gitlab-gitaly'
    static_configs:
      - targets: ['gitlab-server:9236']

  # Redis 指标
  - job_name: 'gitlab-redis'
    static_configs:
      - targets: ['gitlab-server:9121']

  # PostgreSQL 指标
  - job_name: 'gitlab-postgres'
    static_configs:
      - targets: ['gitlab-server:9187']

  # Sidekiq 指标（通过 GitLab Exporter）
  - job_name: 'gitlab-sidekiq'
    static_configs:
      - targets: ['gitlab-server:9168']
    params:
      # 过滤 Sidekiq 相关指标
      metric_path: ['/metrics']
```

#### 步骤3：配置关键告警规则

**目标**：配置 RED（Rate/Errors/Duration）核心告警。

```yaml
# gitlab-alerts.yml
groups:
  - name: gitlab-critical
    rules:
      # ===== 可用性告警 =====
      - alert: GitLabHigh5xxRate
        expr: |
          rate(gitlab_requests_total{status=~"5.."}[5m]) /
          rate(gitlab_requests_total[5m]) > 0.05
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "GitLab 5xx 错误率超过 5%"
          description: "当前 5xx 率: {{ $value | humanizePercentage }}"
          runbook: "https://wiki.internal/gitlab-5xx-runbook"

      # ===== 延迟告警 =====
      - alert: GitLabHighLatency
        expr: |
          histogram_quantile(0.95,
            rate(gitlab_requests_duration_seconds_bucket[5m])
          ) > 2
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "GitLab API P95 延迟超过 2 秒"
          description: "P95: {{ $value }}s"

      # ===== 容量告警 =====
      - alert: GitLabPumaQueueFull
        expr: gitlab_puma_queue_size > 10
        for: 2m
        labels:
          severity: warning
        annotations:
          summary: "Puma 请求队列堆积"
          description: "队列长度: {{ $value }}"

      - alert: GitLabGitalyHighLatency
        expr: |
          histogram_quantile(0.99,
            rate(grpc_server_handling_seconds_bucket{grpc_method!="Ping"}[5m])
          ) > 1
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "Gitaly RPC P99 延迟超过 1 秒"

      # ===== 资源告警 =====
      - alert: GitLabDiskFull
        expr: |
          (node_filesystem_avail_bytes{mountpoint="/var/opt/gitlab"} /
           node_filesystem_size_bytes{mountpoint="/var/opt/gitlab"}) < 0.15
        for: 10m
        labels:
          severity: critical
        annotations:
          summary: "GitLab 数据盘使用率超过 85%"

      - alert: GitLabHighMemory
        expr: (1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes) > 0.9
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "GitLab 服务器内存使用超过 90%"
```

#### 步骤4：Grafana Dashboard 配置

**目标**：导入 GitLab 官方 Dashboard，创建 RED 大盘。

```bash
# 1. 官方 Dashboard 导入
# Grafana → Dashboards → Import
# 导入以下 Dashboard ID（从 grafana.com）：

# GitLab Overview:       12973
# GitLab Rails:          15553  
# GitLab Sidekiq:        15554
# GitLab Gitaly:         15552
# PostgreSQL:             9628
# Redis:                  11835

# 2. 自定义 RED 大盘
# 创建 Panel，使用以下 PromQL 查询：

# Rate（请求速率）：
# sum(rate(gitlab_requests_total[1m]))

# Errors（错误率）：
# sum(rate(gitlab_requests_total{status=~"5.."}[1m])) /
# sum(rate(gitlab_requests_total[1m]))

# Duration（P95 延迟）：
# histogram_quantile(0.95,
#   sum(rate(gitlab_requests_duration_seconds_bucket[5m])) by (le))

# 活跃连接数：
# gitlab_active_connections
```

### 完整代码清单

- `docker-compose-monitoring.yml`：监控栈编排
- `prometheus.yml`：采集配置
- `gitlab-alerts.yml`：告警规则

### 测试验证

```bash
# 验证1：Prometheus 采集状态
curl -s http://localhost:9090/api/v1/targets | \
  python3 -c "import json,sys; [print(t['labels']['job'], t['health']) for t in json.load(sys.stdin)['data']['activeTargets']]"
# 所有 targets 应显示 "up"

# 验证2：Grafana 数据源
# 访问 http://localhost:3000 → Configuration → Data Sources
# Prometheus 应显示 "Health: Green"

# 验证3：告警规则生效
curl -s http://localhost:9090/api/v1/rules | \
  python3 -c "import json,sys; [print(g['name'], r['name']) for g in json.load(sys.stdin)['data']['groups'] for r in g['rules']]"

# 验证4：触发告警测试
# 临时停止 Puma：sudo gitlab-ctl stop puma
# 等待 5 分钟后检查 AlertManager：http://localhost:9093/#/alerts
# 应出现 GitLabHigh5xxRate 告警
```

## 4. 项目总结

### 关键监控指标速查表

| 指标类别 | 关键指标 | 告警阈值建议 | 数据来源 |
|---------|---------|-------------|---------|
| 可用性 | 5xx 比率 | > 5% (5min) | GitLab Exporter |
| 延迟 | P95 API 延迟 | > 2s (5min) | GitLab Exporter |
| 容量 | Puma 队列 | > 10 | GitLab Exporter |
| Gitaly | P99 RPC 延迟 | > 1s | Gitaly |
| Sidekiq | 队列长度 | > 1000 (持续增长) | Redis |
| 数据库 | 连接池利用率 | > 80% | PostgreSQL |
| 存储 | 磁盘使用率 | > 85% | Node Exporter |
| 内存 | 可用内存 | < 10% | Node Exporter |

### 适用场景

- **生产环境**：必须配置（基本可用性保障）
- **预发布环境**：建议配置（提前发现性能退化）
- **开发环境**：可选（用于性能测试和调优）

### 注意事项

- **Prometheus 的存储保留时间**：根据磁盘大小设置，建议至少 30 天
- **告警阈值需要根据实际流量调整**：小流量的 GitLab 可能正常的延迟也比大流量高
- **分环境采集**：如果有多套 GitLab 环境，用不同的 job_name 区分

### 常见踩坑经验

1. **指标数据为空**：Prometheus 能连接但没有任何 GitLab 指标。根因：GitLab Exporter 没有启用或端口不匹配。解决：检查 `/etc/gitlab/gitlab.rb` 中 exporter 配置，确认端口。
2. **Grafana Dashboard 显示 "No data"**：Dashboard 的变量默认值不匹配。根因：导入的 Dashboard 可能预设了 `instance` 标签过滤。解决：修改 Dashboard 变量或 Prometheus 配置中的 label 值。
3. **告警不触发**：Prometheus 规则里定义的条件从未满足。根因：表达式中的 `rate()` 函数需要 `[5m]` 区间，但测试时间不够。解决：等 5-10 分钟后再检查，或临时调低阈值测试。

### 思考题

1. GitLab 的 Sidekiq 队列突然从一个稳定的基线（100）飙升到 10000。如何通过 Prometheus 指标定位是哪种异步任务导致的？应该检查哪些指标？
2. 如果 GitLab 的 PostgreSQL 连接池利用率持续 95%+，你如何判断是连接池配置太小还是存在慢查询？Prometheus 中哪些指标能帮助区分？

> 答案见附录 D。

### 推广计划提示

- **运维**：监控是 GitLab 运维的基本功，没有监控的生产环境就是在"盲飞"
- **开发**：了解 GitLab 的指标结构后，可以在自己的应用中也采用 RED 方法设计监控
- **管理**：Grafana 大盘是向非技术人员展示平台健康度的有效方式
