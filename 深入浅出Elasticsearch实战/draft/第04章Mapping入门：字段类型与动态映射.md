# 背景

# 第04章 Mapping 入门：字段类型与动态映射

很多检索效果问题，表面看是“查询写得不对”，本质却是“字段类型建错了”。Mapping 是 ES 的数据契约，一旦错误，修复成本通常高于一开始多花的建模时间。

## 本章目标
- 掌握字段类型与查询能力的关系。
- 学会控制动态映射带来的风险。

## 1. Mapping 为什么关键
Mapping 是 ES 的“数据合同”。  
字段类型一旦设计错误，后续查询、聚合、排序都可能异常，修复成本很高。

## 2. 常见字段类型
- `text`：全文检索字段。
- `keyword`：精确匹配、聚合、排序字段。
- `date`/`long`/`double`：时间与数值统计场景。
- `object`/`nested`：对象结构建模。

## 3. 动态映射的利与弊
### 优点
- 上手快，写入新字段不需要提前定义。

### 风险
- 字段类型被误判。
- 字段数量膨胀导致 mapping explosion。

## 4. 实践建议
- 核心字段必须显式 mapping。
- 通过 dynamic templates 统一字段规则。
- 建立索引模板，保证环境一致性。

# 总结
- Mapping 是“先设计后写入”的关键环节。
- 省略建模会在后期以更高代价偿还。

## 练习题
1. 为订单文档设计一版 mapping（支持检索、过滤、聚合）。  
2. 把 `title` 分别定义为 `text` 和 `keyword`，比较查询差异。  
3. 用 dynamic templates 控制 `*_id` 字段为 `keyword`。  

## 实战（curl）

```bash
# 创建测试索引并验证 mapping
curl -u "$ES_USER:$ES_PASS" -X PUT "$ES_URL/mapping_demo" \
  -H "Content-Type: application/json" \
  -d '{
    "mappings":{
      "dynamic_templates":[
        {"ids":{"match":"*_id","mapping":{"type":"keyword"}}}
      ],
      "properties":{
        "title":{"type":"text","fields":{"keyword":{"type":"keyword"}}},
        "price":{"type":"double"}
      }
    }
  }'
curl -u "$ES_USER:$ES_PASS" "$ES_URL/mapping_demo/_mapping?pretty"
```

## 实战（Java SDK）

```java
client.indices().getMapping(g -> g.index("mapping_demo"))
    .result().forEach((k,v) -> System.out.println(k));
```

