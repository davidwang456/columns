# 第19章：MultiIndex 与多维数据建模

## 1. 项目背景

某跨国零售集团在中国有 5 个区域（华东、华南、华北、西南、华中），每个区域下有 4-6 个城市，每个城市有 2-5 家门店，每家门店销售 5 个品类、每个品类下有 10-20 个 SKU。集团经营分析师老田需要构建一个"区域-城市-门店-品类"四维经营模型，用于月度经营分析会。

数据源是门店月度销售汇总表（monthly_sales.csv），包含：区域、城市、门店、品类、月份、销售额、销量、毛利率。约 2000 行（5 区域 × 25 城市 × 100 门店 × 5 品类 = 可能组合）。

老田需要在同一张表里回答不同维度的问题：
- "华东地区各城市各品类的销售额对比"
- "各门店在所属品类中的排名"
- "按区域-品类双维度的毛利率分布"
- "每个门店最畅销的品类是什么"

他目前的做法是为每个问题建一张 Excel Sheet，用透视表分别做——结果是一份包含 8 张 Sheet 的 Excel 文件，每张 Sheet 之间的数据关联只能靠人为理解。

**痛点一：维度切换靠重建透视表**。从"区域×品类"切到"城市×门店"需要重新拖拽字段，重建透视表。如果一个分析会涉及 5 种维度组合，就要做 5 张透视表。

**痛点二：多层级聚合结果无法直观展示层次关系**。"区域→城市→门店"天然是层级结构，但 Excel 透视表把它展平成了并列字段，层次感丢失。老田向总监汇报时，总监反复确认"这个城市属于哪个区域"。

**痛点三：跨层级计算需要手工拆合**。"每个门店的销售额占其所在城市的百分比"——这是一个跨两层级的计算（门店层 / 城市层），Excel 需要先分别求和再手工除。

痛点流程：

```
月度销售 (2000行, 5维度)
  ├── 维度切换 → 5 张透视表 → 数据分散
  ├── 层级关系 → 展平 → 层次丢失
  └── 跨层计算 → 先分层求和在手工除 → 繁琐
```

本章将系统讲解 pandas 的 MultiIndex——多层索引的创建、选择、排序、重组，以及 stack/unstack 在宽表长表之间的转换，构建真正的多维数据模型。

## 2. 项目设计：剧本式交锋对话

> 场景：老田在准备经营分析会材料，桌上摆着 8 张 Excel Sheet 的打印稿，小胖路过看了一眼。

**小胖**：（捧着酸奶）"田哥，你这 8 张 Sheet 看的我眼花——你要的东西不就是从不同角度切多维数据集吗？数据库里叫 OLAP，pandas 里应该也有吧？"

**大师**："pandas 的 MultiIndex 就是干这个的——它允许你给 DataFrame 的行和列设置多层标签。比如行是'区域→城市→门店'三层，列是'品类'一层——这就是一张 4 维表。"

**小白**："MultiIndex 怎么创建？我只知道普通 Index。"

**大师**："三种常见方式：

1. **groupby 自然产生**：`df.groupby(['区域','城市','门店']).sum()` 结果自动就是 MultiIndex；
2. **set_index 手动指定**：`df.set_index(['区域','城市','门店'])`；
3. **pd.MultiIndex.from_arrays / from_tuples / from_product**：显式构造完整的笛卡尔积。

其中方式 2 最常用——选几列设为 Index，立刻拥有多层索引能力。"

**【技术映射：MultiIndex = 文件的文件夹嵌套——"公司/部门/项目组/员工"比"公司-部门-项目组-员工"展平更有结构性】**

**小胖**："那 MultiIndex 怎么查？`loc` 还能用吗？"

**大师**："`loc` 完全支持 MultiIndex，而且支持部分层级的选择：

```python
# 四层索引: 区域 → 城市 → 门店 → 品类
df.index = ['华东','华南','华北','西南','华中'] × ...

# 选择华东的所有数据
df.loc['华东']

# 选择华东-上海的所有数据
df.loc[('华东', '上海')]

# 选择华东和华南两个区域
df.loc[(['华东','华南'], slice(None), slice(None))]

# 用 pd.IndexSlice 更优雅
idx = pd.IndexSlice
df.loc[idx['华东':'华南', :, :]]
```

核心规则：`loc` 按层级从左到右匹配——第一层先筛区域，第二层筛城市，以此类推。"

**【技术映射：MultiIndex.loc = 收件地址——'中国/北京/朝阳区/XX大厦'——从大到小逐层定位】**

**小白**："那 stack 和 unstack 是做什么的？我经常看到但不太理解。"

**大师**："`stack` 和 `unstack` 是 MultiIndex 的'变形金刚'——它们在行索引和列索引之间相互转换：

```python
# unstack：把行索引的某一层"旋转"到列上
# pivot_table 的底层实现就是 unstack

# stack：把列"压缩"回行索引
# melt 的底层实现就是 stack

# 实例：
# 行 = (区域, 城市), 列 = (品类) → unstack(level='品类') → 行 = (区域, 城市, 品类), 列 = 空
# 反过来 stack 收回
```

- `unstack(level=-1)`：把最后一层行索引旋转到列上（宽表化）
- `stack(level=-1)`：把最外层列旋转到行索引上（长表化）

`level` 可以是指定层级的名称或序号。"

**【技术映射：stack = 把横向的书架旋转 90 度变成纵向抽屉，unstack = 把抽屉转回书架】**

**小白**："swaplevel 和 reorder_levels 又是做什么的？"

**大师**："这两个函数用来重组 MultiIndex 的层级顺序：

```python
# 原始：区域 → 城市 → 品类
# 想变成：品类 → 区域 → 城市（品类在前更容易按品类分析）

# swaplevel：交换两个层级的位置
df.swaplevel('区域', '品类')  # 区域和品类互换

# reorder_levels：任意重新排列
df.reorder_levels(['品类','区域','城市'])  # 完全自定义顺序
```

层级顺序很重要——`loc` 按最左侧层级优先查找，`unstack` 默认操作最内层。合理的层级顺序能让后续操作事半功倍。"

**大师总结**："MultiIndex 的五项基本操作：
1. 创建：`set_index` 或 groupby
2. 选择：`loc` 按层级切片
3. 排序：`sort_index` 加速查找
4. 变形：`stack`/`unstack` 行列互换
5. 重组：`swaplevel`/`reorder_levels` 改变层级顺序

掌握这五项，就能在 3-5 维的表格中自由穿行。"

## 3. 项目实战

### 3.1 准备

```bash
pip install pandas numpy
```

### 3.2 模拟数据

```python
# generate_multi_dim_data.py
import pandas as pd
import numpy as np

np.random.seed(42)

regions = ['华东','华南','华北','西南','华中']
cities_map = {
    '华东': ['上海','杭州','南京','苏州','合肥'],
    '华南': ['广州','深圳','东莞','佛山'],
    '华北': ['北京','天津','石家庄','太原'],
    '西南': ['成都','重庆','昆明','贵阳'],
    '华中': ['武汉','长沙','郑州','南昌'],
}
categories = ['服装','电子产品','食品','家居','美妆']

data = []
for region in regions:
    for city in cities_map[region]:
        for i in range(1, np.random.randint(2,5)+1):
            store = f'{city}-{i:02d}店'
            for cat in categories:
                for month in range(1,4):
                    data.append({
                        '区域': region, '城市': city, '门店': store,
                        '品类': cat, '月份': f'2025-{month:02d}',
                        '销售额': round(np.random.uniform(10000, 500000), 0),
                        '销量': np.random.randint(50, 2000),
                        '毛利率': round(np.random.uniform(0.1, 0.5), 4),
                    })

df = pd.DataFrame(data)
df.to_csv('monthly_sales.csv', index=False, encoding='utf-8-sig')
print(f"已生成 {len(df)} 条记录, {df['区域'].nunique()}区域×{df['城市'].nunique()}城市×{df['门店'].nunique()}门店×{df['品类'].nunique()}品类×3月")
```

### 3.3 分步实现

#### 步骤 1：创建 MultiIndex 并选择

**目标**：用 set_index 创建多层索引，用 loc 按层级切片。

```python
# step1_multi_index_select.py
import pandas as pd

df = pd.read_csv('monthly_sales.csv')

# 创建四层行索引 + 一层列索引
df_multi = df.set_index(['区域','城市','门店','品类'])
df_multi = df_multi.sort_index()
print(f"MultiIndex levels: {df_multi.index.nlevels}")
print(f"Level names: {df_multi.index.names}")

# === loc 按层级选择 ===
# 选择华东区域的所有数据
print("\n华东区域数据(前3):")
print(df_multi.loc['华东'].head(3).to_string())

# 选择华东-上海
print("\n华东-上海:")
print(df_multi.loc[('华东','上海')].head(3).to_string())

# 选择多个区域
idx = pd.IndexSlice
subset = df_multi.loc[idx[['华东','华南'], :, :, '服装'], :]
print(f"\n华东+华南 服装品类: {len(subset)} 条")

# 跨区域全选：所有区域-城市的服装
subset2 = df_multi.loc[idx[:, :, :, '服装'], :]
print(f"全区域服装品类: {len(subset2)} 条")
```

#### 步骤 2：unstack 行列转换

**目标**：将品类从行索引旋转到列上，形成交叉表。

```python
# step2_unstack.py
import pandas as pd

df = pd.read_csv('monthly_sales.csv')
df_multi = df.set_index(['区域','城市','门店','品类']).sort_index()

# === unstack：品类从行→列 ===
# 将最内层(品类) unstack 到列上
unstacked = df_multi['销售额'].unstack(level='品类', fill_value=0)
print("unstack 结果 (区域×城市×门店 vs 品类):")
print(unstacked.head(10).round(0).to_string())

# === 跨层 unstack ===
# 汇总到区域-门店层级，展示区域×品类
region_cat = df_multi['销售额'].groupby(['区域','品类']).sum().unstack(level='品类', fill_value=0)
print(f"\n区域×品类 交叉表:")
print(region_cat.round(0).to_string())

# === unstack + stack 来回 ===
# unstack 后再 stack 回去
back_to_multi = unstacked.stack(future_stack=True)
print(f"\nstack 回去后行数: {len(back_to_multi)}")
```

#### 步骤 3：swaplevel 和 reorder_levels 重组层级

**目标**：改变层级顺序以方便不同维度的聚合。

```python
# step3_swap_reorder.py
import pandas as pd

df = pd.read_csv('monthly_sales.csv')
df_multi = df.set_index(['区域','城市','门店','品类']).sort_index()

# === swaplevel：交换两个层级 ===
# 原始顺序：区域→城市→门店→品类
# 交换'门店'和'品类'，让品类在门店前
swapped = df_multi.swaplevel('门店', '品类')
print("swaplevel 后 index names:", swapped.index.names)

# === reorder_levels：完全自由排列 ===
# 新顺序：区域→品类→城市→门店
reordered = df_multi.reorder_levels(['区域','品类','城市','门店'])
reordered = reordered.sort_index()
print("reorder 后:")
print(reordered.head(5).to_string())

# 按品类优先聚合：先按品类看各区域销售额
cat_region = reordered['销售额'].groupby(['品类','区域']).sum()
print(f"\n品类→区域 层级聚合:")
print(cat_region.unstack().round(0).to_string())
```

#### 步骤 4：跨层级计算（门店占城市比例）

**目标**：计算每个门店销售额占其所在城市的比例。

```python
# step4_cross_level_calc.py
import pandas as pd

df = pd.read_csv('monthly_sales.csv')

# 门店级销售额
store_sales = df.groupby(['区域','城市','门店'], as_index=False)['销售额'].sum()

# 城市级销售额 (用 transform 广播到门店级)
store_sales['城市销售额'] = store_sales.groupby(['区域','城市'])['销售额'].transform('sum')
store_sales['门店占比%'] = (store_sales['销售额'] / store_sales['城市销售额'] * 100).round(2)

print("门店在城市内占比 (Top 5):")
print(store_sales.nlargest(5, '门店占比%').to_string(index=False))

# MultiIndex 版本：直接在 MultiIndex 上用 transform
df_multi = df.set_index(['区域','城市','门店']).sort_index()
df_multi['城市销售额'] = df_multi.groupby(['区域','城市'])['销售额'].transform('sum')
df_multi['门店占比'] = (df_multi['销售额'] / df_multi['城市销售额'] * 100).round(2)

# 找出每个城市占比最高的门店
idx = df_multi.groupby(['区域','城市'])['门店占比'].transform('max')
top_store_per_city = df_multi[df_multi['门店占比'] == idx]
print(f"\n各城市占比最高门店 (前5):")
print(top_store_per_city[['销售额','城市销售额','门店占比']].head(5).to_string())
```

### 3.4 常见坑及解决方法

| 问题 | 原因 | 解决方法 |
|------|------|----------|
| MultiIndex loc 返回 DataFrame 而非 Series | 选择第一层时保留了下层 | `df_multi.loc['华东'].iloc[0]` 或继续指定下层 |
| unstack 后列头有 NaN | 某些组合不存在 | `fill_value=0` 填充缺失组合 |
| sort_index 很慢 | MultiIndex 未排序导致 | 创建后立即 sort_index |
| swaplevel 后层级混乱 | 原来的排序被打乱 | swaplevel 后重新 sort_index |
| stack 引入 NaN | 某些列组合不存在 | `dropna=True` 删除缺失行 |

### 3.5 测试验证

```python
# test_ch19.py
import pandas as pd
import numpy as np

def test_multi_index_create():
    df = pd.DataFrame({'A':['a','a','b'], 'B':['x','y','x'], 'V':[1,2,3]})
    mi = df.set_index(['A','B'])
    assert mi.index.nlevels == 2
    assert mi.loc[('a','x'),'V'] == 1

def test_unstack():
    df = pd.DataFrame({'R':['E','E','W'], 'C':['A','B','A'], 'V':[1,2,3]})
    mi = df.set_index(['R','C'])
    wide = mi['V'].unstack(fill_value=0)
    assert wide.loc['E','A'] == 1
    assert wide.loc['W','A'] == 3

def test_swaplevel():
    arrays = [['a','a','b'], [1,2,1], ['x','y','z']]
    mi = pd.MultiIndex.from_arrays(arrays, names=['L1','L2','L3'])
    df = pd.DataFrame({'V':[10,20,30]}, index=mi)
    swapped = df.swaplevel('L1','L3')
    assert swapped.index.names == ['L3','L2','L1']

def test_cross_level_sum():
    df = pd.DataFrame({'R':['A','A','B'], 'C':['x','y','x'], 'V':[10,20,30]})
    df['city_sum'] = df.groupby('R')['V'].transform('sum')
    assert df[df['R']=='A']['city_sum'].unique()[0] == 30

if __name__ == '__main__':
    test_multi_index_create(); test_unstack(); test_swaplevel(); test_cross_level_sum()
    print("OK 所有测试通过")
```

**完整代码清单**：参见专栏配套仓库 `column/code/ch19/`。

## 4. 项目总结

### 4.1 优点 & 缺点

| 维度 | MultiIndex 多维表 | Excel 多 Sheet | 数据库 OLAP |
|------|-----------------|---------------|-------------|
| 维度数量 | 不限层级 | 受界面限制 | CUBE 多维 |
| 查询灵活性 | loc + IndexSlice | 切换透视表 | MDX/SQL |
| 跨层计算 | transform 一行 | 多步手工 | 窗口函数 |
| 行列转换 | stack/unstack 秒切 | 手工重建透视表 | PIVOT/UNPIVOT |
| 内存效率 | 紧凑（编码索引） | 多 Sheet 冗余 | 引擎优化 |
| 学习成本 | 高 | 低 | 高 |

### 4.2 适用场景

- **适用场景**：
  1. 3 层以上层级关系的组织数据（如集团→公司→部门→员工）
  2. 需要频繁切换分析维度的经营分析（行→列、列→行）
  3. 与 pivot_table 配合——pivot_table 内部就是 MultiIndex
  4. 分组后保留层级结构，方便二次聚合
- **不适用场景**：
  1. 只有 1-2 个层级——普通 Index 就够了
  2. 需要频繁按非 Index 列做筛选——先重置 Index

### 4.3 注意事项

- **sort_index 必须做**：未排序的 MultiIndex 会导致 `loc` 报 `UnsortedIndexError`；
- **get_level_values**：获取某一层所有的值 `df.index.get_level_values('区域')`；
- **droplevel**：删除不需要的 Index 层级 `df.droplevel('城市')`；
- **MultiIndex 的 groupby level**：`df.groupby(level='区域')` 直接按 Index 层级分组。

### 4.4 常见踩坑经验

1. **unstack 后列头层级顺序出乎意料**：`df.unstack(level='品类')` 把品类旋转到列的最内层——如果期望品类在最外层，先 reorder_levels 再 unstack。
2. **sort_index 后 loc 仍然报 UnsortedIndexError**：MultiIndex 的排序要求所有层级都排好——检查是否遗漏了某一层未排序。
3. **MultiIndex join 效率低于单层 merge**：如果只是简单关联，先 reset_index 变成普通列，用 merge 关联完再 set_index 回去。

### 4.5 思考题

1. `pd.MultiIndex.from_product` 和 `pd.MultiIndex.from_frame` 的区别是什么？什么场景下用 from_product 而非 set_index？
2. 如果把一个 4 层 MultiIndex 全部 unstack 成列，会产生多少级的列头 MultiIndex？这对可读性有什么影响？

（答案将在第 20 章附录中给出）

### 4.6 推广计划提示

- **数据分析师**：掌握 MultiIndex + pivot_table 的组合，替代 Excel 多 Sheet 透视表；
- **数据工程师**：MultiIndex 的编码存储是性能优化的利器——大数据量比展平列更省内存；
- **架构师**：评估 MultiIndex 与 OLAP 系统（如 ClickHouse）的协作方式。

---

> **源码关联**：pandas/core/indexes/multi.py、pandas/core/reshape/reshape.py
