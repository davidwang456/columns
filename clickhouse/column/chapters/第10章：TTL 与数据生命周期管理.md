# 第10章：TTL 与数据生命周期管理

> **版本**：ClickHouse 25.x LTS
> **定位**：基础篇核心章节。冷热分离，让存储成本可控——TTL 是 MergeTree 自带的数据治理利器，无需外部 cron 脚本。
> **前置阅读**：第4章（MergeTree 家族概览）、第5章（分区与排序键）
> **预计阅读**：30 分钟 | **实战耗时**：40 分钟

---

## 1. 项目背景

某金融科技公司（FinTech）的核心业务是聚合支付网关——每天产生数亿笔交易流水，涵盖支付宝、微信、银联等多渠道的收款、退款、对账记录。监管合规部门提出了三条硬性要求：

**要求一：90 天交易明细在线可查。** 所有交易记录的原始明细必须保留至少 90 天，且支持实时 SQL 查询，用于风控回溯和客诉处理。客服接到用户投诉"这笔钱扣了但没到账"，必须在 3 秒内查到该笔交易的完整链路。

**要求二：30 天后脱敏 PII（个人身份信息）。** 根据《个人信息保护法》和 GDPR 合规要求，交易记录中的姓名、手机号等个人敏感字段，在数据产生 30 天后必须自动脱敏或清除——即使是内部 DBA 也不能直接查询到明文的用户手机号。

**要求三：分级存储与到期销毁。** 90 天以上的历史数据不允许继续占用昂贵的 SSD 全闪存集群，需迁移至廉价 HDD 存储，保留 2 年供审计抽样。超过 2 年的数据必须彻底物理删除，不得留存。

在没有使用 TTL 之前，运维团队的日常是这样的：**每周五下午，DBA 老王手动跑一套 DELETE 脚本**——先 `ALTER TABLE transactions DELETE WHERE created_at < now() - INTERVAL 90 DAY`，再写一套 Python 脚本遍历所有列把 `user_name` 和 `phone` UPDATE 成空字符串。这套脚本跑一次至少 4 个小时，期间 MergeTree 的后台 Merge 被大量 Mutation 操作引爆——CPU 飙到 95%，IO Wait 突破 40%，业务查询全线超时。老王苦不堪言："每个周五都是我受难日，脚本跑完天都黑了。"

更糟糕的是一个深夜——老王迷糊中把 `>` 写成了 `<`，删掉了最近一天的数据而非 90 天前的数据。虽然从备份恢复了，但团队被扣了季度奖金。存储成本方面，全 SSD 集群每月账单高达 $20,000，而这些 SSD 上 80% 的数据其实根本没人查过。

**核心痛点**：没有自动化的数据生命周期管理，手动清理不仅低效、危险，还会引发 Merge 风暴拖垮整个集群。

---

## 2. 项目设计：剧本式交锋对话

周一晨会结束，大师在白板上写下一行字："**让数据自己管理自己。**" 小胖端着一杯咖啡晃进来，小白已经打开笔记本准备好提问。

**小胖**（看了一眼白板，不以为然）：“删数据还不简单？`DROP PARTITION` 不就行了？或者写个定时任务跑 `DELETE FROM transactions WHERE created_at < now() - INTERVAL 90 DAY`，crontab 一把梭嘛！我们以前 MySQL 都是这么干的。”

**大师**（转过身来）：“小胖，你用 `ALTER TABLE ... DELETE` 试过吗？它不是普通的 DELETE——它在 ClickHouse 里叫 **Mutation（变更）**。每执行一次，ClickHouse 会为受影响的每个 Part 创建一个新 Part——相当于把整个 Part 重写一遍。如果你每天跑一次 DELETE，你的表永远在重写，永远在合并，磁盘 IO 全被内部操作吃光。”

**小胖**（放下咖啡）：“这么狠？那 `DROP PARTITION` 呢？直接删整个分区，物理删除，总不需要重写了吧？”

**大师**：“`DROP PARTITION` 确实是物理删除，瞬间完成，不产生 Mutation。但这要求你的分区键和清理时间严格对齐——比如你按月分区，想删 90 天前的数据，只能整月整月地删。90 天前是 1 月 31 号，你 `DROP PARTITION 202501`，把 1 月 1 号到 1 月 31 号全删了——可 1 月 25 号到 1 月 31 号的数据还没满 90 天呢。”

**技术映射 #1**：`ALTER TABLE ... DELETE` 是异步 Mutation——会重写 Part，开销极大，不适合高频执行。`DROP PARTITION` 是元数据级删除，瞬间完成，但粒度受限于分区键设计，无法精确到天。

---

**小白**（抬起头，推了推眼镜）：“所以我们需要一种精确到行级别、又不需要手动干预的自动清理机制。我听说过 TTL，但不理解它到底什么时候生效——是数据一到期就立刻删除吗？比如我设置 `created_at + INTERVAL 30 DAY`，第 30 天零 1 秒之后，数据就没了？”

**大师**（赞许地点头）：“问到点子上了。TTL 是 **惰性（Lazy）触发**，不是实时触发。它不会在第 30 天凌晨零点零一秒帮你删数据——而是在后台 Merge 任务执行时，顺带检查每个 Granule 里的行是否到期。”

**小胖**（困惑）：“等 Merge？那如果某个 Partition 很久没有新数据写入，不触发 Merge，TTL 不就永远不生效了？”

**大师**：“正是！这就是 TTL 最大的'坑'。ClickHouse 的 Merge 调度器只有在需要合并 Part 时才会顺便检查 TTL。如果一个分区的 Part 已经很干净（比如只有一个大 Part），Merge 调度器不会主动去碰它——于是 TTL 就'躺平'了。但你可以通过 `ALTER TABLE ... MATERIALIZE TTL` 强制触发一轮 TTL 检查。”

**技术映射 #2**：TTL 的触发时机 = 后台 Merge 执行时。Merge 并非定时启动，而是在满足 `MergeTree` 内部策略（Part 数量/大小达到阈值）时才触发。无新写入 → 无新 Part → 无 Merge → TTL 不检查。`MATERIALIZE TTL` 是手动强制触发器。

---

**小白**（翻开笔记本第二页）：“那 TTL 有几种类型？我看文档上提到列级 TTL、表级 TTL，还有 `TO VOLUME` 和 `TO DISK`——这些有什么区别？”

**大师**（在白板上画了一个三层的金字塔）：“TTL 分三种层级，分别解决不同问题：

**第一层：列级 TTL（Column TTL）**——针对单个列。比如 `user_name String TTL created_at + INTERVAL 30 DAY`，意思是该行的 `created_at` 时间超过 30 天后，`user_name` 列的值自动被清空（设为该类型的默认值，String 默认为空字符串）。注意：是**清空列值**，不是删除整行。PII 脱敏场景正需要这个——行还在，但敏感字段没了。

**第二层：表级 TTL（Table TTL）**——针对整行/整个 Part。比如 `TTL created_at + INTERVAL 90 DAY DELETE`，意思是行超过 90 天后，整行被删除。当某个 Part 内的所有行都到期后，整个 Part 会被直接物理删除——这和 `DROP PARTITION` 一样，是 O(1) 的操作，不重写数据。

**第三层：冷热分层 TTL（Storage TTL）**——`TO VOLUME 'cold'` 或 `TO DISK 'hdd'`。它不做删除，而是把 Part 从一个存储卷迁移到另一个。比如 SSD 上的数据 7 天后自动搬到 HDD——你查询时感知不到任何区别，ClickHouse 会自动追踪 Part 的位置，但存储成本从天差到地别。”

**小胖**（眼睛亮了）：“等等，那这三种 TTL 能同时存在吗？比如我既要脱敏，又要冷热分离，还要到期删除？”

**大师**：“完全可以。一条 `ALTER TABLE ... MODIFY TTL` 可以串联多个 TTL 表达式。执行顺序是从左到右，每个 Part 在 Merge 时依次检查：先看列 TTL 到没到期 → 再看要不要移到冷存储 → 最后看整行要不要删除。一条 TTL 链搞定全部生命周期。”

**技术映射 #3**：TTL 三种层级对应数据生命周期的三个阶段——**保鲜期**（列级脱敏）→ **归档期**（冷热迁移）→ **销毁期**（表级删除）。链式 TTL 表达式按声明顺序执行，`+ INTERVAL N DAY` 表示在基准时间基础上叠加。

---

**小白**（又翻开一页）：“还有一个细节——如果一个 Part 里有 100 万行，其中 50 万行到期了、50 万行没到期，TTL 怎么办？是部分删除还是等整个 Part 都到期？”

**大师**：“这就是 TTL 与 Merge 交互最关键的地方。TTL 的判断单位不是 Part，而是**行（Row）**。在 Merge 过程中，ClickHouse 会逐行检查 TTL 表达式。如果某个 Part 中只有部分行到期：

- 对于**列级 TTL**：只清空到期行的对应列值，未到期的行保留原值。同一个 Part 内可能混合着'有手机号的行'和'手机号已被清空的行'。
- 对于**表级 DELETE**：如果整个 Part 的所有行都到期，直接删除整个 Part 文件；如果只有部分行到期，Merge 会重写这个 Part——保留未到期行，丢弃到期行。这正是 TTL 可能产生 Mutation 开销的情况。
- 对于**冷热迁移**：迁移的单位是 Part，不是行。如果 Part 中最早的一行已经满足迁移条件，整个 Part 会被搬到冷存储。

所以理想情况下，你的 TTL 表达式应该和分区键对齐——比如 `PARTITION BY toYYYYMM(created_at)`，这样每个分区内的行 TTL 到期时间基本相同，到期时直接整个 Part 删除，几乎零开销。”

---

## 3. 项目实战

### 环境准备

你需要一个支持多磁盘配置的 ClickHouse 实例。我们使用 Docker 挂载自定义存储配置：

首先，在宿主机上创建存储配置目录和冷数据目录：

```bash
mkdir -p ~/clickhouse-ch10/config.d
mkdir -p ~/clickhouse-ch10/cold
```

创建 ClickHouse 自定义存储配置文件 `~/clickhouse-ch10/config.d/storage.xml`：

```xml
<clickhouse>
  <storage_configuration>
    <disks>
      <default>
        <!-- 热数据：模拟 SSD -->
        <path>/var/lib/clickhouse/</path>
      </default>
      <cold_disk>
        <!-- 温/冷数据：模拟 HDD -->
        <path>/var/lib/clickhouse/cold/</path>
      </cold_disk>
    </disks>
    <policies>
      <hot_cold>
        <volumes>
          <hot>
            <disk>default</disk>
          </hot>
          <cold>
            <disk>cold_disk</disk>
          </cold>
        </volumes>
      </hot_cold>
    </policies>
  </storage_configuration>
</clickhouse>
```

启动 Docker 容器：

```bash
docker run -d --name ch10 \
  -p 8123:8123 -p 9000:9000 \
  -v ~/clickhouse-ch10/config.d:/etc/clickhouse-server/config.d \
  -v ~/clickhouse-ch10/cold:/var/lib/clickhouse/cold \
  clickhouse/clickhouse-server:25.4-alpine

# 等待容器启动完成，进入客户端
docker exec -it ch10 clickhouse-client
```

> **踩坑提醒**：冷数据目录 `/var/lib/clickhouse/cold/` 必须在容器内存在且 ClickHouse 有写入权限。Docker 的 `-v` 挂载会自动创建宿主机目录，但容器内的父目录 `/var/lib/clickhouse/` 必须已存在（默认镜像已包含）。

验证存储策略是否加载成功：

```sql
SELECT * FROM system.storage_policies WHERE policy_name = 'hot_cold';
```

---

### 分步实现

#### Step 1：创建带 TTL 的交易流水表

本章的核心表——一张涵盖列级 TTL（PII 脱敏）、冷热分层 TTL（成本优化）和表级 TTL（到期删除）的交易流水表：

```sql
CREATE DATABASE IF NOT EXISTS fintech;

CREATE TABLE fintech.transactions (
    tx_id        UInt64,
    user_id      UInt32,
    user_name    String TTL created_at + INTERVAL 30 DAY,  -- 【列级TTL】30天后清空姓名
    phone        String TTL created_at + INTERVAL 30 DAY,  -- 【列级TTL】30天后清空手机号
    amount       Decimal(10,2),
    status       LowCardinality(String),
    channel      LowCardinality(String),
    created_at   DateTime
) ENGINE = MergeTree()
ORDER BY (created_at, tx_id)
PARTITION BY toYYYYMM(created_at)
TTL created_at + INTERVAL 90 DAY DELETE                     -- 【表级TTL】90天后删除整行
SETTINGS storage_policy = 'hot_cold';
```

关键设计说明：
- `user_name` 和 `phone` 后面直接跟 `TTL created_at + INTERVAL 30 DAY`，这是列级 TTL 的内联声明方式。
- `TTL created_at + INTERVAL 90 DAY DELETE` 在表级别声明——任何行的 `created_at` 超过 90 天，整行将被标记删除。
- `SETTINGS storage_policy = 'hot_cold'` 指定使用我们刚才定义的多磁盘存储策略——后续冷热分层 TTL 才能生效。

---

#### Step 2：修改 TTL 加入冷热分层

现在给表追加冷热分层策略——最近 7 天的数据留在 SSD 热卷，7 天后自动迁移到 HDD 冷卷。我们用 `ALTER TABLE ... MODIFY TTL` 重新定义完整的 TTL 链：

```sql
ALTER TABLE fintech.transactions MODIFY TTL
    created_at + INTERVAL 7 DAY TO VOLUME 'cold',          -- 7天后迁到冷卷（模拟HDD）
    created_at + INTERVAL 30 DAY,                           -- 30天后清空PII列值
    created_at + INTERVAL 90 DAY DELETE;                    -- 90天后删除整行
```

> **语法说明**：`MODIFY TTL` 后面的多个 TTL 表达式按声明顺序**从左到右**执行。`TO VOLUME 'cold'` 中的 `cold` 是 `storage_policy` 中定义的卷名（不是磁盘名）。列级 TTL（`INTERVAL 30 DAY`）也可以在 `MODIFY TTL` 中统一声明——效果等同于在列定义时内联声明。

验证 TTL 定义是否生效：

```sql
SELECT
    name,
    engine_full
FROM system.tables
WHERE database = 'fintech' AND name = 'transactions'\G
```

输出中 `engine_full` 字段会包含完整的 TTL 表达式。

---

#### Step 3：插入模拟数据——横跨 120 天

我们需要一批覆盖 0 到 120 天的模拟交易数据，这样才能观察 TTL 的三阶段生效：

```sql
-- 插入 100 万行交易数据，创建时间分布在最近 120 天内
INSERT INTO fintech.transactions
SELECT
    number                                    AS tx_id,
    number % 1000                             AS user_id,
    concat('用户_', toString(number % 1000))   AS user_name,
    concat('138', toString(number % 10000000)) AS phone,
    round(randUniform(0.01, 50000), 2)        AS amount,
    ['pending','completed','failed','refunded'][rand() % 4 + 1] AS status,
    ['wechat','alipay','unionpay','bank'][rand() % 4 + 1]       AS channel,
    now() - INTERVAL (number % 120) DAY        AS created_at
FROM numbers(1000000);
```

数据分布说明：`number % 120` 使得 `created_at` 均匀分布在 `[now() - 119天, now()]` 之间——这意味着大约 25% 的数据（30 天前）已满足列级 TTL 条件，约 25% 的数据（90 天前）满足表级 DELETE 条件。

先看一眼原始数据——确认 `user_name` 和 `phone` 都有值：

```sql
SELECT
    formatDateTime(created_at, '%Y-%m-%d') AS day,
    count()                                 AS rows,
    min(user_name)                          AS sample_name,
    min(phone)                              AS sample_phone
FROM fintech.transactions
GROUP BY day
ORDER BY day ASC
LIMIT 10;
```

---

#### Step 4：观察 TTL 状态——system.parts 的 ttl_info

在 Merge 触发之前，TTL 不会生效。我们先看看当前 Part 的状态：

```sql
SELECT
    partition,
    name,
    rows,
    ttl_info,
    disk_name,
    formatReadableSize(bytes_on_disk) AS size
FROM system.parts
WHERE database = 'fintech'
  AND table = 'transactions'
  AND active = 1
ORDER BY partition;
```

输出示例：

```
┌─partition─┬─name──────────────┬──rows─┬─ttl_info──────────────────────────┬─disk_name─┬─size──────┐
│ 202501    │ 202501_1_1_0      │ 18423 │ ttl: [90 days] DELETE, [7 days] .. │ default   │ 2.34 MiB  │
│ 202502    │ 202502_2_2_0      │ 25140 │ ttl: [90 days] DELETE, [7 days] .. │ default   │ 3.12 MiB  │
│ ...       │ ...               │ ...   │ ...                                │ ...       │ ...       │
└───────────┴───────────────────┴───────┴────────────────────────────────────┴───────────┴───────────┘
```

`ttl_info` 字段显示该 Part 上定义的 TTL 规则。注意此时还没有 Part 被迁移或删除——因为 Merge 还没有触发。`disk_name` 全是 `default`（热卷）。

---

#### Step 5：强制触发 TTL

在生产环境中，你会等后台 Merge 自然触发 TTL。但在测试/演示场景下，我们需要立即看到效果——使用 `MATERIALIZE TTL`：

```sql
-- 强制触发 TTL：检查所有 Part，立刻执行 TTL 逻辑
ALTER TABLE fintech.transactions MATERIALIZE TTL;
```

`MATERIALIZE TTL` 会产生一条 **Mutation** 记录——它本质上是一个异步操作。查看突变任务进度：

```sql
SELECT
    command,
    parts_to_do,
    is_done,
    latest_fail_reason,
    create_time,
    formatReadableTimeDelta(elapsed) AS elapsed
FROM system.mutations
WHERE table = 'transactions' AND database = 'fintech'\G
```

当 `is_done = 1` 时，TTL 物化完成。这个过程可能需要几十秒到几分钟，取决于数据量。

---

#### Step 6：验证 TTL 三阶段效果

**验证一：列级 TTL——检查脱敏效果**

查询 30 天前的数据——`user_name` 和 `phone` 应该已被清空：

```sql
-- 查 30 天前的数据，PII 列应为空字符串
SELECT
    formatDateTime(created_at, '%Y-%m-%d') AS day,
    count()                                 AS total_rows,
    countIf(user_name = '')                 AS anonymized_names,
    countIf(phone = '')                     AS anonymized_phones,
    round(anonymized_names / total_rows * 100, 1) AS name_anonymize_pct
FROM fintech.transactions
GROUP BY day
HAVING created_at < now() - INTERVAL 30 DAY
ORDER BY day ASC
LIMIT 5;
```

预期结果：对于 `created_at < now() - INTERVAL 30 DAY` 的行，`user_name` 和 `phone` 全部为空字符串。

**验证二：冷热分层——检查 Part 迁移**

```sql
SELECT
    partition,
    disk_name,
    count()            AS parts,
    sum(rows)          AS total_rows,
    formatReadableSize(sum(bytes_on_disk)) AS total_size
FROM system.parts
WHERE database = 'fintech'
  AND table = 'transactions'
  AND active = 1
GROUP BY partition, disk_name
ORDER BY partition;
```

预期结果：距今 7 天以上的分区，`disk_name` 应为 `cold_disk`（而非 `default`）。最近 7 天的分区仍在 `default` 热盘上。

**验证三：表级 TTL——检查删除效果**

查询距今 90 天前的分区应该已经不存在（或被标记为非活跃）：

```sql
-- 看有哪些分区保留
SELECT DISTINCT partition
FROM system.parts
WHERE database = 'fintech'
  AND table = 'transactions'
  AND active = 1
ORDER BY partition;
```

距今 90 天以上的分区不应出现在结果中。也可以通过行数对比来验证：

```sql
SELECT
    count() AS current_total_rows
FROM fintech.transactions;
-- 预期：< 1,000,000（因为 90 天前的行被删除了）
-- 理论剩余：(90/120) * 1,000,000 = 750,000 行左右
-- （注意：具体数值取决于时间分布的均匀程度和 Merge 进度）
```

**验证四：综合观察——查看列级别的存储变化**

列级 TTL 清空值后，列的实际存储大小应该显著缩小：

```sql
SELECT
    name,
    formatReadableSize(sum(data_compressed_bytes)) AS compressed_size,
    formatReadableSize(sum(data_uncompressed_bytes)) AS uncompressed_size
FROM system.columns
WHERE database = 'fintech'
  AND table = 'transactions'
GROUP BY name
ORDER BY name;
```

预期结果：`user_name` 和 `phone` 列的压缩大小应远小于 `tx_id`、`amount` 等列——因为空字符串压缩效率极高（几乎为零）。

---

### 测试验证

```sql
-- 验证1：PII脱敏——30天前的姓名必须为空
SELECT count() AS still_has_name_count
FROM fintech.transactions
WHERE created_at < now() - INTERVAL 30 DAY
  AND user_name != '';
-- 预期：0

-- 验证2：冷热分离——7天前的分区必须在冷盘
SELECT count() AS hot_old_parts
FROM system.parts
WHERE database = 'fintech'
  AND table = 'transactions'
  AND active = 1
  AND disk_name = 'default'
  AND partition < formatDateTime(now() - INTERVAL 7 DAY, '%Y%m');
-- 预期：0

-- 验证3：到期删除——90天前的分区不应存在
SELECT count() AS expired_partitions
FROM system.parts
WHERE database = 'fintech'
  AND table = 'transactions'
  AND active = 1
  AND partition < formatDateTime(now() - INTERVAL 90 DAY, '%Y%m');
-- 预期：0
```

---

## 4. 项目总结

### TTL 类型对照表

| TTL 类型 | 作用范围 | 触发方式 | 磁盘影响 | 典型场景 |
|---------|---------|---------|---------|---------|
| 列级 TTL | 单列的值 | Merge 时清空到期列值 | 不释放磁盘（只设默认值） | PII 脱敏、敏感字段定期清除 |
| 表级 DELETE | 整行 / 整 Part | 整 Part 到期→直接删除；部分到期→Merge 重写 Part | 释放磁盘（删除 Part 文件） | 数据保留策略、合规到期删除 |
| TO VOLUME / TO DISK | 整 Part 迁移 | Merge 时迁移 Part 到目标存储 | 释放原存储空间 | SSD→HDD 冷热分层、存储成本优化 |
| GROUP BY TTL | 分组内的行 | 按分组键到期后聚合/删除 | 类似表级 DELETE | 时间窗口聚合、降采样 |

### 适用场景

- **日志保留策略**：Nginx/CDN 日志按天分区 + 90 天自动 DELETE，运维从此告别 cron 清理脚本。
- **GDPR/个保法合规**：用户手机号、身份证号 30 天后列级 TTL 清空——即使数据库被拖库，PII 也已脱敏。
- **存储成本优化**：热数据在 SSD，温数据迁移到 HDD，冷数据到期自动删除——总存储成本降低 60%~80%。
- **时序数据降采样**：原始秒级数据 7 天后通过物化视图聚合为分钟级，原始表 TTL 自动删除。
- **审计归档**：交易日志 2 年保留在廉价存储，2 年后彻底清除。

**不适用场景**：
- 需要精确到秒的实时删除（TTL 是惰性的，不是定时调度）。
- 列级 TTL 不适合频繁修改的列（Merge 开销累积）。
- 没有配置多磁盘策略时，`TO VOLUME` / `TO DISK` 不可用。

### 注意事项

1. **TTL 不是实时的**：Merge 触发才检查。如果某个分区不再有新数据写入且 Part 已完全合并，TTL 可能永远不执行。需要 `MATERIALIZE TTL` 来强制触发。
2. **列级 TTL 不清空 = 列值被设为零值**：对于 `String` 是空字符串 `''`，对于 `Int` 是 `0`，对于 `Nullable(String)` 是 `NULL`。业务查询需要用 `WHERE phone != ''` 来过滤已脱敏的行，或者用 `Nullable` 类型配合 `IS NULL` 判断。
3. **TO VOLUME 必须在 storage_policy 中预定义**：`TO VOLUME 'cold'` 中的卷名来自 `storage_policy` 配置中的 `<volumes>` 定义，不是随意写的字符串。集群所有节点需要相同的存储策略配置。
4. **表级 DELETE 可能产生 Mutation 开销**：如果 Part 中只有部分行到期，Merge 会重写 Part——这本质上是一次小型 Mutation。为减少此开销，让 TTL 表达式与 `PARTITION BY` 对齐。
5. **列级 TTL 清空值后，列文件不会立即缩小**：空字符串也是数据，需要等下一次 Merge 重新压缩才能释放空间。

### 常见踩坑经验

**坑 1：以为数据到期会立即删除**

某团队在上线 TTL 的第一天，DBA 反复查询 `SELECT count() FROM table WHERE created_at < now() - INTERVAL 90 DAY`，发现数据一条没少，怀疑 TTL 没生效。实际上 Merge 还没触发。**教训**：TTL 是惰性的，等 Merge 自然触发（可能需要数小时至数天），或者主动 `MATERIALIZE TTL`。建议在监控看板中加入 `system.parts` 中 `ttl_info` 字段的巡检，而非直接查行数。

**坑 2：列级 TTL 后业务代码爆炸**

某业务代码使用 `SELECT user_name FROM transactions WHERE tx_id = 12345` 来展示用户名，突然某天开始返回空字符串——前端报出"尊敬的  用户您好"。因为开发者不知道 30 天后的数据 `user_name` 会被清空。**教训**：列级 TTL 是一种"静默修改"，必须在接口层做防御处理（如 `IF(user_name = '', '***已脱敏***', user_name)`）。

**坑 3：频繁 MATERIALIZE TTL 导致 Mutation 堆积**

某运维为验证 TTL 效果，每隔 5 分钟跑一次 `MATERIALIZE TTL`，导致 `system.mutations` 堆积了上百条 Mutation 任务，写入性能降到个位数。**教训**：`MATERIALIZE TTL` 产生的是 Mutation 操作（和 `ALTER ... DELETE` / `ALTER ... UPDATE` 一样），不可高频执行。生产环境中每月执行一次足够，甚至永远不需要手动执行——让后台 Merge 自然处理即可。

### 思考题

1. **为什么 ClickHouse 的 TTL 设计成 Merge 时触发而非实时触发？这种设计有什么优势和劣势？**
   > 提示：考虑列存引擎的写入路径（INSERT → Part → 不修改已有文件）和 Merge 机制（唯一重写数据的时机）。如果做成实时触发（类似 MySQL 的 Event Scheduler），需要什么样的额外机制？对写入性能有何影响？

2. **如果一个 Part 中包含了 30 天前、60 天前和当天的数据各三分之一，执行 `MATERIALIZE TTL` 时会发生什么？列级 TTL 和表级 TTL 分别如何处理这个 Part？**
   > 提示：思考 Merge 过程是逐行检查还是逐 Part 判断。列级 TTL 和表级 DELETE 在处理"混合年龄 Part"时的行为有何不同？冷热迁移又是什么粒度？

---

> **下一章预告**：第 11 章《用户管理与权限控制》——创建只读分析员、数据写入者和管理员三种角色，用 ROW POLICY 实现租户级行隔离。让你的 ClickHouse 安全地开放给整个团队。
