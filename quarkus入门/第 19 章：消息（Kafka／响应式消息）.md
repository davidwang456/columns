# 第 19 章：消息（Kafka／响应式消息）

## 0. 课程卡片

| 项目 | 说明 |
|------|------|
| **建议课时** | 75～90 分钟（含 Kafka 启动） |
| **学习目标** | 配置 SmallRye Reactive Messaging + Kafka；编写 `@Incoming`；理解 consumer group |
| **先修** | 第 3、14 章 |

---

## 1. 项目背景

事件驱动架构在 K8s 上常见：**削峰、解耦**。本章给出完整 **pom + properties + 消费者类 + Docker Compose 可选 + K8s Strimzi 提示**。

---

## 2. 项目设计：大师与小白的对话

**小白**：「消息一定比 HTTP 好？」

**大师**：「换来 **最终一致** 与运维复杂度；别默认消息优先。」

**运维**：「lag 飙高谁值班？」

**大师**：「告警规则 + 消费者扩容 + **处理耗时**优化。」

**测试**：「本地怎么起 Kafka？」

**大师**：**Dev Services** 或 `docker compose`；CI 用 Testcontainers。」

**架构师**：「幂等怎么做？」

**大师**：「业务键去重表或幂等写；死信队列。」

---

## 3. 知识要点

- `mp.messaging.incoming.*.connector=smallrye-kafka`  
- `group.id`、`topic`、`auto.offset.reset`

---

## 4. 项目实战

### 4.1 `pom.xml`

```xml
<dependency>
  <groupId>io.quarkus</groupId>
  <artifactId>quarkus-messaging-kafka</artifactId>
</dependency>
```

### 4.2 `application.properties`

```properties
kafka.bootstrap.servers=localhost:9092
mp.messaging.incoming.orders-in.connector=smallrye-kafka
mp.messaging.incoming.orders-in.topic=orders
mp.messaging.incoming.orders-in.group.id=order-service
mp.messaging.incoming.orders-in.auto.offset.reset=earliest
```

### 4.3 消费者

`src/main/java/org/acme/OrderConsumer.java`：

```java
package org.acme;

import jakarta.enterprise.context.ApplicationScoped;
import org.eclipse.microprofile.reactive.messaging.Incoming;

@ApplicationScoped
public class OrderConsumer {

    @Incoming("orders-in")
    public void consume(String payload) {
        // 课堂：替换为 JSON 反序列化 + 幂等处理
        System.out.println("received: " + payload);
    }
}
```

### 4.4 `docker-compose-kafka.yml`（课堂用）

```yaml
services:
  kafka:
    image: redpandadata/redpanda:v24.2.4
    command:
      - redpanda
      - start
      - --kafka-addr internal://0.0.0.0:9092,external://0.0.0.0:19092
      - --advertise-kafka-addr internal://kafka:9092,external://localhost:19092
      - --pandaproxy-addr 0.0.0.0:8082
      - --schema-registry-addr 0.0.0.0:8081
      - --smp 1
      - --memory 1G
      - --mode dev-container
      - --default-log-level=warn
    ports:
      - "19092:19092"
```

> 培训可将 `kafka.bootstrap.servers` 设为 `localhost:19092`（以外部 advertised 为准，按 Redpanda 文档调整）。

### 4.5 Kubernetes（Strimzi 提示）

生产常用 **Strimzi Kafka**；应用 `Deployment` 注入：

```yaml
env:
  - name: KAFKA_BOOTSTRAP_SERVERS
    value: "my-cluster-kafka-bootstrap.kafka:9092"
```

并在 `application.properties` 用 `${KAFKA_BOOTSTRAP_SERVERS}` 映射（或通过 SmallRye Config 映射）。

---

## 5. 课堂实验

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | `docker compose up -d`，创建 topic `orders` | Kafka 就绪 |
| 2 | 启动 Quarkus，用 `rpk topic produce` 或 `kcat` 发消息 | 控制台打印 |
| 3 | 扩展消费者处理时间 `Thread.sleep`，观察 lag | 运维体感 |
| 4 | 讨论：K8s 内 Service DNS 作为 bootstrap | 画出数据流 |

---

## 6. 项目总结

| 维度 | 说明 |
|------|------|
| **优点** | 模型统一；与 Mutiny 衔接。 |
| **缺点** | 本地与 CI 复杂。 |
| **适用场景** | 异步集成、事件流。 |
| **注意事项** | 幂等；重平衡。 |
| **常见踩坑** | 处理慢导致 rebalance；schema 演进。 |

**延伸阅读**：<https://quarkus.io/guides/kafka>
