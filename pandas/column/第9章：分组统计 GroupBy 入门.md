# 第9章：分组统计 GroupBy 入门

## 1. 项目背景

某连锁快餐品牌在全国 50 个城市有 200 家门店，运营总监每周需要一份"门店经营健康度报告"，核心指标包括：各门店销售额、客单价、订单数、复购率、各品类销售额排名、以及 Top 10 热销单品。数据来自 POS 系统（point_of_sale.csv），每周约 30 万条交易记录，每条记录包含：订单号、交易时间、门店 ID、城市、商品名称、品类、数量、单价、实付金额。

运营分析师小刘每周一上午的手工流程是：

**痛点一：Excel 透视表性能瓶颈**。30 万行数据在 Excel 里做透视表，每次切换行列维度都要等 30 秒以上。当他需要同时看"城市×品类×周"三维指标时，Excel 的字段列表已经卡到几乎没有响应。

**痛点二：分组统计逻辑分散**。"每家门店的复购率"需要先按门店分组计数访问次数≥2 的用户数，再除以总用户数——这是两个 GroupBy 操作的组合，Excel 根本无法一步完成。小刘只能先把数据导出再导入另一个 Sheet，用 COUNTIFS 嵌套公式——公式复杂到他自己两周后都看不懂。

**痛点三：分组结果二次计算繁琐**。总监临时要求看"各品类销售额的环比增长率"，小刘需要在 Excel 里手动做两次透视表（本周和上周），再手工除——一个小数点错位就会让增长率翻倍。

痛点流程：

```
POS 交易 (30万条)
  ├── 透视表 → 30秒响应 → 卡顿
  ├── 复购率 → 两个透视表 + COUNTIFS → 不可维护
  └── 环比增长 → 手工复制上周数据 → 易出错
```

本章将系统讲解 pandas 的 GroupBy 机制——split-apply-combine 模型、groupby/agg/transform/filter 的差异、以及如何用命名聚合构建清晰的多指标体系。

## 2. 项目设计：剧本式交锋对话

> 场景：小刘在茶水间抱怨"Excel 透视表太慢了"，小胖立刻凑了上来。

**小胖**：（端着泡面碗）"透视表慢？多简单啊——用 Python 不就好了！我知道有个方法叫 `groupby`，就是把数据按'门店'分堆，然后每堆求和，对吧？"

**大师**："小胖这次方向对了，但只说了一半。pandas 的 GroupBy 遵循 **split-apply-combine** 三阶段模型：

1. **Split（分拆）**：按分组键把数据拆成多个子 DataFrame——'门店 A'一堆，'门店 B'一堆……
2. **Apply（应用）**：对每个子 DataFrame 执行计算——求和、平均、计数或自定义函数
3. **Combine（组合）**：把每组的计算结果拼成一张结果表

这就是为什么它叫 'GroupBy'——分组然后应用计算。"

**【技术映射：split-apply-combine = 食堂分餐——按班级分桌（split）→ 每桌数人数（apply）→ 汇总出勤表（combine）】**

**小白**："那 `groupby.agg` 和 `groupby.transform` 有什么区别？我以前一直搞混。"

**大师**："这是 GroupBy 三个核心方法最重要的区分：

```python
df.groupby('city')['amount']

# 1. agg：每组返回一个标量 → 结果的行数 = 分组数
#    求每城市的平均金额 → 50 个城市 → 结果 50 行
df.groupby('city')['amount'].agg('mean')

# 2. transform：每组返回与原始组相同长度的结果 → 结果行数 = 原始行数
#    给每条记录打上它所在城市的平均金额 → 30 万行 → 结果 30 万行
df.groupby('city')['amount'].transform('mean')

# 3. filter：根据每组条件保留或丢弃整组
#    只保留销售额 > 10000 的城市的所有记录
df.groupby('city').filter(lambda x: x['amount'].sum() > 10000)
```

简单记法：
- `agg`：**缩减**维度（每组的汇总值）
- `transform`：**广播**回去（原数据的每行对应一个组统计值）
- `filter`：**过筛**整组（不符合条件的组全扔掉）
"

**【技术映射：agg = 每桌结算总价，transform = 给每桌每个人发"你们桌平均消费是XX元"的小票】**

**小胖**："那一次想算好几个指标怎么办？比如销售额、客单价、订单数三个指标？"

**大师**："三种写法，从简单到灵活：

```python
# 写法1：传入函数名字符串列表
df.groupby('门店')['实付金额'].agg(['sum', 'mean', 'count'])

# 写法2：传入元组 (新列名, 聚合函数)
df.groupby('门店').agg(
    销售额=('实付金额', 'sum'),
    客单价=('实付金额', 'mean'),
    订单数=('实付金额', 'count')
)

# 写法3：传入字典（同一列不同函数 + 不同列不同函数）
df.groupby('门店').agg({
    '实付金额': ['sum', 'mean'],
    '订单号': 'nunique'  # 去重计数
})
```

写法 2 最推荐——列名有意义，结果可以直接用。注意 agg 中的 'count' vs 'nunique'：count 统计非空行的行数，nunique 统计不重复的值个数。"

**【技术映射：agg 的多种写法 = 点餐方式——可以说'老样子'（函数名字符串），也可以说'少盐多辣'（自定义命名）】**

**小白**："那 `groupby.apply` 呢？好像很万能但我听说很慢？"

**大师**："`apply` 是最灵活但也是最慢的 GroupBy 操作。它把每个分组作为完整 DataFrame 传给一个自定义 Python 函数——因为是在 Python 层面逐组调用，不像 `agg('sum')` 走的是 C/Cython 路径。能用 `agg` 解决的绝不用 `apply`。

举个例子——你想给每个门店的用户按消费金额排名：

```python
# ❌ apply 版（慢）
df.groupby('门店').apply(lambda x: x.sort_values('实付金额', ascending=False))

# ✅ transform + sort_values 版（快得多）
df['rank_in_store'] = df.groupby('门店')['实付金额'].rank(ascending=False)
df.sort_values(['门店', 'rank_in_store'])
```

`transform` + `rank` 走的是 Cython 路径，apply 是 Python 路径，性能差 5-20 倍。"

**小胖**："那 `as_index` 和 `observed` 这两个参数呢？我经常看到但从来不改。"

**大师**："这两个参数在生产中非常重要：

- **`as_index=False`**：分组键不作为 Index 而是作为普通列。我个人习惯始终设为 False，避免后续操作中 Index 混乱；
- **`observed=True`**：当分组键是 category 类型时，只显示数据中实际出现过的类别；`observed=False` 会显示所有类别的全部组合（可能产生大量 0 值行）。对 category 列，`observed=True` 几乎总是你想要的；
- **`dropna=True`**：默认行为——忽略分组键为 NA 的组。如果你希望 NA 也作为一个独立分组，传入 `dropna=False`。"

**【技术映射：observed = 考试报名表——只统计实际参加考试的学生（True），还是把报名了但没来的也算上（False）】**

**大师总结**："GroupBy 是 pandas 最有价值也最易用错的功能。核心记住 split-apply-combine 模型，区分 agg/transform/filter 三个输出维度，你会发现自己 80% 的分析需求三行代码就能搞定。"

## 3. 项目实战

### 3.1 环境准备

```bash
pip install pandas numpy
```

### 3.2 模拟 POS 交易数据

```python
# generate_pos_data.py
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

np.random.seed(42)
N = 50000  # 5 万条交易（模拟 30 万场景）

cities = [f'城市{i:02d}' for i in range(1, 51)]
stores = [f'门店-{c}-{i:02d}' for c in cities for i in range(1, 5)][:200]
categories = ['汉堡', '炸鸡', '饮品', '甜品', '小食']
products = {
    '汉堡': ['经典牛肉堡','鸡肉堡','双层芝士堡'],
    '炸鸡': ['原味鸡','香辣鸡翅','鸡米花'],
    '饮品': ['可乐','柠檬茶','咖啡','奶昔'],
    '甜品': ['冰淇淋','苹果派','蛋挞'],
    '小食': ['薯条','玉米棒','鸡块'],
}

data = []
base_date = datetime(2025, 3, 10)
for i in range(N):
    order_id = f'ORD{20000000 + i:08d}'
    store = np.random.choice(stores)
    city = store.split('-')[0]
    ts = base_date + timedelta(
        days=np.random.randint(0, 7),
        hours=np.random.randint(8, 22),
        minutes=np.random.randint(0, 60)
    )
    user_id = np.random.randint(100000, 200000)
    n_items = np.random.randint(1, 4)
    total = 0.0

    for _ in range(n_items):
        cat = np.random.choice(categories)
        prod = np.random.choice(products[cat])
        qty = np.random.randint(1, 4)
        price = np.random.choice([9.9, 12.5, 15.0, 18.0, 22.0, 25.0, 32.0, 38.0])
        amount = round(qty * price, 2)
        total += amount
        data.append({
            'order_id': order_id,
            'trade_time': ts.strftime('%Y-%m-%d %H:%M'),
            'store_id': store,
            'city': city,
            'user_id': user_id,
            'product': prod,
            'category': cat,
            'quantity': qty,
            'unit_price': price,
            'amount': amount,
        })
    # 最后一个商品行记录实际支付总额（可能有优惠）
    data[-1]['amount'] = round(total * np.random.uniform(0.85, 1.0), 2)

df = pd.DataFrame(data)
df.to_csv('point_of_sale.csv', index=False, encoding='utf-8-sig')
print(f"已生成 {len(df)} 条交易记录, {df['order_id'].nunique()} 个订单")
```

### 3.3 分步实现

#### 步骤 1：split-apply-combine 入门体验

**目标**：理解 GroupBy 的三个阶段和基本聚合方法。

```python
# step1_groupby_basics.py
import pandas as pd

df = pd.read_csv('point_of_sale.csv', parse_dates=['trade_time'])

# === 单键分组 + 单指标 ===
print("===== 各门店销售额 Top 5 =====")
sales_by_store = df.groupby('store_id')['amount'].sum()
print(sales_by_store.sort_values(ascending=False).head())

# === 单键分组 + 多指标 ===
print("\n===== 各品类销售统计 =====")
category_stats = df.groupby('category')['amount'].agg(['count', 'sum', 'mean', 'std'])
category_stats.columns = ['交易笔数', '总销售额', '平均金额', '标准差']
print(category_stats.round(2).to_string())

# === 多键分组 ===
print("\n===== 门店×品类 交叉销售 =====")
store_cat = df.groupby(['store_id', 'category'])['amount'].sum().unstack(fill_value=0)
print(store_cat.head().to_string())
```

#### 步骤 2：agg 多指标命名聚合

**目标**：用命名聚合构建清晰的多指标体系。

```python
# step2_named_agg.py
import pandas as pd

df = pd.read_csv('point_of_sale.csv', parse_dates=['trade_time'])

# 按门店汇总多指标
store_kpi = df.groupby('store_id', as_index=False).agg(
    交易笔数=('order_id', 'nunique'),       # 去重订单数
    销售额=('amount', 'sum'),
    客单价=('amount', 'mean'),
    商品种类=('product', 'nunique'),        # 卖了多少种商品
    用户数=('user_id', 'nunique'),          # 多少独立用户
).round(2)

# 添加排名
store_kpi['销售额排名'] = store_kpi['销售额'].rank(ascending=False).astype(int)
print("===== 门店 KPI 看板 (Top 5) =====")
print(store_kpi.sort_values('销售额', ascending=False).head(10).to_string(index=False))

# 按品类汇总（含占比）
cat_kpi = df.groupby('category', as_index=False).agg(
    总销售额=('amount', 'sum'),
    交易笔数=('amount', 'count'),
)
cat_kpi['销售占比'] = (cat_kpi['总销售额'] / cat_kpi['总销售额'].sum() * 100).round(2)
print("\n===== 品类销售占比 =====")
print(cat_kpi.sort_values('总销售额', ascending=False).to_string(index=False))
```

#### 步骤 3：transform 派生组级指标

**目标**：用 transform 给每条记录加上组统计值。

```python
# step3_transform.py
import pandas as pd

df = pd.read_csv('point_of_sale.csv', parse_dates=['trade_time'])

# === transform：给每条记录加上门店均值/排名 ===
# 每条记录的金额占所在门店总销售额的比例
df['store_total'] = df.groupby('store_id')['amount'].transform('sum')
df['amount_pct'] = (df['amount'] / df['store_total'] * 100).round(2)

# 每条记录在所在门店的销售排名
df['rank_in_store'] = df.groupby('store_id')['amount'].transform('rank', ascending=False)

print("===== 各门店最优销售记录 =====")
print(df[df['rank_in_store'] == 1][['store_id','product','amount','amount_pct']].head(10).to_string(index=False))

# === transform vs agg 的差异 ===
# agg 结果行数 = 门店数
agg_result = df.groupby('store_id')['amount'].agg('sum')
print(f"\nagg 结果行数: {len(agg_result)} (等于门店数)")

# transform 结果行数 = 原始数据行数
transform_result = df.groupby('store_id')['amount'].transform('sum')
print(f"transform 结果行数: {len(transform_result)} (等于原始行数)")
```

#### 步骤 4：复购率计算（组合 GroupBy 操作）

**目标**：计算复购率——同一用户访问≥2 次的比例。

```python
# step4_repurchase_rate.py
import pandas as pd

df = pd.read_csv('point_of_sale.csv', parse_dates=['trade_time'])

# 复购率 = (访问次数≥2的用户数) / (总独立用户数)
# 按门店计算

# 第一步：每个用户在每个门店的访问次数
user_store_visits = (
    df.groupby(['store_id', 'user_id'], as_index=False)
    .agg(访问次数=('order_id', 'nunique'))
)

# 第二步：按门店统计
repurchase = (
    user_store_visits
    .groupby('store_id')
    .agg(
        总用户数=('user_id', 'nunique'),
        复购用户数=('访问次数', lambda x: (x >= 2).sum()),
    )
)
repurchase['复购率'] = (repurchase['复购用户数'] / repurchase['总用户数'] * 100).round(2)

print("===== 门店复购率 Top 10 =====")
print(repurchase.sort_values('复购率', ascending=False).head(10).to_string())

# 全品牌复购率
total_users = user_store_visits['user_id'].nunique()
repeat_users = (user_store_visits.groupby('user_id')['访问次数'].sum() >= 2).sum()
print(f"\n全品牌复购率: {repeat_users/total_users*100:.2f}%")
```

#### 步骤 5：环比增长率计算

**目标**：计算各品类本周 vs 上周的销售额环比增长率。

```python
# step5_wow_growth.py
import pandas as pd

df = pd.read_csv('point_of_sale.csv', parse_dates=['trade_time'])

# 按日期提取周标签
df['week_label'] = df['trade_time'].dt.isocalendar().week.astype(str)

# 每周各品类销售额
weekly = df.groupby(['category', 'week_label'], as_index=False)['amount'].sum()

# 透视：行=品类，列=周
weekly_pivot = weekly.pivot(index='category', columns='week_label', values='amount')

# 计算环比增长率（假设 data 中有连续两周）
weeks = sorted(weekly_pivot.columns)
if len(weeks) >= 2:
    this_week, last_week = weeks[-1], weeks[-2]
    weekly_pivot['环比增长率'] = (
        (weekly_pivot[this_week] - weekly_pivot[last_week])
        / weekly_pivot[last_week] * 100
    ).round(2)
    print(f"===== 品类环比增长率 ({last_week}→{this_week}) =====")
    print(weekly_pivot[[last_week, this_week, '环比增长率']].to_string())
```

### 3.4 完整主流程

```python
# main_kpi_report.py
"""门店经营健康度报告主流程"""
import pandas as pd

df = pd.read_csv('point_of_sale.csv', parse_dates=['trade_time'])

# 1. 门店 KPI 看板
store_kpi = df.groupby('store_id', as_index=False).agg(
    订单数=('order_id', 'nunique'),
    销售额=('amount', 'sum'),
    客单价=('amount', 'mean'),
    用户数=('user_id', 'nunique'),
).round(2)
store_kpi['销售额排名'] = store_kpi['销售额'].rank(ascending=False).astype(int)

# 2. 品类表现
category_kpi = df.groupby('category', as_index=False).agg(
    销售额=('amount', 'sum'),
    占比=('amount', lambda x: x.sum() / df['amount'].sum() * 100)
).round(2)

# 3. 复购率
user_visits = df.groupby(['store_id', 'user_id'])['order_id'].nunique().reset_index()
repurchase = user_visits.groupby('store_id').agg(
    总用户=('user_id', 'nunique'),
    复购用户=('order_id', lambda x: (x >= 2).sum())
)
repurchase['复购率%'] = (repurchase['复购用户'] / repurchase['总用户'] * 100).round(2)

# 4. 热销单品 Top 10
top_products = df.groupby('product').agg(
    销量=('amount', 'sum'),
    订单数=('order_id', 'nunique')
).nlargest(10, '销量').reset_index()

# 5. 导出多 Sheet 报告
with pd.ExcelWriter('门店经营健康度报告.xlsx', engine='openpyxl') as writer:
    store_kpi.to_excel(writer, sheet_name='门店KPI', index=False)
    category_kpi.to_excel(writer, sheet_name='品类表现', index=False)
    repurchase.to_excel(writer, sheet_name='复购率')
    top_products.to_excel(writer, sheet_name='热销Top10', index=False)

print("✓ 门店经营健康度报告已导出")
print(f"  门店数: {store_kpi['store_id'].nunique()}")
print(f"  总销售额: {store_kpi['销售额'].sum():.2f}")
print(f"  平均客单价: {store_kpi['客单价'].mean():.2f}")
```

### 3.5 常见坑及解决方法

| 问题 | 原因 | 解决方法 |
|------|------|----------|
| GroupBy 后结果有 MultiIndex 列 | 传入了列表形式的聚合函数如 `['sum','mean']` | 取一层：`result.columns = result.columns.droplevel()` 或使用命名聚合 |
| `transform` 返回多个值报错 | transform 要求每组返回与输入相同长度的结果 | 检查自定义函数返回值长度 |
| `groupby.apply` 第一组执行两次 | pandas 内部优化需要测试函数行为 | 这是正常行为，不依赖副作用即可 |
| `as_index=True` 后用 `loc` 取不到值 | 分组键变成了 Index | 加 `as_index=False` 或 `reset_index()` |
| `nunique` 比 `count` 小很多 | `count` 统计行数，`nunique` 去重 | 根据需求选择合适的聚合函数 |
| category 分组 `observed=False` 输出大量 0 | 未出现的组合也输出 | 设置 `observed=True` |

### 3.6 测试验证

```python
# test_ch09.py
import pandas as pd
import numpy as np

def test_groupby_agg():
    df = pd.DataFrame({'cat': ['A','A','B','B','C'], 'val': [1,2,3,4,5]})
    result = df.groupby('cat')['val'].agg('sum')
    assert result['A'] == 3
    assert result['B'] == 7

def test_groupby_transform():
    df = pd.DataFrame({'cat': ['A','A','B','B'], 'val': [1,3,5,7]})
    df['mean_by_cat'] = df.groupby('cat')['val'].transform('mean')
    assert df['mean_by_cat'].tolist() == [2.0, 2.0, 6.0, 6.0]

def test_groupby_filter():
    df = pd.DataFrame({'cat': ['A','A','B','B'], 'val': [1,2,10,20]})
    # 只保留组内和大于 5 的组
    result = df.groupby('cat').filter(lambda x: x['val'].sum() > 5)
    assert set(result['cat']) == {'B'}

def test_named_agg():
    df = pd.DataFrame({'g': ['a','a','b'], 'v1': [10,20,30], 'v2': [1,2,3]})
    result = df.groupby('g', as_index=False).agg(
        sum_v1=('v1', 'sum'),
        mean_v2=('v2', 'mean')
    )
    assert result.loc[result['g']=='a', 'sum_v1'].iloc[0] == 30
    assert result.loc[result['g']=='b', 'mean_v2'].iloc[0] == 3.0

def test_nunique_vs_count():
    df = pd.DataFrame({'g': ['a','a','a'], 'id': [1, 1, 2]})
    agg = df.groupby('g').agg(count=('id','count'), unique=('id','nunique'))
    assert agg['count'].iloc[0] == 3
    assert agg['unique'].iloc[0] == 2

if __name__ == '__main__':
    test_groupby_agg()
    test_groupby_transform()
    test_groupby_filter()
    test_named_agg()
    test_nunique_vs_count()
    print("✓ 所有测试通过")
```

**完整代码清单**：参见专栏配套仓库 `column/code/ch09/`。

## 4. 项目总结

### 4.1 优点 & 缺点

| 维度 | pandas GroupBy | Excel 透视表 | SQL GROUP BY |
|------|---------------|-------------|-------------|
| 灵活性 | agg/transform/filter 三种输出维度 | 只有汇总维度 | 只有汇总 + 窗口函数 |
| 自定义聚合 | 任意 Python/NumPy 函数 | 有限的内置函数 | 有限的内置聚合函数 |
| 性能 | 中小数据极快（Cython） | 逐行操作慢 | 有索引时极快 |
| 可复现 | 代码脚本化 | 手工操作 | SQL 脚本化 |
| 多表联动 | pipeline 模式直接衔接 | 需导出再导入 | 需子查询/CTE |
| 内存使用 | 全部在内存中 | 磁盘 + 内存混合 | 数据库引擎管理 |

### 4.2 适用场景

- **适用场景**：
  1. 按维度（时间、地区、品类、渠道）汇总业务指标
  2. 构建业务 KPI 看板：销售额、客单价、转化率、复购率
  3. 用 transform 给明细数据打上组统计标签（如'高于/低于门店均值'）
  4. 计算分组内的排名、累计值、同比环比
  5. 作为进一步分析（透视、merge、导出）的中间步骤
- **不适用场景**：
  1. 只需要整表统计（mean/sum 等）不需要分组——直接用 `df.mean()` 更快
  2. 分组键过多导致每组只有 1-2 条记录——分组开销大于收益

### 4.3 注意事项

- **groupby.apply 的性能陷阱**：apply 是 Python 层面逐个分组调用，对大分组数（如数千个门店）会非常慢。优先考虑 agg/transform；
- **分组键的 dtype 影响**：category 类型分组比 object 类型快——因为底层用整数编码比较；
- **groupby 后的 Index**：不设 `as_index=False` 时结果为 MultiIndex，使用 `.reset_index()` 可转为普通列；
- **dropna 的默认值**：pandas 2.0+ 默认为 True，分组键有 NA 值时忽略；如果 NA 有意义，设置 `dropna=False`。

### 4.4 常见踩坑经验

1. **用 `agg('count')` 统计去重的坑**：一位同事用 `df.groupby('city')['user_id'].agg('count')` 统计各城市用户数，结果比实际大了 3 倍——因为同一个用户可能在多条交易记录中出现。正确做法是 `agg('nunique')`。
2. **transform 函数返回长度不匹配**：`df.groupby('g')['v'].transform(lambda x: x.iloc[0])` 会报错——transform 要求返回与输入同长度的值。如果要取每组第一个值广播，用 `transform('first')` 而非 lambda。
3. **category 列的 `observed=False` 导致结果膨胀**：一个 city 列有 50 个类别，groupby 后 `observed=False` 会输出 50 个城市的结果——包括没有交易的 30 个。教训：使用 category 作为分组键时，始终设置 `observed=True`。

### 4.5 思考题

1. `df.groupby('store_id').apply(lambda x: x.nlargest(3, 'amount'))` 和 `df.sort_values('amount').groupby('store_id').tail(3)` 有何区别？哪个性能更好？为什么？
2. 如何用 `transform` 计算每个交易记录金额在自己所属门店的百分位排名？尝试写代码并用 100 万行数据验证性能。

（答案将在第 10 章附录中给出）

### 4.6 推广计划提示

- **数据分析师/运营**：将本章的 KPI 模板脚本化，每周/月只需替换数据源即可自动更新；
- **开发**：将 `agg` + 命名聚合作为团队数据分析的标准范式，禁用裸 `groupby.apply`；
- **数据工程师**：关注 category 类型在 GroupBy 中的 `observed` 参数行为，避免结果膨胀。

---

> **源码关联**：pandas/core/groupby/groupby.py、pandas/core/groupby/generic.py
