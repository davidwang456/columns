# 第7章：Flow编排与Job依赖管理

## 1. 项目背景

### 业务场景

数据平台团队维护了一个包含15个Job的复杂ETL Flow，每天凌晨执行。近期业务增长，新增了3个Job，并对依赖关系做了调整。但修改Flow依赖后，持续一周出现了两种诡异的现象：

现象一：新增加的"用户标签计算"Job总是等到"商品推荐"Job跑完才开始，但两者根本没有数据依赖关系——它们并行执行才对。

现象二：某个Job明明配置了`dependsOn`，但Azkaban在界面上显示它为"就绪"状态，和其上游Job同时并行启动了。

### 痛点放大

Flow依赖管理出问题时：

1. **隐式串行化**：本可并行的Job被错误地串联，导致总执行时间翻倍。原40分钟的任务链变成了80分钟。
2. **依赖黑洞**：当Job数量超过20个时，`.flow`文件和每个`.job`文件的依赖关系分散在各处，维护者难以快速画出完整的DAG图。
3. **循环依赖**：A依赖B，B依赖C，C又依赖A——这将导致Flow根本无法执行，但Azkaban不一定会给出明确报错，只是"永远在等待"。
4. **跨Flow依赖缺失**：Azkaban原生不支持"Flow A完成后自动触发Flow B"的跨Flow依赖，只能通过API手动编排。

## 2. 项目设计——剧本式交锋对话

**小胖**（手忙脚乱地在15个.job文件之间切来切去）：大师，我现在头晕！15个Job的依赖关系全写在每个文件的`dependsOn`里，没有一张全局视图。每次有人问"这个Flow的执行顺序是什么"，我都要打开所有文件一个一个看。

**大师**：这就是我常说的"分布式依赖的痛"。Azkaban的设计把依赖关系内聚在每个Job里——这是优点，但当Job多了，维护者就需要一张"全局地图"。

**小胖**：那有什么好办法吗？能不能有个工具自动生成DAG图？

**大师**：当然有。我给你一个Python脚本，能读取所有`.job`文件，自动生成DAG的Mermaid格式描述，粘贴到Markdown里就能渲染出来：

```python
# generate_dag.py
import os

def parse_depends(job_file):
    """解析.job文件中的dependsOn"""
    depends_on = []
    with open(job_file, 'r') as f:
        for line in f:
            line = line.strip()
            if line.startswith('dependsOn='):
                deps = line.split('=', 1)[1].strip()
                if deps:
                    depends_on = [d.strip() for d in deps.split(',')]
    return depends_on

def generate_mermaid_dag(directory):
    """遍历目录, 生成Mermaid DAG描述"""
    print('```mermaid')
    print('graph TD')
    for fname in sorted(os.listdir(directory)):
        if fname.endswith('.job'):
            job_name = fname[:-4]
            deps = parse_depends(os.path.join(directory, fname))
            if deps:
                for dep in deps:
                    print(f'    {dep} --> {job_name}')
            else:
                print(f'    START([START]) --> {job_name}')
    print('```')
```

**小白**（眼睛一亮）：这个好！但我的问题是，如果两个Job之间没有数据依赖，但我想让它们按某种顺序执行（比如"因为服务器资源有限，不想同时跑10个Spark任务"），Azkaban能支持这种"非数据依赖的串行"吗？

**大师**：问得非常好。这就是"资源约束"和"数据依赖"的区别。Azkaban本身只管数据依赖——即"A的输出是B的输入"。对于"资源约束"（我想限制并行度），Azkaban没有内置机制。

但你可以通过"伪依赖"的方式实现——创建一个`dummy_resource_guard` Job，假装它有数据依赖关系，把一个并行的Flow"拉成"串行的。

**小胖**：这不是hack吗？有没有更优雅的方法？

**大师**：在Azkaban层面，目前有以下几种处理方式：

1. **分拆Flow**：把15个Job拆成3个Flow，每个Flow内部并行，Flow之间用API串联。
2. **并发控制参数**：在Executor层面配置`executor.max.threads`，限制同时执行的Job数量。
3. **下一章会讲的"子Flow"**：用嵌套Flow隔离Spark任务组。

**小白**：那依赖的传递性呢？A依赖B，B依赖C——那A是否自动依赖C？如果我删掉B，A是自动连到C还是断开？

**大师**：Azkaban中，依赖关系是**严格的直接依赖**，不传递。A只关心B是否成功，不关心B依赖谁。如果你删除了B这个"中间人"，A就失去了依赖，会变成"无依赖直接启动"。所以删除中间Job时，务必同时修改下游Job的`dependsOn`。

**小胖**：最后一个问题——圆形依赖怎么检测？我上周犯了个错，A依赖B，B依赖A，Azkaban直接就卡死了，没有任何报错！

**大师**：这确实是Azkaban的一个短板——它不做循环依赖的编译期检查。因为依赖分散在各个.job文件中，只有运行时构建DAG时才能发现。

我给你的建议是——在提交Flow之前，用脚本提前做循环检测：

```python
# 拓扑排序 + 环检测
from collections import deque

def has_cycle(jobs):
    indegree = {j: 0 for j in jobs}
    for j, deps in jobs.items():
        for d in deps:
            indegree[j] += 1

    queue = deque(j for j, deg in indegree.items() if deg == 0)
    visited = 0
    while queue:
        node = queue.popleft()
        visited += 1
        for j, deps in jobs.items():
            if node in deps:
                indegree[j] -= 1
                if indegree[j] == 0:
                    queue.append(j)
    return visited != len(jobs)  # 有未被访问的节点 => 存在环
```

### 技术映射总结

- **dependsOn** = 剧本中的"前情提要"（先演完上一幕，再演这一幕）
- **DAG** = 地铁线路图（有方向、无环路、看颜色就知道哪站到哪站）
- **并行Job** = 不同的收银台（同时服务，互不干扰）
- **循环依赖** = 两个人互相等对方先开口（永远僵住，死锁）

## 3. 项目实战

### 3.1 环境准备

Azkaban Solo Server运行中，准备一个包含5个Job的测试项目。

### 3.2 分步实现

#### 步骤1：基础依赖——线性依赖

**目标**：创建A → B → C的串行依赖链。

```bash
# job_a.job
type=command
command=echo "Job A: Starting data generation..."
command.1=sleep 2
command.2=echo "Job A: Done. Output: /tmp/a_output.txt"

# job_b.job
type=command
command=echo "Job B: Processing A's output..."
command.1=sleep 3
command.2=echo "Job B: Done."
dependsOn=job_a

# job_c.job
type=command
command=echo "Job C: Final aggregation..."
command.1=sleep 2
command.2=echo "Job C: Done."
dependsOn=job_b
```

**执行效果**：A先跑→B等待A完成→B跑→C等待B完成→C跑。总耗时约7秒（2+3+2）。

#### 步骤2：并行依赖——扇出与扇入

**目标**：创建A完成后，B和C并行，然后D等待B和C都完成的结构。

```bash
# job_data_source.job
type=command
command=echo "Data source ready"
command.1=sleep 1

# job_branch_left.job
type=command
command=echo "Branch Left processing..."
command.1=sleep 3
dependsOn=job_data_source

# job_branch_right.job
type=command
command=echo "Branch Right processing..."
command.1=sleep 5
dependsOn=job_data_source

# job_merge.job
type=command
command=echo "Merging results from both branches..."
command.1=sleep 1
dependsOn=job_branch_left,job_branch_right
```

**DAG图**：
```
        ┌─────────────┐
        │ data_source │ (1s)
        └──────┬──────┘
        ┌──────┴──────┐
        ▼              ▼
┌───────────┐   ┌───────────┐
│branch_left│   │branch_right│ (并行，3s vs 5s，取最慢者：5s)
└─────┬─────┘   └─────┬─────┘
      └───────┬───────┘
              ▼
        ┌──────────┐
        │  merge   │ (1s)
        └──────────┘
```

总耗时约7秒（1 + max(3,5) + 1），而不是串行的10秒（1+3+5+1）。

**Flow定义**：

```
# parallel_demo.flow
nodes=job_data_source,job_branch_left,job_branch_right,job_merge
```

**关键点**：`dependsOn=job_branch_left,job_branch_right`中用逗号分隔多个依赖，表示必须**所有父Job都成功**后才能启动。

#### 步骤3：条件依赖——部分成功

**目标**：多个上游Job中，部分失败不影响下游。

```bash
# job_required.job
type=command
command=echo "Required job - always runs"
command.1=sleep 1

# job_optional.job
type=command
command=echo "Optional job - may fail"
command.1=sleep 2
command.2=exit 1  # 故意失败，但下游不应被阻塞
retries=0

# job_downstream.job
type=command
command=echo "Downstream - only depends on required"
command.1=sleep 1
dependsOn=job_required  # 只依赖必须成功的Job

# all_nodes.flow
nodes=job_required,job_optional,job_downstream
```

**执行逻辑**：`job_required`和`job_optional`并行启动。`job_optional`失败后，`job_downstream`因为只依赖`job_required`，不受影响，可以正常执行。

#### 步骤4：失败阻断策略

**目标**：控制Flow失败后的行为。

```bash
# critical_chain.job
type=command
command=echo "Critical chain execution control"

# 全局Flow参数控制失败行为
# 方式1：在Job级别设置 failure.emails
failure.emails=admin@company.com

# 方式2：在Flow执行时通过Web界面选择失败策略
# - Finish Current Running: 当前running的Job继续，但不启动新Job
# - Cancel All: 立即取消所有Job
# - Finish All Possible: 不受失败影响的Job继续（默认）
```

**REST API执行时指定策略**：

```bash
curl -b cookies.txt \
  -X POST "http://localhost:8081/executor?ajax=executeFlow" \
  --data "project=demo&flow=critical_chain" \
  --data "failureAction=finishCurrent"  # finishCurrent | cancel | finishPossible
```

#### 步骤5：依赖可视化脚本

**目标**：自动生成Flow的DAG可视化描述。

```python
#!/usr/bin/env python3
# visualize_flow.py —— 从.job文件生成DAG描述

import os
import sys
import json

def parse_job_file(filepath):
    """解析单个.job文件，提取dependsOn"""
    job_name = os.path.basename(filepath).replace('.job', '')
    config = {'name': job_name, 'dependsOn': []}

    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if '=' not in line:
                continue
            key, _, value = line.partition('=')
            key = key.strip()
            value = value.strip()
            if key == 'dependsOn' and value:
                config['dependsOn'] = [d.strip() for d in value.split(',')]
    return config

def build_dag(flow_dir):
    """构建DAG"""
    jobs = {}
    for fname in sorted(os.listdir(flow_dir)):
        if fname.endswith('.job'):
            job = parse_job_file(os.path.join(flow_dir, fname))
            jobs[job['name']] = job['dependsOn']

    # 检测循环依赖
    indegree = {j: len(deps) for j, deps in jobs.items()}
    queue = [j for j, deg in indegree.items() if deg == 0]
    sorted_jobs = []

    while queue:
        node = queue.pop(0)
        sorted_jobs.append(node)
        for j, deps in jobs.items():
            if node in deps:
                indegree[j] -= 1
                if indegree[j] == 0:
                    queue.append(j)

    if len(sorted_jobs) != len(jobs):
        print("❌ ERROR: Circular dependency detected!")
        return None

    # 输出执行顺序
    print("✓ DAG Execution Order:")
    for i, job in enumerate(sorted_jobs):
        level = "(ROOT) " if not jobs[job] else ""
        deps = jobs[job]
        dep_str = f" ← depends on [{', '.join(deps)}]" if deps else ""
        print(f"  {i+1}. {level}{job}{dep_str}")

    return jobs

if __name__ == '__main__':
    flow_dir = sys.argv[1] if len(sys.argv) > 1 else '.'
    build_dag(flow_dir)
```

**运行示例**：

```bash
python3 visualize_flow.py ./parallel_demo/
# 输出：
# ✓ DAG Execution Order:
#   1. (ROOT) job_data_source
#   2. job_branch_left ← depends on [job_data_source]
#   3. job_branch_right ← depends on [job_data_source]
#   4. job_merge ← depends on [job_branch_left, job_branch_right]
```

### 3.3 测试验证

```bash
#!/bin/bash
# test_dag_consistency.sh

echo "=== DAG一致性验证 ==="

# 1. 检查Flow中声明的nodes是否与.job文件匹配
FLOW_NODES=$(grep "^nodes=" *.flow | cut -d= -f2 | tr ',' '\n' | sort)
JOB_FILES=$(ls *.job | sed 's/\.job//' | sort)

echo "[Test 1] Flow nodes vs .job files consistency..."
if diff <(echo "$FLOW_NODES") <(echo "$JOB_FILES") > /dev/null; then
    echo "  [PASS] Flow nodes match .job files"
else
    echo "  [FAIL] Mismatch:"
    echo "    Flow: $(echo $FLOW_NODES | xargs)"
    echo "    Jobs: $(echo $JOB_FILES | xargs)"
fi

# 2. 检查dependsOn引用的Job是否存在
echo "[Test 2] Dependency reference integrity..."
for job in *.job; do
    DEPS=$(grep "^dependsOn=" "$job" | cut -d= -f2-)
    if [ -n "$DEPS" ]; then
        for dep in $(echo "$DEPS" | tr ',' '\n'); do
            if [ ! -f "${dep}.job" ]; then
                echo "  [FAIL] ${job%.job} dependsOn '$dep' but ${dep}.job does not exist!"
            fi
        done
    fi
done
echo "  [PASS] All dependency references valid"

# 3. 循环依赖检测
echo "[Test 3] Circular dependency check..."
python3 visualize_flow.py 2>&1 | grep -q "Circular dependency"
if [ $? -eq 0 ]; then
    echo "  [FAIL] Circular dependency found!"
else
    echo "  [PASS] No circular dependencies"
fi

echo "=== 验证完成 ==="
```

## 4. 项目总结

### 依赖管理方式对比

| 维度 | Azkaban | Airflow | DolphinScheduler |
|------|---------|---------|------------------|
| 依赖定义位置 | 分散在.job文件 | 集中在Python DAG | 可视化连线 |
| 全局视图 | 需额外工具 | 代码即DAG | 界面即DAG |
| 循环检测 | 运行时才发现 | 编译时检测 | 保存时检测 |
| 跨Flow依赖 | 不支持 | SubDag/TaskGroup | 上下游依赖 |
| 并行度 | 自动（无上限） | 配置（pool/task_concurrency） | 配置（并行度） |

### 适用场景

- **适用**：10-100个Job的中等规模ETL流水线、依赖关系清晰的数据处理链路、需要自动并行加速的场景
- **不适用**：跨项目的复杂工作流编排、需要动态分支/条件判断的流程、超大规模（500+ Job）的DAG

### 注意事项

- `dependsOn`中的Job名必须与.job文件名一致（大小写敏感）
- 一个Job可以有多个`dependsOn`，用逗号分隔
- 删除或重命名中间Job时，务必更新下游Job的`dependsOn`
- `.flow`文件中的`nodes`声明不是可选的——未声明在nodes中的Job不会被Azkaban识别

### 常见踩坑经验

1. **dependsOn隐形失效**：在.job文件中写了`dependsOn=job_x`，但同时在.flow文件中又声明了该Job与job_x"没有关系"（如放在不同的子目录）。Azkaban不报错，但依赖关系被忽略。
2. **扇形依赖导致Flow过长**：当100个Job依赖同一个上游Job时，这100个Job会同时启动，可能导致Executor线程池耗尽。解决：设置Flow级别的`flow.max.running.jobs`或在Executor侧限制并发数。
3. **命名冲突**：两个`.flow`文件中包含了同名的Job，但实际文件只有一个。Azkaban在Flow交叉引用时可能混乱。

### 思考题

1. 如果一个Flow有100个Job，其中50个是独立的（无依赖关系），它们会全部并行执行。但Executor只有20个线程。请设计一个策略，让这50个Job分3批执行，每批不超过20个。
2. 假设你有Flow A和Flow B，需要在"A完全成功后自动触发B"。Azkaban原生不支持跨Flow依赖。请设计两种方案来实现这个需求，并对比优缺点。
