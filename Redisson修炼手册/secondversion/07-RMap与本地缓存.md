# 第五章（分篇二）：`RMap` 与 `RLocalCachedMap`——分布式 Map 与读多写少

[← 第五章导览](05-分布式对象基础.md)｜[目录](README.md)

---

## 1. 项目背景

订单详情里要缓存 **多字段**：用户昵称、会员等级、最近一单状态……若每次改一个字段都 **整包序列化进 `RBucket`**，并发写容易互相覆盖，且 payload 臃肿。  
更自然的建模是 **Hash**：一个业务 key 下多 field，对应 Redisson 的 **`RMap<K, V>`**。若 QPS 极高、且能接受 **短窗口内本地与远程不完全一致**，可引入 **`RLocalCachedMap`**：在 JVM 内有一层读缓存，由 Redisson 按策略与 Redis 同步。

---

## 2. 项目设计（大师 × 小白）

**小白**：`RMap` 和 `ConcurrentHashMap` 不就是 `put`/`get` 吗？  
**大师**：**语义类似，成本不同**——每次 `get` 可能是一次网络往返；`size()`、`readAllKeySet()` 在大 Map 上可能是 **O(N) 级别的昂贵操作**。把它当本地 Map 随便遍历，等于在 **生产上扫全表**。

**小白**：那我加 `RLocalCachedMap` 是不是就免费加速了？  
**大师**：换的是 **一致性模型与堆内存**。本地命中快，但要配置 **失效、更新策略**，并监控 **堆占用** 与 **脏读窗口**。没有银弹，只有 **权衡**。

---

## 3. 项目实战（主代码片段）

```java
import org.redisson.api.RLocalCachedMap;
import org.redisson.api.RMap;
import org.redisson.api.RedissonClient;
import org.redisson.api.LocalCachedMapOptions;

import java.util.concurrent.TimeUnit;

// 纯远程 Hash：与 Redis Hash 对应，适合字段级读写
RMap<String, String> orderView = redisson.getMap("order:view:10086");
orderView.put("status", "PAID");
orderView.put("buyerNick", "xiaobai");
String status = orderView.get("status");

// 读多写少：本地缓存 + 远程同步（选项以当前版本 API 为准）
LocalCachedMapOptions<String, String> options = LocalCachedMapOptions.<String, String>defaults()
        .syncStrategy(LocalCachedMapOptions.SyncStrategy.INVALIDATE)
        .timeToLive(10, TimeUnit.SECONDS)
        .maxIdle(5, TimeUnit.SECONDS);
RLocalCachedMap<String, String> hot = redisson.getLocalCachedMap("product:summary:9527", options);
String title = hot.get("title"); // 首次可能走 Redis，后续可能命中本地
```

**延伸**：客户端缓存总览见 [client-side-caching.md](../client-side-caching.md)；半结构化文档型需求还可对照 [objects.md](../data-and-services/objects.md)、[collections.md](../data-and-services/collections.md)。

---

## 4. 项目总结

| 维度 | 说明 |
|------|------|
| **优点** | **字段级更新**；比 giant `RBucket` 更易维护；`RLocalCachedMap` 可显著降低 **读路径 RTT**（命中本地时）。 |
| **缺点** | 远程 `RMap` 操作仍有 **网络成本**；本地缓存带来 **一致性复杂度**；大 Map **统计/全量遍历**危险。 |
| **适用场景** | 订单/用户视图缓存、多属性对象、读明显多于写且可接受短延迟一致性的热点数据。 |
| **注意事项** | **热 key** 仍需业务或架构层拆分；与 **第十章** 配合做多 key 原子逻辑；**key 前缀** 与环境、租户隔离（导览篇私房话）。 |
| **常见踩坑** | 在生产对巨大 `RMap` 调用 `size()`/全表扫描；本地缓存 **TTL 设太长** 导致陈旧数据客诉；多服务 **无约定** 写同一 field 命名。 |

---

## 本章实验室（约 35 分钟）

**环境**：单 Redis；若练习 `RLocalCachedMap` 需理解 **短 TTL** 便于观察。

### 步骤

1. `RMap`：`getMap("lab:rmap:order:9")`，写入 `status`、`buyer`、`amount` 三个 field。  
2. `redis-cli`：`HGETALL`（先 `SCAN` 找 key），与 Java `readAllMap()` 或逐 `get` 对照。  
3. **（可选）** `RLocalCachedMap`：进程 A `put` 新值，进程 B **先 `get`（可能旧）** 再等待 `syncStrategy`/TTL 后再 `get`，记录两次是否一致及延迟体感。  
4. **禁止项演练**：对含 1 万 field 的测试 Map（脚本预灌）调用一次 `size()`，用 `redis-cli SLOWLOG GET` 或应用日志感受 **成本**（仅测试环境）。

### 验证标准

- 能指出：**HGETALL 与 Java Map 内容一致**。  
- 能说明：**本地缓存为何会出现短暂不一致**。

### 记录建议

- 一条团队规范：**生产禁止对超大 RMap 做 size/全量遍历，除非有审批与限流**。

**上一篇**：[第五章（分篇一）RBucket](06-RBucket单值与配置.md)｜**下一篇**：[第五章（分篇三）Bloom 与基数估算](08-Bloom与基数估算.md)｜**下一章**：[第六章导览](10-分布式集合选型.md)
