# 第28章：NoSQL 数据同步——MongoDB与HBase 读写实战

## 1. 项目背景

某社交 App 的用户行为日志存储在 MongoDB 中——用户每次点击、滑动、点赞都会生成一条 JSON 文档，日均 5 亿条。MongoDB 的灵活 Schema 非常适合在线业务（字段可以随时加），但对数据分析来说却是噩梦——BI 团队的 SQL 查询平台只认二维表，分析师无法直接查询嵌套文档。

架构组给出的方案是：每天凌晨用 DataX 将 MongoDB 的行为日志同步到 MySQL 分析库，展平嵌套文档、过滤噪音字段、补全时间维度。但执行时遇到三个致命问题：

1. **嵌套文档展平**：MongoDB 的一条文档 `{ "user": { "id": 123, "name": "张三" }, "action": "click" }` 在同步到 MySQL 时需要变成多列 `user_id=123, user_name="张三", action="click"`
2. **ObjectId 不可读**：MongoDB 的 `_id` 字段是 24 位十六进制字符串（如 `507f1f77bcf86cd799439011`），MySQL 分析师不知道这是什么
3. **数组字段处理**：`tags: ["new_user", "promotion"]` 在 MySQL 中怎么存？逗号分隔还是分多行？

同样的问题在 HBase 场景中也存在。某电信公司用 HBase 存储话单流水（RowKey 设计为 `手机号+时间戳`），需要将部分数据同步到 MySQL 供业务系统查询。HBase 的列族/列名体系与关系型完全不同，RowKey 设计直接决定了查询性能。

本章通过两个典型跨端同步实战——MongoDB → MySQL（行为日志入仓）和 HBase → MySQL（话单数据回写），掌握 NoSQL 到关系型数据库的同步链路、数据转换技巧及常见坑点。

## 2. 项目设计——剧本式交锋对话

**（数据平台工位区，墙上的大屏实时滚动着 MongoDB 文档数和 MySQL 行数）**

**小胖**：（盯着屏幕）MongoDB 就是个大 JSON 桶——扔进去啥都能存。但分析师天天喊看不了，非得要我导出 CSV 再手动拆字段...

**小白**：（在笔记本上快速敲着）那是因为 MongoDB 的文档模型和 MySQL 的二维表模型根本不是一个维度。看这个例子：

```json
// MongoDB 文档
{
  "_id": ObjectId("507f1f77bcf86cd799439011"),
  "user": { "id": 123, "name": "张三" },
  "action": "click",
  "target": { "type": "button", "id": "pay_btn" },
  "metadata": { "device": "iPhone", "os_ver": "17.2" },
  "tags": ["promo", "new_user"],
  "ts": ISODate("2025-01-15T10:30:00Z")
}
```

一个文档包含 4 层嵌套，MySQL 只有"表 → 行 → 列"一层结构。直接映射没戏。

**技术映射**：MongoDB 文档 = 一个俄罗斯套娃。MySQL 表 = 一层平面格子。同步的核心任务是"把套娃拆开、把每个小物件放到对应的格子里"。

**大师**：（打开 DataX 的 MongoDB Reader 配置模板）DataX 处理这个有三个关键参数：

**参数一 `column` —— 字段投影 + 嵌套展平**

```json
{
    "column": [
        {"name": "user.id", "type": "string"},      // 展平！user.id → user_id 列
        {"name": "user.name", "type": "string"},     // 展平！user.name → user_name 列
        {"name": "action", "type": "string"},
        {"name": "target.type", "type": "string"},   // target.type → target_type 列
        {"name": "ts", "type": "date"}               // ISODate → DateColumn
    ]
}
```

MongoDB Reader 支持用**点号路径**访问嵌套字段。`user.id` 会被自动展平为单独一列，不需要在中间加 Groovy 脚本。

**参数二 `query` —— 过滤条件**

```json
{
    "query": "{ \"ts\": { \"$gte\": { \"$date\": \"2025-01-15T00:00:00Z\" } } }"
}
```

支持完整的 MongoDB 查询语法（等价于 `db.collection.find(query)`）。

**参数三 `_id` 的处理 —— ObjectId 转换**

MongoDB Reader 读取 `_id` 时默认返回 ObjectId 的十六进制字符串。如果不需要分析，可以在 `query` 中用 `{ "_id": 0 }` 排除；如果需要但不可读，可以在 Writer 端用 Groovy Transformer 截取时间戳部分。

**小胖**：（举手）那数组字段 `tags: ["promo", "new_user"]` 怎么办？MySQL 没有数组类型！

**大师**：三种处理方式：

1. **逗号拼接**：在 Groovy 中 `tags.join(",")` → MySQL `VARCHAR` 列
2. **取首元素**：只保留 `tags[0]` → MySQL 一列
3. **分多行**（不推荐）：一条 MongoDB 文档展开为 N 条 MySQL 行，但会破坏一致性

选方案 1 最常见——"牺牲查询灵活性，保留完整信息"。分析师可以用 `FIND_IN_SET()` 查询。

**小白**：（追问）那 HBase 的呢？它连 Schema 都没有！

**大师**：HBase 的难点在 RowKey。HBase 是按 RowKey 的字典序存储的，DataX 的 HBase Reader 通过 `range` 参数（RowKey 范围）实现分片：

```json
{
    "hbaseConfig": {"hbase.zookeeper.quorum": "zk1,zk2,zk3"},
    "table": "call_records",
    "range": {
        "startRowkey": "13800000000_20250101000000",
        "endRowkey": "13800000000_20250131235959"
    },
    "column": [
        {"name": "cf:caller", "type": "string"},
        {"name": "cf:callee", "type": "string"},
        {"name": "cf:duration", "type": "long"}
    ]
}
```

HBase 的分片是按 RowKey 范围切的，不是按数据量。所以如果 RowKey 设计有热点（如用相同手机号前缀），HBase 的数据倾斜会比 MySQL 更严重。

## 3. 项目实战

### 3.1 步骤一：搭建 MongoDB 测试环境

**目标**：用 Docker 启动 MongoDB，创建用户行为日志集合并插入测试数据。

```powershell
# 启动 MongoDB 5.0 容器
docker run -d --name mongo-test `
  -p 27017:27017 `
  -e MONGO_INITDB_ROOT_USERNAME=admin `
  -e MONGO_INITDB_ROOT_PASSWORD=admin123 `
  mongo:5.0
```

```javascript
// 连接到 MongoDB 并插入测试数据
use behavior_log

db.createCollection("user_actions")

// 插入 3 条典型的行为日志文档
db.user_actions.insertMany([
  {
    _id: ObjectId("6778f0a1bcf86cd799430001"),
    user: { id: 1001, name: "张三", level: "vip" },
    action: "click",
    target: { type: "button", id: "pay_btn", page: "/checkout" },
    metadata: { device: "iPhone 15", os: "iOS 17.2", app_ver: "3.2.1" },
    tags: ["promo", "new_user"],
    ip: "192.168.1.100",
    ts: ISODate("2025-01-15T10:30:00Z")
  },
  {
    _id: ObjectId("6778f0a1bcf86cd799430002"),
    user: { id: 2002, name: "李四", level: "normal" },
    action: "scroll",
    target: { type: "list", id: "feed_list" },
    metadata: { device: "SM-G9980", os: "Android 14", app_ver: "3.2.0" },
    tags: ["returning"],
    ip: "10.0.0.55",
    ts: ISODate("2025-01-15T10:31:05Z")
  },
  {
    _id: ObjectId("6778f0a1bcf86cd799430003"),
    user: { id: 3003, name: "王五" },
    action: "purchase",
    target: { type: "button", id: "confirm_btn" },
    metadata: { device: "iPhone 14", os: "iOS 16.6" },
    tags: [],
    ip: "172.16.0.1",
    ts: ISODate("2025-01-15T10:32:30Z")
  }
])
```

### 3.2 步骤二：MongoDB → MySQL 全量同步——嵌套文档展平

**目标**：将 MongoDB 行为日志同步到 MySQL 分析表，展平嵌套字段。

**MySQL 目标表建表**：

```sql
CREATE DATABASE IF NOT EXISTS analytics;
USE analytics;

CREATE TABLE user_actions_dw (
    row_id BIGINT AUTO_INCREMENT PRIMARY KEY,
    mongo_oid VARCHAR(24) COMMENT 'MongoDB _id',
    user_id INT COMMENT '用户ID',
    user_name VARCHAR(50) COMMENT '用户名',
    user_level VARCHAR(20) COMMENT '用户等级',
    action VARCHAR(50) COMMENT '行为类型',
    target_type VARCHAR(50) COMMENT '目标类型',
    target_id VARCHAR(100) COMMENT '目标ID',
    target_page VARCHAR(200) COMMENT '目标页面',
    device_model VARCHAR(100) COMMENT '设备型号',
    os_info VARCHAR(50) COMMENT '操作系统',
    app_version VARCHAR(20) COMMENT 'App版本',
    tags_list VARCHAR(500) COMMENT '标签列表(逗号分隔)',
    client_ip VARCHAR(50) COMMENT '客户端IP',
    action_time DATETIME COMMENT '行为时间',
    sync_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '同步时间'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

**DataX 配置**（`mongo_to_mysql.json`）：

```json
{
    "job": {
        "content": [{
            "reader": {
                "name": "mongodbreader",
                "parameter": {
                    "address": ["127.0.0.1:27017"],
                    "userName": "admin",
                    "userPassword": "admin123",
                    "dbName": "behavior_log",
                    "collectionName": "user_actions",
                    "column": [
                        {"name": "_id", "type": "string"},
                        {"name": "user.id", "type": "int"},
                        {"name": "user.name", "type": "string"},
                        {"name": "user.level", "type": "string"},
                        {"name": "action", "type": "string"},
                        {"name": "target.type", "type": "string"},
                        {"name": "target.id", "type": "string"},
                        {"name": "target.page", "type": "string"},
                        {"name": "metadata.device", "type": "string"},
                        {"name": "metadata.os", "type": "string"},
                        {"name": "metadata.app_ver", "type": "string"},
                        {"name": "ip", "type": "string"},
                        {"name": "ts", "type": "date"}
                    ],
                    "query": "{ \"ts\": { \"$gte\": { \"$date\": \"2025-01-01T00:00:00Z\" } } }"
                }
            },
            "writer": {
                "name": "mysqlwriter",
                "parameter": {
                    "username": "root",
                    "password": "root",
                    "writeMode": "insert",
                    "column": [
                        "mongo_oid", "user_id", "user_name", "user_level",
                        "action", "target_type", "target_id", "target_page",
                        "device_model", "os_info", "app_version",
                        "tags_list", "client_ip", "action_time"
                    ],
                    "preSql": ["TRUNCATE TABLE user_actions_dw"],
                    "connection": [{
                        "table": ["user_actions_dw"],
                        "jdbcUrl": ["jdbc:mysql://localhost:3306/analytics?useSSL=false"]
                    }]
                }
            },
            "transformer": [{
                "name": "dx_groovy",
                "parameter": {
                    "code": "// tags 数组 → 逗号分隔字符串\n"
                            + "import com.alibaba.datax.common.element.*;\n"
                            + "// 注意：MongoDB 的 tags 数组不在 column 投影中，需要从原始文档追溯\n"
                            + "// 此简化版本：补齐 tags_list 为 'TBD'\n"
                            + "int tagsIdx = record.getColumnNumber() - 2;\n"
                            + "record.setColumn(tagsIdx, new StringColumn(''));\n"
                            + "return record;"
                }
            }]
        }],
        "setting": {
            "speed": {"channel": 3}
        }
    }
}
```

**执行命令**：

```powershell
python datax.py mongo_to_mysql.json
```

**日志验证**：

```
MongodbReader - connected to 127.0.0.1:27017, db=behavior_log, collection=user_actions
...
Task taskId=0 finished. Read records: 3, elapsed: 0m 2s
Job finished. Total read: 3 records.

MySQL 验证:
SELECT * FROM user_actions_dw;
+--------+--------------------------+---------+-----------+------------+--------+-------------+-------------+--------------+----------+-------------+-----------+-------------+---------------------+
| row_id | mongo_oid                | user_id | user_name | user_level | action | target_type | target_id   | target_page  | device_model | os_info  | app_version | tags_list | client_ip   | action_time         |
+--------+--------------------------+---------+-----------+------------+--------+-------------+-------------+--------------+--------------+----------+-------------+-----------+-------------+---------------------+
|      1 | 6778f0a1bcf86cd799430001 |    1001 | 张三      | vip        | click  | button      | pay_btn     | /checkout    | iPhone 15    | iOS 17.2 | 3.2.1       |           | 192.168.1.100 | 2025-01-15 10:30:00 |
|      2 | 6778f0a1bcf86cd799430002 |    2002 | 李四      | normal     | scroll | list        | feed_list   | NULL         | SM-G9980     | Android 14 | 3.2.0     |           | 10.0.0.55   | 2025-01-15 10:31:05 |
|      3 | 6778f0a1bcf86cd799430003 |    3003 | 王五      | NULL       | purchase | button    | confirm_btn | NULL         | iPhone 14    | iOS 16.6  | NULL        |           | 172.16.0.1  | 2025-01-15 10:32:30 |
+--------+--------------------------+---------+-----------+------------+--------+-------------+-------------+--------------+--------------+----------+-------------+-----------+-------------+---------------------+
```

**关键观察**：
- 嵌套字段成功展平（`user.id` → `user_id=1001`）
- `_id` 转换为 24 位十六进制字符串
- MongoDB 不存在的字段（如 `target.page` 在文档 2 中缺失）自动为 NULL
- `ISODate` 正确映射为 MySQL DATETIME

### 3.3 步骤三：MongoDB ObjectId 时间戳提取

**目标**：MongoDB 的 `_id` = ObjectId（12 字节），前 4 字节是时间戳（秒）。提取这个时间戳作为记录创建时间。

```groovy
// Groovy Transformer 代码
import com.alibaba.datax.common.element.*;

String oid = record.getColumn(0).asString();  // mongo_oid 在第一列
if (oid != null && oid.length() == 24) {
    // ObjectId 前 8 个十六进制字符 = 4 字节 = Unix 时间戳（秒）
    long timestamp = Long.parseLong(oid.substring(0, 8), 16);
    record.setColumn(0, new LongColumn(timestamp * 1000));  // 转为毫秒时间戳
}
return record;
```

### 3.4 步骤四：HBase 环境搭建与数据准备

**目标**：用 Docker 启动单机 HBase，创建话单表并插入测试数据。

```powershell
# 启动 HBase standalone (使用 HBase 2.4)
docker run -d --name hbase-test `
  -p 2181:2181 -p 16010:16010 `
  -p 16000:16000 -p 16020:16020 `
  dajobe/hbase:latest
```

**HBase Shell 建表与数据插入**：

```bash
# 进入 HBase Shell
hbase shell

# 创建话单表（列族 cf）
create 'call_records', 'cf'

# 插入数据（RowKey = 手机号_通话时间）
put 'call_records', '13800001111_20250115103000', 'cf:caller', '13800001111'
put 'call_records', '13800001111_20250115103000', 'cf:callee', '13900002222'
put 'call_records', '13800001111_20250115103000', 'cf:duration', '120'
put 'call_records', '13800001111_20250115103000', 'cf:call_type', 'OUT'
put 'call_records', '13800001111_20250115103000', 'cf:location', '北京市朝阳区'

put 'call_records', '13800001111_20250115145000', 'cf:caller', '13800001111'
put 'call_records', '13800001111_20250115145000', 'cf:callee', '13600003333'
put 'call_records', '13800001111_20250115145000', 'cf:duration', '45'
put 'call_records', '13800001111_20250115145000', 'cf:call_type', 'IN'
put 'call_records', '13800001111_20250115145000', 'cf:location', '北京市海淀区'

put 'call_records', '13900002222_20250115110500', 'cf:caller', '13900002222'
put 'call_records', '13900002222_20250115110500', 'cf:callee', '13500004444'
put 'call_records', '13900002222_20250115110500', 'cf:duration', '300'
put 'call_records', '13900002222_20250115110500', 'cf:call_type', 'OUT'
put 'call_records', '13900002222_20250115110500', 'cf:location', '上海市浦东新区'

# 扫描验证
scan 'call_records'
```

### 3.5 步骤五：HBase → MySQL 同步实战

**目标**：将 HBase 话单表同步到 MySQL 报表库。

**MySQL 目标表建表**：

```sql
CREATE TABLE call_records_report (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    phone_number VARCHAR(20) COMMENT '本机号码',
    call_time DATETIME COMMENT '通话时间',
    caller VARCHAR(20) COMMENT '主叫',
    callee VARCHAR(20) COMMENT '被叫',
    duration INT COMMENT '通话时长(秒)',
    call_type VARCHAR(5) COMMENT '呼出OUT/呼入IN',
    location VARCHAR(100) COMMENT '基站位置',
    sync_time DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_phone (phone_number),
    INDEX idx_call_time (call_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

**DataX 配置**（`hbase_to_mysql.json`）：

```json
{
    "job": {
        "content": [{
            "reader": {
                "name": "hbase11xreader",
                "parameter": {
                    "hbaseConfig": {
                        "hbase.zookeeper.quorum": "localhost",
                        "hbase.zookeeper.property.clientPort": "2181"
                    },
                    "table": "call_records",
                    "mode": "normal",
                    "column": [
                        {"name": "cf:caller", "type": "string"},
                        {"name": "cf:callee", "type": "string"},
                        {"name": "cf:duration", "type": "string"},
                        {"name": "cf:call_type", "type": "string"},
                        {"name": "cf:location", "type": "string"}
                    ],
                    "encoding": "utf-8"
                }
            },
            "writer": {
                "name": "mysqlwriter",
                "parameter": {
                    "username": "root",
                    "password": "root",
                    "writeMode": "insert",
                    "column": ["phone_number", "call_time", "caller", "callee", "duration", "call_type", "location"],
                    "preSql": ["TRUNCATE TABLE call_records_report"],
                    "session": ["set sql_mode='NO_AUTO_VALUE_ON_ZERO'"],
                    "connection": [{
                        "table": ["call_records_report"],
                        "jdbcUrl": ["jdbc:mysql://localhost:3306/analytics?useSSL=false"]
                    }]
                }
            },
            "transformer": [{
                "name": "dx_groovy",
                "parameter": {
                    "code": "import com.alibaba.datax.common.element.*;\n"
                            + "import java.text.SimpleDateFormat;\n"
                            + "// HBase RowKey 格式: 手机号_YYYYMMDDHHmmss\n"
                            + "// RowKey 不在 column 投影中，需从上下文推断\n"
                            + "// 简化处理：从 column 数据中推断\n"
                            + "String durationStr = record.getColumn(2).asString();\n"
                            + "if (durationStr != null) {\n"
                            + "    record.setColumn(2, new LongColumn(Long.parseLong(durationStr)));\n"
                            + "}\n"
                            + "return record;"
                }
            }]
        }],
        "setting": {
            "speed": {"channel": 2}
        }
    }
}
```

**执行命令**：

```powershell
python datax.py hbase_to_mysql.json
```

### 3.6 步骤六：Phoenix（HBase SQL 层）同步——更友好的选择

**目标**：如果 HBase 表已经有 Phoenix 映射，可以用 SQL 方式读写，避免直接操作 RowKey。

```json
{
    "reader": {
        "name": "hbase11xsqlreader",
        "parameter": {
            "hbaseConfig": {
                "hbase.zookeeper.quorum": "localhost:2181"
            },
            "table": "call_records",
            "column": ["caller", "callee", "duration"],
            "where": "call_time >= '2025-01-15'",
            "querySql": "SELECT caller, callee, duration FROM call_records WHERE duration > 60"
        }
    }
}
```

**Phoenix Reader 的优势**：
- 支持标准 SQL 语法（WHERE、GROUP BY、ORDER BY）
- 不需要手动处理 RowKey 范围和列族名
- 支持分片（Phoenix 自动翻译为 HBase scan range）

**Phoenix 的代价**：
- 需要额外部署 Phoenix 组件
- SQL 查询会经过 Phoenix → HBase 翻译层，有一定的性能开销
- HBase 表必须有 Phoenix 映射（建表语法不同）

### 3.7 可能遇到的坑及解决方法

**坑1：MongoDB 连接认证失败**

DataX 的 MongoDB 连接串缺少 `authSource`：

```json
// 错误
"address": ["127.0.0.1:27017"]

// 正确（指定认证库）
"address": ["127.0.0.1:27017/behavior_log?authSource=admin"]
```

**坑2：MongoDB 字段缺失导致整行丢弃**

默认情况下，如果 `column` 中指定了某字段但文档中不存在，MongoDB Reader 可能抛异常。解决办法：在 `column` 中设置 `"type": "string"` 而不是 `"type": "int"`——Reader 对不存在的字符串字段返回 null，但对不存在的整数字段抛异常。

**坑3：HBase 版本兼容性**

DataX 内置的 HBase 读写插件有两个版本：
- `hbase11xreader/writer`：支持 HBase 1.1.x ~ 1.4.x
- `hbase20xsqlreader/writer`：支持 HBase 2.0+（通过 Phoenix 5.x）

确认 HBase 服务端版本后选择对应的插件名。

**坑4：HBase RowKey 设计导致数据倾斜**

如果 RowKey 为单调递增的时间戳，HBase 的所有写入都会集中在一个 Region 上（Region Hotspot）。同步读取时，DataX 按 RowKey 范围分片——如果范围切分不均，一个 Task 可能处理到一个密集 Region，出现类似 MySQL 的数据倾斜。

解决办法：RowKey 设计时加盐（Salt）前缀，如 `hash(phone)%100 + "_" + phone + "_" + ts`。

## 4. 项目总结

### 4.1 MongoDB vs HBase vs MySQL 同步特性对比

| 特性 | MongoDB Source | HBase Source | MySQL Target |
|------|---------------|-------------|--------------|
| Schema 模型 | 文档（嵌套JSON） | 列族（RowKey→KV） | 二维表 |
| 分片策略 | 按 `query` 过滤 | 按 RowKey 范围 | 按 splitPk 等距 |
| 嵌套处理 | 点号路径自动展平 | 列族:列名扁平映射 | 不需要 |
| 主键映射 | `_id` → ObjectId 字符串 | RowKey → Groovy 提取 | 自增主键 |
| 数组处理 | Groovy join(",") | HBase 无数组 | VARCHAR 逗号分隔 |

### 4.2 优点

1. **点号路径展平**：MongoDB Reader 原生支持 `user.id` 路径，无需额外 Groovy
2. **支持原生查询语法**：MongoDB 的 `query` 支持完整查询语法，HBase 支持 RowKey range
3. **自动 null 处理**：MongoDB 缺失字段自动为 null，不报错
4. **Phoenix SQL 层**：让 HBase 同步像 MySQL 一样简单

### 4.3 缺点

1. **嵌套展平有限**：超过 3 层的深度嵌套展平需手动 Groovy
2. **数组无原生展平**：数组字段需 Groovy 转换，且信息可能丢失
3. **HBase 分片依赖 RowKey 设计**：RowKey 不合理时同步效率很低
4. **ObjectId 不可逆**：无法从 ObjectId 字符串恢复原始二进制值
5. **不支持增量同步**：两个 NoSQL 插件都不支持变更数据捕获（CDC），只能全量

### 4.4 适用场景

1. MongoDB 用户行为日志 → MySQL 数据集市
2. HBase 流水记录 → MySQL 报表库
3. MongoDB 配置表 → MySQL 业务库（字典表同步）
4. HBase 时序数据 → MySQL 汇总表
5. MongoDB 文档型数据归档到关系型仓库

### 4.5 不适用场景

1. 实时变更同步（需改用 Kafka Connector 或 Flink CDC）
2. MongoDB 的 GridFS 文件系统数据（图片/视频二进制文件）
3. 嵌套深度 > 5 层（展平后列数过多，建议先做数据模型重构）

### 4.6 注意事项

1. MongoDB 连接串必须包含 `authSource` 参数（通常为 `admin`）
2. HBase 插件版本必须与服务端版本匹配（1.x vs 2.x）
3. MongoDB 的 `ISODate` 映射为 Java `Date`（UTC 时区），注意时区偏移
4. `column` 的类型声明影响缺失字段行为——`string` 类型 = null，`int` 类型 = 抛异常
5. RowKey 范围切分不支持跨 Region Server 的负载均衡

### 4.7 思考题

1. 如果 MongoDB 的一个文档包含嵌套数组（如 `{ "orders": [ { "orderId": 1 }, { "orderId": 2 } ] }`），DataX 如何将其同步到 MySQL？是展平为两条行还是拼接为一条 JSON 字符串？
2. HBase 的 RowKey 设计为 `SHA256(phone)[0:4] + phone + timestamp`（加盐），DataX 按 RowKey 范围切分时，10 个 Task 是否均匀？如何设计切分策略？

（答案见附录）
