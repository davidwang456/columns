# 第05章 Query DSL 入门：查询语义分层

## 背景

关系型数据库使用 SQL 作为查询语言，一条 `SELECT ... WHERE ...` 语句只能回答"是否满足条件"这个二元问题。而在搜索引擎的世界里，我们不仅要判断"是否匹配"，还要回答"匹配程度有多高"——这正是 Elasticsearch Query DSL 的核心价值。

Query DSL（Domain Specific Language）是 Elasticsearch 提供的基于 JSON 的查询语言。它将查询语义划分为多个层次：过滤、检索、排除、加权，通过组合这些层次构建出既高效又精准的搜索请求。理解 Query DSL 的语义分层，是写出高质量查询的前提。

## 本章目标

1. 理解 Query Context 与 Filter Context 的本质区别
2. 掌握常用叶子查询（match、term、range 等）的用法与适用场景
3. 深入理解 Bool 复合查询的四个子句及其组合策略
4. 学会将业务需求拆解为 Query DSL 的方法论
5. 掌握 multi_match 跨字段搜索的使用方式
6. 避免常见的查询误区

---

## 1. Query DSL 核心思想

在 Elasticsearch 中，一次搜索请求不是一条单一语句，而是多个语义层次的组合。一个典型的搜索需求可以拆解为以下几层：

- **过滤层**：硬性约束条件，不参与评分，如"价格在 3000-8000 元之间"
- **检索层**：核心搜索意图，参与评分，如"名称包含笔记本"
- **排除层**：必须排除的结果，如"状态为下架的商品"
- **加权层**：可选的额外加分项，如"知名品牌加分"

Query DSL 通过 `bool` 查询将这些层次组合在一起，每一层使用不同的子句（`filter`、`must`、`must_not`、`should`），最终计算出每个文档的相关性得分 `_score`。

---

## 2. Query Context vs Filter Context

这是理解 Query DSL 最重要的概念之一。

### 2.1 Query Context（查询上下文）

在 Query Context 中，Elasticsearch 不仅判断文档是否匹配，还会计算一个相关性得分（`_score`），回答的是"这个文档有多匹配"。

典型场景：全文检索、模糊匹配。

```json
{
  "query": {
    "match": {
      "title": "Elasticsearch 入门"
    }
  }
}
```

### 2.2 Filter Context（过滤上下文）

在 Filter Context 中，Elasticsearch 只判断文档是否匹配，不计算得分，回答的是"是否匹配"。由于不需要计算 `_score`，Filter Context 的查询结果可以被 Elasticsearch 缓存（放入 Node Query Cache），在重复查询时性能显著更好。

典型场景：精确过滤、范围过滤、状态筛选。

```json
{
  "query": {
    "bool": {
      "filter": [
        { "term": { "status": "published" } },
        { "range": { "price": { "gte": 100, "lte": 500 } } }
      ]
    }
  }
}
```

### 2.3 如何选择

原则很简单：**需要影响排序的条件放 Query Context，不需要影响排序的硬性条件放 Filter Context**。Filter Context 不仅更快（跳过评分计算），还能利用缓存，应尽量多用。

---

## 3. 叶子查询详解

叶子查询（Leaf Query）是 Query DSL 的基本构建单元，直接针对某个字段进行匹配。

### 3.1 match：全文检索

`match` 是最常用的全文检索查询。它会先对查询文本进行分词，然后用分词后的词项去匹配字段。默认情况下，多个词项之间是 OR 关系。

```json
{ "match": { "title": "Elasticsearch 分布式搜索" } }
```

使用 `operator` 参数可以将关系改为 AND，要求所有词项都必须匹配：

```json
{ "match": { "title": { "query": "Elasticsearch 分布式搜索", "operator": "and" } } }
```

### 3.2 match_phrase：短语匹配

`match_phrase` 要求词项在文档中以相同的顺序、相邻的位置出现。`slop` 参数允许词项之间有一定的位置偏移量。

```json
{ "match_phrase": { "title": { "query": "分布式搜索", "slop": 1 } } }
```

`slop: 1` 表示允许词项之间最多间隔一个位置。

### 3.3 term：精确匹配

`term` 查询不会对查询文本进行分词，直接将整个输入作为一个词项去匹配。适用于 `keyword` 类型的字段。

```json
{ "term": { "status": { "value": "published" } } }
```

### 3.4 terms：多值精确匹配

`terms` 是 `term` 的复数形式，允许匹配多个精确值中的任意一个。

```json
{ "terms": { "status": ["published", "draft"] } }
```

### 3.5 range：区间过滤

`range` 查询支持 `gte`（大于等于）、`gt`（大于）、`lte`（小于等于）、`lt`（小于）四个边界参数。

```json
{ "range": { "price": { "gte": 3000, "lte": 8000 } } }
```

对于日期字段，`range` 还支持日期数学表达式：

```json
{ "range": { "created_at": { "gte": "now-7d/d", "lte": "now/d" } } }
```

### 3.6 exists：字段是否存在

```json
{ "exists": { "field": "description" } }
```

### 3.7 wildcard / prefix / regexp：模式匹配

这类查询虽然灵活，但性能开销较大，应谨慎使用。尤其是 `wildcard` 以通配符开头（如 `*abc`）时，需要扫描整个倒排索引。

```json
{ "wildcard": { "sku": { "value": "A1*" } } }
{ "prefix": { "sku": { "value": "A1" } } }
{ "regexp": { "sku": { "value": "A[0-9]+" } } }
```

### 3.8 ids：按文档 ID 查询

```json
{ "ids": { "values": ["doc_1", "doc_2", "doc_3"] } }
```

---

## 4. Bool 复合查询深入

`bool` 查询是 Query DSL 中最重要的复合查询，通过四个子句将多个叶子查询组合起来。

### 4.1 四个子句

| 子句 | 是否必须匹配 | 是否参与评分 | 典型用途 |
|------|-------------|-------------|---------|
| `must` | 是 | 是 | 核心搜索条件 |
| `filter` | 是 | 否（可缓存） | 硬性过滤条件 |
| `should` | 否（可选） | 是（匹配则加分） | 加权、偏好 |
| `must_not` | 必须不匹配 | 否 | 排除条件 |

### 4.2 minimum_should_match

当 `bool` 查询中包含 `should` 子句时，默认行为取决于是否存在 `must` 或 `filter`：

- 存在 `must` 或 `filter`：`should` 中的条件全部是可选的（匹配则加分，不匹配也不影响是否返回）
- 不存在 `must` 和 `filter`：至少需要匹配一个 `should` 条件

可以通过 `minimum_should_match` 参数显式控制：

```json
{
  "bool": {
    "should": [
      { "match": { "title": "Elasticsearch" } },
      { "match": { "title": "搜索引擎" } },
      { "match": { "title": "全文检索" } }
    ],
    "minimum_should_match": 2
  }
}
```

### 4.3 嵌套 Bool 示例

`bool` 查询可以嵌套，用于表达复杂的逻辑关系：

```json
{
  "bool": {
    "must": [
      { "match": { "title": "笔记本" } }
    ],
    "filter": [
      { "term": { "category": "laptop" } },
      {
        "bool": {
          "should": [
            { "range": { "price": { "lte": 5000 } } },
            { "term": { "on_sale": true } }
          ],
          "minimum_should_match": 1
        }
      }
    ]
  }
}
```

上面的查询表示：标题必须包含"笔记本"，类目必须是 laptop，并且价格在 5000 以下或者正在促销（二者满足其一即可）。

---

## 5. 业务查询拆解方法论

面对一个真实的业务搜索需求，推荐按以下步骤拆解为 Query DSL。

### 5.1 场景：电商商品搜索

用户输入"笔记本"，系统还需要：类目为 laptop、价格 3000-8000、有库存、排除已下架商品、知名品牌优先。

### 5.2 拆解过程

1. **filter**（硬性条件，不影响排序）：类目 = laptop，价格 3000-8000，库存 = true
2. **must**（核心检索，参与评分）：name 匹配"笔记本"
3. **should**（可选加权，匹配则加分）：品牌为 Apple/Lenovo 加分，热度高加分
4. **must_not**（排除条件）：status = "下架"

### 5.3 完整查询

```json
{
  "query": {
    "bool": {
      "must": [
        { "match": { "name": "笔记本" } }
      ],
      "filter": [
        { "term": { "category": "laptop" } },
        { "range": { "price": { "gte": 3000, "lte": 8000 } } },
        { "term": { "in_stock": true } }
      ],
      "should": [
        { "terms": { "brand": ["Apple", "Lenovo"], "boost": 2.0 } },
        { "range": { "popularity": { "gte": 80 } } }
      ],
      "must_not": [
        { "term": { "status": "下架" } }
      ]
    }
  }
}
```

这个方法论适用于绝大多数业务搜索场景：先识别硬性约束（filter），再确定核心意图（must），然后加上偏好加权（should）和排除项（must_not）。

---

## 6. multi_match：跨多字段搜索

当同一个搜索词需要在多个字段中查找时，使用 `multi_match` 查询。

### 6.1 基本用法

```json
{
  "multi_match": {
    "query": "Elasticsearch 入门",
    "fields": ["title", "description", "tags"]
  }
}
```

### 6.2 type 参数

`type` 参数控制多字段评分的计算方式：

- **best_fields**（默认）：取所有字段中得分最高的那个。适用于同一概念可能出现在不同字段中的情况。
- **most_fields**：将所有字段的得分求和。适用于同一文本在多个字段中以不同方式索引（如不同分词器）的情况。
- **cross_fields**：将多个字段视为一个大字段。适用于姓+名、街道+城市等拆分在多个字段中的情况。

### 6.3 字段权重

使用 `^` 语法为字段设置不同的权重：

```json
{
  "multi_match": {
    "query": "Elasticsearch",
    "fields": ["title^3", "description^1", "tags^2"],
    "type": "best_fields"
  }
}
```

`title^3` 表示 title 字段的得分乘以 3。

---

## 7. 常见误区

### 7.1 用 term 查询 text 字段

这是最常见的错误。`text` 类型的字段在索引时会被分词，存储的是分词后的词项。而 `term` 查询不会对输入进行分词。假设 title 字段的值是 "Elasticsearch Guide"，经过 standard analyzer 分词后存储为 `["elasticsearch", "guide"]`（小写）。此时用 `term` 查询 `"Elasticsearch Guide"` 不会命中任何结果，因为倒排索引中不存在 "Elasticsearch Guide" 这个完整词项。

正确做法：对 `text` 字段使用 `match` 查询。

### 7.2 用 match 查询 keyword 字段

`keyword` 字段不分词，存储的是完整原始值。使用 `match` 查询虽然能工作（因为 `match` 会对输入分词，如果输入本身只有一个词项，效果等同于 `term`），但语义上不够清晰，也可能因为分词导致意外结果。

正确做法：对 `keyword` 字段使用 `term` 查询。

---

## 8. 总结

- Query DSL 的核心是语义分层：将查询拆解为过滤、检索、排除、加权四个层次
- Query Context 参与评分，Filter Context 不参与评分且可缓存
- `bool` 查询通过 `must`、`filter`、`should`、`must_not` 四个子句实现语义组合
- 叶子查询是构建块：`match` 用于全文检索，`term` 用于精确匹配，`range` 用于区间过滤
- `multi_match` 用于跨多字段搜索，通过 `type` 参数控制评分策略
- 牢记字段类型与查询类型的匹配关系，避免 term 查 text、match 查 keyword 的误区

---

## 9. 练习题

1. Query Context 和 Filter Context 的核心区别是什么？为什么推荐将硬性条件放在 Filter Context 中？
2. `match` 查询和 `term` 查询各自适用于什么类型的字段？如果用 `term` 查询一个 `text` 字段，可能出现什么问题？
3. `bool` 查询中，当同时存在 `must` 和 `should` 时，`should` 子句不匹配的文档还会被返回吗？为什么？
4. `multi_match` 的 `best_fields` 和 `most_fields` 两种类型在什么场景下分别更合适？
5. 设计一个电商搜索的 Query DSL：用户搜索"无线耳机"，要求品牌为 Sony 或 Bose，价格 200-1000 元，排除缺货商品，优先展示好评率高的商品。

---

## 10. 实战（curl）

### 10.1 创建测试索引并写入数据

```bash
curl -u "$ES_USER:$ES_PASS" -X PUT "$ES_URL/products" \
  -H 'Content-Type: application/json' -d '
{
  "mappings": {
    "properties": {
      "name":       { "type": "text", "analyzer": "standard" },
      "category":   { "type": "keyword" },
      "brand":      { "type": "keyword" },
      "price":      { "type": "float" },
      "popularity": { "type": "integer" },
      "in_stock":   { "type": "boolean" },
      "status":     { "type": "keyword" },
      "description":{ "type": "text" }
    }
  }
}'

curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/products/_bulk?refresh=true" \
  -H 'Content-Type: application/json' -d '
{"index":{"_id":"1"}}
{"name":"轻薄笔记本电脑","category":"laptop","brand":"Apple","price":6999,"popularity":95,"in_stock":true,"status":"在售","description":"高性能轻薄笔记本"}
{"index":{"_id":"2"}}
{"name":"游戏笔记本电脑","category":"laptop","brand":"Lenovo","price":7599,"popularity":88,"in_stock":true,"status":"在售","description":"专业游戏笔记本"}
{"index":{"_id":"3"}}
{"name":"商务笔记本","category":"laptop","brand":"Dell","price":4599,"popularity":70,"in_stock":false,"status":"在售","description":"轻薄商务办公本"}
{"index":{"_id":"4"}}
{"name":"学生平板电脑","category":"tablet","brand":"Huawei","price":2999,"popularity":60,"in_stock":true,"status":"下架","description":"学生学习平板"}
'
```

### 10.2 Bool 复合查询

```bash
curl -u "$ES_USER:$ES_PASS" -X GET "$ES_URL/products/_search?pretty" \
  -H 'Content-Type: application/json' -d '
{
  "query": {
    "bool": {
      "must": [
        { "match": { "name": "笔记本" } }
      ],
      "filter": [
        { "term": { "category": "laptop" } },
        { "range": { "price": { "gte": 3000, "lte": 8000 } } },
        { "term": { "in_stock": true } }
      ],
      "should": [
        { "term": { "brand": { "value": "Apple", "boost": 2.0 } } }
      ],
      "must_not": [
        { "term": { "status": "下架" } }
      ]
    }
  }
}'
```

### 10.3 multi_match 查询

```bash
curl -u "$ES_USER:$ES_PASS" -X GET "$ES_URL/products/_search?pretty" \
  -H 'Content-Type: application/json' -d '
{
  "query": {
    "multi_match": {
      "query": "轻薄笔记本",
      "fields": ["name^3", "description"],
      "type": "best_fields"
    }
  }
}'
```

### 10.4 match_phrase 查询

```bash
curl -u "$ES_USER:$ES_PASS" -X GET "$ES_URL/products/_search?pretty" \
  -H 'Content-Type: application/json' -d '
{
  "query": {
    "match_phrase": {
      "name": {
        "query": "笔记本电脑",
        "slop": 1
      }
    }
  }
}'
```

### 10.5 range 查询

```bash
curl -u "$ES_USER:$ES_PASS" -X GET "$ES_URL/products/_search?pretty" \
  -H 'Content-Type: application/json' -d '
{
  "query": {
    "range": {
      "price": {
        "gte": 4000,
        "lte": 7000
      }
    }
  }
}'
```

---

## 11. 实战（Java SDK）

```java
import co.elastic.clients.elasticsearch.ElasticsearchClient;
import co.elastic.clients.elasticsearch._types.query_dsl.*;
import co.elastic.clients.elasticsearch.core.SearchRequest;
import co.elastic.clients.elasticsearch.core.SearchResponse;

// Bool 复合查询
SearchResponse<Map> boolResponse = client.search(s -> s
    .index("products")
    .query(q -> q
        .bool(b -> b
            .must(m -> m.match(mt -> mt.field("name").query("笔记本")))
            .filter(f -> f.term(t -> t.field("category").value("laptop")))
            .filter(f -> f.range(r -> r.number(n -> n
                .field("price").gte(3000.0).lte(8000.0))))
            .filter(f -> f.term(t -> t.field("in_stock").value(true)))
            .should(sh -> sh.term(t -> t.field("brand").value("Apple").boost(2.0f)))
            .mustNot(mn -> mn.term(t -> t.field("status").value("下架")))
        )
    ),
    Map.class
);

for (var hit : boolResponse.hits().hits()) {
    System.out.println("id=" + hit.id() + " score=" + hit.score() + " source=" + hit.source());
}

// multi_match 查询
SearchResponse<Map> multiMatchResponse = client.search(s -> s
    .index("products")
    .query(q -> q
        .multiMatch(mm -> mm
            .query("轻薄笔记本")
            .fields("name^3", "description")
            .type(TextQueryType.BestFields)
        )
    ),
    Map.class
);

// match_phrase 查询
SearchResponse<Map> phraseResponse = client.search(s -> s
    .index("products")
    .query(q -> q
        .matchPhrase(mp -> mp
            .field("name")
            .query("笔记本电脑")
            .slop(1)
        )
    ),
    Map.class
);
```
