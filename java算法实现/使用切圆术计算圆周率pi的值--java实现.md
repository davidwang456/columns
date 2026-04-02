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

为什么会出现这个现象呢？因为 **`double` 是 IEEE 754 双精度浮点数**，有效数字约 **15～17 位十进制**。当边数 `n` 足够大时，内切、外切公式在 `double` 精度下已经“算到头了”，继续增大 `circleCutNum` 不会再多出真实小数位，只会得到相同的舍入结果，看起来像“逼近值相等”。

要提高精度，思路不是无限加大 `n` 仍用 `double`，而是：

1. **用任意精度十进制运算**（`java.math.BigDecimal` + `MathContext`），把边数加倍与开方算到指定有效位数；
2. **尽量避免在超高精度下调用 `Math.sin` / `Math.tan`**（它们内部仍是 `double`），改用**与刘徽割圆术一致的纯几何递推（倍边公式）**，只用到加减乘除和开方。

下面给出 **倍边公式** 的要点与 Java 实现。

## 为何 `double` 不够、BigDecimal 可以

| 类型 | 有效精度（约） | 说明 |
|------|----------------|------|
| `double` | 15～17 位十进制 | `Math.sin` 等三角函数返回值也是 `double`，链条上最早就被限制住 |
| `BigDecimal` | 由 `MathContext.precision` 指定 | 可用牛顿迭代等自己做 `sqrt`，递推边长时全程保持高精度 |

## 倍边公式（倍边割圆，无需三角函数）

半径 **r = 1** 的圆内接正 **n** 边形边长记为 **s_n**。从正六边形开始 **s_6 = 1**（与文首几何一致）。将边数加倍时，边长满足：

\[ s_{2n} = \sqrt{\,2 - \sqrt{\,4 - s_n^{2}\,}\,} \]

此时圆周率的下界估计为（半周长）：

\[ \pi \approx \frac{n \cdot s_n}{2} \quad (r=1) \]

不断令 **n ← 2n**、用上式更新 **s**，等价于刘徽“割之又割”的过程；精度主要由 **BigDecimal 的 precision** 与迭代次数决定，而不是被 `double` 截断。

> 外切多边形也可写出类似的倍边关系得到 **π 的上界**；若只关心高精度单侧估计，内接递推已足够说明割圆术与任意精度结合的方式。

### 为何会得到 `0E-116` 之类结果

若像旧版示例那样**全程只用 `MathContext(80)`** 做递推，会出现两类问题：

1. **相消误差**：当边数很大时 **s** 极小，**s²** 远小于 4。在只有 80 位**有效数字**的前提下，`4 - s²` 会被舍入成 **4**，于是 `√(4-s²)=2`、`2-√(4-s²)=0`，下一步 **s** 变成 0，最终 **π** 变成 0。
2. **错误的 sqrt 初值**：用 `Math.sqrt(x.doubleValue())` 给牛顿法当种子时，**x** 极小时 double 下溢为 **0**，迭代也会失败。

另有一条极易忽略：**边数 `n = 6 × 2^k` 在 k 较大时远超 `int` 范围**（约 k≥28 即溢出）。若用 `int n` 和 `n <<= 1`，**高位被丢弃后 `n` 常为 0**，则 `π = n·s/2` 会变成 **0**（打印成 `0E-116`、`0E-268` 等），与 `MathContext` 无关。必须用 **`BigInteger`**（或等价方式）保存 **n**。

**正确做法**：递推与开方使用**更高的工作精度** `workPrec`（通常约为「输出位数 + 翻倍次数 + 常数」），只在最后对 **π** 四舍五入到目标位数；开方优先使用 **JDK 9+** 自带的 `BigDecimal.sqrt(MathContext)`（不经过 double）。

### Java 示例：`BigDecimal` + 足够的工作精度（JDK 9+）

```java
import java.math.BigDecimal;
import java.math.BigInteger;
import java.math.MathContext;
import java.math.RoundingMode;

/**
 * 刘徽倍边割圆求 π。需要 JDK 9+（使用 BigDecimal.sqrt）。
 */
public class CutCirclePiBigDecimal {

    /** x > 0，在工作精度下开平方 */
    public static BigDecimal sqrtPos(BigDecimal x, MathContext mcWork) {
        if (x.signum() <= 0) {
            throw new IllegalArgumentException("sqrt 要求 x > 0");
        }
        return x.sqrt(mcWork);
    }

    /**
     * @param doubleSteps 倍边次数，边数 n = 6·2^doubleSteps
     * @param outDigits   最终 π 保留的有效十进制位数
     */
    public static BigDecimal piByDoubling(int doubleSteps, int outDigits) {
        // 工作精度：必须能分辨 4 - s²。s 约按 2^(-k) 量级缩小，经验公式给足余量
        int workPrec = Math.min(outDigits + doubleSteps + 32, 20_000);
        MathContext mcWork = new MathContext(workPrec, RoundingMode.HALF_EVEN);
        MathContext mcOut = new MathContext(outDigits, RoundingMode.HALF_EVEN);

        BigDecimal s = BigDecimal.ONE; // s_6 = 1
        // 注意：n = 6·2^k 在 k≥28 时已超过 int 上限，不能用 int 与 <<=，否则溢出为 0，π 会变成 0
        BigInteger n = BigInteger.valueOf(6);
        BigDecimal two = BigDecimal.valueOf(2);
        BigDecimal four = BigDecimal.valueOf(4);

        for (int k = 0; k < doubleSteps; k++) {
            BigDecimal s2 = s.multiply(s, mcWork);
            BigDecimal inner = sqrtPos(four.subtract(s2, mcWork), mcWork);
            s = sqrtPos(two.subtract(inner, mcWork), mcWork);
            n = n.shiftLeft(1);
        }

        BigDecimal pi = s.multiply(new BigDecimal(n), mcWork).divide(two, mcWork);
        return pi.round(mcOut);
    }

    public static void main(String[] args) {
        int digits = 80;
        int doubles = 120;
        BigDecimal pi = piByDoubling(doubles, digits);
        System.out.println("BigDecimal π (输出有效位=" + digits + ", doubling=" + doubles + "):");
        System.out.println(pi);
        System.out.println("\n对比 double 的 Math.PI:");
        System.out.println(Math.PI);
    }
}
```

运行后应得到与 **3.141592653589793…** 一致的前 **80** 位有效数字（再往后是否全对，还受 `doubleSteps` 是否足够大限制）。

### 实用注意

1. **工作精度与翻倍次数**：输出 **d** 位、翻倍 **k** 次时，可先用 `workPrec ≈ d + k + 32`（并设上限避免内存过大）。**不要**用输出精度去做每一步的 `4 - s²`。
2. **极端位数**（百万位级 π）不会用割圆递推，而用 **Chudnovsky**、**Machin 公式** 等配合 FFT 乘法；教学场景下 **BigDecimal + 倍边** 已足够说明“如何避免浮点精度损失”。
3. 若仍想用 **sin(π/n)** 形式，也必须用 **BigDecimal 的泰勒级数** 等手段在弧度 **π/n** 上展开，且 **π/n** 本身要用已算出的高精度值或有理逼近，实现成本高于倍边公式。

# 总结

在2019年3月份， **谷歌计算机已经将圆周率计算到了小数点后的31.4万亿位** 。那类工程实现依赖专门算法与海量内存/存储，与 `double` 无关。就**割圆术思想**而言：日常要提高精度，应使用 **`BigDecimal`（或第三方任意精度库）** 配合 **倍边递推** 或更高阶的级数/公式，而不是在 `double` 上无限增大边数。

不得不佩服 2000 年前古人的智慧——在没有现代浮点硬件的条件下，仍能完成极其精细的割圆估计。
