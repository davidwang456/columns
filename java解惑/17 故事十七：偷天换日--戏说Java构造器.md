本故事纯属虚构，如有雷同，纯属巧合。

**故事背景**

小白最近刚刚看了一部经典老片《偷天换日》，影片是根据 1969 年英国同名电影翻拍，讲述了一群人抢劫黄金的故事。影片于 2003 年 5 月 30 日在美国上映。仅北美票房收入就超过 1 亿 7600 万美元。

![img](image/2fca7123578445d48b4fda418fa28091.jfif)



《偷天换日》拥有非常好的节奏和剪辑，带给观众的娱乐感受很震撼，类似爱德华·诺顿的突然背叛、小汽车运金条这样的设计，让小白记忆深刻，特别是细节的设计。

**Java之偷天换日**

我们假定正确的构造方法在 Java 中为太子，假的构造方法为狸猫，那我们看一下下面的程序会打印出什么结果吧？

```java
public class ConstructorTest {
	static {
 		System.out.println("who is prince? ");
 	} 
 	public void ConstructorTest() { 
 		System.out.println("i am prince!");
 	} 
 	public void getName() {
 		System.out.println("i am not prince");
 	}
 	public static void main(String[] args) {
 		ConstructorTest test=new ConstructorTest();
		test.getName();
 	}
}
```

我们本来想打印出太子：

who is prince?

i am prince

真实运行打印出的却是狸猫：

who is prince?

i am not prince

那到底是什么一回事呢？

![img](image/d20e857c77b7457188410390deb9cab3.jfif)

原来是皇帝受奸臣 void 蒙蔽了，将真假太子弄错了。

太监 void 后面的不是太子(构造器)，而是普通方法，在 main 方法中并没调用该方法，而由于没有任何声明的构造器，所以编译器会帮助（真的是在帮忙吗？）生成一个公共的无参数构造器，它除了初始化它所创建的域实例之外，不做任何事情。

**Java之太子之争**

因构造方法可以有多个，就会产生太子之争，那么怎么识别谁才是真正的太子呢？

```java
public class Confusing {
 private Confusing(Object o) {
 System.out.println("Object");
 }
 private Confusing(double[] dArray) {
 System.out.println("double array");
 }
 public static void main(String[] args) {
 new Confusing(null);
 }
}
```

上面的题目给你了两个容易令人混淆的构造器。main 方法调用了一个构造器，但是它调用的到底是哪一个呢？该程序的输出取决于这个问题的答案。那么它到底会打印出什么呢？甚至它是否是合法的呢？

Java 的方法(包括构造器方法)触发过程是以两阶段运行的：第一阶段，选取所有可获得并且可应用的方法或构造器。第二阶段，在第一阶段选取的方法或构造器中选取最精确的一个。如果一个方法或构造器可以接受传递给另一个方法或构造器的任何参数，**那么我们就说第一个方法比第二个方法缺乏精确性 [JLS 15.12.2.5]**

**在我们的程序中，两个构造器都是可获得并且可应用的。**

构造器 Confusing(Object) 可以接受任何传递给 Confusing(double[ ]) 的参数，因此 Confusing(Object) 相对缺乏精确性。（每一个 double 数组都是一个 Object，但是每一个 Object 并不一定是一个 double 数组。）因此，最精确的构造器就是：

Confusing(double[ ])。

故结果是：double array