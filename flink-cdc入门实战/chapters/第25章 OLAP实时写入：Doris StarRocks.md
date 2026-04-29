# 第25章 OLAP实时写入：Doris / StarRocks

## 1 项目背景

### 业务场景：运营看板需要实时数据

公司的运营看板（Dashboard）需要展示**实时GMV、订单量、转化率**等指标。数据来源是MySQL订单库，但直接查询MySQL主库会压垮交易系统。运营团队需要一个——**秒级延迟、高并发查询、支持复杂聚合**的分析型数据库。

Doris和StarRocks是当前最流行的实时OLAP引擎，它们的核心优势：
1. **列式存储** + **向量化执行**：聚合查询比MySQL快10~100倍
2. **Stream Load**：支持高吞吐的实时数据导入
3. **模型灵活**：Duplicate / Unique / Aggregate三种模型适应不同场景

Flink CDC + Doris/StarRocks 的组合，实现了"数据库变更 → OLAP实时分析"的端到端链路。

### 架构

```
MySQL Binlog
    │
    ▼
Flink CDC Source
    │
    ▼
Flink CDC Doris/StarRocks Sink
    ├── Stream Load (HTTP协议批量写入)
    │   └── 数据格式: JSON / CSV
    ├── 表模型选择:
    │   ├── Duplicate: 保留所有历史（适合事实表）
    │   ├── Unique: 按主键去重（适合维表）
    │   └── Aggregate: 预聚合（适合汇总表）
    │
    ▼
Doris / StarRocks 集群
    └── 运营看板查询 (秒级响应)
```

---

## 2 项目设计 · 三人交锋对话

**小胖**（兴奋）：Doris我知道！之前查过文档，说是"MySQL兼容的OLAP数据库"。那Flink CDC往Doris写数据，是不是就像往MySQL写一样简单？

**大师**：API层面确实简单——Flink CDC Pipeline配置中指定`sink.type: doris`即可。但底层写入机制完全不同：

MySQL Sink（JDBC）：逐条INSERT / UPSERT
Doris Sink（Stream Load）：HTTP批量导入，积攒一批数据后统一发送

**Stream Load的流程：**
```
Flink CDC Sink →
  1. 积攒数据（默认10秒或1万条）
  2. 构造HTTP PUT请求到Doris BE
  3. Doris BE写入本地存储
  4. 返回Label + Status
  5. Flink Checkpoint确认

幂等保证：每个批次有唯一Label，Doris保证Label去重
```

**小白**：Doris有三种表模型——Duplicate、Unique、Aggregate。CDC数据应该用哪种？

**大师**：这是使用Doris/StarRocks做CDC最关键的决策。

**1. Duplicate模型（重复模型）**——保留所有历史记录
```sql
-- 适合：订单明细（事实表），每条记录都是唯一的
-- CDC行为：INSERT追加，UPDATE = DELETE旧 + INSERT新
CREATE TABLE orders_dup (
    order_id VARCHAR(64),
    status VARCHAR(32),
    amount DECIMAL(10,2)
) DUPLICATE KEY(order_id);
```

**2. Unique模型（唯一模型）**——按主键去重
```sql
-- 适合：用户信息（维表），每个用户只保留最新状态
-- CDC行为：INSERT新增，UPDATE覆盖，DELETE物理删除
CREATE TABLE users_unique (
    user_id INT,
    username VARCHAR(64),
    level VARCHAR(32)
) UNIQUE KEY(user_id);
```

**3. Aggregate模型（聚合模型）**——预聚合
```sql
-- 适合：每日销售汇总，CDC的每个INSERT/DELETE影响聚合值
-- CDC行为：INSERT = REPLACE_IF_NOT_EXISTS，DELETE = 反向聚合
CREATE TABLE daily_sales_agg (
    sale_date DATE,
    category VARCHAR(64),
    total_amount DECIMAL(10,2) SUM
) AGGREGATE KEY(sale_date, category);
```

**技术映射**：三种模型的关系可以类比为：Duplicate像"日志系统"（所有记录都保留），Unique像"用户档案"（永远是最新版本），Aggregate像"记账本"（只记总数，不记明细）。

**小白**：那在CDC场景中，DELETE事件怎么办？Doris的Unique模型中删除数据需要什么操作？

**大师**：DELETE是CDC入OLAP最棘手的问题。Doris提供了两种方式：
1. **DELETE Flag**：在导入数据中包含`__DELETE_SIGN__`列，标记该行需要被删除
2. **DELETE SQL**：执行`DELETE FROM table WHERE condition`语句

Flink CDC Doris Sink默认使用**DELETE Flag**方式：

```
事件类型        → 写入Doris
INSERT         → 正常写入（__DELETE_SIGN__=false）
UPDATE         → 写入两条（旧行标记删除 + 新行插入）
DELETE         → 写入一条（__DELETE_SIGN__=true）
```

---

## 3 项目实战

### 环境准备

**Docker Compose新增Doris/StarRocks服务：**
```yaml
doris-fe:
  image: apache/doris:2.0-fe
  container_name: doris-fe-cdc
  ports:
    - "8030:8030"    # Web UI
    - "9030:9030"    # MySQL协议连接
  networks:
    - flink-cdc-net

doris-be:
  image: apache/doris:2.0-be
  container_name: doris-be-cdc
  ports:
    - "8040:8040"    # HTTP Server（Stream Load端口）
  depends_on:
    - doris-fe
  networks:
    - flink-cdc-net
```

**Maven依赖：**
```xml
<dependency>
    <groupId>org.apache.flink</groupId>
    <artifactId>flink-cdc-pipeline-connector-doris</artifactId>
    <version>3.0.0</version>
</dependency>
```

### 分步实现

#### 步骤1：在Doris中创建目标表

```sql
-- 通过MySQL协议连接Doris
mysql -h 127.0.0.1 -P 9030 -u root

-- 创建数据库
CREATE DATABASE IF NOT EXISTS cdc_demo;

-- 使用Unique模型创建订单表（支持UPSERT和DELETE）
CREATE TABLE cdc_demo.orders_doris (
    id              INT,
    order_id        VARCHAR(64),
    user_id         INT,
    product         VARCHAR(128),
    amount          DECIMAL(10,2),
    status          VARCHAR(32),
    phone           VARCHAR(32),
    create_time     DATETIME,
    update_time     DATETIME
) UNIQUE KEY(id)                         -- Unique模型：按id去重
DISTRIBUTED BY HASH(id) BUCKETS 10       -- 分桶
PROPERTIES (
    "replication_num" = "1",             -- 单副本（生产环境用3）
    "enable_unique_key_merge_on_write" = "true"  -- Unique Key的MOR模式
);
```

#### 步骤2：配置Doris Sink Pipeline

```yaml
source:
  type: mysql
  hostname: localhost
  port: 3306
  username: cdc_user
  password: cdc_pass
  tables: shop.orders_full

sink:
  type: doris
  # Doris连接信息
  fenodes: localhost:8030                 # FE的HTTP端口
  table-create.enabled: true              # 自动建表

  # 认证
  username: root
  password: ""

  # 表映射
  table:
    database: cdc_demo
    name: orders_doris

  # Stream Load配置
  sink:
    label-prefix: cdc_doris_              # Label前缀（幂等去重）
    batch-size: 10000                     # 每批积攒1万条
    batch-interval: 10000                 # 最多等10秒
    max-retries: 3                        # 失败重试3次
    buffer-flush.queue-size: 1024
    # 数据格式
    format: json                          # json | csv
    strip-outsides: true
    ignore-parse-errors: true

pipeline:
  name: CDC to Doris Pipeline
  parallelism: 2
  schema.change.behavior: EVOLVE
```

#### 步骤3：观察Doris中的数据变更

```bash
# 1. 提交Pipeline
flink-cdc.sh pipeline-doris.yaml --use-mini-cluster

# 2. MySQL执行变更
docker exec mysql-cdc mysql -uroot -proot123 -e "
USE shop;
INSERT INTO orders_full VALUES (9, 'ORD_DORIS', 1003, 'Doris Test', 299.00, 'PAID', '13800009999', '入Doris测试', NOW(), NOW());
UPDATE orders_full SET status = 'SHIPPED' WHERE id = 1;
"

# 3. 查询Doris验证
mysql -h 127.0.0.1 -P 9030 -u root -e "
SELECT id, order_id, status, amount, phone FROM cdc_demo.orders_doris ORDER BY id;
"

# 预期输出：
# id=1: 已更新为SHIPPED
# id=9: 新插入行
```

#### 步骤4：验证Doris的DELETE处理

```sql
-- 在MySQL中执行DELETE
docker exec mysql-cdc mysql -uroot -proot123 -e "
USE shop;
DELETE FROM orders_full WHERE id = 4;
"

-- 在Doris中验证
mysql -h 127.0.0.1 -P 9030 -u root -e "
SELECT * FROM cdc_demo.orders_doris WHERE id = 4;
"
-- 预期输出: Empty set（该行已被删除）
```

#### 步骤5：Aggregate模型——实时聚合订单金额

创建Aggregate模型表验证CDC的实时聚合效果：

```sql
-- 按状态聚合订单金额
CREATE TABLE cdc_demo.order_stats_agg (
    status          VARCHAR(32),
    order_count     BIGINT SUM,
    total_amount    DECIMAL(20,2) SUM,
    max_amount      DECIMAL(10,2) MAX
) AGGREGATE KEY(status)
DISTRIBUTED BY HASH(status) BUCKETS 5
PROPERTIES ("replication_num" = "1");

-- 编写一条Flink SQL写入聚合表（每收到一个CDC事件更新聚合结果）
INSERT INTO cdc_demo.order_stats_agg
SELECT
    status,
    COUNT(*) AS order_count,
    SUM(amount) AS total_amount,
    MAX(amount) AS max_amount
FROM cdc_demo.orders_doris
GROUP BY status;
```

#### 常见陷坑及解决方法

| 问题 | 原因 | 解决方法 |
|------|------|---------|
| Stream Load返回`Error` | 数据格式不正确或Doris表模型不匹配 | 检查数据中的列名和类型是否与Doris表一致 |
| Unique模型下UPDATE变成INSERT | `enable_unique_key_merge_on_write=false` | 设置`enable_unique_key_merge_on_write=true` |
| 写入延迟高（>1分钟） | `batch-interval`或`batch-size`设置过大 | 调小`batch-interval: 5000`、`batch-size: 5000` |
| DELETE在Unique模型中不生效 | 未启用DELETE Flag支持 | 配置`sink.properties.__DELETE_SIGN__`或使用`DELETE FROM` |
| 数据乱序导致Doris中主键冲突 | Unique模型在MOR模式下未正确Merge | 开启Sequence列：`__DORIS_SEQUENCE_COL__` |

---

## 4 项目总结

### Doris模型选择决策

```
CDC数据入Doris
├── 数据含有UPDATE/DELETE？
│   ├── 是
│   │   ├── 需要保留所有历史版本 → Duplicate模型（不适合UPSERT）
│   │   ├── 只需要最新状态 → Unique模型 ✅
│   │   └── 需要预聚合结果 → Aggregate模型（仅INSERT）
│   └── 否（只有INSERT）
│       └── 任意模型（推荐Duplicate）
│
├── 查询模式
│   ├── 按主键精确查询 → Unique模型
│   ├── 时间范围聚合查询 → Aggregate模型
│   └── 明细数据查询 → Duplicate模型
│
└── 数据量级
    ├── < 1亿行 → Duplicate模型（最灵活）
    ├── 1亿~10亿行 → Unique模型
    └── > 10亿行 → Aggregate模型（预聚合减少数据量）
```

### 注意事项

1. **Label去重机制**：Stream Load的Label前缀必须全局唯一。Flink CDC Doris Sink使用`label-prefix + 批次ID`生成Label，确保Exactly-Once。
2. **模型选择不可逆**：Doris表的模型在建表时确定，后续不可修改。如果初期选错了模型，只能删表重建。
3. **Key列设计**：Unique模型和Aggregate模型的所有Key列共同决定了去重/分组的粒度。Key列不能太多（建议不超过5列）。
4. **副本数**：开发环境设置`replication_num=1`，生产环境至少3。

### 常见踩坑经验

**故障案例1：Doris Sink报错"too many open files"**
- **现象**：Stream Load频繁失败，日志中有"too many open files"
- **根因**：Flink并行度过高导致Doris BE的连接数超过文件描述符限制
- **解决方案**：降低Sink并行度，或增大Doris BE的`max_open_files`配置

**故障案例2：Unique模型下数据没有正确去重**
- **现象**：同一个`id`在Doris中有多条记录
- **根因**：`enable_unique_key_merge_on_write=false`（默认关闭），Merge-On-Read模式下查询时未正确聚合
- **解决方案**：建表时设置`"enable_unique_key_merge_on_write" = "true"`，或升级到Doris 2.0+

**故障案例3：CDC DELETE事件写入Doris后数据未被删除**
- **现象**：MySQL中已经删除的订单还在Doris中
- **根因**：Doris Sink未正确处理DELETE事件（默认`__DELETE_SIGN__`未配置）
- **解决方案**：在Sink配置中添加`delete-enabled: true`

### 思考题

1. **进阶题①**：Doris的Unique模型在Merge-On-Read模式下，Query时需要合并Base数据 + Delta数据。如果一个主键在短时间被UPDATE了10次，Doris需要合并10个版本的Delta数据吗？MOR的Compaction策略如何控制Delta文件数？

2. **进阶题②**：StarRocks（Doris的分支）在Flink CDC集成方面比Doris多了一个特性——**主键表（Primary Key表）**的`UPDATE`语法不支持`DELETE`操作。如果你需要从MySQL CDC同步DELETE事件到StarRocks，应该如何处理？提示：考虑使用Flink CDC Transform层的`__DORIS_DELETE_SIGN__`列标记。

---

> **下一章预告**：第26章「Exactly-Once与幂等写入」——Flink CDC作业如何实现端到端的"数据不丢不重"？本章将深入Flink的Two-Phase Commit机制，探讨Kafka事务写入、Doris Label去重、端到端Exactly-Once的验证方法。
