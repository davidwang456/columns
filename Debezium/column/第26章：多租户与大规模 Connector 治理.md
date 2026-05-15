# 第26章：多租户与大规模 Connector 治理

## 1. 项目背景

某 SaaS 平台的 CDC 管道从最初的 5 个 Connector 增长到了 150 个——每签约一个新客户，就需要为其专属数据库创建 3-5 个 Connector（MySQL + PostgreSQL + MongoDB）。运维小李的日常从"偶尔看状态"变成了"每天新建 3 个 Connector + 排查 2 个故障告警"。

更严重的是，所有 150 个 Connector 混跑在同一个 Kafka Connect 集群上——没有一个 Connector 配置了资源隔离。一次租户 A 的数据突发（批量导入 5000 万行历史订单）导致内存队列爆满，直接拖垮了其他 149 个租户的实时同步。业务部门在群里怒骂："我们租户 B 的数据延迟了 40 分钟，客户在投诉！"

**大规模 Connector 治理**不是简单的数量叠加，而是需要在四个维度建立体系——**分（资源隔离）、标（标准化）、动（自动化）、监（可观测性）**。本章将以 150 个 Connector 为例，从配置模板化到 GitOps 自动化，从多 Group 资源隔离到按租户粒度的监控告警，建立一套可扩展的 CDC 治理框架。

## 2. 项目设计——三人对话

**（运维小李满脸疲惫地找到大师，桌上堆着 5 张打印出来的 Connector 配置表格）**

**小李**："大师救命！150 个 Connector 我一个人真管不过来了。昨晚租户 A 的数据导入直接把 Connector 队列打满，其他 149 个租户全都受影响。而且现在每次有新租户上线，我要手动 curl 创建 4 个 Connector，还要复制粘贴改数据库地址、改 server.id、改 topic.prefix...人为出错的概率太高了。"

**大师**："你现在的状态是典型的'规模失控'。150 个 Connector 的核心问题是——**一锅煮**。你需要的是四字诀：**分、标、动、监**。"

**小胖**："四个字怎么讲？我先给你倒杯咖啡你慢慢说。"

**大师**："分——资源隔离。按租户拆分 Connect Group 或者干脆给 VIP 租户独立集群。标——标准化。公共配置抽出来做模板，每个租户只填差异值。动——自动化。Ansible/Terraform 批量部署，GitOps 管理配置变更。监——可观测性。按租户粒度 Grafana 面板，告警自动路由到对应负责人。"

**小白**："我有个问题——150 个 Connector 的配置到底怎么管理？总不能 150 个 JSON 文件都存在硬盘里，每次改一个参数要改 150 个文件吧？"

**大师**："这正是模板化的价值。你看这 150 个 Connector 的配置——90% 的内容是相同的（`connector.class`、`snapshot.mode`、`provide.transaction.metadata`、SMT 配置等），真正不同的只有几个参数：

| 参数 | 差异原因 |
|------|---------|
| `database.hostname` | 每个租户独立的数据库实例 |
| `database.server.id` | binlog slave id 必须全局唯一 |
| `topic.prefix` | 每个租户独立的 Topic 前缀 |
| `table.include.list` | 部分租户的表结构不同 |
| `database.password` | 每个租户独立的密码 |

所以正确的做法是——用 Jinja2 模板把公共部分固定下来，差异参数通过 Ansible 变量循环注入。150 个 Connector = 一个 for 循环。"

**小李**："那资源隔离具体怎么做？难道要给每个租户都买一台服务器？"

**大师**："不需要。隔离粒度有三层——"

```
Level 1: 按租户拆分 Connect Group（最轻量）
  VIP租户 → CONNECT_GROUP_ID=connect-vip
  普通租户 → CONNECT_GROUP_ID=connect-normal

Level 2: 按租户拆分 Connect 集群（中等隔离）
  每个 Connect 集群 3 个 Worker，互不影响

Level 3: 按租户拆分 Kafka 集群（最强隔离）
  但成本最高，一般不推荐
```

**小胖**："那监控呢？150 个 Connector 的告警如果全发给小李，他会被淹死吧？"

**大师**："告警路由——在 Connector 名字中嵌入租户标签，告警时自动解析租户名 → 查 CMDB 找到租户对应的 oncall → 发送到对应企业微信群。比如 Connector 名是 `connector.tenant_a.prod.orders_db.orders`，从名字中提取 `tenant_a`，查表得到 oncall 是'王五'。"

**技术映射**：大规模 Connector 治理 = 城市交通管理。每个 Connector 是一辆公交车，Connect Group = 公交公司（隔离不打架），模板化 = 标准车型（减少定制维护），自动化部署 = 车厂流水线（一键造车），告警路由 = 智能调度中心（哪个车坏了自动通知哪个车队）。

---

## 3. 项目实战

### 环境准备

由于 Docker 环境为单 Worker，我们重点练习配置模板化和批量管理脚本。

```bash
mkdir -p ~/debezium-lab/connector-templates
cd ~/debezium-lab/connector-templates
```

### 步骤1：编写 Jinja2 配置模板

**目标**：创建一份公共 Connector 模板，通过变量渲染生成 150 个实例的配置。

```jinja2
{# connector-template.json.j2 #}
{
  "name": "connector.{{ tenant }}.prod.{{ db }}.orders",
  "config": {
    "connector.class": "io.debezium.connector.mysql.MySqlConnector",
    "database.hostname": "{{ tenant }}-mysql.internal.prod",
    "database.port": "3306",
    "database.user": "debezium_sync",
    "database.password": "{{ vault_db_password }}",
    "database.server.id": "18426{{ server_id }}",
    "topic.prefix": "{{ tenant }}",
    "database.include.list": "{{ db }}",
    "table.include.list": "{{ db }}.orders,{{ db }}.payments",
    "schema.history.internal.kafka.bootstrap.servers": "kafka.prod.internal:9092",
    "schema.history.internal.kafka.topic": "schema-changes.{{ tenant }}",
    "snapshot.mode": "initial",
    "snapshot.locking.mode": "minimal",
    "provide.transaction.metadata": "true",
    "max.batch.size": "8192",
    "max.queue.size": "32768",
    "compression.type": "snappy",
    "transforms": "unwrap,maskPII",
    "transforms.unwrap.type": "io.debezium.transforms.ExtractNewRecordState",
    "transforms.unwrap.delete.handling.mode": "rewrite",
    "transforms.maskPII.type": "org.apache.kafka.connect.transforms.ReplaceField$Value",
    "transforms.maskPII.exclude": "password,phone,id_card,credit_card"
  }
}
```

### 步骤2：Ansible Playbook 批量部署

**目标**：用一个 Ansible Playbook 循环部署所有租户的 Connector。

```yaml
# deploy-connectors.yml
- name: Deploy CDC Connectors for all tenants
  hosts: connect-workers
  vars:
    connect_url: "http://localhost:8083"
    tenants:
      - { name: "tenant_a", db: "tenant_a_db", server_id: "101", priority: "vip" }
      - { name: "tenant_b", db: "tenant_b_db", server_id: "102", priority: "normal" }
      - { name: "tenant_c", db: "tenant_c_db", server_id: "103", priority: "normal" }
      # ... 147 more tenants
      - { name: "tenant_zz", db: "tenant_zz_db", server_id: "250", priority: "normal" }
  
  tasks:
    - name: Render connector config from template
      ansible.builtin.template:
        src: connector-template.json.j2
        dest: "/tmp/connector-{{ item.name }}.json"
      loop: "{{ tenants }}"
    
    - name: Register connector via Kafka Connect REST API
      ansible.builtin.uri:
        url: "{{ connect_url }}/connectors"
        method: POST
        body_format: json
        body: "{{ lookup('file', '/tmp/connector-' + item.name + '.json') | from_json }}"
        headers:
          Content-Type: "application/json"
      loop: "{{ tenants }}"
      register: result
    
    - name: Verify all connectors running
      ansible.builtin.uri:
        url: "{{ connect_url }}/connectors/{{ item.name }}/status"
        method: GET
      loop: "{{ tenants }}"
      register: status_check
      until: status_check.json.connector.state == "RUNNING"
      retries: 10
      delay: 5
```

### 步骤3：资源隔离——多 Connect Group 配置

**目标**：为 VIP 租户和普通租户配置不同的 Connect Group。

```yaml
# docker-compose-vip.yml (VIP 租户专属 Connect 集群)
services:
  connect-vip:
    image: confluentinc/cp-kafka-connect:7.6.0
    environment:
      CONNECT_GROUP_ID: connect-vip        # ← 专属 Group
      CONNECT_BOOTSTRAP_SERVERS: kafka:9092
      # ... 其他配置同基础环境 ...
    volumes:
      - ./plugins:/kafka/connect

# docker-compose-normal.yml (普通租户)
services:
  connect-normal:
    image: confluentinc/cp-kafka-connect:7.6.0
    environment:
      CONNECT_GROUP_ID: connect-normal
      CONNECT_BOOTSTRAP_SERVERS: kafka:9092
```

### 步骤4：批量巡检和告警路由脚本

**目标**：编写巡检脚本，按租户粒度过期 Connector 状态，异常时自动路由到对应 oncall。

```bash
cat > batch_monitor.sh << 'EOF'
#!/bin/bash
CONNECT_URL="http://localhost:8083"

# 租户 → oncall 映射表（实际应从 CMDB 查询）
declare -A ONCALL=(
    ["tenant_a"]="王五"
    ["tenant_b"]="李六"
    ["tenant_c"]="赵七"
)

for name in $(curl -s "${CONNECT_URL}/connectors" | python3 -c "import sys,json;[print(x) for x in json.load(sys.stdin)]"); do
    STATUS=$(curl -s "${CONNECT_URL}/connectors/${name}/status")
    TASK_STATE=$(echo "$STATUS" | python3 -c "import sys,json;print(json.load(sys.stdin)['tasks'][0]['state'])")
    
    # 解析 tenant 名（connector.tenant_a.prod.orders_db.orders → tenant_a）
    TENANT=$(echo "$name" | cut -d'.' -f2)
    ONCALL_PERSON="${ONCALL[$TENANT]:-unknown}"
    
    if [ "$TASK_STATE" != "RUNNING" ]; then
        echo "❌ [${name}] @${ONCALL_PERSON}: ${TASK_STATE}"
        # 实际环境：curl POST 到企业微信/Slack Webhook
        # curl -X POST "$SLACK_WEBHOOK" -d "{\"text\":\"❌ ${name}: ${TASK_STATE} → @${ONCALL_PERSON}\"}"
    else
        echo "✅ [${name}]"
    fi
done
EOF

chmod +x batch_monitor.sh
bash batch_monitor.sh
```

### 步骤5：配置备份与 GitOps 流程

**目标**：将所有 Connector 配置导出并存到 Git，实现"Git 中的配置即生产环境的状态"。

```bash
cat > backup_to_git.sh << 'EOF'
#!/bin/bash
BACKUP_DIR="./connectors-backup/$(date +%Y%m%d)"
mkdir -p "$BACKUP_DIR"

for name in $(curl -s http://localhost:8083/connectors | python3 -c "import sys,json;[print(x) for x in json.load(sys.stdin)]"); do
    curl -s "http://localhost:8083/connectors/${name}/config" | python3 -m json.tool > "${BACKUP_DIR}/${name}.json"
done

# 提交到 Git
cd "$BACKUP_DIR"
git add . && git commit -m "Backup: $(date +%Y-%m-%d) - $(ls *.json | wc -l) connectors" && git push
echo "Backed up $(ls *.json | wc -l) connectors to Git"
EOF
```

### 可能遇到的坑及解决方法

| 坑 | 现象 | 根因 | 解决方法 |
|----|------|------|---------|
| 模板中变量未转义 | JSON 格式错误导致注册失败 | Jinja2 变量包含特殊字符 | 使用 `{{ variable | tojson }}` 过滤器 |
| server.id 冲突 | 多个 Connector 同时报 FAILED | 手动分配的 server.id 重复 | 自动化分配脚本: `server_id = tenant_index * 2 + 1` |
| 租户密码泄露 | Git 仓库中包含明文密码 | 密码直接写在模板中 | 使用 Ansible Vault 或 HashiCorp Vault 管理密钥 |
| VIP 租户仍被普通租户影响 | VIP 延迟也飙高 | 忘了修改 VIP Connector 注册到 VIP Connect Group | 部署脚本中按 `priority` 决定 REST API 目标地址 |

---

## 4. 项目总结

### 优点 & 缺点

| 维度 | 手动管理 | 模板化 + Ansible | GitOps 声明式 |
|------|---------|-----------------|--------------|
| 新增租户耗时 | 30 min（手动 curl 4 次） | 5 min（加一条 YAML） | 3 min（改 Git + ArgoCD 自动同步） |
| 配置一致性 | ★☆☆☆☆ 人为出错风险高 | ★★★★☆ 模板保证 | ★★★★★ Git 审计 + diff |
| 回滚能力 | ★☆☆☆☆ 靠人脑记 | ★★★☆☆ 需手动 revert | ★★★★★ git revert |
| 学习成本 | ★★★★★ 极低 | ★★★☆☆ 需 Ansible 基础 | ★★☆☆☆ 需 K8s + GitOps 知识 |

### 适用场景

1. **SaaS 多租户平台**：每个租户独立数据库 + 独立 Connector，典型 50-500 个
2. **微服务多库同步**：每个微服务独立数据库，需统一纳入 CDC 总线
3. **多环境管理**：dev/staging/prod 各有独立的 Connector 配置
4. **CI/CD 集成**：新数据库上线 → 自动创建 Connector → 自动触发增量快照
5. **合规审计**：Git 中记录了所有 Connector 配置的历史变更

### 不适用场景

1. **Connector 数量 < 10**：手动管理 + Debezium UI 更高效
2. **每个 Connector 的配置差异极大**：模板化的收益降低，需要更复杂的条件分支

### 注意事项

- **命名规范是治理的第一基石**：建议四级命名 `connector.{tenant}.{env}.{db}.{table}`
- **Git 仓库中的密码问题**：敏感信息（database.password）必须使用 SealedSecret / Ansible Vault / HashiCorp Vault
- **Connect Group ID 要保持团队统一**：不同人维护时很容易写错

### 常见踩坑经验

1. **"模板改了公共配置，Ansible 重跑后 150 个 Connector 全部重启"**——PUT /config 会触发 Connector 重新加载。如果不想重启全部，用 Ansible 的 `when: config_changed` 条件只在配置变更时执行 PUT。
2. **"批量创建时第 87 个 Connector 失败，后面 63 个全部跳过了"**——Ansible 默认在任务失败后停止。加上 `ignore_errors: yes` 或使用 `block/rescue` 让失败不影响后续。
3. **"CMDB 中的租户 oncall 联系人改了，但告警脚本还是发给旧人"**——告警路由脚本中的 oncall 映射不应硬编码在 shell 中，而是通过 HTTP 调用 CMDB API 实时获取最新信息。

### 思考题

1. 500 个 Connector（每个 2 Task = 1000 Task），Kafka Connect 单 Worker 理论最大 ~10000 Task，实际推荐 < 1000 Task/Worker。你如何规划 Worker 数量以平衡高可用和资源利用率？如果某 VIP 租户的 SLA 是 99.99% 可用性，至少需要多少 Worker？

2. 当 Connector 数量增长到 500 个时，Kafka Connect 的三个内部 Topic（`connect-configs`、`connect-offsets`、`connect-statuses`）会面临什么压力？如何设计分区策略和保留策略来保证这些 Topic 的可用性？

**（第25章思考题答案）**

1. 8MB 的批会超过 Kafka 的 1MB 上限被拒绝，Connector 日志会看到 `RecordTooLargeException`。联动调整：Kafka Broker 端 `max.message.bytes=10485760`（10MB），同时 Connector Producer 端也需要调大 `max.request.size=10485760`（默认也是 1MB），以及 Topic 的 `max.message.bytes=10485760`。三个地方的 `max.message.bytes`/`max.request.size` 必须一致。

2. 调优前 Full GC 频繁的根因是——大量小对象（每条 Change Event 的临时 Struct/Map 对象）频繁创建和回收，触发 Young GC → 晋升到 Old 区 → Full GC。调优后，虽然队列变大了（`max.queue.size=65536`），但对象晋升路径更长（队列里的对象是长寿的），而且大批次发送减少了 Producer 线程中创建的小对象数量。所以内存总量增加了，但 GC 频率降低了——这是"空间换时间"的经典例子。

---

> **推广提示**：架构团队应制定《CDC 命名规范与治理标准》，明确规定 Connector 命名、Topic 命名、Group ID 命名、告警标签等规则。新 Connector 上线必须通过模板化流程，不允许手动 curl 创建。运维团队应将巡检脚本纳入 K8s CronJob，每 5 分钟执行一次，异常自动通知对应租户的 oncall。
