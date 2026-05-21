# 第21章：SQL 数据库集成与增量同步

## 1. 项目背景

某电商平台的数据团队每天凌晨需要从 MySQL 生产库中同步前一天的订单数据到分析库，生成"日报宽表"后写回 MySQL 的分析表，供 BI 系统查询。数据链路是：

1. 从 MySQL `orders` 表增量拉取昨日订单（约 20 万条/天）
2. 从 `order_items` 表拉取对应订单明细
3. 用 pandas 做数据清洗、关联、聚合
4. 将分析结果写回 MySQL 的 `daily_report` 表

数据工程师小曹目前的做法是：每天凌晨手动登录 MySQL，用 `SELECT * INTO OUTFILE` 导出 CSV，然后用 Python 脚本读 CSV、处理后导出 CSV，再用 `LOAD DATA INFILE` 导回数据库。整套流程依赖中间 CSV 文件，且容易因为字段类型不匹配而失败。

**痛点一：手工导出引入中间文件**。CSV 导出→Python 读→处理→CSV 写→MySQL 导入，中间 CSV 文件是多余的——不仅占用磁盘空间，还多了一次序列化/反序列化开销。更糟的是 MySQL 的 `INTO OUTFILE` 需要 FILE 权限，生产环境 DBA 不给。

**痛点二：增量同步的断点续传问题**。如果凌晨 3 点任务失败，重跑时如何避免重复拉取已处理的数据？小曹靠人工记住"上次处理到了 row_id=1234567"，但一旦忘记就会产生重复纪录。

**痛点三：类型映射不一致**。MySQL 的 `DATETIME` 到 CSV 变成字符串，到 pandas 再解析回 datetime——过程中时区信息丢失。MySQL 的 `DECIMAL(10,2)` 在 CSV 中成了字符串"123.45"，读回 pandas 可能被推断为 float64（丢失精度）。

痛点流程：

```
MySQL 生产库
  └── SELECT INTO OUTFILE → CSV → Python 读
        └── 清洗 → CSV → LOAD DATA → MySQL 分析库
              ├── 中间 CSV 多余 + 需 FILE 权限
              ├── 断点续传靠人工记录
              └── 类型映射丢失 (DATETIME→string→datetime)
```

本章将用 pandas + SQLAlchemy 实现从数据库直读直写，消除中间 CSV，并实现可靠的增量同步策略。

## 2. 项目设计：剧本式交锋对话

> 场景：小曹在周会上抱怨"DBA 不给 FILE 权限导致导出脚本跑不了"，大师笑了。

**小胖**：（嚼着口香糖）"pandas 不是有 `read_csv` 吗？你把 MySQL 导成 CSV，再 `read_csv` 不就行了？我一直这么干！"

**大师**："小胖，你这相当于从北京到上海，先坐火车到天津，再从天津飞上海——绕路了。pandas 直接提供 `read_sql` 和 `to_sql`，通过 SQLAlchemy 直接连接数据库，一行代码从数据库读进 DataFrame，不需要中间 CSV。"

**小白**："SQLAlchemy 是什么？不是直接一个连接字符串就行？"

**大师**："SQLAlchemy 是 Python 生态的数据库抽象层。pandas 通过它和 MySQL、PostgreSQL、SQLite、SQL Server 等几乎所有关系型数据库建立连接。

```python
from sqlalchemy import create_engine

# 创建连接引擎
engine = create_engine('mysql+pymysql://user:pass@host:3306/dbname')

# 从数据库读
df = pd.read_sql('SELECT * FROM orders WHERE date = "2025-03-31"', engine)

# 写回数据库
df.to_sql('daily_report', engine, if_exists='replace', index=False)
```

不需要 FILE 权限、不需要中间 CSV、不需要 LOAD DATA——pandas + SQLAlchemy 一条龙。"

**【技术映射：SQLAlchemy engine = 万能充电线——不管什么数据库（MySQL/PostgreSQL/SQLite），一个接口全搞定】**

**小白**："那增量同步怎么做？比如每天只拉取昨天的新增数据？"

**大师**："核心思路：**用时间戳或自增 ID 做增量标记**。

```python
# 方案1：基于自增 ID 的增量（简单但依赖表设计）
last_id = load_last_synced_id()  # 从状态表读取上次同步的 max id
df = pd.read_sql(f'SELECT * FROM orders WHERE id > {last_id}', engine)
save_last_synced_id(df['id'].max())  # 保存本次的 max id

# 方案2：基于时间戳的增量（更通用，需要表有 update_time 列）
last_time = load_last_synced_time()
df = pd.read_sql(
    'SELECT * FROM orders WHERE update_time > %(last_time)s',
    engine, params={'last_time': last_time}
)
```

方案 2 更通用——即使表结构变了、ID 不是连续的，时间戳增量始终有效。"

**【技术映射：增量同步 = 书签阅读——每次从上次读到的地方继续，不重读也不漏读】**

**小胖**："那如果数据量很大呢？一次 `read_sql` 把 500 万行全部拉到内存，岂不是要炸？"

**大师**："用 `chunksize` 分块读取：

```python
# 一次只读 10000 行，迭代处理
for chunk in pd.read_sql('SELECT * FROM large_table', engine, chunksize=10000):
    # 对 chunk 做处理
    process(chunk)
```

这和 CSV 的 chunksize 用法完全一致——每个 chunk 是一个独立的 DataFrame。"

**小白**："写回数据库的时候，to_sql 的 if_exists 参数有哪些选择？"

**大师**："三个选项：
- `'fail'`：如果表已存在，报错（默认，安全第一）
- `'replace'`：删掉旧表，建新表（全量替换）
- `'append'`：在旧表后面追加数据（增量写入）

增量场景用 `'append'`，全量快照用 `'replace'`。另外 `method='multi'` 可以用多行 INSERT 语法加速写入——比默认的单行 INSERT 快 3-5 倍。"

**大师总结**："pandas + SQL 集成的核心操作：
1. `create_engine` → 建立连接
2. `read_sql` → 从 DB 读进 DataFrame（支持 chunksize）
3. `to_sql` → 从 DataFrame 写回 DB（支持 if_exists/method）
4. 增量同步 → 时间戳/ID 书签机制

记住：**pandas 是计算引擎，数据库是存储引擎——各司其职，不要混用。**"

## 3. 项目实战

### 3.1 环境准备

```bash
pip install pandas sqlalchemy pymysql
```

### 3.2 用 SQLite 模拟（无需安装 MySQL）

```python
# generate_db_data.py
import pandas as pd
import numpy as np
from sqlalchemy import create_engine
from datetime import datetime, timedelta

np.random.seed(42)
engine = create_engine('sqlite:///ecommerce.db')

# 订单表
N = 10000
dates = [datetime(2025,3,25) + timedelta(hours=np.random.randint(0,168)) for _ in range(N)]
orders = pd.DataFrame({
    'id': range(1, N+1),
    'order_no': [f'ORD{800000+i:06d}' for i in range(N)],
    'user_id': np.random.randint(1000, 5000, N),
    'amount': np.round(np.random.uniform(50, 3000, N), 2),
    'status': np.random.choice(['completed','cancelled','pending'], N, p=[0.7,0.15,0.15]),
    'created_at': sorted(dates),
})
orders.to_sql('orders', engine, if_exists='replace', index=False)

# 订单明细表
items = []
for _, o in orders.iterrows():
    for _ in range(np.random.randint(1, 5)):
        items.append({
            'order_id': o['id'],
            'product_name': np.random.choice(['T恤','耳机','坚果','毛巾','口红']),
            'qty': np.random.randint(1, 3),
            'price': round(np.random.uniform(10, 500), 2),
        })
items_df = pd.DataFrame(items)
items_df.to_sql('order_items', engine, if_exists='replace', index=False)

# 同步状态表
pd.DataFrame({'table_name':['orders'], 'last_synced_id':[0],
              'last_synced_at':['2025-03-25 00:00:00']}).to_sql(
    'sync_state', engine, if_exists='replace', index=False)

print(f"已创建 SQLite 数据库: orders={len(orders)}, items={len(items_df)}")
```

### 3.3 分步实现

#### 步骤 1：read_sql 基本读写

**目标**：从数据库直接读取、关联、写回。

```python
# step1_basic_rw.py
import pandas as pd
from sqlalchemy import create_engine

engine = create_engine('sqlite:///ecommerce.db')

# 读取
orders = pd.read_sql('SELECT id, order_no, user_id, amount, status, created_at FROM orders', engine, parse_dates=['created_at'])
print(f"读取订单: {len(orders)} 条")

# 关联订单明细
items = pd.read_sql('SELECT order_id, product_name, qty, price FROM order_items', engine)
merged = orders.merge(items, left_on='id', right_on='order_id', how='left')
merged['line_amount'] = merged['qty'] * merged['price']

# 计算日报指标
daily_kpi = merged.groupby(merged['created_at'].dt.date).agg(
    订单数=('order_no', 'nunique'),
    销售额=('line_amount', 'sum'),
    客单价=('line_amount', 'mean'),
    订单数_total=('id', 'count'),
).round(2).reset_index()
daily_kpi.columns = ['report_date','order_count','total_sales','avg_order','total_items']

# 写回数据库
daily_kpi.to_sql('daily_report', engine, if_exists='replace', index=False)
print(f"日报已写回: {len(daily_kpi)} 天")
```

#### 步骤 2：增量同步（基于自增 ID）

**目标**：只拉取上次同步后新增的数据。

```python
# step2_incremental.py
import pandas as pd
from sqlalchemy import create_engine, text

engine = create_engine('sqlite:///ecommerce.db')

def get_last_synced_id(engine):
    """从状态表读取上次同步的 ID"""
    try:
        result = pd.read_sql("SELECT last_synced_id FROM sync_state WHERE table_name='orders'", engine)
        return result['last_synced_id'].iloc[0] if len(result) > 0 else 0
    except Exception:
        return 0

def save_last_synced_id(engine, last_id):
    """更新状态表的同步位置"""
    with engine.connect() as conn:
        conn.execute(text(f"UPDATE sync_state SET last_synced_id = {last_id} WHERE table_name='orders'"))
        conn.commit()

# === 增量拉取 ===
last_id = get_last_synced_id(engine)
print(f"上次同步到 id={last_id}")

# 只拉取 id > last_id 的数据
new_orders = pd.read_sql(
    f'SELECT * FROM orders WHERE id > {last_id} ORDER BY id',
    engine, parse_dates=['created_at']
)
print(f"增量拉取: {len(new_orders)} 条新订单")

if len(new_orders) > 0:
    # 处理...
    max_id = new_orders['id'].max()
    save_last_synced_id(engine, max_id)
    print(f"已更新同步位置: id={max_id}")
else:
    print("无新数据")
```

#### 步骤 3：分块读取大数据

**目标**：用 chunksize 分块处理，避免 OOM。

```python
# step3_chunksize.py
import pandas as pd
from sqlalchemy import create_engine

engine = create_engine('sqlite:///ecommerce.db')

# 一次读 2000 行，迭代处理
total_sales = 0
chunk_count = 0
for chunk in pd.read_sql(
    'SELECT id, amount, status FROM orders',
    engine, chunksize=2000
):
    # 只统计 completed 订单
    completed = chunk[chunk['status'] == 'completed']
    total_sales += completed['amount'].sum()
    chunk_count += 1
    print(f"  Chunk {chunk_count}: {len(chunk)} 行, 累计销售 {total_sales:.0f}")

print(f"\n总销售额(已完成): {total_sales:.2f}, 共处理 {chunk_count} 个块")
```

#### 步骤 4：to_sql 精细化控制

**目标**：控制写入行为——追加/替换、批量插入、dtype 映射。

```python
# step4_to_sql.py
import pandas as pd
import numpy as np
from sqlalchemy import create_engine

engine = create_engine('sqlite:///ecommerce.db')

# 模拟新日报数据
new_report = pd.DataFrame({
    'report_date': ['2025-04-01', '2025-04-02'],
    'order_count': [1250, 1320],
    'total_sales': [285000.50, 310200.75],
    'avg_order': [228.00, 235.00],
})

# 追加到已有表（不覆盖）
new_report.to_sql('daily_report', engine, if_exists='append', index=False)

# 验证有多少天了
all_dates = pd.read_sql('SELECT COUNT(*) as cnt FROM daily_report', engine)
print(f"daily_report 表记录数: {all_dates['cnt'].iloc[0]}")

# 使用 method='multi' 加速写入（多行 INSERT）
# new_report.to_sql('daily_report', engine, if_exists='append', index=False, method='multi')
```

### 3.4 常见坑及解决方法

| 问题 | 原因 | 解决方法 |
|------|------|----------|
| `read_sql` 报 NoSuchModuleError | 缺少数据库驱动 | `pip install pymysql`(MySQL) 或 `psycopg2`(PostgreSQL) |
| `to_sql` 创建表后 dtype 不对 | pandas 推断的 SQL 类型不符预期 | 用 `dtype=` 参数显式指定（如 `{'col': sqlalchemy.types.Integer}`） |
| 时间戳增量同步漏数据 | 同一秒有多条记录 | 用 `>=` 而非 `>`，结合 ID 辅助去重 |
| `to_sql` 写入慢 | 默认逐行 INSERT | 加 `method='multi'` 或用 `chunksize=1000` |
| 连接未关闭 | 没有显式 dispose | `engine.dispose()` 或使用 context manager |

### 3.5 测试验证

```python
# test_ch21.py
import pandas as pd
from sqlalchemy import create_engine

def test_read_sql():
    engine = create_engine('sqlite:///ecommerce.db')
    df = pd.read_sql('SELECT COUNT(*) as cnt FROM orders', engine)
    assert df['cnt'].iloc[0] > 0

def test_to_sql_roundtrip():
    engine = create_engine('sqlite:///ecommerce.db')
    test_df = pd.DataFrame({'x': [1,2], 'y': ['a','b']})
    test_df.to_sql('_test_roundtrip', engine, if_exists='replace', index=False)
    back = pd.read_sql('SELECT * FROM _test_roundtrip', engine)
    assert len(back) == 2
    pd.read_sql("DROP TABLE _test_roundtrip", engine)

def test_incremental():
    engine = create_engine('sqlite:///ecommerce.db')
    all_ids = pd.read_sql('SELECT id FROM orders ORDER BY id', engine)
    # 模拟增量：只取后半部分
    mid = len(all_ids) // 2
    half = pd.read_sql(f'SELECT * FROM orders WHERE id > {all_ids.iloc[mid-1]["id"]}', engine)
    assert len(half) <= len(all_ids) // 2

if __name__ == '__main__':
    test_read_sql(); test_to_sql_roundtrip(); test_incremental()
    print("OK 所有测试通过")
```

**完整代码清单**：参见专栏配套仓库 `column/code/ch21/`。

## 4. 项目总结

### 4.1 优点 & 缺点

| 维度 | pandas + SQLAlchemy | CSV 中转 | 纯 SQL |
|------|-------------------|---------|--------|
| 数据链路 | DB→DataFrame→DB 无中间文件 | DB→CSV→DF→CSV→DB | 纯 SQL 存储过程 |
| 类型保留 | read_sql 自动推断 dtype | CSV 丢失类型 | 数据库原生类型 |
| 增量同步 | 时间戳/ID 书签灵活 | 手工处理 | 触发器/CDC |
| 灵活性 | pandas 任意清洗逻辑 | pandas 清洗 | 受限于 SQL |
| 性能 | 中等（全量拉到内存） | 多一次 IO | 数据库引擎优化 |
| 学习成本 | 需了解 SQLAlchemy | 低 | 高 |

### 4.2 适用场景

- **适用场景**：
  1. 每日凌晨从 MySQL 增量拉取数据做日报计算
  2. 多表关联后用 pandas 做复杂聚合再写回数据库
  3. 数据库间的数据迁移和转换（MySQL→PostgreSQL）
  4. 替代手工 `SELECT INTO OUTFILE` + `LOAD DATA` 流程
- **不适用场景**：
  1. TB 级全量数据拉取——内存不够，需分块或改用 Spark
  2. 实时流计算——pandas 是批处理工具

### 4.3 注意事项

- **连接字符串中的密码**：不要在代码中硬编码密码，用环境变量 `os.environ['DB_PASS']`；
- **SQL 注入风险**：拼接 SQL 时用 `params` 参数传值，不要用 f-string 拼接用户输入；
- **to_sql 的 if_exists='replace'**：会 DROP TABLE 再 CREATE，如果表有索引/触发器会丢失；
- **时区问题**：MySQL 的 DATETIME 不含时区，TIMESTAMP 含时区。读取后用 `pd.to_datetime(utc=True)` 统一。

### 4.4 常见踩坑经验

1. **read_sql 返回的行数和预期不同**：一位同事用 `SELECT * FROM orders WHERE created_at > '2025-03-31'` 却没拉到 3 月 31 日当天的数据——因为 `created_at` 是 DATETIME，`'2025-03-31'` 被 MySQL 解析为 `'2025-03-31 00:00:00'`，不包含 3 月 31 日白天的数据。教训：用 `>= '2025-03-31' AND < '2025-04-01'` 或 `DATE(created_at) = '2025-03-31'`。
2. **to_sql 创建的表所有字段都是 TEXT**：SQLite 下 pandas 的 `to_sql` 有时会把字符串列建为 TEXT（无长度限制）——如果期望 VARCHAR(50)，需要用 SQLAlchemy 的 dtype 参数显式指定。
3. **增量同步的 state 表被并发写入**：如果两个实例同时跑增量，状态更新可能冲突。用 `SELECT ... FOR UPDATE` 或分布式锁保证互斥。

### 4.5 思考题

1. 如果增量同步的时间戳字段是 `updated_at`，但某些行的这个字段是 NULL，`read_sql` 会怎么表现？如何保证不漏掉这些数据？
2. `to_sql(method='multi')` 在 SQLite 和 PostgreSQL 上的行为有何不同？为什么某些数据库下 `method='multi'` 并不生效？

（答案将在第 22 章附录中给出）

### 4.6 推广计划提示

- **数据工程师**：将 read_sql→pandas处理→to_sql 作为标准 ETL 步骤，用 Airflow 调度；
- **DBA/运维**：关注 to_sql 的锁表行为和连接池配置；
- **开发**：将连接参数外置到配置文件，避免硬编码。

---

> **源码关联**：pandas/io/sql.py
