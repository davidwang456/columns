

# 第14章 更新与幂等：Update、Upsert、版本冲突

# 背景

在分布式系统里，更新请求重试是常态。没有幂等设计与版本冲突治理，数据就会出现"偶发不一致"，而且很难复现。本章从 ES 的更新内部机制出发，讲透 Update/Upsert 的工作原理、乐观并发控制（OCC）的正确用法，以及面向生产的幂等策略设计。

## 本章目标

- 理解 `_update` 的内部流程（get-then-index）及其并发含义。
- 掌握 `_seq_no` + `_primary_term` 乐观并发控制的正确用法。
- 理解 `retry_on_conflict` 与 CAS 的关系和互斥约束。
- 建立可重试、可回放的写入更新方案。

---

## 1. Index vs Update：两种写入语义

### 1.1 Index（全量覆盖）

`PUT /my_index/_doc/1` 或 `POST /my_index/_doc/1`，将整个文档替换：

```json
PUT /my_index/_doc/1
{
  "name": "笔记本",
  "price": 6999,
  "stock": 100
}
```

- 如果文档不存在则创建，存在则**整体覆盖**。
- 逻辑简单，幂等性天然好——同一请求多次执行，结果一致。
- 缺点：必须传完整文档，不适合只改一个字段的场景。

### 1.2 Update（局部更新）

`POST /my_index/_update/1`，只修改指定字段：

```json
POST /my_index/_update/1
{
  "doc": {
    "price": 5999
  }
}
```

- 只传需要修改的字段，其他字段保持不变。
- 底层依然是**整文档写入**——先读旧文档，合并后写入新版本。

### 1.3 Update 的内部实现：Get-Then-Index

Update 不是"原地修改"，而是一个**读-改-写**循环：

```
客户端发起 _update 请求
    │
    ▼
主分片执行 getForUpdate()
    │  从引擎层实时读取当前文档（带 _seq_no / _primary_term）
    │
    ▼
UpdateHelper.prepare()
    ├── 文档不存在？
    │   ├── 有 upsert / doc_as_upsert → 执行插入
    │   └── 否 → DocumentMissingException
    ├── 有 script？ → 执行脚本，ctx.op 决定 index/delete/noop
    └── 有 doc？ → 合并 partial doc 到旧 _source
        └── detect_noop=true（默认）？ → 无变化则 NOOP
    │
    ▼
生成 IndexRequest（或 DeleteRequest）
    │  关键：setIfSeqNo(旧文档的 _seq_no)
    │        setIfPrimaryTerm(旧文档的 _primary_term)
    │
    ▼
通过 Bulk 路径执行写入
    │  引擎在写入时校验 _seq_no / _primary_term
    │  如果已被其他请求修改 → VersionConflictEngineException
```

**核心要点：**

- Update 内部使用 CAS（Compare-And-Swap）保证原子性——写入时会校验文档自读取以来是否被修改。
- 如果在"读"和"写"之间有其他请求修改了同一文档，Update 会收到 **409 版本冲突**。

### 1.4 detect_noop：避免无效写入

`detect_noop`（默认 `true`）在 Update 合并 partial doc 后，会对比新旧 `_source` 是否有实际变化。如果没有变化，直接返回 `"result": "noop"`，不执行任何写入操作。

这对高频幂等重试的场景非常友好——重复发送相同的 Update 请求不会产生无谓的 Segment 写入和 `_version` 递增。

---

## 2. Upsert：统一插入与更新

### 2.1 基本 Upsert

当"文档可能不存在"时，直接 Update 会返回 `DocumentMissingException`。Upsert 统一了"不存在则插入，存在则更新"的逻辑：

```json
POST /my_index/_update/1
{
  "doc": { "price": 5999 },
  "upsert": {
    "name": "笔记本",
    "price": 5999,
    "stock": 100
  }
}
```

- 文档不存在 → 使用 `upsert` 中的内容创建文档。
- 文档已存在 → 使用 `doc` 中的内容做局部更新。

### 2.2 doc_as_upsert

如果插入和更新使用完全相同的内容，可以用 `doc_as_upsert` 简化：

```json
POST /my_index/_update/1
{
  "doc": { "name": "笔记本", "price": 5999 },
  "doc_as_upsert": true
}
```

等价于"如果不存在就用 doc 插入，如果存在就用 doc 更新"。

### 2.3 scripted_upsert

结合脚本和 upsert，可实现更复杂的"初始化或累加"逻辑：

```json
POST /my_index/_update/1
{
  "scripted_upsert": true,
  "script": {
    "source": "ctx._source.counter += params.inc",
    "params": { "inc": 1 }
  },
  "upsert": {
    "counter": 0
  }
}
```

- 文档不存在 → 先用 `upsert` 创建（`counter=0`），然后执行脚本（`counter += 1`），最终 `counter=1`。
- 文档已存在 → 直接执行脚本累加。

### 2.4 Script 中的 ctx.op

在 Update 脚本中，可以通过设置 `ctx.op` 控制操作结果：


| `ctx.op`      | 行为       |
| ------------- | -------- |
| `"index"`（默认） | 写入修改后的文档 |
| `"delete"`    | 删除文档     |
| `"noop"`      | 不做任何操作   |


```json
POST /my_index/_update/1
{
  "script": {
    "source": "if (ctx._source.stock <= 0) { ctx.op = 'delete' } else { ctx._source.stock -= 1 }"
  }
}
```

---

## 3. 版本与乐观并发控制（OCC）

### 3.1 `_seq_no` 与 `_primary_term`

ES 为每次写入操作分配两个标识：


| 字段              | 含义                            |
| --------------- | ----------------------------- |
| `_seq_no`       | 序列号——主分片上每次操作递增，保证同一分片上操作的全序性 |
| `_primary_term` | 主任期——每当主分片发生切换（failover）时递增   |


两者组合 **唯一标识一次变更**。你可以在 Index / Update / Delete 响应和 GET 响应中获取它们：

```json
{
  "_index": "my_index",
  "_id": "1",
  "_version": 3,
  "_seq_no": 42,
  "_primary_term": 1,
  "result": "updated"
}
```

### 3.2 使用 if_seq_no + if_primary_term 实现 CAS

标准的乐观并发控制流程：

```
1. 读取文档，记录 _seq_no 和 _primary_term
   GET /my_index/_doc/1
   → _seq_no=42, _primary_term=1

2. 修改后带条件写回
   PUT /my_index/_doc/1?if_seq_no=42&if_primary_term=1
   { "name": "笔记本", "price": 4999 }

3. 如果在步骤 1 和 2 之间没有其他修改 → 成功
   如果有其他修改 → 返回 409 Conflict
```

```
成功响应：200 OK
{
  "_seq_no": 43,      ← 新的序列号
  "_primary_term": 1,
  "result": "updated"
}

冲突响应：409 Conflict
{
  "error": {
    "type": "version_conflict_engine_exception",
    "reason": "[1]: version conflict, required seqNo [42], primary term [1].
               current document has seqNo [43] and primary term [1]"
  }
}
```

### 3.3 冲突后如何处理

收到 409 冲突后，应用层有几种策略：


| 策略      | 做法                                              | 适用场景        |
| ------- | ----------------------------------------------- | ----------- |
| 重试      | 重新 GET 获取最新 `_seq_no`/`_primary_term`，合并修改后再次写入 | 通用场景        |
| 最后写入胜出  | 不带 `if_seq_no` 直接覆盖                             | 对一致性要求不高的场景 |
| 放弃并通知用户 | 提示"数据已被修改，请刷新后重试"                               | 用户交互界面      |
| 合并冲突    | 读取最新版本，比较差异，智能合并                                | 复杂业务逻辑      |


### 3.4 `_version` 的历史角色

早期 ES 使用 `_version` 字段做并发控制，但 `_version` 是分片本地的单调递增数字，在主分片切换时无法保证全局唯一性。从 ES 6.7+ 开始，**推荐使用 `_seq_no` + `_primary_term`** 替代 `_version` 做并发控制。

Update API 的 REST 层已经**禁止**在请求参数中传 `version` / `version_type`——如果你这样做，会收到明确的错误信息：

> "internal versioning can not be used for optimistic concurrency control. Please use `if_seq_no` and `if_primary_term` instead"

---

## 4. 外部版本控制（External Versioning）

### 4.1 适用场景

当 ES 不是数据的主存储，而是从外部数据库同步数据时，可以使用外部版本控制——让外部系统的版本号（如数据库的 `updated_at` 时间戳或递增版本号）直接作为 ES 的 `_version`。

### 4.2 两种外部版本类型


| 类型                          | 冲突规则               | 写入后 `_version` |
| --------------------------- | ------------------ | -------------- |
| `version_type=external`     | 请求版本必须**严格大于**当前版本 | 请求中的版本号        |
| `version_type=external_gte` | 请求版本必须**大于等于**当前版本 | 请求中的版本号        |


```bash
# external：版本号必须递增
PUT /my_index/_doc/1?version=5&version_type=external
{ "name": "笔记本" }

# external_gte：允许版本号相同（幂等重放）
PUT /my_index/_doc/1?version=5&version_type=external_gte
{ "name": "笔记本" }
```

### 4.3 external vs external_gte 的选择

- `**external**`：严格递增，适合版本号有严格单调性保证的场景（如数据库自增 version 列）。
- `**external_gte**`：允许等于，适合消息队列重放场景——同一条消息被消费多次时，版本号相同的重复写入不会报冲突。

### 4.4 注意事项

- 外部版本控制**仅用于 Index/Delete**，不适用于 Update API。
- 外部版本号与 `_seq_no`/`_primary_term` 是两套独立机制，不要混用。

---

## 5. retry_on_conflict

### 5.1 工作原理

`retry_on_conflict` 是 Update API 专有的参数（默认 `0`），指定在版本冲突时自动重试的次数：

```json
POST /my_index/_update/1?retry_on_conflict=3
{
  "doc": { "price": 5999 }
}
```

当 Update 的 get-then-index 过程中发生 `VersionConflictEngineException` 时，ES 会**从头重新执行**整个 Update 流程（重新读取 → 重新合并 → 重新写入），最多重试 `retry_on_conflict` 次。

### 5.2 在 Bulk 中使用

Bulk 中的 Update 操作也支持 `retry_on_conflict`：

```json
POST /_bulk
{"update":{"_index":"my_index","_id":"1","retry_on_conflict":3}}
{"doc":{"price":5999}}
```

### 5.3 与 CAS 互斥

`**retry_on_conflict` 与 `if_seq_no`/`if_primary_term` 不能同时使用。**

ES 在 `UpdateRequest.validate()` 中显式禁止了这种组合：

> "compare and write operations can not be retried"

原因：CAS 语义要求"精确写入指定版本"。如果冲突后自动重试，新一轮读到的版本已经变了，继续写入可能掩盖逻辑错误。CAS 冲突应由应用层显式处理。

### 5.4 `if_seq_no` 与 upsert 也互斥

ES 同样禁止 `if_seq_no` 与 `upsert` / `doc_as_upsert` 的组合。因为 CAS 预期文档已存在且版本已知，与 upsert 的"可能不存在"语义矛盾。

### 5.5 决策指南


| 场景                       | 方案                                       |
| ------------------------ | ---------------------------------------- |
| "累加计数器"等无需精确版本控制的 Update | `retry_on_conflict=3`（或更高）               |
| "读改写"业务逻辑，需要精确版本控制       | `if_seq_no` + `if_primary_term`，应用层处理冲突  |
| 从外部系统同步数据                | `version_type=external` 或 `external_gte` |
| 幂等写入，不关心现有内容             | 直接 `index`（全量覆盖），天然幂等                    |


---

## 6. Update API 完整参数速查


| 参数                  | 默认值     | 说明                             |
| ------------------- | ------- | ------------------------------ |
| `doc`               | —       | 需要合并到现有文档中的 partial document   |
| `upsert`            | —       | 文档不存在时用于创建的完整文档                |
| `doc_as_upsert`     | `false` | 为 `true` 时，文档不存在则用 `doc` 的内容创建 |
| `script`            | —       | 用脚本修改文档                        |
| `scripted_upsert`   | `false` | 为 `true` 时，无论文档是否存在都执行脚本       |
| `detect_noop`       | `true`  | 为 `true` 时，如果 doc 合并后无变化则跳过写入  |
| `retry_on_conflict` | `0`     | 版本冲突时的自动重试次数                   |
| `if_seq_no`         | —       | CAS 条件：期望的序列号                  |
| `if_primary_term`   | —       | CAS 条件：期望的主任期                  |
| `timeout`           | `1m`    | 等待主分片可用的超时时间                   |
| `refresh`           | `false` | 写入后的刷新策略                       |
| `_source`           | `true`  | 返回的 `_source` 过滤配置             |
| `routing`           | —       | 自定义路由值                         |


---

## 7. 面向生产的幂等策略设计

### 7.1 什么是幂等

同一操作执行一次和执行多次，对系统状态的影响完全相同。在分布式环境中，由于网络超时、消费者重启等原因，写入请求可能被重复发送，幂等性是保证数据正确的基础。

### 7.2 各写入方式的幂等性


| 操作                                   | 幂等性  | 说明                                   |
| ------------------------------------ | ---- | ------------------------------------ |
| `index`（指定 `_id`）                    | 天然幂等 | 相同内容多次 PUT，结果相同（`_version` 会递增但内容不变） |
| `index`（不指定 `_id`）                   | 不幂等  | 每次生成新 `_id`，重复请求会产生多条文档              |
| `update` + `doc`（`detect_noop=true`） | 近似幂等 | 相同 doc 重复发送，`detect_noop` 阻止无效写入     |
| `update` + `script`（累加类）             | 不幂等  | 每次执行脚本都会累加                           |
| `upsert` + `doc_as_upsert`           | 近似幂等 | 首次创建，后续 `detect_noop`                |
| `version_type=external_gte`          | 幂等   | 相同版本号重复写入不冲突                         |


### 7.3 实战幂等方案

**方案一：业务 ID 作为 `_id` + 全量 Index**

最简单的幂等方案——用业务唯一标识（如订单号）作为 `_id`，每次写入都是全量文档覆盖：

```bash
PUT /orders/_doc/ORDER-20250101-001
{
  "order_id": "ORDER-20250101-001",
  "status": "paid",
  "amount": 299.0
}
```

无论执行多少次，文档内容始终一致。

**方案二：外部版本 + external_gte**

从消息队列消费数据入 ES，消息可能被重复消费：

```bash
PUT /orders/_doc/ORDER-001?version=1001&version_type=external_gte
{
  "order_id": "ORDER-001",
  "status": "shipped",
  "updated_at": 1001
}
```

- 使用数据库的 `updated_at` 或 version 字段作为外部版本号。
- `external_gte` 允许相同版本号的重复写入（幂等）。
- 旧版本不会覆盖新版本（防乱序）。

**方案三：CAS 用于精确控制的业务操作**

库存扣减等需要精确控制的场景：

```python
def deduct_stock(product_id, quantity):
    for attempt in range(MAX_RETRIES):
        doc = es.get(index="products", id=product_id)
        current_stock = doc["_source"]["stock"]
        seq_no = doc["_seq_no"]
        primary_term = doc["_primary_term"]

        if current_stock < quantity:
            raise InsufficientStockError()

        try:
            es.index(
                index="products",
                id=product_id,
                body={"stock": current_stock - quantity, ...},
                if_seq_no=seq_no,
                if_primary_term=primary_term
            )
            return  # 成功
        except ConflictError:
            continue  # 重试

    raise MaxRetriesExceeded()
```

### 7.4 重试策略要点


| 维度     | 建议                              |
| ------ | ------------------------------- |
| 最大重试次数 | 有限次（如 3~5 次），避免流量雪崩             |
| 重试间隔   | 指数退避（exponential backoff）       |
| 可追踪性   | 每次重试记录日志，含 `_id`、`_seq_no`、冲突原因 |
| 死信处理   | 超过最大重试次数的操作写入死信队列，人工介入          |


---

# 总结

- Update 的本质是 **get-then-index**——先实时读取旧文档，合并后带 CAS 条件写回。理解这个流程是理解版本冲突的前提。
- 乐观并发控制使用 `if_seq_no` + `if_primary_term`，不要再用旧的 `version` 参数。
- `retry_on_conflict` 适合"不需要精确版本控制"的 Update 场景（如计数器累加），与 CAS 不可同时使用。
- 外部版本控制（`external` / `external_gte`）适合从外部系统同步数据到 ES 的场景。
- 幂等设计是高可用写入的基础——优先选择"业务 ID + 全量覆盖"或"外部版本 + external_gte"，只在必要时使用 CAS。
- `detect_noop=true` 是一个被低估的特性——它能在幂等重试场景中避免大量无效写入。

---

## 练习题

1. 实现一个 `doc_as_upsert` 写入接口，验证文档不存在和已存在时的行为差异。
2. 模拟并发更新：两个客户端同时读取同一文档的 `_seq_no`/`_primary_term`，各自修改后带 CAS 条件写回，观察谁成功、谁收到 409。
3. 使用 `retry_on_conflict=5` 和脚本实现一个计数器累加，在并发下验证最终值的正确性。
4. 用 `version_type=external_gte` 模拟消息队列乱序消费场景，验证旧版本不会覆盖新版本。
5. 给出你们业务的幂等策略设计——选择哪种方案、为什么。

---

## 实战（curl）

### 准备测试索引

```bash
curl -u "$ES_USER:$ES_PASS" -X PUT "$ES_URL/update_demo" \
  -H "Content-Type: application/json" \
  -d '{
    "mappings": { "properties": {
      "name":    { "type": "text" },
      "price":   { "type": "double" },
      "stock":   { "type": "integer" },
      "counter": { "type": "integer" },
      "status":  { "type": "keyword" }
    }}
  }'
```

### Index vs Update

```bash
# 全量写入（Index）—— 天然幂等
curl -u "$ES_USER:$ES_PASS" -X PUT "$ES_URL/update_demo/_doc/1?refresh=wait_for&pretty" \
  -H "Content-Type: application/json" \
  -d '{ "name": "笔记本", "price": 6999, "stock": 100, "status": "active" }'

# 局部更新（Update）—— 只改 price
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/update_demo/_update/1?refresh=wait_for&pretty" \
  -H "Content-Type: application/json" \
  -d '{ "doc": { "price": 5999 } }'
```

### Upsert

```bash
# doc_as_upsert：不存在则创建，存在则更新
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/update_demo/_update/2?refresh=wait_for&pretty" \
  -H "Content-Type: application/json" \
  -d '{
    "doc": { "name": "机械键盘", "price": 599, "stock": 50 },
    "doc_as_upsert": true
  }'

# 独立 upsert 内容
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/update_demo/_update/3?refresh=wait_for&pretty" \
  -H "Content-Type: application/json" \
  -d '{
    "doc": { "price": 499 },
    "upsert": { "name": "鼠标垫", "price": 49, "stock": 200 }
  }'
```

### scripted_upsert：计数器累加

```bash
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/update_demo/_update/counter1?refresh=wait_for&pretty" \
  -H "Content-Type: application/json" \
  -d '{
    "scripted_upsert": true,
    "script": {
      "source": "ctx._source.counter += params.inc",
      "params": { "inc": 1 }
    },
    "upsert": { "counter": 0 }
  }'
```

### 乐观并发控制（CAS）

```bash
# 1) 读取文档，获取 _seq_no 和 _primary_term
curl -u "$ES_USER:$ES_PASS" -X GET "$ES_URL/update_demo/_doc/1?pretty" \
  | grep -E '"_seq_no"|"_primary_term"'

# 2) 带 CAS 条件写入（替换 SEQ 和 TERM 为实际值）
SEQ=2
TERM=1
curl -u "$ES_USER:$ES_PASS" -X PUT "$ES_URL/update_demo/_doc/1?if_seq_no=$SEQ&if_primary_term=$TERM&refresh=wait_for&pretty" \
  -H "Content-Type: application/json" \
  -d '{ "name": "笔记本", "price": 4999, "stock": 100, "status": "active" }'

# 3) 用相同的 SEQ/TERM 再次写入 → 409 Conflict
curl -u "$ES_USER:$ES_PASS" -X PUT "$ES_URL/update_demo/_doc/1?if_seq_no=$SEQ&if_primary_term=$TERM&pretty" \
  -H "Content-Type: application/json" \
  -d '{ "name": "笔记本", "price": 4599, "stock": 100, "status": "active" }'
```

### retry_on_conflict

```bash
# 带自动重试的 Update（适合计数器等场景）
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/update_demo/_update/counter1?retry_on_conflict=5&refresh=wait_for&pretty" \
  -H "Content-Type: application/json" \
  -d '{
    "script": {
      "source": "ctx._source.counter += params.inc",
      "params": { "inc": 1 }
    }
  }'
```

### 外部版本控制

```bash
# external：版本号必须严格递增
curl -u "$ES_USER:$ES_PASS" -X PUT "$ES_URL/update_demo/_doc/ext1?version=10&version_type=external&refresh=wait_for&pretty" \
  -H "Content-Type: application/json" \
  -d '{ "name": "外部同步数据", "status": "v10" }'

# 再次写入相同版本号 → 409 Conflict
curl -u "$ES_USER:$ES_PASS" -X PUT "$ES_URL/update_demo/_doc/ext1?version=10&version_type=external&pretty" \
  -H "Content-Type: application/json" \
  -d '{ "name": "外部同步数据", "status": "v10-dup" }'

# external_gte：允许等于（幂等重放）
curl -u "$ES_USER:$ES_PASS" -X PUT "$ES_URL/update_demo/_doc/ext1?version=10&version_type=external_gte&refresh=wait_for&pretty" \
  -H "Content-Type: application/json" \
  -d '{ "name": "外部同步数据", "status": "v10-replay" }'

# 版本号更大才能覆盖
curl -u "$ES_USER:$ES_PASS" -X PUT "$ES_URL/update_demo/_doc/ext1?version=11&version_type=external&refresh=wait_for&pretty" \
  -H "Content-Type: application/json" \
  -d '{ "name": "外部同步数据", "status": "v11" }'
```

### detect_noop 验证

```bash
# 发送与当前文档内容相同的 Update → result: noop
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/update_demo/_update/2?refresh=wait_for&pretty" \
  -H "Content-Type: application/json" \
  -d '{ "doc": { "name": "机械键盘", "price": 599, "stock": 50 } }'

# 关闭 detect_noop → 即使内容相同也会写入（version 递增）
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/update_demo/_update/2?refresh=wait_for&pretty" \
  -H "Content-Type: application/json" \
  -d '{
    "doc": { "name": "机械键盘", "price": 599, "stock": 50 },
    "detect_noop": false
  }'
```

### Bulk 中的 Update

```bash
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/_bulk?refresh=wait_for&pretty" \
  -H "Content-Type: application/x-ndjson" \
  -d '{"update":{"_index":"update_demo","_id":"1","retry_on_conflict":3}}
{"doc":{"price":4888}}
{"update":{"_index":"update_demo","_id":"99"}}
{"doc":{"name":"不存在的商品","price":1},"doc_as_upsert":true}
'
```

### 清理

```bash
curl -u "$ES_USER:$ES_PASS" -X DELETE "$ES_URL/update_demo"
```

---

## 实战（Java SDK）

```java
// ---------- Index（全量覆盖，天然幂等）----------
var indexResp = client.index(i -> i.index("update_demo").id("1")
    .document(Map.of("name", "笔记本", "price", 6999, "stock", 100))
    .refresh(Refresh.WaitFor));
System.out.println("seq_no=" + indexResp.seqNo() + ", primary_term=" + indexResp.primaryTerm());

// ---------- Update（局部更新）----------
client.update(u -> u.index("update_demo").id("1")
    .doc(Map.of("price", 5999))
    .refresh(Refresh.WaitFor), Map.class);

// ---------- doc_as_upsert ----------
client.update(u -> u.index("update_demo").id("2")
    .doc(Map.of("name", "机械键盘", "price", 599))
    .docAsUpsert(true)
    .refresh(Refresh.WaitFor), Map.class);

// ---------- CAS 乐观并发控制 ----------
var getResp = client.get(g -> g.index("update_demo").id("1"), Map.class);
long seqNo = getResp.seqNo();
long primaryTerm = getResp.primaryTerm();

try {
    client.index(i -> i.index("update_demo").id("1")
        .document(Map.of("name", "笔记本", "price", 4999, "stock", 100))
        .ifSeqNo(seqNo)
        .ifPrimaryTerm(primaryTerm)
        .refresh(Refresh.WaitFor));
    System.out.println("CAS 写入成功");
} catch (ElasticsearchException e) {
    if (e.status() == 409) {
        System.out.println("版本冲突: " + e.getMessage());
    }
}

// ---------- retry_on_conflict（计数器累加）----------
client.update(u -> u.index("update_demo").id("counter1")
    .script(s -> s.inline(i -> i
        .source("ctx._source.counter += params.inc")
        .params("inc", JsonData.of(1))))
    .upsert(Map.of("counter", 0))
    .scriptedUpsert(true)
    .retryOnConflict(5)
    .refresh(Refresh.WaitFor), Map.class);

// ---------- 外部版本控制 ----------
client.index(i -> i.index("update_demo").id("ext1")
    .document(Map.of("name", "外部同步", "status", "v10"))
    .version(10L)
    .versionType(VersionType.ExternalGte)
    .refresh(Refresh.WaitFor));

// 幂等重放（相同版本号不冲突）
client.index(i -> i.index("update_demo").id("ext1")
    .document(Map.of("name", "外部同步", "status", "v10-replay"))
    .version(10L)
    .versionType(VersionType.ExternalGte)
    .refresh(Refresh.WaitFor));

// ---------- Bulk 中的 Update ----------
var bulkResp = client.bulk(b -> b.refresh(Refresh.WaitFor)
    .operations(op -> op.update(u -> u
        .index("update_demo").id("1").retryOnConflict(3)
        .action(a -> a.doc(Map.of("price", 4888)))))
    .operations(op -> op.update(u -> u
        .index("update_demo").id("99")
        .action(a -> a
            .doc(Map.of("name", "新商品", "price", 1))
            .docAsUpsert(true)))));

if (bulkResp.errors()) {
    bulkResp.items().stream()
        .filter(item -> item.error() != null)
        .forEach(item -> System.err.println("Failed: " + item.error().reason()));
}

// ---------- 清理 ----------
client.indices().delete(d -> d.index("update_demo"));
```

