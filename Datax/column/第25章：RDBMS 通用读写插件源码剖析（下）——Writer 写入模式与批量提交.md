# 第25章：RDBMS 通用读写插件源码剖析（下）——Writer 写入模式与批量提交

## 1. 项目背景

某在线教育平台的运营团队需要每天凌晨将前一日的用户订单数据，从业务 MySQL 同步到分析 MySQL。业务提出一个"看似简单"的需求：**增量更新**——如果订单状态变化了（如从"待支付"变为"已支付"），不要新增一条记录，而是在目标表中**原地更新**。

团队成员小王第一次配置 DataX Writer 时，照着网上教程写了 `writeMode: "insert"`——结果每天同步都往目标表追加全量数据，三天后表就 3000 万行，查询卡死。改成 `writeMode: "replace"` 后，每天同步前删除所有数据重新写入，倒是解决了重复问题——但分析工程师投诉说"每天凌晨 4 点查不到数据，因为同步中途表是空的"。

最终 TL 让小王改为 `writeMode: "update"`——根据主键匹配，只更新变化的列。配置如下：

```json
{
    "writer": {
        "name": "mysqlwriter",
        "parameter": {
            "writeMode": "update",
            "column": ["id", "order_no", "status", "amount"],
            "session": ["SET SQL_SAFE_UPDATES = 0"]
        }
    }
}
```

但没过两天，运维发现**写入 QPS 从 insert 模式的 8 万条/秒暴跌到 2000 条/秒**。排查发现：`update` 模式每条记录都走一次单条 UPDATE 语句，没有用批量提交。

问题层层递进——本章从 `CommonRdbmsWriter` 源码入手，深挖四种 writeMode 的实现原理、`PreparedStatement.addBatch()` 批量提交的正确姿势、以及错误重试机制的核心逻辑。

## 2. 项目设计——剧本式交锋对话

**（工位上，小王盯着 MySQL 的 General Log，一脸茫然）**

**小胖**：（嚼着面包走过来）哥们儿，你这 Writer 怎么这么慢？insert 模式 8 万 QPS，改成 update 只剩 2000 QPS——差了 40 倍！

**小王**：（抓头发）对啊，我明明用了 batchSize=2048，也调了 ps.addBatch()，为什么还是慢？

**小白**：（远程连上屏幕，打开 General Log）你看日志：

```sql
-- insert 模式的 General Log
INSERT INTO target (id, order_no, status, amount) VALUES 
    (?, ?, ?, ?), (?, ?, ?, ?), (?, ?, ?, ?), ...  -- 一次 2048 条，批量提交

-- update 模式的 General Log
UPDATE target SET status=?, amount=? WHERE id=?;  -- ★ 只有 1 条！
UPDATE target SET status=?, amount=? WHERE id=?;  -- ★ 又 1 条！
UPDATE target SET status=?, amount=? WHERE id=?;  -- ★ 还 1 条！
```

insert 用了批量插入语法 `INSERT INTO ... VALUES (...), (...), (...)`，update 却是每条一行。问题不在 batchSize 配置，而在 **DataX 对 update 模式没有实现真正的批量 UPDATE**——它拼接的 SQL 是单条 UPDATE。

**大师**：（端着枸杞茶走来）这涉及到 SQL 语法层面的限制。`INSERT VALUES` 天然支持多行值（批量），但 `UPDATE` 不是——标准 SQL 不支持 `UPDATE table SET col=val WHERE id IN (1,2,3)` 这种每行不同值的批量更新。

但 MySQL 有个 `INSERT ... ON DUPLICATE KEY UPDATE` 语法——既支持批量多行输入，又能在遇到主键/唯一键冲突时自动转为 UPDATE。所以**想同时有批量性能 + update 语义，应该用这个语法**，但 DataX 原生没有把它作为一个独立的 writeMode。

**技术映射**：writeMode = 交通工具。`insert` = 高铁（一次拉 2048 人），`update` = 出租车（一次只拉 1 人），`replace` = 救护车（先把旧病人抬下来，再放新病人上去），`delete` = 收垃圾车（先把旧数据扔掉）。用对了快如闪电，用错了慢如蜗牛。

**小胖**：（翻源码）那 DataX 的四种 writeMode 到底怎么落到 SQL 上的？

**大师**：（在白板上画了四张 SQL 模板）核心在 `CommonRdbmsWriter.Task` 的 `buildWriteRecordSql()` 方法：

```java
// CommonRdbmsWriter.java — 根据 writeMode 生成不同的 SQL 模板
private String buildWriteRecordSql(String table, List<String> columns, 
                                     String writeMode) {
    switch (writeMode.toLowerCase()) {
        case "insert":
            // INSERT INTO table (col1, col2, col3) VALUES (?, ?, ?)
            return "INSERT INTO " + table 
                + " (" + join(columns, ",") + ") VALUES (" 
                + repeat("?", columns.size(), ",") + ")";
        
        case "replace":
            // REPLACE INTO table (col1, col2, col3) VALUES (?, ?, ?)
            return "REPLACE INTO " + table 
                + " (" + join(columns, ",") + ") VALUES (" 
                + repeat("?", columns.size(), ",") + ")";
        
        case "update":
            // UPDATE table SET col1=?, col2=? WHERE pk=?
            return "UPDATE " + table 
                + " SET " + buildSetClause(columns) 
                + " WHERE " + buildWhereClause(primaryKeys);
        
        case "delete":
            // DELETE FROM table WHERE pk=?
            return "DELETE FROM " + table 
                + " WHERE " + buildWhereClause(primaryKeys);
        
        default:
            throw DataXException("Unsupported writeMode: " + writeMode);
    }
}
```

**小白**：insert 和 replace 的 SQL 模板其实一样（都是 `VALUES (?, ?, ?)`），差别只在于关键词。但 DataX 怎么实现**批量** insert 的？是一条一条 `executeUpdate()` 还是 batch？

**大师**：关键在这里——`startWrite()` 里的批量提交逻辑：

```java
// CommonRdbmsWriter.Task.startWrite() 核心循环
PreparedStatement ps = conn.prepareStatement(writeSql);
int batchCount = 0;

while (true) {
    Record record = lineReceiver.getFromReader();
    if (record == TerminateRecord.get()) break;
    
    // ★ 逐列设置 PreparedStatement 参数
    for (int i = 0; i < columns.size(); i++) {
        Column column = record.getColumn(i);
        setPreparedStatementValue(ps, i + 1, column, columnTypeMap.get(i));
    }
    
    // ★★ addBatch() — 加入批处理队列
    ps.addBatch();
    batchCount++;
    
    // ★★ 攒满 batchSize 条 → 一次性 executeBatch()
    if (batchCount >= this.batchSize) {
        ps.executeBatch();
        conn.commit();
        ps.clearBatch();
        batchCount = 0;
    }
}
```

所以 **insert/replace 模式都支持 addBatch + executeBatch 的批量提交**。但 update 和 delete 模式的 SQL 模板不一样——它们是逐条更新的 SQL，每条的 WHERE 条件不同——这意味着**PreparedStatement 的 SQL 模板会变**。DataX 的策略是：

- **insert/replace**：SQL 模板不变，`addBatch()` → `executeBatch()`，真批量
- **update/delete**：SQL 每行都不同，但 DataX 仍然用 `addBatch()`（每条占一个 batch entry），`executeBatch()` 时会**逐条执行**——没有真正的 SQL 层批量性能提升，只是减少了一次 `conn.commit()` 的事务开销

**小胖**：（若有所思）所以 update 慢不是因为没 batch，是因为 SQL 本身不支持批量？那除了 `INSERT ON DUPLICATE KEY UPDATE` 还有什么办法？

**大师**：三种方式，各有代价：
1. **临时表 + JOIN UPDATE**：先把数据 insert 到临时表，然后一条 `UPDATE target JOIN temp ON pk SET target.col=temp.col`——但 DataX 默认不支持这种模式
2. **多个 query**：在 `preSql` 里建临时表，Writer 用 insert 写临时表，`postSql` 里执行 JOIN UPDATE
3. **自定义 Writer**：覆盖 `startWrite()`，自己实现批量 UPDATE 逻辑

## 3. 项目实战

### 3.1 步骤一：CommonRdbmsWriter.Job 完整生命周期

**目标**：理解 Writer.Job 的配置解析、preCheck 验证、preSql/postSql 执行。

```java
// CommonRdbmsWriter.java — Job 内部类
public static class Job extends Writer.Job {
    
    private DataBaseType dataBaseType;
    private Configuration originalConfig;
    
    // ============ 阶段 1: init ============
    @Override
    public void init() {
        this.originalConfig = this.getPluginJobConf();
        
        // 1. 推断数据库类型
        String jdbcUrl = this.originalConfig.getString("jdbcUrl");
        this.dataBaseType = DataBaseType.getByUrl(jdbcUrl);
        
        // 2. 读取 Writer 特有配置
        this.writeMode = this.originalConfig.getString("writeMode", "insert");
        this.batchSize = this.originalConfig.getInt("batchSize", 2048);
        this.columnNames = this.originalConfig.getList("column", String.class);
        
        // 3. 读取 preSql / postSql 列表
        this.preSqls = this.originalConfig.getList("preSql", String.class);
        this.postSqls = this.originalConfig.getList("postSql", String.class);
        
        // 4. 读取 session 参数
        this.sessionParams = this.originalConfig.getList("session", String.class);
    }
    
    // ============ 阶段 2: preCheck ============
    @Override
    public void preCheck() {
        // 1. 验证 writeMode 合法性
        Set<String> validModes = new HashSet<>(
            Arrays.asList("insert", "replace", "update", "delete"));
        if (!validModes.contains(this.writeMode.toLowerCase())) {
            throw DataXException("Invalid writeMode: " + this.writeMode);
        }
        
        // 2. 验证目标表是否存在
        Connection conn = DBUtil.getConnection(this.dataBaseType, jdbcUrl, ...);
        DBUtil.isTableExists(dataBaseType, conn, this.tableName);
        
        // 3. 验证要写入的列在目标表中存在
        for (String column : this.columnNames) {
            if (!DBUtil.isColumnExists(dataBaseType, conn, this.tableName, column)) {
                throw DataXException("Column " + column + " not found in table " + tableName);
            }
        }
        
        conn.close();
    }
    
    // ============ 阶段 3: prepare ============
    @Override
    public void prepare() {
        // ★ 执行 preSql（建表、清空、索引删除等）
        Connection conn = DBUtil.getConnection(this.dataBaseType, jdbcUrl, ...);
        conn.setAutoCommit(true);  // DDL 语句需要在事务外执行
        
        for (String preSql : this.preSqls) {
            Statement stmt = conn.createStatement();
            stmt.execute(preSql);
            stmt.close();
        }
        
        conn.close();
    }
    
    // ============ 阶段 4: split ============
    @Override
    public List<Configuration> split(int adviceNumber) {
        // Writer 的 split 通常很简单——直接返回 adviceNumber 个配置
        List<Configuration> writerTaskConfigs = new ArrayList<>();
        for (int i = 0; i < adviceNumber; i++) {
            writerTaskConfigs.add(this.originalConfig.clone());
        }
        return writerTaskConfigs;
    }
    
    // ============ 阶段 5: post ============
    @Override
    public void post() {
        // ★ 执行 postSql（索引重建、统计更新等）
        Connection conn = DBUtil.getConnection(this.dataBaseType, jdbcUrl, ...);
        conn.setAutoCommit(true);
        
        for (String postSql : this.postSqls) {
            Statement stmt = conn.createStatement();
            stmt.execute(postSql);
            stmt.close();
        }
        
        conn.close();
    }
    
    // ============ 阶段 6: destroy ============
    @Override
    public void destroy() {
        // 释放资源
    }
}
```

### 3.2 步骤二：CommonRdbmsWriter.Task.startWrite() 完整写入流程

**目标**：理解 Writer.Task 如何从 Channel 获取 Record、构建 SQL、批量提交。

```java
// CommonRdbmsWriter.java — Task 内部类
public static class Task extends Writer.Task {
    
    private String writeMode;
    private String writeSql;      // ★ SQL 模板
    private int batchSize;
    private List<String> columns;
    private List<String> primaryKeys;
    
    @Override
    public void startWrite(RecordReceiver lineReceiver) {
        // 1. ★ 获取数据库连接，关闭自动提交
        Connection conn = DBUtil.getConnection(
            this.dataBaseType, jdbcUrl, username, password);
        conn.setAutoCommit(false);
        
        // 2. ★ 根据 writeMode 构建 SQL 模板
        this.writeSql = buildWriteRecordSql(
            this.tableName, this.columns, this.writeMode);
        LOG.info("Write SQL template: {}", this.writeSql);
        
        // 3. ★ 创建 PreparedStatement
        PreparedStatement ps = conn.prepareStatement(this.writeSql);
        
        // 4. ★ 预编译获取列类型信息
        ResultSetMetaData metaData = conn.prepareStatement(
            "SELECT * FROM " + this.tableName + " WHERE 1=0"
        ).getMetaData();
        
        // 5. ★★ 核心：循环接收 Record，设置参数，addBatch
        Record record;
        int batchCount = 0;
        long totalRecords = 0;
        
        while (true) {
            record = lineReceiver.getFromReader();
            if (record == TerminateRecord.get()) break;
            
            // 5a. 将 Record 的每个 Column 设置到 PreparedStatement
            for (int i = 0; i < this.columns.size(); i++) {
                Column column = record.getColumn(i);
                int paramIndex = i + 1;
                
                // ★ 根据 Column 类型调用对应的 ps.setXxx()
                switch (column.getType()) {
                    case LONG:
                        ps.setLong(paramIndex, column.asLong());
                        break;
                    case DOUBLE:
                        ps.setDouble(paramIndex, column.asDouble());
                        break;
                    case STRING:
                        ps.setString(paramIndex, column.asString());
                        break;
                    case DATE:
                        ps.setTimestamp(paramIndex, 
                            new Timestamp(column.asDate().getTime()));
                        break;
                    case BOOL:
                        ps.setBoolean(paramIndex, column.asBoolean());
                        break;
                    case BYTES:
                        ps.setBytes(paramIndex, column.asBytes());
                        break;
                    default:
                        ps.setString(paramIndex, column.asString());
                }
            }
            
            // 5b. ★ addBatch — 加入批处理
            ps.addBatch();
            batchCount++;
            totalRecords++;
            
            // 5c. ★ 攒满 batch → executeBatch + commit
            if (batchCount >= this.batchSize) {
                try {
                    ps.executeBatch();
                    conn.commit();
                } catch (SQLException e) {
                    // ★★ 错误重试机制（见步骤三）
                    handleBatchError(ps, conn, e);
                }
                ps.clearBatch();
                batchCount = 0;
            }
        }
        
        // 6. ★ 提交残余 batch
        if (batchCount > 0) {
            try {
                ps.executeBatch();
                conn.commit();
            } catch (SQLException e) {
                handleBatchError(ps, conn, e);
            }
        }
        
        // 7. 释放资源
        ps.close();
        conn.close();
        
        LOG.info("Writer Task finished. Total records: {}", totalRecords);
    }
}
```

### 3.3 步骤三：错误重试机制源码剖析

**目标**：理解 Writer 如何处理写入失败——哪些失败可重试，哪些不可重试。

```java
// CommonRdbmsWriter.Task — 错误重试逻辑
private void handleBatchError(
        PreparedStatement ps, Connection conn, SQLException e) {
    
    // ★ 判断是否为可重试错误
    if (isRetryableError(e)) {
        LOG.warn("Batch execute failed, retrying... Error: {}", e.getMessage());
        // 重试整个 batch（最多 3 次）
        for (int retry = 0; retry < 3; retry++) {
            try {
                ps.executeBatch();
                conn.commit();
                return;  // 重试成功
            } catch (SQLException retryEx) {
                if (!isRetryableError(retryEx)) {
                    break;  // 不可重试错误，跳出重试循环
                }
                try { Thread.sleep(1000 * (retry + 1)); } 
                catch (InterruptedException ie) { Thread.currentThread().interrupt(); }
            }
        }
    }
    
    // ★ 批量重试失败 → 逐条重试（缩小失败范围）
    LOG.warn("Batch retry failed, falling back to single-row retry...");
    dealWithBatchErrorWithSingleRecord(ps);
}

// ★ 判断错误是否可重试
private boolean isRetryableError(SQLException e) {
    int errorCode = e.getErrorCode();
    
    switch (errorCode) {
        // 网络/连接类错误 → 可重试
        case 1040: // MySQL: Too many connections
        case 1042: // MySQL: Can't get hostname
        case 1158: // MySQL: Got an error reading communication packets
        case 17002: // Oracle: IO Error
            return true;
        
        // 超时类错误 → 可重试
        case 1205: // MySQL: Lock wait timeout exceeded
        case 1213: // MySQL: Deadlock found when trying to get lock
            return true;
        
        // 数据类错误 → 不可重试（重试也没用）
        case 1062: // MySQL: Duplicate entry
        case 1406: // MySQL: Data too long for column
        case 1048: // MySQL: Column cannot be null
            return false;
        
        default:
            return false;
    }
}

// ★ 逐条重试——将失败的 batch 拆成单条，识别出具体的脏数据
private void dealWithBatchErrorWithSingleRecord(PreparedStatement ps) {
    // 获取 batch 中的所有 SQL（由 addBatch 添加的）
    // MySQL JDBC 驱动不支持直接获取 batch 中的 SQL
    // 所以需要回退到逐条 executeUpdate 的方式
    
    // ★ 实际实现：回退到缓存中的 Record 列表，逐条重试
    for (Record record : cachedBatchRecords) {
        try {
            clearStatement(ps);
            setStatementValues(ps, record);
            ps.executeUpdate();
        } catch (SQLException singleEx) {
            // ★ 单条失败 → 收集为脏数据
            String errorMsg = String.format(
                "Write failed for record: %s, error: %s", 
                record, singleEx.getMessage());
            this.getTaskPluginCollector().collectDirtyRecord(
                record, singleEx, errorMsg);
        }
    }
    
    // 提交成功写入的单条
    try { conn.commit(); } 
    catch (SQLException ce) { 
        LOG.error("Final commit failed: {}", ce.getMessage());
    }
}
```

### 3.4 步骤四：四种 writeMode 实战对比

**目标**：通过实际运行，对比四种 writeMode 的行为、性能和适用场景。

**测试场景**：源表 10 万行订单数据，目标表结构相同（id 为主键）。

#### 实验 A：insert 模式（目标表起初为空）

```json
{
    "writer": {
        "name": "mysqlwriter",
        "parameter": {
            "writeMode": "insert",
            "column": ["id", "order_no", "status", "amount"],
            "preSql": ["TRUNCATE TABLE target_orders"]
        }
    }
}
```

执行 SQL：`INSERT INTO target_orders (id, order_no, status, amount) VALUES (?, ?, ?, ?)`  
批量方式：`ps.addBatch()` × batchSize → `ps.executeBatch()`，真批量  
QPS：**8.5 万条/秒**  
重复执行后果：主键冲突报错（`Duplicate entry '1' for key 'PRIMARY'`）

#### 实验 B：replace 模式

```json
{
    "writer": {
        "parameter": {
            "writeMode": "replace",
            "column": ["id", "order_no", "status", "amount"]
        }
    }
}
```

执行 SQL：`REPLACE INTO target_orders (id, order_no, status, amount) VALUES (?, ?, ?, ?)`  
批量方式：同 insert，真批量  
QPS：**6.2 万条/秒**（比 insert 慢，因为 replace 需要先 DELETE 冲突行再 INSERT）  
行为：遇到主键冲突 → 删旧行 → 插新行（所有列值完全覆盖）

#### 实验 C：update 模式

```json
{
    "writer": {
        "parameter": {
            "writeMode": "update",
            "column": ["id", "order_no", "status", "amount"],
            "session": ["SET SQL_SAFE_UPDATES = 0"]
        }
    }
}
```

执行 SQL：`UPDATE target_orders SET order_no=?, status=?, amount=? WHERE id=?`  
批量方式：`addBatch()` 逐条（**伪批量**——每条 SQL 不同，executeBatch 仍逐条执行）  
QPS：**2100 条/秒**（无真批量支持）  
特别注意：必须指定 `WHERE` 列——默认用 `column` 列表中的第一列作为 `WHERE` 条件

#### 实验 D：delete 模式

```json
{
    "writer": {
        "parameter": {
            "writeMode": "delete",
            "column": ["id"]
        }
    }
}
```

执行 SQL：`DELETE FROM target_orders WHERE id=?`  
批量方式：同 update，伪批量  
QPS：**1800 条/秒**  
行为：根据 `column` 第一列的值删除目标表中对应行

**四种 writeMode 对比总结**：

| writeMode | SQL 模板 | 真批量？ | QPS（10 万行） | 主键冲突行为 | 适用场景 |
|-----------|---------|---------|--------------|------------|---------|
| insert | INSERT INTO ... VALUES (?,?,?) | ✓ 是 | 8.5 万/s | 报错 | 目标表为空或以追加方式 |
| replace | REPLACE INTO ... VALUES (?,?,?) | ✓ 是 | 6.2 万/s | 覆盖（删+插） | 全量覆盖，目标表可有旧数据 |
| update | UPDATE ... SET ... WHERE pk=? | ✗ 否（伪批量） | 2100/s | 无影响 | 增量更新已知行的字段 |
| delete | DELETE FROM ... WHERE pk=? | ✗ 否（伪批量） | 1800/s | 无影响 | 删除指定行 |

### 3.5 步骤五：batchSize 调优与 OOM 风险控制

**目标**：找到最优 batchSize——平衡吞吐量与内存安全。

```java
// 内存估算公式
// 单次 batch 内存 ≈ batchSize × 单条 Record 大小 × (1个 Record 对象 + N 个 Column 对象)
// 以订单表为例：单条 Record ≈ 500B（4 个字段）

// batchSize 对比测试
```

**测试矩阵**（channel=4，每条 Record=500B，总 1000 万条，writeMode=insert）：

| batchSize | 每批次内存 | executeBatch 频次 | 执行耗时 | QPS | OOM 风险 |
|-----------|----------|------------------|---------|-----|---------|
| 256 | 128KB | 9766 次 | 38s | 26 万/s | 极低 |
| 1024 | 512KB | 2441 次 | 22s | 45 万/s | 低 |
| 2048（默认） | 1MB | 1221 次 | 15s | 66 万/s | 低 |
| 4096 | 2MB | 610 次 | 13s | 77 万/s | 中 |
| 8192 | 4MB | 305 次 | 12s | 83 万/s | 中 |
| 16384 | 8MB | 153 次 | 11.5s | 87 万/s | 高 |
| 32768 | 16MB | 76 次 | 11s | 91 万/s | 极高（GC 频繁） |

**结论**：
- batchSize 从 2048 到 4096，提升明显（commit 次数减半）
- batchSize 从 4096 到 8192，提升放缓（瓶颈从 commit 转到网络 IO）
- batchSize ≥ 8192 → 每批次内存 > 4MB×4 Channel = 16MB，加上 JVM 开销，GC 压力大增

**推荐配置**：

```json
{
    "speed": {"channel": 4, "recordBatchSize": 4096},
    "writer": {
        "parameter": {
            "batchSize": 4096,
            ...
        }
    }
}
```

### 3.6 可能遇到的坑及解决方法

**坑 1：update 模式忘记指定 WHERE 条件列**

DataX 的 `update` 模式默认用 `column` 列表中的**第一列**作为 WHERE 条件。如果第一列是 `order_no` 而非 `id`——可能会更新错误的行。

解决：将主键列放在 `column` 列表的第一位。

```json
{
    "column": ["id", "order_no", "status", "amount"]  // ★ id 放第一位
}
```

**坑 2：delete 模式删除范围过大**

delete 模式默认用 `column` 第一列作为删除条件。如果 `column: ["status"]`，则在目标是全表删除（因为没有指定具体行）。

解决：delete 模式下 `column` 第一列必须是主键。

**坑 3：MySQL 默认 safe-updates 阻止批量更新**

MySQL 默认开启 `--safe-updates` 模式，禁止不带 `WHERE` 主键条件的 UPDATE/DELETE。导致 `update` 模式执行失败。

解决：在 `session` 配置中关闭：

```json
{
    "session": ["SET SQL_SAFE_UPDATES = 0"]
}
```

## 4. 项目总结

### 4.1 CommonRdbmsWriter 架构全景

```
JSON 配置（writeMode, column, batchSize, preSql, postSql, session）
  │
  ▼
CommonRdbmsWriter.Job
  ├─ init()       → 解析配置 → 推断 DataBaseType → 验证 writeMode
  ├─ preCheck()   → 验证连接 + 验证目标表/列存在性
  ├─ prepare()    → 执行 preSql（DDL：建表、TRUNCATE、删索引）
  ├─ split()      → 返回 N 个配置（与 Reader Task 对齐）
  ├─ post()       → 执行 postSql（DDL：建索引、分析统计）
  └─ destroy()
        │
        ▼ (每个 Task 执行)
CommonRdbmsWriter.Task
  ├─ startWrite(RecordReceiver)
  │   ├─ buildWriteRecordSql(writeMode) → SQL 模板
  │   ├─ conn.setAutoCommit(false)
  │   ├─ PreparedStatement ps = conn.prepareStatement(sql)
  │   │
  │   └─ while (record = receiver.getFromReader())
  │       ├─ check TerminateRecord → break
  │       ├─ for each column: ps.setXxx(i, record.getColumn(i))
  │       ├─ ps.addBatch()
  │       └─ if (batchCount >= batchSize)
  │           ├─ ps.executeBatch()
  │           ├─ conn.commit()
  │           └─ ps.clearBatch()
  │
  └─ ps.close() + conn.close()
```

### 4.2 优点

1. **多模式覆盖**：insert/replace/update/delete 四种模式满足增删改查全场景
2. **批量提交**：insert/replace 支持真批量 addBatch/executeBatch，吞吐量高
3. **错误分级处理**：网络错误自动重试，数据错误收集为脏数据，不影响其他正常数据
4. **preSql/postSql**：支持前置和后置 DDL 操作（建表、清空、索引清理/重建）
5. **数据库无关**：同 CommonRdbmsReader 一样，通过 DataBaseType 适配多数据库

### 4.3 缺点

1. **update/delete 无真批量**：SQL 层面不支持多行变量批量更新，executeBatch 仍逐条执行
2. **无 upsert 模式**：缺少 `INSERT ... ON DUPLICATE KEY UPDATE` 的原生支持
3. **脏数据收集机制依赖缓存**：batch 失败回退到逐条重试时，需要缓存原始 Record（占内存）
4. **batchSize 全局共享**：无法按列大小动态调整 batchSize
5. **无写入顺序保证**：多 Channel 并发写入时，事务提交顺序不可控

### 4.4 适用场景

1. 全量数据迁移（insert 模式，大批量写入）
2. 周期性全量覆盖同步（replace 模式，每天凌晨全量覆写）
3. 增量字段更新（update 模式，只更新变化的列）
4. 清洗后的数据删除同步（delete 模式，源端删除 → 目标端同步删除）
5. 跨数据库异构同步（Oracle → MySQL、PG → MySQL 等，统一用 insert/replace）

### 4.5 注意事项

1. `batchSize` 与 `recordBatchSize` 是两个不同的参数——前者控制 Writer 的 commit 批次大小，后者控制 Channel buffer 的批次大小
2. `writeMode="update"` 时，`column` 列表的第一列默认作为 WHERE 条件的主键列
3. `preSql` 和 `postSql` 是**数组**——可以顺序执行多条 SQL
4. MySQL 必须在 `session` 中 `SET SQL_SAFE_UPDATES=0`，否则 update/delete 模式报错
5. `batchSize` 设置过大时，单次 `executeBatch()` 可能超时——需调整 `socketTimeout`

### 4.6 常见踩坑经验

**坑 1：Writer 的 batchSize > Reader 的 recordBatchSize，Writer 饥饿**

Reader 攒满 1024 条才 flush 到 Channel，Writer 每 batchSize=4096 条才 commit。如果 Reader 的 flush 频率满足不了 Writer 的 commit 频率（1024 < 4096），Writer 永远凑不满一个 batch，最后一个残缺 batch 一直在内存里等。解决：Writer 的 batchSize 应 ≤ Reader 的 recordBatchSize × Channel 数。

**坑 2：update 模式的 WHERE 条件用字符串字段，索引失效**

如果 `column` 第一列是 `order_no`（VARCHAR 类型），且 `order_no` 在目标表上有索引——理论上能走索引。但如果源端的 `order_no` 和目标端的 `order_no` 排序不一致（编码不同），索引扫描的范围会超大。解决：始终用主键自增 ID 作为 WHERE 条件列。

**坑 3：executeBatch 返回 `BatchUpdateException`，错误信息不够详细**

`BatchUpdateException.getUpdateCounts()` 返回 `int[]`——每个元素对应 batch 中一条 SQL 的执行结果（SUCCESS=1/FAILED=0/EXECUTE_FAILED=-3）。但**MySQL JDBC 驱动在批次失败时会停止执行后续 SQL**，所以这个 int[] 只能告诉你"第 N 条失败了"，无法告诉你"第 N+1 条到第 M 条是什么状态"。解决：降低 batchSize，让每个 batch 的范围更小，失败影响范围更小。

### 4.7 思考题

1. 如果要实现 `upsert` 模式（`INSERT ... ON DUPLICATE KEY UPDATE`），需要在 `CommonRdbmsWriter` 中改哪些地方？是否需要新增一个 writeMode 值，还是用其他方式区分？
2. `update` 模式的伪批量（逐条执行）与真正的批量 UPDATE（如 `UPDATE target JOIN temp ON target.id=temp.id SET target.col=temp.col`）在 100 万条数据场景下的性能差异有多大？请估算并设计一个验证方案。

（答案见附录）
