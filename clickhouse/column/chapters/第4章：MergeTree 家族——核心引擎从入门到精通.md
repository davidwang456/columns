# 第4章：MergeTree 家族——核心引擎从入门到精通

## 1. 项目背景

某广告技术公司（AdTech）正面临数据膨胀的挑战。他们的核心业务是追踪广告曝光、点击与转化——每天数百亿条事件从SDK和服务器端源源不断地涌入。看似简单的需求背后藏着三个棘手的子场景：

**场景一：事件去重。** 由于移动网络不稳定，SDK端会重传同一事件的多个副本。运营团队发现Dashboard上的曝光量虚高了15%，因为同一条曝光事件被多次写入。他们需要一个能自动按业务主键去重的存储方案。

**场景二：实时汇总。** 广告主需要在秒级看到各Campaign的花费和点击量汇总。如果每次都从原始明细表中做GROUP BY，上百亿行数据的查询能把集群拖垮。他们需要数据在写入时就完成"预聚合"。

**场景三：状态变更。** 用户的转化状态是会变的——下午点击了广告，晚上才完成购买。传统的追加写入（append-only）无法表达"这条记录的状态已经被更新/撤销了"的语义。团队被迫在应用层维护一套复杂的补偿逻辑。

技术负责人老张最初用最基础的`MergeTree()`引擎建了所有表，结果噩梦开始了：重传的数据导致曝光虚高，客户投诉数据不准；领导要看即时汇总，查询却要跑半分钟以上；转化状态的更新更是要靠定时任务跑全量修正，维护成本极高。

老张找到团队的技术顾问"大师"求助。大师看了一眼表结构，淡淡说道："你用错了引擎——ClickHouse的MergeTree不是只有一种，而是一个家族。每一种解决一类问题。选对引擎，这些问题迎刃而解；选错引擎，就是给自己埋坑。"

于是，一场关于MergeTree家族的深度探索就此展开。

## 2. 项目设计：剧本式交锋对话

会议室里，白板上画满了架构图。

**小胖**（两年经验的后端开发）率先发难："不就存个数据嘛，一张表不就够了？为啥要分这么多种引擎？反正都是INSERT SELECT，有啥区别？我们MySQL一张InnoDB走天下不也活得好好的？"

**大师**笑了笑，在白板上画了一个方块："在回答之前，你先把MergeTree的基本构造搞清楚。MergeTree不是一张简单的表——它的核心概念是**Part（数据片段）**。每次INSERT写入的数据不会直接拼接到已有数据上，而是形成一个独立的Part写入磁盘。"

大师继续画道："每个Part内部，数据按照你指定的ORDER BY键排序后存成列式文件。ClickHouse在每个Part上建立**稀疏索引**——默认每隔8192行（这就是所谓的Granule/颗粒）记录一个索引标记。查询时，引擎根据这些标记快速跳过不相关的Granule，大幅减少I/O。"

**小白**（刚转数据方向，问题特别细）追问："那ORDER BY、PARTITION BY、PRIMARY KEY这三个参数到底怎么理解？我一直分不清。"

大师在白板上分别标注：

> - **ORDER BY**：决定了数据在每个Part内部的物理排序顺序——这是最核心的参数，直接影响查询性能和去重/聚合的行为。
> - **PARTITION BY**：把数据按某个维度拆成不同的物理目录。比如按天分区，查询时可以直接跳过不相关的分区，实现分区裁剪。
> - **PRIMARY KEY**：如果不指定，默认就等于ORDER BY；如果指定，必须是ORDER BY的前缀。它的作用是指定稀疏索引的索引键。

"注意，"大师强调，"MergeTree的主键**不保证唯一性**！这和MySQL完全不同。它只是一个索引优化手段，不会帮你挡重复数据。"

**小胖**挠头："那数据写进去之后，到底经历了什么？为什么我INSERT完立刻SELECT，还能看到数据？不是说要等merge吗？"

"好问题。"大师画了一条流水线：

```
INSERT → 内存排序 → 写入磁盘（形成新Part） → 后台Merge
```

"MergeTree写入的核心流程是：数据先写入到内存中完成排序，然后刷盘形成一个新Part——这个Part是**立即可见**的，所以你INSERT完立刻查就能看到。后台有一个异步的Merge调度器，会定期把多个小Part合并成更大的Part，这个过程中数据会被重新排序，去重、聚合等逻辑也是在这个阶段执行的。"

**小白**眼睛一亮："也就是说，去重不是写的时候做的，而是合的时候做的？"

"正是！这就是很多人踩坑的地方。"大师开始介绍家族成员：

> **ReplacingMergeTree**：在Merge时，对于ORDER BY键相同的行，只保留版本号最大（或最新插入）的那一行。适合CDC同步、重传去重这类场景——但要记住，去重发生在Merge时，查询时默认不加FINAL可能看到重复数据。

> **SummingMergeTree**：在Merge时，对ORDER BY键相同的行，将指定的数值列自动求和。适合"按维度汇总金额/次数"的场景——但非数值列不会自动处理，只会保留第一个Part中的值。

> **AggregatingMergeTree**：和SummingMergeTree类似，但配合`AggregateFunction`数据类型使用，可以做更复杂的聚合（如去重计数uniqState、分位数等）。通常与物化视图搭档，实现增量预聚合。

> **CollapsingMergeTree**：通过一个`sign`列（值为1或-1）来标记行的增删。Merge时，ORDER BY键相同且sign相消（1和-1）的行会互相抵消删除。适合状态变更场景——但如果少写了一个-1行，数据就会翻倍。

> **VersionedCollapsingMergeTree**：CollapsingMergeTree的升级版，增加了一个版本号列来保证折叠的严格配对。适合需要精确保证折叠顺序的场景。

**小胖**恍然大悟："所以去重用Replacing，求和使用Summing，状态就用Collapsing？"

"大致如此，但每种引擎都有自己的坑。"大师正色道，"接下来我们实战一下，你就会更清楚。"

## 3. 项目实战

### 环境准备

假设已有一套运行中的ClickHouse实例（Docker或裸机均可）。进入客户端：

```bash
clickhouse-client
```

### Step 1：MergeTree基础表创建

首先，创建最基础的MergeTree表来存储广告曝光明细：

```sql
CREATE DATABASE IF NOT EXISTS ad_analytics;

CREATE TABLE ad_analytics.ad_impressions (
    event_time DateTime,
    ad_id UInt32,
    campaign_id UInt32,
    user_id UInt64,
    event_type String,
    cost Decimal(10,4)
) ENGINE = MergeTree()
ORDER BY (event_time, ad_id, user_id)
PARTITION BY toYYYYMMDD(event_time);
```

解释一下关键设计：
- `ORDER BY (event_time, ad_id, user_id)`：按时间+广告+用户排序，符合最常见的查询模式（按时间范围查某个广告的数据）。
- `PARTITION BY toYYYYMMDD(event_time)`：按天分区，方便做时间范围裁剪和数据生命周期管理（TTL删除过期分区）。
- 没有单独指定`PRIMARY KEY`，所以主键等于ORDER BY。

写入一些测试数据：

```sql
INSERT INTO ad_analytics.ad_impressions VALUES
    ('2025-01-15 10:30:00', 1001, 2001, 50001, 'impression', 0.0050),
    ('2025-01-15 10:30:00', 1001, 2001, 50001, 'impression', 0.0050), -- 重复
    ('2025-01-15 10:31:00', 1002, 2001, 50002, 'impression', 0.0060),
    ('2025-01-15 10:32:00', 1003, 2002, 50003, 'click', 0.0500);
```

查询一下，两条完全相同的"重复"记录都出现了——基础的MergeTree不会帮你去重。

```sql
SELECT * FROM ad_analytics.ad_impressions;
-- 返回4行，包括两条重复的曝光记录
```

### Step 2：ReplacingMergeTree去重实战

现在解决第一个业务痛点——事件去重。创建一张ReplacingMergeTree表：

```sql
CREATE TABLE ad_analytics.ad_impressions_dedup (
    event_time DateTime,
    ad_id UInt32,
    campaign_id UInt32,
    user_id UInt64,
    event_type String,
    cost Decimal(10,4)
) ENGINE = ReplacingMergeTree()
ORDER BY (event_time, ad_id, user_id)
PARTITION BY toYYYYMMDD(event_time);
```

写入同样的数据（包含重复）：

```sql
INSERT INTO ad_analytics.ad_impressions_dedup VALUES
    ('2025-01-15 10:30:00', 1001, 2001, 50001, 'impression', 0.0050),
    ('2025-01-15 10:30:00', 1001, 2001, 50001, 'impression', 0.0050), -- 重复
    ('2025-01-15 10:31:00', 1002, 2001, 50002, 'impression', 0.0060),
    ('2025-01-15 10:32:00', 1003, 2002, 50003, 'click', 0.0500);
```

**关键验证1：不触发Merge时，重复数据仍然可见**

```sql
SELECT * FROM ad_analytics.ad_impressions_dedup;
-- 可能仍然返回4行！因为后台merge还没执行
```

查看当前Part情况：

```sql
SELECT 
    name,
    partition,
    rows,
    active,
    level
FROM system.parts
WHERE database = 'ad_analytics' 
  AND table = 'ad_impressions_dedup';
-- 你会看到数据还在一个小Part里，merge尚未发生
```

**关键验证2：手动触发Merge（OPTIMIZE）**

```sql
OPTIMIZE TABLE ad_analytics.ad_impressions_dedup FINAL;
```

再次查询，重复记录被去掉了：

```sql
SELECT * FROM ad_analytics.ad_impressions_dedup;
-- 现在只有3行，重复的那条(1001, 2001, 50001)只剩下一行
```

**为什么必须加FINAL？** 因为OPTIMIZE不带FINAL时，ClickHouse只会做一次常规的part合并，但不保证将同一分区的所有part合成一个——可能存在跨part的重复。`FINAL`告诉ClickHouse：把所有part合并成一个，完成后不再有跨part重复。在生产环境中，FINAL代价很高（要重写整个分区的数据），不推荐频繁使用。

**替代方案：查询时加FINAL**

```sql
SELECT * FROM ad_analytics.ad_impressions_dedup FINAL;
-- 查询时实时去重，但同样会显著降低查询性能
```

**最佳实践**：ReplacingMergeTree适合"最终一致性"去重，允许短时间内存在重复，依赖后台Merge逐步消除。不要在业务逻辑中假设它"实时去重"。

### Step 3：SummingMergeTree实时汇总

第二个场景：广告主想看每个Campaign每天的总花费。我们用SummingMergeTree来做预聚合。

```sql
CREATE TABLE ad_analytics.campaign_cost_agg (
    campaign_date Date,
    campaign_id UInt32,
    impression_count UInt64,
    click_count UInt64,
    cost Decimal(10,4)
) ENGINE = SummingMergeTree(cost, impression_count, click_count)
ORDER BY (campaign_date, campaign_id)
PARTITION BY toYYYYMM(campaign_date);
```

`SummingMergeTree(cost, impression_count, click_count)`中的参数指定了哪些列在Merge时需要自动求和。Nested结构里的列也会被自动处理。

分批插入数据，模拟多次写入同一Campaign的情况：

```sql
-- 第一批：Campaign 2001的曝光数据
INSERT INTO ad_analytics.campaign_cost_agg VALUES
    ('2025-01-15', 2001, 100, 0, 0.5000),
    ('2025-01-15', 2002, 200, 0, 1.2000);

-- 过一会儿又来了一批（可能是从不同kafka分区消费的）
INSERT INTO ad_analytics.campaign_cost_agg VALUES
    ('2025-01-15', 2001, 50, 10, 0.3000),
    ('2025-01-15', 2002, 80, 5, 0.5000);
```

**关键验证：Merge前后的差异**

Merge前查询——Campaign 2001可能有多行：

```sql
SELECT * FROM ad_analytics.campaign_cost_agg 
WHERE campaign_id = 2001;
-- 可能返回两行：(100,0,0.50) 和 (50,10,0.30)
```

如果不加GROUP BY汇总，数据看起来是"错的"。但实际上，SummingMergeTree的正确使用方式是**查询时自己加GROUP BY**：

```sql
SELECT 
    campaign_date,
    campaign_id,
    SUM(impression_count) AS total_impressions,
    SUM(click_count) AS total_clicks,
    SUM(cost) AS total_cost
FROM ad_analytics.campaign_cost_agg
WHERE campaign_id = 2001
GROUP BY campaign_date, campaign_id;
-- 返回一行：(150, 10, 0.80) —— 这才是正确结果
```

手动Merge后，数据被合并：

```sql
OPTIMIZE TABLE ad_analytics.campaign_cost_agg FINAL;

SELECT * FROM ad_analytics.campaign_cost_agg 
WHERE campaign_id = 2001;
-- 返回一行：(150, 10, 0.80) —— merge时自动求和了
```

**踩坑提醒**：SummingMergeTree对非数值列（如String）不会自动合并，只会保留其中一个Part的值（通常是最先写入的那个）。所以如果ORDER BY键相同但非数值列不同，Merge后数据可能丢失信息。这也是为什么SummingMergeTree通常只用来做纯数值的预聚合，而且建议查询时始终用GROUP BY来兜底。

### Step 4：AggregatingMergeTree + 物化视图

当我们需要更复杂的聚合（比如去重计数、分位数、TopN）时，SummingMergeTree就不够用了。这时需要AggregatingMergeTree配合物化视图。

先创建明细表（数据源）：

```sql
CREATE TABLE ad_analytics.ad_events_raw (
    event_time DateTime,
    campaign_id UInt32,
    user_id UInt64,
    event_type String,
    cost Decimal(10,4)
) ENGINE = MergeTree()
ORDER BY (event_time, campaign_id)
PARTITION BY toYYYYMMDD(event_time);
```

再创建聚合目标表：

```sql
CREATE TABLE ad_analytics.campaign_agg_stats (
    campaign_date Date,
    campaign_id UInt32,
    unique_users AggregateFunction(uniq, UInt64),
    total_cost AggregateFunction(sum, Decimal(10,4)),
    event_count AggregateFunction(count, UInt64)
) ENGINE = AggregatingMergeTree()
ORDER BY (campaign_date, campaign_id)
PARTITION BY toYYYYMM(campaign_date);
```

注意列类型是`AggregateFunction(uniq, UInt64)`这种特殊类型——它存储的不是最终值，而是**中间聚合状态**。

创建物化视图，建立从明细表到聚合表的自动流转：

```sql
CREATE MATERIALIZED VIEW ad_analytics.campaign_agg_mv
TO ad_analytics.campaign_agg_stats
AS SELECT
    toDate(event_time) AS campaign_date,
    campaign_id,
    uniqState(user_id) AS unique_users,
    sumState(cost) AS total_cost,
    countState() AS event_count
FROM ad_analytics.ad_events_raw
GROUP BY campaign_date, campaign_id;
```

写入测试数据：

```sql
INSERT INTO ad_analytics.ad_events_raw VALUES
    ('2025-01-15 10:00:00', 2001, 10001, 'impression', 0.0050),
    ('2025-01-15 10:01:00', 2001, 10001, 'click', 0.0500),     -- 同一用户
    ('2025-01-15 10:02:00', 2001, 10002, 'impression', 0.0050),
    ('2025-01-15 10:03:00', 2001, 10002, 'click', 0.0600);
```

物化视图会自动把数据聚合并写入`campaign_agg_stats`。查询聚合结果时，需要调用`merge`函数来合并中间状态：

```sql
SELECT 
    campaign_date,
    campaign_id,
    uniqMerge(unique_users) AS uv,
    sumMerge(total_cost) AS total_spend,
    countMerge(event_count) AS total_events
FROM ad_analytics.campaign_agg_stats
GROUP BY campaign_date, campaign_id;
-- 结果：Campaign 2001，UV=2，总花费=0.12，总事件=4
```

这个架构的优势在于：对于任何一个新的GROUP BY需求，你不需要等在查询时扫描百亿行；物化视图在写入时就帮你把结果算好了，查询只需读取很少的数据做最终合并。

### Step 5：CollapsingMergeTree状态变更

最后一个场景：用户转化状态的变更。用CollapsingMergeTree的sign机制来实现。

```sql
CREATE TABLE ad_analytics.user_conversion_state (
    event_date Date,
    user_id UInt64,
    campaign_id UInt32,
    status String,
    sign Int8
) ENGINE = CollapsingMergeTree(sign)
ORDER BY (event_date, user_id, campaign_id)
PARTITION BY toYYYYMM(event_date);
```

模拟场景：用户最初是"clicked"状态，后来升级为"converted"——我们需要先取消旧状态，再插入新状态：

```sql
-- 用户初始状态：clicked (sign=1 表示"新增")
INSERT INTO ad_analytics.user_conversion_state VALUES
    ('2025-01-15', 10001, 2001, 'clicked', 1),
    ('2025-01-15', 10002, 2001, 'clicked', 1);

-- 用户10001完成了购买，需要更新状态：先取消旧状态，再写入新状态
INSERT INTO ad_analytics.user_conversion_state VALUES
    ('2025-01-15', 10001, 2001, 'clicked', -1),    -- 取消旧的clicked状态
    ('2025-01-15', 10001, 2001, 'converted', 1);   -- 写入新的converted状态
```

**Merge前查询（注意要特殊处理sign列）：**

正确方式是将sign纳入计算：

```sql
SELECT 
    user_id,
    campaign_id,
    status,
    sum(sign) AS effective
FROM ad_analytics.user_conversion_state
GROUP BY user_id, campaign_id, status
HAVING effective > 0;
-- 如果merge未执行，你会看到：
-- 10001, 2001, 'converted', 1  ✓（新增的状态，保留）
-- 10002, 2001, 'clicked', 1    ✓（未被取消，保留）
-- 10001的clicked行(sign=1 + sign=-1)抵消，effective=0被过滤掉
```

**Merge后：**

```sql
OPTIMIZE TABLE ad_analytics.user_conversion_state FINAL;

SELECT * FROM ad_analytics.user_conversion_state;
-- 10001的clicked(+1)和(-1)两行彻底合并消失
-- 只剩下：10001/converted 和 10002/clicked
```

**最常见踩坑：少写了-1行。** 假如只INSERT了新的converted状态而忘记取消旧的clicked状态：
```sql
INSERT INTO ... VALUES ('2025-01-15', 10001, 2001, 'converted', 1);
-- 没有先写 ('2025-01-15', 10001, 2001, 'clicked', -1)
-- 结果：用户10001同时有clicked和converted两条记录，数据翻倍！
```

这就是CollapsingMergeTree最危险的地方——它严格要求成对写入，不像UPDATE那样原子。生产环境中需要应用层严格保证sign列的配对正确性。

### 测试验证

对以上所有表，可以通过`system.parts`随时查看Part的合并状态：

```sql
SELECT 
    database,
    table,
    partition,
    name,
    rows,
    bytes_on_disk,
    modification_time
FROM system.parts
WHERE database = 'ad_analytics'
ORDER BY table, partition, name;
```

观察规律：每次INSERT产生一个新的Part（以`all_`开头的名字表示wide格式，`%_%_%`的数字编号表示compact格式），OPTIMIZE后同分区的Part合并为一个。

也可以查看Merge操作的历史：

```sql
SELECT 
    database,
    table,
    event_time,
    rows_read,
    rows_written,
    bytes_read / 1048576 AS mb_read
FROM system.part_log
WHERE database = 'ad_analytics'
  AND event_type = 'MergeParts'
ORDER BY event_time DESC
LIMIT 10;
```

## 4. 项目总结

### 各引擎对比表

| 引擎 | 核心能力 | 去重/聚合时机 | 适用场景 | 主要陷阱 |
|------|---------|-------------|---------|---------|
| **MergeTree** | 基础引擎，高性能写入和查询 | 不处理重复 | 明细数据存储，日志存储 | 无去重能力，需应用层处理 |
| **ReplacingMergeTree** | 按ORDER BY键去重，保留最新版本 | 后台Merge时 | CDC同步、重传去重、幂等写入 | 不保证实时去重；FINAL代价大 |
| **SummingMergeTree** | 按ORDER BY键对数值列求和 | 后台Merge时 | 预算汇总、PV/UV预聚合 | 非数值列不合并；查询仍需GROUP BY |
| **AggregatingMergeTree** | 支持复杂聚合函数的状态合并 | 后台Merge时 | 物化视图的目标表，复杂预聚合 | 需配合AggregateFunction类型和物化视图使用；学习曲线较陡 |
| **CollapsingMergeTree** | 通过sign列标记增删，抵消删除 | 后台Merge时 | 状态变更、增量/减量操作 | 必须成对写入(-1/+1)；漏写-1导致数据翻倍 |
| **VersionedCollapsingMergeTree** | 带版本号的折叠，保证配对正确 | 后台Merge时 | 需要严格顺序保证的状态变更 | 生产环境较少需要，复杂度更高 |

### 适用场景

- **ReplacingMergeTree**：最适合做"最终一致性去重"。比如从Kafka消费CDC数据，允许几秒到几分钟内存在重复，依赖后台Merge自然消除。**不适合**需要强一致实时去重的场景（用Distributed+Replicated表配合去重逻辑更好）。

- **SummingMergeTree**：物化视图的经典搭档。在明细表上创建物化视图，写入SummingMergeTree目标表，实现查询加速。注意：它只替换GROUP BY + SUM，不能替代所有聚合需求。

- **AggregatingMergeTree**：SummingMergeTree的进阶版。当你需要的不只是SUM，还要去重计数（uniq）、分位数（quantile）、TopK等，就得用它。典型模式：`明细表 → 物化视图 → AggregatingMergeTree目标表`。

- **CollapsingMergeTree**：处理"增量更新"语义。比如广告系统里，用户状态从"点击"变为"转化"，或者财务系统里的"冲销"操作。如果业务天然就是"先增后减"/"状态流转"的模式，用CollapsingMergeTree比维护UPDATE逻辑简单得多。

### 注意事项

1. **Merge是后台异步执行的，不保证即时性。** 不要依赖INSERT后立即、自动完成去重或聚合——这是ClickHouse特意为之的设计，牺牲一点实时一致性换取极高的写入吞吐。

2. **FINAL是一把双刃剑。** `OPTIMIZE ... FINAL`和SELECT时的`FINAL`修饰符能强制完成去重/折叠，但代价是大量CPU和I/O。生产环境中应避免在高频路径上使用FINAL。

3. **ORDER BY决定了去重/聚合的粒度。** 这在ReplacingMergeTree里意味着"以什么维度来判定重复"，在SummingMergeTree里意味着"以什么维度来汇总"。ORDER BY选错，整个引擎的行为都会偏离预期。

4. **分区要合理，不宜过细。** 分区过细（如按小时）会导致成千上万个小Part，加重Merge负担；分区过粗（如按年）则分区裁剪效果差。按天分区（`toYYYYMMDD`）是最常见的选择。

### 常见踩坑经验

**坑1："我以为ReplacingMergeTree能实时去重"。** 很多新手往ReplacingMergeTree里INSERT完立刻SELECT *，发现重复数据还在，以为表坏了。实际上Merge可能几秒到几分钟后才执行，而且不同分区之间的去重需要通过跨分区merge来实现（更慢）。解决方案：要么接受最终一致，要么查询时使用FINAL（代价高），要么在写入端做幂等。

**坑2："SummingMergeTree里String列的值怎么丢了？"。** SummingMergeTree只对数值列求和，非数值列（String、Date等）只会保留一个Part中的值，其余Part的该列数据被丢弃。如果ORDER BY键相同但String列不同，你会丢失信息。最佳实践：SummingMergeTree只放数值列，String等维度列放在另一个MergeTree表里做JOIN。

**坑3："CollapsingMergeTree我忘了写-1，数据翻了倍"。** 这是CollapsingMergeTree最典型的错误。比如更新用户状态，你记得INSERT新状态（sign=1），但忘了先INSERT一条sign=-1来撤销旧状态。结果就是同一个用户有两条"有效"记录，指标直接翻倍。**一定要在业务代码里封装成"先插入撤销行，再插入新行"的原子操作。**

**坑4："用OPTIMIZE FINAL太随意"。** 某团队写了一个每小时执行一次`OPTIMIZE TABLE ... FINAL`的CronJob，结果每逢整点集群就被打满。FINAL会锁定分区并重写全部数据，一亿行的表可能要跑几分钟。正确做法是让ClickHouse按自己的节奏做后台Merge，只在维护窗口或数据修复时手动执行FINAL。

### 思考题

1. **为什么ClickHouse不保证MergeTree的merge立即执行？这种设计有什么好处？**

   *提示：考虑写入性能、合并策略的灵活性、以及OLAP场景下对"最终一致"的容忍度。对比传统数据库的"写时合并"和ClickHouse的"读时优化+后台合并"两种设计哲学。*

2. **如果ReplacingMergeTree有一亿行数据需要去重，`OPTIMIZE TABLE ... FINAL`会有什么风险？**

   *提示：思考FINAL执行的原理——它需要将同一分区的所有Part读出来、排序、去重、再写回一个新Part。一亿行数据在这个过程中会产生什么样的资源开销？对同时进行的查询有什么影响？*

---

*下一章预告：数据的分布式之旅——ReplicatedMergeTree与Distributed表引擎，我们将揭开ClickHouse集群数据高可用和水平扩展的秘密。*
