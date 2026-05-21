# 第8章：SQL 查询实战——从基本查询到窗口函数

> **版本**：ClickHouse 25.x LTS
> **定位**：基础篇核心章节。把你的 SQL 技能从 MySQL 迁移到 ClickHouse 列存世界，理解哪些写法能充分发挥列存优势，哪些写法会变成灾难。
> **前置阅读**：第5章（分区与排序键）、第7章（主键与稀疏索引）
> **预计阅读**：40 分钟 | **实战耗时**：50 分钟

---

## 1. 项目背景

某社交 App 的增长团队最近接了一个硬仗——投资人下周要看 C 轮融资数据，老板点名要"用户留存漏斗全链路分析"。需求清单在群里刷屏：（1）近 30 天每日新增用户数趋势图；（2）次日/7 日/30 日留存率曲线——这是投资人最关心的指标；（3）Top 10 高活跃功能，按用户 Session 数排名；（4）注册→首次付费→复购的漏斗转化率。团队里四个人对着这份需求沉默了整整三秒——不是需求有多难，而是数据量摆在那：这张 `user_actions` 表已经攒了 1.2 亿行，MySQL 上跑一条留存分析的 `LEFT JOIN` 脚本用了 47 分钟才吐出结果。

"干脆换 ClickHouse，上个月刚搭的生产集群，"增长组 Leader 拍板，"小胖你把之前的 MySQL SQL 脚本拿过来改改，晚上发给我。"

小胖信心满满地打开他的 MySQL 脚本，CTRL+C、CTRL+V 到 ClickHouse 客户端，回车。十秒钟过去了，三十秒过去了，屏幕上蹦出一行红字——`DB::Exception: Unknown function dateDiff`。他愣了一下，改成 `DATEDIFF`，又报错。改成 `date_diff`，还是不对。翻了文档才发现，ClickHouse 的日期函数叫 `dateDiff('day', a, b)`，带引号的粒度参数，和 MySQL 完全不是一个风格。

这只是冰山一角。接下来的两天里，小胖踩了一连串的坑：LEFT ANY JOIN 写成了 LEFT JOIN 导致结果条数翻倍；窗口函数 `ROW_NUMBER() OVER (PARTITION BY user_id)` 在 1 亿用户上跑了 8 分钟；写了一个不带分区过滤的 `uniqExact()` 把集群内存榨干到 95%；用 `count(DISTINCT user_id)` 去计算 UV，被大师一句"用 uniq 试试"后，查询从 12 秒变成了 0.8 秒。

**核心痛点**：
- ClickHouse SQL 方言与 MySQL 差异巨大——函数名不同、JOIN 语义不同、聚合函数行为不同。
- 列存引擎对窗口函数的执行方式与行存截然不同，PARTITION BY 高基数列时性能断崖式下跌。
- 缺乏对 PREWHERE、SAMPLE、ARRAY JOIN 等专属特性的了解，写出来的 SQL 跑得动但跑不快。

本章将通过一个完整的增长分析项目，带你从基本查询一路写到窗口函数和高级 JOIN，彻底完成从 MySQL SQL 到 ClickHouse SQL 的思维切换。

---

## 2. 项目设计：剧本式交锋对话

会议室投影幕上，小胖的留存查询已经跑了三分钟，进度条还在转。

**小胖**（抓头发）：“我就是把 MySQL 那段脚本翻译过来而已，怎么就跟换了门语言似的？SQL 不就是 SELECT...FROM...WHERE 嘛，MySQL 和 ClickHouse 有什么区别？”

**大师**（端着一杯咖啡走过来）：“区别大了。你先看看你这行——”

```sql
SELECT date_diff('day', register_time, event_time)
```

"ClickHouse 的函数叫 `dateDiff`，不是 `date_diff`也不是`DATEDIFF`。而且第一个参数必须带引号。MySQL 和 ClickHouse 的 SQL 虽然长得像，但方言差异足以让你的迁移脚本从头错到尾。先别急着改代码，我们花十分钟把 ClickHouse SQL 的几个关键差异过一遍。"

**小胖**：“那你说说，到底有哪些不一样？”

**大师**：“三大差异。第一，**JOIN 语义不同**。MySQL 的 JOIN 就是找匹配行，一表对多表就膨胀。ClickHouse 在标准 JOIN 之外，还提供了 ALL JOIN、ANY JOIN、SEMI JOIN、ANTI JOIN、ASOF JOIN 五种变体。你用 LEFT JOIN 做留存计算的时候，一个用户的活跃记录有 300 条，LEFT JOIN 后结果行数直接膨胀到 300 倍——你以为你在算留存，其实你在算笛卡尔积。正确的做法是用 LEFT ANY JOIN，每个左边行只取右边一条匹配，或者干脆用子查询 + `IN`。”

**小胖**（恍然大悟）：“难怪我那行数一直不对劲！那 SEMI JOIN 和 ANTI JOIN 又是什么鬼？”

**大师**：“SEMI JOIN 就是 `WHERE EXISTS (subquery)` 的等效写法——只筛选左边表中在右边表有匹配的行，不附加右边表的列。ANTI JOIN 就是 `WHERE NOT EXISTS`——筛选左边表中在右边表没有匹配的行。这在做漏斗分析时特别有用：找出注册了但从未下单的用户，直接 ANTI JOIN 一张订单表，不用写子查询。”

**技术映射 #1**：ClickHouse 的 JOIN 在设计上假定大表 JOIN 小表（右侧表尽量小，能被广播到内存）。ALL JOIN 返回所有匹配行（笛卡尔积），ANY JOIN 只返回一条匹配（去重），SEMI/ANTI JOIN 只做存在性判断不取列。JOIN 顺序至关重要：永远把小表放右侧。

---

**小白**（一直在敲键盘做笔记，抬起头）：“大师，我昨天翻文档看到两个函数——`uniq` 和 `uniqExact`。文档说 `uniqExact` 是精确去重计数，`uniq` 是近似去重，那我为什么要用近似的？数据不是越精确越好吗？”

**大师**：“好问题！精确是好的，但精确有代价。`uniqExact` 需要把所有 distinct 值放到一个 HashSet 里，如果 UV 是 5000 万，这个 HashSet 就要占好几个 G 的内存。而 `uniq` 用的是 HyperLogLog 算法，不管数据量多大，它只用一个固定大小的内存块（大约 4KB），误差控制在 ±2% 以内。”

**小胖**：“那我算 UV 的时候写 `count(DISTINCT user_id)` 跟 `uniq(user_id)` 有什么区别？”

**大师**：“`count(DISTINCT)` 在 ClickHouse 底层也被优化了——对于低基数它会用精确算法，对于高基数它会自动切换为近似算法，默认阈值是 `count_distinct_implementation`。但完全不能跟 `uniq` 比。`uniq` 是一个**聚合状态合并函数**——在分布式查询中，每个分片算一个 HyperLogLog 状态，然后合并这些状态得到最终结果，数据传输量几乎为零。而 `count(DISTINCT user_id)` 在分布式表中会把所有唯一值发回发起节点做去重，网络开销巨大。总而言之：**看趋势用 uniq，看出账用 uniqExact**。日常大屏展示 UV，±2% 的误差完全可接受。”

**技术映射 #2**：`uniq` 系列是 ClickHouse 对精确度的工程取舍——牺牲 2% 精度换取 100 倍内存节省和网络传输量。`uniqExact` = 精确但吃内存，`uniqCombined` = 混合算法（基数低时精确，高时近似），`uniqHLL12` = 纯 HyperLogLog。按场景选用。

---

**小白**：“那我还有一个问题。我看到文档里有 PREWHERE，跟 WHERE 有什么区别？还有那个 FINAL 修饰符，不是用在 ReplacingMergeTree 上的吗？为什么有人说它是'性能杀手'？”

**大师**（拉过白板）：“这两个都是高级武器，用错了伤己不伤人。先说 PREWHERE——它是 ClickHouse 的一个查询优化器自动开启的特性。当你查询一个大宽表（50 列以上），WHERE 条件只涉及少数几列，ClickHouse 会自动把过滤条件提到 PREWHERE 阶段执行——在读取其他列之前先过滤掉不需要的行。但如果优化器判断失误，你也可以手动开：`PREWHERE event_type = 'click'`。它的本质是减少列读操作——最多能砍掉 90% 的 IO。”

"再说 FINAL——这是给 ReplacingMergeTree 和 CollapsingMergeTree 用的修饰符。正常情况下，这些引擎只在后台 Merge 时去重，查询时你可能会看到重复数据。加上 `FINAL`，查询时会强制合并所有 Part 后返回最终结果。但代价是——`FINAL` 会触发一次全量 Part 内存合并，CPU 打满，内存爆炸。生产环境加 `FINAL` 查询几千万行数据，能直接把节点搞 OOM。能用 ORDER BY + LIMIT 1 BY 替代的，就绝不用 FINAL。"

**小胖**：“那我用 SAMPLE 做抽样呢？听起来很轻量。”

**大师**：“SAMPLE 是轻量，但有前提条件——建表时必须指定 `SAMPLE BY` 表达式。`SAMPLE 0.1` 会随机抽取 10% 的数据做近似分析，对于探索性查询非常友好。但要注意：SAMPLE 的随机是 Part 级别的伪随机，可能不够均匀。如果是分布式表，SAMPLE 在每个分片上独立执行，要确保采样比例一致，否则各分片返回行数不均。”

**技术映射 #3**：PREWHERE = 提前过滤列（减少 IO）、FINAL = 强制去重（代价极高）、SAMPLE = 抽样加速（牺牲精度换速度）。三者都是 MergeTree 引擎专属特性，其他引擎无效。

---

## 3. 项目实战

### 环境准备

确保 Docker 环境已就绪（与第 2 章一致），启动 ClickHouse 单节点：

```bash
docker run -d --name ch08 \
  -p 8123:8123 -p 9000:9000 \
  clickhouse/clickhouse-server:25.4-alpine

docker exec -it ch08 clickhouse-client
```

### 分步实现

#### Step 1：创建用户行为表并导入模拟数据

首先创建 `user_actions` 主表——采用按天分区、按 `event_type` + `event_time` 排序的经典设计，同时为漏斗分析准备一张 `ad_campaigns` 广告触达表。

```sql
-- 用户行为主表
CREATE TABLE user_actions (
    user_id      UInt64,
    event_time   DateTime,
    event_type   LowCardinality(String),
    page         String,
    session_id   String,
    properties   Map(String, String)
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(event_time)
ORDER BY (event_type, event_time, user_id);

-- 广告触达表（用于 ASOF JOIN 演示）
CREATE TABLE ad_campaigns (
    user_id       UInt64,
    campaign_id   String,
    campaign_time DateTime
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(campaign_time)
ORDER BY (user_id, campaign_time);
```

插入 100 万行模拟用户行为数据——覆盖注册、浏览、点击、下单、复购五种事件类型，时间跨度 30 天。

```sql
INSERT INTO user_actions
SELECT
    number % 200000 + 1 AS user_id,
    toDateTime('2025-04-01 00:00:00') + (number % (30 * 24 * 60)) * 60 AS event_time,
    multiIf(
        number % 100 < 3, 'register',
        number % 10 < 5,  'page_view',
        number % 10 < 7,  'click',
        number % 20 < 3,  'first_order',
        'repeat_order'
    ) AS event_type,
    concat('/page/', toString(rand() % 500)) AS page,
    toString(cityHash64(number)) AS session_id,
    map('source', multiIf(rand() % 3 = 0, 'organic', rand() % 2 = 0, 'ad', 'referral'))
    AS properties
FROM numbers(1000000);

-- 广告触达数据：每个用户可能被 0-2 个广告触达
INSERT INTO ad_campaigns
SELECT
    user_id,
    concat('camp_', toString(rand() % 20 + 1)) AS campaign_id,
    toDateTime('2025-04-01 00:00:00') + rand() % (30 * 24 * 3600) AS campaign_time
FROM (
    SELECT number % 200000 + 1 AS user_id
    FROM numbers(200000)
    WHERE rand() % 100 < 30  -- 30% 用户被广告触达
);

SELECT count() AS total_rows FROM user_actions;
-- ┌─total_rows─┐
-- │    1000000  │
-- └─────────────┘
```

---

#### Step 2：用户留存分析——窗口函数 + CTE

增长团队最核心的需求：每日新增用户数及其次日/7 日/30 日留存率。用 CTE 增强可读性，用 LEFT ANY JOIN 避免行膨胀。

```sql
WITH
-- CTE 1：每个用户的首次访问日期
first_visit AS (
    SELECT
        user_id,
        min(toDate(event_time)) AS first_date
    FROM user_actions
    WHERE event_type = 'register'
    GROUP BY user_id
),
-- CTE 2：每日活跃用户
daily_active AS (
    SELECT
        user_id,
        toDate(event_time) AS active_date
    FROM user_actions
    GROUP BY user_id, active_date
)
SELECT
    f.first_date,
    count() AS new_users,
    countIf(d.active_date = f.first_date + 1)  AS day1_retained,
    countIf(d.active_date = f.first_date + 7)  AS day7_retained,
    countIf(d.active_date = f.first_date + 30) AS day30_retained,
    round(day1_retained * 100.0 / new_users, 2)  AS day1_rate,
    round(day7_retained * 100.0 / new_users, 2)  AS day7_rate,
    round(day30_retained * 100.0 / new_users, 2) AS day30_rate
FROM first_visit f
LEFT ANY JOIN daily_active d ON f.user_id = d.user_id
GROUP BY f.first_date
ORDER BY f.first_date
LIMIT 10;
```

> **踩坑提醒**：如果用了标准的 `LEFT JOIN`（即 `ALL JOIN`），每个用户在 `daily_active` 中有 N 天活跃记录，JOIN 后行数会膨胀 N 倍。`LEFT ANY JOIN` 保证每个左边行只匹配一条右边行——不过在留存计算中，我们实际需要多天匹配，所以上面的写法在逻辑上有局限性。更准确的做法是先按日期汇聚指标，再横向拼接，或者使用子查询聚合后 LEFT JOIN。上面的示例旨在展示 ANY JOIN 的语义——实际生产留存分析建议使用 `windowFunnel` 函数（见高级篇第 35 章）。

更好的留存计算方式——利用 ClickHouse 的 `retention` 聚合函数：

```sql
SELECT
    first_date,
    count() AS new_users,
    sum(ret[1]) AS day1_retained,
    sum(ret[2]) AS day7_retained,
    sum(ret[3]) AS day30_retained
FROM (
    SELECT
        user_id,
        min(toDate(event_time)) AS first_date,
        retention(
            toDate(event_time) = first_date + 1,
            toDate(event_time) = first_date + 7,
            toDate(event_time) = first_date + 30
        ) AS ret
    FROM user_actions
    WHERE event_type = 'register'
    GROUP BY user_id
)
GROUP BY first_date
ORDER BY first_date;
```

`retention()` 是 ClickHouse 内置的留存聚合函数，接受多个条件表达式，返回一个布尔数组——是否满足第 1/2/…N 天的留存条件。一行代码搞定了原本需要多次 JOIN + CASE WHEN 的复杂逻辑。

---

#### Step 3：Top 10 高活跃功能——窗口函数 `ROW_NUMBER()` + `RANK()`

运营想看在 **每个事件类型下** 访问最多的 Top 10 页面。用 `ROW_NUMBER()` 窗口函数做分组排名：

```sql
SELECT * FROM (
    SELECT
        event_type,
        page,
        count() AS cnt,
        ROW_NUMBER() OVER (PARTITION BY event_type ORDER BY cnt DESC) AS rn,
        RANK()       OVER (PARTITION BY event_type ORDER BY cnt DESC) AS rank_num
    FROM user_actions
    WHERE event_type IN ('page_view', 'click')
    GROUP BY event_type, page
)
WHERE rn <= 10
ORDER BY event_type, rn;
```

输出示例：

```
┌─event_type─┬─page───────┬───cnt─┬─rn─┬─rank_num─┐
│ click      │ /page/42   │  3850 │  1 │        1 │
│ click      │ /page/107  │  3842 │  2 │        2 │
│ click      │ /page/199  │  3831 │  3 │        3 │
│ page_view  │ /page/42   │  4001 │  1 │        1 │
│ page_view  │ /page/291  │  3997 │  2 │        2 │
└────────────┴────────────┴───────┴────┴───────────┘
```

`ROW_NUMBER()` 和 `RANK()` 的区别——当 cnt 相同时，`ROW_NUMBER()` 给不同序号（1, 2, 3），`RANK()` 给相同序号（1, 1, 3 跳过 2）。如果需要去重排序用 `DENSE_RANK()`。

> **踩坑提醒**：`PARTITION BY user_id` 在 1 亿用户上会创建 1 亿个分区窗口——每个窗口独立排序，内存和 CPU 急剧膨胀。窗口函数的 PARTITION BY 应当选择基数较低的列（如 `event_type` 基数 10 以内），高基数列建议用聚合函数替代。如果确实需要对每个用户做排名，考虑先 `GROUP BY user_id, xxx` 聚合后再窗口计算——减少分区数。

---

#### Step 4：漏斗转化率计算

从注册到首次付费到复购，每一步的转化率是多少？ClickHouse 有专用漏斗函数 `windowFunnel`，但这里先用基础 SQL 展示思路：

```sql
SELECT
    event_type,
    count(DISTINCT user_id) AS users,
    round(users / max(users) OVER (), 4) AS conversion_rate
FROM user_actions
WHERE event_type IN ('register', 'first_order', 'repeat_order')
  AND event_time >= '2025-04-01'
GROUP BY event_type
ORDER BY users DESC;
```

输出示例：

```
┌─event_type───┬──users─┬─conversion_rate─┐
│ register     │  67823 │               1 │
│ first_order  │  19854 │          0.2927 │  ← 约 29% 注册用户完成首单
│ repeat_order │   5012 │          0.0739 │  ← 约 7% 注册用户完成复购
└──────────────┴────────┴─────────────────┘
```

`max(users) OVER()` 是窗口函数的巧妙用法——在整个结果集上计算最大值，作为漏斗顶部的分母。如果想精确计算相邻步骤之间的转化率（不复用总分母），可以用 `lag()` 窗口函数：

```sql
SELECT
    event_type,
    users,
    round(users * 1.0 / lag(users) OVER (ORDER BY users DESC), 4) AS step_conversion
FROM (
    SELECT
        event_type,
        count(DISTINCT user_id) AS users
    FROM user_actions
    WHERE event_type IN ('register', 'first_order', 'repeat_order')
      AND event_time >= '2025-04-01'
    GROUP BY event_type
    ORDER BY users DESC
);
```

`lag(users)` 取上一行的值——所以每步转化率都是相对上一步的。`lag()` 和 `lead()` 常用于时间序列分析：环比增长、同比对比等场景。

---

#### Step 5：ARRAY JOIN 展开数组——嵌套数据结构扁平化

ClickHouse 原生支持 Array 列，分析时可以用 `ARRAY JOIN` 将数组展开为多行：

```sql
-- 构造包含数组列的测试数据
SELECT
    user_id,
    array_element,
    cnt
FROM (
    SELECT
        1 AS user_id,
        ['click', 'view', 'share'] AS events,
        [10, 25, 5] AS counts
    UNION ALL
    SELECT
        2 AS user_id,
        ['click', 'favorite'] AS events,
        [18, 3] AS counts
)
ARRAY JOIN events AS array_element, counts AS cnt;
```

输出：

```
┌─user_id─┬─array_element─┬─cnt─┐
│       1 │ click         │  10 │
│       1 │ view          │  25 │
│       1 │ share         │   5 │
│       2 │ click         │  18 │
│       2 │ favorite      │   3 │
└─────────┴───────────────┴─────┘
```

一行变三行——两组数组同时展开且一一对应。如果只展开 `events` 而不展开 `counts`，`cnt` 会取 count 数组的第一个元素。

> **真实场景**：你的 `properties` 列是 `Map(String, String)` 类型（键值对），可以用 `mapKeys()` 和 `mapValues()` 提取数组后用 `ARRAY JOIN` 展开。例如分析每个用户的所有属性来源分布：
>
> ```sql
> SELECT source, count() FROM (
>     SELECT mapValues(properties) AS vals
>     FROM user_actions
> ) ARRAY JOIN vals AS source
> GROUP BY source;
> ```

---

#### Step 6：ASOF JOIN——时间序列关联，事件归因分析

这是 ClickHouse 最具特色的 JOIN 类型。场景：每个用户的每次操作，关联到**最近的一次广告触达**——这是典型的"归因分析"需求。传统 SQL 需要复杂的子查询 + LIMIT 1，ASOF JOIN 一句话搞定：

```sql
SELECT
    a.user_id,
    a.event_time,
    a.event_type,
    a.page,
    b.campaign_id,
    b.campaign_time,
    dateDiff('minute', b.campaign_time, a.event_time) AS minutes_after_ad
FROM user_actions a
ASOF LEFT JOIN ad_campaigns b
    ON a.user_id = b.user_id
    AND a.event_time >= b.campaign_time
WHERE a.event_type IN ('first_order', 'repeat_order')
ORDER BY a.user_id, a.event_time
LIMIT 20;
```

ASOF JOIN 的匹配规则：对于左边每一行，找到右边表中满足 `user_id` 相等、`campaign_time <= event_time` 条件中 **时间差最小** 的那一行。这就是"事件归因"——这个订单究竟是哪次广告带来的。输出示例：

```
┌─user_id─┬─event_time──────────┬─event_type──┬─page──────┬─campaign_id─┬─campaign_time───────┬─minutes_after_ad─┐
│       1 │ 2025-04-01 02:14:00 │ first_order │ /page/42  │ camp_15     │ 2025-04-01 01:52:00 │               22 │
│       1 │ 2025-04-03 06:18:00 │ repeat_order│ /page/199 │ camp_15     │ 2025-04-01 01:52:00 │             3026 │
│       2 │ 2025-04-02 11:32:00 │ first_order │ /page/107 │ camp_3      │ 2025-04-02 10:15:00 │               77 │
└─────────┴─────────────────────┴─────────────┴───────────┴─────────────┴─────────────────────┴──────────────────┘
```

ASOF JOIN 的实现原理不同于普通 JOIN——它要求右边表按 `(user_id, campaign_time)` 升序排列，然后在排序数据上做二分查找。所以 ASOF JOIN 的效率非常高，时间复杂度 O(n log m)，远优于子查询的 O(n×m)。

> **踩坑提醒**：ASOF JOIN 要求匹配键列（如 `user_id, campaign_time`）在右边表的 ORDER BY 中必须是最左前缀，否则会退化为全表扫描。使用前先执行 `OPTIMIZE TABLE ad_campaigns FINAL` 确保数据已排序合并。

---

### 测试验证：PREWHERE 性能对比

这是验证 ClickHouse SQL 优化效果的关键实验——对比同一查询在 PREWHERE 和 WHERE 下的扫描数据量差异。

```sql
-- 先用 EXPLAIN 看两种方式的扫描计划
SET send_logs_level = 'trace';

-- 方式1：标准 WHERE
SELECT count(), avg(length(page))
FROM user_actions
WHERE event_type = 'click'
  AND event_time >= '2025-04-15';

-- 方式2：手动指定 PREWHERE
SELECT count(), avg(length(page))
FROM user_actions
PREWHERE event_type = 'click'
WHERE event_time >= '2025-04-15';
```

在 trace 日志中观察两类指标：`Selected N parts` 和 `Read N rows`。PREWHERE 版本在读取 `page` 列之前先按 `event_type` 过滤，因此实际加载到内存的 `page` 列行数远小于 WHERE 版本。差异在宽表（50+ 列）上尤为明显——WHERE 需要把所有列都读进内存再过滤，PREWHERE 先读过滤列再按需读取其他列。

另一个对照实验——SAMPLE 抽样的效果：

```sql
-- 标准全量查询
SELECT event_type, count() FROM user_actions GROUP BY event_type;
-- 耗时 ~0.15s

-- 10% 抽样查询（需建表时指定 SAMPLE BY）
-- 不适用于本例（本表未指定 SAMPLE BY），此处仅做语法展示
-- SELECT event_type, count() * 10 FROM user_actions SAMPLE 0.1 GROUP BY event_type;
-- 耗时 ~0.02s，误差 ±5%
```

---

## 4. 项目总结

### ClickHouse SQL 独有特性速查表

| 特性 | 说明 | 适用场景 | 注意事项 |
|------|------|---------|---------|
| **ASOF JOIN** | 时间序列就近匹配 | 事件归因、传感器数据对齐、行情快照关联 | 右边表必须按匹配键排序 |
| **PREWHERE** | 预过滤减少列读取 | 大宽表（50+列）+ 少数列过滤条件 | 仅 MergeTree 引擎有效，优化器通常自动开启 |
| **SAMPLE** | 随机抽样加速查询 | 数据探索、趋势初步验证、实时大屏近似值 | 建表时需指定 `SAMPLE BY`，分布式表采样需注意一致性 |
| **ARRAY JOIN** | 数组列扁平化为多行 | 埋点参数列表分析、标签数组展开、嵌套数据查询 | 多个数组同时展开时要保证长度一致 |
| **SEMI/ANTI JOIN** | 存在性判断，不取右表列 | 漏斗筛选（注册未下单用户、已下单未复购用户） | 比 `IN` 子查询性能更好 |
| **FINAL** | 强制合并去重 | 紧急数据校验、手动补全合并 | 极度消耗资源，生产谨慎使用 |
| **retention()** | 内置留存聚合函数 | 用户留存分析 | 相比 JOIN 方式大幅简化 SQL |
| **uniq/uniqExact** | 近似/精确去重计数 | uniq: 大屏趋势；uniqExact: 出账报表 | uniq 误差 ±2%，内存仅 4KB；uniqExact 精确但吃内存 |

### ClickHouse 专门聚合函数一览

| 函数 | 用途 | 示例 |
|------|------|------|
| `uniq(x)` | 近似去重计数（HyperLogLog） | `SELECT uniq(user_id) FROM events` |
| `uniqCombined(x)` | 混合去重（低基数精确，高基数近似） | `SELECT uniqCombined(user_id)` |
| `uniqExact(x)` | 精确去重计数（HashSet） | `SELECT uniqExact(user_id)` |
| `groupArray(x)` | 将分组内所有值收集为数组 | `SELECT groupArray(page)` |
| `argMin(x, y)` | 返回 y 最小时对应的 x 值 | `SELECT argMin(page, event_time) AS first_page` |
| `argMax(x, y)` | 返回 y 最大时对应的 x 值 | `SELECT argMax(page, event_time) AS last_page` |
| `quantile(0.95)(x)` | 近似分位数 | `SELECT quantile(0.95)(response_time) AS p95` |
| `quantileExact(0.5)(x)` | 精确中位数 | `SELECT quantileExact(0.5)(amount)` |
| `windowFunnel(window)(time, cond1, cond2, ...)` | 漏斗分析（滑动窗口内条件匹配） | 见高级篇第 35 章 |
| `retention(cond1, cond2, ...)` | 留存分析 | `SELECT retention(day1, day7, day30)` |

### 适用场景

- **用户行为分析**：留存、漏斗、路径分析——`retention()`、`windowFunnel()`、`sequenceMatch()` 三大函数是核武器。
- **实时数据看板**：按分钟/秒聚合，`uniq` 近似去重保证毫秒级响应。
- **事件归因分析**：广告→点击→付费链路，`ASOF JOIN` 精准匹配最近触点。
- **日志分析**：Nginx 访问日志、App 埋点日志，`PREWHERE` + 分区裁剪 + `quantile` 分位数。
- **时序数据对齐**：IoT 传感器数据、金融行情数据的时间戳匹配。

### 不适用场景

- **OLTP 事务处理**：ClickHouse 不支持垮行事务，频繁的单行 UPDATE/DELETE 是灾难。
- **需要实时一致性的场景**：MergeTree 的去重是最终一致性，查询可能看到中间状态的重复数据。

### 常见踩坑经验

**坑1：大表 JOIN 大表导致内存溢出**

某团队的留存分析脚本把两张 5 亿行的表做 `LEFT JOIN`，右侧表没有被广播到内存而是在磁盘上做 Grace Hash Join，查询跑了 40 分钟后 OOM。**解决方案**：先对两张表按 `user_id` 做预聚合，把 5 亿行压缩到 2000 万行再做 JOIN；或者把右表做成字典（Dictionary），通过 `dictGet` 做实时关联。

**坑2：窗口函数 PARTITION BY 高基数列**

某个排行榜查询：`ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY score DESC)`——1 亿用户、1 亿个分区窗口，每个窗口只有 1-2 行数据。ClickHouse 为此创建了 1 亿个排序任务，内存瞬间飙到 120GB。**解决方案**：先做 `GROUP BY user_id` 聚合（保留 Top N 数据），再对聚合后的小数据集做窗口计算。

**坑3：FINAL 导致查询卡死**

DBA 为了"查最新数据"，在所有查询里加了 `FINAL`。某次大促期间，一个带 `FINAL` 的分析查询在 3 亿行的 ReplacingMergeTree 表上跑了 18 分钟，期间 Merge 线程全部被阻塞，写入延迟暴增到 30 分钟。**解决方案**：用 `GROUP BY + argMax` 替代 FINAL，或者建表时直接用 `ReplacingMergeTree(version)` + 业务版本号，使去重逻辑在 Merge 阶段自动完成。

### 思考题

1. **ASOF JOIN 和普通 LEFT JOIN 在实现上有什么本质区别？为什么 ASOF JOIN 能做时间序列关联而普通 JOIN 不能？**
   > 提示：考虑排序 + 二分查找 vs 哈希表全量匹配的算法差异。ASOF JOIN 的 `ON` 条件中 `>=` 或 `<=` 的语义如何实现？

2. **为什么 ClickHouse 推荐用 `uniq` 代替 `count(DISTINCT)`？`uniq` 的误差有多大，这个误差在什么场景下不可接受？**
   > 提示：HyperLogLog 算法原理——LogLog 估计、调和平均数、偏差修正。`uniqCombined` 和 `uniqExact` 分别在什么场景下更合适？

---

> **下一章预告**：第 9 章《物化视图基础——从 ETL 到 ELT》——把计算量前置，让查询飞起来。教你用物化视图实现秒级聚合、级联汇总和实时看板。
