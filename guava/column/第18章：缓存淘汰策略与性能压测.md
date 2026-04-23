# 第 18 章：缓存淘汰策略与性能压测

## 1 项目背景

在缓存系统上线运行一段时间后，工程师小陈发现了一些奇怪的问题。明明配置了最大容量 1 万条，但某些热点商品却频繁被淘汰；促销期间缓存命中率从 85% 暴跌到 40%，数据库压力剧增；有时候明明还有内存，缓存却被清理了。

追查发现，问题的根源在于缓存淘汰策略的配置不当。团队默认使用了容量限制，但没有理解 W-TinyLFU 算法的行为；也没有针对业务特点（促销期流量模式突变）调整策略参数。

**业务场景**：缓存容量规划、淘汰策略选择、性能压测、容量调优等需要精细化缓存管理的场景。

**痛点放大**：
- **淘汰策略不理解**：不清楚 LRU、LFU、W-TinyLFU 的区别和适用场景。
- **容量设置盲目**：凭感觉设置 maxSize，没有数据支撑。
- **性能瓶颈难定位**：不知道瓶颈在加载、序列化还是网络。
- **突发流量应对差**：缓存命中率在流量突增时暴跌。
- **缺乏压测数据**：没有基准数据指导容量规划。

如果没有科学的缓存策略配置和性能评估，系统将难以应对生产环境的复杂情况。

**技术映射**：Guava Cache 使用 W-TinyLFU 淘汰算法，支持多种容量限制方式（条目数、权重、内存大小），提供详细的统计信息用于性能分析。

---

## 2 项目设计

**场景**：容量规划评审会，讨论缓存性能优化。

---

**小胖**：（看着命中率曲线）"我说，这缓存命中率波动也太大了！平时 85%，促销一来直接跌到 40%。这不就跟食堂平时米饭够用，一到中午高峰期就不够了一样吗？"

**小白**：（分析监控）"问题在于我们的容量设置是静态的，没有考虑访问模式的改变。Guava 用的是 W-TinyLFU 算法，它会根据访问频率和最近性来淘汰，但我们的容量上限卡死了。"

**大师**：（在白板上画算法对比）"三种主要淘汰策略的区别：

```
LRU (Least Recently Used)：
  - 淘汰最久未访问的
  - 优点：对突发流量友好
  - 缺点：对周期性扫描不友好（比如全量备份）

LFU (Least Frequently Used)：
  - 淘汰访问次数最少的
  - 优点：保护热点数据
  - 缺点：对新数据不友好（需要积累次数）

W-TinyLFU (Guava 使用)：
  - 窗口内的 LFU + 历史 LFU
  - 结合两者优点
  - 能抵抗周期性扫描攻击
```

**技术映射**：W-TinyLFU 就像是'有记忆的智能门卫'——它不仅记得谁最近来过，还统计谁来的频率高，新来的常客也有机会进入 VIP 名单。"

**小胖**："那容量到底怎么设？"

**小白**："容量规划要考虑几个因素：

```java
// 1. 条目数限制（简单场景）
.maximumSize(10000)

// 2. 权重限制（条目大小不一）
.weigher(new Weigher<String, Product>() {
    public int weigh(String key, Product value) {
        // 根据商品详情大小估算权重
        return estimateSize(value);  // 返回字节数/1024 等
    }
})
.maximumWeight(100 * 1024 * 1024)  // 100MB

// 3. 基于时间的软引用（内存敏感）
.softValues()  // 内存不足时 JVM 可回收
```

**大师**："还要考虑**命中率公式**：

```
理想命中率 = 1 - (工作集大小 / 缓存容量)

工作集：实际访问的唯一 key 数量
缓存容量：能存储的条目数

如果工作集 5000，容量 10000，理论命中率约 50%
如果工作集 5000，容量 50000，理论命中率约 90%
```

**技术映射**：缓存容量规划就像是'估算食堂要准备多少菜'——你需要知道会有多少人来、每人吃多少、翻台率多高，而不是拍脑袋决定。"

**小胖**："那怎么压测？"

**小白**："可以构建模拟场景：

```java
// 1. 准备测试数据
List<String> hotKeys = generateHotKeys(100);      // 热点数据
List<String> warmKeys = generateWarmKeys(1000);   // 温数据
List<String> coldKeys = generateColdKeys(10000); // 冷数据

// 2. 模拟访问模式
for (int i = 0; i < 100000; i++) {
    String key = selectKeyByDistribution(
        hotKeys, 0.8,   // 80% 热点
        warmKeys, 0.15, // 15% 温数据
        coldKeys, 0.05  // 5% 冷数据
    );
    cache.get(key);
}

// 3. 分析统计
CacheStats stats = cache.stats();
double hitRate = stats.hitRate();
long avgLoadTime = stats.averageLoadPenalty();
```

**大师**："还要注意**并发压测**：

```java
ExecutorService executor = Executors.newFixedThreadPool(50);
CountDownLatch latch = new CountDownLatch(10000);

for (int i = 0; i < 10000; i++) {
    executor.submit(() -> {
        cache.get(selectRandomKey());
        latch.countDown();
    });
}

latch.await();
// 分析并发下的命中率
```

**技术映射**：压测就像是'模拟大客流演练'——你需要知道在真实压力下，系统哪里会先撑不住，是数据库连接池、缓存加载速度，还是内存容量。"

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

### 分步实现：缓存性能压测工具

**步骤目标**：构建缓存容量规划和性能压测工具。

**代码实现**：

```java
package com.example.guava.demo;

import com.google.common.cache.*;
import com.google.common.collect.*;

import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;

/**
 * 缓存性能压测工具
 */
public class CacheBenchmarkTool {

    // 测试场景配置
    public static class BenchmarkConfig {
        int cacheSize;           // 缓存容量
        int hotKeyCount;         // 热点 key 数量
        int warmKeyCount;        // 温数据 key 数量
        int coldKeyCount;        // 冷数据 key 数量
        double hotAccessRatio;   // 热点访问比例
        double warmAccessRatio;  // 温数据访问比例
        int totalRequests;       // 总请求数
        int concurrency;         // 并发数

        public BenchmarkConfig(int cacheSize, int hotKeys, int warmKeys, int coldKeys,
                              double hotRatio, double warmRatio, int requests, int concurrency) {
            this.cacheSize = cacheSize;
            this.hotKeyCount = hotKeys;
            this.warmKeyCount = warmKeys;
            this.coldKeyCount = coldKeys;
            this.hotAccessRatio = hotRatio;
            this.warmAccessRatio = warmRatio;
            this.totalRequests = requests;
            this.concurrency = concurrency;
        }
    }

    // 压测结果
    public static class BenchmarkResult {
        double hitRate;
        long avgLoadTimeMs;
        long totalTimeMs;
        long memoryUsageMb;
        int evictionCount;
        int loadSuccessCount;
        int loadExceptionCount;

        @Override
        public String toString() {
            return String.format(
                "BenchmarkResult{\n" +
                "  命中率: %.2f%%\n" +
                "  平均加载时间: %d ms\n" +
                "  总耗时: %d ms\n" +
                "  预估内存: %d MB\n" +
                "  淘汰次数: %d\n" +
                "  加载成功: %d\n" +
                "  加载失败: %d\n}",
                hitRate * 100, avgLoadTimeMs, totalTimeMs, memoryUsageMb,
                evictionCount, loadSuccessCount, loadExceptionCount
            );
        }
    }

    private LoadingCache<String, String> buildCache(int maxSize) {
        return CacheBuilder.newBuilder()
            .maximumSize(maxSize)
            .recordStats()
            .removalListener(notification -> {
                // 可以统计淘汰
            })
            .build(new CacheLoader<String, String>() {
                @Override
                public String load(String key) {
                    // 模拟加载延迟
                    simulateLoadDelay();
                    return "Value-" + key;
                }
            });
    }

    private void simulateLoadDelay() {
        try {
            Thread.sleep(5);  // 模拟 5ms 加载延迟
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    /**
     * 执行压测
     */
    public BenchmarkResult runBenchmark(BenchmarkConfig config) throws Exception {
        // 准备测试数据
        List<String> hotKeys = generateKeys("H", config.hotKeyCount);
        List<String> warmKeys = generateKeys("W", config.warmKeyCount);
        List<String> coldKeys = generateKeys("C", config.coldKeyCount);

        // 构建缓存
        LoadingCache<String, String> cache = buildCache(config.cacheSize);

        // 预热缓存（先加载热点数据）
        System.out.println("预热缓存...");
        for (String key : hotKeys) {
            cache.get(key);
        }

        // 压测开始
        System.out.println("开始压测: " + config.totalRequests + " 请求, " + config.concurrency + " 并发");
        long startTime = System.currentTimeMillis();

        ExecutorService executor = Executors.newFixedThreadPool(config.concurrency);
        CountDownLatch latch = new CountDownLatch(config.totalRequests);

        for (int i = 0; i < config.totalRequests; i++) {
            executor.submit(() -> {
                try {
                    String key = selectKeyByDistribution(
                        hotKeys, config.hotAccessRatio,
                        warmKeys, config.warmAccessRatio,
                        coldKeys
                    );
                    cache.get(key);
                } catch (Exception e) {
                    System.err.println("请求异常: " + e.getMessage());
                } finally {
                    latch.countDown();
                }
            });
        }

        latch.await();
        executor.shutdown();

        long totalTime = System.currentTimeMillis() - startTime;

        // 收集结果
        CacheStats stats = cache.stats();
        BenchmarkResult result = new BenchmarkResult();
        result.hitRate = stats.hitRate();
        result.avgLoadTimeMs = stats.averageLoadPenalty() / 1_000_000;
        result.totalTimeMs = totalTime;
        result.memoryUsageMb = estimateMemoryUsage(config.cacheSize);
        result.evictionCount = stats.evictionCount();
        result.loadSuccessCount = stats.loadSuccessCount();
        result.loadExceptionCount = stats.loadExceptionCount();

        return result;
    }

    private List<String> generateKeys(String prefix, int count) {
        List<String> keys = new ArrayList<>();
        for (int i = 0; i < count; i++) {
            keys.add(prefix + String.format("%06d", i));
        }
        return keys;
    }

    private String selectKeyByDistribution(List<String> hotKeys, double hotRatio,
                                           List<String> warmKeys, double warmRatio,
                                           List<String> coldKeys) {
        double rand = ThreadLocalRandom.current().nextDouble();
        if (rand < hotRatio) {
            return hotKeys.get(ThreadLocalRandom.current().nextInt(hotKeys.size()));
        } else if (rand < hotRatio + warmRatio) {
            return warmKeys.get(ThreadLocalRandom.current().nextInt(warmKeys.size()));
        } else {
            return coldKeys.get(ThreadLocalRandom.current().nextInt(coldKeys.size()));
        }
    }

    private long estimateMemoryUsage(int cacheSize) {
        // 粗略估算：每个缓存项约 100 字节
        return (cacheSize * 100) / (1024 * 1024);
    }

    /**
     * 容量规划建议
     */
    public void capacityPlanningAdvice(int workingSetSize, double targetHitRate) {
        // 理论公式：命中率 = 1 - (工作集 / 缓存容量)
        // 推导：缓存容量 = 工作集 / (1 - 命中率)

        double requiredCapacity = workingSetSize / (1 - targetHitRate);

        System.out.println("=== 容量规划建议 ===");
        System.out.println("工作集大小: " + workingSetSize);
        System.out.println("目标命中率: " + (targetHitRate * 100) + "%");
        System.out.println("建议缓存容量: " + (int) requiredCapacity);
        System.out.println();

        // 不同命中率对应的容量
        System.out.println("不同命中率对应的容量需求:");
        for (double hr = 0.5; hr <= 0.99; hr += 0.1) {
            double capacity = workingSetSize / (1 - hr);
            System.out.println(String.format("  命中率 %.0f%%: 需要容量 %.0f", hr * 100, capacity));
        }
    }

    // ========== 测试入口 ==========
    public static void main(String[] args) throws Exception {
        CacheBenchmarkTool benchmark = new CacheBenchmarkTool();

        // 容量规划建议
        benchmark.capacityPlanningAdvice(5000, 0.9);
        System.out.println();

        // 场景 1：常规访问模式
        System.out.println("=== 场景 1: 常规访问模式 ===");
        BenchmarkConfig normalConfig = new BenchmarkConfig(
            10000,     // 缓存容量
            100,       // 热点 100
            1000,      // 温数据 1000
            10000,     // 冷数据 10000
            0.8,       // 80% 热点
            0.15,      // 15% 温数据
            50000,     // 50000 请求
            50         // 50 并发
        );
        BenchmarkResult normalResult = benchmark.runBenchmark(normalConfig);
        System.out.println(normalResult);
        System.out.println();

        // 场景 2：缓存容量不足
        System.out.println("=== 场景 2: 缓存容量不足 ===");
        BenchmarkConfig smallCacheConfig = new BenchmarkConfig(
            500,       // 缓存容量很小
            100, 1000, 10000,
            0.8, 0.15,
            50000, 50
        );
        BenchmarkResult smallResult = benchmark.runBenchmark(smallCacheConfig);
        System.out.println(smallResult);
        System.out.println();

        // 场景 3：突发冷数据访问（扫描攻击模拟）
        System.out.println("=== 场景 3: 突发冷数据访问 ===");
        BenchmarkConfig scanConfig = new BenchmarkConfig(
            10000,
            100, 1000, 10000,
            0.1,       // 热点访问降 10%
            0.2,
            50000, 50
        );
        BenchmarkResult scanResult = benchmark.runBenchmark(scanConfig);
        System.out.println(scanResult);

        // 场景 4：高并发压力测试
        System.out.println("\n=== 场景 4: 高并发压力测试 ===");
        BenchmarkConfig highConcurrencyConfig = new BenchmarkConfig(
            10000,
            100, 1000, 10000,
            0.8, 0.15,
            100000,    // 10万请求
            200        // 200 并发
        );
        BenchmarkResult hcResult = benchmark.runBenchmark(highConcurrencyConfig);
        System.out.println(hcResult);
    }
}
```

### 测试验证

```java
package com.example.guava.demo;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

public class CacheBenchmarkToolTest {

    @Test
    public void testCapacityPlanning() {
        CacheBenchmarkTool tool = new CacheBenchmarkTool();
        // 工作集 1000，目标命中率 90%
        // 理论容量 = 1000 / (1 - 0.9) = 10000
        tool.capacityPlanningAdvice(1000, 0.9);
    }

    @Test
    public void testSmallBenchmark() throws Exception {
        CacheBenchmarkTool tool = new CacheBenchmarkTool();
        CacheBenchmarkTool.BenchmarkConfig config = new CacheBenchmarkTool.BenchmarkConfig(
            100,       // 小缓存
            10,        // 热点
            50,        // 温数据
            100,       // 冷数据
            0.8,       // 热点访问
            0.15,
            1000,      // 请求数
            10         // 并发
        );

        CacheBenchmarkTool.BenchmarkResult result = tool.runBenchmark(config);
        assertNotNull(result);
        assertTrue(result.hitRate >= 0 && result.hitRate <= 1);
        assertTrue(result.totalTimeMs > 0);
    }

    @Test
    public void testHitRateFormula() {
        // 验证命中率公式的合理性
        // 工作集 100，缓存容量 1000，理论命中率约 90%
        int workingSet = 100;
        int capacity = 1000;
        double theoreticalHitRate = 1.0 - ((double) workingSet / capacity);
        assertTrue(theoreticalHitRate > 0.8);
    }
}
```

### 可能遇到的坑及解决方法

| 坑点 | 现象 | 解决方法 |
|------|------|----------|
| 压测数据不随机 | 命中率虚高 | 使用 ThreadLocalRandom 保证随机性 |
| JVM 预热不足 | 首次压测结果偏差大 | 先执行 warm-up 轮次 |
| GC 影响结果 | 压测时触发 GC，停顿 | 增加堆内存，或单独分析 GC 日志 |
| 工作集计算错误 | 唯一 key 数估算错误 | 用 HyperLogLog 或采样估算 |

---

## 4 项目总结

### W-TinyLFU 算法优势

| 场景 | W-TinyLFU | 纯 LRU | 纯 LFU |
|------|-----------|--------|--------|
| 突发热点 | ★★★★★ 快速识别 | ★★★★ 可以 | ★★ 需积累 |
| 周期性扫描 | ★★★★★ 抵抗攻击 | ★★ 命中率暴跌 | ★★★★ 较好 |
| 长尾数据 | ★★★★ 较好处理 | ★★★ 可能误删 | ★★★★★ 最优 |
| 新数据机会 | ★★★★ 有窗口期 | ★★★★★ 直接进入 | ★★ 难进入 |

### 容量规划公式

```
理论命中率 = 1 - (工作集 / 缓存容量)

实际建议：
- 目标命中率 90%：容量 = 工作集 × 10
- 目标命中率 95%：容量 = 工作集 × 20
- 目标命中率 99%：容量 = 工作集 × 100
```

### 压测最佳实践

1. **模拟真实访问模式**：热点、温数据、冷数据比例要准
2. **包含并发测试**：单线程压测结果不可信
3. **考虑数据分布**：Zipf 分布比均匀分布更真实
4. **长期运行测试**：观察随时间推移的命中率变化
5. **监控内存和 GC**：缓存不是免费的

### 思考题答案（第 17 章思考题 1）

> **问题**：如何设计缓存加载的熔断器？

**答案**：可以使用 Guava 的 `RateLimiter` 配合异常计数：

```java
public class CircuitBreakerLoader extends CacheLoader<String, Product> {
    private final RateLimiter rateLimiter = RateLimiter.create(100); // QPS 限制
    private final AtomicInteger failCount = new AtomicInteger(0);
    private volatile boolean open = false;

    @Override
    public Product load(String key) throws Exception {
        if (open) {
            throw new CircuitBreakerOpenException();
        }
        
        if (!rateLimiter.tryAcquire()) {
            failCount.incrementAndGet();
            if (failCount.get() > 10) {
                open = true;  // 熔断打开
            }
            throw new LoadException("Too many requests");
        }
        
        // 正常加载...
    }
}
```

### 新思考题

1. 如何设计一个自适应缓存容量调整系统，根据实时命中率动态调整容量？
2. 比较 W-TinyLFU 和 Redis 的 LFU 实现在细节上的差异。

### 推广计划提示

**开发**：
- 上线前必须做容量规划计算
- 定期进行压测验证
- 根据业务特点选择淘汰策略

**测试**：
- 构建真实访问模式的压测场景
- 测试突发流量下的命中率
- 监控内存使用和 GC 频率

**运维**：
- 实时采集命中率、加载时间指标
- 设置命中率下降告警
- 定期容量评估和调整
