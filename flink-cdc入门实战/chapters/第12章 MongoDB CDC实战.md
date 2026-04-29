# 第12章 MongoDB CDC实战

## 1 项目背景

### 业务场景：商品评论系统实时分析

公司电商平台的商品评论系统使用MongoDB存储——因为评论数据结构灵活（不同商品的评论字段不同：有的有图片、有的有标签、有的有追评），MongoDB的文档模型天然适合这种场景。

现在数据团队要构建一个**评论实时分析看板**：
- 按商品ID统计评论总数和平均评分
- 追踪新增评论的实时数量
- 检测负面评论（评分<=2）并实时告警

但这些数据在MongoDB里，传统的关系型数据同步工具无法直接对接（MongoDB没有Binlog）。Flink CDC的MongoDB连接器通过Change Streams解决了这个问题。

### MongoDB CDC架构

```
┌────────────────────────────────┐
│    MongoDB Replica Set         │
│  ┌──────────┐  ┌──────────┐   │
│  │ Primary  │  │Secondary │   │
│  │ Oplog    │  │  ...     │   │
│  └─────┬────┘  └──────────┘   │
│        │                        │
│  ┌─────┴────┐                  │
│  │Change    │  ← Change Stream │
│  │ Stream   │    API (3.6+)    │
│  └─────┬────┘                  │
└────────┼───────────────────────┘
         │ Change Event 流
         ▼
┌────────────────────────────────┐
│  Flink CDC MongoDB Source      │
│  ┌────────────────────────┐    │
│  │ Full Document Enrich   │    │
│  │ UpdateLookup (可选)     │    │
│  └────────────────────────┘    │
│         │                      │
│         ▼                      │
│  DataChangeEvent + Schema      │
└────────────────────────────────┘
```

---

## 2 项目设计 · 三人交锋对话

**小胖**（困惑）：MongoDB没表，只有集合（Collection）。CDC数据怎么映射成行和列？MongoDB的文档有嵌套结构，Flink能处理吗？

**大师**：这是MongoDB CDC最核心的问题。Flink CDC处理MongoDB的数据时，将**每个文档映射为一行数据**。对于嵌套结构，有两种策略：

**策略1——展开（Flatten）**：将嵌套字段展开为平铺的列。比如：
```json
{
  "_id": "123",
  "user": {"name": "Alice", "email": "alice@test.com"},
  "tags": ["phone", "premium"]
}
```
展开后：`_id, user_name, user_email, tags_0, tags_1`

**策略2——JSON序列化**：将嵌套字段整体序列化为JSON字符串：
展开后：`_id, user (JSON字符串), tags (JSON字符串)`

Flink CDC默认使用策略2（JSON字符串），因为策略1存在几个问题：
- 不知道嵌套多深（如果用户文档还有嵌套怎么办？）
- 数组长度不确定（tags可能是0~N个元素）

**小白**：那`_id`字段呢？MongoDB的`_id`是`ObjectId`类型——"5f8d0b3b9d6c7a2b3c4d5e6f"这种24位十六进制字符串。Flink CDC怎么映射？

**大师**：`ObjectId`默认映射为**STRING类型**（24位十六进制字符串）。你也可以选择保留二进制格式，但通常使用STRING更方便。

而且`_id`就是MongoDB CDC的**主键**——类似于MySQL的主键，Flink用它来关联UPDATE/DELETE事件对应的文档。

**小白**：那MongoDB CDC的Change Streams和MySQL的Binlog相比，有什么局限？

**大师**：好问题！MongoDB Change Streams有几个关键限制：

| 对比项 | MySQL Binlog | MongoDB Change Stream |
|-------|-------------|---------------------|
| 历史数据重放 | 可回溯到最早的Binlog位置 | 只保留oplog窗口（通常几小时~几天） |
| DDL捕获 | 完整（CREATE/ALTER/DROP） | 不捕获DDL（如createCollection） |
| 事务支持 | 完整（GTID追踪事务边界） | 4.0+支持多文档事务，但Change Stream不保证事务边界 |
| 并行读取 | FLIP-27支持全量快照并行 | 全量快照串行读取 |
| 监听粒度 | 库级别/表级别 | 集合/库/集群级别 |

**技术映射**：Change Streams像"小区门卫的来访记录"——只记录最近几天的（oplog有窗口期），且不记录小区基础设施变更（DDL）。Binlog像"大楼的安保录像"——保存时间长，且记录所有进出和装修行为（包括DDL）。

---

## 3 项目实战

### 环境准备

**Docker Compose新增MongoDB服务：**
```yaml
mongodb:
  image: mongo:7.0
  container_name: mongodb-cdc
  ports:
    - "27017:27017"
  command:
    - "--replSet"
    - "rs0"
    - "--bind_ip_all"
  environment:
    MONGO_INITDB_DATABASE: shop_reviews
    MONGO_INITDB_ROOT_USERNAME: admin
    MONGO_INITDB_ROOT_PASSWORD: admin123
  volumes:
    - mongo_data:/data/db
  networks:
    - flink-cdc-net
  healthcheck:
    test: echo "rs.status().ok" | mongosh -u admin -p admin123 --quiet
    interval: 15s
```

**初始化副本集：**
```bash
# 进入MongoDB容器初始化副本集
docker exec -it mongodb-cdc mongosh -u admin -p admin123
> rs.initiate({_id: "rs0", members: [{_id: 0, host: "mongodb-cdc:27017"}]})
> rs.status()  # 确认副本集状态为 PRIMARY
```

**创建CDC用户：**
```javascript
use admin;
db.createUser({
    user: "cdc_user",
    pwd: "cdc_pass",
    roles: [
        { role: "read", db: "shop_reviews" },
        { role: "readAnyDatabase", db: "admin" },
        { role: "clusterMonitor", db: "admin" }  // Change Streams需要
    ]
});
```

**Maven依赖：**
```xml
<dependency>
    <groupId>org.apache.flink</groupId>
    <artifactId>flink-connector-mongodb-cdc</artifactId>
    <version>3.0.0</version>
</dependency>
```

### 分步实现

#### 步骤1：准备MongoDB测试数据

```javascript
use shop_reviews;

db.createCollection("reviews");

// 插入文档（带嵌套结构）
db.reviews.insertMany([
    {
        _id: ObjectId("65a1b2c3d4e5f6a7b8c9d0e1"),
        product_id: "P1001",
        user_id: 101,
        rating: 5,
        title: "非常好用！",
        content: "手机性能强劲，拍照效果一流",
        tags: ["好评", "推荐"],
        created_at: new ISODate()
    },
    {
        _id: ObjectId("65a1b2c3d4e5f6a7b8c9d0e2"),
        product_id: "P1001",
        user_id: 102,
        rating: 2,
        title: "一般",
        content: "电池续航不如预期",
        tags: ["差评"],
        created_at: new ISODate()
    }
]);
```

#### 步骤2：编写MongoDB CDC程序

```java
package com.example;

import org.apache.flink.api.common.eventtime.WatermarkStrategy;
import org.apache.flink.cdc.connectors.mongodb.source.MongoDBSource;
import org.apache.flink.cdc.debezium.JsonDebeziumDeserializationSchema;
import org.apache.flink.streaming.api.datastream.DataStreamSource;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;

/**
 * MongoDB CDC读取演示——实时评论分析
 */
public class MongoCdcDemo {

    public static void main(String[] args) throws Exception {
        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        env.enableCheckpointing(5000);

        MongoDBSource<String> source = MongoDBSource.<String>builder()
            .hosts("localhost:27017")
            .databaseList("shop_reviews")          // 监控的数据库
            .collectionList("shop_reviews.reviews") // 监控的集合（格式: db.collection）
            .username("cdc_user")
            .password("cdc_pass")
            .connectionOptions("replicaSet=rs0&authSource=admin")
            .deserializer(new JsonDebeziumDeserializationSchema())
            .startupOptions(
                org.apache.flink.cdc.connectors.mongodb.source.config
                    .StartupOptions.latest())
            .build();

        DataStreamSource<String> mongoStream = env.fromSource(
            source,
            WatermarkStrategy.noWatermarks(),
            "MongoDB CDC Source");

        mongoStream.print();

        env.execute("MongoDB CDC Demo");
    }
}
```

#### 步骤3：观察MongoDB CDC事件格式

在MongoDB中执行变更：
```javascript
// INSERT
db.reviews.insertOne({
    product_id: "P1002",
    user_id: 103,
    rating: 4,
    title: "性价比高",
    content: "屏幕清晰，运行流畅",
    tags: ["推荐"],
    created_at: new ISODate()
});

// UPDATE
db.reviews.updateOne(
    { _id: ObjectId("65a1b2c3d4e5f6a7b8c9d0e1") },
    { $set: { rating: 4, tags: ["好评", "推荐", "已追评"] } }
);

// DELETE
db.reviews.deleteOne({ _id: ObjectId("65a1b2c3d4e5f6a7b8c9d0e2") });
```

**MongoDB CDC事件输出：**

INSERT事件（带嵌套文档）：
```json
{
  "payload": {
    "before": null,
    "after": {
      "_id": "65a1b2c3d4e5f6a7b8c9d0e3",
      "product_id": "P1002",
      "user_id": 103,
      "rating": 4,
      "title": "性价比高",
      "content": "屏幕清晰，运行流畅",
      "tags": "[\"推荐\"]",                     // 数组序列化为JSON字符串
      "created_at": "2024-01-15T10:00:00Z"
    },
    "op": "c",
    "source": {
      "version": "1.9.8.Final",
      "connector": "mongodb",
      "name": "mongodb_connector",
      "db": "shop_reviews",
      "collection": "reviews",
      "snapshot": false,
      "ts_ms": 1714377601000
    }
  }
}
```

**MongoDB CDC与MySQL CDC的事件差异：**
1. `source.connector: "mongodb"` 标识来源
2. `source.collection` 替代 `source.table`
3. `_id` 字段作为隐式主键
4. 嵌套文档（如`tags`）序列化为JSON字符串
5. 没有`before`字段（MongoDB Change Streams的UPDATE不提供更新前镜像，除非配置`fullDocument=updateLookup`）
6. `op`只有`c`(INSERT)、`u`(UPDATE)、`d`(DELETE)，没有`r`(READ/快照)

#### 步骤4：自定义反序列化——处理嵌套文档

```java
package com.example;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.apache.flink.api.common.typeinfo.TypeInformation;
import org.apache.flink.cdc.debezium.DebeziumDeserializationSchema;
import org.apache.flink.util.Collector;
import org.apache.kafka.connect.source.SourceRecord;

/**
 * MongoDB反序列化器——将嵌套文档的tags数组展开为逗号分隔字符串
 */
public class MongoReviewDeserializer implements DebeziumDeserializationSchema<String> {

    private static final ObjectMapper mapper = new ObjectMapper();

    @Override
    public void deserialize(SourceRecord record, Collector<String> out) throws Exception {
        ObjectNode value = (ObjectNode) mapper.readTree(record.value().toString());
        if (value == null) return;

        // 提取after字段
        JsonNode after = value.get("after");
        if (after == null) return;

        // 处理tags数组：JSON数组转为逗号分隔字符串
        JsonNode tagsNode = after.get("tags");
        if (tagsNode != null && tagsNode.isArray()) {
            StringBuilder tagsStr = new StringBuilder();
            for (JsonNode tag : tagsNode) {
                if (tagsStr.length() > 0) tagsStr.append(",");
                tagsStr.append(tag.asText());
            }
            ((ObjectNode) after).put("tags_concat", tagsStr.toString());
        }

        // 提取product_id用于下游分析
        String productId = after.get("product_id").asText();
        int rating = after.get("rating").asInt();

        // 负面评论标记
        boolean isNegative = rating <= 2;

        out.collect(String.format(
            "product=%s, rating=%d, negative=%b, tags_str=%s",
            productId, rating, isNegative,
            after.has("tags_concat") ? after.get("tags_concat").asText() : "[]"
        ));
    }

    @Override
    public TypeInformation<String> getProducedType() {
        return TypeInformation.of(String.class);
    }
}
```

#### 步骤5：实时评论分析——负面评论告警

```java
package com.example;

import org.apache.flink.api.common.functions.MapFunction;
import org.apache.flink.cdc.connectors.mongodb.source.MongoDBSource;
import org.apache.flink.cdc.debezium.JsonDebeziumDeserializationSchema;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;

/**
 * 实时评论分析：统计每个商品的平均评分 + 负面评论告警
 */
public class ReviewAnalysisJob {

    public static void main(String[] args) throws Exception {
        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        env.enableCheckpointing(10000);

        MongoDBSource<String> source = MongoDBSource.<String>builder()
            .hosts("localhost:27017")
            .databaseList("shop_reviews")
            .collectionList("shop_reviews.reviews")
            .username("cdc_user").password("cdc_pass")
            .connectionOptions("replicaSet=rs0&authSource=admin")
            .deserializer(new JsonDebeziumDeserializationSchema())
            .startupOptions(
                org.apache.flink.cdc.connectors.mongodb.source.config
                    .StartupOptions.latest())
            .build();

        DataStream<String> stream = env.fromSource(source,
            org.apache.flink.api.common.eventtime.WatermarkStrategy.noWatermarks(),
            "MongoDB Reviews");

        // 解析评分数据
        DataStream<String> alerts = stream.map(
            (MapFunction<String, String>) json -> {
                if (json.contains("\"op\":\"c\"")
                    && json.contains("\"rating\":")
                    && !json.contains("\"snapshot\":true")) {

                    // 提取rating值
                    int rating = Integer.parseInt(
                        json.replaceAll(".*\"rating\":(\\d+).*", "$1"));
                    String productId = json.replaceAll(
                        ".*\"product_id\":\"([^\"]+)\".*", "$1");

                    if (rating <= 2) {
                        return String.format(
                            "[ALERT] 商品 %s 收到负面评论！评分: %d",
                            productId, rating);
                    }
                }
                return null;
            }
        ).filter(alert -> alert != null);

        alerts.print("NEGATIVE REVIEW>> ");

        env.execute("Review Analysis Job");
    }
}
```

#### 常见陷坑及解决方法

| 问题 | 原因 | 解决方法 |
|------|------|---------|
| `MongoSocketReadException: Prematurely reached end of stream` | Change Stream超时断开 | 设置`heartbeat.interval.ms=5000` |
| `ns missing`事件 | MongoDB的DDL操作（如createIndex）也被捕获 | 设置`change.stream.events.filter=insert,update,replace,delete` |
| UPDATE事件的before为null | Change Stream默认不返回更新前镜像 | 设置`fullDocument=updateLookup`（会额外查询数据库获取完整文档） |
| `_id`为null | ObjectId映射配置不正确 | 确认MongoDB集合中每篇文档都有`_id`字段 |
| 全量快照丢失数据 | 全量快照以非原子方式读取，插入的文档可能丢失 | 使用`snapshotMode=initial`（先全量再增量） |

---

## 4 项目总结

### MySQL vs MongoDB CDC对比

| 维度 | MySQL CDC | MongoDB CDC |
|------|----------|-------------|
| 捕获机制 | Binlog Slave模拟 | Change Streams API |
| 数据模型 | 关系型（行+列） | 文档型（嵌套JSON） |
| 主键 | 显式PRIMARY KEY | `_id`（ObjectId） |
| UPDATE before值 | 完整的前镜像（binlog_row_image=FULL） | 默认没有前镜像（需updateLookup） |
| DDL捕获 | 完整的CREATE/ALTER/DROP | 不捕获DDL |
| 快照并行度 | 支持（FLIP-27 Chunk并行） | 串行（单线程读取） |
| 数据类型复杂度 | 相对简单 | 嵌套文档、数组、地理空间类型 |

### 注意事项

1. **副本集必须**：MongoDB CDC要求数据库运行在副本集模式，单节点不支持Change Streams
2. **oplog大小规划**：Change Streams依赖oplog，推荐设置`oplogSizeMB=10240`（10GB）以上，确保CDC作业停机数小时仍能恢复
3. **避免$project操作**：Change Streams不支持`$project`阶段，所有文档变更都会完整输出
4. **分片集群**：MongoDB分片集群的Change Streams需要特殊配置（`allChangesForCluster`）

### 常见踩坑经验

**故障案例1：MongoDB CDC连接丢失后无法恢复**
- **现象**：网络抖动后Flink CDC报错`CursorNotFound`，作业失败
- **根因**：Change Stream的游标（Cursor）在服务端有`maxAwaitTimeMS`超时限制，超时后服务端关闭游标
- **解决方案**：设置`poll.await.time.ms=1000`（减少每次轮询等待时间）+ 启用Checkpoint自动恢复

**故障案例2：UPDATE事件丢失了部分字段**
- **现象**：MongoDB中执行`$set`只修改了2个字段，但CDC输出的event只包含被修改的字段，缺失了其他字段
- **根因**：Change Stream默认`fullDocument=default`，只输出变更的字段差异，不是完整文档
- **解决方案**：设置`fullDocument=updateLookup`，让Flink CDC在收到Change Event后主动查询一次完整文档

**故障案例3：ObjectId排序问题导致快照数据不一致**
- **现象**：全量快照读取时，通过`_id`排序分页读取，但某些文档没被读到
- **根因**：ObjectId的排序不是严格的插入顺序（因其包含机器码+进程ID+时间戳+计数器）
- **解决方案**：使用`snapshotMode=initial`（全量+增量），且全量阶段使用`_id`范围分页，确保覆盖所有文档

### 思考题

1. **进阶题①**：MongoDB的Change Streams默认不返回UPDATE的"更新前镜像"（before值）。如果业务需要审计"修改前的评分"和"修改后的评分"，应该如何配置？`updateLookup`和`fullDocumentBeforeChange`有什么区别？

2. **进阶题②**：MongoDB的嵌套文档（如`{"address": {"city": "北京", "district": "海淀"}}`）在Flink CDC展开为扁平列（`address_city`, `address_district`）和保持JSON字符串（`address: "{...}"`）两种方案，各有什么优缺点？在数据湖场景（如Iceberg）中哪种更合适？

---

> **下一章预告**：第13章「Checkpoint与状态恢复」——深入Flink的容错机制核心。你将学会如何配置Checkpoint、选择状态后端、实现Savepoint手动恢复，并理解Debezium offset在Flink State中的存储机制。
