# 第20章：数据库Schema设计与元数据管理

## 1. 项目背景

### 业务场景

运营人员反馈：Azkaban Web界面加载Project列表需要30秒以上。运维排查发现，`execution_flows`表已经有800万条记录，每次查询Project列表时Azkaban会JOIN该表做聚合统计，导致全表扫描。

更糟的是，有同事看表太大，手动在MySQL中执行了`DELETE FROM execution_flows WHERE start_time < '2024-01-01'`——结果Azkaban立刻崩了。原因是`execution_flows`与`execution_jobs`、`execution_logs`有外键约束，直接DELETE会级联删除大量记录，导致MySQL事务锁表长达数分钟。

### 痛点放大

不了解Azkaban数据库Schema时：

1. **误操作导致服务中断**：直接操作MySQL表可能触发锁表、级联删除
2. **查询慢难以定位**：不知道哪些表需要索引优化
3. **存储膨胀**：不知道哪些表增长最快，磁盘满了才发现
4. **数据迁移困难**：不知道表之间的依赖关系，无法安全备份/恢复

## 2. 项目设计——剧本式交锋对话

**小胖**（后悔莫及地）：大师，我闯祸了！我刚才手贱在MySQL里删了一些旧数据，结果Azkaban挂了好几分钟，Web界面全部报502……

**大师**：直接操作Azkaban的MySQL表是大忌！你删的应该是`execution_flows`吧？这个表与另外4张表有外键关系，你的DELETE触发了级联操作，锁住了整个表直到事务完成。

**小白**：Azkaban有哪些核心表？它们之间是什么关系？

**大师**（画出ER图）：

```
Azkaban核心表结构（简化版）：
┌─────────────────┐
│    projects      │  项目表
│  - id (PK)       │
│  - name          │
│  - active        │
└────────┬────────┘
         │ 1:N
         ▼
┌─────────────────┐     ┌──────────────────┐
│  project_files   │     │  project_versions │  版本历史
│  - project_id    │     │  - project_id     │
│  - version       │     │  - version        │
│  - file_name     │     │  - upload_time    │
│  - file_content  │     └──────────────────┘
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  project_flows   │  Flow元数据
│  - project_id    │
│  - flow_id       │
│  - flow_name     │
└────────┬────────┘
         │ 1:N
         ▼
┌──────────────────────┐
│  execution_flows      │  每次执行记录
│  - exec_id (PK)       │
│  - project_id         │
│  - flow_id            │
│  - status             │
│  - start_time         │
│  - end_time           │
└──────────┬───────────┘
           │ 1:N
           ▼
┌──────────────────────┐
│  execution_jobs       │  每个Job的执行记录
│  - exec_id            │
│  - job_id             │
│  - status             │
│  - start_time         │
│  - end_time           │
└──────────┬───────────┘
           │ 1:N
           ▼
┌──────────────────────┐
│  execution_logs       │  Job执行日志
│  - exec_id            │
│  - name (job_name)    │
│  - log_content (TEXT) │
│  - upload_time        │
└──────────────────────┘

┌──────────────────────┐
│  triggers             │  调度计划
│  - trigger_id (PK)    │
│  - trigger_source     │
│  - cron_expression    │
└──────────────────────┘

┌──────────────────────┐
│  QRTZ_*              │  Quartz内部表(5张)
│  - QRTZ_TRIGGERS     │
│  - QRTZ_CRON_TRIGGERS│
│  - QRTZ_JOB_DETAILS  │
│  - QRTZ_FIRED_TRIGGERS│
│  - QRTZ_SCHEDULER_STATE│
└──────────────────────┘
```

**小胖**：所以最核心的操作链是：Project → Flow → Execution → Job → Log，一层套一层！

**大师**：对。执行记录是最容易膨胀的。我给你一组数据：

| 场景 | 每天执行数 | 3个月后execution_flows行数 | 日志总大小 |
|------|----------|-------------------------|----------|
| 小型 | 20个Flow | ~1800行 | ~200MB |
| 中型 | 200个Flow | ~18000行 | ~2GB |
| 大型 | 2000个Flow | ~180000行 | ~20GB |

所以`execution_flows`和`execution_logs`两个表是存储优化的重点。

**小白**：那Azkaban自己有没有清理旧数据的机制？

**大师**：有，但很基础。Azkaban有一个CleanerThread后台线程，根据`azkaban.log.retention.days`参数（默认30天）定期清理`execution_logs`表。但它不会清理`execution_flows`和`execution_jobs`——这意味着即使日志被清除了，执行记录还在。

对于活跃的集群，你需要额外的清理策略。

### 技术映射总结

- **project_files** = 档案室（存储了每个版本的Flow文件）
- **execution_flows** = 快递单号记录（每次执行一张单子）
- **execution_logs** = 快递员的工作日志（最大、最占空间、最后才查看）
- **级联删除** = 拆房子挖地基（看起来只拆一座，但整条街都受影响）

## 3. 项目实战

### 3.1 环境准备

MySQL 5.7+ with Azkaban数据库，建议用测试环境。

### 3.2 分步实现

#### 步骤1：数据库Schema全景

**目标**：查看所有表及其关系。

```sql
-- 查看所有表
SHOW TABLES;

-- 查看execution_flows表结构
DESC execution_flows;

-- 查看外键约束
SELECT 
    CONSTRAINT_NAME,
    TABLE_NAME,
    COLUMN_NAME,
    REFERENCED_TABLE_NAME,
    REFERENCED_COLUMN_NAME
FROM information_schema.KEY_COLUMN_USAGE
WHERE TABLE_SCHEMA = 'azkaban'
  AND REFERENCED_TABLE_NAME IS NOT NULL;
```

#### 步骤2：数据量统计与分析

**目标**：识别数据膨胀热点。

```sql
-- ===== 表空间统计 =====
SELECT 
    table_name,
    ROUND(((data_length + index_length) / 1024 / 1024), 2) AS size_mb,
    table_rows
FROM information_schema.TABLES
WHERE table_schema = 'azkaban'
ORDER BY (data_length + index_length) DESC;

-- 典型输出：
-- execution_logs     | 2,048.00 MB | 1,500,000 rows
-- execution_flows    |   156.00 MB |   200,000 rows
-- execution_jobs     |    89.00 MB |   400,000 rows
-- project_files      |    45.00 MB |       500 rows
-- projects           |     0.02 MB |        30 rows
```

**分析结论**：`execution_logs`占90%+的空间，是优化的首要目标。

#### 步骤3：索引优化

**目标**：为高频查询字段创建索引。

```sql
-- ===== 1. execution_flows查询优化 =====
-- 最常见的查询：按project_id + flow_id查找最近的执行记录
CREATE INDEX idx_flows_project_flow_status 
ON execution_flows(project_id, flow_id, status);

-- 按状态查找所有RUNNING的Flow（监控页面）
CREATE INDEX idx_flows_status_start
ON execution_flows(status, start_time);

-- 按时间范围清理旧数据
CREATE INDEX idx_flows_start_time
ON execution_flows(start_time);

-- ===== 2. execution_jobs查询优化 =====
CREATE INDEX idx_jobs_exec_status
ON execution_jobs(exec_id, status);

-- ===== 3. execution_logs查询优化 =====
CREATE INDEX idx_logs_exec_name
ON execution_logs(exec_id, name);

CREATE INDEX idx_logs_upload_time
ON execution_logs(upload_time);
```

**验证索引效果**：

```sql
-- 查看执行计划
EXPLAIN SELECT * FROM execution_flows 
WHERE project_id = 1 AND flow_id = 'daily_report' 
ORDER BY start_time DESC LIMIT 10;
```

#### 步骤4：安全的数据清理策略

**目标**：安全清理历史数据，不影响运行中的服务。

```bash
#!/bin/bash
# safe_cleanup.sh —— 安全清理Azkaban历史数据

MYSQL_HOST="prod-db"
MYSQL_USER="azkaban"
MYSQL_DB="azkaban"
RETENTION_MONTHS=6
BATCH_SIZE=1000

CUTOFF_DATE=$(date -d "${RETENTION_MONTHS} months ago" +%Y-%m-%d)

echo "=== Azkaban数据安全清理 ==="
echo "保留期限: ${RETENTION_MONTHS}个月"
echo "清理截止日期: ${CUTOFF_DATE}"
echo "批大小: ${BATCH_SIZE}行"
echo ""

# 1. 统计待清理数据量
echo "[1/4] 统计待清理数据量..."
TOTAL_FLOWS=$(mysql -h $MYSQL_HOST -u $MYSQL_USER $MYSQL_DB -N -e "
    SELECT COUNT(*) FROM execution_flows 
    WHERE start_time < UNIX_TIMESTAMP('${CUTOFF_DATE}') * 1000
")
echo "  待清理的Flow执行记录: ${TOTAL_FLOWS}"

TOTAL_LOGS=$(mysql -h $MYSQL_HOST -u $MYSQL_USER $MYSQL_DB -N -e "
    SELECT COUNT(*) FROM execution_logs 
    WHERE upload_time < UNIX_TIMESTAMP('${CUTOFF_DATE}') * 1000
")
echo "  待清理的Job日志: ${TOTAL_LOGS}"

# 2. 确认操作
read -p "确认清理? (输入yes继续): " confirm
if [ "$confirm" != "yes" ]; then
    echo "已取消"
    exit 0
fi

# 3. 分批清理——execution_logs（最大的表先清理）
echo "[2/4] 清理execution_logs..."
OFFSET=0
while [ $OFFSET -lt $TOTAL_LOGS ]; do
    mysql -h $MYSQL_HOST -u $MYSQL_USER $MYSQL_DB -e "
        DELETE FROM execution_logs 
        WHERE upload_time < UNIX_TIMESTAMP('${CUTOFF_DATE}') * 1000
        LIMIT ${BATCH_SIZE};
    "
    OFFSET=$((OFFSET + BATCH_SIZE))
    echo "  已清理 ${OFFSET}/${TOTAL_LOGS} 条日志记录..."
    sleep 1  # 防止锁表
done

# 4. 分批清理——execution_jobs
echo "[3/4] 清理execution_jobs..."
OFFSET=0
while [ $OFFSET -lt $TOTAL_FLOWS ]; do
    mysql -h $MYSQL_HOST -u $MYSQL_USER $MYSQL_DB -e "
        DELETE FROM execution_jobs 
        WHERE exec_id IN (
            SELECT exec_id FROM execution_flows 
            WHERE start_time < UNIX_TIMESTAMP('${CUTOFF_DATE}') * 1000
        )
        LIMIT ${BATCH_SIZE};
    "
    OFFSET=$((OFFSET + BATCH_SIZE))
    sleep 1
done

# 5. 清理execution_flows
echo "[4/4] 清理execution_flows..."
CLEANED=0
while true; do
    AFFECTED=$(mysql -h $MYSQL_HOST -u $MYSQL_USER $MYSQL_DB -N -e "
        DELETE FROM execution_flows 
        WHERE start_time < UNIX_TIMESTAMP('${CUTOFF_DATE}') * 1000
        LIMIT ${BATCH_SIZE};
        SELECT ROW_COUNT();
    " | tail -1)
    
    if [ "$AFFECTED" -eq 0 ]; then
        break
    fi
    CLEANED=$((CLEANED + AFFECTED))
    echo "  已清理 ${CLEANED}/${TOTAL_FLOWS} 条Flow执行记录..."
    sleep 1
done

echo ""
echo "=== 清理完成 ==="
echo "释放存储空间建议执行: OPTIMIZE TABLE execution_logs;"
```

#### 步骤5：数据备份与恢复

**目标**：安全备份Azkaban元数据并在需要时恢复。

```bash
#!/bin/bash
# backup_azkaban_db.sh

BACKUP_DIR="/backup/azkaban"
MYSQL_HOST="prod-db"
MYSQL_DB="azkaban"
DATE=$(date +%Y%m%d_%H%M%S)

mkdir -p "$BACKUP_DIR"

echo "=== Azkaban数据库备份 ==="

# 1. 全量备份（mysqldump）
echo "[1/3] 全量备份..."
mysqldump -h $MYSQL_HOST -u azkaban -p'xxx' \
    --single-transaction \
    --routines \
    --triggers \
    $MYSQL_DB \
    | gzip > "${BACKUP_DIR}/azkaban_full_${DATE}.sql.gz"

echo "  备份文件: ${BACKUP_DIR}/azkaban_full_${DATE}.sql.gz"
echo "  大小: $(du -h ${BACKUP_DIR}/azkaban_full_${DATE}.sql.gz | cut -f1)"

# 2. 仅备份元数据（不含日志）
echo "[2/3] 元数据备份（不含execution_logs）..."
mysqldump -h $MYSQL_HOST -u azkaban -p'xxx' \
    --single-transaction \
    --ignore-table=${MYSQL_DB}.execution_logs \
    $MYSQL_DB \
    | gzip > "${BACKUP_DIR}/azkaban_metadata_${DATE}.sql.gz"

# 3. 清理旧备份（保留7天）
echo "[3/3] 清理7天前的旧备份..."
find "$BACKUP_DIR" -name "azkaban_*.sql.gz" -mtime +7 -delete

echo "=== 备份完成 ==="
ls -lh "$BACKUP_DIR"
```

**恢复脚本**：

```bash
#!/bin/bash
# restore_azkaban_db.sh

BACKUP_FILE=$1

if [ ! -f "$BACKUP_FILE" ]; then
    echo "用法: $0 <backup_file.sql.gz>"
    exit 1
fi

echo "=== Azkaban数据库恢复 ==="
echo "备份文件: $BACKUP_FILE"
echo ""
echo "⚠️  警告: 此操作将覆盖当前Azkaban数据库！"
read -p "确认恢复? (输入yes继续): " confirm

if [ "$confirm" != "yes" ]; then
    echo "已取消"
    exit 0
fi

# 停止Azkaban服务（重要！）
echo "[1/3] 停止Azkaban服务..."
# ssh web-01 "cd /opt/azkaban-web && bin/shutdown-web.sh"
# ssh exec-01 "cd /opt/azkaban-exec && bin/shutdown-exec.sh"

# 恢复数据库
echo "[2/3] 恢复数据库..."
zcat "$BACKUP_FILE" | mysql -h prod-db -u azkaban -p'xxx' azkaban

# 启动Azkaban服务
echo "[3/3] 启动Azkaban服务..."
# ssh exec-01 "cd /opt/azkaban-exec && bin/start-exec.sh"
# ssh web-01 "cd /opt/azkaban-web && bin/start-web.sh"

echo "=== 恢复完成 ==="
```

### 3.3 测试验证

```sql
-- 验证数据完整性
-- 1. 检查孤儿记录（execution_jobs的exec_id在execution_flows中不存在）
SELECT COUNT(*) AS orphan_jobs
FROM execution_jobs j
LEFT JOIN execution_flows f ON j.exec_id = f.exec_id
WHERE f.exec_id IS NULL;

-- 2. 检查无项目的Flow
SELECT COUNT(*) AS orphan_flows
FROM project_flows pf
LEFT JOIN projects p ON pf.project_id = p.id
WHERE p.id IS NULL;

-- 3. 验证数据清理效果
SELECT 
    DATE(FROM_UNIXTIME(start_time/1000)) AS day,
    COUNT(*) AS execution_count
FROM execution_flows
WHERE start_time > UNIX_TIMESTAMP(DATE_SUB(NOW(), INTERVAL 7 DAY)) * 1000
GROUP BY day
ORDER BY day;
```

## 4. 项目总结

### 核心表维护指南

| 表名 | 增长速率 | 清理策略 | 索引建议 |
|------|---------|---------|---------|
| execution_logs | 最快（MB/天） | 按`azkaban.log.retention.days`自动清理 | `(exec_id, name)`, `(upload_time)` |
| execution_flows | 中等 | 需手动清理（建议保留6个月） | `(project_id, flow_id, status)`, `(start_time)` |
| execution_jobs | 中等 | 随execution_flows级联清理 | `(exec_id, status)` |
| project_files | 很慢 | 手动管理（每次上传增加记录） | — |
| QRTZ_* | 很慢 | Quartz自动管理 | — |

### 适用场景

- **适用**：大规模生产集群的Azkaban运维、数据归档和合规性管理、性能优化
- **不适用**：Solo Server开发环境（H2数据库不支持这些操作）

### 注意事项

- 直接DELETE大表可能导致锁表，务必使用分批+休眠的方式
- 备份时必须停止Azkaban服务，避免数据不一致
- `execution_logs`的`log_content`是longtext类型，单条记录可达几百MB
- Quartz的5张`QRTZ_*`表是Quartz框架维护的，不要手动操作

### 常见踩坑经验

1. **information_schema.TABLES统计不准**：`table_rows`是估算值，InnoDB中可能偏差50%+。精确统计用`SELECT COUNT(*) FROM xxx`。
2. **OPTIMIZE TABLE锁表**：在大表上执行`OPTIMIZE TABLE`会锁表数分钟到数小时。替代方案：`ALTER TABLE xxx ENGINE=InnoDB`（同样会锁表），或者用pt-online-schema-change工具在线操作。
3. **H2数据库文件暴涨**：Solo Server默认H2数据库的`execution_logs.h2.db`文件会持续增长。H2不支持自动收缩，需手动备份后重建。

### 思考题

1. 如果公司的合规要求是"所有Job执行记录必须保留至少3年"，但Azkaban每天产生5000条执行记录。3年就是540万条，`execution_flows`表会变得非常大。请设计一个分表/归档方案来满足这个需求。
2. 如何实现Azkaban数据库的"读写分离"——将查询流量分散到只读副本，写入仍到主库？
