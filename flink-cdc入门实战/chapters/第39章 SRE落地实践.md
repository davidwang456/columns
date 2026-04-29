# 第39章 SRE落地实践

## 1 项目背景

### 业务场景：CDC系统运维的"至暗时刻"

某公司Flink CDC系统上线半年后，运维团队统计了这段时间的事故记录：

| 事故 | 影响范围 | 根因 | 恢复耗时 |
|------|---------|------|---------|
| Binlog被清理导致全量重新同步 | 核心订单CDC中断6小时 | `expire_log_days`设置不当 | 6小时（重新全量同步） |
| Schema Evolution导致作业崩溃 | 12个Pipeline全挂 | `ALTER TABLE`未通知数据团队 | 45分钟（逐一手动恢复） |
| 大事务OOM Kill | 3个Pipeline节点崩溃 | 批量任务产生10亿行Binlog事件 | 2小时（扩容+调参） |
| Kafka集群写入超限 | 所有CDC作业反压打满 | 高峰期TPS超过Kafka集群容量 | 30分钟（扩容分区） |

这些事故说明：**CDC系统的SRE不仅仅是监控告警，更是一整套包含预防、发现、定位、恢复、改进的完整体系。**

### CDC系统SRE体系架构

```
┌──────────────────────────────────────────────┐
│            CDC SRE 体系                       │
│                                              │
│  预防:                                       │
│  ├── Binlog保留期监控                         │
│  ├── Schema变更审批流程                       │
│  ├── 资源配置上限规划                         │
│  └── 容量预测模型                             │
│                                              │
│  发现:                                       │
│  ├── Prometheus告警规则                       │
│  ├── 端到端延迟探测                           │
│  ├── 数据一致性校验                           │
│  └── 日志异常检测                             │
│                                              │
│  定位:                                       │
│  ├── 反压链路追踪                             │
│  ├── 全链路延迟分析                           │
│  ├── 线程Dump分析                             │
│  └── GC日志分析                              │
│                                              │
│  恢复:                                       │
│  ├── Checkpoint/Savepoint恢复                │
│  ├── 全量重新同步                             │
│  ├── 流量降级方案                             │
│  └── 主备切换预案                             │
│                                              │
│  改进:                                       │
│  ├── 故障复盘机制                             │
│  ├── 混沌工程实验                             │
│  └── 自动化运维工具                           │
└──────────────────────────────────────────────┘
```

---

## 2 项目设计 · 三人交锋对话

**小胖**（擦汗）：这么多事故案例……我光看看就头皮发麻。作为运维，你觉得最应该做的是哪三件事？

**大师**：如果只能做三件事，我会选择：

**第一件：Binlog保护机制**
80%的CDC故障都和Binlog有关。必须：
- 设置`expire_log_days=7`（保留7天）
- 监控Binlog磁盘使用率（预警阈值：70%，严重：85%）
- 新增Slave时同步清理策略

**第二件：灰度发布**
CDC升级/变更流程必须走灰度：
```
开发环境验证 → 测试环境验证(1天) 
→ 生产10%流量(1天) → 生产50%流量(2天) 
→ 生产100%流量(观察1周)
```

**第三件：故障恢复SOP**
遇到故障时按SOP操作，而不是临时想方案：
- Binlog丢失 → 重新全量同步
- OOM → 增加内存 + 调小Chunk Size
- Schema不兼容 → 从Savepoint恢复 + 手动处理DDL

**小白**：那端到端的数据一致性怎么验证？光靠监控指标无法确认"数据有没有丢"吧？

**大师**：一致性验证是CDC SRE的难点。推荐**"离线校验 + 实时抽样"**双保险：

**离线校验（每天一次）：**
```sql
-- 校验MySQL源表和大数据目标表的行数和校验和
-- MySQL
SELECT COUNT(*) AS mysql_cnt, 
       MD5(GROUP_CONCAT(CONCAT(id, status, amount))) AS mysql_checksum
FROM shop.orders;

-- Iceberg目标表（使用Spark SQL）
SELECT COUNT(*) AS iceberg_cnt, 
       MD5(GROUP_CONCAT(CONCAT(id, status, amount))) AS iceberg_checksum
FROM cdc_db.orders;
```

**实时抽样（每条Checkpoint后）：**
每经过一个Checkpoint，在MySQL和中选一条数据对比其完整内容。如果发现差异，立即告警。

**技术映射**：CDC的SRE像"航空公司的飞机维护"——不是等飞机出事了再修，而是定期检查（离线校验）、飞行中监控（实时告警）、有标准的维修流程（故障SOP）。

---

## 3 项目实战

### 分步实现

#### 步骤1：故障案例库

```yaml
# cdc-fault-case-library.yaml
cases:
  - id: F001
    title: Binlog被提前清理导致CDC中断
    symptoms: 作业Checkpoint失败，报错"Binlog file not found"
    root_cause: expire_log_days=3，作业因故障停机4天后恢复时Binlog已被清理
    solution: |
      1. 从最近的全量备份恢复数据
      2. 临时配置scan.startup.mode=latest-offset跳过缺失的Binlog
      3. 将expire_log_days改为7天
      4. 增加Binlog磁盘大小监控告警
    lesson: Binlog保留期必须 > 最大Checkpoint间隔 + 最大恢复时间

  - id: F002
    title: Schema变更导致作业崩溃
    symptoms: 作业报错"Schema doesn't match"，所有Subtask失败
    root_cause: DBA执行ALTER TABLE ADD COLUMN未通知数据团队
    solution: |
      1. 从最近的Savepoint恢复
      2. 设置schema.change.behavior=EVOLVE
      3. 在DBA操作流程中增加CDC通知环节
    lesson: Schema变更需要跨团队协作，使用EVOLVE模式自动适配

  - id: F003
    title: 大事务导致Flink CDC OOM
    symptoms: TaskManager OOM Kill，日志中有"java.lang.OutOfMemoryError"
    root_cause: 批量ETL作业一次UPDATE了3000万行，Debezium缓存了整个事务
    solution: |
      1. 增加TaskManager内存（4G→8G）
      2. 设置debezium.max.batch.size=10240
      3. 将批量作业拆分为每批1000行的小事务
    lesson: 大事务必须拆分，Flink CDC非事务处理利器
```

#### 步骤2：灰度发布流程

```bash
#!/bin/bash
# Flink CDC灰度发布脚本

# 参数
PIPELINE_NAME=${1:?需要Pipeline名称}
NEW_JAR_PATH=${2:?需要新JAR包路径}
CANARY_RATIO=${3:-10}  # 默认10%流量

echo "=== CDC灰度发布: $PIPELINE_NAME ==="

# Step 1: 创建Canary Pipeline（使用新版本）
echo "1. 创建Canary Pipeline ($CANARY_RATIO% 流量)..."
flink-cdc.sh pipeline-${PIPELINE_NAME}-v2.yaml \
  --target kubernetes-application \
  --name "${PIPELINE_NAME}-canary"

# Step 2: 验证Canary运行状态
echo "2. 等待Canary运行5分钟，验证指标..."
sleep 300

# 检查关键指标
echo "   Canary延迟: $(curl -s prometheus:9090/api/v1/query?query=currentFetchDelay{job=~'.*canary.*'})"
echo "   Canary吞吐: $(curl -s prometheus:9090/api/v1/query?query=numRecordsOutPerSecond{job=~'.*canary.*'})"

# Step 3: 逐步切量
echo "3. Canary稳定后，逐步切量..."
# 10% → 50% → 100%

# Step 4: 停止旧版本
echo "4. 停止旧版本Pipeline..."
flink-cdc.sh stop ${PIPELINE_NAME}-v1

echo "=== 灰度发布完成 ==="
```

#### 步骤3：故障恢复SOP模板

```markdown
# Flink CDC 故障恢复SOP

## 一、故障确认（5分钟内）
- [ ] 确认故障Pipeline名称和Job ID
- [ ] 确认故障现象（中断/延迟/数据不一致）
- [ ] 确认影响范围（涉及哪些表、数据源）
- [ ] 确认是否影响线上业务

## 二、初步恢复（15分钟内）
- [ ] 尝试重启作业：`flink run -s <checkpoint-path> ...`
- [ ] 如果Checkpoint不可用：从最新Savepoint恢复
- [ ] 如果Savepoint也不可用：使用`scan.startup.mode=latest-offset`跳过历史数据

## 三、根因定位（30分钟内）
- [ ] 检查Flink Web UI — 反压 / Checkpoint / 日志
- [ ] 检查MySQL — Binlog状态 / 连接数 / server-id冲突
- [ ] 检查目标系统 — Kafka/Doris/ES写入延迟
- [ ] 检查Prometheus告警 — 指标历史趋势

## 四、长期修复（1天内）
- [ ] 根据根因修改配置
- [ ] 灰度发布到生产
- [ ] 更新故障案例库
- [ ] 修改监控告警规则（如需）
```

#### 步骤4：混沌工程——主动注入故障

```bash
#!/bin/bash
# CDC混沌工程实验

echo "=== CDC混沌工程 ==="

# 实验1：模拟MySQL宕机
echo "实验1: MySQL连接中断..."
docker stop mysql-cdc
sleep 30
echo "观察Flink CDC自动重连行为..."
docker start mysql-cdc

# 实验2：模拟Kafka不可用
echo "实验2: Kafka Broker宕机..."
docker stop kafka-cdc
sleep 60
echo "观察Sink反压和作业非崩溃行为..."
docker start kafka-cdc

# 实验3：模拟大量Binlog延迟
echo "实验3: 模拟大事务造成延迟..."
docker exec mysql-cdc mysql -uroot -proot123 -e "
  USE shop;
  -- 模拟阻塞事务
  BEGIN;
  UPDATE orders SET status = status;
  SELECT SLEEP(30);
  COMMIT;
"
```

#### 常见陷坑及解决方法

| 问题 | 原因 | 解决方法 |
|------|------|---------|
| 灰度发布后发现新版本有Bug | 测试覆盖不全 | 立即切回旧版本（保留旧Pipeline），保留Savepoint用于回滚 |
| 故障恢复后数据不一致 | 恢复时跳过了一段Binlog | 使用离线校验任务对比源和目标数据，手动补差 |
| Savepoint恢复失败 | Flink版本升级后Savepoint不兼容 | 旧版本Savepoint → 停止旧作业 → 启动新版本（不加Savepoint）→ 再从最新Checkpoint重放 |
| 自动化SOP执行失败 | 环境差异导致脚本执行异常 | SOP中每个步骤增加手动确认点，确保操作可回退 |

---

## 4 项目总结

### CDC SRE核心指标

| 指标 | 目标值 | 告警条件 |
|------|-------|---------|
| **RTO**（恢复时间目标） | < 30分钟 | Checkpoint恢复后30分钟内达到正常延迟 |
| **RPO**（恢复点目标） | < 5秒 | Checkpoint间隔+恢复延迟不超过5秒 |
| **MTBF**（平均无故障时间） | > 30天 | 连续运行天数 |
| **MTTR**（平均恢复时间） | < 15分钟 | 从发现故障到恢复 |
| **数据一致性** | 99.9999% | 离线校验差异行数/总行数 < 0.0001% |

### 运维工具推荐

| 工具 | 用途 | 推荐配置 |
|------|------|---------|
| Prometheus | 指标存储和告警 | 保留30天数据 |
| Grafana | 可视化大盘 | Flink CDC官方Dashboard + 自定义 |
| Elasticsearch + Kibana | 日志收集和分析 | Flink日志、TaskManager日志 |
| PagerDuty/飞书/钉钉 | 告警通知 | 分级通知（P0电话，P1短信，P2钉钉） |
| ArgoCD/Spinnaker | 灰度发布 | K8s场景下管理CDC Pipeline版本 |

### 思考题

1. **进阶题①**：在设计CDC系统的RTO（恢复时间目标）时，如果Flink CDC作业从Checkpoint恢复需要5分钟，但从Savepoint恢复需要10分钟。在什么场景下应该使用Savepoint而不是Checkpoint？提示：考虑Flink版本升级、Schema变更等场景。

2. **进阶题②**：混沌工程中，如果故意将MySQL的`max_connections`从200降到5，Flink CDC作业会有什么反应？连接池（HikariCP）会重试后失败还是报错？Flink的固定延迟重启策略在这种场景下如何表现？

---

> **下一章预告**：第40章「综合实战：从零构建商业化CDC平台」——高级篇的终章，也是整个专栏的收官之战。结合前39章的所有知识，设计并实现一个商业化CDC数据集成平台的完整方案。
