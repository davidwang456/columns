# 第19章：PromQL进阶——子查询、向量匹配与数学建模

## 1. 项目背景

大数据团队的运维工程师小赵最近接到一个棘手需求：老板想看"过去30天中，每天QPS峰值的7日移动平均线"。乍一听并不复杂——不就是算QPS再求平均吗？可小赵一动手就发现不对劲：`rate()` 能算出QPS，`max_over_time()` 能取每日峰值，但如何把这两个操作串联起来？`rate()` 返回的是 instant vector，`max_over_time()` 需要 range vector 作为输入——它们在时间维度上无法直接衔接。

另一个组的同事也在焦头烂额：需要对比 Pod 的 CPU 实际使用量（来自 cAdvisor）和 CPU request（来自 kube-state-metrics），但这两个指标来自不同的 job，label 也不完全一致——cAdvisor 里叫 `pod="nginx-abc123"`，kube-state-metrics 里叫 `pod="nginx"`，连 namespace 的 key 名都不同。两个指标直接做除法，Prometheus 返回空结果，仿佛它们在两个平行宇宙。

小赵还发现一个工作流痛点：老板经常在周一早上问"上周五下午2点那个峰值到底是多少"，每次都要在 Prometheus 时间选择器中小心翼翼地拖动回退，稍不留神就偏差了几分钟。其实用 `@` 修饰符可以直接锚定到精确时间戳，不需要跟时间选择器较劲。

这三个场景暴露出一个共同问题：**基础 PromQL 掌握后，面对生产环境的复杂查询仍需进阶技巧**。子查询（subquery）让你在时间维度上做二次聚合——先把每分钟的 rate 算出来，再对结果集做小时级或天级的 max/sum/avg。`@` 修饰符让历史分析有了精确锚点，不受界面时间选择器干扰。向量匹配（on/ignoring + group_left/group_right）让你跨越不同 exporter 的 label 体系，把分散在多处的指标串联成一张大表。这三个特性用好了，能解决 90% 的复杂监控查询需求。

## 2. 剧本式交锋对话

**小胖**（抓耳挠腮地盯着 Prometheus Web UI）：
大师！我卡住了。老板要"过去7天每天CPU峰值的移动平均"。我先用 `rate(node_cpu_seconds_total[5m])` 算出每秒的CPU变化率，然后想用 `max_over_time()` 取每天最大值，但 `max_over_time()` 只能接 range vector，不能接 `rate()` 返回的 instant vector 啊！PromQL 能不能做这种"先算一层，再算一层"的嵌套？

**大师**（放下手中的咖啡杯）：
小胖，你需要的叫**子查询**（subquery）。语法是把内层 instant vector 查询用方括号包起来，后面跟上 `<range>:<resolution>`。比如每分钟算一次CPU使用率，然后取过去1小时里每分钟的最大值：

```promql
max_over_time(
  (100 - avg(rate(node_cpu_seconds_total{mode="idle"}[5m])) by (instance) * 100)[1h:1m]
)
```

这里的 `[1h:1m]` 就是子查询——外层范围1小时，步长1分钟。本质是：**Prometheus 会从1小时前到现在，每隔1分钟执行一次内层查询，把60个结果点拼成一段 range vector，再喂给 `max_over_time()`**。这就是时间维度上的"二次聚合"。典型场景有三个：容量预测时用 `max_over_time + predict_linear` 拟合峰值增长曲线；SLO 合规统计时用 `sum_over_time` 汇总全月错误量；数据平滑时用 `avg_over_time` 过滤毛刺。

**小白**（若有所思）：
那偏移时间呢？我想查昨天上午10点的CPU，总不能每次都手动拖时间选择器吧？

**大师**：
这就是 `@` 修饰符的使用场景。它和 `offset` 是两码事——`offset` 是相对偏移，`metric offset 1h` 意思是"从现在往前挪1小时"；`@` 是绝对时间戳锚点，`metric @ 1705312800` 意思是"在时间戳 1705312800 这一刻评估"。更实用的写法是：

```promql
rate(node_cpu_seconds_total{mode="idle"}[5m] @ (time() - 86400))
```

`time() - 86400` 算出24小时前的时间戳，`@` 把整个 range vector 终点锚定到那个时刻。这样不管你什么时候打开 Prometheus，查的都是同一个历史时间点。想做同比（昨天同一时刻）、环比（上周同一时刻），一条查询搞定。

**小胖**（眼睛一亮，旋即又皱眉）：
子查询和时间锚点我大概懂了。但更头疼的事——我要把 cAdvisor 的 Pod CPU 使用量和 kube-state-metrics 的 Pod CPU request 做除法，两个指标的 label 不一样，直接除返回空。怎么破？

**大师**：
这就是**向量匹配**（vector matching）要解决的问题。Prometheus 默认匹配要求两个指标的 label 集合完全相等，否则视为不同的时间序列。你的情况有两种解法：

**方案一**——忽略多余 label，用 `ignoring()`：
```promql
pod_cpu_usage / ignoring(container, endpoint) pod_cpu_request
```
意思是在做匹配时忽略 container、endpoint 这些只在一边存在的 label，只按共同 label（如 pod、namespace）匹配。

**方案二**——指定匹配键，用 `on()`：
```promql
pod_cpu_usage / on(pod, namespace) pod_cpu_request
```
只按 pod 和 namespace 两个 label 做交集匹配。

如果你的场景是一对多或多对一——比如 kube_namespace_labels 每个 namespace 只有一条记录，而 pod_cpu_usage 有上千条——就需要 `group_left` 或 `group_right` 控制以哪边为准：

```promql
pod_cpu_usage * on(namespace) group_left(label_team) kube_namespace_labels
```

`group_left` 的意思是"以左边（多的一方）为准，从右边（一的一方）拉取额外 label"。结果中每条 pod_cpu_usage 都会带上 `label_team`，就像 SQL 的 `LEFT JOIN`。反过来如果想从左边向右边聚合，就用 `group_right`。

**小胖**（恍然大悟）：
明白了！`on()` 控制匹配键，`group_left/right` 控制方向。那子查询 + 向量匹配结合，岂不就能做"按团队统计30天SLI合规率"这种复杂分析了？

**大师**（颔首）：
正是如此。你刚才三个技术点串起来——子查询做时间维度切片、`@` 修饰符锚定对比基准、向量匹配跨指标关联——正是 PromQL 从"能查"到"查得好"的关键一跃。

## 3. 项目实战

### 环境准备

- Prometheus 运行中，已采集 Node Exporter 数据（至少数小时历史数据）
- 推荐使用 https://promlabs.com/promql-testing 在线工具测试 PromQL
- 打开 Prometheus Web UI（默认 `http://localhost:9090`），进入 Graph 页面

### 步骤1：子查询实战——每日峰值和移动平均

场景：计算过去7天中每天的最大CPU使用率，再求7天的平均值。先在 Web UI 中逐步构建查询，逐层理解。

**第一层——每分钟的CPU使用率**：
```promql
100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[1m])) by (instance) * 100)
```
返回每个 instance 当前分钟CPU使用百分比，是一个 instant vector。

**第二层——每天的最大值**：
```promql
max_over_time(
  (100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[1m])) by (instance) * 100))[7d:1d]
)
```
子查询 `[7d:1d]` 让 Prometheus 在过去7天中每隔1天评估一次内层查询，`max_over_time()` 从7个采样点中取最大值。

**第三层——7日平均峰值**（核心目标）：
```promql
avg_over_time(
  (max_over_time(
    (100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[1m])) by (instance) * 100))[1d:5m]
  ))[7d:1d]
)
```
两层嵌套：内层 `max_over_time(...)[1d:5m]` 每天取一个峰值（步长5m保证精度），外层 `avg_over_time(...)[7d:1d]` 对7个峰值求平均。在 Web UI 中逐层删除外层查询对比中间结果，理解子查询的层次结构。

**实用案例——持续高负载检测**：
```promql
max_over_time(
  (100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[5m])) by (instance) * 100) > 80)[1h:1m]
) == 1
```
子查询 `[1h:1m]` 每分钟评估一次 `> 80` 条件（true=1, false=空），`max_over_time()` 检查60个点是否有一个为1。`== 1` 筛选出"过去1小时内至少有一分钟CPU超过80%的instance"。

### 步骤2：@修饰符——精确时刻查询

场景：对比昨天上午10点和上周同一时刻的CPU使用率，无需手动回退时间选择器。

```promql
# 昨天上午10点（time() 返回当前秒数，86400 = 24小时）
100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[5m] @ (time() - 86400))) by (instance) * 100)

# 上周同一天上午10点（604800 = 7天）
100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[5m] @ (time() - 604800))) by (instance) * 100)

# 指定具体历史时间戳（如 2024-01-01 00:00:00 UTC）
100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[5m] @ 1704067200)) by (instance) * 100)
```

**@和offset的关键区别**：`offset` 是相对偏移——`[5m] offset 1h` 把5分钟窗口整体向历史平移1小时；`@` 是绝对锚定——`[5m] @ (time() - 3600)` 把窗口终点固定在1小时前的时刻。前者"相对于现在"，后者"相对于时间戳"。当需要精确回溯某个告警触发时刻的指标值时，`@` 比回退时间选择器可靠得多。

**限制**：`@` 修饰符不能用在 recording rules 中，因为 recording rules 按自身调度时间评估，会忽略 `@`。它仅适用于 ad-hoc 查询和 Grafana 面板。

### 步骤3：向量匹配——一对一

场景：计算每个 instance 的 CPU 使用总量（核心数 × 使用率）。Node Exporter 的 `instance` 是 `"node1:9100"`（带端口），而自定义的 `machine_cpu_cores` 指标中 `instance` 是 `"node1"`（无端口），两个 label 格式不一致。

**方案A：用 label_replace 统一格式**：
```promql
label_replace(
  (100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[5m])) by (instance) * 100)),
  "short_instance", "$1", "instance", "(.+):.+"
)
/ on(short_instance) group_left
machine_cpu_cores * 100
```
`label_replace()` 从 `instance` 中用正则 `(.+):.+` 提取主机名（去掉端口），存到新标签 `short_instance`。然后 `on(short_instance)` 按统一标签匹配。

**方案B：用 ignoring 忽略多余 label**：
```promql
(100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[5m])) by (instance) * 100))
/ ignoring(job, mode) group_left
machine_cpu_cores
```
`ignoring(job, mode)` 告诉 Prometheus 匹配时忽略这两个只在一边存在的 label，只要 instance 相同就算匹配。这种写法更简洁，适用于 label 差异较少的场景。

### 步骤4：向量匹配——多对一

场景：Pod 级指标 `pod_cpu_usage{pod, namespace}` 只含基础维度，需要按团队（`label_team`）汇总。`label_team` 仅存于 `kube_namespace_labels{namespace, label_team}`，每个 namespace 一条记录。

**第一步——给 Pod 指标打上 team 标签**：
```promql
pod_cpu_usage
* on(namespace) group_left(label_team)
kube_namespace_labels
```
`group_left` 表示以左边（Pod 级，多的一方）为准，从右边（namespace 级，一的一方）拉取 `label_team`。结果中每行 pod_cpu_usage 新增 `label_team` 字段。

**第二步——按 team 汇总**：
```promql
sum(
  pod_cpu_usage
  * on(namespace) group_left(label_team)
  kube_namespace_labels
) by (label_team)
```

**延伸——Pod与Node的关联**：
```promql
pod_cpu
* on(pod, namespace) group_left(node)
kube_pod_info
```
`on()` 中写两个 label 防止跨 namespace 同名冲突，`group_left(node)` 将 node 标签拉到 Pod 指标上。

### 步骤5：综合实战——SLO合规率计算

场景：计算过去30天内 HTTP 500 错误请求占比，判断是否满足 99% 可用性 SLO。

```promql
# 30天错误率
sum_over_time(
  (sum(rate(http_requests_total{status="500"}[5m])))[30d:5m]
)
/
sum_over_time(
  (sum(rate(http_requests_total[5m])))[30d:5m]
)
```

子查询 `[30d:5m]` 以5分钟为步长遍历30天数据（约8640个采样点），`sum_over_time()` 对采样点求和。用5分钟步长而非1分钟，是在精度和性能间取平衡——30天窗口 + 1分钟步长会产生43200个采样点，每个点还要评估一次 `rate()`，极易触发 OOM。

### 可能遇到的坑

1. **子查询 resolution 太细导致 OOM**：`[30d:1m]` 产生 43200 个采样点，每个点重新执行内层查询，内存和计算开销极大。原则是 **步长 ≥ 内层 range vector 窗口**，30天用5m足够。

2. **on() 中指定的 label 不在某个指标中**：直接返回空结果，无报错。建议先用 `label_replace()` 统一格式，或用 `ignoring()` 处理不对称场景。

3. **group_left/group_right 用反导致数据膨胀**：多的一方在左用 `group_left`，在右用 `group_right`。用反不会报错，但会导致结果值翻倍——因为少的一方的记录被重复匹配。

4. **@修饰符在 recording rules 中不生效**：recording rules 忽略 `@`，始终按自身评估时间执行。需要定时刻查询只能在 ad-hoc 或 Grafana 面板中使用。

### 测试验证

在 Prometheus Web UI 中依次执行：
- 步骤1的子查询：切换到 Table 视图，每个 instance 一行，Value 为7日峰值数字
- 步骤3的向量匹配：结果行数与两个指标交集匹配，Value 为正常百分比
- 步骤4的 group_left：结果中包含 `label_team` 列，每个 Pod 都对其所属 team

## 4. 项目总结

### 子查询语法速查表

| 场景 | 模板 | 说明 |
|------|------|------|
| 日内峰值 | `max_over_time((query)[24h:5m])` | 过去24小时每5分钟采样取最大 |
| SLO 累计 | `sum_over_time((rate(errors[5m]))[30d:5m])` | 30天累计错误总量 |
| 移动平均 | `avg_over_time((query)[7d:1d])` | 7天均值平滑去毛刺 |
| 持续时间检测 | `max_over_time((query > T)[1h:1m]) == 1` | 过去1小时是否触发过阈值 |

### @修饰符场景速查

| 场景 | 写法 | 说明 |
|------|------|------|
| 同比 | `metric[5m] @ (time() - 86400)` | 24小时前同一时刻 |
| 环比 | `metric[5m] @ (time() - 604800)` | 7天前同一时刻 |
| 快照 | `metric[5m] @ 1704067200` | 指定历史时间戳 |
| 告警回溯 | `metric @ $alert_ts` | 告警模板中传入时间戳 |

### 向量匹配决策树

```
两个指标要运算？
├── label 集合完全一致？
│   └── YES → 直接运算（默认一对一）
├── label 不完全一致？
│   ├── 只需指定匹配键 → on(label1, label2)
│   └── 只想忽略个别差异键 → ignoring(label1, label2)
└── 一边多、一边少？
    ├── 多的一方在左 → ... group_left
    └── 多的一方在右 → ... group_right
```

### 适用场景

- **SLO/SLI 计算**：子查询 + sum_over_time 实现月季年度合规率统计
- **容量规划**：`predict_linear(max_over_time(cpu[1d:5m])[30d:1d], 86400*30)` 预测未来30天峰值趋势
- **多维分析**：向量匹配将 team、cluster、region 等元数据标签注入指标，实现跨维度聚合
- **历史对比**：@修饰符精确对比同一时刻不同日期的数据，实现自动化同比/环比分析

### 注意事项

- 子查询消耗资源大，避免大范围小步长的组合（如 `[30d:10s]`）
- 向量匹配前确保匹配 label 已对齐，必要时用 `label_replace()` 清洗
- `@` 修饰符支持浮点数表达式，如 `@ (time() - 3600.5)`
- 确认 Prometheus 版本 ≥ 2.7.0（子查询从该版本开始支持）

### 常见踩坑经验

1. **子查询 OOM 事故**：某团队用 `[7d:10s]` 查询7天数据，步长10秒产生60480个采样点，且每个点重新执行 `rate()`，导致 Prometheus 内存飙升至32GB触发 OOM Kill。修正为 `[7d:5m]` 后采样点降至2016个，查询耗时从超时变为秒级返回。

2. **group_left 用错导致数据翻倍**：写成了 `pod_cpu / on(namespace) group_right kube_ns_labels`，因为 `group_right` 以右边（少的一方）为准，导致左侧多条 Pod 记录被压缩为1条后做除法，总 CPU 翻倍。应改为 `group_left`。

3. **@修饰符在 recording rule 中静默失效**：在 recording rule 中写了 `metric @ (time() - 3600)`，每次评估结果都相同——recording rule 忽略 `@` 修饰符，始终按自身调度时间评估。定时刻查询必须写在 ad-hoc 或 Grafana 面板的 PromQL 中。

### 思考题

1. 如何用子查询计算"过去30天中，每天0点到6点之间的最大QPS"？（提示：结合 `@` 修饰符先定位每天0点，再用子查询取6小时窗口内的峰值）

2. 两个指标 `A{pod, namespace}` 和 `B{node, namespace}`，如何在 node 级别聚合 pod 指标？需要使用什么向量匹配技术？（提示：需要一个中间指标 `C{pod, node}` 建立 pod→node 映射，通过两次 `group_left` 实现三级关联）

---

*子查询让你驾驭时间维度，向量匹配让你跨越指标体系，@修饰符让你回归历史现场。三者得兼，PromQL 方显真章。*
