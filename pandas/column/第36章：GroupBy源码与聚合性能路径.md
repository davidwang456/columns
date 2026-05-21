# 第36章：GroupBy 源码与聚合性能路径

## 1. 项目背景

某广告平台的数据工程师老董维护着一套"用户标签聚合系统"，每天凌晨对前一天 5000 万条广告曝光日志做 GroupBy 聚合（按用户 × 广告位分组，计算曝光次数、点击次数、平均 CTR）。这个任务在 32 核机器上需要 12 分钟——远超 SLA 的 5 分钟。

老董已经做了第 7 章的 dtype 优化（int→int8，object→category）和第 24 章的向量化调优，但 12 分钟仍然太慢。他怀疑 GroupBy 操作本身是瓶颈，但不知道从哪里深入优化。

**痛点一：同样的 groupby sum，在不同 dtype 上性能差距巨大**。`groupby('user_id').amount.sum()`——当 `user_id` 是 int64 时耗时 3 秒，是 category 时耗时 0.8 秒，是 object 时耗时 8 秒。老董不知道为什么。

**痛点二：`agg('sum')`、`agg(np.sum)`、`agg(lambda x: x.sum())` 性能差异悬殊**。同样的求和操作，传字符串 `'sum'` 比传 lambda 快 50 倍。老董不知道为什么——看起来都是求和啊？

**痛点三：`observed=True` 和 `observed=False` 对 category 列 GroupBy 的结果和性能有微妙影响**。老董有一次切换了这个参数后，输出行数从 50 行变成了 200 行，且慢了 5 倍。

痛点流程：

```
5000万行 GroupBy 聚合
  ├── user_id dtype → 不同性能 → 不知道为什么
  ├── agg('sum') vs agg(lambda) → 50倍差距 → 不知道为什么
  └── observed=False → 行数暴增+变慢 → 不知道为什么
```

本章将深入 GroupBy 源码——Grouper/codes/levels 的构建、Cython 聚合路径 vs Python fallback、以及 agg/transform/apply 三种执行模式的调用链差异。

## 2. 项目设计：剧本式交锋对话

> 场景：老董在代码评审上展示了"agg('sum') 比 agg(lambda) 快 50 倍"的 benchmark，小胖震惊。

**小胖**：（捶着桌子）"不可能！`'sum'` 和 `lambda x: x.sum()` 都是求和——为什么差 50 倍？pandas 是不是有 Bug？"

**大师**："不是 Bug，是**执行路径的选择**。pandas 的 GroupBy 有两种聚合引擎：

1. **Cython 引擎**：当 aggfunc 是内置名称（`'sum'`、`'mean'`、`'std'` 等约 30 个函数）时，pandas 走 Cython 路径——在 C 层直接遍历分组数据，没有 Python 函数调用开销。
2. **Python fallback 引擎**：当 aggfunc 是 lambda/自定义函数时，pandas 必须在 Python 层调用你的函数——每个分组一次。如果 10 万个分组 = 10 万次 Python 函数调用 = 极慢。

`'sum'` 和 `np.sum` 虽然语义相同，但前者被 pandas 识别为内置、走 Cython；后者是一个 Python 对象——pandas 不知道它的内部行为，只能走 Python fallback。"

**【技术映射：内置 aggfunc = 食堂的标准化套餐（提前做好，拿了就走），lambda = 现点现做（每个菜单独炒，等待时间长）】**

**小白**："那 category 列为什么比 int64 列快？category 不是还要查映射表吗？"

**大师**："这恰恰是 category 的优势——**分组键用整数编码**。GroupBy 的第一步是构建 Grouper，核心产物是 `codes`（整数数组）和 `levels`（类别名）。关键在于：

- **int64 分组的 codes**：每个值直接作为分组标签 → codes 就是数据本身
- **category 分组的 codes**：每个值是 0/1/2...的整数编码 → codes 也是整数，但唯一值少
- **object 分组的 codes**：需要对每个字符串做哈希 → 构建哈希表 + 查找

category 分组的 codes 是**低基数的密集整数**（如 0-49），在 Cython 引擎中做 `sum` 时，数据被分到 50 个桶中——桶数少，缓存命中率高。int64 分组的 codes 是稀疏的（如 user_id 可能是 10000~99999），桶数多，缓存局部性差——这就是为什么 category 比 int64 快。"

**【技术映射：category 分组 = 50 个颜色桶（物品直接扔对应颜色），int64 分组 = 10000 个编号桶（桶太多，挨个找）】**

**小胖**："那 `observed=True/False` 呢？怎么还能影响行数？"

**大师**："这是 category 独有的行为。category 有一个 `categories` 属性——定义了所有可能的类别值（即使数据中没出现）。

- **`observed=True`**：结果只包含数据中实际出现的类别组合
- **`observed=False`**（默认）：结果包含 `categories` 中所有类别 × 多层分组的笛卡尔积——即使某些组合没有数据（值为 0）

例如你的 `city` category 定义了 50 个城市，但今天只有 30 个城市有交易。`observed=False` 会输出 50 行（20 行全 0），`observed=True` 只输出 30 行。**大量空行不仅让结果膨胀，还增加了聚合计算量。**"

**大师总结**："GroupBy 性能的三层加速：
1. **dtype 层面**：category 优于 int64 优于 object（分组键的编码效率）
2. **aggfunc 层面**：内置字符串名优于 NumPy 函数优于 lambda（Cython vs Python）
3. **参数层面**：`observed=True` 优于 `observed=False`（减少空桶计算）

记住：**`df.groupby('cat_col', observed=True).agg('sum')` 是最快的 GroupBy 写法。**"

## 3. 项目实战

### 3.1 准备

```bash
pip install pandas numpy
```

### 3.2 模拟数据

```python
# generate_groupby_data.py
import pandas as pd
import numpy as np

np.random.seed(42)
N = 2_000_000

df = pd.DataFrame({
    'user_id': np.random.randint(10000, 50000, N),
    'product_cat': np.random.choice(['A','B','C','D','E','F','G','H','I','J'], N),
    'city': np.random.choice([f'城市{i:02d}' for i in range(50)], N),
    'amount': np.random.exponential(100, N).round(2),
    'qty': np.random.randint(1, 5, N).astype('int8'),
})
df.to_csv('transactions_large.csv', index=False)
print(f"已生成 {N:,} 条数据")
```

### 3.3 分步实现

#### 步骤 1：dtype 对 GroupBy 性能的影响

```python
# step1_dtype_impact.py
import pandas as pd
import time

df = pd.read_csv('transactions_large.csv')

# 测试不同 dtype 的 groupby sum 性能
dtype_tests = {}

# int64 分组键
start = time.perf_counter()
r1 = df.groupby('user_id')['amount'].sum()
dtype_tests['int64'] = time.perf_counter() - start

# category 分组键
df['user_id_cat'] = df['user_id'].astype('category')
start = time.perf_counter()
r2 = df.groupby('user_id_cat', observed=True)['amount'].sum()
dtype_tests['category'] = time.perf_counter() - start

# object 分组键（转为字符串）
df['user_id_str'] = df['user_id'].astype(str)
start = time.perf_counter()
r3 = df.groupby('user_id_str')['amount'].sum()
dtype_tests['object'] = time.perf_counter() - start

print("===== 不同 dtype GroupBy 性能 =====")
for dtype, t in dtype_tests.items():
    print(f"  {dtype}: {t:.3f}s")
print(f"  category 比 int64 快 {dtype_tests['int64']/dtype_tests['category']:.1f}x")
print(f"  category 比 object 快 {dtype_tests['object']/dtype_tests['category']:.1f}x")
```

#### 步骤 2：aggfunc 路径对比——Cython vs Python

```python
# step2_aggfunc_paths.py
import pandas as pd
import time

df = pd.read_csv('transactions_large.csv')
df['product_cat'] = df['product_cat'].astype('category')

# === 内置字符串 (Cython 路径 ) ===
start = time.perf_counter()
r1 = df.groupby('product_cat', observed=True).agg(
    total=('amount','sum'),
    avg=('amount','mean'),
    cnt=('amount','count'),
)
t_cython = time.perf_counter() - start

# === NumPy 函数 (Python fallback) ===
start = time.perf_counter()
r2 = df.groupby('product_cat', observed=True).agg(
    total=('amount', np.sum),
    avg=('amount', np.mean),
    cnt=('amount', 'count'),
)
t_numpy = time.perf_counter() - start

# === Lambda (Python fallback) ===
start = time.perf_counter()
r3 = df.groupby('product_cat', observed=True).agg(
    total=('amount', lambda x: x.sum()),
)
t_lambda = time.perf_counter() - start

print("===== aggfunc 路径性能对比 =====")
print(f"  Cython (字符串): {t_cython:.3f}s")
print(f"  NumPy 函数:      {t_numpy:.3f}s ({t_numpy/t_cython:.1f}x 慢)")
print(f"  Lambda:          {t_lambda:.3f}s ({t_lambda/t_cython:.1f}x 慢)")
```

#### 步骤 3：observed=True/False 对 category 的影响

```python
# step3_observed_impact.py
import pandas as pd
import time

df = pd.read_csv('transactions_large.csv')
df['city'] = df['city'].astype('category')
df['product_cat'] = df['product_cat'].astype('category')

# observed=True
start = time.perf_counter()
r_true = df.groupby(['city','product_cat'], observed=True)['amount'].sum()
t_true = time.perf_counter() - start

# observed=False
start = time.perf_counter()
r_false = df.groupby(['city','product_cat'], observed=False)['amount'].sum()
t_false = time.perf_counter() - start

print(f"observed=True:  {len(r_true)} 行, {t_true:.3f}s")
print(f"observed=False: {len(r_false)} 行, {t_false:.3f}s")
print(f"observed=False 膨胀了 {len(r_false)-len(r_true)} 行 ({(len(r_false)/len(r_true)-1)*100:.0f}%)")
```

#### 步骤 4：agg/transform/apply 执行路径追踪

```python
# step4_execution_paths.py
import pandas as pd
import numpy as np
import time

df = pd.read_csv('transactions_large.csv')
df['product_cat'] = df['product_cat'].astype('category')

# agg (Cython 路径)
start = time.perf_counter()
agg_result = df.groupby('product_cat', observed=True)['amount'].agg('sum')
t_agg = time.perf_counter() - start

# transform (Cython 路径——广播回原始长度)
start = time.perf_counter()
trans_result = df.groupby('product_cat', observed=True)['amount'].transform('sum')
t_trans = time.perf_counter() - start

# apply (Python 路径——最慢)
start = time.perf_counter()
apply_result = df.groupby('product_cat', observed=True)['amount'].apply(lambda x: x.sum())
t_apply = time.perf_counter() - start

print(f"agg (Cython):       {t_agg:.3f}s, 结果长度={len(agg_result)}")
print(f"transform (Cython): {t_trans:.3f}s, 结果长度={len(trans_result)}")
print(f"apply (Python):     {t_apply:.3f}s, 结果长度={len(apply_result)}")
print(f"apply 比 agg 慢 {t_apply/t_agg:.0f}x")
```

### 3.4 常见坑及解决方法

| 问题 | 原因 | 解决方法 |
|------|------|----------|
| object 列 GroupBy 极慢 | 每次比较需要字符串哈希 | 转为 category 或编码为整数 |
| agg 用了 `np.sum` 以为和 `'sum'` 一样 | np.sum 走 Python fallback | 用字符串 `'sum'` |
| observed=False 导致内存不足 | category 笛卡尔积组合爆炸 | 始终使用 `observed=True` |

### 3.5 测试验证

```python
# test_ch36.py
import pandas as pd
import numpy as np

def test_cython_sum_faster():
    df = pd.DataFrame({'g': ['a','a','b','b']*2500, 'v': range(10000)})
    df['g'] = df['g'].astype('category')
    # agg('sum') 走 Cython 路径
    r = df.groupby('g', observed=True)['v'].sum()
    assert r['a'] + r['b'] == sum(range(10000))

def test_observed_difference():
    df = pd.DataFrame({'g': pd.Categorical(['a','a','b'], categories=['a','b','c']), 'v': [1,2,3]})
    r_true = df.groupby('g', observed=True)['v'].sum()
    r_false = df.groupby('g', observed=False)['v'].sum()
    assert len(r_true) == 2  # 只有 a, b
    assert len(r_false) == 3  # a, b, c (c 为 0)

if __name__ == '__main__':
    test_cython_sum_faster(); test_observed_difference()
    print("OK 所有测试通过")
```

**完整代码清单**：参见专栏配套仓库 `column/code/ch36/`。

## 4. 项目总结

### 4.1 优点 & 缺点

| 维度 | Cython 路径 | Python fallback | SQL GROUP BY |
|------|-----------|---------------|-------------|
| 内置聚合 | 极快 (sum/mean/std) | 快（np.xxx） | 极快 |
| 自定义聚合 | 不支持 | 支持但慢 | 有限 |
| category 优化 | 编码加速 | 编码加速 | 无此类型 |
| 调试 | 无法打断点 | 可打断点 | EXPLAIN |

### 4.2 深入：GroupBy 的 Grouper 构建流程

当执行 `df.groupby('key')` 时，pandas 在 `pandas/core/groupby/grouper.py` 中构建 Grouper 对象，核心流程如下：

1. **因子化 (Factorize)**：调用 `pd.factorize(df['key'])`，将分组键转为整数 codes（0, 1, 2...）和 levels（原始标签）。这是整个 GroupBy 的性能基础——后续所有分组操作都在整数 codes 上进行，而非原始的字符串/日期比较。

2. **分组映射 (Grouping Map)**：构建 `codes → row positions` 的映射表。对于 Cython 路径，这一步构建的是 `(group_start, group_end)` 切片对——每个分组对应一个连续的整数范围。对于 Python 路径，构建的是 `label → Index` 的字典。

3. **聚合选择 (Aggregation Selection)**：根据 aggfunc 字符串（如 'sum'）在 `_cython_agg_general` 注册表中查找对应的 Cython 函数指针。如果找不到，回退到 Python fallback。

**为什么 category 比 int64 快？** category 的 codes 是**密集排列**的（0, 1, 2, ..., K-1），而 int64 的 codes 可以是任意 64 位整数。Cython 引擎在处理密集 codes 时，可以直接用 codes 值作为输出数组的索引——O(1) 的桶定位。而稀疏 codes 需要先做 codes 到桶编号的映射——多了一层间接访问。这就是"编码密度"对性能的影响。

**内置聚合函数的完整列表**：在 `pandas/core/groupby/ops.py` 中，`_cython_agg_general` 注册了以下函数名：`sum`, `prod`, `min`, `max`, `mean`, `median`, `var`, `std`, `sem`, `first`, `last`, `ohlc`, `any`, `all`, `count`, `size`, `nunique`。用这些字符串调用 `agg()` 都走 Cython 路径。

### 4.2 适用场景

- **适用场景**：大数据量 GroupBy 优化、需要理解聚合性能瓶颈
- **不适用场景**：小数据量（< 1 万行）优化收益不明显

### 4.3 注意事项

- **category 列的 observed 默认是 False**：每次 groupby 都必须显式 `observed=True`；
- **transform 和 agg 都走 Cython 路径**：两者性能相近，差异仅在结果维度；
- **apply 的第一组会被调用两次**：这是 pandas 的内部优化（用于推断返回类型），不要依赖副作用。

### 4.4 常见踩坑经验

1. **用 `agg(np.sum)` 比 `agg('sum')` 慢 50 倍但从不自知**：所有"看起来正常"的代码都隐藏了这个陷阱——批量替换为字符串形式。
2. **category + observed=False 导致千万行 0 值行**：`df.groupby(['city','product','channel'], observed=False)` 三个 category 列的笛卡尔积可能有几十万行。
3. **transform('sum') 和 agg('sum') 结果虽然不同维度但性能几乎相同**：都走 cython 路径。
4. **groupby 中 `as_index=True` 默认为 True——结果变成 MultiIndex**：很多人没意识到这个默认值，后续操作用 `reset_index()` 补齐。直接用 `as_index=False` 省一步。
5. **groupby 后的 `head/tail` 不是真正取每个组的前 N 条**：`df.groupby('g').head(3)` 内部实现是先 groupby 再对每组取前 3 行——如果数据量大且组很多，性能很差。替代：用 `cumcount` + 布尔筛选（第 18 章/第 26 章）。

### 4.5 思考题

1. 内置聚合函数除了 `sum`/`mean`/`std` 等，还有哪些？查阅 pandas 源码中 `_cython_agg_general` 的注册表。
2. `groupby.rolling` 和先 groupby 再 rolling 的执行顺序有什么不同？哪个更快？

（答案将在第 37 章附录中给出）

### 4.6 推广计划提示

- **所有开发者**：将"内置字符串 aggfunc + category + observed=True"写入团队性能编码规范。

---

> **源码关联**：pandas/core/groupby/grouper.py、pandas/core/groupby/ops.py、pandas/core/groupby/generic.py
