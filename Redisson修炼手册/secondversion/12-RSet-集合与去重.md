# 第六章（分篇二）：`RSet`——去重与集合运算

[← 第六章导览](10-分布式集合选型.md)｜[目录](README.md)

---

## 1. 项目背景

活动「单日投票」要求：**每个用户 ID 只能投一票**。用字符串 `SET` 多次 `SADD` 可做到，但在业务代码里更希望 **面向集合** 编程。  
Redisson 的 **`RSet<V>`** 对应 Redis **Set**：**元素唯一、无序**（不保证遍历顺序稳定用于业务逻辑），支持 **交集、并集、差集** 等集合代数（见接口与文档），适合 **去重、标签、参与人集合**。

若需要 **按元素值排序且唯一**，应评估 **`RSortedSet`**（Redis ZSet 上 score=0 的特例等场景），与本篇 **`RSet`** 区分。

---

## 2. 项目设计（大师 × 小白）

**小白**：去重我用 `RList` 自己 `contains` 行不行？  
**大师**：`contains` 在 List 上是 **O(n)** 网络+复杂度噩梦；`Set` 的语义就是 **为唯一性而生**。

**小白**：`RSet` 和布隆过滤器都是「在不在」？  
**大师**：`RSet` **精确**：说在就一定在过集合里（以一致性为准）；布隆 **省空间** 但有 **假阳性**。要 **投票防重复**，用 Set；要 **亿级前置过滤**，再看布隆（第五章分篇三）。

---

## 3. 项目实战（主代码片段）

```java
import org.redisson.api.RSet;
import org.redisson.api.RedissonClient;

RedissonClient redisson = /* ... */;

RSet<String> voters = redisson.getSet("vote:2026-spring:user-ids");
boolean firstTime = voters.add("user-9527"); // Set.add：新元素返回 true
if (!firstTime) {
    throw new IllegalStateException("already voted");
}

boolean maybeStaff = voters.contains("user-admin");
int total = voters.size(); // 集合很大时同样要警惕成本
```

**延伸**：带过期需求的 Set 可了解 **`RSetCache`**（见 `RedissonClient` JavaDoc）；与 **Map 结构** 的选型见第五章 `RMap`。

---

## 4. 项目总结

| 维度 | 说明 |
|------|------|
| **优点** | **唯一性** 由数据结构保证；API 贴近 `java.util.Set`；支持 **集合运算** 做推荐、标签重叠分析等。 |
| **缺点** | **无序**；大 Set **SMEMBERS 式全量** 类操作危险；内存随成员数增长。 |
| **适用场景** | 投票/签到去重、黑白名单、标签集、协作者列表。 |
| **注意事项** | member 若为 **大对象**，Codec 与 value 体积仍受第四章约束。 |
| **常见踩坑** | 依赖 **迭代顺序** 当业务顺序；超大 Set 做 **全量导出** 打爆 Redis；与 **有序排行榜** 需求混淆（应去 ZSet 篇）。 |

---

## 本章实验室（约 25 分钟）

**环境**：单 Redis。

### 步骤

1. `getSet("lab:rset:vote")`，对 `"user-1"` 第一次 `add` 记录返回值，第二次再 `add` 记录返回值。  
2. 验证：**第一次 true / 第二次 false**（以 `Set.add` 语义为准）。  
3. `contains("user-1")` 为 true；`redis-cli` 对底层 Set key 执行 `SCARD` 与 `SISMEMBER`。  
4. （可选）再 `add` 99 个用户，`SCARD` 应为 100；思考 **生产全量 SMEMBERS 的风险**。

### 验证标准

- Java 与 `redis-cli` **成员存在性**一致。  
- 能说明：**为何 Set 适合做投票去重**。

### 记录建议

- 表格列：**操作 / 返回值 / redis-cli 对照**。

**上一篇**：[第六章（分篇一）RList](11-RList-列表与顺序.md)｜**下一篇**：[第六章（分篇三）ZSet 与排行榜](13-ZSet与排行榜.md)｜**下一章**（读完分篇三后）：[第七章导览](14-队列与流.md)
