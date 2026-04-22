# 第 25 章：EventBus 解耦事件驱动流程

## 1 项目背景

在电商系统的订单状态流转中，工程师小周面临着模块耦合的问题。订单状态变更后需要通知库存、物流、营销等多个模块，直接调用导致订单核心逻辑臃肿，而且新增监听方时需要修改订单代码。

## 2 项目设计

**小胖**："订单状态变了要通知一堆系统，代码乱成一锅粥！"

**大师**："EventBus 发布订阅模式解耦：

```java
// 定义事件
public class OrderCreatedEvent {
    private final String orderId;
    private final String userId;
    // ...
}

// 发布者
EventBus eventBus = new EventBus();
eventBus.post(new OrderCreatedEvent(orderId, userId));

// 订阅者
@Subscribe
public void onOrderCreated(OrderCreatedEvent event) {
    // 处理订单创建
    stockService.reserve(event.getOrderId());
}
```

**技术映射**：EventBus 就像是'内部广播系统'——发布者喊话，感兴趣的订阅者接收，互不干扰。"

## 3 项目实战

```java
public class OrderService {
    private final EventBus eventBus;
    
    public OrderService() {
        eventBus = new EventBus("order-events");
        
        // 注册订阅者
        eventBus.register(new StockListener());
        eventBus.register(new LogisticsListener());
        eventBus.register(new PromotionListener());
    }
    
    public void createOrder(OrderRequest req) {
        // 创建订单逻辑
        Order order = doCreateOrder(req);
        
        // 发布事件
        eventBus.post(new OrderCreatedEvent(order.getId(), order.getUserId()));
    }
}

// 异步 EventBus
AsyncEventBus asyncEventBus = new AsyncEventBus(
    Executors.newFixedThreadPool(10)
);

// 异常处理
eventBus.register(new Object() {
    @Subscribe
    public void handleDeadEvent(DeadEvent event) {
        log.warn("Unhandled event: {}", event.getEvent());
    }
});
```

## 4 项目总结

### EventBus vs MQ

| 维度 | EventBus | MQ |
|------|----------|-----|
| 通信范围 | 进程内 | 分布式 |
| 持久化 | 无 | 支持 |
| 事务 | 简单 | 完善 |
| 复杂度 | 低 | 高 |

### 适用场景

1. 模块间解耦
2. 状态变更通知
3. 本地事件流处理
