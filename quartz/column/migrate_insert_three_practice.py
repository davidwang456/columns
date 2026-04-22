# -*- coding: utf-8 -*-
"""
Insert three template-compliant 综合实战 chapters at 14, 29, 41;
renumber existing 00–38 → 00–13, 15–28, 30–40; update 思考题揭底 headings.
Run from repo root: python column/migrate_insert_three_practice.py
"""
from __future__ import annotations

import os
import re
import shutil

ROOT = os.path.dirname(os.path.abspath(__file__))
CH = os.path.join(ROOT, "chapters")
TMP = os.path.join(CH, "__mig_tmp__")


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


def remap_jiedi_heading(k: int) -> int:
    if k <= 13:
        return k
    if k <= 27:
        return k + 1
    if k <= 38:
        return k + 2
    return k


def remap_jiedi_in_text(text: str) -> str:
    def repl(m: re.Match[str]) -> str:
        k = int(m.group(1))
        return f"#### 第{remap_jiedi_heading(k):02d}章思考题揭底"

    return re.sub(r"#### 第(\d{2})章思考题揭底", repl, text)


def old_to_new_num(old: int) -> int:
    if old <= 13:
        return old
    if old <= 27:
        return old + 1
    return old + 2


def transform_existing(old_num: int, content: str) -> tuple[str, str]:
    """Return (new_num, new_content)."""
    new_num = old_to_new_num(old_num)
    lines = content.split("\n")
    m = re.match(r"^# 第\d{2}章：(.+)$", lines[0])
    if not m:
        raise ValueError(f"Bad H1 for old {old_num}")
    title_rest = m.group(1)
    lines[0] = f"# 第{new_num:02d}章：{title_rest}"
    body = "\n".join(lines[1:])
    body = remap_jiedi_in_text(body)
    return new_num, lines[0] + "\n" + body


def extract_between(content: str, start: str, end: str) -> tuple[str, str]:
    i = content.find(start)
    if i < 0:
        raise ValueError(f"missing {start!r}")
    j = content.find(end, i)
    if j < 0:
        raise ValueError(f"missing {end!r}")
    block = content[i:j].rstrip()
    new_content = content[:i].rstrip() + "\n\n" + content[j:].lstrip()
    return block, new_content


def main() -> None:
    if os.path.exists(TMP):
        shutil.rmtree(TMP)
    os.makedirs(TMP)

    # Load all chapters by old number
    by_old: dict[int, str] = {}
    old_fn: dict[int, str] = {}
    for fn in os.listdir(CH):
        if not fn.endswith(".md") or not fn.startswith("第"):
            continue
        if fn.startswith("__"):
            continue
        path = os.path.join(CH, fn)
        with open(path, encoding="utf-8") as f:
            text = f.read()
        m = re.match(r"^# 第(\d{2})章：", text)
        if not m:
            print("skip", fn)
            continue
        n = int(m.group(1))
        by_old[n] = text
        old_fn[n] = fn

    # Extract moved 揭底 blocks
    b13, c14 = extract_between(
        by_old[14],
        "#### 第13章思考题揭底",
        "### 思考题",
    )
    by_old[14] = c14

    b28_ans27, c28 = extract_between(
        by_old[28],
        "#### 第27章思考题揭底",
        "### 思考题",
    )
    by_old[28] = c28
    b28_ans27 = b28_ans27.replace(
        "#### 第27章思考题揭底", "#### 第28章思考题揭底", 1
    )

    idx = by_old[38].find("#### 第38章思考题揭底")
    if idx < 0:
        raise ValueError("no 38 jiedi")
    block38 = by_old[38][idx:]
    by_old[38] = by_old[38][:idx].rstrip()

    # SRE ch40: fix 思考题 pointer
    by_old[38] = by_old[38].replace(
        "（答案见本节「第38章思考题揭底」或 [答案索引](answers-index.md)）",
        "（答案见下一章或 [答案索引](answers-index.md)）",
    )

    # Write transformed old chapters to TMP
    for old in range(0, 39):
        if old not in by_old:
            continue
        new_num, new_text = transform_existing(old, by_old[old])
        m = re.match(r"^# 第\d{2}章：(.+)$", new_text.split("\n")[0])
        assert m
        stem = windows_safe_filename(new_text.split("\n")[0][2:])  # drop "# "
        out_fn = stem + ".md"
        out_path = os.path.join(TMP, out_fn)
        with open(out_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(new_text)
        print("moved", old, "->", new_num, out_fn)

    # New chapter 14 body (abbrev: load from separate write)
    from textwrap import dedent

    ch14 = dedent(
        """
        # 第14章：基础篇综合实战：券投放与 RAM 调度串联验收

        > **篇别**：基础篇（综合实战）
        > **建议篇幅**：3000–5000 字（含对话与代码）
        > **结构约束**：对齐 [专栏模板](../../column/template.md) 四段式；本章为模板「基础篇结束后独立综合实战」。

        ## 示例锚点

        | 类型 | 路径 |
        | --- | --- |
        | example1 | [SimpleExample.java](../../examples/src/main/java/org/quartz/examples/example1/SimpleExample.java) |
        | example8 | [CalendarExample.java](../../examples/src/main/java/org/quartz/examples/example8/CalendarExample.java) |

        ## 1 项目背景（约 500 字）

        ### 业务场景

        新零售「**券批次只在营业日投放**」：运营要求 **工作日白天整点** 写缓存，**法定假与公司维护窗** 一律跳过；审计要能对齐 **JobKey、TriggerKey、nextFireTime** 与 **排除日历** 的来源。团队已读完第00–13章，但缺少一次 **端到端签字**：从 RAM 配置到 **优雅停机**，证明「听得懂」能变成「跑得通」。

        ### 痛点放大

        - **概念散落**：会写 Cron 却不会解释 **Calendar 与 Trigger 的绑定顺序**。
        - **环境割裂**：本地 RAM 跑通，一上预发就忘了 **`shutdown` 语义** 与线程名观测。
        - **验收不可复制**：没有 **清单化步骤**，新人无法自证「基础篇结业」。

        ```mermaid
        flowchart LR
          J[JobDetail] --> T[Trigger]
          T --> C[Calendar]
          C --> S[Scheduler 生命周期]
        ```

        ## 2 项目设计（约 1200 字）

        **角色**：小胖 · 小白 · 大师

        ---

        **小胖**：这不就是把前面十几章的作业拼成一个大作业吗？我抄一遍 example1 行不行？

        **小白**：验收要是 **可回放证据**：线程 dump、`SchedulerMetaData`、Calendar 变更日志——抄例子没有这些怎么算过？

        **大师**：综合实战的价值是 **「约束下的拼装」**：同一 `JobKey` 上挂 **两个 Trigger**（整点 + 补偿），再用 **`HolidayCalendar` + 维护窗 Calendar`** 叠加；RAM 下重启丢失是预期，但你要 **书面声明** 为何本阶段接受 RAM。

        **技术映射**：**多 Trigger 单 JobDetail + Calendar 链 + 显式生命周期**。

        ---

        **小胖**：那我怎么证明「排除日真的排除了」？

        **小白**：边界是 **时区与「日切」**：如果业务以 **UTC** 存券，Calendar 却以 **服务器默认时区** 建，会不会出现「假跳过」？

        **大师**：用 **固定时钟的集成测试思路**：在日志里打印 **`fireTime` 与 `ScheduledFireTime`**，对维护窗前后各打一条；必要时把 **`quartz.scheduler.timeZone`** 写进验收表。

        **技术映射**：**可观测触发点 + 时区显式化**。

        ---

        **小胖**：停机呢？我 `kill -9` 算快速回收吗？

        **小白**：如果券写入缓存是 **at-least-once**，暴力杀进程会不会 **双写** 或 **半截写**？

        **大师**：综合演练里 **`shutdown(true)` + 超时 cap** 是默认答案；`kill -9` 只能作为 **混沌对照组**，并在 Runbook 里标红「禁止作为常规发布手段」。

        **技术映射**：**优雅停机与业务幂等键**。

        ## 3 项目实战（约 1500–2000 字）

        ### 环境准备

        - JDK 与 `./gradlew :examples:classes` 与第02章一致。
        - 仅用 **RAMJobStore** 与默认 `quartz.properties` 起步。

        ### 分步实现

        **步骤 1：目标** —— 从 example1 复制最小 `Scheduler` 启动骨架，把 **线程名前缀** 改成团队规范。

        ```java
        // 目标：日志中可检索「coupon-」前缀，便于与业务线程区分
        // SchedulerFactory sf = new StdSchedulerFactory();
        // Scheduler sched = sf.getScheduler();
        ```

        **验证（文字）**：启动后线程列表出现 `QuartzSchedulerThread` 与 `coupon-` worker。

        **步骤 2：目标** —— 绑定 `HolidayCalendar` 与 **维护窗** 两个 Calendar，挂到 **同一 Trigger**。

        **验证**：维护窗当天日志 **无触发**；窗后 **首 fire** 与 `nextFireTime` 对齐。

        **步骤 3：目标** —— 执行 `shutdown(true)`，观察 **在途 Job** 是否在 30s cap 内结束。

        **可能踩坑**：`scheduleJob` 后未 `start`；Calendar 未 `addCalendar` 就引用——对照第08、13章排错表。

        ### 完整代码清单

        - 本仓库 `examples/example1`、`examples/example8` 与 `quartz` 模块。

        ### 测试验证

        - 线程 dump 各截一张 **维护窗前后**；`Scheduler#getMetaData().getSummary()` 文本粘贴到验收表。

        ## 4 项目总结（约 500–800 字）

        ### 优点与缺点（对比「只考单章」）

        | 维度 | 串联验收 | 单章刷题 |
        | --- | --- | --- |
        | 迁移性 | 高 | 低 |
        | 成本 | 中 | 低 |

        ### 适用 / 不适用场景

        - **适用**：基础篇培训结业、入职第 2 周考核。
        - **不适用**：已直接上 JDBC 集群的团队（改做缩小版对照）。

        ### 注意事项

        - RAM **不**承诺重启续跑；验收表需声明阶段边界。

        ### 常见踩坑（生产案例）

        1. **只验 happy path**：维护窗边界未测。
        2. **Calendar 未版本化**：人事导入覆盖错环境。
        3. **把综合演练当生产方案**：未追加持久化评审。

        PLACEHOLDER_J13

        ### 思考题（答案见下一章或 [答案索引](answers-index.md)）

        1. 若维护窗跨天，Calendar 与 Cron 的「日界」如何对齐验收？
        2. 同一 `JobKey` 上双 Trigger 时，`@DisallowConcurrentExecution` 对「补偿触发」有何可见影响？

        ### 推广计划提示

        - **测试**：维护窗用例集沉淀为数据驱动表。
        - **运维**：把线程前缀纳入 **告警路由**。
        - **开发**：下一章进入 **Misfire**（第15章）精读。
        """
    ).strip()
    ch14 = ch14.replace("PLACEHOLDER_J13", b13.strip())

    ch29 = dedent(
        """
        # 第29章：中级篇综合实战：对账集群与观测闭环演练

        > **篇别**：中级篇（综合实战）
        > **建议篇幅**：3000–5000 字（含对话与代码）
        > **结构约束**：对齐 [专栏模板](../../column/template.md) 四段式；本章为模板「中级篇结束后独立综合实战」。

        ## 示例锚点

        | 类型 | 路径 |
        | --- | --- |
        | 文档 | [docs/index.md](../../docs/index.md) |

        ## 1 项目背景（约 500 字）

        ### 业务场景

        账务 **nightly 对账** 已切到 **JDBC JobStore + 双实例**：白天还有 **高优先级补偿 Trigger**。SRE 要求 **无双跑**、**misfire 可解释**、**RMI 默认关闭**；开发要把第14–28章变成 **一张 Runbook + 一次联调记录**。

        ### 痛点放大

        - **指标碎片化**：Listener、DB 锁、线程池各说各话。
        - **压测与生产不一致**：`batchTriggerAcquisitionMaxCount` 盲调。
        - **远程运维入口失控**：RMI 误开等于 **扩大爆炸半径**。

        ```mermaid
        flowchart TB
          M[Misfire 策略] --> J[JDBC JobStore]
          J --> C[Cluster 锁]
          C --> T[ThreadPool]
          T --> O[Listener 观测]
        ```

        ## 2 项目设计（约 1200 字）

        **角色**：小胖 · 小白 · 大师

        ---

        **小胖**：我们就把两个实例都拉起来，看谁抢到 Trigger，算不算验收？

        **小白**：要看 **失败面**：从库延迟、时钟 skew、行锁等待——抢到了也可能 **错账**。

        **大师**：中级综合的验收是 **「可恢复 + 可解释」**：预置 **一次人为 misfire**、一次 **线程池打满**，Runbook 里写清 **先看哪三张表 / 哪三个指标**。

        **技术映射**：**JobStore 真相来源 + 观测分层**。

        ---

        **小胖**：RMI 我全关了就安全吧？

        **小白**：那运维还要 **pause**，走 API 网关行不行？

        **大师**：关 RMI 是 **减暴露面**；运维改走 **受控 API**，把 **审计日志** 与 **双人复核** 写进同一套流程，才与第27章边界一致。

        **技术映射**：**远程控制面最小化**。

        ---

        **小胖**：吞吐章节说调 `threadCount`，我直接拉满？

        **小白**：DB 连接池与 **批量拉取** 会不会把 **锁时间** 拉长？

        **大师**：用 **压测曲线** 回答：记录 **p50/p99 acquire 时间** 与 **misfire 计数** 的联动，形成 **一张二维表** 作为调参上限。

        **技术映射**：**吞吐—锁—misfire 三角权衡**。

        ## 3 项目实战（约 1500–2000 字）

        ### 环境准备

        - 预备 **MySQL** 或团队已有 QRTZ 库；若无，可用 **Docker Compose** 起最小实例（自行编写 `compose.yml`）。
        - Quartz **JDBCJobStore** 与 **集群** 参数与第21、24章对齐。

        ### 分步实现

        **步骤 1：目标** —— 画出 **「触发—落库—执行」** 序列图，标注 **哪一步由哪张表** 证明。

        **步骤 2：目标** —— 人为制造 **短窗口线程池饥饿**，观察 **misfireInstruction** 行为与日志关键字。

        **步骤 3：目标** —— 关闭 RMI，验证 **仅本机 JMX/HTTP 控制面** 可达。

        ### 完整代码清单

        - 本仓库 `examples/example5`、`examples` 负载示例与 `quartz` 源码索引。

        ### 测试验证

        - 附 **一次压测截图** + **一次行锁等待 SQL**（`information_schema` 或等价）解释文本。

        ## 4 项目总结（约 500–800 字）

        ### 优点与缺点

        | 维度 | 串联 Runbook | 分散排障 |
        | --- | --- | --- |
        | 协同 | 高 | 低 |
        | 准备成本 | 中 | 低 |

        ### 适用 / 不适用场景

        - **适用**：中级篇结业、预发演练周。
        - **不适用**：尚无持久化条件的团队（先做缩小版）。

        ### 注意事项

        - 生产凭据不进 Git；Runbook 放 **内部 Wiki**。

        ### 常见踩坑（生产案例）

        1. **只看 CPU**：忽略 **DB 锁与批量拉取**。
        2. **无双实例对照**：集群问题 **单测测不出**。
        3. **把压测当一次性**：未 **回归基线**。

        PLACEHOLDER_J28

        ### 思考题（答案见下一章或 [答案索引](answers-index.md)）

        1. 若 `acquire` SQL p99 上升，你如何在 Runbook 中区分 **锁竞争** vs **网络抖动**？
        2. **`requestsRecovery=true`** 与业务 **at-least-once** 如何共同写进验收表？

        ### 推广计划提示

        - **测试**：把本 Runbook 变成 **自动化检查脚本**（grep 关键字 + SQL）。
        - **运维**：纳入 **月度演练**。
        - **开发**：下一章进入 **StdSchedulerFactory**（第30章）源码阅读。
        """
    ).strip()
    ch29 = ch29.replace("PLACEHOLDER_J28", b28_ans27.strip())

    ch41 = dedent(
        """
        # 第41章：高级篇综合实战：工厂—线程—插件—发布的串联审计

        > **篇别**：高级篇（综合实战）
        > **建议篇幅**：3000–5000 字（含对话与代码）
        > **结构约束**：对齐 [专栏模板](../../column/template.md) 四段式；本章为模板「高级篇结束后独立综合实战」。

        ## 示例锚点

        | 类型 | 路径 |
        | --- | --- |
        | 源码 | [StdSchedulerFactory.java](../../quartz/src/main/java/org/quartz/impl/StdSchedulerFactory.java) |

        ## 1 项目背景（约 500 字）

        ### 业务场景

        平台组要对 **Quartz 大版本升级** 做 **Go/No-Go**：需串起 **工厂装配、调度线程、JobStore 锁路径、OperableTrigger、misfire 计算、SPI、XML 热加载、NativeJob 边界、SRE 演练** 的 **证据链**，回答架构师一句：「**哪些扩展点是允许的，哪些是红线？**」

        ### 痛点放大

        - **配置漂移**：多 jar 带入多份 `quartz.properties`。
        - **插件黑盒**：启了插件却不知 **副作用顺序**。
        - **发布回滚**：缺 **检查表** 导致灰度失败。

        ```mermaid
        flowchart LR
          F[Factory] --> Q[QuartzSchedulerThread]
          Q --> S[JobStoreSupport]
          S --> P[Plugins/XML]
          P --> G[SRE GameDay]
        ```

        ## 2 项目设计（约 1200 字）

        **角色**：小胖 · 小白 · 大师

        ---

        **小胖**：我把 SPI 全实现一遍算高级吗？

        **小白**：问题是 **可替换性与回滚**：自定义 `ThreadPool` 一旦卡死，你如何证明 **不是业务代码** 先阻塞？

        **大师**：高级综合强调 **「边界清单」**：列出 **允许替换的 SPI**、**必须原厂默认的组件**、以及 **升级时二进制不兼容的 Job 类** 处理策略。

        **技术映射**：**SPI 白名单 + 变更评审**。

        ---

        **小胖**：XML 热加载很香啊？

        **小白**：覆盖策略写错会不会 **静默丢 Trigger**？

        **大师**：把 **XMLSchedulingDataProcessor 的 overwrite 行为** 与 **代码路径** 做 **diff 截图**，并在发布单上 **双人签字**。

        **技术映射**：**配置即代码，需同等治理**。

        ---

        **小胖**：NativeJob 跑脚本多快？

        **小白**：资源隔离与 **退出码** 谁看？

        **大师**：把 **cgroups/timeout** 与 **日志回传** 写进 **NativeJob 运维附录**，与 `quartz-jobs` 二次封装策略并列审计。

        **技术映射**：**进程外执行 = 新故障域**。

        ## 3 项目实战（约 1500–2000 字）

        ### 环境准备

        - 两套 **并行配置快照**（升级前/后）；**只读** 预发集群。

        ### 分步实现

        **步骤 1：目标** —— 对照 `StdSchedulerFactory` 初始化，列出 **10 个关键 property** 与默认值差异。

        **步骤 2：目标** —— 走读 **`acquireNextTriggers`** 与 **`triggersFired`** 的 **断点路径**，各截 **一张调用栈**。

        **步骤 3：目标** —— 做一次 **XML 导入 dry-run**，输出 **将被创建/跳过的 Trigger 列表**。

        ### 完整代码清单

        - 本仓库 `quartz` 与 `examples`；外部链接仅内部 Wiki。

        ### 测试验证

        - **GameDay 记录**：时钟回拨 + 从库延迟各 **一页纸**；链接第40章指标。

        ## 4 项目总结（约 500–800 字）

        ### 优点与缺点

        | 维度 | 审计链 | 口头汇报 |
        | --- | --- | --- |
        | 可审计性 | 高 | 低 |
        | 成本 | 中 | 低 |

        ### 适用 / 不适用场景

        - **适用**：大版本升级评审、上市前技术尽调。
        - **不适用**：无源码阅读能力的团队（先补课）。

        ### 注意事项

        - 证据链含 **敏感配置** 时需脱敏。

        ### 常见踩坑（生产案例）

        1. **只看功能测试**：忽略 **序列化兼容**。
        2. **插件顺序依赖未文档化**。
        3. **回滚只回 jar 不回表**。

        PLACEHOLDER_J40

        ### 思考题（答案见 [答案索引](answers-index.md)）

        1. 若必须自定义 `JobStore`，你会把 **哪三条不变式** 写进单测？
        2. **XML 与程序化 `scheduleJob` 混用** 时，如何做 **幂等键** 设计？

        ### 推广计划提示

        - **测试**：把本检查表固化为 **CI 门禁**（properties 快照比对）。
        - **运维**：与 **第40章 SRE** Runbook 合并版本。
        - **开发**：作为 **晋升答辩附录**。

        #### 第41章思考题揭底

        1. **自定义 JobStore 不变式示例**  
           **答**：**（1）** 触发器获取与释放 **配对且幂等**；**（2）** `nextFireTime` 更新与 **集群锁** 同事务或等价一致性；**（3）** **misfire 计算** 与 Trigger 类型 **分支可测试**。

        2. **XML 与程序化混用幂等**  
           **答**：以 **`JobKey`+`TriggerKey`+配置版本号** 做 **自然键**；导入采用 **upsert** 语义并记录 **checksum**；禁止 **隐式 delete** 除非显式开关；对 **生产变更** 走 **双人复核 + 可回滚快照**。
        """
    ).strip()
    # Remap block38 headings: 第38章揭底 -> 第40章揭底 content answers old38 -> new40 questions
    b40 = block38.replace("第38章思考题揭底", "第40章思考题揭底")
    ch41 = ch41.replace("PLACEHOLDER_J40", b40.strip())

    for name, body in [
        ("14", ch14),
        ("29", ch29),
        ("41", ch41),
    ]:
        first = body.split("\n")[0]
        stem = windows_safe_filename(first[2:]) + ".md"
        with open(os.path.join(TMP, stem), "w", encoding="utf-8", newline="\n") as f:
            f.write(body)
        print("new", stem)

    # Remove originals from CH (except README, answers-index)
    for fn in list(os.listdir(CH)):
        if fn.endswith(".md") and fn.startswith("第"):
            os.remove(os.path.join(CH, fn))

    # Move from TMP to CH
    for fn in os.listdir(TMP):
        shutil.move(os.path.join(TMP, fn), os.path.join(CH, fn))
    os.rmdir(TMP)

    j14_ans = """#### 第14章思考题揭底

1. **维护窗跨天与 Cron 日界**  
   **答**：以 Runbook 中的 **业务 timezone** 为单一真相；Calendar 与 Cron 共用 **`quartz.scheduler.timeZone`**；跨天维护窗用 **[start, end)** 半开区间建模；验收用 **固定 Instant 列表** 断言 `nextFireTime` 与日志 **fireTime** 一致。

2. **同一 JobKey 双 Trigger + `@DisallowConcurrentExecution`**  
   **答**：第二次触发可能 **阻塞等待** 或触发 **misfire**（视线程池余量与 Store 语义）；用 **TriggerListener** 观察 **refireCount** 与等待；业务写路径需 **幂等键** 防双写。
"""

    j29_ans = """#### 第29章思考题揭底

1. **`acquire` SQL p99 上升：锁竞争 vs 网络抖动**  
   **答**：锁竞争常伴 **行锁等待事件**、**同一 QRTZ 表热点**、**多实例同时段飙升**；网络抖动多伴 **连接池 borrow 超时**、**跨 AZ RTT 尖刺**、与 **DB CPU/锁指标脱钩**。Runbook 要求 **同时截取 JDBC 驱动日志片段** 与 **`SHOW ENGINE INNODB STATUS` 等价信息** 做二分。

2. **`requestsRecovery` 与 at-least-once**  
   **答**：`requestsRecovery` 面向 **调度器崩溃后的恢复语义**；业务 at-least-once 仍需 **幂等键 + 去重表**。验收表应分列 **基础设施故障** 与 **业务失败** 两类重试，避免互相覆盖 SLA。
"""

    for fn in os.listdir(CH):
        if not fn.startswith("第") or not fn.endswith(".md"):
            continue
        p = os.path.join(CH, fn)
        t = open(p, encoding="utf-8").read()
        nt = t
        if t.startswith("# 第15章：Misfire"):
            nt = t.replace("### 思考题", j14_ans.strip() + "\n\n### 思考题", 1)
            print("inserted j14 into", fn)
        elif t.startswith("# 第30章：`StdSchedulerFactory"):
            nt = t.replace("### 思考题", j29_ans.strip() + "\n\n### 思考题", 1)
            print("inserted j29 into", fn)
        if nt != t:
            open(p, "w", encoding="utf-8", newline="\n").write(nt)

    rebuild_answers_index(CH)
    print("answers-index rebuilt")


def rebuild_answers_index(ch_dir: str) -> None:
    rows: list[tuple[int, str, str]] = []
    for fn in os.listdir(ch_dir):
        if not fn.startswith("第") or not fn.endswith(".md"):
            continue
        p = os.path.join(ch_dir, fn)
        with open(p, encoding="utf-8") as f:
            line1 = f.readline().rstrip("\n")
        m = re.match(r"^# 第(\d{2})章：(.+)$", line1)
        if not m:
            continue
        n, title = int(m.group(1)), m.group(2)
        rows.append((n, title, fn))
    rows.sort(key=lambda x: x[0])

    out: list[str] = [
        "# Quartz 专栏思考题与参考答案索引",
        "",
        "> **约定**：各章末尾 2 道思考题；揭底正文可写在「下一章 §4」或本索引。",
        "",
        "## 总览表",
        "",
        "| 出题章 | 思考题 | 参考答案位置 |",
        "| --- | --- | --- |",
    ]
    for i, (n, title, fn) in enumerate(rows):
        title_link = f"[{title}]({fn})"
        if i + 1 < len(rows):
            nn, _, nfn = rows[i + 1]
            loc = f"[第{nn:02d}章 §4「第{n:02d}章思考题揭底」]({nfn}#第{n:02d}章思考题揭底)"
        else:
            loc = f"[第{n:02d}章正文 §「第{n:02d}章思考题揭底」]({fn}#第{n:02d}章思考题揭底)"
        out.append(f"| 第{n:02d}章 {title_link} | Q1–Q2 | {loc} |")

    out.extend(
        [
            "",
            "## 第41章思考题揭底（索引镜像）",
            "",
            "正文以最后一章综合实战文件为准；此处便于全文搜索。",
            "",
            "## 跨章引用维护",
            "",
            "- 修改章节编号时，请同步更新本表锚点与各章「思考题揭底」小节标题。",
            "",
        ]
    )
    with open(os.path.join(ch_dir, "answers-index.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(out) + "\n")


if __name__ == "__main__":
    main()
