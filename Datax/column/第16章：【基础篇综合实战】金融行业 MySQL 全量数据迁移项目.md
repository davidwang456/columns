# 第16章：【基础篇综合实战】金融行业 MySQL 全量数据迁移项目

## 1. 项目背景

某银行科技部接到IT架构升级任务：将核心交易系统数据库从自建 MySQL 5.7 迁移到云原生 MySQL 8.0。迁移涉及 523 张表、约 2.3TB 数据，其中最大的"交易流水表"（trans_log）有 8.7 亿行、430GB。业务要求：
- 停服窗口不超过 6 小时（凌晨 0:00-6:00）
- 数据一致性 100%（不能丢一行、不能多一行、不能错一行）
- 迁移期间源库 CPU 不超过 30%（不能影响正在进行的日终批处理）
- 迁移完成后 30 分钟内完成数据校验并切换业务流量

项目组评估了三种方案：MySQL 原生主从复制（需要同版本且初始化慢）、Mydumper/myloader 逻辑导出导入（单线程太慢）、DataX（可调并发+限速+脏数据零容忍）。最终选择 DataX 作为核心同步引擎，本章完整还原该项目的设计、实现和验收全流程。

## 2. 项目设计——剧本式交锋对话

**（项目启动会，银行科技部会议室，PM、DBA、开发、测试围坐）**

**PM**：（打开 PPT）大家看下时间线。凌晨 0:00 开始停服，6:00 前必须恢复业务。中间只有 6 小时，要同步 523 张表、2.3TB 数据。

**小胖**：（瞪大眼睛）2.3TB！6 小时！平均每秒要写 110MB，这可能吗？

**大师**：（在白板上快速画出架构图）不能一张张串行跑。我们的策略是：
1. 把 523 张表按数据量分成大表（>10GB）、中表（1GB~10GB）、小表（<1GB）三组
2. 大表单独一个 DataX Job（配 20 channel），中表 5 个一组批量跑，小表 50 个一组批量跑
3. 三组**并行执行**，三台服务器各跑一组

**小白**：（在本子上算）大表有 3 张（trans_log、account、ledger），每张配 20 channel、50MB/s 限速。430GB ÷ 50MB/s = 8600s ≈ 2.4h。但 3 张表在三台机器上并行，取最慢的那张——也是 2.4h，在 6h 窗口内。

**DBA**：（担忧）但限速 50MB/s x 20 channel x 3 表 = 总共 3GB/s 的 IO 压力，源库的 SSD 能扛住吗？

**大师**：我们算一笔账。源库是 NVMe SSD，顺序读 3GB/s。但 DataX 分片后每个 Task 是顺序读一小段，整体读模式接近随机读——实际 IO 吞吐约 1.5GB/s。再加上 30% 的其他业务负载，总共 1.95GB/s，在 SSD 极限的 70% 左右，安全。

**技术映射**：DataX 并行读 = 10 个人同时翻一本 1000 页的书。每个人只读 100 页（顺序读），但 10 个人翻的是不同段落，整体对书的磨损（随机读）比一个人从头读到尾更大。

**测试**：那数据校验怎么做？2.3TB 不可能逐行比对。

**大师**：（翻到 PPT 下一页）校验分三层：
1. **行数校验**（快速）：每张表源端和目标端 `SELECT COUNT(*)`，1 分钟内跑完 523 张表
2. **MD5 抽样**（中等）：对前 100 行、中间 100 行、最后 100 行做 `MD5(GROUP_CONCAT(col1, col2, ...))`，10 分钟跑完
3. **全量校验**（彻底）：用 DataX 再跑一次反方向同步（目标端 → 源端校验表），对比差异。这层留到第二天非窗口期做

## 3. 项目实战

### 3.1 步骤一：环境准备与基线测试

**目标**：搭建测试环境，用一张 1 亿行的模拟表做基线性能测试。

```sql
-- 源端 MySQL 5.7
CREATE TABLE trans_log_test (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id BIGINT NOT NULL,
    amount DECIMAL(16,2) NOT NULL,
    trans_type TINYINT NOT NULL,
    status TINYINT DEFAULT 0,
    remark VARCHAR(500),
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_user_id (user_id),
    INDEX idx_create_time (create_time)
) ENGINE=InnoDB;

-- 用存储过程插入 1 亿行测试数据
DELIMITER $$
CREATE PROCEDURE gen_trans_log(IN total INT)
BEGIN
    DECLARE i INT DEFAULT 1;
    DECLARE batch INT DEFAULT 10000;
    WHILE i <= total DO
        INSERT INTO trans_log_test (user_id, amount, trans_type, status, remark, create_time)
        VALUES 
            (FLOOR(RAND()*10000000), ROUND(RAND()*99999.99,2), FLOOR(RAND()*10), FLOOR(RAND()*5),
             REPEAT('X', FLOOR(RAND()*100)), 
             DATE_ADD('2020-01-01', INTERVAL FLOOR(RAND()*2190) DAY));
        SET i = i + 1;
        IF i % batch = 0 THEN COMMIT; END IF;
    END WHILE;
END$$
DELIMITER ;
CALL gen_trans_log(100000000);
```

**基线 Job 配置**：

```json
{
    "job": {
        "content": [{
            "reader": {
                "name": "mysqlreader",
                "parameter": {
                    "username": "etl_user",
                    "password": "${SRC_PWD}",
                    "column": ["*"],
                    "splitPk": "id",
                    "connection": [{
                        "table": ["trans_log_test"],
                        "jdbcUrl": ["jdbc:mysql://10.0.1.100:3306/core_db?useSSL=false&serverTimezone=Asia/Shanghai"]
                    }]
                }
            },
            "writer": {
                "name": "mysqlwriter",
                "parameter": {
                    "username": "etl_user",
                    "password": "${TGT_PWD}",
                    "writeMode": "insert",
                    "column": ["*"],
                    "preSql": ["DROP TABLE IF EXISTS trans_log_test"],
                    "batchSize": 2048,
                    "connection": [{
                        "table": ["trans_log_test"],
                        "jdbcUrl": ["jdbc:mysql://10.0.2.100:3306/core_db?useSSL=false&rewriteBatchedStatements=true"]
                    }]
                }
            }
        }],
        "setting": {
            "speed": {
                "channel": 20,
                "byte": 52428800
            },
            "errorLimit": {
                "record": 0,
                "percentage": 0
            }
        }
    }
}
```

**重要参数**：`rewriteBatchedStatements=true` 是 MySQL JDBC 驱动的隐藏优化——将多条 INSERT 合并为一条 `INSERT INTO t VALUES (...),(...),(...)` 而不是多条独立的 `INSERT INTO t VALUES (...)`。这可以减少 80% 的网络往返，写入性能提升 3-5 倍。

**基线结果**：

| metric | 值 |
|--------|-----|
| 总记录数 | 100,000,000 |
| Channel | 20 |
| 总耗时 | 38 min |
| 平均流量 | 48 MB/s |
| 记录写入速度 | 43,859 rec/s |
| 脏数据 | 0 |

### 3.2 步骤二：批量配置生成

**目标**：用 Shell 脚本从 MySQL information_schema 自动生成 523 张表的 JSON 配置。

```bash
#!/bin/bash
# generate_jobs.sh — 根据源库表清单批量生成 DataX Job JSON

SOURCE_HOST="10.0.1.100"
SOURCE_DB="core_db"
TARGET_HOST="10.0.2.100"
TARGET_DB="core_db"
CHANNEL=5
BYTE_LIMIT=20971520  # 20MB/s

# 获取所有表名
TABLES=$(mysql -h $SOURCE_HOST -u root -p${SRC_PWD} -N -e "SHOW TABLES FROM $SOURCE_DB")

for TABLE in $TABLES; do
    # 获取列名（排除自增ID和update_time，这些留给目标端自动生成）
    COLUMNS=$(mysql -h $SOURCE_HOST -u root -p${SRC_PWD} -N -e "
        SELECT GROUP_CONCAT(COLUMN_NAME SEPARATOR '\",\"') 
        FROM information_schema.COLUMNS 
        WHERE TABLE_SCHEMA='$SOURCE_DB' AND TABLE_NAME='$TABLE'
    ")
    
    # 获取主键列名（用于 splitPk）
    PK=$(mysql -h $SOURCE_HOST -u root -p${SRC_PWD} -N -e "
        SELECT COLUMN_NAME FROM information_schema.COLUMNS 
        WHERE TABLE_SCHEMA='$SOURCE_DB' AND TABLE_NAME='$TABLE' AND COLUMN_KEY='PRI' LIMIT 1
    ")
    
    # 获取表数据量（用于确定 channel 数）
    ROW_COUNT=$(mysql -h $SOURCE_HOST -u root -p${SRC_PWD} -N -e "
        SELECT TABLE_ROWS FROM information_schema.TABLES 
        WHERE TABLE_SCHEMA='$SOURCE_DB' AND TABLE_NAME='$TABLE'
    ")
    
    # 根据数据量确定 channel 数
    if [ "$ROW_COUNT" -gt 10000000 ]; then
        CH=20
    elif [ "$ROW_COUNT" -gt 1000000 ]; then
        CH=10
    else
        CH=5
    fi
    
    # 生成 JSON 文件
    cat > jobs/${TABLE}.json <<EOF
{
    "job": {
        "content": [{
            "reader": {
                "name": "mysqlreader",
                "parameter": {
                    "username": "etl_user",
                    "password": "\${SRC_PWD}",
                    "column": ["$COLUMNS"],
                    "splitPk": "$PK",
                    "connection": [{
                        "table": ["$TABLE"],
                        "jdbcUrl": ["jdbc:mysql://$SOURCE_HOST:3306/$SOURCE_DB?useSSL=false&serverTimezone=Asia/Shanghai"]
                    }]
                }
            },
            "writer": {
                "name": "mysqlwriter",
                "parameter": {
                    "username": "etl_user",
                    "password": "\${TGT_PWD}",
                    "writeMode": "insert",
                    "column": ["$COLUMNS"],
                    "preSql": ["DROP TABLE IF EXISTS $TABLE"],
                    "batchSize": 2048,
                    "connection": [{
                        "table": ["$TABLE"],
                        "jdbcUrl": ["jdbc:mysql://$TARGET_HOST:3306/$TARGET_DB?useSSL=false&rewriteBatchedStatements=true"]
                    }]
                }
            }
        }],
        "setting": {
            "speed": {"channel": $CH, "byte": $BYTE_LIMIT},
            "errorLimit": {"record": 0, "percentage": 0}
        }
    }
}
EOF
    
    echo "Generated: $TABLE (${ROW_COUNT} rows, channel=$CH)"
done
```

### 3.3 步骤三：并行执行调度

**目标**：在 3 台服务器上并行执行 523 个 Job，最大化利用 6 小时窗口。

**调度脚本**：

```bash
#!/bin/bash
# run_parallel.sh — 批量并行执行 DataX Job

NODES=("10.0.3.1" "10.0.3.2" "10.0.3.3")  # 3 台 Worker 节点
JOBS_DIR="/data/datax/jobs"
LOG_DIR="/data/datax/logs"
MAX_PARALLEL=15  # 每台节点最多同时跑 15 个 Job（根据 CPU/内存/连接数确定）

# 将 Job 按数据量分配到 3 台节点（大表单独、中表分组、小表批量）
while IFS=, read -r table rows; do
    if [ "$rows" -gt 10000000 ]; then
        NODE_INDEX=0  # 大表 → 节点 0
    elif [ "$rows" -gt 1000000 ]; then
        NODE_INDEX=$(( (RANDOM % 2) + 1 ))  # 中表 → 节点 1 或 2
    else
        NODE_INDEX=$(( RANDOM % 3 ))  # 小表 → 随机分配
    fi
    
    echo "$JOBS_DIR/${table}.json" >> "queue_${NODE_INDEX}.txt"
done < table_inventory.csv

# 在每台节点上并发执行（xargs 控制并发数）
for i in 0 1 2; do
    cat "queue_${i}.txt" | xargs -P $MAX_PARALLEL -I {} sh -c "
        ssh ${NODES[$i]} 'cd /data/datax && python bin/datax.py {} > ${LOG_DIR}/\$(basename {} .json).log 2>&1'
    " &
done

# 等待所有 Job 完成
wait
echo "All jobs completed at $(date)"
```

### 3.4 步骤四：数据校验

**目标**：三层校验确保数据一致性。

**第一层：快速行数校验**

```sql
-- 在源端和目标端分别执行，对比结果
SELECT TABLE_NAME, TABLE_ROWS 
FROM information_schema.TABLES 
WHERE TABLE_SCHEMA = 'core_db' 
ORDER BY TABLE_NAME;
```

```bash
# 自动化对比脚本
diff <(mysql -h source -N -e "SELECT TABLE_NAME, TABLE_ROWS FROM information_schema.TABLES WHERE TABLE_SCHEMA='core_db' ORDER BY 1") \
     <(mysql -h target -N -e "SELECT TABLE_NAME, TABLE_ROWS FROM information_schema.TABLES WHERE TABLE_SCHEMA='core_db' ORDER BY 1")
```

**第二层：MD5 抽样校验**

```sql
-- 对每张表，取首/中/尾各 100 行做 MD5
SELECT MD5(GROUP_CONCAT(
    CONCAT_WS('|', col1, col2, col3, col4, col5) 
    ORDER BY id
    SEPARATOR '\n'
)) AS checksum
FROM (
    (SELECT * FROM core_db.trans_log ORDER BY id LIMIT 100)
    UNION ALL
    (SELECT * FROM core_db.trans_log ORDER BY id LIMIT 50 OFFSET 43500000)
    UNION ALL
    (SELECT * FROM core_db.trans_log ORDER BY id DESC LIMIT 100)
) t;
```

**第三层：反向全量校验（非窗口期执行）**

用 DataX 将目标端数据同步回源端临时校验库 `audit_db`，逐表对比差异。

### 3.5 步骤五：失败处理与回滚预案

**目标**：确保任何环节失败都能平滑回滚。

**回退方案**：
1. 如果某张表同步失败 → 仅重试该表（不要重跑全部 523 张表）
2. 如果 6 小时内未完成 → 立即回滚（K8s 上切换 Service 指回源库）
3. 如果数据校验发现不一致 → 执行反向同步（目标端→源端）修复

**失败处理 Shell**：

```bash
#!/bin/bash
# retry_failed.sh — 重试失败的 Job

FAILED_JOBS=$(grep -l "exit code: 1" /data/datax/logs/*.log)

for JOB_LOG in $FAILED_JOBS; do
    JOB_NAME=$(basename $JOB_LOG .log)
    echo "Retrying: $JOB_NAME"
    
    # 重试前先清理目标库残留数据
    mysql -h target -e "DROP TABLE IF EXISTS core_db.$JOB_NAME;"
    
    # 重新执行
    python bin/datax.py jobs/${JOB_NAME}.json > logs/${JOB_NAME}_retry.log 2>&1
    
    if grep -q "exit code: 0" logs/${JOB_NAME}_retry.log; then
        echo "$JOB_NAME: RETRY SUCCESS"
    else
        echo "$JOB_NAME: RETRY FAILED — needs manual intervention"
    fi
done
```

### 3.6 可能遇到的坑及解决方法

**坑1：大表 preSql 中的 DROP TABLE 触发元数据锁**

23 张并发写入的同时、某个 preSql 在执行 DROP TABLE——锁等待可能导致整个 Job 卡死。

解决：将 `preSql` 的 DDL 操作统一移到所有 Job 启动之前的预处理阶段执行，避免混在写入阶段。

**坑2：binlog 爆炸**

2.3TB 的写入量全部记录到 binlog，可能撑爆磁盘。

解决：目标端提前执行 `SET SESSION sql_log_bin = 0;` 关闭 binlog 记录。迁移完成后通过全量备份补齐从库。

**坑3：超大表 8.7 亿行的 split 查询超时**

`SELECT MIN(id), MAX(id) FROM trans_log` 在 8.7 亿行表上执行耗时超过 30 秒。

解决：利用索引统计信息快速获取近似 MIN/MAX：
```sql
SELECT MIN(id), MAX(id) FROM trans_log USE INDEX (PRIMARY);
-- 或从 information_schema 获取近似值（不够精确但足够用于分片）
```

## 4. 项目总结

### 4.1 实际执行结果

| 指标 | 计划 | 实际 | 达成 |
|------|------|------|------|
| 同步表数 | 523 | 523 | 100% |
| 同步数据量 | 2.3 TB | 2.31 TB | - |
| 总耗时 | ≤ 6h | 4h 22min | ✓ |
| 源库 CPU | ≤ 30% | avg 22%, peak 28% | ✓ |
| 数据一致性 | 100% | 0 差异 | ✓ |
| 脏数据 | 0 | 3 条（remark 超长，已手工修复） | 可接受 |

### 4.2 优点

1. **并行度高**：3 节点 × 15 并发 Job，充分利用多机资源
2. **限速精准**：byte=50MB/s 把源库 CPU 控制在 30% 以下
3. **自动重试**：失败 Job 自动清理+重试，人工介入率 < 2%
4. **三层校验**：快速行数 + MD5 抽样 + 反向全量，覆盖不同精度要求
5. **可回滚**：任何阶段可回滚到源库，风险可控

### 4.3 缺点

1. **脚本管理分散**：Shell 脚本、JSON 模板、SQL 文件散落在多处，维护不便
2. **无统一监控**：3 台节点的 Job 进度需要在各节点分别查看
3. **大表迁移窗口紧**：如果 trans_log 再大 50%，6 小时窗口可能不够
4. **binlog 关闭后从库需重建**：增加了后续的从库维护成本

### 4.4 经验总结

1. **大表独立、小表组合**：大表单独一个 Job 配高 channel，小表批量打包减少调度开销
2. **rewriteBatchedStatements=true**：MySQL 写入性能提升 3-5 倍的隐藏参数
3. **先同步表结构、再同步数据**：避免 DataX 在写入阶段执行 DDL 引发元数据锁
4. **校验分层**：不要一开始就做全量校验——先快速行数校验定位问题表，再抽样校验，最后全量
5. **回滚方案要从 Day 1 准备**：6 小时窗口内如果完不成，必须有干净的流量切换方案

### 4.5 思考题

1. 如果源库有主从架构（一主三从），如何利用从库分担 DataX 的读取压力？DataX 的 Reader 能配置读写分离吗？
2. 8.7 亿行的 trans_log 表用了 splitPk=id 做分片。如果 source 端存在"热点行"（某几个 ID 被频繁 UPDATE），DataX 读取这些行时会因为 InnoDB 行锁而阻塞吗？

（答案见附录）
