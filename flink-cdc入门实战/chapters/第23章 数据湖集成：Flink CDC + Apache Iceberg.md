# 第23章 数据湖集成：Flink CDC + Apache Iceberg

## 1 项目背景

### 业务场景：从实时CDC到数据湖的"最后一公里"

实时CDC数据最终要沉淀到数据湖（Data Lake），用于OLAP分析、机器学习训练和历史数据回溯。Apache Iceberg作为新一代数据湖表格式，提供了ACID事务、Time Travel、Schema Evolution等关键能力。

但CDC数据入湖面临两个挑战：
1. **CDC数据有UPDATE/DELETE**——数据湖通常是"写后不可变"的（Append-Only），如何处理行级变更？
2. **Schema变更同步**——MySQL加了列，Iceberg表也要自动加列

Flink CDC + Apache Iceberg的组合完美解决了这些问题：**Iceberg的Merge-On-Read + CDC的Changelog流，实现了数据湖上的行级更新**。

### CDC入湖架构

```
MySQL Binlog (INSERT/UPDATE/DELETE)
    │
    ▼
Flink CDC Source (Changelog Stream: +I, -U, +U, -D)
    │
    ▼
Iceberg Sink:
  ├── INSERT → 直接Append
  ├── UPDATE_BEFORE → 删除旧数据 (position delete)
  ├── UPDATE_AFTER → 追加新数据 (append)
  └── DELETE → 标记删除 (position delete / equality delete)
    │
    ▼
Iceberg Table (HDFS/S3)
  ├── Data Files (Parquet/ORC/Avro)
  ├── Delete Files (position delete / equality delete)
  └── Metadata (Snapshot, Manifest, Schema)
```

---

## 2 项目设计 · 三人交锋对话

**小胖**（好奇）：冰山的Iceberg和Flink CDC啥关系？是不是把CDC数据写入Iceberg，然后Iceberg上的查询引擎（Trino/Spark）就能读到实时数据了？

**大师**：是的！Iceberg的核心理念是"表格式 + 元数据层"——它不像Hive那样把数据文件直接暴露给查询引擎，而是在数据文件和查询引擎之间加了一层**元数据管理层**。

当CDC数据写入Iceberg时，Iceberg会：
1. 将数据写入新的Parquet/ORC文件（Append）
2. 对于UPDATE/DELETE，写入Delete File（标记哪些行被修改/删除）
3. 更新Metadata Layer：添加新的Snapshot指针

查询引擎读取时：
- 如果是最新Snapshot：看到的是"合并了Deletes之后"的数据
- 如果是指定时间点的历史Snapshot：看到的是该时间点的数据快照（Time Travel）

**小白**：那我Flink CDC的DELETE事件，Iceberg怎么处理？Iceberg的数据文件不是不可变的吗？

**大师**：Iceberg通过两种方式处理行级变更：

**方式1：Copy-On-Write（COW）**
当UPDATE/DELETE发生时，读取原数据文件，排除被删除的行后，写入新的数据文件。旧文件通过Metadata层隐藏。
- 优点：查询快（不需要合并Delete Files）
- 缺点：写入慢（需要重写整个文件）

**方式2：Merge-On-Read（MOR）——Iceberg + Flink CDC默认方式**
UPDATE/DELETE时只写入小的"Delete File"（`.delete`文件），标记被删除的行。查询时，读取端合并Data File + Delete File。
- 优点：写入快（只追加小文件）
- 缺点：查询慢（需要合并读取）

Flink CDC的Iceberg Sink默认使用MOR方式，因为CDC写入的实时性优先于查询性能。

**技术映射**：Copy-On-Write像"修改历史教材后，把整本书重印一遍"——查询时看到的是全新的、干净的书。Merge-On-Read像"在教材的勘误表上标注错误"——查询时既要看原书，还要看勘误表，才能知道真正的正确内容。

---

## 3 项目实战

### 环境准备

**Docker服务扩展：**
- MinIO（S3兼容存储，用于Iceberg的数据文件存放）
- Hadoop（HDFS可选，用于大规模部署）

**Maven依赖：**
```xml
<dependency>
    <groupId>org.apache.flink</groupId>
    <artifactId>flink-cdc-pipeline-connector-iceberg</artifactId>
    <version>3.0.0</version>
</dependency>
<dependency>
    <groupId>org.apache.iceberg</groupId>
    <artifactId>iceberg-flink-runtime-1.20</artifactId>
    <version>1.5.0</version>
</dependency>
```

### 分步实现

#### 步骤1：配置Iceberg Catalog和Pipeline YAML

```yaml
source:
  type: mysql
  hostname: localhost
  port: 3306
  username: cdc_user
  password: cdc_pass
  tables: shop.orders_full, shop.users_dim, shop.products_dim

sink:
  type: iceberg
  # Iceberg Catalog配置
  catalog-type: hadoop                # hadoop | hive | rest | jdbc
  catalog-name: my_catalog
  catalog-database: cdc_db
  warehouse: /tmp/iceberg_warehouse   # 数据文件存储路径
  # 也可配置S3:
  # warehouse: s3://my-bucket/cdc
  
  # 表结构配置
  table-prefix: cdc_                  # 表名前缀
  auto-create-table: true             # 自动创建Iceberg表
  upsert-mode: merge-on-read          # merge-on-read | copy-on-write

  # Iceberg Sink配置
  commit-branch: main
  commit-interval: 60s                # 提交间隔（频繁提交产生小文件问题）

route:
  - source-table: shop.orders_full
    sink-table: cdc_orders

pipeline:
  name: CDC to Iceberg Pipeline
  parallelism: 2
  schema.change.behavior: EVOLVE
```

#### 步骤2：验证Iceberg数据写入

```bash
# 1. 提交Pipeline
flink-cdc.sh pipeline-iceberg.yaml --use-mini-cluster

# 2. 在MySQL中执行变更
docker exec mysql-cdc mysql -uroot -proot123 -e "
USE shop;
INSERT INTO orders_full VALUES (7, 'ORD_ICEBERG', 1001, 'Iceberg Test', 99.00, 'PAID', '13800007777', '入湖测试', NOW(), NOW());
"

# 3. 检查Iceberg表文件
ls -la /tmp/iceberg_warehouse/cdc_db/cdc_orders/
# 预期看到:
#   metadata/          ← 元数据
#   data/              ← Parquet数据文件
#   delete/            ← Delete Files (如果有UPDATE/DELETE)
```

#### 步骤3：验证Time Travel（时间旅行查询）

写入数据后，使用Spark或Trino查询Iceberg表的不同时间版本：

```sql
-- 查询当前数据
SELECT * FROM cdc_db.cdc_orders;

-- 查询特定时间点的历史快照
SELECT * FROM cdc_db.cdc_orders 
FOR SYSTEM_TIME AS OF '2024-01-15 10:00:00';

-- 查看历史Snapshot
SELECT * FROM cdc_db.cdc_orders.history;
```

#### 步骤4：Iceberg表的分区优化

对于CDC数据，合理分区可以显著提升查询性能：

```yaml
sink:
  type: iceberg
  # 分区配置（根据业务查询模式选择）
  partition-by:
    - days(create_time)                # 按天分区（适合时间范围查询）
  # 或者：
  # partition-by:
  #   - bucket(status, 10)             # 按状态哈希分区（适合状态过滤查询）
  
  # 分区后的文件大小优化
  target-file-size-bytes: 536870912    # 目标文件大小512MB
  write-distribution-mode: hash        # hash | range | none
```

#### 步骤5：CDC UPDATE/DELETE在Iceberg中的行为验证

```bash
# 1. 执行UPDATE
docker exec mysql-cdc mysql -uroot -proot123 -e "
USE shop;
UPDATE orders_full SET status = 'SHIPPED' WHERE id = 1;
"

# 2. 检查Iceberg的数据文件——发现新的Data File + Delete File
ls -la /tmp/iceberg_warehouse/cdc_db/cdc_orders/data/
ls -la /tmp/iceberg_warehouse/cdc_db/cdc_orders/delete/

# 3. 执行DELETE
docker exec mysql-cdc mysql -uroot -proot123 -e "
USE shop;
DELETE FROM orders_full WHERE id = 2;
"

# 4. 查看最终数据——id=2的行应已消失
```

#### 常见陷坑及解决方法

| 问题 | 原因 | 解决方法 |
|------|------|---------|
| 小文件问题 | 频繁提交产生大量小Parquet文件 | 设置`commit-interval: 300s`，增大`target-file-size-bytes` |
| Schema Evolution失败 | Iceberg不支持某些类型变更 | 检查Iceberg Schema兼容性规则 |
| Merge-On-Read查询慢 | Delete Files太多，查询时需要合并 | 定期执行`REWRITE DATA`和`REMOVE ORPHAN FILES` |
| Hadoop Catalog不支持多会话 | Hadoop Catalog的`file://`在不同进程中不共享 | 生产环境使用Hive Catalog或REST Catalog |

---

## 4 项目总结

### Iceberg vs Paimon vs Hudi对比

| 维度 | Iceberg | Paimon | Hudi |
|------|---------|--------|------|
| ACID事务 | ✅ | ✅ | ✅ |
| Schema Evolution | ✅ | ✅ | ✅ |
| Time Travel | ✅ | ✅ | ✅ |
| Merge-On-Read | ✅ | ✅ | ✅ |
| Flink CDC集成 | 原生支持（Pipeline） | 原生支持（Pipeline） | 需要额外配置 |
| 查询引擎 | Spark/Trino/Flink/StarRocks | Spark/Flink/Trino | Spark/Hive/Trino |
| 写入模式 | Append + Delete Files | Changelog + LSM | Copy-On-Write + Merge-On-Read |

### 注意事项

1. **小文件治理**：CDC的频繁提交会产生大量小文件。定期执行Iceberg的`REWRITE DATA`和`EXPIRE SNAPSHOTS`清理任务。
2. **分区策略**：CDC数据通常按时间分区（天/小时）。分区粒度越细，查询剪枝效果越好，但文件数越多。
3. **提交频率**：`commit-interval`建议设置为60~300秒。太短（<10秒）会产生大量小文件；太长（>1小时）会导致数据延迟大。

### 常见踩坑经验

**故障案例1：Iceberg表不断膨胀——历史Snapshot未清理**
- **现象**：Iceberg表存储量每天都在增长，但实际数据量没变
- **根因**：CDC作业每次提交都产生新的Snapshot，旧的Snapshot从未清理
- **解决方案**：定期运行`spark.sql("CALL catalog.system.expire_snapshots('cdc_db.cdc_orders', TIMESTAMP '2024-01-01'))"`或配置自动过期策略

**故障案例2：Flink CDC写入Iceberg时Schema Evolution失败**
- **现象**：MySQL执行`ALTER TABLE orders ADD COLUMN discount DECIMAL`后，Flink CDC报错`Iceberg schema evolution failed`
- **根因**：Iceberg的Schema Evolution不支持某些变更（如列重命名后的类型变化）
- **解决方案**：设置`schema.change.behavior=LENIENT`（允许Sink失败），或升级Iceberg版本

**故障案例3：Merge-On-Read查询结果与MySQL不一致**
- **现象**：DELETE一条数据后，查询Iceberg表发现该行还在
- **根因**：Iceberg的MOR模式下，Delete File虽然已经写入，但查询端没有正确合并Data File和Delete File
- **解决方案**：确认查询引擎支持Iceberg V2格式（支持Delete File），或启用Copy-On-Write模式

### 思考题

1. **进阶题①**：Iceberg的`Merge-On-Read`模式下，当DELETE文件积累到一定程度（比如Delete File中的记录数超过Data File的总行数），会发生什么？此时查询性能会下降多少？应该如何定期Compaction？

2. **进阶题②**：Flink CDC写入Iceberg时，如果MySQL执行了大事务（修改了10万行数据），Iceberg Sink如何处理这个大事务——是一个Flink Checkpoint提交一个大事务，还是拆分多个Checkpoint？Iceberg的Snapshot隔离级别如何保证数据一致性？

---

> **下一章预告**：第24章「数据湖集成：Flink CDC + Apache Paimon」——Apache Paimon（原Flake）作为新一代流式数据湖存储，在CDC场景下提供了更丰富的语义支持。本章将对比Paimon与Iceberg的差异，并实战Paimon的CDC Ingestion和Changelog查询。
