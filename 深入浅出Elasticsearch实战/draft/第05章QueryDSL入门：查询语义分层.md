# 背景

# 第05章 Query DSL 入门：查询语义分层

业务搜索需求看起来是一句话，但落到 ES 往往是多层语义组合：必须满足的过滤条件、参与相关性排序的检索条件、需要排除的条件。不会拆解，就会出现“能查但不准、能跑但不稳”。

## 本章目标
- 建立 Query DSL 的基础心智模型。
- 能把业务需求拆成可维护的查询结构。

## 1. Query DSL 的核心思想
查询不是“一条语句解决所有问题”，而是组合：
- 过滤条件（是否命中）
- 相关性排序（命中后谁更靠前）

## 2. 常用查询组件
- `term`：精确匹配（ID、状态、标签）。
- `match`：全文检索（标题、正文）。
- `range`：区间过滤（价格、时间）。
- `bool`：组合查询（must/filter/should/must_not）。

## 3. must 与 filter 的区别
- `must`：参与评分，适合检索语义。
- `filter`：不参与评分，适合结构化条件，性能更稳。

建议把“硬条件”尽量放到 `filter`。

## 4. 一个通用拆解方式
以商品搜索为例：
- filter：类目、价格、库存状态
- must：关键词匹配标题
- should：品牌加权、热度加权

这样查询可解释、可调优、可扩展。

# 总结
- Query DSL 的难点不在语法，而在语义拆解。
- 先分层，再组合，查询会更清晰稳定。

## 练习题
1. 写一个“关键词 + 类目 + 价格区间 + 排除下架”的 bool 查询。  
2. 分别把价格条件放在 `must` 和 `filter`，比较结果差异。  
3. 给查询增加一个 `should` 条件并解释排序变化。  

## 实战（curl）

```bash
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/$IDX/_search?pretty" \
  -H "Content-Type: application/json" \
  -d '{
    "query":{
      "bool":{
        "must":[{"match":{"name":"笔记本"}}],
        "filter":[
          {"term":{"category":"laptop"}},
          {"range":{"price":{"gte":3000,"lte":8000}}}
        ],
        "must_not":[{"term":{"in_stock":false}}],
        "should":[{"match":{"name":"pro"}}],
        "minimum_should_match":0
      }
    }
  }'
```

## 实战（Java SDK）

```java
var resp = client.search(s -> s.index("products_v1")
    .query(q -> q.bool(b -> b
        .must(m -> m.match(mm -> mm.field("name").query("笔记本")))
        .filter(f -> f.term(t -> t.field("category").value("laptop"))))), Map.class);
System.out.println(resp.hits().hits().size());
```

