# 第38章：大规模 SonarQube 性能与容量治理

## 1. 项目背景

**业务场景**：某集团级研发组织规模达到 1,000+ 项目、3,000+ 开发者、日均 500+ 次扫描。SonarQube 平台在峰值时段出现严重的性能下降——CE 任务排队超过 2 小时、Web UI 加载超过 30 秒、ES 索引写入延迟超过 10 秒。运维团队需要系统性地进行容量规划和性能治理。

**痛点放大**：

- **容量模型缺失**：不知道当前配置能支撑多少项目和并发扫描
- **数据库膨胀**：issues 表已累计 2000 万行，查询越来越慢
- **ES 索引碎片化**：components 索引远超推荐大小
- **历史数据无限增长**：3 年前已删除项目的 Issue 数据仍占用磁盘

**更多现实场景**：

- **场景一：ES 索引碎片化导致搜索超时**：一个 800+ 项目规模的 SonarQube 实例，ES 的 `components` 索引已经膨胀到 50GB。开发者在 Web UI 搜索一个文件需要 15 秒以上——远超出 3 秒内的可用性标准。排查发现，ES segment 数量超过了 5000 个，每次搜索需要扫描大量小 segment。

- **场景二：数据库慢查询拖垮 CE**：PostgreSQL 的 `issues` 表已累积 2500 万行。CE Worker 在处理一个任务时，`INSERT INTO issues ... ON CONFLICT DO UPDATE` 查询耗时从 200ms 飙升到 5 秒——因为相关索引碎片化严重，而且 VACUUM 已经 3 个月没有运行了。

- **场景三：备份窗口不足**：数据库已膨胀到 400GB，全量 `pg_dump` 耗时 3 小时——超过了凌晨维护窗口的 2 小时限制。运维团队被迫将备份频率从每日降为每周，增加了数据丢失风险。

**容量治理核心问题**：
1. 如何预测 6 个月后的存储需求？
2. 数据库和 ES 索引的清理策略如何制定？
3. 什么情况下需要从 Community Edition 迁移到 Data Center Edition？
4. 磁盘 I/O 瓶颈如何诊断和解决？
5. 如何在不停机的情况下执行数据清理？

## 2. 项目设计

### 剧本式交锋对话

---

**小胖**（看着数据库的 issues 表——2000 万行）："大师！我们的 SonarQube 数据库 issues 表 2000 万行——很多是 3 年前已经删掉的项目的数据。能不删掉它们？"

**大师**："SonarQube 本身有 Housekeeping 机制——每天自动清理一定天数以外的历史数据。但默认配置可能不够激进。检查一下：

- `sonar.dbcleaner.cleanDirectory.keep.hours`（清理临时文件）
- `sonar.dbcleaner.hoursBeforeKeepingOnlyOneAnalysisByDay`（保留每日一个快照的天数）
- `sonar.dbcleaner.weeksBeforeKeepingOnlyOneAnalysisByMonth`（保留每月一个快照的周数）

对于 1000+ 项目的规模，建议将数据保留策略收紧——例如只保留最近 90 天的每日快照、最近 12 个月的每月快照。超过时间的数据自动清理。"

**小白**："如果数据库还是太大，能手动清理吗？"

**大师**："可以——但极其危险。SonarQube 的数据表之间有复杂的引用关系，直接 DELETE 可能导致外键约束失败或 UI 异常。推荐的安全方式：

1. 在 Web UI 中删除不再需要的项目（`Administration → Projects → Management → Delete`）
2. 等待 Housekeeping 自动清理关联数据
3. 如果磁盘仍然紧张，可以清理 CE 任务历史数据
4. 最后才考虑直接操作数据库——且必须在 SonarQube 停止状态下进行"

**小胖**："大师，ES 索引碎片化这个问题我还没搞懂。你说 components 索引超过了推荐大小——多大才算'过大'？碎片化到底是什么意思？"

**大师**："好问题，这是大规模部署中最常见的性能杀手。让我拆解一下：

**ES 索引碎片化**是指索引被分成了太多小 segment。每次 CE 处理完一个任务，ES 会创建一个新的 segment 来存储新数据。正常情况下，ES 后台会自动合并（merge）这些小 segment。但如果写入速度超过合并速度——比如每天 500 次扫描，每次扫描写入上百条——就会产生大量未合并的 segment。

判断标准：
- **Segment 数 > 1000** → 轻度碎片化，搜索延迟开始明显
- **Segment 数 > 5000** → 中度碎片化，搜索延迟 5-10 秒
- **Segment 数 > 10000** → 重度碎片化，搜索可能超时

检查方法：
```bash
# 查看 segment 统计
curl -s "http://localhost:9001/_cat/segments?v&h=index,segment,size" \
  | awk '{sum[$1]++; size[$1]+=$3} END {for(i in sum) print i, sum[i], size[i]}'
```

**修复方法**：
1. 手动触发 merge：`POST /components/_forcemerge?max_num_segments=1`（会消耗大量 IO）
2. 重启 SonarQube 触发 Reindex（更安全，但耗时长）
3. 调整 ES 的 merge 策略参数。"

**小白**："那数据库那边呢？PostgreSQL 在大数据量下最容易出现什么问题？"

**大师**："三个典型问题：

**1. 索引膨胀**：频繁的 UPDATE 和 DELETE 会导致索引碎片。PostgreSQL 的 `issues` 表尤其严重——每次分析都会更新 Issue 状态，产生大量死元组（dead tuples）。

```sql
-- 检查表膨胀情况
SELECT schemaname, relname, 
       n_dead_tup, n_live_tup,
       round(n_dead_tup * 100.0 / NULLIF(n_live_tup + n_dead_tup, 0), 2) AS dead_ratio
FROM pg_stat_user_tables 
WHERE n_dead_tup > 1000
ORDER BY dead_ratio DESC;
```

dead_ratio > 20% 就需要执行 `VACUUM`，> 50% 建议 `VACUUM FULL`（但会锁表）。

**2. 慢查询**：SonarQube 的分页查询、Issue 搜索查询在大数据量下会变慢。

```sql
-- 开启慢查询日志
ALTER SYSTEM SET log_min_duration_statement = 1000;  -- 记录 > 1s 的查询
SELECT pg_reload_conf();

-- 使用 pg_stat_statements 扩展分析
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
SELECT query, calls, mean_exec_time, total_exec_time 
FROM pg_stat_statements 
WHERE query NOT LIKE '%pg_stat%'
ORDER BY total_exec_time DESC LIMIT 10;
```

**3. 连接池耗尽**：CE Worker 和 Web 服务共享数据库连接池。如果 CE Worker 数量 × 每个 Worker 所需连接数 > `sonar.jdbc.maxActive`，就会出现连接等待，表现为 CE 处理卡顿和 Web UI 间歇性 5xx。

```
连接池需求估算：
- 每个 CE Worker: 5-8 个连接
- Web 服务: 10-20 个连接
- 额外缓冲: 20-30%
总计 ≈ (Worker数 × 8 + 20) × 1.3
```

**小胖**："大师，有没有什么自动化清理脚本可以定期执行？比如每周清理一次旧数据？"

**大师**："有，但需要非常小心。这里提供一个安全的半自动化清理方案：

```bash
#!/bin/bash
# sonarqube-cleanup.sh - SonarQube 数据库主动清理脚本
# ⚠️ 必须在维护窗口内执行，且先备份数据库

set -e

DB_HOST="localhost"
DB_PORT="5432"
DB_NAME="sonar"
DB_USER="sonar"
BACKUP_DIR="/backup/sonar"

echo "=== SonarQube 数据清理 $(date) ==="

# 1. 先备份
echo "[1/5] 创建清理前备份..."
pg_dump -h $DB_HOST -p $DB_PORT -U $DB_USER -Fc $DB_NAME \
    > "$BACKUP_DIR/pre_cleanup_$(date +%Y%m%d_%H%M).dump"

# 2. 检查当前数据量
echo "[2/5] 当前数据统计..."
psql -h $DB_HOST -p $DB_PORT -U $DB_USER -d $DB_NAME -c "
SELECT 'live_measures' AS table_name, COUNT(*) FROM live_measures
UNION ALL SELECT 'project_measures', COUNT(*) FROM project_measures
UNION ALL SELECT 'issues', COUNT(*) FROM issues
UNION ALL SELECT 'ce_activity', COUNT(*) FROM ce_activity
ORDER BY count DESC;"

# 3. 清理 CE 任务历史（保留最近 90 天）
echo "[3/5] 清理 CE 任务历史..."
CE_DELETED=$(psql -h $DB_HOST -p $DB_PORT -U $DB_USER -d $DB_NAME -t -c "
    DELETE FROM ce_activity 
    WHERE status IN ('SUCCESS', 'FAILED', 'CANCELED')
    AND submitted_at < NOW() - INTERVAL '90 days';
    SELECT 'CE tasks deleted: ' || COUNT(*) FROM ce_activity;")
echo "$CE_DELETED"

# 4. 执行 VACUUM 回收空间
echo "[4/5] 执行 VACUUM ANALYZE..."
psql -h $DB_HOST -p $DB_PORT -U $DB_USER -d $DB_NAME -c "
    VACUUM ANALYZE issues;
    VACUUM ANALYZE live_measures;
    VACUUM ANALYZE project_measures;
    VACUUM ANALYZE ce_activity;"

# 5. 前后对比
echo "[5/5] 清理后数据库大小..."
psql -h $DB_HOST -p $DB_PORT -U $DB_USER -d $DB_NAME -c "
    SELECT pg_size_pretty(pg_database_size('$DB_NAME')) AS database_size;"

echo "=== 清理完成 ==="
```"

---

## 3. 项目实战

### 3.1 分步实现

**步骤 1：容量评估模型**

```bash
#!/bin/bash
# capacity-assessment.sh

echo "=== SonarQube 容量评估 ==="

# 项目数
PROJECTS=$(curl -s -u admin:Sonar@2024Admin \
  "http://localhost:9000/api/projects/search?ps=1" \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['paging']['total'])")
echo "项目数: $PROJECTS"

# 日均扫描次数
DAILY_SCANS=$(curl -s -u admin:Sonar@2024Admin \
  "http://localhost:9000/api/ce/activity?ps=500" \
  | python3 -c "
import sys, json
from datetime import datetime, timedelta
tasks = json.load(sys.stdin)['tasks']
yesterday = datetime.now() - timedelta(days=1)
count = sum(1 for t in tasks if t.get('submittedAt','0') > yesterday.isoformat())
print(count)")
echo "预计日均扫描: $DAILY_SCANS"

# 数据库大小
DB_SIZE=$(docker compose exec -T postgres \
  psql -U sonar -c "SELECT pg_size_pretty(pg_database_size('sonar'))" -t | tr -d ' ')
echo "数据库大小: $DB_SIZE"
```

**步骤 2：配置数据清理策略**

在 `sonar.properties` 或 Docker 环境变量中配置：

```properties
# 保留最近 90 天的每日分析
sonar.dbcleaner.hoursBeforeKeepingOnlyOneAnalysisByDay=2160

# 保留最近 6 个月的每月分析
sonar.dbcleaner.weeksBeforeKeepingOnlyOneAnalysisByMonth=26

# 清理超过 365 天的审计日志
sonar.dbcleaner.cleanDirectory.keep.hours=8760
```

重启后验证：

```bash
# 检查 Housekeeping 是否激活
curl -s -u admin:Sonar@2024Admin \
  "http://localhost:9000/api/system/info" \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['System'].get('Housekeeping','N/A'))"
```

**步骤 3：数据库性能优化**

```sql
-- 检查慢查询
SELECT query, calls, mean_exec_time
FROM pg_stat_statements
ORDER BY mean_exec_time DESC LIMIT 10;

-- 检查索引使用情况
SELECT schemaname, tablename, indexname, idx_scan, idx_tup_read
FROM pg_stat_user_indexes
WHERE idx_scan = 0
ORDER BY pg_relation_size(indexrelid) DESC LIMIT 10;

-- 对 issues 表的关键查询添加索引（如果需要）
CREATE INDEX CONCURRENTLY IF NOT EXISTS issues_project_status
  ON issues (project_uuid, status);
```

**步骤 4：ES 索引治理**

```bash
# 查看索引大小
curl -s "http://localhost:9001/_cat/indices?v&h=index,pri.store.size,docs.count"

# 如果索引过大，在 SonarQube 中重建索引
# Administration → System → Reindex
```

**步骤 5：数据库备份策略**

```bash
#!/bin/bash
# 备份清理脚本（保留最近 7 天）
BACKUP_DIR="/backup/sonarqube"
find $BACKUP_DIR -type f -name "*.dump" -mtime +7 -delete

# 按日备份
pg_dump -U sonar -Fc sonar \
  > "$BACKUP_DIR/sonar_$(date +%Y%m%d).dump"
```

### 3.2 验证

```bash
# 验证清理效果
echo "清理前/后数据库大小对比"
echo "清理前数据库查询："
docker compose exec -T postgres \
  psql -U sonar -c "SELECT relname, n_live_tup FROM pg_stat_user_tables ORDER BY n_live_tup DESC LIMIT 5;"
```

---

## 4. 项目总结

### 4.1 容量规划速查

| 规模 | 项目数 | 推荐配置 | 数据库预估 |
|------|--------|---------|-----------|
| 小 | < 50 | 1 CE Worker, 2GB ES heap | 5-20 GB |
| 中 | 50-200 | 2 CE Workers, 4GB ES heap | 20-100 GB |
| 大 | 200-1000 | 3 CE Workers, 8GB ES heap | 100-500 GB |
| 超大 | > 1000 | Data Center 版 | > 500 GB |

### 4.2 数据清理策略速查

| 数据类型 | 默认保留策略 | 激进策略（大规模） | 保守策略（合规要求） |
|---------|-------------|------------------|-------------------|
| 分析快照 | 所有版本 | 90天日快照 + 6月月快照 | 保留全部 |
| CE 任务记录 | 永久 | 保留 90 天 | 保留 365 天 |
| 审计日志 | 永久 | 保留 180 天 | 保留 3 年 |
| 临时文件 | 5 天 | 1 天 | 7 天 |

### 4.3 数据库性能优化检查清单

| 检查项 | 命令/方法 | 目标值 |
|--------|----------|-------|
| 死元组比例 | `SELECT dead_ratio FROM pg_stat_user_tables` | < 20% |
| 缓存命中率 | `SELECT hit_ratio FROM pg_stat_database` | > 95% |
| 索引使用率 | `SELECT idx_scan FROM pg_stat_user_indexes` | > 0（无未使用的大索引） |
| 慢查询 | `pg_stat_statements` | mean_exec_time < 1s |
| 连接数 | `SELECT COUNT(*) FROM pg_stat_activity` | < max_connections × 0.8 |

### 4.4 ES 性能优化参数

| 参数 | 推荐值 | 说明 |
|------|--------|------|
| `SONAR_SEARCH_JAVAOPTS` | `-Xms4g -Xmx4g` | ES 堆内存（不超过物理内存的 50%） |
| `indices.memory.index_buffer_size` | 20% | 索引缓冲区 |
| `SONAR_SEARCH_PORT` | 9001 | ES HTTP 端口（仅内网可达） |
| Segment merge | 定时触发 `_forcemerge` | 减少 segment 数量 |

### 4.5 注意事项

1. **删除项目前先导出质量数据**：如果需要保留历史记录但没有 SonarQube 商业版的审计功能，通过 API 导出指标数据再删除。
2. **不要在业务高峰期执行 Reindex**：重建 ES 索引期间搜索功能不可用。
3. **数据库 VACUUM**：PostgreSQL 在大量 DELETE 后需要 `VACUUM FULL` 回收磁盘空间，但这会锁表——安排在维护窗口内执行。

### 4.6 思考题

1. 如果遇到 CE 队列积压到 500 个任务且持续增长，你优先排查哪些环节？
2. SonarQube 的 ES 索引可以完全从数据库重建——为什么有些运维团队仍然选择备份 ES 数据文件？

---

> **推广计划提示**：容量治理应从接入 100 个项目时开始规划——等到 1,000 个项目再补救就晚了。建议每季度做一次容量评估，预测未来 6 个月的存储和计算需求。
