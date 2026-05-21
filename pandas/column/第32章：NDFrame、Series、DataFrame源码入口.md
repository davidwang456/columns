# 第32章：NDFrame、Series、DataFrame 源码入口

## 1. 项目背景

某数据平台团队的资深工程师老罗最近接到一个任务：**为 pandas 增加一个边界场景的支持**——"DataFrame 构造函数在传入空字典时应该返回一个空 DataFrame，但目前的行为是抛异常"。团队决定由老罗来修复这个 bug。

老罗虽然用了 5 年 pandas，但从未看过它的源码。他打开 GitHub 上的 pandas 仓库，面对 `pandas/core/` 目录下 200+ 个 `.py` 文件——完全不知道从哪里开始看。

**痛点一：不知道 DataFrame 的构造入口在哪里**。用户写 `pd.DataFrame({'a': [1,2,3]})` ——这个构造函数到底调用了什么？经过了哪些内部步骤？最终数据是怎么存到内存里的？老罗找不到入口。

**痛点二：不知道方法调用链**。`df.head()` 这个方法是在哪个文件里定义的？是在 `frame.py` 还是 `generic.py`？老罗用 `grep` 搜索 `def head` 发现了 5 个定义——它们之间是什么关系？

**痛点三：public API 和 internal API 边界不清**。`df._data`、`df._mgr`、`df._values` ——这些下划线开头的属性是什么意思？能直接用吗？文档里没有解释。

痛点流程：

```
pd.DataFrame({'a':[1,2,3]})
  └── 源码中到底发生了什么？
        ├── 入口在哪？200+ .py 文件
        ├── 调用链？5 个 def head
        └── _data / _mgr 是什么？
```

本章将从"源码读者"的视角，系统梳理 pandas 核心对象的层次结构——NDFrame 抽象、Series/DataFrame 构造流程、方法分发机制和 public/internal API 边界。这是高级篇的开端，从"用 pandas"过渡到"理解 pandas"。

## 2. 项目设计：剧本式交锋对话

> 场景：老罗对着 IDE 里 200+ 个 .py 文件发愁，小胖端着泡面走过来。

**小胖**：（吸溜着面条）"源码有啥好看的？pandas 用着没问题就行了呗！看源码不是浪费时间吗？"

**大师**："小胖，你今天用了 `df.head()`，没问题。但如果你需要修一个 bug、加一个 feature、或者搞懂为什么某个操作特别慢——不看源码就只能靠猜。而且 pandas 的源码写得相当好，是学习 Python 大型项目设计的最佳教材之一。"

**小白**："那 DataFrame 的源码结构到底是什么样的？从哪里开始看？"

**大师**："pandas 核心对象有三层继承关系：

```
NDFrame (pandas/core/generic.py)
  ├── 公共方法：head/tail/info/describe/drop/rename/fillna/pipe...
  ├── 属性访问：columns/index/dtypes/shape...
  └── 序列化/IO：to_csv/to_excel/to_parquet...
    │
    ├── Series (pandas/core/series.py) —— 一维带标签数组
    │     └── 特有方法：str.dt.cat 访问器、map、value_counts...
    │
    └── DataFrame (pandas/core/frame.py) —— 二维带标签表格
          └── 特有方法：merge/groupby/pivot...
```

NDFrame 是 Series 和 DataFrame 的**公共父类**——它定义了大约 200 个公共方法。Series 和 DataFrame 各约 50 个独有方法。

**关键原则**：如果你在找 `head()` 的定义，先去 `generic.py`——它很可能在那里被定义一次，两个子类共享。"

**【技术映射：NDFrame/Series/DataFrame 的继承关系 = 汽车平台——MQB 平台（NDFrame）的刹车系统（head）在两款车（Series/DataFrame）上完全一样】**

**小白**："那 `pd.DataFrame(...)` 构造函数内部发生了什么？"

**大师**："构造函数 `DataFrame.__init__()` 的简化流程：

```
pd.DataFrame({'a': [1,2,3]})
  │
  ├── 1. 参数校验: _init_dict → 检测输入是 dict/ndarray/DataFrame...
  │
  ├── 2. 数据提取: extract_index → 从 dict 中提取 index
  │
  ├── 3. 列构建: _init_dict → 遍历 dict 的每个 key-value
  │       └── 每列生成一个 Series 或 np.ndarray
  │
  ├── 4. Block 合并: BlockManager._from_axes
  │       └── 同 dtype 的列合并为一个 Block（减少内存碎片）
  │
  └── 5. 赋值: self._mgr = mgr
          └── DataFrame 本质上就是一个 mgr (BlockManager) + 元数据
```

关键要点：DataFrame 的核心数据结构是 `_mgr`（一个 BlockManager 实例），所有的数据读写最终都委托给 `_mgr`。"

**【技术映射：DataFrame 构造 = 搬家——先分拣物品（列构建）、再按类别装箱（Block 合并）、最后贴标签（index/columns 元数据）】**

**小胖**："那 `df['new_col'] = ...` 和 `df.loc[...] = ...` 内部是怎么处理的？"

**大师**："列赋值和索引赋值是两个不同的代码路径：

- **`df['new'] = val`** 走 `__setitem__` → `_set_item` → `BlockManager.insert`
- **`df.loc[mask, 'col'] = val`** 走 `_LocIndexer.__setitem__` → `_setitem_with_indexer`

loc 索引器的代码在 `pandas/core/indexing.py`——这是 pandas 中最复杂的模块之一（约 3000 行）。它要处理标量赋值、数组赋值、切片赋值、布尔掩码赋值——每种情况的内部实现都不同。"

**【技术映射：`df['new']=` = 直接加一列（简单），`df.loc[mask]=` = 按标签找到位置再修改（复杂）】**

**大师总结**："源码阅读的三个入口：
1. **generic.py**（约 6000 行）——找公共方法（head/tail/drop/fillna...）
2. **frame.py**（约 8000 行）——找 DataFrame 特有方法（merge/groupby/pivot...）
3. **indexing.py**（约 3000 行）——找索引器实现（loc/iloc/at/iat...）

记住：**下划线开头的属性（_mgr/_data/_values）是内部 API，外部代码不要依赖它们——它们可能在下一个版本改名。**"

## 3. 项目实战

### 3.1 准备

```bash
pip install pandas
# 克隆源码（可选）
# git clone https://github.com/pandas-dev/pandas.git
```

### 3.2 探索 NDFrame 公共方法

```python
# step1_ndframe_explore.py
import pandas as pd
import inspect

# 查看 NDFrame 中定义的方法
from pandas.core.generic import NDFrame

ndframe_methods = [m for m in dir(NDFrame) if not m.startswith('_') and callable(getattr(NDFrame, m))]
print(f"NDFrame 公共方法: {len(ndframe_methods)} 个")
print(f"前 20 个: {ndframe_methods[:20]}")

# 查看 head 方法的定义位置
print(f"\nhead 定义在: {inspect.getfile(NDFrame.head)}")
print(f"head 源码前 5 行:")
print(inspect.getsource(NDFrame.head).split('\n')[:5])

# DataFrame 本身额外定义的方法
df_methods = set(dir(pd.DataFrame)) - set(dir(NDFrame))
df_methods = [m for m in df_methods if not m.startswith('_')]
print(f"\nDataFrame 独有方法: {len(df_methods)} 个")
print(f"示例: {sorted(df_methods)[:15]}")
```

### 3.3 跟踪 DataFrame 构造流程

```python
# step2_construction_trace.py
import pandas as pd
import numpy as np

df = pd.DataFrame({'a': [1, 2, 3], 'b': [4.0, 5.0, 6.0], 'c': ['x', 'y', 'z']})

# 1. 查看内部结构
print("===== DataFrame 内部属性 =====")
print(f"_mgr 类型: {type(df._mgr).__name__}")
print(f"_mgr.ndim: {df._mgr.ndim}")

# 2. 查看 blocks（同类型列合并存储）
print(f"\nblocks 数量: {len(df._mgr.blocks)}")
for i, blk in enumerate(df._mgr.blocks):
    print(f"  Block {i}: dtype={blk.dtype}, shape={blk.shape}, 列位置={blk.mgr_locs}")

# 3. 列访问的数据流
print(f"\n===== df['a'] 的数据获取路径 =====")
print(f"df['a'] (Series): {type(df['a']).__name__}")
print(f"df['a'].values: {type(df['a'].values).__name__}, dtype={df['a'].values.dtype}")

# 4. 观察：int 列和 float 列是否合并为一个 Block
df2 = pd.DataFrame({'int_col': [1,2,3], 'float_col': [1.0,2.0,3.0]})
print(f"\n===== int + float DataFrame =====")
for i, blk in enumerate(df2._mgr.blocks):
    print(f"  Block {i}: dtype={blk.dtype}, shape={blk.shape}, 列位置={blk.mgr_locs}")
    # 注意：int 和 float 可能合并为一个 float64 block（因为 int 可安全转为 float）
```

### 3.4 方法分发机制

```python
# step3_method_dispatch.py
import pandas as pd

# pandas 中很多方法是"链式调用"：DataFrame → Series → scalar
df = pd.DataFrame({'a': [1,2,3], 'b': [4,5,6]})

# head() 由 NDFrame 定义
print(f"head 定义在: {type(df.head).__qualname__}")

# merge() 由 DataFrame 定义（NDFrame 没有）
print(f"merge 在 NDFrame? {'merge' in dir(pd.core.generic.NDFrame)}")

# 属性访问：df.columns 实际上是 property
print(f"\ncolumns 类型: {type(pd.DataFrame.columns)}")
print(f"columns 定义在: {pd.DataFrame.columns.fget.__qualname__ if hasattr(pd.DataFrame.columns, 'fget') else 'N/A'}")

# _internal_names 和 _internal_names_set：标记内部属性
print(f"\n_internal_names: {pd.DataFrame._internal_names}")
```

### 3.5 public vs internal API

```python
# step4_api_boundary.py
import pandas as pd

df = pd.DataFrame({'x': [1,2,3]})

# Public API：文档化、稳定、可依赖
print("Public API 示例:")
print(f"  df.head(): {df.head(1).values}")
print(f"  df.shape: {df.shape}")
print(f"  df.dtypes: {df.dtypes.values}")

# Internal API：下划线前缀、不稳定、不可依赖
print("\nInternal API (仅供理解源码用):")
print(f"  df._mgr: {type(df._mgr).__name__}")
# print(f"  df._data: {type(df._data)}")  # 可能已弃用

# 正确的数据访问方式：用 .values 或 .to_numpy()
print(f"\n推荐: df.values = {df.values}")
print(f"推荐: df.to_numpy() = {df.to_numpy()}")
```

### 3.6 绘制源码阅读路线图

```python
# 源码阅读路线图（文本描述）
roadmap = """
===== pandas 源码阅读路线图 =====

1. 入口对象（先看这个）
   pandas/core/generic.py  → NDFrame (公共方法)
   pandas/core/series.py   → Series (一维)
   pandas/core/frame.py    → DataFrame (二维)

2. 索引与选择（再看这个）
   pandas/core/indexing.py      → _LocIndexer / _iLocIndexer
   pandas/core/indexes/base.py  → Index 基类
   pandas/core/indexes/range.py → RangeIndex

3. 内部存储（进阶）
   pandas/core/internals/blocks.py        → Block
   pandas/core/internals/managers.py      → BlockManager / ArrayManager
   pandas/core/internals/construction.py  → 构造辅助

4. 计算链路（深入）
   pandas/core/groupby/    → GroupBy 引擎
   pandas/core/window/     → 窗口函数
   pandas/core/reshape/    → 数据重塑

5. IO 链路（实用）
   pandas/io/parsers/      → CSV 读取
   pandas/io/parquet.py    → Parquet 读写
   pandas/io/sql.py        → SQL 交互

6. 测试
   pandas/tests/           → 官方测试用例（最佳学习材料）
"""
print(roadmap)
```

### 3.7 常见坑及解决方法

| 问题 | 原因 | 解决方法 |
|------|------|----------|
| `df._data` 不存在 | pandas 2.0+ 改为 `_mgr` | 用 `df._mgr` 访问内部管理器 |
| `inspect.getfile` 报错 | 内置类型无源文件 | 对 C 扩展的类型用 `__module__` 代替 |
| 修改源码不生效 | pip install 的是编译后的版本 | 用 `pip install -e .` 以开发模式安装 |

### 3.8 测试验证

```python
# test_ch32.py
import pandas as pd
from pandas.core.generic import NDFrame

def test_ndframe_is_parent():
    assert issubclass(pd.DataFrame, NDFrame)
    assert issubclass(pd.Series, NDFrame)

def test_df_has_mgr():
    df = pd.DataFrame({'a': [1,2,3]})
    assert hasattr(df, '_mgr')

def test_blocks_exist():
    df = pd.DataFrame({'a': [1,2,3], 'b': [4.0,5.0,6.0]})
    assert len(df._mgr.blocks) >= 1

if __name__ == '__main__':
    test_ndframe_is_parent(); test_df_has_mgr(); test_blocks_exist()
    print("OK 所有测试通过")
```

**完整代码清单**：参见专栏配套仓库 `column/code/ch32/`。

## 4. 项目总结

### 4.1 优点 & 缺点

| 维度 | 阅读 pandas 源码 | 只看文档 | 看第三方教程 |
|------|----------------|---------|------------|
| 理解深度 | 彻底（知道"为什么"） | 表面（知道"怎么用"） | 中等 |
| 调试能力 | 强（能定位 bug） | 弱 | 中 |
| 学习成本 | 高（6000+ 行 generic.py） | 低 | 中 |
| 时效性 | 始终最新 | 可能过时 | 可能过时 |

### 4.2 深入理解：NDFrame 中的关键设计模式

**属性访问的延迟求值**：`df.columns`、`df.index`、`df.dtypes` 这些看起来是简单属性，但很多都用了 `@property` 装饰器并在内部做了缓存。例如 `df.columns` 第一次访问时从 `_mgr` 中提取，后续直接返回缓存值——如果底层数据变化，缓存需要手动刷新。

**方法分发的 @final 标记**：pandas 源码中大量使用 `@final` 装饰器标记不应该被子类覆盖的方法。例如 `NDFrame.head()` 被标记为 `@final`——防止 Series 或 DataFrame 各自重写导致行为不一致。

**`__getattr__` 和列名的冲突**：当你有 `df['sum']` 和 `df.sum()` 时——pandas 如何区分？答案是 `__getattr__` 会先检查列名再检查方法名。如果列名和 DataFrame 内置方法同名（比如你把一列命名为 "sum"），`df.sum` 会返回列而不是方法——这可能导致难以排查的 bug。

**内部缓存机制 `_cache`**：NDFrame 维护了一个 `_cache` 字典来缓存 frequently accessed 的派生数据。例如访问 `df.dtypes` 后结果被缓存——下次访问直接返回缓存。但修改数据后必须 `_clear_item_cache()` 清空缓存，否则返回过时数据。

### 4.2 适用场景

- **适用场景**：修复 pandas bug、定制私有版本、深入理解性能瓶颈、学习 Python 大型项目设计
- **不适用场景**：日常数据分析——不需要看源码

### 4.3 注意事项

- **`_mgr` 可能改名或重构**：不要在生产代码中使用 `_mgr`——它是不稳定的内部 API；
- **Cython 代码**：pandas 的部分核心算法用 Cython 编写（`.pyx` 文件），性能调优时可能需要阅读。

### 4.4 常见踩坑经验

1. **在 `frame.py` 中找 `head` 却找不到**：它在 `generic.py`。遇到找不到的方法，先去父类 NDFrame 找。
2. **`_data` 在 pandas 2.0 被弃用**：用 `_mgr` 替代——但注意两者返回的对象类型可能不同。`_data` 返回的是 `BlockManager`，`_mgr` 在某些情况下是 `ArrayManager`。
3. **追踪方法调用链时被 `@final` 装饰器和 property 迷惑**：IDE 的"Go to Definition"可能跳到装饰器而非实际实现。使用 `inspect.getsource()` 或直接在源码中搜索 `def 方法名` 来定位。
4. **`__finalize__` 方法的隐式调用**：很多 DataFrame/Series 方法在返回新对象时会自动调用 `__finalize__`，将原始对象的元数据（如 `attrs`、`name`）复制过来。如果你在子类化 DataFrame 时没有正确实现 `__finalize__`，元数据会丢失。
5. **`_constructor` 属性的作用**：`DataFrame._constructor` 指向 `DataFrame` 类本身——当 groupby/merge 等操作需要创建新的同类容器时，它们通过 `self._constructor(...)` 来调用。这就是为什么子类化 DataFrame 后 groupby 的结果仍然是子类类型。

### 4.5 pandas 源码中的 Cython 代码导航

pandas 的核心性能关键路径使用 Cython（`.pyx` 文件）编写，位于 `pandas/_libs/` 目录。常见的 Cython 模块：

| .pyx 文件 | 负责的功能 |
|-----------|----------|
| `pandas/_libs/lib.pyx` | 通用工具函数（isna、is_integer 等） |
| `pandas/_libs/algos.pyx` | 排序、分组、take 等核心算法 |
| `pandas/_libs/hashtable.pyx` | 哈希表实现（用于 groupby、value_counts） |
| `pandas/_libs/parsers.pyx` | CSV 解析的 C 引擎 |
| `pandas/_libs/interval.pyx` | Interval 和 IntervalIndex |
| `pandas/_libs/join.pyx` | merge/join 的快速路径 |

Cython 代码编译为 `.so`/`.pyd` 文件，可以在 Python 中直接 import。阅读时关注 `.pyx` 源文件，调用链通常从 Python 层（`.py`）传入数据到 Cython 层（`.pyx`）完成计算，结果返回到 Python 层。例如 `df.sum()` 的调用链：`NDFrame.sum()` → `BlockManager.reduce()` → `_libs.lib.reduce()` (Cython)。

### 4.5 思考题

1. pandas 中 `df.head(n)` 和 `df.iloc[:n]` 的内部实现有何不同？哪个更快？为什么？
2. `Series` 继承自 `NDFrame`，那 `Index` 继承自什么？`MultiIndex` 呢？尝试画出 pandas 核心对象完整的类继承图。

（答案将在第 33 章附录中给出）

### 4.6 推广计划提示

- **架构师/资深开发**：将 pandas 源码作为团队"阅读优秀开源代码"的教材；
- **所有高级篇读者**：本章是高级篇的导航页——后面的章节将逐一深入第 3-6 层。

---

> **源码关联**：pandas/core/generic.py、pandas/core/series.py、pandas/core/frame.py
