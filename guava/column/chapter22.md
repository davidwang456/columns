# 第 22 章：Service 框架管理后台任务生命周期

## 1 项目背景

在订单系统的后台任务模块中，工程师小孙管理着多个定时任务：数据同步、缓存预热、日志清理。每个任务都有自己的启动、停止逻辑，状态管理混乱，有的任务停止时资源未释放，有的重复启动导致数据不一致。

## 2 项目设计

**小胖**："后台任务管理太乱了，有的停不掉，有的重复启动！"

**大师**："Guava `Service` 框架提供了标准生命周期管理：

```java
public class DataSyncService extends AbstractScheduledService {
    @Override
    protected void runOneIteration() {
        // 执行同步逻辑
    }
    
    @Override
    protected Scheduler scheduler() {
        return Scheduler.newFixedDelaySchedule(0, 5, TimeUnit.MINUTES);
    }
    
    @Override
    protected void startUp() {
        // 初始化资源
    }
    
    @Override
    protected void shutDown() {
        // 释放资源
    }
}
```

**技术映射**：`Service` 就像是后台任务的'指挥官'——它统一管理启动、运行、停止，确保任务有序执行。"

## 3 项目实战

```java
// Service 管理器
ServiceManager serviceManager = new ServiceManager(Arrays.asList(
    new DataSyncService(),
    new CacheWarmupService(),
    new LogCleanupService()
));

// 批量启动
serviceManager.startAsync().awaitHealthy();

// 批量停止
serviceManager.stopAsync().awaitStopped();

// 健康检查
boolean healthy = serviceManager.isHealthy();
```

## 4 项目总结

### Service 状态机

```
NEW -> STARTING -> RUNNING -> STOPPING -> TERMINATED
         |           |           |
         v           v           v
      FAILED     FAILED      FAILED
```

### 适用场景

1. 定时任务管理
2. 资源生命周期管理
3. 服务健康检查
