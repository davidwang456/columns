# -*- coding: utf-8 -*-
"""
将「2）项目设计」标题改为三人，插入角色分工引导语；
将对话中的 **小白** 提问行按出现顺序交替改为 **小胖**（偶数索引）/ **小白**（奇数索引）。
"""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1] / "chapters"
START_NEW = "## 2）项目设计（小胖 × 小白 × 大师）"
START_OLD = "## 2）项目设计（大师与小白）"
GUIDE = (
    "> **角色分工**：**小胖**（爱吃爱玩、不求甚解）用生活化、口语化的方式把话题「开球」，先把问题问出来；"
    "**小白**（喜静、喜深入）负责追问原理、边界、风险与可比方案；"
    "**大师**（资深技术 Leader）把业务约束与技术选型说透，善打比方、由浅入深。\n\n"
)
TABLE = "\n### 一页纸决策表"


def main():
    for path in sorted(ROOT.glob("*.md")):
        t = path.read_text(encoding="utf-8")
        t = t.replace(START_OLD, START_NEW, 1)
        if START_NEW not in t:
            path.write_text(t, encoding="utf-8")
            continue
        if "> **角色分工**：**小胖**" not in t:
            t = t.replace(START_NEW + "\n", START_NEW + "\n" + GUIDE, 1)

        pos = t.find(START_NEW)
        if pos < 0:
            path.write_text(t, encoding="utf-8")
            continue
        end = t.find(TABLE, pos)
        if end < 0:
            path.write_text(t, encoding="utf-8")
            continue

        segment = t[pos:end]
        lines = segment.split("\n")
        q_idx = 0
        new_lines = []
        for line in lines:
            strip = line.strip()
            if strip.startswith(">"):
                new_lines.append(line)
                continue
            if strip.startswith("**小白**") and ("：" in line or ":" in line):
                if q_idx % 2 == 0:
                    line = line.replace("**小白**", "**小胖**", 1)
                q_idx += 1
            new_lines.append(line)
        new_seg = "\n".join(new_lines)
        t = t[:pos] + new_seg + t[end:]
        path.write_text(t, encoding="utf-8")


if __name__ == "__main__":
    main()
