#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""One-off or idempotent rename: NNN-slug.md -> 第 NNN 章：slug —— title.md"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHAPTERS = ROOT / "chapters"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from chapter_rich_content import chapter_filename  # noqa: E402
from generate_chapters import MODULES  # noqa: E402


def write_index() -> None:
    lines = [
        "# Spring Boot 专栏总索引（128 章）",
        "",
        "本索引与 `spring-boot-main/spring-boot-main/module` 下一级目录 **一一对应**。",
        "",
        "| 序号 | 模块 | 章节文件 |",
        "|------|------|----------|",
    ]
    for num, slug, title_zh in MODULES:
        fn = chapter_filename(num, slug, title_zh)
        link = f"[chapters/{fn}](chapters/{fn})"
        lines.append(f"| {num:03d} | {slug} | {link} |")
    (ROOT / "INDEX.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("Wrote INDEX.md")


def main() -> None:
    renamed = 0
    for num, slug, title_zh in MODULES:
        old = CHAPTERS / f"{num:03d}-{slug}.md"
        new = CHAPTERS / chapter_filename(num, slug, title_zh)
        if not old.exists():
            continue
        if new.exists() and old.resolve() != new.resolve():
            raise SystemExit(f"Refusing to overwrite existing: {new}")
        old.rename(new)
        renamed += 1
        print(f"{old.name} -> {new.name}")
    print(f"Renamed: {renamed}")
    write_index()


if __name__ == "__main__":
    main()
