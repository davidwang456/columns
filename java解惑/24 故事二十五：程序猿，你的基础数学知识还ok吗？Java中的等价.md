**故事背景**

《宇宙追缉令》是黄毅瑜执导的动作科幻类电影，由哥伦比亚三星公司出品，戴尔里·林多、李连杰、杰森·斯坦森领衔主演。影片于 2001 年 11 月 2 日在美国上映。该片讲述了邪恶尤兰，为了成为宇宙最强的人，逐一消灭了一百二十三个宇宙其他空间的分身，并吸收他们的能量，最后剩下一个在洛杉矶当警察的正义尤兰与他展开一场生死决战的故事。

![平行世界中你还是你吗？--java中的==是否相等](image/4a0b99709d1b47789ff0a3ab95dca984.jfif)

 

在故事中，其他数个空间的分身，每杀掉一个自己，其他分身的功力就会增长。感觉有点恐怖和惊奇，分身是自己还是别人？或者自己是自己吗？值得一看的电影。

**数学世界**

在数学中，不存在模糊的概念，等号（＝）定义了一种真实的数之间的等价关系，满足自反性，传递性，对称性。

自反性：对于所有 x，x = x。也就是说，每个值与其自身存在相等关系 。

传递性：如果 x = y 并且 y = z，那么 x = z。

对称性：如果 x = y，那么 y = x。

**Java世界**

Java 中存在 == 用来表示相等的关系，那么它满足自反性，传递性和对称性吗？能否提供一段程序来演示它是否违反了任意性质？

1.自反性的例子

```java
public static void main(String[] args) {
   int i=5;
   System.out.println("x is int x = x : "+(i==5));
   float f=Float.NaN;
   System.out.println("x is float nan x=x :"+(f==Float.NaN));
   double d=Double.NaN;
   System.out.println("x is double nan x=x :"+(d==Double.NaN));        
}
```

 输出结果：

```java
x is int x = x : true
x is float nan x=x :false
x is double nan x=x :false
```

从上面的实例来看，== 不具有自反性。

2.传递性

```java
public static void main(String[] args) {
     long x = Long.MAX_VALUE;
     double y = (double) Long.MAX_VALUE;
     long z = Long.MAX_VALUE - 1;
     System.out.println((x == y) + ""); // 不精确的!
     System.out.println((y == z) + ""); // 不精确的!
     System.out.println(x == z); // 精确的!        
}
```

输出结果为：

```java
true
true
false
```

传递性有问题。

3.对称性

```java
    public static void main(String[] args) {
        int i=5,j=5;    
        System.out.println("x y is int x = y : "+(i==j));
        
        float f=0.53f,f1=0.53f;        
        System.out.println("x y is float x = y : "+(f==f1));
        
        double d=0.3836,d1=0.3836;        
        System.out.println("x y is double x = y : "+(d==d1));    
    }
```

输出结果为：

```java
x y is int x = y : truex y is float x = y : truex y is double x = y : true
```

**总结**

总之，Java中的 == 使用时要警惕到 float 和 double 类型的拓宽原始类型转换所造成的损失。它们是悄无声息的，但却是致命的。它们会违反你的直觉，并且可以造成非常微妙的错误。

参考资料：

【1】https://baike.baidu.com/tashuo/browse/content?id=7a442b409e380dc8e1b3fb5f&fr=qingtian&lemmaId=69962

【2】《Java解惑》

【3】https://baike.baidu.com/item/%E5%AE%87%E5%AE%99%E8%BF%BD%E7%BC%89%E4%BB%A4/6174641?fr=aladdin

