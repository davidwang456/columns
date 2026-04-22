# -*- coding: utf-8 -*-
"""One-off: rename column/chapters/*.md to match H1 title (第nn章：主题.md)."""
import os
from collections import Counter

ROOT = os.path.dirname(os.path.abspath(__file__))
CH_DIR = os.path.join(ROOT, "chapters")


def windows_safe_filename(s: str) -> str:
    trans = str.maketrans(
        {
            "\\": "＼",
            "/": "／",
            ":": "：",
            "*": "＊",
            "?": "？",
            '"': "＂",
            "<": "＜",
            ">": "＞",
            "|": "｜",
        }
    )
    s = s.translate(trans)
    s = "".join(ch for ch in s if ord(ch) >= 32 and ch not in "\r\n\t")
    return s.rstrip(" .")


def main():
    skip = {"README.md", "answers-index.md"}
    mapping = []
    for fn in sorted(os.listdir(CH_DIR)):
        if fn in skip or not fn.endswith(".md"):
            continue
        path = os.path.join(CH_DIR, fn)
        with open(path, encoding="utf-8") as f:
            line1 = f.readline().rstrip("\n")
        if not line1.startswith("# "):
            print("SKIP no H1:", fn)
            continue
        title = line1[2:].strip()
        new_fn = windows_safe_filename(title) + ".md"
        if new_fn == fn:
            print("UNCHANGED", fn)
            continue
        new_path = os.path.join(CH_DIR, new_fn)
        if os.path.exists(new_path):
            raise SystemExit(f"TARGET EXISTS: {new_fn} (from {fn})")
        mapping.append((fn, new_fn))

    c = Counter(n for _, n in mapping)
    dups = [k for k, v in c.items() if v > 1]
    if dups:
        raise SystemExit("duplicate new names: " + repr(dups))

    for old, new in mapping:
        os.rename(os.path.join(CH_DIR, old), os.path.join(CH_DIR, new))
        print(f"{old} -> {new}")
    print("TOTAL", len(mapping))


if __name__ == "__main__":
    main()
