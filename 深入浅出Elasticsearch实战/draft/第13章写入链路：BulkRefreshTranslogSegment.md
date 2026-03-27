# 背景

# 第13章 写入链路：Bulk、Refresh、Translog、Segment

写入性能优化不是只调一个参数。Bulk、refresh、translog、segment merge 是同一条链路，必须整体理解才能避免“提了吞吐却拖慢查询”。

## 本章目标
- 理解 ES 写入链路的关键环节。
- 学会在吞吐与可见性之间做平衡。

## 1. 写入链路概览
文档写入后会经历：
- 主分片接收与处理
- translog 记录
- refresh 后可被搜索
- segment 持续合并优化

## 2. Bulk 为什么是默认选择
- 单条写入网络与协议开销高。
- `_bulk` 可显著提升吞吐。
- 但批次过大也会增加失败成本和内存压力。

## 3. refresh 的影响
- 刷新越频繁，写入开销越高。
- 刷新间隔合理设置可提升整体吞吐。

## 4. 进阶关注
- merge 抖动可能造成延迟波动。
- 需要结合磁盘 IO、线程池与队列指标分析。

# 总结
- 写入优化不是单点调参，而是链路协同。
- 批量、刷新、合并三者需要平衡。

## 练习题
1. 比较不同 bulk 批大小下的吞吐。  
2. 调整 `refresh_interval` 并观察查询可见性变化。  
3. 分析一次写入高峰下的延迟波动原因。  

## 实战（curl）

```bash
# 调整 refresh_interval
curl -u "$ES_USER:$ES_PASS" -X PUT "$ES_URL/$IDX/_settings" \
  -H "Content-Type: application/json" -d '{"index":{"refresh_interval":"30s"}}'

# bulk 写入
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/$IDX/_bulk?refresh=wait_for" \
  -H "Content-Type: application/x-ndjson" \
  -d '{ "index": { "_id": "b1" } }
{ "name":"bulk-1","category":"demo","price":1,"in_stock":true }'
```

## 实战（Java SDK）

```java
client.indices().putSettings(p -> p.index("products_v1")
    .settings(s -> s.refreshInterval(t -> t.time("30s"))));
```

