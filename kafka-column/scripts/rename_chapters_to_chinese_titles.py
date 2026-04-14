# -*- coding: utf-8 -*-
"""将 chapters/*.md 重命名为「第 n 章：主题.md」（与首行 # 标题一致；非法路径字符替换为全角）。"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CH = ROOT / "chapters"


def title_to_safe_filename(title: str) -> str:
    out: list[str] = []
    for ch in title:
        if ch in "\r\n":
            continue
        if ch == ":":
            out.append("\uff1a")
        elif ch == "/":
            out.append("\uff0f")
        elif ch == "\\":
            out.append("\uff3c")
        elif ch == "?":
            out.append("\uff1f")
        elif ch == "*":
            out.append("\uff0a")
        elif ch == '"':
            out.append("\uff02")
        elif ch == "<":
            out.append("\uff1c")
        elif ch == ">":
            out.append("\uff1e")
        elif ch == "|":
            out.append("\uff5c")
        else:
            out.append(ch)
    base = "".join(out).strip().rstrip(".")
    return base + ".md"


def collect_mapping() -> list[tuple[str, str, str]]:
    pairs: list[tuple[str, str, str]] = []
    for p in sorted(CH.glob("*.md")):
        first = p.read_text(encoding="utf-8").split("\n", 1)[0]
        if not first.startswith("# "):
            raise SystemExit(f"Missing H1: {p}")
        title = first[2:].strip()
        new_name = title_to_safe_filename(title)
        pairs.append((p.name, new_name, title))
    return pairs


def main() -> None:
    pairs = collect_mapping()
    new_names = [n for _, n, _ in pairs]
    dup = {n for n in new_names if new_names.count(n) > 1}
    if dup:
        raise SystemExit(f"Duplicate target filenames: {dup}")

    # 两阶段重命名，避免目标名互相覆盖
    tmp_dir = CH
    for i, (old, new, _) in enumerate(pairs):
        if old == new:
            continue
        tmp = tmp_dir / f".__rename_tmp_{i:02d}__.md"
        (CH / old).rename(tmp)

    for i, (old, new, _) in enumerate(pairs):
        if old == new:
            continue
        tmp = tmp_dir / f".__rename_tmp_{i:02d}__.md"
        final = CH / new
        tmp.rename(final)

    for old, new, title in pairs:
        print(f"{old} -> {new}")


if __name__ == "__main__":
    main()
