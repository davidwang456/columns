本故事纯属虚构，如有雷同，纯属巧合。

**故事背景**

小白至今还会诵读的经典电影《大话西游之仙履奇缘》

> “曾经有一份真诚的爱情摆在我的面前，但是我没有珍惜，等到了失去的时候才后悔莫及，尘世间最痛苦的事莫过于此。如果可以给我一个机会再来一次的话，我会跟那个女孩子说我爱她，如果非要把这份爱加上一个期限，我希望是一万年！”

![img](image/03a3df5628ea461bb983865bbd048eb0.jfif)

《大话西游之大圣娶亲》（又名《大话西游之仙履奇缘》）是周星驰彩星电影公司 1994 年制作和出品的一部经典的无厘头搞笑爱情片，改编依据是吴承恩所撰写的神怪小说《西游记》，该片是《大话西游》系列的第二部，由刘镇伟导演，周星驰制作，周星驰、朱茵、莫文蔚、蔡少芬、陆树铭、吴孟达等人主演。

![img](image/787486a4b6d04a4aa05a12989840f30f.jfif)

女主角朱茵一度是小白的梦中情人。还有“月光宝盒”的时间穿越也给小白留下了深刻的印象。

其实 Java 中关于时间的设计经历了 Date，Calendar，到最后引用第三方包 Joda time，都发生了什么？让我们看看吧。

**Java 时间前生之 Date**

在 Java 平台首次发布时，它唯一支持日历计算类的就是 Date 类。这个类在能力方面是受限的，特别是当需要支持国际化时，它就暴露出了一个基本的设计缺陷：Date 实例是易变的。

Date 会产生什么问题呢？请看一下下面程序的输出：

```java
	public static void main(String[] args) {
		Date date=new Date(2018,12,31,0,0,0);		
		System.out.println(date.getYear());
		System.out.println(date.getMonth());
		System.out.println(date.getDay());
	}
```

我们想打印出的结果是：

2018

12

31

可是，运行后的结果打印是：

2019

0

5

穿越了吗？还是我的机器有问题？

![img](image/a164ffd0156d478cbcc7f1594b7c742b.gif)

换了别的机器依然如此，只好进源码看看：

```java
 /**
 * Allocates a <code>Date</code> object and initializes it so that
 * it represents the instant at the start of the minute specified by
 * the <code>year</code>, <code>month</code>, <code>date</code>,
 * <code>hrs</code>, and <code>min</code> arguments, in the local
 * time zone.
 *
 * @param year the year minus 1900.
 * @param month the month between 0-11.
 * @param date the day of the month between 1-31.
 * @param hrs the hours between 0-23.
 * @param min the minutes between 0-59.
 * @see java.util.Calendar
 * @deprecated As of JDK version 1.1,
 * replaced by <code>Calendar.set(year + 1900, month, date,
 * hrs, min)</code> or <code>GregorianCalendar(year + 1900,* month, date, hrs, min)</code>.
  */
 @Deprecated
 public Date(int year, int month, int date, int hrs, int min) {
 this(year, month, date, hrs, min, 0);
 }
```

**程序大揭秘：**

1. 设置年份是从 1900 开始的，即 2018-1900=118
2. 设置月份是从 0 开始的，即 0~11，12 等于下一年 119 年的第一个月即值为 0
3. day 返回的是周几   

```java
 /**
 * Returns the day of the week represented by this date. The
 * returned value (<tt>0</tt> = Sunday, <tt>1</tt> = Monday,
 * <tt>2</tt> = Tuesday, <tt>3</tt> = Wednesday, <tt>4</tt> =
 * Thursday, <tt>5</tt> = Friday, <tt>6</tt> = Saturday)
 * represents the day of the week that contains or begins with
 * the instant in time represented by this <tt>Date</tt> object,
 * as interpreted in the local time zone.
 *
 * @return the day of the week represented by this date.
 * @see java.util.Calendar
 * @deprecated As of JDK version 1.1,
 * replaced by <code>Calendar.get(Calendar.DAY_OF_WEEK)</code>.
 */
 @Deprecated
 public int getDay() {
 return normalize().getDayOfWeek() - BaseCalendar.SUNDAY;
 }
```

看到这里，你是否在想怎么改才可以得到正确的结果呢，不要着急，咱们往下看。

**Java 时间前生之 Calenar**

在 1.1 版中，Calendar 类被添加到了 Java 平台中，以矫正 Date 的缺点，由此大部分的 Date 方法就都被弃用了。遗憾的是，这么做只能使情况更糟。我们的程序说明 Date 和 Calendar API 有许多问题，我们来看一下：

```java
	public static void main(String[ ] args) {
		Calendar cal = Calendar.getInstance();
		cal.set(2018, 12, 31); // Year, Month, Day
		System.out.print(cal.get(Calendar.YEAR) + " ");
		Date d = cal.getTime();
		System.out.println(d.getDay());
		}
```

来干活吧，运行输出结果：

2019 4

![img](image/7d2f77c10d304eee91d890040989727e.jfif)

为什么会这样？进源码看看吧：

```java
 /**
 * Sets the values for the calendar fields <code>YEAR</code>,
 * <code>MONTH</code>, and <code>DAY_OF_MONTH</code>.
 * Previous values of other calendar fields are retained. If this is not desired,
 * call {@link #clear()} first.
 *
 * @param year the value used to set the <code>YEAR</code> calendar field.
 * @param month the value used to set the <code>MONTH</code> calendar field.
 * Month value is 0-based. e.g., 0 for January.
 * @param date the value used to set the <code>DAY_OF_MONTH</code> calendar field.
 * @see #set(int,int)
 * @see #set(int,int,int,int,int)
 * @see #set(int,int,int,int,int,int)
 */
 public final void set(int year, int month, int date)
 {
 set(YEAR, year);
 set(MONTH, month);
 set(DATE, date);
 }
```

从上面的理解中，月份是从 0 开始的即 0~11 代表 1月......12月

接着 date 又是从 1 开始的，为什么同一个方法设计的如此怪异？

![img](image/42376ed5f9e34863bc02c34c1ddf33ac.jfif)



**程序揭秘**

1.标准的（西历）日历只有 12 个月，该方法调用肯定应该抛出 一 IllegalArgumentException 异常，对吗？它是应该这么做，但是它并没有这么做。Calendar 类直接将其替换为下一年，即：2019

有两种方法可以订正这个问题。你可以将 cal.set 调用的第二个参数由 12 改为11，但是这么做容易引起混淆，因为数字 11 会让读者误以为是 11 月。更好的方式是使用 Calendar 专为此目的而定义的常量，即Calendar.DECEMBER

2.Date.getDay 返回的是 Date 实例所表示的星期日期，而不是月份日期。这个返回值是基于 0 的，从星期天开始计算，即：4

有两种方法可以订正这个问题。你可以调用 Date.date 这一名字极易让人混淆的方法，它返回的是月份日期。然而，与大多数  Date 方法一样，它已经被弃用了，因此你最好是将 Date 彻底抛弃，直接调用 Calendar 的 get(Calendar.DAY_OF_MONTH) 方法。

上例只是掀开了 Calendar 和 Date 缺陷的冰山一角。这些 API 简直就是雷区。Calendar 其他的严重问题包括弱类型（几乎每样事物都是一个 int）、过于复杂的状态空间、拙劣的结构、不一致的命名以及不一致的雨衣等。在使用 Calendar 和 Date 的时候一定要当心，千万要记着查阅 API 文档。

 **Java 时间后世之 Joda Time**

JDK 在 8 之前的版本，对日期时间的处理相当麻烦，有些方法设计非常反人类。而 Joda-Time 使用起来不仅方便，而且可读性强。虽然 JDK 8 引用了新的时间处理类，而且参与设计的人也正是 Joda-Time 的作者，但是由于各种原因，很多项目还是使用的 JDK7，所以使用 Joda-Time 还是一个不错的选择。

Joda-Time 提供了一组 Java 类包用于处理包括 ISO8601 标准在内的 date 和 time。可以利用它把 JDK Date 和Calendar 类完全替换掉，而且仍然能够提供很好的集成。

Joda-Time 主要的特点包括：

1. **易于使用**: Calendar 让获取"正常的"的日期变得很困难，使它没办法提供简单的方法，而 Joda-Time 能够直接进行访问域并且索引值 1 就是代表 January。

2. **易于扩展**：JDK 支持多日历系统是通过 Calendar 的子类来实现，这样就显示的非常笨重，而且事实上要实现其它日历系统是很困难的。Joda-Time 支持多日历系统是通过基于 Chronology 类的插件体系来实现。

3. **提供一组完整的功能**：它打算提供所有关系到 date-time 计算的功能．Joda-Time 当前支持 8 种日历系统，而且在将来还会继续添加，有着比 JDK Calendar 更好的整体性能等等。

Joda time示例

```java
		//JDK 
		Calendar calendar=Calendar.getInstance(); 
		calendar.set(2012, 12, 15, 18, 23,55); 
		System.out.println(calendar.getTime());
		//Joda-time 
		DateTime dateTime=new DateTime(2012, 12, 15, 18, 23,55); 
		System.out.println(dateTime.toString("yyyy-MM-dd HH:mm:ss"));
```

输出结果：

> Tue Jan 15 18:23:55 CST 2013
> 2012-12-15 18:23:55

**总结**

对 API 设计来说，其教训是：如果你不能在第一次设计时就正确使用它，那么至少应该在第二次设计时应该正确使用它，绝对不能留到第三次设计时去处理。如果你对某个 API 的首次尝试出现了严重问题，那么你的客户可能会原谅你，并且会再给你一次机会。如果你第二次尝试又有问题，你可能会永远坚持这些错误了。



