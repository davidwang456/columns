本故事纯属虚构，如有雷同，纯属巧合。

网上有个段子，讲如何卖彩票的。他是怎么卖的呢？他说：

> 你在我这有500万的存款，但是很可惜，你忘了密码。
>
> 不过没关系，你可以花2块钱猜一次。猜对了你就可以把500万拿走了。

**故事背景**

据一名网友说，他们的公司的年会的中奖概率有点“耐人寻味”，CTO 说决定回去 review 一下抽奖程序，

![img](image/7bc12fbcda8c4d4aa537cb713ed967ba.jfif)

然后这个倒霉的程序员就决定上台主动展示抽奖源代码，可据这名网友说，当时在台下的人就有 1000 多个，这样的情况下 review 代码实在是太“壮观”了，他想了解这名程序员的心里阴影面积。

![img](image/dcdd78f76096419dbc8f9965fdbacdba.jfif)

公司年会的抽奖程序，你们准备自己写么？Java 程序员们要小心了，随机数的获取有玄机。

**Java中如何取随机数？**

随机数的获取方法有很多种，我们先看看最简单的两种吧。

```java
	private static final Random rnd = new Random();
	//第一种
	static int random(int n) {
		return n>=0 ? rnd.nextInt(n) : rnd.nextInt(Math.abs(n));
	}
	//第二种
	static int random2(int n) {
		return Math.abs(rnd.nextInt())%n;
	}
```

大家看看第一种和第二种有什么不同？

不太容易看出来吧？我们来跑个简单的程序看看：

```java
	public static void main(String[] args) {
		int n = 1000;
		int di = 0;
		int low = 0;
		for(int i = 0;i < 10_000_000;i++) {
			if(random(n) < Math.abs(n/2)) {
				di++;
			}
			
			if(random2(n) < Math.abs(n/2)) {
				low++;
			}
		}
		System.out.println(di);
		System.out.println(low);
	}
```

发现我们测试了很多数字都是相同的，是不是就认为他们差不多呢。

```java
public static void main(String[] args) {		
		int n = 2*(Integer.MAX_VALUE/3);
		int di = 0;
		int low = 0;
		for(int i=0;i<10_000_000;i++) {
			if(random(n)<Math.abs(n/2)) {
				di++;
			}
			
			if(random2(n)<Math.abs(n/2)) {
				low++;
			}
		}
		System.out.println(di);
		System.out.println(low);
	}
```

运行数据看结果就会发现：

- random 的数据比较均匀；

- random2 的数据差不多 2/3 落到前半部分，后半部分只有 1/3。


**究根追底**

nextInt() 这个方法看起来可能不错，但是存在三个缺点。

第一个缺点是： 如果 n 是比较小的 2 的乘方，经过一段相当短的周期之后，它产生的随机数将会重复。

第二个缺点是：如果 n 不是 2 的乘法，那么平均起来，有些数就会比其它的数出现的更频繁。特别是 n 比较大，这个缺点就非常明显。这就是为什么 `2*(Integer.MAX_VALUE/3)` 中有用2乘的原因。

第三个缺点是：在极少数情况下，会返回一个落在指定范围之外的数或者报错。示例如下：

```java
	public static void main(String[] args) {
		int n=Integer.MIN_VALUE;
		int di=0;
		int low=0;
		for(int i=0;i<10_000_000;i++) {
			if(random(n)<Math.abs(n/2)) {
				di++;
			}
			
			if(random2(n)<Math.abs(n/2)) {
				low++;
			}
		}
		System.out.println(di);
		System.out.println(low);
	}
```

报错原因 Math.abs() 碰到 Integer.MIN_VALUE 时会返回 Integer.MIN_VALUE。在 abs 的方法说明中有这么一段话：

> Note that if the argument is equal to the value of {@link Integer#MIN_VALUE}, the most negative representable{@code int} value, the result is that same value, which is negative.

即注意：Math.abs() 方法中，如果输入值为 Integer.MIN_VALUE，那么会返回同样的结果。

另一方面，也可以看看 abs 的代码实现来理解，

` public static int abs(int a) {
 	return (a < 0) ? -a : a;
 }` 

假设 a=Integer.MIN_VALUE 即 -2147483648(0x80000000)，假设返回 int 的值 2147483648 会发生溢出，因为 int 的最大值为 2147483647(0x7fffffff)，溢出后又变成了 0x80000000，即 Integer.MIN_VALUE

源码详细描述如下：

> ```java
>  /**
>  * Returns the absolute value of an {@code int} value.
>  * If the argument is not negative, the argument is returned.
>  * If the argument is negative, the negation of the argument is returned.
>  *
>  * <p>Note that if the argument is equal to the value of
>  * {@link Integer#MIN_VALUE}, the most negative representable
>  * {@code int} value, the result is that same value, which is
>  * negative.
>  *
>  * @param a the argument whose absolute value is to be determined
>  * @return the absolute value of the argument.
>  */
>  public static int abs(int a) {
>  	return (a < 0) ? -a : a;
>  }
> ```
>

Java 类库提供了一个带 seed 的方法来解决上面的问题，就是 Random.nextInt(n)。

**总结**

随机数的生成器涉及了很多算法的相关知识，幸运的时，我们并不需要自己来做这些工作，我们可以利用现成的成果为我们所用，如 Random.nextInt(n)或者java.security.SecureRandom，或者第三方的 API。注意：我们尽量使用类库，而不是自己去开发。

Linux系统有/dev/random,/dev/urandom向用户提供真随机数。 

http://random.irb.hr/ 是一个免费为学术和科研机构提供真随机数字服务的网站

http://random.org/在Internet上提供真随机数服务了，它用大气噪音生成真随机数

