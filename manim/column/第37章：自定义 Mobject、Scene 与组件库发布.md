# 第37章：自定义 Mobject、Scene 与组件库发布

---

## 1. 项目背景

第 25 章介绍了项目内部的组件封装（`TitleCard`、`StepIndicator` 等）。但当一个团队积累了大量组件后，新的需求浮现了：如何将这些组件打包为**可安装的 Python 包**，供团队内其他项目（乃至社区）通过 `pip install` 直接使用？

某教育科技公司的工具链团队维护了一套内部 Manim 组件库——包括 15 个自定义 Mobject、5 个 Scene 基类和 8 个 Animation。最初这些组件散落在各项目的 `components/` 目录中，靠复制粘贴"同步"。当组件在 A 项目中被修复了 bug 后，B 项目还在用旧版本，导致了"版本分裂"。

工具链负责人老苏决定将组件库发布为一个正式的 Python 包 `manim-edu-kit`，通过 GitLab 私有 PyPI 仓库分发。这需要掌握：`pyproject.toml` 的包结构配置、自定义 Mobject/Scene 的 API 设计规范、语义化版本管理和文档生成。

---

## 2. 剧本式交锋对话

> **场景**：老苏在 VS Code 中打开了 `pyproject.toml`，左边是打包教程，右边是组件源码。

**小胖**（抱着一桶爆米花）：

"苏哥，你不是已经有个 `components/` 目录了吗？为什么还要折腾打包？复制粘贴到其他项目不就完了？几秒钟的事。"

**老苏**（指着屏幕上的 Git Blame）：

"你看——`TitleCard` 在项目 A 里已经被改过 3 次了（修圆角、改字号、加渐变色）。但在项目 B 的 `components/` 里还是半年前的版本。上周项目 B 渲染出来的标题圆角是 0.1 而不是 0.15，教研组抱怨了两个小时。复制粘贴的代价就是'永远不知道哪个版本是最新的'。"

**小白**（已经写好了 `pyproject.toml`）：

"Python 包机制就是为了解决这个问题。你不用再复制文件——`pip install manim-edu-kit` 一行命令，所有组件自动安装到虚拟环境中。升级时 `pip install --upgrade manim-edu-kit` 一键全项目更新。

打包只需要一个 `pyproject.toml` 文件："

```toml
[build-system]
requires = ["setuptools>=64", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "manim-edu-kit"
version = "1.2.0"
description = "Manim 教育课件组件库"
requires-python = ">=3.9"
dependencies = ["manim>=0.20.0"]

[project.optional-dependencies]
dev = ["pytest", "pytest-cov", "pre-commit"]

[tool.setuptools.packages.find]
where = ["src"]
```

**大师**（在白板上画了一个包结构图）：

"Python 包的目录结构也很简洁——"

```
manim-edu-kit/
├── pyproject.toml
├── README.md
├── src/
│   └── manim_edu_kit/
│       ├── __init__.py
│       ├── mobjects.py      # 自定义 Mobject
│       ├── scenes.py        # Scene 基类
│       ├── animations.py    # 自定义 Animation
│       └── components/
│           ├── cards.py     # 第25章的 TitleCard 等
│           └── indicators.py
├── tests/
│   └── test_components.py
└── examples/
    └── gallery.py           # 组件画廊示例
```

"发布命令只有两步：

```bash
python -m build              # 构建 .whl 和 .tar.gz
twine upload dist/*          # 上传到 PyPI（或私有仓库）
```"

---

## 3. 项目实战

### 3.1 环境准备

```bash
pip install build twine setuptools wheel
```

---

### 3.2 分步实现

> **本章实战目标**：将前 25 章的组件库升级为可发布的 Python 包 `manim-edu-kit`，包含自定义 Mobject、Scene 基类和发布流程。

---

#### 步骤一：创建包目录结构

**步骤目标**：搭建标准的 `src` 布局和 `pyproject.toml`。

```bash
# 创建包目录
manim-edu-kit/
├── pyproject.toml
├── src/
│   └── manim_edu_kit/
│       ├── __init__.py
│       ├── mobjects.py
│       ├── scenes.py
│       ├── animations.py
│       └── components/
│           ├── __init__.py
│           ├── cards.py
│           └── indicators.py
├── tests/
│   ├── __init__.py
│   └── test_cards.py
└── examples/
    └── gallery.py
```

`src/manim_edu_kit/__init__.py`：

```python
"""Manim Education Kit — 教学视频组件库"""
__version__ = "1.2.0"

from .mobjects import ProgressBar, GradientBadge
from .scenes import ThemedScene, SlideScene
from .animations import TypewriterNumber, FlashBorder
from .components.cards import TitleCard, InfoBox, AnswerCard
from .components.indicators import StepIndicator, StepFlow
```

**关键决策**：`__init__.py` 是包的"公共 API 面"。只导出稳定的、经过文档化的接口。内部实现细节不要导出。

---

#### 步骤二：自定义 Mobject 和 Scene 封装

**步骤目标**：实现 `ProgressBar`（进度条）和 `ThemedScene`（主题场景基类）。

```python
# src/manim_edu_kit/mobjects.py
from manim import *

class ProgressBar(VGroup):
    """可配置的进度条组件"""
    def __init__(self, total=1.0, value=0.0, width=6, height=0.3,
                 fill_color=GREEN, bg_color=GRAY, **kwargs):
        super().__init__(**kwargs)
        self.total = total
        self._value = value
        self.bar_width = width
        self.bar_height = height

        # 背景轨
        self.track = RoundedRectangle(
            width=width, height=height, corner_radius=0.15,
            color=bg_color, fill_opacity=0.2, stroke_width=0,
        )
        # 填充条
        self.fill = RoundedRectangle(
            width=width * (value / total) if total > 0 else 0,
            height=height, corner_radius=0.15,
            color=fill_color, fill_opacity=0.9, stroke_width=0,
        )
        self.fill.align_to(self.track, LEFT)

        # 文字标签
        self.label = Text(f"{value:.0f}/{total:.0f}", font_size=20, color=WHITE)
        self.label.move_to(self.track.get_center())

        self.add(self.track, self.fill, self.label)

    @property
    def value(self):
        return self._value

    @value.setter
    def value(self, new_value):
        self._value = new_value
        ratio = new_value / self.total if self.total > 0 else 0
        self.fill.stretch_to_fit_width(self.bar_width * ratio)
        self.fill.align_to(self.track, LEFT)
        self.label.become(Text(
            f"{self._value:.0f}/{self.total:.0f}",
            font_size=20, color=WHITE,
        ).move_to(self.track.get_center()))


class GradientBadge(VMobject):
    """渐变色徽章"""
    def __init__(self, text, gradient=(BLUE, PURPLE), **kwargs):
        super().__init__(**kwargs)
        self.text = Text(text, font_size=24, weight=BOLD)
        # 用多层 Text 叠加实现渐变效果（简化版）
        self.text.set_color(gradient[0])
        self.add(self.text)
```

```python
# src/manim_edu_kit/scenes.py
from manim import *

class ThemedScene(Scene):
    """主题化场景基类——所有教学场景的父类"""
    primary = ManimColor("#4C9AFF")
    accent  = ManimColor("#FFB020")
    success = ManimColor("#47D38D")
    danger  = ManimColor("#FF4D4F")

    def setup(self):
        super().setup()
        self.camera.background_color = ManimColor("#0D1117")

    def make_card(self, title, subtitle="", **kwargs):
        """创建统一风格卡片"""
        from manim_edu_kit.components.cards import TitleCard
        return TitleCard(title, subtitle, **kwargs)

    def transition(self, duration=0.8):
        """段落过渡留白"""
        self.wait(duration)
```

---

#### 步骤三：构建、测试与发布

**步骤目标**：完成包的构建、本地测试和发布流程。

```bash
# 1. 构建 wheel 包
cd manim-edu-kit
python -m build

# 输出在 dist/ 目录：
# dist/manim_edu_kit-1.2.0-py3-none-any.whl
# dist/manim_edu_kit-1.2.0.tar.gz

# 2. 本地安装测试
pip install dist/manim_edu_kit-1.2.0-py3-none-any.whl

# 3. 验证
python -c "from manim_edu_kit import TitleCard, ProgressBar, ThemedScene; print('OK')"

# 4. 发布到 PyPI（公开）或私有仓库
# twine upload dist/*                                    # 公开 PyPI
# twine upload --repository-url https://gitlab.com/api/v4/projects/.../packages/pypi dist/*  # GitLab PyPI
```

**单元测试**（`tests/test_cards.py`）：

```python
from manim_edu_kit import TitleCard, ProgressBar

def test_title_card_creation():
    card = TitleCard("测试标题", "副标题")
    assert len(card.submobjects) >= 2
    return "PASS"

def test_progress_bar_update():
    bar = ProgressBar(total=100, value=30)
    assert bar.value == 30
    bar.value = 50
    assert bar.value == 50
    return "PASS"

if __name__ == "__main__":
    for test in [test_title_card_creation, test_progress_bar_update]:
        try:
            print(f"[{test()}] {test.__name__}")
        except Exception as e:
            print(f"[FAIL] {test.__name__}: {e}")
```

---

### 3.3 完整代码清单

```python
# pyproject.toml —— 见上方
# src/manim_edu_kit/__init__.py
# src/manim_edu_kit/mobjects.py —— ProgressBar, GradientBadge
# src/manim_edu_kit/scenes.py —— ThemedScene
# src/manim_edu_kit/animations.py —— TypewriterNumber, FlashBorder
# tests/test_cards.py
```

### 3.4 测试验证

| 验证项 | 操作 | 预期结果 |
|--------|------|----------|
| 包构建 | `python -m build` | dist/ 下生成 .whl 和 .tar.gz |
| pip 安装 | `pip install dist/*.whl` | 不报错 |
| 导入可用 | `from manim_edu_kit import TitleCard` | 成功导入 |
| 单元测试 | `python tests/test_cards.py` | 全部 PASS |

---

## 4. 项目总结

### 优点 & 缺点

| 维度 | 优点 | 缺点 |
|------|------|------|
| 版本管理 | `pip install --upgrade` 一键全项目更新 | 私有 PyPI 仓库需要额外运维 |
| API 稳定性 | `__init__.py` 定义公共 API 面 | 内部重构可能破坏下游项目 |
| 测试保障 | 单元测试让组件修改有把握 | Manim 组件测试需要 mock Scene 对象 |
| 文档生成 | 可配合 Sphinx 自动生成 API 文档 | 需要额外维护文档基础设施 |
| 团队协作 | 组件库是团队规范的可执行版本 | 包的向后兼容承诺增加了维护成本 |

### 适用场景

| 场景 | 说明 |
|------|------|
| 多项目共享组件 | 团队内 3+ 个 Manim 项目共用组件 |
| 开源贡献 | 将有用组件贡献给社区 |
| 企业标准化 | 公司品牌设计和动画规范的代码化 |
| 教学工具包 | 为特定学科（数学/物理）封装专用组件 |
| CI 集成 | 组件库作为独立包在 CI 中安装 |

**不适用场景**：单项目内使用的私有组件（第 25 章的 `components/` 目录已足够）、组件 API 仍在快速变化的实验阶段。

### 注意事项

1. **包的依赖声明**：`pyproject.toml` 中 `dependencies = ["manim>=0.20.0"]` 指明最低 Manim 版本。如果用了 OpenGL 特性，增加 `"manim[opengl]>=0.20.0"`。
2. **`src` 布局**：`src/manim_edu_kit/` 而非顶层的 `manim_edu_kit/`。前者防止在开发目录中误 import 包，强制从已安装的路径导入。
3. **语义化版本**：`主版本.次版本.修订号`（如 1.2.0）。API 不兼容变更 → 主版本 +1，新功能向下兼容 → 次版本 +1，bug 修复 → 修订号 +1。

### 常见踩坑经验

**故障一：`pip install` 后导入报 `ModuleNotFoundError: No module named 'manim_edu_kit'`**

根因：`pyproject.toml` 中 `[tool.setuptools.packages.find]` 的 `where = ["src"]` 没有生效。

解决：确认包代码在 `src/manim_edu_kit/` 下，且 `src/` 目录下无同名冲突文件夹。

**故障二：包发布后下游项目更新报 API 错误**

根因：主版本升级了（1.x → 2.0）但下游没更新调用代码。

解决：严格遵守语义化版本。在 CHANGELOG.md 中记录每版本的不兼容变更。

**故障三：`python -m build` 报 `No module named 'setuptools'`**

根因：`build` 工具依赖 `setuptools>=64`，但当前环境安装的是旧版。

解决：`pip install --upgrade setuptools wheel build`。

**故障四：私有 PyPI 上传后 pip install 超时**

根因：公司 GitLab/JFrog 私有仓库的内网 DNS 解析慢或需 VPN。

解决：在 `pip.conf` 中设置超时：`timeout = 60`，或在 `pip install --index-url` 中直接指定 IP。

**故障五：包中的 `from manim import *` 与下游项目的 import 冲突**

根因：包在 `__init__.py` 顶部做了 `from manim import *`，污染了下游的命名空间。

解决：在包的 `__init__.py` 中只导入必要的 Manim 类型，不使用 `*`。或者在 `__all__` 中显式声明导出列表。

**故障六：自定义 Mobject 在包中工作，但 pip install 后渲染空白**

根因：自定义 Mobject 的 `__init__` 中调用了 Manim 的全局 config 对象（如 `config.frame_width`），但在包加载时 config 尚未初始化。

解决：将依赖 config 的代码延迟到 `construct()` 或 `add()` 时执行，而非 `__init__` 时。

**故障七：包的 `__init__.py` 顶层导入触发了循环依赖**

根因：`from .mobjects import ProgressBar` 引用了 `ProgressBar`，而 `ProgressBar` 又引用了包内的其他模块，形成循环。

解决：使用延迟导入（lazy import）——在函数内部 `import` 而非模块顶层。

**故障八：用户 pip install 后使用组件时，组件内部引用的 assets 路径失效**

根因：包中的 `ImageMobject("assets/logo.png")` 使用了相对路径，但 pip install 后当前工作目录不是包的源码目录。

解决：使用 `importlib.resources` 或 `pkg_resources` 访问包内资源文件，或要求用户显式传递文件路径而非硬编码。

### 思考题

1. 将第 33 章的 `PolylineMobject` 和第 34 章的 `TypewriterNumber` 集成到 `manim-edu-kit` 包中，并在 `examples/gallery.py` 中创建一个展示全部组件的画廊场景。

2. 为 `manim-edu-kit` 添加 Sphinx 文档生成：用 `sphinx-quickstart` 创建文档目录，用 `sphinx.ext.autodoc` 自动从 docstring 生成 API 文档，最后用 Read the Docs 托管。

3. 实现一个 `class CircleProgress(ProgressBar)`——继承自 ProgressBar，但将进度条改为圆形进度环（用 `Arc` 和 `SVGMobject` 实现）。要求支持自定义颜色、线宽和动画过渡。

---

### 推广计划提示

| 角色 | 本章阅读重点 | 协作事项 |
|------|-------------|----------|
| 架构师/资深开发 | 掌握打包和发布全流程 | 制定团队包发布规范 |
| 核心开发 | 将自定义组件迁移进包 | 编写包的使用文档和示例 |
| 测试 | 为包编写自动化测试 | 维护包的 CI 测试流水线 |

#### 附录：Python 包发布 checklist

1. `pyproject.toml` 填写 name/version/description/dependencies
2. 包代码放在 `src/` 目录下（`src` 布局）
3. `__init__.py` 只导出稳定的公共 API
4. `python -m build` 构建成功生成 `.whl` 和 `.tar.gz`
5. `pip install dist/*.whl` 本地安装测试通过
6. `python -c "import my_package"` 不报错
7. `tests/` 目录中至少 3 个测试用例
8. `README.md` 含安装说明和最简示例
9. `twine upload` 或推送到私有 PyPI 仓库
10. 打 git tag `v1.0.0` 并推送

---

### 推广计划提示

| 角色 | 本章阅读重点 | 协作事项 |
|------|-------------|----------|
| 架构师/资深开发 | 掌握打包和发布全流程 | 制定团队包发布规范 |
| 核心开发 | 将自定义组件迁移进包 | 编写包的使用文档和示例 |
| 测试 | 为包编写自动化测试 | 维护包的 CI 测试流水线 |
