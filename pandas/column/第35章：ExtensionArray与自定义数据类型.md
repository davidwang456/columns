# 第35章：ExtensionArray 与自定义数据类型

## 1. 项目背景

某跨境电商平台的商品团队需要维护一个"多币种价格表"——每个商品在不同国家有不同的价格和币种（CNY、USD、EUR、JPY）。当前的存储方式是在 DataFrame 中用两列：`price`（float64）和 `currency`（string）。但这种设计有显著问题：

**痛点一：币种和金额分离导致逻辑分散**。计算"把欧元价格转换为人民币"需要同时操作 `price` 和 `currency` 两列——一旦某处遗漏了 `currency` 筛选，就会用错汇率。上个季度就发生过一次——运营把 USD 标价直接用 CNY 汇率结算，亏了 15 万。

**痛点二：float64 不适合货币计算**。`0.1 + 0.2 != 0.3` 的浮点精度问题在货币计算中不可接受。使用 Python 的 `Decimal` 可以解决精度问题，但 pandas 没有原生支持 `Decimal` dtype。

**痛点三：无法在聚合操作中保留货币语义**。对价格列做 `sum` 时，不同币种的价格被简单相加——100 USD + 100 CNY = 200（而实际价值完全不同）。理想的行为是：聚合时自动按币种分组，不同币种不混合计算。

痛点流程：

```
商品价格表 (price: float64, currency: string)
  ├── 汇率转换 → price × rate → 忘记筛选 currency → 亏15万
  ├── 浮点精度 → 0.1+0.2 ≠ 0.3 → 对账不平
  └── sum 不同币种 → 100USD+100CNY=200 → 无意义
```

本章将深入 pandas 的 ExtensionArray 机制——这是 pandas 类型系统的"扩展接口"。你将学习如何实现一个 `MoneyArray`，把币种和金额封装在一个 dtype 中，让 pandas 原生支持货币语义。

## 2. 项目设计：剧本式交锋对话

> 场景：老宋在 code review 中发现了一处"漏了 currency 筛选"的 bug，引发了关于 dtype 设计的大讨论。

**小胖**：（嚼着口香糖）"漏了筛选？加个 assert 不就行了！`assert df['currency'] == 'CNY'`——多简单！"

**大师**："小胖，assert 只能检查一处。但你能保证整个代码库、所有同事、所有未来接手的人都不会忘记加这个检查吗？更好的方式是把'币种+金额'封装成一个**数据类型**——让类型系统来保证正确性，而不是靠程序员的记忆力。"

**小白**："封成数据类型？pandas 能自定义 dtype 吗？我只知道用 `astype` 转换内置类型。"

**大师**："这就是 ExtensionArray 的用武之地。pandas 提供了一套接口——只要你实现了 `ExtensionDtype` 和 `ExtensionArray` 两个类，你的自定义类型就能像 `int64`、`string` 一样被 pandas 原生支持。

核心接口：

```python
from pandas.api.extensions import ExtensionDtype, ExtensionArray

class MoneyDtype(ExtensionDtype):
    name = 'money'
    type = ...  # 底层的标量类型
    na_value = ...  # 缺失值表示

class MoneyArray(ExtensionArray):
    # 必须实现的方法
    def __getitem__(self, item): ...
    def __len__(self): ...
    def isna(self): ...
    def take(self, indices, allow_fill=False, fill_value=None): ...
    def _from_sequence(cls, scalars, *, dtype=None, copy=False): ...
    def _from_factorized(cls, values, original): ...
    # 可选但推荐
    def _values_for_factorize(self): ...
    def _reduce(self, name, *, skipna=True, **kwargs): ...
```

实现这 6-8 个方法后，你的 `MoneyArray` 就能：
- 在 DataFrame 中作为一列存储（`df['price'] = MoneyArray(...)`）
- 支持 `loc`/`iloc` 选择
- 支持 `isna()` 缺失值检测
- 支持 `sum()`/`mean()` 聚合（如果你实现了 `_reduce`）"

**【技术映射：ExtensionArray = 乐高积木的接口——只要你的积木有标准的凹凸接头（接口），就能和任何其他乐高拼接（与 pandas 集成）】**

**小胖**："这也太复杂了吧！实现 8 个方法就为了让钱不混在一起？"

**大师**："前面 30 章我们都在用 pandas 的能力。从这一章开始——**为 pandas 增加能力**。这是一个质的飞跃。而且实现一次 `MoneyArray` 后，团队所有分析师都能直接用它——比让每个人记住'别忘筛选 currency'可靠一万倍。"

**小白**："nullable integer（Int64）也是用 ExtensionArray 实现的吗？"

**大师**："对！pandas 的 `Int64`（大写 I）、`string`、`boolean`、`ArrowDtype`——**全部**是基于 ExtensionArray 接口实现的。你平时用的 `df['col'].astype('Int64')`，底层就是把 NumPy int64 数组包装为 `IntegerArray`（一个 ExtensionArray 子类）。

源码位置：`pandas/core/arrays/` 目录下：
- `integer.py` → IntegerArray（Int64 的实现）
- `string_.py` → StringArray（string dtype 的实现）
- `boolean.py` → BooleanArray
- `arrow/array.py` → ArrowExtensionArray（Arrow-backed dtype）"

**【技术映射：ExtensionArray = 标准化的插头——所有电器（Int64/string/MoneyArray）都通过同一个插座（ExtensionArray 接口）接入 pandas】**

**大师总结**："ExtensionArray 的三个层次理解：
1. **使用者**：用 `Int64`、`string`、`ArrowDtype` 等现成类型
2. **扩展者**：实现自己的 `MoneyArray`、`GeoArray` 等业务类型
3. **贡献者**：向 pandas 提交新的内置类型

前 30 章我们是使用者，从这一章开始成为扩展者——为团队定制领域专用的数据类型。"

## 3. 项目实战

### 3.1 准备

```bash
pip install pandas numpy
```

### 3.2 实现 MoneyArray

```python
# money_dtype.py
import numpy as np
import pandas as pd
from pandas.api.extensions import ExtensionDtype, ExtensionArray
from pandas.api.types import is_scalar

@pd.api.extensions.register_extension_dtype
class MoneyDtype(ExtensionDtype):
    """货币数据类型"""
    name = 'money'
    type = str  # 标量类型
    na_value = pd.NA

    @classmethod
    def construct_array_type(cls):
        return MoneyArray

    def __repr__(self):
        return 'money'

class MoneyArray(ExtensionArray):
    """货币数组：存储"金额+币种"对"""
    _dtype = MoneyDtype()

    def __init__(self, amounts, currencies):
        self._amounts = np.asarray(amounts, dtype=float)
        self._currencies = np.asarray(currencies, dtype=str)
        self._validate()

    def _validate(self):
        if len(self._amounts) != len(self._currencies):
            raise ValueError("amounts 和 currencies 长度必须一致")

    @classmethod
    def _from_sequence(cls, scalars, *, dtype=None, copy=False):
        """从标量序列构建（pandas 内部调用）"""
        amounts = []
        currencies = []
        for s in scalars:
            if s is None or s is pd.NA:
                amounts.append(np.nan)
                currencies.append('')
            elif isinstance(s, tuple) and len(s) == 2:
                amounts.append(s[0])
                currencies.append(s[1])
            else:
                raise ValueError(f"MoneyArray 需要 (amount, currency) 元组: {s}")
        return cls(amounts, currencies)

    @classmethod
    def _from_factorized(cls, values, original):
        """从 factorized 结果重建"""
        return cls._from_sequence(values)

    def __getitem__(self, item):
        if is_scalar(item):
            if self.isna()[item]:
                return pd.NA
            return (self._amounts[item], self._currencies[item])
        return MoneyArray(self._amounts[item], self._currencies[item])

    def __len__(self):
        return len(self._amounts)

    def __eq__(self, other):
        if not isinstance(other, MoneyArray):
            return np.full(len(self), False)
        return (self._amounts == other._amounts) & (self._currencies == other._currencies)

    def isna(self):
        return np.isnan(self._amounts) | (self._currencies == '')

    def take(self, indices, allow_fill=False, fill_value=None):
        indices = np.asarray(indices, dtype=int)
        if allow_fill and fill_value is not None:
            mask = indices == -1
            indices = np.where(mask, 0, indices)
            result_amounts = self._amounts[indices].copy()
            result_currencies = self._currencies[indices].copy()
            result_amounts[mask] = np.nan
            result_currencies[mask] = ''
            return MoneyArray(result_amounts, result_currencies)
        return MoneyArray(self._amounts[indices], self._currencies[indices])

    def copy(self):
        return MoneyArray(self._amounts.copy(), self._currencies.copy())

    def _values_for_factorize(self):
        """返回用于 factorize 的唯一值"""
        return np.array([f'{a}:{c}' for a, c in zip(self._amounts, self._currencies)]), pd.NA

    @property
    def dtype(self):
        return self._dtype

    @property
    def nbytes(self):
        return self._amounts.nbytes + self._currencies.nbytes

    # === 聚合支持 ===
    def _reduce(self, name, *, skipna=True, **kwargs):
        if name == 'sum':
            # 按币种分组求和
            unique_currencies = np.unique(self._currencies[self._currencies != ''])
            result = {}
            for curr in unique_currencies:
                mask = (self._currencies == curr) & (~np.isnan(self._amounts))
                result[curr] = self._amounts[mask].sum()
            return result
        if name == 'mean':
            unique_currencies = np.unique(self._currencies[self._currencies != ''])
            result = {}
            for curr in unique_currencies:
                mask = (self._currencies == curr) & (~np.isnan(self._amounts))
                result[curr] = self._amounts[mask].mean()
            return result
        raise NotImplementedError(f"聚合 {name} 未实现")

    def __repr__(self):
        items = []
        for i in range(min(len(self), 10)):
            if self.isna()[i]:
                items.append('NA')
            else:
                items.append(f'{self._amounts[i]:.2f}{self._currencies[i]}')
        return f'MoneyArray([{", ".join(items)}...])' if len(self) > 10 else f'MoneyArray([{", ".join(items)}])'
```

### 3.3 使用 MoneyArray

```python
# use_money.py
import pandas as pd
import numpy as np
from money_dtype import MoneyArray

# 创建包含 MoneyArray 的 DataFrame
df = pd.DataFrame({
    'product': ['A','B','C','D'],
    'price': MoneyArray(
        amounts=[99.9, 25.5, 150.0, 200.0],
        currencies=['CNY','USD','CNY','EUR']
    )
})

print("===== DataFrame with MoneyArray =====")
print(df)
print(f"\nprice dtype: {df['price'].dtype}")
print(f"price 类型: {type(df['price'].values)}")

# loc 选择
print(f"\ndf.loc[0, 'price']: {df.loc[0, 'price']}")

# isna 检测
print(f"\nisna: {df['price'].isna().tolist()}")

# take（内部操作）
arr = df['price'].values
taken = arr.take([0, 2])
print(f"\ntake([0,2]): {[taken[i] for i in range(len(taken))]}")
```

### 3.4 常见坑及解决方法

| 问题 | 原因 | 解决方法 |
|------|------|----------|
| `_from_sequence` 未被调用 | 未用 `register_extension_dtype` 注册 | 加 `@register_extension_dtype` 装饰器 |
| groupby 后类型退化 | groupby 内部重建 ExtensionArray 失败 | 确保 `_from_sequence` 实现正确 |
| `astype` 转换失败 | 缺少 `_from_sequence` 或 `_from_factorized` | 实现这两个方法 |

### 3.5 测试验证

```python
# test_ch35.py
import pandas as pd
import numpy as np
from money_dtype import MoneyArray

def test_create_money_array():
    arr = MoneyArray([10.0, 20.0], ['CNY', 'USD'])
    assert len(arr) == 2
    assert arr[0] == (10.0, 'CNY')

def test_isna():
    arr = MoneyArray([10.0, np.nan], ['CNY', ''])
    assert not arr.isna()[0]
    assert arr.isna()[1]

def test_take():
    arr = MoneyArray([10.0, 20.0, 30.0], ['CNY', 'USD', 'CNY'])
    taken = arr.take([0, 2])
    assert taken[0] == (10.0, 'CNY')
    assert taken[1] == (30.0, 'CNY')

def test_in_dataframe():
    df = pd.DataFrame({'price': MoneyArray([99.0, 150.0], ['CNY', 'USD'])})
    assert df['price'].dtype.name == 'money'

if __name__ == '__main__':
    test_create_money_array(); test_isna(); test_take(); test_in_dataframe()
    print("OK 所有测试通过")
```

**完整代码清单**：参见专栏配套仓库 `column/code/ch35/`。

## 4. 项目总结

### 4.1 优点 & 缺点

| 维度 | ExtensionArray 自定义类型 | 多列组合 | Python 类包装 |
|------|------------------------|---------|-------------|
| pandas 集成度 | 原生支持 loc/groupby | 需手动处理两列 | 需来回转换 |
| 类型安全 | 编译期保证 | 运行时检查 | 中等 |
| 聚合支持 | 可实现 _reduce | 需 groupby | 手写循环 |
| 开发成本 | 高（需实现 8 个方法） | 低 | 中 |
| 性能 | 取决于实现 | 原生性能 | 低 |

### 4.2 ExtensionArray 实现要点深入

**`_from_sequence` 的多种输入格式**：pandas 内部调用 `_from_sequence` 时传入的 `scalars` 可以是多种类型——Python 列表、NumPy 数组、另一个 ExtensionArray、甚至是一个标量值。你的实现需要处理所有这些情况。最好的做法是在方法开头做类型归一化：

```python
@classmethod
def _from_sequence(cls, scalars, *, dtype=None, copy=False):
    if isinstance(scalars, cls):
        return scalars.copy() if copy else scalars
    if isinstance(scalars, np.ndarray):
        # 处理 NumPy 数组
        ...
    # 处理其他可迭代对象
    ...
```

**`_reduce` 的语义约束**：`_reduce` 被 pandas 内部在 `sum()`、`mean()`、`min()` 等聚合操作中调用。它接收的参数 `name` 是字符串形式的聚合名（如 `'sum'`、`'mean'`）。返回值可以是标量（如 `sum` 返回一个字典）或者保持 ExtensionArray 类型（如 `cumsum` 应该返回同类型的 ExtensionArray）。如果不支持某个聚合操作，抛出 `TypeError` 而非 `NotImplementedError`——这样 pandas 的 fallback 机制才能正确处理。

**测试基类的使用**：pandas 在 `pandas/tests/extension/` 目录下提供了 `BaseExtensionTests` 基类，包含了数百个标准测试用例。如果你的 ExtensionArray 通过了 `BaseExtensionTests` 的所有测试，就说明它在各种边界情况下都能和 pandas 正确协作。使用方式：

```python
from pandas.tests.extension import BaseExtensionTests
import pytest

class TestMoneyArray(BaseExtensionTests):
    @pytest.fixture
    def data(self):
        return MoneyArray([10.0, 20.0, 30.0], ['CNY', 'USD', 'CNY'])
```

**性能考量**：`take` 和 `isna` 是最频繁被调用的两个方法——pandas 内部在选择、过滤、分组时大量使用它们。用 NumPy 向量化实现这两个方法（而非 Python 循环）可以显著提升性能。

### 4.2 适用场景

- **适用场景**：封装业务语义的数据类型（货币、地理坐标、化学元素）、需要类型安全的关键业务、团队复用的领域 dtype
- **不适用场景**：一次性分析、简单的类型转换（用 `astype` 即可）

### 4.3 注意事项

- **`_from_sequence` 必须接受标量列表**：pandas 内部通过 `_from_sequence` 创建新的 ExtensionArray——如果实现不对整个类型不可用；
- **`_reduce` 是可选的**：不实现则降级为 Python fallback；
- **astype 转换**：如果希望 `df['col'].astype('money')` 能工作，需要实现 `MoneyDtype.construct_from_string`。

### 4.4 常见踩坑经验

1. **忘记 `@register_extension_dtype` 导致 pandas 不识别**：没有注册的 dtype 无法在 DataFrame 构造时自动选择。
2. **`take` 的 `fill_value` 类型不匹配**：`fill_value` 必须是本 ExtensionArray 的有效值或 NA。
3. **`_values_for_factorize` 返回值格式不对**：必须返回 `(array, na_value)` 元组。

### 4.5 思考题

1. pandas 的 `IntegerArray`（Int64 的实现）中如何处理 `pd.NA` 和 `np.nan` 的区别？它为什么选择用 `pd.NA` 而非 `np.nan`？
2. 如果要实现一个 `GeoPointArray`（存储经纬度），如何设计 `_reduce` 方法来支持 `mean()`（返回中心点）？

（答案将在第 36 章附录中给出）

### 4.6 推广计划提示

- **资深开发/架构师**：评估团队是否有需要封装为自定义 dtype 的业务概念（货币/坐标/时段）；
- **开源贡献者**：ExtensionArray 是向 pandas 贡献新类型的主要入口——参考 `pandas/tests/extension/` 下的测试基类。

---

> **源码关联**：pandas/core/arrays/base.py、pandas/core/dtypes/base.py、pandas/tests/extension/
