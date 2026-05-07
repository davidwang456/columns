# 第7章：MySQL Writer——四种写入模式实战

## 1. 项目背景

电商平台的运营团队每天需要将"今日热销商品榜单"从 Hive 数仓同步回 MySQL 报表库。需求看起来简单——跑一次全量覆盖就行。但实际执行时遇到问题：第一天全量同步了 1000 行顺利入库；第二天又全量同步 1000 行，但其中 200 行是昨天已经存在的商品（销量更新了），800 行是新增商品。运营要求"已有的更新销量，新增的直接插入"。

如果建表时定义了 `PRIMARY KEY (product_id)`，MySQL 的 `INSERT` 遇到主键冲突会直接报错。研发起初用了一个笨办法——先 `TRUNCATE` 清空目标表，再全量插入。问题是大表 TRUNCATE 会短暂锁表，导致前端报表查询中断。而且 TRUNCATE 后如果写入失败，表是空的，比数据不一致更严重。

DataX MySQL Writer 提供了四种写入模式应对不同场景：`insert`（追加）、`replace`（覆盖）、`update`（更新）、`delete`（删除）。本章带你掌握每种模式的适用场景、SQL 生成逻辑和批量写入优化技巧。

## 2. 项目设计——剧本式交锋对话

**（午餐时间，食堂角落里）**

**小胖**：（边吃炸鸡边刷手机）我真服了，运营又来找我，说榜单数据不对。我一看，原来 INSERT 遇到主键冲突直接跳异常，后面的 batch 全丢了。

**小白**：（端着一碗素面）你不能用 REPLACE INTO 吗？主键冲突自动覆盖，一条 SQL 搞定。

**小胖**：试过了！REPLACE 的问题是——它先 DELETE 再 INSERT。如果目标表有自增主键，DELETE 会把自增 ID 腾出来，INSERT 时可能重用旧 ID，但前端缓存的是新 ID，数据全乱了。

**大师**：（端着红烧肉走过来）你们在讨论写入模式？正好，我给你们画个表。

| 模式 | SQL | 主键冲突行为 | 适用场景 |
|------|-----|------------|---------|
| insert | `INSERT INTO t VALUES (...) ON DUPLICATE KEY UPDATE ...` | 主键冲突可配置为 skip/update（取决于配置） | 纯追加，不关心已有数据 |
| replace | `REPLACE INTO t VALUES (...)` | 删除旧行→插入新行 | 全量覆盖，目标表无自增字段依赖 |
| update | `UPDATE t SET c1=?, c2=? WHERE pk=?` | 只更新匹配行，不存在则跳过 | 增量更新，按主键/唯一键定位 |
| delete | `DELETE FROM t WHERE pk=?` | 只删除匹配行 | 源端删除的数据同步到目标端 |

**技术映射**：四种写入模式 = 仓库管理四件套。insert = 新货入仓（放到空位），replace = 旧货换新（把旧箱子扔掉换新的），update = 盘点更新（只修改标签不改货物），delete = 清仓处理（直接搬走）。

**小胖**：（放下鸡腿）等一下，我怎么记得 DataX 的 insert 模式不会用 ON DUPLICATE KEY UPDATE？它不就是简单的 `INSERT INTO t (c1,c2) VALUES (?,?)` 吗？

**大师**：（赞许地点头）小胖这次对了。DataX 原生的 insert 模式确实只是 INSERT ... VALUES，遇到主键冲突直接抛 `DuplicateKeyException`，由脏数据机制收集。后来社区扩展了不同版本，有的在 Writer 层面加了 `onConflict='update'` 参数。

**小白**：（快速翻文档）那 update 模式的 WHERE 子句怎么生成？我知道是 `UPDATE t SET c1=?,c2=? WHERE pk=?`，但 WHERE 条件里的 pk 值从哪来？

**大师**：好问题。update 模式需要显式配置 `updateKey` 参数——告诉 DataX 用哪些列作为定位条件：

```json
"parameter": {
    "writeMode": "update",
    "column": ["product_id", "product_name", "sales_volume", "update_time"],
    "updateKey": ["product_id"]  // ← 用 product_id 定位要更新的行
}
```

生成的 SQL 就是：
```sql
UPDATE hot_products SET product_name=?, sales_volume=?, update_time=? 
WHERE product_id=?
```

**技术映射**：updateKey = 快递单号。快递员（DataX）不知道你要改哪件货，必须靠单号定位。

**小胖**：（吃完最后一块炸鸡）那 delete 模式呢？谁会用 DataX 去删除数据啊？

**大师**：有两种典型场景：
1. **对账删除**：源端删除了某个用户的数据，目标端也需要同步删除。用 DataX 读源端的"删除日志表"，Writer 模式设为 `delete`，按主键删除目标端对应行。
2. **临时表清理**：用 StreamReader 生成待删主键列表，Writer 批量删除。

## 3. 项目实战

### 3.1 步骤一：insert 模式——批量追加写入

**目标**：将 `source_products` 全量导入 `target_products`（空表，纯追加）。

```json
{
    "writer": {
        "name": "mysqlwriter",
        "parameter": {
            "username": "root",
            "password": "root123",
            "column": ["product_id", "product_name", "price", "stock", "create_time"],
            "writeMode": "insert",
            "batchSize": 2048,
            "preSql": [],
            "postSql": [],
            "connection": [{
                "table": ["target_products"],
                "jdbcUrl": ["jdbc:mysql://localhost:3306/report_db"]
            }]
        }
    }
}
```

生成的 SQL（每条 batch 执行一次，batchSize=3 的示例）：

```sql
INSERT INTO target_products (product_id, product_name, price, stock, create_time) 
VALUES (?, ?, ?, ?, ?), (?, ?, ?, ?, ?), (?, ?, ?, ?, ?)
```

**批量提交源码逻辑**（CommonRdbmsWriter.Task.startWrite 简版）：

```java
public void startWrite(RecordReceiver recordReceiver) {
    Connection conn = DBUtil.getConnection(jdbcUrl, username, password);
    conn.setAutoCommit(false);  // 关闭自动提交，手动批量
    
    String sql = buildInsertSql(); // INSERT INTO t (c1,c2) VALUES (?,?)
    PreparedStatement ps = conn.prepareStatement(sql);
    
    int batchCount = 0;
    Record record;
    while ((record = recordReceiver.getFromReader()) != null) {
        // 检查是否为结束标记
        if (record instanceof TerminateRecord) break;
        
        // Record 逐列映射到 PreparedStatement
        for (int i = 0; i < columnNumber; i++) {
            Column col = record.getColumn(i);
            ps.setObject(i + 1, col.getRawData());
        }
        ps.addBatch();
        batchCount++;
        
        // 达到 batchSize 阈值，执行批量提交
        if (batchCount >= batchSize) {
            try {
                ps.executeBatch();
                conn.commit();
            } catch (BatchUpdateException e) {
                // 逐条重试，找出失败行
                handleBatchException(e, ps, batchCount);
            }
            batchCount = 0;
        }
    }
    
    // 提交最后一批
    if (batchCount > 0) {
        ps.executeBatch();
        conn.commit();
    }
}
```

### 3.2 步骤二：replace 模式——全量覆盖

**目标**：每天全量覆盖榜单表，主键冲突自动替换。

```json
{
    "writer": {
        "name": "mysqlwriter",
        "parameter": {
            "writeMode": "replace",
            "batchSize": 2048,
            "preSql": [],
            "postSql": [
                "UPDATE report_db.hot_ranking SET update_time = NOW()"
            ],
            "connection": [{
                "table": ["hot_ranking"],
                "jdbcUrl": ["jdbc:mysql://localhost:3306/report_db"]
            }]
        }
    }
}
```

封装生成的 SQL：

```sql
REPLACE INTO hot_ranking (id, product_name, sales_volume, rank, update_time) 
VALUES (?, ?, ?, ?, ?), (?, ?, ?, ?, ?), ...
```

**REPLACE 的隐藏问题——自增 ID 跳跃验证**：

```sql
-- 测试环境
CREATE TABLE replace_test (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(50)
);

INSERT INTO replace_test (name) VALUES ('A');   -- id=1
INSERT INTO replace_test (name) VALUES ('B');   -- id=2

REPLACE INTO replace_test (id, name) VALUES (1, 'A_new');  -- id=1 被删除后重新插入，自增计数+1
INSERT INTO replace_test (name) VALUES ('C');   -- 期望id=3，实际id=4！
```

**验证**：DataX 文档中也明确警告——如果目标表有自增主键且被其他表作为外键引用，**禁止使用 replace 模式**，改用 update 模式。

### 3.3 步骤三：update 模式——增量更新

**目标**：只更新已有记录，不插入新记录。

```json
{
    "writer": {
        "name": "mysqlwriter",
        "parameter": {
            "writeMode": "update",
            "column": ["id", "user_name", "balance", "update_time"],
            "updateKey": ["id"],
            "batchSize": 2048,
            "connection": [{
                "table": ["user_account"],
                "jdbcUrl": ["jdbc:mysql://localhost:3306/report_db"]
            }]
        }
    }
}
```

生成的 SQL（batchSize=3 的示例）：

```sql
UPDATE user_account SET user_name=?, balance=?, update_time=? WHERE id=?;
UPDATE user_account SET user_name=?, balance=?, update_time=? WHERE id=?;
UPDATE user_account SET user_name=?, balance=?, update_time=? WHERE id=?;
```

**注意事项**：
1. `updateKey` 中的列必须包含在 `column` 数组中
2. 生成的 UPDATE 语句不具备幂等性（batch 中某条失败不会回滚前面成功的）
3. 如果 WHERE 条件匹配不到行，UPDATE 不会报错（受影响行数为 0），也不会产生脏数据——优雅地"跳过"

### 3.4 步骤四：delete 模式——按条件删除

**目标**：读取源端的"待删除 ID 列表"，删除目标端对应行。

```json
{
    "reader": {
        "name": "streamreader",
        "parameter": {
            "column": [
                {"type": "long", "random": "1,1000"}
            ],
            "sliceRecordCount": 50
        }
    },
    "writer": {
        "name": "mysqlwriter",
        "parameter": {
            "writeMode": "delete",
            "column": ["id"],
            "updateKey": ["id"],  // delete 模式实际上也读 updateKey 作为 WHERE 条件
            "batchSize": 2048,
            "connection": [{
                "table": ["target_products"],
                "jdbcUrl": ["jdbc:mysql://localhost:3306/report_db"]
            }]
        }
    }
}
```

生成的 SQL：

```sql
DELETE FROM target_products WHERE id=?;
DELETE FROM target_products WHERE id=?;
```

**安全警告**：delete 模式非常危险！如果 `updateKey` 配置错误（如缺失主键），可能导致全表数据被误删。生产环境使用 delete 模式前，**必须在测试环境验证 WHERE 条件**。

### 3.5 步骤五：preSql 与 postSql 的高级用法

**目标**：掌握写入前的准备和写入后的收尾 SQL。

```json
{
    "writer": {
        "name": "mysqlwriter",
        "parameter": {
            "preSql": [
                "CREATE TABLE IF NOT EXISTS daily_report (
                    id BIGINT PRIMARY KEY,
                    report_date DATE,
                    total_amount DECIMAL(18,2),
                    INDEX idx_date (report_date)
                )",
                "DELETE FROM daily_report WHERE report_date = '${bizdate}'"
            ],
            "postSql": [
                "ANALYZE TABLE daily_report",
                "INSERT INTO sync_log (table_name, sync_time, status) 
                 VALUES ('daily_report', NOW(), 'SUCCESS')"
            ]
        }
    }
}
```

**preSql 执行时机**：在 `prepare()` 阶段，init 之后、split 之前执行。按数组顺序执行。

**postSql 执行时机**：在 `post()` 阶段，所有 Task 完成后执行。

**典型用法**：

| 阶段 | 常用 SQL | 用途 |
|------|---------|------|
| preSql | `TRUNCATE TABLE` | 清空目标表（全量覆盖） |
| preSql | `CREATE TABLE IF NOT EXISTS` | 建表（防止目标表不存在） |
| preSql | `DROP INDEX` | 删除索引（提升写入速度） |
| preSql | `SET foreign_key_checks = 0` | 关闭外键检查 |
| postSql | `CREATE INDEX` | 重建索引 |
| postSql | `ANALYZE TABLE` | 更新统计信息 |
| postSql | `INSERT INTO sync_log` | 写同步日志 |

### 3.6 可能遇到的坑及解决方法

**坑1：batchSize 太小导致吞吐量上不去**

设为 128 时每秒只能写入 5000 行；调为 2048 后升至 50000 行。

**但注意**：batchSize 太大也有风险——单条 batch 的 SQL 文本长度 = `batchSize × 列数 × 平均列宽`。如果 batchSize=5000，列数=20，每列平均 100 字节，SQL 文本就是 10MB，MySQL 的 `max_allowed_packet` 默认只有 4MB。

解决：`batchSize ≤ max_allowed_packet / (列数 × 平均列宽)`

**坑2：replace 模式下自增主键跳跃**

解决：如果目标表自增主键被其他表引用，使用"先 UPDATE 再 INSERT"的组合策略，而非 replace。

**坑3：update/delete 模式下 updateKey 不包含索引**

如果目标表没有在 updateKey 列上建索引，DELETE/UPDATE 会全表扫描。

解决：
```sql
SHOW INDEX FROM target_table WHERE Column_name = 'user_id';
-- 如果为空，先建索引
CREATE INDEX idx_user_id ON target_table (user_id);
```

**坑4：写入速度忽快忽慢**

原因：目标表在 DataX 写入期间同时有业务查询，产生锁争用。

解决：在 `session` 中降低写入优先级：
```json
"session": [
    "SET SESSION sql_log_bin = 0",           // 关闭 binlog（主从同步用其他方式）
    "SET SESSION tx_isolation = 'READ-COMMITTED'",
    "SET SESSION innodb_autoinc_lock_mode = 2"  // 自增锁改为轻量级
]
```

## 4. 项目总结

### 4.1 四种写入模式完整对比

| 维度 | insert | replace | update | delete |
|------|--------|---------|--------|--------|
| SQL 模板 | INSERT INTO ... VALUES | REPLACE INTO ... VALUES | UPDATE ... SET ... WHERE | DELETE FROM ... WHERE |
| 主键冲突 | 抛异常→收集脏数据 | 先删后插 | 更新现有行 | 删除匹配行 |
| 自增主键 | 正常递增 | 自增ID跳跃 | 不影响 | 不影响 |
| 外键影响 | 无 | 级联删除！ | 外键冲突可能报错 | 级联删除！ |
| 幂等性 | 否 | 是 | 是 | 是 |
| 必然参数 | 无 | 无 | updateKey | updateKey |
| 适用数据量 | 任意 | 中小表 | 大表局部更新 | 少量删除 |

### 4.2 优点

1. **四种模式覆盖全**：增删改全覆盖，一口锅炒四个菜
2. **preSql/postSql 灵活**：准备 + 收尾的扩展能力让 Writer 不只是"写入数据"
3. **批量提交**：batchSize 控制单批 SQL 长度，支持大批量高性能写入
4. **session 参数可配**：SET 语句注入，精细控制事务隔离级别和写入策略
5. **自动批错误重试**：单条失败不回滚整批，而是逐条重试收集脏数据

### 4.3 缺点

1. **update/delete 依赖 updateKey**：updateKey 必须匹配唯一索引，否则多行受影响
2. **batch 粒度不可控**：单 batch 失败后重试整批，无法定位具体失败行
3. **无 upsert 原语**：不支持 `INSERT ... ON DUPLICATE KEY UPDATE`（需社区扩展版）
4. **replace 外键风险**：REPLACE 本质是 DELETE+INSERT，会触发外键级联删除
5. **无分布式事务**：跨实例写入不支持 XA 事务

### 4.4 适用场景

1. insert：日志归档、数据仓库全量导入
2. replace：每日全量榜单覆盖、配置表刷新
3. update：用户余额对账、订单状态回写
4. delete：注销用户数据清理、过期日志清理

### 4.5 注意事项

1. replace 模式下目标表外键必须使用 `ON DELETE NO ACTION` 或 `ON DELETE SET NULL`
2. update/delete 模式必须确保 updateKey 列有索引
3. batchSize = 1024~4096 之间性价比最高（经验值）
4. preSql 中如有 DDL，确保 `IF EXISTS` / `IF NOT EXISTS` 防御性写法
5. postSql 失败不影响任务最终状态（post 阶段异常不抛到上层）

### 4.6 思考题

1. 如果需要实现 `INSERT ... ON DUPLICATE KEY UPDATE` 模式，如何在 CommonRdbmsWriter 中扩展一个新的写入模式？需要修改哪些类和配置？
2. 如果同时配置了 `writeMode: "update"` 和 `column: ["*"]`，生成的 UPDATE SET 子句会包含哪些列？updateKey 的列是否出现在 SET 子句中？

（答案见附录）
