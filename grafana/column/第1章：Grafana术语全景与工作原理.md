# 第1章：Grafana术语全景与工作原理

## 1. 项目背景

"告警又炸了，钉钉群直接被刷屏，我看都看不懂。"运维组的李明焦急地盯着屏幕上的红色告警列表，"CPU使用率、内存占用、磁盘IO、应用响应时间，这些指标分散在五个不同的工具里，每次排查问题都要切换好几个系统，光打开页面就要好几分钟。"

李明的团队维护着一个日活百万的电商平台，技术栈涵盖Java微服务、MySQL集群、Redis缓存、Kubernetes容器化部署。整个系统有超过200个服务实例，50台虚拟机，8个数据库节点。然而他们的监控方案却是一盘散沙：Prometheus收集指标但没有统一的展示面板，ELK存日志却查询缓慢，各种告警规则散落在不同的配置文件中。

"最要命的是业务部门问'现在系统健康吗'，我根本拿不出一张能看的图。"李明的痛点集中反映了中大型团队在可观测性建设初期面临的典型困境：数据孤岛、视图碎片化、告警泛滥。Grafana正是为了解决这个问题而生——它不是又一个监控工具，而是一个统一的可视化与分析平台。

本章作为专栏开篇，需要先回答三个核心问题：Grafana到底是什么？它内部是怎么运作的？它的核心术语有哪些？只有建立起统一的"语言体系"，后续的学习才能丝滑顺畅。

图中展示了一个典型的请求链路：用户在浏览器中打开Dashboard → Grafana Server（Go后端）接收请求 → 检查认证与权限 → 查找Dashboard定义 → 遍历Panel向DataSource发起查询 → Data Source Proxy转发到Prometheus/MySQL/ES等后端 → 返回数据给前端React渲染 → 呈现可视化图表。整个链路在数百毫秒内完成，背后是Grafana精巧的架构设计。

## 2. 项目设计

**小胖**（嚼着薯片，盯着屏幕发呆）：大师，我坦白说，我就想画几张好看的监控图，结果打开Grafana官网一看，什么Dashboard、Panel、DataSource、Organization、Plugin……光名词就十几个！这不就跟去食堂打饭，明明我就想吃个红烧肉，非得让我知道猪肉产地、厨师工号、灶台温度吗？

**大师**（端起茶杯）：你这个问题问得好。其实你可以把Grafana想象成一家餐厅。DataSource就是后厨的仓库——Prometheus仓库里存的是指标，MySQL仓库里存的是业务数据，Loki仓库里存的是日志。每个仓库有自己独特的"取货方式"，也就是查询语言。

**小白**（从电脑后探出头）：那Dashboard就是客人面前的餐桌咯？把各个仓库拿来的食材摆成一桌子菜？

**大师**：没错，而且还不止于此。Organization相当于整个餐厅大楼，不同团队可以在不同的楼层（Org）里各自摆桌子，互相看不见。Panel就是桌上的每道菜——Time series是红烧肉这种随时间变化的主菜，Stat就是单独一碗汤，Gauge就是气压计一样的水位表。

**小胖**（眼睛一亮）：那Plugin插件就是……可以自己发明一道新菜？

**大师**：准确。如果你觉得Grafana自带的60多种图表还不够，你可以自己写一个Panel插件，画拓扑图、组织架构图、甚至三维图都行。DataSource插件也一样，你想监控某个私有协议的后端，自己写个数据源插件就接入了。

**小白**（若有所思）：那架构层面呢？浏览器打开Dashboard到看到图表，这中间发生了什么？

**大师**：关键链路分四步。第一步，浏览器请求Grafana Server，这是用Go写的后端，负责认证、权限、Dashboard元数据管理。第二步，Go后端根据Dashboard里定义好的Panel，逐个去对应的DataSource取数据——但它不直接连DataSource，而是通过Grafana内置的代理层转发。第三步，DataSource返回数据后，Go后端会把它转换成统一的数据结构：DataFrame——行是时间，列是字段，这就是Grafana的"通用数据语言"。第四步，数据发到浏览器，React前端根据Panel类型选择对应的可视化组件渲染。

**小胖**：等等，那比如我10个Panel都查同一个Prometheus，Grafana会发10次请求吗？

**大师**：好问题！这就是Query Caching的用武之地。如果你开启了查询缓存，同一个查询在缓存TTL内不会重复请求后端，这是性能优化的关键手段。另外，10个查询是并发发送的，不会排队等。

**技术映射**：餐厅比喻对应关系——餐厅大楼=Organization，餐桌=Dashboard，菜品=Panel，仓库=DataSource，自创菜=Plugin，服务员=API Server，送餐路线=请求处理链路。

## 3. 项目实战

**环境准备**

| 组件 | 版本 | 用途 |
|------|------|------|
| Docker | 24.x+ | 容器运行时 |
| Grafana | 11.x | 可视化平台 |
| Prometheus | 2.50.x+ | 指标采集与存储 |
| Node Exporter | 1.8.x | 主机指标采集 |

使用Docker Compose编排所有服务：

```yaml
# docker-compose.yml
version: '3.8'
services:
  prometheus:
    image: prom/prometheus:v2.50.0
    container_name: prometheus
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml
      - prometheus_data:/prometheus
    ports:
      - "9090:9090"
    command:
      - '--config.file=/etc/prometheus/prometheus.yml'
      - '--storage.tsdb.path=/prometheus'
      - '--web.enable-lifecycle'

  node_exporter:
    image: prom/node-exporter:v1.8.0
    container_name: node_exporter
    ports:
      - "9100:9100"

  grafana:
    image: grafana/grafana:11.0.0
    container_name: grafana
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=admin
      - GF_INSTALL_PLUGINS=grafana-clock-panel
    ports:
      - "3000:3000"
    volumes:
      - grafana_data:/var/lib/grafana
    depends_on:
      - prometheus

volumes:
  prometheus_data:
  grafana_data:
```

```yaml
# prometheus.yml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: 'prometheus'
    static_configs:
      - targets: ['localhost:9090']
  - job_name: 'node_exporter'
    static_configs:
      - targets: ['node_exporter:9100']
```

**步骤一：启动环境并验证**

```bash
docker compose up -d
# 访问 http://localhost:3000，用户名/密码：admin/admin
# 访问 http://localhost:9090 确认Prometheus正常运行
# 访问 http://localhost:9100/metrics 确认Node Exporter有数据
```

**步骤二：添加Prometheus数据源**

在Grafana左侧菜单 → Connections → Data Sources → Add data source → 选择Prometheus。填写Prometheus server URL：`http://prometheus:9090`，点击Save & test。如果显示"Data source is working"，说明连接成功。

**步骤三：创建第一个Dashboard并理解术语**

手工创建Dashboard，而不是导入现成的模板，才能理解每个概念。

新建Dashboard → Add visualization → 选择Prometheus数据源。在Query编辑器中输入：
```
rate(node_cpu_seconds_total{mode!="idle"}[5m])
```

点击Run queries，你将看到CPU使用率的时序曲线。此时你创建了：
- 1个Dashboard（仪表盘容器）
- 1个Panel（面板，包含这个图表）
- Panel使用了Prometheus DataSource（数据源）
- 查询中使用了PromQL表达式

**步骤四：添加Stat面板显示当前值**

Add → Visualization → Stat → 输入查询：
```
100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)
```

在Panel右侧设置：Unit → Percent (0-100)，Thresholds → 添加阈值 80（红色）、60（黄色）、0（绿色）。现在你有了一个实时CPU使用率指示器。

**步骤五：添加Gauge面板**

再添加一个Gauge面板，查询内存使用率：
```
(1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) * 100
```

设置Display → Show threshold markers，阈值 90/70/50。

**可能遇到的坑**

1. **Prometheus连接失败**：如果在Docker Compose中使用`localhost:9090`，容器内的Grafana无法访问宿主机localhost。必须使用服务名`prometheus:9090`。
2. **数据为空**：确认Prometheus的scrape_interval已过至少一个周期（15秒），且targets状态为UP。
3. **Grafana密码错误**：首次登录后必须修改密码，如果忘记可以用`grafana-cli admin reset-admin-password`重置。

## 4. 项目总结

**优点**
| 属性 | 说明 |
|------|------|
| 统一可视化 | 一个Dashboard集成多种数据源（Prometheus/MySQL/ES），无需切换工具 |
| 插件生态 | 200+官方/社区插件，自定义扩展门槛低 |
| 权限体系 | Org/Team/Role三级权限，支持LDAP/OAuth/SAML |
| 配置即代码 | Provisioning + Dashboard JSON，完美融入GitOps |
| 开源免费 | AGPLv3协议，核心功能免费，企业版增加高级特性 |

**缺点**
| 属性 | 说明 |
|------|------|
| 学习曲线 | 术语多、概念抽象，新人需要系统学习 |
| 无数据存储 | Grafana不存储业务数据，需搭配数据库使用 |
| 大规模挑战 | 万级Dashboard需要额外的架构优化 |
| 告警不完整 | 告警功能相比专用工具（PagerDuty）仍有差距 |
| 前端重量级 | React SPA首屏加载较慢，需要CDN优化 |

**适用场景**
1. 基础设施监控：CPU/内存/磁盘/网络指标的Dashboard
2. 应用性能监控：QPS、延迟、错误率的RED仪表盘
3. 业务数据可视化：日活用户、订单量趋势、转化率漏斗
4. 日志分析大盘：结合Loki/ES展示错误日志分布
5. 告警值班大屏：TV Mode全屏展示核心KPI

**不适用场景**
1. 实时高频交易系统（毫秒级延迟要求不适合Web渲染）
2. 纯数据仓库报表（更适合BI工具如Superset）

**常见踩坑经验**
1. **Dashboard版本丢失**：多人同时编辑同一Dashboard会覆盖，建议开启版本历史或使用Provisioning管理。
2. **变量查询超时**：变量下拉框查询返回超过1000个值时页面卡死，必须添加过滤条件。
3. **DataSource代理超时**：Grafana默认代理超时30s，查询大数据源时要调大`GF_DATAPROXY_TIMEOUT`。

**思考题**
1. 如果一个Dashboard有20个Panel，每个Panel都查询同一个Prometheus实例，如何优化查询性能？
2. Grafana如何做到数据源与面板的解耦？如果换一个数据源（如从Prometheus换到InfluxDB），面板需要重新配置吗？
