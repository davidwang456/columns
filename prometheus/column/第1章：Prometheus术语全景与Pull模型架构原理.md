# 第1章：Prometheus术语全景与Pull模型架构原理

## 一、项目背景

晚上十一点，运维老张刚准备下班，手机突然狂震——客服群里炸了锅："订单页面一直转圈，客户投诉爆了！"老张赶紧登上VPN，先查Nginx日志看不出明显异常，再看应用日志也没报错，最后登录服务器发现订单服务的CPU和内存都正常，但所有API请求都在超时。三个小时后，他才摸到根因：订单服务线程池耗尽，进入"假死"状态——进程活着，端口开着，健康检查通过，就是不处理任何请求。

这家电商公司在过去两年里，从一套Spring Boot单体应用拆成了50多个微服务。服务间调用链错综复杂，每个团队各自维护自己的日志和监控——有的用Zabbix，有的用ELK，有的索性只靠`tail -f`看日志。没有统一监控视图，故障发现靠"客户投诉驱动"。这次事故让CTO下了死命令：一个月内必须建成统一监控体系。

技术选型会议上，团队列出了三个候选方案：

| 特性 | Zabbix | Nagios | Prometheus |
|------|--------|--------|------------|
| 数据模型 | 基于主机/模板 | 基于主机/服务 | 多维标签模型 |
| 采集方式 | Pull + Agent | Pull + Agent | Pull + Exporter |
| 存储引擎 | RDBMS | 扁平文件 | 自研TSDB |
| 动态发现 | 弱 | 弱 | 强（K8s/Consul等） |
| 水平扩展 | 困难 | 困难 | 原生联邦集群 |
| 生态适配 | 传统运维 | 传统运维 | 云原生标配 |

最终Prometheus胜出，不仅因为它已是CNCF毕业项目、Kubernetes事实标准，更核心的原因在于它的**Pull模型**——监控端主动去目标拉取数据，而非等目标推送。在微服务架构中，这意味着监控系统天然具备了服务健康探测能力：如果Prometheus能拉到数据，说明服务是活的；如果拉不到，直接告警。Push模型中，你永远不知道是"服务挂了所以没推送"还是"推送通道堵塞了"——这是运维视角下的根本性差异。

## 二、剧本式交锋对话

会议室里，大师在白板上画了一个圈，旁边又画了几十个散落的小圈。

**小胖**：（嚼着薯片）"这不就跟食堂打饭排队一样吗？一堆窗口（服务），一个打菜阿姨（监控），阿姨挨个问'今天做了几个菜'——为啥要搞这么复杂？"

**大师**："比喻不错。但食堂里阿姨是Push——每个窗口把菜单报上来；Prometheus是Pull——阿姨主动走到每个窗口看。区别在哪？如果窗口的大师傅晕倒了，Push模型下阿姨收不到菜单，还以为是今天歇业；Pull模型下阿姨走过去发现没人，立刻知道出事了。"

> **技术映射**：Prometheus的**scrape（抓取）**每15秒主动向每个**target（目标）**发起HTTP请求，获取`/metrics`端点数据。每个target由一个唯一标识 **instance（实例）** 和逻辑分组 **job（作业）** 组成。拉取失败 = 服务不可达 = 触发告警。

**小白**：（托着下巴）"那如果监控阿姨自己忙不过来呢？50个微服务，每个可能多副本，一秒上千个指标点，HTTP请求排队怎么办？有没有比全量抓取更轻量的方案？"

**大师**："这个问题问得好。第一，Prometheus的TSDB存储引擎专门做了优化——新数据先写入内存中的**Head Block**，同时异步写**WAL（Write-Ahead Log，预写日志）**保证数据不丢，真正落盘由后台**Compaction**（压实）定期执行，把多个时间块合并成大块，减少碎片。第二，如果你担心指标量太大，可以用**recording rule（记录规则）**提前预聚合，比如把`sum(rate(http_requests_total[5m]))`的结果提前算好存下来，查询时直接拿聚合值。"

> **技术映射**：**TSDB**（Time Series Database，时序数据库）是Prometheus的心脏，核心三部分：Head Block存最新数据（内存）、WAL防崩溃丢失（磁盘顺序写）、Compaction做冷数据合并与降采样（磁盘批量整理）。

**小胖**：（举手）"等等，你说的那些Exporter是什么？是保安吗？每个地方站一个？"

**大师**：（笑）"比保安好用。**Node Exporter**把Linux机器的CPU、内存、磁盘变成指标暴露出来；**业务Exporter**是你自己写的，比如订单服务暴露`order_total{status="paid"}`。本质上，任何能输出`/metrics`的HTTP服务都是Exporter。还记得面包店吗——Node Exporter是门外传感器（机器指标），业务Exporter是内部台账（业务指标）。"

**小白**：（若有所思）"那临时跑一次的批处理任务怎么办？它跑完就退了，Prometheus还没来得及拉取呢。"

**大师**："这就是**Pushgateway**的存在价值。批处理任务活着的时间比抓取间隔还短，所以它主动把指标push到Pushgateway这个中转站，Prometheus再来从Pushgateway拉。但要记住——Pushgateway只适用于短生命周期任务，长期运行的服务绝对不要用，否则就成了Push模型，丢失了Pull模型的核心优势。"

> **技术映射**：**Exporter（导出器）**将第三方系统的指标转换为Prometheus可读格式。**Pushgateway（推送网关）**解决短任务指标采集问题，是Pull模型对Push场景的唯一妥协。

**小胖**：（挠头）"那触发了告警，谁来发短信打电话？总不能自己叫吧？"

**大师**："**Alertmanager**登场。Prometheus评估**alerting rule（告警规则）**，比如'订单错误率 > 5% 持续5分钟'，满足条件就把告警推给Alertmanager。Alertmanager负责去重、分组、静默、路由——比如同一台机器上的CPU和内存同时告警，合并成一条通知发给运维，而不是连发十条吓死人。"

**小白**："还有个问题——数据存在TSDB里，怎么画图？"

**大师**："Grafana。Prometheus自带简易UI，但Grafana才是专业可视化。PromQL查询TSDB里的**time series（时间序列）**——每一条都是`指标名 + 一组标签`唯一确定的数据流，里面的每个点叫**sample（采样点）**——一个`(timestamp, value)`对。你画出来的每根线就是一个time series。"

> **技术映射**：**metric（指标）**是测量对象（如http请求数），**label（标签）**是维度（如`method="GET", status="200"`），**time series**是metric + label的笛卡尔积结果，**sample**是时间线上的一个数据点。Grafana通过PromQL查询sample并渲染图表。

**小胖**：（总结）"所以我懂了——Prometheus就是个勤劳的抄表员，每家每户敲门抄表（scrape），回来记在本子上（TSDB），看谁家电表转太快就打电话（Alertmanager→告警）。"

**大师**："没错。但你还漏了最重要的一点：这个抄表员从来不等人把账单送过来（Push），必须自己去敲门（Pull）。这个设计选择，决定了Prometheus的整个架构哲学。"

## 三、项目实战

本章不需要安装任何软件，我们的目标是用**纸笔或draw.io**画出Prometheus的完整架构，并把概念固化到团队Wiki。

### 步骤1：绘制Prometheus整体架构图

请打开draw.io（或拿出一张A4纸），按以下描述从中心向外绘制：

**中心组件：Prometheus Server**
画一个最大的矩形框在画面中央，标注"Prometheus Server"。内部再画三个小框：
- 左侧：**TSDB**（时序数据库，存所有sample）
- 右侧：**HTTP Server**（对外暴露API和UI）
- 底部：**Rule Evaluator**（规则评估引擎）

**数据来源层（左侧）**，从上到下画三个框，箭头都指向Prometheus Server：
- **Service Discovery**：画一个齿轮图标，连线标注"发现Target列表"。列出子项：Kubernetes、Consul、File SD（基于文件的服务发现）
- **Exporters**：画多个小方框，标注"Node Exporter（机器指标）"、"业务Exporter（自定义指标）"。连线标注"HTTP GET /metrics, scrape每15s"
- **Pushgateway**：画一个菱形（代表中转），标注"短任务→Push→Pushgateway←Pull←Prometheus"

**告警通道（上方）**，箭头从Prometheus Server指向：
- **Alertmanager**：画一个框，内部标注"去重→分组→静默→路由→邮件/钉钉/PagerDuty"

**可视化层（右侧）**，箭头从Prometheus Server指向：
- **Grafana**：画一个仪表盘图标，标注"PromQL查询→Dashboard渲染"

**关键数据流箭头标注**，请在连线上写：
1. Exporter → Prometheus Server：`scrape`（拉取/metrics）
2. Prometheus Server → TSDB：`store`（存入时序数据库）
3. Rule Evaluator ⇄ TSDB：`evaluate`（读取数据，评估规则）
4. Rule Evaluator → Alertmanager：`alert`（告警推送）
5. Prometheus Server → Grafana：`visualize`（可视化查询）

**最终效果**：一个以Prometheus Server为中心的星型架构图，左侧是数据来源（Service Discovery + Exporters + Pushgateway），上方是告警出口（Alertmanager），右侧是可视化出口（Grafana）。

### 步骤2：编写团队术语表Wiki

在你的团队Wiki（Confluence/语雀/飞书文档）创建一个页面，标题为《Prometheus术语表》，内容如下：

| 术语 | 一句话解释 |
|------|-----------|
| **Metric（指标）** | 可测量的数值对象，如`http_requests_total`表示HTTP请求总数 |
| **Label（标签）** | 指标的附加维度，`{method="GET", status="200"}`用于区分同一指标的不同子项 |
| **Time Series（时间序列）** | metric名称与一组label值唯一确定的数据流，如`http_requests_total{method="GET", status="200"}` |
| **Sample（采样点）** | 时间序列上某个时刻的具体数值，形如`(t=1715678900, v=42.5)` |
| **Scrape（抓取）** | Prometheus主动发起HTTP GET请求，从target的`/metrics`端点获取指标数据 |
| **Target（目标）** | 被监控的对象，由IP:Port唯一标识，如`10.0.1.5:9100` |
| **Instance（实例）** | 一个target的具名标识，通常等于`host:port` |
| **Job（作业）** | 一组功能相同的instance的逻辑分组，如`job="order-service"` |
| **Exporter（导出器）** | 将第三方系统的监控数据转为Prometheus指标格式的代理程序 |
| **Pushgateway（推送网关）** | 短期任务的指标中转站，任务主动push，Prometheus再来pull |
| **Recording Rule（记录规则）** | 预先计算并存储复杂PromQL查询的结果，加速后续查询 |
| **Alerting Rule（告警规则）** | 定义触发告警的条件，如`expr: rate(errors[5m]) > 0.05` |
| **Alertmanager** | 接收告警、去重分组、静默路由，最终通知到人 |
| **WAL（预写日志）** | TSDB中先于内存写入磁盘的顺序日志，保证crash后数据可恢复 |
| **Head Block** | TSDB的内存块，存储最近2小时的最新数据，查询性能最高 |
| **Compaction（压实）** | TSDB后台将多个小时间块合并为大块的压缩整理过程 |

### 步骤3：编写第一个采集任务配置

在Prometheus的配置文件`prometheus.yml`中添加如下采集任务：

```yaml
global:
  scrape_interval: 15s       # 全局默认抓取间隔：每15秒拉取一次
  evaluation_interval: 15s   # 规则评估间隔：每15秒检查一次告警/记录规则

scrape_configs:
  - job_name: 'order-service'               # job名称，一组实例的逻辑分组
    scrape_interval: 10s                    # 可覆盖全局值，订单服务更频繁采集
    static_configs:
      - targets:
          - '10.0.1.10:8080'                # instance 1
          - '10.0.1.11:8080'                # instance 2
          - '10.0.1.12:8080'                # instance 3
        labels:
          env: 'production'
          team: 'order-team'
```

**关键参数解释**：

- **`scrape_interval`**：Prometheus去抓取`/metrics`端点的频率。设得太短（如1s）会导致双方面临过高的CPU和网络开销；设得太长（如5min）可能错过短期峰谷。生产环境建议15s~60s。
- **`evaluation_interval`**：评估recording rule和alerting rule的频率。与scrape_interval独立——哪怕数据每15s采集一次，你也可以每30s才评估一次告警规则。通常设为与scrape_interval相同。
- **`static_configs`**：手动列出所有target的IP和端口。适合机器数量少且不变的场景，机器多了要用Service Discovery动态发现。

### 步骤4：文字描述完整监控链路

请对着你画的架构图，按照以下链路顺序向团队讲解一遍：

> **步骤①** 你的订单服务在`/metrics`端点用Prometheus客户端库暴露指标，如`http_request_duration_seconds_count{method="POST", status="200"}`。
>
> **步骤②** Service Discovery模块从Kubernetes API或Consul获取到所有订单服务Pod的IP列表，更新Target列表。
>
> **步骤③** Prometheus Server按照`scrape_interval`（这里每10秒），对每个target发起HTTP GET请求，获取`/metrics`的文本内容并解析。
>
> **步骤④** 解析得到的每个sample首先写入Head Block（内存），同时追加到WAL（磁盘）。Head Block中的数据即时可查。
>
> **步骤⑤** 每**evaluation_interval**（每15秒），Rule Evaluator从TSDB读取最近数据，评估告警规则。假设规则是`rate(http_errors_total[5m]) > 5`，如果满足条件，生成一条告警推送给Alertmanager。
>
> **步骤⑥** Alertmanager收到告警后，检查是否有重复（同一告警多次触发）、是否在静默期（维护窗口）、按告警标签分组（同一机器多条告警合并）。最终通过钉钉机器人推送："[prod] 订单服务错误率 8.2% > 5%，持续5分钟，影响3台实例。"
>
> **步骤⑦** Grafana中配置的Dashboard每分钟刷新，通过API向Prometheus发起PromQL查询，获取最近15分钟的`rate(http_request_duration_seconds_count[15m])`数据，绘制成折线图展示在运维大屏上。
>
> **步骤⑧** 2小时后，Compaction后台任务将Head Block中的数据落盘，与更早的数据块合并压缩，释放内存。

**可能遇到的坑**：

1. **术语混淆：metric ≠ time series**。新手常说"我有一个metric叫`http_requests_total`"，但实际上`http_requests_total`只是一个名称，真正存储的是`http_requests_total{method="GET", status="200"}`这样的time series。一个metric下有N条time series，N = 各label取值数的乘积。如果`method`有4种、`status`有5种，那`http_requests_total`下面实际是20条time series。

2. **Label cardinality（基数）失控**。如果把`user_id`或`request_id`设成label，那time series数量会爆炸。Prometheus官方建议单个label的取值数不超过100，所有label组合的总time series不超过1000万。基数爆炸会导致TSDB内存溢出、查询变慢，是生产中最常见的踩坑点。

## 四、项目总结

### 优点与缺点对比

| 维度 | Prometheus | Zabbix | Nagios |
|------|-----------|--------|--------|
| **数据模型** | 多维标签，灵活查询 | 主机模板，维度固定 | 主机/服务，平面化 |
| **存储引擎** | 自研TSDB，高效时序 | MySQL/PostgreSQL，依赖外部DB | 扁平文件，扩展困难 |
| **云原生适配** | K8s原生支持，自动发现Pod | 需插件，体验差 | 无原生支持 |
| **水平扩展** | 联邦集群 + Thanos/Cortex | 代理 + 节点，架构重 | 分布部署，管理复杂 |
| **告警能力** | 灵活PromQL + Alertmanager | 触发器 + 告警动作 | 基础阈值告警 |
| **部署复杂度** | 单二进制，开箱即用 | Server + Agent + DB + Web | Server + Agent + Plugin |

### 适用场景

1. **微服务监控**：多维label天然适配服务名、版本、实例等多维度筛选，PromQL的聚合能力能轻松计算服务级QPS、错误率、P99延迟。
2. **Kubernetes监控**：作为CNCF生态核心，Prometheus能自动发现Pod、Service、Node，开箱即用地采集容器和集群指标。
3. **基础设施监控**：Node Exporter覆盖CPU、内存、磁盘、网络，配合Blackbox Exporter做拨测，覆盖传统运维需求。
4. **业务指标监控**：在代码中埋点即可暴露业务指标（订单量、支付成功率、库存量等），产品和运营也能统一看Grafana大盘。
5. **CI/CD Pipeline监控**：在Pipeline中推送构建时长、成功率、部署频率指标到Pushgateway，实现DevOps全链路可观测。

### 不适用场景

1. **高可靠事件日志**：Prometheus不做持久化设计，样本保留期通常15天。如果需要审计级别的完整事件记录，应选择Elasticsearch或Loki。
2. **需要ACID事务的指标存储**：TSDB不保证强一致性，写入是追加式不可变更的。如果业务需要高频更新某条记录的精确值（如账户余额），该用关系型数据库。

### 注意事项

- **Pull模型对网络的硬要求**：Prometheus必须能网络直达每个target的`/metrics`端点。跨VPC、跨防火墙场景需要提前规划网络打通方案，或采用Push Proxies等辅助工具。
- **时序数据库的不可变性**：数据一旦写入，无法修改。这意味着错误标签的值一旦落库就无法修正（除了等它过期被compaction清理），上线前务必校验标签取值。

### 常见踩坑经验

**案例1：标签基数爆炸导致OOM**
某团队将HTTP请求的`user_id`设为label，一个月后time series达到800万条，Prometheus内存从2GB飙到32GB后OOM。
根因：`user_id`取值无限增长。解决：改为`histogram`统计分布，`user_id`只在日志中保留。

**案例2：scrape_interval与业务周期冲突**
某支付服务定时任务每分钟执行一次，每次执行耗时30秒。但Prometheus的scrape_interval也是60秒，导致每次抓取都在任务刚启动时（指标全为0），运维看到的QPS永远为0。
根因：抓取频率与业务节奏脱节。解决：把scrape_interval改为15秒，覆盖完整执行周期。

**案例3：Alertmanager静默规则误配**
某团队在周五设置了一个"周末维护静默"规则，标签写错成`env="prod"`而非`env="staging"`。周六生产环境MySQL宕机6小时无人知晓。
根因：静默规则标签过于宽泛。解决：静默规则必须精确匹配，且设置到期自动解除时间，团队需建立告警确认制度。

### 思考题

1. **Prometheus为什么选择Pull而不是Push？** 提示：从故障检测可靠性、集中控制权、安全策略（谁有权推送数据？）三个角度分析。
2. **Label的设计对查询性能和存储有什么影响？** 提示：考虑label数量与time series基数的指数关系，以及PromQL中label筛选对扫描范围的影响。

---

*下一章预告：Prometheus的安装部署与启动流程详解——从二进制文件到第一个`up`指标。*
