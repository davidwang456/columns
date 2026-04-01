# 背景

句法模式识别（Syntactic Pattern Recognition）是结构模式识别的重要分支，由K.S. Fu在20世纪70年代系统发展。与统计模式识别使用数值特征向量描述模式不同，句法模式识别用符号串（或图、树等结构）来描述模式，用形式语言和文法来定义模式的类别。

句法模式识别的核心思想：
1. **基元提取**：将复杂模式分解为基本的不可再分的子模式（基元/原语）
2. **文法定义**：为每种模式类别定义一套产生规则（文法），描述基元的合法组合方式
3. **句法分析**：对待识别的模式提取基元串后，用语法分析器判断该串是否属于某个文法定义的语言

形式文法 G = (V_N, V_T, P, S)：
- V_N：非终结符集合
- V_T：终结符集合（基元）
- P：产生规则集合
- S：起始符号

例如，识别简单几何图形：
- 三角形 = 三条线段顺序连接且首尾相连
- 矩形 = 四条线段交替水平和垂直连接

句法模式识别广泛应用于：
- 字符/手写体识别
- 染色体形状分类
- 电路图识别
- 编程语言语法检查

# 句法模式识别的java实现

下面实现一个完整的句法模式识别系统，包括文法定义、CYK语法分析器和模式分类：

```
import java.util.*;

public class SyntacticPatternRecognition {

    /**
     * 上下文无关文法(CFG)
     */
    static class Grammar {
        String name;
        Set<String> nonTerminals = new HashSet<>();
        Set<String> terminals = new HashSet<>();
        List<String[]> rules = new ArrayList<>();  // [左部, 右部1, 右部2...] 
        String startSymbol;

        Grammar(String name, String startSymbol) {
            this.name = name;
            this.startSymbol = startSymbol;
            nonTerminals.add(startSymbol);
        }

        // 添加产生规则: A -> B C 或 A -> a
        void addRule(String lhs, String... rhs) {
            String[] rule = new String[rhs.length + 1];
            rule[0] = lhs;
            System.arraycopy(rhs, 0, rule, 1, rhs.length);
            rules.add(rule);
            nonTerminals.add(lhs);
            for (String s : rhs) {
                if (s.equals(s.toLowerCase()) && s.length() == 1) terminals.add(s);
                else nonTerminals.add(s);
            }
        }

        @Override
        public String toString() {
            StringBuilder sb = new StringBuilder();
            sb.append("文法: ").append(name).append("\n");
            sb.append("产生规则:\n");
            for (String[] rule : rules) {
                sb.append("  ").append(rule[0]).append(" -> ");
                for (int i = 1; i < rule.length; i++) {
                    if (i > 1) sb.append(" ");
                    sb.append(rule[i]);
                }
                sb.append("\n");
            }
            return sb.toString();
        }
    }

    /**
     * CYK算法（Cocke-Younger-Kasami）
     * 用于判断字符串是否能被CNF文法生成
     */
    static class CYKParser {
        Grammar grammar;

        CYKParser(Grammar grammar) {
            this.grammar = grammar;
        }

        // 判断输入串是否属于该文法定义的语言
        boolean parse(String[] input) {
            int n = input.length;
            if (n == 0) return false;

            // table[i][j] 存储能产生 input[i..j] 的非终结符集合
            @SuppressWarnings("unchecked")
            Set<String>[][] table = new HashSet[n][n];
            for (int i = 0; i < n; i++)
                for (int j = 0; j < n; j++)
                    table[i][j] = new HashSet<>();

            // 填充对角线：单个终结符
            for (int i = 0; i < n; i++) {
                for (String[] rule : grammar.rules) {
                    if (rule.length == 2 && rule[1].equals(input[i]))
                        table[i][i].add(rule[0]);
                }
            }

            // 自底向上填表
            for (int len = 2; len <= n; len++) {
                for (int i = 0; i <= n - len; i++) {
                    int j = i + len - 1;
                    for (int k = i; k < j; k++) {
                        for (String[] rule : grammar.rules) {
                            if (rule.length == 3) {
                                if (table[i][k].contains(rule[1]) && table[k + 1][j].contains(rule[2]))
                                    table[i][j].add(rule[0]);
                            }
                        }
                    }
                }
            }

            return table[0][n - 1].contains(grammar.startSymbol);
        }
    }

    /**
     * 简单的递归下降解析器（用于更直观的文法）
     */
    static class RecursiveDescentParser {
        Grammar grammar;
        String[] input;
        int pos;

        RecursiveDescentParser(Grammar grammar) {
            this.grammar = grammar;
        }

        boolean parse(String[] input) {
            this.input = input;
            this.pos = 0;
            boolean result = matchSymbol(grammar.startSymbol);
            return result && pos == input.length;
        }

        private boolean matchSymbol(String symbol) {
            // 如果是终结符
            if (grammar.terminals.contains(symbol)) {
                if (pos < input.length && input[pos].equals(symbol)) {
                    pos++;
                    return true;
                }
                return false;
            }

            // 如果是非终结符，尝试所有规则
            int savedPos = pos;
            for (String[] rule : grammar.rules) {
                if (rule[0].equals(symbol)) {
                    pos = savedPos;
                    boolean match = true;
                    for (int i = 1; i < rule.length; i++) {
                        if (!matchSymbol(rule[i])) { match = false; break; }
                    }
                    if (match) return true;
                }
            }
            pos = savedPos;
            return false;
        }
    }

    /**
     * 模式分类器：用多个文法定义不同的模式类别
     */
    static class PatternClassifier {
        Map<String, Grammar> classGrammars = new LinkedHashMap<>();
        Map<String, CYKParser> parsers = new LinkedHashMap<>();

        void addClass(String className, Grammar grammar) {
            classGrammars.put(className, grammar);
            parsers.put(className, new CYKParser(grammar));
        }

        String classify(String[] input) {
            for (Map.Entry<String, CYKParser> entry : parsers.entrySet()) {
                if (entry.getValue().parse(input))
                    return entry.getKey();
            }
            return "未知模式";
        }

        List<String> classifyAll(String[] input) {
            List<String> matches = new ArrayList<>();
            for (Map.Entry<String, CYKParser> entry : parsers.entrySet()) {
                if (entry.getValue().parse(input))
                    matches.add(entry.getKey());
            }
            return matches;
        }
    }

    public static void main(String[] args) {
        System.out.println("=== 句法模式识别 ===\n");

        // 示例1：几何图形识别
        // 基元：h(水平线), v(垂直线), d(斜线)
        System.out.println("--- 示例1：几何图形识别 ---\n");

        // 三角形文法（CNF）：三条斜线连接
        // S -> D R, R -> D D, D -> d
        Grammar triangleGrammar = new Grammar("三角形", "S");
        triangleGrammar.addRule("S", "D", "R");
        triangleGrammar.addRule("R", "D", "D");
        triangleGrammar.addRule("D", "d");

        // 矩形文法（CNF）：h v h v
        // S -> H T, T -> V U, U -> H V, H -> h, V -> v
        Grammar rectangleGrammar = new Grammar("矩形", "S");
        rectangleGrammar.addRule("S", "H", "T");
        rectangleGrammar.addRule("T", "V", "U");
        rectangleGrammar.addRule("U", "H", "V");
        rectangleGrammar.addRule("H", "h");
        rectangleGrammar.addRule("V", "v");

        // 直线文法：单个h或v
        // S -> h | v
        Grammar lineGrammar = new Grammar("直线", "S");
        lineGrammar.addRule("S", "h");
        lineGrammar.addRule("S", "v");

        System.out.println(triangleGrammar);
        System.out.println(rectangleGrammar);

        PatternClassifier shapeClassifier = new PatternClassifier();
        shapeClassifier.addClass("三角形", triangleGrammar);
        shapeClassifier.addClass("矩形", rectangleGrammar);
        shapeClassifier.addClass("直线", lineGrammar);

        String[][] shapeTests = {
            {"d", "d", "d"},     // 三角形
            {"h", "v", "h", "v"}, // 矩形
            {"h"},                // 直线
            {"d", "d"},           // 未知
            {"v"},                // 直线
        };

        for (String[] pattern : shapeTests) {
            String result = shapeClassifier.classify(pattern);
            System.out.println("基元串 " + Arrays.toString(pattern) + " -> " + result);
        }

        // 示例2：简单算术表达式的语法检查
        System.out.println("\n--- 示例2：表达式模式识别 ---\n");

        // 简化的表达式文法(CNF): n + n, n * n, (n + n) 等
        // 用基元: n(数字), p(加号), m(乘号), l(左括号), r(右括号)
        Grammar exprGrammar = new Grammar("算术表达式", "S");
        exprGrammar.addRule("S", "N");             // S -> N (单个数)
        exprGrammar.addRule("S", "N", "T");        // S -> N T (数+运算符尾)
        exprGrammar.addRule("T", "P", "N");        // T -> P N (+ n)
        exprGrammar.addRule("T", "M", "N");        // T -> M N (* n)
        exprGrammar.addRule("N", "n");
        exprGrammar.addRule("P", "p");
        exprGrammar.addRule("M", "m");

        CYKParser exprParser = new CYKParser(exprGrammar);

        String[][] exprTests = {
            {"n"},               // 合法：单个数
            {"n", "p", "n"},     // 合法：n + n
            {"n", "m", "n"},     // 合法：n * n
            {"p", "n"},          // 非法：+ n
            {"n", "n"},          // 非法：n n
        };

        System.out.println("表达式语法检查:");
        for (String[] expr : exprTests) {
            boolean valid = exprParser.parse(expr);
            System.out.println("  " + Arrays.toString(expr) + " -> " + (valid ? "合法表达式" : "非法表达式"));
        }

        // 示例3：DNA序列模式识别
        System.out.println("\n--- 示例3：DNA序列模式 ---\n");

        // 简化的回文序列文法（如限制性酶切位点）
        // 模式: a...t 配对, c...g 配对 -> 回文序列
        Grammar palindromeGrammar = new Grammar("回文序列", "S");
        palindromeGrammar.addRule("S", "A", "X");  // a...t
        palindromeGrammar.addRule("X", "S", "T");
        palindromeGrammar.addRule("S", "C", "Y");  // c...g
        palindromeGrammar.addRule("Y", "S", "G");
        palindromeGrammar.addRule("S", "A", "T");  // at
        palindromeGrammar.addRule("S", "C", "G");  // cg
        palindromeGrammar.addRule("A", "a");
        palindromeGrammar.addRule("T", "t");
        palindromeGrammar.addRule("C", "c");
        palindromeGrammar.addRule("G", "g");

        CYKParser dnaParser = new CYKParser(palindromeGrammar);
        String[][] dnaTests = {
            {"a", "t"},                 // at 回文
            {"a", "c", "g", "t"},       // acgt 回文
            {"c", "a", "t", "g"},       // catg 回文
            {"a", "a", "t"},            // 非回文
        };

        System.out.println("DNA回文序列检测:");
        for (String[] seq : dnaTests) {
            boolean isPalindrome = dnaParser.parse(seq);
            System.out.println("  " + Arrays.toString(seq) + " -> " + (isPalindrome ? "回文序列" : "非回文序列"));
        }
    }
}
```

# 总结

句法模式识别是一种独特的模式识别方法论，其核心特点：

1. **结构化描述**：用符号和文法规则描述模式的结构，而非数值特征向量
2. **强表达能力**：能描述具有递归、嵌套等复杂结构的模式
3. **可解释性强**：文法规则直观反映了模式的构成逻辑
4. **分类即解析**：模式分类等价于语法分析问题

其主要局限性：
- **基元提取困难**：如何从原始数据中可靠地提取基元是关键难点
- **文法设计依赖专家知识**：需要领域专家手工设计文法规则
- **对噪声敏感**：基元提取的错误会直接导致解析失败
- **计算复杂度**：CYK算法为O(n³)，对长序列不够高效

句法模式识别与统计模式识别并非互斥，现代方法常将两者结合：用统计方法提取可靠的基元，再用句法方法进行结构化分类。在自然语言处理、编译原理、生物序列分析等领域，句法方法仍然发挥着不可替代的作用。
