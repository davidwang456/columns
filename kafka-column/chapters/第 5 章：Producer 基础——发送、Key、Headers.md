# 第 5 章：Producer 基础——发送、Key、Headers

> 级别：基础  
> 预计阅读：约 3000～5000 字（正文以汉字为主，含标点）  
> 配套示例：`examples/src/main/java/org/example/column/ch05/ProducerBasicsDemo.java`

---

## 1. 项目背景

> **读法提示**：本章「项目设计」为 **小胖 / 小白 / 大师** 三角色对话——**小胖**用直觉与「土问题」抛误区，**小白**追问原理、边界与风险，**大师**结合业务约束给选型与架构级结论；请对照「项目实战」动手与「项目总结」沉淀。

前几章已经建立 **Topic / Partition / 副本** 的宏观视图；从本章开始，我们把镜头对准**客户端写入路径**的第一站：**KafkaProducer**。在业务代码里，最常被操作的对象是 **`ProducerRecord`**——它承载 **Key、Value、Headers、目标 Topic（或分区）** 等元信息，决定消息进入哪一分区、携带哪些**可路由的元数据**（如链路追踪 ID），以及后续序列化与批处理的输入。

实际项目中的痛点包括：**（1）** 把 Key 放进 Value 的 JSON 里，却期望 Kafka 按「用户 ID」分区，导致分区键与路由键不一致；**（2）** 把大段业务字段塞进 **Headers**，超出合理大小或破坏语义（Header 适合轻量元数据）；**（3）** 不区分 **`linger.ms` / `batch.size`** 的默认行为，压测时发现延迟抖动却找不到调参抓手（第 13 章深入）。

本章产出物：**（1）** 能说明 `ProducerRecord` 各字段含义及与分区器的关系；**（2）** 能正确使用 **Headers** 传递 trace-id、内容类型等**与消息体解耦**的元数据；**（3）** 能在本机跑通示例，观察 **带 Key** 与 **Key=null** 时的分区行为差异，并显式配置基础 `ProducerConfig`（`acks`、`linger.ms`、`batch.size`）作为后续调优的基准。

在真实微服务里，Producer 往往与 **REST 网关、定时任务、CDC** 等并列存在；同一进程内可能**共用**一个 `KafkaProducer` 实例向多个 Topic 写入。此时更要注意：**不同 Topic 的分区策略与压缩策略可能不同**，不宜在业务层硬编码「全局 Producer 配置」；推荐按 Topic 或按业务域拆分配置，并在配置中心留**版本与变更记录**。入门阶段可先单 Topic 跑通，再演进到多 Topic。

若团队已有 **统一日志规范**，可把 **服务名、环境、版本号** 放进 Headers 或 Value 的固定字段，避免每个服务自定义一套键名；Kafka 不强制 Header 命名，但**团队内一致**能显著降低联调成本。

从 **API 视角**看，`KafkaProducer` 负责**元数据刷新、缓冲、重试、序列化**等横切能力；业务代码最频繁接触的是 **`send(ProducerRecord)`** 与 **`Callback`**（或 `Future`）。`ProducerRecord` 既可以是「只填 Topic + Value」的极简形态，也可以带 **分区号**、**时间戳**、**Headers**；时间戳若显式指定，会影响日志保留与压缩（与第 17 章衔接），入门阶段可先使用默认 `CreateTime`。理解这一分层后，阅读客户端源码或配置文档时，就不会把「批处理」与「分区路由」混成一步。

与 **运维/可观测**的衔接：Headers 中的 **trace-id** 往往与 APM、日志平台关联；Metrics 侧则关注 **request-latency、batch-size、record-error-rate** 等（第 22 章）。入门阶段先在日志里把 **topic、partition、offset、trace-id** 打齐，后续接入 Prometheus 时，团队已有统一字段语义，而不是临时拼字段。

---

## 2. 项目设计：小胖、小白与大师的对话

**小胖**：Producer 不就是 send 吗？我调大并发为啥吞吐上不去，还老超时？

**小白**：我们 JSON 里已经有 `userId` 了，Producer 还要设 Key 吗？重复了吧？

**大师**：**分区**默认按 **Key** 的哈希路由（若未指定分区）；若 Key 为空，则走 **轮询/粘性分区** 等策略。若 `userId` 只写在 JSON 里而 **Key 为空**，Kafka **看不到**你的业务键，**无法按用户维度保序**。把「路由键」放进 Key 是常见做法；JSON 仍可承载完整业务负载，但二者职责不同。

**小白**：Header 和 Key 不都是附加字段吗？我把鉴权放在 Header 里，把用户 ID 也放 Header，行不行？

**大师**：**Key** 参与**分区与顺序**；**Headers** 不参与分区，适合**追踪、协议版本、内容类型**等元数据。鉴权与敏感信息通常不应依赖 Header 明文穿越（TLS/SASL 与 ACL 在后续安全章节），且 Header 不应承载大块业务负载——否则压缩与序列化成本会偏离预期。

**小白**：`acks` 我设成 `all` 是不是最稳？`linger.ms=0` 是不是最低延迟？

**大师**：**`acks=all`** 表示等待 ISR 内足够副本确认（与 `min.insync.replicas` 联动，见第 4、14 章），**耐久更强**但延迟与失败概率**可能**更高；**`linger.ms=0`** 表示不人为等待聚合，但**仍可能**因批处理、网络、压缩等产生非零延迟。「最稳」与「最低延迟」在工程上往往冲突，需要结合业务与 SLA 选择。

**小白**：`Producer` 需要配 `bootstrap.servers` 写三个 Broker 吗？写一个行不行？

**大师**：**建议至少写多个**（或配合 DNS/负载均衡），这样**元数据发现**与**故障切换**更平滑；只写一个**单点**在 Broker 重启时可能增加短暂失败窗口。客户端会**自动刷新**集群元数据，但初始地址列表越健康，冷启动越稳。具体列表与 SRE 规范对齐即可。

**小白**：`send` 返回的 `RecordMetadata` 里 `offset` 是全局的吗？

**大师**：**offset 是分区内的**；跨分区比较 offset **没有意义**。排查「第几条消息」时，要同时带上 **topic + partition + offset**（或业务键 + 时间），这也是日志与追踪系统的设计基础。

**收束**：本章验收标准：**（1）** 发送一条带 **Key** 与 **Headers** 的消息，并打印 `RecordMetadata` 的 `partition`/`offset`；**（2）** 连续发送 **Key=null** 的若干条消息，观察分区分布；**（3）** 在代码中显式写出 `acks`、`linger.ms`、`batch.size`，作为后续第 13 章对比的基线。

三个「看起来对但不对」的推论：**推论 A**，「Headers 越大越好，方便传业务」——应轻量，**大负载放 Value**；**推论 B**，「Key 必须全局唯一」——Key 应是**路由与有序单元**所需粒度，**不是全局主键**的同义词；**推论 C**，「`batch.size` 越大延迟越低」——批次大往往**提高吞吐**，但可能**增加等待**（与 `linger` 共同作用）。

---

## 3. 项目实战

### 3.1 环境说明

与第 3、4 章相同，使用 [examples/docker-compose.yml](../examples/docker-compose.yml) 的本地 Broker；客户端 **kafka-clients 3.7.0** 见 [examples/pom.xml](../examples/pom.xml)。

### 3.2 主代码片段：ProducerRecord、Headers、Key=null

示例类：`org.example.column.ch05.ProducerBasicsDemo`。流程包括：**（1）** 若不存在则创建 `column.ch05.events`（3 分区、RF=1）；**（2）** 构造一条 **`ProducerRecord`**，带 **Key**（如订单相关键）与 **`RecordHeader`**（`trace-id`、`content-type`），同步 `send().get()` 打印 `partition`/`offset`；**（3）** 再发送 **Key=null** 的若干条消息，观察分区分布（与第 3 章衔接）。

**关于序列化**：示例使用 **StringSerializer**，Key 与 Value 均为字符串；生产常见还有 **ByteArraySerializer** 或 **自定义 Serializer**（例如 Protobuf）。无论哪种，**分区器看到的 Key 类型**必须与序列化结果一致；更换序列化方式时，**分区路由**可能变化，需评估是否影响有序性。入门阶段保持 Key/Value 类型稳定，比过早抽象「通用 Serializer」更重要。

**关于时间戳**：未显式设置时，Broker 通常使用 **CreateTime**（以 Broker 接收为准还是 Producer 携带为准取决于版本与配置，第 17 章与高级文档会细化）。调试时若发现「时间乱序」，不要立刻怀疑业务时钟——先确认 **timestamp.type** 与 **是否手动指定了 `ProducerRecord` 时间戳**。

```java
// 节选：Headers（不参与分区）
ProducerRecord<String, String> rec = new ProducerRecord<>(
    TOPIC, null, key, "{\"amt\":100}",
    List.of(
        new RecordHeader("trace-id", "tr-abc-001".getBytes(StandardCharsets.UTF_8)),
        new RecordHeader("content-type", "application/json".getBytes(StandardCharsets.UTF_8))));

// 节选：KafkaProducer 基础配置
props.put(ProducerConfig.ACKS_CONFIG, "all");
props.put(ProducerConfig.LINGER_MS_CONFIG, 0);
props.put(ProducerConfig.BATCH_SIZE_CONFIG, 16_384);
```

完整代码见仓库。**阅读点**：`ProducerRecord` 的构造函数有多个重载，可指定 **分区号**、**时间戳**、**Headers**；未指定分区时，由 `Partitioner` + Key 决定目标分区。**Headers** 在消费端通过 `ConsumerRecord.headers()` 读取（第 6 章可对照）。

**验证步骤**：

1. `cd kafka-column/examples && docker compose up -d`。
2. `mvn -q compile exec:java -Dexec.mainClass=org.example.column.ch05.ProducerBasicsDemo`。
3. 观察：第一段输出带 **Key** 的 `partition`/`offset`；第二段 **Key=null** 的各条 `partition` 可能分布在多个分区（与第 3 章一致）。

**期望输出（节选）**：

```text
已创建 topic: column.ch05.events ...

=== 1) 带 Key + Headers（trace-id、content-type）===
partition=1 offset=... key=order-... headers=trace-id, content-type

=== 2) Key=null 的多条消息（默认分区器：轮询/粘性分区，与第 3 章衔接）===
partition=0 offset=... value=heartbeat-0
...
```

若同一 Key 却落到不同分区，排查：**是否误用不同 Key**、**是否自定义分区器**、**是否指定了固定分区号**。

### 3.3 贴近生产的变体：显式参数

生产环境几乎总会配置 **`linger.ms`、`batch.size`、`compression.type`、`buffer.memory`** 等（第 13 章）。本章变体建议：在**保持 `acks=all` 不变**的前提下，将 **`linger.ms` 从 0 改为 5～10ms**，观察端到端延迟与批次行为（可用日志或指标）；同时尝试 **`compression.type=lz4`**，对比 CPU 与网络带宽。注意：**分区选择在发送前确定**，批处理主要影响**网络与 Broker 侧吞吐**，但**不会改变**「这条消息属于哪一分区」的路由决策（除非使用自定义逻辑与错误用法）。

**与 CLI 的对照**：`kafka-console-producer.sh` 支持 `--property headers=...`（版本与脚本需与 Broker 一致），适合快速验证 Header 是否被消费端看见；Java 示例更适合**单元测试与代码评审**。

**异步发送变体（推荐在预发练习）**：将 `send().get()` 改为 **`send(record, (metadata, exception) -> { ... })`**，在回调里记录成功/失败与 **`metadata.partition()`**。这样更接近生产负载模型，也能暴露**重试**与**乱序**问题（第 14 章）。本地演示时注意 **`flush()`** 或优雅关闭，避免进程退出时缓冲区未刷盘。

**调试技巧**：若怀疑「消息没进预期分区」，可在 `Producer` 侧打印 **`RecordMetadata`**，在 `Consumer` 侧打印 **`ConsumerRecord` 的 partition、offset、timestampType`**；两边用同一 `trace-id` Header 关联。不要把「分区不对」直接归因于网络——先核对 **Key、分区器、是否指定 partition 字段**。


### 3.4 运维视角（提要）

- 变更与发布：配置、副本、Leader 迁移对写入与消费的影响；关注指标与日志入口（参见第 10、22 章及 [rollout/OPS_ALERT_MAPPING.md](../rollout/OPS_ALERT_MAPPING.md)）。
- 容量与磁盘：分区数、保留策略与磁盘水位；避免「只盯 Lag 不盯 ISR」。

### 3.5 测试视角（提要）

- 集成：最小可复现链路 + 断言幂等与顺序边界；异常：断网、Broker 重启、重复投递（按环境裁剪）。
- 性能：对比调参前后同负载下的延迟与吞吐，避免「感觉更快」。


---

## 4. 项目总结

### 4.1 优点

- **模型清晰**：`ProducerRecord` 把路由键（Key）、业务负载（Value）、元数据（Headers）分开，便于评审与演进。
- **生态可观测**：Headers 常承载 trace-id，与 **OpenTelemetry** 等体系衔接（第 22 章指标与链路联动）。
- **可渐进调优**：先显式 `acks`、`linger`、`batch`，再进入第 13 章系统调参。
- **与多语言互通**：Key/Value/Headers 均为字节序列化结果，便于 **JVM 与 Go/Python** 等服务互发互收，只要契约一致即可。

### 4.2 缺点

- **误用 Key/Headers 成本高**：路由键与业务键不一致会导致分区与顺序问题，排查困难。
- **同步 `send().get()` 仅适合演示**：生产应使用回调或异步 + `flush()`，避免阻塞。
- **默认参数未必适合生产**：`linger=0` 可能在某些负载下牺牲吞吐。
- **契约演进压力**：Key/Value/Headers 的字段若缺乏版本治理，多团队并行升级时易出现「能发不能收」（第 9、21 章）。

### 4.3 适用场景

- **需要分区键与追踪**：订单 Key + trace-id Header。
- **Fire-and-forget 或日志型**：Key=null + 高吞吐，配合批处理与压缩。
- **多语言微服务**：Headers 传 `content-type`、schema id、租户 ID，便于消费端路由到不同反序列化器（与第 9、21 章衔接）。

### 4.4 注意事项

- **序列化**：`key.serializer` / `value.serializer` 要与 Topic 契约一致；**JSON 字符串**与 **Avro/Protobuf**（第 9、21 章）演进策略不同。
- **Header 大小与条数**：避免把大对象或敏感明文塞入 Header。
- **幂等与重试**：`enable.idempotence`、重试与乱序在第 14 章讨论，与第 5 章「能发」不同。
- **资源与线程**：`KafkaProducer` 是线程安全的，**多线程共享同一实例**通常优于每请求新建；关闭时务必 **`close(Duration)`** 或配合 **`flush`**，避免进程退出丢缓冲（第 13 章结合参数一起讲）。
- **监控与日志**：生产建议至少记录 **client-id**、**目标 topic**、**发送失败原因**（可脱敏），便于与 Broker 日志关联；必要时对 **重试次数**打点，并与 **SLA** 及 **告警阈值**对齐，避免盲飞现象。

### 4.5 常见踩坑

- **Key 只在 JSON 里**：Kafka 不会按 JSON 分区。
- **Header 当第二份 Value**：语义混乱、重复序列化。
- **忽略 `acks` 与 `min.insync.replicas` 联动**：以为「客户端 acks=all」就必然三副本落盘（需 ISR 与配置一致）。
- **在循环里反复 `new KafkaProducer`**：应复用单例 Producer，否则连接与线程池开销会淹没业务逻辑（第 13 章会结合 `close` 与资源释放）。

**跨团队沟通提示**：与前端/测试约定：**「路由键」= Producer Key**，**「展示字段」= Value**；日志检索用 **trace-id Header**，避免在日志里重复贴全量 JSON。

**与后续章节的衔接**：第 6 章 Consumer 读取 `ConsumerRecord` 与 **位移**；第 13 章系统调优 **batch/linger/compression**；第 14 章把 **acks、重试、幂等**与副本语义（第 4 章）连成一体。

**读者自测**：在纸上写三条 `ProducerRecord`：**（1）** 仅 Topic+Value；**（2）** Topic+Key+Value；**（3）** Topic+Key+Value+Headers。分别说明 **哪几条会参与分区路由**、**消费端从哪里取 trace-id**。若 **（1）** 与 **（2）** 路由不同，说明已理解 Key 的作用。

**与第 6 章的衔接**：下一章将用 **`KafkaConsumer`** 读取 **`ConsumerRecord`**，你会看到 **Key、Value、Headers、partition、offset** 在消费侧一一对应；建议在阅读第 6 章时，把本章示例再跑一遍，并在消费端打印 **Headers**，验证端到端契约未被破坏。若消费端过滤了 Headers，排查时优先核对 **反序列化器**与 **老版本客户端**是否支持 Headers。


### 4.6 跨部门协作提要

- **研发**：把本章涉及的 API、配置键与默认值记入团队 Wiki；变更前评估对上下游 Topic 与消费者组的影响。
- **运维**：将关键指标接入告警与值班手册；发布与扩缩容步骤可回滚。
- **测试**：用例覆盖正常、重复、乱序、迟到与故障注入；与产品对齐 SLA 语言（如 RPO/RTO、可接受重复）。


---

## 附录（可选）

### 研发：设计评审问题清单

- 业务有序单元对应的 **Key** 是什么？是否出现在 JSON 中但未设 Key？
- Headers 是否包含 **trace-id**、**schema 版本** 等轻量元数据？
- 生产是否计划 **`acks=all`** 与 **`enable.idempotence`**（第 14 章）？
- 多 Topic 场景下，**Producer 实例与配置**如何隔离与复用？

### 测试：用例模板

- 正常：带 Key 消息进入预期分区（与第 3 章断言一致）。
- Header：消费端读取 `headers` 与发送一致。
- 异常：Broker 不可达时重试与超时行为（与第 14 章衔接）。
- 回归：变更序列化或 Key 策略后，**分区分布**与**有序性**用例是否仍通过。

### 运维：变更与告警映射

- **生产端发送失败率上升**：与 **ISR、网络、认证**、**Topic 存在性** 联合排查（第 4、10、12 章）。
- **客户端重试激增**：观察是否与 **Broker 抖动**、**元数据过期**、**批量过大** 相关；必要时对比 **request.timeout.ms** 与 **delivery.timeout.ms**（第 14 章细调）。
