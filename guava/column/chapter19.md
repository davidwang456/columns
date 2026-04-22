# 第 19 章：RemovalListener 与缓存可观测性

## 1 项目背景

在缓存系统稳定运行后，运维团队提出了新的需求：需要知道缓存里面有多少数据、命中率是多少、哪些数据被淘汰了、为什么被淘汰。这些信息对于容量规划、性能优化和故障排查至关重要。

工程师小陈发现，虽然 Guava Cache 提供了统计功能，但团队并没有充分利用。更麻烦的是，当缓存数据被移除时，系统需要做一些清理工作（如关闭资源、记录日志），但不知道何时何地执行这些操作。

**业务场景**：缓存监控、告警、数据清理、审计日志等需要了解缓存内部状态的场景。

**痛点放大**：
- **缓存黑盒**：不知道缓存里有什么，命中率多少。
- **移除事件丢失**：数据被淘汰时无法及时感知。
- **监控指标缺失**：缺乏命中率、加载时间等关键指标。
- **告警延迟**：缓存失效导致数据库压力时才发现问题。
- **调试困难**：缓存行为不透明，问题难定位。

如果没有完善的缓存可观测性方案，系统将处于"盲人摸象"的状态。

**技术映射**：Guava Cache 提供了 `RemovalListener` 监听移除事件、`CacheStats` 统计指标、以及 `asMap()` 视图访问缓存内容，可以构建完整的可观测性方案。

---

## 2 项目设计

**场景**：运维需求评审会，讨论缓存监控方案。

---

**小胖**：（看着缓存监控仪表盘）"我说，这缓存也太黑盒了吧！里面有多少数据不知道，什么被淘汰了也不知道。这不就跟食堂后厨一样，只知道菜送出来了，不知道锅里还剩多少？"

**小白**：（点头）"Guava Cache 其实提供了很丰富的观测能力，我们只是没用起来。`

**大师**：（在白板上画架构）"完整的可观测性包括三个层面：

```
1. 指标（Metrics）：命中率、加载时间、大小等
2. 日志（Logs）：移除事件、加载事件等
3. 追踪（Traces）：缓存操作链路
```

Guava 提供了：
- `recordStats()` 开启统计
- `RemovalListener` 监听移除
- `asMap()` 查看缓存内容

**技术映射**：可观测性就像是给缓存装上'摄像头'和'仪表盘'——你可以实时看到里面发生了什么，而不是出了问题才去猜。"

**小胖**："那 `RemovalListener` 具体能做什么？"

**小白**："`RemovalListener` 在缓存项被移除时触发，可以获取移除原因：

```java
CacheBuilder.newBuilder()
    .removalListener(new RemovalListener<String, Product>() {
        @Override
        public void onRemoval(RemovalNotification<String, Product> notification) {
            RemovalCause cause = notification.getCause();
            
            switch (cause) {
                case EXPLICIT:      // 手动移除 (invalidate)
                    log.info("手动移除: " + notification.getKey());
                    break;
                case REPLACED:      // 被新值替换
                    log.info("值被替换: " + notification.getKey());
                    break;
                case COLLECTED:     // 垃圾回收
                    log.warn("被 GC 回收: " + notification.getKey());
                    break;
                case EXPIRED:       // 过期
                    log.info("过期移除: " + notification.getKey());
                    break;
                case SIZE:          // 容量超限
                    log.info("容量淘汰: " + notification.getKey());
                    break;
            }
            
            // 清理资源
            Product product = notification.getValue();
            if (product != null) {
                product.cleanup();
            }
        }
    })
```

**大师**："还要注意 `RemovalListener` 的执行是**异步**的，默认在 `ForkJoinPool.commonPool()` 中执行。如果需要同步处理或自定义线程池：

```java
.removalListener(listener, executor)  // 指定执行器
```

**技术映射**：`RemovalListener` 就像是缓存的'离职交接员'——当数据'离开'缓存时，它会告诉你原因，并给你机会做善后处理。"

**小胖**："那统计指标怎么用？"

**小白**："统计指标很丰富：

```java
CacheStats stats = cache.stats();

// 命中率相关
long requestCount = stats.requestCount();      // 总请求数
long hitCount = stats.hitCount();              // 命中次数
double hitRate = stats.hitRate();              // 命中率 (0.0 ~ 1.0)
long missCount = stats.missCount();            // 未命中次数

// 加载相关
long loadCount = stats.loadCount();            // 加载次数
long loadSuccessCount = stats.loadSuccessCount();  // 加载成功
long loadExceptionCount = stats.loadExceptionCount(); // 加载失败
long totalLoadTime = stats.totalLoadTime();    // 总加载时间（纳秒）
double avgLoadPenalty = stats.averageLoadPenalty();  // 平均加载时间

// 淘汰相关
long evictionCount = stats.evictionCount();    // 淘汰次数
```

**大师**："还可以配合监控框架（如 Micrometer、Prometheus）导出指标：

```java
// 定期上报到监控系统
scheduler.scheduleAtFixedRate(() -> {
    CacheStats stats = cache.stats();
    metrics.gauge("cache.hit_rate", stats.hitRate());
    metrics.counter("cache.evictions", stats.evictionCount());
    // ...
}, 0, 1, TimeUnit.MINUTES);
```

**技术映射**：统计指标就像是缓存的'体检报告'——它告诉你缓存是否健康，哪里需要优化，什么时候该扩容。"

---

## 3 项目实战

### 环境准备

```xml
<dependency>
    <groupId>com.google.guava</groupId>
    <artifactId>guava</artifactId>
    <version>33.0.0-jre</version>
</dependency>
```

### 分步实现：可观测的缓存系统

**步骤目标**：构建带完整监控和告警的缓存系统。

**代码实现**：

```java
package com.example.guava.demo;

import com.google.common.cache.*;

import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicLong;

/**
 * 可观测的缓存系统 - 使用 RemovalListener 和 CacheStats
 */
public class ObservableCacheSystem {

    private LoadingCache<String, CacheEntry> cache;
    private ExecutorService removalExecutor;
    
    // 指标收集
    private final MetricsCollector metrics = new MetricsCollector();

    public ObservableCacheSystem() {
        this.removalExecutor = Executors.newSingleThreadExecutor(r -> {
            Thread t = new Thread(r, "removal-listener");
            t.setDaemon(true);
            return t;
        });
        
        buildCache();
        startMetricsReporter();
    }

    private void buildCache() {
        cache = CacheBuilder.newBuilder()
            .maximumSize(1000)
            .expireAfterWrite(5, TimeUnit.MINUTES)
            .recordStats()
            .removalListener(this::onRemoval, removalExecutor)
            .build(new CacheLoader<String, CacheEntry>() {
                @Override
                public CacheEntry load(String key) throws Exception {
                    metrics.recordLoad();
                    long startTime = System.nanoTime();
                    
                    try {
                        CacheEntry entry = loadFromDatabase(key);
                        long loadTime = System.nanoTime() - startTime;
                        metrics.recordLoadSuccess(loadTime);
                        return entry;
                    } catch (Exception e) {
                        metrics.recordLoadFailure();
                        throw e;
                    }
                }
            });
    }

    private void onRemoval(RemovalNotification<String, CacheEntry> notification) {
        RemovalCause cause = notification.getCause();
        String key = notification.getKey();
        CacheEntry entry = notification.getValue();
        
        // 按原因分类统计
        metrics.recordRemoval(cause);
        
        // 记录详细日志
        switch (cause) {
            case EXPLICIT:
                System.out.println("[移除-手动] Key: " + key);
                break;
            case REPLACED:
                System.out.println("[移除-替换] Key: " + key);
                break;
            case COLLECTED:
                System.err.println("[移除-GC] Key: " + key + " (可能是内存不足)");
                break;
            case EXPIRED:
                System.out.println("[移除-过期] Key: " + key);
                break;
            case SIZE:
                System.out.println("[移除-容量] Key: " + key);
                break;
        }
        
        // 清理资源
        if (entry != null) {
            entry.cleanup();
        }
    }

    private CacheEntry loadFromDatabase(String key) {
        // 模拟加载
        try {
            Thread.sleep(10);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
        return new CacheEntry(key, "Value-" + key, System.currentTimeMillis());
    }

    /**
     * 获取缓存项
     */
    public CacheEntry get(String key) {
        try {
            return cache.get(key);
        } catch (ExecutionException e) {
            return null;
        }
    }

    /**
     * 使缓存失效
     */
    public void invalidate(String key) {
        cache.invalidate(key);
    }

    /**
     * 获取当前统计
     */
    public CacheStats getStats() {
        return cache.stats();
    }

    /**
     * 获取缓存快照（调试用）
     */
    public Map<String, CacheEntry> getSnapshot() {
        return new HashMap<>(cache.asMap());
    }

    /**
     * 获取缓存大小
     */
    public long getSize() {
        return cache.size();
    }

    /**
     * 打印详细报告
     */
    public void printReport() {
        CacheStats stats = cache.stats();
        
        System.out.println("\n========== 缓存报告 ==========");
        System.out.println("当前大小: " + cache.size());
        System.out.println("总请求数: " + stats.requestCount());
        System.out.println("命中次数: " + stats.hitCount());
        System.out.println("未命中数: " + stats.missCount());
        System.out.println("命中率: " + String.format("%.2f%%", stats.hitRate() * 100));
        System.out.println();
        System.out.println("加载次数: " + stats.loadCount());
        System.out.println("加载成功: " + stats.loadSuccessCount());
        System.out.println("加载失败: " + stats.loadExceptionCount());
        System.out.println("平均加载时间: " + (stats.averageLoadPenalty() / 1_000_000) + " ms");
        System.out.println();
        System.out.println("淘汰次数: " + stats.evictionCount());
        System.out.println("==============================\n");
        
        // 按原因的淘汰统计
        metrics.printRemovalStats();
    }

    private void startMetricsReporter() {
        ScheduledExecutorService scheduler = Executors.newSingleThreadScheduledExecutor();
        scheduler.scheduleAtFixedRate(() -> {
            CacheStats stats = cache.stats();
            
            // 模拟上报到监控系统
            if (stats.hitRate() < 0.5) {
                System.err.println("[告警] 命中率低于 50%: " + String.format("%.2f%%", stats.hitRate() * 100));
            }
            
            if (stats.loadExceptionCount() > 10) {
                System.err.println("[告警] 加载失败次数过多: " + stats.loadExceptionCount());
            }
            
        }, 1, 1, TimeUnit.MINUTES);
    }

    // ========== 领域模型 ==========
    public static class CacheEntry {
        private final String key;
        private final String value;
        private final long createTime;

        public CacheEntry(String key, String value, long createTime) {
            this.key = key;
            this.value = value;
            this.createTime = createTime;
        }

        public String getKey() { return key; }
        public String getValue() { return value; }
        public long getCreateTime() { return createTime; }

        public void cleanup() {
            System.out.println("  清理资源: " + key);
        }
    }

    // 指标收集器
    public static class MetricsCollector {
        private final Map<RemovalCause, AtomicLong> removalStats = new ConcurrentHashMap<>();
        private final AtomicLong loadCount = new AtomicLong(0);
        private final AtomicLong loadSuccess = new AtomicLong(0);
        private final AtomicLong loadFailure = new AtomicLong(0);
        private final AtomicLong totalLoadTime = new AtomicLong(0);

        public void recordRemoval(RemovalCause cause) {
            removalStats.computeIfAbsent(cause, k -> new AtomicLong(0)).incrementAndGet();
        }

        public void recordLoad() {
            loadCount.incrementAndGet();
        }

        public void recordLoadSuccess(long nanoTime) {
            loadSuccess.incrementAndGet();
            totalLoadTime.addAndGet(nanoTime);
        }

        public void recordLoadFailure() {
            loadFailure.incrementAndGet();
        }

        public void printRemovalStats() {
            System.out.println("淘汰原因统计:");
            removalStats.forEach((cause, count) -> 
                System.out.println("  " + cause + ": " + count.get())
            );
        }
    }

    // ========== 测试入口 ==========
    public static void main(String[] args) throws Exception {
        ObservableCacheSystem cache = new ObservableCacheSystem();

        System.out.println("=== 正常访问测试 ===");
        for (int i = 0; i < 100; i++) {
            cache.get("KEY_" + (i % 20));  // 20 个热点 key
        }
        cache.printReport();

        System.out.println("=== 手动失效测试 ===");
        cache.invalidate("KEY_0");
        Thread.sleep(100);  // 等待 RemovalListener 执行

        System.out.println("\n=== 容量淘汰测试 ===");
        // 写入大量数据触发容量淘汰
        for (int i = 0; i < 2000; i++) {
            cache.get("COLD_" + i);
        }
        Thread.sleep(500);
        cache.printReport();

        System.out.println("\n=== 缓存快照 ===");
        Map<String, CacheEntry> snapshot = cache.getSnapshot();
        System.out.println("快照大小: " + snapshot.size());
        
        // 验证快照是视图（修改会影响原缓存）
        // snapshot.clear();  // 会清空缓存！

        cache.removalExecutor.shutdown();
    }
}
```

### 测试验证

```java
package com.example.guava.demo;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

import com.google.common.cache.RemovalCause;

public class ObservableCacheSystemTest {

    private ObservableCacheSystem cache;

    @BeforeEach
    public void setUp() {
        cache = new ObservableCacheSystem();
    }

    @Test
    public void testBasicOperation() {
        ObservableCacheSystem.CacheEntry entry = cache.get("test");
        assertNotNull(entry);
        assertEquals("test", entry.getKey());
    }

    @Test
    public void testCacheHit() {
        // 第一次加载
        cache.get("key1");
        
        // 第二次命中
        ObservableCacheSystem.CacheEntry entry = cache.get("key1");
        assertNotNull(entry);
        
        com.google.common.cache.CacheStats stats = cache.getStats();
        assertEquals(2, stats.requestCount());  // 2 次请求
        assertEquals(1, stats.hitCount());      // 1 次命中
    }

    @Test
    public void testStatsCollection() {
        // 触发多次加载
        for (int i = 0; i < 10; i++) {
            cache.get("key_" + i);
        }
        
        // 重复访问
        for (int i = 0; i < 10; i++) {
            cache.get("key_" + i);
        }
        
        com.google.common.cache.CacheStats stats = cache.getStats();
        assertEquals(20, stats.requestCount());
        assertEquals(10, stats.hitCount());  // 10 次命中
        assertEquals(10, stats.missCount()); // 10 次未命中
        assertTrue(stats.hitRate() > 0.4 && stats.hitRate() < 0.6);  // 约 50%
    }

    @Test
    public void testInvalidate() {
        cache.get("key1");
        cache.invalidate("key1");
        
        // 再次访问应该重新加载
        cache.get("key1");
        com.google.common.cache.CacheStats stats = cache.getStats();
        assertEquals(2, stats.loadCount());  // 加载了 2 次
    }

    @Test
    public void testSnapshot() {
        cache.get("key1");
        cache.get("key2");
        
        java.util.Map<String, ObservableCacheSystem.CacheEntry> snapshot = cache.getSnapshot();
        assertTrue(snapshot.size() >= 2);
    }
}
```

### 可能遇到的坑及解决方法

| 坑点 | 现象 | 解决方法 |
|------|------|----------|
| RemovalListener 执行延迟 | 移除后监听器未立即执行 | 使用自定义 ExecutorService |
| asMap() 视图误修改 | 修改快照影响原缓存 | 复制后再修改 |
| 统计精度 | requestCount 超过 Long 范围 | 定期调用 `cache.stats()` 重置（快照后新建缓存） |
| 告警抖动 | 命中率短暂波动触发告警 | 设置持续时间和阈值 |

---

## 4 项目总结

### 可观测性三层模型

```
┌─────────────────────────────────────────┐
│           业务层（Business）             │
│   - 缓存命中率对业务的影响               │
├─────────────────────────────────────────┤
│           应用层（Application）          │
│   - CacheStats 指标                      │
│   - RemovalListener 事件                 │
│   - asMap() 视图                        │
├─────────────────────────────────────────┤
│           系统层（System）               │
│   - JVM 内存使用                         │
│   - GC 频率                              │
│   - 线程池状态                           │
└─────────────────────────────────────────┘
```

### 关键指标清单

| 指标 | 正常范围 | 告警阈值 |
|------|----------|----------|
| 命中率 | > 80% | < 50% 持续 5 分钟 |
| 平均加载时间 | < 50ms | > 200ms |
| 加载失败率 | < 1% | > 10% |
| 淘汰率 | 平稳 | 突增 10 倍 |
| 缓存大小 | 接近 maxSize | 持续 100% |

### RemovalCause 使用场景

| 原因 | 触发条件 | 处理建议 |
|------|----------|----------|
| EXPLICIT | `invalidate()` | 记录审计日志 |
| REPLACED | `put()` 覆盖 | 版本冲突处理 |
| COLLECTED | GC 回收 | 内存不足告警 |
| EXPIRED | 时间过期 | 正常清理 |
| SIZE | 容量超限 | 扩容评估 |

### 思考题答案（第 18 章思考题 1）

> **问题**：如何设计自适应缓存容量调整？

**答案**：基于命中率反馈的 PID 控制器：

```java
if (hitRate < targetRate - threshold) {
    // 命中率过低，尝试扩容
    newCapacity = currentCapacity * 1.5;
} else if (hitRate > targetRate + threshold && memoryUsage > 0.8) {
    // 命中率过高且内存紧张，可以缩容
    newCapacity = currentCapacity * 0.8;
}
// 重建缓存（注意：会丢失数据）
```

### 新思考题

1. 如何设计一个缓存分析工具，自动识别热点数据和冷数据，给出容量优化建议？
2. 比较 Guava Cache 的可观测性与 Caffeine、Redis 的差异。

### 推广计划提示

**开发**：
- 所有生产缓存必须开启统计
- RemovalListener 做好资源清理
- 避免在监听器中做耗时操作

**测试**：
- 验证监听器执行
- 测试统计准确性
- 模拟各种移除原因

**运维**：
- 建立缓存指标基线
- 设置命中率、加载时间告警
- 定期分析淘汰原因分布
