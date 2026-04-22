# 第 21 章：Futures.transform/callback 异步回调与错误传播

## 1 项目背景

在使用 `ListenableFuture` 重构订单查询系统后，工程师小马又遇到了新的问题。异步回调中的异常被吞掉了，导致问题难以排查；多个异步任务串联时，一个环节出错，整个流程就中断，但没有优雅的错误恢复机制；回调代码嵌套层级太深，形成了新的"回调地狱"。

**业务场景**：异步错误处理、回调编排、异常恢复、超时降级等需要精细控制异步流程的场景。

**痛点放大**：
- **异常被吞**：异步任务异常未正确传播。
- **回调嵌套**：多层回调代码难以维护。
- **错误恢复困难**：局部失败无法降级处理。
- **取消传播**：父任务取消，子任务未收到通知。
- **调试困难**：异步堆栈难以追踪问题。

**技术映射**：Guava `Futures` 提供了 `transform`、`catching`、`withFallback` 等工具方法，配合 `FutureCallback` 可以实现优雅的异步错误处理和恢复。

---

## 2 项目设计

**小胖**："异步代码一出错就懵，堆栈都看不懂了！"

**小白**："需要用 `Futures.catching` 捕获特定异常，用 `withFallback` 提供降级。"

**大师**："`Futures.transform` 的异常会包装在 `ExecutionException` 中，可以用 `catching` 解包处理：

```java
ListenableFuture<Integer> future = Futures.submit(() -> {
    return 100 / 0;  // 会抛 ArithmeticException
}, executor);

// 捕获异常并返回默认值
ListenableFuture<Integer> safeFuture = Futures.catching(
    future,
    ArithmeticException.class,
    ex -> -1,  // 返回默认值
    executor
);
```

**技术映射**：`catching` 就像是异步流程的'安全气囊'——出问题时它能接住异常，让流程继续而不是崩溃。"

---

## 3 项目实战

```java
// 异常捕获与降级
ListenableFuture<User> userFuture = getUserAsync(id);

ListenableFuture<User> withFallback = Futures.withFallback(
    userFuture,
    t -> {
        log.warn("获取用户失败，使用默认用户: {}", id);
        return Futures.immediateFuture(User.DEFAULT);
    },
    executor
);

// 链式异常处理
ListenableFuture<Order> orderFuture = Futures.transform(
    userFuture,
    user -> createOrder(user),
    executor
);

ListenableFuture<Order> safeOrder = Futures.catchingAsync(
    orderFuture,
    OrderCreateException.class,
    ex -> retryCreateOrder(ex.getUser()),
    executor
);
```

### 测试验证

```java
@Test
public void testCatching() throws Exception {
    ListenableFuture<Integer> future = Futures.immediateFuture(100);
    ListenableFuture<Integer> result = Futures.catching(
        future,
        RuntimeException.class,
        ex -> -1,
        executor
    );
    assertEquals(100, result.get().intValue());  // 正常值不变
}

@Test
public void testWithFallback() throws Exception {
    ListenableFuture<String> failed = Futures.immediateFailedFuture(new RuntimeException("fail"));
    ListenableFuture<String> result = Futures.withFallback(
        failed,
        t -> Futures.immediateFuture("default"),
        executor
    );
    assertEquals("default", result.get());
}
```

---

## 4 项目总结

### 错误处理策略

| 方法 | 用途 |
|------|------|
| `catching` | 捕获特定异常，返回默认值 |
| `catchingAsync` | 捕获异常，异步恢复 |
| `withFallback` | 通用降级处理 |
| `immediateFailedFuture` | 创建失败 Future |

### 新思考题

1. 如何设计一个异步重试策略，带指数退避？
2. 比较 Guava 错误处理与 Resilience4j 的差异。
