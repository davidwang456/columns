# 第35章：FlowSlot 源码：限流规则如何被执行

## 1 项目背景

前两章我们理解了 Slot Chain 的骨架和滑动窗口统计，但"规则到底怎么判断"还没细看。你在 Dashboard 上配了一条 QPS=10 的流控规则，点击保存——Sentinel 内部发生了什么？`FlowSlot` 是如何拿到规则的？三种流控效果（快速失败、预热、排队）在代码层面是怎么实现的？

理解 FlowSlot 的源码对于排查"规则不按预期生效"和"自定义限流逻辑"至关重要。本章将深入 FlowRuleChecker 的判断流程和三种 Controller 的实现细节。

## 2 项目设计

**小白**："FlowSlot 里我看到一个 `FlowRuleChecker.checkFlow()`，是不是所有的流控判断都在这里？"

**大师**："对。`FlowSlot.entry()` 做的事情很简单：从 `FlowRuleManager` 获取该资源的所有 FlowRule → 逐条调用 `FlowRuleChecker.checkFlow()` → 任一规则不通过就抛 `FlowException`。"

**小胖**："那三种流控效果的实现应该也在 FlowRuleChecker 里？"

**大师**："`FlowRuleChecker` 调用 `TrafficShapingController`。有三种实现：`DefaultController`（快速失败）、`WarmUpController`（预热）、`RateLimiterController`（匀速排队）。根据规则的 `controlBehavior` 字段选择对应的 Controller。"

**小白**："那一个资源配了多条流控规则的时候，FlowSlot 的判断顺序是什么？先配的先生效吗？"

**大师**："没错，FlowRuleChecker 是逐条遍历 `Collection<FlowRule>` 的。默认情况下规则的顺序就是它们在 List 中的顺序，**第一条不通过就直接抛异常，后面的规则不会再检查**。所以如果你对同一个 resource 配置了 QPS=100 和 QPS=500 两条规则，只有第一条会生效。"

**小胖**："如果我想实现"先检查来源限流，再检查 QPS 限流"这种分层判断呢？"

**大师**："可以通过调整规则在 List 中的顺序来控制优先级。也可以在 `canPassCheck` 逻辑中引入规则的 `limitApp` 字段判断——如果 limitApp='default' 表示对所有来源生效，limitApp='someApp' 只对指定来源生效。Sentinel 内部会优先匹配精确的 limitApp。"

**小胖**："ClusterNode 和 DefaultNode 的统计差异在 FlowSlot 里是怎么体现的？什么时候用 DefaultNode 什么时候用 ClusterNode？"

**大师**："这取决于 `FlowRule.strategy` 字段。`STRATEGY_DIRECT`（默认）直接用 `ClusterNode`——该资源所有入口的汇总统计。`STRATEGY_RELATE` 用关联资源的 `ClusterNode`。`STRATEGY_CHAIN` 用当前 Context 下入口资源的 `DefaultNode`——这就是"链路限流"的实现基础。`selectNodeByStrategy()` 方法封装了这四个策略的 Node 选择逻辑。"

**小白**："WarmUpController 的斜率 `slope` 是怎么算出来的？公式看起来挺复杂。"

**大师**："`slope` 的计算基于 Guava 的 `SmoothWarmingUp` 算法：`slope = (count - count/coldFactor) / warmUpPeriodSec / count`。其中 `coldFactor` 默认是 3，表示冷启动阶段的 QPS 是正常 QPS 的 1/3。`warmUpPeriodSec` 是预热时长。slope 决定了从冷启动到正常状态的"升温"曲线的斜率——斜率越大，升温越快。"

## 3 项目实战

### 3.1 FlowSlot 核心流程

```java
// FlowSlot.java
public void entry(Context context, ResourceWrapper resourceWrapper,
                  DefaultNode node, int count, boolean prioritized,
                  Object... args) throws Throwable {
    checkFlow(resourceWrapper, context, node, count, prioritized);
    fireEntry(context, resourceWrapper, node, count, prioritized, args);
}

void checkFlow(ResourceWrapper resource, Context context,
               DefaultNode node, int count, boolean prioritized)
        throws BlockException {
    FlowRuleChecker checker = checkerMap.get(resource.getName());
    if (checker != null) {
        checker.checkFlow(/* ... */);
    }
}
```

### 3.2 FlowRuleChecker 判断流程

```java
// FlowRuleChecker.java 核心判断
public void checkFlow(Function<String, Collection<FlowRule>> ruleProvider, ...) {
    Collection<FlowRule> rules = ruleProvider.apply(resource);
    for (FlowRule rule : rules) {
        if (!canPassCheck(rule, context, node, count, prioritized)) {
            throw new FlowException(rule.getLimitApp(), rule);
        }
    }
}

boolean canPassCheck(FlowRule rule, Context context,
                     DefaultNode node, int count, boolean prioritized) {
    // 1. 获取对应策略的 Controller
    TrafficShapingController controller = rule.getRater();

    // 2. 根据策略获取统计 Node
    Node selectedNode = selectNodeByStrategy(rule, context, node);
    //   - STRATEGY_DIRECT → node（当前资源的 DefaultNode）
    //   - STRATEGY_RELATE → 关联资源的 ClusterNode
    //   - STRATEGY_CHAIN → 入口资源的 DefaultNode

    // 3. 判断是否允许通过
    return controller.canPass(selectedNode, count, prioritized);
}
```

### 3.3 DefaultController（快速失败）

```java
// DefaultController.java — 默认实现
public boolean canPass(Node node, int acquireCount, boolean prioritized) {
    // 获取当前 QPS（通过 LeapArray 的 MetricBucket 计算）
    long currentQps = node.passQps();

    // 与阈值比较
    if (currentQps + acquireCount > count) {
        return false;
    }
    return true;
}
```

逻辑非常简单：当前窗口的 pass QPS + 本次请求数 > 阈值 → 拒绝。

### 3.4 WarmUpController（预热）

```java
// WarmUpController.java — 基于 Guava 的 SmoothWarmingUp 算法
public boolean canPass(Node node, int acquireCount, boolean prioritized) {
    long passQps = node.passQps();
    long previousQps = node.previousPassQps();  // 上一个窗口的 QPS

    syncToken(previousQps);   // 1. 基于上一窗口 QPS 同步令牌桶

    long restToken = storedTokens.get();  // 2. 当前令牌数
    if (restToken >= warningToken) {
        // 冷启动阶段：当前令牌数大于告警阈值
        // 阈值 = count（预热完成后的上限）→ 限制较严
        long aboveToken = restToken - warningToken;
        double warningQps = Math.nextUp(1.0 / (aboveToken * slope + 1.0 / count));
        if (passQps + acquireCount <= warningQps) {
            return true;
        }
    } else {
        // 预热完成：正常阈值
        if (passQps + acquireCount <= count) {
            return true;
        }
    }
    return false;
}
```

Warm Up 的核心是一个**令牌桶**：冷启动时桶里令牌多（表示系统冷），每放行一个请求消耗令牌 → 令牌逐渐减少 → 放行速率逐渐提高到满值。

### 3.5 RateLimiterController（匀速排队）

```java
// RateLimiterController.java — 令牌桶算法
public boolean canPass(Node node, int acquireCount, boolean prioritized) {
    // 计算当前时间应该生成的令牌数
    long currentTime = TimeUtil.currentTimeMillis();
    long costTime = Math.round(1.0 * acquireCount / count * 1000); // 每令牌耗时

    // 期望拿到令牌的时间
    long expectedTime = costTime + latestPassedTime.get();

    if (expectedTime <= currentTime) {
        // 令牌充足，立即通过
        latestPassedTime.set(currentTime);
        return true;
    } else {
        // 需要排队等待
        long waitTime = costTime + latestPassedTime.get() - currentTime;
        if (waitTime > maxQueueingTimeMs) {
            // 排队超时，拒绝
            return false;
        } else {
            // 排队等待
            long oldTime = latestPassedTime.addAndGet(costTime);
            waitTime = oldTime - currentTime;
            if (waitTime > maxQueueingTimeMs) {
                latestPassedTime.addAndGet(-costTime);
                return false;
            }
            // 实际 sleep 等待
            try {
                Thread.sleep(waitTime);
            } catch (InterruptedException e) { /* ... */ }
            return true;
        }
    }
}
```

### 3.6 调试三种 Controller

```java
@Test
public void testControllers() {
    // 1. DefaultController
    DefaultController defaultCtrl = new DefaultController(10);
    System.out.println(defaultCtrl.canPass(node, 1, false)); // 基于 passQps 判断

    // 2. WarmUpController（预热 10 秒）
    WarmUpController warmUpCtrl = new WarmUpController(20, 3, 10000);
    // 冷启动时限制约 20/3 ≈ 6.7 QPS

    // 3. RateLimiterController
    RateLimiterController rateCtrl = new RateLimiterController(5, 500);
    // 每 200ms 放一个，最多排队 500ms
}
```

### 3.7 预热效果验证测试

```java
@Test
public void testWarmUpBehavior() throws Exception {
    // 预热配置：正常 QPS=100, 预热时长 10s, coldFactor=3
    // 冷启动阶段速率上限 ≈ 100/3 ≈ 33 QPS
    WarmUpController controller = new WarmUpController(100, 3, 10000);
    Node mockNode = mock(Node.class);

    int coldPassed = 0;
    int warmPassed = 0;

    // 第 1 秒：冷启动阶段
    for (int i = 0; i < 100; i++) {
        when(mockNode.passQps()).thenReturn((long) coldPassed);
        when(mockNode.previousPassQps()).thenReturn(0L);
        if (controller.canPass(mockNode, 1, false)) {
            coldPassed++;
        }
    }
    System.out.println("冷启动阶段允许通过: " + coldPassed + " (预期 ~33)");

    // 等待预热完成
    Thread.sleep(12000);

    // 第 12 秒后：预热完成
    for (int i = 0; i < 100; i++) {
        when(mockNode.passQps()).thenReturn((long) warmPassed);
        when(mockNode.previousPassQps()).thenReturn((long) warmPassed);
        if (controller.canPass(mockNode, 1, false)) {
            warmPassed++;
        }
    }
    System.out.println("预热完成后允许通过: " + warmPassed + " (预期 ~100)");
}
```

### 3.8 RateLimiterController 排队精度验证

```java
@Test
public void testRateLimiterPrecision() throws Exception {
    // QPS=10, 即每 100ms 放一个, 最大排队 2000ms
    RateLimiterController controller = new RateLimiterController(10, 2000);
    Node mockNode = mock(Node.class);

    long start = System.currentTimeMillis();
    int passed = 0;
    int rejected = 0;

    // 模拟 1 秒内连续发 50 个请求
    for (int i = 0; i < 50; i++) {
        if (controller.canPass(mockNode, 1, false)) {
            passed++;
        } else {
            rejected++;
        }
    }
    long elapsed = System.currentTimeMillis() - start;

    System.out.println("1 秒内通过: " + passed + ", 拒绝: " + rejected);
    System.out.println("实际耗时: " + elapsed + "ms");
    // 预期：通过约 10 个（排队模式下每个请求被 sleep 约 100ms）
    // 50 个请求中有 10 个通过、约 18-20 个排队超时拒绝、其余拒绝
}
```

### 3.9 FlowRule 多规则匹配优先级测试

```java
@Test
public void testMultiRulePriority() {
    // 场景：同一个 resource 配置两条规则
    // 规则 1: QPS=10 (快速失败)
    // 规则 2: QPS=100 (快速失败)
    // 预期：只有规则 1 生效，因为遍历到第一条就抛出了 FlowException

    List<FlowRule> rules = Arrays.asList(
        new FlowRule("testResource").setCount(10).setGrade(FLOW_GRADE_QPS),
        new FlowRule("testResource").setCount(100).setGrade(FLOW_GRADE_QPS)
    );

    // 模拟 QPS=50 的场景
    // 规则1 判断：50 > 10 → 拒绝
    // 规则2 永远不会被检查到
    assertThrows(FlowException.class, () -> {
        // FlowRuleChecker.canPassCheck 内部会遍历 rules
    });
}
```

**踩坑记录**：

1. **WarmUpController 的数学公式**：基于 Guava 的 `SmoothWarmingUp` 算法，核心是令牌桶 + 预热因子。注意 `storedTokens` 最大值为 `maxToken = warmUpPeriodSec * count / coldFactor`。
2. **RateLimiterController 在慢请求场景下**：如果请求处理很慢（如 2 秒），排队效果会被放大。建议配合线程数限流使用。
3. **occupiedPass 的含义**：在排队模式下，"预占令牌"会在 `StatisticSlot.exit()` 时通过 `addOccupiedPass()` 更新指标。
4. **多规则场景的短路判断**：一旦某个规则抛出 `FlowException`，后续规则不再判断。如有分层限流需求，需要修改 `checkFlow` 方法中的 break 逻辑。
5. **clusterMode 与 FlowSlot 的关系**：集群模式下，`FlowRuleChecker` 最终调用 `ClusterFlowChecker`，后者走 Token Client/Server 协议，不经过本地的 `TrafficShapingController`。

## 4 项目总结

### 4.1 三种 Controller 源码对比

| Controller | 算法 | 适用场景 | 核心代码行数 | 精度 |
|-----------|------|---------|-----------|------|
| DefaultController | 直接计数比较 | 快速失败 | ~10 行 | 受滑动窗口过冲影响 |
| WarmUpController | 令牌桶预热 | 冷启动 | ~80 行 | 令牌桶同步有周期偏差 |
| RateLimiterController | 令牌桶 + 排队 | 匀速削峰 | ~60 行 | 精度取决于 Thread.sleep |

### 4.2 FlowSlot 核心流程速查

```
SphU.entry("resource")
  → CtSph.entryWithType()
    → ProcessorSlotChain.entry()
      → FlowSlot.entry()
        → FlowRuleChecker.checkFlow()
          → FlowRuleManager.getRules(resource)      // 获取所有规则
          → for each FlowRule:
              → TrafficShapingController.canPass()   // 三种实现之一
                → DefaultController:    passQps + acquireCount > count → false
                → WarmUpController:     syncToken() → check storedTokens → 比较
                → RateLimiterController: calculate expectedTime → sleep/wait
              → if false → throw FlowException
```

### 4.3 限流规则配置决策表

| 业务场景 | 推荐 Controller | 典型配置 | 注意事项 |
|---------|----------------|---------|---------|
| API 网关通用限流 | DefaultController | QPS=500, 快速失败 | 配合热点参数限流做细粒度控制 |
| 秒杀/抢购启动瞬间 | WarmUpController | QPS=2000, warmUpPeriodSec=30, coldFactor=3 | 避免 Cache 击穿 |
| 下游数据库保护 | RateLimiterController | QPS=50, maxQueueingTimeMs=500 | 配合线程数限流 |
| 批量导入/定时任务 | RateLimiterController | QPS=10, maxQueueingTimeMs=5000 | 长队列注意 OOM |
| 内部 RPC 调用 | DefaultController | QPS=1000 | 配合集群流控做全局限制 |

### 4.4 常见问题排查清单

- [ ] 规则不生效：检查 `FlowRuleManager.loadRules()` 是否正确调用了 `FlowRuleManager.getRules()`？
- [ ] 阈值不准：确认是滑动窗口的过冲（10-20%误差），还是规则本身配置问题？
- [ ] WarmUp 不生效：检查 `warmUpPeriodSec` 设置，预热期间 QPS 逐步上升，不是瞬时生效
- [ ] 排队模式延迟高：检查 `maxQueueingTimeMs`，如果设得太小请求被拒绝；设得太大请求在 Thread.sleep 中堆积
- [ ] 多规则冲突：确认规则在 List 中的顺序，第一条命中的规则即生效，后续规则被短路

### 4.5 思考题

1. 为什么 WarmUpController 依赖 `previousPassQps()` 而不是实时 `passQps()`？
2. 如果你需要实现一个新的流控效果——"令牌桶 + 浮动阈值（根据下游实时负载动态调整）"，应该怎么扩展？

### 4.6 推广计划

- **核心开发**：掌握三种 Controller 的算法思想，能根据业务需求选择合适的流控效果。
- **架构师**：在特殊场景下（如需要按业务指标动态调整阈值），可以基于 TrafficShapingController 接口扩展自定义实现。
