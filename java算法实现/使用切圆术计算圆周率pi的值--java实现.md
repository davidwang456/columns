# 背景

大约在公元265年，魏国的数学家刘徽创立了割圆术，用3,072边的正多边形计算出π的数值为**3.1416**。今天我们可以利用计算机复现这一过程。

# 切圆术的java实现

先演示正六边形的切圆术

![](https://p3-sign.toutiaoimg.com/tos-cn-i-pyjprfzmqi/c0c066676e3e45d8b319b06e2dcadc80~tplv-tt-origin.webp?_iz=30575&lk3s=eb9271ed&x-expires=1775123580&x-signature=sfg50eJhwWPOXbSUiRZsMGQFYUY%3D)

内切或者外切正六边形中由6个60度的正三角形组成：假定圆的半径r=1

内切正六边形的边长R=2*sin30，周长s=2sin30 *6，即内切多边形的周长=2sin(360/3072/2) *3072 ,此时pi_min=sin(360/3072/2) *3072*

外切正六边形的边长R=2*tan30，周长s=2*tan30 *6  *即外切多边形的周长=2tan(360/3072/2) *3072 ，此时pi_max=sin(360/3072/2) *3072*

Java代码如下所

示

```
public class CutCircle2Pi {

    public static void main(String[] args) {
      // as circle is 360 degredd
        Double degree=360d;
        Double circleCutNum=3072d;
        int r=1;
        Double angle=degree/circleCutNum;
        Double radians=Math.toRadians(angle);
        //内切正方形的边长 2*r*Math.sin(0.5*radians)*circleCutNum  s=2pi*r,pi=s/(2r)
        double pi_min=r*Math.sin(0.5*radians)*circleCutNum;
        System.out.println("circle pi_min "+pi_min);
        //外切正方形的边长 2*r*Math.tan(0.5*radians)*circleCutNum
        double pi_max=r*Math.tan(0.5*radians)*circleCutNum;
        System.out.println("circle pi_max "+pi_max);
    }

}
```

```
        Double degree=360d;
        Double circleCutNum=3072d;
        int r=1;
        Double angle=degree/circleCutNum;
        Double radians=Math.toRadians(angle);
        //内切正方形的边长 2*r*Math.sin(0.5*radians)*circleCutNum  s=2pi*r,pi=s/(2r)
        double pi_min=r*Math.sin(0.5*radians)*circleCutNum;
        System.out.println("circle pi_min "+pi_min);
        //外切正方形的边长 2*r*Math.tan(0.5*radians)*circleCutNum
        double pi_max=r*Math.tan(0.5*radians)*circleCutNum;
        System.out.println("circle pi_max "+pi_max);
    }

}
```

运行结果如下所示：

```
circle pi_min 3.1415921059992713
circle pi_max 3.141593748771352
```

另，为了尽可能的逼近pi，可以更改切圆的数量。我设置了一较大的值

Double circleCutNum=3600000000d;

得到的圆周率如下所示：

```
circle pi_min 3.141592653589793
circle pi_max 3.141592653589793
```

为什么会出现这个线性呢？因为java中都是进行的浮点数运算，会损失部分精准度，故造成了pi逼近的值相等了。

# 总结

在2019年3月份， **谷歌计算机已经将圆周率计算到了小数点后的31.4万亿位** 。但不得不佩服2000年前古人的智慧，在没有任何工具协助下，完成这项复杂的计算。
