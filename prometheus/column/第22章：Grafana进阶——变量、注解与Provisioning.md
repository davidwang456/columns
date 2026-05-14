# 第22章：Grafana进阶——变量、注解与Provisioning

## 一、项目背景

运维团队的Grafana之路走到了一个瓶颈期。团队已经手工搭建了10张监控大盘，涵盖主机指标、应用QPS、中间件健康度等方方面面。这些大盘在最初的环境里工作得井井有条，可一旦需要迁移到测试、预发布或生产环境，噩梦就开始了——因为数据源名称变了（`Prometheus-Dev` vs `Prometheus-Prod`）、服务标签变了（`order-api-staging` vs `order-api-prod`），运维同事不得不在每张大盘上逐一替换查询语句，重复劳动且极易出错。

更糟糕的事情还在后面。某次Grafana版本升级后，一张核心的"订单服务监控大盘"面板配置全部丢失——因为团队从未做过Dashboard的版本管理，没有导出过JSON备份。大家只能凭借记忆重新拖拽面板，花了整整一个下午才恢复了大盘的大致面貌，但一些精心调整过的告警阈值和面板布局细节却永远丢失了。

另一个日常痛点则与故障定位有关。新来的运维同事小胖想看看上周三上线后CPU使用率的变化趋势——他还记得那次上线发生在下午三点左右，但Grafana的时间选择器只能靠手动拖拽，他反复试了好几次才定位到准确的时间窗口。更让人沮丧的是，每次事故复盘，大家都要在Grafana和变更管理平台之间来回切换：一边看指标曲线，一边翻变更记录，两条时间线割裂开来，无法直观地看到"某个变更事件"与"指标突变"之间的因果关系。

这三个场景分别指向Grafana的三个进阶能力：**Dashboard变量**让一张大盘适配多套环境，告别"单机专属"的笨重模式；**Annotations（注解）**能将部署、扩容、故障等事件以标记线的形式叠加到指标图上，排障效率成倍提升；**Provisioning（代码化配置）**让Dashboard、Datasource乃至Alert规则都通过Git管理、自动部署，团队从此告别手动点击和配置漂移。

这三大能力，正是运维团队从"能用Grafana"走向"用好Grafana"的分水岭。

## 二、剧本式交锋

**小胖**：大师！我按第8章的教程给生产环境配了一套全新的Grafana和Prometheus，但问题来了——我们北京、上海两个机房各有三套环境（测试/预发布/生产），如果给每个组合都手工建一张大盘，算下来要18张！后面再加个广州机房就是27张……这谁顶得住啊？

**大师**：（笑）你这就是典型的"变量缺失症"。Grafana的Dashboard变量就是为这个场景设计的。你只需要建一张大盘，然后定义三个级联变量——`datacenter`（地域）、`cluster`（集群）、`instance`（实例），面板里的PromQL用变量替换硬编码标签。用户在下拉框中切换地域，集群和实例的可选范围会自动联动缩小，所有面板的查询同步刷新，一张大盘打天下。

**小白**：等等，我插一句。你说变量有几种类型来着？我上次在UI里看到有什么Query、Custom、Constant、Interval……

**大师**：四类，各司其职。**Query类型**最常用，直接从Prometheus查询中动态获取标签值，比如`label_values(node_uname_info, datacenter)`就能自动拉取所有地域名称。**Custom类型**适合写死的枚举值，比如地域就那几个，写`beijing,shanghai`最简单。**Constant类型**是固定字符串，通常用来区分环境但又不希望用户改动。**Interval类型**是时间窗口选择器，用户可以动态调整`rate(...[$interval])`中的时间范围。对了，还有**Data link**——它能让你在一个面板点击数值，直接跳转到另一个Dashboard并自动带上变量值，实现"从概览钻取到详情"的效果。

**小胖**：级联变量我理解了。但我还有两个场景一直没搞明白。第一个：每次线上部署，我想在CPU或QPS图上看到"上线标记"，这样一眼就能判断性能抖动是否和部署相关。第二个：Dashboard的配置能不能像代码一样用Git管起来？我受够手工点击了。

**大师**：第一个场景用**Annotations**解决。Grafana支持三种注解来源。最灵活的是**Prometheus查询型**：让你们的发布平台在上线时通过Pushgateway推送一条事件指标——`deployment_event{service="order-api", version="v2.3.1"}`，你只需要在Dashboard的Annotations设置里配一条PromQL，Grafana就会自动在图表上画出垂直线标记，鼠标悬停还能显示版本号和状态。第二种是**Grafana内置事件**，比如Dashboard的创建和修改记录，适合追溯配置变更。第三种是**HTTP API**，外部系统可以直接`POST /api/annotations`向任意面板打标记，比如告警系统触发后又恢复了，就可以通过API自动在图表上留痕。

**小胖**：那Provisioning呢？

**大师**：Provisioning是Grafana的"基础设施即代码"能力。你在Grafana的配置目录下放YAML或JSON文件——`provisioning/datasources/`放数据源定义，`provisioning/dashboards/`放Dashboard的JSON文件——Grafana启动时自动读取并创建这些资源。所有配置纳入Git仓库，上线前走Code Review，变更历史一目了然，回滚就是一个`git revert`的事。这里面有一个关键设计：`editable: false`字段一旦设置，UI就会变成只读，彻底杜绝手工修改带来的配置漂移。当然，调试阶段可以先设成`true`，灵活调整，稳定后再锁死。

**小白**：那Grafana自己也能发告警了，这和Prometheus Alertmanager到底怎么选？

**大师**：好问题。给你一个原则：**需要图表截图作为告警证据、或告警条件依赖复杂的可视化计算**时，用Grafana Alerting；**需要告警抑制、静默、分组路由、长期告警历史分析**时，用Prometheus Alertmanager。两者不互斥，可以互补——Alertmanager处理基础设施级的标准告警，Grafana Alerting处理业务面板级的高级告警。至于Dashboard的版本管理，策略很简单：**JSON导出 + Git + CI/CD自动部署**。每次改完大盘后导出JSON（UI上点"JSON Model"直接复制，或用API `curl /api/dashboards/uid/XXXX` 拉取），提交到Git仓库，然后通过Provisioning机制自动同步到Grafana实例。

## 三、项目实战

### 环境准备

- Grafana运行中（默认端口3000），Prometheus Datasource已配置完成
- Grafana API可用（开发环境默认`admin/admin`，生产环境建议使用API Key）
- Pushgateway可用（用于Annotations演示）

### 步骤1：创建级联变量（地域 → 集群 → 实例）

**场景**：一张大盘适配北京/上海两个地域，每个地域有多个集群，每个集群下有多台实例。

**变量1——datacenter（Custom类型）**：
进入 Dashboard Settings → Variables → New，配置如下：
- Name: `datacenter`
- Type: `Custom`
- Values: `beijing,shanghai`
- 勾选 `Show on dashboard`（在下拉框中可见）

**变量2——cluster（Query类型，依赖datacenter）**：
- Name: `cluster`
- Type: `Query`
- Query: `label_values(node_uname_info{datacenter="$datacenter"}, cluster)`
- Selection Options: Multi-value = ON, Include All option = ON
- 效果：选择beijing时，cluster下拉框中只显示北京地域的集群列表

**变量3——instance（Query类型，依赖前两者）**：
- Name: `instance`
- Type: `Query`
- Query: `label_values(node_cpu_seconds_total{datacenter="$datacenter", cluster="$cluster"}, instance)`
- Selection Options: Multi-value = ON, Include All option = ON

**修改面板PromQL以使用变量**：

```promql
100 - (avg(rate(node_cpu_seconds_total{
  mode="idle",
  datacenter="$datacenter",
  cluster="$cluster",
  instance=~"$instance"
}[5m])) * 100)
```

注意关键细节：多值变量`$instance`（勾选了Multi-value后，变量值可能是`{host1,host2}`）必须使用`=~`（正则匹配运算符），单值变量则使用`=`。这是新手最容易踩的坑——忘记切换运算符导致查询返回空数据。

### 步骤2：配置Annotations实现变更事件标记

**场景**：运维发布平台在上线时通过Pushgateway推送事件指标，Grafana读取并显示为图表上的垂直线标记。

**Step 2.1：通过Pushgateway推送事件指标**

假设Prometheus已集成Pushgateway，每次部署完成后，CI/CD流水线执行：

```bash
echo "deployment_event{service=\"order-api\", version=\"v2.3.1\", status=\"success\"} $(date +%s)" | \
  curl --data-binary @- http://pushgateway:9091/metrics/job/deployments/instance/ops-system
```

这条命令向Pushgateway推送了一条名为`deployment_event`的指标，其值为Unix时间戳（秒），标签记录了服务名、版本号和部署状态。

**Step 2.2：Grafana Annotation查询配置**

在 Dashboard Settings → Annotations → New Annotation Query 中：
- Name: `Deployments`
- Data source: `Prometheus`
- Query: `deployment_event{service=~".*"}`
- Enable: ON
- 字段映射规则：Grafana将查询结果中的`Time`列（取自指标值）映射为标记线的X轴位置，`Tags`映射为`service`标签（可用于过滤），`Text`映射为`version`标签（悬停时显示的文本）
- 点击保存后，图表上每个部署时间点都会出现一条垂直线，鼠标悬停可显示版本号等详细信息

**Step 2.3：进阶——通过Grafana HTTP API直接创建Annotation**

除了依赖Prometheus查询外，外部系统（如告警系统、变更管理平台）也可以直接用Grafana API在指定面板上创建注解：

```bash
curl -X POST http://admin:admin@localhost:3000/api/annotations \
  -H "Content-Type: application/json" \
  -d '{
    "dashboardId": 1,
    "panelId": 2,
    "time": 1705312800000,
    "tags": ["deployment", "order-api"],
    "text": "订单服务升级到 v2.3.1"
  }'
```

**关键坑点**：Grafana API的`time`字段使用**epoch毫秒**，而Prometheus默认是**epoch秒**。如果从Prometheus查询或Pushgateway获取时间戳，需要**乘以1000**，否则标记线会显示在1970年附近。

### 步骤3：Dashboard Provisioning——代码化管理

Provisioning的核心思想是：Grafana启动时自动读取配置目录中的YAML/JSON文件，无需任何人机交互即可创建Datasource和Dashboard。这消除了手工配置的随意性，实现了基础设施的声明式管理。

**推荐目录结构**：

```
grafana/
├── provisioning/
│   ├── datasources/
│   │   └── prometheus.yml
│   ├── dashboards/
│   │   ├── dashboard.yml
│   │   └── json/
│   │       ├── host-monitoring.json
│   │       └── app-overview.json
│   └── notifiers/
│       └── alert-channel.yml
```

**datasources/prometheus.yml——数据源定义**：

```yaml
apiVersion: 1
datasources:
  - name: Prometheus-Prod
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
    editable: false
    jsonData:
      timeInterval: "30s"
```

关键字段说明：`editable: false`禁止UI手动修改，强制所有变更走Git流程。生产环境建议设为`false`，调试阶段可临时设为`true`以便快速验证配置效果。`access: proxy`表示Grafana后端代理请求，适用于Prometheus和Grafana在同一网络内的场景。

**dashboards/dashboard.yml——Dashboard Provider配置**：

```yaml
apiVersion: 1
providers:
  - name: 'default'
    orgId: 1
    folder: ''
    type: file
    options:
      path: /etc/grafana/provisioning/dashboards/json
```

Provider告知Grafana从哪个目录加载Dashboard JSON文件。Grafana会递归扫描该目录下的所有`.json`文件并自动创建对应的Dashboard。`options.path`必须使用**绝对路径**，否则Grafana无法找到文件——这是最常见的Provisioning问题。

**获取Dashboard JSON的方法**：

- **UI方式**：Dashboard Settings → JSON Model → 全选复制
- **API方式**：`curl http://admin:admin@localhost:3000/api/dashboards/uid/<DASHBOARD_UID> | jq '.dashboard' > dashboard.json`
- 将导出的JSON文件放入`dashboards/json/`目录，重启Grafana（或调用reload API）即可生效

### 步骤4：Grafana Alerting的补充说明

Grafana Alerting不需要写PromQL规则文件，直接在UI中基于面板数据定义告警条件，并支持将图表截图作为告警通知的一部分。

**配置流程**：Alert Rule → 选择Prometheus数据源 → 定义条件（例如`cpu_usage > 90`持续5分钟）→ 选择Evaluation Group和间隔 → 配置Contact point（Email/Webhook/钉钉）→ 配置Notification policy（路由规则）。

| 场景 | 推荐方案 |
|------|----------|
| 简单的阈值检测 | 两者均可 |
| 需要图表截图作为告警证据 | Grafana Alerting |
| 复杂的多条件组合 | Prometheus + Alertmanager |
| 需要告警抑制/静默/分组路由 | Prometheus Alertmanager |
| 需要长期告警历史分析 | Prometheus Alertmanager |

### 步骤5：Loki + Prometheus联动下钻

在Prometheus面板上添加Data Link，实现"指标异常 → 一键跳转日志"的排障工作流。

编辑面板 → Data links → Add link，配置如下URL：

```
/explore?left={"datasource":"Loki","queries":[{"expr":"{app=\"${__field.labels.app}\"}"}],"range":{"from":"${__from}","to":"${__to}"}}
```

效果：用户点击某个时间点的异常指标后，Grafana自动跳转到Explore界面，Loki数据源已选中，LogQL查询已填入，时间范围与当前视图完全对齐。这打通了从"发现异常"到"定位日志"的最后一公里，大幅缩短MTTR（Mean Time to Repair）。

### 常见坑点速查

1. **Provisioning中`editable: false`后UI只读** → 调试阶段设为`true`，稳定后再换`false`；或者通过API `PUT /api/dashboards/db` 直接更新
2. **级联变量的Refresh问题** → 父变量变化时子变量默认不刷新。在子变量的Selection Options中必须勾选`Refresh`，确保重新执行Query获取新的可选值列表
3. **API Annotation时间戳** → Grafana用毫秒，Prometheus用秒，从Prometheus取值时必须乘1000
4. **Dashboard JSON跨环境迁移** → JSON中嵌入了`datasource.uid`，迁移到新环境后UID不匹配会导致面板无法加载。建议用Provisioning中的`datasource`名称匹配功能，或在导入时使用`--plugins-dir`参数

### 测试验证清单

- 切换`datacenter`变量，验证集群和实例下拉框联动更新，所有面板数据正确刷新
- 通过API创建一条Annotation（时间设为当前时间前5分钟），验证图表上出现垂直线标记
- 在新的空白Grafana实例中配置Provisioning目录，启动后验证Dashboard和数据源自动创建
- 配置Data Link后，点击面板数值验证是否正确跳转到Loki Explore界面

## 四、项目总结

### 变量类型速查

| 类型 | 用途 | 示例 |
|------|------|------|
| Query | 从数据源动态获取可选值 | `label_values(node_uname_info, datacenter)` |
| Custom | 自定义固定枚举值 | `beijing,shanghai,guangzhou` |
| Constant | 不可改变的固定值 | `production`（区分环境） |
| Interval | 时间窗口选择器 | `1m,5m,10m,30m,1h` |
| Data link | 点击跳转到其他面板/系统 | 配合URL模板实现钻取 |

### Provisioning文件结构模板（可直接复用）

```
provisioning/
├── datasources/prometheus.yml    # 数据源定义
├── dashboards/dashboard.yml      # Provider配置
├── dashboards/json/*.json        # Dashboard JSON文件
└── notifiers/alert-channel.yml   # 告警通知渠道（可选）
```

### 适用场景

- **多环境统一监控**：通过级联变量，一套Dashboard适配开发/测试/预发布/生产所有环境
- **自动化运维平台集成**：Provisioning + Git实现Dashboard的CI/CD流水线，与业务代码一同管理
- **GitOps管理**：所有Grafana配置纳入Git仓库，变更可追溯、可审计、可回滚
- **事件关联分析**：Annotations将部署/扩容/故障等事件与指标曲线叠加，正确定位根因

### 注意事项

1. Provisioning配置变更后需重启Grafana，或通过API `/api/admin/provisioning/dashboards/reload` 热加载
2. Dashboard UID必须全局唯一，建议自定义有意义的UID（如`host-monitoring-v1`）而非使用随机字符串
3. 多环境部署时，`datasource.name`必须统一（例如所有环境的数据源都叫`Prometheus`），否则Dashboard JSON迁移后无法识别
4. 级联变量层级不宜过深（建议不超过3层），每层数据量不宜过大（上千条可选值会导致页面渲染卡顿）

### 常见踩坑经验

**案例1：Provisioning路径错误**。同事将`options.path`写成了相对路径`./dashboards/json`，导致Grafana启动后无法加载Dashboard。排查方法：查看Grafana日志中的`[provisioning.dashboard]`关键字，确认实际扫描路径。**教训**：Provisioning路径必须是绝对路径。

**案例2：Annotation时间戳错位**。使用Prometheus查询结果作为Annotation时间时，忘了将秒转为毫秒，导致标记线全部显示在50年前。排查方法：在Grafana Annotation Query测试面板中预览查询结果，确认Time列的数值量级。**教训**：Grafana全部使用毫秒时间戳。

**案例3：级联变量导致页面卡顿**。在某环境中将`instance`变量获取了全部2000+台主机的hostname，每次展开下拉框都会触发全量查询，页面响应超过10秒。解决：增加一层`service`层级作为中间过滤，确保每级变量的可选值控制在100条以内。**教训**：级联设计中要考虑数据量，必要时增加过滤层级。

### 思考题

1. **如何实现"从应用概览大盘的某个服务QPS面板，点击跳转到该服务的详细监控大盘"？**  
  提示：在概览面板上配置Data links，URL中使用变量传递`${__field.labels.service}`。目标大盘的链接为`/d/SERVICE_DASHBOARD_UID?var-service=${__field.labels.service}`，这样跳转后目标大盘的变量会自动填充为被点击的服务名。

2. **Provisioning中如何通过环境变量区分测试/生产环境的datasource？**  
  提示：Grafana Provisioning的YAML文件支持`${ENV_VAR}`语法引用环境变量。在`prometheus.yml`的`url`字段中使用`${PROMETHEUS_URL}`，不同环境启动容器时设置不同的环境变量值即可。例如：开发环境`PROMETHEUS_URL=http://prometheus-dev:9090`，生产环境`PROMETHEUS_URL=http://prometheus-prod:9090`。这样同一份Provisioning代码可以适配所有环境。

---

本章从Dashboard变量的级联设计、Annotations的事件标记能力，到Provisioning的代码化配置，系统性地介绍了Grafana进阶的三大核心技能。至此，你已具备构建生产级Grafana监控体系的能力。"用好Grafana"，从这里开始。
