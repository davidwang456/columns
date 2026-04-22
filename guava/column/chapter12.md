# 第 12 章：Range 与 DiscreteDomain 范围建模

## 1 项目背景

在在线教育平台的课程预约系统中，工程师小郑遇到了一个复杂的业务规则问题。系统需要判断一个时间段是否与已预约的时间段冲突，需要根据年龄段推荐不同课程，还需要支持时间段的各种运算（如合并、交集、补集等）。

他最初用两个 `LocalDateTime` 字段来表示时间段，但冲突判断逻辑很快就变得复杂——需要考虑开区间、闭区间、空区间等边界情况。更麻烦的是年龄段的表示：是否包含边界？如何表示"18岁以上"？如何用统一的方式处理时间范围和数值范围？

**业务场景**：时间段管理、年龄段判断、数值区间查询、日程安排等需要范围建模的场景。

**痛点放大**：
- **边界处理混乱**：区间开闭不明确，判断时容易出错。
- **交集并集运算复杂**：手写比较逻辑，容易遗漏边界情况。
- **空区间处理**：没有统一的方式来表示无效区间。
- **代码重复**：时间和数值范围需要两套类似的代码。
- **可读性差**：比较逻辑淹没在业务代码中。

如果没有专门的范围抽象，这类任务将难以维护。

**技术映射**：Guava 的 `Range` 提供了专门的范围抽象，支持开闭区间定义、包含判断、集合运算（交集、并集、补集）等操作，配合 `DiscreteDomain` 还可以处理离散值的范围操作。

---

## 2 项目设计

**场景**：课程系统需求评审会，讨论时间段和年龄段规则。

---

**小胖**：（看着一堆时间比较代码）"我说，这时间段判断也太复杂了吧！我就想看两个时间段有没有重叠，写了十几行比较逻辑，还要考虑开闭区间。这不就跟食堂看两个排队队伍有没有交叉一样，明明一眼就能看出来，偏要画坐标轴分析？"

**小白**：（叹气）"而且开闭区间还容易出错。你说是 `[9:00, 10:00)` 表示 9 点到 10 点（不含 10 点），还是 `[9:00, 10:00]` 包含 10 点？

**大师**：（在白板上画数轴）"Guava 的 `Range` 就是专门解决这个问题的。看这段对比：

```java
// 传统写法：手写区间比较
boolean overlaps(LocalDateTime start1, LocalDateTime end1,
                 LocalDateTime start2, LocalDateTime end2) {
    return start1.isBefore(end2) && end1.isAfter(start2);
    // 等等，如果 end1 等于 start2 算不算重叠？
}

// Guava 写法：Range
Range<LocalDateTime> range1 = Range.closedOpen(start1, end1);
Range<LocalDateTime> range2 = Range.closedOpen(start2, end2);
boolean overlaps = range1.isConnected(range2) && !range1.intersection(range2).isEmpty();
```

**技术映射**：`Range` 就像是数学课上的区间表示法——它把边界是开是闭都明确编码了，让区间运算有了可靠的数学基础。"

**小胖**："那 `Range` 怎么定义区间类型？"

**小白**："有四种边界类型：

```java
Range.closed(1, 10);        // [1, 10]  闭区间
Range.open(1, 10);          // (1, 10)  开区间
Range.closedOpen(1, 10);    // [1, 10)  左闭右开
Range.openClosed(1, 10);    // (1, 10]  左开右闭
Range.atLeast(10);          // [10, +∞)  10以上
Range.atMost(10);           // (-∞, 10]  10以下
Range.greaterThan(10);      // (10, +∞)  大于10
Range.lessThan(10);         // (-∞, 10)  小于10
Range.all();                // (-∞, +∞)  全部
Range.singleton(10);        // [10, 10]  单点
```

这些都是不可变的，可以安全地共享。"

**大师**："`Range` 的运算能力很强大：

```java
Range<Integer> range1 = Range.closed(1, 10);
Range<Integer> range2 = Range.closed(5, 15);

// 包含判断
boolean contains = range1.contains(5);  // true

// 交集
Range<Integer> intersection = range1.intersection(range2);  // [5, 10]

// 并集（如果相连）
Range<Integer> span = range1.span(range2);  // [1, 15]

// 是否相连
boolean connected = range1.isConnected(range2);  // true

// 间隙（如果不相连）
Range<Integer> gap = range1.gap(anotherRange);
```

**技术映射**：`Range` 把区间的数学运算（交集、并集、包含）封装成方法调用，避免了手写比较逻辑的繁琐和错误。"

**小胖**："那 `DiscreteDomain` 是做什么的？"

**小白**："`DiscreteDomain` 用于处理离散值的范围操作，比如整数范围。它定义了：
- `next(value)`：下一个离散值
- `previous(value)`：上一个离散值
- `distance(start, end)`：两个值之间的距离

配合 `ContiguousSet` 可以创建离散值的集合视图：

```java
ImmutableSortedSet<Integer> set = ContiguousSet.create(
    Range.closed(1, 100), 
    DiscreteDomain.integers()
);
// 返回 [1, 2, 3, ..., 100] 的集合视图，不实际存储 100 个元素！
```

**大师**："注意 `Range` 的类型参数必须是 `Comparable`，而且 Guava 为常用类型提供了工具：
- `DiscreteDomain.integers()`
- `DiscreteDomain.longs()`
- 自定义类型需要实现 `DiscreteDomain`

**技术映射**：`DiscreteDomain` 把连续区间和离散集合桥接起来，让你可以像操作集合一样操作整数区间，而不用真的创建包含所有整数的集合。"

---

## 3 项目实战

### 环境准备

```xml
<dependency>
    <groupId>com.google.guava</groupId>
    <artifactId>guava</artifactId>
    <version>33.0.0-jre</version>
</dependency>
```

### 分步实现：课程预约与年龄段规则

**步骤目标**：用 `Range` 构建课程预约的时间段管理和年龄段判断系统。

**代码实现**：

```java
package com.example.guava.demo;

import com.google.common.base.Preconditions;
import com.google.common.collect.*;

import java.time.LocalDateTime;
import java.util.*;

/**
 * 课程预约与年龄段规则 - 使用 Range
 */
public class CourseBookingSystem {

    // 已预约时间段集合
    private List<Range<LocalDateTime>> bookedRanges = new ArrayList<>();

    /**
     * 创建时间段
     */
    public Range<LocalDateTime> createTimeRange(LocalDateTime start, LocalDateTime end) {
        Preconditions.checkArgument(start.isBefore(end), "Start must be before end");
        // 左闭右开区间：包含开始，不包含结束
        return Range.closedOpen(start, end);
    }

    /**
     * 检查时间段是否可用（不与已预约冲突）
     */
    public boolean isTimeSlotAvailable(Range<LocalDateTime> requestedRange) {
        for (Range<LocalDateTime> booked : bookedRanges) {
            // 如果两个区间有交集，则冲突
            if (booked.isConnected(requestedRange) && 
                !booked.intersection(requestedRange).isEmpty()) {
                return false;
            }
        }
        return true;
    }

    /**
     * 预约时间段
     */
    public boolean bookTimeSlot(Range<LocalDateTime> range) {
        if (!isTimeSlotAvailable(range)) {
            return false;
        }
        bookedRanges.add(range);
        return true;
    }

    /**
     * 找出所有冲突的时间段
     */
    public List<Range<LocalDateTime>> findConflicts(Range<LocalDateTime> range) {
        return bookedRanges.stream()
            .filter(booked -> booked.isConnected(range) && 
                    !booked.intersection(range).isEmpty())
            .collect(java.util.stream.Collectors.toList());
    }

    /**
     * 计算可用时间段（给定范围内减去已预约）
     */
    public List<Range<LocalDateTime>> findAvailableSlots(
            Range<LocalDateTime> overallRange, 
            int minDurationMinutes) {
        
        List<Range<LocalDateTime>> available = new ArrayList<>();
        LocalDateTime current = overallRange.lowerEndpoint();
        LocalDateTime upperBound = overallRange.upperEndpoint();
        
        // 按开始时间排序已预约段
        List<Range<LocalDateTime>> sorted = bookedRanges.stream()
            .filter(r -> r.isConnected(overallRange))
            .sorted(Comparator.comparing(Range::lowerEndpoint))
            .collect(java.util.stream.Collectors.toList());
        
        for (Range<LocalDateTime> booked : sorted) {
            LocalDateTime bookedStart = booked.lowerEndpoint();
            LocalDateTime bookedEnd = booked.upperEndpoint();
            
            // current 到 bookedStart 之间是空闲的
            if (current.isBefore(bookedStart)) {
                Range<LocalDateTime> freeSlot = Range.closedOpen(current, bookedStart);
                if (isDurationEnough(freeSlot, minDurationMinutes)) {
                    available.add(freeSlot);
                }
            }
            
            // current 移动到 bookedEnd 之后
            if (current.isBefore(bookedEnd)) {
                current = bookedEnd;
            }
        }
        
        // 最后一个空闲段
        if (current.isBefore(upperBound)) {
            Range<LocalDateTime> lastSlot = Range.closedOpen(current, upperBound);
            if (isDurationEnough(lastSlot, minDurationMinutes)) {
                available.add(lastSlot);
            }
        }
        
        return available;
    }

    private boolean isDurationEnough(Range<LocalDateTime> range, int minutes) {
        long actualMinutes = java.time.Duration.between(
            range.lowerEndpoint(), 
            range.upperEndpoint()
        ).toMinutes();
        return actualMinutes >= minutes;
    }

    // ========== 年龄段规则 ==========

    /**
     * 创建年龄段
     */
    public Range<Integer> createAgeRange(int minAge, int maxAge) {
        Preconditions.checkArgument(minAge <= maxAge, "Min age must be <= max age");
        // 闭区间：包含边界
        return Range.closed(minAge, maxAge);
    }

    /**
     * 检查年龄是否在范围内
     */
    public boolean isAgeInRange(int age, Range<Integer> range) {
        return range.contains(age);
    }

    /**
     * 获取离散的年龄集合（用于枚举显示）
     */
    public ImmutableSortedSet<Integer> getAgeSet(Range<Integer> range) {
        return ContiguousSet.create(range, DiscreteDomain.integers());
    }

    /**
     * 找出推荐的课程年龄段（基于用户年龄段和可用课程段的交集）
     */
    public Range<Integer> findRecommendedAgeRange(
            Range<Integer> userAgeRange, 
            Range<Integer> courseAgeRange) {
        
        if (!userAgeRange.isConnected(courseAgeRange)) {
            return null;  // 无交集
        }
        
        return userAgeRange.intersection(courseAgeRange);
    }

    // ========== TreeRangeSet：高效的范围集合 ==========

    /**
     * 使用 TreeRangeSet 管理课程排班
     */
    private TreeRangeSet<LocalDateTime> courseSchedule = TreeRangeSet.create();

    public void scheduleCourse(Range<LocalDateTime> timeRange) {
        courseSchedule.add(timeRange);
    }

    public boolean isScheduled(Range<LocalDateTime> timeRange) {
        return courseSchedule.encloses(timeRange);
    }

    public void cancelSchedule(Range<LocalDateTime> timeRange) {
        courseSchedule.remove(timeRange);
    }

    /**
     * 找出排班间隙（可用于插入新课程）
     */
    public RangeSet<LocalDateTime> findScheduleGaps(Range<LocalDateTime> overallRange) {
        return courseSchedule.complement().subRangeSet(overallRange);
    }

    // ========== 测试入口 ==========
    public static void main(String[] args) {
        CourseBookingSystem system = new CourseBookingSystem();

        // 测试时间段
        System.out.println("=== 时间段测试 ===");
        LocalDateTime now = LocalDateTime.now();
        
        Range<LocalDateTime> slot1 = system.createTimeRange(
            now.plusHours(1), now.plusHours(2));
        Range<LocalDateTime> slot2 = system.createTimeRange(
            now.plusHours(1).plusMinutes(30), now.plusHours(3));
        Range<LocalDateTime> slot3 = system.createTimeRange(
            now.plusHours(3), now.plusHours(4));
        
        System.out.println("Slot 1: " + slot1);
        System.out.println("Slot 2: " + slot2);
        System.out.println("Slot 1 与 Slot 2 相连? " + slot1.isConnected(slot2));
        System.out.println("Slot 1 与 Slot 2 交集: " + slot1.intersection(slot2));
        System.out.println("Slot 1 与 Slot 3 相连? " + slot1.isConnected(slot3));

        // 测试预约
        System.out.println("\n=== 预约测试 ===");
        system.bookTimeSlot(slot1);
        System.out.println("预约 Slot 1: 成功");
        System.out.println("预约 Slot 2: " + (system.bookTimeSlot(slot2) ? "成功" : "失败（冲突）"));
        System.out.println("预约 Slot 3: " + (system.bookTimeSlot(slot3) ? "成功" : "失败"));

        // 测试年龄段
        System.out.println("\n=== 年龄段测试 ===");
        Range<Integer> children = system.createAgeRange(6, 12);
        Range<Integer> teens = system.createAgeRange(13, 18);
        Range<Integer> courseRange = system.createAgeRange(10, 15);
        
        System.out.println("儿童段: " + children);
        System.out.println("青少年段: " + teens);
        System.out.println("课程段: " + courseRange);
        System.out.println("10岁在儿童段? " + system.isAgeInRange(10, children));
        System.out.println("儿童段与课程段交集: " + system.findRecommendedAgeRange(children, courseRange));
        System.out.println("青少年段与课程段交集: " + system.findRecommendedAgeRange(teens, courseRange));

        // 测试离散年龄集合
        System.out.println("\n儿童段包含的年龄: " + system.getAgeSet(children).subSet(8, 11));

        // 测试 RangeSet
        System.out.println("\n=== RangeSet 排班测试 ===");
        system.scheduleCourse(slot1);
        system.scheduleCourse(slot3);
        System.out.println("排班间隙（可用于插入课程）:");
        RangeSet<LocalDateTime> gaps = system.findScheduleGaps(
            Range.closedOpen(now, now.plusHours(5)));
        gaps.asRanges().forEach(System.out::println);
    }
}
```

### 测试验证

```java
package com.example.guava.demo;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

import java.time.LocalDateTime;
import java.util.List;

public class CourseBookingSystemTest {

    private CourseBookingSystem system;

    @BeforeEach
    public void setUp() {
        system = new CourseBookingSystem();
    }

    @Test
    public void testCreateTimeRange() {
        LocalDateTime start = LocalDateTime.now();
        LocalDateTime end = start.plusHours(1);
        
        var range = system.createTimeRange(start, end);
        assertTrue(range.contains(start));  // 包含起点
        assertFalse(range.contains(end));   // 不包含终点（左闭右开）
    }

    @Test
    public void testOverlappingRanges() {
        LocalDateTime now = LocalDateTime.now();
        var range1 = system.createTimeRange(now, now.plusHours(2));
        var range2 = system.createTimeRange(now.plusHours(1), now.plusHours(3));
        
        assertTrue(range1.isConnected(range2));
        assertFalse(range1.intersection(range2).isEmpty());
    }

    @Test
    public void testNonOverlappingRanges() {
        LocalDateTime now = LocalDateTime.now();
        var range1 = system.createTimeRange(now, now.plusHours(1));
        var range2 = system.createTimeRange(now.plusHours(2), now.plusHours(3));
        
        // 注意：左闭右开区间 [now, now+1) 和 [now+2, now+3) 是不相连的
        assertFalse(range1.isConnected(range2));
    }

    @Test
    public void testBookTimeSlot() {
        LocalDateTime now = LocalDateTime.now();
        var range = system.createTimeRange(now, now.plusHours(1));
        
        assertTrue(system.bookTimeSlot(range));
        assertFalse(system.isTimeSlotAvailable(range));  // 已被预约
    }

    @Test
    public void testBookConflictingSlot() {
        LocalDateTime now = LocalDateTime.now();
        var range1 = system.createTimeRange(now, now.plusHours(2));
        var range2 = system.createTimeRange(now.plusHours(1), now.plusHours(3));
        
        assertTrue(system.bookTimeSlot(range1));
        assertFalse(system.bookTimeSlot(range2));  // 冲突
    }

    @Test
    public void testAgeRange() {
        var range = system.createAgeRange(6, 12);
        
        assertTrue(system.isAgeInRange(6, range));   // 包含边界
        assertTrue(system.isAgeInRange(12, range));  // 包含边界
        assertTrue(system.isAgeInRange(8, range));
        assertFalse(system.isAgeInRange(5, range));
        assertFalse(system.isAgeInRange(13, range));
    }

    @Test
    public void testAgeIntersection() {
        var userRange = system.createAgeRange(10, 15);
        var courseRange = system.createAgeRange(6, 12);
        
        var intersection = system.findRecommendedAgeRange(userRange, courseRange);
        assertNotNull(intersection);
        assertTrue(intersection.contains(10));
        assertTrue(intersection.contains(12));
    }

    @Test
    public void testAgeSet() {
        var range = system.createAgeRange(8, 10);
        var set = system.getAgeSet(range);
        
        assertEquals(3, set.size());  // 8, 9, 10
        assertTrue(set.contains(8));
        assertTrue(set.contains(10));
    }

    @Test
    public void testIntersection() {
        var range1 = com.google.common.collect.Range.closed(1, 10);
        var range2 = com.google.common.collect.Range.closed(5, 15);
        
        var intersection = range1.intersection(range2);
        assertEquals(com.google.common.collect.Range.closed(5, 10), intersection);
    }

    @Test
    public void testSpan() {
        var range1 = com.google.common.collect.Range.closed(1, 10);
        var range2 = com.google.common.collect.Range.closed(5, 15);
        
        var span = range1.span(range2);
        assertEquals(com.google.common.collect.Range.closed(1, 15), span);
    }
}
```

### 可能遇到的坑及解决方法

| 坑点 | 现象 | 解决方法 |
|------|------|----------|
| `closedOpen` vs `closed` | 边界包含性混淆 | 明确业务需求，文档化区间类型 |
| `intersection()` 抛异常 | 不相交时抛 IAE | 先用 `isConnected` 判断 |
| `DiscreteDomain` 范围过大 | 内存问题 | 大范围避免用 `ContiguousSet` |
| 时间精度问题 | 毫秒级差异导致不相连 | 统一时间精度到分钟或秒 |

---

## 4 项目总结

### 优缺点对比

| 维度 | Range | 手写比较 | 数据库区间类型 |
|------|-------|----------|----------------|
| 开闭语义 | ★★★★★ 明确 | ★★ 容易混淆 | ★★★★ 部分支持 |
| 区间运算 | ★★★★★ 完整 | ★★ 需手写 | ★★★★ 部分支持 |
| 可读性 | ★★★★★ 清晰 | ★★ 淹没在逻辑中 | ★★★★ SQL |
| 内存效率 | ★★★★★ 只存边界 | ★★★★★ 相同 | ★★ 索引开销 |
| 持久化 | ★ 内存 only | ★ 内存 only | ★★★★★ 支持 |

### 适用场景

1. **时间段管理**：预约、排班、日程
2. **年龄段判断**：课程分级、权限分级
3. **数值区间查询**：价格段、评分段
4. **版本范围**：API 版本兼容性
5. **IP 段判断**：白名单、地理位置

### 不适用场景

1. **需持久化存储**：用数据库区间类型
2. **超大数据集**：考虑区间树或线段树
3. **复杂空间范围**：用 JTS 等空间库
4. **非连续区间**：用 `RangeSet` 替代

### 生产踩坑案例

**案例 1：时间精度导致不相连**
```java
Range<LocalDateTime> r1 = Range.closedOpen(start, start.plusHours(1));
Range<LocalDateTime> r2 = Range.closedOpen(start.plusHours(1).plusNanos(1), ...);
// r1 和 r2 不相连，因为中间差了 1 纳秒！
```
解决：统一使用 `truncatedTo(ChronoUnit.MINUTES)`。

**案例 2：intersection() 不相交时抛异常**
```java
Range<Integer> r1 = Range.closed(1, 5);
Range<Integer> r2 = Range.closed(10, 15);
r1.intersection(r2);  // IllegalArgumentException！
```
解决：先用 `isConnected` 判断。

**案例 3：ContiguousSet 内存爆炸**
```java
ContiguousSet.create(Range.closed(1, 100000000), DiscreteDomain.integers());
// 内存溢出！
```
解决：大范围不要用 `ContiguousSet`，用 `Range` 表示即可。

### 思考题答案（第 11 章思考题 1）

> **问题**：`BiMap` 的 `forcePut` 在什么场景下必须使用？

**答案**：当需要**替换已有映射**而不先手动移除时使用。例如：
1. 用户修改 SKU 对应的平台 ID
2. 平台 ID 已被其他 SKU 使用
3. 业务要求新的映射生效，旧映射自动解除

注意：`forcePut` 会原子性地完成"移除旧映射+建立新映射"。

### 新思考题

1. 如何用 `RangeSet` 实现一个高效的时间段合并算法？
2. 设计一个支持夏令时的时间范围系统，需要考虑哪些边界情况？

### 推广计划提示

**开发**：
- 时间段/数值段优先使用 Range
- 注意开闭区间的文档化

**测试**：
- 边界测试：区间端点
- 相交/不相交场景的覆盖

**运维**：
- 监控时间段查询性能
- 考虑时间段索引优化
