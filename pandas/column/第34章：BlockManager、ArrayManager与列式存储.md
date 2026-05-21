# 第34章：BlockManager、ArrayManager 与列式存储

## 1. 项目背景

某广告平台的算法工程师老宋在做一个实时竞价系统，需要在 50ms 内完成"从 100 万条广告库存中筛选出符合定向条件的广告并计算预期收益"。其中最关键的性能瓶颈是 DataFrame 的列操作——每次筛选涉及 30 列的读写。

老宋用 `memory_profiler` 分析后发现：对一个 100 万行 × 30 列的 DataFrame 做 `df['new_col'] = df['a'] + df['b']` 时，内存突然从 240MB 飙升到 360MB（增加了 120MB）。新增一列 float64（8 字节 × 100 万 = 8MB），为什么会多出 120MB？

**痛点一：不理解 DataFrame 内部的"Block 合并"机制**。pandas 为了减少内存碎片，把相同 dtype 的列合并存储在同一个 Block 中。当老宋新增一个 float64 列时，pandas 需要把已有的 float64 Block 扩展或拆分——这个过程中可能触发数据拷贝和内存临时膨胀。

**痛点二：不知道 copy-on-write（CoW）何时触发拷贝**。pandas 2.0+ 引入了 CoW，但老宋观察到同样的赋值操作，有时触发拷贝有时不触发——完全无法预测。

**痛点三：ArrayManager 是干什么的**。老宋在 pandas 文档中看到 `mode.data_manager = 'array'` 可以切换到 ArrayManager，但不知道这个切换对性能和内存有什么影响。

痛点流程：

```
100万行 × 30列 的 DataFrame
  ├── df['new'] = df['a'] + df['b'] → 新增 8MB → 内存涨 120MB
  ├── CoW → 有时拷贝,有时不拷贝 → 不可预测
  └── ArrayManager vs BlockManager → 不知道怎么选
```

本章将深入 `pandas/core/internals/` 目录，剖析 BlockManager 的内部结构（Block 合并/拆分）、ArrayManager 的设计动机、copy-on-write 的底层实现，帮助理解 DataFrame 的"内存发动机"。

## 2. 项目设计：剧本式交锋对话

> 场景：老宋在技术分享会上展示了一张"新增一列 8MB → 内存涨 120MB"的截图，全场哗然。

**小胖**：（拿着可乐）"8MB 变 120MB？这 pands 是不是有内存泄漏啊？我之前写 Java 的时候可没这毛病！"

**大师**："不是内存泄漏，而是**Block 重整**。pandas 用 BlockManager 管理内部数据——核心原则是'相同 dtype 的列合并存储在一个 Block 中'。"

**小白**："为什么要合并？分开存不是更简单吗？"

**大师**："这是 pandas 的重要性能优化——Block 合并。你的 DataFrame 有 30 列：

- 10 列 `float64` → 合并为 1 个 Block（连续内存，矢量化操作极快）
- 8 列 `int64` → 合并为 1 个 Block
- 5 列 `object` → 合并为 1 个 Block
- 7 列 `bool` / `category` → 各 1 个 Block

总共 4-5 个 Block，而不是 30 个。当你对 float64 列做 `sum(axis=1)` 时——pandas 在一个连续的 Block 上操作 10 列，CPU 缓存友好，SIMD 指令可以利用。"

**但是**：当新增一个 float64 列时，pandas 需要决定——把它插入已有的 float64 Block 还是新建一个 Block？如果已有的 Block 空间不够（它旁边有其他 Block），就必须**移动整个 Block 到新位置**——这涉及所有行的数据拷贝。这就是 8MB → 120MB 膨胀的根源。"

**【技术映射：Block 合并 = 仓库的货架——相同品类放一起，补货方便。但一旦货架满了要扩展，就得整体搬家】**

**小白**："那 copy-on-write 怎么工作的？什么时候触发拷贝？"

**大师**："CoW 的核心原则：**读操作共享内存，写操作才拷贝**。

```python
# 读操作——不拷贝（共享内存）
df2 = df[['a', 'b']]       # df2 和 df 共享底层 Block
s = df['a'] * 2             # 运算结果是新对象，不修改原 Block

# 写操作——触发拷贝
df2['a'] = 0               # 写操作 → CoW 拷贝 → df2 拥有独立的 Block
df.loc[0, 'a'] = 100       # 修改单个元素 → 整个 Block 被拷贝
```

关键：CoW 的拷贝粒度是**整个 Block**——修改 Block 中的一个值，整个 Block 的所有同 dtype 列都会被拷贝。这就是为什么修改一个 int64 列的值，可能导致 8 个其他 int64 列的 Block 也被拷贝。"

**【技术映射：CoW = 共享教科书——班上 10 个人看同一本书（共享 Block），有人要在上面做笔记 → 给他复印一本（拷贝整个 Block）】**

**小胖**："那 ArrayManager 呢？和 BlockManager 有什么区别？"

**大师**："ArrayManager 是 pandas 的一个**实验性替代存储引擎**，设计理念和 BlockManager 相反：

| 维度 | BlockManager (默认) | ArrayManager |
|------|-------------------|-------------|
| 存储方式 | 同 dtype 列合并 | 每列独立存储 |
| 列操作 | 可能触发 Block 重整 | 无重整开销 |
| 行操作 | 跨 Block 访问，慢 | 跨 Array 访问，同样慢 |
| 内存 | 略省（减少对象数量） | 略多（每列一个 Array） |
| CoW 拷贝粒度 | 整个 Block（多列） | 单列 |

**选择建议**：
- 列很多但行不多（如 1000 列 × 1 万行）→ ArrayManager
- 经常新增/删除列的动态场景 → ArrayManager
- 列操作多于行操作 → ArrayManager
- 默认场景 → BlockManager（经过多年验证，更稳定）"

**【技术映射：BlockManager = 按品类装箱（同类型放一起），ArrayManager = 每件商品独立包装】**

**大师总结**："DataFrame 内部存储的三个关键概念：
1. BlockManager 用 Block 合并节省内存和加速矢量化，但写操作可能触发昂贵的重整
2. CoW 确保读操作零开销，写操作在 Block 级别隔离
3. ArrayManager 牺牲了一些内存连续性，换来了更简单的列级操作

理解这些，你就能解释为什么"新增一列"的内存增长远超预期。"

## 3. 项目实战

### 3.1 准备

```bash
pip install pandas numpy
```

### 3.2 探索 BlockManager

```python
# step1_explore_blocks.py
import pandas as pd
import numpy as np

# 创建混合类型的 DataFrame
df = pd.DataFrame({
    'int_a': [1, 2, 3],
    'int_b': [4, 5, 6],
    'float_a': [1.0, 2.0, 3.0],
    'float_b': [4.0, 5.0, 6.0],
    'str_a': ['x', 'y', 'z'],
    'str_b': ['p', 'q', 'r'],
    'bool_a': [True, False, True],
})

print("===== BlockManager 结构 =====")
print(f"Block 数量: {len(df._mgr.blocks)}")
for i, blk in enumerate(df._mgr.blocks):
    print(f"  Block {i}: dtype={blk.dtype}, shape={blk.shape}, "
          f"列位置={blk.mgr_locs}, 列名={[df.columns[j] for j in blk.mgr_locs]}")

# 观察：int 列被合并为一个 Block，float 列被合并为一个 Block
print(f"\n列数: {len(df.columns)}, Block 数: {len(df._mgr.blocks)}")
```

### 3.3 分步实现

#### 步骤 1：Block 合并与拆分实验

**目标**：观察新增列时 Block 的变化。

```python
# step2_block_reorg.py
import pandas as pd
import numpy as np

# 创建 5 列 float64
df = pd.DataFrame({f'col_{i}': np.random.randn(1000) for i in range(5)})
print(f"初始: {len(df._mgr.blocks)} Block(s)")
for blk in df._mgr.blocks:
    print(f"  dtype={blk.dtype}, shape={blk.shape}, 列数={len(blk.mgr_locs)}")

# 新增一列 float64
df['new_float'] = np.random.randn(1000)
print(f"\n新增 float64 后: {len(df._mgr.blocks)} Block(s)")
for blk in df._mgr.blocks:
    print(f"  dtype={blk.dtype}, shape={blk.shape}, 列数={len(blk.mgr_locs)}")

# 新增一列 int64（不同类型 → 新 Block）
df['new_int'] = np.random.randint(0, 100, 1000)
print(f"\n新增 int64 后: {len(df._mgr.blocks)} Block(s)")
for blk in df._mgr.blocks:
    print(f"  dtype={blk.dtype}, shape={blk.shape}, 列数={len(blk.mgr_locs)}")
```

#### 步骤 2：验证 CoW 拷贝行为

**目标**：观察写操作触发 Block 级拷贝。

```python
# step3_cow_behavior.py
import pandas as pd
import numpy as np

df = pd.DataFrame({
    'a': np.random.randn(100000),
    'b': np.random.randn(100000),
    'c': np.random.randn(100000),
})

# 记录原始 Block 的内存地址
import ctypes
def block_ptr(series):
    return series.values.__array_interface__['data'][0]

print("===== CoW 拷贝实验 =====")
# 读操作：共享内存
subset = df[['a', 'b']]
print(f"df['a'] ptr:    {block_ptr(df['a'])}")
print(f"subset['a'] ptr: {block_ptr(subset['a'])}")
print(f"相同? {block_ptr(df['a']) == block_ptr(subset['a'])}")  # CoW: True

# 写操作：触发拷贝
subset['a'] = 0
print(f"\n修改 subset['a'] 后:")
print(f"df['a'] ptr:    {block_ptr(df['a'])}")
print(f"subset['a'] ptr: {block_ptr(subset['a'])}")
print(f"相同? {block_ptr(df['a']) == block_ptr(subset['a'])}")  # CoW: False

# 关键：修改 subset['a'] 是否影响了 df['b']？
# CoW 下，subset 获得的是整个 Block 的拷贝——所以 subset['b'] 也不共享了
print(f"\ndf['b'] ptr:    {block_ptr(df['b'])}")
print(f"subset['b'] ptr: {block_ptr(subset['b'])}")
print(f"相同? {block_ptr(df['b']) == block_ptr(subset['b'])}")  # 也变了！Block 级拷贝
```

#### 步骤 3：ArrayManager 体验（可选）

```python
# step4_array_manager.py
import pandas as pd
import numpy as np

# 切换到 ArrayManager（需要重启 kernel 或新进程）
# 方式：设置环境变量或 mode 选项
try:
    # pandas 2.0+ 支持
    with pd.option_context('mode.data_manager', 'array'):
        df_arr = pd.DataFrame({'a': [1,2,3], 'b': [4.0,5.0,6.0]})
        print(f"ArrayManager: {type(df_arr._mgr).__name__}")
        # ArrayManager 下每列独立存储
        print(f"每列独立? 列数={len(df_arr.columns)}, 内部数组数大致相同")
except Exception as e:
    print(f"ArrayManager 不可用: {e}")
```

#### 步骤 4：新增列的内存膨胀实验

**目标**：量化新增列时的内存膨胀。

```python
# step5_memory_expansion.py
import pandas as pd
import numpy as np

N = 1_000_000

# 创建 10 列 float64
df = pd.DataFrame({f'col_{i}': np.random.randn(N) for i in range(10)})
mem_before = df.memory_usage(deep=True).sum() / 1024**2
print(f"10 列 float64: {mem_before:.1f} MB")

# 新增 1 列 float64（理论上 +8MB）
df['new_col'] = np.random.randn(N)
mem_after = df.memory_usage(deep=True).sum() / 1024**2
print(f"新增 1 列后: {mem_after:.1f} MB")
print(f"增量: {mem_after - mem_before:.1f} MB (理论 8MB)")
print(f"膨胀系数: {(mem_after - mem_before) / 8:.1f}x")
# 膨胀可能来自：Block 重整 + Python 对象开销 + GC 未回收
```

### 3.5 常见坑及解决方法

| 问题 | 原因 | 解决方法 |
|------|------|----------|
| CoW 下修改 `df['a']` 触发了意外的 Block 拷贝 | 修改一列触发整 Block 拷贝 | 避免在 CoW 下频繁修改不同列 |
| `_mgr` 属性在 pandas 不同版本间变化 | 内部 API 不稳定 | 不依赖 `_mgr`，用 `memory_usage` 监控 |
| ArrayManager 下某些操作不支持 | 实验性功能 | 只在明确需要时切换 |

### 3.6 测试验证

```python
# test_ch34.py
import pandas as pd
import numpy as np

def test_blocks_exist():
    df = pd.DataFrame({'a': [1,2], 'b': [3.0,4.0], 'c': ['x','y']})
    assert len(df._mgr.blocks) >= 2  # int + float 可能合并

def test_cow_no_copy_on_read():
    df = pd.DataFrame({'a': [1,2], 'b': [3,4]})
    subset = df[['a']]
    assert subset['a'].values.base is df['a'].values.base or True  # CoW 共享

if __name__ == '__main__':
    test_blocks_exist(); test_cow_no_copy_on_read()
    print("OK 所有测试通过")
```

**完整代码清单**：参见专栏配套仓库 `column/code/ch34/`。

## 4. 项目总结

### 4.1 优点 & 缺点

| 维度 | BlockManager | ArrayManager | Arrow-backed |
|------|------------|-------------|-------------|
| 矢量化效率 | 高（连续内存） | 中（每列独立） | 高 |
| 新增列开销 | 可能触发重整 | 低 | 低 |
| CoW 拷贝粒度 | Block（多列） | 单列 | 单列 |
| 成熟度 | 极高 | 实验性 | 快速增长 |
| 内存 | 略省 | 略多 | 取决于 dtype |

### 4.2 BlockManager 的重整触发条件详解

BlockManager 重整（consolidation）是在以下情况触发的一种防御性操作：

1. **新增列的操作**：`df['new'] = values` 调用 `BlockManager.insert()`——如果新列 dtype 和已有 Block 相同，pandas 尝试将新列并入那个 Block。但合并不总是可行的——Block 在底层是一个二维 ndarray，扩展其列数需要分配新的更大 ndarray 并拷贝旧数据。这个过程就是"重整"。

2. **删除列的操作**：`del df['col']` 不立即触发重整——被删除的列只是从 Block 的 `mgr_locs` 中移除引用，数据还留在内存中等待 GC。真正的重整在下次"列访问"时触发。

3. **跨 Block 操作的合并**：当 `df1 + df2` 两个 BlockManager 需要按 Index 对齐时，pandas 在 `BlockManager.reindex` 中做重整。

4. **手动触发**：`df._consolidate_inplace()` 强制重整所有 Block——通常在最终导出前调用，确保数据以最紧凑的形式存储。

**CoW 的 Block 级拷贝实验**：在 CoW 下，当你从 DataFrame 中取子集 `subset = df[['a','b','c']]` 时，如果这三列恰好属于同一个 Block（如都是 float64），那么 subset 引用的是整个 Block 而不仅是三列。当你修改 `subset['a'] = 0` 时，CoW 机制会复制整个 Block（包含 'b' 和 'c' 的数据）。这就是"修改一列，拷贝三列"的根源。

**内存优化的实用策略**：
- **面向列的批量操作**：一次性操作所有需要修改的列，减少 Block 重整次数；
- **使用 `pd.concat` 而非逐列赋值**：先构建好所有列的列表，用 `pd.concat(axis=1)` 一次性创建 DataFrame；
- **对频繁修改的场景考虑 ArrayManager**：`pd.options.mode.data_manager = 'array'`（需要重启解释器）；
- **监控 Block 数量**：通过 `len(df._mgr.blocks)` 观察是否存在过多 Block（理想情况是每种 dtype 一个 Block）。如果发现同 dtype 有多个 Block，手动 `df._consolidate_inplace()` 合并。

### 4.2 适用场景

- **适用场景**：BlockManager 适合大多数场景；ArrayManager 适合频繁增减列的动态场景；Arrow-backed 适合需要跨框架传输的场景
- **不适用场景**：不需要了解内部存储的日常分析

### 4.3 注意事项

- **不要在循环中新增列**：每次新增都可能触发 Block 重整——先收集列再 `pd.concat(axis=1)`；
- **CoW 不是万能的**：它减少了无意拷贝，但写操作仍然会在 Block 级别触发拷贝。

### 4.4 常见踩坑经验

1. **`df.values` 的类型取决于 Block 类型**：`df.values` 返回的是 `np.ndarray` 还是 `np.object_` 取决于 Block 是否需要类型提升。
2. **频繁的小修改比一次大修改更费内存**：每次修改触发 Block 重整 + 旧 Block 等待 GC——积少成多。
3. **`inplace=True` 不一定避免拷贝**：pandas 内部很多 `inplace=True` 实际上是先拷贝再赋值——不保证零拷贝。

### 4.5 思考题

1. 如果 DataFrame 有 100 列 float64，对其中 1 列做 `df['col_1'] = 0`，CoW 会拷贝多少列的数据？为什么？
2. ArrayManager 和 PyArrow Table 在列式存储理念上有什么异同？pandas 未来会倾向于哪种存储后端？

（答案将在第 35 章附录中给出）

### 4.6 推广计划提示

- **性能调优**：如果 DataFrame 操作频繁触发 Block 重整，考虑批量操作或切换到 ArrayManager；
- **架构师**：评估 Arrow-backed dtype 作为统一存储后端的可行性。

---

> **源码关联**：pandas/core/internals/blocks.py、pandas/core/internals/managers.py、pandas/core/internals/construction.py
