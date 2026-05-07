# 第12章：RDBMS 通用读写——多数据库一把梭

## 1. 项目背景

某集团公司在并购后需要将三个子公司的数据统一到总部数据仓库。三个子公司用的数据库各不相同：A 公司用 MySQL 5.7，B 公司用 Oracle 11g，C 公司用 PostgreSQL 13。数据平台的 TL 最初计划为每种数据库写一套 ETL 脚本，分别维护三套 sqoop 命令。但运维提出了一个致命问题——三套脚本的代码重复率高达 80%，只是数据库驱动和 JDBC URL 不一样。任何一个字段映射的修改需要改三处。

DataX 的 plugin-rdbms-util 模块提供了对关系型数据库的统一抽象。30+ 种 Reader 插件和 20+ 种 Writer 插件共享同一套基类：`CommonRdbmsReader` 和 `CommonRdbmsWriter`。MySQL Reader 和 Oracle Reader 的代码差异只有数据库驱动 JAR 和少量方言配置。本章带你通过 plugin-rdbms-util 的源码，理解"一套代码，适配所有 RDBMS"的设计哲学，并实战完成 Oracle → PostgreSQL 的跨数据库全量迁移。

## 2. 项目设计——剧本式交锋对话

**（技术方案评审会，白板上画着三个数据库的图标）**

**小胖**：（趴在桌子上）我快疯了。昨天刚改完 MySQL Reader 的日期格式，今天 Oracle Reader 的报错了，同样的逻辑要写三遍！

**小白**：（翻开 DataX 源码）其实你不用改三遍。你看 plugin-rdbms-util——它定义了 `CommonRdbmsReader`，mysqlreader、oraclereader、postgresqlreader 这三个插件都只是它的子类，总共不到 100 行代码。

**大师**：（站起来在白板上画了一个继承树）关键的设计模式——模板方法模式。

```
AbstractPlugin (定义生命周期框架)
  └── Reader (插件契约)
        └── Reader.Job (Job级接口)
              └── CommonRdbmsReader.Job (RDBMS通用实现)
                    └── MysqlReader.Job (只覆盖差异部分: 分片SQL、类型映射)
                    └── OracleReader.Job (只覆盖差异部分: session参数)
                    └── PostgresqlReader.Job (只覆盖差异部分: COPY模式)
```

Plugin-rdbms-util 提供了 90% 的通用逻辑——JDBC 连接、ResultSet 遍历、类型映射、分片策略。每个具体数据库插件只需要覆盖 10% 的差异逻辑——数据库驱动加载方式、方言 SQL、session 参数。

**技术映射**：plugin-rdbms-util = 自动挡汽车。你只需要选品牌（MySQL/Oracle/PG），发动机和变速箱（Reader/Writer 核心逻辑）都是通用的。

**小胖**：那不同数据库的 SQL 方言怎么处理？比如 Oracle 的 `ROWNUM` vs MySQL 的 `LIMIT`？

**大师**：好问题。这就是 `DataBaseType` 枚举的作用——记录了每种数据库的方言：

```java
public enum DataBaseType {
    MySQL("mysql", "com.mysql.jdbc.Driver"),
    Oracle("oracle", "oracle.jdbc.OracleDriver"),
    PostgreSQL("postgresql", "org.postgresql.Driver"),
    SqlServer("sqlserver", "com.microsoft.sqlserver.jdbc.SQLServerDriver"),
    // ...
    
    private String driverClass;
    public String getDriverClass() { return this.driverClass; }
}
```

在 `CommonRdbmsReader` 的 split 方法中，根据 `DataBaseType` 来决定：MySQL 用 `LIMIT ?,?`，Oracle 用 `ROWNUM <= ?`，PostgreSQL 用 `OFFSET ? LIMIT ?`。

**小白**：（追问）那 Oracle 的分片策略和 MySQL 一样吗？都用 splitPk 等距切分？

**大师**：核心算法一样——查 MIN/MAX，等距分片。但 Oracle 有一个额外的优化：并行度。你可以在 `session` 参数中写 `ALTER SESSION ENABLE PARALLEL DML`，Oracle 的并行查询会把一个 SQL 分发到多个 CPU 上执行。这跟 DataX 自身的 channel 并发是两个层级的并行——DataX 是应用层多线程，Oracle 并行是数据库层多进程。两者结合能达到更好的效果。

**小胖**：那 PostgreSQL 呢？我听说 pgloader 同步 PG 特别快——DataX 比它慢多少？

**大师**：PostgreSQL 的 **COPY 模式** 是 PG 特有的高速导入机制——它绕过 SQL 解析器，直接把二进制数据流灌入表中，比 INSERT 快 5-10 倍。DataX 的 postgresqlwriter 目前只支持 INSERT 模式，这是它的短板。如果需要 COPY 模式的高性能，可以自定义扩展 Writer 插件。

## 3. 项目实战

### 3.1 步骤一：Oracle → PostgreSQL 全量迁移

**目标**：将 Oracle 11g 中的 `employee` 表迁移到 PostgreSQL 13。

**Oracle Reader 配置**：

```json
{
    "reader": {
        "name": "oraclereader",
        "parameter": {
            "username": "hr",
            "password": "${ORACLE_PWD}",
            "column": ["EMP_ID", "EMP_NAME", "SALARY", "HIRE_DATE", "DEPT_ID"],
            "splitPk": "EMP_ID",
            "connection": [{
                "table": ["HR.EMPLOYEES"],
                "jdbcUrl": ["jdbc:oracle:thin:@//10.0.1.100:1521/ORCLPDB"]
            }],
            "session": [
                "ALTER SESSION SET NLS_DATE_FORMAT = 'YYYY-MM-DD HH24:MI:SS'",
                "ALTER SESSION SET NLS_TIMESTAMP_FORMAT = 'YYYY-MM-DD HH24:MI:SS.FF'"
            ]
        }
    }
}
```

**Oracle 特殊配置注意**：
- `jdbcUrl` 格式：`jdbc:oracle:thin:@//host:port/SERVICE_NAME`（注意双斜杠）
- `session` 中必须设置 `NLS_DATE_FORMAT`，否则 Oracle 返回的日期格式可能是 `DD-MON-YY`，DateColumn 解析失败
- `column` 中的列名建议大写

**PostgreSQL Writer 配置**：

```json
{
    "writer": {
        "name": "postgresqlwriter",
        "parameter": {
            "username": "etl_user",
            "password": "${PG_PWD}",
            "column": ["emp_id", "emp_name", "salary", "hire_date", "dept_id"],
            "writeMode": "insert",
            "preSql": [
                "CREATE TABLE IF NOT EXISTS employees (
                    emp_id BIGINT PRIMARY KEY,
                    emp_name VARCHAR(100),
                    salary DECIMAL(10,2),
                    hire_date DATE,
                    dept_id INT
                )"
            ],
            "connection": [{
                "table": ["employees"],
                "jdbcUrl": ["jdbc:postgresql://10.0.1.200:5432/hr_db?currentSchema=public"]
            }]
        }
    }
}
```

**关键适配点**：
- Oracle `DATE` 包含时间信息 → PG `DATE` 只包含日期（时间信息丢失）
  - 解决：目标表用 `TIMESTAMP` 而非 `DATE`
- Oracle `NUMBER(10,2)` → PG `DECIMAL(10,2)`（PG 不推荐用 NUMBER）
- 列名从大写（Oracle 习惯）转为小写（PG 习惯）——DataX 的 Column 映射对大小写不敏感

### 3.2 步骤二：SQL Server → MySQL 迁移

**SQL Server Reader 重点配置**：

```json
{
    "reader": {
        "name": "sqlserverreader",
        "parameter": {
            "username": "sa",
            "password": "${MSSQL_PWD}",
            "column": ["order_id", "customer_name", "total_amount", "order_date"],
            "splitPk": "order_id",
            "connection": [{
                "table": ["dbo.Orders"],
                "jdbcUrl": ["jdbc:sqlserver://10.0.1.50:1433;databaseName=SalesDB"]
            }],
            "session": [
                "SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED"
            ]
        }
    }
}
```

**SQL Server 特殊配置**：
1. **Windows 认证**（不用 sa 账号）：
```
jdbc:sqlserver://host:1433;databaseName=SalesDB;integratedSecurity=true
```
但 DataX 运行所在的 Linux 需要配置 Kerberos 或 NTLM 库。

2. **大字段查询超时**：SQL Server 的 `TEXT/NTEXT/IMAGE` 大字段的 `ResultSet` 读取比普通字段慢一个数量级。如果不需要这些字段，在 `column` 中排除。

3. **splitPk 用自增列**：SQL Server 的 `IDENTITY` 列天然有序，非常适合做分片键。

### 3.3 步骤三：plugin-rdbms-util 通用能力一览

**目标**：了解通用模块提供了哪些能力，减少重复开发。

**CommonRdbmsReader 提供的通用能力**：

| 能力 | 位置 | 说明 |
|------|------|------|
| JDBC 连接管理 | DBUtil.getConnection() | Druid 连接池管理，支持多 jdbcUrl 轮询 |
| SQL 执行 | DBUtil.query() | 封装了 Statement/PreparedStatement |
| 类型映射 | CommonRdbmsReader.Task.buildRecord() | JDBC Types → DataX Column 的自动映射表 |
| 分片策略 | SingleTableSplitUtil.genSplitSql() | 等距切分、哈希取模切分 |
| session 注入 | CommonRdbmsReader.Job.prepare() | 执行 session 数组中的 SQL |
| 错误处理 | CommonRdbmsReader 异常包装 | 将 JDBC 异常统一转为 DataXException |

**CommonRdbmsWriter 提供的通用能力**：

| 能力 | 位置 | 说明 |
|------|------|------|
| 批量写入 | CommonRdbmsWriter.Task.startWrite() | PreparedStatement.addBatch() + executeBatch() |
| 写入模式适配 | 根据 writeMode 生成不同 SQL 模板 | insert/replace/update/delete |
| preSql/postSql | CommonRdbmsWriter.Job | Job 级别的准备/清理 SQL |
| 脏数据收集 | AbstractTaskPluginCollector | 自动收集写入失败的行 |
| 空值处理 | setWithNull 参数 | 控制 NULL 值的写入行为 |

### 3.4 步骤四：各数据库 JDBC URL 速查表

| 数据库 | JDBC URL 模板 | Driver Class | Maven 坐标 |
|--------|--------------|-------------|------------|
| MySQL | `jdbc:mysql://host:3306/db?useSSL=false&serverTimezone=Asia/Shanghai` | `com.mysql.cj.jdbc.Driver` | mysql:mysql-connector-java |
| Oracle | `jdbc:oracle:thin:@//host:1521/service` | `oracle.jdbc.OracleDriver` | com.oracle.database.jdbc:ojdbc8 |
| PostgreSQL | `jdbc:postgresql://host:5432/db?currentSchema=public` | `org.postgresql.Driver` | org.postgresql:postgresql |
| SQL Server | `jdbc:sqlserver://host:1433;databaseName=db` | `com.microsoft.sqlserver.jdbc.SQLServerDriver` | com.microsoft.sqlserver:mssql-jdbc |
| DRDS | `jdbc:mysql://host:3306/db` | `com.mysql.jdbc.Driver` | 同 MySQL |
| OceanBase | `jdbc:oceanbase://host:2883/db` | `com.alipay.oceanbase.jdbc.Driver` | com.oceanbase:oceanbase-client |
| GaussDB | `jdbc:gaussdb://host:25308/db` | `com.huawei.gaussdb.jdbc.Driver` | 华为 gaussdb-jdbc |
| Sybase | `jdbc:sybase:Tds:host:5000/db` | `net.sourceforge.jtds.jdbc.Driver` | jtds:jtds |

### 3.5 步骤五：自定义 session 参数的跨库适配

**目标**：不同数据库的 session 语法差异巨大，掌握各自的写法。

```json
// MySQL session（设置事务隔离级别+超时）
"session": [
    "SET SESSION TRANSACTION ISOLATION LEVEL READ UNCOMMITTED",
    "SET SESSION innodb_lock_wait_timeout = 3",
    "SET SESSION wait_timeout = 86400"
]

// Oracle session（修改会话级参数）
"session": [
    "ALTER SESSION SET NLS_DATE_FORMAT = 'YYYY-MM-DD HH24:MI:SS'",
    "ALTER SESSION SET NLS_TIMESTAMP_FORMAT = 'YYYY-MM-DD HH24:MI:SS.FF3'",
    "ALTER SESSION ENABLE PARALLEL DML"
]

// PostgreSQL session
"session": [
    "SET SESSION CHARACTERISTICS AS TRANSACTION ISOLATION LEVEL READ UNCOMMITTED",
    "SET SESSION statement_timeout = '10min'",
    "SET SESSION client_encoding = 'UTF8'"
]

// SQL Server session
"session": [
    "SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED",
    "SET LOCK_TIMEOUT 3000",
    "SET ARITHABORT ON"
]
```

### 3.6 可能遇到的坑及解决方法

**坑1：Oracle jdbcUrl 格式错误**

经常有人写成 `jdbc:oracle:thin:@10.0.1.100:1521:ORCL`（SID 模式），而不是 `jdbc:oracle:thin:@//10.0.1.100:1521/ORCLPDB`（Service Name 模式）。

解决：确认 Oracle DBA 给的是 SID 还是 Service Name。PDB 环境必须用 Service Name 格式（带双斜杠）。

**坑2：PG 默认 schema 不是 `public`**

PG 中如果不指定 `currentSchema`，会默认使用 `$user` 同名的 schema，导致"表不存在"错误。

解决：JDBC URL 中加上 `?currentSchema=目标schema`。

**坑3：日期类型跨库精度不一致**

| 数据库 | 类型 | 精度 | DataX 映射 |
|--------|------|------|-----------|
| MySQL DATETIME | 秒级 | 到秒 | DateColumn |
| Oracle DATE | 秒级 | 到秒 | DateColumn |
| Oracle TIMESTAMP | 纳秒级 | 6位小数 | DateColumn |
| PG TIMESTAMP | 微秒级 | 6位小数 | DateColumn |

跨库迁移时精度丢失：Oracle TIMESTAMP → MySQL DATETIME，纳秒级精度会被截断到秒。

## 4. 项目总结

### 4.1 优点

1. **代码复用率极高**：90% 逻辑共用 CommonRdbmsReader/Writer，减少 bug
2. **新数据库扩展成本低**：只需写 DriverClass + 方言 SQL，3 天可上线一个新 Reader
3. **统一异常处理**：所有 JDBC 异常被包装为 DataXException，日志格式统一
4. **session 灵活注入**：支持每个数据库的方言语法
5. **连接池管理统一**：Druid 连接池对所有数据库一致，运维只用配一个连接池参数

### 4.2 缺点

1. **不支持 COPY/LOAD DATA 等高性能导入**：批量写入只能 INSERT
2. **分片策略固定**：只支持等距切分，不支持基于索引的 Range 切分
3. **Oracle 依赖 ojdbc**：Oracle JDBC 驱动需要从 Oracle 官网下载，Maven 中央仓库没有
4. **PG 大字段性能差**：BYTEA 类型通过 JDBC 读写效率低
5. **不同数据库的 batchSize 最优值不同**：MySQL 约 2048，PG 约 1024，Oracle 约 512

### 4.3 适用场景

1. 跨数据库全量迁移（Oracle → PostgreSQL / MySQL → MySQL 8.0）
2. 多数据库统一数据采集（子公司数据汇聚到总部）
3. 数据库升级迁移（MySQL 5.7 → 8.0）
4. 异构数据库报表同步（SQL Server → MySQL 报表库）
5. 数据库国产化替代（Oracle → 达梦/GuassDB）

### 4.4 注意事项

1. Oracle `VARCHAR2` vs MySQL `VARCHAR` 的字符集长度差异（Oracle 按字节，MySQL 按字符）
2. PG 的 `SERIAL` 自增类型在 INSERT 时不需要显式传值
3. SQL Server 的 `DATETIME` 精度为 3.33ms（非毫秒级），不建议用作分片键
4. 跨库迁移前务必做一次数据采样，确认各列的类型映射正确
5. 大表跨库迁移时，考虑先迁表结构再迁数据（两阶段）

### 4.5 思考题

1. PostgreSQL 的 COPY 模式比 INSERT 快 5-10 倍。如果要在 CommonRdbmsWriter 中新增 `writeMode: "copy"`，需要修改哪些类和接口？
2. Oracle 的 `ROWNUM` 分页和 MySQL 的 `LIMIT` 分页在语义上有何不同？为什么 `CommonRdbmsReader` 的默认分片策略（WHERE id >= ? AND id < ?）比 `LIMIT OFFSET` 更高效？

（答案见附录）
