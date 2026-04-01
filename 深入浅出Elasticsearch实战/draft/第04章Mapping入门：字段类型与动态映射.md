# 第04章 Mapping 入门：字段类型与动态映射

# 背景

很多检索效果问题，表面看是"查询写得不对"，本质却是"字段类型建错了"。Mapping 是 Elasticsearch 的数据契约，等同于关系数据库中的 Schema。一旦设计错误，修复成本往往远高于最初多花的建模时间——改字段类型意味着重建索引、迁移数据、切换别名，整个流程既慢又有风险。

## 本章目标

- 掌握核心字段类型与其查询能力的对应关系。
- 理解动态映射的推断规则及其风险。
- 学会用 Dynamic Templates 和 strict 模式防止映射膨胀。
- 明确 Mapping 不可变性及其应对策略。

---

## 1. Mapping 是什么

Mapping 定义了索引中每个字段的类型、分词方式、是否索引等属性。它直接决定三件事：

- **能不能搜到**：text 类型经过分词后才能做全文检索，keyword 类型只能精确匹配。
- **能不能聚合/排序**：只有启用 doc_values 的字段（keyword、数值、日期等）才能高效聚合和排序。
- **存储效率**：选错类型会浪费磁盘空间，也会拖慢写入和查询。

把 Mapping 理解为 ES 的 `CREATE TABLE` 语句即可：表结构一旦建好，改列类型需要重建整张表。

---

## 2. 核心字段类型详解

### 2.1 字符串类型

**text** —— 写入时经过 Analyzer 分词，生成倒排索引中的多个词项（term）。支持 match、match_phrase 等全文检索查询；不支持直接排序和聚合（除非开启 fielddata，生产环境极不推荐）。适用于商品描述、文章正文、用户评论等长文本。

**keyword** —— 整个字段值作为一个词项存入倒排索引，不做任何分词。支持 term 精确匹配、排序、聚合。可通过 `ignore_above` 限制最大长度，超出部分不被索引。适用于 ID、状态码、标签、手机号等需要精确过滤或分桶统计的字段。

**text + keyword multi-fields 组合** —— 同一个字符串字段既要全文检索又要排序/聚合时，使用 multi-fields 是最常见的做法。查询时用 `title` 做全文检索，用 `title.keyword` 做排序和聚合。ES 动态映射在遇到字符串时默认就采用这种组合。

```json
{
  "title": {
    "type": "text",
    "fields": { "keyword": { "type": "keyword", "ignore_above": 256 } }
  }
}
```

### 2.2 数值类型


| 类型                            | 大小                       | 典型场景             |
| ----------------------------- | ------------------------ | ---------------- |
| byte / short / integer / long | 1 / 2 / 4 / 8 字节         | 枚举、年龄、通用整数、大 ID  |
| float / double / half_float   | 4 / 8 / 2 字节             | 科学计算、高精度浮点、低精度场景 |
| scaled_float                  | 8 字节（需指定 scaling_factor） | 金额、汇率            |


**scaled_float 适合金额场景**：通过 `scaling_factor` 将小数转为 long 存储。例如 `scaling_factor: 100` 时，`19.99` 内部存储为 `1999`，避免浮点精度丢失，保留 range 查询和聚合能力。选型原则：在满足精度需求的前提下选最小类型，节省磁盘和内存。

```json
{ "price": { "type": "scaled_float", "scaling_factor": 100 } }
```

### 2.3 日期类型 date

- **默认格式**：`strict_date_optional_time||epoch_millis`，同时接受 ISO 8601 字符串和毫秒时间戳。
- **内部存储**：无论输入什么格式，ES 统一转为 UTC 时区的 long 型毫秒值存储。
- **自定义 format**：可指定多种格式，用 `||` 分隔。

```json
{ "created_at": { "type": "date", "format": "yyyy-MM-dd HH:mm:ss||epoch_millis" } }
```

### 2.4 布尔类型 boolean

接受 `true`/`false`（JSON 布尔值）及字符串 `"true"`/`"false"`。用于状态标记和过滤。

### 2.5 二进制类型 binary

存储 Base64 编码的二进制数据。默认不索引、不可搜索，仅保存在 `_source` 中。适合小型附件或缩略图。

### 2.6 复杂类型

**object（默认）** —— JSON 对象会被扁平化存储。当字段值是对象数组时，object 会丢失数组元素之间的字段关联性。例如 `[{"name":"A","age":30},{"name":"B","age":25}]` 被展平为 `name=["A","B"]`、`age=[30,25]`，查询"name=A 且 age=25"会错误命中。

**nested** —— 保持数组中每个对象的字段关联性，每个嵌套对象在内部被索引为独立的隐藏文档。性能开销大于 object，仅在需要关联查询时使用。第 11 章会深入讲解。

**flattened** —— 将整个 JSON 对象作为一组 keyword 处理，不为子字段单独建立映射。适合字段名不可预测的场景（如用户自定义标签、Kubernetes labels），防止映射爆炸。代价是只能做 keyword 级别的查询。

### 2.7 专用类型


| 类型                         | 用途           | 典型场景         |
| -------------------------- | ------------ | ------------ |
| geo_point                  | 经纬度坐标        | 附近的人/店、距离排序  |
| geo_shape                  | 复杂地理形状       | 判断点是否在配送区域内  |
| ip                         | IPv4/IPv6 地址 | IP 范围过滤、安全分析 |
| date_range / integer_range | 范围值          | 预约时段、价格区间    |
| token_count                | 字符串的词项计数     | 按文章字数过滤      |
| completion                 | 自动补全（FST 结构） | 搜索建议、前缀联想    |


---

## 3. 动态映射（Dynamic Mapping）

### 3.1 自动类型推断规则

当写入文档包含 Mapping 中未定义的字段时，ES 根据 JSON 值自动推断类型：


| JSON 值           | 推断的 ES 类型                    | 说明           |
| ---------------- | ---------------------------- | ------------ |
| `"hello"`        | text + keyword（multi-fields） | 字符串默认同时建两种索引 |
| `123`            | long                         | 整数统一推断为 long |
| `1.5`            | float                        | 浮点数推断为 float |
| `true` / `false` | boolean                      | 布尔值          |
| `{"a": 1}`       | object                       | 嵌套 JSON 对象   |
| `["a", "b"]`     | 取第一个元素的类型                    | 数组本身不是独立类型   |
| `"2025-01-01"`   | date                         | 符合日期格式的字符串   |


常见陷阱：数字型字符串 `"12345"` 会被推断为 text + keyword 而非 long，后续无法做 range 查询。

### 3.2 四种 dynamic 模式


| 模式         | 行为                          | 适用场景             |
| ---------- | --------------------------- | ---------------- |
| `true`（默认） | 自动添加新字段到 Mapping 并索引        | 开发/探索阶段          |
| `runtime`  | 新字段作为 runtime field，不写入倒排索引 | 灵活查询但不膨胀 Mapping |
| `false`    | 不索引新字段，但数据保留在 `_source` 中   | 允许存在但不需要检索       |
| `strict`   | 拒绝包含未知字段的文档，直接返回错误          | 生产环境推荐           |


**生产环境推荐 strict 模式**。它强制所有字段先定义后使用，从源头阻止映射膨胀和类型误判。开发阶段可用 true 快速迭代，上线前切换为 strict。

---

## 4. Dynamic Templates

Dynamic Templates 允许在动态映射阶段根据字段名模式或 JSON 类型自定义映射规则。

### 4.1 按字段名匹配

所有以 `_id` 结尾的字段自动设为 keyword，以 `_text` 结尾的设为 text：

```json
{
  "dynamic_templates": [
    { "ids_as_keyword": { "match": "*_id", "mapping": { "type": "keyword" } } },
    { "text_fields":    { "match": "*_text", "mapping": { "type": "text" } } }
  ]
}
```

### 4.2 按 JSON 类型匹配

将所有动态字符串字段默认映射为 keyword（而非 text + keyword），可显著减少索引体积：

```json
{
  "dynamic_templates": [
    { "strings_as_kw": { "match_mapping_type": "string", "mapping": { "type": "keyword" } } }
  ]
}
```

模板支持 `match`、`unmatch`、`match_mapping_type`、`path_match` 等条件组合，按数组顺序匹配，第一个命中的规则生效。

---

## 5. Mapping 不可变性

### 5.1 为什么不能改字段类型

字段类型决定了倒排索引的物理存储结构。text 存储分词后的词项，keyword 存储完整字符串，long 使用 BKD 树存储数值——这些数据结构完全不同，无法原地转换。

### 5.2 可以做什么

- **新增字段**：随时通过 PUT Mapping API 添加。
- **新增 multi-fields**：为已有字段添加子字段。
- **修改部分参数**：如 `ignore_above` 等不影响索引结构的参数。

### 5.3 修改类型的唯一方法

1. 创建新索引，定义正确的 Mapping。
2. 使用 `_reindex` API 将数据迁移到新索引。
3. 通过 Alias（别名）无感切换读写流量。

详见第 15 章（别名与零停机重建）。

---

## 6. 实践建议

1. **核心字段必须显式 Mapping**：不要依赖动态映射决定关键字段类型。
2. **用 Index Template 保证一致性**：将 Mapping 放入模板，确保开发/测试/生产环境统一。
3. **控制字段数量上限**：`index.mapping.total_fields.limit` 默认 1000，接近上限时考虑 flattened 或拆分索引。
4. **字符串优先考虑 keyword**：除非确定需要全文检索，否则默认用 keyword。
5. **金额用 scaled_float，时间用 date**：避免用字符串存储数值或日期。

---

## 7. 综合建模示例：电商商品索引

```json
PUT /products
{
  "settings": { "number_of_shards": 3, "number_of_replicas": 1 },
  "mappings": {
    "dynamic": "strict",
    "dynamic_templates": [
      { "ids_as_keyword": { "match": "*_id", "mapping": { "type": "keyword" } } }
    ],
    "properties": {
      "product_id":  { "type": "keyword" },
      "title":       { "type": "text", "fields": { "keyword": { "type": "keyword", "ignore_above": 128 } } },
      "description": { "type": "text" },
      "category":    { "type": "keyword" },
      "brand":       { "type": "keyword" },
      "tags":        { "type": "keyword" },
      "price":       { "type": "scaled_float", "scaling_factor": 100 },
      "stock":       { "type": "integer" },
      "is_on_sale":  { "type": "boolean" },
      "created_at":  { "type": "date", "format": "yyyy-MM-dd HH:mm:ss||epoch_millis" },
      "updated_at":  { "type": "date", "format": "yyyy-MM-dd HH:mm:ss||epoch_millis" },
      "location":    { "type": "geo_point" },
      "specs": {
        "type": "nested",
        "properties": {
          "name":  { "type": "keyword" },
          "value": { "type": "keyword" }
        }
      }
    }
  }
}
```

设计要点：

- `dynamic: strict` 防止意外字段写入。
- `title` 使用 text + keyword multi-fields，兼顾搜索和排序。
- `price` 使用 scaled_float 避免浮点精度问题。
- `specs`（规格参数）使用 nested 类型，保证"颜色=红色且尺码=L"不会错误匹配。
- `tags` 写入时可能是数组 `["new","hot"]`，ES 中数组不需要特殊类型，keyword 即可。

---

# 总结

- Mapping 是 ES 的数据契约，字段类型决定了查询能力、聚合方式和存储效率。
- text 用于全文检索，keyword 用于精确匹配/排序/聚合，二者通过 multi-fields 组合是最常见的字符串建模方式。
- 动态映射方便但危险，生产环境应使用 strict 模式，结合 Dynamic Templates 控制推断行为。
- Mapping 一旦建立不可修改字段类型，修正的唯一途径是新建索引 + reindex + alias 切换。
- 核心字段必须显式定义，用 Index Template 保证环境一致性。

---

## 练习题

1. 为一个"订单"文档设计 Mapping，要求支持：订单号精确查询、商品名称全文检索、金额范围过滤、下单时间排序、收货地址地理距离计算。
2. 分别将 `title` 定义为 `text` 和 `keyword`，写入相同数据后用 `match` 和 `term` 查询，对比差异并解释原因。
3. 使用 Dynamic Templates 实现：所有 `*_id` 字段自动设为 keyword，所有 `*_content` 字段自动设为 text。
4. 在 `dynamic: strict` 的索引上尝试写入未定义字段，观察错误信息。然后改为 `dynamic: false` 再写入，对比 `_source` 和 `_search` 的差异。
5. 使用 `_analyze` API 分别对 standard 和 keyword analyzer 分析 `"Elasticsearch is powerful"`，比较分词结果。

---

## 实战（curl）

```bash
# 1) 创建索引：显式 Mapping + Dynamic Templates + strict 模式
curl -u "$ES_USER:$ES_PASS" -X PUT "$ES_URL/mapping_demo" \
  -H "Content-Type: application/json" \
  -d '{
    "mappings": {
      "dynamic": "strict",
      "dynamic_templates": [
        { "ids_as_keyword": { "match": "*_id", "mapping": { "type": "keyword" } } }
      ],
      "properties": {
        "product_id": { "type": "keyword" },
        "title": { "type": "text", "fields": { "keyword": { "type": "keyword", "ignore_above": 128 } } },
        "price": { "type": "scaled_float", "scaling_factor": 100 },
        "is_active": { "type": "boolean" },
        "created_at": { "type": "date", "format": "yyyy-MM-dd HH:mm:ss||epoch_millis" },
        "tags": { "type": "keyword" }
      }
    }
  }'

# 2) 查看 Mapping
curl -u "$ES_USER:$ES_PASS" "$ES_URL/mapping_demo/_mapping?pretty"

# 3) 写入文档
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/mapping_demo/_doc/1" \
  -H "Content-Type: application/json" \
  -d '{
    "product_id": "P1001",
    "title": "Elasticsearch in Action",
    "price": 59.99,
    "is_active": true,
    "created_at": "2025-06-01 10:00:00",
    "tags": ["search", "database"]
  }'

# 4) strict 模式验证：未定义字段会被拒绝
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/mapping_demo/_doc/2" \
  -H "Content-Type: application/json" \
  -d '{ "product_id": "P1002", "title": "Test", "unknown_field": "rejected" }'

# 5) _analyze 观察分词差异
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/_analyze?pretty" \
  -H "Content-Type: application/json" \
  -d '{ "analyzer": "standard", "text": "Elasticsearch is powerful" }'

curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/_analyze?pretty" \
  -H "Content-Type: application/json" \
  -d '{ "analyzer": "keyword", "text": "Elasticsearch is powerful" }'

# 6) text vs keyword 查询对比
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/mapping_demo/_search?pretty" \
  -H "Content-Type: application/json" \
  -d '{ "query": { "match": { "title": "elasticsearch" } } }'

curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/mapping_demo/_search?pretty" \
  -H "Content-Type: application/json" \
  -d '{ "query": { "term": { "title.keyword": "Elasticsearch in Action" } } }'

# term 查 text 字段通常无法命中（分词后原始值不存在）
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/mapping_demo/_search?pretty" \
  -H "Content-Type: application/json" \
  -d '{ "query": { "term": { "title": "Elasticsearch in Action" } } }'

# 7) 动态映射实验
curl -u "$ES_USER:$ES_PASS" -X PUT "$ES_URL/dynamic_true_demo" \
  -H "Content-Type: application/json" \
  -d '{ "mappings": { "dynamic": true } }'

curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/dynamic_true_demo/_doc/1" \
  -H "Content-Type: application/json" \
  -d '{ "name": "test", "age": 30, "score": 9.5, "active": true }'

curl -u "$ES_USER:$ES_PASS" "$ES_URL/dynamic_true_demo/_mapping?pretty"

# 8) 清理
curl -u "$ES_USER:$ES_PASS" -X DELETE "$ES_URL/mapping_demo"
curl -u "$ES_USER:$ES_PASS" -X DELETE "$ES_URL/dynamic_true_demo"
```

---

## 实战（Java SDK）

```java
import co.elastic.clients.elasticsearch.ElasticsearchClient;
import co.elastic.clients.elasticsearch.indices.GetMappingResponse;
import co.elastic.clients.elasticsearch.indices.get_mapping.IndexMappingRecord;
import co.elastic.clients.elasticsearch._types.mapping.*;

import java.util.Map;

public class MappingDemo {

    private final ElasticsearchClient client;

    public MappingDemo(ElasticsearchClient client) {
        this.client = client;
    }

    public void createIndexWithMapping() throws Exception {
        client.indices().create(c -> c
            .index("mapping_demo")
            .mappings(m -> m
                .dynamic(DynamicMapping.Strict)
                .properties("product_id", p -> p.keyword(k -> k))
                .properties("title", p -> p.text(t -> t
                    .analyzer("standard")
                    .fields("keyword", f -> f.keyword(k -> k.ignoreAbove(128)))
                ))
                .properties("price", p -> p.scaledFloat(sf -> sf.scalingFactor(100.0)))
                .properties("is_active", p -> p.boolean_(b -> b))
                .properties("created_at", p -> p.date(d -> d
                    .format("yyyy-MM-dd HH:mm:ss||epoch_millis")
                ))
                .properties("tags", p -> p.keyword(k -> k))
            )
        );
    }

    public void getMapping() throws Exception {
        GetMappingResponse response = client.indices().getMapping(g -> g.index("mapping_demo"));
        for (Map.Entry<String, IndexMappingRecord> entry : response.result().entrySet()) {
            System.out.println("Index: " + entry.getKey());
            entry.getValue().mappings().properties().forEach((field, prop) ->
                System.out.println("  " + field + " -> " + prop._kind())
            );
        }
    }

    public void indexDocument() throws Exception {
        var doc = Map.of(
            "product_id", "P1001", "title", "Elasticsearch in Action",
            "price", 59.99, "is_active", true,
            "created_at", "2025-06-01 10:00:00",
            "tags", java.util.List.of("search", "database")
        );
        client.index(i -> i.index("mapping_demo").id("1").document(doc));
    }

    public void analyzeText() throws Exception {
        var stdResponse = client.indices().analyze(a -> a
            .analyzer("standard").text("Elasticsearch is powerful"));
        System.out.println("Standard tokens:");
        stdResponse.tokens().forEach(t -> System.out.println("  " + t.token()));

        var kwResponse = client.indices().analyze(a -> a
            .analyzer("keyword").text("Elasticsearch is powerful"));
        System.out.println("Keyword tokens:");
        kwResponse.tokens().forEach(t -> System.out.println("  " + t.token()));
    }

    public void cleanup() throws Exception {
        client.indices().delete(d -> d.index("mapping_demo"));
    }
}
```

