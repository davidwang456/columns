# 第9章：Transform数据转换实战

## 1. 项目背景

"两个Prometheus查询的结果，一个返回按服务维度的QPS，一个返回按服务维度的错误数。我想在一个TablePanel里同时展示QPS和错误数，并且计算错误率。这个需求写PromQL不可能实现吧？"

数据工程师小周遇到了Grafana使用中的经典困境——跨查询的数据组合。PromQL不支持JOIN操作，SQL功底再强的开发者面对跨指标的关联也束手无策。而现实业务中，这种"多表联查"的需求比比皆是：QPS和错误率要放在同一行对比、不同数据源的指标要合并显示、时序数据需要先降精度再合并。

Grafana的Transform功能正是填补这个能力空白的设计。它允许在面板渲染前，对查询返回的DataFrame做一系列管道式处理：Filter（过滤）、Join（连接）、Merge（合并）、Reduce（聚合）、Calculate（计算字段）、Sort（排序）等。把Transform想象成"Grafana版的SQL查询计划"——数据从数据源流过来，经过一系列Transform节点加工，最后交给面板渲染。

本章将通过5个真实业务场景，逐一拆解Transform的核心操作，让你掌握"不写SQL也能做数据ETL"的能力。

## 2. 项目设计

**小胖**（盯着Grafana界面左看右看）：大师，面板编辑界面多了一个"Transform"标签，这是干嘛用的？我从来没用过。

**大师**：Transform是Grafana内置的数据变换管道。你可以把它想象成Linux的管道符`|`——上一个操作的结果传给下一个操作，一步一步把原始数据加工成面板需要的样子。

**小白**（放下书）：我理解管道的概念，但具体有哪些操作？为什么不直接在PromQL里做？

**大师**：这就触及Transform的核心价值——它可以操作多个查询的结果。PromQL只能操作单个查询，而Transform可以把查询A的结果和查询B的结果Join在一起，这是PromQL做不到的。

给你看五个最常用的Transform：

**Filter by name**：按字段名过滤。比如你的查询返回了`Time`、`instance`、`Value`、`__name__`四个字段，但你只想要`Value`，过滤掉其他三个。

**Filter data by values**：按数据值过滤。类似SQL的WHERE子句——只保留QPS>100的行，或者只保留error开头的序列。

**Group by**：按某个字段分组聚合。比如按`status`字段分组，计算每个status的`total`和`avg`。

**Join by field**：按某个字段做关联。这是最强大的功能——比如查询A返回服务名和QPS，查询B返回服务名和错误数，按服务名Join后得到一个表，包含服务名/QPS/错误数三列。

**Reduce**：把时间序列数据缩减为单个值。一根CPU线的历史数据是几十个时间点，Reduce取Max就是峰值CPU。

**小胖**：听起来很强大，但实际用起来呢？我有个场景——我想比较"今天"和"昨天同一时段"的QPS，这用PromQL的offset可以实现，但两个序列是分开的。能做到在一个面板里并排比较吗？

**大师**：这正是Transform的经典场景。具体做法：
1. 查询A：`sum(rate(http_requests_total[5m]))`（今天）
2. 查询B：`sum(rate(http_requests_total[5m] offset 1d))`（昨天）
3. Transform → Merge → 合并两个查询
4. Transform → Rename by regex → 把A重命名为"Today"，B重命名为"Yesterday"

现在Time series面板同时显示两根线，一根Today一根Yesterday，一目了然。

**小白**：那如果我要在Table面板的每一行后面附加一个Sparkline（迷你趋势图），用Transform能做到吗？

**大师**：Sparkline不需要Transform，它是Table面板的Cell display mode功能。但如果你想把"当前QPS"和"24小时QPS趋势"放在Table的同一行里，那确实需要Transform。

具体来说，你有两个查询：
- 查询A：Instant查询，返回每个服务当前的QPS
- 查询B：Range查询，返回每个服务24小时的QPS趋势

用Outer Join按服务名把A和B连起来，Table面板中A列显示数字，B列设置Cell display mode为Sparkline。最终效果：每行左边是当前QPS数字，右边是该服务的迷你趋势图。

**小胖**（兴奋）：这也太酷了！那Calculate field呢？能不能像Excel一样写公式？

**大师**：Calculate field支持数学表达式。比如加入Transform → `Add field from calculation`，Mode选`Binary operation`，对QPS列和Error列做除法：`QPS / Error`得到"每个错误对应的请求数"。还可以用`Reduce row`模式做累加、求百分比等。

**小白**：那Transform执行的顺序重要吗？

**大师**：非常重要！Transform是按从上到下的顺序执行的，排列组合不同结果完全不同。比如先Filter再Group by和先Group by再Filter，结果可能不一样。实操中，拿到数据后的标准处理顺序是：

1. **Filter** → 去掉不需要的字段和数据
2. **Join/Merge** → 合并多个查询的结果
3. **Calculate field** → 基于已有字段计算新字段
4. **Organize fields** → 重命名、排序、隐藏字段
5. **Sort/Group** → 最终排序和分组

这个顺序不是绝对的，但遵循"先筛选→再合并→再计算→再整理"的原则基本不会出错。

**小胖**：Transform在性能上有什么需要注意的吗？

**大师**：Transform的所有计算都在浏览器端执行。这意味着如果原始数据量很大（比如1000条时间序列，每条200个数据点），Transform的处理可能比后端查询本身还慢。所以一个原则是——尽量在查询层面就减少数据量（用PromQL的`topk`、过滤条件等），Transform只做最后的数据整形。

**技术映射**：Filter by values = 安检门（不让不合格的数据通过），Join by field = 数据库的JOIN操作（按共同字段合并），Reduce = 统计函数（MAX/AVG/SUM），Expression = Excel公式。

## 3. 项目实战

**环境准备**

统一使用之前的Docker Compose环境，prometheus数据源已配置。

**步骤一：Filter by name——隐藏不必要字段**

创建Dashboard → Add panel → Table → Prometheus数据源。

查询：
```promql
# Query A：返回多个标签的指标
node_cpu_seconds_total{instance="node_exporter:9100", mode="idle"}
```

原始返回的字段包括：`Time`, `__name__`, `instance`, `job`, `mode`, `cpu`, `Value`。

你只需要保留`instance`和`Value`两个字段。添加Transform：

1. Filter by name → 开启（默认所有字段都显示）
2. 关闭`Time`、`__name__`、`job`、`mode`、`cpu`的勾选

现在Table只显示instance和Value两列。

**步骤二：Group by——按状态码聚合**

查询Nginx访问日志指标（来自模拟数据）：
```promql
# 如果有nginx-exporter
rate(nginx_http_requests_total[5m])
```

假设返回的序列包含`status="200"`、`status="404"`、`status="502"`等标签。

Transform Pipeline：
1. **Group by** → Field: `status` → Calculation: `Total`
2. **Organize fields** → 把字段重命名为`Status Code`和`Total Requests`

现在你得到一个饼图友好的数据：按HTTP状态码分组的请求总数。

**步骤三：Join by field——跨查询关联**

这是Transform最强大的场景。

**Query A**：各服务QPS
```promql
sum by (service) (rate(http_requests_total[5m]))
```
返回：`{service="order-svc"} 120`、`{service="user-svc"} 85`

**Query B**：各服务误差率
```promql
sum by (service) (rate(http_requests_total{status=~"5.."}[5m]))
/
sum by (service) (rate(http_requests_total[5m])) * 100
```
返回：`{service="order-svc"} 0.5`、`{service="user-svc"} 2.3`

Transform Pipeline：
1. **Join by field** → Field: `service` → Mode: `Outer join`（保留所有服务，即使另一查询缺失数据）
2. **Organize fields** → 重命名：
   - `Value #A` → `QPS`
   - `Value #B` → `Error Rate %`
3. **Add field from calculation** → Mode: `Binary Operation` → `Error Rate % / 100` → New field name: `Error Rate (decimal)`

最终Table面板显示：

| service | QPS | Error Rate % | Error Rate (decimal) |
|---------|-----|-------------|---------------------|
| order-svc | 120 | 0.5 | 0.005 |
| user-svc | 85 | 2.3 | 0.023 |

**步骤四：Reduce——时序转指标**

如果你的查询返回的是时间序列（Range Query），但你想在Stat面板显示某个聚合值（如最大值）：

```promql
# Range查询，返回5分钟内的CPU变化曲线
rate(node_cpu_seconds_total{mode!="idle"}[5m])
```

Transform：
1. **Reduce** → Mode: `Reduce fields` → Field: `Value` → Calculation: `Max`
2. 现在就得到了CPU使用率在所选时间范围内的峰值。

其他常用Reduce计算：
- `Last`：最新值（最常用）
- `Mean`：平均值
- `Min`：最小值
- `Max`：最大值
- `Total`：总和
- `Count`：数据点个数

**步骤五：Merge + Rename——今天/昨天对比**

**Query A**（今天）：
```promql
sum(rate(http_requests_total[5m]))
```

**Query B**（昨天）：
```promql
sum(rate(http_requests_total[5m] offset 1d))
```

Transform Pipeline：
1. **Merge** → 合并查询A和查询B的结果（保留Time字段对齐）
2. **Rename by regex** → Match: `Value #A` → Replace: `Today` → Match: `Value #B` → Replace: `Yesterday`

Time series面板现在同时显示两条线，Today和Yesterday，方便做日环比分析。

**综合实战：服务健康度排行表**

需求：一个Table显示所有服务，每个服务显示QPS（数字）、错误率（数字+背景色）、CPU趋势（Sparkline）。

设计3个查询：

**Query A**：QPS
```promql
sum by (service) (rate(http_requests_total[5m]))
```

**Query B**：错误率
```promql
sum by (service) (rate(http_requests_total{status=~"5.."}[5m]))
/
sum by (service) (rate(http_requests_total[5m])) * 100
```

**Query C**：CPU趋势（用于Sparkline）
```promql
rate(node_cpu_seconds_total[5m])
```
（这里假设service标签可以从CPU指标关联，实际中可能需要额外的service discovery）

Transform Pipeline：
1. Join by field → Field: `service` → Mode: `Outer join`（合并Query A + Query B）
2. Join by field → Field: `service` → Mode: `Outer join`（合并上一步 + Query C）
3. Organize fields → 重命名各列
4. Table面板：QPS列用默认显示，错误率列用`Color background`+阈值着色，CPU列Cell display mode选`Sparkline`

**常见坑点**
1. **Join by field字段名必须完全匹配**：大小写、空格、特殊字符都要一致。如果两个查询返回的字段名不同（如`service`和`svc`），先用`Rename by regex`统一。
2. **Outer join导致NULL值**：如果某个服务在查询A有数据但查询B没有，Outer join会产生NULL值，在计算结果时需要注意——NULL参与除法会得到NULL。
3. **Merge合并时序数据时Time字段不对齐**：两个查询如果采样时间点不同，Merge后会增加数据密度，可能改变Reduce的结果。
4. **Transform顺序导致逻辑错误**：先Sort再Filter可能浪费渲染时间，先Filter再Sort效率更高。

## 4. 项目总结

**优点 & 缺点**

| 优点 | 说明 |
|------|------|
| 跨查询关联 | Join/Merge填补了PromQL无JOIN的空白 |
| 无代码操作 | UI拖拽式管道，不需要写脚本 |
| 即时预览 | 每步Transform有实时预览，方便调试 |
| 多数据源混合 | 支持不同数据源返回的DataFrame合并 |
| 减少面板数 | 原本需要多个面板的数据可以合并到一个面板 |

| 缺点 | 说明 |
|------|------|
| 浏览器计算 | 大数据量时前端可能卡顿 |
| 状态不保存 | Transform配置在Dashboard JSON里，不能跨面板复用 |
| 无SQL级能力 | 不支持子查询、窗口函数等复杂操作 |
| 学习曲线 | 管道思维对新手不够直觉 |

**适用场景**
1. 多数据源数据合并：Prometheus指标 + MySQL业务数据同一张表
2. 时序聚合：Range查询结果压缩为单个统计值（MAX/AVG/LAST）
3. 同比环比：今天/昨天/YoY对比
4. 数据清洗：过滤无关字段、重命名、格式化
5. 计算派生指标：基于已有列计算错误率、转化率等

**注意事项**
1. Transform中Join的性能取决于数据行数——两个1000行的表做Outer Join，结果可能是1000×1000=100万行。务必在Join前用Filter缩小数据范围。
2. Organize fields里的"隐藏字段"不是删除，当前面板仍持有所有数据，只是不渲染。如果担心数据安全，在Query层面限制返回的字段。
3. Reduce为单个值后，原始的时间序列信息就丢失了。如果需要同时展示趋势和聚合值，用两个面板分别查询。
4. Transform管道不支持"条件分支"——每行数据走同一个管道，不能根据值做if-else路由。

**常见踩坑经验**
1. **Join时字段类型不匹配**：查询A的service字段是string，查询B的是number（因为标签值是数字），Join会失败。解决：在数据源查询中确保标签值类型一致。
2. **Reduce取Last但不知Last是哪个**：Last取的是数据中最后一个时间戳的值，但实际上如果数据源返回的排序不一致，"Last"可能不是真正的"最新"。解决：Sort by time后再Reduce Last。
3. **Calculate field表达式写入后不生效**：确认字段名不要包含特殊字符和空格，数学运算的字段名用`${}`包裹。表达式引擎支持`+ - * /`，不支持`%`（百分比），需要改写成`/ 100`。

**思考题**
1. 如何用Transform实现一个"服务依赖健康度矩阵"？行是调用方服务，列是被调用方服务，单元格是调用成功率。
2. Transform的Join by field支持Inner和Outer两种模式。什么场景用Inner join会导致数据丢失？什么场景必须用Outer join？
