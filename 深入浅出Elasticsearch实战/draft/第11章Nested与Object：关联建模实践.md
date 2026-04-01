

# 第11章 Nested 与 Object：关联建模实践

# 背景

数组对象查询"命中结果不符合直觉"是 ES 建模高频坑。object 与 nested 的差异不在语法，而在数据关联语义是否被正确保留。本章从内部原理出发，通过完整的可执行示例，带你把这个问题彻底弄清楚。

## 本章目标

- 理解 object 与 nested 的语义差异及其内部实现原理。
- 复现并避免数组对象查询中的"错配命中"问题。
- 掌握 nested query、inner_hits、nested aggregation 与 nested sort 的使用。
- 了解 nested 的性能代价与集群级保护参数。
- 能够根据业务场景做出 object / nested / 反范式 的正确选择。

---

## 1. object 类型：扁平化的代价

### 1.1 内部存储原理

Elasticsearch 没有真正的"内部对象"概念。当你用默认的 `object` 类型存储一个对象数组时，ES 会把它**扁平化**为多个多值字段。

以电商订单中的"商品明细"为例：

```json
{
  "order_id": "O1001",
  "items": [
    { "sku": "A", "price": 10 },
    { "sku": "B", "price": 20 }
  ]
}
```

ES 内部实际存储的是：

```json
{
  "order_id": "O1001",
  "items.sku":   ["A", "B"],
  "items.price": [10, 20]
}
```

`sku=A` 和 `price=10` 的对应关系被丢失了。

### 1.2 "错配命中"问题复现

查询"sku 为 A 且 price >= 20 的订单"：

```json
{
  "query": {
    "bool": {
      "must": [
        { "term": { "items.sku": "A" } },
        { "range": { "items.price": { "gte": 20 } } }
      ]
    }
  }
}
```

实际上不存在 `sku=A, price>=20` 的明细行，但因为扁平化后 `items.sku` 包含 A、`items.price` 包含 20，这条文档会被**错误命中**。

### 1.3 object 的适用场景

object 并非"错误"选项，在以下场景仍然是首选：

- **单一对象**（非数组）：`manager: { name: "...", age: 30 }` 不存在错配问题。
- **查询不涉及跨字段关联**：只按 `items.sku` 做过滤、不与同对象的 `items.price` 联合查询。
- **高写入吞吐、低精度容忍**：日志、埋点类数据，更看重写入速度而非精确匹配。

---

## 2. nested 类型：保留对象边界

### 2.1 内部实现原理

`nested` 是 `object` 的特殊版本。当字段声明为 `nested` 后，数组中的每个对象会被索引为**独立的隐藏 Lucene 文档**。

以上面的订单为例，如果 `items` 声明为 `nested`，则一条订单文档实际会产生 **3 个 Lucene 文档**：


| 文档     | 内容                                |
| ------ | --------------------------------- |
| 隐藏文档 1 | `items.sku=A, items.price=10`     |
| 隐藏文档 2 | `items.sku=B, items.price=20`     |
| 父文档    | `order_id=O1001`（+ 指向上面两个隐藏文档的关系） |


每个 nested 对象的字段值被隔离存储，查询时通过 Block Join 在 Lucene 内部关联父子文档，从而保证**对象级别的精确匹配**。

### 2.2 nested 查询

nested 文档必须使用 `nested` query 访问：

```json
{
  "query": {
    "nested": {
      "path": "items",
      "query": {
        "bool": {
          "must": [
            { "term": { "items.sku": "A" } },
            { "range": { "items.price": { "gte": 20 } } }
          ]
        }
      }
    }
  }
}
```

此查询不会命中上面的订单——因为没有任何单个 nested 对象同时满足 `sku=A` 且 `price>=20`。

### 2.3 score_mode：多个 nested 对象如何影响评分

当父文档包含多个 nested 对象、且其中若干对象匹配查询时，`score_mode` 控制如何将它们的分数合并到父文档：


| score_mode | 含义                    |
| ---------- | --------------------- |
| `avg`（默认）  | 取所有匹配 nested 对象得分的平均值 |
| `max`      | 取最高分                  |
| `min`      | 取最低分                  |
| `sum`      | 求和                    |
| `none`     | 不评分，相当于 filter 语义     |


```json
{
  "query": {
    "nested": {
      "path": "items",
      "score_mode": "max",
      "query": {
        "range": { "items.price": { "gte": 15 } }
      }
    }
  }
}
```

---

## 3. inner_hits：返回命中的 nested 对象

默认的 `_source` 返回的是完整的父文档（包含所有 nested 对象）。要知道"到底是哪个 nested 对象命中了"，需要使用 `inner_hits`：

```json
{
  "query": {
    "nested": {
      "path": "items",
      "query": {
        "term": { "items.sku": "B" }
      },
      "inner_hits": {
        "_source": ["items.sku", "items.price"],
        "size": 3,
        "highlight": {
          "fields": { "items.sku": {} }
        }
      }
    }
  }
}
```

`inner_hits` 会在命中结果中额外返回匹配的 nested 对象列表，支持独立的 `_source` 过滤、`size`、`sort`、`highlight` 等参数。在以下场景非常实用：

- 订单中展示"命中的是哪个商品行"。
- 简历搜索中高亮"匹配的是哪段工作经历"。

---

## 4. nested 聚合与 reverse_nested

### 4.1 nested aggregation

普通聚合无法直接访问 nested 文档内部字段。需要先用 `nested` 聚合"进入"nested 上下文：

```json
{
  "size": 0,
  "aggs": {
    "item_agg": {
      "nested": { "path": "items" },
      "aggs": {
        "avg_price": { "avg": { "field": "items.price" } },
        "sku_terms": { "terms": { "field": "items.sku" } }
      }
    }
  }
}
```

该查询统计所有 nested 明细行的平均价格和 SKU 分布。

### 4.2 reverse_nested：从 nested 上下文回到父文档

某些场景需要在 nested 聚合内部关联父文档字段。例如"按 SKU 聚合后，统计每个 SKU 关联了多少个不同的订单"：

```json
{
  "size": 0,
  "aggs": {
    "item_agg": {
      "nested": { "path": "items" },
      "aggs": {
        "by_sku": {
          "terms": { "field": "items.sku" },
          "aggs": {
            "back_to_order": {
              "reverse_nested": {},
              "aggs": {
                "order_count": {
                  "cardinality": { "field": "order_id" }
                }
              }
            }
          }
        }
      }
    }
  }
}
```

`reverse_nested` 将聚合上下文从 nested 层"跳回"到父文档层。

---

## 5. nested 排序

需要按 nested 对象的字段排序时，必须在 `sort` 中声明 `nested` 上下文：

```json
{
  "sort": [
    {
      "items.price": {
        "order": "asc",
        "mode": "min",
        "nested": {
          "path": "items",
          "filter": {
            "term": { "items.sku": "A" }
          }
        }
      }
    }
  ]
}
```

- `mode`：当一个父文档有多个 nested 对象时，`min` / `max` / `avg` / `sum` / `median` 决定取哪个值参与排序。
- `nested.filter`：可选，只考虑满足条件的 nested 对象参与排序。

---

## 6. nested 的性能代价与防护参数

### 6.1 存储膨胀

每个 nested 对象 = 1 个独立 Lucene 文档。一条包含 100 个 nested 对象的文档，在 Lucene 层面实际是 **101 个文档**（100 个 nested + 1 个父文档）。这意味着：

- **索引体积膨胀**：segment 中的文档数远大于业务文档数。
- **写入成本增加**：每次更新父文档，所有 nested 对象都要一起删除并重建。
- **查询成本增加**：nested query 需要执行 Block Join，比普通查询多一层关联。

### 6.2 更新代价

nested 文档的更新是**整体替换**——即使只修改了一个 nested 对象，整个父文档（包括所有 nested 对象）都需要删除并重新索引。如果一条文档有 1000 个 nested 对象，更新任意一个都意味着重写 1001 个 Lucene 文档。

对于频繁更新 nested 对象的场景，建议：

- 减少单个文档的 nested 对象数量（拆分到多条文档）。
- 考虑用反范式冗余替代 nested。

### 6.3 集群级保护参数

Elasticsearch 提供了三个索引级参数来防止 nested 滥用导致的性能问题：


| 参数                                   | 默认值   | 含义                              |
| ------------------------------------ | ----- | ------------------------------- |
| `index.mapping.nested_fields.limit`  | 100   | 一个索引中最多定义多少个不同的 nested 字段类型     |
| `index.mapping.nested_objects.limit` | 10000 | 单个文档中所有 nested 类型的对象总数上限        |
| `index.mapping.nested_parents.limit` | 50    | nested 字段作为其他 nested 字段的父级的最大数量 |


一般建议：

- 保持默认值，如果需要调大，先审视建模是否合理。
- 单个文档的 nested 对象数建议控制在**几十到几百**，超过千级别需要重新考虑建模。

---

## 7. include_in_parent 与 include_in_root

这两个参数提供了一种"既要又要"的折中：

```json
{
  "mappings": {
    "properties": {
      "items": {
        "type": "nested",
        "include_in_parent": true,
        "properties": {
          "sku": { "type": "keyword" },
          "price": { "type": "double" }
        }
      }
    }
  }
}
```

- `include_in_parent: true`：nested 对象的字段会**同时**以扁平化的方式添加到直接父文档中。
- `include_in_root: true`：nested 对象的字段会同时添加到根文档中（多层嵌套时有区别）。

效果：你可以同时使用普通查询（不要求对象级精确匹配）和 nested 查询（要求精确匹配）。代价是索引体积进一步膨胀。

适用场景举例：

- 大部分查询只按单个字段过滤（用普通查询即可），少部分查询需要对象级精确匹配（用 nested 查询）。
- 不想为两种查询模式维护两套索引。

---

## 8. 如何选择：决策框架

```
是否为对象数组？
├── 否（单一对象） → object（默认即可）
└── 是
    ├── 查询是否需要跨字段关联匹配？
    │   ├── 否 → object（性能最优）
    │   └── 是
    │       ├── nested 对象数量是否可控（< 几百）？
    │       │   ├── 是 → nested
    │       │   └── 否 → 考虑拆分文档或反范式
    │       └── 更新是否频繁？
    │           ├── 否 → nested
    │           └── 是 → 反范式冗余 / 应用层 Join
```

### 决策要素速查


| 维度        | object | nested         | 反范式冗余    |
| --------- | ------ | -------------- | -------- |
| 查询精确性     | 可能错配   | 精确             | 精确       |
| 写入性能      | 最优     | 有膨胀            | 看冗余程度    |
| 更新成本      | 低      | 高（整体重写）        | 中（需同步冗余） |
| 聚合能力      | 普通聚合   | 需 nested agg   | 普通聚合     |
| 查询复杂度     | 简单     | 需 nested query | 简单       |
| Kibana 支持 | 完整     | 有限（Lens 不支持）   | 完整       |


---

## 9. 替代思路

### 9.1 反范式（Denormalization）

把 nested 对象的关键字段冗余到父文档：

```json
{
  "order_id": "O1001",
  "item_skus": ["A", "B"],
  "item_a_price": 10,
  "item_b_price": 20
}
```

适合字段少、对象数有限的场景。牺牲存储换取查询简单和更好的性能。

### 9.2 应用层 Join

主数据存在关系数据库中，ES 只做检索视图。查询流程：

1. 在 ES 中搜索并拿到文档 ID。
2. 回主库查完整的关联数据。

适合关联关系复杂、更新频繁、ES 只承担搜索职责的场景。

### 9.3 flattened 类型

当 nested 对象的 key 是动态的（如标签、自定义属性），且只需要简单的 term 级别查询时，`flattened` 类型比 nested 更轻量：

```json
{
  "mappings": {
    "properties": {
      "labels": { "type": "flattened" }
    }
  }
}
```

局限：不支持 range 查询、不支持聚合中的数值运算。

---

# 总结

- object 是默认选项，适合单一对象或不要求跨字段关联的数组。
- nested 不是默认选项，而是"准确性优先"选项——它通过独立 Lucene 文档保留对象边界。
- nested 的性能代价体现在存储膨胀、更新成本和查询复杂度上，建模时要先算清楚收益与成本。
- `inner_hits` 是 nested 查询的重要搭档，用于定位具体命中了哪个子对象。
- `nested` / `reverse_nested` 聚合是在 nested 文档上做分析的唯一路径。
- 当 nested 对象数量过大或更新过于频繁时，反范式冗余或应用层 Join 往往是更务实的选择。

---

## 练习题

1. 用 object 和 nested 各建一版订单明细索引，分别插入 `[{sku:A, price:10}, {sku:B, price:20}]`，查询 `sku=A AND price>=20`，对比两者的命中差异。
2. 在 nested 索引上使用 `inner_hits`，返回具体命中的明细行。
3. 使用 `nested` 聚合统计所有 SKU 的平均价格，再用 `reverse_nested` 统计每个 SKU 关联的订单数。
4. 插入一条包含 50 个 nested 对象的文档，使用 `_cat/indices?v` 观察文档数（docs.count）与业务文档数的差异。
5. 给出你在业务中选 nested 的判断标准（写下决策依据）。

---

## 实战（curl）

### 准备：同时创建 object 版和 nested 版索引

```bash
# object 版索引
curl -u "$ES_USER:$ES_PASS" -X PUT "$ES_URL/order_object" \
  -H "Content-Type: application/json" \
  -d '{
    "mappings": { "properties": {
      "order_id": { "type": "keyword" },
      "items": { "properties": {
        "sku":   { "type": "keyword" },
        "price": { "type": "double" }
      }}
    }}
  }'

# nested 版索引
curl -u "$ES_USER:$ES_PASS" -X PUT "$ES_URL/order_nested" \
  -H "Content-Type: application/json" \
  -d '{
    "mappings": { "properties": {
      "order_id": { "type": "keyword" },
      "items": { "type": "nested", "properties": {
        "sku":   { "type": "keyword" },
        "price": { "type": "double" }
      }}
    }}
  }'
```

### 插入测试数据

```bash
# 向两个索引插入相同的数据
for IDX in order_object order_nested; do
  curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/$IDX/_doc/1?refresh=wait_for" \
    -H "Content-Type: application/json" \
    -d '{
      "order_id": "O1001",
      "items": [
        { "sku": "A", "price": 10 },
        { "sku": "B", "price": 20 }
      ]
    }'
done
```

### 对比查询：object 错配 vs nested 精确

```bash
# object 版查询 —— 会错误命中（sku=A 和 price>=20 来自不同对象）
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/order_object/_search?pretty" \
  -H "Content-Type: application/json" \
  -d '{
    "query": { "bool": { "must": [
      { "term": { "items.sku": "A" } },
      { "range": { "items.price": { "gte": 20 } } }
    ]}}
  }'

# nested 版查询 —— 不会命中（同一 nested 对象中不存在 sku=A 且 price>=20）
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/order_nested/_search?pretty" \
  -H "Content-Type: application/json" \
  -d '{
    "query": { "nested": {
      "path": "items",
      "query": { "bool": { "must": [
        { "term": { "items.sku": "A" } },
        { "range": { "items.price": { "gte": 20 } } }
      ]}}
    }}
  }'
```

### inner_hits：定位命中的 nested 对象

```bash
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/order_nested/_search?pretty" \
  -H "Content-Type: application/json" \
  -d '{
    "query": { "nested": {
      "path": "items",
      "query": { "term": { "items.sku": "B" } },
      "inner_hits": {
        "_source": ["items.sku", "items.price"],
        "highlight": { "fields": { "items.sku": {} } }
      }
    }}
  }'
```

### nested 聚合 + reverse_nested

```bash
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/order_nested/_search?pretty" \
  -H "Content-Type: application/json" \
  -d '{
    "size": 0,
    "aggs": {
      "items_agg": {
        "nested": { "path": "items" },
        "aggs": {
          "avg_price": { "avg": { "field": "items.price" } },
          "by_sku": {
            "terms": { "field": "items.sku" },
            "aggs": {
              "back_to_order": {
                "reverse_nested": {},
                "aggs": {
                  "order_count": { "cardinality": { "field": "order_id" } }
                }
              }
            }
          }
        }
      }
    }
  }'
```

### nested 排序

```bash
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/order_nested/_search?pretty" \
  -H "Content-Type: application/json" \
  -d '{
    "sort": [{
      "items.price": {
        "order": "asc",
        "mode": "min",
        "nested": { "path": "items" }
      }
    }]
  }'
```

### 观察 Lucene 文档数膨胀

```bash
curl -u "$ES_USER:$ES_PASS" -X GET "$ES_URL/_cat/indices/order_*?v&h=index,docs.count,store.size"
# order_nested 的 docs.count 会是 3（2 个 nested + 1 个父文档），而非业务上的 1 条
```

### 清理

```bash
curl -u "$ES_USER:$ES_PASS" -X DELETE "$ES_URL/order_object,order_nested"
```

---

## 实战（Java SDK）

```java
// ---------- 创建 nested 索引 ----------
client.indices().create(c -> c.index("order_nested")
    .mappings(m -> m.properties("order_id", p -> p.keyword(k -> k))
        .properties("items", p -> p.nested(n -> n
            .properties("sku", sp -> sp.keyword(k -> k))
            .properties("price", sp -> sp.double_(d -> d))))));

// ---------- 写入数据 ----------
client.index(i -> i.index("order_nested").id("1").refresh(Refresh.WaitFor)
    .document(Map.of(
        "order_id", "O1001",
        "items", List.of(
            Map.of("sku", "A", "price", 10),
            Map.of("sku", "B", "price", 20)))));

// ---------- nested 查询 + inner_hits ----------
var response = client.search(s -> s.index("order_nested")
    .query(q -> q.nested(n -> n.path("items")
        .query(nq -> nq.bool(b -> b
            .must(m -> m.term(t -> t.field("items.sku").value("B")))
            .must(m -> m.range(r -> r.number(nr -> nr.field("items.price").gte(15d))))))
        .innerHits(ih -> ih.source(src -> src.filter(f -> f.includes(List.of("items.sku", "items.price"))))))),
    Map.class);

System.out.println("命中文档数: " + response.hits().total().value());
response.hits().hits().forEach(hit -> {
    System.out.println("文档 ID: " + hit.id());
    hit.innerHits().get("items").hits().hits().forEach(inner ->
        System.out.println("  命中的 nested 对象: " + inner.source()));
});

// ---------- nested 聚合 ----------
var aggResponse = client.search(s -> s.index("order_nested").size(0)
    .aggregations("items_agg", a -> a
        .nested(n -> n.path("items"))
        .aggregations("avg_price", sub -> sub.avg(avg -> avg.field("items.price")))
        .aggregations("by_sku", sub -> sub
            .terms(t -> t.field("items.sku"))
            .aggregations("back_to_order", rev -> rev
                .reverseNested(rn -> rn)
                .aggregations("order_count", oc -> oc.cardinality(ca -> ca.field("order_id")))))),
    Map.class);

var nestedAgg = aggResponse.aggregations().get("items_agg").nested();
System.out.println("平均价格: " + nestedAgg.aggregations().get("avg_price").avg().value());

// ---------- 清理 ----------
client.indices().delete(d -> d.index("order_nested"));
```

