# 背景

# 第11章 Nested 与 Object：关联建模实践

数组对象查询“命中结果不符合直觉”是 ES 建模高频坑。object 与 nested 的差异不在语法，而在数据关联语义是否被正确保留。

## 本章目标
- 理解 object 与 nested 的语义差异。
- 避免数组对象查询中的“错配命中”问题。

## 1. object 的特点
`object` 字段在内部会被扁平化，  
查询时可能出现“不同对象字段被拼在一起命中”的问题。

## 2. nested 的特点
`nested` 会保留对象边界，查询更准确，  
但索引与查询成本更高。

## 3. 如何选择
- 数据关系简单、准确性要求一般：可考虑 object。
- 需要严格对象级匹配：优先 nested。

## 4. 替代思路
- 业务反范式（冗余关键字段）
- 主数据在主库，ES 只做检索视图

# 总结
- nested 不是默认选项，而是“准确性优先”选项。
- 建模时要先算清楚准确性收益与性能成本。

## 练习题
1. 用 object 和 nested 各建一版订单明细索引。  
2. 写查询验证两者结果差异。  
3. 给出你在业务中选 nested 的判断标准。  

## 实战（curl）

```bash
curl -u "$ES_USER:$ES_PASS" -X PUT "$ES_URL/nested_demo" \
  -H "Content-Type: application/json" \
  -d '{
    "mappings":{"properties":{
      "items":{"type":"nested","properties":{
        "sku":{"type":"keyword"},
        "price":{"type":"double"}
      }}
    }}
  }'

curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/nested_demo/_doc/1?refresh=wait_for" \
  -H "Content-Type: application/json" \
  -d '{"items":[{"sku":"A","price":10},{"sku":"B","price":20}]}'

curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/nested_demo/_search?pretty" \
  -H "Content-Type: application/json" \
  -d '{"query":{"nested":{"path":"items","query":{"bool":{"must":[{"term":{"items.sku":"A"}},{"range":{"items.price":{"gte":10}}}]}}}}}'
```

## 实战（Java SDK）

```java
var r = client.search(s -> s.index("nested_demo")
    .query(q -> q.nested(n -> n.path("items")
        .query(nq -> nq.term(t -> t.field("items.sku").value("A"))))), Map.class);
System.out.println(r.hits().total().value());
```

