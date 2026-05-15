# 第26章：Dashboard性能优化

## 1. 项目背景

"Dashboard有60个面板，每次打开要等25秒。运维说服务器CPU才用了15%，不存在性能瓶颈。那问题到底出在哪？"

这是Grafana大规模使用中最常见的痛点——Dashboard加载慢但找不到明显资源瓶颈。事实上，Grafana Dashboard加载涉及多个环节：浏览器渲染、后端查询、数据源响应、数据库读取。任何一个环节的瓶颈都会导致用户看"Loading..."转圈。

本章从Query Inspector工具出发，系统性地诊断和优化Dashboard加载性能。目标是让一个60面板的Dashboard从25秒降到3秒以内。

## 2. 项目设计

**小胖**：大师，我Dashboard有60个面板，加载要25秒。我加了CPU、加了内存、加了带宽——都没用。到底慢在哪？

**大师**：Dashboard性能优化不能用猜的，要用工具。Grafana内置了一个神器——Query Inspector。你打开Dashboard后，按`Ctrl+Shift+I`（或点击面板标题→Inspect→Query），可以看到每个面板查询的详细耗时。

通常Dashboard加载慢有三种可能：
1. **查询慢**：某个PromQL/SQL查询本身执行慢（占80%的慢Dashboard问题）
2. **渲染慢**：浏览器端JS执行慢（面板很多时出现）
3. **数据量大**：查询返回的数据点太多，浏览器内存爆炸

**小白**：那具体怎么诊断？

**大师**：分四步走。

**第一步：Query Inspector定位慢查询**

打开Dashboard → Query Inspector → 查看每个面板查询的Duration。通常你会看到：大部分面板查询<500ms，但有几个面板查询>5s。这几个"慢查询"就是瓶颈。

因为Grafana并发查询限制默认20个，如果前20个查询中有5个慢查询（各5秒），整个Dashboard就要等至少5秒。

**第二步：优化慢PromQL**

常见原因和优化：
1. **范围太大**：`rate(metric[5m])`改成`rate(metric[1m])`（但要确保准确性）
2. **使用了高基数标签**：`sum by (pod)`有1000个Pod→改成`sum by (deployment)`
3. **没使用Recording Rules**：复杂PromQL每秒被10个Dashboard引用→改成Recording Rule预计算
4. **查询时间范围太长**：Dashboard选"Last 30 days"，Prometheus扫描30天的Block→用更大的Min interval

**第三步：减少面板数量**

60个面板太多了。按第16章的三层Dashboard架构拆分：
- 总览Dashboard：5-8个关键面板
- 服务Dashboard：10-15个面板
- 详情Dashboard：需要时再打开

**第四步：面板级优化**

每个面板独立设置Max data points（800-2000）、Min interval（15s-5m）、Cache timeout。

**小胖**：那浏览器端优化呢？我有些Dashboard里面Table超大，滚动都卡。

**大师**：Table数据量过大是前端渲染杀手。解决：
1. 分页：Table面板开启分页，每页<50条
2. 减少列：不必要的列隐藏
3. 延迟Schema：复杂Table可以用Transform先Reduce再渲染

**技术映射**：Query Inspector = 体检报告（精确告诉你哪个器官出问题），慢查询 = 堵车路段（找到它治理它，整条路就通了），面板拆分 = 分库（数据过多把一个Dashboard拆成多个）。

## 3. 项目实战

**环境准备**：使用之前的Docker Compose环境。

**步骤一：Query Inspector深度使用**

打开任意Dashboard → 按F12打开浏览器DevTools → Network标签 → 筛选XHR请求。

找`ds/query`请求（数据源查询），看Response Time。

更精细的方式——面板级别Query Inspector：
1. 点击面板标题 → Inspect → Query
2. 查看每个Query的"Total time"和"Data points"
3. 如果Data points > 10000 → 调小Max data points

**步骤二：PromQL优化实战**

**场景：QPS查询返回1000+条序列**

优化前：
```promql
rate(http_requests_total{env="prod"}[5m])
```
如果`http_requests_total`有1000个实例标签，返回1000条序列，Grafana渲染1000根线。

优化后：
```promql
# 1. 先聚合到服务维度
sum by (service) (rate(http_requests_total{env="prod"}[5m]))

# 2. 如果必须看实例，topk只显示前5
topk(5, sum by (instance) (rate(http_requests_total{env="prod"}[5m])))
```

**场景：30天查询慢**

优化前：查询`rate(metric[30d])`——Prometheus加载30天的全量Block。

优化后：
```promql
# 使用Recording Rule预计算5分钟rate
# recording rule: instance:requests:rate5m = rate(http_requests_total[5m])
instance:requests:rate5m
```
Dashboard查询从扫描全量Block变为直接取预聚合结果——速度提升100倍。

**步骤三：Dashboard架构级优化**

用API工具分析慢Dashboard：
```bash
# 列出某个Dashboard的所有面板查询
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:3000/api/dashboards/uid/<uid> | \
  jq '.dashboard.panels[].targets[].expr'
```

统计：有60个面板，其中40个涉及HTTP指标查询。这些查询重叠度高。

优化方案：拆分为3个Dashboard
- `系统总览（轻量级）`：8个面板，打开<2s
- `HTTP服务详情`：20个面板，打开<5s
- `数据库详情`：15个面板，按需打开

**步骤四：缓存与性能配置**

grafana.ini性能相关配置：
```ini
[dataproxy]
timeout = 30
dialTimeout = 30
keep_alive_seconds = 30

[query_history]
enabled = true

[panels]
disable_sanitize_html = false

# Alerting评估引擎
[alerting]
enabled = true
execute_alerts = true
evaluation_timeout = 30
max_attempts = 3

# 查询缓存（企业版原生支持，OSS用Redis扩展）
[caching]
enabled = true
type = redis
```

Dashboard级别缓存：Settings → General → Cache timeout → 60s

**步骤五：性能基准测试**

测试脚本：
```bash
#!/bin/bash
# perf-test.sh
TOKEN="glsa_xxx"
DASHBOARD_URL="http://localhost:3000/api/dashboards/uid/test-dashboard"
ITERATIONS=10

echo "Dashboard加载性能测试"
for i in $(seq 1 $ITERATIONS); do
    TIME=$(curl -s -o /dev/null -w '%{time_total}' \
      -H "Authorization: Bearer $TOKEN" $DASHBOARD_URL)
    echo "Request $i: ${TIME}s"
    sleep 1
done

# 数据源查询性能
echo -e "\n数据源查询性能"
curl -s -o /dev/null -w '
DNS解析: %{time_namelookup}s
TCP连接: %{time_connect}s
SSL握手: %{time_appconnect}s
首字节: %{time_starttransfer}s
总耗时: %{time_total}s
' -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"queries":[{"refId":"A","datasource":{"uid":"prometheus"},"expr":"up"}]}' \
  http://localhost:3000/api/ds/query
```

**常见坑点**
1. **所有查询共享同一个DataSource连接池**：200个面板查询共用一个Prometheus连接池→查询排队。解决：增加连接数或创建多个相同的数据源实例分散负载。
2. **Template Variables刷新触发全量查询**：变量变化→所有面板重新查询→20个面板同时发请求。解决：减少面板数量或增加变量缓存。
3. **HTML面板加载外部资源**：Text面板中嵌入了iframe或外部图片→阻塞Dashboard渲染。

**步骤七：真实案例——优化一个"加载30秒"的Dashboard**

某电商团队有一个"全站总览"Dashboard，136个面板，加载时间约30秒。按以下步骤优化：

**诊断阶段**：
```bash
# 用curl模拟Dashboard加载测试
time curl -s -H "Authorization: Bearer $TOKEN" \
  "http://localhost:3000/api/dashboards/uid/fullsite-overview" > /dev/null
# 输出：real 28.5s
```

打开Query Inspector，发现Top5慢查询：
```
1. sum(rate(http_requests_total[30d])) → 12.3s (扫描30天数据)
2. histogram_quantile(0.99, rate(bucket[30d])) → 8.7s
3. count by (pod) (kube_pod_info) → 4.2s (1200个pod)
4. topk(100, rate(metric[7d])) → 3.1s
5. node_filesystem_size_bytes → 2.8s (所有磁盘)
```

**优化措施**：
1. 查询1和2改为Recording Rules（预计算5min rate）→ 查询时间从12s降到0.2s
2. 查询3添加namespace过滤，按deployment聚合而非pod → 从1200条降到50条
3. 查询4将topk(100)改为topk(10)，只关注Top10 → 从3.1s降到0.5s
4. 查询5添加mountpoint=~"/"过滤 → 从2.8s降到0.3s
5. 将136个面板拆分为5个Dashboard（按模块分：网关/订单/用户/支付/基础设施）

**优化效果**：
```
优化前：加载时间 28.5s，并发查询峰值 95
优化后：加载时间 2.1s，并发查询峰值 15
Dashboard打开速度提升 13.5倍
```

**自动化性能监控脚本**：
```bash
#!/bin/bash
# 定期检测Dashboard性能退化
THRESHOLD=5  # 超过5秒告警

DASHBOARD_UID="fullsite-overview"
LOAD_TIME=$(curl -s -o /dev/null -w '%{time_total}' \
  -H "Authorization: Bearer $TOKEN" \
  "http://localhost:3000/api/dashboards/uid/$DASHBOARD_UID")

if (( $(echo "$LOAD_TIME > $THRESHOLD" | bc -l) )); then
    echo "WARNING: Dashboard $DASHBOARD_UID loading in ${LOAD_TIME}s (threshold: ${THRESHOLD}s)"
    # 发送告警到Slack
fi
```

## 4. 项目总结

**性能优化检查清单**

| 优化项 | 预期收益 | 实施难度 |
|--------|---------|---------|
| 拆分Dashboard | 50-70% | 中 |
| 减少面板数量 | 30-50% | 低 |
| 优化PromQL | 20-80% | 中 |
| Recording Rules | 80-95% | 中 |
| 增加Max data points限制 | 20-40% | 低 |
| 数据库迁移到PostgreSQL | 20-50% | 高 |
| 查询缓存 | 50-90%（重复查询） | 低 |
| 升级Grafana版本 | 10-30% | 低 |

**注意**
1. 优化前务必用Query Inspector基准测试，优化后才能量化效果
2. "一个Dashboard解决所有问题"是坏实践，推广"小而精"的Dashboard
3. 查询缓存可能导致数据延迟——实时监控Dashboard不要设太长缓存

**思考题**
1. 如果Dashboard中有一个Panel的查询需要5秒（查询第三方慢API），但其他Panel只需要200ms。如何让这个慢Panel不阻塞其他Panel的加载？
2. Grafana的`min_interval`设为10s，但Prometheus的采集间隔是15s。这两个参数不匹配会有什么后果？

**压测验证脚本——量化性能优化效果**

优化完成后，用自动化脚本持续监控Dashboard加载性能：

```bash
#!/bin/bash
# perf-benchmark.sh - Dashboard性能基准测试

DASHBOARDS=("system-overview" "app-monitor" "business-dashboard")
TOKEN="glsa_xxx"
GRAFANA_URL="http://localhost:3000"

echo "Dashboard性能基准测试报告"
echo "=========================="
for uid in "${DASHBOARDS[@]}"; do
    echo -e "\n=== Dashboard: $uid ==="
    
    # 冷却1秒
    sleep 1
    
    # 测试5次取平均
    total=0
    for i in {1..5}; do
        time=$(curl -s -o /dev/null -w '%{time_total}' \
            -H "Authorization: Bearer $TOKEN" \
            "$GRAFANA_URL/api/dashboards/uid/$uid")
        total=$(echo "$total + $time" | bc)
    done
    avg=$(echo "scale=2; $total / 5" | bc)
    
    echo "  平均加载时间: ${avg}s"
    
    if (( $(echo "$avg > 3" | bc -l) )); then
        echo "  ⚠️  性能警告：加载时间超过3秒"
    else
        echo "  ✅ 性能正常"
    fi
done
```

将此脚本加入CI/CD或crontab，Daily监控Dashboard加载性能退化。当加载时间超过基线2倍时自动告警，通知相关团队排查最近的配置变更或数据增长导致的性能退化。
