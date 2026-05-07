# 第32章：Dag解析引擎源码剖析

## 1 项目背景

某大型数据团队在 Airflow 3.x 升级后遇到了一个诡异的"幽灵 Dag"现象：团队成员在 `dags/` 目录下修改了 Dag 文件的调度频率和任务依赖关系，提交到 Git 仓库后，Airflow Web UI 显示的 Dag 结构和调度策略仍然是旧版本。更奇怪的是，重启 Scheduler 后问题依然存在。

排查过程走了很多弯路——先排查了 Git 同步是否正常，再排查了 Web Server 缓存，最后才发现真正的问题：Dag Processor 进程因为 OOM 被 Kubernetes 静默 kill 了。Dag 的序列化数据一直停留在元数据库的 `serialized_dag` 表中没有被刷新，Scheduler 和 Web Server 读取的都是这份"僵尸数据"。

另一个典型故障场景是：团队引入了 `TaskGroup` 来组织 50+ 个任务，但调度时发现 Task 的执行顺序和预期不符——有些本该并行的 Task 被串行化了。根因在于 TaskGroup 的嵌套结构在序列化时被"拍平"成了线性 List，但依赖关系的边信息在序列化过程中被部分丢失。

> 这些案例生动地说明了一个事实：**理解 Dag 从 Python 源码到 JSON 序列化数据的完整生命旅程，是定位生产环境 Airflow 疑难杂症的必备技能。** Dag 解析引擎是 Airflow 3.x 架构中"消化系统"的核心——它将用户编写的 Python Dag 文件转化为 Scheduler 和 Worker 可消费的结构化数据，是连接"人的意图"和"机器的执行"的关键桥梁。

本章将深入源码，完整剖析 DagBag 文件扫描、AST 解析、DAG 对象提取和序列化的全过程，同时解析 TaskGroup 嵌套展开和 MappedOperator 动态映射的底层实现。

---

## 2 项目设计

**小胖**（盯着 `airflow dags list` 的输出一脸困惑）："为什么我明明在 Python 文件里改了 Dag 的参数，界面上显示的却是老版本？我连 Scheduler 都重启了！"

**大师**："因为你没有重启 Dag Processor。在 Airflow 3.x 架构里，Dag 文件的解析和 Scheduler 是分离的——Dag Processor 是独立的进程，它负责读取 Python 文件、解析出 DAG 对象、序列化为 JSON 存入数据库。Scheduler 读的是数据库里的序列化数据，根本不碰 Python 文件。"

**小白**："那 Dag Processor 具体是怎么把 Python 文件变成 JSON 的？这个过程会不会丢失信息？"

**大师**："整个流程分四步走。第一步，`DagBag.collect_dags()` 扫描文件目录，用 `airflowignore` 过滤不需要的文件，然后通过 Importer 注册表找到合适的文件导入器。第二步，导入器在独立子进程中执行 `import` 或 `exec` 来加载 Python 模块——这步是关键，它在独立进程中执行，防止用户代码污染 Scheduler 的内存空间。第三步，`DagSerialization.to_dict(dag)` 递归遍历 DAG 对象的所有属性——tasks、dependencies、schedule、params、tags——将其转化为字典结构。第四步，`LazyDeserializedDAG` 封装序列化数据，通过 `sync_bag_to_db` 写入数据库的 `serialized_dag` 字段。"

**小胖**："等等，第二步为什么要在独立子进程中 import？这不是小题大做吗？"

**大师**："这是 Airflow 3.x 安全模型的核心。想象一下，如果某个 Dag 文件中有恶意代码——比如 `import os; os.system('rm -rf /')`——如果 Scheduler 直接 import 这个文件，那 Scheduler 进程就沦陷了，整个调度系统瘫痪。通过把 Dag 文件解析放在独立的 Dag Processor 子进程中，恶意代码最多影响那个子进程，伤不到 Scheduler。而且子进程由 `DagFileProcessorProcess`（继承自 `WatchedSubprocess`）管理，有超时和内存限制。"

**小白**："那序列化具体是怎么处理复杂类型的？比如 TaskGroup——它是有层级关系的，JSON 怎么表示树状结构？"

**大师**："好问题。TaskGroup 的序列化是一个'展开'的过程。`SerializedTaskGroup` 类递归遍历 TaskGroup 的 children——每个 child 可能是 Task 或另一个 TaskGroup。最终输出的是一个扁平化的任务列表，但保留了 `upstream_group_id` 和 `downstream_group_id` 来重建依赖关系。对于 `MappedTaskGroup`（动态映射版本），会额外保存 `expand_input` 字段，Scheduler 在运行时根据它动态展开成多个 Task 实例。这里有一个性能坑点——如果 expand_input 的结果集非常大（比如映射了 10000 个分区），会产生大量 TaskInstance，需要在 Pool 和并发控制层面做限制。"

**小胖**："那个 `DagFileProcessorProcess` 怎么保证子进程出问题了能被发现？"

**大师**："它继承自 `WatchedSubprocess`，关键机制包括：第一，`_check_subprocess_exit()` 轮询子进程是否存活。第二，通过 Unix socket pair 进行父进程-子进程通信——子进程解析完 Dag 后通过 socket 发送 `DagFileParsingResult`。第三，`is_ready` 属性综合判断进程状态和通信完成情况。第四，父进程注册心跳监控，如果子进程长时间无响应，会触发超时处理。这套机制本质上是一个'带外管理的进程沙箱'——不让子进程直接操作数据库，所有 DB 访问都通过 Execution API 代理。"

> **技术映射**：DagBag = 邮局的邮件分拣中心（扫描、分类、处理），DagSerialization = 邮件翻译成电报（将 Python 对象变成标准 JSON），DagFileProcessorProcess = 独立的分拣车间（故障隔离，不影响主楼）。Scheduler 读 SerializedDAG = 邮递员只读电报，不看原始信件。

---

## 3 项目实战

### 3.1 环境准备

```bash
# 确认 Dag Processor 配置
grep -E "dag_processor|parsing_processes" airflow.cfg

# 关键配置项
# [dag_processor]
# parsing_processes = 2           # 并行的 Dag 解析进程数
# parsing_pre_import_modules = True  # 预导入 Airflow 模块加速
# dag_dir_list_interval = 300     # Dag 目录扫描间隔（秒）
```

### 3.2 阶段一：追踪 DagBag 文件扫描流程

**步骤目标**：通过源码和日志理解 `DagBag.collect_dags()` 的完整调用链。

```python
# 关键源码位置: airflow-core/src/airflow/dag_processing/dagbag.py

# DagBag.collect_dags() 的核心逻辑简化版
def collect_dags(self, dag_folder, only_if_updated=True, ...):
    # 1. 获取文件列表（含 .airflowignore 过滤）
    registry = get_importer_registry()
    files_to_parse = registry.list_dag_files(dag_folder, safe_mode=safe_mode)
    
    # 2. 逐个文件解析
    for filepath in files_to_parse:
        found_dags = self.process_file(filepath, only_if_updated=only_if_updated)
        # 3. 记录统计信息
        stats.append(FileLoadStat(file=filepath, dag_num=len(found_dags), ...))
```

**观察日志**：

```bash
# 启动时观察 Dag Processor 日志
docker compose logs -f airflow-dag-processor-1 | grep -E "Filling|Processing|Loaded|Serialized"
```

典型输出：
```
[dag_processor] Filling up the DagBag from /opt/airflow/dags
[dag_processor] Processing file /opt/airflow/dags/comprehensive_etl.py
[dag_processor] Loaded DAG comprehensive_etl
[dag_processor] Serialized DAG comprehensive_etl (6 tasks, version: a1b2c3d4)
```

### 3.3 阶段二：深入 DAG 对象序列化

**步骤目标**：理解 `DagSerialization.to_dict()` 的递归序列化机制。

```python
# 关键源码: airflow-core/src/airflow/serialization/serialized_objects.py

class DagSerialization(BaseSerialization):
    """
    将 DAG 对象序列化为 JSON 兼容的字典。
    核心方法 to_dict() 递归处理所有属性。
    """
    
    @classmethod
    def to_dict(cls, dag: DAG) -> dict:
        return {
            "dag_id": dag.dag_id,
            "schedule": BaseSerialization.serialize(dag.schedule),
            "tasks": [BaseSerialization.serialize(task) for task in dag.tasks],
            "dag_dependencies": [
                DagDependency(source=src, target=tgt)
                for src, tgt in dag.dag_dependencies.items()
            ],
            "params": BaseSerialization.serialize(dag.params),
            "tags": list(dag.tags),
            "description": dag.description,
            "max_active_tasks": dag.max_active_tasks,
            "catchup": dag.catchup,
            # ... 更多属性
        }
```

**实战：手动序列化和反序列化一个 DAG**：

```python
"""
理解 DAG 序列化的双向过程。
此脚本可在 Breeze 环境中运行。
"""
import json
from airflow.serialization.serialized_objects import (
    DagSerialization, 
    LazyDeserializedDAG
)
from airflow.sdk import DAG
from airflow.sdk.operators.empty import EmptyOperator
from datetime import datetime

# 1. 创建一个简单的 DAG
with DAG(
    dag_id="serialization_demo",
    schedule=None,
    start_date=datetime(2025, 1, 1),
    tags=["demo"],
) as dag:
    t1 = EmptyOperator(task_id="start")
    t2 = EmptyOperator(task_id="process")
    t3 = EmptyOperator(task_id="end")
    t1 >> t2 >> t3

# 2. 序列化为字典
serialized_dict = DagSerialization.to_dict(dag)
print("=== 序列化后的字典结构 ===")
print(json.dumps(serialized_dict, indent=2, default=str)[:2000])

# 3. 包装为 LazyDeserializedDAG（模拟数据库存储）
lazy_dag = LazyDeserializedDAG(data=serialized_dict, last_loaded=None)

# 4. 反序列化回 DAG 对象
# 注意：反序列化的 DAG 是"轻量版"，不包含原始 Python 执行逻辑
deserialized_dag = lazy_dag.dag
print(f"\n=== 反序列化后 DAG {deserialized_dag.dag_id}: {len(deserialized_dag.tasks)} 个任务 ===")
for task in deserialized_dag.tasks:
    print(f"  Task: {task.task_id}, "
          f"upstream: {[t.task_id for t in task.upstream_list]}, "
          f"downstream: {[t.task_id for t in task.downstream_list]}")
```

### 3.4 阶段三：TaskGroup 嵌套解析与展开

**步骤目标**：理解 TaskGroup 如何从树状结构展开为扁平任务列表。

```python
"""
TaskGroup 序列化演示：层级结构 → 扁平化
关键源码: airflow-core/src/airflow/serialization/definitions/taskgroup.py
"""
from airflow.sdk import DAG
from airflow.sdk.operators.empty import EmptyOperator
from airflow.sdk.definitions.taskgroup import TaskGroup
from airflow.serialization.serialized_objects import DagSerialization
from datetime import datetime
import json

with DAG(
    dag_id="taskgroup_demo",
    schedule=None,
    start_date=datetime(2025, 1, 1),
) as dag:
    # 顶层任务
    start = EmptyOperator(task_id="start")
    
    # TaskGroup: 数据提取
    with TaskGroup(group_id="extract_group") as extract_group:
        fetch_api = EmptyOperator(task_id="fetch_api")
        fetch_db = EmptyOperator(task_id="fetch_db")
        fetch_api >> fetch_db
    
    # TaskGroup: 数据清洗（嵌套）
    with TaskGroup(group_id="clean_group") as clean_group:
        dedup = EmptyOperator(task_id="dedup")
        with TaskGroup(group_id="validate_subgroup") as validate:
            check_null = EmptyOperator(task_id="check_null")
            check_type = EmptyOperator(task_id="check_type")
        dedup >> validate
    
    # 顶层依赖
    start >> extract_group >> clean_group

# 序列化后观察 TaskGroup 如何被展开
serialized = DagSerialization.to_dict(dag)
print(f"DAG 共 {dag.dag_id} 有 {len(dag.tasks)} 个叶子任务")
print("\n序列化后的 tasks 列表（已扁平化）:")
for task_data in serialized["tasks"]:
    if isinstance(task_data, dict):
        tid = task_data.get("task_id", "?")
    else:
        tid = getattr(task_data, "task_id", "?")
    print(f"  - {tid}")

# 关键点：all tasks 包括
# start, extract_group.fetch_api, extract_group.fetch_db,
# clean_group.dedup, clean_group.check_null, clean_group.check_type
# 层级信息通过 task_id 的点号命名和上游依赖关系保留
```

### 3.5 阶段四：MappedOperator 动态映射序列化

**步骤目标**：理解动态映射 Operator 的序列化与展开机制。

```python
"""
MappedOperator 序列化演示
关键类: MappedOperator (SDK), SerializedMappedOperator (序列化定义)
"""
from airflow.sdk import DAG
from airflow.sdk.operators.python import PythonOperator
from airflow.sdk.definitions.mappedoperator import MappedOperator
from datetime import datetime

with DAG(
    dag_id="mapped_demo",
    schedule=None,
    start_date=datetime(2025, 1, 1),
) as dag:
    
    def _process_partition(partition: str, **ctx):
        print(f"处理分区: {partition}")
    
    # 使用 .expand() 创建动态映射
    partitions = [f"p_{i}" for i in range(5)]
    process = PythonOperator.partial(
        task_id="process_partition",
        python_callable=_process_partition,
    ).expand(op_kwargs=[{"partition": p} for p in partitions])

# 序列化时，MappedOperator 保存 expand_input
serialized = DagSerialization.to_dict(dag)
print(f"DAG: {dag.dag_id}")
print(f"任务数: {len(dag.tasks)}")  # 1 (未展开的 MappedOperator)
print(f"\n关键：MappedOperator 序列化时保存 expand_input 元信息")
print(f"Scheduler 在运行时根据 expand_input 动态创建 TaskInstance")

# 在数据库中查看序列化结果
# SELECT dag_id, serialized_dag->'tasks' FROM dag WHERE dag_id = 'mapped_demo';
```

### 3.6 阶段五：Dag Processor 子进程架构

**步骤目标**：理解 DagFileProcessorProcess 的进程管理机制。

```python
"""
DagFileProcessorProcess 架构要点
来源: airflow-core/src/airflow/dag_processing/processor.py
"""

# 1. WatchedSubprocess 基类 (task-sdk/src/airflow/sdk/execution_time/supervisor.py)
#    - 管理子进程生命周期
#    - Unix socket pair 通信
#    - 超时检测和资源限制

# 2. DagFileProcessorProcess 扩展
#    - start() 方法: 创建子进程, 发送 DagFileParseRequest
#    - _on_child_started(): 子进程启动后立即发送解析请求
#    - _handle_request(): 处理子进程发回的消息
#      - DagFileParsingResult → 存储解析结果
#      - GetConnection → 通过 Execution API 代理查询
#      - GetVariable → 通过 Execution API 代理查询
#    - is_ready: 进程退出 + 通信完成 → True

# 3. _parse_file_entrypoint() — 子进程入口
#    - 设置 _AIRFLOW_PROCESS_CONTEXT = "client"
#    - 接收 DagFileParseRequest
#    - 调用 _parse_file() 执行解析
#    - 通过 CommsDecoder 发送结果回父进程
```

**监控 Dag Processor 状态**：

```sql
-- 查看 Dag 解析状态
SELECT 
    dag_id,
    last_parsed_time,
    fileloc,
    bundle_name,
    is_active
FROM dag
WHERE is_active = TRUE
ORDER BY last_parsed_time DESC;
```

### 3.7 完整代码清单

以上所有代码片段可在以下路径找到：
- `DagBag`: `airflow-core/src/airflow/dag_processing/dagbag.py`
- `Processor`: `airflow-core/src/airflow/dag_processing/processor.py`
- `SerializedObjects`: `airflow-core/src/airflow/serialization/serialized_objects.py`
- `TaskGroup 定义`: `airflow-core/src/airflow/serialization/definitions/taskgroup.py`
- `MappedOperator 定义`: `airflow-core/src/airflow/serialization/definitions/mappedoperator.py`

---

## 4 项目总结

### 优点 & 缺点对比

| 维度 | Airflow 3.x 序列化架构 | Airflow 2.x 传统 import 方式 |
|------|----------------------|---------------------------|
| 安全性 | 子进程隔离，恶意代码无法影响 Scheduler | Scheduler 直接 import，存在代码注入风险 |
| 性能 | 一次解析，多次消费（读 JSON 毫秒级） | 每次心跳都 re-import（秒级） |
| 解耦性 | Dag 来源抽象为 Bundle（Git/S3/本地） | 仅支持本地目录 |
| 调试难度 | 序列化层间接，问题定位需要理解多层 | 直接 import，问题直观 |
| 序列化一致性 | 自定义字段需要注册序列化器 | 无需序列化 |

### 适用场景

1. **多团队共享 Airflow 实例**：通过 Bundle 隔离不同团队的 Dag 文件来源。
2. **Dag 文件数量多（>50）**：序列化缓存显著减少 Scheduler 负载。
3. **安全合规要求高**：需要 Dag 解析与 Scheduler 隔离的金融、医疗行业。
4. **频繁修改 Dag**：Git-driven 开发，Dag Processor 自动检测变化并重新解析。
5. **动态 Dag 生成**：结合 TaskGroup 和 MappedOperator 实现大规模任务编排。

### 不适用场景

1. **单用户开发环境**：直接 import 更简单直观。
2. **需要运行时动态修改 Dag 结构**：序列化后的 DAG 是静态快照。

### 注意事项

- **配置陷阱**：`parsing_processes` 不宜超过 CPU 核数，否则上下文切换成本超过并行收益。
- **版本兼容**：序列化格式随着 Airflow 版本升级可能变化，升级前务必测试序列化兼容性。
- **安全边界**：Dag Processor 子进程虽然有隔离，但仍有 Execution API 访问权限，不要把它当作完整的安全沙箱。
- **MappedOperator 性能**：展开 10000+ 个 TaskInstance 时，会产生大量数据库写入，建议配合 `max_active_tis_per_dag` 限制并发。

### 常见踩坑经验

1. **幽灵 Dag**：Dag Processor 进程 OOM 被 kill，序列化数据未刷新。解决：设置 `parsing_processes` 的 memory limit，配置监控告警。
2. **TaskGroup 依赖丢失**：序列化时依赖关系编码到 `downstream_task_ids` 而非结构化 Group 关系。解决：不要依赖序列化后的 TaskGroup 层级关系来推断业务逻辑。
3. **MappedOperator 展开爆炸**：动态参数列表意外包含大量元素。解决：在 expand 之前添加 `max_map_length` 限制，或使用分区策略分批处理。

### 思考题

1. **进阶题**：如果需要在 MappedOperator 展开时实现"延迟展开"——即不在 Dag 解析阶段展开，而是在 Scheduler 每次心跳时动态计算展开结果——需要修改哪些源代码模块？请画出数据流图。

2. **设计题**：假设你需要为 Airflow 实现一个"增量解析"功能——只重新解析修改过的 Dag 文件，而不是每次扫描全目录。请设计 `DagBag.collect_dags()` 的改进方案，考虑文件修改时间、MD5 校验和缓存策略。

> **推广计划提示**：开发团队建议先阅读第18章（序列化机制概述），再深入本章的源码剖析。运维团队重点关注 Dag Processor 进程的监控和故障恢复。测试团队可基于本章的序列化/反序列化流程设计集成测试用例。
