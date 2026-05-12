# 第19章：PostgreSQL 数据模型与 Redis 缓存层

## 1 项目背景

某中型电商平台"速淘科技"运维Harbor已满2年，数据量增长到800个项目、60000+个制品、120万条审计日志。但最近频繁出现数据不一致问题，运维团队陷入了"不知道信谁"的困境。

**痛点一：数据不一致——Portal说删了但实际还能拉取。** 运维通过API删除了一个Artifact的标签，API返回200。Portal刷新后该标签消失。但半小时后有开发者报告"被删除的镜像还是能pull"。排查1小时后发现——API只删了标签（`artifact_tag`表中的行），但Artifact本体（`artifact`表）和Blob文件都没删除。Registry中的Manifest仍然有效，`docker pull`自然能成功。更深层的原因是：Harbor的"删除标签"操作不等于"删除镜像"，真正删除需要触发GC任务。

**痛点二：分页查询结果重复。** 运维写了一个Python脚本通过API分页遍历所有制品以做资产盘点。第一页（page=1, page_size=50）有50条，第二页也有50条——但其中有5条在第一页已经出现过了。脚本的去重逻辑拦截了重复项，但实际上丢了5条新数据。问题出在遍历期间正好有CI流水线持续推送新镜像——新数据不断插入导致`OFFSET`偏移量计算错位。

**痛点三：Redis缓存过期导致凌晨性能抖动。** 每天凌晨3点，Harbor API响应时间突然从50ms飙升到800ms，持续约5分钟。排查发现这个时间点正好是大量Redis缓存Key的TTL（30分钟）集中过期时刻——所有项目/仓库的热数据缓存同时失效，导致Core短时间内向PostgreSQL发起数百个并发查询（缓存雪崩）。

**痛点四：手动修改数据库导致连锁异常。** 一位运维为了"快速修复"——直接在PostgreSQL中执行了`UPDATE project SET public=true WHERE name='order-platform'`。随后Portal中该项目配置页面无法正常加载，因为Harbor的数据模型之间有外键约束和业务逻辑触发器——直接修改核心表绕过了ORM层的校验逻辑，导致关联表数据不一致。

本章将深入Harbor的数据库模型层，详解PostgreSQL Schema设计、核心表ER关系、Redis缓存策略及其在生产环境的管理实践。

---

## 2 项目设计——剧本式交锋对话

**场景：DBA老钱被CTO点名来给运维组做Harbor数据库知识培训。会议室里投影着pgAdmin的ER图。**

**小胖**（看着满屏幕的表结构）："Harbor不就是存个镜像信息吗？一张user表、一张image表、一张tag表，三张表搞定的事。你们设计了15+张表——是不是过度设计了？"

**大师**："小胖，你说三张表——那我来模拟一下：你的image表怎么存多架构Manifest（如amd64+arm64同一个tag对应不同的digest）？怎么存Helm Chart和CNAB bundle？怎么关联每个镜像的CVE扫描报告？怎么记录'这个镜像被谁在什么时间推送到哪个项目'的审计信息？"

小胖愣住了。

大师接着说："Harbor的数据库设计遵循**第三范式**——每张表有单一职责边界，通过外键建立起清晰的数据关系。Harbor核心数据模型是分层级的："

```
Project（项目: 多租户隔离的顶级容器）
  └─ Repository（仓库: 项目下的镜像/Chart分组）
       └─ Artifact（制品: OCI spec下的一个具体镜像/Chart/Bundle）
            ├─ Tag（标签: 一个Artifact可有多个tag）
            ├─ ArtifactBlob（关联: Artifact使用了哪些Blob）
            ├─ Vulnerability Report（扫描报告: Trivy扫描结果）
            ├─ Accessory（附加物: SBOM、Cosign签名等）
            └─ Reference（引用: Artifact之间的关联，如多架构索引）
Blob（镜像层: 独立于Artifact存在，多个Artifact可共享同一Blob）
```

**技术映射**：Harbor v2.x引入"Artifact"概念替代早期版本的"Image"概念，因为Harbor已支持OCI兼容的各种制品类型（Docker Image、Helm Chart、CNAB、OPA Bundle等）。核心表定义在`src/pkg/dao/`目录下的各model文件中。ORM层使用GORM（Go语言），版本迁移通过`make/migrations/postgresql/`下的SQL文件管理。

**小白**："我们之前遇到了API分页重复的问题——为什么`LIMIT + OFFSET`会导致重复？是bug吗？为什么不用更稳定的游标分页？"

**大师**："这是个好问题，涉及数据库分页的根本原理。Harbor的默认分页用OFFSET，但OFFSET在数据变化时有'错位'风险。用个例子说明："

```
时间 T1: SELECT * FROM artifact ORDER BY push_time LIMIT 10 OFFSET 0
         返回 A1, A2, A3, ... A10（最近推送的10个制品）

时间 T2: CI流水线推送了一个新制品 A0（push_time 最新）
         A0的push_time比A1更新 → 插入到表的最前面

时间 T3: SELECT * FROM artifact ORDER BY push_time LIMIT 10 OFFSET 10
         现在排第1的是 A0
         排第2-11的是 A1-A10
         OFFSET 10跳过了 A0-A9 → 返回 A10-A19
         其中A10在第一页的OFFSET 9已经出现过了——重复！
```

"解决方法：
1. **游标分页（Cursor-based）**：用上一页最后一条的`push_time`作为下一页的过滤条件——`WHERE push_time < last_push_time LIMIT 10`——不受新插入影响
2. **应用层去重**：接受分页遍历期间数据可能变化，用digest做去重（但会漏掉几行）
3. **快照式遍历**：在遍历开始前记下当前最大`push_time`，只查该时间点之前的数据"

**技术映射**：Harbor的分页参数从API的`page`和`page_size`传入（`src/server/v2.0/handler/base.go:54`），底层用GORM的`db.Offset(offset).Limit(limit).Find(&items)`执行。目前未内置游标分页，需应用层处理。

**小胖**："那Redis缓存到底存了啥？每次查API是先查Redis还是先查数据库？我最近发现重启Redis后Harbor慢了3秒——是缓存没预热吗？"

**大师**："你观察得很对。Harbor的缓存策略是经典的**Cache-Aside（旁路缓存）**模式。我画一下读写的两条路径："

```
读流程（如：查询项目列表）：
API请求 → Core Handler 
       → Cache.Get("project:all"): Redis GET
         ├─ Hit → 直接返回缓存数据（1ms, 不查PG）
         └─ Miss → Core SQL Query → PostgreSQL 
                        → 返回数据 + Cache.Set("project:all", data, TTL=30min)
                        → 下一次同样的请求直接Hit（50ms → 1ms）

写流程（如：创建新项目）：
API请求 → Core Handler 
       → GORM.Insert → PostgreSQL (INSERT INTO project ...)
       → Cache.Del("project:all")（使项目列表缓存失效）
       → Cache.Del("project:<name>")（使该项目的单条缓存失效）
```

"重启Redis后缓存全空——所有请求都Miss，都要查PostgreSQL，自然慢3秒。预热方案：在重启后立即用脚本或启动Hook自动请求最热门的API，主动填充缓存。"

**Redis Key示例**：
```bash
project:42:info             → {"project_id":42,"name":"order-platform","public":false,...}
repository_count:42         → 25 (项目42下的仓库数量)
artifact:tags:order-platform/order-service@sha256:abc123 → ["v1.0.0","latest","v1.0.1"]
user:1:info                 → {"user_id":1,"username":"admin","sysadmin_flag":true}
artifact:digest:sha256:abc  → (digest → Artifact ID 的快速映射)
```

**技术映射**：Harbor的缓存层在`src/pkg/cache/`包中实现，默认使用Redis作为后端。缓存Key的命名规范为`{namespace}:{resource}:{identifier}`。TTL在代码中通过`time.Duration`设置，不同的资源有不同的TTL（项目信息30分钟，仓库数量5分钟，标签列表10分钟）。

---

## 3 项目实战

### 环境要求

| 组件 | 版本 | 说明 |
|------|------|------|
| Harbor | v2.12.x | 分析目标版本 |
| PostgreSQL | 14.x | Harbor后端数据库 |
| Redis | 6.2+ | 缓存与队列服务 |
| psql client | 14+ | PostgreSQL命令行工具 |
| redis-cli | 6.2+ | Redis命令行工具 |
| jq | 1.6+ | JSON格式化 |

### 3.1 直接查询PostgreSQL核心表结构

**目标**：熟悉Harbor的核心数据表及其关系，建立直观的数据模型认知。

```bash
# 进入harbor-db容器
docker exec -it harbor-db psql -U postgres -d registry

# 查看全部表
\dt

# 预期输出（15+张表）：
#  Schema |          Name           | Type  |  Owner
# --------+-------------------------+-------+----------
#  public | accessory               | table | postgres
#  public | artifact                | table | postgres
#  public | artifact_blob           | table | postgres
#  public | artifact_reference      | table | postgres
#  public | artifact_tag            | table | postgres
#  public | audit_log               | table | postgres
#  public | blob                    | table | postgres
#  public | harbor_user             | table | postgres
#  public | project                 | table | postgres
#  public | project_member          | table | postgres
#  public | replication_policy      | table | postgres
#  public | repository              | table | postgres
#  public | robot                   | table | postgres
#  public | scan_report             | table | postgres
#  public | schedule                | table | postgres
#  ...

# 查看关键表结构
\d project
# 输出：project_id(PK) | owner_id | name | public | registry_id | ...

\d artifact
# 输出：id(PK) | digest | media_type | repository_id(FK) | push_time | ...

\d artifact_tag
# 输出：id(PK) | tag | artifact_id(FK) | ...

\d blob
# 输出：id(PK) | digest | size | content_type | ...
```

### 3.2 常用分析SQL——存储、用户、审计

**目标**：用SQL分析Harbor的运行数据，辅助运维决策。

```sql
-- 1. 各项目存储消耗排名（Top 10）
SELECT 
    p.name AS project,
    COUNT(DISTINCT r.repository_id) AS repos,
    COUNT(DISTINCT a.id) AS artifacts,
    pg_size_pretty(COALESCE(SUM(b.size), 0)) AS total_size
FROM project p
JOIN repository r ON r.project_id = p.project_id
JOIN artifact a ON a.repository_id = r.repository_id
JOIN artifact_blob ab ON ab.digest_af = a.digest
JOIN blob b ON b.digest = ab.digest_bl
WHERE p.deleted = false
GROUP BY p.name
ORDER BY SUM(b.size) DESC
LIMIT 10;

-- 预期输出：
--        project       | repos | artifacts | total_size
-- --------------------+-------+-----------+------------
--  order-platform     |    12 |      3927 | 48 GB
--  shared-base        |     5 |      2100 | 32 GB
--  payment-platform   |     8 |      1500 | 15 GB
--  ...

-- 2. 最近30天推送最多的用户
SELECT 
    username,
    COUNT(*) AS push_count,
    MIN(op_time) AS first_op,
    MAX(op_time) AS last_op
FROM audit_log
WHERE operation = 'PUSH'
  AND op_time > NOW() - INTERVAL '30 days'
GROUP BY username
ORDER BY push_count DESC
LIMIT 10;

-- 3. 标签重复的Artifact（一个digest对应多个tag，检查是否有意外重复）
SELECT 
    r.name AS repo,
    a.digest,
    STRING_AGG(t.tag, ', ') AS tags,
    COUNT(t.id) AS tag_count
FROM artifact a
JOIN repository r ON r.repository_id = a.repository_id
JOIN artifact_tag t ON t.artifact_id = a.id
GROUP BY r.name, a.digest
HAVING COUNT(t.id) > 3
LIMIT 20;

-- 4. 审计日志增长趋势（按天统计）
SELECT 
    DATE(op_time) AS date,
    operation,
    COUNT(*) AS count
FROM audit_log
WHERE op_time > NOW() - INTERVAL '30 days'
GROUP BY DATE(op_time), operation
ORDER BY date DESC
LIMIT 30;
```

### 3.3 Redis缓存深度探查

**目标**：了解Redis中实际缓存的数据形态、TTL分布和内存占用。

```bash
# Step 1: 进入Redis容器
docker exec -it redis redis-cli

# Step 2: 用SCAN遍历Key（生产环境别用KEYS *，会阻塞）
SCAN 0 MATCH project:* COUNT 10
# 返回：cursor + Key列表
# 1) "53248"
# 2) 1) "project:order-platform:info"
#    2) "project:payment-platform:info"
#    3) "project:1:repository_count"

# Step 3: 查看单个Key的内容和TTL
GET project:order-platform:info
# 返回JSON（可能被gzip压缩后转base64）

TTL project:order-platform:info
# 返回剩余秒数：(integer) 1620 → 27分钟后过期

# Step 4: 查看各类型Key的数量分布
SCAN 0 MATCH project:* COUNT 100
SCAN 0 MATCH artifact:* COUNT 100
SCAN 0 MATCH _sid:* COUNT 100
SCAN 0 MATCH job:* COUNT 100

# Step 5: 内存分析
INFO memory
# 关注 used_memory_human, used_memory_peak_human, mem_fragmentation_ratio

# Step 6: 查找大Key（超过1MB的Key）
redis-cli --bigkeys -h redis 2>/dev/null | head -20
# 注意：--bigkeys是SCAN-based，不会阻塞，但会消耗一定CPU

# Step 7: 退出
exit
```

### 3.4 缓存雪崩预防与处理

**目标**：理解并实施缓存雪崩的缓解策略。

**方案A：TTL随机化——从代码层面解决**

```go
// Harbor Core中缓存设置的典型代码（Go伪代码，演示原理）
// 源码参考：src/pkg/cache/cache.go

import "math/rand"

func setWithJitter(key string, value interface{}, baseTTL time.Duration) {
    // 基础TTL 30分钟
    // 增加 0-5分钟的随机值，避免所有Key同时过期
    jitter := time.Duration(rand.Intn(300)) * time.Second
    actualTTL := baseTTL + jitter
    redis.Set(key, value, actualTTL)
}
```

**方案B：定时预热脚本——从运维层面解决**

```bash
#!/bin/bash
# /opt/harbor/scripts/cache-warmup.sh
# 每天早上8点预热所有项目缓存

HARBOR_URL="https://harbor.company.com"
ADMIN_AUTH="admin:Str0ng@Admin2024"

echo "=== Cache Warmup Started at $(date) ==="

# 1. 预热项目列表
curl -s -u "$ADMIN_AUTH" \
  "$HARBOR_URL/api/v2.0/projects?page=1&page_size=100" > /dev/null
echo "✅ Projects list cached"

# 2. 获取所有项目并逐个预热
for project_name in $(curl -s -u "$ADMIN_AUTH" \
  "$HARBOR_URL/api/v2.0/projects?page=1&page_size=100" | \
  jq -r '.[].name'); do
  curl -s -u "$ADMIN_AUTH" \
    "$HARBOR_URL/api/v2.0/projects?name=$project_name" > /dev/null
done
echo "✅ All project details cached"

# 3. 预热项目下的仓库列表
for project_name in $(curl -s -u "$ADMIN_AUTH" \
  "$HARBOR_URL/api/v2.0/projects?page=1&page_size=100" | \
  jq -r '.[].name'); do
  curl -s -u "$ADMIN_AUTH" \
    "$HARBOR_URL/api/v2.0/projects/$project_name/repositories?page=1&page_size=50" > /dev/null
done
echo "✅ Repository lists cached"

echo "=== Cache Warmup Completed at $(date) ==="
```

```bash
# 部署为CronJob
chmod +x /opt/harbor/scripts/cache-warmup.sh
# 添加到crontab（每天早上8点）
# 0 8 * * * /opt/harbor/scripts/cache-warmup.sh >> /var/log/harbor-cache-warmup.log 2>&1
```

### 3.5 数据库维护最佳实践

**目标**：执行常规数据库维护任务，防止性能退化。

```sql
-- 在harbor-db容器中执行

-- 1. 查看表大小（识别增长最快的表）
SELECT 
    tablename,
    pg_size_pretty(pg_total_relation_size(tablename::regclass)) AS total_size,
    pg_size_pretty(pg_relation_size(tablename::regclass)) AS table_size,
    pg_size_pretty(pg_indexes_size(tablename::regclass)) AS index_size
FROM pg_tables
WHERE schemaname = 'public'
ORDER BY pg_total_relation_size(tablename::regclass) DESC
LIMIT 10;

-- 预期输出：
--   tablename    | total_size | table_size | index_size
-- --------------+------------+------------+------------
--  audit_log    | 850 MB     | 620 MB     | 230 MB
--  blob         | 340 MB     | 280 MB     | 60 MB
--  artifact     | 120 MB     | 85 MB      | 35 MB
--  ...

-- 2. 清理审计日志（保留90天，需要确认Harbor已配置自动清理）
-- Harbor默认通过JobService定时清理。如果未配置，可手动：
-- 注意：务必在数据库低峰期执行，并在事务中分批处理
DO $$ 
DECLARE
    deleted_count INTEGER;
BEGIN
    WITH deleted AS (
        DELETE FROM audit_log 
        WHERE op_time < NOW() - INTERVAL '90 days'
        RETURNING id
    )
    SELECT COUNT(*) INTO deleted_count FROM deleted;
    RAISE NOTICE 'Deleted % audit log records older than 90 days', deleted_count;
END $$;

-- 3. VACUUM + ANALYZE（回收已删除空间 + 更新统计信息）
VACUUM ANALYZE artifact;
VACUUM ANALYZE blob;
VACUUM ANALYZE audit_log;
-- 注意：VACUUM不锁表，生产环境可安全执行
-- VACUUM FULL会锁表，绝对不要在业务高峰期执行

-- 4. 重建索引（提升查询性能）
-- 仅在确认索引碎片化严重时执行（通过pg_stat_user_indexes查看）
REINDEX TABLE artifact_tag;
-- 注意：REINDEX会短暂锁表（毫秒到秒级），建议在低峰期
```

### 3.6 可能遇到的坑

**坑1：直接修改数据库后Harbor行为异常**

| 项目 | 内容 |
|------|------|
| **症状** | 运维直接用`UPDATE`或`DELETE`修改了PG中的某行数据，随后Portal中该数据无法加载、相关API返回500。 |
| **根因** | （1）Harbor的ORM层（GORM）有级联回调——直接SQL绕过了这些回调，导致关联表数据不一致。（2）某些表之间有外键约束，直接修改主表可能违反引用完整性。（3）Harbor有Redis缓存——数据改了但缓存未失效，读取到的仍是旧数据。 |
| **解决** | 始终通过API操作数据（`curl`调用REST API）。如果必须直接查库，只能做只读`SELECT`。已改坏时：通过API重新执行相同操作，让ORM层修复关联数据。 |

**坑2：pg_dump超时或文件太大**

| 项目 | 内容 |
|------|------|
| **症状** | `pg_dump -U postgres registry > backup.sql` 执行2小时后超时，生成一个20GB的SQL文件，但大部分是审计日志。 |
| **根因** | `audit_log`表可能有数百万行，导出时不仅量大，而且`pg_dump`默认用`transaction`模式（全局锁）——大数据量下会长期阻塞其他操作。 |
| **解决** | 分离导出：`pg_dump --exclude-table=audit_log ...` 导出核心数据；审计日志单独导出或直接用`COPY`命令导出CSV。备份时加`--no-owner --no-acl`避免导入时的权限问题。 |

**坑3：Redis内存持续增长直至OOM**

| 项目 | 内容 |
|------|------|
| **症状** | Redis内存从初始200MB持续增长到maxmemory上限，然后开始淘汰Key。任务队列被截断，Session丢失导致用户被踢出。 |
| **根因** | （1）某些缓存Key没有设置TTL（如旧版本的`artifact:tags:*`），永久驻留在内存中。（2）任务队列中的失败任务JSON不断追加但从未被清理（Core只有RPUSH，没有对应的LTRIM）。（3）RDB持久化时fork会产生内存副本（COW机制），峰值可达used_memory的2倍。 |
| **解决** | 设置`maxmemory-policy allkeys-lru`；在Core配置中开启`cache_cleanup_interval`；对任务队列做最大长度限制。定期用`MEMORY DOCTOR`分析内存碎片。 |

**坑4：缓存与数据库不一致导致Portal显示错误**

| 项目 | 内容 |
|------|------|
| **症状** | 通过API修改了项目名称后，Portal中项目列表显示的仍是旧名称，持续约15分钟后才更新。 |
| **根因** | 写操作更新了PostgreSQL，但Redis中对应的缓存Key没有被正确删除（Cache Invalidation遗漏）。直到TTL到期后缓存自动过期，Portal才读到新数据。 |
| **解决** | 确认缓存失效逻辑覆盖了所有相关Key。排查方法：执行Update操作后立即检查Redis中对应的Key是否被DEL（用`EXISTS key`检查）。修复后可通过API强制刷新或手动`DEL`相关缓存Key。 |

---

## 4 项目总结

### 4.1 PostgreSQL核心表清单与功能

| 表名 | 存储内容 | 关键字段 | 典型记录量级 | 增长速度 | 索引策略 |
|------|---------|---------|------------|---------|---------|
| `project` | 项目元数据 | `project_id(PK)`, `name`, `owner_id`, `public` | 10-500 | 低（日均<1） | name唯一索引 |
| `repository` | 仓库信息 | `repository_id(PK)`, `name`, `project_id(FK)` | 100-10000 | 中（日均1-10） | (project_id,name)联合索引 |
| `artifact` | 制品（镜像/Chart等） | `id(PK)`, `digest`, `repository_id(FK)`, `push_time` | 1000-200000 | 高（日均50-500） | (digest,repository_id) |
| `artifact_tag` | 标签 | `id(PK)`, `tag`, `artifact_id(FK)` | 1000-200000 | 高 | (artifact_id) + (tag) |
| `blob` | 镜像层/文件层 | `id(PK)`, `digest`, `size`, `content_type` | 5000-1000000 | 高 | digest唯一索引 |
| `artifact_blob` | Artifact-Blob多对多 | `digest_af`, `digest_bl` | 5000-500000 | 高 | (digest_af, digest_bl) |
| `audit_log` | 审计日志 | `id(PK)`, `username`, `operation`, `op_time` | 10000-50000000+ | 最高 | op_time索引（分区） |
| `project_member` | 项目成员 | `project_id`, `entity_id`, `role` | 100-10000 | 低 | (project_id,entity_id) |

### 4.2 Redis Key分类与缓存策略

| Key前缀 | 示例 | TTL | 数据源 | 更新策略 | 内存占比 |
|--------|------|-----|--------|---------|---------|
| `_sid:` | `_sid:abc123` | 30min可续 | Session创建时写入 | 无（被动淘汰） | <5% |
| `project:` | `project:42:info` | 30min±jitter | API读取PG后回写 | 项目更新时DEL | 15% |
| `artifact:` | `artifact:tags:order/service@sha` | 10min | 同上 | 标签变更时DEL | 30% |
| `repository:` | `repository:count:42` | 5min | 同上 | 仓库变更时DEL | 5% |
| `user:` | `user:1:info` | 30min | 同上 | 用户信息变更时DEL | <5% |
| `job:` | `job:scan:queue` | 无(List数据类型) | Core RPUSH | JobService消费后BRPOP | 20% |
| `lock:` | `lock:scheduled:retention` | 任务时长的2倍 | JobService SETNX | 任务完成后DEL | <1% |

### 4.3 适用场景与不适用场景

**适用场景：**
- **数据一致性排查**：当Portal显示与API返回不一致时，直接查PG确认"真相"——查出Portal的缓存是否过期
- **存储成本分析**：通过SQL分析各项目/仓库/制品的存储占用趋势，识别存储增长异常的团队
- **缓存性能优化**：通过Redis监控识别热点Key，调整TTL策略防止缓存雪崩，必要时增加Redis内存
- **合规审计**：从`audit_log`表导出所有操作记录，按用户、时间、操作类型生成合规报告
- **容量规划**：基于各表的增长速度预测未来3/6/12个月的存储需求，提前扩容

**不适场景：**
- **业务数据修改**：绝不直接操作PG数据来做业务变更——绕过了业务逻辑、缓存、审计，会引起连锁异常
- **高频实时查询的替代**：Redis缓存有TTL和Miss惩罚——如果你的业务要求100%实时数据（如金融交易），需要额外的更新通知机制

### 4.4 注意事项

1. **绝不直接修改PG数据**——始终通过Harbor API操作。即使是查询也建议用API而非直连数据库（API有权限校验和审计记录）
2. **Redis持久化务必开启**（RDB + AOF）——如果Redis重启且未开启持久化，所有Session和任务队列全部丢失，用户被迫重新登录
3. **审计日志表是最大增长源**——如果没有自动清理策略，该表可能从1万行增长到1亿行。务必确认JobService的audit_log_cleanup job已启用
4. **VACUUM不是可选项而是必需品**——PostgreSQL的MVCC机制需要在删除行后执行VACUUM回收空间。如果长期不VACUUM，表会持续膨胀（dead tuples占用空间但不被复用）
5. **`pg_dump`备份时必须排除审计日志或使用`--exclude-table`**——全量备份在审计日志过亿时会超时。同时建议用`pg_basebackup`做物理备份作为灾难恢复方案

### 4.5 常见故障速查表

| 故障现象 | 根因 | 快速解决 |
|---------|------|---------|
| Portal和API返回数据不一致 | Redis缓存未及时失效 | `redis-cli DEL <相关Key>` 或等待TTL过期 |
| 凌晨3点API响应飙升至800ms | 缓存雪崩（大量Key同时过期） | TTL加随机值 + 定时预热脚本 |
| database disk usage接近100% | 审计日志表过大或dead tuples未回收 | VACUUM ANALYZE + 清理旧审计日志 |
| Redis OOM后服务异常 | maxmemory达到上限，Key被强制淘汰 | 增加maxmemory + 清理无TTL的Key |
| pg_dump备份超时 | audit_log表过大 + SQL格式导出慢 | 排除audit_log表 + 使用custom格式压缩 |
| 分页查询结果重复 | 遍历期间有新数据插入导致OFFSET错位 | 使用游标分页或用`push_time`做锚点 |

### 4.6 深度思考题

1. **如果Harbor的PostgreSQL彻底损坏（数据文件不可恢复），但`/data/registry/`下的所有Blob文件完好。是否可能基于Blob文件反向重建数据库中的元数据（项目、仓库、Artifact、Tag关系）？需要哪些信息？哪些数据无法从Blob中恢复？**

2. **为Harbor设计一个"读写分离"方案——读请求（项目列表、标签查询等）走只读副本PostgreSQL，写请求（push、删除等）走主PostgreSQL。需要改造Core的哪些代码层？如何解决"写后马上读"的复制延迟问题？**

---

> 下一章预告：第20章将详解Harbor的镜像存储后端——本地文件系统 vs S3 vs Azure vs GCS的性能对比与选型决策。
