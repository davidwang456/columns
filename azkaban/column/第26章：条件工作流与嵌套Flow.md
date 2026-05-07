# 第26章：条件工作流与嵌套Flow

## 1. 项目背景

### 业务场景

数据团队的ETL流程中有一个"数据质量检查"步骤——如果检查通过（通过率>95%），继续执行后续的"数据分析"；如果不通过，则执行"数据修复"分支。但Azkaban的dependsOn机制是"全有或全无"——所有上游Job都成功时下游才执行，无法根据上游Job的输出来动态选择不同的下游路径。

此外，核心Flow越来越复杂，包含50个Job，维护单一Flow变得极其困难。团队想把它拆成多个子Flow，但Azkaban没有原生的"嵌套Flow"或"子流程"概念。

### 痛点放大

没有条件分支和嵌套能力时：

1. **线性固化的DAG**：所有路径都是硬编码的，无法根据运行时状态动态选择
2. **超级Flow失控**：一个Flow有80个Job，团队不敢修改其中一个Job——怕影响下游依赖
3. **重复定义**：多个Flow中都有"MySQL导出→HDFS上传→Hive建表"这三步，需要重复配置

## 2. 项目设计——剧本式交锋对话

**小胖**（绝望地看着80个Job的DAG图）：大师，这个Flow快失控了。一个同事不小心改了其中某个Job的dependsOn，直接搞出了循环依赖。我想把它拆成3个子Flow，但不知道怎么让它们自动串联……

**大师**：这是Azkaban的两个核心挑战：**条件分支**和**子Flow复用**。

**小白**：Azkaban支持if-else吗？比如"检查通过就做A，不通过就做B"？

**大师**：Azkaban原生不支持条件分支。dependsOn的语义是"所有依赖都成功才执行"，而不是"根据依赖的输出决定是否执行"。

但你可以模拟条件分支：

```bash
# check_quality.job
command=python3 quality_check.py
# quality_check.py:
#   - 检查通过 → exit 0
#   - 检查失败但可修复 → exit 2
#   - 检查严重失败 → exit 1

# analysis.job —— 只有检查通过才执行
dependsOn=check_quality
# 如果check_quality exit≠0，analysis不会执行

# repair.job —— 检查失败执行修复
dependsOn=check_quality
# 问题：repair也会因为check_quality失败而被阻塞！
```

**小胖**：那怎么让repair在check_quality失败后执行呢？

**大师**：经典的方法是"翻转依赖"：

```bash
# check_quality.job —— 永远返回成功（但通过文件标记结果）
command=bash -c '
python3 quality_check.py
RESULT=$?
echo "PASSED" > /tmp/quality_result.txt  # 或 "FAILED"
exit 0  # 总是成功！
'

# analysis.job —— 读标记文件决定是否执行
command=bash -c '
if grep -q "PASSED" /tmp/quality_result.txt; then
    echo "Quality check passed, proceeding..."
    python3 run_analysis.py
else
    echo "Quality check failed, skipping analysis"
    exit 0  # 跳过但不失败
fi
'
dependsOn=check_quality

# repair.job —— 读标记文件决定是否执行
command=bash -c '
if grep -q "FAILED" /tmp/quality_result.txt; then
    echo "Quality check failed, running repair..."
    python3 run_repair.py
else
    echo "No repair needed"
    exit 0
fi
'
dependsOn=check_quality
```

**小白**：那嵌套Flow呢？怎么把一个大Flow拆成多个子Flow？

**大师**：Azkaban支持"嵌套Flow"——在.flow文件中通过`embedded.flows`声明子Flow：

```
# parent.flow
nodes=step_a,sub_flow,step_d
embedded.flows=sub_flow

# sub_flow.flow  
nodes=step_b,step_c
```

但说实话，嵌套Flow在3.x版本中支持得不完善。我更推荐以下方案来组织复杂工作流：

1. **逻辑拆分**：按功能将80个Job拆成4-5个独立Flow，每个Flow20个Job，然后用API或文件信号串联
2. **Git子模块**：将公共的Job模板抽取为独立的Git仓库，各项目通过子模块引用

### 技术映射总结

- **条件分支** = 十字路口（根据信号灯动态选择走左还是走右）
- **嵌套Flow** = 文件柜嵌套（大文件夹里放小文件夹）
- **标记文件** = 工作交接单（上个人做完后在交接单上打钩，下个人根据钩决定做什么）
- **子Flow** = 预制构件（把一段标准墙预先造好，盖楼时直接拼装）

## 3. 项目实战

### 3.1 环境准备

Azkaban运行中，准备模拟条件分支和嵌套场景。

### 3.2 分步实现

#### 步骤1：基于退出码的条件分支

**目标**：通过自定义退出码实现简单的条件分支。

```bash
# gatekeeper.job —— 守门员Job
type=command
command=bash -c '
echo "=== Gatekeeper: Quality Check ==="
echo "Checking data quality..."

# 模拟质量检查结果
QUALITY_SCORE=$((RANDOM % 100))
echo "Quality score: $QUALITY_SCORE%"

if [ $QUALITY_SCORE -ge 95 ]; then
    echo "PASS: Quality score ${QUALITY_SCORE}% >= 95%"
    echo "BRANCH=success" > /tmp/azkaban_branch.txt
    exit 0
elif [ $QUALITY_SCORE -ge 80 ]; then
    echo "WARN: Quality score ${QUALITY_SCORE}% >= 80%, need repair"
    echo "BRANCH=repair" > /tmp/azkaban_branch.txt
    exit 2  # 自定义退出码：需要修复
else
    echo "FAIL: Quality score ${QUALITY_SCORE}% < 80%, critical issue"
    echo "BRANCH=failure" > /tmp/azkaban_branch.txt
    exit 1  # 严重失败
fi
'

# success_path.job —— 成功路径
type=command
command=bash -c '
echo "=== Success Path ==="
echo "Data quality passed, running full analysis..."
python3 full_analysis.py
'
dependsOn=gatekeeper

# repair_path.job —— 修复路径
type=command
command=bash -c '
. /tmp/azkaban_branch.txt
echo "=== Repair Path ==="
echo "Quality check result: $BRANCH"
if [ "$BRANCH" = "repair" ]; then
    echo "Running data repair..."
    python3 data_repair.py
else
    echo "No repair needed (branch=$BRANCH), skipping"
fi
'
dependsOn=gatekeeper

# fail_path.job —— 失败处理
type=command
command=bash -c '
. /tmp/azkaban_branch.txt
echo "=== Failure Path ==="
if [ "$BRANCH" = "failure" ]; then
    echo "Quality critically failed. Aborting pipeline."
    exit 1
else
    echo "Branch is ${BRANCH}, not a failure. Skipping."
    exit 0
fi
'
dependsOn=gatekeeper
```

#### 步骤2：条件Flow——读标记文件驱动

**目标**：使用标记文件实现更清晰的条件分支。

```bash
# conditional_flow.flow
nodes=check_gate,branch_a,branch_b,branch_c
```

```bash
# check_gate.job —— 网关节点，设置分支标记
type=command
command=bash -c '
echo "=== Condition Gate ==="

# 检查HDFS上是否有当天的原始数据
HDFS_DATA_PATH="/data/raw/dt=${process_date}"
if hdfs dfs -test -e "$HDFS_DATA_PATH"; then
    FILE_COUNT=$(hdfs dfs -ls "$HDFS_DATA_PATH" | wc -l)
    FILE_SIZE=$(hdfs dfs -du -s "$HDFS_DATA_PATH" | awk "{print \$1}")
    
    echo "Raw data path: $HDFS_DATA_PATH"
    echo "File count: $FILE_COUNT"
    echo "Total size: ${FILE_SIZE} bytes"
    
    if [ $FILE_COUNT -gt 10 ] && [ $FILE_SIZE -gt 1048576 ]; then
        echo "CONDITION=full_dataset" > /tmp/flow_condition.txt
        echo "条件: 全量数据集"
    elif [ $FILE_COUNT -gt 0 ]; then
        echo "CONDITION=partial_dataset" > /tmp/flow_condition.txt
        echo "条件: 部分数据集"
    else
        echo "CONDITION=empty_dataset" > /tmp/flow_condition.txt
        echo "条件: 空数据集"
    fi
else
    echo "CONDITION=no_data" > /tmp/flow_condition.txt
    echo "条件: 无数据"
fi

cat /tmp/flow_condition.txt
exit 0  # 无论什么条件，网关都返回成功
'

# branch_a.job —— 处理全量数据
type=command
command=bash -c '
. /tmp/flow_condition.txt
if [ "$CONDITION" = "full_dataset" ]; then
    echo "Processing full dataset..."
    python3 process_full.py
else
    echo "Condition is $CONDITION, skipping full processing"
    exit 0
fi
'
dependsOn=check_gate

# branch_b.job —— 处理部分数据（含修复逻辑）
type=command
command=bash -c '
. /tmp/flow_condition.txt
if [ "$CONDITION" = "partial_dataset" ]; then
    echo "Processing partial dataset with gap-filling..."
    python3 process_partial.py --fill_gaps
else
    echo "Condition is $CONDITION, skipping partial processing"
    exit 0
fi
'
dependsOn=check_gate

# branch_c.job —— 无数据时的通知
type=command
command=bash -c '
. /tmp/flow_condition.txt
if [ "$CONDITION" = "no_data" ] || [ "$CONDITION" = "empty_dataset" ]; then
    echo "⚠️  No data available for date: ${process_date}"
    python3 /opt/scripts/no_data_alert.py --date="${process_date}"
    exit 0
else
    exit 0
fi
'
dependsOn=check_gate
```

#### 步骤3：嵌套Flow（子Flow）

**目标**：将公共的ETL三步封装为子Flow。

```bash
# parent_flow.flow —— 父Flow
nodes=init,sub_etl,finalize
embedded.flows=sub_etl
```

```bash
# sub_etl.flow —— 子Flow（三合一：导出→上传→建表）
nodes=export_mysql,upload_hdfs,create_hive_table
```

```bash
# 父Flow中的Job引用子Flow
# init.job —— 初始化
type=command
command=echo "Parent flow starting..."
command.1=echo "Sub-flow will handle: export → upload → create table"

# finalize.job —— 收尾（依赖子Flow整体完成）
type=command
command=echo "Sub-flow completed, running finalize..."
dependsOn=sub_etl  # 注意：依赖的是子Flow整体，不是其中的某个Job！
```

#### 步骤4：Flow拆分与API串联

**目标**：将大Flow拆分为独立Flow，用API串联。

```bash
# 原始大Flow（80个Job）
# 拆分为 4 个独立Flow

# Flow 1: 数据采集（20个Job）→ 主调度凌晨1:00
# Flow 2: 数据清洗（20个Job）→ 等Flow 1完成后自动触发
# Flow 3: 指标计算（20个Job）→ 等Flow 2完成后自动触发
# Flow 4: 报表生成（20个Job）→ 等Flow 3完成后自动触发

# flow_chain.sh —— Flow串联脚本
```

```python
#!/usr/bin/env python3
# flow_orchestrator.py —— Flow编排器

import time
import requests

class FlowOrchestrator:
    """Flow编排器：将多个独立Flow串行/并行编排"""
    
    def __init__(self, azkaban_url, session):
        self.base_url = azkaban_url
        self.session = session
    
    def run_pipeline(self, stages):
        """
        stages = [
            {"flows": ["data_collection"], "parallel": False},
            {"flows": ["data_cleanse", "user_profile"], "parallel": True},
            {"flows": ["metrics_calculation"], "parallel": False},
            {"flows": ["report_generation", "data_export"], "parallel": True},
        ]
        """
        for stage in stages:
            print(f"\n{'='*50}")
            print(f"Stage: {'Parallel' if stage['parallel'] else 'Sequential'}")
            
            exec_ids = []
            # 提交本阶段所有Flow
            for flow_name in stage["flows"]:
                exec_id = self._execute_flow("core_pipeline", flow_name)
                exec_ids.append((flow_name, exec_id))
                print(f"  Started: {flow_name} (exec_id: {exec_id})")
            
            # 等待本阶段所有Flow完成
            for flow_name, exec_id in exec_ids:
                status = self._wait_for_completion(exec_id, timeout=3600)
                print(f"  Completed: {flow_name} -> {status}")
                
                if status == "FAILED":
                    print(f"  ✗ Pipeline stopped due to {flow_name} failure")
                    return False
        
        print(f"\n{'='*50}")
        print("Pipeline completed successfully!")
        return True
    
    def _execute_flow(self, project, flow):
        resp = requests.post(
            f"{self.base_url}/executor?ajax=executeFlow",
            data={"project": project, "flow": flow},
            cookies={"azkaban.browser.session.id": self.session}
        )
        return resp.json().get("execid")
    
    def _wait_for_completion(self, exec_id, timeout=3600):
        start = time.time()
        while time.time() - start < timeout:
            resp = requests.get(
                f"{self.base_url}/executor",
                params={"execid": exec_id, "ajax": "fetchexecflow"},
                cookies={"azkaban.browser.session.id": self.session}
            )
            status = resp.json().get("status")
            if status in ("SUCCEEDED", "FAILED", "KILLED"):
                return status
            time.sleep(15)
        return "TIMEOUT"

if __name__ == '__main__':
    orch = FlowOrchestrator("http://localhost:8081", "session_xxx")
    orch.run_pipeline([
        {"flows": ["data_collection"], "parallel": False},
        {"flows": ["data_cleanse", "user_profile"], "parallel": True},
        {"flows": ["report_generation"], "parallel": False},
    ])
```

### 3.3 测试验证

```bash
# 1. 测试条件分支
curl -b cookies.txt -X POST "http://localhost:8081/executor?ajax=executeFlow" \
  --data "project=branch_test&flow=conditional_flow"

# 2. 测试嵌套Flow
curl -b cookies.txt -X POST "http://localhost:8081/executor?ajax=executeFlow" \
  --data "project=nested_test&flow=parent_flow"

# 3. 测试Flow编排
python3 flow_orchestrator.py
```

## 4. 项目总结

### 条件/嵌套方案对比

| 方案 | 灵活性 | 可维护性 | 局限性 |
|------|--------|---------|--------|
| 退出码分支 | ★★☆ | ★★☆ | 无法并行分支 |
| 标记文件分支 | ★★★ | ★★☆ | 标记文件管理复杂 |
| 嵌套Flow | ★★☆ | ★★★ | 3.x支持不完善 |
| API编排 | ★★★ | ★★☆ | 需要外部编排器 |

### 适用场景

- **适用**：有条件的ETL流程（数据满足条件才继续）、需要复用的公共子流程、超大型Flow需要拆分
- **不适用**：简单线性的固定流程（过度设计）、对延迟极度敏感的场景

### 注意事项

- 标记文件方案中，多个并发Flow需用唯一文件名
- 嵌套Flow中的子Flow Job不能在父Flow中直接引用
- API编排方案需要考虑编排器自身的HA
- 条件分支中"跳过的路径"应返回exit 0而非exit 1

### 常见踩坑经验

1. **嵌套Flow中的dependsOn跨级问题**：父Flow的Job不能直接`dependsOn=子Flow中的Job名`。只能依赖子Flow整体。
2. **标记文件残留**：上次Flow的标记文件没清理，下次执行时读到的是旧结果。解决：在网关Job开始时先清理标记文件。
3. **条件分支的DAG图不变**：Azkaban的DAG图是在Flow开始时确定的，条件分支虽然在运行时会跳过某些Job，但DAG图里它们仍显示为"READY→SKIPPED"。

### 思考题

1. 如何实现一个"循环结构"——让某个Job重复执行直到满足条件（如等待数据到达）？Azkaban原生不支持循环，请设计实现方案。
2. 如果你有100个Flow需要按DAG顺序编排（部分并行），但Azkaban不支持跨Flow依赖。请设计一个"全局调度引擎"来管理这种复杂依赖。
