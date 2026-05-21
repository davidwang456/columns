本故事纯属虚构，如有雷同，纯属巧合。

**故事背景**

随着工作经历的增长，小白开始慢慢的开始有计划的看源代码，包括公司的代码和开源框架的源码，有时候读着读着就迷糊了，比如下面的语句会编译报错或者打印什么？

```java
 System.out.print("baidu site :");
 https://www.baidu.com;
 System.out.println(" format");
```

刚开始时，小白会想：会编译出错，中间那行是什么鬼？

![img](image/882b402c32654cbfb5da3cb525d4f7ee.jfif)

但最后小白测试时，并不会报错，而是会打印出：

```
baidu site : format
```

小白尝试改成这样的语句，是不是就不会觉得编译报错了？

```java
 System.out.print("baidu site :");
 https :
 //www.baidu.com;
 System.out.println(" format");
```

有点像 switch 语句中的 case：

```java
int q = (n+7)/8;
switch (n%8) {
 case 0: do { foo(); // Great C hack, Tom,
 case 7: foo(); // but it's not valid here.
 case 6: foo();
 case 5: foo();
 case 4: foo();
 case 3: foo();
 case 2: foo();
 case 1: foo();
 } while (--q > 0);
}
```

原来上面的语句，":" 是 statement label 翻译成标号语句。

其语法如下：

```java
LabeledStatement: Identifier : StatementLabeledStatementNoShortIf: Identifier : StatementNoShortIf
```

与 c 和 c++ 不同，Java 中没有 goto 语句；标号语句用于出现在标号语句内任何地方的 break 或者 continue 语句之上。

再来一个标句语句作为结尾的练习吧：                

```java
class Test {
 	char[] value;
 	int offset, count;
 	int indexOf(TestString str, int fromIndex) {
 	char[] v1 = value, v2 = str.value;
 	int max = offset + (count - str.count);
 	int start = offset + ((fromIndex < 0) ? 0 : fromIndex);
 	i:
 	for (int i = start; i <= max; i++) {
 		int n = str.count, j = i, k = str.offset;
 		while (n-- != 0) {
 		if (v1[j++] != v2[k++])
 			continue i;
 	} 
 	return i - offset;
 }
 return -1;
 }
}
```

**总结**

说起 Java 的标签，很容易联想到 Java 的循环语句。通常，在 Java 编程中，用到标签的地方大多是在循环语句之前。在标签和循环之前最好不要加入其它的语句。最后，不要为了使用标句语句而使用标句语句。

参考资料

【1】https://docs.oracle.com/javase/specs/jls/se12/html/jls-14.html#jls-14.7