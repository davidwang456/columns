# 第36章：自定义 Connector 开发实战——以 Redis Connector 为例

## 1. 项目背景

"我们的实时推荐系统用 Redis 做用户画像缓存。每次业务代码执行 `SET user:1001 {...}` 更新画像后，需要实时通知下游的推荐模型服务重新计算推荐结果。目前的做法是在业务代码里 Redis SET 完之后再调一次 Kafka Producer API——但这带来了两个问题：一是 Redis 写入成功但 Kafka 发送失败（分布式事务的不一致）；二是每个业务团队都要在自己代码里写'双写'逻辑，重复且容易遗漏。"

理想的方案是——像 Debezium 监控 MySQL binlog 一样监控 Redis 的写操作。当 Redis 执行 SET/DEL/HSET 等写命令时，自动产生一条 Change Event 写入 Kafka。虽然 Debezium 官方没有 Redis Source Connector，但 Kafka Connect 的 `SourceConnector + SourceTask` 两大接口为一切"能产生变更事件的数据源"提供了统一的框架——无论是数据库 binlog、Redis Keyspace Notifications、还是文件系统的变更。

本章将从零开发一个最小可用的 Redis Source Connector——使用 Redis 的 **Keyspace Notifications**（键空间通知）机制订阅写操作，通过 Kafka Connect 的 SourceTask 接口将变更事件写入 Kafka Topic。完整覆盖 Connector 开发的两大接口 → Maven 打包 → 部署 → 验证。

## 2. 项目设计——三人对话

**小胖**："大师，我看了 SourceConnector 和 SourceTask 两个接口，但不太明白它们的分工。为什么需要两个类？一个类搞定所有事情不行吗？"

**大师**："这是 Kafka Connect 的'管理-执行分离'设计——SourceConnector 是**配置解析和任务分配**的入口（管理面），SourceTask 是**实际拉取数据**的执行者（执行面）。比如你配置了 `tasks.max=3`，Connector 类负责把配置拆成 3 份（`taskConfigs(3)` 返回 3 个 Map），每个 Map 传给一个 Task 实例——3 个 Task 并行拉取数据。"

**小白**："那 `poll()` 方法为什么返回 `List<SourceRecord>` 而不是一条一条返回？"

**大师**："这是 Kafka Connect 框架的批处理设计——框架每隔 `poll.interval.ms`（默认 500ms）调用一次 `poll()`，每次期望拿到一批 SourceRecord。框架把这批 Record 打包成 `ProducerRecord` 批量发送到 Kafka，减少网络请求。如果你的数据源没有事件（比如 Redis 当前没有写操作），`poll()` 返回空列表即可——框架会等待下一个周期再调。"

**小胖**："Redis 的 Keyspace Notifications 是什么？怎么开启？"

**大师**："Keyspace Notifications 是 Redis 的 Pub/Sub 机制——当某个 key 发生变化时（SET/DEL/EXPIRE 等），Redis 会自动向 `__keyspace@<db>__:<key>` 频道发布一条消息。要在生产环境开启，需要配置 `notify-keyspace-events KEA`，注意这个配置在 redis.conf 中默认是关闭的。" 
- K = Keyspace events
- E = Keyevent events  
- A = 全部事件（相当于是 `g$lshzxe` 的组合）

## 3. 项目实战

### 步骤1：Maven 项目结构和依赖

```xml
<dependencies>
    <dependency>
        <groupId>org.apache.kafka</groupId>
        <artifactId>connect-api</artifactId>
        <version>3.6.0</version>
        <scope>provided</scope>
    </dependency>
    <dependency>
        <groupId>redis.clients</groupId>
        <artifactId>jedis</artifactId>
        <version>5.1.0</version>
    </dependency>
</dependencies>
```

### 步骤2：SourceConnector 实现（管理面）

```java
package com.example.debezium.redis;

import org.apache.kafka.common.config.ConfigDef;
import org.apache.kafka.connect.source.SourceConnector;
import java.util.*;

public class RedisSourceConnector extends SourceConnector {
    private Map<String, String> configProps;

    @Override
    public void start(Map<String, String> props) {
        this.configProps = Collections.unmodifiableMap(new HashMap<>(props));
    }

    @Override
    public Class<RedisSourceTask> taskClass() {
        return RedisSourceTask.class;
    }

    @Override
    public List<Map<String, String>> taskConfigs(int maxTasks) {
        // Redis Keyspace Notifications 是全局广播 → 多 Task 会重复消费
        // 因此强制只分配 1 个 Task
        if (maxTasks > 1) {
            LOGGER.warn("Redis Connector supports only 1 task due to global Keyspace Notifications. maxTasks={}", maxTasks);
        }
        return Collections.singletonList(configProps);
    }

    @Override
    public ConfigDef config() {
        return RedisConnectorConfig.configDef();
    }

    @Override
    public String version() {
        return "1.0.0";
    }

    @Override
    public void stop() {}
}
```

### 步骤3：SourceTask 实现（执行面核心）

```java
package com.example.debezium.redis;

import org.apache.kafka.connect.source.SourceRecord;
import org.apache.kafka.connect.source.SourceTask;
import org.apache.kafka.connect.data.Schema;
import org.apache.kafka.connect.data.SchemaBuilder;
import org.apache.kafka.connect.data.Struct;
import redis.clients.jedis.Jedis;
import redis.clients.jedis.JedisPubSub;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.*;
import java.util.concurrent.*;

public class RedisSourceTask extends SourceTask {
    private static final Logger LOGGER = LoggerFactory.getLogger(RedisSourceTask.class);
    
    private Jedis jedis;
    private final BlockingQueue<SourceRecord> eventQueue = new LinkedBlockingQueue<>(10000);
    private String topic;
    private String redisHost;
    private int redisPort;
    private volatile boolean running = true;
    
    // Value Schema（事件的字段定义）
    private static final Schema VALUE_SCHEMA = SchemaBuilder.struct()
        .name("com.example.debezium.redis.RedisChangeEvent")
        .field("key", Schema.STRING_SCHEMA)
        .field("operation", Schema.STRING_SCHEMA)
        .field("value", Schema.OPTIONAL_STRING_SCHEMA)
        .field("timestamp", Schema.INT64_SCHEMA)
        .field("database", Schema.INT32_SCHEMA)
        .build();

    @Override
    public void start(Map<String, String> props) {
        this.redisHost = props.get("redis.host");
        this.redisPort = Integer.parseInt(props.get("redis.port"));
        this.topic = props.get("topic.name");
        
        this.jedis = new Jedis(redisHost, redisPort);
        LOGGER.info("Connected to Redis {}:{}", redisHost, redisPort);
        
        // 启动 Keyspace Notification 订阅线程
        Thread subscriber = new Thread(this::subscribeKeyspace);
        subscriber.setDaemon(true);
        subscriber.setName("redis-keyspace-subscriber");
        subscriber.start();
    }
    
    /**
     * 订阅 Redis Keyspace Notifications
     * 频道格式: __keyspace@<db>__:<key>
     * 消息内容: set / del / hset / expire 等命令名
     */
    private void subscribeKeyspace() {
        try {
            jedis.psubscribe(new JedisPubSub() {
                @Override
                public void onPMessage(String pattern, String channel, String message) {
                    // channel: "__keyspace@0__:user:1001"
                    // message: "set" or "del" or "hset"
                    String[] parts = channel.split(":", 2);
                    if (parts.length < 2) return;
                    
                    String key = parts[1];    // "user:1001"
                    String op = message;       // "set"
                    int db = 0;                // 从 channel 中提取 db 编号
                    
                    try {
                        SourceRecord record = buildSourceRecord(key, op, db);
                        eventQueue.offer(record);
                        LOGGER.debug("Captured Redis event: key={} op={}", key, op);
                    } catch (Exception e) {
                        LOGGER.error("Failed to build SourceRecord for key={} op={}", key, op, e);
                    }
                }
            }, "__keyspace@*__:*");
        } catch (Exception e) {
            LOGGER.error("Redis subscription error", e);
        }
    }
    
    /**
     * 构建 SourceRecord —— 将 Redis 操作转换为 Kafka 消息
     */
    private SourceRecord buildSourceRecord(String key, String op, int db) {
        // 查询当前 value（可选——如果频繁查询可能影响 Redis 性能）
        String value = null;
        try {
            value = jedis.get(key);  // 只对 String 类型做 get（Hash/List/Set 需不同命令）
        } catch (Exception e) {
            LOGGER.debug("Could not GET value for key {}: {}", key, e.getMessage());
        }
        
        // 构建 Struct value
        Struct valueStruct = new Struct(VALUE_SCHEMA)
            .put("key", key)
            .put("operation", op)
            .put("value", value)
            .put("timestamp", System.currentTimeMillis())
            .put("database", db);
        
        // SourcePartition 和 SourceOffset 可用于实现断点恢复
        Map<String, String> sourcePartition = Collections.singletonMap("redis", redisHost);
        Map<String, String> sourceOffset = Collections.singletonMap("timestamp", String.valueOf(System.currentTimeMillis()));
        
        return new SourceRecord(
            sourcePartition,
            sourceOffset,
            topic,
            null,                  // keySchema (null = use default)
            Schema.STRING_SCHEMA,  // key 的 Schema
            key,                   // Kafka 消息 Key = Redis key
            VALUE_SCHEMA,          // value 的 Schema
            valueStruct            // Kafka 消息 Value
        );
    }

    @Override
    public List<SourceRecord> poll() throws InterruptedException {
        List<SourceRecord> batch = new ArrayList<>();
        // 批量取出最多 100 条，如果队列空则阻塞最多 500ms
        eventQueue.drainTo(batch, 100);
        return batch;
    }

    @Override
    public void stop() {
        running = false;
        if (jedis != null) {
            jedis.close();
        }
    }

    @Override
    public String version() {
        return "1.0.0";
    }
}
```

### 步骤4：打包 + 部署 + 验证

```bash
mvn clean package -DskipTests
cp target/debezium-connector-redis-1.0.0.jar ~/debezium-lab/plugins/
docker restart connect && sleep 20

# 注册 Connector
curl -X POST http://localhost:8083/connectors -H "Content-Type: application/json" -d '{
  "name": "redis-source-test",
  "config": {
    "connector.class": "com.example.debezium.redis.RedisSourceConnector",
    "redis.host": "localhost",
    "redis.port": "6379",
    "topic.name": "redis.changes",
    "tasks.max": "1"
  }
}'

# 验证：执行 Redis 写操作观察 Kafka Topic
redis-cli SET user:1001 '{"name":"Alice","email":"alice@test.com","phone":"138****5678"}'
redis-cli DEL user:1001
redis-cli SET inventory:sku-42 '100'

docker exec kafka kafka-console-consumer --bootstrap-server localhost:9092 \
  --topic redis.changes --max-messages 3 --timeout-ms 10000 2>/dev/null | python3 -m json.tool
```

## 4. 项目总结

| 组件 | 职责 | 调用频率 |
|------|------|---------|
| `SourceConnector` | 配置解析 + Task 分配 | Connector 生命周期 1 次 |
| `SourceTask.start()` | 初始化连接 + 启动订阅线程 | Task 启动 1 次 |
| `SourceTask.poll()` | 返回待发送的 SourceRecord 批 | framework 每 500ms 调用 |
| `SourceTask.stop()` | 清理资源 | Task 终止 1 次 |

### 限制与改进方向
- **单 Task**：Keyspace Notifications 全局广播，多 Task 会全部收到相同事件 → 重复
- **无 offset 持久化**：重启后从当前时刻开始，错过停机期间的变更
- **改进**：引入 Redis Stream 消费位点 或 维护消费时间戳表

### 思考题

1. 如何为 Redis Connector 实现 offset 持久化？使得 Connector 重启后能从上次断点恢复而不错过停机期间的 SET/DEL 操作？提示：参考 Debezium 的 `OffsetContext` 设计。

2. Redis Connector 在 `buildSourceRecord` 中调用了 `jedis.get(key)` 查询当前值。如果 Redis 是高负载实例，这个 GET 可能带来性能影响。如何设计一个不依赖 GET 的方案来获取被修改后的值？

---

> **推广提示**：掌握自定义 Connector 开发后，任何有变更事件的数据源都可纳入 Debezium 体系——这是从 CDC 使用者到构建者的质变。建议将本 Redis Connector 作为团队 Kafa Connect 开发培训的"Hello World"项目。
