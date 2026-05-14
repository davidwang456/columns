# 第4章：PromQL入门——选择器与操作符

## 一、项目背景

运维小张的Grafana大盘已经顺利接入了Node Exporter的CPU、内存数据，曲线图看上去像模像样，他颇为自得。直到某天下午，老板在群里丢了一句："把过去1小时内所有Web服务器的平均CPU使用率发我看看。"

小张二话不说打开Prometheus，在Expression Browser里敲下：

```promql
avg(node_cpu_seconds_total)
```

回车之后，一个四位数的结果赫然出现在屏幕上。"什么情况？CPU使用率最大值不是100吗？"小张慌了。同事老李路过瞟了一眼，淡淡地说："`node_cpu_seconds_total`是Counter类型，存的是CPU累计运行秒数，数字当然大。你得先用`rate()`算速率，而且记得用`mode="idle"`的标签过滤。"

小张这才如梦初醒——PromQL不是SQL，它有自己一套独特的语义规则。指标不是表、标签不是列、向量不是行。

这次"翻车"其实戳中了绝大多数PromQL初学者的痛点：**Instant Vector**和**Range Vector**到底有什么区别？为什么有些查询返回一条平滑的曲线，有些却返回一大堆离散的时间序列？`{}`里面的标签过滤和SQL的`WHERE`从句本质上有何不同？`=~`和`!~`的正则匹配该怎么写？`offset`修饰符又是干什么用的？

这些概念构成了PromQL的"语法地基"。如果地基没打牢，后面写告警规则、做容量规划就全是空中楼阁。本章我们就从选择器和操作符入手，把这层地基夯实。

---

## 二、剧本式交锋对话

**场景：运维部的茶水间，三人端着咖啡围坐。**

**小胖**（往嘴里塞了块饼干）："PromQL有啥难的？不就是SQL换个语法嘛。你看这`up`、`node_cpu_seconds_total`，和`SELECT * FROM metrics`一个意思，后面加个大括号`{}`就是`WHERE`条件。搞定！"

**小白**（皱眉）："不对吧胖哥。我刚试了`node_cpu_seconds_total{mode="idle"}`，出来一堆时间戳和数值对儿，可我想看的是过去5分钟的平均值，怎么写？"

**小胖**（不假思索）："那还不简单，加个`[5m]`呗，过滤最近5分钟的数据——"

**大师**（放下杯子，笑了）："打住。`[5m]`的作用**不是过滤**，它把查询从Instant Vector变成了Range Vector。小胖，你把PromQL想得太像SQL了，这是新手最容易掉的坑。"

**小胖**（不服）："Instant Vector？Range Vector？这俩名词听着就唬人。"

**大师**："打个比方你就明白了。**Instant Vector**像是拍一张快照——你按下快门的那一刻，每个时间序列只返回一个值。你在Graph视图里看到的所有曲线，本质上是Prometheus每隔一段时间帮你拍一张快照，然后把点连成线。而**Range Vector**像是录一段视频——每个时间序列返回过去一段时间内的**一整套样本数据**。"

**小白**（眼睛一亮）："所以`node_cpu_seconds_total[5m]`不是过滤出最近5分钟的数据，而是把每个时间序列的最近5分钟样本全都端出来了？"

**大师**："完全正确。这就能解释为什么在Table视图里你看到一大堆时间戳——那是所有样本点。`[5m]`是**范围选择器**，它把Instant Vector转换成了Range Vector。`rate()`、`avg_over_time()`这些函数必须吃Range Vector才能工作，因为速率计算需要知道'过去一段时间的变化量'，单一个点没法算。"

**小胖**（恍然大悟）："怪不得`rate(node_cpu_seconds_total{mode="idle"}[5m])`能算出每秒速率！`[5m]`先拉出5分钟的样本，`rate()`再用这些样本算出每秒增长量。"

**大师**："没错。其实`rate()`还有一个容易忽略的细节——它要求Range Vector里至少有两个样本点，因为它用首尾两个点做外推。如果抓取间隔是15秒，那理论上5分钟内有20个样本点，足够计算了。但如果你写`[30s]`而抓取间隔是60秒，那可能只有一个样本点，`rate()`就会失效。"

**小胖**（挠头）："那大括号里的`=~`又是啥？我看别人写的`job=~"node.*"`——这不是shell通配符吗？"

**大师**："这是另一个高频误区。`=~`后面跟的是**正则表达式**，不是shell glob。`.*`在正则里表示'匹配任意字符零次或多次'，和shell里的`*`完全不同。同理，`!~`是反向正则匹配。来，记住这四种标签匹配符："

- **`=`**：精确等于，如 `{mode="idle"}`
- **`!=`**：不等于，如 `{mode!="idle"}`
- **`=~`**：正则匹配，如 `{job=~"node.*"}` 匹配以"node"开头的所有job
- **`!~`**：反向正则匹配，如 `{job!~"prometheus"}` 排除prometheus

**小白**（举一反三）："那我用`{__name__=~"node_network.*bytes.*"}`就能找到所有网络字节相关的指标了？"

**大师**："聪明。`__name__`是PromQL的内置标签，代表指标名本身。用正则把指标名当标签来过滤，这是非常实用的技巧。"

**小胖**（受到启发）："那`offset`又是什么鬼？我看到有人写`offset 1h`。"

**大师**："`offset`是把时间窗口平移。比如你想对比当前QPS和1小时前的QPS，写法是："

```promql
rate(http_requests_total[5m])           # 当前QPS
rate(http_requests_total[5m] offset 1h) # 1小时前的QPS
```

"做同比环比全靠它。报警规则里最常见的应用：当前流量相比昨天同一时刻下降超过50%。"

**小胖**（掰着手指）："那运算符部分呢？`+`、`-`这些好理解，就是算术。比较运算符`>`、`<`用于告警阈值也没毛病。但`and`、`or`、`unless`是不是就是布尔运算？"

**大师**（摇头）："又一个坑。PromQL里的`and`/`or`/`unless`是**集合操作**，不是布尔运算。"

- **`and`**：取交集。`A and B`返回既在A中又在B中的时间序列（标签组合完全匹配才算同一条）。
- **`or`**：取并集。`A or B`返回A和B的所有序列，A中已有的不会从B中重复取。
- **`unless`**：取差集。`A unless B`返回在A中但不在B中的序列。

"比如`up == 1 unless node_cpu_seconds_total`，返回的是状态为UP但又没有CPU数据的实例——这种'孤儿'实例就该告警。"

**小白**（合上笔记本）："今天信息量真大。我回去实战一遍。"

---

## 三、项目实战

### 环境准备

继续使用第3章的Docker Compose环境（Prometheus + Node Exporter）。确保Prometheus已经采集了至少5分钟数据。浏览器打开 `http://localhost:9090`，进入Graph页面。

没有Node Exporter的读者，也可以用Prometheus自带的`prometheus_http_requests_total`指标完成所有练习。

---

### 步骤1：Instant Vector vs Range Vector 对比实验

在Expression Browser中依次执行以下查询，观察Table视图和Graph视图的差异：

```promql
node_cpu_seconds_total
```

Graph视图中出现多条曲线——每个CPU核心、每种CPU模式（idle、user、system、iowait等）各生成一条时间序列。这就是**Instant Vector**：查询返回当前时刻一组时间序列的值，Graph视图把这些Instant Vector按时间轴拼成连续曲线。

```promql
node_cpu_seconds_total{mode="idle"}
```

现在只剩idle模式的曲线。`=`实现了精确标签过滤——只保留`mode`标签值为`idle`的时间序列。

```promql
node_cpu_seconds_total{mode!="idle"}
```

排除idle，留下user、system、iowait等其他所有模式。

接下来是关键一步：

```promql
node_cpu_seconds_total[5m]
```

**切换到Table视图**。你会看到每个时间序列展开为一组`timestamp @ value`对——这就是Range Vector。Graph视图无法直接展示Range Vector（一条时间序列在同一个时间戳上有多个值，没法画），所以会自动切到Table视图（Console标签页）。

现在把Range Vector喂给函数：

```promql
rate(node_cpu_seconds_total{mode="idle"}[5m])
```

这才是正确的每秒CPU idle速率。`[5m]`提供5分钟窗口的样本，`rate()`计算每秒增长量。Graph视图恢复曲线显示——因为`rate()`的输出是Instant Vector。

---

### 步骤2：正则选择器实战

先用`up`查看所有抓取目标的状态：

```promql
up
```

这会列出所有target，包括Prometheus自身（job=prometheus）和Node Exporter（job=node）。

```promql
up{job=~"node.*"}
```

正则`node.*`匹配以"node"开头的所有job。注意：`=~`后面是正则表达式，`.`匹配任意字符，`*`表示前一个模式重复零次或多次。

```promql
up{job!~"prometheus"}
```

排除prometheus自身，只看其他target的状态。

用`__name__`标签做指标名级别的正则过滤：

```promql
{__name__=~"node_network.*bytes.*"}
```

这会匹配`node_network_receive_bytes_total`、`node_network_transmit_bytes_total`等所有网络字节相关指标。这是一个非常强大的查询技巧——在不清楚确切指标名时，用正则做模糊搜索。

> **切记**：`=~`使用的是[RE2正则语法](https://github.com/google/re2/wiki/Syntax)，锚定默认与字符串完全匹配。`.+`表示至少一个字符，`.*`表示零个或多个。不要写成shell通配符的`*`。

---

### 步骤3：算术与比较操作符

计算CPU使用率（非idle模式的时间占比）：

```promql
sum(rate(node_cpu_seconds_total{mode!="idle"}[5m])) by (instance)
  /
count(node_cpu_seconds_total{mode="idle"}) by (instance)
```

> 注意：此公式有精度问题（每个核心的非idle时间加总后除以核心数，不同核心采集时刻可能不同），留到第5章用`avg`优化。

计算内存可用百分比：

```promql
node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes * 100
```

计算后返回百分比数字，Graph视图可直接展示曲线。

比较运算符用于告警阈值判断：

```promql
node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes < 0.1
```

这条查询只返回可用内存低于10%的实例。比较运算符的行为是：**满足条件的序列保持原值不变，不满足条件的序列直接被丢弃**。它不是返回`true`/`false`，这是PromQL和传统编程语言的重要区别。

---

### 步骤4：offset与时间对比

查询Prometheus当前自身的HTTP QPS：

```promql
rate(prometheus_http_requests_total[5m])
```

对比1小时前的QPS：

```promql
rate(prometheus_http_requests_total[5m] offset 1h)
```

将两个查询画在一张图里：在Expression Browser中一次输入两条查询（用换行分隔，或点击"Add Graph"），即可直观对比。绿色曲线是当前QPS，红色曲线是1小时前同一时刻的QPS。

计算同比变化率：

```promql
(
  rate(prometheus_http_requests_total[5m])
  -
  rate(prometheus_http_requests_total[5m] offset 1h)
)
/
rate(prometheus_http_requests_total[5m] offset 1h)
* 100
```

结果为百分比。正值表示流量增长，负值表示流量下降。这个模板可以套用到任意指标上，是做容量规划和异常检测的核心手段。

---

### 步骤5：逻辑运算符（集合操作）

查找内存不足**且**CPU idle也很低的实例（两者同时满足才需要关注）：

```promql
node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes < 0.2
and
node_cpu_seconds_total{mode="idle"} < 100
```

`and`取交集——只有两个条件**在同一个instance上有匹配序列**时，结果中才包含该序列。

查找状态为UP但没有上报CPU数据的"异常"实例：

```promql
up == 1 unless node_cpu_seconds_total
```

`unless`取差集——从UP的target中排除那些有CPU数据的，剩下的就是没有CPU数据的实例。这在监控覆盖度检查中非常有用。

> **再次强调**：`and`/`or`/`unless`是**集合运算**，基于标签集合的交/并/差。它们和布尔逻辑的`&&`/`||`/`!`有本质区别。PromQL没有布尔运算符。

---

### 可能遇到的坑

1. **range vector在Graph视图不显示**：`[5m]`将Instant Vector转换为Range Vector后，Graph视图无法渲染多条时间序列在同一个时间戳的多个值。此时Prometheus会自动跳转到Table视图（Console标签页），这不是报错，是预期行为。

2. **`=~`正则不是glob**：`*`在shell里表示任意字符串，但在正则里表示重复前一个模式。要用`.*`表示任意字符任意次数。反过来，正则里的`.`匹配任意单个字符，shell里的`?`才匹配单个字符——语法完全不同。

3. **比较运算符不返回布尔值**：`cpu > 90`返回的是cpu值大于90的那些时间序列（保持原值），而不是`1`（true）或`0`（false）。这和SQL的行为一致，但不熟悉的人很容易误解。

---

### 测试验证

在Expression Browser中逐条验证以下查询，确保结果符合预期：

| # | 查询 | 预期结果 |
|---|------|---------|
| 1 | `up` | 列出所有target的UP/DOWN状态 |
| 2 | `up{job="prometheus"}` | 仅Prometheus自身的状态 |
| 3 | `node_cpu_seconds_total{mode=~"user\|system"}` | 仅user和system模式的CPU累计时间 |
| 4 | `rate(node_cpu_seconds_total{mode="idle"}[1m])` | 每秒idle速率，观察短窗口对曲线平滑度的影响 |
| 5 | `node_memory_MemAvailable_bytes/1024/1024/1024` | 可用内存，单位GB |
| 6 | `node_memory_MemAvailable_bytes/node_memory_MemTotal_bytes > 0.5` | 可用内存超过50%的实例 |
| 7 | `rate(prometheus_http_requests_total[5m] offset 30m)` | 30分钟前Prometheus自身的QPS |
| 8 | `up{job="prometheus"} and up{job="node"}` | 返回空（同一序列的job标签不可能同时等于两个值） |

---

## 四、项目总结

### Instant Vector vs Range Vector

| 维度 | Instant Vector | Range Vector |
|------|---------------|-------------|
| 类比 | 拍快照 | 录视频 |
| 返回数据 | 每个序列当前时刻的一个值 | 每个序列一段时间的多个样本 |
| 能否在Graph视图渲染 | 能 | 不能（需切换到Table视图） |
| 喂给哪些函数 | `sum`、`avg`、`count`等聚合函数 | `rate`、`irate`、`increase`、`avg_over_time`等 |
| 转换方式 | 默认查询即返回 | 在指标名后加`[时间窗口]` |

### 选择器类型速查

| 操作符 | 含义 | 示例 | 使用场景 |
|--------|------|------|---------|
| `=` | 精确等于 | `{mode="idle"}` | 过滤特定模式、特定实例 |
| `!=` | 不等于 | `{mode!="idle"}` | 排除某个已知值 |
| `=~` | 正则匹配 | `{job=~"node.*"}` | 批量匹配一组job、按命名规范过滤 |
| `!~` | 反向正则匹配 | `{job!~"prometheus"}` | 排除自身监控、过滤掉测试环境 |

### 运算符优先级（从高到低）

| 优先级 | 运算符 | 说明 |
|--------|--------|------|
| 1（最高） | `^` | 幂运算 |
| 2 | `*` `/` `%` | 乘、除、取模 |
| 3 | `+` `-` | 加、减 |
| 4 | `==` `!=` `<=` `<` `>=` `>` | 比较运算符 |
| 5 | `and` `unless` | 逻辑/集合运算（交集与差集） |
| 6（最低） | `or` | 逻辑/集合运算（并集） |

注意：`and`和`unless`优先级相同，`or`优先级最低。多个逻辑运算符混用时建议加括号。

### 适用场景

- **资源监控**：CPU使用率、内存可用百分比、磁盘剩余空间——用算术运算符做单位转换和比率计算。
- **性能分析**：QPS同比环比、延迟P99变化趋势——用`offset`做时间对比。
- **容量规划**：内存可用率低于20%且持续超过1小时——`and`组合多个条件。
- **异常检测**：流量相比昨天同一时刻骤降——`offset`加减法计算变化率。

### 注意事项

1. PromQL**大小写敏感**：`node_cpu`和`Node_Cpu`是两个不同的指标，标签名和标签值同样区分大小写。
2. 标签名**不能包含连字符`-`**：Prometheus的标签名规范只允许`[a-zA-Z_][a-zA-Z0-9_]*`。如果你的exporter暴露了含`-`的标签，Prometheus会直接拒绝采集。
3. 正则匹配默认**锚定字符串首尾**：`job=~"node"`只匹配job标签值**恰好等于**`node`的情况；要匹配包含`node`的字符串需写成`job=~".*node.*"`。

### 新手常见踩坑三例

**案例一：把`[5m]`当成WHERE过滤条件**

```promql
# 错误：心里想的是"查最近5分钟的idle数据"
node_cpu_seconds_total{mode="idle"}[5m]
# 实际效果：返回一堆raw samples，Graph无法渲染
```

正确姿势：用`rate()`或`avg_over_time()`消费Range Vector，得到Instant Vector后再看曲线。

**案例二：把`and`当`&&`用**

```promql
# 错误：期望返回布尔值用于告警
node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes < 0.2 and 1
# PromQL里没有"and 1"的布尔简写
```

正确姿势：`and`连接的是两个完整的PromQL表达式（各自返回时间序列集合），不是布尔短路求值。

**案例三：`offset`方向写反**

```promql
# 错误：以为offset是"往后"偏移
rate(node_cpu_seconds_total[5m] offset -1h)
# offset必须是正数，表示往前回溯；offset -1h会报语法错误
```

正确姿势：`offset 1h`表示"1小时之前那个时刻的5分钟窗口"。PromQL不支持向未来偏移。

### 思考题

1. **`rate()`和`irate()`的区别是什么？各自适用什么场景？**

2. **如果要查询"当前值相比5分钟前增长超过20%的指标"，PromQL该怎么写？**

（答案将在第5章揭晓）

---

*本章完。从选择器的"四个等号"到操作符的优先级，从`[5m]`的正确理解到`offset`的时间魔术，PromQL的基础语法你已经掌握了。下一章我们将深入聚合函数——`sum`、`avg`、`topk`、`histogram_quantile`，真正开始写生产级的监控查询。*
