# 第39章：SonarQube SRE 落地与故障演练

## 1. 项目背景

**业务场景**：某公司将 SonarQube 列为 Tier-2 生产服务（影响 CI/CD 流水线但不直接影响终端用户）。但一次数据库故障导致 SonarQube 停机 8 小时，所有 PR 门禁无法执行——虽然没有导致线上事故，但 300 名开发者的工作流程被完全中断。

运维团队复盘时发现：没有 SLO 定义（可用性目标到底是什么？99.5% 还是 99.9%？）、没有故障演练（从未模拟过 ES 损坏或数据库故障的恢复流程）、没有分级降级策略（SonarQube 不可用时能否让 CI 继续而不阻断部署？）。

**痛点放大**：

- SonarQube 是"软基础设施"——既不像数据库那样有成熟的 SRE 实践，也不像业务服务那样有明确的 SLA
- 故障恢复流程全凭记忆，没有 Runbook
- 备份从未验证过可恢复性
- 没有故障降级方案——要么全死要么全活

**更多现实场景**：

- **场景一：凌晨 3 点的紧急电话**：某个周日的凌晨 3:00，CI Pipeline 突然全部报错 `waitForQualityGate timeout`。值班运维被 PagerDuty 叫醒，登录服务器发现 SonarQube 主页返回 502。检查发现——PostgreSQL 数据库因为磁盘满停止响应。由于没有 Runbook，运维花了 20 分钟摸索才定位到根因，又花了 30 分钟清理临时文件。总共停机 1.5 小时，期间 12 个紧急修复 PR 无法合并。

- **场景二：备份恢复从未验证**：团队一直每周自动备份 PostgreSQL 数据库和 ES 数据目录。备份脚本运行了 6 个月都没有报错。某天 ES 节点磁盘损坏，需要从备份恢复——结果发现：备份的 `pg_dump` 文件因为数据库版本升级已经无法直接恢复，ES 数据目录的备份只备份了目录结构而遗漏了实际数据文件（备份脚本的路径配置错误）。

- **场景三：插件不兼容导致启动失败**：运维升级了 SonarQube 版本，但一个自定义插件与新版本不兼容。SonarQube 启动到一半就卡住了——插件加载失败但进程没有退出，Web API 不响应，存活探针一直检测为"健康"（因为进程还在）。故障持续了 2 小时才被发现。

**SRE 落地的关键里程碑**：
1. 定义 SLO 和服务等级指标（SLI）
2. 编写并培训应急 Runbook
3. 建立故障演练机制
4. 实现降级策略和熔断机制
5. 建立备份恢复的定期验证流程

## 2. 项目设计

### 剧本式交锋对话

---

**小胖**（凌晨 3 点被 PagerDuty 叫醒——SonarQube 挂了）："大师救命！SonarQube 页面 502，CI Pipeline 全堵了！我现在该干什么？！"

**大师**（淡定）："打开 Runbook。第一页就是'SonarQube 故障应急流程'。如果没有 Runbook——现在就是演练的机会。"

**小白**："什么是 Runbook？和普通的运维文档有什么区别？"

**大师**："Runbook 是 Step-by-Step 的应急操作手册——每个步骤只有 2-3 句话，可以闭着眼睛执行。它和运维文档的区别是：运维文档告诉你'SonarQube 的架构是什么'，Runbook 告诉你'SonarQube 挂了，第一个命令敲什么'。

一个好的 Runbook 包含：
1. 症状（如何判断触发了这个故障）
2. 影响范围（哪些团队会受影响）
3. 应急步骤（5 分钟内能执行的止血操作）
4. 恢复步骤（完整的恢复流程）
5. 复盘模板"

**小胖**："那怎么演练？总不能真的把生产 SonarQube 搞挂吧？"

**大师**："专门搭建一个演练环境——配置和生产一样。然后模拟故障场景：数据库不可用、磁盘满了、ES 索引损坏、插件不兼容导致启动失败。每次演练只模拟 1 个故障，4 个故障轮着来——一个季度轮一次。"

**小胖**："SLO 这个东西我们一直没搞清楚——SonarQube 的可用性应该定多少？99.5% 还是 99.9%？两者差别有多大？"

**大师**："让我给你算笔账：

| SLO | 每年允许停机时间 | 每月允许停机时间 | 每周允许停机时间 |
|-----|---------------|---------------|---------------|
| 99.0% | 3.65 天 (87.6h) | 7.3 小时 | 1.68 小时 |
| 99.5% | 1.83 天 (43.8h) | 3.65 小时 | 50 分钟 |
| 99.9% | 8.76 小时 | 43.8 分钟 | 10.1 分钟 |
| 99.99% | 52.6 分钟 | 4.38 分钟 | 1.01 分钟 |

对于大多数企业的 SonarQube 来说，**99.5% 是一个务实的目标**。为什么？

1. **SonarQube 是 Tier-2 服务**：它影响开发流程但不直接影响用户。停机 1 小时不会导致线上事故——只会让 PR 门禁暂停。
2. **99.9% 的维护成本太高**：需要多副本（Data Center 版）、自动故障转移、专门的 SRE 团队轮值。Community Edition 做不到。
3. **计划内维护也需要计入停机**：每月一次的 SonarQube 版本升级预计停机 15 分钟——这在 99.9% 的 SLO 下已经用掉了月预算的一半。

但提醒一点：SLO 不是越低越好——它代表了团队对平台的承诺。99.5% 意味着团队承诺每月最多 3.65 小时不可用，超过这个数就需要写事故报告和改进计划。"

**小白**："我理解了 SLO。那 SLI 怎么选？用哪些指标来衡量服务水平？"

**大师**："SonarQube 需要 4 个核心 SLI：

**SLI-1: 可用性（Availability）**
```
测量方式：健康检查 API 响应 UP 的时间比例
端点：/api/system/health
采集频率：每 30 秒一次
正常响应：{"health": "GREEN"} 或 {"health": "YELLOW"}
异常响应：HTTP 非 200、超时、{"health": "RED"}
```

**SLI-2: 扫描成功率（Scan Success Rate）**
```
测量方式：CE Task SUCCESS / (SUCCESS + FAILED)
采集方式：/api/ce/activity?statuses=SUCCESS,FAILED
时间窗口：过去 24 小时
目标：≥ 95%
```

**SLI-3: CE 处理延迟（CE Processing Latency）**
```
测量方式：从任务提交到处理完成的时间
采集方式：/api/ce/activity 中的 submittedAt 和 executedAt 差值
统计维度：P50、P95、P99
目标：P95 < 5 分钟，P99 < 10 分钟
```

**SLI-4: 门禁评估延迟（Gate Evaluation Latency）**
```
测量方式：Scanner upload 完成到 Quality Gate 状态可查询的时间
采集方式：/api/ce/task 的 executionTimeMs
目标：P95 < 3 分钟
```

推荐使用 Prometheus + Grafana 来实现这些 SLI 的可视化和告警："

```promql
# SLI-1: 可用性 - 过去 30 天的 UP 比例
avg_over_time(probe_success{job="sonarqube-health"}[30d]) * 100

# SLI-2: 扫描成功率 - 过去 24h
sum(rate(sonarqube_ce_task_success[24h])) / 
(sum(rate(sonarqube_ce_task_success[24h])) + sum(rate(sonarqube_ce_task_failed[24h]))) * 100

# SLI-3: CE 处理延迟 P95
histogram_quantile(0.95, rate(sonarqube_ce_processing_duration_seconds_bucket[1h]))

# SLI-4: 错误预算消耗率
(slo_target - current_sli) / slo_target * 100
```

**小胖**："故障演练完了之后呢？怎么确保 Runbook 保持最新？"

**大师**："Runbook 的生命周期管理是 SRE 的核心实践：

**1. 演练后 24 小时内更新**
每次故障演练或真实故障恢复后，立即更新 Runbook。常见更新内容包括：
- 新发现的故障根因和恢复方法
- 之前 Runbook 中不准确或过时的步骤
- 新增的检查命令和验证步骤

**2. 每个季度进行一次'Runbook 评审'**
召集运维团队花 1 小时逐条评审 Runbook 内容。检查：
- SonarQube 版本是否有变化（API 路径、配置文件路径是否变了）
- 部署方式是否有变化（Docker Compose → K8s）
- 环境是否有变化（数据库类型、存储方式）

**3. 设置 Runbook 的版本管理和修改日志**
```markdown
# Runbook 版本历史
| 版本 | 日期 | 修改人 | 修改内容 |
|------|------|--------|---------|
| v1.0 | 2024-01-15 | 张三 | 初始版本，覆盖 3 个应急场景 |
| v1.1 | 2024-02-20 | 李四 | 增加"磁盘满"场景 |
| v1.2 | 2024-03-10 | 张三 | 更新 ES 恢复步骤（从备份恢复→Reindex） |
| v2.0 | 2024-05-01 | 王五 | 迁移到 K8s 部署，重写全部章节 |
```

**4. 新人入职必须用 Runbook 进行一次模拟演练**
新运维入职第一周，用演练环境按照 Runbook 操作一遍。这样既能验证 Runbook 的可执行性，也能加速新人上手。"

---

## 3. 项目实战

### 3.1 分步实现

**步骤 1：定义 SonarQube SLO**

| 指标 | 目标 | 测量方式 |
|------|------|---------|
| 可用性 | ≥ 99.5%（月） | 健康检查 API 的 UP 时间比例 |
| 扫描成功率 | ≥ 95% | CE Task SUCCESS / Total |
| CE 处理延迟 | P99 < 5 分钟 | Task execution time |
| 门禁响应时间 | P95 < 3 分钟 | Scanner 提交到 Gate 返回时间 |

```promql
# Prometheus SLO 查询示例
# 可用性：健康检查成功的比例
avg_over_time(sonarqube_health{component="web"}[30d]) * 100
```

**步骤 2：编写应急 Runbook**

**场景 A：SonarQube Web UI 不可用（502/503）**

```
症状：浏览器访问 → 502 Bad Gateway / 页面白屏
影响：所有开发者无法查看质量报告；CI 中的 waitForQualityGate 会超时

应急步骤（5 分钟内）：
1. docker compose ps                         # 确认容器状态
2. docker compose logs sonarqube --tail 50   # 查看最近日志
3. curl http://localhost:9000/api/system/health # API 是否正常

常见根因：
- PostgreSQL 连接失败 → 检查数据库容器
  docker compose ps postgres
  docker compose exec postgres pg_isready -U sonar

- ES OOM → 检查 ES 日志
  docker compose exec sonarqube tail -50 /opt/sonarqube/logs/es.log | grep -i "OutOfMemory"

- 磁盘满 → df -h /opt/sonarqube/data

恢复步骤：
- DB 故障 → 如果 DB 是外部服务，上报 DBA 团队
- ES OOM → SONAR_SEARCH_JAVAOPTS=-Xmx2g 增加内存后重启
- 磁盘满 → 清理日志 + 旧数据（见第15章）
```

**场景 B：CE 队列积压（所有新扫描无法显示结果）**

```
症状：扫描完成但不出现数据；ce.log 中出现 timeout
影响：CI 中的 waitForQualityGate 超时

应急步骤：
1. 查看积压量：
   curl http://localhost:9000/api/ce/activity?statuses=PENDING
2. 查看 Worker 状态：
   curl http://localhost:9000/api/ce/worker_count

恢复步骤：
- 数据库连接池耗尽 → SONAR_JDBC_MAXACTIVE=60 扩容
- CE 卡死 → docker compose restart sonarqube（快速重启）
```

**步骤 3：故障演练计划**

```bash
#!/bin/bash
# chaos-engineering.sh - SonarQube 故障演练脚本

SCENARIO=$1

case $SCENARIO in
  db-down)
    echo "模拟数据库故障..."
    docker compose stop postgres
    echo "验证：SonarQube 应该返回 503"
    curl -o /dev/null -s -w "%{http_code}" http://localhost:9000
    ;;
  disk-full)
    echo "模拟磁盘满..."
    dd if=/dev/zero of=/tmp/fill-disk bs=1M count=5000
    echo "验证：ES 是否进入只读模式"
    ;;
  es-corrupt)
    echo "模拟 ES 索引损坏..."
    docker compose exec sonarqube rm -rf /opt/sonarqube/data/es8/nodes
    echo "恢复：重新创建 EC2 索引...需要重启后自动重建"
    ;;
  *)
    echo "用法: $0 [db-down|disk-full|es-corrupt]"
    ;;
esac
```

**步骤 4：建立降级策略**

| 故障级别 | 影响 | 降级策略 |
|---------|------|---------|
| P1 - SonarQube 全挂 | CI 门禁全部超时 | 暂时关闭 CI 中的门禁检查（允许合并），等恢复后批量补扫 |
| P2 - ES 不可用 | Web UI 搜索不可用，新数据不展示 | CI 仍可扫描（Scanner 上传正常），但门禁等待超时 |
| P3 - CE 积压 | 新扫描结果延迟 | 正常扫描但门禁等待时间延长，超过阈值时报超时 |

**步骤 5：建立自动化健康检查与告警**

```yaml
# docker-compose.yml 中加入健康检查
services:
  sonarqube:
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9000/api/system/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 180s  # 等 3 分钟启动
```

```promql
# Prometheus 告警规则示例
groups:
  - name: sonarqube
    rules:
      - alert: SonarQubeDown
        expr: probe_success{job="sonarqube-health"} == 0
        for: 2m
        labels:
          severity: critical
        annotations:
          summary: "SonarQube is DOWN"
          description: "健康检查失败超过 2 分钟，立即查看 Runbook 场景 A"

      - alert: SonarQubeCEQueueBacklog
        expr: sonarqube_ce_pending_tasks > 50
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "CE 队列积压 {{ $value }}"
          description: "CE 队列 PENDING 超过 50 个任务持续 10 分钟"

      - alert: SonarQubeScanFailureRateHigh
        expr: |
          (sum(rate(sonarqube_ce_task_failed[1h])) / 
           sum(rate(sonarqube_ce_task_total[1h]))) > 0.1
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "扫描失败率 > 10%"
          description: "过去 1 小时扫描失败率超过 10%，检查 CE 日志"

      - alert: SonarQubeErrorBudgetBurnRateHigh
        expr: |
          (1 - avg_over_time(probe_success{job="sonarqube-health"}[1h])) /
          (1 - 0.995) > 5
        for: 1h
        labels:
          severity: critical
        annotations:
          summary: "错误预算消耗速度过快"
          description: "过去 1 小时的故障率是 SLO 预算率的 5 倍以上"
```

**步骤 6：定期备份恢复验证脚本**

```bash
#!/bin/bash
# backup-restore-drill.sh - 备份恢复验证演练

set -e

BACKUP_FILE="/backup/sonarqube/sonar_$(date +%Y%m%d).dump"
RESTORE_INSTANCE_NAME="sonarqube-restore-test"
RESTORE_PORT=9001

echo "=== SonarQube 备份恢复验证 $(date) ==="

# 1. 检查备份文件是否存在且非空
echo "[1/6] 检查备份文件..."
if [ ! -f "$BACKUP_FILE" ]; then
    echo "❌ 备份文件不存在: $BACKUP_FILE"
    exit 1
fi

BACKUP_SIZE=$(stat -f%z "$BACKUP_FILE" 2>/dev/null || stat -c%s "$BACKUP_FILE" 2>/dev/null)
if [ "$BACKUP_SIZE" -lt 1024 ]; then
    echo "❌ 备份文件异常: $BACKUP_SIZE bytes"
    exit 1
fi
echo "  备份文件大小: $(du -h "$BACKUP_FILE" | cut -f1)"

# 2. 启动一个干净的 PostgreSQL 实例进行恢复测试
echo "[2/6] 启动测试用 PostgreSQL..."
docker run -d --name "$RESTORE_INSTANCE_NAME-db" \
    -e POSTGRES_USER=sonar -e POSTGRES_PASSWORD=test \
    -e POSTGRES_DB=sonar \
    postgres:15-alpine

sleep 5

# 3. 执行恢复
echo "[3/6] 恢复数据库..."
docker exec -i "$RESTORE_INSTANCE_NAME-db" \
    pg_restore -U sonar -d sonar --clean --if-exists < "$BACKUP_FILE"

# 4. 验证关键数据
echo "[4/6] 验证恢复数据..."
PROJECT_COUNT=$(docker exec "$RESTORE_INSTANCE_NAME-db" \
    psql -U sonar -d sonar -t -c "SELECT COUNT(*) FROM projects WHERE enabled=true")
ISSUE_COUNT=$(docker exec "$RESTORE_INSTANCE_NAME-db" \
    psql -U sonar -d sonar -t -c "SELECT COUNT(*) FROM issues")

echo "  恢复的项目数: $PROJECT_COUNT"
echo "  恢复的 Issue 数: $ISSUE_COUNT"

if [ "$PROJECT_COUNT" -gt 0 ] && [ "$ISSUE_COUNT" -gt 0 ]; then
    echo "✅ 数据恢复验证通过"
else
    echo "❌ 数据恢复异常：项目数或 Issue 数为 0"
    exit 1
fi

# 5. 清理测试环境
echo "[5/6] 清理测试环境..."
docker rm -f "$RESTORE_INSTANCE_NAME-db"

# 6. 记录验证结果
echo "[6/6] 记录验证日志..."
echo "$(date '+%Y-%m-%d %H:%M:%S') | BACKUP_VERIFY | SUCCESS | $BACKUP_FILE | projects=$PROJECT_COUNT, issues=$ISSUE_COUNT" \
    >> /var/log/sonarqube/backup-verify.log

echo "=== 验证完成 ==="
```

**步骤 7：错误预算（Error Budget）看板搭建**

```python
#!/usr/bin/env python3
"""SonarQube 错误预算监控看板"""
import requests
from datetime import datetime, timedelta

SONAR_URL = "http://localhost:9000"
SONAR_AUTH = ("admin", "Sonar@2024Admin")
SLO_TARGET = 99.5  # 可用性目标

def get_uptime_last_30d():
    """计算过去 30 天的可用性"""
    # 从 CE activity 推算服务活跃时间
    resp = requests.get(
        f"{SONAR_URL}/api/ce/activity",
        auth=SONAR_AUTH,
        params={"ps": 1, "statuses": "SUCCESS"}
    )
    return 99.7  # 示例值，实际应从监控系统获取

def get_error_budget_remaining():
    uptime = get_uptime_last_30d()
    downtime_minutes = (100 - uptime) * 30 * 24 * 60 / 100
    budget_minutes = (100 - SLO_TARGET) * 30 * 24 * 60 / 100
    remaining = budget_minutes - downtime_minutes
    return remaining, budget_minutes

remaining, budget = get_error_budget_remaining()

print(f"""
=== SonarQube 错误预算看板 ===
SLO 目标: {SLO_TARGET}%
月度错误预算: {budget:.0f} 分钟
已使用: {budget - remaining:.0f} 分钟
剩余预算: {remaining:.0f} 分钟 ({remaining/budget*100:.1f}%)
状态: {'✅ 健康' if remaining > budget * 0.5 else '⚠️ 预算消耗过快' if remaining > 0 else '❌ 预算已耗尽'}
""")
```

### 3.2 验证

```bash
# 验证备份恢复
# 1. 从备份恢复数据库
docker compose exec -T postgres pg_restore -U sonar -d sonar < backup.dump
# 2. 启动 SonarQube
docker compose up -d sonarqube
# 3. 健康检查和项目数验证
sleep 120
curl -s -u admin:Sonar@2024Admin "http://localhost:9000/api/system/health"
curl -s -u admin:Sonar@2024Admin "http://localhost:9000/api/projects/search?ps=1" \
  | python3 -c "import sys,json;print(f'项目数: {json.load(sys.stdin)[\"paging\"][\"total\"]}')"
```

---

## 4. 项目总结

### 4.1 SRE 成熟度模型

| 等级 | 特征 | 建议行动 |
|------|------|---------|
| L0 无管理 | 没有监控、没有备份 | 建立基本监控和备份 |
| L1 被动响应 | 出问题再修，凭记忆操作 | 编写 Runbook |
| L2 主动管理 | 有监控告警、有备份恢复流程 | 定期故障演练 |
| L3 可量化 | 有 SLO、有容量规划 | 优化 SLO 目标 |
| L4 自动化 | 自动扩缩容、自愈 | 实现自动降级和恢复 |

### 4.2 应急联系人与升级路径

| 故障级别 | 响应时间 | 升级条件 | 通知对象 |
|---------|---------|---------|---------|
| P1 - 全挂 | 15 分钟内 | 30 分钟内未恢复 | 运维团队 + 开发 Lead |
| P2 - 部分降级 | 30 分钟内 | 1 小时内未恢复 | 运维团队 |
| P3 - 延迟升高 | 60 分钟内 | 积压持续增长 | 运维值班 |

### 4.3 SonarQube 故障模式与影响分析 (FMEA) 速查

| 故障模式 | 影响 | 严重度 | 发生概率 | 检测手段 | 恢复时间 |
|---------|------|--------|---------|---------|---------|
| PostgreSQL 不可用 | 全平台不可用 | 严重 | 低 | 健康检查 | 取决于 DB 恢复 |
| ES OOM | 搜索不可用、新数据不展示 | 高 | 中 | 内存监控 | 重启 5-10 分钟 |
| 磁盘满 | ES 只读、CE 无法写入 | 严重 | 中 | 磁盘监控 | 清理后重启 |
| CE 队列积压 | 扫描结果延迟 | 中 | 高 | 队列监控 | 调整 Worker 配置 |
| 插件不兼容 | 启动失败 | 严重 | 低 | 启动日志检查 | 移除插件后重启 |
| 网络分区（K8s） | Web 和 DB 断连 | 严重 | 低 | 连通性探测 | K8s 自愈机制 |

### 4.4 Runbook 模板

```markdown
# Runbook: [故障场景名称]
版本: v1.0  |  最后更新: YYYY-MM-DD  |  负责人: [姓名]

## 症状
- 用户报告: [用户看到的异常]
- 监控告警: [Prometheus/Grafana 告警名称]
- 日志特征: [关键错误日志片段]

## 影响范围
- 受影响团队: [列出团队和人数]
- 业务影响: [PR 阻塞 / 无法查看质量报告 / ...]

## 应急步骤 (前 5 分钟)
1. [命令 1 - 查看容器/进程状态]
2. [命令 2 - 检查核心依赖（DB、ES）]
3. [命令 3 - 查看最近日志中的错误]

## 根因判断
- 如果 [条件 A] → 执行恢复方案 A
- 如果 [条件 B] → 执行恢复方案 B

## 恢复步骤
### 方案 A: [场景描述]
1. [步骤 1]
2. [步骤 2]
3. [验证: curl /api/system/health]

### 方案 B: [场景描述]
1. [步骤 1]
2. [步骤 2]

## 恢复验证
- [ ] 健康检查 API 返回 GREEN
- [ ] 项目列表正常加载
- [ ] 最近一次扫描结果可见
- [ ] CI Pipeline 门禁恢复正常

## 事后复盘
- 故障发生时间: [YYYY-MM-DD HH:MM]
- 恢复时间: [YYYY-MM-DD HH:MM]
- 总停机时长: [分钟]
- 根因: [简述]
- 改进措施: [待办项]
```

### 4.5 注意事项

1. **Runbook 要定期更新**：每次故障处理后 24 小时内更新 Runbook。
2. **故障演练在测试环境做**：不要在生产环境中模拟，除非你已经驾轻就熟且有完善的回滚方案。
3. **降级策略需要提前和团队沟通**：当 SonarQube 不可用时，CI 是应该"放宽门禁"还是"暂停部署"——这个决策需要管理层提前批准。

### 4.6 思考题

1. SonarQube 的可用性目标 99.5% vs 99.9%——两者的年度允许停机时间分别是多少？哪个更适合你们的场景？
2. 如果 SonarQube 停机 2 小时，但 CI 中配置了门禁阻断——你选择等待恢复还是临时关闭门禁？为什么？

> **答案提示**：第1题 99.5% = 每年最多 43.8 小时不可用；99.9% = 每年最多 8.76 小时。选择取决于团队对 CI 门禁的依赖程度和平台维护预算。

---

> **推广计划提示**：SRE 落地建议从"最小的 Runbook"开始——1 页纸，覆盖 3 个最常见的故障场景。当团队亲自经历过 1-2 次故障恢复后，Runbook 自然会丰富起来。
