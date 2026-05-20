# 第40章：【高级篇综合实战】从零构建 Manim 课程生产平台

---

## 1. 项目背景

经过 39 章的系统学习，读者已经从 Manim 的零基础用户成长为能独立开发插件、优化渲染管线、搭建生产流水线的资深开发者。现在是时候将全部知识付诸实践——**从零构建一个完整的 Manim 课程生产平台**。

本章模拟一个真实的创业场景：一家技术教育公司准备用 Manim 批量生产"Python 全栈开发"系列课程（预计 100 集）。公司需要一个完整的课程生产平台，支持脚本编写、组件复用、批量渲染、质量检查和发布归档的全流程。

这个平台的架构设计需要整合全书的知识：
- **基础篇**：Mobject/Animation/坐标系（第 1-16 章）
- **中级篇**：组件化/音画同步/批量渲染/数据可视化（第 17-31 章）
- **高级篇**：源码理解/自定义渲染/插件开发/生产稳定性（第 32-39 章）

本章将展示平台的整体架构、核心模块实现和部署方案。

---

## 2. 剧本式交锋对话

> **场景**：项目启动会。白板上画着平台架构图，团队四人围坐。

**小胖**（抱着一大桶混合坚果）：

"39 章学完，现在你要我从零搭一个平台？这相当于把之前学的所有东西拼成一台机器。零件我都会造，但组装图在哪？"

**老罗**（项目负责人，拍着白板上的架构图）：

"这就是组装图。平台分六层，从下到上——"

```
┌─────────────────────────────────────────┐
│  6. 发布层: 视频归档 + 字幕 + 索引页面    │
├─────────────────────────────────────────┤
│  5. 质检层: 黑帧检测 + 时长校验 + 字体检查 │
├─────────────────────────────────────────┤
│  4. 渲染层: build_v2 + 并行 + 断点续渲    │
├─────────────────────────────────────────┤
│  3. 场景层: 课程脚本 + 组件库复用          │
├─────────────────────────────────────────┤
│  2. 数据层: CSV/YAML → DataLoader        │
├─────────────────────────────────────────┤
│  1. 基础层: manim.cfg + theme + 字体+Docker │
└─────────────────────────────────────────┘
```

"每一层对应前面某些章节的知识。基础层用第 11/12/39 章，数据层用第 23/24 章，场景层用第 15/25 章，渲染层用第 28/29/39 章，质检层用第 13/28 章，发布层用第 12/27 章。

这不是'再学一套新东西'——这是**把前 39 章的知识拼成一副完整的拼图**。"

**小白**（已经在搭建项目骨架）：

"我从基础层开始。第 1 步——用 Docker 封装一个包含 Python + FFmpeg + LaTeX + 中文字体的渲染镜像。第 2 步——在 `manim.cfg` 中统一配置 + `constants/theme.py` 统一调色板。第 3 步——把第 37 章的自定义组件包 `manim-edu-kit` 安装进去。

这三步完成后，任何人 `docker pull` 这个镜像，渲染输出就跟团队一致——字体、颜色、分辨率全统一。"

**大师**：

"我补充三个关键设计决策——

**决策一：镜头脚本用 YAML 驱动而非 Python 硬编码**

```yaml
# scripts/ep01_intro.yaml
title: "Python 全栈 · 第1集"
segments:
  - type: title_card
    text: "Web 开发基础"
    duration: 8
  - type: code_block
    file: "examples/hello_flask.py"
    highlight_lines: [1, 5, 8]
    duration: 20
  - type: architecture_diagram
    nodes: [Browser, Flask, Database]
    edges: [[0,1], [1,2]]
    duration: 15
```

场景代码变成一个通用的 YAML 解释器——读取 YAML，按顺序解释每段并渲染。教研老师不需要会 Python，写 YAML 就行。

**决策二：质检自动化**——渲染完成后自动运行质检脚本，检查：
- 黑帧检测（任何一帧全黑？可能是 bug）
- 时长校验（预期 120 秒，实际 ±5 秒？）
- 字幕完整性（`.srt` 的行数 ≥ YAML 中声明的旁白句数）

**决策三：渐进式部署**——先做最小可用版本（MVP）：1 个 Docker 镜像 + 1 个 YAML 解释器 + 1 个渲染脚本。验证能产出第 1 集后，再扩展为 100 集的批量生产。"

**小胖**（坚果快要吃完了）：

"你说 MVP——那 MVP 的镜头脚本长什么样？第 16 章的镜头脚本表格在平台里怎么映射成 YAML？我对着那个表格写 YAML 应该怎么下笔？"

**大师**（铺开一张更大的白纸）：

"问得好。镜头脚本表格是'设计稿'，YAML 是'施工图'。我用第 1 集的例子展示两者的映射关系——"

**镜头脚本（设计稿）**：

| 时间戳 | 画面内容 | 动画效果 | YAML 段类型 | 书籍章节映射 |
|--------|---------|---------|------------|------------|
| 0-8s | 标题"Python 全栈·第1集" | Write 逐字 | title_card | 第25章组件 |
| 8-20s | 欢迎文字 + 课程大纲 | FadeIn + wait | text | 第7/15章 |
| 20-40s | Flask 代码展示 + 行高亮 | Code + highlight_lines | code_block | 第26章 |
| 40-55s | 架构图：Browser→Flask→DB | FadeIn 节点 + Create 箭头 | architecture_diagram | 第26章 |
| 55-65s | 关键词总结卡片 | Write + Indicate | summary_card | 第10/25章 |
| 65-75s | 画面淡出 | FadeOut | (解释器自动处理) | 第3章 |

**对应的 YAML 施工图**：

```yaml
title: "Python 全栈 · 第1集 · Web 开发基础"
segments:
  - type: title_card
    text: "Python 全栈开发 · 第1集"
    subtitle: "Web 开发基础与 Flask 入门"
    duration: 8
  - type: text
    text: "本集你将学会：\n1. Flask 应用的基本结构\n2. 路由与视图函数\n3. Browser → Flask → DB 的请求链路"
    duration: 12
  - type: code_block
    file: "examples/hello_flask.py"
    language: "python"
    highlight_lines: [1, 5, 8, 12]
    duration: 20
  - type: architecture_diagram
    nodes: ["Browser", "Flask App", "PostgreSQL"]
    edges: [[0, 1], [1, 2], [2, 1]]
    duration: 15
  - type: summary_card
    items:
      - "每个 URL 对应一个视图函数"
      - "Flask 通过装饰器 @app.route 绑定路由"
      - "数据库查询在视图函数内部执行"
    duration: 10
```

"看到了吗？表格的每一行对应 YAML 的一个 segment。表格是给人看的，YAML 是给解释器看的。两者表述同一件事，只是格式不同。"

**小白**（已经开了新终端）：

"那第 16 章强调的'分段编码'在平台里怎么体现？总不能 YAML 解释器里把 6 段写成一个大函数吧？"

**大师**：

"正是要拆。每个 `seg_type` 对应解释器里的一个 `_render_xxx()` 方法——这就是'分段编码'在平台层面的映射。第 16 章是手动写 7 段代码，这里是**用 YAML 声明 6 段，解释器自动路由到对应渲染方法**。本质一样，但平台的抽象层级更高。"

> **技术映射**：第 16 章的 `# ===== 第 N 段 =====` 变成 YAML 的 `segments[].type`。第 16 章的手写 `self.play(...)` 变成解释器的 `self._render_xxx(seg)`。结构没变，表达方式升级了。

---

## 3. 项目实战

### 3.1 平台架构与目录结构

```bash
manim-platform/
├── docker/
│   ├── Dockerfile
│   └── entrypoint.sh
├── config/
│   ├── manim.cfg
│   └── theme.py
├── components/                  # manim-edu-kit 包
│   └── ...
├── scenes/
│   └── yaml_interpreter.py     # 通用 YAML 解释器
├── scripts/
│   ├── course_define.yaml      # 课程定义（镜头脚本）
│   ├── build_pipeline.py       # 全流程渲染 + 质检 + 发布
│   ├── quality_check.py       # 自动质检
│   └── archive_publish.py     # 发布归档
├── data/                        # CSV/JSON 数据源
├── assets/                      # 图片/字体/音频
├── output/                      # 最终产物
└── README.md
```

---

### 3.2 核心模块实现

#### 模块一：YAML 驱动场景解释器

**步骤目标**：实现一个通用 Scene，读取 YAML 镜头脚本并逐段渲染。

> 本节参考第 16 章的"分段编码"模式，将 YAML 的 6 个 segment 映射为解释器的 6 个渲染方法。

**分段编码——第 1 段：title_card 渲染方法**（映射至第 16 章第 1 段：开场标题）：

```python
# scenes/yaml_interpreter.py
from manim import *
import yaml
from pathlib import Path

class YAMLDrivenScene(Scene):
    """通用 YAML 驱动的场景解释器"""
    def __init__(self, script_path=None, **kwargs):
        super().__init__(**kwargs)
        self.script_path = script_path or "scripts/course_define.yaml"

    def construct(self):
        script = yaml.safe_load(Path(self.script_path).read_text(encoding="utf-8"))

        # 主标题（等价于第16章的 title + subtitle 段）
        title = Text(script["title"], font_size=40, color=BLUE, weight=BOLD)
        title.to_edge(UP, buff=0.4)
        self.play(Write(title), run_time=2)
        self.wait(0.5)

        # 逐段解释——每一段等价于第16章的一个 # ===== 第 N 段 =====
        for seg in script["segments"]:
            seg_type = seg.get("type", "text")

            if seg_type == "title_card":
                self._render_title_card(seg)
            elif seg_type == "code_block":
                self._render_code_block(seg)
            elif seg_type == "architecture_diagram":
                self._render_arch_diagram(seg)
            elif seg_type == "text":
                self._render_text(seg)
            elif seg_type == "summary_card":
                self._render_summary_card(seg)

            self.wait(1)

        # 收束（等价于第16章的第7段：结论+淡出）
        self.play(FadeOut(title), run_time=1)
        self.wait(0.5)
```

**第 2 段：text 渲染方法**（映射至第 16 章第 2 段：核心内容展示）：

```python
    def _render_text(self, seg):
        """渲染纯文本段落"""
        text = Text(seg["text"], font_size=28, color=WHITE, line_spacing=0.7)
        self.play(Write(text), run_time=2)
        self.wait(seg.get("duration", 3) - 2)
        self.play(FadeOut(text), run_time=1)
```

**第 3 段：title_card 渲染方法**（映射至第 16 章第 3 段：卡片式标题）：

```python
    def _render_title_card(self, seg):
        """渲染标题卡片"""
        card = RoundedRectangle(
            width=10, height=2, corner_radius=0.15,
            color=BLUE, fill_opacity=0.08, stroke_width=2,
        )
        text = Text(seg["text"], font_size=36, color=BLUE)
        text.move_to(card.get_center())

        subtitle = None
        if "subtitle" in seg:
            subtitle = Text(seg["subtitle"], font_size=20, color=TEXT_MUTED)
            subtitle.next_to(text, DOWN, buff=0.15)
            text.shift(UP * 0.2)

        elements = [card, text]
        if subtitle:
            elements.append(subtitle)
        self.play(FadeIn(card), *[Write(e) for e in elements[1:]], run_time=2)
        self.wait(seg.get("duration", 4) - 2)
```

**第 4 段：code_block 渲染方法**（映射至第 16 章第 4 段：技术细节展示）：

```python
    def _render_code_block(self, seg):
        """渲染代码块 + 行高亮"""
        code = Code(
            code=Path(seg["file"]).read_text(encoding="utf-8"),
            language=seg.get("language", "python"),
            font_size=20, line_spacing=0.6,
            background="rectangle",
            background_config={"stroke_color": GRAY, "fill_opacity": 0.08},
        )
        code.to_edge(LEFT, buff=0.5)
        self.play(FadeIn(code), run_time=2)

        for line_no in seg.get("highlight_lines", []):
            self.play(
                code.background_mobject[line_no - 1].animate.set_color(YELLOW_A).set_opacity(0.25),
                run_time=0.5,
            )

        total_highlight_time = 0.5 * len(seg.get("highlight_lines", []))
        self.wait(seg.get("duration", 10) - 2 - total_highlight_time)
        self.play(FadeOut(code), run_time=1)
```

**第 5 段：architecture_diagram 渲染方法**（映射至第 16 章第 5 段：关系图展示）：

```python
    def _render_arch_diagram(self, seg):
        """渲染架构图（节点 + 边）"""
        nodes_data = seg["nodes"]
        edges_data = seg["edges"]

        nodes = VGroup()
        for name in nodes_data:
            box = RoundedRectangle(width=2, height=1, corner_radius=0.1,
                                    color=BLUE, fill_opacity=0.1)
            label = Text(name, font_size=20, color=BLUE)
            label.move_to(box.get_center())
            nodes.add(VGroup(box, label))
        nodes.arrange(RIGHT, buff=1.5).shift(UP * 0.5)

        edges = VGroup()
        for u, v in edges_data:
            arrow = Arrow(nodes[u].get_right(), nodes[v].get_left(),
                          color=GRAY, buff=0.15, tip_length=0.15)
            edges.add(arrow)

        self.play(FadeIn(nodes), Create(edges), run_time=3)
        self.wait(seg.get("duration", 5) - 3)
```

**第 6 段：summary_card 渲染方法**（映射至第 16 章第 6 段：总结回扣）：

```python
    def _render_summary_card(self, seg):
        """渲染总结卡片"""
        items = seg.get("items", [])
        card = RoundedRectangle(width=9, height=len(items)*0.7 + 0.8,
                                corner_radius=0.15, color=GREEN,
                                fill_opacity=0.08, stroke_width=2)
        text_lines = VGroup()
        for i, item in enumerate(items):
            line = Text(f"• {item}", font_size=22, color=WHITE)
            text_lines.add(line)
        text_lines.arrange(DOWN, buff=0.2, aligned_edge=LEFT)
        text_lines.move_to(card.get_center())

        self.play(FadeIn(card), Write(text_lines), run_time=2)
        self.play(Indicate(card, color=GREEN, scale_factor=1.02), run_time=1)
        self.wait(seg.get("duration", 5) - 3)
        self.play(FadeOut(card), FadeOut(text_lines), run_time=1)
```

**对应的 YAML 镜头脚本**：

```yaml
# scripts/course_define.yaml
title: "Python 全栈 · 第1集 · Web 开发基础"
segments:
  - type: text
    text: "欢迎来到 Python 全栈开发课程"
    duration: 6
  - type: title_card
    text: "Flask 微框架"
    duration: 8
  - type: code_block
    file: "examples/hello_flask.py"
    language: "python"
    highlight_lines: [1, 5]
    duration: 12
  - type: architecture_diagram
    nodes: ["Browser", "Flask", "Database"]
    edges: [[0, 1], [1, 2]]
    duration: 10
```

**运行命令**：

```bash
manim -pqh scenes/yaml_interpreter.py YAMLDrivenScene
```

---

#### 模块二：自动化质检

**步骤目标**：渲染完成后自动检查产物质量。

```python
# scripts/quality_check.py
"""自动化质量检查"""
import subprocess, json
from pathlib import Path

def check_black_frames(video_path, threshold=0.02):
    """检测黑帧比例（黑帧可能表示渲染 Bug）"""
    cmd = [
        "ffmpeg", "-i", str(video_path),
        "-vf", "blackdetect=d=0.5:pix_th=0.1",
        "-f", "null", "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    black_lines = [l for l in result.stderr.splitlines() if "black_start" in l]
    black_ratio = len(black_lines) / 100  # 粗略估算
    return black_ratio < threshold, black_ratio

def check_duration(video_path, expected_min, expected_max):
    """检查视频时长是否在预期范围内"""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    duration = float(result.stdout.strip())
    ok = expected_min <= duration <= expected_max
    return ok, duration

def quality_check(video_path, expected_duration):
    """完整质量检查"""
    issues = []

    # 检查 1: 黑帧
    ok_black, black_ratio = check_black_frames(video_path)
    if not ok_black:
        issues.append(f"黑帧比例过高: {black_ratio:.2%}")

    # 检查 2: 时长
    ok_dur, actual_dur = check_duration(video_path, expected_duration - 5, expected_duration + 5)
    if not ok_dur:
        issues.append(f"时长异常: 预期 {expected_duration}s, 实际 {actual_dur:.1f}s")

    # 检查 3: 文件完整性
    if video_path.stat().st_size < 100 * 1024:
        issues.append(f"文件过小: {video_path.stat().st_size // 1024}KB")

    if issues:
        print(f"❌ {video_path.name}: " + "; ".join(issues))
    else:
        print(f"✅ {video_path.name}: 质检通过")
    return len(issues) == 0
```

---

#### 模块三：全流程构建脚本

**步骤目标**：整合渲染 + 质检 + 发布的全流程。

```python
# scripts/build_pipeline.py
"""全流程：渲染 → 质检 → 发布"""
import subprocess, sys, json, shutil
from pathlib import Path
from datetime import datetime

from quality_check import quality_check

def run_pipeline(script_file="scripts/course_define.yaml", episode="ep01", quality="h"):
    print(f"🚀 启动全流程: {episode}")

    # === 第 1 步：渲染 ===
    print("[1/3] 渲染中...")
    cmd = [
        sys.executable, "-m", "manim",
        "-q", quality,
        "scenes/yaml_interpreter.py", "YAMLDrivenScene",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"❌ 渲染失败:\n{result.stderr[-500:]}")
        return False

    # === 第 2 步：质检 ===
    print("[2/3] 质检中...")
    video_path = Path(f"build/media/videos/yaml_interpreter/{quality}p30/YAMLDrivenScene.mp4")
    if not video_path.exists():
        print("❌ 视频文件未找到")
        return False

    if not quality_check(video_path, expected_duration=60):
        print("⚠️ 质检未通过，但继续发布（人工审核）")

    # === 第 3 步：归档发布 ===
    print("[3/3] 归档发布中...")
    release_dir = Path(f"output/{episode}/{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    release_dir.mkdir(parents=True, exist_ok=True)

    shutil.copy2(video_path, release_dir / f"{episode}.mp4")
    # 复制字幕和镜头脚本
    subtitle_src = Path(f"build/media/subtitles/YAMLDrivenScene.srt")
    if subtitle_src.exists():
        shutil.copy2(subtitle_src, release_dir / f"{episode}.srt")
    shutil.copy2(script_file, release_dir / "script.yaml")

    # 生成索引
    index_html = f"""<html><body>
<h1>{episode}</h1>
<video controls src="{episode}.mp4"></video>
<p>生成时间: {datetime.now()}</p>
</body></html>"""
    (release_dir / "index.html").write_text(index_html, encoding="utf-8")

    print(f"✅ 发布完成: {release_dir}")
    return True

if __name__ == "__main__":
    episode = sys.argv[1] if len(sys.argv) > 1 else "ep01"
    run_pipeline(episode=episode)
```

**运行命令**：

```bash
python scripts/build_pipeline.py ep01
# 🚀 启动全流程: ep01
# [1/3] 渲染中...
# [2/3] 质检中...
# [3/3] 归档发布中...
# ✅ 发布完成: output/ep01/20250119_143000/
```

---

### 3.3 完整代码清单

```python
# scenes/yaml_interpreter.py —— YAMLDrivenScene
# scripts/course_define.yaml —— 镜头脚本示例
# scripts/quality_check.py —— 黑帧/时长/文件完整性检查
# scripts/build_pipeline.py —— 全流程渲染+质检+发布
# docker/Dockerfile —— 容器化（见第39章）
```

### 3.4 验收标准

| 验证项 | 操作 | 合格标准 |
|--------|------|----------|
| YAML 驱动 | 修改 YAML 后渲染 | 画面按 YAML 定义变化 |
| 黑帧检测 | 渲染一段含 bug 的视频 | 质检报告黑帧 |
| 时长校验 | 渲染后检查质检输出 | 时长在 ±5 秒内 |
| 全流程 | 运行 `build_pipeline.py ep01` | 输出目录含 MP4 + SRT + YAML |
| Docker 一致性 | 在 3 台机器上渲染 | 产物一致 |

---

## 4. 项目总结

### 优点 & 缺点

| 维度 | 优点 | 缺点 |
|------|------|------|
| YAML 驱动 | 教研人员无需学 Python，写 YAML 即可 | 复杂动画（含 Updater）难以用 YAML 表达 |
| 自动化质检 | 黑帧/时长/文件完整全自动 | 质检规则需持续维护（新 bug 类型出现） |
| 全流程整合 | 渲染→质检→发布一键完成 | 单点失败影响全流程（需增强容错） |
| 归档索引 | 每版本独立目录 + HTML 页面 | 多版本积累后磁盘占用大 |
| 可扩展性 | 新增 seg_type 只需扩展解释器 | 解释器代码随类型增多而膨胀 |

### 适用场景

| 场景 | 说明 |
|------|------|
| 课程生产基地 | 100+ 集的规模化课程产出 |
| 教研协作 | 教研写 YAML，开发维护平台 |
| 自动化内容更新 | 数据变化时自动重新渲染 |
| CI 持续交付 | 每次 Git push 自动渲染+质检 |
| 多语言版本 | 同一 YAML 切换语言文件生成不同语言版本 |

### 注意事项

1. **YAML 解释器的健壮性**：YAML 字段缺失时应有默认值，避免 `KeyError` 导致渲染中断。
2. **质检的误报率**：黑帧检测的阈值（`pix_th=0.1`）需根据实际场景调整。深色背景教学视频的"黑帧"与真正 bug 的"黑帧"可能混淆。
3. **平台维护成本**：平台代码本身也是需要持续维护的软件。建议至少配备 1 名开发负责平台维护，2 名教研负责课程内容。

### 常见踩坑经验

**故障一：YAML 解析报 `ScannerError` 但文件看起来正常**

根因：YAML 对缩进和特殊字符（冒号、引号）严格。含中文冒号"："的文本容易解析失败。

解决：含特殊字符的字符串用引号包裹：`text: "欢迎：Python 全栈"`。

**故障二：质检脚本误将深色画面判为黑帧**

根因：教学动画的背景是深色（`#0D1117`），`ffmpeg` 的 `blackdetect` 将深色误判为黑色。

解决：调低 `pix_th=0.05`（像素阈值），或只对预期为浅色画面的段落做黑帧检测。

**故障三：`build_pipeline.py` 在质检失败时仍标记"成功"**

根因：质检失败只打印 log 未设置非零退出码。

解决：`sys.exit(0 if all_checks_passed else 1)`，让 CI 感知失败。

**故障四：多集并行渲染时 `output/` 目录竞争**

根因：两个进程同时渲染第 1 集和第 2 集，都往 `output/` 写文件但目录名不同，不是问题。但都写 `build/media/` 下的中间产物可能冲突。

解决：每集使用独立的 `--media_dir` 参数。

**故障五：YAML 解释器的 `_render_arch_diagram` 边不可见**

根因：`Arrow` 的 `buff=0.15` 在节点间距小时可能被裁剪。

解决：增大 `arrange(RIGHT, buff=2.0)` 的间距，或减小箭头的 `buff=0.05`。

### 思考题

1. 在 YAML 解释器中新增一个 `type: chart` 段，支持从 CSV 文件自动生成折线图/柱状图。YAML 示例：`{type: chart, chart_type: line, data: "data/ep01_sales.csv", x_col: month, y_col: revenue, duration: 15}`。提示：复用第 20/23 章的 Axes + plot 能力。

2. 为平台设计一个 Web 管理界面（用 Flask/Streamlit），教研人员可以在浏览器中：上传 YAML 脚本 → 点击"渲染预览"（低清）→ 检查无误后点击"发布高清"。提示：后端用 `subprocess` 调用 `manim`，渲染结果用 `<video>` 标签嵌入 HTML。

---

### 推广计划与全书总结

恭喜你完成了本专栏全部 40 章的学习！从"Hello Manim"到一个完整的课程生产平台，你已经掌握了：

- **基础篇（16 章）**：让画面动起来
- **中级篇（15 章）**：让动画更专业
- **高级篇（9 章）**：让生产规模化

无论你是一线教师、技术布道师，还是工具链工程师，Manim 都已经成为你用代码讲故事的利器。现在，轮到你去制作下一支让观众"茅塞顿开"的动画了。

| 角色 | 本章阅读重点 | 协作事项 |
|------|-------------|----------|
| 架构师 | 平台整体架构设计 | 推动团队采纳 YAML 驱动模式 |
| 核心开发 | YAML 解释器和质检脚本 | 扩展 seg_type 库 |
| 教研人员 | 学习撰写 YAML 镜头脚本 | 提供前 5 集的课程脚本 |
| 运维 | 部署 Docker + CI 流水线 | 监控渲染平台的资源使用 |
