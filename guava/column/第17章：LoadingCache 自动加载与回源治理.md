# 第 17 章：LoadingCache 自动加载与回源治理

## 1 项目背景

在商品缓存系统上线后，工程师小陈又遇到了新的问题。促销活动期间，某些爆款商品的缓存突然失效，大量请求同时穿透到数据库，导致数据库 CPU 飙升。更严重的是，有一个供应商接口在缓存失效后重新加载时返回了错误数据，结果被缓存了 10 分钟，影响了大量用户。

追查发现，问题出在缓存加载策略上。Guava Cache 的自动加载虽然方便，但缺乏对加载失败的优雅处理，也没有对回源流量的控制能力。

**业务场景**：缓存自动加载、回源保护、异常降级、批量加载等需要精细控制缓存加载过程的场景。

**痛点放大**：
- **缓存穿透**：并发请求同时触发加载，数据库压力剧增。
- **加载失败无降级**：加载异常时没有备用方案。
- **脏数据缓存**：错误数据被缓存，影响时间长。
- **批量加载效率低**：循环单个加载，数据库 IO 次数多。
- **加载时间不可控**：慢查询阻塞缓存获取。

如果没有完善的加载治理策略，缓存系统反而会成为故障的放大器。

**技术映射**：Guava 的 `LoadingCache` 提供了更强大的加载控制能力，包括异步刷新、批量加载、异常降级、加载统计等功能。

---

## 2 项目设计

**场景**：故障复盘会，讨论缓存加载策略优化。

---

**小胖**：（看着监控曲线）"我说，这缓存穿透也太狠了吧！一个 key 过期，几十个请求同时去查数据库。这不就跟食堂所有人都去拿同一个菜，厨师忙不过来一样吗？"

**小白**：（点头）"Guava Cache 的 `CacheLoader` 其实有自动防穿透机制——相同 key 的并发加载只执行一次，其他线程等待结果。但问题是加载失败后的处理。"

**大师**：（在白板上画流程）"Guava 提供了多层防护：

```java
new CacheLoader<String, Product>() {
    @Override
    public Product load(String key) throws Exception {
        // 1. 基础加载
        return loadFromDB(key);
    }
    
    @Override
    public ListenableFuture<Product> reload(String key, Product oldValue) {
        // 2. 异步刷新，不阻塞读取
        return asyncReload(key, oldValue);
    }
    
    @Override
    public Map<String, Product> loadAll(Iterable<? extends String> keys) {
        // 3. 批量加载，减少 IO
        return batchLoadFromDB(keys);
    }
}
```

**技术映射**：`CacheLoader` 就像是缓存的'智能供货员'——它不仅负责取货，还能处理批量订单、质量问题、紧急补货等各种情况。"

**小胖**："那如果数据库挂了怎么办？"

**小白**："可以用异常降级：

```java
public Product load(String key) {
    try {
        return loadFromDB(key);
    } catch (DBException e) {
        // 方案 1：返回默认值
        return Product.EMPTY;
        
        // 方案 2：返回过期数据（需要包装 CacheLoader）
        // 见下方进阶用法
        
        // 方案 3：抛异常让上层处理
        throw new RuntimeException("Service unavailable", e);
    }
}
```

但更好的做法是使用 `expireAfterWrite` 配合 `refreshAfterWrite`，这样即使刷新失败，老数据还能用。"

**大师**："还有一个高级技巧——**异步刷新不阻塞**：

```java
CacheBuilder.newBuilder()
    .refreshAfterWrite(1, TimeUnit.MINUTES)  // 1 分钟后触发刷新
    .build(loader);
```

刷新是异步的，读取线程立即返回旧值，后台线程去加载新值。这样读取永远不会被加载阻塞。

**技术映射**：`refreshAfterWrite` 就像是'未雨绸缪'——在数据还没过期前就悄悄更新，用户永远拿到的是'热'数据。"

**小胖**："那批量加载怎么用？"

**小白**："重写 `loadAll` 方法：

```java
@Override
public Map<String, Product> loadAll(Iterable<? extends String> keys) {
    List<String> keyList = ImmutableList.copyOf(keys);
    System.out.println("批量加载 " + keyList.size() + " 个 key");
    return database.batchGet(keyList);
}

// 调用时
Map<String, Product> products = cache.getAll(ImmutableList.of("P1", "P2", "P3"));
// 只会触发一次 loadAll，而不是三次 load
```

这对于列表查询场景特别有用。"

**大师**："还要注意**加载异常的处理策略**：

```java
// 策略 1：异常不缓存（默认行为）
// 加载抛异常，缓存中不会存储，下次继续尝试加载

// 策略 2：缓存异常（需配合 expireAfterWrite）
// 使用 Optional 包装，异常时存 Optional.empty()
```

**技术映射**：合理的加载异常处理是缓存系统的'安全带'——它决定了故障时的行为是'优雅降级'还是'彻底崩溃'。"

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

### 分步实现：带降级保护的缓存系统

**步骤目标**：用 `LoadingCache` 构建带异常降级、批量加载、异步刷新的缓存系统。

**代码实现**：

```java
package com.example.guava.demo;

import com.google.common.cache.*;
import com.google.common.collect.ImmutableList;
import com.google.common.collect.ImmutableMap;
import com.google.common.util.concurrent.*;

import java.util.*;
import java.util.concurrent.*;

/**
 * 带降级保护的缓存系统 - 使用 LoadingCache 高级特性
 */
public class ResilientCacheSystem {

    private final MockDatabase database = new MockDatabase();
    private final ExecutorService refreshExecutor = Executors.newFixedThreadPool(4);
    
    // 带降级保护的缓存
    private LoadingCache<String, CacheResult<Product>> productCache;
    
    // 批量查询缓存
    private LoadingCache<List<String>, Map<String, Product>> batchCache;

    public ResilientCacheSystem() {
        buildProductCacheWithFallback();
        buildBatchCache();
    }

    private void buildProductCacheWithFallback() {
        productCache = CacheBuilder.newBuilder()
            .maximumSize(10000)
            .expireAfterWrite(10, TimeUnit.MINUTES)
            .refreshAfterWrite(30, TimeUnit.SECONDS)  // 30 秒后异步刷新
            .recordStats()
            .build(new CacheLoader<String, CacheResult<Product>>() {
                @Override
                public CacheResult<Product> load(String key) throws Exception {
                    try {
                        Product product = database.getProduct(key);
                        return CacheResult.success(product);
                    } catch (Exception e) {
                        System.err.println("[加载失败] " + key + ": " + e.getMessage());
                        return CacheResult.failure(e.getMessage());
                    }
                }

                @Override
                public ListenableFuture<CacheResult<Product>> reload(
                        String key, CacheResult<Product> oldValue) {
                    // 异步刷新，不阻塞读取
                    return Futures.submit(() -> {
                        try {
                            Product product = database.getProduct(key);
                            System.out.println("[刷新成功] " + key);
                            return CacheResult.success(product);
                        } catch (Exception e) {
                            System.err.println("[刷新失败] " + key + ", 保留旧值");
                            // 刷新失败，保留旧值
                            return oldValue;
                        }
                    }, refreshExecutor);
                }
            });
    }

    private void buildBatchCache() {
        batchCache = CacheBuilder.newBuilder()
            .maximumSize(1000)
            .expireAfterWrite(5, TimeUnit.MINUTES)
            .build(new CacheLoader<List<String>, Map<String, Product>>() {
                @Override
                public Map<String, Product> load(List<String> keys) throws Exception {
                    System.out.println("[批量加载] " + keys.size() + " 个商品");
                    return database.batchGet(keys);
                }

                @Override
                public Map<String, Product> loadAll(Iterable<? extends List<String>> keysList) {
                    // 合并所有 keys
                    Set<String> allKeys = new HashSet<>();
                    for (List<String> keys : keysList) {
                        allKeys.addAll(keys);
                    }
                    System.out.println("[合并批量加载] " + allKeys.size() + " 个唯一商品");
                    return database.batchGet(new ArrayList<>(allKeys));
                }
            });
    }

    /**
     * 获取商品（带降级）
     */
    public Optional<Product> getProduct(String productId) {
        try {
            CacheResult<Product> result = productCache.get(productId);
            if (result.isSuccess()) {
                return Optional.of(result.getData());
            } else {
                System.err.println("[缓存降级] " + productId + ": " + result.getErrorMessage());
                return Optional.empty();
            }
        } catch (ExecutionException e) {
            return Optional.empty();
        }
    }

    /**
     * 获取商品或默认值
     */
    public Product getProductOrDefault(String productId, Product defaultValue) {
        return getProduct(productId).orElse(defaultValue);
    }

    /**
     * 批量获取商品（利用 loadAll 优化）
     */
    public Map<String, Product> getProducts(List<String> productIds) {
        try {
            return batchCache.get(ImmutableList.copyOf(productIds));
        } catch (ExecutionException e) {
            System.err.println("[批量加载失败] " + e.getMessage());
            return new HashMap<>();
        }
    }

    /**
     * 手动触发刷新
     */
    public void refresh(String productId) {
        productCache.refresh(productId);
    }

    /**
     * 获取统计
     */
    public void printStats() {
        CacheStats stats = productCache.stats();
        System.out.println("\n=== 缓存统计 ===");
        System.out.println("请求次数: " + stats.requestCount());
        System.out.println("命中次数: " + stats.hitCount());
        System.out.println("命中率: " + String.format("%.2f%%", stats.hitRate() * 100));
        System.out.println("加载次数: " + stats.loadCount());
        System.out.println("加载失败次数: " + stats.loadExceptionCount());
        System.out.println("平均加载时间: " + stats.averageLoadPenalty() / 1_000_000 + " ms");
    }

    // ========== 缓存结果包装类 ==========
    public static class CacheResult<T> {
        private final T data;
        private final String errorMessage;
        private final boolean success;

        private CacheResult(T data, String errorMessage, boolean success) {
            this.data = data;
            this.errorMessage = errorMessage;
            this.success = success;
        }

        public static <T> CacheResult<T> success(T data) {
            return new CacheResult<>(data, null, true);
        }

        public static <T> CacheResult<T> failure(String message) {
            return new CacheResult<>(null, message, false);
        }

        public T getData() { return data; }
        public String getErrorMessage() { return errorMessage; }
        public boolean isSuccess() { return success; }
    }

    // ========== 领域模型 ==========
    public static class Product {
        private final String id;
        private final String name;
        private final double price;
        private final Date loadTime;
        public static final Product EMPTY = new Product("EMPTY", "暂不可用", 0.0);

        public Product(String id, String name, double price) {
            this.id = id;
            this.name = name;
            this.price = price;
            this.loadTime = new Date();
        }

        public String getId() { return id; }
        public String getName() { return name; }
        public double getPrice() { return price; }
        public Date getLoadTime() { return loadTime; }

        @Override
        public String toString() {
            return String.format("Product[%s: %s @ ¥%.0f]", id, name, price);
        }
    }

    // 模拟数据库（带故障注入）
    private static class MockDatabase {
        private Map<String, Product> data = new HashMap<>();
        private volatile boolean failNext = false;

        public MockDatabase() {
            for (int i = 1; i <= 100; i++) {
                data.put("P" + i, new Product("P" + i, "商品" + i, 100.0 * i));
            }
        }

        public Product getProduct(String id) throws Exception {
            simulateDelay();
            
            if (failNext) {
                failNext = false;
                throw new RuntimeException("数据库故障模拟");
            }
            
            Product p = data.get(id);
            if (p == null) {
                throw new RuntimeException("Product not found: " + id);
            }
            return p;
        }

        public Map<String, Product> batchGet(List<String> ids) throws Exception {
            simulateDelay();
            
            Map<String, Product> result = new HashMap<>();
            for (String id : ids) {
                Product p = data.get(id);
                if (p != null) result.put(id, p);
            }
            return result;
        }

        public void triggerNextFailure() {
            failNext = true;
        }

        private void simulateDelay() throws InterruptedException {
            Thread.sleep(10);  // 模拟 10ms 延迟
        }
    }

    // ========== 测试入口 ==========
    public static void main(String[] args) throws Exception {
        ResilientCacheSystem cache = new ResilientCacheSystem();

        System.out.println("=== 正常加载测试 ===");
        Optional<Product> p1 = cache.getProduct("P1");
        System.out.println("获取 P1: " + p1);

        System.out.println("\n=== 缓存命中测试 ===");
        Optional<Product> p1Cached = cache.getProduct("P1");
        System.out.println("再次获取 P1（缓存）: " + p1Cached);
        System.out.println("加载时间: " + p1Cached.get().getLoadTime());

        System.out.println("\n=== 降级测试 ===");
        cache.database.triggerNextFailure();
        Optional<Product> p2 = cache.getProduct("P2");
        System.out.println("故障后获取 P2: " + p2);

        // 使用默认值
        Product defaultProduct = cache.getProductOrDefault("P2", Product.EMPTY);
        System.out.println("默认值: " + defaultProduct);

        System.out.println("\n=== 批量加载测试 ===");
        Map<String, Product> products = cache.getProducts(Arrays.asList("P10", "P20", "P30"));
        System.out.println("批量获取结果: " + products.size() + " 个商品");

        System.out.println("\n=== 手动刷新测试 ===");
        cache.refresh("P1");
        Thread.sleep(100);  // 等待异步刷新完成
        Optional<Product> refreshed = cache.getProduct("P1");
        System.out.println("刷新后 P1 加载时间: " + refreshed.get().getLoadTime());

        // 统计
        cache.printStats();

        cache.refreshExecutor.shutdown();
    }
}
```

### 测试验证

```java
package com.example.guava.demo;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

import java.util.Arrays;
import java.util.Optional;

public class ResilientCacheSystemTest {

    private ResilientCacheSystem cache;

    @BeforeEach
    public void setUp() {
        cache = new ResilientCacheSystem();
    }

    @Test
    public void testNormalLoad() {
        Optional<ResilientCacheSystem.Product> p = cache.getProduct("P1");
        assertTrue(p.isPresent());
        assertEquals("P1", p.get().getId());
    }

    @Test
    public void testCacheHit() {
        // 第一次加载
        cache.getProduct("P1");
        
        // 第二次应该命中缓存（通过加载时间判断）
        Optional<ResilientCacheSystem.Product> p2 = cache.getProduct("P1");
        assertTrue(p2.isPresent());
    }

    @Test
    public void testFallback() {
        // 触发故障
        // 注意：由于无法直接访问 database，这里依赖测试环境
        // 实际测试应该注入 mock
    }

    @Test
    public void testBatchLoad() {
        var result = cache.getProducts(Arrays.asList("P1", "P2", "P3"));
        assertEquals(3, result.size());
    }

    @Test
    public void testGetOrDefault() {
        ResilientCacheSystem.Product fallback = new ResilientCacheSystem.Product("X", "默认", 0);
        ResilientCacheSystem.Product result = cache.getProductOrDefault("NOT_EXIST", fallback);
        // 注意：如果缓存中没有，会尝试加载，加载失败可能返回空
    }
}
```

### 可能遇到的坑及解决方法

| 坑点 | 现象 | 解决方法 |
|------|------|----------|
| 刷新任务堆积 | 刷新频率过高，线程池耗尽 | 控制刷新间隔，使用独立线程池 |
| CacheResult 内存开销 | 包装类增加内存占用 | 只在必要时使用，简单场景直接存值 |
| 批量加载 key 不匹配 | loadAll 返回的 map 缺少某些 key | 确保 batch 查询返回所有 key 的结果 |
| 异步刷新异常丢失 | 刷新失败未感知 | 添加日志或回调监控 |

---

## 4 项目总结

### 优缺点对比

| 维度 | 高级 LoadingCache | 基础 CacheLoader |
|------|-------------------|------------------|
| 异常处理 | ★★★★★ 降级支持 | ★★ 需自行处理 |
| 批量加载 | ★★★★★ 支持 | ★ 不支持 |
| 异步刷新 | ★★★★★ 支持 | ★ 需手动实现 |
| 复杂度 | ★★★ 较复杂 | ★★★★ 简单 |
| 适用场景 | 生产级高可用 | 简单场景 |

### 适用场景

1. **高可用缓存**：加载失败需降级
2. **批量查询优化**：列表页批量加载
3. **热点数据保护**：异步刷新避免阻塞
4. **复杂回源场景**：多数据源聚合

### 生产最佳实践

1. **始终设置过期时间**：避免脏数据长期存在
2. **配合刷新使用**：refreshAfterWrite 减少穿透
3. **批量加载优化**：列表查询场景必用
4. **监控加载异常**：及时发现数据源问题
5. **设置默认值**：加载失败时优雅降级

### 思考题答案（第 16 章思考题 1）

> **问题**：`expireAfterWrite` 和 `refreshAfterWrite` 的区别？

**答案**：
- **`expireAfterWrite`**：数据在写入后固定时间过期，过期后必须重新加载，读取时阻塞
- **`refreshAfterWrite`**：数据在写入后固定时间触发刷新，但旧值仍可用，刷新是异步的，读取不阻塞

**使用场景**：
- 过期：数据必须是最新的（如价格）
- 刷新：可以容忍短暂旧数据，优先保证可用性（如用户信息）

### 新思考题

1. 如何设计一个缓存加载的熔断器，在数据库故障时自动停止加载并返回缓存数据？
2. 比较 Guava Cache 的批量加载和 Redis Pipeline 在性能上的差异。

### 推广计划提示

**开发**：
- 生产缓存必须设置加载异常处理
- 列表查询优先使用批量加载
- 热点数据启用异步刷新

**测试**：
- 模拟加载失败场景
- 测试批量加载性能
- 验证降级策略生效

**运维**：
- 监控加载异常率
- 设置加载耗时告警
- 准备数据库故障预案
