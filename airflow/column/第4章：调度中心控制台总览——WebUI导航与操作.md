# 第4章：调度中心控制台总览——Web UI 导航与操作

## 1 项目背景

某数据平台团队刚刚将 20 个 ETL Dag 迁移到 Airflow。第二天早上，运维工程师小赵照例打开 Airflow Web UI，发现首页一片"红灯"——有 3 个 Dag 显示为 Failed 状态。他愣住了：cron 时代他只要看邮件通知就行，脚本失败了自己重跑；但在 Airflow 里，他需要快速弄清楚"哪个 Dag 的哪个 Task 在哪一步失败了？失败原因是什么？怎么快速恢复？"

更棘手的是，下午产品经理跑来问："上周三的日报为啥数据不对？"小赵需要在 Airflow 里找到那天的 DagRun，看哪些 Task 成功、哪些没跑、哪一步的数据有问题。

> Airflow 的 Web UI 不只是"看看状态"的仪表盘——它是一个完整的**工作流管理控制台**，能让你从宏观到微观逐层下钻，从"知道出了问题"到"找到出问题的那个 Task 的那行日志"。

本章将带你全面掌握 Airflow Web UI 的导航与操作，让你像使用 IDE 一样高效地使用它。

---

## 2 项目设计

**小胖**（盯着 Grid 视图，眼花缭乱）："这么多五颜六色的方块，我感觉自己在玩俄罗斯方块……怎么快速找到我要的信息？"

**大师**："Airflow 的 UI 设计遵循'下钻'理念。首页是最宏观的概览——所有 Dag 的状态一览表。点击一个 Dag 进入详情页，你能看到这个 Dag 所有历史 DagRun 和 TaskInstance。再点击一个 Task 方块，就能看到它的详细日志。三层逐级深入，跟剥洋葱一样。"

**小白**："Grid 视图和 Graph 视图有什么区别？我该看哪个？"

**大师**："Grid 视图适合看时间维度——X 轴是时间（每个 DagRun 一列），Y 轴是任务。你可以直观地对比同一个任务在不同时间点的表现。比如今天的 `extract_orders` 跑了 3 分钟，昨天只跑了 1 分钟——是不是数据量变大了，还是数据库慢了？"

**小胖**："那 Graph 视图呢？"

**大师**："Graph 视图展示的是拓扑结构——任务之间的依赖关系图。当你有一个复杂的 Dag，涉及分支、条件跳过时，Graph 视图能帮你快速理解"为什么某个任务没跑"。绿色的圆代表成功，红色的代表失败，灰色代表被跳过。"

**小白**："那 Gantt 图呢？"

**大师**："Gantt 图是性能优化的利器。它把每个任务的时间段画成横条，你可以一眼看出哪些任务耗时最长、哪些有大量等待间隙。比如你发现 `extract` 跑了 30 分钟但 `clean` 只跑了 1 分钟——优化重点显然是 extract。"

**小胖**："还有 Code 视图——它在 UI 里直接展示我的 Dag 源码？"

**大师**："对，而且 Code 视图展示的是序列化后从数据库读取的版本。这里有个重要的安全设计：在分布式架构中，Webserver 不直接读取你的 Dag 文件——它读取的是 Dag Processor 序列化后存到数据库的 Dag 定义。所以你看到的 Code 视图就是数据库里存的那个版本，也是 Scheduler 实际使用的版本。"

> **技术映射**：Grid 视图 = 考勤表（每天考勤状态一目了然），Graph 视图 = 地铁线路图（站与站之间的连接关系），Gantt 视图 = 快递时间线（每个环节的耗时占比）。

---

## 3 项目实战

### 3.1 首页 Dashboard 导航

登录 `http://localhost:8080`，你会看到首页：

**顶部导航栏**：
- **DAGs**：Dag 列表与状态总览（默认首页）
- **Dag Runs**：跨 Dag 的所有 DagRun 汇总
- **Jobs**：后台 Job（Scheduler、Triggerer）的状态
- **Audit Logs**：用户操作审计日志
- **Browse**：Connections、Variables、XComs、Pools 等资源管理
- **Admin**：用户管理、角色权限、配置查看

**Dag 列表页核心功能**：

| 功能 | 操作 | 说明 |
|------|------|------|
| 筛选 | 点击顶部 Tags 标签 | 只显示特定标签的 Dag |
| 搜索 | 搜索框输入 Dag ID | 快速定位特定 Dag |
| 批量操作 | 勾选多个 Dag | 批量 Pause/Delete |
| 开关 | 每个 Dag 左侧的开关 | Pause/Unpause |
| 触发 | 每个 Dag 右侧的播放按钮 | 手动触发 DagRun |
| 状态统计 | Dag 行右侧的红/绿数字 | 近期成功/失败统计 |

### 3.2 Dag 详情页四大视图详解

创建一个测试用 Dag 来练习各视图：

在 `dags/` 目录下创建 `ui_demo.py`：

```python
from datetime import datetime, timedelta
from airflow.sdk import DAG
from airflow.sdk.operators.bash import BashOperator
from airflow.sdk.operators.empty import EmptyOperator

default_args = {
    "owner": "demo",
    "retries": 1,
    "retry_delay": timedelta(seconds=30),
}

with DAG(
    dag_id="ui_demo",
    default_args=default_args,
    schedule=None,  # 仅手动触发
    start_date=datetime(2025, 1, 1),
    catchup=False,
    tags=["demo"],
) as dag:

    start = EmptyOperator(task_id="start")
    end = EmptyOperator(task_id="end")

    # 并行数据获取组
    get_users = BashOperator(
        task_id="get_users",
        bash_command='echo "获取用户数据..." && sleep 5',
    )
    get_orders = BashOperator(
        task_id="get_orders",
        bash_command='echo "获取订单数据..." && sleep 8',
    )
    get_products = BashOperator(
        task_id="get_products",
        bash_command='echo "获取商品数据..." && sleep 3',
    )

    # 数据合并
    merge_data = BashOperator(
        task_id="merge_data",
        bash_command='echo "合并数据..." && sleep 4',
    )

    # 质量控制检查
    check_quality = BashOperator(
        task_id="check_quality",
        bash_command="""
            echo "数据质量检查..."
            # 模拟质量检查（95% 概率通过）
            if [ $((RANDOM % 100)) -gt 5 ]; then
                echo "质量检查通过"
                exit 0
            else
                echo "质量检查失败！"
                exit 1
            fi
        """,
        retries=0,
    )

    # 报表生成（只在质量检查通过后执行）
    generate_report = BashOperator(
        task_id="generate_report",
        bash_command='echo "生成报表..." && sleep 2',
    )

    start >> [get_users, get_orders, get_products] >> merge_data >> check_quality
    check_quality >> generate_report >> end
```

触发这个 Dag 跑几次（不同时间），然后练习以下操作：

**Grid 视图操作**：
1. 点击 Task 方块 → 弹出详情面板 → 查看日志、XCom、Task 详情
2. 右键 Task 方块 → Clear（清除状态并重跑）
3. 点击左上角的 "Auto-refresh" 开关，切换自动刷新
4. 观察不同 DagRun 的同一 Task 的颜色差异（绿/红/灰）

**Graph 视图操作**：
1. 点击 Task 节点 → 弹出菜单 → Mark Success / Mark Failed / Clear
2. 鼠标悬停在节点上查看详情
3. 点击连线查看依赖关系

**Gantt 视图操作**：
1. 观察各 Task 的时间条长度对比
2. 分析并行任务的执行时间重叠情况

### 3.3 任务状态机与操作

Airflow 的 TaskInstance 状态流转如下：

```
                ┌──────────┐
                │ scheduled │
                └────┬─────┘
                     ▼
                ┌──────────┐
                │  queued   │
                └────┬─────┘
                     ▼
                ┌──────────┐
         ┌──────│ running  │──────┐
         │      └────┬─────┘      │
         ▼           ▼            ▼
    ┌────────┐  ┌─────────┐  ┌────────┐
    │ success│  │ failed  │  │up_for_ │
    └────────┘  └───┬─────┘  │ retry  │
                    │        └───┬────┘
                    ▼            │
              ┌──────────┐      │
              │upstream_ │      │
              │ failed   │◄─────┘
              └──────────┘
```

**常见操作**：

| 操作 | 效果 | 使用场景 |
|------|------|---------|
| **Trigger Dag** | 创建新的 DagRun | 手动补跑或测试 |
| **Clear** | 清除状态重新执行 | 任务失败需要重跑 |
| **Mark Success** | 标记为成功（不执行） | 跳过已知可忽略的失败 |
| **Mark Failed** | 标记为失败（不执行） | 手动标记异常 |
| **Pause** | 暂停调度 | 维护时段或临时下线 |

### 3.4 查看日志与调试

**通过 Web UI 查看日志**：

在 Grid 视图中点击一个 Task 方块 → 右侧弹出面板 → 点击 "Logs" 标签。

**通过 CLI 查看日志**：

```bash
# 查看特定 DagRun 的特定 Task 的日志
docker exec airflow-scheduler-1 airflow tasks logs ui_demo get_orders 2025-01-15T00:00:00

# 查看最近的日志
docker exec airflow-scheduler-1 airflow tasks logs ui_demo get_orders --try-number 1
```

### 3.5 使用审计日志追踪操作

首页 → Browse → Audit Logs，可以看到所有用户的操作记录：

```bash
# CLI 查看审计日志
docker exec airflow-scheduler-1 airflow audit-logs list --limit 20
```

---

## 4 项目总结

### 四种视图对比

| 视图 | 维度 | 最适合 | 关键优势 |
|------|------|--------|---------|
| **Grid** | 时间 × 任务 | 日常监控、状态追踪 | 快速发现异常 DagRun |
| **Graph** | 拓扑结构 | 理解依赖、调试分支 | 直观展示"为什么没跑" |
| **Gantt** | 时间线 | 性能分析、瓶颈定位 | 一眼看出耗时分布 |
| **Code** | 源代码 | 代码审查、版本确认 | 确保运行版本一致 |

### 常用操作速查

| 目标 | 操作 |
|------|------|
| 手动触发 Dag | Dag 详情页 → 点 ▶ 按钮 → Trigger |
| 重跑失败的 Task | 右键 → Clear → 选择 Clear 范围 |
| 跳过某个 Task | 右键 → Mark Success |
| 查看失败原因 | 点击红色方块 → Logs 标签 |
| 对比不同 DagRun | Grid 视图，同一列对比 |
| 暂停/恢复调度 | 首页左侧开关或详情页 Pause 按钮 |

### 注意事项

1. **Mark Success 是危险的**：它不会执行任务，只是改状态。如果下游任务依赖该 Task 的 XCom 数据，会导致数据缺失。
2. **Clear 操作谨慎选择范围**：默认 Clear 会清除"当前 + 下游"，可能触发大量重跑。
3. **不要频繁刷新大集群的首页**：加载所有 Dag 的状态需要多次数据库查询，刷新过快会增加 DB 压力。

### 思考题

1. 一个包含 50 个 Task 的 Dag 中，第 25 个 Task 失败了。如果你用 Clear 操作只清除该 Task（不包含下游），下游的 Task 会重新执行吗？为什么？
2. 在 Grid 视图中，你发现同一 Dag 的多个 DagRun 中，同一个 Task 有时成功有时失败。你会如何利用 Gantt 视图来排查根因？

*（答案将在后续章节揭晓）*

---

> **本章完成**：你已经可以熟练使用 Airflow 的 Web UI 监控和操作工作流。下一章我们将深入学习调度策略——Cron、Timetable 和 Catchup 的精妙设计。
