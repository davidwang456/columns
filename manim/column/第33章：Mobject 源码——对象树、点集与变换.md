# 第33章：Mobject 源码——对象树、点集与变换

---

## 1. 项目背景

某 Manim 插件开发者阿木正在实现一个自定义的可视化组件——`PolylineMobject`，需要在已有折线的基础上支持动态添加顶点、局部着色和路径长度统计。他要深入理解 Mobject 的核心数据结构和变换机制。

但他翻开 `manim/mobject/mobject.py` 后，面对 2000+ 行代码和十几个内部方法，不知道该从哪个字段开始理解。更关键的是——他需要区分"Mobject 自身的属性"和"继承/传递给 submobjects 的属性"，因为前者影响单个对象，后者会递归影响整个对象树。

本章要深入剖析 Mobject 的三个核心层面：
1. **数据层**：`points`（贝塞尔点集）、`submobjects`（子对象列表）、`updaters`（帧更新函数）
2. **变换层**：`shift`/`scale`/`rotate` 如何通过变换矩阵影响 `points`
3. **继承层**：`copy`/`become`/`family` 的对象树操作机制

---

## 2. 剧本式交锋对话

> **场景**：阿木的屏幕上，左侧是 `mobject.py` 的源码，右侧是自己的 `PolylineMobject` 实现——部分顶点颜色不对。

**小胖**（抱着一袋栗子）：

"阿木你这个折线有点诡异——前半段是蓝色的，后半段突然变红了。你是给整个折线 set_color 了，还是给某个顶点单独特色？"

**阿木**（盯着源码）：

"问题就在这——我给折线的第 3 个顶点单独设了 `set_color(RED)`，但它好像把整条折线的颜色都改了。我以为 Mobject 支持逐顶点着色。"

**小白**（翻到 `VMobject` 的 `set_stroke` 实现）：

"你的理解有一个关键误解——Mobject 的 `points` 数组中，每条贝塞尔曲线共享**同一种颜色**。`set_color` 修改的是 Mobject 级别的 `stroke_color` 和 `fill_color`，不是每个控制点的颜色。如果你想要逐段不同颜色的折线，你需要创建多个独立的 `VMobject`，每个表示一段并用不同颜色，然后放进 VGroup。"

**大师**（在白板上画出 Mobject 的数据结构）：

"Mobject 的核心数据结构只有三样东西：

1. **`points`**：`np.ndarray (n, 3)`。每一行是一个 3D 坐标点。对于圆形，它是 4 条贝塞尔曲线 × 每段 4 个控制点 = 16 个点。对于 Line，它是 2 个端点。`set_points_as_corners` 将你的顶点列表转换为一系列的直线段。

2. **`submobjects`**：`List[Mobject]`。子对象列表。当你 `group.add(circle, square)` 时，`circle` 和 `square` 被加入 `group.submobjects`。当 `group` 被 `shift`/`scale`/`rotate` 时，变换会**递归**应用到所有子对象的 `points`。

3. **`updaters`**：`List[Callable]`。一个函数列表。Scene 在每帧渲染前会逐个调用这些函数，传入 `dt` 参数。"

> **技术映射**：`Mobject.points` 是 `np.float64` 的 N×3 数组。`shift(vector)` 本质是 `points += vector` 的矩阵运算。`scale(factor)` 是 `points *= factor` 以对象中心为原点。

**小胖**（栗子壳掉了一腿）：

"那 `copy` 和 `become` 有什么区别？我经常搞混。"

**大师**（在白板上画了两个状态图）：

"`copy()` → **深拷贝整个对象树**。包括 `points`、`submobjects`（递归拷贝）、`color`、`stroke_width` 等所有属性。返回一个完全独立的对象，修改它不影响原对象。

`become(other)` → **把当前对象的视觉属性变成 other 的样子**。只拷贝 `points`、`color`、`opacity` 等渲染相关的属性，**不改变** `submobjects` 列表。这意味着如果你 `circle.become(square)`，`circle` 的形状和颜色变成了方形，但它原有的子对象列表保持不变。

`Transform(a, b)` → **Animation 版**：在动画开始前 `a` 保存当前状态，然后逐渐 `interpolate` 到 `b` 的状态。"

---

## 3. 项目实战

### 3.1 环境准备

沿用基础篇环境。确认可以 import manim 内部模块：

```python
from manim.mobject.mobject import Mobject
from manim.mobject.types.vectorized_mobject import VMobject
```

---

### 3.2 分步实现

> **本章实战目标**：实现一个自定义 `PolylineMobject`，支持动态添加顶点、局部路径颜色和长度统计。

---

#### 步骤一：探索 Mobject 的数据结构

**步骤目标**：用诊断代码探测 Mobject 内部状态。

```python
# scenes/chapter33_explore.py
from manim import *

class ExploreMobject(Scene):
    def construct(self):
        circle = Circle(radius=1, color=YELLOW, fill_opacity=0.3)
        square = Square(side_length=2, color=BLUE, fill_opacity=0.3)

        group = VGroup(circle, square)
        group.arrange(RIGHT, buff=0.5)

        # 1. 探索 points
        print(f"=== circle.points ===")
        print(f"shape: {circle.points.shape}")  # (16, 3) — 4 贝塞尔 × 4 控制点
        print(f"dtype: {circle.points.dtype}")  # float64
        print(f"first point: {circle.points[0]}")

        # 2. 探索 submobjects
        print(f"\n=== group.submobjects ===")
        print(f"count: {len(group.submobjects)}")  # 2
        print(f"[0] is circle: {group.submobjects[0] is circle}")  # True

        # 3. 探索 family
        full_tree = group.family_members_with_points()
        print(f"\n=== family members ===")
        print(f"count: {len(full_tree)}")  # 3 (group + 2 submobjects)
        for m in full_tree:
            print(f"  {type(m).__name__}: {len(m.points)} points")

        # 4. 探索 copy vs become
        circle_copy = circle.copy()
        circle_copy.set_color(RED).shift(UP * 2)
        print(f"\n=== copy ===")
        print(f"original color: {circle.get_color()}")
        print(f"copy color: {circle_copy.get_color()}")
        print(f"is same object: {circle is circle_copy}")  # False

        self.add(group, circle_copy)
        self.wait(2)
```

**运行结果**：终端打印出 Circle 有 16 个点（4 条贝塞尔曲线，每条 4 个控制点），VGroup 的 submobjects 包含两个子对象，`family_members_with_points` 返回整棵对象树。

---

#### 步骤二：实现 PolylineMobject

**步骤目标**：创建一个自定义 Mobject，封装折线的动态管理。

```python
# manim_components/polyline.py
from manim import *
import numpy as np

class PolylineMobject(VMobject):
    """自定义折线对象，支持动态添加顶点"""
    def __init__(self, vertices=None, **kwargs):
        super().__init__(**kwargs)
        self._vertices = []
        if vertices:
            for v in vertices:
                self.add_vertex(v)

    def add_vertex(self, point):
        """添加一个顶点"""
        self._vertices.append(np.array(point))
        self._rebuild()

    def remove_vertex(self, index):
        """移除指定索引的顶点"""
        if 0 <= index < len(self._vertices):
            self._vertices.pop(index)
            self._rebuild()

    def _rebuild(self):
        """根据 _vertices 重建贝塞尔点集"""
        if len(self._vertices) < 2:
            self.points = np.zeros((0, 3))
            return
        self.set_points_as_corners(self._vertices)

    @property
    def path_length(self):
        """计算折线总长度（欧几里得距离之和）"""
        if len(self._vertices) < 2:
            return 0.0
        total = 0.0
        for i in range(len(self._vertices) - 1):
            total += np.linalg.norm(self._vertices[i+1] - self._vertices[i])
        return total


class PolylineDemo(Scene):
    def construct(self):
        title = Text("自定义 PolylineMobject", font_size=30, color=BLUE).to_edge(UP, buff=0.3)
        self.play(Write(title))

        # 创建折线
        poly = PolylineMobject(
            vertices=[LEFT * 4, LEFT * 2 + UP, ORIGIN, RIGHT * 2 + DOWN, RIGHT * 4],
            color=YELLOW, stroke_width=3,
        )
        self.play(Create(poly), run_time=2)
        self.wait(0.5)

        # 动态添加顶点
        poly.add_vertex(RIGHT * 5 + UP * 1.5)
        self.play(
            poly.animate.set_color(ORANGE),
            run_time=1,
        )
        self.wait(0.5)

        # 显示路径长度
        length_text = Text(
            f"路径长度: {poly.path_length:.2f}",
            font_size=24, color=GREEN,
        ).to_edge(DOWN, buff=0.5)
        self.play(Write(length_text), run_time=1)
        self.wait(1.5)

        # 移除最后一个顶点
        poly.remove_vertex(-1)
        self.play(
            poly.animate.set_color(YELLOW),
            FadeOut(length_text),
            run_time=1,
        )
        self.wait(1)

        self.play(FadeOut(VGroup(title, poly)), run_time=1.5)
```

**运行结果**：

折线在画面中从左到右绘制，经过 5 个顶点。动态添加第 6 个顶点后折线延伸并变橙色，显示总长度。移除最后一个顶点后恢复黄色。

**关键理解**：
- `self.set_points_as_corners(vertices)` 将顶点列表转换为 VMobject 的贝塞尔点集。
- 覆盖 `_rebuild()` 方法每次修改顶点后重建点集。
- `np.linalg.norm` 计算路径段长度。

---

#### 步骤三：理解变换矩阵

**步骤目标**：验证 `shift`/`scale`/`rotate` 对 points 的实际影响。

```python
class TransformInternals(Scene):
    def construct(self):
        line = Line(LEFT * 3, RIGHT * 3, color=YELLOW, stroke_width=3)
        line.move_to(UP * 2)

        self.add(line)

        # 打印原始点
        orig_points = line.points.copy()
        print(f"Original points:\n{orig_points[:4]}")

        # shift 的效果
        line.shift(DOWN * 2)
        print(f"\nAfter shift DOWN*2:")
        print(f"points diff: {(line.points - orig_points)[:4]}")

        # scale 的效果
        line.scale(0.5)
        print(f"\nAfter scale(0.5):")
        print(f"points min/max: {line.points[:,0].min():.2f}, {line.points[:,0].max():.2f}")

        self.wait(2)
```

**运行结果**：终端展示 shift 后所有点增加了 (0, -2, 0) 的偏移，scale(0.5) 后 x 范围从 [-3, 3] 变为 [-1.5, 1.5]。

---

### 3.3 完整代码清单

```python
# manim_components/polyline.py —— PolylineMobject
# scenes/chapter33_explore.py —— ExploreMobject, PolylineDemo
```

### 3.4 测试验证

| 验证项 | 操作 | 预期结果 |
|--------|------|----------|
| points 结构 | `print(circle.points.shape)` | (16, 3) |
| submobjects | `print(len(group.submobjects))` | 等于 add 的子对象数 |
| copy 独立性 | `copy.set_color(RED)` 后检查原对象 | 原对象颜色不变 |
| 变换矩阵 | shift(UP*2) 后打印 points 差 | 全部点 Y 坐标 +2 |
| 路径长度 | `poly.path_length` 随顶点增加而增长 | 符合几何规律 |

---

#### 补充实战：实现 GradientPolyline —— 逐段渐变色折线

**步骤目标**：扩展 PolylineMobject，支持逐段不同颜色。

```python
class GradientPolyline(VGroup):
    """逐段着色折线——每两个相邻顶点间为一段独立 VMobject"""
    def __init__(self, vertices=None, colors=None, **kwargs):
        super().__init__(**kwargs)
        self._vertices = list(vertices or [])
        self._colors = list(colors or [])
        self._segments = []
        self._rebuild()

    def _rebuild(self):
        # 清除旧段
        for seg in self._segments:
            self.remove(seg)
        self._segments.clear()

        # 为每两个相邻顶点创建一段直线
        for i in range(len(self._vertices) - 1):
            seg = Line(
                self._vertices[i], self._vertices[i + 1],
                color=self._colors[i] if i < len(self._colors) else WHITE,
                stroke_width=3,
            )
            self._segments.append(seg)
            self.add(seg)

    def add_vertex(self, point, color=None):
        self._vertices.append(np.array(point))
        if color:
            self._colors.append(color)
        self._rebuild()
```

**关键理解**：逐段着色折线不能用单个 VMobject（单 VMobject 共享颜色），必须用 VGroup 包装多个 Line 段。这是"对象模型"对"渲染模型"的根本限制。

#### 补充探索：Mobject 的 `z_index` 和渲染顺序

```
# z_index 控制渲染顺序（值越大越靠前）
background = Circle(radius=3, color=GRAY, fill_opacity=0.1).set_z_index(0)
midground  = Square(side_length=2, color=BLUE, fill_opacity=0.3).set_z_index(1)
foreground = Dot(color=RED).set_z_index(2)

# Camera 在 capture_mobjects 时按 z_index 升序渲染
# z_index 相同的对象按 scene.mobjects 中的顺序渲染
```

> **技术映射**：`Camera.display_multiple_vectorized_mobjects()` 在渲染前对 `scene.mobjects` 按 `z_index` 排序。

---

## 4. 项目总结

### 优点 & 缺点

| 维度 | 优点 | 缺点 |
|------|------|------|
| points 数组 | numpy 向量化运算高效，shift/scale 是 O(n) 矩阵操作 | 逐顶点着色不支持，需分段创建 Mobject |
| 对象树 | submobjects 递归变换，编组后整体操作方便 | 深拷贝开销大（递归复制整棵树） |
| 变换矩阵 | Mobject 内部维护变换矩阵，叠加变换自动组合 | 矩阵不可直接读写，需通过 shift/scale/rotate |
| 扩展性 | 继承 VMobject → 实现自定义图形 | 需要深入理解贝塞尔点集结构 |

### 适用场景

| 场景 | 说明 |
|------|------|
| 自定义图形 | Polyline、GradientBar、自定义图标 |
| 图形编辑 | 需要动态增删顶点的图形工具 |
| 数据分析 | 读取外部数据后生成动态几何 |
| 性能优化 | 合批多个简单图形到单个 VMobject |
| 源码研究 | 理解 Manim 对象模型 |

### 注意事项

1. **`points` 的数组格式不可手动修改**：修改 `mob.points = ...` 后需调用 `mob.refresh_bounding_box()` 更新包围盒。
2. **`set_points_as_corners` 创建的路径不支持 `fill_opacity`**：只有闭合路径才支持填充。折线需要 `stroke_width > 0`。
3. **子对象变换是引用传递**：`group.scale(2)` 会直接修改子对象的 points。如果子对象被多个 VGroup 共享，可能产生意外副作用。

### 常见踩坑经验

**故障一：自定义 Mobject 渲染后不可见**

根因：`points` 数组为空（`np.zeros((0, 3))`），或 stroke_width=0 且 fill_opacity=0。

解决：确保 `_rebuild()` 后 `points` 非空，且至少设置了 `stroke_width` 或 `fill_opacity`。

**故障二：`family_members_with_points()` 返回顺序非预期**

根因：返回顺序是深度优先遍历，不是层级遍历。

解决：如需按层级操作，手动遍历 `submobjects` 列表。

**故障三：`become` 后的对象丢失了原有的 submobjects**

根因：`become` 只拷贝视觉属性，submobjects 列表不受影响。但如果之后调用了 `set_points_as_corners`，submobjects 可能被覆盖。

解决：在自定义 Mobject 的 `_rebuild` 中避免操作 `submobjects`。

### 思考题

1. 扩展 `PolylineMobject`，支持**分段着色**：不同路径段使用不同颜色。提示：为每个颜色区段创建独立的 VMobject，然后所有段放在一个 VGroup 中，VGroup 对外暴露统一的 `add_vertex` 接口。

2. 实现一个 `class GradientLine(VMobject)`：从起点到终点颜色平滑过渡（不是用颜色渐变常量，而是手动在 `points` 之间创建多个短段，每段用 `interpolate_color(c1, c2, t)` 着色）。

---

### 推广计划提示

| 角色 | 本章阅读重点 | 协作事项 |
|------|-------------|----------|
| 架构师/资深开发 | 深入 Mobject 数据结构 | 设计自定义 Mobject 的团队规范 |
| 核心开发 | 掌握 transform 和 submobjects 机制 | 开发团队专用图形组件 |
| 测试 | 验证 copy/become/transform 的独立性 | 编写对象状态变更测试 |
