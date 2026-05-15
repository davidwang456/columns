# 第32章：SonarQube 内部架构深挖

## 1. 项目背景

**业务场景**：某公司的架构师团队在评估是否基于 SonarQube 构建企业质量中台时，需要深入理解其内部架构——特别是在面对以下问题时：扫描完成后数据如何流转？CE 处理失败时如何追踪？插件加载机制是怎样的？数据如何存储在 PostgreSQL 和 Elasticsearch 中？

- **插件加载机制是怎样的？**数据如何存储在 PostgreSQL 和 Elasticsearch 中？

产品文档对这些问题只有简要描述，源码级知识才能回答这些深层次问题。本章将从架构师视角深入 SonarQube 的内部组件、数据流和插件体系，为后续的自定义开发、性能调优和高级治理打下基础。

**痛点放大**：不理解架构就无法真正排障——CE 任务为什么卡住了？Issue 为什么入库了但搜索不到？自定义插件该怎么挂载？这些问题的根因都在架构设计中。

- **Web Server 和 CE 的通信模型不透明**：扫描报告通过 HTTP 上传，但 CE 是如何异步处理的？如果 CE 重启，PENDING 任务会丢吗？
- **数据库表结构没有官方文档**：100+ 张表的关系、索引设计、数据生命周期——排查数据问题时只能靠逆向工程
- **ES 索引与 PostgreSQL 的双写一致性**：Update 一个 Issue 的状态时，PG 和 ES 怎么写？如果 ES 写入失败会怎样？
- **多节点部署的内部机制**：社区版只支持单节点，但 DataCenter 版如何实现 CE 的任务分发、Web Server 的负载均衡？
- **Scanner 端的分析上下文（SensorContext）生命周期**：理解这个生命周期才能写出高效的自定义 Sensor

**深入架构的价值矩阵**：

| 架构知识 | 解决的实际问题 |
|---------|--------------|
| CE 任务处理流程 | 排查"扫描成功但看不到结果" |
| 数据库表关系 | 自定义报表、数据迁移 |
| 插件加载+ClassLoader 隔离 | 插件冲突排障、自定义插件开发 |
| ES 索引映射 | 理解为什么某些 API 查询慢 |
| Web API 层设计 | 构建高质量的自定义集成 |

## 2. 项目设计

### 剧本式交锋对话

---

**小胖**（打开 SonarQube 的开源仓库，看到几十个 Maven 模块）："大师，SonarQube 的源码里有一堆模块——sonar-core、sonar-server、sonar-plugin-api、sonar-scanner-engine……这些模块分别负责什么？"

**大师**："SonarQube 的源码按职责分为几大块：

- **sonar-plugin-api**：插件 API，定义了 Plugin、Sensor、Rule、MeasureComputer 等核心接口。所有扩展（语言分析器、自定义规则、Web 部件）都依赖这个模块。
- **sonar-core**：核心工具类和数据模型（Component、Issue、Measure 等）。
- **sonar-scanner-engine**：Scanner 端引擎，负责文件索引、Sensor 调度、报告生成。
- **sonar-server**：服务端，包含 Web Server、Compute Engine、Search、数据库访问层。
- **sonar-db**：数据库访问层，封装了 MyBatis 和 DAO 操作。
- **sonar-ws**：Web API 定义和客户端。

从架构视角看，分 4 层：

```
┌─────────────────────────────────────────┐
│              Web UI / API                │  ← 用户交互层
├─────────────────────────────────────────┤
│  Web Server  │  Compute Engine  │ Search │  ← 服务处理层
├─────────────────────────────────────────┤
│          PostgreSQL  │  Elasticsearch    │  ← 数据存储层
├─────────────────────────────────────────┤
│  Scanner Engine  │  Plugins             │  ← 分析执行层
└─────────────────────────────────────────┘
```"

**小白**："一次完整的扫描数据流是怎样的？从 Scanner 上传报告到最终在 Web UI 看到结果，经过了哪些步骤？"

**大师**：

"1. Scanner 执行分析 → 生成 Protobuf 格式的分析报告
2. Scanner 通过 HTTP POST 将报告上传到 Web Server
3. Web Server 将报告写入磁盘暂存，并在数据库中创建 CE Task 记录（状态：PENDING）
4. Compute Engine Worker 轮询到 PENDING 任务 → 取出分析报告
5. CE 执行指标计算（MeasureComputer）、Issue 关联、Quality Gate 评估
6. CE 将结果写入 PostgreSQL（measures、issues、components 表）
7. CE 将搜索数据索引到 Elasticsearch
8. CE 更新 Task 状态为 SUCCESS
9. Web UI 查询数据时：部分指标从 PostgreSQL 读（精确），部分（如 Issue 列表）从 ES 读（快速搜索）

其中步骤 5-7 是最复杂的——如果这里失败，扫描日志显示 SUCCESS 但 Web UI 看不到结果，因为报告已上传但 CE 处理失败了。"

**小胖**："我最头疼的场景——扫描结果显示 SUCCESS，但去 SonarQube Web UI 里看不到项目的任何 Issue。这是哪个环节出了问题？怎么排查？"

**大师**："这是典型的 CE 处理失败场景。排查路径是：

```bash
# 1. 确认报告已上传
curl -s -u admin:Sonar@2024Admin \
  "http://localhost:9000/api/ce/activity?ps=5&statuses=FAILED" \
  | python3 -c "
import sys, json
for t in json.load(sys.stdin)['tasks']:
    print(f'任务ID: {t[\"id\"]}')
    print(f'组件: {t[\"componentKey\"]}')
    print(f'状态: {t[\"status\"]}')
    print(f'错误: {t.get(\"errorMessage\", \"无\")[:200]}')
    print()"

# 2. 查看 CE 日志中的具体错误
docker compose exec sonarqube tail -200 /opt/sonarqube/logs/ce.log | grep -A5 "ERROR\|WARN"

# 3. 检查是否有分析报告文件残留
docker compose exec sonarqube ls -la /opt/sonarqube/data/ce/

# 4. 常见失败原因：
# - OutOfMemoryError: CE 处理大项目时堆不够 → 增加 sonar.ce.javaOpts=-Xmx4g
# - 数据库连接超时: CE 等待数据库连接池分配连接 → 增加 sonar.jdbc.maxActive
# - ES 写入失败: 磁盘满或 ES 节点不可用 → 检查 ES 状态
```

关键认知：Scanner 端的成功 ≠ CE 端处理成功。Scanner 显示 'EXECUTION SUCCESS' 只代表报告已生成并上传——就像快递已揽件但不代表已送达。"

**小白**："SonarQube 的双存储模型——PostgreSQL 和 Elasticsearch——数据是怎么分配职责的？如果 ES 挂了还能用吗？"

**大师**："职责分工很清晰：

| 数据类型 | PostgreSQL（主存储）| Elasticsearch（索引存储）|
|---------|-------------------|------------------------|
| 项目元数据 | ✅ 唯一存储 | ❌ 不存 |
| Issue | ✅ 权威数据 | ✅ 全文搜索用 |
| 指标值 (Measures) | ✅ 权威数据 | ✅ 聚合查询用 |
| 代码行/文件 | ✅ 存储 | ✅ 存储 |
| Quality Gate 配置 | ✅ 唯一存储 | ❌ 不存 |
| CE 任务 | ✅ 唯一存储 | ❌ 不存 |
| 用户/权限 | ✅ 唯一存储 | ❌ 不存 |

ES 挂了的影响：SonarQube Web UI 仍然可用，但搜索 Issue、组件、查看大部分指标列表都会报错——因为这些查询走的是 ES。基本页面（项目列表、系统设置）不受影响，因为这些走 PG。

这就是为什么 PG 是 'Source of Truth'——即使 ES 索引损坏，可以通过系统功能 → Reindex 从 PG 重建。但反过来不行——PG 数据丢了，ES 索引救不回来。"

**小胖**："DataCenter 版（商业版）的 CE 集群模式——多个 CE Worker 怎么避免重复处理同一个任务？"

**大师**："DataCenter 版使用数据库作为'任务队列锁'。当 CE Worker 轮询到 PENDING 任务时：

```
Worker A 轮询 → SELECT * FROM ce_queue WHERE status='PENDING' 
    → UPDATE SET status='IN_PROGRESS', worker_uuid='worker-A' 
        WHERE uuid='task-123' AND status='PENDING'  -- 乐观锁
    → 如果 UPDATE 影响行数 > 0 → Worker A 获得任务
    → 如果 UPDATE 影响行数 = 0 → 被其他 Worker 抢了，跳过
```

社区版只有 1 个 Worker，所以不存在竞争。这个设计就是数据库乐观锁的经典应用——用 `WHERE status='PENDING'` 作为锁条件，避免了分布式锁的复杂性。

实际部署建议：即使是 DataCenter 版，CE Worker 数也不宜超过 CPU 核心数的 50%。CE 任务是 CPU + IO 密集型——Worker 太多会争抢数据库连接和 ES 写入带宽，反而降低吞吐。"

---

## 3. 项目实战

### 3.1 分步实现

**步骤 1：跟踪 CE 任务数据流**

查询一次扫描对应的 CE Task：

```bash
# 获取最近的 CE 任务列表
curl -s -u admin:Sonar@2024Admin \
  "http://localhost:9000/api/ce/activity?ps=5" \
  | python3 -c "
import sys, json
for t in json.load(sys.stdin)['tasks']:
    print(f\"ID: {t['id']}\")
    print(f\"  Type: {t['type']}\")
    print(f\"  Component: {t.get('componentName', t.get('componentKey', 'N/A'))}\")
    print(f\"  Status: {t['status']}\")
    print(f\"  Submitted: {t['submittedAt']}\")
    print(f\"  Executed: {t.get('executedAt', 'N/A')}\")
    print()"

# 查看某一任务的详细信息
curl -s -u admin:Sonar@2024Admin \
  "http://localhost:9000/api/ce/task?id=AXeqW7sdf123" \
  | python3 -m json.tool
```

**步骤 2：探索数据库表结构**

连接 PostgreSQL 查看关键表：

```sql
-- 组件（项目、模块、文件）
SELECT kee, name, scope, qualifier FROM components WHERE scope = 'PRJ' LIMIT 5;

-- 指标（覆盖率、Bug 数等）
SELECT c.name, m.name, pm.value
FROM project_measures pm
JOIN metrics m ON pm.metric_id = m.id
JOIN components c ON pm.component_uuid = c.uuid
WHERE pm.analysis_uuid = (SELECT uuid FROM snapshots ORDER BY created_at DESC LIMIT 1)
LIMIT 10;

-- Issue 表
SELECT issue_key, rule_key, severity, status, message
FROM issues WHERE project_uuid = (
  SELECT uuid FROM components WHERE kee = 'com.example:order-service'
) LIMIT 5;

-- CE 任务表
SELECT uuid, task_type, status, created_at, started_at, executed_at
FROM ce_activity ORDER BY created_at DESC LIMIT 5;
```

**步骤 3：理解插件加载机制**

查看已安装插件：

```bash
curl -s -u admin:Sonar@2024Admin \
  "http://localhost:9000/api/plugins/installed" \
  | python3 -c "
import sys, json
for p in json.load(sys.stdin)['plugins']:
    print(f\"{p['key']:40s} v{p.get('version', '?')}\")
    print(f\"  Name: {p['name']}\")
    print(f\"  Implementation: {p.get('implementationBuild', 'N/A')}\")
    print()"
```

插件加载顺序：
1. SonarQube 启动时扫描 `extensions/plugins/` 目录
2. 加载所有 `.jar` 文件，通过 `META-INF/MANIFEST.MF` 中的 `Plugin-Class` 找到入口
3. 调用 `Plugin.define(Context)` 方法注册扩展（Sensor、Rule、MeasureComputer 等）
4. 插件通过独立的 ClassLoader 隔离加载，避免类冲突

**步骤 4：数据存储路径**

```bash
# 查看数据目录结构
docker compose exec sonarqube ls -la /opt/sonarqube/data/
# es8/     → Elasticsearch 索引数据
# web/     → Web 服务中间文件
# ce/      → CE 任务中间文件

# 查看 ES 索引
curl -s "http://localhost:9001/_cat/indices?v"
# 关键索引：components, issues, measures, projectmeasures
```

**步骤 5：深入理解 Issue 生命周期（状态机）**

SonarQube 的 Issue 状态是一个非常严格的状态机：

```
OPEN → CONFIRMED → REOPENED
  ↓         ↓
  ├─────────┼──→ RESOLVED (FIXED / FALSE_POSITIVE / WONTFIX)
  └─────────┘         ↓
                 CLOSED（在下次扫描中不再出现时自动关闭）
```

对应的数据库变化：

```sql
-- Issue 创建（Scanner 发现新 Issue）
INSERT INTO issues (kee, rule_key, severity, status, issue_type, ...)
VALUES ('uuid', 'java:S1234', 'MAJOR', 'OPEN', 'BUG', ...);

-- Issue 被标记为 Won't Fix
UPDATE issues 
SET status = 'RESOLVED', resolution = 'WONTFIX', 
    updated_at = NOW()
WHERE kee = 'uuid';

-- 下次扫描时 Issue 不再出现 → 自动关闭
UPDATE issues 
SET status = 'CLOSED', updated_at = NOW()
WHERE kee = 'uuid' AND status = 'RESOLVED';
-- (这个过程由 CE 在分析完成后执行)
```

理解这个状态机对于排查 "Issue 为什么又被重新打开了" 这类问题很重要。

**步骤 6：理解 SonarQube 的 API 分层设计**

```
┌──────────────────────────────────┐
│      Web API (REST + JSON)       │  ← api/issues, api/measures, ...
├──────────────────────────────────┤
│      Service Layer               │  ← IssueService, MeasureService, ...
├──────────────────────────────────┤
│      DAO / MyBatis Mapper        │  ← IssueMapper.xml, ...
├──────────────────────────────────┤
│      Database (PostgreSQL)       │
└──────────────────────────────────┘
```

每个 API 端点对应一个 `org.sonar.server.*.ws.*Action` 类。如果你需要扩展 API，需要实现的接口是 `org.sonar.api.server.ws.WebService`。

### 3.2 验证

```bash
# 验证各组件的连接状态
curl -s -u admin:Sonar@2024Admin \
  "http://localhost:9000/api/system/info" \
  | python3 -c "
import sys, json
info = json.load(sys.stdin)
print('Web:', info.get('System',{}).get('Web Server'))
print('CE:', info.get('System',{}).get('Compute Engine'))
print('ES:', info.get('System',{}).get('Elasticsearch',{}).get('State'))
print('DB:', info.get('System',{}).get('Database'))"
```

---

## 4. 项目总结

### 4.1 架构要点速查

| 组件 | 职责 | 关键源码路径 |
|------|------|-------------|
| Plugin API | 扩展接口定义 | `sonar-plugin-api/src/main/java/org/sonar/api/` |
| Scanner | 分析执行 | `sonar-scanner-engine/` |
| Web Server | HTTP + UI | `sonar-server/src/main/java/org/sonar/server/` |
| Compute Engine | 数据处理 | `server/sonar-ce/` |
| Database | 数据持久化 | `sonar-db/` |
| Search | 快速检索 | 内嵌 Elasticsearch |

### 4.2 注意事项

1. **不要直接操作数据库**：SonarQube 的数据库 Schema 是内部实现细节，可能在版本升级时变化。通过 API 访问数据。
2. **ES 索引可能会损坏**：如果 Web UI 搜索不到数据但数据库中有——尝试重建 ES 索引（Administration → System → Reindex）。
3. **插件 ClassLoader 隔离**：插件之间不能直接访问对方的类。如果需要共享代码，通过 `sonar-plugin-api` 的扩展点实现。
4. **CE 任务没有重试机制**：CE 任务如果 FAILED，不会自动重试——你需要重新触发一次扫描。如果 CE 频繁 FAILED，排查根因而非手动重试。
5. **PG 连接池是瓶颈信号的来源**：CE Worker 堵塞最常见的原因不是 CPU，而是数据库连接池耗尽——`Active connections > maxActive * 0.8` 时就要扩容。
6. **API 速率限制**：社区版没有内置速率限制——如果你用脚本高频调用 API（QPS > 10），会压垮 SonarQube。建议通过 `sonar.web.connections.maxThreads` 间接控制。

### 4.3 架构层面的排障决策树

```
问题现象                              → 排查方向
─────────────────────────────────────────────────────────
扫描慢 (Scanner端)                   → Scanner JVM 内存 / 规则数 / exclusions
扫描显示成功但 Web UI 无数据          → CE 日志（ce.log）
CE 队列积压                          → CE Worker 数 / 数据库连接池 / ES 状态
Issue 搜不到                          → ES 索引 → 重建索引
API 响应慢 (>5s)                      → ES 查询 / 数据库慢查询
插件加载失败                          → 插件兼容性 / ClassLoader
数据库连接耗尽                        → 连接池配置 / 检查 CE 任务是否卡住
Web UI 白屏或 500                     → Web Server 日志（web.log）
```

### 4.4 关键数据库表速查（高级使用者视角）

| 表名 | 用途 | 关键字段 | 备注 |
|------|------|---------|------|
| `components` | 项目/文件/模块 | `kee`, `uuid`, `scope`, `qualifier` | 核心表，所有其他表都关联它 |
| `snapshots` | 每次分析的快照 | `uuid`, `component_uuid`, `created_at` | 分析历史 |
| `project_measures` | 项目指标值 | `metric_id`, `analysis_uuid`, `value` | 与 metrics 表 JOIN |
| `metrics` | 指标定义 | `name`, `key`, `domain` | 如 coverage, bugs, ncloc |
| `issues` | Issue 记录 | `kee`, `rule_key`, `status`, `severity` | Issue 状态机 |
| `issue_changes` | Issue 变更历史 | `issue_key`, `change_type`, `user_login` | 审计追踪 |
| `ce_activity` | CE 任务 | `uuid`, `status`, `error_message` | 任务历史 |
| `ce_queue` | CE 任务队列 | `uuid`, `status`, `worker_uuid` | 当前队列 |
| `quality_gate_conditions` | 门禁条件 | `qgate_id`, `metric_id`, `operator`, `error_value` | 门禁定义 |
| `rules` | 规则定义 | `plugin_rule_key`, `plugin_name`, `status` | 规则注册 |
| `active_rules` | 已激活规则 | `rule_id`, `profile_id`, `severity` | 规则与 Profile 关联 |

### 4.5 架构演进趋势（2024-2025）

| 趋势 | 说明 | 影响 |
|------|------|------|
| ES 迁移到 OpenSearch | 社区版逐步从内嵌 ES 迁移到 OpenSearch | 升级时注意兼容性 |
| 多分支分析增强 | 支持更细粒度的分支级别分析 | New Code 定义更灵活 |
| Plugin API 稳定性提升 | 减少主版本间 Plugin API 的 break change | 自定义插件升级成本降低 |
| 安全热点集成 | 安全热点与 SAST 分析深度集成 | 安全团队需要关注新功能 |

### 4.6 思考题

1. 如果 CE 任务处理超时（30 分钟后被取消）——数据流中哪些步骤可能导致的？如何排查？
2. SonarQube 为什么选择 PostgreSQL + Elasticsearch 双存储模型，而不是只用一种数据库？
3. 在插件开发中，你的 Sensor 需要访问数据库中的项目设置——可以直接读数据库吗？如果不能，应该用什么方式？
4. 假设你需要在 SonarQube 启动时执行一段初始化代码（如创建默认的 Quality Gate）——应该用 Plugin API 的哪个扩展点？

> **答案提示**：第1题排查 CE 日志中的慢操作——通常是复杂的 MeasureComputer、大覆盖率报告解析、ES 批量索引写入。第2题 PostgreSQL 适合关系型数据（精确查询、事务），ES 适合全文搜索（Issue 列表、组件搜索）。第3题不能直接读数据库，应通过 `org.sonar.api.config.Configuration` 注入配置，或调用 Web API。第4题使用 `org.sonar.api.server.ServerSide` 加 `@Startable` 接口的 `start()` 方法。

---

> **推广计划提示**：本章适合架构师和平台运维人员。理解架构后，才能正确设计 SonarQube 的高可用、备份恢复、和性能调优方案。建议将架构图挂到团队 Wiki，作为新成员了解 SonarQube 的第一份文档。
