# 背景

# 第12章 查询性能工具箱：慢日志、Profile、Explain

线上慢查询不可怕，可怕的是“慢了但不知道慢在哪里”。本章把慢日志、Profile、Explain 串成一条标准排查链路，减少无效调优。

## 本章目标
- 掌握慢查询定位的标准流程。
- 从“凭感觉优化”转为“证据驱动优化”。

## 1. 三个核心工具
- 慢日志：找到慢查询“是谁”。
- Profile：定位慢在“哪一段”。
- Explain：解释“为什么这样排序”。

## 2. 推荐排查顺序
1) 看慢日志，筛出高频慢查询  
2) 用 Profile 分析执行阶段  
3) 检查 mapping 与 query 结构  
4) 做有目标的优化并回归验证

## 3. 常见优化方向
- 把结构化条件放 `filter`
- 控制深分页和高基数聚合
- 减少不必要脚本和复杂评分

## 4. 进阶建议
- 建立查询基线与压测数据集。
- 每次优化都记录前后延迟对比。

# 总结
- 没有可观测就没有真正的优化。
- 工具链越规范，团队排障效率越高。

## 练习题
1. 找出一条慢查询并输出 Profile 结果。  
2. 提出 3 条优化方案并验证效果。  
3. 用 Explain 解释一次“排序不合理”现象。  

## 实战（curl）

```bash
# 1) profile 查询
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/$IDX/_search?pretty" \
  -H "Content-Type: application/json" \
  -d '{
    "profile": true,
    "query":{"bool":{"must":[{"match":{"name":"笔记本"}}]}}
  }'

# 2) explain 单文档
curl -u "$ES_USER:$ES_PASS" -X GET "$ES_URL/$IDX/_explain/p1?pretty" \
  -H "Content-Type: application/json" \
  -d '{"query":{"match":{"name":"笔记本"}}}'
```

## 实战（Java SDK）

```java
var profileResp = client.search(s -> s.index("products_v1")
    .profile(true)
    .query(q -> q.match(m -> m.field("name").query("笔记本"))), Map.class);
System.out.println(profileResp.profile() != null);
```

