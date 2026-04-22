# 第 23 章：RateLimiter 限流设计与热点保护

## 1 项目背景

在秒杀系统的接口层，工程师小李面临着流量洪峰的挑战。某次促销活动中，接口被瞬间涌入的请求打垮，数据库连接池耗尽，服务雪崩。事后分析发现，系统缺乏有效的限流保护机制。

## 2 项目设计

**小白**："Guava `RateLimiter` 提供令牌桶算法，支持突发流量和平滑限流。"

**大师**："两种限流模式：

```java
// 平滑突发：允许一定突发
RateLimiter limiter = RateLimiter.create(100);  // 每秒 100 个许可

// 平滑预热：冷启动时逐步提升
RateLimiter warmLimiter = RateLimiter.create(
    100,  // QPS
    10, TimeUnit.SECONDS  // 预热时间
);
```

**技术映射**：`RateLimiter` 就像是接口的'安检门'——高峰期限流保护，低峰期快速通过。"

## 3 项目实战

```java
public class ApiGateway {
    private final RateLimiter limiter = RateLimiter.create(1000);  // 1000 QPS
    
    public Response processRequest(Request req) {
        if (!limiter.tryAcquire(100, TimeUnit.MILLISECONDS)) {
            return Response.rateLimited();  // 限流响应
        }
        return handleRequest(req);
    }
}

// 预热限流（防止冷启动压垮）
RateLimiter warmLimiter = RateLimiter.create(
    1000, 2, TimeUnit.SECONDS
);  // 预热 2 秒达到 1000 QPS
```

## 4 项目总结

### 限流策略对比

| 策略 | 特点 |
|------|------|
| 令牌桶 | 允许突发，平均速率稳定 |
| 漏桶 | 平滑输出，无突发 |
| 计数器 | 简单，临界突变问题 |

### 思考题

1. 如何实现分布式限流？
2. 限流与熔断如何配合使用？
