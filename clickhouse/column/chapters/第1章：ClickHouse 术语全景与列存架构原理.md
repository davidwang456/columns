# 第1章：ClickHouse 术语全景与列存架构原理

> **定位**：专栏总览与开篇，建立统一语系。
> **核心内容**：术语词典、列存 vs 行存、ClickHouse 整体架构图、写入与查询路径纵览、LSM-Tree 在 MergeTree 中的体现。
> **实战目标**：用 Docker 部署 ClickHouse，手绘一张三层架构图，输出到团队 Wiki。

---

## 1. 项目背景

双十一大促刚过，电商公司数据团队的小胖瘫在工位上，盯着屏幕唉声叹气。运营同学五分钟前发来紧急需求："帮我跑一下近30天各区域GMV趋势，十分钟后开高管复盘会要"。看起来很简单的SQL——`GROUP BY region` 再 `SUM(amount)`——可这张 `orders` 表已经攒了5000万行记录，MySQL跑了三分钟还没出结果。期间整个系统卡得像幻灯片，订单列表打不开、库存查询超时、客服查不了用户订单状态。老板在群里@所有人："数据库怎么又堵了？"

这不是一次两次了。随着公司业务增长，数据每季度膨胀十倍往上，从年初的百万行到年末的千万行甚至过亿行。行式存储的MySQL面对OLTP单点查询游刃有余，但一到分析型聚合查询就原形毕露——它必须把整行数据从磁盘读出来，哪怕你只查两列，也得把整行的几十列拖进内存。更要命的是，MySQL的查询响应时间随数据量线性增长，这意味着三个月后四分钟，半年后十分钟。运营团队想要的那种"拖拽式实时看板"，在MySQL上根本是痴人说梦。

小胖试过在MySQL上建索引、拆表、加只读副本，但治标不治本。分区表能救一次两次，可每次新建分区又要变更ETL脚本；加缓存能加速固定维度查询，但运营的需求千奇百怪，没法预缓存所有可能。团队意识到：这不是调参能解决的问题——这是**工具用错了**。OLTP数据库干不了OLAP的事，就像货车跑不了F1赛道一样。

架构师大师拍板："上ClickHouse。"

小胖挠头："ClickHouse是啥？跟MySQL有啥不一样？"小白推了推眼镜："听说这东西查几十亿行数据跟飞一样，是什么原理？会不会也有坑？"于是，一场关于列存架构的三人对话就此展开。

---

## 2. 项目设计：剧本式交锋对话

**Scene**：数据团队会议室，白板上画着一个MySQL和一个带问号的方框。

**小胖（打开一包薯片）**："大师，这个ClickHouse到底有啥魔法？我那一查询跑了三分钟没结果，运营那边都快把我工位围了。咱把MySQL的索引再优化优化不行吗？"

**大师**："不是索引的问题。你自己想想，你要查的是GMV——只关心 `region` 和 `amount` 两列，但MySQL把一整行所有列都读出来了。订单号、用户ID、收件地址、商品SKU……全塞进内存，然后扔掉90%不用的字段。这不就跟——"

**小胖**："食堂打饭一样嘛！明明只要一勺红烧肉，师傅非要把每个菜都舀一勺放盘里，最后我只吃红烧肉，其他全倒掉。浪费啊！"

**大师**："对。所以ClickHouse玩的是另外一套逻辑。它不是按行存，是按列存。你只要区域和金额两列，它就只读两列的磁盘文件，IO量砍掉90%。"

**小白（放下笔记本）**："这个按列存听起来是好，但有个问题：如果我要查某个订单的全部信息呢？列存就得把每个列的对应位置都拼回来，不也一样慢吗？"

**大师**："好问题。这正是OLTP和OLAP的根本分歧。MySQL要干的活儿是'频繁的增删改查单行'，行式存储在内存里相邻，CPU缓存命中率高。ClickHouse要干的活儿是'批量扫描百万行做聚合'，列式存储能向量化执行——CPU一次性算整列的加法，SIMD指令流水线拉满。说白了，**MySQL是手术刀，ClickHouse是拖拉机**——你不能用手术刀犁地，也不能用拖拉机做微创手术。"

**技术映射**：行式存储（Row Store）适合OLTP的点查询和事务更新；列式存储（Column Store）适合OLAP的大批量扫描和聚合计算。

---

**小胖**："哦我懂了！但ClickHouse是怎么把数据组织到磁盘上的？MySQL有B+Tree索引页，ClickHouse有啥？"

**大师**："问得好。ClickHouse最核心的表引擎叫MergeTree。它把数据组织成三个层次：**Part（数据块）→ Granule（颗粒，默认8192行）→ Block（内存中的列数据块）**。写入数据的时候，先攒一个Block在内存，然后按8192行切成一个Granule，每个Granule写出一组列文件。多个Granule凑成一个Part落盘。"

**小白**："等一下，那如果我不停地INSERT，会一直产生新的Part，越积越多怎么办？"

**大师**："这就是MergeTree名字的由来——**Merge（合并）**。后台会定期把多个小Part合并成一个大Part，就像乐高拼装一样，10个小拼图合成一个大拼图。这个过程叫Merge，完全后台自动执行，不阻塞前台查询。"

**小胖**："哇，这不就像我家楼下快递站嘛！双十一多了几百个小包裹，快递员过来先把小的归拢到一个大麻袋里再搬走，省得来回跑。"

**大师**："就是这个道理。这个思路学术界叫**LSM-Tree（Log-Structured Merge-Tree）**——先写内存（MemTable），内存满了刷成一个小文件（SSTable），后台定期合并小文件为大文件。ClickHouse把内存换成了Block，把小文件换成了Part。只不过经典LSM-Tree是Key-Value模型，而ClickHouse是列存模型。"

**小白**："那合并的时候如果数据有重复怎么办？比如我UPDATE了一行，现在同一个订单有两个版本分布在不同的Part里？"

**大师**："好，这引出ClickHouse的一个核心特性：它其实**不支持原地更新（UPDATE）**。所谓的UPDATE在ClickHouse是ALTER DELETE+INSERT，叫Mutation——它本质上是给每个Part标记一个版本号，查询的时候取最新版本的Part。但对于数据仓库场景，大部分数据是追加写入的日志、事件流，不需要频繁更新。真要更新怎么办？可以用ReplacingMergeTree引擎——合并时去重，但这也是'最终去重'，不是事务级的即时去重。**列存的代价就是UPDATE慢；但换来的是SELECT快几十倍。**"

**技术映射**：MergeTree = LSM-Tree思想（内存Buffer → 磁盘有序文件 → 后台归并） + 列式存储（按列组织文件，避免读无关列）。

---

**小胖**："说了半天，ClickHouse整体到底长啥样啊？一个SQL从写进去到查出来，要经过哪些步骤？"

**大师**："ClickHouse架构分四层，我画给你们看。"

大师走向白板，边画边讲：

```
┌──────────────────────────────────────────────┐
│              接入层（Access Layer）            │
│   TCP 9000（Native Protocol）                │
│   HTTP 8123（REST接口）                      │
│   客户端：clickhouse-client / JDBC / Python    │
└────────────────────┬─────────────────────────┘
                     │
┌────────────────────┴─────────────────────────┐
│            查询层（Query Layer）                │
│   Parser → Analyzer → Planner → Pipeline      │
│   → 向量化执行引擎（Pipeline Executor）         │
└────────────────────┬─────────────────────────┘
                     │
┌────────────────────┴─────────────────────────┐
│           存储层（Storage Layer）               │
│   MergeTree 家族引擎（核心）                    │
│   Part / Granule / Column File / Mark File    │
│   压缩/编码：LZ4, ZSTD, Delta                   │
└────────────────────┬─────────────────────────┘
                     │
┌────────────────────┴─────────────────────────┐
│        分布式层（Distributed Layer）            │
│   分片（Shard）+ 副本（Replica）                │
│   Distributed 表引擎                          │
│   ZooKeeper / ClickHouse Keeper（协调）        │
└──────────────────────────────────────────────┘
```

"一个INSERT请求进来，先走接入层的Native Protocol（9000端口）。到了查询层，解析SQL变成AST，分析器检查语法和权限，Planner生成执行计划，最后交给Pipeline Executor——这是向量化执行的核心，一条流水线多个Worker线程并行处理。Pipeline把数据交给存储层，存储层按MergeTree的规则把Block切成Granule、写列文件、生成Mark标记文件,攒够Part落盘。后台Merge Scheduler定期触发合并，把小Part并成大Part。"

**小白**："那查询呢？SELECT语句走哪个路径？"

**大师**："查询走的路径类似，但不需要写磁盘这一步。SQL Parser解析 → Analyzer分析 → Planner做查询计划优化——比如判断哪些分区可以跳过（分区裁剪）、哪个Granule可以跳过（稀疏索引）——然后把只需要的列文件读出来，Block里跑聚合计算，返回结果。如果你问的是Distributed表，Planner会先把查询扇出到各个分片节点，各节点本地算完再把中间结果汇总到发起节点做最终聚合。"

**技术映射**：四层架构对应的是——接入层（门卫负责登记放行）、查询层（大脑负责理解任务、制定计划、指挥执行）、存储层（仓库负责货物存取和整理）、分布式层（分公司网络负责跨区域协同）。

---

## 3. 项目实战

### 环境准备

用Docker Compose启动ClickHouse单节点，一份YAML搞定：

```yaml
# docker-compose.yml
version: '3.8'
services:
  clickhouse:
    image: clickhouse/clickhouse-server:24.12
    container_name: ch01-clickhouse
    ports:
      - "8123:8123"   # HTTP 接口
      - "9000:9000"   # Native 协议
    environment:
      CLICKHOUSE_USER: default
      CLICKHOUSE_PASSWORD: colab2024
      CLICKHOUSE_DB: analytics
    volumes:
      - ./data:/var/lib/clickhouse
      - ./logs:/var/log/clickhouse-server
    ulimits:
      nofile:
        soft: 262144
        hard: 262144
```

> **踩坑提醒**：Windows下用Docker Desktop挂载本地卷可能会有权限问题.如果启动失败，先在当前目录下 `mkdir data logs` 确保目录存在且可写。

### 分步实现

#### Step 1：启动 ClickHouse 并连接

**目标**：启动单节点，验证服务可用。

```bash
# 在 docker-compose.yml 同目录下执行
docker compose up -d

# 验证容器运行状态
docker logs ch01-clickhouse 2>&1 | grep -i "ready"

# 进入 ClickHouse 客户端
docker exec -it ch01-clickhouse clickhouse-client -u default --password colab2024
```

进入客户端后执行验证：

```sql
-- 验证服务器存活
SELECT 1;

-- 查看 ClickHouse 版本
SELECT version();

-- 查看当前数据库
SELECT currentDatabase();
```

预期输出：

```
┌─1─┐
│ 1 │
└───┘

┌─version()──────┐
│ 24.12.1.1614   │
└────────────────┘
```

#### Step 2：探索系统表，理解 ClickHouse 架构

**目标**：通过 `system.*` 系统表直观感受四层架构。

```sql
-- ① 接入层：查看支持的数据类型，属于查询层的功能注册
SELECT name, case_insensitive FROM system.data_type_families LIMIT 10;

-- ② 存储层：查看有哪些数据库
SELECT name, engine, data_path FROM system.databases;

-- ③ 存储层：system 库下有哪些表（元数据总览）
SELECT name, engine, create_table_query 
FROM system.tables 
WHERE database = 'system' 
LIMIT 10;

-- ④ 分布式层相关设置（单节点暂时为空，但可以看到配置项）
SELECT name, value, description 
FROM system.settings 
WHERE name LIKE '%merge%' OR name LIKE '%distributed%';
```

**架构映射产出**：根据查询结果，你能看到 `system.databases` 返回的 `data_path` 指向磁盘上的真实目录（存储层），`system.settings` 中的 `max_threads` 控制了 Pipeline 的并行度（查询层），`distributed_*` 系列设置对应分布式层。这就是一张动态可查询的架构图。

#### Step 3：创建第一张 MergeTree 表，理解 Part 和 Granule

**目标**：亲手建表、写入数据，观察 Part 和 Granule 的生成。

```sql
-- 切换到 analytics 库
USE analytics;

-- 创建订单表（MergeTree 引擎）
CREATE TABLE orders (
    order_id        UInt64,
    user_id         UInt64,
    region          LowCardinality(String),   -- 省份，基数低，用 LowCardinality 优化
    order_date      Date,
    amount          Decimal(10, 2),
    product_count   UInt16,
    status          Enum8('pending' = 0, 'paid' = 1, 'shipped' = 2, 'done' = 3)
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(order_date)   -- 按月分区
ORDER BY (region, order_date, order_id);  -- 排序键 = 稀疏索引

-- 批量插入模拟数据：2024年Q4的30个区域的订单
INSERT INTO orders
SELECT
    number AS order_id,
    rand(1) % 10000 AS user_id,
    ['北京','上海','广州','深圳','杭州','成都','武汉','南京','苏州','天津'][(rand(3) % 10) + 1] AS region,
    toDate('2024-10-01') + (rand(2) % 91) AS order_date,
    toDecimal64(rand(4) % 10000 + 50, 2) AS amount,
    toUInt16(rand(5) % 10 + 1) AS product_count,
    ['pending','paid','shipped','done'][(rand(6) % 4) + 1] AS status
FROM numbers(500000);  -- 生成 50 万行模拟数据
```

```sql
-- 查看 Part 信息（核心！理解数据物理组织）
SELECT
    partition,
    name,
    part_type,       -- Compact / Wide / InMemory
    rows,
    bytes_on_disk,
    modification_time
FROM system.parts
WHERE table = 'orders'
ORDER BY modification_time DESC;
```

预期输出解读：

```
┌─partition─┬─name──────────────┬─part_type─┬───rows─┬─bytes_on_disk─┬─modification_time─────┐
│ 202410    │ 202410_1_1_0      │ Wide      │ 169580 │      12345678 │ 2024-10-15 10:23:45.000 │
│ 202411    │ 202411_2_2_0      │ Wide      │ 164235 │      11876543 │ 2024-10-15 10:23:45.000 │
│ 202412    │ 202412_3_3_0      │ Wide      │ 165185 │      12012345 │ 2024-10-15 10:23:45.000 │
└───────────┴───────────────────┴───────────┴────────┴───────────────┴────────────────────────┘
```

- `partition` = 按月分区，三个分区各对应一个Part
- `name` = `202410_1_1_0` 解读: 分区名_最小Block编号_最大Block编号_版本号
- `part_type` = Wide 表示每个列对应独立的 `.bin` 文件
- `rows` = 每个Part的行数

```sql
-- 查看列的磁盘文件分布
SELECT
    column,
    type,
    data_compressed_bytes,
    data_uncompressed_bytes,
    compression_codec
FROM system.columns
WHERE table = 'orders';
```

```sql
-- 模拟一次INSERT，看看同一个分区是否会新增Part
INSERT INTO orders (order_id, user_id, region, order_date, amount, product_count, status)
VALUES (999999, 8888, '杭州', '2024-10-15', 999.99, 3, 'paid');

-- 再次查看Part——注意 202410 分区多了一个新Part！
SELECT partition, name, rows, part_type
FROM system.parts
WHERE table = 'orders' AND partition = '202410';

-- 手动触发Merge，将两个Part合并
OPTIMIZE TABLE orders PARTITION '202410' FINAL;

-- Merge后两个小Part变成一个大Part
SELECT partition, name, rows
FROM system.parts
WHERE table = 'orders' AND partition = '202410';
```

> **关键观察**：插入新数据后，202410 分区出现了两个Part（`202410_1_1_0` 和 `202410_4_4_0`）。执行 `OPTIMIZE` 后合并为一个。这就是MergeTree核心原理——**数据按Part组织，写入产生新Part，后台合并统一**。

#### Step 4：体验列式存储的性能优势

**目标**：对比全行扫描 vs 列裁剪的IO量差异。

```sql
-- 场景A：全表扫描所有列（模拟行式存储）
SELECT * FROM orders WHERE order_date = '2024-11-11';

-- 场景B：只查两列聚合（列式存储的典型OLAP查询）
SELECT region, SUM(amount) AS gmv
FROM orders
WHERE order_date BETWEEN '2024-10-01' AND '2024-12-31'
GROUP BY region
ORDER BY gmv DESC;
```

```sql
-- 用 EXPLAIN 查看索引命中情况
EXPLAIN indexes = 1
SELECT SUM(amount)
FROM orders
WHERE region = '杭州'
  AND order_date >= '2024-11-01';

-- 观察输出中的 KeyCondition 和 Granule 数量
```

预期EXPLAIN输出关键行解读：

```
Key condition: (column 0 in ['杭州', '杭州'])  -- 表示 sparse index 命中了 region 列
Marks selected  : final: 1                    -- 只需扫描 1 个 Granule（即8192行）
```

#### 完整代码清单

以上所有 SQL 按顺序执行即可完整复现。也可以保存为 `chapter-01.sql` 通过以下方式批量执行：

```bash
docker exec -i ch01-clickhouse clickhouse-client -u default --password colab2024 < chapter-01.sql
```

---

### 测试验证

用以下查询验证架构理解是否到位：

```sql
-- 验证 1：确认Part数量与分区数一致
SELECT COUNT(DISTINCT partition) AS total_partitions,
       COUNT(*) AS total_parts
FROM system.parts
WHERE table = 'orders' AND active = 1;

-- 验证 2：确认列压缩有效（压缩比应 > 1）
SELECT column, 
       data_compressed_bytes,
       data_uncompressed_bytes,
       round(data_uncompressed_bytes / data_compressed_bytes, 2) AS compression_ratio
FROM system.columns
WHERE table = 'orders'
ORDER BY compression_ratio DESC;

-- 验证 3：分区裁剪生效——查询单月只扫描对应Part
SELECT partition, name, rows
FROM system.parts
WHERE table = 'orders' AND partition = '202411';
```

**验收标准**：
1. `total_parts = total_partitions`（每个分区一个Part，说明Merge完成）
2. `compression_ratio > 2`（压缩有效）
3. 分区查询只在对应Part上扫描，不触发全表扫描

---

## 4. 项目总结

### 优点 & 缺点对比

| 维度 | ClickHouse（列存 OLAP） | MySQL（行存 OLTP） |
|------|------------------------|-------------------|
| 聚合查询 | 列裁剪 + 向量化，秒级响应百亿行 | 全行扫描，千万行需数分钟 |
| 写入性能 | 追加写入，数万行/秒 | 行锁竞争，高并发写入受限 |
| 压缩率 | 同列数据模式统一，压缩比 5-10x | 行内混合类型，压缩比 1-2x |
| 事务支持 | 弱事务（Mutation异步、非ACID） | 完整ACID事务 |
| 单行UPDATE/DELETE | 不支持原地更新，需Mutation | 支持，毫秒级 |
| 索引类型 | 稀疏索引（Granule级），Skip Index | B+Tree（行级），二级索引 |
| 并发查询 | 高吞吐，适合少量重型查询 | 高并发，适合大量轻量查询 |

### 适用场景

**5个典型场景**：

1. **日志分析**：Nginx/CDN/App埋点日志，按时间范围统计PV、UV、错误率 —— 典型的大批量扫描 + 聚合
2. **实时看板**：运营/BI仪表盘，按分钟/小时/天颗粒度展示GMV、订单量、转化率等核心指标
3. **用户行为分析**：留存分析、漏斗转化、用户画像标签计算——窗口函数 + 大量聚合
4. **时序数据**：IoT传感器、服务器监控指标——按时间分区，压缩率高，占用存储极小
5. **Ad-hoc OLAP查询**：数据分析师的多维度下钻、切片、旋转——需要快速响应的即席分析

**2个不适用场景**：

1. **OLTP事务系统**：电商下单、支付扣款、库存扣减——需要ACID事务和单行毫秒级更新，ClickHouse做不了
2. **频繁单行更新/删除**：比如用户资料变更、订单状态实时流转——Mutation会拖垮ClickHouse

### 注意事项

1. **不要把ClickHouse当主库用**：它的定位是分析型数据库，不能替代MySQL做在线事务。正确的架构是MySQL → Kafka → ClickHouse或Binlog同步，ClickHouse做只读分析。
2. **分区粒度不要太细**：按小时分区虽然查询快，但会导致上百万个Part，后台Merge跟不上，系统metadata膨胀甚至ZooKeeper挂掉。一般按天或按月分区。
3. **ORDER BY列的顺序至关重要**：排序键决定了稀疏索引，只有查询条件命中ORDER BY的**前列**才能利用索引裁剪。`ORDER BY (a,b,c)` 查询 `WHERE b=1` **不会用到索引**——这是最常见的踩坑点。

### 常见踩坑经验

**坑1：Part数量爆炸**

某电商团队把日志表按小时分区，结果每天产生24个Part，一个月720个Part，半年4000+个Part。后台Merge Threads跟不上产生速度，`system.merges` 积压严重，查询变慢。根因是分区太细 + 频繁写入小批次。解决方案：改为按天分区 + 每次INSERT攒够10000行再提交。

**坑2：Mutation拖垮系统**

某金融团队在10亿行表上执行 `ALTER TABLE ... DELETE WHERE status='expired'`，导致后台Mutation线程满负载运行两个小时，期间Merge被Block，Part越积越多，整个集群查询延迟飙升至30秒。根因是Mutation本质是全表扫描+重写Part，不适合大批量数据变更。解决方案：用TTL自动清理 + 查询时用WHERE过滤，而不是DELETE。

**坑3：排序键顺序搞反**

某团队建表 `ORDER BY (order_date, user_id)`，业务95%的查询是 `WHERE user_id=? AND order_date BETWEEN ? AND ?`。结果 `user_id` 在排序列第二位，无法利用稀疏索引，每次查询都在每个分区内做全分区扫描。根因是不理解**左缀匹配原则**——稀疏索引只对ORDER BY的前列生效。正确设计应该是 `ORDER BY (user_id, order_date)`。

### 思考题

1. **如果 ORDER BY 是 (a, b)，查询 `WHERE b=1` 会不会用到稀疏索引？为什么？**
2. **为什么 ClickHouse 的 Part 数量不能太多？Part 数量过大会有什么具体后果？请结合 MergeTree 的架构原理分析。**

> **提示**：思考题1请从稀疏索引的左缀匹配原理出发；思考题2请考虑Merge调度开销、系统表metadata压力、查询时Part扫描开销三个方面。

---

> **推广计划提示**：本章适合所有角色阅读。新人开发重点理解"列存 vs 行存"的思维转换；运维同学重点关注 Docker 部署和系统表监控；架构师关注四层架构图和选型边界。推荐下一章阅读《第2章：单机部署与客户端生态》，或者直接跳到第4章深入 MergeTree 引擎。
