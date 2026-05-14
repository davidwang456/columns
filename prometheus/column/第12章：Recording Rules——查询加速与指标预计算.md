# 第12章：Recording Rules——查询加速与指标预计算

## 一、项目背景

运维小林做了一张核心业务监控大盘，上面摆了10个面板。每次打开大盘都要干等30秒以上——因为每个面板里都嵌着一条复杂的PromQL：比如按集群、服务、接口三个维度聚合QPS，需要做多次`sum(rate(...))`再套`label_replace`做维度标准化。老板每次开周例会都要现场切到大屏看大盘，漫长的加载时间让小林恨不得往地缝里钻。更糟糕的是，当他配置告警规则时，发现同样那条PromQL在规则评估时也要被反复计算。Prometheus的CPU使用率从30%一路飙到85%，监控系统成了最大的资源消耗者。

小林的困境折射出一个根本性问题：Prometheus的每一次查询都是**即时计算**——它要从TSDB的原始数据点出发，实时执行rate()、sum()、除法和标签操作，最后才返回结果。如果同一个昂贵的计算被10个面板和3条告警规则反复触发，那就是一笔巨大的重复开销。

Recording Rules正是为这个问题设计的。它是Prometheus内置的"物化视图"机制——把复杂查询的结果预先计算并存储为一条全新的时间序列，后续查询直接读这条新序列而无需重复计算。数据库爱好者可以把Recording Rules理解为TSDB中的Materialized View，用存储空间换取查询时间。

然而，光知道"用Recording Rules"还不够。很多团队兴冲冲地把所有慢查询都写成Recording Rules，结果却反过来把TSDB打爆了——因为没有控制好label基数，一条规则产生了数万条新的time series。命名规范也是一笔糊涂账，有人写`cpu_avg`，有人写`node_cpu_usage_5m_avg_by_instance`，维护者根本猜不出这条指标是什么意思。更关键的是，Recording Rules和Alerting Rules虽然共享同一套评估引擎，但二者的目标、结果形式和设计思路有着本质差异。什么时候该把一条PromQL放入Recording Rules？如何量化"这条查询值不值得pre-record"？本章将逐一解答。

## 二、剧本式交锋对话

**小胖**（看着大盘加载圈疯狂转圈）：大师救命！老板下周一来视察，我这大盘打开要半分钟，他肯定要问我是不是在摸鱼。我听运维群说有个叫"Recording Rules"的东西能加速，可我一搜文档，发现还要写YAML、设计命名、考虑基数——这跟告警规则有什么区别啊？我直接把PromQL拷进去不就行了吗？

**大师**：你这个直觉对了一半。Recording Rules确实是把PromQL拷进去，但"直接拷"是要出大问题的。先从原理上说——Prometheus的规则引擎是一个统一的定时调度器，按`evaluation_interval`周期性触发group内的所有规则求值。**Recording Rule**求值后把结果作为新指标写入TSDB存储；**Alerting Rule**求值后判断是否超过阈值，触发告警通知。两条路用一个引擎、一份配置文件、一套group机制，区别只在于求值后的处理动作。你可以把Recording Rules理解成数据库里的Materialized View——复杂查询的结果提前算好存下来，后续再查就直接读物化视图，不用每次从头算。

**小胖**：物化视图我熟！我们MySQL里就有，每天凌晨跑一个定时任务刷新。那我是不是把大盘里所有慢PromQL都搞成Recording Rules就行了？

**小白**（皱着眉头）：我觉得不行。物化视图也不是见一个建一个——如果只加速了10%，却多占了1GB存储，划不来吧？而且MySQL物化视图刷新一次要锁表，Prometheus这边会不会也有类似的坑？

**大师**（竖起大拇指）：小白问到点子上了。要不要用Recording Rules，核心判断标准是两条：**计算复杂度**和**复用频率**。像`up == 0`这种直接在原始数据上做简单比较的，完全没必要record；但如果是`sum(rate(...)) by (...) / count(...) by (...) * 100`这种多层嵌套的，被多个面板或告警规则重复引用，就应该pre-record。再给你一个量化思路——在Prometheus Web UI的Graph里分别跑原始查询和一条最简单的`up`查询，对比两者的Response Time。如果原始查询耗时是简单查询的5倍以上，且日均查询频率超过100次，就值得考虑Recording Rules。

**小胖**：那我先问问命名的事。我看群里有人写`instance:node_cpu_utilization:avg_rate5m`这种又长又带冒号的格式，是故意的还是装X？

**大师**：这是Prometheus社区的最佳实践，格式是`<scope>:<metric_name>:<operation>_<window>`，冒号是分隔符。第一部分`level`表示聚合维度——`instance`级、`cluster`级还是`job`级。第二部分是原始指标名——`node_cpu_utilization`、`http_requests_total`。第三部分是操作+时间窗口——`avg_rate5m`表示取了5分钟平均速率。这个规范看似啰嗦，实际上是**自文档化**的：任何人看到指标名，立刻就知道这条指标是谁的、怎么算出来的、粒度多大。反过来，如果只写一个`cpu_avg`，三个月后你自己都记不清这是按什么维度、多长时间、用什么函数算的。

**小白**：那Recording Rules对TSDB的影响到底有多大？我印象中每多一条规则就等于多存一条新的时间序列，如果没控制好label，岂不是把TSDB撑爆了？

**大师**：你抓住了最关键的风险点。Recording Rule产生的time series数量，等于expr中`by`或`without`子句所保留的label维度的笛卡尔积。举个极端例子——如果你写了一条`sum(rate(http_requests_total[5m])) by (path)`，但没写by，那结果会保留所有原始label，包括`instance`、`job`、`method`、`status_code`等，每个组合产生一条新序列。假如你有100个instance × 20个method × 10个status_code × 200个path，那就是400万条新序列——TSDB直接原地爆炸。正确做法是显式指定`by (instance)`，结果就只有100条序列。**by/without就是你控制基数闸门的开关**，千万别省略。

**小胖**：那evaluation_interval设多少合适？我看群里有人设5秒，有人设5分钟，差距也太大了吧？

**大师**：这是一个精度和开销的权衡。evaluation_interval越短，预计算结果越接近实时数据，但对Prometheus CPU和内存的消耗也越大。一个经验法则是：**interval通常设为scrape_interval的2倍**。比如你的scrape_interval是15秒，Recording Rule的interval设30秒就够了。设得比scrape_interval还短没意义——数据还没采进来，重复算的是同一批老数据，等于空转。反过来，如果你的Recording Rule用于容量规划（比如计算过去24小时的趋势），interval可以放宽到5分钟甚至更长，因为趋势本身就不需要秒级精度。

**小白**：最后确认一下——Recording Rules和Alerting Rules能放在同一个rule group里吗？还是必须分开？

**大师**：技术上完全可以共存于同一个group。甚至推荐这么做——比如你把`instance:node_cpu_utilization:avg_rate5m`的Recording Rule和引用它的`HighCPUUsage`告警规则放在同一个group里，能保证二者使用相同的评估时间点，避免时间偏差造成的告警抖动。但需注意：一个group内rule是按顺序执行的，如果Recording Rule排在后面、Alerting Rule排在前面，那么第一轮评估时告警规则还读不到预计算数据。所以**Recording Rule要写在同group的最前面**，确保先产出指标、再被后续规则引用。

## 三、项目实战

### 环境准备

确保以下组件已就绪：
- Prometheus运行中（2.45+），且至少1台Node Exporter target状态为UP，已采集超过30分钟
- Prometheus配置文件目录（如`/etc/prometheus/`）下有rules子目录

```bash
mkdir -p /etc/prometheus/rules
```

### 步骤1：识别需要Recording Rules的查询

以经典的CPU使用率查询为例，在Grafana中找出一条加载慢的PromQL：

```promql
100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[5m])) by (instance) * 100)
```

这条查询在每次执行时都要经过以下链路：扫描磁盘上的raw data → 对每个instance的idle模式构建5分钟range vector → `rate()`计算每秒增长率 → `avg() by (instance)`做实例级聚合 → 算术运算转换为使用率。如果10个面板和3条告警规则都在引用它，每次Grafana刷新或规则评估都是一轮完整计算。

在Prometheus Web UI的Graph中分别执行上述原始查询，以及一条简单的`up`查询，用浏览器DevTools的Network面板记录`/api/v1/query_range`的响应耗时。假设原始查询耗时320ms，`up`查询耗时15ms——超过20倍的差异，完全值得做Recording Rule。

### 步骤2：设计并创建Recording Rules

按命名规范设计规则，创建`/etc/prometheus/rules/recording_rules.yml`：

```yaml
groups:
  - name: cpu_recording_rules
    interval: 30s
    rules:
      # CPU使用率（按instance聚合）
      - record: instance:node_cpu_utilization:avg_rate5m
        expr: |
          100 - (
            avg(rate(node_cpu_seconds_total{mode="idle"}[5m])) by (instance)
            * 100
          )

      # 总CPU核数
      - record: instance:node_cpu_count:sum
        expr: count(node_cpu_seconds_total{mode="idle"}) by (instance)

      # 按instance聚合的网络入流量（bps，乘以8将bytes转为bits）
      - record: instance:node_network_receive_bytes:rate5m
        expr: |
          sum(rate(node_network_receive_bytes_total{device!~"lo|docker.*|veth.*"}[5m])) by (instance) * 8
```

**关键点解读：**

- `interval: 30s`：该group内所有规则每30秒评估一次。如果`scrape_interval`是15秒，30秒足以拿到2个新数据点做准确计算。
- `record`：指定新产生的指标名称。严格按照`<scope>:<metric_name>:<operation>`规范命名，一目了然。
- `expr`：与普通PromQL完全一致的表达式。注意`rate()`内部可以包含range vector `[5m]`，这是合法的——Recording Rule的expr在最外层限制的是不能直接返回range vector，但表达式内部的子查询可以自由使用。
- `by (instance)`：**显式指定聚合维度**，确保结果只有instance这一个label。如果省略by，结果将携带所有原始label（包括`cpu`、`mode`等），导致一条rule产生数百条时间序列。

### 步骤3：在prometheus.yml中注册规则文件

编辑`prometheus.yml`，在`rule_files`中引入新规则：

```yaml
rule_files:
  - 'rules/recording_rules.yml'
  - 'rules/host_rules.yml'  # 第9章的告警规则，可共存
```

热加载Prometheus使配置生效：

```bash
curl -X POST http://localhost:9090/-/reload
```

**验证规则是否加载成功：**

访问 **http://localhost:9090/rules**，在页面上应看到`cpu_recording_rules`这个group，下面列出3条Recording Rules，每条状态为绿色的`OK`。

然后在Expression Browser中输入新指标名查询预计算结果：

```promql
instance:node_cpu_utilization:avg_rate5m
```

切换到Graph视图，观察是否有连续的数据曲线。再切换到Table视图，确认每个instance都有一条对应的值。

**性能对比验证：** 在Graph中分别查询原始PromQL和新Recording Rule指标，用浏览器DevTools Network面板记录`/api/v1/query_range`的响应耗时。原始查询通常在100-500ms的量级，而Recording Rule查询因为直接读取已存储的时序数据，耗时通常在10-30ms以内，差异可达10倍以上。

### 步骤4：在Grafana中使用Recording Rules

打开Grafana，编辑现有CPU面板的查询配置。将PromQL从：

```promql
100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[5m])) by (instance) * 100)
```

改为：

```promql
instance:node_cpu_utilization:avg_rate5m
```

保存面板后刷新Dashboard，观察整体加载速度的变化。历史数据越多，加速效果越明显——因为Recording Rule自创建起就在持续预计算，Grafana查询不再需要回放过去的raw data。

此外，由于数据已经预聚合，可以在Grafana中为Recording Rule指标配置更大时间范围的趋势面板（如过去6h、24h、7d的趋势），查询性能不会因时间窗口增大而线性退化。

### 步骤5：Recording Rules的告警复用

Recording Rule产生的指标和普通指标完全等价，可以无缝嵌入告警规则。修改第9章的`HighCPUUsage`告警规则，将expr从原始PromQL改为引用Recording Rule：

```yaml
- alert: HighCPUUsage
  expr: instance:node_cpu_utilization:avg_rate5m > 80
  for: 5m
  labels:
    severity: warning
  annotations:
    summary: "实例 {{ $labels.instance }} CPU使用率过高"
    description: "当前值: {{ $value | printf \"%.1f\" }}%，已持续5分钟"
```

**好处：** 告警规则评估时不再需要重复执行`rate()`和`avg()`计算，直接从TSDB读取已聚合的`instance:node_cpu_utilization:avg_rate5m`指标，做一次简单的大于比较即可。评估效率大幅提升，Prometheus CPU消耗降低。

使用`promtool`验证规则有效性：

```bash
promtool check rules rules/recording_rules.yml
promtool check rules rules/host_rules.yml
```

### 可能遇到的坑

**坑1：忘记写`by`导致label基数爆炸。** 初学者最常见的错误是直接在expr里用`sum(rate(...))`而不加`by`或`without`。结果是Recording Rule产出的新指标携带了所有原始label——包括`mode`、`cpu`、`job`等——每个label值组合都会生成一条独立的time series。比如100台机器×8个CPU模式，瞬间800条序列。**排查方法：** 在Prometheus Web UI查询`count({__name__=~"instance:.*"})`查看Recording Rule产生的序列总数，减去预期值即为"意外的基数膨胀"。

**坑2：rule group的interval设得太短。** 如果Recording Rule的interval小于scrape_interval（比如scrape_interval=30s，interval=10s），意味着每10秒评估一次规则，但数据源每30秒才更新一次。中间的两次评估其实是在重复计算同一批数据——浪费CPU但产出完全相同的值。**解决方案：** interval ≥ scrape_interval，推荐设为scrape_interval的2倍。

**坑3：expr中`[5m]`在最外层不合法的误解。** 有同学认为Recording Rule的expr不能包含range vector selector。实际上，`rate()`函数内部使用`[5m]`是完全合法的，因为rate()的返回值是instant vector。只是expr整体的结果必须可以写入TSDB（必须是instant vector类型），不能在**最外层**直接返回一个range vector（如`node_cpu_seconds_total[5m]`），这在语法上就不成立。

### 测试验证

```bash
# 1. 查询Recording Rule指标，确认返回有label（instance）的值
curl -s "http://localhost:9090/api/v1/query?query=instance:node_cpu_utilization:avg_rate5m" | jq '.data.result[0].metric'

# 2. 确认规则状态为OK
curl -s "http://localhost:9090/api/v1/rules?type=record" | jq '.data.groups[].rules[] | {name: .name, health: .health}'

# 3. 在浏览器DevTools Network中记录/api/v1/query_range耗时
#    分别对比原始PromQL和Recording Rule的响应时间
```

## 四、项目总结

### Recording Rules vs Alerting Rules 对比

| 维度 | Recording Rules | Alerting Rules |
|------|----------------|---------------|
| 目的 | 预计算复杂查询，加速后续查询 | 判断指标是否异常，触发通知 |
| 结果形式 | 新时间序列写入TSDB | 告警事件推送至Alertmanager |
| 配置关键字段 | `record`（新指标名） | `alert`（告警名） + `for` + `annotations` |
| 对TSDB影响 | 每条规则产生新序列，增加存储与内存开销 | 不产生新序列（告警状态存于内存中） |
| 是否必需 | 可选，优化手段 | 可选，但生产环境强烈建议配备 |
| 可共存于同一group | 是，且建议Recording Rule排在Alerting Rule之前 | 是 |

### 命名规范最佳实践

| 层级 | 格式 | 示例 | 含义 |
|------|------|------|------|
| 实例级 | `instance:<metric>:<op>_<window>` | `instance:node_cpu_utilization:avg_rate5m` | 单台机器的CPU使用率 |
| 任务级 | `job:<metric>:<op>_<window>` | `job:http_requests_total:rate5m` | 按服务聚合的QPS |
| 集群级 | `cluster:<metric>:<op>_<window>` | `cluster:node_memory_usage:max_avg1h` | 跨集群的聚合指标 |
| 全局级 | `global:<metric>:<op>_<window>` | `global:api_error_ratio:rate5m` | 全局跨数据中心聚合 |

### 什么时候用/不用的决策

**应该使用Recording Rules的场景：**
- Grafana大盘加载慢，面板中多次使用相同或相似的复杂PromQL
- 多个告警规则引用同一条昂贵PromQL（如CPU/内存使用率告警）
- 需要跨大时间窗口聚合的查询（如过去30天的日均QPS趋势）
- 需要为容量规划建立历史基线（预计算的聚合指标数据更紧凑，查询更快）
- CK/ETL类场景：需要按`hour`或`day`维度对原始指标做二次聚合

**不应该使用Recording Rules的场景：**
- 简单查询（`up`、`scrape_duration_seconds`、单个函数+短窗口），开销不值得
- 高基数场景（expr结果可能产生数千甚至数万条新序列），尤其是没写by/without的时候
- 临时性、一次性查询（按需用HTTP API查一次即可，没必要持久化存储）
- 查询结果依赖"当前时刻"的快速变化（Recording Rule有评估延迟，不如直接查raw data实时）

### 常见踩坑经验

**案例1：忘记写by导致基数爆炸。** 某团队写了一条`sum(rate(http_requests_total[5m]))`的Recording Rule，没有加by子句。由于`http_requests_total`原始携带了`instance`、`job`、`method`、`path`、`status_code`五个label，结果产生了2000+条time series。Prometheus内存占用一周内翻了三倍，TSDB compaction压力剧增。修复方法：显式写`by (job, path)`将基数控制在50以内。

**案例2：interval过短导致CPU飙升。** 有人为了追求"实时"，把Recording Rule的interval设为5秒，同时写入了30条复杂度较高的规则。结果Prometheus Server的CPU使用率从20%飙升到95%，规则评估开销甚至超过了原始查询的开销，本末倒置。修复方法：将interval统一调至30秒，CPU使用率回落至35%，查询加速效果丝毫不减。

**案例3：Recording Rule指标名与原始指标重名导致混淆。** 某团队让Recording Rule的记录名称和原始指标名相似度极高（如`node_cpu_seconds_total:rate5m`和`node_cpu_seconds_total`），新人在Grafana面板中选错指标，查询结果与预期不符，排查了一下午才发现是指标引用错了。修复方法：严格遵循`<scope>:<metric_name>:<operation>`三层命名规范，让record指标名与源指标名有清晰的视觉区分。

### 思考题

**1.** 如果一条Recording Rule的expr使用了`by (status_code, method, path)`，而每个label维度分别有`5`、`4`、`200`个不同的值，最终会产生多少条time series？这对TSDB的存储和查询性能有什么影响？

**2.** Recording Rules能否跨Prometheus实例共享？假设你有两个独立的Prometheus实例（一个负责采集、一个负责告警），能否在采集实例上执行Recording Rules，让告警实例直接从采集实例读取预计算结果？为什么？如果一定要实现"共享"，有哪些替代方案？

---

> **下一章预告：** 第13章将介绍Pushgateway——短生命周期任务监控，解决CronJob和批处理作业的监控难题。
