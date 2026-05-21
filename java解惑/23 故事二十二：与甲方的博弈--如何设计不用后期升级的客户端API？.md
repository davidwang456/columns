本故事纯属虚构，如有雷同，纯属巧合。

**故事背景**

自古以来，做买卖、特别是供大于求情况下，市场游戏总会出现乙方有求于甲方的现象。 在现在的市场经济机制下，甲方和乙方的地位更难平等，小白是深有体会。小白他们公司和一家国企有业务往来，为该国企提供软件服务。

最近小王比较烦，因甲方大爷的需求变更，提供给国企软件中引用的一个 jar 中的常量发生了变化，他们更新了 jar 包，但甲方将新 jar 包替换掉旧的 jar 包，现在系统出现执行异常！该国企限令必须尽快找到问题并解决掉！

为了防止信息泄露，我们模拟一下这个场景:

```java
public class BinaryCompatibilityTest {
 public static void main(String[] args) {
 	System.out.println(DefineConstants.FIRST + " " +
 	DefineConstants.SECOND + " " +
 	DefineConstants.THIRD);
 }
}
```

其中 DefineConstants 来自甲方对乙方的引用：

```java
import com.test.constants.Words;
public class DefineConstants {
	private DefineConstants() { }; // Uninstantiable
 	public static final String FIRST = Words.FIRST;
 	public static final String SECOND = Words.SECOND;
 	public static final String THIRD = Words.THIRD;
}
```

其中，Words 是引用的公用 jar 包。

![img](image/b4667c15d79b4dc2a55591c9af2ce1ab.jfif)

类实现如下：

```java
package com.test.constants;
public class Words {
 private Words() {
 }; // Uninstantiable
 public static final String FIRST = "the";
 public static final String SECOND = null;
 public static final String THIRD = "set";
}
```

原先打印结果为

```java
the null set
```

现在乙方小王修改了jar包后，代码变成了 package com.test.constants;

```java
public class Words {
 private Words() {
 }; // Uninstantiable
 public static final String FIRST = "physics";
 public static final String SECOND = "chemistry";
 public static final String THIRD = "biology";
 }
```

他将重新打包后的 jar 包传给甲方，让甲方在 tomcat 上替换原来的 jar 包，结果运行后打印的结果却为：

```java
the chemistry set
```

小白百思不得其解。

![img](image/b6358ec822e8463a9b4cd14e0a9f7125.jfif)

反复确认了 jar 包是否正确，都是最新的 jar 包。

项目经理万般无奈之下，只好请出半退隐的技术大神扫地僧，并答应扫地僧 1w/d 的辛苦费。

![img](image/2aae0a438c784724837185ea9f104e36.jfif)

老司机了解了情况后，很快就找到了原因，通过 jd-gui 反编译了代码给小白看：

![img](image/2f941e46384343bdb832f912f0da3aaa.jfif)

替换了 jar 包后，DefineConstants 并没有被重新编译，导致 FIRST和THIRD 的结果没有发生改变，但因 SECOND 本身为 null，在编译期常量表达式(compile-time constant expression)[JLS15.28]的精确定义中找到。它的定义太长了，就不在这里写出来了，但是理解这个程序的行为的关键是 null 不是一个编译期常量表达式。运行时就会执行新的结果：chemistry

**解决办法是**

1.需要重新编译 DefineConstants 后，替换到新的 class

2.重新编译整个项目的打包文件，提供新的包文件替换旧的打包文件

**第一个方案**

优点： 线上改动小，影响小，速度快

缺点：只能解决当前问题，如果项目中还有别的地方引用这个变量，将还会出错。

**第二个方案**

优点：从根本上解决问题

缺点：线上影响稍微大一些。

小白可是个勤奋好学的家伙，项目搞定后请扫地僧吃饭喝酒，趁扫地僧酒醉，趁机问解决这个问题的诀窍，扫地僧喝迷糊后道出了本质：

原来 Java 考虑到升级的问题，有二进制兼容性规范。。。。。。。。。

因扫地僧喝的有点多，描述的不是很清楚，小王只记住了在 JSL 规范了有明确的描述。