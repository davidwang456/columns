# 第 3 章：Preconditions 前置校验与失败早发现

## 1 项目背景

在金融支付系统的核心交易模块中，高级工程师老王最近接手了一个棘手的生产故障。一笔转账交易在处理过程中，账户余额计算出现了负数，导致下游风控系统误判为"洗钱"，触发了资金冻结。追查发现，问题的根源竟然是一个早该被拦截的非法参数：交易金额传入了 -1000 元。

这个负数本应该在接口入口处就被拒掉，却因为参数校验逻辑分散且不完善，一路穿透到了核心业务层。更可怕的是，类似的参数问题在系统中有 17 处之多，有些校验甚至只是简单的 `if (amount > 0)`，连错误消息都没有。

**业务场景**：支付系统对参数合法性要求极高，任何非法输入都必须在最外层就被拦截，避免污染核心业务逻辑。

**痛点放大**：
- **校验逻辑散落各处**：每个方法都重复写类似的 if 判断，维护成本极高。
- **错误消息不统一**：有些抛 `IllegalArgumentException`，有些抛自定义异常，有些甚至只是打日志返回 false。
- **边界条件遗漏**：只检查了正数，没检查最大值；只检查了非空，没检查长度。
- **排查困难**：异常发生时没有上下文信息，不知道哪个参数出了问题。

如果没有一套统一的、表达力强的参数校验机制，类似的故障将反复发生。

**技术映射**：Guava 的 `Preconditions` 提供了一组静态方法，用于在方法入口处快速检查前置条件，失败时立即抛出带有详细信息的异常，实现"快速失败"原则。

---

## 2 项目设计

**场景**：支付系统架构评审会，讨论参数校验规范。

---

**小胖**：（拿着故障报告）"我说，这故障根因也太离谱了！传个负数进来居然能走到转账逻辑？这不就跟食堂卖饭，有人递过来一张假钞，收银小妹还认真数了一遍才说'哎呀这是假的'一样吗？"

**小白**：（点头）"问题出在防御层级。参数校验应该在**方法入口处**就完成，而不是散落在业务逻辑中。现在的代码像筛子，到处都是洞。"

**大师**：（在白板上画了三个同心圆）"你们看，系统应该有**三层防御**：
1. **外层**：接口层参数格式校验（长度、类型、必填）
2. **中层**：业务规则校验（范围、状态、权限）
3. **内层**：领域模型不变式校验（对象内部一致性）

Guava 的 `Preconditions` 最适合**中层**，在业务方法入口处做快速失败检查。

**技术映射**：`Preconditions` 就像是工厂的安检门，不合格品在第一道门就被拒收，不会流入生产线污染后续流程。"

**小胖**："那用 `Preconditions` 具体怎么写？是不是就是 `if` 语句换个写法？"

**小白**："远不止。你看 `Preconditions` 的几个核心方法：
- `checkArgument(boolean, message)`：检查业务条件，失败抛 `IllegalArgumentException`
- `checkNotNull(T, message)`：检查非空，失败抛 `NullPointerException`
- `checkState(boolean, message)`：检查对象状态，失败抛 `IllegalStateException`
- `checkElementIndex(int, size)`：检查索引边界，失败抛 `IndexOutOfBoundsException`
- `checkPositionIndexes(int, int, size)`：检查范围边界

最关键的是**错误消息支持格式化**：`checkArgument(age > 0, "Age must be positive, but was %s", age)`，异常直接告诉你哪个值出了问题。"

**大师**："而且 `Preconditions` 的设计是**零成本抽象**——校验通过时几乎没有额外开销，失败时才构造异常对象。这比手动写 if-else 更简洁，可读性也更好。

**技术映射**：`Preconditions` 把防御性代码从'怎么做'（if/throw）提升到'做什么'（check what），让代码意图更清晰。"

**小胖**："那如果我想在失败时抛自定义异常呢？比如我们的业务异常 `PaymentException`？"

**小白**："`Preconditions` 不支持自定义异常类型，它就是为快速失败设计的。如果需要抛业务异常，可以用 `verify` 模式：
```java
if (amount <= 0) {
    throw new PaymentException(ErrorCode.INVALID_AMOUNT, amount);
}
```
但建议先用 `Preconditions` 做基础校验，业务异常处理放在上层转换。"

**大师**："还要注意**校验顺序**。先检查 null，再检查空字符串，再检查格式和范围。这样异常消息会更精确，比如先发现是 null，而不是说'格式不对'。

**技术映射**：合理的校验顺序能让错误诊断更高效，把最明显的问题最先暴露出来。"

**小胖**："明白了！就是说 `Preconditions` 负责'这个值必须满足什么条件'，业务异常负责'这个业务场景为什么不能继续'。"

**大师**："对！而且要注意，**不要滥用 `Preconditions` 做复杂的业务校验**。它只是简单的布尔检查，复杂的规则（如'用户是否有足够余额'）应该用领域服务来表达。"

---

## 3 项目实战

### 环境准备

沿用第 1 章配置：

```xml
<dependency>
    <groupId>com.google.guava</groupId>
    <artifactId>guava</artifactId>
    <version>33.0.0-jre</version>
</dependency>
```

### 分步实现：支付系统参数校验重构

**步骤目标**：用 `Preconditions` 构建支付接口的多层校验体系。

**代码实现**：

```java
package com.example.guava.demo;

import com.google.common.base.Preconditions;
import com.google.common.base.Strings;
import java.math.BigDecimal;
import java.util.List;
import java.util.regex.Pattern;

/**
 * 支付系统参数校验服务
 */
public class PaymentValidationService {

    // 常量定义
    private static final int MAX_ACCOUNT_LENGTH = 32;
    private static final BigDecimal MAX_SINGLE_AMOUNT = new BigDecimal("100000000.00");  // 1亿
    private static final Pattern ACCOUNT_PATTERN = Pattern.compile("^[A-Z0-9]+$");
    private static final Pattern CURRENCY_PATTERN = Pattern.compile("^[A-Z]{3}$");

    /**
     * 转账请求参数校验 - 完整示例
     */
    public void validateTransferRequest(TransferRequest request) {
        // ========== Level 1: 非空检查 ==========
        Preconditions.checkNotNull(request, "Transfer request cannot be null");
        
        // ========== Level 2: 字符串非空且非空白 ==========
        Preconditions.checkArgument(
            !Strings.isNullOrEmpty(request.getSourceAccount()),
            "Source account is required"
        );
        Preconditions.checkArgument(
            !Strings.isNullOrEmpty(request.getTargetAccount()),
            "Target account is required"
        );
        Preconditions.checkArgument(
            !Strings.isNullOrEmpty(request.getCurrency()),
            "Currency is required"
        );
        
        // ========== Level 3: 格式检查 ==========
        String sourceAccount = request.getSourceAccount().trim();
        String targetAccount = request.getTargetAccount().trim();
        String currency = request.getCurrency().trim();
        
        // 账户格式：大写字母+数字，长度1-32
        Preconditions.checkArgument(
            ACCOUNT_PATTERN.matcher(sourceAccount).matches(),
            "Source account format invalid: %s (expected: uppercase letters and digits)",
            sourceAccount
        );
        Preconditions.checkArgument(
            ACCOUNT_PATTERN.matcher(targetAccount).matches(),
            "Target account format invalid: %s",
            targetAccount
        );
        Preconditions.checkArgument(
            sourceAccount.length() <= MAX_ACCOUNT_LENGTH,
            "Source account too long: %s chars (max: %s)",
            sourceAccount.length(), MAX_ACCOUNT_LENGTH
        );
        
        // 货币格式：3位大写字母（ISO 4217）
        Preconditions.checkArgument(
            CURRENCY_PATTERN.matcher(currency).matches(),
            "Currency format invalid: %s (expected: 3 uppercase letters like CNY, USD)",
            currency
        );
        
        // ========== Level 4: 业务规则检查 ==========
        Preconditions.checkNotNull(request.getAmount(), "Amount cannot be null");
        
        BigDecimal amount = request.getAmount();
        Preconditions.checkArgument(
            amount.compareTo(BigDecimal.ZERO) > 0,
            "Amount must be positive, but was %s",
            amount
        );
        Preconditions.checkArgument(
            amount.compareTo(MAX_SINGLE_AMOUNT) <= 0,
            "Amount exceeds single transaction limit: %s (max: %s)",
            amount, MAX_SINGLE_AMOUNT
        );
        
        // 小数位数检查（假设货币最小单位是分）
        Preconditions.checkArgument(
            amount.scale() <= 2,
            "Amount precision too high: %s decimal places (max: 2)",
            amount.scale()
        );
        
        // 账户不能相同
        Preconditions.checkArgument(
            !sourceAccount.equals(targetAccount),
            "Source and target account cannot be the same: %s",
            sourceAccount
        );
        
        // ========== Level 5: 集合参数检查 ==========
        if (request.getTags() != null) {
            List<String> tags = request.getTags();
            Preconditions.checkArgument(
                tags.size() <= 10,
                "Too many tags: %s (max: 10)",
                tags.size()
            );
            for (int i = 0; i < tags.size(); i++) {
                Preconditions.checkArgument(
                    tags.get(i) != null && !tags.get(i).isEmpty(),
                    "Tag at index %s cannot be null or empty",
                    i
                );
            }
        }
    }

    /**
     * 批量转账校验 - 使用 checkElementIndex
     */
    public void validateBatchTransfer(List<TransferRequest> requests) {
        Preconditions.checkNotNull(requests, "Request list cannot be null");
        
        Preconditions.checkArgument(
            !requests.isEmpty(),
            "Batch transfer requires at least one request"
        );
        Preconditions.checkArgument(
            requests.size() <= 100,
            "Batch size too large: %s (max: 100)",
            requests.size()
        );
        
        for (int i = 0; i < requests.size(); i++) {
            try {
                validateTransferRequest(requests.get(i));
            } catch (IllegalArgumentException e) {
                throw new IllegalArgumentException(
                    String.format("Invalid request at index %s: %s", i, e.getMessage()),
                    e
                );
            }
        }
    }

    /**
     * 账户状态检查 - 使用 checkState
     */
    public void checkAccountStatus(Account account) {
        Preconditions.checkNotNull(account, "Account cannot be null");
        
        // checkState 用于检查对象内部状态
        Preconditions.checkState(
            account.isActivated(),
            "Account %s is not activated",
            account.getAccountId()
        );
        Preconditions.checkState(
            !account.isFrozen(),
            "Account %s is frozen",
            account.getAccountId()
        );
        Preconditions.checkState(
            !account.isClosed(),
            "Account %s is closed",
            account.getAccountId()
        );
    }

    /**
     * 索引边界检查 - 使用 checkElementIndex / checkPositionIndexes
     */
    public String getTransactionId(List<String> transactions, int index) {
        Preconditions.checkNotNull(transactions, "Transaction list cannot be null");
        
        // checkElementIndex: 检查是否是有效的元素索引 [0, size)
        int validIndex = Preconditions.checkElementIndex(
            index, 
            transactions.size(),
            "Transaction index"
        );
        
        return transactions.get(validIndex);
    }

    public List<String> getTransactionRange(List<String> transactions, int from, int to) {
        Preconditions.checkNotNull(transactions, "Transaction list cannot be null");
        
        // checkPositionIndexes: 检查范围 [from, to) 是否有效
        Preconditions.checkPositionIndexes(from, to, transactions.size());
        
        return transactions.subList(from, to);
    }

    // ========== 领域模型 ==========
    public static class TransferRequest {
        private String sourceAccount;
        private String targetAccount;
        private BigDecimal amount;
        private String currency;
        private List<String> tags;
        
        // getters and setters
        public String getSourceAccount() { return sourceAccount; }
        public void setSourceAccount(String sourceAccount) { this.sourceAccount = sourceAccount; }
        public String getTargetAccount() { return targetAccount; }
        public void setTargetAccount(String targetAccount) { this.targetAccount = targetAccount; }
        public BigDecimal getAmount() { return amount; }
        public void setAmount(BigDecimal amount) { this.amount = amount; }
        public String getCurrency() { return currency; }
        public void setCurrency(String currency) { this.currency = currency; }
        public List<String> getTags() { return tags; }
        public void setTags(List<String> tags) { this.tags = tags; }
    }

    public static class Account {
        private String accountId;
        private boolean activated;
        private boolean frozen;
        private boolean closed;
        
        public Account(String accountId, boolean activated, boolean frozen, boolean closed) {
            this.accountId = accountId;
            this.activated = activated;
            this.frozen = frozen;
            this.closed = closed;
        }
        
        public String getAccountId() { return accountId; }
        public boolean isActivated() { return activated; }
        public boolean isFrozen() { return frozen; }
        public boolean isClosed() { return closed; }
    }
}
```

### 测试验证

```java
package com.example.guava.demo;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.DisplayName;
import static org.junit.jupiter.api.Assertions.*;

import java.math.BigDecimal;
import java.util.Arrays;
import java.util.Collections;

public class PaymentValidationServiceTest {

    private final PaymentValidationService service = new PaymentValidationService();

    @Test
    @DisplayName("正常转账请求应该通过校验")
    public void testValidTransfer() {
        PaymentValidationService.TransferRequest request = createValidRequest();
        assertDoesNotThrow(() -> service.validateTransferRequest(request));
    }

    @Test
    @DisplayName("空请求应该抛 NullPointerException")
    public void testNullRequest() {
        NullPointerException ex = assertThrows(
            NullPointerException.class,
            () -> service.validateTransferRequest(null)
        );
        assertTrue(ex.getMessage().contains("Transfer request"));
    }

    @Test
    @DisplayName("负数金额应该抛 IllegalArgumentException 并包含实际值")
    public void testNegativeAmount() {
        PaymentValidationService.TransferRequest request = createValidRequest();
        request.setAmount(new BigDecimal("-100"));
        
        IllegalArgumentException ex = assertThrows(
            IllegalArgumentException.class,
            () -> service.validateTransferRequest(request)
        );
        assertTrue(ex.getMessage().contains("-100"));
        assertTrue(ex.getMessage().contains("positive"));
    }

    @Test
    @DisplayName("金额精度超过2位小数应该报错")
    public void testAmountPrecision() {
        PaymentValidationService.TransferRequest request = createValidRequest();
        request.setAmount(new BigDecimal("100.123"));
        
        IllegalArgumentException ex = assertThrows(
            IllegalArgumentException.class,
            () -> service.validateTransferRequest(request)
        );
        assertTrue(ex.getMessage().contains("precision"));
    }

    @Test
    @DisplayName("相同账户转账应该被拒绝")
    public void testSameAccount() {
        PaymentValidationService.TransferRequest request = createValidRequest();
        request.setTargetAccount(request.getSourceAccount());
        
        IllegalArgumentException ex = assertThrows(
            IllegalArgumentException.class,
            () -> service.validateTransferRequest(request)
        );
        assertTrue(ex.getMessage().contains("same"));
    }

    @Test
    @DisplayName("冻结账户应该抛 IllegalStateException")
    public void testFrozenAccount() {
        PaymentValidationService.Account account = 
            new PaymentValidationService.Account("ACC001", true, true, false);
        
        IllegalStateException ex = assertThrows(
            IllegalStateException.class,
            () -> service.checkAccountStatus(account)
        );
        assertTrue(ex.getMessage().contains("frozen"));
    }

    @Test
    @DisplayName("索引越界应该返回被修正后的有效索引")
    public void testElementIndex() {
        var list = Arrays.asList("T1", "T2", "T3");
        String result = service.getTransactionId(list, 1);
        assertEquals("T2", result);
    }

    @Test
    @DisplayName("无效索引应该抛 IndexOutOfBoundsException")
    public void testInvalidElementIndex() {
        var list = Arrays.asList("T1", "T2");
        assertThrows(IndexOutOfBoundsException.class, () -> {
            service.getTransactionId(list, 5);
        });
    }

    private PaymentValidationService.TransferRequest createValidRequest() {
        PaymentValidationService.TransferRequest request = 
            new PaymentValidationService.TransferRequest();
        request.setSourceAccount("ACC123456");
        request.setTargetAccount("ACC789012");
        request.setAmount(new BigDecimal("1000.00"));
        request.setCurrency("CNY");
        return request;
    }
}
```

### 可能遇到的坑及解决方法

| 坑点 | 现象 | 解决方法 |
|------|------|----------|
| 校验顺序不当 | null 值进入格式校验，异常消息不准确 | 先 null 检查，再格式检查，再业务检查 |
| `checkNotNull` 返回值被忽略 | 未使用返回值赋值，后续仍用原变量 | 使用链式：`this.field = checkNotNull(param)` |
| 异常消息中包含敏感信息 | 账户号、金额泄露到日志 | 对敏感字段脱敏或使用错误码 |
| 校验逻辑过于复杂 | 一个方法内校验代码超过业务代码 | 复杂校验抽取为独立的 Validator 类 |

---

## 4 项目总结

### 优缺点对比

| 维度 | Guava Preconditions | 手工 if-throw | Bean Validation (JSR 303) |
|------|---------------------|---------------|-------------------------|
| 简洁度 | ★★★★★ 一行顶五行 | ★★ 样板代码多 | ★★★★ 注解驱动 |
| 表达能力 | ★★★★★ 格式化消息 | ★★★ 自己拼接 | ★★★★ 支持国际化 |
| 灵活性 | ★★★★ 仅支持简单条件 | ★★★★★ 任意逻辑 | ★★★★ 支持自定义约束 |
| 性能 | ★★★★★ 零成本抽象 | ★★★★★ 相同 | ★★★ 反射开销 |
| 与业务集成 | ★★★ 需转业务异常 | ★★★★★ 直接抛业务异常 | ★★★★ 支持分组校验 |

### 适用场景

1. **方法入口参数校验**：快速拦截非法输入
2. **集合索引边界检查**：替代手工比较
3. **对象状态检查**：确保对象处于有效状态
4. **防御性编程**：对外部输入保持不信任
5. **内部方法契约**：明确前置条件

### 不适用场景

1. **复杂业务规则校验**：如"账户余额是否充足"
2. **需要国际化错误消息**：`Preconditions` 不支持 i18n
3. **需要收集所有错误**：`Preconditions` 是快速失败，不累积
4. **与框架集成的校验**：如 Spring 的 `@Valid`

### 生产踩坑案例

**案例 1：异常消息中包含用户密码**
```java
// 坑：敏感信息泄露
Preconditions.checkArgument(
    password.length() >= 8,
    "Password too short: %s",  // 可能记录明文密码！
    password
);
```
解决：敏感信息不要打印到异常消息中。

**案例 2：校验顺序错误导致诊断困难**
```java
// 坑：先检查格式，后发现是 null
Preconditions.checkArgument(
    email.matches("..."),  // NPE 先于格式错误！
    "Invalid email: %s", email
);
```
解决：先 `checkNotNull`，再格式校验。

**案例 3：过度使用 Preconditions 做业务校验**
```java
// 坑：用 Preconditions 检查余额
Preconditions.checkArgument(
    account.getBalance().compareTo(amount) >= 0,
    "Insufficient balance"  // 应该抛业务异常！
);
```
解决：业务规则用业务异常，输入格式用 `Preconditions`。

### 思考题答案（第 2 章思考题 1）

> **问题**：在什么场景下应该选择 `or(default)` 而不是 `orNull()`？

**答案**：
1. **链式调用中**：`orNull()` 可能导致后续 NPE，`or(default)` 提供安全默认值
2. **数值计算中**：如折扣计算，空值应默认为 0 而非 null
3. **配置读取中**：缺失配置应使用默认值而非 null
4. **展示层**：用户看不到 null，需要可读的默认文案

### 新思考题

1. `checkArgument` 和 `checkState` 的区别是什么？请举例说明各自适用的场景。
2. 如果需要在校验失败时返回错误码而非异常，如何封装 `Preconditions` 实现这一需求？

### 推广计划提示

**开发**：
- 制定团队规范：公共方法入口必须用 `Preconditions` 校验参数
- Code Review Checklist：检查新方法的参数校验覆盖率

**测试**：
- 针对每个 `Preconditions` 校验点设计边界用例
- 验证异常消息的准确性和信息完整度

**运维**：
- 在日志系统中对 `IllegalArgumentException` 设置告警
- 统计参数校验失败率，发现潜在攻击或接口误用
