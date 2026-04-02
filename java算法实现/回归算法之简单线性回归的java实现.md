# 回归算法的Java实现

## 背景

回归算法的原理是通过建立自变量和因变量之间的关系模型，来预测连续型的因变量。它基于已知的自变量和因变量样本数据，通过拟合一个函数或曲线来描述二者之间的关系，并利用该模型进行预测。

回归算法的应用场景非常广泛，以下是一些常见的应用场景：

1. 经济预测：回归算法可以用于经济学领域的预测和分析，例如预测股票价格、房价、经济增长率等。通过建立自变量（如利率、通货膨胀率等）与因变量（如股票价格）之间的关系模型，可以进行未来趋势的预测和决策支持。
2. 金融风险评估：回归算法可以用于金融领域的风险评估和信用评分。通过分析客户的个人信息、财务状况等自变量与违约风险等因变量之间的关系，可以建立风险评估模型，帮助金融机构做出信贷决策。
3. 市场营销：回归算法可以用于市场营销领域的用户行为预测和推荐系统。通过分析用户的历史购买记录、浏览行为等自变量与用户购买意愿、偏好等因变量之间的关系，可以建立用户行为模型，实现个性化推荐和精准营销。
4. 医学研究：回归算法可以用于医学领域的疾病预测和治疗效果评估。通过分析患者的生理指标、基因表达等自变量与疾病发展、治疗效果等因变量之间的关系，可以建立疾病预测模型和治疗评估模型，为临床决策提供依据。
5. 自然资源管理：回归算法可以用于环境科学和资源管理领域的预测和决策支持。例如，通过分析气象数据、土壤特征等自变量与水资源供应、森林覆盖率等因变量之间的关系，可以建立水资源管理模型和森林覆盖预测模型，为资源管理和环境保护提供指导。

回归算法可以分为以下几个主要的类别：

1. **线性回归**（Linear Regression）：假设自变量与因变量之间存在线性关系，通过最小二乘法拟合直线/超平面
2. **逻辑回归**（Logistic Regression）：用于分类问题，通过Sigmoid函数将线性输出映射到概率空间
3. **多项式回归**（Polynomial Regression）：引入高次项拟合非线性关系，本质仍是线性回归
4. **支持向量回归**（SVR）：利用ε-不敏感损失函数，在ε管道内的误差不计入损失
5. **决策树回归**（Decision Tree Regression）：递归划分特征空间，在叶节点内用均值预测
6. **随机森林回归**（Random Forest Regression）：集成多棵决策树，取平均值作为最终预测
7. **岭回归**（Ridge Regression）：加入L2正则化项，解决多重共线性问题
8. **Lasso回归**（Lasso Regression）：加入L1正则化项，实现特征选择和稀疏解

## 回归算法的Java实现

下面对上述 8 种回归算法逐一给出纯 Java 实现（不依赖第三方库），每种算法均包含完整可运行的代码和测试用例。

**关于公式图示**：各小节中的数学公式使用 **LaTeX 渲染成的 SVG 图片**（来源：[CodeCogs](https://www.codecogs.com/latex/eqneditor.php)），在 Markdown 预览中通常比纯文本 LaTeX 更易读。需要 **联网** 才能加载图片；若无法显示，可将图片地址复制到浏览器打开，或使用支持 MathJax 的编辑器查看文末附录中的 LaTeX 源码。

### 1. 简单线性回归与多元线性回归

**简单线性回归**：假设因变量与单个自变量呈线性关系，用最小二乘法估计斜率与截距。

![简单线性回归模型 y=β₀+β₁x+ε](https://latex.codecogs.com/svg.latex?%5Cdisplaystyle%20y%3D%5Cbeta_0%2B%5Cbeta_1%20x%2B%5Cvarepsilon)

![最小二乘闭式解：斜率 β̂₁ 与截距 β̂₀](https://latex.codecogs.com/svg.latex?%5Cdisplaystyle%20%5Chat%7B%5Cbeta%7D_1%3D%5Cfrac%7B%5Csum_i(x_i-%5Cbar%7Bx%7D)(y_i-%5Cbar%7By%7D)%7D%7B%5Csum_i(x_i-%5Cbar%7Bx%7D)%5E2%7D%2C%5Cquad%20%5Chat%7B%5Cbeta%7D_0%3D%5Cbar%7By%7D-%5Chat%7B%5Cbeta%7D_1%5Cbar%7Bx%7D)

**多元线性回归**：设计矩阵 **X**（含截距列）、响应 **y**，普通最小二乘（OLS）的闭式解与拟合优度 **R²** 如下。

![OLS：系数向量的矩阵形式](https://latex.codecogs.com/svg.latex?%5Cdisplaystyle%20%5Chat%7B%5Cboldsymbol%7B%5Cbeta%7D%7D%3D(%5Cmathbf%7BX%7D%5E%5Ctop%5Cmathbf%7BX%7D)%5E%7B-1%7D%5Cmathbf%7BX%7D%5E%5Ctop%5Cmathbf%7By%7D)

![决定系数 R²](https://latex.codecogs.com/svg.latex?%5Cdisplaystyle%20R%5E2%3D1-%5Cfrac%7B%5Cmathrm%7BSS%7D_%7B%5Cmathrm%7Bres%7D%7D%7D%7B%5Cmathrm%7BSS%7D_%7B%5Cmathrm%7Btot%7D%7D%7D)

```
import java.util.Arrays;

public class LinearRegression {

    // ==================== 简单线性回归 ====================

    static class SimpleLinearRegression {
        private double intercept;  // 截距 β₀
        private double slope;      // 斜率 β₁
        private double rSquared;   // 决定系数 R²

        public void fit(double[] x, double[] y) {
            int n = x.length;
            double sumX = 0, sumY = 0;
            for (int i = 0; i < n; i++) { sumX += x[i]; sumY += y[i]; }
            double meanX = sumX / n, meanY = sumY / n;

            double ssXY = 0, ssXX = 0, ssYY = 0;
            for (int i = 0; i < n; i++) {
                double dx = x[i] - meanX, dy = y[i] - meanY;
                ssXY += dx * dy;
                ssXX += dx * dx;
                ssYY += dy * dy;
            }

            slope = ssXY / ssXX;
            intercept = meanY - slope * meanX;

            double ssRes = 0;
            for (int i = 0; i < n; i++) ssRes += Math.pow(y[i] - predict(x[i]), 2);
            rSquared = 1.0 - ssRes / ssYY;
        }

        public double predict(double x) { return intercept + slope * x; }

        @Override
        public String toString() {
            return String.format("y = %.4f + %.4f * x  (R² = %.4f)", intercept, slope, rSquared);
        }
    }

    // ==================== 多元线性回归 ====================

    static class MultipleLinearRegression {
        private double[] coefficients;  // β向量（含截距）
        private double rSquared;

        public void fit(double[][] X, double[] y) {
            int n = X.length, p = X[0].length;
            // 增广矩阵（加截距列）
            double[][] Xa = new double[n][p + 1];
            for (int i = 0; i < n; i++) {
                Xa[i][0] = 1.0;
                System.arraycopy(X[i], 0, Xa[i], 1, p);
            }
            // β = (Xᵀ X)⁻¹ Xᵀ y
            double[][] Xt = transpose(Xa);
            double[][] XtX = matMul(Xt, Xa);
            double[][] XtX_inv = inverse(XtX);
            double[][] XtXinvXt = matMul(XtX_inv, Xt);
            coefficients = matVecMul(XtXinvXt, y);

            double meanY = 0;
            for (double v : y) meanY += v;
            meanY /= n;
            double ssTot = 0, ssRes = 0;
            for (int i = 0; i < n; i++) {
                double pred = predict(X[i]);
                ssRes += Math.pow(y[i] - pred, 2);
                ssTot += Math.pow(y[i] - meanY, 2);
            }
            rSquared = 1.0 - ssRes / ssTot;
        }

        public double predict(double[] x) {
            double val = coefficients[0];
            for (int i = 0; i < x.length; i++) val += coefficients[i + 1] * x[i];
            return val;
        }

        public double getRSquared() { return rSquared; }
        public double[] getCoefficients() { return coefficients; }
    }

    // ==================== 矩阵工具 ====================

    static double[][] transpose(double[][] m) {
        int r = m.length, c = m[0].length;
        double[][] t = new double[c][r];
        for (int i = 0; i < r; i++) for (int j = 0; j < c; j++) t[j][i] = m[i][j];
        return t;
    }

    static double[][] matMul(double[][] a, double[][] b) {
        int r = a.length, c = b[0].length, k = a[0].length;
        double[][] res = new double[r][c];
        for (int i = 0; i < r; i++) for (int j = 0; j < c; j++)
            for (int l = 0; l < k; l++) res[i][j] += a[i][l] * b[l][j];
        return res;
    }

    static double[] matVecMul(double[][] m, double[] v) {
        double[] res = new double[m.length];
        for (int i = 0; i < m.length; i++)
            for (int j = 0; j < v.length; j++) res[i] += m[i][j] * v[j];
        return res;
    }

    static double[][] inverse(double[][] matrix) {
        int n = matrix.length;
        double[][] aug = new double[n][2 * n];
        for (int i = 0; i < n; i++) { System.arraycopy(matrix[i], 0, aug[i], 0, n); aug[i][i + n] = 1; }
        for (int i = 0; i < n; i++) {
            int mx = i;
            for (int k = i + 1; k < n; k++) if (Math.abs(aug[k][i]) > Math.abs(aug[mx][i])) mx = k;
            double[] tmp = aug[i]; aug[i] = aug[mx]; aug[mx] = tmp;
            double piv = aug[i][i];
            for (int j = 0; j < 2 * n; j++) aug[i][j] /= piv;
            for (int k = 0; k < n; k++) if (k != i) {
                double f = aug[k][i];
                for (int j = 0; j < 2 * n; j++) aug[k][j] -= f * aug[i][j];
            }
        }
        double[][] inv = new double[n][n];
        for (int i = 0; i < n; i++) System.arraycopy(aug[i], n, inv[i], 0, n);
        return inv;
    }

    // ==================== 评估工具 ====================

    static double mse(double[] actual, double[] predicted) {
        double sum = 0;
        for (int i = 0; i < actual.length; i++) sum += Math.pow(actual[i] - predicted[i], 2);
        return sum / actual.length;
    }

    static double rSquared(double[] actual, double[] predicted) {
        double mean = 0;
        for (double v : actual) mean += v;
        mean /= actual.length;
        double ssTot = 0, ssRes = 0;
        for (int i = 0; i < actual.length; i++) {
            ssTot += Math.pow(actual[i] - mean, 2);
            ssRes += Math.pow(actual[i] - predicted[i], 2);
        }
        return 1.0 - ssRes / ssTot;
    }

    public static void main(String[] args) {
        System.out.println("========== 1. 简单线性回归 ==========\n");
        double[] x = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10};
        double[] y = {2.1, 4.0, 5.8, 8.2, 9.8, 12.1, 14.0, 15.9, 18.2, 19.8};

        SimpleLinearRegression slr = new SimpleLinearRegression();
        slr.fit(x, y);
        System.out.println("模型: " + slr);
        System.out.println("预测 x=12: " + String.format("%.2f", slr.predict(12)));

        System.out.println("\n========== 多元线性回归 ==========\n");
        // 房价预测：面积、房间数、楼龄 -> 价格(万元)
        double[][] mX = {
            {80, 2, 10}, {90, 3, 8}, {100, 3, 5}, {120, 4, 3},
            {70, 2, 15}, {110, 3, 2}, {85, 2, 12}, {95, 3, 6},
            {130, 4, 1}, {75, 2, 20}, {105, 3, 4}, {115, 4, 7}
        };
        double[] mY = {150, 200, 250, 320, 120, 280, 160, 220, 350, 100, 260, 300};

        MultipleLinearRegression mlr = new MultipleLinearRegression();
        mlr.fit(mX, mY);
        System.out.println("系数 [截距, 面积, 房间数, 楼龄]: " + Arrays.toString(mlr.getCoefficients()));
        System.out.println("R² = " + String.format("%.4f", mlr.getRSquared()));
        double[] newHouse = {100, 3, 5};
        System.out.println("预测 " + Arrays.toString(newHouse) + " 的价格: "
            + String.format("%.1f万元", mlr.predict(newHouse)));
    }
}
```

### 2. 逻辑回归

逻辑回归先对特征做线性组合，再经 Sigmoid 映射为属于正类的概率；训练时常最小化平均交叉熵（对数损失），用梯度下降更新参数。

![线性得分 z](https://latex.codecogs.com/svg.latex?%5Cdisplaystyle%20z%3D%5Cmathbf%7Bw%7D%5E%5Ctop%5Cmathbf%7Bx%7D%2Bb)

![Sigmoid 函数](https://latex.codecogs.com/svg.latex?%5Cdisplaystyle%20%5Csigma(z)%3D%5Cfrac%7B1%7D%7B1%2Be%5E%7B-z%7D%7D)

![正类条件概率](https://latex.codecogs.com/svg.latex?%5Cdisplaystyle%20P(y%3D1%5Cmid%5Cmathbf%7Bx%7D)%3D%5Csigma(%5Cmathbf%7Bw%7D%5E%5Ctop%5Cmathbf%7Bx%7D%2Bb)

![交叉熵损失（平均对数损失）](https://latex.codecogs.com/svg.latex?%5Cdisplaystyle%20%5Cmathcal%7BL%7D%3D-%5Cfrac%7B1%7D%7Bn%7D%5Csum_%7Bi%3D1%7D%5En%5Cbig%5By_i%5Clog%5Chat%7By%7D_i%2B(1-y_i)%5Clog(1-%5Chat%7By%7D_i)%5Cbig%5D)

```
import java.util.Arrays;
import java.util.Random;

public class LogisticRegression {

    private double[] weights;
    private double bias;
    private double learningRate;
    private int maxIterations;

    public LogisticRegression(double learningRate, int maxIterations) {
        this.learningRate = learningRate;
        this.maxIterations = maxIterations;
    }

    private double sigmoid(double z) {
        return 1.0 / (1.0 + Math.exp(-z));
    }

    public void fit(double[][] X, int[] y) {
        int n = X.length, d = X[0].length;
        weights = new double[d];
        bias = 0;
        Random rand = new Random(42);
        for (int i = 0; i < d; i++) weights[i] = rand.nextGaussian() * 0.01;

        for (int iter = 0; iter < maxIterations; iter++) {
            double[] dw = new double[d];
            double db = 0;
            double totalLoss = 0;

            for (int i = 0; i < n; i++) {
                double z = bias;
                for (int j = 0; j < d; j++) z += weights[j] * X[i][j];
                double pred = sigmoid(z);

                double error = pred - y[i];
                for (int j = 0; j < d; j++) dw[j] += error * X[i][j];
                db += error;

                totalLoss += -y[i] * Math.log(pred + 1e-15) - (1 - y[i]) * Math.log(1 - pred + 1e-15);
            }

            for (int j = 0; j < d; j++) weights[j] -= learningRate * dw[j] / n;
            bias -= learningRate * db / n;

            if ((iter + 1) % 200 == 0)
                System.out.println("Epoch " + (iter + 1) + ", Loss = " + String.format("%.4f", totalLoss / n));
        }
    }

    public double predictProba(double[] x) {
        double z = bias;
        for (int j = 0; j < x.length; j++) z += weights[j] * x[j];
        return sigmoid(z);
    }

    public int predict(double[] x) {
        return predictProba(x) >= 0.5 ? 1 : 0;
    }

    public double accuracy(double[][] X, int[] y) {
        int correct = 0;
        for (int i = 0; i < X.length; i++) if (predict(X[i]) == y[i]) correct++;
        return (double) correct / X.length;
    }

    public static void main(String[] args) {
        System.out.println("========== 2. 逻辑回归 ==========\n");

        // 考试成绩预测是否通过：学习时长、做题数 -> 通过(1)/不通过(0)
        double[][] X = {
            {1.0, 10}, {1.5, 15}, {2.0, 20}, {2.5, 30}, {3.0, 40},
            {3.5, 45}, {4.0, 50}, {4.5, 55}, {5.0, 60}, {5.5, 70},
            {6.0, 80}, {6.5, 85}, {7.0, 90}, {7.5, 95}, {8.0, 100}
        };
        int[] y = {0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1};

        // 特征归一化
        double[] maxVals = new double[X[0].length];
        for (int j = 0; j < X[0].length; j++) {
            for (double[] row : X) maxVals[j] = Math.max(maxVals[j], row[j]);
            for (double[] row : X) row[j] /= maxVals[j];
        }

        LogisticRegression lr = new LogisticRegression(0.5, 1000);
        lr.fit(X, y);

        System.out.println("\n训练集准确率: " + String.format("%.1f%%", lr.accuracy(X, y) * 100));
        System.out.println("权重: " + Arrays.toString(lr.weights) + ", 偏置: " + String.format("%.4f", lr.bias));

        double[][] testX = {{3.0, 35}, {5.0, 60}, {7.0, 90}};
        System.out.println("\n预测结果:");
        for (double[] sample : testX) {
            double[] norm = {sample[0] / maxVals[0], sample[1] / maxVals[1]};
            System.out.println("  学习" + sample[0] + "h+做" + (int)sample[1] + "题 -> 概率="
                + String.format("%.2f%%", lr.predictProba(norm) * 100)
                + ", 预测: " + (lr.predict(norm) == 1 ? "通过" : "不通过"));
        }
    }
}
```

### 3. 多项式回归

将标量 **x** 映射为多项式特征向量 **φ(x)**，再对 **φ(x)** 做线性回归；因此模型关于 **x** 是非线性的，关于系数 **β** 仍是线性的。

![多项式模型与特征向量 φ(x)](https://latex.codecogs.com/svg.latex?%5Cdisplaystyle%20y%3D%5Csum_%7Bj%3D0%7D%5Ep%5Cbeta_j%20x%5Ej%2C%5Cquad%20%5Cboldsymbol%7B%5Cphi%7D(x)%3D%5B1%2Cx%2Cx%5E2%2C%5Cldots%2Cx%5Ep%5D%5E%5Ctop)

```
import java.util.Arrays;

public class PolynomialRegression {

    private double[] coefficients;  // β₀, β₁, ..., βₚ
    private int degree;
    private double rSquared;

    public PolynomialRegression(int degree) {
        this.degree = degree;
    }

    // 将一维特征展开为多项式特征矩阵
    private double[][] expandFeatures(double[] x) {
        int n = x.length;
        double[][] X = new double[n][degree + 1];
        for (int i = 0; i < n; i++)
            for (int j = 0; j <= degree; j++)
                X[i][j] = Math.pow(x[i], j);
        return X;
    }

    public void fit(double[] x, double[] y) {
        double[][] X = expandFeatures(x);
        // β = (Xᵀ X)⁻¹ Xᵀ y
        double[][] Xt = transpose(X);
        double[][] XtX = matMul(Xt, X);
        double[][] XtX_inv = inverse(XtX);
        double[][] pseudo = matMul(XtX_inv, Xt);
        coefficients = matVecMul(pseudo, y);

        double meanY = 0;
        for (double v : y) meanY += v;
        meanY /= y.length;
        double ssTot = 0, ssRes = 0;
        for (int i = 0; i < y.length; i++) {
            double pred = predict(x[i]);
            ssRes += Math.pow(y[i] - pred, 2);
            ssTot += Math.pow(y[i] - meanY, 2);
        }
        rSquared = 1.0 - ssRes / ssTot;
    }

    public double predict(double x) {
        double val = 0;
        for (int j = 0; j <= degree; j++) val += coefficients[j] * Math.pow(x, j);
        return val;
    }

    // 矩阵工具（同上，省略重复注释）
    static double[][] transpose(double[][] m) {
        int r = m.length, c = m[0].length;
        double[][] t = new double[c][r];
        for (int i = 0; i < r; i++) for (int j = 0; j < c; j++) t[j][i] = m[i][j];
        return t;
    }
    static double[][] matMul(double[][] a, double[][] b) {
        int r = a.length, c = b[0].length, k = a[0].length;
        double[][] res = new double[r][c];
        for (int i = 0; i < r; i++) for (int j = 0; j < c; j++)
            for (int l = 0; l < k; l++) res[i][j] += a[i][l] * b[l][j];
        return res;
    }
    static double[] matVecMul(double[][] m, double[] v) {
        double[] res = new double[m.length];
        for (int i = 0; i < m.length; i++) for (int j = 0; j < v.length; j++) res[i] += m[i][j] * v[j];
        return res;
    }
    static double[][] inverse(double[][] matrix) {
        int n = matrix.length;
        double[][] aug = new double[n][2 * n];
        for (int i = 0; i < n; i++) { System.arraycopy(matrix[i], 0, aug[i], 0, n); aug[i][i + n] = 1; }
        for (int i = 0; i < n; i++) {
            int mx = i;
            for (int k = i + 1; k < n; k++) if (Math.abs(aug[k][i]) > Math.abs(aug[mx][i])) mx = k;
            double[] tmp = aug[i]; aug[i] = aug[mx]; aug[mx] = tmp;
            double piv = aug[i][i];
            for (int j = 0; j < 2 * n; j++) aug[i][j] /= piv;
            for (int k = 0; k < n; k++) if (k != i) {
                double f = aug[k][i]; for (int j = 0; j < 2 * n; j++) aug[k][j] -= f * aug[i][j];
            }
        }
        double[][] inv = new double[n][n];
        for (int i = 0; i < n; i++) System.arraycopy(aug[i], n, inv[i], 0, n);
        return inv;
    }

    public static void main(String[] args) {
        System.out.println("========== 3. 多项式回归 ==========\n");

        // y = 0.5x² - 2x + 3 + 噪声
        double[] x = {0, 0.5, 1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5, 5.5, 6};
        double[] y = new double[x.length];
        java.util.Random rand = new java.util.Random(42);
        for (int i = 0; i < x.length; i++)
            y[i] = 0.5 * x[i] * x[i] - 2 * x[i] + 3 + rand.nextGaussian() * 0.3;

        System.out.println("不同阶数的多项式拟合对比:");
        for (int deg = 1; deg <= 4; deg++) {
            PolynomialRegression pr = new PolynomialRegression(deg);
            pr.fit(x, y);
            StringBuilder sb = new StringBuilder();
            sb.append(String.format("  %d阶: y = %.3f", deg, pr.coefficients[0]));
            for (int j = 1; j <= deg; j++)
                sb.append(String.format(" %+.3f*x^%d", pr.coefficients[j], j));
            sb.append(String.format("  (R² = %.4f)", pr.rSquared));
            System.out.println(sb);
        }

        System.out.println("\n2阶多项式预测:");
        PolynomialRegression best = new PolynomialRegression(2);
        best.fit(x, y);
        double[] testX = {-1, 3, 7};
        for (double tx : testX)
            System.out.println("  x=" + tx + " -> y=" + String.format("%.2f", best.predict(tx))
                + " (真实值≈" + String.format("%.2f", 0.5*tx*tx - 2*tx + 3) + ")");
    }
}
```

### 4. 支持向量回归（SVR）

线性 SVR 的预测为 **w** 与 **x** 的内积加偏置；**ε-不敏感损失** 在误差绝对值不超过 ε 时为 0，否则只惩罚超出部分。原始问题常在 **‖w‖²** 与经验损失之间用 **C** 折中（与下文代码中的近似梯度下降相对应）。

![线性 SVR 预测函数](https://latex.codecogs.com/svg.latex?%5Cdisplaystyle%20f(%5Cmathbf%7Bx%7D)%3D%5Cmathbf%7Bw%7D%5E%5Ctop%5Cmathbf%7Bx%7D%2Bb)

![ε-不敏感损失](https://latex.codecogs.com/svg.latex?%5Cdisplaystyle%20L_%5Cvarepsilon%3D%5Cmax%5Cbig(0%2C%5C%2C%7Cy-f(%5Cmathbf%7Bx%7D)%7C-%5Cvarepsilon%5Cbig)

![SVR 目标函数示意（带 L2 正则）](https://latex.codecogs.com/svg.latex?%5Cdisplaystyle%20%5Cmin_%7B%5Cmathbf%7Bw%7D%2Cb%7D%5C%3B%5Cfrac%7B1%7D%7B2%7D%5C%7C%5Cmathbf%7Bw%7D%5C%7C%5E2%2BC%5Csum_i%20L_%5Cvarepsilon)

```
import java.util.Arrays;
import java.util.Random;

public class SVRegression {

    private double[] weights;
    private double bias;
    private double epsilon;   // ε-不敏感带宽度
    private double C;         // 正则化参数
    private double lr;

    public SVRegression(double epsilon, double C, double learningRate) {
        this.epsilon = epsilon;
        this.C = C;
        this.lr = learningRate;
    }

    public void fit(double[][] X, double[] y, int maxIter) {
        int n = X.length, d = X[0].length;
        weights = new double[d];
        bias = 0;
        Random rand = new Random(42);
        for (int i = 0; i < d; i++) weights[i] = rand.nextGaussian() * 0.01;

        for (int iter = 0; iter < maxIter; iter++) {
            double[] dw = new double[d];
            double db = 0;

            for (int i = 0; i < n; i++) {
                double pred = predict(X[i]);
                double diff = pred - y[i];

                // ε-不敏感损失的梯度
                if (Math.abs(diff) > epsilon) {
                    double sign = diff > 0 ? 1.0 : -1.0;
                    for (int j = 0; j < d; j++) dw[j] += C * sign * X[i][j];
                    db += C * sign;
                }
            }

            // L2正则化梯度 + 损失梯度
            for (int j = 0; j < d; j++) {
                weights[j] -= lr * (weights[j] + dw[j] / n);
            }
            bias -= lr * db / n;

            if ((iter + 1) % 500 == 0) {
                double loss = 0;
                for (int j = 0; j < d; j++) loss += 0.5 * weights[j] * weights[j];
                for (int i = 0; i < n; i++) {
                    double err = Math.abs(predict(X[i]) - y[i]) - epsilon;
                    if (err > 0) loss += C * err;
                }
                System.out.println("Iter " + (iter + 1) + ", Loss = " + String.format("%.4f", loss / n));
            }
        }
    }

    public double predict(double[] x) {
        double val = bias;
        for (int j = 0; j < x.length; j++) val += weights[j] * x[j];
        return val;
    }

    public static void main(String[] args) {
        System.out.println("========== 4. 支持向量回归(SVR) ==========\n");

        // y = 3x₁ + 2x₂ + 1 + 噪声
        Random rand = new Random(42);
        int n = 50;
        double[][] X = new double[n][2];
        double[] y = new double[n];
        for (int i = 0; i < n; i++) {
            X[i][0] = rand.nextDouble() * 10;
            X[i][1] = rand.nextDouble() * 10;
            y[i] = 3 * X[i][0] + 2 * X[i][1] + 1 + rand.nextGaussian() * 0.5;
        }

        SVRegression svr = new SVRegression(0.5, 1.0, 0.001);
        svr.fit(X, y, 2000);

        System.out.println("\n权重: " + Arrays.toString(svr.weights) + " (期望≈[3, 2])");
        System.out.println("偏置: " + String.format("%.4f", svr.bias) + " (期望≈1)");

        double mse = 0;
        for (int i = 0; i < n; i++) mse += Math.pow(svr.predict(X[i]) - y[i], 2);
        System.out.println("MSE = " + String.format("%.4f", mse / n));
    }
}
```

### 5. 决策树回归

叶节点 **t** 上的预测值取该节点样本集 **S_t** 中 **y** 的均值；每次分裂时，在候选特征与阈值下将样本划为左子集 **S_L**、右子集 **S_R**，选择使 **加权方差** 最小的划分（与最小化加权 MSE 等价）。

![叶节点预测（样本均值）](https://latex.codecogs.com/svg.latex?%5Cdisplaystyle%20%5Chat%7By%7D_t%3D%5Cfrac%7B1%7D%7B%7CS_t%7C%7D%5Csum_%7Bi%5Cin%20S_t%7Dy_i)

![分裂准则：左右子集的加权方差](https://latex.codecogs.com/svg.latex?%5Cdisplaystyle%20%5Cfrac%7Bn_L%7D%7Bn%7D%5Cmathrm%7BVar%7D(y%5Cmid%20S_L)%2B%5Cfrac%7Bn_R%7D%7Bn%7D%5Cmathrm%7BVar%7D(y%5Cmid%20S_R)

```
import java.util.Arrays;
import java.util.ArrayList;
import java.util.List;

public class DecisionTreeRegression {

    static class TreeNode {
        int featureIndex;
        double threshold;
        double value;       // 叶节点预测值
        TreeNode left, right;
        boolean isLeaf;
    }

    private TreeNode root;
    private int maxDepth;
    private int minSamplesLeaf;

    public DecisionTreeRegression(int maxDepth, int minSamplesLeaf) {
        this.maxDepth = maxDepth;
        this.minSamplesLeaf = minSamplesLeaf;
    }

    public void fit(double[][] X, double[] y) {
        int[] indices = new int[X.length];
        for (int i = 0; i < indices.length; i++) indices[i] = i;
        root = buildTree(X, y, indices, 0);
    }

    private TreeNode buildTree(double[][] X, double[] y, int[] indices, int depth) {
        TreeNode node = new TreeNode();

        if (depth >= maxDepth || indices.length <= minSamplesLeaf) {
            node.isLeaf = true;
            node.value = mean(y, indices);
            return node;
        }

        int bestFeature = -1;
        double bestThreshold = 0, bestMSE = Double.MAX_VALUE;

        for (int f = 0; f < X[0].length; f++) {
            double[] vals = new double[indices.length];
            for (int i = 0; i < indices.length; i++) vals[i] = X[indices[i]][f];
            Arrays.sort(vals);

            for (int i = 0; i < vals.length - 1; i++) {
                if (vals[i] == vals[i + 1]) continue;
                double thr = (vals[i] + vals[i + 1]) / 2.0;

                List<Integer> leftIdx = new ArrayList<>(), rightIdx = new ArrayList<>();
                for (int idx : indices) {
                    if (X[idx][f] <= thr) leftIdx.add(idx);
                    else rightIdx.add(idx);
                }
                if (leftIdx.size() < minSamplesLeaf || rightIdx.size() < minSamplesLeaf) continue;

                double mse = weightedMSE(y, leftIdx, rightIdx);
                if (mse < bestMSE) {
                    bestMSE = mse;
                    bestFeature = f;
                    bestThreshold = thr;
                }
            }
        }

        if (bestFeature == -1) {
            node.isLeaf = true;
            node.value = mean(y, indices);
            return node;
        }

        node.featureIndex = bestFeature;
        node.threshold = bestThreshold;

        List<Integer> leftList = new ArrayList<>(), rightList = new ArrayList<>();
        for (int idx : indices) {
            if (X[idx][bestFeature] <= bestThreshold) leftList.add(idx);
            else rightList.add(idx);
        }

        node.left = buildTree(X, y, leftList.stream().mapToInt(Integer::intValue).toArray(), depth + 1);
        node.right = buildTree(X, y, rightList.stream().mapToInt(Integer::intValue).toArray(), depth + 1);
        return node;
    }

    public double predict(double[] x) {
        TreeNode node = root;
        while (!node.isLeaf) {
            node = (x[node.featureIndex] <= node.threshold) ? node.left : node.right;
        }
        return node.value;
    }

    private double mean(double[] y, int[] indices) {
        double sum = 0;
        for (int i : indices) sum += y[i];
        return sum / indices.length;
    }

    private double weightedMSE(double[] y, List<Integer> left, List<Integer> right) {
        double total = left.size() + right.size();
        return (left.size() / total) * variance(y, left) + (right.size() / total) * variance(y, right);
    }

    private double variance(double[] y, List<Integer> indices) {
        double mean = 0;
        for (int i : indices) mean += y[i];
        mean /= indices.size();
        double var = 0;
        for (int i : indices) var += Math.pow(y[i] - mean, 2);
        return var / indices.size();
    }

    public static void main(String[] args) {
        System.out.println("========== 5. 决策树回归 ==========\n");

        // 非线性数据: y = sin(x) + 噪声
        java.util.Random rand = new java.util.Random(42);
        int n = 60;
        double[][] X = new double[n][1];
        double[] y = new double[n];
        for (int i = 0; i < n; i++) {
            X[i][0] = i * 0.2;
            y[i] = Math.sin(X[i][0]) + rand.nextGaussian() * 0.1;
        }

        System.out.println("不同深度对比:");
        for (int depth : new int[]{2, 4, 6, 10}) {
            DecisionTreeRegression dt = new DecisionTreeRegression(depth, 2);
            dt.fit(X, y);
            double mse = 0;
            for (int i = 0; i < n; i++) mse += Math.pow(dt.predict(X[i]) - y[i], 2);
            mse /= n;
            System.out.println("  maxDepth=" + depth + ", MSE=" + String.format("%.6f", mse));
        }

        DecisionTreeRegression dt = new DecisionTreeRegression(5, 2);
        dt.fit(X, y);
        System.out.println("\n预测样例 (maxDepth=5):");
        double[] testVals = {0.5, 1.57, 3.14, 4.71, 6.28};
        for (double v : testVals)
            System.out.println("  x=" + String.format("%.2f", v) + " -> 预测=" + String.format("%.4f", dt.predict(new double[]{v}))
                + ", 真实sin(x)=" + String.format("%.4f", Math.sin(v)));
    }
}
```

### 6. 随机森林回归

对训练集做 **Bootstrap** 抽样并训练多棵回归树 **h_t**，回归预测取各棵树输出的 **算术平均**，从而降低单棵树的方差。

![Bagging：森林平均预测](https://latex.codecogs.com/svg.latex?%5Cdisplaystyle%20%5Chat%7By%7D(%5Cmathbf%7Bx%7D)%3D%5Cfrac%7B1%7D%7BT%7D%5Csum_%7Bt%3D1%7D%5ET%20h_t(%5Cmathbf%7Bx%7D)

```
import java.util.*;

public class RandomForestRegression {

    private List<SimpleRegressionTree> trees = new ArrayList<>();
    private int numTrees;
    private int maxDepth;
    private int maxFeatures;  // 每次分裂考虑的最大特征数
    private Random random;

    public RandomForestRegression(int numTrees, int maxDepth, int maxFeatures) {
        this.numTrees = numTrees;
        this.maxDepth = maxDepth;
        this.maxFeatures = maxFeatures;
        this.random = new Random(42);
    }

    public void fit(double[][] X, double[] y) {
        int n = X.length;
        for (int t = 0; t < numTrees; t++) {
            // Bootstrap采样
            int[] sampleIdx = new int[n];
            for (int i = 0; i < n; i++) sampleIdx[i] = random.nextInt(n);

            double[][] Xs = new double[n][];
            double[] ys = new double[n];
            for (int i = 0; i < n; i++) { Xs[i] = X[sampleIdx[i]]; ys[i] = y[sampleIdx[i]]; }

            SimpleRegressionTree tree = new SimpleRegressionTree(maxDepth, 2, maxFeatures, random);
            tree.fit(Xs, ys);
            trees.add(tree);
        }
    }

    public double predict(double[] x) {
        double sum = 0;
        for (SimpleRegressionTree tree : trees) sum += tree.predict(x);
        return sum / trees.size();
    }

    // 简化的回归树（支持随机特征选择）
    static class SimpleRegressionTree {
        int maxDepth, minLeaf, maxFeatures;
        Random random;
        int[] root_featureIdx;
        double[] root_threshold;
        double[] root_value;
        int[] root_left, root_right;
        int nodeCount = 0;

        SimpleRegressionTree(int maxDepth, int minLeaf, int maxFeatures, Random random) {
            this.maxDepth = maxDepth; this.minLeaf = minLeaf;
            this.maxFeatures = maxFeatures; this.random = random;
            int maxNodes = (int) Math.pow(2, maxDepth + 1);
            root_featureIdx = new int[maxNodes]; root_threshold = new double[maxNodes];
            root_value = new double[maxNodes]; root_left = new int[maxNodes]; root_right = new int[maxNodes];
            Arrays.fill(root_left, -1); Arrays.fill(root_right, -1);
        }

        void fit(double[][] X, double[] y) {
            List<Integer> all = new ArrayList<>();
            for (int i = 0; i < X.length; i++) all.add(i);
            build(X, y, all, 0, 0);
        }

        int build(double[][] X, double[] y, List<Integer> indices, int depth, int nodeId) {
            nodeCount = Math.max(nodeCount, nodeId + 1);
            if (depth >= maxDepth || indices.size() <= minLeaf) {
                double sum = 0; for (int i : indices) sum += y[i];
                root_value[nodeId] = sum / indices.size();
                return nodeId;
            }

            int d = X[0].length;
            Set<Integer> featureSet = new HashSet<>();
            while (featureSet.size() < Math.min(maxFeatures, d))
                featureSet.add(random.nextInt(d));

            double bestMSE = Double.MAX_VALUE; int bestF = -1; double bestT = 0;
            for (int f : featureSet) {
                double[] vals = new double[indices.size()];
                for (int i = 0; i < indices.size(); i++) vals[i] = X[indices.get(i)][f];
                Arrays.sort(vals);
                for (int i = 0; i < vals.length - 1; i++) {
                    if (vals[i] == vals[i + 1]) continue;
                    double thr = (vals[i] + vals[i + 1]) / 2.0;
                    List<Integer> L = new ArrayList<>(), R = new ArrayList<>();
                    for (int idx : indices) { if (X[idx][f] <= thr) L.add(idx); else R.add(idx); }
                    if (L.size() < minLeaf || R.size() < minLeaf) continue;
                    double mse = wMSE(y, L, R);
                    if (mse < bestMSE) { bestMSE = mse; bestF = f; bestT = thr; }
                }
            }

            if (bestF == -1) {
                double sum = 0; for (int i : indices) sum += y[i];
                root_value[nodeId] = sum / indices.size();
                return nodeId;
            }

            root_featureIdx[nodeId] = bestF; root_threshold[nodeId] = bestT;
            List<Integer> L = new ArrayList<>(), R = new ArrayList<>();
            for (int idx : indices) { if (X[idx][bestF] <= bestT) L.add(idx); else R.add(idx); }
            root_left[nodeId] = nodeId * 2 + 1;
            root_right[nodeId] = nodeId * 2 + 2;
            build(X, y, L, depth + 1, root_left[nodeId]);
            build(X, y, R, depth + 1, root_right[nodeId]);
            return nodeId;
        }

        double predict(double[] x) {
            int node = 0;
            while (root_left[node] != -1) {
                node = (x[root_featureIdx[node]] <= root_threshold[node]) ? root_left[node] : root_right[node];
            }
            return root_value[node];
        }

        double wMSE(double[] y, List<Integer> L, List<Integer> R) {
            double total = L.size() + R.size();
            return (L.size() / total) * var(y, L) + (R.size() / total) * var(y, R);
        }
        double var(double[] y, List<Integer> idx) {
            double m = 0; for (int i : idx) m += y[i]; m /= idx.size();
            double v = 0; for (int i : idx) v += (y[i] - m) * (y[i] - m); return v / idx.size();
        }
    }

    public static void main(String[] args) {
        System.out.println("========== 6. 随机森林回归 ==========\n");

        Random rand = new Random(42);
        int n = 80;
        double[][] X = new double[n][2];
        double[] y = new double[n];
        for (int i = 0; i < n; i++) {
            X[i][0] = rand.nextDouble() * 6; X[i][1] = rand.nextDouble() * 6;
            y[i] = Math.sin(X[i][0]) + Math.cos(X[i][1]) + rand.nextGaussian() * 0.1;
        }

        System.out.println("树数量对比:");
        for (int nt : new int[]{1, 5, 20, 50}) {
            RandomForestRegression rf = new RandomForestRegression(nt, 5, 1);
            rf.fit(X, y);
            double mse = 0;
            for (int i = 0; i < n; i++) mse += Math.pow(rf.predict(X[i]) - y[i], 2);
            System.out.println("  numTrees=" + nt + ", MSE=" + String.format("%.6f", mse / n));
        }

        RandomForestRegression rf = new RandomForestRegression(30, 5, 1);
        rf.fit(X, y);
        System.out.println("\n预测样例 (30棵树):");
        double[][] testX = {{1.0, 1.0}, {3.14, 0}, {0, 3.14}};
        for (double[] tx : testX)
            System.out.println("  x=" + Arrays.toString(tx) + " -> 预测=" + String.format("%.4f", rf.predict(tx))
                + ", 真实≈" + String.format("%.4f", Math.sin(tx[0]) + Math.cos(tx[1])));
    }
}
```

### 7. 岭回归（Ridge Regression）

在残差平方和上加 **L2 惩罚**（通常不对截距惩罚，代码中矩阵 **D** 的对角元在截距位置为 0、其余为 1），闭式解与普通 OLS 类似，只是把 **XᵀX** 换成 **XᵀX + λD**。

![岭回归目标函数（不对 β₀ 惩罚的常用形式）](https://latex.codecogs.com/svg.latex?%5Cdisplaystyle%20%5Cmin_%7B%5Cboldsymbol%7B%5Cbeta%7D%7D%5C%7C%5Cmathbf%7By%7D-%5Cmathbf%7BX%7D%5Cboldsymbol%7B%5Cbeta%7D%5C%7C_2%5E2%2B%5Clambda%5Csum_%7Bj%3D1%7D%5Ep%5Cbeta_j%5E2)

![岭回归闭式解](https://latex.codecogs.com/svg.latex?%5Cdisplaystyle%20%5Chat%7B%5Cboldsymbol%7B%5Cbeta%7D%7D%3D(%5Cmathbf%7BX%7D%5E%5Ctop%5Cmathbf%7BX%7D%2B%5Clambda%5Cmathbf%7BD%7D)%5E%7B-1%7D%5Cmathbf%7BX%7D%5E%5Ctop%5Cmathbf%7By%7D)

```
import java.util.Arrays;

public class RidgeRegression {

    private double[] coefficients;
    private double lambda;
    private double rSquared;

    public RidgeRegression(double lambda) {
        this.lambda = lambda;
    }

    public void fit(double[][] X, double[] y) {
        int n = X.length, p = X[0].length;
        double[][] Xa = new double[n][p + 1];
        for (int i = 0; i < n; i++) { Xa[i][0] = 1.0; System.arraycopy(X[i], 0, Xa[i], 1, p); }

        double[][] Xt = transpose(Xa);
        double[][] XtX = matMul(Xt, Xa);

        // 加入 λI（注意不对截距正则化）
        for (int i = 1; i < XtX.length; i++) XtX[i][i] += lambda;

        double[][] inv = inverse(XtX);
        double[][] pseudo = matMul(inv, Xt);
        coefficients = matVecMul(pseudo, y);

        double meanY = 0; for (double v : y) meanY += v; meanY /= n;
        double ssTot = 0, ssRes = 0;
        for (int i = 0; i < n; i++) {
            double pred = predict(X[i]);
            ssRes += Math.pow(y[i] - pred, 2);
            ssTot += Math.pow(y[i] - meanY, 2);
        }
        rSquared = 1.0 - ssRes / ssTot;
    }

    public double predict(double[] x) {
        double v = coefficients[0];
        for (int i = 0; i < x.length; i++) v += coefficients[i + 1] * x[i];
        return v;
    }

    // 矩阵工具
    static double[][] transpose(double[][] m) {
        int r = m.length, c = m[0].length; double[][] t = new double[c][r];
        for (int i = 0; i < r; i++) for (int j = 0; j < c; j++) t[j][i] = m[i][j]; return t;
    }
    static double[][] matMul(double[][] a, double[][] b) {
        int r = a.length, c = b[0].length, k = a[0].length; double[][] res = new double[r][c];
        for (int i = 0; i < r; i++) for (int j = 0; j < c; j++)
            for (int l = 0; l < k; l++) res[i][j] += a[i][l] * b[l][j]; return res;
    }
    static double[] matVecMul(double[][] m, double[] v) {
        double[] res = new double[m.length];
        for (int i = 0; i < m.length; i++) for (int j = 0; j < v.length; j++) res[i] += m[i][j] * v[j]; return res;
    }
    static double[][] inverse(double[][] matrix) {
        int n = matrix.length; double[][] aug = new double[n][2*n];
        for (int i = 0; i < n; i++) { System.arraycopy(matrix[i],0,aug[i],0,n); aug[i][i+n]=1; }
        for (int i = 0; i < n; i++) {
            int mx=i; for (int k=i+1;k<n;k++) if (Math.abs(aug[k][i])>Math.abs(aug[mx][i])) mx=k;
            double[] tmp=aug[i]; aug[i]=aug[mx]; aug[mx]=tmp;
            double piv=aug[i][i]; for (int j=0;j<2*n;j++) aug[i][j]/=piv;
            for (int k=0;k<n;k++) if(k!=i){ double f=aug[k][i]; for (int j=0;j<2*n;j++) aug[k][j]-=f*aug[i][j]; }
        }
        double[][] inv = new double[n][n];
        for (int i=0;i<n;i++) System.arraycopy(aug[i],n,inv[i],0,n); return inv;
    }

    public static void main(String[] args) {
        System.out.println("========== 7. 岭回归 ==========\n");

        // 包含多重共线性的数据：x₂ ≈ 2*x₁ + 噪声
        java.util.Random rand = new java.util.Random(42);
        int n = 30;
        double[][] X = new double[n][3];
        double[] y = new double[n];
        for (int i = 0; i < n; i++) {
            X[i][0] = rand.nextDouble() * 10;
            X[i][1] = 2 * X[i][0] + rand.nextGaussian() * 0.5;  // 与x₁高度相关
            X[i][2] = rand.nextDouble() * 5;
            y[i] = 3 * X[i][0] + 1.5 * X[i][1] + 2 * X[i][2] + 5 + rand.nextGaussian() * 1.0;
        }

        System.out.println("不同λ值的岭回归系数对比:");
        System.out.printf("%-8s %-10s %-10s %-10s %-10s %-8s%n",
            "λ", "截距", "β₁", "β₂", "β₃", "R²");
        System.out.println("-".repeat(56));

        for (double lam : new double[]{0, 0.01, 0.1, 1.0, 10.0, 100.0}) {
            RidgeRegression rr = new RidgeRegression(lam);
            rr.fit(X, y);
            double[] c = rr.coefficients;
            double norm = 0;
            for (int i = 1; i < c.length; i++) norm += c[i] * c[i];
            System.out.printf("%-8.2f %-10.3f %-10.3f %-10.3f %-10.3f %-8.4f%n",
                lam, c[0], c[1], c[2], c[3], rr.rSquared);
        }
        System.out.println("\n观察：λ增大时，系数范数缩小（正则化效果），但R²会降低");
    }
}
```

### 8. Lasso回归（坐标下降法）

在残差平方和上加 **L1 惩罚** **λ‖β‖₁**，可产生稀疏系数。目标在零点不可微，常用 **坐标下降**；每步对某一坐标更新时会出现 **软阈值** 算子 **S(z, γ)**。

![Lasso 目标函数](https://latex.codecogs.com/svg.latex?%5Cdisplaystyle%20%5Cmin_%7B%5Cboldsymbol%7B%5Cbeta%7D%7D%5C%7C%5Cmathbf%7By%7D-%5Cmathbf%7BX%7D%5Cboldsymbol%7B%5Cbeta%7D%5C%7C_2%5E2%2B%5Clambda%5C%7C%5Cboldsymbol%7B%5Cbeta%7D%5C%7C_1)

![软阈值算子（坐标下降中的闭式更新）](https://latex.codecogs.com/svg.latex?%5Cdisplaystyle%20S(z%2C%5Cgamma)%3D%5Cmathrm%7Bsign%7D(z)%5Cmax(%7Cz%7C-%5Cgamma%2C0)

```
import java.util.Arrays;

public class LassoRegression {

    private double[] coefficients;  // β₁, β₂, ...（不含截距）
    private double intercept;
    private double lambda;

    public LassoRegression(double lambda) {
        this.lambda = lambda;
    }

    // 软阈值函数 S(z, γ) = sign(z) * max(|z| - γ, 0)
    private double softThreshold(double z, double gamma) {
        if (z > gamma) return z - gamma;
        if (z < -gamma) return z + gamma;
        return 0.0;
    }

    public void fit(double[][] X, double[] y, int maxIter, double tol) {
        int n = X.length, p = X[0].length;
        coefficients = new double[p];
        intercept = 0;

        // 标准化特征
        double[] xMean = new double[p], xStd = new double[p];
        double yMean = 0;
        for (double v : y) yMean += v;
        yMean /= n;

        for (int j = 0; j < p; j++) {
            for (int i = 0; i < n; i++) xMean[j] += X[i][j];
            xMean[j] /= n;
            for (int i = 0; i < n; i++) xStd[j] += Math.pow(X[i][j] - xMean[j], 2);
            xStd[j] = Math.sqrt(xStd[j] / n);
            if (xStd[j] == 0) xStd[j] = 1;
        }

        double[][] Xn = new double[n][p];
        double[] yn = new double[n];
        for (int i = 0; i < n; i++) {
            yn[i] = y[i] - yMean;
            for (int j = 0; j < p; j++) Xn[i][j] = (X[i][j] - xMean[j]) / xStd[j];
        }

        // 坐标下降
        double[] beta = new double[p];
        for (int iter = 0; iter < maxIter; iter++) {
            double maxChange = 0;
            for (int j = 0; j < p; j++) {
                // 计算残差（排除第j个特征的贡献）
                double rhoJ = 0;
                for (int i = 0; i < n; i++) {
                    double residual = yn[i];
                    for (int k = 0; k < p; k++)
                        if (k != j) residual -= beta[k] * Xn[i][k];
                    rhoJ += Xn[i][j] * residual;
                }
                rhoJ /= n;

                double oldBeta = beta[j];
                beta[j] = softThreshold(rhoJ, lambda);
                maxChange = Math.max(maxChange, Math.abs(beta[j] - oldBeta));
            }
            if (maxChange < tol) {
                System.out.println("坐标下降在第 " + (iter + 1) + " 轮收敛");
                break;
            }
        }

        // 还原到原始尺度
        for (int j = 0; j < p; j++) coefficients[j] = beta[j] / xStd[j];
        intercept = yMean;
        for (int j = 0; j < p; j++) intercept -= coefficients[j] * xMean[j];
    }

    public double predict(double[] x) {
        double v = intercept;
        for (int j = 0; j < x.length; j++) v += coefficients[j] * x[j];
        return v;
    }

    public int numNonZero() {
        int count = 0;
        for (double c : coefficients) if (Math.abs(c) > 1e-10) count++;
        return count;
    }

    public static void main(String[] args) {
        System.out.println("========== 8. Lasso回归 ==========\n");

        // 5个特征，但只有前2个对y有贡献: y = 3x₁ + 2x₂ + 噪声
        java.util.Random rand = new java.util.Random(42);
        int n = 50, p = 5;
        double[][] X = new double[n][p];
        double[] y = new double[n];
        for (int i = 0; i < n; i++) {
            for (int j = 0; j < p; j++) X[i][j] = rand.nextDouble() * 10;
            y[i] = 3 * X[i][0] + 2 * X[i][1] + rand.nextGaussian() * 0.5;
        }

        System.out.println("Lasso的特征选择能力（不同λ值）:");
        System.out.printf("%-8s %-8s %-60s%n", "λ", "非零系数", "系数向量");
        System.out.println("-".repeat(76));

        for (double lam : new double[]{0.001, 0.01, 0.1, 0.5, 1.0, 5.0}) {
            LassoRegression lasso = new LassoRegression(lam);
            lasso.fit(X, y, 5000, 1e-6);
            String coefStr = "[";
            for (int j = 0; j < lasso.coefficients.length; j++) {
                if (j > 0) coefStr += ", ";
                coefStr += String.format("%7.4f", lasso.coefficients[j]);
            }
            coefStr += "]";
            System.out.printf("%-8.3f %-8d %-60s%n", lam, lasso.numNonZero(), coefStr);
        }
        System.out.println("\n观察：λ增大时，不重要特征(x₃,x₄,x₅)的系数被压缩为0");
        System.out.println("真实系数: [3.0, 2.0, 0.0, 0.0, 0.0]");
    }
}
```

## 附录：公式 LaTeX 源码（便于本地 MathJax / KaTeX 渲染）

若无法加载上文图片，可将下列代码粘贴到支持 LaTeX 的编辑器或 [在线公式编辑器](https://www.codecogs.com/latex/eqneditor.php) 中查看。

**1. 线性回归**

```latex
y=\beta_0+\beta_1 x+\varepsilon
\hat{\beta}_1=\frac{\sum_i(x_i-\bar{x})(y_i-\bar{y})}{\sum_i(x_i-\bar{x})^2},\quad \hat{\beta}_0=\bar{y}-\hat{\beta}_1\bar{x}
\hat{\boldsymbol{\beta}}=(\mathbf{X}^\top\mathbf{X})^{-1}\mathbf{X}^\top\mathbf{y}
R^2=1-\frac{\mathrm{SS}_{\mathrm{res}}}{\mathrm{SS}_{\mathrm{tot}}}
```

**2. 逻辑回归**

```latex
z=\mathbf{w}^\top\mathbf{x}+b
\sigma(z)=\frac{1}{1+e^{-z}}
P(y=1\mid\mathbf{x})=\sigma(\mathbf{w}^\top\mathbf{x}+b)
\mathcal{L}=-\frac{1}{n}\sum_{i=1}^n\big[y_i\log\hat{y}_i+(1-y_i)\log(1-\hat{y}_i)\big]
```

**3. 多项式回归**

```latex
y=\sum_{j=0}^p\beta_j x^j,\quad \boldsymbol{\phi}(x)=[1,x,x^2,\ldots,x^p]^\top
```

**4. SVR**

```latex
f(\mathbf{x})=\mathbf{w}^\top\mathbf{x}+b
L_\varepsilon=\max\big(0,\,|y-f(\mathbf{x})|-\varepsilon\big)
\min_{\mathbf{w},b}\;\frac{1}{2}\|\mathbf{w}\|^2+C\sum_i L_\varepsilon
```

**5. 决策树回归**

```latex
\hat{y}_t=\frac{1}{|S_t|}\sum_{i\in S_t}y_i
\frac{n_L}{n}\mathrm{Var}(y\mid S_L)+\frac{n_R}{n}\mathrm{Var}(y\mid S_R)
```

**6. 随机森林**

```latex
\hat{y}(\mathbf{x})=\frac{1}{T}\sum_{t=1}^T h_t(\mathbf{x})
```

**7. 岭回归**

```latex
\min_{\boldsymbol{\beta}}\|\mathbf{y}-\mathbf{X}\boldsymbol{\beta}\|_2^2+\lambda\sum_{j=1}^p\beta_j^2
\hat{\boldsymbol{\beta}}=(\mathbf{X}^\top\mathbf{X}+\lambda\mathbf{D})^{-1}\mathbf{X}^\top\mathbf{y}
```

**8. Lasso**

```latex
\min_{\boldsymbol{\beta}}\|\mathbf{y}-\mathbf{X}\boldsymbol{\beta}\|_2^2+\lambda\|\boldsymbol{\beta}\|_1
S(z,\gamma)=\mathrm{sign}(z)\max(|z|-\gamma,0)
```

## 总结

### 8种回归算法对比

| 算法 | 类型 | 核心思想 | 优势 | 局限 |
|------|------|---------|------|------|
| 简单/多元线性回归 | 线性 | 最小二乘法 | 简单高效、可解释性强 | 只能拟合线性关系 |
| 逻辑回归 | 分类 | Sigmoid+交叉熵 | 输出概率、适合二分类 | 线性决策边界 |
| 多项式回归 | 非线性 | 特征空间扩展 | 能拟合曲线关系 | 高阶易过拟合 |
| 支持向量回归 | 非线性 | ε-不敏感损失 | 对异常值鲁棒 | 参数选择敏感 |
| 决策树回归 | 非线性 | 递归划分 | 可处理非线性、无需标准化 | 易过拟合 |
| 随机森林回归 | 集成 | Bagging+随机特征 | 高精度、抗过拟合 | 不可解释、计算量大 |
| 岭回归 | 正则化 | L2惩罚 | 解决多重共线性 | 不能做特征选择 |
| Lasso回归 | 正则化 | L1惩罚+坐标下降 | 自动特征选择、稀疏解 | 特征高度相关时不稳定 |

### 选择建议

- **线性关系、解释性优先** → 线性回归
- **分类问题** → 逻辑回归
- **明显曲线关系** → 多项式回归（注意阶数控制）
- **异常值多** → SVR 或岭回归
- **非线性+高维** → 决策树/随机森林
- **特征冗余、需要特征选择** → Lasso回归
- **多重共线性** → 岭回归
- **追求最高精度** → 随机森林
