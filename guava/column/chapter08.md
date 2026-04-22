# 第 8 章：Lists/Sets/Maps 常用集合工具 API 速用

## 1 项目背景

在数据分析平台的报表生成模块中，工程师小孙发现自己每天都在写类似的集合操作代码：把 List 按每 1000 条分批处理、找出两个数据集的差异、将 List 转换为 Map 以便快速查找。这些代码看似简单，但稍微不注意就会踩坑——比如分批时最后一个批次的大小不一致，或者转换 Map 时遇到重复键。

更头疼的是，团队里每个人都按照自己的习惯实现这些工具方法，导致同样的功能在代码库里出现了五六种不同的版本，有的处理了边界情况，有的没有，测试覆盖率也参差不齐。

**业务场景**：集合分批、交并差运算、List-Map 转换、条件筛选等常见的集合处理任务。

**痛点放大**：
- **样板代码重复**：每人重复造轮子，质量不一。
- **边界条件易遗漏**：空集合、null 元素、重复键等处理不完善。
- **代码可读性差**：循环嵌套循环，业务逻辑被淹没。
- **性能隐患**：某些实现使用 LinkedList 做频繁随机访问，或者重复计算哈希值。
- **缺乏工具方法**：JDK 的 `Collections` 工具类功能有限。

如果没有一套完善的集合工具类，开发效率将大打折扣。

**技术映射**：Guava 的 `Lists`、`Sets`、`Maps` 工具类提供了一系列静态方法，用于快速创建集合、分批处理、交并差运算、条件过滤等常见操作。

---

## 2 项目设计

**场景**：代码评审会，讨论报表模块的工具类设计。

---

**小胖**：（看着满屏的 for 循环）"我说，这集合操作也太啰嗦了吧！我就想把一个 List 按每 1000 条切分，写了十几行代码。这不就跟食堂分餐一样，明明有分餐器，偏要一勺一勺数？"

**小白**：（叹气）"而且每个人写的版本都不一样。你看这个分批方法——它用 `subList` 做的，看起来高效，但如果原 List 是 ArrayList 倒还好，要是 LinkedList，每次 `subList` 都遍历，性能就崩了。"

**大师**：（打开 Guava 文档）"Guava 的 `Lists` 工具类把这些都封装好了：

```java
// 按指定大小分批
List<List<String>> batches = Lists.partition(bigList, 1000);

// 快速创建带初始容量的 List
List<String> list = Lists.newArrayListWithCapacity(100);
List<String> list = Lists.newArrayListWithExpectedSize(100);  // 考虑扩容因子

// 快速创建并初始化
List<String> list = Lists.newArrayList("a", "b", "c");
```

**技术映射**：`Lists.partition()` 内部做了优化——它返回的是原 List 的视图，不会复制数据，而且正确处理了各种 List 实现。"

**小胖**："那 `Sets` 和 `Maps` 有什么好用的？"

**小白**："`Sets` 提供集合运算，这在数据分析中很常见：

```java
Set<String> intersection = Sets.intersection(set1, set2);  // 交集
Set<String> union = Sets.union(set1, set2);                  // 并集
Set<String> difference = Sets.difference(set1, set2);        // 差集
Set<String> symDiff = Sets.symmetricDifference(set1, set2); // 对称差集
```

这些都是**懒计算**的视图，不会立即创建新集合，访问时才计算。"

**大师**："`Maps` 的工具更丰富。看这个转换场景——从对象 List 到 id 映射的 Map：

```java
// 传统写法：手写循环
Map<Long, User> userMap = new HashMap<>();
for (User user : users) {
    userMap.put(user.getId(), user);
}

// Guava 写法：一行搞定
Map<Long, User> userMap = Maps.uniqueIndex(users, User::getId);
```

还有处理重复键的情况：

```java
// 允许重复，按某种规则处理冲突
Map<String, User> userMap = users.stream()
    .collect(ImmutableMap.toImmutableMap(
        User::getName,
        u -> u,
        (u1, u2) -> u1  // 遇到重复取第一个
    ));
```

**技术映射**：`Maps.uniqueIndex` 把'列表转映射'这个常见模式封装成一行代码，还能自动检测重复键并抛异常，比手写循环更安全。"

**小胖**："那如果我要同时处理 Map 的多个键值对呢？"

**小白**："用 `Maps.transformValues()` 或 `Maps.transformEntries()`：

```java
// 将 Map 的所有值转换
Map<String, Integer> lengths = Maps.transformValues(
    stringMap, 
    String::length
);

// 这个转换是视图的视图，不会复制数据
```

**大师**："还有一个很实用的功能——`Maps.newEnumMap()` 和 `Sets.newEnumSet()`，专门给枚举类型优化过的实现：

```java
// EnumMap 内部用数组实现，比普通 HashMap 快很多
Map<Status, Integer> countMap = Maps.newEnumMap(Status.class);
```

**技术映射**：Guava 集合工具的设计理念是'识别常见模式，提供类型安全的高效实现'，让你写出更短、更安全、更快的代码。"

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

### 分步实现：报表数据批处理工具

**步骤目标**：用 `Lists`、`Sets`、`Maps` 构建一个报表数据处理和转换工具集。

**代码实现**：

```java
package com.example.guava.demo;

import com.google.common.base.Preconditions;
import com.google.common.collect.*;

import java.util.*;
import java.util.function.Function;
import java.util.stream.Collectors;

/**
 * 报表数据处理工具
 */
public class ReportDataProcessor {

    /**
     * 将大数据集分批处理
     */
    public <T> List<List<T>> batchProcess(List<T> data, int batchSize) {
        Preconditions.checkArgument(batchSize > 0, "Batch size must be positive");
        if (data == null || data.isEmpty()) {
            return Collections.emptyList();
        }
        return Lists.partition(data, batchSize);
    }

    /**
     * 从对象列表创建 ID 映射 Map（自动检测重复）
     */
    public <K, V> ImmutableMap<K, V> indexById(List<V> list, Function<V, K> keyExtractor) {
        if (list == null || list.isEmpty()) {
            return ImmutableMap.of();
        }
        return Maps.uniqueIndex(list, keyExtractor::apply);
    }

    /**
     * 将 List 转换为 Map，处理重复键
     */
    public <K, V> Map<K, V> toMapWithMerge(List<V> list, 
                                           Function<V, K> keyExtractor,
                                           java.util.function.BinaryOperator<V> mergeFunction) {
        if (list == null || list.isEmpty()) {
            return Collections.emptyMap();
        }
        return list.stream()
            .collect(Collectors.toMap(
                keyExtractor,
                v -> v,
                mergeFunction
            ));
    }

    /**
     * 计算两个数据集的差异
     */
    public <T> SetDifference<T> compareSets(Set<T> set1, Set<T> set2) {
        return Sets.difference(set1, set2);
    }

    /**
     * 获取只在 set1 中存在、不在 set2 中的元素
     */
    public <T> Set<T> findOnlyInFirst(Set<T> set1, Set<T> set2) {
        return Sets.difference(set1, set2);
    }

    /**
     * 获取两个集合的交集
     */
    public <T> Set<T> findIntersection(Set<T> set1, Set<T> set2) {
        return Sets.intersection(set1, set2);
    }

    /**
     * 创建带初始容量的集合（性能优化）
     */
    public <T> List<T> createWithCapacity(int expectedSize) {
        return Lists.newArrayListWithExpectedSize(expectedSize);
    }

    public <T> Set<T> createHashSetWithCapacity(int expectedSize) {
        return Sets.newHashSetWithExpectedSize(expectedSize);
    }

    /**
     * 笛卡尔积（两两组合）
     */
    public <T> Set<List<T>> cartesianProduct(Set<T> set1, Set<T> set2) {
        return Sets.cartesianProduct(set1, set2);
    }

    /**
     * 所有子集（幂集）
     */
    public <T> Set<Set<T>> powerSet(Set<T> set) {
        return Sets.powerSet(set);
    }

    /**
     * 创建 EnumMap
     */
    public <K extends Enum<K>, V> Map<K, V> createEnumMap(Class<K> enumClass) {
        return Maps.newEnumMap(enumClass);
    }

    /**
     * 过滤 Map 的 values
     */
    public <K, V> Map<K, V> filterValues(Map<K, V> map, com.google.common.base.Predicate<V> predicate) {
        return Maps.filterValues(map, predicate);
    }

    /**
     * 转换 Map 的 values
     */
    public <K, V1, V2> Map<K, V2> transformValues(Map<K, V1> map, Function<V1, V2> transformer) {
        return Maps.transformValues(map, transformer::apply);
    }

    // ========== 测试入口 ==========
    public static void main(String[] args) {
        ReportDataProcessor processor = new ReportDataProcessor();

        // 测试分批处理
        System.out.println("=== 分批处理测试 ===");
        List<Integer> bigList = new ArrayList<>();
        for (int i = 1; i <= 25; i++) {
            bigList.add(i);
        }
        List<List<Integer>> batches = processor.batchProcess(bigList, 10);
        System.out.println("25 个元素分 3 批: " + batches);
        System.out.println("批次数量: " + batches.size());
        System.out.println("最后一批大小: " + batches.get(batches.size() - 1).size());

        // 测试 ID 映射
        System.out.println("\n=== ID 映射测试 ===");
        List<Product> products = Arrays.asList(
            new Product(1L, "iPhone", 9999),
            new Product(2L, "iPad", 5999),
            new Product(3L, "MacBook", 12999)
        );
        Map<Long, Product> productMap = processor.indexById(products, Product::getId);
        System.out.println("ID->Product 映射: " + productMap);

        // 测试集合运算
        System.out.println("\n=== 集合运算测试 ===");
        Set<String> oldUsers = Sets.newHashSet("Alice", "Bob", "Charlie");
        Set<String> newUsers = Sets.newHashSet("Bob", "Charlie", "David");
        
        Set<String> onlyInOld = processor.findOnlyInFirst(oldUsers, newUsers);
        Set<String> common = processor.findIntersection(oldUsers, newUsers);
        
        System.out.println("只在老用户中: " + onlyInOld);
        System.out.println("共同用户: " + common);

        // 测试笛卡尔积
        System.out.println("\n=== 笛卡尔积测试 ===");
        Set<String> colors = Sets.newHashSet("Red", "Blue");
        Set<String> sizes = Sets.newHashSet("S", "M", "L");
        Set<List<String>> combinations = processor.cartesianProduct(colors, sizes);
        System.out.println("颜色 x 尺码组合数: " + combinations.size());
        combinations.forEach(c -> System.out.println("  " + c));
    }

    // ========== 示例领域模型 ==========
    public static class Product {
        private Long id;
        private String name;
        private double price;

        public Product(Long id, String name, double price) {
            this.id = id;
            this.name = name;
            this.price = price;
        }

        public Long getId() { return id; }
        public String getName() { return name; }
        public double getPrice() { return price; }

        @Override
        public String toString() {
            return name;
        }
    }
}
```

### 测试验证

```java
package com.example.guava.demo;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

import java.util.*;

public class ReportDataProcessorTest {

    private final ReportDataProcessor processor = new ReportDataProcessor();

    @Test
    public void testBatchProcess() {
        List<Integer> data = Arrays.asList(1, 2, 3, 4, 5, 6, 7);
        List<List<Integer>> batches = processor.batchProcess(data, 3);
        
        assertEquals(3, batches.size());
        assertEquals(Arrays.asList(1, 2, 3), batches.get(0));
        assertEquals(Arrays.asList(4, 5, 6), batches.get(1));
        assertEquals(Arrays.asList(7), batches.get(2));
    }

    @Test
    public void testBatchProcess_empty() {
        List<List<Integer>> batches = processor.batchProcess(Collections.emptyList(), 10);
        assertTrue(batches.isEmpty());
    }

    @Test
    public void testIndexById() {
        List<ReportDataProcessor.Product> products = Arrays.asList(
            new ReportDataProcessor.Product(1L, "A", 100),
            new ReportDataProcessor.Product(2L, "B", 200)
        );
        
        Map<Long, ReportDataProcessor.Product> map = 
            processor.indexById(products, ReportDataProcessor.Product::getId);
        
        assertEquals(2, map.size());
        assertEquals("A", map.get(1L).getName());
    }

    @Test
    public void testIndexById_duplicate() {
        List<ReportDataProcessor.Product> products = Arrays.asList(
            new ReportDataProcessor.Product(1L, "A", 100),
            new ReportDataProcessor.Product(1L, "B", 200)  // 重复 ID
        );
        
        assertThrows(IllegalArgumentException.class, () -> {
            processor.indexById(products, ReportDataProcessor.Product::getId);
        });
    }

    @Test
    public void testFindOnlyInFirst() {
        Set<String> set1 = Sets.newHashSet("a", "b", "c");
        Set<String> set2 = Sets.newHashSet("b", "c", "d");
        
        Set<String> result = processor.findOnlyInFirst(set1, set2);
        assertEquals(Sets.newHashSet("a"), result);
    }

    @Test
    public void testFindIntersection() {
        Set<String> set1 = Sets.newHashSet("a", "b", "c");
        Set<String> set2 = Sets.newHashSet("b", "c", "d");
        
        Set<String> result = processor.findIntersection(set1, set2);
        assertEquals(Sets.newHashSet("b", "c"), result);
    }

    @Test
    public void testCartesianProduct() {
        Set<String> set1 = Sets.newHashSet("A", "B");
        Set<String> set2 = Sets.newHashSet("1", "2");
        
        Set<List<String>> result = processor.cartesianProduct(set1, set2);
        assertEquals(4, result.size());
    }

    @Test
    public void testCreateEnumMap() {
        Map<Status, String> map = processor.createEnumMap(Status.class);
        assertNotNull(map);
        assertTrue(map.isEmpty());
    }

    @Test
    public void testTransformValues() {
        Map<String, String> input = new HashMap<>();
        input.put("a", "hello");
        input.put("b", "world");
        
        Map<String, Integer> result = processor.transformValues(input, String::length);
        
        assertEquals(Integer.valueOf(5), result.get("a"));
        assertEquals(Integer.valueOf(5), result.get("b"));
    }

    enum Status {
        PENDING, PROCESSING, COMPLETED
    }
}
```

### 可能遇到的坑及解决方法

| 坑点 | 现象 | 解决方法 |
|------|------|----------|
| `partition` 返回视图 | 修改子列表影响原列表 | 需要复制时手动创建新 List |
| `Sets.intersection` 懒计算 | 原集合修改后结果变化 | 立即需要时复制到 HashSet |
| `Maps.uniqueIndex` 遇重复 | 抛 IllegalArgumentException | 预处理确保唯一，或用 Stream API |
| `transformValues` 是视图 | 每次访问都重新计算 | 需要缓存时复制到 HashMap |

---

## 4 项目总结

### 优缺点对比

| 维度 | Guava 集合工具 | 手写循环 | Stream API |
|------|---------------|----------|------------|
| 代码量 | ★★★★★ 极简 | ★ 冗长 | ★★★★ 简洁 |
| 可读性 | ★★★★★ 意图清晰 | ★★★ 需理解逻辑 | ★★★★ 声明式 |
| 性能 | ★★★★ 优化实现 | ★★★★ 可控 | ★★★ 中间操作开销 |
| 灵活性 | ★★★★ 丰富工具 | ★★★★★ 完全可控 | ★★★★★ 丰富操作 |
| null 处理 | ★★★ 需前置检查 | ★★★ 需手动 | ★★★ 需手动 |

### 适用场景

1. **数据分批**：大数据集分批处理
2. **交并差运算**：数据对比分析
3. **List-Map 转换**：对象索引化
4. **初始化优化**：预分配容量的集合
5. **笛卡尔积/幂集**：组合计算

### 不适用场景

1. **复杂流水线操作**：Stream API 更适合
2. **需要副作用操作**：传统 for 循环
3. **Java 8+ 项目**：可优先评估 Stream

### 生产踩坑案例

**案例 1：partition 视图修改影响原列表**
```java
List<List<String>> batches = Lists.partition(bigList, 10);
batches.get(0).clear();  // 原 bigList 前 10 个元素也被清了！
```
解决：需要隔离时用 `Lists.newArrayList(batch)` 复制。

**案例 2：Sets.intersection 懒计算陷阱**
```java
Set<String> intersection = Sets.intersection(set1, set2);
set1.remove("a");  // intersection 也变了！
```
解决：立即固定结果：`Sets.newHashSet(intersection)`。

**案例 3：EnumMap 的类型安全**
```java
Map<Status, Integer> map = Maps.newEnumMap(Status.class);
map.put(null, 1);  // EnumMap 不允许 null key
```
解决：用 `HashMap` 或处理 null。

### 思考题答案（第 7 章思考题 1）

> **问题**：`ImmutableMap.Builder` 遇到重复 key 如何处理？

**答案**：默认行为是抛 `IllegalArgumentException`。可以自定义：

```java
// 使用 ImmutableMap.Builder 的 putAll 时会检查重复
// 如需自定义合并逻辑，使用 Stream API：
ImmutableMap<K, V> map = list.stream()
    .collect(ImmutableMap.toImmutableMap(
        keyExtractor,
        valueExtractor,
        (v1, v2) -> v1  // 合并函数
    ));
```

### 新思考题

1. `Sets.powerSet` 返回的幂集在什么场景下会内存溢出？如何避免？
2. 比较 `Maps.transformValues` 和 Stream 的 `map` 操作在性能和语义上的差异。

### 推广计划提示

**开发**：
- 制定工具类使用规范，优先使用 Guava 工具
- Code Review 检查重复造轮子

**测试**：
- 边界测试：空集合、单元素集合
- 性能测试：大数据集分批性能

**运维**：
- 监控分批处理任务的内存使用
- 优化集合操作的 GC 开销
