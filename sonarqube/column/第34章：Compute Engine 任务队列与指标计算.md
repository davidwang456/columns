# 第34章：Compute Engine 任务队列与指标计算

## 1. 项目背景

**业务场景**：某公司在 CI 高峰期（上午 9-11 点）集中触发 50+ 项目扫描，SonarQube 的 CE 队列经常积压。开发者提交扫描后，5-10 分钟才在 Web UI 看到结果——这大大降低了门禁的即时反馈价值。运维团队需要理解 CE 的内部机制：任务如何调度？Worker 如何分配？指标如何计算？质量门禁何时评估？

**痛点放大**：CE 是 SonarQube 的"心脏"——所有扫描结果的入库、指标计算、门禁评估都在这里完成。CE 的性能瓶颈直接决定了 SonarQube 平台的吞吐量。

**更多现实场景**：

- **场景一：早起高峰拥堵**：每天早上 9:00，CI Pipeline 批量触发 50+ 个项目扫描。Scanner 在 2 分钟内跑完分析上传报告，但 CE 队列已经排了 40 多个任务。开发者等到 9:15 才在 Web UI 看到结果，质量门禁反馈延迟了整整 13 分钟——远超出"5 分钟内反馈"的 SLA。

- **场景二：大项目"堵车"**：某个单体 Java 项目（50 万行代码）的 CE 处理耗时 8 分钟。当它排在队列前头时，后续 30 个小项目的任务都得等它处理完——典型的"队头阻塞"问题。

- **场景三：指标计算失败难排查**：某次扫描后覆盖率突然从 80% 掉到 40%，开发团队以为是代码问题，排查了半天才发现是 CE 计算派生指标时，JaCoCo 报告解析失败导致的。但 Scanner 日志里没有报错，因为报告是 Scanner 上传后由 CE 异步处理的。

**关键问题清单**：
1. CE Worker 数量如何确定？什么时候该从 1 个加到 3 个？
2. 哪些指标是 Scanner 计算的，哪些是 CE 计算的？
3. CE 任务失败后如何自动重试？重试机制是怎样的？
4. 如何监控 CE 队列的健康状态并设置告警？
5. 派生指标（如 Technical Debt Ratio）的计算逻辑是什么？

## 2. 项目设计

### 剧本式交锋对话

---

**小胖**（提交了扫描，5 分钟了 Web UI 还是空的）："大师，扫描明明显示 SUCCESS 了，为什么等了 5 分钟 Web UI 才有数据？这 5 分钟 CE 在干什么？"

**大师**："CE 不是立刻处理你的任务——如果前面排着 50 个任务，你的任务就在队列里等着。CE Task 有五个状态：

1. **PENDING**：任务已创建，等待 Worker 取走
2. **IN_PROGRESS**：Worker 正在处理
3. **SUCCESS**：处理成功
4. **FAILED**：处理失败
5. **CANCELED**：被取消（通常因为同项目来了新扫描，旧任务被取消）

你的任务从 PENDING → IN_PROGRESS 的时间取决于队列长度和 Worker 数量。"

**小白**："CE Worker 的数量是固定的吗？能不能动态扩容？"

**大师**："Worker 数量由 `sonar.ce.workerCount` 控制（默认 1）。增加 Worker 数可以并行处理更多任务，但每个 Worker 都在消耗数据库连接和 CPU。一般建议：

- 项目 < 50 个 → 1-2 个 Worker
- 项目 50-200 个 → 2-3 个 Worker
- 项目 > 200 个 → 3-4 个 Worker

但注意：Worker 增加不等于吞吐量线性增加——瓶颈可能转移到数据库连接池或 ES 索引写入速度。"

**小胖**："等一下，大师。每次扫描完，Scanner 日志里明明已经显示了 Bug 数量、覆盖率这些数据——这些不就是在 Scanner 端计算好的吗？那 CE 到底还要算什么？"

**大师**："好问题。Scanner 和 CE 的分工很明确：

- **Scanner 端计算**：原始度量数据——行数、圈复杂度、重复率、Bug 数量、漏洞数量。这些数据直接来自代码分析，Scanner 跑完就有结果。
- **CE 端计算**：派生指标——技术债务比率（sqale_index / 总开发成本）、New Code 上的指标、质量门禁状态。这些指标需要和数据库中的历史数据对比，所以必须由 CE 在处理任务时计算。

举个例子：Scanner 能告诉你'这个项目有 50 个 Bug'，但 CE 才能告诉你'相比上次分析，这 50 个 Bug 中有 12 个是新增的'——因为判断'新增'需要读数据库中的上次分析结果。"

**小白**："那 CE 是怎么跟踪 Issue 的历史变化的？比如修复了一个 Bug，CE 怎么知道它不是'新引入'的？"

**大师**："CE 内部有一个 Issue Tracker 机制，核心是一个指纹匹配算法：

1. **Issue 指纹**：每个 Issue 生成一个 Hash 指纹——基于规则 Key + 文件路径 + 行号的组合。同一个 Bug 在多次扫描中的 Hash 相同。
2. **状态追踪**：CE 将本次扫描的 Issue Hash 集合与上次扫描的集合做对比：
   - 新 Hash → 标记为 `OPENED`（新引入）
   - 消失的 Hash → 标记为 `CLOSED`（已修复）
   - 相同 Hash → 标记为 `CONFIRMED`（持续存在）
3. **跨文件追踪**：如果代码行号因新增代码发生了偏移，Issue Tracker 会用更复杂的算法（基于代码上下文而非行号）来匹配同一个 Issue。

这也是为什么你的 Web UI 里能看到'New Bugs: 5, Fixed Bugs: 3'——这些信息完全是由 CE 在入库时计算出来的。"

**小胖**："如果 CE 任务失败了，Scanner 返回 SUCCESS 有意义吗？会不会出现'扫描成功但质量门禁没算'的情况？"

**大师**："这正是最容易被忽略的坑。Scanner 和 CE 是两个独立的流程：

- **Scanner 返回 EXIT CODE 0** → 说明源码分析完成、报告上传成功
- **CE Task 状态** → 说明报告入库、指标计算、门禁评估完成

如果 Scanner 成功但 CE 失败，CI 中配置的 `waitForQualityGate` 步骤会超时并返回失败。但如果你在 CI 中只检查了 Scanner 的退出码，而没有使用 `waitForQualityGate`——你的 CI 会'绿'，但质量门禁其实根本没执行完。

这就是为什么 CI 集成中一定要配置 `waitForQualityGate` 步骤，而不是仅依赖 Scanner 的返回值。"

---

## 3. 项目实战

### 3.1 分步实现

**步骤 1：监控 CE 队列健康**

```bash
# CE 队列状态
echo "=== CE 队列状态 ==="
curl -s -u admin:Sonar@2024Admin \
  "http://localhost:9000/api/ce/activity?statuses=PENDING,IN_PROGRESS&ps=1" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f\"队列总数: {d['paging']['total']}\")"

# 按状态分布
for status in PENDING IN_PROGRESS SUCCESS FAILED CANCELED; do
  count=$(curl -s -u admin:Sonar@2024Admin \
    "http://localhost:9000/api/ce/activity?statuses=$status&ps=1" \
    | python3 -c "import sys,json;print(json.load(sys.stdin)['paging']['total'])")
  printf "  %-12s: %d\n" "$status" "$count"
done
```

**步骤 2：配置 CE Worker 和性能调优**

```yaml
# Docker Compose 环境变量
environment:
  SONAR_CE_JAVAOPTS: "-Xms512m -Xmx2g -XX:+UseG1GC"
  SONAR_CE_WORKER_COUNT: "3"  # 增加 Worker 数
  SONAR_JDBC_MAXACTIVE: "60"  # 增加数据库连接池
```

**步骤 3：重试失败的 CE 任务**

```bash
# 查看失败任务列表
curl -s -u admin:Sonar@2024Admin \
  "http://localhost:9000/api/ce/activity?statuses=FAILED&ps=10" \
  | python3 -c "
import sys, json
for t in json.load(sys.stdin)['tasks']:
    print(f\"ID: {t['id']}  Component: {t.get('componentKey','?')}  Error: {t.get('errorMessage','N/A')}\")"

# （如果 CE 任务失败是暂时性的，SonarQube 会自动重试——不需要手动操作）
```

**步骤 4：理解指标计算流程**

CE 处理一个任务时，按顺序执行：
1. 解析分析报告（提取原始 Issue 和度量数据）
2. 运行 MeasureComputer（计算派生指标，如 sqale_index 技术债务）
3. 运行 Issue Tracker（关联新旧 Issue，标记已修复/新引入）
4. 评估 Quality Gate（检查所有条件）
5. 写入 PostgreSQL + 索引到 ES

**步骤 5：CE 任务处理耗时分析**

```bash
#!/bin/bash
# ce-timing-analysis.sh - 分析最近 CE 任务各阶段耗时

curl -s -u admin:Sonar@2024Admin \
  "http://localhost:9000/api/ce/activity?ps=20&statuses=SUCCESS" \
  | python3 -c "
import sys, json
from datetime import datetime

tasks = json.load(sys.stdin)['tasks']
print(f'{\"Task ID\":<20} {\"项目\":<30} {\"耗时(s)\":>10} {\"状态\":<10}')
print('-' * 80)
for t in tasks:
    submitted = t.get('submittedAt', '')
    executed = t.get('executedAt', '')
    if submitted and executed:
        s = datetime.fromisoformat(submitted.replace('+0000',''))
        e = datetime.fromisoformat(executed.replace('+0000',''))
        duration = (e - s).total_seconds()
        component = t.get('componentName', t.get('componentKey', '?'))[:28]
        print(f\"{t['id']:<20} {component:<30} {duration:>10.1f} {t['status']:<10}\")
"

# 按项目分组统计平均 CE 处理耗时
echo ""
echo "=== 各项目平均 CE 处理耗时（最近20次） ==="
curl -s -u admin:Sonar@2024Admin \
  "http://localhost:9000/api/ce/activity?ps=1000&statuses=SUCCESS" \
  | python3 -c "
import sys, json
from datetime import datetime
from collections import defaultdict

tasks = json.load(sys.stdin)['tasks']
stats = defaultdict(list)
for t in tasks:
    if t.get('submittedAt') and t.get('executedAt'):
        s = datetime.fromisoformat(t['submittedAt'].replace('+0000',''))
        e = datetime.fromisoformat(t['executedAt'].replace('+0000',''))
        stats[t.get('componentKey','?')].append((e-s).total_seconds())

for key, durations in sorted(stats.items(), key=lambda x: sum(x[1])/len(x[1]), reverse=True):
    avg = sum(durations) / len(durations)
    print(f'  {key}: avg={avg:.1f}s, count={len(durations)}')
"
```

**步骤 6：构建 CE 队列监控脚本**

```bash
#!/bin/bash
# ce-queue-monitor.sh - CE 队列持续监控，超过阈值告警

THRESHOLD=50
CHECK_INTERVAL=60  # 每 60 秒检查一次
ALERT_COOLDOWN=300  # 告警冷却时间（秒）
LAST_ALERT=0

while true; do
    PENDING=$(curl -s -u admin:Sonar@2024Admin \
        "http://localhost:9000/api/ce/activity?statuses=PENDING&ps=1" \
        | python3 -c "import sys,json;print(json.load(sys.stdin)['paging']['total'])")

    IN_PROGRESS=$(curl -s -u admin:Sonar@2024Admin \
        "http://localhost:9000/api/ce/activity?statuses=IN_PROGRESS&ps=1" \
        | python3 -c "import sys,json;print(json.load(sys.stdin)['paging']['total'])")

    NOW=$(date +%s)
    echo "[$(date '+%H:%M:%S')] PENDING=$PENDING  IN_PROGRESS=$IN_PROGRESS"

    if [ "$PENDING" -gt "$THRESHOLD" ] && [ $((NOW - LAST_ALERT)) -gt $ALERT_COOLDOWN ]; then
        echo "🚨 ALERT: CE 队列积压 $PENDING 个任务（阈值: $THRESHOLD）"
        # 可选：发送钉钉/企业微信/邮件告警
        # curl -X POST "https://hooks.example.com/alert" -d "{\"msg\":\"CE队列积压: $PENDING\"}"
        LAST_ALERT=$NOW
    fi

    sleep $CHECK_INTERVAL
done
```

**步骤 7：理解派生指标（MeasureComputer）工作原理**

CE 中的 MeasureComputer 负责计算所有派生指标。以下是常见派生指标及其计算逻辑：

| 派生指标 | 计算公式 | 依赖的原始度量 |
|---------|---------|--------------|
| `sqale_index`（技术债务/分钟） | Σ(每条规则的技术债务修复时间) | 每条 Issue 的修复时间 |
| `sqale_debt_ratio`（技术债务比率） | sqale_index / (开发总时长) × 100% | sqale_index + 代码行数 |
| `new_technical_debt`（新增技术债务） | 新 Issue 的技术债务总和 | 新 Issue 列表 |
| `new_sqale_debt_ratio`（新增债务比） | new_sqale_index / new_dev_cost × 100% | 新增债务 + 新增代码量 |
| `coverage`（覆盖率） | (覆盖行数 / 总行数) × 100% | JaCoCo 导入数据 |
| `new_coverage`（新增代码覆盖率） | (新增覆盖行数 / 新增总行数) × 100% | 新增代码的范围 |

派生指标的计算依赖前序步骤的结果——如果 Issue 解析失败，所有依赖 Issue 的派生指标都会计算错误。这就是为什么 CE 失败时常常看到"指标全部为 0"或"指标值异常"。

### 3.3 验证

```bash
# CE 队列不积压
PENDING=$(curl -s -u admin:Sonar@2024Admin \
  "http://localhost:9000/api/ce/activity?statuses=PENDING&ps=1" \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['paging']['total'])")
if [ "$PENDING" -gt 50 ]; then
  echo "⚠️ CE 队列积压: $PENDING 个待处理任务"
else
  echo "✅ CE 队列正常: $PENDING 个待处理"
fi
```

---

## 4. 项目总结

### 4.1 CE 性能诊断路径

```
CE 队列积压 → 查看 CE Worker 是否都在忙
├── 是 → CPU 瓶颈 → 增加 Worker 数
├── 是 → IO 瓶颈（数据库慢查询）→ 优化数据库
├── 否 → CE 整体卡住 → 检查 CE 日志，是否有线程死锁或 OOM
└── 某个任务长时间 IN_PROGRESS → 单个项目过大 → 拆分项目或优化扫描
```

### 4.2 CE 任务状态流转图

```
Scanner 上传报告
       ↓
   [PENDING] ──→ Worker 取走任务
       ↓
 [IN_PROGRESS] ──→ 处理中...
       ↓
   ┌───┼───┐
   ↓   ↓   ↓
[SUCCESS] [FAILED] [CANCELED]
   ↓        ↓         ↓
 入库+索引  记录错误   丢弃
   ↓
Quality Gate
评估完成
```

### 4.3 CE 配置参数速查表

| 参数 | 默认值 | 建议值（中小规模） | 建议值（大规模） | 说明 |
|------|--------|-----------------|----------------|------|
| `sonar.ce.workerCount` | 1 | 2-3 | 3-4 | CE 并发 Worker 数 |
| `SONAR_CE_JAVAOPTS` | -Xms512m -Xmx512m | -Xms512m -Xmx2g | -Xms1g -Xmx4g | CE JVM 内存 |
| `sonar.jdbc.maxActive` | 60 | 60-100 | 100-200 | 数据库连接池大小 |
| `sonar.ce.queue.pollingDelay` | 2000ms | 1000ms | 500ms | 队列轮询间隔 |

### 4.4 常见故障排查表

| 故障现象 | 可能根因 | 排查命令 | 解决方案 |
|---------|---------|---------|---------|
| CE 队列持续积压 | Worker 数不足 | `api/ce/worker_count` | 增加 `sonar.ce.workerCount` |
| 单个任务处理超慢(>10min) | 项目规模过大 | `api/ce/task?id=xxx` 查看耗时 | 拆分项目或优化扫描范围 |
| CE 任务频繁 FAILED | 数据库连接不足/超时 | 查看 `ce.log` | 增加 `sonar.jdbc.maxActive` |
| CE 任务状态卡在 PENDING | Worker 线程死锁 | `jstack <ce-pid>` | 重启 SonarQube |
| 派生指标全为 0 | Scanner 未上传完整报告 | 检查 Scanner Debug 日志 | 修复 Scanner 配置 |
| ES 索引写入失败 | ES 磁盘满或只读 | `_cat/indices` 查看状态 | 清理 ES 数据或扩容 |

### 4.5 注意事项

1. **不要无限增加 Worker**：超过数据库连接池上限会让 Worker 在等待连接上浪费时间。
2. **CE 失败不会自动通知开发者**：CI 中的 Scanner 已返回成功时，CI 不会知道 CE 失败了。需要额外的监控和通知机制。
3. **取消的任务状态不可恢复**：如果因为新扫描取消了旧任务，旧任务的数据不会被写入数据库。

### 4.6 思考题

1. 如果 CE 的平均处理时间是 60 秒，同时有 3 个 Worker，每分钟提交 4 个任务——队列会怎样变化？多长时�后队列会溢出？
2. Quality Gate 评估是在 CE 处理流程的哪个步骤执行的？如果 Quality Gate 条件中同时包含 New Code 和 Overall Code 指标，会怎么计算？

---

> **推广计划提示**：CE 性能是 SonarQube 运维的核心。建议运维团队建立 "CE 队列深度" 的监控，当队列深度连续 10 分钟 > 50 时触发告警。
