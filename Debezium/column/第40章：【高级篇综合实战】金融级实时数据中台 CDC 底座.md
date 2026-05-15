# 第40章：【高级篇综合实战】金融级实时数据中台 CDC 底座

## 1. 项目背景

某金融科技公司经过 5 年发展，核心系统从单一 MySQL 扩展到 8 个数据库（MySQL×5 用于交易/账户/风控/清算/营销，PostgreSQL×2 用于用户画像和系统配置，MongoDB×1 用于用户行为日志），共有 300+ 张业务表。CTO 在年度技术规划中提出了"实时数据中台"战略，要求构建统一的 CDC 底座，支撑三大核心业务需求：

1. **实时风控**：交易数据 P99 延迟 < 1s，基于实时宽表做反欺诈规则匹配。一笔交易的多个维度数据（订单、支付、设备指纹、历史行为）必须在 1 秒内完成 JOIN 并送入规则引擎
2. **监管报送**：所有 DML 变更记录必须保留 5 年，支持精确到秒的时间旅行查询——"2023 年 6 月 15 日 14:32:00 时刻，用户 ID 88421 的账户余额和持仓详情是什么？"审计时必须能回答
3. **经营分析**：GMV、用户活跃度、资产规模的 T+0 实时统计，早上 9 点开盘前必须是包含凌晨交易的最新数据

现有方案是 Canal + 自研 Python 脚本 + 定时 ETL——端到端延迟 > 5 分钟，月均故障 3 次，维护成本高达 3 人专职。这个方案显然无法满足三大需求。

本章将作为整个高级篇（第 32-39 章）和整个专栏的综合实战，融合前面 39 章的全部知识——源码架构、自定义 SMT/Connector、性能极限调优、数据湖入湖、实时数仓——构建一套可直接交付的金融级 CDC 数据中台底座。

## 2. 架构总设计

```
┌─ 数据源层 ─────────────────────────────────────────────────┐
│ MySQL×5 (交易/账户/风控/清算/营销)  PG×2 (用户/配置)  MongoDB×1  │
│ 200+ 张业务表，日均变更 5 亿行                                  │
└───────┬──────────────────┬──────────────────┬───────────────┘
        │                  │                  │
┌───────┴──────────────────┴──────────────────┴───────────────┐
│              Kafka Connect 集群（6 Worker, 3 AZ）             │
│  ┌────────────────┐ ┌──────────────┐ ┌─────────────────┐   │
│  │ 18 个 Connector │ │ 自定义安全SMT │ │ Avro+Registry   │   │
│  │ MySQL 12+PG 4   │ │ AES加密+脱敏  │ │ Schema 版本管理  │   │
│  │ +MongoDB 2      │ │ 合规扫描      │ │ FULL 兼容策略   │   │
│  └────────────────┘ └──────────────┘ └─────────────────┘   │
│                                                              │
│  信号表驱动增量快照 | 事务元数据开启 | GTID 追踪             │
│  batch.size=16384 | queue.size=131072 | zstd compression    │
└───────────────────────┬──────────────────────────────────────┘
                        │
┌───────────────────────┴──────────────────────────────────────┐
│          Kafka Cluster（3 AZ × 3 Broker, 108 分区）           │
│  峰值 120 万条/s，Avro 序列化，Schema Registry                │
└──┬────────────────────┬──────────────────┬──────────────────┘
   │                    │                  │
   ▼                    ▼                  ▼
实时风控             监管报送           经营分析
Flink CEP            Iceberg (5年)       ClickHouse 宽表
P99 < 50ms           Time Travel         实时 Join 查询
规则匹配             历史快照查询         T+0 统计
```

### 技术决策矩阵

| 需求 | 技术选型 | 决策理由 |
|------|---------|---------|
| CDC 引擎 | Debezium 2.7 + Kafka Connect | 成熟的 offset 管理 + Schema History + SMT 生态 |
| 序列化 | Avro + Schema Registry (FULL) | 体积 < JSON 的 30%，兼容性自动检查 |
| 安全合规 | 自定义 SMT (AES 加密 + 字段脱敏) | 消息进入 Kafka 前完成，数据最小化 |
| 数据持久化 | Apache Iceberg (V2) | Upsert 支持 + Time Travel + Schema Evolution |
| 实时计算 | Apache Flink SQL | 状态后端成熟 + Checkpoint 强劲 |
| OLAP 查询 | ClickHouse | 列存 + 向量化执行 + 实时物化视图 |
| 部署运维 | Strimzi Operator on K8s | GitOps 声明式 + 自动滚动升级 |
| 性能调优 | ZGC + zstd + binlog NOBLOB | < 10ms GC 停顿 + 70% 压缩 |

## 3. 关键实现

### 3.1 金融级安全 SMT——AES 加密 + 字段脱敏

```java
public class EncryptAndMaskTransform<R extends ConnectRecord<R>> 
        extends BaseTransformation<R> {
    
    private static final SecretKey AES_KEY = loadKeyFromKMS();
    
    @Override
    public R apply(R record) {
        Struct after = ((Struct) record.value()).getStruct("after");
        if (after == null) return record;
        
        // L4 机密: AES/GCM 加密 → Base64
        encryptField(after, "bank_account", AES_KEY);
        encryptField(after, "id_card", AES_KEY);
        encryptField(after, "credit_card", AES_KEY);
        
        // L3 敏感: 部分遮盖
        maskField(after, "name", 1);            // "张三丰" → "张**"
        maskPhone(after, "phone");               // 138****5678
        maskPhone(after, "contact_phone");       // 备用手机号
        
        // L2 内部: 保持原样（通过 Kafka ACL 控制读取权限）
        // L1 公开: 无需处理
        
        return record;
    }
    
    private void encryptField(Struct after, String field, SecretKey key) {
        try {
            String plain = after.getString(field);
            if (plain != null) {
                Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
                cipher.init(Cipher.ENCRYPT_MODE, key);
                byte[] encrypted = cipher.doFinal(plain.getBytes(StandardCharsets.UTF_8));
                after.put(field, Base64.getEncoder().encodeToString(encrypted));
            }
        } catch (Exception e) {
            LOGGER.error("Failed to encrypt field: {}", field, e);
        }
    }
    
    private void maskPhone(Struct after, String field) {
        String phone = after.getString(field);
        if (phone != null && phone.length() == 11 && phone.matches("\\d{11}")) {
            after.put(field, phone.substring(0, 3) + "****" + phone.substring(7));
        }
    }
    
    private void maskField(Struct after, String field, int keepChars) {
        String value = after.getString(field);
        if (value != null && value.length() > keepChars + 2) {
            after.put(field, value.substring(0, keepChars) 
                + "*".repeat(Math.min(value.length() - keepChars, 10)));
        }
    }
}
```

### 3.2 全自动一致性校验服务

```java
@Service
public class ConsistencyVerificationService {
    
    private static final Logger LOG = LoggerFactory.getLogger(ConsistencyVerificationService.class);
    
    @Scheduled(cron = "0 0 * * * *")  // 每小时执行
    public void verifyAllDomains() {
        List<VerificationTask> tasks = buildVerificationTasks();
        List<VerificationResult> results = new ArrayList<>();
        
        for (VerificationTask task : tasks) {
            try {
                long srcCount = sourceDataSource.count(task.sourceTable());
                long tgtCount = targetDataSource.count(task.targetTable());
                
                if (srcCount != tgtCount) {
                    // P0: 严重不一致
                    results.add(new VerificationResult(task, false, 
                        String.format("Count mismatch: src=%d tgt=%d diff=%d", 
                            srcCount, tgtCount, Math.abs(srcCount - tgtCount))));
                    
                    // 自动触发增量快照修复
                    signalService.sendSignal(
                        SignalType.EXECUTE_SNAPSHOT,
                        Map.of("data-collections", List.of(task.sourceTable()))
                    );
                    
                    alertingService.sendP0(
                        "数据严重不一致",
                        String.format("表 %s: 源%d行 目标%d行 差异%d行 → 已自动触发增量快照修复",
                            task.sourceTable(), srcCount, tgtCount, Math.abs(srcCount - tgtCount))
                    );
                    
                } else {
                    // 行数一致，但进一步做抽样校验和对比
                    String srcChecksum = sourceDataSource.checksumSample(task.sourceTable(), 1000);
                    String tgtChecksum = targetDataSource.checksumSample(task.targetTable(), 1000);
                    
                    if (!srcChecksum.equals(tgtChecksum)) {
                        results.add(new VerificationResult(task, true,
                            "Checksum mismatch (same row count)"));
                        alertingService.sendWarning(
                            "数据校验和不一致",
                            String.format("表 %s: 行数一致但抽样校验和不同 (抽样1000行)", task.sourceTable())
                        );
                    } else {
                        results.add(new VerificationResult(task, true, "OK"));
                    }
                }
            } catch (Exception e) {
                LOG.error("Verification failed for table: {}", task.sourceTable(), e);
                results.add(new VerificationResult(task, false, "Error: " + e.getMessage()));
            }
        }
        
        // 记录指标
        long passedCount = results.stream().filter(r -> r.passed()).count();
        metricsCollector.recordVerificationRun(results.size(), passedCount);
        LOG.info("Hourly verification completed: {}/{} tables passed", passedCount, results.size());
    }
    
    private List<VerificationTask> buildVerificationTasks() {
        // 从 CMDB 获取源-目标表映射关系
        return configService.getTableMappings().stream()
            .map(m -> new VerificationTask(m.source(), m.target()))
            .toList();
    }
}
```

### 3.3 全链路压测脚本

```bash
#!/bin/bash
# 全链路压力测试 + 实时监控
set -eo pipefail

echo "===== CDC 全链路压测开始 $(date) ====="

# Phase 1: Sysbench 压测 MySQL（模拟 120 万 TPS 写入）
echo "[Phase 1] Sysbench 压测 MySQL..."
sysbench /usr/share/sysbench/oltp_write_only.lua \
  --mysql-host=mysql-prod.internal \
  --mysql-user=debezium_bench \
  --mysql-password="${MYSQL_BENCH_PASSWORD}" \
  --mysql-db=bench_db \
  --tables=10 --table-size=1000000 \
  --threads=128 --time=600 \
  --report-interval=10 \
  run > /tmp/sysbench.log 2>&1 &
SYSBENCH_PID=$!

sleep 10  # 等压测进入稳态

# Phase 2: Prometheus 实时监控端到端延迟
echo "[Phase 2] 监控 CDC 端到端延迟..."
for i in $(seq 1 60); do
    LAG=$(curl -s 'http://prometheus:9090/api/v1/query?query=debezium_MilliSecondsBehindSource' \
        | python3 -c "import sys,json;d=json.load(sys.stdin);print(d['data']['result'][0]['value'][1])")
    echo "[$(date +%H:%M:%S)] CDC Lag = ${LAG}ms"
    sleep 10
done

# Phase 3: 随机抽样验证数据一致性
echo "[Phase 3] 随机抽样验证..."
python3 << 'PYEOF'
import mysql.connector, clickhouse_driver, random, time

SRC = mysql.connector.connect(host="mysql-prod", user="debezium", password="***", database="bench_db")
TGT = clickhouse_driver.Client(host="clickhouse-prod")
TABLES = ["sbtest1", "sbtest2", "sbtest3"]

for _ in range(30):  # 每 10 秒一次，持续 5 分钟
    table = random.choice(TABLES)
    ids = random.sample(range(1, 1000000), 100)
    src_rows = SRC.cursor().execute(f"SELECT id, k, c, pad FROM {table} WHERE id IN ({','.join(map(str,ids))})").fetchall()
    tgt_rows = TGT.execute(f"SELECT id, k, c, pad FROM {table} WHERE id IN ({','.join(map(str,ids))})")
    
    if len(src_rows) != len(tgt_rows):
        print(f"❌ MISMATCH: {table} src={len(src_rows)} tgt={len(tgt_rows)}")
    else:
        print(f"✅ {table}: sampled 100 rows OK")
    time.sleep(10)
PYEOF

# Phase 4: 故障注入——随机 kill Connect Worker
echo "[Phase 4] 故障注入..."
kubectl delete pod -n kafka -l app=connect --force --grace-period=0
echo "Killed Kafka Connect Worker — 监控恢复时间..."

sleep 30
READY=$(kubectl get pods -n kafka -l app=connect --field-selector=status.phase=Running --no-headers | wc -l)
echo "恢复后 Ready Worker 数量: $READY"

wait $SYSBENCH_PID
echo "===== CDC 全链路压测结束 $(date) ====="
```

### 3.4 零停机三项组件升级方案

**场景**：需要同时将 MySQL 5.7→8.0、Kafka 3.5→3.7、Debezium Connector 2.5→2.7 升级，且必须 **99.99% 可用性，零停机**。

```
升级窗口：周六凌晨 2:00-4:00（业务最低谷期）

Phase 1: 数据库层 —— MySQL 5.7 → 8.0（2 小时）
  Step 1: 在 8.0 从库上部署第二个 Connector（server.id 不同, topic.prefix 加 _v2）
  Step 2: 两个 Connector 并行运行（5.7 的旧 Connector + 8.0 的新 Connector）
  Step 3: 下游消费者灰度切换到新 Topic 前缀（通过 Flink SQL 双源 UNION）
  Step 4: 验证 1 小时一致性 → 停旧 Connector → 停 5.7 实例

Phase 2: 中间件层 —— Kafka 3.5 → 3.7（30 分钟）
  Step 1: 逐个 Broker 滚动升级（新 Broker 加入 ISR → 分区迁移 → 旧 Broker 下线）
  Step 2: 每个 Broker 升级间隔 10 分钟，确保 ISR 稳定
  Step 3: 升级完成后验证所有 Topic 的 ISR 健康

Phase 3: 引擎层 —— Connector 2.5 → 2.7（30 分钟）
  Step 1: 构建新版本 Debezium 插件镜像
  Step 2: Strimzi Operator 修改 KafkaConnect.spec.build.image 触发滚动更新
  Step 3: 逐个 Worker Pod 滚动升级（Operator 自动）
  Step 4: 验证所有 Connector 状态 + offset 一致性

Phase 4: 回滚方案（任何 Phase 失败时）
  Phase 1 失败 → 下游切回旧 Topic 前缀 + 停新 Connector
  Phase 2 失败 → Broker 逐个回滚到 3.5（同升级流程逆向）
  Phase 3 失败 → Strimzi Operator revert KafkaConnect version
```

## 4. 验收标准

| 指标 | 目标值 | 实测值 | 状态 |
|------|--------|--------|------|
| 可用性 (SLA) | 99.99% | 99.997% | ✅ |
| 端到端 P99 延迟 | < 1s | 0.8s | ✅ |
| 数据一致性 | > 99.999% | 99.9998% | ✅ |
| 单实例吞吐量 | 80 万 TPS | 95 万 TPS | ✅ |
| 故障自动切换时间 | < 60s | 42s | ✅ |
| RPO（数据丢失） | < 10s | 6s | ✅ |
| PII 合规 | 100% 加密/脱敏 | 100% | ✅ |
| 时间旅行查询 | 5 年内任意时间点 | ✅ | ✅ |

## 5. 最终交付物清单

```
✅ 01. 架构设计文档 (ADR)                                                  [架构师]
✅ 02. 18 个 Connector 配置 JSON 模板（含 Jinja2 渲染脚本）                 [开发]
✅ 03. 自定义安全 SMT 源码（AES 加密 + 脱敏 + 合规扫描）                    [开发]
✅ 04. Ansible Playbook（一键部署全栈 8 DB + Kafka + Connect + 监控）       [运维]
✅ 05. Prometheus + Grafana 监控大盘 JSON                                   [运维]
✅ 06. 全自动一致性校验服务（Spring Boot + K8s CronJob）                     [开发]
✅ 07. 灾难恢复 SOP（塑封卡片 + 自动化脚本）                                 [运维]
✅ 08. 全链路压测脚本 + JMeter 场景配置文件                                 [测试]
✅ 09. 团队培训材料（PPT + Hands-on Lab 实验手册）                          [全员]
✅ 10. 零停机升级方案文档（含回滚路径）                                     [架构师]
```

## 6. 编年史——从 CDC 到数据中台

回顾本专栏的 40 章学习路径：

```
基础篇 (1-16)：从零到一，建立 CDC 核心认知
  环境搭建 → 三种数据库接入 → 事件格式 → 参数体系 → 快照机制
  → Topic 路由 → Avro 序列化 → UI 管理 → 故障排查 → Server 模式
  → 每日运维 → [综合实战] 三数据源 CDC 流水线

中级篇 (17-31)：分布式场景下的架构设计、性能调优、可观测性
  事务元数据 → 高级路由 → 高级 SMT → Schema 演进 → 分布式 Worker
  → 信号表 → 增量快照 → 跨库一致性 → 性能调优 → 多租户治理
  → HA 与恢复 → Grafana 监控 → Strimzi on K8s → 安全合规
  → [综合实战] 多机房 CDC 数据总线

高级篇 (32-40)：源码剖析、自定义扩展、极端场景优化、数据平台落地
  架构全景 → Binlog 解析 → 适配器体系 → 自定义 SMT → 自定义 Connector
  → 百万 TPS 调优 → 数据湖入湖 → 实时数仓宽表
  → [综合实战] 金融级实时数据中台 CDC 底座
```

**终章思考题**：在日均 10 亿行 CDC 数据的金融平台中，需同时完成 MySQL 5.7→8.0、Kafka 3.5→3.7、Connector 2.5→2.7 三项核心组件升级，且必须 99.99% 零停机。假设升级过程中任何一步出现问题——比如 Phase 1 的 8.0 新 Connector 数据一致性验证失败，如何设计完整的回滚方案，确保下游消费者无感知？

---

> **终章寄语**：从第 1 章"什么是 CDC"到第 40 章"百万 TPS 金融级 CDC 数据中台"，本专栏贯穿了一个完整的学习曲线——从零基础到架构师。你已掌握的不是一个工具，而是一套以 CDC 为骨架的实时数据架构方法论。它教你如何在异构数据库丛林里构建数据高速公路、如何让数据以毫秒的延迟流入每个需要它的地方、以及如何从容应对从 10 TPS 到 100 万 TPS 的增长跨越。**Deploy with confidence. Debug with precision. Architect with vision.**
