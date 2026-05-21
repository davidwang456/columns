本故事纯属虚构，如有雷同，纯属巧合。

**故事背景**

原来11月11日是“光棍节”。现在11月11日搞成了购物节。11月11日快到了，你是过双十一，还是过？

![img](image/19f47cc96952487c8effa7defbd298d4.jfif)

还有更悲催的是单身穷狗！！！！！！！！！！

![img](image/10f05c6f76054b19990a448cf0d52de2.jfif)

小白也被虐狗了，连程序来也来欺负他。我们来看看吧：

**Java 的 equal 和 hashcode 是相亲相爱的一对**

请看一下这单身狗的待遇吧，男单身狗的待遇。

```java
public class EqualTest {
	private String Odd, even;
	
	public EqualTest(String Odd, String even) {
		this.Odd = Odd;
		this.even = even;
	}
	
	public boolean equals(Object o) {
		if (!(o instanceof EqualTest))
			return false;
		EqualTest n = (EqualTest)o;
		return n.Odd.equals(Odd) && n.even.equals(even);
	}
	
	/*
	public int hashCode() {
		return 32 * Odd.hashCode() + even.hashCode();
		}
	*/	
    public static void main(String[] args) {
        Set<EqualTest> s = new HashSet<>();
		s.add(new EqualTest("sigle", "couple"));
		System.out.println(s.contains(new EqualTest("sigle", "couple")));
	}
}
```

输出结果：

```java
false
```

女单身狗待遇

```java
public class EqualTest {
	private String Odd, even;
	
	public EqualTest(String Odd, String even) {
		this.Odd = Odd;
		this.even = even;
	}
	/*
	public boolean equals(Object o) {
	if (!(o instanceof EqualTest))
	return false;
	EqualTest n = (EqualTest)o;
	return n.Odd.equals(Odd) && n.even.equals(even);
	}
	*/
	
	public int hashCode() {
		return 32 * Odd.hashCode() + even.hashCode();
	}		
		
	public static void main(String[] args) {
		Set<EqualTest> s = new HashSet<>();
		s.add(new EqualTest("sigle", "couple"));
		System.out.println(s.contains(new EqualTest("sigle", "couple")));
	}
}
```

输出结果

```java
false
```

虐狗一对出现

```java
public class EqualTest {
    
	private String Odd, even;
	public EqualTest(String Odd, String even) {
		this.Odd = Odd;
		this.even = even;

}

public boolean equals(Object o) {

	if (!(o instanceof EqualTest))
		return false;

	EqualTest n = (EqualTest)o;
	return n.Odd.equals(Odd) && n.even.equals(even);

}

public int hashCode() {
	return 32 * Odd.hashCode() + even.hashCode();
}

public static void main(String[] args) {
    
	Set<EqualTest> s = new HashSet<>();
	s.add(new EqualTest("sigle", "couple"));
	System.out.println(s.contains(new EqualTest("sigle", "couple")));

}

}
```

输出结果：

```java
true
```

**追根究底**

hashcod约定

> When overriding equals() in a class, the hashCode() method should be overrided as well such that it maintains its contract with equals().

简单说：hashCode 约定要求相等的对象要具有相同的散列码。为了遵守这项约定，无论何时，只要你覆写了equals 方法，你就必须同时覆写 hashCode 方法。