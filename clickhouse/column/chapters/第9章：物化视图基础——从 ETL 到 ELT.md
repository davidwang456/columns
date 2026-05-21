# 第9章：物化视图基础——从 ETL 到 ELT

> **版本**：ClickHouse 25.x LTS
> **定位**：进阶篇核心章节。理解物化视图的 INSERT 触发机制，掌握 SummingMergeTree 预聚合设计模式。
> **前置阅读**：第4章（MergeTree 家族概览）、第5章（分区与排序键）
> **预计阅读**：35 分钟 | **实战耗时**：60 分钟

---

## 1. 项目背景

凌晨两点，某电商公司运营部的大屏突然卡死了。十块监控屏幕上的 GMV 曲线齐齐停在原地，像被点了穴。值班同事小胖被电话炸醒，睡眼惺忪地打开电脑一看——集群 CPU 飙到 95%，上面挂着二十几个同样的查询：

```sql
SELECT
    toStartOfHour(created_at) AS hour,
    sum(amount)
FROM orders
WHERE created_at >= today() - 30
GROUP BY hour
```

这 SQL 看起来人畜无害对吧？但 `orders` 表有 **5 亿行**，每个查询都要把三十天的数据全扫一遍，然后按小时聚合。运营部 10 个同事同时刷新看板，等于 10 个 `full scan + group by` 在上面硬扛。单次查询 15 秒，十个人并发就是灾难级雪崩。更要命的是一到早高峰，运营、市场、客服三个部门大约 30 人同时打开看板，ClickHouse 直接跪地求饶。

小胖一脸绝望："这不就是查个每日 GMV 吗？MySQL 时代也跑这个查询啊，怎么到了 ClickHouse 反而撑不住了？"

大师在他背后看了看屏幕："不是 ClickHouse 撑不住，是你在让 ClickHouse 干重复劳动。同样的三张表、同样的聚合逻辑，每刷一次看板就全量扫一次，十万行也好、五亿行也好——**没有缓存，没有预计算，每次查询都是重新造轮子**。"

小胖挠头："那咋办？加索引？加缓存？"

大师摇头："治标不治本。真正的问题是架构——你把 ClickHouse 当成 MySQL 在用，暴力全表扫描。列存的扫描速度再快也经不住几十人这么轮。不是磁盘不够快，是 CPU 要被重复的 GROUP BY 烧穿了。我们需要的是 **预聚合（Pre-Aggregation）**——把计算结果提前算好，查的时候直接拿。"

小白推了推眼镜："这不就是报表工程师的老本行嘛——晚上跑 ETL 任务，把聚合结果写进一张汇总表？"

大师："接近了。但传统的 ETL 有三个致命缺陷：第一，它依赖外部调度工具，链路复杂；第二，它是定时跑批，有延迟——比如每小时跑一次，那大盘上的数据就滞后一小时；第三，数据量一大，定时 ETL 很容易积压、超时、中断。而 ClickHouse 给了一个更优雅的方案——**物化视图（Materialized View）**。它把 ETL 变成了 ELT——Extract、Load、然后 **Transform 在引擎内部自动完成**。"

这场关于物化视图的深度讨论，就此展开。

---

## 2. 项目设计：剧本式交锋对话

**Scene**：凌晨三点的运维办公室，三台显示器亮着，小胖面前摆着熬夜标配——红牛和泡面。

**小胖**（放下泡面叉子）："物化视图……这不就是多建一张表吗？直接把聚合结果写进去不就完事了，非要用什么视图？多一张表浪费磁盘不说，我还得自己写脚本往里灌数据，多麻烦啊。"

**大师**（接过小胖的键盘）："你思路对了，但方向错了。手工维护聚合表有两个致命问题。第一，**数据一致性问题**——你写了一个 Python 脚本，每分钟跑一次，从 `orders` 表 `SELECT ... GROUP BY` 然后 `INSERT INTO summary`。但在两次脚本执行之间，新写入 `orders` 的数据就查不到，看板上的数字会比实际少一个窗口期。第二，**幂等性和重复计算问题**——你脚本挂了、断网了、重试了，怎么保证不重复写数据？你是不是还得在代码里傻傻地加个去重逻辑？"

**小胖**："呃……好像是有点麻烦。"

**小白**："大师，你说的这个物化视图到底是怎么保证数据一致性的？源表来一条，MV 就插一条？这跟 MySQL 的触发器（Trigger）是不是一回事？"

**大师**："悟性不错。ClickHouse 的物化视图，本质上就是一个 **INSERT 触发器**。你把数据 `INSERT INTO orders_src` 的这个动作一旦完成，ClickHouse 内核会自动把新写入的这批 Block 扔给物化视图的 `SELECT` 语句去算一遍，算出聚合结果后 `INSERT INTO` 目标表。整个过程原子化——源表写入成功，MV 就一定有对应的聚合行，不需要你写任何额外代码。"

**技术映射**：物化视图 ≠ 普通视图（VIEW）。普通视图只是一个存储的 SQL 查询别名，每次查询时再执行一次——不存数据、不算结果、零加速效果。物化视图则相反：**它把查询的结果物理存储在磁盘上**，以后查这个结果直接读磁盘，不需要重新扫描源表。四个字概括：**空间换时间**。

---

**小白**："等一下，我捋一捋。INSERT 到源表 → 触发 MV 的 SELECT → 生成聚合行 → 写入目标表。那如果这个 MV 一多，源表的 INSERT 不就越来越慢了？而且并发写入的时候，MV 能保证数据不丢不重吗？"

**大师**："问得好。这是物化视图最核心的设计权衡。首先说性能——每建一个 MV，相当于源表的每次 INSERT 要多跑一次额外的聚合计算。一个 MV 大约增加 3%~8% 的 INSERT 开销，具体取决于聚合复杂度。建三四个 MV 还行，建十个以上就要掂量一下了。不过这种开销换来的收益是巨大的——一个查询从 15 秒变成 0.01 秒，这条账怎么算都划算。"

**小胖**："那一致性呢？我刚插了 10 万行进去，MV 里立刻就能查到吗？"

**大师**："严格来说，在当前 INSERT 事务完成后，MV 的数据对后续查询是可读的。但注意一个细节——ClickHouse 的 MV 是 **每个 INSERT Block 粒度触发** 的，不是逐行触发。比如你 `INSERT INTO orders_src SELECT * FROM file(...)` 插入了 10 万行，ClickHouse 会把这 10 万行分成若干个 Block，每个 Block 经过 MV 的 GROUP BY 生成一批聚合行，写入目标表。同一个 INSERT 的多个 Block 之间没有事务保证，但它们都会在 INSERT 返回前完成。所以最终一致性是保证的，准确性也不会丢。"

**小白**："明白了。但还有个问题——我在创建物化视图的时候，源表里已经有 5 亿历史数据了，MV 会自动把这些历史数据也聚合进去吗？"

**大师**："默认不会。这就引出了 `POPULATE` 关键字。如果你在建 MV 时不加 `POPULATE`，MV 只会从创建之后新写入的数据开始触发。历史数据它一点儿也不管。如果你加了 `POPULATE`，ClickHouse 会在创建 MV 时**同步扫描源表的所有现有数据**，算完聚合后一口气写进目标表。注意，POPULATE 会阻塞当前连接，5 亿行可能跑几分钟，记得挑业务低峰期操作。"

---

**小胖**："好，现在数据能自动聚合进来了。但我还有一个场景——能不能让 MV 去做跨表 JOIN 聚合？比如订单表 JOIN 用户表，按用户等级统计 GMV？"

**大师**："这里必须敲黑板——**在 25.x 版本中，ClickHouse 物化视图不支持 JOIN 操作**。准确的说是，你可以在 MV 的 SELECT 里写 JOIN，但 JOIN 的右边那张表（比如用户表）不会被监听，数据变更不会触发 MV 重算。也就是说 JOIN 只在创建 MV 的那个时刻执行一次，后面用户表改了，MV 也不知道。"

**小白**："那如果我真要做跨表聚合怎么办？"

**大师**："两个方法。第一，**预 JOIN 到源表**——在数据写入 `orders_src` 之前，就先在 ETL 层把用户等级 JOIN 进来，放到源表的一个列里。这样 MV 只需要做单表聚合。第二，**字典（Dictionary）**——把用户表挂载为 ClickHouse 的字典，MV 的 SELECT 里用 `dictGet()` 函数实时查询字典，这样既能获取最新的用户等级，又能利用字典的内存缓存加速。"

**小胖**："第二个方法听起来很骚，但字典数据能不能正确触发 MV？"

**大师**："能。`dictGet()` 是一个纯读操作，每次 INSERT 触发 MV 时都会调用一次字典，拿到当前最新的维度值。所以源表写入了新订单，MV 聚合时就能拿到最新的用户等级。但性能开销要注意——每行数据都要调一次字典，字典大了会有延迟。"

---

**小胖**："那目标表怎么建呢？MV 只能往一张表里写，这张表用什么引擎？"

**大师**："目标表引擎的选择决定了 MV 的价值。如果你用普通的 MergeTree，聚合结果会原封不动地往里写——同一条数据的多次更新会产生多行，你需要自己负责去重逻辑。但 ClickHouse 为物化视图场景专门设计了一个神器——**SummingMergeTree**。"

**小白**："SummingMergeTree 有什么特别的？"

**大师**："MergeTree 存的是明细行，SummingMergeTree 存的是 **可合并的预聚合行**。想象一下——你的订单每小时都在进来，每 INSERT 一次，MV 就生成一行 `(hour=10:00, status='paid', count=50, amount=32000)`。十分钟后又来一批新订单，MV 又生成一行 `(hour=10:00, status='paid', count=30, amount=18000)`。如果是普通 MergeTree，目标表里现在有两行。查的时候你必须再 `GROUP BY hour, status` 一下，把两行加在一起。"

**小胖**："那不等于没有预聚合吗？"

**大师**："所以要用 SummingMergeTree。它的 ORDER BY 就是合并键。后台 Merge 过程会自动把 `ORDER BY` 列相同的多行合并成一行——`count` 和 `amount` 加起来，变成 `(hour=10:00, status='paid', count=80, amount=50000)`。这样目标表里的行数始终保持最小，查询时直接 SELECT 就能拿到最终结果。"

**小白**："那是不是查 SummingMergeTree 的时候一定要带 `FINAL` 关键字？"

**大师**："一半对。`FINAL` 会强制 ClickHouse 在查询时做一次额外的合并，保证结果是精确的。但如果你能接受微小的不精确（比如后台 Merge 还在进行中），也可以不加 FINAL，直接查。对于实时看板这种场景，差几十条订单对大盘曲线的影响基本可以忽略。追求绝对精确的话就带 `FINAL`，追求极限性能就不带。**工具没有绝对的正确答案，只有最适合自己场景的取舍。**"

---

**小胖**："最后一个问题——TO 子句是什么鬼？我看很多文档里都不写，到底什么时候需要？"

**大师**："`TO` 子句让你自己指定目标表的名称和结构，而不是让 ClickHouse 自动创建一张匿名内部表。如果没有 `TO`，ClickHouse 会以 `.inner.` 前缀的内部表名存储数据，你在 `system.tables` 里能看到。加了 `TO` 的话，你自己控制表名、引擎、分区键、排序键、TTL——给了你完全的控制权。推荐始终使用 `TO` 子句，原因很简单：**显式优于隐式**。你可以单独对这目标表做 OPTIMIZE、备份、迁移、加 TTL。内部匿名表你想操作它？对不起，表名是一串 hash，你连名字都记不住。"

**小白**："所以最佳实践是——源表 MergeTree + MV + 目标表 SummingMergeTree，三者各司其职，MV 作为数据管线自动串联？"

**大师**："正是。这就是 ClickHouse 的 ELT 范式：**Extract（源表写入）→ Load（落盘）→ Transform（MV 自动计算，目标表聚拢）**。以前你需要 Airflow + Python + 定时任务才能完成的事，现在全收进 ClickHouse 内部，一条 SQL 搞定。"

---

## 3. 项目实战

### 环境准备

拉起 ClickHouse 实例：

```bash
docker run -d --name ch-mv-lab \
  -p 8123:8123 -p 9000:9000 \
  clickhouse/clickhouse-server:25.3
```

进入客户端：

```bash
docker exec -it ch-mv-lab clickhouse-client
```

验证环境：

```sql
SELECT version();
-- 输出应类似：25.3.x.x
```

---

### Step 1：创建源表与物化视图

首先建源表，模拟电商订单流水：

```sql
-- 源表：订单明细表
CREATE TABLE orders_src (
    order_id   UInt64,
    user_id    UInt32,
    amount     Decimal(10, 2),
    status     LowCardinality(String),
    created_at DateTime
) ENGINE = MergeTree()
ORDER BY (created_at, order_id)
PARTITION BY toYYYYMM(created_at);
```

关键设计点：
- `ORDER BY (created_at, order_id)`：按时间+订单号排序，让按时间范围的查询能最大利用主键索引。
- `PARTITION BY toYYYYMM(created_at)`：按月分区，方便按月清理过期数据。
- `status` 使用 `LowCardinality(String)`：因为订单状态只有几种（paid、pending、refunded 等），降低存储开销。

然后创建第一个物化视图——按小时聚合：

```sql
-- 物化视图：小时级聚合
CREATE MATERIALIZED VIEW orders_hourly_mv
ENGINE = SummingMergeTree()
ORDER BY (hour, status)
PARTITION BY toYYYYMM(hour)
AS SELECT
    toStartOfHour(created_at) AS hour,
    status,
    count()                   AS order_count,
    sum(amount)               AS total_amount
FROM orders_src
GROUP BY hour, status;
```

解读：
- `ENGINE = SummingMergeTree()`：目标引擎选择 SummingMergeTree，自动合并同键的行。
- `ORDER BY (hour, status)`：合并键 = 小时 + 状态；每次 Merge 后，同一小时同一状态的所有订单合并为一行。
- `toStartOfHour()`：ClickHouse 内置时间取整函数，把 `2025-03-15 14:37:12` 截断为 `2025-03-15 14:00:00`。
- `count()` 和 `sum(amount)`：SummingMergeTree 默认会对所有数值列求和——`order_count` 和 `total_amount` 在合并时自动累加。

验证 MV 已创建：

```sql
SELECT
    database,
    name,
    engine,
    total_rows
FROM system.tables
WHERE name LIKE '%orders%'
FORMAT PrettyCompact;
```

你应该能看到 `orders_src`（源表，MergeTree）和 `orders_hourly_mv`（MV 目标表，SummingMergeTree）两条记录。

---

### Step 2：插入数据并验证 MV 自动更新

生成并插入 10 万行模拟订单：

```sql
INSERT INTO orders_src
SELECT
    rowNumberInAllBlocks()                                AS order_id,
    rand(1) % 10000 + 1                                   AS user_id,
    toDecimal64(round(rand(2) % 100000 / 100, 2), 2)     AS amount,
    ['paid', 'pending', 'refunded', 'shipped'][rand(3) % 4 + 1] AS status,
    now() - INTERVAL rand(4) % 2592000 SECOND             AS created_at
FROM numbers(100000);
```

这行 SQL 用 `numbers(100000)` 生成 10 万行，每行随机分配用户、金额、订单状态和过去 30 天内的时间戳。

验证数据量：

```sql
SELECT
    'Source' AS name,
    count()  AS rows
FROM orders_src
UNION ALL
SELECT
    'MV'      AS name,
    sum(order_count) AS rows
FROM orders_hourly_mv;

-- 期望输出：
-- Source    500000  (100000)
-- MV        500000  (100000)
```

两边的总订单数应该一致。如果 MV 总订单数少于源表，检查是否有数据在 `INSERT` 之前就已存在（那需要 POPULATE）。

查看 MV 内部数据结构：

```sql
SELECT
    hour,
    status,
    order_count,
    total_amount
FROM orders_hourly_mv
ORDER BY hour DESC
LIMIT 20;
```

你应该能看到按小时+状态分组的聚合行，比如 `2025-03-15 14:00:00 | paid | 1234 | 56789.00`。

```sql
-- 同样逻辑直接查源表，对比结果
SELECT
    toStartOfHour(created_at) AS hour,
    status,
    count()                   AS order_count,
    sum(amount)               AS total_amount
FROM orders_src
GROUP BY hour, status
ORDER BY hour DESC
LIMIT 20;
```

两个查询的结果应该完全一致——这就是 MV 的正确性证明。

---

### Step 3：创建级联 MV（多粒度聚合）

现实场景中，看板通常需要多种粒度的数据——实时看板看小时级，日报看天级，周报看周级。单层 MV 只能解决一个粒度，这时就需要 **级联物化视图**。

```sql
-- 第二层 MV：天级聚合（从小时级 MV 中读取）
CREATE MATERIALIZED VIEW orders_daily_mv
ENGINE = SummingMergeTree()
ORDER BY (day, status)
PARTITION BY toYYYYMM(day)
AS SELECT
    toStartOfDay(hour) AS day,
    status,
    sum(order_count)   AS order_count,
    sum(total_amount)  AS total_amount
FROM orders_hourly_mv
GROUP BY day, status;
```

这里的关键点：第二层 MV 的源表是 `orders_hourly_mv`（第一层 MV 的目标表，一张 SummingMergeTree）。当 `orders_hourly_mv` 里有新行写入（由第一层 MV 自动触发生成的），第二层 MV 就会自动按天再次聚合。

级联链路如下：

```
orders_src (MergeTree, 源表)
    │  INSERT 触发
    ▼
orders_hourly_mv (SummingMergeTree, 小时聚合)
    │  INSERT 触发
    ▼
orders_daily_mv (SummingMergeTree, 天聚合)
```

每一层都是自动触发，无需任何外部调度。

继续验证数据一致性——插入更多数据后检查级联链路：

```sql
-- 再插入 5 万行
INSERT INTO orders_src
SELECT
    rowNumberInAllBlocks() + 100000,
    rand(5) % 10000 + 1,
    toDecimal64(round(rand(6) % 100000 / 100, 2), 2),
    ['paid', 'pending', 'refunded', 'shipped'][rand(7) % 4 + 1],
    now() - INTERVAL rand(8) % 2592000 SECOND
FROM numbers(50000);

-- 三层数据行数比对
SELECT 'src' AS layer, count() FROM orders_src
UNION ALL
SELECT 'hourly_mv', sum(order_count) FROM orders_hourly_mv
UNION ALL
SELECT 'daily_mv', sum(order_count) FROM orders_daily_mv;

-- 期望：三层行数一致（均等于 150000）
```

注意：`orders_daily_mv` 的聚合结果行数会比小时级少（因为按天粒度更粗），但 `sum(order_count)` 应该一致。

---

### Step 4：性能对比——MV 究竟快了多少？

这是最燃的环节。我们手动构造一个大数据量场景来压测：

```sql
-- 先给 orders_src 灌入 500 万行（模拟真实生产量级）
INSERT INTO orders_src
SELECT
    rowNumberInAllBlocks() + 200000,
    rand(9) % 100000 + 1,
    toDecimal64(round(rand(10) % 100000 / 100, 2), 2),
    ['paid', 'pending', 'refunded', 'shipped'][rand(11) % 4 + 1],
    toDateTime('2025-01-01 00:00:00') + INTERVAL rand(12) % 15552000 SECOND
FROM numbers(5000000);
```

等待 MV 自动完成聚合（通常几秒到几十秒，取决于机器性能）。

然后做性能对比。先查源表（全量扫描）：

```sql
-- 查询最近 60 天的数据
SELECT
    status,
    count()    AS cnt,
    sum(amount) AS amt
FROM orders_src
WHERE created_at >= toDateTime('2025-03-01 00:00:00')
GROUP BY status
FORMAT Null;  -- FORMAT Null 只计时不输出数据，用于性能测试

-- 记录耗时，例如：
-- Elapsed: 0.852 sec
```

再查物化视图：

```sql
-- 等价逻辑查小时级 MV
SELECT
    status,
    sum(order_count)  AS cnt,
    sum(total_amount) AS amt
FROM orders_hourly_mv
WHERE hour >= toDateTime('2025-03-01 00:00:00')
GROUP BY status
FORMAT Null;

-- 记录耗时，例如：
-- Elapsed: 0.008 sec
```

典型结果对比：

| 数据量 | 源表查询 | MV 查询 | 加速比 |
|--------|----------|---------|--------|
| 500 万行 | 0.85s | 0.008s | **106x** |
| 5000 万行 | 4.2s | 0.012s | **350x** |
| 5 亿行 | 15.6s | 0.015s | **1040x** |

数据量越大，加速比越夸张。因为源表查询的时间随数据量线性增长（O(n)），而 MV 查询只扫描预聚合行——行数是按小时×状态的数量级，约几万到几十万行，几乎不随源表增长而增大。

```sql
-- 看下 MV 内部行数 vs 源表行数
SELECT
    (SELECT count() FROM orders_src)          AS src_rows,
    (SELECT count() FROM orders_hourly_mv)    AS mv_rows,
    round(mv_rows / src_rows * 100, 2)        AS compression_ratio;
```

对于 500 万行源数据、30 天 × 24 小时 × 4 种状态 ≈ 2880 种组合，MV 行数压缩比高达 **99.94%**。

---

### Step 5：MV 运维操作

**查看 MV 状态：**

```sql
SELECT
    database,
    name,
    engine,
    total_rows,
    total_bytes,
    formatReadableSize(total_bytes) AS size
FROM system.tables
WHERE name LIKE '%orders%'
ORDER BY name;
```

**暂停/恢复物化视图：**

```sql
-- 停止接收新数据（源表 INSERT 不再触发此 MV）
SYSTEM STOP VIEW orders_hourly_mv;

-- 恢复
SYSTEM START VIEW orders_hourly_mv;
```

业务场景：在做源表的大批量导入时（比如历史数据补录），暂时停掉 MV 可以加速导入，导入完成后再恢复并手动回填。

**分离/挂载物化视图：**

```sql
-- 分离（从 ClickHouse 注册表中移除，但不删除数据文件）
DETACH TABLE orders_hourly_mv;

-- 重新挂载
ATTACH MATERIALIZED VIEW orders_hourly_mv
ENGINE = SummingMergeTree()
ORDER BY (hour, status)
PARTITION BY toYYYYMM(hour)
AS SELECT
    toStartOfHour(created_at) AS hour,
    status,
    count()                   AS order_count,
    sum(amount)               AS total_amount
FROM orders_src
GROUP BY hour, status;
```

**删除物化视图（注意顺序）：**

```sql
-- 先删除 MV 定义
DROP VIEW orders_hourly_mv;

-- 如果目标表是由 MV 自动创建的内部表（`.inner.` 前缀），删 MV 会自动级联删除目标表。
-- 如果目标表通过 TO 子句显式指定，删 MV 不会删目标表——需要手动 DROP。
```

---

### Step 6：POPULATE 使用场景

假设你接手了一个遗留系统——`orders_src` 已有 3 亿历史数据，现在需要新建一个 MV 来做实时看板：

```sql
-- 创建带 POPULATE 的物化视图
CREATE MATERIALIZED VIEW orders_hourly_mv_v2
ENGINE = SummingMergeTree()
ORDER BY (hour, status)
PARTITION BY toYYYYMM(hour)
POPULATE
AS SELECT
    toStartOfHour(created_at) AS hour,
    status,
    count()                   AS order_count,
    sum(amount)               AS total_amount
FROM orders_src
WHERE created_at >= toDateTime('2025-01-01 00:00:00')
GROUP BY hour, status;
```

`POPULATE` 会阻塞当前会话，扫描 `orders_src` 中所有 `created_at >= '2025-01-01'` 的数据并填充到 MV。3 亿行全量扫描 + 聚合，根据机器性能可能耗时几分钟到十几分钟。完成后，新的 INSERT 自动触发增量更新。

如果不加 POPULATE，MV 初始为空，只从创建后新写入的数据开始累计——那么最近一个月的数据将完全缺失。

**POPULATE 的局限**：
- 阻塞执行（无法并发写入）；
- 历史数据量大时耗时较长；
- 如果源表有写入倾斜（某些分区特别大），可能 OOM。

生产环境的稳妥做法是：先不加 POPULATE 创建 MV，然后手动执行 `INSERT INTO orders_hourly_mv SELECT ... FROM orders_src WHERE ... GROUP BY ...`，这样可以分批执行、控制批次大小、监控进度。

---

### 测试验证——完整流程演习

重新整理一遍端到端验证流程，确保你对 MV 的每个环节都有体感：

```sql
-- 1. 清空源表重新来
TRUNCATE TABLE orders_src;
-- MV 会自动跟着清空（因为目标表的行都来自源表 INSERT）

-- 2. 确认所有表为空
SELECT count() FROM orders_src;          -- 0
SELECT count() FROM orders_hourly_mv;    -- 0
SELECT count() FROM orders_daily_mv;     -- 0

-- 3. 插入一批数据
INSERT INTO orders_src VALUES
    (1, 1001, 99.90, 'paid',    '2025-03-15 14:23:00'),
    (2, 1002, 199.00, 'paid',   '2025-03-15 14:45:00'),
    (3, 1003, 50.00, 'pending', '2025-03-15 14:50:00'),
    (4, 1004, 299.00, 'paid',   '2025-03-15 15:10:00'),
    (5, 1005, 149.00, 'shipped', '2025-03-15 15:30:00');

-- 4. 立即查询 MV（无需等待）
SELECT hour, status, order_count, total_amount
FROM orders_hourly_mv
ORDER BY hour, status;

-- 输出应为：
-- 2025-03-15 14:00:00 | paid     | 2 | 298.90
-- 2025-03-15 14:00:00 | pending  | 1 | 50.00
-- 2025-03-15 15:00:00 | paid     | 1 | 299.00
-- 2025-03-15 15:00:00 | shipped  | 1 | 149.00

-- 5. 再插一笔同小时同状态（验证 SummingMergeTree 合并能力）
INSERT INTO orders_src VALUES
    (6, 1006, 88.00, 'paid', '2025-03-15 14:30:00');

-- 6. 查 MV（不触发 Merge 时看到两行 paid）
SELECT hour, status, order_count, total_amount
FROM orders_hourly_mv
WHERE hour = '2025-03-15 14:00:00' AND status = 'paid';

-- 可能是两行：
-- 2025-03-15 14:00:00 | paid | 2 | 298.90
-- 2025-03-15 14:00:00 | paid | 1 | 88.00

-- 7. 手动触发 Merge（或等待后台自动 Merge）
OPTIMIZE TABLE orders_hourly_mv FINAL;

-- 8. 再次查询，两行已合并为一行
SELECT hour, status, order_count, total_amount
FROM orders_hourly_mv
WHERE hour = '2025-03-15 14:00:00' AND status = 'paid';

-- 输出：
-- 2025-03-15 14:00:00 | paid | 3 | 386.90
```

踩坑提醒：如果你第 6 步查到的 sum 不对，大概率是因为没加 `FINAL` 关键词——SummingMergeTree 的合并不是实时的，想要精确结果就加 `FINAL`。

---

## 4. 项目总结

### MV vs 普通查询对比表

| 指标 | 直接查源表（Raw Query） | 物化视图（Materialized View） |
|------|------------------------|-------------------------------|
| 5亿行聚合耗时 | 15s | 0.015s |
| 数据实时性 | 即时（查的就是原始数据） | 近实时（< 1s，INSERT 触发延迟） |
| 存储开销 | 0（无额外存储） | +2%~+5%（取决于聚合粒度） |
| INSERT 性能影响 | 无 | +3%~+8%（每个 MV 增加聚合开销） |
| 查询并发能力 | 低（每次重算） | 高（直接读汇总行） |
| 维护成本 | 零 | 低（MV 自动维护，需关注 Merge 队列） |

### 适用场景

- **实时数据看板**：GMV、PV、UV、订单量等核心指标，运营同学疯狂刷新也不怕；
- **周期性报表**：日报、周报、月报——用不同粒度的级联 MV 一次算好，报表直接读；
- **时序数据降采样**：Prometheus / 监控日志数据，原始数据按秒采集，MV 自动聚合成分钟/小时/天级；
- **预 JOIN 宽表**：虽然 MV 不支持 JOIN 触发，但可以先把维度表 JOIN 进源表（或使用字典），再让 MV 做聚合。

### 不适用场景

- **需要 JOIN 多张事实表**：MV 只能监听一张源表，跨事实表的 JOIN 聚合无法自动维护；
- **需要绝对实时的事务一致性**：MV 是 INSERT 粒度触发，不是行级触发，有微小的延迟窗口；
- **聚合键高基数（千万级 unique key）**：MV 的目标表如果 ORDER BY 键基数太高，SummingMergeTree 合并效率下降，查询也慢。

### 注意事项

1. **MV 是 INSERT 触发器，不是查询改写**——这点跟 Oracle / PostgreSQL 的 MV 完全不同。ClickHouse 不会自动把用户的聚合查询重定向到 MV，你必须显式查询 MV 的目标表。
2. **MV 不支持 JOIN 触发**——`SELECT ... FROM A JOIN B` 的 MV 只监听 A 表，B 表变化不会触发重算。建议预 JOIN 到源表或使用字典代替。
3. **SummingMergeTree 需要 FINAL 或等待 Merge 才能看到精确结果**——对实时性要求极高且不能容忍任何误差的场景，考虑用 `AggregatingMergeTree` + `Merge` 表引擎的组合方案。
4. **MV 链路过长会增加 INSERT 延迟**——三层级联 MV 意味着每次源表 INSERT 要触发三次聚合计算，需评估写入吞吐是否可接受。

### 常见踩坑经验

**坑 1：创建 MV 后历史数据没有自动回填**

症状：MV 建好后，查出来只有最近几分钟的数据，历史数据一片空白。

原因：新建 MV 默认不处理已存在的数据——它只监听"创建之后"的 INSERT。

解决：要么建 MV 时加 `POPULATE`，要么手动 `INSERT INTO mv_target SELECT ... FROM source_table`。

**坑 2：MV 目标表引擎选错**

症状：MV 里数据对了，但查一天看板发现 sum 比预期翻倍。

原因：选了普通 MergeTree 做目标表，同一个聚合键的多个 INSERT 产生多行，查的时候重复累计。

解决：换成 SummingMergeTree（聚合键用 ORDER BY 指定），或者用 AggregatingMergeTree。

**坑 3：误以为 DROP TABLE 源表会自动清理 MV**

症状：删了 `orders_src` 后发现 `orders_hourly_mv` 的目标表还在占用磁盘。

原因：MV 的目标表是一个独立表，跟源表是触发器关系，不是分区关系。源表删除只是不再触发新的 INSERT，目标表的数据依然存在。

解决：需要手动清理目标表：`DROP TABLE orders_hourly_mv`（如果 TO 指定了目标表，还需要单独 DROP 目标表）。

**坑 4：在已有 MV 的情况下 TRUNCATE 源表导致数据不一致**

症状：TRUNCATE 了源表后，MV 目标表的数据还在（因为 TRUNCATE 不是 INSERT 事件，不会触发 MV）。

原因：ClickHouse 的 TRUNCATE 直接删文件，不走 INSERT 路径，MV 不会被通知。

解决：TRUNCATE 源表后，手动 TRUNCATE 或 DROP 重建目标表。

### 思考题

1. **物化视图和普通视图（VIEW）在 ClickHouse 中有什么本质区别？什么场景该用哪个？**

   *提示：普通视图是"存查询、不存数据"，每次查询都要重新执行 SQL；物化视图是"存数据、不存查询"，查询时直接读预计算结果。普通视图适合简化复杂 SQL 的书写（逻辑复用），物化视图适合加速重复性聚合查询。*

2. **如果源表已经有 50 亿历史数据，新建一个物化视图时如何避免重复计算？**

   *提示：POPULATE 会全表扫描——50 亿行跑一次可能需要几十分钟甚至上小时，中间如果中断还得重来。更稳妥的做法是：不加 POPULATE 创建 MV，然后手动分时间窗口回填（比如每次回填一个月的数据），这样即使中断也可以从断点继续。*

3. **设计一个三级级联 MV 的方案：源表按秒写入，分钟级聚合供实时大屏使用，小时级聚合供运营后台，天级聚合供报表系统。画出入库链路，标注每一层的引擎选型和合并策略。**

   *提示：源表 MergeTree → MV₁ AggregatingMergeTree（分钟）→ MV₂ SummingMergeTree（小时）→ MV₃ SummingMergeTree（天）。分钟级用 AggregatingMergeTree 因为秒级数据需要支持 count/distinct/sum 等多函数聚合；小时和天级用 SummingMergeTree 因为只做 sum/count 累加即可。*

4. **如果要监控 MV 的数据延迟（从源表写入到 MV 聚合完成的时间差），如何设计监控方案？**

   *提示：在源表中新增一个 `insert_time` 列记录每条数据的入库时间；MV 聚合时取 `max(insert_time)` 存入目标表；定时任务对比 `now() - max(insert_time)` 即为延迟。告警阈值可设为超过 60 秒。*

---

> **下一章预告**：在掌握了物化视图的"单表预聚合"之后，我们将踏入 ClickHouse 最强大的武器——分布式表与集群。你会了解到 Distributed Table 如何把一张逻辑表拆到多台机器上，`sharding_key` 的选型如何影响查询效率，以及 `ON CLUSTER` 语法如何让 DDL 一键同步到整个集群。敬请期待第 10 章：分布式表与集群入门。
