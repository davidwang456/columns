# 第13章：非结构化数据同步——TXT/CSV/ORC/Parquet

## 1. 项目背景

运维小周每周五下午需要手工将业务部门提交的 CSV 文件导入 MySQL 作为当周报表数据。流程繁琐：先用 Python 脚本校验 CSV 格式（分隔符、编码、空值），再上传到服务器，用 `LOAD DATA LOCAL INFILE` 命令导入 MySQL。最近业务部门改了 CSV 的列顺序——第 3 列从 `user_name` 变成了 `user_email`，但小周的导入脚本没有感知变化，导致整周报表的"用户名"和"邮箱"两列数据全错。

技术 manager 建议用 DataX 的非结构化存储插件（txtfilereader + mysqlwriter），通过 JSON 配置显式声明列映射关系，避免隐式的列序号依赖。更进一步，可以用 DataX 统一管理"CSV → MySQL"、"MySQL → ORC"、"ORC → Parquet"等 10 多种文件格式转换，不再需要维护一堆 Python/Shell 脚本。

本章深入 plugin-unstructured-storage-util 模块，理解 DataX 如何抽象本地文本文件、CSV、ORC、Parquet 等多种非结构化格式的读写逻辑，让你对任意文件格式的同步都游刃有余。

## 2. 项目设计——剧本式交锋对话

**（周五下午 4:50，运维告警群里消息不断）**

**小胖**：（发语音）小周，你的 CSV 导入又失败了！报表组说用户名字段全是空的！

**小周**：（一脸疲惫）我知道，业务那边改了 CSV 列顺序，我还没来得及改导入脚本。

**小白**：（发了一条消息）用 txtfilereader + mysqlwriter 不就完了？你在 JSON 配置里用 `column.index` 指定每一列在 CSV 里的位置，不管业务那边怎么调顺序，只要 index 对应关系不变就不会乱。

**大师**：（发了一个配置示例）你们看看这个设计——DataX 的非结构化存储插件有统一的抽象层。不管是读本地 TXT 文件、HDFS 上的 CSV 文件、还是 OSS 上的 ORC 文件，底层都是同样的 `UnstructuredStorageReaderUtil` 工具类。它做的事情就是：根据 `fileType` 选择合适的 Reader，把文件内容逐行解析为 DataX Record。

**技术映射**：UnstructuredStorageReaderUtil = 万能文件解码器。给它一个 TXT/CSV/ORC/Parquet 文件，它都能解码成 DataX Record 流。

**小胖**：（好奇）那不同格式的文件，切分策略一样吗？比如一个 1GB 的 CSV 文件，怎么分成多个 Task 并行读？

**大师**：这就是设计精巧的地方。非结构化文件的切分不依赖内容（不像 MySQL 按主键值切分），而是按字节偏移量切分：

1. 先算文件总字节数（1GB）
2. 按 channel 数等距切分（channel=5 → 每个 Task 负责 200MB）
3. 每个 Task 从自己的偏移量起始位置开始读
4. 第一个 Task 之外的 Task 需要跳过自己起始偏移量处的半行数据（找到第一个完整行）

**小白**：（追问）那 ORC 和 Parquet 这些列式格式呢？它们是一整块 Stripe/RowGroup，不能按字节偏移量随机切吧？

**大师**：对。ORC 的最小读取单元是 Stripe（默认 64MB），Parquet 是 RowGroup（默认 128MB）。hdfsreader 在 split 时按 Stripe/RowGroup 的总数来切分——如果文件有 20 个 Stripe，channel=5，每个 Task 读 4 个 Stripe。这比按行切分更高效——因为它不需要"跳过半行"的逻辑。

**小周**：（眼睛发亮）那本地 txt 文件的编码问题怎么处理？我们经常遇到 Windows 上的 GBK 文件导入 Linux 时乱码。

**大师**：（发了一个参数配置）txtfilereader 有 `encoding` 参数：

```json
{
    "reader": {
        "name": "txtfilereader",
        "parameter": {
            "path": ["/data/input/orders.csv"],
            "encoding": "GBK",
            "column": [
                {"index": 0, "type": "long"},
                {"index": 1, "type": "string"},
                {"index": 2, "type": "double"}
            ],
            "fieldDelimiter": ",",
            "skipHeader": "true"
        }
    }
}
```

## 3. 项目实战

### 3.1 步骤一：CSV 文件 → MySQL（带编码转换和空值处理）

**目标**：将 Windows GBK 编码的 CSV 文件导入 MySQL（UTF-8 编码）。

**CSV 文件示例（orders.csv）**：

```csv
order_id,customer_name,amount,order_date,status
1001,"张三",299.99,2026-05-01,"已付款"
1002,"李四",,2026-05-02,     ← amount 为空
1003,"王五",599.00,invalid_date,"已取消" ← 日期格式错误
```

**DataX 配置**：

```json
{
    "job": {
        "content": [{
            "reader": {
                "name": "txtfilereader",
                "parameter": {
                    "path": ["/data/input/orders.csv"],
                    "encoding": "GBK",
                    "column": [
                        {"index": 0, "type": "long"},
                        {"index": 1, "type": "string"},
                        {"index": 2, "type": "double"},
                        {"index": 3, "type": "date"},
                        {"index": 4, "type": "string"}
                    ],
                    "fieldDelimiter": ",",
                    "skipHeader": "true",
                    "nullFormat": ""
                }
            },
            "writer": {
                "name": "mysqlwriter",
                "parameter": {
                    "writeMode": "insert",
                    "column": ["order_id", "customer_name", "amount", "order_date", "status"],
                    "nullFormat": "\\N",
                    "connection": [{
                        "table": ["orders"],
                        "jdbcUrl": ["jdbc:mysql://localhost:3306/test_db?useUnicode=true&characterEncoding=utf8"]
                    }]
                }
            }
        }],
        "setting": {
            "speed": {"channel": 2},
            "errorLimit": {"record": 10, "percentage": 0.01}
        }
    }
}
```

**执行结果分析**：

```
读出的记录总数: 3
读写失败总数: 1  ← 第3行 order_date="invalid_date" 转换失败被标记为脏数据
```

**空值处理链路**：
1. CSV 中 amount 为空 → txtfilereader 将其解析为 null（`nullFormat=""`）
2. Record 中该列为 null → DoubleColumn(null)
3. 传入 Writer → PreparedStatement.setNull(3, Types.DOUBLE)
4. MySQL 中该行 amount 为 NULL

### 3.2 步骤二：MySQL → 本地 CSV 文件

**目标**：将 MySQL 数据导出为本地 CSV 文件，供业务部门下载。

```json
{
    "reader": {
        "name": "mysqlreader",
        "parameter": {
            "username": "etl_user",
            "password": "123456",
            "column": ["*"],
            "splitPk": "id",
            "connection": [{
                "table": ["employees"],
                "jdbcUrl": ["jdbc:mysql://localhost:3306/hr_db"]
            }]
        }
    },
    "writer": {
        "name": "txtfilewriter",
        "parameter": {
            "path": "/data/export/",
            "fileName": "employees",
            "writeMode": "truncate",
            "fileFormat": "csv",
            "fieldDelimiter": ",",
            "encoding": "UTF-8",
            "header": ["id", "name", "department", "salary", "hire_date"]
        }
    }
}
```

**txtfilewriter 的 writeMode**：

| writeMode | 行为 | 适用场景 |
|-----------|------|---------|
| `truncate` | 写入前清空目录 | 全量导出 |
| `append` | 追加写入 | 增量导出 |
| `nonConflict` | 目录非空则报错 | 防止误覆盖 |

**生成的 CSV 文件**：

```csv
id,name,department,salary,hire_date
1,张三,技术部,15000.00,2020-03-15
2,李四,市场部,12000.00,2021-07-01
3,王五,财务部,13000.00,2019-01-10
```

### 3.3 步骤三：CSV → ORC 文件格式转换

**目标**：将原始 CSV 转为压缩的 ORC 格式，节省 HDFS 存储空间。

```json
{
    "reader": {
        "name": "txtfilereader",
        "parameter": {
            "path": ["/data/input/logs_20260506.csv"],
            "fieldDelimiter": "\t",
            "column": [
                {"index": 0, "type": "string"},
                {"index": 1, "type": "date"},
                {"index": 2, "type": "long"},
                {"index": 3, "type": "string"}
            ]
        }
    },
    "writer": {
        "name": "hdfswriter",
        "parameter": {
            "defaultFS": "hdfs://ns-cluster",
            "fileType": "orc",
            "path": "/data/warehouse/logs/dt=2026-05-06",
            "fileName": "logs",
            "writeMode": "truncate",
            "compress": "SNAPPY",
            "column": [
                {"name": "event_type", "type": "STRING"},
                {"name": "event_time", "type": "DATE"},
                {"name": "user_id", "type": "BIGINT"},
                {"name": "event_data", "type": "STRING"}
            ],
            "hadoopConfig": {...}
        }
    }
}
```

**效果对比**（1000 万行日志，每行约 200 字节）：

| 格式 | 文件大小 | 节省空间 | 读取速度 |
|------|---------|---------|---------|
| CSV (原始) | 2.0 GB | 基准 | 80 MB/s |
| CSV + gzip | 400 MB | 80% | 15 MB/s |
| ORC + SNAPPY | 350 MB | 82.5% | 50 MB/s |
| Parquet + SNAPPY | 380 MB | 81% | 45 MB/s |

### 3.4 步骤四：通配符匹配多文件

**目标**：一次性读取 `/data/input/` 下所有 `*.csv` 文件。

```json
{
    "reader": {
        "name": "txtfilereader",
        "parameter": {
            "path": ["/data/input/*.csv", "/data/input/subdir/**/*.tsv"],
            "encoding": "UTF-8",
            "column": [...]
        }
    }
}
```

**通配符规则**：

| 模式 | 匹配 |
|------|------|
| `*.csv` | 当前目录所有 .csv 文件 |
| `**/2026-05-*` | 所有日期前缀的目录中的文件 |
| `/data/input/orders_{01..10}.csv` | orders_01.csv 到 orders_10.csv |

通配符展开后，每个文件独立分一个 Task（除非文件太大按字节切分）。如果有 100 个小文件且 channel=10，前 10 个文件并发读，读完后依次读剩余 90 个。

### 3.5 步骤五：二进制文件（图片、PDF）的读写

**目标**：从数据库读取 blob 字段，写入本地文件。

```json
{
    "reader": {
        "name": "mysqlreader",
        "parameter": {
            "column": ["id", "file_name", "file_data"],
            "connection": [{
                "querySql": ["SELECT id, file_name, file_data FROM attachments WHERE $CONDITIONS"],
                "jdbcUrl": ["jdbc:mysql://..."]
            }]
        }
    },
    "writer": {
        "name": "txtfilewriter",
        "parameter": {
            "path": "/data/export/attachments/",
            "fileName": "attachment",
            "writeMode": "truncate",
            "fileFormat": "binary",
            "suffix": "{file_name}"
        }
    }
}
```

`fileFormat: "binary"` 模式下，每个 Record 的第一列作为文件名，第二列（BytesColumn）作为文件内容。`suffix` 参数支持动态后缀（从 Record 的指定列取值）。

### 3.6 可能遇到的坑及解决方法

**坑1：CSV 字段内含分隔符**

如果 CSV 的字段值本身包含逗号，必须用引号包裹：

```csv
id,name,description
1,"张三","身高180cm, 体重75kg"  ← description 含逗号
2,"李四","产品经理，5年经验"
```

txtfilereader 默认支持双引号转义，如果有特殊引号字符，用 `quoteChar` 参数指定。

**坑2：本地文件路径在 Windows 和 Linux 不兼容**

解决：JSON 中的路径使用正斜杠 `/`，DataX 运行时会自动适配当前 OS。
```
Windows: "path": ["D:/data/input/orders.csv"]
Linux:   "path": ["/data/input/orders.csv"]
```

**坑3：大文件切分的半行问题**

channel=5 读取 1GB 的文本文件时，第 2 个 Task 的起始偏移量可能落在某行中间。

解决：txtfilereader 内部会从起始偏移量往后搜索第一个换行符 `\n`，跳过该半行。但这意味着每个 Task 都会额外丢失（或重复）若干字节，对数据完整性无影响（一行要么完整被读，要么完整被跳过）。

**坑4：压缩文件直接读取**

txtfilereader 支持 `.gz`、`.bz2` 压缩格式的自动解压：

```json
"path": ["/data/input/orders.csv.gz"]
```

DataX 会根据文件后缀自动选择解压缩算法。但**压缩文件不支持并发读取**——它必须被当做一个整体，channel 会强制设为 1。

## 4. 项目总结

### 4.1 非结构化存储插件能力矩阵

| 能力 | txtfilereader | txtfilewriter | hdfsreader | hdfswriter |
|------|--------------|---------------|------------|------------|
| 本地文件读取 | ✓ | ✓ | ✗ | ✗ |
| HDFS 文件 | ✗ | ✗ | ✓ | ✓ |
| TXT/CSV 格式 | ✓ | ✓ | ✓ | ✓ |
| ORC 格式 | ✗ | ✗ | ✓ | ✓ |
| Parquet 格式 | ✗ | ✗ | ✓ | ✓ |
| 通配符匹配 | ✓ | ✗ | ✓ | ✗ |
| 压缩文件读取 | 自动 | 手动 | 自动 | 压缩参数 |
| 编码转换 | ✓ | ✓ | 自动 | 自动 |
| 二进制文件 | ✗ | ✓ | ✗ | ✗ |

### 4.2 优点

1. **格式统一抽象**：TXT/CSV/ORC/Parquet 共用一套读取流程，新增格式只需实现对应的 Reader
2. **编码自动处理**：GBK 转 UTF-8 一行配置解决
3. **通配符批处理**：一次配置匹配 N 个文件，适合日志采集场景
4. **压缩透明**：`.gz`/`.bz2` 文件自动解压，无需预处理
5. **分片智能**：文本文件按字节偏移量切分，ORC/Parquet 按 Stripe/RowGroup 切分

### 4.3 缺点

1. **不支持 Excel 格式**：XLS/XLSX 文件需先用其他工具转为 CSV
2. **ORC/Parquet 写性能依赖 Hadoop**：本地无法写 ORC（需要 Hadoop Native Library）
3. **压缩文件不支持并发**：gzip 归档只能单线程读取
4. **CSV 嵌套引号处理不完善**：多级引号嵌套可能解析失败
5. **文件监控无原生支持**：新文件到达不会自动触发同步

### 4.4 适用场景

1. 业务部门手工提交 CSV → 自动导入 MySQL 报表库
2. MySQL → 本地 CSV 导出（供数据分析师下载）
3. 日志文件采集：`/var/log/*.log` → HDFS ORC 存储
4. 数据格式标准化：CSV → ORC/Parquet（节省 80% 存储）
5. 跨平台数据交换：Windows GBK CSV → Linux UTF-8 MySQL

### 4.5 注意事项

1. CSV 字段值包含分隔符时必须用引号包裹
2. 压缩文件读取时 channel 强制定为 1（限制并发）
3. ORC/Parquet Writer 需要 Hadoop 环境（本地编译略复杂）
4. 通配符匹配大目录时，先验证匹配结果避免意外
5. 二进制文件写入必须配合 BytesColumn 类型，其他列类型无法导出为二进制

### 4.6 思考题

1. txtfilereader 的"字节偏移切分"方案中，如果文本文件没有换行符（单行 2GB），切分行为会如何？如何解决？
2. 如果想实现"实时监控本地目录，新文件到达自动触发 DataX 同步"，你会如何设计这个监控+调度方案？DataX 本身缺少什么能力？

（答案见附录）
