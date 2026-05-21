# 第33章：Index 引擎与对齐机制源码剖析

## 1. 项目背景

某量化交易团队的技术负责人老侯发现，他们的回测系统中有一个隐秘的性能瓶颈——两个大型 DataFrame 的合并操作（`df1 + df2`）在股票数量增加到 5000 只后突然变慢，从 0.5 秒飙升到 8 秒。排查后发现：5000 只股票的 Index 是一个 `object` 类型的 Index（股票代码为字符串），而只有 500 只时用的是 `RangeIndex`（整数），两者的对齐（alignment）性能差距巨大。

**痛点一：不知道不同 Index 类型的性能差异**。`RangeIndex`、`Int64Index`、`DatetimeIndex`、`MultiIndex`——它们的底层查找算法完全不同。老侯用了 3 年 pandas 都不知道这些，直到性能出了问题才去深究。

**痛点二：`df.reindex()` 在某些场景下很慢**。老侯在回测中需要把 5000 只股票的日线数据对齐到统一的交易日历——用 `df.reindex(trading_days)` 需要 3 秒。后来发现用 `df.loc[trading_days]` 只需要 0.3 秒——两者的底层路径完全不同。

**痛点三：不理解 Index 的不可变性的代价**。老侯写了一个函数频繁 `df.index.append(new_item)`——每次 append 都创建了一个新的 Index 对象。5000 次循环产生了 5000 个临时 Index，GC 压力巨大。

痛点流程：

```
5000只股票 × 244个交易日
  ├── df1 + df2 → Index 对齐 → 从 0.5s 飙升到 8s
  ├── reindex vs loc → 3s vs 0.3s → 不知道区别
  └── index.append 频繁调用 → 5000 个临时对象 → GC 压力
```

本章将深入到 `pandas/core/indexes/` 目录，剖析 Index 的不可变语义、查找引擎（哈希表 vs 二分查找 vs 切片）、reindex/align 的底层流程，以及不同类型 Index 的性能特征。

## 2. 项目设计：剧本式交锋对话

> 场景：老侯在周会上分享了"索引类型对性能影响 10 倍"的发现，小胖惊呆了。

**小胖**：（瞪大了眼睛）"同一个 DataFrame 加法，500 只股票 0.5 秒，5000 只股票 8 秒——这难道不是数据量大了必然的吗？数据多了 10 倍，时间多了 16 倍也正常吧？"

**大师**："数据量从 500 到 5000 增长了 10 倍，但时间从 0.5 到 8 秒增长了 16 倍——**这不是线性的**。这种超线性增长说明算法复杂度不是 O(n) 而是接近 O(n^2)。根源在于 Index 的查找引擎：

- `RangeIndex`（整数序列）：通过简单的数学公式定位，O(1)
- `Int64Index` / `Float64Index`：内部用哈希表，O(1) 平均
- `DatetimeIndex`：如果单调递增，用二分查找 O(log n)；否则用哈希表
- `object` Index（字符串）：哈希表，但哈希字符串本身的开销比整数大

老侯的 5000 只股票代码是字符串 Index（object），对齐时对每个标签做哈希查找——5000 次字符串哈希 + 5000 次哈希表查找。当数据量增长时，哈希冲突也增多，性能就超线性恶化了。"

**【技术映射：Index 查找引擎 = 图书馆找书——索书号（RangeIndex）= 直接走到对应书架 O(1)，书名（object Index）= 查目录卡 O(1) 平均但每张卡得读完】**

**小白**："那 `reindex` 和 `loc` 为什么性能差 10 倍？它们不都是按 Index 取数据吗？"

**大师**："这是 pandas 源码中最精妙的设计之一。`reindex` 和 `loc` 虽然结果可能相同，但底层路径完全不同：

- **`reindex(new_index)`**：通用的索引重建。过程 = 对 new_index 中的每个标签，在旧 Index 中查找位置 → 构建一个新的 BlockManager → 把数据从旧位置拷到新位置。这是一个"全量重建"过程，涉及内存分配和数据拷贝。

- **`loc[new_index]`**：索引切片。如果 current Index 是**唯一且已排序的**，loc 内部调用 `searchsorted`（二分查找），只返回视图或轻量拷贝。如果 Index 是 `RangeIndex`，loc 直接用整数运算定位——几乎是零开销。

关键差异：`reindex` 假设新旧 Index 完全不同，需要重建一切；`loc` 假设你只是要取子集，尽量共享内存。"

**【技术映射：reindex = 把整个书架按新编号重新排一遍（全量重建），loc = 按编号取出几本需要的书（局部操作）】**

**小胖**："那 Index 的不可变性呢？为什么不能直接 `df.index[0] = 'new_label'`？"

**大师**："Index 被设计为**不可变对象**——一旦创建就不能修改。原因有三：

1. **哈希一致性**：Index 内部有哈希缓存（`_cache`），如果允许修改标签，缓存的哈希值就失效了——下次查找会出错。
2. **多 DataFrame 共享**：两个 DataFrame 可能共享同一个 Index 对象——如果其中一个改了 Index，另一个会受影响。
3. **线程安全**：不可变对象天然线程安全，不需要锁。

正确的做法：`df.index = new_index` ——替换整个 Index 对象，而不是修改其中的元素。"

**【技术映射：Index 不可变 = 身份证号——可以换一张新证（新 Index），但不能在原证上涂改（修改元素）】**

**大师总结**："Index 引擎的三个核心洞察：
1. 查找算法取决于 Index 类型——RangeIndex 最快（O(1) 数学运算），object 最慢（字符串哈希）
2. `loc` 优先于 `reindex`——前者尽量共享内存，后者总是重建
3. Index 不可变是设计基石——频繁修改 Index 的代码需要重构

理解这些，你就能解释为什么同样的操作在不同 Index 上差 10 倍性能。"

## 3. 项目实战

### 3.1 准备

```bash
pip install pandas numpy
```

### 3.2 探索 Index 体系

```python
# step1_index_types.py
import pandas as pd
import numpy as np

# === 不同类型的 Index 及查找引擎 ===
indexes = {
    'RangeIndex': pd.RangeIndex(0, 1000000),
    'Int64Index': pd.Index(np.arange(1000000)),
    'Float64Index': pd.Index(np.arange(1000000, dtype=float)),
    'DatetimeIndex': pd.date_range('2025-01-01', periods=1000000, freq='1min'),
    'object_Index': pd.Index([f'STOCK_{i:06d}' for i in range(1000000)]),
}

for name, idx in indexes.items():
    print(f"{name}: type={type(idx).__name__}, 是否单调={idx.is_monotonic_increasing}, "
          f"是否唯一={idx.is_unique}, 内存={idx.memory_usage(deep=True)//1024}KB")
```

### 3.3 分步实现

#### 步骤 1：Index 查找性能对比

**目标**：对比不同类型 Index 的 `get_loc` 性能。

```python
# step2_get_loc_benchmark.py
import pandas as pd
import numpy as np
import time

N = 500_000

# 构建不同类型的 Index
idx_range = pd.RangeIndex(0, N)
idx_int = pd.Index(np.arange(N))
idx_obj = pd.Index([f'STOCK_{i:06d}' for i in range(N)])

# 查找性能测试
target_range = N - 1
target_obj = f'STOCK_{N-1:06d}'

for name, idx, target in [('RangeIndex', idx_range, target_range),
                           ('Int64Index', idx_int, target_range),
                           ('object', idx_obj, target_obj)]:
    start = time.perf_counter()
    loc = idx.get_loc(target)
    t = time.perf_counter() - start
    print(f"{name}.get_loc: {t*1000:.4f}ms, loc={loc}")

# 批量查找性能
print("\n批量查找 (1000 个随机标签):")
targets_range = np.random.randint(0, N, 1000)
targets_obj = [f'STOCK_{i:06d}' for i in np.random.randint(0, N, 1000)]

for name, idx, targets in [('RangeIndex', idx_range, targets_range),
                            ('object', idx_obj, targets_obj)]:
    start = time.perf_counter()
    locs = idx.get_indexer(targets)
    t = time.perf_counter() - start
    print(f"{name}.get_indexer (1000次): {t*1000:.2f}ms")
```

#### 步骤 2：reindex vs loc 路径对比

**目标**：理解 reindex 和 loc 的底层差异。

```python
# step3_reindex_vs_loc.py
import pandas as pd
import numpy as np
import time

N = 100_000
df = pd.DataFrame({
    'value': np.random.randn(N)
}, index=pd.date_range('2025-01-01', periods=N, freq='1min'))

# 取子集：每隔 5 分钟取一条
new_idx = pd.date_range('2025-01-01', '2025-01-05', freq='5min')

# reindex
start = time.perf_counter()
r1 = df.reindex(new_idx)
t_reindex = time.perf_counter() - start

# loc（更高效）
start = time.perf_counter()
r2 = df.loc[df.index.intersection(new_idx)]
# 或者如果 new_idx 是 df.index 的子集
r3 = df.loc[new_idx[:max(df.index.get_indexer(new_idx[new_idx <= df.index.max()]))]]
t_loc = time.perf_counter() - start

print(f"reindex: {t_reindex:.4f}s")
print(f"loc:     {t_loc:.4f}s ({t_reindex/t_loc:.1f}x 更快)")
```

#### 步骤 3：对齐机制的性能实验

**目标**：构造不同 Index 类型下对齐操作的性能对比。

```python
# step4_alignment_perf.py
import pandas as pd
import numpy as np
import time

N = 50_000

# 实验 1：RangeIndex vs object Index 的对齐
s1_range = pd.Series(np.random.randn(N))
s2_range = pd.Series(np.random.randn(N))

s1_obj = pd.Series(np.random.randn(N), index=[f'S{i:06d}' for i in range(N)])
s2_obj = pd.Series(np.random.randn(N), index=[f'S{i:06d}' for i in range(N)])

# RangeIndex 对齐
start = time.perf_counter()
_ = s1_range + s2_range
t_range = time.perf_counter() - start

# object Index 对齐
start = time.perf_counter()
_ = s1_obj + s2_obj
t_obj = time.perf_counter() - start

print(f"RangeIndex 加法: {t_range*1000:.2f}ms")
print(f"object Index 加法: {t_obj*1000:.2f}ms ({t_obj/t_range:.1f}x 慢)")

# 实验 2：Index 不匹配时的对齐开销
s2_mismatch = pd.Series(np.random.randn(N), index=range(N//2, N + N//2))
start = time.perf_counter()
_ = s1_range + s2_mismatch
t_mismatch = time.perf_counter() - start
print(f"Index 不匹配加法: {t_mismatch*1000:.2f}ms")
```

#### 步骤 4：Index 不可变性的实验

**目标**：验证 Index 的不可变性及其对性能的影响。

```python
# step5_immutable.py
import pandas as pd
import numpy as np
import time

# === 验证不可变性 ===
idx = pd.Index([1, 2, 3])
try:
    idx[0] = 10
except TypeError as e:
    print(f"Index 不可变: {e}")

# === 频繁修改 Index 的性能陷阱（反例） ===
# 反例：循环中 append
new_items = range(1000)
start = time.perf_counter()
idx = pd.Index([])
for item in new_items:
    idx = idx.append(pd.Index([item]))  # 每次创建新 Index
t_bad = time.perf_counter() - start

# 正例：收集列表，一次性创建
start = time.perf_counter()
idx_good = pd.Index(list(new_items))
t_good = time.perf_counter() - start

print(f"\n逐次 append: {t_bad*1000:.2f}ms")
print(f"一次性创建: {t_good*1000:.4f}ms ({t_bad/t_good:.0f}x 更快)")
```

### 3.5 常见坑及解决方法

| 问题 | 原因 | 解决方法 |
|------|------|----------|
| `get_loc` 对重复 Index 返回布尔数组 | 重复标签有多个位置 | 先用 `df.index.is_unique` 检查 |
| `reindex` 产生大量 NaN | 新 Index 中有旧 Index 没有的值 | 用 `method='ffill'` 填充 |
| object Index 的 DataFrame 合并很慢 | 每次比较都需要字符串比较 | 转为 category Index（底层是整数编码）|

### 3.6 测试验证

```python
# test_ch33.py
import pandas as pd
import numpy as np

def test_range_index_o1():
    idx = pd.RangeIndex(0, 1000000)
    loc = idx.get_loc(500000)
    assert loc == 500000

def test_index_immutable():
    idx = pd.Index([1,2,3])
    try: idx[0] = 10; assert False
    except TypeError: pass

def test_alignment_nan():
    s1 = pd.Series([1,2], index=['a','b'])
    s2 = pd.Series([3,4], index=['b','c'])
    result = s1 + s2
    assert result['a'] != result['a']  # NaN

if __name__ == '__main__':
    test_range_index_o1(); test_index_immutable(); test_alignment_nan()
    print("OK 所有测试通过")
```

**完整代码清单**：参见专栏配套仓库 `column/code/ch33/`。

## 4. 项目总结

### 4.1 优点 & 缺点

| 维度 | RangeIndex | Int64Index | DatetimeIndex | object Index |
|------|-----------|-----------|--------------|-------------|
| 查找速度 | O(1) 数学运算 | O(1) 哈希 | O(log n) 二分 | O(1) 字符串哈希 |
| 内存占用 | 极低（只存 start/stop/step） | 低（整数数组） | 低 | 高（Python 对象） |
| 切片能力 | 极快 | 快 | 快（日期切片） | 中 |
| 对齐性能 | 极快 | 快 | 快 | 慢 |

### 4.2 Index 引擎的深层机制

**DatetimeIndex 的特殊优化**：DatetimeIndex 内部存储的是 `int64` 时间戳（自 epoch 以来的纳秒数），而非 Python datetime 对象。这意味着日期比较和日期运算都是整数运算——不需要 Python 对象参与。当你用 `df.loc['2025-01':'2025-03']` 做日期切片时，pandas 先把字符串 `'2025-01'` 转为纳秒整数，然后做整数二分查找——这就是为什么 DatetimeIndex 的切片和查找几乎是零开销的。

**MultiIndex 的 get_loc 复杂度分析**：MultiIndex 的每一层可以有不同的查找引擎。`get_loc` 在 MultiIndex 上的实现是逐层下钻：
1. 第一层（如 "华东"）：在第一层的 Index 上 get_loc → 得到位置范围
2. 第二层（如 "上海"）：在缩小的范围内，在第二层的 Index 上 get_loc
3. 以此类推……

如果每层都是排序的且唯一的，整体复杂度是 O(k × log n)，其中 k 是层数。如果某一层使用了哈希表，复杂度可以降到 O(k)。但最坏情况下（所有层都是未排序的字符串），复杂度接近 O(k × n)。

**`_cache` 机制**：Index 内部有一个 `_cache` 字典，缓存 `get_loc` 的结果哈希表。当你第一次在某个 Index 上做标签查找时，pandas 在后台构建一个哈希映射（`{label: position}`），存入 `_cache`。后续相同标签的查找直接从哈希表取——O(1) 而非 O(log n) 或 O(n)。缓存会在 Index 被修改（如 append/delete）时自动失效，但因为 Index 是不可变的——实际上缓存稳定性很高。

**`get_indexer` 的批量优化**：当 `target` 和当前 Index 类型相同且都是排序的时，`get_indexer` 使用**归并算法**——同时遍历两个有序数组，时间复杂度 O(n+m)。这比循环调用 `get_loc` 快 10-100 倍。但如果 Index 类型不同（如 DatetimeIndex 查找 Int64Index），会回退到逐个 `get_loc` 调用。

### 4.2 适用场景

- **适用场景**：默认用 RangeIndex、时间数据用 DatetimeIndex、低基数字符串用 category Index、需要快速查找用排序后的 Index
- **不适用场景**：不需要查找的纯数值矩阵——直接用 NumPy

### 4.3 注意事项

- **排序后的 Index 才能用 searchsorted**：`df.sort_index()` 后再用 loc 能触发二分查找加速；
- **`get_indexer` 比 `get_loc` 循环快**：批量查找用 `get_indexer`，内部有向量化优化；
- **不要频繁 rebuild Index**：设计时尽量一次构建 Index，而非逐行 append。

### 4.4 常见踩坑经验

1. **`pd.Index` 和 `pd.RangeIndex` 混用导致性能降级**：`pd.date_range` 返回 DatetimeIndex，但 `df.reset_index()` 可能把它变成 Int64Index。
2. **用 `df.index = df.index + 1` 创建了新 Index**：看似就地修改，实际是新对象——频繁操作有性能开销。
3. **MultiIndex 的 `get_loc` 复杂度随层级增长**：4 层 MultiIndex 的 get_loc 可能比单层慢 5 倍。

### 4.5 思考题

1. 如果有一个 1000 万行的 DataFrame，Index 是未排序的字符串，如何将它转换为一个查找更高效的 Index 类型？
2. `df.index.get_indexer(targets)` 返回 -1 表示什么？如何在代码中优雅处理找不到的标签？

（答案将在第 34 章附录中给出）

### 4.6 推广计划提示

- **性能调优**：排查 DataFrame 操作慢时，先检查 Index 类型和是否排序；
- **开发规范**：避免在生产代码中使用字符串 Index（object），优先用 category 或整数编码。

---

> **源码关联**：pandas/core/indexes/base.py、pandas/core/indexes/range.py、pandas/core/indexes/multi.py
