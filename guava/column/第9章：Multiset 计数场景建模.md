# 第 9 章：Multiset 计数场景建模

## 1 项目背景

在电商平台的数据分析部门，数据分析师小李负责统计商品评价中的词频。他最初的实现是用 `Map<String, Integer>` 来存储每个词的出现次数，代码很快变得臃肿——每次遇到新词要判断是否存在，存在则取出现值加1，不存在则放入1。更麻烦的是需要找出出现次数最多的词，还得手写排序逻辑。

在另一个场景，库存管理系统需要统计每种商品在购物车中的数量。同样的问题再次出现，团队里甚至出现了三四种不同的计数 Map 实现，有的用 `getOrDefault`，有的用 `containsKey`，测试结果还不一致。

**业务场景**：词频统计、库存计数、投票统计、重复元素计数等需要统计元素出现次数的场景。

**痛点放大**：
- **样板代码重复**：每次计数都要写判断 null 或 containsKey 的逻辑。
- **代码可读性差**：业务意图（计数）被实现细节（Map 操作）淹没。
- **不支持负数和零的语义**：Map 中计数为 0 是否移除键？不同实现不同。
- **遍历不便**：要按频次排序或过滤需要额外代码。
- **API 不一致**：团队的多种实现导致维护困难。

如果没有专门的计数集合抽象，这类任务将消耗大量开发时间。

**技术映射**：Guava 的 `Multiset` 提供了专门的计数抽象，把"元素→次数"的映射封装成直观的 API，如 `add(element)`、`count(element)`、`setCount(element, count)` 等。

---

## 2 项目设计

**场景**：数据分析团队周会，讨论词频统计工具选型。

---

**小胖**：（看着 Map 计数代码）"我说，这计数代码也太啰嗦了吧！我就想把一组词的出现次数记下来，写了七八行判断 null、get、put 的代码。这不就跟食堂打饭时数自己打了几个菜一样，明明看一眼就知道，偏要数一遍？"

**小白**：（苦笑）"而且每个人写的都不一样。你看这个版本用 `getOrDefault`，那个版本用 `if (map.containsKey())`，还有个版本直接 `map.merge()`。虽然都能跑，但维护起来头大。"

**大师**：（在白板上写代码对比）"Guava 的 `Multiset` 就是专门解决这个问题的。看这段对比：

```java
// 传统写法：Map 计数
Map<String, Integer> wordCount = new HashMap<>();
for (String word : words) {
    wordCount.put(word, wordCount.getOrDefault(word, 0) + 1);
}

// Guava 写法：Multiset
Multiset<String> wordCount = HashMultiset.create();
for (String word : words) {
    wordCount.add(word);  // 就是这么简单！
}
```

**技术映射**：`Multiset` 就像是自动计数的收银机——你每放一个商品（add），它就自动帮你计数，不需要你先看一眼原来的数量再加一。"

**小胖**："那 `Multiset` 还能做什么？"

**小白**："功能很丰富：

```java
// 查询出现次数
int count = wordCount.count("apple");  // 返回 0 而不是 null

// 直接设置次数
wordCount.setCount("banana", 5);  // 设置精确值

// 批量添加
wordCount.add("orange", 3);  // 加 3 次

// 移除（计数减 1）
wordCount.remove("apple");  // 计数减 1，到 0 时自动移除元素

// 总元素数（含重复）
int total = wordCount.size();  // 所有计数的和

// 不重复元素数
int unique = wordCount.elementSet().size();

// 按频次遍历（降序）
wordCount.entrySet().stream()
    .sorted(Comparator.comparing(Multiset.Entry::getCount).reversed())
    .forEach(e -> System.out.println(e.getElement() + ": " + e.getCount()));
```

**大师**："`Multiset` 还有一个重要特性——**计数为零时元素自动消失**。用 Map 时你要决定 `count == 0` 时是否 `remove(key)`，`Multiset` 帮你自动处理了。

**技术映射**：`Multiset` 把计数的'存在性语义'统一了——计数为零等价于元素不存在，这比 Map 中 0 和 null 的歧义更清晰。"

**小胖**："那如果我要找出现次数最多的前 N 个呢？"

**小白**："可以用 `Multisets` 工具类：

```java
// 获取最高频的前 3 个
ImmutableMultiset<String> top3 = Multisets.copyHighestCountFirst(wordCount)
    .elementSet()
    .stream()
    .limit(3)
    .collect(ImmutableMultiset.toImmutableMultiset(
        e -> e, 
        e -> wordCount.count(e)
    ));
```

或者用 `Ordering`：

```java
Ordering<String> byCount = Ordering.natural()
    .onResultOf(wordCount::count)
    .reverse();
List<String> topN = byCount.immutableSortedCopy(wordCount.elementSet());
```

**大师**："还要注意 `Multiset` 有多个实现：
- `HashMultiset`：基于 HashMap，O(1) 操作
- `TreeMultiset`：基于 TreeMap，元素有序
- `LinkedHashMultiset`：保持插入顺序
- `ConcurrentHashMultiset`：线程安全版本
- `ImmutableMultiset`：不可变版本

**技术映射**：选择合适的 `Multiset` 实现，就像选择合适的容器——要快速查找用 Hash，要排序用 Tree，要并发用 Concurrent。"

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

### 分步实现：电商评论词频与库存统计

**步骤目标**：用 `Multiset` 构建评论分析和库存统计系统。

**代码实现**：

```java
package com.example.guava.demo;

import com.google.common.base.CharMatcher;
import com.google.common.base.Splitter;
import com.google.common.base.Strings;
import com.google.common.collect.*;

import java.util.*;
import java.util.stream.Collectors;

/**
 * 电商数据分析 - 使用 Multiset 进行计数统计
 */
public class EcommerceAnalytics {

    // 停用词列表（不可变）
    private static final ImmutableSet<String> STOP_WORDS = 
        ImmutableSet.of("的", "了", "是", "我", "有", "和", "就", "不", "人", "都", "一", "一个", "上", "也", "很", "到", "说", "要", "去", "你", "会", "着", "没有", "看", "好", "自己", "这");

    /**
     * 统计评论词频
     */
    public Multiset<String> analyzeWordFrequency(List<String> reviews) {
        Multiset<String> wordCount = HashMultiset.create();
        
        for (String review : reviews) {
            if (Strings.isNullOrEmpty(review)) continue;
            
            // 分词（简单按空白和标点分割）
            List<String> words = Splitter.on(CharMatcher.javaLetterOrDigit().negate())
                .omitEmptyStrings()
                .trimResults()
                .splitToList(review.toLowerCase());
            
            for (String word : words) {
                // 过滤停用词和太短/太长的词
                if (isValidWord(word)) {
                    wordCount.add(word);
                }
            }
        }
        
        return wordCount;
    }

    private boolean isValidWord(String word) {
        return word.length() >= 2 
            && word.length() <= 20 
            && !STOP_WORDS.contains(word)
            && CharMatcher.javaLetterOrDigit().matchesAllOf(word);
    }

    /**
     * 获取高频词 Top N
     */
    public List<WordFrequency> getTopWords(Multiset<String> wordCount, int n) {
        return wordCount.entrySet().stream()
            .map(e -> new WordFrequency(e.getElement(), e.getCount()))
            .sorted(Comparator.comparing(WordFrequency::getCount).reversed())
            .limit(n)
            .collect(Collectors.toList());
    }

    /**
     * 购物车商品数量统计
     */
    public Multiset<String> countCartItems(List<CartItem> items) {
        Multiset<String> itemCount = HashMultiset.create();
        
        for (CartItem item : items) {
            // 添加指定数量的商品
            itemCount.add(item.getSkuId(), item.getQuantity());
        }
        
        return itemCount;
    }

    /**
     * 合并多个用户的购物车统计
     */
    public Multiset<String> mergeCartStats(List<Multiset<String>> allCarts) {
        Multiset<String> merged = HashMultiset.create();
        for (Multiset<String> cart : allCarts) {
            merged.addAll(cart);
        }
        return merged;
    }

    /**
     * 计算商品热度得分（根据出现次数加权）
     */
    public Map<String, Double> calculatePopularityScore(Multiset<String> wordCount) {
        Map<String, Double> scores = new HashMap<>();
        
        // 找出最大频次用于归一化
        int maxCount = wordCount.entrySet().stream()
            .mapToInt(Multiset.Entry::getCount)
            .max()
            .orElse(1);
        
        for (String word : wordCount.elementSet()) {
            // 简单归一化得分
            double score = (double) wordCount.count(word) / maxCount;
            scores.put(word, score);
        }
        
        return scores;
    }

    /**
     * 使用 TreeMultiset 进行有序统计（按元素自然序）
     */
    public SortedMultiset<String> getSortedWordStats(List<String> words) {
        TreeMultiset<String> sorted = TreeMultiset.create();
        sorted.addAll(words);
        return sorted;
    }

    /**
     * 统计差异（两个 Multiset 的差异）
     */
    public MultisetDifference<String> compareWordSets(Multiset<String> set1, Multiset<String> set2) {
        // 返回 set1 相对于 set2 的差异
        return Multisets.difference(set1, set2);
    }

    // ========== 领域模型 ==========
    public static class WordFrequency {
        private final String word;
        private final int count;

        public WordFrequency(String word, int count) {
            this.word = word;
            this.count = count;
        }

        public String getWord() { return word; }
        public int getCount() { return count; }

        @Override
        public String toString() {
            return word + "(" + count + ")";
        }
    }

    public static class CartItem {
        private final String skuId;
        private final int quantity;

        public CartItem(String skuId, int quantity) {
            this.skuId = skuId;
            this.quantity = quantity;
        }

        public String getSkuId() { return skuId; }
        public int getQuantity() { return quantity; }
    }

    // ========== 测试入口 ==========
    public static void main(String[] args) {
        EcommerceAnalytics analytics = new EcommerceAnalytics();

        // 测试词频分析
        System.out.println("=== 词频分析测试 ===");
        List<String> reviews = Arrays.asList(
            "产品质量很好，质量很好，值得购买",
            "质量不错，物流很快",
            "质量很好，推荐购买",
            "一般般，质量不太好"
        );
        
        Multiset<String> wordCount = analytics.analyzeWordFrequency(reviews);
        System.out.println("所有词频统计: " + wordCount);
        System.out.println("唯一词数: " + wordCount.elementSet().size());
        System.out.println("总词数（含重复）: " + wordCount.size());
        
        List<WordFrequency> topWords = analytics.getTopWords(wordCount, 5);
        System.out.println("\nTop 5 高频词:");
        topWords.forEach(System.out::println);

        // 测试购物车统计
        System.out.println("\n=== 购物车统计测试 ===");
        List<CartItem> cart = Arrays.asList(
            new CartItem("SKU001", 2),
            new CartItem("SKU002", 1),
            new CartItem("SKU001", 3)  // 同一 SKU 多次添加
        );
        
        Multiset<String> cartStats = analytics.countCartItems(cart);
        System.out.println("购物车统计: " + cartStats);
        System.out.println("SKU001 数量: " + cartStats.count("SKU001"));
        
        // 测试热度得分
        System.out.println("\n=== 热度得分 ===");
        Map<String, Double> scores = analytics.calculatePopularityScore(wordCount);
        scores.entrySet().stream()
            .sorted(Map.Entry.<String, Double>comparingByValue().reversed())
            .limit(5)
            .forEach(e -> System.out.println(e.getKey() + ": " + String.format("%.2f", e.getValue())));
    }
}
```

### 测试验证

```java
package com.example.guava.demo;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

import java.util.Arrays;
import java.util.List;
import java.util.Set;

public class EcommerceAnalyticsTest {

    private final EcommerceAnalytics analytics = new EcommerceAnalytics();

    @Test
    public void testAnalyzeWordFrequency() {
        List<String> reviews = Arrays.asList(
            "apple banana apple",
            "banana cherry"
        );
        
        com.google.common.collect.Multiset<String> result = 
            analytics.analyzeWordFrequency(reviews);
        
        assertEquals(2, result.count("apple"));
        assertEquals(2, result.count("banana"));
        assertEquals(1, result.count("cherry"));
    }

    @Test
    public void testCountCartItems() {
        List<EcommerceAnalytics.CartItem> items = Arrays.asList(
            new EcommerceAnalytics.CartItem("A", 2),
            new EcommerceAnalytics.CartItem("B", 1),
            new EcommerceAnalytics.CartItem("A", 3)
        );
        
        com.google.common.collect.Multiset<String> result = 
            analytics.countCartItems(items);
        
        assertEquals(5, result.count("A"));  // 2 + 3
        assertEquals(1, result.count("B"));
    }

    @Test
    public void testGetTopWords() {
        com.google.common.collect.Multiset<String> wordCount = 
            com.google.common.collect.HashMultiset.create();
        wordCount.add("apple", 5);
        wordCount.add("banana", 3);
        wordCount.add("cherry", 1);
        
        List<EcommerceAnalytics.WordFrequency> top = analytics.getTopWords(wordCount, 2);
        
        assertEquals(2, top.size());
        assertEquals("apple", top.get(0).getWord());
        assertEquals(5, top.get(0).getCount());
    }

    @Test
    public void testMultisetCountZero() {
        com.google.common.collect.Multiset<String> multiset = 
            com.google.common.collect.HashMultiset.create();
        multiset.add("test", 3);
        multiset.remove("test", 3);
        
        // 计数归零后元素自动移除
        assertEquals(0, multiset.count("test"));
        assertFalse(multiset.elementSet().contains("test"));
    }

    @Test
    public void testMergeCartStats() {
        com.google.common.collect.Multiset<String> cart1 = 
            com.google.common.collect.HashMultiset.create();
        cart1.add("A", 2);
        
        com.google.common.collect.Multiset<String> cart2 = 
            com.google.common.collect.HashMultiset.create();
        cart2.add("A", 3);
        cart2.add("B", 1);
        
        com.google.common.collect.Multiset<String> merged = 
            analytics.mergeCartStats(Arrays.asList(cart1, cart2));
        
        assertEquals(5, merged.count("A"));
        assertEquals(1, merged.count("B"));
    }
}
```

### 可能遇到的坑及解决方法

| 坑点 | 现象 | 解决方法 |
|------|------|----------|
| `elementSet()` 视图修改 | 修改 elementSet 影响计数 | 视为只读，修改用 `add`/`remove` |
| `entrySet()` 包含零计数 | 遍历到计数为 0 的项 | 过滤或使用迭代器移除 |
| 并发修改异常 | 多线程操作非线程安全实现 | 用 `ConcurrentHashMultiset` 或加锁 |
| 比较器与 TreeMultiset | 元素类型需实现 Comparable | 提供自定义 Comparator |

---

## 4 项目总结

### 优缺点对比

| 维度 | Multiset | Map<String, Integer> | Stream  groupingBy |
|------|----------|---------------------|-------------------|
| API 直观性 | ★★★★★ 计数语义清晰 | ★★★ 通用 Map 语义 | ★★★★ 声明式 |
| null 处理 | ★★★★★ 返回 0 | ★★ null 歧义 | ★★★ 需处理 |
| 自动移除 | ★★★★★ 计数归零自动移除 | ★★ 需手动 remove | ★★★★ 可配置 |
| 遍历支持 | ★★★★ 专门 entrySet | ★★★ 通用 entrySet | ★★★★ Stream 操作 |
| 实现多样性 | ★★★★ 多种实现 | ★★★★★ 任意 Map | ★★★ 结果需转换 |

### 适用场景

1. **词频统计**：文本分析、日志分析
2. **库存统计**：购物车、仓库盘点
3. **投票统计**：选项得票数
4. **重复计数**：元素去重前的计数
5. **频次排序**：按出现次数排序

### 不适用场景

1. **非计数场景**：普通键值对存储
2. **负计数需求**：Multiset 不支持负数
3. **需要其他关联数据**：计数外还要存储更多信息
4. **Java 8+ 简单统计**：Stream `groupingBy` 足够

### 生产踩坑案例

**案例 1：误用 `elementSet().add()`**
```java
// 坑：直接操作 elementSet 不增加计数
multiset.elementSet().add("new");  // 计数仍是 0！
```
解决：用 `multiset.add("new")`。

**案例 2：遍历时修改**
```java
// 坑：ConcurrentModificationException
for (String elem : multiset.elementSet()) {
    if (shouldRemove(elem)) multiset.remove(elem);
}
```
解决：用迭代器的 `remove()` 或先收集再批量移除。

**案例 3：TreeMultiset 类型问题**
```java
TreeMultiset<Object> set = TreeMultiset.create();  // Object 无法比较！
```
解决：提供 Comparator 或使用 Comparable 类型。

### 思考题答案（第 8 章思考题 1）

> **问题**：`Sets.powerSet` 在什么场景下会内存溢出？

**答案**：幂集大小是 2^n，n=20 时就有 100 万个子集。当原始集合超过 20 个元素时，幂集会迅速耗尽内存。避免方法：
1. 限制原始集合大小
2. 用迭代器按需生成而非全部存储
3. 考虑用 BitSet 表示子集

### 新思考题

1. `Multiset` 和 Java 8 的 `Map.merge()` 在计数场景下如何选择？
2. 设计一个支持负数的扩展 `Multiset` 实现，有哪些关键设计点？

### 推广计划提示

**开发**：
- 计数场景优先使用 Multiset
- Code Review 检查 Map 计数用法

**测试**：
- 边界测试：零计数、大计数
- 性能测试：大数据集统计性能

**运维**：
- 监控词频统计任务的内存使用
- 优化热点词的缓存策略
