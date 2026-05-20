# 第9章：图像、SVG 与外部素材管理

---

## 1. 项目背景

一家科技媒体的编辑团队正在用 Manim 制作一个"产品功能讲解"系列短视频。每一集需要展示一张公司 Logo、几个图标箭头，以及一张产品截图。编辑小陈负责把这些素材整合进 Manim 动画中。

小陈很快遇到了三个问题：

1. **Logo 是位图（PNG）**，放进画面后面画质模糊，放大后锯齿严重。她用 `ImageMobject` 加载了 PNG，想在旁边加一个箭头指向它——但 `next_to` 定位完全对不上，因为 `ImageMobject` 的宽高是像素尺寸，而她不知道它在画面上占了几个单位。

2. **图标是 SVG 格式**，理论上可以无限放大。但有些 SVG 加载失败——Manim 报 `ValueError: Unsupported SVG element`。原因是公司设计师用的 SVG 包含渐变滤镜和复杂裁剪路径，Manim 的 SVG 解析器不支持。

3. **素材散落各处**——Logo 在桌面，图标在下载文件夹，截图在微信传输目录。每次换台电脑渲染，路径全部失效。

这个痛点涉及 Manim 的素材管理三要素：**光栅图像**的加载与缩放、**矢量 SVG** 的解析与兼容性、**素材目录**的组织与团队规范。本章要讲清这三方面，并产出一段整合了位图、矢量和动态流程的成品动画。

---

## 2. 剧本式交锋对话

> **场景**：小陈的屏幕上同时开着 Finder、Illustrator 和 Manim 文档。小胖又在吃零食。

**小胖**（指着屏幕上模糊的 Logo）：

"陈姐，你这个小狐狸的 Logo 怎么跟长了毛一样？是故意的吗？（嚼嚼嚼）——还是 ImageMobject 有 bug？"

**小陈**（叹气）：

"不是 bug，是我给了一个 200×200 的 PNG，然后 `scale(3)` 放大到三倍——它就变成马赛克了。我本以为 Manim 会像 AE 那样自动做平滑放大，看来我想多了。"

**小白**（看了一眼文件属性）：

"位图的本质是固定像素网格。200×200 的图片一共 4 万个色块，你 scale(3) 后每个色块变成 3×3，肉眼就看出锯齿了。Manim 不会帮你做超分辨率——它只是把像素纹理贴到四边形上。解决方案很简单：用高分辨率原图（至少 1024px 宽），让它在画面上的物理尺寸不超过原图的 1:1 像素比。"

**大师**：

"我补充一个计算规则。Manim 的默认画面宽度是 14.2 个单位，高清渲染是 1920 像素。所以**每单位约等于 135 像素**（1920 ÷ 14.2）。如果你的 Logo 希望在画面上占 3 个单位宽，原始图片宽度至少需要 3 × 135 ≈ 400 像素。如果不确定，原则是——图片分辨率宁高勿低，Manim 缩小图片比放大图片效果好得多。"

> **技术映射**：`ImageMobject` 内部使用 `PIL.Image` 加载图片，并以 1:1 像素比映射为 Manim 画面坐标。`scale(2)` 相当于把纹理拉伸两倍，不增加像素信息。

**小胖**（举手打断）：

"那 SVG 呢？SVG 不是矢量的吗，怎么还会报错？"

**小白**：

"SVG 确实是矢量的，但 SVGs 文件里有各种奇技淫巧——滤镜（filter）、裁剪路径（clipPath）、渐变（linearGradient/radialGradient）、蒙版（mask）、CSS 样式。Manim 的 SVG 解析器只是 SVG 规范的一个子集实现，它把 SVG 基本形状（path、rect、circle、line）解析为 VMobject 的贝塞尔点集。遇到不支持的标签（如 filter）就直接抛异常。"

**大师**：

"处理策略分三层：

第一层——**用简单 SVG**：从 Feather Icons、Material Design Icons、Phosphor Icons 等纯路径图标库下载 SVG，这些图标只有 `<path>` 标签，兼容性 100%。

第二层——**预处理 SVG**：用 Inkscape 或 Illustrator 将复杂 SVG 导出为"Plain SVG"或"Optimized SVG"，去掉滤镜、渐变和 CSS，保留下 `<path>` 核心。

第三层——**SVG 直接编辑**：用文本编辑器打开 SVG 文件，手动删除 `<filter>`、`<clipPath>`、`<style>` 等不被 Manim 支持的标签。保留 `<path>` 和简单 `<rect>`、`<circle>`。"

> **技术映射**：Manim 的 SVG 解析器位于 `manim/mobject/svg/svg_mobject.py`，它使用 `xml.etree.ElementTree` 解析 XML，并将识别的 shape 元素转换为 `VMobject` 的贝塞尔路径。

**小陈**（快速打字记录）：

"那第三个问题——素材路径。我不同电脑上跑同一份代码，路径全变了。"

**大师**：

"素材管理的黄金法则是：**所有素材相对于项目根目录**。建立 `assets/` 目录，按类型分子文件夹——"

```
project/
├── assets/
│   ├── images/       # PNG、JPG、WebP
│   ├── svg/          # 图标、矢量插图
│   ├── fonts/        # 自定义字体文件
│   └── audio/        # 背景音乐、旁白
├── scenes/           # 场景 Python 文件
└── manim.cfg
```

"代码中用相对路径加载：`ImageMobject("assets/images/logo.png")`。只要保证在项目根目录执行 `manim` 命令，所有机器的路径都一致。更进一步——写一个 `ASSETS` 常量模块，所有路径集中管理："

```python
# constants/paths.py
from pathlib import Path
ROOT = Path(__file__).parent.parent
ASSETS = ROOT / "assets"
LOGO = ASSETS / "images" / "logo.png"
ICON_ARROW = ASSETS / "svg" / "arrow-right.svg"
```

---

## 3. 项目实战

### 3.1 环境准备

在项目中建立素材目录结构，并准备测试素材：

```bash
# 创建目录
New-Item -ItemType Directory -Path assets/images, assets/svg -Force

# 准备一个测试 PNG（可用任何图片）
# 准备一个测试 SVG（从 https://feathericons.com 下载箭头图标）

# 验证 SVG 可解析
python -c "from manim import SVGMobject; SVGMobject('assets/svg/arrow-right.svg')"
```

---

### 3.2 分步实现

> **本章实战目标**：制作一个"产品功能讲解"动画，包含 Logo（图片）、功能图标（SVG）、流程箭头（Manim 原生 Arrow）和说明文字。

---

#### 步骤一：ImageMobject 使用

**步骤目标**：掌握图片的加载、缩放、定位和常见问题处理。

```python
# scenes/chapter09_image.py
from manim import *

class ImageBasics(Scene):
    def construct(self):
        # --- 加载图片 ---
        # 注意：请替换为你本地存在的图片路径
        img = ImageMobject("assets/images/demo.jpg")
        # 限制高度，保持宽高比
        img.height = 4
        img.to_corner(UL, buff=0.5)

        self.play(FadeIn(img), run_time=1.5)
        self.wait(0.5)

        # --- 图片周围加边框 ---
        rect = SurroundingRectangle(img, color=BLUE, buff=0.1, stroke_width=2)
        self.play(Create(rect), run_time=1)
        self.wait(0.5)

        # --- 缩放和移动 ---
        self.play(
            img.animate.scale(0.5).to_corner(UR, buff=0.5),
            rect.animate.scale(0.5).move_to(img.get_center()),
            run_time=2,
        )
        self.wait(0.5)

        # --- 多张图片布局 ---
        img2 = ImageMobject("assets/images/demo.jpg")
        img2.height = 2
        img2.next_to(img, DOWN, buff=0.8)
        img2_label = Text("缩略图", font_size=20, color=GRAY)
        img2_label.next_to(img2, DOWN, buff=0.15)

        self.play(FadeIn(img2), Write(img2_label), run_time=1.5)
        self.wait(1)

        self.play(FadeOut(VGroup(img, rect, img2, img2_label)), run_time=1.5)
```

**可能遇到的坑**：

1. **图片路径报 `FileNotFound`**：Manim 的当前工作目录必须是项目根目录。检查路径时用 `os.path.exists("assets/images/demo.jpg")` 预先验证。
2. **透明 PNG 背景变黑**：Manim 默认背景是黑色。如果 PNG 的透明区域在深色背景上不可见，可以设置 `config.background_color = WHITE` 或改用浅灰。

---

#### 步骤二：SVG 素材使用

**步骤目标**：加载 SVG 图标，理解矢量解析和可操作能力。

```python
# scenes/chapter09_svg.py
from manim import *

class SVGBasics(Scene):
    def construct(self):
        # --- 加载并解析 SVG ---
        # 用 Manim 内置的测试 SVG 或者你自己的 SVG 文件
        svg = SVGMobject("assets/svg/arrow-right.svg", height=2)

        self.play(DrawBorderThenFill(svg), run_time=1.5)
        self.wait(0.5)

        # --- SVG 支持动画（因为是 VMobject 子类） ---
        self.play(
            svg.animate.set_color(YELLOW).scale(1.5),
            run_time=1.5,
        )
        self.wait(0.3)

        # --- 旋转和透明度 ---
        self.play(
            svg.animate.rotate(PI * 2).set_opacity(0.3),
            run_time=2,
        )
        self.wait(0.5)

        # --- 恢复 ---
        self.play(svg.animate.set_opacity(1.0).set_color(WHITE).rotate(-PI * 2),
                  run_time=1.5)
        self.wait(1)

        self.play(FadeOut(svg), run_time=1)
```

**可能遇到的坑**：

1. **SVG 加载无报错但内容为空**：Manim 解析到了 SVG 标签，但无法转换为路径。用 `print(len(svg.submobjects))` 检查是否成功（>0 则成功）。
2. **SVG 尺寸巨大**：某些 SVG 使用 `viewBox="0 0 1000 1000"` 但内部只有 50px 的图形——Manim 会按 viewBox 整体计算尺寸，导致图标四周大量空白。解决：在 Illustrator/Inkscape 中缩小画板到图形边界。

---

#### 步骤三：产品功能讲解完整动画

**步骤目标**：整合图片、SVG、文字和箭头，制作产品讲解动画。

```python
# scenes/chapter09_product.py
from manim import *

class ProductDemo(Scene):
    def construct(self):
        # ---- 标题 ----
        title = Text("产品功能演示：智能推荐引擎", font_size=36, color=BLUE)
        title.to_edge(UP, buff=0.4)
        self.play(Write(title), run_time=1.5)

        # ---- 中心元件：产品 Logo（图片） ----
        # 用代码生成的替代方案——如果没准备图片素材
        logo_rect = RoundedRectangle(
            width=2.5, height=2.5, corner_radius=0.3,
            color="#4A90D9", fill_opacity=0.2, stroke_width=3,
        )
        logo_text = Text("AI\n推荐", font_size=40, color=WHITE, weight=BOLD)
        logo_text.move_to(logo_rect.get_center())
        logo = VGroup(logo_rect, logo_text)
        # 如果有实际图片，替换为：
        # logo = ImageMobject("assets/images/logo.png").scale(0.5)

        self.play(FadeIn(logo, scale=0.8), run_time=1.5)
        self.wait(0.3)

        # ---- 四个功能图标围绕 Logo ----
        labels = [
            ("用户画像", LEFT * 5 + UP * 1.5, "#F5A623", "👤"),
            ("内容理解", RIGHT * 5 + UP * 1.5, "#7ED321", "📄"),
            ("实时计算", LEFT * 5 + DOWN * 1.5, "#4A90D9", "⚡"),
            ("效果评估", RIGHT * 5 + DOWN * 1.5, "#D0021B", "📊"),
        ]

        features = VGroup()
        for name, pos, color, emoji in labels:
            # 用代码生成图标（替代 SVG）
            icon_box = Square(side_length=1.0, color=color, fill_opacity=0.15)
            icon_emoji = Text(emoji, font_size=36, color=WHITE)
            icon_emoji.move_to(icon_box.get_center())
            icon = VGroup(icon_box, icon_emoji)

            label = Text(name, font_size=22, color=color)
            label.next_to(icon, DOWN, buff=0.15)

            feature = VGroup(icon, label)
            feature.move_to(ORIGIN + pos)
            features.add(feature)

        # 连接线：Logo 到每个图标
        connections = VGroup()
        for feat in features:
            arrow = Arrow(
                logo.get_center(), feat[0].get_center(),
                color=GRAY, stroke_width=2, buff=0.15,
                tip_length=0.15,
            )
            connections.add(arrow)

        self.play(
            LaggedStart(*[FadeIn(f, scale=0.5) for f in features], lag_ratio=0.15),
            LaggedStart(*[Create(c) for c in connections], lag_ratio=0.15),
            run_time=3,
        )
        self.wait(0.5)

        # ---- 高亮一个功能点 ----
        highlight_feature = features[0]
        self.play(
            highlight_feature.animate.scale(1.2),
            Indicate(highlight_feature[0], color=YELLOW, scale_factor=1.2),
            run_time=2,
        )
        self.wait(1)

        # 展示细节文字
        detail = Text(
            "基于浏览和购买历史\n构建用户兴趣模型",
            font_size=22, color=WHITE, line_spacing=0.6,
            alignment="center",
        )
        detail.next_to(highlight_feature, RIGHT, buff=1.2)
        self.play(FadeIn(detail, shift=LEFT * 0.3), run_time=1.5)
        self.wait(1.5)

        self.play(FadeOut(VGroup(title, logo, highlights_self.features,
            connections, detail)), run_time=2)
        self.wait(0.5)
```

---

### 3.3 完整代码清单

> 代码仓库：`https://github.com/yourteam/manim-column-src/tree/main/chapter09`

由于素材文件不便在文档中直接展示，本章实战代码以 Python 脚本形式存在，完整 `scenes/chapter09_product.py` 见上方。

### 3.4 测试验证

| 验证项 | 操作 | 预期结果 |
|--------|------|----------|
| 图片加载 | `ImageMobject("...png")` → `print(img.width, img.height)` | 输出非零值 |
| SVG 解析 | `SVGMobject("...svg")` → `print(len(svg.submobjects))` | > 0 |
| 相对路径 | 在项目根目录和子目录分别执行 manim | 均能正确找到素材 |
| SVG 动画 | 对 SVG 对象执行 `animate.set_color()` | 颜色平滑变化 |
| 透明通道 | 在白色背景上加载透明 PNG | 透明区域不遮挡底层 |

---

## 4. 项目总结

### 优点 & 缺点

| 维度 | 优点 | 缺点 |
|------|------|------|
| 图片加载 | ImageMobject 支持常见格式（PNG/JPG/WebP） | 无格式转换能力，需预处理素材 |
| SVG 矢量 | 解析后为 VMobject，支持颜色/缩放/旋转动画 | 复杂 SVG（滤镜/渐变）需预处理 |
| 素材路径 | 支持相对路径，团队共享方便 | 路径依赖当前工作目录，需规范 |
| 透明通道 | PNG 透明正确显示 | SVG 透明度有时丢失 |
| 尺寸控制 | height/width 属性保持宽高比 | 初始尺寸由图片像素决定，计算不便 |

### 适用场景

| 场景 | 说明 |
|------|------|
| 品牌宣传 | Logo、品牌色、品牌图标的统一展示 |
| 产品演示 | 产品截图 + 功能图标 + 流程箭头 |
| 技术架构 | 技术栈图标 + 连接关系 |
| 排版装饰 | 矢量装饰元素作为背景/边框 |
| 数据报告 | 二维码、数据来源 Logo |

**不适用场景**：需要逐帧动画的复杂角色（应用 Spine/Live2D）、需要交互操作的图像热区（应用 HTML/CSS/JS）。

### 注意事项

1. **SVG 文件编码**：确保 SVG 文件是 UTF-8 编码，否则 Manim 的 XML 解析器可能报编解码错误。
2. **图片版权**：商业项目中使用素材需确认版权许可。推荐 Unsplash（图片）、Feather Icons（SVG）。
3. **大文件优化**：单张图片超过 5MB 可能导致渲染内存峰值过高。建议在导入前用 PIL 或在线工具压缩。

### 常见踩坑经验

**故障一：SVG 渲染后颜色与预览不符**

根因：SVG 文件中定义了 `<style>` 或 CSS 规则覆盖了内联属性，但 Manim 只解析内联属性。

解决：在 SVG 中移除 `<style>` 标签，将样式写为内联属性，或加载后用 `svg.set_color(xxx)` 覆盖。

**故障二：`ImageMobject` 移动后画面残留**

根因：如果图片背景是透明的，上一帧的像素残留在缓冲区。

解决：这不是 Manim 的问题——是播放器的帧混合问题。在视频编辑器中重新渲染，或确保 `manim.cfg` 中设 `background_color=BLACK`。

**故障三：SVG 解析报 `ValueError: can't normalize zero length array`**

根因：SVG 中存在退化路径（如两个点重合的 `<path>`），Manim 无法创建贝塞尔曲线。

解决：在矢量编辑器中删除退化路径，或将 SVG 保存为"简化"版本。

### 思考题

1. 编写一个 `class IconLibrary`，在 `__init__` 时扫描 `assets/svg/` 目录下所有 `.svg` 文件，并用 `get_icon(name)` 方法按文件名返回 `SVGMobject` 实例。要求：图标在首次加载后缓存，避免重复解析。提示：使用 `Path.glob()` 扫描文件，字典缓存已加载对象。

2. 在 `ProductDemo` 中，将四个功能图标和连接线实现为"可交互"效果：当某个功能图标被高亮时，对应的连接线从灰色变为高亮色。提示：给每个功能创建一个包含图标 + 标签 + 连接线的 VGroup，高亮时遍历 VGroup 的所有子对象依次改变颜色。

---

### 推广计划提示

| 角色 | 本章阅读重点 | 协作事项 |
|------|-------------|----------|
| 新人开发 | 完整通读，掌握素材加载和路径规范 | 完成产品演示动画 |
| 测试 | 验证 SVG 兼容性矩阵 | 收集常见 SVG 图标库的兼容性数据 |
| 运维 | 关注素材版本管理 | 将 assets/ 纳入 Git LFS 管理 |
