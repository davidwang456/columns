#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate per-chapter markdown under chapters/ from part1~part4. Run: python generate_chapter_files.py"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CHAP = ROOT / "chapters"
CHAP.mkdir(exist_ok=True)

CHAPTER_SLUGS = [
    "第01章-HBase定位-与RDBMS-ES-Kafka-HDFS的边界",
    "第02章-架构鸟瞰-Master-RegionServer-Meta-协调服务",
    "第03章-环境搭建-单机伪分布式或Docker",
    "第04章-hbase-shell-namespace与DDL",
    "第05章-数据模型-RowKey列族Qualifier-Cell时间戳",
    "第06章-Java入门-Configuration-Connection-Table",
    "第07章-Put-Get-Delete与行大小意识",
    "第08章-Scan基础-边界与caching-Scan非快照",
    "第09章-Admin建表-描述符列族预分区",
    "第10章-一致性与超时-行级原子与batch",
    "第11章-监控入门-指标日志延迟",
    "第12章-测试基础-等价类与RowKey边界",
    "第13章-RowKey设计-散列反转时间盐与热点",
    "第14章-Filter-FilterList代价与误用",
    "第15章-批量-BufferedMutator与batch背压",
    "第16章-异步客户端-AsyncConnection-AsyncTable",
    "第17章-checkAndPut-Increment与并发测试",
    "第18章-列族进阶-压缩缓存Bloom-TTL-MOB概念",
    "第19章-Snapshot-Export与恢复演练",
    "第20章-容量规划-Region堆磁盘与网络",
    "第21章-安全-Kerberos-ACL与网络",
    "第22章-客户端调优-超时重试-caching线程",
    "第23章-集成测试-MiniCluster与CI隔离",
    "第24章-性能测试-模型基线报告与SLA",
    "第25章-读路径-客户端到RegionServer",
    "第26章-写路径-MemStore与Flush",
    "第27章-WAL-滚动回放与可靠性",
    "第28章-HFile-StoreFile-块索引Bloom",
    "第29章-Compaction-Minor-Major与IO放大",
    "第30章-Region-Split-Merge-策略与P99",
    "第31章-Assignment与Balance-迁移与RIT",
    "第32章-MVCC与读点-行内可见性与Scan语义",
    "第33章-RPC与Protobuf-服务边界",
    "第34章-Coprocessor-Observer-Endpoint与发布风险",
    "第35章-Replication-DR拓扑与切换演练",
    "第36章-综合实战-亿级订单HBase与Elasticsearch",
    "第37章-选修-Phoenix-SQL层与HBase查询",
    "第38章-选修-Spark与Flink集成",
    "第39章-选修-MOB-大对象列存储",
    "第40章-选修-Quota-命名空间与表级限流配额",
]

TITLES = [
    "HBase 定位——与 RDBMS、ES、Kafka、HDFS 的边界",
    "架构鸟瞰——Master、RegionServer、Meta、协调服务",
    "环境搭建——单机、伪分布式或 Docker",
    "hbase shell——namespace、DDL、describe、count",
    "数据模型——RowKey、列族、Qualifier、Cell、时间戳",
    "Java 入门——Configuration、Connection、Table",
    "Put、Get、Delete 与行大小意识",
    "Scan 基础——边界、caching；Scan 非快照",
    "Admin 建表——描述符、列族、预分区",
    "一致性与超时——行级原子、batch、不确定完成",
    "监控入门——指标、日志、延迟",
    "测试基础——等价类、RowKey 边界、数据准备",
    "RowKey 设计——散列、反转时间、盐与热点",
    "Filter——FilterList、代价与误用",
    "批量——BufferedMutator、batch、背压",
    "异步客户端——AsyncConnection、AsyncTable",
    "checkAndPut、Increment 与并发测试",
    "列族进阶——压缩、缓存、Bloom、TTL、MOB 概念",
    "Snapshot、Export 与恢复演练",
    "容量规划——Region 数、堆内存、磁盘与网络",
    "安全——Kerberos、ACL、网络",
    "客户端调优——超时、重试、caching、线程",
    "集成测试——MiniCluster、HBaseTestingUtility、CI 隔离",
    "性能测试——模型、基线、报告与 SLA",
    "读路径——从客户端到 RegionServer",
    "写路径——MemStore、Flush",
    "WAL——滚动、回放与可靠性",
    "HFile / StoreFile——块、索引、Bloom",
    "Compaction——Minor / Major 与 IO 放大",
    "Region Split / Merge——策略与 P99",
    "Assignment 与 Balance——迁移与 RIT",
    "MVCC 与读点——行内可见性与 Scan 语义衔接",
    "RPC 与 Protobuf——服务边界",
    "Coprocessor——Observer、Endpoint 与发布风险",
    "Replication、DR 拓扑与切换演练",
    "综合实战——亿级订单 HBase + Elasticsearch",
    "选修：Phoenix（SQL 层与 HBase 之上查询）",
    "选修：Spark / Flink 与 HBase 集成",
    "选修：MOB（大对象 / 中等对象列）",
    "选修：Quota / 限流（命名空间与表级配额）",
]


def extract_chapters(text: str) -> list[str]:
    bodies: list[str] = []
    for p in re.split(r"\n---\n", text):
        p = p.strip()
        if not p.startswith("## 第 "):
            continue
        lines = p.split("\n", 1)
        if len(lines) < 2:
            continue
        bodies.append(lines[1].strip())
    return bodies


def fix_paths(body: str) -> str:
    return body.replace("](../../hbase-server/", "](../../../hbase-server/")


def build_chapter_markdown(num: int, slug: str, title: str, body: str, elective: bool) -> str:
    prev_n, next_n = num - 1, num + 1
    nav = []
    if prev_n >= 1:
        nav.append(f"上一章：[第 {prev_n} 章]({prev_n:02d}-{CHAPTER_SLUGS[prev_n - 1]}.md)")
    if next_n <= 40:
        nav.append(f"下一章：[第 {next_n} 章]({next_n:02d}-{CHAPTER_SLUGS[next_n - 1]}.md)")
    nav_line = " | ".join(nav)
    elective_note = "\n> **选修章**：与主线 1～36 章并行选修。\n" if elective else ""
    return f"""# 第 {num} 章：{title}

> 统一模板与检查表：[../00-template-pack.md](../00-template-pack.md)
> 官方文档：[HBase Reference Guide](https://hbase.apache.org/docs)
{elective_note}
{nav_line}

---

{body.strip()}

---

**返回目录**：[../README.md](../README.md)
"""


def write_manifest_and_omnibus(chapter_files: list[tuple[int, str, str, str]]) -> None:
    """chapter_files: (num, slug, title, full_markdown)"""
    # 章节文件列表.md：文件名与标题对照 + 链接
    rows = [
        "# 分章 Markdown 文件列表与路径",
        "",
        "命名规则：`{序号两位}-{CHAPTER_SLUGS 同 generate_chapter_files.py}.md`，与脚本中 `CHAPTER_SLUGS` 一一对应。",
        "",
        "由 `generate_chapter_files.py` 生成各单章文件时同步更新本文；运行：`python generate_chapter_files.py`。",
        "",
        "## 文件一览",
        "",
        "| 章 | Markdown 文件名 | 正文章标题 | 链接 |",
        "|----|-----------------|------------|------|",
    ]
    for num, slug, title, _ in chapter_files:
        fname = f"{num:02d}-{slug}.md"
        rows.append(
            f"| 第 {num} 章 | `{fname}` | {title} | [打开](chapters/{fname}) |"
        )
    rows.extend(
        [
            "",
            "## 相对路径（仓库内）",
            "",
            "单章正文目录：[chapters/](chapters/)",
            "",
            "全文镜像（下列文件含**全部 40 章完整 Markdown 正文**，便于检索与一次性导出）：",
            "",
            "- [章节全文合集.md](章节全文合集.md)",
            "",
        ]
    )
    (ROOT / "章节文件列表.md").write_text("\n".join(rows) + "\n", encoding="utf-8")

    # 章节全文合集.md：按序拼接各章完整内容
    omnibus_header = "\n".join(
        [
            "# HBase 培训讲义 — 分章全文合集",
            "",
            "> **自动生成**：由 `generate_chapter_files.py` 根据 `part1-basic.md`～`part4-capstone.md` 与选修模板拼接。",
            "> 修改讲义请编辑合订本或脚本内选修段落后重新运行脚本；**导航链接**以 `chapters/` 下单文件为准。",
            "",
            "---",
            "",
        ]
    )
    parts = []
    for num, slug, title, full_md in chapter_files:
        parts.append(f"<!-- 第 {num} 章：{title} | 源文件 chapters/{num:02d}-{slug}.md -->\n\n{full_md.strip()}")
    (ROOT / "章节全文合集.md").write_text(omnibus_header + "\n\n---\n\n".join(parts) + "\n", encoding="utf-8")


def main() -> None:
    p1 = (ROOT / "part1-basic.md").read_text(encoding="utf-8")
    p1 = re.sub(r"^# 基础篇：第 1～12 章\n+.*?(?=\n## 第 1 章)", "", p1, count=1, flags=re.S)
    b1 = extract_chapters(p1)
    assert len(b1) == 12, len(b1)

    p2 = (ROOT / "part2-intermediate.md").read_text(encoding="utf-8")
    p2 = re.sub(r"^# 中级篇：第 13～24 章\n+.*?(?=\n## 第 13 章)", "", p2, count=1, flags=re.S)
    b2 = extract_chapters(p2)
    assert len(b2) == 12, len(b2)

    p3 = (ROOT / "part3-advanced.md").read_text(encoding="utf-8")
    p3 = re.sub(r"^# 高级篇：第 25～35 章.*?(?=\n## 第 25 章)", "", p3, count=1, flags=re.S)
    idx = p3.find("\n## 高级篇阅读清单")
    if idx != -1:
        p3 = p3[:idx]
    b3 = extract_chapters(p3)
    assert len(b3) == 11, len(b3)

    p4 = (ROOT / "part4-capstone.md").read_text(encoding="utf-8")
    p4_body = re.sub(r"^# 第 36 章.*?\n\n---\n\n", "", p4, count=1, flags=re.S)
    m = re.search(r"^## 1）项目背景", p4_body, re.M)
    if m:
        p4_body = p4_body[m.start() :]
    p4_body = re.sub(r"\n---\n\n\*\*全文系列索引.*", "", p4_body, flags=re.S)
    b4 = [p4_body.strip()]

    all_bodies = [fix_paths(x) for x in b1 + b2 + b3] + b4
    assert len(all_bodies) == 36, len(all_bodies)

    electives = [
        """**受众：主【Dev】 辅【Ops、QA】 难度：高级（选修）**

### 1）项目背景

- **开发**：团队已熟悉原生 API，需要 **SQL**、二级索引、联合查询以降低接入成本。
- **运维**：多一层 Phoenix 服务（或嵌入式）与 schema 工具，版本需与 HBase 对齐。
- **测试**：SQL 语义与 HBase 底层仍受 RowKey、热点、Scan 代价约束，用例不能按「传统 OLTP」照搬。

### 2）项目设计（大师 × 小白）

- **小白**：「上了 Phoenix 就能随便 JOIN 了吧？」
- **大师**：「Phoenix 把 SQL **编译成 Scan/协处理器**；大表 JOIN、无索引字段过滤仍会 **扫很多数据**，要会看 **执行计划**。」
- **小白**：「和 Hive 呢？」
- **大师**：「Phoenix 偏 **低延迟在线查询**；Hive 偏 **批分析**。选型看延迟与数据新鲜度。」

### 3）项目实战

1. 阅读 [Apache Phoenix](https://phoenix.apache.org/) 官方文档中与 **HBase 版本兼容矩阵**。
2. 在测试环境：创建一张映射表，执行 `UPSERT`/`SELECT`/`CREATE INDEX`（若环境允许）；用 `EXPLAIN` 观察是否退化为全表 Scan。
3. 记录：盐（salt buckets）、主键与底层 RowKey 的对应关系。

### 4）项目总结

- **优点**：SQL 接入、二级索引能力（视版本与配置）、生态工具多。
- **缺点**：增加组件与运维面；错误 SQL 仍可压垮集群。
- **适用**：需 SQL 的运营后台、中等复杂度查询、与现有 HBase 表共存。
- **注意**：索引表额外存储与写入放大。
- **踩坑**：把 Phoenix 当无限扩展的 MySQL。
- **运维检查项**：Phoenix 与 HBase、JDK 版本锁表。
- **测试检查项**：核心查询必须有 EXPLAIN 与压测基线。""",
        """**受众：主【Dev】 辅【Ops】 难度：高级（选修）**

### 1）项目背景

- **开发**：数仓、实时计算需从 HBase **批量或流式**读写；需明确 **Catalog、分区、容错**。
- **运维**：Spark/Flink 作业资源与 HBase **Region 热点、scanner 超时** 相互影响。

### 2）项目设计（大师 × 小白）

- **小白**：「Spark 直接扫 HBase 全表行不行？」
- **大师**：「**行**，但要控 **分区并行度、缓存、列裁剪**；否则 RS 和网络先爆。」
- **小白**：「Flink 实时写呢？」
- **大师**：「注意 **幂等、sink 并行度与 RowKey 设计**；结合 **BufferedMutator** 或连接器提供的批量能力。」

### 3）项目实战

1. 阅读官方 [Spark](https://hbase.apache.org/book.html#spark) 与 [External APIs](https://hbase.apache.org/book.html#external_apis) 相关章节；Flink 查阅当前使用的 **Flink-HBase connector** 文档版本。
2. 做一个 **最小读**：从 HBase 读一张表写入文件或控制台（脱敏）；记录 `TableInputFormat` 或 DataSource API 用法。
3. 列出作业参数：`cache`, `batch`, 连接超时、重试。

### 4）项目总结

- **优点**：与大数据栈统一；适合 ETL、特征回填、离线对账。
- **缺点**：批扫对在线集群冲击大；需窗口与限流。
- **适用**：T+1 报表、批量修复、流式同步（配合 Kafka 等）。
- **注意**：离线任务避开业务高峰；优先 **RowKey 范围** 剪枝。
- **踩坑**：默认并行度过高打满单 RS。
- **运维检查项**：RS handler 队列与读延迟告警。
- **测试检查项**：空表、部分 Region 不可用时的失败语义。""",
        """**受众：主【Dev、Ops】 难度：高级（选修）**

### 1）项目背景

- **开发**：单列存 **中等大小** 二进制（图片缩略图、PDF、大 JSON）时，普通列易导致 **flush/compaction 与读写放大** 问题。
- **运维**：MOB 有 **独立文件与合并策略**，需监控空间与合并任务。

### 2）项目设计（大师 × 小白）

- **小白**：「MOB 就是把大字段扔 HDFS？」
- **大师**：「MOB 是 HBase 的 **中等对象管理**：协调对象与 WAL/HFile 生命周期；**超大** 仍建议对象存储。」
- **小白**：「所有大字段都开 MOB？」
- **大师**：「只给 **尺寸与访问模式合适** 的列族；要读官方 **阈值与合并** 说明。」

### 3）项目实战

1. 阅读官方 [MOB](https://hbase.apache.org/book.html#mob)。
2. 在测试表上对 **一个列族** 启用 MOB（按文档属性）；对比启用前后 **同样写入** 下的表大小、Get 延迟（粗略）。
3. 记录：**compaction 类型**、是否需 **MOB 压缩任务**（视版本）。

### 4）项目总结

- **优点**：较优雅地托管中等对象；减少巨型 Cell 对普通路径的伤害。
- **缺点**：配置与运维复杂；版本行为差异大。
- **适用**：文档、图片元数据、较大半结构化 payload。
- **注意**：备份、snapshot 与 MOB 文件一致性按文档操作。
- **踩坑**：MOB + 错误 RowKey 热点叠加 IO 风暴。
- **运维检查项**：MOB 相关目录磁盘与定时任务。
- **测试检查项**：对象边界大小（略小于/等于/大于 MOB 阈值）。""",
        """**受众：主【Ops】 辅【Dev】 难度：高级（选修）**

### 1）项目背景

- **运维**：多租户共用集群时，需限制 **表或命名空间** 的请求与空间，避免单一业务拖垮全局。
- **开发**：应用需处理 **配额超限** 的异常与退避。

### 2）项目设计（大师 × 小白）

- **小白**：「Quota 像 K8s limit？」
- **大师**：「类似 **多维度预算**：读写速率、空间等（**以当前版本支持项为准**）；超限会 **拒绝或 throttle**。」
- **小白**：「限流了业务报错怎么办？」
- **大师**：「**客户端重试 + 降速 + 扩容或拆分集群**；根因常是热点或批量任务未错峰。」

### 3）项目实战

1. 阅读官方 [Quotas](https://hbase.apache.org/book.html#quotas) 与 [Throttle](https://hbase.apache.org/book.html#throttle)（章节名随版本可能略有不同）。
2. 在测试环境对某 **namespace** 设置 **Throttle**（若启用 Quota 功能）；用压测观察限流行为或日志关键字（以实际版本为准）。
3. 写一段 Runbook：**谁审批、如何临时上调、如何回滚**。

### 4）项目总结

- **优点**：保护共享集群；公平性可量化。
- **缺点**：配置错误会导致「莫名失败」；需与业务对齐。
- **适用**：多租户平台、多团队共用 HBase。
- **注意**：Quota 与 **Region 分布、RS 能力** 联动，不是万能药。
- **踩坑**：只加限流不治理热点 RowKey。
- **运维检查项**：配额变更审计；告警接入。
- **测试检查项**：超限后的错误码与重试可恢复性。""",
    ]

    all_bodies.extend(electives)

    chapter_files: list[tuple[int, str, str, str]] = []
    for i in range(40):
        num = i + 1
        slug = CHAPTER_SLUGS[i]
        title = TITLES[i]
        body = all_bodies[i]
        elective = num >= 37
        full_md = build_chapter_markdown(num, slug, title, body, elective)
        (CHAP / f"{num:02d}-{slug}.md").write_text(full_md, encoding="utf-8")
        chapter_files.append((num, slug, title, full_md))

    write_manifest_and_omnibus(chapter_files)

    print("Wrote 40 files to", CHAP)
    print("Wrote", ROOT / "章节文件列表.md")
    print("Wrote", ROOT / "章节全文合集.md")


if __name__ == "__main__":
    main()
