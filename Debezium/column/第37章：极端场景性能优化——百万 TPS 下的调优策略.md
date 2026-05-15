# 第37章：极端场景性能优化——百万 TPS 下的调优策略

## 1. 项目背景

某头部电商双 11 大促期间，核心交易库的写入量从日常 2000 QPS 飙升至 12 万 TPS（每秒事务数）。Debezium 需要将 200+ 张核心业务表的实时变更同步到 15 个下游系统（实时风控、实时大屏、推荐系统、BI 报表、财务对账等）。在默认配置下，P99 端到端延迟从日常 1 秒飙升至 30 秒——风控系统的反欺诈检测窗口被拉长了 30 倍，足够欺诈订单完成支付和发货。

运维团队紧急调参：先改 `max.batch.size` 从 2048 到 8192，Connector 开始报 `RecordTooLargeException`；再改 `max.queue.size` 到 32768，JVM 堆内存吃紧，GC 频率从每 5 分钟一次变成每 30 秒一次；最后把 `compression.type` 改成 zstd，CPU 飙到 95%，Connector 直接 OOM。三次"盲调"非但没有提升性能，反而让延迟更差了。

极端场景下的 CDC 性能优化不是调几个参数就能解决的，而是需要在 **OS → Kafka → Connect → 数据库**四个层面进行系统性逐层诊断和突破。本章以"P99 延迟从 30s 降到 1s 以内，吞吐量从 12 万 TPS 提升到 85 万 TPS"为实战目标，展示完整的四层调优方法论、火焰图分析技巧、以及参数之间的联动关系。

### 四层调优金字塔

```
          ┌──────────────┐
          │    OS 层      │  ← ulimit -n, TCP buffer, vm.swappiness
          │  硬限制 - 突破  │
         ┌┴──────────────┴┐
         │   Kafka 层     │  ← 分区数, compression, max.message.bytes
         │  吞吐天花板     │
        ┌┴────────────────┴┐
        │   Connect 层     │  ← queue, batch, poll, JVM GC
        │  调优主战场       │
       ┌┴──────────────────┴┐
       │     数据库层        │  ← binlog format, group commit, sync
       │   源头减负          │
       └────────────────────┘
```

**调优漏斗规则**：从底层到上层——底层是硬限制（OS 文件描述符不够，上层调什么都没用），上层是软调优（参数之间的联动权衡）。

## 2. 项目设计——三人对话

**（双 11 大促前一天，运维小张盯着 Grafana 上几乎垂直向上的 Lag 曲线，满头大汗）**

**小胖**（拿着一包薯片走进来）："小张你怎么了？你的 Lag 曲线看起来像火箭发射。"

**小张**："比火箭还快！领导说如果 2 小时内调不好，就得把下游 15 个系统全部切回老方案——那个延迟 5 分钟的 Canal 脚本。我可不想倒退回石器时代。但每次我改参数，性能不但没提升反而更差了——我是不是改错了方向？"

**大师**（过来看了看监控）："你没有改错方向——你只是改了一个参数但没联动调整其他参数。举个经典例子：你把 `max.batch.size` 从 2048 调到 8192，单批数据量大了 4 倍。但那批数据大小 = 8192 条 × 120 bytes（Avro）≈ 1MB。如果 Kafka Broker 的 `max.message.bytes` 还是默认的 1MB——消息刚好卡在边界上，时而过时而被拒。这就是'联动参数陷阱'。"

**小张**："啊！那我应该先调哪些？后调哪些？有没有一个顺序？"

**大师**："**BQNJ 调优顺序**——先分区（Partition）、再队列（Queue）、后批次（Batch）、最后 JVM。这就像你开车——先确定有几条车道（分区），再决定每辆车能装多少货（队列 + 批次），最后保证发动机马力足够（JVM）。"

```
BQNJ 优先级：
1. P (Partition) — Topic 分区数 = 并行消费的物理上限（不改这个，其他白调）
2. Q (Queue) — max.queue.size = 背压容忍度（太小就变成了"一碰就满"）
3. B (Batch) — max.batch.size + snapshot.fetch.size = 吞吐效率
4. J (JVM) — heap + GC = 引擎马力（前三个调了必须联动调 JVM）
```

**小白**："大师，为什么 `binlog_row_image` 要从 FULL 改成 NOBLOB？不会丢数据吗？"

**大师**："NOBLOB 只省略 BLOB/TEXT 列在 binlog 的 `before` 镜像中的完整内容，不影响 `after` 镜像和所有非 BLOB 列。比如你的 orders 表有个 `order_attachment` 字段是 LONGBLOB（存订单附件的 PDF），每次 UPDATE 订单状态时，如果 `binlog_row_image=FULL`，binlog 会把这个几十 KB 的 PDF 也写入 `before` 镜像——但实际上你只改了 `status` 字段。改成 NOBLOB 后，binlog 只记录 `before` 中 BLOB 列的元数据（长度、是否变更），不记录完整内容——binlog 体积瞬间减 30-50%，Connector 的解析开销也大幅降低。**业务数据一个字节都不会丢**。"

**技术映射**：binlog_row_image = 监控摄像头的录像质量——FULL 是 4K 无损（录下每帧每个像素），NOBLOB 是智能编码（静止背景只录变化部分），MINIMAL 是只录变化区域。对于"只想看到谁进了房间"（CDC 同步）的需求，智能编码完全够用，不需要 4K 无损。

**小胖**："那 ZGC 和 G1GC 到底选哪个？我在网上看到有人说 ZGC 不稳定？"

**大师**："那是 JDK 11-15 时期的老黄历了。JDK 17+ 的 ZGC 已经非常稳定。直接上数据——"

| 指标 | G1GC (默认) | ZGC (JDK 17+) | 选谁 |
|------|-----------|--------------|------|
| GC 暂停 (P99) | 50-200ms | < 10ms | **ZGC 碾压** |
| 吞吐影响 | < 3% | < 5% | 几乎持平 |
| 最大堆支持 | < 32GB 推荐 | 16TB | 都够用 |
| 是否分代 | 是 | JDK 21+ 支持分代 ZGC | ZGC 更好 |
| CPU 核数要求 | 任意 | 建议 ≥ 4 核 | 生产都满足 |

**小张**："那压缩算法呢？snappy、zstd、gzip、lz4 一共 4 个，我选哪个？"

**大师**：

"这个要看你 CPU 余量。给你一个决策表——"

| 压缩算法 | 压缩率 | CPU 开销 | 适用场景 |
|---------|--------|---------|---------|
| `none` | 0% | 0% | 内网带宽充足（10Gbps+），不需要压缩 |
| `lz4` | 40-50% | 1-2% | 极低延迟场景（< 5ms），CPU 紧张 |
| `snappy` | 50-60% | 3-5% | **通用推荐**，CPU 和压缩率的最佳平衡 |
| `zstd` | 70-80% | 8-12% | 极限压缩，跨机房（带宽瓶颈） |
| `gzip` | 60-70% | 15-20% | **不推荐**，CPU 成本太高 |

"如果 Connector CPU 余量 > 30%，选 zstd 获得最高压缩率；CPU 在 15-30%，选 snappy 平衡；CPU < 15%，先解决 CPU 瓶颈再考虑压缩。"

---

## 3. 项目实战

### 环境准备

```bash
# 创建性能测试大表
docker exec mysql mysql -uroot -proot1234 inventory << 'SQL'
DROP TABLE IF EXISTS bench_large;
CREATE TABLE bench_large (
    id INT PRIMARY KEY AUTO_INCREMENT,
    name VARCHAR(255) NOT NULL,
    value DECIMAL(10,2),
    stock INT DEFAULT 0,
    category VARCHAR(100),
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
-- 插入 50 万行测试数据（存储过程代码省略）
SQL
```

### 步骤1：建立性能基线——默认配置的快照耗时

**目标**：使用全默认参数对 50 万行表执行快照，记录耗时作为 Baseline。

```bash
echo "===== 基线测试开始 $(date +%H:%M:%S) ====="
curl -X POST http://localhost:8083/connectors -H "Content-Type: application/json" -d '{
  "name": "perf-baseline",
  "config": {
    "connector.class": "io.debezium.connector.mysql.MySqlConnector",
    "database.hostname": "mysql", "database.port": "3306",
    "database.user": "debezium", "database.password": "dbz1234",
    "database.server.id": "184371", "topic.prefix": "perf_base",
    "table.include.list": "inventory.bench_large",
    "schema.history.internal.kafka.bootstrap.servers": "kafka:9092",
    "schema.history.internal.kafka.topic": "schema-changes.perf",
    "snapshot.mode": "initial",
    "snapshot.fetch.size": "2000",
    "max.batch.size": "2048",
    "max.queue.size": "8192",
    "poll.interval.ms": "500",
    "compression.type": "none",
    "topic.creation.default.partitions": "1"
  }
}'
# 监控快照完成 → 停止计时 → 基线: ~95 秒
```

### 步骤2：OS 层极致调优

**目标**：解除操作系统层面的硬限制。

```bash
# 1. 文件描述符上限 —— 必须 > 100000（Connector Task + Kafka Broker + JVM 内部线程）
ulimit -n 1000000

# 2. TCP 缓冲区 —— 减少小包传输，提升批量发送效率
sysctl -w net.core.rmem_max=134217728     # 接收 128MB
sysctl -w net.core.wmem_max=134217728     # 发送 128MB
sysctl -w net.ipv4.tcp_rmem="4096 87380 134217728"
sysctl -w net.ipv4.tcp_wmem="4096 65536 134217728"

# 3. 减少 Swap —— GC 时如果触发 Swap，停顿可能数秒
sysctl -w vm.swappiness=1                 # 只在内存几乎耗尽时才用 Swap
```

### 步骤3：数据库层极限调优

**目标**：从源头减少 binlog 体积和 MySQL 磁盘 I/O 压力。

```sql
-- 1. 减少 binlog 内容量
SET GLOBAL binlog_row_image = 'NOBLOB';       -- 减 30-50% binlog 体积
SET GLOBAL binlog_row_metadata = 'MINIMAL';    -- 减 TableMap 元数据信息

-- 2. 减少磁盘 fsync 次数（牺牲少量持久性，换来大量吞吐）
SET GLOBAL sync_binlog = 0;                    -- 不实时 fsync，由 OS 调度
SET GLOBAL binlog_group_commit_sync_delay = 1000;  -- 1ms 延迟窗口内合并 fsync
SET GLOBAL binlog_group_commit_sync_no_delay_count = 100;  -- 或攒够 100 个事务则立即提交
```

### 步骤4：JVM 极限调优

**目标**：使用 ZGC 实现亚 10ms GC 暂停，释放 CPU 给业务线程。

```yaml
# docker-compose.yml 或 K8s env
environment:
  KAFKA_HEAP_OPTS: >-
    -Xms4g -Xmx8g
    -XX:+UseZGC
    -XX:ZCollectionInterval=5
    -XX:+ZGenerational
    -XX:ConcGCThreads=2
```

### 步骤5：Connect Worker 极限调优

**目标**：调大队列和批次，配合 snappy/zstd 压缩，最大化吞吐。

```json
{
  "name": "perf-extreme",
  "config": {
    "connector.class": "io.debezium.connector.mysql.MySqlConnector",
    "snapshot.fetch.size": "20000",
    "max.queue.size": "131072",
    "max.batch.size": "32768",
    "poll.interval.ms": "50",
    "compression.type": "zstd",
    "max.in.flight.requests": "5",
    "topic.creation.default.partitions": "12",
    "tombstones.on.delete": "false"
  }
}
```

### 步骤6：火焰图定位热点函数

**目标**：通过 async-profiler 生成 CPU 火焰图，定位最耗时的函数。

```bash
# 安装 async-profiler
wget https://github.com/async-profiler/async-profiler/releases/download/v3.0/async-profiler-3.0-linux-x64.tar.gz
tar -xzf async-profiler-3.0-linux-x64.tar.gz

# 对 Kafka Connect Worker 进程采集 60 秒 CPU profile
./profiler.sh -d 60 -f /tmp/flamegraph.html $(pgrep -f ConnectDistributed)

# 在火焰图中寻找"最宽的方块" → CPU 占比最大的函数
# 常见热点及优化方向：
#
# JsonConverter.fromConnectData() — 25%+ CPU
#   → 切换到 Avro Converter（省 25-40% CPU）
#
# KafkaProducer.doSend() — 20%+ CPU
#   → 增大 max.batch.size → 减少 send() 调用次数
#   → 增大 linger.ms → 让更多消息打包到同一批次
#
# EventDeserializer.deserialize() — 30%+ CPU
#   → binlog_row_image=NOBLOB → 减少反序列化的数据量
#
# OffsetContext.flush() — 5%+ CPU 且锯齿状周期出现
#   → offset.flush.interval.ms 从 60s 调到 300s
```

### 步骤7：性能对比验证

| 指标 | 默认配置 | 极限调优 | 提升 |
|------|---------|---------|------|
| P99 端到端延迟 | 29 秒 | 0.8 秒 | **36x** |
| 吞吐量 | 12 万 TPS | 85 万 TPS | **7x** |
| Full GC 频率 | 每 5 分钟 / 2 秒停顿 | 无 (ZGC < 10ms) | - |
| CPU 使用率 | 85% | 60% | -25% |
| 磁盘 IO Wait | 15% | 3% | -12% |
| Kafka 磁盘日增量 | 500GB | 150GB（zstd 压缩 70%） | -70% |

### 可能遇到的坑及解决方法

| 坑 | 现象 | 根因 | 解决方法 |
|----|------|------|---------|
| 调大 batch.size 后 Kafka 拒绝 | `RecordTooLargeException` | 单批大小 > `max.message.bytes`(1MB) | 联动调大 Broker: `max.message.bytes=10485760`（10MB） |
| snapshot.fetch.size=50000 OOM | Connector JVM GC 频繁 | 单次从 MySQL 读取过多数据到内存 | 安全上限 20000，保守 10000 |
| zstd 压缩后 CPU 飙升到 95% | Connector 变慢 | 压缩消耗 8-12% CPU，CPU 已紧张 | 降级到 snappy (3-5% CPU)，或先扩容 CPU |
| 10 个参数一起改，不知道哪个生效 | 性能变化无法归因 | 一次改了太多参数 | **每次只改 1-2 个参数，测完再改下一个** |
| 调大分区后老消费者 offset 丢失 | 消费者重新从 0 开始 | Kafka 分区数变大后，新分区的 offset 默认为 0 | `kafka-consumer-groups --reset-offsets --to-latest` |

---

## 4. 项目总结

### 优点 & 缺点（极限调优 vs 默认 vs 容器化 auto-scale）

| 维度 | 默认配置 | 极限调优 | 自动扩容 |
|------|---------|---------|---------|
| 吞吐量 | ★★ 5000 events/s | ★★★★★ 85000 events/s | ★★★★ 按需伸缩 |
| 延迟 (P99) | ★★ < 10s | ★★★★★ < 1s | ★★★★ < 2s |
| 资源效率 | ★★★★★ 512MB | ★★★ 8GB | ★★★★ 按需分配 |
| 运维复杂度 | ★★★★★ 零配置 | ★★ 需 15+ 参数 | ★★★ 需 K8s HPA |
| 成本 | ★★★★★ 最低 | ★★★ 较高 | ★★★★ 按需 |

### 适用场景

1. **双 11/618 大促**：流量是日常 10-50 倍，提前 1 周完成调优并压测验证
2. **大表全量快照**：> 5000 万行表的首次快照从"跑一晚上"缩短到"跑 1 小时"
3. **跨机房/跨云同步**：带宽是瓶颈，zstd 压缩能省 70% 带宽费
4. **实时风控/实时大屏**：P99 延迟必须 < 1s，超过则业务不可用
5. **大规模 Topic 场景**：200+ Topic 的场景，压缩省下的磁盘空间非常可观

### 不适用场景

1. **开发/测试环境**：数据量小，默认配置足够
2. **资源极度受限环境**：内存 < 2GB 时调大 queue.size 反而容易 OOM

### 注意事项

- **参数联动**：`max.batch.size ↑ → 必须联动调 Kafka Broker 的 max.message.bytes`，否则消息被拒
- **压缩有代价**：zstd 省 70% 带宽但加 10% CPU，需要根据实际 CPU 余量选择
- **分区数是物理上限**：Topic 只有 1 个分区时，Connector 调到天上也无法并行消费

### 常见踩坑经验

1. **"我一口气把 10 个参数全改了，性能反而更差"**——必须一次只改 1-2 个参数，梯度测试。先用 Baseline → 改 `max.queue.size` → 测 → 改 `max.batch.size` → 测 → 以此类推。
2. **"zstd 压缩后 CPU 飙升，延迟反而更差"**——CPU 基线已经 > 85%，zstd 再吃 10% → CPU 饱和 → 所有操作都排队。先扩容 CPU 或降级到 snappy。
3. **"火焰图中 JsonConverter 占了 35% CPU"**——JSON 序列化是大户。JSON → Avro 切换后，序列化 CPU 从 35% 降到 10%，延迟 P99 从 8s 降到 2s。

### 思考题

1. ZGC 下仍然间歇性出现每 5 分钟的 3 秒延迟尖刺——不像是 GC 导致的。通过 JFR（Java Flight Recorder）排查后，发现尖刺与 Connect Worker 的 `offset.flush()` 操作高度相关。`offset.flush()` 在 flush 到 Kafka 时可能阻塞 poll 线程——如何重新设计 offset flush 机制来避免阻塞？

2. 为什么 `compression.type=zstd` 虽然增加了 10% CPU，但整体的端到端延迟反而从 10s 降到了 2s？（提示：网络 I/O 等待也计入延迟——压缩后数据量小了，Kafka Broker 的磁盘写入和网络传输都快了。）

**（第36章思考题答案）**

1. Redis Connector offset 可通过两种方式持久化：① 模仿 Debezium 的 OffsetContext 设计——将最后处理的时间戳或 Redis 的 `LASTSAVE` 时间戳写入 Kafka 的 offset Topic；② 在 Redis 中维护一张 `debezium_offset` key，每次 poll 后更新其值为 `System.currentTimeMillis()`，重启时从该 key 读取最后位点，用 Redis Stream 的 `XREAD COUNT ... BLOCK ...` 从该时间点后恢复。

2. 不依赖 `jedis.get()` 查询当前值的方法：Redis Keyspace Notifications 本身不携带 value。替代方案：① 改用 Redis Stream 作为变更捕获机制（`XADD` 时可以带上完整数据）；② 在应用层双写——业务代码 SET 后立即发一条包含完整 value 的 Kafka 消息；③ 如果只关心 key 名和操作类型（不关心 value），完全不需要 GET。

---

> **推广提示**：将本章的调优过程记录为"性能优化 A/B 实验报告"——包含 Baseline 数据、每一步的改动和对应的性能变化、火焰图对比。存入团队的"性能优化案例库"，下次大促时直接复用已验证的参数组合。大促前 1 周用 Sysbench 做一次全链路压测，P99 延迟超过 3s 则自动触发扩容。
