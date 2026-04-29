# 第36章 自定义Connector开发（上）：DataSource

## 1 项目背景

### 业务场景：Flink CDC不支持的数据库怎么办？

公司的消息队列使用的是**Pulsar**，不是Kafka。但Flink CDC官方的Pipeline连接器只支持Kafka Sink，不支持Pulsar Sink。团队需要扩展Flink CDC——开发一个自定义的Pulsar DataSink。

或者，公司的某张业务表使用的是**SQLite**（嵌入式数据库），Flink CDC不支持SQLite Source。团队需要开发一个自定义的SQLite DataSource。

**自定义Connector开发**是Flink CDC扩展能力的核心。本章和下一章将通过一个完整示例，教你如何开发自定义的DataSource和DataSink。

### 自定义DataSource架构

```
Flink CDC Pipeline框架
    │
    ├── 接口: DataSource (flink-cdc-common)
    │   ├── EventSourceProvider
    │   │   └── 返回 DataStream<Event>
    │   └── MetadataAccessor
    │       └── 获取表元数据（表名、列名、类型）
    │
    ├── SPI注册: META-INF/services/org.apache.flink.cdc.common.source.DataSource
    │
    └── YAML配置:
        source:
          type: my_custom_source    # 与SPI注册的type对应
```

---

## 2 项目设计 · 三人交锋对话

**小胖**（兴奋）：自定义Connector！是不是像写一个普通的Flink SourceFunction一样？

**大师**：不完全一样。Flink CDC Pipeline框架的自定义DataSource需要实现`DataSource`接口，包含两个部分：

```java
public interface DataSource {
    // 1. Source提供器——返回Flink的DataStream<Event>
    EventSourceProvider getEventSourceProvider();
    
    // 2. 元数据访问器——获取表的Schema信息
    MetadataAccessor getMetadataAccessor();
}
```

`EventSourceProvider`负责返回实际的Flink DataStream（数据从哪里来），`MetadataAccessor`负责告诉框架"这个数据源有哪些表、每张表有什么列"。

通过SPI（Service Provider Interface）机制，框架可以在运行时自动发现你的自定义Connector。

**小白**：那`MetadataAccessor`为什么重要？如果我不实现它，只用`EventSourceProvider`能工作吗？

**大师**：`MetadataAccessor`的重要性在于**Schema Evolution**。当你在Pipeline YAML中配置了自动建表（`auto-create-table: true`）或者Schema Evolution（`schema.change.behavior: EVOLVE`）时，框架需要知道数据源的Schema是什么。

例如，你的自定义Source输出一个事件，框架需要知道"这个事件属于哪个表、有哪些列、列的类型是什么"——这些信息通过`MetadataAccessor`获取。

如果不需要Schema Evolution，可以返回一个简单的`MetadataAccessor`：

```java
public SimpleMetadataAccessor(List<TableId> tableIds) {
    // 直接返回TableId列表，不提供详细列信息
}
```

**技术映射**：DataSource的接口设计像"USB设备的热插拔"——你插上USB（引入自定义DataSource的JAR包），系统自动识别（SPI发现），不需要重启电脑或改配置（即插即用）。

---

## 3 项目实战

### 分步实现

#### 步骤1：项目结构和依赖

```
my-custom-connector/
├── pom.xml
├── src/main/java/com/example/connector/
│   ├── source/
│   │   ├── CustomDataSource.java
│   │   ├── CustomEventSourceProvider.java
│   │   └── CustomMetadataAccessor.java
│   └── sink/
│       └── (第37章实现)
└── src/main/resources/
    └── META-INF/services/
        └── org.apache.flink.cdc.common.source.DataSource  ← SPI注册
```

**Maven依赖：**
```xml
<dependencies>
    <!-- Flink CDC核心抽象 -->
    <dependency>
        <groupId>org.apache.flink</groupId>
        <artifactId>flink-cdc-common</artifactId>
        <version>3.0.0</version>
        <scope>provided</scope>
    </dependency>
    <!-- Flink Streaming API -->
    <dependency>
        <groupId>org.apache.flink</groupId>
        <artifactId>flink-streaming-java</artifactId>
        <version>1.20.3</version>
        <scope>provided</scope>
    </dependency>
</dependencies>
```

#### 步骤2：实现自定义DataSource——CSV文件CDC Source

假设我们有一个CSV文件，内容不断增加（类似日志追加写入）。我们开发一个"CSV CDC Source"，将CSV文件的每行新增内容作为INSERT事件输出。

```java
package com.example.connector.source;

import org.apache.flink.cdc.common.event.DataChangeEvent;
import org.apache.flink.cdc.common.event.Event;
import org.apache.flink.cdc.common.event.TableId;
import org.apache.flink.cdc.common.schema.Column;
import org.apache.flink.cdc.common.schema.Schema;
import org.apache.flink.cdc.common.source.DataSource;
import org.apache.flink.cdc.common.source.EventSourceProvider;
import org.apache.flink.cdc.common.source.MetadataAccessor;
import org.apache.flink.cdc.common.types.DataTypes;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.streaming.api.functions.source.RichSourceFunction;

import java.io.File;
import java.io.RandomAccessFile;
import java.util.Arrays;
import java.util.List;

/**
 * 自定义CSV File CDC DataSource
 * 
 * 功能: 持续监控CSV文件的尾部追加内容，作为CDC INSERT事件输出
 * 
 * YAML配置:
 *   source:
 *     type: csv_file
 *     path: /data/orders.csv
 *     interval.ms: 1000
 */
public class CsvFileDataSource implements DataSource {

    private final String filePath;
    private final long intervalMs;
    private final TableId tableId;

    public CsvFileDataSource(String filePath, long intervalMs) {
        this.filePath = filePath;
        this.intervalMs = intervalMs;
        this.tableId = TableId.tableId("default", "csv_db", "orders");
    }

    @Override
    public EventSourceProvider getEventSourceProvider() {
        return env -> {
            // 返回Flink DataStream<Event>
            DataStream<Event> stream = env.addSource(
                new CsvFileSourceFunction(filePath, intervalMs, tableId));
            return stream;
        };
    }

    @Override
    public MetadataAccessor getMetadataAccessor() {
        // 返回表的Schema信息（列名和类型）
        Schema schema = Schema.newBuilder()
            .column(Column.physicalColumn("id", DataTypes.INT()))
            .column(Column.physicalColumn("order_id", DataTypes.STRING()))
            .column(Column.physicalColumn("amount", DataTypes.DECIMAL(10, 2)))
            .column(Column.physicalColumn("status", DataTypes.STRING()))
            .primaryKey("id")
            .build();

        return new MetadataAccessor() {
            @Override
            public List<TableId> listTableIds() {
                return Arrays.asList(tableId);
            }

            @Override
            public Schema getTableSchema(TableId tableId) {
                return schema;
            }
        };
    }

    // ========== CSV文件读取SourceFunction ==========
    private static class CsvFileSourceFunction 
            extends RichSourceFunction<Event> {
        
        private final String filePath;
        private final long intervalMs;
        private final TableId tableId;
        private volatile boolean running = true;

        @Override
        public void run(SourceContext<Event> ctx) throws Exception {
            File file = new File(filePath);
            if (!file.exists()) {
                throw new RuntimeException("CSV file not found: " + filePath);
            }

            RandomAccessFile raf = new RandomAccessFile(file, "r");
            // 跳到文件末尾（只读新增内容）
            raf.seek(file.length());

            while (running) {
                String line = raf.readLine();
                if (line != null) {
                    // 解析CSV行 → INSERT事件
                    Event event = parseLineToEvent(line);
                    if (event != null) {
                        synchronized (ctx.getCheckpointLock()) {
                            ctx.collect(event);
                        }
                    }
                } else {
                    // 没有新数据，等待
                    Thread.sleep(intervalMs);
                }
            }
            raf.close();
        }

        @Override
        public void cancel() {
            running = false;
        }

        private Event parseLineToEvent(String line) {
            // "1,ORD001,99.99,PAID" → DataChangeEvent
            String[] fields = line.split(",");
            if (fields.length < 4) return null;
            
            // 构建DataChangeEvent（INSERT）
            // ... (省略详细构建代码)
            return null; // 实际返回DataChangeEvent
        }
    }
}
```

#### 步骤3：SPI注册文件

创建 `src/main/resources/META-INF/services/org.apache.flink.cdc.common.source.DataSource`：

```
# 注册自定义DataSource
# 格式: <type_name>=<实现类的全限定名>
csv_file=com.example.connector.source.CsvFileDataSource
```

#### 步骤4：DataSourceFactory工厂类（可选）

如果自定义DataSource需要从YAML配置中解析参数，可以实现`DataSourceFactory`：

```java
package com.example.connector.source;

import org.apache.flink.cdc.common.source.DataSource;
import org.apache.flink.cdc.common.source.DataSourceFactory;

import java.util.Map;

/**
 * Factory类：从YAML配置创建DataSource实例
 * 框架通过SPI发现此工厂类
 */
public class CsvFileDataSourceFactory implements DataSourceFactory {

    @Override
    public String identifier() {
        return "csv_file";  // 匹配YAML中的source.type
    }

    @Override
    public DataSource createDataSource(Map<String, String> properties) {
        String filePath = properties.get("path");
        long intervalMs = Long.parseLong(
            properties.getOrDefault("interval.ms", "1000"));
        return new CsvFileDataSource(filePath, intervalMs);
    }
}
```

**SPI注册：**
```
# META-INF/services/org.apache.flink.cdc.common.source.DataSourceFactory
csv_file=com.example.connector.source.CsvFileDataSourceFactory
```

#### 步骤5：使用自定义DataSource的Pipeline YAML

```yaml
source:
  type: csv_file                 # 自定义类型，与Factory.identifier()匹配
  path: /data/orders.csv
  interval.ms: 1000

sink:
  type: kafka
  properties:
    bootstrap.servers: localhost:9092

pipeline:
  name: Custom CSV CDC Pipeline
  parallelism: 1
```

**提交时需要将自定义Connector的JAR包加入classpath：**
```bash
flink-cdc.sh pipeline-csv.yaml \
  --jar my-custom-connector-1.0.jar
```

#### 常见陷坑及解决方法

| 问题 | 原因 | 解决方法 |
|------|------|---------|
| `DataSourceFactory` SPI未发现 | META-INF/services文件缺失或格式错误 | 检查文件名是否与接口全限定名一致，内容格式正确 |
| 自定义Source的Event不能被Sink接受 | Event类型不匹配 | 确保输出的是`DataChangeEvent`，使用`Event`接口 |
| MetadataAccessor返回空Schema | Schema Evolution失败 | 至少返回表名和主键信息 |
| 并行度>1时数据重复 | 自定义Source未考虑并行情况 | 实现`CheckpointedFunction`接口保证Source的Exactly-Once |

---

## 4 项目总结

### 自定义DataSource开发清单

```
□ 实现DataSource接口
  □ getEventSourceProvider() → 返回Flink DataStream<Event>
  □ getMetadataAccessor() → 返回表的Schema信息

□ 实现DataSourceFactory接口（推荐）
  □ identifier() → 返回YAML中的type名称
  □ createDataSource() → 从配置参数创建DataSource实例

□ SPI注册
  □ META-INF/services/org.apache.flink.cdc.common.source.DataSource
  □ META-INF/services/org.apache.flink.cdc.common.source.DataSourceFactory

□ 打包和测试
  □ mvn package → 生成JAR
  □ 使用--jar参数提交Pipeline
  □ 验证YAML配置正确解析
```

### DataSource vs DataSink接口对比

| 接口 | DataSource | DataSink |
|------|-----------|---------|
| 核心方法 | `getEventSourceProvider()` | `getEventSinkProvider()` |
| 返回类型 | `EventSourceProvider` | `EventSinkProvider` |
| 元数据 | `MetadataAccessor`（读） | `MetadataApplier`（写） |
| Factory | `DataSourceFactory` | `DataSinkFactory` |
| Event类型 | 生产`Event` | 消费`Event` |

### 思考题

1. **进阶题①**：自定义DataSource需要支持Exactly-Once语义。在CSV File Source实现中，如果作业崩溃后恢复，如何保证不重复读取CSV文件的某些行？提示：实现`CheckpointedFunction`，记录已读取的行偏移量。

2. **进阶题②**：`MetadataAccessor.listTableIds()`方法返回的`TableId`列表，是否可以动态变化（比如CSV Source运行时检测到新的CSV文件）？Flink CDC Pipeline框架是否支持运行时新增表？查看`capture-new-tables`配置的源码实现。

---

> **下一章预告**：第37章「自定义Connector开发（下）：DataSink」——实现自定义DataSink写入到任意目标系统。以自定义Pulsar Sink为例，实现`DataSink`接口、`MetadataApplier`、`EventSinkProvider`，以及sink的幂等写入和事务支持。
