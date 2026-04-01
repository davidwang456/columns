

# 第15章 别名与零停机重建：Reindex、双写、切流

# 背景

索引结构迭代几乎不可避免。问题不在"改不改"，而在"如何不停机、可回滚地改"。别名 + Reindex 是线上最常见的安全迁移方案。本章从别名的底层原子性出发，讲透零停机迁移的完整流程、Reindex 的进阶用法，以及面向生产的风险控制。

## 本章目标

- 理解别名的底层实现，掌握读写别名的分离设计。
- 掌握 Reindex API 的核心参数和进阶用法（限流、分片并行、脚本转换）。
- 建立"创建 → Reindex → 双写 → 校验 → 切流 → 回滚窗口"的标准迁移流程。
- 了解 Task API 对长时间 Reindex 的监控与管理。

---

## 1. 为什么需要重建索引

Elasticsearch 的 Mapping 一旦字段类型确定，**不可原地修改**。以下场景都必须通过重建索引来解决：


| 场景     | 示例                                              |
| ------ | ----------------------------------------------- |
| 修改字段类型 | `text` → `keyword`，`string` → `date`            |
| 调整分词器  | 从 `standard` 换为 `ik_max_word`                   |
| 修改分片数  | 创建时分片数设置不合理                                     |
| 重构字段结构 | 将 `object` 改为 `nested`，拆分/合并字段                  |
| 数据迁移   | 跨集群、跨版本迁移                                       |
| 清理历史数据 | 去除已删除文档占用的磁盘空间（Force Merge 也可，但改 mapping 时必须重建） |


---

## 2. 别名（Alias）：解耦业务与物理索引

### 2.1 别名的本质

别名是一个**指向一个或多个物理索引的逻辑名称**。应用层通过别名读写数据，当需要切换底层物理索引时，只需修改别名指向，无需改应用配置。

```
应用层
  │  读写 "products"（别名）
  ▼
别名层
  │  products → products_v1（物理索引）
  │
  │  迁移后切换为：
  │  products → products_v2（新物理索引）
  ▼
物理索引层
```

### 2.2 别名的关键参数


| 参数                  | 含义                            |
| ------------------- | ----------------------------- |
| `index` / `indices` | 别名指向的物理索引                     |
| `alias` / `aliases` | 别名名称                          |
| `is_write_index`    | 标记该索引为别名的写入目标（多索引别名时必须指定唯一一个） |
| `filter`            | 为别名添加查询过滤器（通过别名只能看到满足条件的文档）   |
| `routing`           | 别名关联的默认路由值                    |
| `search_routing`    | 别名搜索时使用的路由值                   |
| `index_routing`     | 别名写入时使用的路由值                   |
| `is_hidden`         | 是否隐藏别名（不出现在通配符匹配中）            |


### 2.3 别名操作的原子性

`POST /_aliases` 接受一个 `actions` 数组，**整组操作在同一次集群状态更新中原子生效**：

```json
POST /_aliases
{
  "actions": [
    { "remove": { "index": "products_v1", "alias": "products" } },
    { "add":    { "index": "products_v2", "alias": "products", "is_write_index": true } }
  ]
}
```

这两个操作要么同时成功，要么同时失败——**不存在"别名已从 v1 摘除但还没加到 v2"的中间状态**。这是零停机切换的基础。

### 2.4 读写别名分离

生产环境推荐设置**独立的读别名和写别名**：

```json
POST /_aliases
{
  "actions": [
    { "add": { "index": "products_v1", "alias": "products_read" } },
    { "add": { "index": "products_v1", "alias": "products_write", "is_write_index": true } }
  ]
}
```

好处：

- 迁移期间可以先切写别名（新数据写入新索引），再切读别名（搜索切换到新索引）。
- 回滚时只需把写别名切回旧索引。

### 2.5 带过滤器的别名

别名可以附加查询过滤器，实现数据隔离的效果（类似"视图"）：

```json
POST /_aliases
{
  "actions": [
    {
      "add": {
        "index": "orders",
        "alias": "orders_active",
        "filter": { "term": { "status": "active" } }
      }
    }
  ]
}
```

通过 `orders_active` 别名查询时，只能看到 `status=active` 的订单。

---

## 3. Reindex API：数据搬迁的核心工具

### 3.1 基本用法

```json
POST _reindex
{
  "source": { "index": "products_v1" },
  "dest":   { "index": "products_v2" }
}
```

Reindex 底层是一个 **scroll + bulk** 循环：从源索引分批 scroll 读取文档，通过 bulk 写入目标索引。

### 3.2 核心参数速查

**URL 参数：**


| 参数                    | 默认值       | 说明                       |
| --------------------- | --------- | ------------------------ |
| `wait_for_completion` | `true`    | `false` 则异步执行，返回 task ID |
| `requests_per_second` | `-1`（不限流） | 限制每秒处理的文档数               |
| `slices`              | `1`       | 并行分片数，可设为 `auto`         |
| `scroll`              | `5m`      | scroll 上下文保持时间           |
| `max_docs`            | 无限制       | 最多处理的文档数                 |
| `refresh`             | `false`   | 完成后是否 refresh 目标索引       |
| `timeout`             | `1m`      | 单次 bulk 请求的超时            |


**Body - source 参数：**


| 参数        | 说明                   |
| --------- | -------------------- |
| `index`   | 源索引名（可为数组或通配符）       |
| `query`   | 只 reindex 满足条件的文档    |
| `size`    | scroll 每批大小（默认 1000） |
| `_source` | 字段过滤，只迁移指定字段         |
| `slice`   | 手动分片（`id` + `max`）   |
| `remote`  | 从远程集群拉取（跨集群迁移）       |


**Body - dest 参数：**


| 参数             | 说明                               |
| -------------- | -------------------------------- |
| `index`        | 目标索引名                            |
| `op_type`      | `create` 时只插入不覆盖（已有文档跳过）         |
| `version_type` | `external` 时保留源版本号               |
| `pipeline`     | 使用 ingest pipeline 做数据转换         |
| `routing`      | `keep`（默认保留）/ `discard` / `=<值>` |


**Body - 顶层参数：**


| 参数          | 说明                               |
| ----------- | -------------------------------- |
| `conflicts` | `abort`（默认）或 `proceed`（忽略版本冲突继续） |
| `script`    | 在 reindex 过程中用脚本修改文档             |
| `max_docs`  | 限制最大文档数                          |


### 3.3 按条件迁移

只迁移指定条件的文档：

```json
POST _reindex
{
  "source": {
    "index": "orders_v1",
    "query": {
      "range": { "created_at": { "gte": "2024-01-01" } }
    }
  },
  "dest": { "index": "orders_v2" }
}
```

### 3.4 脚本转换

在迁移过程中修改文档结构（如重命名字段、添加字段）：

```json
POST _reindex
{
  "source": { "index": "products_v1" },
  "dest":   { "index": "products_v2" },
  "script": {
    "source": """
      ctx._source.full_name = ctx._source.remove('name');
      ctx._source.updated_at = '2025-01-01T00:00:00Z';
    """
  }
}
```

脚本中可以设置 `ctx.op = 'noop'` 跳过某些文档，或 `ctx.op = 'delete'` 删除目标中已有的文档。

### 3.5 限流与并行

**限流：** 在不影响集群正常业务的前提下做迁移：

```json
POST _reindex?requests_per_second=500
{
  "source": { "index": "products_v1" },
  "dest":   { "index": "products_v2" }
}
```

限流原理：每批 1000 条写入后，计算需要等待的时间以满足目标速率。例如 `requests_per_second=500` 时，每批目标耗时 2 秒，如果实际写入花了 0.5 秒，则等待 1.5 秒。

**动态调速：** Reindex 过程中可以实时调整限流：

```bash
POST _reindex/<task_id>/_rethrottle?requests_per_second=1000
```

加速立即生效，减速在当前批次完成后生效。设为 `-1` 取消限流。

**并行分片（slicing）：** 利用多个并行工作线程加速 Reindex：

```json
POST _reindex?slices=auto
{
  "source": { "index": "products_v1" },
  "dest":   { "index": "products_v2" }
}
```

- `slices=auto`：ES 自动选择分片数（通常等于源索引的分片数）。
- 手动设置时，`slices` 建议等于源索引的分片数，不要超过。
- 远程 Reindex 不支持 slicing。

### 3.6 从远程集群 Reindex

跨集群迁移数据：

```json
POST _reindex
{
  "source": {
    "remote": {
      "host": "https://old-cluster:9200",
      "username": "user",
      "password": "pass",
      "socket_timeout": "1m",
      "connect_timeout": "30s"
    },
    "index": "old_products",
    "size": 100
  },
  "dest": { "index": "products_v2" }
}
```

远程集群必须在 `elasticsearch.yml` 中配置白名单：

```yaml
reindex.remote.whitelist: "old-cluster:9200"
```

远程 Reindex 的注意事项：

- 不支持 slicing（不能并行）。
- 建议减小 `source.size`（如 100），因为数据需要通过网络传输并缓冲在堆内存中（默认最多 100MB）。
- 适合跨版本迁移（目标集群版本 >= 源集群主版本）。

### 3.7 Reindex 响应结构

```json
{
  "took": 147,
  "timed_out": false,
  "total": 120,
  "created": 120,
  "updated": 0,
  "deleted": 0,
  "batches": 1,
  "version_conflicts": 0,
  "noops": 0,
  "retries": { "bulk": 0, "search": 0 },
  "throttled_millis": 0,
  "requests_per_second": -1.0,
  "failures": []
}
```

关键字段：

- `total` vs `created`：如果 `total > created + updated`，说明有文档被跳过。
- `version_conflicts`：使用 `conflicts: proceed` 时记录冲突数。
- `failures`：非空说明部分文档迁移失败，需要排查。

---

## 4. Task API：监控长时间 Reindex

### 4.1 异步 Reindex + Task 监控

对于大索引，同步 Reindex 可能超时。推荐异步执行：

```bash
POST _reindex?wait_for_completion=false
{
  "source": { "index": "big_index" },
  "dest":   { "index": "big_index_v2" }
}
```

返回 task ID：

```json
{ "task": "oTUltX4IQMOUUVeiohTt8A:12345" }
```

### 4.2 查看任务状态

```bash
GET _tasks/oTUltX4IQMOUUVeiohTt8A:12345
```

响应中包含进度信息：

```json
{
  "completed": false,
  "task": {
    "status": {
      "total": 1000000,
      "created": 250000,
      "updated": 0,
      "deleted": 0,
      "batches": 250,
      "version_conflicts": 0,
      "noops": 0,
      "throttled_millis": 0,
      "requests_per_second": -1.0
    }
  }
}
```

可以根据 `status.created / status.total` 计算进度百分比。

### 4.3 列出所有 Reindex 任务

```bash
GET _tasks?actions=*reindex&detailed=true
```

### 4.4 取消任务

```bash
POST _tasks/oTUltX4IQMOUUVeiohTt8A:12345/_cancel
```

取消后当前批次会执行完，但不再启动新批次。

---

## 5. 零停机迁移标准流程

### 5.1 完整流程图

```
第一步：准备
│  1. 创建新索引 products_v2（新 mapping + 新 settings）
│  2. 确认新索引的 mapping 正确
│
第二步：迁移历史数据
│  3. 执行 Reindex：products_v1 → products_v2
│  4. 等待 Reindex 完成
│
第三步：双写过渡
│  5. 应用层开始双写（同时写入 v1 和 v2）
│  6. 增量同步：对 Reindex 期间产生的增量数据补偿
│     （或使用"Reindex + 切写别名"无缝衔接）
│
第四步：校验
│  7. 数据量比对：GET _count
│  8. 抽样对比：随机取若干文档对比字段值
│  9. 聚合结果对比：关键聚合在新旧索引上对比
│
第五步：切流
│  10. 原子切换别名：摘旧挂新
│
第六步：观察与回滚窗口
│  11. 保留旧索引 N 天（如 7 天）
│  12. 监控新索引的查询和写入指标
│  13. 确认无问题后删除旧索引
```

### 5.2 方案一：Reindex + 别名切换（简单场景）

适合写入量低、Reindex 窗口内增量数据可忽略的场景。

```bash
# 1. 创建新索引
PUT products_v2 { "mappings": {...}, "settings": {...} }

# 2. Reindex
POST _reindex?wait_for_completion=false
{ "source": { "index": "products_v1" }, "dest": { "index": "products_v2" } }

# 3. 等待完成（通过 Task API 监控）

# 4. 校验数据量
GET products_v1/_count
GET products_v2/_count

# 5. 原子切换别名
POST /_aliases
{
  "actions": [
    { "remove": { "index": "products_v1", "alias": "products" } },
    { "add":    { "index": "products_v2", "alias": "products", "is_write_index": true } }
  ]
}
```

### 5.3 方案二：双写 + Reindex（高写入场景）

适合 Reindex 期间有持续写入、不能丢失增量数据的场景。

```
时间线：
  T0     T1              T2       T3
  │      │               │        │
  ▼      ▼               ▼        ▼
  开始   开始双写         Reindex   切别名
  Reindex (v1+v2)        完成      (只保留v2)
```

步骤：

1. **T0**：启动 Reindex（`products_v1 → products_v2`）。
2. **T1**：应用层开始双写——所有新写入同时发往 v1 和 v2。此时 Reindex 的 `dest.op_type=create` 避免覆盖双写期间已到 v2 的新文档。
3. **T2**：Reindex 完成。校验数据。
4. **T3**：原子切换别名到 v2，停止双写。

```json
POST _reindex
{
  "source": { "index": "products_v1" },
  "dest":   { "index": "products_v2", "op_type": "create" },
  "conflicts": "proceed"
}
```

`op_type: create` + `conflicts: proceed` 的组合确保：

- Reindex 搬迁的旧数据不会覆盖双写期间已到 v2 的新数据。
- 版本冲突被忽略而非中止迁移。

### 5.4 方案三：写别名先切 + Reindex 历史（推荐）

利用读写别名分离，进一步简化流程：

```bash
# 初始状态
# products_write → products_v1 (is_write_index=true)
# products_read  → products_v1

# 1. 创建新索引
PUT products_v2

# 2. 切写别名：新数据直接写入 v2
POST /_aliases
{ "actions": [
  { "remove": { "index": "products_v1", "alias": "products_write" } },
  { "add":    { "index": "products_v2", "alias": "products_write", "is_write_index": true } }
]}

# 3. Reindex 历史数据（op_type=create 不覆盖已有）
POST _reindex?wait_for_completion=false
{
  "source": { "index": "products_v1" },
  "dest":   { "index": "products_v2", "op_type": "create" },
  "conflicts": "proceed"
}

# 4. Reindex 完成后，切读别名
POST /_aliases
{ "actions": [
  { "remove": { "index": "products_v1", "alias": "products_read" } },
  { "add":    { "index": "products_v2", "alias": "products_read" } }
]}
```

优势：不需要应用层实现双写逻辑，只需使用别名，全程通过 ES API 完成。

---

## 6. 迁移校验清单

### 6.1 数量校验

```bash
# 对比文档总数
GET products_v1/_count
GET products_v2/_count
```

### 6.2 抽样校验

```bash
# 随机抽取若干文档 ID，对比内容
GET products_v1/_doc/<id>
GET products_v2/_doc/<id>
```

### 6.3 聚合校验

```bash
# 关键聚合指标对比
POST products_v1/_search
{ "size": 0, "aggs": { "total_revenue": { "sum": { "field": "price" } } } }

POST products_v2/_search
{ "size": 0, "aggs": { "total_revenue": { "sum": { "field": "price" } } } }
```

### 6.4 Mapping 校验

```bash
# 确认新索引 mapping 符合预期
GET products_v2/_mapping
```

### 6.5 校验清单模板


| 检查项          | v1 值         | v2 值         | 通过？ |
| ------------ | ------------ | ------------ | --- |
| 文档总数         | 1,000,000    | 1,000,000    | ✓   |
| 字段 price 总和  | 5,234,567.89 | 5,234,567.89 | ✓   |
| 分类分布 Top 5   | [...]        | [...]        | ✓   |
| 随机抽样 10 条    | 一致           | 一致           | ✓   |
| mapping 字段类型 | —            | 符合预期         | ✓   |


---

## 7. 回滚策略

### 7.1 回滚 = 切回别名

如果发现新索引有问题，回滚只需一条原子别名切换命令：

```json
POST /_aliases
{
  "actions": [
    { "remove": { "index": "products_v2", "alias": "products" } },
    { "add":    { "index": "products_v1", "alias": "products", "is_write_index": true } }
  ]
}
```

### 7.2 回滚窗口

- 旧索引在切流后至少保留 **7 天**（或根据业务需要）。
- 旧索引可设为**只读**减少资源占用：

```json
PUT products_v1/_settings
{ "index.blocks.write": true }
```

### 7.3 回滚期间的数据缺口

如果已经有新数据写入了 v2（但不在 v1 中），回滚后这部分数据在 v1 中不可见。解决方案：

- 从 v2 向 v1 做一次增量 Reindex。
- 或从上游数据源重放这段时间的写入。

---

## 8. 索引模板：自动化别名管理

对于按时间滚动的索引（如日志），使用 **索引模板（Index Template）** 自动为新索引绑定别名：

```json
PUT _index_template/logs_template
{
  "index_patterns": ["logs-*"],
  "template": {
    "settings": {
      "number_of_shards": 3,
      "number_of_replicas": 1
    },
    "mappings": {
      "properties": {
        "message": { "type": "text" },
        "timestamp": { "type": "date" }
      }
    },
    "aliases": {
      "logs_current": {}
    }
  }
}
```

每当创建符合 `logs-*` 模式的新索引时，都会自动绑定 `logs_current` 别名。

---

# 总结

- 别名是零停机迁移的基石——`POST /_aliases` 的 actions 数组在同一次集群状态更新中**原子生效**。
- 读写别名分离是生产最佳实践——先切写别名、后切读别名，可以最小化迁移风险。
- Reindex 不是简单的复制——`op_type: create` + `conflicts: proceed` 是双写场景的关键组合。
- 大索引 Reindex 必须异步执行（`wait_for_completion=false`），通过 Task API 监控进度，通过 `_rethrottle` 动态调速。
- 迁移校验不是可选步骤——数量、抽样、聚合三级校验是确保迁移正确的底线。
- 回滚 = 切回别名——旧索引保留 N 天作为安全网，直到确认新索引完全正常。

---

## 练习题

1. 设计一次 mapping 变更迁移：从 `text` 类型改为 `keyword`，写出完整的操作步骤（建索引 → Reindex → 校验 → 切别名）。
2. 使用 `_reindex` + `script` 在迁移过程中重命名一个字段，验证新索引中的字段结构。
3. 模拟双写场景：先启动 Reindex（`op_type: create, conflicts: proceed`），同时向目标索引写入新数据，验证新数据不被 Reindex 覆盖。
4. 对一个包含 10 万条文档的索引做异步 Reindex（`slices=auto`），通过 Task API 监控进度百分比。
5. 写出迁移校验清单（数量、抽样、聚合结果），设计迁移失败时的回滚步骤。

---

## 实战（curl）

### 准备初始索引和别名

```bash
# 创建 v1 索引
curl -u "$ES_USER:$ES_PASS" -X PUT "$ES_URL/products_v1" \
  -H "Content-Type: application/json" \
  -d '{
    "settings": { "number_of_shards": 1, "number_of_replicas": 0 },
    "mappings": { "properties": {
      "name":     { "type": "text" },
      "category": { "type": "keyword" },
      "price":    { "type": "double" }
    }}
  }'

# 写入测试数据
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/products_v1/_bulk?refresh=wait_for" \
  -H "Content-Type: application/x-ndjson" \
  -d '{"index":{"_id":"1"}}
{"name":"笔记本电脑","category":"electronics","price":6999}
{"index":{"_id":"2"}}
{"name":"机械键盘","category":"peripherals","price":599}
{"index":{"_id":"3"}}
{"name":"降噪耳机","category":"peripherals","price":1299}
{"index":{"_id":"4"}}
{"name":"显示器支架","category":"accessories","price":199}
'

# 设置初始别名
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/_aliases" \
  -H "Content-Type: application/json" \
  -d '{
    "actions": [
      { "add": { "index": "products_v1", "alias": "products_read" } },
      { "add": { "index": "products_v1", "alias": "products_write", "is_write_index": true } }
    ]
  }'

# 验证别名
curl -u "$ES_USER:$ES_PASS" "$ES_URL/_cat/aliases/products_*?v"
```

### 创建新索引（mapping 变更）

```bash
# v2 索引：新增 brand 字段，name 加 keyword 子字段
curl -u "$ES_USER:$ES_PASS" -X PUT "$ES_URL/products_v2" \
  -H "Content-Type: application/json" \
  -d '{
    "settings": { "number_of_shards": 1, "number_of_replicas": 0 },
    "mappings": { "properties": {
      "name":     { "type": "text", "fields": { "keyword": { "type": "keyword" } } },
      "brand":    { "type": "keyword" },
      "category": { "type": "keyword" },
      "price":    { "type": "double" }
    }}
  }'
```

### Reindex + 脚本转换

```bash
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/_reindex?pretty" \
  -H "Content-Type: application/json" \
  -d '{
    "source": { "index": "products_v1" },
    "dest":   { "index": "products_v2", "op_type": "create" },
    "conflicts": "proceed",
    "script": {
      "source": "ctx._source.brand = \"unknown\""
    }
  }'
```

### 迁移校验

```bash
# 数量对比
echo "=== v1 count ==="
curl -u "$ES_USER:$ES_PASS" "$ES_URL/products_v1/_count?pretty"
echo "=== v2 count ==="
curl -u "$ES_USER:$ES_PASS" "$ES_URL/products_v2/_count?pretty"

# 聚合对比
echo "=== v1 price sum ==="
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/products_v1/_search?pretty&filter_path=aggregations" \
  -H "Content-Type: application/json" \
  -d '{ "size": 0, "aggs": { "total_price": { "sum": { "field": "price" } } } }'

echo "=== v2 price sum ==="
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/products_v2/_search?pretty&filter_path=aggregations" \
  -H "Content-Type: application/json" \
  -d '{ "size": 0, "aggs": { "total_price": { "sum": { "field": "price" } } } }'

# 抽样对比
curl -u "$ES_USER:$ES_PASS" "$ES_URL/products_v1/_doc/1?pretty&filter_path=_source"
curl -u "$ES_USER:$ES_PASS" "$ES_URL/products_v2/_doc/1?pretty&filter_path=_source"
```

### 原子切换别名

```bash
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/_aliases?pretty" \
  -H "Content-Type: application/json" \
  -d '{
    "actions": [
      { "remove": { "index": "products_v1", "alias": "products_read" } },
      { "add":    { "index": "products_v2", "alias": "products_read" } },
      { "remove": { "index": "products_v1", "alias": "products_write" } },
      { "add":    { "index": "products_v2", "alias": "products_write", "is_write_index": true } }
    ]
  }'

# 验证切换结果
curl -u "$ES_USER:$ES_PASS" "$ES_URL/_cat/aliases/products_*?v"

# 通过别名查询（应该查到 v2 的数据）
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/products_read/_search?pretty&filter_path=hits.hits._source.brand" \
  -H "Content-Type: application/json" \
  -d '{ "size": 1 }'
```

### 回滚演示

```bash
# 回滚：切回 v1
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/_aliases?pretty" \
  -H "Content-Type: application/json" \
  -d '{
    "actions": [
      { "remove": { "index": "products_v2", "alias": "products_read" } },
      { "add":    { "index": "products_v1", "alias": "products_read" } },
      { "remove": { "index": "products_v2", "alias": "products_write" } },
      { "add":    { "index": "products_v1", "alias": "products_write", "is_write_index": true } }
    ]
  }'
```

### 异步 Reindex + Task 监控

```bash
# 异步启动
TASK_ID=$(curl -s -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/_reindex?wait_for_completion=false" \
  -H "Content-Type: application/json" \
  -d '{
    "source": { "index": "products_v1" },
    "dest":   { "index": "products_v3" }
  }' | python -c "import sys,json; print(json.load(sys.stdin)['task'])")

echo "Task ID: $TASK_ID"

# 查看进度
curl -u "$ES_USER:$ES_PASS" "$ES_URL/_tasks/$TASK_ID?pretty"

# 列出所有 reindex 任务
curl -u "$ES_USER:$ES_PASS" "$ES_URL/_tasks?actions=*reindex&detailed=true&pretty"

# 动态调速
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/_reindex/$TASK_ID/_rethrottle?requests_per_second=500"

# 取消任务（如有需要）
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/_tasks/$TASK_ID/_cancel"
```

### 带过滤器的别名

```bash
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/_aliases?pretty" \
  -H "Content-Type: application/json" \
  -d '{
    "actions": [{
      "add": {
        "index": "products_v2",
        "alias": "products_peripherals",
        "filter": { "term": { "category": "peripherals" } }
      }
    }]
  }'

# 通过过滤别名查询 — 只返回 peripherals 类别
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/products_peripherals/_search?pretty" \
  -H "Content-Type: application/json" \
  -d '{ "query": { "match_all": {} } }'
```

### 清理

```bash
curl -u "$ES_USER:$ES_PASS" -X DELETE "$ES_URL/products_v1,products_v2,products_v3"
```

---

## 实战（Java SDK）

```java
// ---------- 创建新索引 ----------
client.indices().create(c -> c.index("products_v2")
    .mappings(m -> m
        .properties("name", p -> p.text(t -> t.fields("keyword", f -> f.keyword(k -> k))))
        .properties("brand", p -> p.keyword(k -> k))
        .properties("category", p -> p.keyword(k -> k))
        .properties("price", p -> p.double_(d -> d))));

// ---------- Reindex ----------
var reindexResp = client.reindex(r -> r
    .source(s -> s.index("products_v1"))
    .dest(d -> d.index("products_v2").opType(OpType.Create))
    .conflicts(Conflicts.Proceed)
    .script(s -> s.inline(i -> i.source("ctx._source.brand = 'unknown'"))));

System.out.println("Total: " + reindexResp.total() + ", Created: " + reindexResp.created());

// ---------- 校验数据量 ----------
var v1Count = client.count(c -> c.index("products_v1")).count();
var v2Count = client.count(c -> c.index("products_v2")).count();
System.out.println("v1: " + v1Count + ", v2: " + v2Count);
assert v1Count == v2Count : "数据量不一致！";

// ---------- 原子切换别名 ----------
client.indices().updateAliases(u -> u
    .actions(a -> a.remove(r -> r.index("products_v1").alias("products_read")))
    .actions(a -> a.add(ad -> ad.index("products_v2").alias("products_read")))
    .actions(a -> a.remove(r -> r.index("products_v1").alias("products_write")))
    .actions(a -> a.add(ad -> ad.index("products_v2").alias("products_write").isWriteIndex(true))));

// ---------- 通过别名查询验证 ----------
var searchResp = client.search(s -> s.index("products_read")
    .query(q -> q.matchAll(m -> m))
    .size(1), Map.class);
System.out.println("通过别名查询结果: " + searchResp.hits().hits().get(0).source());

// ---------- 回滚（切回 v1）----------
client.indices().updateAliases(u -> u
    .actions(a -> a.remove(r -> r.index("products_v2").alias("products_read")))
    .actions(a -> a.add(ad -> ad.index("products_v1").alias("products_read")))
    .actions(a -> a.remove(r -> r.index("products_v2").alias("products_write")))
    .actions(a -> a.add(ad -> ad.index("products_v1").alias("products_write").isWriteIndex(true))));

// ---------- 带过滤器的别名 ----------
client.indices().updateAliases(u -> u
    .actions(a -> a.add(ad -> ad
        .index("products_v2")
        .alias("products_peripherals")
        .filter(f -> f.term(t -> t.field("category").value("peripherals"))))));

// ---------- 清理 ----------
client.indices().delete(d -> d.index("products_v1", "products_v2"));
```

