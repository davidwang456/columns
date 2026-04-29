# 第6章 MySQL Binlog深度解析与CDC配置

## 1 项目背景

### 业务场景：Binlog配置不当导致的CDC数据丢失

某电商公司使用Flink CDC做订单实时同步，某天凌晨运营反馈："大屏上的今日订单金额比数据库实际少了300万！"排查发现，Flink CDC作业在凌晨2:00~2:15期间没有捕获到任何变更事件。进一步调查发现，MySQL的Binlog刚好在凌晨2:00被自动清理了，而CDC作业因为Checkpoint延迟恰好在那段时间重启，重启后Binlog位点已丢失。

这就是**Binlog配置不当**引发的典型故障。Binlog是Flink CDC的"数据源头"，对Binlog的理解深度决定了CDC系统的可靠性。

### 痛点放大

| 问题 | 表现 | 根因 |
|------|------|------|
| Binlog被提前清理 | CDC作业恢复后读不到增量数据 | `expire_logs_days`设置过短 |
| server-id冲突 | CDC作业重复读/丢数据 | 多个CDC连接器用了相同server-id |
| binlog_format非ROW | Flink CDC无法解析事件类型 | MySQL默认可能是STATEMENT格式 |
| GTID未开启 | 主从切换后CDC断流 | GTID故障转移依赖未配置 |
| 大事务阻塞 | Binlog解析延迟飙升 | 单个事务修改了数百万行数据 |

本章将彻底讲透MySQL Binlog的原理，并给出Flink CDC场景下的最佳配置。

### Binlog在Flink CDC中的位置

```
┌────────────────────────────────────┐
│         MySQL Server              │
│  ┌────────────────────────┐       │
│  │   Storage Engine       │       │
│  │  (InnoDB/MyISAM)       │       │
│  └──────────┬─────────────┘       │
│             │                      │
│             ▼                      │
│  ┌────────────────────────┐       │
│  │    Binlog (二进制日志)    │       │
│  │  ROW格式 + GTID模式     │       │
│  │  /var/lib/mysql/mysql-bin.*  │  │
│  └──────────┬─────────────┘       │
│             │                      │
│    Dump线程  │  (模拟Slave拉取)     │
└─────────────┼──────────────────────┘
              │
              ▼
┌──────────────────────────────┐
│    Flink CDC MySqlSource     │
│  (Debezium Embedded Engine) │
│    解析Binlog Event →        │
│    DataChangeEvent           │
└──────────────────────────────┘
```

---

## 2 项目设计 · 三人交锋对话

**小胖**（挠头）：Binlog这东西我听说过，不就是MySQL的日志嘛。但为啥Flink CDC非要读它？直接查表不就行了吗？

**大师**：这个问题我在第二章回答过，但值得深入。Binlog是MySQL的"黑匣子"——它记录了所有数据变更的完整历史。直接查表只能看到"当前状态"，而Binlog能看到"每一步怎么变的"。比如你需要审计"这个订单是谁把价格从100改成50的"——直接查表只能看到当前价格50，Binlog才能告诉你改之前的100。

**小胖**：那Binlog有三种格式——ROW、STATEMENT、MIXED——Flink CDC为啥非要ROW？STATEMENT格式不是日志量更小吗？

**大师**：好问题！三种格式的本质区别在于"记录什么"：

| 格式 | 记录内容 | 日志大小 | Flink CDC兼容 |
|------|---------|---------|--------------|
| STATEMENT | 执行的SQL语句（如`UPDATE orders SET amount=100 WHERE id>1000`） | 小 | ❌ 无法确定影响了哪些行 |
| ROW | 每行变更的before/after镜像（如id=1001的amount从50→100） | 大 | ✅ 精确到行级 |
| MIXED | 自动切换：DDL用STATEMENT，DML用ROW | 中 | 部分兼容（需要ROW模式支持） |

STATEMENT格式的问题是：`UPDATE orders SET amount=100 WHERE id>1000`执行后，Binlog只记录了这条SQL，没有记录到底更新了哪些行。Flink CDC需要知道"哪几行变了"才能输出变更事件——所以必须用ROW格式。

**小白**（推眼镜）：那GTID呢？我查资料说GTID是Flink CDC高可用的关键。为什么有了`(filename, position)`还不够，非要GTID？

**大师**：`(filename, position)`是物理位点，GTID是逻辑位点。理解这个区别是关键：

在单机环境下，`(mysql-bin.000042, 12345)`这个坐标是精确的——它唯一标识了Binlog中的一个位置。

但是，如果发生了**主从切换**：
- 旧主库宕机，Binlog文件 `mysql-bin.000042` 在新主库上不存在
- 即使新主库有同名的文件，里面的内容也完全不同
- 用旧的`(filename, position)`去找新主库，要么找不到，要么找到错误的位置

GTID（Global Transaction Identifier）解决了这个问题：
- 每个事务分配一个全局唯一的ID：`a2b3c4d5-e6f7:42`（其中`a2b3c4d5-e6f7`是server_uuid，42是事务序号）
- GTID在整个复制拓扑中保持一致——主库和从库上同一事务的GTID相同
- 即使发生主从切换，Flink CDC可以根据GTID在新主库上定位到正确位置

**技术映射**：`(filename, position)`就像"街道地址 + 门牌号"——换了城市就找不到了。GTID就像"身份证号"——不管搬到哪个城市，你的身份不变。

**小白**：那server-id冲突又是怎么回事？我看Flink CDC文档里反复强调"每个CDC连接器必须用不同的server-id"。

**大师**：这涉及到MySQL主从复制的底层原理。当Flink CDC作为"模拟Slave"连接到MySQL时：
1. MySQL给每个连接的Slave分配一个server-id
2. Dump线程根据server-id来区分不同的Slave，并为每个Slave维护独立的Binlog发送状态
3. **如果两个Slave用了相同的server-id，MySQL会认为是一个Slave重连了，于是断开旧连接**

所以，如果你启动了2个Flink CDC作业监听同一个MySQL实例，但忘了配置不同的server-id，第二个作业启动时会把第一个作业的连接踢掉。更隐蔽的问题是：在同一作业内，如果有多个Source并行子任务（并行度>1），每个子任务也需要不同的server-id！

Flink CDC 3.0+自动处理了这个问题——你只需要配置`server-id: 5400-5409`（一个范围），Flink会为每个并行子任务自动分配唯一的ID。

---

## 3 项目实战

### 环境准备

使用第3章的Docker Compose环境。MySQL 8.0已开启Binlog。

### 分步实现

#### 步骤1：检查MySQL当前Binlog状态

```sql
-- 登录MySQL
docker exec -it mysql-cdc mysql -uroot -proot123

-- 查看Binlog核心参数
SHOW VARIABLES LIKE 'log_bin';
SHOW VARIABLES LIKE 'binlog_format';
SHOW VARIABLES LIKE 'binlog_row_image';
SHOW VARIABLES LIKE 'gtid_mode';
SHOW VARIABLES LIKE 'server_id';
SHOW VARIABLES LIKE 'expire_logs_days';

-- 查看当前Binlog文件列表
SHOW BINARY LOGS;
-- 预期输出:
-- +------------------+-----------+
-- | Log_name         | File_size |
-- +------------------+-----------+
-- | mysql-bin.000001 |      1234 |
-- | mysql-bin.000002 |    567890 |
-- +------------------+-----------+

-- 查看当前Binlog位置
SHOW MASTER STATUS;
-- 预期输出（包含GTID）：
-- +------------------+----------+--------------+------------------+------------------------------------------+
-- | File             | Position | Binlog_Do_DB | Binlog_Ignore_DB | Executed_Gtid_Set                       |
-- +------------------+----------+--------------+------------------+------------------------------------------+
-- | mysql-bin.000002 |      567 |              |                  | a2b3c4d5-e6f7:1-42                      |
-- +------------------+----------+--------------+------------------+------------------------------------------+
```

#### 步骤2：观察Binlog事件格式

```sql
-- 查看最近Binlog事件
SHOW BINLOG EVENTS IN 'mysql-bin.000002' LIMIT 10;

-- 使用mysqlbinlog工具查看ROW格式的具体内容
-- 退出MySQL，在宿主机上查看Binlog
```

在宿主机（或容器中）使用mysqlbinlog：

```bash
# 进入MySQL容器
docker exec -it mysql-cdc bash

# 使用mysqlbinlog解析Binlog（需要先安装）
mysqlbinlog --base64-output=DECODE-ROWS -v \
  /var/lib/mysql/mysql-bin.000002 | head -100
```

**输出示例（ROW格式的UPDATE事件）：**

```
# at 567
#240101 10:00:00 server id 1  end_log_pos 789 CRC32 0x12345678
# GTID [a2b3c4d5-e6f7:43]
# Table_id: 123
# Rows_query: UPDATE orders SET amount = 6499.00 WHERE id = 1001
### UPDATE `shop`.`orders`
### WHERE
###   @1=1001 /* INT */
###   @2='ORD20240101001' /* STRING */
###   @3=1 /* INT */
###   @4='iPhone 15' /* STRING */
###   @5=6999.00 /* DECIMAL */
###   @6='PAID' /* STRING */
### SET
###   @5=6499.00 /* DECIMAL */
```

注意ROW格式的输出中，`### WHERE`部分是UPDATE前的老数据，`### SET`部分是修改后的新数据——这就是Flink CDC `before`和`after`字段的源头。

#### 步骤3：演示server-id冲突的场景

**场景A（冲突）：** 启动两个使用相同server-id的Flink CDC作业

```java
// 作业1（默认server-id=5400）
MySqlSource<String> source1 = MySqlSource.<String>builder()
    .hostname("localhost").port(3306)
    .databaseList("shop").tableList("shop.orders")
    .username("cdc_user").password("cdc_pass")
    .deserializer(new JsonDebeziumDeserializationSchema())
    .serverId("5400") // 不指定server-id时，Flink CDC 3.0会自动生成
    .startupOptions(StartupOptions.latest())
    .build();

// 作业2（也用了5400——冲突！）
MySqlSource<String> source2 = MySqlSource.<String>builder()
    .hostname("localhost").port(3306)
    .databaseList("shop").tableList("shop.orders")
    .username("cdc_user").password("cdc_pass")
    .deserializer(new JsonDebeziumDeserializationSchema())
    .serverId("5400") // 与作业1冲突！
    .startupOptions(StartupOptions.latest())
    .build();
```

**结果：** 作业2启动后，MySQL会断开作业1的连接。作业1报错：`The slave is connecting using CHANGE MASTER TO ... but the same server id (5400)`

**场景B（正确）：** 使用server-id范围自动分配

```java
// 配置server-id范围（6400-6403），Flink自动为每个并行子任务分配
MySqlSource<String> source = MySqlSource.<String>builder()
    .hostname("localhost").port(3306)
    .databaseList("shop").tableList("shop.orders")
    .serverId("6400-6403") // 范围 = 起始server-id ~ 起始server-id + 并行度 - 1
    .build();
```

#### 步骤4：演示五种startup.mode的行为差异

编写测试程序验证不同启动模式：

```java
package com.example;

import org.apache.flink.cdc.connectors.mysql.source.MySqlSource;
import org.apache.flink.cdc.connectors.mysql.table.StartupOptions;
import org.apache.flink.cdc.debezium.JsonDebeziumDeserializationSchema;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;

/**
 * 演示scan.startup.mode的五种模式
 */
public class StartupModeDemo {

    public static void main(String[] args) throws Exception {
        String mode = args.length > 0 ? args[0] : "initial";

        StartupOptions options;
        switch (mode) {
            case "initial":
                // 模式1：initial（默认）
                // 先执行无锁全量快照，再无缝切换到增量Binlog
                // 适用于：首次上线、数据量大的表
                options = StartupOptions.initial();
                break;
            case "earliest":
                // 模式2：earliest-offset
                // 从最早的可用Binlog位置开始读取（可能丢失已清理的日志）
                // 适用于：需要从头开始捕获所有变更
                options = StartupOptions.earliest();
                break;
            case "latest":
                // 模式3：latest-offset
                // 只读取从启动时刻开始的新变更
                // 适用于：不需要历史数据、上下游水位对齐
                options = StartupOptions.latest();
                break;
            case "timestamp":
                // 模式4：specific-timestamp
                // 从指定时间戳之后的变更开始读取
                // 适用于：需要从特定时间点开始恢复
                options = StartupOptions.timestamp(1714377600000L);
                break;
            case "position":
                // 模式5：specific-offset
                // 从指定Binlog文件+位点开始读取
                // 适用于：精确断点续传（如从其他CDC工具切换过来）
                options = StartupOptions.specificOffset(
                    "mysql-bin.000002", 567, null);
                break;
            default:
                options = StartupOptions.initial();
        }

        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        env.enableCheckpointing(5000);

        MySqlSource<String> source = MySqlSource.<String>builder()
            .hostname("localhost").port(3306)
            .databaseList("shop").tableList("shop.orders")
            .username("cdc_user").password("cdc_pass")
            .deserializer(new JsonDebeziumDeserializationSchema())
            .startupOptions(options)
            .build();

        env.fromSource(source,
            org.apache.flink.api.common.eventtime.WatermarkStrategy.noWatermarks(),
            "MySQL CDC Source (" + mode + ")")
            .print();

        env.execute("StartupModeDemo - " + mode);
    }
}
```

**各模式对比总结：**

| 模式 | 全量快照 | 增量续接 | 适用场景 | 风险 |
|------|---------|---------|---------|------|
| `initial` | ✅ | ✅ | 首次上线、大表 | 快照时间长 |
| `earliest-offset` | ❌ | ✅（从头） | 审计全量历史 | Binlog可能已被清理 |
| `latest-offset` | ❌ | ✅（从现在） | 无需历史数据 | 启动前的变更丢失 |
| `timestamp` | ❌ | ✅（从指定时刻） | 定点恢复 | 指定时间点不在Binlog内会失败 |
| `specific-offset` | ❌ | ✅（从指定位点） | 其他工具切Flink CDC | 位点不在Binlog内会失败 |

#### 常见陷坑及解决方法

| 问题 | 原因 | 解决方法 |
|------|------|---------|
| Binlog文件占用大量磁盘 | `expire_log_days`设置过长或未设置 | 设置`expire_log_days=7`，监控磁盘使用率 |
| `Last event read from ... exceeds max_allowed_packet` | 单个Binlog事件过大（大事务） | 设置`max_allowed_packet=1G`，拆分大事务 |
| `The N most recent binlog files are still in use` | 有Slave延迟，Binlog无法清理 | 监控Slave延迟，增加`expire_log_days` |
| GTID与auto-position冲突 | 在启用GTID时使用了`(filename, position)`定位 | 使用`StartupOptions.specificOffset`时`gtidSet`参数设置正确 |

---

## 4 项目总结

### 优缺点对比：Binlog三种格式

| 格式 | 日志量 | CPU消耗 | 可读性 | CDC兼容 |
|------|-------|---------|-------|--------|
| STATEMENT | 最小 | 高（需要重放SQL） | 好 | ❌ |
| ROW | 大（通常是STATEMENT的10~20倍） | 低（直接记录行数据） | 二进制需解析 | ✅ |
| MIXED | 中 | 中 | 混合 | 部分兼容 |

### 推荐配置清单

```
[mysqld]
# 基础配置
server-id = 1                                    # 每个实例唯一
log-bin = mysql-bin                               # 开启Binlog
binlog-format = ROW                               # 必须ROW
binlog-row-image = FULL                           # 记录全行镜像
expire-log-days = 7                               # 保留7天

# GTID（强烈推荐）
gtid-mode = ON                                    # 开启GTID
enforce-gtid-consistency = ON                     # 强制GTID一致性

# 性能优化（可选）
binlog_cache_size = 4M                            # 事务缓存
max_binlog_size = 512M                            # 单个Binlog文件大小
binlog_order_commits = ON                         # 按顺序提交事务

# 安全（可选）
sync_binlog = 1                                   # 每次事务提交都刷盘
innodb_flush_log_at_trx_commit = 1                # InnoDB redo log同步刷盘
```

### 注意事项

1. **Disk Space Planning**：ROW格式Binlog增长迅速，每天约产生源数据1~2倍的Binlog。根据峰值吞吐量预留磁盘空间。
2. **expire_log_days与CDC Checkpoint的关系**：Binlog保留期必须大于Flink CDC作业的最大Checkpoint间隔 + 恢复时间，否则恢复时可能因Binlog被清理而失败。
3. **DDL语句的记录**：ROW格式下DDL仍然以STATEMENT格式记录，Flink CDC能正常捕获。但如果是`DROP TABLE`等危险操作，需要提前防护。

### 常见踩坑经验

**故障案例1：ALTER TABLE导致CDC作业失败**
- **现象**：执行`ALTER TABLE orders ADD COLUMN discount DECIMAL(5,2)`后，Flink CDC作业报错
- **根因**：Debezium在Schema变更后对事件结构的校验失败
- **解决方案**：升级Flink CDC到3.0+并设置`schema.change.behavior=EVOLVE`，自动处理Schema变更

**故障案例2：大事务导致CDC延迟15分钟**
- **现象**：某次批量UPDATE操作修改了500万行数据，单个Binlog事件达到2GB，Flink CDC处理该事件耗时15分钟
- **根因**：Debezium的`binlog_cache_size`和`max_allowed_packet`设置不足
- **解决方案**：设置`max_allowed_packet=1G`，优化分批执行（每批1000行），或使用Debezium的`event.processing.handoff.mode`

**故障案例3：MySQL 8.0 caching_sha2_password导致连接失败**
- **现象**：Flink CDC启动报错`Unable to connect to MySQL`，但账号密码正确
- **根因**：MySQL 8.0默认认证插件为`caching_sha2_password`，JDBC驱动7.x以下不支持
- **解决方案**：使用MySQL JDBC 8.0.30+驱动，或创建用户时指定`IDENTIFIED WITH mysql_native_password BY 'pass'`

### 思考题

1. **进阶题①**：假设MySQL的Binlog保留7天，但Flink CDC作业因故障停止了10天才恢复。此时Binlog已经被清理。如果不重新全量快照，有什么办法能让Flink CDC从当前时刻恢复？提示：结合`latest()`模式和`scan.newly-added-table.enabled`配置。

2. **进阶题②**：MySQL在ROW格式下，`UPDATE`只修改了1列，但Binlog中会记录所有列的before和after。有什么办法可以减少Binlog网络传输量？提示：研究`binlog_row_image=MINIMAL`和`DebeziumColumnFilter`的配合使用。

---

> **下一章预告**：第7章「深入理解Event模型」——我们将深入Flink CDC的事件体系，剖析`DataChangeEvent`（数据变更事件）和`SchemaChangeEvent`（Schema变更事件）的结构，并通过自定义处理理解Flink CDC的内部数据流转。
