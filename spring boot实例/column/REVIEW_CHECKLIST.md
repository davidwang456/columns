# 专栏终稿对照清单（template.md）

撰写或修订任意章节时，按 [template.md](template.md) 与本清单自检。试点章节 [027](chapters/第 027 章：spring-boot-data-jpa —— Spring Data JPA 与仓库.md)、[054](chapters/第 054 章：spring-boot-webmvc —— Spring MVC 与 Boot.md)、[112](chapters/第 112 章：spring-boot-actuator —— Actuator 端点与运维 API.md) 为**结构与字数标杆**。

## 1 项目背景（约 500 字）

| 检查项 | 说明 |
|--------|------|
| 业务场景 | 有拟真或真实业务线，非纯概念堆砌 |
| 痛点 | 至少覆盖性能、一致性、可维护性中的若干维度 |
| 可选 | 流程图或示意图（Mermaid 等） |

## 2 项目设计 — 剧本对话（约 1200 字）

| 检查项 | 说明 |
|--------|------|
| 角色 | 小胖、小白、大师三人出场，话风符合模板 |
| 结构 | 小胖开球 1–2 轮 → 小白追问 2–3 轮 → 大师解答并递进；循环 2–3 次 |
| 技术映射 | 关键回合有「技术映射」金句 |
| 批量章说明 | 非试点章由 `scripts/chapter_rich_content.py` + `generate_chapters.py` 生成，结构与试点一致；若需更长篇幅可再扩写案例与代码 |

## 3 项目实战（约 1500–2000 字）

| 检查项 | 说明 |
|--------|------|
| 环境 | 依赖、版本、最小配置（Maven/Gradle/Docker 等） |
| 分步 | 每步：目标、代码、运行结果（文字描述）、坑 |
| 清单 | 仓库链接或附录占位 |
| 验证 | 单元测试或 curl/命令行 |

## 4 项目总结（约 500–800 字）

| 检查项 | 说明 |
|--------|------|
| 优缺点 | 各 3–5 点，表格更佳 |
| 场景 | 适用 3–5、不适用 1–2 |
| 注意事项 | 配置、版本、安全 |
| 踩坑 | 3 个典型故障 + 根因 |
| 思考题 | 2 道；答案指向 [appendix/thinking-answers.md](appendix/thinking-answers.md) 或下一章 |
| 推广 | 开发/运维/测试协作提示 |

## 专栏整体

| 检查项 | 说明 |
|--------|------|
| 单章字数 | 3000–5000 字（批量占位章可在定稿时补全） |
| 基础/中级/高级 | 每章内部覆盖概念、实战、进阶（原理/扩展） |
| 独立成文件 | 每模块一章，命名 `第 NNN 章：spring-boot-xxx —— 标题.md` |

## 索引与脚本

- 总目录：[INDEX.md](INDEX.md)
- 批量生成：[scripts/generate_chapters.py](scripts/generate_chapters.py)（跳过试点三章；输出文件名由 [scripts/chapter_rich_content.py](scripts/chapter_rich_content.py) 的 `chapter_filename()` 决定）
- 批量重命名（旧 `NNN-slug.md` → 新格式）：[scripts/rename_chapters.py](scripts/rename_chapters.py)
