# 背景

朴素贝叶斯（Naive Bayes）分类器是基于贝叶斯定理和特征条件独立假设的分类方法。"朴素"二字正是因为它假设各特征之间相互独立——这个假设虽然在现实中很少严格成立，但在实践中却表现出惊人的分类效果。

朴素贝叶斯分类器的核心思想：

1. 利用贝叶斯定理计算后验概率 P(ω|x)
2. 在特征独立假设下，类条件概率可分解为各特征概率的乘积：P(x|ω) = ∏ P(x_i|ω)
3. 选择后验概率最大的类别作为预测结果

朴素贝叶斯有多种变体：
- **高斯朴素贝叶斯**：假设连续特征服从高斯分布，适用于连续型数据
- **多项式朴素贝叶斯**：适用于离散特征，常用于文本分类（词频特征）
- **伯努利朴素贝叶斯**：适用于二值特征，常用于文档分类（词是否出现）

朴素贝叶斯广泛应用于垃圾邮件过滤、文本情感分析、医学诊断等领域。

# 朴素贝叶斯分类器的java实现

下面实现两种朴素贝叶斯分类器：高斯朴素贝叶斯（用于连续数据）和多项式朴素贝叶斯（用于文本分类）。

## 高斯朴素贝叶斯

```
import java.util.*;

public class GaussianNaiveBayes {

    private Map<Integer, Double> classPriors = new HashMap<>();
    private Map<Integer, double[]> classMeans = new HashMap<>();
    private Map<Integer, double[]> classVariances = new HashMap<>();

    public void train(double[][] X, int[] y) {
        Map<Integer, List<double[]>> classData = new HashMap<>();
        for (int i = 0; i < y.length; i++) {
            classData.computeIfAbsent(y[i], k -> new ArrayList<>()).add(X[i]);
        }

        int totalSamples = y.length;
        int numFeatures = X[0].length;

        for (Map.Entry<Integer, List<double[]>> entry : classData.entrySet()) {
            int label = entry.getKey();
            List<double[]> samples = entry.getValue();
            int n = samples.size();

            classPriors.put(label, (double) n / totalSamples);

            double[] mean = new double[numFeatures];
            for (double[] sample : samples)
                for (int j = 0; j < numFeatures; j++) mean[j] += sample[j];
            for (int j = 0; j < numFeatures; j++) mean[j] /= n;
            classMeans.put(label, mean);

            double[] variance = new double[numFeatures];
            for (double[] sample : samples)
                for (int j = 0; j < numFeatures; j++)
                    variance[j] += Math.pow(sample[j] - mean[j], 2);
            for (int j = 0; j < numFeatures; j++) variance[j] = variance[j] / n + 1e-9;
            classVariances.put(label, variance);
        }
    }

    // 高斯概率密度函数
    private double gaussianPdf(double x, double mean, double variance) {
        return Math.exp(-Math.pow(x - mean, 2) / (2 * variance)) / Math.sqrt(2 * Math.PI * variance);
    }

    public int predict(double[] x) {
        int bestClass = -1;
        double bestLogProb = Double.NEGATIVE_INFINITY;

        for (int label : classPriors.keySet()) {
            double logProb = Math.log(classPriors.get(label));
            double[] mean = classMeans.get(label);
            double[] var = classVariances.get(label);

            for (int j = 0; j < x.length; j++)
                logProb += Math.log(gaussianPdf(x[j], mean[j], var[j]));

            if (logProb > bestLogProb) {
                bestLogProb = logProb;
                bestClass = label;
            }
        }
        return bestClass;
    }

    public static void main(String[] args) {
        double[][] X = {
            {1.0, 2.1}, {1.4, 1.8}, {1.2, 2.3}, {0.8, 1.9},
            {5.0, 4.2}, {4.8, 4.5}, {5.2, 3.9}, {4.6, 4.1},
            {1.5, 5.0}, {1.2, 4.8}, {1.8, 5.2}, {1.0, 4.6}
        };
        int[] y = {0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2};

        GaussianNaiveBayes gnb = new GaussianNaiveBayes();
        gnb.train(X, y);

        System.out.println("=== 高斯朴素贝叶斯分类器 ===");
        double[][] testData = {{1.1, 2.0}, {4.9, 4.3}, {1.3, 4.9}, {3.0, 3.0}};
        for (double[] sample : testData) {
            int pred = gnb.predict(sample);
            System.out.println("样本 " + Arrays.toString(sample) + " -> 类别" + pred);
        }
    }
}
```

## 多项式朴素贝叶斯（文本分类）

```
import java.util.*;

public class MultinomialNaiveBayes {

    private Map<Integer, Double> classPriors = new HashMap<>();
    private Map<Integer, double[]> classWordProbs = new HashMap<>();
    private int vocabSize;

    // 训练：X为词频矩阵，y为类别标签
    public void train(int[][] X, int[] y) {
        vocabSize = X[0].length;
        Map<Integer, List<int[]>> classData = new HashMap<>();
        for (int i = 0; i < y.length; i++)
            classData.computeIfAbsent(y[i], k -> new ArrayList<>()).add(X[i]);

        int totalDocs = y.length;

        for (Map.Entry<Integer, List<int[]>> entry : classData.entrySet()) {
            int label = entry.getKey();
            List<int[]> docs = entry.getValue();
            classPriors.put(label, (double) docs.size() / totalDocs);

            // 拉普拉斯平滑：P(w_j|c) = (count(w_j, c) + 1) / (totalWords(c) + V)
            double[] wordCounts = new double[vocabSize];
            double totalWords = 0;
            for (int[] doc : docs)
                for (int j = 0; j < vocabSize; j++) {
                    wordCounts[j] += doc[j];
                    totalWords += doc[j];
                }

            double[] wordProbs = new double[vocabSize];
            for (int j = 0; j < vocabSize; j++)
                wordProbs[j] = (wordCounts[j] + 1.0) / (totalWords + vocabSize);
            classWordProbs.put(label, wordProbs);
        }
    }

    public int predict(int[] x) {
        int bestClass = -1;
        double bestLogProb = Double.NEGATIVE_INFINITY;

        for (int label : classPriors.keySet()) {
            double logProb = Math.log(classPriors.get(label));
            double[] wordProbs = classWordProbs.get(label);

            for (int j = 0; j < x.length; j++)
                if (x[j] > 0) logProb += x[j] * Math.log(wordProbs[j]);

            if (logProb > bestLogProb) {
                bestLogProb = logProb;
                bestClass = label;
            }
        }
        return bestClass;
    }

    public static void main(String[] args) {
        // 模拟文本分类：词汇表 = [体育, 比赛, 编程, 算法, 电影, 导演]
        String[] vocab = {"体育", "比赛", "编程", "算法", "电影", "导演"};
        int[][] X = {
            {3, 4, 0, 0, 0, 0},  // 体育类
            {5, 2, 0, 0, 1, 0},  // 体育类
            {0, 0, 4, 3, 0, 0},  // 科技类
            {0, 1, 3, 5, 0, 0},  // 科技类
            {0, 0, 0, 0, 4, 3},  // 娱乐类
            {1, 0, 0, 0, 5, 2},  // 娱乐类
        };
        int[] y = {0, 0, 1, 1, 2, 2};

        MultinomialNaiveBayes mnb = new MultinomialNaiveBayes();
        mnb.train(X, y);

        System.out.println("=== 多项式朴素贝叶斯（文本分类）===");
        String[] classNames = {"体育", "科技", "娱乐"};
        int[][] testDocs = {
            {4, 3, 0, 0, 0, 0},   // 应该是体育
            {0, 0, 2, 4, 0, 0},   // 应该是科技
            {0, 0, 0, 0, 3, 4},   // 应该是娱乐
            {2, 1, 1, 2, 0, 0}    // 混合
        };
        for (int[] doc : testDocs) {
            int pred = mnb.predict(doc);
            System.out.println("文档词频 " + Arrays.toString(doc) + " -> " + classNames[pred]);
        }
    }
}
```

# 总结

朴素贝叶斯分类器具有以下优点：

1. **简单高效**：训练和预测速度都很快，时间复杂度低
2. **小样本表现好**：在训练数据较少时依然能给出合理的分类结果
3. **可解释性强**：每个特征对分类结果的贡献清晰可见
4. **多类别扩展自然**：无需额外处理即可用于多分类问题

其局限性主要在于特征独立假设在现实中往往不成立，当特征之间存在强相关性时分类效果可能下降。尽管如此，朴素贝叶斯在文本分类、垃圾邮件检测等实际应用中依然是一种非常有效且广泛使用的基线分类方法。
