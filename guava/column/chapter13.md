# 第 13 章：Primitives 工具与基础性能优化

## 1 项目背景

在大数据平台的指标计算模块中，工程师小刘负责优化一个统计报表的生成速度。分析发现，瓶颈出在大量的 `Long` 对象创建和垃圾回收上——系统需要处理数十亿条记录，每条记录的操作都会触发自动装箱拆箱。更麻烦的是，某些计算涉及无符号整数，Java 的原生类型无法直接表示。

在另一个场景，网络编程模块需要高效地处理字节数组和基本类型的转换。手写这些转换代码既繁琐又容易出错，而且需要考虑大小端序的问题。

**业务场景**：大数据计算、网络协议处理、数值统计、缓存优化等需要高效处理基本类型的场景。

**痛点放大**：
- **自动装箱开销**：`Long` vs `long`，对象创建和 GC 压力大。
- **集合存储效率**：`List<Long>` 每个元素都是对象引用。
- **无符号数处理**：Java 没有无符号类型，处理起来麻烦。
- **字节转换重复**：int/long 和 byte[] 之间的转换到处复制粘贴。
- **比较计算样板代码**：min/max/compare 到处写。

如果没有专门的基本类型工具，性能和代码质量都将受到影响。

**技术映射**：Guava 的 `Primitives` 工具类（Ints、Longs、Shorts、Bytes、Floats、Doubles、Booleans、Chars）提供了一系列静态方法，用于高效处理基本类型，包括无符号运算、字节转换、集合操作等。

---

## 2 项目设计

**场景**：性能优化专项会议，讨论大数据模块优化方案。

---

**小胖**：（看着 GC 日志）"我说，这 GC 也太频繁了吧！分析发现是 `Long` 对象创建太多。我就想做几个数字比较，Java 偏要给我包成对象。这不就跟食堂买饭，明明给现金就行，偏要先换成饭票再用饭票买？"

**小白**：（点头）"而且 `List<Long>` 存储基本类型时，每个元素都是一个对象引用，内存占用是原始数据的 3-4 倍。"

**大师**：（在白板上画内存布局）"Guava 的 Primitives 工具类虽然不能消除装箱，但提供了更高效的工具方法。看无符号数处理：

```java
// 传统：无符号比较需要手动处理符号位
int compareUnsigned(int a, int b) {
    return Integer.compareUnsigned(a, b);  // Java 8+ 有原生支持
}

// Guava：Ints 工具类
int result = Ints.compare(a, b);
int max = Ints.max(a, b, c, d);
int min = Ints.min(a, b, c, d);

// 无符号转换
long unsignedValue = Ints.toLongSigned(-1);  // 将 -1 转为无符号 long
```

**技术映射**：`Ints`、`Longs` 等工具类就像是基本类型的'瑞士军刀'，虽然不能避免装箱，但提供了标准、高效的常用操作。"

**小胖**："那字节转换呢？我们经常需要把 int 转成 byte[] 发网络。"

**小白**："Guava 提供了标准方法：

```java
// int 转 byte[]（大端序）
byte[] bytes = Ints.toByteArray(1024);  // [0, 0, 4, 0]

// byte[] 转 int
int value = Ints.fromByteArray(bytes);

// 从指定位置转换
int value = Ints.fromBytes(bytes[0], bytes[1], bytes[2], bytes[3]);

// Long 版本
byte[] longBytes = Longs.toByteArray(123456789L);
long value = Longs.fromByteArray(longBytes);
```

这些都是大端序（网络字节序），符合网络协议标准。"

**大师**："还有 `UnsignedInts` 和 `UnsignedLongs`，专门处理无符号运算：

```java
// 无符号除法
int result = UnsignedInts.divide(0xFFFFFFFF, 2);  // 2147483647

// 无符号比较
int cmp = UnsignedInts.compare(0xFFFFFFFF, 1);  // 正数，因为 0xFFFFFFFF 是 4294967295

// 解析无符号字符串
int value = UnsignedInts.parseUnsignedInt("4294967295");
String str = UnsignedInts.toString(0xFFFFFFFF);  // "4294967295"
```

**技术映射**：无符号工具类把 Java 的有符号限制封装起来，让你在需要时可以像使用无符号类型一样操作。"

**小胖**："那还有别的实用功能吗？"

**小白**："还有一些集合工具：

```java
// 基本类型数组转 List（返回的是视图，不是拷贝）
List<Integer> list = Ints.asList(intArray);

// 连接多个数组
int[] combined = Ints.concat(array1, array2, array3);

// 查找元素
int index = Ints.indexOf(array, target);

// 检查是否包含
boolean contains = Ints.contains(array, target);

// 数组前部匹配
boolean startsWith = Ints.startsWith(array, prefix);
```

**大师**："但要注意，Guava 的 Primitives 工具**不能替代**真正的原始集合（如 fastutil、trove）。如果你需要存储海量基本类型，应该使用专门的原始集合库。

**技术映射**：Guava Primitives 的定位是'轻量级工具'，不是'高性能集合'。它在'偶尔需要处理基本类型'的场景下很有用，但在'海量数据存储'场景下应该选择专门的原始集合库。"

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

### 分步实现：网络协议解析与数值统计

**步骤目标**：用 Primitives 工具类构建协议解析和统计计算模块。

**代码实现**：

```java
package com.example.guava.demo;

import com.google.common.primitives.*;

import java.util.*;

/**
 * 网络协议与数值统计 - 使用 Guava Primitives
 */
public class NetworkAndStatsProcessor {

    /**
     * 构建协议数据包头部
     */
    public byte[] buildPacketHeader(int version, int commandId, long sequenceId, int payloadLength) {
        // 头部结构：version(4) + commandId(4) + sequenceId(8) + payloadLength(4) = 20 bytes
        byte[] header = new byte[20];
        
        System.arraycopy(Ints.toByteArray(version), 0, header, 0, 4);
        System.arraycopy(Ints.toByteArray(commandId), 0, header, 4, 4);
        System.arraycopy(Longs.toByteArray(sequenceId), 0, header, 8, 8);
        System.arraycopy(Ints.toByteArray(payloadLength), 0, header, 16, 4);
        
        return header;
    }

    /**
     * 解析协议头部
     */
    public PacketHeader parsePacketHeader(byte[] header) {
        if (header.length < 20) {
            throw new IllegalArgumentException("Header too short");
        }
        
        int version = Ints.fromByteArray(Arrays.copyOfRange(header, 0, 4));
        int commandId = Ints.fromByteArray(Arrays.copyOfRange(header, 4, 8));
        long sequenceId = Longs.fromByteArray(Arrays.copyOfRange(header, 8, 16));
        int payloadLength = Ints.fromByteArray(Arrays.copyOfRange(header, 16, 20));
        
        return new PacketHeader(version, commandId, sequenceId, payloadLength);
    }

    /**
     * 处理无符号 IP 地址段
     */
    public List<String> expandIpRange(int startIp, int endIp) {
        List<String> ips = new ArrayList<>();
        
        // 使用无符号比较遍历
        for (int ip = startIp; UnsignedInts.compare(ip, endIp) <= 0; ) {
            ips.add(intToIp(ip));
            ip = UnsignedInts.add(ip, 1);  // 无符号加 1
        }
        
        return ips;
    }

    /**
     * 将 int 转为 IP 字符串（无符号处理）
     */
    public String intToIp(int ip) {
        long unsignedIp = UnsignedInts.toLong(ip);
        return String.format("%d.%d.%d.%d",
            (unsignedIp >> 24) & 0xFF,
            (unsignedIp >> 16) & 0xFF,
            (unsignedIp >> 8) & 0xFF,
            unsignedIp & 0xFF
        );
    }

    /**
     * 计算统计数据（避免装箱）
     */
    public StatsResult calculateStats(int[] data) {
        if (data == null || data.length == 0) {
            return new StatsResult(0, 0, 0, 0);
        }
        
        int min = Ints.min(data);
        int max = Ints.max(data);
        
        // 手动计算平均值（避免创建 Integer 对象）
        long sum = 0;
        for (int value : data) {
            sum += value;
        }
        double avg = (double) sum / data.length;
        
        return new StatsResult(min, max, avg, sum);
    }

    /**
     * 查找众数
     */
    public int[] findMode(int[] data) {
        if (data == null || data.length == 0) {
            return new int[0];
        }
        
        // 使用基本类型数组排序
        int[] sorted = data.clone();
        Arrays.sort(sorted);
        
        // 统计频次
        Map<Integer, Integer> frequency = new HashMap<>();
        for (int value : sorted) {
            frequency.merge(value, 1, Integer::sum);
        }
        
        // 找出最高频次
        int maxFreq = Ints.max(Ints.toArray(frequency.values()));
        
        // 收集众数
        return frequency.entrySet().stream()
            .filter(e -> e.getValue() == maxFreq)
            .mapToInt(Map.Entry::getKey)
            .toArray();
    }

    /**
     * 字节数组工具
     */
    public byte[] combineByteArrays(byte[]... arrays) {
        return Bytes.concat(arrays);
    }

    public boolean isValidUtf8(byte[] data) {
        // 简单检查：Guava 没有 UTF-8 校验，这里用 Bytes 工具辅助
        return data != null && data.length > 0;
    }

    /**
     * 处理 short 数组（音频采样等场景）
     */
    public short[] normalizeAudio(short[] samples) {
        if (samples == null || samples.length == 0) {
            return new short[0];
        }
        
        short max = Shorts.max(samples);
        short min = Shorts.min(samples);
        
        // 归一化到 [-32768, 32767] 范围
        short[] normalized = new short[samples.length];
        for (int i = 0; i < samples.length; i++) {
            // 简单归一化示例
            normalized[i] = samples[i];  // 实际应用需要更复杂的算法
        }
        
        return normalized;
    }

    /**
     * 处理 float 数组（机器学习场景）
     */
    public float[] softmax(float[] logits) {
        if (logits == null || logits.length == 0) {
            return new float[0];
        }
        
        float maxLogit = Floats.max(logits);
        
        float[] exp = new float[logits.length];
        float sum = 0;
        for (int i = 0; i < logits.length; i++) {
            exp[i] = (float) Math.exp(logits[i] - maxLogit);
            sum += exp[i];
        }
        
        for (int i = 0; i < exp.length; i++) {
            exp[i] /= sum;
        }
        
        return exp;
    }

    // ========== 领域模型 ==========
    public static class PacketHeader {
        public final int version;
        public final int commandId;
        public final long sequenceId;
        public final int payloadLength;

        public PacketHeader(int version, int commandId, long sequenceId, int payloadLength) {
            this.version = version;
            this.commandId = commandId;
            this.sequenceId = sequenceId;
            this.payloadLength = payloadLength;
        }

        @Override
        public String toString() {
            return String.format("PacketHeader{v=%d, cmd=%d, seq=%d, len=%d}",
                version, commandId, sequenceId, payloadLength);
        }
    }

    public static class StatsResult {
        public final int min;
        public final int max;
        public final double average;
        public final long sum;

        public StatsResult(int min, int max, double average, long sum) {
            this.min = min;
            this.max = max;
            this.average = average;
            this.sum = sum;
        }

        @Override
        public String toString() {
            return String.format("Stats{min=%d, max=%d, avg=%.2f, sum=%d}",
                min, max, average, sum);
        }
    }

    // ========== 测试入口 ==========
    public static void main(String[] args) {
        NetworkAndStatsProcessor processor = new NetworkAndStatsProcessor();

        // 测试协议头部
        System.out.println("=== 协议头部测试 ===");
        byte[] header = processor.buildPacketHeader(1, 100, 123456789L, 1024);
        System.out.println("Header bytes: " + Arrays.toString(header));
        
        PacketHeader parsed = processor.parsePacketHeader(header);
        System.out.println("Parsed: " + parsed);

        // 测试无符号 IP 处理
        System.out.println("\n=== 无符号 IP 测试 ===");
        // 192.168.1.1 = 3232235777 unsigned
        int ip = (192 << 24) | (168 << 16) | (1 << 8) | 1;
        System.out.println("IP int: " + UnsignedInts.toString(ip));
        System.out.println("IP string: " + processor.intToIp(ip));

        // 测试统计
        System.out.println("\n=== 统计计算测试 ===");
        int[] data = {23, 45, 67, 89, 12, 34, 56, 78, 90, 11};
        StatsResult stats = processor.calculateStats(data);
        System.out.println("Stats: " + stats);

        int[] mode = processor.findMode(new int[]{1, 2, 2, 3, 3, 3, 4});
        System.out.println("Mode: " + Arrays.toString(mode));

        // 测试 softmax
        System.out.println("\n=== Softmax 测试 ===");
        float[] logits = {1.0f, 2.0f, 3.0f};
        float[] probs = processor.softmax(logits);
        System.out.println("Softmax: " + Arrays.toString(probs));
        System.out.println("Sum: " + Floats.sum(probs));
    }
}
```

### 测试验证

```java
package com.example.guava.demo;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

import java.util.Arrays;

public class NetworkAndStatsProcessorTest {

    private final NetworkAndStatsProcessor processor = new NetworkAndStatsProcessor();

    @Test
    public void testBuildAndParseHeader() {
        byte[] header = processor.buildPacketHeader(1, 100, 123456789L, 1024);
        assertEquals(20, header.length);
        
        NetworkAndStatsProcessor.PacketHeader parsed = processor.parsePacketHeader(header);
        assertEquals(1, parsed.version);
        assertEquals(100, parsed.commandId);
        assertEquals(123456789L, parsed.sequenceId);
        assertEquals(1024, parsed.payloadLength);
    }

    @Test
    public void testUnsignedInts() {
        // 0xFFFFFFFF 作为无符号数是 4294967295
        int maxUnsigned = 0xFFFFFFFF;
        long asLong = com.google.common.primitives.UnsignedInts.toLong(maxUnsigned);
        assertEquals(4294967295L, asLong);
    }

    @Test
    public void testCalculateStats() {
        int[] data = {10, 20, 30, 40, 50};
        var stats = processor.calculateStats(data);
        
        assertEquals(10, stats.min);
        assertEquals(50, stats.max);
        assertEquals(30.0, stats.average, 0.01);
        assertEquals(150, stats.sum);
    }

    @Test
    public void testIntsMax() {
        int[] data = {3, 1, 4, 1, 5, 9, 2, 6};
        assertEquals(9, com.google.common.primitives.Ints.max(data));
    }

    @Test
    public void testIntsMin() {
        int[] data = {3, 1, 4, 1, 5, 9, 2, 6};
        assertEquals(1, com.google.common.primitives.Ints.min(data));
    }

    @Test
    public void testIntsToByteArray() {
        int value = 1024;  // 0x00000400
        byte[] bytes = com.google.common.primitives.Ints.toByteArray(value);
        assertArrayEquals(new byte[]{0, 0, 4, 0}, bytes);
    }

    @Test
    public void testIntsFromByteArray() {
        byte[] bytes = {0, 0, 4, 0};
        int value = com.google.common.primitives.Ints.fromByteArray(bytes);
        assertEquals(1024, value);
    }

    @Test
    public void testSoftmax() {
        float[] logits = {0.0f, 0.0f, 0.0f};
        float[] probs = processor.softmax(logits);
        
        // 三个相等值 softmax 后应该各约 0.333
        assertEquals(0.333f, probs[0], 0.01);
        assertEquals(0.333f, probs[1], 0.01);
        assertEquals(0.333f, probs[2], 0.01);
        
        // 概率之和应为 1
        float sum = com.google.common.primitives.Floats.sum(probs);
        assertEquals(1.0f, sum, 0.01);
    }

    @Test
    public void testCombineByteArrays() {
        byte[] a = {1, 2, 3};
        byte[] b = {4, 5};
        byte[] c = {6};
        
        byte[] combined = processor.combineByteArrays(a, b, c);
        assertArrayEquals(new byte[]{1, 2, 3, 4, 5, 6}, combined);
    }
}
```

### 可能遇到的坑及解决方法

| 坑点 | 现象 | 解决方法 |
|------|------|----------|
| 字节序混淆 | 网络/主机字节序不一致 | Guava 默认大端序，符合网络标准 |
| 无符号数溢出 | 计算结果异常 | 使用 `UnsignedXxx` 工具类 |
| `asList()` 视图修改 | 修改 List 影响原数组 | 需要隔离时用 `new ArrayList<>(...)` |
| 海量数据装箱 | GC 压力大 | 考虑 fastutil/Trove 等原始集合库 |

---

## 4 项目总结

### 优缺点对比

| 维度 | Guava Primitives | 原生 Java | fastutil/Trove |
|------|------------------|-----------|----------------|
| 工具方法 | ★★★★★ 丰富 | ★★★ Java 8+ 有基础 | ★★★★ 丰富 |
| 无符号支持 | ★★★★ 完整 | ★★ Java 8+ 部分支持 | ★★★★ 完整 |
| 字节转换 | ★★★★ 标准 | ★★ 需手写 | ★★★★ 支持 |
| 原始集合 | ★ 无 | ★ 无 | ★★★★★ 专业 |
| 装箱避免 | ★ 不能避免 | ★ 不能避免 | ★★★★★ 避免 |

### 适用场景

1. **协议解析**：字节和基本类型的转换
2. **数值统计**：min/max/sum 等计算
3. **无符号数处理**：IP 地址、CRC 校验等
4. **数组操作**：连接、查找、比较
5. **轻量级优化**：减少样板代码

### 不适用场景

1. **海量数据存储**：使用原始集合库
2. **高频计算**：考虑向量化或 GPU
3. **复杂数学运算**：使用 Apache Commons Math

### 生产踩坑案例

**案例 1：混淆有符号和无符号比较**
```java
int a = 0xFFFFFFFF;  // -1 有符号，4294967295 无符号
int b = 1;
Ints.compare(a, b);  // 返回负数（-1 < 1）
UnsignedInts.compare(a, b);  // 返回正数（4294967295 > 1）
```
解决：明确业务场景，选择正确的比较方式。

**案例 2：asList 视图修改问题**
```java
int[] array = {1, 2, 3};
List<Integer> list = Ints.asList(array);
list.set(0, 100);  // array[0] 也变成了 100！
```
解决：需要隔离时用 `new ArrayList<>(Ints.asList(array))`。

**案例 3：海量数据 GC 问题**
```java
List<Integer> list = new ArrayList<>();
for (int i = 0; i < 10000000; i++) {
    list.add(i);  // 自动装箱创建大量对象！
}
```
解决：使用 `IntArrayList`（fastutil）或 `TIntArrayList`（Trove）。

### 思考题答案（第 12 章思考题 1）

> **问题**：如何用 `RangeSet` 实现高效的时间段合并？

**答案**：`TreeRangeSet` 自动合并相连或重叠的区间：

```java
TreeRangeSet<Integer> set = TreeRangeSet.create();
set.add(Range.closed(1, 5));
set.add(Range.closed(3, 7));  // 与 [1,5] 重叠，自动合并为 [1,7]
set.add(Range.closed(10, 15));  // 不重叠，保持独立
// 结果: {[1, 7], [10, 15]}
```

### 新思考题

1. 在什么场景下应该优先选择 fastutil/Trove 而非 Guava Primitives？
2. 设计一个支持无符号 128 位整数（IPv6 地址）的工具类，参考 Guava 的设计思路。

### 推广计划提示

**开发**：
- 协议解析使用 Primitives 字节转换
- 数值计算使用 min/max 工具
- 海量数据考虑原始集合库

**测试**：
- 边界测试：无符号数极值
- 字节序验证：网络协议对接

**运维**：
- 监控大数据模块 GC 情况
- 评估原始集合库引入收益
