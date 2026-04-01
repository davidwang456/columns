

# 第16章 ILM 与数据分层：Hot-Warm-Cold 成本治理

# 背景

时序数据（日志、指标、链路追踪）天然会持续增长。手工管理索引的生命周期——什么时候该滚动、什么时候该迁移到便宜的磁盘、什么时候该删除——不仅耗费人力，还极易遗漏导致磁盘打满。ILM（Index Lifecycle Management）把"经验操作"变成"声明式策略"，让 Elasticsearch 自动执行滚动、缩分片、合并段、迁移节点、删除索引等动作，在性能与成本之间取得长期平衡。

## 本章目标

- 理解 ILM 策略的完整结构：五个 Phase 的语义与每个 Phase 可用的 Action。
- 掌握数据分层（Data Tiers）的节点角色配置与自动迁移机制。
- 通过 Rollover + Data Stream + ILM 实现日志场景的全自动生命周期管理。
- 学会通过 `_ilm/explain` 监控策略执行状态，排查常见卡住问题。
- 了解 SLM（Snapshot Lifecycle Management）作为 ILM 的补充。

---

## 1. ILM 解决什么问题

### 1.1 时序数据的增长困境

假设每天产生 50GB 日志，保留 90 天，2 副本：

```
总存储 = 50GB × 90 × (1 + 2) = 13.5TB
```

如果不做任何分层，所有数据都放在高性能 SSD 节点上，成本将远高于实际需求——因为 90 天前的日志几乎无人查询。

### 1.2 ILM 的核心思想

ILM 将索引的生命周期划分为多个**阶段（Phase）**，每个阶段对应不同的访问频率和硬件需求：

```
写入密集          查询为主          偶尔查询          极少查询          不再需要
   Hot     →     Warm     →     Cold     →     Frozen    →    Delete
  (SSD)        (HDD/大容量)    (低配/快照)     (全量快照)       (释放空间)
```

每个阶段可以配置自动执行的**动作（Action）**，如滚动、缩分片、合并段、迁移节点、删除索引等。

---

## 2. ILM 策略完整结构

### 2.1 策略的 JSON 结构

```json
PUT _ilm/policy/my_policy
{
  "policy": {
    "phases": {
      "hot": {
        "min_age": "0ms",
        "actions": { ... }
      },
      "warm": {
        "min_age": "7d",
        "actions": { ... }
      },
      "cold": {
        "min_age": "30d",
        "actions": { ... }
      },
      "frozen": {
        "min_age": "90d",
        "actions": { ... }
      },
      "delete": {
        "min_age": "180d",
        "actions": { ... }
      }
    }
  }
}
```

### 2.2 min_age 的语义

`min_age` 是**索引进入当前阶段的最小年龄**，计算基准为：

- 对于 **Rollover 后的索引**：从 rollover 时间开始计算（即索引不再接受写入的时间）。
- 对于 **未经 Rollover 的索引**：从索引的创建时间开始计算。
- 可通过 `index.lifecycle.origination_date` 覆盖创建时间（常见于从外部导入数据的场景）。

每个 phase 的 `min_age` 是**相对于索引创建/rollover 时间**的绝对值，不是相对于上一个 phase 的增量。例如 warm 设为 `7d`、cold 设为 `30d`，表示从 rollover 算起第 7 天进入 warm，第 30 天进入 cold。

### 2.3 Phase 执行顺序

Phase 严格按 `hot → warm → cold → frozen → delete` 顺序执行。不需要配置所有 Phase——只配置需要的即可，ILM 会自动跳过未配置的阶段。

---

## 3. 每个 Phase 的可用 Actions

### 3.1 Hot Phase

Hot Phase 是索引的初始阶段，承担写入和高频查询。

| Action | 关键参数 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `rollover` | `max_size` | 无（不限制） | 主分片总大小超过此值触发滚动 |
|  | `max_primary_shard_size` | 无 | 单个主分片超过此值触发（推荐使用） |
|  | `max_age` | 无 | 索引存在时间超过此值触发 |
|  | `max_docs` | 无 | 文档数超过此值触发 |
|  | `max_primary_shard_docs` | 无 | 单主分片文档数超过此值触发 |
| `set_priority` | `priority` | 无 | 设置索引恢复优先级（Hot 建议 100） |
| `unfollow` | — | — | 停止 CCR follower 索引的复制 |
| `readonly` | — | — | 将索引设为只读 |

**Rollover 触发逻辑**：多个条件之间是 **OR** 关系——任何一个条件满足即触发滚动。

推荐使用 `max_primary_shard_size` 而非 `max_size`，因为前者不受分片数量影响，更容易控制单分片大小在 10~50GB 的合理范围内。

### 3.2 Warm Phase

Warm Phase 面向查询密集但不再写入的数据，目标是压缩存储和提升查询性能。

| Action | 关键参数 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `allocate` | `number_of_replicas` | 无（保持原值） | 调整副本数 |
|  | `include/exclude/require` | 无 | 指定数据分配到特定节点属性 |
| `migrate` | `enabled` | `true` | 自动迁移到对应 data tier 节点 |
| `shrink` | `number_of_shards` | 无 | 缩减主分片数到指定值 |
|  | `max_primary_shard_size` | 无 | 缩减后单分片最大大小 |
| `forcemerge` | `max_num_segments` | 无 | 合并到指定 Segment 数（通常设为 1） |
| `readonly` | — | — | 设为只读（shrink/forcemerge 隐含执行） |
| `set_priority` | `priority` | 无 | 恢复优先级（Warm 建议 50） |

**shrink 的前置条件**：索引必须先变为只读，且所有分片的副本必须位于同一个节点上。ILM 会自动处理这些前置步骤。

**forcemerge 的注意事项**：将 Segment 合并为 1 可以最大化查询性能，但合并过程本身需要临时占用 1 倍原始数据的磁盘空间。

### 3.3 Cold Phase

Cold Phase 面向偶尔查询的历史数据，目标是极致的成本压缩。

| Action | 关键参数 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `allocate` | 同 Warm | 同 Warm | 分配到 cold 节点 |
| `migrate` | `enabled` | `true` | 自动迁移到 `data_cold` 节点 |
| `searchable_snapshot` | `snapshot_repository` | 必填 | 将索引转为可搜索快照 |
|  | `force_merge_index` | `true` | 快照前是否 forcemerge |
| `readonly` | — | — | 设为只读 |
| `set_priority` | `priority` | 无 | 恢复优先级（Cold 建议 0） |
| `downsample` | `fixed_interval` | 必填 | 降采样间隔（如 `1h`、`1d`） |

**Searchable Snapshot**：将索引数据存储在远端仓库（如 S3），查询时按需加载。Cold Phase 使用 `fully_mounted` 模式——数据完全缓存在本地节点。

### 3.4 Frozen Phase

Frozen Phase 面向极少查询但需要保留的数据。

| Action | 关键参数 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `searchable_snapshot` | `snapshot_repository` | 必填 | 使用 `partially_mounted` 模式 |

Frozen Phase 与 Cold Phase 的关键区别：使用 `partially_mounted` 模式——数据存储在远端仓库，仅在查询时按需从快照中加载所需的 Segment 片段，**本地几乎不占用磁盘空间**。查询延迟更高，但存储成本极低。

### 3.5 Delete Phase

Delete Phase 自动删除不再需要的索引。

| Action | 关键参数 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `delete` | `delete_searchable_snapshot` | `true` | 删除时是否同时删除对应的可搜索快照 |
| `wait_for_snapshot` | `policy` | 必填 | 等待指定 SLM 策略完成快照后再删除 |

`wait_for_snapshot` 常用于确保删除前数据已被 SLM 快照备份——避免"删了才发现没备份"的灾难。

---

## 4. 数据分层（Data Tiers）

### 4.1 节点角色配置

数据分层通过 `node.roles` 配置实现。每个节点声明自己属于哪个 tier：

```yaml
# Hot 节点 (SSD, 高 CPU)
node.roles: ["data_hot", "ingest"]

# Warm 节点 (HDD/大容量 SSD, 中等 CPU)
node.roles: ["data_warm"]

# Cold 节点 (HDD, 低配)
node.roles: ["data_cold"]

# Frozen 节点 (最小本地磁盘, 大量 RAM 做缓存)
node.roles: ["data_frozen"]
```

如果使用旧版配置 `node.roles: ["data"]`（通用数据节点），该节点接受所有 tier 的数据，不参与自动分层。

### 4.2 自动迁移（migrate action）

ILM 的每个 Phase 默认启用 `migrate` action：当索引进入 warm phase 时，ILM 自动将分片从 `data_hot` 节点迁移到 `data_warm` 节点。

迁移的底层实现是设置 `index.routing.allocation.include._tier_preference`：

```json
{
  "index.routing.allocation.include._tier_preference": "data_warm,data_hot"
}
```

**tier_preference 的 fallback 机制**：如果集群中没有 `data_warm` 节点，数据会保留在 `data_hot` 节点上（fallback），而不是阻塞 ILM 策略执行。

### 4.3 硬件建议

| Tier | 磁盘类型 | CPU | 内存 | 典型用途 |
| --- | --- | --- | --- | --- |
| Hot | NVMe/SSD | 高 | 高（≥64GB） | 写入 + 实时查询 |
| Warm | SSD/HDD | 中 | 中（32~64GB） | 查询为主，无写入 |
| Cold | HDD | 低 | 低（16~32GB） | 偶尔查询 |
| Frozen | 最小本地盘 | 低 | 中（缓存用） | 极少查询，数据在远端 |

---

## 5. Rollover 详解

### 5.1 Rollover 的核心概念

Rollover 将一个**写别名**（write alias）从当前索引切换到新索引。触发条件基于当前索引的大小、文档数或存在时间。

```
写入 → logs-write（别名）
         │
         ├── logs-000001 (已满, 只读)
         ├── logs-000002 (已满, 只读)
         └── logs-000003 (当前写入目标)
```

### 5.2 Rollover 与 Data Stream 的配合

**Data Stream** 是 Elasticsearch 为时序数据设计的高级抽象，内置了 rollover 机制：

- Data Stream 自动管理 backing indices（命名格式：`.ds-<name>-<generation>`）。
- 写入永远追加到最新的 backing index。
- ILM 的 rollover action 自动创建新的 backing index。
- 不需要手动管理写别名。

Data Stream 的创建流程：

1. 定义 Index Template（包含 ILM 策略和 `data_stream` 声明）。
2. 第一次写入文档时，Data Stream 自动创建。
3. ILM 根据策略自动 rollover。

### 5.3 Rollover 与 Index Template

```json
PUT _index_template/logs_template
{
  "index_patterns": ["logs-*"],
  "data_stream": {},
  "template": {
    "settings": {
      "number_of_shards": 1,
      "number_of_replicas": 1,
      "index.lifecycle.name": "logs_ilm_policy"
    },
    "mappings": {
      "properties": {
        "@timestamp": { "type": "date" },
        "message":    { "type": "text" },
        "level":      { "type": "keyword" },
        "service":    { "type": "keyword" }
      }
    }
  }
}
```

---

## 6. 实战：完整的日志场景 ILM 配置

### 6.1 策略设计

| Phase | 时间 | 动作 | 目标 |
| --- | --- | --- | --- |
| Hot | 0~7 天 | rollover（50GB 或 7 天），set_priority 100 | 高性能写入和查询 |
| Warm | 7~30 天 | shrink 到 1 分片，forcemerge 到 1 段，set_priority 50 | 压缩存储，优化查询 |
| Cold | 30~90 天 | 降副本到 0，set_priority 0 | 极致成本 |
| Delete | 180 天 | 删除索引 | 释放空间 |

### 6.2 创建 ILM 策略

```json
PUT _ilm/policy/logs_ilm_policy
{
  "policy": {
    "phases": {
      "hot": {
        "min_age": "0ms",
        "actions": {
          "rollover": {
            "max_primary_shard_size": "50gb",
            "max_age": "7d"
          },
          "set_priority": { "priority": 100 }
        }
      },
      "warm": {
        "min_age": "7d",
        "actions": {
          "shrink": { "number_of_shards": 1 },
          "forcemerge": { "max_num_segments": 1 },
          "set_priority": { "priority": 50 }
        }
      },
      "cold": {
        "min_age": "30d",
        "actions": {
          "allocate": { "number_of_replicas": 0 },
          "set_priority": { "priority": 0 }
        }
      },
      "delete": {
        "min_age": "180d",
        "actions": {
          "delete": {}
        }
      }
    }
  }
}
```

---

## 7. ILM 策略执行监控

### 7.1 _ilm/explain API

查看索引的 ILM 执行状态：

```bash
GET logs-myapp/_ilm/explain
```

响应中的关键字段：

| 字段 | 说明 |
| --- | --- |
| `managed` | 是否由 ILM 管理 |
| `policy` | 关联的策略名称 |
| `phase` | 当前所处阶段 |
| `action` | 当前正在执行的动作 |
| `step` | 当前步骤 |
| `phase_time_millis` | 进入当前阶段的时间 |
| `age` | 索引年龄 |
| `step_info` | 当步骤出错时的错误信息 |

### 7.2 策略执行状态

| 状态 | 含义 |
| --- | --- |
| `RUNNING` | ILM 正在正常运行 |
| `STOPPING` | ILM 正在停止 |
| `STOPPED` | ILM 已停止（需手动启动） |
| `ERROR` | 当前步骤执行出错 |

查看和控制 ILM 全局状态：

```bash
GET _ilm/status

POST _ilm/start
POST _ilm/stop
```

### 7.3 手动推进 Phase

当需要调试或快速推进时：

```bash
POST logs-myapp-000001/_ilm/move
{
  "current_step": {
    "phase": "hot",
    "action": "complete",
    "name": "complete"
  },
  "next_step": {
    "phase": "warm",
    "action": "shrink",
    "name": "shrink"
  }
}
```

### 7.4 ILM 轮询间隔

ILM 默认每 **10 分钟** 检查一次策略条件（`indices.lifecycle.poll_interval`，默认 `10m`）。这意味着即使索引已满足 rollover 条件，实际 rollover 可能最多延迟 10 分钟。

调试时可以缩短轮询间隔：

```bash
PUT _cluster/settings
{
  "persistent": {
    "indices.lifecycle.poll_interval": "1m"
  }
}
```

---

## 8. SLM（Snapshot Lifecycle Management）简介

SLM 是 ILM 的补充，用于自动化快照备份。

### 8.1 SLM 策略结构

```json
PUT _slm/policy/nightly_backup
{
  "schedule": "0 30 1 * * ?",
  "name": "<nightly-snap-{now/d}>",
  "repository": "my_s3_repo",
  "config": {
    "indices": ["logs-*"],
    "ignore_unavailable": true,
    "include_global_state": false
  },
  "retention": {
    "expire_after": "30d",
    "min_count": 5,
    "max_count": 50
  }
}
```

### 8.2 SLM 与 ILM 的协作

ILM delete phase 的 `wait_for_snapshot` action 可以引用 SLM 策略名称，确保在删除索引前已完成至少一次快照：

```json
"delete": {
  "min_age": "180d",
  "actions": {
    "wait_for_snapshot": {
      "policy": "nightly_backup"
    },
    "delete": {}
  }
}
```

---

## 9. 常见问题与排查

### 9.1 策略不执行

**症状**：索引明明超过了 `min_age`，但没有进入下一个 Phase。

**排查步骤**：

1. 检查 ILM 是否全局开启：`GET _ilm/status`，确认状态为 `RUNNING`。
2. 检查轮询间隔：`indices.lifecycle.poll_interval` 默认 10 分钟。
3. 检查 `_ilm/explain` 中的 `step_info` 是否有错误信息。
4. 检查 `min_age` 的计算基准——如果索引经过 rollover，`min_age` 从 rollover 时间算起，而非索引创建时间。

### 9.2 origination_date 问题

从外部导入的数据，索引创建时间是导入时间而非数据产生时间，导致 `min_age` 计算不符预期。

解决方案：在导入前设置 `index.lifecycle.origination_date`：

```json
PUT my_imported_index/_settings
{
  "index.lifecycle.origination_date": 1704067200000
}
```

### 9.3 Phase 卡在 ERROR 状态

**排查步骤**：

1. 查看 `_ilm/explain` 中的 `step_info.reason`。
2. 常见原因：
   - shrink 失败：目标分片数不是原分片数的因子。
   - forcemerge 超时：数据量过大。
   - migrate 失败：集群中没有对应 tier 的节点。
3. 修复问题后，使用 `_ilm/retry` 重试：

```bash
POST my_index/_ilm/retry
```

### 9.4 Rollover 不触发

**常见原因**：

- 索引没有关联 ILM 策略：检查 `index.lifecycle.name` 设置。
- 使用传统索引而非 Data Stream：确认写别名的 `is_write_index=true`。
- ILM 被全局停止：`GET _ilm/status`。
- 轮询间隔未到：等待或缩短 `poll_interval`。

---

# 总结

- ILM 通过 **声明式策略** 将索引生命周期管理从手工操作变为自动化执行——从 hot 到 delete 的五个阶段各司其职。
- **min_age** 是相对于 rollover/创建时间的绝对值，不是阶段间的增量——理解这一点是避免"策略不生效"的关键。
- **数据分层** 通过 `node.roles` 实现硬件隔离，ILM 的 `migrate` action 自动完成跨 tier 迁移。
- **Rollover + Data Stream + Index Template** 是时序数据管理的标配组合——消除手工创建索引和管理别名的负担。
- `_ilm/explain` 是 ILM 排障的第一工具——`step_info` 字段直接告诉你卡在哪一步、因为什么原因。
- ILM 的 delete phase 应配合 **SLM** 使用——先备份再删除，避免数据丢失。

---

## 练习题

1. 为日志数据设计一个五阶段 ILM 策略（hot/warm/cold/frozen/delete），包含 rollover、shrink、forcemerge、searchable_snapshot 和 delete action。
2. 创建一个 Data Stream，写入若干文档后手动触发 rollover，观察 backing index 的变化。
3. 修改 `indices.lifecycle.poll_interval` 为 `1m`，观察 ILM 策略的执行频率变化。
4. 模拟一个 ILM ERROR 状态（如 shrink 目标分片数设为不合法的值），使用 `_ilm/explain` 排查并修复。
5. 创建 SLM 策略并配合 ILM 的 `wait_for_snapshot` action，验证删除前快照的自动化流程。

---

## 实战（curl）

### 创建完整 ILM 策略

```bash
curl -u "$ES_USER:$ES_PASS" -X PUT "$ES_URL/_ilm/policy/logs_ilm_policy" \
  -H "Content-Type: application/json" \
  -d '{
    "policy": {
      "phases": {
        "hot": {
          "min_age": "0ms",
          "actions": {
            "rollover": {
              "max_primary_shard_size": "50gb",
              "max_age": "7d"
            },
            "set_priority": { "priority": 100 }
          }
        },
        "warm": {
          "min_age": "7d",
          "actions": {
            "shrink": { "number_of_shards": 1 },
            "forcemerge": { "max_num_segments": 1 },
            "set_priority": { "priority": 50 }
          }
        },
        "cold": {
          "min_age": "30d",
          "actions": {
            "allocate": { "number_of_replicas": 0 },
            "set_priority": { "priority": 0 }
          }
        },
        "delete": {
          "min_age": "180d",
          "actions": {
            "delete": {}
          }
        }
      }
    }
  }'
```

### 查看 ILM 策略

```bash
curl -u "$ES_USER:$ES_PASS" "$ES_URL/_ilm/policy/logs_ilm_policy?pretty"
```

### 创建关联 ILM 的 Index Template + Data Stream

```bash
curl -u "$ES_USER:$ES_PASS" -X PUT "$ES_URL/_index_template/logs_template" \
  -H "Content-Type: application/json" \
  -d '{
    "index_patterns": ["logs-myapp-*"],
    "data_stream": {},
    "template": {
      "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 1,
        "index.lifecycle.name": "logs_ilm_policy"
      },
      "mappings": {
        "properties": {
          "@timestamp": { "type": "date" },
          "message":    { "type": "text" },
          "level":      { "type": "keyword" },
          "service":    { "type": "keyword" },
          "host":       { "type": "keyword" }
        }
      }
    }
  }'
```

### 写入数据到 Data Stream

```bash
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/logs-myapp-prod/_bulk" \
  -H "Content-Type: application/x-ndjson" \
  -d '{"create":{}}
{"@timestamp":"2025-06-01T10:00:00Z","message":"Application started","level":"INFO","service":"order-svc","host":"node-1"}
{"create":{}}
{"@timestamp":"2025-06-01T10:01:00Z","message":"Processing order 12345","level":"DEBUG","service":"order-svc","host":"node-1"}
{"create":{}}
{"@timestamp":"2025-06-01T10:02:00Z","message":"Database connection timeout","level":"ERROR","service":"order-svc","host":"node-2"}
{"create":{}}
{"@timestamp":"2025-06-01T10:03:00Z","message":"Retry succeeded after 3 attempts","level":"WARN","service":"order-svc","host":"node-2"}
'
```

### 查看 Data Stream 状态

```bash
curl -u "$ES_USER:$ES_PASS" "$ES_URL/_data_stream/logs-myapp-prod?pretty"
```

### 查看 ILM 执行状态

```bash
# 查看索引的 ILM 状态
curl -u "$ES_USER:$ES_PASS" "$ES_URL/logs-myapp-prod/_ilm/explain?pretty"

# 查看 ILM 全局状态
curl -u "$ES_USER:$ES_PASS" "$ES_URL/_ilm/status?pretty"
```

### 手动触发 Rollover

```bash
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/logs-myapp-prod/_rollover?pretty"

# 再次查看 Data Stream — 应该有两个 backing index
curl -u "$ES_USER:$ES_PASS" "$ES_URL/_data_stream/logs-myapp-prod?pretty"
```

### 手动推进 Phase（调试用）

```bash
# 缩短 ILM 轮询间隔
curl -u "$ES_USER:$ES_PASS" -X PUT "$ES_URL/_cluster/settings" \
  -H "Content-Type: application/json" \
  -d '{
    "persistent": {
      "indices.lifecycle.poll_interval": "1m"
    }
  }'

# 手动推进索引到 warm phase
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/.ds-logs-myapp-prod-000001/_ilm/move?pretty" \
  -H "Content-Type: application/json" \
  -d '{
    "current_step": {
      "phase": "hot",
      "action": "complete",
      "name": "complete"
    },
    "next_step": {
      "phase": "warm"
    }
  }'

# 查看推进后的状态
curl -u "$ES_USER:$ES_PASS" "$ES_URL/.ds-logs-myapp-prod-000001/_ilm/explain?pretty"
```

### ILM 出错后重试

```bash
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/.ds-logs-myapp-prod-000001/_ilm/retry?pretty"
```

### 使用传统写别名模式（非 Data Stream）

```bash
# 创建初始索引（带写别名）
curl -u "$ES_USER:$ES_PASS" -X PUT "$ES_URL/logs-legacy-000001" \
  -H "Content-Type: application/json" \
  -d '{
    "settings": {
      "number_of_shards": 1,
      "number_of_replicas": 0,
      "index.lifecycle.name": "logs_ilm_policy",
      "index.lifecycle.rollover_alias": "logs-legacy"
    },
    "aliases": {
      "logs-legacy": { "is_write_index": true }
    }
  }'

# 通过别名写入
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/logs-legacy/_doc" \
  -H "Content-Type: application/json" \
  -d '{"@timestamp":"2025-06-01T12:00:00Z","message":"legacy log entry"}'

# 手动 rollover
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/logs-legacy/_rollover?pretty"

# 查看别名指向
curl -u "$ES_USER:$ES_PASS" "$ES_URL/_cat/aliases/logs-legacy?v"
```

### 清理

```bash
curl -u "$ES_USER:$ES_PASS" -X DELETE "$ES_URL/_data_stream/logs-myapp-prod"
curl -u "$ES_USER:$ES_PASS" -X DELETE "$ES_URL/logs-legacy-*"
curl -u "$ES_USER:$ES_PASS" -X DELETE "$ES_URL/_index_template/logs_template"
curl -u "$ES_USER:$ES_PASS" -X DELETE "$ES_URL/_ilm/policy/logs_ilm_policy"

# 恢复 ILM 轮询间隔
curl -u "$ES_USER:$ES_PASS" -X PUT "$ES_URL/_cluster/settings" \
  -H "Content-Type: application/json" \
  -d '{
    "persistent": {
      "indices.lifecycle.poll_interval": null
    }
  }'
```

---

## 实战（Java SDK）

```java
// ---------- 创建 ILM 策略 ----------
client.ilm().putLifecycle(p -> p
    .name("logs_ilm_policy")
    .policy(pol -> pol
        .phases(ph -> ph
            .hot(h -> h
                .minAge(t -> t.time("0ms"))
                .actions(a -> a
                    .rollover(r -> r
                        .maxPrimaryShardSize(sz -> sz.size("50gb"))
                        .maxAge(t -> t.time("7d")))
                    .setPriority(sp -> sp.priority(100))))
            .warm(w -> w
                .minAge(t -> t.time("7d"))
                .actions(a -> a
                    .shrink(s -> s.numberOfShards(1))
                    .forcemerge(f -> f.maxNumSegments(1))
                    .setPriority(sp -> sp.priority(50))))
            .cold(c -> c
                .minAge(t -> t.time("30d"))
                .actions(a -> a
                    .allocate(al -> al.numberOfReplicas(0))
                    .setPriority(sp -> sp.priority(0))))
            .delete(d -> d
                .minAge(t -> t.time("180d"))
                .actions(a -> a
                    .delete(del -> del))))));

// ---------- 查看 ILM 策略 ----------
var getResp = client.ilm().getLifecycle(g -> g.name("logs_ilm_policy"));
System.out.println("Policy phases: " + getResp.get("logs_ilm_policy").policy().phases());

// ---------- 创建 Index Template + Data Stream ----------
client.indices().putIndexTemplate(t -> t
    .name("logs_template")
    .indexPatterns("logs-myapp-*")
    .dataStream(ds -> ds)
    .template(tmpl -> tmpl
        .settings(s -> s
            .numberOfShards("1")
            .numberOfReplicas("1")
            .lifecycle(l -> l.name("logs_ilm_policy")))
        .mappings(m -> m
            .properties("@timestamp", p -> p.date(d -> d))
            .properties("message", p -> p.text(tx -> tx))
            .properties("level", p -> p.keyword(k -> k))
            .properties("service", p -> p.keyword(k -> k))
            .properties("host", p -> p.keyword(k -> k)))));

// ---------- 写入数据到 Data Stream ----------
client.bulk(b -> b
    .operations(op -> op.create(c -> c
        .index("logs-myapp-prod")
        .document(Map.of(
            "@timestamp", "2025-06-01T10:00:00Z",
            "message", "Application started",
            "level", "INFO",
            "service", "order-svc",
            "host", "node-1"))))
    .operations(op -> op.create(c -> c
        .index("logs-myapp-prod")
        .document(Map.of(
            "@timestamp", "2025-06-01T10:01:00Z",
            "message", "Database connection timeout",
            "level", "ERROR",
            "service", "order-svc",
            "host", "node-2")))));

// ---------- 查看 Data Stream ----------
var dsResp = client.indices().getDataStream(g -> g.name("logs-myapp-prod"));
dsResp.dataStreams().forEach(ds ->
    System.out.println("Data stream: " + ds.name()
        + ", backing indices: " + ds.indices().size()));

// ---------- 查看 ILM 执行状态 ----------
var explainResp = client.ilm().explainLifecycle(e -> e.index("logs-myapp-prod"));
explainResp.indices().forEach((idx, info) ->
    System.out.println("Index: " + idx
        + ", phase: " + info.phase()
        + ", action: " + info.action()
        + ", step: " + info.step()));

// ---------- 手动 Rollover ----------
var rolloverResp = client.indices().rollover(r -> r.alias("logs-myapp-prod"));
System.out.println("Rollover acknowledged: " + rolloverResp.acknowledged()
    + ", new index: " + rolloverResp.newIndex());

// ---------- 查看 ILM 全局状态 ----------
var statusResp = client.ilm().getStatus();
System.out.println("ILM status: " + statusResp.operationMode());

// ---------- 清理 ----------
client.indices().deleteDataStream(d -> d.name("logs-myapp-prod"));
client.indices().deleteIndexTemplate(d -> d.name("logs_template"));
client.ilm().deleteLifecycle(d -> d.name("logs_ilm_policy"));
```
