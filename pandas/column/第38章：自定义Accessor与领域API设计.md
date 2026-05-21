# 第38章：自定义 Accessor 与领域 API 设计

## 1. 项目背景

某金融科技公司的数据团队维护着一套"股票技术分析库"，包含 20+ 个常用的技术指标计算（MACD、RSI、布林带、均线交叉等）。这些指标都是以 DataFrame 或 Series 为输入，输出新的列或信号。

当前代码库中，这些指标函数散落在 5 个文件中，调用的方式是：

```python
df = add_macd(df)
df = add_rsi(df, period=14)
df = add_bollinger_bands(df)
signal = detect_golden_cross(df)
```

分析师小卢每次分析新股票时都要写一长串的函数调用链。而且因为函数签名不统一（有的 `add_xxx(df)`、有的 `compute_xxx(df, col)`、有的 `xxx(df, **kwargs)`），新人经常传错参数。

**痛点一：API 风格不统一**。20 个函数来自 3 位前同事，风格各异——`add_macd`、`compute_rsi`、`bollinger`、`golden_cross_detect`——没有统一的命名规范。

**痛点二：不支持链式调用**。`df.pipe(add_macd).pipe(add_rsi, period=14)` 比 `df = add_macd(df); df = add_rsi(df, 14)` 好一点——但 pipe 每次都要显式传递 DataFrame 作为第一个参数，仍然繁琐。

**痛点三：无法像 pandas 原生方法那样使用**。分析师希望 `df.ta.macd()` 这样调用——就像 `df.plot()` 一样自然。

痛点流程：

```
技术分析库 (20+ 函数, 5 个文件)
  ├── add_macd, compute_rsi, bollinger → 命名不统一
  ├── df.pipe(...).pipe(...) → 仍然繁琐
  └── 期望 df.ta.macd() → 像 pandas 原生方法
```

本章将介绍 pandas 的 **Accessor 机制**（`register_dataframe_accessor` / `register_series_accessor`），教你如何将团队的业务规则封装为 `df.xxx.method()` 的形式，让自定义 API 像 pandas 原生功能一样自然。

## 2. 项目设计：剧本式交锋对话

> 场景：小卢在周会上展示了他封装的 `DataFrame.ta` 访问器，大家看到 `df.ta.macd()` 的写法后眼睛都亮了。

**小胖**：（瞪大了眼睛）"`df.ta.macd()` ？这个 `.ta` 是哪来的？pandas 里没有 `.ta` 这个属性啊！你改 pandas 源码了？"

**大师**："不需要改源码。pandas 提供了 **Accessor 注册机制**——`@register_dataframe_accessor('ta')` 装饰器。你只需要写一个类，用这个装饰器注册——之后所有的 DataFrame 就自动拥有 `.ta` 属性了。"

**小白**："具体怎么实现？"

**大师**："三步：

```python
@pd.api.extensions.register_dataframe_accessor('ta')
class TechnicalAnalysisAccessor:
    def __init__(self, pandas_obj):
        self._obj = pandas_obj  # 保存 DataFrame 引用
        self._validate()        # 校验必要的列是否存在

    def _validate(self):
        required = ['open','high','low','close','volume']
        missing = set(required) - set(self._obj.columns)
        if missing:
            raise AttributeError(f"缺少必要列: {missing}")

    def macd(self, fast=12, slow=26, signal=9):
        df = self._obj.copy()
        df['ema_fast'] = df['close'].ewm(span=fast).mean()
        df['ema_slow'] = df['close'].ewm(span=slow).mean()
        df['macd'] = df['ema_fast'] - df['ema_slow']
        df['macd_signal'] = df['macd'].ewm(span=signal).mean()
        df['macd_hist'] = df['macd'] - df['macd_signal']
        return df[['macd','macd_signal','macd_hist']]
```

之后任何 DataFrame 都能直接用 `df.ta.macd()`。"

**【技术映射：Accessor = 手机壳——手机（DataFrame）本身没变，但戴上壳（Accessor）就有了新的功能（支架/卡槽）】**

**小白**："那 `.ta` 中能缓存计算结果吗？比如多次调用 `macd` 只需要算一次？"

**大师**："可以在 `__init__` 中维护一个 `_cache` 字典：

```python
class TAAccessor:
    def __init__(self, pandas_obj):
        self._obj = pandas_obj
        self._cache = {}  # 缓存已计算的结果

    def macd(self):
        if 'macd' in self._cache:
            return self._cache['macd']
        # 计算...
        self._cache['macd'] = result
        return result
```

但要注意：**DataFrame 是可变对象**——缓存只在当前 DataFrame 实例的生命周期内有效。一旦 DataFrame 被修改（如新增了行），缓存的指标就失效了。"

**小白**："那 Accessor 和 `monkey patch`（直接给 DataFrame 加方法）有什么区别？"

**大师**："Accessor 有三个 monkey patch 不具备的优势：

1. **命名空间隔离**：`.ta.macd()` 不会和 DataFrame 的现有方法冲突——如果未来 pandas 新增了 `macd` 方法，你的 `.ta.macd()` 不受影响。
2. **自动验证**：`_validate` 方法在每次访问 `.ta` 时自动执行——确保 DataFrame 满足前置条件。
3. **文档化**：`help(df.ta)` 显示你定义的 API 文档。

```python
# ❌ monkey patch
DataFrame.macd = macd_function  # 污染全局命名空间

# ✅ Accessor
@register_dataframe_accessor('ta')
class TAAccessor: ...  # 隔离在 .ta 命名空间下
```

Monkey patch 是'随地大小便'，Accessor 是'公共厕所'——优雅且安全。"

**【技术映射：Accessor = 官方授权的插件系统，monkey patch = 自己偷偷接的电线】**

**大师总结**："Accessor 的三个设计原则：
1. 一个 Accessor 对应一个业务领域（如 `.ta` 技术分析、`.marketing` 营销分析）
2. 方法返回 pandas 对象（DataFrame/Series），保持链式调用兼容
3. `_validate` 做前置校验，`_cache` 做性能优化

accessor 是 pandas 的'插件系统'——让你在不修改 pandas 源码的前提下，为 DataFrame 增加领域专用功能。"

## 3. 项目实战

### 3.1 准备

```bash
pip install pandas numpy
```

### 3.2 实现技术分析 Accessor

```python
# ta_accessor.py
import pandas as pd
import numpy as np

@pd.api.extensions.register_dataframe_accessor('ta')
class TechnicalAnalysisAccessor:
    """股票技术分析访问器

    使用方式: df.ta.macd() / df.ta.rsi(14) / df.ta.bollinger()
    要求 DataFrame 必须有 'close' 列
    """
    def __init__(self, pandas_obj):
        self._obj = pandas_obj
        self._validate()

    def _validate(self):
        if 'close' not in self._obj.columns:
            raise AttributeError("TA Accessor 需要 'close' 列")

    def macd(self, fast=12, slow=26, signal=9):
        df = self._obj
        ema_fast = df['close'].ewm(span=fast, adjust=False).mean()
        ema_slow = df['close'].ewm(span=slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
        histogram = macd_line - signal_line
        return pd.DataFrame({
            'macd': macd_line, 'signal': signal_line, 'histogram': histogram
        }, index=df.index)

    def rsi(self, period=14):
        delta = self._obj['close'].diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)
        avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        return (100 - 100 / (1 + rs)).rename('rsi')

    def sma(self, period=20):
        """简单移动平均"""
        return self._obj['close'].rolling(period).mean().rename(f'sma_{period}')

    def bollinger(self, period=20, std=2):
        sma = self.sma(period)
        std_dev = self._obj['close'].rolling(period).std()
        return pd.DataFrame({
            'upper': sma + std * std_dev,
            'middle': sma,
            'lower': sma - std * std_dev,
        }, index=self._obj.index)

    def summary(self):
        """一键生成常用技术指标汇总"""
        return pd.DataFrame({
            'close': self._obj['close'],
            'sma_20': self.sma(20),
            'rsi_14': self.rsi(14),
        }).join(self.macd())
```

### 3.3 测试 Accessor

```python
# test_ta_accessor.py
import pandas as pd
import numpy as np
from ta_accessor import TechnicalAnalysisAccessor  # 触发注册

# 模拟行情数据
np.random.seed(42)
dates = pd.date_range('2025-01-01', periods=200, freq='B')
close = 100 * np.exp(np.cumsum(np.random.normal(0.001, 0.02, 200)))
df = pd.DataFrame({
    'open': close * np.random.uniform(0.99, 1.01, 200),
    'high': close * np.random.uniform(1.00, 1.03, 200),
    'low': close * np.random.uniform(0.97, 1.00, 200),
    'close': close,
    'volume': np.random.randint(100000, 1000000, 200),
}, index=dates)

# 使用 Accessor
print("===== MACD =====")
print(df.ta.macd().tail().to_string())

print("\n===== RSI =====")
print(df.ta.rsi(14).tail().to_string())

print("\n===== 布林带 =====")
print(df.ta.bollinger().tail().to_string())

print("\n===== 一键汇总 =====")
print(df.ta.summary().tail().to_string())
```

### 3.4 实现营销分析 Accessor

```python
# marketing_accessor.py
import pandas as pd

@pd.api.extensions.register_dataframe_accessor('marketing')
class MarketingAccessor:
    """营销分析访问器——留存、漏斗、同期群"""
    def __init__(self, pandas_obj):
        self._obj = pandas_obj

    def retention(self, cohort_col='cohort_week', period_col='week_number', user_col='user_id'):
        """计算留存率矩阵"""
        df = self._obj
        cohort = df.pivot_table(index=cohort_col, columns=period_col,
                                values=user_col, aggfunc='nunique')
        return cohort.div(cohort.iloc[:,0], axis=0).round(3)

    def funnel(self, steps, user_col='user_id'):
        """计算转化漏斗: steps=['view','click','purchase']"""
        counts = {s: self._obj[self._obj['event']==s][user_col].nunique() for s in steps}
        result = pd.DataFrame({'step': list(counts.keys()), 'users': list(counts.values())})
        result['rate'] = (result['users'] / result['users'].iloc[0] * 100).round(1)
        result['step_rate'] = (result['users'] / result['users'].shift(1) * 100).round(1)
        return result
```

### 3.5 Series Accessor 示例

```python
# series_accessor.py
import pandas as pd

@pd.api.extensions.register_series_accessor('stats')
class StatsAccessor:
    """Series 统计访问器"""
    def __init__(self, pandas_obj):
        self._obj = pandas_obj

    def zscore(self):
        return (self._obj - self._obj.mean()) / self._obj.std()

    def normalize(self, method='minmax'):
        if method == 'minmax':
            return (self._obj - self._obj.min()) / (self._obj.max() - self._obj.min())
        elif method == 'zscore':
            return self.zscore()

# 使用
s = pd.Series([10, 20, 30, 40, 50])
print(f"Z-score: {s.stats.zscore().tolist()}")
print(f"MinMax: {s.stats.normalize('minmax').round(2).tolist()}")
```

### 3.6 常见坑及解决方法

| 问题 | 原因 | 解决方法 |
|------|------|----------|
| Accessor 访问返回 AttributeError | `_validate` 条件不满足 | 检查 DataFrame 是否有必要的列 |
| 多次访问 `.ta` 每次都重建实例 | 每次属性访问都触发 `__init__` | 在 `__init__` 中设置缓存字典 |
| Accessor 返回非 DataFrame 破坏了链式调用 | 返回了 dict 或 scalar | 返回 DataFrame/Series 保持一致 |

### 3.7 测试验证

```python
# test_ch38.py
import pandas as pd
from ta_accessor import TechnicalAnalysisAccessor

def test_accessor_exists():
    df = pd.DataFrame({'close': [10, 20, 30]})
    assert hasattr(df, 'ta')

def test_macd_returns_df():
    df = pd.DataFrame({'close': [10, 20, 30, 40, 50]})
    result = df.ta.macd()
    assert 'macd' in result.columns

def test_missing_column_raises():
    df = pd.DataFrame({'price': [10, 20]})
    try:
        _ = df.ta.macd()
        assert False
    except AttributeError:
        pass

if __name__ == '__main__':
    test_accessor_exists(); test_macd_returns_df(); test_missing_column_raises()
    print("OK 所有测试通过")
```

**完整代码清单**：参见专栏配套仓库 `column/code/ch38/`。

## 4. 项目总结

### 4.1 优点 & 缺点

| 维度 | Accessor | Monkey Patch | 普通函数库 |
|------|---------|-------------|----------|
| 命名空间 | 隔离 (df.ta.xxx) | 污染 (df.xxx) | 无冲突 |
| 自动校验 | `_validate` 每次执行 | 无 | 无 |
| 可发现性 | `help(df.ta)` | 无 | 需查文档 |
| 链式调用 | `df.ta.macd().ta.rsi()` | 不自然 | 需 pipe |
| 开发成本 | 中 | 低 | 低 |

### 4.2 深入：Accessor 的注册和查找机制

当 Python 解释器加载包含 `@register_dataframe_accessor('ta')` 的模块时，pandas 做了以下事情：

1. **全局注册表更新**：`pd.core.accessor._accessors` 字典中添加 `{'ta': TechnicalAnalysisAccessor}` 条目。这个字典维护了所有已注册的 accessor 名称和对应的类。

2. **NDFrame 的 `__getattr__` 拦截**：当你访问 `df.ta` 时，DataFrame 的 `__getattr__` 方法被调用。pandas 在 `NDFrame.__getattr__` 中检查属性名是否在 `_accessors` 字典中。如果命中——实例化对应的 Accessor 类（调用 `__init__(self, pandas_obj)`），并将实例缓存在 `df._accessors` 字典中。

3. **缓存机制**：同一个 DataFrame 对象的同一个 Accessor 只会实例化一次——第一次访问 `df.ta` 时创建 `TAAccessor` 实例并存入 `df._accessors['ta']`，后续访问直接返回缓存。这意味着你可以在 `__init__` 中做重计算（如构建查找索引），因为它只执行一次。

4. **验证时机**：`_validate()` 在每次 `__init__` 调用时执行。但如果 Accessor 被缓存了（即 `__init__` 只执行一次），`_validate` 也只执行一次。如果 DataFrame 后续被修改（如新增/删除列），缓存的 Accessor 实例不会自动重新验证——你需要确保在使用前 DataFrame 满足前置条件。

**Accessor 与 `pipe` 的配合**：如果 Accessor 方法返回 DataFrame，你可以继续链式调用：

```python
(df.ta.macd()          # 返回含 MACD 列的 DataFrame
   .ta.rsi(14)          # 在同一个 df 上继续计算 RSI
   .ta.bollinger()      # 计算布林带
)
```

注意 `.ta` 每次都会创建新的 Accessor 实例（因为返回的是新的 DataFrame），但 `_validate` 确保了新 DataFrame 也满足前置条件。

### 4.2 适用场景

- **适用场景**：团队内部领域专用 API、统一业务分析函数、需要在 DataFrame 上挂载额外功能
- **不适用场景**：个人临时分析——过度封装得不偿失

### 4.3 注意事项

- **Accessor 名称不能和现有属性冲突**：`df.plot`、`df.sparse` 等已被占用，不能注册同名的 accessor；
- **_obj 是引用不是拷贝**：修改 `_obj` 会反映到原始 DataFrame——注意副作用；
- **Accessor 不支持 `inplace` 修改**：要修改 DataFrame 需要返回新的并赋值。

### 4.4 常见踩坑经验

1. **`_validate` 太严格导致正常使用报错**：比如要求必须有 `close` 列，但有时候只想用 accessor 的某个不需要 `close` 的方法——改为在具体方法中校验。
2. **Accessor 实例重复创建导致缓存失效**：`__init__` 在每个属性访问时调用——不要在 `__init__` 中做重计算。
3. **返回 DataFrame 但丢失了原始 Index**：确保返回结果 `index=df.index`。

### 4.5 思考题

1. Accessor 是如何被注册到 DataFrame 上的？阅读 `pandas/core/accessor.py` 中的 `_register_accessor` 函数源码。
2. 如果同时注册了 DataFrame 和 Series 的 Accessor（同名），它们会冲突吗？为什么？

（答案将在第 39 章附录中给出）

### 4.6 推广计划提示

- **开发**：将团队的业务分析函数逐步迁移到 Accessor，统一 API 风格；
- **架构师**：为每个业务领域（风控、营销、财务）设计专属 Accessor。

---

> **源码关联**：pandas/core/accessor.py、pandas/tests/test_register_accessor.py
