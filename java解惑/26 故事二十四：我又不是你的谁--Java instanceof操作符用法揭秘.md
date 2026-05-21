本故事纯属虚构，如有雷同，纯属巧合。

**故事背景**

月老，是我们中国神话中的爱情之神，专门为有缘男女牵媒拉线，所谓“千里姻缘一线牵，有缘千里来相会”正是他的口头禅，只要前世有约，那么今生就算门不当户不对，月老也会竭尽所能促成有缘人终成眷属。

![img](image/b50cc1f640db47a2ba1685017bafb340.jfif)

爱神丘比特是西方神话中的爱情之神，主管爱情和婚姻，手中握有一把弓以及一支箭，被他的箭射中的一男一女，无论前世有约，还是有缘无分，皆可成为一对恋人。

![img](image/92d84f70fb48493e950aae83a78c7940.jfif)

月老和丘比特都负责人间的匹配，那么在 Java 中，谁负责对象的牵线匹配呢？

**牵线之牛刀小试**

如何判断是不是谁的谁？Java有一个 instanceof 操作符（关系操作符）可以做这件事。

```java
 public static void main(String[] args) {
 	String s = "Hello World!";
 	System.out.println(s instanceof String);
 }
```

打印出结果：

```java
true
```

可是如果你的那个谁不存在呢？请看代码：

```java
 public static void main(String[] args) {
 String s = null;
 System.out.println(s instanceof String);
 }
```

很多人都会异口同声的说：

```java
false
```

![img](image/eda74463c5ba44aa94b471dba18f72d8.gif)

你答对了!

JSL-15.20.2 规定

> At run time, the result of the instanceof operator is true if the value of the RelationalExpression is not null and the reference could be cast to the ReferenceType without raising a ClassCastException. Otherwise the result is false.
>
> 注：在运行期，如果关系表达式不为空，并且类型转换时不会抛出ClassCastException异常时，instanceof表达式返回结果为真，否则结果为假

**牵线之乱点鸳鸯谱**

如果没有任何关系的两个类使用 instanceof 会如何？

```java
class Point { int x, y; }
class Element { int atomicNumber; }

public class InstanceofTest {
 public static void main(String[] args) {
 	Point p = new Point();
	 Element e = new Element();
	 if (e instanceof Point) { 
 		System.out.println("匹配成功!");
 	}else {
 		System.out.println("匹配不成功");
 	}
 }
}
```

不少人会说：“匹配不成功”

![img](image/4f094ccb59ee4ec7b3e6436a6a6f5f21.gif)

抱歉，你又掉进坑里了，这个会报编译错误:

![img](image/81012c1451db46c9a6b2719af8faa1a6.jfif)

JSL-15.20.2规定

> The type of the RelationalExpression operand of the instanceof operator must be a reference type or the null type, or a compile-time error occurs.
>
> 注：instanceof操作符的关系表达式操作数的类型必须是一个引用类型或者null类型，否则抛出编译错误
>
> It is a compile-time error if the ReferenceType mentioned after the instanceof operator does not denote a reference type that is reifiable。
>
> 注：如在instanceof操作符后面提及的引用类型表示的不是可具化(§4.7)引用类型.就会抛出编译错误。
>
> If a cast of the RelationalExpression to the ReferenceType would be rejected as a compile-time error (§15.16), then the instanceof relational expression likewise produces a compile-time error. In such a situation, the result of the instanceof expression could never be true.
>
> 注：如从关系表达式到引用类型的强制类型转换将作为编译时错误而拒绝，那么instanceof关系表达式也就同样地产生编译时错误。这种情况下，instanceof表达式的结果也永远不会是真。

当然，cast 也会是编译错误:

```java
class Point { int x, y; }
class Element { int atomicNumber; }
public class InstanceofTest {
 	public static void main(String[] args) {
 		Point p = new Point();
 		Element e = new Element();
		p = (Point)e; // compile-time error
 	}
}
```

**牵线之暗藏玄机**

编译器并不是万能的，并不能检测出所有问题，看下面：

```java
class Point { int x, y; }
class Element { int atomicNumber; }
public class InstanceofTest {
 	public static void main(String[] args) {
 	Point p = new Point();
 	//Element e = new Element();
 	p = (Point) new Object();
 	System.out.println(p instanceof Point);
 }
}
```

猛一看，没事问题，编译也没有问题，可是运行时报错：

```java
Exception in thread "main" java.lang.ClassCastException: java.lang.Object cannot be cast to Point
```

上面的程序展示了，当要被转型的表达式的静态类型是转型类型的超类时，转型操作符的行为。与 instanceof 操作相同，如果在一个转型操作中的两种类型都是类，那么其中一个必须是另一个的子类型。尽管对我们来说，这个转型很显然会失败，但是类型系统还没有强大到能够洞悉表达式 new Object() 的运行期类型不可能是 Point 的一个子类型。因此，该程序将在运行期抛出 ClassCastException 异常。

**牵线之竞争激烈**

关系操作符 instanceof 可不是市场上唯一的选择，另外一个背靠大山的家伙要注意了：

Class 的方法

```java
booleanisInstance(Object obj)

Determines if the specified Object is assignment-compatible with the object represented by this Class.
```

那么什么时候该用 instanceof 什么时候该用 isInstance 呢 ？ 我的理解是：

*instanceof 偏向于比较 class之间*

*isInstance 偏向于比较 instance 和 class 之间*

stackoverflow 也有此问题的解答：

> I take that to mean that isInstance() is primarily intended for use in code dealing with type reflection at runtime. In particular, I would say that it exists to handle cases where you might not know in advance the type(s) of class(es) that you want to check for membership of in advance (rare though those cases probably are).
>
> 注：我认为这意味着isInstance()主要用于在运行时处理类型反射的代码中。特别是，我想说它的存在是为了处理，您可能事先不知道您想要检查的类的类型(s)(尽管这些情况可能很少)的情况。
>
> For instance, you can use it to write a method that checks to see if two arbitrarily typed objects are assignment-compatible, like:
>
> 注：例如，您可以使用它来编写一个方法，检查两个任意类型的对象是否兼容，如:

```
public boolean areObjectsAssignable(Object left, Object right) {
 return left.getClass().isInstance(right);
} 
```

> In general, I'd say that using instanceof should be preferred whenever you know the kind of class you want to check against in advance. In those very rare cases where you do not, use isInstance() instead.
>
> 注：一般来说，我想说，当您知道要提前检查的类的类型时，应该首选使用instanceof。在不使用isInstance()的非常罕见的情况下，可以使用isInstance()。

总结

回归本源，instanceof 是J ava 中的二元运算符，左边是对象，右边是类；当对象是右边类或子类所创建对象时，返回 true；否则，返回 false。

