**故事背景**

《西游记》第五十七回：唐僧因悟空又打死拦路强盗，再次把他撵走。六耳猕猴精趁机变作悟空模样，抢走行李关文，又把小妖变作唐僧、八戒、沙僧模样，欲上西天骗取真经。真假二悟空从天上杀到地下，菩萨、玉帝、地藏王等均不能辨认真假，直到雷音寺如来佛处，才被佛祖说出本相，猕猴精被悟空打死。

![img](image/9e4762365d71493daece23129858a6ee.jfif)

 **Java之真假美猴王**

Java 中有时候也会出现真假美猴王的事件，请看下面的程序后打印什么？

```java
public class Pet {
    public final String name;
    public final String food;
    public final String sound;
    public Pet(String name, String food, String sound) {
        this.name = name;
        this.food = food;
        this.sound = sound;
    }
    public void eat() {
        System.out.println(name + ": Mmmmm, " + food);
    }
    public void play() {
        System.out.println(name + ": " + sound + " " + sound);
    }
    public void sleep() {
        System.out.println(name + ": Zzzzzzz...");
    }
    public void live() {
        new Thread() {
            public void run() {
                while (true) {
                    eat();
                    play();
                    sleep();
                }
            }
        }.start();
    }
    public static void main(String[] args) {
        new Pet("Fido", "beef", "Woof").live();
    }
}
```

我们期望程序打印：

```java
Fido: Mmmmm, beef

Fido: Woof Woof

Fido: Zzzzzzz…
```

实际上报编译错误。

**The method sleep(long) in the type Thread is not applicable for the arguments ()**

查看 Thread 的 sleep 方法：

```java
 /**
 * Causes the currently executing thread to sleep (temporarily cease
 * execution) for the specified number of milliseconds, subject to
 * the precision and accuracy of system timers and schedulers. The thread
 * does not lose ownership of any monitors.
 *
 * @param millis
 * the length of time to sleep in milliseconds
 *
 * @throws IllegalArgumentException
 * if the value of {@code millis} is negative
 *
 * @throws InterruptedException
 * if any thread has interrupted the current thread. The
 * <i>interrupted status</i> of the current thread is
 * cleared when this exception is thrown.
 */
 public static native void sleep(long millis) throws InterruptedException;
 /**
 * Causes the currently executing thread to sleep (temporarily cease
 * execution) for the specified number of milliseconds plus the specified
 * number of nanoseconds, subject to the precision and accuracy of system
 * timers and schedulers. The thread does not lose ownership of any
 * monitors.
 *
 * @param millis
 * the length of time to sleep in milliseconds
 *
 * @param nanos
 * {@code 0-999999} additional nanoseconds to sleep
 *
 * @throws IllegalArgumentException
 * if the value of {@code millis} is negative, or the value of
 * {@code nanos} is not in the range {@code 0-999999}
 *
 * @throws InterruptedException
 * if any thread has interrupted the current thread. The
 * <i>interrupted status</i> of the current thread is
 * cleared when this exception is thrown.
 */
 public static void sleep(long millis, int nanos)
 throws InterruptedException {
 if (millis < 0) {
 throw new IllegalArgumentException("timeout value is negative");
 }
 if (nanos < 0 || nanos > 999999) {
 throw new IllegalArgumentException(
 "nanosecond timeout value out of range");
 }
 if (nanos >= 500000 || (nanos != 0 && millis == 0)) {
 millis++;
 }
 sleep(millis);
 }
```

等等！

![img](image/29a6593ec4cf4320b2f891e706b67eb5.jfif)

 

我不是要调用 Thread 的 sleep 方法，而是要调用 Pet 的 sleep 方法。为什么出现这种情况呢？

JSL-6.4 定义了这种情况：

> It is a compile-time error if the name of a formal parameter is used to declare a new variable within the body of the method, constructor, or lambda expression, unless the new variable is declared within a class declaration contained by the method, constructor, or lambda expression.
>
> 注：如果形参的名字用在方法体、构造器体或者 lambda 表示式体内声明的新变量，将会抛出编译时错误，除非该新变量是在方法体、构造器体或者 lambda 表示式所包含的类声明内声明的。
>
> It is a compile-time error if the name of a local variable v is used to declare a new variable within the scope of v, unless the new variable is declared within a class whose declaration is within the scope of v.
>
> 注：如果局部变量v的名字用来在v的作用域内声明新的变量，将会抛出编译时错误，除非该新变量是其类声明在v作用域的类内声明的。
>
> It is a compile-time error if the name of an exception parameter is used to declare a new variable within the Block of the catch clause, unless the new variable is declared within a class declaration contained by the Block of the catch clause.
>
> 注：如果表达式参数的名字用在catch子句的语句块内声明的新变量，将会抛出编译时错误，除非该新变量是在catch子句的语句块所包含的类声明内声明的。
>
> It is a compile-time error if the name of a local class C is used to declare a new local class within the scope of C, unless the new local class is declared within another class whose declaration is within the scope of C.
>
> 注：如果局部类c的名字用来在c的作用域内声明新的局部类，将会抛出编译时错误，除非该新局部类是在其类声明在c的作用域内的另一个类内声明的。

Java 中有 Shadowing （遮蔽)）的描述，其中：

> Shadowing：Some declarations may be shadowed in part of their scope by another declaration of the same name, in which case a simple name cannot be used to refer to the declared entity.

简单的意思是：在作用域内，一个地方的声明可能被另一个同名的声明所遮蔽。在这种情况下不能简单的使用名字来引用他们所声明的实体。

变量 Shadowing 举例：

```java
class Test1 {
 public static void main(String[] args) {
 int i;
 for (int i = 0; i < 10; i++)
 	System.out.println(i);
 }
}
```

编译报错，但编译检测也不是万能的，也有一些 trick 来逃避：

```java
class Test2 {
 public static void main(String[] args) {
 	int i;
 	class Local {
 		{
 		for (int i = 0; i < 10; i++)
 		System.out.println(i);
 		}
    }
 	new Local();
 }
}
```

如果在不同 block，则不会出现 Shadowing 的问题：

```java
class Test3 {
 public static void main(String[] args) {
 	for (int i = 0; i < 10; i++)
 		System.out.print(i + " ");
 	for (int i = 10; i > 0; i--)
 		System.out.print(i + " ");
 	System.out.println();
 }
}
```

原因找到了，那该怎么解决呢？

**问题解决**

方式一：线程内调用，改成 Pet.this.sleep(); 限定具体的方法：

```java
    public void live() {
        new Thread() {
            public void run() {
                while (true) {
                    eat();
                    play();
                    Pet.this.sleep();
                }
            }
        }.start();
    }
```

方式二：将 sleep 名称改为其它不冲突的名称，如 petSleep，然后线程内调用该方法：

```java
    public void petSleep() {
        System.out.println(name + ": Zzzzzzz...");
    }
```

 方式三：也是最好的方式，使用 Thread(Runnable) 构造器来替代对 Thread 的继承。那个匿名类不会再继承Thread.sleep 方法，故也不会有冲突了。

```java
public void live(){
	new Thread(new Runnable(){
		public void run(){
		while(true){
			eat();
			play();
			sleep();
		}
		}
	}).start();
}
```

参考资料

【1】https://docs.oracle.com/javase/specs/jls/se12/html/jls-6.html#jls-6.4

【2】《Java解惑》