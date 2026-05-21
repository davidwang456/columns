# 第18章：高级 GroupBy 与业务指标体系

## 1. 项目背景

某电商平台的用户运营团队正在进行"会员精准营销"项目，核心需求是构建 **RFM 模型**（Recency 最近消费时间、Frequency 消费频次、Monetary 消费金额），将用户分为 8 个细分群体，针对不同群体制定差异化营销策略。

数据源是过去 180 天的交易明细（transactions.csv，约 200 万行），包含：user_id、order_id、order_date、category、amount。

运营分析师小方之前的做法是用 Excel 做 RFM 分析——但这是面向百万级数据的噩梦：

**痛点一：分组内计算无法一步完成**。计算"每个用户的最近消费时间"需要按 user_id 分组取 order_date 的最大值——这是 groupby + agg。计算"每个用户的消费频次"是同一个 groupby。但计算"每个用户在所有用户中的 R/F/M 排名"需要另一个 groupby——Excel 根本无法一步完成这种分组嵌套的计算。

**痛点二：transform 和 agg 混淆导致错误**。小方用 `groupby('user_id').amount.mean()` 算出了"用户的每笔订单平均金额"（每人一行），但在后续分析中他需要的是"给每条订单打上该用户的平均金额"（每行一个值）。他把 agg 的结果 merge 回原表，结果因为 merge 的重复键导致了行数膨胀。

**痛点三：groupby.apply 的性能陷阱**。小方用 `apply(lambda x: x.sort_values('amount', ascending=False).head(3))` 取每个用户消费金额 Top 3 的订单——这个操作处理 200 万行耗时超过 5 分钟，因为 apply 是 Python 层面逐组调用的 lambda。

痛点流程：

```
交易明细 (200万行)
  ├── RFM → 多层 groupby → Excel 无法处理
  ├── transform vs agg → 用错导致行数膨胀
  └── apply 取 Top 3 → 5 分钟 → 性能灾难
```

本章将深入讲解高级 GroupBy——多层分组、自定义聚合、transform 的正确用法、分组内排名和累计，以及如何避免 apply 的性能陷阱。

## 2. 项目设计：剧本式交锋对话

> 场景：小方在代码评审会上展示了用 apply 实现的 RFM 代码，大师皱眉。

**小胖**：（喝着可乐）"RFM 分析嘛，就是算一下每个用户最后啥时候买的、买了多少次、花了多少钱——三个 groupby 搞定！每个写一行，然后 merge！"

**大师**："小胖说的方向对，但写法不优。三个 groupby 分别做，再 merge 三次——这是"分-分-合"模式。高级的做法是"合在一处"——**一次 groupby + 多函数聚合**直接用命名 agg 搞定 R、F、M 三个指标。"

**小白**："一次 groupby 怎么同时算 'max(date)' 和 'count()' 和 'sum(amount)'？这三个聚合函数的逻辑完全不一样啊。"

**大师**："命名聚合（named aggregation）可以给同一个分组的不同列绑定不同的聚合函数：

```python
rfm = df.groupby('user_id').agg(
    recency=('order_date', 'max'),       # 最近消费时间
    frequency=('order_id', 'nunique'),   # 订单数（去重）
    monetary=('amount', 'sum'),          # 消费总额
).reset_index()
```

一行代码同时算出三个指标。然后再对 R/F/M 分别做分组打分，就完成了 RFM 建模。"

**【技术映射：命名聚合 = 期末考试——同一张卷子（同一分组），语文算总分、数学算平均、英语算最高——一次考试出多样分数】**

**小胖**："那如果想按不同的时间窗口算 RFM 呢？比如近 30 天、30-90 天、90-180 天的 R 不一样？"

**大师**："这是多层分组 + 时间桶的经典场景。先给每条交易打上'时间桶'标签，然后对 user_id × 时间桶做分组：

```python
# 先打标签
df['period'] = pd.cut(df['days_ago'], bins=[0,30,90,180], labels=['30天','90天','180天'])

# user_id × period 双键分组
period_rfm = df.groupby(['user_id','period']).agg(
    spend=('amount','sum')
).unstack(fill_value=0)
```

这样每个用户一行四列（三个时间段的消费 + 总消费），可以用来分析'用户消费是否在衰减'。"

**【技术映射：多层分组 = 成绩单——按"班级×科目"分组——每个班级每科的平均分】**

**小白**："那 transform 呢？我之前在 agg 和 transform 之间选错了导致结果行数不对。能不能再解释一下什么时候用哪个？"

**大师**："核心判断标准看输出维度：

| 方法 | 输出行数 | 典型用途 |
|------|---------|---------|
| `agg` | = 分组数 | 汇总报表 |
| `transform` | = 原表行数 | 给每行打上组标签 |
| `apply` | 取决于函数 | 灵活(但慢) |
| `filter` | ≤ 原表行数 | 剔除整组 |

```python
# agg：输出 1 行/组
df.groupby('user_id')['amount'].agg('sum')  # 每个用户一行

# transform：输出与原始行数相同
df.groupby('user_id')['amount'].transform('sum')  # 每行 = 该用户的总消费

# 典型应用：标记"高于/低于用户自身均值"的订单
df['above_avg'] = df['amount'] > df.groupby('user_id')['amount'].transform('mean')
```

transform 的结果可以像普通列一样继续参与后续计算——不需要 merge 回来，这是它最大的便利。"

**【技术映射：agg = 每桌统计单，transform = 给每桌每人发"你们桌平均消费 XX 元"小票】**

**小胖**："那我用 apply 取每用户 Top 3 订单为啥特别慢？apply 不是很万能吗？"

**大师**："`apply` 是万能，但万能的代价是慢。`groupby.apply(func)` 会把每个分组作为一个完整 DataFrame 传给 Python 函数 func——如果 10 万个用户就是 10 万次 Python 函数调用。而 `agg('sum')` 走的是 Cython 路径，全程在 C 层一次完成。

对于'取 Top 3'这个需求，用 `groupby` + `head` 或 `rank` 替代 apply：

```python
# ❌ apply 取每用户 Top 3 (慢)
df.groupby('user_id').apply(lambda x: x.nlargest(3, 'amount'))

# ✅ rank + 布尔索引 (快 10-50 倍)
df['rank'] = df.groupby('user_id')['amount'].rank(ascending=False, method='first')
df[df['rank'] <= 3]
```

`rank` 走 Cython 路径，比 apply 的 Python 路径快得多。"

**大师总结**："高级 GroupBy 的三条进阶路径：
1. 从单键分组到多键分组——给用户行为加上时间维度
2. 从 agg 到 transform——把组级信息广播到明细行
3. 从 apply 到 rank/cumsum/cumcount——用向量化替代逐组 Python 调用

记住：**能用 agg/transform 解决的问题，绝不用 apply。**"

## 3. 项目实战

### 3.1 准备

```bash
pip install pandas numpy
```

### 3.2 模拟数据

```python
# generate_rfm_data.py
import pandas as pd
import numpy as np

np.random.seed(42)
N_USERS = 5000
N_TRANSACTIONS = 80000

user_ids = np.random.randint(1, N_USERS+1, N_TRANSACTIONS)
base = pd.Timestamp('2025-03-31')

data = []
for uid in user_ids:
    days_ago = np.random.randint(0, 180)
    ts = base - pd.Timedelta(days=days_ago)
    data.append({
        'user_id': uid,
        'order_id': f'ORD{np.random.randint(800000,999999)}',
        'order_date': ts.strftime('%Y-%m-%d'),
        'category': np.random.choice(['服装','电子','食品','家居'], p=[0.3,0.25,0.25,0.2]),
        'amount': round(np.random.exponential(200), 2),
    })

df = pd.DataFrame(data)
df.to_csv('transactions.csv', index=False, encoding='utf-8-sig')
print(f"已生成: {N_USERS} 用户, {len(df)} 条交易")
```

### 3.3 分步实现

#### 步骤 1：命名聚合构建 RFM

**目标**：一次 groupby 同时计算 R、F、M 三个指标。

```python
# step1_rfm.py
import pandas as pd

df = pd.read_csv('transactions.csv', parse_dates=['order_date'])
ref_date = pd.Timestamp('2025-03-31')

rfm = df.groupby('user_id', as_index=False).agg(
    recency=('order_date', lambda d: (ref_date - d.max()).days),
    frequency=('order_id', 'nunique'),
    monetary=('amount', 'sum'),
).round(2)

print("===== RFM 前 10 名用户 =====")
print(rfm.head(10).to_string(index=False))
print(f"\nR均值: {rfm['recency'].mean():.0f}天, F均值: {rfm['frequency'].mean():.1f}次, M均值: {rfm['monetary'].mean():.0f}元")
```

#### 步骤 2：RFM 打分与用户分层

**目标**：将 R/F/M 分别打 1-3 分，组合为 27 个细分群体。

```python
# step2_rfm_score.py
import pandas as pd

df = pd.read_csv('transactions.csv', parse_dates=['order_date'])
ref_date = pd.Timestamp('2025-03-31')

rfm = df.groupby('user_id').agg(
    recency=('order_date', lambda d: (ref_date - d.max()).days),
    frequency=('order_id', 'nunique'),
    monetary=('amount', 'sum'),
)

# 按三分位数打 1-3 分（1=最差，3=最优）
# R 越小越好（最近消费），所以 bins 反转
rfm['R'] = pd.qcut(rfm['recency'], 3, labels=[3, 2, 1]).astype(int)
rfm['F'] = pd.qcut(rfm['frequency'], 3, labels=[1, 2, 3]).astype(int)
rfm['M'] = pd.qcut(rfm['monetary'], 3, labels=[1, 2, 3]).astype(int)
rfm['RFM'] = rfm['R'].astype(str) + rfm['F'].astype(str) + rfm['M'].astype(str)

# 用户分层
def classify(rfm_code):
    if rfm_code in ['333','332','323','233']: return '重要价值'
    elif rfm_code in ['311','312','313']: return '重要唤回'
    elif rfm_code in ['133','233','333']: return '重要深耕'
    elif rfm_code in ['111','112','121','211']: return '流失'
    else: return '一般'

rfm['segment'] = rfm['RFM'].apply(classify)

print("===== 用户分层分布 =====")
print(rfm['segment'].value_counts().to_string())
```

#### 步骤 3：transform 构建用户级派生指标

**目标**：用 transform 给每笔交易打上用户级的统计标签。

```python
# step3_transform.py
import pandas as pd

df = pd.read_csv('transactions.csv', parse_dates=['order_date'])

# transform：行数不变，值 = 组内统计
df['user_avg_amount'] = df.groupby('user_id')['amount'].transform('mean').round(2)
df['user_total_amount'] = df.groupby('user_id')['amount'].transform('sum').round(2)
df['user_order_count'] = df.groupby('user_id')['order_id'].transform('nunique')

# 超出自身平均水平的订单标记
df['is_high_value'] = df['amount'] > df['user_avg_amount'] * 1.5
# 每用户的首单标记
df['is_first_order'] = df.groupby('user_id')['order_date'].transform('min') == df['order_date']

print("transform 增值列:")
print(df[['user_id','amount','user_avg_amount','is_high_value','is_first_order']].head(10).to_string(index=False))
print(f"\n高价值订单: {df['is_high_value'].sum()} ({df['is_high_value'].mean()*100:.1f}%)")
```

#### 步骤 4：分组内排名和累计

**目标**：用 rank、cumsum、cumcount 替代 apply。

```python
# step4_rank_cumsum.py
import pandas as pd
import numpy as np

df = pd.read_csv('transactions.csv', parse_dates=['order_date'])
df = df.sort_values(['user_id', 'order_date'])

# === 分组内排序排名 ===
# 每用户订单按金额排名
df['amount_rank'] = df.groupby('user_id')['amount'].rank(ascending=False, method='dense')
# 每用户订单按时间编号
df['order_seq'] = df.groupby('user_id').cumcount() + 1

# === 分组内累计 ===
df['cum_amount'] = df.groupby('user_id')['amount'].cumsum()  # 累计消费
df['cum_count'] = df.groupby('user_id').cumcount() + 1       # 累计单数

# === 每用户 Top 3 订单（替代 apply）===
df['rank'] = df.groupby('user_id')['amount'].rank(ascending=False, method='first')
top3_per_user = df[df['rank'] <= 3].sort_values(['user_id','rank'])

print("每用户 Top 3 订单（前 5 用户）:")
print(top3_per_user.groupby('user_id').head(1)[['user_id','amount','rank']].head(5).to_string(index=False))

# === 性能对比 ===
import time

# apply 方式
start = time.perf_counter()
_ = df.groupby('user_id').apply(lambda x: x.nlargest(3, 'amount'), include_groups=False)
t_apply = time.perf_counter() - start

# rank 方式
start = time.perf_counter()
_ = df[df.groupby('user_id')['amount'].rank(ascending=False, method='first') <= 3]
t_rank = time.perf_counter() - start

print(f"\napply: {t_apply:.3f}s, rank: {t_rank:.3f}s, 加速比: {t_apply/t_rank:.1f}x")
```

### 3.5 常见坑及解决方法

| 问题 | 原因 | 解决方法 |
|------|------|----------|
| apply 第一组执行两次 | 内部优化测试 | 不要依赖副作用，或改用 agg/transform |
| transform 自定义函数报错 | 返回值长度不等于输入 | 确保返回与输入等长的结果 |
| rank 的 method='dense' 和 'first' 差异 | dense: 同值同排名; first: 按出现顺序 | 根据需求选择 |
| cumsum 不按预期顺序 | 数据未排序 | 先 `sort_values(['user_id','date'])` |
| groupby 后 agg 多个函数返回 MultiIndex 列 | 传入了列表形式的 aggfunc | 用命名聚合 `new_col=('col','func')` |

### 3.6 测试验证

```python
# test_ch18.py
import pandas as pd
import numpy as np

def test_named_agg():
    df = pd.DataFrame({'g':['a','a','b'], 'v1':[10,20,30], 'v2':[1,2,3]})
    r = df.groupby('g', as_index=False).agg(sum_v1=('v1','sum'), mean_v2=('v2','mean'))
    assert r[r['g']=='a']['sum_v1'].iloc[0] == 30
    assert r[r['g']=='b']['mean_v2'].iloc[0] == 3.0

def test_transform_vs_agg():
    df = pd.DataFrame({'g':['a','a','b'], 'v':[10,20,30]})
    agg = df.groupby('g')['v'].agg('sum')
    trans = df.groupby('g')['v'].transform('sum')
    assert len(agg) == 2 and len(trans) == 3
    assert trans.tolist() == [30,30,30]

def test_rank_top3():
    df = pd.DataFrame({'g':['a','a','a','b'], 'v':[1,5,3,10]})
    df['r'] = df.groupby('g')['v'].rank(ascending=False, method='first')
    top = df[df['r'] <= 2]
    assert len(top[top['g']=='b']) == 1

if __name__ == '__main__':
    test_named_agg(); test_transform_vs_agg(); test_rank_top3()
    print("OK 所有测试通过")
```

**完整代码清单**：参见专栏配套仓库 `column/code/ch18/`。

## 4. 项目总结

### 4.1 优点 & 缺点

| 维度 | pandas 高级 GroupBy | SQL 窗口函数 | 手工循环 |
|------|-------------------|-------------|---------|
| 多层分组 | groupby([k1,k2]) 一行 | GROUP BY k1,k2 | 嵌套循环 |
| 组内排名 | rank/cumsum 向量化 | ROW_NUMBER/RANK | 手写排序 |
| 派生指标 | transform 直接添加列 | 需子查询或 CTE | 手写循环 |
| 性能 | Cython 路径极快 | 有索引时极快 | 慢 |
| 灵活性 | agg 支持自定义函数但慢 | 有限内置函数 | 灵活但慢 |

### 4.2 适用场景

- **适用场景**：
  1. RFM 客户价值分层——一次 groupby 打出 R/F/M 三分位
  2. 用户级派生指标：高于/低于自身均值、首单标记、消费次数排名
  3. 分组内 Top-N 提取——用 rank 替代 apply
  4. 累计指标（GMV 增长、用户累积）的时序追踪
- **不适用场景**：
  1. 只有一层简单汇总——基础 GroupBy 即可
  2. 分组内逻辑极其复杂（需要状态机等）——apply 虽慢但实在，或考虑迭代方案

### 4.3 注意事项

- **rank 的 na_option**：决定 NaN 排在最前（'top'）、最后（'bottom'）还是保持 NaN；
- **cumsum 需要预排序**：`cumsum` 依赖行序——确保在 cumsum 前 `sort_values`；
- **apply 的 include_groups 参数**：pandas 2.2+ 中 apply 默认不再传入分组键列，如果函数需要分组键，加 `include_groups=True`。

### 4.4 常见踩坑经验

1. **transform 返回多列**：`transform` 的设计是返回单个 Series 或与输入等长的 DataFrame。如果自定义函数返回 3 列——报错。解决：拆成 3 次 transform 或改用 apply。
2. **rank(method='average') 导致排名出现小数**：`method='average'` 对并列值取平均排名，导致排名可能是 2.5——如果期望整数排名改 `method='min'` 或 `'dense'`。
3. **qcut 分位数为 NaN**：当数据量太少或重复值太多导致边界重合时，`qcut` 可能报错。加 `duplicates='drop'` 解决。

### 4.5 思考题

1. 如何用 `transform` 实现 Z-score 标准化（(x-组均值)/组标准差）并比较 transform 和 (df - group_mean) / group_std 两种写法的性能？
2. `df.groupby('user_id').rolling('7D', on='order_date')['amount'].mean()` 和先 groupby 再 rolling 的语义是什么？它和普通的 rolling 有什么不同？

（答案将在第 19 章附录中给出）

### 4.6 推广计划提示

- **运营/数据分析师**：将 RFM 打分脚本模板化，替换数据源即可用于任何用户分层场景；
- **开发**：制定"禁止裸 apply"的团队规范——能用 agg/transform/rank 绝不用 apply；
- **数据工程师**：关注 transform 的内存占用——结果是原表大小，大数据集需评估。

---

> **源码关联**：pandas/core/groupby/ops.py、pandas/core/groupby/generic.py
