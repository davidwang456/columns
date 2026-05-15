# 第8章：变量与模板化Dashboard

## 1. 项目背景

"我们开发环境、测试环境、预发布环境、生产环境各有一套Prometheus，运维要求每个环境都有一套监控大盘。我现在是4个Dashboard，每个Dashboard里30个面板，每次要修改一个面板就得改4遍。有没有办法一个Dashboard适配所有环境？"

全栈工程师思思正在被"Dashboard维护噩梦"折磨。她的团队刚完成微服务拆分，从原来的5个服务变成了30个服务。如果按传统方式——每个服务建一个Dashboard——就是30个Dashboard。再乘以4个环境，总共120个Dashboard。任何一个面板的修改都要在多个Dashboard上重复操作，不仅低效，而且容易出现"生产改了测试没改"导致的监控盲区。

Grafana的变量系统正是为解决这个问题而生。通过变量，一个Dashboard可以动态适应不同的环境、服务、集群、命名空间。变量不仅让Dashboard数量从N×M降到1，还能通过重复面板（Repeat）实现"一个模板，自动展开为N个面板"的批量管理能力。

但变量系统功能强大也意味着复杂度高——变量类型有8种，变量之间的依赖关系、查询性能、默认值处理、URL传递，每一个细节都可能成为坑。本章将通过"一套Dashboard统治所有环境"的实战路线，逐一击破变量系统的核心应用场景。

## 2. 项目设计

**小胖**（摊在椅子上）：大师，我快疯了。公司有20个微服务，每个微服务在4个环境部署——开发、测试、预发布、生产。老板要求每个服务都得有独立的监控大盘。我掐指一算，20×4=80个Dashboard！这要是哪天改一个面板配置，我是不是要改80次？

**大师**（笑了）：你这个问题的解法叫"模板变量"。Grafana的变量就像一个占位符——你设计一个Dashboard模板，把环境名、服务名这些变化的部分做成变量，使用时通过下拉框选择即可。80个Dashboard直接变成1个。

**小白**（眼睛一亮）：具体怎么实现？

**大师**：我们从简单到复杂。先看三种最常用的变量类型。

**Custom变量**：手动维护选项列表。比如环境变量，创建一个名为`$env`的Custom变量，值填`dev,staging,prod`。面板的PromQL查询里写`up{env="$env"}`，当你从下拉框选"prod"时，实际执行的是`up{env="prod"}`。

**Query变量**：下拉选项从数据源动态查询。比如服务名变量，创建`$service`变量，Query填写`label_values(http_requests_total, service)`。Grafana会去Prometheus查询`http_requests_total`指标中所有`service`标签的值，自动填入下拉框。服务增加或下线，下拉框自动更新。

**Data source变量**：切换数据源。如果你的开发环境用本地Prometheus，生产环境用远程Prometheus，创建一个`$datasource`变量，类型选`Data source`，可选择特定类型的全部数据源实例。

**小胖**（举手）：等等，我有个疑问。我的Dashboard不是一次只查一个服务，而是要"全部服务"的汇总视图，同时又能切换到"单个服务"的详情视图。这种需求怎么办？

**大师**：这正是`Include All option`的用武之地。在变量设置中开启`Include All option`，自定义All的值——通常设为`.*`（正则匹配所有）。然后在PromQL里用`=~`操作符：`up{service=~"$service"}`。这样选"All"时匹配所有服务，选"order-svc"时只匹配order-svc。

**小白**：那如果我有三个变量，环境→集群→实例，形成一个三层级联，怎么保证选了环境后集群下拉框只显示该环境的集群？

**大师**：这就涉及变量的级联刷新。假设你有`$env`、`$cluster`、`$instance`三个变量。`$cluster`的Query要引用`$env`：`label_values(node_uname_info{env="$env"}, cluster)`。`$instance`引用`$cluster`：`label_values(node_cpu_seconds_total{cluster="$cluster"}, instance)`。

然后在`$cluster`和`$instance`的`Selection options`中设置`Refresh`为`On variable change`——这样当`$env`变化时，`$cluster`自动重新查询并刷新下拉框。

**小胖**：这个"Refresh"还有"On time range change"，跟"On variable change"有什么区别？

**大师**：这个区分很重要。"On time range change"意思是每次调整Dashboard的时间范围（比如从6h改成1h），变量重新查询。适合那些下拉选项会随时间范围变化的变量（比如只看最近1小时有数据的实例）。

"On variable change"只在上游变量变化时刷新，不受时间范围影响。适合那些与时间无关的选项（如环境列表、集群列表）。

**小白**：变量还有一个Repeat功能，我一直没太懂。

**大师**：Repeat是变量系统的杀手级功能。举个例子——你有一个变量`$instance`，包含3个值：server01、server02、server03。光切换看太麻烦，你想同时看到3台服务器的同一个监控视图。

在面板的Repeat options中，选择`Repeat by variable = $instance`，方向选择Horizontal。Grafana会自动复制这个面板3份，每份用不同的`$instance`值。这样你只要设计1个面板，就能得到N个面板。

更高级的用法是Repeat Row——整行面板按变量值复制。这样你可以设计一套"服务监控模板行"，然后按服务名Repeat，每个服务占一行，每行包含QPS/延迟/错误率三个面板。

**小胖**：我还有一个实操问题。Dashboard的URL很长，里面带了变量值。我把URL发给同事，他能看到和我一样的视图吗？

**大师**：这正是变量URL传递的价值。当你选择`$env=prod`和`$service=order-svc`时，浏览器URL会变成：
```
/d/dashboard-uid?var-env=prod&var-service=order-svc
```
把这个URL发给同事，他打开的Dashboard会自动选择prod环境和order-svc服务。这在告警通知中特别有用——告警消息里带一个Dashboard URL，受害者点开直接定位到问题服务。

**技术映射**：Query变量 = 智能推荐菜单（根据库存动态更新），Repeat = 复印机（一份模板复制多份），Include All = 筛子的"全部"选项（不筛选）。URL参数传递 = 书签（标记特定视图状态）。

## 3. 项目实战

**环境准备**

继续使用之前的Docker Compose环境。为模拟多环境场景，修改Prometheus配置：

```yaml
# prometheus.yml 添加多环境标签
global:
  external_labels:
    env: 'dev'

scrape_configs:
  - job_name: 'node_exporter_dev'
    static_configs:
      - targets: ['node_exporter:9100']
        labels:
          env: 'dev'
          cluster: 'beijing'

  - job_name: 'node_exporter_staging'
    static_configs:
      - targets: ['node_exporter:9100']  # 复用同一exporter模拟不同环境
        labels:
          env: 'staging'
          cluster: 'shanghai'

  - job_name: 'node_exporter_prod'
    static_configs:
      - targets: ['node_exporter:9100']
        labels:
          env: 'prod'
          cluster: 'guangzhou'
```

注意：生产环境不建议同一个exporter打多个job的label，这里仅为演示变量功能。

**步骤一：创建基础变量体系**

创建Dashboard → Settings → Variables → Add variable。

**变量1：env（环境）**
| 属性 | 值 |
|------|-----|
| Name | `env` |
| Type | `Custom` |
| Values separated by comma | `dev, staging, prod` |
| Selection options → Include All option | ✅ |
| All value | `.*` |
| Preview of values | dev, staging, prod |

**变量2：cluster（集群）**
| 属性 | 值 |
|------|-----|
| Name | `cluster` |
| Type | `Query` |
| Data source | Prometheus |
| Query | `label_values(node_uname_info{env=~"$env"}, cluster)` |
| Regex | (留空) |
| Sort | Alphabetical (asc) |
| Refresh | On variable change |

测试级联：在Dashboard顶部选择`env=dev`，cluster下拉框应只显示dev环境的集群；切换到`env=prod`，cluster下拉框更新。

**变量3：instance（实例）**
| 属性 | 值 |
|------|-----|
| Name | `instance` |
| Type | `Query` |
| Query | `label_values(node_cpu_seconds_total{env=~"$env",cluster=~"$cluster"}, instance)` |
| Multi-value | ✅（允许同时选多个实例）|
| Include All option | ✅ |
| Refresh | On variable change |

**变量4：cpu_mode（CPU模式）**
| 属性 | 值 |
|------|-----|
| Name | `cpu_mode` |
| Type | `Query` |
| Query | `label_values(node_cpu_seconds_total, mode)` |
| Multi-value | ✅ |
| Refresh | On time range change |

**步骤二：设计变量驱动面板**

**面板1：CPU使用率（Stat，带环境变量）**
```promql
100 - (avg(rate(node_cpu_seconds_total{env=~"$env",cluster=~"$cluster",instance=~"$instance",mode="idle"}[5m])) * 100)
```
当env=All时，`env=~".*"`匹配所有环境，显示全部环境的总CPU使用率。

**面板2：各环境CPU对比（Time series）**
```promql
100 - (avg by (env) (rate(node_cpu_seconds_total{instance=~"$instance",mode="idle"}[5m])) * 100)
```
Y轴单位：Percent (0-100)。不同环境的线用不同颜色自动区分。

**面板3：Repeat by instance——内存使用率**

创建单个Gauge面板：
```promql
(1 - (node_memory_MemAvailable_bytes{instance=~"$instance"} / node_memory_MemTotal_bytes{instance=~"$instance"})) * 100
```

Panel → Repeat options：
- Repeat by variable：`instance`
- Repeat direction：`Horizontal`
- Max per row：`4`

选择2个实例后，4个面板自动生成（每行最多4个）。

**面板4：Repeat by cpu_mode——CPU各模式组成**

创建Time series面板：
```promql
rate(node_cpu_seconds_total{instance=~"$instance",mode=~"$cpu_mode"}[5m])
```

Repeat by `cpu_mode`，每个mode独立一个面板。

**步骤三：变量联动的高级场景**

**场景：仪表盘下拉框联动**

```promql
# 变量 chain 链
# $namespace → $deployment → $pod
# namespace = label_values(kube_namespace_created, namespace)
# deployment = label_values(kube_deployment_created{namespace="$namespace"}, deployment)
# pod = label_values(kube_pod_info{namespace="$namespace", created_by_name="$deployment"}, pod)
```

设置namespace的Refresh=Never（手动刷新），deployment和pod的Refresh=On variable change，实现级联。

**场景：Ad hoc filter（即席过滤器）**

创建变量：
| 属性 | 值 |
|------|-----|
| Name | `Filters` |
| Type | `Ad hoc filters` |
| Data source | Prometheus |

使用时在Dashboard顶部会出现动态添加条件的界面，用户可以任意添加`env=prod`、`instance!=server03`等任意过滤条件，所有面板自动应用。

**场景：Constant变量（固定常量）**

如果你有一个魔法数字需要在多个面板的查询中使用（如SLO阈值99.9%），创建Constant变量：
| 属性 | 值 |
|------|-----|
| Name | `slo_target` |
| Type | `Constant` |
| Value | `99.9` |

面板查询中引用`$slo_target`，修改时只改变量一处即可。

**步骤四：Dashboard保存与URL分享**

创建一个有意义的Dashboard URL，包含预设变量：

```
http://localhost:3000/d/dashboard-uid/host-monitor?var-env=prod&var-cluster=guangzhou&from=now-1h&to=now
```

参数说明：
- `var-env=prod`：预设env变量为prod
- `var-cluster=guangzhou`：预设cluster变量
- `from=now-1h&to=now`：预设时间范围（也可以用`from=1715155200000&to=1715162400000`绝对时间戳）

在告警通知中嵌入这样的URL，可以实现"一键直达问题视图"。

**常见坑点**
1. **变量值为空时Dashboard空白**：当Query变量查询返回0结果时，所有面板的查询因为`instance=~""`匹配不到数据而空白。解决：设置变量的`Include All option`为默认值，或在查询中处理空值情况。
2. **变量查询触发大量后端请求**：每次变量变化，所有使用了该变量的面板同时重新查询。如果有20个面板，变量改变一次=20次查询。解决：合理设置Refresh策略为`Never`或`On time range change`。
3. **Multi-value变量在PromQL中的坑**：多选变量值在URL中表现为`var-instance=server01&var-instance=server02`。在PromQL中自动展开为`instance=~"server01|server02"`。注意这里用的是`=~`正则匹配，不要用`=`。

## 4. 项目总结

**优点 & 缺点**

| 优点 | 说明 |
|------|------|
| 一表多用 | 1个模板Dashboard替代N×M个静态Dashboard |
| 下拉联动 | 环境→集群→实例三级联动，减少选择错误 |
| Repeat | 一个面板模板自动复制为N个，适合批量监控 |
| URL参数 | 分享URL自带变量状态，一键定位问题 |
| Ad hoc filter | 即席过滤，无需预定义变量 |

| 缺点 | 说明 |
|------|------|
| 性能开销 | 变量变化触发大量面板重查询 |
| 复杂度 | 3层以上级联变量管理困难 |
| 无变量验证 | 自定义变量的值没有格式校验 |
| Repeat限制 | Repeat的面板不能独立设置阈值 |

**适用场景**
1. 多环境/多集群监控：同一Dashboard，切换变量看不同环境
2. 多服务实例大盘：每个服务一行，Repeat Row自动扩展
3. 多租户SaaS平台：每个租户独立变量，互不干扰
4. 告警快速定位：告警URL携带变量值，点击直接看问题上下文
5. A/B对比分析：通过变量选择两个不同的时间段并排对比

**注意事项**
1. Query变量的查询建议添加限制条件（如只看最近有数据的实例），避免下拉框出现已下线的历史实例
2. Multi-value选择上限：默认最多选择1000个值，超过后Grafana可能卡顿
3. Variable的Regex可以对查询结果进行二次过滤（如`/.*-prod$/`只保留prod结尾的值）
4. 如果变量的依赖链很长（A→B→C→D），每次A变化需要等待BC全部刷新完成后D才能正确显示

**常见踩坑经验**
1. **Query变量超时**：如果`label_values()`查询的指标基数很大（如kube_pod_info有上万个Pod），查询可能超时导致下拉框空白。解决：缩小范围（加namespace过滤）或使用更高效的变量查询方式。
2. **变量值被URL缓存**：通过URL分享Dashboard后，浏览器会记住变量值。下次直接打开可能看到"过期的选择"。解决：Dashboard设置中开启"Save variable values" 。
3. **Repeat panel与变量的交互**：Repeat产生的面板副本不能独立设置自己的阈值——这是功能限制，不是bug。如果需要对不同实例设置不同阈值，用Threshold override或者分开创建面板。

**思考题**
1. 现有变量体系：$env → $service → $instance（3层级联）。第3层$instance的Query查询返回1000个值（多选模式），每次env或service变化，instance下拉框要等待5秒才能显示。如何优化？
2. Dashboard A有变量$service，Dashboard B有变量$pod。如何在面板上设置一个Data Link，从Dashboard A点击某个服务时跳转到Dashboard B并自动选择该服务关联的所有Pod？
