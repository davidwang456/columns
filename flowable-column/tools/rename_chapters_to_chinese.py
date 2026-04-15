# -*- coding: utf-8 -*-
"""Rename chapter files to match H1: 第 n 章：主题——副标题.md (Windows-safe)."""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1] / "chapters"
# Windows forbidden: \ / : * ? " < > |
WIN_BAD = {
    "/": "／",
    "\\": "＼",
    ":": "：",  # ASCII colon only; Chinese ： unchanged if we only replace ASCII
    "*": "＊",
    "?": "？",
    '"': "＂",
    "<": "《",
    ">": "》",
    "|": "｜",
}


def safe_filename(title: str) -> str:
    s = title
    for a, b in WIN_BAD.items():
        s = s.replace(a, b)
    return s.strip()


def main():
    for path in sorted(ROOT.glob("*.md")):
        first = path.read_text(encoding="utf-8").splitlines()[0]
        if not first.startswith("# "):
            print("skip (no H1):", path.name)
            continue
        title = first[2:].strip()
        new_name = safe_filename(title) + ".md"
        new_path = path.parent / new_name
        if path.name == new_name:
            continue
        if new_path.exists() and new_path != path:
            print("collision:", new_name, "for", path.name)
            continue
        print(path.name, "->", new_name)
        path.rename(new_path)


if __name__ == "__main__":
    main()
