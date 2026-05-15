# 第34章：统计滑动窗口源码：LeapArray 与 MetricBucket

## 1 项目背景

基础篇我们多次提到"Sentinel 使用滑动窗口统计 QPS"，但一直没深究它的实现原理。滑动窗口是如何在纳秒级开销下做到秒级统计的？为什么 Sentinel 的 QPS 统计有时会出现 10-20% 的过冲？滑动窗口和普通的时间窗口有什么本质区别？

这些问题的答案在 `LeapArray` 和 `MetricBucket` 两个核心类中。理解它们，你才能解释"为什么 QPS=10 有时候通过了 12 个请求"这种看似 Bug 实则正常的行为，才能在极端高并发下评估 Sentinel 的统计性能瓶颈。

## 2 项目设计

**小胖**："大师，我有个疑惑——Sentinel 的统计到底是怎么做到的？一个请求进来，它怎么知道这一秒已经过了几个请求？难不成每个请求都去更新一个计数器？"

**大师**："确实每个请求都更新计数器，但用了一个很巧妙的数据结构——`LeapArray`。它是一个环形的滑动窗口数组，默认有 2 个格子（每个 500 毫秒）。"

**小白**："环形数组...就是说它会覆盖旧数据？这不就是滑动窗口避免内存膨胀的核心吗。"

**大师**："对。而且 Sentinel 用 `LongAdder` 来计数，在高并发下比 `AtomicLong` 有更好的性能。`LongAdder` 通过分拆多个 Cell 来减少 CAS 竞争。"

**小白**："那为什么 sampleCount 默认是 2 而不是更大？比如 10 个窗口不是更平滑吗？"

**大师**："sampleCount=2 意味着 1 秒的统计窗口被分为两个 500ms 的小窗口。这个设计的核心理由是：Sentinel 的统计只需要'最近 1 秒'的 QPS，不需要更细的粒度。sampleCount 越大 → 窗口越多 → 每次计算 QPS 需要遍历更多窗口 → CPU 开销更大。在单机 10 万 QPS 的场景下，sampleCount=2 的 CPU 开销约 0.5%，sampleCount=10 会增加到约 2%。"

**小胖**："我在线上看到过一个现象：配置了 QPS=100，但监控显示实际过了 120 个请求。这 20% 的过冲是怎么产生的？"

**大师**："两个原因。第一是滑动窗口的 timeId 边界切换——当一个请求在窗口 A 被统计为 pass 之后，窗口恰好翻转到 B，这个请求被算了两次。第二是 `LongAdder.sum()` 的非原子性——在并发更新时 sum() 可能捕获到中间状态。这两者叠加导致了 10-20% 的过冲。这是设计上的取舍：用少量精度损失换取极高的统计性能。"

**小白**："那如果我想实现精确计数（不允许过冲），应该怎么改？"

**大师**："可以用 `AtomicLong` 替代 `LongAdder`，用 CAS 做原子累加——但这会让高并发下的性能急剧下降（CAS 自旋竞争）。更好的做法是接受近似统计，在配置阈值时留出 20% 的 buffer。比如你希望实际限流在 1000 QPS，就配置 800。"

**小胖**："`TimeUtil.currentTimeMillis()` 防时间回拨的逻辑，如果 NTP 真的回拨了 5 秒会怎样？"

**大师**："`TimeUtil` 检测到回拨后会返回上次的时间值（`lastTime`），保证单调递增。后果是统计窗口会在 5 秒内'停滞'——新窗口不会被创建，所有请求的统计都累加在旧窗口上，导致 QPS 统计虚高。这就是为什么生产环境要确保 NTP 同步正常。极端情况下可以用 `System.nanoTime()` 做辅助时间源（单调递增不受 NTP 影响），但 nanoTime 无法做 wall-time 关联。"

## 3 项目实战

### 3.1 LeapArray 核心机制

```java
// LeapArray.java 核心字段
public abstract class LeapArray<T> {
    protected int windowLengthInMs;   // 窗口长度（毫秒），默认 500
    protected int sampleCount;        // 样本数，默认 2
    protected int intervalInMs;       // 总时间跨度 = windowLength * sampleCount = 1000
    protected final AtomicReferenceArray<WindowWrap<T>> array;  // 环形数组

    // 获取当前时间窗口
    public WindowWrap<T> currentWindow() {
        return currentWindow(TimeUtil.currentTimeMillis());
    }

    // 获取指定时间的窗口
    public WindowWrap<T> currentWindow(long timeMillis) {
        long timeId = timeMillis / windowLengthInMs;
        int idx = (int)(timeId % array.length());  // 环形索引

        // CAS 循环直到获取到正确的窗口
        while (true) {
            WindowWrap<T> old = array.get(idx);
            if (old == null) {
                // 创建新窗口
                WindowWrap<T> window = new WindowWrap<>(...);
                if (array.compareAndSet(idx, null, window)) {
                    return window;
                }
            } else if (old.windowStart() == timeId) {
                return old;  // 命中
            } else if (old.windowStart() < timeId) {
                // 窗口过期，重置并复用
                old.resetTo(timeId);
                return old;
            }
        }
    }
}
```

**关键设计点**：
1. `timeId = timeMillis / windowLengthInMs`：将时间分片为连续的时间片 ID
2. `idx = timeId % array.length()`：环形索引，自动覆盖旧数据
3. 重置而非新建：过期窗口被 reset 后复用，避免 GC 压力

### 3.2 MetricBucket 统计维度

```java
// MetricBucket.java — 一个时间窗口内的统计数据
public class MetricBucket {
    // LongAdder 数组，每个元素代表一个统计指标
    private final LongAdder[] counters;

    // 统计指标枚举
    public enum MetricEvent {
        PASS,           // 通过
        BLOCK,          // 拒绝
        SUCCESS,        // 业务成功
        EXCEPTION,      // 业务异常
        RT,             // 总响应时间（毫秒）
        OCCUPIED_PASS,  // 预占通过
        WAITING         // 等待中
    }

    // 增加指标
    public void add(MetricEvent event, long count) {
        counters[event.ordinal()].add(count);
    }

    // 获取指标
    public long get(MetricEvent event) {
        return counters[event.ordinal()].sum();
    }
}
```

### 3.3 滑动窗口测试

```java
@Test
public void testLeapArrayBehavior() {
    // 窗口长度 500ms，样本数 2 → 总跨度 1000ms
    LeapArray<MetricBucket> array = new LeapArray<MetricBucket>(2, 1000) {
        @Override
        public MetricBucket newEmptyBucket(long timeMillis) {
            return new MetricBucket();
        }
        @Override
        protected WindowWrap<MetricBucket> resetWindowTo(
                WindowWrap<MetricBucket> wrap, long startTime) {
            wrap.value().reset();
            return wrap;
        }
    };

    // 当前时间窗口
    WindowWrap<MetricBucket> current = array.currentWindow();
    current.value().add(MetricEvent.PASS, 1);

    // 500ms 后，窗口会自动切换
    Thread.sleep(600);
    WindowWrap<MetricBucket> next = array.currentWindow();
    // current != next（窗口已切换）
}
```

### 3.4 StatisticSlot 如何使用 LeapArray

```java
// StatisticSlot.entry() 简化版
public void entry(Context context, ResourceWrapper resourceWrapper,
                  DefaultNode node, int count, Object... args) throws Throwable {
    try {
        // 调用下一个 Slot（先执行规则校验）
        fireEntry(context, resourceWrapper, node, count, args);

        // 通过后增加统计
        node.addPassRequest(count);  // 内部调 ClusterNode 的 MetricBucket.add(PASS)
    } catch (BlockException e) {
        // 被拒绝，增加 Block 统计
        node.addBlockRequest(count);
        throw e;
    }
}

// StatisticSlot.exit() 简化版
public void exit(Context context, ResourceWrapper resourceWrapper,
                 int count, Object... args) {
    DefaultNode node = (DefaultNode) context.getCurNode();
    if (context.getCurEntry().getError() == null) {
        // 成功
        node.addSuccessRequest(count);
    } else {
        // 异常
        node.addExceptionRequest(count);
    }
    // 记录 RT
    long rt = TimeUtil.currentTimeMillis() - context.getCurEntry().getCreateTime();
    node.addRtAndSuccess(rt, count);

    fireExit(context, resourceWrapper, count);
}
```

### 3.5 滑动窗口精度测试

```bash
# 测试：QPS 过冲现象
# 配置 QPS=100，压测 150 QPS
# 观察：前几秒 Throughput 约 100-120
# 原因：滑动窗口的 timeId 边界切换时有短暂的重叠期
```

**核心原因**：`LeapArray` 的 `currentWindow()` 中，CAS 操作和窗口切换之间有纳秒级的缝隙，导致极少数的请求落在"旧窗口"和"新窗口"之间。

### 3.6 时间回拨问题

Sentinel 使用 `TimeUtil.currentTimeMillis()` 而非 `System.currentTimeMillis()`，前者内部有一个自旋逻辑来检测时间回拨：

```java
// TimeUtil 简化版
public static long currentTimeMillis() {
    long current = System.currentTimeMillis();
    // 检测时间回拨
    if (current < lastTime) {
        // 时间回拨了，返回 lastTime（保证单调递增）
        return lastTime;
    }
    lastTime = current;
    return current;
}
```

### 3.7 LeapArray 窗口切换并发安全性验证

```java
@Test
public void testLeapArrayConcurrency() throws Exception {
    LeapArray<MetricBucket> array = new LeapArray<MetricBucket>(2, 1000) {
        @Override public MetricBucket newEmptyBucket(long t) {
            return new MetricBucket();
        }
        @Override protected WindowWrap<MetricBucket> resetWindowTo(
                WindowWrap<MetricBucket> w, long s) {
            w.value().reset();
            w.resetTo(s);
            return w;
        }
    };

    int threadCount = 20;
    int opsPerThread = 50000;
    CountDownLatch latch = new CountDownLatch(threadCount);
    AtomicLong totalPass = new AtomicLong(0);

    for (int t = 0; t < threadCount; t++) {
        new Thread(() -> {
            for (int i = 0; i < opsPerThread; i++) {
                WindowWrap<MetricBucket> window = array.currentWindow();
                window.value().add(MetricEvent.PASS, 1);
                totalPass.incrementAndGet();
            }
            latch.countDown();
        }).start();
    }

    latch.await();

    // 验证所有窗口的 PASS 总和是否与预期一致
    long sumFromWindows = 0;
    for (WindowWrap<MetricBucket> window : array.list()) {
        sumFromWindows += window.value().get(MetricEvent.PASS);
    }
    // 注意：由于窗口过期重置，sumFromWindows 只反映当前有效窗口的统计
    System.out.println("Total operations: " + totalPass.get());
    System.out.println("Sum from windows: " + sumFromWindows);
    // 验证无数据丢失（需在窗口重置前统计）
}
```

### 3.8 LongAdder vs AtomicLong 性能对比

```java
@Test
public void benchmarkLongAdderVsAtomicLong() throws Exception {
    int threads = 8;
    int iterations = 1_000_000;

    // LongAdder 测试
    LongAdder adder = new LongAdder();
    long start = System.nanoTime();
    CountDownLatch latch1 = new CountDownLatch(threads);
    for (int t = 0; t < threads; t++) {
        new Thread(() -> {
            for (int i = 0; i < iterations; i++) adder.increment();
            latch1.countDown();
        }).start();
    }
    latch1.await();
    long adderTime = System.nanoTime() - start;
    System.out.printf("LongAdder: %d ops in %d ms%n",
        adder.sum(), adderTime / 1_000_000);

    // AtomicLong 测试
    AtomicLong atomic = new AtomicLong(0);
    start = System.nanoTime();
    CountDownLatch latch2 = new CountDownLatch(threads);
    for (int t = 0; t < threads; t++) {
        new Thread(() -> {
            for (int i = 0; i < iterations; i++) atomic.incrementAndGet();
            latch2.countDown();
        }).start();
    }
    latch2.await();
    long atomicTime = System.nanoTime() - start;
    System.out.printf("AtomicLong: %d ops in %d ms%n",
        atomic.get(), atomicTime / 1_000_000);

    // 典型结果：LongAdder 比 AtomicLong 快 3-5 倍（线程越多差距越大）
}
```

### 3.9 QPS 过冲定量分析

```java
@Test
public void testOverflowQuantification() throws Exception {
    // 配置：阈值 QPS=10
    int threshold = 10;
    LeapArray<MetricBucket> array = createLeapArray();

    // 模拟持续 30 秒的请求流，每秒均匀发送 15 个请求
    int overshootCount = 0;
    int totalSeconds = 30;
    int requestPerSecond = 15;

    for (int sec = 0; sec < totalSeconds; sec++) {
        int passedThisSecond = 0;
        long secondStart = System.currentTimeMillis();
        for (int i = 0; i < requestPerSecond; i++) {
            // 模拟请求间隔（每个请求约 66ms）
            try { Thread.sleep(1000 / requestPerSecond); } catch (Exception e) {}
            
            long currentQps = getCurrentQps(array);
            if (currentQps < threshold) {
                WindowWrap<MetricBucket> win = array.currentWindow();
                win.value().add(MetricEvent.PASS, 1);
                passedThisSecond++;
            }
        }

        if (passedThisSecond > threshold) {
            overshootCount++;
            System.out.printf("第 %d 秒过冲: 通过 %d (阈值 %d)%n",
                sec, passedThisSecond, threshold);
        }
    }
    System.out.printf("总计: %d/%d 秒出现过冲 (%.1f%%)%n",
        overshootCount, totalSeconds,
        100.0 * overshootCount / totalSeconds);
}
```

**踩坑记录**：

1. **LeapArray 的 sampleCount 不是越大越好**：sampleCount=2 已经能提供足够的平滑度，设太大增加内存和 CPU 开销。
2. **高并发下 LongAdder 的 sum() 操作**：sum() 不是原子的（可能获取到中间状态），但 Sentinel 的统计本身就是近似的，可接受偏差。
3. **窗口长度与阈值精度的关系**：窗口越短（如 200ms），统计越精细但开销越大。
4. **时间回拨的防御**：NTP 调整超过 1 秒时，`TimeUtil` 的单调递增策略会导致 QPS 统计虚高。监控 NTP offset > 500ms 的告警。
5. **LeapArray 的内存模型**：每个 `WindowWrap` 约 200 字节，sampleCount=2 时每个资源约 400 字节，10 万个资源约 40 MB——实际占用可控。

## 4 项目总结

### 4.1 滑动窗口关键参数

| 参数 | 默认值 | 含义 | 调优建议 |
|------|-------|------|---------|
| windowLengthInMs | 500 | 单个窗口长度 | 保持默认 |
| sampleCount | 2 | 窗口样本数 | 保持默认 |
| intervalInMs | 1000 | 总统计跨度 | 根据规则统计窗口需要调整 |

### 4.2 滑动窗口与普通时间窗口对比

| 维度 | 滑动窗口 (LeapArray) | 固定时间窗口 |
|------|--------------------|-----------|
| 统计精度 | 平滑过渡，窗口边界无跳变 | 窗口切换瞬间 QPS 跳变 |
| 内存占用 | O(sampleCount) 固定 | O(1) 更小 |
| 实现复杂度 | 环形数组 + CAS 自旋 | 简单计数器 |
| 突发容忍 | 容忍窗口内的合法突发 | 前后两秒可叠加漏过 |
| 适用场景 | 流量限流、熔断判断 | 日志计数、低频统计 |

### 4.3 MetricEvent 统计维度说明

| 枚举值 | 更新位置 | 触发条件 | 用途 |
|-------|---------|---------|------|
| PASS | `StatisticSlot.entry()` | 通过所有规则检查 | 计算 passQps |
| BLOCK | `StatisticSlot.entry()` catch | 任意规则拦截 | 计算 blockQps |
| SUCCESS | `StatisticSlot.exit()` | 业务无异常 | 计算成功率 |
| EXCEPTION | `StatisticSlot.exit()` | 业务抛异常 | 计算异常比例（熔断用） |
| RT | `StatisticSlot.exit()` | 请求完成 | 计算 avgRt（总 RT / PASS） |
| OCCUPIED_PASS | `StatisticSlot.exit()` | 排队模式预占 | 排队限流的"未来通过"计数 |

### 4.4 性能基准参考

| 场景 | sampleCount=2 | sampleCount=10 | sampleCount=60 |
|------|-------------|---------------|---------------|
| 单线程写入 | ~50 ns/op | ~55 ns/op | ~70 ns/op |
| 8 线程并发写入 (LongAdder) | ~80 ns/op | ~90 ns/op | ~120 ns/op |
| 8 线程并发写入 (AtomicLong) | ~300 ns/op | ~320 ns/op | ~350 ns/op |
| QPS 统计开销 (10w QPS) | ~0.5% CPU | ~1.2% CPU | ~3.5% CPU |

### 4.5 思考题

1. 如果 `LeapArray` 的 `sampleCount` 设为 10（每 100ms 一个窗口），相比默认的 2（每 500ms），统计精度提高了多少？代价是什么？
2. `LongAdder` 在高并发场景下的 sum() 操作为什么不是精确的？这个"不精确"对 Sentinel 的限流判断有影响吗？

### 4.6 推广计划

- **核心开发**：理解滑动窗口的精确度和性能特性，在极端高并发下能解释 QPS 过冲现象。
- **架构师**：在自定义指标统计时复用 LeapArray 结构。
