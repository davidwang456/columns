# 第24章：RDBMS 通用读写插件源码剖析（上）——JDBC 封装与类型映射

## 1. 项目背景

某数据中台团队承接了集团 12 个业务线的数据汇聚需求——需要从 MySQL、Oracle、PostgreSQL、SQL Server 四种数据库中同步数据到统一的数据仓库。最初，团队为每种数据库分别写了独立的 DataX 同步 Job 配置，但随着业务增长，每周都有新表接入，维护 200+ 个 Job 配置成了噩梦。

更棘手的是类型兼容问题。Oracle 的 `NUMBER` 类型在 DataX 中被映射为 `LongColumn`，但实际存储的值可能是 3.1415926——`LongColumn.asLong()` 静默截断小数部分，导致财务对账出现"分位数偏差"。而 PostgreSQL 的 `TIMESTAMPTZ` 类型映射为 `DateColumn` 后，时区信息丢失，订单时间"偏差"了 8 小时。

团队决定深入 `plugin-rdbms-util` 这层通用基类的源码，理解 `CommonRdbmsReader` 如何通过一套统一的 JDBC 封装，支持上述四种数据库——同时搞清楚 JDBC Types 到 DataX Column 的类型映射表是如何工作的，以及 `fetchSize=Integer.MIN_VALUE` 为何能"凭空"让 MySQL Reader 的内存占用降低 90%。

## 2. 项目设计——剧本式交锋对话

**（办公室，三人围着一台显示器，屏幕上开着 MySQL、Oracle、PostgreSQL 三个数据库的 JDBC 驱动源码）**

**小胖**：（啃着薯片）我就想不通——MySQL、Oracle、PG 三个数据库的 JDBC 驱动完全不同，sql 语法也不同（LIMIT vs ROWNUM vs FETCH），DataX 怎么做到一套 `CommonRdbmsReader` 通吃？

**小白**：（推了推眼镜）你看 `CommonRdbmsReader` 的源码——它自己根本不知道数据库类型。它依赖两个关键抽象：

```java
// CommonRdbmsReader.java
public static class Job {
    private DataBaseType dataBaseType;  // ★ 数据库类型标记
    
    public void init() {
        String jdbcUrl = conf.getString("jdbcUrl");
        // ★ 从 jdbcUrl 前缀推断数据库类型
        if (jdbcUrl.startsWith("jdbc:mysql:")) {
            this.dataBaseType = DataBaseType.MySql;
        } else if (jdbcUrl.startsWith("jdbc:oracle:")) {
            this.dataBaseType = DataBaseType.Oracle;
        }
    }
}
```

所有数据库差异性的逻辑都封装在 `DataBaseType` 枚举里——包括 SQL 方言、分片策略、JDBC 连接参数等。

**大师**：对。`DataBaseType` 是数据源适配模式中的"**策略枚举**"。你想象一个跨国餐饮集团——不同国家的菜单不同（SQL 方言），但厨房流程统一（Reader→Channel→Writer）。`DataBaseType` 就是那个"国家标记"，告诉你该上炸鸡还是寿司。

**技术映射**：`DataBaseType` = 餐厅的国家菜单。日本的店用筷子（Oracle ROWNUM），美国的店用刀叉（MySQL LIMIT），但后厨的切菜-烹饪-装盘的流程（CommonRdbmsReader）完全一样。

**小胖**：（翻文档）那类型映射呢？我上周同步 Oracle 到 PG，`NUMBER(10,2)` → `LongColumn`，小数点全丢了！

**大师**：（打开 `Column` 类型映射表）问题出在这里：

```java
// DBUtil.java — JDBC Types → DataX Column 映射
public static Column convertToColumn(ResultSet rs, int i, String columnType) 
    throws SQLException {
    
    switch (columnType) {
        case "TINYINT":
        case "SMALLINT":
        case "INTEGER":
        case "BIGINT":
            return new LongColumn(rs.getLong(i));
        
        case "FLOAT":
        case "DOUBLE":
        case "DECIMAL":
        case "NUMERIC":        // ★ Oracle NUMBER 在这里被当作整数了！
            return new DoubleColumn(rs.getDouble(i));
        
        case "CHAR":
        case "VARCHAR":
        case "LONGVARCHAR":
            return new StringColumn(rs.getString(i));
        
        case "DATE":
        case "TIME":
        case "TIMESTAMP":
            return new DateColumn(rs.getTimestamp(i));
        
        default:
            return new StringColumn(rs.getString(i));
    }
}
```

Oracle 的 `NUMBER` 映射到 JDBC Types 的 `NUMERIC`，被 DataX 当做 `DoubleColumn` 处理，但 `DoubleColumn` 精度只有 15-16 位有效数字——财务场景的 38 位精度需求会被截断。所以**对于财务数据，应该强制映射为 `StringColumn`**，在目标端再转回 `DECIMAL`。

**小白**：我注意到 `convertToColumn` 用的是 `columnType` 字符串参数，而不是 JDBC 的 `Types.INTEGER` 这种 int 常量——这是故意的？

**大师**：非常对。JDBC 规范定义了 `Types.NUMERIC = 2`，但不同数据库对 `NUMERIC` 的语义不完全一致。Oracle 的 NUMBER 可以是无精度小数、整数或浮点数——JDBC 统一上报为 `NUMERIC`，但实际业务语义各不相同。DataX 选择用 `columnType` 字符串是因为从 `ResultSetMetaData.getColumnTypeName()` 获取的是数据库原生类型名（如 `INT`、`NUMBER`、`BIGSERIAL`），比 JDBC int 常量语义更丰富。

**小胖**：那 `fetchSize` 呢？我听说 MySQL 设 `Integer.MIN_VALUE` 能"流式读取"——什么原理？

**大师**：（在白板上画了一个时序图）MySQL JDBC 驱动默认行为是**一次性把 ResultSet 全部加载到客户端内存**。当你查询 5000 万行时，这 5000 万行的数据会全部加载到 JVM 堆内存里。

设置 `fetchSize = Integer.MIN_VALUE` 后，JDBC 驱动改为**逐行流式读取**——每 `rs.next()` 一次，从 MySQL 服务端拉一行，内存中只保留当前行。原理是利用 MySQL 协议中的 **MYSQL_TYPE_LONG_DATA 标志**，告诉服务端"别一次性全发，我一行一行取"。

```java
// CommonRdbmsReader.Task — 设置 fetchSize
public void prepare() {
    Connection conn = DBUtil.getConnection(dataBaseType, jdbcUrl, username, password);
    PreparedStatement ps = conn.prepareStatement(querySql);
    
    // ★ 关键：MySQL 流式读取
    if (dataBaseType == DataBaseType.MySql) {
        ps.setFetchSize(Integer.MIN_VALUE);  // 地狱模式：逐行拉取
    } else {
        ps.setFetchSize(1024);  // 其他数据库默认 1024 条一批
    }
}
```

但流式读取有代价——Connection 在读取期间**不能复用**（直到 ResultSet 关闭），并且每次 `rs.next()` 都是一次网络往返（延迟增加）。所以只适合**大表全量同步**场景。

## 3. 项目实战

### 3.1 步骤一：追踪 CommonRdbmsReader.Job 的完整生命周期

**目标**：理解 Reader.Job 的每一个阶段做了什么，以及业务配置如何影响内部行为。

```java
// CommonRdbmsReader.java — Job 内部类
public static class Job extends Reader.Job {
    
    private DataBaseType dataBaseType;
    private Configuration originalConfig;
    
    // ============ 阶段 1: init ============
    @Override
    public void init() {
        // 1. 保存原始配置（后续 split 阶段需要）
        this.originalConfig = this.getPluginJobConf();
        
        // 2. ★ 推断数据库类型
        String jdbcUrl = this.originalConfig.getString("jdbcUrl");
        this.dataBaseType = DataBaseType.getByUrl(jdbcUrl);
        LOG.info("Detected database type: {}", this.dataBaseType);
        
        // 3. 验证配置（必填字段检查）
        validateParameter();
        
        // 4. 设置 Druid 连接池属性（可选）
        int queryTimeout = this.originalConfig.getInt("queryTimeout", 0);
        int fetchSize = this.originalConfig.getInt("fetchSize", 1024);
        String sessionParams = this.originalConfig.getString("session", "");
    }
    
    // ============ 阶段 2: preCheck ============
    @Override
    public void preCheck() {
        // ★ 验证 JDBC 连接可用性
        Connection conn = DBUtil.getConnection(
            this.dataBaseType, jdbcUrl, username, password);
        conn.close();
        
        // 验证表存在性、列存在性
        DBUtil.isTableExists(dataBaseType, conn, tableName);
        DBUtil.isColumnExists(dataBaseType, conn, tableName, columns);
    }
    
    // ============ 阶段 3: prepare ============
    @Override
    public void prepare() {
        // ★ 执行 session 级参数设置（如 Oracle 并行度）
        String session = this.originalConfig.getString("session", "");
        if (!session.isEmpty()) {
            // 例如 Oracle: "ALTER SESSION SET parallel_degree_policy = AUTO"
            DBUtil.executeSql(dataBaseType, jdbcUrl, username, password, session);
        }
    }
    
    // ============ 阶段 4: split ============
    @Override
    public List<Configuration> split(int adviceNumber) {
        // ★ 调用分片工具方法——根据数据库类型选择不同分片策略
        return SingleTableSplitUtil.splitSingleTable(
            this.originalConfig, 
            adviceNumber,      // channel 建议数
            this.dataBaseType
        );
        // MySQL: 生成 WHERE id BETWEEN ? AND ? 的 N 段子查询
        // Oracle: 同上，但用 ROWNUM 分页
    }
    
    // ============ 阶段 5: post ============
    @Override
    public void post() {
        // 统计输出
        Communication comm = this.getContainerCommunicator().collect();
        LOG.info("Reader total records: {}", comm.getLongCounter("totalReadRecords"));
        LOG.info("Reader total bytes: {}", comm.getLongCounter("totalReadBytes"));
    }
    
    // ============ 阶段 6: destroy ============
    @Override
    public void destroy() {
        // 清理连接池
        DruidDataSourceFactory.remove(originalConfig.getString("jdbcUrl"));
    }
}
```

### 3.2 步骤二：CommonRdbmsReader.Task.startRead() 完整数据读取流程

**目标**：逐行理解 `startRead()` 如何从 JDBC ResultSet 转换为 DataX Record。

```java
// CommonRdbmsReader.java — Task 内部类
public static class Task extends Reader.Task {
    
    @Override
    public void startRead(RecordSender recordSender) {
        // 1. ★ 获取连接（每个 Task 一个独立 Connection）
        Connection conn = DBUtil.getConnection(
            this.dataBaseType, jdbcUrl, username, password);
        
        // 2. ★ 获取查询 SQL（由 split 阶段生成的 WHERE 片段）
        String querySql = this.getPluginJobConf().getString("querySql");
        // 例如: "SELECT id, name, amount FROM orders WHERE id >= 1 AND id < 3333334"
        
        // 3. ★ 创建 PreparedStatement
        PreparedStatement ps = conn.prepareStatement(
            querySql, 
            ResultSet.TYPE_FORWARD_ONLY,     // 只向前遍历
            ResultSet.CONCUR_READ_ONLY       // 只读
        );
        
        // 4. ★ 设置 fetchSize（流式读取关键）
        if (this.dataBaseType == DataBaseType.MySql) {
            ps.setFetchSize(Integer.MIN_VALUE);  // 流式读取
        } else {
            ps.setFetchSize(this.fetchSize);      // 批量读取
        }
        
        // 5. ★ 设置查询超时
        ps.setQueryTimeout(this.queryTimeout);
        
        // 6. ★ 执行查询
        ResultSet rs = ps.executeQuery();
        ResultSetMetaData metaData = rs.getMetaData();
        int columnCount = metaData.getColumnCount();
        
        // 7. ★ 构建列名 → 类型名映射（用于类型转换）
        Map<String, String> columnTypeMap = new HashMap<>();
        for (int i = 1; i <= columnCount; i++) {
            columnTypeMap.put(
                metaData.getColumnName(i),
                metaData.getColumnTypeName(i)  // ★ 数据库原生类型名
            );
        }
        
        // 8. ★★ 逐行读取 → 逐列映射 → 发送给 Writer
        while (rs.next()) {
            // 创建空 Record
            Record record = recordSender.createRecord();
            
            for (int i = 1; i <= columnCount; i++) {
                String columnType = columnTypeMap.get(metaData.getColumnName(i));
                
                // ★★ JDBC 值 → DataX Column
                Column column = DBUtil.convertToColumn(rs, i, columnType);
                record.addColumn(column);
            }
            
            // 发送给 Writer（通过 BufferedRecordExchanger）
            recordSender.sendToWriter(record);
        }
        
        // 9. ★ 发送结束信号
        recordSender.flush();
        recordSender.terminate();
        
        // 10. 释放资源
        rs.close();
        ps.close();
        DBUtil.closeConnection(conn);
    }
}
```

### 3.3 步骤三：深入 JDBC Types → DataX Column 的类型映射表

**目标**：理解类型映射的完整规则，以及如何扩展自定义映射。

```java
// DBUtil.java — 完整类型映射逻辑
public static Column convertToColumn(
        ResultSet rs, int columnIndex, String columnType) throws SQLException {
    
    // ★ 先尝试从 ResultSet 获取，避免 NPE
    Object rawData = rs.getObject(columnIndex);
    if (rs.wasNull()) {
        return new StringColumn(null);  // NULL 值统一用 StringColumn(null)
    }
    
    // ★ 类型名标准化（去空白、转大写）
    String normalizedType = columnType.trim().toUpperCase();
    
    // ★★ MySQL 类型映射
    switch (normalizedType) {
        case "TINYINT":
        case "SMALLINT":
        case "MEDIUMINT":
        case "INT":
        case "INTEGER":
        case "BIGINT":
        case "YEAR":
            return new LongColumn(rs.getLong(columnIndex));
        
        case "FLOAT":
        case "DOUBLE":
        case "DECIMAL":
        case "NUMERIC":
            // ★ 注意：DECIMAL 可能丢失精度
            return new DoubleColumn(rs.getDouble(columnIndex));
        
        case "CHAR":
        case "VARCHAR":
        case "TINYTEXT":
        case "TEXT":
        case "MEDIUMTEXT":
        case "LONGTEXT":
        case "ENUM":
        case "SET":
        case "JSON":       // ★ MySQL JSON 类型 → StringColumn
            return new StringColumn(rs.getString(columnIndex));
        
        case "DATE":
        case "DATETIME":
        case "TIMESTAMP":
        case "TIME":
            Timestamp ts = rs.getTimestamp(columnIndex);
            return ts != null ? new DateColumn(ts) : new StringColumn(null);
        
        case "BIT":
            return new BoolColumn(rs.getBoolean(columnIndex));
        
        case "BLOB":
        case "TINYBLOB":
        case "MEDIUMBLOB":
        case "LONGBLOB":
        case "BINARY":
        case "VARBINARY":
            return new BytesColumn(rs.getBytes(columnIndex));
        
        default:
            // ★ 兜底：当数据库类型在映射表中不存在时，用 StringColumn
            LOG.warn("Unknown column type: {}, fallback to StringColumn", normalizedType);
            return new StringColumn(rs.getString(columnIndex));
    }
}
```

**关键映射对照表**：

| 数据库 | 源类型 | JDBC Type | DataX Column | 精度风险 |
|--------|--------|-----------|-------------|---------|
| MySQL | INT | Types.INTEGER | LongColumn | 无（Long 精度支持 8 字节） |
| MySQL | DECIMAL(38,18) | Types.DECIMAL | DoubleColumn | ★ 高精度金融数据会截断 |
| MySQL | DATETIME | Types.TIMESTAMP | DateColumn | 无（微秒精度保留） |
| MySQL | JSON | Types.LONGVARCHAR | StringColumn | 无 |
| MySQL | BLOB | Types.BLOB | BytesColumn | 无 |
| Oracle | NUMBER | Types.NUMERIC | DoubleColumn | ★ 会丢失整数部分的超长精度 |
| Oracle | VARCHAR2 | Types.VARCHAR | StringColumn | 无 |
| Oracle | DATE | Types.TIMESTAMP | DateColumn | 无 |
| PG | BIGSERIAL | Types.BIGINT | LongColumn | 无 |
| PG | NUMERIC | Types.NUMERIC | DoubleColumn | ★ 同 MySQL DECIMAL |
| PG | TIMESTAMPTZ | Types.TIMESTAMP_TZ | DateColumn | ★ 时区信息丢失 |
| SQL Server | MONEY | Types.DECIMAL | DoubleColumn | ★ 精度不足 |

### 3.4 步骤四：fetchSize 调优——Integer.MIN_VALUE 流式读取实战

**目标**：对比默认 fetchSize 与流式读取在内存和性能上的差异。

**测试场景**：MySQL 单表 5000 万行，每行约 500B，单 Task 同步。

```json
{
    "reader": {
        "name": "mysqlreader",
        "parameter": {
            "jdbcUrl": "jdbc:mysql://localhost:3306/test?useSSL=false",
            "username": "root",
            "password": "xxx",
            "table": "big_orders",
            "column": ["id", "order_no", "amount", "create_time"],
            "splitPk": "id",
            "fetchSize": 0
        }
    }
}
```

**fetchSize 行为对比测试**：

| fetchSize | 数据拉取方式 | 峰值堆内存 | GC 频率 | QPS | 网络往返 |
|-----------|------------|----------|--------|-----|---------|
| 0（默认） | 客户端缓冲 1 万行 | 2.8GB | 每 3 秒一次 Full GC | 18 万条/s | 约 5000 次 |
| 1024 | 每次网络往返返回 1024 行 | 512MB | 每 30 秒一次 Minor GC | 15 万条/s | 约 5 万次 |
| Integer.MIN_VALUE | 逐行流式拉取 | 128MB | 几乎不 GC | 8 万条/s | 5000 万次 |

**结论**：
- `fetchSize=0`（默认）：QPS 最高，但内存爆炸，不适合大表
- `fetchSize=Integer.MIN_VALUE`：内存极低，适合大表，但网络往返次数指数级增加，QPS 下降约 50%
- **最优实践**：`fetchSize=2048~8192`，在内存和性能间取平衡

**建议**：
- 表 < 100 万行 → `fetchSize=0`（默认即可）
- 表 100 万～5000 万行 → `fetchSize=4096`
- 表 > 5000 万行且内存紧张 → `fetchSize=Integer.MIN_VALUE`

### 3.5 步骤五：DBUtil 工具类关键方法一览

**目标**：了解 `DBUtil` 提供的核心工具方法，方便自定义插件开发。

```java
// DBUtil.java — 核心工具方法
public class DBUtil {
    
    // ★ 获取数据库连接（自动适配不同数据库的连接参数）
    public static Connection getConnection(
            DataBaseType dataBaseType, 
            String jdbcUrl, 
            String username, 
            String password) {
        
        Properties props = new Properties();
        props.setProperty("user", username);
        props.setProperty("password", password);
        
        // ★ 不同数据库的特殊连接属性
        switch (dataBaseType) {
            case MySql:
                props.setProperty("useSSL", "false");
                props.setProperty("characterEncoding", "UTF-8");
                props.setProperty("connectTimeout", "30000");
                props.setProperty("socketTimeout", "1800000"); // 30 分钟
                break;
            case Oracle:
                props.setProperty("oracle.jdbc.ReadTimeout", "1800000");
                break;
            case PostgreSQL:
                props.setProperty("ApplicationName", "DataX");
                props.setProperty("connectTimeout", "30");
                break;
        }
        
        return DriverManager.getConnection(jdbcUrl, props);
    }
    
    // ★ 执行 SQL（用于 preSql / postSql）
    public static void executeSql(
            DataBaseType dataBaseType,
            String jdbcUrl, String username, String password,
            String sql) {
        
        Connection conn = getConnection(dataBaseType, jdbcUrl, username, password);
        Statement stmt = conn.createStatement();
        stmt.execute(sql);
        stmt.close();
        conn.close();
    }
    
    // ★ 验证表是否存在
    public static boolean isTableExists(
            DataBaseType dataBaseType, Connection conn, String tableName) {
        
        ResultSet rs = conn.getMetaData().getTables(
            null, null, tableName, null);
        return rs.next();
    }
    
    // ★ 验证列是否存在
    public static boolean isColumnExists(
            DataBaseType dataBaseType, Connection conn, 
            String tableName, List<String> columns) {
        
        ResultSet rs = conn.getMetaData().getColumns(
            null, null, tableName, null);
        Set<String> existingColumns = new HashSet<>();
        while (rs.next()) {
            existingColumns.add(rs.getString("COLUMN_NAME"));
        }
        return existingColumns.containsAll(columns);
    }
}
```

## 4. 项目总结

### 4.1 CommonRdbmsReader 架构全景

```
JSON 配置
  │
  ▼
CommonRdbmsReader.Job
  ├─ init()         → 解析 jdbcUrl → 推断 DataBaseType → 验证配置
  ├─ preCheck()     → 验证连接 + 验证表/列存在性
  ├─ prepare()      → 执行 session 参数（如 Oracle: ALTER SESSION）
  ├─ split()        → SingleTableSplitUtil.splitSingleTable()
  │                   └─ 根据 splitPk + channel 生成 N 个 WHERE 条件
  ├─ post()         → 统计输出
  └─ destroy()      → 释放连接池
        │
        ▼ (每个 Task 执行)
CommonRdbmsReader.Task
  ├─ startRead(RecordSender)
  │   ├─ DBUtil.getConnection() → JDBC Connection
  │   ├─ ps.setFetchSize()     → 流式/批量读取
  │   ├─ rs.executeQuery()     → ResultSet
  │   └─ while (rs.next())
  │       ├─ DBUtil.convertToColumn(rs, i, columnType) → Column
  │       ├─ record.addColumn(column)
  │       └─ recordSender.sendToWriter(record)
  └─ recordSender.terminate()
```

### 4.2 优点

1. **数据库无关性**：DataBaseType 枚举封装了所有数据库差异，增删数据库无需改动核心逻辑
2. **类型映射自动化**：JDBC Types → DataX Column 的转换自动完成，开发者无需手动决定
3. **流式读取支持**：`fetchSize=Integer.MIN_VALUE` 让大表同步不再 OOM
4. **配置驱动**：所有参数通过 JSON 配置，Reader 实现不写死任何值
5. **语句注入安全**：`querySql` 通过 `PreparedStatement` 执行，避免 SQL 注入

### 4.3 缺点

1. **DECIMAL 精度丢失**：高精度 `DECIMAL(38,18)` → `DoubleColumn` 可能截断
2. **时区信息丢失**：`TIMESTAMPTZ` → `DateColumn` 丢失时区偏移
3. **JSON 类型不支持原生映射**：MySQL JSON → StringColumn，非结构化数据需二次解析
4. **fetchSize 不可按列差异化**：所有列共用一个 fetchSize，无法为超大字段单独优化
5. **连接无心跳**：长时间查询时连接可能被中间网络设备（防火墙/NAT）断掉

### 4.4 适用场景

1. 任何 JDBC 兼容的关系型数据库全量同步（MySQL、Oracle、PG、SQL Server 等）
2. 大表全量迁移（`fetchSize=Integer.MIN_VALUE` 保证内存安全）
3. 多数据库类型统一接入（一套 Reader 代码支持所有数据库）
4. 列级映射与类型转换（默认 Column 映射 + 自定义转换逻辑）
5. 增量同步场景（复用 CommonRdbmsReader，在 WHERE 条件里加时间戳过滤）

### 4.5 注意事项

1. `fetchSize=Integer.MIN_VALUE` 只对 MySQL 有效（Oracle/PG 有不同的流式读取方式）
2. `columnType` 来自 `ResultSetMetaData.getColumnTypeName()`，是数据库原生类型名
3. `splitPk` 必须是有索引的列——否则 `SELECT MIN/MAX` 全表扫描
4. `querySql` 模式会跳过所有分片逻辑——要自己控制数据量
5. Druid 连接池的 `maxActive` 默认 1——多 Channel 时每个 Channel 一个连接，不会超标

### 4.6 常见踩坑经验

**坑 1：Oracle NUMBER → DoubleColumn 的精度灾难**

某项目同步 Oracle 财务表，收款金额字段 `NUMBER(20,4)` 被映射为 `DoubleColumn`。`DoubleColumn` 在 Java 中是 double 类型（IEEE 754 64-bit），精度约 15-16 位有效数字。20 位整数 + 4 位小数的 NUMBER 值（共 24 位有效数字）在 double 中无法精确表示——产生 0.000001 的误差。修复：重写 `convertToColumn`，对 NUMBER 类型用 `rs.getBigDecimal(i).toPlainString()` → `StringColumn`。

**坑 2：MySQL fetchSize=Integer.MIN_VALUE 导致查询超时**

流式读取时，每次 `rs.next()` 都会触发一次网络往返。如果应用和数据库之间有慢速网络（延迟 > 10ms），5000 万次网络往返 = 5000 万 × 10ms = 138 小时。实际测试中，同机房 MySQL 流式读取 5000 万行耗时约 4 小时（单次延迟 < 0.3ms），跨机房则飙升至 30+ 小时。修复：跨机房不要用流式读取，用 `fetchSize=8192`。

**坑 3：PostgreSQL TIMESTAMPTZ → DateColumn 丢失时区**

PG 的 `TIMESTAMPTZ` 在 JDBC 驱动中返回 `java.sql.Timestamp`（无时区）。DataX 将其转为 `DateColumn` 后，写入目标库时目标库的时区可能与源库不同——导致订单创建时间"偏差"数小时。修复：重写 `convertToColumn`，对 `TIMESTAMPTZ` 类型保留原始偏移字符串 `rs.getString(i)` 并用 `StringColumn` 传输，在 Writer 端再解析。

### 4.7 思考题

1. 如果你的源库是 Oracle，字段为 `NUMBER(38,0)`，DataX 默认映射为 `DoubleColumn`。但实际业务要求精确到个位数（17 位以上精度）。除了改为 `StringColumn`，还有其他方案吗？对比各方案的优缺点。
2. DataX 的 `fetchSize` 配置在 Reader `parameter` 中——如果同一个 Job 有两个 Content（两张表），表 A 千万行、表 B 百万行，能否为表 A 和表 B 分别设置不同的 `fetchSize`？如果不能，设计一个扩展方案。

（答案见附录）
