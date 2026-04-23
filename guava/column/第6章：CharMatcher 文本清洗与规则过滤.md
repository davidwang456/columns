# 第 6 章：CharMatcher 文本清洗与规则过滤

## 1 项目背景

在用户生成内容（UGC）平台的审核系统中，工程师小赵遇到了一个棘手的问题。用户提交的商品评价中充斥着各种"变形"的敏感词：有人在敏感词中间加空格，有人用特殊符号替代字母，还有人用全角字符绕过简单的关键词匹配。

更糟糕的是，从第三方数据源导入的商品描述字段，格式混乱不堪：有的包含不可见控制字符，有的混合了全角半角标点，有的连续出现多个换行。这些数据直接入库后，导致前端展示错乱，搜索分词也受到影响。

**业务场景**：内容审核、数据清洗、输入标准化、敏感词过滤等需要精细化字符处理的场景。

**痛点放大**：
- **字符匹配粒度太粗**：用 `String.contains` 只能做字面匹配，无法应对变形攻击。
- **正则表达式复杂难维护**：简单的字符过滤用正则，性能差且可读性低。
- **空白字符处理困难**：空格、制表符、换行、全角空格难以统一处理。
- **大小写敏感问题**：大小写混合输入时匹配失败。
- **特殊字符移除**：需要从字符串中提取或移除特定类别的字符。

如果没有一套灵活的字符级别处理工具，内容质量和数据质量将无法保证。

**技术映射**：Guava 的 `CharMatcher` 提供了一套声明式 API，用于匹配、计数、移除、替换、修剪特定类别的字符，比正则表达式更直观高效。

---

## 2 项目设计

**场景**：内容安全评审会，讨论敏感词过滤方案。

---

**小胖**：（看着敏感词绕过案例）"我说，这帮用户也太能折腾了！'反动'两个字中间加个空格，或者把'动'换成 unicode 相似字符，我们的关键词匹配就失效了。这不就跟食堂有人插队还故意站歪了假装没插队一样吗？"

**小白**：（皱眉）"根本问题是我们的匹配粒度太粗。我们需要在**字符级别**做标准化，把变形的输入还原成标准形式，再做匹配。"

**大师**：（在白板上画流程图）"Guava 的 `CharMatcher` 就是干这个的。它把字符匹配从'正则表达式'变成'组合式谓词'。看这些预定义常量：

```java
CharMatcher.anyOf("abc")           // 匹配 a、b 或 c
CharMatcher.inRange('a', 'z')      // 匹配小写字母
CharMatcher.digit()                // 匹配数字
CharMatcher.javaLetter()           // 匹配 Unicode 字母
CharMatcher.whitespace()           // 匹配空白字符
CharMatcher.ascii()                // 匹配 ASCII 字符
CharMatcher.noneOf("xyz")          // 不匹配 x、y、z
```

而且它们是**可组合**的：

```java
// 匹配字母或数字
CharMatcher.alphanumeric = CharMatcher.javaLetter().or(CharMatcher.digit());

// 匹配非空白字符
CharMatcher.notWhitespace = CharMatcher.whitespace().negate();
```

**技术映射**：`CharMatcher` 就像是字符级别的筛选器，你可以组合多个筛选条件，定义自己的'字符通行证'规则。"

**小胖**："那具体能做什么操作？"

**小白**："操作非常丰富：
- `matchesAllOf(String)`：是否全部匹配
- `matchesAnyOf(String)`：是否包含匹配
- `countIn(String)`：统计匹配次数
- `removeFrom(String)`：移除所有匹配字符
- `retainFrom(String)`：只保留匹配字符
- `replaceFrom(String, char)`：替换匹配字符
- `trimFrom(String)`：从两端修剪
- `collapseFrom(String, char)`：合并连续匹配字符

**大师**："举个例子——用户输入清洗：

```java
// 1. 将非数字字符替换为空，得到纯数字
String phone = CharMatcher.digit().retainFrom(userInput);

// 2. 合并连续空白为一个空格
String normalized = CharMatcher.whitespace().collapseFrom(text, ' ');

// 3. 只保留字母和数字，移除特殊符号
String cleaned = CharMatcher.javaLetterOrDigit().retainFrom(text);

// 4. 修剪两端空白（包括全角空格）
String trimmed = CharMatcher.whitespace().trimFrom(text);
```

**技术映射**：`CharMatcher` 是**预编译的字符判断逻辑**，比正则表达式在简单场景下性能更好，而且代码意图更清晰。"

**小胖**："那敏感词过滤怎么实现？"

**小白**："可以组合多个步骤：

```java
public String normalizeForMatching(String input) {
    return input
        // 1. 转为小写
        .toLowerCase()
        // 2. 将全角字符转为半角
        .transform(this::fullwidthToHalfwidth)
        // 3. 移除所有非字母数字字符（包括用于插入的空格和符号）
        .transform(s -> CharMatcher.javaLetterOrDigit().retainFrom(s));
}
```

先把输入标准化，再用标准化的形式做敏感词匹配。"

**大师**："还有一个高级用法——自定义 `CharMatcher`：

```java
// 自定义匹配：中文字符
CharMatcher chinese = CharMatcher.forPredicate(
    c -> Character.UnicodeBlock.of(c) == Character.UnicodeBlock.CJK_UNIFIED_IDEOGRAPHS
);

// 或使用范围
CharMatcher chineseRange = CharMatcher.inRange('\u4e00', '\u9fa5');
```

**技术映射**：自定义 `CharMatcher` 让你能处理任何 Unicode 范围的字符，应对国际化的字符处理需求。"

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

### 分步实现：UGC 内容清洗与敏感词过滤

**步骤目标**：用 `CharMatcher` 构建一套内容标准化和敏感词过滤系统。

**代码实现**：

```java
package com.example.guava.demo;

import com.google.common.base.CharMatcher;
import com.google.common.base.Joiner;
import com.google.common.base.Splitter;
import com.google.common.base.Strings;
import com.google.common.collect.ImmutableList;

import java.util.Arrays;
import java.util.List;
import java.util.stream.Collectors;

/**
 * UGC 内容清洗服务
 */
public class ContentCleaningService {

    // 预定义的 CharMatcher
    private static final CharMatcher DIGIT = CharMatcher.digit();
    private static final CharMatcher LETTER = CharMatcher.javaLetter();
    private static final CharMatcher LETTER_OR_DIGIT = CharMatcher.javaLetterOrDigit();
    private static final CharMatcher WHITESPACE = CharMatcher.whitespace();
    private static final CharMatcher INVISIBLE = CharMatcher.javaIsoControl();  // 控制字符
    private static final CharMatcher PUNCTUATION = CharMatcher.javaLetterOrDigit().negate()
        .and(CharMatcher.whitespace().negate());

    // 全角空格和常见全角标点
    private static final CharMatcher FULLWIDTH_WHITESPACE = CharMatcher.is('\u3000');
    private static final CharMatcher ANY_WHITESPACE = WHITESPACE.or(FULLWIDTH_WHITESPACE);

    /**
     * 标准化用户输入（用于搜索和匹配）
     */
    public String normalize(String input) {
        if (Strings.isNullOrEmpty(input)) {
            return "";
        }

        return input
            // 1. 移除控制字符
            .transform(INVISIBLE::removeFrom)
            // 2. 合并所有空白（包括全角空格）为一个半角空格
            .transform(s -> ANY_WHITESPACE.collapseFrom(s, ' '))
            // 3. 修剪两端空白
            .transform(ANY_WHITESPACE::trimFrom)
            // 4. 转为小写
            .toLowerCase();
    }

    /**
     * 提取纯文本（仅保留字母、数字和中文字符）
     */
    public String extractPlainText(String input) {
        if (Strings.isNullOrEmpty(input)) {
            return "";
        }

        CharMatcher chinese = CharMatcher.inRange('\u4e00', '\u9fa5');
        CharMatcher validChars = LETTER_OR_DIGIT.or(chinese).or(CharMatcher.is(' '));

        return validChars.retainFrom(normalize(input));
    }

    /**
     * 提取电话号码（仅保留数字）
     */
    public String extractPhoneNumber(String input) {
        if (Strings.isNullOrEmpty(input)) {
            return "";
        }
        return DIGIT.retainFrom(input);
    }

    /**
     * 移除所有标点符号
     */
    public String removePunctuation(String input) {
        if (Strings.isNullOrEmpty(input)) {
            return "";
        }
        return LETTER_OR_DIGIT.or(ANY_WHITESPACE).retainFrom(input);
    }

    /**
     * 计算有效字符数（不含空白和标点）
     */
    public int countValidChars(String input) {
        if (Strings.isNullOrEmpty(input)) {
            return 0;
        }
        return LETTER_OR_DIGIT.countIn(input);
    }

    /**
     * 检查是否全是空白字符
     */
    public boolean isAllWhitespace(String input) {
        if (Strings.isNullOrEmpty(input)) {
            return true;
        }
        return ANY_WHITESPACE.matchesAllOf(input);
    }

    /**
     * 检查是否包含中文字符
     */
    public boolean containsChinese(String input) {
        if (Strings.isNullOrEmpty(input)) {
            return false;
        }
        CharMatcher chinese = CharMatcher.inRange('\u4e00', '\u9fa5');
        return chinese.matchesAnyOf(input);
    }

    /**
     * 格式化多行文本（修剪每行，移除空行）
     */
    public String formatMultiline(String input) {
        if (Strings.isNullOrEmpty(input)) {
            return "";
        }

        return Splitter.onPattern("\r?\n")
            .splitToStream(input)
            .map(ANY_WHITESPACE::trimFrom)
            .filter(s -> !s.isEmpty())
            .collect(Collectors.joining("\n"));
    }

    /**
     * 敏感词标准化（用于匹配）
     */
    public String normalizeForSensitiveCheck(String input) {
        return normalize(input)
            // 移除所有非字母数字字符（包括用于分隔的空格）
            .transform(LETTER_OR_DIGIT::retainFrom);
    }

    /**
     * 简单敏感词检测（基于标准化后的匹配）
     */
    public boolean containsSensitiveWord(String input, List<String> sensitiveWords) {
        String normalized = normalizeForSensitiveCheck(input);
        
        for (String word : sensitiveWords) {
            String normalizedWord = normalizeForSensitiveCheck(word);
            if (normalized.contains(normalizedWord)) {
                return true;
            }
        }
        return false;
    }

    /**
     * 清洗商品描述（用于展示）
     */
    public String cleanProductDescription(String input) {
        if (Strings.isNullOrEmpty(input)) {
            return "";
        }

        return input
            // 1. 移除控制字符
            .transform(INVISIBLE::removeFrom)
            // 2. 合并连续空白
            .transform(s -> ANY_WHITESPACE.collapseFrom(s, ' '))
            // 3. 限制连续换行最多2次
            .transform(this::limitConsecutiveNewlines)
            // 4. 修剪两端
            .transform(ANY_WHITESPACE::trimFrom);
    }

    private String limitConsecutiveNewlines(String input) {
        return input.replaceAll("\n{3,}", "\n\n");
    }

    // ========== 测试入口 ==========
    public static void main(String[] args) {
        ContentCleaningService service = new ContentCleaningService();

        // 测试用例 1：空白标准化
        String input1 = "  \u3000  Hello   World  \t  \n  ";
        System.out.println("Original: [" + input1 + "]");
        System.out.println("Normalized: [" + service.normalize(input1) + "]");
        System.out.println();

        // 测试用例 2：提取电话号码
        String input2 = "Tel: +86 138-1234-5678";
        System.out.println("Phone input: " + input2);
        System.out.println("Extracted: " + service.extractPhoneNumber(input2));
        System.out.println();

        // 测试用例 3：敏感词检测
        List<String> sensitiveWords = Arrays.asList("违禁词", "敏感词");
        String input3 = "这 是 一 条 违 禁 词 的 测 试";
        System.out.println("Sensitive check: " + input3);
        System.out.println("Contains sensitive: " + service.containsSensitiveWord(input3, sensitiveWords));
        System.out.println();

        // 测试用例 4：多行格式化
        String input4 = "  \n\n  Line 1  \n  \n  \n  Line 2  \n  ";
        System.out.println("Multiline formatted:\n" + service.formatMultiline(input4));
    }
}
```

### 测试验证

```java
package com.example.guava.demo;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

import java.util.Arrays;
import java.util.List;

public class ContentCleaningServiceTest {

    private final ContentCleaningService service = new ContentCleaningService();

    @Test
    public void testNormalize_whitespace() {
        // 包含全角空格、制表符、普通空格
        String input = "  \u3000  Hello   World  \t  ";
        String result = service.normalize(input);
        assertEquals("hello world", result);
    }

    @Test
    public void testNormalize_controlChars() {
        // 包含控制字符
        String input = "Hello\u0000World\u0001";
        String result = service.normalize(input);
        assertEquals("helloworld", result);
    }

    @Test
    public void testExtractPhoneNumber() {
        assertEquals("13812345678", service.extractPhoneNumber("138-1234-5678"));
        assertEquals("13812345678", service.extractPhoneNumber("Tel: +86 138 1234 5678"));
        assertEquals("", service.extractPhoneNumber("No numbers here"));
    }

    @Test
    public void testRemovePunctuation() {
        assertEquals("Hello World", service.removePunctuation("Hello, World!"));
        assertEquals("Test 123", service.removePunctuation("Test@#$%123"));
    }

    @Test
    public void testCountValidChars() {
        assertEquals(10, service.countValidChars("Hello12345"));
        assertEquals(0, service.countValidChars("!@#$%"));
        assertEquals(5, service.countValidChars("Hi!123"));
    }

    @Test
    public void testIsAllWhitespace() {
        assertTrue(service.isAllWhitespace("   "));
        assertTrue(service.isAllWhitespace("\u3000\t\n"));
        assertFalse(service.isAllWhitespace("a"));
    }

    @Test
    public void testContainsChinese() {
        assertTrue(service.containsChinese("Hello世界"));
        assertTrue(service.containsChinese("中文"));
        assertFalse(service.containsChinese("Hello"));
    }

    @Test
    public void testFormatMultiline() {
        String input = "  Line 1  \n\n\n  Line 2  \n  ";
        String result = service.formatMultiline(input);
        assertEquals("Line 1\nLine 2", result);
    }

    @Test
    public void testSensitiveWordDetection() {
        List<String> sensitiveWords = Arrays.asList("违禁词", "敏感词");
        
        // 正常匹配
        assertTrue(service.containsSensitiveWord("这是一条违禁词", sensitiveWords));
        
        // 间隔字符绕过
        assertTrue(service.containsSensitiveWord("这 是 一 条 违 禁 词", sensitiveWords));
        
        // 大小写（中文无大小写，但测试标准化）
        assertTrue(service.containsSensitiveWord("违禁词", sensitiveWords));
        
        // 不包含
        assertFalse(service.containsSensitiveWord("正常内容", sensitiveWords));
    }

    @Test
    public void testExtractPlainText() {
        String input = "Hello, 世界! 123";
        String result = service.extractPlainText(input);
        assertEquals("hello 世界 123", result);
    }

    @Test
    public void testCleanProductDescription() {
        String input = "  \n\n  Product  \n  \n  \n  Description  \n  ";
        String result = service.cleanProductDescription(input);
        assertTrue(result.contains("Product"));
        assertTrue(result.contains("Description"));
        // 验证没有多余空行
        assertFalse(result.contains("\n\n\n"));
    }
}
```

### 可能遇到的坑及解决方法

| 坑点 | 现象 | 解决方法 |
|------|------|----------|
| Unicode 范围不全 | 生僻汉字匹配失败 | 扩展 `CharMatcher` 范围或使用 `Predicate` |
| `retainFrom` 性能 | 大字符串处理慢 | 预编译 `CharMatcher`，避免重复创建 |
| 正则和 CharMatcher 混用 | 行为不一致 | 统一使用 CharMatcher 处理字符级操作 |
| 全角半角转换 | 日文字符处理异常 | 使用 ICU4J 库处理复杂的 Unicode 转换 |

---

## 4 项目总结

### 优缺点对比

| 维度 | CharMatcher | 正则表达式 | 手工循环 |
|------|-------------|------------|----------|
| 可读性 | ★★★★★ 声明式 | ★★ 难维护 | ★★★ 直观但冗长 |
| 性能 | ★★★★★ 预编译 | ★★★ 一般 | ★★★★ 可控 |
| 灵活性 | ★★★★ 可组合 | ★★★★★ 强大 | ★★★★★ 完全可控 |
| null 安全 | ★★★ 需外部处理 | ★★ NPE 风险 | ★★★ 需外部处理 |
| Unicode 支持 | ★★★★ 良好 | ★★★★ 良好 | ★★★★ 依赖 Character API |

### 适用场景

1. **输入标准化**：移除控制字符、合并空白
2. **数据提取**：从混合文本中提取数字、字母
3. **内容过滤**：敏感词检测前的标准化处理
4. **格式验证**：检查是否全数字、全空白
5. **批量替换**：移除或替换特定类别字符

### 不适用场景

1. **复杂模式匹配**：如邮箱、URL 验证（用正则）
2. **多字符替换**：如全词替换（用正则或专门算法）
3. **复杂 Unicode 转换**：如繁简转换（用 ICU4J）

### 生产踩坑案例

**案例 1：未处理 null 输入**
```java
// 坑：null 导致 NPE
CharMatcher.digit().retainFrom(null);
```
解决：前置判空或使用 `Strings.nullToEmpty`。

**案例 2：CharMatcher 范围遗漏**
```java
// 坑：只处理了基本汉字，遗漏扩展区
CharMatcher.inRange('\u4e00', '\u9fa5');  // 不包括扩展A-G区
```
解决：使用 `Character.UnicodeBlock` 判断。

**案例 3：正则和 CharMatcher 混用导致重复处理**
```java
// 坑：CharMatcher 移除后，正则又处理一遍
String cleaned = CharMatcher.whitespace().removeFrom(input);
cleaned = cleaned.replaceAll("\\s+", "");  // 重复！
```
解决：统一使用一种方案。

### 思考题答案（第 5 章思考题 1）

> **问题**：`Splitter` 的 `limit(n)` 方法在什么场景下有用？

**答案**：当需要**保留剩余部分不进行分割**时使用。例如解析 `key=value1,value2,value3`，只想把第一个 `=` 作为分隔符，后面的逗号分隔的值作为一个整体：

```java
Splitter.on('=').limit(2).split("key=value1,value2,value3");
// 结果: ["key", "value1,value2,value3"]
```

### 新思考题

1. `CharMatcher` 和正则表达式的性能差异在什么量级？请设计一个实验验证。
2. 如果要支持 Emoji 字符的识别和处理，如何扩展 `CharMatcher`？

### 推广计划提示

**开发**：
- 输入接口统一调用标准化方法
- 敏感词检测前先标准化输入

**测试**：
- 边界字符测试（全角、Emoji、控制字符）
- 性能基准测试（大文本处理）

**运维**：
- 监控内容审核漏过率
- 建立敏感词库更新流程
