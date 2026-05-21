# 第39章：百万 QPS 极致优化与 QUIC/HTTP3

> **版本**：ClickHouse 25.x LTS
> **定位**：高级篇——从 1 万 QPS 到 100 万 QPS 的工程突破，逐层拆解网络协议、序列化、连接管理、操作系统与硬件的优化瓶颈。
> **前置阅读**：第2章（单机部署与客户端生态）、第16章（连接池与并发控制）
> **预计阅读**：40 分钟 | **实战耗时**：60 分钟

---

## 1. 项目背景

某广告技术公司的实时竞价（RTB）引擎扛着每秒 **50 万次** 的点查请求，每一条请求都走 `WHERE user_id = ? AND campaign_id = ?` 对 ClickHouse 做点查询。当前部署在一台 64 核、256GB 内存、NVMe 盘的裸金属节点上，压测跑到 **15 万 QPS** 就再也上不去了。而竞争对手在同等硬件规格下跑到了 **50 万 QPS**。

运维团队起初的判断很朴素："加实例嘛，一台 15 万，四台不就 60 万了？"可财务同事把 TCO 一算——单节点年成本 20 万，四台就是 80 万，而竞品一套就搞定。更关键的是，横向扩展在这个场景下并不干净——广告竞价请求需要在一个节点内完成全量匹配，跨节点查询引入的网络 RTT 延迟（0.5-2ms）在 50 万 QPS 的量级下，足以让竞价超时率的 SLA（<5ms P99）直接炸掉。

团队开始逐层审视：网络协议的 HTTP 头开销有多大？序列化格式的 CPU 消耗能不能省？TCP 连接的三次握手能否绕过？操作系统调度器是不是把查询线程踢来踢去？NUMA 拓扑下跨节点访存是不是在悄悄吃掉宝贵纳秒（微秒）？这些问题在低 QPS 场景下永远不是瓶颈——10 万 QPS 以下，随便调几个参数就有显著提升。但当每一个微秒都开始被计入账单的时候，这些"小事"就一一浮出水面，成为高悬的天花板。

这一章的目标就是把从网络到 CPU、从序列化到内存的每一层天花板全部掀开，用可复现的 benchmark 数据记录每一次优化带来的提升，最终让单节点逼近百万 QPS 的极限。

---

## 2. 项目设计：剧本式交锋对话

周五下午，大师把台 64 核压测机连上了 Grafana 大屏，一条曲线在 15 万 QPS 附近被死死压平，任凭 `wrk` 加多少并发都纹丝不动。小胖咬着零食进来了。

**小胖**（盯着 Grafana 那条压平的线）："大师，这曲线也太直了吧？15 万 QPS 就到头了？加并发没用吗？CPU 才跑 40%，网络带宽才用了 2Gb，怎么就饱和了？"

**大师**（敲了敲屏幕上的火焰图）："看到这个火焰图里最宽的那根柱子没？`HTTPHandler::readRequest` 占了 28% 的 CPU。你不是 CPU 没跑满——你是把 CPU 花在了解析 HTTP 头、处理 chunked encoding、做 URL decode 上，而不是花在执行查询上。你每一个查询 body 才 80 字节，HTTP 头就要 300 字节。你在用卡车运一颗糖豆。"

**小胖**："那我不用 HTTP 了？ClickHouse 不是还有个 9000 端口吗？"

**大师**："对，这就是我们要做的第一层优化。ClickHouse 的 Native Protocol（9000 端口）是完全二进制化的协议，没有 HTTP 头、没有 chunked encoding、没有 Content-Type 协商。字段的序列化直接按列二进制格式写入 TCP 流，客户端拿到的是已经分好列的原生二进制——不需要解析。源码在 `src/Server/TCPHandler.cpp` 里，`TCPHandler::runImpl()` 方法直接用 `NativeReader` 和 `NativeWriter` 做端到端的二进制读写。这个协议比 HTTP 快了 3 到 5 倍。"

**技术映射 #1**：Native Protocol 省掉了 HTTP 头解析（~300 字节/请求）、chunked encoding 组装/拆解、Content-Type 协商、URL decode 等环节。在高 QPS 场景下，请求体本身才几十到几百字节，HTTP 头的开销占比极高，这就是 Native Protocol 性能碾压 HTTP 的根本原因。

---

**小白**（快速翻着火焰图的另一列）："大师，就算换了 Native Protocol，我发现 `ColumnNullable::deserializeBinaryBulk` 在火焰图里占比也很高。序列化的开销真有那么大吗？不就是把内存里的列数据拷贝到网络缓冲区吗？"

**大师**："你说的是最理想的情况——Native 格式下的二进制零拷贝。但如果你用的是 TabSeparated、CSV 甚至 JSONEachRow 格式，序列化就不是'拷贝'而是'转换'：每一列的值都要先转成字符串表示，比如 `Int64` 的 1234567890 要转成 10 个 ASCII 字符；`Date` 要转成 `2025-01-15`；`Float64` 要格式化精度——这些都靠 CPU 算。反序列化端再把这些字符串解析回二进制。一来一回，CPU 就烧在这些转换上了。"

**小白**："那不同格式之间的性能差距到底有多大？"

**大师**在屏幕上调出 benchmark 数据：

"我上周在 64 核机器上用 `clickhouse-benchmark` 跑了相同的点查 `SELECT user_id, bid_price FROM rt_auction WHERE user_id = 12345`，只改输出格式：

- `FORMAT Native`：22 万 QPS（二进制，零拷贝可行路径最多）
- `FORMAT RowBinary`：16 万 QPS（按行二进制，每一行仍然需要构建 buffer）
- `FORMAT TabSeparated`：7 万 QPS（字符串转换 + 分隔符插入 + 转义处理）
- `FORMAT JSONEachRow`：1.5 万 QPS（字段名重复序列化 + 字符串转义 + JSON 语法组装）

Native 比 JSONEachRow 快了 **15 倍**，这就是序列化开销的'隐身杀人'——代码写对了，CPU 却在不该花的地方烧光了。"

**技术映射 #2**：序列化可分为两个维度——文本格式（TabSeparated、CSV、JSONEachRow）和二进制格式（Native、RowBinary）。文本格式的 CPU 开销来自数值到字符串的转换、分隔符/转义处理、以及接收端的解析。Native 格式按列直接写入二进制流，在内存布局对齐的情况下可以实现真正的零拷贝（`writeBinaryBulk` 直接把列的内存地址传给 `WriteBuffer`）。

---

**小胖**（突然从零食里抬起头）："那写入呢？我们晚上还有一批每小时 3000 万行的实时日志要灌进来，现在 INSERT 一条条发，10 万行每秒就到头了。这个能优化吗？"

**大师**："写路径的优化思路跟读完全不同。单条 INSERT 最大的开销不在序列化——而在于每次 INSERT 都是一个独立事务，要写 Part、更新 ZK（如果是复制表）、触发 Merge 检查。你用 100 万条小 INSERT 灌入，ClickHouse 就生成 100 万个 Part——MergeTree 的 Merge 线程直接炸掉。

解法是 **Async Insert 批处理**。你开启 `async_insert=1`，ClickHouse 会在内存中把陆续到达的小 INSERT 攒成一个大的 Block，攒到 10MB（`async_insert_max_data_size`）或者 100ms（`async_insert_busy_timeout_ms`）就批量写入磁盘——100 万条小 INSERT 变成了 500 个大的写入操作。写入吞吐可以从 1 万行/秒飙到 20 万行/秒——20 倍提升。

但代价也清楚——`wait_for_async_insert=0` 意味着数据在攒批期间（最坏 100ms）存在内存里，如果宕机了就丢了。金融场景请设 `wait_for_async_insert=1`，宁愿降低吞吐也不能丢数据。"

**小胖**（眼睛亮了一下）："懂了！就是收银台攒满一盒再放进保险柜，而不是每次收一块钱就去开一次保险柜！"

**大师**："这个比喻比我的好。再加一个细节——收银台有三个关闭条件：盒子满了（`max_data_size`）、到点了（`busy_timeout_ms`）、或者是单子太多了（`max_insert_count`）。任何一个条件满足都会触发提交流程。"

**技术映射 #3**：Async Insert 的本质是把 N 次磁盘写入操作合并为 1 次，减少 Part 碎片数、减少 Merge 压力、减少 ZK 操作次数。`AsyncInsertManager` 在 `src/Core/AsyncInsertQueue.cpp` 中实现，维护内存 buffer 并按三个阈值触发批量刷盘。

---

**小白**（推了推眼镜）："大师，网络协议换了、序列化换了、写入也批处理了，可我看 `htop` 上有个现象——ClickHouse 的线程在 64 个核之间跳来跳去，每跳一次 CPU cache 就全废了。这个能解决吗？"

**大师**："你说的是 CPU 调度器迁移（CPU scheduler migration）问题。ClickHouse 默认不绑核，Linux 调度器会根据自己的策略把查询线程在不同核间迁移。每次迁移，L1/L2 缓存全丢，重新加载数据的延迟是 ~10ns（L1 hit）变成 ~100ns（L2 miss）。在千万次查询的级别下，这点延迟会指数级累积。

解法分三层：

第一层，**CPU 绑核**——在 `config.xml` 配置 `<cpu_set>0-31</cpu_set>`，把 ClickHouse 进程钉在前 32 个物理核上，调度器没法把它迁移出去，保持缓存热度。

第二层，**NUMA 亲和性**——你的 64 核机器是双路 AMD EPYC，每个 CPU socket 连着 128GB 本地内存。如果线程跑在 socket 0 上却访问 socket 1 的内存，延迟加倍（跨 NUMA 访存约 150ns vs 本地 80ns）。用 `numactl --cpunodebind=0 --membind=0 clickhouse-server` 把进程绑定在 NUMA 节点 0 上，强制 CPU 和内存本地化。

第三层，**Huge Pages**——默认 4KB 页表项（PTE）下，256GB 内存需要 6700 万个 PTE 条目。TLB（快表）只有几千个槽位，TLB miss 会触发页表遍历，一次 miss 几十到上百个 CPU 周期。开 2MB Huge Pages 后，同样 256GB 只需要 13 万个条目，TLB 命中率从 60% 升到 99.5%。在 config.xml 中设 `<huge_pages>true</huge_pages>`，并提前在 OS 层面预留足够的大页。"

**小胖**："三个配置这么简单，效果大吗？"

**大师**在 Grafana 上拉出新曲线——17 万 QPS 跳到了 22 万 QPS，提升 30%。"不是惊天动地的提升，但在这场毫厘必争的游戏里，30% 可能就是你和竞品之间的差距。"

**技术映射 #4**：系统级优化的收益是叠加式的。CPU 绑核减少缓存失效，NUMA 亲和性避免跨节点访存，Huge Pages 降低 TLB miss——三者各自解决的是 CPU 微架构中不同层级的瓶颈，互不重叠，提升可以累加。

---

## 3. 项目实战

### 环境准备

- 一台高性能单机：64 核、256GB RAM、NVMe SSD
- ClickHouse 25.x LTS
- 压测工具：`wrk`/`wrk2`（HTTP）、`clickhouse-benchmark`（Native Protocol）
- 样本表建表脚本（模拟 RTB 点查场景）

```sql
CREATE TABLE rt_auction (
    request_id UUID,
    user_id UInt64,
    campaign_id UInt32,
    bid_price Float32,
    event_time DateTime DEFAULT now()
) ENGINE = MergeTree()
ORDER BY (user_id, campaign_id)
PARTITION BY toYYYYMMDD(event_time);

-- 预填 1 亿行样本数据
INSERT INTO rt_auction (request_id, user_id, campaign_id, bid_price)
SELECT generateUUIDv4(), rand64() % 100000000, rand32() % 100000, rand32() % 100
FROM numbers(100000000);
```

---

### 分步实现

#### Step 1：HTTP vs Native Protocol —— 第一层的性能分水岭

**目标**：对比 HTTP (8123) 与 Native Protocol (9000) 在同一查询上的 QPS 差异。

```bash
# ====== HTTP 协议压测 ======
# 准备 wrk 的 POST 脚本 post.lua
cat > post.lua << 'EOF'
wrk.method = "POST"
wrk.body   = "SELECT user_id, campaign_id, bid_price FROM rt_auction WHERE user_id = 12345678 FORMAT TabSeparated"
wrk.headers["Content-Type"] = "text/plain"
EOF

# 发起压测
wrk -t16 -c100 -d30s --latency -s post.lua http://localhost:8123/

# 典型输出:
# Requests/sec:  32000.50
# Latency (P50): 2.8ms
# Latency (P99): 12.5ms
# 瓶颈: HTTP 头解析 (300+ 字节) + TabSeparated 序列化


# ====== Native Protocol 压测 ======
clickhouse-benchmark --host localhost --port 9000 \
  --concurrency 100 --iterations 1000000 \
  --query "SELECT user_id, campaign_id, bid_price FROM rt_auction WHERE user_id = 12345678"

# 典型输出:
# Queries executed: 1000000
# Queries per second: 138000
# 提升: 3.2 万 → 13.8 万, 4.3x 提升

# ====== Native Protocol + 连接池 ======
# clickhouse-benchmark 默认复用连接，每线程一个持久 TCP 连接
# 如果用 Python 客户端，务必开连接池:
# from clickhouse_driver import Client
# from clickhouse_driver.pool import Pool
# pool = Pool(host='localhost', port=9000, pool_size=50)
```

**常见坑**：`wrk` 的每个连接默认带 `Connection: keep-alive`，但如果后端 ClickHouse 的 `keep_alive_timeout` 设得太短（如 3 秒），wrk 连接池里的空闲连接会被服务端主动关闭，下次请求触发新连接的三次握手——P99 延迟瞬间从 5ms 飙到 50ms。务必设置 `keep_alive_timeout >= 300`。

---

#### Step 2：序列化格式的性能差距——CPU 花在了哪里

**目标**：量化不同输出格式的 CPU 开销，选出最优序列化方案。

```sql
-- ====== 在 clickhouse-client 内执行 ======

-- 1. Native 格式 (二进制按列, 零拷贝)
-- QPS: ~140K | 服务端序列化 CPU: ~5%

-- 2. RowBinary 格式 (二进制按行)
-- QPS: ~105K | 服务端序列化 CPU: ~12%
-- 解释: RowBinary 需要逐行构建二进制 buffer，无法直接映射列内存

-- 3. TabSeparated 格式 (文本按列)
-- QPS: ~45K  | 服务端序列化 CPU: ~28%
-- 解释: 每个字段都要 toString() + 分隔符插入 + 转义特殊字符

-- 4. CSV 格式
-- QPS: ~38K  | CPU: ~32%
-- 额外开销: 引号包裹字符串 + 双引号转义

-- 5. JSONEachRow 格式
-- QPS: ~15K  | CPU: ~55%
-- 额外开销: 每行重复输出字段名（如 "user_id":12345 中 "user_id": 占 10 字节）
--           + JSON 语法组装（逗号、花括号、方括号）
--           + 字符串转义 (\n → \\n, " → \")
```

```bash
# 可以直接用 clickhouse-benchmark 切换格式对比
clickhouse-benchmark -c 50 -i 500000 \
  --query "SELECT * FROM rt_auction WHERE user_id = 12345678 FORMAT JSONEachRow" \
  2>&1 | grep "QPS"

# 格式选择决策树:
# ┌─ 客户端是 C++ clickhouse-client / clickhouse-benchmark
# │  → 用 Native, 零拷贝路径最多
# ├─ 客户端是 Python clickhouse-driver (C++ binding)
# │  → 默认走 Native, 无需额外配置
# ├─ 客户端是 Go/Java/Rust 的第三方库
# │  → 优先 Native (如果库支持), 其次 RowBinary
# ├─ 客户端要通过 HTTP 网关 / 负载均衡器
# │  → 只能用 TabSeparated / JSONEachRow (HTTP 不支持 Native)
# └─ 客户端是浏览器 JavaScript
#    → JSONEachRow (唯一选项)
```

---

#### Step 3：异步写入——把 N 次写操作合并为 1 次

**目标**：将 INSERT 吞吐从 ~1 万行/秒提升至 20 万行/秒。

```sql
-- ====== 同步写入（基准） ======
-- 100 万次单条 INSERT:
-- Time: 102.5 sec → QPS: ~9.7K INSERT/s
-- 磁盘行为: 生成了 10 万个 Part (Part 碎片灾难)
-- Merge 延迟: 系统 CPU 被 Merge 线程吃了 40%

SELECT
    count() as parts_count,
    sum(bytes_on_disk) / 1024 / 1024 as size_mb
FROM system.parts
WHERE table = 'rt_auction' AND active;


-- ====== 开启 Async Insert ======
SET async_insert = 1;
SET wait_for_async_insert = 0;            -- Fire-and-forget (性能模式)
SET async_insert_max_data_size = 10485760; -- 每个批次最大 10MB
SET async_insert_busy_timeout_ms = 100;    -- 每 100ms 强制刷一次

-- 同样 100 万次单条 INSERT:
-- Time: 4.8 sec → QPS: ~208K INSERT/s (20x 提升!)
-- 磁盘行为: 仅生成 ~500 个 Part (批次合并后的大 Part)
-- Merge 延迟: Merge 线程 CPU 从 40% 降到 5%

-- 再次检查 Part 数量:
-- parts_count: ~500 (之前 100K!)

-- 生产环境推荐配置（兼顾安全与性能）:
SET async_insert = 1;
SET wait_for_async_insert = 1;            -- 等待刷盘确认
SET async_insert_max_data_size = 5242880;  -- 5MB/批次
SET async_insert_busy_timeout_ms = 200;    -- 200ms 超时
-- 这个配置下吞吐约 80K INSERT/s, 但宕机后最多丢失 200ms 数据
```

**内部原理**（`src/Core/AsyncInsertQueue.cpp` 简化逻辑）：

```
AsyncInsertManager 工作流程:
┌─────────────────────────────────────────────────────┐
│ Client1 INSERT ─┐                                   │
│ Client2 INSERT ─┤                                   │
│ Client3 INSERT ─┼─→ Memory Buffer ─→ 触发条件检查   │
│ ...             │   (Block 累积)      ↓              │
│ ClientN INSERT ─┘               ┌─────┴──────┐       │
│                                 │ 3 个阈值:  │       │
│                                 │ ① 10MB     │       │
│                                 │ ② 100ms    │       │
│                                 │ ③ 1000条   │       │
│                                 └─────┬──────┘       │
│                                       ↓              │
│                               满足任一 → 批量写入     │
│                                  storage->write()    │
│                                  (单次 I/O)          │
└─────────────────────────────────────────────────────┘
```

---

#### Step 4：系统级优化——从 CPU 微架构要性能

**目标**：通过 OS 层面优化消除 CPU 缓存失效与跨 NUMA 访存。

```bash
# ========== 1. CPU 绑核 ==========
# 编辑 /etc/clickhouse-server/config.d/cpu_bind.xml:
cat > /etc/clickhouse-server/config.d/cpu_bind.xml << EOF
<clickhouse>
    <cpu_set>0-31</cpu_set>  <!-- 钉在前 32 个物理核 -->
</clickhouse>
EOF

# 验证: top → 按 1 查看每核负载 → 只有核 0-31 有 ClickHouse 线程
# 效果: 缓存命中率提升, P99 延迟降低 8-15%


# ========== 2. NUMA 亲和性 ==========
# 检查 NUMA 拓扑
numactl --hardware
# 输出示例:
# node 0 cpus: 0-31
# node 0 size: 128000 MB
# node 1 cpus: 32-63
# node 1 size: 128000 MB

# 启动时绑定到单 NUMA 节点
numactl --cpunodebind=0 --membind=0 clickhouse-server --daemon

# 或者通过 systemd unit 配置:
# /etc/systemd/system/clickhouse-server.service.d/numa.conf
# [Service]
# ExecStartPre=/usr/bin/numactl --cpunodebind=0 --membind=0
# 效果: 跨 NUMA 访存消除, 内存延迟降低 ~50%


# ========== 3. Huge Pages ==========
# 预留大页 (2MB/page, 8192 页 = 16GB)
echo 8192 > /proc/sys/vm/nr_hugepages

# 确认预留成功
cat /proc/meminfo | grep HugePages
# HugePages_Total:    8192
# HugePages_Free:     8192

# 在 config.xml 中开启:
echo '<clickhouse><huge_pages>true</huge_pages></clickhouse>' \
  > /etc/clickhouse-server/config.d/huge_pages.xml

# 验证启用:
grep -i huge /var/log/clickhouse-server/clickhouse-server.log
# 输出: <Information> Application: huge pages enabled

# 效果: TLB miss 率从 ~35% 降到 ~0.5%, CPU cycles 节省 5-10%


# ========== 4. 网络栈调优 ==========
# 连接队列
sysctl -w net.core.somaxconn=65535
sysctl -w net.ipv4.tcp_max_syn_backlog=8192

# TIME_WAIT 复用 (减少 CLOSE_WAIT 堆积)
sysctl -w net.ipv4.tcp_tw_reuse=1
sysctl -w net.ipv4.tcp_fin_timeout=10

# TCP Fast Open (省掉 1 次 RTT 的握手)
sysctl -w net.ipv4.tcp_fastopen=3
# 0 = 禁用, 1 = 客户端, 2 = 服务端, 3 = 双向启用

# ========== 5. NVMe I/O 调度器 ==========
echo none > /sys/block/nvme0n1/queue/scheduler
# NVMe 没有物理旋转延迟和寻道时间，不需要任何调度算法
# none/noop 是最优选择 (CFQ/deadline 徒增延迟)
```

---

#### Step 5：QUIC/HTTP3 实验性支持

**目标**：了解 24.x+ 的 QUIC/HTTP3 实验性特性。

```xml
<!-- 在 config.xml 中启用 QUIC -->
<clickhouse>
  <listen_host>0.0.0.0</listen_host>

  <!-- 监听 8443 端口提供 QUIC/HTTP3 -->
  <quic_port>8443</quic_port>

  <quic_server>
    <certificate_file>/etc/clickhouse-server/cert.pem</certificate_file>
    <private_key_file>/etc/clickhouse-server/key.pem</private_key_file>
  </quic_server>
</clickhouse>
```

```bash
# 重新加载配置后，用支持 HTTP3 的 curl 测试:
curl --http3 -v https://localhost:8443/?query=SELECT+1+FORMAT+TabSeparated

# 在 system.metrics 中查看 QUIC 连接数:
SELECT metric, value FROM system.metrics WHERE metric LIKE '%QUIC%';
```

**QUIC 的理论优势**：

| 特性 | TCP + TLS 1.3 | QUIC (HTTP/3) |
|------|---------------|---------------|
| 连接建立 | 3 次 TCP 握手 + TLS 握手 (2-3 RTT) | 0-RTT (复用连接) 或 1-RTT (首次) |
| 队头阻塞 | TCP 流级队头阻塞 | 每个 Stream 独立, 无队头阻塞 |
| 连接迁移 | 不支持 (切换 IP 需重新建连) | 支持 (Connection ID 不变) |
| 内核实现 | 内核 TCP 栈 | 用户态 (灵活迭代) |

**当前限制**：ClickHouse 24.x 的 QUIC 实现仍处于实验性阶段，且 ClickHouse 的查询模型天然是请求-响应短连接模式（每条 SELECT 都是一个独立请求），QUIC 的 0-RTT 优势在点查场景下有意义，但在批量查询或持续传输的 ETL 场景下收益有限。**暂不建议生产使用**，此节以技术预览为主。

---

#### Step 6：客户端选择对比

**目标**：不同客户端库在高 QPS 场景下的性能排位。

```python
# ====== 方案 1: clickhouse-driver (Python + C++ Native Protocol) ======
# pip install clickhouse-driver
from clickhouse_driver import Client
from clickhouse_driver.pool import Pool

# 连接池配置
pool = Pool(
    host='localhost', port=9000,
    pool_size=50,          # 预建 50 条持久 TCP 连接
    pool_min_size=10,
    pool_max_size=100
)

with pool.get_client() as client:
    result = client.execute(
        'SELECT user_id, campaign_id, bid_price FROM rt_auction WHERE user_id=%(uid)s',
        {'uid': 12345678}
    )
# 单连接 QPS: ~50K | 50 连接池 QPS: ~200K
# 优势: Native Protocol, C++ binding, 无 GIL 竞争
# 劣势: 仅支持 Python, 需要安装 C++ 依赖


# ====== 方案 2: clickhouse-connect (Python + HTTP) ======
# pip install clickhouse-connect
from clickhouse_connect import get_client

client = get_client(
    host='localhost', port=8123,
    pool_size=32,
    compress='lz4'         # HTTP body 压缩
)
result = client.query(
    'SELECT user_id, campaign_id, bid_price FROM rt_auction WHERE user_id={uid:UInt64}',
    parameters={'uid': 12345678}
)
# 单连接 QPS: ~8K | 32 连接池 QPS: ~80K
# 优势: 纯 Python, 兼容性好, 支持 HTTP 负载均衡
# 劣势: HTTP 头开销 + TabSeparated/JSON 序列化


# ====== 方案 3: 原生 C++ 客户端 ======
// #include <clickhouse/client.h>
// 直连 Native Protocol, 性能最高
// QPS: ~250K (同样 50 连接池)
// 劣势: 开发成本高, 缺乏高级语言生态
```

**客户端选择决策矩阵**：

| 客户端 | 协议 | 单连接 QPS | 连接池 QPS | 适用场景 |
|--------|------|------------|------------|----------|
| C++ Native Client | Native | ~80K | ~250K | 自研 RTB/交易系统 |
| Python clickhouse-driver | Native | ~50K | ~200K | Python 内部服务 |
| Python clickhouse-connect | HTTP | ~8K | ~80K | 网关代理后服务 |
| Java JDBC | HTTP | ~5K | ~60K | Spring Boot 业务 |

---

#### Step 7：综合优化结果追踪

**目标**：记录每一步优化带来的 QPS 提升，形成可复现的调优手册。

```
压测查询: SELECT user_id, campaign_id, bid_price
          FROM rt_auction WHERE user_id = 12345678

基线 (HTTP + TabSeparated + 单连接 + 默认配置)
  QPS: ~32,000 (3.2 万)
  P50: 2.8ms  P99: 12.5ms

阶段 1: 切换 Native Protocol (9000)
  QPS: ~138,000 (+331%)  累计: 4.3x
  P50: 0.4ms  P99: 2.2ms
  核心收益: 消除 HTTP 头解析开销

阶段 2: 连接池 (50 持久连接)
  QPS: ~185,000 (+34%)   累计: 5.8x
  P50: 0.3ms  P99: 1.6ms
  核心收益: 消除 TCP 三次握手 + TLS 协商

阶段 3: 格式优化 (Native → 本身就是 Native)
  (本案例从 Step1 就用 Native 格式, 没有额外提升;
   如果之前是 TabSeparated, 切换到 Native 额外 +70% QPS)

阶段 4: CPU 绑核 + NUMA 亲和
  QPS: ~218,000 (+18%)   累计: 6.8x
  P50: 0.25ms P99: 1.2ms
  核心收益: 缓存命中率 + 消除跨 NUMA 访存

阶段 5: Huge Pages + 网络栈调优
  QPS: ~235,000 (+8%)    累计: 7.3x
  P50: 0.22ms P99: 0.9ms
  核心收益: TLB miss 降低 + 减少网络栈开销

阶段 6: 写入侧 Async Insert (不影响查询 QPS)
  写入基准: ~10,000 INSERT/s
  写入优化后: ~200,000 INSERT/s (+1900%)

单节点极限 (查询场景): 235K QPS (点查)
单节点极限 (写入场景): 200K INSERT/s (批量点写)
```

**优化收益分层**：

```
第一梯队 (协议/格式, 3-5x 提升):
  ① Native Protocol > HTTP
  ② Binary 格式 > Text 格式
  → 投入产出比最高, 改配置/代码即可

第二梯队 (系统/架构, 20-50% 提升):
  ③ 连接池复用
  ④ CPU 绑核 + NUMA
  ⑤ Async Insert Batching
  → 需要 OS 层面配置, 风险可控

第三梯队 (硬件/微架构, 5-15% 提升):
  ⑥ Huge Pages
  ⑦ 网络栈参数
  ⑧ I/O 调度器换 noop
  → 收益递减, 适合极致优化场景
```

---

### 测试验证

```bash
# 验证脚本: 记录每次优化前后的 QPS 与 P50/P99

echo "=== Pre-Optimization Baseline ==="
wrk -t16 -c100 -d30s --latency -s post.lua http://localhost:8123/

echo "=== Post-Optimization Verification ==="
clickhouse-benchmark -c 100 -i 100000 \
  --query "SELECT 1" 2>&1 | grep "QPS"

# 饱和度测试: 递增并发找 QPS 天花板
for c in 10 50 100 200 500 1000; do
  echo "Concurrency: $c"
  clickhouse-benchmark -c $c -i 200000 \
    --query "SELECT user_id, campaign_id, bid_price \
             FROM rt_auction WHERE user_id = 12345678" \
    2>&1 | grep "QPS"
done

# 观察: 随着并发增加，QPS 先升高后持平，P99 在饱和点开始剧烈上升
# 饱和点就是单节点的硬极限，超过此点继续加并发只会增加延迟
```

---

## 4. 项目总结

### 优化层级与收益速查表

| 层级 | 优化项 | 典型收益 | 难度 | 风险 |
|------|--------|----------|------|------|
| 协议 | Native vs HTTP | 3-5x | 低 | 无（仅客户端改代码） |
| 序列化 | Binary vs Text 格式 | 2-5x | 低 | 无（仅改 FORMAT 子句） |
| 写入批处理 | Async Insert | 10-20x | 中 | 可能丢失 ~100ms 数据 |
| 连接管理 | KeepAlive + 连接池 | 30-50% | 低 | 连接池过大占用 fd/内存 |
| CPU 调度 | 绑核 + NUMA 亲和性 | 15-30% | 中 | 绑错核降性能 |
| 内存管理 | Huge Pages | 5-15% | 中 | 大页预留不足分配失败 |
| 硬件升级 | 更高主频 CPU / NVMe / 25GbE | 50-200% | 高 ($) | 成本、机房空间 |

### 适用场景

1. **广告实时竞价（RTB）**：每条 HTTP 请求对应一条点查，QPS 通常在 10 万-100 万量级。Native Protocol + 连接池是必选项。
2. **高频交易数据喂送**：交易行情写入 + 实时风控查询，写入吞吐和查询延迟同等重要。Async Insert + 二进制格式的组合收益最高。
3. **实时推荐服务**：用户画像点查，响应时间 < 5ms P99。CPU 绑核 + NUMA 亲和性是消除延迟长尾的关键。
4. **高流量 API 分析**：埋点日志实时入库，INSERT 吞吐是关键瓶颈。Async Insert 是首选优化。
5. **不适用场景**：离线 ETL 大查询跑批（优化网络协议无意义，瓶颈在磁盘与 CPU 计算）；低频管理型查询（调优投入产出比低）。

### 注意事项

1. **Async Insert 的数据安全性**：`wait_for_async_insert=0` 是极致性能模式，数据在宕机时可能丢失。对金融、支付等对数据完整性有刚性要求的场景，请用 `wait_for_async_insert=1`，牺牲部分吞吐换取写入确认。
2. **NUMA 绑定的反效果**：如果只绑定到 1 个 NUMA 节点（比如只用 32 核），另一个节点的 128GB 内存就成了"远内存"——访问代价更高。确认你的工作集能塞进单 NUMA 节点的本地内存后再做绑定。
3. **Huge Pages 配置失败**：`/proc/sys/vm/nr_hugepages` 设置会被重启重置, 务必写入 `/etc/sysctl.conf` 持久化。如果 ClickHouse 日志中出现 `Cannot allocate huge pages` 错误, 说明预留大页不足或内存碎片化, 建议在系统启动早期预留。
4. **Async Insert 不适合小数据量场景**：如果每分钟才写入几百条, Async Insert 的批处理反而增加了延迟（数据要等到批满或超时才落盘）。同步写入就够了。
5. **QUIC/HTTP3 勿用于生产**：ClickHouse 24.x 的 QUIC 还是实验性功能, 稳定性、安全性均未经过大量生产验证。仅在内部测试环境尝鲜。

### 常见踩坑经验

1. **连接池开太大把文件描述符耗尽**：某团队设 `pool_size=2000`，查询高峰期进程 fd 数从 200 暴涨到 24000，触发 `ulimit -n` 上限（默认 1024），所有新连接全部失败，`Too many open files`。解法：每个线程 5-10 个连接足矣，`pool_size = worker_threads × 5`。

2. **async_insert 配合 insert_quorum 导致双写等待**：某团队同时开启 `async_insert=1` 和 `insert_quorum=2`，原本期望批处理提速，结果每次 Async Insert 的刷盘都要等待 quorum 确认——延迟从 100ms 变成 2 秒。因为 await 会阻塞整批数据。**Async Insert 与 insert_quorum 是冲突的优化方向**，不要同时使用。

3. **CPU 绑核后其他服务抢占核资源**：把 ClickHouse 钉在前 32 核后，Node Exporter / Grafana Agent / SSH daemon 等系统进程全挤到后 32 核，导致监控采集延迟飙升。解法：把 ClickHouse 绑在后 48 核（16-63），前 16 核留给系统进程。或者用 cgroup 按权重而非硬绑定管理 CPU。

### 思考题

1. **为什么 Native Protocol 比 HTTP 快 3-5 倍？具体是哪些开销导致的？**

   *提示：从 HTTP 头解析（~300B/请求 × QPS = 额外带宽 + CPU）、chunked encoding 组装/拆解、Content-Type 协商的三次握手、URL decode 等多角度分析。考虑请求体仅 80 字节的极端场景——HTTP 开销占比超过 80%。*

2. **如果单节点 50 万 QPS 已达瓶颈，横向扩展到 5 节点能到 250 万 QPS 吗？为什么？**

   *提示：分布式查询的"短板效应"——`SELECT ... WHERE user_id = ?` 的分布式查询需要发起节点合并分片结果，全局延迟 = max(各分片延迟) + 合并延迟。此外，如果分片键与查询条件不匹配（比如按 user_id 分片但查询带 campaign_id），查询会扇出到所有分片。线性扩展的前提是查询能精准路由到单分片。考虑数据的 sharding 策略和查询路由的设计。*

---

> **本章完。第 40 章【高级篇综合实战】将把本专题所有知识融会贯通，从零构建一个承载 500+ 报表的企业级数据仓库。**

