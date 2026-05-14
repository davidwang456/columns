# 第8章：Grafana可视化——从图表到大屏

## 一、项目背景

运维小刘最近很头疼。自从用Prometheus + Node Exporter把公司30台服务器的指标都采上来了，数据倒是有了，可每次给老板汇报都得手工截图、拼Excel，折腾半小时就为了一张"过去7天QPS趋势"的图表。更痛苦的是，开发团队想看应用层面的监控——QPS、延迟、错误率——小刘只能在Prometheus Web UI里一条一条跑PromQL，再把结果手动汇总。

压垮小刘的最后一根稻草是一次线上故障。凌晨两点，服务响应变慢，老板在群里问"哪台机器出问题了"，小刘对着Prometheus的Graph页面一台台查CPU、内存、磁盘，足足花了10分钟才定位到那台磁盘写满的机器——因为他根本没有一张全局健康大盘，只能靠猜。

问题说到底，Prometheus自带的Web UI太基础了。它只能做单条PromQL查询，画一张孤零零的折线图；面板之间不能联动，没有变量过滤，也不能把多个指标放在同一张画布上对比。更要命的是，Prometheus Web UI没有"收藏Dashboard"的概念，每次都得重新敲PromQL。归根到底，Prometheus是一个**时序数据库+告警引擎**，不是可视化工具。它的强项是存数据和算指标，"画图"这件事，得交给更专业的人——Grafana。

Grafana是监控领域事实上的可视化标准。它支持几十种数据源（Prometheus、InfluxDB、Elasticsearch、MySQL等），提供Time series、Stat、Gauge、Table、Bar chart等十几种面板类型，支持Dashboard变量、多面板联动、告警规则、团队协作和代码化配置。但新手面对这些功能往往一头雾水：面板类型怎么选？Dashboard变量怎么配？为什么从社区导入的1860号大盘（Node Exporter Full）图表一片空白？Grafana告警和Prometheus Alertmanager是什么关系？本章就带你用Grafana把一堆枯燥的PromQL变成一张能"镇住全场"的运维大屏。

---

## 二、剧本式交锋对话

**小胖**（抓耳挠腮）：大师救命！我用Prometheus采了一个月数据了，但每次老板要报表我都得截图拼Excel，昨天还被吐槽"不够专业"。我听说Grafana是专门做可视化大屏的，这玩意儿和Prometheus到底是什么关系啊？

**大师**（放下茶杯）：你问到了一个核心问题。Prometheus和Grafana的关系，用一句话总结——**Prometheus是"颜料库"，Grafana是"画家"**。Prometheus负责采集和存储时序数据，提供PromQL查询接口；Grafana不存数据，它只负责从Prometheus（以及其他数据源）里拉数据，然后画成各种漂亮的图表。

**小白**（若有所思）：所以Grafana本质上是一个"数据可视化代理"？那我怎么让Grafana连接到我的Prometheus呢？

**大师**：通过配置**Data Source**。登录Grafana后，进Configuration → Data Sources → Add data source → 选Prometheus，填一个URL就行。这里有个关键配置叫**Access模式**，有两个选项：`Server`和`Browser`。选`Server`意味着所有查询请求由Grafana后端代理转发，浏览器不直接访问Prometheus——这在你用Docker部署的时候特别重要，因为浏览器跑在你本机，访问不到Docker内网的`prometheus:9090`，但Grafana容器可以。选`Browser`的话，浏览器直连Prometheus，适用于Prometheus暴露公网地址的场景。**生产环境一律用Server模式**，既安全又免跨域。

**小胖**：明白了。那Grafana里一堆面板类型——Time series、Stat、Gauge、Table、Bar chart——我什么时候该用哪个？

**大师**：好问题，记住四条原则就够了。**Time series（折线图）**适合"看趋势"——CPU使用率变化、QPS波动、网络流量曲线，X轴是时间。**Stat（单个数值）**适合"一眼看现状"——当前在线用户数、今天总请求量、磁盘使用率百分比，只显示一个数字，干净利落。**Gauge（仪表盘）**适合"看占比和健康度"——内存使用率、CPU温度，用颜色阈值（绿/黄/红）直观表达"正常/警告/危险"。**Table（明细表）**适合"逐项排查"——列出所有磁盘分区的使用率、每个Pod的资源用量排行。至于Bar chart，本质上是分类对比，比如"各个机房的QPS对比"。选面板其实就一句话：**趋势用Time series，快照用Stat，健康度用Gauge，排障用Table**。

**小白**：等等，我导入了一个社区大盘，ID是1860（Node Exporter Full），结果大部分面板都没数据，怎么回事？

**大师**：哈哈，新手必踩的坑！社区Dashboard是某个作者在他自己的环境里做的，里面硬编码了标签值。比如`instance="$node:9100"`这种变量，你的环境里instance可能是`10.0.1.5:9100`格式。你得做两件事：第一，检查Dashboard Variables里变量的Query表达式是否匹配你Prometheus里的标签结构；第二，确认每个面板的**数据源**指向了你的Prometheus——有时候导入后面板的数据源会变成"Mixed"或"-- Grafana --"。另外社区大盘的UID（数据源唯一标识符）可能和你的不一致，需要在Dashboard JSON里手动替换，或者直接在数据源设置里把UID改成大盘期望的值。

**小胖**：说到变量，Grafana那个Dashboard顶部下拉框是怎么做的？我看社区大盘可以按"主机名"、"业务线"切换数据。

**大师**：那是Dashboard Variables，本质上是**把PromQL里的硬编码值变成动态选择框**。比如你原来写`node_cpu_seconds_total{instance="10.0.1.5:9100"}`，改成`node_cpu_seconds_total{instance=~"$instance"}`之后，`$instance`就变成了一个变量，它的可选值由你定义的Query决定——比如`label_values(node_cpu_seconds_total, instance)`会从Prometheus里查出所有instance标签的可选值，渲染成一个下拉框。用户切换下拉框，所有的面板里的PromQL中的`$instance`都会跟着变，这就是**多面板联动**的底层机制。注意一个坑：如果变量启用了Multi-value，你PromQL里的运算符得从`=`改成`=~`，不然正则匹配会失效。

**小胖**：那Grafana自己也支持告警，和Prometheus的Alertmanager有什么区别？我该用哪个？

**大师**：分工很明确。**Grafana告警**适合"大盘上肉眼可见的异常"——比如折线图突然掉底了、Stat数值飙红了，你在Grafana里直接配告警规则就很自然，省得来回切工具。它还能带截图发通知，给钉钉/企业微信发报警时附一张图表，特别直观。**Prometheus Alertmanager告警**适合"规则驱动的复杂阈值逻辑"——比如"错误率连续5分钟高于5%"、"磁盘预计4小时写满"，这种需要多条件组合、预测计算、多级路由分派的场景，Prometheus的Recording Rule + Alertmanager更专业。一句话：**简单的、可视化的告警用Grafana；复杂的、多条件的、需要分组降噪的告警用Alertmanager**。两者可以共存，互不冲突。

---

## 三、项目实战

### 环境准备

假设你已按前几章用Docker Compose跑起了Prometheus + Node Exporter，现在在同一个`docker-compose.yml`里加入Grafana：

```yaml
grafana:
  image: grafana/grafana:latest
  container_name: grafana
  ports:
    - "3000:3000"
  volumes:
    - grafana-storage:/var/lib/grafana
  networks:
    - monitor-net
```

启动后浏览器访问`http://localhost:3000`，默认账号`admin/admin`，首次登录会要求改密码。

### 步骤1：配置Prometheus数据源

登录Grafana后，左侧菜单 → **Configuration**（齿轮图标） → **Data Sources** → **Add data source**。

在搜索框输入"Prometheus"，选中后进入配置页：

- **Name**：`Prometheus`（这个名字后面Dashboard会引用）
- **URL**：`http://prometheus:9090`（Docker Compose同一网络内直接用容器名）
- **Access**：选择`Server`（Grafana后端代理，避免浏览器跨域）

其他保持默认，点击底部的**Save & Test**。看到绿色提示`Data source is working`即配置成功。

> **为什么Access选Server？** 你的浏览器跑在宿主机上，`http://prometheus:9090`这个地址浏览器根本解析不了（除非你改了hosts）。选Server模式后，Grafana后端容器去请求Prometheus容器，浏览器只和Grafana通信，完美避开跨域问题。

### 步骤2：创建第一张监控面板——CPU使用率

左侧菜单 → **Create**（+号） → **Dashboard** → **Add new panel**。

进入面板编辑器，默认选中的就是**Time series**面板（折线图），这正是我们需要的。在下方的PromQL查询框中输入：

```promql
100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[5m])) by (instance) * 100)
```

这条PromQL的含义：计算每台主机过去5分钟内CPU非空闲时间的平均百分比，`mode="idle"`的占比被100减去后就是使用率。

配置面板属性（右侧面板）：

- **Panel options → Title**：设为`CPU使用率`
- **Legend**：在右侧面板的Legend设置中，将Mode改为`Table`，Values选择`Last`、`Mean`、`Max`，这样图例区域会以表格形式显示每台主机的最新值、平均值和峰值
- **Graph styles → Legend display mode**：选择`Table`，并确保`{{instance}}`出现在Legend值中

配置Y轴单位（右侧面板 → Standard options）：

- **Unit** → 搜索`Percent`，选择`Percent (0-100)`
- **Min**：设为`0`
- **Max**：设为`100`

点击右上角**Apply**，此时Dashboard上出现了第一张面板——每台主机的CPU使用率趋势折线图。

点击顶部工具栏的保存图标，Dashboard名称填`主机监控`，保存。

### 步骤3：构建完整监控视图

回到Dashboard，点击**Add → Visualization**继续添加面板。

#### 内存使用率面板（Gauge类型）

PromQL查询：

```promql
(1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes) * 100
```

在右侧面板中，将**Visualization**类型从Time series切换为**Gauge**。

配置阈值颜色（右侧 → Thresholds）：

- 添加阈值：`70`设为绿色，`90`设为黄色，`90以上`自动变红
- Thresholds mode：选择`Absolute`（绝对值模式）

配置单位：**Unit → Percent (0-100)**，Min=0，Max=100。

面板标题改为`内存使用率`，Apply保存。

#### 磁盘使用率面板（Stat类型）

```promql
(1 - node_filesystem_avail_bytes{fstype!~"tmpfs|squashfs"} / node_filesystem_size_bytes) * 100
```

注意这里用`fstype!~"tmpfs|squashfs"`过滤掉了临时文件系统，只保留真正有意义的磁盘分区。

Visualization类型选择**Stat**。在右侧Value mappings中添加色彩映射：值`> 80`显示为红色背景，起到醒目警告作用。Stat面板默认显示聚合后的单一值，如果要按分区展示多个Stat，需要在PromQL中加上`by (instance, mountpoint)`分组，并配合Grafana的**Repeat by variable**功能（高级用法，本章不展开）。

面板标题：`磁盘使用率`。

#### 网络流量面板（Time series双曲线）

入站流量PromQL：

```promql
rate(node_network_receive_bytes_total{device!~"lo|docker.*"}[5m]) * 8
```

出站流量PromQL（点击下方的**+ Add query**增加查询B）：

```promql
rate(node_network_transmit_bytes_total{device!~"lo|docker.*"}[5m]) * 8
```

乘以8是将Byte/s转为bit/s（bps），网络流量习惯用bit/s表示。在Standard options中，**Unit → Data rate → bits/sec**，Grafana会自动做单位换算（Kbps/Mbps/Gbps）。

面板标题：`网络流量`。

#### 系统负载面板（Time series三线对比）

添加三条Query：

```promql
node_load1
node_load5
node_load15
```

每条Query的Legend名称分别设为`1min`、`5min`、`15min`，对比系统负载的短期、中期、长期变化趋势。面板标题：`系统负载`。

### 步骤4：Dashboard变量——多主机自由切换

现在Dashboard显示的是所有主机的聚合或全部曲线，如果想只看某台主机怎么办？手动改每个面板的PromQL加`instance="xxx"`？太傻了。用Dashboard变量。

Dashboard顶部的齿轮图标 → **Settings** → **Variables**（左侧菜单）→ **Add variable**。

配置如下：

- **Name**：`instance`（变量名，后续PromQL中用`$instance`引用）
- **Type**：`Query`
- **Data source**：选择`Prometheus`
- **Query**：填`label_values(node_cpu_seconds_total, instance)`
- **Multi-value**：开启（允许同时选多台主机）
- **Include All option**：开启（增加"All"选项，汇总查看所有主机）

点击Update，Dashboard顶部出现了一个`instance`下拉框，里面列出了所有Node Exporter上报的instance值。

接下来修改每个面板的PromQL，将隐式的"所有instance"变成显式的`$instance`变量过滤。以CPU面板为例：

```promql
100 - (avg(rate(node_cpu_seconds_total{mode="idle", instance=~"$instance"}[5m])) by (instance) * 100)
```

注意这里用的是`=~`（正则匹配）而不是`=`，因为当用户在下拉框中选中多台主机时，`$instance`会被展开为正则表达式`host1|host2|host3`，用`=`就匹配不上了。

同理修改其他面板，逐一加上`instance=~"$instance"`过滤条件。保存Dashboard后测试：在顶部下拉框选一台主机，所有面板数据联动切换为仅该主机；选择All，恢复全部主机视图。

### 步骤5：导入社区大盘并适配

Grafana社区维护了大量开箱即用的Dashboard模板，网址是 [grafana.com/dashboards](https://grafana.com/dashboards)。最经典的是ID为**1860**的"Node Exporter Full"大盘，集成了CPU、内存、磁盘、网络、系统信息等几十个面板。

导入步骤：左侧菜单 → **Dashboards → Import** → 输入`1860` → Load → 在底部数据源下拉框选择你的`Prometheus` → Import。

导入后你很可能会发现：**大量面板没有数据**。

这是因为社区大盘的作者在他的环境里做的模板，里面用到了特定的变量名和标签格式。你需要逐项排查：

1. **检查Dashboard变量**：进Settings → Variables，看`job`变量的Query是不是`label_values(node_uname_info, job)`，如果不是，改成与你环境匹配的表达式。`node`变量的正则提取逻辑也可能需要调整。
2. **检查面板数据源**：有些面板导入后数据源显示为`Mixed`或空白，需要手动在每个面板的Query检查器里把数据源重新选为你的Prometheus。
3. **标签格式差异**：社区大盘假设instance标签是`hostname:9100`格式，你的环境可能是`IP:9100`。这种情况下大盘里依赖`label_replace()`做hostname提取的Transform就会失效，需要按你实际的标签结构改写。

适配完毕后，一张专业的Node Exporter全栈监控大屏就呈现在你面前了——这比你自己从零画几十个面板快十倍。

### 可能遇到的坑

1. **变量多值与PromQL运算符**：启用Multi-value的变量，PromQL中必须用`=~`而非`=`。当变量值为All时，Grafana默认展开为`.*`（匹配所有），如果你的PromQL对All有特殊处理需求，需要在变量的Custom all value中显式指定。
2. **Legend模板变量的显示**：Time series面板中Legend若设为`{{instance}}`，Table视图下会显示Prometheus返回的原始标签值（带端口号），如果觉得太长，可以用`label_replace()`或Grafana的Transform做字符串截取。
3. **社区大盘的数据源UID**：Grafana用数据源的UID（而非名称）来关联面板和数据源。如果你的Prometheus数据源UID不是大盘期望的值（常见是`prometheus`），可以在数据源设置里把UID改成大盘匹配的值，或者用文本编辑器全局替换Dashboard JSON中的`"datasource":{"type":"prometheus","uid":"xxx"}`。

### 测试验证

1. 打开`主机监控`Dashboard，切换顶部的`instance`变量，确认所有面板数据随下拉框联动更新。
2. 将右上角时间范围设为`Last 1 hour`，观察折线图是否连续（无断线、无数据空缺），说明采集链路稳定。
3. 最终Dashboard布局建议：第一行放CPU使用率（Time series，全宽）、第二行放内存（Gauge）+ 磁盘（Stat）并排、第三行放网络流量（Time series）+ 系统负载（Time series）并排。

---

## 四、项目总结

### 面板类型选型指南

| 面板类型 | 适用场景 | 典型例子 |
|---|---|---|
| **Time series** | 看趋势、对比时序变化 | CPU使用率曲线、QPS波动、接口延迟分位数 |
| **Stat** | 一眼看当前值、强调关键KPI | 当前在线用户数、今日订单量、磁盘使用率% |
| **Gauge** | 看健康度、占比可视化 | 内存利用率、CPU温度、任务完成进度 |
| **Table** | 逐项排障、排序对比 | 各分区磁盘使用率排行、慢查询Top10、Pod资源用量 |
| **Bar chart** | 分类对比、非时序聚合 | 各机房QPS柱状对比、按HTTP状态码统计请求量 |

选择的底层逻辑：**数据是否具有时间维度？** 有→Time series；没有但需要看比例→Gauge；没有但需要看具体数值→Stat；没有但需要明细排行→Table。

### Grafana告警 vs Prometheus Alertmanager告警

| 维度 | Grafana告警 | Prometheus Alertmanager |
|---|---|---|
| 配置入口 | Dashboard面板上直接配 | Prometheus配置文件（rules文件） |
| 适用场景 | 图表可见的异常（折线断崖、数值飙红） | 多条件复合规则、预测型告警 |
| 通知方式 | 支持截图附件、钉钉/企微/邮件 | 支持分组、抑制、静默、路由分发 |
| 规则复杂度 | 较低，一个面板一个规则 | 高，支持Recording Rule预处理 |
| 推荐用途 | 运维大屏的一线告警 | 核心服务SLO告警、分级通知体系 |
| 局限 | 面板删除则告警消失，不易版本管理 | 无截图，排查需要跳转Grafana看图表 |

**一句话总结分工：Grafana告警解决"看到了就告"的问题，Alertmanager解决"算到了就告"的问题。**

### 适用场景

- **运维大屏**：几十台服务器的CPU/内存/磁盘/网络一张Dashboard搞定，配好变量后按主机、机房、业务线自由切换。老板路过时看到满屏绿色，安心走开。
- **应用监控**：QPS、延迟P99、错误率、上游依赖健康度，结合Prometheus的Histogram/Summary指标，搭建RED（Rate-Errors-Duration）方法论的应用监控大盘。
- **业务指标**：订单量、支付成功率、用户活跃度，用Stat面板展示核心数字，Time series展示趋势，形成业务健康看板。
- **容量规划**：磁盘使用率增长趋势、内存泄漏排查，用Time series的长期视图（Last 30 days），观察资源消耗的增长斜率。

### 注意事项

- **Access模式**：Server模式适用绝大多数场景（Docker/K8s/内网），Browser模式仅在Prometheus公网可访问且无跨域限制时使用。
- **JSON导出兼容性**：Dashboard导出为JSON时，版本号（schemaVersion）很重要——Grafana大版本升级后旧版JSON可能无法直接导入，建议先在同版本测试环境验证。
- **变量引用语法**：`$var`（基本引用）、`${var}`（防止变量名和后续字符粘连）、`${var:regex}`（高级用法，从标签值中提取子串做二级过滤），三者要区分清楚。特别是在PromQL正则中引用变量，要注意`$instance`展开后的正则是否合法（比如值中包含`.`或`-`等正则特殊字符）。
- **Transform慎用**：Grafana的Transform功能可以在展示前对数据做二次处理（过滤、合并、排序、计算），但它运行在Grafana前端，数据量大时会拖慢Dashboard加载速度。能通过PromQL解决的问题，尽量在查询层搞定。

### 常见踩坑经验

**案例一：Grafana时区导致图表偏移。** 某天运维发现监控大盘显示"凌晨3点CPU飙高"，但实际那个时间点没人上班。排查发现Grafana默认使用UTC时区，图表上显示的时间比北京时间晚8小时。解决：Grafana配置文件中设置`default_timezone = browser`，让图表跟随用户浏览器时区。

**案例二：变量全选时PromQL正则错误。** 小刘给`$instance`开了Multi-value和All option，面板上选了All后图表直接变红报错。排查发现他的PromQL写的是`instance="$instance"`，当All时Grafana把变量展开为`.*`，而`=`遇正则就报错。正确写法是`instance=~"$instance"`，让`.*`作为正则被正常执行。

**案例三：版本升级后面板不兼容。** Grafana从8.x升到10.x后，部分使用AngularJS插件（如旧版Pie Chart）的面板显示为空白。解决：升级前检查Dashboard中是否有标记为`Angular (deprecated)`的面板，提前替换为React版的新面板类型。

### 思考题

1. **如何用Grafana实现"单击某台主机的CPU面板，下钻到该主机的进程级别CPU详情"？**  
   提示：考察Dashboard Links和变量传递机制。在面板的General设置中配置一个Link，URL指向另一个Dashboard（例如进程监控大盘），并将当前Dashboard的`$instance`变量作为URL参数传递过去，目标Dashboard接收参数后自动过滤出该主机的进程级指标。

2. **Grafana的Provisioning功能如何实现Dashboard和Datasource的代码化管理？**  
   提示：Grafana支持通过YAML/JSON配置文件自动加载数据源和Dashboard，路径为`/etc/grafana/provisioning/datasources/`和`/etc/grafana/provisioning/dashboards/`。配合Git版本控制和CI/CD，可以实现"配置即代码"，新环境启动时Grafana自动加载所有数据源和Dashboard，无需手动配置。
