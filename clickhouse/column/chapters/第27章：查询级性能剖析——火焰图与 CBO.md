# 第27章：查询级性能剖析——火焰图与 CBO

> **版本**：ClickHouse 24.x+ LTS
> **定位**：中级篇核心章节。从 query_log 诊断到 EXPLAIN PLAN 的 CBO 决策分析，再到 perf 火焰图的 CPU 热路径定位，构建一套系统化的查询性能剖析工具链。
> **前置阅读**：第15章（查询分析器与 query_log 诊断）、第26章（性能调优——从配置到内核）
> **预计阅读**：35 分钟 | **实战耗时**：60 分钟

---

## 1. 项目背景

某金融风控平台的ClickHouse集群接到运营团队投诉：一张名为`risk_events`的大宽表上，每日例行风控报表SQL从半个月前的800毫秒飙到了5秒以上。运营Dashboard每30秒刷新一次，5秒的查询延迟导致页面频繁出现"加载中"的旋转图标，客服电话已经被打爆了。

运维团队第一反应是加索引。他们在`created_at`、`event_type`、`user_id`三个字段上各建了一套跳数索引，结果查询延迟纹丝不动——还是5秒。接着把`max_threads`从16调到32，延迟反而涨到了5.8秒，上下文切换开销把新增的算力全吃掉了。又试着把`WHERE`条件里的`OR`改成`UNION ALL`，依然没用。

技术负责人在周会上拍桌子："咱们现在是瞎猫碰死耗子，改一个参数测一次，测了八次一次正向收益都没有。根本问题是我们不知道CPU时间到底消耗在哪——是MergeTree的数据扫描？是聚合计算？是排序？还是I/O等待？不知道瓶颈在哪，所有的优化都是抛硬币。"

这是典型的"知其慢而不知其所以慢"——`EXPLAIN`给出的执行计划看起来毫无问题，统计信息也正常，但查询就是慢。他们需要的不是更多的索引和更大的线程数，而是一套**查询级性能剖析工具链**：从宏观的query_log耗时概览，到中观的EXPLAIN PIPELINE并行度分析，再到微观的perf火焰图CPU热路径定位。

本章将以"定位风控报表慢查询根因"为主线，带你系统性地掌握这套工具链的每一个环节。

---

## 2. 项目设计：剧本式交锋对话

周一上午十点，大师被请到运维作战室。大屏上投着Grafana监控，`risk_events`表的那条查询P99延迟曲线在5秒的位置纹丝不动。

**小胖**瘫在椅子上刷手机："慢就加机器呗，看什么火焰图？我又不是C++程序员。ClickHouse服务端的代码我一窍不通，你给我看调用栈我也看不懂啊。加两台机器做分布式查询不香吗？"

**大师**敲了敲白板："胖儿，你要先搞清楚一个概念——你到底是CPU瓶颈还是I/O瓶颈。如果是CPU瓶颈，加机器能线性扩展；但如果是单线程串行瓶颈——比如排序阶段只有一个stream在跑——你把集群扩到100个节点，这5秒的排序延迟也一分都不会少。"

**小胖**放下手机："等一下，什么叫'只有一个stream在跑'？ClickHouse不是并行查询吗？"

**大师**："并行不等于所有阶段都并行。ClickHouse的查询执行模型叫Pipeline——一条SQL被拆成多个Stage，每个Stage内部可以有多个Stream并行执行。但如果某个Stage的数据必须汇聚后才能继续——比如全局排序、或者聚合的最终合并——这个Stage就只能有1个Stream。这就是阿姆达尔定律：你的总延迟≥所有串行阶段的延迟之和。要找到串行瓶颈，必须看到Pipeline的拓扑结构。"

**技术映射 #1**：ClickHouse Pipeline = 多个ProcessingStage组成的DAG，每个Stage内并行执行的单元叫Stream。`max_threads`只控制可并行Stage的Stream数量，无法影响串行Stage。EXPLAIN PIPELINE的输出就是这张DAG的文本表示。

---

**小白**推了推眼镜，把一份`EXPLAIN PLAN json=1`的输出投到屏幕上："大师，我用EXPLAIN PLAN看了CBO的执行计划，它选择的JOIN顺序和我想的不一样。我写的SQL是`FROM orders o JOIN users u ON o.user_id = u.user_id`，但CBO把users放到了左边，把orders放到了右边——它把大表当驱动表了？这不是反了吗？"

**大师**眼睛一亮："小白问到点子上了。CBO——Cost-Based Optimizer——不是按SQL文本的字面顺序来决定JOIN顺序的，它会估算每张表的行数和基数，选择代价最小的JOIN顺序。如果`users`表虽然小，但`user_id`上面的过滤条件筛掉了99%的数据，CBO可能会先扫描users拿到一个小集合，再去orders做IN或半连接。这比先全扫orders再Hash Join到users高效得多。"

"但这里有一个坑——CBO的代价估算是基于表统计信息的。如果统计信息过期了，CBO会做出愚蠢的决策。比如你一个月没跑`OPTIMIZE TABLE`，stats显示orders表只有100万行，实际已经3亿行了，CBO就可能选错JOIN顺序。"

**技术映射 #2**：CBO（Cost-Based Optimizer）是ClickHouse 23.x+为替代RBO（Rule-Based Optimizer）引入的新型优化器框架（通过`allow_experimental_analyzer=1`启用）。其核心思想是为每个候选执行计划估算CPU/IO/Memory成本，选择总代价最小的计划。代价估算依赖表级统计信息——行数、列基数、数据分布等，准确性直接决定CBO的决策质量。

---

**小胖**举手："那我要怎么知道CBO做的每个决策对不对？EXPLAIN PLAN只给了最终计划，没有中间的决策过程啊。"

**大师**："对，EXPLAIN PLAN的默认输出只告诉你'它决定了什么'，不告诉你'为什么这样决定'。要看到决策过程，有三个工具可以深挖：第一，`EXPLAIN PLAN json=1`看完整的分层计划树——包括每个子步骤的估算行数、内存和I/O。第二，打开`send_logs_level='trace'`，ClickHouse会打印每一步的实际执行耗时，跟估算值对比你就能知道CBO哪里估偏了。第三，`system.query_log`里的`ProfileEvents`字段记录了查询执行期间触发的所有运行时事件——包括选了多少part、读了几个mark、用了哪个索引、有没有触发OOM阈值——这相当于查询的'体检报告'。"

**小白**追问："send_logs_level=trace会输出多少日志？会不会把磁盘刷爆？"

**大师**严肃起来："问得好。trace级别日志是一个**短时诊断工具**，不能一直开着。一条5秒的查询在trace级别可能产生几万行日志——每一步的数据读取、聚合、排序都会打印。正确用法是：在诊断会话中`SET send_logs_level='trace'`，跑完目标SQL立刻`SET send_logs_level='warning'`切回来。千万不要在config.xml里全局设成trace——否则你的系统日志盘会在十分钟内爆满。"

---

**小胖**挠头："好吧，就算我看到了Pipeline，也看到了trace日志，如果瓶颈在ClickHouse的C++代码里面怎么办？比如MergeTree的某个底层读取函数特别慢，我们除了看调用栈还能干嘛？"

**大师**在白板上画了一个火苗形状："这时候就需要终极武器——**火焰图**。perf是Linux内核提供的性能采样工具，它每10毫秒中断一次CPU，记录当前正在执行的函数调用栈。跑30秒就能收集几千个样本，然后把这些样本按调用栈聚合、可视化，最终得到一张SVG图——宽度越宽的函数，CPU耗时占比越大；高度越高的调用链，调用深度越深。"

"火焰图不会告诉你'哪里写错了'，但它会对你吼——**CPU时间都花在这几个函数上了，你看着办！**比如你一眼就能看到`Aggregator::executeImpl`占了40%的宽度，说明聚合是瓶颈；`MergeTreeRangeReader::readRows`占了35%，说明数据扫描是瓶颈。有了这个方向，再决定是优化WHERE条件、加索引、还是改Schema，就是有据可依的决策了。"

**技术映射 #3**：火焰图的横轴按函数CPU占比排序（不是时间顺序），纵轴表示调用栈深度。颜色没有特殊含义，仅用于区分函数。阅读技巧——先看"平顶山"（宽而矮的函数），这些是CPU热路径；再看"烟囱"（窄而高的调用链），这些是深度递归或异常路径。

---

**小胖**终于来了兴趣："那如果我是Windows环境或者Docker环境，不方便跑perf怎么办？"

**大师**："ClickHouse内置了`system.stack_trace`表和query_log中的trace字段，执行`SELECT arrayJoin(trace) FROM system.stack_trace`或者从query_log的trace字段中提取，可以拿到查询执行期间的采样调用栈。精度不如perf（perf是硬件级采样，ClickHouse内部的采样是软件级的），但好处是零环境依赖，任何部署方式都能用。对于大多数慢查询诊断来说，内部trace已经足够定位问题了。"

"另一个轻量级替代——`allow_experimental_analyzer=1`启用新版分析器后，EXPLAIN PLAN的输出会包含更多决策信息，包括`header`（列级统计）、`actions`（表达式求值步骤）、`indexes`（索引使用情况）。新分析器的查询计划通常比老分析器更优——尤其是在子查询优化和CTE展开方面。目前(24.x版本)已默认开启。"

**小胖**合上手机："行，我懂了。诊断慢查询就是按照这个顺序来：query_log看宏观 → EXPLAIN PIPELINE看拓扑 → EXPLAIN PLAN看CBO决策 → trace看每步耗时 → 火焰图看CPU热点。就像看病一样——先问诊，再拍CT，最后动刀。"

**大师**笑着点头："就是这个思路。开工！"

---

## 3. 项目实战

### 环境准备

本章实战使用Docker部署单节点ClickHouse，内置perf命令：

```bash
# 拉取带debug符号的ClickHouse镜像（火焰图需要符号表）
docker run -d --name ch-profiling \
  --ulimit nofile=262144:262144 \
  --cap-add SYS_PTRACE \
  -p 8123:8123 -p 9000:9000 \
  clickhouse/clickhouse-server:24.x

# 进入容器
docker exec -it ch-profiling bash

# 确认perf可用
perf --version
```

> **注意**：`--cap-add SYS_PTRACE` 是perf正常运行的必要权限。如果没有这个参数，perf会报"Permission denied"。

初始化测试表和数据：

```sql
-- 创建大宽表 orders（模拟风控场景）
CREATE TABLE orders (
    order_id UInt64,
    user_id UInt64,
    product_id UInt64,
    status LowCardinality(String),
    amount Decimal(12,2),
    channel LowCardinality(String),
    risk_level LowCardinality(String),
    created_at DateTime,
    updated_at DateTime,
    -- 宽表冗余字段
    province LowCardinality(String),
    city LowCardinality(String),
    device_type LowCardinality(String),
    is_fraud UInt8,             -- 0=正常, 1=欺诈
    rule_id UInt32,
    score Float32
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(created_at)
ORDER BY (created_at, city, status)
SETTINGS index_granularity = 8192;

-- 插入1亿行数据（模拟半年数据）
INSERT INTO orders
SELECT
    rand64() % 100000000,
    rand64() % 5000000,
    rand64() % 50000,
    ['pending','processing','completed','cancelled','refunded'][rand() % 5 + 1],
    rand() % 99999 / 100.0,
    ['app','web','miniprogram','h5','api'][rand() % 5 + 1],
    ['low','medium','high','critical'][rand() % 4 + 1],
    toDateTime('2025-01-01') + rand() % (86400 * 180),
    toDateTime('2025-01-01') + rand() % (86400 * 200),
    ['广东','浙江','江苏','北京','上海'][rand() % 5 + 1],
    ['广州','杭州','南京','北京','上海'][rand() % 5 + 1],
    ['iOS','Android','Web','HarmonyOS'][rand() % 4 + 1],
    rand() % 2,
    rand() % 100,
    rand() / 1000000.0
FROM numbers(100000000);
```

---

### Step 1: query_log 深度分析

先跑一条模拟风控报表的慢查询，让它进入query_log：

```sql
-- 模拟风控报表SQL（故意写一个会产生大量扫描的查询）
SELECT
    toStartOfHour(created_at) AS hour,
    city,
    risk_level,
    count() AS total_orders,
    countIf(is_fraud = 1) AS fraud_orders,
    round(fraud_orders / total_orders, 4) AS fraud_rate,
    sum(amount) AS total_amount,
    avg(score) AS avg_risk_score,
    uniq(user_id) AS unique_users,
    uniqExact(rule_id) AS triggered_rules
FROM orders
WHERE created_at >= '2025-06-01' AND created_at < '2025-07-01'
GROUP BY hour, city, risk_level
HAVING total_orders > 100
ORDER BY hour, city, risk_level;
```

查询ID会被打印在ClickHouse日志中，记下它。然后从query_log中提取详细画像：

```sql
-- 获取最近5条最慢的查询
SELECT
    query_start_time,
    query_duration_ms,
    formatReadableSize(memory_usage) AS mem,
    read_rows,
    read_bytes,
    result_rows,
    result_bytes,
    substring(query, 1, 120) AS query_preview
FROM system.query_log
WHERE type = 'QueryFinish'
  AND query_duration_ms > 1000
ORDER BY query_duration_ms DESC
LIMIT 5;
```

输出示例：

```
query_start_time       |query_duration_ms|mem      |read_rows  |query_preview
2026-04-30 10:15:00.123|4832             |2.34 GiB |52000000   |SELECT toStartOfHour(...
2026-04-30 10:10:00.456|3210             |1.89 GiB |38000000   |SELECT city, count()...
```

接着深挖该查询的ProfileEvents——这是比裸耗时更有价值的信息：

```sql
-- 查看某个特定查询的ProfileEvents（用实际query_id替换）
SELECT
    arrayJoin(mapKeys(ProfileEvents)) AS event,
    arrayJoin(mapValues(ProfileEvents)) AS value
FROM system.query_log
WHERE type = 'QueryFinish'
  AND query_id = 'your-query-id-here'
ORDER BY value DESC
LIMIT 15;
```

关键指标解读：
- **SelectedParts / SelectedRanges / SelectedMarks**：实际选了多少part/范围/mark——如果SelectedMarks远大于预期，说明索引过滤效果差，全表扫描占比高
- **ContextLock**：上下文锁等待——数值高说明并发竞争严重
- **AggregationHashTablesStatistics**：聚合哈希表的统计——可以判断分组键的基数是否合理
- **RealTimeMicroseconds / UserTimeMicroseconds**：实际耗时 vs CPU时间——比值大于2说明大量时间在等I/O

从ProfileEvents中可以获得一个关键洞察：如果`SelectedParts`很大（比如上千），但每个part都很小——说明表的分区粒度过细，合并没跟上，小文件过多导致open/read开销巨大。这是`OPTIMIZE TABLE FINAL`或调整分区键的信号。

---

### Step 2: EXPLAIN PIPELINE 找瓶颈

Pipeline是ClickHouse查询执行模型的骨架。每个Stage要么并行要么串行，理解它的拓扑结构就能预判瓶颈：

```sql
-- 查看Pipeline的文本表示
EXPLAIN PIPELINE
SELECT
    city, risk_level,
    count() AS cnt,
    sum(amount) AS total,
    uniq(user_id) AS users
FROM orders
WHERE created_at >= '2025-06-01' AND created_at < '2025-06-15'
GROUP BY city, risk_level
ORDER BY city, risk_level;
```

典型输出：

```
(Expression)
ExpressionTransform
  (Aggregating)
  Resize 32 → 32   (说明：32个并行stream)
    AggregatingTransform × 32
      (Expression)
      ExpressionTransform × 32
        (ReadFromMergeTree)
        MergeTreeThread × 32  ← 读取阶段，32路并行读取不同的part/granule
          (Sorting)
          SortingTransform
            MergingSortedTransform 32 → 1  ← 注意！从32个stream合并到1个，这里可能是瓶颈
```

关键观察点：
1. **`× N`** 表示该Stage的并行度——N越大越并行，1就是单线程
2. **`M → N`** 在Resize/Merging阶段表示stream数量变化——`32→1`是典型的汇聚串行点
3. **SortingStage** 几乎总是串行——排序的本质就是要把所有数据汇聚后比较，这是无法避免的

可以用`graph=1`输出DOT格式，用于可视化：

```sql
EXPLAIN PIPELINE graph=1
SELECT city, risk_level, count(), sum(amount)
FROM orders
WHERE created_at >= '2025-06-01' AND created_at < '2025-06-15'
GROUP BY city, risk_level;
```

将输出粘贴到Graphviz在线工具中，可以直观地看到Pipeline的DAG拓扑图——哪个Stage宽（高并行），哪个Stage窄（串行），一目了然。

---

### Step 3: CBO 决策分析

启用新版分析器，看CBO如何选择JOIN顺序和过滤下推：

```sql
-- 确保新版分析器已启用（24.x默认开启）
SET allow_experimental_analyzer = 1;

EXPLAIN PLAN json=1
SELECT
    o.city, o.risk_level, u.level,
    count() AS cnt,
    sum(o.amount) AS total_amount
FROM orders o
INNER JOIN users u ON o.user_id = u.user_id
WHERE o.created_at >= '2025-06-01'
  AND o.created_at < '2025-06-15'
  AND u.level IN ('vip', 'svip')
GROUP BY o.city, o.risk_level, u.level;
```

JSON输出中的核心字段：

```json
{
  "Plan": {
    "Node Type": "Aggregating",
    "Plans": [
      {
        "Node Type": "Join",
        "Table": "right",
        "Plans": [
          {
            "Node Type": "ReadFromMergeTree",
            "Description": "orders",
            "Indexes": ["MinMax", "Partition"],     // ← 实际使用了哪些索引
            "Header": [
              {"Name": "city", "Type": "LowCardinality(String)"},
              {"Name": "amount", "Type": "Decimal(12,2)"}
            ]
          },
          {
            "Node Type": "ReadFromMergeTree",
            "Description": "users",
            "Filter": "level IN ('vip', 'svip')"    // ← 过滤条件已下推到存储层
          }
        ]
      }
    ]
  }
}
```

重点关注：
- **`Plans`的嵌套顺序**：反映了CBO决定的执行顺序——子查询先执行，外层后执行
- **`Filter`的位置**：条件是否下沉到`ReadFromMergeTree`节点——下沉意味着利用主键索引过滤，没有下沉则说明ClickHouse认为在内存中过滤更高效
- **`Indexes`数组**：列出了查询实际命中的索引类型——如果为空，说明全表扫描是唯一的道路

对比老分析器和新分析器的差异：

```sql
-- 老分析器
SET allow_experimental_analyzer = 0;
EXPLAIN PLAN json=1
SELECT ...;  -- 同一个查询

-- 新分析器
SET allow_experimental_analyzer = 1;
EXPLAIN PLAN json=1
SELECT ...;  -- 同一个查询

-- 主要区别：
-- 1. JOIN顺序可能不同（新分析器更激进地重排）
-- 2. Filter下推策略更优（子查询中的WHERE条件可能被提升到外层）
-- 3. 聚合优化——新分析器支持GROUP BY键自动重排和预聚合
```

---

### Step 4: send_logs_level='trace' 追踪每步执行

这是定位"到底哪一步最慢"的直接手段：

```sql
-- 开启trace级别日志
SET send_logs_level = 'trace';

-- 执行目标查询
SELECT status, count(), uniq(user_id)
FROM orders
WHERE created_at >= '2025-06-01'
  AND created_at < '2025-06-02'
GROUP BY status
ORDER BY status;

-- ⚠️ 执行完后立刻关掉！
SET send_logs_level = 'warning';
```

ClickHouse客户端会输出类似以下trace日志（简化版）：

```
[tcp] [db:default] (query_id): Executing query.
[tcp] [db:default] (query_id): Reading approx. 52000000 rows, 6.23 GiB
[tcp] [db:default] (query_id): Selected 94 parts by date, 94 parts by key, 12046 marks by primary key
[tcp] [db:default] (query_id): Reading from marks: first=0, last=12046
[tcp] [db:default] (query_id): MergeTreeRangeReader: reading ranges in 94 parts, ≈12046 marks
[tcp] [db:default] (query_id): MergeTreeRangeReader: read 12046 marks in 1.82 sec.
[tcp] [db:default] (query_id): MergeTreeRangeReader: read 52013428 rows in 2.31 sec.
[tcp] [db:default] (query_id): Aggregating.
[tcp] [db:default] (query_id): Aggregated. 5 rows in 0.04 sec.
[tcp] [db:default] (query_id): Sorted. 5 rows in 0.001 sec.
```

从trace中可以准确计算每一步的耗时占比：
- **数据读取**：2.31秒（占总耗时约95%）——瓶颈确认！
- **聚合**：0.04秒（几乎可以忽略）
- **排序**：0.001秒（结果集只有5行，无开销）

结论清晰：这条查询的问题不在聚合和排序，而在数据扫描量过大——5200万行。优化方向应该是缩小WHERE条件中的时间窗口，或者使用物化视图预聚合。

---

### Step 5: 构建慢查询诊断脚本

将前面的查询整合成一个一键诊断脚本：

```sql
-- 一站式慢查询诊断
WITH
    slow_query AS (
        SELECT query_id, query, query_duration_ms,
               memory_usage, read_rows, read_bytes,
               result_rows, result_bytes,
               ProfileEvents
        FROM system.query_log
        WHERE type = 'QueryFinish'
          AND query NOT LIKE '%system.query_log%'
        ORDER BY query_duration_ms DESC LIMIT 1
    )
SELECT '=== SLOW QUERY PROFILE ===' AS section
UNION ALL
SELECT format('Duration: {}ms | Memory: {} | Rows read: {} | Result rows: {}',
    toString(query_duration_ms),
    formatReadableSize(memory_usage),
    toString(read_rows),
    toString(result_rows))
FROM slow_query
UNION ALL
SELECT '=== FULL QUERY TEXT ==='
UNION ALL
SELECT query FROM slow_query
UNION ALL
SELECT '=== PROFILE EVENTS (TOP 20) ==='
UNION ALL
SELECT format('{}: {}', event, toString(value))
FROM (
    SELECT arrayJoin(mapKeys(ProfileEvents)) AS event,
           arrayJoin(mapValues(ProfileEvents)) AS value
    FROM slow_query
)
ORDER BY value DESC
LIMIT 20
UNION ALL
SELECT '=== SCAN EFFICIENCY ==='
UNION ALL
SELECT format('Rows scanned per result row: {}',
    toString(toInt64(read_rows / greatest(result_rows, 1))))
FROM slow_query
FORMAT PrettyCompactMonoBlock;
```

输出示例：

```
=== SLOW QUERY PROFILE ===
Duration: 5234ms | Memory: 2.71 GiB | Rows read: 52013428 | Result rows: 120
=== FULL QUERY TEXT ===
SELECT city, risk_level, count() ... FROM orders WHERE ...
=== PROFILE EVENTS (TOP 20) ===
SelectedMarks: 12046
SelectedParts: 94
SelectedRanges: 188
RealTimeMicroseconds: 5234000
UserTimeMicroseconds: 3800000
ContextLock: 42
...
=== SCAN EFFICIENCY ===
Rows scanned per result row: 433445
```

**扫描效率**`Rows scanned per result row = 433445`——每产出一行结果需要扫描43万行，效率极低。这明确指向了问题：数据没有预聚合，每次查询都在对原始明细做全量扫描。

---

### Step 6: perf + 火焰图（Linux环境）

当Pipeline和trace都指向数据扫描，但你想确认扫描内部具体是哪个函数在消耗CPU时，火焰图登场：

```bash
# 1. 找到ClickHouse进程PID
clickhouse_pid=$(pgrep -f clickhouse-server | head -1)
echo "ClickHouse PID: $clickhouse_pid"

# 2. 在ClickHouse客户端中启动慢查询（用另一个终端）
#    同时在宿主机上运行perf采样30秒
sudo perf record -F 99 -p $clickhouse_pid -g -- sleep 30
# -F 99: 每秒采样99次（99Hz，不会与定时器共振）
# -p: 指定采样进程
# -g: 记录调用栈（call graph）
# sleep 30: 采样30秒

# 3. 将采样数据导出为文本
sudo perf script > /tmp/out.perf

# 4. 使用 Brendan Gregg 的 FlameGraph 工具生成SVG
git clone https://github.com/brendangregg/FlameGraph.git /tmp/FlameGraph
/tmp/FlameGraph/stackcollapse-perf.pl /tmp/out.perf > /tmp/out.folded
/tmp/FlameGraph/flamegraph.pl /tmp/out.folded > /tmp/clickhouse_flame.svg

# 5. 将SVG文件下载到本地，用浏览器打开即可交互查看
```

阅读火焰图的关键技巧：
- **水平方向找宽函数**：`MergeTreeRangeReader::readRows`、`Aggregator::executeImpl`、`SortingStep::transform`这些函数如果在图上占了很大的宽度，就是热路径
- **垂直方向看调用深度**：如果一个"塔"很高——函数调了函数、函数又调了函数、叠了十几层——说明调用层次深，可能是递归或过度抽象，但不是最大的问题
- **忽略内核态函数**：`[kernel]`前缀的函数是系统调用开销，比如`__do_page_fault`、`sys_read`——这说明在page fault或磁盘I/O上消耗了大量时间，瓶颈可能在内存或磁盘而非CPU
- **`??`的含义**：如果火焰图上大量出现`??`，说明ClickHouse二进制缺少debug符号——需要安装`clickhouse-server-dbg`包或使用带符号的镜像

ClickHouse内置的轻量级替代方案（无需perf）：

```sql
-- 从query_log中提取调用栈
SELECT
    query_start_time,
    query_duration_ms,
    arrayJoin(trace) AS stack_frame
FROM system.query_log
WHERE type = 'QueryFinish'
  AND query_duration_ms > 5000
LIMIT 30;
```

内部trace的精度不如perf（样本量少、采的是QueryProfiler软件计数器而非硬件PMU），但在Docker、K8s、Windows等不方便跑perf的环境下足够实用。

---

### 测试验证

**验证1：诊断脚本输出是否合理**

跑完Step 5的诊断脚本后，确认：
- `Duration` 准确反映慢查询耗时
- `ProfileEvents` 中出现大量`SelectedMarks`和`SelectedParts`——确认扫描量是瓶颈
- `Rows scanned per result row` 远超100——数据未预聚合

**验证2：EXPLAIN PIPELINE 的串行点定位**

在Pipeline输出中找到`32→1`的汇聚点，验证该Stage是否确实是排序——如果是，则提前用物化视图做预排序可以消除此瓶颈。

**验证3：发送一个简单查询看trace精度**

```sql
SET send_logs_level = 'trace';
SELECT count() FROM system.numbers LIMIT 1000;
SET send_logs_level = 'warning';
```

确认客户端输出包含每步耗时信息——如果看不到trace日志，检查`send_logs_level`是否正确设置（该参数是session级的，断开重连需重新设置）。

---

## 4. 项目总结

### 性能剖析工具对比

| 工具 | 显示内容 | 使用时机 | 技能要求 |
|------|---------|---------|---------|
| `system.query_log` | 查询耗时、内存、行数、ProfileEvents | 所有慢查询诊断的**第一步** | 入门 |
| `EXPLAIN PIPELINE` | 执行Stage并行度、汇聚点 | 理解查询拓扑、定位串行瓶颈 | 中级 |
| `EXPLAIN PLAN json=1` | CBO选择的JOIN顺序、过滤下推、索引使用 | 优化JOIN查询、验证CBO决策 | 中级 |
| `send_logs_level='trace'` | 每步精确耗时 | 精确定位最慢的执行阶段 | 中级 |
| `perf` + 火焰图 | CPU采样的函数级热路径 | 深度C++代码级瓶颈分析 | 高级 |
| `system.stack_trace` | 轻量级调用栈采样 | 不方便跑perf时的替代方案 | 中级 |

> 按推荐顺序使用：query_log（1分钟） → EXPLAIN PIPELINE（5分钟） → EXPLAIN PLAN（5分钟） → trace（1分钟+跑查询的时间） → 火焰图（30分钟）。大多数慢查询在前三步就能定位，**90%的场景不需要动火焰图**。

### 适用场景

- **慢查询根因分析**：不再盲目加索引、改WHERE条件，而是用数据驱动的方式定位瓶颈
- **优化前后效果验证**：跑一次trace保存基线，优化后重新跑对比——是哪个阶段变快了，一目了然
- **容量规划参考**：ProfileEvents中的内存和行数数据，可以帮助预测查询在扩缩容后的资源消耗
- **代码级性能调优**：当ClickHouse本身的配置调优空间耗尽时，火焰图告诉你"就是这段代码慢"，可以带着火焰图上GitHub提issue，效率远高于"我的查询慢怎么办"

### 注意事项

- **`send_logs_level='trace'` 是诊断工具，不是监控工具**：用完立刻关掉，否则日志量会快速填满磁盘。一个5秒的查询在trace级别可能产生上百MB的日志
- **EXPLAIN返回的rows是估算值**：CBO基于minmax索引和采样数据估算行数，实际差异可能高达10倍——所以trace中的实际耗时才是你该相信的
- **火焰图需要debug符号**：如果ClickHouse是用`-DCMAKE_BUILD_TYPE=Release`编译的，perf生成的火焰图上会大量出现`??`。务必使用带符号的编译版本或安装`clickhouse-server-dbg`包
- **perf需要`SYS_PTRACE`权限**：Docker容器必须加`--cap-add SYS_PTRACE`参数，Kubernetes Pod需要设置`securityContext.privileged: true`或在节点上运行

### 常见踩坑经验

1. **trace日志刷爆磁盘**：在诊断会话中设了`send_logs_level='trace'`，执行完忘记关就断开了连接——下一条查询会把前一条也带上trace级别输出。**解法**：加一条`SET send_logs_level='warning'`到你的诊断脚本末尾
2. **EXPLAIN PIPELINE看不懂**：输出一大堆，不知道哪个Stage慢。**解法**：trace + pipeline组合拳——先跑trace找到具体是哪个阶段耗时最大，再回到pipeline图看该阶段的并行度和上下游关系
3. **火焰图中`??`太多**：Release二进制的函数符号被strip掉了。**解法**：拉取带符号的官方镜像 `clickhouse/clickhouse-server:24.x`（不带`-alpine`后缀的通常保留了符号表），或自行编译RelWithDebInfo版本
4. **ProfileEvents全是0或NULL**：`system.query_log`中ProfileEvents字段在某些老版本ClickHouse中可能为NULL——升级到23.x+即可解决

### 思考题

1. **如果EXPLAIN PIPELINE显示某阶段只有1个stream（单线程），可能的原因是什么？如何优化？**
   
   > 原因：①排序/聚合的最终合并阶段天然单线程（需要全局有序/全局去重）；②使用了LIMIT BY、DISTINCT等需要全局视野的操作；③分布式查询的协调节点上的merge阶段。优化方案：①在写入端用物化视图做预聚合，把汇聚操作提前到写入时；②调整ORDER BY键让数据天然有序，减少排序开销；③对于非精确场景，用`approx`系列的近似函数替代精确聚合（如`uniq`→`uniqCombined`可以多线程并行）

2. **`send_logs_level='trace'`和`system.query_log`中的`ProfileEvents`有什么区别？各自适用什么场景？**
   
   > `send_logs_level='trace'`是日志级别的实时输出——记录每一步的执行耗时和中间数据量，适合在测试环境逐条诊断。`system.query_log`中的`ProfileEvents`是查询结束后的汇总计数器——记录整条查询期间各类事件的发生次数，适合做历史趋势分析和批量慢查询筛选。前者告诉你"哪一步慢"，后者告诉你"慢的原因是什么类型的问题"。两者配合使用：先用query_log筛选出慢查询样本，再用trace对最慢的那个进行精准解剖。

---

> **下一章预告**：第28章《分布式查询优化——从单点到集群的思维跃迁》。当单节点优化到极限后，我们将面对分布式环境下的新挑战——数据倾斜、网络开销、查询计划的全局 vs 本地——如何将单节点调优经验迁移到分布式场景中。
