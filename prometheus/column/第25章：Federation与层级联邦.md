# 第25章：Federation与层级联邦

## 一、项目背景

某跨国公司拥有北京、上海、新加坡三个数据中心，每个数据中心各部署了一套独立的Prometheus实例，负责采集本地近200个微服务的监控指标。全球运维团队的总部设在北京，他们需要在单一仪表盘中看到所有数据中心的全局视图——包括全球CPU利用率、跨区域请求延迟P99、以及各区域SLA达标率。

如果让北京的Prometheus直接跨太平洋去采集新加坡机房的Node Exporter，结果会怎样？实测数据是残酷的：每个scrape请求的网络往返时间高达2-3秒，大量采集超时（默认10秒），Prometheus的Target列表里一片红。更糟的是，新加坡的运维团队也强烈要求本地拥有独立的监控能力——他们的Grafana不能因为北京到新加坡的光缆被挖断就变成空白页面。

另一个典型场景来自Kubernetes多租户集群：每个namespace部署了自己的Prometheus做租户级隔离，但平台运维团队需要一个全局Prometheus来跨租户做容量规划和资源统计——"到底哪个部门占用GPU最多"这类问题，分散的Prometheus根本无法回答。

Prometheus给出了一个原生的答案：**Federation（联邦）**。它的核心思想很朴素——上层Prometheus不直接采集下层exporter的端点，而是通过`/federate`端点从下层Prometheus**拉取已经聚合过滤后的指标**。联邦机制填补了"全局视图"与"分布式部署"之间的鸿沟，但它并非银弹：数据精度损失、match[]维护成本、以及延迟叠加，都是需要在实践中认真权衡的问题。本章将深入联邦的两种部署模式、match参数设计哲学，以及Federation与Thanos/VictoriaMetrics等方案的选型边界。

---

## 二、剧本式交锋对话

**小胖**：大师，我快被跨机房监控搞疯了！新加坡那边网络三天两头抖动，北京的Prometheus直接去抓新加坡的exporter，timeout率都快30%了。群里有人建议我关掉几个新加坡的采集任务，这显然不对吧？

**大师**：方向对了。让北京的Prometheus跨越太平洋抓exporter，本身就是反模式。Prometheus的联邦机制就是为这个场景设计的——你让新加坡的Prometheus继续在本地采集exporter，然后在总部部署一个"全局Prometheus"，让它通过`/federate`端点从新加坡、上海、北京的三套Prometheus上拉取数据。

**小白**：等等，`/federate`端点？我一直以为Prometheus只有一个`/metrics`端口。它和普通exporter有什么不同？

**大师**：问得好。`/metrics`暴露的是Prometheus**自身**的运行时指标——比如`prometheus_tsdb_head_samples_appended_total`。而`/federate`暴露的是Prometheus**数据库中存储的**时间序列数据。更关键的是，`/federate`必须通过`match[]`参数**显式指定**要拉取哪些指标，不支持自动发现。比如：

```
GET /federate?match[]={__name__=~"up|node_cpu_seconds_total"}
```

只有命中了match[]条件的时间序列才会被返回。如果没有指定match[]，端点返回空响应——不是返回全部，这一点和`/api/v1/query`完全不同。

**小胖**：为什么要这么设计？像普通exporter那样把所有指标一口气全拉回来不就行了？无非就是数据量大一点嘛。

**大师**：你低估了这个"大一点"。一个中等规模的Prometheus实例可能存着**百万条**活跃时间序列。全量通过联邦拉取，意味着全局Prometheus要翻倍存储所有这些数据——这就不是联邦了，这叫"数据备份"。联邦的设计哲学是"**选择性拉取**"：下层Prometheus负责原始采集和Recording Rules预聚合，上层只拉取聚合后的结果。比如下层有`node_cpu_seconds_total`的原始数据，但你只需要全局的CPU利用率，那你就应该只拉`instance:node_cpu_utilization:rate5m`这样的Recording Rule结果。

**小白**：那联邦也有两种部署模式吧？刚才说的全局Prometheus拉三个区域Prometheus，这是哪种？

**大师**：这是**层级联邦**（Hierarchical Federation）——上层汇总下层。还有一个模式叫**横向联邦**（Cross-Service Federation），适合在不同职能部门之间共享指标：比如监控团队有一套Prometheus采集基础设施指标，业务团队有一套采集应用指标，二者通过联邦互相拉取对方关心的那部分数据，地位平级、数据互补。而层级联邦是树状结构——命名空间级Prometheus → 集群级Prometheus → 全局级Prometheus，每一层只拉取下一层聚合后的指标。

**小胖**：我刚才想到一个坑：假设新加坡的Prometheus已经对`node_cpu_seconds_total`算好了rate，全局Prometheus再去拉这个rate值，这中间不是有误差吗？

**大师**：这正是联邦最大的代价——**数据精度损失**。联邦拉取的不是原始samples，而是下层Prometheus已经经过计算（可能是rate、avg_over_time、甚至quantile）的结果。原始数据的时间粒度在下层就丢掉了。更糟的是，拉取间隔还存在**延迟叠加**：下层每30s scrape一次，上层每60s联邦拉取一次，你在全局Grafana上看到的数据，理论上最多有90s的滞后。

**小胖**：那external_labels呢？三家机房各自有`node_cpu_seconds_total`，如果不加区分，全局Prometheus不就分不清谁是谁了？

**大师**：这就是`external_labels`的用武之地。下层Prometheus配置：

```yaml
global:
  external_labels:
    datacenter: singapore
```

然后全局Prometheus的联邦采集任务设置`honor_labels: true`，这样下层打上的`datacenter`标签就会被保留。你在全局写PromQL时，直接`node_cpu_seconds_total{datacenter="singapore"}`就能区分。

**小白**：那联邦和Thanos比呢？我们团队最近也在看长期存储方案。

**大师**：核心区别一句话：**联邦拉的是"聚合结果"，Thanos存的是"原始数据"**。联邦适合的场景是"我只需要全局视图、不需要原始细节"——比如VP看的全球大盘、容量规划报表。Thanos+VictoriaMetrics这类方案适合"我需要查任意时间点的原始数据、需要全局去重、需要长期存储"——比如事故调查时需要回放某个exporter的原始metric曲线到秒级精度。实际生产中，两者经常组合使用：联邦满足日常大盘需求（轻量、成本低），Remote Storage + Thanos满足深度排查和合规存储需求（重但完整）。

---

## 三、项目实战

### 环境准备

- 3个区域Prometheus实例（模拟北京、上海、新加坡）
- 每个区域配一个Node Exporter
- 1个全局Prometheus做联邦拉取
- Docker Compose编排

### 步骤1：搭建三个区域Prometheus实例

`docker-compose.yml`：

```yaml
version: '3.8'
services:
  prometheus-beijing:
    image: prom/prometheus:latest
    volumes:
      - ./prometheus-beijing.yml:/etc/prometheus/prometheus.yml
    ports:
      - '9090:9090'
    command:
      - '--config.file=/etc/prometheus/prometheus.yml'
    networks:
      - monitor

  node-exporter-beijing:
    image: prom/node-exporter:latest
    networks:
      - monitor

  prometheus-shanghai:
    image: prom/prometheus:latest
    volumes:
      - ./prometheus-shanghai.yml:/etc/prometheus/prometheus.yml
    ports:
      - '9091:9090'
    command:
      - '--config.file=/etc/prometheus/prometheus.yml'
    networks:
      - monitor

  node-exporter-shanghai:
    image: prom/node-exporter:latest
    networks:
      - monitor

  prometheus-singapore:
    image: prom/prometheus:latest
    volumes:
      - ./prometheus-singapore.yml:/etc/prometheus/prometheus.yml
    ports:
      - '9092:9090'
    command:
      - '--config.file=/etc/prometheus/prometheus.yml'
    networks:
      - monitor

  node-exporter-singapore:
    image: prom/node-exporter:latest
    networks:
      - monitor

networks:
  monitor:
    driver: bridge
```

`prometheus-beijing.yml`（上海、新加坡同理，替换datacenter标签和node-exporter的hostname）：

```yaml
global:
  external_labels:
    datacenter: beijing

scrape_configs:
  - job_name: 'node'
    static_configs:
      - targets: ['node-exporter-beijing:9100']
```

三个区域的配置差异化：每个实例的`external_labels.datacenter`分别设为`beijing`、`shanghai`、`singapore`，`static_configs`中的target指向本地Node Exporter。

### 步骤2：配置全局Prometheus联邦拉取

新增`prometheus-global`服务和对应的配置文件`prometheus-global.yml`：

```yaml
global:
  external_labels:
    level: global

scrape_configs:
  - job_name: 'federate'
    honor_labels: true
    metrics_path: '/federate'
    params:
      'match[]':
        - '{__name__=~"up|node_cpu_seconds_total"}'
        - '{__name__=~"node_memory_MemAvailable_bytes|node_memory_MemTotal_bytes"}'
        - '{__name__=~"node_filesystem_size_bytes|node_filesystem_avail_bytes"}'
    static_configs:
      - targets:
          - 'prometheus-beijing:9090'
          - 'prometheus-shanghai:9090'
          - 'prometheus-singapore:9090'
        labels:
          role: 'regional'
```

配置要点解析：

- **`honor_labels: true`**：保留下层Prometheus通过`external_labels`打上的`datacenter`标签。如果设为`false`（默认），全局Prometheus会用自己采集任务配置的标签去覆盖，导致`datacenter`信息丢失。
- **`match[]`精心设计**：只拉取了`up`状态、CPU总量、内存总量/可用量、文件系统容量。没有使用`{job=~".*"}`之类的全量匹配——这是联邦最重要的原则："**只拉上层需要的、已聚合的指标**"。一个生产环境的下层Prometheus可能有几十万条时间序列，全量拉取会让全局Prometheus的存储量等同于所有下层之总和，完全背离联邦的设计初衷。
- **显式target列表**：三个区域Prometheus的地址硬编码在`static_configs`中。对于动态环境（如K8s），可以配合HTTP SD或文件服务发现来动态管理。

### 步骤3：验证联邦效果

启动所有容器后，访问全局Prometheus的Web UI（`http://localhost:9093`），执行以下PromQL验证：

```promql
# 1. 查看全球所有区域的UP状态
up{job="federate"}
# 预期输出：3条记录，datacenter标签分别为 beijing / shanghai / singapore

# 2. 计算全局平均空闲CPU比例
avg by (datacenter) (
  rate(node_cpu_seconds_total{mode="idle"}[5m])
)
# 预期：beijing/shanghai/singapore 各一条结果

# 3. 计算北京机房的CPU利用率
100 - (
  avg by (datacenter) (
    rate(node_cpu_seconds_total{mode="idle", datacenter="beijing"}[5m])
  ) * 100
)

# 4. 验证联邦拉取的指标数量
count({job="federate"})
# 此数量应远小于下层Prometheus的指标总量（比如几百条 vs 几万条）
```

停掉上海Prometheus容器后，查询`up{job="federate", datacenter="shanghai"}`，预期在1-2个采集周期后变为0。

### 步骤4：层级联邦实战——三层架构

假设一个多K8s集群场景，我们需要三层联邦：

**L1 — Namespace级别**（如每个租户一个Prometheus）：

```yaml
global:
  external_labels:
    cluster: 'prod-k8s-1'
    namespace: 'tenant-a'
    level: 'namespace'

rule_files:
  - 'ns-recording-rules.yml'
```

其中`ns-recording-rules.yml`预聚合命名空间级指标（如`namespace:pod_cpu_usage:avg5m`），供上层联邦选择拉取。

**L2 — 集群级别**：

```yaml
global:
  external_labels:
    cluster: 'prod-k8s-1'
    level: 'cluster'

scrape_configs:
  - job_name: 'ns-federate'
    honor_labels: true
    metrics_path: '/federate'
    params:
      'match[]':
        - '{__name__=~"namespace:.*"}'
    static_configs:
      - targets:
          - 'ns-prom-a:9090'
          - 'ns-prom-b:9090'
```

**L3 — 全局级别**：

```yaml
global:
  external_labels:
    level: 'global'

scrape_configs:
  - job_name: 'cluster-federate'
    honor_labels: true
    metrics_path: '/federate'
    params:
      'match[]':
        - '{__name__=~"cluster:.*"}'
    static_configs:
      - targets:
          - 'cluster-prom-1:9090'
          - 'cluster-prom-2:9090'
```

数据流向：**原始metrics → Recording Rules聚合 → L1 namespace级别 → Federation拉取 → L2集群级别 → Recording Rules再聚合 → Federation拉取 → L3全局级别**。每一层往上，数据粒度变粗、指标数量成倍减少。

### 步骤5：联邦 vs Remote Storage 选型对比

| 维度 | Federation | Thanos / VictoriaMetrics |
|------|-----------|--------------------------|
| 架构复杂度 | 简单，Prometheus原生支持，无额外组件 | 复杂，需部署Sidecar、Store、Querier等多个组件 |
| 数据延迟 | scrape_interval叠加（通常60s-120s） | 近实时（5s-30s） |
| 数据精度 | 聚合后数据，丢失原始样本粒度 | 保留原始samples，支持秒级回放 |
| 存储方式 | 各自独立本地存储，retention各自管理 | 统一对象存储（S3/GCS），集中管理 |
| 跨机房查询 | 全局Prometheus单点查询 | Thanos Querier对下层Store做分布式查询 |
| 长期存储 | 受本地磁盘限制（通常15-30天） | 支持数年（对象存储成本极低） |
| 全局去重 | 不支持（同一target被多个下层采集会造成重复） | 原生支持去重（Querier自动选取replica） |
| 适用场景 | 全局大盘、层级报表、容量规划 | 事故深度排查、长期存储、全局高可用 |

**实际建议**：联邦满足"有全局视图就行"的需求，Thanos满足"需要任意时间点原始数据"的需求。多数团队走的是分步演进路线——先用联邦快速搭建全局大盘（0额外组件），发现长期存储和深度排查需求后再引入Thanos，两者并不互斥。

### 常见踩坑汇总

1. **match[]忘记同步**：下层新增了一条Recording Rule（如`namespace:new_metric:rate5m`），但全局Prometheus的match[]没有更新，导致该指标在全局不可见。建议：将match[]配置与Recording Rules文件纳入同一CI流程，或使用通配模式`{__name__=~"namespace:.*"}`覆盖所有前缀规范的Recording Rule。

2. **external_labels冲突**：下层设置了`external_labels: {datacenter: "beijing"}`，但全局联邦采集job也配置了`static_configs.labels: {datacenter: "global"}`，且`honor_labels`为`false`，导致所有数据源的`datacenter`都变成了`global`——彻底失去区分能力。规范做法是联邦采集job**不加与下层external_labels同名的标签**，并设置`honor_labels: true`。

3. **联邦拉取频率过高**：将全局Prometheus的`scrape_interval`设为5s，导致下层Prometheus的`/federate`端点频繁被调用。如果match[]匹配了几十万条序列，每次联邦拉取的响应体可能达到上百MB，下层Prometheus的CPU直接打满。建议联邦拉取间隔不低于30s，最好60s-120s。

4. **match[]过于宽泛**：写成`{__name__=~".*"}`等于把下层所有数据搬了一份到上层，存储翻倍、网络压力骤增——这不是联邦，这是灾难性复制。

---

## 四、项目总结

### Federation架构全景

```
┌─────────────────────────────────────────────┐
│          L3: Global Prometheus              │
│   (match[]: cluster:* via /federate)        │
│   external_labels: {level: global}          │
└────┬──────────────────┬─────────────────────┘
     │                  │
┌────▼─────┐      ┌─────▼────┐
│ Cluster  │      │ Cluster  │   ← L2: 集群级
│ Prom-1   │      │ Prom-2   │
│ hon_lbls │      │ hon_lbls │
└──┬───┬───┘      └──┬───┬───┘
   │   │             │   │
┌──▼─┐ ┌▼──┐    ┌───▼─┐ ┌▼──┐  ← L1: 命名空间级
│NS-A│ │NS-B│    │NS-C │ │NS-D│
└────┘ └───┘    └─────┘ └───┘
   ▲     ▲          ▲      ▲
   └──┬──┘          └──┬───┘  ← 原始exporter
   exporters        exporters
```

数据流：底层exporter的原始samples在L1被Recording Rules预聚合为`namespace:*`指标，L2通过Federation选择性拉取并二次聚合为`cluster:*`指标，L3再拉取`cluster:*`形成全局视图。每一层上升，指标基数缩小一个数量级。

### match[]设计原则

1. **只拉上层需要的**：问自己"全局Grafana上需要展示哪些指标"，把这些指标对应的match[]写进配置，其余一概不拉。
2. **粒度越往上越粗**：L1拉原始指标或轻度聚合指标，L2拉`namespace:*` Recording Rules，L3拉`cluster:*`。不要在L3拉`node_cpu_seconds_total`原始数据。
3. **靠命名规范而不是硬编码**：用`{__name__=~"namespace:.*"}`代替逐条列出规则名，减少维护成本。
4. **定期审计**：每个月检查一次联邦match[]，清理不再需要的指标、添加新增需要的指标。

### Federation适用与不适用场景

**适用**：多数据中心全局视图、多租户K8s平台容量规划、跨集群SLA报表、按组织层级逐级汇总的监控看板。

**不适用**：需要查询任意时刻原始数据细节（事故排查）、需要长期高精度存储（合规审计）、需要全局去重（HA部署中同一target被两个Prometheus采集时，联邦会看到两份重复数据）、需要秒级实时告警（延迟叠加可能导致告警滞后1-2分钟）。

### 思考题

1. 如果同一个target同时被两个下层Prometheus采集（高可用部署），联邦拉取后全局Prometheus会看到两份该target的指标。在不改变下层部署的前提下，如何在全局Prometheus中做到去重？提示：考虑在PromQL层面使用`max by`或引入`group_left/excluding`技巧。

2. Federation和Remote Read的区别是什么？联邦是定时"拉"下层聚合数据到本地存储，Remote Read是上层"实时查询"时向Remote Storage发起临时的HTTP查询请求。什么场景下二者可以互补使用？提示：考虑"日常看板"与"按需深查"的不同数据时效需求。
