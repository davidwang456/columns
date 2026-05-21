本故事纯属虚构，如有雷同，纯属巧合。

**故事背景**

当一个人问另一个人“敢不敢”的时候，另一个人必须说“敢”，这就是游戏的规则。小男孩朱利安和小女孩苏菲的相遇即开始于这样一场孩童的闹剧，一个精美的铁盒子就是他们游戏的见证。说脏话，扰乱课堂，在校长室小便，内衣外穿，一个游戏两人一玩十多年，他们什么都敢，除了承认彼此相爱。

苏菲（玛丽昂·歌迪亚饰）提议两人分别十年，挑战的内容是朱利安（吉约姆·卡内饰）敢不敢伤害苏菲。恍惚十年逝去，朱利安找到苏菲，为了游戏的进行他决定另娶她人，邀请苏菲做伴娘。受到伤害的苏菲在朱利安的婚礼上抛出铁盒子“你敢悔婚么？”原本最最亲密的朋友相互伤害最深。同样心痛的两个人相约再次分别十年。

十年里，朱利安拥有了一切，家庭、事业、朋友，只是没了苏菲，宛如没了心，原来丧失激情的生活这般索然无味。

终于十年过去，“Love me, if you dare...”。

![img](image/c145a7dfd9814542b25615573072fa90.jfif)

**Java 中 =+ 和 += 的关系揭秘**

Java 有一对关系密切的对象，=+ 和 +=，许多程序员都会认为表达式（x += i）只是表达式（x =x + i）的简写方式，真的是这样吗？请看程序：

```java
 public static void main(String[] args) {
 short x = 0;
 int i = 123456;
 x += i;
 System.out.println(x);
 }
```

这个程序的结果是什么？

运行后结果是：

-7616

<img src="image/00cb41c7954547f78048a521c317f57e.jfif" alt="img" style="zoom:33%;" />

不急，来看下面的程序：

```java
 public static void main(String[] args) {
 	short x = 0;
 	int i = 123456;
 	i = x + i;
 	System.out.println(i);
 }
```

结果如我们预期：

```java
123456
```

探究原因：

在 JSl15.26 中定义，= 是简单的赋值表达式，+= 是复杂的赋值表达式，其中 += 表达式满足规则：

> A compound assignment expression of the form E1 op= E2 is equivalent to E1 = (T) ((E1) op (E2)), where T is the type of E1, except that E1 is evaluated only once.

看不懂不用着急，中文：

（复合赋值操作符包括 +=、-=、*=、/=、%=、<<=、>>=、>>>=、&=、^= 和 |=）Java 语言规范中讲到，复合赋值 `E1 op= E2` 等价于简单赋值 `E1 =(T)((E1)op(E2))`，其中 `T` 是 `E1` 的类型，除非 `E1` 只被计算一次。

举例如下：

```java
short x = 3;
x += 4.6;
```

等同于：

```java
short x = 3;
x = (short)(x + 4.6);
```

分析例一

```java
 short x = 0;
 int i = 123456;
 x += i;
```

等同于：

```java
 short x = 0;
 int i = 123456;
 x = (short)(x + i);
```

第一步，`x+i` 结果是 `int`，第二步是 `int` 转 `short` 是窄化，会丢失精度。得到 `-7616` 就可以理解了。

分析例二

```java
 short x = 0;
 int i = 123456;
 i = x + i;
```

i 是 int 类型，x 是 short 类型，如果是 x=x+i 编译不能通过，报错：

```java
Type mismatch: cannot convert from int to short
```

如果使用 i=x+i 则没有问题，结果为 12345。

![img](image/d7c99ab63082483c8b5fa6f3b399cbf4.jfif)

参考资料

【1】Java解惑