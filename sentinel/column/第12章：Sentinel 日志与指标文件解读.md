# 第12章：Sentinel 日志与指标文件解读

## 1 项目背景

第 11 章我们学会了用 Dashboard 可视化管理规则和监控流量。但在一次生产事故中，Dashboard 因为内存溢出暂时不可用，运维团队面对用户投诉"下单一直提示系统繁忙"，无法确认是 Sentinel 限流还是下游故障。

开发紧急 SSH 到服务器，用 `tail -f` 看了半天应用日志，只能看到业务日志中的 `BlockException` 堆栈，但不知道是哪个资源、什么规则触发的，更不知道过去 10 分钟的流量趋势。最后花了 40 分钟才定位到根因：一条 3 天前配置的流控规则阈值太低（QPS=5），被最近业务的自然增长触发了限流。

复盘时有人提出：能不能不看 Dashboard，直接读 Sentinel 的日志文件来判断故障？答案是：完全可以。Sentinel 在 `~/logs/csp/` 目录下输出了非常丰富的日志和指标文件，包括秒级 metrics 日志、block 日志、规则变更日志。学会解读这些文件，你就能在 Dashboard 不可用时独立完成以下工作：

- 确认限流/熔断是否真的发生了、发生在哪个资源上
- 统计过去 N 分钟的通过数、拒绝数、平均 RT
- 关联 block 日志和业务日志，还原一条请求的完整路径
- 评估磁盘空间使用量（metrics 日志可能很多）

## 2 项目设计

**小胖**（盯着满屏的日志）："这是什么鬼——`~/logs/csp/` 里有十几个文件！`metric-2024-06-01.log`、`sentinel-record.log`、`block-2024-06-01.log`，到底该看哪个？"

**大师**："三种日志各有用处。`sentinel-record.log` 是 Sentinel 自身的运行日志，包括规则加载、状态变更、异常信息。`metric-*.log` 是秒级指标日志，记录了每个资源的通过/拒绝/异常等数字。`block-*.log` 是拦截日志，记录了每次 BlockException 的详情。"

**小白**："metric 日志的格式是什么？我看到一行有好多数字。"

**大师**："格式是固定的。每一行代表一个资源在 1 秒内的统计信息。我给你拆解一下。"

```
1590999780000|2020-06-01 10:23:00|createOrder|10|2|0|0|25|0|0|0
│              │                    │           │ │ │ │ │  │ │ └─ occupiedPass（预占通过数）
│              │                    │           │ │ │ │ │  │ └── upcoming（未来窗口令牌）
│              │                    │           │ │ │ │ │  └─── exception（异常数）
│              │                    │           │ │ │ │ └── rt（平均响应时间，毫秒）
│              │                    │           │ │ │ └─── success（成功数）
│              │                    │           │ │ └──── block（拒绝数）
│              │                    │           │ └────── pass（通过数）
│              │                    │           └────── timestamp（Unix 毫秒时间戳）
│              │                    └────────── 格式化时间
│              └────────── 秒级时间戳
└── 资源名
```

**小胖**："这个日志太大了！1 天就几百 MB。能不能关掉或压缩？"

**大师**："可以配置。指标日志每秒写一行（每个资源一行，资源多了就大），你可以增大日志文件的索引间隔或者轮转策略。但生产环境建议保留至少 7 天，方便排障和容量分析。"

**小白**："block 日志能关联到业务日志吗？我想知道某个被限流的请求的具体参数。"

**大师**："block 日志只记录资源名、异常类型、规则信息，不记录参数。要关联业务日志，需要在 blockHandler 中主动打印业务上下文（如 requestId、userId），这样就可以通过时间和资源名关联了。"

**小胖**："大师，metric 日志每秒写一行，我们线上有 30 个服务、每个服务有 20 个资源——一天下来就是 30×20×86400 = 5000 万行日志。这磁盘扛得住吗？"

**大师**："你这个估算是上限。实际上大部分资源的流量是 0（秒级日志行不会产生，因为那些资源没有请求）。Sentinel 只在资源被访问的秒才写日志行。所以实际只有活跃的资源产生日志。保守估计按活跃资源数 20% 算，一天也就 1000 万行——约 500 MB。建议按天轮转，保留 7 天。"

**小白**："sentinel-record.log 里哪些日志行是关键日志？出问题时我该搜什么关键词？"

**大师**："三个关键词：`FlowChecker`（流控触发）、`DegradeChecker`（熔断触发）、`SystemChecker`（系统保护触发）。此外，规则变更时会有 `FlowRuleManager.*load` 和 `DegradeRuleManager.*load`。根据故障类型，先搜对应关键词，再按时间线关联。"

**小胖**："那如果我怀疑 Sentinel 的滑动窗口统计有问题（比如实际通过的请求比 metric 日志记录的多），怎么验证？"

**大师**："两个办法交叉验证。第一个：在业务日志中用 AOP 记录每个请求是否通过，然后和同一秒的 metric 日志做对比。第二个：用 JMX 直接读取 Sentinel 内部的滑动窗口数据。`StatisticNode` 暴露了 `totalPass()` 和 `totalBlock()` 方法，可以通过 JMX MBean 访问。注意 AOP 会有额外性能开销，不适合长期开启。"

**技术映射**：
- metric 日志在 `StatisticSlot` 的 `exit()` 中被写入，通过 `MetricWriter` 实现，默认每秒写入一次。
- block 日志在 `LogSlot` 中被写入，每次 BlockException 触发时记录，包含了被拦截的资源、异常类型、规则 ID。
- 日志文件默认路径为 `~/logs/csp/`，可通过 `-Dcsp.sentinel.log.dir=/your/path` 自定义，日志输出目录通过 `-Dcsp.sentinel.log.output.pid=true/false` 控制是否带 PID。

## 3 项目实战

### 3.1 环境准备

先找到 Sentinel 日志目录：

```bash
# 默认路径
ls ~/logs/csp/

# 自定义路径
# 启动参数：-Dcsp.sentinel.log.dir=/var/log/sentinel
```

### 3.2 分步实现

**步骤一：读懂 metric 日志**

查看最新的 metric 文件：

```bash
tail -f ~/logs/csp/metric-$(date +%Y-%m-%d).log
```

示例输出：

```
1622467380000|2024-06-01 10:23:00|createOrder|15|3|12|0|45|0|0|0
1622467381000|2024-06-01 10:23:01|createOrder|12|8|4|0|50|0|0|0
1622467382000|2024-06-01 10:23:02|queryOrder|50|0|50|0|10|0|0|0
1622467382000|2024-06-01 10:23:02|createOrder|10|10|0|0|60|0|0|0
```

解读：
- 第一行：`createOrder` 在 10:23:00 这 1 秒内，通过 15 个，拒绝 3 个，成功 12 个，0 个异常，平均 RT 45ms。
- 第二行：10:23:01 时，通过 12 个，拒绝 8 个（限流生效），成功 4 个。
- 第三行：`queryOrder`，没有限流，通过 50 个，全部成功。
- 第四行：`createOrder` 在 10:23:02，通过 10 个但全部被拒绝！说明阈值被调整或流量异常。

**步骤二：从 metric 日志生成排障报告**

编写一个简单的脚本统计关键指标：

```bash
#!/bin/bash
# metric-report.sh — 统计指定资源的流量指标

RESOURCE=${1:-"createOrder"}
METRIC_FILE=~/logs/csp/metric-$(date +%Y-%m-%d).log

echo "=== Sentinel Metric Report ==="
echo "Resource: $RESOURCE"
echo "Time: $(date)"
echo "=== 最近 5 分钟统计 ==="

# 最近 5 分钟（300 行 ≈ 300 秒）
grep "$RESOURCE" "$METRIC_FILE" | tail -300 | awk -F '|' '
{
    pass += $3; block += $4; success += $5; exception += $6;
    if ($3 > 0) { rt_sum += $7; rt_count++ }
    total++
}
END {
    avg_qps = total / 1;              # 实际为 1，这里简化
    pass_rate = pass / (pass + block) * 100;
    avg_rt = (rt_count > 0) ? rt_sum / rt_count : 0;
    printf "总秒数: %d\n", total;
    printf "总通过: %d, 总拒绝: %d, 通过率: %.1f%%\n", pass, block, pass_rate;
    printf "平均 RT(仅通过请求): %.1f ms\n", avg_rt;
    printf "总异常: %d\n", exception;
}'
```

运行：

```bash
chmod +x metric-report.sh
./metric-report.sh createOrder
```

输出示例：

```
=== Sentinel Metric Report ===
Resource: createOrder
Time: 2024-06-01 10:30:00
=== 最近 5 分钟统计 ===
总秒数: 300
总通过: 2450, 总拒绝: 850, 通过率: 74.2%
平均 RT(仅通过请求): 52.3 ms
总异常: 12
```

**步骤三：读懂 block 日志**

查看 block 日志：

```bash
tail -f ~/logs/csp/block-$(date +%Y-%m-%d).log
```

示例输出：

```
1622467381000|2024-06-01 10:23:01|createOrder|FlowException|default| FlowRule{resource=createOrder, grade=1, count=10.0}
1622467382000|2024-06-01 10:23:02|createOrder|FlowException|default| FlowRule{resource=createOrder, grade=1, count=10.0}
1622467383000|2024-06-01 10:23:03|queryStock|DegradeException|default|
    DegradeRule{resource=queryStock, grade=0, count=500.0, timeWindow=10}
```

解读：
- 前两条：`createOrder` 资源被 QPS 流控（grade=1，即 FLOW_GRADE_QPS），阈值是 10。
- 第三条：`queryStock` 资源触发了熔断降级（DegradeException），熔断时长为 10 秒。

**步骤四：关联 block 日志与业务日志**

案例：某个用户反馈"下单一直失败"，从业务日志找到 requestId：

```bash
grep "REQ-20240601-001" /var/log/order-service/app.log
# 输出：2024-06-01 10:23:01 | REQ-20240601-001 | userId=888 | TraceId=abc123 
#        | BlockException | createOrder
```

时间对应到 `10:23:01`，在 block 日志中找这个时间附近的 createOrder：

```bash
grep "10:23:01.*createOrder.*FlowException" ~/logs/csp/block-2024-06-01.log
```

确认该请求确实被 Sentinel 限流拦截。

**步骤五：日志清理策略**

防止磁盘写满：

```bash
#!/bin/bash
# clean-sentinel-logs.sh — 清理 7 天前的 Sentinel 日志

LOG_DIR=~/logs/csp
RETENTION_DAYS=7

find "$LOG_DIR" -name "metric-*.log" -mtime +$RETENTION_DAYS -delete
find "$LOG_DIR" -name "block-*.log" -mtime +$RETENTION_DAYS -delete
echo "清理完成：已删除 $RETENTION_DAYS 天前的日志"
```

生产环境建议加入 crontab：

```bash
0 2 * * * /opt/scripts/clean-sentinel-logs.sh >> /var/log/cron-sentinel.log 2>&1
```

**步骤六：常见异常日志解析**

| 日志关键字 | 含义 | 排查方向 |
|-----------|------|---------|
| `FlowException` | QPS/线程数限流触发 | 检查流控规则阈值是否合理 |
| `DegradeException` | 熔断触发 | 检查下游服务健康状态、maxAllowedRt 配置 |
| `ParamFlowException` | 热点参数限流触发 | 检查热点 skuId/userId 的流量 |
| `SystemBlockException` | 系统保护触发 | 检查 CPU/Load/RT 是否异常 |
| `AuthorityException` | 授权规则拦截 | 检查请求来源是否符合白名单 |
| `sentinel record log error` | 日志写入失败 | 检查磁盘空间、权限 |

**踩坑记录**：

1. **metric 日志不输出**：检查 `-Dcsp.sentinel.metric.flush.interval` 是否合理（默认 1000ms）。如果在 IDE 中运行，确认工作目录的父目录存在 `logs/csp/`。
2. **日志占用磁盘过大**：单个 metric 文件可以达到 GB 级别（资源数 × 86400 秒 × 每行大小）。建议按天轮转并配置定期清理策略。
3. **日志路径不存在报错**：Sentinel 不会自动创建 `~/logs/csp/` 目录。如果用户目录不可写，日志会静默失败。建议自定义路径到 `/var/log/` 或 `/tmp/`。
4. **日志字段值突然全部为 0**：可能 Sentinel 内部统计窗口轮转，短暂出现全 0 的秒。正常现象，不超过 2 秒。

**步骤七：基于 metric 日志的自动化流量异常检测**

```bash
#!/bin/bash
# anomaly-detector.sh — 检测流量异常

METRIC_FILE=~/logs/csp/metric-$(date +%Y-%m-%d).log
ALERT_BLOCK_RATE=50  # 拒绝率超过 50% 告警

echo "=== 流量异常检测 ==="
for resource in $(grep -oP '^[^|]+' "$METRIC_FILE" | sort -u); do
    # 最近 1 分钟的统计
    PASS=$(grep "$resource" "$METRIC_FILE" | tail -60 | awk -F'|' '{sum+=$3} END{print sum}')
    BLOCK=$(grep "$resource" "$METRIC_FILE" | tail -60 | awk -F'|' '{sum+=$4} END{print sum}')
    TOTAL=$((PASS + BLOCK))

    if [ "$TOTAL" -gt 0 ]; then
        BLOCK_RATE=$((BLOCK * 100 / TOTAL))
        if [ "$BLOCK_RATE" -gt "$ALERT_BLOCK_RATE" ]; then
            echo "[WARN] $resource: 拒绝率异常 ${BLOCK_RATE}% (通过=$PASS, 拒绝=$BLOCK)"
        fi
    fi
done
```

**步骤八：将 Sentinel 指标接入 Prometheus**

引入 `sentinel-prometheus-exporter` 依赖后，Sentinel 可暴露 Prometheus 指标端点：

```java
@Configuration
public class SentinelPrometheusConfig {

    @PostConstruct
    public void init() {
        // 注册 Prometheus 指标导出器
        SentinelMetricExtension.addSentinelMetrics();
    }
}
```

访问 `/actuator/prometheus` 可看到 Sentinel 相关指标：

```
# HELP sentinel_pass_qps_total Total pass QPS
# TYPE sentinel_pass_qps_total gauge
sentinel_pass_qps_total{resource="createOrder",} 15.0
sentinel_block_qps_total{resource="createOrder",} 3.0
sentinel_rt_avg{resource="createOrder",} 45.0
```

## 4 项目总结

### 4.1 优点与缺点

| 维度 | 直接读 Sentinel 日志 | Dashboard 监控 | Prometheus + Grafana |
|------|-------------------|---------------|---------------------|
| 上手成本 | 低（文本文件） | 中 | 高（需部署监控栈） |
| 实时性 | 延迟 1 秒（写入周期） | 延迟 1 秒（拉取周期） | 取决于采集间隔 |
| 历史数据 | 有（取决于保留天数） | 无（内存数据） | 有（取决于存储大小） |
| 聚合分析 | 需脚本 | Dashboard 内置 | 强大（PromQL） |
| Dashboard 故障时可用 | ✅ 完全可用 | ❌ 不可用 | ✅ 可用 |

### 4.2 适用场景

- Dashboard 不可用时的应急排障
- 容量评估（统计历史 QPS 趋势）
- 自动化巡检脚本（每天统计限流/熔断频率）
- 成本分析（某个接口到底消耗了多少服务器资源）
- 不适用：需要实时拓扑图和依赖关系的场景（Dashboard 的簇点链路更合适）

### 4.3 注意事项

1. metric 日志的格式在不同 Sentinel 版本中可能略有差异（新版本增加了字段），解析脚本需兼容性考虑。
2. Windows 下日志路径为 `C:\Users\用户名\logs\csp\`，与 Linux/Mac 不同。
3. metric 日志不记录熔断状态切换的时间点（只知道那一秒有异常），状态切换在 sentinel-record.log 中。
4. 如果启用了集群流控，集群级别的 metric 日志路径可能不同。

### 4.4 日志文件类型与用途速查

| 文件 | 写入频率 | 内容 | 排障场景 |
|------|---------|------|---------|
| `metric-YYYY-MM-DD.log` | 每秒 | 每个资源的 pass/block/rt 统计 | "哪个资源的 block 最多？" |
| `block-YYYY-MM-DD.log` | 每次 Block | BlockException 详情含规则信息 | "谁触发了哪条规则的限流？" |
| `sentinel-record.log` | 事件驱动 | 规则加载、状态变更、系统事件 | "规则什么时候加载的？熔断何时触发？" |

### 4.5 日志驱动排障 SOP

```
问题: 大量用户反馈"系统繁忙"
│
├─ Step 1: 确认是 Sentinel 拦截还是业务异常
│   $ tail -100 ~/logs/csp/block-$(date +%Y-%m-%d).log
│   有输出 → Sentinel 拦截，继续 Step 2
│   无输出 → 业务异常，查看应用日志
│
├─ Step 2: 定位被拦截的资源
│   $ grep "block" block-*.log | awk -F'|' '{print $3}' | sort | uniq -c | sort -rn
│   输出 Top 3 被拦截资源名
│
├─ Step 3: 确认触发的是哪种规则
│   $ grep "<资源名>" block-*.log | awk -F'|' '{print $4}' | sort | uniq -c
│   FlowException → 流控   DegradeException → 熔断
│   ParamFlowException → 热点   SystemBlockException → 系统保护
│
└─ Step 4: 量化影响面
    $ ./metric-report.sh <资源名>
    输出最近 5 分钟的通过/拒绝比例
```

### 4.4 常见踩坑经验

| 故障现象 | 根因 | 解决方案 |
|---------|------|---------|
| 日志里有 BlockException 但应用日志看不到 | BlockException 被全局异常处理器吞掉 | 检查全局异常处理逻辑 |
| metric 日志中某秒通过数异常大 | 滑动窗口边界的"过冲"现象 | 正常，可接受 10-20% 误差 |
| block 日志正常但 metric 中 block 数为 0 | 日志写入异步，时间未对齐 | 用更长时间窗口统计 |

### 4.5 思考题

1. metric 日志中的 `occupiedPass` 和 `pass` 有什么区别？什么情况下需要关注 `occupiedPass` 字段？
2. 如果启用了集群流控，metric 日志中的 `block` 数反映的是集群级别还是单机级别的拒绝？如何区分？

### 4.6 推广计划

- **运维团队**：将 Sentinel 日志纳入日常巡检（每日脚本统计 top 3 被限流资源 + top 3 被熔断资源）。
- **测试团队**：压测时同步采集 metric 日志，用于生成压测报告中的热点数据。
- **开发团队**：在核心接口的 blockHandler 中打印结构化日志（traceId + 资源名 + 规则 ID），方便排障。
