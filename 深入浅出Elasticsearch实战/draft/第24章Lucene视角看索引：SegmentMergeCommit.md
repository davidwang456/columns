# 背景

# 第24章 Lucene 视角看索引：Segment、Merge、Commit

很多性能抖动问题无法仅靠 API 层解释，需要回到 Lucene 机制理解 segment 与 merge 的影响，才能做出正确调优决策。

## 本章目标
- 从底层机制理解 ES 性能波动原因。
- 知道哪些优化动作会产生副作用。

## 1. Segment 是什么
ES 底层数据以 segment 组织。  
segment 增多会增加查询开销，过多时需要 merge 合并。

## 2. Merge 的双面性
- 好处：减少段数，提升长期查询效率。
- 代价：占用 IO/CPU，可能带来短时抖动。

## 3. Commit 与持久化
commit 关系到数据持久化阶段，  
需要结合 translog 与刷新机制整体理解。

## 4. 实践建议
- 不要把 force_merge 当日常操作。
- 观察段数、merge 时间与 IO 指标再决策。

# 总结
- 底层机制决定了 ES 的性能边界。
- 理解 segment/merge，才能做正确调优。

## 练习题
1. 观察写入压测期间段数变化趋势。  
2. 分析一次 merge 高峰期延迟抖动。  
3. 说明 force_merge 的适用与禁用场景。  

## 实战（curl）

```bash
# 观察段信息
curl -u "$ES_USER:$ES_PASS" "$ES_URL/_cat/segments/$IDX?v"

# 手动触发 forcemerge（仅测试环境）
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/$IDX/_forcemerge?max_num_segments=1&pretty"
```

## 实战（Java SDK）

```java
client.indices().forcemerge(f -> f.index("products_v1").maxNumSegments(1L));
```

