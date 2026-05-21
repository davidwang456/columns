# 第3章：Series、DataFrame 与 Index 的基本操作

## 1. 项目背景

某在线教育平台的数据团队接到一个任务：分析用户行为日志，识别高价值潜在付费用户。产品经理希望将"过去 30 天内听了 10 节以上免费课且完成课后练习超过 5 次的用户"打上"高意向"标签，并结合用户画像（城市、年龄、注册渠道）生成营销名单。

数据分布在两张表中：
- `user_behavior.csv`：用户行为日志，包含 user_id、课程 ID、行为类型（听课/练习/分享）、行为时间；
- `user_profile.csv`：用户画像表，包含 user_id、城市、年龄、注册渠道、VIP 等级。

这个任务看似简单，但数据结构上有几个棘手的地方：

**痛点一：两张表的 user_id 不完全对应**。行为日志中有 8000 个用户，画像表中只有 6000 个用户（部分用户注册后未填写画像）。需要按 user_id 把画像"对齐"到行为表上，缺失的填"未知"。

**痛点二：错误使用 Python 循环处理**。一位同事的做法是：`for uid in behavior['user_id']: profile_row = profile[profile['user_id']==uid]`——这个二重循环在处理 8000 个用户时跑了近 15 秒，而且内存占用很高。

**痛点三：不了解 Index 对齐的含义**。当尝试对两个不同长度、不同顺序的 Series 做加法时，结果出现了意料之外的 NaN，导致统计指标全错。

```python
# 错误写法示例
s1 = pd.Series([1, 2, 3], index=[0, 1, 2])
s2 = pd.Series([4, 5, 6], index=[1, 2, 3])
# s1 + s2 → 0: NaN, 1: 6.0, 2: 8.0, 3: NaN
# 同事预期的是 [5, 7, 9]
```

这个例子揭示了 pandas 最核心也最容易被忽略的机制：**Index 驱动的自动对齐**。本章将深入 Series、DataFrame 和 Index 的基本操作，建立正确的数据模型认知。

## 2. 项目设计：剧本式交锋对话

> 场景：代码评审会上，大师打开了一段"for 循环嵌套"的代码，引发了讨论。

**小胖**：（盯着屏幕）"大师，这段代码怎么了？for 循环哪里不对？这我写的！我觉得很清楚啊——遍历每个用户，找到他的画像，拼起来。跟查字典一样。"

**大师**："比喻没错，但是做法错了。你是一页页翻字典查，pandas 是给你一本倒排索引——`user_id` 就是那个索引。你用 `profile.set_index('user_id')` 之后，pandas 内部会建立一个哈希表，查找任何一个 user_id 的时间复杂度是 O(1)，而不是你的 O(n)。"

**小白**："等等，我一直没搞懂——Index 到底是什么？和普通的列有什么区别？"

**大师**：（走到白板前画了一个两列表格）"DataFrame 有两类'名字'：

**列名（column names）**：决定每一'列'是什么，比如 'user_id'、'amount'、'city'；
**行索引（row index / Index）**：决定每一'行'怎么被定位和访问，比如行号 0、1、2……或者有意义的标签如用户 ID。

Index 和普通列的关键区别在于：
- Index 是**不可变的**（immutable），你不能直接修改索引标签；
- Index 是**查找的入口**，`loc` 基于它做标签查找；
- Index 是**对齐的依据**——两个 Series 运算时，是按 Index 标签匹对的，而不是按位置。
"

**小胖**："那是不是所有表都要设一个 Index？用默认的 0、1、2……行不行？"

**大师**："默认的 `RangeIndex`（整数序列）在很多场景下够了。但当你需要按某个业务 ID 做关联、查找、或去重时，把它设为 Index 能大幅提升代码的可读性和性能。"

**【技术映射：Index = 图书馆索书号——你可以按书架位置找书（按位置），也可以按索书号精确查找（按标签）】**

**小白**："那 `loc` 和 `iloc` 到底怎么分？我总是搞混。"

**大师**："记住一个口诀：**loc = label（标签定位），iloc = integer location（整数位置定位）**。

```python
df = pd.DataFrame({'A': [10, 20, 30]}, index=['x', 'y', 'z'])

df.loc['y', 'A']    # → 20  用标签'y'定位
df.iloc[1, 0]       # → 20  用位置(第1行,第0列)定位
df.loc[1, 'A']      # → 报错！因为没有标签为1的行
```

更微妙的是切片行为：
- `loc` 的切片是**包含两端**的（闭区间）；
- `iloc` 的切片是**左闭右开**的（和 Python 列表一致）。"

**小胖**："这谁记得住啊！为啥不统一？"

**大师**："因为 `loc` 用的是标签——标签没有'下一个标签'的自然定义，包含两端更符合直觉。`iloc` 用的是整数位置，继承 Python 的切片语义保持一致。"

**【技术映射：loc = 点名找人（"3号到5号，3号和5号都出来"），iloc = 排队报数（"第3到第5个，不包括第5个"）】**

**小白**："那对齐机制，能不能再解释一下？我总觉得凭空冒出来 NaN 很吓人。"

**大师**："这就是 pandas 最核心的设计哲学之一：**数据表达是带标签的，运算基于标签对齐**。

举个例子——你有两个班级的数学成绩：
- 一班：{'小明': 85, '小红': 90, '小刚': 78}
- 二班：{'小红': 92, '小刚': 80, '小丽': 88}

问题是：全年级的数学平均分是多少？你当然应该按学生姓名对齐再加总——二班的'小丽'在一班里没有成绩，不应该参与平均。

pandas 就是这么干的：

```python
s1 = pd.Series([85, 90, 78], index=['小明','小红','小刚'])
s2 = pd.Series([92, 80, 88], index=['小红','小刚','小丽'])
s1 + s2
# 小明     NaN (只有一班有)
# 小红   182.0
# 小刚   158.0
# 小丽     NaN (只有二班有)
```

这个默认行为能防止你误把不同人的成绩加到一起。"

**小胖**："那如果我确定两个 Series 就是完全对应的关系，不想按标签对齐怎么办？"

**大师**："用 `.values` 或 `.to_numpy()` 取出底层 NumPy 数组做运算——这就退化成位置对位置的普通数组运算了。但记住：**放弃对齐就等于放弃了 pandas 最重要的安全网**。"

**【技术映射：Index 对齐 = 按身份证号匹配信息，而不是按队列位置匹配】**

**小白**："那 axis 参数呢？axis=0 和 axis=1 到底往哪里操作？我又常搞反。"

**大师**：（在白板上画了一个 DataFrame 并标出箭头）

```
        col_A  col_B  col_C
row_0     1      2      3
row_1     4      5      6
row_2     7      8      9

axis=0 → 沿着行的方向（从上到下），即对每一列操作（drop a row, sum per column）
axis=1 → 沿着列的方向（从左到右），即对每一行操作（drop a column, sum per row）
```

简单记法：axis=0 对应 Index（行轴），axis=1 对应 columns（列轴）。`df.sum(axis=0)` 是每列求和（结果是一行），`df.sum(axis=1)` 是每行求和（结果是一列）。"

**大师总结**："今天聊的五个概念——Index 语义、loc/iloc 区分、axis 方向、自动对齐、不可变性——是 pandas 操作模型的基础设施。它们不像 groupby 那样'好用'，但理解之后你写任何代码都会更自信、更少出 bug。"

## 3. 项目实战

### 3.1 环境准备

依赖同第 2 章，确认环境中已有 `pandas>=2.0.0` 和 `numpy`。

```bash
pip install pandas numpy
```

### 3.2 模拟数据生成

**目标**：生成用户行为日志和用户画像两份数据，制造 Index 不匹配、列缺失等真实场景。

```python
# generate_user_data.py
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

np.random.seed(42)

# ===== 用户画像表 (6000 用户) =====
n_profiles = 6000
cities = ['北京', '上海', '广州', '深圳', '杭州', '成都', '武汉', '南京']
channels = ['微信', '抖音', '搜索引擎', '地推', '口碑推荐']

profiles = pd.DataFrame({
    'user_id': range(10001, 10001 + n_profiles),
    'city': np.random.choice(cities, n_profiles),
    'age': np.random.choice(['18-25', '26-35', '36-45', '46+'], n_profiles, p=[0.3, 0.4, 0.2, 0.1]),
    'channel': np.random.choice(channels, n_profiles),
    'vip_level': np.random.randint(0, 4, n_profiles)
})

# ===== 用户行为表 (8000 用户，部分用户无画像) =====
n_behavior_users = 8000
behavior_user_ids = range(10001, 10001 + n_behavior_users)

n_records = 30000
records = []
base_date = datetime(2025, 2, 15)

for _ in range(n_records):
    uid = np.random.choice(behavior_user_ids)
    event_type = np.random.choice(['听课', '练习', '分享', '浏览'], p=[0.5, 0.3, 0.1, 0.1])
    days_ago = np.random.randint(0, 30)
    ts = base_date - timedelta(days=days_ago)
    records.append({
        'user_id': uid,
        'event_type': event_type,
        'event_time': ts.strftime('%Y-%m-%d %H:%M:%S'),
        'course_id': f'C{np.random.randint(1, 50):03d}'
    })

behavior = pd.DataFrame(records)
behavior = behavior.sort_values(['user_id', 'event_time']).reset_index(drop=True)

profiles.to_csv('user_profile.csv', index=False, encoding='utf-8-sig')
behavior.to_csv('user_behavior.csv', index=False, encoding='utf-8-sig')

print(f"用户画像: {len(profiles)} 条，用户行为: {len(behavior)} 条")
print(f"行为表唯一用户: {behavior['user_id'].nunique()}，画像表唯一用户: {profiles['user_id'].nunique()}")
print(f"仅存在行为表中而无画像的用户数: {behavior['user_id'].nunique() - profiles['user_id'].nunique()}")
```

### 3.3 分步实现

#### 步骤 1：创建 Series 和 DataFrame，理解 Index 操作

**目标**：掌握 Series/DataFrame 的创建、Index 操作、以及 loc/iloc 的区别。

```python
# step1_basics.py
import pandas as pd
import numpy as np

# ----- 创建 Series -----
s = pd.Series([88, 92, 76, 95], index=['小明', '小红', '小刚', '小丽'], name='数学成绩')
print("Series 创建：")
print(s)
print(f"Index: {s.index.tolist()}")
print(f"Values: {s.values}")
print(f"Name: {s.name}")

# ----- 通过列指定的 Index 创建 DataFrame -----
df = pd.DataFrame({
    '数学': [88, 92, 76, 95],
    '语文': [85, 89, 72, 91],
}, index=['小明', '小红', '小刚', '小丽'])
print("\nDataFrame 创建：")
print(df)

# ----- loc vs iloc -----
print("\nloc 标签访问 '小红' 的数学成绩:", df.loc['小红', '数学'])
print("iloc 位置访问第2行第1列:", df.iloc[1, 0])

print("\nloc 切片 (包含两端):")
print(df.loc['小红':'小刚'])  # 包含'小红'、'小刚'

print("\niloc 切片 (左闭右开):")
print(df.iloc[1:3])  # 包含第1行(小红)，不包含第3行(小丽)

# ----- Index 不可变性 -----
try:
    df.index[0] = '大胖'
except TypeError as e:
    print(f"\nIndex 不可变验证: {e}")
    print("正确做法: df.rename(index={'小明': '大胖'}, inplace=True)")

# ----- 重置和设置 Index -----
print("\n重置 Index:")
df2 = s.reset_index()
df2.columns = ['姓名', '成绩']
print(df2)

print("\n设置列作为 Index:")
df3 = df2.set_index('姓名')
print(df3)
```

预期输出摘要：

```
Series 创建：
小明    88
小红    92
小刚    76
小丽    95
Name: 数学成绩, dtype: int64
Index: ['小明', '小红', '小刚', '小丽']

loc 标签访问 '小红' 的数学成绩: 92
iloc 位置访问第2行第1列: 92

loc 切片 (包含两端):
     数学  语文
小红   92   89
小刚   76   72

Index 不可变验证: Index does not support mutable operations
```

#### 步骤 2：用 Index 对齐补全用户画像

**目标**：将用户画像"对齐"到行为数据，缺失用户填"未知"。

```python
# step2_alignment.py
import pandas as pd

behavior = pd.read_csv('user_behavior.csv')
profiles = pd.read_csv('user_profile.csv')

# ----- 方法一：set_index + align -----
# 将 user_id 设为 Index
bp = behavior.set_index('user_id')
pp = profiles.set_index('user_id')

# align 自动对齐 Index，缺失填 NaN
bp_aligned, pp_aligned = bp.align(pp, join='left', axis=0)
print("对齐后: bp_aligned 行数 =", len(bp_aligned), ", pp_aligned 行数 =", len(pp_aligned))

# 查看无画像的用户
missing_mask = pp_aligned['city'].isna()
print(f"画像缺失用户数: {missing_mask.sum()}")

# ----- 方法二：map（针对单列补全）-----
print("\n用 map 补全单个字段：")
city_map = profiles.set_index('user_id')['city'].to_dict()
behavior['city'] = behavior['user_id'].map(city_map).fillna('未知')
print(f"city 字段填充后，'未知'数量: {(behavior['city'] == '未知').sum()}")

# ----- 方法三：merge（推荐的生产写法）-----
print("\n用 merge 关联画像：")
result = behavior.merge(
    profiles, on='user_id', how='left', indicator=True
)
right_only = (result['_merge'] == 'right_only').sum()
left_only = (result['_merge'] == 'left_only').sum()
print(f"仅左表: {left_only} 条, 仅右表: {right_only} 条, 两表共存: {(result['_merge']=='both').sum()} 条")

# 对比三种方法
import time
n_test = 100

# for 循环法（反面教材）
def for_loop_join(behavior, profiles):
    result = []
    for _, brow in behavior.iterrows():
        uid = brow['user_id']
        match = profiles[profiles['user_id'] == uid]
        if not match.empty:
            result.append({**brow.to_dict(), **match.iloc[0].to_dict()})
        else:
            result.append(brow.to_dict())
    return pd.DataFrame(result)

start = time.perf_counter()
df_for = for_loop_join(behavior.head(1000), profiles)
print(f"\nfor 循环 (1000行): {time.perf_counter() - start:.3f}s")

start = time.perf_counter()
df_merge = behavior.head(1000).merge(profiles, on='user_id', how='left')
print(f"merge (1000行): {time.perf_counter() - start:.3f}s")
```

#### 步骤 3：axis 操作实战

**目标**：理解 axis=0 和 axis=1 在各类方法中的语义。

```python
# step3_axis.py
import pandas as pd
import numpy as np

# 创建一个小型 DataFrame
df = pd.DataFrame({
    'A': [1, 2, 3],
    'B': [4, 5, 6],
    'C': [7, 8, np.nan]
}, index=['x', 'y', 'z'])
print("原始 DataFrame:")
print(df)

print("\naxis=0 (沿行方向 → 每列操作):")
print("sum(axis=0):", df.sum(axis=0).tolist(), "  # 每列求和")

print("\naxis=1 (沿列方向 → 每行操作):")
print("sum(axis=1):", df.sum(axis=1).tolist(), "  # 每行求和")

print("\ndrop axis=0 (删除行):")
print(df.drop('y', axis=0))

print("\ndrop axis=1 (删除列):")
print(df.drop('B', axis=1))

# 记忆法验证
print("\nconcat axis=0 (纵向拼接，行数增加):")
print(pd.concat([df, df], axis=0).shape, "→ 行数翻倍")

print("concat axis=1 (横向拼接，列数增加):")
print(pd.concat([df, df], axis=1).shape, "→ 列数翻倍")
```

### 3.4 完整项目脚本

```python
# main_analysis.py
"""用户高意向标签识别脚本"""
import pandas as pd

# 1. 读取数据，设置 Index
behavior = pd.read_csv('user_behavior.csv', parse_dates=['event_time'])
profiles = pd.read_csv('user_profile.csv')

# 2. 按 user_id 关联画像
data = behavior.merge(profiles, on='user_id', how='left')

# 3. 填充缺失画像
data['city'] = data['city'].fillna('未知')
data['age'] = data['age'].fillna('未知')
data['channel'] = data['channel'].fillna('未知')

# 4. 计算用户行为指标（axis=1 逐行操作示例）
# 统计过去30天每个用户的听课次数和练习次数
user_stats = (
    data.groupby('user_id')['event_type']
    .value_counts()
    .unstack(fill_value=0)
    .rename_axis(columns=None)
)

# 5. 识别高意向用户
user_stats['高意向'] = (
    (user_stats.get('听课', 0) >= 10) &
    (user_stats.get('练习', 0) >= 5)
)

# 6. 关联回画像信息（按 user_id 对齐）
result = (
    profiles.set_index('user_id')
    .join(user_stats, how='inner')
)

print(f"总用户数: {len(result)}")
print(f"高意向用户数: {result['高意向'].sum()}")
print(f"高意向用户城市分布:")
print(result[result['高意向']].groupby('city').size().sort_values(ascending=False))

# 导出高意向用户名单
output = result[result['高意向']].reset_index()
output[['user_id', 'city', 'age', 'channel', 'vip_level', '听课', '练习']].to_csv(
    'high_intent_users.csv', index=False, encoding='utf-8-sig'
)
print("\n高意向用户名单已导出至 high_intent_users.csv")
```

### 3.5 测试验证

```python
# test_ch03.py
import pandas as pd

def test_series_alignment():
    """验证 Series 对齐行为"""
    s1 = pd.Series([1, 2, 3], index=['a', 'b', 'c'])
    s2 = pd.Series([4, 5, 6], index=['b', 'c', 'd'])
    result = s1 + s2
    assert result['a'] != result['a'], "'a' 应为 NaN"  # NaN != NaN
    assert result['b'] == 6
    assert result['c'] == 8
    assert result['d'] != result['d'], "'d' 应为 NaN"

def test_loc_vs_iloc():
    """验证 loc/iloc 切片差异"""
    df = pd.DataFrame({'val': [10, 20, 30, 40]}, index=[2, 4, 6, 8])
    # loc 按标签切片，包含两端
    assert df.loc[4:8, 'val'].tolist() == [20, 30, 40]
    # iloc 按位置切片，左闭右开
    assert df.iloc[1:3, 0].tolist() == [20, 30]

def test_axis_sum():
    """验证 axis 方向"""
    df = pd.DataFrame({'A': [1, 2], 'B': [3, 4]})
    assert df.sum(axis=0).tolist() == [3, 7], "axis=0 按列求和"
    assert df.sum(axis=1).tolist() == [4, 6], "axis=1 按行求和"

def test_index_immutable():
    """验证 Index 不可变性"""
    df = pd.DataFrame({'x': [1]}, index=['a'])
    try:
        df.index[0] = 'b'
        assert False, "应该抛出 TypeError"
    except TypeError:
        pass  # 预期行为

if __name__ == '__main__':
    test_series_alignment()
    test_loc_vs_iloc()
    test_axis_sum()
    test_index_immutable()
    print("✓ 所有测试通过")
```

**完整代码清单**：参见专栏配套仓库 `column/code/ch03/` 下的 `generate_user_data.py`、`step1_basics.py`、`step2_alignment.py`、`step3_axis.py`、`main_analysis.py`、`test_ch03.py`。

## 4. 项目总结

### 4.1 优点 & 缺点

| 维度 | pandas Index 对齐 | 手工 Python for 循环 | SQL JOIN |
|------|-------------------|---------------------|----------|
| 自动化程度 | 一行代码完成对齐 | 需手写嵌套循环 | 一条 SQL 完成 |
| 缺失值处理 | 自动填 NaN，可定制 | 需手动处理 | LEFT JOIN 自动 |
| 调试友好度 | 高——.align() 方法可对比结果 | 低——逻辑分散在循环中 | 中 |
| 性能 | 高（C 级别实现） | 低（Python 逐行） | 高（数据库引擎） |
| 多表操作 | align 方法支持 | 需多层嵌套 | 多表 JOIN 直观 |
| 灵活性 | 对齐后仍为 pandas 对象，可继续操作 | 灵活但繁琐 | 结果脱离数据库 |

### 4.2 适用场景

- **适用场景**：
  1. 多源数据按业务 ID（用户 ID、订单号、商品编码）关联整合
  2. 需要按 Index 标签做快速查找（`loc`）的场景
  3. 时间序列分析——DatetimeIndex 提供丰富的日期运算
  4. 数据清洗中按 Index 补全缺失字段
  5. 分层数据建模——MultiIndex 表达多维层级（后续章节展开）
- **不适用场景**：
  1. 纯数值矩阵运算——直接使用 NumPy 更高效
  2. 不需要标签语义的单表简单统计

### 4.3 注意事项

- **Index 的 set_index 与 reset_index**：`set_index` 后原来的列不再是普通列，想把它变回列需要用 `reset_index`，这个转换不是零成本的——涉及内存复制；
- **Index 重复值**：pandas 允许 Index 有重复值，但会导致 `loc` 返回多行，部分方法行为可能出乎意料。建议在设为 Index 前用 `duplicated()` 检查唯一性；
- **RangeIndex 的陷阱**：默认的整数 Index 可能让人误以为 loc 可以用整数位置访问——记住 `loc[5]` 是找标签为 `5` 的行，不是第 6 行。

### 4.4 常见踩坑经验

1. **loc 用整数标签但 Index 也是整数**：当 `df.index` 是 `[10, 20, 30]` 时，`df.loc[0]` 会报 KeyError——因为 Index 里没有标签 `0`。这类 DataFrame 建议用 `iloc` 或重置 Index。
2. **忘记 Index 对齐导致统计翻倍**：两个 DataFrame 做加法时，如果都有重复 Index 值，结果会膨胀成笛卡尔积。例如 `df1` 中 user_id=100 出现 2 次，`df2` 中出现 3 次，对齐后该 user_id 会膨胀为 6 行。务必在操作前检查 Index 唯一性。
3. **merge 的 indicator 参数被忽视**：`indicator=True` 可以在合并结果中添加 `_merge` 列，标记每行数据来源（left_only、right_only、both）。这个参数在生产中极其有价值，能帮你快速定位"为什么关联后多了/少了行"。

### 4.5 思考题

1. 假设行为表有 8000 个 user_id，画像表有 6000 个 user_id。用 `merge(left, right, on='user_id', how='inner')` 结果有多少行？用 `on='user_id', how='outer'` 呢？尝试画 Venn 图辅助理解。
2. 当 DataFrame 的 Index 有重复值时，`df.loc[duplicate_label]` 返回什么？`df.at[duplicate_label, 'col']` 呢？查阅源码理解两者的差异。

（答案将在第 4 章附录中给出）

### 4.6 推广计划提示

- **新人开发**：把 Index 对齐机制理解透彻，这是后续所有 pandas 操作的基础；
- **测试**：重点学习本章的测试用例，理解如何为 Index 操作编写确定性测试；
- **数据分析师**：掌握 `merge` + `indicator` 的数据关联质量校验方法，避免无声的数据膨胀。

---

> **源码关联**：pandas/core/generic.py、pandas/core/indexes/base.py、pandas/core/reshape/merge.py
