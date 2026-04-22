# 第 24 章：Hashing 与 BloomFilter 防重与快速判定

## 1 项目背景

在爬虫系统的 URL 去重模块中，工程师小陈需要快速判断一个 URL 是否已抓取。使用 HashSet 存储已抓取 URL，内存占用随着数据量增长急剧膨胀，最终达到数十 GB。需要一种更节省内存的去重方案。

## 2 项目设计

**大师**："Bloom Filter 以可接受的误判率换取巨大内存节省：

```java
// 创建布隆过滤器
BloomFilter<String> filter = BloomFilter.create(
    Funnels.stringFunnel(Charset.defaultCharset()),
    10000000,  // 预期数据量
    0.01       // 误判率 1%
);

// 添加元素
filter.put(url);

// 判定（可能误判，但不会漏判）
boolean mightContain = filter.mightContain(url);
```

**技术映射**：Bloom Filter 就像是'可能存在名单'——它说'可能有'时可能有，说'肯定没有'时肯定没有。"

## 3 项目实战

```java
public class UrlDeduplicator {
    private final BloomFilter<String> crawledUrls;
    private final Set<String> confirmedUrls;  // 二次确认
    
    public UrlDeduplicator() {
        crawledUrls = BloomFilter.create(
            Funnels.stringFunnel(StandardCharsets.UTF_8),
            100_000_000, 0.01
        );
        confirmedUrls = Sets.newHashSetWithExpectedSize(10000);
    }
    
    public boolean shouldCrawl(String url) {
        // 第一阶段：Bloom Filter 快速过滤
        if (!crawledUrls.mightContain(url)) {
            return true;  // 肯定没爬过
        }
        
        // 第二阶段：精确检查（仅少数情况）
        return !confirmedUrls.contains(url);
    }
    
    public void markCrawled(String url) {
        crawledUrls.put(url);
        confirmedUrls.add(url);
    }
}

// Guava Hashing 工具
HashFunction hf = Hashing.md5();  // 或 sha256, murmur3_128
HashCode hc = hf.hashString("data", StandardCharsets.UTF_8);
String hex = hc.toString();
```

## 4 项目总结

### Bloom Filter 特点

| 特性 | 说明 |
|------|------|
| 内存占用 | 极省（比 HashSet 小 10-100 倍）|
| 误判率 | 可调节，但无法避免 |
| 不可删除 | 标准实现不支持删除 |
| 扩容困难 | 需要重建 |

### 适用场景

1. URL 去重
2. 垃圾邮件过滤
3. 缓存穿透防护
4. 数据库查询优化（快速判断不存在）
