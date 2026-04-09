# 第七章（分篇五）：`RRingBuffer`——定长环形覆盖

[← 第七章导览](14-队列与流.md)｜[目录](README.md)

---

## 1. 项目背景

实时监控大屏只关心 **最近 N 条** 采样点：旧数据可以丢，但不能 **无限占内存**。  
**`RRingBuffer<V>`** 在 **容量满** 时从 **头部驱逐** 旧元素（见接口注释），适合 **固定窗口的最近数据**，而不是 **全量可靠队列**。

---

## 2. 项目设计（大师 × 小白）

**小白**：我用 `RQueue` 自己判断 size 超了再删头行不行？  
**大师**：行，但 **竞态与脚本次数** 烦人。RingBuffer 把 **「满则顶掉最老」** 封成一种对象语义，**少写一堆 if**。

**小白**：那我用 RingBuffer 做订单可靠队列？  
**大师**：**旧订单会被顶飞**——这是 **特征不是 bug**。可靠业务请回分篇三、四。

---

## 3. 项目实战（主代码片段）

```java
import org.redisson.api.RRingBuffer;
import org.redisson.api.RedissonClient;

RedissonClient redisson = /* ... */;

RRingBuffer<Double> samples = redisson.getRingBuffer("probe:cpu:host-01");
samples.trySetCapacity(3600); // 首次设置容量；已存在则 false，需按文档用 setCapacity 等

samples.add(0.42);
samples.add(0.55);

int left = samples.remainingCapacity();
```

**要点**：**必须先 `trySetCapacity` / `setCapacity`**（见 `RRingBuffer` JavaDoc）；`extends RQueue`，故 **offer/poll** 等仍可用，但语义以 **环形驱逐** 为准。

---

## 4. 项目总结

| 维度 | 说明 |
|------|------|
| **优点** | **自动限制长度**；**最近数据** 场景下 API 清晰；省自建裁剪逻辑。 |
| **缺点** | **不保证历史全保留**；**非** 可靠消息；容量变更要理解 **trim** 行为。 |
| **适用场景** | 滑动窗口指标、最近 N 条日志样本、采样式监控。 |
| **注意事项** | 与 **第九章 Pub/Sub** 区分：此处是 **有界存储**，不是广播。 |
| **常见踩坑** | **未初始化容量**；把环形缓冲当 **审计账本**；多实例 **同 key** 写入顺序依赖业务是否可接受。 |

---

## 本章实验室（约 25 分钟）

**环境**：单 Redis。

### 步骤

1. `getRingBuffer("lab:ring:probe")`，`trySetCapacity(5)`。  
2. 依次 `add` 元素 `"a"`～`"g"`（7 个），`remainingCapacity()` 与 `size()`（若有）或 `readAll()` 观察 **只保留最近 5 个**（以实际 API 行为为准：最早被逐出）。  
3. 打印 **队头/队尾** 或 `readAll()` 顺序，确认 **最老的 a、b 已不在**。  
4. 再次 `trySetCapacity(5)` 应返回 **false**（已初始化），换 `setCapacity` 观察文档描述的 **trim** 行为（谨慎仅在测试环境）。

### 验证标准

- 能口述：**满则顶头** 与 **普通无限队列** 的差异。  
- `redis-cli` 中 key 的 **元素个数** 与预期 **≤ capacity**。

### 记录建议

- 截图：`readAll()` 或等价输出 **7 次 add 后** 的最终列表。

**上一篇**：[第七章（分篇四）RStream](18-RStream.md)｜**下一章**：[第八章上 分布式锁](20-分布式锁-RLock与看门狗.md)
