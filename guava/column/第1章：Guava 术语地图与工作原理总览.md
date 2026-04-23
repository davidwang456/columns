# 第 1 章：Guava 术语地图与工作原理总览

## 1 项目背景

在一家中型互联网公司的技术部，新人开发工程师小林刚入职两周。他接到的第一个任务是维护一个老旧的订单处理模块。代码里充满了各种样板代码：手工写的空值判断、冗长的字符串拼接、复杂的集合操作循环。

小林发现，团队里资深工程师写的代码总是很简洁。同样是判断参数是否为空，他们只用一行 `Preconditions.checkNotNull(param)`；同样是拼接字符串，他们用 `Joiner.on(",").join(list)` 就搞定了。追问之下，他才知道团队一直在使用 Google 开源的 Guava 库。

**业务场景**：公司正在推进技术栈统一，要求所有新项目和重构模块必须采用 Guava 作为基础工具库，以减少重复造轮子、提升代码可读性。

**痛点放大**：
- **重复造轮子**：每个项目都自己写字符串工具、集合工具，质量参差不齐，测试覆盖率低。
- **空值灾难**：Java 的 null 设计导致生产环境频繁出现 NPE，排查成本高。
- **集合操作冗长**：JDK 原生 API 功能有限，为实现一个简单的"按条件过滤并转换"操作，往往需要写 5-10 行循环代码。
- **并发编程门槛高**：线程池配置、异步回调处理缺乏统一范式，新人容易踩坑。

如果没有 Guava，团队将继续在样板代码中耗费 30% 以上的开发时间，且代码质量难以保证。

**技术映射**：Guava 是 Google 内部广泛使用并开源的核心 Java 库，填补了 JDK 在集合、缓存、并发、字符串处理等方面的能力空白，强调"快速失败"（fail-fast）和不可变性（immutability）的防御式编程理念。

---

## 2 项目设计

**场景**：技术部周会，讨论是否在全公司推广 Guava。

---

**小胖**：（啃着包子走进会议室）"我说各位，最近看代码库，发现有人用个叫 Guava 的东西，几行代码就能搞定我以前写几十行的事。这不就跟食堂打饭有自动结算机一样吗？不用人工算钱了？"

**小白**：（放下笔记本）"你是说那个 `Joiner` 和 `Splitter` 吧？确实省代码。但我有个疑问——引入第三方库会不会增加包体积？而且万一 Google 不维护了怎么办？"

**大师**：（在白板上画了个图）"问得好。你们看这张图，Guava 的核心模块其实是有层次结构的。最底层是 **Basic Utilities**，包括空值处理、前置条件校验、对象方法增强、字符串工具等；往上是 **Collections**，提供了不可变集合、新集合类型（Multiset、Multimap、BiMap、Table）以及强大的集合工具类；再往上是 **Concurrency**，包含 ListenableFuture、Service、RateLimiter 等并发抽象；还有 **Cache** 本地缓存、**IO** 简化流操作、**Hashing** 哈希工具、**EventBus** 事件总线等。

**技术映射**：可以把 Guava 想象成一把瑞士军刀——每一层工具都针对特定场景做了打磨，而且它们之间是正交的，用多少引多少，不会强制你用全套。"

**小胖**："哦！那是不是就像我去食堂，既可以只用筷子，也可以筷子勺子叉子一起用，没人强迫我必须用全套？"

**小白**："这个比喻... 有点歪但意思到了。那我想追问一下，Guava 和 Apache Commons 有什么区别？我看很多功能两者都有。"

**大师**："关键差异在 **设计理念**。Guava 强调 **不可变性** 和 **fail-fast**。比如它的集合很多都有 Immutable 版本，一旦创建就不能修改，这在并发场景下天然安全。再比如 `Optional` 的设计，强制你显式处理空值，而不是返回 null 让调用方猜。

**技术映射**：Apache Commons 像是工具大杂烩，什么都有；Guava 则是一套有设计哲学的工程库，每个 API 都经过 Google 内部大规模生产验证。"

**小胖**："等等，你说那个 `Optional`，是不是就是让我不能再偷懒返回 null 了？那我不是要多写很多代码？"

**小白**："恰恰相反。你看这个例子——"

```java
// 不用 Guava：要写判空 + 异常 + 日志
public User getUser(String id) {
    if (id == null) {
        throw new IllegalArgumentException("id is null");
    }
    User user = userDao.findById(id);
    if (user == null) {
        throw new NotFoundException("user not found");
    }
    return user;
}

// 用 Guava：三行搞定
public User getUser(String id) {
    Preconditions.checkNotNull(id, "id cannot be null");
    return Optional.ofNullable(userDao.findById(id))
                   .orElseThrow(() -> new NotFoundException("user not found"));
}
```

**大师**："而且 Guava 的 **兼容性策略** 很清晰。它用 `@Beta` 注解标记实验性 API，用 `@Deprecated` 标记即将移除的 API，给足迁移时间。从 Release 历史看，每个大版本都会提前在 Wiki 公布废弃清单。

**技术映射**：引入 Guava 不是引入技术债，而是引入一套经过验证的工程规范。"

**小胖**："听你们这么一说，这玩意儿确实挺香。那咱们要是全公司推广，从哪开始学？"

**大师**："我建议分三步走：
1. **基础篇**（1-15章）：先把 Basic Utilities 和 Collections 用熟，解决 80% 的日常编码痛点；
2. **中级篇**（16-30章）：掌握 Cache、Concurrency、IO 等工程化能力，能设计中等复杂度的系统；
3. **高级篇**（31-40章）：深入源码，理解设计原理，能在极端场景下调优和扩展。

**技术映射**：Guava 的学习曲线是渐进式的，每个模块都可以独立使用，不需要一次性掌握全部。"

**小白**："明白了。那第一阶段的验收标准是什么？"

**大师**："很简单：新代码中不再出现手写的空值判断循环、不再用 `+` 拼接字符串、集合操作能用工具类就不用循环。能做到这三点，基础就算过关。"

---

## 3 项目实战

### 环境准备

**依赖配置**（Maven）：

```xml
<dependency>
    <groupId>com.google.guava</groupId>
    <artifactId>guava</artifactId>
    <version>33.0.0-jre</version>
</dependency>
```

**JDK 要求**：Guava 33+ 需要 JDK 8 或更高版本。

**验证安装**：

```bash
mvn dependency:tree | findstr guava
```

预期输出：
```
[INFO] com.google.guava:guava:jar:33.0.0-jre:compile
```

---

### 分步实现：用户注册参数校验模块

**步骤目标**：用 Guava 的 `Preconditions` 和 `Strings` 重构一个用户注册接口的参数校验逻辑。

**代码实现**：

```java
package com.example.guava.demo;

import com.google.common.base.Preconditions;
import com.google.common.base.Strings;
import com.google.common.collect.ImmutableList;

public class UserRegistrationService {

    private static final ImmutableList<String> RESERVED_NAMES = 
        ImmutableList.of("admin", "root", "system");

    /**
     * 用户注册参数校验
     * 
     * @param username 用户名（必填，3-20字符，不能是保留名）
     * @param email 邮箱（必填，必须符合基本格式）
     * @param age 年龄（必填，必须在 18-150 之间）
     * @return 校验通过返回 true
     */
    public boolean validateRegistration(String username, String email, Integer age) {
        // Step 1: 非空校验
        Preconditions.checkNotNull(username, "Username cannot be null");
        Preconditions.checkNotNull(email, "Email cannot be null");
        Preconditions.checkNotNull(age, "Age cannot be null");
        
        // Step 2: 字符串非空白校验
        Preconditions.checkArgument(
            !Strings.isNullOrEmpty(username.trim()), 
            "Username cannot be empty"
        );
        Preconditions.checkArgument(
            !Strings.isNullOrEmpty(email.trim()), 
            "Email cannot be empty"
        );
        
        // Step 3: 长度校验
        Preconditions.checkArgument(
            username.length() >= 3 && username.length() <= 20,
            "Username must be between 3 and 20 characters, but was %s",
            username.length()
        );
        
        // Step 4: 保留名校验
        Preconditions.checkArgument(
            !RESERVED_NAMES.contains(username.toLowerCase()),
            "Username '%s' is reserved and cannot be used",
            username
        );
        
        // Step 5: 年龄范围校验
        Preconditions.checkArgument(
            age >= 18 && age <= 150,
            "Age must be between 18 and 150, but was %s",
            age
        );
        
        // Step 6: 邮箱格式简单校验（生产环境建议用正则库）
        Preconditions.checkArgument(
            email.contains("@") && email.contains("."),
            "Email format is invalid: %s",
            email
        );
        
        return true;
    }
}
```

**运行结果说明**：

```java
public static void main(String[] args) {
    UserRegistrationService service = new UserRegistrationService();
    
    // 测试用例 1：正常情况
    try {
        boolean result = service.validateRegistration("john_doe", "john@example.com", 25);
        System.out.println("Test 1 passed: " + result);  // 输出: true
    } catch (Exception e) {
        System.out.println("Test 1 failed: " + e.getMessage());
    }
    
    // 测试用例 2：空用户名
    try {
        service.validateRegistration(null, "test@test.com", 25);
    } catch (NullPointerException e) {
        System.out.println("Test 2 passed - Null check works: " + e.getMessage());
        // 输出: Test 2 passed - Null check works: Username cannot be null
    }
    
    // 测试用例 3：保留名
    try {
        service.validateRegistration("admin", "test@test.com", 25);
    } catch (IllegalArgumentException e) {
        System.out.println("Test 3 passed - Reserved name check: " + e.getMessage());
        // 输出: Test 3 passed - Reserved name check: Username 'admin' is reserved...
    }
    
    // 测试用例 4：年龄超出范围
    try {
        service.validateRegistration("valid_user", "test@test.com", 10);
    } catch (IllegalArgumentException e) {
        System.out.println("Test 4 passed - Age range check: " + e.getMessage());
        // 输出: Test 4 passed - Age range check: Age must be between 18 and 150...
    }
}
```

**可能遇到的坑及解决方法**：

| 坑点 | 现象 | 解决方法 |
|------|------|----------|
| `checkNotNull` 抛的是 NPE 而非 IAE | 捕获异常时需要区分类型 | 根据业务需要选择 `checkNotNull` (NPE) 还是自定义检查 |
| 格式化字符串参数不匹配 | `IllegalFormatException` | 确保占位符数量与参数数量一致 |
| 使用 `Strings.isNullOrEmpty` 时传入的是 `" "`（空格） | 判断失败 | 需要 `trim()` 后再判断，或使用 `CharMatcher` |

---

### 完整代码清单

```java
package com.example.guava.demo;

import com.google.common.base.Preconditions;
import com.google.common.base.Strings;
import com.google.common.collect.ImmutableList;

/**
 * 第1章实战：Guava 基础工具入门
 * 
 * 演示内容：
 * 1. Preconditions 参数校验
 * 2. Strings 字符串工具
 * 3. ImmutableList 不可变集合
 */
public class Chapter01Demo {

    private static final ImmutableList<String> FORBIDDEN_WORDS = 
        ImmutableList.of("admin", "root", "system", "test");

    public static void main(String[] args) {
        Chapter01Demo demo = new Chapter01Demo();
        
        // 运行所有测试
        System.out.println("=== Guava Chapter 01 Demo ===\n");
        
        demo.testValidInput();
        demo.testNullUsername();
        demo.testEmptyUsername();
        demo.testReservedName();
        demo.testInvalidAge();
        demo.testInvalidEmail();
        
        System.out.println("\n=== All Tests Completed ===");
    }

    public void validateUserInput(String username, String email, Integer age) {
        Preconditions.checkNotNull(username, "Username is required");
        Preconditions.checkNotNull(email, "Email is required");
        Preconditions.checkNotNull(age, "Age is required");
        
        String trimmedUsername = username.trim();
        Preconditions.checkArgument(
            !Strings.isNullOrEmpty(trimmedUsername),
            "Username cannot be blank"
        );
        
        Preconditions.checkArgument(
            trimmedUsername.length() >= 3 && trimmedUsername.length() <= 20,
            "Username length must be 3-20, actual: %s",
            trimmedUsername.length()
        );
        
        Preconditions.checkArgument(
            !FORBIDDEN_WORDS.contains(trimmedUsername.toLowerCase()),
            "Username '%s' is reserved",
            trimmedUsername
        );
        
        Preconditions.checkArgument(
            age >= 18 && age <= 120,
            "Age must be 18-120, actual: %s",
            age
        );
        
        Preconditions.checkArgument(
            email.matches("^[A-Za-z0-9+_.-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}$"),
            "Invalid email format: %s",
            email
        );
        
        System.out.println("✓ Validation passed for: " + username);
    }

    // 测试方法
    private void testValidInput() {
        System.out.println("Test: Valid input");
        try {
            validateUserInput("john_doe", "john@example.com", 25);
        } catch (Exception e) {
            System.out.println("✗ Unexpected error: " + e.getMessage());
        }
        System.out.println();
    }

    private void testNullUsername() {
        System.out.println("Test: Null username");
        try {
            validateUserInput(null, "test@test.com", 25);
        } catch (NullPointerException e) {
            System.out.println("✓ Caught expected: " + e.getMessage());
        }
        System.out.println();
    }

    private void testEmptyUsername() {
        System.out.println("Test: Empty/blank username");
        try {
            validateUserInput("   ", "test@test.com", 25);
        } catch (IllegalArgumentException e) {
            System.out.println("✓ Caught expected: " + e.getMessage());
        }
        System.out.println();
    }

    private void testReservedName() {
        System.out.println("Test: Reserved username");
        try {
            validateUserInput("ADMIN", "test@test.com", 25);
        } catch (IllegalArgumentException e) {
            System.out.println("✓ Caught expected: " + e.getMessage());
        }
        System.out.println();
    }

    private void testInvalidAge() {
        System.out.println("Test: Invalid age");
        try {
            validateUserInput("validuser", "test@test.com", 15);
        } catch (IllegalArgumentException e) {
            System.out.println("✓ Caught expected: " + e.getMessage());
        }
        System.out.println();
    }

    private void testInvalidEmail() {
        System.out.println("Test: Invalid email format");
        try {
            validateUserInput("validuser", "invalid-email", 25);
        } catch (IllegalArgumentException e) {
            System.out.println("✓ Caught expected: " + e.getMessage());
        }
        System.out.println();
    }
}
```

---

### 测试验证

**JUnit 5 测试类**：

```java
package com.example.guava.demo;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.DisplayName;
import static org.junit.jupiter.api.Assertions.*;

public class Chapter01DemoTest {

    private final Chapter01Demo demo = new Chapter01Demo();

    @Test
    @DisplayName("正常输入应该通过校验")
    public void testValidInput() {
        assertDoesNotThrow(() -> {
            demo.validateUserInput("john_doe", "john@example.com", 25);
        });
    }

    @Test
    @DisplayName("空用户名应该抛出 NullPointerException")
    public void testNullUsername() {
        NullPointerException exception = assertThrows(
            NullPointerException.class,
            () -> demo.validateUserInput(null, "test@test.com", 25)
        );
        assertTrue(exception.getMessage().contains("Username"));
    }

    @Test
    @DisplayName("保留用户名应该抛出 IllegalArgumentException")
    public void testReservedName() {
        IllegalArgumentException exception = assertThrows(
            IllegalArgumentException.class,
            () -> demo.validateUserInput("admin", "test@test.com", 25)
        );
        assertTrue(exception.getMessage().contains("reserved"));
    }

    @Test
    @DisplayName("年龄超出范围应该抛出异常并包含实际值")
    public void testAgeOutOfRange() {
        IllegalArgumentException exception = assertThrows(
            IllegalArgumentException.class,
            () -> demo.validateUserInput("user", "test@test.com", 200)
        );
        assertTrue(exception.getMessage().contains("200"));
    }
}
```

**运行测试命令**：

```bash
mvn test -Dtest=Chapter01DemoTest
```

**预期输出**：
```
[INFO] Tests run: 4, Failures: 0, Errors: 0, Skipped: 0
[INFO] Chapter01DemoTest ✔
```

---

## 4 项目总结

### 优点与缺点对比

| 维度 | Guava | 原生 JDK / Apache Commons |
|------|-------|---------------------------|
| API 简洁度 | ★★★★★ 链式调用，一行顶十行 | ★★★ 需要多行样板代码 |
| 空值安全 | ★★★★★ Optional + Preconditions 强制处理 | ★★ null 可随意传递 |
| 不可变支持 | ★★★★★ 完整 Immutable 集合体系 | ★★ 需要 `Collections.unmodifiableXXX` 包装 |
| 文档与生态 | ★★★★★ Wiki 详尽，StackOverflow 资源丰富 | ★★★★ JDK 文档完整但不够实战 |
| 包体积 | ★★★ 约 3MB（可 ProGuard 裁剪） | ★★★★★ 无需额外依赖 |
| 版本兼容性 | ★★★★ 大版本升级需关注废弃 API | ★★★★★ JDK 向后兼容极佳 |

### 适用场景

**典型场景**：
1. 参数校验密集型接口（如用户注册、支付接口）
2. 需要防御性编程的共享工具类
3. 集合操作复杂的业务逻辑（过滤、转换、分组）
4. 配置解析与字符串处理场景
5. 需要不可变集合保证线程安全的场景

**不适用场景**：
1. 对包体积极度敏感的场景（如某些嵌入式设备）
2. 已经全面使用 Java 8+ Stream API 且无需向下兼容的项目
3. 已经深度使用 Apache Commons 且无重构预算的老项目

### 注意事项

1. **版本选择**：JDK 8+ 使用 `guava-33.x-jre`；Android 项目使用 `guava-33.x-android`（裁剪版）
2. **避免滥用**：不要仅仅为了 `Preconditions` 就引入 Guava，如果项目已用 Spring，可考虑 `Assert` 类
3. **异常类型区分**：`checkNotNull` 抛 `NullPointerException`，`checkArgument` 抛 `IllegalArgumentException`，捕获时注意类型
4. **线程安全**：`ImmutableList` 等不可变集合是线程安全的，但构建过程（`Builder`）不是

### 常见踩坑经验

**案例 1：日志中打印了敏感信息**

```java
// 坑：Preconditions 的异常消息直接暴露在了日志中
Preconditions.checkArgument(
    password.length() >= 8, 
    "Password too short: %s",  // 生产环境不要这样写！
    password
);
```

**根因**：Guava 的格式化消息会直接输出参数值。
**解决**：敏感字段校验失败时，不要打印字段值，只打印校验规则。

**案例 2：ImmutableList 修改导致异常**

```java
List<String> list = ImmutableList.of("a", "b");
list.add("c");  // 抛出 UnsupportedOperationException
```

**根因**：Immutable 集合是真正不可变的，不是包装器。
**解决**：需要可变集合时，使用 `Lists.newArrayList()` 或 `new ArrayList<>(immutableList)`。

**案例 3：版本升级导致编译失败**

Guava 21+ 移除了 `Futures.get()` 等部分方法，老项目升级后编译报错。

**根因**：Guava 会废弃并移除 API，尽管给了迁移时间。
**解决**：升级前查阅 [Release Notes](https://github.com/google/guava/wiki/ReleaseHistory)，使用 `@Deprecated` 标记的方法时做好迁移计划。

### 思考题

1. **进阶题**：`Preconditions.checkNotNull` 和 Java 8 的 `Objects.requireNonNull` 有什么区别？在什么场景下应该优先选择 Guava 的版本？

2. **设计题**：假设你要设计一个用户注册的参数校验框架，除了 Guava 的 `Preconditions`，你还需要考虑哪些维度（如国际化错误消息、分组校验、嵌套对象校验）？Guava 能否满足这些需求？如果不能，你会如何扩展或配合其他框架（如 Hibernate Validator）使用？

> **答案提示**：思考题 1 的答案在第 2 章末尾给出；思考题 2 的答案在第 15 章综合实战中讨论。

---

### 推广计划提示

**开发部门**：
- 第一周：在 Code Review 中要求新代码使用 `Preconditions` 替代手写空值判断
- 第二周：分享会讲解 `ImmutableList` 和 `Joiner/Splitter` 的使用场景
- 第三周：制定团队编码规范，明确 Guava 与 Stream API 的选用原则

**测试部门**：
- 配合开发完善参数校验的边界测试用例
- 关注 Guava 引入后的异常类型变化，更新自动化测试断言

**运维部门**：
- 在 CI/CD 中增加 Guava 版本扫描，防止多版本冲突
- 监控应用启动时 Guava 相关的类加载异常

**阅读顺序建议**：
- 新人开发：第 1-5 章 → 动手实战 → 第 6-10 章
- 核心开发：先通读基础篇，再重点攻克第 16-25 章（缓存与并发）
- 架构师：直接跳到高级篇，关注源码设计和 SRE 实践章节
