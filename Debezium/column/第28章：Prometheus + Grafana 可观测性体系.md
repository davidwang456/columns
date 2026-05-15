# 第28章：Prometheus + Grafana 可观测性体系

## 1. 项目背景

"小李！实时大屏又没数据了！"——这是本周第三次了。每次都需要 SSH 到服务器 curl REST API 查状态、翻数十页日志找原因、手动执行命令尝试恢复。最要命的是，凌晨 3 点的故障没人发现——直到早上 9 点业务方上班投诉，CDC 管道已经停了 6 个小时。

没有自动化监控和告警的 CDC 管道就是一个黑盒——你永远不知道它什么时候会挂，直到有人告诉你"数据停了"。可观测性不是"锦上添花"，而是 CDC 管道从"能用"到"可信"的分水岭。本章将搭建 **Prometheus + Grafana + Alertmanager** 三件套，用 RED 方法（Rate/Errors/Duration）监控 CDC 的服务质量，用 USE 方法（Utilization/Saturation/Errors）监控系统资源。

### 痛点放大

无监控的四大灾难场景：

| 场景 | 无监控时 | 有监控时 |
|------|---------|---------|
| Connector 静默 FAILED | 业务方投诉才发现（数小时） | 15 秒内 PagerDuty 告警 |
| CDC Lag 积压至 200 万条 | 下游数据严重过期 | Lag > 60s 告警 → 5 分钟内处理 |
| 队列即将满（背压前兆） | OOM 崩溃后才知 | 队列余量 < 10% 预警 |
| Kafka Broker 磁盘将满 | Connector 被拒写才发现 | 磁盘使用率 > 80% 告警 |

## 2. 项目设计——三人对话

**小胖**："大师，我装了 Prometheus 和 Grafana，但对着几十个 JMX 指标完全不知道该看哪个。你能不能帮我列出最重要的 5 个？"

**大师**："5 个是关键，但我给你 8 个——按 P0/P1/P2 分级："

```
P0（必须告警，否则可能数据丢失）：
  1. debezium_Connected{context="streaming"} 
     值 == 0 → Connector 已断开数据库连接
  2. debezium_MilliSecondsBehindSource
     值 > 60000 → CDC 延迟超过 1 分钟
  3. kafka_connect_connector_status{state="FAILED"}
     值 > 0 → 有 Connector 处于 FAILED 状态

P1（WARNING，需要关注但不会立刻丢数据）：
  4. debezium_QueueRemainingCapacity
     值 < 1000 → 内存队列即将满
  5. debezium_SnapshotCompleted{context="snapshot"} 
     值 == false 且快照已运行 > 1h → 快照卡住了
  6. jvm_memory_used_bytes / jvm_memory_max_bytes > 0.85
     → JVM 内存使用率超过 85%

P2（INFO，趋势观察）：
  7. rate(debezium_TotalNumberOfEventsSeen[1m])
     值突然降为 0 → 数据流可能已停止
  8. debezium_NumberOfFailedEvents
     值 > 0 → 有事件在转换/投递过程中失败
```

**小白**："RED + USE 这个方法论具体怎么落地到 CDC 监控？"

**大师**："RED 是看'服务表现怎么样'——Rate（每秒处理多少事件）、Errors（失败率多少）、Duration（延迟多大）。USE 是看'资源还剩多少'——Utilization（CPU/内存/jvm 用了多少）、Saturation（队列/连接池还剩多少余量）、Errors（断连/异常次数）。"

**技术映射**：RED = 外卖配送评价（送得多快、有无撒漏、送到超时没）。USE = 外卖站点的资源监控（电动车电量剩多少、保温箱满没满、骑手有无异常请假）。

---

## 3. 项目实战

### 环境准备

```bash
# 确认 Docker Compose 环境中已添加 Prometheus 和 Grafana
# 在第 2 章的 docker-compose.yml 基础上追加：

cat >> docker-compose.yml << 'EOF'
  prometheus:
    image: prom/prometheus:v2.50.0
    container_name: prometheus
    ports: ["9090:9090"]
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml
    command:
      - '--config.file=/etc/prometheus/prometheus.yml'
  
  grafana:
    image: grafana/grafana:10.3.0
    container_name: grafana
    ports: ["3000:3000"]
    environment:
      GF_SECURITY_ADMIN_PASSWORD: admin
EOF

docker compose up -d prometheus grafana
```

### 步骤1：JMX 指标暴露——让 Connect Worker 吐出指标

```yaml
# docker-compose.yml 中 Connect Worker 增加 JMX 配置
connect:
  environment:
    KAFKA_JMX_PORT: 9999
    KAFKA_JMX_HOSTNAME: connect
    CONNECT_METRIC_REPORTERS: "io.confluent.metrics.reporter.ConfluentMetricsReporter"
    CONNECT_METRICS_ENABLE: "true"
```

```bash
# 下载 jmx_prometheus_javaagent（将 JMX 转为 HTTP /metrics）
wget https://repo1.maven.org/maven2/io/prometheus/jmx/jmx_prometheus_javaagent/0.20.0/jmx_prometheus_javaagent-0.20.0.jar -P ~/debezium-lab/

# 创建 JMX Exporter 配置
cat > ~/debezium-lab/jmx-config.yml << 'EOF'
rules:
- pattern: "debezium.connector.mysql.*<type=connector-metrics, context=(\\w+), server=(\\w+)><>(\\w+)"
  name: "debezium_$3"
  labels:
    context: "$1"
    server: "$2"
- pattern: "kafka.connect<type=connect-worker-metrics><>([^:]+)"
  name: "kafka_connect_worker_$1"
- pattern: "kafka.connect<type=connector-metrics, connector=(.+)><>(.+)"
  name: "kafka_connect_connector_$3"
  labels:
    connector: "$1"
EOF
```

### 步骤2：Prometheus 抓取配置

```yaml
# prometheus.yml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: 'kafka-connect'
    static_configs:
      - targets: ['connect:8080']
    metrics_path: /metrics
  
  - job_name: 'kafka-broker'
    static_configs:
      - targets: ['kafka:9999']
```

```bash
docker compose restart connect prometheus
sleep 20

# 验证 Prometheus 已成功抓取 Debezium 指标
curl http://localhost:9090/api/v1/query?query=debezium_Connected | python3 -m json.tool | head -20
# 预期：返回指标数据，value 为 1（表示已连接）
```

### 步骤3：Grafana 大盘 —— 核心面板定义

```json
// 导入 Grafana Dashboard JSON（关键面板）
{
  "title": "Debezium CDC Monitoring",
  "panels": [
    {
      "title": "Events/sec (Rate)",
      "targets": [{
        "expr": "rate(debezium_TotalNumberOfEventsSeen[1m])",
        "legendFormat": "{{server}}"
      }]
    },
    {
      "title": "CDC Lag (ms)",
      "targets": [{
        "expr": "debezium_MilliSecondsBehindSource",
        "legendFormat": "{{server}}"
      }],
      "alert": {
        "conditions": [{
          "evaluator": { "type": "gt", "params": [60000] }
        }]
      }
    },
    {
      "title": "Queue Remaining Capacity",
      "targets": [{
        "expr": "debezium_QueueRemainingCapacity",
        "legendFormat": "{{server}}"
      }]
    },
    {
      "title": "JVM Memory Usage (%)",
      "targets": [{
        "expr": "(jvm_memory_used_bytes / jvm_memory_max_bytes) * 100",
        "legendFormat": "{{area}}"
      }]
    },
    {
      "title": "Connector Connected Status",
      "targets": [{
        "expr": "debezium_Connected",
        "legendFormat": "{{server}}-{{context}}"
      }]
    }
  ]
}
```

### 步骤4：Prometheus 告警规则

```yaml
# prometheus-alerts.yml
groups:
  - name: debezium_cdc
    interval: 30s
    rules:
      - alert: DebeziumHighLag
        expr: debezium_MilliSecondsBehindSource > 60000
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "CDC Lag exceeds 60s"
          description: "Server {{ $labels.server }} lag is {{ $value }}ms"
      
      - alert: DebeziumDisconnected
        expr: debezium_Connected == 0
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "Connector disconnected from database"
          description: "{{ $labels.server }} is not connected"
      
      - alert: DebeziumQueueNearFull
        expr: debezium_QueueRemainingCapacity < 1000
        for: 1m
        labels:
          severity: warning
        annotations:
          summary: "Connector queue nearly full"
          description: "Only {{ $value }} slots remaining"
      
      - alert: DebeziumTaskFailed
        expr: kafka_connect_connector_status{state="FAILED"} > 0
        for: 30s
        labels:
          severity: critical
        annotations:
          summary: "Connector task FAILED"
          description: "Connector {{ $labels.connector }} is FAILED"

      - alert: DebeziumSnapshotStuck
        expr: debezium_SnapshotCompleted == 0
        for: 60m
        labels:
          severity: warning
        annotations:
          summary: "Snapshot taking > 1 hour"
```

### 步骤5：Alertmanager 通知配置

```yaml
# alertmanager.yml
route:
  receiver: 'slack-cdc-alerts'
  group_by: ['alertname']
  group_wait: 10s
  group_interval: 30s
  repeat_interval: 1h
receivers:
  - name: 'slack-cdc-alerts'
    slack_configs:
      - api_url: 'https://hooks.slack.com/services/XXX/YYY/ZZZ'
        channel: '#cdc-alerts'
        title: '{{ .GroupLabels.alertname }}'
        text: '{{ range .Alerts }}{{ .Annotations.description }}{{ end }}'
```

### 可能遇到的坑及解决方法

| 坑 | 现象 | 根因 | 解决方法 |
|----|------|------|---------|
| JMX 指标无法暴露 | Prometheus targets 显示 DOWN | jmx_prometheus_javaagent 未正确挂载 | 确认 JAR 路径和 `-javaagent` JVM 参数 |
| Grafana 面板数据为空 | 面板"No data" | PromQL 查询的指标名与 JMX 暴露的不一致 | 使用 Prometheus UI 的 autocomplete 确认正确的指标名 |
| 告警频繁抖动 | 每隔几分钟触发一次恢复-告警-恢复 | 告警阈值的 `for` 时间太短 | `for: 5m` 确保持续性异常才告警 |
| Prometheus TSDB 磁盘爆炸 | /prometheus 目录超过 50GB | 采集周期太密 + 保留时间太长 | `--storage.tsdb.retention.time=15d` 限制 15 天 |

---

## 4. 项目总结

### 优点 & 缺点

| 方案 | 优点 | 缺点 |
|------|------|------|
| Prometheus + Grafana | 开源、社区活跃、告警灵活 | 需自行部署维护 |
| Datadog | 零运维、内置 Debezium 集成 | 成本高（按 host 计费） |
| ELK Metricbeat | 与日志堆栈统一 | 指标种类有限、查询不如 PromQL 强大 |
| Debezium UI 自带 | 零配置 | 仅限于 Connector 状态，无历史趋势 |

### 适用场景

1. **生产级 CDC 管道**：7×24 监控，P1 故障 < 5 分钟内发现
2. **容量规划**：通过历史趋势预测何时需要扩容
3. **性能优化回测**：调优前后的指标对比，量化调优效果
4. **SLA 报表**：月度可用性、延迟百分位（P99/P95）自动报表
5. **多集群统一监控**：一套 Grafana 大盘监控 dev/staging/prod 所有环境

### 注意事项

- **JMX 指标名在不同 Debezium 版本间可能变化**：升级前先对比新旧版本的 JMX MBean 清单
- **Grafana Dashboard JSON 应纳入 Git 管理**：与代码同等地位
- **告警静默期**：计划维护窗口期间用 Alertmanager 的 silence 功能避免误告

### 思考题

1. 如果 Prometheus 自身挂了（进程被 kill），在 Prometheus 恢复之前 CDC 管道的故障如何被检测？设计一个不依赖 Prometheus 的独立"健康心跳"机制——在 MySQL 中每分钟 INSERT 一条心跳记录，独立进程消费 Kafka 心跳 Topic 验证延迟。

2. 如何设计一个基于 CDC 延迟的自动扩容策略——当 `MilliSecondsBehindSource > 60000` 持续 10 分钟时，自动触发 K8s HPA 扩容 Kafka Connect Worker？

**（第27章思考题答案）**

1. PITR 后数据库回到 2 小时前，但 offset 指向 2 小时后——Connector 无法从未来位点消费。恢复方案：① 如果能确定 PITR 后所有 GTID 事务 ID 都变了（因为 PITR 后的事务会重新分配 GTID），则 `snapshot.mode=when_needed` 检测到 offset 无效自动触发快照；② 如果可以计算出 PITR 后的 GTID 起始位置，通过 `database.initial.statements` 手动设置 GTID 起点跳过不存在的位点。
2. Group 协议通过 Kafka 的 Consumer Group 机制解决：新 Leader 在 Rebalance 时重新分配分区（Task），旧 Worker 的分配被撤销（revoke）。旧 Worker 恢复到 Group 后检测到自己的分区已被分配给他人，放弃旧分区。Offset 不会乱——因为只有被分配该分区的 Worker 才能提交 offset（Kafka 保证同一时刻只有一个 Consumer 能提交某分区的 offset）。

---

> **推广提示**：提供预制 Grafana Dashboard JSON 文件到团队 Git 仓库 `monitoring/grafana/` 目录，新成员一键导入。告警规则集成到 On-Call 平台（PagerDuty/OpsGenie），确保凌晨告警能找到人。Grafana 大盘投屏到 NOC 电视墙——红绿灯一眼即可判断全链路健康状态。
