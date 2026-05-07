# 第6章：MySQL Reader——从配置到数据读取全流程

## 1. 项目背景

数据团队接到需求：将核心交易库的 `orders` 表（1.2 亿行，约 300GB）同步到数据仓库。DBA 要求：第一，必须在业务低峰期（凌晨 0-6 点）完成，不能影响在线交易；第二，源库的 `vendor_code` 字段是加密存储的，Reader 读取时需要调用解密函数；第三，同步不能锁表，源库有持续的写入流量。

团队最初计划用 sqoop 的 `--split-by` + `--boundary-query`，但遇到了两个问题：一是 sqoop 的 `--query` 模式要求 SQL 中必须有 `$CONDITIONS` 占位符，加密函数的参数化写法非常拗口；二是 sqoop 默认使用 `SELECT *`，导致大字段 `remark(text)` 在分片边界查询时产生了严重的 IO 压力。

切换到 DataX MySQL Reader 后，团队发现一个配置就能覆盖上述所有需求：用 `querySql` 模式写自由 SQL（包含解密函数 + 列裁剪），用 `splitPk` 指定分片键，配置 `session` 参数降低事务隔离级别避免锁表。本章带你从源码级别理解 MySQL Reader 的完整工作链路——从 JDBC 连接建立，到 ResultSet 逐行映射为 Column，再到通过 RecordSender 送入 Channel。

## 2. 项目设计——剧本式交锋对话

**（数据库操作间，DBA 老王盯着监控屏幕）**

**小胖**：（啃着苹果）老王，你说 DataX 的 MySQL Reader 到底是怎么读取数据的？不就是 SELECT * FROM table 吗？有啥复杂的？

**老王**：（白了他一眼）你以为是查 Excel 呢？1.2 亿行的表，你一个 SELECT * 下去，源库内存直接飙到 90%。DataX 要做分片并发读——把 1.2 亿行按主键切成 N 段，每个线程读一段，这样既快又不打垮源库。

**小白**：（翻着源码）其实是三步：首先，Reader.Job.split() 查询 `SELECT MIN(id), MAX(id) FROM orders` 得到主键范围；然后按 channel 数等距切分——比如 MIN=1, MAX=1.2 亿，channel=10，就切成 [1, 1200万], [1200万, 2400万] ... [1.08亿, 1.2亿]；最后每个 Task 替掉 `WHERE id >= ? AND id < ?` 去并发查询。

**技术映射**：分片并发读 = 把一本 1200 页的书拆成 10 份，雇 10 个人同时抄写。第一个人抄 1-120 页，第二个抄 121-240 页……互不干扰，10 倍速度。

**小胖**：（瞪大眼睛）那万一主键不是连续的怎么办？比如删过数据，ID 从 1 跳到 100 万，又跳到 500 万。

**大师**：（推门进来）问得好。这就是 `splitPk` 的局限——它假设主键是连续且均匀分布的。如果 ID 有大量空洞，某些分段可能只有几百行，某些分段有几千万行。这就是"数据倾斜"的根源。

**小白**：（追问）那能不能不用 splitPk 切分？比如用 ORDER BY LIMIT OFFSET 分页式切分？

**大师**：技术上可以，但性能很差。`LIMIT 12000000, 1200000` 这种大偏移量查询，MySQL 需要扫描并跳过前 1200 万行才能拿到目标数据。分页式切分在第 100 个 Task 时，每个查询都要扫描前面所有的行。而 `WHERE id >= ? AND id < ?` 能直接用主键索引定位，时间复杂度是 O(1)。

**小胖**：那如果我的表没有合适的分片键呢？比如没有自增 ID，只有一个 UUID 字符串主键？

**大师**：这时候有两个选择：
1. **数字哈希切分**：`WHERE MOD(CONV(SUBSTRING(MD5(uuid), 1, 8), 16, 10), 10) = 0`——把 UUID 转成哈希值后取模，缺点是无法用索引。
2. **直接取消切分**：设 `channel=1`，单 Task 读取全表。对中小数据量（< 500 万行）这是最稳妥的方式。

## 3. 项目实战

### 3.1 步骤一：基础配置——table 模式 vs querySql 模式

**目标**：掌握两种读模式的配置方式和适用场景。

**模式 A：table + column + where（声明式）**

```json
{
    "reader": {
        "name": "mysqlreader",
        "parameter": {
            "username": "reader_user",
            "password": "reader_pwd",
            "column": ["id", "user_name", "amount", "status", "create_time"],
            "splitPk": "id",
            "where": "status != -1 AND create_time >= '2026-01-01'",
            "connection": [{
                "table": ["orders"],
                "jdbcUrl": ["jdbc:mysql://10.0.1.100:3306/trade_db"]
            }]
        }
    }
}
```

优点：简洁，DataX 自动拼接 `SELECT id, user_name, amount, status, create_time FROM orders WHERE status != -1 AND create_time >= '2026-01-01'`。

缺点：无法做复杂 JOIN、子查询、函数调用。

**模式 B：querySql（自定义 SQL）**

```json
{
    "reader": {
        "name": "mysqlreader",
        "parameter": {
            "username": "reader_user",
            "password": "reader_pwd",
            "column": ["*"],
            "splitPk": "id",
            "connection": [{
                "querySql": [
                    "SELECT id, user_name, AES_DECRYPT(vendor_code, 'secret_key') as vendor_code, amount, status, create_time FROM orders WHERE status = 1"
                ],
                "jdbcUrl": ["jdbc:mysql://10.0.1.100:3306/trade_db"]
            }]
        }
    }
}
```

**关键限制**：使用 querySql 时，**必须在 WHERE 条件中以 `$CONDITIONS` 占位符标记**，DataX 会自动替换为 `(id >= ? AND id < ?)` 或 `(1=1)`（如果不切分）：

```json
"querySql": [
    "SELECT * FROM orders WHERE status = 1 AND $CONDITIONS"
]
```

如果忘记写 `$CONDITIONS`，当 `channel > 1` 时 DataX 会抛：
```
ERROR - querySql 中未包含 $CONDITIONS 占位符，无法进行 Task 切分
```

当 `channel = 1` 时，`$CONDITIONS` 会被替换为 `1=1`，等价于全表扫描。

### 3.2 步骤二：理解连接池参数

**目标**：配置合适的 Druid 连接池参数，避免连接耗尽。

```json
{
    "reader": {
        "name": "mysqlreader",
        "parameter": {
            "connection": [{
                "jdbcUrl": [
                    "jdbc:mysql://10.0.1.100:3306/trade_db?useUnicode=true&characterEncoding=utf8&useSSL=false&serverTimezone=Asia/Shanghai&tinyInt1isBit=false"
                ]
            }]
        }
    }
}
```

**JDBC URL 关键参数**：

| 参数 | 建议值 | 原因 |
|------|--------|------|
| `useSSL=false` | false | 内网环境关闭 SSL，减少握手开销 |
| `useUnicode=true&characterEncoding=utf8` | utf8/utf8mb4 | 避免中文乱码 |
| `serverTimezone=Asia/Shanghai` | 按实际时区 | 防止 DateColumn 8 小时偏移 |
| `tinyInt1isBit=false` | false | 防止 tinyint(1) 被误识别为 BoolColumn |
| `connectTimeout=5000` | 5000ms | 连接超时不宜过长 |
| `socketTimeout=180000` | 180000ms(3分钟) | socket 超时需大于单 Task 最长执行时间 |

### 3.3 步骤三：追踪 common-rdbms-util Reader 源码链路

**目标**：阅读 `CommonRdbmsReader.Task.startRead()` 源码，理解数据从 JDBC ResultSet 到 Record 的完整转换链。

打开 `plugin-rdbms-util/src/main/java/com/alibaba/datax/plugin/rdbms/reader/CommonRdbmsReader.java`：

```java
// Reader.Task.startRead 的核心流程
public class Task {
    private static final ThreadLocal<Connection> connHolder = new ThreadLocal<>();
    
    public void startRead(RecordSender recordSender) {
        // 1. 获取数据库连接（每个Task独立连接）
        Connection conn = DBUtil.getConnection(
            this.dataBaseType, jdbcUrl, username, password);
        conn.setReadOnly(true);  // 只读模式，避免意外修改
        conn.setAutoCommit(false); // 关闭自动提交，避免长时间锁表
        
        // 2. 构建查询SQL
        // querySql = "SELECT * FROM orders WHERE id >= ? AND id < ?"
        String querySql = this.readerSliceConfig.getString("querySql");
        
        // 3. 执行查询（流式读取）
        PreparedStatement ps = conn.prepareStatement(
            querySql, 
            ResultSet.TYPE_FORWARD_ONLY,      // 只向前滚动
            ResultSet.CONCUR_READ_ONLY         // 只读
        );
        // 关键：设置 fetchSize = Integer.MIN_VALUE，启用MySQL流式读取
        ps.setFetchSize(Integer.MIN_VALUE);   // !!!重要!!!
        
        // 设置分片参数
        ps.setLong(1, lowerBound);   // WHERE id >= ?
        ps.setLong(2, upperBound);   // WHERE id < ?
        
        ResultSet rs = ps.executeQuery();
        
        // 4. 逐行读取 → 构建 Record
        ResultSetMetaData metaData = rs.getMetaData();
        int columnCount = metaData.getColumnCount();
        
        while (rs.next()) {
            // 创建一条空Record
            Record record = recordSender.createRecord();
            
            // 逐列转换为DataX Column类型
            for (int i = 1; i <= columnCount; i++) {
                Column column = buildColumn(rs, metaData, i);
                record.addColumn(column);
            }
            
            // 发送Record到Channel
            recordSender.sendToWriter(record);
        }
        
        // 5. 发送结束标记
        recordSender.terminate();
    }
    
    // 类型映射：JDBC Types → DataX Column
    private Column buildColumn(ResultSet rs, ResultSetMetaData meta, int index) {
        int jdbcType = meta.getColumnType(index);
        
        switch (jdbcType) {
            case Types.TINYINT:
            case Types.SMALLINT:
            case Types.INTEGER:
            case Types.BIGINT:
                long longVal = rs.getLong(index);
                if (rs.wasNull()) return new LongColumn(null);
                return new LongColumn(longVal);
                
            case Types.FLOAT:
            case Types.DOUBLE:
            case Types.DECIMAL:
            case Types.NUMERIC:
                // 注意：使用 getBigDecimal + doubleValue 存在精度损失风险
                BigDecimal decimal = rs.getBigDecimal(index);
                if (rs.wasNull()) return new DoubleColumn(null);
                return new DoubleColumn(decimal.doubleValue()); // ← 精度损失点！
                
            case Types.VARCHAR:
            case Types.CHAR:
            case Types.LONGVARCHAR:
                String strVal = rs.getString(index);
                if (rs.wasNull()) return new StringColumn(null);
                return new StringColumn(strVal);
                
            case Types.DATE:
            case Types.TIME:
            case Types.TIMESTAMP:
                Timestamp ts = rs.getTimestamp(index);
                if (rs.wasNull()) return new DateColumn(null);
                return new DateColumn(new Date(ts.getTime())); // ← 时区转换点！
                
            case Types.BOOLEAN:
            case Types.BIT:
                boolean boolVal = rs.getBoolean(index);
                if (rs.wasNull()) return new BoolColumn(null);
                return new BoolColumn(boolVal);
                
            case Types.BINARY:
            case Types.VARBINARY:
            case Types.LONGVARBINARY:
            case Types.BLOB:
                byte[] bytes = rs.getBytes(index);
                if (rs.wasNull()) return new BytesColumn(null);
                return new BytesColumn(bytes);
                
            default:
                throw DataXException.asDataXException(
                    DBUtilErrorCode.UNSUPPORTED_TYPE,
                    "不支持的JDBC类型: " + jdbcType);
        }
    }
}
```

### 3.4 步骤四：fetchSize 调优验证

**目标**：对比 `fetchSize=默认值` 和 `fetchSize=Integer.MIN_VALUE` 对内存和性能的影响。

| fetchSize | MySQL JDBC 行为 | 内存影响 | 适用场景 |
|-----------|----------------|---------|---------|
| 0（默认） | JDBC 驱动一次性拉取全部行到客户端内存 | 1 亿行 × 200B ≈ 20GB 内存，OOM | 小数据量 < 1000 行 |
| Integer.MIN_VALUE | 启用流式读取，逐行从服务端游标获取 | 内存中只存当前行，约 200B | 所有大数据量场景 |

验证脚本——在源端 MySQL 上观察连接状态：

```sql
-- 观察 DataX 连接：fetchSize=0（默认）时，连接状态为"读取结果集"
SHOW PROCESSLIST;
-- | Id | State              | Info                          |
-- | 15 | Sending data       | SELECT * FROM orders WHERE... |

-- fetchSize=Integer.MIN_VALUE 时，连接状态为"等待客户端消费"
-- | Id | State                               | Info |
-- | 15 | Writing to net                      | NULL |
```

### 3.5 步骤五：session 参数优化

**目标**：通过 session 参数避免锁表、降低事务隔离级别、设置超时。

```json
"parameter": {
    "connection": [{
        "jdbcUrl": ["jdbc:mysql://..."]
    }],
    "session": [
        "SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED",
        "SET innodb_lock_wait_timeout = 3",
        "SET net_write_timeout = 600"
    ]
}
```

| session 参数 | 作用 | 风险 |
|-------------|------|------|
| `READ UNCOMMITTED` | 允许脏读，不加共享锁，不阻塞写入 | 可能读到未提交的数据，但全量同步通常可接受 |
| `innodb_lock_wait_timeout=3` | 获取锁超时 3 秒就放弃 | 超时后 Task 失败，可能导致重跑 |
| `net_write_timeout=600` | socket 写入超时 10 分钟 | 过长可能掩盖网络问题 |

### 3.6 可能遇到的坑及解决方法

**坑1：`communications link failure`**

现象：任务运行到一半，Reader 报连接断开。

原因：MySQL 的 `wait_timeout` 默认 8 小时，但如果有防火墙/负载均衡器，可能在更短时间内（如 1 小时）强制断开空闲连接。

解决：
```json
"session": [
    "SET SESSION wait_timeout = 86400",
    "SET SESSION interactive_timeout = 86400"
]
```

**坑2：敏感字段解密后乱码**

解决：在 querySql 中做类型转换，确保解密结果能被 DataX 正确映射：
```sql
SELECT id, CAST(AES_DECRYPT(encrypted_field, 'key') AS CHAR) AS plain_field FROM orders
```

**坑3：MySQL 连接数超限**

现象：`Data source rejected establishment of connection, message from server: "Too many connections"`

计算：`连接数 = channel数 × 2（Reader+Writer连接池） + Druid 预留连接`

解决：降低 channel 数，或在 MySQL 端调大 `max_connections`。

## 4. 项目总结

### 4.1 MySQL Reader 参数完整清单

| 参数 | 必填 | 类型 | 说明 |
|------|------|------|------|
| `username` | 是 | string | 数据库用户名 |
| `password` | 是 | string | 数据库密码 |
| `column` | 是 | array | 需要读取的列名（querySql 模式下可写 `["*"]`） |
| `splitPk` | 建议 | string | 分片键（主键列名），仅 table 模式生效 |
| `where` | 否 | string | 过滤条件，不含 WHERE 关键字 |
| `querySql` | 否 | array | 自定义 SQL（与 table 互斥） |
| `session` | 否 | array | 初始化 SQL（如 SET TRANSACTION ISOLATION） |
| `fetchSize` | 否 | int | JDBC fetchSize，默认由插件内部设 Integer.MIN_VALUE |
| `connection[].jdbcUrl` | 是 | array | JDBC 连接串，支持多实例负载均衡 |
| `connection[].table` | 否 | array | 表名数组（querySql 模式不需要） |

### 4.2 优点

1. **双模式灵活**：table 模式简洁，querySql 模式自由度高
2. **自动分片**：splitPk 自动计算等距区间，无需手动切分
3. **连接池复用**：Druid 连接池管理，避免重复建连
4. **session 可控**：支持注入 SET 语句，精准控制事务隔离级别和超时
5. **多实例负载均衡**：jdbcUrl 支持数组，轮询分发查询

### 4.3 缺点

1. **splitPk 依赖索引**：分片耗时与表大小成正比（MIN/MAX 查询），但仍在分钟级
2. **大字段性能差**：TEXT/BLOB 字段默认全部加载，一个 remark 字段可能让 Record 从 200B 膨胀到 100KB
3. **Decimal 精度丢失**：使用 `decimal.doubleValue()` 转换，精度 > 15 位时出现尾数误差
4. **SQL 注入风险**：querySql 直接拼接，需外部保证 SQL 安全
5. **只读连接不支持写事务**：Reader 连接设置为 `readOnly=true`

### 4.4 适用场景

1. 大表全量导出（1 亿行 + splitPk 分片，数据仓库导数首选）
2. 条件过滤导出（WHERE + status + create_time）
3. 加密字段解密同步（querySql + AES_DECRYPT）
4. 跨实例 JOIN 导出（querySql + 跨库查询）
5. 多租户数据隔离同步（session 参数 + SET schema_name）

### 4.5 不适用场景

1. 实时 CDC 增量捕获（DataX 是批处理，需配合 Canal/Flink CDC）
2. 无主键表的并发读取（splitPk 失效，只能用 channel=1）

### 4.6 思考题

1. 如果表的主键是复合主键（如 `PRIMARY KEY (org_id, user_id)`），如何配置 splitPk 才能正确分片？
2. DataX MySQL Reader 的 `fetchSize=Integer.MIN_VALUE` 为什么能启用流式读取？这个魔法值是 MySQL JDBC 驱动协议约定的吗？

（答案见附录）
