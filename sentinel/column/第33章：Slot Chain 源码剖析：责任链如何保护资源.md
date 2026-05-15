# 第33章：Slot Chain 源码剖析：责任链如何保护资源

## 1 项目背景

第 32 章我们断点调试了 `SphU.entry()` 的完整调用链，看到了 9 个 Slot 依次执行。但这些 Slot 之间如何协作？如果某一个 Slot 抛出了异常，后续的 Slot 还会执行吗？如果我要插入一个自定义 Slot，应该放在什么位置？插入后有什么风险？

这些问题需要深入理解 Sentinel 的**责任链模式**（Chain of Responsibility）。本章将剖析 Slot 的抽象基类、加载机制、异常传递和 Entry 生命周期管理，并带你实现一个自定义 Slot。

## 2 项目设计

**小白**："我看到每个 Slot 都继承自 `AbstractLinkedProcessorSlot`，而且都有一个 `fireEntry()` 方法。这是不是就是责任链的标准实现？"

**大师**："对。`fireEntry(context, resource, node, count, args)` 的作用就是调用链上的**下一个** Slot。如果某个 Slot 没有调用 `fireEntry()`，链就断了——后续 Slot 不会执行。"

**小胖**："那如果我想要在 FlowSlot 前面插入一个自定义 Slot，怎么办？"

**大师**："通过 Sentinel 的 SPI 机制。你需要在 `META-INF/services/` 中注册你的 Slot 类，并用 `@SpiOrder` 控制顺序。但一定要注意：放在 FlowSlot 前面意味着你的 Slot 也会影响限流判断的结果。"

**小胖**："那 Slot 的 exit 方法呢？我看到每个 Slot 都有 exit 方法，但这个 exit 是按什么顺序调用的？是和 entry 一样正序执行，还是逆序？"

**大师**："好问题。`entry()` 是**正序**执行（NodeSelector → ... → ParamFlowSlot），而 `exit()` 是**逆序**执行（ParamFlowSlot → ... → NodeSelector）。这确保了资源的正确释放——后面进去的 Slot 先退出，类似于栈的后进先出。你可以在 StatisticSlot 的 exit 中看到 RT 的统计——因为它是倒数第二个退出的，此时所有业务逻辑已经执行完毕。"

**小白**："那如果我在自定义 Slot 的 entry 中抛了 BlockException，Sentinel 会怎么处理？我担心抛了异常后 exit 不会被调用，统计数据就丢了。"

**大师**："这正是 Sentinel 设计的精妙之处。CtEntry 的 `exit()` 中有一个 `error` 字段——如果 entry 过程中有异常，exit 时仍然会遍历所有已执行的 Slot 并调用它们的 exit。只不过统计时，BlockException 会被标记为 block 而非 error。你可以看 `CtSph.entryWithPriority()` 的 finally 块：`entry.exit()` 永远会被调用。"

**小胖**："那自定义 Slot 最常见的应用场景有哪些？除了业务标签降级，还有什么？"

**大师**："三个经典场景。第一，**调用链追踪埋点**——在 Slot 中向 Tracing 系统（如 SkyWalking）发送 Span 事件。第二，**多租户资源隔离**——根据租户 ID 分配不同的 Node，实现租户级别的限流。第三，**灰度流量标记**——在 Slot 中根据 Header 标记流量为灰度，后续 Slot 根据标记做不同的限流判断。"

**小胖**："大师，如果我在 NodeSelectorSlot 之前插一个 Slot——也就是 SpiOrder < -10000——会不会把调用树搞乱？"

**大师**："在 NodeSelectorSlot 之前插入是高风险操作——因为 NodeSelectorSlot 负责创建 DefaultNode 和构建调用树。你前面的 Slot 如果访问了 `context.getCurNode()`，拿到的可能是 null。除非你有明确的理由（比如需要在树构建之前做安全校验并直接拒绝），否则不建议在 NodeSelectorSlot 之前插入。"

**小白**："那自定义 Slot 能不能替代 @SentinelResource 注解？比如我想在自定义 Slot 中统一处理所有资源的 blockHandler？"

**大师**："不能替代。@SentinelResource 处理的是业务层的 BlockException 兜底，而 Slot 处理的是规则判断。它们的职责在 Sentinel 设计中是严格分开的——Slot 负责'判断要不要放行'，BlockHandler 负责'不放行之后怎么办'。你可以在 Slot 中抛 BlockException，但不能在 Slot 中返回降级结果——那是业务层的事。"

## 3 项目实战

### 3.1 自定义 Slot 实战

**需求**：业务需要为每个请求打上"业务标签"（如活动 ID、AB 实验分组），并基于标签做差异化限流。

```java
@SpiOrder(-3000)  // 在 NodeSelectorSlot(-10000) 之后、StatisticSlot(-2000) 之前
public class BizTagSlot extends AbstractLinkedProcessorSlot<DefaultNode> {

    @Override
    public void entry(Context context, ResourceWrapper resourceWrapper,
                      DefaultNode node, int count, boolean prioritized,
                      Object... args) throws Throwable {

        // 1. 从上下文获取业务标签
        String bizTag = context.getCurEntry().getResourceWrapper().getName();
        String activityId = (String) context.getAttachment("activityId");

        // 2. 对特定活动做自定义判断
        if ("DOUBLE_11".equals(activityId)) {
            // 特殊逻辑：双十一活动降级非核心查询
            if (resourceWrapper.getName().contains("query")
                && !resourceWrapper.getName().contains("order")) {
                throw new FlowException("双十一活动期间非核心查询降级");
            }
        }

        // 3. 调用下一个 Slot（必须！否则链断裂）
        fireEntry(context, resourceWrapper, node, count, prioritized, args);
    }

    @Override
    public void exit(Context context, ResourceWrapper resourceWrapper,
                     int count, Object... args) {
        // 4. exit 时也需要调用下一个 Slot 的 exit
        fireExit(context, resourceWrapper, count, args);
    }
}
```

注册 SPI（`META-INF/services/com.alibaba.csp.sentinel.slotchain.ProcessorSlot`）：

```
com.example.sentinel.ext.BizTagSlot
```

### 3.2 验证自定义 Slot

```java
@GetMapping("/order/create")
public String createOrder(@RequestParam(defaultValue = "NORMAL") String tag) {
    Context context = ContextUtil.getContext();
    context.setAttachment("activityId", tag);  // 设置业务标签

    try (Entry entry = SphU.entry("createOrder")) {
        return "下单成功";
    } catch (FlowException e) {
        return "被自定义 Slot 拦截: " + e.getMessage();
    }
}
```

测试：
```bash
# 正常请求
curl http://localhost:8090/order/create?tag=NORMAL  # 通过

# 双十一活动 + 非核心查询
curl http://localhost:8090/order/query?tag=DOUBLE_11  # 被自定义 Slot 拦截
```

### 3.3 Slot 异常传递机制

关键代码在 `CtEntry` 中：

```java
// CtEntry.exit() 简化版
protected void exitForContext(Context context, int count, Object... args) {
    if (context != null) {
        if (error != null) {
            // 如果 entry 期间有异常，exit 时也会记录
            context.setCurEntry(parent);
        }
        // 调用 chain 的 exit
        if (chain != null) {
            chain.exit(context, resourceWrapper, count, args);
        }
    }
}
```

异常传递规则：
- 如果某个 Slot 抛异常 → 后续 Slot 不执行（因为 `fireEntry()` 没被调用）
- `CtEntry.exit()` 仍会执行 → 已经通过的 Slot 会收到 exit 事件 → StatisticSlot 记录统计

### 3.4 各 Slot 职责速查

| Slot | SpiOrder | 职责 |
|------|----------|------|
| NodeSelectorSlot | -10000 | 创建/获取 DefaultNode |
| ClusterBuilderSlot | -9000 | 创建/获取 ClusterNode |
| LogSlot | -8000 | BlockException 发生时写日志 |
| StatisticSlot | -2000 | 统计 QPS/RT/线程数 |
| AuthoritySlot | 0 | 黑白名单检查 |
| SystemSlot | 1000 | 系统保护检查 |
| FlowSlot | 2000 | 流控规则检查 |
| DegradeSlot | 3000 | 熔断规则检查 |
| ParamFlowSlot | 4000 | 热点参数检查 |

### 3.5 自定义 Slot 的风险

1. **在 StatisticSlot 之前插入**：你的 Slot 消耗的时间也会被统计进 RT，可能导致 RT 偏高
2. **抛出非 BlockException**：业务异常会中断 Slot 链，但 Sentinel 不处理业务异常，可能影响统计
3. **忘记调用 fireEntry()**：链断裂，后续保护全部失效

### 3.5 多租户隔离 Slot 实战

业务场景：SaaS 平台，需要按租户（tenantId）做资源隔离和限流。

```java
@SpiOrder(-5000) // 在 StatisticSlot(-2000) 之前，确保统计正确
public class TenantIsolationSlot extends AbstractLinkedProcessorSlot<DefaultNode> {

    private final Map<String, ClusterNode> tenantNodes = new ConcurrentHashMap<>();

    @Override
    public void entry(Context context, ResourceWrapper resourceWrapper,
                      DefaultNode node, int count, boolean prioritized,
                      Object... args) throws Throwable {

        String tenantId = (String) context.getAttachment("tenantId");
        if (tenantId == null) {
            tenantId = "default";
        }

        // 为每个租户创建独立的 ClusterNode
        ClusterNode tenantNode = tenantNodes.computeIfAbsent(
            tenantId, k -> new ClusterNode());

        // 将租户 Node 替换为当前 Node（后续统计和限流都基于租户维度）
        ContextUtil.getContext().setCurNode(tenantNode);

        fireEntry(context, resourceWrapper, tenantNode, count, prioritized, args);
    }

    @Override
    public void exit(Context context, ResourceWrapper resourceWrapper,
                     int count, Object... args) {
        fireExit(context, resourceWrapper, count, args);
    }
}
```

验证：
```bash
# 租户 A 的请求
curl -H "X-Tenant-Id: tenantA" http://localhost:8090/order/create
# 租户 B 的请求
curl -H "X-Tenant-Id: tenantB" http://localhost:8090/order/create
# 两者共享全局 QPS 限制，但统计维度独立
```

### 3.6 Slot 单元测试

验证自定义 Slot 的正确性：

```java
@Test
public void testBizTagSlot_blockedInDouble11() {
    // 1. 构造 Context 并设置 activityId
    Context context = ContextUtil.enter("test_context");
    context.setAttachment("activityId", "DOUBLE_11");

    // 2. 调用 entry — 期望被自定义 Slot 拦截
    assertThrows(FlowException.class, () -> {
        try (Entry entry = SphU.entry("order_query")) {
            fail("应该在 BizTagSlot 中被拦截");
        }
    });

    ContextUtil.exit();
}

@Test
public void testBizTagSlot_normalPass() {
    Context context = ContextUtil.enter("test_context");
    context.setAttachment("activityId", "NORMAL");

    try (Entry entry = SphU.entry("order_create")) {
        assertNotNull(entry);
    } catch (BlockException e) {
        fail("正常请求不应该被拦截");
    }

    ContextUtil.exit();
}
```

### 3.7 Slot Chain 性能基准测试

```java
@Benchmark
@BenchmarkMode(Mode.Throughput)
@OutputTimeUnit(TimeUnit.SECONDS)
public class SlotChainBenchmark {

    @State(Scope.Thread)
    public static class BenchmarkState {
        @Setup(Level.Trial)
        public void setup() {
            FlowRuleManager.loadRules(Collections.singletonList(
                new FlowRule("bench_resource").setCount(Integer.MAX_VALUE)
            ));
        }
    }

    @Benchmark
    public void slotChainEntry(BenchmarkState state) {
        try (Entry entry = SphU.entry("bench_resource")) {
            // 空负载：测量 Slot Chain 纯遍历开销
        } catch (BlockException ignored) {}
    }

    // 结果（参考值）：单次 entry+exit 约 1-3 μs（9 个 Slot）
}
```

**踩坑记录**：
- 自定义 Slot 的 SPI 文件必须放在 `META-INF/services/` 下，且文件名必须是接口全限定名
- SpiOrder 值不是绝对顺序，而是相对顺序——越小越靠前

### 3.8 自定义 Slot 的集成测试

验证自定义 Slot 不会破坏 Sentinel 内置规则的正常工作：

```java
@SpringBootTest
class CustomSlotIntegrationTest {

    @BeforeEach
    void setUp() {
        // 1. 加载自定义 Slot（通过 SPI 自动发现）
        // 2. 加载标准流控规则
        FlowRule rule = new FlowRule("testResource")
                .setGrade(RuleConstant.FLOW_GRADE_QPS)
                .setCount(5);
        FlowRuleManager.loadRules(Collections.singletonList(rule));
    }

    @Test
    void testCustomSlotDoesNotBreakFlowRule() {
        // 验证自定义 Slot 存在（通过 SlotChain 反射检查）
        ProcessorSlotChain chain = SlotChainProvider.newSlotChain();
        assertTrue(chainContainsSlot(chain, "BizTagSlot"),
            "自定义 Slot 应在链中");

        // 验证流控规则仍然生效
        int passCount = 0, blockedCount = 0;
        for (int i = 0; i < 10; i++) {
            try (Entry e = SphU.entry("testResource")) {
                passCount++;
            } catch (FlowException ex) {
                blockedCount++;
            }
        }
        assertTrue(passCount >= 3, "至少 3 个请求应通过（QPS=5，考虑误差）");
        assertTrue(blockedCount >= 2, "至少 2 个请求应被限流");
    }

    private boolean chainContainsSlot(ProcessorSlotChain chain, String name) {
        // 遍历链，检查是否有指定名称的 Slot
        AbstractLinkedProcessorSlot<?> current = chain.getNext();
        while (current != null) {
            if (current.getClass().getSimpleName().equals(name)) {
                return true;
            }
            current = current.getNext();
        }
        return false;
    }
}
```

## 4 项目总结

### 4.1 Slot 扩展决策矩阵

| 场景 | 是否需要自定义 Slot | 推荐 SpiOrder | 替代方案 |
|------|-------------------|---------------|---------|
| 业务标签降级 | 是 | -3000 | Context Attachment + 规则判断 |
| 多租户隔离 | 是 | -5000 | 独立服务部署 |
| 调用链埋点 | 是 | -7000 | AOP + 拦截器 |
| 灰度流量路由 | 是 | -4000 | Spring Cloud LoadBalancer |
| 自定义限流算法 | 否 | — | 修改 FlowSlot 判断逻辑 |
| 日志增强 | 否 | — | LogSlot 已覆盖 |

### 4.2 Slot 扩展最佳实践

- 新 Slot 的 SpiOrder 建议在 **-5000 到 5000** 之间
- 必须在 `entry()` 中调用 `fireEntry()`，在 `exit()` 中调用 `fireExit()`
- 抛异常时必须考虑对后续 Slot 的影响
- `entry()` 和 `exit()` 中的耗时操作（如 RPC 调用）必须异步，否则会拖慢整个调用链
- 自定义 Slot 建议在 `exit()` 中添加 try-catch，防止其他 Slot 的异常传播

### 4.3 Slot 异常处理速查

| 异常类型 | 抛出位置 | 对统计的影响 | 对后续 Slot 的影响 |
|---------|---------|------------|------------------|
| BlockException | 规则 Slot（Flow/Degrade/System） | StatisticSlot 记录为 block | 不执行后续 Slot |
| 业务异常（RuntimeException） | 自定义 Slot | StatisticSlot 记录为 error | 不执行后续 Slot |
| SPI 加载异常 | SlotChainBuilder | 链构建失败 | 所有 Slot 不执行 |

### 4.4 思考题

1. 如果自定义 Slot 在 `entry()` 中抛了 `FlowException`，`StatisticSlot` 的 `entry()` 还会被调用吗？那 `StatisticSlot.exit()` 呢？
2. 自定义 Slot 中如果调用了 `fireEntry()` 两次（即调用同一个 Slot 两次），会发生什么？

### 4.5 推广计划

- **核心开发**：掌握 Slot 扩展机制，为团队的自定义需求（业务标签、埋点、审计）开发专用 Slot。
- **架构师**：审核所有自定义 Slot 的 SpiOrder，确保不破坏 Sentinel 的内置执行顺序。
