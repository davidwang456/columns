# 第40章：【高级篇综合实战】企业级可观测性平台——自研API网关的监控体系

> 全书终章，综合运用第1~39章全部知识，从零构建一套金融级可观测性平台。

---

## 一、项目背景

"融易付"是一家持牌金融科技公司，自研了一套高性能API网关，承载着所有支付、转账、风控请求的入口流量，日均调用量高达**100亿次**。网关直接面对三个硬指标：**低延迟（P99 < 5ms）**、**高可用（99.99%）**、**可追溯（每笔交易可追踪）**。

现有的监控方案——Prometheus + Node Exporter + 基础告警——只能看到网关节点的CPU/内存/网络等系统级指标。以下关键问题完全不可见：

- 每个API端点的QPS、延迟分布、错误率是多少？
- 网关内部熔断器何时切换状态？限流器触发了多少次？
- 某一笔支付失败的根因是下游超时、风控拒绝、还是自身限流？
- 一条TraceID能否串联指标、日志、调用链，实现"一键溯源"？

本章的任务：为融易付设计并落地一套完整的可观测性平台，技术栈为 **自研Exporter → Prometheus Operator on K8s → VictoriaMetrics长期存储 → Thanos全局查询（可选） → Grafana SLO大屏 → Prometheus + Loki + Tempo三柱联动**。这不仅是监控，而是从"发现问题"到"定位根因"的全链路闭环。

---

## 二、剧本式交锋对话

**小胖**（兴奋地搓手）："大师，咱们网关日均100亿请求，光Prometheus干撸肯定扛不住。我琢磨着是不是得搞一套'全家桶'：自研Exporter采网关内部指标，VictoriaMetrics做长期存，Grafana画SLO大屏，Loki+Tempo还能联动查日志和trace？"

**大师**（抿了口茶）："方向对，但坑在细节里。先说你自研Exporter，准备采哪些指标？"

**小胖**："QPS用Counter、延迟用Histogram、熔断器状态用Gauge、限流触发用Counter。对了，我还想给每个指标打上TraceID标签，这样就能从指标面板直接跳到trace了！"

**小白**（突然插嘴）："等等，TraceID当标签？每天100亿个唯一TraceID，Prometheus的series基数直接炸穿内存啊！这在第34章'高基数治理'里专门讲过，你是不是忘了？"

**大师**（赞许地点头）："小白学扎实。小胖，TraceID不能做label，但可以做**Exemplar**。Exemplar的概念在第19章TSDB原理和第34章都提过——它是挂在Metric Sample上的'注释'（annotation），不参与Series标识，不增加基数。你在Counter.Inc()或Histogram.Observe()时，把当前请求的TraceID作为Exemplar注入进去，Grafana面板上就能看到'这个异常延迟值对应的TraceID是xxx'，点击即可跳转到Tempo。"

**小胖**（恍然大悟）："所以Exemplar就像指标的'便签条'，贴在sample上但不改变series identity！"

**大师**："没错。继续——远程写入那块，你为什么选VictoriaMetrics而不是直接用Thanos？第26章我们对比过两者。"

**小胖**："VM写入快啊。咱日均1500亿个samples，按15秒采集间隔，写入峰值约350万samples/s。VM的vminsert组件水平扩展就能吃下这个量，压缩比也高（每个sample约0.4字节）。Thanos依赖对象存储，写入路径长，延迟高。"

**大师**："那Thanos就没用了？"

**小胖**："也不全是。咱们多地机房，每个机房一套Prometheus + VM。如果需要做全局聚合查询（比如查'全公司所有网关的P99延迟'），Thanos Querier可以跨集群汇总。这就是第27章讲的'Thanos全局视图'。所以是**双轨制**：VM做本地高性能存储，Thanos做跨集群联邦查询。"

**小白**："SLO这一块，99.99%可用性意味着一个月只能宕机26秒。这告警得多灵敏？"

**大师**："问得好。用第37章的多窗口Burn Rate告警：短期窗口（1h）检测快速燃烧，长期窗口（30d）防止预算耗尽。当短窗口Burn Rate超过100倍时，意味着如果持续下去，几小时内就会烧光月度预算——此时必须立即拉群、暂停发布、启动应急。这是SLO告警和普通阈值告警的根本区别：SLO告警关注的是**预算消耗速率**，而不是绝对值。"

**小胖**："还有一个问题——三柱联动怎么配？Grafana里Metric面板点一下跳Tempo看trace，trace里再跳Loki看日志？"

**大师**："对，核心是**统一TraceID格式**。网关在请求入口生成一个TraceID（W3C格式，32位hex），分别注入到：①Prometheus的Exemplar（指标→追踪）、②Tempo的SpanContext（追踪）、③Loki日志的structured metadata（日志→追踪）。然后在Grafana中配好Data Link：指标面板的field override里设置 `TraceID` 字段链接到Tempo的TraceQL查询，Tempo面板再配置到Loki的Label查询。第36章讲Loki时我们演示过类似配置。"

**小胖**（合上笔记本）："全串起来了！自研Exporter → Exemplar防高基数 → Remote Write到VM → Thanos跨集群 → Grafana三柱联动 → SLO Burn Rate告警，40章的知识一条线全用上了。"

---

## 三、项目实战

### 环境准备

```bash
# K8s集群（1.28+），安装核心组件
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm install kps prometheus-community/kube-prometheus-stack \
  --set prometheus.prometheusSpec.enableFeatures={exemplar-storage} \
  --set prometheus.prometheusSpec.remoteWrite[0].url=http://vminsert:8480/insert/0/prometheus

# VictoriaMetrics集群
# vmstorage × 6, vminsert × 3, vmselect × 2（生产配置见容量规划）
helm install vm vm/victoria-metrics-cluster

# Grafana + Loki + Tempo（通过Grafana Agent或OTel Collector统一采集）
```

### 步骤1：自研API Gateway Exporter设计

自研Exporter是整套体系的**数据源头**，设计的核心原则：**宁可多埋点，不可少维度；但cardinality必须严格控制**。

```go
package metrics

import "github.com/prometheus/client_golang/prometheus"

var (
    // 指标1：请求总量（Counter，只增不减）
    // 维度：api路径、HTTP方法、状态码、客户端等级
    // 注意：不放trace_id、request_id等唯一标识！
    GatewayRequestsTotal = prometheus.NewCounterVec(
        prometheus.CounterOpts{
            Name: "gateway_requests_total",
            Help: "Total number of API gateway requests",
        },
        []string{"api", "method", "status_code", "tier"},
    )

    // 指标2：请求延迟分布（Histogram，可聚合）
    // Buckets设计：覆盖P50(1ms)到P99(5ms)到P999(50ms)
    GatewayRequestDuration = prometheus.NewHistogramVec(
        prometheus.HistogramOpts{
            Name:    "gateway_request_duration_seconds",
            Help:    "Request duration distribution in seconds",
            Buckets: []float64{0.001, 0.002, 0.003, 0.005, 0.01, 0.02, 0.05, 0.1, 0.5, 1},
        },
        []string{"api", "method"},
    )

    // 指标3：熔断器状态（Gauge，可上可下）
    // state值：0=closed, 1=half_open, 2=open
    GatewayCircuitBreakerState = prometheus.NewGaugeVec(
        prometheus.GaugeOpts{
            Name: "gateway_circuit_breaker_state",
            Help: "Circuit breaker state per API (0=closed, 1=half_open, 2=open)",
        },
        []string{"api", "state"},
    )

    // 指标4：限流器触发次数（Counter）
    // limit_type: token_bucket / sliding_window / concurrent_limit
    GatewayRateLimitHits = prometheus.NewCounterVec(
        prometheus.CounterOpts{
            Name: "gateway_rate_limit_hits_total",
            Help: "Total number of rate limit hits",
        },
        []string{"api", "limit_type"},
    )

    // 指标5：下游依赖健康度（Gauge）
    GatewayDownstreamHealth = prometheus.NewGaugeVec(
        prometheus.GaugeOpts{
            Name: "gateway_downstream_health",
            Help: "Downstream service health status (1=healthy, 0=unhealthy)",
        },
        []string{"service_name", "endpoint"},
    )
)
```

**为什么不用TraceID做Label？**

每天100亿请求 × 每个请求一个唯一的TraceID = **每天100亿个唯一Label组合**。即使Prometheus的TSDB在理论上可以处理数百万Series，100亿也远超上限。解决方式：使用**Exemplar**——将TraceID作为指标的"注释"附在Sample上，不参与Series Identity，不增加基数。

```go
// middleware/metrics.go：在网关中间件中记录指标并注入Exemplar
func MetricsMiddleware(next http.Handler) http.Handler {
    return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        api := r.URL.Path
        method := r.Method
        traceID := r.Header.Get("X-Trace-Id") // 从请求头获取TraceID

        // 计时
        timer := prometheus.NewTimer(
            GatewayRequestDuration.WithLabelValues(api, method),
        )
        defer timer.ObserveDuration()

        // 记录请求计数
        rw := &responseWriter{ResponseWriter: w}
        next.ServeHTTP(rw, r)

        statusCode := strconv.Itoa(rw.statusCode)
        tier := r.Header.Get("X-Client-Tier")

        // 使用ExemplarAdder接口注入TraceID（需要prometheus client_golang v1.12+）
        counter, _ := GatewayRequestsTotal.GetMetricWithLabelValues(api, method, statusCode, tier)
        if adder, ok := counter.(prometheus.ExemplarAdder); ok {
            adder.AddWithExemplar(1, prometheus.Labels{"trace_id": traceID})
        } else {
            counter.Inc()
        }
    })
}
```

> **注意**：Prometheus需要开启 `--enable-feature=exemplar-storage` 才能存储Exemplar。Exemplar的保留时间有限（默认约24小时），仅用于近期的指标↔追踪关联。

### 步骤2：部署架构设计

```
                          ┌──────────────────────────────────────┐
                          │           Kubernetes Cluster           │
                          │                                        │
  ┌───────────────┐       │  ┌─────────────────────────────────┐  │
  │ API Gateway   │──/metrics─→│  Prometheus Operator (Stateful)  │  │
  │ (Deployment)  │       │  │  ┌─────┐ ┌─────┐ ┌─────┐        │  │
  │  · 中间件埋点  │       │  │  │ Pod │ │ Pod │ │ Pod │        │  │
  │  · Exemplar   │       │  │  └──┬──┘ └──┬──┘ └──┬──┘        │  │
  └───────┬───────┘       │  │     │Remote Write (Snappy)       │  │
          │               │  └─────┼───────────────┼────────────┘  │
          │               │        │               │               │
  ┌───────▼───────┐       │  ┌─────▼───────┐ ┌─────▼───────┐      │
  │ OTel Collector│       │  │  vminsert   │ │  vminsert   │      │
  │ (Trace+Log)   │       │  │  (×3 副本)  │ │  (×3 副本)  │      │
  └───┬───────┬───┘       │  └─────┬───────┘ └─────┬───────┘      │
      │       │           │        │               │               │
      ▼       ▼           │  ┌─────▼───────────────▼───────┐      │
   Tempo   Loki           │  │       vmstorage (×6)        │      │
                          │  │  每个节点12TB SSD, 副本×2   │      │
                          │  └─────────────┬───────────────┘      │
                          │                │                       │
                          │  ┌─────────────▼───────────────┐      │
                          │  │     vmselect (×2)           │      │
                          │  └─────────────┬───────────────┘      │
                          │                │                       │
                          └────────────────┼───────────────────────┘
                                           │
                                    ┌──────▼──────┐
                                    │   Grafana   │
                                    │  /   |   \  │
                                    │ VM  Loki Tempo│
                                    └─────────────┘
```

**关键技术选型决策表：**

| 决策点 | 选项A | 选项B | 最终选择 | 理由 |
|--------|-------|-------|----------|------|
| K8s内采集 | vmagent | Prometheus Operator | **Prometheus Operator** | 更好的K8s服务发现（PodMonitor/ServiceMonitor），内置Rule管理，社区成熟 |
| 长期存储 | Thanos | VictoriaMetrics | **VictoriaMetrics** | 写入性能高（百万samples/s），压缩比好（0.4B/sample），运维简单 |
| 全局查询 | 仅VM | VM + Thanos | **双轨制** | 单集群用VM；多集群加Thanos Querier实现联邦查询 |
| 日志方案 | ELK | Loki | **Loki** | 与Prometheus标签体系一致，低成本对象存储，与Grafana原生集成 |
| 追踪方案 | Jaeger | Tempo | **Tempo** | 对象存储低成本，与Grafana无缝集成，无需独立UI |
| 可视化 | 自研 | Grafana | **Grafana** | 三柱联动唯一选择，成熟的Dashboard生态 |

### 步骤3：SLO监控体系

为API Gateway定义**3个核心SLO**（遵循第37章的SLO最佳实践：不宜过多，聚焦关键）：

**SLO 1：可用性（Availability）≥ 99.99%**

```promql
# SLI：成功请求数（非5xx）占总请求的比例
sum(rate(gateway_requests_total{status_code!~"5.."}[5m]))
  /
sum(rate(gateway_requests_total[5m]))
```

**SLO 2：P99延迟（Latency）< 5ms**

```promql
# SLI：延迟低于5ms的请求占比
sum(rate(gateway_request_duration_seconds_bucket{le="0.005"}[5m]))
  /
sum(rate(gateway_request_duration_seconds_count[5m]))
```

**SLO 3：错误预算燃尽告警（第37章多窗口Burn Rate）**

99.99%可用性意味着**每月只允许26秒故障时间**。1分钟的故障就会烧掉2个月的预算，告警必须极其灵敏。

```yaml
# prometheus-rules/gateway-slo.yaml
groups:
  - name: gateway_slo
    rules:
      # 短期窗口：Burn Rate > 100x → 数小时内耗尽预算
      - alert: GatewayErrorBudgetBurnCritical
        expr: |
          (
            (1 - sum(rate(gateway_requests_total{status_code!~"5.."}[1h]))
              / sum(rate(gateway_requests_total[1h])))
            / 0.0001   # 错误预算比例 = (1 - 0.9999)
          ) > 100
        for: 1m
        labels:
          severity: critical
          slo: "availability-99.99"
        annotations:
          summary: "API Gateway错误预算极速燃烧，短期Burn Rate > 100x"
          description: "当前1小时内错误预算消耗速率是正常速率的{{ $value }}倍，预计数小时内耗尽月度预算。请立即停止发布并排查。"
      
      # 长期窗口：Burn Rate > 3x → 预算消耗加速，需关注
      - alert: GatewayErrorBudgetBurnWarning
        expr: |
          (
            (1 - sum(rate(gateway_requests_total{status_code!~"5.."}[6h]))
              / sum(rate(gateway_requests_total[6h])))
            / 0.0001
          ) > 3
        for: 10m
        labels:
          severity: warning
          slo: "availability-99.99"
        annotations:
          summary: "API Gateway错误预算消耗加速，Burn Rate > 3x"

      # 熔断器打开告警——保护机制触发
      - alert: GatewayCircuitBreakerOpen
        expr: gateway_circuit_breaker_state{state="open"} == 2
        for: 30s
        labels:
          severity: critical
        annotations:
          summary: "API网关熔断器打开，API {{ $labels.api }} 已自动熔断"

      # 错误预算耗尽 → 强制暂停发布
      - alert: GatewayErrorBudgetExhausted
        expr: |
          (
            1 - sum(rate(gateway_requests_total{status_code!~"5.."}[30d]))
              / sum(rate(gateway_requests_total[30d]))
          ) > 0.0001
        labels:
          severity: critical
          action: "freeze_deploy"
        annotations:
          summary: "API Gateway月度错误预算已耗尽，暂停所有发布和变更操作"
```

### 步骤4：Grafana三柱联动

**场景演示**：Grafana面板上发现 `POST /api/payment` 的P99延迟从3ms飙升到500ms。

1. **指标 → Tempo**：在Grafana的Metric面板配置Data Link，点击异常时间点的数据，自动跳转到Tempo的TraceQL查询：

```
/explore?left={"datasource":"Tempo","queries":[{"refId":"A","queryType":"traceql","query":"{resource.service.name=\"api-gateway\" && span.http.route=\"/api/payment\"}"}],"range":{"from":"$__from","to":"$__to"}}
```

2. **Tempo → 根因定位**：在火焰图中找到耗时最长的Span——"风控检查"调用耗时480ms，远超正常的2ms。

3. **Tempo → Loki**：点击Span中的TraceID，配置Data Link跳转Loki，查看该TraceID关联的日志上下文：

```
/explore?left={"datasource":"Loki","queries":[{"refId":"A","expr":"{service=\"api-gateway\"} |= \"$__value\""}]}
```

4. **日志 → 根因确认**：日志显示 `Redis connection timeout to risk-control-cluster: read timeout 500ms` → **根因是Redis慢查询导致风控服务响应超时**。

**配置要点**：
- 所有组件使用统一TraceID格式（W3C TraceContext: `00-trace-id-span-id-01`）
- Prometheus需开启 `--enable-feature=exemplar-storage`
- Grafana数据源配置中确保Tempo和Loki的TraceID字段映射一致
- Metric面板的Data Link使用 `${__data.fields.trace_id}` 传递Exemplar中的TraceID

### 步骤5：容量规划

**数据量估算**（日均100亿API调用）：

| 项目 | 计算过程 | 数值 |
|------|----------|------|
| 每个请求的Metric Samples | QPS (1) + 延迟Buckets (9) + 状态码 (1) + 其他 (4) | ~15 samples/request |
| 每日总Samples | 100亿 × 15 | 1500亿 samples |
| Remote Write速率（平均） | 1500亿 ÷ 86400s | 173万 samples/s |
| Remote Write速率（峰值×2） | 173万 × 2 | 约350万 samples/s |
| Active Series（2小时内） | 100个API × 3个状态码 × 3个tier ≈ 900 | ~900 series |
| 长期存储（3年，VM压缩） | 1500亿 × 0.4B × 365天 × 3年 | 约65 TB |

**硬件建议**：

| 组件 | 数量 | 配置 | 用途 |
|------|------|------|------|
| vmstorage | 6节点 | 16C/64G/12TB SSD × 2(replica) | 存储层，共72TB可用（含副本） |
| vminsert | 3节点 | 8C/32G | 写入层，每节点支持100万samples/s |
| vmselect | 2节点 | 8C/32G | 查询层，负载均衡 |
| Prometheus | 2副本 | 4C/16G/100GB SSD | K8s内采集，保留2h数据 |

**常见踩坑经验**：

1. **Exemplar未开启导致TraceID关联失败**：Prometheus默认不开启Exemplar Storage，必须显式添加 `--enable-feature=exemplar-storage`。忘记开启会导致Grafana面板上看不到TraceID字段，三柱联动断裂。

2. **vmstorage单节点故障因未配置副本导致数据丢失**：VictoriaMetrics集群的 `-replicationFactor` 默认值为1，意味着每个时间序列只存储在一个vmstorage节点上。生产环境必须设置为2或以上，否则一个节点故障就会丢失数据。

3. **SLO告警和普通阈值告警互相冲突**：例如普通告警设置"P99延迟>10ms告警"，而SLO目标也是P99<5ms。两者阈值不一致会导致告警风暴和优先级混乱。解决方案：以SLO为核心，普通阈值告警作为SLO的补充（预警告警），统一告警等级体系。

---

## 四、项目总结

### 全平台架构总览

```
用户请求 → [API Gateway (自研Exporter埋点)]
               │                 │                  │
        指标(Metrics)       追踪(Tracing)       日志(Logging)
               │                 │                  │
               ▼                 ▼                  ▼
    Prometheus Operator    OTel Collector     Loki Agent
    (K8s StatefulSet)          │                  │
               │               ▼                  │
          Remote Write      Tempo                 │
               │            (对象存储)             │
               ▼               │                  │
      VictoriaMetrics           │                  │
      Cluster (vminsert,        │                  │
      vmstorage, vmselect)      │                  │
               │                │                  │
               └────────┬───────┴──────────┬───────┘
                        │                  │
                        ▼                  ▼
                     Grafana (统一可视化)
                   ┌─────────────────────────┐
                   │  SLO Dashboard           │
                   │  指标→Tempo→Loki 联动     │
                   │  Burn Rate 告警大屏       │
                   └─────────────────────────┘
                              │
                              ▼
                     Alertmanager → 钉钉/PagerDuty
```

### 全专栏知识运用回顾

本章串联了全专栏40章的核心知识点：

| 知识域 | 相关章节 | 本章应用 |
|--------|----------|----------|
| PromQL基础 | 第1~5章 | SLO SLI计算公式、Histogram分位数查询 |
| Counter/Gauge/Histogram | 第7~8章 | 网关四大指标类型选择与设计 |
| Exporter开发 | 第10~12章 | 自研API Gateway Exporter完整实现 |
| TSDB原理 | 第17~19章 | 理解Exemplar存储、高基数对TSDB的影响 |
| Remote Read/Write | 第21~23章 | Prometheus → VictoriaMetrics远程写入调优 |
| Prometheus Operator | 第24~25章 | K8s环境下自动服务发现与管理 |
| Thanos & VictoriaMetrics | 第26~28章 | 双轨制架构选型与落地 |
| K8s全方位监控 | 第29~30章 | Operator + ServiceMonitor自动采集 |
| 高基数治理 | 第33~34章 | TraceID不用Label用Exemplar的决策 |
| SLO/SLI/错误预算 | 第37~38章 | 99.99%可用性SLO、多窗口Burn Rate告警 |
| Loki日志系统 | 第36章 | Loki+Tempo+Prometheus三柱联动 |
| Scrape引擎与调优 | 第31~32章 | 高并发采集下的scrape间隔与超时配置 |

### 适用场景与注意事项

**适用场景**：日均亿级以上流量的API网关、金融交易核心链路、电商大促高并发系统、任何需要"指标→追踪→日志"三柱联动的复杂分布式系统。

**核心注意事项**：
- Exemplar需要Prometheus显式开启Feature Flag，且保留时间有限（约24h）
- SLO聚焦3~5个核心指标，避免SLO泛滥导致告警疲劳
- 容量规划留足峰值冗余（建议×2），存储层必要配置副本（replicationFactor ≥ 2）
- VictoriaMetrics的vmstorage节点均衡策略需监控，避免热点节点

### 全书终章寄语

从第1章"认识Prometheus的四种指标类型"开始，到第40章亲手落地一套服务100亿日均流量的企业级可观测性平台，40章是一段从"知晓"到"驾驭"的修炼之路。

监控不是为了看大盘——精美的Grafana面板不是终点。监控的真正价值在于：**当凌晨3点系统故障时，你能在3分钟内从告警定位到根因，而不是对着五个监控系统来回切换、一脸茫然。** Prometheus + VictoriaMetrics + Loki + Tempo + Grafana 这套技术栈的意义也在于此——它们不是孤立的工具，而是一条完整的"感知→诊断→修复"的闭环链路。

40章的修炼告一段落，但可观测性之路永无止境。愿你带着这40章的知识，在你自己的系统中，构建出真正"可观测"的基石。

---

> **全书完**

> 感谢阅读《Prometheus学习专栏》全部40章。从第1章到第40章，每一行代码、每一个PromQL、每一个架构决策，都是你成为可观测性专家的阶梯。祝你一路向上，监控无死角，告警不误报，根因秒定位。
