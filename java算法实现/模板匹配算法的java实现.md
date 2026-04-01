# 背景

模板匹配（Template Matching）是模式识别中最直观、最简单的方法之一。其基本思想是：预先为每个类别建立一个或多个"模板"（即该类别的代表性样本），当需要对新样本进行分类时，将其与所有模板进行相似度比较，归入与其最相似的模板所属的类别。

模板匹配常用的相似度/距离度量方法：

1. **欧氏距离**：d(x, t) = √(Σ(x_i - t_i)²)，距离越小越相似
2. **相关系数**：r = Σ(x_i * t_i) / (||x|| * ||t||)，即余弦相似度，值越大越相似
3. **曼哈顿距离**：d(x, t) = Σ|x_i - t_i|
4. **归一化互相关**：消除亮度和对比度差异的影响

模板匹配广泛应用于：
- 字符识别（OCR）：用标准字符作为模板
- 图像检索：在大图中搜索小模板图像
- 工业质检：将产品图像与标准模板对比
- 人脸识别：与已注册人脸模板比对

# 模板匹配算法的java实现

下面实现一个支持多种距离度量的模板匹配分类器，包括对一维特征向量和二维图像矩阵的匹配：

```
import java.util.*;

public class TemplateMatching {

    // 欧氏距离
    public static double euclideanDistance(double[] a, double[] b) {
        double sum = 0;
        for (int i = 0; i < a.length; i++)
            sum += Math.pow(a[i] - b[i], 2);
        return Math.sqrt(sum);
    }

    // 曼哈顿距离
    public static double manhattanDistance(double[] a, double[] b) {
        double sum = 0;
        for (int i = 0; i < a.length; i++)
            sum += Math.abs(a[i] - b[i]);
        return sum;
    }

    // 余弦相似度
    public static double cosineSimilarity(double[] a, double[] b) {
        double dot = 0, normA = 0, normB = 0;
        for (int i = 0; i < a.length; i++) {
            dot += a[i] * b[i];
            normA += a[i] * a[i];
            normB += b[i] * b[i];
        }
        if (normA == 0 || normB == 0) return 0;
        return dot / (Math.sqrt(normA) * Math.sqrt(normB));
    }

    // 模板匹配分类器
    static class TemplateClassifier {
        private List<double[]> templates = new ArrayList<>();
        private List<Integer> templateLabels = new ArrayList<>();
        private String metric;

        public TemplateClassifier(String metric) {
            this.metric = metric;
        }

        // 添加模板（可以每类一个均值模板，也可以每类多个样本模板）
        public void addTemplate(double[] template, int label) {
            templates.add(template);
            templateLabels.add(label);
        }

        // 从训练数据中生成类均值模板
        public void trainFromData(double[][] data, int[] labels) {
            Map<Integer, List<double[]>> classData = new LinkedHashMap<>();
            for (int i = 0; i < labels.length; i++)
                classData.computeIfAbsent(labels[i], k -> new ArrayList<>()).add(data[i]);

            for (Map.Entry<Integer, List<double[]>> entry : classData.entrySet()) {
                int label = entry.getKey();
                List<double[]> samples = entry.getValue();
                int dim = samples.get(0).length;
                double[] mean = new double[dim];
                for (double[] s : samples)
                    for (int j = 0; j < dim; j++) mean[j] += s[j];
                for (int j = 0; j < dim; j++) mean[j] /= samples.size();
                addTemplate(mean, label);
            }
        }

        public int predict(double[] x) {
            int bestLabel = -1;
            double bestScore = metric.equals("cosine") ? Double.NEGATIVE_INFINITY : Double.MAX_VALUE;

            for (int i = 0; i < templates.size(); i++) {
                double score;
                switch (metric) {
                    case "euclidean":
                        score = euclideanDistance(x, templates.get(i));
                        if (score < bestScore) { bestScore = score; bestLabel = templateLabels.get(i); }
                        break;
                    case "manhattan":
                        score = manhattanDistance(x, templates.get(i));
                        if (score < bestScore) { bestScore = score; bestLabel = templateLabels.get(i); }
                        break;
                    case "cosine":
                        score = cosineSimilarity(x, templates.get(i));
                        if (score > bestScore) { bestScore = score; bestLabel = templateLabels.get(i); }
                        break;
                    default:
                        throw new IllegalArgumentException("未知度量: " + metric);
                }
            }
            return bestLabel;
        }

        public double getMatchScore(double[] x) {
            double bestScore = metric.equals("cosine") ? Double.NEGATIVE_INFINITY : Double.MAX_VALUE;
            for (double[] template : templates) {
                double score;
                switch (metric) {
                    case "euclidean": score = euclideanDistance(x, template); bestScore = Math.min(bestScore, score); break;
                    case "manhattan": score = manhattanDistance(x, template); bestScore = Math.min(bestScore, score); break;
                    case "cosine":   score = cosineSimilarity(x, template);  bestScore = Math.max(bestScore, score); break;
                    default: throw new IllegalArgumentException("未知度量");
                }
            }
            return bestScore;
        }
    }

    // 二维图像模板匹配：在大图中滑动搜索小模板
    public static int[] imageTemplateMatch(double[][] image, double[][] template) {
        int imgH = image.length, imgW = image[0].length;
        int tplH = template.length, tplW = template[0].length;
        int bestR = 0, bestC = 0;
        double minDist = Double.MAX_VALUE;

        for (int r = 0; r <= imgH - tplH; r++) {
            for (int c = 0; c <= imgW - tplW; c++) {
                double dist = 0;
                for (int i = 0; i < tplH; i++)
                    for (int j = 0; j < tplW; j++)
                        dist += Math.pow(image[r + i][c + j] - template[i][j], 2);
                if (dist < minDist) {
                    minDist = dist;
                    bestR = r;
                    bestC = c;
                }
            }
        }
        return new int[]{bestR, bestC};
    }

    public static void main(String[] args) {
        System.out.println("=== 一维特征向量模板匹配 ===\n");

        double[][] trainData = {
            {1.0, 2.0}, {1.2, 1.8}, {0.9, 2.1},
            {5.0, 4.0}, {4.8, 4.2}, {5.2, 3.8},
            {1.0, 5.0}, {1.2, 4.8}, {0.8, 5.2}
        };
        int[] labels = {0, 0, 0, 1, 1, 1, 2, 2, 2};

        String[] metrics = {"euclidean", "manhattan", "cosine"};
        double[][] testSamples = {{1.1, 2.0}, {4.9, 4.1}, {1.0, 4.9}, {3.0, 3.0}};

        for (String metric : metrics) {
            TemplateClassifier clf = new TemplateClassifier(metric);
            clf.trainFromData(trainData, labels);
            System.out.println("度量方式: " + metric);
            for (double[] sample : testSamples) {
                int pred = clf.predict(sample);
                System.out.println("  样本 " + Arrays.toString(sample) + " -> 类别" + pred);
            }
            System.out.println();
        }

        System.out.println("=== 二维图像模板匹配 ===\n");
        double[][] image = {
            {0, 0, 0, 0, 0, 0, 0},
            {0, 0, 1, 1, 0, 0, 0},
            {0, 0, 1, 1, 0, 0, 0},
            {0, 0, 0, 0, 0, 0, 0},
            {0, 0, 0, 0, 1, 1, 0},
            {0, 0, 0, 0, 1, 1, 0},
            {0, 0, 0, 0, 0, 0, 0}
        };
        double[][] template = {{1, 1}, {1, 1}};

        int[] pos = imageTemplateMatch(image, template);
        System.out.println("模板在图像中的最佳匹配位置: (" + pos[0] + ", " + pos[1] + ")");
    }
}
```

# 总结

模板匹配算法的特点：

1. **直观简洁**：原理易于理解，实现简单直接
2. **无需训练过程**：只需存储模板即可，适用于小规模分类问题
3. **对模板质量敏感**：模板的代表性直接决定分类效果
4. **计算量随模板数增加而增大**：大规模应用时需要优化搜索策略

模板匹配的主要局限在于对旋转、缩放、变形等变换敏感，实际应用中常需要结合特征提取、归一化等预处理步骤来提高鲁棒性。在深度学习时代，模板匹配仍然在实时性要求高、模式变化小的工业质检等场景中有重要应用。
