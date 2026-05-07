# 第20章：Task 切分机制与表分片算法

## 1. 项目背景

某社交平台需要将用户关系表的 5 亿条数据从 MySQL 同步到 Hive。按照惯用配置，`splitPk=uid`（用户 ID）、`channel=20`。任务跑到一半发现——`taskId=0` 只花了 2 分钟就跑完了，而 `taskId=15` 跑了 45 分钟还在转。日志显示 taskId=0 读取了 100 万行，taskId=15 读取了 8000 万行！

排查发现 `uid` 不是自增列，而是基于注册时间的雪花算法 ID——前 40 位是时间戳，后 24 位是机器 ID + 序列号。运营早期注册了大量测试用户，uid 从 100000000 到 100050000，中间 500 万个连续 ID 但只有一个真实用户。而在 uid 500000000~800000000 区段，每个 ID 都对应一个真实用户。

默认的等距切分假设 `splitPk` 的值是均匀分布的——MIN 和 MAX 之间等距切 N 段。但实际数据并不均匀——结果就是木桶效应：20 个 Task 中最慢的那个拖累了整个 Job。

本章深入 DataX 的分片算法源码，理解四种切分策略的原理和适用场景，并亲手实现一个自适应分片算法来应对数据倾斜。

## 2. 项目设计——剧本式交锋对话

**（数据分析组，大屏幕上显示着 Task 耗时的柱状图——有一根柱子特别高）**

**小胖**：（指着屏幕）这也太夸张了吧！最慢的 Task 是最快的 22 倍！

**小白**：（快速看源码）根因在 `SingleTableSplitUtil.genSplitSql()`。它的逻辑很简单：

```java
// 伪代码
long min = SELECT MIN(splitPk) FROM table;  // 1
long max = SELECT MAX(splitPk) FROM table;  // 500000000
int n = adviceNumber;                        // 10
long step = (max - min) / n;                // 50000000

// 生成 10 个区间:
// [1, 50000000), [50000000, 100000000), ...
```

它假设数据在 [1, 500000000] 之间均匀分布。但实际上只有 10% 的区段有密集数据，90% 是空洞。

**技术映射**：等距切分 = 把一块瑞士奶酪（有洞的）按面积等分 10 块——有的块全是空洞（空洞无数据）、有的块全是奶酪（密集数据）。

**大师**：问题在这个 `step = (max - min) / n`。它只考虑了**值的范围**，没有考虑**值的分布**。改进方案有三种：

**方案 A：基于行数的切分（采样）**

```sql
-- 先采样找到每个区段的实际行数接近的点
SELECT splitPk FROM table ORDER BY splitPk 
LIMIT 1 OFFSET 1000000;   -- 第 100 万行的 splitPk 值
LIMIT 1 OFFSET 2000000;   -- 第 200 万行的 splitPk 值
```

用这些采样点的 splitPk 值作为分段边界——保证每段的数据量接近。

**方案 B：基于数据分布的切分（histogram）**

利用 MySQL 的 `histogram` 统计信息（MySQL 8.0+）获取数据分布直方图。

**方案 C：自适应 splitFactor**

在 `split()` 中根据总行数 / adviceNumber 计算每个 Task 的理想行数，再反推切分边界。如果某段过大，递归再切。

**小胖**：（挠头）这些方案听起来都很复杂——有没有简单粗暴的解法？

**大师**：有，最务实的一招——**明确告诉业务方，换一个分布均匀的分片键**。如果表有自增 ID，用它。如果只有不均匀的 uid，就在源表加一列 `batch_id = FLOOR(uid / 1000000)`，用 `batch_id` 做 splitPk。

## 3. 项目实战

### 3.1 步骤一：等距切分算法源码

打开 `plugin-rdbms-util/src/main/java/.../util/SingleTableSplitUtil.java`：

```java
public static String genSplitSql(String table, List<String> columns, 
        String splitPk, String where, long min, long max, int splitNum) {
    
    StringBuilder sql = new StringBuilder();
    sql.append("SELECT ").append(String.join(",", columns))
       .append(" FROM ").append(table);
    
    if (where != null && !where.isEmpty()) {
        sql.append(" WHERE ").append(where);
    }
    
    // 关键：计算步长
    long step = (max - min) / splitNum;
    
    // 拼接 WHERE 子句
    sql.append(" AND ").append(splitPk).append(" >= ? ");
    sql.append(" AND ").append(splitPk).append(" < ? ");
    
    return sql.toString();
    // 返回: SELECT * FROM table WHERE ... AND id >= 1 AND id < 50000
}
```

**切分边界生成**：

```java
public static List<String> split(int splitNum, long min, long max) {
    long step = (max - min) / splitNum;
    List<String> splits = new ArrayList<>();
    
    for (int i = 0; i < splitNum; i++) {
        long lower = min + i * step;
        long upper = (i == splitNum - 1) ? max + 1 : min + (i + 1) * step;
        splits.add(lower + "," + upper);
    }
    return splits;
    // 返回: ["1,50000", "50000,100000", ...]
}
```

### 3.2 步骤二：数据倾斜检测脚本

**目标**：在分片前检测数据分布是否均匀。

```sql
-- 步骤1: 获取总行数和 splitPk 范围
SELECT COUNT(*) as total_rows, MIN(id), MAX(id) FROM orders;
-- total_rows=500000000, MIN(id)=1, MAX(id)=500000000

-- 步骤2: 按 100 个区间判断每个区间的行数
SELECT 
    FLOOR(id / 5000000) as bucket,
    COUNT(*) as cnt
FROM orders 
GROUP BY bucket
ORDER BY bucket;

-- 结果示例：
-- bucket | cnt
-- 0      | 1200000   ← 前 500 万个ID只有 120 万行
-- 1      | 800000
-- ...
-- 95     | 8500000   ← 这 500 万个ID有 850 万行！分布严重不均
-- 96     | 9200000
-- 97     | 7800000
```

**Python 检测脚本**：

```python
import pymysql
import numpy as np

conn = pymysql.connect(host='10.0.1.100', user='root', password='x', db='trade')
cursor = conn.cursor()

# 查询分桶数据
cursor.execute("""
    SELECT FLOOR(id / 5000000) as bucket, COUNT(*) as cnt 
    FROM orders GROUP BY bucket ORDER BY bucket
""")
rows = cursor.fetchall()
cnts = [r[1] for r in rows]

# 计算变异系数 (CV = std/mean, > 0.5 表示严重倾斜)
cv = np.std(cnts) / np.mean(cnts)
print(f"数据倾斜系数 CV = {cv:.2f}")

if cv > 0.5:
    print("警告: 数据严重倾斜！建议使用自适应分片")
else:
    print("数据分布均匀，等距分片可行")
```

### 3.3 步骤三：自定义自适应分片算法

**目标**：实现按数据量均匀的分片算法。

```java
/**
 * 自适应分片算法：通过采样确定每个分段的数据量接近
 */
public static List<String> adaptiveSplit(
        Connection conn, String table, String splitPk, 
        int adviceNumber, long totalRows) {
    
    int samplesPerSplit = 3;  // 每个分段采 3 个点
    int totalSamples = adviceNumber * samplesPerSplit;
    long step = totalRows / totalSamples;
    
    List<Long> samplePKs = new ArrayList<>();
    samplePKs.add(getMin(conn, table, splitPk));
    
    // 每隔 {step} 行取一个采样点
    for (int i = 1; i < totalSamples; i++) {
        long offset = i * step;
        Long pk = queryValueAtOffset(conn, table, splitPk, offset);
        if (pk != null) samplePKs.add(pk);
    }
    
    samplePKs.add(getMax(conn, table, splitPk));
    
    // 从采样点中取 adviceNumber 个切分边界
    List<String> splits = new ArrayList<>();
    for (int i = 0; i < adviceNumber; i++) {
        long lower = samplePKs.get(i * samplesPerSplit);
        long upper = samplePKs.get(Math.min((i + 1) * samplesPerSplit, samplePKs.size() - 1));
        splits.add(lower + "," + upper);
    }
    
    return splits;
}

// 查询指定偏移量位置的 splitPk 值
private static Long queryValueAtOffset(Connection conn, String table, 
        String splitPk, long offset) {
    String sql = String.format(
        "SELECT %s FROM %s ORDER BY %s LIMIT 1 OFFSET %d",
        splitPk, table, splitPk, offset);
    try (Statement stmt = conn.createStatement();
         ResultSet rs = stmt.executeQuery(sql)) {
        if (rs.next()) return rs.getLong(1);
    }
    return null;
}
```

**问题**：LIMIT OFFSET 在大偏移量时性能极差（OFFSET 1 亿时要扫描前 1 亿行）。改进——用二分法找近似位置：

```java
// 二分查找法：在 [min, max] 中找第 k 行的 splitPk 值
private static long findApproxOffset(Connection conn, String table, 
        String splitPk, long k, long min, long max) {
    
    long lo = min, hi = max;
    while (lo <= hi) {
        long mid = lo + (hi - lo) / 2;
        
        // 统计小于 mid 的行数
        String sql = String.format(
            "SELECT COUNT(*) FROM %s WHERE %s < %d", table, splitPk, mid);
        long count = queryCount(conn, sql);
        
        if (count < k) {
            lo = mid + 1;
        } else if (count > k) {
            hi = mid - 1;
        } else {
            return mid;
        }
    }
    return lo;
}
```

### 3.4 步骤四：非数值分片键——字符串哈希切分

**目标**：当 splitPk 是 `VARCHAR` 类型的 UUID 时，使用哈希切分。

```sql
-- 方案1: CRC32 哈希取模（10 个 Task）
-- Task 0
SELECT * FROM orders WHERE MOD(CRC32(uuid), 10) = 0 AND $CONDITIONS;
-- Task 1
SELECT * FROM orders WHERE MOD(CRC32(uuid), 10) = 1 AND $CONDITIONS;
-- ...
```

```json
{
    "reader": {
        "parameter": {
            "connection": [{
                "querySql": [
                    "SELECT * FROM orders WHERE MOD(CRC32(uuid), 10) = ${taskIndex} AND $CONDITIONS"
                ]
            }]
        }
    }
}
```

**缺陷**：哈希取模无法利用索引——`MOD(CRC32(uuid), 10)` 是计算列，MySQL 每次都要全表扫描。对于几千万行的表，每个 Task 都要全表扫描 1/10 的数据——还不如 channel=1 单线程跑。

**更优方案**：用 UUID 的**字符前缀**做范围切分：

```sql
-- Task 0: uuid < '20000000-...'
-- Task 1: '20000000' <= uuid < '40000000-...'
-- Task 2: uuid >= '40000000-...'
```

前提：UUID 的字符分布相对均匀（MD5/SHA 生成的 UUID 在第 1-8 位基本均匀）。

### 3.5 可能遇到的坑及解决方法

**坑1：MIN/MAX 查询超慢**

表有 10 亿行但没有索引 → `SELECT MIN(id)` 全表扫描。

解决：确保 splitPk 列有索引（主键自带）。如果没有 — 先建索引。

**坑2：splitPk 跨天时的日期格式问题**

splitPk 是 `DATE` 类型时，等距切分的 `step` 计算的是天数差。

```java
// 对于 Date 类型分片键
long minMillis = dateColumn.getTime();
long maxMillis = dateColumn.getTime();
long stepMillis = (maxMillis - minMillis) / splitNum;
// 生成: WHERE dt >= '2026-01-01' AND dt < '2026-02-09'
```

如果 splitPk=DATE，step 可能不是整天数，导致分片边界落在 `2026-02-09 08:30:00`——不在整天边界上。

**坑3：复合主键的分片**

表主键是 `(org_id, user_id)`，需要按 `org_id` 为一级分片、`user_id` 为二级分片。但 DataX 原生的 splitPk 只支持单字段。

解决：用 querySql + 手动构造 10×10 的二级分片（100 个 Task，每个 WHERE `org_id IN (1,2,3) AND user_id >= ? AND user_id < ?`）。

## 4. 项目总结

### 4.1 四种分片策略对比

| 策略 | 算法 | 适用分片键类型 | 数据倾斜抗性 | CPU 开销 |
|------|------|-------------|-------------|---------|
| 等距切分 | (MAX-MIN)/N | 连续数值（自增ID） | 差 | 极低(2 次 SQL) |
| 哈希取模 | MOD(CRC32(col), N) | VARCHAR/UUID | 好 | 高(全表扫描) |
| 字符前缀 | col >= 'prefix1' | 均匀分布的字符串 | 中 | 低(索引扫描) |
| 自适应采样 | 按行数采样边界 | 分布不均的数值 | 好 | 中(N 次采样查询) |
| LIMIT分页 | LIMIT k OFFSET n | 任意 | 好 | 极高(大偏移量扫描) |

### 4.2 优点

1. **等距切分简单高效**：只需两次查询（MIN/MAX），适合 80% 场景
2. **可定制**：Reader 可以覆盖 split() 实现自己的切分策略
3. **adviceNumber 仅是建议**：插件可以根据数据特点自主决定真正的 Task 数
4. **支持多种分片键类型**：数值、字符串（CHAR/VARCHAR）、日期

### 4.3 缺点

1. **默认只支持等距切分**：遇到数据倾斜需要手动实现新算法
2. **不支持复合主键**：splitPk 只接受单字段
3. **非数值分片键需用户手动处理**：UUID 等类型需要用户在 querySql 中写 MOD
4. **采样方案在大表上开销大**：N 次 COUNT 查询在 10 亿行表上很慢

### 4.4 选型决策树

```
表有自增 ID 且分布均匀？
  ├─ 是 → 用 splitPk=id + 等距切分（最省事）
  └─ 否 → splitPk 是字符串 UUID？
        ├─ 是 → channel=1（小表）/ hash取模 + Custom split（大表）
        └─ 否 → splitPk 是数值但分布不均？
              ├─ 行数 < 5000万 → 使用自适应采样切分
              └─ 行数 > 5000万 → 先分离热点数据（where 过滤）再等距切分
```

### 4.5 注意事项

1. splitPk 必须是有索引的列（否则 MIN/MAX 全表扫描）
2. 分片 SQL 中的 `$CONDITIONS` 占位符不可省略（即使 channel=1）
3. 哈希切分只能用在有"至少一次"语义的场景（不能保证精确去重）
4. 自适应采样中的 OFFSET 查询在大偏移量时考虑用二分替代
5. 每个 Task 的理想数据量应保持在 50 万~500 万行之间（经验值）

### 4.6 思考题

1. 使用 `CRC32(uuid) % 10` 做哈希切分时，10 个 Task 的 CRC32 值分布是否完全均匀？如果不均匀，如何修正？
2. 如果一张表有 5 个分片键候选（id 自增、create_time 连续、user_id 随机、status 枚举、region 少基数），应该选哪个做 splitPk？请从索引利用率和数据分布两个维度分析。

（答案见附录）
