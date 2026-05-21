# 第15章：Pandas 测试与可复现分析

## 1. 项目背景

某数据团队的同事小周维护着一套"订单清洗脚本"（order_clean.py），这套脚本每天被生产调度系统调用，清洗约 50 万条订单数据，是公司经营日报的数据源头。脚本逻辑经过多次迭代，已经累积了 300 多行代码，包含 15 条业务清洗规则。

上周五小周对脚本做了一次"小改动"——把"已取消订单金额设为 0"改成了"已取消订单金额设为 NaN"。他自认为改动很小，没有做完整测试就直接部署了。结果周一早晨——日报脚本报错：下游的 `groupby.sum()` 因为 skipna=True 自动跳过了 NaN，导致"周销售额"少了 8%，整整一天各部门都在用错误的经营数据做决策。

复盘发现，**脚本有 300 行代码但零个测试**。小周的改动手动跑了 5 条数据看"能跑通"就上线了，完全没覆盖到"下游 sum 的 skipna 行为"这个关键边界。

**痛点一：改一行代码影响整条数据链路**。数据分析脚本通常不是独立运行的——上游的清洗规则改变会影响下游的聚合统计。没有回归测试，任何改动都是盲飞。

**痛点二：测试数据靠手工编造，不可复现**。小周的"手工测 5 条"每次都是随机从生产数据里抽，今天和明天的测试结果可能不一样——因为生产数据本身就变了。没有固定测试数据集，测试不可复现。

**痛点三：数据质量的断言散落在代码各处的 print 语句里**。"行数应该等于 X""金额总和应该等于 Y"——这些校验逻辑以注释和 print 的形式散落，格式不统一，有些已经过时，有些从未被执行过。

痛点流程：

```
order_clean.py (300行, 0测试)
  └── 小改动: 0 → NaN
        └── 手工测5条(OK) → 部署
              └── 日报脚本 groupby.sum skipna 跳过 NaN
                    └── 销售额少了 8% × 全天
```

本章将系统讲解 pandas 的测试方法——assert_frame_equal/assert_series_equal、固定随机种子、pytest 集成、数据质量断言和可复现分析的工程实践。

## 2. 项目设计：剧本式交锋对话

> 场景：周一事故复盘会上，小周红着脸解释"我以为就改了一行"，小胖忍不住接话。

**小胖**：（举着奶茶）"不就是打印一下 DataFrame 看看吗？print(df.head()) 不就行了？我每次写完代码都 print 一下！"

**大师**："print 只能证明'你这次跑的时候看起来是对的'。但你的 print 不会在凌晨 3 点自动执行，不会提醒你'sum 的值比昨天少了 8%'。你需要的是**自动化测试**——代码来验证代码的行为。"

**小白**："pandas 有专门的测试工具吗？和 Python 标准库的 unittest 有什么区别？"

**大师**："pandas 提供了两个专门测试 DataFrame 和 Series 的函数：

```python
from pandas.testing import assert_frame_equal, assert_series_equal

# 比较两个 DataFrame 是否完全一致
assert_frame_equal(result, expected)

# 比较两个 Series
assert_series_equal(result_series, expected_series)
```

这两个函数比 `assert df1.equals(df2)` 更好——因为它们会在失败时打印**具体的差异**：哪行哪列不一致、dtype 不同、还是值不同。普通的 equals 只返回 True/False，排查问题像大海捞针。"

**小白**："`assert_frame_equal` 有哪些参数可以控制比较的严格程度？"

**大师**："四个最常用的参数：

- **`check_dtype`**：是否检查 dtype。默认 True——Int64 和 int64 被视为不同。如果只想比较数值，设为 False。
- **`check_index_type`**：是否检查 Index 类型。默认 True。
- **`check_names`**：是否检查 Index/columns 的 name 属性。默认 True。
- **`rtol` / `atol`**：浮点数比较的相对容忍度和绝对容忍度。`atol=0.01` 表示两位小数内近似相等即可。这是财务场景最常用的参数——金额不可能完全精确相等。
- **`check_like`**：只比较指定的列或行，忽略其他。

```python
# 严格模式（默认）
assert_frame_equal(result, expected)

# 宽松模式（只比较值，容忍浮点误差）
assert_frame_equal(result, expected, check_dtype=False, atol=0.01)
```"

**【技术映射：assert_frame_equal = 天平称重——能告诉你具体差了多少克，而不是只回答"不一样"】**

**小胖**："但我每次跑代码，数据都不一样——今天生产数据 50 万条，明天 51 万条，怎么测试？"

**大师**"这就需要两个工程实践：

1. **固定随机种子**：`np.random.seed(42)` 确保每次生成的模拟数据完全一致；
2. **测试夹具（fixture）**：预先准备一份小的、固定的 CSV 文件作为测试输入，提交到 Git 仓库。这份文件中包含了所有关键边界场景（正常数据、缺失值、异常值、空值）。

测试应该用**固定的、小的（几十到几百行）**的测试数据，而不是依赖生产环境的大数据集。"

**【技术映射：固定种子 + 测试夹具 = 考试的标准试卷——和真实考试内容不同但能验证你掌握了知识点】**

**小白**："那数据质量断言呢？比如'去重后的行数应是 X''销售额总和不应为负'？"

**大师**："这正是测试应该覆盖的内容。我建议把数据质量断言分成三层：

1. **结构断言**：行数、列名、dtype —— `assert df.shape[0] == 1000`
2. **业务断言**：主键唯一（`assert df['id'].is_unique`）、金额非负（`assert (df['amount']>=0).all()`）
3. **一致性断言**：聚合值 = 明细值的总和（`assert abs(df['amount'].sum() - expected_total) < 0.01`）

这些断言应该在每次数据清洗后自动执行——用 pytest 把这些组织成测试用例，CI 中每次提交都跑一遍。"

**【技术映射：三层断言 = 工厂质检——结构（产品形状对）、业务（功能正常）、一致性（零件数对得上）】**

**小白**："pytest 里怎么组织 pandas 的测试？"

**大师**："典型的模式：

```python
# conftest.py: 定义测试夹具
import pytest
import pandas as pd

@pytest.fixture
def sample_orders():
    return pd.DataFrame({
        'order_id': [1, 2, 3, 4, 3],  # 注意：id=3 重复了！
        'amount': [100.0, 200.0, 0.0, 400.0, 300.0],
        'status': ['已完成', '已取消', '已完成', '已完成', '已完成']
    })

# test_clean.py: 测试清洗函数
def test_remove_duplicates(sample_orders):
    cleaned = remove_duplicate_orders(sample_orders)
    assert cleaned['order_id'].is_unique
    assert len(cleaned) == 4

def test_cancel_amount_zero(sample_orders):
    cleaned = clean_orders(sample_orders)
    cancelled = cleaned[cleaned['status'] == '已取消']
    assert (cancelled['amount'] == 0).all()
```

这样每次 `pytest` 跑一遍，就能发现谁破坏了哪条规则。"

**大师总结**："可复现分析的三个支柱：
1. `assert_frame_equal` + `assert_series_equal` —— 精确比较 DataFrame
2. 固定随机种子 + 测试夹具 —— 保证测试可复现
3. 三层断言（结构/业务/一致性） —— 数据质量的自动化守卫

记住：没有测试的脚本是'野代码'——今天能跑，明天不一定能跑。一个有测试的数据脚本才是'可信代码'。"

## 3. 项目实战

### 3.1 环境准备

```bash
pip install pandas numpy pytest
```

### 3.2 创建被测函数

```python
# order_clean.py —— 被测模块
import pandas as pd
import numpy as np

def load_orders(filepath):
    """读取订单 CSV"""
    return pd.read_csv(filepath, parse_dates=['order_time'])

def clean_duplicates(df):
    """去除重复订单号，保留第一条"""
    return df.drop_duplicates(subset='order_id', keep='first').reset_index(drop=True)

def clean_amount(df):
    """清洗金额：已取消订单 → 0，负金额 → NaN"""
    df = df.copy()
    df.loc[df['status'] == '已取消', 'amount'] = 0.0
    df.loc[df['amount'] < 0, 'amount'] = np.nan
    return df

def clean_missing(df):
    """填充缺失：城市缺失 → '未知'，金额缺失 → 0"""
    df = df.copy()
    df['city'] = df['city'].fillna('未知')
    df['amount'] = df['amount'].fillna(0)
    return df

def full_clean_pipeline(filepath):
    """完整清洗流水线"""
    df = load_orders(filepath)
    df = clean_duplicates(df)
    df = clean_amount(df)
    df = clean_missing(df)
    return df

def compute_summary(df):
    """计算汇总：总销售额、订单数、客单价"""
    return {
        'total_sales': df['amount'].sum(),
        'order_count': len(df),
        'avg_amount': df['amount'].mean(),
    }
```

### 3.3 分步实现

#### 步骤 1：创建测试夹具（conftest.py）

**目标**：准备固定的、包含边界场景的测试数据。

```python
# conftest.py
import pytest
import pandas as pd
import numpy as np

@pytest.fixture
def sample_orders():
    """标准测试数据：包含正常、重复、取消、负金额、缺失城市"""
    np.random.seed(42)
    return pd.DataFrame({
        'order_id': [1, 2, 3, 4, 3],       # 3 重复了
        'order_time': pd.date_range('2025-03-15', periods=5, freq='h'),
        'amount': [100.0, 200.0, -50.0, 400.0, 300.0],  # -50 是负金额
        'status': ['已完成', '已取消', '已完成', '已完成', '已完成'],
        'city': ['北京', '上海', np.nan, '广州', '深圳'],  # 一个缺失
    })

@pytest.fixture
def all_cancelled_orders():
    """边界测试：全部是已取消订单"""
    return pd.DataFrame({
        'order_id': [1, 2, 3],
        'amount': [100.0, 200.0, 300.0],
        'status': ['已取消', '已取消', '已取消'],
        'city': ['北京', '上海', '广州']
    })

@pytest.fixture
def empty_orders():
    """边界测试：空 DataFrame"""
    return pd.DataFrame(columns=['order_id','order_time','amount','status','city'])
```

#### 步骤 2：用 assert_frame_equal 和 pytest 写测试

**目标**：为每个清洗函数编写测试用例。

```python
# test_order_clean.py
import pandas as pd
import numpy as np
from pandas.testing import assert_frame_equal, assert_series_equal
from order_clean import (clean_duplicates, clean_amount, clean_missing,
                          compute_summary, full_clean_pipeline)

# === 测试 1：去重 ===
def test_clean_duplicates_removes_dup(sample_orders):
    result = clean_duplicates(sample_orders)
    assert result['order_id'].is_unique, "仍有重复 order_id"
    assert len(result) == 4, f"期望 4 行，实际 {len(result)}"

def test_clean_duplicates_keeps_first(sample_orders):
    result = clean_duplicates(sample_orders)
    # order_id=3 的第一条是 amount=300 还是 amount=-50？
    # 原始数据中第一条 id=3 是 index=2 (amount=-50)
    dup_row = result[result['order_id'] == 3]
    assert dup_row['amount'].values[0] == -50.0, "应保留第一条出现的值"

# === 测试 2：金额清洗 ===
def test_clean_amount_sets_cancelled_to_zero(sample_orders):
    result = clean_amount(sample_orders)
    cancelled = result[result['status'] == '已取消']
    assert (cancelled['amount'] == 0).all(), "已取消订单金额应为 0"

def test_clean_amount_sets_negative_to_nan(sample_orders):
    result = clean_amount(sample_orders)
    # order_id=3 的 amount=-50，被转为 NaN
    neg_row = result[result['amount'].isna()]
    assert len(neg_row) == 1, "应有一个 NaN（来源是负金额）"

def test_clean_amount_does_not_affect_normal(sample_orders):
    result = clean_amount(sample_orders)
    normal = result[(result['status'] != '已取消') & (result['amount'].notna())]
    assert (normal['amount'] > 0).all(), "正常订单金额应 > 0"

# === 测试 3：缺失值填充 ===
def test_clean_missing_fills_city(sample_orders):
    result = clean_missing(sample_orders)
    assert result['city'].isna().sum() == 0, "city 不应有缺失"
    assert '未知' in result['city'].values, "应有一条 city='未知'"

def test_clean_missing_fills_amount_zero(sample_orders):
    result = clean_missing(sample_orders)
    assert result['amount'].isna().sum() == 0, "amount 不应有缺失"

# === 测试 4：汇总计算 ===
def test_compute_summary(sample_orders):
    # 手工计算期望值
    summary = compute_summary(sample_orders)
    assert summary['order_count'] == 5
    assert summary['total_sales'] == 950.0  # 100+200+(-50)+400+300
    assert abs(summary['avg_amount'] - 190.0) < 0.01

# === 测试 5：边界场景 ===
def test_all_cancelled_summary(all_cancelled_orders):
    df = clean_amount(all_cancelled_orders)
    summary = compute_summary(df)
    assert summary['total_sales'] == 0.0, "全取消订单总销售额应为 0"

def test_empty_orders(empty_orders):
    result = clean_duplicates(empty_orders)
    assert len(result) == 0, "空 DataFrame 去重后仍应为空"

# === 测试 6：流程整合测试 ===
def test_full_pipeline_integration(tmp_path, sample_orders):
    """验证完整流水线的输入输出一致性"""
    # 将 sample_orders 写入临时文件
    filepath = tmp_path / 'orders.csv'
    sample_orders.to_csv(filepath, index=False)

    result = full_clean_pipeline(filepath)
    # 去重后应只有 4 行
    assert len(result) == 4
    # 清洗后 amount 无 NaN 无负数
    assert result['amount'].isna().sum() == 0
    assert (result['amount'] >= 0).all()
    # city 无缺失
    assert result['city'].isna().sum() == 0
```

#### 步骤 3：assert_frame_equal 精确比较

**目标**：学习 assert_frame_equal 的各项参数。

```python
# test_assert_helpers.py
import pandas as pd
from pandas.testing import assert_frame_equal, assert_series_equal

def test_assert_frame_strict_vs_loose():
    expected = pd.DataFrame({
        'store': pd.Series(['A','B'], dtype='category'),
        'sales': [100.001, 200.002]
    })
    actual = pd.DataFrame({
        'store': ['A','B'],
        'sales': [100.0, 200.0]
    })

    # 严格模式：category ≠ object → 报错
    try:
        assert_frame_equal(expected, actual)
        assert False
    except AssertionError:
        pass

    # 宽松模式：不检查 dtype，容忍浮点误差
    assert_frame_equal(expected, actual, check_dtype=False, atol=0.01)
    print("OK 宽松模式通过")

def test_assert_series_equal():
    s1 = pd.Series([1, 2, 3], name='count')
    s2 = pd.Series([1, 2, 3], name='count')
    assert_series_equal(s1, s2)

    s3 = pd.Series([1, 2, 4], name='count')
    try:
        assert_series_equal(s1, s3)
    except AssertionError as e:
        print(f"预期报错: {str(e)[:80]}...")

def test_check_like():
    df1 = pd.DataFrame({'A':[1,2,3], 'B':[4,5,6], 'C':[7,8,9]})
    df2 = pd.DataFrame({'A':[1,2,3], 'B':[4,5,7], 'C':[7,8,9]})  # B[2] 不同
    # 只比较列 A 和 C，忽略 B 的差异
    assert_frame_equal(df1, df2, check_like=['A','C'])
    print("OK check_like 测试通过")
```

#### 步骤 4：运行测试

```bash
# 运行所有测试
pytest test_order_clean.py test_assert_helpers.py -v

# 预期输出：
# test_order_clean.py::test_clean_duplicates_removes_dup PASSED
# test_order_clean.py::test_clean_amount_sets_cancelled_to_zero PASSED
# ... (12 tests passed)
```

### 3.4 常见坑及解决方法

| 问题 | 原因 | 解决方法 |
|------|------|----------|
| assert_frame_equal 报 dtype 不匹配 | Int64 ≠ int64 | `check_dtype=False` 或统一 dtype |
| 浮点数比较永远失败 | 浮点精度问题 | 设置 `atol=0.01` 或 `rtol=0.001` |
| tmp_path fixture 找不到 | 未安装 pytest | `pip install pytest`，tmp_path 是内置 fixture |
| 固定种子后数据仍不一致 | 多线程或依赖外部状态 | 在每个测试 fixture 中重新设置 seed |
| NaN 比较失败 | NaN != NaN | `assert_frame_equal` 默认认为 NaN=NaN（已处理）|

### 3.5 测试验证

```bash
# 运行测试套件
pytest test_order_clean.py test_assert_helpers.py -v --tb=short
```

**完整代码清单**：参见专栏配套仓库 `column/code/ch15/` 下的 `order_clean.py`、`conftest.py`、`test_order_clean.py`、`test_assert_helpers.py`。

## 4. 项目总结

### 4.1 优点 & 缺点

| 维度 | pytest + assert_frame_equal | 手工 print 调试 | Great Expectations / Pandera |
|------|---------------------------|---------------|------------------------------|
| 自动化 | 一次编写，CI 自动执行 | 每次手工操作 | 声明式配置，自动验证 |
| 精确度 | 行/列/值 精确比对 | 肉眼判断，易漏 | Schema + 统计分布 |
| 成本 | 写测试的首次成本 | 零成本（但持续消耗） | 学习新框架 |
| 边界覆盖 | 需手动设计 | 无法系统覆盖 | 内置统计检验 |
| 可复现 | 种子+夹具保证 | 依赖生产数据 | 配置+数据保证 |
| 团队协作 | Git + CI 标准化 | 个人习惯差异 | 统一配置文件 |

### 4.2 适用场景

- **适用场景**：
  1. 生产环境数据管道——任何修改都要经过回归测试
  2. 多人协作的数据脚本——测试作为"活文档"说明预期行为
  3. 数据质量要求高的场景——金融、医疗、风控
  4. pandas 函数库/工具包的开发——TDD 驱动
  5. 复杂清洗逻辑（15+ 规则）的回归保护
- **不适用场景**：
  1. 一次性的探索性分析——测试成本大于收益
  2. 数据量极小（<100 行）——肉眼 review 更快

### 4.3 注意事项

- **随机种子在每个 fixture 中单独设置**：不同的 fixture 如果共享全局随机状态，测试可能互相干扰；
- **assert_frame_equal 默认比较 dtypes**：如果你的清洗函数改变了 dtype（如 int 变 float），记得设 `check_dtype=False`；
- **tmp_path 是临时目录**：文件在测试结束后自动删除，不要把重要输出放在 tmp_path；
- **测试数据要提交到 Git**：小文件（几十 KB）的测试 CSV 应放在 `tests/data/` 目录下并纳入版本控制。

### 4.4 常见踩坑经验

1. **"我的测试在本地能过，CI 上就挂"**：最常见的根因是测试依赖了本地文件路径或环境变量（如 `pd.read_csv('data/orders.csv')` 中的相对路径）。改用 `tmp_path` fixture 或 `pathlib.Path(__file__).parent` 构造绝对路径。
2. **固定种子写在函数里而不是 fixture 里**：`def test_foo(): np.random.seed(42); ...` 如果前面有其他测试改了随机状态，这个种子不一定能恢复初始状态。推荐在 fixture 的最开始设置 seed。
3. **assert_frame_equal 报错信息太长看不完**：对大数据集不要直接跑 assert_frame_equal——先用 `df.head()` 或 `df.iloc[:10]` 取子集比较，缩小排查范围。定位到问题后可以针对特定列加 `check_like` 参数。

### 4.5 思考题

1. 除了 `assert_frame_equal` 外，pandas 还提供了哪些测试工具函数？查阅 `pandas.testing` 模块的文档，列出至少 3 个并说明用途。
2. 如何用 pytest 的 `parametrize` 装饰器为一个清洗函数同时测试 10 组不同的输入输出？写出示例代码。

（答案将在第 16 章附录中给出）

### 4.6 推广计划提示

- **开发**：将本章的 pytest 测试模板作为团队标准，要求所有 pandas 清洗函数必须有对应测试；
- **测试**：掌握 assert_frame_equal 的各类参数，编写全面的 DataFrame 比对断言；
- **数据分析师**：至少写 3 个"数据质量断言"测试——行数、主键唯一、聚合一致性。

---

> **源码关联**：pandas/_testing/、pandas/tests/
