本故事纯属虚构，如有雷同，纯属巧合。

**故事背景**

小白今天刚上班就碰到一件郁闷的事情，测试 MM 扔过来一个 bug，说日期显示有问题。昨天还测试好好的，为什么今天会出现 bug 呢？还是要重头说起！

小白要实现一个记录奶酪生产期限的程序，昨天自测完后，提交给测试 MM 进行测试，测试 MM 测试完后说没有问题后小白才下班。今天早上测试 MM 走自动回归测试时，发现了 bug。

```java
import java.util.Date;
public class Cheese {
	public static final Cheese cheese=new Cheese();
	private final long produceTimes;
	private static final long produceDate =new Date(119,8,1).getTime();
	
	
	private Cheese() {
		produceTimes=new Date().getTime()-produceDate;
	}
	
	public long produceTimes() {
		return produceTimes;
	}
	
	public static void main(String[] args) {
		System.out.println("current time in day(from 1900:00:00) : "+new Date().getTime()/(1000*60*60*24L));
		
		System.out.println("cheese had produces : "+ cheese.produceTimes()/(1000*60*60*24L) +" days");
		
	}
}
```

小白把测试环境的代码拉下来，进行调试，发现运行结果果然不对：

```java
current time in day(from 1900:00:00) : 18153
cheese had produces : 18153 days
```

产品生产日期完全没有起效。检查了一下，是自己的代码呀。这就奇了怪了？

![img](image/3b9873c1df2f4129adaecca21ed19637.jfif)

**破案**

难道昨天和测试MM都眼花了？不可能呀！肯定是代码的问题。拉出看历史提交记录，发现仅有小胖对程序做了改动，将两行代码的顺序做了调换。调换前的程序如下:

```JAVA
import java.util.Date;
public class Cheese {
	
	private final long produceTimes;
	private static final long produceDate =new Date(119,8,1).getTime();//这里
	public static final Cheese cheese=new Cheese();//这里
	
	private Cheese() {
		produceTimes=new Date().getTime()-produceDate;
	}
	
	public long produceTimes() {
		return produceTimes;
	}
	
	public static void main(String[] args) {
		System.out.println("current time in day(from 1900:00:00) : "+new Date().getTime()/(1000*60*60*24L));
		
		System.out.println("cheese had produces : "+ cheese.produceTimes()/(1000*60*60*24L) +" days");
		
	}
}
```

运行结果：

```java
current time in day(from 1900:00:00) : 18153
cheese had produces : 13 days
```

这才是小白想要的结果，也是测试 MM 期望看到的结果。

原来小胖昨天写单元测试时，发现小白没有按照代码规范格式化代码，就帮小白格式化一下，顺便整理了一下顺序，整理后看得更容易读了。找到罪魁祸首后，小胖被迫接受一周的下午茶才结束。

**追根究底**

小胖无心的好意为什么会导致程序产生如此大的变化呢，小白一时搞不明白，越想越难受，感觉不找到根本的原因，恐怕吃饭都不香了。于是想到了扫地僧还没有下班，遂向他请教，免不了被勒索一顿好吃好喝的伺候。原来，实例的初始化也是有讲究的。

1. static 字段先设置默认值，其中 cheese 被设置为 null，produceDate 被设置为 0；

2. 然后 static 初始器执行，按照声明出现的顺序执行：

如果先执行 cheese 的话，调用 Cheese() 构造方法，此时用 produceDate=0 为值；

如果先执行 produceDate 的话，producteDate 被设置为 2019-09-01，再调用 cheese() 构造方法。

3. 最后从构造器返回 cheese 类的初始化。

**另外，还学习了新的一招**

Date 设置日期为 2019-09-01 为何设置为：

new Date(119,8,1)

进去源码看一眼：

```java
 /**
 * Allocates a <code>Date</code> object and initializes it so that
 * it represents midnight, local time, at the beginning of the day
 * specified by the <code>year</code>, <code>month</code>, and
 * <code>date</code> arguments.
 *
 * @param year the year minus 1900.
 * @param month the month between 0-11.
 * @param date the day of the month between 1-31.
 * @see java.util.Calendar
 * @deprecated As of JDK version 1.1,
 * replaced by <code>Calendar.set(year + 1900, month, date)</code>
 * or <code>GregorianCalendar(year + 1900, month, date)</code>.
 */
 @Deprecated
 public Date(int year, int month, int date) {
 this(year, month, date, 0, 0, 0);
 }
```

其中，**year** 份是从 1900 年开始的年数，即 2019-1900=119；

**month** 是 0~11 计数的，需要实际月份减 1，即 9-1=8；

**date** 是 1~31 计数的，实际天就可以 即 1。

