# 第15章：Prometheus日常运维与故障排查

## 一、项目背景

凌晨2点，运维老王的手机撕破黑夜——Prometheus宕机了。Prometheus负责公司全部服务的监控和告警，它一挂，意味着整个监控体系瞬间"失明"：没人知道线上流量是否正常，没人知道数据库是否慢查询，更没人知道Redis是否内存即将溢出。老王ssh登录服务器，发现Prometheus进程消失得干干净净，/var/log/messages里只留下一行冰冷的"Killed"。他立刻反应过来：OOM Killer干的。但该调大内存限制还是该优化采集配置？老王心里没底。

重启Prometheus后，日志开始打印"Replaying WAL, this may take a while"，这一"while"就是20分钟——Prometheus在回放WAL（Write-Ahead Log）文件恢复数据。更令人崩溃的是，重启完成后老王发现凌晨2点到现在的监控数据出现了缺口，这段时间的采集数据因未写入磁盘而永久丢失。

这只是Prometheus运维噩梦的冰山一角。实际生产环境中，Prometheus自身就是一个需要精心维护的复杂系统：内存OOM导致进程被杀、突然断电引发WAL损坏、采集目标太多产生Scrape timeout、未设置retention导致磁盘写满、配置reload看似成功实则静默失败……本章将系统梳理Prometheus服役期间的运维命令、常见故障排查SOP以及健康检查机制，让"监控者"本身也被有效监控。

## 二、剧本式交锋对话

**小胖**：（半夜两点被叫起来，顶着黑眼圈）大师，Prometheus又挂了！日志里就一个"Killed"，我猜又是OOM。但上周刚给它加了4G内存，这也不够吃？

**大师**：（淡定地抿了口咖啡）加内存是治标不治本。你先用promtool看看TSDB现在有多少active series——我怀疑你们哪个业务新接进来的指标基数暴涨了。

**小胖**：promtool？那个命令行工具箱？

**小白**：（抢话）我知道我知道！上次你让我用`promtool check config prometheus.yml`检查配置语法。除了check config，还有哪些功能？

**大师**：（竖起四根手指）promtool有四大核心功能。第一，**check config**——静态检查prometheus.yml语法，注意它只管语法不管语义，语法通过不代表配置能正常运行。第二，**check rules**——检查告警/记录规则的表达式是否合法。第三，**test rules**——单元测试你的告警规则，喂入样本数据验证报警是否会按预期触发。第四，**tsdb**子命令，这是运维利器——`tsdb list`列出所有block的时间范围和样本数，`tsdb analyze`输出每个指标的series数和label分布，`tsdb bench`做查询性能压测。

**小胖**：（掏出小本本疯狂记录）等等，tsdb analyze能帮我揪出高基数指标？

**大师**：正是。90%的OOM问题都源于label基数爆炸。analyze的输出里有个"Highest cardinality labels"部分，哪个指标的label组合数最多一目了然。比如某个日活用户的UUID被当成了label值，那series数量直接百万起步。排错三步走：先`promtool tsdb analyze`看基数分布，再用Prometheus自身的HTTP API查询`prometheus_tsdb_head_series`确认当前series数，最后下钻到具体指标做针对性优化——比如把UUID从label改成日志打印。

**小白**：对了大师，说到HTTP API——我们经常在Grafana里查数据，其实底层不就是调Prometheus的API吗？有哪些API是运维必备的？

**大师**：（赞许地点头）问到点子上了。核心API我给你列五类。第一，`/api/v1/query`——即席查询瞬时值，比如`curl 'localhost:9090/api/v1/query?query=up'`看所有target的存活状态。第二，`/api/v1/query_range`——时间范围查询，带start/end/step参数，Grafana画图的背后就是它。第三，`/api/v1/targets`——查看所有采集目标的状态，包含health（up/down）、lastScrape、lastError等字段，排查哪个target挂了特别有用。第四，`/api/v1/rules`——查看告警规则和记录规则的运行状态，能看到每个rule的health和最后一次执行时间。第五，`/api/v1/status/tsdb`——TSDB内部状态，head series数量、block总数、存储占用一目了然。

**小胖**：（皱眉）说到启动慢的问题——WAL回放为什么这么慢？它到底在干什么？

**大师**：WAL的全称是Write-Ahead Log，所有刚采集到的样本数据先写入WAL，随后定期压缩到block中。假如Prometheus异常退出，重启时它必须把WAL里的数据全部回放到内存，重建索引。因此WAL越大，启动越慢。可通过`prometheus_tsdb_wal_corruptions_total`监控WAL健康度，通过`prometheus_tsdb_data_replay_duration_seconds`观测回放耗时。想加速的话，可以调整`--storage.tsdb.wal-compression`启用压缩减少WAL体积，或者用SSD提升I/O吞吐。

**小白**：那健康检查端点`/-/healthy`和`/-/ready`到底有什么区别？K8s里我经常搞混。

**大师**：这俩区别很大，混用容易翻车。`/-/healthy`只检查进程是否存活，只要HTTP Server能响应就返回200，哪怕WAL还在回放、配置还没加载完毕。`/-/ready`则严格得多——它必须等所有组件初始化完成才返回200，包括WAL回放完毕、配置文件加载成功、所有scrape target注册完成。所以K8s中：**liveness probe用/-/healthy**（进程挂了就重启），**readiness probe必须用/-/ready**（防止流量打到还没准备好的Pod上）。很多同学把liveness probe也配成/-/ready，结果WAL回放期间Pod被反复kill，永远起不来。

**小胖**：（恍然大悟）难怪上次我们的Prometheus在K8s里重启了十几次才成功……

## 三、项目实战

### 环境准备

- 一台已运行Prometheus至少24小时的机器（有一定TSDB数据积累）
- promtool命令行工具（与Prometheus一起安装）
- 有停/启Prometheus进程的权限
- 实验环境有额外磁盘空间用于故障模拟

### 步骤1：使用promtool诊断TSDB健康状态

首先查看TSDB的block组织结构：

```bash
promtool tsdb list /prometheus/data/
```

输出示例：

```
BLOCK ULID                  MIN TIME       MAX TIME       DURATION     NUM SAMPLES  NUM CHUNKS  NUM SERIES
01HX9K8Z3A4B5C6D7E8F9G0H   1715686400000  1715772800000  24h0m0s      123456789    45678       12345
01HX8J7Y2Z3A4B5C6D7E8F9G0   1715600000000  1715686400000  24h0m0s      118765432    43210       11980
```

每个block代表一个不可变的24小时（默认）数据窗口。重点关注NUM SERIES列——如果某个block的series数突然飙升，说明该时间段内引入了高基数指标。

执行全文分析：

```bash
promtool tsdb analyze /prometheus/data/
```

输出关键部分解读：

```
Block count: 15
Total samples: 1,234,567,890

Label name statistics:
  job: 42 unique values
  instance: 156 unique values
  __name__: 3245 unique values

Most time series:
  150k: node_network_transmit_bytes_total
  120k: container_memory_usage_bytes
   98k: http_request_duration_seconds_bucket  ← 高基数！
```

上述输出中，`http_request_duration_seconds_bucket`拥有98k个series，结合其label分布能定位到元凶——例如`user_id`被设为label导致组合爆炸。建议将高基数的user_id信息改为span/trace的attribute打印到日志，而非作为metric label。

检查WAL目录状态：

```bash
ls -lh /prometheus/data/wal/
```

输出示例：

```
total 512M
00000001  128M
00000002  128M
00000003  128M
00000004  128M
checkpoint.00000002/  (目录)
```

WAL文件大小直接影响启动回放耗时。若WAL过大（超过1GB），建议检查采集间隔是否过密，或者`--storage.tsdb.min-block-duration`是否设置过小导致block迟迟不落盘。

### 步骤2：Prometheus HTTP API实战

**查询瞬时值——确认所有端点存活：**

```bash
curl 'http://localhost:9090/api/v1/query?query=up' | jq .
```

返回JSON中`data.result`数组包含每个target的`metric`（标签集）和`value`（时间戳+数值），up=1表示正常，up=0表示采集失败。

**查询时间范围——观察CPU使用率变化趋势：**

```bash
START=$(date -d '1 hour ago' +%s)
END=$(date +%s)
curl "http://localhost:9090/api/v1/query_range?query=rate(node_cpu_seconds_total{mode=\"idle\"}[5m])&start=$START&end=$END&step=15s" | jq .
```

核心参数：start/end为Unix时间戳，step为查询步长（影响返回数据点的密度）。Grafana的每一个面板本质上就是在生成这样一条HTTP请求。

**查看采集目标状态——排查哪个target掉线：**

```bash
curl 'http://localhost:9090/api/v1/targets' | jq '.data.activeTargets[] | {labels: .labels, health: .health, lastScrape: .lastScrape, lastError: .lastError}'
```

重点关注`health`字段（up/down）和`lastError`——后者记录了最近一次scrape失败的原因，如"context deadline exceeded"（超时）或"connection refused"（目标未启动）。

**查看告警规则运行状态：**

```bash
curl 'http://localhost:9090/api/v1/rules' | jq '.data.groups[].rules[] | {name: .name, health: .health, lastEvaluation: .lastEvaluation}'
```

`health`为"ok"表示规则正常执行；若为"unknown"则表示规则评估出错。

**查看TSDB内部状态：**

```bash
curl 'http://localhost:9090/api/v1/status/tsdb' | jq '.data'
```

返回数据包括`headStats`（当前活跃series数、已写入样本数、WAL回放状态）和`seriesCountByMetricName`（按指标名统计series数）。这是排查OOM的第一站。

### 步骤3：监控Prometheus本身

Prometheus暴露了丰富的自身运行指标，在`/metrics`端点可查看。关键指标汇总如下：

| 指标 | 含义 | 告警阈值建议 |
|------|------|-------------|
| prometheus_tsdb_head_series | 当前活跃time series总数 | >100万Warning，>150万Critical |
| prometheus_tsdb_head_samples_appended_total | 累计写入样本总数 | 观察增长速率，突增100%说明有新的大规模采集加入 |
| prometheus_engine_query_duration_seconds | 查询耗时分布（Histogram） | P99 > 5s告警 |
| prometheus_target_scrape_pool_targets | 每个采集job的target数量 | 10分钟内突降50%触发Critical告警 |
| prometheus_tsdb_storage_blocks_bytes | TSDB存储占用字节数 | 超过可用磁盘容量的80%触发告警 |
| prometheus_notifications_dropped_total | 丢弃的告警通知（发给Alertmanager失败） | >0立即告警 |
| prometheus_tsdb_wal_corruptions_total | WAL损坏次数 | >0立刻告警，说明磁盘或文件系统有问题 |

创建自监控告警规则文件 `rules/prometheus_self.yml`：

```yaml
groups:
  - name: prometheus_self
    rules:
      - alert: PrometheusHighSeriesCount
        expr: prometheus_tsdb_head_series > 1000000
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Prometheus time series数超过100万，当前值: {{ $value }}"
          description: "基数过高可能导致OOM，请执行promtool tsdb analyze排查高基数指标"

      - alert: PrometheusHighQueryDuration
        expr: histogram_quantile(0.99, rate(prometheus_engine_query_duration_seconds_bucket[5m])) > 5
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Prometheus P99查询延迟超过5秒"
          description: "查询性能下降会影响Grafana面板渲染和告警评估"

      - alert: PrometheusScrapeTargetsDown
        expr: prometheus_target_scrape_pool_targets < prometheus_target_scrape_pool_targets offset 10m
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "Prometheus采集目标数突然下降，当前: {{ $value }}"
          description: "可能为配置变更导致部分job丢失，请检查prometheus.yml和目标服务状态"

      - alert: PrometheusNotificationsDropped
        expr: rate(prometheus_notifications_dropped_total[5m]) > 0
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "Prometheus告警通知丢弃，Alertmanager可能不可达"
          description: "丢弃速率: {{ $value }} 条/秒"

      - alert: PrometheusStorageFull
        expr: prometheus_tsdb_storage_blocks_bytes / 1024 / 1024 / 1024 > 80
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Prometheus存储占用超过80GB"
          description: "建议检查--storage.tsdb.retention.size设置或清理旧数据"
```

重启Prometheus加载新规则后，在Web UI的`/rules`页面可看到`prometheus_self`规则组。

### 步骤4：故障模拟与排查

#### 模拟故障1：磁盘空间不足

在实验环境中用dd快速填充磁盘（**注意：仅在实验环境操作！**）：

```bash
dd if=/dev/zero of=/tmp/bigfile bs=1M count=5000
```

当磁盘空间被耗尽后，Prometheus日志会出现：

```
level=error ts=2025-05-14T02:15:30.123Z caller=main.go:123 msg="Opening storage failed" err="no space left on device"
```

排查流程：

```bash
df -h /prometheus/                                    # 确认磁盘使用率
du -sh /prometheus/data/*                             # 找到占用最大的目录
promtool tsdb list /prometheus/data/ | tail -5         # 查看最老的block
```

解决方案分三个层次。第一，临时救急——手动删除最老的block释放空间（风险低，ZIP文件独立存储）。第二，治本——设置`--storage.tsdb.retention.size=80GB`让Prometheus自动淘汰旧block。第三，监控——将磁盘使用率纳入自监控告警。注意：`--storage.tsdb.retention.time`和`--storage.tsdb.retention.size`同时设置时，以先达到的条件为准触发淘汰。

#### 模拟故障2：配置reload失败静默

这是Prometheus最隐蔽的坑之一。修改`prometheus.yml`故意加入语法错误（如缩进错误或重复定义job_name），然后执行热加载：

```bash
curl -X POST http://localhost:9090/-/reload
```

**你会收到HTTP 200 OK！**——但新配置根本没有生效。Prometheus的热加载失败不会返回非200状态码，只是默默地在日志里记录一行：

```
level=error ts=... caller=main.go:456 msg="Loading configuration file failed" err="parsing YAML file prometheus.yml: yaml: line 15: did not find expected key"
```

标准操作流程（SOP）：

1. **改配置后先check**：`promtool check config prometheus.yml`
2. **check通过后再reload**：`curl -X POST http://localhost:9090/-/reload`
3. **reload后验证生效**：查看日志确认无error，或用`curl /api/v1/targets`检查新增job的target是否已出现
4. **考虑自动化**：在CI/CD流水线中加入promtool check步骤，阻止有语法错误的配置合并到主分支

#### 模拟故障3：WAL损坏恢复

正常启动时的WAL回放日志：

```
level=info ts=... caller=head.go:613 msg="Replaying WAL, this may take a while"
level=info ts=... caller=head.go:667 msg="WAL checkpoint loaded"
level=info ts=... caller=head.go:672 msg="WAL segment loaded" segment=123 maxSegment=156
```

如果WAL文件物理损坏，Prometheus会打印类似"corrupted WAL entry"的错误并启动失败。此时有两条路：

- **止损优先**：删除`/prometheus/data/wal/`目录（损失最近2小时未压缩的数据），让Prometheus从最后一个完整block恢复。
- **保留数据**：尝试用`promtool tsdb dump`导出可读的block数据，然后重建。

生产环境建议：启用`--storage.tsdb.wal-compression`减少WAL体积（压缩率约50%），配合SSD磁盘加速I/O，定期备份`/prometheus/data/wal/`目录。

### 步骤5：健康检查端点

两个端点务必区分清楚：

```bash
# 进程存活检查——只要HTTP服务启动就返回200
curl http://localhost:9090/-/healthy

# 就绪检查——WAL回放完成、配置加载成功才返回200（否则返回503）
curl http://localhost:9090/-/ready
```

Kubernetes配置示例：

```yaml
livenessProbe:
  httpGet:
    path: /-/healthy
    port: 9090
  initialDelaySeconds: 10
  periodSeconds: 15

readinessProbe:
  httpGet:
    path: /-/ready
    port: 9090
  initialDelaySeconds: 30
  periodSeconds: 10
  failureThreshold: 3
```

关键点：readinessProbe的`initialDelaySeconds`要大于WAL回放的预估耗时，否则Pod在没准备好时就会被判定为失败。如果Prometheus数据量大，这个值可能需要设为300秒甚至更长。

### 可能遇到的坑

1. **promtool check config能通过但实际运行出错**：check仅做静态YAML语法校验和PromQL表达式语法检查，不做语义校验（如scrape目标是否可达）。生产部署前必须在预发环境验证。
2. **Prometheus API没有鉴权**：`/api/v1/*`和`/-/reload`默认无认证，任何人可访问。生产环境必须通过nginx或oauth2-proxy等反代添加认证层。
3. **双retention同时设置**：`--storage.tsdb.retention.time=30d --storage.tsdb.retention.size=100GB`——哪个先触达就按哪个淘汰，不是同时满足。
4. **WAL回放过慢**：除了启用WAL压缩外，可考虑调大`--storage.tsdb.wal-segment-size`（默认128MB），更大的segment意味着更少的文件数和更低的fsync频率。

### 测试验证

```bash
# 验证配置正确性
promtool check config prometheus.yml && echo "OK" || echo "FAIL"

# 验证健康状态
curl -s -o /dev/null -w "%{http_code}" http://localhost:9090/-/ready
# 期望输出: 200

# 确认自监控规则已加载
curl -s http://localhost:9090/api/v1/rules | jq '.data.groups[] | select(.name=="prometheus_self") | .rules[].name'
# 期望输出: "PrometheusHighSeriesCount" "PrometheusHighQueryDuration" "PrometheusScrapeTargetsDown" ...
```

## 四、项目总结

### promtool命令速查表

| 命令 | 功能 | 典型场景 |
|------|------|---------|
| promtool check config prometheus.yml | 检查配置文件语法 | CI/CD预检、修改配置后验证 |
| promtool check rules rules/*.yml | 检查告警规则表达式 | 新规则上线前验证 |
| promtool test rules test_rules.yml | 单元测试告警规则 | 验证告警逻辑是否符合预期 |
| promtool tsdb list data/ | 列出TSDB block列表 | 查看数据覆盖范围、确认block健康 |
| promtool tsdb analyze data/ | 分析TSDB基数分布 | 排查OOM、容量规划 |
| promtool tsdb bench data/ | 查询性能压测 | 评估硬件升级效果 |
| promtool tsdb dump data/ | 导出block原始数据 | 数据迁移或修复 |

### Prometheus关键HTTP API速查表

| API端点 | 功能 | 运维场景 |
|---------|------|---------|
| /api/v1/query | 即席查询 | 快速检查某个指标值 |
| /api/v1/query_range | 时间范围查询 | 数据分析、趋势观察 |
| /api/v1/targets | 采集目标状态 | 排查target掉线 |
| /api/v1/rules | 规则状态 | 确认告警规则执行正常 |
| /api/v1/status/tsdb | TSDB内部状态 | OOM排查、容量评估 |
| /-/reload | 热加载配置 | 不中断服务更新配置 |
| /-/healthy | 进程存活检查 | K8s liveness probe |
| /-/ready | 服务就绪检查 | K8s readiness probe |

### 常见故障排查SOP

**OOM Kill** → 查看`prometheus_tsdb_head_series`确认基数 → `promtool tsdb analyze`定位高基数指标 → 裁减不必要的label（如UUID/request_id） → 减小`--storage.tsdb.retention.time`降低数据量 → 如必要，扩容内存或拆分为多个Prometheus实例（联邦模式）。

**Scrape Timeout** → `curl /api/v1/targets`查看lastError → 确认目标服务是否延迟高 → 调整`scrape_timeout`（不超过scrape_interval的一半） → 如目标确实慢，将指标预聚合后改用Pushgateway推送。

**磁盘爆满** → `df -h`确认 → `du -sh`定位大目录 → `promtool tsdb list`找最老block → 临时删除旧block释放空间 → 调整`--storage.tsdb.retention.time`或`--storage.tsdb.retention.size` → 添加磁盘使用率告警。

**配置Reload失败** → 执行`promtool check config prometheus.yml` → 修复语法错误 → 重新reload → 检查日志和`/api/v1/targets`确认生效。

### 适用场景

- **日常巡检**：每日检查`prometheus_tsdb_head_series`趋势，防止基数平滑膨胀至危险水位。
- **故障定位**：当监控告警消失或Grafana面板无数据时，通过`/api/v1/targets`快速定位链路断裂点。
- **容量规划**：基于`prometheus_tsdb_storage_blocks_bytes`的增长速率预测磁盘何时需要扩容。
- **合规审计**：TSDB block清单和配置变更记录可作为监控体系可追溯性的证明材料。

### 注意事项

- **Prometheus本身必须被监控**：至少部署一个独立的"监控Prometheus的Prometheus"，或用Thanos/Mimir等方案做高可用。
- **reload配置前必须promtool check**：不要依赖reload的返回值判断成功与否——它永远返回200。
- **WAL和数据目录做好备份**：尤其在升级Prometheus版本前，备份整个data目录是最稳妥的保险措施。

### 常见踩坑经验

**案例1**：某团队在K8s中将liveness probe和readiness probe都指向`/-/ready`。Prometheus启动后开始回放大量WAL，30秒内`/-/ready`返回503，K8s直接Kill并重启Pod，如此循环十几次。修复：liveness用`/-/healthy`，readiness用`/-/ready`，并将`initialDelaySeconds`设为足够大。

**案例2**：某公司配置了`--storage.tsdb.retention.time=365d`想要保留全年数据，但机器只挂了200GB SSD。运行到第4个月磁盘写满，Prometheus崩溃且因为磁盘满无法写入WAL，陷入死循环。修复：改为`--storage.tsdb.retention.size=150GB`，配合远程写入（Remote Write）将长期数据存入VictoriaMetrics。

**案例3**：运维团队在Prometheus机器上配置了磁盘使用率告警，但忘记排除Prometheus自身的数据目录。某天告警响起，运维顺手清理了几个大日志文件，但磁盘使用率没降——原来Prometheus的block占用了总空间的90%。教训：监控规则需要包含数据目录的独立告警项。

### 思考题

1. **一个Prometheus实例最多能支撑多少active series？这个上限由什么决定？**
   （提示：答案不是固定数值，需从内存（每个series约1-3KB）、CPU（采集+规则评估+查询）、磁盘IOPS三个维度分析。生产环境建议上限通常为100万-200万series，此阈值受机器规格和scrape_interval直接影响）。

2. **设计一个Prometheus高可用方案：两个Prometheus实例同时采集同样的target，在Grafana中如何避免数据显示两份？**
   （提示：方案一——Grafana中使用`max()`/`min()`聚合消除重复；方案二——两个Prometheus实例添加不同的`external_labels`，Grafana中按label过滤；方案三——使用Thanos/Cortex等方案，在查询层自动去重。需比较各方案的复杂度与可靠性。）
