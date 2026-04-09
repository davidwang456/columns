# 第六章（分篇三）：`RScoredSortedSet`（ZSet）——分数、排行与原子加分

[← 第六章导览](10-分布式集合选型.md)｜[目录](README.md)

---

## 1. 项目背景

游戏要 **实时积分榜**：玩家多次得分，榜单按 **累计分** 排序，运营要 **Top 100**；电商要 **按销量/热度排序的商品榜**。  
这类需求需要 **member + score**，且加分应 **原子**（避免「读出分数 → 加 → 写回」的竞态）。Redis **Sorted Set** 是标准答案；Redisson 对应 **`RScoredSortedSet<V>`**。

---

## 2. 项目设计（大师 × 小白）

**小白**：我用 `RMap` 存 userId → score，定时排序行不行？  
**大师**：排序若在应用里做，数据一大 **CPU 与一致性** 都扛不住。ZSet 让 **Redis 维护有序结构**，`addScore` 一类操作 **原子增量**，才是正路。

**小白**：全员给同一个「榜首」刷分，Redis 扛得住吗？  
**大师**：那是 **热 member / 热 key** 问题——架构上要 **分桶、分榜、或读写分离**，不是换一个 API 能糊弄过去的。

---

## 3. 项目实战（主代码片段）

```java
import org.redisson.api.RScoredSortedSet;
import org.redisson.api.RedissonClient;
import org.redisson.client.protocol.ScoredEntry;

import java.util.Collection;

RedissonClient redisson = /* ... */;

RScoredSortedSet<String> board = redisson.getScoredSortedSet("game:board:daily:2026-04-08");

board.add(0, "player-alice");           // 首次上榜可给初值
board.addScore("player-alice", 10);     // 原子加分（ZINCRBY 语义）
board.add(5, "player-bob");

// 按名次取 Top 10：entryRangeReversed 的索引含「反向名次」语义，负数表示从高榜计起；
// 具体边界以当前版本 JavaDoc 为准，上线前用单测对齐期望顺序
Collection<ScoredEntry<String>> top10 = board.entryRangeReversed(0, 9);

for (ScoredEntry<String> e : top10) {
    System.out.println(e.getValue() + " -> " + e.getScore());
}
```

**深度**：并发下避免「先查名次再改分」的 **读改写**；优先 **`addScore` / 带版本的原子更新**，必要时 **Lua**（第十章）。时间窗口榜要想 **过期与 key 分片**，勿单 key 扛所有活动。

---

## 4. 项目总结

| 维度 | 说明 |
|------|------|
| **优点** | **按分数有序**；**原子增减分**；取 **Top N**、按分范围查询 与 Redis 一致；适合排行榜、延时队列（按 score 当时间）等模式。 |
| **缺点** | 大 ZSet **全量范围扫描** 昂贵；**热 member** 会导致单 key QPS 集中；复杂分析仍宜下推到 **数仓/专用引擎**。 |
| **适用场景** | 排行榜、点赞/积分、带权重的优先队列雏形、按时间排序的 score 设计（需仔细设计 score 编码）。 |
| **注意事项** | **`entryRange` / `entryRangeReversed` 的索引与边界** 以当前版本文档为准；与 **集群 slot** 相关的多 key 操作限制（第二章）。 |
| **常见踩坑** | 用 **List 做榜**；**读改写** 更新分数导致丢更新；忽略 **大 key** 与 **热 key**；同一 ZSet 混用 **多种活动** 导致逻辑纠缠。 |

---

## 本章实验室（约 35 分钟）

**环境**：单 Redis；可选第二个线程做并发 `addScore`。

### 步骤

1. `getScoredSortedSet("lab:zset:game")`，`add(0, "alice")`，`addScore("alice", 10)`，`add(5, "bob")`。  
2. `entryRangeReversed(0, 9)` 打印 **带分数条目**，确认 **alice 在 bob 前**（若分数高在前，以你本地 JavaDoc 为准则调整断言）。  
3. 开两线程同时对 `"alice"` `addScore` 各 5000 次；结束后 `getScore("alice")` 应为 **10010**（0+10+5000+5000）。  
4. `redis-cli` `ZCARD` / `ZRANGE WITHSCORES`（先找到 key）与 Java 对照。

### 验证标准

- 并发加分 **无丢更新**（最终分数与次数乘积一致）。  
- `redis-cli` 与 Java **member/score** 一致。

### 记录建议

- 若 `entryRangeReversed` 边界与直觉不符，**抄一段 JavaDoc** 到笔记。

**上一篇**：[第六章（分篇二）RSet](12-RSet-集合与去重.md)｜**下一章**：[第七章导览](14-队列与流.md)
