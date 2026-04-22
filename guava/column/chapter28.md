# 第 28 章：Streams + Guava 协同写法与可读性权衡

## 1 项目背景

在系统升级后，开发团队内部出现了分歧。Java 8 的 Stream API 和 Guava 的函数式工具都可以处理集合操作，有的同事坚持用 Stream，有的坚持 Guava，代码风格不统一，新人无所适从。

## 2 项目设计

**大师**："两种工具各有优势，需要根据场景选择：

```java
// Java 8 Stream 优势：链式操作、并行支持
list.stream()
    .filter(p -> p.getPrice() > 100)
    .map(Product::getName)
    .sorted()
    .collect(Collectors.toList());

// Guava 优势：null 安全、不可变、早期计算
ImmutableList<String> names = list.stream()
    .filter(p -> p.getPrice() > 100)
    .map(Product::getName)
    .collect(ImmutableList.toImmutableList());

// Guava 特有功能（Stream 无法实现）
ImmutableMultiset<String> counts = ImmutableMultiset.copyOf(
    Iterables.transform(products, Product::getCategory)
);
```

**技术映射**：选择工具就像选交通工具——短途步行（手写循环），中途骑车（Stream），长途开车（Guava+Stream）。"

## 3 项目实战

```java
// 推荐：Stream 为主，Guava 补充
public class HybridApproach {
    
    // Stream 处理链式操作
    public List<String> getTopProductNames(List<Product> products, int n) {
        return products.stream()
            .filter(p -> p.getRating() > 4.0)
            .sorted(Comparator.comparing(Product::getSales).reversed())
            .limit(n)
            .map(Product::getName)
            .collect(Collectors.toList());
    }
    
    // Guava 处理 null 安全
    public ImmutableMap<String, Product> indexById(List<Product> products) {
        return products.stream()
            .filter(Objects::nonNull)
            .collect(ImmutableMap.toImmutableMap(
                Product::getId,
                p -> p,
                (p1, p2) -> p1  // 处理重复
            ));
    }
    
    // Guava 特有场景：Multiset 计数
    public Multiset<String> countByCategory(List<Product> products) {
        return products.stream()
            .map(Product::getCategory)
            .collect(ImmutableMultiset.toImmutableMultiset());
    }
    
    // 并行处理（Stream 优势）
    public double averagePrice(List<Product> products) {
        return products.parallelStream()
            .mapToDouble(Product::getPrice)
            .average()
            .orElse(0.0);
    }
}

// 团队编码规范建议
public class StyleGuide {
    // 1. 简单过滤/映射：优先 Stream
    // 2. 需要不可变结果：Guava Collector
    // 3. 需要 Multiset/Multimap：Guava
    // 4. 并行处理：Stream
    // 5. null 处理：Guava Optional
}
```

## 4 项目总结

### 选择决策树

```
需要并行处理？
  是 -> Stream
  否 -> 需要不可变结果？
         是 -> Guava Immutable
         否 -> 复杂链式操作？
                是 -> Stream（可读性好）
                否 -> Guava（null 安全）
```

### 混合使用最佳实践

1. Stream 做流程控制
2. Guava 做数据容器
3. 统一团队规范
