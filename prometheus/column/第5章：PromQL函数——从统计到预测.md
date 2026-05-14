# 第5章：PromQL函数——从统计到预测

## 一、项目背景

运维老张用上一章学的PromQL做成了一张CPU监控大盘，投到电视屏上，领导看了直点头。可是好景不长，第二天同事就找上门来了——"老张，昨天下午3点用户一直喊卡，你的大盘咋一点异常都没有？"

老张赶紧回看大盘截图，CPU使用率确实在0%到100%之间来回跳动，但根本看不出什么规律。他排查了半天才发现问题：大盘用的是`irate()`，只取最后两个采样点的瞬时速率，毛刺太多——真正的CPU均值完全被淹没了。更要命的是，大盘只盯CPU，磁盘IO、网络流量一概没看。用户卡顿很可能是因为磁盘IO打满，而CPU图上一片太平。

老张翻了一圈Prometheus官方文档，发现PromQL远不止选择器那点东西——还有一整座函数大厦等着他探索。`rate()`和`irate()`的区别到底是什么？`avg_over_time`、`max_over_time`这些时间聚合怎么用？`predict_linear`能做容量预测，但它算出来的"24小时后磁盘写满"到底靠不靠谱？还有`label_replace`和`label_join`——为什么需要折腾标签？`sum`、`avg`、`topk`这些聚合函数跟SQL里`GROUP BY`的玩法一样吗？

这张函数拼图，缺了哪一块都会让监控变成"睁眼瞎"。本章我们就一口气拆开这些函数的黑盒，从统计走到预测。

## 二、剧本式交锋对话

中午食堂，小胖捧着一盘红烧肉坐下，小白正对着笔记本屏幕皱眉。

**小胖**：（边吃边问）"小白你看啥呢这么认真？我昨天用`rate()`和`irate()`画了两条QPS曲线，一条像打了镇定剂，一条像心电图——明明都是算速率，区别这么大？"

**小白**："我也在纠结这个。`rate(node_cpu_seconds_total[5m])`是一条圆滑的曲线，换成`irate`就全是锯齿。文档说rate是区间平均速率，irate是最后两个点的瞬时速率——但这不就意味着irate更'实时'吗？为什么老张说日常监控应该用rate？"

**大师**：（端着餐盘坐下）"正好你们问对了。我打个比方——**rate()像体温计**，测的是你5分钟内的平均体温，今天36.5°C明天36.6°C，曲线稳定好判断趋势；**irate()像心电图**，每一次心跳都画一个尖峰，医生靠它看心律不齐，但你要拿它来判断'这一周体温是否正常'，毛刺反而干扰判断。"

> **技术映射**：`rate()`计算range vector在指定时间窗口内的**平均每秒增长率**，公式：`(最后一个值 - 第一个值) / 窗口时长`。窗口内所有数据点参与平滑，适合日常趋势监控。`irate()`只取窗口内**最后两个采样点**计算瞬时速率，灵敏但极易受毛刺影响，适合短期排障。

**小胖**：（放下筷子）"那我还有一个问题——`increase()`又是什么？我看它就是`rate() * 时间窗口`，直接看增量不行吗？"

**大师**："没错，`increase()`本质就是rate乘以窗口秒数。但它的价值在于**叙事方便**：你跟领导说'过去5分钟内，用户服务处理了`increase(http_requests_total[5m])`个请求'，比说'平均每秒处理rate(8.2)个请求'更直观。不过要记住——`increase()`只对Counter有意义，Gauge算增量得用`delta()`。"

**小白**：（翻开笔记本）"那`delta()`和`idelta()`呢？跟rate/irate也是对称的关系？"

**大师**："对，规律一样。`delta(node_memory_MemAvailable_bytes[5m])`算的是窗口内最后一个值减去第一个值（Gauge差值），`idelta()`取最后两个点的差值。一个平滑，一个灵敏。但`delta()`用在Counter上会出错——Counter可能reset（重启归零），`delta`不会处理reset，`increase()`会自动补偿reset的跳变。"

> **技术映射**：`increase()` = `rate() * range_seconds`。`increase()`自动处理Counter reset（如果发现新值 < 旧值，视作reset，补偿差值）。`delta()`仅适用于Gauge，不处理reset。`idelta()`是delta的瞬时版。

**小胖**：（挠头）"那`avg_over_time`这类函数呢？上周做一周报告，领导要每个服务的平均内存使用量，我用`avg(node_memory_MemAvailable_bytes)`发现出来的是一个标量——不对啊，我明明要的是过去一周每小时的平均值？"

**大师**："你踩了一个经典的坑。`avg()`是横跨多time series做聚合（类似SQL的GROUP BY），`avg_over_time()`是沿单条time series的时间轴做聚合。比如三台机器各有自己的内存曲线，`avg()`算三台机器的同一时刻平均值，`avg_over_time()`算单台机器过去1小时的平均值。方向完全不同。"

**小白**："那`predict_linear`呢？我想用它做磁盘容量预测，但心里没底——线性回归是不是太简单了？磁盘的增长真的是线性的吗？"

**大师**：（放下筷子认真说）"问得好。`predict_linear(v range-vector, t scalar)`就是拿range vector里的数据做**简单最小二乘线性回归**，然后外推到t秒后。磁盘写入很多场景确实是线性的——比如日志每天固定增长10GB，你拿过去一周的数据预测一周后，误差很小。但CPU和网络流量通常不是线性的，有早晚高峰、有突发流量，拿它预测CPU一周后的值就很容易误告警。所以原则很简单——**只对线性趋势的数据用predict_linear，而且只做短期预测**。"

> **技术映射**：`predict_linear() = 最小二乘法拟合直线 → y = a·t + b → 代入t得到预测值。要求range vector至少覆盖你预测时长的1-2倍。适合磁盘、日志增长等线性场景，不适合CPU、网络等非线性场景。

**小胖**：（凑过来看屏幕）"还有个头疼的问题——我们公司Node Exporter的instance标签全是'10.0.1.5:9100'这种，Grafana图例太长根本看不清。能不能只显示IP？"

**大师**："`label_replace()`这时候就派上用场了。它用正则从现有label中提取内容，创建新label。比如从`instance="10.0.1.5:9100"`提取IP存到新label `ip`里：

```promql
label_replace(up, "ip", "$1", "instance", "(.+):.*")
```

它的语法是`label_replace(expression, "新label名", "替换值模板", "源label名", "正则")`。`$1`就是正则第一个捕获组的内容。还有一个`label_join()`，能把多个label的值拼接成一个——比如把cluster和namespace合并成`cluster-ns`。"

**小白**："聚合函数的`by`和`without`呢？我一直分不清什么时候用哪个。"

**大师**："它俩互为反义词——`by(instance)`是'按instance分组，其他label全丢掉'；`without(instance)`是'丢掉instance，其他label全保留用于分组'。比如有job、instance、mountpoint三个label，`sum by (instance) (...)`等于`sum without (job, mountpoint) (...)`。通常label多的场景用by更简洁，label少的场景用without更稳健。topk/bottomk更好理解——直接取前N或后N条time series，比如`topk(3, rate(node_network_receive_bytes_total[5m]))`取网络流量最高的3条曲线。"

> **技术映射**：`by`和`without`是聚合函数的grouping参数，等价于SQL的`GROUP BY`。用by列出要保留的label，用without列出要去掉的label。聚合类函数（sum/avg/min/max/stddev/stdvar/count/count_values/quantile）都支持这两个参数。

**小胖**：（扒完最后一口饭）"所以这堆函数总结起来就是——rate看趋势，irate看毛刺，increase讲故事，over_time从时间维度聚合，predict_linear做短期预测但要小心非线性数据，label_replace给标签整容？"

**大师**："概括精辟。今天这顿饭没白吃。"

## 三、项目实战

### 环境准备

延续之前Prometheus + Node Exporter环境（通过Docker Compose一键启动三件套），确保运行**30分钟以上**，积累足够历史数据。

浏览器打开 `http://localhost:9090`，进入Graph视图。

### 步骤1：增长率函数对比实验

**目标**：同一指标、同一窗口，直观感受rate、irate、increase三条曲线的差异。

在Prometheus Web UI依次执行以下三条PromQL，观察曲线形态：

```promql
# 平滑的idle速率曲线
rate(node_cpu_seconds_total{mode="idle"}[5m])

# 锯齿状的idle速率——与上图对比，毛刺明显
irate(node_cpu_seconds_total{mode="idle"}[5m])

# 5分钟内的idle CPU增量（单位：秒）
increase(node_cpu_seconds_total{mode="idle"}[5m])
```

**操作建议**：切换到不同的时间范围观察——1h窗口下`irate`的锯齿非常突出；拖到6h，`rate`的曲线平滑优势更加明显；24h窗口下`irate`几乎成了一团噪音。

```
rate:      ────___────〰️────___────  (平滑，逐点波动小)
irate:     ┃╱╲╱╲╱╲╱╲╱╲╱╲╱╲╱╲╱╲╱╲  (锯齿，相邻点剧烈跳动)
increase:  ───────╱──────╱──────╱──  (单调递增，看累计增量)
```

**总结**：日常监控大盘用`rate`，短期排障临时查毛刺用`irate`。Grafana告警规则的`for`参数已经提供了抖动容忍窗口，不需要靠`irate`来"更早发现"。

### 步骤2：时间聚合函数实战

**目标**：在单条时间序列上沿时间轴做聚合，找到峰值和低谷。

```promql
# 过去1小时的平均可用内存
avg_over_time(node_memory_MemAvailable_bytes[1h])

# 过去1小时的最高1分钟负载
max_over_time(node_load1[1h])

# 过去24小时的最低可用内存——判断凌晨是否有内存泄漏
min_over_time(node_memory_MemAvailable_bytes[24h])
```

**应用场景**：做日报时需要`max_over_time`发现CPU/负载峰值，`min_over_time`发现内存低谷（判断是否有OOM风险），`avg_over_time`做容量规划基线。这三个函数的值可以直接扔进Grafana的Stat面板，一行一个数字，干净利落。

### 步骤3：预测函数

**目标**：基于历史趋势预测未来值，评估是否需要提前扩容。

```promql
# 预测24小时后根分区的剩余空间（单位：字节）
predict_linear(node_filesystem_free_bytes{mountpoint="/"}[1h], 24 * 3600)

# 预测一周后的可用内存（谨慎使用——内存趋势通常非线性）
predict_linear(node_memory_MemAvailable_bytes[6h], 7 * 24 * 3600)
```

**告警规则写法**：直接将预测结果作为告警条件。

```promql
# 如果预测4小时后磁盘耗尽，立即告警
predict_linear(node_filesystem_free_bytes{mountpoint="/"}[1h], 4 * 3600) < 0
```

预测值 < 0 意味着按当前线性趋势，**在预测时间点之前资源就会耗尽**，必须立即通知运维。注意这里不推荐用`predict_linear(...) < 1024*1024*1024`（小于1GB）——一旦增长速率突然变快，4小时expire窗口不够用，值越小越危险。推荐短周期（1~4小时）+ 小阈值（趋近于0），时效性最强。

**重点警示**：`predict_linear`基于简单线性回归。以下三类指标不适用：
- **CPU使用率 / 网络流量**：早晚高峰呈峰谷曲线，不是直线
- **请求延迟**：P99延迟忽高忽低，无线性趋势
- **突发型指标**：秒杀活动的QPS，线性预测毫无意义

### 步骤4：聚合函数（by与without）

**目标**：理解跨时间序列的聚合，与SQL GROUP BY类比。

```promql
# 每个实例的总CPU使用速率（排除idle）
sum(rate(node_cpu_seconds_total{mode!="idle"}[5m])) by (instance)

# 去掉mode和cpu两个label后的平均可用内存
avg(node_memory_MemAvailable_bytes) without (mode, cpu)

# 接收流量最高的3条时间序列（可能是3个网卡或3台机器）
topk(3, rate(node_network_receive_bytes_total[5m]))

# 可用内存最少的3台实例——快速定位内存紧张的主机
bottomk(3, node_memory_MemAvailable_bytes)
```

`by(instance)`的等价写法：`without(mode, cpu, job, __name__)`。实际项目中label可能有十几个，用by列出1~2个关注维度，比用without排除一堆维度要简洁得多。

**关键理解**：`sum(...) by (instance)`的语义是——"对每条曲线按instance分组，组内求和"。如果某台机器有4个CPU核（4条CPU曲线），`sum by (instance)`会把这4条合并成1条，这才是"这台机器的总CPU使用"——拿来画大盘的也正是它。

### 步骤5：标签操作函数

**目标**：在不改变Exporter的前提下，用PromQL重新整理标签。

```promql
# 场景1：从instance="10.0.1.5:9100"提取IP，创建新label名为ip
label_replace(up, "ip", "$1", "instance", "(.+):.*")

# 场景2：将cluster和namespace合并成cluster_ns新标签（用-连接）
label_join(up, "cluster_ns", "-", "cluster", "namespace")
```

验证效果：执行`label_replace(up, "short_instance", "$1", "instance", "(.+):.*")`，在Table视图中应看到原始`instance`列旁边多出一列`short_instance`，只显示IP去掉了端口。

### 可能遇到的坑

**坑1：rate窗口太小导致断线**

`rate()`要求窗口内至少有两个数据点。如果`scrape_interval=15s`，`rate(...[5m])`对应的窗口内有`5*60/15=20`个点——足够。但如果窗口设为`[15s]`，窗口内只有2个点（默认要求至少2个点），此时恰好一个点采集失败，只剩1个点，rate直接返回空，监控图表出现断线。**最小窗口建议 ≥ 2×scrape_interval**。

**坑2：predict_linear预测CPU导致误告警**

某团队在生产上写了这条规则：`predict_linear(node_cpu_seconds_total{mode="idle"}[1h], 7*24*3600) < 0`，预测一周后CPU idle归零。结果每逢业务低峰期就触发告警——因为低峰期idle曲线上升（空闲增多），但线性回归线拟合了上升趋势就预测未来idle无穷大；等到高峰期idle下降，趋势反转，预测值又跳水。线性模型在非平稳数据上反复横跳，告警风暴就此产生。

**坑3：topk在Grafana Stat面板显示异常**

你写了一条`topk(3, rate(http_requests_total[5m]))`，扔进Grafana的Stat面板却只显示1个数而不是3个。因为Stat面板默认只展示第一条time series的值。正确做法：用Table面板渲染多条曲线，或者配合`scalar()`函数取单值。同理，`bottomk`也一样。

### 测试验证

在Prometheus Web UI中逐条执行以下5条查询，确保每一条返回有效数据：

```promql
# 1. 网络流量速率——应有大于0的曲线
rate(node_network_receive_bytes_total[5m])

# 2. 15分钟平均负载——应有0~N的数值
avg_over_time(node_load1[15m])

# 3. 预测24小时后磁盘空间——应返回一个正整数（单位：字节）
predict_linear(node_filesystem_free_bytes[1h], 3600 * 24)

# 4. CPU使用Top3实例——应返回最多3条曲线
topk(3, sum(rate(node_cpu_seconds_total{mode!="idle"}[5m])) by (instance))

# 5. 提取短实例名——Table视图应显示short_instance列
label_replace(up, "short_instance", "$1", "instance", "(.+):.*")
```

## 四、项目总结

### 函数分类速查表

| 分类 | 函数 | 核心用途 |
|------|------|---------|
| **增长率类** | `rate` / `irate` / `increase` | 计算Counter的每秒增量或窗口总增量 |
| **差值类** | `delta` / `idelta` | 计算Gauge的窗口差值 |
| **时间聚合类** | `avg_over_time` / `max_over_time` / `min_over_time` / `sum_over_time` / `count_over_time` / `quantile_over_time` / `stdvar_over_time` / `stddev_over_time` | 沿时间轴对range vector做聚合 |
| **预测类** | `predict_linear` / `deriv` / `holt_winters` | 线性回归预测 / 每秒导数 / 霍尔特-温特斯平滑 |
| **标签操作类** | `label_replace` / `label_join` | 正则提取创建新label / 拼接多个label |
| **聚合类** | `sum` / `avg` / `min` / `max` / `topk` / `bottomk` / `count` / `stddev` / `stdvar` / `quantile` | 跨time series聚合（支持by/without） |

### rate vs irate 对比表

| 维度 | rate() | irate() |
|------|--------|---------|
| 计算方式 | 窗口首尾值之差 / 窗口时长 | 最后两个点之差 / 各自时间差 |
| 平滑度 | ★★★★★ 平滑 | ★☆☆☆☆ 锯齿 |
| 灵敏度 | ★★★☆☆ 中等 | ★★★★★ 极高 |
| 适用场景 | 日常监控大盘、趋势告警 | 短期排障、毛刺捕捉 |
| Counter reset | 自动补偿 | 自动补偿 |
| CPU开销 | 低（窗口内所有点参与） | 更低（只取最后两个点） |

### 适用场景

1. **日常运维监控**：`rate()` + `sum by (instance)` 构建CPU/内存/网络大盘，曲线平滑，趋势一目了然。
2. **容量规划**：`predict_linear()` 预测磁盘/日志增长趋势，配合`max_over_time()`找历史峰值，数据驱动扩容决策。
3. **故障排查**：`irate()` + `topk()` 快速定位毛刺来源，找到流量尖峰的源头服务。
4. **告警配置**：`(rate(errors_total[5m]) / rate(requests_total[5m])) > 0.01` 错误率告警；`predict_linear(disk_free[1h], 4*3600) < 0` 磁盘预满告警。
5. **日报周报**：`avg_over_time` + `max_over_time` + `min_over_time` 快速生成各维度的统计数字，扔进Grafana Stat面板。
6. **标签治理**：`label_replace()` 在不重启Exporter的前提下统一标签格式，适配Grafana图例和告警路由规则。

### 常见踩坑经验

**案例1：rate窗口=scrape_interval，监控图频繁断线**

某团队配置`scrape_interval=30s`，PromQL写作`rate(cpu_seconds_total[30s])`。窗口内刚好2个点，网络抖动导致一个采集点丢失，rate返回空。Grafana大盘上CPU曲线被切成虚线。**根因**：窗口太小无容错空间。**解决**：窗口至少设为2~4倍采集间隔（如`[2m]`）。

**案例2：predict_linear预测CPU，每天凌晨准时告警**

线上部署了一条规则 `predict_linear(cpu_idle[1h], 24*3600) < 0`。每天凌晨业务低峰CPU idle上升，线性拟合出"idle暴涨"趋势，预测值朝天飞；早上8点业务高峰来临，idle下降，拟合线突然跳水，告警触发。**根因**：CPU idle是非平稳时间序列，简单线性回归反复过拟合局部趋势。**解决**：CPU使用`avg_over_time` + 阈值，预测类告警仅保留磁盘容量。

**案例3：topk用在Grafana Stat面板，只显示1个值**

运维想用Stat面板醒目展示"CPU最高的3台机器"，写了`topk(3, sum(rate(node_cpu_seconds_total{mode="idle"}[5m])) by (instance))`，但Stat面板只显示一个巨大数字。**根因**：Stat面板默认取第一条series的值，topk返回的3条它只认第一条。**解决**：改用Table面板渲染topk结果，每行一台机器。

### 思考题

1. **如果采集间隔是15s，`rate(node_cpu_seconds_total[5m])`的窗口内理论上有多少个数据点？如果采集过程中发生了一次scrape失败（丢了一个点），rate如何应对——是会返回错误还是用剩余的点继续计算？这时候irate的表现又会怎样？**

2. **请写出一条PromQL，找出所有"按当前趋势，未来1小时内磁盘空间将耗尽"的主机（假设使用Node Exporter的`node_filesystem_free_bytes`指标）。提示：需要用到`predict_linear`，并将预测值（字节）与0做比较。**

---

*下一章预告：Node Exporter——主机监控从入门到精通，拆解每个Collector背后的内核指标。*
