# 第33章：Workflow 图引擎源码完全剖析

## 1. 项目背景

第 22 章我们阅读了 WorkflowEntry 入口和拓扑排序。本章深入 **GraphEngine** 的全部核心源码——理解图如何从 JSON DSL 构建为可执行对象、线程池如何调度并行节点、变量池如何注册节点输出、节点失败时图如何处理、以及如何添加进度事件。

读完本章你将能够：修改 GraphEngine 添加自定义行为（如进度百分比、允许部分节点失败后继续）、理解"为什么有些节点没有并行执行"的根因、为自定义节点写入正确的执行上下文。

核心源码文件：
- `api/core/workflow/workflow_entry.py`：入口 + GraphEngine 类
- `api/core/workflow/node_factory.py`：节点工厂，类型注册与实例化
- `api/core/workflow/node_runtime.py`：节点运行时状态（封装了输入获取/输出注册）
- `api/core/workflow/variable_pool_initializer.py`：变量池

## 2. 项目设计——剧本式交锋对话

**小胖**：（看着 Workflow 执行日志）"大师！我 20 个节点的 Workflow，理论上步骤 3 和步骤 4 应该同时执行（它们不互相依赖），但日志显示是先后执行的。为什么没有并行？"

**大师**："三种可能。**①线程池 max_workers 限制**——默认是 10。如果当前批次有 15 个节点入度同时为 0，只有前 10 个立刻执行，后 5 个排队等前面完成。你的情况可能是批次 2 只有 2 个节点——应该都并行，所以不是这个问题。**②它们实际上有间接依赖**——虽然节点 C 和 D 之间没有直接的连线，但它们可能都依赖同一个上游节点 B。在拓扑排序中，B 完成后才能开始下一批次——C 和 D 确实在同一批次，应该并行。检查 DSL 确认。**③Python GIL 的假象**——对于 I/O 密集型任务（等 LLM、等 HTTP），线程池的并行是有效的。但如果你的节点是 CPU 密集型（如 Code 节点里的 `for i in range(10**8)`），GIL 会让它们看起来是串行的。"

**技术映射**：并行度 = min(批次节点数, max_workers)。如果两个看起来应该并行的节点实际串行了——检查是否有隐藏的连线或线程池已满。

**小白**："节点失败了图怎么办？会全部崩吗？"

**大师**："默认行为：一个节点失败 → `GraphEngine._run_node()` 抛 `NodeExecutionError` → `run()` 中的 `try-except` 捕获 → 推送 `GraphFailedEvent` → **整个图终止**。失败节点之后的节点永远不会执行——它们的入度永远不会变为 0（因为失败节点没有发出'下游入度减 1'的信号）。

如果你想要'允许部分节点失败后继续'，需要修改 `run()` 中的异常处理：不 return，仍把下游入度减 1（相当于'跳过这个节点'）。但这有风险——下游节点可能依赖失败节点的输出，拿到 None 后未必能正确处理。"

**技术映射**：失败策略 = 默认终止（Fail-Fast）vs 可选跳过（Fail-Silent）。后者需要下游节点能处理缺失的输入。

**小胖**："如果我需要进度百分比——比如看到'正在执行第 3/20 个节点'——怎么加？"

**大师**："在 `run()` 的 `for future in as_completed(futures)` 循环中加一个计数器。每完成一个节点，计算 `completed / total * 100`，包装成 `ProgressEvent` yield 出去。前端收到这个事件后更新进度条。"

## 3. 项目实战

### GraphEngine 核心执行循环（完整带注释版）

```python
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict, deque

class GraphEngine:
    def __init__(self, graph_config, node_factory, variable_pool):
        # 通过工厂创建所有节点实例
        self.nodes = {n.id: node_factory.create(n) for n in graph_config.nodes}
        self.edges = graph_config.edges
        self.variable_pool = variable_pool
        self.total_nodes = len(self.nodes)
        self._build_graph()
    
    def _build_graph(self):
        """构建邻接表 + 入度表——拓扑排序的数据基础"""
        self.adjacency = defaultdict(list)       # {source: [target1, target2, ...]}
        self.in_degree = defaultdict(int)        # {node_id: incoming_edge_count}
        
        for edge in self.edges:
            self.adjacency[edge.source].append(edge.target)
            self.in_degree[edge.target] += 1
    
    def run(self):
        """★ 主执行循环——BFS 拓扑遍历 + 线程池并行"""
        # 入度为 0 的节点 = 无前置依赖，最先执行
        queue = deque([nid for nid in self.nodes if self.in_degree[nid] == 0])
        
        if not queue:
            raise WorkflowError("无法找到起始节点（检查是否有环路或未连接）")
        
        completed_count = 0
        
        while queue:
            # ★ 当前批次 = 所有入度为 0 的节点（可并行）
            batch = list(queue)
            queue.clear()
            
            # ★ 线程池并行执行当前批次
            with ThreadPoolExecutor(max_workers=10) as executor:
                futures = {
                    executor.submit(self._run_node, node_id): node_id 
                    for node_id in batch
                }
                
                for future in as_completed(futures):
                    node_id = futures[future]
                    
                    try:
                        # 等待节点执行完成
                        output = future.result()
                        # ★ 注册输出到变量池（下游节点才能引用）
                        self.variable_pool.add_node_output(node_id, output)
                        
                        completed_count += 1
                        yield ProgressEvent(
                            completed=completed_count,
                            total=self.total_nodes,
                            percentage=int(completed_count / self.total_nodes * 100)
                        )
                        
                    except NodeExecutionError as e:
                        # ★ 默认：一个失败 = 全图终止
                        yield GraphFailedEvent(
                            node_id=node_id, 
                            error=str(e),
                            message=f"节点执行失败，Workflow 已终止"
                        )
                        return  # ← 这里终止整个图
                        
                        # ★ [可选修改] 允许失败后继续：
                        # yield NodeFailedEvent(node_id=node_id, error=str(e))
                        # # 仍然唤醒下游（下游可能收不到该节点的输出）
                        # for neighbor in self.adjacency[node_id]:
                        #     self.in_degree[neighbor] -= 1
                        #     if self.in_degree[neighbor] == 0:
                        #         queue.append(neighbor)
                        # continue  # 跳过 return，继续处理
                    
                    # ★ 唤醒下游：每个下游节点的入度减 1
                    for neighbor in self.adjacency[node_id]:
                        self.in_degree[neighbor] -= 1
                        if self.in_degree[neighbor] == 0:
                            queue.append(neighbor)  # 入度变为 0 → 下一批可执行
            
            # yield 一个批次完成事件（前端可渲染批次进度）
            yield BatchCompletedEvent(
                batch_nodes=batch,
                remaining=sum(self.in_degree.values()),
            )
        
        # 所有节点执行完毕
        yield WorkflowFinishedEvent(outputs=self.variable_pool.export())
    
    def _run_node(self, node_id: str):
        """执行单个节点——包装事件推送"""
        node = self.nodes[node_id]
        runtime = NodeRuntime(node_id, self.variable_pool)
        
        yield NodeStartedEvent(node_id=node_id, title=node.title, type=node.type)
        
        try:
            output = node._run(runtime)
            yield NodeFinishedEvent(node_id=node_id, output=output)
            return output
        except Exception as e:
            yield NodeFailedEvent(node_id=node_id, error=str(e))
            raise NodeExecutionError(str(e)) from e
```

### 修改引擎——添加进度百分比

```python
# 在 run() 中添加（见上面代码中的 ProgressEvent 部分）
# completed_count 在每次 future.result() 成功后 +1
# yield ProgressEvent(...) 推送进度
# 前端收到 event.event == 'progress' 后更新进度条
```

### 修改引擎——允许失败后继续

```python
# 将 run() 中 except NodeExecutionError 块的 return 改为：
# 唤醒下游 + continue（见上面代码中的注释部分）
# 警告：下游节点需要能处理"前序节点输出为 None"的情况
```

### 测试验证

```bash
# 1. 导出 Workflow DSL 后用 Python 手动验证拓扑排序
python -c "
import json
with open('workflow.json') as f: dsl = json.load(f)
print(f'节点: {len(dsl[\"graph\"][\"nodes\"])}, 连线: {len(dsl[\"graph\"][\"edges\"])}')
from collections import defaultdict
indeg = defaultdict(int)
for e in dsl['graph']['edges']: indeg[e['target']] += 1
start = [n['id'] for n in dsl['graph']['nodes'] if indeg[n['id']] == 0]
print(f'起始节点: {len(start)}')
"

# 2. 验证 Core 层独立于 Flask
cd api && python -c "from core.workflow.workflow_entry import WorkflowEntry; print('OK')"
```

## 4. 项目总结

| 组件 | 职责 | 关键源码 | 数据结构 |
|------|------|---------|---------|
| GraphEngine.run() | 拓扑调度 + 并行执行 | `workflow_entry.py` | BFS 队列 + 入度 HashMap |
| NodeFactory.create() | 类型 → 实例 | `node_factory.py` | NODE_TYPE_MAP 字典 |
| VariablePool.resolve() | {{#x.y#}} → 值 | `variable_pool_initializer.py` | 嵌套字典逐级取值 |
| ThreadPoolExecutor | 并行节点执行 | `concurrent.futures` | 线程池（max_workers=10） |

**思考题**：
1. 如何让某些节点失败后整个 Workflow 继续（而不只是跳过该节点）？（见代码中注释的可选修改）
2. 1000 个节点的 Workflow——GraphEngine 把所有节点一次性加载到内存，占用 ~500MB。如何改为惰性加载（按需创建节点）？

> **参考答案**：见附录 D
