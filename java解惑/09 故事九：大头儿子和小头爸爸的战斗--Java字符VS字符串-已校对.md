本故事纯属虚构，如有雷同，纯属巧合！

**故事背景**

一座普普通通的小屋里，住着大头儿子、小头爸爸和围裙妈妈。在他们普普通通的生活中，总是响起充满欢乐的笑声。最温暖的家又成了他们每个人的爱的源泉。

《大头儿子和小头爸爸》是孩子居首（大头），妈妈居中，爸爸最末（小头）；爸爸主外，妈妈主内（围裙），他们是中国现代家庭教育典型的缩影。

![img](image/b1f5f49de6c046d8aee44613da751c10.jfif)

**Java中的大头儿子和小头爸爸**

Java中也有一对冤家对头，它们就是字符串 `String`和字符 `char` 。来看看它们的表现吧：

```java
System.out.println("h"+"i");
System.out.println('h'+'i');
```

会打印出什么呢？结果可能出乎你的意料：

```java
hi
209
```

为什么会出现 209 这个结果呢？

编译器在计算常量表达式 'h'+'i' 时，是通过我们熟知的拓宽原始类型转换，将两个具有字符型数值的操作数（'h'和'i'）提升为 int 数值而实现的。从 char 到 int 的拓宽原始类型转换，是将 16 位的 char 数值零扩展到 32 位的 int。对于'h'，char 数值是 104，而对于'i'，char 数值是 105，因此表达式 'h'+'i' 等价于 int 常量 104 + 105，或 209。

有三种方式避免出现 char 的连接问题。第一种最简单：

```java
System.out.println("" + 'h' + 'i');
```

第二种：使用函数：

```java
System.out.printf("%c%c", 'h', 'i');
```
或者：
```
System.out.println(String.format("%c%c", 'h','i'));
```

第三种，利用 API 拼装：

```java
StringBuffer sb = new StringBuffer();
sb.append('h');
sb.append('i');
System.out.println(sb);
```

也许你会认为这比较简单，那我们就见识一个比较复杂点的吧！

```java
	private static Random rnd = new Random();
	public static void main(String[] args) {
		StringBuffer word = null;
		switch(rnd.nextInt(2)) {
			case 1: word = new StringBuffer('P');
			case 2: word = new StringBuffer('G');
			default: word = new StringBuffer('M');
	}
	word.append('a');
	word.append('i');
	word.append('n');
	System.out.println(word);
	}
```

乍一看，这个程序可能会在一次又一次的运行中，以相等的概率打印出 Pain、Gain 或 Main。看起来该程序会根据随机数生成器所选取的值来选择单词的第一个字母：0 选 M，1 选 P，2 选 G。但它实际上既不会打印 `Pain`，也不会打印 `Gain`。也许更令人吃惊的是，它也不会打印 `Main`，并且它的行为不会在一次又一次的运行中发生变化，它总是在打印 `ain`。这又是为什么呢？

多个问题，纠结在一起导致了这个问题：

1. Random.nextInt(int) 的规范描述是这样写的：“返回一个伪随机地、均等地分布在从 0（包括）到指定的数值（不包括）之间的一个int 数值”[Java-API]。这意味着表达式rnd.nextInt(2)可能的取值只有 0 和 1，Switch 语句将永远也到不了case2 分支，这表示程序将永远不会打印 Gain。nextInt 的参数应该是`3` 而不是`2`；
2. case 中没有任何 break 语句。不论 switch 表达式为何值，该程序都将执行其相对应的 case 以及所有后续的 case [JLS 14.11]。因此，尽管每一个 case 都对变量 word 赋了一个值，但是总是最后一个赋值胜出，覆盖了前面的赋值。最后一个赋值将总是最后一种情况（default），即 new StringBuffer{'M'}。这表明该程序将总是打印 Main，而从来不打印 Pain 或 Gain；

3. 在本例中，编译器会选择接受 int 的构造器，通过拓宽原始类型转换把字符数值'M'转换为一个 int 数值 77[JLS 5.1.2]。换句话说，new StringBuffer('M') 返回的是一个具有初始容量 77 的空的字符串缓冲区。该程序余下的部分将字符 a、i 和 n 添加到了这个空字符串缓冲区中，并打印出该字符串缓冲区那总是 ain 的内容。

那怎么样呢，修改如下：

```java
	private static Random rnd = new Random();
	public static void main(String[] args) {
	System.out.println("PGM".charAt(rnd.nextInt(3)) + "ain");
	}
```

参考资料

【1】Java解惑

【2】https://baike.baidu.com/item/%E5%A4%A7%E5%A4%B4%E5%84%BF%E5%AD%90%E5%92%8C%E5%B0%8F%E5%A4%B4%E7%88%B8%E7%88%B8/2346537?fromtitle=%E5%A4%A7%E5%A4%B4%E5%84%BF%E5%AD%90%E5%B0%8F%E5%A4%B4%E7%88%B8%E7%88%B8&fromid=3076860&fr=aladdin