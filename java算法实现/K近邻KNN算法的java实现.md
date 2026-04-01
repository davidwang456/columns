# 背景

K近邻算法（K-Nearest Neighbors，KNN）是一种基于实例的分类方法，由Cover和Hart在1967年提出。它是模式识别中最简单、最直观的分类算法之一，属于"懒学习"（Lazy Learning）方法——不需要显式的训练过程，分类时直接使用全部训练样本。

KNN的核心思想：给定一个待分类样本，在训练集中找到与其距离最近的K个样本（K个"近邻"），根据这K个近邻的类别通过多数表决来决定待分类样本的类别。

KNN的三个关键要素：
1. **K值选择**：K值过小容易受噪声影响（过拟合），K值过大会使分类边界模糊（欠拟合）
2. **距离度量**：欧氏距离、曼哈顿距离、闵可夫斯基距离等
3. **决策规则**：多数表决法、加权表决法（距离越近权重越大）

KNN广泛应用于推荐系统、图像识别、文本分类、医学诊断等领域。

# K近邻算法的java实现

实现步骤：
1. 存储训练数据集
2. 对待分类样本，计算其与所有训练样本的距离
3. 选择距离最小的K个样本
4. 根据K个近邻的类别进行投票表决

```
import java.util.*;

public class KNN {

    private double[][] trainData;
    private int[] trainLabels;
    private int k;

    public KNN(int k) {
        this.k = k;
    }

    public void fit(double[][] data, int[] labels) {
        this.trainData = data;
        this.trainLabels = labels;
    }

    // 欧氏距离
    private double euclideanDistance(double[] a, double[] b) {
        double sum = 0;
        for (int i = 0; i < a.length; i++)
            sum += Math.pow(a[i] - b[i], 2);
        return Math.sqrt(sum);
    }

    // 基本KNN：多数表决
    public int predict(double[] x) {
        int n = trainData.length;
        double[] distances = new double[n];
        Integer[] indices = new Integer[n];

        for (int i = 0; i < n; i++) {
            distances[i] = euclideanDistance(x, trainData[i]);
            indices[i] = i;
        }

        // 按距离排序
        Arrays.sort(indices, (a, b) -> Double.compare(distances[a], distances[b]));

        // K近邻多数表决
        Map<Integer, Integer> voteCount = new HashMap<>();
        for (int i = 0; i < k; i++) {
            int label = trainLabels[indices[i]];
            voteCount.put(label, voteCount.getOrDefault(label, 0) + 1);
        }

        int bestLabel = -1, maxVotes = 0;
        for (Map.Entry<Integer, Integer> entry : voteCount.entrySet()) {
            if (entry.getValue() > maxVotes) {
                maxVotes = entry.getValue();
                bestLabel = entry.getKey();
            }
        }
        return bestLabel;
    }

    // 加权KNN：距离越近权重越大
    public int predictWeighted(double[] x) {
        int n = trainData.length;
        double[] distances = new double[n];
        Integer[] indices = new Integer[n];

        for (int i = 0; i < n; i++) {
            distances[i] = euclideanDistance(x, trainData[i]);
            indices[i] = i;
        }

        Arrays.sort(indices, (a, b) -> Double.compare(distances[a], distances[b]));

        // 加权表决：权重 = 1 / (distance + epsilon)
        Map<Integer, Double> weightedVotes = new HashMap<>();
        double epsilon = 1e-8;
        for (int i = 0; i < k; i++) {
            int label = trainLabels[indices[i]];
            double weight = 1.0 / (distances[indices[i]] + epsilon);
            weightedVotes.put(label, weightedVotes.getOrDefault(label, 0.0) + weight);
        }

        int bestLabel = -1;
        double maxWeight = 0;
        for (Map.Entry<Integer, Double> entry : weightedVotes.entrySet()) {
            if (entry.getValue() > maxWeight) {
                maxWeight = entry.getValue();
                bestLabel = entry.getKey();
            }
        }
        return bestLabel;
    }

    // 计算测试集准确率
    public double accuracy(double[][] testData, int[] testLabels) {
        int correct = 0;
        for (int i = 0; i < testData.length; i++)
            if (predict(testData[i]) == testLabels[i]) correct++;
        return (double) correct / testData.length;
    }

    public static void main(String[] args) {
        // 三类二维数据
        double[][] trainData = {
            {1.0, 1.1}, {1.0, 1.0}, {0.9, 0.8}, {1.2, 0.9},
            {5.0, 5.1}, {5.1, 5.0}, {4.9, 4.8}, {5.2, 5.2},
            {1.0, 5.0}, {0.9, 5.1}, {1.1, 4.9}, {1.2, 5.2}
        };
        int[] trainLabels = {0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2};

        System.out.println("=== K近邻算法(KNN) ===\n");

        int[] kValues = {1, 3, 5};
        double[][] testSamples = {{1.1, 1.0}, {5.0, 5.0}, {1.0, 5.1}, {3.0, 3.0}, {2.0, 4.0}};

        for (int k : kValues) {
            KNN knn = new KNN(k);
            knn.fit(trainData, trainLabels);
            System.out.println("K = " + k + ":");
            for (double[] sample : testSamples) {
                int pred = knn.predict(sample);
                int predW = knn.predictWeighted(sample);
                System.out.println("  样本 " + Arrays.toString(sample)
                    + " -> 多数表决: 类别" + pred
                    + ", 加权表决: 类别" + predW);
            }
            System.out.println();
        }

        // 交叉验证：留一法
        System.out.println("=== 留一法交叉验证 ===");
        for (int k : kValues) {
            int correct = 0;
            for (int i = 0; i < trainData.length; i++) {
                double[][] loData = new double[trainData.length - 1][];
                int[] loLabels = new int[trainData.length - 1];
                int idx = 0;
                for (int j = 0; j < trainData.length; j++) {
                    if (j != i) {
                        loData[idx] = trainData[j];
                        loLabels[idx] = trainLabels[j];
                        idx++;
                    }
                }
                KNN knn = new KNN(k);
                knn.fit(loData, loLabels);
                if (knn.predict(trainData[i]) == trainLabels[i]) correct++;
            }
            double acc = (double) correct / trainData.length;
            System.out.println("K = " + k + " 的留一法准确率: " + String.format("%.2f%%", acc * 100));
        }
    }
}
```

# 总结

KNN算法的特点：

1. **简单直观**：无需训练过程，新数据可以即时加入
2. **天然支持多分类**：无需额外修改即可处理任意数量的类别
3. **非参数方法**：不对数据分布做任何假设

其主要局限性：
- **计算量大**：每次预测都需要计算与所有训练样本的距离，时间复杂度 O(n*d)
- **存储开销大**：需要保存全部训练数据
- **对特征尺度敏感**：不同特征量纲差异大时需先进行归一化
- **K值选择影响大**：通常通过交叉验证选择最优K值

实际应用中，可以使用KD-Tree、Ball-Tree等空间索引结构来加速近邻搜索，提高KNN在大数据集上的效率。
