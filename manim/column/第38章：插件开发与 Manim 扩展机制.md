# 第38章：插件开发与 Manim 扩展机制

---

## 1. 项目背景

某教育工具团队在使用 Manim 制作课程的过程中，积累了一套固定的工作流——每次新建一节课都需要执行相同的步骤：创建目录、生成模板文件、配置场景骨架、复制组件引用。这些重复操作每次耗时 15 分钟，一个月累计超过 5 小时。

团队想自动化这个流程——写一个 CLI 命令 `manim-course init "第22课·矩阵乘法"`，一键生成标准项目骨架。但 Manim 的 CLI 是基于 click 框架的，如何在不修改 Manim 源码的前提下扩展 CLI 命令？

另一个场景：教研团队想在 Manim 中直接读取 YAML 格式的镜头脚本文件，自动生成动画的时间轴。这需要扩展 Manim 的配置系统，支持自定义的 `[course]` 配置段。

这些需求指向了 Manim 的**插件机制**——通过 Python 的 `entry_points` 协议，将自己的模块注册到 Manim 的插件系统中，扩展 CLI 命令、添加自定义配置段和注入新的 Mobject/Scene 类型。

---

## 2. 剧本式交锋对话

> **场景**：工具团队老马正在阅读 Manim 的 `cli/main.py` 和 `setup.cfg`，试图搞懂插件注册机制。

**小胖**（啃着一根麻花）：

"马哥，你为啥要读 Manim 的源码？你不是只想加一个 `manim-course init` 命令吗？自己写个独立脚本不就完了，非要往 Manim 里面塞？"

**老马**（指着屏幕上的 `entry_points`）：

"独立脚本的问题在于——用户需要记两套命令：`manim scene.py` 和 `python course.py init`。如果能把自定义命令注册为 Manim 的子命令，用户只用记住 `manim` 一个入口。这就是插件的核心价值：**无缝集成**，不需要用户改变工作习惯。"

**小白**（打开 Manim 的 `setup.cfg` 和 `pyproject.toml`）：

"Manim 支持两种插件扩展方式：

**方式一：entry_points 注册**——在你的 `pyproject.toml` 中声明一个 Manim 插件入口：

```toml
[project.entry-points."manim.plugins"]
"manim_course" = "manim_course.plugin:register"
```

然后在插件代码中实现 `register()` 函数，返回自定义的命令列表或配置段。

**方式二：click group 注入**——如果你的包定义了一个 click 命令组，可以通过 `entry_points` 把它注册为 Manim 的子命令：

```toml
[project.entry-points."manim.cli.commands"]
"course" = "manim_course.cli:course_group"
```

用户安装你的包后，`manim course init` 自动可用。"

**大师**（补充）：

"除了 CLI 扩展，Manim 插件还可以做三件事：

1. **自定义配置段**：在 `manim.cfg` 中添加 `[course]` 段，读取课程模板路径等参数。
2. **注入全局 Mobject/Animation**：在包的 `register()` 中向 Manim 的全局命名空间注入自定义类，让用户的场景代码可以直接使用。
3. **模板命令**：插件可以包括一个 `manim course new` 的子命令，从模板目录生成新场景文件。

这三者组合起来就是一套完整的'课程脚手架工具'。"

> **技术映射**：`entry_points` 是 Python 包的标准分发机制。Manim 在启动时通过 `importlib.metadata.entry_points()` 扫描已安装包的入口点，发现并注册插件。

---

## 3. 项目实战

### 3.1 环境准备

```bash
# 创建插件包目录
manim-course-kit/
├── pyproject.toml
├── src/
│   └── manim_course_kit/
│       ├── __init__.py
│       ├── cli.py
│       ├── config.py
│       └── templates/
│           ├── scene_template.py.j2
│           └── manim.cfg.j2
└── tests/
```

---

### 3.2 分步实现

> **本章实战目标**：开发一个 `manim-course-kit` 插件，提供课程初始化、章节渲染和配置扩展。

---

#### 步骤一：注册 CLI 命令

**步骤目标**：实现 `manim course init` 和 `manim course render` 命令。

```python
# src/manim_course_kit/cli.py
import click
from pathlib import Path

@click.group()
def course():
    """Manim 课程管理工具"""
    pass

@course.command()
@click.argument("title")
@click.option("--dir", "directory", default=".", help="项目根目录")
def init(title, directory):
    """初始化一个新课程项目"""
    project_root = Path(directory).resolve()
    scenes_dir = project_root / "scenes"
    scenes_dir.mkdir(parents=True, exist_ok=True)

    # 生成 manim.cfg
    cfg_content = """[CLI]
quality = h
frame_rate = 30
background_color = #0D1117
output_dir = ./build/media

[directories]
tex = ./build/tex_cache
"""
    (project_root / "manim.cfg").write_text(cfg_content, encoding="utf-8")

    # 生成场景模板
    safe_name = title.replace(" ", "_").replace("·", "_")
    template = f'''"""第 {title} 章"""
from manim import *
from manim_course_kit import ThemedScene

class {safe_name}(ThemedScene):
    def construct(self):
        card = self.make_card("{title}")
        card.to_edge(UP, buff=0.4)
        self.play(card.show(self))
        self.wait(2)
        self.play(card.hide(self))
        self.wait(0.5)
'''
    scene_file = scenes_dir / f"{safe_name.lower()}.py"
    scene_file.write_text(template, encoding="utf-8")

    click.echo(f"✅ 课程项目已初始化: {project_root}")
    click.echo(f"   场景文件: {scene_file}")

@course.command()
@click.option("--quality", default="h", help="渲染质量 (l/m/h/k)")
@click.option("--preview/--no-preview", default=False)
def render(quality, preview):
    """渲染课程的所有场景（调用 build_v2.py）"""
    import subprocess, sys
    cmd = [sys.executable, "build_v2.py", quality]
    if preview:
        cmd.append("--preview")
    click.echo(f"执行: {' '.join(cmd)}")
    subprocess.run(cmd)
```

```python
# src/manim_course_kit/__init__.py
"""Manim Course Kit — 课程脚手架插件"""
__version__ = "0.1.0"

def register():
    """注册插件到 Manim 系统"""
    # 这个函数由 entry_points 机制自动调用
    from manim import logger
    logger.info("[manim-course-kit] 插件已加载")

# 声明 entry_points
# 在 pyproject.toml 中：
# [project.entry-points."manim.plugins"]
# "course_kit" = "manim_course_kit:register"
#
# [project.entry-points."manim.cli.commands"]
# "course" = "manim_course_kit.cli:course"
```

**`pyproject.toml`** 关键段：

```toml
[project]
name = "manim-course-kit"
version = "0.1.0"
dependencies = ["manim>=0.20.0", "click>=8.0"]

[project.entry-points."manim.plugins"]
"course_kit" = "manim_course_kit:register"

[project.entry-points."manim.cli.commands"]
"course" = "manim_course_kit.cli:course"
```

---

#### 步骤二：自定义配置段

**步骤目标**：让插件支持在 `manim.cfg` 中读取自定义的 `[course]` 段。

```python
# src/manim_course_kit/config.py
from manim import config, logger
from pathlib import Path

class CourseConfig:
    """读取 manim.cfg 中 [course] 段的自定义配置"""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._load()
        return cls._instance

    def _load(self):
        # 从 manim.cfg 读取自定义段
        parser = config._config_parser
        if parser.has_section("course"):
            self.template_dir = parser.get("course", "template_dir", fallback="templates")
            self.default_quality = parser.get("course", "default_quality", fallback="h")
        else:
            self.template_dir = "templates"
            self.default_quality = "h"
        logger.info(f"[course config] template_dir={self.template_dir}")

    @classmethod
    def get(cls):
        return cls()

# manim.cfg 示例：
# [course]
# template_dir = my_templates
# default_quality = h
```

---

#### 步骤三：完整插件验证

**步骤目标**：安装插件并验证所有功能。

```bash
# 1. 安装插件
pip install -e /path/to/manim-course-kit

# 2. 验证 CLI 扩展
manim course --help
# 输出：
#   init    初始化一个新课程项目
#   render  渲染课程的所有场景

# 3. 初始化新课程
manim course init "第22课·矩阵乘法"
# 输出：
#   ✅ 课程项目已初始化: /current/dir
#      场景文件: scenes/第22课_矩阵乘法.py

# 4. 渲染
cd project_dir
manim course render --quality h
```

---

### 3.3 完整代码清单

```python
# pyproject.toml (关键段)
# src/manim_course_kit/__init__.py
# src/manim_course_kit/cli.py —— course init/render
# src/manim_course_kit/config.py —— CourseConfig
# templates/scene_template.py.j2
```

### 3.4 测试验证

| 验证项 | 操作 | 预期结果 |
|--------|------|----------|
| 插件加载 | `manim --help` 查看子命令 | 含 `course` 子命令 |
| init 命令 | `manim course init "测试课"` | 创建目录和模板文件 |
| 配置文件 | 添加 `[course]` 段后渲染 | 插件读取到自定义配置 |
| render 命令 | `manim course render -q l` | 调用 build_v2.py |

---

## 4. 项目总结

### 优点 & 缺点

| 维度 | 优点 | 缺点 |
|------|------|------|
| CLI 扩展 | 用户无需记忆额外命令入口 | entry_points 的注册格式在不同打包工具间有差异 |
| 配置扩展 | 自定义配置段与 manim.cfg 共存 | 配置段名可能与 Manim 未来官方段名冲突 |
| 模板生成 | 一键生成标准项目骨架 | 模板的灵活性受限于预定义的目录结构 |
| 安装体验 | `pip install` 即启用 | 插件与 Manim 主版本的兼容性需手动管理 |

### 适用场景

| 场景 | 说明 |
|------|------|
| 课程脚手架 | 快速初始化课程项目 |
| 设计规范注入 | 自动加载团队主题和字体 |
| 自定义渲染流水线 | 批量渲染 + 字幕 + 归档 |
| 学科工具包 | 物理/化学/数学的特化组件和命令 |
| CI 集成 | 插件提供 CI 友好的命令行接口 |

**不适用场景**：仅需 1-2 个自定义 Mobject 的简单项目（直接用第 25 章的组件目录），需要深度修改 Manim 渲染核心的极端定制（直接 fork Manim 仓库）。

### 注意事项

1. **entry_points 的命名空间**：插件名应该是 `[project.entry-points."manim.plugins"]`，不是 `"manim.plugin"`（单数）。拼写错误会导致注册失败。
2. **插件间的命名冲突**：两个插件都注册了 `course` 子命令会冲突。命名时加上命名空间前缀（如 `edu-course` 而非 `course`）。
3. **config 读取时机**：插件在 Manim 启动时加载，此时 `manim.cfg` 已读取完毕。自定义配置段在 `register()` 中可以安全读取。

### 常见踩坑经验

**故障一：插件已安装但 `manim` 不识别新命令**

根因：`pyproject.toml` 的 `entry-points` 拼写错误，或 packager 不支持该格式。

解决：确认打包工具版本正确。`python -c "from importlib.metadata import entry_points; print(entry_points(group='manim.plugins'))"` 验证注册。

**故障二：插件安装后 `manim` 启动慢 2-3 秒**

根因：插件的 `register()` 中执行了耗时的初始化（如加载大型模型）。

解决：懒加载——在 `register()` 中只做轻量声明，实际初始化延迟到命令执行时。

**故障三：`pip install -e .` 开发模式下插件行为与发布版不同**

根因：开发模式下 Python 直接从源码目录导入，而发布版从 `.whl` 安装。路径解析可能不一致。

解决：在 CI 中用 `pip install dist/*.whl` 测试发布版的行为。

**故障四：两个插件依赖了同一个库的不同版本**

根因：A 插件依赖 `pydantic>=2.0`，B 插件依赖 `pydantic<2.0`，pip 只能装一个。

解决：在 `pyproject.toml` 中放宽版本约束（如 `>=1.10,<3.0`），或使用 `pipx` 为每个插件创建独立环境。

**故障五：自定义命令中的 `subprocess.run` 找不到 manim**

根因：插件的虚拟环境中 `manim` 命令在 `bin/` 下，但 `subprocess.run` 不继承 shell PATH。

解决：使用 `sys.executable -m manim` 而非直接调用 `manim` 命令。

**故障六：`entry_points` 在 `pip install -e` 开发模式下注册失败**

根因：`build` 工具生成的元数据中 `entry_points.txt` 文件未被 setuptools 正确写入可编辑安装的元数据目录。

解决：先 `python -m build` 构建 wheel，再 `pip install dist/*.whl` 测试注册是否生效。如果开发模式下注册失败是 setuptools 的已知 bug，升级 setuptools 到 >=69.0。

**故障七：插件注册的 CLI 命令与 Manim 未来版本的命令名冲突**

根因：插件叫 `manim course`，Manim 0.22 版本也增加了官方的 `manim course` 命令。

解决：插件命名加公司/项目前缀（如 `manim mycompany-course`），或使用 namespace package 隔离。

**故障八：用户在不同 Python 版本下安装插件，entry_points 不生效**

根因：Python 3.9 和 3.12 的 `importlib.metadata` API 有细微差异。旧代码的 `entry_points()` 调用方式在新版 Python 中已弃用。

解决：使用兼容写法：
```python
import sys
if sys.version_info >= (3, 12):
    from importlib.metadata import entry_points
    plugins = entry_points(group="manim.plugins")
else:
    from importlib.metadata import entry_points
    plugins = entry_points().get("manim.plugins", [])
```

### 思考题

1. 扩展 `manim-course-kit`，新增 `manim course archive <version>` 命令：将 build/media 中的 MP4 按版本号归档到 output/ 目录，并自动生成一个 HTML 索引页面列出所有视频和对应字幕。

2. 为插件添加 `manim course validate` 命令：扫描 scenes/ 目录下所有场景，检查必选的 import 语句、检查 `construct` 方法是否存在、并验证不包含第 13 章的常见错误模式（如硬编码颜色而非从 theme 导入）。

3. 实现插件的自动更新检查：在 `register()` 中调用 PyPI API（`https://pypi.org/pypi/manim-course-kit/json`）检查是否有新版本，若有则在 Manim 启动时打印一条升级提示。注意：这需要网络请求，应考虑超时和离线容错。

---

### 推广计划提示

| 角色 | 本章阅读重点 | 协作事项 |
|------|-------------|----------|
| 架构师/资深开发 | 掌握 entry_points 插件机制 | 设计团队插件架构 |
| 核心开发 | 实现 CLI 和配置扩展 | 开发课程脚手架插件 |
| 运维 | 管理插件发布的 CI/CD | 配置私有插件仓库 |

#### 附录：插件开发最佳实践

1. **命名空间**：插件命令名加前缀（如 `manim-edu-*`），避免与社区插件冲突
2. **懒加载**：`register()` 中只调用 `logger.info`，实际逻辑延迟到命令执行
3. **错误友好**：YAML/TOML 解析错误时给出含行号的错误消息
4. **版本锁定**：`pyproject.toml` 中依赖 `manim>=0.20.0,<0.22`
5. **向后兼容**：deprecation warning 给至少 2 个次版本的缓冲期
6. **日志规范**：使用 `logging.getLogger("manim").info` 统一日志出口
7. **dry-run 模式**：所有有副作用的命令（init/render/archive）都应支持 `--dry-run` 参数，先打印即将执行的动作用于确认
8. **进度反馈**：长时间操作（如批量渲染）应输出进度条或百分比，避免用户以为卡死

---

### 推广计划提示

| 角色 | 本章阅读重点 | 协作事项 |
|------|-------------|----------|
| 架构师/资深开发 | 掌握 entry_points 插件机制 | 设计团队插件架构 |
| 核心开发 | 实现 CLI 和配置扩展 | 开发课程脚手架插件 |
| 运维 | 管理插件发布的 CI/CD | 配置私有插件仓库 |
