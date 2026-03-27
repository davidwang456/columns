# 背景

# 第26章 缓存与内存：Heap、文件缓存、断路器

内存问题通常不是瞬间崩溃，而是先出现延迟抖动、再出现拒绝请求。理解 heap、文件缓存、断路器分工，是稳定性治理的基础。

## 本章目标
- 理解 ES 的内存分工与常见风险。
- 学会识别并缓解内存压力问题。

## 1. 三类关键内存视角
- JVM Heap：对象管理与部分缓存
- 文件系统缓存：查询性能关键
- 断路器：防止单次请求占用失控

## 2. 常见误区
- 误以为 heap 越大越好。
- 忽略文件系统缓存对检索性能的影响。

## 3. 风险场景
- 高基数聚合导致内存峰值过高。
- text 字段误用聚合触发 fielddata 压力。

## 4. 实践建议
- 优先使用 doc_values 支撑排序聚合。
- 对高风险查询设置资源边界与超时。

# 总结
- 内存问题通常先表现为延迟抖动，再演化为稳定性故障。
- 预防优于救火，监控优于猜测。

## 练习题
1. 列出你们查询中最可能触发内存峰值的场景。  
2. 分析一次 heap pressure 告警并给出处置方案。  
3. 解释 doc_values 与 fielddata 的差异。  

## 实战（curl）

```bash
curl -u "$ES_USER:$ES_PASS" "$ES_URL/_nodes/stats/jvm,indices/fielddata,breaker?pretty"
curl -u "$ES_USER:$ES_PASS" "$ES_URL/_cat/fielddata?v"
```

## 实战（Java SDK）

```java
var stats = client.nodes().stats(n -> n.metric("jvm","breaker","indices"));
System.out.println(stats.nodes().size());
```

