# 第30章：Pandas 与其他计算框架协作

## 1. 项目背景

某大型互联网公司的数据架构师老郑正在推进"数据中台 2.0"项目。公司目前有多个数据团队在使用不同的计算框架：

- **BI 团队**：用 pandas 做日常报表，数据量约 100MB-2GB
- **算法团队**：用 PyArrow + Polars 做特征工程，需要高性能的列式运算
- **数据工程团队**：用 Spark 处理 TB 级的日志数据
- **实时团队**：用 Flink 做流式指标计算

问题是：这些团队之间交换数据时格式不统一。BI 团队导出 CSV 给算法团队，算法团队转成 Parquet 给数据工程团队，数据工程团队通过 Hive 表给实时团队——每一步都有格式转换和类型丢失的风险。

**痛点一：pandas → Polars 的转换需要经过中间格式**。BI 团队用 pandas 处理完的数据想交给算法团队用 Polars，要先 `to_csv` 再 `polars.read_csv`——一趟 IO 开销 + 类型推断损失。

**痛点二：pandas 处理不了的数据只能扔给 Spark**。50GB 的日志数据 pandas 处理不了，但用 Spark 又太重——需要起 YARN 集群、写 JAR 包、配置资源。老郑想知道：**是否有介于 pandas 和 Spark 之间的选择？**

**痛点三：跨框架传输时类型丢失**。pandas 的 `category` dtype 经过 CSV 中转后变成了 `object`；`datetime64[ns]` 变成字符串——下游每次都要重新 `astype`，浪费大量时间。

痛点流程：

```
BI(pandas) ──CSV──→ 算法(Polars) ──Parquet──→ 工程(Spark) ──Hive──→ 实时(Flink)
  ↑                                                        ↑
  └──────────── 每一步都丢失类型 + 额外IO ──────────────┘
```

本章将介绍 pandas 与 PyArrow、Polars、Dask、Spark 的协作方式，以及 Apache Arrow 作为统一内存格式如何实现跨框架零拷贝传输。

## 2. 项目设计：剧本式交锋对话

> 场景：老郑在白板上画了团队的"数据格式转换迷宫"，小胖看到后惊呆了。

**小胖**：（喝着可乐）"这不就是几个工具嘛！CSV 走天下——所有框架都能读 CSV！为什么要把问题搞得这么复杂？"

**大师**："小胖，CSV 有三大原罪：没有类型、没有压缩、没有 schema。一个 `datetime64[ns]` 类型的列，写到 CSV 变成字符串，读到 Polars 再推断类型——运气好能猜对，运气不好就变成了 object。这在 100 万行数据上浪费几秒钟，在 10 亿行上浪费几小时。"

**小白**："那有什么办法让 pandas 和 Polars 直接交换数据，不需要 CSV？"

**大师**："Apache Arrow 就是解决这个问题的。Arrow 定义了一种**统一的内存列式格式**——pandas、Polars、Spark、DuckDB 等十几个框架都能直接读写 Arrow 格式，**不需要转换**。

```python
# pandas → Arrow Table → Polars (零拷贝)
import pyarrow as pa

# pandas 导出为 Arrow Table
arrow_table = pa.Table.from_pandas(df)

# Polars 直接从 Arrow Table 读取（零拷贝）
import polars as pl
pl_df = pl.from_arrow(arrow_table)
```

`from_pandas` 和 `from_arrow` 之间的数据传递是**零拷贝**的——共享同一块内存，不需要序列化/反序列化。比 CSV 快 50-100 倍。"

**【技术映射：Arrow = 万能电源适配器——任何国家的电器（pandas/Polars/Spark）都能通过它直接接入同一个插座（Arrow 内存格式）】**

**小白**："那 pandas 和 Dask 是什么关系？Dask DataFrame 看起来和 pandas DataFrame 几乎一样？"

**大师**："Dask 的设计哲学就是'pandas 的分布式版本'——Dask DataFrame 由多个 pandas DataFrame 组成，API 几乎和 pandas 一模一样：

```python
import dask.dataframe as dd

# pandas 读 CSV
df = pd.read_csv('big.csv')  # 全量读入内存

# Dask 读 CSV（延迟计算）
ddf = dd.read_csv('big.csv')  # 不会立即执行
result = ddf.groupby('category')['amount'].sum().compute()  # 分布式计算
```

核心区别：pandas 是**急切计算**（每行代码立即执行），Dask 是**懒计算**（构建计算图，最后 `.compute()` 才执行）。

适用边界：
- < 2GB：pandas 直接搞定
- 2GB - 50GB：pandas + chunksize 分块（第 23 章）
- 50GB - 1TB：Dask（单机多核或小集群）
- > 1TB：Spark（YARN/K8s 集群）"

**【技术映射：pandas = 家用烤箱（小量），Dask = 食堂厨房（中量），Spark = 食品工厂（大量）】**

**小胖**："那 Polars 呢？听说是 Rust 写的——比 pandas 快？为啥不直接用 Polars 代替 pandas？"

**大师**："Polars 确实在很多场景下更快——它是 Rust 实现的，天生支持多线程和惰性计算。但它和 pandas 不是'替代'关系，而是'互补'关系：

| 维度 | pandas | Polars | Dask |
|------|--------|--------|------|
| 实现语言 | Python/Cython | Rust | Python |
| 计算模式 | 急切 | 惰性+急切 | 惰性 |
| 多线程 | 部分支持 | 原生全线程 | 支持 |
| 生态成熟度 | ★★★★★ | ★★★ | ★★★★ |
| 学习成本 | 低 | 中 | 中 |

选型建议：
- 已有 pandas 代码 → 继续用，优化慢的部分
- 新项目 + 高性能需求 → Polars
- 中等数据 + 分布式需求 → Dask
- 超大集群 → Spark"

**大师总结**："框架选择的三层决策：
1. **数据量**：< 2GB pandas，2-50GB chunksize，50GB-1TB Dask，> 1TB Spark
2. **性能需求**：pandas 慢的向量化 → eval/query 加速（第 24 章）→ Polars 替代
3. **跨框架传输**：统一用 Arrow IPC 格式避免序列化开销

记住：**Arrow 是高速公路，pandas/Polars/Spark 是不同类型的车——路修好了，什么车都能跑得快。**"

## 3. 项目实战

### 3.1 准备

```bash
pip install pandas pyarrow polars
```

### 3.2 模拟数据

```python
# generate_data_ch30.py
import pandas as pd
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

np.random.seed(42)
N = 500_000

df = pd.DataFrame({
    'id': range(N),
    'category': np.random.choice(['A','B','C','D','E'], N),
    'value': np.random.normal(100, 30, N).round(2),
    'date': pd.date_range('2025-01-01', periods=N, freq='30s'),
    'is_active': np.random.choice([True, False], N),
})
df['category'] = df['category'].astype('category')

# 导出为不同格式
df.to_csv('data.csv', index=False)
df.to_parquet('data.parquet', index=False)

# Arrow IPC 格式（零拷贝交换用）
table = pa.Table.from_pandas(df)
with pa.ipc.new_file(pa.OSFile('data.arrow', 'wb'), table.schema) as writer:
    writer.write_table(table)

print(f"已生成 {N:,} 行数据 (CSV + Parquet + Arrow)")
```

### 3.3 分步实现

#### 步骤 1：pandas ↔ PyArrow 零拷贝交换

**目标**：演示 pandas 和 Arrow 之间的零拷贝转换。

```python
# step1_pandas_arrow.py
import pandas as pd
import pyarrow as pa
import time

df = pd.read_parquet('data.parquet')

# === pandas → Arrow Table ===
start = time.perf_counter()
table = pa.Table.from_pandas(df)
t_convert = time.perf_counter() - start
print(f"pandas → Arrow: {t_convert:.4f}s, {table.num_rows:,} 行 x {table.num_columns} 列")

# === Arrow Table → pandas ===
start = time.perf_counter()
df_back = table.to_pandas()
t_back = time.perf_counter() - start
print(f"Arrow → pandas: {t_back:.4f}s, {len(df_back):,} 行")

# === 验证 dtype 保留 ===
print(f"\ncategory dtype 保留: {df_back['category'].dtype}")
print(f"datetime dtype 保留: {df_back['date'].dtype}")

# === Arrow IPC 文件（跨进程零拷贝交换）===
with pa.ipc.open_file(pa.memory_map('data.arrow', 'r')) as reader:
    table_from_ipc = reader.read_all()
print(f"\nIPC 读取: {table_from_ipc.num_rows:,} 行")
```

#### 步骤 2：pandas ↔ Polars 对比

**目标**：对比 pandas 和 Polars 在聚合操作上的性能。

```python
# step2_polars_compare.py
import pandas as pd
import polars as pl
import time

# pandas 读取
start = time.perf_counter()
df_pd = pd.read_parquet('data.parquet')
t_pd_read = time.perf_counter() - start

# pandas 聚合
start = time.perf_counter()
result_pd = df_pd.groupby('category', observed=True).agg(
    sum_val=('value','sum'),
    count_val=('value','count'),
    mean_val=('value','mean')
).reset_index()
t_pd_agg = time.perf_counter() - start

# Polars 读取
start = time.perf_counter()
df_pl = pl.read_parquet('data.parquet')
t_pl_read = time.perf_counter() - start

# Polars 聚合
start = time.perf_counter()
result_pl = df_pl.group_by('category').agg([
    pl.col('value').sum().alias('sum_val'),
    pl.col('value').count().alias('count_val'),
    pl.col('value').mean().alias('mean_val'),
])
t_pl_agg = time.perf_counter() - start

print(f"读取: pandas {t_pd_read:.3f}s vs Polars {t_pl_read:.3f}s")
print(f"聚合: pandas {t_pd_agg:.3f}s vs Polars {t_pl_agg:.3f}s")
print(f"\npandas 结果:\n{result_pd.to_string(index=False)}")
print(f"\nPolars 结果:\n{result_pl}")
```

#### 步骤 3：DataFrame Interchange Protocol

**目标**：使用 `__dataframe__` 协议实现跨库零拷贝。

```python
# step3_interchange.py
import pandas as pd

df = pd.read_parquet('data.parquet')

# pandas 2.0+ 支持 __dataframe__ 协议
if hasattr(df, '__dataframe__'):
    dfi = df.__dataframe__()
    print(f"Interchange 协议: {dfi.num_rows():,} 行 x {dfi.num_columns()} 列")
    print(f"列名: {[dfi.get_column(i).name for i in range(dfi.num_columns())]}")
else:
    print("当前 pandas 版本不支持 __dataframe__ 协议（需要 2.0+）")

# 可以传给任何支持该协议的库（Polars/Vaex/cuDF 等）
# polars_df = pl.from_dataframe(df)  # Polars 0.20+ 支持
```

#### 步骤 4：何时从 pandas 迁移——决策矩阵

**目标**：用数据量、延迟要求和团队技能做迁移决策。

```python
# step4_decision.py
def recommend_framework(data_size_gb, latency_requirement, team_skill):
    """推荐计算框架"""
    if data_size_gb < 2 and latency_requirement != 'streaming':
        return 'pandas'
    elif data_size_gb < 50:
        if team_skill in ['python_advanced', 'rust']:
            return 'Polars'
        else:
            return 'pandas + chunksize / Dask'
    elif data_size_gb < 1000:
        return 'Dask (单机多核或小集群)'
    else:
        return 'Spark (YARN/K8s 集群)'

print("场景推荐:")
print(f"  100MB 日报: {recommend_framework(0.1, 'batch', 'python_basic')}")
print(f"  20GB 特征工程: {recommend_framework(20, 'batch', 'python_advanced')}")
print(f"  500GB 日志: {recommend_framework(500, 'batch', 'python_advanced')}")
print(f"  5TB 数据仓库: {recommend_framework(5000, 'batch', 'spark')}")
```

### 3.4 常见坑及解决方法

| 问题 | 原因 | 解决方法 |
|------|------|----------|
| `from_pandas` 后 category 丢失 | Arrow 不原生支持 pandas category | Arrow 2.0+ 支持字典类型，升级 pyarrow |
| Polars group_by 语义和 pandas 不同 | Polars 默认不过滤 null 组 | 对照文档调整参数 |
| `__dataframe__` 协议报 AttributeError | pandas 版本 < 2.0 | 升级 pandas 或降级用法 |
| Dask 的 compute() 内存爆炸 | 中间结果太大 | 用 `persist()` 或增量写出 |

### 3.5 测试验证

```python
# test_ch30.py
import pandas as pd
import pyarrow as pa

def test_pandas_to_arrow_roundtrip():
    df = pd.DataFrame({'a':[1,2], 'b':['x','y'], 'c':[True,False]})
    table = pa.Table.from_pandas(df)
    back = table.to_pandas()
    assert list(back['a']) == [1,2]
    assert list(back['b']) == ['x','y']

def test_parquet_cross_framework():
    df = pd.DataFrame({'x': [1,2,3]})
    df.to_parquet('_test.parquet', index=False)
    # 验证 Polars 也能读
    try:
        import polars as pl
        pl_df = pl.read_parquet('_test.parquet')
        assert len(pl_df) == 3
    except ImportError:
        pass
    import os; os.remove('_test.parquet')

if __name__ == '__main__':
    test_pandas_to_arrow_roundtrip(); test_parquet_cross_framework()
    print("OK 所有测试通过")
```

**完整代码清单**：参见专栏配套仓库 `column/code/ch30/`。

## 4. 项目总结

### 4.1 优点 & 缺点

| 维度 | pandas | Polars | Dask | Spark |
|------|--------|--------|------|-------|
| 数据量上限 | < 2GB | < 50GB | < 1TB | PB级 |
| 单机性能 | 中 | 极高 | 中（分布式加速） | 低（单机） |
| API 熟悉度 | ★★★★★ | ★★★ | ★★★★ | ★★ |
| 生态库 | 极丰富 | 快速成长 | 兼容 pandas | 独立生态 |
| 部署难度 | 低 | 低 | 中 | 高 |
| Arrow 支持 | 原生（2.0+） | 原生 | 支持 | 支持 |

### 4.2 适用场景

- **适用场景**：
  1. pandas 现有代码 → 遇到性能瓶颈时考虑 Polars（API 相似度高）
  2. 中等数据量需要分布式 → Dask（和 pandas API 几乎一致）
  3. 跨团队数据交换 → Arrow 作为统一内存格式
  4. 需要与 GPU 框架协作 → Arrow + cuDF（RAPIDS 生态）
- **不适用场景**：
  1. < 100MB 的简单分析——pandas 足够，过度工程化
  2. 纯实时流计算——Flink/Kafka Streams 而非 pandas

### 4.3 注意事项

- **Arrow IPC 文件格式不是长期存储格式**：Arrow IPC 适合进程间零拷贝交换，长期存储用 Parquet（更小、更通用）；
- **Polars 的惰性模式**：polars 的 `lazy()` 模式和 Dask 的惰性计算类似——构建计算图然后优化执行。但它不会自动分布式；
- **不要因为追逐新框架而忽略 pandas**：pandas 的生态（教程/社区/第三方库）是最大的优势。

### 4.4 常见踩坑经验

1. **用 Polars 替代 pandas 后发现第三方库不兼容**：很多库（如 feature-engine、category_encoders）只接受 pandas DataFrame。策略：核心数据处理用 Polars，特征工程部分转回 pandas。Polars 提供了 `df.to_pandas()` 方法，且利用了 Arrow 的零拷贝转换，性能开销很小。
2. **Dask 的 `compute()` 返回的是 pandas DataFrame 但全部在内存中**：小规模 Dask 计算完后 compute 没问题，但大规模任务 Compute 会 OOM。应该用 `ddf.to_parquet('output/')` 分片写出，避免全量加载。或者使用 `ddf.persist()` 将中间结果分布式存储在内存中，而非单机集中。
3. **Arrow Table 的 `to_pandas()` 在零拷贝模式下修改数据会互相影响**：`to_pandas(types_mapper=...)` 创建的 pandas DataFrame 可能共享 Arrow 的内存——修改 pandas 的数据可能影响原始的 Arrow Table。需要 copy 时加 `to_pandas(copy=True)`。判断是否零拷贝：检查 `df.values.base is table.columns[0].chunks[0].buffers()[1]`。
4. **pandas 和 Spark DataFrame 互转时丢失 nullable 语义**：Spark 的 `NULL` 映射到 pandas 的 `NaN`（数值列）或 `None`（字符串列）。如果 pandas 的 Int64（大写 I）列转到 Spark 再转回来，可能降级为 float64。使用 Arrow 作为中间格式可以保留 nullable 语义。
5. **Dask 和 pandas 的某些方法名相同但参数不同**：例如 `dask.dataframe.merge` 不支持 `validate` 参数（pandas 的 merge 支持）。在迁移 pandas→Dask 时，务必查看 Dask 文档确认支持的参数子集。

### 4.5 框架选型评测方法论

在决定是否从 pandas 迁移到其他框架时，建议做一个小规模的基准测试：

```python
# 基准测试模板
import pandas as pd
import pyarrow as pa
import time, sys

def benchmark(name, fn, *args, n_runs=3):
    times = []
    for _ in range(n_runs):
        t0 = time.perf_counter()
        result = fn(*args)
        times.append(time.perf_counter() - t0)
    mem_mb = sys.getsizeof(result) / 1024**2 if hasattr(result, '__sizeof__') else 0
    print(f"{name}: avg={np.mean(times):.3f}s, min={min(times):.3f}s, mem={mem_mb:.1f}MB")
    return result

# 示例：对比 pandas vs Polars 的 groupby 性能
# benchmark('pandas', lambda df: df.groupby('cat')['val'].sum(), df_pd)
# benchmark('polars', lambda df: df.group_by('cat').agg(pl.col('val').sum()), df_pl)
```

评测指标应同时关注：**执行耗时、内存峰值、代码可维护性**——后者往往比前者更重要，因为代码会被修改很多次，而只会被执行一次。

### 4.5 思考题

1. 如果公司有一个 500GB 的数据集，目前用 Spark 处理需要 30 分钟。用 Dask 单机（32 核 + 256GB）是否能达到类似性能？如何评估？
2. Apache Arrow 的 `Flight` 协议是什么？它和 Arrow IPC 有何不同？适用于什么场景？

（答案将在第 31 章附录中给出）

### 4.6 推广计划提示

- **架构师**：将 Arrow 作为跨团队数据交换的标准格式，纳入技术规范；
- **数据工程师**：评估 Dask 作为"轻量 Spark"的可行性——减少 YARN 集群的维护成本；
- **所有团队**：不要为了追新而替换 pandas——先评估数据量和性能瓶颈，再做决策。

---

> **源码关联**：pandas/core/interchange/、pandas/core/arrays/arrow/
