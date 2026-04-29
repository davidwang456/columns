# 第19章 Chunk切分策略：亿级大表优化

## 1 项目背景

### 业务场景：5亿行订单表的全量同步

某电商平台遇到一个棘手问题：核心订单表`orders`有**5亿行数据**（约500GB），需要从MySQL同步到Iceberg数据湖。使用Flink CDC的增量快照，但遇到了三个问题：

1. **全量快照慢**：5亿行数据用默认Chunk Size（8096）被切成**约6万个Chunk**，每个Chunk执行一次SELECT，MySQL的查询线程数飙升到60000+
2. **Chunk切分不均衡**：因为订单ID不是连续自增（存在年份+序号组合），有些Chunk范围间数据量差异巨大（有的Chunk有100万行，有的只有几百行）
3. **全量快照期间内存溢出**：每个Chunk的中间结果存在内存中，6万Chunk的元数据占用了大量TaskManager堆内存

解决这些问题需要深入理解`MySqlChunkSplitter`的切分算法，以及针对不同主键类型的优化策略。

### 切分策略类型

```
有主键表
  ├── 数字主键（自增ID）
  │   ├── 均匀分布 → 等距切分（默认）
  │   └── 不均匀分布 → 等宽切分（自动检测）
  ├── 字符串主键（UUID/VARCHAR）
  │   └── 采样切分
  ├── 联合主键
  │   └── 第一列作为切分键
  └── 自定义切分列（chunk-key-column）
      └── 用户指定列作为切分键

无主键表
  └── 单线程全表扫描（降级模式）
```

---

## 2 项目设计 · 三人交锋对话

**小胖**（困惑）：Chunk切分不就是把表按主键切成N段吗？MySQL的`BETWEEN AND`搞定，还能有不同的策略？

**大师**：看似简单的`BETWEEN AND`，在5亿行表上坑非常多。我们来看几个真实case：

**Case 1：自增ID空洞**
```sql
-- 主键id，因为DELETE和事务回滚，实际分布如下：
id: 1, 2, 3, 10001, 10002, 20001, 20002, 20003, ...
-- MIN=1, MAX=1000000
-- 等距切分成10个Chunk，每个Chunk范围: 100000行
-- Chunk 1: [1, 100000]   → 实际只有3行
-- Chunk 2: [100001, 200000] → 实际约90000行
-- 严重不均衡！
```

**Case 2：UUID主键**
```sql
-- 主键是UUID（VARCHAR(36)）
id: 'aaa001', 'aaa002', 'bbb001', 'ccc010', ...
-- 字符串范围切分困难，'aaa'~'azz'之间可能存在大量空隙
```

**小白**：那MySqlChunkSplitter是怎么检测"均匀分布"和"非均匀分布"的？还是一种策略适用所有情况？

**大师**：`MySqlChunkSplitter`内部实现了**自适应切分算法**。流程如下：

```
1. 查询 MIN(主键), MAX(主键), COUNT(*) → 获得表的基础信息
2. 如果数据量 < chunk.size → 不切分，单Chunk直接读取
3. 如果数据量 > chunk.size:
   a. 尝试等距切分（预设N个Chunk）
   b. 采样验证：随机读取几个Chunk的实际数据量
   c. 如果Chunk间数据量差异 < 阈值（20%）→ 等距切分有效
   d. 如果Chunk间数据量差异 > 阈值 → 退化为等宽切分
      → 执行`SELECT MAX(pk) FROM (SELECT pk FROM table ORDER BY pk LIMIT offset, size)` 
        获取每个Chunk的精确边界
```

**技术映射**：Chunk切分就像"切蛋糕"——等距切分是按尺寸切（每个人得到的块一样大），等宽切分是按重量切（每一块包含的数据行数一样多）。如果你知道蛋糕密度均匀（均匀分布）就用等距切分（快）；如果你不知道密度分布（不均匀分布）就先称重量再切（等宽切分，慢但准）。

**小白**：那如果自增ID空洞的情况发生了，Chunk之间的数据量严重不均，会导致什么问题？

**大师**：最直接的问题——**拖后腿效应（Straggler Effect）**。并行度=16，如果15个Chunk在5秒内读完了，但第16个Chunk因为数据量是其他Chunk的10倍，需要50秒。整个快照阶段被这个"慢Chunk"拖累，总时间=50秒而不是5秒。

更严重的是内存问题：大Chunk缓存的数据量大，可能导致TaskManager OOM。

**解决方案**：调小`chunk.size`，让Chunk更小更多，降低单Chunk的数据量。

---

## 3 项目实战

### 分步实现

#### 步骤1：分析表的数据分布

```sql
-- 1. 查看表的基本信息
SELECT 
    COUNT(*) AS total_rows,
    MIN(id) AS min_id,
    MAX(id) AS max_id,
    (MAX(id) - MIN(id)) AS id_range
FROM user_action_log;

-- 2. 检查主键的"空洞率"
-- 空洞率 = (id范围 - 实际行数) / id范围 × 100%
-- 空洞率高说明自增ID不连续
SELECT 
    (MAX(id) - MIN(id) - COUNT(*) + 1) AS holes,
    ROUND((MAX(id) - MIN(id) - COUNT(*) + 1) / MAX(id) * 100, 2) AS hole_pct
FROM user_action_log;

-- 3. 采样检查数据分布（按主键等分100份，统计每份的实际行数）
-- 这可以判断分布是否均匀
SELECT
    CEIL(id / (MAX(id) / 100)) AS bucket,
    COUNT(*) AS cnt,
    MIN(id) AS range_start,
    MAX(id) AS range_end
FROM user_action_log
GROUP BY bucket
ORDER BY bucket;
```

#### 步骤2：自定义切分列——用非主键列作为Chunk切分键

有些表的自增主键和数据分布无关（比如插入顺序和业务ID不相关），更合适的切分列可能是业务创建时间等。

```yaml
source:
  type: mysql
  tables: shop.user_action_log
  # 自定义切分列——指定使用user_id作为切分键
  chunk-column:
    shop.user_action_log: user_id
```

Java代码配置：
```java
MySqlSource<String> source = MySqlSource.<String>builder()
    .hostname("localhost").port(3306)
    .databaseList("shop").tableList("shop.user_action_log")
    .username("cdc_user").password("cdc_pass")
    .deserializer(new JsonDebeziumDeserializationSchema())
    .serverId("5400-5403")
    .startupOptions(StartupOptions.initial())
    .chunkKeyColumn(
        // 为每张表指定切分列
        new Object[][]{
            {"shop.user_action_log", "user_id"}
        })
    .build();
```

**注意：** 自定义切分列必须有索引，否则`ORDER BY chunk_column LIMIT`会触发全表排序，性能极差。

#### 步骤3：处理Chunk元数据内存问题

```java
import org.apache.flink.cdc.connectors.mysql.source.MySqlSource;
import org.apache.flink.cdc.connectors.mysql.table.StartupOptions;

/**
 * 大表Chunk切分配置优化
 */
public class ChunkOptimizedSource {

    public static MySqlSource<String> create() {
        return MySqlSource.<String>builder()
            .hostname("localhost").port(3306)
            .databaseList("shop").tableList("shop.user_action_log")
            .username("cdc_user").password("cdc_pass")
            .deserializer(new JsonDebeziumDeserializationSchema())
            .serverId("5400-5403")
            .startupOptions(StartupOptions.initial())

            // ========== Chunk优化配置 ==========
            .splitSize(2000)                // 调小Chunk Size(默认8096)
                                            // → 单Chunk内存降低
                                            // → Chunk总数变多
                                            // → 更细粒度，更好的并行

            .splitMetaGroupSize(500)        // 元数据组大小(Chunk元数据的批处理单位)
                                            // 元数据不是一次性加载，而是按组加载
                                            // → 降低元数据内存占用

            .distributionFactorLower(0.05)  // Chunk最小分布系数
            .distributionFactorUpper(100.0) // Chunk最大分布系数
                                            // → 控制Chunk均匀性检测的灵敏度
            .build();
    }
}
```

#### 步骤4：监控Chunk读取进度

自定义Source Reader监听器来监控Chunk进度：

```java
import org.apache.flink.api.common.functions.RichMapFunction;
import org.apache.flink.metrics.Gauge;

/**
 * 监控Chunk进度——在大表全量快照时了解当前进度
 */
public class ChunkProgressMonitor extends RichMapFunction<String, String> {

    private int totalChunks;
    private int completedChunks;
    private double progress;

    @Override
    public void open(Configuration parameters) {
        getRuntimeContext().getMetricGroup()
            .gauge("snapshot_progress_pct",
                (Gauge<Double>) () -> progress);
        getRuntimeContext().getMetricGroup()
            .gauge("completed_chunks",
                (Gauge<Integer>) () -> completedChunks);
    }

    @Override
    public String map(String value) throws Exception {
        if (value.contains("\"op\":\"r\"")) {
            completedChunks++;
            // 从外部系统读取totalChunks（或在作业启动时注入）
            // progress = (double) completedChunks / totalChunks * 100;
        }
        return value;
    }
}
```

#### 步骤5：大表全量快照的最佳配置清单

```yaml
source:
  type: mysql
  hostname: localhost
  port: 3306
  username: cdc_user
  password: cdc_pass
  tables: shop.user_action_log
  server-id: 5400-5407

  # 大表优化配置
  scan.incremental.snapshot.chunk.size: 2000     # 小Chunk，避免OOM
  scan.snapshot.fetch.size: 1024                 # 每批读取行数
  scan.incremental.snapshot.chunk.key.columns:   # 自定义切分列
    shop.user_action_log: id

  # 连接池优化
  connect.timeout: 30s
  connect.max-retries: 5
  pool.size: 8                                   # MySQL连接池大小（匹配并行度）
  
  # Debezium优化
  debezium:
    snapshot.locking.mode: none                  # 不锁表
    min.row.count.to.stream.results: 100000      # 超过10万行使用流式读取
    query.fetch.size: 1024

pipeline:
  parallelism: 1                                 # Sink并行度保持1
```

#### 常见陷坑及解决方法

| 问题 | 原因 | 解决方法 |
|------|------|---------|
| Chunk读取超时 | 单个Chunk数据量过大，SELECT超过`connect.timeout` | 调小`chunk.size`，或增大`connect.timeout` |
| Chunk之间数据严重不均 | 主键分布极不均匀，等距切分失效 | 启用等宽切分，或设置更小的`distributionFactorUpper` |
| 自定义切分列导致全表扫描 | 切分列不是索引列 | 为切分列添加索引，或使用主键 |
| Chunk比预期多很多 | `chunk.size`和`split_key.even.distribution`配置冲突 | 检查配置，确保`chunk.size`和`splitMetaGroupSize`匹配 |
| 快照阶段MySQL连接数过高 | 并行度*Chunk并发数 > MySQL max_connections | 调低并行度，或在MySQL端增大`max_connections` |

---

## 4 项目总结

### Chunk切分策略决策表

| 主键类型 | 切分策略 | 说明 | 推荐Chunk Size |
|---------|---------|------|---------------|
| INT自增 | 等距切分（均匀分布） | IDFA分布 = 实际行数，Chunk均匀 | 5000~10000 |
| INT自增（大量DELETE） | 等宽切分（不均匀分布） | 空洞率高，等距切分严重不均 | 2000~5000 |
| BIGINT无符号 | 等距切分 | 注意Java类型溢出 | 5000~10000 |
| VARCHAR(UUID) | 采样切分 | 需要先采样获取分布 | 1000~3000 |
| 联合主键(2列) | 使用第一列 | 第一列分布均匀性决定切分效果 | 见第一列类型 |
| 无主键 | 全表扫描（降级） | 单线程，无法并行 | N/A |

### Chunk优化核心公式

```
Chunk总数 = CEIL(总行数 / chunk.size)

理想Chunk大小（行）= 总行数 / 并行度 / 每Chunk处理时间

单Chunk内存 ≈ chunk.size × 单行大小 × (1 + 序列化因子)

建议：
  - Chunk总数建议在 并行度 × 10 ~ 并行度 × 50 之间
  - 单Chunk读取时间建议在 1~5秒 之间
  - 单Chunk内存建议控制在 50MB以内
```

### 常见踩坑经验

**故障案例1：Chunk切分遇到数据倾斜——99%数据在一个Chunk中**
- **现象**：增量快照99%的Chunk秒级完成，但最后一个Chunk跑了2小时
- **根因**：等距切分+自增ID空洞。`id BETWEEN [1, 2000]` 范围内有1990行，但`[2001, 4000]`范围内只有10行。实际数据集中在某些范围
- **解决方案**：启用手动Chunk列配置，选择一个分布更均匀的列（如`create_time`），或使用等宽切分

**故障案例2：Chunk读取返回重复数据**
- **现象**：全量数据中出现了重复的主键记录
- **根因**：Chunk边界重叠。两个相邻Chunk的`[id >= 1000 AND id < 2000]`和`[id >= 2000 AND id < 3000]`如果边界条件不是严格互斥（比如一个用`>=`一个用`>`），会导致id=2000的行出现在两个Chunk中
- **解决方案**：升级到Flink CDC 3.0.3+（修复了Chunk边界重叠的bug），或确保Chunk切分使用`[start, end)`半开区间

**故障案例3：增量快照阶段直接跳过（没有快照输出）**
- **现象**：配置了`startupOptions.initial()`但Source只输出了增量Binlog，没有全量快照
- **根因**：表是空表（COUNT(*)=0），ChunkSplitter不会切分Chunk，直接跳过快照阶段
- **解决方案**：这是正常行为。空表不需要全量快照，直接从增量流开始

### 思考题

1. **进阶题①**：一张表有联合主键`(year, month, order_id)`，其中`year`和`month`只有少数几种取值（如2024年的1~12月）。如果使用默认切分策略，Chunk会怎么样分布？应该如何配置自定义切分列来优化切分？

2. **进阶题②**：Flink CDC的`MySqlChunkSplitter.distributionFactorUpper`和`distributionFactorLower`参数控制什么？如果设置`distributionFactorUpper=1.0`会发生什么？提示：结合`MySqlChunkSplitter.attemptSplitUsingDistributionStrategy`源码分析。

---

> **下一章预告**：第20章「Schema Evolution：DDL变更自动同步」——源库表结构变更后，Flink CDC如何自动同步到目标系统？深入Schema变更的5种处理模式（IGNORE/LENIENT/TRY_EVOLVE/EVOLVE/EXCEPTION）以及SchemaOperator的实现机制。
