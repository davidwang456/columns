# 第16章 Pipeline YAML API：声明式数据集成

## 1 项目背景

### 业务场景：从DataStream/SQL到声明式Pipeline

基础篇中我们用DataStream API和SQL API实现了CDC。但有天架构师发现：**团队里10个Flink CDC作业，每个都有一堆重复的代码**：
- 每个作业都要配置Checkpoint、State Backend
- 都要定义Source、Transform、Sink
- 代码风格不统一，维护成本高

有没有一种方式——**用配置文件定义数据管道（Pipeline），而不需要写Java代码**？

Flink CDC 3.x的**Pipeline YAML API**正是为此而生。它提供了一种声明式的方式：
- 用YAML定义Source、Sink、Route、Transform、UDF
- 通过`flink-cdc.sh`一键提交
- 无需写Java/SQL代码

### Pipeline YAML vs 其他API的定位

```
                 学习难度
                    ▲
                    │
                    │           DataStream API
                    │         (最高灵活性)
                    │
                    │       SQL API
                    │    (中等灵活性)
                    │
                    │  Pipeline YAML ← 你在这里
                    │ (最低代码量)
                    │
                    └──────────────────────────► 代码量
```

---

## 2 项目设计 · 三人交锋对话

**小胖**（好奇）：Pipeline YAML是啥？是不是把第9章那个YAML文件扩展成大而全的声明式配置？

**大师**（点头）：是的。但Pipeline YAML不仅仅是"DataStream API的YAML版本"，而是一套更高级的抽象。它定义了 CDC数据管道的完整生命周期。

一个完整的Pipeline YAML包含5个部分：

```yaml
source:      # 数据源定义（从哪里来）
sink:        # 目标定义（到哪里去）
route:       # 路由规则（表映射）
transform:   # 数据转换（投影/过滤）
pipeline:    # 管道配置（全局参数）
```

**小白**：我注意到Pipeline YAML的`source`和`sink`支持多种类型。那Source和Sink是怎么组合的？是不是任意Source都可以搭配任意Sink？

**大师**：理论上是的——只要Source输出`Event`类型，Sink输入`Event`类型，就能自由组合。Pipeline框架内部通过`FlinkPipelineComposer`将Source、Transform、Route、Sink编排成一个完整的DataStream拓扑。

支持的Source-Sink组合：

| Source → Sink | Kafka | MySQL | Iceberg | Paimon | Doris | ES |
|--------------|-------|-------|---------|--------|-------|----|
| MySQL | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| PostgreSQL | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| MongoDB | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Oracle | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

**技术映射**：Pipeline YAML就像"乐高积木说明书"——你只需要说"我要把红色积木（MySQL Source）拼到蓝色积木（Kafka Sink）上"，框架自动帮你拼好。不需要自己设计每个积木的卡槽接口。

**小白**：那Pipeline YAML的内部架构是什么样的？YAML文件是怎么变成能运行的Flink作业的？

**大师**：Pipeline YAML的执行链路如下：

```
YAML文件 → YamlPipelineDefinitionParser
    ↓
PipelineDef（SourceDef, SinkDef, RouteDef, TransformDef, UDFDef...）
    ↓
FlinkPipelineComposer.compose()
    ↓
DataSourceTranslator → TransformTranslator → PartitioningTranslator
→ SchemaOperatorTranslator → DataSinkTranslator
    ↓
DataStream API拓扑图
    ↓
提交到Flink集群执行
```

这个过程中最核心的是`FlinkPipelineComposer`，它把YAML配置翻译成DataStream API的Operator链。

---

## 3 项目实战

### 环境准备

**关键JAR包（必须放到Flink的lib目录下）：**
```bash
# 1. Flink CDC Pipeline核心JAR
flink-cdc-pipeline-connector-mysql-3.0.0.jar
flink-cdc-pipeline-connector-kafka-3.0.0.jar

# 2. Flink CDC Composer（Pipeline解析和执行引擎）
flink-cdc-composer-3.0.0.jar
flink-cdc-cli-3.0.0.jar

# 3. Flink CDC Runtime
flink-cdc-runtime-3.0.0.jar
flink-cdc-common-3.0.0.jar

# 4. MySQL JDBC驱动
mysql-connector-java-8.0.33.jar
```

### 分步实现

#### 步骤1：最简Pipeline——MySQL → Console

创建 `pipeline-simple.yaml`：

```yaml
source:
  type: mysql
  hostname: localhost
  port: 3306
  username: cdc_user
  password: cdc_pass
  tables: shop.orders_full

sink:
  type: values                # Values Sink = 打印到控制台
  name: Console Sink

pipeline:
  name: Simple CDC Pipeline
  parallelism: 1
```

**提交命令：**
```bash
flink-cdc.sh pipeline-simple.yaml \
  --use-mini-cluster \
  --flink-home /opt/flink
```

**预期输出：**
```
[INFO] Pipeline: Simple CDC Pipeline
[INFO] Source: MySQL (shop.orders_full)
[INFO] Sink: Console Sink
[INFO] Job ID: a1b2c3d4e5f6a7b8c9d0e1f2
[INFO] Starting MiniCluster...
[INFO] +I[1, ORD20240001, 1001, iPhone 15, 6999.00, PAID, ...]
[INFO] +I[2, ORD20240002, 1002, MacBook Air, 8999.00, SHIPPED, ...]
```

#### 步骤2：完整Pipeline——MySQL → Kafka + 路由 + 转换

创建 `pipeline-full.yaml`：

```yaml
# ========== Source定义 ==========
source:
  type: mysql
  name: Order Database
  hostname: localhost
  port: 3306
  username: cdc_user
  password: cdc_pass
  tables: shop.orders_full, shop.users        # 多表支持
  server-id: 5400-5402
  server-time-zone: Asia/Shanghai
  scan.startup.mode: initial
  # 增量快照配置
  chunk-column:                               # 自定义切分列
    shop.orders_full: id
    shop.users: id
  capture-new-tables: true                    # 动态发现新增表

# ========== Sink定义 ==========
sink:
  type: kafka
  name: Order Events
  properties:
    bootstrap.servers: localhost:9092
    auto.create.topics.enabled: true
  # Kafka特有配置
  topic-prefix: cdc_                          # Topic前缀
  sink:

# ========== 路由规则 ==========
route:
  - source-table: shop.orders_full
    sink-table: ods.orders                    # 路由到ods库
    description: "订单表从shop路由到ods"

  - source-table: shop.users
    sink-table: ods.users
    description: "用户表从shop路由到ods"

# ========== 数据转换 ==========
transform:
  - source-table: shop.orders_full
    # 投影：选择列 + 计算列
    projection: |
      id,
      order_id,
      user_id,
      product,
      CAST(amount AS DECIMAL(10,2)) / 100 AS amount,  # 分转元
      status,
      create_time
    # 过滤：只保留PAID和SHIPPED
    filter: "status = 'PAID' OR status = 'SHIPPED'"
    # 主键定义
    primary-keys: id

  - source-table: shop.users
    projection: |
      id,
      username,
      email
    filter: "level = 'VIP'"

# ========== 管道配置 ==========
pipeline:
  name: Full CDC Pipeline
  parallelism: 2
  schema.change.behavior: EVOLVE              # Schema变更处理策略
  execution.runtime-mode: STREAMING           # STREAMING | BATCH
  # 模型/AI配置（可选）
  # model:
  #   - model-name: text_embedding
  #     class-name: OpenAIEmbeddingModel
  #     ...
```

#### 步骤3：Pipeline执行和配置解析

```bash
# 1. 提交Pipeline到Flink Standalone集群
flink-cdc.sh pipeline-full.yaml \
  --flink-home /opt/flink \
  --target remote \
  --jar /opt/flink/lib/flink-cdc-pipeline-connector-mysql-3.0.0.jar \
  --jar /opt/flink/lib/flink-cdc-pipeline-connector-kafka-3.0.0.jar

# 2. 在Flink Web UI (http://localhost:8081) 查看作业
# 可以看到作业图包含以下算子：
#   Source: MySQL CDC → PreTransform → PostTransform
#   → Partitioning → SchemaOperator → Sink: Kafka

# 3. 验证Kafka中收到了数据
docker exec kafka-cdc kafka-console-consumer \
  --topic cdc_ods.orders \
  --bootstrap-server localhost:9092 \
  --from-beginning --max-messages 5
```

#### 步骤4：Pipeline + UDF配置

```yaml
# UDF定义
udfs:
  - function-name: phone_mask
    class-name: com.example.PhoneMaskUDF
    # 可指定UDF JAR包
    # jar-file: /opt/flink/lib/cdc-udf-demo.jar

  - function-name: order_status_name
    class-name: com.example.OrderStatusUDF

# 在transform中使用UDF
transform:
  - source-table: shop.orders_full
    projection: |
      id,
      order_id,
      phone_mask(phone) AS phone_masked,       # UDF脱敏
      order_status_name(status) AS status_name, # UDF转换
      amount
    filter: "status_name = '已支付' OR status_name = '已发货'"
```

#### 步骤5：使用Pipeline提交MySQL→MySQL同步

```yaml
source:
  type: mysql
  hostname: localhost
  port: 3306
  username: cdc_user
  password: cdc_pass
  tables: shop.orders_full

sink:
  type: mysql
  hostname: localhost
  port: 3307                                    # 备库端口
  username: cdc_user
  password: cdc_pass
  database: shop_backup
  table: orders
  # MySQL Sink特有配置
  auto-create-table: true                       # 自动建表
  replace-mode: UPSERT                          # INSERT / UPSERT / REPLACE

pipeline:
  name: MySQL → MySQL Sync
  parallelism: 1
  schema.change.behavior: EVOLVE
```

#### 常见陷坑及解决方法

| 问题 | 原因 | 解决方法 |
|------|------|---------|
| `Connector 'mysql' not found` | Pipeline连接器JAR包未加载 | 添加`--jar flink-cdc-pipeline-connector-mysql-3.0.0.jar`参数 |
| YAML解析报错`MappingException` | YAML格式错误（缩进不一致） | 使用2空格缩进，不要用Tab |
| Transform不生效 | `source-table`正则写错 | 确保格式为`db.table`，支持通配符`*` |
| Pipeline Sink不支持某些配置 | Sink类型不同，支持的配置不同 | 查看对应Sink的连接器文档 |
| `auto-create-table`失败 | 备库用户缺少建表权限 | 执行`GRANT CREATE, ALTER ON shop_backup.* TO 'cdc_user'@'%'` |

---

## 4 项目总结

### 三种API对比总结

| 维度 | DataStream API | SQL API | Pipeline YAML |
|------|---------------|---------|--------------|
| **代码量** | 30-100行 | 5-20行SQL | 20-50行YAML |
| **灵活性** | 最高（任意算子） | 中（限于SQL语义） | 中（限于YAML定义） |
| **学习曲线** | 陡峭（需懂Flink算子） | 中（需懂SQL） | 低（只需懂配置） |
| **调试能力** | 强（IDE断点调试） | 中（SQL执行计划） | 弱（YAML黑盒） |
| **Schema Evolution** | 手动 | 部分自动 | 自动 |
| **多表配置** | 手动编码 | 需要多个DDL | 一行tables配置 |
| **适合团队** | 平台开发团队 | 数据分析团队 | 数据运维/平台 |

### 适用场景

**Pipeline YAML最适合：**
1. **标准CDC管道**：Source→Transform→Sink的标准流程
2. **多表批量接入**：几十张表的同步，YAML配置比Java代码快得多
3. **快速原型验证**：不写Java代码，快速验证CDC方案是否可行
4. **运维团队维护**：不适合写代码的运维团队也能操作

**Pipeline YAML不适合：**
1. **复杂多流Join**：需要自定义Join逻辑时
2. **自定义状态和定时器**：需要用到`KeyedProcessFunction`的场景
3. **非标准数据类型处理**：如MySQL的GEOMETRY需要自定义序列化

### 注意事项

1. **YAML缩进**：Flink CDC Pipeline YAML使用严格的YAML缩进（2空格），不要使用Tab。可以用`yamllint`做语法检查。
2. **密码安全**：YAML中的密码是明文。生产环境应使用环境变量引用或密钥管理服务。
3. **版本锁定**：Pipeline框架的JAR版本必须一致（common/runtime/composer/cli/connector），混合版本会导致NoSuchMethodError。
4. **端口冲突**：`--use-mini-cluster`模式默认使用8081端口，如果有其他Flink集群在运行需要指定不同端口。

### 常见踩坑经验

**故障案例1：flink-cdc.sh脚本找不到**
- **现象**：安装Flink CDC后，执行`flink-cdc.sh`报错"command not found"
- **根因**：`flink-cdc.sh`在`flink-cdc-dist`模块的发布包中，不是Flink自带的
- **解决方案**：从发布包（flink-cdc-3.0.0-bin.tar.gz）解压后，将`bin/flink-cdc.sh`放到PATH中

**故障案例2：Pipeline提交后TaskManager报错ClassNotFoundException**
- **现象**：作业已提交，但JM日志正常，TM日志报错"ClassNotFoundException: org.apache.flink.cdc.common.event.Event"
- **根因**：Pipeline JAR包只在JobManager的lib/目录中，TaskManager上没有
- **解决方案**：确保所有Pipeline连接器的JAR包也复制到TaskManager的lib/目录，或在`flink-cdc.sh`提交时使用`--jar`参数

**故障案例3：YAML配置了transform但数据没变化**
- **现象**：控制台输出的数据仍然是原始字段，没有投影和过滤效果
- **根因**：`source-table`的格式不匹配。如果是MySQL，格式应为`db.table`，但YAML中写了`table`而非`db.table`
- **解决方案**：检查`transform.source-table`是否与`source.tables`中的格式完全一致

### 思考题

1. **进阶题①**：Pipeline YAML的`transform`块中的`projection`和`filter`，最终在Flink作业图中对应什么Operator？与DataStream API的`map()`和`filter()`有何区别？提示：查看`PreTransformOperator`和`PostTransformOperator`的实现。

2. **进阶题②**：如果使用Pipeline YAML从MySQL同步到MySQL，`sink.type: mysql`是否支持Exactly-Once写入？MySQL Sink的幂等写入是如何实现的？提示：查看`MysqlSinkWriter`源码中的`prepareCommit`和`snapshotState`方法。

---

> **下一章预告**：第17章「Pipeline链路全解析」——从YAML配置到Flink DataStream拓扑的完整翻译过程。深入`FlinkPipelineComposer`的源码，理解Source→Transform→Partitioning→SchemaOperator→Sink六阶段架构设计。
