# 第34章：自定义 Transformer 开发与动态注册

## 1. 项目背景

某广告平台的数据仓库团队维护着一套 DataX 同步链路：Nginx 访问日志（HDFS）→ 广告投放报表（MySQL）。日志中有一条关键字段——广告请求的客户端 IP 地址。业务方要求在同步过程中将 IP 转换为"国家/省份/城市"三维地理信息，写入报表表。

当前方案是在 DataX 同步完成后，再跑一个 Python 脚本——从 MySQL 读 IP 字段 → 查 GeoIP 库 → 更新 MySQL 的地理信息列。这套方案的痛点：

1. **两步 IO**：数据写进 MySQL 后又被读出来、再写回去——额外的 2 次全表扫描
2. **时效性差**：同步完成到地理信息补齐之间有 30~60 分钟的空窗期，业务报表这段时间里的地理分布是空白
3. **不可复用**：每张需要 IP 地址转换的表都要配置一遍 Python 脚本

架构师提出：**在 DataX 的 Transformer 环节完成 IP 地址转换**——数据在 Channel 流动过程中就"顺便"完成转换，写入目标库时已经包含地理信息。

但 DataX 内置的 `dx_groovy` Transformer 无法完成这个任务——IP 地址库 `ip2region.db` 是一个 11MB 的离线文件，Groovy 脚本中没有加载本地文件的 API；而且 IP 库需要全局只加载一次（11MB 加载到内存中），不能每条 Record 都重新加载。

本章从 `Transformer` 接口入手，实现一个带状态、可加载本地资源的 `dx_ip2region` Transformer，并介绍 Transformer 的注册机制与 JSON 配置调用方式。

## 2. 项目设计——剧本式交锋对话

**（数据平台工位区，小胖在跑广告报表的补充脚本，已经等得不耐烦了）**

**小胖**：（抱怨）这个 Python 脚本要跑 40 分钟——先 `SELECT ip FROM report WHERE province IS NULL`，再一行行调 GeoIP 库，再 `UPDATE` 回去。5000 万行数据是这么玩的吗？

**小白**：你可以把 GeoIP 的逻辑移到 DataX 同步环节啊——数据在读和写之间的 Transformer 环节就把 IP 转了，写到 MySQL 时已经是完整的地理信息。

**小胖**：但 DataX 的 Transformer 不是只有 Groovy 吗？Groovy 里怎么加载 11MB 的 ip2region 库？

**大师**：（翻开 DataX 源码的 Transformer 接口）DataX 的 Transformer 体系比你想象的灵活得多。它有两种接口：

**`Transformer` 接口**（无状态——适合简单逐行转换）：

```java
public abstract class Transformer {
    // 每条 Record 调一次，无上下文，不能持状态
    public abstract Record evaluate(Record record, Object... paras);
}
```

dx_groovy、dx_substr、dx_pad 都是这个接口的实现——每条 Record 独立处理，不需要外部资源。

**`ComplexTransformer` 接口**（有状态——适合需要全局资源的场景）：

```java
public abstract class ComplexTransformer {
    private Configuration transformerConfig;
    
    // 初始化一次性资源（如加载 ip2region.db 到内存）
    public void init() { }
    
    // 每条 Record 调一次——可以访问 init() 加载的全局资源
    public abstract Record evaluate(Record record, Object... paras);
}
```

我们的 `dx_ip2region` 就得用 `ComplexTransformer`——在 `init()` 中加载 11MB 的 IP 库到内存，`evaluate()` 中做 O(1) 的 IP 查找。

**技术映射**：`Transformer` = 流水线工人，每条数据到手了就直接操作（剪断、补位）。`ComplexTransformer` = 带了工具台的流水线工人——开工前先把 ip2region.db 这台"重型设备"搬到工位上（init），然后每条数据来了就直接用这台设备处理（evaluate）。

**小白**：（追问）那 `ComplexTransformer` 的注册机制呢？DataX 怎么知道插件里有哪些 Transformer 可用？

**大师**：通过**SPI（Service Provider Interface）机制**——在插件的 `META-INF/services` 目录下放一个文件，列出 Transformer 的完整类名。

插件结构：
```
plugin/transformer/dx_ip2region/
├── plugin.json
├── dx_ip2region.jar
├── libs/
│   ├── ip2region-2.7.0.jar
│   └── ...
└── ip2region.db          ← 离线数据库文件
```

**`plugin.json`**：
```json
{
    "name": "dx_ip2region",
    "class": "com.alibaba.datax.plugin.transformer.ip2region.Ip2RegionTransformer",
    "description": "IP地址转地理位置(国家/省份/城市)",
    "developer": "data-platform"
}
```

**SPI 注册文件** `META-INF/services/com.alibaba.datax.transformer.Transformer`：
```
com.alibaba.datax.plugin.transformer.ip2region.Ip2RegionTransformer
```

这样 DataX 在加载插件时，扫描 `plugin/transformer/` 下的所有 `plugin.json`，通过 SPI 发现并注册 Transformer。

**小胖**：（举手）那 JSON 配置里怎么调用这个 Transformer？

**大师**：跟 `dx_groovy` 一样的配置方式：

```json
{
    "job": {
        "content": [{
            "reader": { ... },
            "writer": { ... },
            "transformer": [{
                "name": "dx_ip2region",
                "parameter": {
                    "columnIndex": 5,
                    "ipDbPath": "plugin/transformer/dx_ip2region/ip2region.db",
                    "resultFormat": "full",
                    "targetColumns": ["country", "province", "city"]
                }
            }]
        }]
    }
}
```

**`columnIndex`**：输入——来源数据中 IP 字段的列索引（从 0 开始）
**`ipDbPath`**：ip2region.db 文件的路径
**`targetColumns`**：输出——Transformer 结束后追加 3 列（国家、省份、城市）

**小白**：（追问）那 ip2region 的 IP 查找算法是什么？5000 万条数据逐条查性能够吗？

**大师**：ip2region 使用二分查找 + B-tree 索引。11MB 的 db 文件加载到内存后是一个 `int[]` 数组（每个 IP 段存储 4 字节起始 IP + 4 字节结束 IP + 数据指针），在内存中用二分查找定位，时间复杂度 O(log N)，单次查询约 1~5 微秒。5000 万次查询 = 50~250 秒——对于 Python 那种行级查询是秒杀级别的提升。

**小胖**：（眼睛一亮）那是不是所有需要全局资源的场景都可以用 `ComplexTransformer`？

**大师**：对，常见场景包括：

1. **IP 地址解析**（本章）→ 加载 ip2region.db 到内存
2. **手机号归属地**→ 加载手机号段库
3. **ID 映射补全**→ 加载 Redis/本地缓存的映射表（如 user_id → user_name）
4. **数据脱敏**（如身份证打码）→ 加载密钥文件
5. **字典表翻译**→ 加载编码映射表（如 status_code → status_name）

核心原则：**凡是需要"全局只加载一次、然后每条 Record 都查询"的资源，都适合用 ComplexTransformer**。

## 3. 项目实战

### 3.1 步骤一：创建 Transformer Maven 项目

**目标**：建立标准 Transformer 插件项目，引入 ip2region 依赖。

```powershell
New-Item -ItemType Directory -Path "dx_ip2region\src\main\java\com\alibaba\datax\plugin\transformer\ip2region" -Force
New-Item -ItemType Directory -Path "dx_ip2region\src\main\resources\META-INF\services" -Force
```

**项目结构**：

```
dx_ip2region/
├── pom.xml
├── src/main/
│   ├── java/com/alibaba/datax/plugin/transformer/ip2region/
│   │   └── Ip2RegionTransformer.java
│   └── resources/
│       └── META-INF/
│           └── services/
│               └── com.alibaba.datax.transformer.Transformer
├── plugin.json
└── ip2region.db
```

**`pom.xml`**：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0
         http://maven.apache.org/xsd/maven-4.0.0.xsd">
    <modelVersion>4.0.0</modelVersion>

    <groupId>com.alibaba.datax</groupId>
    <artifactId>dx_ip2region</artifactId>
    <version>1.0.0</version>

    <dependencies>
        <dependency>
            <groupId>com.alibaba.datax</groupId>
            <artifactId>datax-common</artifactId>
            <version>3.0.0</version>
            <scope>provided</scope>
        </dependency>

        <!-- ip2region 离线库 -->
        <dependency>
            <groupId>org.lionsoul</groupId>
            <artifactId>ip2region</artifactId>
            <version>2.7.0</version>
        </dependency>
    </dependencies>
</project>
```

### 3.2 步骤二：实现 ComplexTransformer

**目标**：实现 `ComplexTransformer` 接口，init() 加载 ip2region.db，evaluate() 做 IP 查询。

**`Ip2RegionTransformer.java`**：

```java
package com.alibaba.datax.plugin.transformer.ip2region;

import com.alibaba.datax.common.element.Column;
import com.alibaba.datax.common.element.Record;
import com.alibaba.datax.common.element.StringColumn;
import com.alibaba.datax.common.exception.DataXException;
import com.alibaba.datax.transformer.ComplexTransformer;
import com.alibaba.datax.common.util.Configuration;
import org.lionsoul.ip2region.xdb.Searcher;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.File;
import java.util.List;

public class Ip2RegionTransformer extends ComplexTransformer {
    private static final Logger LOG = LoggerFactory.getLogger(Ip2RegionTransformer.class);

    private int columnIndex;
    private String resultFormat;
    private List<String> targetColumns;
    private Searcher searcher;  // ip2region 核心搜索引擎（全局单例）

    @Override
    public void init() {
        Configuration config = super.getTransformerConfig();

        // 1. 解析参数
        this.columnIndex = config.getInt("columnIndex", -1);
        if (this.columnIndex < 0) {
            throw DataXException.asDataXException(
                Ip2RegionErrorCode.REQUIRED_PARAM_MISSING,
                "columnIndex 参数必须指定（IP 字段在 Record 中的列位置）");
        }

        this.resultFormat = config.getString("resultFormat", "full");
        this.targetColumns = config.getList("targetColumns", String.class);

        // 2. 加载 ip2region.db 到内存
        String ipDbPath = config.getString("ipDbPath",
            System.getProperty("datax.home") + "/plugin/transformer/dx_ip2region/ip2region.db");

        LOG.info("ip2region Transformer init: loading db from {}", ipDbPath);

        File dbFile = new File(ipDbPath);
        if (!dbFile.exists()) {
            throw DataXException.asDataXException(
                Ip2RegionErrorCode.DB_FILE_NOT_FOUND,
                "ip2region.db 文件不存在: " + ipDbPath);
        }

        try {
            // 加载整个 db 文件到内存（约 11MB）
            byte[] dbData = java.nio.file.Files.readAllBytes(dbFile.toPath());
            this.searcher = Searcher.newWithBuffer(dbData);
            LOG.info("ip2region db loaded: {} bytes, version={}",
                dbData.length, this.searcher.getDbVersion());
        } catch (Exception e) {
            throw DataXException.asDataXException(
                Ip2RegionErrorCode.DB_LOAD_FAILED,
                "ip2region.db 加载失败: " + e.getMessage());
        }
    }

    @Override
    public Record evaluate(Record record, Object... paras) {
        // 获取 IP 字段的值
        Column ipColumn = record.getColumn(columnIndex);
        if (ipColumn == null) {
            appendEmptyResult(record);
            return record;
        }

        String ip = ipColumn.asString();
        if (ip == null || ip.trim().isEmpty()) {
            appendEmptyResult(record);
            return record;
        }

        // 去掉端口号（如果包含）
        if (ip.contains(":")) {
            ip = ip.substring(0, ip.indexOf(":"));
        }

        try {
            String region = this.searcher.search(ip);

            // ip2region 的返回格式: 国家|区域|省份|城市|ISP
            // 如: 中国|0|广东省|深圳市|电信
            if (region == null || region.isEmpty()) {
                appendEmptyResult(record);
            } else {
                String[] parts = region.split("\\|");
                appendRegionColumns(record, parts);
            }
        } catch (Exception e) {
            LOG.debug("IP lookup failed for '{}': {}", ip, e.getMessage());
            appendEmptyResult(record);
        }

        return record;
    }

    private void appendRegionColumns(Record record, String[] parts) {
        // parts: [国家, 区域, 省份, 城市, ISP]
        String country  = (parts.length > 0 && !"0".equals(parts[0])) ? parts[0] : "";
        String province = (parts.length > 2 && !"0".equals(parts[2])) ? parts[2] : "";
        String city     = (parts.length > 3 && !"0".equals(parts[3])) ? parts[3] : "";

        switch (resultFormat) {
            case "full":
                // 追加 3 列：国家、省份、城市
                record.addColumn(new StringColumn(country));
                record.addColumn(new StringColumn(province));
                record.addColumn(new StringColumn(city));
                break;
            case "country":
                record.addColumn(new StringColumn(country));
                break;
            case "province":
                record.addColumn(new StringColumn(province));
                break;
            case "city":
                record.addColumn(new StringColumn(city));
                break;
            case "combined":
                // 拼接为一列
                String combined = country + "|" + province + "|" + city;
                record.addColumn(new StringColumn(combined));
                break;
            default:
                record.addColumn(new StringColumn(country));
                record.addColumn(new StringColumn(province));
                record.addColumn(new StringColumn(city));
        }
    }

    private void appendEmptyResult(Record record) {
        // IP 为空或查询失败时补空串，保证列数一致
        switch (resultFormat) {
            case "full":
                record.addColumn(new StringColumn(""));
                record.addColumn(new StringColumn(""));
                record.addColumn(new StringColumn(""));
                break;
            case "combined":
                record.addColumn(new StringColumn("||"));
                break;
            default:
                record.addColumn(new StringColumn(""));
        }
    }
}
```

### 3.3 步骤三：注册 SPi 与 plugin.json

**目标**：通过 SPI 文件和 plugin.json 让 DataX 发现并加载 Transformer。

**`src/main/resources/META-INF/services/com.alibaba.datax.transformer.Transformer`**：

```
com.alibaba.datax.plugin.transformer.ip2region.Ip2RegionTransformer
```

**`plugin.json`**（放在项目根目录，编译后与 jar 同层）：

```json
{
    "name": "dx_ip2region",
    "class": "com.alibaba.datax.plugin.transformer.ip2region.Ip2RegionTransformer",
    "description": "IP2Region Transformer - convert IP to country/province/city using offline ip2region.db",
    "developer": "data-platform"
}
```

**编译与部署**：

```powershell
# 编译
mvn clean package -DskipTests

# 部署到 DataX
$target = "$env:DATAX_HOME\plugin\transformer\dx_ip2region"
New-Item -ItemType Directory -Path "$target\libs" -Force
Copy-Item "target\dx_ip2region-1.0.0.jar" -Destination "$target\"
Copy-Item "plugin.json" -Destination "$target\"
Copy-Item "ip2region.db" -Destination "$target\"

# 验证目录结构
Get-ChildItem -Recurse "$target"
# 输出:
# plugin/transformer/dx_ip2region/
#   ├── plugin.json
#   ├── dx_ip2region-1.0.0.jar
#   ├── ip2region.db
#   └── libs/
#       └── ip2region-2.7.0.jar
```

### 3.4 步骤四：JSON 配置与调用

**目标**：在 DataX Job 中配置 dx_ip2region Transformer 完成 IP 转地理信息。

**完整 Job 配置**（`adb_report_ip2region.json`）：

```json
{
    "job": {
        "content": [{
            "reader": {
                "name": "mysqlreader",
                "parameter": {
                    "username": "root",
                    "password": "root",
                    "column": ["log_id", "ad_id", "user_id", "request_time", "client_ip", "click_flag"],
                    "splitPk": "log_id",
                    "connection": [{
                        "table": ["ad_request_log"],
                        "jdbcUrl": ["jdbc:mysql://localhost:3306/ad_platform?useSSL=false"]
                    }]
                }
            },
            "writer": {
                "name": "mysqlwriter",
                "parameter": {
                    "username": "root",
                    "password": "root",
                    "writeMode": "insert",
                    "column": ["log_id", "ad_id", "user_id", "request_time", "client_ip", "click_flag", "country", "province", "city"],
                    "preSql": ["TRUNCATE TABLE ad_report_dw"],
                    "batchSize": 4096,
                    "connection": [{
                        "table": ["ad_report_dw"],
                        "jdbcUrl": ["jdbc:mysql://localhost:3306/ad_platform?useSSL=false&rewriteBatchedStatements=true"]
                    }]
                }
            },
            "transformer": [{
                "name": "dx_ip2region",
                "parameter": {
                    "columnIndex": 4,
                    "ipDbPath": "plugin/transformer/dx_ip2region/ip2region.db",
                    "resultFormat": "full",
                    "targetColumns": ["country", "province", "city"]
                }
            }]
        }],
        "setting": {
            "speed": {"channel": 10},
            "errorLimit": {"record": 0, "percentage": 0}
        }
    }
}
```

**关键参数说明**：
- `columnIndex=4`：Reader 的 column 数组中 `client_ip` 在第 4 列（从 0 开始）
- `resultFormat=full`：追加 3 列（country, province, city）
- Reader 投影了 6 列 + Transformer 追加 3 列 = Writer 接收 9 列

**执行命令**：

```powershell
python datax.py jobs/adb_report_ip2region.json
```

**日志关键行**：

```
ip2region Transformer init: loading db from plugin/transformer/dx_ip2region/ip2region.db
ip2region db loaded: 11542184 bytes, version=2024.07
Task taskId=0 finished. Read records: 12500000, elapsed: 6m 42s
Avg throughput: 31094 rec/s
DataX运行完毕，总耗时 6m 45s
```

**MySQL 结果验证**：

```sql
-- 查看 IP 转地理信息的结果
SELECT client_ip, country, province, city 
FROM ad_report_dw 
LIMIT 10;

/*
+----------------+---------+----------+----------+
| client_ip      | country | province | city     |
+----------------+---------+----------+----------+
| 59.37.123.45   | 中国    | 广东省   | 深圳市   |
| 1.119.100.200  | 中国    | 北京市   | 北京市   |
| 202.96.199.133 | 中国    | 广东省   | 广州市   |
| 8.8.8.8        | 美国    |          |          |
| 192.168.1.1    |         |          |          |  ← 内网 IP 返回空串
+----------------+---------+----------+----------+

-- 统计地理分布
SELECT province, COUNT(*) as cnt 
FROM ad_report_dw 
GROUP BY province 
ORDER BY cnt DESC 
LIMIT 5;
*/
```

### 3.5 步骤五：跨行去重——stateful Transformer 进阶场景

**目标**：实现一个"连续窗口去重"的 Transformer——同一个 user_id 在连续 5 分钟内只保留第一条记录，适用于去重广告曝光日志。

```java
// TimeWindowDedupTransformer —— 有状态去重 Transformer
public class TimeWindowDedupTransformer extends ComplexTransformer {
    // 全局状态：user_id → 上一次出现的时间戳
    private Map<String, Long> lastSeenMap = new ConcurrentHashMap<>();
    private long windowMs;  // 去重窗口（毫秒）

    @Override
    public void init() {
        Configuration config = super.getTransformerConfig();
        this.windowMs = config.getLong("windowMs", 300000L);  // 默认 5 分钟
    }

    @Override
    public Record evaluate(Record record, Object... paras) {
        String userId = record.getColumn(0).asString();    // user_id 在第 0 列
        Long timestamp = record.getColumn(1).asLong();      // request_time 在第 1 列

        Long lastTs = lastSeenMap.put(userId, timestamp);

        if (lastTs != null && (timestamp - lastTs) < windowMs) {
            return null;  // 在窗口内重复 → 丢弃该 Record
        }

        return record;  // 保留
    }
}
```

**注意**：`record.addColumn()` 在 `ComplexTransformer` 中可能导致列错位——因为多个 Transformer 串行执行时，每个 Transformer 修改的列数不同，后续 Transformer 的 `columnIndex` 会偏移。生产环境建议：**追加列的 Transformer 放在最后一个执行**，或者用列名而不是列索引定位。

### 3.6 可能遇到的坑及解决方法

**坑1：ip2region.db 文件找不到**

DataX 的 ClassLoader 隔离导致 `ClassLoader.getResource("ip2region.db")` 返回 null。

```
报错: ip2region.db 文件不存在
解决: 使用绝对路径或相对于 datax.home 的路径
     String path = System.getProperty("datax.home") + "/plugin/transformer/dx_ip2region/ip2region.db";
```

**坑2：ComplexTransformer 的 evaluate 返回 null 导致后续 Transformer 抛异常**

如果 Transformer 链中有多个 Transformer，前一个返回 null 后一个会 NPE。

```
报错: NullPointerException at transformer evaluate
解决: 返回 null 表示丢弃该 Record（不传给后续 Transformer 和 Writer）
     但必须在 Transformer 链中排最前面
```

**坑3：ip2region 的内存占用增量**

`searcher = Searcher.newWithBuffer(dbData)` 在 JVM 堆中复制了一份 11MB 的 `byte[]`——如果 channel=20，每个 Task 加载一份 = 220MB 额外堆占用。

```
解决: 
1. 在 Job.init() 中加载一次，通过 Configuration 的 static 字段共享给所有 Task
2. 或使用 ip2region 的 mmap 模式（Searcher.newWithFileHandle），多线程共享同一份映射
```

**坑4：Transformer 追加列后 Writer 列数不匹配**

Reader 投影了 6 列，Transformer 追加了 3 列 = 9 列。但 Writer 的 `column` 配置只写了 6 列——DataX 不会自动填充。

```
报错: IndexOutOfBoundsException at Writer.writeOneRecord
解决: Writer 的 column 配置必须包含追加后的所有列（共 9 列）
```

## 4. 项目总结

### 4.1 Transformer 类型对比

| 特性 | Transformer | ComplexTransformer |
|------|------------|-------------------|
| 状态 | 无状态 | 有状态（持全局资源） |
| 初始化 | 无 init() | 有 init()（加载资源） |
| 适用场景 | substr/pad/replace 等简单映射 | IP 库、字典表、脱敏密钥 |
| 性能 | 极快（纯计算） | 取决于资源查询速度 |
| 线程安全 | 天然安全 | 需自行保证（如 ConcurrentHashMap） |
| 注册方式 | SPI + plugin.json | SPI + plugin.json |
| JSON 配置 | `"name": "dx_xxx"` | 同左 |

### 4.2 优点

1. **Transformer 链式组合**：JSON 中配置多个 Transformer，按序执行，每个做一件事
2. **有状态支持**：ComplexTransformer 可加载本地文件/内存缓存，适合重型转换
3. **SPI 自动发现**：插件丢进目录就自动注册，无需改 DataX 引擎代码
4. **流式处理**：Record 逐条经过 Transformer → Channel → Writer，不落盘
5. **扩展性强**：可复用到手机号归属地、ID 映射、数据脱敏等任意场景

### 4.3 缺点

1. **列索引脆弱**：用 columnIndex 定位，Reader 的 column 顺序一变 Transformer 就错位
2. **加载开销**：每个 Task 都加载一次 ip2region.db（可通过 static 共享优化）
3. **无回滚**：Transformer 中间的转换不可回滚（修改了 Record 就改了）
4. **链式顺序敏感**：追加列的 Transformer 必须在最后，否则后续 Transformer 的 columnIndex 失效

### 4.4 适用场景

1. IP 地址 → 地理信息（本章场景）
2. 手机号 → 归属地/运营商
3. 身份证号 → 性别/年龄/地区（脱敏场景）
4. 状态码 → 中文描述（字典翻译）
5. 加密字段 → 解密（需加载密钥文件）

### 4.5 不适用场景

1. 需要跨行关联的转换（如 JOIN 补全）——Transformer 只处理单行
2. 大量数据需要外查数据库的转换（每条都查 DB 性能太差，可先加载到内存缓存再用 Transformer）

### 4.6 注意事项

1. **记录追加后的列数要匹配 Writer**：Reader 列 + Transformer 追加列 = Writer 列
2. **ComplexTransformer 的 init() 只执行一次**——全局资源的初始化放这里
3. **ip2region.db 需定期更新**（如每月更新一次 GeoIP 数据），更新后需替换文件并重启 DataX 任务
4. **内网 IP 返回空串**是正常行为——ip2region 库不包含私有地址段

### 4.7 思考题

1. 如果需要两个 Transformer 链式执行：第一个做 IP → 地理信息（追加 3 列），第二个做 user_id → user_name 字典翻译（追加 1 列）。如何避免第二个 Transformer 的 columnIndex 因第一个追加列而偏移？

2. ComplexTransformer 的 init() 在 Job 初始化时执行一次还是每个 Task 初始化时执行一次？如果 channel=20，ip2region.db 会被加载 20 次到内存吗？如何优化为全局只加载一次？

（答案见附录）
