# 第36章：DegradeSlot 源码：熔断器状态机与恢复机制

## 1 项目背景

第 35 章研究了 FlowSlot 的三种 Controller，本章转向 Sentinel 的第二大核心能力：熔断降级。熔断的判断逻辑比流控复杂得多——它不是简单地"超过阈值就拒绝"，而是涉及一个完整的状态机：Closed → Open → Half-Open → Closed。

熔断器什么时候从 Closed 切换到 Open？Half-Open 阶段放几个请求？探测失败后怎么办？规则动态变更时熔断状态如何响应？这些问题需要在源码层面解答。

## 2 项目设计

**小白**："DegradeSlot 的判断逻辑和 FlowSlot 类似吗？"

**大师**："结构类似，但更复杂。DegradeSlot 调用 `DegradeRuleManager` 获取规则，然后通过 `CircuitBreaker` 接口判断。有三种 CircuitBreaker 实现：`ResponseTimeCircuitBreaker`（慢调用）、`ExceptionCircuitBreaker`（异常比例/异常数）。"

**小胖**："Half-Open 阶段 Sentinel 会放几个请求？源码怎么控制的？"

**大师**："只放一个请求。这是 Sentinel 的简洁设计——不像 Hystrix 那样放多个。这一个探测请求通过后立即转 Closed，不通过则继续 Open。"

**小白**："那 Half-Open 只放一个请求，如果这唯一一个探测请求刚好是一个合法但慢的请求（比如用户查了一个大数据量的报表），会不会导致'假阳性'——熔断继续？"

**大师**："会的。这就是 Sentinel Half-Open 机制的固有问题。因为是单探测请求，'误判'概率比 Hystrix 的多请求探测高。缓解方案有两个：一是在探测阶段使用简单、快速的接口（如 /health 或 ping 接口）作为探活目标；二是在业务层面对探测请求做特殊处理（比如使用缓存数据、限制查询范围）。"

**小胖**："那如果同一个资源配了多个熔断规则——比如同时配置了慢调用熔断和异常比例熔断——它们的状态是独立的吗？"

**大师**："是的。一个资源可以挂多个 `CircuitBreaker`，每个有独立的状态机和 `tryPass` 逻辑。`DegradeSlot.performDegradeCheck()` 会遍历所有 CircuitBreaker，**任意一个返回 false 就抛 DegradeException**。这意味着如果慢调用熔断器是 OPEN，即使异常比例熔断器是 CLOSED，请求也会被拒绝。"

**小白**："熔断规则的时间窗口（timeWindow）从什么时候开始计时？是从 Open 时刻开始，还是从最后一次拒绝开始？"

**大师**："从 Open 时刻开始。一旦 `fromCloseToOpen()` 被调用，`nextRetryTimestamp = currentTime + timeWindow * 1000` 就被确定了。在 `timeWindow` 期间的任何请求都会被 `tryPass()` 返回 false。即使在这期间下游服务已经恢复了，也要等到时间窗口到期。不过你可以通过修改规则并重新加载来提前结束熔断。"

**小胖**："如果我想基于业务错误码（而不只是异常）来做熔断，应该怎么扩展？"

**大师**："Sentinel 内置的 `ExceptionCircuitBreaker` 只统计 Java 异常。如果需要基于业务错误码（如返回值中的 `code != 200`），你需要实现自己的 `CircuitBreaker` 并注册到 `DegradeRuleManager`。关键是在 `StatisticSlot.exit()` 中判断业务错误码 → 调用自定义 MetricEvent 的计数。你可以在第 37 章的 SPI 扩展机制中学到具体做法。"

## 3 项目实战

### 3.1 DegradeSlot 核心流程

```java
// DegradeSlot.java
public void entry(Context context, ResourceWrapper r, DefaultNode node,
                  int count, boolean prioritized, Object... args) throws Throwable {
    performDegradeCheck(context, r, node, count);

    fireEntry(context, r, node, count, prioritized, args);
}

void performDegradeCheck(Context context, ResourceWrapper r, DefaultNode node, int count) {
    List<CircuitBreaker> circuitBreakers = DegradeRuleManager.getCircuitBreakers(r.getName());
    if (circuitBreakers == null || circuitBreakers.isEmpty()) return;

    for (CircuitBreaker cb : circuitBreakers) {
        if (!cb.tryPass(context)) {
            throw new DegradeException(cb.getRule().getLimitApp(), cb.getRule());
        }
    }
}
```

### 3.2 CircuitBreaker 接口与状态机

```java
// CircuitBreaker.java 接口
public interface CircuitBreaker {
    boolean tryPass(Context context);
    State currentState();
    DegradeRule getRule();
}

// AbstractCircuitBreaker.java — 状态机核心
public abstract class AbstractCircuitBreaker implements CircuitBreaker {

    protected volatile State state = State.CLOSED;
    protected AtomicReference<State> currentState = new AtomicReference<>(State.CLOSED);

    // 状态切换
    protected boolean fromCloseToOpen(double snapshotValue) {
        State prev = currentState.get();
        if (prev == State.CLOSED) {
            return currentState.compareAndSet(prev, State.OPEN);
        }
        return false;
    }

    protected boolean fromOpenToHalfOpen() {
        return currentState.compareAndSet(State.OPEN, State.HALF_OPEN);
    }

    protected boolean fromHalfOpenToClose() {
        return currentState.compareAndSet(State.HALF_OPEN, State.CLOSED);
    }

    protected boolean fromHalfOpenToOpen(double snapshotValue) {
        return currentState.compareAndSet(State.HALF_OPEN, State.OPEN);
    }
}
```

### 3.3 慢调用熔断器

```java
// ResponseTimeCircuitBreaker.java 的核心 tryPass()
public boolean tryPass(Context context) {
    if (state == State.CLOSED) {
        return true;  // 正常处理，由 StatisticSlot.exit() 中判断是否熔断
    }
    if (state == State.OPEN) {
        // 检查是否到了 Half-Open 时间
        if (TimeUtil.currentTimeMillis() - nextRetryTimestamp >= 0) {
            // 进入 Half-Open，放行一个探测请求
            if (fromOpenToHalfOpen()) {
                return true;  // 探测请求通过
            }
        }
        return false;  // 还在熔断期，拒绝
    }
    if (state == State.HALF_OPEN) {
        return false; // 探测阶段不额外放行请求
    }
    return true;
}
```

**关键点**：熔断关闭的触发不在 `tryPass()` 中，而是在 `StatisticSlot.exit()` 中：

```java
// StatisticSlot.exit() 中的熔断判断逻辑
if (rt > rule.getCount()) {  // count = maxAllowedRt
    // 记录慢调用
    node.addSlowRequest(1);
}

// 检查是否达到熔断条件
double slowRatio = node.slowRequestRatio();
if (slowRatio > rule.getSlowRatioThreshold()
    && node.totalRequest() >= rule.getMinRequestAmount()) {
    // 触发熔断：Closed → Open
    circuitBreaker.fromCloseToOpen(slowRatio);
    // 记录熔断时间
    circuitBreaker.updateNextRetryTimestamp();
}
```

### 3.4 异常熔断器

```java
// ExceptionCircuitBreaker.java
public boolean tryPass(Context context) {
    // 逻辑同慢调用熔断，区别在于判断指标是异常比例/异常数
    // 在 StatisticSlot.exit() 中检查：
    if (error != null) {
        node.addExceptionRequest(1);  // 记录异常
    }
    // 判断触发熔断条件
    if (rule.getGrade() == DEGRADE_GRADE_EXCEPTION_RATIO) {
        double exceptionRatio = node.exceptionRatio();
        if (exceptionRatio > rule.getCount()) {
            circuitBreaker.fromCloseToOpen(exceptionRatio);
        }
    } else if (rule.getGrade() == DEGRADE_GRADE_EXCEPTION_COUNT) {
        long exceptionCount = node.totalException();
        if (exceptionCount > rule.getCount()) {
            circuitBreaker.fromCloseToOpen(exceptionCount);
        }
    }
}
```

### 3.5 状态机图绘制

验证熔断状态机变化：

```bash
# 注入慢调用 → 观察日志
tail -f ~/logs/csp/sentinel-record.log | grep -E "degrade from|HALF_OPEN"

# 输出示例：
# [queryStock] degrade from CLOSED to OPEN
# [queryStock] HALF_OPEN probe passed, from HALF_OPEN to CLOSED
# [queryStock] HALF_OPEN probe failed, from HALF_OPEN to OPEN
```

### 3.6 规则变更对熔断状态的影响

```java
// 当 Nacos 推送了熔断规则更新时
// DegradeRuleManager 会重新加载规则，但 CircuitBreaker 的状态呢？

// Sentinel 的行为：
// - 如果旧规则对应的 CircuitBreaker 在新规则列表中存在（相同资源+相同策略）
//   状态保持不变（如已 Open 继续 Open）
// - 如果旧规则已删除，CircuitBreaker 被移除，熔断状态也丢失
```

### 3.7 熔断恢复全流程验证测试

```java
@Test
public void testCircuitBreakerFullLifecycle() throws Exception {
    String resource = "testDegrade";
    int maxRt = 100;          // 慢调用阈值 100ms
    double slowRatioThreshold = 0.5;  // 慢调用比例 50%
    int minRequestAmount = 5;  // 最小请求数
    int timeWindow = 10;       // 熔断时长 10 秒

    // 1. 模拟正常调用（CLOSED 状态）
    for (int i = 0; i < 100; i++) {
        assertTrue(circuitBreaker.tryPass(null));  // 全部放行
    }

    // 2. 注入慢调用：50% 的请求 RT > 100ms
    simulateSlowCalls(resource, 50, 100);  // 50 个慢调用 + 50 个正常
    // 触发条件：慢调用比例 50% >= 50%，且 total >= 5
    // → 触发 CLOSED → OPEN

    assertEquals(State.OPEN, circuitBreaker.currentState());

    // 3. OPEN 状态下的拒绝行为
    for (int i = 0; i < 10; i++) {
        assertFalse(circuitBreaker.tryPass(null));
    }

    // 4. 等待 timeWindow 到期
    Thread.sleep(timeWindow * 1000 + 100);

    // 5. Half-Open 探测请求
    assertTrue(circuitBreaker.tryPass(null));  // 第一个请求通过（探测）
    assertEquals(State.HALF_OPEN, circuitBreaker.currentState());

    // 6. 探测期间其他请求被拒绝
    assertFalse(circuitBreaker.tryPass(null));

    // 7. 探测请求返回（模拟成功）
    // StatisticSlot.exit() 判断慢调用比例 < 50%
    // → fromHalfOpenToClose()
    simulateProbeSuccess(resource);
    assertEquals(State.CLOSED, circuitBreaker.currentState());
}
```

### 3.8 探测请求假阳性问题复现

```java
@Test
public void testHalfOpenFalsePositive() throws Exception {
    // 场景：Half-Open 探测请求恰好是慢请求 → 立即回到 OPEN

    circuitBreaker.fromCloseToOpen(0.8);  // 先进入 OPEN
    Thread.sleep(timeWindow * 1000);

    // Half-Open 放行探测请求
    assertTrue(circuitBreaker.tryPass(null));

    // 但这个探测请求的 RT 是 500ms（> maxRt=100ms）
    // StatisticSlot.exit() 判断：慢调用比例 = 100% > 50%
    // → fromHalfOpenToOpen()
    circuitBreaker.fromHalfOpenToOpen(1.0);

    assertEquals(State.OPEN, circuitBreaker.currentState());
    // 熔断重新开始，timeWindow 重新计时
    // 这就是假阳性 —— 一次正常的慢请求导致熔断继续
}
```

### 3.9 多 CircuitBreaker 并发状态验证

```java
@Test
public void testMultipleCircuitBreakers() {
    String resource = "multiDegrade";

    // 同时配置慢调用熔断 + 异常比例熔断
    CircuitBreaker slowBreaker = new ResponseTimeCircuitBreaker(
        new DegradeRule("multiDegrade")
            .setGrade(RuleConstant.DEGRADE_GRADE_RT)
            .setCount(100)
            .setTimeWindow(10));
    CircuitBreaker exceptionBreaker = new ExceptionCircuitBreaker(
        new DegradeRule("multiDegrade")
            .setGrade(RuleConstant.DEGRADE_GRADE_EXCEPTION_RATIO)
            .setCount(0.3)
            .setTimeWindow(20));

    // 场景 1：只有慢调用超限 → 慢调用熔断器 OPEN，异常熔断器还是 CLOSED
    slowBreaker.fromCloseToOpen(0.6);
    assertEquals(State.OPEN, slowBreaker.currentState());
    assertEquals(State.CLOSED, exceptionBreaker.currentState());

    // DegradeSlot 遍历时：slowBreaker.tryPass()=false → 直接抛异常
    // 即使 exceptionBreaker 还是 CLOSED，请求也会被拒绝
}
```

**踩坑记录**：

1. **Half-Open 只有一个探测请求**：如果这唯一一个请求因为非熔断原因失败（如网络闪断），会导致"假阳性"——熔断继续。建议在探测阶段使用"重试 + 幂等"的业务逻辑。
2. **熔断器与规则资源的对应关系**：一个资源可以有多个 CircuitBreaker（慢调用 + 异常比例 + 异常数），它们的状态是独立的。
3. **规则变更时的状态过渡**：如果修改了 timeWindow（如从 30 秒改为 10 秒），已熔断中的 CircuitBreaker 会立即按新 timeWindow 计算。

## 4 项目总结

### 4.1 熔断器状态机总结

```
                    ┌──────────────────────────────┐
                    │                              │
                    ▼                              │
               ┌─────────┐  慢调用/异常触发    ┌─────────┐
  初始状态 ──▶ │ CLOSED  │ ──────────────────▶ │  OPEN   │
               └─────────┘                    └────┬────┘
                                                  │ timeWindow 到期
                                                  ▼
              ┌──────────────┐               ┌─────────────┐
              │   CLOSED     │ ◀── 探测通过── │  HALF_OPEN  │
              └──────────────┘               └──────┬──────┘
                                                    │ 探测失败
                                                    ▼
                                              ┌─────────┐
                                              │  OPEN   │ (重新计时)
                                              └─────────┘
```

### 4.2 三种熔断策略对比

| 策略 | 触发条件 | 恢复条件 | 适用场景 | 注意 |
|------|---------|---------|---------|------|
| 慢调用比例 (RT) | slowRatio >= threshold && total >= minRequestAmount | Half-Open 探测请求 RT < maxRt | 下游服务变慢 | maxRt 设置需合理 |
| 异常比例 (Exception Ratio) | exceptionRatio >= threshold && total >= minRequestAmount | Half-Open 探测无异常 | 下游偶发异常 | 需区分业务异常和系统异常 |
| 异常数 (Exception Count) | exceptionCount >= threshold | Half-Open 探测无异常 | 分钟级异常计数 | 注意时间窗口内的累计 |

### 4.3 CircuitBreaker 状态切换关键字段

| 字段 | 类型 | 含义 | 更新时机 |
|------|------|------|---------|
| state | volatile State | 当前状态 CLOSED/OPEN/HALF_OPEN | CAS 原子切换 |
| nextRetryTimestamp | volatile long | 下次探测时间 (OPEN → HALF_OPEN) | fromCloseToOpen() |
| rule | DegradeRule | 绑定的熔断规则 | 规则加载时设置 |
| recoveryTimeoutMs | long | timeWindow 的毫秒表示 | 规则变更时更新 |

### 4.4 熔断器常见问题排查清单

- [ ] 熔断不恢复：检查 `nextRetryTimestamp` 是否被正确更新（timeWindow 到期了没？）
- [ ] 恢复后立即再熔断：探测请求只放一个，需要确保探测请求能代表真实情况
- [ ] 规则变更不生效：确认 `DegradeRuleManager.loadRules()` 是否正确调用了 `onRuleUpdate()`
- [ ] 多熔断器冲突：一个资源多个 CircuitBreaker，任意一个拒绝就整体拒绝
- [ ] minRequestAmount 理解偏差：时间窗口内总请求数不足 minRequestAmount 时不会触发熔断

### 4.5 Sentinel 熔断 vs Hystrix 熔断对比

| 维度 | Sentinel | Hystrix |
|------|---------|---------|
| 状态机 | CLOSED → OPEN → HALF_OPEN → CLOSED | 相同 |
| 半开探测请求数 | 1 个 | 可配置（默认 1） |
| 熔断触发指标 | 慢调用比例 / 异常比例 / 异常数 | 异常比例 |
| 统计窗口 | LeapArray 滑动窗口 | Hystrix 滚动窗口 |
| 规则动态变更 | 支持（通过 DataSource） | 需通过 Archaius |

### 4.6 思考题

1. 如果探测请求在 Half-Open 阶段通过了（慢调用比例 0%），但实际上是因为"刚好这一秒流量很低"造成的假象，熔断恢复后又会迅速触发。如何设计更稳健的半开探测策略？
2. 资源的慢调用熔断器的 maxAllowedRt 设为 100ms。如果某时间段内的 RT 平均值是 120ms（100% 慢调用），但异常比例是 0%。Sentinel 会触发熔断吗？

### 4.7 推广计划

- **核心开发**：理解熔断状态机的设计，在排查"熔断不恢复"问题时能定位是 Open 持续、Half-Open 持续还是探测失败。
- **架构师**：在特殊场景下可以自定义 CircuitBreaker 实现（如基于业务错误码的熔断）。
