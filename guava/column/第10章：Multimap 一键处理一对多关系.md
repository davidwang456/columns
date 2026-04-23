# 第 10 章：Multimap 一键处理一对多关系

## 1 项目背景

在企业级权限管理系统中，工程师小周遇到了一个棘手的数据结构设计问题。系统需要维护用户和角色的关系——一个用户可以有多个角色，一个角色也可以分配给多个用户。他最初用 `Map<String, List<String>>` 来实现这个映射，但代码很快变得臃肿——每次添加关系时都要判断 key 是否存在，不存在要创建 ArrayList，然后再 add。

更头疼的是权限检查逻辑。要判断用户是否有某个角色，需要先用 `get()` 拿到 List，判空后再用 `contains()` 检查。这些样板代码散落在系统的十几个模块中，有的处理了空值，有的没有。

**业务场景**：用户-角色、订单-商品、标签-文章等一对多关系建模。

**痛点放大**：
- **样板代码重复**：每次都要处理"key 不存在则创建列表"的逻辑。
- **空值处理复杂**：Map 返回的 List 可能是 null，需要防御性判断。
- **移除操作繁琐**：要移除一对多关系中的一对，需要手动操作 List。
- **视图转换困难**：反向查询（如哪些用户有某个角色）需要额外构建索引。
- **内存效率**：每个 key 对应一个 List，但大多 key 只有 1-2 个值，ArrayList 的默认容量浪费内存。

如果没有专门的一对多映射抽象，这类任务的代码将难以维护。

**技术映射**：Guava 的 `Multimap` 提供了专门的一对多映射抽象，自动处理列表的创建和空值判断，支持 put/get/remove 等操作的自动展开。

---

## 2 项目设计

**场景**：权限系统架构评审会，讨论用户-角色关系设计。

---

**小胖**：（看着 Map<List> 代码）"我说，这一对多关系也太啰嗦了吧！我就想给某个用户加个角色，写了五六行判断 key、创建列表、add 的代码。这不就跟食堂打饭，明明有自助取餐，偏要排队等阿姨一勺一勺盛？"

**小白**：（叹气）"而且每个人写的都不一样。有的用 `computeIfAbsent`，有的用 `getOrDefault`，还有的手写 if。代码评审时根本没法统一标准。"

**大师**：（在白板上写对比）"Guava 的 `Multimap` 就是专门解决这个问题的。看这段对比：

```java
// 传统写法：Map<String, List<String>>
Map<String, List<String>> userRoles = new HashMap<>();

// 添加角色
userRoles.computeIfAbsent("user1", k -> new ArrayList<>()).add("ADMIN");

// 获取角色（要处理 null）
List<String> roles = userRoles.get("user1");
if (roles != null && roles.contains("ADMIN")) {
    // ...
}

// Guava 写法：Multimap
Multimap<String, String> userRoles = ArrayListMultimap.create();

// 添加角色
userRoles.put("user1", "ADMIN");  // 自动处理列表创建！

// 获取角色（永不为 null）
if (userRoles.get("user1").contains("ADMIN")) {
    // ...
}
```

**技术映射**：`Multimap` 就像是自动分格的收纳盒——你只管往格子里放东西（put），它自动帮你创建格子，不需要你先看一眼有没有格子再放。"

**小胖**："那 `Multimap` 返回的集合是什么？可以修改吗？"

**小白**："`get(key)` 返回的是**视图**，对它的修改会反映到原 `Multimap` 中：

```java
List<String> roles = userRoles.get("user1");
roles.add("USER");  // 相当于 userRoles.put("user1", "USER")
roles.remove("ADMIN");  // 相当于 userRoles.remove("user1", "ADMIN")
```

但注意：当最后一个值被移除时，key 会自动从 Multimap 中消失。"

**大师**："`Multimap` 还提供了便捷的批量操作：

```java
// 批量添加
userRoles.putAll("user1", Arrays.asList("ADMIN", "USER", "MANAGER"));

// 替换某用户的所有角色
userRoles.replaceValues("user1", Arrays.asList("GUEST"));

// 获取所有键的视图
Collection<String> allUsers = userRoles.keys();  // 含重复
Set<String> uniqueUsers = userRoles.keySet();  // 去重

// 获取所有值的视图
Collection<String> allRoles = userRoles.values();  // 扁平化所有值
```

**技术映射**：`Multimap` 把一对多关系的'列表管理'细节封装起来，让你专注于关系本身的操作，而不是列表的创建和维护。"

**小胖**："那如果我要做反向查询，比如找出所有有 ADMIN 角色的用户？"

**小白**："用 `Multimaps` 工具类的 `invertFrom`：

```java
// 原映射：用户 -> 角色
Multimap<String, String> userToRoles = ArrayListMultimap.create();

// 反向映射：角色 -> 用户
Multimap<String, String> roleToUsers = Multimaps.invertFrom(
    userToRoles, 
    HashMultimap.create()  // 使用 Set 去重
);

// 现在可以查出所有有 ADMIN 角色的用户
Set<String> admins = roleToUsers.get("ADMIN");
```

**大师**："`Multimap` 有多个实现，选择合适的很重要：
- `ArrayListMultimap`：值用 ArrayList，允许重复，保持插入顺序
- `HashMultimap`：值用 HashSet，去重，无序
- `LinkedListMultimap`：保持键和值的插入顺序
- `TreeMultimap`：键和值都有序
- `ImmutableMultimap`：不可变版本

**技术映射**：选择 `Multimap` 实现就像选择容器——要允许重复用 ListMultimap，要去重用 SetMultimap，要排序用 TreeMultimap。"

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

### 分步实现：权限管理与标签系统

**步骤目标**：用 `Multimap` 构建权限管理和文章标签系统。

**代码实现**：

```java
package com.example.guava.demo;

import com.google.common.base.Preconditions;
import com.google.common.base.Strings;
import com.google.common.collect.*;

import java.util.*;
import java.util.stream.Collectors;

/**
 * 权限管理与标签系统 - 使用 Multimap
 */
public class AuthorizationManager {

    // 用户 -> 角色（一个用户可有多个角色）
    private Multimap<String, String> userRoles = ArrayListMultimap.create();
    
    // 角色 -> 权限（一个角色可有多个权限）
    private Multimap<String, String> rolePermissions = HashMultimap.create();
    
    // 文章 -> 标签（一篇文章可有多个标签）
    private Multimap<String, String> articleTags = LinkedHashMultimap.create();

    // ========== 用户-角色管理 ==========
    
    /**
     * 给用户分配角色
     */
    public void assignRole(String userId, String role) {
        Preconditions.checkArgument(!Strings.isNullOrEmpty(userId), "User ID required");
        Preconditions.checkArgument(!Strings.isNullOrEmpty(role), "Role required");
        userRoles.put(userId, role);
    }

    /**
     * 批量分配角色
     */
    public void assignRoles(String userId, Collection<String> roles) {
        userRoles.putAll(userId, roles);
    }

    /**
     * 撤销用户角色
     */
    public void revokeRole(String userId, String role) {
        userRoles.remove(userId, role);
    }

    /**
     * 获取用户的所有角色（永不为 null）
     */
    public Collection<String> getUserRoles(String userId) {
        return userRoles.get(userId);  // 空 key 返回空集合，不是 null
    }

    /**
     * 检查用户是否有指定角色
     */
    public boolean hasRole(String userId, String role) {
        return userRoles.get(userId).contains(role);
    }

    /**
     * 获取拥有指定角色的所有用户
     */
    public Set<String> getUsersByRole(String role) {
        Multimap<String, String> roleToUsers = Multimaps.invertFrom(
            userRoles, 
            HashMultimap.create()
        );
        return new HashSet<>(roleToUsers.get(role));
    }

    // ========== 角色-权限管理 ==========

    /**
     * 为角色添加权限
     */
    public void grantPermission(String role, String permission) {
        rolePermissions.put(role, permission);
    }

    /**
     * 获取角色的所有权限
     */
    public Set<String> getRolePermissions(String role) {
        return new HashSet<>(rolePermissions.get(role));
    }

    /**
     * 获取用户的所有权限（聚合）
     */
    public Set<String> getUserPermissions(String userId) {
        Set<String> permissions = new HashSet<>();
        for (String role : userRoles.get(userId)) {
            permissions.addAll(rolePermissions.get(role));
        }
        return permissions;
    }

    /**
     * 检查用户是否有指定权限
     */
    public boolean hasPermission(String userId, String permission) {
        return getUserPermissions(userId).contains(permission);
    }

    // ========== 标签系统 ==========

    /**
     * 为文章添加标签
     */
    public void tagArticle(String articleId, String tag) {
        articleTags.put(articleId, tag);
    }

    /**
     * 获取文章的所有标签（保持添加顺序）
     */
    public List<String> getArticleTags(String articleId) {
        return new ArrayList<>(articleTags.get(articleId));
    }

    /**
     * 根据标签查找文章
     */
    public Set<String> getArticlesByTag(String tag) {
        Multimap<String, String> tagToArticles = Multimaps.invertFrom(
            articleTags,
            HashMultimap.create()
        );
        return new HashSet<>(tagToArticles.get(tag));
    }

    /**
     * 获取热门标签（按使用次数排序）
     */
    public List<Map.Entry<String, Integer>> getPopularTags(int topN) {
        Multiset<String> tagCount = HashMultiset.create(articleTags.values());
        
        return tagCount.entrySet().stream()
            .sorted((e1, e2) -> Integer.compare(e2.getCount(), e1.getCount()))
            .limit(topN)
            .map(e -> new AbstractMap.SimpleEntry<>(e.getElement(), e.getCount()))
            .collect(Collectors.toList());
    }

    /**
     * 查找相似文章（有相同标签）
     */
    public Set<String> findSimilarArticles(String articleId) {
        Collection<String> tags = articleTags.get(articleId);
        if (tags.isEmpty()) {
            return Collections.emptySet();
        }

        Set<String> similar = new HashSet<>();
        Multimap<String, String> tagToArticles = Multimaps.invertFrom(
            articleTags,
            HashMultimap.create()
        );
        
        for (String tag : tags) {
            similar.addAll(tagToArticles.get(tag));
        }
        similar.remove(articleId);  // 排除自己
        
        return similar;
    }

    // ========== 统计信息 ==========

    public int getUserCount() {
        return userRoles.keySet().size();
    }

    public int getRoleCount() {
        return rolePermissions.keySet().size();
    }

    public int getArticleCount() {
        return articleTags.keySet().size();
    }

    public Multiset<String> getRoleDistribution() {
        return HashMultiset.create(userRoles.values());
    }

    // ========== 测试入口 ==========
    public static void main(String[] args) {
        AuthorizationManager manager = new AuthorizationManager();

        // 测试权限管理
        System.out.println("=== 权限管理测试 ===");
        
        // 设置角色权限
        manager.grantPermission("ADMIN", "user.create");
        manager.grantPermission("ADMIN", "user.delete");
        manager.grantPermission("ADMIN", "system.config");
        manager.grantPermission("USER", "user.read");
        manager.grantPermission("USER", "order.create");

        // 分配用户角色
        manager.assignRole("user1", "ADMIN");
        manager.assignRole("user1", "USER");
        manager.assignRole("user2", "USER");

        System.out.println("user1 的角色: " + manager.getUserRoles("user1"));
        System.out.println("user1 的权限: " + manager.getUserPermissions("user1"));
        System.out.println("user2 有 order.create? " + manager.hasPermission("user2", "order.create"));
        System.out.println("user2 有 system.config? " + manager.hasPermission("user2", "system.config"));

        // 反向查询
        Set<String> admins = manager.getUsersByRole("ADMIN");
        System.out.println("所有管理员: " + admins);

        // 测试标签系统
        System.out.println("\n=== 标签系统测试 ===");
        manager.tagArticle("art1", "java");
        manager.tagArticle("art1", "guava");
        manager.tagArticle("art1", "tutorial");
        manager.tagArticle("art2", "java");
        manager.tagArticle("art2", "spring");
        manager.tagArticle("art3", "python");
        manager.tagArticle("art3", "tutorial");

        System.out.println("art1 的标签: " + manager.getArticleTags("art1"));
        System.out.println("java 相关文章: " + manager.getArticlesByTag("java"));
        System.out.println("与 art1 相似的文章: " + manager.findSimilarArticles("art1"));

        System.out.println("\n热门标签 Top 3:");
        manager.getPopularTags(3).forEach(e -> 
            System.out.println("  " + e.getKey() + ": " + e.getValue())
        );
    }
}
```

### 测试验证

```java
package com.example.guava.demo;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

import java.util.Arrays;
import java.util.Collection;
import java.util.Set;

public class AuthorizationManagerTest {

    private AuthorizationManager manager;

    @BeforeEach
    public void setUp() {
        manager = new AuthorizationManager();
    }

    @Test
    public void testAssignAndGetRoles() {
        manager.assignRole("user1", "ADMIN");
        manager.assignRole("user1", "USER");
        
        Collection<String> roles = manager.getUserRoles("user1");
        assertTrue(roles.contains("ADMIN"));
        assertTrue(roles.contains("USER"));
    }

    @Test
    public void testGetRolesReturnsEmptyNotNull() {
        Collection<String> roles = manager.getUserRoles("nonexistent");
        assertNotNull(roles);
        assertTrue(roles.isEmpty());
    }

    @Test
    public void testHasRole() {
        manager.assignRole("user1", "ADMIN");
        assertTrue(manager.hasRole("user1", "ADMIN"));
        assertFalse(manager.hasRole("user1", "USER"));
    }

    @Test
    public void testRevokeRole() {
        manager.assignRole("user1", "ADMIN");
        manager.revokeRole("user1", "ADMIN");
        assertFalse(manager.hasRole("user1", "ADMIN"));
    }

    @Test
    public void testGetUsersByRole() {
        manager.assignRole("user1", "ADMIN");
        manager.assignRole("user2", "ADMIN");
        manager.assignRole("user3", "USER");
        
        Set<String> admins = manager.getUsersByRole("ADMIN");
        assertEquals(2, admins.size());
        assertTrue(admins.contains("user1"));
        assertTrue(admins.contains("user2"));
    }

    @Test
    public void testHasPermission() {
        manager.grantPermission("ADMIN", "user.delete");
        manager.assignRole("user1", "ADMIN");
        
        assertTrue(manager.hasPermission("user1", "user.delete"));
    }

    @Test
    public void testGetUserPermissionsAggregation() {
        manager.grantPermission("ADMIN", "user.delete");
        manager.grantPermission("USER", "order.create");
        manager.assignRole("user1", "ADMIN");
        manager.assignRole("user1", "USER");
        
        Set<String> permissions = manager.getUserPermissions("user1");
        assertTrue(permissions.contains("user.delete"));
        assertTrue(permissions.contains("order.create"));
    }

    @Test
    public void testTagArticle() {
        manager.tagArticle("art1", "java");
        manager.tagArticle("art1", "guava");
        
        assertEquals(Arrays.asList("java", "guava"), manager.getArticleTags("art1"));
    }

    @Test
    public void testGetArticlesByTag() {
        manager.tagArticle("art1", "java");
        manager.tagArticle("art2", "java");
        manager.tagArticle("art3", "python");
        
        Set<String> javaArticles = manager.getArticlesByTag("java");
        assertEquals(2, javaArticles.size());
    }

    @Test
    public void testFindSimilarArticles() {
        manager.tagArticle("art1", "java");
        manager.tagArticle("art1", "guava");
        manager.tagArticle("art2", "java");
        manager.tagArticle("art3", "python");
        
        Set<String> similar = manager.findSimilarArticles("art1");
        assertTrue(similar.contains("art2"));
        assertFalse(similar.contains("art1"));  // 不包含自己
        assertFalse(similar.contains("art3"));  // 没有共同标签
    }
}
```

### 可能遇到的坑及解决方法

| 坑点 | 现象 | 解决方法 |
|------|------|----------|
| `get()` 返回视图修改 | 意外修改原 Multimap | 需要隔离时用 `new ArrayList<>(multimap.get(key))` |
| `containsKey` 与空值 | key 存在但无值时返回 false | 用 `!get(key).isEmpty()` 判断 |
| 反向映射性能 | 频繁 invertFrom 性能差 | 缓存反向映射或使用双向 Multimap |
| 重复值问题 | ListMultimap 允许重复 | 需要去重用 HashMultimap |

---

## 4 项目总结

### 优缺点对比

| 维度 | Multimap | Map<String, Collection> | 关系数据库 |
|------|----------|------------------------|------------|
| API 简洁 | ★★★★★ 一行顶多行 | ★★ 样板代码多 | ★★★★ SQL 声明式 |
| null 安全 | ★★★★★ 永不为 null | ★★ 需判空 | ★★★★★ 约束保障 |
| 内存效率 | ★★★★ 优化实现 | ★★★ 手动控制 | ★★ 需要连接查询 |
| 反向查询 | ★★★ invertFrom | ★★ 需自建索引 | ★★★★★ 索引支持 |
| 持久化 | ★ 内存 only | ★ 内存 only | ★★★★★ 持久化 |

### 适用场景

1. **权限系统**：用户-角色、角色-权限
2. **标签系统**：文章-标签、商品-分类
3. **购物车**：用户-商品 SKU
4. **社交关系**：用户-关注、用户-粉丝
5. **配置管理**：环境-配置项

### 不适用场景

1. **需要持久化**：用关系数据库
2. **超大规模数据**：内存放不下
3. **复杂查询需求**：需要多条件联合查询
4. **事务要求**：需要 ACID 保证

### 生产踩坑案例

**案例 1：误用视图导致意外修改**
```java
Collection<String> roles = multimap.get("user1");
roles.clear();  // 原 multimap 中 user1 的所有角色都被清了！
```
解决：需要只读时用 `ImmutableList.copyOf(multimap.get(key))`。

**案例 2：重复值导致统计错误**
```java
ListMultimap<String, String> map = ArrayListMultimap.create();
map.put("key", "value");
map.put("key", "value");  // 重复添加
map.get("key").size();  // 返回 2，不是 1！
```
解决：需要去重用 `HashMultimap`。

**案例 3：containsKey 与空值混淆**
```java
multimap.put("key", "value");
multimap.remove("key", "value");
boolean exists = multimap.containsKey("key");  // false！
```
解决：理解 Multimap 自动移除空 key 的机制。

### 思考题答案（第 9 章思考题 1）

> **问题**：`Multiset` 和 Java 8 的 `Map.merge()` 如何选择？

**答案**：
- **Multiset**：计数是核心业务概念，需要专门的计数 API（如 setCount、最高频查询）
- **`Map.merge()`**：一次性计数操作，不需要后续复杂查询
- **简单统计**：直接用 `groupingBy` + `counting()`  collector

### 新思考题

1. `Multimap` 和数据库的多对多关系表相比，各自的优势和局限是什么？
2. 如何实现一个支持自动过期（TTL）的 Multimap？

### 推广计划提示

**开发**：
- 一对多关系优先使用 Multimap
- Code Review 检查 Map<List> 用法

**测试**：
- 测试视图的修改是否影响原 Multimap
- 测试空 key 和空值集合的行为

**运维**：
- 监控 Multimap 内存占用
- 大数据量时考虑分页或缓存
