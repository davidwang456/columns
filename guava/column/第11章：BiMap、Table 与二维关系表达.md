# 第 11 章：BiMap、Table 与二维关系表达

## 1 项目背景

在国际化的电商平台中，工程师小吴负责开发一个商品编码映射系统。系统需要维护不同渠道的商品编码之间的双向映射——比如商家的内部 SKU 码与平台的商品 ID 的对应关系。他最初用两个 `Map` 来实现：一个 Map 存 SKU 到平台 ID 的映射，另一个存平台 ID 到 SKU 的反向映射。

但很快就出了问题：当更新映射关系时，忘记同步更新反向 Map，导致两边数据不一致。更严重的是，当需要检查是否有重复映射时，代码变得非常复杂。

在另一个场景，价格计算引擎需要根据地区和时间两个维度来查询价格系数。用嵌套 Map（`Map<Region, Map<Time, Price>>`）实现后，代码可读性极差，而且容易出现空指针异常。

**业务场景**：双向映射、二维表格、坐标定位等需要表达特殊关系的场景。

**痛点放大**：
- **双向映射维护困难**：两个 Map 需要手动同步，容易不一致。
- **重复键检测复杂**：需要遍历 Map 检查 value 是否已存在。
- **嵌套 Map 可读性差**：`map.get(r).get(c)` 容易 NPE。
- **性能隐患**：双向查询需要两个 Map，内存占用高。
- **类型安全**：嵌套 Map 的泛型声明冗长且容易出错。

如果没有专门的工具来表达这些特殊关系，代码将难以维护。

**技术映射**：Guava 的 `BiMap`（双向 Map）和 `Table`（二维表）提供了专门的数据结构，分别解决双向映射和二维关系表达的问题。

---

## 2 项目设计

**场景**：商品系统架构评审会，讨论编码映射方案。

---

**小胖**：（看着两个 Map 的同步代码）"我说，这双 Map 维护也太痛苦了吧！我就改了一个映射关系，得同时改两个 Map，漏了就数据不一致。这不就跟食堂既要管点菜又要管收银，两边对不上账一样吗？"

**小白**：（点头）"而且检查 value 是否重复也很麻烦。你要遍历整个 Map 的 values，或者维护另一个 Set。"

**大师**：（在白板上写对比）"Guava 的 `BiMap` 就是专门解决双向映射的。看这段对比：

```java
// 传统写法：两个 Map
Map<String, Integer> skuToId = new HashMap<>();
Map<Integer, String> idToSku = new HashMap<>();

// 添加映射（要同步两个 Map）
skuToId.put("SKU001", 1001);
idToSku.put(1001, "SKU001");  // 容易漏！

// 反向查找
String sku = idToSku.get(1001);

// Guava 写法：BiMap
BiMap<String, Integer> skuBiMap = HashBiMap.create();

// 添加映射（只操作一次）
skuBiMap.put("SKU001", 1001);

// 反向查找
String sku = skuBiMap.inverse().get(1001);
```

**技术映射**：`BiMap` 就像是双向门——你可以从 SKU 走到平台 ID，也可以从平台 ID 走回 SKU，而且两边的门是联动的，开一边另一边自动同步。"

**小胖**："那 `BiMap` 能保证两边一致吗？"

**小白**："当然！`BiMap` 强制要求**value 唯一**，这和普通 Map 不同：

```java
BiMap<String, Integer> biMap = HashBiMap.create();
biMap.put("A", 1);
biMap.put("B", 2);

// 下面这行会抛 IllegalArgumentException！
biMap.put("C", 1);  // 错误！1 已经被 A 使用了

// 正确做法：先强制替换
biMap.forcePut("C", 1);  // A->1 被移除，C->1 建立
```

这保证了反向查找时不会有歧义。"

**大师**："再看 `Table`，它是专门解决二维关系的。传统嵌套 Map 的写法：

```java
// 传统：嵌套 Map
Map<String, Map<String, Double>> priceTable = new HashMap<>();
priceTable.computeIfAbsent("北京", k -> new HashMap<>()).put("上午", 1.2);
Double price = priceTable.get("北京").get("上午");  // 可能 NPE！

// Guava：Table
Table<String, String, Double> priceTable = HashBasedTable.create();
priceTable.put("北京", "上午", 1.2);

// 获取（不会 NPE，不存在返回 null）
Double price = priceTable.get("北京", "上午");

// 获取行/列
Map<String, Double> beijingPrices = priceTable.row("北京");
Map<String, Double> morningPrices = priceTable.column("上午");
```

**技术映射**：`Table` 就像是 Excel 表格——你可以按行查、按列查、按单元格坐标查，不用自己维护嵌套结构。"

**小胖**："`Table` 还能做什么操作？"

**小白**："功能很丰富：

```java
// 获取所有单元格
Set<Table.Cell<String, String, Double>> cells = priceTable.cellSet();

// 遍历
for (Table.Cell<String, String, Double> cell : cells) {
    System.out.println(cell.getRowKey() + "," + cell.getColumnKey() + " = " + cell.getValue());
}

// 行视图（返回 Map<R, Map<C, V>>）
Map<String, Map<String, Double>> rowMap = priceTable.rowMap();

// 列视图
Map<String, Map<String, Double>> columnMap = priceTable.columnMap();

// 转置（行列互换）
Table<String, String, Double> transposed = Tables.transpose(priceTable);
```

**大师**："`Table` 也有多个实现：
- `HashBasedTable`：基于 HashMap，快速查找
- `TreeBasedTable`：基于 TreeMap，行列有序
- `ArrayTable`：基于二维数组，固定大小，内存高效
- `ImmutableTable`：不可变版本

选择哪种取决于你的数据特性。

**技术映射**：`BiMap` 和 `Table` 都是针对特定关系模式的特化数据结构，它们用约束换取便利，用封装换取简洁。"

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

### 分步实现：编码映射与价格矩阵

**步骤目标**：用 `BiMap` 和 `Table` 构建编码映射系统和价格系数表。

**代码实现**：

```java
package com.example.guava.demo;

import com.google.common.base.Preconditions;
import com.google.common.base.Strings;
import com.google.common.collect.*;

import java.util.*;
import java.util.stream.Collectors;

/**
 * 编码映射与价格矩阵 - 使用 BiMap 和 Table
 */
public class MappingAndPricingSystem {

    // ========== BiMap：SKU 与平台 ID 双向映射 ==========
    private BiMap<String, String> skuToPlatformId = HashBiMap.create();
    
    // ========== Table：地区-时段价格系数矩阵 ==========
    private Table<String, String, Double> priceCoefficientTable = HashBasedTable.create();

    /**
     * 添加 SKU 映射
     */
    public void addSkuMapping(String sku, String platformId) {
        Preconditions.checkArgument(!Strings.isNullOrEmpty(sku), "SKU required");
        Preconditions.checkArgument(!Strings.isNullOrEmpty(platformId), "Platform ID required");
        
        // BiMap 会自动保证 value 唯一
        skuToPlatformId.put(sku, platformId);
    }

    /**
     * 强制替换映射（即使 platformId 已存在）
     */
    public void forceSkuMapping(String sku, String platformId) {
        skuToPlatformId.forcePut(sku, platformId);
    }

    /**
     * 根据 SKU 查平台 ID
     */
    public String getPlatformId(String sku) {
        return skuToPlatformId.get(sku);
    }

    /**
     * 根据平台 ID 查 SKU（反向查找）
     */
    public String getSkuByPlatformId(String platformId) {
        return skuToPlatformId.inverse().get(platformId);
    }

    /**
     * 检查映射是否存在
     */
    public boolean containsMapping(String sku, String platformId) {
        return skuToPlatformId.containsEntry(sku, platformId);
    }

    /**
     * 获取所有 SKU
     */
    public Set<String> getAllSkus() {
        return skuToPlatformId.keySet();
    }

    /**
     * 获取所有平台 ID
     */
    public Set<String> getAllPlatformIds() {
        return skuToPlatformId.values();
    }

    /**
     * 移除映射
     */
    public void removeSkuMapping(String sku) {
        skuToPlatformId.remove(sku);
    }

    // ========== Table：价格系数管理 ==========

    /**
     * 设置价格系数
     */
    public void setPriceCoefficient(String region, String timeSlot, double coefficient) {
        Preconditions.checkArgument(coefficient > 0, "Coefficient must be positive");
        priceCoefficientTable.put(region, timeSlot, coefficient);
    }

    /**
     * 获取价格系数
     */
    public Double getPriceCoefficient(String region, String timeSlot) {
        return priceCoefficientTable.get(region, timeSlot);
    }

    /**
     * 获取价格系数（带默认值）
     */
    public double getPriceCoefficient(String region, String timeSlot, double defaultValue) {
        Double coeff = priceCoefficientTable.get(region, timeSlot);
        return coeff != null ? coeff : defaultValue;
    }

    /**
     * 获取某地区的所有时段系数
     */
    public Map<String, Double> getRegionCoefficients(String region) {
        return priceCoefficientTable.row(region);
    }

    /**
     * 获取某时段的所有地区系数
     */
    public Map<String, Double> getTimeSlotCoefficients(String timeSlot) {
        return priceCoefficientTable.column(timeSlot);
    }

    /**
     * 计算最终价格
     */
    public double calculateFinalPrice(double basePrice, String region, String timeSlot) {
        double coefficient = getPriceCoefficient(region, timeSlot, 1.0);
        return basePrice * coefficient;
    }

    /**
     * 找出系数最高的地区-时段组合
     */
    public Table.Cell<String, String, Double> findHighestCoefficient() {
        return priceCoefficientTable.cellSet().stream()
            .max(Comparator.comparing(Table.Cell::getValue))
            .orElse(null);
    }

    /**
     * 获取所有配置的地区
     */
    public Set<String> getConfiguredRegions() {
        return priceCoefficientTable.rowKeySet();
    }

    /**
     * 获取所有配置的时段
     */
    public Set<String> getConfiguredTimeSlots() {
        return priceCoefficientTable.columnKeySet();
    }

    /**
     * 创建价格系数的只读视图
     */
    public Table<String, String, Double> getCoefficientView() {
        return Tables.unmodifiableTable(priceCoefficientTable);
    }

    // ========== 测试入口 ==========
    public static void main(String[] args) {
        MappingAndPricingSystem system = new MappingAndPricingSystem();

        // 测试 BiMap
        System.out.println("=== BiMap 测试 ===");
        system.addSkuMapping("SKU001", "PID_1001");
        system.addSkuMapping("SKU002", "PID_1002");
        system.addSkuMapping("SKU003", "PID_1003");

        System.out.println("SKU001 -> " + system.getPlatformId("SKU001"));
        System.out.println("PID_1002 <- " + system.getSkuByPlatformId("PID_1002"));
        
        // 测试 value 唯一约束
        try {
            system.addSkuMapping("SKU999", "PID_1001");  // 已存在
        } catch (IllegalArgumentException e) {
            System.out.println("✓ 阻止重复 value: " + e.getMessage());
        }

        // 测试 Table
        System.out.println("\n=== Table 测试 ===");
        system.setPriceCoefficient("北京", "上午", 1.0);
        system.setPriceCoefficient("北京", "下午", 1.2);
        system.setPriceCoefficient("上海", "上午", 1.1);
        system.setPriceCoefficient("上海", "下午", 1.3);
        system.setPriceCoefficient("广州", "上午", 0.9);
        system.setPriceCoefficient("广州", "下午", 1.0);

        System.out.println("北京下午系数: " + system.getPriceCoefficient("北京", "下午"));
        System.out.println("所有地区: " + system.getConfiguredRegions());
        System.out.println("所有时段: " + system.getConfiguredTimeSlots());

        System.out.println("\n北京地区所有系数:");
        system.getRegionCoefficients("北京").forEach((k, v) -> 
            System.out.println("  " + k + " = " + v)
        );

        System.out.println("\n下午时段所有系数:");
        system.getTimeSlotCoefficients("下午").forEach((k, v) -> 
            System.out.println("  " + k + " = " + v)
        );

        System.out.println("\n最高系数:");
        Table.Cell<String, String, Double> highest = system.findHighestCoefficient();
        System.out.println("  " + highest.getRowKey() + " " + highest.getColumnKey() + 
            " = " + highest.getValue());

        System.out.println("\n价格计算:");
        double finalPrice = system.calculateFinalPrice(100.0, "上海", "下午");
        System.out.println("  基础价格 100，上海下午 = " + finalPrice);
    }
}
```

### 测试验证

```java
package com.example.guava.demo;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

import java.util.Map;
import java.util.Set;

public class MappingAndPricingSystemTest {

    private MappingAndPricingSystem system;

    @BeforeEach
    public void setUp() {
        system = new MappingAndPricingSystem();
    }

    @Test
    public void testBiMapPutAndGet() {
        system.addSkuMapping("SKU001", "PID1001");
        assertEquals("PID1001", system.getPlatformId("SKU001"));
    }

    @Test
    public void testBiMapInverse() {
        system.addSkuMapping("SKU001", "PID1001");
        assertEquals("SKU001", system.getSkuByPlatformId("PID1001"));
    }

    @Test
    public void testBiMapDuplicateValue() {
        system.addSkuMapping("SKU001", "PID1001");
        assertThrows(IllegalArgumentException.class, () -> {
            system.addSkuMapping("SKU002", "PID1001");  // 重复 value
        });
    }

    @Test
    public void testBiMapForcePut() {
        system.addSkuMapping("SKU001", "PID1001");
        system.forceSkuMapping("SKU002", "PID1001");  // 强制替换
        
        assertNull(system.getPlatformId("SKU001"));  // 原映射被移除
        assertEquals("PID1001", system.getPlatformId("SKU002"));
    }

    @Test
    public void testTablePutAndGet() {
        system.setPriceCoefficient("北京", "上午", 1.2);
        assertEquals(1.2, system.getPriceCoefficient("北京", "上午"));
    }

    @Test
    public void testTableGetWithDefault() {
        assertEquals(1.0, system.getPriceCoefficient("不存在", "上午", 1.0));
    }

    @Test
    public void testTableRowView() {
        system.setPriceCoefficient("北京", "上午", 1.0);
        system.setPriceCoefficient("北京", "下午", 1.2);
        
        Map<String, Double> row = system.getRegionCoefficients("北京");
        assertEquals(2, row.size());
        assertEquals(1.0, row.get("上午"));
    }

    @Test
    public void testTableColumnView() {
        system.setPriceCoefficient("北京", "上午", 1.0);
        system.setPriceCoefficient("上海", "上午", 1.1);
        
        Map<String, Double> column = system.getTimeSlotCoefficients("上午");
        assertEquals(2, column.size());
    }

    @Test
    public void testTableCalculatePrice() {
        system.setPriceCoefficient("北京", "上午", 1.2);
        double finalPrice = system.calculateFinalPrice(100.0, "北京", "上午");
        assertEquals(120.0, finalPrice, 0.01);
    }

    @Test
    public void testTableFindHighest() {
        system.setPriceCoefficient("A", "X", 1.0);
        system.setPriceCoefficient("B", "Y", 2.0);
        system.setPriceCoefficient("C", "Z", 1.5);
        
        var highest = system.findHighestCoefficient();
        assertEquals("B", highest.getRowKey());
        assertEquals("Y", highest.getColumnKey());
        assertEquals(2.0, highest.getValue());
    }

    @Test
    public void testTableGetRegions() {
        system.setPriceCoefficient("北京", "上午", 1.0);
        system.setPriceCoefficient("上海", "上午", 1.1);
        
        Set<String> regions = system.getConfiguredRegions();
        assertTrue(regions.contains("北京"));
        assertTrue(regions.contains("上海"));
    }
}
```

### 可能遇到的坑及解决方法

| 坑点 | 现象 | 解决方法 |
|------|------|----------|
| `BiMap` value 重复 | 抛 IllegalArgumentException | 用 `forcePut` 或先检查 `containsValue` |
| `BiMap.inverse()` 修改 | 修改 inverse 影响原 BiMap | 这是正常行为，注意理解双向性 |
| `Table` 返回视图修改 | 修改 row/column 视图影响原 Table | 需要隔离时复制 |
| `ArrayTable` 越界 | 固定大小限制 | 预估好大小或使用 `HashBasedTable` |

---

## 4 项目总结

### 优缺点对比

| 维度 | BiMap | 双 Map 方案 | Table | 嵌套 Map |
|------|-------|-------------|-------|----------|
| 一致性保证 | ★★★★★ 自动 | ★★ 需手动同步 | ★★★★★ 自动 | ★★★★★ 单 Map |
| API 简洁 | ★★★★★ 简洁 | ★★ 样板代码 | ★★★★★ 简洁 | ★★ 易 NPE |
| 内存占用 | ★★★★ 两份引用 | ★★ 两份数据 | ★★★★ 优化 | ★★★★★ 一份 |
| 查询性能 | ★★★★ O(1) | ★★★★ O(1) | ★★★★ O(1) | ★★★★ O(1) |
| 特殊操作 | ★★★★ inverse | ★★★★★ 完全可控 | ★★★★ 行列视图 | ★★★★★ 完全可控 |

### 适用场景

**BiMap**：
1. SKU 与平台 ID 映射
2. 中英文名称互查
3. 简繁体字转换
4. 编码与名称映射

**Table**：
1. 地区-时段价格矩阵
2. 用户-功能权限表
3. 产品-渠道库存表
4. 行列表格数据

### 不适用场景

**BiMap**：
1. 多对多关系（用 Multimap）
2. 一对多关系
3. 需要存储额外信息（用自定义对象）

**Table**：
1. 超高维数据（三维+）
2. 稀疏度极高的数据（用 Map 存储非空单元格）
3. 需要复杂查询（用数据库）

### 生产踩坑案例

**案例 1：BiMap 修改 inverse 意外影响原映射**
```java
BiMap<String, Integer> biMap = HashBiMap.create();
biMap.put("A", 1);
biMap.inverse().put(2, "B");  // 相当于 biMap.put("B", 2)！
```
解决：理解 inverse() 返回的是双向视图，不是独立副本。

**案例 2：Table row 视图修改影响原表**
```java
Map<String, Double> row = table.row("北京");
row.clear();  // table 中北京行被清空！
```
解决：需要只读时用 `ImmutableMap.copyOf(table.row(key))`。

**案例 3：ArrayTable 大小固定**
```java
ArrayTable<String, String, Double> table = 
    ArrayTable.create(Arrays.asList("A", "B"), Arrays.asList("X", "Y"));
table.put("C", "Z", 1.0);  // IllegalArgumentException！
```
解决：动态数据用 `HashBasedTable`。

### 思考题答案（第 10 章思考题 1）

> **问题**：`Multimap` 和数据库多对多关系表相比的优势和局限？

**答案**：
**优势**：
- 内存操作，查询延迟极低
- API 简洁，代码可读性好
- 支持视图操作（如 invertFrom）

**局限**：
- 无持久化，重启数据丢失
- 无 ACID 保证
- 数据量大时内存受限
- 不支持复杂 SQL 查询

### 新思考题

1. `BiMap` 的 `forcePut` 在什么场景下必须使用？使用时需要注意什么？
2. 设计一个三维数据结构来存储地区-时段-渠道的价格系数，如何扩展 Table 或使用其他方案？

### 推广计划提示

**开发**：
- 双向映射用 BiMap，二维关系用 Table
- Code Review 检查双 Map 和嵌套 Map 用法

**测试**：
- 验证 BiMap value 唯一性约束
- 测试 Table 行列视图的修改行为

**运维**：
- 监控 BiMap/Table 内存占用
- 大数据量时考虑持久化方案
