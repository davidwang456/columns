# 第24章：向量化、eval/query 与性能调优

## 1. 项目背景

某金融科技公司的风控团队维护着一套"实时交易风控评分系统"，需要对每笔交易计算一个风险分数。风控规则共有 12 条，包括：
- 金额异常检测（超过用户历史均值的 3 倍）
- 频次异常检测（1 小时内交易超过 5 次）
- 地理位置异常（两笔交易城市不同且时间间隔 < 30 分钟）
- 设备指纹异常等

数据工程师小周最初实现了一套逐行计算的风控脚本——`for idx, row in df.iterrows(): risk_score = compute(row)`。处理 100 万条交易耗时 45 分钟——远超业务容忍的 5 分钟 SLA。

**痛点一：逐行循环是性能杀手**。`iterrows()` 每行返回一个 Series（Python 对象），100 万行 = 100 万次 Python 函数调用 + 100 万次 Series 构造。相比之下，向量化操作 `df['amount'] > df['user_avg'] * 3` 在 C 层一次完成整列比较。

**痛点二：apply 也不是银弹**。小周把 `iterrows` 改成了 `df.apply(lambda row: ..., axis=1)`，性能从 45 分钟降到 18 分钟——有改善但远不够。因为 apply 仍然是 Python 逐行调用 lambda，只是省略了 Series 构造开销。

**痛点三：不知道哪里慢——盲目优化**。小周凭直觉"觉得 apply 慢""觉得 groupby 慢"，但实际上他的脚本中 60% 的时间花在了字符串操作 `df['city'].str.contains('北京')`——他根本不知道。

痛点流程：

```
风控脚本 (100万条交易)
  ├── iterrows → 45分钟 → SLA 5分钟
  ├── apply → 18分钟 → 仍远超 SLA
  └── 不知道哪里慢 → 盲目优化
```

本章将系统讲解 pandas 的向量化编程思维、性能度量工具、eval/query 加速和常见优化模式——把逐行计算改编为向量化代码，实现 20-50 倍加速。

## 2. 项目设计：剧本式交锋对话

> 场景：小周在代码评审会上展示了他的 iterrows 风控脚本，大师的表情逐渐凝固。

**小胖**：（嘴里塞着薯片）"这代码我熟——for 循环遍历每一行，清晰明了！跟我学 Python 第一课写的猜数字游戏一样！"

**大师**："小胖，你写猜数字游戏可以逐行循环，但处理 100 万行风控数据绝对不行。pandas 最重要的性能原则就是：**能用向量化操作绝不用循环**。

```python
# ❌ 逐行循环（Python 层，100 万次调用）
df['risk'] = 0
for idx in df.index:
    if df.loc[idx, 'amount'] > 10000:
        df.loc[idx, 'risk'] = 1

# ✅ 向量化（C 层，1 次整列操作）
df['risk'] = (df['amount'] > 10000).astype(int)
```

区别在哪里？第二条 `df['amount'] > 10000` 在底层调用的是 NumPy 的 C 实现——100 万次比较在 C 层一次完成，没有 Python 解释器的参与。"

**【技术映射：向量化 = 流水线批量生产——一次处理整批，而非手工作坊逐个加工】**

**小白**："`eval` 和 `query` 呢？它们好像也能加速？和向量化是什么关系？"

**大师**："`eval` 和 `query` 是 pandas 的表达式求值引擎，底层用 `numexpr` 库——它可以把复杂的算术表达式编译为高效的向量化计算，而且免去了 Python 的中间变量分配：

```python
# 普通写法：创建 3 个中间 Series
result = (df['a'] + df['b']) / (df['c'] ** 2 + df['d'])

# eval 写法：直接用字符串表达式，底层 numexpr 优化
result = df.eval('(a + b) / (c ** 2 + d)')
```

`query` 是 `eval` 的筛选版：

```python
# 普通布尔索引
df[(df['amount'] > 1000) & (df['status'] == 'completed')]

# query 写法（更简洁，且用 numexpr 加速）
df.query('amount > 1000 and status == "completed"')
```

对于大数据集（数十万行以上），`eval`/`query` 比普通写法快 2-5 倍——因为它们避免了多个中间布尔 Series 的创建和内存分配。"

**【技术映射：eval/query = 计算器（直接输入表达式，一次算出），普通写法 = 草稿纸（每一步写下来，中间结果占空间）】**

**小白**："那我怎么知道哪段代码最慢？凭感觉优化吗？"

**大师**："绝对不要凭感觉。用性能度量工具：

```python
# 1. timeit：快速对比两段代码
%timeit df['amount'] * 0.9

# 2. perf_counter：脚本级计时
import time
start = time.perf_counter()
# ... your code ...
print(f'{time.perf_counter() - start:.3f}s')

# 3. memory_profiler：看内存占用
# pip install memory_profiler
%memit df.groupby('user_id')['amount'].sum()

# 4. pandas 内置分组耗时
df.groupby('user_id')['amount'].apply(lambda x: x.sum())  # 慢
```

先用 `%timeit` 找到最慢的几行，再针对性地优化——而不是全脚本重构。"

**【技术映射：性能度量 = 体检报告——先做检查知道哪项指标异常，再对症下药】**

**小胖**："那遇到复杂条件——比如 12 条风控规则，每条规则都要判断——怎么才能不用 for 循环？"

**大师**："`np.select` 是终极武器——它可以在一次向量化操作中处理多个条件分支：

```python
conditions = [
    (df['amount'] > 10000) & (df['is_new_user']),
    (df['amount'] > 5000) & (df['city_diff']),
    df['frequency_1h'] > 5,
]
choices = ['高风险', '中风险', '频次异常']
df['risk_level'] = np.select(conditions, choices, default='正常')
```

`np.select` 一次向量化调用替代了 12 条 if-elif——速度提升 10-30 倍。"

**大师总结**："性能优化的四层金字塔：
1. **算法层面**：用 groupby 替代逐行，用 merge 替代循环查找
2. **向量化层面**：整列操作替代 apply/iterrows
3. **引擎层面**：eval/query 替代普通布尔索引
4. **底层加速**：Numba/Cython（高级篇会涉及）

记住：**先度量，再优化，优化最慢的 20% 代码通常能解决 80% 的性能问题。**"

## 3. 项目实战

### 3.1 准备

```bash
pip install pandas numpy matplotlib
```

### 3.2 模拟风控数据

```python
# generate_risk_data.py
import pandas as pd
import numpy as np

np.random.seed(42)
N = 200_000

df = pd.DataFrame({
    'user_id': np.random.randint(10000, 30000, N),
    'amount': np.abs(np.random.normal(500, 400, N)).round(2),
    'city': np.random.choice(['北京','上海','广州','深圳','杭州','成都'], N),
    'is_new_user': np.random.choice([True, False], N, p=[0.2, 0.8]),
    'hour': np.random.randint(0, 24, N),
    'device_type': np.random.choice(['iOS','Android','Web'], N, p=[0.3,0.6,0.1]),
})

# 用户历史均值
user_avg = df.groupby('user_id')['amount'].transform('mean')
df['user_avg_amount'] = user_avg.round(2)

# 上一笔交易城市（模拟）
df['prev_city'] = df.groupby('user_id')['city'].shift(1)

df.to_csv('risk_transactions.csv', index=False)
print(f"已生成 {N:,} 条交易数据")
```

### 3.3 分步实现

#### 步骤 1：性能基准——比较 iterrows / apply / 向量化

**目标**：用 %timeit 量化三种方法的性能差异。

```python
# step1_benchmark.py
import pandas as pd
import numpy as np
import time

df = pd.read_csv('risk_transactions.csv')

# === iterrows ===
start = time.perf_counter()
result1 = np.zeros(len(df), dtype=int)
for i, (idx, row) in enumerate(df.iterrows()):
    if row['amount'] > row['user_avg_amount'] * 3:
        result1[i] = 1
t_iter = time.perf_counter() - start

# === apply ===
start = time.perf_counter()
result2 = df.apply(lambda r: 1 if r['amount'] > r['user_avg_amount'] * 3 else 0, axis=1)
t_apply = time.perf_counter() - start

# === 向量化 ===
start = time.perf_counter()
result3 = (df['amount'] > df['user_avg_amount'] * 3).astype(int)
t_vec = time.perf_counter() - start

print(f"iterrows: {t_iter:.2f}s ({t_iter/t_vec:.0f}x 慢于向量化)")
print(f"apply:    {t_apply:.2f}s ({t_apply/t_vec:.0f}x 慢于向量化)")
print(f"向量化:   {t_vec:.4f}s (基准)")
print(f"结果一致: {(result1 == result3).all()}")
```

#### 步骤 2：eval / query 加速

**目标**：对比普通布尔索引和 query 的性能。

```python
# step2_eval_query.py
import pandas as pd
import time

df = pd.read_csv('risk_transactions.csv')

# === 普通布尔索引 ===
start = time.perf_counter()
r1 = df[(df['amount'] > 1000) & (df['hour'] >= 8) & (df['hour'] <= 22)]
t_bool = time.perf_counter() - start

# === query ===
start = time.perf_counter()
r2 = df.query('amount > 1000 and 8 <= hour <= 22')
t_query = time.perf_counter() - start

print(f"布尔索引: {t_bool:.3f}s")
print(f"query:    {t_query:.3f}s ({t_bool/t_query:.1f}x)")

# === eval 计算派生列 ===
start = time.perf_counter()
df['risk_score_1'] = (df['amount'] - df['user_avg_amount']) / df['user_avg_amount']
t_normal = time.perf_counter() - start

start = time.perf_counter()
df['risk_score_2'] = df.eval('(amount - user_avg_amount) / user_avg_amount')
t_eval = time.perf_counter() - start

print(f"\n普通计算: {t_normal:.4f}s")
print(f"eval:      {t_eval:.4f}s ({t_normal/t_eval:.1f}x)")
```

#### 步骤 3：np.select 多条件分支

**目标**：用 np.select 替代多个 if-elif 分支。

```python
# step3_np_select.py
import pandas as pd
import numpy as np

df = pd.read_csv('risk_transactions.csv')
df['city_diff'] = (df['city'] != df['prev_city']).fillna(False)

# === 12 条风控规则用 np.select ===
conditions = [
    (df['amount'] > df['user_avg_amount'] * 5),                           # 超高金额
    (df['amount'] > df['user_avg_amount'] * 3) & df['is_new_user'],       # 新用户大额
    (df['amount'] > 2000) & (df['city_diff']),                             # 异地大额
    (df['hour'].between(2, 5)) & (df['amount'] > 1000),                   # 凌晨大额
]
choices = ['规则1:超高金额', '规则2:新用户大额', '规则3:异地大额', '规则4:凌晨大额']

df['risk_rule'] = np.select(conditions, choices, default='正常')
df['risk_score'] = np.select(
    [(c != '正常') for c in df['risk_rule']],
    [80, 60, 50, 40],
    default=0
)

print("===== 风控命中分布 =====")
print(df['risk_rule'].value_counts().to_string())

print(f"\n===== 高风险交易 (score>=60) =====")
high_risk = df[df['risk_score'] >= 60]
print(high_risk[['user_id','amount','risk_rule','risk_score']].head(10).to_string(index=False))
```

#### 步骤 4：where/mask 条件替换替代逐行修改

**目标**：用 where/mask 替代逐行 if-else 修改。

```python
# step4_where_mask.py
import pandas as pd
import numpy as np

df = pd.read_csv('risk_transactions.csv')

# ❌ 逐行修改
# for idx in df.index:
#     if df.loc[idx, 'amount'] < 0:
#         df.loc[idx, 'amount'] = 0

# ✅ 向量化
df['amount'] = df['amount'].where(df['amount'] >= 0, other=0)
# 等价于: df['amount'] = df['amount'].mask(df['amount'] < 0, 0)

# ✅ 多条件链式
df['adjusted_amount'] = (
    df['amount']
    .where(df['amount'] > 0, other=0)      # 负值→0
    .where(df['amount'] <= 50000, other=50000)  # 上限 50000
)
```

### 3.4 常见坑及解决方法

| 问题 | 原因 | 解决方法 |
|------|------|----------|
| eval 中引用外部变量 | eval 作用域不含 Python 变量 | 使用 `@` 前缀: `df.eval('amount > @threshold')` |
| query 中列名含空格 | 列名含空格 query 语法非法 | 用 `df.rename(columns=...)` 或反引号 `\`col name\`` |
| np.select 的 default 不生效 | 所有条件都为 True 时 | 把最严格的条件放前面 |
| 向量化操作中间内存峰值高 | 多个临时 Series 同时存在 | 用 eval 减少中间变量 |

### 3.5 测试验证

```python
# test_ch24.py
import pandas as pd
import numpy as np

def test_vectorized_vs_loop():
    df = pd.DataFrame({'a': [1,2,3,4,5]})
    vec = (df['a'] > 2).sum()
    loop = sum(1 for _, r in df.iterrows() if r['a'] > 2)
    assert vec == loop == 3

def test_np_select():
    df = pd.DataFrame({'v': [10, 50, 100, 200]})
    conds = [df['v'] < 50, df['v'] < 150]
    choices = ['低', '中']
    result = np.select(conds, choices, default='高')
    assert list(result) == ['低','中','中','高']

def test_eval():
    df = pd.DataFrame({'a':[1,2,3], 'b':[4,5,6]})
    result = df.eval('a + b')
    assert result.tolist() == [5,7,9]

def test_query_at():
    threshold = 2
    df = pd.DataFrame({'x': [1,2,3,4]})
    result = df.query('x > @threshold')
    assert len(result) == 2

if __name__ == '__main__':
    test_vectorized_vs_loop(); test_np_select(); test_eval(); test_query_at()
    print("OK 所有测试通过")
```

**完整代码清单**：参见专栏配套仓库 `column/code/ch24/`。

## 4. 项目总结

### 4.1 优点 & 缺点

| 维度 | 向量化/eval | apply | iterrows | SQL |
|------|-----------|-------|----------|-----|
| 单列条件 | 毫秒级 | 秒级 | 十秒级 | 毫秒 |
| 多列复合 | np.select 一次搞定 | apply 逐行 | 逐行 | CASE WHEN |
| 表达式计算 | eval 无中间变量 | 需中间 Series | 逐值计算 | 内置 |
| 可读性 | 中（简洁但需理解） | 高 | 高 | 高 |
| 调试 | 中（一次操作整列） | 高（可打断点） | 高 | 中 |

### 4.2 适用场景

- **适用场景**：
  1. 大数据量（>10 万行）的批量计算和条件判断
  2. 多条件风控/评分/分类规则
  3. 特征工程的批量表达式计算
  4. 需要极致性能的数据管道（毫秒级 SLA）
- **不适用场景**：
  1. <1000 行数据——apply 的可读性优势胜过性能差异
  2. 复杂的逐行状态机逻辑（需 Numba/Cython 加速）

### 4.3 注意事项

- **eval/query 不能完全替代布尔索引**：eval/query 内部用 numexpr，不支持某些 Python 操作（如 `.str.contains`）。当表达式包含字符串方法或复杂 lambda 时，退回到普通布尔索引；
- **向量化的 copy-on-write 行为**：`df['new'] = df['a'] + df['b']` 在 CoW 下不会立即复制，但连续大量创建列仍可能触发多次复制。建议在大量列操作后手动 `df.copy()` 来清理碎片化的内存布局；
- **np.select 的条件顺序**：从前到后依次匹配，第一个命中的条件生效——把最严格/最高风险的条件放最前面；
- **query 中变量引用`@`的坑**：`df.query('amount > @threshold')` 中 threshold 必须是 Python 标量或列表，不能是 pandas Series。如果 threshold 是一个 Series 的某个值，需要先 `.item()` 取出标量；
- **eval 不支持赋值**：`df.eval('new_col = a + b')` 在较新版本 pandas 中已弃用。用 `df.assign(new_col=df.eval('a+b'))` 替代；
- **向量化操作的内存峰值**：`df['a'] + df['b'] + df['c'] + df['d']` 会产生 3 个中间临时 Series（a+b, +c, +d），在 1 亿行数据上内存峰值可能达到正常内存的 4 倍。对于超大数据集，考虑分列逐步计算或使用 `eval` 减少中间内存分配；
- **常用加速模式汇总**：

| 原始写法 | 优化写法 | 加速比 |
|---------|---------|--------|
| `iterrows()` | 向量化 `df['col'] > x` | 50-100x |
| `apply(func, axis=1)` | `np.select(conditions, choices)` | 10-30x |
| `df[(df['a']>x) & (df['b']<y)]` | `df.query('a > @x and b < @y')` | 2-5x |
| `df['col'].map(dict_lookup)` | `df['col'].replace(dict_lookup)` | 1.5-2x |
| `pd.to_datetime(df['date'])` | `read_csv(parse_dates=['date'])` | 1.5-3x |
| `pd.concat([df]*1000)` | `pd.DataFrame(np.tile(...))` | 5-10x |

### 4.4 常见踩坑经验

1. **`query` 中 and/or 和 &/| 混用**：`query` 内部用 `and`/`or` 语义，不是 `&`/`|`。`df.query('a > 1 and b < 5')` 是对的，`df.query('a > 1 & b < 5')` 会报错。
2. **向量化后结果"不对"但又不报错**：`df['a'] > df['b']` 如果两列 Index 不对齐——pandas 会自动按 Index 对齐，结果可能和预期完全不同。检查 Index 是否一致。
3. **np.select 的 choices 和 conditions 长度不一致不报错**：多余的 choices 被忽略，缺少的 choice 用 default——不会抛异常，结果静默错误。

### 4.5 思考题

1. `df['col'].map(dict_lookup)` 和 `df['col'].replace(dict_lookup)` 在性能和语义上有什么差异？什么时候用 map 什么时候用 replace？
2. `pd.cut` 和 `np.digitize` 都可以做分桶，底层实现有什么区别？哪个在大数据量下更快？

（答案将在第 25 章附录中给出）

### 4.6 推广计划提示

- **所有开发者**：制定团队的"性能编码规范"——禁止对大数据集使用 iterrows；
- **数据分析师**：掌握 %timeit 和 %memit，养成"先度量再优化"的习惯；
- **架构师**：将 eval/query 纳入代码审查的优化建议列表。

---

> **源码关联**：pandas/core/computation/eval.py、pandas/core/computation/expressions.py
