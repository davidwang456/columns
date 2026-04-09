# 第六章（分篇一）：`RList`——有序序列与「别当排行榜」

[← 第六章导览](10-分布式集合选型.md)｜[目录](README.md)

---

## 1. 项目背景

运营要在首页展示 **最近 20 条动态**：**严格按时间顺序** 追加，且可能要在头部插入「置顶」；或技术侧实现 **简单任务串行队列**（与第七章 `RBlockingQueue`、Stream 的可靠性语义不同，见下文）。  
在 Redis 里这是 **List** 语义；Redisson 对应 **`RList<V>`**——实现了 **`java.util.List`**，元素 **可重复**，顺序由插入位置决定，**不是按分数排序**。

---

## 2. 项目设计（大师 × 小白）

**小白**：排行榜我用 `RList` 行不行？  
**大师**：排行榜要 **按分数高低** 动态排序，该用 **`RScoredSortedSet`（ZSet）**；`RList` 是 **人工排队取号**，不是 **自动按成绩排序**。

**小白**：我把 List 拉出来 `Collections.sort` 再写回 Redis？  
**大师**：那是把 Redis 当 **临时 ArrayList**，并发下 **读改写竞态**、大列表 **全量排序成本** 都会教你做人。**该排序的域就用 ZSet**，别在 List 上硬拗。

---

## 3. 项目实战（主代码片段）

```java
import org.redisson.api.RList;
import org.redisson.api.RedissonClient;

RedissonClient redisson = /* ... */;

RList<String> feed = redisson.getList("home:feed:recent");
feed.add("post-1001");           // 尾部追加
feed.add(0, "post-pin-9");       // 头部置顶
int n = feed.size();             // 注意：大 List 上 size/全量遍历成本高
String latestPinned = feed.get(0);

// 控制长度：避免无限增长（具体 API 以版本为准，如 trim、取子列表后重写等）
while (feed.size() > 100) {
    feed.remove(feed.size() - 1);
}
```

**与第七章的关系**：需要 **阻塞弹出、可靠投递、消费组** 时，应评估 `RBlockingQueue`、`RStream` 等；`RList` 更适合 **显式维护顺序的列表**，而不是 **完整消息队列语义**。

---

## 4. 项目总结

| 维度 | 说明 |
|------|------|
| **优点** | 与 Java `List` 心智一致；**按索引/头尾操作** 简单；适合 **近期列表、有序流水号式数据**。 |
| **缺点** | **无分数排序**；大列表的 **`size`、遍历** 可能很重；**非** 开箱即用的可靠队列。 |
| **适用场景** | 最新动态、简单 FIFO 列表、需要在头/尾精细插入的场景。 |
| **注意事项** | 控制 **最大长度**；集群下 **单 key** 仍是热与大 key 风险点。 |
| **常见踩坑** | 用 `RList` 做 **排行榜**；多实例 **无锁** 读改写同一列表导致覆盖；忽略 **大 key** 对 Redis 单线程的影响。 |

---

## 本章实验室（约 25 分钟）

**环境**：单 Redis。

### 步骤

1. `getList("lab:rlist:feed")`，`addLast` 5 个元素，再 `addFirst("pin")`。  
2. `get(0)` 应为 `pin`；`redis-cli` 用 `LRANGE`（先 `SCAN` 找 key）核对 **顺序与长度**。  
3. 写循环：长度超过 20 则 `remove(size-1)`，直到长度为 10，验证 **裁剪逻辑**。  
4. **反例**：尝试用 `RList` 实现「按分数排序榜」——写出你会改用 **`RScoredSortedSet`** 的一句话理由。

### 验证标准

- `LRANGE` 与 Java 侧遍历顺序 **一致**。  
- 能口述：**List 与 ZSet 选型边界**。

### 记录建议

- 保存 `LRANGE` 输出截图。

**上一章**：[第五章（分篇四）限流](09-分布式限流.md)｜[第五章导览](05-分布式对象基础.md)｜**下一篇**：[第六章（分篇二）RSet](12-RSet-集合与去重.md)｜**下一章**（读完分篇三后）：[第七章导览](14-队列与流.md)
