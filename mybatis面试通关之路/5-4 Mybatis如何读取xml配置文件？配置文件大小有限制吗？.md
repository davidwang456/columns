# Mybatis如何读取xml配置文件？配置文件大小有限制吗？

## 背景

> 小白：师傅，我们在Mybatis使用很多mapper.xml文件，Mybatis对能接受的文件大小或者文件个数有限制吗？
>
> 扫地僧：先卖个关子，平常我们使用的String类型长度有限制吗？如果有的话是多少？
>
> 小白：String不是可以随意的输入字符串吗？我记得我曾经使用String保存过文件的内容，没有出现过报错。
>
> 扫地僧：哈哈，有没有限制我们来让代码说话吧！

## String类型长度限制

要知道String的长度限制,我们就需要知道String是怎么存储字符串的，这就需要我们到源码里看看。

String其实是使用的一个char类型的数组来存储字符串中的字符的。  

```java
    /** The value is used for character storage. */
    private final char value[];
```

String计算长度的方法：

        /**
         * Returns the length of this string.
         * The length is equal to the number of <a href="Character.html#unicode">Unicode
         * code units</a> in the string.
         *
         * @return  the length of the sequence of characters represented by this
         *          object.
         */
    public int length() {
        return value.length;
    } 

可以看到字符串的最大长度为Integer的最大长度：

```JAVA
    /**
     * A constant holding the maximum value an {@code int} can
     * have, 2<sup>31</sup>-1.
     */
    @Native public static final int   MAX_VALUE = 0x7fffffff;
```

计算一下就是（2^31-1 = 2147483647 = 4GB）可真的如此吗？我们来验证一下。

将String.java类的内容读取到String类型中：

```java
package com.test.constants;

public class StringTest {

	public static void main(String[] args) {
		String string_java="/*\r\n" + 
				" * Copyright (c) 1994, 2013, Oracle and/or its affiliates. All rights reserved.\r\n" + 
				" * ORACLE PROPRIETARY/CONFIDENTIAL. Use is subject to license terms.\r\n" + 
				" *\r\n" + 
				" *\r\n" + 
				" *\r\n" + 
				" *\r\n" + 
				" *\r\n" + 
				" *\r\n" + 
				" *\r\n" + 
				" *\r\n" + 
				" *\r\n" + 
				" *\r\n" + 
				" *\r\n" + 
				" *\r\n" + 
				" *\r\n" + 
				" *\r\n" + 
				" *\r\n" + 
				" *\r\n" + 
				" *\r\n" + 
				" *\r\n" + 
				" *\r\n" + 
				" *\r\n" + 
				" */\r\n" + 
				"\r\n" + 
				"package java.lang;\r\n" + 
				"\r\n" + 
				"import java.io.ObjectStreamField;\r\n" + 
				"import java.io.UnsupportedEncodingException;\r\n" + 
				"import java.nio.charset.Charset;\r\n" + 
				"import java.util.ArrayList;\r\n" + 
				"import java.util.Arrays;\r\n" + 
				"import java.util.Comparator;\r\n" + 
				"import java.util.Formatter;\r\n" + 
				"import java.util.Locale;\r\n" + 
				"import java.util.Objects;\r\n" + 
				"import java.util.StringJoiner;\r\n" + 
				"import java.util.regex.Matcher;\r\n" + 
				"import java.util.regex.Pattern;\r\n" + 
				"import java.util.regex.PatternSyntaxException;\r\n" + 
				"\r\n" + 
				"/**\r\n" + 
				" * The {@code String} class represents character strings. All\r\n" + 
				" * string literals in Java programs, such as {@code \"abc\"}, are\r\n" + 
				" * implemented as instances of this class.\r\n" + 
				" * <p>\r\n" + 
				" * Strings are constant; their values cannot be changed after they\r\n" + 
				" * are created. String buffers support mutable strings.\r\n" + 
				" * Because String objects are immutable they can be shared. For example:\r\n" + 
				" * <blockquote><pre>\r\n" + 
				" *     String str = \"abc\";\r\n" + 
				" * </pre></blockquote><p>\r\n" + 
				" * is equivalent to:\r\n" + 
				" * <blockquote><pre>\r\n" + 
				" *     char data[] = {'a', 'b', 'c'};\r\n" + 
				" *     String str = new String(data);\r\n" + 
				" * </pre></blockquote><p>\r\n" + 
				" * Here are some more examples of how strings can be used:\r\n" + 
				" * <blockquote><pre>\r\n" + 
				" *     System.out.println(\"abc\");\r\n" + 
				" *     String cde = \"cde\";\r\n" + 
				" *     System.out.println(\"abc\" + cde);\r\n" + 
				" *     String c = \"abc\".substring(2,3);\r\n" + 
				" *     String d = cde.substring(1, 2);\r\n" + 
				" * </pre></blockquote>\r\n" + 
				" * <p>\r\n" + 
				" * The class {@code String} includes methods for examining\r\n" + 
				" * individual characters of the sequence, for comparing strings, for\r\n" + 
				" * searching strings, for extracting substrings, and for creating a\r\n" + 
				" * copy of a string with all characters translated to uppercase or to\r\n" + 
				" * lowercase. Case mapping is based on the Unicode Standard version\r\n" + 
				" * specified by the {@link java.lang.Character Character} class.\r\n" + 
				" * <p>\r\n" + 
				" * The Java language provides special support for the string\r\n" + 
				" * concatenation operator (&nbsp;+&nbsp;), and for conversion of\r\n" + 
				" * other objects to strings. String concatenation is implemented\r\n" + 
				" * through the {@code StringBuilder}(or {@code StringBuffer})\r\n" + 
				" * class and its {@code append} method.\r\n" + 
				" * String conversions are implemented through the method\r\n" + 
				" * {@code toString}, defined by {@code Object} and\r\n" + 
				" * inherited by all classes in Java. For additional information on\r\n" + 
				" * string concatenation and conversion, see Gosling, Joy, and Steele,\r\n" + 
				" * <i>The Java Language Specification</i>.\r\n" + 
				" *\r\n" + 
				" * <p> Unless otherwise noted, passing a <tt>null</tt> argument to a constructor\r\n" + 
				" * or method in this class will cause a {@link NullPointerException} to be\r\n" + 
				" * thrown.\r\n" + 
				" *\r\n" + 
				" * <p>A {@code String} represents a string in the UTF-16 format\r\n" + 
				" * in which <em>supplementary characters</em> are represented by <em>surrogate\r\n" + 
				" * pairs</em> (see the section <a href=\"Character.html#unicode\">Unicode\r\n" + 
				" * Character Representations</a> in the {@code Character} class for\r\n" + 
				" * more information).\r\n" + 
				" * Index values refer to {@code char} code units, so a supplementary\r\n" + 
				" * character uses two positions in a {@code String}.\r\n" + 
				" * <p>The {@code String} class provides methods for dealing with\r\n" + 
				" * Unicode code points (i.e., characters), in addition to those for\r\n" + 
				" * dealing with Unicode code units (i.e., {@code char} values).\r\n" + 
				" *\r\n" + 
				" * @author  Lee Boynton\r\n" + 
				" * @author  Arthur van Hoff\r\n" + 
				" * @author  Martin Buchholz\r\n" + 
				" * @author  Ulf Zibis\r\n" + 
				" * @see     java.lang.Object#toString()\r\n" + 
				" * @see     java.lang.StringBuffer\r\n" + 
				" * @see     java.lang.StringBuilder\r\n" + 
				" * @see     java.nio.charset.Charset\r\n" + 
				" * @since   JDK1.0\r\n" + 
				" */\r\n" + 
				"\r\n" + 
				"public final class String\r\n" + 
				"    implements java.io.Serializable, Comparable<String>, CharSequence {\r\n" + 
				"    /** The value is used for character storage. */\r\n" + 
				"    private final char value[];\r\n" + 
				"\r\n" + 
				"    /** Cache the hash code for the string */\r\n" + 
				"    private int hash; // Default to 0\r\n" + 
				"\r\n" + 
				"    /** use serialVersionUID from JDK 1.0.2 for interoperability */\r\n" + 
				"    private static final long serialVersionUID = -6849794470754667710L;\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Class String is special cased within the Serialization Stream Protocol.\r\n" + 
				"     *\r\n" + 
				"     * A String instance is written into an ObjectOutputStream according to\r\n" + 
				"     * <a href=\"{@docRoot}/../platform/serialization/spec/output.html\">\r\n" + 
				"     * Object Serialization Specification, Section 6.2, \"Stream Elements\"</a>\r\n" + 
				"     */\r\n" + 
				"    private static final ObjectStreamField[] serialPersistentFields =\r\n" + 
				"        new ObjectStreamField[0];\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Initializes a newly created {@code String} object so that it represents\r\n" + 
				"     * an empty character sequence.  Note that use of this constructor is\r\n" + 
				"     * unnecessary since Strings are immutable.\r\n" + 
				"     */\r\n" + 
				"    public String() {\r\n" + 
				"        this.value = \"\".value;\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Initializes a newly created {@code String} object so that it represents\r\n" + 
				"     * the same sequence of characters as the argument; in other words, the\r\n" + 
				"     * newly created string is a copy of the argument string. Unless an\r\n" + 
				"     * explicit copy of {@code original} is needed, use of this constructor is\r\n" + 
				"     * unnecessary since Strings are immutable.\r\n" + 
				"     *\r\n" + 
				"     * @param  original\r\n" + 
				"     *         A {@code String}\r\n" + 
				"     */\r\n" + 
				"    public String(String original) {\r\n" + 
				"        this.value = original.value;\r\n" + 
				"        this.hash = original.hash;\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Allocates a new {@code String} so that it represents the sequence of\r\n" + 
				"     * characters currently contained in the character array argument. The\r\n" + 
				"     * contents of the character array are copied; subsequent modification of\r\n" + 
				"     * the character array does not affect the newly created string.\r\n" + 
				"     *\r\n" + 
				"     * @param  value\r\n" + 
				"     *         The initial value of the string\r\n" + 
				"     */\r\n" + 
				"    public String(char value[]) {\r\n" + 
				"        this.value = Arrays.copyOf(value, value.length);\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Allocates a new {@code String} that contains characters from a subarray\r\n" + 
				"     * of the character array argument. The {@code offset} argument is the\r\n" + 
				"     * index of the first character of the subarray and the {@code count}\r\n" + 
				"     * argument specifies the length of the subarray. The contents of the\r\n" + 
				"     * subarray are copied; subsequent modification of the character array does\r\n" + 
				"     * not affect the newly created string.\r\n" + 
				"     *\r\n" + 
				"     * @param  value\r\n" + 
				"     *         Array that is the source of characters\r\n" + 
				"     *\r\n" + 
				"     * @param  offset\r\n" + 
				"     *         The initial offset\r\n" + 
				"     *\r\n" + 
				"     * @param  count\r\n" + 
				"     *         The length\r\n" + 
				"     *\r\n" + 
				"     * @throws  IndexOutOfBoundsException\r\n" + 
				"     *          If the {@code offset} and {@code count} arguments index\r\n" + 
				"     *          characters outside the bounds of the {@code value} array\r\n" + 
				"     */\r\n" + 
				"    public String(char value[], int offset, int count) {\r\n" + 
				"        if (offset < 0) {\r\n" + 
				"            throw new StringIndexOutOfBoundsException(offset);\r\n" + 
				"        }\r\n" + 
				"        if (count <= 0) {\r\n" + 
				"            if (count < 0) {\r\n" + 
				"                throw new StringIndexOutOfBoundsException(count);\r\n" + 
				"            }\r\n" + 
				"            if (offset <= value.length) {\r\n" + 
				"                this.value = \"\".value;\r\n" + 
				"                return;\r\n" + 
				"            }\r\n" + 
				"        }\r\n" + 
				"        // Note: offset or count might be near -1>>>1.\r\n" + 
				"        if (offset > value.length - count) {\r\n" + 
				"            throw new StringIndexOutOfBoundsException(offset + count);\r\n" + 
				"        }\r\n" + 
				"        this.value = Arrays.copyOfRange(value, offset, offset+count);\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Allocates a new {@code String} that contains characters from a subarray\r\n" + 
				"     * of the <a href=\"Character.html#unicode\">Unicode code point</a> array\r\n" + 
				"     * argument.  The {@code offset} argument is the index of the first code\r\n" + 
				"     * point of the subarray and the {@code count} argument specifies the\r\n" + 
				"     * length of the subarray.  The contents of the subarray are converted to\r\n" + 
				"     * {@code char}s; subsequent modification of the {@code int} array does not\r\n" + 
				"     * affect the newly created string.\r\n" + 
				"     *\r\n" + 
				"     * @param  codePoints\r\n" + 
				"     *         Array that is the source of Unicode code points\r\n" + 
				"     *\r\n" + 
				"     * @param  offset\r\n" + 
				"     *         The initial offset\r\n" + 
				"     *\r\n" + 
				"     * @param  count\r\n" + 
				"     *         The length\r\n" + 
				"     *\r\n" + 
				"     * @throws  IllegalArgumentException\r\n" + 
				"     *          If any invalid Unicode code point is found in {@code\r\n" + 
				"     *          codePoints}\r\n" + 
				"     *\r\n" + 
				"     * @throws  IndexOutOfBoundsException\r\n" + 
				"     *          If the {@code offset} and {@code count} arguments index\r\n" + 
				"     *          characters outside the bounds of the {@code codePoints} array\r\n" + 
				"     *\r\n" + 
				"     * @since  1.5\r\n" + 
				"     */\r\n" + 
				"    public String(int[] codePoints, int offset, int count) {\r\n" + 
				"        if (offset < 0) {\r\n" + 
				"            throw new StringIndexOutOfBoundsException(offset);\r\n" + 
				"        }\r\n" + 
				"        if (count <= 0) {\r\n" + 
				"            if (count < 0) {\r\n" + 
				"                throw new StringIndexOutOfBoundsException(count);\r\n" + 
				"            }\r\n" + 
				"            if (offset <= codePoints.length) {\r\n" + 
				"                this.value = \"\".value;\r\n" + 
				"                return;\r\n" + 
				"            }\r\n" + 
				"        }\r\n" + 
				"        // Note: offset or count might be near -1>>>1.\r\n" + 
				"        if (offset > codePoints.length - count) {\r\n" + 
				"            throw new StringIndexOutOfBoundsException(offset + count);\r\n" + 
				"        }\r\n" + 
				"\r\n" + 
				"        final int end = offset + count;\r\n" + 
				"\r\n" + 
				"        // Pass 1: Compute precise size of char[]\r\n" + 
				"        int n = count;\r\n" + 
				"        for (int i = offset; i < end; i++) {\r\n" + 
				"            int c = codePoints[i];\r\n" + 
				"            if (Character.isBmpCodePoint(c))\r\n" + 
				"                continue;\r\n" + 
				"            else if (Character.isValidCodePoint(c))\r\n" + 
				"                n++;\r\n" + 
				"            else throw new IllegalArgumentException(Integer.toString(c));\r\n" + 
				"        }\r\n" + 
				"\r\n" + 
				"        // Pass 2: Allocate and fill in char[]\r\n" + 
				"        final char[] v = new char[n];\r\n" + 
				"\r\n" + 
				"        for (int i = offset, j = 0; i < end; i++, j++) {\r\n" + 
				"            int c = codePoints[i];\r\n" + 
				"            if (Character.isBmpCodePoint(c))\r\n" + 
				"                v[j] = (char)c;\r\n" + 
				"            else\r\n" + 
				"                Character.toSurrogates(c, v, j++);\r\n" + 
				"        }\r\n" + 
				"\r\n" + 
				"        this.value = v;\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Allocates a new {@code String} constructed from a subarray of an array\r\n" + 
				"     * of 8-bit integer values.\r\n" + 
				"     *\r\n" + 
				"     * <p> The {@code offset} argument is the index of the first byte of the\r\n" + 
				"     * subarray, and the {@code count} argument specifies the length of the\r\n" + 
				"     * subarray.\r\n" + 
				"     *\r\n" + 
				"     * <p> Each {@code byte} in the subarray is converted to a {@code char} as\r\n" + 
				"     * specified in the method above.\r\n" + 
				"     *\r\n" + 
				"     * @deprecated This method does not properly convert bytes into characters.\r\n" + 
				"     * As of JDK&nbsp;1.1, the preferred way to do this is via the\r\n" + 
				"     * {@code String} constructors that take a {@link\r\n" + 
				"     * java.nio.charset.Charset}, charset name, or that use the platform's\r\n" + 
				"     * default charset.\r\n" + 
				"     *\r\n" + 
				"     * @param  ascii\r\n" + 
				"     *         The bytes to be converted to characters\r\n" + 
				"     *\r\n" + 
				"     * @param  hibyte\r\n" + 
				"     *         The top 8 bits of each 16-bit Unicode code unit\r\n" + 
				"     *\r\n" + 
				"     * @param  offset\r\n" + 
				"     *         The initial offset\r\n" + 
				"     * @param  count\r\n" + 
				"     *         The length\r\n" + 
				"     *\r\n" + 
				"     * @throws  IndexOutOfBoundsException\r\n" + 
				"     *          If the {@code offset} or {@code count} argument is invalid\r\n" + 
				"     *\r\n" + 
				"     * @see  #String(byte[], int)\r\n" + 
				"     * @see  #String(byte[], int, int, java.lang.String)\r\n" + 
				"     * @see  #String(byte[], int, int, java.nio.charset.Charset)\r\n" + 
				"     * @see  #String(byte[], int, int)\r\n" + 
				"     * @see  #String(byte[], java.lang.String)\r\n" + 
				"     * @see  #String(byte[], java.nio.charset.Charset)\r\n" + 
				"     * @see  #String(byte[])\r\n" + 
				"     */\r\n" + 
				"    @Deprecated\r\n" + 
				"    public String(byte ascii[], int hibyte, int offset, int count) {\r\n" + 
				"        checkBounds(ascii, offset, count);\r\n" + 
				"        char value[] = new char[count];\r\n" + 
				"\r\n" + 
				"        if (hibyte == 0) {\r\n" + 
				"            for (int i = count; i-- > 0;) {\r\n" + 
				"                value[i] = (char)(ascii[i + offset] & 0xff);\r\n" + 
				"            }\r\n" + 
				"        } else {\r\n" + 
				"            hibyte <<= 8;\r\n" + 
				"            for (int i = count; i-- > 0;) {\r\n" + 
				"                value[i] = (char)(hibyte | (ascii[i + offset] & 0xff));\r\n" + 
				"            }\r\n" + 
				"        }\r\n" + 
				"        this.value = value;\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Allocates a new {@code String} containing characters constructed from\r\n" + 
				"     * an array of 8-bit integer values. Each character <i>c</i>in the\r\n" + 
				"     * resulting string is constructed from the corresponding component\r\n" + 
				"     * <i>b</i> in the byte array such that:\r\n" + 
				"     *\r\n" + 
				"     * <blockquote><pre>\r\n" + 
				"     *     <b><i>c</i></b> == (char)(((hibyte &amp; 0xff) &lt;&lt; 8)\r\n" + 
				"     *                         | (<b><i>b</i></b> &amp; 0xff))\r\n" + 
				"     * </pre></blockquote>\r\n" + 
				"     *\r\n" + 
				"     * @deprecated  This method does not properly convert bytes into\r\n" + 
				"     * characters.  As of JDK&nbsp;1.1, the preferred way to do this is via the\r\n" + 
				"     * {@code String} constructors that take a {@link\r\n" + 
				"     * java.nio.charset.Charset}, charset name, or that use the platform's\r\n" + 
				"     * default charset.\r\n" + 
				"     *\r\n" + 
				"     * @param  ascii\r\n" + 
				"     *         The bytes to be converted to characters\r\n" + 
				"     *\r\n" + 
				"     * @param  hibyte\r\n" + 
				"     *         The top 8 bits of each 16-bit Unicode code unit\r\n" + 
				"     *\r\n" + 
				"     * @see  #String(byte[], int, int, java.lang.String)\r\n" + 
				"     * @see  #String(byte[], int, int, java.nio.charset.Charset)\r\n" + 
				"     * @see  #String(byte[], int, int)\r\n" + 
				"     * @see  #String(byte[], java.lang.String)\r\n" + 
				"     * @see  #String(byte[], java.nio.charset.Charset)\r\n" + 
				"     * @see  #String(byte[])\r\n" + 
				"     */\r\n" + 
				"    @Deprecated\r\n" + 
				"    public String(byte ascii[], int hibyte) {\r\n" + 
				"        this(ascii, hibyte, 0, ascii.length);\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /* Common private utility method used to bounds check the byte array\r\n" + 
				"     * and requested offset & length values used by the String(byte[],..)\r\n" + 
				"     * constructors.\r\n" + 
				"     */\r\n" + 
				"    private static void checkBounds(byte[] bytes, int offset, int length) {\r\n" + 
				"        if (length < 0)\r\n" + 
				"            throw new StringIndexOutOfBoundsException(length);\r\n" + 
				"        if (offset < 0)\r\n" + 
				"            throw new StringIndexOutOfBoundsException(offset);\r\n" + 
				"        if (offset > bytes.length - length)\r\n" + 
				"            throw new StringIndexOutOfBoundsException(offset + length);\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Constructs a new {@code String} by decoding the specified subarray of\r\n" + 
				"     * bytes using the specified charset.  The length of the new {@code String}\r\n" + 
				"     * is a function of the charset, and hence may not be equal to the length\r\n" + 
				"     * of the subarray.\r\n" + 
				"     *\r\n" + 
				"     * <p> The behavior of this constructor when the given bytes are not valid\r\n" + 
				"     * in the given charset is unspecified.  The {@link\r\n" + 
				"     * java.nio.charset.CharsetDecoder} class should be used when more control\r\n" + 
				"     * over the decoding process is required.\r\n" + 
				"     *\r\n" + 
				"     * @param  bytes\r\n" + 
				"     *         The bytes to be decoded into characters\r\n" + 
				"     *\r\n" + 
				"     * @param  offset\r\n" + 
				"     *         The index of the first byte to decode\r\n" + 
				"     *\r\n" + 
				"     * @param  length\r\n" + 
				"     *         The number of bytes to decode\r\n" + 
				"\r\n" + 
				"     * @param  charsetName\r\n" + 
				"     *         The name of a supported {@linkplain java.nio.charset.Charset\r\n" + 
				"     *         charset}\r\n" + 
				"     *\r\n" + 
				"     * @throws  UnsupportedEncodingException\r\n" + 
				"     *          If the named charset is not supported\r\n" + 
				"     *\r\n" + 
				"     * @throws  IndexOutOfBoundsException\r\n" + 
				"     *          If the {@code offset} and {@code length} arguments index\r\n" + 
				"     *          characters outside the bounds of the {@code bytes} array\r\n" + 
				"     *\r\n" + 
				"     * @since  JDK1.1\r\n" + 
				"     */\r\n" + 
				"    public String(byte bytes[], int offset, int length, String charsetName)\r\n" + 
				"            throws UnsupportedEncodingException {\r\n" + 
				"        if (charsetName == null)\r\n" + 
				"            throw new NullPointerException(\"charsetName\");\r\n" + 
				"        checkBounds(bytes, offset, length);\r\n" + 
				"        this.value = StringCoding.decode(charsetName, bytes, offset, length);\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Constructs a new {@code String} by decoding the specified subarray of\r\n" + 
				"     * bytes using the specified {@linkplain java.nio.charset.Charset charset}.\r\n" + 
				"     * The length of the new {@code String} is a function of the charset, and\r\n" + 
				"     * hence may not be equal to the length of the subarray.\r\n" + 
				"     *\r\n" + 
				"     * <p> This method always replaces malformed-input and unmappable-character\r\n" + 
				"     * sequences with this charset's default replacement string.  The {@link\r\n" + 
				"     * java.nio.charset.CharsetDecoder} class should be used when more control\r\n" + 
				"     * over the decoding process is required.\r\n" + 
				"     *\r\n" + 
				"     * @param  bytes\r\n" + 
				"     *         The bytes to be decoded into characters\r\n" + 
				"     *\r\n" + 
				"     * @param  offset\r\n" + 
				"     *         The index of the first byte to decode\r\n" + 
				"     *\r\n" + 
				"     * @param  length\r\n" + 
				"     *         The number of bytes to decode\r\n" + 
				"     *\r\n" + 
				"     * @param  charset\r\n" + 
				"     *         The {@linkplain java.nio.charset.Charset charset} to be used to\r\n" + 
				"     *         decode the {@code bytes}\r\n" + 
				"     *\r\n" + 
				"     * @throws  IndexOutOfBoundsException\r\n" + 
				"     *          If the {@code offset} and {@code length} arguments index\r\n" + 
				"     *          characters outside the bounds of the {@code bytes} array\r\n" + 
				"     *\r\n" + 
				"     * @since  1.6\r\n" + 
				"     */\r\n" + 
				"    public String(byte bytes[], int offset, int length, Charset charset) {\r\n" + 
				"        if (charset == null)\r\n" + 
				"            throw new NullPointerException(\"charset\");\r\n" + 
				"        checkBounds(bytes, offset, length);\r\n" + 
				"        this.value =  StringCoding.decode(charset, bytes, offset, length);\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Constructs a new {@code String} by decoding the specified array of bytes\r\n" + 
				"     * using the specified {@linkplain java.nio.charset.Charset charset}.  The\r\n" + 
				"     * length of the new {@code String} is a function of the charset, and hence\r\n" + 
				"     * may not be equal to the length of the byte array.\r\n" + 
				"     *\r\n" + 
				"     * <p> The behavior of this constructor when the given bytes are not valid\r\n" + 
				"     * in the given charset is unspecified.  The {@link\r\n" + 
				"     * java.nio.charset.CharsetDecoder} class should be used when more control\r\n" + 
				"     * over the decoding process is required.\r\n" + 
				"     *\r\n" + 
				"     * @param  bytes\r\n" + 
				"     *         The bytes to be decoded into characters\r\n" + 
				"     *\r\n" + 
				"     * @param  charsetName\r\n" + 
				"     *         The name of a supported {@linkplain java.nio.charset.Charset\r\n" + 
				"     *         charset}\r\n" + 
				"     *\r\n" + 
				"     * @throws  UnsupportedEncodingException\r\n" + 
				"     *          If the named charset is not supported\r\n" + 
				"     *\r\n" + 
				"     * @since  JDK1.1\r\n" + 
				"     */\r\n" + 
				"    public String(byte bytes[], String charsetName)\r\n" + 
				"            throws UnsupportedEncodingException {\r\n" + 
				"        this(bytes, 0, bytes.length, charsetName);\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Constructs a new {@code String} by decoding the specified array of\r\n" + 
				"     * bytes using the specified {@linkplain java.nio.charset.Charset charset}.\r\n" + 
				"     * The length of the new {@code String} is a function of the charset, and\r\n" + 
				"     * hence may not be equal to the length of the byte array.\r\n" + 
				"     *\r\n" + 
				"     * <p> This method always replaces malformed-input and unmappable-character\r\n" + 
				"     * sequences with this charset's default replacement string.  The {@link\r\n" + 
				"     * java.nio.charset.CharsetDecoder} class should be used when more control\r\n" + 
				"     * over the decoding process is required.\r\n" + 
				"     *\r\n" + 
				"     * @param  bytes\r\n" + 
				"     *         The bytes to be decoded into characters\r\n" + 
				"     *\r\n" + 
				"     * @param  charset\r\n" + 
				"     *         The {@linkplain java.nio.charset.Charset charset} to be used to\r\n" + 
				"     *         decode the {@code bytes}\r\n" + 
				"     *\r\n" + 
				"     * @since  1.6\r\n" + 
				"     */\r\n" + 
				"    public String(byte bytes[], Charset charset) {\r\n" + 
				"        this(bytes, 0, bytes.length, charset);\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Constructs a new {@code String} by decoding the specified subarray of\r\n" + 
				"     * bytes using the platform's default charset.  The length of the new\r\n" + 
				"     * {@code String} is a function of the charset, and hence may not be equal\r\n" + 
				"     * to the length of the subarray.\r\n" + 
				"     *\r\n" + 
				"     * <p> The behavior of this constructor when the given bytes are not valid\r\n" + 
				"     * in the default charset is unspecified.  The {@link\r\n" + 
				"     * java.nio.charset.CharsetDecoder} class should be used when more control\r\n" + 
				"     * over the decoding process is required.\r\n" + 
				"     *\r\n" + 
				"     * @param  bytes\r\n" + 
				"     *         The bytes to be decoded into characters\r\n" + 
				"     *\r\n" + 
				"     * @param  offset\r\n" + 
				"     *         The index of the first byte to decode\r\n" + 
				"     *\r\n" + 
				"     * @param  length\r\n" + 
				"     *         The number of bytes to decode\r\n" + 
				"     *\r\n" + 
				"     * @throws  IndexOutOfBoundsException\r\n" + 
				"     *          If the {@code offset} and the {@code length} arguments index\r\n" + 
				"     *          characters outside the bounds of the {@code bytes} array\r\n" + 
				"     *\r\n" + 
				"     * @since  JDK1.1\r\n" + 
				"     */\r\n" + 
				"    public String(byte bytes[], int offset, int length) {\r\n" + 
				"        checkBounds(bytes, offset, length);\r\n" + 
				"        this.value = StringCoding.decode(bytes, offset, length);\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Constructs a new {@code String} by decoding the specified array of bytes\r\n" + 
				"     * using the platform's default charset.  The length of the new {@code\r\n" + 
				"     * String} is a function of the charset, and hence may not be equal to the\r\n" + 
				"     * length of the byte array.\r\n" + 
				"     *\r\n" + 
				"     * <p> The behavior of this constructor when the given bytes are not valid\r\n" + 
				"     * in the default charset is unspecified.  The {@link\r\n" + 
				"     * java.nio.charset.CharsetDecoder} class should be used when more control\r\n" + 
				"     * over the decoding process is required.\r\n" + 
				"     *\r\n" + 
				"     * @param  bytes\r\n" + 
				"     *         The bytes to be decoded into characters\r\n" + 
				"     *\r\n" + 
				"     * @since  JDK1.1\r\n" + 
				"     */\r\n" + 
				"    public String(byte bytes[]) {\r\n" + 
				"        this(bytes, 0, bytes.length);\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Allocates a new string that contains the sequence of characters\r\n" + 
				"     * currently contained in the string buffer argument. The contents of the\r\n" + 
				"     * string buffer are copied; subsequent modification of the string buffer\r\n" + 
				"     * does not affect the newly created string.\r\n" + 
				"     *\r\n" + 
				"     * @param  buffer\r\n" + 
				"     *         A {@code StringBuffer}\r\n" + 
				"     */\r\n" + 
				"    public String(StringBuffer buffer) {\r\n" + 
				"        synchronized(buffer) {\r\n" + 
				"            this.value = Arrays.copyOf(buffer.getValue(), buffer.length());\r\n" + 
				"        }\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Allocates a new string that contains the sequence of characters\r\n" + 
				"     * currently contained in the string builder argument. The contents of the\r\n" + 
				"     * string builder are copied; subsequent modification of the string builder\r\n" + 
				"     * does not affect the newly created string.\r\n" + 
				"     *\r\n" + 
				"     * <p> This constructor is provided to ease migration to {@code\r\n" + 
				"     * StringBuilder}. Obtaining a string from a string builder via the {@code\r\n" + 
				"     * toString} method is likely to run faster and is generally preferred.\r\n" + 
				"     *\r\n" + 
				"     * @param   builder\r\n" + 
				"     *          A {@code StringBuilder}\r\n" + 
				"     *\r\n" + 
				"     * @since  1.5\r\n" + 
				"     */\r\n" + 
				"    public String(StringBuilder builder) {\r\n" + 
				"        this.value = Arrays.copyOf(builder.getValue(), builder.length());\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /*\r\n" + 
				"    * Package private constructor which shares value array for speed.\r\n" + 
				"    * this constructor is always expected to be called with share==true.\r\n" + 
				"    * a separate constructor is needed because we already have a public\r\n" + 
				"    * String(char[]) constructor that makes a copy of the given char[].\r\n" + 
				"    */\r\n" + 
				"    String(char[] value, boolean share) {\r\n" + 
				"        // assert share : \"unshared not supported\";\r\n" + 
				"        this.value = value;\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Returns the length of this string.\r\n" + 
				"     * The length is equal to the number of <a href=\"Character.html#unicode\">Unicode\r\n" + 
				"     * code units</a> in the string.\r\n" + 
				"     *\r\n" + 
				"     * @return  the length of the sequence of characters represented by this\r\n" + 
				"     *          object.\r\n" + 
				"     */\r\n" + 
				"    public int length() {\r\n" + 
				"        return value.length;\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Returns {@code true} if, and only if, {@link #length()} is {@code 0}.\r\n" + 
				"     *\r\n" + 
				"     * @return {@code true} if {@link #length()} is {@code 0}, otherwise\r\n" + 
				"     * {@code false}\r\n" + 
				"     *\r\n" + 
				"     * @since 1.6\r\n" + 
				"     */\r\n" + 
				"    public boolean isEmpty() {\r\n" + 
				"        return value.length == 0;\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Returns the {@code char} value at the\r\n" + 
				"     * specified index. An index ranges from {@code 0} to\r\n" + 
				"     * {@code length() - 1}. The first {@code char} value of the sequence\r\n" + 
				"     * is at index {@code 0}, the next at index {@code 1},\r\n" + 
				"     * and so on, as for array indexing.\r\n" + 
				"     *\r\n" + 
				"     * <p>If the {@code char} value specified by the index is a\r\n" + 
				"     * <a href=\"Character.html#unicode\">surrogate</a>, the surrogate\r\n" + 
				"     * value is returned.\r\n" + 
				"     *\r\n" + 
				"     * @param      index   the index of the {@code char} value.\r\n" + 
				"     * @return     the {@code char} value at the specified index of this string.\r\n" + 
				"     *             The first {@code char} value is at index {@code 0}.\r\n" + 
				"     * @exception  IndexOutOfBoundsException  if the {@code index}\r\n" + 
				"     *             argument is negative or not less than the length of this\r\n" + 
				"     *             string.\r\n" + 
				"     */\r\n" + 
				"    public char charAt(int index) {\r\n" + 
				"        if ((index < 0) || (index >= value.length)) {\r\n" + 
				"            throw new StringIndexOutOfBoundsException(index);\r\n" + 
				"        }\r\n" + 
				"        return value[index];\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Returns the character (Unicode code point) at the specified\r\n" + 
				"     * index. The index refers to {@code char} values\r\n" + 
				"     * (Unicode code units) and ranges from {@code 0} to\r\n" + 
				"     * {@link #length()}{@code  - 1}.\r\n" + 
				"     *\r\n" + 
				"     * <p> If the {@code char} value specified at the given index\r\n" + 
				"     * is in the high-surrogate range, the following index is less\r\n" + 
				"     * than the length of this {@code String}, and the\r\n" + 
				"     * {@code char} value at the following index is in the\r\n" + 
				"     * low-surrogate range, then the supplementary code point\r\n" + 
				"     * corresponding to this surrogate pair is returned. Otherwise,\r\n" + 
				"     * the {@code char} value at the given index is returned.\r\n" + 
				"     *\r\n" + 
				"     * @param      index the index to the {@code char} values\r\n" + 
				"     * @return     the code point value of the character at the\r\n" + 
				"     *             {@code index}\r\n" + 
				"     * @exception  IndexOutOfBoundsException  if the {@code index}\r\n" + 
				"     *             argument is negative or not less than the length of this\r\n" + 
				"     *             string.\r\n" + 
				"     * @since      1.5\r\n" + 
				"     */\r\n" + 
				"    public int codePointAt(int index) {\r\n" + 
				"        if ((index < 0) || (index >= value.length)) {\r\n" + 
				"            throw new StringIndexOutOfBoundsException(index);\r\n" + 
				"        }\r\n" + 
				"        return Character.codePointAtImpl(value, index, value.length);\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Returns the character (Unicode code point) before the specified\r\n" + 
				"     * index. The index refers to {@code char} values\r\n" + 
				"     * (Unicode code units) and ranges from {@code 1} to {@link\r\n" + 
				"     * CharSequence#length() length}.\r\n" + 
				"     *\r\n" + 
				"     * <p> If the {@code char} value at {@code (index - 1)}\r\n" + 
				"     * is in the low-surrogate range, {@code (index - 2)} is not\r\n" + 
				"     * negative, and the {@code char} value at {@code (index -\r\n" + 
				"     * 2)} is in the high-surrogate range, then the\r\n" + 
				"     * supplementary code point value of the surrogate pair is\r\n" + 
				"     * returned. If the {@code char} value at {@code index -\r\n" + 
				"     * 1} is an unpaired low-surrogate or a high-surrogate, the\r\n" + 
				"     * surrogate value is returned.\r\n" + 
				"     *\r\n" + 
				"     * @param     index the index following the code point that should be returned\r\n" + 
				"     * @return    the Unicode code point value before the given index.\r\n" + 
				"     * @exception IndexOutOfBoundsException if the {@code index}\r\n" + 
				"     *            argument is less than 1 or greater than the length\r\n" + 
				"     *            of this string.\r\n" + 
				"     * @since     1.5\r\n" + 
				"     */\r\n" + 
				"    public int codePointBefore(int index) {\r\n" + 
				"        int i = index - 1;\r\n" + 
				"        if ((i < 0) || (i >= value.length)) {\r\n" + 
				"            throw new StringIndexOutOfBoundsException(index);\r\n" + 
				"        }\r\n" + 
				"        return Character.codePointBeforeImpl(value, index, 0);\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Returns the number of Unicode code points in the specified text\r\n" + 
				"     * range of this {@code String}. The text range begins at the\r\n" + 
				"     * specified {@code beginIndex} and extends to the\r\n" + 
				"     * {@code char} at index {@code endIndex - 1}. Thus the\r\n" + 
				"     * length (in {@code char}s) of the text range is\r\n" + 
				"     * {@code endIndex-beginIndex}. Unpaired surrogates within\r\n" + 
				"     * the text range count as one code point each.\r\n" + 
				"     *\r\n" + 
				"     * @param beginIndex the index to the first {@code char} of\r\n" + 
				"     * the text range.\r\n" + 
				"     * @param endIndex the index after the last {@code char} of\r\n" + 
				"     * the text range.\r\n" + 
				"     * @return the number of Unicode code points in the specified text\r\n" + 
				"     * range\r\n" + 
				"     * @exception IndexOutOfBoundsException if the\r\n" + 
				"     * {@code beginIndex} is negative, or {@code endIndex}\r\n" + 
				"     * is larger than the length of this {@code String}, or\r\n" + 
				"     * {@code beginIndex} is larger than {@code endIndex}.\r\n" + 
				"     * @since  1.5\r\n" + 
				"     */\r\n" + 
				"    public int codePointCount(int beginIndex, int endIndex) {\r\n" + 
				"        if (beginIndex < 0 || endIndex > value.length || beginIndex > endIndex) {\r\n" + 
				"            throw new IndexOutOfBoundsException();\r\n" + 
				"        }\r\n" + 
				"        return Character.codePointCountImpl(value, beginIndex, endIndex - beginIndex);\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Returns the index within this {@code String} that is\r\n" + 
				"     * offset from the given {@code index} by\r\n" + 
				"     * {@code codePointOffset} code points. Unpaired surrogates\r\n" + 
				"     * within the text range given by {@code index} and\r\n" + 
				"     * {@code codePointOffset} count as one code point each.\r\n" + 
				"     *\r\n" + 
				"     * @param index the index to be offset\r\n" + 
				"     * @param codePointOffset the offset in code points\r\n" + 
				"     * @return the index within this {@code String}\r\n" + 
				"     * @exception IndexOutOfBoundsException if {@code index}\r\n" + 
				"     *   is negative or larger then the length of this\r\n" + 
				"     *   {@code String}, or if {@code codePointOffset} is positive\r\n" + 
				"     *   and the substring starting with {@code index} has fewer\r\n" + 
				"     *   than {@code codePointOffset} code points,\r\n" + 
				"     *   or if {@code codePointOffset} is negative and the substring\r\n" + 
				"     *   before {@code index} has fewer than the absolute value\r\n" + 
				"     *   of {@code codePointOffset} code points.\r\n" + 
				"     * @since 1.5\r\n" + 
				"     */\r\n" + 
				"    public int offsetByCodePoints(int index, int codePointOffset) {\r\n" + 
				"        if (index < 0 || index > value.length) {\r\n" + 
				"            throw new IndexOutOfBoundsException();\r\n" + 
				"        }\r\n" + 
				"        return Character.offsetByCodePointsImpl(value, 0, value.length,\r\n" + 
				"                index, codePointOffset);\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Copy characters from this string into dst starting at dstBegin.\r\n" + 
				"     * This method doesn't perform any range checking.\r\n" + 
				"     */\r\n" + 
				"    void getChars(char dst[], int dstBegin) {\r\n" + 
				"        System.arraycopy(value, 0, dst, dstBegin, value.length);\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Copies characters from this string into the destination character\r\n" + 
				"     * array.\r\n" + 
				"     * <p>\r\n" + 
				"     * The first character to be copied is at index {@code srcBegin};\r\n" + 
				"     * the last character to be copied is at index {@code srcEnd-1}\r\n" + 
				"     * (thus the total number of characters to be copied is\r\n" + 
				"     * {@code srcEnd-srcBegin}). The characters are copied into the\r\n" + 
				"     * subarray of {@code dst} starting at index {@code dstBegin}\r\n" + 
				"     * and ending at index:\r\n" + 
				"     * <blockquote><pre>\r\n" + 
				"     *     dstBegin + (srcEnd-srcBegin) - 1\r\n" + 
				"     * </pre></blockquote>\r\n" + 
				"     *\r\n" + 
				"     * @param      srcBegin   index of the first character in the string\r\n" + 
				"     *                        to copy.\r\n" + 
				"     * @param      srcEnd     index after the last character in the string\r\n" + 
				"     *                        to copy.\r\n" + 
				"     * @param      dst        the destination array.\r\n" + 
				"     * @param      dstBegin   the start offset in the destination array.\r\n" + 
				"     * @exception IndexOutOfBoundsException If any of the following\r\n" + 
				"     *            is true:\r\n" + 
				"     *            <ul><li>{@code srcBegin} is negative.\r\n" + 
				"     *            <li>{@code srcBegin} is greater than {@code srcEnd}\r\n" + 
				"     *            <li>{@code srcEnd} is greater than the length of this\r\n" + 
				"     *                string\r\n" + 
				"     *            <li>{@code dstBegin} is negative\r\n" + 
				"     *            <li>{@code dstBegin+(srcEnd-srcBegin)} is larger than\r\n" + 
				"     *                {@code dst.length}</ul>\r\n" + 
				"     */\r\n" + 
				"    public void getChars(int srcBegin, int srcEnd, char dst[], int dstBegin) {\r\n" + 
				"        if (srcBegin < 0) {\r\n" + 
				"            throw new StringIndexOutOfBoundsException(srcBegin);\r\n" + 
				"        }\r\n" + 
				"        if (srcEnd > value.length) {\r\n" + 
				"            throw new StringIndexOutOfBoundsException(srcEnd);\r\n" + 
				"        }\r\n" + 
				"        if (srcBegin > srcEnd) {\r\n" + 
				"            throw new StringIndexOutOfBoundsException(srcEnd - srcBegin);\r\n" + 
				"        }\r\n" + 
				"        System.arraycopy(value, srcBegin, dst, dstBegin, srcEnd - srcBegin);\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Copies characters from this string into the destination byte array. Each\r\n" + 
				"     * byte receives the 8 low-order bits of the corresponding character. The\r\n" + 
				"     * eight high-order bits of each character are not copied and do not\r\n" + 
				"     * participate in the transfer in any way.\r\n" + 
				"     *\r\n" + 
				"     * <p> The first character to be copied is at index {@code srcBegin}; the\r\n" + 
				"     * last character to be copied is at index {@code srcEnd-1}.  The total\r\n" + 
				"     * number of characters to be copied is {@code srcEnd-srcBegin}. The\r\n" + 
				"     * characters, converted to bytes, are copied into the subarray of {@code\r\n" + 
				"     * dst} starting at index {@code dstBegin} and ending at index:\r\n" + 
				"     *\r\n" + 
				"     * <blockquote><pre>\r\n" + 
				"     *     dstBegin + (srcEnd-srcBegin) - 1\r\n" + 
				"     * </pre></blockquote>\r\n" + 
				"     *\r\n" + 
				"     * @deprecated  This method does not properly convert characters into\r\n" + 
				"     * bytes.  As of JDK&nbsp;1.1, the preferred way to do this is via the\r\n" + 
				"     * {@link #getBytes()} method, which uses the platform's default charset.\r\n" + 
				"     *\r\n" + 
				"     * @param  srcBegin\r\n" + 
				"     *         Index of the first character in the string to copy\r\n" + 
				"     *\r\n" + 
				"     * @param  srcEnd\r\n" + 
				"     *         Index after the last character in the string to copy\r\n" + 
				"     *\r\n" + 
				"     * @param  dst\r\n" + 
				"     *         The destination array\r\n" + 
				"     *\r\n" + 
				"     * @param  dstBegin\r\n" + 
				"     *         The start offset in the destination array\r\n" + 
				"     *\r\n" + 
				"     * @throws  IndexOutOfBoundsException\r\n" + 
				"     *          If any of the following is true:\r\n" + 
				"     *          <ul>\r\n" + 
				"     *            <li> {@code srcBegin} is negative\r\n" + 
				"     *            <li> {@code srcBegin} is greater than {@code srcEnd}\r\n" + 
				"     *            <li> {@code srcEnd} is greater than the length of this String\r\n" + 
				"     *            <li> {@code dstBegin} is negative\r\n" + 
				"     *            <li> {@code dstBegin+(srcEnd-srcBegin)} is larger than {@code\r\n" + 
				"     *                 dst.length}\r\n" + 
				"     *          </ul>\r\n" + 
				"     */\r\n" + 
				"    @Deprecated\r\n" + 
				"    public void getBytes(int srcBegin, int srcEnd, byte dst[], int dstBegin) {\r\n" + 
				"        if (srcBegin < 0) {\r\n" + 
				"            throw new StringIndexOutOfBoundsException(srcBegin);\r\n" + 
				"        }\r\n" + 
				"        if (srcEnd > value.length) {\r\n" + 
				"            throw new StringIndexOutOfBoundsException(srcEnd);\r\n" + 
				"        }\r\n" + 
				"        if (srcBegin > srcEnd) {\r\n" + 
				"            throw new StringIndexOutOfBoundsException(srcEnd - srcBegin);\r\n" + 
				"        }\r\n" + 
				"        Objects.requireNonNull(dst);\r\n" + 
				"\r\n" + 
				"        int j = dstBegin;\r\n" + 
				"        int n = srcEnd;\r\n" + 
				"        int i = srcBegin;\r\n" + 
				"        char[] val = value;   /* avoid getfield opcode */\r\n" + 
				"\r\n" + 
				"        while (i < n) {\r\n" + 
				"            dst[j++] = (byte)val[i++];\r\n" + 
				"        }\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Encodes this {@code String} into a sequence of bytes using the named\r\n" + 
				"     * charset, storing the result into a new byte array.\r\n" + 
				"     *\r\n" + 
				"     * <p> The behavior of this method when this string cannot be encoded in\r\n" + 
				"     * the given charset is unspecified.  The {@link\r\n" + 
				"     * java.nio.charset.CharsetEncoder} class should be used when more control\r\n" + 
				"     * over the encoding process is required.\r\n" + 
				"     *\r\n" + 
				"     * @param  charsetName\r\n" + 
				"     *         The name of a supported {@linkplain java.nio.charset.Charset\r\n" + 
				"     *         charset}\r\n" + 
				"     *\r\n" + 
				"     * @return  The resultant byte array\r\n" + 
				"     *\r\n" + 
				"     * @throws  UnsupportedEncodingException\r\n" + 
				"     *          If the named charset is not supported\r\n" + 
				"     *\r\n" + 
				"     * @since  JDK1.1\r\n" + 
				"     */\r\n" + 
				"    public byte[] getBytes(String charsetName)\r\n" + 
				"            throws UnsupportedEncodingException {\r\n" + 
				"        if (charsetName == null) throw new NullPointerException();\r\n" + 
				"        return StringCoding.encode(charsetName, value, 0, value.length);\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Encodes this {@code String} into a sequence of bytes using the given\r\n" + 
				"     * {@linkplain java.nio.charset.Charset charset}, storing the result into a\r\n" + 
				"     * new byte array.\r\n" + 
				"     *\r\n" + 
				"     * <p> This method always replaces malformed-input and unmappable-character\r\n" + 
				"     * sequences with this charset's default replacement byte array.  The\r\n" + 
				"     * {@link java.nio.charset.CharsetEncoder} class should be used when more\r\n" + 
				"     * control over the encoding process is required.\r\n" + 
				"     *\r\n" + 
				"     * @param  charset\r\n" + 
				"     *         The {@linkplain java.nio.charset.Charset} to be used to encode\r\n" + 
				"     *         the {@code String}\r\n" + 
				"     *\r\n" + 
				"     * @return  The resultant byte array\r\n" + 
				"     *\r\n" + 
				"     * @since  1.6\r\n" + 
				"     */\r\n" + 
				"    public byte[] getBytes(Charset charset) {\r\n" + 
				"        if (charset == null) throw new NullPointerException();\r\n" + 
				"        return StringCoding.encode(charset, value, 0, value.length);\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Encodes this {@code String} into a sequence of bytes using the\r\n" + 
				"     * platform's default charset, storing the result into a new byte array.\r\n" + 
				"     *\r\n" + 
				"     * <p> The behavior of this method when this string cannot be encoded in\r\n" + 
				"     * the default charset is unspecified.  The {@link\r\n" + 
				"     * java.nio.charset.CharsetEncoder} class should be used when more control\r\n" + 
				"     * over the encoding process is required.\r\n" + 
				"     *\r\n" + 
				"     * @return  The resultant byte array\r\n" + 
				"     *\r\n" + 
				"     * @since      JDK1.1\r\n" + 
				"     */\r\n" + 
				"    public byte[] getBytes() {\r\n" + 
				"        return StringCoding.encode(value, 0, value.length);\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Compares this string to the specified object.  The result is {@code\r\n" + 
				"     * true} if and only if the argument is not {@code null} and is a {@code\r\n" + 
				"     * String} object that represents the same sequence of characters as this\r\n" + 
				"     * object.\r\n" + 
				"     *\r\n" + 
				"     * @param  anObject\r\n" + 
				"     *         The object to compare this {@code String} against\r\n" + 
				"     *\r\n" + 
				"     * @return  {@code true} if the given object represents a {@code String}\r\n" + 
				"     *          equivalent to this string, {@code false} otherwise\r\n" + 
				"     *\r\n" + 
				"     * @see  #compareTo(String)\r\n" + 
				"     * @see  #equalsIgnoreCase(String)\r\n" + 
				"     */\r\n" + 
				"    public boolean equals(Object anObject) {\r\n" + 
				"        if (this == anObject) {\r\n" + 
				"            return true;\r\n" + 
				"        }\r\n" + 
				"        if (anObject instanceof String) {\r\n" + 
				"            String anotherString = (String)anObject;\r\n" + 
				"            int n = value.length;\r\n" + 
				"            if (n == anotherString.value.length) {\r\n" + 
				"                char v1[] = value;\r\n" + 
				"                char v2[] = anotherString.value;\r\n" + 
				"                int i = 0;\r\n" + 
				"                while (n-- != 0) {\r\n" + 
				"                    if (v1[i] != v2[i])\r\n" + 
				"                        return false;\r\n" + 
				"                    i++;\r\n" + 
				"                }\r\n" + 
				"                return true;\r\n" + 
				"            }\r\n" + 
				"        }\r\n" + 
				"        return false;\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Compares this string to the specified {@code StringBuffer}.  The result\r\n" + 
				"     * is {@code true} if and only if this {@code String} represents the same\r\n" + 
				"     * sequence of characters as the specified {@code StringBuffer}. This method\r\n" + 
				"     * synchronizes on the {@code StringBuffer}.\r\n" + 
				"     *\r\n" + 
				"     * @param  sb\r\n" + 
				"     *         The {@code StringBuffer} to compare this {@code String} against\r\n" + 
				"     *\r\n" + 
				"     * @return  {@code true} if this {@code String} represents the same\r\n" + 
				"     *          sequence of characters as the specified {@code StringBuffer},\r\n" + 
				"     *          {@code false} otherwise\r\n" + 
				"     *\r\n" + 
				"     * @since  1.4\r\n" + 
				"     */\r\n" + 
				"    public boolean contentEquals(StringBuffer sb) {\r\n" + 
				"        return contentEquals((CharSequence)sb);\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    private boolean nonSyncContentEquals(AbstractStringBuilder sb) {\r\n" + 
				"        char v1[] = value;\r\n" + 
				"        char v2[] = sb.getValue();\r\n" + 
				"        int n = v1.length;\r\n" + 
				"        if (n != sb.length()) {\r\n" + 
				"            return false;\r\n" + 
				"        }\r\n" + 
				"        for (int i = 0; i < n; i++) {\r\n" + 
				"            if (v1[i] != v2[i]) {\r\n" + 
				"                return false;\r\n" + 
				"            }\r\n" + 
				"        }\r\n" + 
				"        return true;\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Compares this string to the specified {@code CharSequence}.  The\r\n" + 
				"     * result is {@code true} if and only if this {@code String} represents the\r\n" + 
				"     * same sequence of char values as the specified sequence. Note that if the\r\n" + 
				"     * {@code CharSequence} is a {@code StringBuffer} then the method\r\n" + 
				"     * synchronizes on it.\r\n" + 
				"     *\r\n" + 
				"     * @param  cs\r\n" + 
				"     *         The sequence to compare this {@code String} against\r\n" + 
				"     *\r\n" + 
				"     * @return  {@code true} if this {@code String} represents the same\r\n" + 
				"     *          sequence of char values as the specified sequence, {@code\r\n" + 
				"     *          false} otherwise\r\n" + 
				"     *\r\n" + 
				"     * @since  1.5\r\n" + 
				"     */\r\n" + 
				"    public boolean contentEquals(CharSequence cs) {\r\n" + 
				"        // Argument is a StringBuffer, StringBuilder\r\n" + 
				"        if (cs instanceof AbstractStringBuilder) {\r\n" + 
				"            if (cs instanceof StringBuffer) {\r\n" + 
				"                synchronized(cs) {\r\n" + 
				"                   return nonSyncContentEquals((AbstractStringBuilder)cs);\r\n" + 
				"                }\r\n" + 
				"            } else {\r\n" + 
				"                return nonSyncContentEquals((AbstractStringBuilder)cs);\r\n" + 
				"            }\r\n" + 
				"        }\r\n" + 
				"        // Argument is a String\r\n" + 
				"        if (cs instanceof String) {\r\n" + 
				"            return equals(cs);\r\n" + 
				"        }\r\n" + 
				"        // Argument is a generic CharSequence\r\n" + 
				"        char v1[] = value;\r\n" + 
				"        int n = v1.length;\r\n" + 
				"        if (n != cs.length()) {\r\n" + 
				"            return false;\r\n" + 
				"        }\r\n" + 
				"        for (int i = 0; i < n; i++) {\r\n" + 
				"            if (v1[i] != cs.charAt(i)) {\r\n" + 
				"                return false;\r\n" + 
				"            }\r\n" + 
				"        }\r\n" + 
				"        return true;\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Compares this {@code String} to another {@code String}, ignoring case\r\n" + 
				"     * considerations.  Two strings are considered equal ignoring case if they\r\n" + 
				"     * are of the same length and corresponding characters in the two strings\r\n" + 
				"     * are equal ignoring case.\r\n" + 
				"     *\r\n" + 
				"     * <p> Two characters {@code c1} and {@code c2} are considered the same\r\n" + 
				"     * ignoring case if at least one of the following is true:\r\n" + 
				"     * <ul>\r\n" + 
				"     *   <li> The two characters are the same (as compared by the\r\n" + 
				"     *        {@code ==} operator)\r\n" + 
				"     *   <li> Applying the method {@link\r\n" + 
				"     *        java.lang.Character#toUpperCase(char)} to each character\r\n" + 
				"     *        produces the same result\r\n" + 
				"     *   <li> Applying the method {@link\r\n" + 
				"     *        java.lang.Character#toLowerCase(char)} to each character\r\n" + 
				"     *        produces the same result\r\n" + 
				"     * </ul>\r\n" + 
				"     *\r\n" + 
				"     * @param  anotherString\r\n" + 
				"     *         The {@code String} to compare this {@code String} against\r\n" + 
				"     *\r\n" + 
				"     * @return  {@code true} if the argument is not {@code null} and it\r\n" + 
				"     *          represents an equivalent {@code String} ignoring case; {@code\r\n" + 
				"     *          false} otherwise\r\n" + 
				"     *\r\n" + 
				"     * @see  #equals(Object)\r\n" + 
				"     */\r\n" + 
				"    public boolean equalsIgnoreCase(String anotherString) {\r\n" + 
				"        return (this == anotherString) ? true\r\n" + 
				"                : (anotherString != null)\r\n" + 
				"                && (anotherString.value.length == value.length)\r\n" + 
				"                && regionMatches(true, 0, anotherString, 0, value.length);\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Compares two strings lexicographically.\r\n" + 
				"     * The comparison is based on the Unicode value of each character in\r\n" + 
				"     * the strings. The character sequence represented by this\r\n" + 
				"     * {@code String} object is compared lexicographically to the\r\n" + 
				"     * character sequence represented by the argument string. The result is\r\n" + 
				"     * a negative integer if this {@code String} object\r\n" + 
				"     * lexicographically precedes the argument string. The result is a\r\n" + 
				"     * positive integer if this {@code String} object lexicographically\r\n" + 
				"     * follows the argument string. The result is zero if the strings\r\n" + 
				"     * are equal; {@code compareTo} returns {@code 0} exactly when\r\n" + 
				"     * the {@link #equals(Object)} method would return {@code true}.\r\n" + 
				"     * <p>\r\n" + 
				"     * This is the definition of lexicographic ordering. If two strings are\r\n" + 
				"     * different, then either they have different characters at some index\r\n" + 
				"     * that is a valid index for both strings, or their lengths are different,\r\n" + 
				"     * or both. If they have different characters at one or more index\r\n" + 
				"     * positions, let <i>k</i> be the smallest such index; then the string\r\n" + 
				"     * whose character at position <i>k</i> has the smaller value, as\r\n" + 
				"     * determined by using the &lt; operator, lexicographically precedes the\r\n" + 
				"     * other string. In this case, {@code compareTo} returns the\r\n" + 
				"     * difference of the two character values at position {@code k} in\r\n" + 
				"     * the two string -- that is, the value:\r\n" + 
				"     * <blockquote><pre>\r\n" + 
				"     * this.charAt(k)-anotherString.charAt(k)\r\n" + 
				"     * </pre></blockquote>\r\n" + 
				"     * If there is no index position at which they differ, then the shorter\r\n" + 
				"     * string lexicographically precedes the longer string. In this case,\r\n" + 
				"     * {@code compareTo} returns the difference of the lengths of the\r\n" + 
				"     * strings -- that is, the value:\r\n" + 
				"     * <blockquote><pre>\r\n" + 
				"     * this.length()-anotherString.length()\r\n" + 
				"     * </pre></blockquote>\r\n" + 
				"     *\r\n" + 
				"     * @param   anotherString   the {@code String} to be compared.\r\n" + 
				"     * @return  the value {@code 0} if the argument string is equal to\r\n" + 
				"     *          this string; a value less than {@code 0} if this string\r\n" + 
				"     *          is lexicographically less than the string argument; and a\r\n" + 
				"     *          value greater than {@code 0} if this string is\r\n" + 
				"     *          lexicographically greater than the string argument.\r\n" + 
				"     */\r\n" + 
				"    public int compareTo(String anotherString) {\r\n" + 
				"        int len1 = value.length;\r\n" + 
				"        int len2 = anotherString.value.length;\r\n" + 
				"        int lim = Math.min(len1, len2);\r\n" + 
				"        char v1[] = value;\r\n" + 
				"        char v2[] = anotherString.value;\r\n" + 
				"\r\n" + 
				"        int k = 0;\r\n" + 
				"        while (k < lim) {\r\n" + 
				"            char c1 = v1[k];\r\n" + 
				"            char c2 = v2[k];\r\n" + 
				"            if (c1 != c2) {\r\n" + 
				"                return c1 - c2;\r\n" + 
				"            }\r\n" + 
				"            k++;\r\n" + 
				"        }\r\n" + 
				"        return len1 - len2;\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * A Comparator that orders {@code String} objects as by\r\n" + 
				"     * {@code compareToIgnoreCase}. This comparator is serializable.\r\n" + 
				"     * <p>\r\n" + 
				"     * Note that this Comparator does <em>not</em> take locale into account,\r\n" + 
				"     * and will result in an unsatisfactory ordering for certain locales.\r\n" + 
				"     * The java.text package provides <em>Collators</em> to allow\r\n" + 
				"     * locale-sensitive ordering.\r\n" + 
				"     *\r\n" + 
				"     * @see     java.text.Collator#compare(String, String)\r\n" + 
				"     * @since   1.2\r\n" + 
				"     */\r\n" + 
				"    public static final Comparator<String> CASE_INSENSITIVE_ORDER\r\n" + 
				"                                         = new CaseInsensitiveComparator();\r\n" + 
				"    private static class CaseInsensitiveComparator\r\n" + 
				"            implements Comparator<String>, java.io.Serializable {\r\n" + 
				"        // use serialVersionUID from JDK 1.2.2 for interoperability\r\n" + 
				"        private static final long serialVersionUID = 8575799808933029326L;\r\n" + 
				"\r\n" + 
				"        public int compare(String s1, String s2) {\r\n" + 
				"            int n1 = s1.length();\r\n" + 
				"            int n2 = s2.length();\r\n" + 
				"            int min = Math.min(n1, n2);\r\n" + 
				"            for (int i = 0; i < min; i++) {\r\n" + 
				"                char c1 = s1.charAt(i);\r\n" + 
				"                char c2 = s2.charAt(i);\r\n" + 
				"                if (c1 != c2) {\r\n" + 
				"                    c1 = Character.toUpperCase(c1);\r\n" + 
				"                    c2 = Character.toUpperCase(c2);\r\n" + 
				"                    if (c1 != c2) {\r\n" + 
				"                        c1 = Character.toLowerCase(c1);\r\n" + 
				"                        c2 = Character.toLowerCase(c2);\r\n" + 
				"                        if (c1 != c2) {\r\n" + 
				"                            // No overflow because of numeric promotion\r\n" + 
				"                            return c1 - c2;\r\n" + 
				"                        }\r\n" + 
				"                    }\r\n" + 
				"                }\r\n" + 
				"            }\r\n" + 
				"            return n1 - n2;\r\n" + 
				"        }\r\n" + 
				"\r\n" + 
				"        /** Replaces the de-serialized object. */\r\n" + 
				"        private Object readResolve() { return CASE_INSENSITIVE_ORDER; }\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Compares two strings lexicographically, ignoring case\r\n" + 
				"     * differences. This method returns an integer whose sign is that of\r\n" + 
				"     * calling {@code compareTo} with normalized versions of the strings\r\n" + 
				"     * where case differences have been eliminated by calling\r\n" + 
				"     * {@code Character.toLowerCase(Character.toUpperCase(character))} on\r\n" + 
				"     * each character.\r\n" + 
				"     * <p>\r\n" + 
				"     * Note that this method does <em>not</em> take locale into account,\r\n" + 
				"     * and will result in an unsatisfactory ordering for certain locales.\r\n" + 
				"     * The java.text package provides <em>collators</em> to allow\r\n" + 
				"     * locale-sensitive ordering.\r\n" + 
				"     *\r\n" + 
				"     * @param   str   the {@code String} to be compared.\r\n" + 
				"     * @return  a negative integer, zero, or a positive integer as the\r\n" + 
				"     *          specified String is greater than, equal to, or less\r\n" + 
				"     *          than this String, ignoring case considerations.\r\n" + 
				"     * @see     java.text.Collator#compare(String, String)\r\n" + 
				"     * @since   1.2\r\n" + 
				"     */\r\n" + 
				"    public int compareToIgnoreCase(String str) {\r\n" + 
				"        return CASE_INSENSITIVE_ORDER.compare(this, str);\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Tests if two string regions are equal.\r\n" + 
				"     * <p>\r\n" + 
				"     * A substring of this {@code String} object is compared to a substring\r\n" + 
				"     * of the argument other. The result is true if these substrings\r\n" + 
				"     * represent identical character sequences. The substring of this\r\n" + 
				"     * {@code String} object to be compared begins at index {@code toffset}\r\n" + 
				"     * and has length {@code len}. The substring of other to be compared\r\n" + 
				"     * begins at index {@code ooffset} and has length {@code len}. The\r\n" + 
				"     * result is {@code false} if and only if at least one of the following\r\n" + 
				"     * is true:\r\n" + 
				"     * <ul><li>{@code toffset} is negative.\r\n" + 
				"     * <li>{@code ooffset} is negative.\r\n" + 
				"     * <li>{@code toffset+len} is greater than the length of this\r\n" + 
				"     * {@code String} object.\r\n" + 
				"     * <li>{@code ooffset+len} is greater than the length of the other\r\n" + 
				"     * argument.\r\n" + 
				"     * <li>There is some nonnegative integer <i>k</i> less than {@code len}\r\n" + 
				"     * such that:\r\n" + 
				"     * {@code this.charAt(toffset + }<i>k</i>{@code ) != other.charAt(ooffset + }\r\n" + 
				"     * <i>k</i>{@code )}\r\n" + 
				"     * </ul>\r\n" + 
				"     *\r\n" + 
				"     * @param   toffset   the starting offset of the subregion in this string.\r\n" + 
				"     * @param   other     the string argument.\r\n" + 
				"     * @param   ooffset   the starting offset of the subregion in the string\r\n" + 
				"     *                    argument.\r\n" + 
				"     * @param   len       the number of characters to compare.\r\n" + 
				"     * @return  {@code true} if the specified subregion of this string\r\n" + 
				"     *          exactly matches the specified subregion of the string argument;\r\n" + 
				"     *          {@code false} otherwise.\r\n" + 
				"     */\r\n" + 
				"    public boolean regionMatches(int toffset, String other, int ooffset,\r\n" + 
				"            int len) {\r\n" + 
				"        char ta[] = value;\r\n" + 
				"        int to = toffset;\r\n" + 
				"        char pa[] = other.value;\r\n" + 
				"        int po = ooffset;\r\n" + 
				"        // Note: toffset, ooffset, or len might be near -1>>>1.\r\n" + 
				"        if ((ooffset < 0) || (toffset < 0)\r\n" + 
				"                || (toffset > (long)value.length - len)\r\n" + 
				"                || (ooffset > (long)other.value.length - len)) {\r\n" + 
				"            return false;\r\n" + 
				"        }\r\n" + 
				"        while (len-- > 0) {\r\n" + 
				"            if (ta[to++] != pa[po++]) {\r\n" + 
				"                return false;\r\n" + 
				"            }\r\n" + 
				"        }\r\n" + 
				"        return true;\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Tests if two string regions are equal.\r\n" + 
				"     * <p>\r\n" + 
				"     * A substring of this {@code String} object is compared to a substring\r\n" + 
				"     * of the argument {@code other}. The result is {@code true} if these\r\n" + 
				"     * substrings represent character sequences that are the same, ignoring\r\n" + 
				"     * case if and only if {@code ignoreCase} is true. The substring of\r\n" + 
				"     * this {@code String} object to be compared begins at index\r\n" + 
				"     * {@code toffset} and has length {@code len}. The substring of\r\n" + 
				"     * {@code other} to be compared begins at index {@code ooffset} and\r\n" + 
				"     * has length {@code len}. The result is {@code false} if and only if\r\n" + 
				"     * at least one of the following is true:\r\n" + 
				"     * <ul><li>{@code toffset} is negative.\r\n" + 
				"     * <li>{@code ooffset} is negative.\r\n" + 
				"     * <li>{@code toffset+len} is greater than the length of this\r\n" + 
				"     * {@code String} object.\r\n" + 
				"     * <li>{@code ooffset+len} is greater than the length of the other\r\n" + 
				"     * argument.\r\n" + 
				"     * <li>{@code ignoreCase} is {@code false} and there is some nonnegative\r\n" + 
				"     * integer <i>k</i> less than {@code len} such that:\r\n" + 
				"     * <blockquote><pre>\r\n" + 
				"     * this.charAt(toffset+k) != other.charAt(ooffset+k)\r\n" + 
				"     * </pre></blockquote>\r\n" + 
				"     * <li>{@code ignoreCase} is {@code true} and there is some nonnegative\r\n" + 
				"     * integer <i>k</i> less than {@code len} such that:\r\n" + 
				"     * <blockquote><pre>\r\n" + 
				"     * Character.toLowerCase(this.charAt(toffset+k)) !=\r\n" + 
				"     Character.toLowerCase(other.charAt(ooffset+k))\r\n" + 
				"     * </pre></blockquote>\r\n" + 
				"     * and:\r\n" + 
				"     * <blockquote><pre>\r\n" + 
				"     * Character.toUpperCase(this.charAt(toffset+k)) !=\r\n" + 
				"     *         Character.toUpperCase(other.charAt(ooffset+k))\r\n" + 
				"     * </pre></blockquote>\r\n" + 
				"     * </ul>\r\n" + 
				"     *\r\n" + 
				"     * @param   ignoreCase   if {@code true}, ignore case when comparing\r\n" + 
				"     *                       characters.\r\n" + 
				"     * @param   toffset      the starting offset of the subregion in this\r\n" + 
				"     *                       string.\r\n" + 
				"     * @param   other        the string argument.\r\n" + 
				"     * @param   ooffset      the starting offset of the subregion in the string\r\n" + 
				"     *                       argument.\r\n" + 
				"     * @param   len          the number of characters to compare.\r\n" + 
				"     * @return  {@code true} if the specified subregion of this string\r\n" + 
				"     *          matches the specified subregion of the string argument;\r\n" + 
				"     *          {@code false} otherwise. Whether the matching is exact\r\n" + 
				"     *          or case insensitive depends on the {@code ignoreCase}\r\n" + 
				"     *          argument.\r\n" + 
				"     */\r\n" + 
				"    public boolean regionMatches(boolean ignoreCase, int toffset,\r\n" + 
				"            String other, int ooffset, int len) {\r\n" + 
				"        char ta[] = value;\r\n" + 
				"        int to = toffset;\r\n" + 
				"        char pa[] = other.value;\r\n" + 
				"        int po = ooffset;\r\n" + 
				"        // Note: toffset, ooffset, or len might be near -1>>>1.\r\n" + 
				"        if ((ooffset < 0) || (toffset < 0)\r\n" + 
				"                || (toffset > (long)value.length - len)\r\n" + 
				"                || (ooffset > (long)other.value.length - len)) {\r\n" + 
				"            return false;\r\n" + 
				"        }\r\n" + 
				"        while (len-- > 0) {\r\n" + 
				"            char c1 = ta[to++];\r\n" + 
				"            char c2 = pa[po++];\r\n" + 
				"            if (c1 == c2) {\r\n" + 
				"                continue;\r\n" + 
				"            }\r\n" + 
				"            if (ignoreCase) {\r\n" + 
				"                // If characters don't match but case may be ignored,\r\n" + 
				"                // try converting both characters to uppercase.\r\n" + 
				"                // If the results match, then the comparison scan should\r\n" + 
				"                // continue.\r\n" + 
				"                char u1 = Character.toUpperCase(c1);\r\n" + 
				"                char u2 = Character.toUpperCase(c2);\r\n" + 
				"                if (u1 == u2) {\r\n" + 
				"                    continue;\r\n" + 
				"                }\r\n" + 
				"                // Unfortunately, conversion to uppercase does not work properly\r\n" + 
				"                // for the Georgian alphabet, which has strange rules about case\r\n" + 
				"                // conversion.  So we need to make one last check before\r\n" + 
				"                // exiting.\r\n" + 
				"                if (Character.toLowerCase(u1) == Character.toLowerCase(u2)) {\r\n" + 
				"                    continue;\r\n" + 
				"                }\r\n" + 
				"            }\r\n" + 
				"            return false;\r\n" + 
				"        }\r\n" + 
				"        return true;\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Tests if the substring of this string beginning at the\r\n" + 
				"     * specified index starts with the specified prefix.\r\n" + 
				"     *\r\n" + 
				"     * @param   prefix    the prefix.\r\n" + 
				"     * @param   toffset   where to begin looking in this string.\r\n" + 
				"     * @return  {@code true} if the character sequence represented by the\r\n" + 
				"     *          argument is a prefix of the substring of this object starting\r\n" + 
				"     *          at index {@code toffset}; {@code false} otherwise.\r\n" + 
				"     *          The result is {@code false} if {@code toffset} is\r\n" + 
				"     *          negative or greater than the length of this\r\n" + 
				"     *          {@code String} object; otherwise the result is the same\r\n" + 
				"     *          as the result of the expression\r\n" + 
				"     *          <pre>\r\n" + 
				"     *          this.substring(toffset).startsWith(prefix)\r\n" + 
				"     *          </pre>\r\n" + 
				"     */\r\n" + 
				"    public boolean startsWith(String prefix, int toffset) {\r\n" + 
				"        char ta[] = value;\r\n" + 
				"        int to = toffset;\r\n" + 
				"        char pa[] = prefix.value;\r\n" + 
				"        int po = 0;\r\n" + 
				"        int pc = prefix.value.length;\r\n" + 
				"        // Note: toffset might be near -1>>>1.\r\n" + 
				"        if ((toffset < 0) || (toffset > value.length - pc)) {\r\n" + 
				"            return false;\r\n" + 
				"        }\r\n" + 
				"        while (--pc >= 0) {\r\n" + 
				"            if (ta[to++] != pa[po++]) {\r\n" + 
				"                return false;\r\n" + 
				"            }\r\n" + 
				"        }\r\n" + 
				"        return true;\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Tests if this string starts with the specified prefix.\r\n" + 
				"     *\r\n" + 
				"     * @param   prefix   the prefix.\r\n" + 
				"     * @return  {@code true} if the character sequence represented by the\r\n" + 
				"     *          argument is a prefix of the character sequence represented by\r\n" + 
				"     *          this string; {@code false} otherwise.\r\n" + 
				"     *          Note also that {@code true} will be returned if the\r\n" + 
				"     *          argument is an empty string or is equal to this\r\n" + 
				"     *          {@code String} object as determined by the\r\n" + 
				"     *          {@link #equals(Object)} method.\r\n" + 
				"     * @since   1. 0\r\n" + 
				"     */\r\n" + 
				"    public boolean startsWith(String prefix) {\r\n" + 
				"        return startsWith(prefix, 0);\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Tests if this string ends with the specified suffix.\r\n" + 
				"     *\r\n" + 
				"     * @param   suffix   the suffix.\r\n" + 
				"     * @return  {@code true} if the character sequence represented by the\r\n" + 
				"     *          argument is a suffix of the character sequence represented by\r\n" + 
				"     *          this object; {@code false} otherwise. Note that the\r\n" + 
				"     *          result will be {@code true} if the argument is the\r\n" + 
				"     *          empty string or is equal to this {@code String} object\r\n" + 
				"     *          as determined by the {@link #equals(Object)} method.\r\n" + 
				"     */\r\n" + 
				"    public boolean endsWith(String suffix) {\r\n" + 
				"        return startsWith(suffix, value.length - suffix.value.length);\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Returns a hash code for this string. The hash code for a\r\n" + 
				"     * {@code String} object is computed as\r\n" + 
				"     * <blockquote><pre>\r\n" + 
				"     * s[0]*31^(n-1) + s[1]*31^(n-2) + ... + s[n-1]\r\n" + 
				"     * </pre></blockquote>\r\n" + 
				"     * using {@code int} arithmetic, where {@code s[i]} is the\r\n" + 
				"     * <i>i</i>th character of the string, {@code n} is the length of\r\n" + 
				"     * the string, and {@code ^} indicates exponentiation.\r\n" + 
				"     * (The hash value of the empty string is zero.)\r\n" + 
				"     *\r\n" + 
				"     * @return  a hash code value for this object.\r\n" + 
				"     */\r\n" + 
				"    public int hashCode() {\r\n" + 
				"        int h = hash;\r\n" + 
				"        if (h == 0 && value.length > 0) {\r\n" + 
				"            char val[] = value;\r\n" + 
				"\r\n" + 
				"            for (int i = 0; i < value.length; i++) {\r\n" + 
				"                h = 31 * h + val[i];\r\n" + 
				"            }\r\n" + 
				"            hash = h;\r\n" + 
				"        }\r\n" + 
				"        return h;\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Returns the index within this string of the first occurrence of\r\n" + 
				"     * the specified character. If a character with value\r\n" + 
				"     * {@code ch} occurs in the character sequence represented by\r\n" + 
				"     * this {@code String} object, then the index (in Unicode\r\n" + 
				"     * code units) of the first such occurrence is returned. For\r\n" + 
				"     * values of {@code ch} in the range from 0 to 0xFFFF\r\n" + 
				"     * (inclusive), this is the smallest value <i>k</i> such that:\r\n" + 
				"     * <blockquote><pre>\r\n" + 
				"     * this.charAt(<i>k</i>) == ch\r\n" + 
				"     * </pre></blockquote>\r\n" + 
				"     * is true. For other values of {@code ch}, it is the\r\n" + 
				"     * smallest value <i>k</i> such that:\r\n" + 
				"     * <blockquote><pre>\r\n" + 
				"     * this.codePointAt(<i>k</i>) == ch\r\n" + 
				"     * </pre></blockquote>\r\n" + 
				"     * is true. In either case, if no such character occurs in this\r\n" + 
				"     * string, then {@code -1} is returned.\r\n" + 
				"     *\r\n" + 
				"     * @param   ch   a character (Unicode code point).\r\n" + 
				"     * @return  the index of the first occurrence of the character in the\r\n" + 
				"     *          character sequence represented by this object, or\r\n" + 
				"     *          {@code -1} if the character does not occur.\r\n" + 
				"     */\r\n" + 
				"    public int indexOf(int ch) {\r\n" + 
				"        return indexOf(ch, 0);\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Returns the index within this string of the first occurrence of the\r\n" + 
				"     * specified character, starting the search at the specified index.\r\n" + 
				"     * <p>\r\n" + 
				"     * If a character with value {@code ch} occurs in the\r\n" + 
				"     * character sequence represented by this {@code String}\r\n" + 
				"     * object at an index no smaller than {@code fromIndex}, then\r\n" + 
				"     * the index of the first such occurrence is returned. For values\r\n" + 
				"     * of {@code ch} in the range from 0 to 0xFFFF (inclusive),\r\n" + 
				"     * this is the smallest value <i>k</i> such that:\r\n" + 
				"     * <blockquote><pre>\r\n" + 
				"     * (this.charAt(<i>k</i>) == ch) {@code &&} (<i>k</i> &gt;= fromIndex)\r\n" + 
				"     * </pre></blockquote>\r\n" + 
				"     * is true. For other values of {@code ch}, it is the\r\n" + 
				"     * smallest value <i>k</i> such that:\r\n" + 
				"     * <blockquote><pre>\r\n" + 
				"     * (this.codePointAt(<i>k</i>) == ch) {@code &&} (<i>k</i> &gt;= fromIndex)\r\n" + 
				"     * </pre></blockquote>\r\n" + 
				"     * is true. In either case, if no such character occurs in this\r\n" + 
				"     * string at or after position {@code fromIndex}, then\r\n" + 
				"     * {@code -1} is returned.\r\n" + 
				"     *\r\n" + 
				"     * <p>\r\n" + 
				"     * There is no restriction on the value of {@code fromIndex}. If it\r\n" + 
				"     * is negative, it has the same effect as if it were zero: this entire\r\n" + 
				"     * string may be searched. If it is greater than the length of this\r\n" + 
				"     * string, it has the same effect as if it were equal to the length of\r\n" + 
				"     * this string: {@code -1} is returned.\r\n" + 
				"     *\r\n" + 
				"     * <p>All indices are specified in {@code char} values\r\n" + 
				"     * (Unicode code units).\r\n" + 
				"     *\r\n" + 
				"     * @param   ch          a character (Unicode code point).\r\n" + 
				"     * @param   fromIndex   the index to start the search from.\r\n" + 
				"     * @return  the index of the first occurrence of the character in the\r\n" + 
				"     *          character sequence represented by this object that is greater\r\n" + 
				"     *          than or equal to {@code fromIndex}, or {@code -1}\r\n" + 
				"     *          if the character does not occur.\r\n" + 
				"     */\r\n" + 
				"    public int indexOf(int ch, int fromIndex) {\r\n" + 
				"        final int max = value.length;\r\n" + 
				"        if (fromIndex < 0) {\r\n" + 
				"            fromIndex = 0;\r\n" + 
				"        } else if (fromIndex >= max) {\r\n" + 
				"            // Note: fromIndex might be near -1>>>1.\r\n" + 
				"            return -1;\r\n" + 
				"        }\r\n" + 
				"\r\n" + 
				"        if (ch < Character.MIN_SUPPLEMENTARY_CODE_POINT) {\r\n" + 
				"            // handle most cases here (ch is a BMP code point or a\r\n" + 
				"            // negative value (invalid code point))\r\n" + 
				"            final char[] value = this.value;\r\n" + 
				"            for (int i = fromIndex; i < max; i++) {\r\n" + 
				"                if (value[i] == ch) {\r\n" + 
				"                    return i;\r\n" + 
				"                }\r\n" + 
				"            }\r\n" + 
				"            return -1;\r\n" + 
				"        } else {\r\n" + 
				"            return indexOfSupplementary(ch, fromIndex);\r\n" + 
				"        }\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Handles (rare) calls of indexOf with a supplementary character.\r\n" + 
				"     */\r\n" + 
				"    private int indexOfSupplementary(int ch, int fromIndex) {\r\n" + 
				"        if (Character.isValidCodePoint(ch)) {\r\n" + 
				"            final char[] value = this.value;\r\n" + 
				"            final char hi = Character.highSurrogate(ch);\r\n" + 
				"            final char lo = Character.lowSurrogate(ch);\r\n" + 
				"            final int max = value.length - 1;\r\n" + 
				"            for (int i = fromIndex; i < max; i++) {\r\n" + 
				"                if (value[i] == hi && value[i + 1] == lo) {\r\n" + 
				"                    return i;\r\n" + 
				"                }\r\n" + 
				"            }\r\n" + 
				"        }\r\n" + 
				"        return -1;\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Returns the index within this string of the last occurrence of\r\n" + 
				"     * the specified character. For values of {@code ch} in the\r\n" + 
				"     * range from 0 to 0xFFFF (inclusive), the index (in Unicode code\r\n" + 
				"     * units) returned is the largest value <i>k</i> such that:\r\n" + 
				"     * <blockquote><pre>\r\n" + 
				"     * this.charAt(<i>k</i>) == ch\r\n" + 
				"     * </pre></blockquote>\r\n" + 
				"     * is true. For other values of {@code ch}, it is the\r\n" + 
				"     * largest value <i>k</i> such that:\r\n" + 
				"     * <blockquote><pre>\r\n" + 
				"     * this.codePointAt(<i>k</i>) == ch\r\n" + 
				"     * </pre></blockquote>\r\n" + 
				"     * is true.  In either case, if no such character occurs in this\r\n" + 
				"     * string, then {@code -1} is returned.  The\r\n" + 
				"     * {@code String} is searched backwards starting at the last\r\n" + 
				"     * character.\r\n" + 
				"     *\r\n" + 
				"     * @param   ch   a character (Unicode code point).\r\n" + 
				"     * @return  the index of the last occurrence of the character in the\r\n" + 
				"     *          character sequence represented by this object, or\r\n" + 
				"     *          {@code -1} if the character does not occur.\r\n" + 
				"     */\r\n" + 
				"    public int lastIndexOf(int ch) {\r\n" + 
				"        return lastIndexOf(ch, value.length - 1);\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Returns the index within this string of the last occurrence of\r\n" + 
				"     * the specified character, searching backward starting at the\r\n" + 
				"     * specified index. For values of {@code ch} in the range\r\n" + 
				"     * from 0 to 0xFFFF (inclusive), the index returned is the largest\r\n" + 
				"     * value <i>k</i> such that:\r\n" + 
				"     * <blockquote><pre>\r\n" + 
				"     * (this.charAt(<i>k</i>) == ch) {@code &&} (<i>k</i> &lt;= fromIndex)\r\n" + 
				"     * </pre></blockquote>\r\n" + 
				"     * is true. For other values of {@code ch}, it is the\r\n" + 
				"     * largest value <i>k</i> such that:\r\n" + 
				"     * <blockquote><pre>\r\n" + 
				"     * (this.codePointAt(<i>k</i>) == ch) {@code &&} (<i>k</i> &lt;= fromIndex)\r\n" + 
				"     * </pre></blockquote>\r\n" + 
				"     * is true. In either case, if no such character occurs in this\r\n" + 
				"     * string at or before position {@code fromIndex}, then\r\n" + 
				"     * {@code -1} is returned.\r\n" + 
				"     *\r\n" + 
				"     * <p>All indices are specified in {@code char} values\r\n" + 
				"     * (Unicode code units).\r\n" + 
				"     *\r\n" + 
				"     * @param   ch          a character (Unicode code point).\r\n" + 
				"     * @param   fromIndex   the index to start the search from. There is no\r\n" + 
				"     *          restriction on the value of {@code fromIndex}. If it is\r\n" + 
				"     *          greater than or equal to the length of this string, it has\r\n" + 
				"     *          the same effect as if it were equal to one less than the\r\n" + 
				"     *          length of this string: this entire string may be searched.\r\n" + 
				"     *          If it is negative, it has the same effect as if it were -1:\r\n" + 
				"     *          -1 is returned.\r\n" + 
				"     * @return  the index of the last occurrence of the character in the\r\n" + 
				"     *          character sequence represented by this object that is less\r\n" + 
				"     *          than or equal to {@code fromIndex}, or {@code -1}\r\n" + 
				"     *          if the character does not occur before that point.\r\n" + 
				"     */\r\n" + 
				"    public int lastIndexOf(int ch, int fromIndex) {\r\n" + 
				"        if (ch < Character.MIN_SUPPLEMENTARY_CODE_POINT) {\r\n" + 
				"            // handle most cases here (ch is a BMP code point or a\r\n" + 
				"            // negative value (invalid code point))\r\n" + 
				"            final char[] value = this.value;\r\n" + 
				"            int i = Math.min(fromIndex, value.length - 1);\r\n" + 
				"            for (; i >= 0; i--) {\r\n" + 
				"                if (value[i] == ch) {\r\n" + 
				"                    return i;\r\n" + 
				"                }\r\n" + 
				"            }\r\n" + 
				"            return -1;\r\n" + 
				"        } else {\r\n" + 
				"            return lastIndexOfSupplementary(ch, fromIndex);\r\n" + 
				"        }\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Handles (rare) calls of lastIndexOf with a supplementary character.\r\n" + 
				"     */\r\n" + 
				"    private int lastIndexOfSupplementary(int ch, int fromIndex) {\r\n" + 
				"        if (Character.isValidCodePoint(ch)) {\r\n" + 
				"            final char[] value = this.value;\r\n" + 
				"            char hi = Character.highSurrogate(ch);\r\n" + 
				"            char lo = Character.lowSurrogate(ch);\r\n" + 
				"            int i = Math.min(fromIndex, value.length - 2);\r\n" + 
				"            for (; i >= 0; i--) {\r\n" + 
				"                if (value[i] == hi && value[i + 1] == lo) {\r\n" + 
				"                    return i;\r\n" + 
				"                }\r\n" + 
				"            }\r\n" + 
				"        }\r\n" + 
				"        return -1;\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Returns the index within this string of the first occurrence of the\r\n" + 
				"     * specified substring.\r\n" + 
				"     *\r\n" + 
				"     * <p>The returned index is the smallest value <i>k</i> for which:\r\n" + 
				"     * <blockquote><pre>\r\n" + 
				"     * this.startsWith(str, <i>k</i>)\r\n" + 
				"     * </pre></blockquote>\r\n" + 
				"     * If no such value of <i>k</i> exists, then {@code -1} is returned.\r\n" + 
				"     *\r\n" + 
				"     * @param   str   the substring to search for.\r\n" + 
				"     * @return  the index of the first occurrence of the specified substring,\r\n" + 
				"     *          or {@code -1} if there is no such occurrence.\r\n" + 
				"     */\r\n" + 
				"    public int indexOf(String str) {\r\n" + 
				"        return indexOf(str, 0);\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Returns the index within this string of the first occurrence of the\r\n" + 
				"     * specified substring, starting at the specified index.\r\n" + 
				"     *\r\n" + 
				"     * <p>The returned index is the smallest value <i>k</i> for which:\r\n" + 
				"     * <blockquote><pre>\r\n" + 
				"     * <i>k</i> &gt;= fromIndex {@code &&} this.startsWith(str, <i>k</i>)\r\n" + 
				"     * </pre></blockquote>\r\n" + 
				"     * If no such value of <i>k</i> exists, then {@code -1} is returned.\r\n" + 
				"     *\r\n" + 
				"     * @param   str         the substring to search for.\r\n" + 
				"     * @param   fromIndex   the index from which to start the search.\r\n" + 
				"     * @return  the index of the first occurrence of the specified substring,\r\n" + 
				"     *          starting at the specified index,\r\n" + 
				"     *          or {@code -1} if there is no such occurrence.\r\n" + 
				"     */\r\n" + 
				"    public int indexOf(String str, int fromIndex) {\r\n" + 
				"        return indexOf(value, 0, value.length,\r\n" + 
				"                str.value, 0, str.value.length, fromIndex);\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Code shared by String and AbstractStringBuilder to do searches. The\r\n" + 
				"     * source is the character array being searched, and the target\r\n" + 
				"     * is the string being searched for.\r\n" + 
				"     *\r\n" + 
				"     * @param   source       the characters being searched.\r\n" + 
				"     * @param   sourceOffset offset of the source string.\r\n" + 
				"     * @param   sourceCount  count of the source string.\r\n" + 
				"     * @param   target       the characters being searched for.\r\n" + 
				"     * @param   fromIndex    the index to begin searching from.\r\n" + 
				"     */\r\n" + 
				"    static int indexOf(char[] source, int sourceOffset, int sourceCount,\r\n" + 
				"            String target, int fromIndex) {\r\n" + 
				"        return indexOf(source, sourceOffset, sourceCount,\r\n" + 
				"                       target.value, 0, target.value.length,\r\n" + 
				"                       fromIndex);\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Code shared by String and StringBuffer to do searches. The\r\n" + 
				"     * source is the character array being searched, and the target\r\n" + 
				"     * is the string being searched for.\r\n" + 
				"     *\r\n" + 
				"     * @param   source       the characters being searched.\r\n" + 
				"     * @param   sourceOffset offset of the source string.\r\n" + 
				"     * @param   sourceCount  count of the source string.\r\n" + 
				"     * @param   target       the characters being searched for.\r\n" + 
				"     * @param   targetOffset offset of the target string.\r\n" + 
				"     * @param   targetCount  count of the target string.\r\n" + 
				"     * @param   fromIndex    the index to begin searching from.\r\n" + 
				"     */\r\n" + 
				"    static int indexOf(char[] source, int sourceOffset, int sourceCount,\r\n" + 
				"            char[] target, int targetOffset, int targetCount,\r\n" + 
				"            int fromIndex) {\r\n" + 
				"        if (fromIndex >= sourceCount) {\r\n" + 
				"            return (targetCount == 0 ? sourceCount : -1);\r\n" + 
				"        }\r\n" + 
				"        if (fromIndex < 0) {\r\n" + 
				"            fromIndex = 0;\r\n" + 
				"        }\r\n" + 
				"        if (targetCount == 0) {\r\n" + 
				"            return fromIndex;\r\n" + 
				"        }\r\n" + 
				"\r\n" + 
				"        char first = target[targetOffset];\r\n" + 
				"        int max = sourceOffset + (sourceCount - targetCount);\r\n" + 
				"\r\n" + 
				"        for (int i = sourceOffset + fromIndex; i <= max; i++) {\r\n" + 
				"            /* Look for first character. */\r\n" + 
				"            if (source[i] != first) {\r\n" + 
				"                while (++i <= max && source[i] != first);\r\n" + 
				"            }\r\n" + 
				"\r\n" + 
				"            /* Found first character, now look at the rest of v2 */\r\n" + 
				"            if (i <= max) {\r\n" + 
				"                int j = i + 1;\r\n" + 
				"                int end = j + targetCount - 1;\r\n" + 
				"                for (int k = targetOffset + 1; j < end && source[j]\r\n" + 
				"                        == target[k]; j++, k++);\r\n" + 
				"\r\n" + 
				"                if (j == end) {\r\n" + 
				"                    /* Found whole string. */\r\n" + 
				"                    return i - sourceOffset;\r\n" + 
				"                }\r\n" + 
				"            }\r\n" + 
				"        }\r\n" + 
				"        return -1;\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Returns the index within this string of the last occurrence of the\r\n" + 
				"     * specified substring.  The last occurrence of the empty string \"\"\r\n" + 
				"     * is considered to occur at the index value {@code this.length()}.\r\n" + 
				"     *\r\n" + 
				"     * <p>The returned index is the largest value <i>k</i> for which:\r\n" + 
				"     * <blockquote><pre>\r\n" + 
				"     * this.startsWith(str, <i>k</i>)\r\n" + 
				"     * </pre></blockquote>\r\n" + 
				"     * If no such value of <i>k</i> exists, then {@code -1} is returned.\r\n" + 
				"     *\r\n" + 
				"     * @param   str   the substring to search for.\r\n" + 
				"     * @return  the index of the last occurrence of the specified substring,\r\n" + 
				"     *          or {@code -1} if there is no such occurrence.\r\n" + 
				"     */\r\n" + 
				"    public int lastIndexOf(String str) {\r\n" + 
				"        return lastIndexOf(str, value.length);\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Returns the index within this string of the last occurrence of the\r\n" + 
				"     * specified substring, searching backward starting at the specified index.\r\n" + 
				"     *\r\n" + 
				"     * <p>The returned index is the largest value <i>k</i> for which:\r\n" + 
				"     * <blockquote><pre>\r\n" + 
				"     * <i>k</i> {@code <=} fromIndex {@code &&} this.startsWith(str, <i>k</i>)\r\n" + 
				"     * </pre></blockquote>\r\n" + 
				"     * If no such value of <i>k</i> exists, then {@code -1} is returned.\r\n" + 
				"     *\r\n" + 
				"     * @param   str         the substring to search for.\r\n" + 
				"     * @param   fromIndex   the index to start the search from.\r\n" + 
				"     * @return  the index of the last occurrence of the specified substring,\r\n" + 
				"     *          searching backward from the specified index,\r\n" + 
				"     *          or {@code -1} if there is no such occurrence.\r\n" + 
				"     */\r\n" + 
				"    public int lastIndexOf(String str, int fromIndex) {\r\n" + 
				"        return lastIndexOf(value, 0, value.length,\r\n" + 
				"                str.value, 0, str.value.length, fromIndex);\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Code shared by String and AbstractStringBuilder to do searches. The\r\n" + 
				"     * source is the character array being searched, and the target\r\n" + 
				"     * is the string being searched for.\r\n" + 
				"     *\r\n" + 
				"     * @param   source       the characters being searched.\r\n" + 
				"     * @param   sourceOffset offset of the source string.\r\n" + 
				"     * @param   sourceCount  count of the source string.\r\n" + 
				"     * @param   target       the characters being searched for.\r\n" + 
				"     * @param   fromIndex    the index to begin searching from.\r\n" + 
				"     */\r\n" + 
				"    static int lastIndexOf(char[] source, int sourceOffset, int sourceCount,\r\n" + 
				"            String target, int fromIndex) {\r\n" + 
				"        return lastIndexOf(source, sourceOffset, sourceCount,\r\n" + 
				"                       target.value, 0, target.value.length,\r\n" + 
				"                       fromIndex);\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Code shared by String and StringBuffer to do searches. The\r\n" + 
				"     * source is the character array being searched, and the target\r\n" + 
				"     * is the string being searched for.\r\n" + 
				"     *\r\n" + 
				"     * @param   source       the characters being searched.\r\n" + 
				"     * @param   sourceOffset offset of the source string.\r\n" + 
				"     * @param   sourceCount  count of the source string.\r\n" + 
				"     * @param   target       the characters being searched for.\r\n" + 
				"     * @param   targetOffset offset of the target string.\r\n" + 
				"     * @param   targetCount  count of the target string.\r\n" + 
				"     * @param   fromIndex    the index to begin searching from.\r\n" + 
				"     */\r\n" + 
				"    static int lastIndexOf(char[] source, int sourceOffset, int sourceCount,\r\n" + 
				"            char[] target, int targetOffset, int targetCount,\r\n" + 
				"            int fromIndex) {\r\n" + 
				"        /*\r\n" + 
				"         * Check arguments; return immediately where possible. For\r\n" + 
				"         * consistency, don't check for null str.\r\n" + 
				"         */\r\n" + 
				"        int rightIndex = sourceCount - targetCount;\r\n" + 
				"        if (fromIndex < 0) {\r\n" + 
				"            return -1;\r\n" + 
				"        }\r\n" + 
				"        if (fromIndex > rightIndex) {\r\n" + 
				"            fromIndex = rightIndex;\r\n" + 
				"        }\r\n" + 
				"        /* Empty string always matches. */\r\n" + 
				"        if (targetCount == 0) {\r\n" + 
				"            return fromIndex;\r\n" + 
				"        }\r\n" + 
				"\r\n" + 
				"        int strLastIndex = targetOffset + targetCount - 1;\r\n" + 
				"        char strLastChar = target[strLastIndex];\r\n" + 
				"        int min = sourceOffset + targetCount - 1;\r\n" + 
				"        int i = min + fromIndex;\r\n" + 
				"\r\n" + 
				"    startSearchForLastChar:\r\n" + 
				"        while (true) {\r\n" + 
				"            while (i >= min && source[i] != strLastChar) {\r\n" + 
				"                i--;\r\n" + 
				"            }\r\n" + 
				"            if (i < min) {\r\n" + 
				"                return -1;\r\n" + 
				"            }\r\n" + 
				"            int j = i - 1;\r\n" + 
				"            int start = j - (targetCount - 1);\r\n" + 
				"            int k = strLastIndex - 1;\r\n" + 
				"\r\n" + 
				"            while (j > start) {\r\n" + 
				"                if (source[j--] != target[k--]) {\r\n" + 
				"                    i--;\r\n" + 
				"                    continue startSearchForLastChar;\r\n" + 
				"                }\r\n" + 
				"            }\r\n" + 
				"            return start - sourceOffset + 1;\r\n" + 
				"        }\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Returns a string that is a substring of this string. The\r\n" + 
				"     * substring begins with the character at the specified index and\r\n" + 
				"     * extends to the end of this string. <p>\r\n" + 
				"     * Examples:\r\n" + 
				"     * <blockquote><pre>\r\n" + 
				"     * \"unhappy\".substring(2) returns \"happy\"\r\n" + 
				"     * \"Harbison\".substring(3) returns \"bison\"\r\n" + 
				"     * \"emptiness\".substring(9) returns \"\" (an empty string)\r\n" + 
				"     * </pre></blockquote>\r\n" + 
				"     *\r\n" + 
				"     * @param      beginIndex   the beginning index, inclusive.\r\n" + 
				"     * @return     the specified substring.\r\n" + 
				"     * @exception  IndexOutOfBoundsException  if\r\n" + 
				"     *             {@code beginIndex} is negative or larger than the\r\n" + 
				"     *             length of this {@code String} object.\r\n" + 
				"     */\r\n" + 
				"    public String substring(int beginIndex) {\r\n" + 
				"        if (beginIndex < 0) {\r\n" + 
				"            throw new StringIndexOutOfBoundsException(beginIndex);\r\n" + 
				"        }\r\n" + 
				"        int subLen = value.length - beginIndex;\r\n" + 
				"        if (subLen < 0) {\r\n" + 
				"            throw new StringIndexOutOfBoundsException(subLen);\r\n" + 
				"        }\r\n" + 
				"        return (beginIndex == 0) ? this : new String(value, beginIndex, subLen);\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Returns a string that is a substring of this string. The\r\n" + 
				"     * substring begins at the specified {@code beginIndex} and\r\n" + 
				"     * extends to the character at index {@code endIndex - 1}.\r\n" + 
				"     * Thus the length of the substring is {@code endIndex-beginIndex}.\r\n" + 
				"     * <p>\r\n" + 
				"     * Examples:\r\n" + 
				"     * <blockquote><pre>\r\n" + 
				"     * \"hamburger\".substring(4, 8) returns \"urge\"\r\n" + 
				"     * \"smiles\".substring(1, 5) returns \"mile\"\r\n" + 
				"     * </pre></blockquote>\r\n" + 
				"     *\r\n" + 
				"     * @param      beginIndex   the beginning index, inclusive.\r\n" + 
				"     * @param      endIndex     the ending index, exclusive.\r\n" + 
				"     * @return     the specified substring.\r\n" + 
				"     * @exception  IndexOutOfBoundsException  if the\r\n" + 
				"     *             {@code beginIndex} is negative, or\r\n" + 
				"     *             {@code endIndex} is larger than the length of\r\n" + 
				"     *             this {@code String} object, or\r\n" + 
				"     *             {@code beginIndex} is larger than\r\n" + 
				"     *             {@code endIndex}.\r\n" + 
				"     */\r\n" + 
				"    public String substring(int beginIndex, int endIndex) {\r\n" + 
				"        if (beginIndex < 0) {\r\n" + 
				"            throw new StringIndexOutOfBoundsException(beginIndex);\r\n" + 
				"        }\r\n" + 
				"        if (endIndex > value.length) {\r\n" + 
				"            throw new StringIndexOutOfBoundsException(endIndex);\r\n" + 
				"        }\r\n" + 
				"        int subLen = endIndex - beginIndex;\r\n" + 
				"        if (subLen < 0) {\r\n" + 
				"            throw new StringIndexOutOfBoundsException(subLen);\r\n" + 
				"        }\r\n" + 
				"        return ((beginIndex == 0) && (endIndex == value.length)) ? this\r\n" + 
				"                : new String(value, beginIndex, subLen);\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Returns a character sequence that is a subsequence of this sequence.\r\n" + 
				"     *\r\n" + 
				"     * <p> An invocation of this method of the form\r\n" + 
				"     *\r\n" + 
				"     * <blockquote><pre>\r\n" + 
				"     * str.subSequence(begin,&nbsp;end)</pre></blockquote>\r\n" + 
				"     *\r\n" + 
				"     * behaves in exactly the same way as the invocation\r\n" + 
				"     *\r\n" + 
				"     * <blockquote><pre>\r\n" + 
				"     * str.substring(begin,&nbsp;end)</pre></blockquote>\r\n" + 
				"     *\r\n" + 
				"     * @apiNote\r\n" + 
				"     * This method is defined so that the {@code String} class can implement\r\n" + 
				"     * the {@link CharSequence} interface.\r\n" + 
				"     *\r\n" + 
				"     * @param   beginIndex   the begin index, inclusive.\r\n" + 
				"     * @param   endIndex     the end index, exclusive.\r\n" + 
				"     * @return  the specified subsequence.\r\n" + 
				"     *\r\n" + 
				"     * @throws  IndexOutOfBoundsException\r\n" + 
				"     *          if {@code beginIndex} or {@code endIndex} is negative,\r\n" + 
				"     *          if {@code endIndex} is greater than {@code length()},\r\n" + 
				"     *          or if {@code beginIndex} is greater than {@code endIndex}\r\n" + 
				"     *\r\n" + 
				"     * @since 1.4\r\n" + 
				"     * @spec JSR-51\r\n" + 
				"     */\r\n" + 
				"    public CharSequence subSequence(int beginIndex, int endIndex) {\r\n" + 
				"        return this.substring(beginIndex, endIndex);\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Concatenates the specified string to the end of this string.\r\n" + 
				"     * <p>\r\n" + 
				"     * If the length of the argument string is {@code 0}, then this\r\n" + 
				"     * {@code String} object is returned. Otherwise, a\r\n" + 
				"     * {@code String} object is returned that represents a character\r\n" + 
				"     * sequence that is the concatenation of the character sequence\r\n" + 
				"     * represented by this {@code String} object and the character\r\n" + 
				"     * sequence represented by the argument string.<p>\r\n" + 
				"     * Examples:\r\n" + 
				"     * <blockquote><pre>\r\n" + 
				"     * \"cares\".concat(\"s\") returns \"caress\"\r\n" + 
				"     * \"to\".concat(\"get\").concat(\"her\") returns \"together\"\r\n" + 
				"     * </pre></blockquote>\r\n" + 
				"     *\r\n" + 
				"     * @param   str   the {@code String} that is concatenated to the end\r\n" + 
				"     *                of this {@code String}.\r\n" + 
				"     * @return  a string that represents the concatenation of this object's\r\n" + 
				"     *          characters followed by the string argument's characters.\r\n" + 
				"     */\r\n" + 
				"    public String concat(String str) {\r\n" + 
				"        int otherLen = str.length();\r\n" + 
				"        if (otherLen == 0) {\r\n" + 
				"            return this;\r\n" + 
				"        }\r\n" + 
				"        int len = value.length;\r\n" + 
				"        char buf[] = Arrays.copyOf(value, len + otherLen);\r\n" + 
				"        str.getChars(buf, len);\r\n" + 
				"        return new String(buf, true);\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Returns a string resulting from replacing all occurrences of\r\n" + 
				"     * {@code oldChar} in this string with {@code newChar}.\r\n" + 
				"     * <p>\r\n" + 
				"     * If the character {@code oldChar} does not occur in the\r\n" + 
				"     * character sequence represented by this {@code String} object,\r\n" + 
				"     * then a reference to this {@code String} object is returned.\r\n" + 
				"     * Otherwise, a {@code String} object is returned that\r\n" + 
				"     * represents a character sequence identical to the character sequence\r\n" + 
				"     * represented by this {@code String} object, except that every\r\n" + 
				"     * occurrence of {@code oldChar} is replaced by an occurrence\r\n" + 
				"     * of {@code newChar}.\r\n" + 
				"     * <p>\r\n" + 
				"     * Examples:\r\n" + 
				"     * <blockquote><pre>\r\n" + 
				"     * \"mesquite in your cellar\".replace('e', 'o')\r\n" + 
				"     *         returns \"mosquito in your collar\"\r\n" + 
				"     * \"the war of baronets\".replace('r', 'y')\r\n" + 
				"     *         returns \"the way of bayonets\"\r\n" + 
				"     * \"sparring with a purple porpoise\".replace('p', 't')\r\n" + 
				"     *         returns \"starring with a turtle tortoise\"\r\n" + 
				"     * \"JonL\".replace('q', 'x') returns \"JonL\" (no change)\r\n" + 
				"     * </pre></blockquote>\r\n" + 
				"     *\r\n" + 
				"     * @param   oldChar   the old character.\r\n" + 
				"     * @param   newChar   the new character.\r\n" + 
				"     * @return  a string derived from this string by replacing every\r\n" + 
				"     *          occurrence of {@code oldChar} with {@code newChar}.\r\n" + 
				"     */\r\n" + 
				"    public String replace(char oldChar, char newChar) {\r\n" + 
				"        if (oldChar != newChar) {\r\n" + 
				"            int len = value.length;\r\n" + 
				"            int i = -1;\r\n" + 
				"            char[] val = value; /* avoid getfield opcode */\r\n" + 
				"\r\n" + 
				"            while (++i < len) {\r\n" + 
				"                if (val[i] == oldChar) {\r\n" + 
				"                    break;\r\n" + 
				"                }\r\n" + 
				"            }\r\n" + 
				"            if (i < len) {\r\n" + 
				"                char buf[] = new char[len];\r\n" + 
				"                for (int j = 0; j < i; j++) {\r\n" + 
				"                    buf[j] = val[j];\r\n" + 
				"                }\r\n" + 
				"                while (i < len) {\r\n" + 
				"                    char c = val[i];\r\n" + 
				"                    buf[i] = (c == oldChar) ? newChar : c;\r\n" + 
				"                    i++;\r\n" + 
				"                }\r\n" + 
				"                return new String(buf, true);\r\n" + 
				"            }\r\n" + 
				"        }\r\n" + 
				"        return this;\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Tells whether or not this string matches the given <a\r\n" + 
				"     * href=\"../util/regex/Pattern.html#sum\">regular expression</a>.\r\n" + 
				"     *\r\n" + 
				"     * <p> An invocation of this method of the form\r\n" + 
				"     * <i>str</i>{@code .matches(}<i>regex</i>{@code )} yields exactly the\r\n" + 
				"     * same result as the expression\r\n" + 
				"     *\r\n" + 
				"     * <blockquote>\r\n" + 
				"     * {@link java.util.regex.Pattern}.{@link java.util.regex.Pattern#matches(String,CharSequence)\r\n" + 
				"     * matches(<i>regex</i>, <i>str</i>)}\r\n" + 
				"     * </blockquote>\r\n" + 
				"     *\r\n" + 
				"     * @param   regex\r\n" + 
				"     *          the regular expression to which this string is to be matched\r\n" + 
				"     *\r\n" + 
				"     * @return  {@code true} if, and only if, this string matches the\r\n" + 
				"     *          given regular expression\r\n" + 
				"     *\r\n" + 
				"     * @throws  PatternSyntaxException\r\n" + 
				"     *          if the regular expression's syntax is invalid\r\n" + 
				"     *\r\n" + 
				"     * @see java.util.regex.Pattern\r\n" + 
				"     *\r\n" + 
				"     * @since 1.4\r\n" + 
				"     * @spec JSR-51\r\n" + 
				"     */\r\n" + 
				"    public boolean matches(String regex) {\r\n" + 
				"        return Pattern.matches(regex, this);\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Returns true if and only if this string contains the specified\r\n" + 
				"     * sequence of char values.\r\n" + 
				"     *\r\n" + 
				"     * @param s the sequence to search for\r\n" + 
				"     * @return true if this string contains {@code s}, false otherwise\r\n" + 
				"     * @since 1.5\r\n" + 
				"     */\r\n" + 
				"    public boolean contains(CharSequence s) {\r\n" + 
				"        return indexOf(s.toString()) > -1;\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Replaces the first substring of this string that matches the given <a\r\n" + 
				"     * href=\"../util/regex/Pattern.html#sum\">regular expression</a> with the\r\n" + 
				"     * given replacement.\r\n" + 
				"     *\r\n" + 
				"     * <p> An invocation of this method of the form\r\n" + 
				"     * <i>str</i>{@code .replaceFirst(}<i>regex</i>{@code ,} <i>repl</i>{@code )}\r\n" + 
				"     * yields exactly the same result as the expression\r\n" + 
				"     *\r\n" + 
				"     * <blockquote>\r\n" + 
				"     * <code>\r\n" + 
				"     * {@link java.util.regex.Pattern}.{@link\r\n" + 
				"     * java.util.regex.Pattern#compile compile}(<i>regex</i>).{@link\r\n" + 
				"     * java.util.regex.Pattern#matcher(java.lang.CharSequence) matcher}(<i>str</i>).{@link\r\n" + 
				"     * java.util.regex.Matcher#replaceFirst replaceFirst}(<i>repl</i>)\r\n" + 
				"     * </code>\r\n" + 
				"     * </blockquote>\r\n" + 
				"     *\r\n" + 
				"     *<p>\r\n" + 
				"     * Note that backslashes ({@code \\}) and dollar signs ({@code $}) in the\r\n" + 
				"     * replacement string may cause the results to be different than if it were\r\n" + 
				"     * being treated as a literal replacement string; see\r\n" + 
				"     * {@link java.util.regex.Matcher#replaceFirst}.\r\n" + 
				"     * Use {@link java.util.regex.Matcher#quoteReplacement} to suppress the special\r\n" + 
				"     * meaning of these characters, if desired.\r\n" + 
				"     *\r\n" + 
				"     * @param   regex\r\n" + 
				"     *          the regular expression to which this string is to be matched\r\n" + 
				"     * @param   replacement\r\n" + 
				"     *          the string to be substituted for the first match\r\n" + 
				"     *\r\n" + 
				"     * @return  The resulting {@code String}\r\n" + 
				"     *\r\n" + 
				"     * @throws  PatternSyntaxException\r\n" + 
				"     *          if the regular expression's syntax is invalid\r\n" + 
				"     *\r\n" + 
				"     * @see java.util.regex.Pattern\r\n" + 
				"     *\r\n" + 
				"     * @since 1.4\r\n" + 
				"     * @spec JSR-51\r\n" + 
				"     */\r\n" + 
				"    public String replaceFirst(String regex, String replacement) {\r\n" + 
				"        return Pattern.compile(regex).matcher(this).replaceFirst(replacement);\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Replaces each substring of this string that matches the given <a\r\n" + 
				"     * href=\"../util/regex/Pattern.html#sum\">regular expression</a> with the\r\n" + 
				"     * given replacement.\r\n" + 
				"     *\r\n" + 
				"     * <p> An invocation of this method of the form\r\n" + 
				"     * <i>str</i>{@code .replaceAll(}<i>regex</i>{@code ,} <i>repl</i>{@code )}\r\n" + 
				"     * yields exactly the same result as the expression\r\n" + 
				"     *\r\n" + 
				"     * <blockquote>\r\n" + 
				"     * <code>\r\n" + 
				"     * {@link java.util.regex.Pattern}.{@link\r\n" + 
				"     * java.util.regex.Pattern#compile compile}(<i>regex</i>).{@link\r\n" + 
				"     * java.util.regex.Pattern#matcher(java.lang.CharSequence) matcher}(<i>str</i>).{@link\r\n" + 
				"     * java.util.regex.Matcher#replaceAll replaceAll}(<i>repl</i>)\r\n" + 
				"     * </code>\r\n" + 
				"     * </blockquote>\r\n" + 
				"     *\r\n" + 
				"     *<p>\r\n" + 
				"     * Note that backslashes ({@code \\}) and dollar signs ({@code $}) in the\r\n" + 
				"     * replacement string may cause the results to be different than if it were\r\n" + 
				"     * being treated as a literal replacement string; see\r\n" + 
				"     * {@link java.util.regex.Matcher#replaceAll Matcher.replaceAll}.\r\n" + 
				"     * Use {@link java.util.regex.Matcher#quoteReplacement} to suppress the special\r\n" + 
				"     * meaning of these characters, if desired.\r\n" + 
				"     *\r\n" + 
				"     * @param   regex\r\n" + 
				"     *          the regular expression to which this string is to be matched\r\n" + 
				"     * @param   replacement\r\n" + 
				"     *          the string to be substituted for each match\r\n" + 
				"     *\r\n" + 
				"     * @return  The resulting {@code String}\r\n" + 
				"     *\r\n" + 
				"     * @throws  PatternSyntaxException\r\n" + 
				"     *          if the regular expression's syntax is invalid\r\n" + 
				"     *\r\n" + 
				"     * @see java.util.regex.Pattern\r\n" + 
				"     *\r\n" + 
				"     * @since 1.4\r\n" + 
				"     * @spec JSR-51\r\n" + 
				"     */\r\n" + 
				"    public String replaceAll(String regex, String replacement) {\r\n" + 
				"        return Pattern.compile(regex).matcher(this).replaceAll(replacement);\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Replaces each substring of this string that matches the literal target\r\n" + 
				"     * sequence with the specified literal replacement sequence. The\r\n" + 
				"     * replacement proceeds from the beginning of the string to the end, for\r\n" + 
				"     * example, replacing \"aa\" with \"b\" in the string \"aaa\" will result in\r\n" + 
				"     * \"ba\" rather than \"ab\".\r\n" + 
				"     *\r\n" + 
				"     * @param  target The sequence of char values to be replaced\r\n" + 
				"     * @param  replacement The replacement sequence of char values\r\n" + 
				"     * @return  The resulting string\r\n" + 
				"     * @since 1.5\r\n" + 
				"     */\r\n" + 
				"    public String replace(CharSequence target, CharSequence replacement) {\r\n" + 
				"        return Pattern.compile(target.toString(), Pattern.LITERAL).matcher(\r\n" + 
				"                this).replaceAll(Matcher.quoteReplacement(replacement.toString()));\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Splits this string around matches of the given\r\n" + 
				"     * <a href=\"../util/regex/Pattern.html#sum\">regular expression</a>.\r\n" + 
				"     *\r\n" + 
				"     * <p> The array returned by this method contains each substring of this\r\n" + 
				"     * string that is terminated by another substring that matches the given\r\n" + 
				"     * expression or is terminated by the end of the string.  The substrings in\r\n" + 
				"     * the array are in the order in which they occur in this string.  If the\r\n" + 
				"     * expression does not match any part of the input then the resulting array\r\n" + 
				"     * has just one element, namely this string.\r\n" + 
				"     *\r\n" + 
				"     * <p> When there is a positive-width match at the beginning of this\r\n" + 
				"     * string then an empty leading substring is included at the beginning\r\n" + 
				"     * of the resulting array. A zero-width match at the beginning however\r\n" + 
				"     * never produces such empty leading substring.\r\n" + 
				"     *\r\n" + 
				"     * <p> The {@code limit} parameter controls the number of times the\r\n" + 
				"     * pattern is applied and therefore affects the length of the resulting\r\n" + 
				"     * array.  If the limit <i>n</i> is greater than zero then the pattern\r\n" + 
				"     * will be applied at most <i>n</i>&nbsp;-&nbsp;1 times, the array's\r\n" + 
				"     * length will be no greater than <i>n</i>, and the array's last entry\r\n" + 
				"     * will contain all input beyond the last matched delimiter.  If <i>n</i>\r\n" + 
				"     * is non-positive then the pattern will be applied as many times as\r\n" + 
				"     * possible and the array can have any length.  If <i>n</i> is zero then\r\n" + 
				"     * the pattern will be applied as many times as possible, the array can\r\n" + 
				"     * have any length, and trailing empty strings will be discarded.\r\n" + 
				"     *\r\n" + 
				"     * <p> The string {@code \"boo:and:foo\"}, for example, yields the\r\n" + 
				"     * following results with these parameters:\r\n" + 
				"     *\r\n" + 
				"     * <blockquote><table cellpadding=1 cellspacing=0 summary=\"Split example showing regex, limit, and result\">\r\n" + 
				"     * <tr>\r\n" + 
				"     *     <th>Regex</th>\r\n" + 
				"     *     <th>Limit</th>\r\n" + 
				"     *     <th>Result</th>\r\n" + 
				"     * </tr>\r\n" + 
				"     * <tr><td align=center>:</td>\r\n" + 
				"     *     <td align=center>2</td>\r\n" + 
				"     *     <td>{@code { \"boo\", \"and:foo\" }}</td></tr>\r\n" + 
				"     * <tr><td align=center>:</td>\r\n" + 
				"     *     <td align=center>5</td>\r\n" + 
				"     *     <td>{@code { \"boo\", \"and\", \"foo\" }}</td></tr>\r\n" + 
				"     * <tr><td align=center>:</td>\r\n" + 
				"     *     <td align=center>-2</td>\r\n" + 
				"     *     <td>{@code { \"boo\", \"and\", \"foo\" }}</td></tr>\r\n" + 
				"     * <tr><td align=center>o</td>\r\n" + 
				"     *     <td align=center>5</td>\r\n" + 
				"     *     <td>{@code { \"b\", \"\", \":and:f\", \"\", \"\" }}</td></tr>\r\n" + 
				"     * <tr><td align=center>o</td>\r\n" + 
				"     *     <td align=center>-2</td>\r\n" + 
				"     *     <td>{@code { \"b\", \"\", \":and:f\", \"\", \"\" }}</td></tr>\r\n" + 
				"     * <tr><td align=center>o</td>\r\n" + 
				"     *     <td align=center>0</td>\r\n" + 
				"     *     <td>{@code { \"b\", \"\", \":and:f\" }}</td></tr>\r\n" + 
				"     * </table></blockquote>\r\n" + 
				"     *\r\n" + 
				"     * <p> An invocation of this method of the form\r\n" + 
				"     * <i>str.</i>{@code split(}<i>regex</i>{@code ,}&nbsp;<i>n</i>{@code )}\r\n" + 
				"     * yields the same result as the expression\r\n" + 
				"     *\r\n" + 
				"     * <blockquote>\r\n" + 
				"     * <code>\r\n" + 
				"     * {@link java.util.regex.Pattern}.{@link\r\n" + 
				"     * java.util.regex.Pattern#compile compile}(<i>regex</i>).{@link\r\n" + 
				"     * java.util.regex.Pattern#split(java.lang.CharSequence,int) split}(<i>str</i>,&nbsp;<i>n</i>)\r\n" + 
				"     * </code>\r\n" + 
				"     * </blockquote>\r\n" + 
				"     *\r\n" + 
				"     *\r\n" + 
				"     * @param  regex\r\n" + 
				"     *         the delimiting regular expression\r\n" + 
				"     *\r\n" + 
				"     * @param  limit\r\n" + 
				"     *         the result threshold, as described above\r\n" + 
				"     *\r\n" + 
				"     * @return  the array of strings computed by splitting this string\r\n" + 
				"     *          around matches of the given regular expression\r\n" + 
				"     *\r\n" + 
				"     * @throws  PatternSyntaxException\r\n" + 
				"     *          if the regular expression's syntax is invalid\r\n" + 
				"     *\r\n" + 
				"     * @see java.util.regex.Pattern\r\n" + 
				"     *\r\n" + 
				"     * @since 1.4\r\n" + 
				"     * @spec JSR-51\r\n" + 
				"     */\r\n" + 
				"    public String[] split(String regex, int limit) {\r\n" + 
				"        /* fastpath if the regex is a\r\n" + 
				"         (1)one-char String and this character is not one of the\r\n" + 
				"            RegEx's meta characters \".$|()[{^?*+\\\\\", or\r\n" + 
				"         (2)two-char String and the first char is the backslash and\r\n" + 
				"            the second is not the ascii digit or ascii letter.\r\n" + 
				"         */\r\n" + 
				"        char ch = 0;\r\n" + 
				"        if (((regex.value.length == 1 &&\r\n" + 
				"             \".$|()[{^?*+\\\\\".indexOf(ch = regex.charAt(0)) == -1) ||\r\n" + 
				"             (regex.length() == 2 &&\r\n" + 
				"              regex.charAt(0) == '\\\\' &&\r\n" + 
				"              (((ch = regex.charAt(1))-'0')|('9'-ch)) < 0 &&\r\n" + 
				"              ((ch-'a')|('z'-ch)) < 0 &&\r\n" + 
				"              ((ch-'A')|('Z'-ch)) < 0)) &&\r\n" + 
				"            (ch < Character.MIN_HIGH_SURROGATE ||\r\n" + 
				"             ch > Character.MAX_LOW_SURROGATE))\r\n" + 
				"        {\r\n" + 
				"            int off = 0;\r\n" + 
				"            int next = 0;\r\n" + 
				"            boolean limited = limit > 0;\r\n" + 
				"            ArrayList<String> list = new ArrayList<>();\r\n" + 
				"            while ((next = indexOf(ch, off)) != -1) {\r\n" + 
				"                if (!limited || list.size() < limit - 1) {\r\n" + 
				"                    list.add(substring(off, next));\r\n" + 
				"                    off = next + 1;\r\n" + 
				"                } else {    // last one\r\n" + 
				"                    //assert (list.size() == limit - 1);\r\n" + 
				"                    list.add(substring(off, value.length));\r\n" + 
				"                    off = value.length;\r\n" + 
				"                    break;\r\n" + 
				"                }\r\n" + 
				"            }\r\n" + 
				"            // If no match was found, return this\r\n" + 
				"            if (off == 0)\r\n" + 
				"                return new String[]{this};\r\n" + 
				"\r\n" + 
				"            // Add remaining segment\r\n" + 
				"            if (!limited || list.size() < limit)\r\n" + 
				"                list.add(substring(off, value.length));\r\n" + 
				"\r\n" + 
				"            // Construct result\r\n" + 
				"            int resultSize = list.size();\r\n" + 
				"            if (limit == 0) {\r\n" + 
				"                while (resultSize > 0 && list.get(resultSize - 1).length() == 0) {\r\n" + 
				"                    resultSize--;\r\n" + 
				"                }\r\n" + 
				"            }\r\n" + 
				"            String[] result = new String[resultSize];\r\n" + 
				"            return list.subList(0, resultSize).toArray(result);\r\n" + 
				"        }\r\n" + 
				"        return Pattern.compile(regex).split(this, limit);\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Splits this string around matches of the given <a\r\n" + 
				"     * href=\"../util/regex/Pattern.html#sum\">regular expression</a>.\r\n" + 
				"     *\r\n" + 
				"     * <p> This method works as if by invoking the two-argument {@link\r\n" + 
				"     * #split(String, int) split} method with the given expression and a limit\r\n" + 
				"     * argument of zero.  Trailing empty strings are therefore not included in\r\n" + 
				"     * the resulting array.\r\n" + 
				"     *\r\n" + 
				"     * <p> The string {@code \"boo:and:foo\"}, for example, yields the following\r\n" + 
				"     * results with these expressions:\r\n" + 
				"     *\r\n" + 
				"     * <blockquote><table cellpadding=1 cellspacing=0 summary=\"Split examples showing regex and result\">\r\n" + 
				"     * <tr>\r\n" + 
				"     *  <th>Regex</th>\r\n" + 
				"     *  <th>Result</th>\r\n" + 
				"     * </tr>\r\n" + 
				"     * <tr><td align=center>:</td>\r\n" + 
				"     *     <td>{@code { \"boo\", \"and\", \"foo\" }}</td></tr>\r\n" + 
				"     * <tr><td align=center>o</td>\r\n" + 
				"     *     <td>{@code { \"b\", \"\", \":and:f\" }}</td></tr>\r\n" + 
				"     * </table></blockquote>\r\n" + 
				"     *\r\n" + 
				"     *\r\n" + 
				"     * @param  regex\r\n" + 
				"     *         the delimiting regular expression\r\n" + 
				"     *\r\n" + 
				"     * @return  the array of strings computed by splitting this string\r\n" + 
				"     *          around matches of the given regular expression\r\n" + 
				"     *\r\n" + 
				"     * @throws  PatternSyntaxException\r\n" + 
				"     *          if the regular expression's syntax is invalid\r\n" + 
				"     *\r\n" + 
				"     * @see java.util.regex.Pattern\r\n" + 
				"     *\r\n" + 
				"     * @since 1.4\r\n" + 
				"     * @spec JSR-51\r\n" + 
				"     */\r\n" + 
				"    public String[] split(String regex) {\r\n" + 
				"        return split(regex, 0);\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Returns a new String composed of copies of the\r\n" + 
				"     * {@code CharSequence elements} joined together with a copy of\r\n" + 
				"     * the specified {@code delimiter}.\r\n" + 
				"     *\r\n" + 
				"     * <blockquote>For example,\r\n" + 
				"     * <pre>{@code\r\n" + 
				"     *     String message = String.join(\"-\", \"Java\", \"is\", \"cool\");\r\n" + 
				"     *     // message returned is: \"Java-is-cool\"\r\n" + 
				"     * }</pre></blockquote>\r\n" + 
				"     *\r\n" + 
				"     * Note that if an element is null, then {@code \"null\"} is added.\r\n" + 
				"     *\r\n" + 
				"     * @param  delimiter the delimiter that separates each element\r\n" + 
				"     * @param  elements the elements to join together.\r\n" + 
				"     *\r\n" + 
				"     * @return a new {@code String} that is composed of the {@code elements}\r\n" + 
				"     *         separated by the {@code delimiter}\r\n" + 
				"     *\r\n" + 
				"     * @throws NullPointerException If {@code delimiter} or {@code elements}\r\n" + 
				"     *         is {@code null}\r\n" + 
				"     *\r\n" + 
				"     * @see java.util.StringJoiner\r\n" + 
				"     * @since 1.8\r\n" + 
				"     */\r\n" + 
				"    public static String join(CharSequence delimiter, CharSequence... elements) {\r\n" + 
				"        Objects.requireNonNull(delimiter);\r\n" + 
				"        Objects.requireNonNull(elements);\r\n" + 
				"        // Number of elements not likely worth Arrays.stream overhead.\r\n" + 
				"        StringJoiner joiner = new StringJoiner(delimiter);\r\n" + 
				"        for (CharSequence cs: elements) {\r\n" + 
				"            joiner.add(cs);\r\n" + 
				"        }\r\n" + 
				"        return joiner.toString();\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Returns a new {@code String} composed of copies of the\r\n" + 
				"     * {@code CharSequence elements} joined together with a copy of the\r\n" + 
				"     * specified {@code delimiter}.\r\n" + 
				"     *\r\n" + 
				"     * <blockquote>For example,\r\n" + 
				"     * <pre>{@code\r\n" + 
				"     *     List<String> strings = new LinkedList<>();\r\n" + 
				"     *     strings.add(\"Java\");strings.add(\"is\");\r\n" + 
				"     *     strings.add(\"cool\");\r\n" + 
				"     *     String message = String.join(\" \", strings);\r\n" + 
				"     *     //message returned is: \"Java is cool\"\r\n" + 
				"     *\r\n" + 
				"     *     Set<String> strings = new LinkedHashSet<>();\r\n" + 
				"     *     strings.add(\"Java\"); strings.add(\"is\");\r\n" + 
				"     *     strings.add(\"very\"); strings.add(\"cool\");\r\n" + 
				"     *     String message = String.join(\"-\", strings);\r\n" + 
				"     *     //message returned is: \"Java-is-very-cool\"\r\n" + 
				"     * }</pre></blockquote>\r\n" + 
				"     *\r\n" + 
				"     * Note that if an individual element is {@code null}, then {@code \"null\"} is added.\r\n" + 
				"     *\r\n" + 
				"     * @param  delimiter a sequence of characters that is used to separate each\r\n" + 
				"     *         of the {@code elements} in the resulting {@code String}\r\n" + 
				"     * @param  elements an {@code Iterable} that will have its {@code elements}\r\n" + 
				"     *         joined together.\r\n" + 
				"     *\r\n" + 
				"     * @return a new {@code String} that is composed from the {@code elements}\r\n" + 
				"     *         argument\r\n" + 
				"     *\r\n" + 
				"     * @throws NullPointerException If {@code delimiter} or {@code elements}\r\n" + 
				"     *         is {@code null}\r\n" + 
				"     *\r\n" + 
				"     * @see    #join(CharSequence,CharSequence...)\r\n" + 
				"     * @see    java.util.StringJoiner\r\n" + 
				"     * @since 1.8\r\n" + 
				"     */\r\n" + 
				"    public static String join(CharSequence delimiter,\r\n" + 
				"            Iterable<? extends CharSequence> elements) {\r\n" + 
				"        Objects.requireNonNull(delimiter);\r\n" + 
				"        Objects.requireNonNull(elements);\r\n" + 
				"        StringJoiner joiner = new StringJoiner(delimiter);\r\n" + 
				"        for (CharSequence cs: elements) {\r\n" + 
				"            joiner.add(cs);\r\n" + 
				"        }\r\n" + 
				"        return joiner.toString();\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Converts all of the characters in this {@code String} to lower\r\n" + 
				"     * case using the rules of the given {@code Locale}.  Case mapping is based\r\n" + 
				"     * on the Unicode Standard version specified by the {@link java.lang.Character Character}\r\n" + 
				"     * class. Since case mappings are not always 1:1 char mappings, the resulting\r\n" + 
				"     * {@code String} may be a different length than the original {@code String}.\r\n" + 
				"     * <p>\r\n" + 
				"     * Examples of lowercase  mappings are in the following table:\r\n" + 
				"     * <table border=\"1\" summary=\"Lowercase mapping examples showing language code of locale, upper case, lower case, and description\">\r\n" + 
				"     * <tr>\r\n" + 
				"     *   <th>Language Code of Locale</th>\r\n" + 
				"     *   <th>Upper Case</th>\r\n" + 
				"     *   <th>Lower Case</th>\r\n" + 
				"     *   <th>Description</th>\r\n" + 
				"     * </tr>\r\n" + 
				"     * <tr>\r\n" + 
				"     *   <td>tr (Turkish)</td>\r\n" + 
				"     *   <td>&#92;u0130</td>\r\n" + 
				"     *   <td>&#92;u0069</td>\r\n" + 
				"     *   <td>capital letter I with dot above -&gt; small letter i</td>\r\n" + 
				"     * </tr>\r\n" + 
				"     * <tr>\r\n" + 
				"     *   <td>tr (Turkish)</td>\r\n" + 
				"     *   <td>&#92;u0049</td>\r\n" + 
				"     *   <td>&#92;u0131</td>\r\n" + 
				"     *   <td>capital letter I -&gt; small letter dotless i </td>\r\n" + 
				"     * </tr>\r\n" + 
				"     * <tr>\r\n" + 
				"     *   <td>(all)</td>\r\n" + 
				"     *   <td>French Fries</td>\r\n" + 
				"     *   <td>french fries</td>\r\n" + 
				"     *   <td>lowercased all chars in String</td>\r\n" + 
				"     * </tr>\r\n" + 
				"     * <tr>\r\n" + 
				"     *   <td>(all)</td>\r\n" + 
				"     *   <td><img src=\"doc-files/capiota.gif\" alt=\"capiota\"><img src=\"doc-files/capchi.gif\" alt=\"capchi\">\r\n" + 
				"     *       <img src=\"doc-files/captheta.gif\" alt=\"captheta\"><img src=\"doc-files/capupsil.gif\" alt=\"capupsil\">\r\n" + 
				"     *       <img src=\"doc-files/capsigma.gif\" alt=\"capsigma\"></td>\r\n" + 
				"     *   <td><img src=\"doc-files/iota.gif\" alt=\"iota\"><img src=\"doc-files/chi.gif\" alt=\"chi\">\r\n" + 
				"     *       <img src=\"doc-files/theta.gif\" alt=\"theta\"><img src=\"doc-files/upsilon.gif\" alt=\"upsilon\">\r\n" + 
				"     *       <img src=\"doc-files/sigma1.gif\" alt=\"sigma\"></td>\r\n" + 
				"     *   <td>lowercased all chars in String</td>\r\n" + 
				"     * </tr>\r\n" + 
				"     * </table>\r\n" + 
				"     *\r\n" + 
				"     * @param locale use the case transformation rules for this locale\r\n" + 
				"     * @return the {@code String}, converted to lowercase.\r\n" + 
				"     * @see     java.lang.String#toLowerCase()\r\n" + 
				"     * @see     java.lang.String#toUpperCase()\r\n" + 
				"     * @see     java.lang.String#toUpperCase(Locale)\r\n" + 
				"     * @since   1.1\r\n" + 
				"     */\r\n" + 
				"    public String toLowerCase(Locale locale) {\r\n" + 
				"        if (locale == null) {\r\n" + 
				"            throw new NullPointerException();\r\n" + 
				"        }\r\n" + 
				"\r\n" + 
				"        int firstUpper;\r\n" + 
				"        final int len = value.length;\r\n" + 
				"\r\n" + 
				"        /* Now check if there are any characters that need to be changed. */\r\n" + 
				"        scan: {\r\n" + 
				"            for (firstUpper = 0 ; firstUpper < len; ) {\r\n" + 
				"                char c = value[firstUpper];\r\n" + 
				"                if ((c >= Character.MIN_HIGH_SURROGATE)\r\n" + 
				"                        && (c <= Character.MAX_HIGH_SURROGATE)) {\r\n" + 
				"                    int supplChar = codePointAt(firstUpper);\r\n" + 
				"                    if (supplChar != Character.toLowerCase(supplChar)) {\r\n" + 
				"                        break scan;\r\n" + 
				"                    }\r\n" + 
				"                    firstUpper += Character.charCount(supplChar);\r\n" + 
				"                } else {\r\n" + 
				"                    if (c != Character.toLowerCase(c)) {\r\n" + 
				"                        break scan;\r\n" + 
				"                    }\r\n" + 
				"                    firstUpper++;\r\n" + 
				"                }\r\n" + 
				"            }\r\n" + 
				"            return this;\r\n" + 
				"        }\r\n" + 
				"\r\n" + 
				"        char[] result = new char[len];\r\n" + 
				"        int resultOffset = 0;  /* result may grow, so i+resultOffset\r\n" + 
				"                                * is the write location in result */\r\n" + 
				"\r\n" + 
				"        /* Just copy the first few lowerCase characters. */\r\n" + 
				"        System.arraycopy(value, 0, result, 0, firstUpper);\r\n" + 
				"\r\n" + 
				"        String lang = locale.getLanguage();\r\n" + 
				"        boolean localeDependent =\r\n" + 
				"                (lang == \"tr\" || lang == \"az\" || lang == \"lt\");\r\n" + 
				"        char[] lowerCharArray;\r\n" + 
				"        int lowerChar;\r\n" + 
				"        int srcChar;\r\n" + 
				"        int srcCount;\r\n" + 
				"        for (int i = firstUpper; i < len; i += srcCount) {\r\n" + 
				"            srcChar = (int)value[i];\r\n" + 
				"            if ((char)srcChar >= Character.MIN_HIGH_SURROGATE\r\n" + 
				"                    && (char)srcChar <= Character.MAX_HIGH_SURROGATE) {\r\n" + 
				"                srcChar = codePointAt(i);\r\n" + 
				"                srcCount = Character.charCount(srcChar);\r\n" + 
				"            } else {\r\n" + 
				"                srcCount = 1;\r\n" + 
				"            }\r\n" + 
				"            if (localeDependent ||\r\n" + 
				"                srcChar == '\\u03A3' || // GREEK CAPITAL LETTER SIGMA\r\n" + 
				"                srcChar == '\\u0130') { // LATIN CAPITAL LETTER I WITH DOT ABOVE\r\n" + 
				"                lowerChar = ConditionalSpecialCasing.toLowerCaseEx(this, i, locale);\r\n" + 
				"            } else {\r\n" + 
				"                lowerChar = Character.toLowerCase(srcChar);\r\n" + 
				"            }\r\n" + 
				"            if ((lowerChar == Character.ERROR)\r\n" + 
				"                    || (lowerChar >= Character.MIN_SUPPLEMENTARY_CODE_POINT)) {\r\n" + 
				"                if (lowerChar == Character.ERROR) {\r\n" + 
				"                    lowerCharArray =\r\n" + 
				"                            ConditionalSpecialCasing.toLowerCaseCharArray(this, i, locale);\r\n" + 
				"                } else if (srcCount == 2) {\r\n" + 
				"                    resultOffset += Character.toChars(lowerChar, result, i + resultOffset) - srcCount;\r\n" + 
				"                    continue;\r\n" + 
				"                } else {\r\n" + 
				"                    lowerCharArray = Character.toChars(lowerChar);\r\n" + 
				"                }\r\n" + 
				"\r\n" + 
				"                /* Grow result if needed */\r\n" + 
				"                int mapLen = lowerCharArray.length;\r\n" + 
				"                if (mapLen > srcCount) {\r\n" + 
				"                    char[] result2 = new char[result.length + mapLen - srcCount];\r\n" + 
				"                    System.arraycopy(result, 0, result2, 0, i + resultOffset);\r\n" + 
				"                    result = result2;\r\n" + 
				"                }\r\n" + 
				"                for (int x = 0; x < mapLen; ++x) {\r\n" + 
				"                    result[i + resultOffset + x] = lowerCharArray[x];\r\n" + 
				"                }\r\n" + 
				"                resultOffset += (mapLen - srcCount);\r\n" + 
				"            } else {\r\n" + 
				"                result[i + resultOffset] = (char)lowerChar;\r\n" + 
				"            }\r\n" + 
				"        }\r\n" + 
				"        return new String(result, 0, len + resultOffset);\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Converts all of the characters in this {@code String} to lower\r\n" + 
				"     * case using the rules of the default locale. This is equivalent to calling\r\n" + 
				"     * {@code toLowerCase(Locale.getDefault())}.\r\n" + 
				"     * <p>\r\n" + 
				"     * <b>Note:</b> This method is locale sensitive, and may produce unexpected\r\n" + 
				"     * results if used for strings that are intended to be interpreted locale\r\n" + 
				"     * independently.\r\n" + 
				"     * Examples are programming language identifiers, protocol keys, and HTML\r\n" + 
				"     * tags.\r\n" + 
				"     * For instance, {@code \"TITLE\".toLowerCase()} in a Turkish locale\r\n" + 
				"     * returns {@code \"t\\u005Cu0131tle\"}, where '\\u005Cu0131' is the\r\n" + 
				"     * LATIN SMALL LETTER DOTLESS I character.\r\n" + 
				"     * To obtain correct results for locale insensitive strings, use\r\n" + 
				"     * {@code toLowerCase(Locale.ROOT)}.\r\n" + 
				"     * <p>\r\n" + 
				"     * @return  the {@code String}, converted to lowercase.\r\n" + 
				"     * @see     java.lang.String#toLowerCase(Locale)\r\n" + 
				"     */\r\n" + 
				"    public String toLowerCase() {\r\n" + 
				"        return toLowerCase(Locale.getDefault());\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Converts all of the characters in this {@code String} to upper\r\n" + 
				"     * case using the rules of the given {@code Locale}. Case mapping is based\r\n" + 
				"     * on the Unicode Standard version specified by the {@link java.lang.Character Character}\r\n" + 
				"     * class. Since case mappings are not always 1:1 char mappings, the resulting\r\n" + 
				"     * {@code String} may be a different length than the original {@code String}.\r\n" + 
				"     * <p>\r\n" + 
				"     * Examples of locale-sensitive and 1:M case mappings are in the following table.\r\n" + 
				"     *\r\n" + 
				"     * <table border=\"1\" summary=\"Examples of locale-sensitive and 1:M case mappings. Shows Language code of locale, lower case, upper case, and description.\">\r\n" + 
				"     * <tr>\r\n" + 
				"     *   <th>Language Code of Locale</th>\r\n" + 
				"     *   <th>Lower Case</th>\r\n" + 
				"     *   <th>Upper Case</th>\r\n" + 
				"     *   <th>Description</th>\r\n" + 
				"     * </tr>\r\n" + 
				"     * <tr>\r\n" + 
				"     *   <td>tr (Turkish)</td>\r\n" + 
				"     *   <td>&#92;u0069</td>\r\n" + 
				"     *   <td>&#92;u0130</td>\r\n" + 
				"     *   <td>small letter i -&gt; capital letter I with dot above</td>\r\n" + 
				"     * </tr>\r\n" + 
				"     * <tr>\r\n" + 
				"     *   <td>tr (Turkish)</td>\r\n" + 
				"     *   <td>&#92;u0131</td>\r\n" + 
				"     *   <td>&#92;u0049</td>\r\n" + 
				"     *   <td>small letter dotless i -&gt; capital letter I</td>\r\n" + 
				"     * </tr>\r\n" + 
				"     * <tr>\r\n" + 
				"     *   <td>(all)</td>\r\n" + 
				"     *   <td>&#92;u00df</td>\r\n" + 
				"     *   <td>&#92;u0053 &#92;u0053</td>\r\n" + 
				"     *   <td>small letter sharp s -&gt; two letters: SS</td>\r\n" + 
				"     * </tr>\r\n" + 
				"     * <tr>\r\n" + 
				"     *   <td>(all)</td>\r\n" + 
				"     *   <td>Fahrvergn&uuml;gen</td>\r\n" + 
				"     *   <td>FAHRVERGN&Uuml;GEN</td>\r\n" + 
				"     *   <td></td>\r\n" + 
				"     * </tr>\r\n" + 
				"     * </table>\r\n" + 
				"     * @param locale use the case transformation rules for this locale\r\n" + 
				"     * @return the {@code String}, converted to uppercase.\r\n" + 
				"     * @see     java.lang.String#toUpperCase()\r\n" + 
				"     * @see     java.lang.String#toLowerCase()\r\n" + 
				"     * @see     java.lang.String#toLowerCase(Locale)\r\n" + 
				"     * @since   1.1\r\n" + 
				"     */\r\n" + 
				"    public String toUpperCase(Locale locale) {\r\n" + 
				"        if (locale == null) {\r\n" + 
				"            throw new NullPointerException();\r\n" + 
				"        }\r\n" + 
				"\r\n" + 
				"        int firstLower;\r\n" + 
				"        final int len = value.length;\r\n" + 
				"\r\n" + 
				"        /* Now check if there are any characters that need to be changed. */\r\n" + 
				"        scan: {\r\n" + 
				"            for (firstLower = 0 ; firstLower < len; ) {\r\n" + 
				"                int c = (int)value[firstLower];\r\n" + 
				"                int srcCount;\r\n" + 
				"                if ((c >= Character.MIN_HIGH_SURROGATE)\r\n" + 
				"                        && (c <= Character.MAX_HIGH_SURROGATE)) {\r\n" + 
				"                    c = codePointAt(firstLower);\r\n" + 
				"                    srcCount = Character.charCount(c);\r\n" + 
				"                } else {\r\n" + 
				"                    srcCount = 1;\r\n" + 
				"                }\r\n" + 
				"                int upperCaseChar = Character.toUpperCaseEx(c);\r\n" + 
				"                if ((upperCaseChar == Character.ERROR)\r\n" + 
				"                        || (c != upperCaseChar)) {\r\n" + 
				"                    break scan;\r\n" + 
				"                }\r\n" + 
				"                firstLower += srcCount;\r\n" + 
				"            }\r\n" + 
				"            return this;\r\n" + 
				"        }\r\n" + 
				"\r\n" + 
				"        /* result may grow, so i+resultOffset is the write location in result */\r\n" + 
				"        int resultOffset = 0;\r\n" + 
				"        char[] result = new char[len]; /* may grow */\r\n" + 
				"\r\n" + 
				"        /* Just copy the first few upperCase characters. */\r\n" + 
				"        System.arraycopy(value, 0, result, 0, firstLower);\r\n" + 
				"\r\n" + 
				"        String lang = locale.getLanguage();\r\n" + 
				"        boolean localeDependent =\r\n" + 
				"                (lang == \"tr\" || lang == \"az\" || lang == \"lt\");\r\n" + 
				"        char[] upperCharArray;\r\n" + 
				"        int upperChar;\r\n" + 
				"        int srcChar;\r\n" + 
				"        int srcCount;\r\n" + 
				"        for (int i = firstLower; i < len; i += srcCount) {\r\n" + 
				"            srcChar = (int)value[i];\r\n" + 
				"            if ((char)srcChar >= Character.MIN_HIGH_SURROGATE &&\r\n" + 
				"                (char)srcChar <= Character.MAX_HIGH_SURROGATE) {\r\n" + 
				"                srcChar = codePointAt(i);\r\n" + 
				"                srcCount = Character.charCount(srcChar);\r\n" + 
				"            } else {\r\n" + 
				"                srcCount = 1;\r\n" + 
				"            }\r\n" + 
				"            if (localeDependent) {\r\n" + 
				"                upperChar = ConditionalSpecialCasing.toUpperCaseEx(this, i, locale);\r\n" + 
				"            } else {\r\n" + 
				"                upperChar = Character.toUpperCaseEx(srcChar);\r\n" + 
				"            }\r\n" + 
				"            if ((upperChar == Character.ERROR)\r\n" + 
				"                    || (upperChar >= Character.MIN_SUPPLEMENTARY_CODE_POINT)) {\r\n" + 
				"                if (upperChar == Character.ERROR) {\r\n" + 
				"                    if (localeDependent) {\r\n" + 
				"                        upperCharArray =\r\n" + 
				"                                ConditionalSpecialCasing.toUpperCaseCharArray(this, i, locale);\r\n" + 
				"                    } else {\r\n" + 
				"                        upperCharArray = Character.toUpperCaseCharArray(srcChar);\r\n" + 
				"                    }\r\n" + 
				"                } else if (srcCount == 2) {\r\n" + 
				"                    resultOffset += Character.toChars(upperChar, result, i + resultOffset) - srcCount;\r\n" + 
				"                    continue;\r\n" + 
				"                } else {\r\n" + 
				"                    upperCharArray = Character.toChars(upperChar);\r\n" + 
				"                }\r\n" + 
				"\r\n" + 
				"                /* Grow result if needed */\r\n" + 
				"                int mapLen = upperCharArray.length;\r\n" + 
				"                if (mapLen > srcCount) {\r\n" + 
				"                    char[] result2 = new char[result.length + mapLen - srcCount];\r\n" + 
				"                    System.arraycopy(result, 0, result2, 0, i + resultOffset);\r\n" + 
				"                    result = result2;\r\n" + 
				"                }\r\n" + 
				"                for (int x = 0; x < mapLen; ++x) {\r\n" + 
				"                    result[i + resultOffset + x] = upperCharArray[x];\r\n" + 
				"                }\r\n" + 
				"                resultOffset += (mapLen - srcCount);\r\n" + 
				"            } else {\r\n" + 
				"                result[i + resultOffset] = (char)upperChar;\r\n" + 
				"            }\r\n" + 
				"        }\r\n" + 
				"        return new String(result, 0, len + resultOffset);\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Converts all of the characters in this {@code String} to upper\r\n" + 
				"     * case using the rules of the default locale. This method is equivalent to\r\n" + 
				"     * {@code toUpperCase(Locale.getDefault())}.\r\n" + 
				"     * <p>\r\n" + 
				"     * <b>Note:</b> This method is locale sensitive, and may produce unexpected\r\n" + 
				"     * results if used for strings that are intended to be interpreted locale\r\n" + 
				"     * independently.\r\n" + 
				"     * Examples are programming language identifiers, protocol keys, and HTML\r\n" + 
				"     * tags.\r\n" + 
				"     * For instance, {@code \"title\".toUpperCase()} in a Turkish locale\r\n" + 
				"     * returns {@code \"T\\u005Cu0130TLE\"}, where '\\u005Cu0130' is the\r\n" + 
				"     * LATIN CAPITAL LETTER I WITH DOT ABOVE character.\r\n" + 
				"     * To obtain correct results for locale insensitive strings, use\r\n" + 
				"     * {@code toUpperCase(Locale.ROOT)}.\r\n" + 
				"     * <p>\r\n" + 
				"     * @return  the {@code String}, converted to uppercase.\r\n" + 
				"     * @see     java.lang.String#toUpperCase(Locale)\r\n" + 
				"     */\r\n" + 
				"    public String toUpperCase() {\r\n" + 
				"        return toUpperCase(Locale.getDefault());\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Returns a string whose value is this string, with any leading and trailing\r\n" + 
				"     * whitespace removed.\r\n" + 
				"     * <p>\r\n" + 
				"     * If this {@code String} object represents an empty character\r\n" + 
				"     * sequence, or the first and last characters of character sequence\r\n" + 
				"     * represented by this {@code String} object both have codes\r\n" + 
				"     * greater than {@code '\\u005Cu0020'} (the space character), then a\r\n" + 
				"     * reference to this {@code String} object is returned.\r\n" + 
				"     * <p>\r\n" + 
				"     * Otherwise, if there is no character with a code greater than\r\n" + 
				"     * {@code '\\u005Cu0020'} in the string, then a\r\n" + 
				"     * {@code String} object representing an empty string is\r\n" + 
				"     * returned.\r\n" + 
				"     * <p>\r\n" + 
				"     * Otherwise, let <i>k</i> be the index of the first character in the\r\n" + 
				"     * string whose code is greater than {@code '\\u005Cu0020'}, and let\r\n" + 
				"     * <i>m</i> be the index of the last character in the string whose code\r\n" + 
				"     * is greater than {@code '\\u005Cu0020'}. A {@code String}\r\n" + 
				"     * object is returned, representing the substring of this string that\r\n" + 
				"     * begins with the character at index <i>k</i> and ends with the\r\n" + 
				"     * character at index <i>m</i>-that is, the result of\r\n" + 
				"     * {@code this.substring(k, m + 1)}.\r\n" + 
				"     * <p>\r\n" + 
				"     * This method may be used to trim whitespace (as defined above) from\r\n" + 
				"     * the beginning and end of a string.\r\n" + 
				"     *\r\n" + 
				"     * @return  A string whose value is this string, with any leading and trailing white\r\n" + 
				"     *          space removed, or this string if it has no leading or\r\n" + 
				"     *          trailing white space.\r\n" + 
				"     */\r\n" + 
				"    public String trim() {\r\n" + 
				"        int len = value.length;\r\n" + 
				"        int st = 0;\r\n" + 
				"        char[] val = value;    /* avoid getfield opcode */\r\n" + 
				"\r\n" + 
				"        while ((st < len) && (val[st] <= ' ')) {\r\n" + 
				"            st++;\r\n" + 
				"        }\r\n" + 
				"        while ((st < len) && (val[len - 1] <= ' ')) {\r\n" + 
				"            len--;\r\n" + 
				"        }\r\n" + 
				"        return ((st > 0) || (len < value.length)) ? substring(st, len) : this;\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * This object (which is already a string!) is itself returned.\r\n" + 
				"     *\r\n" + 
				"     * @return  the string itself.\r\n" + 
				"     */\r\n" + 
				"    public String toString() {\r\n" + 
				"        return this;\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Converts this string to a new character array.\r\n" + 
				"     *\r\n" + 
				"     * @return  a newly allocated character array whose length is the length\r\n" + 
				"     *          of this string and whose contents are initialized to contain\r\n" + 
				"     *          the character sequence represented by this string.\r\n" + 
				"     */\r\n" + 
				"    public char[] toCharArray() {\r\n" + 
				"        // Cannot use Arrays.copyOf because of class initialization order issues\r\n" + 
				"        char result[] = new char[value.length];\r\n" + 
				"        System.arraycopy(value, 0, result, 0, value.length);\r\n" + 
				"        return result;\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Returns a formatted string using the specified format string and\r\n" + 
				"     * arguments.\r\n" + 
				"     *\r\n" + 
				"     * <p> The locale always used is the one returned by {@link\r\n" + 
				"     * java.util.Locale#getDefault() Locale.getDefault()}.\r\n" + 
				"     *\r\n" + 
				"     * @param  format\r\n" + 
				"     *         A <a href=\"../util/Formatter.html#syntax\">format string</a>\r\n" + 
				"     *\r\n" + 
				"     * @param  args\r\n" + 
				"     *         Arguments referenced by the format specifiers in the format\r\n" + 
				"     *         string.  If there are more arguments than format specifiers, the\r\n" + 
				"     *         extra arguments are ignored.  The number of arguments is\r\n" + 
				"     *         variable and may be zero.  The maximum number of arguments is\r\n" + 
				"     *         limited by the maximum dimension of a Java array as defined by\r\n" + 
				"     *         <cite>The Java&trade; Virtual Machine Specification</cite>.\r\n" + 
				"     *         The behaviour on a\r\n" + 
				"     *         {@code null} argument depends on the <a\r\n" + 
				"     *         href=\"../util/Formatter.html#syntax\">conversion</a>.\r\n" + 
				"     *\r\n" + 
				"     * @throws  java.util.IllegalFormatException\r\n" + 
				"     *          If a format string contains an illegal syntax, a format\r\n" + 
				"     *          specifier that is incompatible with the given arguments,\r\n" + 
				"     *          insufficient arguments given the format string, or other\r\n" + 
				"     *          illegal conditions.  For specification of all possible\r\n" + 
				"     *          formatting errors, see the <a\r\n" + 
				"     *          href=\"../util/Formatter.html#detail\">Details</a> section of the\r\n" + 
				"     *          formatter class specification.\r\n" + 
				"     *\r\n" + 
				"     * @return  A formatted string\r\n" + 
				"     *\r\n" + 
				"     * @see  java.util.Formatter\r\n" + 
				"     * @since  1.5\r\n" + 
				"     */\r\n" + 
				"    public static String format(String format, Object... args) {\r\n" + 
				"        return new Formatter().format(format, args).toString();\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Returns a formatted string using the specified locale, format string,\r\n" + 
				"     * and arguments.\r\n" + 
				"     *\r\n" + 
				"     * @param  l\r\n" + 
				"     *         The {@linkplain java.util.Locale locale} to apply during\r\n" + 
				"     *         formatting.  If {@code l} is {@code null} then no localization\r\n" + 
				"     *         is applied.\r\n" + 
				"     *\r\n" + 
				"     * @param  format\r\n" + 
				"     *         A <a href=\"../util/Formatter.html#syntax\">format string</a>\r\n" + 
				"     *\r\n" + 
				"     * @param  args\r\n" + 
				"     *         Arguments referenced by the format specifiers in the format\r\n" + 
				"     *         string.  If there are more arguments than format specifiers, the\r\n" + 
				"     *         extra arguments are ignored.  The number of arguments is\r\n" + 
				"     *         variable and may be zero.  The maximum number of arguments is\r\n" + 
				"     *         limited by the maximum dimension of a Java array as defined by\r\n" + 
				"     *         <cite>The Java&trade; Virtual Machine Specification</cite>.\r\n" + 
				"     *         The behaviour on a\r\n" + 
				"     *         {@code null} argument depends on the\r\n" + 
				"     *         <a href=\"../util/Formatter.html#syntax\">conversion</a>.\r\n" + 
				"     *\r\n" + 
				"     * @throws  java.util.IllegalFormatException\r\n" + 
				"     *          If a format string contains an illegal syntax, a format\r\n" + 
				"     *          specifier that is incompatible with the given arguments,\r\n" + 
				"     *          insufficient arguments given the format string, or other\r\n" + 
				"     *          illegal conditions.  For specification of all possible\r\n" + 
				"     *          formatting errors, see the <a\r\n" + 
				"     *          href=\"../util/Formatter.html#detail\">Details</a> section of the\r\n" + 
				"     *          formatter class specification\r\n" + 
				"     *\r\n" + 
				"     * @return  A formatted string\r\n" + 
				"     *\r\n" + 
				"     * @see  java.util.Formatter\r\n" + 
				"     * @since  1.5\r\n" + 
				"     */\r\n" + 
				"    public static String format(Locale l, String format, Object... args) {\r\n" + 
				"        return new Formatter(l).format(format, args).toString();\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Returns the string representation of the {@code Object} argument.\r\n" + 
				"     *\r\n" + 
				"     * @param   obj   an {@code Object}.\r\n" + 
				"     * @return  if the argument is {@code null}, then a string equal to\r\n" + 
				"     *          {@code \"null\"}; otherwise, the value of\r\n" + 
				"     *          {@code obj.toString()} is returned.\r\n" + 
				"     * @see     java.lang.Object#toString()\r\n" + 
				"     */\r\n" + 
				"    public static String valueOf(Object obj) {\r\n" + 
				"        return (obj == null) ? \"null\" : obj.toString();\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Returns the string representation of the {@code char} array\r\n" + 
				"     * argument. The contents of the character array are copied; subsequent\r\n" + 
				"     * modification of the character array does not affect the returned\r\n" + 
				"     * string.\r\n" + 
				"     *\r\n" + 
				"     * @param   data     the character array.\r\n" + 
				"     * @return  a {@code String} that contains the characters of the\r\n" + 
				"     *          character array.\r\n" + 
				"     */\r\n" + 
				"    public static String valueOf(char data[]) {\r\n" + 
				"        return new String(data);\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Returns the string representation of a specific subarray of the\r\n" + 
				"     * {@code char} array argument.\r\n" + 
				"     * <p>\r\n" + 
				"     * The {@code offset} argument is the index of the first\r\n" + 
				"     * character of the subarray. The {@code count} argument\r\n" + 
				"     * specifies the length of the subarray. The contents of the subarray\r\n" + 
				"     * are copied; subsequent modification of the character array does not\r\n" + 
				"     * affect the returned string.\r\n" + 
				"     *\r\n" + 
				"     * @param   data     the character array.\r\n" + 
				"     * @param   offset   initial offset of the subarray.\r\n" + 
				"     * @param   count    length of the subarray.\r\n" + 
				"     * @return  a {@code String} that contains the characters of the\r\n" + 
				"     *          specified subarray of the character array.\r\n" + 
				"     * @exception IndexOutOfBoundsException if {@code offset} is\r\n" + 
				"     *          negative, or {@code count} is negative, or\r\n" + 
				"     *          {@code offset+count} is larger than\r\n" + 
				"     *          {@code data.length}.\r\n" + 
				"     */\r\n" + 
				"    public static String valueOf(char data[], int offset, int count) {\r\n" + 
				"        return new String(data, offset, count);\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Equivalent to {@link #valueOf(char[], int, int)}.\r\n" + 
				"     *\r\n" + 
				"     * @param   data     the character array.\r\n" + 
				"     * @param   offset   initial offset of the subarray.\r\n" + 
				"     * @param   count    length of the subarray.\r\n" + 
				"     * @return  a {@code String} that contains the characters of the\r\n" + 
				"     *          specified subarray of the character array.\r\n" + 
				"     * @exception IndexOutOfBoundsException if {@code offset} is\r\n" + 
				"     *          negative, or {@code count} is negative, or\r\n" + 
				"     *          {@code offset+count} is larger than\r\n" + 
				"     *          {@code data.length}.\r\n" + 
				"     */\r\n" + 
				"    public static String copyValueOf(char data[], int offset, int count) {\r\n" + 
				"        return new String(data, offset, count);\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Equivalent to {@link #valueOf(char[])}.\r\n" + 
				"     *\r\n" + 
				"     * @param   data   the character array.\r\n" + 
				"     * @return  a {@code String} that contains the characters of the\r\n" + 
				"     *          character array.\r\n" + 
				"     */\r\n" + 
				"    public static String copyValueOf(char data[]) {\r\n" + 
				"        return new String(data);\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Returns the string representation of the {@code boolean} argument.\r\n" + 
				"     *\r\n" + 
				"     * @param   b   a {@code boolean}.\r\n" + 
				"     * @return  if the argument is {@code true}, a string equal to\r\n" + 
				"     *          {@code \"true\"} is returned; otherwise, a string equal to\r\n" + 
				"     *          {@code \"false\"} is returned.\r\n" + 
				"     */\r\n" + 
				"    public static String valueOf(boolean b) {\r\n" + 
				"        return b ? \"true\" : \"false\";\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Returns the string representation of the {@code char}\r\n" + 
				"     * argument.\r\n" + 
				"     *\r\n" + 
				"     * @param   c   a {@code char}.\r\n" + 
				"     * @return  a string of length {@code 1} containing\r\n" + 
				"     *          as its single character the argument {@code c}.\r\n" + 
				"     */\r\n" + 
				"    public static String valueOf(char c) {\r\n" + 
				"        char data[] = {c};\r\n" + 
				"        return new String(data, true);\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Returns the string representation of the {@code int} argument.\r\n" + 
				"     * <p>\r\n" + 
				"     * The representation is exactly the one returned by the\r\n" + 
				"     * {@code Integer.toString} method of one argument.\r\n" + 
				"     *\r\n" + 
				"     * @param   i   an {@code int}.\r\n" + 
				"     * @return  a string representation of the {@code int} argument.\r\n" + 
				"     * @see     java.lang.Integer#toString(int, int)\r\n" + 
				"     */\r\n" + 
				"    public static String valueOf(int i) {\r\n" + 
				"        return Integer.toString(i);\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Returns the string representation of the {@code long} argument.\r\n" + 
				"     * <p>\r\n" + 
				"     * The representation is exactly the one returned by the\r\n" + 
				"     * {@code Long.toString} method of one argument.\r\n" + 
				"     *\r\n" + 
				"     * @param   l   a {@code long}.\r\n" + 
				"     * @return  a string representation of the {@code long} argument.\r\n" + 
				"     * @see     java.lang.Long#toString(long)\r\n" + 
				"     */\r\n" + 
				"    public static String valueOf(long l) {\r\n" + 
				"        return Long.toString(l);\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Returns the string representation of the {@code float} argument.\r\n" + 
				"     * <p>\r\n" + 
				"     * The representation is exactly the one returned by the\r\n" + 
				"     * {@code Float.toString} method of one argument.\r\n" + 
				"     *\r\n" + 
				"     * @param   f   a {@code float}.\r\n" + 
				"     * @return  a string representation of the {@code float} argument.\r\n" + 
				"     * @see     java.lang.Float#toString(float)\r\n" + 
				"     */\r\n" + 
				"    public static String valueOf(float f) {\r\n" + 
				"        return Float.toString(f);\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Returns the string representation of the {@code double} argument.\r\n" + 
				"     * <p>\r\n" + 
				"     * The representation is exactly the one returned by the\r\n" + 
				"     * {@code Double.toString} method of one argument.\r\n" + 
				"     *\r\n" + 
				"     * @param   d   a {@code double}.\r\n" + 
				"     * @return  a  string representation of the {@code double} argument.\r\n" + 
				"     * @see     java.lang.Double#toString(double)\r\n" + 
				"     */\r\n" + 
				"    public static String valueOf(double d) {\r\n" + 
				"        return Double.toString(d);\r\n" + 
				"    }\r\n" + 
				"\r\n" + 
				"    /**\r\n" + 
				"     * Returns a canonical representation for the string object.\r\n" + 
				"     * <p>\r\n" + 
				"     * A pool of strings, initially empty, is maintained privately by the\r\n" + 
				"     * class {@code String}.\r\n" + 
				"     * <p>\r\n" + 
				"     * When the intern method is invoked, if the pool already contains a\r\n" + 
				"     * string equal to this {@code String} object as determined by\r\n" + 
				"     * the {@link #equals(Object)} method, then the string from the pool is\r\n" + 
				"     * returned. Otherwise, this {@code String} object is added to the\r\n" + 
				"     * pool and a reference to this {@code String} object is returned.\r\n" + 
				"     * <p>\r\n" + 
				"     * It follows that for any two strings {@code s} and {@code t},\r\n" + 
				"     * {@code s.intern() == t.intern()} is {@code true}\r\n" + 
				"     * if and only if {@code s.equals(t)} is {@code true}.\r\n" + 
				"     * <p>\r\n" + 
				"     * All literal strings and string-valued constant expressions are\r\n" + 
				"     * interned. String literals are defined in section 3.10.5 of the\r\n" + 
				"     * <cite>The Java&trade; Language Specification</cite>.\r\n" + 
				"     *\r\n" + 
				"     * @return  a string that has the same contents as this string, but is\r\n" + 
				"     *          guaranteed to be from a pool of unique strings.\r\n" + 
				"     */\r\n" + 
				"    public native String intern();\r\n" + 
				"}\r\n" + 
				"";		
		System.out.println(string_java);
		System.out.println(string_java.length());
	}

}
```

运行结果，除了得到上述字符串外，还得到字符串的长度为：127479，没有达到最大长度，故运行正常。

**使用javac编译**

```java
>javac StringTest.java
StringTest.java:6: 错误: 常量字符串过长
                String string_java="/*\r\n" +
                                            ^
1 个错误
```

编译错误，那“常量字符串”到底可以支持多长的长度呢？通过测试发现，当长度小于65535时可编译成功，否则就会失败。通过查找资料发现，原来String常量会放入字符串常量池，字符串常量池对字符串的长度做了限制。字符串在class格式文件中的存储格式为：

```java
CONSTANT_Utf8_info {
u1 tag;
u2 length;
u1 bytes[length];
}
```


u2是无符号的16位整数，最大值为216-1=65535String。所以在字符串常量池里的限制为65535个字节（注意这里是字节，而不是字符）。

## Mybatis读取xml配置文件

`XML` 常见的解析方式有以下三种： `DOM`、 `SAX` 和 `StAX`。

- DOM(**Document Object Model** ) 方式：基于树形结构解析， 它会将整个文档读入内存并构建一个 `DOM` 树， 基于这棵树的结构对各个节点进行解析。

- SAX 方式：基于事件模型的 `XML` 解析方式， 它不需要将整个 `XML` 文档加载到内存中， 而只需要将一部分 `XML` 文档的一部分加载到内存中， 即可开始解析。

- StAX 方式：与 `SAX` 类似， 也是把 `XML` 文档作为一个事件流进行处理， 但不同之处在于 `StAX` 采用的是“拉模式”， 即应用程序通过调用解析器推进解析的过程。

那么Mybatis究竟采用哪种方式呢？最好让代码来告诉我们！

**Mybatis读取配置文件过程**

1.读取入口

```java
      Reader reader = Resources.getResourceAsReader("com/davidwang456/SqlMapConfig.xml");
      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);
```

2.SqlSessionFactoryBuilder调用XMLConfigBuilder来读取配置信息

SqlSessionFactoryBuilder的build方法接受的参数有两种类型：Reader和InputStream。其中Reader读取字符流，InputStream读取字节流。  

```java
 public SqlSessionFactory build(Reader reader, String environment, Properties properties) {
    try {
      XMLConfigBuilder parser = new XMLConfigBuilder(reader, environment, properties);
      return build(parser.parse());
    } catch (Exception e) {
      throw ExceptionFactory.wrapException("Error building SqlSession.", e);
    } finally {
      ErrorContext.instance().reset();
      try {
        reader.close();
      } catch (IOException e) {
        // Intentionally ignore. Prefer previous error.
      }
    }
  }
  public SqlSessionFactory build(InputStream inputStream, String environment, Properties properties) {
    try {
      XMLConfigBuilder parser = new XMLConfigBuilder(inputStream, environment, properties);
      return build(parser.parse());
    } catch (Exception e) {
      throw ExceptionFactory.wrapException("Error building SqlSession.", e);
    } finally {
      ErrorContext.instance().reset();
      try {
        inputStream.close();
      } catch (IOException e) {
        // Intentionally ignore. Prefer previous error.
      }
    }
  }
```

3.XMLConfigBuilder调用XPathParser解析器来读取

同样的，XMLConfigBuilder接收两种参数类型：

```java
   public XMLConfigBuilder(InputStream inputStream, String environment, Properties props) {
    this(new XPathParser(inputStream, true, props, new XMLMapperEntityResolver()), environment, props);
  } 
  public XMLConfigBuilder(Reader reader, String environment, Properties props) {
    this(new XPathParser(reader, true, props, new XMLMapperEntityResolver()), environment, props);
  }
  public XPathParser(Reader reader, boolean validation, Properties variables, EntityResolver entityResolver) {
    commonConstructor(validation, variables, entityResolver);
    this.document = createDocument(new InputSource(reader));
  }
  public XPathParser(InputStream inputStream, boolean validation, Properties variables, EntityResolver entityResolver) {
    commonConstructor(validation, variables, entityResolver);
    this.document = createDocument(new InputSource(inputStream));
  }
```

在这边，Reader和InputStream完成了统一，统一包装为InputSource。

4.XPathParser创建文档DocumentBuilder

```java
  private Document createDocument(InputSource inputSource) {
    // important: this must only be called AFTER common constructor
    try {
      DocumentBuilderFactory factory = DocumentBuilderFactory.newInstance();
      factory.setFeature(XMLConstants.FEATURE_SECURE_PROCESSING, true);
      factory.setValidating(validation);

      factory.setNamespaceAware(false);
      factory.setIgnoringComments(true);
      factory.setIgnoringElementContentWhitespace(false);
      factory.setCoalescing(false);
      factory.setExpandEntityReferences(true);

      DocumentBuilder builder = factory.newDocumentBuilder();
      builder.setEntityResolver(entityResolver);
      builder.setErrorHandler(new ErrorHandler() {
        @Override
        public void error(SAXParseException exception) throws SAXException {
          throw exception;
        }

        @Override
        public void fatalError(SAXParseException exception) throws SAXException {
          throw exception;
        }

        @Override
        public void warning(SAXParseException exception) throws SAXException {
          // NOP
        }
      });
      return builder.parse(inputSource);
    } catch (Exception e) {
      throw new BuilderException("Error creating document instance.  Cause: " + e, e);
    }
  }
```

5.DocumentBuilder实现类DocumentBuilderImpl调用DOM解析器

```java
    public Document parse(InputSource is) throws SAXException, IOException {
        if (is == null) {
            throw new IllegalArgumentException(
                DOMMessageFormatter.formatMessage(DOMMessageFormatter.DOM_DOMAIN,
                "jaxp-null-input-source", null));
        }
        if (fSchemaValidator != null) {
            if (fSchemaValidationManager != null) {
                fSchemaValidationManager.reset();
                fUnparsedEntityHandler.reset();
            }
            resetSchemaValidator();
        }
        domParser.parse(is);
        Document doc = domParser.getDocument();
        domParser.dropDocumentReferences();
        return doc;
    }
```

6.DOM解析器进行解析

```java
    /**
     * parse
     *
     * @param inputSource
     *
     * @exception org.xml.sax.SAXException
     * @exception java.io.IOException
     */
    public void parse(InputSource inputSource)
        throws SAXException, IOException {

        // parse document
        try {
            XMLInputSource xmlInputSource =
                new XMLInputSource(inputSource.getPublicId(),
                                   inputSource.getSystemId(),
                                   null);
            xmlInputSource.setByteStream(inputSource.getByteStream());
            xmlInputSource.setCharacterStream(inputSource.getCharacterStream());
            xmlInputSource.setEncoding(inputSource.getEncoding());
            parse(xmlInputSource);
        }

        // wrap XNI exceptions as SAX exceptions
        catch (XMLParseException e) {
            Exception ex = e.getException();
            if (ex == null) {
                // must be a parser exception; mine it for locator info and throw
                // a SAXParseException
                LocatorImpl locatorImpl = new LocatorImpl();
                locatorImpl.setPublicId(e.getPublicId());
                locatorImpl.setSystemId(e.getExpandedSystemId());
                locatorImpl.setLineNumber(e.getLineNumber());
                locatorImpl.setColumnNumber(e.getColumnNumber());
                throw new SAXParseException(e.getMessage(), locatorImpl);
            }
            if (ex instanceof SAXException) {
                // why did we create an XMLParseException?
                throw (SAXException)ex;
            }
            if (ex instanceof IOException) {
                throw (IOException)ex;
            }
            throw new SAXException(ex);
        }
        catch (XNIException e) {
            Exception ex = e.getException();
            if (ex == null) {
                throw new SAXException(e.getMessage());
            }
            if (ex instanceof SAXException) {
                throw (SAXException)ex;
            }
            if (ex instanceof IOException) {
                throw (IOException)ex;
            }
            throw new SAXException(ex);
        }

    } // parse(InputSource)
```

这里调用的是JDK底层的jaxp，jaxp的局限性较大，比如不能创建一个xml文件，只能读取已有的文件。jaxb提供了两种解析方式：DOM和SAX。但在Mybatis里选择了DOM解析器。至此，真相大白！

**XPATH**

XPath即为XML路径语言（XML Path Language），它是一种用来确定XML文档中某部分位置的语言。XPath 使用路径表达式来选取 XML 文档中的节点或者节点集。这些路径表达式和我们在常规的电脑文件系统中看到的表达式非常相似。**注意：它不是解析XML的方式。**



## 总结

- 字符串有长度限制，在编译器，因为字符串要放到常量池中，它要求长度不能超过65535(2个字节)，并且在javac执行过程中控制了最大值为65534.。
- 在运行期，长度不能超过Int的范围即小于2^32-1则不会报错。
- Mybais读取配置时不是存储在String，故没有Int范围的限制，因 其加载方式是全部加载到内存，故能配置文件的多少或者大小取决于你的内存大小。

- Mybatis在加载 `mybatis-config.xml` 配置文件与映射文件时， 使用的是 `DOM` 解析方式， 并配合使用 `XPath` 解析 `XML` 配置文件。`XPath` 之于 `XML` 就好比 `SQL` 之于数据库。