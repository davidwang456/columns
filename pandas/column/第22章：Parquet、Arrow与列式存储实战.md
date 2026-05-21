# 第22章：Parquet、Arrow 与列式存储实战

## 1. 项目背景

某数据平台团队管理着公司过去 5 年的订单数据，以 CSV 格式存储在 NAS 上，按年分目录（2019-2025），总计约 800GB。分析师和算法工程师经常需要查询"某品类近半年销售额趋势"或"某用户的历史消费行为"——这意味着要扫描多个 CSV 文件。

数据工程师小刘负责维护这套数据存储。他面临三个核心问题：

**痛点一：CSV 查询效率极低**。查询"2024 年服装品类的销售额"需要扫描 2024 年全部的 CSV 行（约 120GB），即使只关心一个品类、一个数值列。因为 CSV 是按行存储的——要读 `amount` 列就必须读完整行（包括 30 个其他字段），IO 浪费严重。一次简单查询耗时 8 分钟。

**痛点二：CSV 不保留数据类型**。公司订单表中有一个 `is_vip` 布尔列——在 CSV 中存储为字符串 "True"/"False"，pandas 每次读取都需要重新推断类型。更糟的是 `user_id` 在 CSV 中是字符串，pandas 读进来是 object（Python 指针），占用内存是实际需要的 3 倍。

**痛点三：存储空间浪费**。800GB CSV 中超过 60% 是重复的维度值（如"品类=服装"在每行出现一次）。CSV 没有任何压缩机制利用这种重复性。

痛点流程：

```
订单 CSV (800GB, 7年)
  ├── 查"某品类销售额"→ 扫描120GB → 8分钟
  ├── dtype 丢失→ bool→string, int→object → 内存膨胀
  └── 无压缩 → 60%重复值
```

本章将介绍 Parquet（列式存储）和 Apache Arrow（内存格式），展示如何将 CSV 转换为分区 Parquet 数据集，实现 10 倍查询加速和 5 倍存储压缩。

## 2. 项目设计：剧本式交锋对话

> 场景：小刘在技术分享会上展示了"CSV vs Parquet 查询耗时对比图"——8 分钟 vs 3 秒，全场震惊。

**小胖**：（震惊地放下可乐）"3 秒？！你是怎么做到的？CSV 要 8 分钟，Parquet 只要 3 秒——这是同一个文件吗？"

**大师**："不是'同一个文件'，而是'同一种数据，不同的存储方式'。CSV 是**行式存储**——数据按行连续存放在磁盘上。Parquet 是**列式存储**——每一列的数据连续存放。

行式存储 vs 列式存储的直观区别：
- 行式（CSV）：`row1_col1, row1_col2, row1_col3 | row2_col1, row2_col2, row2_col3 | ...`
- 列式（Parquet）：`col1_all_rows | col2_all_rows | col3_all_rows`

当你只查询 `amount` 一列时，Parquet 只需要读 `amount` 那一段数据——跳过了所有其他列。这称为**列裁剪（column pruning）**。而 CSV 必须读完整行再去取 amount 列。"

**【技术映射：行式 = 按人读档案（每人的全部信息连续存放），列式 = 按科目收试卷（所有人的数学卷子摞在一起，语文卷子摞在一起）】**

**小白**："等等——Parquet 是列式存储和 Apache Arrow 有什么关系？它们是同一个东西吗？"

**大师**："这是最常被混淆的两个概念：
- **Parquet**：**磁盘格式**（文件存储）——数据以列式结构存在硬盘上，支持高压缩率。
- **Arrow**：**内存格式**（运行时）——数据以列式结构存在内存中，支持跨语言零拷贝传输。

两者的关系类似于：**Parquet 是 Arrow 在磁盘上的'持久化版本'**。

```
磁盘：Parquet 文件（列式 + 压缩）
  ↓ read_parquet
内存：Arrow Table（列式 + 零拷贝）
  ↓ pandas
DataFrame（BlockManager + Arrow-backed dtype）
```

最关键的：pandas 2.0+ 支持 **Arrow-backed dtype**——DataFrame 的底层直接使用 Arrow 数组存储。这意味着从 Parquet 读进来后，数据在内存中保持 Arrow 格式，不需要再复制到 NumPy 数组。"

**【技术映射：Parquet(磁盘) = 压缩饼干，Arrow(内存) = 餐桌上的盘子——饼干泡水(解压)后放到盘子上(Arrow)，可以直接吃(计算)】**

**小胖**："那分区目录是什么？为什么我一看就是按日期分了一大堆文件夹？"

**大师**："**分区（partitioning）** 是按某个列的值把数据分到不同子目录。比如按日期分区：

```
orders/
  year=2024/
    month=01/
      part-0.parquet
      part-1.parquet
    month=02/
      ...
  year=2025/
    ...
```

当查询 `WHERE year=2024 AND month=01` 时，Parquet 读取器直接跳过其他 11 个月的目录——这称为**分区裁剪（partition pruning）**。列裁剪 + 分区裁剪 = 双重过滤，这就是为什么 800GB 数据查一个品类只需 3 秒。"

**大师总结**："Parquet 的三重加速机制：
1. **列裁剪**：只读需要的列，跳过其他
2. **分区裁剪**：只读需要的目录，跳过其他
3. **谓词下推**：读取引擎在文件层面就过滤掉不符合条件的行

加上内置的字典编码和 Snappy/Zstd 压缩——同等数据量，Parquet 只有 CSV 的 1/5 到 1/10 大小。"

## 3. 项目实战

### 3.1 准备

```bash
pip install pandas pyarrow
```

### 3.2 模拟 CSV 数据并转换

```python
# generate_and_convert.py
import pandas as pd
import numpy as np
import os, time
from pathlib import Path

np.random.seed(42)
N = 500_000  # 50 万行

# 模拟宽表订单
df = pd.DataFrame({
    'order_id': [f'ORD{i:08d}' for i in range(N)],
    'user_id': np.random.randint(10000, 50000, N),
    'order_date': pd.date_range('2023-01-01', periods=N, freq='3min'),
    'category': np.random.choice(['服装','电子','食品','家居','美妆'], N),
    'product': np.random.choice(['T恤','耳机','坚果','毛巾','口红','手机','薯片','沙发','粉底','短裤'], N),
    'amount': np.round(np.random.exponential(200, N), 2),
    'qty': np.random.randint(1, 5, N).astype('int8'),
    'is_vip': np.random.choice([True, False], N),
    'city': np.random.choice(['北京','上海','广州','深圳','杭州']*4, N),
    'status': np.random.choice(['completed','cancelled','pending'], N, p=[0.7,0.15,0.15]),
})

# 写入 CSV（作为对比基线）
csv_path = 'orders.csv'
df.to_csv(csv_path, index=False)
csv_size = os.path.getsize(csv_path) / 1024**2
print(f"CSV 大小: {csv_size:.1f} MB")

# 写入 Parquet（分区: year → month）
parquet_dir = 'orders_parquet'
df['year'] = df['order_date'].dt.year
df['month'] = df['order_date'].dt.month

start = time.perf_counter()
df.to_parquet(parquet_dir, partition_cols=['year','month'], index=False)
t_write = time.perf_counter() - start

parquet_size = sum(f.stat().st_size for f in Path(parquet_dir).rglob('*.parquet')) / 1024**2
print(f"Parquet 大小: {parquet_size:.1f} MB ({csv_size/parquet_size:.1f}x 压缩)")
print(f"写入耗时: {t_write:.2f}s")

# 清理 CSV
os.remove(csv_path)
```

### 3.3 分步实现

#### 步骤 1：全量读取对比

**目标**：对比 CSV 和 Parquet 的读取速度和内存。

```python
# step1_read_compare.py
# 注：需要先生成 CSV，这里做示意对比
import pandas as pd
import time

# CSV 读取（模拟）
# df_csv = pd.read_csv('orders.csv')
# Parquet 读取
start = time.perf_counter()
df_pq = pd.read_parquet('orders_parquet')
t_read = time.perf_counter() - start

print(f"Parquet 全量读取: {len(df_pq):,} 行, {t_read:.2f}s")
print(f"dtype 保留: order_date={df_pq['order_date'].dtype}, is_vip={df_pq['is_vip'].dtype}, category={df_pq['category'].dtype}")
```

#### 步骤 2：列裁剪——只读需要的列

**目标**：演示只读部分列时的性能增益。

```python
# step2_column_pruning.py
import pandas as pd
import time

# 只读 3 列（跳过其余 10+ 列）
start = time.perf_counter()
df_slim = pd.read_parquet('orders_parquet', columns=['order_date','category','amount'])
t_col = time.perf_counter() - start

print(f"列裁剪读取 3 列: {len(df_slim):,} 行, {t_col:.2f}s")
print(f"读取的列: {df_slim.columns.tolist()}")

# 聚合查询
category_sales = df_slim.groupby('category')['amount'].sum()
print(f"品类销售额:\n{category_sales.round(0).to_string()}")
```

#### 步骤 3：分区裁剪——只读特定分区

**目标**：利用分区目录结构，只读取 2024 年 1 月的数据。

```python
# step3_partition_pruning.py
import pandas as pd
import time

# 使用 filters 参数只读取特定分区
start = time.perf_counter()
df_jan = pd.read_parquet(
    'orders_parquet',
    filters=[('year', '==', 2024), ('month', '==', 1)]
)
t_filter = time.perf_counter() - start

print(f"分区裁剪读取 (2024-01): {len(df_jan):,} 行, {t_filter:.2f}s")
# 注意：filters 底层会做谓词下推，在文件层面就完成过滤
```

#### 步骤 4：Arrow-backed dtype 实战

**目标**：使用 Arrow 后端实现更低内存和更快计算。

```python
# step4_arrow_backend.py
import pandas as pd
import pyarrow as pa

# 方式1：读取时指定 Arrow 后端
df_arrow = pd.read_parquet('orders_parquet', dtype_backend='pyarrow')
print(f"Arrow-backed dtypes:")
for col in ['category','amount','city','is_vip']:
    print(f"  {col}: {df_arrow[col].dtype}")

# 方式2：转换为 Arrow dtype
df_normal = pd.read_parquet('orders_parquet')
df_normal_arrow = df_normal.convert_dtypes(dtype_backend='pyarrow')

# 查看底层数组类型
print(f"\ncategory 底层数组: {type(df_arrow['category'].values)}")

# Arrow 写的优势：跨框架传输
table = pa.Table.from_pandas(df_arrow)
print(f"Arrow Table: {table.num_rows} 行 x {table.num_columns} 列")
```

#### 步骤 5：Schema 演进兼容

**目标**：模拟新增列后新旧 Parquet 文件的兼容读取。

```python
# step5_schema_evolution.py
import pandas as pd
import os, shutil
from pathlib import Path

# 模拟新增列
df_new_schema = pd.DataFrame({
    'order_id': ['ORD999'],
    'amount': [99.99],
    'category': ['服装'],
    'new_column': ['new_value'],  # 新列
})
df_new_schema['year'] = 2025
df_new_schema['month'] = 5

new_part = Path('orders_parquet') / 'year=2025' / 'month=05'
new_part.mkdir(parents=True, exist_ok=True)
df_new_schema.to_parquet(new_part / 'part-0.parquet', index=False)

# 读取全部——pandas 自动处理 schema 合并
df_all = pd.read_parquet('orders_parquet')
print(f"合并读取: {len(df_all)} 行, 列: {df_all.columns.tolist()}")
print(f"new_column 非空: {df_all['new_column'].notna().sum()}")
```

### 3.4 常见坑及解决方法

| 问题 | 原因 | 解决方法 |
|------|------|----------|
| `read_parquet` 报"not a Parquet file" | 目录结构不符合 Hive 分区格式 | 确保列名=目录名（如 year=2024） |
| `filters` 不生效 | 过滤列不在分区列中 | filters 仅用于分区列过滤 |
| 分区列被读到数据中 | `read_parquet` 默认包含分区列 | 用 `columns` 参数排除 |
| Arrow dtype 报 NotImplementedError | 某些操作尚不支持 Arrow | 对不支持的操作先 `.astype()` 转 NumPy |
| schema 冲突 | 同名列在不同分区中类型不同 | 统一 schema 或读入后用 `astype` 统一 |

### 3.5 测试验证

```python
# test_ch22.py
import pandas as pd
import os

def test_parquet_roundtrip():
    df = pd.DataFrame({'id':[1,2], 'name':['a','b'], 'val':[10.5,20.3]})
    df.to_parquet('_test.parquet', index=False)
    back = pd.read_parquet('_test.parquet')
    assert len(back) == 2
    assert back['name'].dtype == 'object'  # Parquet 保留 string
    os.remove('_test.parquet')

def test_column_pruning():
    # 只读部分列
    df = pd.DataFrame({'a':[1,2], 'b':[3,4], 'c':[5,6]})
    df.to_parquet('_test2.parquet', index=False)
    slim = pd.read_parquet('_test2.parquet', columns=['a','c'])
    assert list(slim.columns) == ['a','c']
    os.remove('_test2.parquet')

def test_filters():
    df = pd.DataFrame({'year':[2024,2024,2025], 'val':[1,2,3]})
    df.to_parquet('_test3.parquet', partition_cols=['year'])
    filtered = pd.read_parquet('_test3.parquet', filters=[('year','==',2024)])
    assert len(filtered) == 2

import shutil
if os.path.exists('_test3.parquet'): shutil.rmtree('_test3.parquet')

if __name__ == '__main__':
    test_parquet_roundtrip(); test_column_pruning()
    print("OK 测试通过 (test_filters 需手动清理 _test3.parquet)")
```

**完整代码清单**：参见专栏配套仓库 `column/code/ch22/`。

## 4. 项目总结

### 4.1 优点 & 缺点

| 维度 | Parquet | CSV | JSON | ORC |
|------|---------|-----|------|-----|
| 存储大小 | 1x（基线） | 5-10x | 8-15x | 0.8-1.2x |
| 读取速度 | 极快（列裁剪） | 慢（全行扫描） | 慢 | 快 |
| 类型保留 | 完整 | 丢失 | 部分 | 完整 |
| 压缩 | Snappy/Zstd/gzip | 无/外部 gzip | 无 | Zlib/Snappy |
| 人类可读 | 二进制 | 纯文本 | 纯文本 | 二进制 |
| 生态支持 | 广泛（Spark/Hive/Trino） | 通用 | 通用 | Hadoop 生态 |

### 4.2 适用场景

- **适用场景**：
  1. 中大型数据集（>100MB）的长期存储和查询
  2. 数据管道中间格式——下游只需读特定列
  3. 需要按日期/区域等维度做分区查询的分析场景
  4. 需要保留 dtype 的数据交换（pandas ↔ Spark）
- **不适用场景**：
  1. 小文件（<1MB）——Parquet 的元数据开销比数据还大
  2. 需要人工直接查看的数据（CSV 更合适）
  3. 高频写入（每秒几百次）——Parquet 是批处理格式

### 4.3 注意事项

- **分区列不宜过多**：3 层以内（如 year→month→day），超过 5 层目录过多反而降低性能；
- **小文件问题**：分区过细会导致每个文件只有几行——合并小文件或调大每个分区的文件大小。建议用 `pyarrow.dataset.write_dataset` 的 `max_rows_per_file` 控制文件大小；
- **兼容性**：pandas 生成的 Parquet 默认用 `pyarrow` 引擎，`fastparquet` 引擎兼容性稍差但速度更快。如果需要和 Spark/Hive 互通，建议使用 `pyarrow` 引擎并设置 `version='2.6'`；
- **compression**：默认 `snappy`（快速），`gzip`（高压缩比），`zstd`（新，兼顾速度和压缩比）。Snappy 适合频繁读取的数据，Zstd 适合长期归档；
- **Arrow dtype 的局限性**：不是所有 pandas 操作都支持 Arrow-backed dtype。例如 `df.groupby().apply()` 和某些 `str` 访问器操作会回退到 NumPy。在关键路径上先验证 Arrow dtype 的支持程度；
- **Parquet 写入模式**：`to_parquet` 默认是单文件写入。对于大数据集，建议先按分区目录结构预先创建目录，再使用 `partition_cols` 参数进行分区写入，这样每个分区内的 Parquet 文件大小更可控；
- **读取时的 schema 冲突**：如果某个分区内的 Parquet 文件列类型与其他分区不一致（如 amount 在一个分区是 int32，另一个是 float64），`read_parquet` 会尝试合并 schema，但可能报错。生产环境中应该使用 Schema 注册中心（如 Hive Metastore）或在 ETL 中强制统一类型。

### 4.4 常见踩坑经验

### 4.4 常见踩坑经验

1. **Parquet 文件用 Excel 打开乱码**：一位同事把 `.parquet` 文件当 Excel 发给运营——运营懵了。教训：Parquet 是给程序读的，给人类看的用 XLSX/CSV。
2. **分区列名为中文导致 Windows 兼容问题**：`orders_parquet/城市=北京/` 在 Windows 和 Linux 之间移动时路径兼容性差。建议分区列使用英文或拼音。
3. **filters 中 == None 和 isna 的区别**：`filters=[('col', '==', None)]` 在 PyArrow 中不生效。用 `filters=[('col', 'is', None)]` 或读入后用 `dropna`。

### 4.5 思考题

1. 如果数据本身没有自然的分区字段（如没有日期列），还可以用什么策略做分区？有哪些替代方案？
2. `df.to_parquet('data.parquet', row_group_size=10000)` 中的 `row_group_size` 是什么？它如何影响查询性能？

（答案将在第 23 章附录中给出）

### 4.6 推广计划提示

- **数据工程师**：将 Parquet 作为数据管道中间格式的默认选择，CSV 仅用于初始采集；
- **架构师**：评估 Arrow-backed dtype 对团队技术栈的影响——pandas→PyArrow→其他 Arrow 兼容系统；
- **运维**：关注 Parquet 存储的压缩比和分区数量，定期做小文件合并。

---

> **源码关联**：pandas/io/parquet.py、pandas/core/arrays/arrow/array.py
