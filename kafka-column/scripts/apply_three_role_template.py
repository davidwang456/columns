# -*- coding: utf-8 -*-
"""按《技术专栏优化模板》为各章补上三角色读法提示、小胖开场、运维/测试提要、跨部门提要（幂等）。"""
from __future__ import annotations

import re
from pathlib import Path

from chapter_three_role_openers import (
    CROSS_DEPT_BLOCK,
    OPS_TEST_BLOCK,
    READ_HINT_BLOCK,
    XIAO_PANG_LINE,
)

ROOT = Path(__file__).resolve().parents[1]
CH = ROOT / "chapters"

OLD_H2 = "## 2. 项目设计：大师与小白的对话"
NEW_H2 = "## 2. 项目设计：小胖、小白与大师的对话"
READ_MARKER = "**读法提示**：本章「项目设计」为 **小胖 / 小白 / 大师**"


def chapter_num_from_name(name: str) -> int | None:
    m = re.match(r"第\s*(\d+)\s*章：", name)
    return int(m.group(1)) if m else None


def insert_read_hint(text: str) -> str:
    if READ_MARKER in text:
        return text
    if "## 1. 项目背景" not in text:
        return text
    # 在「背景」标题后紧接读法提示（不改变原有首段正文顺序时，插在第一段前）
    text = re.sub(
        r"(## 1\. 项目背景)\n(\r?\n)?",
        r"\1\n" + READ_HINT_BLOCK,
        text,
        count=1,
    )
    return text


def insert_ops_test(text: str) -> str:
    if "### 3.4 运维视角" in text:
        return text
    sep = "\n---\n\n## 4. 项目总结"
    if sep not in text:
        return text
    return text.replace(sep, OPS_TEST_BLOCK + sep, 1)


def insert_cross_dept(text: str) -> str:
    if "### 4.6 跨部门协作提要" in text:
        return text
    bridge = "\n---\n\n## 附录（可选）"
    s4 = "## 4. 项目总结"
    start = text.find(s4)
    if start == -1:
        return text
    pos = text.find(bridge, start)
    if pos == -1:
        return text
    return text[:pos] + CROSS_DEPT_BLOCK + text[pos:]


def insert_xiao_pang(text: str, n: int) -> str:
    """仅在「第 2 节」标题后插入小胖开场（读法提示里也会出现「小胖」字样，不能用全文判断）。"""
    opener = XIAO_PANG_LINE.get(n)
    if not opener or NEW_H2 not in text:
        return text
    pos = text.find(NEW_H2)
    if pos == -1:
        return text
    after_h2 = text[pos + len(NEW_H2) :].lstrip()
    if after_h2.startswith("**小胖**"):
        return text
    needle = NEW_H2 + "\n\n"
    if needle not in text:
        return text
    return text.replace(needle, NEW_H2 + "\n\n" + opener + "\n\n", 1)


def patch(text: str, n: int) -> str:
    if OLD_H2 in text:
        text = text.replace(OLD_H2, NEW_H2)

    text = insert_read_hint(text)
    text = insert_ops_test(text)
    text = insert_cross_dept(text)
    text = insert_xiao_pang(text, n)
    return text


def main() -> None:
    for p in sorted(CH.glob("*.md")):
        n = chapter_num_from_name(p.name)
        if n is None:
            continue
        raw = p.read_text(encoding="utf-8")
        new = patch(raw, n)
        if new != raw:
            p.write_text(new, encoding="utf-8")
            print("updated", p.name)


if __name__ == "__main__":
    main()
