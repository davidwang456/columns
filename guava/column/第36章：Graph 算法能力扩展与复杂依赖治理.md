# 第 36 章：Graph 算法能力扩展与复杂依赖治理

## 1 项目背景

在微服务发布系统中，架构师小刘需要管理服务间的复杂依赖。200+ 微服务，存在循环依赖风险，需要自动检测依赖环路，计算发布顺序。

## 2 项目设计

**大师**："Guava Graph 提供基础算法，可扩展实现复杂分析：

```java
// 拓扑排序获取发布顺序
Iterable<String> order = Graphs.topologicalSort(dependencyGraph);

// 环路检测
boolean hasCycle = Graphs.hasCycle(dependencyGraph);

// 可达性分析
Set<String> dependencies = Graphs.reachableNodes(graph, serviceName);

// 自定义算法：关键路径分析
public List<String> findCriticalPath(Graph<String> graph, String start, String end) {
    // BFS/DFS 变种实现
}
```

**技术映射**：图算法就像是'地图导航'——找出从 A 到 B 的所有路径，找出最短路径，避开环路。"

## 3 项目实战

```java
public class DeploymentDependencyAnalyzer {
    private MutableGraph<String> dependencyGraph = GraphBuilder.directed()
        .allowsSelfLoops(false)
        .build();
    
    // 添加依赖关系
    public void addDependency(String service, String dependsOn) {
        dependencyGraph.putEdge(service, dependsOn);
    }
    
    // 检测发布阻塞
    public List<String> findBlockingServices() {
        // 入度为 0 的节点可以并行发布
        return dependencyGraph.nodes().stream()
            .filter(node -> dependencyGraph.inDegree(node) == 0)
            .collect(toImmutableList());
    }
    
    // 循环依赖检测与报告
    public Optional<List<String>> detectCycle() {
        if (!Graphs.hasCycle(dependencyGraph)) {
            return Optional.empty();
        }
        
        // 使用 DFS 找出具体环路
        return findCyclePath();
    }
    
    private Optional<List<String>> findCyclePath() {
        Set<String> visited = new HashSet<>();
        Set<String> recStack = new HashSet<>();
        
        for (String node : dependencyGraph.nodes()) {
            if (findCycleDFS(node, visited, recStack, new ArrayList<>())) {
                return Optional.of(new ArrayList<>(recStack));
            }
        }
        return Optional.empty();
    }
    
    // 计算发布批次（分层）
    public List<List<String>> calculateDeploymentBatches() {
        List<List<String>> batches = new ArrayList<>();
        MutableGraph<String> remaining = Graphs.copyOf(dependencyGraph);
        
        while (!remaining.nodes().isEmpty()) {
            // 找出入度为 0 的节点（无未发布依赖）
            List<String> batch = remaining.nodes().stream()
                .filter(n -> remaining.inDegree(n) == 0)
                .collect(toImmutableList());
            
            if (batch.isEmpty()) {
                throw new IllegalStateException("Circular dependency detected");
            }
            
            batches.add(batch);
            batch.forEach(remaining::removeNode);
        }
        
        return batches;
    }
}

// Network 用于带版本依赖
public class VersionedDependencyAnalyzer {
    private MutableNetwork<String, String> network = NetworkBuilder.directed()
        .build();
    
    public void addDependency(String from, String to, String versionConstraint) {
        network.addEdge(from, to, versionConstraint);
    }
    
    public Set<String> getVersionConstraints(String from, String to) {
        return network.edgesConnecting(from, to);
    }
}
```

## 4 项目总结

### 扩展算法列表

| 算法 | 实现方式 |
|------|----------|
| 拓扑排序 | `Graphs.topologicalSort` |
| 环路检测 | `Graphs.hasCycle` + 自定义 DFS |
| 最短路径 | Dijkstra（需自定义） |
| 关键路径 | 带权拓扑排序 |
| 连通分量 | BFS + 染色 |
