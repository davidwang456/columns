本故事纯属虚构，如有雷同，纯属巧合。

**背景故事**

影片《金刚》是2005年上映的一部冒险电影，它讲述 1933 年的美国，一名勇于冒险的企业家及电影制作者，率领摄制队伍到荒岛拍摄，其中包括女主角安及编剧杰克，他们遇到恐龙及当地土著的袭击，安发出的尖叫声换来金刚的回应。这只巨大无比的猩猩，连凶悍的恐龙也惧怕它几分，偏偏它却钟情于安。安其后将金刚由荒岛带回纽约，但却是它悲剧命运的开始。后来金刚被抓到了城市。为保护爱人同军队战斗，金刚为了带安再看一次她曾说过美丽的日出，爬上了帝国大厦，使自己陷入困境，与人类的飞机展开了最后决战。最后它摔下了帝国大厦，为自己的爱人谱写了最后的悲歌。

![img](image/b9f553d63ef347fca01471af3541e1ec.jfif)

**try-catch 之一矮矬穷泡妞**

Java 程序猿也会碰到难以琢磨的 try-catch，请看下面的例子：

```java
 public static void main(String[] args) {
 	try {
 		System.out.println("Hello world");
 	} catch(IOException e) {
 		System.out.println("抓到一个 IO 异常!");
 	}
 }
```

这段代码可以打印出什么？

可能不少人会说，这个不是很简单嘛？打印出：

Hello world

其实它压根编译都不能通过！

![img](image/5ca22ccc85a342d58e777efb1600341f.jfif)

报错情况

> Unreachable catch block for IOException. This exception is never thrown from the try statement body

为什么呢？简单的说，就是 try 里没有能抛出 IOException 异常的语句，catch 该异常就通不过编译。

JSL-11.2.3 里规定了：

```java
It is a compile-time error if a method or constructor body can throw some exception class E when E is a checked exception class and E is not a subclass of some class declared in the throws clause of the method or constructor.
It is a compile-time error if a lambda body can throw some exception class E when E is a checked exception class and E is not a subclass of some class declared in the throws clause of the function type targeted by the lambda expression.
It is a compile-time error if a class variable initializer (§8.3.2) or static initializer (§8.7) of a named class or interface can throw a checked exception class.
It is a compile-time error if an instance variable initializer (§8.3.2) or instance initializer (§8.6) of a named class can throw a checked exception class, 
unless the named class has at least one explicitly declared constructor and the exception class or one of its superclasses is explicitly declared in the throws clause of each constructor.
It is a compile-time error if a catch clause can catch checked exception class E1 and it is not the case that the try block corresponding to the catch clause can throw a checked exception class that is a subclass or superclass of E1, 
unless E1 is Exception or a superclass of Exception.
It is a compile-time error if a catch clause can catch an exception class E1 and a preceding catch clause of the immediately enclosing try statement can catch E1 or a superclass of E1.
```

根据上面所述，矮矬穷泡妞本身都被排除掉了，只有有一项特长，才能泡妞！

最简单的方法是抛出一个异常或者子异常：

```java
import java.io.IOException;
public class TryCatchException {
 public static void main(String[] args) {
 try {
 	System.out.println("Hello world");
 	throw new IOException();//或者子异常，如throw new FileNotFoundException();
 } catch(IOException e) {
 	System.out.println("抓到一个IO 异常!");
 }
 }
}
```

**try-catch 之二高富帅泡妞**

那来看看这个吧！打印什么？

```java
public class TryCatchException {
 public static void main(String[] args) {
 	try {
 		System.out.println("hello world!");
 	} catch(Exception e) {
 		System.out.println("捕获到异常");
 	}
 }
}
```

可能不少人会说，不是和上面的一样嘛！会报编译异常。

![img](image/6e57269ac3f54009ad2ad9fb927d8487.jfif)

哈哈，你掉到坑里了！它打印

```java
hello world!
```

不管与其相对应的 try 子句的内容是什么，捕获 Exception 或者 Throwable 的 catch 语句是 ok 的，这点 JSL 并没有说清楚。

总之，高富帅泡妞总是很超脱的，很多妞也愿意倒扑！

**try-catch 之三泡妞技可以继承吗？**

我们来看看异常如何继承的吧：

```java
public interface FileNotFound {
	void run() throws FileNotFoundException;
}
public interface CloneNotSupported {
	void run() throws CloneNotSupportedException;
}
public class TryCatchException implements FileNotFound,CloneNotSupported {
	public static void main(String[] args) {
 		TryCatchException e=new TryCatchException();
 		e.run();
 }
 @Override
 public void run() {
 	System.out.println("Hello world"); 
 }
}
```

上面的程序可以编译通过吗？不少人会说，不能通过编译！原因：TryCatchException 继承了 FileNotFound 和 CloneNotSupported 的方法，同时必然继承它的异常，故必须捕获这两个异常。

![img](image/a1d2d31a563d4baf94584460460e40c1.jfif)

你再次避过了正确答案！可以正确编译，且打印出结果。

**一个方法可以抛出的受检查异常的集合时，它所适用所有类型声明要抛出的受检查异常的交集，而不是合集。**

小结：

矮矬穷就别想着捕获少女心了，想着一项特长吧，比如富！

高富帅可以无所顾忌，无往不利。

泡妞只能最新化的继承，不用太担心。

参考资料

【1】Java解惑