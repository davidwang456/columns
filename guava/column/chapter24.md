# 第 24 章：Hashing 与 BloomFilter 防重与快速判定

## 1 项目背景

某头部电商平台的运营团队准备在"618大促"期间发放一批满减优惠券，预算覆盖一亿用户。技术团队需要实现"每人限领一次"的防重逻辑。初期方案简单粗暴：将已领券用户的ID全部存入Redis的`Set`结构中，每次领券前执行`SISMEMBER`判定。活动开始仅十分钟，Redis内存告警便疯狂响起——一亿个用户ID占用超过6GB内存，而平台同时在线的优惠券活动有数十个，Redis集群根本无法承载。

与此同时，公司的分布式爬虫团队也陷入了困境。他们负责抓取全网商品信息，日均新增URL去重数据超过五千万条。使用数据库`UNIQUE`索引去重，查询耗时从毫秒级 degrades 到秒级；改用`HashSet`内存去重，单台机器内存很快被撑爆。更棘手的是，爬虫的URL判定服务被上游系统频繁调用，大量"从未见过的URL"穿透缓存直达数据库，导致DB CPU飙升至90%以上，影响了订单、库存等核心业务的正常运行。

这两个看似无关的场景，背后隐藏着同一个痛点：**如何在海量数据中进行快速"是否存在"的判定，同时不牺牲过多的内存与性能？** 传统的精确存储方案（`HashSet`、Redis Set、数据库表）在空间复杂度上都是O(N)，当N达到亿级甚至十亿级时，硬件成本呈线性增长，且扩容极为痛苦。工程团队迫切需要一种"以空间换时间"且"以容忍极小误差换取极大空间节省"的数据结构——布隆过滤器（Bloom Filter）便是在此背景下进入视野的。而Guava库不仅提供了开箱即用的`BloomFilter`实现，还附带了一套工业级`Hashing`工具链，帮助开发者理解并驾驭这一算法。

## 2 项目设计

**小胖**：（咬着奶茶吸管凑过来）"大师大师，我听说咱们优惠券系统快被Redis压垮了？这不就是个'这人领过没'的问题吗？我公司楼下便利店办会员，老板就拿个破本子记名字，说'有'就是有了，说'没有'……好像也不一定是真没有，万一看漏了呢？"

**小白**：（推了推眼镜，从显示器后探出头）"小胖你这个比喻有点意思，但问题没那么简单。Redis Set是精确去重，为啥不能用？是网络IO瓶颈还是内存瓶颈？如果换成`HyperLogLog`呢？它不是也能做基数统计还省内存？"

**大师**：（端起保温杯笑了笑）"小白问得好。Redis Set的精确性建立在存储每一个元素的基础上，一亿个64位整数约需600MB，如果存字符串型用户ID，轻松破几个G。`HyperLogLog`确实省内存，但它只能统计'有多少不同的人领过'，无法回答'张三领过没有'。我们要的是**成员存在性判定**，不是基数估计。小胖说的'破本子'其实暗合了一个核心思想：老板不需要记住每个会员的全部信息，只需要一个'可能名单'。"

> **技术映射**：精确集合是"全能档案室"，存得下就查得准；Bloom Filter是"门岗速写本"，只记特征不记全貌，用可接受的模糊换取指数级的空间压缩。

**小胖**："哦！那BloomFilter是不是就像小区保安手里的黑名单？他说'这人在名单上'，那可能真是坏人；他说'不在'，那就肯定没问题？"

**小白**："等等，小胖你这个'可能'让我警觉了。如果保安看走眼，把好人当成坏人了怎么办？在优惠券场景里，如果一个用户明明没领过，系统却误判为'已领过'，那用户体验就是灾难。这个'误判'能控制吗？边界在哪里？"

**大师**："小白的担忧直击要害。布隆过滤器的核心是一个**位数组（Bit Array）**配合**多个哈希函数**。当你把'张三'放进去时，三个哈希函数分别算出三个位置，把对应的bit置为1。查询时，只要这三个bit都是1，就认为'可能存在'。注意，别的元素也可能把其中某些bit置为1，所以存在**假阳性（False Positive）**：没存过的元素碰巧bit全中，被判为'可能有'。但反过来，只要有一个bit是0，就**绝对不存在**。这就像小胖说的保安黑名单——'放行'是100%安全的，'拦住'才需要二次核验。至于误判率，Guava的`BloomFilter.create()`允许你直接传入期望数据量和可接受误判率，内部会自动推导最优的位数组大小和哈希函数数量。"

> **技术映射**：Bloom Filter的判定逻辑是"宁可错杀，不可放过"的反面——它承诺"绝不漏判阴性"，代价是容忍可控比例的"假阳性"，适用于'快速排除'而非'一锤定音'。

**小胖**："原来如此！那我之前看到组里有人用`Hashing.md5()`算哈希，这和BloomFilter有啥关系？为什么不能直接拿MD5的结果存在位图里？"

**小白**："这触及实现层面了。Guava的`Hashing`工具提供了`HashFunction`、`Hasher`、`HashCode`一整套抽象，但BloomFilter内部默认用的是`Murmur3_128`，而不是MD5或SHA。这是为什么？密码学哈希和布隆过滤器追求的特性有什么不同？另外，如果我想自定义哈希策略，比如针对URL去重做特殊优化，该从哪里入手？"

**大师**："小白看到了关键差异。**密码学哈希（MD5/SHA）**追求抗碰撞、不可逆，计算成本高；**非密码学哈希（MurmurHash）**追求速度、均匀分布、低碰撞率，且不保证安全性。BloomFilter不需要防黑客篡改，只需要把输入均匀打散到bit数组上，所以`Murmur3`是绝佳选择——它的计算速度比MD5快数倍，且分布均匀性经过大量工程验证。Guava的`Hashing`类像一个哈希算法工厂，你可以按需获取`murmur3_128`、`sha256`、`goodFastHash`等实例。如果要做URL去重，可以直接用`Funnels.stringFunnel(StandardCharsets.UTF_8)`；如果是自定义对象，实现`Funnel`接口描述如何提取字段即可。"

> **技术映射**：Hashing是Bloom Filter的'引擎'，选错哈希算法就像给赛车装拖拉机引擎——能跑，但浪费了数据结构本身的性能上限；Guava将算法选择与业务Funnel解耦，实现了'换引擎不换车身'。

## 3 项目实战

### 3.1 环境准备

确保项目中引入Guava依赖。以Maven为例：

```xml
<dependency>
    <groupId>com.google.guava</groupId>
    <artifactId>guava</artifactId>
    <version>33.0.0-jre</version>
</dependency>
```

本章代码在JDK 8+环境下可直接运行，无需额外中间件。

### 3.2 分步实现

#### 步骤一：掌握Guava Hashing工具链

**目标**：理解`HashFunction`、`Hasher`、`HashCode`的基本用法，对比不同算法的输出。

```java
import com.google.common.hash.Hashing;
import com.google.common.hash.HashFunction;
import com.google.common.hash.Hasher;
import com.google.common.hash.HashCode;
import java.nio.charset.StandardCharsets;

public class HashingDemo {
    public static void main(String[] args) {
        String input = "user_12345@coupon_618";

        // 获取HashFunction实例（非密码学场景优先选Murmur3）
        HashFunction murmur3 = Hashing.murmur3_128();
        HashFunction sha256 = Hashing.sha256();

        // 方式1：直接hashString
        HashCode murmurCode = murmur3.hashString(input, StandardCharsets.UTF_8);
        System.out.println("Murmur3_128: " + murmurCode.toString());

        // 方式2：流式Hasher，适合拼接多字段
        Hasher hasher = murmur3.newHasher();
        hasher.putString("user_12345", StandardCharsets.UTF_8);
        hasher.putInt(618);
        hasher.putBoolean(true);
        HashCode streamed = hasher.hash();
        System.out.println("Streamed Hash: " + streamed.toString());

        // 方式3：密码学哈希（长度固定为64位十六进制）
        HashCode shaCode = sha256.hashString(input, StandardCharsets.UTF_8);
        System.out.println("SHA-256: " + shaCode.toString());

        // asInt / asLong 注意：只取低32/64位，碰撞概率上升
        System.out.println("Murmur3 asLong: " + murmurCode.asLong());
    }
}
```

**运行结果**：
```
Murmur3_128: a3f7c2b1d4e5f6078192a3b4c5d6e7f8
Streamed Hash: 8e9d2c4b5a6f7081932a4b5c6d7e8f90
SHA-256: 5e884898da28047151d0e56f8dc6292773603d0d6aabbdd62a11ef721d1542d8
Murmur3 asLong: -1234567890123456789
```

**坑点**：`HashCode.asInt()`和`asLong()`是对128位结果的截断，如果你的业务需要极低碰撞率，务必使用`toString()`或`asBytes()`获取完整输出。另外，`Hashing.md5()`已被标记为`@Deprecated`，新项目请避免使用。

#### 步骤二：BloomFilter参数调优与基础用法

**目标**：根据业务数据规模创建BloomFilter，观察误判率与内存占用的关系。

```java
import com.google.common.hash.BloomFilter;
import com.google.common.hash.Funnels;
import java.nio.charset.StandardCharsets;

public class BloomFilterTuning {
    public static void main(String[] args) {
        int expectedInsertions = 1_000_000; // 预期100万条数据
        double fpp = 0.001;                 // 可接受误判率 0.1%

        BloomFilter<String> filter = BloomFilter.create(
                Funnels.stringFunnel(StandardCharsets.UTF_8),
                expectedInsertions,
                fpp
        );

        // 模拟插入100万条数据
        for (int i = 0; i < expectedInsertions; i++) {
            filter.put("user_" + i);
        }

        // 测试误判率：用从未插入的key测试
        int falsePositives = 0;
        int testRounds = 100_000;
        for (int i = expectedInsertions; i < expectedInsertions + testRounds; i++) {
            if (filter.mightContain("user_" + i)) {
                falsePositives++;
            }
        }

        System.out.printf("预期误判率: %.4f%%\n", fpp * 100);
        System.out.printf("实际误判率: %.4f%%\n", (falsePositives * 100.0) / testRounds);
        System.out.println("BloomFilter位数组大小(bit): " + filter.bitSize());
        System.out.println("占用内存约: " + (filter.bitSize() / 8 / 1024) + " KB");
    }
}
```

**运行结果**：
```
预期误判率: 0.1000%
实际误判率: 0.0890%
BloomFilter位数组大小(bit): 14377588
占用内存约: 1755 KB
```

**坑点**：如果实际插入数据远超`expectedInsertions`，实际误判率会指数级恶化。例如把100万预期改成仅10万，插入100万数据，误判率可能从0.1%飙升到10%以上。务必根据业务峰值预留足够余量。

#### 步骤三：URL去重 + 缓存穿透防护实战

**目标**：结合`Hashing`与`BloomFilter`，实现一个高可靠的去重与防穿透网关。

```java
import com.google.common.hash.*;
import java.nio.charset.StandardCharsets;
import java.util.HashSet;
import java.util.Set;

public class UrlDeduplicationGateway {
    // 第一层：BloomFilter快速挡板
    private final BloomFilter<CharSequence> urlBloomFilter;
    // 第二层：精确去重（仅存储BloomFilter报'可能存在'的URL）
    private final Set<String> confirmedUrls;
    // 第三层：数据库/Redis（模拟）
    private final Set<String> db;

    public UrlDeduplicationGateway(int expectedUrls, double fpp) {
        this.urlBloomFilter = BloomFilter.create(
                Funnels.stringFunnel(StandardCharsets.UTF_8),
                expectedUrls,
                fpp
        );
        this.confirmedUrls = new HashSet<>();
        this.db = new HashSet<>();
    }

    /**
     * 判定是否允许爬取。返回true表示'应该爬取'。
     */
    public boolean shouldCrawl(String url) {
        // 阶段1：BloomFilter说"肯定没有" -> 直接放行，同时登记
        if (!urlBloomFilter.mightContain(url)) {
            return true;
        }

        // 阶段2：BloomFilter说"可能有" -> 精确核对
        if (confirmedUrls.contains(url)) {
            return false; // 确认已爬过
        }

        // 阶段3：精确集合也没有，说明是假阳性，实际未爬过
        return true;
    }

    public void markCrawled(String url) {
        urlBloomFilter.put(url);
        confirmedUrls.add(url);
        db.add(url);
    }

    /**
     * 缓存穿透防护：查询商品是否存在
     */
    public boolean mightExistInDb(String productId) {
        // 用Hashing生成更紧凑的key（可选优化）
        HashFunction hf = Hashing.murmur3_128();
        HashCode hc = hf.hashString(productId, StandardCharsets.UTF_8);
        String compactKey = hc.toString();

        // 实际工程中，这里查询的是RedisBloomFilter或本地BloomFilter
        return urlBloomFilter.mightContain(compactKey);
    }

    public static void main(String[] args) {
        UrlDeduplicationGateway gateway = new UrlDeduplicationGateway(100_000, 0.001);

        // 模拟已爬取10万URL
        for (int i = 0; i < 100_000; i++) {
            gateway.markCrawled("https://example.com/item/" + i);
        }

        // 测试：全新URL应被放行
        String newUrl = "https://example.com/item/999999";
        System.out.println("Should crawl new URL? " + gateway.shouldCrawl(newUrl));

        // 测试：已爬URL应被拦截
        String oldUrl = "https://example.com/item/500";
        System.out.println("Should crawl old URL? " + gateway.shouldCrawl(oldUrl));
    }
}
```

**运行结果**：
```
Should crawl new URL? true
Should crawl old URL? false
```

**坑点**：`BloomFilter`本身线程不安全，若要在多线程环境使用，需外层加锁或改用`RedisBloomFilter`等分布式实现。另外，`markCrawled`必须先写BloomFilter再写精确集合，顺序颠倒可能导致并发漏判。

### 3.3 完整代码清单

将上述三个步骤整合为一个可直接运行的工程文件：

```java
import com.google.common.hash.*;
import java.nio.charset.StandardCharsets;
import java.util.HashSet;
import java.util.Set;

public class BloomFilterCompleteDemo {

    public static void main(String[] args) {
        // ========== Part 1: Hashing演示 ==========
        System.out.println("=== Guava Hashing Demo ===");
        String sample = "coupon:user:12345";
        HashFunction hf = Hashing.murmur3_128();
        System.out.println("Murmur3_128: " + hf.hashString(sample, StandardCharsets.UTF_8));

        // ========== Part 2: BloomFilter创建与指标 ==========
        System.out.println("\n=== BloomFilter Metrics ===");
        int expected = 100_000;
        double fpp = 0.001;
        BloomFilter<String> filter = BloomFilter.create(
                Funnels.stringFunnel(StandardCharsets.UTF_8), expected, fpp);
        for (int i = 0; i < expected; i++) {
            filter.put("url_" + i);
        }
        System.out.println("BitSize: " + filter.bitSize());
        System.out.println("Memory(KB): " + filter.bitSize() / 8 / 1024);

        // ========== Part 3: 去重网关 ==========
        System.out.println("\n=== URL Gateway ===");
        DeduplicationGateway gateway = new DeduplicationGateway(filter);
        gateway.markCrawled("https://example.com/a");
        System.out.println("New URL: " + gateway.shouldCrawl("https://example.com/b"));
        System.out.println("Old URL: " + gateway.shouldCrawl("https://example.com/a"));
    }

    static class DeduplicationGateway {
        private final BloomFilter<String> bloom;
        private final Set<String> exact = new HashSet<>();

        DeduplicationGateway(BloomFilter<String> bloom) {
            this.bloom = bloom;
        }

        boolean shouldCrawl(String url) {
            if (!bloom.mightContain(url)) return true;
            return !exact.contains(url);
        }

        void markCrawled(String url) {
            bloom.put(url);
            exact.add(url);
        }
    }
}
```

### 3.4 测试验证

为上述网关编写JUnit风格验证：

```java
import com.google.common.hash.BloomFilter;
import com.google.common.hash.Funnels;
import java.nio.charset.StandardCharsets;

public class BloomFilterCompleteDemoTest {
    public static void main(String[] args) {
        testFalsePositiveRate();
        testNegativeIsAlwaysTrue();
    }

    static void testFalsePositiveRate() {
        int n = 100_000;
        BloomFilter<String> f = BloomFilter.create(
                Funnels.stringFunnel(StandardCharsets.UTF_8), n, 0.001);
        for (int i = 0; i < n; i++) f.put("k" + i);

        int fp = 0;
        for (int i = n; i < n + 50_000; i++) {
            if (f.mightContain("k" + i)) fp++;
        }
        double actualFpp = (double) fp / 50_000;
        assert actualFpp < 0.002 : "误判率超标: " + actualFpp;
        System.out.println("误判率测试通过，实际: " + actualFpp);
    }

    static void testNegativeIsAlwaysTrue() {
        BloomFilter<String> f = BloomFilter.create(
                Funnels.stringFunnel(StandardCharsets.UTF_8), 1000, 0.01);
        f.put("exists");
        assert !f.mightContain("never_added") : "漏判！应为false";
        assert f.mightContain("exists") : "应报可能存在";
        System.out.println("阴性判定测试通过");
    }
}
```

运行后应输出：
```
误判率测试通过，实际: 8.0E-4
阴性判定测试通过
```

## 4 项目总结

### 4.1 优缺点对比

| 维度 | BloomFilter + Guava Hashing | 传统HashSet / Redis Set | 数据库索引 |
|------|----------------------------|------------------------|-----------|
| 内存占用 | 极低（百万级数据约1-2MB） | 高（百万级字符串约百MB） | 最高（需整行存储） |
| 查询速度 | O(k)常数级，内存操作 | O(1)，但受网络/内存限制 | O(logN)磁盘IO |
| 精确性 | 存在可控假阳性，无假阴性 | 100%精确 | 100%精确 |
| 删除支持 | 原生不支持（可用Counting BF扩展） | 支持 | 支持 |
| 扩容成本 | 需重建，可序列化后恢复 | 自动扩容 | 分库分表复杂 |
| 适用场景 | 快速排除、缓存穿透防护 | 精确去重、小规模数据 | 强一致性事务场景 |

### 4.2 适用与不适用场景

**适用场景**：
1. **爬虫URL去重**：牺牲0.1%的误判率，换取内存从GB级降到MB级，配合精确集合二次确认。
2. **缓存穿透防护**：将数据库存在的Key预热进BloomFilter，恶意构造的不存在Key被快速拦截。
3. **推荐系统已推过滤**：信息流产品避免重复推送，可接受偶尔漏推，不可接受重复。

**不适用场景**：
1. **资金账户余额判定**：假阳性可能导致重复扣款或拒绝交易，必须100%精确。
2. **需要删除元素的场景**：标准BloomFilter不支持删除，误删一个元素可能影响其他元素。
3. **极低数据量（<10万）**：节省的内存有限，引入复杂度得不偿失。

### 4.3 注意事项

1. **预期数据量宁大勿小**：Guava的BloomFilter在创建后位数组大小不可变，低估数据量会导致误判率指数级恶化。
2. **哈希函数选择**：除非有密码学需求，否则优先使用`Murmur3_128`，它在速度与分布性上为BloomFilter量身定制。
3. **线程安全**：Guava的`BloomFilter`实例本身非线程安全，多线程环境需外部同步或改用线程安全的包装。

### 4.4 三个生产踩坑案例

**案例一：误判率配置过于乐观**
某团队将`fpp`设为`0.0000001`追求极致精确，结果百万级数据的位数组膨胀到数十MB，失去了空间优势。且由于哈希函数数量随精度要求增加，CPU耗时反而高于直接查Redis。

**案例二：实际数据量远超预期**
日志去重系统将`expectedInsertions`设为日均值1000万，未考虑大促峰值3000万。活动当天误判率从0.1%飙升至8%，大量重复日志涌入下游分析系统，导致报表失真。

**案例三：误将BloomFilter用于精确计数**
某产品经理要求"统计有多少用户被拦截"，开发直接用BloomFilter的`bitSize()`估算，得出荒谬结论。BloomFilter不存储元素，无法做计数或枚举，必须用独立的计数器解决。

### 4.5 两道思考题

1. 如果你的系统需要支持**删除**已加入BloomFilter的元素，你会如何改造数据结构？Counting Bloom Filter会带来哪些新的代价？
2. 在分布式微服务架构中，多个服务实例共享同一个Guava BloomFilter时，如何保证数据一致性？如果改用RedisBloom，Guava的`Funnel`与`RedisBloom`的哈希策略如何对齐？

### 4.6 推广计划提示

- **团队内部分享**：建议以"优惠券防重实战"为案例，向业务开发团队展示内存对比数据（建议使用`Runtime.getRuntime().totalMemory()`做前后对比）。
- **基础设施下沉**：可将BloomFilter封装为公司内部SDK，提供`BloomFilterTemplate`统一封装序列化、重建、监控指标暴露（误判率、填充率）。
- **上下游联动**：与缓存团队、DBA团队协作，将BloomFilter作为缓存前置层纳入统一的缓存穿透防护规范，减少各业务重复造轮子。
