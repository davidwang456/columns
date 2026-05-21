# 第14章：SQL 优化入门——从执行计划到索引命中

> **版本**：ClickHouse 25.x LTS
> **定位**：基础篇核心章节。掌握 EXPLAIN 工具链，理解分区裁剪、稀疏索引命中机制，学会识别并修复常见 SQL 反模式。
> **前置阅读**：第4章（MergeTree 家族）、第5章（分区与排序键）、第7章（主键与稀疏索引）
> **预计阅读**：40 分钟 | **实战耗时**：50 分钟

---

## 1. 项目背景

周一早九点，数据分析师阿琳在 BI 看板上发现一个诡异的现象：同一张折线图，九点零三分刷新等了 8 秒，九点零五分重刷变成 45 秒。她翻出底层 SQL，看起来平平无奇：

```sql
SELECT count(DISTINCT user_id)
FROM events
WHERE toDate(timestamp) = '2025-01-15'
```

这条 SQL 在 MySQL 上跑了两年都没出过事——查某一天的活跃用户数，不就是按天过滤再 count distinct 嘛。可换到 ClickHouse 上，`events` 表积累了 **10 亿行** 事件数据，这条查询直接把集群的一个节点 CPU 打满，跑了整整 45 秒才出结果。更要命的是，看板上挂了 12 张类似的卡片，每张都查同一个 `events` 表的不同日期窗口，用户一刷新就是 12 条 SQL 同时砸过来，节点直接进入雪崩状态。

DBA 老张扫了一眼 SQL，在命令行敲了另一条：

```sql
SELECT uniq(user_id)
FROM events
WHERE timestamp >= '2025-01-15' AND timestamp < '2025-01-16'
```

回车。0.3 秒出结果。

阿琳愣住了："这不就是换了个写法吗？逻辑完全一样啊，为啥一个 45 秒一个 0.3 秒？"

老张指着屏幕说："不是换了个写法——是换了个命。第一条 SQL，分区裁剪失效，10 亿行全扫；第二条，分区裁剪生效，只扫了那一天对应的 300 万行。差距 150 倍。"

这不是孤例。从 MySQL 迁移到 ClickHouse 的团队，几乎都会踩到同一个坑：**把 MySQL 的 SQL 习惯原封不动搬过来**。在 MySQL 里，`WHERE toDate(timestamp) = '2025-01-15'` 和 `WHERE timestamp >= '2025-01-15' AND timestamp < '2025-01-16'` 走同一个索引，性能差不多。但在 ClickHouse 里，前者等于在分区键上包了一层函数，优化器直接放弃分区裁剪，后果就是全表扫描。更糟糕的是，这类 SQL 在数据量小的时候表现正常——100 万行可能只慢 0.3 秒，没人会注意。等数据涨到 10 亿行，炸弹才爆炸。

真正的问题不是 SQL 写不对，而是 **不会看执行计划**。写对了自己不知道对在哪，写错了也看不出来错在哪。MySQL 的 `EXPLAIN` 人人都用，但 ClickHouse 的 `EXPLAIN` 远比 MySQL 的丰富——它不仅能告诉你走不走索引，还能让你看到分区裁剪的表达式、Pipeline 的执行拓扑、甚至每一步的耗时。然而大多数开发者的 ClickHouse SQL 优化还停留在"感觉慢了就改改"的阶段，从未用过 `EXPLAIN indexes=1` 或 `send_logs_level='trace'`。

本章的目标就是用一次真实的"急诊救援"，带你从零掌握 ClickHouse 的 SQL 优化工具链。

---

## 2. 项目设计：剧本式交锋对话

**Scene**：数据团队工区，阿琳的屏幕上挂着一条红彤彤的超时告警。

**小胖**（啃着肉松饼凑过来）："琳姐，你这 SQL 我瞅着挺正常的啊——MySQL 上跑了两年都没事，原封不动搬到 ClickHouse 怎么就这么慢？这列存引擎吹得天花乱坠，一问速度怎么还不如 MySQL？"

**大师**（放下咖啡杯）："小胖，你这问题本身就有毛病。不是 ClickHouse 慢，是你的 SQL 写法让 ClickHouse 有劲使不出来。来，先把这条 SQL 的执行计划跑出来看看。"

大师在阿琳的终端里敲下：

```sql
EXPLAIN indexes=1
SELECT count(DISTINCT user_id)
FROM events
WHERE toDate(timestamp) = '2025-01-15';
```

屏幕刷出一大段输出。大师指着 `KeyCondition` 那一行："看到了吗？这里的表达式是 `(toDate(timestamp) = '2025-01-15')`——优化器拿到的不是原生的 `timestamp` 列，而是一个被 `toDate()` 函数包裹过的表达式。分区键是 `toYYYYMM(event_date)` 或者基于 `timestamp` 的分区表达式，但你现在给它的是 `toDate(timestamp)`，它根本没法和分区的元数据匹配上，只能放弃分区裁剪。后果就是——"

**小胖**："全表扫描！10 亿行挨个读一遍，那不慢才怪。食堂打饭，明明有你爱吃的窗口，你偏要每个窗口都排队看一眼，最后只端走一勺红烧肉。"

**大师**："比喻不错，但不完全对。食堂每个窗口排队你还能并行，ClickHouse 全表扫描是把 10 亿行的 `timestamp` 列从头读到尾，每一行都要执行 `toDate()` 函数，函数开销虽然小，但乘上 10 亿就是天文数字。再看正确写法——"

```sql
EXPLAIN indexes=1
SELECT uniq(user_id)
FROM events
WHERE timestamp >= '2025-01-15' AND timestamp < '2025-01-16';
```

"`KeyCondition` 变成了 `(timestamp in ['2025-01-15', '2025-01-16')`——优化器一看，哟，这是个范围查询，而且这个范围刚好完整落在某个分区里，直接定位到这个分区的目录，只读那几个数据文件。10 亿行变 300 万行，这就是分区裁剪的威力。"

**技术映射**：函数包裹分区键列 = 破坏分区裁剪。ClickHouse 的分区裁剪是**表达式匹配**而非**值匹配**。优化器需要看到原始分区列上的直接比较才能裁剪分区，任何包装函数都会让它放弃优化。

---

**小白**（推了推眼镜）："大师，EXPLAIN 的输出里还有 `Expression`、`Filter`、`ReadFromMergeTree` 这些字样，都代表什么意思？MySQL 的 EXPLAIN 只有一行 type 和 rows，ClickHouse 这一大坨怎么读？"

**大师**："好问题。ClickHouse 提供了多种 EXPLAIN 类型，针对不同的诊断目的。最常用的五种——听好，是五种，不是一种。"

大师打开笔记本上的笔记：

"**第一种：`EXPLAIN PLAN`**——默认行为。展示查询的宏观执行计划，从 `ReadFromMergeTree`（读数据）开始，逐层经过 `Filter`（过滤）、`Expression`（表达式计算）、`Aggregating`（聚合）、`Sorting`（排序），直到最后输出。它回答的问题是：**这条 SQL 要经过哪些算子？执行拓扑长什么样？**

**第二种：`EXPLAIN PIPELINE`**——展示物理执行管道。PLAN 是逻辑计划，PIPELINE 才是真正分配线程的东西。你能看到哪些步骤是并行执行的（多个 stream）、哪些步骤是串行瓶颈。它回答的问题是：**这条 SQL 用了多少个线程？哪里可能成为瓶颈？**

**第三种：`EXPLAIN indexes=1`**——这是你今天必须记住的。它会额外展示 MergeTree 表扫描的索引使用情况——哪些 Granule 被标记文件筛选掉了，哪些被读取了。它回答的问题是：**我的主键索引到底命中了没有？扫描了多少个 Granule？**

**第四种：`EXPLAIN AST`**——打印 SQL 的抽象语法树。这主要是开发调试用的，你可以看到 ClickHouse 解析完你的 SQL 后的内部表示。如果你怀疑 ClickHouse 理解错了你的 SQL，用它来验证。

**第五种：`EXPLAIN SYNTAX`**——这个很有意思。它会展示 ClickHouse 对你的 SQL 做了哪些隐式优化和重写。比如你写了 `WHERE a = 1 OR a = 2`，它可能会重写成 `WHERE a IN (1, 2)`。你写了 `count(DISTINCT x)`，它可能会自动优化成 `uniqExact(x)`。"

**小白**："也就是说，排查慢查询的 SOP 是——先用 `EXPLAIN PLAN` 看整体架构，再用 `EXPLAIN indexes=1` 看索引命中，最后用 `EXPLAIN PIPELINE` 看并行度？"

**大师**："差不多。如果还不够，再加一个 `send_logs_level='trace'`——这个设置能让 ClickHouse 在查询执行过程中实时打印每一步的耗时和中间结果量。你只要在会话里 SET 一下，再跑 SQL，就能看到类似 `Read 8192 rows in 3ms`、`Aggregated 8192 rows in 12ms` 这样的逐级日志。不过注意——trace 级别的日志量非常大，**只在单条排查时开启，用完立刻关掉**，否则日志文件能把你磁盘撑爆。"

---

**小胖**："索引命中我懂了。但大师，你刚提到的 `PREWHERE` 是什么？我见过有些 SQL 里写了 `PREWHERE`，跟 `WHERE` 有什么区别？是不是所有查询都该改成 PREWHERE？"

**大师**："PREWHERE 是 ClickHouse 独有的优化机制。普通的 WHERE 过滤是这样工作的：先根据主键索引把候选 Granule 找出来 → 然后把 Granule 内所有行的所有列都读进内存 → 再在内存里做 WHERE 过滤。如果你只用到其中几列，那剩余列的读取就全浪费了。"

**小胖**："这不又是食堂打饭的问题吗——明明只吃红烧肉和青菜，师傅非要把一整个餐盘所有格子都打满菜，我再把不吃的倒掉。"

**大师**："精准！PREWHERE 解决的就是这个问题。它让你指定'先用这几列做一次轻量级过滤，只对通过过滤的行才读取剩余的列'。比如："

```sql
SELECT user_id, page_url
FROM events
PREWHERE duration > 200;
```

"ClickHouse 会先只读 `duration` 列，过滤出 `duration > 200` 的行号，然后只对这些行号读取 `user_id` 和 `page_url` 列。如果 80% 的行都被 `duration > 200` 过滤掉了，那你就省掉了 80% 的列读取 IO。"

**小白**："那 ClickHouse 能不能自动把 WHERE 优化成 PREWHERE？难道每次都要我自己判断该不该加？"

**大师**："能，而且默认就是开的。`optimize_move_to_prewhere = 1` 这个参数会让优化器自动判断一个 WHERE 条件是否适合升级为 PREWHERE。它会在条件列的过滤率足够高、列宽度足够大的时候自动移动。但自动判断不是万能的——有时候优化器选择了错误的列做 PREWHERE，导致过滤率很低，反而多读了一列。这时候你就需要手动指定 PREWHERE，并观察 EXPLAIN 输出来验证效果。"

**技术映射**：PREWHERE = 先读过滤列，筛出需要真正读取的行，再读其他列的延迟读取优化。WHERE = 把所有列读出来再过滤。自动 PREWHERE 不总是对的——用 `EXPLAIN` 验证。

---

**小胖**："那大师，你再帮我看看——我这条 SQL 明明主键是 `(event_date, event_type, user_id)`，查的也是 `event_type = 'purchase'`，按理说应该走索引吧？为什么右边这条跑了 8 秒？"

```sql
-- 慢的
SELECT * FROM events WHERE toLower(event_type) = 'purchase';

-- 快的
SELECT * FROM events WHERE event_type = 'purchase';
```

**大师**："同一个坑，不同的函数。`toLower()` 包裹了排序键列，主键索引失效率 100%。同样的问题还有 `toDate()`、`toYear()`、`toString()`、`substr()`、算数表达式包裹。核心原则只有一条——**放在 WHERE 里的列，别对它做任何函数运算。让比较运算符直接作用在原始列上。**

**小白**："那如果我的数据里 `event_type` 既有 `'purchase'` 又有 `'Purchase'` 甚至 `'PURCHASE'`，我想做大小写不敏感查询怎么办？"

**大师**："两个办法。第一，数据入库时就做好规范化——统一转小写再入表。第二，建一个 `ngrambf_v1` 跳数索引——这个我们在第21章会详细讲，它专治模糊匹配。但现在你先记住：**LowCardinality 列上的 `=` 比较是最快的人类速度**，为了大小写不敏感而牺牲这个优势，不值得。"

**技术映射**：主键/排序键列被函数包裹 = 稀疏索引完全失效。稀疏索引依赖列值的顺序性来做 Granule 级跳转，函数破坏了这种顺序关系。

---

## 3. 项目实战

### 环境准备

用 Docker 拉起 ClickHouse 25.x 实例并暴露端口：

```bash
docker run -d --name ch-opt-lab \
  -p 8123:8123 -p 9000:9000 \
  clickhouse/clickhouse-server:25.3
```

进入客户端：

```bash
docker exec -it ch-opt-lab clickhouse-client
```

验证版本：

```sql
SELECT version();
-- 25.3.x.x
```

---

### Step 1：创建测试表并导入数据

**目标**：构建一张千万级事件表，覆盖 3 个月数据，模拟真实业务场景。

```sql
-- 创建 events 表，按月份分区，按 (event_date, event_type, user_id) 排序
CREATE TABLE events (
    event_date  Date,
    user_id     UInt64,
    event_type  LowCardinality(String),
    page_url    String,
    duration    UInt32
) ENGINE = MergeTree()
ORDER BY (event_date, event_type, user_id)
PARTITION BY toYYYYMM(event_date);

-- 插入 1000 万行测试数据
-- 日期跨度：2025-01-01 ~ 2025-03-31（约 90 天）
-- 每天约 11 万行，分布在 4 种事件类型上
INSERT INTO events SELECT
    toDate('2025-01-01') + ((number / 100) % 90),
    rand64() % 1000000,
    ['page_view','click','purchase','share'][rand() % 4 + 1],
    concat('/page/', toString(rand64() % 10000)),
    rand() % 300
FROM numbers(10000000);
```

验证数据是否均匀分布：

```sql
SELECT
    event_date,
    count() AS rows,
    uniq(event_type) AS event_types
FROM events
GROUP BY event_date
ORDER BY event_date ASC
LIMIT 5;

-- 输出示例：
-- 2025-01-01  111111  4
-- 2025-01-02  111111  4
-- 2025-01-03  111111  4
```

查看表的分区信息：

```sql
SELECT
    partition,
    name,
    rows
FROM system.parts
WHERE table = 'events' AND active
ORDER BY partition;
```

---

### Step 2：EXPLAIN 分析反模式 SQL

**目标**：用 `EXPLAIN indexes=1` 对比正确与错误写法的索引命中差异。

#### 反模式一：函数包裹分区键

```sql
-- 错误写法：toDate() 破坏分区裁剪
EXPLAIN indexes=1
SELECT count() FROM events
WHERE toDate(event_date) = '2025-01-15';

-- 关键输出字段：
-- KeyCondition: (toDate(event_date) = '2025-01-15')
-- Parts: 3  ← 三个月份分区全部被扫描！
-- Granules: 1221  ← 几乎全部 Granule 被读取
--
-- 分区裁剪完全失败，扫描了全部 3 个月的分区数据
```

```sql
-- 正确写法：直接比较分区列
EXPLAIN indexes=1
SELECT count() FROM events
WHERE event_date = '2025-01-15';

-- 关键输出字段：
-- KeyCondition: (event_date in ['2025-01-15', '2025-01-15'])
-- Parts: 1  ← 只命中一个分区！
-- Granules: 136  ← 仅读取约 136 个 Granule（约 111 万行）
--
-- 分区裁剪生效，扫描量减少约 9 倍
```

#### 反模式二：函数包裹排序键列

```sql
-- 错误写法：toLower() 导致排序键失效
EXPLAIN indexes=1
SELECT * FROM events
WHERE toLower(event_type) = 'purchase';

-- Marks read: 同全表  ← 标记文件全部被读取
-- 排序键完全失效，每个 Granule 的 min/max 索引都无法排除
```

```sql
-- 正确写法：直接等值比较
EXPLAIN indexes=1
SELECT * FROM events
WHERE event_type = 'purchase';

-- Marks read: 约四分之一  ← 排序键索引筛选后只读取匹配的 Granule
-- 主键的稀疏索引通过 min/max 值快速跳过了其他 event_type 的 Granule
```

#### 反模式三：count(DISTINCT) 替代方案

```sql
-- count(DISTINCT) 会耗尽内存做精确去重
-- 10M 行数据，100 万不同 user_id → 内存中维护 100 万个 entry 的哈希表
EXPLAIN PLAN
SELECT count(DISTINCT user_id) FROM events WHERE event_date = '2025-01-15';
-- 执行计划中出现大量的内存分配和哈希计算操作

-- 推荐：uniq() 使用 HyperLogLog 近似去重，内存占用极小
EXPLAIN PLAN
SELECT uniq(user_id) FROM events WHERE event_date = '2025-01-15';
-- 执行计划中只有一个轻量的合并步骤
```

---

### Step 3：PREWHERE vs WHERE 实验

**目标**：对比 PREWHERE 和 WHERE 的过滤时机，理解列读取优化。

```sql
-- WHERE 方式：先读所有列，再过滤
EXPLAIN PLAN
SELECT user_id, page_url
FROM events
WHERE duration > 250;
-- 注意观察 EXPLAIN 输出中 Filter 算子的位置
-- Filter 在读取所有列之后才执行

-- PREWHERE 方式：先用 duration 列过滤，只对命中行读 user_id 和 page_url
EXPLAIN PLAN
SELECT user_id, page_url
FROM events
PREWHERE duration > 250;
-- 观察 EXPLAIN 输出：PREWHERE 在读取其余列之前就完成了过滤

-- 验证自动 PREWHERE 优化（默认 optimize_move_to_prewhere=1）
-- 在 SETTINGS 里显式确认该行为
SELECT user_id, page_url
FROM events
WHERE duration > 250
SETTINGS optimize_move_to_prewhere = 1;
```

**坑点提醒**：如果 `duration` 列几乎全部大于 250（比如 90%），那 PREWHERE 几乎没有过滤效果，反而因为多读了一趟 `duration` 列而变慢。自动优化通常会判断过滤率，但有时会误判。

---

### Step 4：避免全表扫描的常见陷阱

**目标**：识别并修复三种典型导致全表扫描的 SQL 写法。

#### 陷阱一：否定条件（NOT IN / !=）

```sql
-- 否定条件几乎必然全表扫描
-- ClickHouse 的稀疏索引基于 min/max 做区间裁剪
-- != 'page_view' 意味着 Granule 只要包含 page_view 就不能被排除
EXPLAIN indexes=1
SELECT count() FROM events WHERE event_type != 'page_view';
-- Marks read: 几乎全部 —— 无法有效裁剪

-- 修复方案：如果排除值很少且已知，改用 IN 指定命中值
SELECT count() FROM events
WHERE event_type IN ('click', 'purchase', 'share');
-- Marks read: 只读取三种事件类型对应的 Granule
```

#### 陷阱二：OR 条件跨不同列

```sql
-- OR 两边的列不同，需要两套索引分别扫描
EXPLAIN indexes=1
SELECT * FROM events
WHERE user_id = 12345 OR event_type = 'purchase';

-- 修复：拆成 UNION ALL，每个子查询独立走索引
SELECT * FROM events WHERE user_id = 12345
UNION ALL
SELECT * FROM events WHERE event_type = 'purchase' AND user_id != 12345;
-- UNION ALL 的两条子 SQL 各自走自己的最优索引路径
```

#### 陷阱三：LIKE 前导通配符

```sql
-- LIKE '%product%' 无法使用主键索引
-- 前缀是 %，无法利用排序键的有序性来做区间裁剪
EXPLAIN indexes=1
SELECT count() FROM events WHERE page_url LIKE '%product%';
-- Marks read: 全部

-- 修复方案1：如果前缀固定，去掉前面的 %
SELECT count() FROM events WHERE page_url LIKE '/page/product%';

-- 修复方案2：建 ngrambf_v1 跳数索引（第21章详述）
ALTER TABLE events ADD INDEX page_url_ngram page_url TYPE ngrambf_v1(3, 256, 4, 0) GRANULARITY 1;
```

---

### Step 5：optimize_read_in_order 加速

**目标**：利用数据在排序键上的有序性，避免额外的排序开销。

```sql
-- 场景：按 event_date 和 event_type 分组并排序
-- 数据本身按 (event_date, event_type, user_id) 存储在磁盘上
-- 前两列天然有序，不需要再排序

-- 关闭优化：读取后需要额外做一次排序
SELECT event_date, event_type, count()
FROM events
WHERE event_date >= '2025-01-01'
GROUP BY event_date, event_type
ORDER BY event_date, event_type
SETTINGS optimize_read_in_order = 0;
-- EXPLAIN 中会多出 Sorting 算子

-- 开启优化：顺序读取即可，无需额外排序（默认开启）
SELECT event_date, event_type, count()
FROM events
WHERE event_date >= '2025-01-01'
GROUP BY event_date, event_type
ORDER BY event_date, event_type
SETTINGS optimize_read_in_order = 1;
-- EXPLAIN 中 Sorting 算子消失，数据按存储顺序直接读取

-- 注意：ORDER BY 的列顺序必须严格匹配 ORDER BY 键的左缀，否则优化无效
-- 例如 ORDER BY event_type, event_date 就无法利用 optimize_read_in_order
```

---

### Step 6：send_logs_level='trace' 追踪查询

**目标**：获取查询执行每一步的精确耗时，定位瓶颈。

```sql
-- 开启 trace 级别日志
SET send_logs_level = 'trace';

-- 执行待分析的查询
SELECT uniq(user_id) FROM events WHERE event_date = '2025-01-15';

-- trace 输出示例（实际输出为多行时间戳日志）：
-- [trace] 2025.01.15 10:00:00.001 Context: Reading 111111 rows from 202501_1_1_0
-- [trace] 2025.01.15 10:00:00.012 Aggregator: Aggregating 111111 rows
-- [trace] 2025.01.15 10:00:00.015 Aggregator: Merging aggregated data
-- [trace] 2025.01.15 10:00:00.018 Read 111111 rows, 889.96 KiB in 0.017 sec
-- [trace] 2025.01.15 10:00:00.018 Query finished

-- 关闭 trace 日志（非常重要！）
SET send_logs_level = 'warning';
```

**重要**：trace 日志会随查询结果混在标准输出中，如果你用 HTTP 接口查询，日志会出现在响应头里。不建议在生产环境的常规查询中开启。

---

### 测试验证

汇总三条反模式修复前后的对比：

```sql
-- 对比测试：记录耗时
-- 测试1：分区裁剪
-- 前 -> 后：1221 granules → 136 granules（约 9 倍减少）
-- 测试2：排序键索引
-- 前 -> 后：全表 marks → 约 1/4 marks（约 4 倍减少）
-- 测试3：count(DISTINCT) vs uniq
-- 前 -> 后：内存从 ~200MB → ~2MB（约 100 倍减少）

-- 一体测试脚本
SELECT '=== 反模式1（分区裁剪失败） ===' AS test;
SELECT count() FROM events WHERE toDate(event_date) = '2025-01-15';

SELECT '=== 修复后（分区裁剪成功） ===' AS test;
SELECT count() FROM events WHERE event_date = '2025-01-15';

SELECT '=== 反模式2（排序键失效） ===' AS test;
SELECT count() FROM events WHERE toLower(event_type) = 'purchase';

SELECT '=== 修复后（排序键命中） ===' AS test;
SELECT count() FROM events WHERE event_type = 'purchase';
```

在 `clickhouse-client` 中执行以上脚本，观察每个查询的 `Elapsed` 时间差异。

---

## 4. 项目总结

### SQL 优化核心清单

| 反模式 | 问题根因 | 正确写法 |
|--------|---------|---------|
| `toDate(col) = '2025-01-01'` | 函数包裹导致分区裁剪失效 | `col >= '2025-01-01' AND col < '2025-01-02'` |
| `count(DISTINCT col)` | 精确去重消耗大量内存 | 用 `uniq(col)` 近似去重，或 `uniqExact(col)` 精确去重 |
| `WHERE col != value` / `NOT IN` | 稀疏索引无法排除含 value 的 Granule | 改用 `IN` 枚举命中值 |
| `LIKE '%keyword%'` | 前导通配符无法利用有序索引 | 去掉前导 `%` 或建 `ngrambf_v1` 跳数索引 |
| 函数包裹排序键列 | 主键稀疏索引完全失效 | WHERE 条件中不对排序列做任何函数运算 |
| OR 跨列条件 | 无法单索引覆盖 | 拆为 `UNION ALL`，各自走最优索引 |

### EXPLAIN 工具速查

| EXPLAIN 类型 | 用途 | 关键信息 |
|-------------|------|---------|
| `EXPLAIN PLAN` | 宏观执行计划拓扑 | 算子链条：Read → Filter → Aggregate → Sort |
| `EXPLAIN indexes=1` | 索引命中诊断 | Parts 数、Granules 扫描数、KeyCondition 表达式 |
| `EXPLAIN PIPELINE` | 物理执行并行度 | Streams 数量、线程分配、瓶颈阶段 |
| `EXPLAIN AST` | 抽象语法树验证 | 解析后的内部表示 |
| `EXPLAIN SYNTAX` | 优化重写结果 | ClickHouse 的隐式 SQL 优化 |
| `send_logs_level='trace'` | 逐步骤耗时追踪 | 每步的 rows/s、耗时、IO 量 |

### 适用场景

- **看板查询加速**：运营看板的聚合 SQL 通常是优化的第一优先级——高频刷新的 GROUP BY 查询对分区裁剪和索引命中极度敏感。
- **ETL 查询调优**：数据清洗过程中常有大范围扫描，通过 EXPLAIN 确认是否利用了分区裁剪和排序键。
- **Ad-hoc 分析加速**：数据分析师写的探索性 SQL 最容易踩坑——`toDate()`、`toLower()`、`LIKE '%xxx%'` 是最常见的三大元凶。

### 不适用场景

- **写入密集型场景**：SQL 优化主要面向查询，INSERT 性能瓶颈应关注异步写入、批量大小和 Merge 策略。
- **分布式表全局聚合**：`optimize_read_in_order` 对 Distributed 表无效，需要在本地表层面优化。

### 常见踩坑经验

1. **`EXPLAIN indexes=1` 的 Granules 数是估算值**。CLICKHOUSE 基于分区元数据和标记文件做预估，实际扫描的 Granule 数可能略有偏差（尤其在 Merge 中途）。把它当作相对比较指标，不要当作精确数字。

2. **`optimize_move_to_prewhere` 不是万能的**。优化器基于列的压缩大小和基数做判断，如果列的类型是 LowCardinality 且读取开销很小，自动 PREWHERE 可能反而增加一次额外的列读取。在关键查询上建议关闭自动优化，手动指定 PREWHERE 并用 EXPLAIN 验证。

3. **`send_logs_level='trace'` 只在单条排查时用**。trace 日志对磁盘 IO 和网络带宽都有影响。一次 trace 可能产生几十 MB 日志文本。务必在排查结束后切回 `warning` 级别。

4. **UNION ALL 拆 OR 时注意去重**。如果 OR 两边可能命中相同的行，需要在 UNION ALL 的第二条里加排除条件（如 `AND user_id != 12345`），否则会返回重复行。如果 UNION ALL 结果集本来就不需要去重（比如你只做 count），则不额外加排除条件以获得最快性能。

5. **分区键和排序键设计直接影响后续所有优化的天花板**。这是根因——如果分区键和排序键设错了，后续的 EXPLAIN、PREWHERE、optimize_read_in_order 全部白搭。在表设计阶段就考虑好查询模式，是最高杠杆的优化。

### 思考题

1. 为什么 `WHERE event_date = '2025-01-15'` 能触发分区裁剪，而 `WHERE toDate(event_date) = '2025-01-15'` 不能？ClickHouse 的分区裁剪是**表达式匹配**而非**语义等价推导**——它不会自动识别 `toDate(event_date)` 在含义上等价于 `event_date`。优化器看到的表达式必须与分区定义表达式在结构上一致才能匹配。提示：延伸思考——如果分区键是 `toYYYYMM(event_date)`，那 `WHERE event_date >= '2025-01-01' AND event_date < '2025-02-01'` 能触发裁剪吗？

2. 一条 SQL 有 10 个 WHERE 条件，如何用 EXPLAIN 快速定位是哪个条件导致了全表扫描？方法：逐条件拆解测试。先用 `EXPLAIN indexes=1` 跑完整 SQL，记录 Granules 扫描数。然后依次注释掉单个条件，观察 Granules 数的变化。如果去掉某个条件后 Granules 数骤降，说明该条件是瓶颈。此外，在 `EXPLAIN indexes=1` 的输出中直接观察 `KeyCondition`——只有出现在 `KeyCondition` 里的条件才能被主键索引利用，没出现的就是未命中索引的条件。

