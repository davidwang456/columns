# 第33章：窗口源码剖析与自定义Trigger

---

## 1. 项目背景

Flink内置的三种窗口（Tumbling/Sliding/Session）覆盖了大多数场景。但在生产环境中，总有复杂的业务规则需要"定制化"的窗口触发逻辑：

- 交易量达到1000笔才触发一次统计（不关心时间，只关心数量）
- 每天凌晨2:00触发一次窗口，统计截至凌晨2:00的数据（对齐到业务日凌晨而不是自然日）
- 窗口触发后，前10%的数据要提前输出（自定义早激发机制）

这些需求要求我们理解窗口的底层实现，并使用**自定义Trigger（触发器）** 和**自定义Evictor（驱逐器）**。

---

## 2. 项目设计

> 场景：运营需要"每收到1000个订单"统计一次平均客单价，而不是每5分钟。小胖发现内置窗口没有"按数量触发"的功能。

**大师**：Flink的窗口默认只有三种触发条件：时间（ProcessingTime/EventTime）、数量（CountTrigger）、会话间隔（Session）。但CountTrigger是"全局计数模式"——每条新增数据都触发，不区分窗口边界。

**技术映射：内置Trigger = 决定窗口什么时候"输出结果"。EventTimeTrigger = Watermark ≥ windowEnd；ProcessingTimeTrigger = 系统时间 ≥ windowEnd；CountTrigger = 元素数 ≥ 阈值。自定义Trigger = 继承Trigger类，实现onElement/onProcessingTime/onEventTime三个方法。**

**小白**：那Evictor是什么？我看源码里Trigger触发后还有个Evictor的步骤。

**大师**：Trigger决定"什么时候输出"，Evictor决定"输出前删除哪些元素"。默认的Evictor是空的（不删除任何元素）。自定义Evictor可以做：只保留最后100个元素 / 删除超过阈值的元素 / 对元素做排序后再输出。

**技术映射：Window处理三阶段——① Trigger判断是否触发 ② Evictor从窗口中移除元素 ③ 对剩余元素执行窗口函数。触发≠清空窗口，清空窗口需要通过Evictor或Trigger的clear()方法。**

---

## 3. 项目实战

### 分步实现

#### 步骤1：自定义Trigger——按数据量触发（而非时间）

**目标**：实现一个CountAndTimeTrigger——每收到N条数据或超过T时间触发一次（先到先得）。

```java
package com.flink.column.chapter33;

import org.apache.flink.streaming.api.windowing.triggers.Trigger;
import org.apache.flink.streaming.api.windowing.triggers.TriggerResult;
import org.apache.flink.streaming.api.windowing.windows.TimeWindow;

/**
 * 自定义Trigger：每收到N条元素或超时T时间触发一次
 * 不同于CountTrigger（每次add都触发），这个Trigger在accumulatedCount >= threshold时才触发
 * 且同时有超时保护
 */
public class CountAndTimeTrigger extends Trigger<Object, TimeWindow> {

    private final long maxCount;
    private final long maxDelayMs;

    public CountAndTimeTrigger(long maxCount, long maxDelayMs) {
        this.maxCount = maxCount;
        this.maxDelayMs = maxDelayMs;
    }

    @Override
    public TriggerResult onElement(Object element, long timestamp, TimeWindow window, TriggerContext ctx) {
        // 1. 注册下一个定时器（如果还没注册）
        if (ctx.getPartitionedState(
                new ValueStateDescriptor<>("first", Boolean.class)).value() == null) {
            // 第一次有数据进入窗口——注册一个超时定时器
            long delayTime = ctx.getCurrentProcessingTime() + maxDelayMs;
            ctx.registerProcessingTimeTimer(delayTime);
            ctx.getPartitionedState(
                    new ValueStateDescriptor<>("first", Boolean.class)).update(true);
        }

        // 2. 更新计数
        ValueStateDescriptor<Long> countDesc = new ValueStateDescriptor<>("count", Long.class);
        Long count = ctx.getPartitionedState(countDesc).value();
        if (count == null) count = 0L;
        count++;
        ctx.getPartitionedState(countDesc).update(count);

        // 3. 如果达到阈值，触发并清空窗口
        if (count >= maxCount) {
            // 清理定时器状态
            ctx.getPartitionedState(
                    new ValueStateDescriptor<>("first", Boolean.class)).clear();
            return TriggerResult.FIRE_AND_PURGE;  // 触发并清空窗口数据
        }

        return TriggerResult.CONTINUE;
    }

    @Override
    public TriggerResult onProcessingTime(long time, TimeWindow window, TriggerContext ctx) {
        // 超时了——触发并清空
        ctx.getPartitionedState(
                new ValueStateDescriptor<>("first", Boolean.class)).clear();
        ctx.getPartitionedState(
                new ValueStateDescriptor<>("count", Long.class)).clear();
        return TriggerResult.FIRE_AND_PURGE;
    }

    @Override
    public TriggerResult onEventTime(long time, TimeWindow window, TriggerContext ctx) {
        return TriggerResult.CONTINUE;
    }

    @Override
    public void clear(TimeWindow window, TriggerContext ctx) {
        ctx.getPartitionedState(
                new ValueStateDescriptor<>("first", Boolean.class)).clear();
        ctx.getPartitionedState(
                new ValueStateDescriptor<>("count", Long.class)).clear();
    }
}
```

**使用自定义Trigger**：

```java
DataStream<String> result = events
    .keyBy(e -> e.category)
    .window(TumblingEventTimeWindows.of(Time.hours(1)))
    .trigger(new CountAndTimeTrigger(1000, 60_000))  // 1000条或60秒触发
    .aggregate(new AverageAggregator())
    .name("category-avg-price");
```

#### 步骤2：自定义Evictor——保留窗口内最后N条数据

**目标**：在窗口触发输出前，只保留窗口内最后到达的N条数据。

```java
package com.flink.column.chapter33;

import org.apache.flink.streaming.api.windowing.evictors.Evictor;
import org.apache.flink.streaming.api.windowing.windows.TimeWindow;
import org.apache.flink.streaming.api.windowing.windows.Window;

import java.util.ArrayList;
import java.util.List;

/**
 * 自定义Evictor：窗口触发输出前，只保留最后N条数据
 */
public class LastNEvictor<T> implements Evictor<T, TimeWindow> {

    private final int n;

    public LastNEvictor(int n) {
        this.n = n;
    }

    @Override
    public void evictBefore(Iterable<TimestampedValue<T>> elements,
                            int size, TimeWindow window, EvictorContext ctx) {
        // 窗口函数执行前删除——保留最后N条
        List<TimestampedValue<T>> list = new ArrayList<>();
        elements.forEach(list::add);

        if (list.size() <= n) return;

        // 删除前面的（保留最后N条）
        int toRemove = list.size() - n;
        for (int i = 0; i < toRemove; i++) {
            // 实际实现需要用Iterator.remove()
            // 这里只是一个逻辑示意
        }
    }

    @Override
    public void evictAfter(Iterable<TimestampedValue<T>> elements,
                           int size, TimeWindow window, EvictorContext ctx) {
        // 窗口函数执行后不需要操作
    }
}
```

#### 步骤3：WindowOperator源码解读

**目标**：理解WindowOperator的内部数据结构和处理流程。

```java
// ========== WindowOperator.java 核心逻辑 ==========

// 1. 数据结构
// WindowOperator内部维护了一个 MapState<Window, List<T>> 或 HeapWindowBuffer
// 每个窗口一个独立的state buffer，存储该窗口内的所有元素

// 2. 数据处理流程（processElement）
// a. 根据EventTime/Count找到对应的Window
// b. 将元素加入该Window的buffer
// c. 调用 Trigger.onElement()
// d. 如果Trigger返回FIRE → 执行窗口函数（并调用Evictor）
// e. 如果返回FIRE_AND_PURGE → 执行窗口函数后清空buffer
// f. 如果返回PURGE → 仅清空buffer

// 3. Timer注册
// 每个窗口在接收到第一个元素时，注册一个"窗口结束时间"的定时器
// 对于EventTime窗口：注册 timer = windowEnd
// 对于ProcessingTime窗口：注册 timer = currentTime + windowSize

// 4. 定时器触发
// 定时器触发时调用 Trigger.onEventTime() / onProcessingTime()
// 如果Trigger返回FIRE → 触发窗口计算
```

#### 步骤4：自定义窗口分配器——对齐到业务日的"凌晨2点"

**目标**：实现一个自定义WindowAssigner，将事件分配到"业务日"窗口（每日凌晨2:00到次日凌晨2:00）。

```java
package com.flink.column.chapter33;

import org.apache.flink.streaming.api.windowing.assigners.WindowAssigner;
import org.apache.flink.streaming.api.windowing.windows.TimeWindow;
import java.time.*;

/**
 * 自定义窗口分配器：对齐到业务日凌晨2点
 * 窗口 = [前一天的02:00, 当天的02:00)
 */
public class BusinessDayWindowAssigner
        extends WindowAssigner<Object, TimeWindow> {

    private final ZoneId zone = ZoneId.of("Asia/Shanghai");

    @Override
    public Collection<TimeWindow> assignWindows(
            Object element, long timestamp, WindowAssignerContext context) {

        // 计算这个eventTime所属的业务日窗口
        Instant instant = Instant.ofEpochMilli(timestamp);
        ZonedDateTime dateTime = instant.atZone(zone);

        // 如果当前时间 < 02:00，窗口起止是昨天的02:00到今天的02:00
        // 如果当前时间 >= 02:00，窗口起止是今天的02:00到明天的02:00
        ZonedDateTime windowStart;
        if (dateTime.getHour() < 2) {
            windowStart = dateTime.toLocalDate()
                    .minusDays(1).atStartOfDay(zone).plusHours(2);
        } else {
            windowStart = dateTime.toLocalDate()
                    .atStartOfDay(zone).plusHours(2);
        }

        ZonedDateTime windowEnd = windowStart.plusDays(1);
        return Collections.singletonList(new TimeWindow(
                windowStart.toInstant().toEpochMilli(),
                windowEnd.toInstant().toEpochMilli()));
    }

    @Override
    public Trigger<Object, TimeWindow> getDefaultTrigger(StreamExecutionEnvironment env) {
        return EventTimeTrigger.create();
    }

    @Override
    public TypeSerializer<TimeWindow> getWindowSerializer(ExecutionConfig executionConfig) {
        return new TimeWindow.Serializer();
    }

    @Override
    public boolean isEventTime() {
        return true;
    }
}
```

### 可能遇到的坑

1. **自定义Trigger中注册的Timer太多导致性能下降**
   - 根因：每个窗口每个key都可能注册Timer，Timer数量 = 窗口数 × key数
   - 解决：只在onElement中注册一次Timer（通过State判断是否已注册）；使用ProcessingTimeTimer代替EventTimeTimer（精度要求不高时）

2. **自定义Evictor中遍历elements两次导致性能翻倍**
   - 根因：Evictor接受Iterable（只能单次遍历），但需要先统计数据再决定删除哪些
   - 解方：先用List复制一份（`new ArrayList<>(elements)`），或使用CountEvictor等不需要两次遍历的Evictor

3. **FIRE_AND_PURGE清空窗口后，同一个窗口又有新数据到来**
   - 这是正常行为——清空后新数据再次进入窗口，重新累积
   - 但如果不清空（用FIRE），窗口数据一直累积（适合"累加"语义）

---

## 4. 项目总结

### Trigger和Evictor的关系

```
数据到来 → 分配窗口 → 加入buffer → Trigger.onElement()
                                          │
                                    ┌─────┴─────┐
                                    │ CONTINUE  │ → 什么都不做
                                    │ FIRE      │ → Evictor → 窗口函数 → 不清空buffer
                                    │ FIRE_AND  │ → Evictor → 窗口函数 → 清空buffer
                                    │_PURGE     │
                                    │ PURGE     │ → 仅清空buffer（不触发窗口函数）
                                    └───────────┘
```

### 内置Trigger速查

| Trigger | 触发条件 | 适合场景 |
|---------|---------|---------|
| EventTimeTrigger | Watermark ≥ windowEnd | EventTime窗口（默认） |
| ProcessingTimeTrigger | 系统时间 ≥ windowEnd | ProcessingTime窗口 |
| CountTrigger | 元素数 ≥ 阈值 | 按数量触发 |
| DeltaTrigger | 最新元素与上一个元素的差值 ≥ 阈值 | 按变化量触发 |
| ContinuousEventTimeTrigger | 每隔N毫秒检查一次EventTime | 提前输出中间结果 |
| PurgingTrigger | 包装其他Trigger，FIRE后执行PURGE | 确保窗口清空 |

### 注意事项
- Trigger中使用ValueState保存状态时，注意State的生命周期与窗口一致——`clear()`时必须清理所有状态
- 自定义WindowAssigner必须保证同一元素只能分配给有限个窗口（通常1个或2个）
- 不要在Trigger中做耗时操作——它在数据处理线程中执行，阻塞会影响整个算子的吞吐

### 常见踩坑经验

**案例1：自定义Trigger注册的EventTimeTimer永远不会触发**
- 根因：EventTimeTimer的触发依赖Watermark推进。如果Watermark没有超过timer的设定时间，定时器永远不会触发
- 解方：确认Watermark策略正确；或改用ProcessingTimeTimer（依赖系统时钟）

**案例2：窗口触发后，Evictor中删除的元素在Checkpoint恢复后重新出现**
- 根因：Evictor删除的是"运行时内存中的数据"，而不影响State中存储的窗口元素
- 解方：窗口函数的结果应通过`FIRE_AND_PURGE`清除State；如果需要"删除窗口内某些元素"的语义，应该在ProcessFunction中手动管理

**案例3：自定义Trigger的onProcessingTime多次触发同一窗口**
- 根因：`onProcessingTime`中注册的定时器没有在`clear()`中删除
- 解方：在`clear()`中使用`deleteProcessingTimeTimer()`删除所有已注册的定时器

### 优点 & 缺点

| | 自定义Trigger/Evictor/WindowAssigner | 内置窗口（Tumbling/Sliding/Session） |
|------|-----------|-----------|
| **优点1** | 按任意条件触发——数据量/时间/业务事件组合 | 三种窗口+内置Trigger覆盖大多数场景 |
| **优点2** | 自定义Evictor精细控制窗口内元素 | Element全部参与计算，无法过滤 |
| **优点3** | 自定义WindowAssigner对齐任意业务时间 | 窗口对齐到自然时间，不支持偏移 |
| **缺点1** | 开发复杂度高——需理解WindowOperator内部机制 | 声明式API，一行代码即可 |
| **缺点2** | Trigger/Evictor中的状态管理容易出错 | 内置Trigger经过充分测试，稳定性高 |

### 适用场景

**典型场景**：
1. 按数据量触发窗口——每收到N条记录计算一次，而非按时间
2. 业务时间对齐——窗口起始偏移到凌晨2点而非0点
3. 窗口内数据筛选——只保留最后N条或按条件过滤后再计算
4. 提前激发中间结果——窗口未结束时输出部分结果

**不适用场景**：
1. 标准时间窗口需求——内置Tumbling/Sliding/Session完全满足
2. 追求最小开发和维护成本——自定义Trigger增加代码复杂度和测试负担

### 思考题

1. 建议自定义Trigger的`FIRE`和`FIRE_AND_PURGE`的区别。如果一个窗口的Trigger第一次返回FIRE，第二次返回FIRE_AND_PURGE，两次之间窗口内还保留着第一次触发时的元素——对输出结果有什么影响？

2. 自定义WindowAssigner返回的窗口时间范围可能非常大（如"业务周"窗口=7天），这对State的大小和Timer的注册有什么影响？应该如何在窗口结束时及时清理大窗口的状态？

---

> **完整代码**：本章完整代码请参考附录或访问 https://github.com/flink-column/flink-practitioner  
> **思考题答案**：见附录文件 `appendix-answers.md`
