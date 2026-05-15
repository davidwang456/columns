# 第13章：Connector 状态机与故障排查初级手册

## 1. 项目背景

凌晨 3:47，运维报警——"实时数据大屏超过 10 分钟没有更新"。运维小张快速定位到 Kafka Connect，执行 `curl /connectors/orders-connector/status`，状态显示 `FAILED`。翻开日志，只看到一句 `A slave with the same server_uuid/server_id has been connected`。小张不认识这个错误，只能打电话给开发小胖。小胖睡眼惺忪地爬起来，发现是 `database.server.id` 和新部署的另一个 Connector 重复了——一个简单的参数冲突，却因为不知道 Connector 的各种状态和含义，在小张"不认识 → 问开发 → 改配置 → 重启"这个链路上浪费了 45 分钟。

Debezium Connector 的状态机看似简单，但每个状态的背后都有特定的上下文和排查套路。UNASSIGNED 可能是因为 Worker 不够，RUNNING 不代表一切正常（Task 可能已经 FAILED），FAILED 的错误信息可能被截断...本章将带你深入理解 Connector 和 Task 的双层状态模型，掌握 5 种常见故障场景的排查 SOP，让"半夜被叫起来修 CDC"从此成为历史。

### 痛点放大

- **Connector RUNNING ≠ Task RUNNING**：Connector 状态可能显示 RUNNING，但它的某个 Task 已经 FAILED，部分表的数据停止同步
- **FAILED 后盲目重启**：看到 FAILED 就 curl DELETE + POST 重建，如果在错误未修复的情况下重复操作，会导致 offset 被重置、数据重复或丢失
- **错误信息被截断**：Kafka Connect 的 REST API 返回的 `trace` 字段有长度限制，真正的错误原因可能在 Worker 日志的后几十行

---

## 2. 项目设计——三人对话

**（周一早会，小张揉着黑眼圈）**

**小张**："大师，昨晚 Connector 挂了，被领导骂了。我想问一下，这个 FAILED 到底是什么意思？Connector 不会自己恢复吗？"

**大师**："先看状态机的全貌——Connector 有 5 种状态，Task 有 4 种状态，它们是分层管理的。"

```
Connector 状态机：
         ┌──────────┐
         │UNASSIGNED│ ← 刚创建，尚未分配 Worker
         └────┬─────┘
              │ Worker 分配
              ▼
         ┌──────────┐
    ┌───▶│ RUNNING  │───┐
    │    └────┬─────┘   │
    │  暂停   │         │ 故障
    │         ▼         ▼
    │    ┌──────────┐ ┌──────────┐
    │    │ PAUSED   │ │ FAILED   │
    │    └────┬─────┘ └────┬─────┘
    │  恢复   │            │ 手动/自动重启
    │         ▼            ▼
    │    ┌──────────┐ ┌──────────┐
    └────│ RUNNING  │ │DESTROYED │ ← 被删除
         └──────────┘ └──────────┘
```

**小白**："那 Task 的状态呢？一个 Connector 可以有多个 Task 吧？"

**大师**："对。Task 状态相对简单：UNASSIGNED → RUNNING → FAILED。关键是一个 Connector 的总体状态和它的 Task 状态可能是不同的——"

| Connector 状态 | Task 状态 | 实际情况 |
|---------------|----------|---------|
| RUNNING | RUNNING | 一切正常 |
| RUNNING | FAILED | Connector 框架正常但某个 Task 异常 |
| FAILED | FAILED | Connector 整体挂掉 |
| PAUSED | PAUSED | 手动暂停 |

**大师**："最常见的困惑是——看到 Connector 状态是 RUNNING，就以为万事大吉。但其实 Task 可能已经静默 FAILED，而 Connector 的 state 不会自动变成 FAILED。必须同时检查 `/connectors/{name}/status` 返回的 `tasks[].state`。"

**小张**："那昨晚的情况怎么办？FAILED 之后该走什么流程？"

**大师**："记住这个 SOP——"

```
FAILED 排查五步法：

Step 1: 确定故障范围
  curl /connectors/{name}/status  → 看 connector.state + tasks[].state + tasks[].trace

Step 2: 查看详细错误
  docker logs connect | grep -A 20 "ERROR\|Exception" | tail -50

Step 3: 根据错误关键字定位根因
  ├── "Access denied" → 数据库权限问题
  ├── "server_uuid/server_id" → server.id 冲突
  ├── "table not found" → table.include.list 配置错误
  ├── "topic not created" → Kafka ACL/权限问题
  └── "OutOfMemoryError" → JVM 堆内存不足

Step 4: 修复根因（不改配置只修环境）
  不需要 Delete → POST，用 PUT /connectors/{name}/config 修改配置即可

Step 5: 重启 Connector
  POST /connectors/{name}/restart → 重新加载配置 + 从 offset 恢复
```

---

## 3. 项目实战

### 环境准备

沿用之前的测试环境，先部署一个正常的 Connector 用于故障模拟。

```bash
# 创建测试 Connector
curl -X POST http://localhost:8083/connectors \
  -H "Content-Type: application/json" \
  -d '{
    "name": "fault-test-connector",
    "config": {
      "connector.class": "io.debezium.connector.mysql.MySqlConnector",
      "database.hostname": "mysql",
      "database.port": "3306",
      "database.user": "debezium",
      "database.password": "dbz1234",
      "database.server.id": "184131",
      "topic.prefix": "fault_test",
      "table.include.list": "inventory.orders",
      "schema.history.internal.kafka.bootstrap.servers": "kafka:9092",
      "schema.history.internal.kafka.topic": "schema-changes.fault-test",
      "snapshot.mode": "initial"
    }
  }'
```

### 步骤1：模拟故障 1——server.id 冲突

**目标**：人为制造 `database.server.id` 冲突，观察 FAILED 状态并执行排查。

```bash
# 创建第二个 Connector，使用相同的 server.id
curl -X POST http://localhost:8083/connectors \
  -H "Content-Type: application/json" \
  -d '{
    "name": "fault-test-conflict",
    "config": {
      "connector.class": "io.debezium.connector.mysql.MySqlConnector",
      "database.hostname": "mysql",
      "database.port": "3306",
      "database.user": "debezium",
      "database.password": "dbz1234",
      "database.server.id": "184131",
      "topic.prefix": "fault_test2",
      "table.include.list": "inventory.products",
      "schema.history.internal.kafka.bootstrap.servers": "kafka:9092",
      "schema.history.internal.kafka.topic": "schema-changes.fault-test2",
      "snapshot.mode": "initial"
    }
  }'

# 等待几秒后查看状态
sleep 10
curl http://localhost:8083/connectors/fault-test-conflict/status | python3 -m json.tool
```

**预期输出**：
```json
{
  "name": "fault-test-conflict",
  "connector": {"state": "RUNNING", "worker_id": "..."},
  "tasks": [{
    "id": 0,
    "state": "FAILED",
    "trace": "org.apache.kafka.connect.errors.ConnectException: A slave with the same server_uuid/server_id as this slave has connected to the master..."
  }],
  "type": "source"
}
```

**关键观察**：Connector 状态是 RUNNING，但 Task 状态是 FAILED——这是陷阱！

```bash
# Step 2: 查看 Connect Worker 日志
docker logs connect 2>&1 | grep -A 10 "server_uuid"
# 预期看到详细的 MySQL binlog 连接错误

# Step 3: 定位根因 → server.id 冲突
# Step 4: 修复 → 修改 server.id
curl -X PUT http://localhost:8083/connectors/fault-test-conflict/config \
  -H "Content-Type: application/json" \
  -d '{
    "connector.class": "io.debezium.connector.mysql.MySqlConnector",
    "database.hostname": "mysql",
    "database.port": "3306",
    "database.user": "debezium",
    "database.password": "dbz1234",
    "database.server.id": "184132",
    "topic.prefix": "fault_test2",
    "table.include.list": "inventory.products",
    "schema.history.internal.kafka.bootstrap.servers": "kafka:9092",
    "schema.history.internal.kafka.topic": "schema-changes.fault-test2",
    "snapshot.mode": "initial"
  }'

# Step 5: 重启
curl -X POST http://localhost:8083/connectors/fault-test-conflict/restart

# 验证恢复
sleep 10
curl http://localhost:8083/connectors/fault-test-conflict/status | python3 -m json.tool
# 预期：task state = RUNNING
```

### 步骤2：模拟故障 2——数据库权限不足

**目标**：模拟缺少 REPLICATION 权限导致的 FAILED。

```bash
# 创建一个权限不足的 MySQL 账号
docker exec mysql mysql -uroot -proot1234 -e "
  CREATE USER 'limited_user'@'%' IDENTIFIED BY 'limited1234';
  GRANT SELECT ON inventory.* TO 'limited_user'@'%';
  -- 注意：没有 REPLICATION CLIENT, REPLICATION SLAVE 等权限
"

# 创建一个使用此账号的 Connector（预期直接 FAILED）
curl -X POST http://localhost:8083/connectors \
  -H "Content-Type: application/json" \
  -d '{
    "name": "fault-test-permission",
    "config": {
      "connector.class": "io.debezium.connector.mysql.MySqlConnector",
      "database.hostname": "mysql",
      "database.port": "3306",
      "database.user": "limited_user",
      "database.password": "limited1234",
      "database.server.id": "184133",
      "topic.prefix": "fault_perm",
      "table.include.list": "inventory.orders",
      "schema.history.internal.kafka.bootstrap.servers": "kafka:9092",
      "schema.history.internal.kafka.topic": "schema-changes.fault-perm",
      "snapshot.mode": "initial"
    }
  }'

sleep 10
curl http://localhost:8083/connectors/fault-test-permission/status | python3 -m json.tool
# 预期：state=FAILED，trace 中包含 "Access denied" 或 "REPLICATION CLIENT"
```

### 步骤3：模拟故障 3——Snapot 失败后的恢复

**目标**：模拟快照期间表被删除导致 FAILED，然后手动恢复。

```bash
# 创建 Connector 监控临时表
docker exec mysql mysql -uroot -proot1234 inventory -e "CREATE TABLE IF NOT EXISTS temp_sync (id INT PRIMARY KEY AUTO_INCREMENT, data VARCHAR(255)); INSERT INTO temp_sync (data) VALUES ('test1'),('test2'),('test3');"

# 创建 Connector（使用 initial 快照）
curl -X POST http://localhost:8083/connectors \
  -H "Content-Type: application/json" \
  -d '{
    "name": "fault-test-snapshot",
    "config": {
      "connector.class": "io.debezium.connector.mysql.MySqlConnector",
      "database.hostname": "mysql",
      "database.port": "3306",
      "database.user": "debezium",
      "database.password": "dbz1234",
      "database.server.id": "184134",
      "topic.prefix": "fault_snap",
      "table.include.list": "inventory.temp_sync",
      "schema.history.internal.kafka.bootstrap.servers": "kafka:9092",
      "schema.history.internal.kafka.topic": "schema-changes.fault-snap",
      "snapshot.mode": "initial"
    }
  }'

# 在快照进行中删除表（模拟 DBA 误操作）
sleep 5
docker exec mysql mysql -uroot -proot1234 inventory -e "DROP TABLE temp_sync;"

# 查看 Connector 状态
sleep 10
curl http://localhost:8083/connectors/fault-test-snapshot/status | python3 -m json.tool
# 预期：FAILED，"table not found" 相关错误

# 恢复步骤：重建表 → Put 修改 snapshot.mode → Restart
docker exec mysql mysql -uroot -proot1234 inventory -e "CREATE TABLE IF NOT EXISTS temp_sync (id INT PRIMARY KEY AUTO_INCREMENT, data VARCHAR(255)); INSERT INTO temp_sync (data) VALUES ('recovered1');"
```

### 步骤4：编写 Connector 健康检查脚本

**目标**：使用 Python 脚本定期巡检所有 Connector 和 Task 的状态。

```bash
cat > check_connectors.py << 'PYEOF'
#!/usr/bin/env python3
import requests
import sys

CONNECT_URL = "http://localhost:8083"

def check_all_connectors():
    connectors = requests.get(f"{CONNECT_URL}/connectors").json()
    failed = []
    
    for name in connectors:
        status = requests.get(f"{CONNECT_URL}/connectors/{name}/status").json()
        
        # 检查 Connector 状态
        conn_state = status.get("connector", {}).get("state", "UNKNOWN")
        
        # 检查 Task 状态
        for task in status.get("tasks", []):
            task_id = task.get("id", 0)
            task_state = task.get("state", "UNKNOWN")
            
            if task_state == "FAILED":
                trace = task.get("trace", "")[:200]  # 截取前 200 字符
                failed.append(f"  [{name}] Task {task_id}: FAILED - {trace}")
            elif conn_state != "RUNNING":
                failed.append(f"  [{name}] Connector: {conn_state}")
    
    if failed:
        print("❌ FAILED Connectors/Tasks:")
        for f in failed:
            print(f)
        sys.exit(1)
    else:
        print(f"✅ All {len(connectors)} connectors are healthy")

if __name__ == "__main__":
    check_all_connectors()
PYEOF

python3 check_connectors.py
```

### 可能遇到的坑及解决方法

| 坑 | 现象 | 排查方法 |
|----|------|---------|
| Connector RUNNING 但 Task FAILED | 有部分表停止同步 | 必须检查 `tasks[].state`，不要只看 `connector.state` |
| `/restart` 失败 | 返回 409 Conflict | Connector 可能还在 FAILED 中未完全释放状态，等几秒再试 |
| 日志中的错误被截断 | trace 只显示前 200 字符 | 直接查 Worker 日志：`docker logs connect --tail 200` |
| FAILED 后重建 Connector 丢失 offset | 新 Connector 重新快照 | 如要保留 offset，用 PUT 修改 config 而不是 DELETE + POST |

---

## 4. 项目总结

### 优点 & 缺点

| 排查手段 | 优点 | 缺点 |
|---------|------|------|
| REST API 状态查询 | 即时、可编程 | trace 字段可能被截断 |
| Worker 日志 | 完整的错误上下文 | 需要直接访问容器/服务器 |
| Debezium UI | 可视化、颜色编码 | 错误详情展示有限 |
| 自动化巡检脚本 | 7x24 主动监控 | 需要维护和告警集成 |

### 适用场景

1. **值班运维**：将健康检查脚本集成到 Prometheus/告警系统，代替人工 curl
2. **CI/CD 流水线**：部署新 Connector 后自动执行健康检查，FAILED 则阻断部署
3. **故障复盘**：将本章的五步 SOP 打印成卡片，放在团队 wiki 首页
4. **新人培训**：让新人依次制造本章的 5 种故障并独立恢复，作为"CDC 出师考试"

### 注意事项

- **不要在生产 Connector 上 DELETE + POST**：这样做会丢失 offset，重新全量快照。正确做法是 PUT 修改配置 + POST /restart
- **`state: UNASSIGNED` 持续过长**：说明没有可用的 Worker。检查 Worker 数量、心跳设置、网络分区
- **`trace` 字段在大规模错误时可能返回 `null`**：此时必须查 Worker 日志

### 思考题

1. Connector 的 `/restart` 和 `/tasks/{id}/restart` 有何不同？什么场景下应该用 task 级的重启而不是 Connector 级的重启？

2. 如何在不使用第三方监控平台的情况下，让 Connect Worker 在 Connector 状态变为 FAILED 时自动发送邮件/webhook 通知？

**（第12章思考题答案）**

1. 使用不同的浏览器标签页颜色标识——绿色 = dev，橙色 = staging，红色 = prod。防呆设计：① 在 prod 集群的 Connector 操作按钮旁增加"确认提示"（"你正在操作生产集群，确定吗？"）；② 使用不同的 API Token 区分集群权限；③ Debezium UI 支持通过 cluster_name 标签区分集群，一目了然；④ Settings 中可以将 UI 设置为 readonly 模式，只展示不修改。

2. Debezium UI 通过 Kafka Connect 的 REST API（`GET /connectors/{name}/config`）获取 Connector 配置，从中解析出 `database.hostname`、`database.user` 等连接信息。然后用这些信息直接连接数据库执行 `SHOW TABLES` 等查询来获取表列表。实际上 UI 并不会通过 Kafka Connect 的 API 来查询数据库表——它建立了一个从 UI 到数据库的临时 JDBC 连接。这意味着 UI 必须能够直接访问数据库（网络可达），且有相应的数据库账号权限。

---

> **推广提示**：将本章的 Python 健康检查脚本作为团队运维的基准工具，集成到 Prometheus Blackbox Exporter 或 Jenkins cron job 中，确保 Connector 异常的 MTTR（平均修复时间）从人工发现的数小时降到自动化发现的 5 分钟内。
