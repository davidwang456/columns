# 背景

# 第15章 别名与零停机重建：Reindex、双写、切流

索引结构迭代几乎不可避免。问题不在“改不改”，而在“如何不停机、可回滚地改”。别名 + reindex 是线上最常见的安全迁移方案。

## 本章目标
- 学会在不中断业务的情况下重建索引。
- 掌握索引迁移的标准流程与回滚思路。

## 1. 为什么需要重建
- mapping 改动通常不可原地完成。
- 历史索引策略不合理需要升级。

## 2. 别名的作用
- 读别名和写别名可解耦业务与物理索引。
- 切换别名比改业务配置更安全。

## 3. 标准迁移流程
1) 创建新索引  
2) 历史数据 reindex  
3) 双写新旧索引（短窗口）  
4) 校验一致性  
5) 切别名  
6) 保留回滚窗口后下线旧索引

## 4. 风险控制
- 迁移前准备回滚方案。
- 对关键统计做迁移前后对比。

# 总结
- 别名 + reindex 是最常用的低风险迁移组合。
- 可验证、可回滚是迁移成功关键。

## 练习题
1. 设计一次 mapping 变更迁移方案。  
2. 写出迁移校验清单（数量、抽样、聚合结果）。  
3. 设计迁移失败时的回滚步骤。  

## 实战（curl）

```bash
# 1) 创建新索引
curl -u "$ES_USER:$ES_PASS" -X PUT "$ES_URL/products_v2" -H "Content-Type: application/json" -d '{"mappings":{"properties":{"name":{"type":"text"}}}}'

# 2) reindex
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/_reindex?wait_for_completion=true&pretty" \
  -H "Content-Type: application/json" \
  -d '{"source":{"index":"products_v1"},"dest":{"index":"products_v2"}}'

# 3) 切别名
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/_aliases" -H "Content-Type: application/json" -d '{
  "actions":[
    {"remove":{"index":"products_v1","alias":"products_current"}},
    {"add":{"index":"products_v2","alias":"products_current","is_write_index":true}}
  ]
}'
```

## 实战（Java SDK）

```java
client.reindex(r -> r.source(s -> s.index("products_v1")).dest(d -> d.index("products_v2")));
```

