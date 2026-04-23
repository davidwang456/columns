# 第 40 章：高级篇综合实战：可观测、高可用、可演进的推荐服务内核

## 1 项目背景

在核心推荐系统的架构升级中，首席架构师小孙需要设计一个支撑日均 10 亿次请求的推荐服务。系统需要整合 Guava 所有高级特性，具备可观测性、高可用性和可演进性。

## 2 项目设计

**大师**："推荐服务内核架构：

```
┌──────────────────────────────────────────────────────┐
│                    推荐服务内核                        │
├──────────────────────────────────────────────────────┤
│  接入层：RateLimiter 限流 + BloomFilter 去重           │
├──────────────────────────────────────────────────────┤
│  计算层：ListenableFuture 并行特征计算                 │
│          Graph 用户-物品关系建模                       │
├──────────────────────────────────────────────────────┤
│  缓存层：LoadingCache 多级缓存 + 异步刷新              │
│          ImmutableMap 热数据预加载                     │
├──────────────────────────────────────────────────────┤
│  治理层：EventBus 配置变更通知                        │
│          Service 组件生命周期管理                       │
│          Metrics 全链路监控                           │
├──────────────────────────────────────────────────────┤
│  存储层：IO 工具异步日志 + 反射序列化                  │
└──────────────────────────────────────────────────────┘
```

## 3 项目实战

```java
@Service
public class RecommendationKernel {
    
    // 限流保护
    private final RateLimiter queryLimiter = RateLimiter.create(50000);
    
    // 请求去重
    private final BloomFilter<String> recentQueries = BloomFilter.create(
        Funnels.stringFunnel(StandardCharsets.UTF_8),
        10000000, 0.001
    );
    
    // 多级缓存
    private final LoadingCache<String, List<Recommendation>> hotCache;
    private final LoadingCache<String, UserProfile> userCache;
    
    // 用户-物品关系图
    private final MutableGraph<String> userItemGraph = GraphBuilder.undirected()
        .build();
    
    // 事件总线
    private final EventBus eventBus = new AsyncEventBus(
        Executors.newFixedThreadPool(4)
    );
    
    // 监控指标
    private final MeterRegistry metrics;
    
    public RecommendationKernel() {
        this.hotCache = CacheBuilder.newBuilder()
            .maximumSize(100000)
            .expireAfterWrite(1, TimeUnit.MINUTES)
            .refreshAfterWrite(10, TimeUnit.SECONDS)
            .recordStats()
            .removalListener(this::onCacheEviction)
            .build(new CacheLoader<String, List<Recommendation>>() {
                @Override
                public List<Recommendation> load(String key) {
                    return computeRecommendations(key);
                }
                
                @Override
                public ListenableFuture<List<Recommendation>> reload(
                        String key, List<Recommendation> old) {
                    return Futures.submit(() -> computeRecommendations(key), 
                        refreshExecutor);
                }
            });
            
        eventBus.register(new ConfigChangeListener());
    }
    
    public List<Recommendation> recommend(RecommendRequest request) {
        Timer.Sample sample = Timer.start(metrics);
        
        try {
            // 1. 限流检查
            if (!queryLimiter.tryAcquire(10, TimeUnit.MILLISECONDS)) {
                metrics.counter("rec.rate_limited").increment();
                return fallbackRecommendations(request);
            }
            
            // 2. 去重检查
            String queryKey = Hashing.md5().hashString(
                request.toString(), StandardCharsets.UTF_8).toString();
            if (recentQueries.mightContain(queryKey)) {
                return cachedResult(queryKey);
            }
            recentQueries.put(queryKey);
            
            // 3. 并行特征计算
            ListenableFuture<UserProfile> userFuture = 
                Futures.submit(() -> getUserProfile(request.getUserId()), 
                    featureExecutor);
                    
            ListenableFuture<ContextFeatures> contextFuture = 
                Futures.submit(() -> extractContext(request), 
                    featureExecutor);
                    
            ListenableFuture<RecommendResult> resultFuture = 
                Futures.transform(
                    Futures.allAsList(userFuture, contextFuture),
                    inputs -> mergeAndRecommend(
                        (UserProfile) inputs.get(0),
                        (ContextFeatures) inputs.get(1)
                    ),
                    computeExecutor
                );
            
            List<Recommendation> result = resultFuture.get(100, TimeUnit.MILLISECONDS);
            
            sample.stop(metrics.timer("rec.latency"));
            return result;
            
        } catch (Exception e) {
            metrics.counter("rec.errors").increment();
            return fallbackRecommendations(request);
        }
    }
    
    private List<Recommendation> computeRecommendations(String key) {
        // 从模型服务获取推荐结果
        return modelService.predict(key);
    }
    
    @Subscribe
    public void onConfigChange(ConfigChangeEvent event) {
        // 动态调整参数
        if ("cache.ttl".equals(event.getKey())) {
            // 重建缓存
            hotCache.invalidateAll();
        }
    }
    
    private void onCacheEviction(RemovalNotification<String, List<Recommendation>> notification) {
        if (notification.getCause() == RemovalCause.SIZE) {
            eventBus.post(new CachePressureEvent());
        }
    }
    
    public Map<String, Object> healthCheck() {
        return ImmutableMap.of(
            "cache.hit_rate", hotCache.stats().hitRate(),
            "cache.size", hotCache.size(),
            "rate_limiter.available", queryLimiter.acquire(),
            "graph.nodes", userItemGraph.nodes().size(),
            "status", "HEALTHY"
        );
    }
}
```

## 4 项目总结

### 40 章技术全景

| 层级 | 核心工具 | 能力 |
|------|----------|------|
| 基础 | Optional/Preconditions/Strings/Collections | 代码质量提升 |
| 中级 | Cache/Concurrency/IO/EventBus | 工程化能力 |
| 高级 | 源码/算法/治理/演进 | 架构师视野 |

### 学习路径建议

1. **新人开发**：1-5 章 → 7-10 章 → 16-17 章
2. **核心开发**：全基础篇 + 中级篇
3. **架构师**：全篇 + 源码研读

### Guava 生态展望

- **Java 21+**：部分功能被 JDK 替代，但 Cache/Graph/EventBus 仍有价值
- **Kotlin**：与标准库竞争，但 Java 项目仍广泛使用
- **替代方案**：Caffeine（缓存）、RxJava（并发）、Apache Commons（基础）

**结语**：
Guava 是 Java 工程化实践的精华总结，学习它不仅是学习 API，更是学习 Google 的工程思维和设计哲学。掌握 Guava，让你的 Java 代码更简洁、更安全、更高效。

---

**全书完**
