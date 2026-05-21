# 第4章：数据读取实战——CSV、Excel、JSON 一网打尽

## 1. 项目背景

某大型零售集团的 BI 团队每周要处理来自三个部门的数据源：

- **客服系统**导出的订单投诉 CSV（GBK 编码，30 万行，分隔符为制表符），包含投诉单号、订单号、投诉类型、用户手机号、投诉时间；
- **财务系统**导出的退款 Excel（含 5 个 Sheet——每 Sheet 对应一个退款渠道：支付宝、微信、银行卡、余额、优惠券），每个 Sheet 的列结构略有不同；
- **活动运营系统**导出的促销活动 JSON（嵌套结构），包含活动 ID、活动名称、优惠明细（嵌套数组，每个元素含门槛金额和减免金额）。

业务需求是：**把这三份数据整合成一张"订单全貌宽表"**，每条记录包含：原始订单信息、是否有投诉、投诉类型、退款金额及渠道、参与的活动及优惠金额。市场经理要用这张表分析"哪类促销活动的投诉率最高""哪个退款渠道的退款金额最大"。

目前 BI 团队的小张靠手工处理这些数据：

**痛点一：CSV 编码坑**。客服系统的 CSV 是 GBK 编码，小张直接用 Excel 打开——中文全部乱码。他需要先用记事本转码再导入 Excel，来回倒了三次才搞清楚编码问题。

**痛点二：多 Sheet Excel 处理繁琐**。财务 Excel 有 5 个 Sheet，每个 Sheet 列名不同，比如"退款金额"有的叫"退款金额(元)"有的叫"实退金额"。小张需要手工打开 5 个 Sheet，重命名列头，再逐一复制粘贴到汇总表。

**痛点三：嵌套 JSON 无法直接展开**。活动 JSON 中 `discounts: [{"threshold": 100, "reduce": 10}, {"threshold": 200, "reduce": 30}]`，这样的嵌套结构在 Excel 中完全无法解析。小张只能手动逐条填进表格——150 个活动规则，填了两个下午。

痛点流程：

```
客服CSV (GBK编码, TSV分隔) ──┐
财务Excel (5个Sheet)        ──┤── 手工转码+复制粘贴 ──→ "订单宽表" ✓(3天)
活动JSON (嵌套结构)          ──┘
                              ↑
                       编码混淆 | Sheet合并 | JSON扁平化
```

本章将用 pandas 的 IO 子系统一次性解决这三个痛点，展示如何把杂乱的原始数据高效、准确地读入 DataFrame。

## 2. 项目设计：剧本式交锋对话

> 场景：小张把问题抛到了技术群，小胖第一个跳出来回复。

**小胖**："CSV 编码是什么鬼？不就是个文本文件吗？我用记事本打开都是正常的啊。"

**小白**："那是因为你用的是 UTF-8 默认编码。客服系统是 GBK 的老系统，Windows 中文地区很多遗留软件都用 GBK——这是历史遗留问题。"

**小胖**："那 pandas 咋搞定编码？"

**大师**："`pd.read_csv` 有一个 `encoding` 参数，直接指定 `encoding='gbk'` 就行。更妙的办法是用 `chardet` 库自动检测编码——但生产环境我建议显式指定，别靠猜测。"

**【技术映射：encoding 参数 = 钥匙上的齿纹——不同编码需要不同的"解码钥匙"，用错了开不了门】**

**小白**："那分隔符呢？这个 CSV 不是逗号分割的，是制表符。"

**大师**："`read_csv` 默认分隔符是逗号，但可以通过 `sep` 参数指定。制表符用 `sep='\t'`。此外还有 `sep='|'`（管道符）、`sep=';'`（欧洲 CSV 常用）等。如果不确定分隔符，先打开文件看一眼就知道了。"

**小胖**："那 Excel 多个 Sheet 怎么写？我一个一个读岂不是累死？"

**大师**："两种方式：
1. `pd.read_excel('file.xlsx', sheet_name=None)`——返回一个字典，key 是 Sheet 名，value 是 DataFrame。然后 `pd.concat` 合并。
2. `pd.read_excel('file.xlsx', sheet_name='支付宝')`——指定读某一个 Sheet。

更高级的是，你还可以用 `sheet_name=[0, 1, '支付宝']` 混合使用位置和名称。"

**【技术映射：sheet_name=None = 一次性把所有菜（Sheet）都端上桌】**

**小白**："但如果每个 Sheet 列名不完全一致怎么办？比如'退款金额'vs'实退金额'。"

**大师**："读取后做列名统一化。可以写一个映射字典做 `rename`，也可以用 `usecols` 只读需要的列并统一命名。这是数据清洗的第一步——**标准化**。"

**小胖**：（挠头）"那嵌套 JSON 呢？我一个 `pd.read_json` 能用吗？"

**大师**："`read_json` 只适用于扁平 JSON 或 ndjson（每行一个 JSON 对象）。如果你的 JSON 有嵌套数组，需要 `json_normalize`（在 pandas 1.x 中）或 `pd.json_normalize`（pandas 2.0+）。它能把嵌套结构自动"拍平"为多行：

```python
data = [{'id': 1, 'discounts': [
    {'threshold': 100, 'reduce': 10},
    {'threshold': 200, 'reduce': 30}
]}]
pd.json_normalize(data, 'discounts', ['id'])
# → threshold  reduce  id
#        100      10   1
#        200      30   1
```

参数 `record_path` 指定要'展开'的嵌套键，`meta` 指定需要保留的上级字段。"

**【技术映射：json_normalize = 把一个多层嵌套的便当盒拆成平铺的一层，每个内层元素单独成行】**

**小白**："那大数据量的时候，一次性读到内存会不会爆？"

**大师**："关键参数 `chunksize`。`read_csv('big.csv', chunksize=10000)` 返回一个迭代器，每次吐出 10000 行。你可以在循环中对每块做过滤、聚合，最后汇总——这就是分块计算的雏形。第 23 章会深入展开。"

**小白**："日期字段呢？读进来全是字符串，还得手工转？"

**大师**："用 `parse_dates` 参数。三种用法：
- `parse_dates=['date']`：指定某列解析为日期；
- `parse_dates=[['date', 'time']]`：把两列拼接解析（如日期列 + 时间列）；
- `parse_dates=True`：自动推断日期列（不推荐，性能差且不可控）。

生产环境我建议显式指定，最好在读取阶段就完成 dtype 和日期解析——比读进来再转高效得多。"

**【技术映射：parse_dates = 在卸货时就分类打包好，而不是把所有东西丢进仓库再分拣】**

**小白**："读 Excel 的时候能不能限制只读某些列？有些 Excel 几十列我只想要 4 列。"

**大师**："`usecols` 参数。CSV 和 Excel 都支持：
- CSV：`usecols=['order_id', 'amount', 'date']` 或 `usecols=[0, 2, 5]`
- Excel：`usecols='A:C,F'`（Excel 列范围格式）或列名列表

这不仅仅是便利性——只读需要的列可以减少 50-80% 的内存占用。"

**大师总结**："今天把 pandas IO 层的四大核心能力串联了：
- `read_csv` 的编码、分隔符、chunksize、dtype、parse_dates
- `read_excel` 的多 Sheet、usecols
- `json_normalize` 的嵌套扁平化
- 以及贯穿始终的'在读取时而非读取后做类型转换'原则。

下面我们就把三份真实数据读进来，整合成一张订单全貌宽表。"

## 3. 项目实战

### 3.1 环境准备

```bash
pip install pandas openpyxl
```

### 3.2 模拟数据生成

```python
# generate_multi_source_data.py
import pandas as pd
import numpy as np
import json
from pathlib import Path

np.random.seed(42)
Path('data').mkdir(exist_ok=True)

# ===== 1. 客服CSV — GBK编码，制表符分隔 =====
n_complaints = 5000
complaints = pd.DataFrame({
    '投诉单号': [f'TS{10000+i}' for i in range(n_complaints)],
    '订单号': [f'ORD{np.random.randint(5000,15000)}' for _ in range(n_complaints)],
    '投诉类型': np.random.choice(['物流延迟', '商品质量', '价格争议', '客服态度'], n_complaints),
    '用户手机': [f'138{np.random.randint(10000000,99999999)}' for _ in range(n_complaints)],
    '投诉时间': pd.date_range('2025-03-01', periods=n_complaints, freq='3min').strftime('%Y-%m-%d %H:%M:%S')
})
complaints.to_csv('data/投诉数据.csv', sep='\t', index=False, encoding='gbk')
print(f"1. 投诉 CSV 已生成 (GBK + TSV): {len(complaints)} 条")

# ===== 2. 财务Excel — 5个Sheet，列名不统一 =====
refund_data = {
    '支付宝': pd.DataFrame({
        '订单号': [f'ORD{np.random.randint(5000,15000)}' for _ in range(1000)],
        '退款金额(元)': np.round(np.random.uniform(10, 500, 1000), 2),
        '退款时间': pd.date_range('2025-03-01', periods=1000, freq='h').strftime('%Y-%m-%d %H:%M:%S')
    }),
    '微信': pd.DataFrame({
        '订单号': [f'ORD{np.random.randint(5000,15000)}' for _ in range(800)],
        '实退金额': np.round(np.random.uniform(10, 500, 800), 2),
        '退款时间': pd.date_range('2025-03-01', periods=800, freq='h').strftime('%Y-%m-%d %H:%M:%S')
    }),
    '银行卡': pd.DataFrame({
        '订单号': [f'ORD{np.random.randint(5000,15000)}' for _ in range(600)],
        '退款金额(元)': np.round(np.random.uniform(50, 2000, 600), 2),
        '退款日期': pd.date_range('2025-03-01', periods=600, freq='h').strftime('%Y-%m-%d')
    }),
    '余额': pd.DataFrame({
        '订单号': [f'ORD{np.random.randint(5000,15000)}' for _ in range(300)],
        '退款金额': np.round(np.random.uniform(5, 100, 300), 2),
        '退款时间': pd.date_range('2025-03-01', periods=300, freq='h').strftime('%Y-%m-%d %H:%M')
    }),
    '优惠券': pd.DataFrame({
        '订单号': [f'ORD{np.random.randint(5000,15000)}' for _ in range(200)],
        '实退金额(元)': np.round(np.random.uniform(5, 50, 200), 2),
        '退款时间': pd.date_range('2025-03-01', periods=200, freq='h').strftime('%Y-%m-%d %H:%M:%S')
    }),
}

with pd.ExcelWriter('data/退款数据.xlsx', engine='openpyxl') as writer:
    for sheet_name, df in refund_data.items():
        df.to_excel(writer, sheet_name=sheet_name, index=False)
print(f"2. 退款 Excel 已生成: {list(refund_data.keys())}")

# ===== 3. 活动JSON — 嵌套结构 =====
promotions = []
for i in range(150):
    thresholds = sorted(np.random.choice([50, 100, 200, 300, 500], size=np.random.randint(1, 4), replace=False).tolist())
    promo = {
        'activity_id': f'ACT{1000+i}',
        'activity_name': np.random.choice(['满减券', '新人礼包', '会员折扣', '秒杀券']),
        'start_date': '2025-03-01',
        'end_date': '2025-03-31',
        'discounts': [
            {'threshold': t, 'reduce': np.random.randint(5, t//3)}
            for t in thresholds
        ]
    }
    promotions.append(promo)

with open('data/活动规则.json', 'w', encoding='utf-8') as f:
    json.dump(promotions, f, ensure_ascii=False, indent=2)
print(f"3. 活动 JSON 已生成: {len(promotions)} 个活动")
```

### 3.3 主流程实现

```python
# read_and_merge.py
"""从 CSV、Excel、JSON 三源读取数据并整合为订单全貌宽表"""
import pandas as pd
import json
from pathlib import Path

print("=" * 50)
print("开始多源数据整合")
print("=" * 50)

# ===== 第 1 步：读取 GBK 编码的 TAB 分隔 CSV =====
print("\n[1/4] 读取投诉 CSV (GBK + 制表符)...")

complaints = pd.read_csv(
    'data/投诉数据.csv',
    sep='\t',                    # 制表符分隔
    encoding='gbk',              # GBK 编码
    dtype={'用户手机': str},      # 手机号保留为字符串，避免科学计数法
    parse_dates=['投诉时间']      # 读入时解析日期
)
print(f"  ✓ 读取 {len(complaints)} 条投诉记录")
print(f"  列名: {complaints.columns.tolist()}")
print(f"  dtypes:\n{complaints.dtypes}")

# ===== 第 2 步：读取多 Sheet Excel 并统一列名 =====
print("\n[2/4] 读取退款 Excel (5个Sheet，列名不统一)...")

# 读取所有 Sheet
all_sheets = pd.read_excel('data/退款数据.xlsx', sheet_name=None)
print(f"  Sheet 列表: {list(all_sheets.keys())}")

# 统一列名映射
COLUMN_MAPPING = {
    '退款金额(元)': '退款金额',
    '实退金额':    '退款金额',
    '退款金额':    '退款金额',
    '实退金额(元)': '退款金额',
    '退款日期':    '退款时间',
}

refund_frames = []
for channel, df in all_sheets.items():
    # 统一列名
    df = df.rename(columns=COLUMN_MAPPING)
    # 添加退款渠道列
    df['退款渠道'] = channel
    # 确保退款时间列存在，如缺失则填充
    if '退款时间' not in df.columns:
        df['退款时间'] = None
    # 只保留三列
    df = df[['订单号', '退款金额', '退款时间', '退款渠道']]
    refund_frames.append(df)
    print(f"    {channel}: {len(df)} 条 → 列名: {df.columns.tolist()}")

# 纵向合并所有 Sheet
refunds = pd.concat(refund_frames, ignore_index=True)
refunds['退款时间'] = pd.to_datetime(refunds['退款时间'], errors='coerce')
print(f"  ✓ 合并后共 {len(refunds)} 条退款记录，{refunds['退款渠道'].nunique()} 个渠道")

# ===== 第 3 步：读取并扁平化嵌套 JSON =====
print("\n[3/4] 读取并扁平化活动规则 JSON...")

with open('data/活动规则.json', 'r', encoding='utf-8') as f:
    promotions_raw = json.load(f)

# json_normalize 展开嵌套 discounts 数组
promotions = pd.json_normalize(
    promotions_raw,
    record_path='discounts',       # 嵌套路径（要展开的数组）
    meta=['activity_id', 'activity_name', 'start_date', 'end_date'],  # 保留的上级字段
    record_prefix='discount_'       # 展开字段的前缀
)
promotions.columns = ['门槛金额', '优惠金额', '活动ID', '活动名称', '开始日期', '结束日期']
print(f"  ✓ 原始活动数: {len(promotions_raw)}, 展开后规则数: {len(promotions)}")
print(f"  前 3 条:")
print(promotions.head(3).to_string(index=False))

# ===== 第 4 步：模拟订单主表并关联三张表 =====
print("\n[4/4] 构建订单全貌宽表...")

# 模拟订单主表 (真实场景中从数据库或另一份 CSV 读取)
np.random.seed(42)
orders = pd.DataFrame({
    '订单号': [f'ORD{i}' for i in range(5000, 15000)],
    '用户ID': np.random.randint(10000, 20000, 10000),
    '下单时间': pd.date_range('2025-03-01', periods=10000, freq='5min').strftime('%Y-%m-%d %H:%M:%S'),
    '订单金额': np.round(np.random.uniform(50, 3000, 10000), 2)
})

# 左连接投诉表
order_wide = orders.merge(
    complaints[['订单号', '投诉类型', '投诉时间']],
    on='订单号', how='left', suffixes=('', '_投诉')
)

# 左连接退款表
order_wide = order_wide.merge(
    refunds[['订单号', '退款金额', '退款渠道']],
    on='订单号', how='left', suffixes=('', '_退款')
)

print(f"  ✓ 宽表构建完成: {len(order_wide)} 行 × {len(order_wide.columns)} 列")
print(f"  有投诉记录: {(order_wide['投诉类型'].notna()).sum()} 条")
print(f"  有退款记录: {(order_wide['退款渠道'].notna()).sum()} 条")

# 导出结果
order_wide.to_csv('data/订单全貌宽表.csv', index=False, encoding='utf-8-sig')
print(f"\n  ✓ 订单全貌宽表已导出至 data/订单全貌宽表.csv")

# 快速统计
print("\n" + "=" * 50)
print("快速统计分析")
print("=" * 50)
print(f"\n退款渠道分布:")
print(order_wide['退款渠道'].value_counts(dropna=False).to_string())
print(f"\n投诉类型分布:")
print(order_wide['投诉类型'].value_counts(dropna=False).to_string())
```

### 3.4 运行输出示例

```
==================================================
开始多源数据整合
==================================================

[1/4] 读取投诉 CSV (GBK + 制表符)...
  ✓ 读取 5000 条投诉记录
  列名: ['投诉单号', '订单号', '投诉类型', '用户手机', '投诉时间']
  dtypes:
  投诉单号     object
  订单号       object
  投诉类型     object
  用户手机     object
  投诉时间     datetime64[ns]

[2/4] 读取退款 Excel (5个Sheet，列名不统一)...
  Sheet 列表: ['支付宝', '微信', '银行卡', '余额', '优惠券']
    支付宝: 1000 条 → 列名: ['订单号', '退款金额', '退款时间', '退款渠道']
    微信: 800 条 → ...
  ✓ 合并后共 2900 条退款记录，5 个渠道

[3/4] 读取并扁平化活动规则 JSON...
  ✓ 原始活动数: 150, 展开后规则数: 342

[4/4] 构建订单全貌宽表...
  ✓ 宽表构建完成: 10000 行 × 7 列

快速统计分析
==================================================
退款渠道分布:
NaN      7951
支付宝    429
...
```

### 3.5 可能的坑及解决方法

| 问题 | 原因 | 解决方法 |
|------|------|----------|
| CSV 部分列被截断 | 某行列数多于其他行 | `on_bad_lines='warn'` 跳过坏行 |
| `read_excel` 报错 "Unknown engine" | 未安装 openpyxl 或 xlrd | `pip install openpyxl` 处理 `.xlsx` |
| JSON 解析失败 | JSON 不是标准格式（单引号、尾随逗号） | 先 `json.loads` 验证，修改源数据 |
| `json_normalize` 后行数暴增 | 嵌套数组展开导致笛卡尔积 | 检查是否误用了非数组字段作为 record_path |
| 大 Excel 文件读取很慢 | openpyxl 逐行解析 | 用 `read_excel(..., engine='openpyxl', usecols=cols)` 限制列数 |
| 日期解析后变成 NaT | 日期格式不匹配 | `pd.to_datetime(..., format='%Y%m%d')` 显式指定 |

### 3.6 测试验证

```python
# test_ch04.py
import pandas as pd
from pathlib import Path

def test_csv_encoding():
    """验证 GBK CSV 能被正确读取"""
    df = pd.read_csv('data/投诉数据.csv', sep='\t', encoding='gbk')
    assert len(df) == 5000, "行数不对"
    assert '投诉类型' in df.columns, "中文列名丢失"

def test_excel_sheet_count():
    """验证所有 Sheet 都被读取"""
    sheets = pd.read_excel('data/退款数据.xlsx', sheet_name=None)
    assert len(sheets) == 5, f"期望 5 个 Sheet，实际 {len(sheets)}"
    assert '支付宝' in sheets, "缺少'支付宝' Sheet"

def test_refund_merge():
    """验证退款合并后渠道数正确"""
    sheets = pd.read_excel('data/退款数据.xlsx', sheet_name=None)
    frames = []
    for ch, df in sheets.items():
        df = df.iloc[:, :2].copy()
        df.columns = ['订单号', '退款金额']
        df['退款渠道'] = ch
        frames.append(df)
    refunds = pd.concat(frames)
    assert refunds['退款渠道'].nunique() == 5

def test_json_normalize():
    """验证 JSON 扁平化行数 > 原始活动数"""
    import json
    with open('data/活动规则.json', 'r', encoding='utf-8') as f:
        raw = json.load(f)
    flat = pd.json_normalize(raw, record_path='discounts', meta=['activity_id'])
    assert len(flat) > len(raw), "展开后行数应大于原始活动数"

if __name__ == '__main__':
    test_csv_encoding()
    test_excel_sheet_count()
    test_refund_merge()
    test_json_normalize()
    print("✓ 所有测试通过")
```

**完整代码清单**：参见专栏配套仓库 `column/code/ch04/` 下的 `generate_multi_source_data.py`、`read_and_merge.py`、`test_ch04.py`。

## 4. 项目总结

### 4.1 优点 & 缺点

| 维度 | pandas IO 方案 | 手工 Excel/文本编辑 | Python 原生库 (csv/json模块) |
|------|---------------|-------------------|---------------------------|
| 编码处理 | `encoding` 参数一行搞定 | 需手动另存为改编码 | `open(encoding='...')` 较底层 |
| 多 Sheet 合并 | `sheet_name=None` + `concat` | 手动逐个 Sheet 复制 | 需使用 openpyxl/lib 自行遍历 |
| 嵌套 JSON | `json_normalize` 自动扁平化 | 几乎不可行 | 需手写递归展开逻辑 |
| 性能 | C/PyArrow 引擎，读取快 | 慢且依赖软件性能 | 纯 Python，较慢 |
| 类型推断 | 支持 `dtype` 显式指定 | 需手工设置格式 | 需自行转换 |
| 容错性 | `on_bad_lines` 等参数处理异常 | 人工发现后处理 | 需自行处理异常 |

### 4.2 适用场景

- **适用场景**：
  1. 多源异构数据（CSV + Excel + JSON）的批量整合，如日报、周报的数据采集
  2. 遗留系统导出数据的清洗入库——处理各种非标准编码、分隔符
  3. 批量处理多 Sheet 的财务报表、运营报表
  4. 从 API 返回的嵌套 JSON 中提取结构化分析数据
  5. 定期从固定路径扫描新文件并自动处理的 ETL 轻量场景
- **不适用场景**：
  1. 实时数据流采集——pandas 是批处理工具，请用 Kafka/Flink
  2. 需要保留 Excel 复杂格式（合并单元格、VBA 宏）的场景

### 4.3 注意事项

- **编码优先使用 utf-8-sig**：Windows 下 Excel 导出的 CSV 常带 BOM 头，用 `utf-8-sig` 可以自动处理；
- **Excel 读取引擎选择**：`.xls` 用 `xlrd`，`.xlsx` 用 `openpyxl`，大文件建议用 `calamine`（第三方，速度更快）；
- **手机号/身份证号**：读取时务必加 `dtype={'phone': str}`，否则 18 位身份证末位会丢失精度变成科学计数法；
- **json_normalize 的 max_level**：默认只展开一层嵌套，多层嵌套需用 `max_level` 或多次调用。

### 4.4 常见踩坑经验

1. **GBK 编码文件用 UTF-8 读，然后中文乱码，但程序不报错**：一位同事做日报脚本，投诉 CSV 中的"商品质量"读进来变成了"鍟嗗搧璐ㄩ噺"，所有 `value_counts` 统计全错了。排查半天才意识到是编码问题。教训：读取陌生的数据源时，先用记事本或 `file` 命令确认编码。
2. **Excel 日期列读进来变成数字**：Excel 内部用"自 1900-01-01 以来的天数"表示日期。如果 `read_excel` 没有正确识别，读进来的可能是一个 5 位数字（如 45000）。解决方法：读取后用 `pd.to_datetime(df['date'], unit='D', origin='1899-12-30')` 转换。
3. **json_normalize 对空嵌套数组的处理**：如果某条记录的 `discounts` 字段是空列表 `[]`，第 3 步扁平化后该记录会消失（因为 `record_path` 里没有元素可展开）。解决方法：先 `json_normalize` 展开，再用 `merge` 把原表主键左连接回来，缺失的用 `fillna` 处理。

### 4.5 思考题

1. 如果客户系统的 CSV 更新为每天导出 500MB，用 `read_csv` 一次性读取会 OOM。如何用 `chunksize` 参数分块读取，并在每个分块内完成日期解析和类型转换，减少总体执行时间？
2. 尝试用 `pd.read_csv` 的 `usecols` 参数只读取需要的列，对比全量读取的内存占用差异（用 `memory_usage(deep=True)` 验证）。

（答案将在第 5 章附录中给出）

### 4.6 推广计划提示

- **开发/数据工程师**：将本章的 IO 函数封装成模块，替换团队中的手工数据采集环节；
- **测试**：重点掌握 `test_ch04.py` 中的数据完整性测试思路，确保 IO 函数在各种编码/格式下输出正确；
- **运营/数据分析**：学会 `json_normalize` 打开嵌套 JSON 数据的大门，许多第三方 API 数据都依赖此能力。

---

> **源码关联**：pandas/io/parsers/readers.py、pandas/io/excel/_base.py、pandas/io/json/_normalize.py
