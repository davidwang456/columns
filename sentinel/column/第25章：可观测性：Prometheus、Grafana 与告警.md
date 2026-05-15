# 第25章：可观测性：Prometheus、Grafana 与告警

## 1 项目背景

Dashboard 提供了实时监控，但它有两个致命弱点：一是数据只存内存（重启即丢失），二是无法做历史趋势分析和告警。运维团队每天需要手动在 Dashboard 截图、做表、写日报——这种方式在服务数量超过 5 个时就崩溃了。

更严重的是，凌晨一次限流故障中，没有人知道 Sentinel 触发限流了——因为没有告警！第二天早上客服收到大量用户投诉"下单频繁失败"，运维检查了 CPU、内存、网络一切正常，最后在 Sentinel 日志里才发现昨晚 2 点到 4 点有大量 FlowException。如果有告警机制，运维能在 2:05 就收到通知并调整阈值。

这就是**可观测性**（Observability）的三个支柱：**指标**（Metrics，知道"发生了什么"）、**告警**（Alerting，知道"什么时候需要介入"）、**日志**（Logging，知道"为什么发生"）。Sentinel 自带日志和 Dashboard 提供了基础，但生产级可观测性需要与 Prometheus + Grafana + AlertManager 这样的标准监控栈整合。

## 2 项目设计

**小胖**："Prometheus、Grafana、AlertManager...光名字就一大堆。Sentinel 怎么把指标给 Prometheus？"

**大师**："有几个方案。一是通过 Sentinel 的 metric 日志文件，用 Filebeat 或 Logstash 采集后转成 Prometheus 格式；二是用 Micrometer 桥接，自动把 Sentinel 指标导出为 Prometheus；三是用 Sentinel 官方的 Prometheus Exporter。"

**小白**："Micrometer 方案更标准，和 Spring Boot Actuator 天然集成。推荐这个。"

**大师**："对。接入 Micrometer 后，你可以通过 `/actuator/prometheus` 端点看到 Sentinel 的指标。然后用 Prometheus 抓取，Grafana 可视化，AlertManager 告警。"

**小胖**："等等，Prometheus 是"拉"模式，那如果 Sentinel 客户端太多，Prometheus 抓取不过来了怎么办？我们有 200 个微服务实例。"

**大师**："好问题。有两个方案：一是用 Prometheus Federation（联邦），上层 Prometheus 从下层聚合抓取；二是用 Grafana Agent 或 Telegraf 做采集代理，由代理统一推送到 Prometheus Remote Write。200 个实例还不算多，一台 Prometheus 服务器（4C8G）抓取间隔 15 秒可以轻松应对 1000 个 target。"

**小白**："那 Prometheus 的 Gauge 和 Counter 怎么选？Sentinel 的 passQps 是瞬时值，不是累计值。"

**大师**："精确来说，Sentinel 的 QPS 是"过去 1 秒通过数"——既不是真正的瞬时值，也不是累计值。用 Gauge 是对的，因为 Counter 只增不减，而 Sentinel 的 QPS 会上下波动。但如果要算"每秒限流次数"，可以用 `rate(sentinel_resource_block_qps[1m])`——Prometheus 的 `rate()` 函数会把 Gauge 当 Counter 算增量。"

**小胖**："告警这块，AlertManager 能直接发钉钉吗？我们团队都在钉钉上。"

**大师**："AlertManager 原生只支持 Email、Webhook、PagerDuty 等渠道。发钉钉需要中间层——你可以用 `alertmanager-webhook-xx`（community project）做 Webhook → 钉钉的转换，也可以用 PrometheusAlert 这个开源工具，一站式转发到钉钉、企业微信、飞书。"

**小白**："还有一个场景：同一个资源在不同服务的限流阈值不同，告警规则怎么写？比如 createOrder 在 order-service 阈值是 500，在 batch-order-service 阈值是 2000。"

**大师**："两种方式。一是给每个服务写独立规则——这个最清晰但维护成本高。二是用 recording rule 先把各服务的阈值写到指标里，然后在告警规则里做除法比较。更优雅的做法是用 Prometheus Rule 的 `label_replace` 从 Nacos 配置指标中动态获取阈值——但这需要 Sentinel 把规则配置也暴露为 Prometheus 指标。"

## 3 项目实战

### 3.1 环境准备

```xml
<dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-actuator</artifactId>
</dependency>
<dependency>
    <groupId>io.micrometer</groupId>
    <artifactId>micrometer-registry-prometheus</artifactId>
</dependency>
```

`application.yml`：

```yaml
management:
  endpoints:
    web:
      exposure:
        include: health,info,prometheus
  metrics:
    export:
      prometheus:
        enabled: true
    tags:
      application: ${spring.application.name}
```

### 3.2 分步实现

**步骤一：Sentinel 指标对接 Micrometer**

```java
@Configuration
public class SentinelMetricsConfig {

    @Bean
    public MeterRegistryCustomizer<MeterRegistry> sentinelMetrics() {
        return registry -> {
            // 注册 Sentinel 指标到 Micrometer
            SentinelMetricsRegistry.register(registry);
        };
    }
}

// 自定义 SentinelMetricsRegistry（简化版）
public class SentinelMetricsRegistry {

    private static final MeterRegistry registry = null;

    public static void register(MeterRegistry reg) {
        // 每 5 秒采集一次 Sentinel 的内部指标
        Executors.newSingleThreadScheduledExecutor()
            .scheduleAtFixedRate(() -> collectSentinelMetrics(reg), 0, 5, TimeUnit.SECONDS);
    }

    private static void collectSentinelMetrics(MeterRegistry reg) {
        // 遍历所有 ClusterNode，上报指标
        Map<ResourceWrapper, ClusterNode> nodeMap =
            ClusterBuilderSlot.getClusterNodeMap();

        for (Map.Entry<ResourceWrapper, ClusterNode> entry : nodeMap.entrySet()) {
            String resource = entry.getKey().getName();
            ClusterNode node = entry.getValue();

            // 通过 QPS
            Gauge.builder("sentinel_resource_pass_qps", node, ClusterNode::passQps)
                .tag("resource", resource)
                .register(reg);

            // 拒绝 QPS
            Gauge.builder("sentinel_resource_block_qps", node, ClusterNode::blockQps)
                .tag("resource", resource)
                .register(reg);

            // 平均 RT
            Gauge.builder("sentinel_resource_avg_rt", node, ClusterNode::avgRt)
                .tag("resource", resource)
                .register(reg);

            // 当前线程数
            Gauge.builder("sentinel_resource_threads", node, ClusterNode::curThreadNum)
                .tag("resource", resource)
                .register(reg);
        }
    }
}
```

**步骤二：Prometheus 配置**

`prometheus.yml`：

```yaml
global:
  scrape_interval: 5s

scrape_configs:
  - job_name: 'order-service'
    metrics_path: '/actuator/prometheus'
    static_configs:
      - targets: ['order-service:8090']
        labels:
          service: 'order-service'
  - job_name: 'inventory-service'
    metrics_path: '/actuator/prometheus'
    static_configs:
      - targets: ['inventory-service:8082']

  - job_name: 'sentinel-dashboard'
    metrics_path: '/actuator/prometheus'
    static_configs:
      - targets: ['sentinel-dashboard:8080']
```

Docker Compose 启动 Prometheus：

```yaml
prometheus:
  image: prom/prometheus:latest
  ports:
    - "9090:9090"
  volumes:
    - ./prometheus.yml:/etc/prometheus/prometheus.yml
```

**步骤三：Grafana 面板设计**

Grafana 面板 JSON 配置（关键 queries）：

```
Panel 1: Sentinel 资源 QPS 总览 (Time Series)
  Query: sum by(resource) (sentinel_resource_pass_qps{service="order-service"})

Panel 2: Sentinel 拒绝 QPS (Time Series)
  Query: sum by(resource) (sentinel_resource_block_qps{service="order-service"})

Panel 3: Sentinel RT 百分位 (Time Series)
  Query: sentinel_resource_avg_rt{resource="createOrder"}

Panel 4: 熔断状态 (Status Panel)
  Query: sentinel_degrade_open{resource="queryStock"}

Panel 5: 热点参数限流触发次数 (Stat)
  Query: rate(sentinel_param_flow_block_total[5m])
```

**步骤四：告警规则配置**

`alertmanager-rules.yml`：

```yaml
groups:
  - name: sentinel_alerts
    rules:
      # 流控拒绝率突增
      - alert: HighBlockRate
        expr: |
          rate(sentinel_resource_block_qps[1m]) /
          (rate(sentinel_resource_pass_qps[1m]) + rate(sentinel_resource_block_qps[1m]))
          > 0.5
        for: 2m
        labels:
          severity: warning
        annotations:
          summary: "资源 {{ $labels.resource }} 限流拒绝率超过 50%"
          description: "当前拒绝率 {{ $value | humanizePercentage }}，请检查阈值配置"

      # 熔断持续打开
      - alert: CircuitBreakerOpen
        expr: sentinel_degrade_open == 1
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "资源 {{ $labels.resource }} 熔断持续打开超过 5 分钟"
          description: "可能下游服务故障未自动恢复，需人工介入"

      # P99 RT 异常升高
      - alert: HighResponseTime
        expr: sentinel_resource_avg_rt > 500
        for: 3m
        labels:
          severity: warning
        annotations:
          summary: "资源 {{ $labels.resource }} 平均 RT 超过 500ms"

      # 规则异常变更（通过比较 Nacos 配置版本检测）
      - alert: RuleChanged
        expr: delta(sentinel_rule_version[1m]) > 0
        labels:
          severity: info
        annotations:
          summary: "Sentinel 规则发生变更，请关注"
```

**步骤五：验证**

```bash
# 1. 检查 Prometheus 指标
curl http://localhost:8090/actuator/prometheus | grep sentinel

# 期望输出：
# sentinel_resource_pass_qps{resource="createOrder",} 25.0
# sentinel_resource_block_qps{resource="createOrder",} 3.0
# sentinel_resource_avg_rt{resource="createOrder",} 45.0

# 2. 登录 Grafana (http://localhost:3000)，导入面板
# 3. 用 JMeter 压测并观察面板实时变化
# 4. 触发限流 → 等待 2 分钟 → 收到 AlertManager 告警
```

**步骤六：Grafana 告警通道（替代 AlertManager）**

Grafana 8+ 内置告警引擎，可以在面板上直接配告警：

```yaml
# Grafana Alert Rule (在 Grafana UI 或 provisioning 中配置)
alertRules:
  - uid: sentinel-high-block
    title: Sentinel 限流拒绝率过高
    condition: C
    data:
      - refId: A
        queryType: range
        relativeTimeRange: { from: 600, to: 0 }
        datasourceUid: prometheus
        model:
          expr: |
            rate(sentinel_resource_block_qps{service="order-service"}[2m]) /
            (rate(sentinel_resource_pass_qps{service="order-service"}[2m]) + 
             rate(sentinel_resource_block_qps{service="order-service"}[2m])) > 0.3
    noDataState: NoData
    execErrState: Error
    for: 2m
    annotations:
      summary: "Sentinel 限流拒绝率超过 30%"
    labels:
      severity: critical
```

**步骤七：AlertManager 多通道告警（钉钉 + 邮件）**

`alertmanager.yml` 添加多渠道配置：

```yaml
route:
  receiver: 'default'
  group_by: ['alertname', 'service']
  group_wait: 10s
  group_interval: 10s
  repeat_interval: 1h
  routes:
    - match:
        severity: critical
      receiver: 'dingtalk-critical'
      continue: true
    - match:
        severity: warning
      receiver: 'dingtalk-warning'

receivers:
  - name: 'dingtalk-critical'
    webhook_configs:
      - url: 'http://prometheus-webhook-dingtalk:8060/dingtalk/critical/send'
        send_resolved: true
  - name: 'dingtalk-warning'
    webhook_configs:
      - url: 'http://prometheus-webhook-dingtalk:8060/dingtalk/warning/send'
  - name: 'default'
    email_configs:
      - to: 'sre@example.com'
```

**步骤八：Prometheus Recording Rules 降低查询复杂度**

```yaml
# recording-rules.yml
groups:
  - name: sentinel_precompute
    interval: 30s
    rules:
      # 预计算限流拒绝率，避免 Grafana 每次实时计算
      - record: sentinel:block_rate:ratio
        expr: |
          rate(sentinel_resource_block_qps[2m]) /
          (rate(sentinel_resource_pass_qps[2m]) + rate(sentinel_resource_block_qps[2m]))
      
      # 预计算 P99 RT（近似）
      - record: sentinel:avg_rt:p99_approx
        expr: sentinel_resource_avg_rt * 1.5
      
      # 预计算全局总 QPS
      - record: sentinel:pass_qps:total
        expr: sum(sentinel_resource_pass_qps)
```

**步骤九：生产级 Grafana Dashboard 设计（多维度）**

完整的生产级 Dashboard 应包括 6 个 Row：

```
Row 1: 全局总览 (Service Overview)
  - 所有服务总通过 QPS (Stat)
  - 所有服务总拒绝率 (Gauge)
  - 当前生效规则数 (Table)

Row 2: 各服务 QPS 排行 (Service Ranking)
  - Top 10 资源通过 QPS (Bar Gauge)
  - Top 10 资源拒绝率 (Table, 红色高亮 > 10%)

Row 3: 单服务明细 (Per-Service Detail)
  - 变量选择器: $service, $resource
  - 通过 QPS vs 拒绝 QPS (Time Series, 双 Y 轴)
  - 平均 RT (Time Series)
  - 线程数 (Time Series)

Row 4: 熔断状态 (Circuit Breaker Status)
  - 当前打开/半开/关闭的熔断器 (State Timeline)
  - 熔断打开时长 (Stat, 告警阈值 > 5min)

Row 5: 系统保护 (System Protection)
  - CPU 使用率 vs 系统保护阈值 (Time Series)
  - 系统 Load (Time Series)

Row 6: 规则变更追踪 (Rule Change Tracker)
  - 规则版本号变更时间线 (Time Series, step=1min)
  - 最近 1 小时变更次数 (Stat)
```

**步骤十：指标高基数治理**

```java
// 避免高基数标签：用 Top N + Other 聚合模式
@Component
public class SentinelMetricFilter {

    private static final Set<String> LOW_TRAFFIC_RESOURCES = 
        ConcurrentHashMap.newKeySet();

    @Scheduled(fixedRate = 60000)
    public void cleanLowTrafficResources() {
        // 清理 5 分钟内无流量资源的指标
        Map<ResourceWrapper, ClusterNode> nodes = 
            ClusterBuilderSlot.getClusterNodeMap();
        for (Map.Entry<ResourceWrapper, ClusterNode> e : nodes.entrySet()) {
            if (e.getValue().passQps() < 0.01 &&
                e.getValue().blockQps() < 0.01 &&
                e.getValue().completeQps() < 0.01) {
                LOW_TRAFFIC_RESOURCES.add(e.getKey().getName());
            }
        }
    }
}
```

**踩坑记录**：

1. **Micrometer 指标延迟**：`Gauge` 类型的指标在 Prometheus 中被"拉取"时才计算，如果 Sentinel 的 ClusterNode 数据已更新，Grafana 需要等下一个 scrape_interval 才能看到。
2. **高基数标签风险**：不要把 `resource` 和 `userId` 同时作为标签——会形成海量时间序列（标签值的乘积）。Prometheus 不喜欢高基数。
3. **Dashboard 与 Grafana 数据不一致**：Dashboard 和 Grafana 的采集间隔不同可能导致数值有细微差异（1-5 秒延迟）。
4. **Prometheus 存储压力**：每个 Sentinel 资源产生约 6-8 个时间序列。100 个资源、10 个 Pod = 8000 个时间序列。按 15 秒抓取间隔，每天约产生 1.5GB 数据。建议保留 15 天，配 `--storage.tsdb.retention.time=15d`。
5. **告警风暴**：AlertManager 的 `group_by` 配置不当会导致 1 条规则变更触发 200 条告警。务必按 `alertname` 和 `service` 分组，设置 `group_wait: 10s` 聚合窗口。

## 4 项目总结

### 4.1 监控栈对比

| 方案 | 成本 | 适用规模 | 实时性 | 告警能力 | 历史趋势 | 多租户 |
|------|------|---------|-------|---------|---------|-------|
| Dashboard 自带 | 零 | 单服务 | 秒级 | 无 | 无 | 无 |
| Prometheus + Grafana | 低 | 50-500 个服务 | 5-15 秒 | 强（AlertManager） | 15-30 天 | 需自行隔离 |
| 商业 APM（Datadog） | 高 | 无限 | 秒级 | 强 | 按需保留 | 原生支持 |
| ELK + Sentinel | 中 | 50-200 个服务 | 分钟级 | 中（Watcher） | 按需保留 | 原生支持 |
| Grafana Loki + Sentinel | 低 | 200-1000 个服务 | 近实时 | 中（Grafana Alert） | 按需保留 | 租户 ID 标签 |

### 4.2 告警分级与响应 SLA

| 级别 | 条件示例 | 响应时间 | 通知渠道 | 升级策略 |
|------|---------|---------|---------|---------|
| **P0 - 紧急** | 熔断打开 > 10min / 拒绝率 > 80% | 5 分钟 | 电话 + 钉钉 + 短信 | 10 分钟无响应 → 升级到 CTO |
| **P1 - 严重** | 拒绝率 > 50% / P99 RT > 2s | 15 分钟 | 钉钉 @all + 邮件 | 30 分钟无响应 → 升级到 Tech Lead |
| **P2 - 警告** | 拒绝率 > 10% / 规则变更 | 30 分钟 | 钉钉群通知 | 1 小时无响应 → 升级到值班 SRE |
| **P3 - 信息** | 系统保护触发 / 新版本规则加载 | 1 小时 | 钉钉群静默通知 | 无需升级 |

### 4.3 Grafana 面板设计最佳实践清单

- [ ] Row 1: 全局总览（总 QPS、拒绝率、规则数）—— 一眼看清整体健康度
- [ ] Row 2: 各服务排行（Top N QPS / 拒绝率）—— 快速定位问题服务
- [ ] Row 3: 单服务明细（带变量选择器）—— 深入分析单个服务
- [ ] Row 4: 熔断器状态时间线 —— 跟踪熔断恢复过程
- [ ] Row 5: 系统保护与资源 —— CPU / Load / Memory
- [ ] 使用 Recording Rules 预计算复杂查询（减轻 Grafana 负载）
- [ ] 面板告警使用 `for: 2m` 避免瞬时抖动误报
- [ ] 所有面板添加 `$datasource` 变量支持多数据源切换

### 4.4 常见故障排查

| 现象 | 可能原因 | 排查步骤 |
|------|---------|---------|
| Grafana 不显示 Sentinel 指标 | Prometheus 未采集到 | 1) `curl /actuator/prometheus \| grep sentinel` 检查端点 2) Prometheus Targets 页面检查 scrape 状态 |
| 指标值始终为 0 | Sentinel ClusterNode 未初始化 | 发一次真实请求触发资源初始化 |
| 告警不触发 | 告警规则阈值不匹配 | Prometheus Alerts 页面检查规则状态，看`active/pending/firing` |
| 钉钉收不到告警 | Webhook URL 错误或网络不通 | 手动 curl webhook URL 发送测试消息 |
| Grafana 查询超时 | 时间序列过多/时间范围过大 | 缩小时间范围或使用 Recording Rules 预计算 |

### 4.5 思考题

1. 为什么 Prometheus 的 `Gauge` 适合 Sentinel 指标，而 `Counter` 不太适合？Sentinel 的 QPS 是瞬时值还是累计值？
2. 如果 `sentinel_resource_block_qps` 突然飙升但 `pass_qps` 正常，可能是什么原因？设计一条对应的告警规则。

### 4.6 推广计划

- **运维/SRE**：部署 Prometheus + Grafana 栈，导入 Sentinel 面板，配置关键告警规则。
- **开发团队**：在 Grafana 中查看自己服务的 Sentinel 指标，理解正常运行态。
- **测试团队**：利用 Grafana 面板观察压测过程中的 Sentinel 指标变化，作为压测报告的数据来源。
