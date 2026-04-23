# 第 39 章：Guava 在 SRE 场景的稳定性治理实践

## 1 项目背景

在 SRE 团队负责的稳定性治理中，资深工程师小孙需要建立 Guava 组件的监控、告警、故障预案体系。缓存雪崩、限流失效、并发泄漏等风险需要系统化治理。

## 2 项目设计

**大师**："SRE 治理框架：

```
监控层：
- CacheStats 上报（命中率、加载时间）
- RateLimiter 饱和度监控
- 线程池队列深度

告警层：
- 命中率 < 50% 持续 5 分钟
- 加载失败率 > 10%
- 限流触发频率突增

预案层：
- 缓存降级开关
- 限流阈值动态调整
- 紧急扩容流程
```

**技术映射**：SRE 治理就像是'城市应急系统'——平时监控预警，战时快速响应。"

## 3 项目实战

```java
@Component
public class GuavaSREGovernance {
    
    // 监控指标上报
    @Scheduled(fixedRate = 60000)
    public void reportMetrics() {
        CacheStats stats = cache.stats();
        
        metrics.gauge("guava.cache.hit_rate", stats.hitRate());
        metrics.gauge("guava.cache.size", cache.size());
        metrics.counter("guava.cache.evictions", stats.evictionCount());
        
        // 告警检查
        if (stats.hitRate() < 0.5) {
            alertService.send("缓存命中率低于 50%", AlertLevel.WARNING);
        }
    }
    
    // 动态限流调整
    @PostMapping("/admin/rate-limiter/threshold")
    public void adjustRateLimiter(@RequestParam double qps) {
        // 动态替换 RateLimiter（需包装支持）
        rateLimiter.setRate(qps);
    }
    
    // 缓存紧急清空（故障预案）
    @PostMapping("/admin/cache/clear")
    public void emergencyClear() {
        cache.invalidateAll();
        alertService.send("缓存已紧急清空", AlertLevel.CRITICAL);
    }
    
    // 线程池健康检查
    public HealthStatus checkThreadPools() {
        ThreadPoolExecutor executor = (ThreadPoolExecutor) asyncExecutor;
        
        double saturation = executor.getActiveCount() / 
                           (double) executor.getMaximumPoolSize();
        
        if (saturation > 0.8) {
            return HealthStatus.DEGRADED;
        }
        return HealthStatus.HEALTHY;
    }
}

// 故障演练
public class ChaosEngineering {
    
    // 模拟缓存雪崩
    public void simulateCacheAvalanche() {
        // 批量使缓存失效
        cache.invalidateAll();
        // 观察系统行为
    }
    
    // 模拟限流失效
    public void simulateRateLimiterBypass() {
        // 绕过限流直接调用
        // 验证兜底机制
    }
}
```

## 4 项目总结

### SRE 检查清单

| 检查项 | 频率 | 阈值 |
|--------|------|------|
| 缓存命中率 | 实时 | > 80% |
| 加载时间 | 实时 | < 100ms |
| 限流饱和度 | 实时 | < 90% |
| 线程池队列 | 实时 | < 1000 |
| 内存占用 | 5min | < 80% |

### 应急预案

1. 缓存穿透：启用布隆过滤器 + 空值缓存
2. 限流失效：降级到服务拒绝
3. 线程池耗尽：动态扩容 + 队列清理
