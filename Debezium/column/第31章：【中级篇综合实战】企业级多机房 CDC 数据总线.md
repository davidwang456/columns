# 第31章：【中级篇综合实战】企业级多机房 CDC 数据总线

## 1. 项目背景

某跨国电商平台在全球有两个核心数据中心——华东（上海）作为主数据中心处理所有在线交易，东南亚（新加坡）作为灾备和区域服务节点。核心业务数据分布在 8 个 MySQL 数据库（总共 200+ 张表）中，涉及订单、库存、支付、用户、物流、营销等 6 个业务域。

CTO 在季度技术规划会上提出三大硬性要求：

1. **实时数据同步**：上海主中心的任何 DML 变更，必须在 5 秒内同步到新加坡灾备中心，端到端延迟 < 5s
2. **机房级灾备**：上海机房整体宕机时，新加坡机房在 60 秒内接管全部 CDC 数据流的消费，RPO（数据丢失）< 10 秒
3. **安全合规**：所有 CDC 数据中，手机号、身份证号、银行卡号必须在进入 Kafka 之前完成脱敏处理

现有方案是一套自研的"定时 mysqldump + rsync + 手动脚本恢复"，显然无法满足这三条要求。架构师一鸣被任命为这个项目的技术负责人，需要基于 Debezium 构建一套企业级的多机房 CDC 数据总线。

本章将作为中级篇（第17-30章）的综合实战，融会贯通前 14 章的核心知识——事务元数据、增量快照、高级路由、Schema 演进、性能调优、分布式 Worker、信号表、可观测性、安全合规——构建一套可直接交付的企业级方案。

## 2. 架构设计

### 2.1 网络拓扑

```
┌─────────────────────────────────────────────────────────────────┐
│                    华东主数据中心（上海）                           │
│                                                                   │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐            │
│  │ orders_db│ │ inv_db   │ │ pay_db   │ │ user_db  │ ×4 more    │
│  │ (MySQL)  │ │ (MySQL)  │ │ (MySQL)  │ │ (PG)     │            │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘            │
│       │              │            │            │                  │
│  ┌────┴──────────────┴────────────┴────────────┴────────────┐   │
│  │         Kafka Connect 集群 (6 Worker, 3 AZ)               │   │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐                  │   │
│  │  │ MySQL    │ │   PG     │ │ 自定义SMT │  24 Connectors   │   │
│  │  │Connector×│ │Connector×│ │ (脱敏/加密)│                  │   │
│  │  │   16     │ │    4     │ └──────────┘                  │   │
│  │  └──────────┘ └──────────┘                                │   │
│  └──────────────────────┬────────────────────────────────────┘   │
│                         │                                        │
│  ┌──────────────────────┴────────────────────────────────────┐   │
│  │          Kafka Cluster (3 Broker × 3 AZ)                   │   │
│  │  250+ Topic, 1000+ Partition, Avro + Schema Registry       │   │
│  └──────────────────────┬────────────────────────────────────┘   │
└─────────────────────────┼────────────────────────────────────────┘
                          │
            MirrorMaker 2 │ (跨机房异步复制, < 5s 延迟)
                          │
┌─────────────────────────┼────────────────────────────────────────┐
│                新加坡灾备数据中心 (standby)                         │
│  ┌──────────────────────┴────────────────────────────────────┐   │
│  │          Kafka Cluster (3 Broker, standby)                 │   │
│  └──────────────────────┬────────────────────────────────────┘   │
│                         │                                        │
│  ┌──────────────────────┴────────────────────────────────────┐   │
│  │    Kafka Connect (6 Worker, standby, 平时未激活)           │   │
│  │    灾难时 Operator 自动激活 → Task 自动接管消费            │   │
│  └───────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
```

### 2.2 数据流设计

```
上海 MySQL INSERT/UPDATE/DELETE
  │
  ▼
上海 Connector 捕获 → 自定义 SMT 脱敏 → Avro 序列化 → Schema Registry 注册
  │
  ▼
上海 Kafka Topic
  │
  ├──→ 上海下游消费者(实时风控、经营分析、BI 报表)
  │
  └──→ MirrorMaker 2 异步复制
         │
         ▼
       新加坡 Kafka Topic
         │
         └──→ 新加坡下游消费者(灾备/区域服务)
```

## 3. 功能实现

### 3.1 Ansible 自动化部署——24 个 Connector 一键上线

```yaml
# ansible/deploy-cdc-bus.yml
- name: Deploy Multi-DC Debezium CDC Bus
  hosts: connect-workers-shanghai
  vars:
    connect_url: "http://localhost:8083"
    schema_registry_url: "http://schema-registry.internal:8081"
    
    # 24 个 Connector 的定义
    connectors:
      # 订单域
      - tenant: shanghai
        domain: orders
        db: orders_db_sh
        tables: "orders_db_sh.orders,orders_db_sh.order_items"
        server_id: "01"
        partitions: 12
      # 库存域
      - tenant: shanghai
        domain: inventory
        db: inventory_db_sh
        tables: "inventory_db_sh.stock_log,inventory_db_sh.warehouse"
        server_id: "02"
        partitions: 8
      # 支付域
      - tenant: shanghai
        domain: payment
        db: payment_db_sh
        tables: "payment_db_sh.transactions,payment_db_sh.refunds"
        server_id: "03"
        partitions: 6
      # ... 21 more connectors
    
    # 公共 SMT 配置（所有 Connector 统一使用）
    common_transforms: "unwrap,maskPII"
    common_transforms_unwrap_type: "io.debezium.transforms.ExtractNewRecordState"
    common_transforms_unwrap_delete_handling_mode: "rewrite"
    common_transforms_maskPII_type: "org.apache.kafka.connect.transforms.ReplaceField$Value"
    common_transforms_maskPII_exclude: "phone,id_card,bank_account,password"
    
    # 公共性能配置
    common_max_batch_size: "8192"
    common_max_queue_size: "32768"
    common_poll_interval_ms: "100"
    common_compression_type: "snappy"
  
  tasks:
    - name: Create Connector config JSON from Jinja2 template
      ansible.builtin.template:
        src: connector.json.j2
        dest: "/tmp/connector-{{ item.tenant }}-{{ item.domain }}.json"
      loop: "{{ connectors }}"
    
    - name: Register Connector via REST API
      ansible.builtin.uri:
        url: "{{ connect_url }}/connectors"
        method: POST
        body_format: json
        body: "{{ lookup('file', '/tmp/connector-' + item.tenant + '-' + item.domain + '.json') }}"
        headers:
          Content-Type: "application/json"
      loop: "{{ connectors }}"
      register: deploy_result
    
    - name: Verify all Connectors RUNNING
      ansible.builtin.uri:
        url: "{{ connect_url }}/connectors/connector.{{ item.tenant }}.prod.{{ item.domain }}/status"
        method: GET
      loop: "{{ connectors }}"
      register: health_check
      until: health_check.json.connector.state == "RUNNING"
      retries: 20
      delay: 10
```

### 3.2 Connector Jinja2 模板

```jinja2
{# connector.json.j2 #}
{
  "name": "connector.{{ tenant }}.prod.{{ domain }}",
  "config": {
    "connector.class": "io.debezium.connector.mysql.MySqlConnector",
    "database.hostname": "{{ db }}-master.internal.prod",
    "database.port": "3306",
    "database.user": "debezium_sync",
    "database.password": "{{ vault('secret/debezium/' + tenant + '/mysql') }}",
    "database.server.id": "18431{{ server_id }}",
    
    "topic.prefix": "{{ tenant }}.{{ domain }}",
    "database.include.list": "{{ db }}",
    "table.include.list": "{{ tables }}",
    
    "schema.history.internal.kafka.bootstrap.servers": "kafka.prod.internal:9092",
    "schema.history.internal.kafka.topic": "schema-changes.{{ tenant }}.{{ domain }}",
    
    "snapshot.mode": "initial",
    "snapshot.locking.mode": "minimal",
    "snapshot.fetch.size": "20000",
    
    "provide.transaction.metadata": "true",
    
    "signal.enabled.channels": "source",
    "signal.data.collection": "{{ db }}.debezium_signal",
    "incremental.snapshot.chunk.size": "5000",
    
    "key.converter": "io.confluent.connect.avro.AvroConverter",
    "value.converter": "io.confluent.connect.avro.AvroConverter",
    "key.converter.schema.registry.url": "{{ schema_registry_url }}",
    "value.converter.schema.registry.url": "{{ schema_registry_url }}",
    
    "transforms": "{{ common_transforms }}",
    "transforms.unwrap.type": "{{ common_transforms_unwrap_type }}",
    "transforms.unwrap.delete.handling.mode": "{{ common_transforms_unwrap_delete_handling_mode }}",
    "transforms.maskPII.type": "{{ common_transforms_maskPII_type }}",
    "transforms.maskPII.exclude": "{{ common_transforms_maskPII_exclude }}",
    
    "max.batch.size": "{{ common_max_batch_size }}",
    "max.queue.size": "{{ common_max_queue_size }}",
    "poll.interval.ms": "{{ common_poll_interval_ms }}",
    "compression.type": "{{ common_compression_type }}",
    "topic.creation.default.partitions": "{{ partitions }}",
    "topic.creation.default.replication.factor": "3"
  }
}
```

### 3.3 MirrorMaker 2 跨机房复制配置

```yaml
# mirror-maker-2.yaml
apiVersion: kafka.strimzi.io/v1beta2
kind: KafkaMirrorMaker2
metadata:
  name: shanghai-to-singapore
spec:
  version: 3.6.0
  replicas: 3
  connectCluster: singapore-kafka
  clusters:
    - alias: shanghai
      bootstrapServers: kafka-shanghai.prod.internal:9092
    - alias: singapore
      bootstrapServers: kafka-singapore.prod.internal:9092
  mirrors:
    - sourceCluster: shanghai
      targetCluster: singapore
      sourceConnector:
        config:
          replication.factor: 3
          offset-syncs.topic.replication.factor: 3
          sync.topic.acls.enabled: false
      topicsPattern: "shanghai\\..*"
      groupsPattern: ".*"
```

### 3.4 灾难恢复演练 SOP

```bash
#!/bin/bash
# DR Drill Script - 每月执行一次

echo "===== DR Drill 开始 $(date) ====="

# Phase 1: 模拟上海 Connect Worker 集群宕机
echo "[Phase 1] 模拟上海主集群故障..."
kubectl scale deployment/kafka-connect -n shanghai --replicas=0

# Phase 2: 等待新加坡备集群自动接管
echo "[Phase 2] 等待新加坡备集群接管..."
ATTEMPTS=0
while [ $ATTEMPTS -lt 30 ]; do
    READY=$(kubectl get pods -n singapore -l app=connect --field-selector=status.phase=Running --no-headers | wc -l)
    if [ "$READY" -ge 3 ]; then
        echo "✅ 新加坡 Connect 集群已就绪 (${ATTEMPTS}s)"
        break
    fi
    sleep 2
    ATTEMPTS=$((ATTEMPTS + 2))
done

# Phase 3: 数据一致性校验
echo "[Phase 3] 数据一致性校验..."
python3 << 'PYEOF'
import mysql.connector, clickhouse_driver

SRC = mysql.connector.connect(host="mysql-orders-shanghai", user="debezium", password="***")
TGT = clickhouse_driver.Client(host="clickhouse-singapore")

TABLES = ["orders", "order_items", "payments", "stock_log"]
for t in TABLES:
    src_count = SRC.cursor().execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    tgt_count = TGT.execute(f"SELECT COUNT(*) FROM {t}")[0][0]
    status = "✅" if src_count == tgt_count else "❌"
    print(f"{status} {t}: src={src_count} tgt={tgt_count}")
PYEOF

# Phase 4: 验证 RPO (MirrorMaker 2 复制延迟)
echo "[Phase 4] 验证 RPO..."
kafka-consumer-groups --bootstrap-server kafka-singapore:9092 \
  --group mm2-shanghai-to-singapore --describe

echo "===== DR Drill 结束 $(date) ====="
```

### 3.5 全自动一致性校验服务

```java
@Service
public class ConsistencyVerificationService {
    
    @Scheduled(cron = "0 0 * * * *")  // 每小时执行
    public void verifyAllDomains() {
        for (VerificationTask task : createTasks()) {
            long srcCount = sourceDB.count(task.sourceTable);
            long tgtCount = targetDB.count(task.targetTable);
            String srcChecksum = sourceDB.checksumTable(task.sourceTable);
            String tgtChecksum = targetDB.checksumTable(task.targetTable);
            
            if (srcCount != tgtCount) {
                alertingService.sendCritical(
                    String.format("严重不一致: %s → 源%d行 目标%d行 差异%d行",
                        task.sourceTable, srcCount, tgtCount, Math.abs(srcCount - tgtCount)));
                // 自动触发增量快照修复
                signalService.sendSignal("execute-snapshot",
                    Map.of("data-collections", List.of(task.sourceTable)));
            } else if (!srcChecksum.equals(tgtChecksum)) {
                alertingService.sendWarning(
                    String.format("校验和不一致(行数相同): %s", task.sourceTable));
            }
            metrics.recordVerification(task.sourceTable, srcCount == tgtCount);
        }
    }
}
```

## 4. 验收标准与最终交付物

| 指标 | 目标 | 实测 | 方案 |
|------|------|------|------|
| 端到端 P99 延迟 | < 5s | 3.2s | batch.size=8192 + poll=100ms |
| 可用性 (SLA) | 99.99% | 99.997% | 3AZ × 6 Worker |
| RPO (数据丢失) | < 10s | 6s | MirrorMaker 2 |
| 故障切换时间 | < 60s | 42s | K8s Rebalance |
| 数据一致性 | > 99.999% | ✓ | 每小时校验和 + 增量快照自动修复 |
| PII 合规 | 100% | ✓ | SMT 自动脱敏 + 合规扫描 |

**最终交付物清单**：
✅ Ansible Playbook 包（一键部署 24 Connector）
✅ Jinja2 模板（Connector + MirrorMaker）
✅ Grafana Dashboard JSON（4 面板：延迟/Lag/吞吐/资源）
✅ 灾难恢复 SOP（含自动化脚本）
✅ 合规扫描服务 Python 代码
✅ 架构设计文档 ADR

---

> **推广提示**：本章的架构图和 Ansible Playbook 可作为企业 CDC 公线的标准蓝图。建议将灾难恢复 SOP 塑封打印贴在 NOC 值班工位。将一致性校验和合规扫描纳入 K8s CronJob，每 30 分钟自动执行，失败时告警自动路由到对应业务的 oncall。
