# 背景

结构聚类算法是结构模式识别的重要方法，它利用数据点之间的结构关系（如连接、距离拓扑）来发现数据的聚类结构。与基于距离度量的统计聚类方法不同，结构聚类更关注数据的拓扑结构和连接模式。

最小生成树（Minimum Spanning Tree，MST）聚类是最经典的结构聚类方法之一。其基本思想是：

1. 将所有数据点视为图中的节点
2. 构建完全图，边的权重为节点间的距离
3. 使用Prim或Kruskal算法计算最小生成树
4. 删除MST中权重最大的若干条边，将树分割为若干子树，每个子树对应一个聚类

MST聚类的优势在于能发现任意形状的簇，不受簇的几何形状限制。此外，基于图的结构聚类还包括DBSCAN（基于密度的方法）、谱聚类等。

结构聚类广泛应用于：
- 社交网络社区发现
- 图像分割
- 地理空间数据分析
- 生物信息学中的蛋白质交互网络分析

# 结构聚类算法的java实现

下面实现基于最小生成树的结构聚类，使用Kruskal算法构建MST，然后通过删边策略进行聚类：

```
import java.util.*;

public class MSTClustering {

    // 图的边
    static class Edge implements Comparable<Edge> {
        int from, to;
        double weight;

        Edge(int from, int to, double weight) {
            this.from = from; this.to = to; this.weight = weight;
        }

        @Override
        public int compareTo(Edge other) {
            return Double.compare(this.weight, other.weight);
        }

        @Override
        public String toString() {
            return "(" + from + "-" + to + ", " + String.format("%.4f", weight) + ")";
        }
    }

    // 并查集（Union-Find）
    static class UnionFind {
        int[] parent, rank;

        UnionFind(int n) {
            parent = new int[n];
            rank = new int[n];
            for (int i = 0; i < n; i++) parent[i] = i;
        }

        int find(int x) {
            if (parent[x] != x) parent[x] = find(parent[x]);
            return parent[x];
        }

        boolean union(int x, int y) {
            int px = find(x), py = find(y);
            if (px == py) return false;
            if (rank[px] < rank[py]) { int t = px; px = py; py = t; }
            parent[py] = px;
            if (rank[px] == rank[py]) rank[px]++;
            return true;
        }
    }

    private double[][] data;
    private List<Edge> mstEdges;

    public MSTClustering(double[][] data) {
        this.data = data;
    }

    private double distance(double[] a, double[] b) {
        double sum = 0;
        for (int i = 0; i < a.length; i++) sum += Math.pow(a[i] - b[i], 2);
        return Math.sqrt(sum);
    }

    // Kruskal算法构建最小生成树
    public List<Edge> buildMST() {
        int n = data.length;
        List<Edge> allEdges = new ArrayList<>();

        for (int i = 0; i < n; i++)
            for (int j = i + 1; j < n; j++)
                allEdges.add(new Edge(i, j, distance(data[i], data[j])));

        Collections.sort(allEdges);

        UnionFind uf = new UnionFind(n);
        mstEdges = new ArrayList<>();

        for (Edge edge : allEdges) {
            if (uf.union(edge.from, edge.to)) {
                mstEdges.add(edge);
                if (mstEdges.size() == n - 1) break;
            }
        }
        return mstEdges;
    }

    // 方法1：删除k-1条最长边，得到k个簇
    public int[] clusterByRemovingLongestEdges(int k) {
        if (mstEdges == null) buildMST();

        List<Edge> sortedEdges = new ArrayList<>(mstEdges);
        sortedEdges.sort((a, b) -> Double.compare(b.weight, a.weight));

        // 删除最长的k-1条边
        Set<Edge> removedEdges = new HashSet<>();
        System.out.println("删除的边:");
        for (int i = 0; i < k - 1 && i < sortedEdges.size(); i++) {
            removedEdges.add(sortedEdges.get(i));
            System.out.println("  " + sortedEdges.get(i));
        }

        // 用剩余边重建连通分量
        int n = data.length;
        UnionFind uf = new UnionFind(n);
        for (Edge edge : mstEdges)
            if (!removedEdges.contains(edge))
                uf.union(edge.from, edge.to);

        // 分配聚类标签
        Map<Integer, Integer> rootToLabel = new HashMap<>();
        int[] labels = new int[n];
        int nextLabel = 0;
        for (int i = 0; i < n; i++) {
            int root = uf.find(i);
            if (!rootToLabel.containsKey(root))
                rootToLabel.put(root, nextLabel++);
            labels[i] = rootToLabel.get(root);
        }
        return labels;
    }

    // 方法2：基于边权重的不一致性度量（inconsistency）
    public int[] clusterByInconsistency(double threshold) {
        if (mstEdges == null) buildMST();

        // 计算每条边的不一致性：与相邻边权重的比值
        double meanWeight = 0;
        for (Edge e : mstEdges) meanWeight += e.weight;
        meanWeight /= mstEdges.size();

        double stdWeight = 0;
        for (Edge e : mstEdges) stdWeight += Math.pow(e.weight - meanWeight, 2);
        stdWeight = Math.sqrt(stdWeight / mstEdges.size());

        Set<Edge> removedEdges = new HashSet<>();
        System.out.println("不一致性删边 (阈值=" + threshold + "):");
        for (Edge edge : mstEdges) {
            double inconsistency = (edge.weight - meanWeight) / (stdWeight + 1e-10);
            if (inconsistency > threshold) {
                removedEdges.add(edge);
                System.out.println("  " + edge + " (不一致性=" + String.format("%.4f", inconsistency) + ")");
            }
        }

        int n = data.length;
        UnionFind uf = new UnionFind(n);
        for (Edge edge : mstEdges)
            if (!removedEdges.contains(edge))
                uf.union(edge.from, edge.to);

        Map<Integer, Integer> rootToLabel = new HashMap<>();
        int[] labels = new int[n];
        int nextLabel = 0;
        for (int i = 0; i < n; i++) {
            int root = uf.find(i);
            if (!rootToLabel.containsKey(root))
                rootToLabel.put(root, nextLabel++);
            labels[i] = rootToLabel.get(root);
        }
        return labels;
    }

    public static void main(String[] args) {
        // 包含3个明显簇的数据
        double[][] data = {
            {1.0, 1.0}, {1.5, 1.5}, {1.2, 1.3}, {0.8, 1.2}, {1.3, 0.9},
            {5.0, 5.0}, {5.5, 5.5}, {5.2, 5.3}, {4.8, 5.2}, {5.3, 4.8},
            {9.0, 1.0}, {9.5, 1.5}, {9.2, 1.3}, {8.8, 0.8}, {9.3, 1.1}
        };

        MSTClustering mstClust = new MSTClustering(data);

        System.out.println("=== 最小生成树构建 ===");
        List<Edge> mst = mstClust.buildMST();
        System.out.println("MST边:");
        for (Edge e : mst) System.out.println("  " + e);
        double totalWeight = mst.stream().mapToDouble(e -> e.weight).sum();
        System.out.println("MST总权重: " + String.format("%.4f", totalWeight));

        System.out.println("\n=== 方法1：删除最长边聚类 (K=3) ===");
        int[] labels1 = mstClust.clusterByRemovingLongestEdges(3);
        System.out.println("聚类结果:");
        for (int i = 0; i < data.length; i++)
            System.out.println("  样本" + i + " " + Arrays.toString(data[i]) + " -> 簇" + labels1[i]);

        System.out.println("\n=== 方法2：不一致性度量聚类 ===");
        int[] labels2 = mstClust.clusterByInconsistency(1.0);
        System.out.println("聚类结果:");
        for (int i = 0; i < data.length; i++)
            System.out.println("  样本" + i + " " + Arrays.toString(data[i]) + " -> 簇" + labels2[i]);
    }
}
```

# 总结

基于最小生成树的结构聚类算法是结构模式识别的经典方法，其主要特点：

1. **能发现任意形状的簇**：不像K-Means那样假设簇是球形的，MST聚类可以发现链状、环状等任意形状的聚类
2. **基于图论的清晰框架**：利用图的连通性来定义聚类，数学基础严谨
3. **层次化结构**：删除不同数量的边可以得到不同粒度的聚类结果
4. **无需指定初始中心**：避免了K-Means的初始化问题

其主要局限性：
- 时间复杂度较高：构建完全图需要 O(n²)，排序需要 O(n²logn)
- 对噪声敏感：噪声点可能导致异常的长边连接
- 删边策略需要人工参与：如何选择合适的阈值或删边数量

在实际应用中，MST聚类常与密度估计、噪声过滤等技术结合使用，以提高其在复杂数据上的鲁棒性。
