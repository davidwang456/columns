# 背景

# 第27章 线程池与背压：Rejections、限流、保护

线上流量有峰谷，ES 不可能无限吞吐。理解线程池与背压机制，才能把“拒绝请求”从事故变成可控保护行为。

## 本章目标
- 识别 ES 拒绝请求（rejections）的根因。
- 建立端到端背压与限流策略。

## 1. 为什么会有 rejections
线程池和队列容量有限，  
当请求速度超过处理能力时，拒绝是保护机制。

## 2. 常见触发原因
- 突发流量过高
- 查询过重或写入批次过大
- 节点资源不足或不均衡

## 3. 处理策略
- 服务端：优化查询、扩容、隔离关键流量
- 客户端：限流、退避重试、幂等保障

## 4. 工程实践建议
- 不要把重试当万能方案。
- 关键接口设置降级策略，保护核心路径。

# 总结
- rejections 是系统在自我保护，不是“随机报错”。
- 背压治理要服务端和客户端协同。

## 练习题
1. 设计一个包含限流与退避的客户端重试策略。  
2. 分析一次高峰期 rejections 的排查路径。  
3. 给出一个“核心请求优先”资源隔离方案。  

## 实战（curl）

```bash
curl -u "$ES_USER:$ES_PASS" "$ES_URL/_cat/thread_pool/search,write?v&h=node_name,name,active,queue,rejected,completed"
curl -u "$ES_USER:$ES_PASS" "$ES_URL/_nodes/stats/thread_pool?pretty"
```

## 实战（Java SDK）

```java
var tpStats = client.nodes().stats(n -> n.metric("thread_pool"));
System.out.println(tpStats.nodes().size());
```

