本故事纯属虚构，如有雷同，纯属巧合！

**故事背景**

小白刚进入公司做 Java 开发的时候，其实自学过 1 年，在学校里练习过几个项目，得到过老师的好评，常常以老手自居。小白对分配给自己的任务，总是快速的从网上找类似的代码，然后修修改改就提交上去了，导致他的导师小 L 让他返工了很多次，他心理很不爽。觉得小 L 是小题大做，程序猿不就是 ctr+c 加 ctr+v 吗？

**踩坑经历**

小 L 已经工作 5 年了，也是过来人了，知道小白不爽，得找个方法让他意识到自己的问题，恰巧一个外包项目出问题了，小 L 了解情况后，就安排小白去排查。测试反映，客户每次下单扣款后金额都对不上，为了不泄露公司密码，将程序简化一下：

```java
public static void main(String[] args) {		
	double total=2.00f;
	double cost=1.10f;
	System.out.println(total-cost);
}
```

小白测试了好多次，每次的结果都和预期不一致，就有点懵逼了。

![img](image/4c3d3252b6344424a4fabd6f409b854d.jfif)

结果是：

```
0.8999999761581421
```

涉及到金额，不正确可不行！

**寻找门路**

碰到问题，网上找。小白赶紧找人问，网上千篇一律的都告诉他，交易计算时使用 BigDecimal 不要使用 double，赶紧解决问题吧。

```java
public static void main(String[] args) {
		BigDecimal total=new BigDecimal(2.00);
		BigDecimal cost=new BigDecimal(1.10);
		System.out.println(total.subtract(cost));
}
```

运行结果：

0.899999999999999911182158029987476766109466552734375

这网上也太不靠谱了吧？

![img](image/00da96aed76a49f7badf76a72e2ed5c9.jfif)

**求助Leader**

折腾了半天，问题并没有解决，小白慌了，这种 bug 的工作量一般是一天，如果再不解决，测试 MM 就要催死了，于是向小 L 求救，小 L 只看了小白的代码一眼，敲了一下键盘，小白眼一花，结果就正确了。

```java
public static void main(String[] args) {
		BigDecimal total=new BigDecimal("2.00");
		BigDecimal cost=new BigDecimal("1.10");
		System.out.println(total.subtract(cost));
	}
```

输出结果：

0.90

![img](image/c4851d2857b74ba580de6b42727a0f5c.jfif)

小白看傻了！

**小白**：“等等！我们的程序不是一样的吗？为什么你的可以得出正确的结果，而我的是错误的？”

**小L**：“

```java
BigDecimal total=new BigDecimal(2.00);
BigDecimal cost=new BigDecimal(1.10);
```

是将 double 值传入 BigDecimal 实例里，double 本身会损失精度，故结果也会损失精度；而使用

```java
BigDecimal total=new BigDecimal("2.00");
BigDecimal cost=new BigDecimal("1.10");
```

BigDecimal 实例 total 和 cost 会解析字符串的值，不会损失精度。详细实现你可以看 BigDecimal 的源码”

**小白**：“好的，那我就提交代码，让测试 MM 测试了。”

**小L**：“且慢，这段程序会出现什么问题？有没有更好的办法？”

**小白**：“网上大家都是这么用的，应该没有问题。”

**小L**：“在我们公司的业务中，下单扣款接口比较重要，调用量也高，使用 BigDecimal 会出现什么问题吗？”

**小白**：“会影响计算性能，导致 cpu 升高，进而影响接口的性能。”

**小L**：“你的计算机功底不错，那我们该如何解决呢？”

**小白**：“能否给我一点提示呢？”

**小L**：“在现实生活中，我们的金额计算是不是最小为分，没有更小的货币了，可以从这方面考虑！”

**小白**：“我懂了，在我们的业务中可以使用 int 结算，毕竟总额和消费金额及余额最小单位为分，可以使用 int 计算出。”

**小L**：“总之， 在需要精确答案的地方，要避免使用 float 和 double；对于货币计算，要使用 int、long 或 BigDecimal，同时也要考虑业务的本身的使用场景，选择合适的数据类型。”

**问题解决**

向小L请教之后，小白的思路清晰起来，迅速修改程序，提交并打包交给测试 M 进行测试，最后圆满通过所有用例。

```java
	public static void main(String[] args) {		
		int total=200;
		int cost=110;
		System.out.println((200-110)+" cents");
	}
```

通过这件事情之后，小白端正了自己的态度，学习了以下几点：

1.Java基础不够扎实，需要进一步加强；

2.不同的业务场景，程序可能不一样，需要因地制宜。