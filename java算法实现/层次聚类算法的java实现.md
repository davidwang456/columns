# 背景

层次聚类（Hierarchical Clustering）是一种经典的无监督聚类方法，它通过建立一个聚类的层次结构（树状图/谱系图）来组织数据。与K-Means等划分式聚类不同，层次聚类不需要预先指定聚类个数，用户可以通过在不同层次切割树状图来获得不同粒度的聚类结果。

层次聚类有两种策略：

1. **凝聚式（自底向上，Agglomerative）**：初始时每个样本为一个单独的簇，然后逐步合并最相似的两个簇，直到所有样本归为一个簇
2. **分裂式（自顶向下，Divisive）**：初始时所有样本在一个簇中，然后逐步将簇分裂，直到每个样本单独成簇

簇间距离的计算方式（链接准则）：
- **单链接（Single Linkage）**：两簇间最近两点的距离
- **全链接（Complete Linkage）**：两簇间最远两点的距离
- **平均链接（Average Linkage）**：两簇间所有点对距离的平均值
- **Ward方法**：合并后簇内方差增量最小

层次聚类广泛应用于生物信息学（基因聚类）、社交网络分析、文档分类等领域。

# 层次聚类算法的java实现

下面实现凝聚式层次聚类，支持多种链接准则：

```
import java.util.*;

public class HierarchicalClustering {

    // 聚类合并记录
    static class MergeRecord {
        int cluster1, cluster2;
        double distance;
        int newCluster;

        MergeRecord(int c1, int c2, double dist, int nc) {
            this.cluster1 = c1; this.cluster2 = c2;
            this.distance = dist; this.newCluster = nc;
        }

        @Override
        public String toString() {
            return "合并 簇" + cluster1 + " 和 簇" + cluster2
                 + " (距离=" + String.format("%.4f", distance) + ") -> 簇" + newCluster;
        }
    }

    private double[][] data;
    private String linkage;
    private List<MergeRecord> mergeHistory = new ArrayList<>();

    public HierarchicalClustering(double[][] data, String linkage) {
        this.data = data;
        this.linkage = linkage;
    }

    // 欧氏距离
    private double distance(double[] a, double[] b) {
        double sum = 0;
        for (int i = 0; i < a.length; i++) sum += Math.pow(a[i] - b[i], 2);
        return Math.sqrt(sum);
    }

    // 计算两个簇之间的距离
    private double clusterDistance(List<Integer> cluster1, List<Integer> cluster2) {
        switch (linkage) {
            case "single": {
                double minDist = Double.MAX_VALUE;
                for (int i : cluster1)
                    for (int j : cluster2)
                        minDist = Math.min(minDist, distance(data[i], data[j]));
                return minDist;
            }
            case "complete": {
                double maxDist = 0;
                for (int i : cluster1)
                    for (int j : cluster2)
                        maxDist = Math.max(maxDist, distance(data[i], data[j]));
                return maxDist;
            }
            case "average": {
                double totalDist = 0;
                int count = 0;
                for (int i : cluster1)
                    for (int j : cluster2) {
                        totalDist += distance(data[i], data[j]);
                        count++;
                    }
                return totalDist / count;
            }
            default:
                throw new IllegalArgumentException("未知链接准则: " + linkage);
        }
    }

    // 执行凝聚式层次聚类
    public int[] fit(int numClusters) {
        int n = data.length;
        Map<Integer, List<Integer>> clusters = new LinkedHashMap<>();
        for (int i = 0; i < n; i++) {
            List<Integer> list = new ArrayList<>();
            list.add(i);
            clusters.put(i, list);
        }

        int nextClusterId = n;
        mergeHistory.clear();

        while (clusters.size() > numClusters) {
            // 找距离最小的两个簇
            double minDist = Double.MAX_VALUE;
            int mergeId1 = -1, mergeId2 = -1;

            List<Integer> clusterIds = new ArrayList<>(clusters.keySet());
            for (int i = 0; i < clusterIds.size(); i++) {
                for (int j = i + 1; j < clusterIds.size(); j++) {
                    double dist = clusterDistance(
                        clusters.get(clusterIds.get(i)),
                        clusters.get(clusterIds.get(j))
                    );
                    if (dist < minDist) {
                        minDist = dist;
                        mergeId1 = clusterIds.get(i);
                        mergeId2 = clusterIds.get(j);
                    }
                }
            }

            // 合并
            List<Integer> merged = new ArrayList<>(clusters.get(mergeId1));
            merged.addAll(clusters.get(mergeId2));
            mergeHistory.add(new MergeRecord(mergeId1, mergeId2, minDist, nextClusterId));

            clusters.remove(mergeId1);
            clusters.remove(mergeId2);
            clusters.put(nextClusterId, merged);
            nextClusterId++;
        }

        // 生成标签
        int[] labels = new int[n];
        int labelIdx = 0;
        for (Map.Entry<Integer, List<Integer>> entry : clusters.entrySet()) {
            for (int idx : entry.getValue()) labels[idx] = labelIdx;
            labelIdx++;
        }
        return labels;
    }

    public List<MergeRecord> getMergeHistory() { return mergeHistory; }

    public static void main(String[] args) {
        double[][] data = {
            {1.0, 1.0}, {1.5, 1.5}, {1.2, 1.3},
            {5.0, 5.0}, {5.5, 5.5}, {5.2, 5.3},
            {9.0, 1.0}, {9.5, 1.5}, {9.2, 1.3}
        };

        String[] linkages = {"single", "complete", "average"};

        for (String linkage : linkages) {
            System.out.println("=== 层次聚类 (" + linkage + " linkage) ===");
            HierarchicalClustering hc = new HierarchicalClustering(data, linkage);
            int[] labels = hc.fit(3);

            System.out.println("合并过程:");
            for (MergeRecord record : hc.getMergeHistory())
                System.out.println("  " + record);

            System.out.println("聚类结果 (3个簇):");
            for (int i = 0; i < data.length; i++)
                System.out.println("  样本" + i + " " + Arrays.toString(data[i]) + " -> 簇" + labels[i]);
            System.out.println();
        }

        // 不同簇数的聚类
        System.out.println("=== 不同聚类数目 (average linkage) ===");
        for (int k = 2; k <= 5; k++) {
            HierarchicalClustering hc = new HierarchicalClustering(data, "average");
            int[] labels = hc.fit(k);
            System.out.print("K=" + k + ": ");
            System.out.println(Arrays.toString(labels));
        }
    }
}
```

# 总结

层次聚类算法的主要优点：

1. **无需预先指定聚类数**：可以根据树状图在任意层次获取聚类结果
2. **结果可解释性强**：树状图直观展示了数据的层次结构关系
3. **可发现任意形状的簇**：尤其是单链接法

其主要局限性：
- **时间复杂度高**：基本实现为 O(n³)，不适合大规模数据集
- **不可逆性**：一旦合并或分裂，无法撤回
- **对噪声敏感**：特别是单链接法容易产生"链式效应"

实际应用中，可以通过先使用K-Means进行粗聚类，再对粗聚类结果进行层次聚类的方式来处理大规模数据集。
