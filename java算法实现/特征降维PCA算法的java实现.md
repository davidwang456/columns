# 背景

主成分分析（Principal Component Analysis，PCA）是最经典的特征降维方法，由Karl Pearson在1901年提出。PCA通过线性变换将高维数据投影到低维空间，同时最大限度地保留数据的方差信息（即信息量）。

PCA的核心思想：
1. 找到数据方差最大的方向，作为第一主成分
2. 在与第一主成分正交的方向中，找方差最大的方向，作为第二主成分
3. 依此类推，直到获得所需数量的主成分

PCA的数学步骤：
1. **数据中心化**：将每个特征减去其均值
2. **计算协方差矩阵**：C = (1/n) X^T X
3. **特征值分解**：对协方差矩阵求特征值和特征向量
4. **选择主成分**：按特征值从大到小排序，选取前k个特征向量
5. **投影降维**：Y = X * W，其中W为选取的特征向量组成的矩阵

PCA广泛应用于：
- 数据可视化（高维数据降至2D/3D）
- 噪声过滤
- 特征压缩
- 人脸识别（特征脸方法）

# 特征降维PCA算法的java实现

下面纯Java实现PCA算法，包括协方差矩阵计算、特征值分解（Jacobi迭代法）和降维投影：

```
import java.util.Arrays;

public class PCA {

    private double[] mean;
    private double[][] eigenvectors;
    private double[] eigenvalues;
    private int numComponents;

    public PCA(int numComponents) {
        this.numComponents = numComponents;
    }

    // 数据中心化
    private double[][] centerData(double[][] data) {
        int n = data.length, d = data[0].length;
        mean = new double[d];
        for (double[] row : data)
            for (int j = 0; j < d; j++) mean[j] += row[j];
        for (int j = 0; j < d; j++) mean[j] /= n;

        double[][] centered = new double[n][d];
        for (int i = 0; i < n; i++)
            for (int j = 0; j < d; j++)
                centered[i][j] = data[i][j] - mean[j];
        return centered;
    }

    // 计算协方差矩阵
    private double[][] covarianceMatrix(double[][] centered) {
        int n = centered.length, d = centered[0].length;
        double[][] cov = new double[d][d];
        for (int i = 0; i < d; i++)
            for (int j = 0; j < d; j++) {
                double sum = 0;
                for (int k = 0; k < n; k++)
                    sum += centered[k][i] * centered[k][j];
                cov[i][j] = sum / (n - 1);
            }
        return cov;
    }

    /**
     * Jacobi迭代法求对称矩阵的特征值和特征向量
     * 通过一系列旋转变换使矩阵对角化
     */
    private void jacobiEigen(double[][] matrix) {
        int n = matrix.length;
        double[][] a = new double[n][n];
        for (int i = 0; i < n; i++) a[i] = matrix[i].clone();

        double[][] v = new double[n][n];
        for (int i = 0; i < n; i++) v[i][i] = 1.0;

        int maxIter = 100 * n * n;
        for (int iter = 0; iter < maxIter; iter++) {
            // 找最大非对角元素
            int p = 0, q = 1;
            double maxVal = Math.abs(a[0][1]);
            for (int i = 0; i < n; i++)
                for (int j = i + 1; j < n; j++)
                    if (Math.abs(a[i][j]) > maxVal) {
                        maxVal = Math.abs(a[i][j]);
                        p = i; q = j;
                    }

            if (maxVal < 1e-12) break;

            // 计算旋转角度
            double theta;
            if (Math.abs(a[p][p] - a[q][q]) < 1e-15)
                theta = Math.PI / 4;
            else
                theta = 0.5 * Math.atan2(2 * a[p][q], a[p][p] - a[q][q]);

            double cosT = Math.cos(theta), sinT = Math.sin(theta);

            // 执行旋转
            double[][] newA = new double[n][n];
            for (int i = 0; i < n; i++) newA[i] = a[i].clone();

            for (int i = 0; i < n; i++) {
                if (i != p && i != q) {
                    newA[i][p] = newA[p][i] = cosT * a[i][p] + sinT * a[i][q];
                    newA[i][q] = newA[q][i] = -sinT * a[i][p] + cosT * a[i][q];
                }
            }
            newA[p][p] = cosT * cosT * a[p][p] + 2 * sinT * cosT * a[p][q] + sinT * sinT * a[q][q];
            newA[q][q] = sinT * sinT * a[p][p] - 2 * sinT * cosT * a[p][q] + cosT * cosT * a[q][q];
            newA[p][q] = newA[q][p] = 0;
            a = newA;

            // 更新特征向量
            for (int i = 0; i < n; i++) {
                double vip = v[i][p], viq = v[i][q];
                v[i][p] = cosT * vip + sinT * viq;
                v[i][q] = -sinT * vip + cosT * viq;
            }
        }

        eigenvalues = new double[n];
        for (int i = 0; i < n; i++) eigenvalues[i] = a[i][i];

        // 按特征值从大到小排序
        Integer[] indices = new Integer[n];
        for (int i = 0; i < n; i++) indices[i] = i;
        Arrays.sort(indices, (x, y) -> Double.compare(eigenvalues[y], eigenvalues[x]));

        double[] sortedEigenvalues = new double[n];
        eigenvectors = new double[n][n];
        for (int i = 0; i < n; i++) {
            sortedEigenvalues[i] = eigenvalues[indices[i]];
            for (int j = 0; j < n; j++)
                eigenvectors[j][i] = v[j][indices[i]];
        }
        eigenvalues = sortedEigenvalues;
    }

    // 拟合PCA模型
    public void fit(double[][] data) {
        double[][] centered = centerData(data);
        double[][] cov = covarianceMatrix(centered);
        jacobiEigen(cov);
    }

    // 降维变换
    public double[][] transform(double[][] data) {
        int n = data.length, d = data[0].length;
        double[][] result = new double[n][numComponents];
        for (int i = 0; i < n; i++)
            for (int j = 0; j < numComponents; j++)
                for (int k = 0; k < d; k++)
                    result[i][j] += (data[i][k] - mean[k]) * eigenvectors[k][j];
        return result;
    }

    // 计算各主成分的方差贡献率
    public double[] explainedVarianceRatio() {
        double total = 0;
        for (double ev : eigenvalues) total += ev;
        double[] ratio = new double[numComponents];
        for (int i = 0; i < numComponents; i++) ratio[i] = eigenvalues[i] / total;
        return ratio;
    }

    // 累积方差贡献率
    public double[] cumulativeVarianceRatio() {
        double[] ratio = explainedVarianceRatio();
        double[] cumulative = new double[numComponents];
        cumulative[0] = ratio[0];
        for (int i = 1; i < numComponents; i++) cumulative[i] = cumulative[i - 1] + ratio[i];
        return cumulative;
    }

    public static void main(String[] args) {
        // 4维数据（模拟鸢尾花数据）
        double[][] data = {
            {5.1, 3.5, 1.4, 0.2}, {4.9, 3.0, 1.4, 0.2}, {4.7, 3.2, 1.3, 0.2},
            {4.6, 3.1, 1.5, 0.2}, {5.0, 3.6, 1.4, 0.2}, {5.4, 3.9, 1.7, 0.4},
            {7.0, 3.2, 4.7, 1.4}, {6.4, 3.2, 4.5, 1.5}, {6.9, 3.1, 4.9, 1.5},
            {5.5, 2.3, 4.0, 1.3}, {6.5, 2.8, 4.6, 1.5}, {5.7, 2.8, 4.5, 1.3},
            {6.3, 3.3, 6.0, 2.5}, {5.8, 2.7, 5.1, 1.9}, {7.1, 3.0, 5.9, 2.1},
            {6.5, 3.0, 5.8, 2.2}, {7.6, 3.0, 6.6, 2.1}, {7.2, 3.6, 6.1, 2.5}
        };

        System.out.println("=== PCA特征降维 ===");
        System.out.println("原始数据维度: " + data[0].length + "维, " + data.length + "个样本\n");

        PCA pca = new PCA(2);
        pca.fit(data);

        System.out.println("特征值:");
        for (int i = 0; i < pca.eigenvalues.length; i++)
            System.out.println("  λ" + (i + 1) + " = " + String.format("%.4f", pca.eigenvalues[i]));

        System.out.println("\n方差贡献率:");
        double[] ratio = pca.explainedVarianceRatio();
        double[] cumRatio = pca.cumulativeVarianceRatio();
        for (int i = 0; i < ratio.length; i++)
            System.out.println("  PC" + (i + 1) + ": " + String.format("%.2f%%", ratio[i] * 100)
                + " (累积: " + String.format("%.2f%%", cumRatio[i] * 100) + ")");

        System.out.println("\n降维后数据 (4D -> 2D):");
        double[][] transformed = pca.transform(data);
        for (int i = 0; i < transformed.length; i++)
            System.out.println("  样本" + String.format("%2d", i) + ": ["
                + String.format("%7.4f", transformed[i][0]) + ", "
                + String.format("%7.4f", transformed[i][1]) + "]");
    }
}
```

# 总结

PCA主成分分析是最基本、最重要的特征降维方法，其核心优势：

1. **有效降维**：去除冗余特征，减少计算量，同时保留最重要的信息
2. **去除相关性**：降维后的各主成分相互正交，消除了特征间的线性相关
3. **噪声抑制**：丢弃小特征值对应的分量，等效于去除噪声
4. **无监督方法**：不需要类别标签信息

PCA的局限性：
- 只能处理线性关系，对非线性结构无法有效降维（可使用核PCA扩展）
- 主成分的物理含义不明确，可解释性较差
- 对数据的尺度敏感，通常需要先进行标准化

在模式识别中，PCA常作为预处理步骤，用于在分类前降低特征维度，既加速后续分类器的训练，又通过去除噪声提高分类精度。
