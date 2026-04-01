# 背景

最小风险贝叶斯分类器是最小错误率贝叶斯分类器的推广。最小错误率准则假设所有错误分类的代价相同，但在实际应用中，不同类型的错误往往具有不同的代价。例如在医学诊断中，将患者误诊为健康的代价远高于将健康人误诊为患者。

最小风险贝叶斯分类器引入了**损失函数**（Loss Function）的概念。设 λ(α_i|ω_j) 表示真实类别为 ω_j 时采取决策 α_i 的损失，则对样本x采取决策 α_i 的条件风险为：

R(α_i|x) = Σ_j λ(α_i|ω_j) * P(ω_j|x)

最小风险判决规则：选择使条件风险最小的决策，即 α* = argmin R(α_i|x)。

当损失函数取 0-1 损失（正确分类损失为0，错误分类损失为1）时，最小风险准则退化为最小错误率准则。

# 最小风险贝叶斯分类器的java实现

实现步骤：

1. 从训练数据估计各类别参数（先验概率、均值、协方差）
2. 定义损失矩阵 λ(α_i|ω_j)
3. 对待分类样本，计算各类别后验概率
4. 计算每个决策的条件风险
5. 选择条件风险最小的决策

```
import java.util.Arrays;

public class MinRiskBayesClassifier {

    private int numClasses;
    private int numFeatures;
    private double[] priors;
    private double[][] means;
    private double[][][] covariances;
    private double[][][] covInverses;
    private double[] covDets;
    private double[][] lossMatrix;  // 损失矩阵 lossMatrix[i][j] = λ(α_i|ω_j)

    public MinRiskBayesClassifier(int numClasses, int numFeatures, double[][] lossMatrix) {
        this.numClasses = numClasses;
        this.numFeatures = numFeatures;
        this.lossMatrix = lossMatrix;
        this.priors = new double[numClasses];
        this.means = new double[numClasses][numFeatures];
        this.covariances = new double[numClasses][numFeatures][numFeatures];
        this.covInverses = new double[numClasses][numFeatures][numFeatures];
        this.covDets = new double[numClasses];
    }

    public void train(double[][][] classData) {
        int totalSamples = 0;
        for (double[][] data : classData) totalSamples += data.length;

        for (int c = 0; c < numClasses; c++) {
            double[][] data = classData[c];
            int n = data.length;
            priors[c] = (double) n / totalSamples;

            for (int j = 0; j < numFeatures; j++) {
                double sum = 0;
                for (int i = 0; i < n; i++) sum += data[i][j];
                means[c][j] = sum / n;
            }

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

    // 计算高斯概率密度
    private double gaussianPdf(double[] x, int classIdx) {
        double[] diff = new double[numFeatures];
        for (int i = 0; i < numFeatures; i++) diff[i] = x[i] - means[classIdx][i];

        double[] temp = new double[numFeatures];
        for (int i = 0; i < numFeatures; i++)
            for (int j = 0; j < numFeatures; j++)
                temp[i] += covInverses[classIdx][i][j] * diff[j];

        double mahalanobis = 0;
        for (int i = 0; i < numFeatures; i++) mahalanobis += diff[i] * temp[i];

        double coeff = 1.0 / (Math.pow(2 * Math.PI, numFeatures / 2.0) * Math.sqrt(Math.abs(covDets[classIdx])));
        return coeff * Math.exp(-0.5 * mahalanobis);
    }

    // 计算后验概率
    public double[] posteriors(double[] x) {
        double[] joint = new double[numClasses];
        for (int c = 0; c < numClasses; c++)
            joint[c] = gaussianPdf(x, c) * priors[c];

        double evidence = 0;
        for (double j : joint) evidence += j;

        double[] post = new double[numClasses];
        for (int c = 0; c < numClasses; c++)
            post[c] = (evidence > 0) ? joint[c] / evidence : 1.0 / numClasses;
        return post;
    }

    // 计算各决策的条件风险
    public double[] conditionalRisks(double[] x) {
        double[] post = posteriors(x);
        double[] risks = new double[numClasses];
        for (int i = 0; i < numClasses; i++)
            for (int j = 0; j < numClasses; j++)
                risks[i] += lossMatrix[i][j] * post[j];
        return risks;
    }

    // 预测：选择条件风险最小的决策
    public int predict(double[] x) {
        double[] risks = conditionalRisks(x);
        int bestAction = 0;
        double minRisk = risks[0];
        for (int i = 1; i < numClasses; i++) {
            if (risks[i] < minRisk) {
                minRisk = risks[i];
                bestAction = i;
            }
        }
        return bestAction;
    }

    private double determinant(double[][] m) {
        int n = m.length;
        if (n == 1) return m[0][0];
        if (n == 2) return m[0][0] * m[1][1] - m[0][1] * m[1][0];
        double[][] lu = new double[n][n];
        for (int i = 0; i < n; i++) lu[i] = Arrays.copyOf(m[i], n);
        double det = 1.0;
        for (int i = 0; i < n; i++) {
            int maxRow = i;
            for (int k = i + 1; k < n; k++)
                if (Math.abs(lu[k][i]) > Math.abs(lu[maxRow][i])) maxRow = k;
            if (maxRow != i) { double[] t = lu[i]; lu[i] = lu[maxRow]; lu[maxRow] = t; det *= -1; }
            if (Math.abs(lu[i][i]) < 1e-12) return 0;
            det *= lu[i][i];
            for (int k = i + 1; k < n; k++) {
                lu[k][i] /= lu[i][i];
                for (int j = i + 1; j < n; j++) lu[k][j] -= lu[k][i] * lu[i][j];
            }
        }
        return det;
    }

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
            double[] t = aug[i]; aug[i] = aug[maxRow]; aug[maxRow] = t;
            double pivot = aug[i][i];
            for (int j = 0; j < 2 * n; j++) aug[i][j] /= pivot;
            for (int k = 0; k < n; k++) {
                if (k != i) {
                    double f = aug[k][i];
                    for (int j = 0; j < 2 * n; j++) aug[k][j] -= f * aug[i][j];
                }
            }
        }
        double[][] inv = new double[n][n];
        for (int i = 0; i < n; i++) System.arraycopy(aug[i], n, inv[i], 0, n);
        return inv;
    }

    public static void main(String[] args) {
        // 三类分类问题：损失矩阵体现不同错误的不同代价
        // lossMatrix[i][j] 表示真实类别j被判为类别i时的损失
        double[][] lossMatrix = {
            {0, 1, 2},    // 判为类0：正确0，类1误判损失1，类2误判损失2
            {3, 0, 1},    // 判为类1：类0误判损失3，正确0，类2误判损失1
            {1, 2, 0}     // 判为类2：类0误判损失1，类1误判损失2，正确0
        };

        double[][] class0 = {{1.0, 1.0}, {1.2, 0.8}, {0.8, 1.2}, {1.1, 1.1}, {0.9, 0.9}};
        double[][] class1 = {{4.0, 4.0}, {4.2, 3.8}, {3.8, 4.2}, {4.1, 4.1}, {3.9, 3.9}};
        double[][] class2 = {{1.0, 4.0}, {1.2, 3.8}, {0.8, 4.2}, {1.1, 4.1}, {0.9, 3.9}};

        double[][][] trainData = {class0, class1, class2};

        MinRiskBayesClassifier classifier = new MinRiskBayesClassifier(3, 2, lossMatrix);
        classifier.train(trainData);

        System.out.println("=== 最小风险贝叶斯分类器 ===");
        System.out.println("损失矩阵:");
        for (int i = 0; i < lossMatrix.length; i++)
            System.out.println("  决策" + i + ": " + Arrays.toString(lossMatrix[i]));

        double[][] testSamples = {{1.0, 1.0}, {4.0, 4.0}, {1.0, 4.0}, {2.5, 2.5}};
        System.out.println("\n=== 分类结果 ===");
        for (double[] sample : testSamples) {
            int prediction = classifier.predict(sample);
            double[] risks = classifier.conditionalRisks(sample);
            double[] post = classifier.posteriors(sample);
            System.out.println("样本 " + Arrays.toString(sample));
            System.out.println("  后验概率: [" + String.format("%.4f, %.4f, %.4f", post[0], post[1], post[2]) + "]");
            System.out.println("  条件风险: [" + String.format("%.4f, %.4f, %.4f", risks[0], risks[1], risks[2]) + "]");
            System.out.println("  决策: 类别" + prediction);
        }
    }
}
```

# 总结

最小风险贝叶斯分类器是最小错误率贝叶斯分类器的自然推广，其核心特点是：

1. **灵活的损失建模**：通过损失矩阵可以为不同类型的错误分类赋予不同的代价权重
2. **实际意义更强**：在医学诊断、金融风控等领域，不同错误的代价差异极大，最小风险准则更符合实际需求
3. **统一框架**：当损失矩阵为0-1损失时退化为最小错误率分类器，具有良好的理论一致性

在实际应用中，损失矩阵的设定通常需要领域专家参与，合理的损失矩阵设定是该分类器取得良好效果的关键。
