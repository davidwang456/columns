# 第 14 章：Ordering 与 Comparator 链式排序

## 1 项目背景

在电商平台的商品搜索系统中，工程师小冯负责实现一个复杂的排序功能。用户可以根据价格、销量、评分等多个维度排序，还需要支持升序、降序、空值处理等多种策略。最初的实现是用多个 if-else 分支来处理不同的排序条件，代码很快变得难以维护。

更麻烦的是排序的稳定性问题——当主排序字段相同时，需要按次要字段排序，次要字段相同再按第三字段排序。手写的比较逻辑很容易出错，而且每次新增排序维度都要修改大量代码。

**业务场景**：商品排序、排行榜、数据查询结果排序等需要多维度排序的场景。

**痛点放大**：
- **Comparator 编写繁琐**：手写 compare 方法容易出错。
- **多字段排序复杂**：主次排序字段的组合逻辑难以维护。
- **空值处理不统一**：null 应该排在前面还是后面？不同地方实现不一致。
- **排序方向切换麻烦**：升序降序切换需要重新写比较器。
- **性能优化困难**：频繁调用的排序逻辑难以优化。

如果没有强大的排序工具，复杂的排序需求将难以实现。

**技术映射**：Guava 的 `Ordering` 类提供了链式 Comparator 构建器，支持多字段排序、空值处理、反向排序、复合排序等高级功能，代码可读性远超手写 Comparator。

---

## 2 项目设计

**场景**：搜索系统需求评审会，讨论排序功能设计。

---

**小胖**：（看着一堆 if-else）"我说，这排序逻辑也太复杂了吧！我就想让商品先按价格升序，价格相同的按销量降序，销量相同的按评分降序，写了三十多行 Comparator。这不就跟食堂排队，先按窗口分、再按先到后到分、还要考虑 VIP 插队一样吗？"

**小白**：（叹气）"而且 null 处理还不一致。有的地方 null 放前面，有的地方 null 放后面，有的地方直接 NPE。"

**大师**：（在白板上写对比）"Guava 的 `Ordering` 让排序变成链式声明：

```java
// 传统写法：手写 Comparator
Comparator<Product> comparator = (p1, p2) -> {
    int priceCmp = Double.compare(p1.getPrice(), p2.getPrice());
    if (priceCmp != 0) return priceCmp;
    
    int salesCmp = Long.compare(p2.getSales(), p1.getSales());  // 降序
    if (salesCmp != 0) return salesCmp;
    
    return Double.compare(p2.getRating(), p1.getRating());  // 降序
};

// Guava 写法：链式构建
Ordering<Product> ordering = Ordering.natural()
    .onResultOf(Product::getPrice)           // 价格升序
    .compound(Ordering.natural()
        .onResultOf(Product::getSales)
        .reverse())                         // 销量降序
    .compound(Ordering.natural()
        .onResultOf(Product::getRating)
        .reverse());                        // 评分降序
```

**技术映射**：`Ordering` 就像是排序的'积木'，你可以把简单的比较器组合成复杂的排序规则，就像搭积木一样直观。"

**小胖**："那空值怎么处理？"

**小白**："`Ordering` 专门处理了 null：

```java
// null 放前面
Ordering<String> nullsFirst = Ordering.natural().nullsFirst();

// null 放后面
Ordering<String> nullsLast = Ordering.natural().nullsLast();

// 复合排序中的 null 处理
Ordering<Product> ordering = Ordering.natural()
    .nullsFirst()                          // null 价格排前面
    .onResultOf(Product::getPrice);
```

而且 Guava 预定义了一些常用 Ordering：

```java
Ordering.natural();           // 自然序（Comparable）
Ordering.usingToString();   // 按 toString 排序
Ordering.from(comparator);    // 包装已有 Comparator
```

**大师**："还有高级功能——`Ordering` 可以创建排序后的集合：

```java
// 获取最小/最大 k 个元素（比全排序后截取高效）
List<Product> top10 = ordering.leastOf(products, 10);
List<Product> bottom10 = ordering.greatestOf(products, 10);

// 生成排序后的不可变列表
ImmutableList<Product> sorted = ordering.immutableSortedCopy(products);

// 检查是否已排序
boolean isOrdered = ordering.isOrdered(products);

// 二分查找（要求集合已排序）
int index = ordering.binarySearch(sortedList, key);
```

**技术映射**：`Ordering` 不只是 Comparator，它提供了'排序生态系统'——从构建比较器到执行排序、验证排序、高效查找，一站式解决。"

**小胖**："那和 Java 8 的 Comparator 相比呢？"

**小白**："Java 8 的 `Comparator.comparing().thenComparing()` 也很强大，但 `Ordering` 有一些独特优势：
1. **nullsFirst/nullsLast** 更简洁
2. **leastOf/greatestOf** 等 Guava 特有的高效算法
3. **isOrdered/binarySearch** 等辅助方法
4. **已稳定**：Guava 的 Ordering 经过多年生产验证

新项目可以优先用 Java 8 Comparator，但 Guava Ordering 在特定场景仍有价值。"

**大师**："还有一个实用功能——`Ordering` 可以处理复合键：

```java
// 按多个字段排序，但有默认值
Ordering<Product> ordering = Ordering.natural()
    .onResultOf(p -> Objects.firstNonNull(p.getPriority(), Integer.MAX_VALUE))
    .compound(Ordering.natural()
        .onResultOf(Product::getSales)
        .reverse());
```

**技术映射**：`Ordering` 的设计理念是'排序规则也是可组合的对象'，让复杂的排序逻辑从'命令式编码'变成'声明式组装'。"

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

### 分步实现：商品排序与排行榜

**步骤目标**：用 `Ordering` 构建商品多维度排序和排行榜系统。

**代码实现**：

```java
package com.example.guava.demo;

import com.google.common.base.Strings;
import com.google.common.collect.*;

import java.util.*;
import java.util.stream.Collectors;

/**
 * 商品排序与排行榜 - 使用 Ordering
 */
public class ProductRankingSystem {

    // ========== 预定义排序规则 ==========
    
    // 按价格升序（null 价格排最后）
    public static final Ordering<Product> BY_PRICE_ASC = Ordering.natural()
        .nullsLast()
        .onResultOf(Product::getPrice);
    
    // 按价格降序
    public static final Ordering<Product> BY_PRICE_DESC = BY_PRICE_ASC.reverse();
    
    // 按销量降序
    public static final Ordering<Product> BY_SALES_DESC = Ordering.natural()
        .nullsLast()
        .onResultOf(Product::getSales)
        .reverse();
    
    // 按评分降序
    public static final Ordering<Product> BY_RATING_DESC = Ordering.natural()
        .nullsLast()
        .onResultOf(Product::getRating)
        .reverse();
    
    // 按名称字典序
    public static final Ordering<Product> BY_NAME = Ordering.natural()
        .nullsLast()
        .onResultOf(Product::getName);
    
    // 综合排序：销量降序 -> 评分降序 -> 价格升序
    public static final Ordering<Product> COMPOSITE_RANKING = BY_SALES_DESC
        .compound(BY_RATING_DESC)
        .compound(BY_PRICE_ASC);
    
    // 个性化排序：高优先级在前 -> 销量降序 -> 时间降序
    public static final Ordering<Product> PERSONALIZED = Ordering.natural()
        .nullsLast()
        .onResultOf(Product::getPriority)
        .compound(BY_SALES_DESC)
        .compound(Ordering.natural()
            .nullsLast()
            .onResultOf(Product::getCreateTime)
            .reverse());

    /**
     * 商品列表
     */
    public static class Product {
        private final Long id;
        private final String name;
        private final Double price;
        private final Long sales;
        private final Double rating;
        private final Integer priority;
        private final Date createTime;

        public Product(Long id, String name, Double price, Long sales, 
                      Double rating, Integer priority, Date createTime) {
            this.id = id;
            this.name = name;
            this.price = price;
            this.sales = sales;
            this.rating = rating;
            this.priority = priority;
            this.createTime = createTime;
        }

        // Getters
        public Long getId() { return id; }
        public String getName() { return name; }
        public Double getPrice() { return price; }
        public Long getSales() { return sales; }
        public Double getRating() { return rating; }
        public Integer getPriority() { return priority; }
        public Date getCreateTime() { return createTime; }

        @Override
        public String toString() {
            return String.format("%s(¥%.0f, %d件, %.1f分)", 
                name, price != null ? price : 0, sales != null ? sales : 0, 
                rating != null ? rating : 0);
        }
    }

    // ========== 排序方法 ==========

    /**
     * 按指定规则排序
     */
    public List<Product> sort(List<Product> products, Ordering<Product> ordering) {
        return ordering.sortedCopy(products);
    }

    /**
     * 获取 Top N（高效算法，不完全排序）
     */
    public List<Product> getTopProducts(List<Product> products, int n, Ordering<Product> ordering) {
        return ordering.leastOf(products, n);  // leastOf 返回最小，对降序 Ordering 就是 Top
    }

    /**
     * 获取 Bottom N
     */
    public List<Product> getBottomProducts(List<Product> products, int n, Ordering<Product> ordering) {
        return ordering.greatestOf(products, n);  // greatestOf 返回最大，对降序 Ordering 就是 Bottom
    }

    /**
     * 检查是否已排序
     */
    public boolean isSorted(List<Product> products, Ordering<Product> ordering) {
        return ordering.isOrdered(products);
    }

    /**
     * 二分查找（要求已排序）
     */
    public int binarySearch(List<Product> sortedProducts, Product target, Ordering<Product> ordering) {
        return ordering.binarySearch(sortedProducts, target);
    }

    /**
     * 获取最小和最大元素
     */
    public Product getMin(List<Product> products, Ordering<Product> ordering) {
        return ordering.min(products);
    }

    public Product getMax(List<Product> products, Ordering<Product> ordering) {
        return ordering.max(products);
    }

    /**
     * 多字段排序构建器
     */
    public Ordering<Product> buildOrdering(SortConfig config) {
        Ordering<Product> ordering = null;
        
        for (SortField field : config.getFields()) {
            Ordering<Product> fieldOrdering = getFieldOrdering(field);
            
            if (ordering == null) {
                ordering = fieldOrdering;
            } else {
                ordering = ordering.compound(fieldOrdering);
            }
        }
        
        return ordering != null ? ordering : BY_NAME;
    }

    private Ordering<Product> getFieldOrdering(SortField field) {
        Ordering<Product> base;
        
        switch (field.getField()) {
            case "price":
                base = BY_PRICE_ASC;
                break;
            case "sales":
                base = BY_SALES_DESC;
                break;
            case "rating":
                base = BY_RATING_DESC;
                break;
            case "name":
                base = BY_NAME;
                break;
            default:
                base = BY_NAME;
        }
        
        return field.isDesc() ? base.reverse() : base;
    }

    // ========== 配置类 ==========
    public static class SortConfig {
        private List<SortField> fields = new ArrayList<>();
        
        public void addField(String field, boolean desc) {
            fields.add(new SortField(field, desc));
        }
        
        public List<SortField> getFields() { return fields; }
    }

    public static class SortField {
        private final String field;
        private final boolean desc;
        
        public SortField(String field, boolean desc) {
            this.field = field;
            this.desc = desc;
        }
        
        public String getField() { return field; }
        public boolean isDesc() { return desc; }
    }

    // ========== 测试入口 ==========
    public static void main(String[] args) {
        ProductRankingSystem system = new ProductRankingSystem();

        // 创建测试数据
        List<Product> products = Arrays.asList(
            new Product(1L, "iPhone", 5999.0, 1000L, 4.8, 1, new Date()),
            new Product(2L, "iPad", 3999.0, 500L, 4.6, 2, new Date()),
            new Product(3L, "MacBook", 12999.0, 300L, 4.9, 1, new Date()),
            new Product(4L, "AirPods", 1299.0, 2000L, 4.7, 3, new Date()),
            new Product(5L, "Watch", 2999.0, null, 4.5, 2, new Date()),  // null sales
            new Product(6L, "HomePod", null, 100L, null, 3, new Date())   // null price & rating
        );

        System.out.println("=== 原始数据 ===");
        products.forEach(System.out::println);

        // 按价格升序
        System.out.println("\n=== 按价格升序 ===");
        system.sort(products, BY_PRICE_ASC).forEach(System.out::println);

        // 按销量降序
        System.out.println("\n=== 按销量降序 ===");
        system.sort(products, BY_SALES_DESC).forEach(System.out::println);

        // 综合排序
        System.out.println("\n=== 综合排序（销量->评分->价格） ===");
        system.sort(products, COMPOSITE_RANKING).forEach(System.out::println);

        // Top 3 销量
        System.out.println("\n=== Top 3 销量 ===");
        system.getTopProducts(products, 3, BY_SALES_DESC).forEach(System.out::println);

        // 动态排序配置
        System.out.println("\n=== 动态配置排序（价格降序->名称升序） ===");
        SortConfig config = new SortConfig();
        config.addField("price", true);
        config.addField("name", false);
        Ordering<Product> dynamicOrdering = system.buildOrdering(config);
        system.sort(products, dynamicOrdering).forEach(System.out::println);

        // 检查是否已排序
        List<Product> sorted = system.sort(products, BY_PRICE_ASC);
        System.out.println("\n排序后的列表是否已排序？" + system.isSorted(sorted, BY_PRICE_ASC));
    }
}
```

### 测试验证

```java
package com.example.guava.demo;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

import java.util.*;

public class ProductRankingSystemTest {

    private ProductRankingSystem system;
    private List<ProductRankingSystem.Product> products;

    @BeforeEach
    public void setUp() {
        system = new ProductRankingSystem();
        products = Arrays.asList(
            new ProductRankingSystem.Product(1L, "A", 100.0, 10L, 4.0, 1, new Date()),
            new ProductRankingSystem.Product(2L, "B", 200.0, 20L, 4.5, 2, new Date()),
            new ProductRankingSystem.Product(3L, "C", 100.0, 30L, 3.5, 1, new Date()),
            new ProductRankingSystem.Product(4L, "D", null, 5L, 5.0, 3, new Date())
        );
    }

    @Test
    public void testSortByPriceAsc() {
        List<ProductRankingSystem.Product> sorted = system.sort(products, ProductRankingSystem.BY_PRICE_ASC);
        
        assertEquals(100.0, sorted.get(0).getPrice());
        assertEquals(100.0, sorted.get(1).getPrice());
        assertEquals(200.0, sorted.get(2).getPrice());
        assertNull(sorted.get(3).getPrice());  // null 排最后
    }

    @Test
    public void testSortBySalesDesc() {
        List<ProductRankingSystem.Product> sorted = system.sort(products, ProductRankingSystem.BY_SALES_DESC);
        
        assertEquals(Long.valueOf(30), sorted.get(0).getSales());
        assertEquals(Long.valueOf(20), sorted.get(1).getSales());
        assertEquals(Long.valueOf(10), sorted.get(2).getSales());
    }

    @Test
    public void testCompositeOrdering() {
        List<ProductRankingSystem.Product> sorted = system.sort(products, ProductRankingSystem.COMPOSITE_RANKING);
        
        // 先按销量降序，销量相同按评分降序
        assertEquals(Long.valueOf(30), sorted.get(0).getSales());
    }

    @Test
    public void testGetTopProducts() {
        List<ProductRankingSystem.Product> top2 = system.getTopProducts(products, 2, ProductRankingSystem.BY_SALES_DESC);
        
        assertEquals(2, top2.size());
        assertEquals(Long.valueOf(30), top2.get(0).getSales());
        assertEquals(Long.valueOf(20), top2.get(1).getSales());
    }

    @Test
    public void testIsSorted() {
        List<ProductRankingSystem.Product> sorted = system.sort(products, ProductRankingSystem.BY_PRICE_ASC);
        assertTrue(system.isSorted(sorted, ProductRankingSystem.BY_PRICE_ASC));
        assertFalse(system.isSorted(products, ProductRankingSystem.BY_PRICE_ASC));
    }

    @Test
    public void testBuildDynamicOrdering() {
        ProductRankingSystem.SortConfig config = new ProductRankingSystem.SortConfig();
        config.addField("price", true);  // 降序
        
        var ordering = system.buildOrdering(config);
        List<ProductRankingSystem.Product> sorted = system.sort(products, ordering);
        
        assertEquals(200.0, sorted.get(0).getPrice());
    }

    @Test
    public void testNullsLast() {
        List<ProductRankingSystem.Product> sorted = system.sort(products, ProductRankingSystem.BY_PRICE_ASC);
        assertNull(sorted.get(sorted.size() - 1).getPrice());
    }
}
```

### 可能遇到的坑及解决方法

| 坑点 | 现象 | 解决方法 |
|------|------|----------|
| `leastOf` 与 `greatestOf` 语义 | 降序时 leastOf 返回最大值 | 理解 Ordering 的反向语义 |
| 复合排序性能 | 多字段排序慢 | 考虑缓存排序键或使用数据库 |
| null 处理不一致 | 不同字段 null 位置不同 | 统一配置 nullsFirst/nullsLast |
| 稳定性问题 | 相等元素顺序变化 | Java 排序是稳定的，Guava 继承 |

---

## 4 项目总结

### 优缺点对比

| 维度 | Guava Ordering | Java 8 Comparator | 手写 Comparator |
|------|----------------|-------------------|-----------------|
| 链式构建 | ★★★★★ 流畅 | ★★★★ 良好 | ★ 无 |
| null 处理 | ★★★★★ 简洁 | ★★★★ nullsFirst/Last | ★★ 需手写 |
| 高级功能 | ★★★★★ 丰富 | ★★★ 基础 | ★★ 完全可控 |
| 性能 | ★★★★ 良好 | ★★★★★ 原生 | ★★★★★ 最优 |
| 兼容性 | ★★★★ Java 6+ | ★★★ Java 8+ | ★★★★★ 全版本 |

### 适用场景

1. **多字段排序**：主次排序字段组合
2. **动态排序规则**：用户可配置的排序
3. **Top N 查询**：`leastOf/greatestOf` 高效算法
4. **排行榜实现**：多种排序维度的排行榜
5. **空值处理**：统一的 null 排序策略

### 不适用场景

1. **简单单字段排序**：直接用 `Comparator.comparing()`
2. **性能极度敏感**：考虑数据库排序或专用算法
3. **Java 8+ 新项目**：可优先使用标准 Comparator

### 生产踩坑案例

**案例 1：leastOf 语义混淆**
```java
Ordering<Product> bySalesDesc = Ordering.natural()
    .onResultOf(Product::getSales).reverse();
// 想获取销量最高的 3 个
List<Product> top3 = bySalesDesc.leastOf(products, 3);  // 正确
// 不能 greatestOf，因为那返回最小的
```
解决：理解 `leastOf` 返回 Ordering 意义上的'最小'。

**案例 2：复合排序性能下降**
```java
Ordering<Product> complex = ordering1.compound(ordering2).compound(ordering3);
// 大数据量时频繁调用 compare
```
解决：考虑缓存排序键或预计算。

**案例 3：null 处理不一致**
```java
Ordering<Product> o1 = Ordering.natural().nullsFirst().onResultOf(Product::getPrice);
Ordering<Product> o2 = Ordering.natural().onResultOf(Product::getSales).nullsLast();
// 复合后 null 处理不一致
```
解决：每个字段 Ordering 单独配置 null 策略。

### 思考题答案（第 13 章思考题 1）

> **问题**：什么场景下应该优先选择 fastutil/Trove 而非 Guava Primitives？

**答案**：
1. **海量数据存储**：`List<Long>` 存储百万级数据，fastutil 的 `LongArrayList` 避免装箱
2. **高频数值计算**：统计、机器学习等需要原始数组的场景
3. **内存受限环境**：嵌入式、缓存等需要极致内存效率的场景

Guava Primitives 定位为轻量级工具，不是高性能集合库。

### 新思考题

1. 设计一个支持随机访问的 Top N 算法，结合 `Ordering` 和优先队列。
2. 比较 `Ordering.leastOf` 和 Stream API 的 `sorted().limit()` 在性能上的差异。

### 推广计划提示

**开发**：
- 多字段排序使用 Ordering
- 统一 null 处理策略
- 利用 leastOf/greatestOf 优化 Top N

**测试**：
- 验证 null 排序位置
- 测试复合排序稳定性

**运维**：
- 监控排序操作性能
- 大数据量考虑分页
