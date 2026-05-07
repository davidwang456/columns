# 第18章：Dag 文件处理与序列化机制

## 1 项目背景

某团队的 Airflow 部署中，50 多个 Dag 文件散落在 Git 仓库的 `dags/` 目录下。最近团队引入了 Dag Bundle 机制，将 Dag 文件按业务线拆分到不同的 Git 仓库。但上线后遇到了诡异的问题：某个 Dag 文件在 Git 中明明已经更新，Web UI 中显示的代码却是旧版本。重启 Scheduler 后依然如故。

排查发现：Dag Processor 解析 Dag 文件后将其序列化为 JSON 存入元数据库的 `dag.serialized_dag` 字段，Scheduler 和 Webserver 读的都是这个序列化后的版本。而由于配置错误，Dag Processor 进程没有正常运行，旧的序列化数据一直没被刷新。团队以为自己在改 Dag 文件，实际上 Airflow 一直用的是"缓存"在数据库里的旧版本。

> 这个案例揭示了理解 Airflow 3.x 架构的核心——**Dag 文件的"消化系统"**：Dag Processor 解析 Python 文件 → 序列化为 JSON → 存入元数据库 → Scheduler 从数据库读取并调度。这是一个"解析一次，到处使用"的设计，带来了性能和安全上的双重收益。

---

## 2 项目设计

**小胖**（刚发现改了一个小时的 Dag 压根没生效）："为什么不直接读 Dag 文件？为什么要序列化到数据库？"

**大师**："这在 Airflow 3.x 中是架构性的选择，带来三个好处：第一，安全隔离——Scheduler 不读 Dag 文件，不执行用户代码，只读序列化后的 JSON。如果用户 Dag 中有恶意代码，它伤不到 Scheduler。第二，性能——Scheduler 不需要每次都 import Python 文件（import 一个复杂 Dag 可能需要几秒钟），直接从数据库读 JSON 反序列化，毫秒级。第三，解耦——Dag 文件可以来自不同来源（本地目录、Git 仓库、S3），通过 Bundle 机制统一管理。"

**小白**："序列化的过程是怎样的？会不会丢失信息？"

**大师**："序列化过程由 `serialization/` 模块完成。它遍历 DAG 对象的所有属性——tasks、dependencies、params、schedule、tags——将其转换为 JSON 格式。特殊类型（如 timedelta、datetime）会被转为 ISO 格式字符串。复杂对象（如 Operator 的自定义参数）通过注册的序列化器处理。反序列化时从 JSON 重新构造 DAG 对象——但注意，反序列化后的 DAG 是一个'轻量版本'，只有调度所需的属性和方法，不包含原始的 Python 执行逻辑。"

**小胖**："那 Bundle 又是什么？"

**大师**："Bundle 是 Dag 文件来源的抽象。可以是 Local Bundle（本地文件系统）、Git Bundle（Git 仓库）、或者自定义 Bundle（S3、GCS 等）。Dag Processor 根据 Bundle 配置去获取 Dag 文件，然后解析。多 Bundle 支持意味着一个 Airflow 实例可以同时管理来自不同仓库、不同团队的 Dag 文件。"

> **技术映射**：Dag Processor = 食材加工房（把生食材加工成预制菜），Serialized Dag（JSON）= 预制菜（方便存储、运输），Scheduler = 餐厅厨房（用预制菜快速出餐，不需要从洗菜开始）。

---

## 3 项目实战

### 3.1 观察序列化过程

**步骤目标**：理解 Dag 文件 → SerializedDagModel 的完整流程。

```bash
# 查看 Dag Processor 的解析日志
docker compose logs airflow-dag-processor-1 | grep "DAG.*serialized"

# 典型输出：
# [dag_processor] Processing file /opt/airflow/dags/comprehensive_etl.py
# [dag_processor] Serialized DAG comprehensive_etl (6 tasks)
# [dag_processor] Stored serialized DAG comprehensive_etl (version: a1b2c3d4)
```

**直接查看序列化的 Dag**：

```sql
-- 查询 serialized_dag 的 JSON 结构（截取前 1000 字符）
SELECT 
    dag_id,
    LEFT(serialized_dag::text, 1000) AS serialized_preview,
    LENGTH(serialized_dag::text) AS json_size,
    last_updated
FROM dag
WHERE dag_id = 'comprehensive_etl';
```

**序列化 JSON 结构示例**：

```json
{
  "dag_id": "comprehensive_etl",
  "schedule_interval": "0 2 * * *",
  "tasks": [
    {
      "task_id": "extract_orders",
      "operator": "BashOperator",
      "bash_command": "echo ...",
      "downstream_task_ids": ["quality_check"],
      "retries": 3
    },
    {
      "task_id": "quality_check",
      "operator": "_PythonDecoratedOperator",
      "downstream_task_ids": ["clean_data"]
    }
  ],
  "dag_dependencies": [
    {"source": "extract_orders", "target": "quality_check"}
  ],
  "params": {"force_full_sync": false}
}
```

### 3.2 DagBag 与解析流程

```python
# 通过 Python API 查看 DagBag 的行为
from airflow.dag_processing.dagbag import DagBag

# 加载所有 Dag 文件
dagbag = DagBag(dag_folder="/opt/airflow/dags/")

# 查看加载结果
print(f"成功加载: {len(dagbag.dags)} 个 Dag")
print(f"导入错误: {len(dagbag.import_errors)} 个")

# 查看具体错误
for filepath, error in dagbag.import_errors.items():
    print(f"{filepath}: {error}")
```

### 3.3 Dag Bundle 配置实战

**步骤目标**：配置 Git Bundle 从远程仓库拉取 Dag 文件。

```ini
# airflow.cfg
[dag_processor]
dag_bundle_config_path = /opt/airflow/dag_bundles.yaml
```

```yaml
# /opt/airflow/dag_bundles.yaml
bundles:
  - name: core_etl
    type: git
    repo_url: https://gitlab.company.com/data/core-etl-dags.git
    branch: main
    path: dags/
    refresh_interval: 60  # 每 60 秒拉取一次

  - name: ml_pipelines
    type: git
    repo_url: https://gitlab.company.com/ml/ml-pipeline-dags.git
    branch: main
    path: dags/
    refresh_interval: 300

  - name: local_dev
    type: local
    path: /opt/airflow/dags/
```

启动后，Dag Processor 会从多个 Bundle 获取 Dag 文件并统一处理。

### 3.4 序列化器扩展

如果自定义 Operator 有特殊参数需要序列化，可以注册自定义序列化器：

```python
from airflow.serialization.serializers import register_serializer
from airflow.serialization.serialized_objects import BaseSerialization

class MyCustomObject:
    def __init__(self, name, value):
        self.name = name
        self.value = value

# 注册序列化器
@register_serializer
def serialize_custom(obj: MyCustomObject):
    if isinstance(obj, MyCustomObject):
        return {
            "__type": "MyCustomObject",
            "name": obj.name,
            "value": obj.value,
        }
    return BaseSerialization.serialize(obj)  # 回退到默认

# 注册反序列化器
@register_serializer
def deserialize_custom(data: dict, version: int):
    if data.get("__type") == "MyCustomObject":
        return MyCustomObject(data["name"], data["value"])
    return BaseSerialization.deserialize(data, version)
```

### 3.5 诊断 Dag 解析问题

**常见问题排查清单**：

```bash
# 1. 查看 Dag Processor 状态
docker compose ps airflow-dag-processor-1

# 2. 查看解析报错
docker compose logs airflow-dag-processor-1 | grep -i "error\|exception" | tail -20

# 3. 手动测试 Dag 加载
docker exec airflow-dag-processor-1 python -c "
from airflow.dag_processing.dagbag import DagBag
dagbag = DagBag('/opt/airflow/dags/')
for dag_id, dag in dagbag.dags.items():
    print(f'{dag_id}: {len(dag.tasks)} tasks')
for filepath, err in dagbag.import_errors.items():
    print(f'ERROR in {filepath}: {err}')
"

# 4. 查看序列化版本
docker exec airflow-scheduler-1 airflow dags list --output json | python -m json.tool
```

### 3.6 Dag Processor 高可用配置

```ini
# airflow.cfg
[dag_processor]
# 多实例运行（每个实例独立解析 Dag 文件）
max_runs = 2

# 解析超时（单文件）
dag_file_processor_timeout = 300

# 心跳超时
parsing_process_heartbeat_timeout = 120
```

---

## 4 项目总结

### Dag 解析链路总结

```
Dag 文件（Python）
    │
    ▼
DagBag 扫描 & import
    │
    ├─── 成功 → DAG 对象
    │           │
    │           ▼
    │     序列化（serialization/）
    │           │
    │           ▼
    │     SerializedDagModel（JSON）
    │           │
    │           ▼
    │     写入元数据库 dag 表
    │           │
    │           ├─── Scheduler 读取 → 创建 DagRun
    │           └─── Webserver 读取 → UI 展示
    │
    └─── 失败 → import_errors → 日志记录
```

### 常见 Dag 解析错误

| 错误类型 | 原因 | 解决 |
|---------|------|------|
| ModuleNotFoundError | 顶层 import 了未安装的库 | 检查 airflow.cfg 的 `pip_packages` 或在容器中安装 |
| SyntaxError | Dag 文件 Python 语法错误 | 运行 `python -m py_compile` 检查 |
| DagBag 加载超时 | 顶层代码做重 IO 操作 | 将重操作移到 Task 内部 |
| dag_id 冲突 | 两个文件定义了相同 dag_id | 全局搜索并重命名 |

### 思考题

1. Airflow 3.x 中，Scheduler 和 Webserver 都不直接读取 Dag 文件——它们读序列化后的 JSON。如果一个 Dag 文件中 import 了 `pandas`，但 Scheduler 的 Python 环境中没有安装 pandas，这个 Dag 能正常调度吗？为什么？
2. 如果你有 1000 个 Dag 文件，Dag Processor 每个循环都重新解析全部文件。这会有什么性能问题？如何优化？

*（答案将在下一章揭晓）*

---

> **本章完成**：你已理解了 Dag 文件从 Python 到 JSON 的完整旅程。下一章将深入 Airflow 的"心脏"——调度器源码架构。
