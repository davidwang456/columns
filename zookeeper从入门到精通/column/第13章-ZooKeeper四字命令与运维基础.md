# 第13章：ZooKeeper 四字命令与运维基础

## 1. 项目背景

### 业务场景

ZooKeeper 集群在生产环境运行了半年，今天突然出现异常——部分客户端连接中断，服务注册发现延迟。作为运维工程师，你需要回答几个问题：

1. ZooKeeper 集群还活着吗？（`ruok`）
2. 谁是 Leader？（`stat`）
3. 集群当前的 QPS 是多少？（`mntr`）
4. 有多少连接的客户端？（`cons`）
5. 注册了多少个 Watcher？（`wchs`）

ZooKeeper 的四字命令就是回答这些问题的最快方式——通过 `nc`（netcat）或者 `telnet` 发送一个 4 字母的命令，ZooKeeper 立即返回状态信息。

### 痛点放大

没有四字命令，排查 ZooKeeper 问题需要：
- 登录每台机器看日志
- 用 JConsole 连接 JMX
- 写专门的监控脚本采集数据

四字命令让运维人员可以在终端**秒级**获取集群的核心指标。

---

## 2. 项目设计

### 剧本式交锋对话

**场景**：小胖负责的 ZooKeeper 集群出现了性能问题，需要排查定位。

**小胖**：我登录到 ZooKeeper 服务器了，怎么查它的状态？

**大师**：最简单的，发个 `ruok` 命令：

```bash
echo ruok | nc 127.0.0.1 2181
# 输出：imok
```

`imok` = I'm OK，表示该节点正常运行。如果没输出，说明进程挂了或者端口不对。

**小白**：那怎么看集群的角色？谁是 Leader？

**大师**：`stat` 命令：

```bash
echo stat | nc 127.0.0.1 2181

# 输出类似：
# ZooKeeper version: 3.9.2-...
# Clients:
#  /127.0.0.1:54321[1](queued=0,recved=1,sent=0)
#  /10.0.0.100:8080[0](queued=0,recved=42,sent=42)
#  /10.0.0.101:8080[1](queued=0,recved=100,sent=100)
# Latency min/avg/max: 0/2/35
# Received: 15420
# Sent: 15420
# Connections: 3
# Outstanding: 0
# Zxid: 0x300000042
# Mode: leader       ← 关键信息：该节点的角色
# Node count: 152
# ...
```

看 `Mode` 这一行：`leader` / `follower` / `observer` / `standalone`。

**小胖**：那怎么获取集群的监控指标？我想知道集群当前的性能和健康状态。

**大师**：`mntr`（monitor）是运维最常用的命令：

```bash
echo mntr | nc 127.0.0.1 2181

# 输出：
# zk_version  3.9.2
# zk_avg_latency  2     ← 平均处理延迟（毫秒）
# zk_max_latency  35    ← 最大处理延迟（毫秒）
# zk_min_latency  0     ← 最小处理延迟（毫秒）
# zk_packets_received  15420  ← 总接收包数
# zk_packets_sent  15420       ← 总发送包数
# zk_num_alive_connections  3  ← 当前活跃连接数
# zk_outstanding_requests  0   ← 排队中的请求数（>0 说明有积压）
# zk_server_state  leader      ← 节点角色
# zk_znode_count  152          ← ZNode 总数
# zk_watch_count  12           ← Watcher 总数
# zk_ephemerals_count  8       ← 临时节点总数
# zk_approximate_data_size  4352  ← 数据总大小（字节）
# zk_open_file_descriptor_count  42  ← 打开的文件描述符数
# zk_max_file_descriptor_count  1024 ← 最大文件描述符数
```

**小白**：那怎么看 Watcher 信息？有时候 Watcher 泄漏会导致性能问题。

**大师**：有三个 Watcher 相关的命令：

```bash
# 查看 Watcher 总数
echo wchs | nc 127.0.0.1 2181
# 输出：watch_count: 12

# 按路径列出 Watcher（3.5+）
echo wchc | nc 127.0.0.1 2181
# 输出：
# /services/order-service
# /services/user-service
# /config/db-url

# 按会话列出 Watcher（3.5+）
echo wchp | nc 127.0.0.1 2181
# 输出：按会话分组显示 Watcher 路径
```

**小胖**：之前第 5 章说过四字命令有安全风险？怎么控制？

**大师**：从 ZooKeeper 3.5 开始，四字命令默认被**白名单控制**。只有白名单中的命令才能执行：

```properties
# zoo.cfg
# 允许所有命令（生产环境不推荐）
4lw.commands.whitelist=*

# 只允许特定的命令（推荐）
4lw.commands.whitelist=ruok,stat,mntr,cons,conf,wchs
```

> **技术映射**：四字命令 = Docker 的 healthcheck + ps + top，mntr = 系统监控面板，wchs = 事件监听器列表

---

## 3. 项目实战

### 环境准备

- ZooKeeper 3.9.x 集群运行中
- `nc`（netcat）或 `telnet` 工具
- Windows 用户可以使用 PowerShell 的 `Test-NetConnection` 或下载 `nc` 工具

### 分步实现

#### 步骤 1：命令速查表

```bash
# 1. 服务状态
echo ruok | nc 127.0.0.1 2181   # imok → 服务正常
echo srvr | nc 127.0.0.1 2181   # 服务端详细信息（类似 stat 的精简版）
echo stat | nc 127.0.0.1 2181   # 详细状态 + 角色
echo envi | nc 127.0.0.1 2181   # 环境变量
echo conf | nc 127.0.0.1 2181   # 配置信息

# 2. 连接信息
echo cons | nc 127.0.0.1 2181   # 所有客户端连接的详细信息
echo crst | nc 127.0.0.1 2181   # 重置连接统计计数

# 3. Watcher
echo wchs | nc 127.0.0.1 2181   # Watcher 总数
echo wchc | nc 127.0.0.1 2181   # 按路径列出 Watcher
echo wchp | nc 127.0.0.1 2181   # 按会话列出 Watcher

# 4. 监控指标
echo mntr | nc 127.0.0.1 2181   # 监控指标（最常用）

# 5. 其他
echo dump | nc 127.0.0.1 2181   # 临时节点和会话信息
echo srst | nc 127.0.0.1 2181   # 重置服务统计
echo isro | nc 127.0.0.1 2181   # 是否只读模式（rw → 正常, ro → 只读）
```

#### 步骤 2：编写健康检查脚本

创建 `zk-health-check.sh`：

```bash
#!/bin/bash

# ZooKeeper 集群健康检查脚本
# 用法: ./zk-health-check.sh <zk_host:port> [zk_host:port ...]

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

check_node() {
    local host=$1
    local port=$2
    local node="$host:$port"

    # ruok 检查
    local ruok=$(echo ruok | nc -w 3 "$host" "$port" 2>/dev/null)
    if [ "$ruok" != "imok" ]; then
        echo -e "[${RED}DOWN${NC}] $node - 服务无响应"
        return 1
    fi

    # 获取监控指标
    local mntr=$(echo mntr | nc -w 3 "$host" "$port" 2>/dev/null)

    # 提取关键指标
    local role=$(echo "$mntr" | grep "zk_server_state" | awk '{print $2}')
    local avg_latency=$(echo "$mntr" | grep "zk_avg_latency" | awk '{print $2}')
    local connections=$(echo "$mntr" | grep "zk_num_alive_connections" | awk '{print $2}')
    local outstanding=$(echo "$mntr" | grep "zk_outstanding_requests" | awk '{print $2}')
    local znode_count=$(echo "$mntr" | grep "zk_znode_count" | awk '{print $2}')
    local watch_count=$(echo "$mntr" | grep "zk_watch_count" | awk '{print $2}')
    local ephemerals=$(echo "$mntr" | grep "zk_ephemerals_count" | awk '{print $2}')
    local fd_used=$(echo "$mntr" | grep "zk_open_file_descriptor_count" | awk '{print $2}')
    local fd_max=$(echo "$mntr" | grep "zk_max_file_descriptor_count" | awk '{print $2}')
    local data_size=$(echo "$mntr" | grep "zk_approximate_data_size" | awk '{print $2}')

    # 检查阈值
    local status="${GREEN}OK${NC}"
    local warnings=""

    if [ "$outstanding" -gt 100 ]; then
        status="${YELLOW}WARN${NC}"
        warnings+="outstanding=$outstanding(>100) "
    fi
    if [ "$avg_latency" -gt 50 ]; then
        status="${YELLOW}WARN${NC}"
        warnings+="latency=$avg_latency(>50ms) "
    fi
    if [ "$fd_used" -gt $((fd_max * 9 / 10)) ]; then
        status="${YELLOW}WARN${NC}"
        warnings+="fd_used=$fd_used/$fd_max "
    fi

    echo -e "[${status}] $node"
    echo "  Role: $role | Latency: ${avg_latency}ms | Connections: $connections"
    echo "  Outstanding: $outstanding | Znodes: $znode_count | Watchers: $watch_count"
    echo "  Ephemerals: $ephemerals | DataSize: ${data_size}bytes | FD: ${fd_used}/${fd_max}"
    if [ -n "$warnings" ]; then
        echo -e "  ${YELLOW}⚠ Warnings: $warnings${NC}"
    fi
    return 0
}

# 主逻辑
if [ $# -eq 0 ]; then
    echo "用法: $0 <host:port> [host:port ...]"
    echo "示例: $0 127.0.0.1:2181 127.0.0.1:2182 127.0.0.1:2183"
    exit 1
fi

echo "=== ZooKeeper 集群健康检查 ==="
echo "时间: $(date)"
echo ""

for node in "$@"; do
    host="${node%%:*}"
    port="${node##*:}"
    check_node "$host" "$port"
    echo ""
done
```

**使用方法**：

```bash
chmod +x zk-health-check.sh
./zk-health-check.sh 127.0.0.1:2181 127.0.0.1:2182 127.0.0.1:2183
```

**预期输出**：

```
=== ZooKeeper 集群健康检查 ===
时间: Thu Mar 14 10:00:00 CST 2025

[OK] 127.0.0.1:2181
  Role: leader | Latency: 2ms | Connections: 10
  Outstanding: 0 | Znodes: 152 | Watchers: 12
  Ephemerals: 8 | DataSize: 4352bytes | FD: 42/1024
```

#### 步骤 3：将 mntr 输出集成到 Prometheus

`mntr` 输出本身就是 key-value 对，非常适合转换为 Prometheus 指标格式：

```bash
#!/bin/bash
# zk-exporter.sh —— 将 mntr 转为 Prometheus 格式
# 配合 Prometheus node_exporter textfile collector 使用

HOST=$1
PORT=$2
OUTPUT_DIR="/var/lib/prometheus/node-exporter"

if [ -z "$HOST" ] || [ -z "$PORT" ]; then
    echo "Usage: $0 <host> <port>"
    exit 1
fi

METRICS=$(echo mntr | nc -w 3 "$HOST" "$PORT" 2>/dev/null)
if [ -z "$METRICS" ]; then
    echo "zk_down{host=\"$HOST\",port=\"$PORT\"} 1" > "$OUTPUT_DIR/zookeeper.prom"
    exit 1
fi

# 转换格式
echo "# HELP zk_up ZooKeeper server is up"
echo "# TYPE zk_up gauge"
echo "zk_up{host=\"${HOST}\",port=\"${PORT}\"} 1"
echo ""

echo "$METRICS" | while IFS=$'\t' read -r key value; do
    # 跳过非数字值
    case $key in
        zk_packets_received|zk_packets_sent|zk_num_alive_connections|zk_outstanding_requests|zk_znode_count|zk_watch_count|zk_ephemerals_count|zk_approximate_data_size|zk_open_file_descriptor_count|zk_max_file_descriptor_count|zk_avg_latency|zk_max_latency|zk_min_latency)
            echo "# HELP $key ZooKeeper metric"
            echo "# TYPE $key gauge"
            echo "$key{host=\"${HOST}\",port=\"${PORT}\",role=\"${ROLE}\"} $value"
            ;;
    esac
done
```

#### 步骤 4：JVM 监控命令速查

```bash
# 查看 ZooKeeper 进程的 JVM 信息
# 1. 找到 ZooKeeper 进程 PID
jps -l | grep QuorumPeerMain
# 输出：12345 org.apache.zookeeper.server.quorum.QuorumPeerMain

PID=12345

# 2. 查看 JVM 堆内存使用
jmap -heap $PID

# 3. 查看 GC 情况
jstat -gcutil $PID 1000 5  # 每秒输出一次，共 5 次

# 4. 查看线程信息
jstack $PID | head -100

# 5. 查看 JVM 参数
jinfo $PID
```

### 可能遇到的坑

| 问题 | 原因 | 解决 |
|------|------|------|
| `nc: command not found` | 未安装 netcat | `apt install netcat` / `yum install nc` |
| 命令无输出 | 四字命令未在白名单中 | 配置 `4lw.commands.whitelist=*` |
| `is not executed because it is not in the whitelist` | 3.5+ 默认白名单为空 | 配置 `4lw.commands.whitelist=stat,mntr,ruok,...` |
| Windows 无 `nc` 命令 | netcat 非 Windows 原生 | 使用 PowerShell 或 Cygwin |

---

## 4. 项目总结

### 四字命令速查表

| 命令 | 用途 | 生产推荐 |
|------|------|---------|
| `ruok` | 是否存活（返回 imok） | ★★★★★ |
| `mntr` | 监控指标（延迟、连接数、ZNode 数等） | ★★★★★ |
| `stat` | 详细状态 + 角色 | ★★★★★ |
| `srvr` | 服务端统计信息 | ★★★★ |
| `cons` | 客户端连接详情 | ★★★★ |
| `wchs` | Watcher 统计 | ★★★ |
| `conf` | 配置参数 | ★★★ |
| `envi` | 环境变量 | ★★ |
| `dump` | 临时节点和会话 | ★★ |
| `crst` | 重置连接统计 | ★ |
| `srst` | 重置服务统计 | ★ |
| `wchc/wchp` | Watcher 详情（大数据量时性能差） | ★ |

### 适用场景

- **日常巡检**：`ruok` + `mntr`，确认集群健康
- **故障排查**：`cons` 看客户端连接、`stat` 看角色、`mntr` 看延迟和积压
- **容量规划**：`mntr` 的 ZNode 数量、数据大小、连接数
- **监控报警**：`mntr` 输出集成 Prometheus

### 注意事项

- ZooKeeper 3.5+ 默认白名单为空，需要显式配置
- `wchc` / `wchp` 在 Watcher 数量多时（>1万）会导致 ZooKeeper 暂停
- `dump` 在会话和临时节点多时也会对性能有影响
- 不适合在脚本中高频调用（建议通过 Prometheus 拉取替代）

### 常见踩坑经验

**故障 1：wchc 导致 ZooKeeper 假死**

现象：运维执行 `echo wchc | nc zk 2181`，ZooKeeper 进程 CPU 飙升到 100%，服务暂停响应。

根因：当集群有数万个 Watcher 时，`wchc` 需要遍历所有 Watcher 信息并序列化输出，这个过程会持有内部锁，阻塞正常请求处理。

解决方案：只在 Watcher 数量少时使用，或者用 JMX 替代。

**故障 2：四字命令连接不上但服务正常**

现象：`echo ruok | nc zk 2181` 没有输出，但应用连接正常。

根因：ZooKeeper 的 `nc` 连接需要快速发送命令并关闭连接。如果 ZooKeeper 的 NIO 连接队列满了，`nc` 连接可能被拒绝。改用 `telnet` 重试。

### 思考题

1. 从 `mntr` 输出中，如何判断 ZooKeeper 集群是否"过载"？列出 3 个关键指标和它们的阈值。
2. `wchc` 和 `wchp` 都可能对 ZooKeeper 性能产生影响。如果要排查 Watcher 泄漏，除了四字命令还有哪些替代方案？

### 推广计划提示

- **开发**：了解四字命令有助于快速判断 ZooKeeper 服务状态
- **运维**：将本文的健康检查脚本集成到运维监控系统中（Zabbix/Prometheus）
- **测试**：压测时使用 `mntr` 观察 ZooKeeper 的延迟和积压指标
