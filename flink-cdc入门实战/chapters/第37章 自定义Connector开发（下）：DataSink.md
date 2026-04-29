# 第37章 自定义Connector开发（下）：DataSink

## 1 项目背景

### 业务场景：团队需要将CDC数据写入Pulsar

第36章实现了自定义CSV File Source。但实际需求是：**将CDC数据写入Apache Pulsar**（而不是Kafka）。Flink CDC官方没有提供Pulsar Sink，团队需要自己开发。

自定义DataSink需要实现：
1. `DataSink`接口——定义Sink的基本行为
2. `EventSinkProvider`接口——返回Flink Sink算子
3. `MetadataApplier`接口——处理Schema变更（DDL同步到Pulsar）

### 自定义DataSink架构

```
Pipeline Framework
    │
    ├── DataSink (接口)
    │   ├── EventSinkProvider
    │   │   └── 返回 DataStreamSink<Event>
    │   └── MetadataApplier
    │       └── 应用Schema变更到Sink
    │
    ├── SPI: META-INF/services/.../DataSinkFactory
    │
    └── YAML:
        sink:
          type: pulsar    # 自定义类型
```

---

## 2 项目设计 · 三人交锋对话

**小胖**（思考）：DataSource和DataSink看起来是对称的——一个生产Event，一个消费Event。但Sink好像多了一个`MetadataApplier`？

**大师**：没错。`MetadataApplier`是Flink CDC Pipeline Sink独有接口——它负责将DDL变更（`SchemaChangeEvent`）应用到目标系统。

```java
public interface DataSink {
    // 1. Sink提供器——返回Flink DataStreamSink
    EventSinkProvider getEventSinkProvider();
    
    // 2. 元数据应用器——将Schema变更应用到目标系统
    //    例如: Pulsar Sink收到AddColumnEvent → 更新Pulsar Schema
    MetadataApplier getMetadataApplier(TableId tableId);
}
```

**小白**：那`MetadataApplier`在什么时候被调用？是每个DDL事件单独调一次，还是批量调用？

**大师**：在Pipeline SchemaOperator的处理流程中，每个DDL事件经过SchemaOperator协调后，通过`MetadataApplier.applySchemaChange()`单独调用。不同的DDL类型对应不同的处理：

| SchemaChangeEvent | MetadataApplier行为 |
|-------------------|---------------------|
| CreateTableEvent | 在目标系统创建表/主题 |
| AddColumnEvent | 增加列（如Pulsar更新Schema） |
| AlterColumnTypeEvent | 修改列类型 |
| DropColumnEvent | 删除列 |

---

## 3 项目实战

### 分步实现

#### 步骤1：实现Pulsar DataSink

```java
package com.example.connector.sink;

import org.apache.flink.cdc.common.event.Event;
import org.apache.flink.cdc.common.event.TableId;
import org.apache.flink.cdc.common.sink.DataSink;
import org.apache.flink.cdc.common.sink.EventSinkProvider;
import org.apache.flink.cdc.common.sink.MetadataApplier;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.datastream.DataStreamSink;
import org.apache.flink.streaming.api.functions.sink.DiscardingSink;

/**
 * 自定义Pulsar DataSink
 * 
 * YAML配置:
 *   sink:
 *     type: pulsar
 *     service-url: pulsar://localhost:6650
 *     topic-prefix: cdc_
 */
public class PulsarDataSink implements DataSink {

    private final String serviceUrl;
    private final String topicPrefix;

    public PulsarDataSink(String serviceUrl, String topicPrefix) {
        this.serviceUrl = serviceUrl;
        this.topicPrefix = topicPrefix;
    }

    @Override
    public EventSinkProvider getEventSinkProvider() {
        return new EventSinkProvider() {
            @Override
            public DataStreamSink<Event> sinkTo(DataStream<Event> input) {
                return input
                    .map(event -> {
                        // 将Flink CDC Event → Pulsar Message
                        // ...
                        return event;
                    })
                    .sinkTo(new DiscardingSink<>()) // 替换为Pulsar Sink
                    .name("Pulsar Sink");
            }
        };
    }

    @Override
    public MetadataApplier getMetadataApplier(TableId tableId) {
        return new PulsarMetadataApplier(serviceUrl, topicPrefix + tableId.getTableName());
    }
}
```

#### 步骤2：实现MetadataApplier——Schema变更同步

```java
package com.example.connector.sink;

import org.apache.flink.cdc.common.event.*;
import org.apache.flink.cdc.common.sink.MetadataApplier;
import org.apache.flink.cdc.common.types.DataType;

/**
 * Pulsar Schema变更应用器
 * 将Flink CDC的DDL事件转换为Pulsar Schema操作
 */
public class PulsarMetadataApplier implements MetadataApplier {

    private final String serviceUrl;
    private final String topicName;

    public PulsarMetadataApplier(String serviceUrl, String topicName) {
        this.serviceUrl = serviceUrl;
        this.topicName = topicName;
    }

    @Override
    public void applySchemaChange(SchemaChangeEvent event) {
        if (event instanceof CreateTableEvent) {
            handleCreateTable((CreateTableEvent) event);
        } else if (event instanceof AddColumnEvent) {
            handleAddColumn((AddColumnEvent) event);
        } else if (event instanceof DropColumnEvent) {
            handleDropColumn((DropColumnEvent) event);
        } else if (event instanceof AlterColumnTypeEvent) {
            handleAlterColumnType((AlterColumnTypeEvent) event);
        } else {
            throw new UnsupportedOperationException(
                "Unsupported SchemaChangeEvent: " + event.getClass());
        }
    }

    private void handleCreateTable(CreateTableEvent event) {
        // 1. 获取Pulsar Admin客户端
        // 2. 创建Topic（如果不存在）
        // 3. 设置Schema（基于event的列定义）
        System.out.println("[Pulsar] Creating topic: " + topicName);
        // PulsarAdmin admin = new PulsarAdmin(serviceUrl);
        // admin.topics().createNonPartitionedTopic(topicName);
        // admin.schemas().createSchema(topicName, buildSchema(event));
    }

    private void handleAddColumn(AddColumnEvent event) {
        // 更新Pulsar Topic的Schema：追加新列
        for (AddColumnEvent.ColumnWithPosition col : event.getAddedColumns()) {
            String colName = col.getColumn().getName();
            DataType colType = col.getColumn().getType();
            System.out.printf("[Pulsar] Adding column %s (%s) to %s%n",
                colName, colType, topicName);
        }
        // admin.schemas().updateSchema(topicName, updatedSchema);
    }

    private void handleDropColumn(DropColumnEvent event) {
        System.out.println("[Pulsar] Dropping columns from " + topicName
            + ": " + event.getDroppedColumnNames());
    }

    private void handleAlterColumnType(AlterColumnTypeEvent event) {
        System.out.println("[Pulsar] Altering column types on " + topicName);
    }

    @Override
    public void setAcceptedSchemaEvolutionTypes(
            java.util.Set<Class<? extends SchemaChangeEvent>> types) {
        // 控制Sink接受哪些类型的Schema变更
        // 例如：只接受ADD COLUMN，不接受DROP COLUMN
    }
}
```

#### 步骤3：实现DataSinkFactory

```java
package com.example.connector.sink;

import org.apache.flink.cdc.common.sink.DataSink;
import org.apache.flink.cdc.common.sink.DataSinkFactory;

import java.util.Map;

/**
 * Pulsar DataSink Factory
 * 从YAML配置创建PulsarDataSink实例
 */
public class PulsarDataSinkFactory implements DataSinkFactory {

    @Override
    public String identifier() {
        return "pulsar";  // 匹配YAML中的sink.type
    }

    @Override
    public DataSink createDataSink(Map<String, String> properties) {
        String serviceUrl = properties.get("service-url");
        String topicPrefix = properties.getOrDefault("topic-prefix", "cdc_");
        return new PulsarDataSink(serviceUrl, topicPrefix);
    }
}
```

#### 步骤4：SPI注册

```bash
# META-INF/services/org.apache.flink.cdc.common.sink.DataSinkFactory
pulsar=com.example.connector.sink.PulsarDataSinkFactory
```

#### 步骤5：使用自定义Sink的Pipeline

```yaml
source:
  type: mysql
  hostname: localhost
  port: 3306
  username: cdc_user
  password: cdc_pass
  tables: shop.orders

sink:
  type: pulsar
  service-url: pulsar://pulsar-broker:6650
  topic-prefix: cdc_
  # Pulsar特有配置
  producer:
    batch-max-size: 1024
    send-timeout-ms: 30000

pipeline:
  name: CDC to Pulsar Pipeline
  parallelism: 2
  schema.change.behavior: EVOLVE
```

#### 常见陷坑及解决方法

| 问题 | 原因 | 解决方法 |
|------|------|---------|
| Sink写入慢导致反压 | Sink未实现批量写入 | 实现缓冲机制，每批N条或每T毫秒刷新一次 |
| SchemaEvolution在Sink端不生效 | MetadataApplier未正确处理DDL | 实现applySchemaChange()并处理各类SchemaChangeEvent |
| 自定义Sink数据格式不匹配 | Event序列化方式与Sink预期不一致 | 在EventSinkProvider中实现自定义序列化 |
| Factory的identifier()与YAML不匹配 | 大小写或连字符不一致 | 确保identifier()返回值和YAML的type完全一致 |

---

## 4 项目总结

### 自定义DataSource vs DataSink

| 对比维度 | DataSource | DataSink |
|---------|-----------|---------|
| 接口 | `EventSourceProvider` | `EventSinkProvider` |
| 元数据 | `MetadataAccessor`（读） | `MetadataApplier`（写） |
| 主方法 | `sinkTo(DataStream<Event>)` | 返回DataStream |
| 关键能力 | 读取外部数据 | 写入外部数据 |
| Exactly-Once | Source重放 | Sink幂等/事务 |
| Schema处理 | 提供Schema信息 | 应用Schema变更 |

### 生产环境Connector开发检查清单

```
□ 实现核心接口
  □ DataSource / DataSink
  □ DataSourceFactory / DataSinkFactory
  □ MetadataAccessor / MetadataApplier

□ 线程安全（Flink多线程环境）
  □ 实例变量线程安全
  □ 无静态可变状态

□ 容错
  □ Checkpoint集成
  □ 幂等写入
  □ 连接重试

□ SPI注册
  □ META-INF/services/...配置正确
  □ JAR包可被Flink ClassLoader加载

□ 测试
  □ 单元测试
  □ 集成测试（Testcontainers）
  □ E2E Pipeline测试
```

### 思考题

1. **进阶题①**：自定义DataSink的`getMetadataApplier()`方法返回的`MetadataApplier`实例，在Pipeline运行时是每个`TableId`创建一个还是全局共享一个？这对有状态操作（如Pulsar Admin客户端连接管理）有什么影响？

2. **进阶题②**：在`MetadataApplier.applySchemaChange()`中，如果Sink应用DDL失败（如Pulsar不支持DROP COLUMN），应该如何通过`setAcceptedSchemaEvolutionTypes()`控制接受的DDL类型？返回成功还是抛出异常？Flink CDC Pipeline框架会如何处理失败的DDL？

---

> **下一章预告**：第38章「极端场景优化：万级表与大事务」——当Flink CDC需要同时监控10000+张表，或处理修改数亿行的大事务时，如何优化内存、网络、CPU等资源消耗？本章将深入探讨Flink CDC在大规模场景下的极限能力。
