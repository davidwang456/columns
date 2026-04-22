# 第 31 章：Immutable 集合源码剖析与内存布局取舍

## 1 项目背景

在内存优化专项中，资深工程师小赵需要深入理解 Immutable 集合的实现机制。500 万条数据的 ImmutableMap 占用内存比预期大 30%，需要分析内存布局并优化。

## 2 项目设计

**大师**："ImmutableMap 使用 `RegularImmutableMap`（ entry 数组）或 `SingletonImmutableBiMap`：

```java
// ImmutableMap.of(k1, v1, k2, v2) 源码路径
// -> ImmutableMap::construct -> RegularImmutableMap
// 内部结构：Entry<K, V>[] entries;

// 内存布局分析
HashMap:  table[] + Entry(16 bytes header + key + value + next + hash)
ImmutableMap: entries[] + 紧凑存储，无链表指针
```

**技术映射**：Immutable 通过紧凑布局和去指针化换取内存效率，但构建时拷贝成本。"

## 3 项目实战

```java
// 内存分析工具
public class MemoryAnalyzer {
    
    // 比较不同实现内存占用
    public void compareMemoryUsage() {
        int size = 100000;
        
        // HashMap
        Map<String, String> hashMap = new HashMap<>();
        // ... populate
        
        // ImmutableMap
        ImmutableMap<String, String> immutableMap = ImmutableMap.copyOf(hashMap);
        
        // 分析：ImmutableMap 节省约 20-30% 内存（无链表指针）
    }
    
    // 构建优化：预估容量避免扩容
    ImmutableMap.Builder<String, String> builder = 
        ImmutableMap.builderWithExpectedSize(10000);
}

// 源码关键片段分析
// RegularImmutableMap.createFromEntries:
// 1. 创建 Entry 数组
// 2. 构建时排序去重（HashMap 冲突处理）
// 3. 不可变后使用二分查找或开放寻址
```

## 4 项目总结

### Immutable 内存优势

| 特性 | HashMap | ImmutableMap |
|------|---------|--------------|
| 链表指针 | 有（next）| 无 |
| 扩容预留 | 有 | 无（精确大小）|
| 哈希缓存 | 有 | 无（构建时计算）|
| 内存占用 | 高 | 低 20-30% |

### 构建优化建议

1. 预估容量使用 `builderWithExpectedSize`
2. 避免小对象 Immutable（< 10 条目）
3. 大数据量考虑 `ImmutableSortedMap`（有序压缩）
