# 第 7 章：ImmutableList/Set/Map 不可变集合入门

## 1 项目背景

在微服务架构的配置中心，工程师小钱遇到了一个线上故障。系统启动时加载了一份全局配置 Map，这份配置被多个线程共享读取。某天运维修改了一个配置值，测试时没问题，上线后却发现部分服务节点使用了旧值，部分使用了新值，导致集群行为不一致。

追查发现，问题出在配置的传递方式上。配置中心返回的 Map 被直接赋值给了一个静态变量，某个下游组件拿到后误以为可以修改，调用了 `put()` 方法。更糟糕的是，这份配置还被传给了第三方库，那个库在内部又做了一层缓存引用...

**业务场景**：配置管理、常量定义、共享数据、函数返回值等需要保证数据不被意外修改的场景。

**痛点放大**：
- **防御性复制**：每次返回集合都复制一份，性能损耗大。
- **unmodifiableXXX 的局限**：只能阻止直接修改，不能阻止间接修改（如遍历器删除）。
- **并发安全风险**：多线程环境下可变集合需要加锁。
- **误修改难以发现**：编译期无法阻止修改操作，运行时才抛异常。
- **文档约束失效**：通过注释说明"不要修改"，但无法强制执行。

如果没有真正的不可变集合，共享数据的安全性将无法保证。

**技术映射**：Guava 的 `ImmutableList`、`ImmutableSet`、`ImmutableMap` 提供了真正不可变的集合实现，创建后内容不可修改，天然线程安全，且可以安全地共享引用。

---

## 2 项目设计

**场景**：架构评审会，讨论配置中心设计方案。

---

**小胖**：（挠头）"我说，这配置被改的问题也太隐蔽了吧！我们明明用了 `Collections.unmodifiableMap`，怎么还是被改了？这不就跟食堂贴了'禁止插队'的告示，但没人看守一样吗？"

**小白**：（摇头）"`unmodifiableXXX` 只是**包装器**，它阻止的是对包装对象的修改，但底层集合仍然是可变的。如果有人保留了原始集合的引用，还是可以修改。"

**大师**：（在白板上画对比图）"你们看这两者的区别：

```java
// 方案 1：unmodifiableMap（包装器模式）
Map<String, String> original = new HashMap<>();
Map<String, String> unmodifiable = Collections.unmodifiableMap(original);
// original 仍然可变！
original.put("key", "value");  // 这会反映到 unmodifiable 中！

// 方案 2：ImmutableMap（真正不可变）
ImmutableMap<String, String> immutable = ImmutableMap.of("key", "value");
// 没有任何引用可以修改它，底层数据是独立的
```

**技术映射**：`ImmutableMap` 就像是刻在石头上的字——一旦刻好，没有任何手段可以修改，而 `unmodifiableMap` 只是给可变地图加了层玻璃罩，地图本身还是纸质的。"

**小胖**："那怎么创建这些不可变集合？"

**小白**："有多种方式：

```java
// 方式 1：of() 方法（最多 5 个元素）
ImmutableList<String> list = ImmutableList.of("a", "b", "c");
ImmutableMap<String, Integer> map = ImmutableMap.of("one", 1, "two", 2);

// 方式 2：Builder 模式（元素多时用）
ImmutableMap<String, String> map = ImmutableMap.<String, String>builder()
    .put("key1", "value1")
    .put("key2", "value2")
    .build();

// 方式 3：从现有集合复制
List<String> mutableList = new ArrayList<>();
ImmutableList<String> immutableList = ImmutableList.copyOf(mutableList);
```

**大师**："而且不可变集合有一些**特性**：
- **禁止 null**：Immutable 集合不允许存储 null 元素
- **顺序保持**：ImmutableList 和 ImmutableMap 保持插入顺序
- **线程安全**：创建后任何线程都可以安全读取，无需同步
- **内存效率**：某些实现（如 `ImmutableSet.of()` 空集合）是单例

**技术映射**：不可变集合把'不要修改'从约定变成物理限制，编译期和运行期都会阻止修改操作。"

**小胖**："那如果我需要修改怎么办？总得有个更新机制吧？"

**小白**："不可变集合的'修改'实际上是**创建新版本**：

```java
ImmutableList<String> original = ImmutableList.of("a", "b");
// 添加元素实际上是创建新集合
ImmutableList<String> withC = ImmutableList.<String>builder()
    .addAll(original)
    .add("c")
    .build();
// original 仍然是 ["a", "b"]，withC 是 ["a", "b", "c"]
```

这看起来低效，但 Guava 做了优化，而且不可变性带来的安全性通常值得这点开销。"

**大师**："还要注意**有序性**的问题。`ImmutableSet` 保持插入顺序（实际上是 `ImmutableLinkedHashSet` 的行为），`ImmutableMap` 也保持插入顺序。这在需要稳定遍历顺序时很有用。

**技术映射**：不可变集合的设计哲学是'用空间换安全'，在配置、常量、共享数据等场景下，这是值得的交易。"

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

### 分步实现：配置中心不可变配置管理

**步骤目标**：用 `ImmutableMap` 和 `ImmutableList` 构建一个线程安全的配置管理系统。

**代码实现**：

```java
package com.example.guava.demo;

import com.google.common.collect.ImmutableList;
import com.google.common.collect.ImmutableMap;
import com.google.common.collect.ImmutableSet;

import java.util.*;

/**
 * 配置中心 - 使用不可变集合保证配置安全
 */
public class ConfigurationCenter {

    // 系统默认配置（真正不可变，任何线程都可以安全读取）
    private static final ImmutableMap<String, String> DEFAULT_CONFIG = 
        ImmutableMap.<String, String>builder()
            .put("app.name", "MyApplication")
            .put("app.version", "1.0.0")
            .put("db.pool.maxSize", "20")
            .put("db.pool.minIdle", "5")
            .put("cache.ttl.seconds", "300")
            .put("feature.newUI.enabled", "false")
            .put("feature.beta.enabled", "false")
            .build();

    // 受保护的关键配置键（不允许覆盖）
    private static final ImmutableSet<String> PROTECTED_KEYS = 
        ImmutableSet.of("app.name", "app.version", "security.key");

    // 有效环境列表
    private static final ImmutableList<String> VALID_ENVIRONMENTS = 
        ImmutableList.of("dev", "test", "staging", "prod");

    // 运行时配置存储
    private volatile ImmutableMap<String, String> currentConfig;

    public ConfigurationCenter() {
        // 初始化时复制默认配置
        this.currentConfig = ImmutableMap.copyOf(DEFAULT_CONFIG);
    }

    /**
     * 获取配置值（线程安全）
     */
    public String get(String key) {
        return currentConfig.get(key);
    }

    /**
     * 获取配置值（带默认值）
     */
    public String get(String key, String defaultValue) {
        return currentConfig.getOrDefault(key, defaultValue);
    }

    /**
     * 获取所有配置（返回不可变视图）
     */
    public ImmutableMap<String, String> getAllConfig() {
        return currentConfig;
    }

    /**
     * 获取特定前缀的所有配置
     */
    public ImmutableMap<String, String> getConfigByPrefix(String prefix) {
        ImmutableMap.Builder<String, String> builder = ImmutableMap.builder();
        for (Map.Entry<String, String> entry : currentConfig.entrySet()) {
            if (entry.getKey().startsWith(prefix)) {
                builder.put(entry.getKey(), entry.getValue());
            }
        }
        return builder.build();
    }

    /**
     * 批量更新配置（原子操作）
     */
    public synchronized void updateConfig(Map<String, String> newConfigs) {
        // 检查受保护的键
        for (String key : newConfigs.keySet()) {
            if (PROTECTED_KEYS.contains(key)) {
                throw new IllegalArgumentException("Cannot modify protected key: " + key);
            }
        }

        // 创建新的不可变配置（原子替换）
        ImmutableMap.Builder<String, String> builder = ImmutableMap.builder();
        builder.putAll(currentConfig);
        builder.putAll(newConfigs);
        this.currentConfig = builder.build();
    }

    /**
     * 验证环境名称是否有效
     */
    public boolean isValidEnvironment(String env) {
        return VALID_ENVIRONMENTS.contains(env.toLowerCase());
    }

    /**
     * 获取有效环境列表（不可变）
     */
    public ImmutableList<String> getValidEnvironments() {
        return VALID_ENVIRONMENTS;
    }

    /**
     * 创建应用配置的副本（用于子系统）
     */
    public ImmutableMap<String, String> createConfigSnapshot() {
        // 即使调用者拿到 Map，也无法修改当前配置
        return ImmutableMap.copyOf(currentConfig);
    }

    // ========== 演示不可变特性 ==========
    public static void demonstrateImmutability() {
        System.out.println("=== 不可变集合特性演示 ===\n");

        // 1. 创建不可变列表
        ImmutableList<String> list = ImmutableList.of("a", "b", "c");
        System.out.println("原始列表: " + list);

        // 2. 尝试修改会抛出异常
        try {
            list.add("d");
        } catch (UnsupportedOperationException e) {
            System.out.println("✓ add() 被阻止: " + e.getMessage());
        }

        try {
            list.set(0, "x");
        } catch (UnsupportedOperationException e) {
            System.out.println("✓ set() 被阻止: " + e.getMessage());
        }

        try {
            list.remove(0);
        } catch (UnsupportedOperationException e) {
            System.out.println("✓ remove() 被阻止: " + e.getMessage());
        }

        // 3. "修改"实际上是创建新集合
        ImmutableList<String> newList = ImmutableList.<String>builder()
            .addAll(list)
            .add("d")
            .build();
        System.out.println("原始列表（未变）: " + list);
        System.out.println("新列表: " + newList);

        // 4. 禁止 null 元素
        try {
            ImmutableList.of("a", null, "b");
        } catch (NullPointerException e) {
            System.out.println("✓ null 元素被阻止");
        }

        // 5. ImmutableMap 保持插入顺序
        ImmutableMap<String, Integer> map = ImmutableMap.of(
            "one", 1, "two", 2, "three", 3, "four", 4
        );
        System.out.println("\nMap 遍历顺序: " + map.keySet());
    }

    public static void main(String[] args) {
        demonstrateImmutability();

        System.out.println("\n=== 配置中心演示 ===\n");

        ConfigurationCenter config = new ConfigurationCenter();

        // 读取配置
        System.out.println("App Name: " + config.get("app.name"));
        System.out.println("DB Pool Max: " + config.get("db.pool.maxSize"));

        // 获取所有配置
        System.out.println("\n所有配置:");
        config.getAllConfig().forEach((k, v) -> 
            System.out.println("  " + k + " = " + v)
        );

        // 获取特定前缀配置
        System.out.println("\nDB 相关配置:");
        config.getConfigByPrefix("db.").forEach((k, v) -> 
            System.out.println("  " + k + " = " + v)
        );

        // 尝试修改受保护配置
        try {
            Map<String, String> updates = new HashMap<>();
            updates.put("app.name", "Hacked");
            config.updateConfig(updates);
        } catch (IllegalArgumentException e) {
            System.out.println("\n✓ 阻止修改受保护配置: " + e.getMessage());
        }

        // 正常更新
        Map<String, String> updates = new HashMap<>();
        updates.put("cache.ttl.seconds", "600");
        config.updateConfig(updates);
        System.out.println("\n更新后缓存 TTL: " + config.get("cache.ttl.seconds"));
    }
}
```

### 测试验证

```java
package com.example.guava.demo;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

import java.util.HashMap;
import java.util.Map;

public class ConfigurationCenterTest {

    @Test
    public void testGetExistingConfig() {
        ConfigurationCenter config = new ConfigurationCenter();
        assertEquals("MyApplication", config.get("app.name"));
        assertEquals("20", config.get("db.pool.maxSize"));
    }

    @Test
    public void testGetWithDefault() {
        ConfigurationCenter config = new ConfigurationCenter();
        assertEquals("default", config.get("non.existent", "default"));
    }

    @Test
    public void testGetAllConfigIsImmutable() {
        ConfigurationCenter config = new ConfigurationCenter();
        Map<String, String> allConfig = config.getAllConfig();
        
        assertThrows(UnsupportedOperationException.class, () -> {
            allConfig.put("new.key", "value");
        });
    }

    @Test
    public void testUpdateConfig() {
        ConfigurationCenter config = new ConfigurationCenter();
        
        Map<String, String> updates = new HashMap<>();
        updates.put("cache.ttl.seconds", "600");
        
        config.updateConfig(updates);
        assertEquals("600", config.get("cache.ttl.seconds"));
    }

    @Test
    public void testProtectedKeysCannotBeModified() {
        ConfigurationCenter config = new ConfigurationCenter();
        
        Map<String, String> updates = new HashMap<>();
        updates.put("app.name", "NewName");
        
        assertThrows(IllegalArgumentException.class, () -> {
            config.updateConfig(updates);
        });
    }

    @Test
    public void testValidEnvironment() {
        ConfigurationCenter config = new ConfigurationCenter();
        assertTrue(config.isValidEnvironment("prod"));
        assertTrue(config.isValidEnvironment("PROD"));
        assertFalse(config.isValidEnvironment("invalid"));
    }

    @Test
    public void testConfigSnapshot() {
        ConfigurationCenter config = new ConfigurationCenter();
        Map<String, String> snapshot = config.createConfigSnapshot();
        
        // 快照也是不可变的
        assertThrows(UnsupportedOperationException.class, () -> {
            snapshot.put("key", "value");
        });
    }

    @Test
    public void testImmutableListOf() {
        assertThrows(NullPointerException.class, () -> {
            com.google.common.collect.ImmutableList.of("a", null, "b");
        });
    }

    @Test
    public void testImmutableMapOrderPreserved() {
        com.google.common.collect.ImmutableMap<String, Integer> map = 
            com.google.common.collect.ImmutableMap.of(
                "first", 1, "second", 2, "third", 3
            );
        
        String[] keys = map.keySet().toArray(new String[0]);
        assertEquals("first", keys[0]);
        assertEquals("second", keys[1]);
        assertEquals("third", keys[2]);
    }
}
```

### 可能遇到的坑及解决方法

| 坑点 | 现象 | 解决方法 |
|------|------|----------|
| 尝试存储 null | 抛出 NullPointerException | 使用 `Optional` 或空值对象替代 |
| 误以为 copyOf 是浅拷贝 | 集合元素仍可修改 | Immutable 集合只保证结构不可变，元素需自身不可变 |
| 大量更新导致性能问题 | 频繁创建新集合 | 考虑使用 Mutable 集合批量修改后再 copyOf |
| 遍历器不支持 remove | 抛出异常 | 无需 remove，过滤用新集合 |

---

## 4 项目总结

### 优缺点对比

| 维度 | Immutable Collections | unmodifiableXXX | 普通 Mutable |
|------|-----------------------|-----------------|--------------|
| 真正不可变 | ★★★★★ 物理不可变 | ★★ 包装器 | ★ 完全可变 |
| 线程安全 | ★★★★★ 天然安全 | ★★ 有条件安全 | ★ 需同步 |
| 性能 | ★★★★ 读优化 | ★★★★ 代理开销 | ★★★★★ 最优 |
| null 支持 | ★ 不支持 | ★★★★★ 支持 | ★★★★★ 支持 |
| 内存效率 | ★★★★ 单例优化 | ★★★★ 包装器 | ★★★★★ 最优 |

### 适用场景

1. **配置管理**：全局配置、常量定义
2. **共享数据**：多线程共享的只读数据
3. **函数返回值**：明确表示"不要修改"
4. **缓存键值**：Map 的 key 用 Immutable 保证 hashCode 稳定
5. **防御性编程**：对外暴露的内部数据副本

### 不适用场景

1. **频繁修改的场景**：性能开销大
2. **需要 null 元素**：Immutable 集合不允许 null
3. **内存极度敏感**：Immutable 有一定对象开销
4. **超大集合**：Builder 构建时有内存峰值

### 生产踩坑案例

**案例 1：存储了可变对象**
```java
// 坑：虽然 Map 不可变，但 Date 对象可变
Map<String, Date> map = ImmutableMap.of("key", new Date());
map.get("key").setTime(0);  // 修改了！
```
解决：存储 `java.time.Instant` 或 `LocalDateTime`（不可变）。

**案例 2：尝试用 null 作为值**
```java
// 坑：ImmutableMap 不支持 null 值
ImmutableMap.of("key", null);  // NPE
```
解决：用空字符串或 `Optional.absent()` 占位。

**案例 3：频繁小量更新**
```java
// 坑：频繁构建新集合性能差
for (String item : items) {
    list = ImmutableList.<String>builder().addAll(list).add(item).build();
}
```
解决：批量收集后用 `ImmutableList.copyOf()`。

### 思考题答案（第 6 章思考题 1）

> **问题**：`CharMatcher` 和正则表达式的性能差异在什么量级？

**答案**：简单字符匹配场景下，`CharMatcher` 通常比正则快 2-10 倍，因为：
1. `CharMatcher` 是预编译的字符判断，无解析开销
2. 正则引擎有回溯等复杂逻辑
3. `CharMatcher` 针对字符操作做了特化优化

验证方法：用 JMH 基准测试对比 `CharMatcher.digit().retainFrom()` 和 `replaceAll("\\D", "")`。

### 新思考题

1. `ImmutableMap.Builder` 在构建时遇到重复 key 会如何处理？如何自定义这种行为？
2. 设计一个配置热更新方案：如何在保持不可变集合安全性的同时支持高效更新？

### 推广计划提示

**开发**：
- 配置、常量类统一使用 Immutable 集合
- Code Review 检查共享可变集合

**测试**：
- 验证不可变性：尝试修改应抛异常
- 并发测试：多线程读取无需同步

**运维**：
- 监控配置更新频率
- 配置变更审计日志
