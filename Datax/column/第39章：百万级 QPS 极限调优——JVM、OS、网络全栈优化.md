# 第39章：百万级 QPS 极限调优——JVM、OS、网络全栈优化

## 1. 项目背景

某头部互联网公司的实时数据仓库团队承担着一项"不可能任务"：每天凌晨 2:00~4:00 的 2 小时窗口内，完成 100 亿行 MySQL 核心交易表到另一套 MySQL 分析库的全量同步。源库部署在 64 核 128GB 物理机上，目标库配置相当。团队用 DataX Standalone 模式，经过基础调优（channel=32、batchSize=8192、G1GC、fetchSize=MIN_VALUE），同步速度稳定在 420,000 rec/s——但这个速度跑完全量需要 6.6 小时，远超出 2 小时 SLA。

CTO 下了死命令：**必须在当前硬件下达到 100 万 QPS**（1,000,000 rec/s），否则就需要采购 8 倍硬件——预算高达 200 万。

架构组启动了"百万 QPS 攻坚专项"——从 JVM 内部（DirectBuffer、GC 选型、JIT 预热）到 OS 内核参数（文件描述符上限、TCP 队列、内存交换），再到网络协议栈（JDBC 参数优化、零拷贝），最后用 perf + FlameGraph 做全栈性能剖析。经过两周的系统化调优和 47 次压测，最终在 64C128G 的单机上将 QPS 从 420K 推到了 1,140,000 rec/s——提升了 2.7 倍，成功在 2 小时内完成全量同步。

本章将公开这条完整的"百万 QPS 调优路径"——从 JVM 底层到 OS 内核再到网络协议栈，每一步都有基准测试数据和调优原理。

## 2. 项目设计——剧本式交锋对话

**（周三凌晨 2:30，数据仓库作战室，大屏幕实时显示着当前 DataX 的 QPS：418,723）**

**小胖**：（盯着屏幕上几乎水平的 QPS 曲线）这曲线也太稳了——稳稳地达不到 100 万。420K 已经是 CPU 80% 了，再加 channel 就 100% 了，感觉硬件到头了啊？

**小白**：（敲着键盘拉出一张 perf top 输出）不对。你看 perf top——CPU 前三个热点是 `ArrayBlockingQueue.poll()`、`PreparedStatement.setString()` 和 `G1ParScanThreadState::copy_to_survivor_space`。第三个是 GC 的——读写线程的 CPU 被 GC 线程抢走了。

**大师**：（在白板上画了一条水平线）420K 不是硬件的极限，是当前 **JVM 配置和 OS 参数** 的极限。我们要做的不是"加 CPU"，而是重启一个思路——**全栈调优**。从下往上改四个层次：

```
应用层     batchSize / channel / fetchSize / Transformer
JVM 层     GC选型 / DirectBuffer / JIT编译 / 堆大小
OS 层      ulimit / somaxconn / tcp_tw_reuse / swappiness
网络层     JDBC参数 / socketTimeout / useCompression / sendfile
```

**技术映射**：全栈调优 = F1 赛车的调校。应用层调的是"引擎马力和变速箱齿比"（业务配置），JVM 层调的是"ECU 电子控制系统"（内存管理），OS 层调的是"轮胎气压和悬挂硬度"（系统资源），网络层调的是"空气动力学套件"（数据传输路径）。任何一层短板，圈速都上不去。

---

**小胖**：（挠头）我们先从哪里下手？

**大师**：从最底层——JVM 的 GC 和内存模型。

**第一步：JVM GC 选型——从 G1GC 换到 ZGC**

G1GC 在 128GB 大堆下有一个致命弱点：Mixed GC 的并发标记阶段需要扫描整个堆，128GB 的扫描线程和业务线程抢 CPU，导致 QPS 周期性跌 30%。ZGC 和 ShenandoahGC 实现了"并发标记 + 并发整理"，GC 停顿与堆大小无关，稳定在 1ms 以内。

我们的测试数据：

| GC | 堆大小 | 平均 QPS | QPS 抖动幅度 | 最大 GC 停顿 |
|-----|--------|---------|-------------|-------------|
| G1GC | 96GB | 420,000 | ±18%（周期性掉速） | 1,230ms |
| ShenandoahGC | 96GB | 680,000 | ±5% | 8ms |
| ZGC | 96GB | 720,000 | ±3% | 1.2ms |

ZGC 的缺点是 CPU 开销比 G1GC 高约 10%（用于并发标记的染色指针），但在数据同步这种对停顿敏感、对吞吐更敏感的场景——这 10% 的 CPU 换 1ms 停顿 + 零 QPS 抖动，非常值。

JVM 启动参数：
```bash
-XX:+UseZGC -XX:ZCollectionInterval=0 -XX:+ZGenerational
-Xms64g -Xmx96g -XX:SoftMaxHeapSize=80g
```

**小白**：（追问）ZGC 的染色指针（Colored Pointers）是什么意思？为什么它能做到 1ms 停顿？

**大师**：ZGC 在 64 位指针中偷了 4 位做标记（Finalizable、Remapped、Marked0、Marked1），用这 4 位来实现并发标记期间的"读屏障"——当业务线程读到被移动的对象时，读屏障自动修正引用，不需要 Stop-The-World。细节可以参考 OpenJDK JEP 333。

---

**第二步：JVM DirectBuffer 优化**

**小胖**：DirectBuffer 是什么？跟 DataX 有什么关系？

**大师**：DataX 的 JDBC Reader 在读取大字段（TEXT/BLOB）时，JDBC 驱动可能用 DirectBuffer（堆外内存）来暂存数据，避免大对象直接进堆触发 GC。但如果 `-XX:MaxDirectMemorySize` 设太小（如默认等于 Xmx），DirectBuffer 满了也会 OOM。

```
配置: -XX:MaxDirectMemorySize=16g
原因: DataX 的大字段读取使用堆外内存，16GB 足够缓冲 32 个 channel × 8192 batch × 2KB/record 的峰值数据
```

**第三步：JIT 编译预热**

DataX 的热路径代码（`ArrayBlockingQueue.poll()`、`PreparedStatement.setString()`、`ResultSet.getString()`）在 JVM 启动初始是解释执行的。前 5 分钟 JIT 还在编译这些方法，CPU 都在编译线程上——QPS 前 5 分钟只有峰值的 50%。

```
配置: -XX:+PrintCompilation -XX:CompileThreshold=2000
      -XX:ReservedCodeCacheSize=512m
      -XX:+TieredCompilation -XX:TieredStopAtLevel=3
```

或者更激进——直接写一个"预热 Job"：在正式跑之前，先用一个小数据量（100 万行）的 StreamReader→StreamWriter 跑 2 分钟，JVM 在此期间完成 JIT 编译，然后正式 Job 上来就是满速。

**技术映射**：JIT 编译 = 翻译官现场做同声传译。前 5 分钟还在查字典（解释执行），查完了就开始流利翻译（编译执行）。预热 Job = 提前给翻译官 5 分钟预习演讲稿。

---

**第四步：OS 内核参数——从 Linux 默认到生产级**

**小白**：（翻出自己的《Linux 性能优化》笔记）ulimit、TCP 参数，这些我也记得一些，但具体给 DataX 怎么配？

**大师**：四个关键参数：

**（1）文件描述符上限 `ulimit -n`**

DataX 的每个 Channel = 1 个 JDBC Reader 连接 + 1 个 Writer 连接 = 2 个 socket。channel=40 就是 80 个 socket，再加 16 个 Channel 内部的 ArrayBlockingQueue 需要少量 fd——保守 1024 起步，建议 65536：

```bash
ulimit -n 65536
# 持久化: /etc/security/limits.conf
# * soft nofile 65536
# * hard nofile 65536
```

**（2）TCP 连接队列 `net.core.somaxconn`**

MySQL JDBC 的 TCP 三次握手完成后，连接进入 accept queue。如果队列满了，新连接收到 SYN 后会被丢弃——表现为"偶发的连接超时"。DataX 在启动瞬间会建立大量 JDBC 连接（channel 数 × 2），扩容队列防止丢连接：

```bash
sysctl -w net.core.somaxconn=4096
```

**（3）TIME_WAIT 复用 `tcp_tw_reuse`**

每次 JDBC 连接关闭后进入 TIME_WAIT 状态（60 秒），大量短连会导致端口耗尽。开启 reuse 复用 TIME_WAIT 端口：

```bash
sysctl -w net.ipv4.tcp_tw_reuse=1
sysctl -w net.ipv4.tcp_fastopen=3
```

**（4）内存换出 `vm.swappiness`**

DataX 的 JVM 堆设为 96GB，OS 只剩 32GB。如果 Linux 觉得内存不够，会把 JVM 的页换到 swap——一旦发生，QPS 直接掉 90%。所以 DataX 机器上应该把 swappiness 设为 1（尽可能不 swap）：

```bash
sysctl -w vm.swappiness=1
```

---

**第五步：网络层——JDBC URL 优化**

**小胖**：JDBC URL 还能调？不是填个 IP 端口就行了吗？

**大师**：（在屏幕上投影出完整的 JDBC URL 参数表）

```bash
# 完整优化后的 MySQL JDBC URL
jdbc:mysql://10.0.1.100:3306/bench?
  useSSL=false&                          # 内网无需SSL
  useCompression=true&                   # 启用协议压缩（Reader拉数据时，MySQL服务端压缩后传输）
  useCursorFetch=true&                   # 游标模式
  defaultFetchSize=-2147483648&          # 流式读取
  useServerPrepStmts=false&              # 不用服务端预处理（批量insert场景客户端预处理更快）
  rewriteBatchedStatements=true&         # 重写批量SQL为单条多VALUES
  cachePrepStmts=true&                   # 客户端缓存PreparedStatement
  prepStmtCacheSize=256&                 # 缓存256条
  prepStmtCacheSqlLimit=2048&            # SQL长度限制
  useLocalSessionState=true&             # 不查询服务端状态（减少一次往返）
  socketTimeout=60000&                   # socket超时60秒
  connectTimeout=15000&                  # 连接超时15秒
  netTimeoutForStreamingResults=0&       # 流式读取无超时
  characterEncoding=utf8mb4&
  serverTimezone=Asia/Shanghai
```

每个参数的意义：

| 参数 | 作用 | 收益 |
|------|------|------|
| `useCompression=true` | MySQL 协议层压缩 | Reader 端网络流量降 60% |
| `useLocalSessionState=true` | 跳过服务端状态查询 | 每条连接减少 1 次 RTT |
| `rewriteBatchedStatements=true` | N 条 INSERT 合并为 1 条 | Writer 端 RTT 减少 N-1 次 |
| `cachePrepStmts=true` | 客户端 PS 缓存 | 避免每次重新解析 SQL |
| `socketTimeout=60000` | 60s 超时 | 防止死连接永久阻塞 |

**零拷贝——HDFS Writer 中的 sendfile**

如果目标端是 HDFS，HDFS Writer 可以使用 `sendfile` 系统调用实现零拷贝——数据从磁盘到 socket 不经过用户态内存：

```java
// HDFS Writer 底层: java.nio.channels.FileChannel.transferTo()
// 内部调用 sendfile()，数据路径: Disk → Kernel Buffer → Socket Buffer → NIC
// 绕过: Disk → Kernel Buffer → User Buffer(DataX) → Kernel Buffer → Socket Buffer
```

MySQL Writer 不支持 sendfile（因为 MySQL 协议是应用层协议），但 `useCompression=true` 相当于在协议层降低了字节量，间接实现了"少传输"。

---

**第六步：火焰图分析——找出 CPU 真实热点**

**小胖**：perf + FlameGraph 是什么？听起来很高级。

**大师**：（打开一台 Linux 测试机）perf 是 Linux 内核自带的性能分析工具，FlameGraph 是 Brendan Gregg 开源的可视化脚本。步骤很简单：

```bash
# 1. 记录 DataX 进程的 CPU 采样（PID=12345, 采样 30 秒）
perf record -F 99 -p 12345 -g --call-graph dwarf -- sleep 30

# 2. 生成火焰图
perf script > out.perf
git clone https://github.com/brendangregg/FlameGraph.git
FlameGraph/stackcollapse-perf.pl out.perf > out.folded
FlameGraph/flamegraph.pl out.folded > flamegraph.svg

# 3. 用浏览器打开 flamegraph.svg
```

火焰图的读法：X 轴是 CPU 采样占比，Y 轴是调用栈深度。宽度越宽的函数，CPU 占比越高。优化就盯着"最宽的平顶"下手。

我们最初的火焰图 top 3 热点：
1. **`ArrayBlockingQueue.poll()` —— 18% CPU**：Channel push/pull 频繁阻塞
   - 优化方案：增大 MemoryChannel 的 capacity（从默认 128 到 2048），减少阻塞次数
2. **`PreparedStatement.setString()` —— 14% CPU**：每次 setString 都需要 UTF-8 编码
   - 优化方案：如果字段都是 ASCII，Writer 端关闭 `characterEncoding=utf8`，使用 `latin1`
3. **`HashMap.get()` —— 9% CPU**：Column 名称到索引的查找
   - 优化方案：预计算好 Column 顺序的数组，避免每次查 HashMap

**结果**：这三项优化将 QPS 从 720K 进一步提升到 1,140,000。

---

## 3. 项目实战

### 3.1 步骤一：压测环境准备

**目标**：搭建 64C128G 物理机，生成 100 亿行测试数据，建立调优基线。

**硬件配置**：

| 组件 | 配置 |
|------|------|
| CPU | Intel Xeon Platinum 8380 @ 2.3GHz, 64 核 128 线程 |
| 内存 | 128GB DDR4-3200 ECC |
| 磁盘 | Intel Optane P5800X 1.6TB NVMe |
| 网络 | Mellanox ConnectX-6 100GbE |
| OS | CentOS Stream 9, Kernel 5.14 |

**MySQL 配置调优**（`/etc/my.cnf` 关键部分）：

```ini
[mysqld]
# InnoDB
innodb_buffer_pool_size = 80G
innodb_log_file_size = 8G
innodb_flush_log_at_trx_commit = 2
innodb_flush_method = O_DIRECT
innodb_io_capacity = 20000
innodb_io_capacity_max = 40000
innodb_read_io_threads = 16
innodb_write_io_threads = 16

# Connection
max_connections = 2000
max_connect_errors = 10000

# Network
max_allowed_packet = 256M
net_write_timeout = 600
net_read_timeout = 600

# Disable binlog for benchmark
# skip-log-bin
# sync_binlog = 0
```

**生成 100 亿行测试数据**：

```sql
CREATE DATABASE IF NOT EXISTS extreme_bench;
USE extreme_bench;

CREATE TABLE orders_10b (
    order_id BIGINT PRIMARY KEY AUTO_INCREMENT,
    user_id BIGINT NOT NULL,
    amount DECIMAL(10,2) NOT NULL,
    status TINYINT,
    create_time DATETIME NOT NULL,
    update_time DATETIME NOT NULL,
    INDEX idx_user (user_id),
    INDEX idx_time (create_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 分 100 批，每批 1 亿行
-- 使用 LOAD DATA INFILE 比 INSERT 快 5~10 倍
-- 生产环境也可以用 DataX StreamReader → MySQL Writer 灌数据
```

### 3.2 步骤二：调优前的基线测试（420K QPS）

**目标**：用"中级篇级别"的调优参数跑一次，记录基线。

**DataX 配置**（`baseline_10b.json`）：

```json
{
    "job": {
        "content": [{
            "reader": {
                "name": "mysqlreader",
                "parameter": {
                    "username": "bench",
                    "password": "bench",
                    "column": ["*"],
                    "splitPk": "order_id",
                    "fetchSize": -2147483648,
                    "connection": [{
                        "table": ["orders_10b"],
                        "jdbcUrl": ["jdbc:mysql://localhost:3307/bench?useSSL=false&useCursorFetch=true"]
                    }]
                }
            },
            "writer": {
                "name": "mysqlwriter",
                "parameter": {
                    "username": "bench",
                    "password": "bench",
                    "writeMode": "insert",
                    "column": ["*"],
                    "preSql": ["TRUNCATE TABLE orders_10b_bak"],
                    "batchSize": 8192,
                    "session": ["SET unique_checks=0","SET foreign_key_checks=0"],
                    "connection": [{
                        "table": ["orders_10b_bak"],
                        "jdbcUrl": ["jdbc:mysql://localhost:3308/bench?useSSL=false&rewriteBatchedStatements=true"]
                    }]
                }
            }
        }],
        "setting": {
            "speed": {"channel": 40}
        }
    }
}
```

**JVM 参数**（中级篇级别）：

```bash
-Xms8g -Xmx96g -XX:+UseG1GC -XX:MaxGCPauseMillis=200
```

**基线结果**：

```
Total records: 10,000,000,000
Total bytes: 4,380,000,000,000 (3,980 GB)
Total time: 396m 48s (6h 36m)
Average speed: 420,168 rec/s
Average byte speed: 178 MB/s

CPU: avg 82%, peak 94%
Heap: avg 64GB, peak 88GB
GC Pause: avg 638ms, max 4,230ms (!)
Network RX: 185 MB/s, TX: 180 MB/s
```

### 3.3 步骤三：JVM 全栈优化（720K QPS）

**目标**：替换 GC 为 ZGC，优化 DirectBuffer，添加 JIT 预热。

**JVM 参数（高级篇级别）**：

```bash
java -server \
  -Xms64g -Xmx96g \
  -XX:SoftMaxHeapSize=80g \
  -XX:+UseZGC \
  -XX:+ZGenerational \
  -XX:ZCollectionInterval=0 \
  -XX:ConcGCThreads=8 \
  -XX:MaxDirectMemorySize=16g \
  -XX:ReservedCodeCacheSize=512m \
  -XX:+TieredCompilation \
  -XX:TieredStopAtLevel=3 \
  -XX:CompileThreshold=2000 \
  -XX:+DisableExplicitGC \
  -XX:+UseNUMA \
  -XX:+UseTransparentHugePages \
  -Ddatax.home=/opt/datax \
  -classpath "/opt/datax/lib/*" \
  com.alibaba.datax.core.Engine \
  -mode standalone -jobid -1 -job baseline_10b.json
```

**ZGC 结果**：

```
Total time: 233m 10s (3h 53m)
Average speed: 715,043 rec/s
Average byte speed: 302 MB/s

CPU: avg 88%, peak 97%
Heap: avg 52GB, peak 78GB
GC Pause: avg 0.8ms, max 2.1ms (!)
```

**关键变化**：
- GC 停顿从 4.2 秒降到 2.1 毫秒——差了 2000 倍
- 但 CPU 利用率从 82% 升到 88%（ZGC 的并发标记消耗更多 CPU）
- QPS 抖动从 ±18% 降到 ±3%，曲线几乎是一条直线

### 3.4 步骤四：OS 内核参数优化（840K QPS）

**目标**：调整 Linux 内核参数，释放 OS 层面的性能瓶颈。

**内核参数调节脚本**（`tune_os.sh`）：

```bash
#!/bin/bash
# OS Tuning for DataX Extreme Performance

# ---- 文件描述符 ----
ulimit -n 65536
cat >> /etc/security/limits.conf << 'EOF'
* soft nofile 65536
* hard nofile 65536
* soft nproc 65536
* hard nproc 65536
EOF

# ---- TCP/IP 栈 ----
sysctl -w net.core.somaxconn=4096
sysctl -w net.ipv4.tcp_max_syn_backlog=8192
sysctl -w net.ipv4.tcp_tw_reuse=1
sysctl -w net.ipv4.tcp_fastopen=3
sysctl -w net.ipv4.tcp_slow_start_after_idle=0
sysctl -w net.ipv4.tcp_rmem="4096 87380 134217728"
sysctl -w net.ipv4.tcp_wmem="4096 65536 134217728"

# ---- 内存 & Swap ----
sysctl -w vm.swappiness=1
sysctl -w vm.dirty_ratio=15
sysctl -w vm.dirty_background_ratio=5
sysctl -w vm.zone_reclaim_mode=0

# ---- 透明大页 ----
echo always > /sys/kernel/mm/transparent_hugepage/enabled
echo defer+madvise > /sys/kernel/mm/transparent_hugepage/defrag

# ---- 中断亲和性（可选，绑定网卡中断到特定 CPU） ----
# systemctl stop irqbalance
# echo 2 > /proc/irq/<网卡IRQ>/smp_affinity

echo "OS tuning completed. Verify with: sysctl -a | grep -E 'tcp_tw_reuse|somaxconn|swappiness'"
```

**OS 调优后结果**：

```
Total time: 198m 05s (3h 18m)
Average speed: 841,237 rec/s
Average byte speed: 356 MB/s

Network RX: 365 MB/s, TX: 360 MB/s
Connection errors: 0
Port exhaustion events: 0
```

**关键变化**：
- TCP 连接建连更稳定：`somaxconn=4096` 让连接队列容量提升 32 倍
- 无端口耗尽：`tcp_tw_reuse=1` 让 TIME_WAIT 端口可以立即复用
- Swap 零触发：`swappiness=1` 确保 JVM 堆永远不被换出

### 3.5 步骤五：网络层 & JDBC 极限优化（1,140K QPS）

**目标**：优化 JDBC URL 全部参数，启用 useCompression，增大 Channel capacity。

**完整优化后的 DataX JSON**：

```json
{
    "job": {
        "content": [{
            "reader": {
                "name": "mysqlreader",
                "parameter": {
                    "username": "bench",
                    "password": "bench",
                    "column": ["*"],
                    "splitPk": "order_id",
                    "fetchSize": -2147483648,
                    "connection": [{
                        "table": ["orders_10b"],
                        "jdbcUrl": ["jdbc:mysql://localhost:3307/bench?useSSL=false&useCompression=true&useCursorFetch=true&useLocalSessionState=true&useLocalTransactionState=true&cachePrepStmts=true&prepStmtCacheSize=256&prepStmtCacheSqlLimit=2048&socketTimeout=120000&connectTimeout=15000&netTimeoutForStreamingResults=0&characterEncoding=latin1&serverTimezone=UTC"]
                    }]
                }
            },
            "writer": {
                "name": "mysqlwriter",
                "parameter": {
                    "username": "bench",
                    "password": "bench",
                    "writeMode": "insert",
                    "column": ["*"],
                    "preSql": ["TRUNCATE TABLE orders_10b_bak"],
                    "batchSize": 16384,
                    "session": [
                        "SET unique_checks=0",
                        "SET foreign_key_checks=0",
                        "SET autocommit=0",
                        "SET sql_log_bin=0"
                    ],
                    "connection": [{
                        "table": ["orders_10b_bak"],
                        "jdbcUrl": ["jdbc:mysql://localhost:3308/bench?useSSL=false&rewriteBatchedStatements=true&useCompression=true&useLocalSessionState=true&cachePrepStmts=true&prepStmtCacheSize=256&socketTimeout=120000&connectTimeout=15000&characterEncoding=latin1&serverTimezone=UTC"]
                    }]
                }
            }
        }],
        "setting": {
            "speed": {
                "channel": 64
            }
        }
    }
}
```

**同时修改 MemoryChannel 源码**——将 capacity 从默认 128 调大到 2048：

```java
// MemoryChannel.java 修改
public class MemoryChannel extends Channel {
    // 原始: private ArrayBlockingQueue<Record> queue = new ArrayBlockingQueue<>(128);
    // 修改为可配置:
    private static final int DEFAULT_CAPACITY = 
        Integer.parseInt(System.getProperty("datax.channel.capacity", "2048"));
    private ArrayBlockingQueue<Record> queue = new ArrayBlockingQueue<>(DEFAULT_CAPACITY);
}
```

**极限优化后结果**：

```
Total records: 10,000,000,000
Total bytes: 4,380,000,000,000 (3,980 GB)
Total time: 146m 12s (2h 26m !!!)
Average speed: 1,140,251 rec/s ✓
Average byte speed: 483 MB/s

CPU: avg 92%, peak 99%
Heap: avg 46GB, peak 72GB
GC Pause: avg 0.9ms, max 3.2ms
Network RX: 496 MB/s, TX: 490 MB/s
Channel utilization: avg 87%, peak 98%
Connection errors: 0
Dirty records: 0
```

**分段提升对比**：

| 调优阶段 | QPS(rec/s) | 耗时 | 累计提升 | CPU(%) | GC Pause(max) |
|---------|-----------|------|---------|--------|---------------|
| 基线(G1GC, 默认OS) | 420,168 | 6h 36m | 基准 | 82% | 4,230ms |
| JVM: ZGC + DirectBuffer | 715,043 | 3h 53m | 1.70x | 88% | 2.1ms |
| OS: 内核参数 | 841,237 | 3h 18m | 2.00x | 91% | 2.8ms |
| 网络: JDBC + Channel | 1,140,251 | 2h 26m | **2.71x** | 92% | 3.2ms |

### 3.6 步骤六：生成 CPU 火焰图验证

```bash
# 1. 在 DataX 运行期间采样（PID 已在运行）
perf record -F 99 -p $(pgrep -f DataX) -g --call-graph dwarf -- sleep 60

# 2. 生成火焰图
perf script > datax_1m_qps.perf
FlameGraph/stackcollapse-perf.pl datax_1m_qps.perf > datax_1m_qps.folded
FlameGraph/flamegraph.pl --title "DataX @ 1.14M QPS" datax_1m_qps.folded > datax_flamegraph.svg

# 3. 火焰图分析结果：
# Top 3 热点（优化前）:
#   [1] ArrayBlockingQueue.poll()      18.2% → 优化后: 6.8%
#   [2] PreparedStatement.setString()  14.1% → 优化后: 9.2%
#   [3] HashMap.get()                   9.3% → 优化后: 2.1%
#
# Top 3 热点（优化后）:
#   [1] MySQL JDBC readPacket()        12.3% (网络IO，不可优化)
#   [2] InnoDB row_search_mvcc()       10.8% (MySQL内部，不可优化)
#   [3] PreparedStatement.executeBatch() 8.5% (写入提交，不可优化)
#
# 结论: 优化后 top 3 热点都是 I/O 和 MySQL 内部函数——当前硬件已达软件上限。
```

### 3.7 可能遇到的坑及解决方法

**坑1：ZGC 在 JDK 11 上不是默认 GC，需要 JDK 17+**

DataX 官方支持 JDK 8，但 ZGC 从 JDK 15 起生产可用。需要升级 JDK。

```
解决: 使用 JDK 17 LTS，DataX 3.x 实测兼容 JDK 17
      如果必须 JDK 8，使用 ShenandoahGC（JDK 8u262+ 支持）
      启动参数: -XX:+UseShenandoahGC -XX:ShenandoahGCHeuristics=compact
```

**坑2：`useCompression=true` 增加 MySQL 服务端 CPU**

压缩和解压缩消耗 MySQL 服务端 CPU（约 5~10%）。如果 MySQL 已经是 CPU 瓶颈，启用压缩反而导致读写变慢。

```
解决: 先检查 MySQL 的 CPU 利用率
      如果 MySQL CPU < 60%: 启用 useCompression（收益 > 成本）
      如果 MySQL CPU > 80%: 关闭 useCompression（成本 > 收益）
```

**坑3：`characterEncoding=latin1` 导致中文字段乱码**

优化建议中将编码设为 latin1 仅对纯 ASCII 数据有效（如订单号、金额）。中文内容字段不能设为 latin1。

```
解决: 分列设置——对中文列保留 utf8mb4，对 ASCII 列用 latin1
      或在 Writer 端做一次 Groovy Transformer 验证编码正确性
```

**坑4：perf 火焰图缺失 Java 符号**

perf 只能看到 native 函数调用栈，Java 方法调用栈默认不在 perf 的输出中。

```
解决: 使用 async-profiler (https://github.com/async-profiler/async-profiler)
      ./profiler.sh -d 30 -f flamegraph.html <PID>
      async-profiler 直接采样 Java 方法的 CPU 时间，比 perf 更适合 JVM 分析
```

## 4. 项目总结

### 4.1 百万 QPS 调优参数速查

| 层级 | 参数 | 默认/基准值 | 优化值 | 收益 |
|------|------|-----------|--------|------|
| JVM | GC | G1GC | ZGC (JDK 17) | GC 停顿 ↓ 99.9% |
| JVM | Xmx | 8g | 96g | 堆容量 ↑ 12x |
| JVM | MaxDirectMemorySize | 无限制 | 16g | 防止堆外 OOM |
| JVM | JIT预热 | 无 | TieredStopAtLevel=3 | 冷启动 ↑ 40% |
| OS | ulimit -n | 1024 | 65536 | 解决 Too many open files |
| OS | somaxconn | 128 | 4096 | TCP 建连零拒绝 |
| OS | tcp_tw_reuse | 0 | 1 | 消除 TIME_WAIT 端口耗尽 |
| OS | swappiness | 60 | 1 | 禁止 JVM 堆被 swap |
| 网络 | useCompression | false | true | 网络字节量 ↓ 60% |
| 网络 | rewriteBatchedStatements | false | true | Writer 写入 RTT ↓ N-1 |
| 网络 | cachePrepStmts | false | true | 避免 SQL 重复解析 |
| 应用 | batchSize | 1024 | 16384 | 单次提交行数 ↑ 16x |
| 应用 | channel | 5 | 64 | 并发度 ↑ 12.8x |
| 应用 | Channel capacity | 128 | 2048 | 阻塞率 ↓ 70% |

### 4.2 优点

1. **系统化路径可复现**：JVM → OS → 网络 → 应用四层渐进式调优，每层独立验证
2. **火焰图驱动**：每次调优后生成火焰图，确认 CPU 热点从"内部开销"迁移到"有效 IO"
3. **数据量化**：每个参数的改动都有 QPS 增量，不存在"玄学调优"
4. **2.71 倍提升**：从 420K 到 1.14M QPS，零硬件成本投入

### 4.3 缺点

1. **JDK 版本要求**：ZGC 需要 JDK 17+，与 DataX 的 JDK 8 默认环境不兼容
2. **环境耦合强**：内核参数优化只在 Linux 有效，Windows/Mac 下无对应参数
3. **MySQL 服务端承担更多**：useCompression 和服务端预处理会增加 MySQL 的 CPU 开销
4. **运维门槛高**：ZGC 的 JVM 参数、内核参数、JDBC URL 优化——每一项都需要深度理解，不能盲目复制

### 4.4 注意事项

1. **生产环境先 in-place 测试**：OS 内核参数在生产机器上改之前，在相同硬件的测试机验证
2. **`SET sql_log_bin=0` 禁用 binlog**——确保数据已通过其他方式备份，否则无法回滚
3. **JDBC `characterEncoding=latin1` 的副作用**——中文字段会被截断或乱码，仅适用于纯 ASCII 场景
4. **不要同时调所有参数**——否则无法定位哪一个参数起了负效果

### 4.5 思考题

1. 如果 MySQL 源库和目标库不在同一机房（延迟 10ms RTT），useCompression=true 是否仍然有效？为什么？如何调整 JDBC 参数来应对高延迟链路？
2. 火焰图中显示了 `InnoDB row_search_mvcc()` 占了 10.8% CPU——这是 MySQL 内部的函数。作为 DataX 开发者，你无法修改 MySQL 源码，但你可以在 MySQL 层做什么优化来降低这个函数的热度？（提示：考虑索引覆盖、Buffer Pool 调优）

（答案见附录）
