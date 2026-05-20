# 第18章：Updater 与动态响应式画面

---

## 1. 项目背景

某物理教学团队在用 Manim 制作"简谐运动"系列动画。助教小周负责实现"弹簧振子"场景——一个重物挂在弹簧上上下振动，同时侧面需要显示速度箭头、位移标尺和能量柱状图。这些辅助元素必须**实时跟随重物的运动**而变化。

小周的第一版实现是这样的：重物每移动一段距离，他就写一段代码更新速度箭头、位移标尺和能量柱的位置。结果代码又臭又长——每一帧的变化都要手动写 `play` 语句来"推"画面，而不是让画面自动"跟"着重物。

更严重的是，当他想调整振动的振幅时，所有的箭头、标尺更新逻辑都要跟着改。他意识到自己写的是"命令式逐帧更新"——每步都靠人力维护，而不是"声明式响应式绑定"。

这个痛点正是 `Updater` 要解决的核心问题。Updater 是 Manim 的**被动式动画机制**——你注册一个函数到 Mobject 上，Manim 在每一帧都会自动调用这个函数来更新该对象的状态。与 Animation 的"主动推"不同，Updater 是"被动拉"——对象"观察"某个状态，状态变了它自动跟上。

---

## 2. 剧本式交锋对话

> **场景**：小周的代码有 300 行全是位置更新逻辑。小胖在吃薯片。

**小胖**（往嘴里塞了一把薯片）：

"小周你这个代码像春运调度的火车时刻表——重物动一格，箭头跟着动一格，柱子跳一格，标尺刷一格。要是重物跳个舞，你是不是还得写 1000 行更新代码？"

**小周**（苦笑）：

"我也觉得不对。但 Manim 不就是一行行 `play` 吗？难道还有别的办法让箭头自动跟着重物跑？"

**小白**（调出一个只有 10 行的示例）：

"当然有。给你看一段代码——"

```python
weight = Circle(radius=0.3, color=RED)
arrow = Arrow(ORIGIN, UP)
arrow.add_updater(lambda a: a.put_start_and_end_on(
    ORIGIN, weight.get_center()
))
self.play(weight.animate.shift(UP * 3), run_time=2)
# 箭头自动跟随！无需手动更新箭头位置
```

"这就是 `add_updater`。你给箭头注册了一个函数——'每一帧，把你的起点放在原点，终点放在 weight 的位置'。之后每次 weight 移动（通过 play 或任意方式），箭头自动跟上。你不用推它，它自己会跟。"

**大师**（接过话题）：

"小白，你给了一个精彩的切面。Updater 的哲学是——**让对象之间的关系声明出来，而不是手动维护**。这和 React 的响应式绑定、Excel 的公式计算异曲同工：

- 弹簧振子场景：重物位置变化 → 箭头长度变化 → 能量柱高度变化。这条因果链用 Updater 就是三个 `add_updater` 声明，不用一个 `play`。
- Animation vs Updater：Animation 是事件驱动（'在第 N 秒做某件事'），Updater 是数据驱动（'当某个值变了，某样东西随之变'）。"

> **技术映射**：`Mobject.add_updater(func)` 将 `func` 加入对象的 `updaters` 列表。Scene 在每帧渲染前会遍历所有对象的 `updaters` 并调用，传入 `dt`（上一帧到当前帧的时间间隔）。

**小胖**（嘴里的薯片还没嚼完）：

"等等——如果 Updater 每一帧都在跑，是不是很耗性能？我要是注册了 100 个 updater，渲染岂不是要卡死？"

**小白**：

"好问题，Updater 确实有性能代价。它的执行时间是**O(updater 数量 × 每帧更新复杂度)**。经验法则：

- **5 个以内的简单 updater**（改坐标、改颜色）——几乎不影响帧率。
- **10-20 个中等 updater**（含 LaTeX 重建）——60fps 开始吃力。
- **50+ 个 updater 或含数值积分**——建议切换到 `always_redraw` 的批量模式，或降低帧率。"

**大师**：

"另外提醒——Updater 不是'越多越好'的神器。它最适合三种场景：

1. **连接线**：箭头、连线跟随两个移动的对象
2. **动态标签**：数值标签跟随 tracker 的值
3. **视觉效果**：尾迹、阴影、投影跟随主对象

当你发现自己在 `play` 里写很多 `obj.animate.set_xxx` 来同步多个对象时，想一想——这能不能用 Updater 来声明式绑定？"

---

## 3. 项目实战

### 3.1 环境准备

沿用基础篇环境。本章无额外依赖。

---

### 3.2 分步实现

> **本章实战目标**：制作一个"弹簧振子"物理动画，展示 Updater 驱动的连接线、动态箭头和能量柱的响应式更新。

---

#### 步骤一：基础 Updater 入门

**步骤目标**：理解 `add_updater` / `clear_updaters` / `dt` 参数的用法。

```python
# scenes/chapter18_basics.py
from manim import *

class UpdaterBasics(Scene):
    def construct(self):
        # 1. 简单跟随：箭头跟随圆
        circle = Circle(radius=0.3, color=RED, fill_opacity=0.5)
        circle.move_to(LEFT * 3)

        # 创建箭头，起点固定在原点，终点跟随 circle
        arrow = Arrow(ORIGIN, circle.get_center(), color=YELLOW, buff=0)
        arrow.add_updater(lambda a: a.put_start_and_end_on(
            ORIGIN, circle.get_center()
        ))

        self.play(FadeIn(circle), FadeIn(arrow), run_time=1)
        self.wait(0.3)

        # 圆移动 → 箭头自动跟随（无需手动更新箭头）
        self.play(circle.animate.shift(RIGHT * 4 + UP * 2), run_time=2)
        self.wait(0.5)
        self.play(circle.animate.shift(DOWN * 2 + LEFT * 2), run_time=2)
        self.wait(0.5)

        # 2. dt 参数：每帧时间间隔驱动运动
        circle.clear_updaters()
        arrow.clear_updaters()

        # 给圆注册一个每帧向右移动的 updater
        def move_right(mob, dt):
            mob.shift(RIGHT * dt * 2)  # 每秒移动 2 单位
        circle.add_updater(move_right)

        # 箭头继续跟随
        arrow.add_updater(lambda a: a.put_start_and_end_on(
            a.get_start(), circle.get_center()
        ))

        self.play(circle.animate.move_to(LEFT * 3), run_time=0.5)
        # 圆自动匀速向右移动 3 秒
        self.wait(3)

        circle.clear_updaters()
        arrow.clear_updaters()
        self.play(FadeOut(circle), FadeOut(arrow), run_time=1)
```

**运行结果**：

第一段：箭头跟随圆在画面中移动，圆走到哪箭头跟到哪，无需手动写箭头更新代码。第二段：圆注册了一个"每帧向右漂移"的 updater，等待 3 秒期间圆持续向右移动，箭头自动跟随。展示了 `dt` 参数与物理时间的关系。

---

#### 步骤二：弹簧振子动画

**步骤目标**：用 Updater 实现重物振动、弹簧伸缩、速度箭头和能量柱的实时联动。

```python
# scenes/chapter18_spring.py
from manim import *
import numpy as np

class SpringOscillator(Scene):
    def construct(self):
        # ---- 坐标系 ----
        axes = Axes(
            x_range=[0, 1, 1], y_range=[-4, 4, 1],
            x_length=1, y_length=7,
            axis_config={"color": BLUE, "include_tip": False},
        )
        axes.to_edge(LEFT, buff=1.5)
        self.play(Create(axes), run_time=1)

        # ---- 弹簧和重物 ----
        spring_top = UP * 3
        weight = Circle(radius=0.3, color=RED, fill_opacity=0.8)
        weight.move_to(spring_top + DOWN * 2)

        # 弹簧：一条锯齿折线来模拟
        def get_spring():
            n_coils = 12
            bottom = weight.get_center()
            points = [spring_top]
            for i in range(n_coils):
                t = (i + 1) / (n_coils + 1)
                x_off = 0.3 if i % 2 == 0 else -0.3
                y = spring_top[1] * (1 - t) + bottom[1] * t
                points.append(np.array([x_off, y, 0]))
            points.append(bottom)
            return VMobject().set_points_as_corners(points).set_color(GRAY).set_stroke(width=2)

        spring = always_redraw(get_spring)
        self.add(spring, weight)

        # ---- 速度箭头（动态跟随） ----
        vel_arrow = Arrow(weight.get_center(),
                          weight.get_center() + RIGHT * 1.5,
                          color=YELLOW, stroke_width=3, buff=0)
        vel_label = Text("v", font_size=22, color=YELLOW)
        vel_label.next_to(vel_arrow.get_end(), RIGHT, buff=0.1)

        vel_arrow.add_updater(lambda a: a.put_start_and_end_on(
            weight.get_center(),
            weight.get_center() + RIGHT * (weight.get_center()[1] - ORIGIN[1]) * 0.5
        ))
        vel_label.add_updater(lambda l: l.next_to(vel_arrow.get_end(), RIGHT, buff=0.1))

        self.play(Create(vel_arrow), Write(vel_label), run_time=1)

        # ---- 能量柱状图 ----
        def get_energy_bars():
            y_pos = weight.get_center()[1]
            ke = 0.5 * (y_pos) ** 2  # 简化动能
            pe = 0.5 * (3 - y_pos) ** 2  # 简化势能

            ke_bar = Rectangle(
                width=0.5, height=ke * 0.5,
                color=YELLOW, fill_opacity=0.7, stroke_width=0,
            )
            pe_bar = Rectangle(
                width=0.5, height=pe * 0.5,
                color=GREEN, fill_opacity=0.7, stroke_width=0,
            )
            ke_bar.shift(RIGHT * 4 + DOWN * 2)
            pe_bar.next_to(ke_bar, RIGHT, buff=0.2)

            ke_label = Text("动能", font_size=16, color=YELLOW).next_to(ke_bar, DOWN, buff=0.1)
            pe_label = Text("势能", font_size=16, color=GREEN).next_to(pe_bar, DOWN, buff=0.1)
            return VGroup(ke_bar, pe_bar, ke_label, pe_label)

        energy_bars = always_redraw(get_energy_bars)
        self.add(energy_bars)
        self.play(FadeIn(energy_bars), run_time=1)
        self.wait(0.5)

        # ---- 仿真：重物做简谐运动 ----
        # 用 Updater 模拟运动方程
        time_tracker = ValueTracker(0)

        def update_weight(mob, dt):
            # 不使用 dt，改用显式的正弦运动
            pass  # 由 play 控制

        weight.clear_updaters()
        # 让 weight 做正弦运动
        weight.add_updater(lambda m, dt: m.move_to(
            spring_top + DOWN * (2 + 1.5 * np.sin(time_tracker.get_value()))
        ))

        # 标题
        title = Text("弹簧振子——简谐运动", font_size=32, color=BLUE)
        title.to_edge(UP, buff=0.4)
        self.play(Write(title))

        # 模拟 2 个周期的振动
        self.play(
            time_tracker.animate.set_value(4 * PI),
            run_time=4,
            rate_func=linear,
        )
        self.wait(0.5)

        # 清理
        weight.clear_updaters()
        time_tracker.clear_updaters()
        self.play(FadeOut(VGroup(axes, spring, weight, vel_arrow, vel_label,
                                  energy_bars, title)), run_time=2)
```

**运行命令**：

```bash
manim -pqm scenes/chapter18_spring.py SpringOscillator
```

**运行结果**：

约 10 秒的物理模拟动画。重物在弹簧下方做正弦振动（通过 `time_tracker` + Updater 驱动），弹簧随重物实时伸缩（`always_redraw` 重建折线），黄色速度箭头长度和方向实时改变（Updater 跟随），右侧动能/势能柱状图高度随位置实时变化（`always_redraw` 重建）。四个元素全部声明式绑定，无需逐帧手动更新。

**可能遇到的坑**：

1. **`always_redraw` 和 `add_updater` 的配合**：`always_redraw` 本质上就是在内部给一个 VGroup 注册了 updater。两者可以配合，但注意 `always_redraw` 每次重建对象时会丢弃之前的 updater 绑定。
2. **`dt` 的精确性**：`dt` 是真实世界的帧间隔（取决于渲染 FPS）。如果依赖 `dt` 做物理积分，帧率波动会导致模拟不一致。建议用显式的 `ValueTracker` + `set_value` 控制时间。
3. **Updater 的调试**：在 updater 函数中 `print` 会每一帧输出，终端瞬间刷爆。建议只在 updater 中累加计数器，每 60 帧打印一次。

---

### 3.3 完整代码清单

```python
# scenes/chapter18_basics.py —— UpdaterBasics
# scenes/chapter18_spring.py —— SpringOscillator
```

### 3.4 测试验证

| 验证项 | 操作 | 预期结果 |
|--------|------|----------|
| 跟随性 | 注册 updater 后移动目标对象 | 跟随对象位置实时更新 |
| dt 响应 | 设置 `wait(2)` 期间 updater 持续运行 | 对象按 dt 速度移动 |
| 清理 | `clear_updaters()` 后 `wait(2)` | 对象停止自动运动 |
| 性能 | 10 个 always_redraw + 60fps | 帧率不低于 30fps |

---

## 4. 项目总结

### 优点 & 缺点

| 维度 | 优点 | 缺点 |
|------|------|------|
| 声明式绑定 | 一个 updater 替代表达多条 play 更新逻辑 | 过度使用后依赖链不直观 |
| dt 驱动 | 适合物理模拟和时间积分 | dt 波动可能导致模拟不一致 |
| always_redraw | 适用于任何需要"每帧重建"的视觉元素 | 性能开销大，含 LaTeX 时尤甚 |
| 动态响应 | 动画期间可实时改变绑定关系 | 调试困难（无断点、无单步执行） |
| 解耦能力 | 视觉元素和数学模型解耦 | 多层依赖时 upater 的执行顺序不可控 |

### 适用场景

| 场景 | 说明 |
|------|------|
| 物理模拟 | 弹簧、摆锤、粒子系统 |
| 实时数据 | 动态图表、仪表盘 |
| 连接线 | 流动线条、关系箭头 |
| 视觉效果 | 拖尾、阴影、光晕 |
| 交互反馈 | 参数调节时的实时预览 |

**不适用场景**：固定节奏的叙事动画（用 Animation 更直观）、需要精确帧级同步的逐帧动画（用 Animation + run_time）。

### 注意事项

1. **Updater 的执行时机**：Updater 在每帧的 Animation 插值**之前**执行。如果 updater 修改了 Animation 正在处理的对象属性，两者可能冲突。
2. **`clear_updaters` 的选择性**：`mob.clear_updaters()` 清除所有 updater。如果只想清除部分，用 `mob.remove_updater(func_ref)` 传入具体函数引用。
3. **闭包陷阱**：updater 的 lambda 同样存在闭包捕获引用的经典问题，确保使用 `lambda m, dt, val=val: ...` 捕获快照值。

### 常见踩坑经验

**故障一：updater 注册后对象不动**

根因：updater 注册在了错误的对象上——需要跟随的是箭头，但 updater 加到了圆上。

解决：确定"谁跟随谁"——跟随者（箭头）注册 updater，被跟随者（圆）不需要。

**故障二：`always_redraw` 中的对象闪烁**

根因：每帧重建对象导致 LaTeX 重复编译。

解决：将 `MathTex` 等重型对象提取到 `always_redraw` 外部，只重建轻量的 VMobject。

**故障三：多个 updater 的执行顺序导致状态不一致**

根因：Manim 按对象被添加的顺序执行 updater，但不同对象间的依赖关系可能被打乱。

解决：将所有相关逻辑合并到一个 `always_redraw` 中，保证单线程内的顺序一致。

### 思考题

1. 改造 `SpringOscillator`，增加阻尼效果：重物的振幅随时间指数衰减（`np.sin(t) * np.exp(-0.3 * t)`），并在画面左下角显示当前的能量损耗（总能量从 100% 逐渐降低）。

2. 实现一个"鼠标跟随"效果：注册一个 updater，让一个圆点根据 `ValueTracker` 的值移动到指定坐标。然后用一段循环演示圆点走过"MANIM"五个字母的轮廓路径。提示：将字母路径存为坐标列表，tracker 轮流转到每个坐标。

---

### 推广计划提示

| 角色 | 本章阅读重点 | 协作事项 |
|------|-------------|----------|
| 新人开发 | 掌握 add_updater / always_redraw / dt | 完成弹簧振子动画 |
| 测试 | 验证 updater 的调用次数和时序 | 编写 updater 覆盖率测试 |
| 运维 | 关注 always_redraw 的渲染性能 | 统计不同 updater 密度下的帧率 |
