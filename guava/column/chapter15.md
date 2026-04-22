# 第 15 章：基础篇综合实战：用户画像清洗与标签引擎（单机版）

## 1 项目背景

在电商平台的用户增长团队中，数据分析师小王接手了一个紧急任务：清洗和标准化 500 万用户的历史数据，并构建一个标签引擎用于精细化运营。数据来自多个渠道——注册信息、订单记录、浏览行为、客服反馈，格式混乱不堪：有的手机号带区号，有的为空；有的年龄是字符串"25岁"，有的直接写数字；地址字段里夹杂着全角半角、特殊符号、重复空格。

更糟糕的是，业务方要求周五上线，而小王发现光是数据清洗的样板代码就需要写一周。他必须找到一个能快速交付的方案。

**业务场景**：用户画像数据清洗、标签提取、规则匹配等数据工程任务。

**痛点放大**：
- **数据格式混乱**：同一字段有多种格式，需要标准化。
- **校验逻辑复杂**：手机号、邮箱、身份证号格式校验分散在各处。
- **规则引擎缺失**：用户标签的匹配规则用硬编码 SQL，维护困难。
- **数据量中等**：500万条，单机内存可处理，但需要高效实现。
- **工期紧张**：没有时间去构建复杂的数据管道。

如果有一套现成的基础工具能快速组合出解决方案，将大大缩短交付周期。

**技术映射**：本章综合运用 Guava 基础篇学到的 `Optional`、`Preconditions`、`Strings`、`CharMatcher`、`Immutable` 集合、`Multimap`、`Range`、`Ordering` 等工具，构建一个完整的用户画像清洗与标签引擎。

---

## 2 项目设计

**场景**：项目启动会，讨论技术方案选型。

---

**小胖**：（看着需求文档）"我说，这任务也太急了吧！500 万条数据，各种格式混乱，周五就要上线。这不就跟食堂突然来了 5000 人要吃饭，但菜还没切一样吗？"

**小白**：（皱眉）"传统做法是写 Spark 或 Hadoop 作业，但那套环境搭建就要几天。数据量其实单机内存能放下，500万用户数据压缩后也就几百 MB。"

**大师**：（在白板上画架构）"我们可以用 Guava 基础工具快速搭建单机版方案：

```
原始数据 → CharMatcher 清洗 → Preconditions 校验 → 
ImmutableMap 标准化存储 → Multimap 标签索引 → Range 年龄段分桶
```

**技术映射**：这套方案就像是'组装家具'——Guava 提供了标准化的'板材'和'连接器'，你只需要按图纸组装，不用从零开始锯木头。"

**小胖**："具体怎么设计？"

**小白**："分三个模块：

1. **数据清洗模块**：
   - `CharMatcher` 去除控制字符、标准化空白
   - `Strings.nullToEmpty` 处理空值
   - 正则 + `Preconditions` 校验格式

2. **标准化存储模块**：
   - `ImmutableMap` 存储用户标准化后的字段
   - `Optional` 表达可选字段
   - `Range` 表示年龄段、消费区间

3. **标签引擎模块**：
   - `Multimap` 建立标签→用户索引
   - `Ordering` 标签权重排序
   - `Multiset` 统计标签分布

**大师**："而且 Guava 的工具都是**不可变**或**线程安全**的，单机多线程处理时不用担心并发问题。

**技术映射**：Guava 基础工具的组合使用，让'小数据量、快速交付'的场景有了一个轻量级但工程化程度高的解决方案。"

**小胖**："那性能怎么样？500 万条能处理完吗？"

**小白**："估算一下：
- CharMatcher 清洗：单条 < 1ms，500万条约 10 分钟
- 内存占用：ImmutableMap 每个用户约 200 字节，500万约 1GB
- 单机 8G 内存的机器完全可以处理

而且处理流程可以并行化，用 Java 8 Stream parallel() 加速。"

**大师**："但要注意**扩展性边界**——单机方案只适合 500 万用户这个量级。如果数据量涨到 5000 万，就需要迁移到 Spark。Guava 方案的价值在于**快速验证业务逻辑**，后续可以平滑迁移。

**技术映射**：技术选型要考虑'当前需求'和'未来增长'的平衡，Guava 单机方案作为 MVP（最小可行产品）是很合适的选择。"

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

### 分步实现：用户画像清洗与标签引擎

**步骤目标**：综合运用 Guava 基础工具构建完整的用户画像处理系统。

**代码实现**：

```java
package com.example.guava.demo;

import com.google.common.base.*;
import com.google.common.collect.*;
import com.google.common.primitives.Ints;

import java.util.*;
import java.util.regex.Pattern;
import java.util.stream.Collectors;

/**
 * 用户画像清洗与标签引擎（基础篇综合实战）
 */
public class UserProfileEngine {

    // ========== 配置常量 ==========
    private static final Pattern PHONE_PATTERN = Pattern.compile("^1[3-9]\\d{9}$");
    private static final Pattern EMAIL_PATTERN = Pattern.compile("^[A-Za-z0-9+_.-]+@[A-Za-z0-9.-]+$");
    private static final CharMatcher CLEAN_MATCHER = CharMatcher.javaLetterOrDigit()
        .or(CharMatcher.whitespace())
        .or(CharMatcher.anyOf("@._-"));
    private static final CharMatcher WHITESPACE_COLLAPSER = CharMatcher.whitespace().collapseTo(' ');

    // ========== 年龄段定义 ==========
    private static final ImmutableMap<String, Range<Integer>> AGE_RANGES = ImmutableMap.of(
        "青少年", Range.closed(13, 18),
        "青年", Range.closed(19, 35),
        "中年", Range.closed(36, 50),
        "中老年", Range.closed(51, 65),
        "老年", Range.atLeast(66)
    );

    // ========== 数据模型 ==========
    public static class RawUser {
        String userId;
        String name;
        String phone;
        String email;
        String age;        // 可能为 "25" 或 "25岁"
        String address;    // 可能包含特殊字符
        String gender;     // "M"/"F"/"男"/"女"
        Double totalSpend; // 消费总额
        Integer orderCount; // 订单数
    }

    public static class CleanUser {
        private final String userId;
        private final String name;
        private final Optional<String> phone;
        private final Optional<String> email;
        private final Optional<Integer> age;
        private final String ageGroup;
        private final String address;
        private final String gender;
        private final Range<Double> spendRange;
        private final ImmutableList<String> tags;

        public CleanUser(String userId, String name, Optional<String> phone,
                        Optional<String> email, Optional<Integer> age,
                        String ageGroup, String address, String gender,
                        Range<Double> spendRange, ImmutableList<String> tags) {
            this.userId = userId;
            this.name = name;
            this.phone = phone;
            this.email = email;
            this.age = age;
            this.ageGroup = ageGroup;
            this.address = address;
            this.gender = gender;
            this.spendRange = spendRange;
            this.tags = tags;
        }

        // Getters
        public String getUserId() { return userId; }
        public String getName() { return name; }
        public Optional<String> getPhone() { return phone; }
        public Optional<String> getEmail() { return email; }
        public Optional<Integer> getAge() { return age; }
        public String getAgeGroup() { return ageGroup; }
        public String getAddress() { return address; }
        public String getGender() { return gender; }
        public Range<Double> getSpendRange() { return spendRange; }
        public ImmutableList<String> getTags() { return tags; }
    }

    // ========== 清洗流程 ==========

    public CleanUser cleanUser(RawUser raw) {
        // 1. 基础校验
        Preconditions.checkNotNull(raw, "Raw user cannot be null");
        Preconditions.checkArgument(!Strings.isNullOrEmpty(raw.userId), "User ID required");

        // 2. 字段清洗
        String name = cleanText(raw.name);
        String address = cleanText(raw.address);
        String gender = standardizeGender(raw.gender);

        // 3. 可选字段处理
        Optional<String> phone = parsePhone(raw.phone);
        Optional<String> email = parseEmail(raw.email);
        Optional<Integer> age = parseAge(raw.age);

        // 4. 衍生字段
        String ageGroup = age.map(this::calculateAgeGroup).orElse("未知");
        Range<Double> spendRange = calculateSpendRange(raw.totalSpend);

        // 5. 标签生成
        ImmutableList<String> tags = generateTags(age, gender, spendRange, raw.orderCount);

        return new CleanUser(
            raw.userId, name, phone, email, age, ageGroup,
            address, gender, spendRange, tags
        );
    }

    private String cleanText(String input) {
        if (Strings.isNullOrEmpty(input)) return "";
        
        return input
            .transform(CLEAN_MATCHER::retainFrom)       // 只保留合法字符
            .transform(WHITESPACE_COLLAPSER::collapseFrom)  // 合并空白
            .transform(CharMatcher.whitespace()::trimFrom)  // 修剪两端
            .toLowerCase();
    }

    private Optional<String> parsePhone(String phone) {
        if (Strings.isNullOrEmpty(phone)) return Optional.absent();
        
        String cleaned = CharMatcher.digit().retainFrom(phone);
        if (PHONE_PATTERN.matcher(cleaned).matches()) {
            return Optional.of(cleaned);
        }
        return Optional.absent();
    }

    private Optional<String> parseEmail(String email) {
        if (Strings.isNullOrEmpty(email)) return Optional.absent();
        
        String cleaned = email.trim().toLowerCase();
        if (EMAIL_PATTERN.matcher(cleaned).matches()) {
            return Optional.of(cleaned);
        }
        return Optional.absent();
    }

    private Optional<Integer> parseAge(String age) {
        if (Strings.isNullOrEmpty(age)) return Optional.absent();
        
        // 提取数字
        String digits = CharMatcher.digit().retainFrom(age);
        if (digits.isEmpty()) return Optional.absent();
        
        try {
            int ageValue = Integer.parseInt(digits);
            if (ageValue > 0 && ageValue < 150) {
                return Optional.of(ageValue);
            }
        } catch (NumberFormatException e) {
            // 忽略
        }
        return Optional.absent();
    }

    private String standardizeGender(String gender) {
        if (Strings.isNullOrEmpty(gender)) return "未知";
        
        String g = gender.trim().toUpperCase();
        if (g.equals("M") || g.equals("男") || g.equals("MALE")) return "男";
        if (g.equals("F") || g.equals("女") || g.equals("FEMALE")) return "女";
        return "未知";
    }

    private String calculateAgeGroup(int age) {
        for (Map.Entry<String, Range<Integer>> entry : AGE_RANGES.entrySet()) {
            if (entry.getValue().contains(age)) {
                return entry.getKey();
            }
        }
        return "未知";
    }

    private Range<Double> calculateSpendRange(Double spend) {
        if (spend == null || spend < 0) return Range.closed(0.0, 0.0);
        
        if (spend < 100) return Range.closedOpen(0.0, 100.0);
        if (spend < 500) return Range.closedOpen(100.0, 500.0);
        if (spend < 1000) return Range.closedOpen(500.0, 1000.0);
        if (spend < 5000) return Range.closedOpen(1000.0, 5000.0);
        return Range.atLeast(5000.0);
    }

    private ImmutableList<String> generateTags(Optional<Integer> age, String gender,
                                                  Range<Double> spendRange, Integer orderCount) {
        List<String> tags = new ArrayList<>();
        
        // 性别标签
        if (!gender.equals("未知")) tags.add("性别:" + gender);
        
        // 年龄段标签
        age.map(a -> "年龄:" + a).ifPresent(tags::add);
        
        // 消费等级标签
        if (spendRange.hasUpperBound()) {
            double upper = spendRange.upperEndpoint();
            if (upper >= 5000) tags.add("消费等级:高");
            else if (upper >= 1000) tags.add("消费等级:中");
            else tags.add("消费等级:低");
        }
        
        // 活跃度标签
        if (orderCount != null) {
            if (orderCount >= 10) tags.add("活跃度:高");
            else if (orderCount >= 3) tags.add("活跃度:中");
            else tags.add("活跃度:低");
        }
        
        return ImmutableList.copyOf(tags);
    }

    // ========== 标签引擎 ==========
    
    public static class TagEngine {
        // 标签 -> 用户列表
        private Multimap<String, String> tagIndex = HashMultimap.create();
        // 标签分布统计
        private Multiset<String> tagStats = HashMultiset.create();

        public void indexUser(String userId, List<String> tags) {
            for (String tag : tags) {
                tagIndex.put(tag, userId);
                tagStats.add(tag);
            }
        }

        public Set<String> findUsersByTag(String tag) {
            return new HashSet<>(tagIndex.get(tag));
        }

        public Set<String> findUsersByAllTags(List<String> tags) {
            if (tags.isEmpty()) return Collections.emptySet();
            
            Set<String> result = new HashSet<>(tagIndex.get(tags.get(0)));
            for (int i = 1; i < tags.size(); i++) {
                result.retainAll(tagIndex.get(tags.get(i)));
            }
            return result;
        }

        public Set<String> findUsersByAnyTag(List<String> tags) {
            Set<String> result = new HashSet<>();
            for (String tag : tags) {
                result.addAll(tagIndex.get(tag));
            }
            return result;
        }

        public List<Map.Entry<String, Integer>> getTopTags(int n) {
            return tagStats.entrySet().stream()
                .sorted((e1, e2) -> Integer.compare(e2.getCount(), e1.getCount()))
                .limit(n)
                .map(e -> new AbstractMap.SimpleEntry<>(e.getElement(), e.getCount()))
                .collect(Collectors.toList());
        }
    }

    // ========== 测试入口 ==========
    public static void main(String[] args) {
        UserProfileEngine engine = new UserProfileEngine();
        TagEngine tagEngine = new TagEngine();

        // 创建测试数据
        List<RawUser> rawUsers = Arrays.asList(
            createRawUser("U001", "张三", "138-1234-5678", "zhangsan@test.com", "25岁", 
                         "北京市 朝阳区", "M", 2500.0, 15),
            createRawUser("U002", "李四", "13987654321", null, "35", 
                         "上海市\t浦东新区", "女", 800.0, 5),
            createRawUser("U003", "王五", "invalid", "invalid-email", "150", 
                         "广州市", "未知", null, 0),
            createRawUser("U004", "赵六", null, "zhaoliu@example.com", null, 
                         "深圳市\u3000南山区", "MALE", 6000.0, 25)
        );

        System.out.println("=== 用户清洗 ===\n");
        List<CleanUser> cleanUsers = new ArrayList<>();
        
        for (RawUser raw : rawUsers) {
            try {
                CleanUser clean = engine.cleanUser(raw);
                cleanUsers.add(clean);
                tagEngine.indexUser(clean.getUserId(), clean.getTags());
                
                System.out.println("用户: " + clean.getName());
                System.out.println("  手机: " + clean.getPhone().or("未提供"));
                System.out.println("  邮箱: " + clean.getEmail().or("未提供"));
                System.out.println("  年龄: " + clean.getAge().orNull() + " -> " + clean.getAgeGroup());
                System.out.println("  性别: " + clean.getGender());
                System.out.println("  消费区间: " + clean.getSpendRange());
                System.out.println("  标签: " + clean.getTags());
                System.out.println();
            } catch (Exception e) {
                System.out.println("清洗失败: " + raw.userId + " - " + e.getMessage());
            }
        }

        System.out.println("=== 标签查询 ===\n");
        
        System.out.println("有 '性别:男' 标签的用户: " + tagEngine.findUsersByTag("性别:男"));
        System.out.println("有 '消费等级:高' 标签的用户: " + tagEngine.findUsersByTag("消费等级:高"));
        
        System.out.println("\n同时有 '性别:男' 和 '活跃度:高' 的用户: " + 
            tagEngine.findUsersByAllTags(Arrays.asList("性别:男", "活跃度:高")));

        System.out.println("\nTop 5 标签:");
        tagEngine.getTopTags(5).forEach(e -> 
            System.out.println("  " + e.getKey() + ": " + e.getValue() + " 人"));
    }

    private static RawUser createRawUser(String id, String name, String phone, 
                                          String email, String age, String address,
                                          String gender, Double spend, Integer orders) {
        RawUser user = new RawUser();
        user.userId = id;
        user.name = name;
        user.phone = phone;
        user.email = email;
        user.age = age;
        user.address = address;
        user.gender = gender;
        user.totalSpend = spend;
        user.orderCount = orders;
        return user;
    }
}
```

### 测试验证

```java
package com.example.guava.demo;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

import java.util.Arrays;
import java.util.Set;

public class UserProfileEngineTest {

    private UserProfileEngine engine = new UserProfileEngine();
    private UserProfileEngine.TagEngine tagEngine = new UserProfileEngine.TagEngine();

    @Test
    public void testCleanText() {
        UserProfileEngine.RawUser raw = new UserProfileEngine.RawUser();
        raw.userId = "U001";
        raw.name = "  张三  \t  ";
        raw.phone = "13812345678";
        raw.age = "25";
        raw.gender = "M";

        var clean = engine.cleanUser(raw);
        assertEquals("张三", clean.getName());  // 空白已清理
        assertEquals("男", clean.getGender());  // M -> 男
    }

    @Test
    public void testParsePhone() {
        UserProfileEngine.RawUser raw = new UserProfileEngine.RawUser();
        raw.userId = "U001";
        raw.name = "Test";
        raw.phone = "138-1234-5678";  // 带分隔符

        var clean = engine.cleanUser(raw);
        assertTrue(clean.getPhone().isPresent());
        assertEquals("13812345678", clean.getPhone().get());
    }

    @Test
    public void testInvalidPhone() {
        UserProfileEngine.RawUser raw = new UserProfileEngine.RawUser();
        raw.userId = "U001";
        raw.name = "Test";
        raw.phone = "invalid";

        var clean = engine.cleanUser(raw);
        assertFalse(clean.getPhone().isPresent());  // Invalid phone returns absent
    }

    @Test
    public void testAgeGroup() {
        UserProfileEngine.RawUser raw = new UserProfileEngine.RawUser();
        raw.userId = "U001";
        raw.name = "Test";
        raw.age = "25岁";  // 带中文

        var clean = engine.cleanUser(raw);
        assertEquals(25, clean.getAge().get().intValue());
        assertEquals("青年", clean.getAgeGroup());
    }

    @Test
    public void testSpendRange() {
        UserProfileEngine.RawUser raw = new UserProfileEngine.RawUser();
        raw.userId = "U001";
        raw.name = "Test";
        raw.totalSpend = 2500.0;

        var clean = engine.cleanUser(raw);
        assertTrue(clean.getSpendRange().contains(2500.0));
        assertTrue(clean.getTags().contains("消费等级:高"));
    }

    @Test
    public void testTagEngine() {
        tagEngine.indexUser("U001", Arrays.asList("标签A", "标签B"));
        tagEngine.indexUser("U002", Arrays.asList("标签A", "标签C"));
        tagEngine.indexUser("U003", Arrays.asList("标签B", "标签C"));

        Set<String> tagAUsers = tagEngine.findUsersByTag("标签A");
        assertEquals(2, tagAUsers.size());

        Set<String> intersection = tagEngine.findUsersByAllTags(Arrays.asList("标签A", "标签B"));
        assertEquals(1, intersection.size());
        assertTrue(intersection.contains("U001"));
    }

    @Test
    public void testTopTags() {
        tagEngine.indexUser("U001", Arrays.asList("A", "B"));
        tagEngine.indexUser("U002", Arrays.asList("A", "C"));
        tagEngine.indexUser("U003", Arrays.asList("A", "B", "C"));

        var topTags = tagEngine.getTopTags(2);
        assertEquals("A", topTags.get(0).getKey());  // A 出现 3 次
        assertEquals(3, topTags.get(0).getValue().intValue());
    }
}
```

### 可能遇到的坑及解决方法

| 坑点 | 现象 | 解决方法 |
|------|------|----------|
| 内存溢出 | 500万用户数据加载后 OOM | 分批处理，或使用流式处理 |
| 标签索引过大 | Multimap 占用内存过多 | 只存储用户 ID，不存储完整对象 |
| 清洗规则冲突 | 不同字段清洗逻辑相互影响 | 每个字段独立清洗，不共享状态 |
| Range 边界 | 边界值被分到错误区间 | 明确开闭区间，用单元测试验证 |

---

## 4 项目总结

### 技术栈回顾

本章综合运用了基础篇所有核心工具：

| 工具 | 用途 |
|------|------|
| `Optional` | 表达可选字段（phone, email, age） |
| `Preconditions` | 入口参数校验 |
| `Strings`/`CharMatcher` | 文本清洗标准化 |
| `ImmutableList/Map` | 标准化数据存储 |
| `Multimap` | 标签索引构建 |
| `Range` | 年龄段、消费区间分桶 |
| `Multiset` | 标签分布统计 |
| `Ordering` | 标签权重排序 |

### 优点

1. **快速交付**：无需搭建大数据环境，单机 1-2 天完成开发
2. **类型安全**：Guava 不可变集合保证数据不被意外修改
3. **代码可读性**：声明式 API 比手写逻辑清晰
4. **易于测试**：每个清洗步骤可独立单元测试

### 局限

1. **数据量限制**：单机内存限制（约 1000 万用户）
2. **实时性**：批处理模式，非实时
3. **扩展性**：无法水平扩展

### 演进路线

```
Guava 单机版 → 增加 Spark 分布式处理 → 引入 Flink 实时处理
     ↓                    ↓                      ↓
  MVP 验证           大数据量生产              实时标签
  （1-2天）          （1-2周搭建）            （1个月）
```

### 思考题答案（第 14 章思考题 1）

> **问题**：设计支持随机访问的 Top N 算法，结合 Ordering 和优先队列。

**答案**：使用 `PriorityQueue` 维护大小为 N 的堆：

```java
PriorityQueue<T> heap = new PriorityQueue<>(n, ordering);
for (T item : data) {
    if (heap.size() < n) {
        heap.offer(item);
    } else if (ordering.compare(item, heap.peek()) > 0) {
        heap.poll();
        heap.offer(item);
    }
}
return new ArrayList<>(heap);
```

时间复杂度 O(m log n)，适合 m 很大、n 很小的场景。

### 新思考题

1. 如果要将本章方案扩展支持 1000 万用户，有哪些内存优化手段？
2. 设计一个从 Guava 单机方案平滑迁移到 Spark 分布式方案的演进策略。

### 推广计划提示

**开发**：
- 小数据量快速验证用 Guava 单机方案
- 明确方案边界，做好监控告警
- 预留数据导出接口便于迁移

**测试**：
- 清洗规则单元测试覆盖
- 内存占用压测
- 数据准确性抽样验证

**运维**：
- 监控内存使用，设置水位告警
- 定期导出数据备份
- 准备好 Spark 集群作为后备方案

---

**基础篇总结**：

第 1-15 章覆盖了 Guava 基础工具的核心内容：
- **Basic Utilities**（Optional、Preconditions、Strings、CharMatcher、Primitives）
- **Collections**（Immutable、Lists/Sets/Maps、Multiset、Multimap、BiMap、Table、Range、Ordering）

掌握这些工具，可以在日常开发中：
1. 减少样板代码 30-50%
2. 提升代码可读性和安全性
3. 快速构建中小规模数据处理方案

下一章开始进入**中级篇**，探讨 Guava 在缓存、并发、I/O 等工程化场景的应用。
