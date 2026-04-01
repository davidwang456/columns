# 背景

LMSE（Least Mean Square Error，最小均方误差）算法是一种经典的线性分类器训练方法。与感知器算法不同，LMSE算法不仅适用于线性可分的情况，还可以在线性不可分的情况下给出一个"最优"的线性分类面，即使得分类误差的均方值最小的超平面。

LMSE算法的核心思想是：将分类问题转化为求解线性方程组的最小二乘解问题。给定训练样本矩阵X和目标向量b，我们需要找到权向量w，使得 ||Xw - b||² 最小。这可以通过伪逆矩阵法直接求解，也可以通过梯度下降法迭代逼近。

LMSE算法的主要应用场景：

1. 模式分类：在特征空间中寻找最优线性判别函数
2. 回归分析：最小化预测误差的平方和
3. 信号处理：自适应滤波器的权重调整
4. 图像识别：线性特征匹配

# LMSE算法的java实现

LMSE算法有两种常见的实现方式：

1. **伪逆法（直接法）**：通过计算 w = (X^T X)^{-1} X^T b 一步求解
2. **梯度下降法（迭代法）**：通过 w(k+1) = w(k) + ρ * X^T(b - Xw(k)) 逐步迭代

下面同时实现两种方法：

```
import java.util.Arrays;
import java.util.Random;

public class LMSE {

    // 矩阵转置
    private static double[][] transpose(double[][] matrix) {
        int rows = matrix.length, cols = matrix[0].length;
        double[][] result = new double[cols][rows];
        for (int i = 0; i < rows; i++)
            for (int j = 0; j < cols; j++)
                result[j][i] = matrix[i][j];
        return result;
    }

    // 矩阵乘法
    private static double[][] multiply(double[][] a, double[][] b) {
        int rows = a.length, cols = b[0].length, inner = a[0].length;
        double[][] result = new double[rows][cols];
        for (int i = 0; i < rows; i++)
            for (int j = 0; j < cols; j++)
                for (int k = 0; k < inner; k++)
                    result[i][j] += a[i][k] * b[k][j];
        return result;
    }

    // 矩阵乘以向量
    private static double[] multiplyVec(double[][] matrix, double[] vec) {
        int rows = matrix.length, cols = matrix[0].length;
        double[] result = new double[rows];
        for (int i = 0; i < rows; i++)
            for (int j = 0; j < cols; j++)
                result[i] += matrix[i][j] * vec[j];
        return result;
    }

    // 2x2矩阵求逆（用于演示，实际可扩展为高斯消元法）
    private static double[][] inverse2x2(double[][] m) {
        double det = m[0][0] * m[1][1] - m[0][1] * m[1][0];
        if (Math.abs(det) < 1e-10) throw new ArithmeticException("矩阵不可逆");
        return new double[][] {
            { m[1][1] / det, -m[0][1] / det},
            {-m[1][0] / det,  m[0][0] / det}
        };
    }

    // 高斯消元法求逆矩阵（通用版本）
    private static double[][] inverse(double[][] matrix) {
        int n = matrix.length;
        double[][] augmented = new double[n][2 * n];
        for (int i = 0; i < n; i++) {
            System.arraycopy(matrix[i], 0, augmented[i], 0, n);
            augmented[i][i + n] = 1.0;
        }
        for (int i = 0; i < n; i++) {
            int maxRow = i;
            for (int k = i + 1; k < n; k++)
                if (Math.abs(augmented[k][i]) > Math.abs(augmented[maxRow][i]))
                    maxRow = k;
            double[] temp = augmented[i];
            augmented[i] = augmented[maxRow];
            augmented[maxRow] = temp;

            if (Math.abs(augmented[i][i]) < 1e-10)
                throw new ArithmeticException("矩阵不可逆");

            double pivot = augmented[i][i];
            for (int j = 0; j < 2 * n; j++)
                augmented[i][j] /= pivot;

            for (int k = 0; k < n; k++) {
                if (k != i) {
                    double factor = augmented[k][i];
                    for (int j = 0; j < 2 * n; j++)
                        augmented[k][j] -= factor * augmented[i][j];
                }
            }
        }
        double[][] inv = new double[n][n];
        for (int i = 0; i < n; i++)
            System.arraycopy(augmented[i], n, inv[i], 0, n);
        return inv;
    }

    /**
     * 伪逆法求解 LMSE
     * w = (X^T X)^{-1} X^T b
     */
    public static double[] solvePseudoInverse(double[][] X, double[] b) {
        double[][] Xt = transpose(X);
        double[][] XtX = multiply(Xt, X);
        double[][] XtX_inv = inverse(XtX);
        double[][] pseudoInv = multiply(XtX_inv, Xt);
        return multiplyVec(pseudoInv, b);
    }

    /**
     * 梯度下降迭代法求解 LMSE
     * w(k+1) = w(k) + rho * X^T (b - X * w(k))
     */
    public static double[] solveGradientDescent(double[][] X, double[] b,
                                                 double rho, int maxIter, double tolerance) {
        int n = X[0].length;
        double[] w = new double[n];
        Random rand = new Random(42);
        for (int i = 0; i < n; i++) w[i] = rand.nextDouble() * 0.1;

        double[][] Xt = transpose(X);

        for (int iter = 0; iter < maxIter; iter++) {
            double[] Xw = multiplyVec(X, w);
            double[] error = new double[b.length];
            for (int i = 0; i < b.length; i++) error[i] = b[i] - Xw[i];

            double mse = 0;
            for (double e : error) mse += e * e;
            mse /= error.length;

            if (mse < tolerance) {
                System.out.println("梯度下降在第 " + (iter + 1) + " 轮收敛，MSE = " + mse);
                return w;
            }

            double[] gradient = multiplyVec(Xt, error);
            for (int i = 0; i < n; i++) w[i] += rho * gradient[i];

            if ((iter + 1) % 100 == 0) {
                System.out.println("第 " + (iter + 1) + " 轮，MSE = " + String.format("%.6f", mse));
            }
        }
        System.out.println("梯度下降达到最大迭代次数");
        return w;
    }

    // 使用权向量进行分类预测
    public static int predict(double[] w, double[] sample) {
        double sum = 0;
        for (int i = 0; i < w.length; i++) sum += w[i] * sample[i];
        return sum >= 0 ? 1 : -1;
    }

    public static void main(String[] args) {
        // 构造增广样本矩阵（最后一列为偏置项1）
        // 类别1的样本规范化为正，类别2的样本取反
        double[][] X = {
            { 1.0,  2.0, 1.0},  // 类别1
            { 2.0,  3.0, 1.0},  // 类别1
            { 3.0,  3.0, 1.0},  // 类别1
            {-2.0, -1.0, -1.0}, // 类别2（取反）
            {-3.0, -2.0, -1.0}, // 类别2（取反）
            {-2.0, -3.0, -1.0}  // 类别2（取反）
        };
        double[] b = {1, 1, 1, 1, 1, 1};

        System.out.println("=== 伪逆法求解 ===");
        double[] w1 = solvePseudoInverse(X, b);
        System.out.println("权向量: " + Arrays.toString(w1));

        System.out.println("\n=== 梯度下降法求解 ===");
        double[] w2 = solveGradientDescent(X, b, 0.001, 1000, 0.001);
        System.out.println("权向量: " + Arrays.toString(w2));

        System.out.println("\n=== 分类测试 ===");
        double[][] testSamples = {{1.5, 2.5, 1.0}, {-1.5, -1.5, 1.0}};
        String[] labels = {"类别1", "类别2"};
        for (int i = 0; i < testSamples.length; i++) {
            int pred1 = predict(w1, testSamples[i]);
            int pred2 = predict(w2, testSamples[i]);
            System.out.println("样本 " + Arrays.toString(testSamples[i])
                + " -> 伪逆法预测: " + (pred1 == 1 ? labels[0] : labels[1])
                + "，梯度下降法预测: " + (pred2 == 1 ? labels[0] : labels[1]));
        }
    }
}
```

# 总结

LMSE算法是一种基于最小均方误差准则的线性分类器训练方法。相较于感知器算法，它具有以下优势：

1. **适用范围广**：不要求数据线性可分，在线性不可分时也能给出最小误差的解
2. **收敛性好**：伪逆法可以一步得到解析解，梯度下降法在适当学习率下也能可靠收敛
3. **数学基础扎实**：基于最小二乘理论，有完备的数学推导

其局限性在于：伪逆法需要计算矩阵逆，当特征维度很高时计算量大；梯度下降法对学习率敏感，需要合理选择步长参数。LMSE算法广泛应用于模式识别、自适应信号处理等领域。
