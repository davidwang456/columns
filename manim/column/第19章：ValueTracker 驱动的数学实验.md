# 第19章：ValueTracker 驱动的数学实验

---

## 1. 项目背景

第 14 章已经介绍了 `ValueTracker` 的基础用法（驱动参数变化），但仅限于"单个参数控制一条曲线"。随着动画复杂度升级，真实的教学场景往往需要多个参数联动、参数与可视化之间有多条映射关系。

某高校数学建模团队正在用 Manim 制作"傅里叶级数逼近方波"的动画。需求是：通过增加谐波的阶数（N），观众可以看到叠加的正弦曲线从一个模糊的波形逐步收敛到方波形状。教研老师要求这个动画既能"单步调 N"展示静态度对比，又能"连续扫参"展示动态渐进过程。

这个需求的核心技术就是 `ValueTracker` 的多维度应用——不仅要驱动一条曲线，还要驱动谐波分解表格、频谱直方图和误差曲线的同步更新。本章要深入讲解多 tracker 协同、`DecimalNumber` 动态标签和参数扫描的工作流。

---

## 2. 剧本式交锋对话

> **场景**：数学建模团队的小林在调试傅里叶级数动画。屏幕上三条曲线各走各的。

**小胖**（捧着半个西瓜用勺子挖着吃）：

"小林你这个动画让我想起了多屏手机广告——左边正弦在扭，右边余弦在摇，中间那条总曲线像个醉汉走 S 路。这是效果还是 bug？"

**小林**（叹气）：

"我用了 3 个 ValueTracker，分别控制 a0、a1、b1。但问题是——动画跑起来后，a1 和 b1 的动画进度不同步。一条曲线的谐波加到了 5 阶，另一条才刚开始。"

**小白**（调出代码）：

"你的问题出在多 tracker 的**独立动画没有同步**。你依次写了三个 play：

```python
self.play(a1_tracker.animate.set_value(5), run_time=2)
self.play(b1_tracker.animate.set_value(5), run_time=2)
```

"第一个 play 跑完 2 秒后第二个才开始——当然不同步了。正确做法是把它们放到一个 `play` 里并行："

```python
self.play(
    a1_tracker.animate.set_value(5),
    b1_tracker.animate.set_value(5),
    run_time=2,
)
```

**大师**（站起补充）：

"小白指出了多 tracker 同步的基本面。我再讲一个更高级的技巧——**用单个 tracker 驱动多个衍生值**。

在傅里叶级数场景中，你实际只需要一个 tracker 表示'阶数 N'，所有谐波系数都是从 N 推导出来的——不需要给每个系数建独立的 tracker。用一个函数 `harmonics(N)` 接收 N 并返回（a1, b1, a2, b2, ...），所有视觉效果都从这个函数消费数据。"

```python
n_tracker = ValueTracker(1)

def get_waveform():
    N = int(n_tracker.get_value())
    # 从 N 计算出所有谐波系数和合成波形
    return axes.plot(lambda x: sum_fourier(x, N), ...)

waveform = always_redraw(get_waveform)
self.play(n_tracker.animate.set_value(10), run_time=5)
```

"N 从 1 到 10 连续变化，曲线自动更新。不需要管理 10 个 tracker 的同步问题。"

> **技术映射**：`ValueTracker` 是一个数值容器，`tracker.get_value()` 返回当前值。`DecimalNumber(tracker)` 可创建一个跟随 tracker 数值更新的文字标签。

**小胖**（挖了一勺西瓜）：

"那 `DecimalNumber` 是啥？我看别人用它在动画里显示实时数值。"

**小白**：

"`DecimalNumber` 是一个特殊的 VMobject，它接受一个数值（通常来自 `ValueTracker.get_value()`），实时渲染成字符串，并支持小数位控制。典型用法：

```python
n_tracker = ValueTracker(1)
n_label = DecimalNumber(
    n_tracker.get_value(),
    num_decimal_places=1,  # 保留 1 位小数
    font_size=36, color=YELLOW,
)
n_label.add_updater(lambda m: m.set_value(n_tracker.get_value()))
```

"每次 tracker 的值变化，n_label 会自动更新显示。它比 `always_redraw(lambda: MathTex(...))` 轻量得多——只重建文字，不触发 LaTeX 编译。"

**大师**：

"最后，参数扫描的黄金组合是 `np.linspace` + `for` 循环 + `ValueTracker.set_value`：

```python
for val in np.linspace(0, 10, 50):  # 50 步从 0 到 10
    tracker.set_value(val)
    self.wait(0.05)  # 等待约 1 帧时间
```

"`set_value` 是瞬间跳变（不像 `animate.set_value` 是平滑过渡），配合 50 帧的循环就模拟出了连续扫描的效果。比 `animate.set_value` 更灵活——你可以控制扫参的非均匀步长。"

**小胖**（西瓜已经见了底）：

"我还有个实战问题——如果我同时有 3 个 tracker（分别控制频率、振幅和相位），想让它们同时变化但节奏不同（比如频率快速扫，相位慢慢调），怎么写才干净？"

**小白**：

"那就是用同一个 `play` 但不同 `run_time` 不能直接写在一个 play 里——因为 play 的 `run_time` 对所有子动画统一生效。正确做法是分层嵌套 `AnimationGroup`：

```python
self.play(
    AnimationGroup(
        freq_tracker.animate(run_time=2, rate_func=linear).set_value(10),
        amp_tracker.animate(run_time=4, rate_func=smooth).set_value(3),
        phase_tracker.animate(run_time=2, rate_func=there_and_back).set_value(PI),
        run_time=4,  # 外层 run_time 被最长者（4秒）决定
    )
)
```

"这里 `AnimationGroup` 取最长的子动画（4秒）为基准，`rate_func` 各自独立控制运动曲线。相位用 `there_and_back` 扫到 PI 又回来，振幅用 `smooth` 慢慢调整，频率用 `linear` 快速跑完。"

**大师**：

"补充一个实际技巧——如果你想让观众看到参数变化过程中的'中间状态'（如频率 f=5Hz 时的波形），可以在扫参过程中插入 `wait(0.5)` 做"停留点"。用 `set_value` 停在关键值上，给观众消化时间。"

> **技术映射**：`ValueTracker.set_value()` 直接赋值无动画，`ValueTracker.animate.set_value()` 创建平滑过渡 Animation。用 `set_value` + `wait` 交替实现停留效果。

---

## 3. 项目实战

### 3.1 环境准备

沿用基础篇环境。本章需要 `numpy`（Manim 已内置依赖）。

---

### 3.2 分步实现

> **本章实战目标**：制作一个"傅里叶级数逼近方波"动画，展示递增阶数 N 时波形的渐进收敛过程。

---

#### 步骤一：DecimalNumber 基础

**步骤目标**：掌握 `DecimalNumber` 的创建和与 tracker 的绑定。

```python
# scenes/chapter19_decimal.py
from manim import *

class DecimalNumberDemo(Scene):
    def construct(self):
        tracker = ValueTracker(0)

        # 创建数字标签并绑定
        label = DecimalNumber(
            tracker.get_value(),
            num_decimal_places=2,
            font_size=72, color=YELLOW,
        )
        label.add_updater(lambda m: m.set_value(tracker.get_value()))

        title = Text("DecimalNumber 演示", font_size=36, color=BLUE).to_edge(UP, buff=0.4)
        self.play(Write(title), FadeIn(label, scale=1.5), run_time=1.5)
        self.wait(0.3)

        # 从 0 平滑过渡到 100
        self.play(tracker.animate.set_value(100), run_time=3, rate_func=smooth)
        self.wait(0.5)

        # 瞬间跳变
        tracker.set_value(999)
        self.wait(0.5)

        self.play(FadeOut(VGroup(title, label)), run_time=1)
```

---

#### 步骤二：傅里叶级数逼近方波

**步骤目标**：展示 N 从 1 到 20 阶的谐波叠加过程。

```python
# scenes/chapter19_fourier.py
from manim import *
import numpy as np

class FourierSquareWave(Scene):
    def construct(self):
        # ---- 坐标轴 ----
        axes = Axes(
            x_range=[0, 2 * PI, PI / 2],
            y_range=[-1.5, 1.5, 0.5],
            x_length=8, y_length=5,
            axis_config={"color": BLUE, "include_tip": True},
        )
        axes.shift(DOWN * 0.5)
        axes.x_axis.add_labels(
            {0: "0", PI: r"\pi", 2 * PI: r"2\pi"}
        )

        title = Text("傅里叶级数逼近方波", font_size=36, color=BLUE)
        title.to_edge(UP, buff=0.4)

        self.play(Write(title), Create(axes), run_time=2)

        # ---- 目标方波（虚线） ----
        target_wave = axes.plot(
            lambda x: 1 if np.sin(x) > 0 else -1,
            x_range=[0.01, 2 * PI - 0.01],
            color=GRAY, stroke_width=1, stroke_opacity=0.4,
        )
        target_label = Text("目标方波", font_size=20, color=GRAY)
        target_label.next_to(target_wave.get_end(), UR, buff=0.2)
        self.play(Create(target_wave), Write(target_label), run_time=1.5)

        # ---- N 阶 tracker ----
        n_tracker = ValueTracker(1)

        def fourier_wave(x, N):
            """计算傅里叶级数近似值"""
            result = 0.0
            for k in range(1, int(N) + 1):
                if k % 2 == 1:  # 只取奇数谐波
                    result += (4 / (np.pi * k)) * np.sin(k * x)
            return result

        # 实时更新的近似曲线
        approx_wave = always_redraw(lambda: axes.plot(
            lambda x: fourier_wave(x, n_tracker.get_value()),
            x_range=[0.01, 2 * PI - 0.01],
            color=YELLOW, stroke_width=3,
        ))

        # N 值实时显示
        n_label = DecimalNumber(
            n_tracker.get_value(),
            num_decimal_places=0,
            font_size=40, color=YELLOW,
        )
        n_label.add_updater(lambda m: m.set_value(n_tracker.get_value()))
        n_desc = Text("N =", font_size=32, color=GRAY)
        n_desc.next_to(n_label, LEFT, buff=0.1)
        n_group = VGroup(n_desc, n_label).to_corner(UR, buff=0.8)

        self.add(approx_wave, n_group)
        self.wait(0.5)

        # ---- 渐进增加 N ----
        stages = [1, 3, 5, 7, 11, 21]
        for N in stages:
            self.play(n_tracker.animate.set_value(N), run_time=1.5)
            self.wait(0.8)

        self.wait(1)

        # 高亮说明
        note = Text(
            "随着 N 增加，近似曲线越来越接近方波",
            font_size=24, color=GREEN,
        )
        note.next_to(axes, DOWN, buff=0.6)
        self.play(Write(note), run_time=1.5)
        self.wait(1.5)

        self.play(FadeOut(VGroup(title, axes, target_wave, target_label,
                                  approx_wave, n_group, note)), run_time=2)
```

**运行命令**：

```bash
manim -pqm scenes/chapter19_fourier.py FourierSquareWave
```

**运行结果**：

画面展示坐标轴（x 轴 0 到 2π），灰色虚线标注目标方波，黄色曲线是当前阶数的傅里叶近似。N 从 1 逐次增加到 3、5、7、11、21，每个阶段停留 0.8 秒供观众对比。右上角实时显示当前 N 值。随着 N 增大，近似曲线从简单的正弦波逐渐"长出"方波的棱角。

---

#### 步骤三：多参数扫描——N 连续变化

**步骤目标**：展示 N 从 1 连续变化到 20 的"扫描"过程。

```python
class FourierSweep(Scene):
    def construct(self):
        axes = Axes(
            x_range=[0, 2 * PI, PI / 2],
            y_range=[-1.5, 1.5, 0.5],
            x_length=8, y_length=5,
            axis_config={"color": BLUE, "include_tip": True},
        )
        axes.shift(DOWN * 0.5)

        self.play(Create(axes), run_time=1.5)

        n_tracker = ValueTracker(1)

        def fourier_wave(x, N):
            result = 0.0
            for k in range(1, int(N) + 1):
                if k % 2 == 1:
                    result += (4 / (np.pi * k)) * np.sin(k * x)
            return result

        approx_wave = always_redraw(lambda: axes.plot(
            lambda x: fourier_wave(x, n_tracker.get_value()),
            x_range=[0.01, 2 * PI - 0.01],
            color=YELLOW, stroke_width=3,
        ))

        n_label = DecimalNumber(1, num_decimal_places=0, font_size=40, color=YELLOW)
        n_label.add_updater(lambda m: m.set_value(n_tracker.get_value()))
        n_desc = Text("N =", font_size=32, color=GRAY)
        n_group = VGroup(n_desc, n_label).to_corner(UR, buff=0.8)

        self.add(approx_wave, n_group)

        # 连续扫描：N 从 1 平滑到 20
        self.play(n_tracker.animate.set_value(20), run_time=5, rate_func=linear)
        self.wait(1)

        self.play(FadeOut(VGroup(axes, approx_wave, n_group)), run_time=1.5)
```

---

### 3.3 完整代码清单

```python
# scenes/chapter19_decimal.py —— DecimalNumberDemo
# scenes/chapter19_fourier.py —— FourierSquareWave, FourierSweep
```

### 3.4 测试验证

| 验证项 | 操作 | 预期结果 |
|--------|------|----------|
| 单 tracker 驱动 | 修改 N 值检查曲线形态 | 曲线随 N 增加趋近方波 |
| DecimalNumber 跟随 | 观察 tracker 变化时数字标签 | 实时同步更新 |
| always_redraw 更新 | 在每次 N 变化后打印曲线点数 | 曲线被重建 |
| 连续扫描流畅性 | 渲染 FourierSweep 并观察 | 5 秒内曲线平滑演变 |

---

## 4. 项目总结

### 优点 & 缺点

| 维度 | 优点 | 缺点 |
|------|------|------|
| 单 tracker 多衍生量 | 一个 tracker 驱动全部视觉效果，同步自然 | 衍生函数的计算复杂度集中在 always_redraw |
| DecimalNumber | 轻量实时文本，比 always_redraw(MathTex) 省资源 | 不支持公式排版，只能显示数值 |
| 参数扫描 | set_value 瞬间跳变 + for 循环实现可控扫描 | 循环 + wait 的累加会导致非恒定帧率 |
| 动画过渡 | animate.set_value 平滑过渡适合展示演变 | 无法中途改变目标值或速度 |
| 数学实验 | 傅里叶、泰勒展开等分析性主题天然适合 | 若数值计算过重（如 O(n³)），渲染会阻塞 |

### 适用场景

| 场景 | 说明 |
|------|------|
| 傅里叶/泰勒级数 | 调和分析、逼近理论 |
| 梯度下降 | 损失函数曲面的参数更新 |
| 参数探索 | 函数族 f(x;a,b,c) 中 a/b/c 对形状的影响 |
| 数据拟合 | 多项式拟合、样条插值 |
| 动画对比 | A/B 测试不同参数组合的视觉效果 |

**不适用场景**：需要实时交互调整参数（应用 ManimGL + IPyWidgets）、参数维度高于 5 个（可视化空间不足）。

### 注意事项

1. **`DecimalNumber.set_value()` 不能用于 `animate` 链**：`DecimalNumber` 的数值变化应通过绑定的 tracker 驱动，不直接调用 `set_value()`。
2. **`always_redraw` 中的 `n_tracker.get_value()` 是实时值**：它在每一帧被调用，反映了 tracker 在那一帧的准确值。不要缓存它。
3. **傅里叶级数中的浮点精度**：当 N 很大时（如 N=100），`1 / (np.pi * k)` 可能产生数值误差。建议用 `np.float64` 保持精度。

### 常见踩坑经验

**故障一：`DecimalNumber` 更新动画报 `AttributeError`**

根因：对 `DecimalNumber` 使用了 `.animate.set_value()`——它不支持 animate 接口。

解决：始终通过 `add_updater` 绑定 tracker 来更新，或用 `always_redraw` 替代。

**故障二：傅里叶曲线在 x=0 和 x=2π 处有尖刺**

根因：`plot` 函数的采样范围包含端点，但方波的间断点导致数值震荡（吉布斯现象）。

解决：采样范围设为 `[0.01, 2*PI - 0.01]` 避开端点，或增加采样密度以平滑震荡。

**故障三：`set_value` 循环中 `wait(0.05)` 不产生预期帧数**

根因：`wait(0.05)` 在 30fps 下约等于 1.5 帧，Manim 可能只渲染 1 或 2 帧。

解决：用 `set_value` + `self.wait(1/self.camera.frame_rate)` 或改用 `animate.set_value`。

### 思考题

1. 扩展 `FourierSquareWave`，增加一个"频谱图"——在画面右上角用柱状图显示各阶谐波的振幅（`4/(πk)` for odd k）。要求频谱图随 N 增加而增长。提示：用 `Rectangle` 柱状条 + `always_redraw`。

2. 将傅里叶级数换为"泰勒展开"：用 `ValueTracker` 控制展开项数 N，展示 `e^x` 的泰勒多项式在 x ∈ [-3, 3] 上随 N 增加逐渐逼近 `e^x`。提示：`f(x, N) = sum_{n=0}^{N} x^n / n!`。

---

### 推广计划提示

| 角色 | 本章阅读重点 | 协作事项 |
|------|-------------|----------|
| 新人开发 | 掌握多 tracker 协同和 DecimalNumber | 完成傅里叶方波动画 |
| 测试 | 验证不同 N 值下的曲线形态 | 对比 Manim 输出与 Matplotlib 对照组 |
| 运维 | 关注参数扫描的批量渲染 | 设计自动化参数扫描报告 |
