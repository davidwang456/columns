# 第 16 章：CacheBuilder 本地缓存架构设计

## 1 项目背景

在高并发的电商系统中，工程师小陈负责的订单查询服务遇到了严重的性能瓶颈。每次查询都要访问数据库，在促销高峰期，数据库 CPU 飙升到 90% 以上，响应时间从 50ms 暴涨到 2s，大量请求超时。

初步分析发现，80% 的查询都在访问相同的商品和用户信息——热点数据反复从数据库读取。团队尝试过用简单的 HashMap 做缓存，但遇到了内存溢出、数据不一致、并发安全等一系列问题。

**业务场景**：高频读取、低频更新的热点数据缓存，如商品信息、用户配置、字典数据等。

**痛点放大**：
- **数据库压力大**：热点数据反复查询，IO 瓶颈明显。
- **并发安全问题**：HashMap 不是线程安全，ConcurrentHashMap 功能有限。
- **内存管理困难**：无限制的缓存最终导致 OOM。
- **数据一致性**：缓存与数据库的数据不同步。
- **缺乏监控**：命中率、加载时间等关键指标无法观测。

如果没有专业的本地缓存解决方案，系统性能和稳定性将无法保证。

**技术映射**：Guava 的 `CacheBuilder` 提供了强大的本地缓存构建器，支持容量限制、过期策略、弱/软引用、并发控制、统计等功能，是生产级本地缓存的首选方案。

---

## 2 项目设计

**场景**：性能优化专项会议，讨论缓存架构选型。

---

**小胖**：（看着监控大屏）"我说，这数据库压力也太大了吧！同样的商品信息，一分钟被查了上千次。这不就跟食堂的招牌菜，每个人都去问一遍 ingredients，厨师累死了？"

**小白**：（点头）"需要引入缓存。但自己写缓存坑很多——容量怎么控制？过期怎么实现？怎么统计命中率？"

**大师**：（在白板上画架构）"Guava Cache 解决了这些问题：

```java
LoadingCache<String, Product> cache = CacheBuilder.newBuilder()
    .maximumSize(10000)           // 最多 1 万条
    .expireAfterWrite(10, TimeUnit.MINUTES)  // 写入 10 分钟后过期
    .recordStats()                // 开启统计
    .build(
        new CacheLoader<String, Product>() {
            public Product load(String key) throws Exception {
                return loadFromDatabase(key);  // 自动加载
            }
        }
    );
```

**技术映射**：`CacheBuilder` 就像是缓存的'配置中心'——你声明要什么，它帮你处理容量控制、过期清理、并发加载等复杂问题，不用自己从零造轮子。"

**小胖**："那如果缓存满了怎么办？"

**小白**："Guava 使用 **W-TinyLFU** 算法（近似 LRU + LFU 的组合）进行淘汰：
- 优先淘汰最近最少使用 + 使用频率最低的数据
- 保留真正有价值的缓存项
- 比纯 LRU 更能抵抗突发扫描（scan）攻击

**大师**："过期策略也有多种选择：

```java
// 写入后多久过期
.expireAfterWrite(10, TimeUnit.MINUTES)

// 访问后多久过期（适合热点数据）
.expireAfterAccess(5, TimeUnit.MINUTES)

// 刷新（异步重新加载，不阻塞读取）
.refreshAfterWrite(1, TimeUnit.MINUTES)
```

**技术映射**：过期策略的选择就像食材保鲜——有的适合放冷藏（固定过期），有的适合现做现吃（访问后过期），有的可以定期复热（刷新）。"

**小胖**："那内存紧张时怎么办？"

**小白**："可以用引用类型来控制内存敏感性：

```java
CacheBuilder.newBuilder()
    .weakKeys()      // 键用弱引用，GC 可回收
    .weakValues()    // 值用弱引用
    // .softValues()  // 软引用，内存不足时回收
```

但注意：用弱/软引用后，缓存项可能在任何时候消失，需要做好 null 处理。"

**大师**："还有一点很重要——**并发控制**。Guava Cache 使用分段锁（类似 ConcurrentHashMap），支持高并发读写：

```java
CacheBuilder.newBuilder()
    .concurrencyLevel(8)  // 并发级别，默认 4
    .initialCapacity(1000)  // 初始容量
```

**技术映射**：Guava Cache 的设计哲学是'开箱即用的生产级缓存'，它把缓存领域的最佳实践（容量管理、淘汰算法、并发控制、统计监控）都封装好了。"

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

### 分步实现：商品缓存系统

**步骤目标**：用 `CacheBuilder` 构建生产级商品缓存。

**代码实现**：

```java
package com.example.guava.demo;

import com.google.common.base.Preconditions;
import com.google.common.cache.*;

import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * 商品缓存系统 - 使用 CacheBuilder
 */
public class ProductCacheSystem {

    // 模拟数据库
    private ProductDatabase database = new ProductDatabase();
    private AtomicInteger dbQueryCount = new AtomicInteger(0);

    // 商品缓存配置
    private LoadingCache<String, Product> productCache;
    
    // 批量加载缓存
    private LoadingCache<List<String>, Map<String, Product>> batchCache;

    public ProductCacheSystem() {
        buildProductCache();
        buildBatchCache();
    }

    private void buildProductCache() {
        productCache = CacheBuilder.newBuilder()
            // 容量限制
            .maximumSize(10000)
            .initialCapacity(1000)
            
            // 并发配置
            .concurrencyLevel(8)
            
            // 过期策略
            .expireAfterWrite(10, TimeUnit.MINUTES)
            .refreshAfterWrite(1, TimeUnit.MINUTES)  // 异步刷新
            
            // 弱引用（可选，内存敏感时启用）
            // .weakValues()
            
            // 统计
            .recordStats()
            
            // 移除监听器
            .removalListener(new RemovalListener<String, Product>() {
                @Override
                public void onRemoval(RemovalNotification<String, Product> notification) {
                    System.out.println("[移除] " + notification.getKey() + 
                        " 原因: " + notification.getCause());
                }
            })
            
            // 构建
            .build(
                new CacheLoader<String, Product>() {
                    @Override
                    public Product load(String key) throws Exception {
                        System.out.println("[加载] 从数据库加载: " + key);
                        dbQueryCount.incrementAndGet();
                        return database.getProduct(key);
                    }
                    
                    @Override
                    public ListenableFuture<Product> reload(String key, Product oldValue) {
                        // 异步刷新
                        ExecutorService executor = Executors.newSingleThreadExecutor();
                        return Futures.submit(() -> {
                            System.out.println("[刷新] 异步刷新: " + key);
                            dbQueryCount.incrementAndGet();
                            return database.getProduct(key);
                        }, executor);
                    }
                }
            );
    }

    private void buildBatchCache() {
        // 批量加载缓存（用于列表查询）
        batchCache = CacheBuilder.newBuilder()
            .maximumSize(1000)
            .expireAfterWrite(5, TimeUnit.MINUTES)
            .recordStats()
            .build(
                new CacheLoader<List<String>, Map<String, Product>>() {
                    @Override
                    public Map<String, Product> load(List<String> keys) throws Exception {
                        System.out.println("[批量加载] 加载 " + keys.size() + " 个商品");
                        dbQueryCount.incrementAndGet();
                        return database.getProducts(keys);
                    }
                }
            );
    }

    /**
     * 获取商品（自动加载）
     */
    public Product getProduct(String productId) {
        try {
            return productCache.get(productId);
        } catch (ExecutionException e) {
            throw new RuntimeException("Failed to load product: " + productId, e);
        }
    }

    /**
     * 批量获取商品
     */
    public Map<String, Product> getProducts(List<String> productIds) {
        try {
            return batchCache.get(productIds);
        } catch (ExecutionException e) {
            throw new RuntimeException("Failed to load products", e);
        }
    }

    /**
     * 获取商品（如果缓存中有）
     */
    public Optional<Product> getIfPresent(String productId) {
        return Optional.ofNullable(productCache.getIfPresent(productId));
    }

    /**
     * 手动放入缓存
     */
    public void putProduct(String productId, Product product) {
        productCache.put(productId, product);
    }

    /**
     * 使缓存失效
     */
    public void invalidate(String productId) {
        productCache.invalidate(productId);
    }

    public void invalidateAll(List<String> productIds) {
        productCache.invalidateAll(productIds);
    }

    /**
     * 清空缓存
     */
    public void clearCache() {
        productCache.invalidateAll();
    }

    /**
     * 获取统计信息
     */
    public CacheStats getStats() {
        return productCache.stats();
    }

    /**
     * 获取数据库查询次数
     */
    public int getDbQueryCount() {
        return dbQueryCount.get();
    }

    /**
     * 获取缓存当前大小
     */
    public long getCacheSize() {
        return productCache.size();
    }

    /**
     * 查看缓存内容（调试用）
     */
    public Map<String, Product> getCacheSnapshot() {
        return productCache.asMap();
    }

    // ========== 领域模型 ==========
    public static class Product {
        private final String id;
        private final String name;
        private final double price;
        private final int stock;
        private final Date loadTime;

        public Product(String id, String name, double price, int stock) {
            this.id = id;
            this.name = name;
            this.price = price;
            this.stock = stock;
            this.loadTime = new Date();
        }

        public String getId() { return id; }
        public String getName() { return name; }
        public double getPrice() { return price; }
        public int getStock() { return stock; }
        public Date getLoadTime() { return loadTime; }

        @Override
        public String toString() {
            return String.format("Product[%s: %s @ ¥%.0f]", id, name, price);
        }
    }

    // 模拟数据库
    private static class ProductDatabase {
        private Map<String, Product> data = new HashMap<>();

        public ProductDatabase() {
            // 初始化测试数据
            for (int i = 1; i <= 100; i++) {
                data.put("P" + i, new Product("P" + i, "商品" + i, 100.0 * i, 100));
            }
        }

        public Product getProduct(String id) {
            // 模拟数据库延迟
            try {
                Thread.sleep(10);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
            return data.get(id);
        }

        public Map<String, Product> getProducts(List<String> ids) {
            try {
                Thread.sleep(50);  // 批量查询稍慢
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
            
            Map<String, Product> result = new HashMap<>();
            for (String id : ids) {
                Product p = data.get(id);
                if (p != null) result.put(id, p);
            }
            return result;
        }
    }

    // ========== 测试入口 ==========
    public static void main(String[] args) throws Exception {
        ProductCacheSystem cache = new ProductCacheSystem();

        System.out.println("=== 缓存基本操作 ===\n");

        // 第一次获取（从数据库加载）
        System.out.println("获取 P1:");
        Product p1 = cache.getProduct("P1");
        System.out.println("结果: " + p1);
        System.out.println("数据库查询次数: " + cache.getDbQueryCount());

        // 第二次获取（从缓存）
        System.out.println("\n再次获取 P1:");
        Product p1Cached = cache.getProduct("P1");
        System.out.println("结果: " + p1Cached);
        System.out.println("数据库查询次数: " + cache.getDbQueryCount());  // 应该还是 1

        // 获取不存在的商品
        System.out.println("\n获取不存在的商品:");
        try {
            cache.getProduct("NOT_EXIST");
        } catch (Exception e) {
            System.out.println("预期异常: " + e.getMessage());
        }

        // 模拟高并发访问
        System.out.println("\n=== 并发测试 ===");
        ExecutorService executor = Executors.newFixedThreadPool(10);
        CountDownLatch latch = new CountDownLatch(100);
        
        for (int i = 0; i < 100; i++) {
            final String pid = "P" + (i % 10 + 1);  // 只访问 P1-P10
            executor.submit(() -> {
                cache.getProduct(pid);
                latch.countDown();
            });
        }
        
        latch.await();
        executor.shutdown();
        
        System.out.println("100 次并发查询（10 个商品）完成");
        System.out.println("数据库查询次数: " + cache.getDbQueryCount());  // 应该接近 10

        // 统计信息
        System.out.println("\n=== 统计信息 ===");
        CacheStats stats = cache.getStats();
        System.out.println("请求次数: " + stats.requestCount());
        System.out.println("命中次数: " + stats.hitCount());
        System.out.println("命中率: " + String.format("%.2f%%", stats.hitRate() * 100));
        System.out.println("加载次数: " + stats.loadCount());
        System.out.println("平均加载时间: " + stats.averageLoadPenalty() / 1000000 + " ms");
        System.out.println("当前缓存大小: " + cache.getCacheSize());
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
import java.util.List;
import java.util.concurrent.ExecutionException;

public class ProductCacheSystemTest {

    private ProductCacheSystem cache;

    @BeforeEach
    public void setUp() {
        cache = new ProductCacheSystem();
    }

    @Test
    public void testCacheHit() {
        // 第一次加载
        Product p1 = cache.getProduct("P1");
        assertNotNull(p1);
        assertEquals(1, cache.getDbQueryCount());

        // 第二次命中缓存
        Product p1Again = cache.getProduct("P1");
        assertEquals(p1.getName(), p1Again.getName());
        assertEquals(1, cache.getDbQueryCount());  // 数据库查询次数不变
    }

    @Test
    public void testGetIfPresent() {
        // 缓存中没有
        assertFalse(cache.getIfPresent("P1").isPresent());

        // 加载到缓存
        cache.getProduct("P1");

        // 现在有
        assertTrue(cache.getIfPresent("P1").isPresent());
    }

    @Test
    public void testInvalidate() {
        cache.getProduct("P1");
        assertEquals(1, cache.getDbQueryCount());

        cache.invalidate("P1");

        // 重新加载
        cache.getProduct("P1");
        assertEquals(2, cache.getDbQueryCount());
    }

    @Test
    public void testPutProduct() {
        Product custom = new ProductCacheSystem.Product("CUSTOM", "自定义", 999.0, 10);
        cache.putProduct("CUSTOM", custom);

        Product cached = cache.getIfPresent("CUSTOM").get();
        assertEquals("自定义", cached.getName());
    }

    @Test
    public void testCacheStats() {
        // 多次访问
        for (int i = 0; i < 10; i++) {
            cache.getProduct("P1");
        }

        var stats = cache.getStats();
        assertEquals(10, stats.requestCount());
        assertEquals(9, stats.hitCount());  // 9 次命中
        assertEquals(1, stats.loadCount()); // 1 次加载
    }

    @Test
    public void testBatchCache() {
        List<String> ids = Arrays.asList("P1", "P2", "P3");
        
        var result = cache.getProducts(ids);
        assertEquals(3, result.size());
        assertEquals(1, cache.getDbQueryCount());  // 批量查询只算 1 次
    }
}
```

### 可能遇到的坑及解决方法

| 坑点 | 现象 | 解决方法 |
|------|------|----------|
| `get()` 抛 ExecutionException | 加载失败时传播异常 | 用 `getIfPresent` 或 try-catch 处理 |
| 缓存加载阻塞 | 大量缓存失效时线程阻塞 | 使用 `refreshAfterWrite` 异步刷新 |
| 内存泄露 | 缓存无限增长 | 设置 `maximumSize` 或 `expireAfterWrite` |
| 弱引用缓存消失 | 数据莫名消失 | 改用强引用，或检查 GC 情况 |

---

## 4 项目总结

### 优缺点对比

| 维度 | Guava Cache | ConcurrentHashMap | Redis | Caffeine |
|------|-------------|-------------------|-------|----------|
| 功能丰富度 | ★★★★★ 完整 | ★★ 基础 | ★★★★★ 完整 | ★★★★★ 完整 |
| 本地/远程 | ★★★★★ 本地 | ★★★★★ 本地 | ★★★ 远程 | ★★★★★ 本地 |
| 性能 | ★★★★ 良好 | ★★★★★ 最优 | ★★★ 网络开销 | ★★★★★ 最优 |
| 内存效率 | ★★★★ W-TinyLFU | ★★ 无管理 | ★★★★★ 独立进程 | ★★★★★ W-TinyLFU |
| 过期策略 | ★★★★★ 丰富 | ★ 无 | ★★★★★ 丰富 | ★★★★★ 丰富 |
| 统计监控 | ★★★★★ 内置 | ★ 无 | ★★★★★ 完整 | ★★★★★ 内置 |

### 适用场景

1. **本地热点数据**：用户会话、商品信息、配置数据
2. **计算结果缓存**：复杂查询、聚合计算结果
3. **限流计数器**：IP 访问频率统计
4. **临时数据存储**：验证码、token 等短期数据

### 不适用场景

1. **分布式缓存需求**：需要多节点共享缓存
2. **大对象缓存**：缓存项超过 JVM 堆 1/3
3. **持久化需求**：需要缓存数据落盘
4. **超高并发写**：写多读少场景

### 生产踩坑案例

**案例 1：缓存雪崩**
```java
// 所有缓存同时过期，大量请求穿透到数据库
.expireAfterWrite(10, TimeUnit.MINUTES)  // 所有缓存 10 分钟后同时过期
```
解决：过期时间加随机偏移，或使用 `refreshAfterWrite`。

**案例 2：缓存加载阻塞**
```java
// 缓存失效时，大量线程等待加载完成
return cache.get(key);  // 阻塞直到加载完成
```
解决：使用 `getIfPresent` 返回默认值，异步加载。

**案例 3：缓存 OOM**
```java
CacheBuilder.newBuilder()
    // 没有设置 maximumSize！
    .build(loader);
```
解决：必须设置容量限制或过期策略。

### 思考题

1. `expireAfterWrite` 和 `refreshAfterWrite` 的区别是什么？在什么场景下应该使用刷新而非过期？
2. 设计一个防止缓存雪崩的策略，结合 Guava Cache 的功能实现。

### 推广计划提示

**开发**：
- 热点数据优先使用 Guava Cache
- 必须设置容量限制和过期策略
- 开启统计监控

**测试**：
- 测试缓存加载失败场景
- 压测缓存命中率
- 验证过期策略生效

**运维**：
- 监控命中率、加载时间
- 设置缓存大小告警
- 准备好缓存穿透兜底方案
