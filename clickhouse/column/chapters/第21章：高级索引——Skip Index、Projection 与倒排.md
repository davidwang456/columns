# 第21章：高级索引——Skip Index、Projection 与倒排

> **版本**：ClickHouse 25.x LTS
> **定位**：中级篇核心章节。当主键索引不够用，Skip Index / Projection / 倒排索引三剑客如何为你的特殊查询模式加速。
> **前置阅读**：第4章（MergeTree 家族）、第7章（主键与稀疏索引）、第14章（SQL 优化入门）
> **预计阅读**：45 分钟 | **实战耗时**：60 分钟

---

## 1. 项目背景

某网安公司的安全运营中心（SOC），大厅中央悬挂着一块 8 米宽的态势感知大屏。大屏右下角有一个"威胁狩猎"分析面板，安全分析师每天在这里用交互式查询搜索 200 亿行访问日志，试图从海量流量中嗅出 APT 组织的蛛丝马迹。

周三下午，分析师阿瑾在查询窗口敲下一行：

```sql
SELECT timestamp, ip_address, user_agent, url
FROM access_logs
WHERE user_agent LIKE '%python%'
  AND ip_address IN (SELECT ip FROM threat_intel);
```

回车。15 秒过去了，光标还在闪。30 秒过去了，还没出结果。阿瑾深吸一口气，点了一杯咖啡——这已经是本周第三次因为查询超时而被迫进入"等咖啡状态"了。

这不是 ClickHouse 本身慢。这张 `access_logs` 表的主键是 `(timestamp, host)`，排序键设计得非常合理——对于按时间和主机名过滤的场景，稀疏索引可以精准裁剪 Granule，几百亿数据秒级返回。可一旦换到 `user_agent` 或 `ip_address` 这种非主键列的过滤条件上，情况就完全变了：**主键索引帮不上忙，ClickHouse 被迫全表扫描**。

"要不……我把主键改成 `(user_agent, ip_address, timestamp)`？"阿瑾问 DBA 老杨。

老杨摇了摇头："你改了主键，原来按 `timestamp` 查的查询就全死了。一张表只有一个主键——同时服务时间窗口查询和 IP 特征检索，一根筋扛不住两个碗。"

这正是 ClickHouse 高级索引要解决的核心命题：**在不重建整张表、不改变主键设计的前提下，为特定列构建辅助索引，加速非主键列的过滤查询**。本章要介绍的三件武器——Skip Index（跳数索引）、Projection（投影）、Inverted Index（倒排索引）——就是为此而生。

Skip Index 是在每个 Granule（8192 行的数据块）上附加一层粗粒度的元数据——比如这个 Granule 里有没有包含值为 `192.168.1.1` 的 IP、有没有包含 `'python'` 这个 Token。查询时，ClickHouse 先用 Skip Index 快速排除掉那些"绝对不可能命中"的 Granule，只读剩下的 Granule，从而避免全表扫描。

Projection 则更激进——它是把一张表的列子集隐式复制成一个"内嵌物化子表"，按照另一种排序键存储。当你查询的 GROUP BY 维度恰好匹配 Projection 的定义时，ClickHouse 的优化器会自动路由到 Projection 上，效果等同于查一张预聚合表。

倒排索引是 ClickHouse 24.x 引入的实验性特性——直接对标 Elasticsearch 的全文检索能力，为海量文本数据提供"搜索引擎级"的字符串匹配性能。

本章将用一张模拟企业安全日志的表，带你逐一测试这三种索引的实际加速效果。

---

## 2. 项目设计：剧本式交锋对话

**Scene**：安全运营中心工区，阿瑾的屏幕上挂着一个已经转了 45 秒的 Loading 圈。小胖抱着一袋薯片晃过来。

**小胖**（凑近屏幕）："瑾姐，你这查了多久了？咋还没出结果？ClickHouse 不是号称列存引擎扫描几百亿数据也就几秒吗？"

**阿瑾**："那是按时间戳查——主键命中了才快。我现在要查 `user_agent` 里带 `python` 的请求，`user_agent` 不在主键上，它只能全扫。200 亿行，你说慢不慢？"

**小胖**（抓了一把薯片）："索引不就是 PRIMARY KEY 吗？ClickHouse 的文档上不是写了吗——不支持二级索引？那这 Skip Index、Projection 又是什么鬼？"

**大师**（端着茶从工位探过头）："小胖，你说的是对的——ClickHouse 确实不支持 MySQL 那种 B+Tree 二级索引。但这不代表你没招。"大师拖了把椅子坐下来，"我问你，ClickHouse 的最小数据单元是什么？"

**小胖**："Granule。每 8192 行一个 Granule，主键上的稀疏索引就是一个 Granule 一个条目。"

**大师**："对。那如果你在磁盘上把数据放好了，ClickHouse 只需要一个 min/max 信息就能判断"这个 Granule 绝对不可能有我需要的行"——这就是主键索引的裁剪原理。Skip Index 在思想上完全一致，只不过它处理的不是排序键，而是你在 DDL 里声明的任意列。"

大师打开阿瑾的 SQL 终端，敲了起来：

```sql
-- 先看现在的执行计划
EXPLAIN indexes=1
SELECT count() FROM access_logs
WHERE user_agent LIKE '%python%';
```

"看这个 `Selected marks` 后面的数字——这就是要读的 Granule 数量。如果你的表有 200 亿行，一个 Granule 是 8192 行，那大约有 244 万个 Granule。这条 SQL 后面如果显示 `Selected marks: 2440000`——那就意味着，一个 Granule 都没跳过，全表扫描。"

"如果我们给 `user_agent` 建一个 Skip Index，ClickHouse 在每个 Granule 写入时，会额外计算并存储这个 Granule 里 `user_agent` 列的索引信息。比如 `tokenbf_v1` 会把这个 Granule 里的所有 user_agent 值分词、哈希、写入一个 Bloom Filter。查询时，`WHERE user_agent LIKE '%python%'` 先不走数据文件，而是扫一遍这个 Skip Index——它比数据小 100 倍以上——只有当某个 Granule 的 Bloom Filter '说可能有'时，才真正去读那个 Granule 的数据。至少 99% 的 Granule 都会被直接跳过。"

**小胖**："哦！这不就跟食堂打饭一样？食堂门口贴了个今日菜单——你今天只想吃红烧肉，看一眼就知道哪些窗口有红烧肉，有红烧肉的几个窗口才去排队。你要是每个窗口排一遍问'有红烧肉吗'，这队伍得排多久啊！"

**大师**："精准。Bloom Filter 就是那个'今日菜单'——它告诉你'可能有'，但也会'谎报军情'。这就是 Bloom Filter 的误判率——它可能说这个 Granule 里有你要的值，实际打开一看没有。但它永远不会漏报——如果它有，你查了一定能找到。"

**技术映射 #1**：Skip Index = Granule 级别的粗粒度索引快照。每个 Granule 写入时同步构建索引数据，查询时先扫描索引快照过滤 Granule，再读数据。Bloom Filter = 带误判率的"可能有"判断器，空间换时间。

---

**小白**（一直在旁边无声地用手机查文档，此时抬头）："大师，Skip Index 有很多类型——`minmax`、`set(N)`、`bloom_filter`、`ngrambf_v1`、`tokenbf_v1`，什么时候用哪种？还有，Projection 和 Skip Index 的区别又是什么？"

**大师**："问到点子上了。Skip Index 和 Projection 虽然都叫'索引进阶'，但思路完全不同。Skip Index 是'给 Granule 贴标签'，不存完整数据，只存元信息。Projection 是'复制一份排好序的列子集'，它本质上是一张隐藏的子表。"

"咱们拆开说——**Skip Index 五种类型怎么选**，一言以蔽之：

- `minmax`：最朴素。每个 Granule 只存这一列的最小值和最大值。适合单调递增或递减的数值列，比如 `timestamp`。查 `WHERE ts >= '2025-03-01'`，一看这个 Granule 的 min 是 `2025-02-28`、max 是 `2025-02-28`，直接跳过——因为这个 Granule 的最大值都没到 3 月 1 号。

- `set(N)`：每个 Granule 存这列里出现过的 N 个不同值。适合低基数列——比如 `status_code` 只有 2xx/3xx/4xx/5xx 几十种。查 `WHERE status = 200`，一看这个 Granule 的 set 里没有 200，直接跳过。但如果列基数太高，set 装不下，效果就打折。

- `bloom_filter()`：不存具体值，存哈希位图。一个 Granule 里所有值都哈希到一个 bitmap 里。查 `WHERE ip = '192.168.1.100'`，把 IP 哈希一下，看 bitmap 对应位置是不是 1——不是 1 就绝对没有这个 IP，是 1 就'可能有'。适合高基数列的等值查询。

- `tokenbf_v1(n, size, hashes, seed)`：bloom_filter 的升级版——先把值按非字母数字字符切分成 token，再哈希。查 `WHERE msg LIKE '%error%'`，tokenbf 会把 Granule 里所有 token 放入 Bloom Filter，error 这个 token 在其中，说明该 Granule 可能包含含 error 的文本。适合空格分词的等值 token 搜索。

- `ngrambf_v1(n, size, hashes, seed)`：最灵活也最占空间。它把字符串按 n-gram 滑动窗口切成片段再哈希。比如 `'hello'` 切成 n=3：`hel`、`ell`、`llo`。查 `WHERE msg LIKE '%hel%'`，ngrambf 就能匹配上。适合子串模糊搜索，但误判率比 tokenbf 更高。"

**小白**（记着笔记）："那 Projection 呢？什么时候用 Projection 而不是 Skip Index？"

**大师**："Projection 和 Skip Index 的差异就像买菜和点外卖。Skip Index 是自己在市场上转——你每次查询都要扫一遍 Granule 数据再聚合，只不过 Skip Index 帮你跳过了大量无关 Granule。Projection 是点外卖——有人在后台帮你预先做好（预聚合），查询直接端上来。"

"举例：你频繁查询 `SELECT status, count(), avg(response_time) FROM access_logs WHERE timestamp >= today() GROUP BY status`。两种方案对比——

方案 A：给 `status` 列建一个 `set(10)` Skip Index。查询时先用 Skip Index 过滤掉不包含对应 status 值的 Granule → 读取筛选后的 Granule 的 `timestamp`、`response_time` 列 → 在内存里实时做 GROUP BY 和聚合。跳过了 70% 的 Granule 但依然要读 30% 的原始数据再现场聚合。

方案 B：建一个 Projection，按 `status` 排序并预聚合：
```sql
ALTER TABLE access_logs ADD PROJECTION proj_status_agg (
    SELECT status, count(), avg(response_time), min(timestamp), max(timestamp)
    GROUP BY status
);
```
数据写入时，ClickHouse 自动同步维护这张隐藏子表。查询时，优化器一看你的 SELECT 的聚合列和 GROUP BY 维度跟 Projection 定义一样——直接路由到 Projection 上，连原始表都不用碰，秒出结果。代价是写入放大：每次 INSERT 都要额外维护 Projection 的数据。数据量大概增加 10%-50% 不等。"

**小胖**："那 Projection 不就跟物化视图一样吗？都是预聚合。"

**大师**："很像，但有一个关键差别——**Projection 是透明自动的**。你写 `SELECT status, count() FROM access_logs GROUP BY status`，优化器自动判断是否走 Projection，你不需要改 SQL。而物化视图你得显式查询 `FROM mv_access_logs_status`，应用层要改代码。另外，Projection 的数据跟原始表存在同一个 Part 文件夹里，生命周期完全同步——Part Merge 的时候 Projection 也跟着 Merge。物化视图是独立表，你需要自己管理它的 Partition 和 TTL。"

**小白**："那倒排索引呢？跟 ngrambf_v1 有什么不同？"

**大师**："倒排索引是 ClickHouse 24.x 引入的实验性特性，底层用的是反向列表——每个 token 对应一个命中行号的列表。类比：ngrambf_v1 是'I think this Granule might contain "error"'，倒排索引是'There are exactly 1423 rows containing "error", here are the ids'。倒排索引支持更复杂的全文检索操作——布尔查询、短语匹配、排名打分。但要注意，它目前还是实验性的，需要 `SET allow_experimental_inverted_index = 1` 才能使用。"

**技术映射 #2**：Skip Index = Granule 快照索引，轻量但每次查询仍需读取命中的 Granule 原始数据再计算。Projection = 隐藏的预排序子表，写入放大但查询极快，优化器自动路由。倒排索引 = 实验性全文检索引擎，未来方向。

---

**小胖**："大师，我明白了。那是不是我给每列都建一个 Skip Index，所有查询就都飞起来？"

**大师**："停！小胖，你这不是索引优化，是索引滥用。Skip Index 不是免费的——每多一个 Skip Index，写入性能就多一份开销，磁盘空间也多一份占用。典型的 `bloom_filter()` 约占数据 1%-3%，`ngrambf_v1` 可能占到 5%-10%。你有 100 列，每列都建 ngrambf——你磁盘就全被索引吃掉了。"

"**索引选择决策树**——记住这个口诀：

1. **等值查高基数列**（`WHERE col = 'value'`，col 不在主键中）→ `bloom_filter`。
2. **查低基数枚举列**（`WHERE status IN (200, 302)`）→ `set(N)`，N 设为 max set size。
3. **子串 LIKE 模糊搜索**（`WHERE msg LIKE '%error%'`）→ `ngrambf_v1`。
4. **分词后的 Token 搜索**（`WHERE msg LIKE '%error%'` 且文本有空格分隔）→ `tokenbf_v1`，比 ngrambf 误判率更低。
5. **高频聚合 GROUP BY 查询**（每 5 秒刷一次的看板 SQL）→ `Projection`。
6. **全文检索、布尔查询**（需要搜索引擎级文本匹配）→ 倒排索引实验性特性，或干脆接 Elasticsearch。

最重要的原则：**用 EXPLAIN indexes=1 验证**——建之前看 Granule 扫描数，建之后再看 Granule 扫描数。数字说话，别拍脑袋。"

**技术映射 #3**：Skip Index 选型 = 查询模式 × 列基数 × 存储成本。没有万能索引，只有最适合的组合。每加一个索引前，必须用 EXPLAIN 验证收益，用 system.parts 确认存储开销。

---

## 3. 项目实战

### 环境准备

使用 Docker 启动 ClickHouse 25.x：

```bash
docker run -d --name ch-index-lab \
  -p 8123:8123 -p 9000:9000 \
  clickhouse/clickhouse-server:25.3
```

进入客户端：

```bash
docker exec -it ch-index-lab clickhouse-client
```

验证版本：

```sql
SELECT version();
-- 应为 25.3.x.x
```

---

### Step 1：创建带 Skip Index 的日志表

**目标**：构建一张 1000 万行安全访问日志表，内置四种 Skip Index。

```sql
-- 创建带多种 Skip Index 的访问日志表
CREATE TABLE access_logs (
    timestamp     DateTime,
    host          LowCardinality(String),
    ip            IPv4,
    user_agent    String,
    url           String,
    status        UInt16,
    response_time UInt32,
    bytes_sent    UInt64,
    
    -- bloom_filter：等值查 IP，GRANULARITY 1 → 每个 Granule 一个 bloom filter
    INDEX idx_ip           ip         TYPE bloom_filter() GRANULARITY 1,
    
    -- tokenbf_v1：LIKE搜索 user_agent 中的 token
    -- 参数：bloom filter 大小(256 bytes)、hash 函数数(3)、seed(0)
    INDEX idx_user_agent   user_agent TYPE tokenbf_v1(256, 3, 0) GRANULARITY 4,
    
    -- ngrambf_v1：子串搜索 URL
    -- 参数：ngram 长度(4)、bloom filter 大小(256)、hash 函数数(2)、seed(0)
    INDEX idx_url          url        TYPE ngrambf_v1(4, 256, 2, 0) GRANULARITY 4,
    
    -- set(N)：低基数状态码
    INDEX idx_status       status     TYPE set(10) GRANULARITY 4,
    
    -- minmax：数值列范围裁剪
    INDEX idx_response_time response_time TYPE minmax GRANULARITY 1
    
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(timestamp)
ORDER BY (host, timestamp);
```

**参数说明**：
- `GRANULARITY 1`：每 1 个 Granule（8192 行）做一个 Skip Index 条目。值越小，索引越密集，过滤越精准，但空间占用越大。
- `GRANULARITY 4`：每 4 个 Granule（约 32768 行）共享一个 Skip Index 条目，空间更省，但可能多读一些数据。
- `tokenbf_v1(256, 3, 0)`：256 是 Bloom Filter 的字节大小，3 是 hash 函数数量。hash 函数越多误判率越低，但计算开销越大。

**插入 1000 万行模拟数据**：

```sql
INSERT INTO access_logs
SELECT
    now() - INTERVAL number * 10 SECOND AS timestamp,
    ['web-01','web-02','api-01','admin-01'][rand() % 4 + 1] AS host,
    toIPv4(concat(
        toString(rand() % 256), '.',
        toString(rand() % 256), '.',
        toString(rand() % 256), '.',
        toString(rand() % 256)
    )) AS ip,
    ['Mozilla/5.0 Chrome/120','python-requests/2.31','curl/8.4','Go-http-client/2.0',
     'Wget/1.21','Java/17 HttpClient','Mozilla/5.0 Safari/17','python-urllib/3.11'][rand() % 8 + 1] AS user_agent,
    concat('/api/', ['users','orders','products','search','login','admin/config','health','metrics'][rand() % 8 + 1],
           '?id=', toString(rand64() % 1000000)) AS url,
    [200, 200, 200, 200, 301, 400, 403, 404, 500, 502][rand() % 10 + 1] AS status,
    rand() % 5000 AS response_time,
    rand() % 1048576 AS bytes_sent
FROM numbers(10000000);
```

验证数据量和 Skip Index 是否建立：

```sql
SELECT
    count() AS rows,
    formatReadableSize(sum(bytes_on_disk)) AS disk_size,
    count(DISTINCT host) AS hosts,
    count(DISTINCT status) AS statuses
FROM access_logs;

-- 查看已创建的 Skip Index
SELECT
    name,
    type,
    expr,
    granularity
FROM system.data_skipping_indices
WHERE table = 'access_logs'
ORDER BY name;
```

输出示例：
```
┌─name─────────────┬─type─────────┬─expr──────────┬─granularity─┐
│ idx_ip           │ bloom_filter │ ip            │           1 │
│ idx_response_time│ minmax       │ response_time │           1 │
│ idx_status       │ set(10)      │ status        │           4 │
│ idx_url          │ ngrambf_v1   │ url           │           4 │
│ idx_user_agent   │ tokenbf_v1   │ user_agent    │           4 │
└──────────────────┴──────────────┴───────────────┴─────────────┘
```

---

### Step 2：测试 Skip Index 效果

**目标**：对比 EXPLAIN 输出，验证各类型 Skip Index 的裁剪效果。

#### 2.1 bloom_filter — IP 精确查询

```sql
-- 测试 bloom_filter：精确 IP 查询
-- 先用 EXPLAIN 看 Granule 扫描数
EXPLAIN indexes=1
SELECT count() FROM access_logs WHERE ip = '192.168.1.100';

-- 关键观察输出中 Selected marks 的数量
-- 如果没有 Skip Index：Selected marks ≈ 全部 ~1220
-- 有 bloom_filter 后：可能只扫描几个 Granule

-- 对照：查一个不存在的 IP（Skip Index 应该能完全跳过）
EXPLAIN indexes=1
SELECT count() FROM access_logs WHERE ip = '10.0.0.99';
-- 如果 bloom_filter 生效：Selected marks ≈ 0
```

**预期结果**：`idx_ip` 的 bloom_filter 启动后，等值 IP 查询的 Granule 扫描量从全表降为极少数。不存在的 IP 几乎能完全跳过。

#### 2.2 tokenbf_v1 — user_agent LIKE 搜索

```sql
-- 没有 Skip Index 的查询（关闭所有索引对比）
SELECT count() FROM access_logs
WHERE user_agent LIKE '%python%'
SETTINGS use_skip_indexes = 0;
-- 耗时：可能 2-5 秒

-- 开启 Skip Index 后查询
SELECT count() FROM access_logs
WHERE user_agent LIKE '%python%';
-- 耗时：应该降到 0.1-0.3 秒

-- EXPLAIN 对比
EXPLAIN indexes=1
SELECT count() FROM access_logs
WHERE user_agent LIKE '%python%';

-- 观察输出：
-- KeyCondition: (user_agent LIKE '%python%')
-- 如果有 idx_user_agent 行出现 → Skip Index 已被使用
-- 对比 use_skip_indexes=0 时的 marks 数量
```

#### 2.3 set(N) — 状态码 IN 查询

```sql
-- set 索引对低基数列的 IN 查询非常有效
EXPLAIN indexes=1
SELECT count() FROM access_logs WHERE status = 200;

EXPLAIN indexes=1
SELECT count() FROM access_logs WHERE status IN (200, 302, 500);

-- 对比：不命中 set 索引的列（没有 Skip Index）
EXPLAIN indexes=1
SELECT count() FROM access_logs WHERE bytes_sent > 100000;
-- 这个查询没有对应的 Skip Index → Selected marks 应该是全量
```

#### 2.4 ngrambf_v1 — URL 子串搜索

```sql
-- URL 中包含 'admin' 的请求
EXPLAIN indexes=1
SELECT count() FROM access_logs WHERE url LIKE '%admin%';

-- URL 中包含 'search' 的请求
EXPLAIN indexes=1
SELECT count() FROM access_logs WHERE url LIKE '%search%';

-- 对比关闭 Skip Index
SELECT count() FROM access_logs WHERE url LIKE '%admin%'
SETTINGS use_skip_indexes = 0;
```

#### 2.5 minmax — 响应时间范围查询

```sql
-- minmax 索引：查询 response_time > 4000ms 的慢请求
EXPLAIN indexes=1
SELECT count() FROM access_logs WHERE response_time > 4000;

-- minmax 对范围查询有效
EXPLAIN indexes=1
SELECT count() FROM access_logs
WHERE response_time >= 1000 AND response_time <= 2000;
```

---

### Step 3：Projection 实战——局部预聚合

**目标**：为高频聚合查询创建 Projection，让优化器自动选择最快路径。

#### 3.1 创建 Projection

```sql
-- 场景：SOC 看板需要每 10 秒刷新一次"按主机和状态码统计请求量和平均响应时间"
-- 原始查询：GROUP BY host, status，每次扫描大量行
-- 建 Projection 替代实时聚合

ALTER TABLE access_logs ADD PROJECTION proj_host_status (
    SELECT
        host,
        status,
        count() AS cnt,
        avg(response_time) AS avg_rt,
        sum(bytes_sent) AS total_bytes,
        min(timestamp) AS first_seen,
        max(timestamp) AS last_seen
    GROUP BY host, status
);
```

#### 3.2 物化历史数据

```sql
-- Projection 创建后只对新写入的数据生效
-- 历史数据必须手工 MATERIALIZE
ALTER TABLE access_logs MATERIALIZE PROJECTION proj_host_status;
-- 耗时跟数据量有关，1000 万行大约 20-40 秒

-- 查看物化进度
SELECT
    name,
    parent_name,
    formatReadableSize(sum(bytes_on_disk)) AS total_size,
    count() AS part_count
FROM system.projection_parts
WHERE parent_table = 'access_logs'
  AND name = 'proj_host_status'
GROUP BY name, parent_name;
```

#### 3.3 验证 Projection 自动选择

```sql
-- 查询必须"精准匹配" Projection 的 SELECT 表达式（或子集）
EXPLAIN indexes=1
SELECT
    host,
    status,
    count(),
    avg(response_time)
FROM access_logs
WHERE timestamp >= today()
GROUP BY host, status;

-- 看 EXPLAIN 输出中是否包含：
-- "projection: proj_host_status" 字样
-- 如果有 → ClickHouse 自动走了 Projection，跳过原始表

-- 对比没有匹配到 Projection 的查询
EXPLAIN indexes=1
SELECT
    host,
    status,
    count(),
    avg(response_time),
    uniq(ip)  -- Projection 里没有的聚合函数
FROM access_logs
WHERE timestamp >= today()
GROUP BY host, status;
-- 应该不会走 Projection，而是原始表扫描

-- 也可以手动关闭 Projection 优化对比
SELECT host, status, count(), avg(response_time)
FROM access_logs
GROUP BY host, status
SETTINGS optimize_use_projections = 0;
-- 对比打开 optimize_use_projections = 1 的耗时
```

**坑点提示**：Projection 的 SELECT 表达式必须精确匹配（或被子集包含）查询的聚合。如果你的查询是 `sum(response_time)` 但 Projection 里存的是 `avg(response_time)`，优化器无法直接用 Projection 推导出 sum，会回退到原始表扫描。

---

### Step 4：倒排索引实验（24.x+）

**目标**：体验 ClickHouse 24.x 的实验性倒排索引。

```sql
-- 必须先开启实验性功能
SET allow_experimental_inverted_index = 1;

-- 创建一张文章表用于全文检索
DROP TABLE IF EXISTS articles;
CREATE TABLE articles (
    id      UInt64,
    title   String,
    content String,
    tags    Array(String),
    
    -- 倒排索引：对 content 列做全文检索
    INDEX inv_content content TYPE inverted GRANULARITY 1,
    INDEX inv_title   title   TYPE inverted GRANULARITY 1,
    INDEX inv_tags    tags    TYPE inverted GRANULARITY 1
    
) ENGINE = MergeTree()
ORDER BY id;

-- 插入模拟文章数据
INSERT INTO articles SELECT
    number AS id,
    concat('Article ', toString(number), ': ',
           ['ClickHouse Performance Guide','Monitoring Best Practices',
            'Distributed Systems Design','SQL Optimization Tips',
            'Data Compression Techniques'][number % 5 + 1]) AS title,
    concat('This article discusses ',
           ['ClickHouse query optimization and index strategies',
            'Prometheus integration and alerting rules for ClickHouse monitoring',
            'Distributed computing patterns and fault tolerance in modern systems',
            'Query tuning techniques including EXPLAIN and PREWHERE usage',
            'Column compression codec selection and storage optimization'][number % 5 + 1],
           '. Additional topics include ',
           ['merge tree engine internals','replication and sharding',
            'Kafka integration pipelines','materialized views',
            'dictionary external lookup'][number % 5 + 1],
           '.') AS content,
    [['clickhouse','performance','index'],['clickhouse','monitoring','prometheus'],
     ['distributed','architecture','design'],['sql','optimization','tuning'],
     ['compression','storage','codecs']][number % 5 + 1] AS tags
FROM numbers(50000);

-- 全文检索：LIKE 查询利用倒排索引加速
EXPLAIN indexes=1
SELECT id, title
FROM articles
WHERE content LIKE '%ClickHouse%' OR title LIKE '%ClickHouse%';

-- 支持多条件布尔搜索
EXPLAIN indexes=1
SELECT id, title
FROM articles
WHERE content LIKE '%ClickHouse%' AND tags::String LIKE '%index%';

-- 对比无倒排索引（关闭 Skip Index）
EXPLAIN indexes=1
SELECT id, title
FROM articles
WHERE content LIKE '%ClickHouse%'
SETTINGS use_skip_indexes = 0;
```

---

### Step 5：索引选择决策树——综合场景

**目标**：根据业务查询模式，为表的每一列选择最合适的索引类型。

```sql
-- 场景 1：需要精确匹配某个 SessionID，列基数极高
-- → bloom_filter()，GRANULARITY 1
ALTER TABLE access_logs ADD INDEX idx_session session_id
    TYPE bloom_filter() GRANULARITY 1;

-- 场景 2：需要 LIKE '%keyword%' 搜索日志消息
-- → ngrambf_v1 或 tokenbf_v1
-- tokenbf_v1 适合有空格/标点分隔的文本，误判率更低
ALTER TABLE access_logs ADD INDEX idx_message message
    TYPE tokenbf_v1(256, 3, 0) GRANULARITY 4;

-- 场景 3：频繁按 status + host 聚合 count() 和 avg()
-- → Projection，让优化器自动选择
ALTER TABLE access_logs ADD PROJECTION proj_status_count (
    SELECT status, host, count(), avg(response_time), uniq(ip)
    GROUP BY status, host
);
ALTER TABLE access_logs MATERIALIZE PROJECTION proj_status_count;

-- 场景 4：需要跨多列全文搜索
-- 方案一：建倒排索引（24.x+）
-- 方案二：建 ngrambf_v1 组合（兼容旧版本）
ALTER TABLE access_logs ADD INDEX idx_search user_agent
    TYPE ngrambf_v1(4, 256, 2, 0) GRANULARITY 4;
ALTER TABLE access_logs ADD INDEX idx_search_url url
    TYPE ngrambf_v1(4, 256, 2, 0) GRANULARITY 4;

-- 场景 5：数值列范围查询（response_time BETWEEN x AND y）
-- → minmax，GRANULARITY 1
ALTER TABLE access_logs ADD INDEX idx_rt response_time
    TYPE minmax GRANULARITY 1;
```

---

### Step 6：索引维护与监控

**目标**：管理已有索引，监控空间占用和效果。

```sql
-- 1. 查看所有 Skip Index
SELECT
    database,
    table,
    name,
    type,
    expr,
    granularity
FROM system.data_skipping_indices
WHERE table = 'access_logs'
ORDER BY name;

-- 2. 查看 Projection 状态与空间占用
SELECT
    name,
    parent_name,
    formatReadableSize(sum(bytes_on_disk)) AS total_size,
    formatReadableSize(sum(data_compressed_bytes)) AS compressed,
    count() AS parts
FROM system.projection_parts
WHERE parent_table = 'access_logs'
GROUP BY name, parent_name;

-- 3. 查看索引占用的磁盘空间（估算）
SELECT
    database,
    table,
    formatReadableSize(sum(data_compressed_bytes)) AS data_size,
    formatReadableSize(sum(marks_bytes)) AS marks_size
FROM system.parts
WHERE table = 'access_logs' AND active
GROUP BY database, table;

-- 4. 删除不再需要的索引
ALTER TABLE access_logs DROP INDEX IF EXISTS idx_unused;
-- 注意：DROP INDEX 是轻量级元数据操作，不会重写数据

-- 5. 删除不再需要的 Projection
ALTER TABLE access_logs DROP PROJECTION IF EXISTS proj_old;
-- 已有 Part 中的 Projection 数据会在下次 Merge 时被清理

-- 6. 添加新的 Skip Index（对已有数据不会自动构建）
ALTER TABLE access_logs ADD INDEX idx_new_col new_col
    TYPE bloom_filter() GRANULARITY 1;
-- 新索引只对新写入的数据生效。如需对历史数据生效，需要：
ALTER TABLE access_logs MATERIALIZE INDEX idx_new_col;
-- 此操作会重写所有 Part，耗时与数据量成正比
```

**重要坑点**：`ADD INDEX` 后历史数据不会自动构建索引！必须执行 `ALTER TABLE ... MATERIALIZE INDEX idx_name` 来填充历史数据。这个操作会在后台异步执行（Mutation），可通过 `system.mutations` 查看进度。

---

### 测试验证

汇总各索引类型的加速效果：

```sql
-- ==================== 对比测试脚本 ====================

-- 测试 1：bloom_filter 效果
SELECT '=== bloom_filter: IP 等值查询 ===' AS test;
SELECT count() FROM access_logs WHERE ip = '192.168.1.100'
SETTINGS use_skip_indexes = 0;
-- 记录耗时: __秒

SELECT count() FROM access_logs WHERE ip = '192.168.1.100';
-- 记录耗时: __秒（预期 5-10 倍提升）

-- 测试 2：tokenbf_v1 效果
SELECT '=== tokenbf_v1: LIKE 搜索 ===' AS test;
SELECT count() FROM access_logs WHERE user_agent LIKE '%python%'
SETTINGS use_skip_indexes = 0;
SELECT count() FROM access_logs WHERE user_agent LIKE '%python%';

-- 测试 3：set(N) 效果
SELECT '=== set: 状态码 IN 查询 ===' AS test;
SELECT count() FROM access_logs WHERE status IN (200, 302, 500)
SETTINGS use_skip_indexes = 0;
SELECT count() FROM access_logs WHERE status IN (200, 302, 500);

-- 测试 4：Projection 效果
SELECT '=== Projection: 聚合查询 ===' AS test;
SELECT host, status, count(), avg(response_time)
FROM access_logs GROUP BY host, status
SETTINGS optimize_use_projections = 0;
-- 记录耗时: __秒

SELECT host, status, count(), avg(response_time)
FROM access_logs GROUP BY host, status;
-- 记录耗时: __秒（预期 3-5 倍提升）

-- 测试 5：minmax 效果
SELECT '=== minmax: 范围查询 ===' AS test;
SELECT count() FROM access_logs WHERE response_time > 4000
SETTINGS use_skip_indexes = 0;
SELECT count() FROM access_logs WHERE response_time > 4000;

-- 测试 6：EXPLAIN 汇总对比
SELECT '=== EXPLAIN 验证 ===' AS test;
EXPLAIN indexes=1
SELECT host, status, count(), avg(response_time)
FROM access_logs GROUP BY host, status;
-- 确认输出中包含 "projection: proj_host_status"
```

---

## 4. 项目总结

### 索引类型选择速查表

| 查询模式 | 推荐索引 | 为什么 |
|---------|---------|--------|
| `col = 'value'`（高基数） | `bloom_filter()` | 低空间开销（~1-3%），等值判断极快 |
| `col IN (...)`（低基数枚举） | `set(N)` | 精确值集合判断，无误判 |
| `col LIKE '%xxx%'`（子串搜索） | `ngrambf_v1` | n-gram 滑动窗口覆盖任意子串 |
| `col LIKE '%token%'`（分词文本） | `tokenbf_v1` | 空格/标点分词，误判率低于 ngrambf |
| 数值范围查询 | `minmax` | 最小/最大值边界裁剪，空间开销极小 |
| GROUP BY + 聚合（高频） | `Projection` | 预计算聚合结果，查询时直接返回 |
| 多列全文检索 | `inverted`（24.x+） | 倒排索引，搜索级文本匹配 |
| 复合条件 OR/AND | 组合多个 Skip Index | 每种条件用对应索引，全扫描时各司其职 |

### Skip Index vs Projection vs 物化视图

| 特性 | Skip Index | Projection | 物化视图 |
|------|-----------|-----------|---------|
| **存储方式** | 每个 Granule 附加元数据 | 隐藏子表（同 Part 内） | 独立表 |
| **空间开销** | ~1%-10% | ~10%-50% | 与基表无关，视聚合粒度而定 |
| **查询方式** | 自动（透明） | 自动（优化器选择） | 必须显式查询视图表 |
| **写入放大** | 轻微（构建索引） | 中等（并行写子表数据） | 中等（异步触发视图查询） |
| **历史数据** | 需 `MATERIALIZE INDEX` | 需 `MATERIALIZE PROJECTION` | 初始 `INSERT INTO ... SELECT` 全量加载 |
| **数据一致性** | 实时（Part 级同步） | 实时（Part 级同步） | 最终一致（异步写入触发） |
| **ALTER 灵活性** | 可 Add/Drop/Materialize | 可 Add/Drop/Materialize | 可 DETACH/ATTACH/MODIFY QUERY |
| **适用场景** | 过滤条件加速 | 预聚合加速 | 复杂多级聚合、跨表关联 |

### 注意事项

1. **`allow_experimental_inverted_index = 1`**：倒排索引是 24.x 的实验性功能，不建议在生产库核心表上使用。先在灰度环境充分测试。

2. **Skip Index 的 GRANULARITY 越大，过滤效果越差**。`GRANULARITY 1`（每个 Granule 一个索引）最精准但空间最大；`GRANULARITY 8` 空间最省但可能把一个不该读的 Granule 也带进来。一般来说，高基数列用 `GRANULARITY 1`，低基数列用 `GRANULARITY 4`。

3. **Projection 需要 `MATERIALIZE PROJECTION`**。创建 Projection 后，只对新写入数据自动物化，历史数据必须手动 `ALTER TABLE ... MATERIALIZE PROJECTION`。这个操作会触发 Mutation，期间 Part 会重写，注意磁盘空间和 Merge 压力。

4. **`ADD INDEX` 后历史数据也需要 `MATERIALIZE INDEX`**。与 Projection 一样，新索引只对新数据生效。历史数据的 `MATERIALIZE INDEX` 同样是后台异步的。

5. **倒排索引不支持 ALTER**。一旦创建，不能 `MATERIALIZE`（因为它不是 Skip Index 那种 Granule 级构建），必须重建表。目前倒排索引的 DDL 操作还很有限。

### 常见踩坑经验

1. **bloom_filter 误判率比想象中高**。Bloom Filter 的误判率取决于 hash 函数数量和 bit 数组大小。如果 GRANULARITY 设置太大（比如 `GRANULARITY 8`），一个 bloom filter 要"记住" 8 个 Granule 的所有值，bit 数组被塞得太满，误判率会急剧飙升——可能截获 50% 的 Granule 但实际上只有 5% 的 Granule 真正包含目标值。典型的劳而无功。

2. **创建大量 Projection 导致 INSERT 性能雪崩**。每个 Projection 都是一张隐藏的子表，每次 INSERT 都需要同步写。如果一张表挂了 10 个 Projection，写入放大就是 11 倍（1 原表 + 10 Projection）。应该只为核心的高频看板查询建 Projection，不要贪多。

3. **忘记 `MATERIALIZE PROJECTION` 导致历史数据索引失效**。很多同学建了 Projection、跑了一下 EXPLAIN 发现快了很多、就上线了——结果第二天运营说"昨天的数据查出来是空的"。原因就是只对新数据自动物化，忘记手工 `MATERIALIZE PROJECTION` 填充历史数据。

4. **ngrambf_v1 的 n 值设置不当**。n 太小（如 n=2），索引会把几乎所有字符组合都装进 Bloom Filter，误判率极高。n 太大（如 n=6），搜索短词（如 'sql'）时无法匹配到 ngram。`n=3` 或 `n=4` 是最常见的选择，3 更灵活，4 误判更低。

5. **倒排索引不等于 Elasticsearch 替换**。倒排索引目前是单列粒度，不支持 Elasticsearch 那样的跨字段评分、高亮、聚合。如果你的业务需要专业的全文检索能力，还是应该 ClickHouse + Elasticsearch 的组合架构，倒排索引只适合轻量的 LIKE 加速。

### 思考题

1. `bloom_filter()` Skip Index 的误判率在 `GRANULARITY=1` 时大约是多少？如果每 Granule 有 8192 行，每行 IP 不同，bloom filter 大小为默认值（约 8192 bits = 1024 bytes），使用 3 个 hash 函数，请估算误判率。提示：$p \approx (1 - e^{-kn/m})^k$，其中 $k=3$ 为 hash 函数数，$n=8192$ 为插入元素数，$m=65536$ 为 bit 数组大小。这个误判率在实际场景中可接受吗？如何调整参数降低误判率？

2. 某业务有一个 `SELECT user_id, count(), sum(amount) FROM orders WHERE order_date >= today() GROUP BY user_id` 的查询，每分钟执行 200 次。你决定建 Projection 加速。但上线后发现，虽然查询确实变快了，表的 INSERT 延迟却从 30ms 飙升到 200ms。请分析原因，并提出两种优化方案——既要保持查询速度，又要控制写入延迟。

---

> **下一章预告**：第22章——物化视图进阶：多级聚合与实时看板。我们将基于本章的 Projection 概念，深入 ClickHouse 的异步物化视图机制，构建"分钟级 → 小时级 → 天级"的多级聚合链路，实现零查询延迟的实时看板。
