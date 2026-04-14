# -*- coding: utf-8 -*-
"""从 guider.md 拆分章节并附加扩展块，生成 column/第N章 标题.md"""
import json
import re
from pathlib import Path
from typing import Optional, Tuple

ROOT = Path(__file__).resolve().parent
GUIDER = ROOT / "guider.md"
EXPANSIONS = ROOT / "chapter_expansions.json"


def chapter_md_filename(title_line: str) -> str:
    """与正文标题一致；半角冒号等在 Windows 文件名中非法，改为全角或其它安全字符。"""
    s = title_line.strip()
    for a, b in (
        (":", "："),
        ("*", "＊"),
        ("?", "？"),
        ('"', "＂"),
        ("<", "＜"),
        (">", "＞"),
        ("|", "｜"),
        ("\\", "＼"),
        ("/", "／"),
    ):
        s = s.replace(a, b)
    return s + ".md"


def part_for_chapter(n: int) -> Tuple[str, str]:
    if 1 <= n <= 10:
        return "第一部分：基础入门篇（第1-10章）", "1"
    if 11 <= n <= 22:
        return "第二部分：核心能力篇（第11-22章）", "2"
    if 23 <= n <= 32:
        return "第三部分：高级进阶篇（第23-32章）", "3"
    if 33 <= n <= 40:
        return "第四部分：专题实战篇（第33-40章）", "4"
    return "", ""


def strip_trailing_hr(body: str) -> str:
    """去掉章末与下一章之间的 ---，避免与扩展区块重复。"""
    body = body.replace("\r\n", "\n").rstrip()
    while True:
        new_body = re.sub(r"\n---\s*$", "", body)
        if new_body == body:
            break
        body = new_body.rstrip()
    return body


def normalize_headings(body: str) -> str:
    """首行 ### 第N章 -> # 第N章 标题；其余 #### -> ##（与一级标题形成正确层级）"""
    lines = body.splitlines()
    if not lines:
        return body
    first = lines[0]
    m = re.match(r"^### (第\d+章\s+.+)$", first.strip())
    if m:
        lines[0] = f"# {m.group(1).strip()}"
    out = []
    for line in lines[1:]:
        if line.startswith("#### "):
            line = "## " + line[5:]
        out.append(line)
    return "\n".join([lines[0]] + out)


def build_expansion_block(
    exp: dict,
    prev_name: Optional[str],
    next_name: Optional[str],
    prev_title: str,
    next_title: str,
) -> str:
    nav = []
    if prev_name and prev_title:
        nav.append(f"上一章：[{prev_title}]({prev_name})")
    if next_name and next_title:
        nav.append(f"下一章：[{next_title}]({next_name})")
    nav_md = " | ".join(nav) if nav else ""

    return f"""

---

## 编者扩展

> **本章导读**：{exp["hook"]}

### 趣味角

{exp["fun"]}

### 实战演练

{exp["lab"]}

### 深度延伸

{exp["deep"]}

---

{nav_md}

*返回 [专栏目录](README.md)*
"""


def main() -> None:
    text = GUIDER.read_text(encoding="utf-8")
    expansions: list = json.loads(EXPANSIONS.read_text(encoding="utf-8"))
    if len(expansions) != 40:
        raise SystemExit(f"expected 40 expansions, got {len(expansions)}")

    # 分割：各章从 ### 第N章 开始
    chapter_starts = list(re.finditer(r"^### (第(\d+)章)\s+(.+)$", text, re.MULTILINE))
    appendix_m = re.search(r"^## 附录：", text, re.MULTILINE)

    chapters = []
    for i, m in enumerate(chapter_starts):
        num = int(m.group(2))
        title_line = m.group(1) + " " + m.group(3).strip()
        start = m.start()
        if i + 1 < len(chapter_starts):
            end = chapter_starts[i + 1].start()
        else:
            end = appendix_m.start() if appendix_m else len(text)
        body = strip_trailing_hr(text[start:end])
        chapters.append((num, title_line, body))

    written = []
    for idx, (num, title_line, body) in enumerate(chapters):
        fname = chapter_md_filename(title_line)
        part_name, _ = part_for_chapter(num)
        exp = expansions[num - 1]

        prev_f = chapter_md_filename(chapters[idx - 1][1]) if num > 1 else None
        next_f = chapter_md_filename(chapters[idx + 1][1]) if num < 40 else None
        prev_title = chapters[idx - 1][1] if idx > 0 else ""
        next_title = chapters[idx + 1][1] if idx + 1 < len(chapters) else ""

        normalized = normalize_headings(body)
        front = f"""---
title: "{title_line}"
part: "{part_name}"
chapter: {num}
---

"""
        expansion = build_expansion_block(exp, prev_f, next_f, prev_title, next_title)
        out = front + normalized + expansion
        out_path = ROOT / fname
        out_path.write_text(out, encoding="utf-8")
        written.append((num, title_line, fname))

    # 附录
    if appendix_m:
        appendix = text[appendix_m.start() :].strip()
        # 附录标题提升
        appendix = re.sub(r"^## 附录：", "# 附录：", appendix, count=1, flags=re.MULTILINE)
        (ROOT / "appendix-resources.md").write_text(
            "---\ntitle: \"附录：Istio学习资源与社区\"\n---\n\n" + appendix + "\n",
            encoding="utf-8",
        )

    # README 目录
    lines = [
        "# Istio 全彩实录 | 专栏目录",
        "",
        "主文档：[guider.md](guider.md)（完整合并版）。以下为按章拆分的 Markdown，每章含 **编者扩展**（趣味 / 实战 / 深度）。",
        "",
    ]
    for num, title_line, fname in written:
        part_name, _ = part_for_chapter(num)
        if num == 1 or num == 11 or num == 23 or num == 33:
            if num != 1:
                lines.append("")
            lines.append(f"## {part_name.split('（')[0]}")
            lines.append("")
        # 链接文案与各章首行标题一致，例如：第1章 Istio架构详解：掌控微服务的中枢神经系统
        lines.append(f"- [{title_line}]({fname})")
    lines.extend(
        [
            "",
            "## 附录",
            "",
            "- [学习资源与社区](appendix-resources.md)",
            "",
        ]
    )
    (ROOT / "README.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {len(written)} chapters + appendix + README")


if __name__ == "__main__":
    main()
