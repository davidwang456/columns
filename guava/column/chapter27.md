# 第 27 章：Graph/ValueGraph/Network 业务关系建模

## 1 项目背景

在权限系统的角色继承模块中，工程师小刘需要建模角色之间的继承关系。一个角色可以继承多个父角色的权限，同时可以有多个子角色。传统的邻接表实现复杂，查询关系路径时递归容易出错。

## 2 项目设计

**大师**："Guava Graph 库提供了三种图模型：

```java
// Graph: 边无权重、无方向（或双向）
Graph<String> roleGraph = GraphBuilder.undirected().build();
roleGraph.putEdge("ADMIN", "USER");

// ValueGraph: 边有值（如权重）
ValueGraph<String, Integer> weightedGraph = GraphBuilder.directed()
    .build();
weightedGraph.putEdgeValue("A", "B", 5);

// Network: 边是对象（可唯一标识）
Network<String, String> network = NetworkBuilder.directed()
    .build();
network.addEdge("A", "B", "edge1");
```

**技术映射**：Guava Graph 就像是'关系数据库的视图层'——你可以声明式地查询节点、边、路径，不用手写递归。"

## 3 项目实战

```java
public class RoleHierarchy {
    private MutableGraph<String> roleGraph = GraphBuilder.directed()
        .allowsSelfLoops(false)
        .build();
    
    // 添加继承关系
    public void addInheritance(String child, String parent) {
        roleGraph.putEdge(child, parent);  // child -> parent 表示继承
    }
    
    // 获取所有父角色
    public Set<String> getParents(String role) {
        return roleGraph.predecessors(role);
    }
    
    // 获取所有子角色
    public Set<String> getChildren(String role) {
        return roleGraph.successors(role);
    }
    
    // 检测循环继承
    public boolean hasCycle() {
        return Graphs.hasCycle(roleGraph);
    }
    
    // 获取拓扑排序（用于权限计算顺序）
    public Iterable<String> topologicalSort() {
        return Graphs.topologicalSort(roleGraph);
    }
    
    // 查找可达角色（包含间接继承）
    public Set<String> getAllParents(String role) {
        return Graphs.reachableNodes(roleGraph, role);
    }
}

// Network 示例：社交关系
public class SocialNetwork {
    private MutableNetwork<String, String> network = NetworkBuilder.undirected()
        .build();
    
    public void addFriendship(String p1, String p2, String relationId) {
        network.addEdge(p1, p2, relationId);
    }
    
    public int getDegree(String person) {
        return network.degree(person);
    }
}
```

## 4 项目总结

### 图类型选择

| 类型 | 边有值 | 边可唯一 | 适用场景 |
|------|--------|----------|----------|
| Graph | 否 | 否 | 简单关系 |
| ValueGraph | 是 | 否 | 带权重关系 |
| Network | 是 | 是 | 复杂网络 |

### 应用场景

1. 权限继承
2. 依赖分析
3. 社交网络
4. 路径规划
