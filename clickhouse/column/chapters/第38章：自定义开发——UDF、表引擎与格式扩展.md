# 第38章：自定义开发——UDF、表引擎与格式扩展

> **版本**：ClickHouse 25.x LTS
> **定位**：高级篇核心章节——从使用者到扩展者，掌握UDF、表引擎与格式三大自定义开发能力。
> **前置阅读**：第3章（数据类型与建表）、第4章（MergeTree家族）、第17章（分布式架构）
> **预计阅读**：45分钟 | **实战耗时**：120分钟

---

## 1. 项目背景

某金融科技公司数据平台团队接到三个紧急需求，都不是 ClickHouse 内置功能能够覆盖的。

**需求一：PII列国密加密**。监管要求数据库中存储的个人身份信息（手机号、身份证号）必须使用国密 SM4 算法加密，且**加密过程必须在数据库内部完成**——不让敏感明文离开数据库引擎。现有方案是在 Python 应用层先加密再写入，但这种方式失去了 SQL 层面的表达能力：无法对加密列做索引、无法在查询中解密后聚合、每次查询都需要把密文拉回应用层解密再处理。一条简单的"统计上海地区用户数"，本来一句 `SELECT count() FROM users WHERE province='上海'` 就能搞定，现在要把所有密文拉出来、逐行解密、在内存中过滤——延迟从毫秒级变成了秒级。

**需求二：实时行情接入**。公司自研的交易信号系统通过内部 WebSocket 协议对外推送实时股票行情（每秒约5000条）。业务方希望这些数据能直接进 ClickHouse 做分析——而不是先落 MySQL 再通过字典或 ETL 导入。但 ClickHouse 原生并没有 WebSocket 表引擎，Kafka 引擎又不支持 WebSocket 协议。

**需求三：监管报送固定宽度格式**。央行反洗钱系统要求报送数据必须使用固定宽度文本格式（Fixed-Width），每个字段占固定字符数，不足补空格，超出截断。ClickHouse 内置 60+ 种输入输出格式（CSV、JSONEachRow、Parquet、TSV等），偏偏没有 Fixed-Width。运维同事每次都要用 Python + Pandas 把查询结果导出再转换，一条 `SELECT * FROM anti_money_laundering WHERE report_date = today()` 需要 4 步操作，且中间 CSV 文件常常超过 2GB。

**核心痛点**：当 ClickHouse 的内置功能无法满足业务需求时，很多团队习惯性地"绕出去"——数据拉出来在 Python/Java 层处理，再写回 ClickHouse。这样做打破了 SQL 的表达连贯性，增加了数据传输成本，也引入了额外的故障点。ClickHouse 提供了三种扩展机制——UDF（用户自定义函数）、自定义表引擎、自定义格式——让扩展逻辑留在数据库内部，共享 ClickHouse 的列存优化、向量化执行和分布式计算能力。本章将逐一拆解这三种扩展方式的设计要点、开发流程与生产落地注意事项。

---

## 2. 项目设计：剧本式交锋对话

晚饭后，大师在钉钉群里发了三条需求，叫小胖和小白来会议室讨论技术方案。

**小胖**（端着奶茶走进来，往白板前一坐）："三个需求我都看了。第一个不就是个加密函数吗？Python写个脚本，读出来加密完写回去，多简单！第二个拉行情数据嘛，写个Node.js脚本连上WebSocket，收到数据就批量INSERT，10分钟搞定。第三个更好办，Python Pandas导出CSV再写个Fixed-Width转换——三个脚本加起来半天写完了，老板还夸效率高，干嘛非要改ClickHouse？"

**大师**（把白板笔递给他）："按你的方案，加密函数上线后，来一个需求：'帮我统计最近7天，上海地区用户的交易总额，按手机号归属地分组'——这条SQL怎么写？"

**小胖**（愣住了，放下奶茶）："呃……得先 `SELECT phone FROM users` 拉出来，用 Python 解密，再按归属地分组……不对，phone 是加密的没法按归属地分组……所以得先把所有用户的归属地也写回一张新表，然后再 Join……"

**小白**（扶了扶眼镜）："这就是问题的关键。SQL 的威力在于你可以用声明式语言描述'要什么'，而不需要关心'怎么拿'。一旦你在应用层处理数据，你就把一条优雅的 SQL 拆成了 `读 → Python处理 → 写回去 → 再查询` 四步操作，每一步都引入网络 IO 和序列化开销。而且中间结果越来越大，查询越来越不可维护。"

**大师**： "小白说得对。ClickHouse 给了我们三条路，让扩展逻辑留在数据库引擎内部。"

他在白板上写下三行字：

```
UDF（User Defined Function）       → 自定义函数，扩展 SQL 语法
Custom Table Engine（自定义表引擎）  → 自定义数据源，扩展 FROM 子句
Custom Format（自定义格式）          → 自定义序列化，扩展 FORMAT 子句
```

"先讲 **UDF**。ClickHouse 支持两种 UDF：一是 **Lambda UDF**，纯 SQL 表达式，定义在 SQL 里，没有外部依赖，速度最快，适合简单计算；二是 **Executable UDF**，调用外部可执行程序——Python、Shell、C++编译的二进制都行，数据通过 stdin/stdout 以 TabSeparated 格式传递。适合需要外部库支持的场景，比如你那个国密 SM4——Python 有现成的 `gmssl` 库。"

**技术映射 #1**：Lambda UDF 相当于 Excel 里的公式函数——表达式在 ClickHouse 向量化引擎中执行，无 IPC 开销。Executable UDF 相当于`子进程管道`——ClickHouse 启动一个外部进程，输入数据写 stdin，从 stdout 读结果。每次函数调用可能启动一个新进程（取决于池化策略），IPC 开销约为每行 0.1-1ms。

---

**小白**：（眼睛亮起来）"Executable UDF 每次调用都启动一个进程？那如果 `SELECT sm4_encrypt(phone) FROM billion_row_table`，岂不是要启动十亿个 Python 进程？"

**大师**："问到要害了。Executable UDF 的执行模型有两种：**One-per-block** 和 **One-per-call**。默认是按 Block 粒度启动进程——一个 Block 通常包含 65536 行，ClickHouse 把整个 Block 的输入通过 stdin 一次性发给 Python 进程，Python 进程循环处理每一行再写回 stdout。所以十亿行大约是 15000 个进程启动——虽然比十亿好多了，但 IPC 开销依然显著。后面实战部分我们会看到一个优化技巧：用**连接池**模式写 Python 进程，让它常驻内存，通过某种协议复用。"

**小胖**："那自定义表引擎呢？是不是像写一个插件，把 Redis 的数据映射成 ClickHouse 的表？"

**大师**（在白板上画出接口图）："对。自定义表引擎的核心是实现 `IStorage` 接口。最小实现只需要覆盖三个方法：

- `read()`：返回数据读取管道（Pipe），ClickHouse 通过它迭代拉取数据
- `getName()`：返回引擎名称，比如 `'Redis'`、`'WebSocket'`
- `getStoragePolicy()`：返回存储策略——如果你的数据完全在外部，返回空指针

当然，如果你还需要支持 `INSERT`、`ALTER DELETE`、`OPTIMIZE` 等 DML 操作，就需要实现更多接口方法。但只读场景下这三板斧就够了。"

**技术映射 #2**：`IStorage` 是 ClickHouse 表引擎的抽象基类，类似 Java 中的 `DataSource` 接口。`read()` 方法返回一个 `Pipe` 对象——Pipe 本质上是一个生产者-消费者管道，内置并发控制和反压机制。你的数据源只需要实现一个 `ISource` 子类，重写 `generate()` 方法逐行产出数据。

---

**小白**："那自定义格式呢？Fixed-Width 这种需求改个输出格式，需要写 C++ 重新编译整个 ClickHouse？"

**大师**："格式扩展确实需要编译进 ClickHouse 二进制文件，但改动范围非常小——你只需要：

1. 继承 `IOutputFormat`（输出）或 `IInputFormat`（输入）
2. 重写 `write()` 或 `read()` 方法，实现你自己的序列化逻辑
3. 在 `FormatFactory` 中注册你的格式类

总共大约 50-80 行 C++ 代码。而且如果你用 `-DUSE_STATIC_LIBRARIES=0` 选项编译，可以把你自定义的格式编译成**独立动态库（.so 文件）**，这样 ClickHouse 主程序不需要重新编译全部代码——只链接你的动态库就行。这在生产环境中非常重要：你不希望为了加一个格式而重新编译整个 300 万行源码的 ClickHouse。"

**小胖**（挠头）："安全性呢？UDF 跑 Python 脚本，万一有人写了个 `os.system('rm -rf /')` 怎么办？"

**大师**神情严肃起来："这一点必须重点强调。ClickHouse 的 Executable UDF **没有真正的沙箱机制**——它只是以 `clickhouse` 用户身份启动子进程，该用户能看到什么文件，UDF 就能访问什么文件。所以生产环境必须加上多重保护：

- **网络隔离**：UDF 进程所在的机器防火墙规则禁止外网访问，防止数据外泄
- **资源限制**：使用 cgroup 限制 UDF 进程的 CPU 和内存（`command_timeout_ms`、`max_command_execution_time`）
- **用户权限**：ClickHouse 进程以专用非 root 用户运行，UDF 继承该用户权限
- **代码审查**：所有 UDF 脚本必须通过安全审计后才能部署
- **容器封装**：最安全的做法是把 UDF 跑在 Docker 容器里，`--network=none --read-only`"

**技术映射 #3**：Executable UDF 的安全模型是"信任但验证"——它假设部署者控制了脚本内容，安全边界不在 ClickHouse 内部，而在运维层的资源隔离与网络策略。这与 AWS Lambda 的 Firecracker 微虚拟机沙箱有本质差距。

---

**小白**："如果我们做的扩展足够通用，可以贡献给开源社区吧？流程是什么样的？"

**大师**："当然可以。ClickHouse 社区对贡献很友好，但要求严格：

首先，**代码风格**：必须通过 `clang-format` 格式化，遵循 [ClickHouse C++ Style Guide](https://clickhouse.com/docs/en/development/style)。变量用蛇形命名（`snake_case`）、类用驼峰（`CamelCase`）、成员变量下划线后缀（`member_`）。

其次，**测试**：每个新功能必须有对应的功能测试（`tests/queries/0_stateless/*.sql`），如果你改的是存储或网络相关还得跑集成测试。PR 提交后 CI 会自动跑 `stateless tests`、`integration tests`、`performance tests` 和 `fuzzer`——四个全绿才给 Review。

最后，**PR 流程**：先开 Issue 或 RFC 讨论设计方案，避免写了代码才发现方向不对。核心贡献者通常是俄罗斯团队（ClickHouse Inc.），北京时间的晚上到凌晨是 Review 高峰期。PR title 要有清晰的前缀，比如 `[Feature] Add FixedWidth format`。"

---

## 3. 项目实战

### 环境准备

你需要一套可运行的 ClickHouse 服务（版本 ≥ 23.x），以及 C++ 编译环境（GCC ≥ 12 或 Clang ≥ 15）。如果想做自定义表引擎或格式扩展，还需 Clone ClickHouse 源码。

```bash
# 1. 确认 ClickHouse 版本 ≥ 23.x
clickhouse-client --query "SELECT version()"

# 2. 确认 UDF 功能已开启
# 检查 /etc/clickhouse-server/config.xml 中是否有:
# <user_defined_executable_functions_config>*_udf.xml</user_defined_executable_functions_config>

# 3. 安装 UDF 依赖（国密加密示例）
pip3 install gmssl

# 4. 如果想编译自定义模块，预先安装编译工具链
sudo apt-get install -y cmake ninja-build clang-15 libc++-dev
```

---

### 分步实现

#### Step 1：可执行脚本 UDF — Python 国密 SM4 加密

**目标**：注册一个 `sm4_encrypt` 函数，通过 Python 脚本调用 `gmssl` 库实现国密 SM4 加密。

首先配置 ClickHouse 开启 UDF 功能：

```xml
<!-- /etc/clickhouse-server/config.d/udf.xml -->
<clickhouse>
    <user_defined_executable_functions_config>
        *executable_udf.xml
    </user_defined_executable_functions_config>
</clickhouse>
```

注册 `sm4_encrypt` 函数：

```xml
<!-- /etc/clickhouse-server/executable_udf.xml -->
<functions>
    <function>
        <type>executable</type>
        <name>sm4_encrypt</name>
        <return_type>String</return_type>
        <argument>
            <type>String</type>
        </argument>
        <format>TabSeparated</format>
        <command>python3 /var/lib/clickhouse/user_scripts/sm4_encrypt.py</command>
        <execute_direct>0</execute_direct>
    </function>
    <function>
        <type>executable</type>
        <name>sm4_decrypt</name>
        <return_type>String</return_type>
        <argument>
            <type>String</type>
        </argument>
        <format>TabSeparated</format>
        <command>python3 /var/lib/clickhouse/user_scripts/sm4_decrypt.py</command>
    </function>
</functions>
```

编写 Python 加密脚本：

```python
#!/usr/bin/env python3
# /var/lib/clickhouse/user_scripts/sm4_encrypt.py
# UDF 约定: stdin 接收 TabSeparated 输入, stdout 输出结果, stderr 日志
# ClickHouse 按 Block 发数据, 每个 Block 约 65536 行

import sys
from gmssl.sm4 import CryptSM4, SM4_ENCRYPT

KEY = b'0123456789abcdef'  # 16 字节密钥, 生产环境应从密钥管理系统获取

def encrypt(plaintext: str) -> str:
    crypt_sm4 = CryptSM4()
    crypt_sm4.set_key(KEY, SM4_ENCRYPT)
    ciphertext = crypt_sm4.crypt_ecb(plaintext.encode())
    return ciphertext.hex()

if __name__ == '__main__':
    for line in sys.stdin:
        plaintext = line.rstrip('\n')
        if not plaintext or plaintext == r'\N':
            print(r'\N', flush=True)
        else:
            result = encrypt(plaintext)
            print(result, flush=True)
```

编写解密脚本：

```python
#!/usr/bin/env python3
# /var/lib/clickhouse/user_scripts/sm4_decrypt.py

import sys
from gmssl.sm4 import CryptSM4, SM4_DECRYPT

KEY = b'0123456789abcdef'

def decrypt(hex_cipher: str) -> str:
    crypt_sm4 = CryptSM4()
    crypt_sm4.set_key(KEY, SM4_DECRYPT)
    plaintext = crypt_sm4.crypt_ecb(bytes.fromhex(hex_cipher))
    return plaintext.decode('utf-8')

if __name__ == '__main__':
    for line in sys.stdin:
        hex_cipher = line.rstrip('\n')
        if not hex_cipher or hex_cipher == r'\N':
            print(r'\N', flush=True)
        else:
            try:
                result = decrypt(hex_cipher)
                print(result, flush=True)
            except Exception:
                print(r'\N', flush=True)
```

```bash
# 重启 ClickHouse 使配置生效
sudo systemctl restart clickhouse-server
```

验证 UDF：

```sql
-- 测试加密
SELECT sm4_encrypt('13812345678') AS encrypted_phone;
-- 输出: a3f2b8c9d1e4f5a6...

-- 测试解密
SELECT sm4_decrypt(encrypted_phone) AS plaintext
FROM (SELECT sm4_encrypt('13812345678') AS encrypted_phone);
-- 输出: 13812345678

-- 实际业务使用
CREATE TABLE users_encrypted (
    user_id UInt64,
    phone_enc String,
    name String
) ENGINE = MergeTree()
ORDER BY user_id;

INSERT INTO users_encrypted
SELECT number, sm4_encrypt(concat('138', toString(10000000 + number))), concat('user_', toString(number))
FROM numbers(100000);

-- 查询时解密 (注意: 解密视图需要物化或应用层处理)
SELECT user_id, sm4_decrypt(phone_enc) AS phone FROM users_encrypted LIMIT 10;
```

> **坑**：Python UDF 每次 Block 调用都会 `import gmssl`，产生约 50ms 启动开销。如果追求极致性能，考虑将 Python 脚本改为常驻进程（while True 读 stdin）或者用 Rust/C++ 重写为纯二进制 UDF。

---

#### Step 2：Lambda UDF — 纯 SQL 表达式函数

**目标**：无需外部依赖，用 SQL 表达式定义计算函数。

```sql
-- 方式一: CREATE FUNCTION (ClickHouse 21.x+)
-- 线性评分: 将 page_views 映射为 0-100 的评分
CREATE FUNCTION linear_score AS (x, a, b) -> a * x + b;

SELECT 
    page_views,
    linear_score(page_views, 0.5, 10) AS engagement_score
FROM (
    SELECT number AS page_views FROM numbers(10)
);

-- 方式二: 多分支 Lambda — 垃圾内容检测
CREATE FUNCTION spam_score AS (text) ->
    multiIf(
        text LIKE '%buy now%', 0.9,
        text LIKE '%free%', 0.6,
        text LIKE '%discount%', 0.4,
        text LIKE '%click here%', 0.7,
        0.1
    );

SELECT spam_score('buy now, free shipping');

-- 方式三: 复杂 Lambda — 计算工作日天数
CREATE FUNCTION working_days AS (start_date, end_date) ->
    (toUInt32(end_date) - toUInt32(start_date) + 1) -
    toUInt32(
        (toDayOfWeek(start_date) > 1) * 
        floor((toUInt32(end_date) - toUInt32(start_date) + toUInt32(toDayOfWeek(start_date) - 2)) / 7) +
        (toDayOfWeek(start_date) <= 1) * 
        ceil((toUInt32(end_date) - toUInt32(start_date) + toUInt32(toDayOfWeek(start_date) - 2)) / 7)
    ) * 2;

SELECT working_days(toDate('2025-01-01'), toDate('2025-01-31')) AS jan_workdays;
```

> **Lambda UDF 特点**：编译到 SQL 分析阶段，零 IPC 开销，与内置函数性能一致。但受限于 ClickHouse SQL 表达式能力，无法调用外部 C/Python 库。适合简单的数值计算、字符串变换、条件映射。

---

#### Step 3：自定义格式 — Fixed-Width 输出

**目标**：实现 `FORMAT FixedWidth`，输出固定宽度文本用于监管报送。

这里以 C++ 代码说明核心逻辑（生产环境需集成到 ClickHouse 源码树中编译）：

```cpp
// src/Formats/FixedWidthOutputFormat.h
#pragma once
#include <Formats/FormatFactory.h>
#include <Processors/Formats/IOutputFormat.h>

namespace DB
{

class FixedWidthOutputFormat : public IOutputFormat
{
public:
    FixedWidthOutputFormat(
        WriteBuffer & out_,
        const Block & header_,
        const FormatSettings & format_settings_)
        : IOutputFormat(header_, out_)
        , column_widths({10, 20, 15, 30, 12})  // 各列宽度
    {
    }

    String getName() const override { return "FixedWidth"; }

protected:
    void consume(Chunk chunk) override
    {
        auto columns = chunk.getColumns();
        size_t num_rows = chunk.getNumRows();
        size_t num_cols = columns.size();

        for (size_t row = 0; row < num_rows; ++row)
        {
            for (size_t col = 0; col < num_cols; ++col)
            {
                String value = columns[col]->getDataAt(row).toString();

                // 截断或补空格至固定宽度
                if (value.size() > column_widths[col])
                    value.resize(column_widths[col]);
                else
                    value.resize(column_widths[col], ' ');

                writeString(value, out);
            }
            writeChar('\n', out);
        }
    }

private:
    std::vector<size_t> column_widths;
};

// src/Formats/registerFormats.cpp 中添加注册逻辑
void registerFormats(FormatFactory & factory)
{
    // ... 已有注册 ...

    factory.registerOutputFormat("FixedWidth", [](
        WriteBuffer & buf,
        const Block & sample,
        const FormatSettings & settings)
    {
        return std::make_shared<FixedWidthOutputFormat>(buf, sample, settings);
    });
}

} // namespace DB
```

编译自定义格式（动态库方式）：

```bash
# 假设已将 FixedWidthOutputFormat 文件放入 ClickHouse 源码目录
cd /path/to/ClickHouse
mkdir -p build && cd build

# 动态链接编译 (使自定义模块可以作为独立 .so 加载)
cmake -DCMAKE_BUILD_TYPE=Release \
      -DENABLE_TESTS=OFF \
      -DUSE_STATIC_LIBRARIES=0 \
      ..

# 只编译 server 目标 (跳过测试和工具, 加快迭代)
ninja clickhouse-server

# 替换原有 binary 并重启
sudo cp programs/clickhouse-server /usr/bin/clickhouse
sudo systemctl restart clickhouse-server
```

使用自定义格式：

```sql
-- 查询结果以 Fixed-Width 格式输出
SELECT 
    account_id,
    account_name,
    transaction_amount,
    report_date
FROM anti_money_laundering 
WHERE report_date = '2025-03-15'
FORMAT FixedWidth;

-- 输出示例 (每列固定宽度):
-- 0012345678Zhang San           15000.50     2025-03-15
-- 0098765432Li Si              220000.00    2025-03-15
```

```bash
# 命令行导出
clickhouse-client --query="SELECT * FROM aml_report" --format FixedWidth > /data/aml_report_20250315.txt
```

---

#### Step 4：自定义表引擎 — Redis 数据源

**目标**：实现一个从 Redis 读取数据的表引擎，将 Redis Key 模式映射为 ClickHouse 列。

```cpp
// src/Storages/StorageRedis.h
#pragma once
#include <Storages/IStorage.h>
#include <Processors/Sources/SourceWithProgress.h>
#include <Interpreters/Context.h>

namespace DB
{

class StorageRedis final : public IStorage
{
public:
    StorageRedis(
        const StorageID & table_id_,
        const String & redis_url_,
        const String & key_prefix_,
        const ColumnsDescription & columns_,
        const ConstraintsDescription & constraints_,
        const String & comment)
        : IStorage(table_id_)
        , redis_url(redis_url_)
        , key_prefix(key_prefix_)
    {
        StorageInMemoryMetadata storage_metadata;
        storage_metadata.setColumns(columns_);
        storage_metadata.setConstraints(constraints_);
        storage_metadata.setComment(comment);
        setInMemoryMetadata(storage_metadata);
    }

    String getName() const override { return "Redis"; }

    Pipe read(
        const Names & column_names,
        const StorageSnapshotPtr & storage_snapshot,
        SelectQueryInfo & query_info,
        ContextPtr context,
        QueryProcessingStage::Enum processed_stage,
        size_t max_block_size,
        size_t num_streams) override
    {
        auto sample_block = storage_snapshot->getSampleBlockForColumns(column_names);
        auto redis_source = std::make_shared<RedisSource>(sample_block, redis_url, key_prefix);
        return Pipe(redis_source);
    }

    StoragePolicyPtr getStoragePolicy() const override
    {
        return nullptr; // 数据完全在外部 Redis 中
    }

private:
    String redis_url;
    String key_prefix;
};

// 注册到 StorageFactory
void registerStorageRedis(StorageFactory & factory)
{
    factory.registerStorage("Redis", [](
        const StorageFactory::Arguments & args)
    {
        ASTs & engine_args = args.engine_args;
        if (engine_args.size() < 2)
            throw Exception(ErrorCodes::NUMBER_OF_ARGUMENTS_DOESNT_MATCH,
                "Storage Redis requires 2 arguments: redis_url, key_prefix");

        String redis_url = safeGetLiteralValue<String>(engine_args[0], "Redis");
        String key_prefix = safeGetLiteralValue<String>(engine_args[1], "Redis");

        return std::make_shared<StorageRedis>(
            args.table_id, redis_url, key_prefix,
            args.columns, args.constraints, args.comment);
    });
}

} // namespace DB
```

```sql
-- 使用自定义 Redis 引擎创建表
CREATE TABLE stock_quotes (
    symbol String,
    price Float64,
    volume UInt64,
    timestamp DateTime
) ENGINE = Redis('redis://redis.internal:6379', 'stock:quote:');

-- 直接查询 Redis 中的数据
SELECT symbol, price, volume, timestamp
FROM stock_quotes
WHERE symbol IN ('AAPL', 'MSFT', 'GOOGL')
ORDER BY timestamp DESC
LIMIT 100;

-- 结合 UDF 做实时计算
SELECT 
    symbol,
    price,
    linear_score(toUInt64(price), 0.01, 0) AS normalized_score,
    sm4_encrypt(symbol) AS symbol_hash
FROM stock_quotes;
```

> **说明**：完整实现还需要处理 Redis 连接池、Key 解析（SCAN + HGETALL）、数据类型转换、错误重试逻辑等，这里只展示最小接口骨架。生产级 Redis 引擎建议参考 ClickHouse 社区已有的 [clickhouse-redis](https://github.com/spaced4/clickhouse-redis) 项目。

---

#### Step 5：UDF 安全加固

**目标**：给 UDF 加上资源限制，防止失控进程拖垮服务器。

```xml
<!-- /etc/clickhouse-server/config.d/udf-security.xml -->
<clickhouse>
    <user_defined_executable_functions_config>
        <!-- 单次调用超时 5 秒 -->
        <command_timeout_ms>5000</command_timeout_ms>
        <!-- 函数最长执行时间 10 分钟 (防止脚本死循环) -->
        <max_command_execution_time>600</max_command_execution_time>
        <!-- 同时执行的 UDF 进程数上限 -->
        <pool_size>16</pool_size>
        <!-- 超过上限时的请求排队超时 -->
        <send_timeout>10000</send_timeout>
    </user_defined_executable_functions_config>
</clickhouse>
```

Python UDF 安全编码规范：

```python
#!/usr/bin/env python3
"""
UDF 安全最佳实践：
1. 禁止网络访问 (防止数据外泄到外网)
2. 禁止文件系统读写 (除 stdin/stdout 外)
3. 禁止执行系统命令 (os.system, subprocess)
4. 禁止动态导入模块 (importlib)
5. 始终设置信号处理超时
"""

import sys
import signal
import os

# 移除不必要的环境变量
for key in ['PATH', 'PYTHONPATH', 'LD_LIBRARY_PATH']:
   os.environ.pop(key, None)

# 超时保护: 10 秒后强制退出
signal.alarm(10)

# 禁止文件系统操作
def block_open(*args, **kwargs):
    raise PermissionError("File system access is disabled in UDF")

# 仅读取 stdin, 输出 stdout
def main():
    for line in sys.stdin:
        # 业务逻辑 ...
        print(processed_result, flush=True)

if __name__ == '__main__':
    main()
```

```bash
# 生产环境推荐方案: 在 Docker 容器中运行 UDF 脚本
cat > /usr/bin/udf-sandbox << 'EOF'
#!/bin/bash
# 将 ClickHouse 的 command 配置改为调用此脚本
docker run --rm \
    --network=none \           # 无网络
    --read-only \              # 只读文件系统
    --memory=256m \            # 内存限制
    --cpus=0.5 \               # CPU 限制
    --security-opt=no-new-privileges \
    -i \                       # stdin 透传
    udf-python:latest \
    python3 $@
EOF
chmod +x /usr/bin/udf-sandbox

# 修改 executable_udf.xml 中的 command 为:
# <command>/usr/bin/udf-sandbox /var/lib/clickhouse/user_scripts/sm4_encrypt.py</command>
```

---

### 测试验证

```sql
-- 1. 验证 SM4 加解密往返
SELECT 
    count() AS total,
    countIf(phone = sm4_decrypt(sm4_encrypt(phone))) AS match_count
FROM (
    SELECT concat('138', toString(10000000 + number)) AS phone
    FROM numbers(10000)
);
-- 预期: total = 10000, match_count = 10000

-- 2. 验证 Lambda UDF 边界情况
SELECT 
    linear_score(NULL, 0.5, 10) AS null_case,
    linear_score(1e18, 0.5, 10) AS large_case,
    linear_score(0, 0.5, 10) AS zero_case;
-- 预期: NULL, 500000000000000010, 10

-- 3. 验证 Fixed-Width 格式
SELECT 
    'abc' AS col1,
    12345 AS col2
FORMAT FixedWidth;
-- 预期: "abc       12345               " (空格填充至设定宽度)

-- 4. 验证 UDF 超时保护
-- 构造一个会让 Python 脚本计算时间很长的输入
SELECT sm4_encrypt(repeat('x', 10000000));
-- 预期: 5 秒后返回超时错误 (不阻塞整个查询)
```

---

## 4. 项目总结

### 扩展方式对比

| 扩展类型 | 语言 | 复杂度 | 性能 | 适用场景 |
|----------|------|--------|------|----------|
| Lambda UDF | SQL表达式 | 最低 | 最快（零 IPC） | 简单计算、条件映射、数值变换 |
| Executable UDF | Python/Bash/C++ | 低 | 中等（IPC 开销） | 外部库集成、加密算法、格式转换 |
| 自定义格式 | C++ | 中 | 快 | 非标准数据格式、监管报送格式 |
| 自定义表引擎 | C++ | 高 | 快 | 外部数据源接入、自研存储集成 |

### 适用场景

- **国密/行业加密**：金融、政务领域必须使用 SM2/SM3/SM4 等国密算法，通过 Executable UDF 集成
- **内部自研 API 对接**：公司内部有特殊的 WebSocket/RPC/HTTP 协议，用自定义表引擎接入 ClickHouse
- **监管报送格式**：央行、银保监、税务等机构的固定宽度文本、特殊分隔符格式需求
- **Lambda UDF 快捷计算**：动态评分、内容分类、简单数据脱敏——无需部署外部脚本
- **学习+贡献**：自定义模块是理解 ClickHouse 内部架构的最佳切入点，成熟后可提交 PR 回馈社区

### 不适用场景

- **高并发 OLTP 场景**：Executable UDF 的进程启动开销在毫秒级，不适合单次查询几万次的调用
- **需要事务性保证**：自定义表引擎如果接入外部系统，ClickHouse 不做分布式事务协调——写入可能部分成功

### 注意事项

1. **Executable UDF 的 IPC 开销**：按 Block 批量传入可以缓解，但相比内置函数仍有 10-100 倍性能差距。如果性能是关键瓶颈，考虑将 Python 脚本用 C++/Rust 重写为 native function
2. **ClickHouse 内部 API 不稳定**：IStorage、IOutputFormat 等接口在不同大版本间可能 breaking change，升级前务必阅读 Changelog
3. **动态库 vs 静态链接**：`-DUSE_STATIC_LIBRARIES=0` 动态链接模式在不同 Linux 发行版的 libc++/libstdc++ 版本差异下可能出现符号冲突，建议在与生产环境一致的操作系统上编译
4. **UDF 安全隐患**：无内置沙箱，安全责任在运维侧。生产环境务必配合 cgroup + 网络隔离 + 只读文件系统

### 常见踩坑经验

1. **Python UDF 未设置 `flush=True`**：stdout 默认是行缓冲（line-buffered），但在管道模式下会变为全缓冲（fully-buffered），导致 ClickHouse 端收不到输出而阻塞。一定要在 `print()` 中加 `flush=True`，或者 `sys.stdout.reconfigure(line_buffering=True)`
2. **Executable UDF 输入包含特殊字符**：TabSeparated 格式下，如果数据本身包含 `\t` 或 `\n`，会导致行解析错误。ClickHouse 的 TabSeparated 格式会转义这些特殊字符——Python 端需要用 `split('\t')` 和正确的 unescape 逻辑处理
3. **自编译 ClickHouse 升级故障**：用自定义模块编译出来的 `clickhouse-server` 二进制，在集群滚动升级时如果忘记在所有节点重建，会导致查询下发到不同节点时出现 `Unknown table engine` 或 `Unknown format` 错误

### 思考题

1. Executable UDF 默认按 Block 粒度启动进程（每个 Block 约 65536 行），如果改用**进程池**模式（Python 常驻进程 + 消息队列通信），如何设计通信协议来避免序列化开销？提示：考虑使用 Unix Domain Socket + Arrow Flight 格式传递列式数据。

2. 自定义表引擎的 `read()` 方法返回 `Pipe` 对象。如果要支持 `INSERT`（写入）和 `ALTER DELETE`（删除），分别需要额外实现 `IStorage` 的哪些接口方法？这些方法如何与 ClickHouse 的 Mutation 机制（参见第36章）协调工作？提示：查看 `IStorage::write()`、`IStorage::truncate()`、`IStorage::alter()` 等虚函数声明。

---
