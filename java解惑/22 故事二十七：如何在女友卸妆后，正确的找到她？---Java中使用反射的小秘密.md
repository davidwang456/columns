**故事背景**

小白是个程序猿，刚毕业两年，最近交了一个女朋友，是同事介绍的。女朋友和闺蜜住在一起。小白早上很早接到女朋友电话，昨天她的一个文件错放到了他的电脑包，希望他帮忙送到她住的地方，她今天要向她 boss 汇报的。救急如救火，为了好好表现自己，小白赶紧打了个车到女朋友的小区，然后在小区门口等她。早上7点，人流如织，等了许久，没有见到，遂电话之，被女友的闺蜜告知，女友未化妆素颜下来找他，未带电话，请他好好辨别。

![img](image/3a8c0c3c0dc74a8c8e39371aeff1401a.gif)

小白听说有一句话特别流行，说现在女生有着三分的长相，画着五分的妆容，看着美颜中七分的自己。小伙伴们都说了女生化妆到底有多厉害？

![img](image/abfd31d8e51846efba093884f715d4ec.jfif)

 	这个怎么办呢？小白灵机一动，敌不动我不动,敌一动,我以静制动。遂带上眼镜，举着文件，站在一个显眼的位置，自己观察走来的女生，天见可怜，最终将文件交到一个看着有点熟悉又有点陌生的女孩手里，完成任务。

**Java反射的故事**

工作中，小白也碰到同样的一个问题，他希望下面的程序打印 true：

```java
    public static void main(String[] args) throws Exception {
        Set<String> s = new HashSet<String>();
        s.add("foo");
        Iterator<String> it = s.iterator();
        Method m = it.getClass().getMethod("hasNext");
        System.out.println(m.invoke(it));
    }
```

运行时，报错如下：

```java
Exception in thread "main" java.lang.IllegalAccessException: Class com.javapuzzle.davidwang456.ReflectorTest can not access a member of class java.util.HashMap$HashIterator with modifiers "public final"
    at sun.reflect.Reflection.ensureMemberAccess(Reflection.java:102)
    at java.lang.reflect.AccessibleObject.slowCheckMemberAccess(AccessibleObject.java:296)
    at java.lang.reflect.AccessibleObject.checkAccess(AccessibleObject.java:288)
    at java.lang.reflect.Method.invoke(Method.java:491)
    at com.javapuzzle.davidwang456.ReflectorTest.main(ReflectorTest.java:15)
```

hasNext 方法当然是公共的，所以它在任何地方都是可以被访问的。那么为什么这个基于反射的方法调用是非法的呢？我们看一下 JSL 定义的规范【https://docs.oracle.com/javase/specs/jls/se12/html/jls-6.html#jls-6.6.1】

> If a top level class or interface type is declared public and is a member of a package that is exported by a module, then the type may be accessed by any code in the same module, and by any code in another module to which the package is exported, provided that the compilation unit in which the type is declared is visible to that other module (§7.3).
> If a top level class or interface type is declared public and is a member of a package that is not exported by a module, then the type may be accessed by any code in the same module.
> If a top level class or interface type is declared with package access, then it may be accessed only from within the package in which it is declared.
> A top level class or interface type declared without an access modifier implicitly has package access.
> A member (class, interface, field, or method) of a reference type, or a constructor of a class type, is accessible only if the type is accessible and the member or constructor is declared to permit access:
> If the member or constructor is declared public, then access is permitted.
> All members of interfaces lacking access modifiers are implicitly public.
> Otherwise, if the member or constructor is declared protected, then access is permitted only when one of the following is true:
> Access to the member or constructor occurs from within the package containing the class in which the protected member or constructor is declared.
> Access is correct as described in §6.6.2.
> Otherwise, if the member or constructor is declared with package access, then access is permitted only when the access occurs from within the package in which the type is declared.
> A class member or constructor declared without an access modifier implicitly has package access.
> Otherwise, the member or constructor is declared private, and access is permitted if and only if it occurs within the body of the top level type (§7.6) that encloses the declaration of the member or constructor.
> An array type is accessible if and only if its element type is accessible.

其中一条，如果类或接口在声明时没任何访问权限修饰符，那么它就**隐式地被赋予了包访问权限控制**。 我们看看调用情况：

1.HashSet 默认调用 HashMap 生成方式：

```java
 /**
 * Constructs a new, empty set; the backing <tt>HashMap</tt> instance has
 * default initial capacity (16) and load factor (0.75).
 */
 public HashSet() {
 map = new HashMap<>();
 }
```

2.调用 HashMap.KeyIterator 类

```java
 final class KeyIterator extends HashIterator
 implements Iterator<K> {
 public final K next() { return nextNode().key; }
 }
```

 hasNext() 方法，调用父类 HashMap.HashIterator 的 hasNext() 方法：

```java
 abstract class HashIterator {
 	Node<K,V> next; // next entry to return要返回的下一个节点
 	Node<K,V> current; // current entry 当前节点
	 int expectedModCount; // for fast-fail 支持fast-fail
 	int index; // current slot  当前索引
 	HashIterator() {
    	expectedModCount = modCount;
 		Node<K,V>[] t = table;
 		current = next = null;
 		index = 0;
	 	if (t != null && size > 0) { // advance to first entry第一个节点优先
 			do {} while (index < t.length && (next = t[index++]) == null);
	 	}
   	 }
 	public final boolean hasNext() {
 		return next != null;
 	}
........
}
```

我们看到 HashIterator 是 HashMap 的子类，并没有授予 public 权限，那么默认情况下的访问权限是：包访问权限，即它可以被包内的类调用。

这里的问题，并不在于该方法的访问级别（access level），而在于该方法所在的类型的访问级别。这个类型所扮演的角色和一个普通方法调用中的限定类型（qualifying type）是相同的[JLS 13.1]。在这个程序中，该方法是从某个类中选择出来的，而这个类型是由从 it.getClass 方法返回的 Class 对象表示的。这是迭代器的动态类型（dynamic type），它恰好是私有的嵌套类（nested class）java.util.HashMap.KeyIterator。出现 IllegalAccessException 异常的原因就是这个类不是公共的，它来自另外一个包：访问位于其他包中的非公共类型的成员是不合法的 [JLS 6.6.1]。无论是一般的访问还是通过反射的访问，上述的禁律都是有效的。

**问题解决思路**

在使用反射访问某个类型时，请使用表示某种可访问类型的 Class 对象。hasNext 方法是声明在一个公共类型 java.util.Iterator 中的，所以它的类对象应该被用来进行反射访问。经过这样的修改后，这个程序就会打印出 true。

```java
public static void main(String[] args) throws Exception {
     Set<String> s = new HashSet<String>();
     s.add("foo");
     Iterator<String> it = s.iterator();
     Method m = Iterator.class.getMethod("hasNext");
     System.out.println(m.invoke(it));
}
```

**经验教训**

总之，访问其他包中的非公共类型的成员是不合法的，即使这个成员同时也被声明为某个公共类型的公共成员也是如此。不论这个成员是否是通过反射被访问的，上述规则都是成立的。

**参考资料**：

【1】https://new.qq.com/omn/20190527/20190527A07COH.html?pc

【2】https://docs.oracle.com/javase/specs/jls/se12/html/jls-6.html#jls-6.6.1

【3】《Java解惑》

