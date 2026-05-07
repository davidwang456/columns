# 第35章：非存储类插件扩展——Kafka与HTTP与Pulsar

## 1. 项目背景

某在线教育平台的实时数据管道面临架构升级。平台的核心业务数据流是：用户在 App 上观看课程 → 观看记录实时写入 Kafka Topic `watch_events` → 需要近乎实时地存入 ClickHouse 做多维分析。当前方案是用 Flink 消费 Kafka、解析 JSON、写入 ClickHouse。但运维团队反馈了三个痛点：

1. **Flink 集群维护成本高**：需要 JobManager + TaskManager + Checkpoint 存储，3 台机器空转维护费一个月 5000 元——对于每天仅 5 亿条、单 QPS 约 6 万的小规模场景来说"杀鸡用牛刀"
2. **跨团队协作成本**：Flink 任务的开发语言是 Java/DataStream API，业务分析师不懂，需求变更（如新增一个字段映射）需要排 3 天开发工期
3. **无统一的清洗+同步治理**：Kafka 消息中可能包含脏数据（JSON 格式错误、字段缺失），Flink 的旁路输出需要额外编码，而 DataX 的内置脏数据收集和 errorLimit 可以直接复用

架构组评估后提出：**用 DataX 的非存储类插件（Kafka Reader → DataX Transform → ClickHouse Writer）搭建轻量级"微批"同步管道**。Kafka Reader 定时消费一批消息（如每 30 秒拉取一次），经过 DataX 的 Transformer 做数据清洗，最后批量写入 ClickHouse。虽然延迟从 Flink 的 100ms 变为 30 秒，但对于 T+0 准实时分析来说完全可接受——运维成本从每月 5000 元降到 500 元（DataX 部署在一台已有服务器上）。

本章从 Kafka Reader 的 Consumer API 封装、Kafka Writer 的 Producer 批量发送、HTTP Reader 的分页拉取、以及 Pulsar 的适配思路，系统介绍"MQ 类型数据源的 DataX 插件设计模式"，并完成一个 Kafka → DataX → ClickHouse 的端到端实时管道。

## 2. 项目设计——剧本式交锋对话

**（运维监控室，大屏上显示着 Flink 集群的 GC 频繁告警）**

**小胖**：（心疼地看着服务器账单）3 台 ECS 跑 Flink，一个月 5000 块，每天才处理 500GB 数据——换算下来 1GB 数据 33 块！这性价比也太低了。

**小白**：500GB/天、QPS 5.8 万——这个量级根本不需要 Flink。DataX 的一个 10 channel 的 Kafka Reader Job 就能搞定，延迟控制在 30 秒以内。

**小胖**：DataX 还能连 Kafka？它不是只能读 MySQL/HDFS 这些"存着不动"的数据吗？

**大师**：这正是本章要讲的核心概念——**DataX 的数据源不只有"存储类"（MySQL、HDFS），还有"消息队列类"（Kafka、Pulsar、HTTP）**。两者的本质区别在于数据的"存在周期"：

| 特性 | 存储类数据源 | MQ 类数据源 |
|------|-----------|-----------|
| 数据存在周期 | 持久（数据一直在） | 短暂（消费即消失/归档） |
| 读取模型 | 主动拉取（select） | 被动接收（poll/subscribe） |
| 偏移量管理 | 无（SQL WHERE 定范围） | 有（offset/cursor 需持久化） |
| 分片策略 | splitPk 等距切分 | partition 分配 |
| 典型代表 | MySQL/PG/HDFS/MongoDB | Kafka/Pulsar/RabbitMQ |

**Kafka Reader 的设计核心**：

```
┌─────────┐    poll(batch)    ┌──────────┐    send(record)    ┌──────────┐
│  Kafka  │ ────────────────▶ │  Kafka   │ ────────────────▶  │  Channel │
│  Topic  │                   │  Reader  │                    │  (Queue) │
└─────────┘                   └──────────┘                    └──────────┘
              JSON 消息 → 解析为 Record
              每个 Task 消费一个 Partition
```

关键设计决策：

**1. Partition 分配 = Reader 的 split 策略**

Kafka Topic 有 16 个 Partition → DataX 的 `Job.split(adviceNumber)` 返回 16 个 Task 配置（每个配置绑定一个 partitionId），16 个 Task 各自独立消费一个 Partition。

**2. offset 管理 → at-least-once 语义**

Kafka Reader 在 `Task.startRead()` 中循环 `consumer.poll(Duration.ofMillis(1000))`。如果 Task 失败（如 JVM 崩溃），已发送到 Channel 但未提交 offset 的消息会重复消费。Writer 端通过幂等写入（`INSERT ... ON DUPLICATE KEY`）兜底。

**3. 消费模式 → 微批而非流式**

Kafka Reader 不是无限循环消费——而是消费到一个"时间窗口"或"条数上限"后就停止。比如消费 1 分钟内的消息或 10 万条——这是 DataX 的 Task 生命周期决定的（Task 有 start 和 end）。

**技术映射**：存储类 Reader = 去图书馆借书——书一直在书架上（数据持久），每次借指定书目（SQL WHERE）。MQ 类 Reader = 订阅报纸——每天的新报纸在报箱里（Topic），取走就没了（消费偏移前进），如果忘记取（offset 未提交）报纸会堆在报箱里。

**小胖**：（挠头）那 Kafka Writer 怎么写？往 Kafka 里写数据也是"微批"模式？

**大师**：Kafka Writer 的典型模式：

```java
@Override
public void startWrite(RecordReceiver receiver) {
    List<ProducerRecord<String, String>> batch = new ArrayList<>();
    Record record;
    
    while ((record = receiver.getFromReader()) != null) {
        String json = recordToJson(record);              // Record → JSON
        String key = extractKey(record, keyColumn);      // 提取 key（用于分区）
        batch.add(new ProducerRecord<>(topic, key, json));
        
        if (batch.size() >= batchSize) {
            // 批量发送 + flush
            for (ProducerRecord<String, String> pr : batch) {
                producer.send(pr, callback);  // 异步发送 + 回调
            }
            producer.flush();      // 强制 flush，确保数据到达 broker
            batch.clear();
        }
    }
    // 兜底 flush
    flushRemaining(batch);
}
```

三个关键点：
1. **`producer.send()` 是异步的**——不用等每条消息确认，靠 `producer.flush()` 批量等结果
2. **callback 处理失败**——失败的 Record 收集到脏数据
3. **key 字段影响分区**——不设 key 就是 round-robin 均匀分布，设 key 可以实现"同一用户的消息进入同一 Partition"

**小白**：（追问）那 HTTP Reader 跟上次我们写的 `httpapireader` 有什么不同？

**大师**：第 32 章的 `httpapireader` 是"存储类 HTTP Reader"——API 返回的是全量数据集（分页遍历）。但还有一种"MQ 类 HTTP Reader"——对接第三方实时推送的 Webhook 或 SSE（Server-Sent Events）流。这种 Reader 的 `split` 策略不是分页，而是"按时间段分割"——比如过去 1 小时的数据按 5 分钟一段切成 12 个 Task，每个 Task 拉取对应 5 分钟的 API 数据。

**小白**：（追问）那 Pulsar 呢？它跟 Kafka 的设计差异会影响 Reader 实现吗？

**大师**：Pulsar 的核心差异是**计算与存储分离**——Broker 无状态、BookKeeper 持久存储。这对 Reader 来说有一个大优势：Pulsar 原生支持 `reader` 接口（按 offset 精确拉取——不像 Kafka 必须 group subscribe）。更适合微批场景：

```java
// Pulsar Reader 按位置精确拉取
PulsarClient client = PulsarClient.builder().serviceUrl(url).build();
Reader<byte[]> reader = client.newReader()
    .topic(topic)
    .startMessageId(startOffset)
    .create();

while (reader.hasMessageAvailable()) {
    Message<byte[]> msg = reader.readNext();
    // 解析 → Record → sendToChannel
}
```

不需要 Consumer Group、不需要 offset commit——Reader 语义天然适合 DataX 的"拉一批、处理一批、结束"的模式。

## 3. 项目实战

### 3.1 步骤一：搭建 Kafka 测试环境

**目标**：用 Docker Compose 启动 Kafka 集群，创建测试 Topic。

```powershell
New-Item -ItemType Directory -Path "kafka-test" -Force
```

**`docker-compose.yml`**：

```yaml
version: '3'
services:
  zookeeper:
    image: confluentinc/cp-zookeeper:7.5.0
    environment:
      ZOOKEEPER_CLIENT_PORT: 2181
    ports:
      - "2181:2181"

  kafka:
    image: confluentinc/cp-kafka:7.5.0
    depends_on:
      - zookeeper
    ports:
      - "9092:9092"
    environment:
      KAFKA_BROKER_ID: 1
      KAFKA_ZOOKEEPER_CONNECT: zookeeper:2181
      KAFKA_ADVERTISED_LISTENERS: PLAINTEXT://localhost:9092
      KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR: 1
```

```powershell
# 启动 Kafka
docker-compose up -d

# 创建 Topic（16 分区）
docker exec kafka-test-kafka-1 kafka-topics --create `
  --topic watch_events `
  --partitions 16 `
  --replication-factor 1 `
  --bootstrap-server localhost:9092

# 验证 Topic 创建
docker exec kafka-test-kafka-1 kafka-topics --describe `
  --topic watch_events `
  --bootstrap-server localhost:9092
```

**模拟 Kafka 生产者（灌入测试数据）**：

```python
# produce_test_data.py — 模拟 100 万条观看事件
from kafka import KafkaProducer
import json, time, random, uuid

producer = KafkaProducer(
    bootstrap_servers='localhost:9092',
    value_serializer=lambda v: json.dumps(v).encode('utf-8')
)

courses = ['math_101', 'english_201', 'history_301', 'cs_401', 'art_501']
actions = ['play', 'pause', 'seek', 'complete']

for i in range(1, 1000001):
    event = {
        "event_id": str(uuid.uuid4()),
        "user_id": random.randint(1, 500000),
        "course_id": random.choice(courses),
        "action": random.choice(actions),
        "progress": round(random.random() * 100, 2),
        "duration": random.randint(10, 3600),
        "device": random.choice(['iOS', 'Android', 'Web', 'iPad']),
        "ip": f"192.168.{random.randint(1,255)}.{random.randint(1,255)}",
        "timestamp": int(time.time() * 1000)
    }
    producer.send('watch_events', value=event)
    if i % 10000 == 0:
        producer.flush()
        print(f"Produced {i} messages...")

producer.flush()
producer.close()
print("Done: 1,000,000 messages produced to watch_events")
```

### 3.2 步骤二：Kafka Reader 插件开发（核心实现）

**目标**：实现 `kafkareader`，消费 Kafka Topic 的 JSON 消息并转换为 DataX Record。

**`KafkaReader.java`——Job 内部类**：

```java
package com.example.datax.plugin.reader.kafka;

import com.alibaba.datax.common.spi.Reader;
import com.alibaba.datax.common.util.Configuration;
import org.apache.kafka.clients.consumer.KafkaConsumer;
import org.apache.kafka.common.PartitionInfo;
import org.apache.kafka.common.TopicPartition;
import java.util.*;

public class KafkaReader extends Reader {

    public static class Job extends Reader.Job {
        private Configuration originalConfig;

        @Override
        public void init() {
            this.originalConfig = super.getPluginJobConf();
            String bootstrapServers = originalConfig.getString("bootstrapServers");
            String topic = originalConfig.getString("topic");
            
            if (bootstrapServers == null || topic == null) {
                throw new RuntimeException("bootstrapServers and topic are required");
            }
        }

        @Override
        public List<Configuration> split(int adviceNumber) {
            String bootstrapServers = originalConfig.getString("bootstrapServers");
            String topic = originalConfig.getString("topic");
            String groupId = originalConfig.getString("groupId", "datax-kafka-reader");

            // 获取 Topic 的 Partition 列表
            Properties props = new Properties();
            props.put("bootstrap.servers", bootstrapServers);
            props.put("key.deserializer", 
                "org.apache.kafka.common.serialization.StringDeserializer");
            props.put("value.deserializer",
                "org.apache.kafka.common.serialization.StringDeserializer");

            KafkaConsumer<String, String> tempConsumer = new KafkaConsumer<>(props);
            List<PartitionInfo> partitions = tempConsumer.partitionsFor(topic);
            tempConsumer.close();

            // 每个 Partition 分配一个 Task
            int actualTaskCount = Math.min(adviceNumber, partitions.size());
            List<Configuration> taskConfigs = new ArrayList<>();

            for (int i = 0; i < actualTaskCount; i++) {
                Configuration taskConfig = this.originalConfig.clone();
                taskConfig.set("partitionId", partitions.get(i).partition());
                taskConfig.set("taskIndex", i);
                taskConfigs.add(taskConfig);
            }

            return taskConfigs;
        }
    }
```

**`KafkaReader.java`——Task 内部类**：

```java
    public static class Task extends Reader.Task {
        private KafkaConsumer<String, String> consumer;
        private int partitionId;
        private int batchSize;
        private long maxRecords;
        private List<Configuration> columnMetas;

        @Override
        public void init() {
            Configuration config = super.getPluginJobConf();
            this.partitionId = config.getInt("partitionId");
            this.batchSize = config.getInt("batchSize", 1000);
            this.maxRecords = config.getLong("maxRecords", 100000);
            this.columnMetas = config.getListConfiguration("column");

            Properties props = new Properties();
            props.put("bootstrap.servers", config.getString("bootstrapServers"));
            props.put("group.id", config.getString("groupId", "datax-kafka-reader"));
            props.put("enable.auto.commit", "false");
            props.put("key.deserializer",
                "org.apache.kafka.common.serialization.StringDeserializer");
            props.put("value.deserializer",
                "org.apache.kafka.common.serialization.StringDeserializer");
            props.put("max.poll.records", String.valueOf(batchSize));

            this.consumer = new KafkaConsumer<>(props);
            // 只消费指定的 Partition
            TopicPartition tp = new TopicPartition(
                config.getString("topic"), partitionId);
            this.consumer.assign(Collections.singletonList(tp));

            // 从配置的 offset 开始消费（默认 latest）
            String offsetPolicy = config.getString("offsetPolicy", "latest");
            if ("earliest".equals(offsetPolicy)) {
                this.consumer.seekToBeginning(Collections.singletonList(tp));
            } else if ("none".equals(offsetPolicy)) {
                // 保持当前位置
            } else {
                // 可以支持自定义 offset
                long customOffset = config.getLong("startOffset", -1);
                if (customOffset >= 0) {
                    this.consumer.seek(tp, customOffset);
                }
            }
        }

        @Override
        public void startRead(RecordSender recordSender) {
            long totalRead = 0;
            long emptyPolls = 0;
            int maxEmptyPolls = 5;  // 连续 5 次拉不到数据就退出

            while (totalRead < maxRecords && emptyPolls < maxEmptyPolls) {
                var records = consumer.poll(Duration.ofMillis(1000));

                if (records.isEmpty()) {
                    emptyPolls++;
                    continue;
                }
                emptyPolls = 0;

                for (var kafkaRecord : records) {
                    try {
                        String jsonValue = kafkaRecord.value();
                        JSONObject jsonObj = JSON.parseObject(jsonValue);

                        Record dataxRecord = recordSender.createRecord();
                        for (Configuration colMeta : columnMetas) {
                            String colName = colMeta.getString("name");
                            String colType = colMeta.getString("type", "string");
                            dataxRecord.addColumn(
                                KafkaUtil.createColumn(jsonObj, colName, colType));
                        }
                        // 可选：追加 kafka 元数据列（offset、partition、timestamp）
                        dataxRecord.addColumn(new LongColumn(kafkaRecord.offset()));
                        dataxRecord.addColumn(new LongColumn(kafkaRecord.timestamp()));

                        recordSender.sendToWriter(dataxRecord);
                        totalRead++;
                    } catch (Exception e) {
                        // 格式错误的 JSON → 脏数据
                        Record dirtyRecord = recordSender.createRecord();
                        dirtyRecord.addColumn(
                            new StringColumn(kafkaRecord.value()));
                        dirtyRecord.addColumn(
                            new StringColumn("JSON_PARSE_ERROR:" + e.getMessage()));
                        recordSender.sendToWriter(dirtyRecord);
                    }
                }

                if (totalRead % 10000 == 0) {
                    getTaskPluginCollector().notify(totalRead + " records");
                }
            }

            consumer.close();
            LOG.info("Kafka Task[partition={}] finished: read {} records",
                partitionId, totalRead);
        }
    }
}
```

### 3.3 步骤三：Kafka → ClickHouse 端到端配置

**目标**：配置 DataX Job，Kafka 消费观看事件 → Transformer 清洗 → ClickHouse 写入。

**ClickHouse 目标表建表**：

```sql
CREATE TABLE watch_events_dw (
    event_id    String,
    user_id     UInt32,
    course_id   String,
    action      String,
    progress    Float32,
    duration    UInt32,
    device      String,
    client_ip   String,
    event_time  DateTime,
    kafka_offset UInt64,
    kafka_ts    UInt64,
    sync_date   Date DEFAULT toDate(event_time)
) ENGINE = MergeTree()
PARTITION BY toYYYYMMDD(event_time)
ORDER BY (course_id, user_id, event_time)
SETTINGS index_granularity = 8192;
```

**DataX Job 配置**（`kafka_to_clickhouse.json`）：

```json
{
    "job": {
        "content": [{
            "reader": {
                "name": "kafkareader",
                "parameter": {
                    "bootstrapServers": "localhost:9092",
                    "topic": "watch_events",
                    "groupId": "datax-watch-pipeline",
                    "offsetPolicy": "latest",
                    "batchSize": 2000,
                    "maxRecords": 500000,
                    "column": [
                        {"name": "event_id", "type": "string"},
                        {"name": "user_id", "type": "long"},
                        {"name": "course_id", "type": "string"},
                        {"name": "action", "type": "string"},
                        {"name": "progress", "type": "double"},
                        {"name": "duration", "type": "long"},
                        {"name": "device", "type": "string"},
                        {"name": "ip", "type": "string"},
                        {"name": "timestamp", "type": "date"}
                    ]
                }
            },
            "writer": {
                "name": "clickhousewriter",
                "parameter": {
                    "username": "default",
                    "password": "",
                    "column": ["event_id", "user_id", "course_id", "action", "progress", "duration", "device", "client_ip", "event_time", "kafka_offset", "kafka_ts"],
                    "preSql": [],
                    "batchSize": 8192,
                    "connection": [{
                        "table": ["watch_events_dw"],
                        "jdbcUrl": ["jdbc:clickhouse://localhost:8123/analytics"]
                    }]
                }
            },
            "transformer": [{
                "name": "dx_groovy",
                "parameter": {
                    "code": "import com.alibaba.datax.common.element.*;\n"
                            + "// timestamp 毫秒 → DateTime 秒\n"
                            + "Long tsMs = record.getColumn(8).asLong();\n"
                            + "if (tsMs != null) {\n"
                            + "    record.setColumn(8, new DateColumn(tsMs / 1000));\n"
                            + "}\n"
                            + "// IP 字段名修正（reader 的 'ip' → writer 的 'client_ip'）\n"
                            + "String ip = record.getColumn(7).asString();\n"
                            + "record.setColumn(7, new StringColumn(ip));\n"
                            + "return record;"
                }
            }]
        }],
        "setting": {
            "speed": {"channel": 8},
            "errorLimit": {"record": 500, "percentage": 0.1}
        }
    }
}
```

**执行命令**：

```powershell
python datax.py jobs/kafka_to_clickhouse.json

# 日志关键行:
# KafkaReader Task[partition=0] init: topic=watch_events, partition=0
# KafkaReader Task[partition=0] startRead: consuming from latest offset
# Kafka Task[partition=0] finished: read 31250 records
# ClickHouseWriter Task finished: written 500000, errors 0
```

### 3.4 步骤四：Kafka Writer 插件开发（核心实现）

**目标**：实现 `kafkawriter`，将 DataX Record 批量写入 Kafka Topic。

**`KafkaWriter.java`——Task 内部类核心**：

```java
public static class Task extends Writer.Task {
    private KafkaProducer<String, String> producer;
    private String topic;
    private int batchSize;
    private String keyColumn;

    @Override
    public void init() {
        Configuration config = super.getPluginJobConf();
        this.topic = config.getString("topic");
        this.batchSize = config.getInt("batchSize", 1000);
        this.keyColumn = config.getString("keyColumn");

        Properties props = new Properties();
        props.put("bootstrap.servers", config.getString("bootstrapServers"));
        props.put("key.serializer",
            "org.apache.kafka.common.serialization.StringSerializer");
        props.put("value.serializer",
            "org.apache.kafka.common.serialization.StringSerializer");
        props.put("acks", config.getString("acks", "1"));
        props.put("retries", config.getInt("retries", 3));
        props.put("batch.size", config.getInt("producerBatchSize", 16384));
        props.put("linger.ms", config.getInt("lingerMs", 10));
        props.put("compression.type", config.getString("compression", "snappy"));

        this.producer = new KafkaProducer<>(props);
    }

    @Override
    public void startWrite(RecordReceiver receiver) {
        List<ProducerRecord<String, String>> batch = new ArrayList<>(batchSize);
        Record record;

        while ((record = receiver.getFromReader()) != null) {
            try {
                String json = recordToJson(record);
                String key = (keyColumn != null) 
                    ? record.getColumn(keyIndex).asString() 
                    : null;

                ProducerRecord<String, String> pr = 
                    new ProducerRecord<>(topic, key, json);
                batch.add(pr);

                if (batch.size() >= batchSize) {
                    flushBatch(batch);
                    batch.clear();
                }
            } catch (Exception e) {
                getTaskPluginCollector().collectDirtyRecord(
                    record, "Serialization failed: " + e.getMessage());
            }
        }

        if (!batch.isEmpty()) {
            flushBatch(batch);
        }
    }

    private void flushBatch(List<ProducerRecord<String, String>> batch) {
        List<Record> dirtyRecords = new ArrayList<>();

        for (ProducerRecord<String, String> pr : batch) {
            producer.send(pr, (metadata, exception) -> {
                if (exception != null) {
                    // 异步回调中收集失败记录
                    synchronized (dirtyRecords) {
                        dirtyRecords.add(linkedRecord);
                    }
                }
            });
        }

        // 强制 flush：等待所有异步发送完成
        producer.flush();

        // 失败的 Record 归集到脏数据
        for (Record dirty : dirtyRecords) {
            getTaskPluginCollector().collectDirtyRecord(
                dirty, "Kafka send failed after flush");
        }
    }
}
```

### 3.5 步骤五：Pulsar Reader 适配思路

**目标**：展示 Pulsar Reader 与 Kafka Reader 的核心差异——Reader 接口替代 Consumer Group。

```java
// Pulsar Reader 的 startRead——基于 Reader 接口而非 Consumer Group
@Override
public void startRead(RecordSender recordSender) {
    // Pulsar Reader 按 offset 精确拉取，不需要 group subscribe
    PulsarClient client = PulsarClient.builder()
        .serviceUrl(pulsarUrl)
        .build();

    // 每个 Task 消费一个 Partition 的指定 offset 范围
    Reader<byte[]> reader = client.newReader()
        .topic(topic)
        .startMessageId(MessageId.fromByteArray(startOffset))
        .readCompacted(true)
        .create();

    long totalRead = 0;
    while (totalRead < maxRecords && reader.hasMessageAvailable()) {
        Message<byte[]> msg = reader.readNext(1000, TimeUnit.MILLISECONDS);
        if (msg == null) break;

        String jsonValue = new String(msg.getData(), StandardCharsets.UTF_8);
        Record dataxRecord = recordSender.createRecord();
        // ... JSON → Record 转换逻辑 ...
        recordSender.sendToWriter(dataxRecord);
        totalRead++;
    }

    reader.close();
    client.close();
}
```

**Pulsar 相比 Kafka 的优势**（对 DataX 插件开发者）：
1. **Reader 接口天生适合微批**：不需要 Consumer Group、不需要 offset commit
2. **Topic 无需预分区**：Pulsar 的 Partition 是逻辑分片，可以动态扩缩
3. **消息 TTL**：Pulsar 支持自动过期，天然避免 Topic 堆积
4. **Schema Registry**：原生支持 JSON/AVRO Schema，Reader 可自动反序列化

### 3.6 可能遇到的坑及解决方法

**坑1：Kafka Consumer Group offset 提交与 DataX 批处理不匹配**

DataX 的 Task `startRead()` 结束后 Consumer close，如果没有显式 `commitSync()`，offset 不会提交——下次启动时重复消费。

```
报错: [无报错] 但重复消费了大量数据
解决: 在 startRead() 结束前调用 consumer.commitSync()
     但注意：commitSync 意味着 at-least-once，
     如果 Writer 写了一半数据但 offset 已提交 → 数据丢失
     建议：offset 提交放在 Task 的 post() 中，等 Writer 确认完成后再提交
```

**坑2：Kafka Writer 的 `producer.flush()` 阻塞导致 Channel 满**

如果 `producer.flush()` 等待 broker 确认期间耗时较长（如网络延迟 500ms），而 Channel 的容量有限（默认 128），Reader 端 `sendToWriter()` 被阻塞 → 整个 Task 管道卡顿。

```
解决:
1. 增大 Channel capacity: setting.speed.channel 对数增加或调整 MemoryChannel 容量
2. 将 flush 拆分为小批量：每 200 条 flush 一次而不是攒满 8000 条
3. 使用 producer.send() 的回调异步处理结果
```

**坑3：Kafka 消费到的 JSON 中包含字段类型不匹配**

Kafka 消息中的 `user_id` 是字符串 `"123"` 但 column 配置为 `"type": "long"`。

```
解决: 在 createColumn() 方法中用 try-catch 包裹类型转换：
     try {
         return new LongColumn(Long.parseLong(value.toString()));
     } catch (NumberFormatException e) {
         // 降级为字符串或标记为脏数据
         return new StringColumn(value.toString());
     }
```

**坑4：Pulsar Reader 的 Topic 名称与 Admin API 不兼容**

Pulsar 的 `reader` 接口要求 topic 全名（`persistent://public/default/my-topic`）而不是短名。

```
报错: Topic not found: my-topic
解决: 使用完整 topic 名称 persistent://{tenant}/{namespace}/{topic}
     或在插件内部自动补前缀
```

## 4. 项目总结

### 4.1 MQ 类插件设计模式总结

```java
// 统一设计模式：所有 MQ 类 Reader 都遵循此模式
public class GenericMqReader extends Reader {
    public class Job extends Reader.Job {
        // 1. 获取 Partition 列表
        // 2. 将每个 Partition 分配给一个 Task
        // 3. 返回 List<Configuration>（每个包含 partitionId + offset）
    }
    public class Task extends Reader.Task {
        // 1. init(): 创建 Consumer/Reader 实例，seek 到指定 offset
        // 2. startRead(): while(未超过限制) { poll/receive → 解析 → send }
        // 3. post(): commit offset
    }
}
```

### 4.2 Kafka vs Pulsar vs HTTP 三种 MQ 类 Reader 对比

| 特性 | Kafka Reader | Pulsar Reader | HTTP Reader (MQ型) |
|------|-------------|---------------|-------------------|
| 消费模型 | ConsumerGroup + poll() | Reader + readNext() | HTTP GET + pagination |
| offset 管理 | commitSync（手动） | MessageId（持久） | 时间窗口（URL 参数） |
| 数据划窗 | maxRecords + emptyPolls | maxRecords + hasMessageAvailable | 时间段切片 |
| 适用延迟 | 10~60 秒 | 10~60 秒 | 30~300 秒 |
| 部署复杂度 | 需要 Kafka Cluster | 需要 Pulsar Cluster | 仅需 HTTP Endpoint |
| 数据一致性 | at-least-once | at-least-once | exactly-once（幂等 API） |

### 4.3 优点

1. **运维成本低**：替换 Flink 集群后月成本从 5000 元降到 500 元
2. **配置驱动**：新增同步链路只需增一个 JSON 配置，不写 Java 代码
3. **复用 DataX 治理能力**：脏数据收集、限速、监控、告警自动继承
4. **at-least-once + 幂等写入**：通过 Writer 端的 `ON DUPLICATE KEY` 保证最终一致性
5. **Partition 级并发**：每个 Task 消费一个 Partition，天然支持并发扩展

### 4.4 缺点

1. **微批延迟不可控**：30 秒的调度频率不是实时（< 1 秒）
2. **offset 管理需自研**：DataX 无内置 offset 存储，需自行设计持久化方案
3. **大消息可能 OOM**：如果 Kafka 单条消息 10MB，Record 对象化后内存占用倍增
4. **Consumer Group rebalance**：新增/删除 Task 时可能触发短暂停顿

### 4.5 适用场景

1. 视频播放记录 → ClickHouse 实时分析（本章场景）
2. IoT 传感器数据 → 时序数据库（InfluxDB/TDengine）
3. 微服务日志 → Elasticsearch（ELK 替代方案）
4. 第三方 Webhook 推送 → 数据仓库
5. 消息队列跨集群同步（Kafka → Pulsar / Kafka → Kafka）

### 4.6 不适用场景

1. 毫秒级实时风控（延迟要求 < 100ms）——继续用 Flink
2. 需要 exactly-once 语义的金融交易——DataX 的 at-least-once 无法保证
3. 消息量超过单机吞吐上限（> 100 万 QPS）——需考虑集群化部署

### 4.7 思考题

1. Kafka Reader 的 offset 提交应该在 `startRead()` 结束前（每 poll 一次就 commit）还是在 `post()` 中统一提交？这两种方式的"数据丢失"和"重复消费"风险分别是什么？

2. 如果 Kafka Topic 有 64 个 Partition 但 DataX Job 的 channel 只设为 16——多余的 48 个 Partition 会被分配吗？如何设计"一个 Task 消费多个 Partition"的分配策略？

（答案见附录）
