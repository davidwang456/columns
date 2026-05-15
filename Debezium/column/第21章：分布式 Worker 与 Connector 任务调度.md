# 第21章：分布式 Worker 与 Connector 任务调度

## 1. 项目背景

某支付平台的 CDC 管道从 3 个 Connector 扩展到 30 个 Connector 后，运维团队发现一个诡异的现象：所有 30 个 Connector 的 Task 都被调度到了 Worker-1 节点上，而 Worker-2 和 Worker-3 几乎空转。高峰期 Worker-1 的 CPU 飙到 95%，GC 频繁，最终 OOM 重启。而每次重启又会触发 Rebalance——所有 Connector 短暂中断。整个 CDC 集群每隔 2 小时就"抽搐"一次，端到端延迟从 1 秒飙到 10 分钟。

排查后发现：运维在三个月前新增 Worker-2 和 Worker-3 时，把它们的 `CONNECT_GROUP_ID` 配成了不同的值——导致三个 Worker 不在同一个 Consumer Group 里，Leader 机制失效，所有 Connector 只能在创建它们的那个 Worker 上运行。这就是不懂分布式 Worker 调度机制带来的代价。

本章将深入 Kafka Connect 的分布式架构——Worker 集群、Leader 选举、Task 分配算法、Rebalance 机制、以及三个内部系统 Topic 的作用。学完本章，你将能自如管理 3-30 个 Worker 节点的 Connect 集群，并理解 Rebalance 的触发条件和优化策略。

## 2. 项目设计——三人对话

**（周三上午，运维小张一脸疲惫地找到大师）**

**小张**："大师，我们的 Kafka Connect 集群有 3 台机器，但我发现 Connector 全在 Worker-1 上跑。我明明配了 3 个 Worker 节点，为什么没有负载均衡？"

**大师**："你三个 Worker 的 `CONNECT_GROUP_ID` 是什么？一样吗？"

**小张**（翻出配置）："Worker-1 是 `connect-cluster-prod`，Worker-2 是 `connect-cluster-prod-2`，Worker-3 是 `connect-cluster-prod-3`。"

**大师**："这就对了。`CONNECT_GROUP_ID` 是 Worker 集群的'群聊名称'。只有相同 Group ID 的 Worker 才被识别为同一个集群。你把三个 Worker 的 Group ID 改成同一个值，Leader 选举和 Task 分配机制才会生效。"

**小胖**："等等，Leader 选举又是什么？我以为是 ZooKeeper 做的选举？"

**大师**："Kafka Connect 的 Leader 选举不依赖 ZooKeeper——它是通过 Kafka 自身的 Consumer Group 协议实现的。当多个 Worker 以相同的 `CONNECT_GROUP_ID` 连接到 Kafka 时，Kafka 的 Group Coordinator 会在它们之间选举一个 Leader。Leader Worker 负责读取 `connect-configs` Topic 中的所有 Connector 配置，然后将 Task 分配给各个 Worker。"

**小白**："那 Rebalance 是什么时候发生的？我总觉得这个过程很可怕，好像所有 Connector 都会暂停。"

**大师**："Rebalance 有三种触发条件：① Worker 加入或离开集群（如扩容或宕机）；② Connector 配置变更导致 Task 数量改变；③ Worker 心跳超时（超过 `session.timeout.ms`，默认 10 秒）。Rebalance 期间——所有 Task 会被暂停，Leader 重新分配 Task 给各 Worker，然后 Task 从 offset 继续。这个过程的暂停时间通常小于 30 秒。"

**技术映射**：Rebalance = 公交公司的车辆重新调度。原有路线上的所有公交车（Task）全部回场（暂停），调度中心（Leader Worker）按照新的车辆数量重新分配路线，然后再派车出发。这个过程必然有短暂的服务中断——优化目标是减少 Rebalance 的频率和时长。

**小胖**："那单 Worker 环境中，Rebalance 会怎样？我们之前的基础篇都是单 Worker。"

**大师**："单 Worker 环境下，没有'其他 Worker'来分担 Task，所以 Rebalance 几乎即时完成——Leader 就是它自己，Task 全部分配给自己。但当 Worker 本身重启时，确实有 30-60 秒的中断。"

**小张**："怎么避免频繁 Rebalance？我们高峰期每 2 小时就 Rebalance 一次。"

**大师**："三个优化点：
1. **增加 `scheduled.rebalance.max.delay.ms`**（默认 5 分钟）→ 新 Worker 加入后等 5 分钟不触发 Rebalance（避免短暂加入就退出的抖动）
2. **减少 Connector 配置变更的频率**→ 每次改 Connector 配置（PUT /config）都触发 Rebalance
3. **调整 `session.timeout.ms`**（默认 10 秒）→ 如果 Worker 因为 Full GC 暂时停止心跳，10 秒内恢复就不触发 Rebalance。你高峰期 CPU > 90%，Full GC 可能 > 10 秒，所以频繁触发。"

---

## 3. 项目实战

### 环境准备

虽然是单 Worker Docker 环境，但我们仍可以通过 API 理解多 Worker 机制。我们将观察单 Worker 的行为，并讲解多 Worker 时的差异。

```bash
# 查看当前 Worker 信息
curl http://localhost:8083/ | python3 -m json.tool
# 单 Worker 环境：version, commit, kafka_cluster_id

# 查看已注册的 Connector 和它们的 Task 所在 Worker
curl http://localhost:8083/connectors/avro-orders-connector/status | python3 -c "
import sys, json
s = json.load(sys.stdin)
print('Connector Worker:', s['connector']['worker_id'])
for t in s['tasks']:
    print(f'  Task {t[\"id\"]}: state={t[\"state\"]}, worker={t[\"worker_id\"]}')"
```

### 步骤1：理解 Connect 的三个内部系统 Topic

**目标**：理解 `connect-configs`、`connect-offsets`、`connect-statuses` 三个 Topic 的职责。

```bash
# 1. connect-configs —— 存储所有 Connector 的配置（Key=connector name, Value=JSON config）
docker exec kafka kafka-console-consumer --bootstrap-server localhost:9092 \
  --topic connect-configs --from-beginning --max-messages 3 \
  --property print.key=true --property key.separator=" → " 2>/dev/null

# 2. connect-offsets —— 存储所有 Connector 的 offset（Key=connector+partition, Value=offset 详情）
docker exec kafka kafka-console-consumer --bootstrap-server localhost:9092 \
  --topic connect-offsets --from-beginning --max-messages 3 \
  --property print.key=true --property key.separator=" → " 2>/dev/null

# 3. connect-statuses —— 存储 Worer + Connector + Task 的当前状态（compact 策略）
docker exec kafka kafka-topics --bootstrap-server localhost:9092 --describe \
  --topic connect-statuses | head -5

# 检查这三个 Topic 的复制因子和清理策略
for t in connect-configs connect-offsets connect-statuses; do
    echo "=== $t ==="
    docker exec kafka kafka-configs --bootstrap-server localhost:9092 \
      --entity-type topics --entity-name $t --describe 2>/dev/null | grep -E "cleanup.policy|replication.factor"
done
```

### 步骤2：创建多 Task 的 Connector 观察分配

**目标**：设置 `tasks.max=3`，观察 3 个 Task 在单 Worker 环境下的分配行为。

```bash
curl -X POST http://localhost:8083/connectors \
  -H "Content-Type: application/json" \
  -d '{
    "name": "multi-task-connector",
    "config": {
      "connector.class": "io.debezium.connector.mysql.MySqlConnector",
      "tasks.max": "3",
      "database.hostname": "mysql",
      "database.port": "3306",
      "database.user": "debezium",
      "database.password": "dbz1234",
      "database.server.id": "184211",
      "topic.prefix": "multitask",
      "table.include.list": "inventory.orders,inventory.products,inventory.analytics_events",
      "schema.history.internal.kafka.bootstrap.servers": "kafka:9092",
      "schema.history.internal.kafka.topic": "schema-changes.multitask",
      "snapshot.mode": "initial"
    }
  }'

sleep 30

# 查看 Task 分布
curl http://localhost:8083/connectors/multi-task-connector/status | python3 -c "
import sys, json
s = json.load(sys.stdin)
print(f'Total tasks: {len(s[\"tasks\"])}')
for t in s['tasks']:
    print(f'  Task {t[\"id\"]}: {t[\"state\"]} @ {t[\"worker_id\"]}')"
# 预期：3 个 Task 都在 connect:8083（单 Worker 环境）
# 多 Worker 环境：3 个 Task 均匀分配到 3 个 Worker
```

### 步骤3：模拟 Connector 配置变更触发 Rebalance

**目标**：修改 `poll.interval.ms` 参数触发 Rebalance，观察 Task 状态。

```bash
# 修改配置——从 REST API 观察状态变化
curl -X PUT http://localhost:8083/connectors/multi-task-connector/config \
  -H "Content-Type: application/json" \
  -d '{
    "connector.class": "io.debezium.connector.mysql.MySqlConnector",
    "tasks.max": "3",
    "database.hostname": "mysql",
    "database.port": "3306",
    "database.user": "debezium",
    "database.password": "dbz1234",
    "database.server.id": "184211",
    "topic.prefix": "multitask",
    "table.include.list": "inventory.orders,inventory.products",
    "schema.history.internal.kafka.bootstrap.servers": "kafka:9092",
    "schema.history.internal.kafka.topic": "schema-changes.multitask",
    "snapshot.mode": "initial",
    "poll.interval.ms": "200"
  }'

# 立刻查看状态——可能看到短暂的 UNASSIGNED 或 REBALANCING
sleep 2
curl http://localhost:8083/connectors/multi-task-connector/status | python3 -c "
import sys, json
s = json.load(sys.stdin)
print('Connector state:', s['connector']['state'])"

sleep 10
curl http://localhost:8083/connectors/multi-task-connector/status | python3 -c "
import sys, json
s = json.load(sys.stdin)
print('After restart - Connector state:', s['connector']['state'])"
```

### 步骤4：Worker 心跳与 Rebalance 参数优化

**目标**：理解 Connect Worker 的关键配置参数。

```yaml
# docker-compose.yml 中的 Connect Worker 关键配置
environment:
  # Worker 集群标识
  CONNECT_GROUP_ID: "debezium-prod-cluster"
  
  # Leader 发现与会话保持
  CONNECT_CONFIG_STORAGE_TOPIC: "connect-prod-configs"
  CONNECT_OFFSET_STORAGE_TOPIC: "connect-prod-offsets"
  CONNECT_STATUS_STORAGE_TOPIC: "connect-prod-statuses"
  
  # 复制因子（确保 HA）
  CONNECT_CONFIG_STORAGE_REPLICATION_FACTOR: 3
  CONNECT_OFFSET_STORAGE_REPLICATION_FACTOR: 3
  CONNECT_STATUS_STORAGE_REPLICATION_FACTOR: 3
  
  # Rebalance 延迟（减少抖动）
  CONNECT_SCHEDULED_REBALANCE_MAX_DELAY_MS: 300000  # 5 分钟
  
  # 心跳与会话超时
  CONNECT_HEARTBEAT_INTERVAL_MS: 10000   # 每 10 秒心跳
  CONNECT_SESSION_TIMEOUT_MS: 30000      # 30 秒无心跳→触发 Rebalance
```

### 步骤5：Rebalance 监控脚本

**目标**：编写脚本监控 Rebalance 事件和 Task 分配变化。

```bash
cat > monitor_rebalance.py << 'EOF'
#!/usr/bin/env python3
import requests, json, time, hashlib

CONNECT_URL = "http://localhost:8083"
last_task_map = {}

def get_task_map():
    """获取当前 Task 分布：{connector_name: {task_id: worker_id}}"""
    task_map = {}
    for name in requests.get(f"{CONNECT_URL}/connectors").json():
        status = requests.get(f"{CONNECT_URL}/connectors/{name}/status").json()
        task_map[name] = {}
        for task in status.get("tasks", []):
            task_map[name][task["id"]] = task.get("worker_id", "unknown")
    return task_map

while True:
    current = get_task_map()
    h_prev = hashlib.md5(json.dumps(last_task_map, sort_keys=True).encode()).hexdigest()
    h_curr = hashlib.md5(json.dumps(current, sort_keys=True).encode()).hexdigest()
    
    if last_task_map and h_prev != h_curr:
        print(f"\n⚠️  {time.strftime('%H:%M:%S')} Rebalance detected!")
        print("  Previous distribution:")
        for name, tasks in last_task_map.items():
            print(f"    {name}: {tasks}")
        print("  Current distribution:")
        for name, tasks in current.items():
            print(f"    {name}: {tasks}")
    
    last_task_map = current
    time.sleep(5)
EOF

echo "Run with: python3 monitor_rebalance.py"
```

### 可能遇到的坑及解决方法

| 坑 | 现象 | 根因 | 解决 |
|----|------|------|------|
| Worker 不加入集群 | 单 Worker 运行，其余空闲 | `CONNECT_GROUP_ID` 不一致 | 统一 Group ID |
| Rebalance 抖动 | 每隔几分钟 Rebalance | 心跳超时太短 / Full GC 太久 | 增大 `session.timeout.ms` |
| Task 分配不均衡 | Worker-1 跑 20 个 Task，Worker-2 跑 0 个 | 某些 Connector 的 `tasks.max=1`，Leader 无法拆分 | 增大 `tasks.max` 或接受不均衡（单 Task Connector 天然串行） |
| Leader 选举失败 | 集群无 Leader，所有 Connector UNASSIGNED | Kafka Broker 不可达 / 网络分区 | 检查网络 + Kafka 健康状态 |

---

## 4. 项目总结

### 优点 & 缺点

| 维度 | 单 Worker | 多 Worker（分布式） |
|------|----------|-------------------|
| 部署复杂度 | ★★★★★ 极简 | ★★★☆☆ 需规划 Group ID |
| 高可用 | ☆☆☆☆☆ 无 | ★★★★★ 自动 Failover |
| Task 负载均衡 | ☆☆☆☆☆ 无 | ★★★★☆ 自动分配 |
| Rebalance 影响 | ★★★★☆ 无影响（无其他 Worker） | ★★★☆☆ 有短暂中断 |
| 运维成本 | ★★★★★ 低 | ★★★☆☆ 需监控 Rebalance |

### 适用场景

1. **生产环境**：至少 3 个 Worker，确保单节点宕机后 Task 自动迁移
2. **多 Connector 共享集群**：30 个 Connector 分散到多个 Worker，避免单点压力
3. **滚动升级**：新版本 Worker 逐个加入，Task 逐步迁移，零停机升级
4. **跨 AZ 容灾**：Worker 分布在 3 个可用区，任一个 AZ 挂掉不影响 CDC

### 不适用场景

1. **开发和测试环境**：单 Worker 即可，不需要 HA
2. **Connector 数量 < 5**：单 Worker 完全够用，多 Worker 增加了 Rebalance 的复杂度

### 注意事项

- **三个内部 Topic 的 `cleanup.policy` 必须是 `compact`，尤其是 `connect-offsets`**——如果用 `delete`，offset 过期后 Connector 会重做快照
- **`session.timeout.ms` 不宜太小（默认 10 秒太小）**——Full GC 或网络轻微抖动可能导致误判 Rebalance
- **Leader Worker 故障后，新 Leader 从 `connect-configs` Topic 读取所有 Connector 配置——不需要额外的手动恢复**

### 常见踩坑经验

1. **"增加了 Worker 但 Task 没有重新分配"**——根因是 `scheduled.rebalance.max.delay.ms` 设得太大（如 30 分钟），新 Worker 加入后需要等这么久才触发 Rebalance。
2. **"Worker 重启后所有 Connector 都 UNASSIGNED 了 5 分钟"**——根因是 `connect-offsets` 和 `connect-configs` Topic 的 replication factor 为 1，Broker 故障时这些 Topic 不可用。必须设置 ≥ 3 的 replication factor。
3. **"集群中 Worker 数量是偶数（2,4,6），脑裂风险"**——Kafka Connect 的 Leader 选举不是多数派投票制，偶数节点也不会脑裂。但建议奇数个 Worker（3,5,7），确保可用性最大化。

### 思考题

1. 如果一个 Connect Worker 因为 Full GC 而暂时停止响应（心跳超时 35 秒），Leader 认为它挂了并触发 Rebalance。30 秒后 Full GC 结束，Worker 恢复响应。此时集群中出现"两个 Worker 声称负责同一个 Task"的冲突——Kafka Connect 如何解决这个冲突？

2. 3 个 Worker 的集群中，Leader Worker 突然崩溃（进程被 kill -9）。剩余 2 个 Worker 如何选举新 Leader？新 Leader 从哪里获取当前的任务分配状态？消费者端的 offset 是否受到影响？

**（第20章思考题答案）**

1. Schema Registry 的兼容性检查是基于"当前最新版本 Schema"进行的单向检查——只检查新注册的 Schema 是否与最新版本兼容，不追溯历史版本。消费者通过消息头部嵌入的 4-byte Schema ID（Avro 二进制格式的 magic byte + ID）来知道每条消息应该用哪个版本反序列化。消费者在本地维护 Schema Cache（`{schemaId → Schema}`），读消息时先解析 Schema ID，从缓存获取 Schema，再反序列化。这意味着一个消费者可以同时处理 v1 到 v5 的消息而不会出错。

2. `DECIMAL(10,2)` 扩展到 `DECIMAL(12,4)` 在 Avro 中对应的是 `bytes(decimal)` 类型，精度和标的（precision/scale）作为 logical type 属性存在。扩展 precision（10→12）是向后兼容的（新 Schema 可以读旧精度），扩展 scale（2→4）也是兼容的（旧值 `100.00` 的字节表示 → 新 Schema 解析为 `100.0000` 也不会出错）。所以 FULL 策略下 DECIMAL 的 precision/scale 扩展是兼容的。但反过来缩 precision/scale（12→10）可能不兼容。

---

> **推广提示**：运维团队应将本章的 Worker 配置模板化——明确规定不同规模下的 Worker 数量、内存分配、Group ID 命名规范。将 Rebalance 监控脚本集成到 Prometheus 告警中——如果 15 分钟内发生 > 2 次 Rebalance，发送 P1 告警通知相关团队。
