# 第7章：Prometheus集成与监控可视化

## 1. 项目背景

"我们Prometheus已经采了2000多个指标，Alertmanager也配好了告警。现在老板要看整体系统的健康状况，需求是——一张Dashboard能展示所有关键指标，能不能实现？"

运维负责人大刘正在经历"有数据但没洞察"的尴尬期。Prometheus完美实现了数据采集和存储，但默认自带的Web UI（Prometheus Expression Browser）只能做即席查询，没法组合成Dashboard。而Grafana恰恰是Prometheus的"灵魂伴侣"——两者的结合就像火箭有了发射台。

但Prometheus + Grafana的组合不是简单连上线就完事了。PromQL编写、指标类型理解（Counter/Gauge/Histogram/Summary）、采样粒度选择、告警状态可视化——每一个环节都有大量的实践技巧和常见陷阱。比如`rate()`函数的采样窗口选多久？`histogram_quantile()`的误差怎么控制？`increase()`和`rate()`什么时候该用哪个？

本章聚焦在Prometheus与Grafana的实际搭配使用中，最频繁遇到的5类场景，通过实战PromQL建立"看到需求就能写出查询"的肌肉记忆。

## 2. 项目设计

**小胖**（抓耳挠腮）：大师，我写了个PromQL——`rate(http_requests_total[1m])`，结果Grafana的图显示出来是一堆锯齿状的线，跟心电图似的。同事说这个图没法看，让我优化一下。

**大师**（看了一眼）：典型的采样窗口过短问题。`rate()`函数的`[1m]`意思是取最近1分钟内的数据点做线性回归计算rate。如果你的Prometheus采集间隔是15秒，1分钟里只有4个点，统计学样本太小，rate自然波动剧烈。

**小白**（掏出纸笔）：那窗口选多大合适？

**大师**：经验法则是——至少是scrape_interval的4倍。比如采集间隔15秒，窗口至少`[1m]`；但通常建议`[5m]`，因为5分钟既平滑了毛刺，又不会掩盖真实波动。对于告警规则，甚至可以用`[15m]`来做平滑。

**小胖**（追问）：那`rate()`和`increase()`有什么区别？我看文档说都是处理Counter类型。

**大师**：本质区别在于输出单位。`rate()`返回"每秒的增长速率"，`increase()`返回"时间段内的增长总量"。所以：
- `rate(http_requests_total[5m])` → 每秒多少个请求（QPS）
- `increase(http_requests_total[5m])` → 5分钟内一共多少个请求

前者适合在Grafana的Y轴上展示（因为单位是/s，纵轴值恒定），后者适合做绝对值比较。现实中90%的场景用`rate()`就够了。

**小胖**（转换话题）：还有那个Histogram和Summary，学Prometheus的时候就说这两个类型能算分位数，但实际上我写了`histogram_quantile(0.99, ...)`，Grafana显示的P99值跟真正的P99对不上。

**大师**：Histogram计算分位数有一个著名的精度陷阱。`histogram_quantile()`是通过bucket插值估算分位数的，精度取决于你的bucket定义。如果你的QPS指标，bucket只有`{0.1, 0.5, 1, 5}`这几个档次，那低于0.1秒的数据都落在第一个bucket，P99的估算误差会非常大。

**小白**（若有所思）：所以Histogram的bucket设计是关键？

**大师**：没错。好的bucket设计要覆盖你的典型延迟范围。比如你的P50延迟30ms，P99延迟200ms，bucket应该这样设：`.005, .01, .025, .05, .1, .25, .5, 1, 2.5, 5, 10`（秒）。让bucket在长尾区分布足够密，这样`histogram_quantile()`估算的误差通常在5%以内。

**小胖**：那Summary类型呢？它直接算好了分位数，是不是更简单？

**大师**：Summary是客户端算好分位数再暴露，Grafana直接取用，看似方便但有一个致命缺陷——不能聚合。两台服务器的Summary分位数不能被`sum()` 或 `avg()`，因为它已经丢失了原始分布信息。Histogram虽然多了一步计算，但可以跨实例聚合，这正是微服务监控的核心需求。

**小胖**（挠头）：说到聚合，我有一个Dashboard挂了50个微服务实例的QPS，图里50根线，颜色花花绿绿的根本看不清。

**大师**：给你一个口诀——"聚合上报，分解下钻"。总览Dashboard用聚合查询：`sum(rate(http_requests_total[5m]))`（全局总QPS）。服务Dashboard加一层：`sum by (service) (rate(http_requests_total[5m]))`（各服务QPS）。实例详情Dashboard才用`rate(http_requests_total{instance="xxx"}[5m])`。三层递进，绝不一开始就展示50根线。

**小白**：Prometheus有一个容易被忽视的功能——Recording rules（记录规则）。Grafana怎么用好它？

**大师**：Recording rules的核心价值是"预计算"。如果你有一个非常复杂的PromQL每秒被10个Dashboard引用，每次都现算就太慢了。把它设成recording rule，Prometheus隔一段时间算一次存起来，Grafana直接查询结果——查询速度从秒级降到毫秒级。尤其是`histogram_quantile()`这种计算开销大的表达式，强烈建议用recording rule。

**小胖**：最后一个问题——Grafana的Explore里面有个Metrics Browser，里面的指标太多了，怎么快速找到我要的？

**大师**：两个技巧。第一，利用Metrics Browser的搜索框，它支持模糊匹配；第二，建立命名规范。比如你们团队约定所有HTTP指标以`http_`开头，所有gRPC指标以`grpc_`开头。再通过Prometheus的`relabel_config`干掉无用的指标（如`go_gc_*`内部指标），减少噪音。

**技术映射**：rate()的窗口 = 拍照的快门速度（太快画面波动大，太慢细节模糊），Histogram bucket = 量杯刻度（刻度越密测量越准），Recording rules = 预制菜（提前做好，点菜时秒上）。

## 3. 项目实战

**环境准备**

基于之前的Docker Compose环境，新增一个模拟应用暴露HTTP指标。

```yaml
# 添加到docker-compose.yml
  demo-app:
    image: prom/statsd-exporter:v0.26.0
    container_name: demo-app
    ports:
      - "9102:9102"
      - "9125:9125/udp"
    command:
      - '--statsd.listen-udp=:9125'
      - '--statsd.mapping-config=/etc/statsd-exporter/mapping.conf'
      - '--web.listen-address=:9102'
```

在Prometheus配置中添加抓取目标：
```yaml
# prometheus.yml 添加
  - job_name: 'demo-app'
    static_configs:
      - targets: ['demo-app:9102']
```

**步骤一：QPS与错误率Dashboard（RED方法）**

创建Dashboard，命名"应用性能监控 - RED"。

RED = Rate（请求速率） + Errors（错误率） + Duration（延迟）。

**面板1：QPS（Stat面板）**
```promql
# 全局QPS
sum(rate(http_requests_total[5m]))
```
阈值：基于业务容量设定。如果正常QPS范围是100-500，则 >500 Warning。

**面板2：错误率（Stat面板）**
```promql
# 5xx错误率 = 5xx请求数 / 总请求数
sum(rate(http_requests_total{status=~"5.."}[5m]))
/
sum(rate(http_requests_total[5m]))
* 100
```
阈值：>1% Warning, >5% Critical。

**面板3：P99延迟（Stat面板）**
```promql
histogram_quantile(0.99,
  sum(rate(http_request_duration_seconds_bucket[5m])) by (le)
)
```
该查询的分解说明：
- `rate(http_request_duration_seconds_bucket[5m])`：计算每个bucket的速率
- `sum by (le)`：跨所有实例聚合每个bucket（le = less than or equal，即bucket的上界）
- `histogram_quantile(0.99, ...)`：根据bucket分布估算99分位数

**面板4：QPS趋势图（Time series）**
```promql
sum(rate(http_requests_total[5m]))
```

**面板5：错误率趋势图（Time series）**
```promql
sum by (status) (rate(http_requests_total{status=~"5..|4.."}[5m]))
```
设置Overrides：5xx用红色、4xx用橙色。

**步骤二：延迟分位数可视化**

**面板6：多分位数延迟（Time series）**
```promql
histogram_quantile(0.50,
  sum(rate(http_request_duration_seconds_bucket[5m])) by (le)
)
```
同样的查询替换分位数为0.90、0.99、0.999，分别显示P50/P90/P99/P999。

技巧：在Grafana中，可以用变量动态选择分位数：
- 创建Custom变量`$percentile`，值：`50, 90, 99, 999`
- 查询改为：`histogram_quantile($percentile/100, ...)`

**面板7：延迟热力分布图（Heatmap）**

这是Grafana特色的可视化方式，比折线图更能展现延迟分布：
```promql
sum(rate(http_request_duration_seconds_bucket[5m])) by (le)
```
面板类型选择`Heatmap`，Y轴Bucket会自动按le排序。

**步骤三：资源使用率Dashboard**

**面板8：CPU使用率（Time series + Gauge组合）**
```promql
# CPU使用率（排除idle）
100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)

# 按实例分解
100 - (avg by (instance) (rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)
```

**面板9：内存使用率（Gauge）**
```promql
(1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) * 100
```

**面板10：磁盘IO（Time series）**
```promql
rate(node_disk_read_bytes_total[5m])
rate(node_disk_written_bytes_total[5m])
```

**面板11：网络流量（Time series）**
```promql
rate(node_network_receive_bytes_total{device!="lo"}[5m])
rate(node_network_transmit_bytes_total{device!="lo"}[5m])
```

**步骤四：服务健康度综合看板**

组合所有面板，最终Dashboard布局：

```
Row 1: 关键KPI
[QPS Stat 3x2][Error Rate Stat 3x2][P99 Stat 3x2][P50 Stat 3x2]

Row 2: 流量趋势
[QPS Time series 6x4][Error Rate Time series 6x4]

Row 3: 延迟分析
[Multi-Percentile Latency 8x5][Latency Heatmap 4x5]

Row 4: 资源使用
[CPU Gauge(×N from Repeat) 4x4][Memory Gauge 4x4][Disk IO 4x4]

Row 5: 实例明细
[CPU per Instance Time series 12x5]
```

**步骤五：常用PromQL速查表**

| 需求 | PromQL |
|------|--------|
| 当前QPS | `sum(rate(http_requests_total[5m]))` |
| 按服务QPS | `sum by (service) (rate(http_requests_total[5m]))` |
| P99延迟 | `histogram_quantile(0.99, sum(rate(http_duration_bucket[5m])) by (le))` |
| 错误率% | `sum(rate(http_requests_total{status=~"5.."}[5m])) / sum(rate(http_requests_total[5m])) * 100` |
| CPU使用率 | `100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)` |
| 内存使用率 | `(1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes) * 100` |
| 磁盘使用率 | `(1 - node_filesystem_avail_bytes / node_filesystem_size_bytes) * 100` |
| Pod重启次数 | `rate(kube_pod_container_status_restarts_total[1h])` |
| 流量（字节/秒）| `rate(node_network_receive_bytes_total[5m])` |

**常见坑点**
1. **Counter重置导致rate跳变**：Counter类型重启后会归零，`rate()`会自动处理重置（通过检测值减少），但`increase()`可能出现非常大的值。如果出现异常值，检查目标是否重启过。
2. **Histogram的le标签缺失**：如果查询Histogram时提示"no datapoints found"，检查是否缺少`by (le)`，因为`histogram_quantile`必须知道bucket边界。
3. **时区问题**：Grafana默认UTC时间，Prometheus也默认UTC，两者一致通常没问题。但如果Dashboard设了时区，`$__range`等变量会受影响。
4. **`rate()`和`irate()`的混淆**：`irate()`只看最近两个数据点，曲线更尖锐但误差大。Dashboard展示用`rate()`，告警规则有时用`irate()`来快速感知突变。

## 4. 项目总结

**优点 & 缺点**

| 优点 | 说明 |
|------|------|
| 原生集成 | Grafana + Prometheus是最成熟的可视化组合 |
| PromQL强大 | 丰富的函数支持(GDP、histogram、predict_linear) |
| 标签灵活 | 多维度聚合和过滤，一份数据多种视角 |
| Grafana Explore | 即席查询和指标浏览，调试友好 |
| 告警无缝对接 | Alert Rule直接引用Prometheus指标 |

| 缺点 | 说明 |
|------|------|
| PromQL学习曲线 | 不同于SQL，查询逻辑对开发者不直观 |
| 大规模性能 | 高基数标签(如user_id)导致时间序列爆炸 |
| 长周期查询 | 30天以上的range query对Prometheus压力大 |
| 无JOIN | 跨指标Join必须用Transform或Recording rule |

**适用场景**
1. 微服务性能监控：QPS、延迟、错误率（RED三大指标）
2. 基础设施监控：CPU、内存、磁盘、网络四大天王
3. Kubernetes集群监控：Pod/Deployment/Node级别的资源与状态
4. 业务指标监控：订单量、支付成功率、用户活跃度

**注意事项**
1. 避免用user_id、request_id等高基数字段作为label（每个值一个时间序列）
2. Histogram bucket不宜超过20个（过多bucket增加存储和计算开销）
3. Recording rule名称不要与原始指标重名，建议加前缀（如`job:http_requests:rate5m`）
4. 每个Dashboard的查询数量控制在30个以内，减少Prometheus负载

**常见踩坑经验**
1. **`sum(rate(...))` 忘记用`by`**：如果直接sum不加by，多个instance的数据会被加在一起，丢失分实例信息。
2. **`rate()`用错指标类型**：`rate()`只适用于Counter类型，对Gauge类型使用`rate()`会得到无意义的值。
3. **Prometheus TSDB compaction期间查询慢**：Prometheus每2小时做一次compaction，期间查询延迟可能翻倍。如果Dashboard在这期间表现变差，调整`storage.tsdb.max-block-duration`。

**思考题**
1. 如果要用Prometheus监控一个每秒产生10万个请求的API网关，如何设计指标和label来避免时间序列爆炸？
2. 为什么`histogram_quantile(0.99, sum(rate(bucket[5m])) by (le, instance))`和`histogram_quantile(0.99, sum(rate(bucket[5m])) by (le))`的结果不同？前者是每个实例的P99再求某种平均，后者是全局P99——哪个更准确？
