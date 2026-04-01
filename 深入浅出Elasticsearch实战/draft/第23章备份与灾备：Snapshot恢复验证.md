

# 第23章 备份与灾备：Snapshot 恢复验证

# 背景

"数据丢失"是所有工程团队最不愿面对但必须准备的场景。副本（Replica）只解决节点故障的高可用，无法防御误删索引、软件 Bug 导致的数据损坏、甚至整个集群不可恢复的灾难。Snapshot 是 ES 提供的完整备份与恢复方案——它将索引数据以增量方式存储到外部仓库（文件系统、S3、GCS 等），支持跨集群恢复、定时自动备份和保留策略。但"有备份"不等于"能恢复"，本章从 Snapshot 核心概念出发，讲透仓库配置、快照创建、恢复操作、SLM 自动化，以及**恢复演练标准流程**。

## 本章目标

- 理解 Snapshot 的增量机制和 Repository 类型选择。
- 掌握快照的创建、恢复和监控全流程。
- 配置 SLM（Snapshot Lifecycle Management）实现定时自动备份 + 保留策略。
- 建立恢复演练标准流程，验证 RPO/RTO 是否满足业务要求。

---

## 1. Snapshot 核心概念

### 1.1 三个关键概念

| 概念 | 说明 |
| --- | --- |
| Repository（仓库） | 快照的存储目标位置。可以是本地文件系统、S3、GCS、Azure Blob 等 |
| Snapshot（快照） | 某个时间点的索引数据完整副本。包含 index metadata、mapping、settings 和 segment 文件 |
| 增量机制 | 每次快照只存储自上次快照以来变化的 segment 文件，大幅降低存储和时间开销 |

### 1.2 增量存储原理

Snapshot 的增量机制基于 Lucene Segment 的不可变性：

```
第一次快照（Full）:
  Segment-A ──→ Repository（上传）
  Segment-B ──→ Repository（上传）
  Segment-C ──→ Repository（上传）

第二次快照（Incremental）:
  Segment-A ──→ Repository（已存在，跳过）
  Segment-B ──→ Repository（已存在，跳过）
  Segment-C ──→ Repository（Merge 后被删除，不再引用）
  Segment-D ──→ Repository（新 Segment，上传）
  Segment-E ──→ Repository（新 Segment，上传）
```

因此，频繁快照的边际成本很低——只有新增或变化的 Segment 才需要传输和存储。

### 1.3 Snapshot 包含什么

一个 Snapshot 默认包含：

- **索引数据**：所有分片的 Segment 文件。
- **索引 metadata**：Mapping、Settings、Aliases。
- **全局集群状态**（可选）：Index Templates、ILM Policies、Ingest Pipelines、Persistent Cluster Settings。

---

## 2. Repository 类型与配置

### 2.1 Repository 类型速查

| 类型 | 说明 | 适用场景 |
| --- | --- | --- |
| `fs` | 本地/NFS 文件系统 | 开发测试、单机备份 |
| `s3` | AWS S3（需安装 `repository-s3` 插件） | AWS 云环境，最常用 |
| `gcs` | Google Cloud Storage（需安装 `repository-gcs` 插件） | GCP 云环境 |
| `azure` | Azure Blob Storage（需安装 `repository-azure` 插件） | Azure 云环境 |
| `hdfs` | Hadoop HDFS（需安装 `repository-hdfs` 插件） | 大数据平台共存场景 |
| `url` | 只读 URL 仓库 | 从远程 HTTP 恢复快照 |

### 2.2 fs 类型配置

适合开发测试和小型部署。需要在 `elasticsearch.yml` 中配置允许路径：

```yaml
path.repo: ["/mnt/es_backups"]
```

注册仓库：

```json
PUT /_snapshot/my_fs_repo
{
  "type": "fs",
  "settings": {
    "location": "/mnt/es_backups/snapshots",
    "compress": true,
    "max_snapshot_bytes_per_sec": "40mb",
    "max_restore_bytes_per_sec": "40mb"
  }
}
```

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `location` | 必填 | 快照存储路径（必须在 `path.repo` 下） |
| `compress` | `true` | 是否压缩 metadata 文件 |
| `max_snapshot_bytes_per_sec` | `40mb` | 快照写入限速 |
| `max_restore_bytes_per_sec` | `40mb` | 恢复读取限速 |
| `chunk_size` | 无限制 | 大文件分块大小 |

### 2.3 S3 类型配置

安装插件：

```bash
bin/elasticsearch-plugin install repository-s3
```

配置凭证（推荐使用 ES Keystore）：

```bash
bin/elasticsearch-keystore add s3.client.default.access_key
bin/elasticsearch-keystore add s3.client.default.secret_key
```

注册仓库：

```json
PUT /_snapshot/my_s3_repo
{
  "type": "s3",
  "settings": {
    "bucket": "my-es-backups",
    "region": "us-east-1",
    "base_path": "elasticsearch/snapshots",
    "compress": true,
    "server_side_encryption": true,
    "storage_class": "standard_ia"
  }
}
```

| 参数 | 说明 |
| --- | --- |
| `bucket` | S3 桶名称 |
| `region` | S3 区域 |
| `base_path` | 桶内的存储路径前缀 |
| `server_side_encryption` | 是否开启服务端加密 |
| `storage_class` | 存储级别（`standard`、`standard_ia`、`intelligent_tiering`） |

### 2.4 验证仓库

创建仓库后，ES 会自动验证连通性。也可以手动验证：

```bash
POST /_snapshot/my_fs_repo/_verify
```

---

## 3. 创建快照

### 3.1 创建快照的完整参数

```json
PUT /_snapshot/my_fs_repo/snapshot_2025_06_15
{
  "indices": "orders-*,products-*",
  "ignore_unavailable": true,
  "include_global_state": false,
  "partial": false,
  "metadata": {
    "taken_by": "ops_team",
    "reason": "weekly backup"
  }
}
```

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `indices` | 所有索引 | 指定要快照的索引（支持通配符、逗号分隔） |
| `ignore_unavailable` | `false` | 指定的索引不存在时是否跳过（而非报错） |
| `include_global_state` | `true` | 是否包含集群全局状态（templates、ILM policies 等） |
| `partial` | `false` | 是否允许部分分片不可用时继续创建快照 |
| `metadata` | `{}` | 自定义元数据（如备份原因、操作人） |

### 3.2 同步 vs 异步

默认情况下，`PUT /_snapshot/<repo>/<snap>` 是异步的——返回 `202 Accepted` 后快照在后台进行。

添加 `wait_for_completion=true` 可以同步等待：

```bash
PUT /_snapshot/my_fs_repo/snapshot_2025_06_15?wait_for_completion=true
```

对于大索引，推荐使用异步模式 + 状态监控。

### 3.3 快照状态含义

| 状态 | 说明 |
| --- | --- |
| `IN_PROGRESS` | 正在创建 |
| `SUCCESS` | 创建完成 |
| `FAILED` | 创建失败 |
| `PARTIAL` | 部分成功（某些分片未备份） |
| `INCOMPATIBLE` | 快照与当前 ES 版本不兼容 |

---

## 4. 恢复快照

### 4.1 基本恢复

```json
POST /_snapshot/my_fs_repo/snapshot_2025_06_15/_restore
{
  "indices": "orders-*",
  "ignore_unavailable": true,
  "include_global_state": false
}
```

**重要限制**：恢复时目标索引必须不存在或处于关闭状态。如果同名索引已存在且处于打开状态，恢复会失败。

### 4.2 恢复到不同索引名

通过 `rename_pattern` 和 `rename_replacement` 将快照中的索引恢复为不同的名称：

```json
POST /_snapshot/my_fs_repo/snapshot_2025_06_15/_restore
{
  "indices": "orders-2025",
  "ignore_unavailable": true,
  "include_global_state": false,
  "rename_pattern": "orders-(.+)",
  "rename_replacement": "restored_orders-$1"
}
```

`orders-2025` 会被恢复为 `restored_orders-2025`——这在不影响线上数据的前提下验证备份有效性时非常有用。

### 4.3 恢复时修改 Settings

恢复时可以覆盖索引的 settings，例如减少副本数加快恢复速度：

```json
POST /_snapshot/my_fs_repo/snapshot_2025_06_15/_restore
{
  "indices": "orders-*",
  "ignore_unavailable": true,
  "include_global_state": false,
  "index_settings": {
    "index.number_of_replicas": 0
  },
  "ignore_index_settings": [
    "index.refresh_interval"
  ]
}
```

| 参数 | 说明 |
| --- | --- |
| `index_settings` | 覆盖恢复后的索引 settings |
| `ignore_index_settings` | 忽略快照中的某些 settings（使用集群默认值） |

### 4.4 恢复特定索引

```json
POST /_snapshot/my_fs_repo/snapshot_2025_06_15/_restore
{
  "indices": "orders-2025,products-2025",
  "ignore_unavailable": true,
  "include_global_state": false
}
```

---

## 5. SLM（Snapshot Lifecycle Management）

手动创建快照容易遗忘。SLM 提供定时自动创建 + 保留策略的完整生命周期管理。

### 5.1 创建 SLM 策略

```json
PUT /_slm/policy/nightly-snapshots
{
  "schedule": "0 30 2 * * ?",
  "name": "<nightly-snap-{now/d}>",
  "repository": "my_fs_repo",
  "config": {
    "indices": ["orders-*", "products-*"],
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

| 参数 | 说明 |
| --- | --- |
| `schedule` | Cron 表达式（示例：每天凌晨 2:30） |
| `name` | 快照名称模板（支持日期变量） |
| `repository` | 使用的仓库 |
| `config` | 快照配置（同手动创建快照的参数） |
| `retention.expire_after` | 快照过期时间 |
| `retention.min_count` | 最少保留的快照数（即使过期也不删） |
| `retention.max_count` | 最多保留的快照数 |

### 5.2 SLM 操作

```bash
# 查看所有 SLM 策略
GET /_slm/policy

# 查看特定策略的状态
GET /_slm/policy/nightly-snapshots

# 手动执行一次策略（不等待下次调度）
POST /_slm/policy/nightly-snapshots/_execute

# 手动触发保留策略清理
POST /_slm/_execute_retention

# 查看 SLM 统计
GET /_slm/stats
```

### 5.3 SLM 策略状态

策略的 `last_success` 和 `last_failure` 字段记录最近一次执行结果：

```json
{
  "nightly-snapshots": {
    "version": 1,
    "modified_date_millis": 1718352000000,
    "policy": { ... },
    "last_success": {
      "snapshot_name": "nightly-snap-2025.06.14",
      "time": 1718352600000
    },
    "last_failure": null,
    "next_execution_millis": 1718439000000,
    "stats": {
      "snapshots_taken": 30,
      "snapshots_failed": 0,
      "snapshots_deleted": 10
    }
  }
}
```

---

## 6. 恢复演练标准流程

**备份的价值取决于恢复的能力。** 定期恢复演练是确保灾备方案有效的唯一手段。

### 6.1 演练步骤

```
步骤 1：选择快照
│  确认最新快照状态为 SUCCESS
│
步骤 2：恢复到测试环境
│  使用 rename_pattern 避免与生产数据冲突
│  或恢复到独立的测试集群
│
步骤 3：数据量验证
│  对比快照中索引的文档数与恢复后的文档数
│
步骤 4：抽样验证
│  随机抽取 N 条文档，对比字段值
│
步骤 5：聚合验证
│  对关键聚合指标（如订单总额、日志条数）进行对比
│
步骤 6：记录恢复耗时
│  从启动恢复到数据完全可搜索的时间 → 对比 RTO 要求
│
步骤 7：记录恢复点
│  快照的创建时间 → 对比 RPO 要求
│  RPO = 当前时间 - 快照创建时间
│
步骤 8：输出演练报告
│  记录验证结果、恢复耗时、发现的问题
```

### 6.2 RPO 与 RTO

| 指标 | 全称 | 含义 | 由什么决定 |
| --- | --- | --- | --- |
| RPO | Recovery Point Objective | 最多能容忍丢失多长时间的数据 | 快照频率（每小时→RPO≤1h） |
| RTO | Recovery Time Objective | 从故障到恢复服务的最长时间 | 恢复速度（数据量、带宽、硬件） |

### 6.3 演练报告模板

| 检查项 | 预期值 | 实际值 | 通过 |
| --- | --- | --- | --- |
| 快照状态 | SUCCESS | SUCCESS | ✓ |
| orders-2025 文档数 | 1,000,000 | 1,000,000 | ✓ |
| orders 总金额 | ¥52,345,678 | ¥52,345,678 | ✓ |
| 随机抽样 10 条 | 一致 | 一致 | ✓ |
| 恢复耗时 | ≤30min(RTO) | 18min | ✓ |
| 恢复点 | ≤1h(RPO) | 45min前 | ✓ |

---

## 7. 监控快照状态

### 7.1 查看快照详情

```bash
# 查看仓库中所有快照
GET /_snapshot/my_fs_repo/_all

# 查看特定快照详情
GET /_snapshot/my_fs_repo/snapshot_2025_06_15

# 只看最新的快照
GET /_snapshot/my_fs_repo/_all?sort=start_time&order=desc&size=1
```

### 7.2 查看正在进行的快照

```bash
# 查看所有仓库中正在进行的快照
GET /_snapshot/_status

# 查看特定快照的进度
GET /_snapshot/my_fs_repo/snapshot_2025_06_15/_status
```

`_status` 返回的关键信息：

```json
{
  "snapshots": [{
    "snapshot": "snapshot_2025_06_15",
    "state": "IN_PROGRESS",
    "stats": {
      "incremental": {
        "file_count": 150,
        "size_in_bytes": 5368709120
      },
      "processed": {
        "file_count": 80,
        "size_in_bytes": 2684354560
      },
      "total": {
        "file_count": 500,
        "size_in_bytes": 21474836480
      },
      "start_time_in_millis": 1718352000000,
      "time_in_millis": 120000
    }
  }]
}
```

进度计算：`processed.file_count / incremental.file_count`（增量文件的处理进度）。

### 7.3 查看恢复进度

```bash
# 查看正在恢复的索引进度
GET /_recovery?active_only=true&detailed=true

# 查看特定索引的恢复进度
GET /orders-2025/_recovery?detailed=true
```

### 7.4 删除快照

```bash
# 删除指定快照
DELETE /_snapshot/my_fs_repo/snapshot_2025_06_15
```

删除快照时，ES 只会删除不被其他快照引用的 Segment 文件（因为增量机制，多个快照可能共享同一个 Segment）。

---

## 8. 快照的注意事项与最佳实践

### 8.1 注意事项

| 要点 | 说明 |
| --- | --- |
| 同一时间同一仓库只能有一个快照在创建 | 并发创建会排队等待 |
| 恢复时目标索引必须不存在或已关闭 | 无法覆盖打开的索引 |
| 快照不包含 translog | 恢复后索引立即可用，但可能丢失快照点之后的写入 |
| 跨主版本恢复有限制 | 8.x 的快照可以恢复到 8.x，但不能恢复到 9.x（除非是最后一个 8.x 小版本） |
| 快照期间可以正常读写 | 不需要停止写入 |

### 8.2 最佳实践

1. **至少两个仓库**——一个本地（快速恢复），一个远程（异地容灾）。
2. **使用 SLM 自动化**——不依赖人工记忆。
3. **定期恢复演练**——建议每季度至少一次，记录 RTO/RPO。
4. **保留策略要合理**——min_count 保底 + expire_after 控制存储成本。
5. **监控快照成功率**——SLM stats 中的 `snapshots_failed` 应始终为 0。
6. **恢复演练使用 rename_pattern**——避免覆盖生产数据。

---

# 总结

- Snapshot 是 ES 唯一的完整备份方案——Replica 不是备份，它只防节点故障，不防误删。
- 增量机制基于 Segment 不可变性——频繁快照的边际成本很低，大胆提高快照频率。
- Repository 选型：开发用 `fs`，云上用 `s3`/`gcs`/`azure`——凭证通过 ES Keystore 管理，不要硬编码。
- SLM 是生产标配——定时快照 + 保留策略 = 自动化的备份生命周期管理。
- **恢复演练比备份更重要**——没有验证过的备份等于没有备份。每次演练记录 RTO/RPO，确保满足业务要求。
- 快照期间不需要停写——ES 通过 Segment 不可变性保证快照的一致性。

---

## 练习题

1. 配置一个 `fs` 类型的 Repository，手动创建一次快照（包含 2-3 个索引），验证快照状态为 `SUCCESS`。
2. 恢复快照到不同的索引名（使用 `rename_pattern`），对比恢复前后的文档数和聚合结果。
3. 配置 SLM 策略：每天凌晨 3 点自动快照，保留最近 7 天，最少保留 3 个，最多保留 30 个。手动执行一次验证。
4. 模拟恢复演练：删除一个索引后从快照恢复，记录恢复耗时，计算 RPO。
5. 连续创建两次快照，对比第二次快照的文件数（观察增量机制的效果）。

---

## 实战（curl）

### 注册 Repository

```bash
# 注册 fs 类型仓库（需提前在 elasticsearch.yml 中配置 path.repo）
curl -u "$ES_USER:$ES_PASS" -X PUT "$ES_URL/_snapshot/my_backup_repo" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "fs",
    "settings": {
      "location": "/mnt/es_backups/snapshots",
      "compress": true
    }
  }'

# 验证仓库连通性
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/_snapshot/my_backup_repo/_verify?pretty"

# 查看所有仓库
curl -u "$ES_USER:$ES_PASS" "$ES_URL/_snapshot?pretty"
```

### 准备测试数据

```bash
curl -u "$ES_USER:$ES_PASS" -X PUT "$ES_URL/snap_orders" \
  -H "Content-Type: application/json" \
  -d '{
    "settings": { "number_of_shards": 1, "number_of_replicas": 0 },
    "mappings": { "properties": {
      "product": { "type": "keyword" },
      "amount": { "type": "double" },
      "created_at": { "type": "date" }
    }}
  }'

curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/snap_orders/_bulk?refresh=wait_for" \
  -H "Content-Type: application/x-ndjson" \
  -d '{"index":{"_id":"1"}}
{"product":"laptop","amount":6999,"created_at":"2025-06-01"}
{"index":{"_id":"2"}}
{"product":"keyboard","amount":599,"created_at":"2025-06-02"}
{"index":{"_id":"3"}}
{"product":"mouse","amount":199,"created_at":"2025-06-03"}
{"index":{"_id":"4"}}
{"product":"monitor","amount":2499,"created_at":"2025-06-04"}
{"index":{"_id":"5"}}
{"product":"headset","amount":899,"created_at":"2025-06-05"}
'
```

### 创建快照

```bash
# 创建快照（同步等待完成）
curl -u "$ES_USER:$ES_PASS" -X PUT \
  "$ES_URL/_snapshot/my_backup_repo/snap_20250615?wait_for_completion=true&pretty" \
  -H "Content-Type: application/json" \
  -d '{
    "indices": "snap_orders",
    "ignore_unavailable": true,
    "include_global_state": false,
    "metadata": {
      "taken_by": "ops_team",
      "reason": "chapter 23 demo"
    }
  }'

# 查看快照详情
curl -u "$ES_USER:$ES_PASS" "$ES_URL/_snapshot/my_backup_repo/snap_20250615?pretty"
```

### 恢复快照

```bash
# 恢复到不同索引名（不影响原始数据）
curl -u "$ES_USER:$ES_PASS" -X POST \
  "$ES_URL/_snapshot/my_backup_repo/snap_20250615/_restore?pretty" \
  -H "Content-Type: application/json" \
  -d '{
    "indices": "snap_orders",
    "ignore_unavailable": true,
    "include_global_state": false,
    "rename_pattern": "snap_(.+)",
    "rename_replacement": "restored_$1",
    "index_settings": {
      "index.number_of_replicas": 0
    }
  }'

# 等待恢复完成后验证
sleep 3

# 数量验证
echo "=== Original count ==="
curl -u "$ES_USER:$ES_PASS" "$ES_URL/snap_orders/_count?pretty"
echo "=== Restored count ==="
curl -u "$ES_USER:$ES_PASS" "$ES_URL/restored_orders/_count?pretty"

# 聚合验证
echo "=== Original sum ==="
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/snap_orders/_search?pretty&filter_path=aggregations" \
  -H "Content-Type: application/json" \
  -d '{ "size": 0, "aggs": { "total_amount": { "sum": { "field": "amount" } } } }'

echo "=== Restored sum ==="
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/restored_orders/_search?pretty&filter_path=aggregations" \
  -H "Content-Type: application/json" \
  -d '{ "size": 0, "aggs": { "total_amount": { "sum": { "field": "amount" } } } }'

# 抽样对比
curl -u "$ES_USER:$ES_PASS" "$ES_URL/snap_orders/_doc/1?pretty&filter_path=_source"
curl -u "$ES_USER:$ES_PASS" "$ES_URL/restored_orders/_doc/1?pretty&filter_path=_source"
```

### 模拟灾难恢复

```bash
# 模拟误删索引
curl -u "$ES_USER:$ES_PASS" -X DELETE "$ES_URL/snap_orders"

# 确认索引已删除
curl -u "$ES_USER:$ES_PASS" "$ES_URL/snap_orders/_count?pretty"

# 从快照恢复（直接恢复原名）
START_TIME=$(date +%s)

curl -u "$ES_USER:$ES_PASS" -X POST \
  "$ES_URL/_snapshot/my_backup_repo/snap_20250615/_restore?wait_for_completion=true&pretty" \
  -H "Content-Type: application/json" \
  -d '{
    "indices": "snap_orders",
    "ignore_unavailable": true,
    "include_global_state": false
  }'

END_TIME=$(date +%s)
echo "Recovery time: $((END_TIME - START_TIME)) seconds"

# 验证恢复结果
curl -u "$ES_USER:$ES_PASS" "$ES_URL/snap_orders/_count?pretty"
```

### 增量快照对比

```bash
# 写入更多数据
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/snap_orders/_bulk?refresh=wait_for" \
  -H "Content-Type: application/x-ndjson" \
  -d '{"index":{"_id":"6"}}
{"product":"tablet","amount":3999,"created_at":"2025-06-06"}
{"index":{"_id":"7"}}
{"product":"charger","amount":99,"created_at":"2025-06-07"}
'

# 创建第二次快照（增量）
curl -u "$ES_USER:$ES_PASS" -X PUT \
  "$ES_URL/_snapshot/my_backup_repo/snap_20250616?wait_for_completion=true&pretty" \
  -H "Content-Type: application/json" \
  -d '{
    "indices": "snap_orders",
    "ignore_unavailable": true,
    "include_global_state": false
  }'

# 对比两次快照的 shard 统计（注意 incremental 字段）
echo "=== First snapshot stats ==="
curl -u "$ES_USER:$ES_PASS" "$ES_URL/_snapshot/my_backup_repo/snap_20250615?pretty" \
  | python -c "import sys,json; snap=json.load(sys.stdin)['snapshots'][0]; print('total files:', snap.get('stats',{}).get('total',{}).get('file_count','N/A'))"

echo "=== Second snapshot stats ==="
curl -u "$ES_USER:$ES_PASS" "$ES_URL/_snapshot/my_backup_repo/snap_20250616?pretty" \
  | python -c "import sys,json; snap=json.load(sys.stdin)['snapshots'][0]; print('total files:', snap.get('stats',{}).get('total',{}).get('file_count','N/A'))"
```

### 配置 SLM

```bash
# 创建 SLM 策略：每天凌晨 2:30，保留 30 天
curl -u "$ES_USER:$ES_PASS" -X PUT "$ES_URL/_slm/policy/nightly-backup?pretty" \
  -H "Content-Type: application/json" \
  -d '{
    "schedule": "0 30 2 * * ?",
    "name": "<nightly-{now/d}>",
    "repository": "my_backup_repo",
    "config": {
      "indices": ["snap_orders", "products-*"],
      "ignore_unavailable": true,
      "include_global_state": false
    },
    "retention": {
      "expire_after": "30d",
      "min_count": 5,
      "max_count": 50
    }
  }'

# 查看策略
curl -u "$ES_USER:$ES_PASS" "$ES_URL/_slm/policy/nightly-backup?pretty"

# 手动执行一次
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/_slm/policy/nightly-backup/_execute?pretty"

# 查看 SLM 统计
curl -u "$ES_USER:$ES_PASS" "$ES_URL/_slm/stats?pretty"

# 删除 SLM 策略
curl -u "$ES_USER:$ES_PASS" -X DELETE "$ES_URL/_slm/policy/nightly-backup"
```

### 监控恢复进度

```bash
# 查看当前活跃的恢复任务
curl -u "$ES_USER:$ES_PASS" "$ES_URL/_recovery?active_only=true&pretty"

# 查看特定索引的恢复进度
curl -u "$ES_USER:$ES_PASS" "$ES_URL/snap_orders/_recovery?pretty"

# 查看正在进行的快照状态
curl -u "$ES_USER:$ES_PASS" "$ES_URL/_snapshot/_status?pretty"
```

### 清理

```bash
curl -u "$ES_USER:$ES_PASS" -X DELETE "$ES_URL/_snapshot/my_backup_repo/snap_20250615"
curl -u "$ES_USER:$ES_PASS" -X DELETE "$ES_URL/_snapshot/my_backup_repo/snap_20250616"
curl -u "$ES_USER:$ES_PASS" -X DELETE "$ES_URL/snap_orders"
curl -u "$ES_USER:$ES_PASS" -X DELETE "$ES_URL/restored_orders"
curl -u "$ES_USER:$ES_PASS" -X DELETE "$ES_URL/_snapshot/my_backup_repo"
```

---

## 实战（Java SDK）

```java
// ---------- 注册 Repository ----------
client.snapshot().createRepository(r -> r
    .name("my_backup_repo")
    .type("fs")
    .settings(s -> s.location("/mnt/es_backups/snapshots").compress(true)));

// ---------- 验证 Repository ----------
var verifyResp = client.snapshot().verifyRepository(v -> v.name("my_backup_repo"));
System.out.println("Repository verified, nodes: " + verifyResp.nodes().keySet());

// ---------- 创建快照 ----------
var snapResp = client.snapshot().create(c -> c
    .repository("my_backup_repo")
    .snapshot("snap_20250615")
    .indices("snap_orders")
    .ignoreUnavailable(true)
    .includeGlobalState(false)
    .waitForCompletion(true));

System.out.println("Snapshot state: " + snapResp.snapshot().state());
System.out.println("Duration: " + snapResp.snapshot().durationInMillis() + "ms");

// ---------- 查看快照详情 ----------
var getSnap = client.snapshot().get(g -> g
    .repository("my_backup_repo")
    .snapshot("snap_20250615"));

getSnap.snapshots().forEach(snap -> {
    System.out.println("Snapshot: " + snap.snapshot());
    System.out.println("State: " + snap.state());
    System.out.println("Indices: " + snap.indices());
    System.out.println("Shards - total: " + snap.shards().total()
        + ", successful: " + snap.shards().successful()
        + ", failed: " + snap.shards().failed());
});

// ---------- 恢复快照（rename） ----------
var restoreResp = client.snapshot().restore(r -> r
    .repository("my_backup_repo")
    .snapshot("snap_20250615")
    .indices("snap_orders")
    .ignoreUnavailable(true)
    .includeGlobalState(false)
    .renamePattern("snap_(.+)")
    .renameReplacement("restored_$1")
    .indexSettings(is -> is.numberOfReplicas("0"))
    .waitForCompletion(true));

System.out.println("Restored indices: " + restoreResp.snapshot().indices());

// ---------- 恢复验证 ----------
var origCount = client.count(c -> c.index("snap_orders")).count();
var restoredCount = client.count(c -> c.index("restored_orders")).count();
System.out.println("Original: " + origCount + ", Restored: " + restoredCount);
assert origCount == restoredCount : "Count mismatch!";

// ---------- 删除快照 ----------
client.snapshot().delete(d -> d
    .repository("my_backup_repo")
    .snapshot("snap_20250615"));

// ---------- 删除 Repository ----------
client.snapshot().deleteRepository(d -> d.name("my_backup_repo"));

// ---------- 清理测试索引 ----------
client.indices().delete(d -> d.index("snap_orders", "restored_orders"));
```
