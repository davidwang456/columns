# 背景

动态聚类（又称K-Means聚类）是最经典、应用最广泛的聚类算法之一，由Stuart Lloyd在1957年提出。K-Means属于划分式聚类方法，目标是将n个数据点划分为K个簇，使得每个数据点属于离其最近的簇中心所在的簇，从而最小化簇内样本到簇中心距离的总和（即最小化簇内误差平方和）。

K-Means算法流程：
1. 随机选择K个初始聚类中心
2. **分配步骤**：将每个样本分配到距离最近的聚类中心所在的簇
3. **更新步骤**：重新计算每个簇的中心（取簇内所有样本的均值）
4. 重复步骤2-3，直到聚类中心不再变化或达到最大迭代次数

K-Means的"动态"体现在：聚类中心和样本的分配在迭代过程中不断动态调整。

K-Means广泛应用于：
- 图像分割与压缩
- 客户分群
- 文档聚类
- 异常检测
- 数据预处理（特征离散化）

# 动态聚类KMeans算法的java实现

下面实现K-Means算法，包含K-Means++初始化策略和肘部法则确定最优K值：

```
import java.util.*;

public class KMeans {

    private int k;
    private int maxIterations;
    private double[][] centroids;
    private int[] assignments;
    private Random random;

    public KMeans(int k, int maxIterations) {
        this.k = k;
        this.maxIterations = maxIterations;
        this.random = new Random(42);
    }

    // 欧氏距离
    private double distance(double[] a, double[] b) {
        double sum = 0;
        for (int i = 0; i < a.length; i++) sum += Math.pow(a[i] - b[i], 2);
        return Math.sqrt(sum);
    }

    // 随机初始化中心
    private void initCentroidsRandom(double[][] data) {
        int n = data.length, d = data[0].length;
        centroids = new double[k][d];
        Set<Integer> chosen = new HashSet<>();
        for (int i = 0; i < k; i++) {
            int idx;
            do { idx = random.nextInt(n); } while (chosen.contains(idx));
            chosen.add(idx);
            centroids[i] = data[idx].clone();
        }
    }

    // K-Means++初始化：选择相距较远的初始中心
    private void initCentroidsPlusPlus(double[][] data) {
        int n = data.length, d = data[0].length;
        centroids = new double[k][d];

        // 随机选第一个中心
        centroids[0] = data[random.nextInt(n)].clone();

        for (int c = 1; c < k; c++) {
            double[] minDists = new double[n];
            double totalDist = 0;
            for (int i = 0; i < n; i++) {
                double minD = Double.MAX_VALUE;
                for (int j = 0; j < c; j++)
                    minD = Math.min(minD, distance(data[i], centroids[j]));
                minDists[i] = minD * minD;
                totalDist += minDists[i];
            }
            // 按概率选择下一个中心
            double r = random.nextDouble() * totalDist;
            double cumSum = 0;
            for (int i = 0; i < n; i++) {
                cumSum += minDists[i];
                if (cumSum >= r) {
                    centroids[c] = data[i].clone();
                    break;
                }
            }
        }
    }

    // 执行K-Means聚类
    public int[] fit(double[][] data) {
        return fit(data, true);
    }

    public int[] fit(double[][] data, boolean usePlusPlus) {
        int n = data.length, d = data[0].length;

        if (usePlusPlus) initCentroidsPlusPlus(data);
        else initCentroidsRandom(data);

        assignments = new int[n];

        for (int iter = 0; iter < maxIterations; iter++) {
            // 分配步骤
            boolean changed = false;
            for (int i = 0; i < n; i++) {
                int nearest = 0;
                double minDist = distance(data[i], centroids[0]);
                for (int c = 1; c < k; c++) {
                    double dist = distance(data[i], centroids[c]);
                    if (dist < minDist) { minDist = dist; nearest = c; }
                }
                if (assignments[i] != nearest) { assignments[i] = nearest; changed = true; }
            }

            if (!changed) {
                System.out.println("在第 " + (iter + 1) + " 轮收敛");
                break;
            }

            // 更新步骤
            double[][] newCentroids = new double[k][d];
            int[] counts = new int[k];
            for (int i = 0; i < n; i++) {
                int c = assignments[i];
                counts[c]++;
                for (int j = 0; j < d; j++) newCentroids[c][j] += data[i][j];
            }
            for (int c = 0; c < k; c++) {
                if (counts[c] > 0)
                    for (int j = 0; j < d; j++) newCentroids[c][j] /= counts[c];
                else
                    newCentroids[c] = data[random.nextInt(n)].clone();
            }
            centroids = newCentroids;
        }
        return assignments;
    }

    // 计算簇内误差平方和（SSE/Inertia）
    public double computeSSE(double[][] data) {
        double sse = 0;
        for (int i = 0; i < data.length; i++)
            sse += Math.pow(distance(data[i], centroids[assignments[i]]), 2);
        return sse;
    }

    public double[][] getCentroids() { return centroids; }

    // 肘部法则：计算不同K值下的SSE
    public static void elbowMethod(double[][] data, int maxK) {
        System.out.println("=== 肘部法则 ===");
        for (int k = 1; k <= maxK; k++) {
            KMeans km = new KMeans(k, 100);
            km.fit(data);
            double sse = km.computeSSE(data);
            System.out.println("K=" + k + ", SSE=" + String.format("%.4f", sse));
        }
    }

    public static void main(String[] args) {
        double[][] data = {
            {1.0, 1.0}, {1.5, 2.0}, {1.2, 1.5}, {0.8, 1.8}, {1.3, 1.2},
            {5.0, 5.0}, {5.5, 4.5}, {4.8, 5.2}, {5.2, 4.8}, {5.0, 5.5},
            {9.0, 1.0}, {9.5, 1.5}, {8.8, 0.8}, {9.2, 1.2}, {9.0, 1.8},
            {5.0, 9.0}, {5.5, 8.5}, {4.8, 9.2}, {5.2, 8.8}, {5.0, 9.5}
        };

        System.out.println("=== K-Means聚类 (K=4) ===");
        KMeans km = new KMeans(4, 100);
        int[] labels = km.fit(data);

        System.out.println("\n聚类结果:");
        for (int c = 0; c < 4; c++) {
            System.out.print("簇" + c + ": ");
            for (int i = 0; i < data.length; i++)
                if (labels[i] == c)
                    System.out.print(Arrays.toString(data[i]) + " ");
            System.out.println();
        }

        System.out.println("\n聚类中心:");
        double[][] centroids = km.getCentroids();
        for (int c = 0; c < centroids.length; c++)
            System.out.println("簇" + c + " 中心: " + Arrays.toString(centroids[c]));

        System.out.println("\nSSE = " + String.format("%.4f", km.computeSSE(data)));

        System.out.println();
        elbowMethod(data, 8);
    }
}
```

# 总结

K-Means动态聚类算法的特点：

1. **简单高效**：原理直观，实现简单，时间复杂度 O(n*k*d*t)，适合大规模数据
2. **K-Means++改进**：通过优化初始中心选择，大幅提升收敛速度和聚类质量
3. **可通过肘部法则选K**：绘制SSE-K曲线，在"肘部"拐点处确定最佳聚类数

其局限性：
- 需要预先指定K值
- 假设簇为球形且大小相近，对非凸形状的簇效果差
- 对初始化敏感（K-Means++可缓解）
- 对异常值敏感

K-Means是许多高级聚类方法的基础，如Mini-Batch K-Means（大数据优化）、K-Medoids（对异常值鲁棒）、谱聚类（处理非凸簇）等。
