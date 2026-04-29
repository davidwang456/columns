# 第24章 数据湖集成：Flink CDC + Apache Paimon

## 1 项目背景

### 业务场景：需要"流批一体"的CDC数据湖

第23章的Iceberg解决了"CDC数据入湖"的问题，但业务方提出了新需求：**要从数据湖中直接消费Changelog流（INSERT/UPDATE/DELETE的完整日志）**，而不仅仅是"当前快照"。

Apache Paimon（原Flink Table Store）在CDC场景下提供了Iceberg不具备的能力：
1. **Changelog Producer**：直接输出完整的INSERT/UPDATE_BEFORE/UPDATE_AFTER/DELETE日志
2. **Lookup Join**：Paimon表可以作为Flink SQL的维表进行实时Lookup
3. **Streaming Read**：读取Paimon表的增量变更流

Iceberg更适合OLAP查询场景，Paimon更适合"流式处理+CDC"场景。

### Paimon CDC架构

```
MySQL Binlog
    │
    ▼
Flink CDC Source (Changelog流)
    │
    ▼
Paimon Sink (LSM-Tree结构写入)
    ├── INSERT → 追加到Sorted Run
    ├── UPDATE_BEFORE → 标记删除旧数据
    ├── UPDATE_AFTER → 追加新数据
    └── DELETE → 标记删除
    │
    ▼
Paimon Table (文件系统/HDFS/S3)
    ├── Snapshot (记录某时刻的完整数据)
    ├── Manifest (文件列表)
    └── Data Files (ORC格式，LSM-Tree组织)

下游消费:
  ├── Batch Read: 读取最新Snapshot（类似Iceberg）
  ├── Streaming Read: 读取增量Changelog（Paimon独有）
  └── Lookup Join: 作为维表实时关联（Paimon独有）
```

---

## 2 项目设计 · 三人交锋对话

**小胖**（困惑）：Paimon和Iceberg都是"数据湖表格式"，那CDC场景下选哪个？

**大师**：核心差异在于**流处理亲和性**。我们做个对比：

| 对比维度 | Iceberg | Paimon |
|---------|---------|--------|
| 数据组织 | Copy-On-Write / Merge-On-Read | LSM-Tree（类似RocksDB） |
| CDC Changelog | 需要额外解析 | 原生Changelog Producer |
| 流式读增量 | 不支持 | ✅ 原生支持 |
| Lookup Join | ❌ | ✅ |
| 写入吞吐 | 中 | 高（LSM-Tree批量写入） |
| 查询延迟 | 低（Parquet列存） | 中（需Merge多Level） |

**CDC场景推荐**：
- 如果数据主要被OLAP引擎（Trino/StarRocks）查询 → Iceberg
- 如果数据需要被Flink SQL流式消费或作为维表 → Paimon

**小白**：Paimon的LSM-Tree结构和RocksDB一样吗？为什么写入吞吐高？

**大师**：Paimon的LSM-Tree借鉴了BigTable/LevelDB的设计思想：

```
写入过程：
  数据到达 → 写入MemTable（内存缓冲区）
    ↓
  MemTable满 → 刷写到Level 0（小文件，无序）
    ↓
  定时Compaction → Level 0 → Level 1 → ... → Level N
                    (合并、排序、去重)

读取过程：
  查询请求 → 依次查找所有Level
    ↓
  合并每Level的结果（通过主键去重）
    ↓
  返回最终结果
```

LSM-Tree的优势在于**写入不涉及随机IO**——所有写入都是顺序追加到MemTable。Iceberg的COW模式在UPDATE/DELETE时需要读取-修改-写回整个文件，开销大得多。

**技术映射**：Iceberg像"图书馆的书架"——书（数据）按固定位置放好，找书很快（查询快），但往书里加一个注释（UPDATE）需要整本书重印（写放大）。Paimon像"快递站的包裹架"——新包裹到了直接放最上层（写入快），但找一个包裹可能需要翻好几层（查询需要Merge多Level）。

---

## 3 项目实战

### 环境准备

**Maven依赖：**
```xml
<dependency>
    <groupId>org.apache.flink</groupId>
    <artifactId>flink-cdc-pipeline-connector-paimon</artifactId>
    <version>3.0.0</version>
</dependency>
<dependency>
    <groupId>org.apache.paimon</groupId>
    <artifactId>paimon-flink-1.20</artifactId>
    <version>0.7.0</version>
</dependency>
```

### 分步实现

#### 步骤1：配置Paimon CDC Pipeline

```yaml
source:
  type: mysql
  hostname: localhost
  port: 3306
  username: cdc_user
  password: cdc_pass
  tables: shop.orders_full

sink:
  type: paimon
  # Paimon Catalog配置
  catalog-type: filesystem               # filesystem | hive
  warehouse: /tmp/paimon_warehouse
  
  # 表配置
  auto-create-table: true
  table-prefix: paimon_

  # Paimon特有配置
  bucket: 4                              # Bucket数量（影响并行度和并发）
  changelog-producer: input              # input | none | lookup | full-compaction
  # input: 直接从输入流中解析Changelog（适合CDC场景）
  # lookup: 通过Lookup产生Changelog（适合批处理）
  # full-compaction: 通过全量Compaction产生Changelog（最准确但最慢）

  # Compaction配置
  compaction.max-size-amplification-percent: 200
  compaction.early-max-size-mb: 100
  compaction.max-num-sorted-run: 50

route:
  - source-table: shop.orders_full
    sink-table: paimon_orders

pipeline:
  name: CDC to Paimon Pipeline
  parallelism: 2
  schema.change.behavior: EVOLVE
```

#### 步骤2：启动Pipeline并验证

```bash
# 1. 提交Pipeline
flink-cdc.sh pipeline-paimon.yaml --use-mini-cluster

# 2. 执行MySQL变更
docker exec mysql-cdc mysql -uroot -proot123 -e "
USE shop;
INSERT INTO orders_full VALUES (8, 'ORD_PAIMON', 1002, 'Paimon Test', 199.00, 'PAID', '13800008888', '入Paimon测试', NOW(), NOW());
UPDATE orders_full SET status = 'DONE' WHERE id = 1;
DELETE FROM orders_full WHERE id = 3;
"

# 3. 查看Paimon文件
ls -la /tmp/paimon_warehouse/paimon_orders/
# 预期看到:
#   schema/         ← Schema元数据
#   manifest/       ← Manifest文件
#   snapshot/       ← Snapshot文件
#   bucket-0/       ← Bucket数据文件
#   bucket-1/
#   ...
```

#### 步骤3：从Paimon表流式读取Changelog

```sql
-- 使用Flink SQL从Paimon表流式读取增量Changelog
CREATE TABLE paimon_orders (
    id INT,
    order_id STRING,
    user_id INT,
    product STRING,
    amount DECIMAL(10,2),
    status STRING,
    phone STRING,
    create_time TIMESTAMP(3),
    update_time TIMESTAMP(3),
    PRIMARY KEY (id) NOT ENFORCED
) WITH (
    'connector' = 'paimon',
    'warehouse' = '/tmp/paimon_warehouse',
    'path' = '/tmp/paimon_warehouse/paimon_orders',
    'streaming-read-mode' = 'changelog'   -- changelog模式输出完整变更记录
);

-- 流式消费Changelog
-- 会输出INSERT (+I)、UPDATE_BEFORE (-U)、UPDATE_AFTER (+U)、DELETE (-D)
SELECT id, order_id, status, amount
FROM paimon_orders
/*+ OPTIONS('scan.mode' = 'latest') */;
```

**预期输出（Changelog流）：**
```
+I(8, ORD_PAIMON, PAID, 199.00)
-U(1, ORD20240001, PAID, 6999.00)     ← 更新前
+U(1, ORD20240001, DONE, 6999.00)     ← 更新后
-D(3, ORD20240003, PENDING, 7999.00)  ← 删除
```

#### 步骤4：Paimon表的维表Lookup Join

Paimon表可以作为维表，在Flink SQL CDC中进行实时Lookup Join：

```sql
-- CDC订单事实表
CREATE TABLE orders_cdc (
    order_id STRING,
    user_id INT,
    amount DECIMAL(10,2),
    status STRING,
    proc_time AS PROCTIME(),
    PRIMARY KEY (order_id) NOT ENFORCED
) WITH (
    'connector' = 'mysql-cdc',
    'hostname' = 'localhost',
    'port' = '3306',
    'username' = 'cdc_user',
    'password' = 'cdc_pass',
    'database-name' = 'shop',
    'table-name' = 'orders_fact'
);

-- Paimon维表（从CDC写入，实时更新）
CREATE TABLE user_dim (
    user_id INT,
    username STRING,
    level STRING,
    PRIMARY KEY (user_id) NOT ENFORCED
) WITH (
    'connector' = 'paimon',
    'warehouse' = '/tmp/paimon_warehouse',
    'path' = '/tmp/paimon_warehouse/paimon_users',
    'lookup.cache-rows' = '10000'    -- Lookup缓存行数
);

-- Lookup Join：实时关联CDC订单和Paimon维表
SELECT
    o.order_id,
    u.username,
    u.level,
    o.amount,
    o.status
FROM orders_cdc AS o
LEFT JOIN user_dim FOR SYSTEM_TIME AS OF o.proc_time AS u
ON o.user_id = u.user_id;
```

#### 步骤5：Paimon表的Compaction管理

Paimon通过Compaction来控制LSM-Tree的层级合并：

```sql
-- 手动触发全量Compaction
CALL sys.compact(
    table => 'default.paimon_orders',
    partitions => 'status=PAID'
);

-- 或通过Flink作业自动Compaction
CREATE TABLE paimon_orders_compacted (
    id INT PRIMARY KEY NOT ENFORCED,
    order_id STRING,
    status STRING,
    amount DECIMAL(10,2)
) WITH (
    'connector' = 'paimon',
    'warehouse' = '/tmp/paimon_warehouse',
    'auto-compaction' = 'true',
    'compaction.max.file-num' = '50',
    'compaction.min.file-num' = '10',
    'compaction.parallelism' = '2'
);
```

#### 常见陷坑及解决方法

| 问题 | 原因 | 解决方法 |
|------|------|---------|
| Paimon表Snapshot数量激增 | 每次提交都产生Snapshot | 配置`snapshot.num-retained.max=100`自动清理 |
| Changelog Producer="input"模式下数据不一致 | 输入的Changelog顺序与Paimon预期不符 | 改用`full-compaction`模式（准确性最高） |
| Lookup Join时维表数据未更新 | Lookup缓存未刷新 | 减小`lookup.cache-rows`或设置`lookup.cache-ttl` |
| Bucket数导致数据倾斜 | Bucket数设置不合理 | Bucket数 = 期望并行度，建议4~16 |

---

## 4 项目总结

### Iceberg vs Paimon对比

| 维度 | Iceberg | Paimon |
|------|---------|--------|
| **数据组织** | Copy-On-Write + Position Delete | LSM-Tree (Sorted Run) |
| **写入吞吐** | 中（UPDATE需要读-改-写） | 高（顺序追加） |
| **查询延迟** | 低（Parquet列存，合并少） | 中（需要Merge多Level） |
| **CDC Changelog** | 需要额外逻辑 | 原生支持 |
| **流式读** | ❌ | ✅ |
| **Lookup Join** | ❌ | ✅ |
| **Schema Evolution** | ✅ | ✅（有限制） |
| **Time Travel** | ✅ | ✅ |

### CDC场景选型建议

- **实时OLAP查询**（使用Trino/StarRocks） → Iceberg
- **流式ETL的中间层**（Flink写入 → Flink读取增量） → Paimon
- **CDC入湖 + 多维分析** → Iceberg
- **CDC入湖 + 流式增值** → Paimon
- **数据归档 + 历史回溯** → Iceberg

### 注意事项

1. **Bucket数定则难改**：Paimon的Bucket数创建后不支持修改。根据数据量和并行度合理选择（一般4~16）。
2. **Changelog Producer选择**：CDC场景推荐`input`模式（延迟最低）。如果对数据准确性要求极高（如金融），使用`full-compaction`模式。
3. **文件系统选择**：生产环境建议使用HDFS或S3，不要使用本地文件系统（`file://`不支持跨节点共享）。

### 常见踩坑经验

**故障案例1：Paimon表数据膨胀——Compaction跟不上写入速度**
- **现象**：Paimon表的数据文件数不断增加，LSM-Tree的Level 0文件堆积
- **根因**：写入速度 > Compaction速度，Level 0文件未被及时合并
- **解决方案**：增加Compaction并行度（`compaction.parallelism=4`），或调大`compaction.max.num.sorted-run`

**故障案例2：输入Changelog乱序导致Paimon数据错误**
- **现象**：Paimon表查询结果与MySQL不一致，同一主键出现两条记录
- **根因**：Flink CDC的Changelog流在部分场景下乱序（如多表并行同步时的DDL事件）
- **解决方案**：启用`changelog-producer=lookup`或`full-compaction`，在Compaction阶段重新排序

**故障案例3：Paimon表的Snapshot不清理导致磁盘爆满**
- **现象**：Paimon的表目录下snapshot目录包含数万个文件
- **根因**：CDC作业每次Checkpoint都产生一个新的Snapshot，但未配置自动清理
- **解决方案**：设置`snapshot.num-retained.max=100`（保留最近100个Snapshot），或定期运行`CALL sys.expire_snapshots()`

### 思考题

1. **进阶题①**：Paimon的LSM-Tree结构相比Iceberg的Copy-On-Write，在CDC场景下的写入吞吐能提升多少？如果有50%的UPDATE操作（MySQL频繁UPDATE），两种方案的写入延迟会有多大差异？

2. **进阶题②**：Paimon的`changelog-producer=input`模式直接从Flink CDC的Changelog流中提取变更记录。但如果Changelog流中包含两个连续的UPDATE（`-U, +U, -U, +U`），Paimon会如何处理？尝试用一个实际的MySQL两次UPDATE来验证。

---

> **下一章预告**：第25章「OLAP实时写入：Doris / StarRocks」——当CDC数据需要直接写入OLAP引擎进行实时分析时，Doris和StarRocks是最佳选择。本章将实战Flink CDC + Doris/StarRocks的实时数据同步，包括Stream Load配置、模型选择、分区策略和性能优化。
