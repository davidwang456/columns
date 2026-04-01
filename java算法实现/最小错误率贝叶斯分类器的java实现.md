# 背景

最小错误率贝叶斯分类器是贝叶斯决策理论中最基本的分类方法。其核心思想是：对于一个待分类样本，计算它属于每个类别的后验概率，然后将其归入后验概率最大的那个类别，从而使总的分类错误率最小。

根据贝叶斯公式：P(ω_i|x) = P(x|ω_i) * P(ω_i) / P(x)

其中：
- P(ω_i|x) 是后验概率，即观测到样本x后属于类别ω_i的概率
- P(x|ω_i) 是类条件概率密度（似然函数）
- P(ω_i) 是先验概率
- P(x) 是证据因子（对所有类别相同，分类时可忽略）

最小错误率判决规则：若 P(ω_i|x) = max P(ω_j|x)，则 x ∈ ω_i。等价于比较 P(x|ω_i) * P(ω_i) 的大小。

当类条件概率密度服从多元高斯分布时，判别函数可以简化为基于均值向量和协方差矩阵的二次/线性判别函数。

# 最小错误率贝叶斯分类器的java实现

实现步骤：

1. 从训练数据估计每个类别的先验概率、均值向量和协方差矩阵
2. 对待分类样本，计算其在各类别下的判别函数值
3. 将样本归入判别函数值最大的类别

假设类条件概率密度服从高斯分布：

```
import java.util.Arrays;

public class MinErrorBayesClassifier {

    private int numClasses;
    private int numFeatures;
    private double[] priors;           // 先验概率
    private double[][] means;          // 各类别均值向量
    private double[][][] covariances;  // 各类别协方差矩阵
    private double[][][] covInverses;  // 协方差逆矩阵
    private double[] covDets;          // 协方差矩阵行列式

    public MinErrorBayesClassifier(int numClasses, int numFeatures) {
        this.numClasses = numClasses;
        this.numFeatures = numFeatures;
        this.priors = new double[numClasses];
        this.means = new double[numClasses][numFeatures];
        this.covariances = new double[numClasses][numFeatures][numFeatures];
        this.covInverses = new double[numClasses][numFeatures][numFeatures];
        this.covDets = new double[numClasses];
    }

    // 训练：从数据中估计参数
    public void train(double[][][] classData) {
        int totalSamples = 0;
        for (double[][] data : classData) totalSamples += data.length;

        for (int c = 0; c < numClasses; c++) {
            double[][] data = classData[c];
            int n = data.length;

            // 先验概率
            priors[c] = (double) n / totalSamples;

            // 均值
            for (int j = 0; j < numFeatures; j++) {
                double sum = 0;
                for (int i = 0; i < n; i++) sum += data[i][j];
                means[c][j] = sum / n;
            }

            // 协方差矩阵
            for (int j = 0; j < numFeatures; j++) {
                for (int k = 0; k < numFeatures; k++) {
                    double sum = 0;
                    for (int i = 0; i < n; i++)
                        sum += (data[i][j] - means[c][j]) * (data[i][k] - means[c][k]);
                    covariances[c][j][k] = sum / (n - 1);
                }
            }

            covInverses[c] = invertMatrix(covariances[c]);
            covDets[c] = determinant(covariances[c]);
        }
    }

    // 判别函数 g_i(x) = ln P(ω_i) - 0.5 * ln|Σ_i| - 0.5 * (x-μ_i)^T Σ_i^{-1} (x-μ_i)
    private double discriminant(double[] x, int classIdx) {
        double[] diff = new double[numFeatures];
        for (int i = 0; i < numFeatures; i++) diff[i] = x[i] - means[classIdx][i];

        double[] temp = new double[numFeatures];
        for (int i = 0; i < numFeatures; i++)
            for (int j = 0; j < numFeatures; j++)
                temp[i] += covInverses[classIdx][i][j] * diff[j];

        double mahalanobis = 0;
        for (int i = 0; i < numFeatures; i++) mahalanobis += diff[i] * temp[i];

        return Math.log(priors[classIdx])
             - 0.5 * Math.log(Math.abs(covDets[classIdx]))
             - 0.5 * mahalanobis;
    }

    // 预测：选择判别函数值最大的类别
    public int predict(double[] x) {
        int bestClass = 0;
        double bestScore = discriminant(x, 0);
        for (int c = 1; c < numClasses; c++) {
            double score = discriminant(x, c);
            if (score > bestScore) {
                bestScore = score;
                bestClass = c;
            }
        }
        return bestClass;
    }

    // 获取各类别的后验概率
    public double[] posteriors(double[] x) {
        double[] logScores = new double[numClasses];
        for (int c = 0; c < numClasses; c++) logScores[c] = discriminant(x, c);

        double maxLog = logScores[0];
        for (double s : logScores) if (s > maxLog) maxLog = s;

        double[] probs = new double[numClasses];
        double sum = 0;
        for (int c = 0; c < numClasses; c++) {
            probs[c] = Math.exp(logScores[c] - maxLog);
            sum += probs[c];
        }
        for (int c = 0; c < numClasses; c++) probs[c] /= sum;
        return probs;
    }

    // 2x2矩阵行列式
    private double determinant(double[][] m) {
        int n = m.length;
        if (n == 1) return m[0][0];
        if (n == 2) return m[0][0] * m[1][1] - m[0][1] * m[1][0];
        // LU分解法求行列式
        double[][] lu = new double[n][n];
        for (int i = 0; i < n; i++) lu[i] = Arrays.copyOf(m[i], n);
        double det = 1.0;
        for (int i = 0; i < n; i++) {
            int maxRow = i;
            for (int k = i + 1; k < n; k++)
                if (Math.abs(lu[k][i]) > Math.abs(lu[maxRow][i])) maxRow = k;
            if (maxRow != i) {
                double[] tmp = lu[i]; lu[i] = lu[maxRow]; lu[maxRow] = tmp;
                det *= -1;
            }
            if (Math.abs(lu[i][i]) < 1e-12) return 0;
            det *= lu[i][i];
            for (int k = i + 1; k < n; k++) {
                lu[k][i] /= lu[i][i];
                for (int j = i + 1; j < n; j++)
                    lu[k][j] -= lu[k][i] * lu[i][j];
            }
        }
        return det;
    }

    // 高斯消元法求逆
    private double[][] invertMatrix(double[][] matrix) {
        int n = matrix.length;
        double[][] aug = new double[n][2 * n];
        for (int i = 0; i < n; i++) {
            System.arraycopy(matrix[i], 0, aug[i], 0, n);
            aug[i][i + n] = 1.0;
        }
        for (int i = 0; i < n; i++) {
            int maxRow = i;
            for (int k = i + 1; k < n; k++)
                if (Math.abs(aug[k][i]) > Math.abs(aug[maxRow][i])) maxRow = k;
            double[] tmp = aug[i]; aug[i] = aug[maxRow]; aug[maxRow] = tmp;
            double pivot = aug[i][i];
            for (int j = 0; j < 2 * n; j++) aug[i][j] /= pivot;
            for (int k = 0; k < n; k++) {
                if (k != i) {
                    double factor = aug[k][i];
                    for (int j = 0; j < 2 * n; j++) aug[k][j] -= factor * aug[i][j];
                }
            }
        }
        double[][] inv = new double[n][n];
        for (int i = 0; i < n; i++) System.arraycopy(aug[i], n, inv[i], 0, n);
        return inv;
    }

    public static void main(String[] args) {
        // 两类二维高斯分布的样本数据
        double[][] class0 = {
            {1.0, 2.0}, {1.5, 1.8}, {2.0, 2.5}, {1.2, 2.2},
            {1.8, 1.5}, {0.8, 2.1}, {1.3, 1.9}, {1.6, 2.3}
        };
        double[][] class1 = {
            {5.0, 4.0}, {4.5, 4.5}, {5.5, 3.8}, {4.8, 4.2},
            {5.2, 4.8}, {4.2, 3.5}, {5.1, 4.1}, {4.7, 3.9}
        };

        double[][][] trainData = {class0, class1};

        MinErrorBayesClassifier classifier = new MinErrorBayesClassifier(2, 2);
        classifier.train(trainData);

        System.out.println("=== 最小错误率贝叶斯分类器 ===");
        System.out.println("类别0 先验概率: " + classifier.priors[0]);
        System.out.println("类别1 先验概率: " + classifier.priors[1]);
        System.out.println("类别0 均值: " + Arrays.toString(classifier.means[0]));
        System.out.println("类别1 均值: " + Arrays.toString(classifier.means[1]));

        double[][] testSamples = {{2.0, 2.0}, {4.0, 3.5}, {3.0, 3.0}, {5.0, 5.0}};
        System.out.println("\n=== 分类结果 ===");
        for (double[] sample : testSamples) {
            int prediction = classifier.predict(sample);
            double[] posteriors = classifier.posteriors(sample);
            System.out.println("样本 " + Arrays.toString(sample)
                + " -> 类别" + prediction
                + "，后验概率: [" + String.format("%.4f", posteriors[0])
                + ", " + String.format("%.4f", posteriors[1]) + "]");
        }
    }
}
```

# 总结

最小错误率贝叶斯分类器是贝叶斯决策理论中最基本、最重要的分类方法。它的优点是：

1. **最优性**：在已知类条件概率密度和先验概率的条件下，该分类器使总分类错误率最小
2. **理论完备**：有严格的数学推导和统计理论基础
3. **概率输出**：不仅给出类别判定，还能输出后验概率，反映分类的置信度

其局限性在于：需要知道或准确估计类条件概率密度的形式和参数，在小样本或高维情况下参数估计可能不准确。当数据量足够时，最小错误率贝叶斯分类器是其他分类方法评价的理论上限（贝叶斯错误率）。
