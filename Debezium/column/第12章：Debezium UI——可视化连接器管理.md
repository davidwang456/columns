# 第12章：Debezium UI——可视化连接器管理

## 1. 项目背景

"大师，我已经部署了 8 个 Connector，但现在完全靠 curl + jq 管理太痛苦了——要看哪个 Connector FAILED 了，得一个一个 curl GET /connectors/xxx/status；要改个配置，得手动构造 JSON 然后 POST。最要命的是，昨晚上线了一个新 Connector，参数里 `database.server.id` 和另一个 Connector 冲突了，我凌晨 3 点才发现——因为没有一个地方能直观看到所有 Connector 的状态。"

这就是 Debezium UI 存在的价值。它是一个轻量级的 Web 管理界面，可以集中管理一个或多个 Kafka Connect 集群上的 Connector。通过可视化的方式，你可以创建 Connector（向导模式）、查看状态（卡片视图）、监控任务分布（柱状图）以及浏览连接器日志。它让 Connector 管理从"命令行盲操"进化到"可视化驾驶舱"。

### 痛点放大

纯命令行管理的典型困境：
- **状态不可见**：8 个 Connector 中有一个 FAILED 了，除非有人手动 curl 或用脚本巡检，否则直到下游数据断流才发现
- **配置易错**：手工构造 JSON 时，`table.include.list` 写成 `table.included.list`（多一个 d），Connector 静默地忽略了参数，全库同步拖垮 Kafka
- **多人协作困难**：A 同事半夜手动 curl 改了一个 Connector 配置，B 同事不知道，第二天排查问题发现配置"凭空变了"
- **新人上手难**：面对 50+ 参数，新人不知道该填什么、填错了是什么后果，UI 的向导模式能提供参数提示和校验

---

## 2. 项目设计——三人对话

**（午休时间，小胖趴在桌上刷手机）**

**小胖**："大师，你说 Debezium 有没有像 Navicat 那样的图形界面？我实在不想再手写 JSON 了，上次拼错了参数名，排查了半天。"

**大师**："有，Debezium UI。你已经在 docker-compose.yml 里部署了——`debezium/debezium-ui:2.7`，浏览器打开 `http://localhost:8080` 就能看到。"

**小胖**（立刻打开浏览器）："哇，真的有！这个界面怎么用？我看到上面有个 'Connect' 按钮，还有 Connector 列表、创建向导..."

**大师**："给你 tour 一下——"

```
Debezium UI 四大功能区：

┌───────────────────────────────────────────┐
│  🏠 Dashboard  │  📋 Connectors  │  ⚙️ Settings  │
├───────────────────────────────────────────┤
│                                           │
│  ┌─ Connector 列表 ────────────────────┐  │
│  │  orders-connector    ● RUNNING      │  │
│  │  pg-users-connector  ● RUNNING      │  │
│  │  inventory-connector ● FAILED  🔴    │  │
│  └────────────────────────────────────┘  │
│                                           │
│  ┌─ 创建向导 ──────────────────────────┐  │
│  │  Step 1: 选择 Connector 类型         │  │
│  │  Step 2: 填写连接信息               │  │
│  │  Step 3: 选择表                     │  │
│  │  Step 4: 高级配置（可选）            │  │
│  │  Step 5: 审查 & 创建                │  │
│  └────────────────────────────────────┘  │
│                                           │
│  ┌─ 监控面板 ──────────────────────────┐  │
│  │  Lag: ████████░░ 2341               │  │
│  │  Events/s: ██████████ 15234         │  │
│  └────────────────────────────────────┘  │
└───────────────────────────────────────────┘
```

**小白**："UI 背后是怎么和 Kafka Connect 通信的？还是 REST API 吗？"

**大师**："对，UI 本质上是一个**前端代理层**。你在 UI 上的所有操作——创建、暂停、重启、删除 Connector——都会转换成对应的 REST API 调用发送到 Kafka Connect 的 `:8083` 端口。UI 本身不存储任何状态，它只是一个漂亮的壳，真正的逻辑和数据都在 Kafka Connect 里。"

**小胖**："那 UI 支持同时管理多个 Kafka Connect 集群吗？我们生产环境有 3 套 Connect 集群。"

**大师**："支持。在 Settings 里可以添加多个 `Kafka Connect URI`，UI 会用颜色编码区分不同的集群。这对多集群运维特别有用——你可以在一个页面上看到所有环境（dev/staging/prod）的 Connector 状态，不需要切换浏览器标签页。"

---

## 3. 项目实战

### 环境准备

Debezium UI 已在 docker-compose.yml 中预装，确认可访问。

```bash
# 确认 UI 容器正在运行
docker ps --filter "name=debezium-ui"
# 预期：STATUS Up

# 确认 UI 能否访问 Kafka Connect REST API
docker exec debezium-ui curl -s http://connect:8083/connectors
# 预期：返回 JSON 数组（当前已注册的 Connector 列表）

# 浏览器访问 http://localhost:8080
```

### 步骤1：通过 UI 创建第一个 Connector（向导模式）

**目标**：不使用 curl，在 UI 上通过向导创建一个 MySQL Connector。

**操作步骤**：

1. 打开 `http://localhost:8080`
2. 点击 **"Create a Connector"** 按钮
3. 在 Connector 类型列表中选择 **"MySQL"**
4. 填写连接信息：
   - `database.hostname`: `mysql`
   - `database.port`: `3306`
   - `database.user`: `debezium`
   - `database.password`: `dbz1234`
   - `topic.prefix`: `ui_test`
   - `database.server.id`: `184121`
5. 在"表选择"步骤中，选择 `inventory.orders`
6. 在"高级配置"中：
   - `snapshot.mode`: `schema_only`
   - `transforms`: `unwrap`
   - `transforms.unwrap.type`: `io.debezium.transforms.ExtractNewRecordState`
   - `transforms.unwrap.delete.handling.mode`: `rewrite`
7. 点击 **"Review & Create"** → 确认参数 → 点击 **"Create"**

**等价于 curl 命令**（UI 自动生成的）：
```bash
curl -X POST http://localhost:8083/connectors \
  -H "Content-Type: application/json" \
  -d '{
    "name": "ui-test-connector",
    "config": { ... }
  }'
```

### 步骤2：利用 Dashboard 查看所有 Connector 状态

**目标**：在一屏内看到所有 Connector 的状态、Lag、错误。

**操作**：点击顶部导航 **"Dashboard"**。

**预期看到**：
- 所有 Connector 以卡片形式展示
- 绿色徽章 = RUNNING
- 红色徽章 = FAILED
- 黄色徽章 = PAUSED
- 每个卡片的右下角：Events/s（每秒事件数）、Lag（延迟）、Last Event Time

### 步骤3：在线修改 Connector 配置

**目标**：通过 UI 修改 `poll.interval.ms`，并重新启动 Connector。

**操作**：
1. 点击某个 RUNNING 状态的 Connector 卡片
2. 进入详情页，点击 **"Edit"** 按钮
3. 找到 `poll.interval.ms` 字段，从 `500` 改为 `100`
4. 点击 **"Save"** → UI 自动调用 `PUT /connectors/{name}/config`
5. Connector 自动重新加载配置（无需重启）

**验证**（命令行）：
```bash
curl http://localhost:8083/connectors/ui-test-connector/config | python3 -c "import sys,json; print(json.load(sys.stdin)['poll.interval.ms'])"
# 预期输出：100
```

### 步骤4：暂停和恢复 Connector

**目标**：通过 UI 暂停 Connector（如计划窗口时间），再恢复。

**操作**：
1. 在 Connector 列表页，点击某个 Connector 右侧的 **"⏸ Pause"** 按钮
2. 确认暂停 → 状态变为 PAUSED（黄色）
3. 过 30 秒后，点击 **"▶ Resume"** 按钮
4. Connector 恢复运行，从暂停前的 offset 继续消费

**命令行验证**：
```bash
curl http://localhost:8083/connectors/ui-test-connector/status | python3 -m json.tool
# PAUSED 时：{"connector":{"state":"PAUSED"}, "tasks":[{"state":"PAUSED"}]}
# RESUMED 后：{"connector":{"state":"RUNNING"}, "tasks":[{"state":"RUNNING"}]}
```

### 步骤5：删除 Connector（安全删除）

**目标**：通过 UI 删除 Connector，并确认 Kafka Topic 中的数据不受影响。

**操作**：
1. 点击 Connector 详情页
2. 点击右上角的 **"🗑 Delete"** 按钮
3. 确认删除

**注意事项**（UI 上会有提示）：
- 删除 Connector **不会删除** Kafka Topic 中的数据
- 删除 Connector 会清理 Connector 相关的 offset 和状态信息
- 如果将来用同名重建 Connector，且 offset 未被清理，会从之前的位点继续

### 可能遇到的坑及解决方法

| 坑 | 现象 | 根因与解决 |
|----|------|-----------|
| UI 连不上 Connect | 页面空白，Connector 列表为空 | `KAFKA_CONNECT_URIS` 环境变量中地址不可达。检查 `http://connect:8083` 在 UI 容器内是否可达 |
| UI 创建 Connector 参数不完整 | Connector 启动 FAILED | UI 向导可能未显示所有高级参数。创建后手动通过 "Edit" 补充缺失参数 |
| UI 无法显示 Avro 消息预览 | "不支持的序列化格式" | Debezium UI 暂不支持 Avro 的消息预览，仅支持 JSON |
| 多 Connect 集群切换后数据混乱 | 看不到某些 Connector | UI 右上角检查当前选择的 Connect 集群是否正确 |

---

## 4. 项目总结

### 优点 & 缺点

| 维度 | Debezium UI | 命令行 (curl) | Kafka Connect REST API 脚本 |
|------|------------|--------------|---------------------------|
| 学习成本 | ★★★★★ 极低 | ★★★☆☆ 需记 API | ★★★★☆ 脚本一次编写 |
| 批量操作 | ★★☆☆☆ 不支持 | ★★★★☆ 脚本批量 | ★★★★★ 天然支持 |
| 可视化状态 | ★★★★★ 直观 | ★☆☆☆☆ 无 | ★★☆☆☆ 需额外开发 |
| 配置校验 | ★★★★☆ 向导校验 | ★☆☆☆☆ 完全手动 | ★★★☆☆ 脚本校验 |
| 生产大规模运维 | ★★☆☆☆ 适合小规模 | ★★★★★ 最适合 | ★★★★★ 最适合 |

### 适用场景

1. **开发 & 测试环境**：UI 是 IDE 级的工具，开发时反复创建/删除/修改 Connector 非常方便
2. **新人培训**：通过 UI 向导手把手教新人理解每个参数的含义
3. **故障快速排查**：Dashboard 上一眼看到哪个 Connector 挂了，不用一个个 curl
4. **轻量生产环境**：Connector 数量 < 20 时，UI 管理效率高于命令行
5. **配置调试**：反复修改 Connector 参数调试最优配置，UI 的表单编辑比手写 JSON 高效

### 注意事项

- **UI 不适合大规模自动化运维**。Connector > 50 时应使用 Ansible/Python 脚本 + REST API
- **UI 的安全边界**。默认无认证，生产环境应加 nginx 反向代理的 basic auth
- **UI 不持久化配置**。所有配置实际存储在 Kafka Connect 中，UI 仅做展示和修改

### 思考题

1. 如果你有 3 个 Kafka Connect 集群（dev、staging、prod），你会如何在 Debezium UI 中组织它们以保证不会误操作生产集群？有哪些 UI 层面的防呆设计建议？

2. Debezium UI 的 Connector 创建向导中，选择"表"时是如何查询到数据库中所有表的？它是直接连接数据库还是通过 Kafka Connect 的某个 API 获取的？

**（第11章思考题答案）**

1. 不能混用 Avro 和 JSON——如果 Connector 用 AvroConverter 序列化，消息体是二进制 Avro 格式，消费者用 JSONDeserializer 反序列化会收到 `Unknown magic byte` 错误。唯一的例外是如果 Kafka 消息的 Key 用 Avro、Value 用 JSON（或反之），各自独立序列化是可行的。但建议统一使用 Avro，避免维护多种序列化链路。

2. 如果 `_schemas` Topic 被误删，Schema Registry 将无法为新消息提供 Schema 解析服务——消费者读到 Schema ID 后查询 Registry 会返回 404，导致反序列化失败，所有下游 Consumer 消费中断。恢复方法：从 Kafka 备份中恢复 `_schemas` Topic 的数据；如果没有备份，需要重建所有 Connector 的 Schema History 并注册到 Registry。预防：设置 `_schemas` Topic 的 replication factor ≥ 3，`min.insync.replicas ≥ 2`，定期备份。

---

> **推广提示**：建议将 Debezium UI 的访问链接添加到团队的浏览器书签或内部开发者门户。运维团队可将其作为"CDC 健康状态 Dash"的常亮显示器在 NOC 墙上展示。
