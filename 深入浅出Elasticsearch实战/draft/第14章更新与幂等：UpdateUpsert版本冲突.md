# 背景

# 第14章 更新与幂等：Update、Upsert、版本冲突

在分布式系统里，更新请求重试是常态。没有幂等设计与版本冲突治理，数据就会出现“偶发不一致”，而且很难复现。

## 本章目标
- 建立可重试、可回放的写入更新方案。
- 正确处理并发更新冲突。

## 1. update 与 index 的区别
- `index`：整文档覆盖，逻辑简单。
- `update`：局部更新，适合字段增量修改。

## 2. upsert 的价值
当“可能不存在”时，`upsert` 可统一插入与更新逻辑，  
降低应用层分支复杂度。

## 3. 幂等与冲突处理
- 幂等键是高可用写入的基础。
- 并发更新可能产生版本冲突。
- 可用重试或外部版本策略控制一致性。

## 4. 工程建议
- 所有重试都要可追踪。
- 避免无上限重试导致流量雪崩。

# 总结
- 更新逻辑稳定性的关键在于幂等与冲突治理。
- 先保证正确，再优化吞吐。

## 练习题
1. 实现一个 `upsert` 写入接口。  
2. 模拟并发更新并观察版本冲突行为。  
3. 给出你们业务的幂等策略设计。  

## 实战（curl）

```bash
# upsert
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/$IDX/_update/u1?refresh=wait_for" \
  -H "Content-Type: application/json" \
  -d '{
    "doc":{"name":"upsert-doc","price":100},
    "doc_as_upsert":true
  }'
```

## 实战（Java SDK）

```java
client.update(u -> u.index("products_v1").id("u1")
    .doc(Map.of("price", 120))
    .docAsUpsert(true), Map.class);
```

