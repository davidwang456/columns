# 第 35 章：BloomFilter 参数推导与误判率工程化校准

## 1 项目背景

在大规模 URL 去重系统中，数据工程师小郑需要精确配置 BloomFilter 参数。100 亿条 URL，要求误判率低于 0.1%，需要计算最优位数组大小和哈希函数数量。

## 2 项目设计

**大师**："BloomFilter 数学原理：

```
最优位数组大小 m = -n * ln(p) / (ln(2)^2)
最优哈希函数数 k = m/n * ln(2)

其中：
n = 预期元素数量
p = 目标误判率
m = 位数组大小（bits）
k = 哈希函数数量
```

**技术映射**：参数推导就像是'配药方'——根据症状（数据量、精度要求）计算药量（位数组大小）。"

## 3 项目实战

```java
public class BloomFilterCalculator {
    
    // 计算最优参数
    public static BloomFilterConfig calculate(int expectedInsertions, double fpp) {
        // m = -n * ln(p) / (ln(2)^2)
        long m = (long) (-expectedInsertions * Math.log(fpp) / (Math.log(2) * Math.log(2)));
        
        // k = m/n * ln(2)
        int k = Math.max(1, (int) Math.round(m / expectedInsertions * Math.log(2)));
        
        // 位数组转换为字节数
        long bytes = (m + 7) / 8;
        
        return new BloomFilterConfig(m, k, bytes, fpp);
    }
    
    // 根据内存限制反推可支持的数据量
    public static int maxInsertions(long maxBytes, double fpp) {
        // n = -m * (ln(2)^2) / ln(p)
        long m = maxBytes * 8;
        return (int) (-m * Math.pow(Math.log(2), 2) / Math.log(fpp));
    }
    
    // 在线误判率估算
    public double estimateFpp(int insertedElements, long bitSize, int numHashFunctions) {
        return Math.pow(1 - Math.exp(-numHashFunctions * insertedElements / (double) bitSize), 
                       numHashFunctions);
    }
}

// 使用示例
public class UrlDeduplicationService {
    private final BloomFilter<CharSequence> urlFilter;
    private final int expectedUrls = 1_000_000_000;  // 10 亿
    private final double targetFpp = 0.001;  // 0.1%
    
    public UrlDeduplicationService() {
        // 计算配置
        var config = BloomFilterCalculator.calculate(expectedUrls, targetFpp);
        System.out.println("位数组: " + config.bits + " bits (" + config.bytes / 1024 / 1024 + " MB)");
        System.out.println("哈希函数: " + config.numHashFunctions);
        
        this.urlFilter = BloomFilter.create(
            Funnels.stringFunnel(StandardCharsets.UTF_8),
            expectedUrls,
            targetFpp
        );
    }
}
```

## 4 项目总结

### 参数参考表

| 数据量 | 误判率 | 内存占用 | 哈希函数 |
|--------|--------|----------|----------|
| 100万 | 1% | 1.14 MB | 7 |
| 100万 | 0.1% | 1.71 MB | 10 |
| 1亿 | 1% | 114 MB | 7 |
| 1亿 | 0.1% | 171 MB | 10 |

### 工程实践

1. 预留 20% 容量余量
2. 定期重建校准误判率
3. 结合精确集合二次验证
4. 监控实际插入数量
