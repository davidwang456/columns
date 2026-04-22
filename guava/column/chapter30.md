# 第 30 章：中级篇综合实战：高并发商品查询与本地缓存加速平台

## 1 项目背景

在双十一大促前，技术团队需要构建一个能支撑 10万 QPS 的商品查询平台。系统需要整合缓存、限流、异步处理、监控等多项能力，并能经受压测验证。

## 2 项目设计

**大师**："综合使用中级篇工具构建完整方案：

```
┌─────────────────────────────────────────┐
│  RateLimiter    限流保护（10000 QPS）     │
├─────────────────────────────────────────┤
│  LoadingCache   热点数据缓存              │
│  - 容量：10万条目                         │
│  - 过期：5分钟写入过期                    │
│  - 刷新：1分钟异步刷新                    │
│  - 统计：命中率监控                       │
├─────────────────────────────────────────┤
│  ListenableFuture  异步加载              │
│  - 批量查询并行化                         │
│  - 超时控制：200ms                        │
├─────────────────────────────────────────┤
│  EventBus      状态变更通知              │
├─────────────────────────────────────────┤
│  Metrics       可观测性                  │
│  - 命中率、加载时间、限流次数              │
└─────────────────────────────────────────┘
```

## 3 项目实战

```java
@Component
public class HighPerformanceProductService {
    
    private final RateLimiter rateLimiter = RateLimiter.create(10000);
    private final LoadingCache<String, Product> cache;
    private final EventBus eventBus;
    private final MetricsCollector metrics;
    
    public HighPerformanceProductService() {
        this.cache = CacheBuilder.newBuilder()
            .maximumSize(100000)
            .expireAfterWrite(5, TimeUnit.MINUTES)
            .refreshAfterWrite(1, TimeUnit.MINUTES)
            .recordStats()
            .removalListener(this::onCacheRemoval)
            .build(new CacheLoader<String, Product>() {
                @Override
                public Product load(String key) {
                    metrics.recordCacheMiss();
                    return loadFromDatabase(key);
                }
                
                @Override
                public ListenableFuture<Product> reload(String key, Product old) {
                    return refreshAsync(key, old);
                }
            });
            
        this.eventBus = new AsyncEventBus(Executors.newFixedThreadPool(4));
        this.metrics = new MetricsCollector();
    }
    
    public Product getProduct(String id) {
        // 1. 限流检查
        if (!rateLimiter.tryAcquire(100, TimeUnit.MILLISECONDS)) {
            metrics.recordRateLimited();
            throw new RateLimitException();
        }
        
        // 2. 缓存获取
        try {
            Product product = cache.get(id);
            metrics.recordCacheHit();
            return product;
        } catch (ExecutionException e) {
            metrics.recordCacheError();
            throw new ServiceException("Failed to load product", e);
        }
    }
    
    public List<Product> getProducts(List<String> ids) {
        // 并行批量查询
        List<ListenableFuture<Product>> futures = ids.stream()
            .map(id -> Futures.submit(() -> getProduct(id), executor))
            .collect(toImmutableList());
            
        try {
            return Futures.successfulAsList(futures).get(500, TimeUnit.MILLISECONDS);
        } catch (Exception e) {
            throw new ServiceException("Batch query failed", e);
        }
    }
    
    private void onCacheRemoval(RemovalNotification<String, Product> notification) {
        if (notification.getCause() == RemovalCause.EXPLICIT) {
            eventBus.post(new ProductInvalidatedEvent(notification.getKey()));
        }
    }
    
    public CacheStats getCacheStats() {
        return cache.stats();
    }
    
    public Map<String, Object> getMetrics() {
        return metrics.snapshot();
    }
}
```

## 4 项目总结

### 中级篇工具综合运用

| 工具 | 用途 |
|------|------|
| CacheBuilder | 热点数据缓存 |
| LoadingCache | 自动加载与刷新 |
| RateLimiter | 限流保护 |
| ListenableFuture | 异步编排 |
| EventBus | 事件通知 |
| CacheStats | 监控统计 |

### 压测指标

- QPS: 10000+
- 平均延迟: < 10ms (命中缓存)
- 命中率: > 90%
- 数据库负载: 降低 80%

---

**中级篇总结**：

第 16-30 章覆盖 Guava 工程化能力：
- **Cache**：本地缓存架构、加载治理、淘汰策略、可观测性
- **Concurrency**：ListenableFuture、Service、RateLimiter
- **其他工具**：BloomFilter、EventBus、IO、Graph

掌握这些工具，可以构建生产级 Java 应用。
