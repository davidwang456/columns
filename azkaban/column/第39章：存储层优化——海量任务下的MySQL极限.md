# 第39章：存储层优化——海量任务下的MySQL极限

## 1. 项目背景

### 业务场景

某大型数据平台的Azkaban已经运行了3年，`execution_flows`表积累了超过2000万条记录，`execution_logs`表超过1.5亿条。MySQL服务器CPU长期90%+，慢查询堆积，严重影响了Azkaban的响应速度。

一次紧急故障中，DBA在业务高峰期执行了`DELETE FROM execution_logs WHERE upload_time < '2024-01-01'`——30分钟后，连接池耗尽、所有Azkaban API超时。这次事故最终导致凌晨2点的核心批处理延迟了3小时。

### 痛点放大

存储层不优化时：
- 单表2000万+行，全表扫描一次需要30秒+
- DELETE大表导致锁表，影响在线服务
- 磁盘空间每月增长50GB，没有清理策略
- 无法利用读写分离提升查询性能

## 2. 项目设计——剧本式交锋对话

**小胖**：大师，`execution_flows`2000万行了，查一次Project的执行历史要15秒！怎么搞？

**大师**：这是典型的"大表病"。你需要分层治理：
1. **索引优化**：添加合适的索引，让查询走索引
2. **分区表**：按时间分区，查询只扫描相关分区
3. **归档清理**：定期将冷数据迁移到归档表
4. **读写分离**：查询走只读从库，减轻主库压力

**小白**：分区表怎么做？

**大师**（给出SQL）：

```sql
-- 按月分区
ALTER TABLE execution_flows
PARTITION BY RANGE (start_time) (
    PARTITION p202401 VALUES LESS THAN (UNIX_TIMESTAMP('2024-02-01')*1000),
    PARTITION p202402 VALUES LESS THAN (UNIX_TIMESTAMP('2024-03-01')*1000),
    PARTITION p202403 VALUES LESS THAN (UNIX_TIMESTAMP('2024-04-01')*1000),
    PARTITION p_future VALUES LESS THAN MAXVALUE
);
```

这样查询2024年2月的数据时，MySQL只扫描`p202402`分区，而不是全表2000万行。

### 技术映射总结

- **分区表** = 档案室分柜（每个月的数据放一个柜子，查2月数据只看2月的柜子）
- **归档** = 把旧档案搬去仓库（日常用不到，但不能丢）
- **读写分离** = 收银台分流（付款去主柜台，查账去副柜台）

## 3. 项目实战

### 3.1 核心操作

#### 步骤1：索引优化

```sql
-- ===== 慢查询分析 =====
-- 查询1：获取某个Project的最近执行记录（慢在ORDER BY start_time）
-- 原始执行计划: type=ref, rows=500000, Extra=Using filesort
EXPLAIN SELECT * FROM execution_flows 
WHERE project_id=1 ORDER BY start_time DESC LIMIT 20;

-- 优化索引：
ALTER TABLE execution_flows 
ADD INDEX idx_project_status_time (project_id, status, start_time);

-- 优化后执行计划: type=ref, rows=20, Extra=Using index

-- 查询2：统计各状态的Flow数量（慢在GROUP BY）
ALTER TABLE execution_flows 
ADD INDEX idx_status (status);

-- 查询3：按提交时间范围查询（慢在范围扫描）
ALTER TABLE execution_flows 
ADD INDEX idx_submit_time (submit_time);
```

#### 步骤2：分区表实施

```sql
-- Step 1：创建分区表结构（假设原表无分区）
-- 注意：Azkaban使用毫秒级时间戳！

-- 备份当前表结构
CREATE TABLE execution_flows_partitioned LIKE execution_flows;

-- 按月创建分区（从2024年开始）
ALTER TABLE execution_flows_partitioned
PARTITION BY RANGE (start_time) (
    PARTITION p202401 VALUES LESS THAN (1706745600000),  -- 2024-02-01 00:00:00
    PARTITION p202402 VALUES LESS THAN (1709251200000),  -- 2024-03-01
    PARTITION p202403 VALUES LESS THAN (1711929600000),  -- 2024-04-01
    PARTITION p202404 VALUES LESS THAN (1714521600000),  -- 2024-05-01
    PARTITION p202405 VALUES LESS THAN (1717200000000),  -- 2024-06-01
    PARTITION p202406 VALUES LESS THAN (1719792000000),  -- 2024-07-01
    PARTITION p_future VALUES LESS THAN MAXVALUE
);

-- Step 2：数据迁移（在线，使用pt-online-schema-change）
pt-online-schema-change \
  --alter "PARTITION BY RANGE (start_time) (...)" \
  --execute \
  D=azkaban,t=execution_flows

-- Step 3：定期添加新分区（通过cron每月执行）
-- add_partition.sql
SELECT CONCAT(
    'ALTER TABLE execution_flows REORGANIZE PARTITION p_future INTO (',
    'PARTITION p', DATE_FORMAT(DATE_ADD(NOW(), INTERVAL 1 MONTH), '%Y%m'),
    ' VALUES LESS THAN (',
    UNIX_TIMESTAMP(DATE_ADD(DATE_ADD(LAST_DAY(NOW()), INTERVAL 1 DAY), INTERVAL 1 MONTH))*1000,
    '),',
    'PARTITION p_future VALUES LESS THAN MAXVALUE);'
) AS sql_cmd;
```

#### 步骤3：安全归档清理

```sql
-- 使用分区交换，秒级完成数据归档
-- 1. 创建归档表
CREATE TABLE execution_flows_archive LIKE execution_flows;

-- 2. 交换分区（秒级操作！）
ALTER TABLE execution_flows 
EXCHANGE PARTITION p202401 WITH TABLE execution_flows_archive;

-- 3. 归档表可以mysqldump备份后TRUNCATE
-- 相比DELETE，EXCHANGE PARTITION不会锁表！
```

#### 步骤4：高级分区自动管理

```python
#!/usr/bin/env python3
# partition_manager.py —— 分区自动管理

import pymysql
from datetime import datetime, timedelta

class PartitionManager:
    def __init__(self, host, user, password, database):
        self.conn = pymysql.connect(host=host, user=user, password=password, 
                                     database=database, autocommit=True)
    
    def add_future_partitions(self, months_ahead=3):
        """提前创建未来3个月的分区"""
        for i in range(1, months_ahead + 1):
            month_date = datetime.now().replace(day=1) + timedelta(days=32*i)
            partition_name = month_date.strftime("p%Y%m")
            next_month = (month_date.replace(day=1) + timedelta(days=62)).replace(day=1)
            boundary_ms = int(next_month.timestamp() * 1000)
            
            sql = f"""
            ALTER TABLE execution_flows 
            REORGANIZE PARTITION p_future INTO (
                PARTITION {partition_name} VALUES LESS THAN ({boundary_ms}),
                PARTITION p_future VALUES LESS THAN MAXVALUE
            )
            """
            try:
                self.conn.cursor().execute(sql)
                print(f"✓ 创建分区: {partition_name} (boundary: {boundary_ms})")
            except pymysql.err.OperationalError as e:
                if "Duplicate partition" in str(e):
                    print(f"  分区 {partition_name} 已存在，跳过")
                else:
                    print(f"✗ 创建分区失败: {e}")
    
    def archive_old_partitions(self, retention_months=12):
        """归档超期的分区"""
        cutoff_date = datetime.now() - timedelta(days=retention_months*30)
        
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT PARTITION_NAME 
            FROM information_schema.PARTITIONS 
            WHERE TABLE_SCHEMA = 'azkaban' 
              AND TABLE_NAME = 'execution_flows'
              AND PARTITION_NAME LIKE 'p20%'
        """)
        
        for row in cursor.fetchall():
            pname = row[0]
            # 解析分区名称中的日期 (p202401)
            p_date = datetime.strptime(pname[1:], "%Y%m")
            
            if p_date < cutoff_date:
                print(f"归档分区: {pname} ({p_date.strftime('%Y-%m')})")
                # 使用分区交换归档
                try:
                    cursor.execute(f"""
                        ALTER TABLE execution_flows 
                        EXCHANGE PARTITION {pname} 
                        WITH TABLE execution_flows_archive_{pname}
                    """)
                    print(f"  ✓ 分区 {pname} 已归档")
                except Exception as e:
                    print(f"  ✗ 归档失败: {e}")

if __name__ == '__main__':
    pm = PartitionManager("prod-db", "azkaban", "xxx", "azkaban")
    pm.add_future_partitions(months_ahead=3)
    pm.archive_old_partitions(retention_months=12)
```

#### 步骤5：读写分离

```properties
# azkaban.properties —— 读写分离配置（需自定义实现）
# 使用MySQL Router或ProxySQL做中间层

# 写库（Master）
mysql.host=mysql-master.company.com
mysql.port=3306

# 读库（通过ProxySQL路由）
mysql.read.host=proxysql.company.com
mysql.read.port=6033
```

### 3.2 测试验证

```sql
-- 验证分区效果
SELECT PARTITION_NAME, TABLE_ROWS, DATA_LENGTH
FROM information_schema.PARTITIONS
WHERE TABLE_NAME = 'execution_flows';

-- 验证查询是否走分区裁剪
EXPLAIN PARTITIONS 
SELECT * FROM execution_flows 
WHERE start_time BETWEEN 1706745600000 AND 1709251200000;
-- Extra应显示: Using where (只扫描p202402分区)
```

## 4. 项目总结

海量数据场景下MySQL优化的核心三板斧：
1. **索引优化**：针对高频查询建立复合索引
2. **分区表**：按时间分区，利用分区裁剪减少扫描范围
3. **EXCHANGE PARTITION**：秒级归档，避免DELETE锁表

### 思考题

1. 当`execution_logs`表超过10亿条记录时，分区+归档策略是否还适用？如果不用MySQL，可以选择什么替代存储方案？
2. 如果Azkaban需要同时支持"查询最近7天的执行记录"（热数据）和"查询3年前的执行记录"（冷数据），如何设计冷热分离架构？
