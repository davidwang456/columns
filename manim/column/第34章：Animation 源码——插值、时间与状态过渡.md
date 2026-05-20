# 第34章：Animation 源码——插值、时间与状态过渡

---

## 1. 项目背景

某 Manim 扩展开发者阿杰正在为一个教学项目实现自定义的 `TypewriterNumber` 动画——数字像打字机一样逐字出现在画面上。他需要为每个 digit 创建一个独立的动画阶段：0→1→2→...→9 逐帧变化。

如果用现成的 `Write` 或 `FadeIn` 动画，它们只负责"让对象从头出现"，无法做到"逐位变化"。阿杰必须继承 `Animation` 并重写 `interpolate` 方法。

但他翻开 `manim/animation/animation.py` 后发现，Animation 的生命周期有三步：`begin()` → 逐帧 `interpolate(alpha)` → `finish()`。alpha 从 0 到 1 由引擎驱动，`rate_func` 负责将线性的进度映射为非线性曲线。理解这三步并实现自定义 Animation 是高级篇的关键技能。

本章要系统剖析 Animation 的内部机制，并实战实现一个自定义 Animation。

---

## 2. 剧本式交锋对话

> **场景**：阿杰在 `animation.py` 的源码中打了一堆断点，逐一追踪 alpha 的变化。

**小胖**（在吃山楂条）：

"阿杰，你不是要做打字机动效吗？为啥在盯着一个叫 `alpha` 的变量发呆？那个从 0 到 1 的东西跟数字变化有什么关系？"

**阿杰**（盯着调试器）：

"alpha 就是动画的'心跳'。Animation 引擎每一帧调用 `interpolate(alpha)`，alpha 从 0 线性增长到 1。我的任务就是在 `interpolate` 里根据 alpha 算出'当前显示到哪个数字'。"

**小白**（在白板上画了一个坐标系）：

"对。Animation 的设计可以用一张时间轴来理解：

- t=0（begin）：alpha=0，原对象状态
- t=run_time/2（中间）：alpha=0.5，中间态
- t=run_time（finish）：alpha=1，目标状态

`rate_func` 控制的是"alpha 的增长曲线"。`linear` 让 alpha 匀速从 0→1，`smooth` 让它在两端减速、中间加速。不管你用什么 rate_func，`interpolate(alpha)` 只负责一件事：**用当前的 alpha 算出这一帧该画什么**。"

**大师**（翻开 `animation.py` 核心代码）：

"Animation 的三个核心方法非常简洁——"

```python
class Animation:
    def begin(self) -> None:
        """动画开始前调用一次。保存初始状态。"""
        self.starting_mobject = self.mobject.copy()

    def interpolate(self, alpha: float) -> None:
        """每帧调用一次。alpha 范围 [0, 1]。
        子类重写这个方法来定义动画的具体行为。"""
        pass  # 子类实现

    def finish(self) -> None:
        """动画结束时调用一次。清理临时状态。"""
        pass  # 子类可选重写
```

"> **技术映射**：`Animation.begin()` 在动画第一帧前调用一次，`interpolate` 每帧调用，`finish` 在最后一帧后调用。`rate_func` 在 Scene 的渲染循环中外挂——它将'当前帧的进度'映射为 alpha 后传入 `interpolate`。"

**小胖**（山楂条噎着了，喝了口水）：

"那自定义 Animation 怎么写？比如我要做一个打字机数字效果。"

**阿杰**（豁然开朗）：

"我只需要重写 `interpolate(alpha)`。alpha=0 时显示 '000'，alpha=1 时显示 '123'。中间的每一帧按比例混合：

```python
class TypewriterNumber(Animation):
    def __init__(self, number_mobject, target_value, **kwargs):
        super().__init__(number_mobject, **kwargs)
        self.target_value = target_value

    def interpolate(self, alpha):
        current = int(self.target_value * alpha)
        self.mobject.set_value(current)  # DecimalNumber 的 set_value
```

"就这么简单。alpha 驱动一切，我只管在每一帧根据 alpha 算出那个时刻的显示值。"

**大师**（补充）：

"还有一个重要细节——`AnimationGroup` 怎么调度多个子动画？它内部维护了一个 `animations` 列表，每个子动画有各自的 `run_time` 和 `rate_func`。调度时，主循环计算**全局进度**，然后为每个子动画独立调用 `interpolate(sub_alpha)`。`LaggedStart` 在此基础上增加了错位偏移——第 i 个子动画的 start_time = i × lag_ratio × sub_run_time。"

---

## 3. 项目实战

### 3.1 环境准备

沿用基础篇环境。确认可以 import animation 基类：

```python
from manim.animation.animation import Animation
```

---

### 3.2 分步实现

> **本章实战目标**：实现自定义 `TypewriterNumber` 和 `FlashBorder` 动画，深入理解 Animation 机制。

---

#### 步骤一：追踪 Animation 生命周期

**步骤目标**：用自定义子类追踪 begin / interpolate / finish 的调用时机。

```python
# scenes/chapter34_trace.py
from manim import *
import logging
logger = logging.getLogger("manim")

class DebugAnimation(Animation):
    """自定义动画——在每个生命周期节点打印日志"""
    def begin(self):
        logger.info("[DebugAnimation] begin() called")

    def interpolate(self, alpha):
        if int(alpha * 10) != int((alpha - 0.01) * 10):  # 每 10% 打印一次
            logger.info(f"[DebugAnimation] interpolate(alpha={alpha:.2f})")

    def finish(self):
        logger.info("[DebugAnimation] finish() called")

class LifecycleDemo(Scene):
    def construct(self):
        dot = Dot(color=RED)
        self.add(dot)
        # 用自定义动画（不做实际变化，只打印日志）
        self.play(DebugAnimation(dot, run_time=3))
        self.wait(1)
```

**运行结果**：终端输出类似：
```
[DebugAnimation] begin() called
[DebugAnimation] interpolate(alpha=0.00)
[DebugAnimation] interpolate(alpha=0.10)
...
[DebugAnimation] interpolate(alpha=1.00)
[DebugAnimation] finish() called
```

beta 从 0→1 被调用约 run_time × frame_rate 次（3s × 30fps = 90 次）。

---

#### 步骤二：实现 TypewriterNumber

**步骤目标**：创建一个让数字逐位增长的动画。

```python
# scenes/chapter34_typewriter.py
from manim import *
import numpy as np

class TypewriterNumber(Animation):
    """将 DecimalNumber 从当前值逐位变化到目标值"""
    def __init__(self, number_mobject, target_value, **kwargs):
        super().__init__(number_mobject, **kwargs)
        self.target_value = target_value
        # 保存起始值
        self.start_value = number_mobject.number
        self.range = target_value - self.start_value

    def interpolate(self, alpha):
        # 线性插值（或阶梯变化）
        current = self.start_value + self.range * alpha
        self.mobject.set_value(current)

class TypewriterDemo(Scene):
    def construct(self):
        title = Text("TypewriterNumber 动画", font_size=30, color=BLUE).to_edge(UP, buff=0.3)
        self.play(Write(title))

        # 创建数字显示
        number = DecimalNumber(0, num_decimal_places=0, font_size=72, color=YELLOW)
        self.add(number)

        # 打字机效果：0 逐渐增长到 2024
        self.play(TypewriterNumber(number, 2024, run_time=4, rate_func=linear))
        self.wait(0.5)

        # 回退效果
        self.play(TypewriterNumber(number, 0, run_time=2, rate_func=rush_into))
        self.wait(1)

        self.play(FadeOut(VGroup(title, number)), run_time=1)
```

**运行结果**：

一个黄色大号数字从 0 在 4 秒内线性增长到 2024，然后花 2 秒快速回退到 0。`rate_func=linear` 时数字匀速增长，`rate_func=rush_into` 时回退是先慢后快。

**关键理解**：
- `TypewriterNumber` 继承了 `Animation`，`mobject` 自动指向传入的 `DecimalNumber`。
- `interpolate(alpha)` 中 `mobject.set_value(current)` 直接修改数值，`DecimalNumber` 会自动刷新显示。
- `rate_func` 由 `play()` 外挂控制，Animation 内部完全不需要关心——它只管"接到什么 alpha 就画什么"。

---

#### 步骤三：实现 FlashBorder 动画

**步骤目标**：创建一个让对象边框闪烁的强调动画。

```python
class FlashBorder(Animation):
    """闪烁包围框：alpha 从 0→1 时边框闪现一次"""
    def __init__(self, mobject, color=YELLOW, **kwargs):
        super().__init__(mobject, **kwargs)
        self.border = SurroundingRectangle(mobject, color=color, buff=0.08)
        self.mobject.add(self.border)  # 将边框加入 mobject

    def interpolate(self, alpha):
        # alpha 0→0.5: 边框从无到有（透明度增加）
        # alpha 0.5→1: 边框从有到无（透明度减少）
        if alpha <= 0.5:
            self.border.set_opacity(alpha * 2)
        else:
            self.border.set_opacity((1 - alpha) * 2)

    def finish(self):
        # 动画结束后移除边框
        self.mobject.remove(self.border)
        super().finish()

class FlashBorderDemo(Scene):
    def construct(self):
        title = Text("FlashBorder 动画", font_size=30, color=BLUE).to_edge(UP, buff=0.3)
        text = Text("关键结论", font_size=48, color=WHITE)
        text.move_to(ORIGIN)

        self.play(Write(title), Write(text), run_time=1.5)
        self.play(FlashBorder(text, color=YELLOW, run_time=1.5))
        self.wait(1)

        self.play(FadeOut(VGroup(title, text)), run_time=1)
```

---

### 3.3 完整代码清单

```python
# scenes/chapter34_trace.py —— DebugAnimation, LifecycleDemo
# scenes/chapter34_typewriter.py —— TypewriterNumber, TypewriterDemo
# scenes/chapter34_flash.py —— FlashBorder, FlashBorderDemo
```

### 3.4 测试验证

| 验证项 | 操作 | 预期结果 |
|--------|------|----------|
| begin 调用次数 | 渲染 DebugAnimation, 统计 begin 日志 | 恰好 1 次 |
| interpolate 频率 | 30fps × 3s = 90 次 | ~90 次 |
| finish 调用 | 检查"finish() called"日志 | 1 次 |
| rate_func 生效 | 用 linear vs smooth 渲染同一动画 | alpha 增长曲线不同 |

---

#### 补充实战：实现 WaveNumber 动画

**步骤目标**：让数字像"波浪"一样变化——先超过目标值再回落。

```python
class WaveNumber(Animation):
    """波浪式数字变化：先冲到目标的 120%，再回落到 100%"""
    def __init__(self, number_mobject, target_value, overshoot=0.2, **kwargs):
        super().__init__(number_mobject, **kwargs)
        self.target_value = target_value
        self.start_value = number_mobject.number
        self.overshoot = overshoot
        self.range = target_value - self.start_value

    def interpolate(self, alpha):
        # 使用二次函数：alpha=0 时 0, alpha=0.6 时 max, alpha=1 时 1
        if alpha <= 0.6:
            t = alpha / 0.6
            factor = t * (2 - t)  # 先加速
            current = self.start_value + self.range * (1 + self.overshoot) * factor
        else:
            t = (alpha - 0.6) / 0.4
            factor = t  # 线性回落
            current = self.target_value + self.range * self.overshoot * (1 - factor)
        self.mobject.set_value(current)
```

**运行效果**：数字从 0 冲到 120%（如目标 100，先到 120），然后回落到 100。比线性增长更吸引眼球，适合展示"冲刺目标"效果。

#### 补充探索：自定义 rate_func 的实现

```python
def bounce(t):
    """弹性曲线：模拟小球弹跳"""
    if t < 0.5:
        return 2 * t * t
    else:
        return 1 - pow(-2 * t + 2, 2) / 2

def cubic_bezier(p1x, p1y, p2x, p2y):
    """三次贝塞尔曲线 rate_func 生成器（CSS easing 风格）"""
    def curve(t):
        # 用 De Casteljau 算法计算贝塞尔曲线
        return (3 * (1-t)**2 * t * p1y +
                3 * (1-t) * t**2 * p2y + t**3)
    return curve

# 使用自定义 rate_func
self.play(obj.animate(rate_func=bounce).shift(UP * 3), run_time=2)
```

---

## 4. 项目总结

### 优点 & 缺点

| 维度 | 优点 | 缺点 |
|------|------|------|
| Animation 设计 | begin/interpolate/finish 三个方法简洁清晰 | 没有"暂停/恢复"机制 |
| alpha 驱动 | 所有动画统一为 0→1 的归一化进度 | 不支持非线性时间轴（如弹性/回弹） |
| rate_func 外挂 | 动画逻辑和曲线逻辑解耦 | rate_func 只映射 [0,1]→[0,1]，无导数信息 |
| 自定义门槛 | 继承 Animation + 重写 interpolate 即可 | 需要手动管理 mobject 的状态保存/恢复 |
| AnimationGroup | 支持嵌套/串行/并行/错位 | 大嵌套（>3 层）性能下降明显 |

### 适用场景

| 场景 | 说明 |
|------|------|
| 自定义动画 | TypewriterNumber、FlashBorder 等 |
| 数据驱动动画 | 根据数值计算结果生成动画 |
| 物理模拟 | 用物理公式计算每帧位置 |
| 渐变特效 | 透明度闪烁、颜色渐变 |
| 教学工具 | 制作"逐步揭示"式动画 |

### 注意事项

1. **`begin()` 中保存的 `starting_mobject` 是深拷贝**：确保动画可以从初始状态正确恢复。如果 mobject 很大（如含上千个子对象），深拷贝开销显著。
2. **`interpolate` 中的 `alpha` 是 rate_func 映射后的值**：动画引擎在调用 `interpolate` 前已经应用了 `rate_func`。不要在你自己的 `interpolate` 中再次应用。
3. **`finish` 中释放资源**：如果在 `begin` 或 `interpolate` 中创建了额外的 Mobject，在 `finish` 中清理。

### 常见踩坑经验

**故障一：自定义动画播放后 mobject 停留在中间状态**

根因：`finish` 中没有将 mobject 设置到最终状态。

解决：在 `finish` 中调用 `self.interpolate(1.0)` 确保 mobject 到达目标状态。

**故障二：AnimationGroup 中自定义动画的 run_time 被忽略**

根因：AnimationGroup 的 `run_time` 会覆盖子动画的 `run_time`。

解决：在 `self.play(AnimationGroup(anim1, anim2, run_time=3))` 中，不要给 `anim1` 和 `anim2` 单独设 `run_time`。

**故障三：`interpolate` 中的 alpha 不是递增的**

根因：`rate_func` 可以是 `there_and_back` 这种"先去再回"的曲线，alpha 会从 0→1→0。

解决：在 `interpolate` 中正确处理 alpha 的来回情况，或用 `np.abs` 等做对称处理。

**故障四：`begin` 中的 `starting_mobject.copy()` 深拷贝了不必要的数据**

根因：如果 mobject 有上千个 submobjects，`copy()` 耗时可超过动画本身的播放时间。

解决：在 `begin` 中只保存最小必要的状态（如 `self.start_pos = mobject.get_center().copy()`），而非全量 copy。

**故障五：自定义动画结束后 Scene 中残留临时 Mobject**

根因：在 `begin` 或 `interpolate` 中创建了临时 Mobject（如标注框），但忘了在 `finish` 中从 Scene 移除。

解决：在 `finish` 中 `self.mobject.remove(temp_obj)` 或 `self.mobject.remove_from_scene(temp_obj, scene)`。

### 思考题

1. 实现一个 `class BounceNumber(Animation)`：让数字从 0 跳到 100，但不是线性增长——每到整十数时停顿 0.1 秒，然后"跳"到下一个十位。提示：在 `interpolate` 中实现阶梯函数 `alpha → floor(alpha * 10)`。

2. 分析 `Transform` 的源码（`manim/animation/transform.py`），理解它是如何保存起始状态的。然后实现一个自定义的 `MorphTo` 动画——仅 morph 形状不改变颜色。提示：继承 `Transform`，在 `begin` 中保存原有颜色，在 `interpolate` 中 morph shape 后恢复颜色。

---

### 推广计划提示

| 角色 | 本章阅读重点 | 协作事项 |
|------|-------------|----------|
| 架构师/资深开发 | 深入 Animation 生命周期 | 设计自定义动画的复用模板 |
| 核心开发 | 掌握 interpolate 和 rate_func | 开发团队专用动画效果库 |
| 测试 | 验证 begin/finish 的调用时机 | 编写动画生命周期测试 |
