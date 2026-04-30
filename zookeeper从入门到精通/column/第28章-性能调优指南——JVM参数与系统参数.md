# 第28章：性能调优指南——JVM 参数与系统参数

## 1. 项目背景

### 业务场景

ZooKeeper 集群遇到了性能瓶颈：

- 写入延迟从 2ms 飙升到 200ms
- Full GC 频繁发生，每次停顿 3-5 秒
- 大量客户端连接超时，触发会话过期

ZooKeeper 的性能主要受三个因素影响：**JVM 参数**、**操作系统参数**、**ZooKeeper 自身配置**。本章从这三方面入手，系统地提升 ZooKeeper 集群的性能。

### 痛点放大

性能问题如果不及时处理：

- **客户端超时断开**：临时节点被删除，服务发现失效
- **请求积压**：outstanding_requests 增长，客户端响应延迟变大
- **选主慢**：GC 停顿导致 ZooKeeper 无法及时响应心跳，触发不必要的选举

---

## 2. 项目设计

### 剧本式交锋对话

**场景**：小胖的 ZooKeeper 集群在高峰期延迟飙高，客户端大量超时。

**小白**：我们 8G 堆内存，tickTime=2000，应该够了吧？为什么还卡？

**大师**：ZooKeeper 的性能瓶颈主要看这几个方面：

**1. 磁盘 IO（最重要）**

ZooKeeper 每次写入都要 `fsync` 事务日志到磁盘。如果磁盘慢（HDD），写入延迟就大。

```bash
# 测试磁盘 IO 性能
# 随机写 IOPS——最接近 ZooKeeper 场景
fio --name=write-test --ioengine=libaio --rw=randwrite \
    --bs=4k --direct=1 --size=1G --numjobs=1 --runtime=60

# 期望结果：
# SSD: > 10,000 IOPS（延迟 < 0.1ms）
# HDD: < 200 IOPS（延迟 > 5ms）
```

**2. GC 配置**

ZooKeeper 将所有数据放在内存中（DataTree + 会话 + Watcher）。Full GC 的频率和时长直接影响 ZooKeeper 的响应能力。

```bash
# 查看 GC 统计
jstat -gcutil <pid> 1000

# S0  S1  E   O   M  CCS  YGC   YGCT  FGC  FGCT
# 0.00 0.00 25.43 45.67 ...  1200  12.34  5   18.90
# O（Old）区占用 45.67%，FGC=5 次，FGCT 总耗时 18.9 秒
# Full GC 次数越少越好，FGCT 越小越好
```

**3. ZooKeeper 参数配置**

| 参数 | 默认 | 推荐（生产） | 说明 |
|------|------|-------------|------|
| tickTime | 2000 | 2000-3000 | 基本时间单位 |
| initLimit | 10 | 10-20 | Follower 连接 Leader 的超时 |
| syncLimit | 5 | 2-5 | Follower 同步超时 |
| snapCount | 100000 | 50000-100000 | 快照触发间隔 |
| globalOutstandingLimit | 1000 | 1000-10000 | 写入请求排队上限 |
| maxClientCnxns | 60 | 0（不限制） | 单 IP 最大连接数 |

**小胖**：那 JVM 参数怎么配？8G 够不够？

**大师**：给你一个生产级 JVM 配置参考：

```bash
# 生产环境推荐 JVM 参数（4-8G 堆内存）
export JVMFLAGS="
-server
-Xms4g -Xmx4g            # 堆内存 4G（Xms = Xmx，防止动态调整）
-XX:+UseG1GC             # G1 垃圾回收器
-XX:MaxGCPauseMillis=200 # GC 最大暂停时间 200ms
-XX:+DisableExplicitGC   # 禁止 System.gc()
-XX:+HeapDumpOnOutOfMemoryError
-XX:HeapDumpPath=/var/log/zookeeper/heapdump.hprof
-XX:+PrintGCDetails
-XX:+PrintGCDateStamps
-Xloggc:/var/log/zookeeper/gc.log
-Dzookeeper.jmx.log4j.disable=true
"
```

**内存估算公式**：

```
每个 ZNode 约占用 100-500 字节（路径 + 数据 + Stat）
每个 Watcher 约占用 100 字节
每个 Session 约占用 200 字节 + 临时节点

4G 堆内存 ≈ 500 万个 ZNode + 10 万个 Watcher + 10 万个 Session
8G 堆内存 ≈ 1000 万个 ZNode + 20 万个 Watcher + 20 万个 Session
```

> **技术映射**：ZooKeeper 性能 = 数据库性能优化三板斧：磁盘（慢查询问题）、内存（缓存命中率）、CPU（并发度）

**小白**：那我应该用 G1GC 还是 ZGC？ZGC 不是延迟更低吗？

**大师**：好问题。JDK 11+ 可以用 ZGC，JDK 17+ 可以用 ZGC 的改进版：

| GC | 暂停时间 | 吞吐 | 适用场景 |
|------|---------|------|---------|
| G1GC | ~200ms | 高 | 常规生产，堆内存 < 32G |
| ZGC | < 10ms | 中 | 低延迟场景，堆内存 < 64G |
| Shenandoah | < 10ms | 中 | JDK 12+，暂停时间敏感 |

注意：ZGC 的吞吐量略低于 G1GC（约 5-10%），但对 ZooKeeper 来说，**低延迟比高吞吐更重要**（ZooKeeper 本身的 QPS 瓶颈在磁盘 IO 不在 GC）。

---

## 3. 项目实战

### 环境准备

- ZooKeeper 3.9.x 运行中
- JDK 11+
- 压测工具：zk-smoketest 或 zk-latencies

### 分步实现

#### 步骤 1：JVM 参数调优

创建 `zk-env.sh`（ZooKeeper 启动参数配置）：

```bash
#!/bin/bash
# ZooKeeper 生产环境 JVM 参数配置

# 堆内存（根据机器配置调整）
ZK_HEAP_SIZE="4G"

# JVM 参数
ZK_SERVER_JVMFLAGS="
-server
-Xms${ZK_HEAP_SIZE}
-Xmx${ZK_HEAP_SIZE}
-XX:+UseG1GC
-XX:MaxGCPauseMillis=200
-XX:InitiatingHeapOccupancyPercent=70
-XX:G1ReservePercent=20
-XX:+ParallelRefProcEnabled
-XX:+DisableExplicitGC
-XX:+HeapDumpOnOutOfMemoryError
-XX:HeapDumpPath=/var/log/zookeeper/heapdump-${HOSTNAME}.hprof
-XX:+PrintGCDetails
-XX:+PrintGCDateStamps
-XX:+PrintGCApplicationStoppedTime
-Xloggc:/var/log/zookeeper/gc-${HOSTNAME}.log
-XX:+UseGCLogFileRotation
-XX:NumberOfGCLogFiles=10
-XX:GCLogFileSize=10M
"

# ZGC 版本（JDK 17+）：
# -XX:+UseZGC
# -XX:ZAllocationSpikeTolerance=2
# -XX:+ZGenerational (JDK 21+ 分代 ZGC)

# 导出给 ZooKeeper
export JVMFLAGS="${ZK_SERVER_JVMFLAGS}"
```

使用方式：

```bash
# 加载 JVM 参数
source zk-env.sh

# 启动 ZooKeeper（自动使用配置的 JVM 参数）
./bin/zkServer.sh start
```

#### 步骤 2：监控 GC 和 JVM 参数

创建 `JvmMonitor.java`：

```java
package com.zkdemo.tuning;

import javax.management.*;
import java.lang.management.*;
import java.util.*;

public class JvmMonitor {
    public static void main(String[] args) throws Exception {
        System.out.println("=== ZooKeeper JVM 监控 ===\n");

        // 1. JVM 信息
        RuntimeMXBean runtime = ManagementFactory.getRuntimeMXBean();
        System.out.println("JVM: " + runtime.getVmName() + " " + runtime.getVmVersion());

        // 2. 堆内存
        MemoryMXBean memory = ManagementFactory.getMemoryMXBean();
        MemoryUsage heap = memory.getHeapMemoryUsage();
        System.out.printf("堆内存: 已用 %d MB / 最大 %d MB (%.1f%%)%n",
                heap.getUsed() / 1024 / 1024,
                heap.getMax() / 1024 / 1024,
                heap.getUsed() * 100.0 / heap.getMax());

        // 3. GC 统计
        for (GarbageCollectorMXBean gc : ManagementFactory.getGarbageCollectorMXBeans()) {
            System.out.printf("GC [%s]: 次数=%d, 累计耗时=%d ms%n",
                    gc.getName(), gc.getCollectionCount(), gc.getCollectionTime());
        }

        // 4. 线程信息
        ThreadMXBean threads = ManagementFactory.getThreadMXBean();
        System.out.printf("线程: 活跃=%d, 峰值=%d%n",
                threads.getThreadCount(), threads.getPeakThreadCount());

        // 5. 操作系统
        OperatingSystemMXBean os = ManagementFactory.getOperatingSystemMXBean();
        System.out.printf("系统: 负载=%.2f, 核数=%d%n",
                os.getSystemLoadAverage(), os.getAvailableProcessors());

        // 6. 文件描述符（JDK 11+）
        try {
            Object openFD = ManagementFactory.getPlatformMBeanServer()
                    .getAttribute(new ObjectName("java.lang:type=OperatingSystem"),
                            "OpenFileDescriptorCount");
            Object maxFD = ManagementFactory.getPlatformMBeanServer()
                    .getAttribute(new ObjectName("java.lang:type=OperatingSystem"),
                            "MaxFileDescriptorCount");
            System.out.printf("文件描述符: %s / %s (%.1f%%)%n",
                    openFD, maxFD,
                    Double.parseDouble(openFD.toString()) * 100 / Double.parseDouble(maxFD.toString()));
        } catch (Exception e) {
            System.out.println("文件描述符: 无法获取（需要 JDK 11+）");
        }
    }
}
```

#### 步骤 3：压测并对比调优效果

使用 `zk-latencies` 工具压测：

```bash
#!/bin/bash
# 性能基准测试脚本

echo "=== ZooKeeper 性能基准测试 ==="

# 1. 测试环境信息
echo "--- 系统信息 ---"
uname -a
java -version
free -h

# 2. 磁盘 IO 测试
echo ""
echo "--- 磁盘写入延迟测试 ---"
# 测试 ZooKeeper 数据目录所在磁盘
DATA_DIR=$(grep dataDir conf/zoo.cfg | cut -d= -f2)
echo "数据目录: $DATA_DIR"
sync; echo 3 > /proc/sys/vm/drop_caches  # 清理缓存

# 简单延迟测试
dd if=/dev/zero of=$DATA_DIR/test-io bs=4k count=10000 conv=fdatasync 2>&1 | tail -1
rm -f $DATA_DIR/test-io

# 3. 使用 zk-latencies 压测
echo ""
echo "--- ZooKeeper 延迟测试 ---"
echo "测试内容: 每次创建/读取/删除一个节点，共 10000 次"

java -cp zookeeper-3.9.2.jar:lib/* \
    org.apache.zookeeper.perf.CreateReadDeleteBenchmark \
    127.0.0.1:2181 10000 100 1000 1

# 4. 使用 zk-smoketest 压测
echo ""
echo "--- zk-smoketest 压测 ---"
python zk-smoketest/zk-smoketest.py \
    --servers 127.0.0.1:2181 \
    --force \
    --timeout 30000 \
    --synchronous \
    --verbose \
    --zc 10 \
    --loop 1000
```

#### 步骤 4：操作系统参数优化

创建 `sysctl-zk.conf`：

```properties
# ZooKeeper 操作系统优化参数

# 1. 文件描述符上限（重要！）
# ZooKeeper 每个客户端连接占用一个文件描述符
# 在 /etc/security/limits.conf 中配置
# * soft nofile 65536
# * hard nofile 65536

# 2. 网络参数
# 加快 TIME_WAIT 状态的连接回收
net.ipv4.tcp_fin_timeout = 15
# 允许更多的 TIME_WAIT 连接
net.ipv4.tcp_max_tw_buckets = 2000000
# TCP keepalive 时间（秒）
net.ipv4.tcp_keepalive_time = 300
# 增加 TCP 连接队列长度
net.core.somaxconn = 4096

# 3. 虚拟内存
# 减少 swap 使用（ZooKeeper 使用大量内存，swap 会导致性能灾难）
vm.swappiness = 1
# 增大脏页刷新速度
vm.dirty_background_ratio = 5
vm.dirty_ratio = 10
```

应用参数：

```bash
# 应用 sysctl 参数（需要 root 权限）
sudo sysctl -p sysctl-zk.conf

# 配置文件描述符限制
echo "* soft nofile 65536" >> /etc/security/limits.conf
echo "* hard nofile 65536" >> /etc/security/limits.conf
```

#### 步骤 5：ZooKeeper 配置优化

创建 `zoo-production.cfg`：

```properties
# ZooKeeper 生产环境配置

# 时间参数
tickTime=2000
initLimit=10
syncLimit=2

# 数据目录
dataDir=/var/lib/zookeeper/data
dataLogDir=/var/lib/zookeeper/logs  # 事务日志独立磁盘（SSD）

# 客户端连接
clientPort=2181
maxClientCnxns=0              # 不限制单 IP 连接数

# 快照与日志
snapCount=50000               # 减少快照频率（降低 IO）
autopurge.snapRetainCount=5   # 保留 5 个快照
autopurge.purgeInterval=1     # 每小时清理一次

# 性能参数
globalOutstandingLimit=10000  # 增大排队上限
preAllocSize=65536            # 事务日志预分配大小（64KB）
snapSizeLimitInKb=4194304     # 快照最大 4GB

# ZAB 协议参数
maxCnxns=0                    # 最大连接数（0=不限制）
electionAlg=3                 # 选举算法（3 = FastLeaderElection，推荐）
cnxTimeout=5000               # Leader 选举连接超时（毫秒）

# 安全
4lw.commands.whitelist=ruok,stat,mntr,conf,isro,srvr,wchs
admin.serverPort=8080         # Admin Server HTTP 端口
admin.enableServer=true       # 开启 Admin Server

# 高级功能（3.5+）
extendedTypesEnabled=true     # 启用 TTL 等扩展节点类型
```

### 测试验证

```bash
# 调优前
# 1. 记录基准性能
echo "=== 调优前 ==="
export JVMFLAGS="-Xms2g -Xmx2g"
./bin/zkServer.sh restart
# 运行 zk-latencies 或其他压测工具记录结果

# 调优后
echo "=== 调优后 ==="
source zk-env.sh  # 加载生产 JVM 参数
./bin/zkServer.sh restart
# 运行同样的压测，对比结果
```

### 可能遇到的坑

| 问题 | 原因 | 解决 |
|------|------|------|
| GC 暂停导致会话过期 | Full GC 暂停 > Session Timeout | 增大 Session Timeout 或换 ZGC |
| fsync 延迟高 | HDD 磁盘 | 换 SSD，事务日志和快照分盘 |
| 堆内存不足 | ZNode 数据量超预期 | 增大堆内存或清理无用 ZNode |

---

## 4. 项目总结

### 调优优先级

```
第一优先：磁盘 IO（换 SSD，事务日志和快照分盘）
第二优先：JVM 参数（G1GC/ZGC，堆内存规划）
第三优先：ZooKeeper 参数（snapCount、globalOutstandingLimit）
第四优先：操作系统参数（文件描述符、网络、swap）
```

### 推荐配置速查

| 场景 | 堆内存 | GC | SSD | 节点数 | ZNode 数 |
|------|--------|-----|-----|--------|---------|
| 开发测试 | 1-2G | G1GC | 可选 | 3 | < 10万 |
| 生产（标准） | 4-8G | G1GC | 必需 | 3-5 | < 100万 |
| 生产（大规模） | 16-32G | ZGC | 必需 | 5-7 | < 500万 |
| 生产（超大规模） | 32-64G | ZGC | 必需（NVMe） | 7-13 | < 1000万 |

### 注意事项

- swap 一定要关或设极小的 swappiness（ZooKeeper 内存访问很频繁，swap 导致性能暴跌）
- 事务日志和快照放在不同磁盘上（一个是顺序写，一个是随机写/读，互相干扰）
- 堆内存不建议超过 64G（GC 压力太大）

### 常见踩坑经验

**故障 1：增大堆内存后 Full GC 更频繁**

现象：把堆内存从 4G 增大到 16G 后，Full GC 次数不降反升。

根因：堆内存越大，G1GC 的初始标记和并发标记阶段耗时越长。如果 ZooKeeper 中的 ZNode 数量和数据量没有相应增长，增大堆内存只是增加了 GC 的压力，没有带来实际收益。

解决方案：根据实际数据量规划堆内存，不要盲目增大。

**故障 2：事务日志和快照在同一磁盘导致 IO 竞争**

现象：ZooKeeper 的写入延迟出现周期性波动，每写入 snapCount/2 次后延迟突增。

根因：快照生成时需要全量序列化 DataTree，同时会有大量随机读写。如果和事务日志在同一磁盘，快照生成期间的 IO 竞争会导致事务日志写入延迟增加。

解决方案：设置 `dataLogDir` 到独立磁盘（SSD），快照仍在 `dataDir`。

### 思考题

1. ZooKeeper 的数据全部存储在内存中，堆内存大小决定了集群可以存储的数据量。假设每个 ZNode 平均占用 200 字节（含路径、数据、Stat），8G 堆内存理论上可以存储约 4000 万个 ZNode。但实际中为什么通常建议不要超过 100 万个 ZNode？
2. G1GC 的 `MaxGCPauseMillis=200` 和 ZGC 的 `<10ms` 暂停时间，对 ZooKeeper 客户端会话有什么影响？如果 ZooKeeper 服务端 GC 暂停了 5 秒，而客户端 Session Timeout 是 10 秒，客户端会话会过期吗？

### 推广计划提示

- **开发**：理解 ZooKeeper 的性能影响因素，有助于在日常开发中写出高效的 ZooKeeper 使用代码（减少 ZNode 数量、控制 Watcher 数量）
- **运维**：调优需要基于压测数据，不要凭空调整。建议每次只改一个参数，对比压测结果
- **架构师**：性能规划需要根据业务预估数据量（ZNode 数、QPS）、预算（SSD 磁盘数、机器配置）和可用性需求（GC 暂停容忍度）
