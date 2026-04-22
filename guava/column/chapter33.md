# 第 33 章：LocalCache 内核机制深拆（段、队列、清理）

## 1 项目背景

在极端性能调优场景中，资深工程师小冯需要深入理解 Guava Cache 的内部机制。分析高并发下的长尾延迟问题，定位到 LocalCache 的段锁竞争和清理策略。

## 2 项目设计

**大师**："LocalCache 核心机制：

```java
// 分段设计（类似 ConcurrentHashMap）
Segment[] segments;  // 默认 4 个段
int segmentMask;       // 用于定位段

// 每个 Segment 独立加锁
// - 降低竞争
// - 独立过期清理

// 引用队列清理
ReferenceQueue<K> keyReferenceQueue;
ReferenceQueue<V> valueReferenceQueue;
// 弱/软引用对象被 GC 后进入队列，异步清理

//  LRU 队列（访问顺序）
Queue<ReferenceEntry<K, V>> accessQueue;
Queue<ReferenceEntry<K, V>> writeQueue;
```

**技术映射**：分段设计就像是'多仓库管理'——每个仓库独立运营，互不影响。"

## 3 项目实战

```java
// 段数调优
CacheBuilder.newBuilder()
    .concurrencyLevel(16)  // 增加段数减少竞争
    .build();

// 监控段竞争（通过 JMX 或自定义）
public class CacheSegmentMonitor {
    private final LocalCache<?, ?> localCache;
    
    public int getSegmentCount() {
        // localCache.segments.length
        return 4;  // 默认
    }
    
    // 分析段分布均匀性
    public void analyzeSegmentDistribution() {
        // 检查 key hash 在段间的分布
    }
}

// 清理策略源码分析
// expireEntries：遍历 writeQueue，移除过期条目
// evictEntries：按访问顺序移除，直到低于容量
```

## 4 项目总结

### 内核调优参数

| 参数 | 作用 | 调优建议 |
|------|------|----------|
| concurrencyLevel | 段数 | CPU 核心数 2-4 倍 |
| initialCapacity | 初始容量 | 减少扩容开销 |
| maximumSize | 容量上限 | 配合内存限制 |

### 常见问题

1. **段竞争**：段数过少，热点 key 集中
2. **清理延迟**：过期条目未及时清理
3. **内存泄露**：弱引用未及时回收
