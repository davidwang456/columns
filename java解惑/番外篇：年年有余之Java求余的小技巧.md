本故事纯属虚构，如有雷同，纯属巧合。

**故事背景**

快要过年了，小白手头的工作逐渐的减少，现在每天不少时间来自己安排，今天他突然就想到了年年有余这个古老的传言，于是就去网上找其来源。

> 传说里玉皇大帝派龙王马上降雨到共光一带，龙王接到玉皇大帝命令，立马从海上调水，跑去共光施云布雨，但粗心又着急的龙王不小心把海里的鲸鱼随着雨水一起降落在了共光，龙王怕玉皇大帝责怪，灵机一动便声称他是派鱼到共光，希望百姓可以年年有余，并请求玉皇大帝将这条鱼任命为鱼神，保佑人间太平可以年年有余。

![img](image/807462b3a0f94cf48425fafd9729d114.jfif)

**Java 求余操作初阶**

Java 中也有余的规范【JLS-15.17.3】，废话不说，直接上代码，从中我们可以学到很多技巧：

例1：

```java
int a = 5%3; // 2
int b = 5/3; // 1
System.out.println("5%3 produces " + a +" (note that 5/3 produces " + b + ")");
```

相信大多数人都知道结果了：

```java
5%3 produces 2 (note that 5/3 produces 1)
```

**Java 求余操作中阶**

我们知道，正数不仅仅有正整数还有负整数，那么负数的情况下，会出现什么变化呢？

例2：

```java
int c = 5%(-3); // 2
int d = 5/(-3); // -1
System.out.println("5%(-3) produces " + c +" (note that 5/(-3) produces " + d + ")");
int e = (-5)%3; // -2
int f = (-5)/3; // -1
System.out.println("(-5)%3 produces " + e +" (note that (-5)/3 produces " + f + ")");
int g = (-5)%(-3); // -2
int h = (-5)/(-3); // 1
System.out.println("(-5)%(-3) produces " + g +" (note that (-5)/(-3) produces " + h + ")");
```

能完全正确得到结果的就很少了吧？

```java
5%(-3) produces 2 (note that 5/(-3) produces -1)
(-5)%3 produces -2 (note that (-5)/3 produces -1)
(-5)%(-3) produces -2 (note that (-5)/(-3) produces 1)
```

![img](image/812f60ac933847e4a5b93d5760e7e396.gif)



为什么求余的结果是这样的呢？jls-15.17.3 规范告诉我们：

> The binary % operator is said to yield the remainder of its operands from an implied division; the left-hand operand is the dividend and the right-hand operand is the divisor.
>
> It follows from this rule that the result of the remainder operation can be negative only if the dividend is negative, and can be positive only if the dividend is positive. Moreover, the magnitude of the result is always less than the magnitude of the divisor.
>
> 注：二元%操作符执行隐含的除法，产生操作数的余数，左操作数是被除数，右操作数是除数。
>
> 按照上面的规则，取余操作的结果只有在被除数为负数时才能时负数，且只有在被除数为正数时才能时正数。而且，结果的数量级永远都比除数的数量级小。

注意：求余的正负数给 dividend(左边操作数) 的符号位一致！

**Java 求余操作高阶**

Java求余操作不但支持整数，还支持浮点数：

```java
class Test2 {
 public static void main(String[] args) {
 double a = 5.0%3.0; // 2.0
 System.out.println("5.0%3.0 produces " + a);
 double b = 5.0%(-3.0); // 2.0
 System.out.println("5.0%(-3.0) produces " + b);
 double c = (-5.0)%3.0; // -2.0
 System.out.println("(-5.0)%3.0 produces " + c);
 double d = (-5.0)%(-3.0); // -2.0
 System.out.println("(-5.0)%(-3.0) produces " + d);
 }
}
```

相信很多人可以根据整型的规则，得出正确的结果：

```java
5.0%3.0 produces 2.0
5.0%(-3.0) produces 2.0
(-5.0)%3.0 produces -2.0
(-5.0)%(-3.0) produces -2.0
```

补充一下，浮点型的求余有一些特殊的规则：

> The result of a floating-point remainder operation as computed by the % operator is not the same as that produced by the remainder operation defined by IEEE 754. The IEEE 754 remainder operation computes the remainder from a rounding division, not a truncating division, and so its behavior is not analogous to that of the usual integer remainder operator. Instead, the Java programming language defines % on floating-point operations to behave in a manner analogous to that of the integer remainder operator; this may be compared with the C library function fmod. The IEEE 754 remainder operation may be computed by the library routine Math.IEEEremainder.
>
> 按照%操作符计算的浮点数取余操作的结果与IEEE754定义的浮点数取余操作所产生的结果不同。IEEE754取余操作按照舍入除法而不是截尾除法计算余数，因此其行为不能类比通常的整数取余操作符的行为。因此，java变成语言在浮点数操作上定义%以类比整数取余操作符的行为，这可以对应C类库的函数fmod。IEEE754取余操作可以用类库例程Math.IEEEremainder来计算。
>
> The result of a floating-point remainder operation is determined by the rules of IEEE 754 arithmetic:
>
> 浮点取余的结果由IEEE754算术规则确定：
>
> If either operand is NaN, the result is NaN.
>
> 如果两个操作数之一是NaN，那么结果就是NaN
>
> If the result is not NaN, the sign of the result equals the sign of the dividend.
>
> 如果结果不是NaN,那么结果的符号等于被除数的符号。 
>
> If the dividend is an infinity, or the divisor is a zero, or both, the result is NaN.
>
> 如果被除数是无穷大，或者除数是0或者两者同事满足，那么结果就是NaN。
>
> If the dividend is finite and the divisor is an infinity, the result equals the dividend.
>
> 如果被除数是有穷值且除数是无穷大，那么结果就是被除数。
>
> If the dividend is a zero and the divisor is finite, the result equals the dividend.
>
> 如果被除数是0且除数是有穷值，那么结果就是被除数。
>
> In the remaining cases, where neither an infinity, nor a zero, nor NaN is involved, the floating-point remainder r from the division of a dividend n by a divisor d is defined by the mathematical relation r = n - (d ? q) where q is an integer that is negative only if n/d is negative and positive only if n/d is positive, and whose magnitude is as large as possible without exceeding the magnitude of the true mathematical quotient of n and d.
>
> 在其余情况中(既不涉及无穷大、0或者NaN)，会从被除数n和除数b的除法中产生浮点余数r，产生方式满足算术关系r=n-(d-q),其中q只有在n/d为负时是负整数，在n/d为正时时正整数，并且其数量级取在不超过n和d的真正数学上的商的数量级的情况下尽可能大的值。
>
> Evaluation of a floating-point remainder operator % never throws a run-time exception, even if the right-hand operand is zero. Overflow, underflow, or loss of precision cannot occur.
>
> 浮点取余操作符%的计算永远都不会抛出运行时异常，尽管右操作数可能是0。上溢、下溢或者精度丢失也都不会发生。

**Java 求余操作骨灰级**

学到这里，或许有人沾沾自喜，我都掌握了求余的所有规则，看来需要给你泼泼冷水：

```java
 public static void main(String[] args) {
 final int MODULUS = 3;
 int[] histogram = new int[MODULUS];
 // Iterate over all ints (Idiom from Puzzle 26)
 int i = Integer.MIN_VALUE;
 do {
 	histogram[Math.abs(i) % MODULUS]++;
 } while (i++ != Integer.MAX_VALUE);
 for (int j = 0; j < MODULUS; j++)
	 System.out.println(histogram[j] + " ");
 }
```

这个程序会打印什么？有人经过繁琐复杂的算出一个结果：

```java
1431655765 
1431655766 
1431655765
```

但其实，上述程序运行报错(数组越界异常)：

```java
Exception in thread "main" java.lang.ArrayIndexOutOfBoundsException: -2 at com.java.puzzlers.ModTest.main(ModTest.java:11)
```

为什么数组会出现索引 -2？奇怪吧？要回答这个问题，我们必须要去看看 Math.abs 的文档：

```java
 /**
 * Returns the absolute value of an {@code int} value.
 * If the argument is not negative, the argument is returned.
 * If the argument is negative, the negation of the argument is returned.
 *
 * <p>Note that if the argument is equal to the value of
 * {@link Integer#MIN_VALUE}, the most negative representable
 * {@code int} value, the result is that same value, which is
 * negative.
 *
 * @param a the argument whose absolute value is to be determined
 * @return the absolute value of the argument.
 */
 public static int abs(int a) {
 return (a < 0) ? -a : a;
 }
```

特意说明，如果是 Integer#MIN_VALUE，返回负数。

Java里有很多小技巧，需要我们勤翻 API 和 JLS，多学习多练习。

参考资料：

【1】https://baike.baidu.com/item/%E5%B9%B4%E5%B9%B4%E6%9C%89%E4%BD%99/7625174?fr=aladdin

【2】https://docs.oracle.com/javase/specs/jls/se12/html/jls-15.html#jls-15.17.3

【3】《Java解惑》