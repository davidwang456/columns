# -*- coding: utf-8 -*-
"""Align column chapters with template.md checklist (idempotent)."""
from __future__ import annotations

import glob
import os

HERE = os.path.dirname(os.path.abspath(__file__))

MARKER_ROLE = "**角色（对齐 [template.md](../template.md)）**"
MARKER_APPENDIX = "### 完整代码清单与仓库附录（模板对齐）"
MARKER_RUN = "### 运行结果与测试验证（模板对齐）"
MARKER_ENV_ANY = "### 环境准备"

ROLE_BLOCK = """
> **角色（对齐 [template.md](../template.md)）**：**小胖**（生活化比喻、抛问题）· **小白**（原理、边界、风险与备选）· **大师**（选型与「**技术映射**」承接）。  
> **对话结构**：小胖开球 1～2 轮 → 小白追问 2～3 轮 → 大师解答并引出下一子话题；全文循环 **2～3 次**，覆盖本章核心概念。

"""

SPLIT = "\n---\n\n## 4 项目总结"


def build_section3_suffix(head: str) -> str:
    """Insert before SPLIT; skip parts already present."""
    if MARKER_APPENDIX in head:
        return ""
    has_env = MARKER_ENV_ANY in head
    has_run = MARKER_RUN in head
    parts: list[str] = []
    if not has_env:
        parts.append(
            "\n### 环境准备（模板对齐）\n\n"
            "- **依赖**：HiveServer2 + Beeline + HDFS（或 Docker），参见 [第 2 章](<第 2 章：HDFS 与 Hive 的最小可运行环境.md>)。\n"
            "- **版本**：以 [source/hive/pom.xml](../source/hive/pom.xml) 为准；仅在非生产库验证。\n"
            "- **权限**：目标库 DDL/DML 与 HDFS 路径写权限齐备。\n"
        )
    if not has_run:
        parts.append(
            "\n### 运行结果与测试验证（模板对齐）\n\n"
            "- 各步骤给出「预期 / 验证」；建议 `beeline -f` 批量执行。**自测回执**：SQL 文件链接 + 成功输出 + 失败 stderr 前 80 行。\n"
        )
    parts.append(
        "\n### 完整代码清单与仓库附录（模板对齐）\n\n"
        "- **本章清单**：合并上文可执行片段为单文件纳入团队 Git（建议 `column/_scripts/`）。\n"
        "- **上游参考**：<https://github.com/apache/hive>（对照本仓库 `source/hive`）。\n"
        "- **本仓库路径**：`../source/hive`。\n"
    )
    return "".join(parts)


def patch_file(path: str) -> tuple[str, list[str]]:
    rel = os.path.basename(path)
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    changes: list[str] = []

    anchor2 = "## 2 项目设计（约 1200 字）\n\n"
    if MARKER_ROLE not in text and anchor2 in text:
        text = text.replace(anchor2, anchor2 + ROLE_BLOCK, 1)
        changes.append("role_block")

    if SPLIT not in text:
        raise SystemExit(f"No section-4 anchor in {rel}")

    head, tail = text.split(SPLIT, 1)
    suffix = build_section3_suffix(head)
    if suffix:
        head = head.rstrip() + "\n" + suffix.lstrip("\n")
        changes.append("section3_template")
    new_text = head + SPLIT + tail

    if new_text != text:
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(new_text)
    return rel, changes


def main():
    files = sorted(glob.glob(os.path.join(HERE, "第*.md")))
    if len(files) != 38:
        print("WARN: expected 38 files, got", len(files))
    for p in files:
        rel, ch = patch_file(p)
        if ch:
            print(rel, ch)


if __name__ == "__main__":
    main()
