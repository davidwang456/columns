# 第11章：HDFS Reader/Writer——打通大数据生态

## 1. 项目背景

某视频平台的数据架构是典型的 Lambda 架构：实时流走 Kafka → Flink，离线批走 DataX → HDFS 数据湖。数据分析团队每天需要将 MySQL 业务库的 300+ 张表同步到 HDFS，供 Spark/Hive 做离线分析。HDFS 上的数据格式要求为 ORC（列式存储 + 高压缩率），按 `dt=2026-05-06` 做日期分区。

运维用 sqoop 跑了一个月后发现了三个严重问题：第一，sqoop 默认输出 TextFile，转 ORC 需要额外跑一个 Hive INSERT OVERWRITE 任务，增加 40 分钟；第二，sqoop 在 Kerberos 认证环境下需要单独配置 keytab 路径，经常因为票据过期导致任务静默失败；第三，当 HDFS NameNode 发生 HA 切换时，sqoop 必须手工修改 `--target-dir` 参数中的 namenode 地址。

切换到 DataX HDFS 插件后，这三个问题迎刃而解：hdfswriter 直接支持 fileType=orc，一步到位；Kerberos 认证通过 `hadoopConfig` 中配置 `hadoop.security.authentication=kerberos` 自动化；HA 模式通过 `defaultFS` 配置 nameservice 自动 failover。本章带你完整走通 MySQL → HDFS → MySQL 的双向链路。

## 2. 项目设计——剧本式交锋对话

**（大数据平台组，屏幕上显示昨晚的同步任务失败告警）**

**小胖**：（抓狂）又来了！sqoop 导入 HDFS 的任务因为 Kerberos 票据过期挂了，一晚上白跑了！

**小白**：（冷静地说）我上个月就建议切换到 DataX 的 hdfswriter 了。它通过 `hadoopConfig` 集成 Kerberos，不需要在外面手动 kinit。

**大师**：（打开 hdfswriter 的源码）DataX 的 HDFS 插件比 sqoop 灵活得多。它不是简单地把数据"灌"到 HDFS，而是提供了完整的数据格式抽象层。你看这个配置——

```json
{
    "writer": {
        "name": "hdfswriter",
        "parameter": {
            "defaultFS": "hdfs://ns-cluster",
            "fileType": "orc",
            "path": "/user/hive/warehouse/orders/dt=${bizdate}",
            "fileName": "orders",
            "writeMode": "truncate",
            "compress": "SNAPPY",
            "fieldDelimiter": "\t",
            "hadoopConfig": {
                "hadoop.security.authentication": "kerberos",
                "dfs.nameservices": "ns-cluster",
                "dfs.ha.namenodes.ns-cluster": "nn1,nn2",
                "dfs.namenode.rpc-address.ns-cluster.nn1": "master1:8020",
                "dfs.namenode.rpc-address.ns-cluster.nn2": "master2:8020",
                "dfs.client.failover.proxy.provider.ns-cluster": "org.apache.hadoop.hdfs.server.namenode.ha.ConfiguredFailoverProxyProvider"
            }
        }
    }
}
```

一份配置包含了文件格式、压缩算法、分区路径、HA 高可用、Kerberos 认证——全部声明式，不需要你在外面多写一行 Shell。

**技术映射**：hdfswriter 配置 = 一份全自动售货机的点餐单。你说我要 ORC 格式 + SNAPPY 压缩 + 写入 `/dt=2026-05-06/` 路径 + Kerberos 认证——它全帮你搞定。

**小胖**：（探头看源码）不对啊，ORC 不是列式存储格式吗？DataX 一条条 Row 传到 Writer，怎么变成列式？

**大师**：（赞许地点点头）这个问题问到了 HDFS 插件设计的精髓。hdfswriter 内部不是自己实现 ORC 编码的，它依靠 `plugin-unstructured-storage-util` 模块，该模块封装了 Hadoop 原生的 `OrcOutputFormat` / `ParquetOutputFormat`。dataX 传入 Row → util 内部缓存 → 攒够一批后调用 Hadoop Writer 刷出一个 Stripe（ORC 的最小写入单元）。

**小白**：（追问）那文件切分呢？hdfsreader 怎么知道应该并发的读哪些文件块？

**大师**：hdfsreader 支持两种切分方式：
1. **按文件切分**：目录下有 10 个文件 → 最多切 10 个 Task
2. **按 Block 切分**：一个 1GB 的文件有 8 个 Block（128MB/Block） → 最多切 8 个 Task

这比 sqoop 的 `--split-by` 更智能——sqoop 只能按字段值切分，如果表没有合适的分片键，就只能单线程跑。

## 3. 项目实战

### 3.1 步骤一：MySQL → HDFS（全量导出到 ORC）

**目标**：将 MySQL 订单表导出为 ORC 格式，存入 HDFS 分区目录。

```json
{
    "job": {
        "content": [{
            "reader": {
                "name": "mysqlreader",
                "parameter": {
                    "username": "etl_user",
                    "password": "${MYSQL_PWD}",
                    "column": ["id", "user_name", "amount", "status", "create_time"],
                    "splitPk": "id",
                    "connection": [{
                        "table": ["orders"],
                        "jdbcUrl": ["jdbc:mysql://10.0.1.100:3306/trade_db"]
                    }]
                }
            },
            "writer": {
                "name": "hdfswriter",
                "parameter": {
                    "defaultFS": "hdfs://ns-cluster",
                    "fileType": "orc",
                    "path": "/data/warehouse/orders/dt=${bizdate}",
                    "fileName": "orders",
                    "column": [
                        {"name": "id", "type": "BIGINT"},
                        {"name": "user_name", "type": "STRING"},
                        {"name": "amount", "type": "DOUBLE"},
                        {"name": "status", "type": "INT"},
                        {"name": "create_time", "type": "DATE"}
                    ],
                    "writeMode": "truncate",
                    "compress": "SNAPPY",
                    "fieldDelimiter": "\t",
                    "hadoopConfig": {
                        "dfs.nameservices": "ns-cluster",
                        "dfs.ha.namenodes.ns-cluster": "nn1,nn2",
                        "dfs.namenode.rpc-address.ns-cluster.nn1": "n1:8020",
                        "dfs.namenode.rpc-address.ns-cluster.nn2": "n2:8020",
                        "dfs.client.failover.proxy.provider.ns-cluster": "org.apache.hadoop.hdfs.server.namenode.ha.ConfiguredFailoverProxyProvider"
                    }
                }
            }
        }],
        "setting": {
            "speed": {
                "channel": 10
            }
        }
    }
}
```

**column 类型映射表**（DataX Column → ORC Schema）：

| DataX Column | ORC Type | Hive Type |
|-------------|----------|-----------|
| LongColumn | BIGINT | BIGINT |
| DoubleColumn | DOUBLE | DOUBLE |
| StringColumn | STRING | STRING |
| DateColumn | DATE 或 TIMESTAMP | TIMESTAMP |
| BoolColumn | BOOLEAN | BOOLEAN |
| BytesColumn | BINARY | BINARY |

### 3.2 步骤二：在 Hive 中建外表关联

```sql
-- Hive 建外表，关联 HDFS 上的 ORC 文件
CREATE EXTERNAL TABLE orders_ods (
    id BIGINT,
    user_name STRING,
    amount DOUBLE,
    status INT,
    create_time TIMESTAMP
)
PARTITIONED BY (dt STRING)
STORED AS ORC
LOCATION '/data/warehouse/orders'
TBLPROPERTIES ('orc.compress'='SNAPPY');

-- 修复分区（DataX 写入文件后，Hive 不会自动感知新分区）
MSCK REPAIR TABLE orders_ods;

-- 或手动添加分区（推荐，比 MSCK 快）
ALTER TABLE orders_ods ADD IF NOT EXISTS PARTITION (dt='2026-05-06');
```

### 3.3 步骤三：HDFS → MySQL（数据回流）

**目标**：将 HDFS 上清洗后的 ORC 数据回写到 MySQL 报表库。

```json
{
    "reader": {
        "name": "hdfsreader",
        "parameter": {
            "path": "/data/warehouse/orders/dt=2026-05-06/*",
            "defaultFS": "hdfs://ns-cluster",
            "fileType": "orc",
            "column": [
                {"index": 0, "type": "long"},
                {"index": 1, "type": "string"},
                {"index": 2, "type": "double"},
                {"index": 3, "type": "long"},
                {"index": 4, "type": "date"}
            ],
            "hadoopConfig": { ... }
        }
    },
    "writer": {
        "name": "mysqlwriter",
        "parameter": {
            "writeMode": "replace",
            "column": ["id", "user_name", "amount", "status", "create_time"],
            "connection": [{
                "table": ["report_orders"],
                "jdbcUrl": ["jdbc:mysql://10.0.1.200:3306/report_db"]
            }]
        }
    }
}
```

**hdfsreader 的 column 配置**：不是按列名，而是按 `index` 来选择 ORC 文件中的第几列。ORC 是列式存储，DataX Reader 根据 `index` 列表只反序列化这些列（列裁剪），大幅减少 IO。

### 3.4 步骤四：Kerberos 认证配置

**目标**：在安全集群环境中通过 Kerberos 认证访问 HDFS。

**前置条件**：已经通过 `kinit` 获取 TGT，或配置 `keytab` 自动续期。

```json
{
    "hadoopConfig": {
        "hadoop.security.authentication": "kerberos",
        "hadoop.security.authorization": "true",
        "dfs.namenode.kerberos.principal": "hdfs/_HOST@REALM.COM"
    }
}
```

**TGT 自动续期脚本**（在 datax.py 启动前执行）：

```bash
#!/bin/bash
export KRB5CCNAME=/tmp/krb5cc_datax_$$

# 用 keytab 认证
kinit -kt /etc/security/keytabs/datax.keytab datax/hostname@REALM.COM

# 启动 DataX
python bin/datax.py job.json

# 清理票据
kdestroy
```

**验证 Kerberos 是否生效**：

```bash
# 用 hadoop 命令验证
hadoop fs -ls /data/warehouse/

# 查看 Kerberos 票据
klist
```

### 3.5 步骤五：文件格式与压缩算法的选择指南

| 文件格式 | 压缩算法 | 压缩率 | 读性能 | 写性能 | 适用场景 |
|---------|---------|-------|--------|--------|---------|
| TEXT | gzip | 高(7:1) | 慢 | 慢 | 日志归档 |
| CSV | none | 无 | 快 | 快 | 数据交换（给其他工具读） |
| ORC | SNAPPY | 中(3:1) | 快 | 中 | Hive/Spark 分析 |
| ORC | ZLIB | 高(5:1) | 中 | 慢 | 冷数据归档 |
| Parquet | SNAPPY | 中(3:1) | 快 | 中 | Presto/Impala 查询 |
| Parquet | GZIP | 高(5:1) | 慢 | 慢 | 跨云传输 |

**建议**：
- Hive/Spark 离线分析 → ORC + SNAPPY
- 跨云存储/传输 → Parquet + GZIP
- 给 BI 工具直接消费 → CSV + none（兼容性最好）

### 3.6 可能遇到的坑及解决方法

**坑1：HDFS 写入权限不足**

```
ERROR HdfsWriter$Task - Permission denied: user=datax, access=WRITE, 
inode="/data/warehouse":hive:hadoop:drwxr-xr-x
```

解决：确保 DataX 运行用户（即 datax.py 的执行用户）有目标目录的写权限，或改用有权限的用户执行。

**坑2：ORC 文件无法被 Hive 读取**

原因：DataX hdfswriter 生成的 ORC 文件默认不包含 Hive 元数据。

解决：建外部表关联后，执行 `MSCK REPAIR TABLE` 或手动 `ALTER TABLE ADD PARTITION`。

**坑3：HDFS NameNode HA 切换时任务失败**

原因：DataX 建立的 HDFS 连接在 failover 后没有重试机制。

解决：配置 `dfs.client.failover.proxy.provider` 和相关 HA 参数，让 Hadoop Client 自动处理 failover。

**坑4：Kerberos 票据在长时间任务中过期**

解决两种方案：
1. 在 cron 中加定时 `kinit -R` 续期（`cron */6 * * * * kinit -R`）
2. 在 `datax.py` 启动脚本中嵌入 ticket 续期逻辑

**坑5：hdfsreader 读取大目录时 OOM**

原因：hdfsreader 在 `preCheck` 阶段会列出目录下所有文件，如果目录下有 10 万+ 个小文件，list 操作会占用大量内存。

解决：第一步先用 `hadoop fs -getmerge` 合并小文件，再用 DataX 读取合并后的大文件。

## 4. 项目总结

### 4.1 优点

1. **多格式原生支持**：ORC/Parquet/TEXT/CSV 一步到位，无需额外转换
2. **HA 透明**：通过 Hadoop Configuration 参数自动 failover
3. **Kerberos 集成**：无需外部 kinit，无需定时续期脚本
4. **列裁剪**：hdfsreader 通过 index 指定读取列，减少 IO
5. **压缩可选**：SNAPPY/ZLIB/GZIP/BZIP2/LZO 五种算法，按需选择

### 4.2 缺点

1. **依赖 Hadoop 环境**：需要在运行节点上安装 Hadoop Client 库（或在插件 libs 中打包）
2. **小文件性能差**：每个文件至少一个 HDFS Block（典型 128MB），小文件浪费空间且读取慢
3. **Kerberos 配置复杂**：HA+Kerberos 组合下的配置项多达 10 个
4. **不支持 HDFS 联邦**：多个 nameservice 时只能指定一个 defaultFS
5. **ORC 写入无 Bloom Filter**：不支持建索引，Hive 查询优化受限

### 4.3 适用场景

1. MySQL → HDFS 数据湖每日全量导出（ETL 第一步）
2. HDFS → MySQL 清洗结果回流（报表库数据刷新）
3. 跨集群 HDFS 数据搬迁（HDFS Reader → HDFS Writer，但需自定义）
4. 日志文件压缩归并（TEXT → ORC，压缩存储）
5. 云存储（OSS/S3）到 HDFS 的数据同步

### 4.4 注意事项

1. hdfswriter 的 `writeMode=truncate` 会清空目标目录下所有文件
2. ORC 文件写入后，需执行 Hive `MSCK REPAIR` 才能查询新分区
3. Kerberos 认证环境下 `defaultFS` 必须用 nameservice（如 `hdfs://ns-cluster`），不能用 IP
4. 不做 Kerberos 认证的测试环境，必须删除或注释 `hadoopConfig` 中的 security 参数
5. Parquet 格式的 column index 从 0 开始，和 Hive 习惯一致

### 4.5 思考题

1. 如果 DataFrame 的 ORC 文件中包含了 `array<int>` 和 `map<string, string>` 类型的列，DataX 如何读取这些复杂类型？hdfsreader 目前支持吗？
2. DataX hdfswriter 在"truncate"模式下，是每条 Task 独立 truncate 目标目录还是全局执行一次？如果 10 个 Task 并发执行 truncate，会发生什么？

（答案见附录）
