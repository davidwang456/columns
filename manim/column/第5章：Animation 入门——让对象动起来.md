# 第5章：Animation 入门——让对象动起来

---

## 1. 项目背景

某程序员社区准备举办一期"算法可视化大赛"，参赛者需要用动画展示经典算法的执行过程。选手陈锋选择了"冒泡排序可视化"作为选题——他希望用彩色柱状条的高度变化来展示数组元素的排序过程。

陈锋花了三天写出第一版：每个柱子单独创建，比较时两个柱子变红，交换时两个柱子互换高度。代码写完一跑，出问题了——"交换"这一步，两个柱子的高度是"瞬间"切换的，就像 PPT 里直接改数字一样生硬。观众看不到"柱子从高度 5 平滑变到高度 3"的过程，只能看到画面一闪就换完了。

陈锋的同事帮忙看了一眼代码："你没用 Animation 啊。你只是用 `set_height()` 直接修改了柱子的属性，Manim 当然不会自动加过渡效果。你需要 `play(Counter.animate.set_height(...))` 才能看到平滑动画。"

陈锋恍然大悟，但很快又遇到新问题：动画速度太快来不及看清细节，改慢了又拖沓；多条动画想同时播放，但不知怎么组合；特别想做一个"比较-交换-确认"的三步流程，但动画一个接一个的时间控制总是对不齐。

这个痛点恰恰是 Manim 动画系统的核心价值所在——**Animation 不仅让对象"动"，更提供了一套精确的时间控制体系**：

- `rate_func` 决定动画曲线（线性、缓入缓出、弹簧等）
- `run_time` 控制动画总时长
- `lag_ratio` 在批量动画中制造错位感
- `AnimationGroup` / `LaggedStart` / `Succession` 组合多个动画的并行或串行关系
- `animate` 语法糖让属性修改也能参与动画管线

本章将从排序可视化出发，逐步讲解这些概念，最终交付一段节奏舒适的冒泡排序动画。

---

## 2. 剧本式交锋对话

> **场景**：陈锋在屏幕前反复调试动画速度，柱状条时而瞬间跳变，时而像慢动作回放。

**小胖**（啃着鸡腿走过来）：

"锋哥，你这冒泡排序动画——怎么说呢，挺'冒泡'的。柱子一会儿唰一下变了，一会儿又慢得我以为电脑卡了。你是不是把 `run_time` 设成了随机数？"

**陈锋**（无奈地抓头）：

"不是……我一开始只用了 `self.play(obj.animate.set_height(x))`，完全没设 `run_time`，它就默认很快。后来我试着设了 `run_time=3`，结果慢到像在播慢放。中间值怎么调都不对劲。"

**小白**（冷眼旁观了一会儿）：

"你陷入了二元思维——不是快就是慢。Animation 的速度不应该只有一个维度。`run_time` 只管'多久做完'，但动画的快慢感觉还取决于另一个参数——`rate_func`。同样 2 秒，`rate_func=linear` 是匀速运动，看起来机械；`rate_func=smooth` 是慢→快→慢，看起来更自然、更像物理世界的运动。后者在视觉上会让人觉得'更灵敏'，因为它把更多时间花在了起点和终点——那是人眼最敏感的阶段。"

**大师**（接过话题）：

"我来换个通俗的比方。想象你在电梯里：匀速电梯从 1 楼到 10 楼，你感觉就是'在动而已'。但迪士尼乐园里的'跳楼机'——启动时猛冲，中段匀速，快到时突然减速——这就是 `smooth` 的效果。观众看动画时，视觉注意力在'开始动'和'快结束'两个时刻最集中，所以把时间分配给这两端，比匀速运动更抓眼球。"

> **技术映射**：`rate_func` 是一个函数 `f(alpha) -> alpha`，输入线性进度 0→1，输出实际进度。`smooth` 函数的曲线是 sigmoid-like（S 型），两端平缓中间陡峭。Manim 内置了 `linear`、`smooth`、`there_and_back`、`rush_into`、`rush_from` 等曲线。

**小胖**（鸡腿差点掉了）：

"等等，我想起来了！我打游戏的时候，角色走路就是匀速的，但开枪的后坐力是'砰！慢慢收回'的 —— 是不是也可以用 `rate_func` 实现？"

**大师**：

"对！开火的一瞬间用 `rush_into`——从慢到快；后坐力回弹用 `rush_from`——从快到慢。同一个动画，配不同 rate_func，表达的感觉完全不同。建议你做一个实验：把排序中'交换'这个动作分别用 `linear`、`smooth` 和 `there_and_back` 各渲染一次，然后对比——你会立刻明白 rate_func 的叙事力量。"

> **技术映射**：`there_and_back` 让动画先正向走到终点，再反向回到起点，适合"看一下这个"的强调效果。

**小白**（继续追问）：

"那 `lag_ratio` 是怎么用的？我理解它是让一组动画错开播放，但它和 `run_time` 之间是什么数学关系？"

**大师**：

"很好。假设你要让 10 个柱子依次冒出来，每个柱子的动画用 `run_time=0.3` 秒。如果直接写 10 个 `play`，总时长是 3 秒，但看起来很死板——一个出来完了下一个才开始。用 `LaggedStart` 配合 `lag_ratio=0.2`，意思是：第 1 个柱子 0 时刻开始，第 2 个柱子在第 0.06 秒开始（0.3 × 0.2），第 3 个在第 0.12 秒开始……最后总时长约 `run_time * (1 + (n-1) * lag_ratio)` = 0.3 × (1 + 9×0.2) = 0.84 秒。时间压缩了近 4 倍，但视觉上更流畅——这就是 lag_ratio 的魔力。"

> **技术映射**：`lag_ratio` 表示相邻动画的延迟比例。`lag_ratio=0.2` 意味着每个动画在"前一个动画完成 20%"时就开始。`lag_ratio=1` 是完全串行（一个结束才下一个），`lag_ratio=0` 是完全并行。

**陈锋**（重新振作）：

"明白了。那我最后一个问题——`animate` 语法和直接用 `Transform` 区别在哪？什么时候用哪个？"

**大师**：

"这个问题问到了 Manim 动画系统的两根支柱。用表格说清楚——"

| 维度 | `obj.animate.property(value)` | `Transform(obj, target)` |
|------|-------------------------------|--------------------------|
| 底层实现 | 自动创建一个 `_MethodAnimation` | 拷贝 target 状态，每帧插值 morph |
| 适用场景 | 单个属性变化（move、scale、set_color） | 形态变化（圆变方、A 变 B） |
| 是否创建新对象 | 不创建 | Transform 内部会 deepcopy target |
| 链式调用 | 支持 `.animate.shift(UP).set_color(RED)` | 不支持，需要提前创建 target |
| 性能 | 轻量，只改属性 | 较重，涉及整个对象形态插值 |

"简单记住：**改属性用 animate，变形态用 Transform**。"

---

## 3. 项目实战

### 3.1 环境准备

沿用第 2 章搭建的环境。本章需要 `numpy` 依赖（Manim 自带）。

---

### 3.2 分步实现

> **本章实战目标**：制作一个"冒泡排序可视化"动画，展示柱状条的比较（红色高亮）、交换（平滑移动）和最终排序完成的过程。

---

#### 步骤一：理解基础动画类

**步骤目标**：对比 `Create`、`FadeIn`、`Write`、`Transform`、`GrowFromCenter` 的视觉效果差异。

```python
# scenes/chapter05_basics.py
from manim import *

class AnimationCatalog(Scene):
    def construct(self):
        # 准备 5 个位置，分别展示 5 种动画
        positions = [LEFT * 5 + UP * 2.5, ORIGIN + UP * 2.5, RIGHT * 5 + UP * 2.5]

        # 1. Create —— 逐渐绘制贝塞尔曲线
        c1 = Circle(radius=0.8, color=RED).move_to(positions[0])
        self.play(Create(c1), run_time=1.5)
        label1 = Text("Create", font_size=20, color=RED).next_to(c1, DOWN, buff=0.3)
        self.play(Write(label1), run_time=0.5)
        self.wait(0.3)

        # 2. FadeIn —— 从无到有淡入
        s1 = Square(side_length=1.6, color=BLUE).move_to(positions[1])
        self.play(FadeIn(s1), run_time=1.5)
        label2 = Text("FadeIn", font_size=20, color=BLUE).next_to(s1, DOWN, buff=0.3)
        self.play(Write(label2), run_time=0.5)
        self.wait(0.3)

        # 3. GrowFromCenter —— 从中心点膨胀
        t1 = Triangle(color=GREEN).move_to(positions[2])
        self.play(GrowFromCenter(t1), run_time=1.5)
        label3 = Text("GrowFromCenter", font_size=20, color=GREEN).next_to(t1, DOWN, buff=0.3)
        self.play(Write(label3), run_time=0.5)
        self.wait(0.5)

        # 4. 全部清屏
        self.play(
            FadeOut(c1), FadeOut(label1),
            FadeOut(s1), FadeOut(label2),
            FadeOut(t1), FadeOut(label3),
            run_time=1.5,
        )
        self.wait(0.3)

        # 5. Transform —— 形态过渡
        c2 = Circle(radius=0.8, color=YELLOW, fill_opacity=0.5)
        s2 = Square(side_length=1.6, color=ORANGE, fill_opacity=0.5)
        c2.move_to(LEFT * 2)
        s2.move_to(RIGHT * 2)

        self.play(Create(c2), run_time=1)
        self.wait(0.3)
        self.play(Transform(c2, s2), run_time=2)
        self.wait(0.3)

        # 6. Indicate —— 闪烁强调
        self.play(Indicate(c2, color=RED, scale_factor=1.2), run_time=1.5)
        self.wait(0.5)

        self.play(FadeOut(c2), run_time=1)
        self.wait(0.3)
```

---

#### 步骤二：冒泡排序可视化

**步骤目标**：制作完整的冒泡排序动画，展示比较-交换-确认三步流程。

```python
# scenes/chapter05_bubblesort.py
from manim import *
import numpy as np

class BubbleSort(Scene):
    def construct(self):
        # ---- 第1段：创建随机柱子 ----
        values = [17, 5, 12, 8, 20, 3]
        bars = self.create_bars(values)
        bars.move_to(ORIGIN)

        title = Text("冒泡排序可视化", font_size=40, color=BLUE)
        title.to_edge(UP, buff=0.4)

        self.play(Write(title), run_time=1)
        self.play(
            LaggedStart(
                *[GrowFromCenter(bar) for bar in bars],
                lag_ratio=0.15,
            ),
            run_time=2,
        )
        self.wait(0.5)

        # ---- 第2段：逐对比较与交换 ----
        n = len(values)
        for i in range(n - 1):
            for j in range(n - 1 - i):
                # 高亮当前比较的两个柱子
                self.play(
                    bars[j].animate.set_color(RED),
                    bars[j + 1].animate.set_color(RED),
                    run_time=0.3,
                )

                if values[j] > values[j + 1]:
                    # 需要交换——两个柱子平滑互换位置
                    self.play(
                        SwapBars(bars[j], bars[j + 1]), run_time=0.8
                    )
                    # 更新引用
                    values[j], values[j + 1] = values[j + 1], values[j]
                    # VGroup 内部索引不变，需要交换元素 → 用 VGroup 的 swap 方案
                    # 注：此处创新用法，将 bars VGroup 中的元素交换
                    bars.submobjects[j], bars.submobjects[j + 1] = \
                        bars.submobjects[j + 1], bars.submobjects[j]

                # 恢复颜色
                self.play(
                    bars[j].animate.set_color(TEAL),
                    run_time=0.2,
                )

            # 本轮最后一个归位的柱子标绿
            self.play(
                bars[n - 1 - i].animate.set_color(GREEN),
                run_time=0.5,
            )

        # 最后一个元素也标记为绿色
        self.play(bars[0].animate.set_color(GREEN), run_time=0.5)

        # ---- 第3段：排序完成 ----
        done = Text("排序完成！", font_size=48, color=GREEN, weight=BOLD)
        done.next_to(bars, DOWN, buff=1.2)
        self.play(Write(done), run_time=1.5)
        self.wait(1)

        self.play(FadeOut(bars), FadeOut(done), FadeOut(title), run_time=2)
        self.wait(0.5)

    def create_bars(self, values):
        """创建柱子 VGroup，每根柱子高度与数值成正比。"""
        bars = VGroup()
        max_val = max(values)
        for v in values:
            bar = Rectangle(
                width=0.7,
                height=v / max_val * 3.5,
                color=TEAL,
                fill_opacity=0.7,
                stroke_width=1,
            )
            # 标注数值
            label = Text(str(v), font_size=18, color=WHITE)
            label.next_to(bar, DOWN, buff=0.15)
            bar_group = VGroup(bar, label)
            bars.add(bar_group)
        bars.arrange(RIGHT, buff=0.5, aligned_edge=DOWN)
        return bars


class SwapBars(Animation):
    """自定义动画：交换两个柱子的位置。"""
    def __init__(self, bar1, bar2, **kwargs):
        super().__init__(bar1, **kwargs)
        self.bar2 = bar2
        # 保存初始位置
        self.pos1_start = bar1.get_center()
        self.pos2_start = bar2.get_center()

    def interpolate_mobject(self, alpha):
        # 两个柱子沿弧形互换位置
        new_pos1 = self.pos1_start * (1 - alpha) + self.pos2_start * alpha
        new_pos2 = self.pos2_start * (1 - alpha) + self.pos1_start * alpha
        self.mobject.move_to(new_pos1)
        self.bar2.move_to(new_pos2)
```

**运行命令**：

```bash
manim -pqm scenes/chapter05_bubblesort.py BubbleSort
```

**运行结果**：

一段约 15-20 秒的冒泡排序动画。6 根柱子从底部生长出来，然后逐对比较——当前比较的柱子变红色，若需要交换则两柱平滑沿弧线互换位置，交换后恢复青色。每轮结束后，最后一个归位的柱子变为绿色。最终全部绿色 + "排序完成！"文字。

**可能遇到的坑**：

1. **VGroup 内部交换子对象**：`bars.submobjects[j], bars.submobjects[j+1] = ...` 是直接操作 VGroup 的内部列表。这种方式虽然可行，但不是 Manim 的推荐做法——官方建议用 Updater 或 Transform 动画来完成。更稳妥的方案是：创建一个新的 VGroup 承载排序后的柱子排列，用 `Transform(bars, bars_sorted)` 做整体变换。
2. **自定义 Animation 的 `interpolate_mobject`**：这个方法每帧调用一次，`alpha` 从 0 到 1 线性变化。如果想实现弧线交换而非直线交换，需要在插值时加上 Y 轴偏移：`mid_y = max(pos1_start[1], pos2_start[1]) + 0.5 * np.sin(alpha * PI)`。
3. **动画过多导致渲染慢**：本示例只有 6 个元素，若扩展到 50 个，动画数量会暴增。这时建议合并比较动画，减少 `play` 调用次数。

---

#### 步骤三：rate_func 对比实验

**步骤目标**：对比不同 rate_func 对同一动画的视觉效果影响。

```python
# scenes/chapter05_ratefunc.py
from manim import *

class RateFuncDemo(Scene):
    def construct(self):
        title = Text("rate_func 对比：同一路径，不同曲线", font_size=32, color=WHITE)
        title.to_edge(UP, buff=0.5)
        self.play(Write(title), run_time=1.5)

        # 四个圆，用不同 rate_func 沿相同路径运动
        funcs = [
            ("linear", linear),
            ("smooth", smooth),
            ("rush_into", rush_into),
            ("there_and_back", there_and_back),
        ]

        dots = VGroup()
        labels = VGroup()
        for i, (name, rf) in enumerate(funcs):
            dot = Dot(color=YELLOW, radius=0.15)
            dot.move_to(LEFT * 5 + UP * (1.5 - i * 1.0))

            label = Text(name, font_size=20, color=GRAY)
            label.next_to(dot, LEFT, buff=0.3)

            dots.add(dot)
            labels.add(label)

        self.play(
            LaggedStart(*[FadeIn(d) for d in dots], lag_ratio=0.1),
            LaggedStart(*[Write(l) for l in labels], lag_ratio=0.1),
            run_time=2,
        )
        self.wait(0.3)

        # 四个圆同时开始向右移动，用时相同，但 rate_func 不同
        animations = []
        for dot, (_, rf) in zip(dots, funcs):
            animations.append(dot.animate(rate_func=rf).shift(RIGHT * 10))

        # 绘制虚线终点
        end_line = DashedLine(UP * 3, DOWN * 3, color=GRAY, stroke_opacity=0.5)
        end_line.move_to(RIGHT * 5)

        self.play(
            AnimationGroup(*animations, run_time=3),
            Create(end_line),
        )
        self.wait(1.5)

        self.play(FadeOut(VGroup(title, dots, labels, end_line)), run_time=1.5)
```

**运行结果**：

4 个彩色圆点从左侧出发向右侧移动，用时均为 3 秒。观察：
- `linear`：全程匀速，最先到达中点但整体感觉机械
- `smooth`：慢启动→加速→慢停，最自然
- `rush_into`：极慢启动→突然加速冲出→猛停，像被弹弓射出
- `there_and_back`：先到终点再返回起点，适合"看一下然后回来"的提示

---

### 3.3 完整代码清单

> 代码仓库：`https://github.com/yourteam/manim-column-src/tree/main/chapter05`

```python
# scenes/chapter05_bubblesort.py
from manim import *

class SwapBars(Animation):
    def __init__(self, bar1, bar2, **kwargs):
        super().__init__(bar1, **kwargs)
        self.bar2 = bar2
        self.pos1_start = bar1.get_center()
        self.pos2_start = bar2.get_center()

    def interpolate_mobject(self, alpha):
        new_pos1 = self.pos1_start * (1 - alpha) + self.pos2_start * alpha
        new_pos2 = self.pos2_start * (1 - alpha) + self.pos1_start * alpha
        self.mobject.move_to(new_pos1)
        self.bar2.move_to(new_pos2)


class BubbleSort(Scene):
    def construct(self):
        values = [17, 5, 12, 8, 20, 3]
        bars = self.create_bars(values)
        bars.move_to(ORIGIN)

        title = Text("冒泡排序可视化", font_size=40, color=BLUE).to_edge(UP, buff=0.4)
        self.play(Write(title), run_time=1)
        self.play(
            LaggedStart(*[GrowFromCenter(bar) for bar in bars], lag_ratio=0.15),
            run_time=2,
        )
        self.wait(0.5)

        n = len(values)
        for i in range(n - 1):
            for j in range(n - 1 - i):
                self.play(
                    bars[j].animate.set_color(RED),
                    bars[j + 1].animate.set_color(RED),
                    run_time=0.3,
                )
                if values[j] > values[j + 1]:
                    self.play(SwapBars(bars[j], bars[j + 1]), run_time=0.8)
                    values[j], values[j + 1] = values[j + 1], values[j]
                    bars.submobjects[j], bars.submobjects[j + 1] = \
                        bars.submobjects[j + 1], bars.submobjects[j]
                self.play(bars[j].animate.set_color(TEAL), run_time=0.2)
            self.play(bars[n - 1 - i].animate.set_color(GREEN), run_time=0.5)
        self.play(bars[0].animate.set_color(GREEN), run_time=0.5)

        done = Text("排序完成！", font_size=48, color=GREEN, weight=BOLD)
        done.next_to(bars, DOWN, buff=1.2)
        self.play(Write(done), run_time=1.5)
        self.wait(1)
        self.play(FadeOut(bars), FadeOut(done), FadeOut(title), run_time=2)
        self.wait(0.5)

    def create_bars(self, values):
        bars = VGroup()
        max_val = max(values)
        for v in values:
            bar = Rectangle(
                width=0.7, height=v / max_val * 3.5,
                color=TEAL, fill_opacity=0.7, stroke_width=1,
            )
            label = Text(str(v), font_size=18, color=WHITE)
            label.next_to(bar, DOWN, buff=0.15)
            bars.add(VGroup(bar, label))
        bars.arrange(RIGHT, buff=0.5, aligned_edge=DOWN)
        return bars
```

---

### 3.4 测试验证

| 验证项 | 操作 | 预期结果 |
|--------|------|----------|
| 动画不跳跃 | 逐帧检查柱子交换过程 | 位置平滑过渡，无跳变 |
| run_time 生效 | 将 swap 的 run_time 从 0.8 改为 0.2 和 3，对比 | 动画快/慢符合设置值 |
| rate_func 生效 | 在 SwapBars 中传入 `rate_func=there_and_back` | 两柱子交换到中间又回到原位再交换 |
| lag_ratio 生效 | 在 LaggedStart 中试用 `lag_ratio=0` 和 `lag_ratio=1` | 前者所有柱子同时出现，后者依次出现 |
| 颜色不泄漏 | 确认 swap 后红色柱子变回 TEAL | 没有红色残留 |

---

## 4. 项目总结

### 优点 & 缺点

| 维度 | 优点 | 缺点 |
|------|------|------|
| 动画种类 | 内置 30+ 种 Animation，覆盖绝大部分场景 | 缺少"弹性动画"和"物理模拟"类的原生动画 |
| 时间控制 | run_time + rate_func + lag_ratio 三维控制 | rate_func 曲线查看不直观，缺乏可视化编辑器 |
| animate 语法 | 链式调用，语义清晰，学习成本低 | 跟 Transform 混用时行为可能出乎意料 |
| 自定义动画 | 继承 Animation 即可，`interpolate_mobject` 简洁 | 无回调（on_start/on_complete），调试困难 |
| 组合能力 | AnimationGroup/LaggedStart/Succession 灵活 | 不支持"条件判断"（如只在某条件满足时播） |

### 适用场景

| 场景 | 说明 |
|------|------|
| 算法可视化 | 排序、搜索、图遍历的过程演绎 |
| 教学讲解 | 分步骤展示知识点，逐步揭示 |
| 数据展示 | KPI 指标动画、排名变化 |
| 产品演示 | 界面交互的过场动画 |
| 数学动画 | 函数图像变换、公式变形 |

**不适用场景**：需要精确帧同步的交互式动画（应用 ManimGL）、需要与用户输入实时交互的动画。

### 注意事项

1. **`animate` 的参数顺序**：`obj.animate.scale(2).shift(UP)` 是先缩放再移动，但动画播放时两个效果是"同时进行"的（即对属性取插值）。这与直觉上的"先做完一个再做下一个"不同。如果需要串行，应用两个独立的 `play`。
2. **`Transform` 的 target 对象不应再被单独使用**：`Transform(a, b)` 后，`b` 的内部状态已与 `a` 合并。如果之后还要用 `b` 做其他事，应该用 `b_copy = b.copy()` 来保存一个副本。
3. **`lag_ratio` 与 `run_time` 的配合**：`lag_ratio` 基于单个动画的 `run_time` 计算延迟。如果内部动画没有显式设置 `run_time`，会使用默认值（通常为 1 秒），这可能导致整体时长与预期不符。

### 常见踩坑经验

**故障一：`animate` 颜色修改不生效**

根因：`animate` 创建的是基于属性插值的动画，部分属性（如 `fill_color`）的 setter 与 `animate` 的兼容性在旧版本中不完全。

解决：使用 `self.play(obj.animate.set_color(RED))` 而非 `obj.animate.set_fill(RED)`。或改用 `Transform(obj, obj.copy().set_color(RED))`。

**故障二：`LaggedStart` 中所有动画都播完后画面闪烁**

根因：`LaggedStart` 内的子动画有独立的 run_time，如果 `lag_ratio` 设得过大，最后一个动画可能超出视觉舒适区。

解决：手动计算总时长：`total = run_time * (1 + (n - 1) * lag_ratio)`，确认 `lag_ratio` 在 0.1-0.3 之间通常最舒适。

**故障三：自定义 Animation 的 `alpha` 不是 0 到 1**

根因：`interpolate_mobject` 的参数名是 `alpha`，但它的值范围**不一定是 0 到 1**。如果 Animation 设置了 `rate_func`，传入的 `alpha` 是经过 rate_func 映射后的值，但动画的反向播放可能导致 alpha 从 1 到 0。

解决：如果 Animation 只支持正向播放，在 `interpolate_mobject` 中只处理 `alpha` 正向的情况，反向场景可调用 `interpolate(alpha)` 重写逻辑。

### 思考题

1. 修改 `BubbleSort` 代码，在每对比较时增加一个"比较标记"：在两个柱子之间出现一个红色感叹号，比较结束后消失。要求使用自定义 Animation 类 `ComparisonMark`，继承自 `Animation`，同时控制标记的出现和消失。提示：在 `interpolate_mobject` 中根据 `alpha` 控制标记的 `opacity`（0 到 0.5 出现，0.5 到 1 消失）。

2. 在 `RateFuncDemo` 的基础上，新增一个"物理反弹"效果：让圆点碰到右侧边界后弹回左侧。提示：组合使用 `rate_func=rush_from` 的向右运动和 `rate_func=rush_into` 的向左运动，中间用 `wait(0)` 连接。

---

### 推广计划提示

| 角色 | 本章阅读重点 | 协作事项 |
|------|-------------|----------|
| 新人开发 | 完整通读，掌握 5 种基础动画和 animate 语法 | 用 Manim 重写自己最喜欢的一个"PPT 动画" |
| 测试 | 验证自定义 Animation 的边界行为 | 编写测试确保 SwapBars 在极端值下的表现 |
| 运维 | 了解 Animation 数量对渲染速度的影响 | 收集不同动画数量的渲染耗时数据 |
