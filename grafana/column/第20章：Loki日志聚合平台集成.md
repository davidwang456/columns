# 第20章：Loki日志聚合平台集成

## 1. 项目背景

"我们用了ELK做日志分析两年了，但运维成本实在太高——三节点的ES集群每个月云成本超过2万元，Logstash经常OOM，Kibana查询还慢。有没有更轻量的方案？"

SRE工程师大刘在成本优化会议上提出了一个尖锐的问题。公司每天的日志量大约200GB，ES集群需要3台16核64GB的机器来撑住。更糟糕的是，大部分日志写了之后再也没人查——日志的价值远低于成本。

这就是Grafana Loki的设计初衷——"像Prometheus一样处理日志"。Loki不索引日志内容，只索引元数据标签（Label），日志正文以压缩块形式存储在对象存储（S3/GCS/MinIO）中。查询时通过标签缩小范围，然后暴力扫描匹配的内容。成本比ES低一个数量级，查询虽慢一些但满足99%的排查场景。

更重要的是，Loki与Grafana无缝集成——同UI、同查询体验、标签体系与Prometheus一致，真正实现了Metrics和Logs的联动。本章将完整覆盖Loki部署、LogQL查询、与Grafana Dashobard的集成。

## 2. 项目设计

**小胖**（盯着AWS账单）：大师，我们ELK三节点一个月花了2万3。老板说要么降成本，要么砍日志。我想试试你说的Loki，它真的能用S3存日志吗？

**大师**：这正是Loki的核心设计理念。传统日志方案（ELK）的做法是：把日志内容全部建倒排索引→每条日志都能快速搜索→代价是存储成本和内存成本极高。

Loki的做法完全不同：
1. 日志原文压缩后存到对象存储（S3/GCS/MinIO），成本极低（S3标准存储约0.023$/GB/月）。
2. 只对标签（Label）建索引——类似于Prometheus的标签体系。比如`{app="nginx", env="prod"}`。
3. 查询时先通过标签快速定位到相关的日志块，然后暴力扫描这些块的内容。

**小白**（计算着账本）：那如果我要查一条包含特定TraceID的日志，没有全文索引怎么查？

**大师**：这就是Loki的Trade-off——全文搜索确实比ES慢。但也有应对策略：

第一，如果你把TraceID作为Label（如`{trace_id="abc123"}`），Loki会走标签索引，查询毫秒级。但注意Label不能是高基数的（如每个请求一个值），否则索引膨胀。

第二，如果TraceID在日志正文中（没有作为Label），Loki需要扫描日志块。为了加速，可以使用`json`解析器提取字段后在内存中过滤。几百MB的日志块扫描通常在几秒内完成。

**小胖**：那LogQL语法难吗？毕竟要从Kibana迁移过来。

**大师**：LogQL借鉴了PromQL的设计。一个LogQL查询由三部分组成：

```
{app="nginx"}              ← 日志流选择器（必须，类似PromQL的指标选择器）
|= "ERROR"                 ← 日志管道（可选，过滤、解析、格式化）
| json                     ← 解析器（提取JSON字段）
| line_format "{{.message}}"  ← 格式化
```

基本模式：
```
{标签选择器} | 过滤操作 | 解析操作 | 格式化
```

你只需要记住几个操作符：

**过滤**：
- `|= "string"`：日志行包含此字符串
- `!= "string"`：日志行不包含此字符串
- `|~ "regex"`：日志行匹配正则
- `!~ "regex"`：日志行不匹配正则

**解析**（提取日志中的结构化字段）：
- `| json`：把日志行当JSON解析，自动提取字段
- `| logfmt`：解析key=value格式
- `| pattern "<ip> - <_> <method> <url>"`：按模式提取
- `| regexp "(?P<status>\\d+)"`：正则提取

**小白**（追问）：那和Prometheus的指标联动呢？你说的Metrics→Logs跳转。

**大师**：这是Loki+Grafana的杀手级功能。分两步：

第一步，LogQL支持生成指标（Metric queries）：
```logql
rate({app="nginx"} |= "ERROR" [5m])
```
这和PromQL的`rate()`看起来一模一样，区别在于数据来源是日志计数而非Prometheus指标。你可以在Grafana上像PromQL一样画日志趋势图。

第二步，通过Exemplar关联。如果你的应用在HTTP响应中携带了TraceID，并且在Prometheus指标中也记录了TraceID（通过Exemplar），那么在Grafana的Time series面板上，异常点旁边会有一个"小蓝点"——点它直接跳转到Loki查看该TraceID的关联日志。

**小胖**（眼中闪着光芒）：这完全是我们需要的！Prometheus看指标异常→点蓝色Exemplar→跳到Loki看日志→再跳到Tempo看Trace。一条龙！

**大师**：这就是Grafana LGTM栈（Loki+Grafana+Tempo+Mimir）的设计目标——让Metrics/Logs/Traces三根支柱无缝联动。

**技术映射**：Loki标签 = 图书馆书架标签（不索引书的内容，只索引书的分类标签），LogQL = PromQL的日志版（同根同源），对象存储 = 便宜的仓库（存量大但取用慢），ES = 高端保险柜（存得精但成本高）。

## 3. 项目实战

**环境准备**

在Docker Compose中添加Loki和日志采集器（Promtail）：

```yaml
  loki:
    image: grafana/loki:3.0.0
    container_name: loki
    ports:
      - "3100:3100"
    command: -config.file=/etc/loki/local-config.yaml
    volumes:
      - ./loki-config.yaml:/etc/loki/local-config.yaml
      - loki_data:/loki

  promtail:
    image: grafana/promtail:3.0.0
    container_name: promtail
    volumes:
      - /var/log:/var/log:ro
      - /var/lib/docker/containers:/var/lib/docker/containers:ro
      - ./promtail-config.yaml:/etc/promtail/config.yml
    command: -config.file=/etc/promtail/config.yml

volumes:
  loki_data:
```

创建 `loki-config.yaml`（简化配置，本地文件存储）：

```yaml
auth_enabled: false

server:
  http_listen_port: 3100

common:
  path_prefix: /loki
  storage:
    filesystem:
      chunks_directory: /loki/chunks
      rules_directory: /loki/rules
  replication_factor: 1
  ring:
    kvstore:
      store: inmemory

schema_config:
  configs:
    - from: 2024-01-01
      store: tsdb
      object_store: filesystem
      schema: v13
      index:
        prefix: index_
        period: 24h
```

创建 `promtail-config.yaml`：

```yaml
server:
  http_listen_port: 9080
  grpc_listen_port: 0

positions:
  filename: /tmp/positions.yaml

clients:
  - url: http://loki:3100/loki/api/v1/push

scrape_configs:
  - job_name: system
    static_configs:
      - targets:
          - localhost
        labels:
          job: varlogs
          __path__: /var/log/*.log

  - job_name: docker
    docker_sd_configs:
      - host: unix:///var/run/docker.sock
        refresh_interval: 5s
    relabel_configs:
      - source_labels: ['__meta_docker_container_name']
        regex: '/(.*)'
        target_label: 'container'
      - source_labels: ['__meta_docker_container_id']
        target_label: 'container_id'
```

**步骤一：配置Grafana Loki数据源**

Grafana → Data Sources → Add data source → Loki：

| 参数 | 值 |
|------|-----|
| Name | `Loki` |
| URL | `http://loki:3100` |
| Max lines | `1000`（单次查询最大返回行数）|
| Derived fields | （下节配置TraceID关联）|

Save & test → 成功。

**步骤二：LogQL基础查询实战**

打开Grafana Explore，选择Loki数据源。

**查询1：查看Nginx容器的所有日志**
```logql
{container="nginx"}
```

**查询2：只查看ERROR级别的日志**
```logql
{container="nginx"} |= "ERROR"
```

**查询3：查看除healthcheck之外的所有请求**
```logql
{container="nginx"} != "/healthcheck"
```

**查询4：正则匹配——以5开头的HTTP状态码**
```logql
{container="nginx"} |~ "HTTP/[12].[01]\" 5\\d{2}"
```

**查询5：JSON解析——提取结构化字段**

如果日志行是JSON格式：
```logql
{container="nginx"} | json
```
自动提取所有JSON顶级字段，可以在Grafana Explore中按字段筛选。

**查询6：按字段过滤**
```logql
{container="nginx"} | json | status = "500"
```
先解析JSON，然后在`status`字段上等值过滤（不是日志行文本过滤，而是结构化字段过滤）。

**步骤三：日志指标——Metric Queries**

在Logs面板基础上，切换到Metrics模式：

```logql
# 每分钟ERROR日志数量
rate({container="nginx"} |= "ERROR" [5m])

# 按HTTP状态码分组的请求速率
sum by (status) (
  rate({container="nginx"} | json | __error__ = "" [5m])
)

# 各URL的请求量Top5（需要URL在日志中被json解析）
topk(5,
  sum by (url) (
    count_over_time({container="nginx"} | json | __error__ = "" [1h])
  )
)
```

**步骤四：Dashboard + Loki面板**

创建Dashboard → Add panel。

**面板1：ERROR日志趋势（Time series）**
```logql
rate({container=~"$container"} |= "ERROR" [5m])
```

**面板2：日志级别分布（Pie chart）**

需要多个Query组合：
```
# Query A
sum(count_over_time({container=~"$container"} |= "ERROR" [$__range]))
# Query B
sum(count_over_time({container=~"$container"} |= "WARN" [$__range]))
# Query C
sum(count_over_time({container=~"$container"} |= "INFO" [$__range]))
```

然后使用Transform → Merge显示。

**面板3：Logs浏览面板**

添加Logs面板，查询：
```logql
{container=~"$container"} |= "$search"
```

面板配置：显示时间戳、容器名、日志内容列。

创建两个变量：
- `$container`：Query (`label_values(container)`)
- `$search`：Text box（用户输入搜索关键词）

现在你可以在Dashboard上动态切换容器和搜索关键词浏览日志。

**步骤五：Prometheus+Loki联动——Exemplar**

配置应用在Prometheus指标中注入Exemplar（以Go为例）：

```go
// 在http.Handler中
requestDuration.WithLabelValues(method, path).ObserveWithExemplar(
    duration.Seconds(),
    prometheus.Labels{
        "trace_id": traceID,  // 关联TraceID
    },
)
```

在Grafana的Time series面板中，如果你的应用指标带有Exemplar，异常数据点旁会出现蓝色圆点。点击它：

1. 配置Derived Field关联：Loki数据源设置 → Derived fields → Add
   - Name: `trace_id`
   - Regex: `trace_id=(\w+)`
   - URL: 留空（使用默认探索链接）
   
2. 点击Exemplar → 自动跳转到Explore → Loki查询`{app="your-app"} |= "trace_id=abc123"`。

**步骤六：生产环境Loki架构**

小规模（<10GB/天）：
```
Promtail → Loki(单实例) → 本地磁盘
```

中规模（10-100GB/天）：
```
Promtail → Loki(3节点) → S3/MinIO
       → 读写分离(Read/Write targets)
```

大规模（>100GB/天）：
```
Grafana Agent(Alloy) → Loki分布式
  Distributor → Ingester → S3
  Querier → Query Frontend → Grafana
  Compactor → S3
```

**常见坑点**
1. **高基数Label导致性能问题**：不要用`user_id`、`request_id`、`trace_id`作为Label。每个Label值会创建独立的索引条目，高基数Label会导致索引膨胀。
2. **Label数量限制**：Loki默认限制每个流的Label数量为15个，超过会被拒绝写入。
3. **日志时间戳问题**：Promtail默认把日志文件的mtime作为时间戳。如果日志内容是JSON且自带`timestamp`字段，在Promtail配置中添加`timestamp` stage来使用日志自带的时间。
4. **rate()中的时间窗口无法小于Dashboard时间范围**：`rate({app="nginx"} [5m])`中的`[5m]`必须≤Dashboard的时间范围。
5. **大量LogQL查询拖慢Grafana**：Metric query是对日志做聚合统计，开销大于普通HTTP API。Dashboard中不要放超过5个对Loki的`rate()`查询。

## 4. 项目总结

**Loki vs Elasticsearch 终极对比**

| 维度 | Loki | Elasticsearch |
|------|------|---------------|
| 存储成本 | 低（对象存储） | 高（需高性能磁盘） |
| 内存需求 | 中 | 高（JVM堆+索引缓存） |
| 全文搜索 | 慢（暴力扫描） | 快（倒排索引） |
| 标签搜索 | 快（标签索引） | 快 |
| 聚合分析 | 有限 | 强大 |
| 运维复杂度 | 低 | 高 |
| Prometheus亲和性 | 极高（同标签体系） | 一般 |

**适用场景**
1. 低成本日志存储：大量日志需要保留但极少查询
2. Kubernetes日志聚合：所有Pod日志统一收集到Loki
3. Metrics+Logs联动：配合Prometheus做异常下钻
4. 合规留存：长期保留日志满足合规要求

**不适用场景**
1. 频繁的全文模糊搜索（不如ES）
2. 需要对日志做复杂的多维聚合分析（不如ES）
3. 毫秒级查询延迟要求（Loki扫描可能秒级）

**注意事项**
1. Label设计原则：静态不变的信息做Label（app/env/cluster），动态变化的信息放在日志正文中（user_id/trace_id）
2. 日志保留时间通过`compactor`和`retention_deletes_enabled`控制
3. Grafana Explore中的LogQL支持自动补全，善用Ctrl+Space
4. Loki 3.0引入了新的TSDB索引格式（比BoltDB更高效），建议升级

**常见踩坑经验**
1. **日志写入Loki后被截断**：Loki默认单条日志最大64KB。超过则截断。修改`max_line_size`参数。
2. **Promtail采集不到Docker日志**：确认Promtail容器挂载了`/var/lib/docker/containers`目录，且配置了正确的`docker_sd_configs`。
3. **LogQL时间范围导致无数据**：`rate()`函数中`[]`内的时间范围如果小于Grafana Dashboard时间范围，图表可能出现断点。

**思考题**
1. 当前架构是Promtail→Loki单实例。如果Promtail挂了，日志会不会丢？如何设计一个"不丢日志"的采集架构？
2. LogQL中`count_over_time({app="nginx"}[1h])`和`rate({app="nginx"}[1h])`的结果有什么不同？分别适用什么场景？
