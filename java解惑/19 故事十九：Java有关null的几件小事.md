本故事纯属虚构，如有雷同，纯属巧合。

**故事背景**

对中小学学生来说，流传着“一怕文言文，二怕写作文，三怕周树人”的传说。“但小白最喜欢鲁迅的作品了，很是犀利，也还有现代感。

正如：《一件小事》讲述的是在虚伪的时代，车夫撞到人但是在并没有其他人看见，而且冒着被人讹诈的情况下还去帮助老人的故事。

![img](image/e555849273634edd9207fb2711f33bb4.jfif)

鲁迅在最后说到：

> 这事到了现在，还是时时记起。我因此也时时煞了苦痛，努力的要想到我自己。几年来的文治武力，在我早如幼小时候所读过的“子曰诗云”一般，背不上半句了。独有这一件小事，却总是浮在我眼前，有时反更分明，教我惭愧，催我自新，并且增长我的勇气和希望。

Java 中的 null 看着很简单，也容易被很多人忽略，有时候也让人不知所措。

**Java 中的空 null**

我们先看几段代码吧。

1.例一：null 的对象性

```java
public class NullTest {
 public static void greet() {
 	System.out.println("Hello world!");
 }
 public static void main(String[] args) {
 	((NullTest) null).greet();
 }
}
```

上面的程序看起来似乎应该抛出 NullPointerExceptioin 异常，因为其 main 方法是在常量 null 上调用 greet 方法，而你是不可以在 null 上调用方法的，对吗？

其实编译和运行都没有问题。运行结果为：

```
Hello world!
```

2.例二：null 的初始化

```JAVA
 public static void main(String[] args) {
 String str=null;
 Integer in=null;
 Double dou=null;
 
 String str1=(String)null;
 Integer in1=(Integer)null;
 Double dou1=(Double)null;
 
 int in2=null;
 int in3=(int)null;
 }
```



![img](image/c63db856881f4d67bdbdb4e0b5e27fc9.jfif)

发现 null 可以初始化引用类型，也可以转换为任意的引用类型。但不能给基本类型赋值，或者转换为基本类型。

3.例三：null 的相等性

```java
 public static void main(String[] args) {
 	System.out.println(null==null);
 	System.out.println(null!=null); 
 	System.out.println(Double.NaN==Double.NaN);
 	System.out.println(Double.NaN!=Double.NaN);
 }
```

结果该是什么呢？

```java
true
false
false
true
```

4.例四：null 不是引用类型

```java
 public static void main(String[] args) {
 	Integer in=null;
 	if(in instanceof Integer) {
 		System.out.println("null is integer");
 	}else {
 		System.out.println("null is not integer");
 	}
 }
```

你猜会打印出什么？

![img](image/3cbef1169b1b427bb1995f6aad39d073.jfif)

结果是：

```
null is not integer
```

5.例5：不可传递

```java
 public static void main(String[] args) {
 Integer i=null;
 int k=i;
 System.out.println(k);
 }
```

报错：

```java
Exception in thread "main" java.lang.NullPointerException
NullTest.main(NullTest.java:6)
```

6.例6：null 的数组

```java
 public static void main(String[] args) {
 String[] arr1={"abc","123",null,"sky"};
 boolean flag=false;
 for (String s1 : arr1) {
 	if(s1.equals("sky")) {
 		flag=true;
 		break;
 	}
 }
 System.out.println(flag);
 }
```

运行时报错

```java
Exception in thread "main" java.lang.NullPointerException
at NullTest.main(NullTest.java:8)
```

修改成:

```java
 public static void main(String[] args) {
 String[] arr1={"abc","123",null,"sky"};
 boolean flag=false;
 for (String s1 : arr1) {
 	if("sky".equals(s1)) {//对比前后顺序
 		flag=true;
 		break;
 	}
 }
 System.out.println(flag);
 }
```

就没有问题了。

**追根到底**

JSL 3.10.7 定义了 null

> The null type has one value, the null reference, represented by the null literal null, which is formed from ASCII characters.
>

JSL 4.1 做了补充：

> 1.There is also a special null type, the type of the expression null (§3.10.7, §15.8.1), which has no name.
>
> Because the null type has no name, it is impossible to declare a variable of the null type or to cast to the null type.

注：null 是一种特殊类型，它的表达式为 null，但没有名称。因为 null 类型没有名称，故不能声明一个 null 类型（如 private null a），也不能将一个类型转为null类型。
>
> 2.The null reference is the only possible value of an expression of null type.

注：使用 null 类型的唯一方式是使用 null 引用（如 private Integer a = null）;
>3.The null reference can always be assigned or cast to any reference type (§5.2, §5.3, §5.5).

注：空引用可以赋值给其他任意类型，如 String，Integer，Class 等等。
>
> 4.In practice, the programmer can ignore the null type and just pretend that null is merely a special literal that can be of any reference type.

 注：其实，程序开发者可以忽略 null 类型，仅仅将它当作一种可以赋值给其他任意类型的特殊引用。



