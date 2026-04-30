# 第27章：集群监控与运维——JMX 与 Prometheus

## 1. 项目背景

### 业务场景

ZooKeeper 集群在生产环境运行了一段时间，你需要回答几个关键问题：

- 当前的 QPS 是多少？有没有超过集群的负载能力？
- 平均延迟是多少？有没有慢请求？
- 连接数是否正常？有没有客户端连接泄漏？
- Watcher 数量是否在合理范围内？
- 有没有 OOM 风险？

第 13 章的四字命令可以获取这些信息，但那是**手动查询**的。生产环境需要**自动采集、可视化、告警**的监控体系。

### 痛点放大

没有监控体系的问题：

- 容量规划靠猜：不知道集群还能承受多少压力
- 故障发现靠用户投诉：客户端报错了才知道 ZooKeeper 有问题
- 根因分析缺数据：故障发生时没有延迟、QPS 等指标的历史数据

---

## 2. 项目设计

### 剧本式交锋对话

**场景**：小胖的 ZooKeeper 集群又出问题了，但他找不到原因。

**小胖**：集群慢了好几分钟，然后恢复正常了。我不知道发生了什么，因为当时我没有在机器上执行四字命令。

**大师**：所以你需要**持续监控**。ZooKeeper 的监控主要有三种方式：

| 方式 | 优点 | 缺点 |
|------|------|------|
| **四字命令** | 随 ZooKeeper 内置，零配置 | 手动执行，无法持续采集 |
| **JMX** | 丰富的 MBean，可远程采集 | 需要配置 JMX 参数 |
| **Prometheus Exporter** | 生态好，Grafana 可视化 | 额外组件 |

**推荐方案：JMX Exporter + Prometheus + Grafana**

```
┌──────────────┐     ┌──────────────────┐     ┌───────────┐     ┌─────────┐
│ ZooKeeper    │────▶│ JMX Exporter     │────▶│ Prometheus │────▶│ Grafana  │
│ (JMX MBean)  │     │ (HTTP /metrics)  │     │ (TSDB)     │     │ (大盘)   │
└──────────────┘     └──────────────────┘     └───────────┘     └─────────┘
                                                      │
                                                      ▼
                                                 ┌──────────┐
                                                 │ Alertmanager│
                                                 │ (告警)     │
                                                 └──────────┘
```

**小白**：JMX 暴露了哪些关键指标？我们需要关注什么？

**大师**：ZooKeeper 的 JMX MBean 分为几类：

**关键监控指标一览：**

| 分类 | 指标 | 含义 | 告警阈值 |
|------|------|------|---------|
| **延迟** | AvgLatency | 平均请求延迟（ms） | > 50ms |
| | MaxLatency | 最大请求延迟（ms） | > 200ms |
| **流量** | PacketsReceived | 每秒接收的包数 | — |
| | PacketsSent | 每秒发送的包数 | — |
| | OutstandingRequests | 排队中的请求数 | > 100 |
| **连接** | NumAliveConnections | 当前活跃连接数 | — |
| | MinSessionTimeout | 最小会话超时 | — |
| | MaxSessionTimeout | 最大会话超时 | — |
| **数据** | ZNodeCount | ZNode 总数 | 视内存而定 |
| | WatchCount | Watcher 总数 | > 10000 |
| | EphemeralsCount | 临时节点数 | — |
| | DataSize | 数据总大小（字节） | — |
| **系统** | OpenFileDescriptorCount | 已打开文件描述符 | > 80% |
| | MaxFileDescriptorCount | 最大文件描述符 | — |

---

## 3. 项目实战

### 环境准备

- ZooKeeper 3.9.x 运行中（需开启 JMX）
- Docker & Docker Compose
- Prometheus + Grafana

### 分步实现

#### 步骤 1：启用 ZooKeeper JMX

ZooKeeper 默认启用 JMX，但需要配置远程访问。

创建 `zk-jmx.yml`（Docker Compose 扩展）：

```yaml
version: '3.8'

services:
  zoo1:
    image: zookeeper:3.9
    hostname: zoo1
    ports:
      - "2181:2181"
      - "8080:8080"    # Admin Server
    environment:
      ZOO_MY_ID: 1
      ZOO_SERVERS: server.1=0.0.0.0:2888:3888;2181 server.2=zoo2:2888:3888;2181 server.3=zoo3:2888:3888;2181
      ZOO_4LW_COMMANDS_WHITELIST: "*"
      # JMX 配置
      ZOO_JMX_ENABLED: "true"
      JMXPORT: "1099"
      # 开启 Admin Server（REST API）
      ZOO_ADMIN_SERVER_PORT: "8080"

  zoo2:
    image: zookeeper:3.9
    hostname: zoo2
    ports:
      - "2182:2181"
      - "8082:8080"
    environment:
      ZOO_MY_ID: 2
      ZOO_SERVERS: server.1=zoo1:2888:3888;2181 server.2=0.0.0.0:2888:3888;2181 server.3=zoo3:2888:3888;2181
      ZOO_4LW_COMMANDS_WHITELIST: "*"
      ZOO_JMX_ENABLED: "true"
      JMXPORT: "1099"
      ZOO_ADMIN_SERVER_PORT: "8080"

  zoo3:
    image: zookeeper:3.9
    hostname: zoo3
    ports:
      - "2183:2181"
      - "8083:8080"
    environment:
      ZOO_MY_ID: 3
      ZOO_SERVERS: server.1=zoo1:2888:3888;2181 server.2=zoo2:2888:3888;2181 server.3=0.0.0.0:2888:3888;2181
      ZOO_4LW_COMMANDS_WHITELIST: "*"
      ZOO_JMX_ENABLED: "true"
      JMXPORT: "1099"
      ZOO_ADMIN_SERVER_PORT: "8080"

  # JMX Exporter (将 JMX 转为 Prometheus 格式)
  jmx-exporter-1:
    image: bitnami/jmx-exporter:latest
    ports:
      - "9101:9101"
    volumes:
      - ./jmx-config.yml:/opt/bitnami/jmx-exporter/config.yml
    command: ["9101", "/opt/bitnami/jmx-exporter/config.yml"]
    network_mode: "host"

  prometheus:
    image: prom/prometheus:latest
    ports:
      - "9090:9090"
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml

  grafana:
    image: grafana/grafana:latest
    ports:
      - "3000:3000"
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=admin
```

创建 `jmx-config.yml`（JMX Exporter 配置）：

```yaml
---
lowercaseOutputName: true
lowercaseOutputLabelNames: true
whitelistObjectNames:
  - "org.apache.ZooKeeperService:*"
rules:
  # 将 ZooKeeper 指标映射到 Prometheus 格式
  - pattern: "org.apache.ZooKeeperService<name0=ReplicatedServer_id(\\d+)><>(\\w+)"
    name: "zk_$2"
    type: UNTYPED
  - pattern: "org.apache.ZooKeeperService<name0=ReplicatedServer_id(\\d+),name1=replica.(\\d+)><>(\\w+)"
    name: "zk_replica_$3"
    type: UNTYPED
    labels:
      replicaId: "$2"
  - pattern: "org.apache.ZooKeeperService<name0=ReplicatedServer_id(\\d+),name1=replica.(\\d+),name2=(\\w+)><>(\\w+)"
    name: "zk_$4"
    type: UNTYPED
    labels:
      replicaId: "$2"
      memberType: "$3"
```

创建 `prometheus.yml`：

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: 'zookeeper'
    static_configs:
      - targets:
        - 'localhost:9101'  # JMX Exporter
        - 'localhost:8080'  # ZooKeeper Admin Server (可选)
```

#### 步骤 2：使用 ZooKeeper Admin Server（REST API）

ZooKeeper 3.5+ 内置了 Admin Server（HTTP，默认端口 8080）：

```bash
# 获取集群健康状态
curl http://127.0.0.1:8080/commands/stat
# 输出 JSON 格式的状态信息

# 获取监控指标
curl http://127.0.0.1:8080/commands/monitor
# 输出 JSON 格式的 mntr 指标

# 获取连接信息
curl http://127.0.0.1:8080/commands/connections
```

#### 步骤 3：编写 ZooKeeper 指标采集程序

创建 `MetricsCollector.java`：

```java
package com.zkdemo.monitor;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.Timer;
import java.util.TimerTask;

public class MetricsCollector {
    private static final String ADMIN_SERVER_URL = "http://127.0.0.1:8080/commands/monitor";
    private static final DateTimeFormatter FORMATTER =
            DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");

    public static void main(String[] args) throws Exception {
        System.out.println("=== ZooKeeper 指标采集器 ===\n");

        Timer timer = new Timer(true);
        timer.scheduleAtFixedRate(new TimerTask() {
            @Override
            public void run() {
                try {
                    collectMetrics();
                } catch (Exception e) {
                    System.err.println("采集失败: " + e.getMessage());
                }
            }
        }, 0, 15000); // 每 15 秒采集一次

        System.out.println("按 Enter 停止...");
        System.in.read();
        timer.cancel();
    }

    static void collectMetrics() throws Exception {
        URL url = new URL(ADMIN_SERVER_URL);
        HttpURLConnection conn = (HttpURLConnection) url.openConnection();
        conn.setRequestMethod("GET");

        BufferedReader reader = new BufferedReader(
                new InputStreamReader(conn.getInputStream()));
        StringBuilder response = new StringBuilder();
        String line;
        while ((line = reader.readLine()) != null) {
            response.append(line);
        }
        reader.close();

        // 解析 JSON 并输出关键指标（简化版）
        String json = response.toString();
        String timestamp = LocalDateTime.now().format(FORMATTER);

        System.out.println("[" + timestamp + "]");
        System.out.println("  avg_latency: " + extractValue(json, "zk_avg_latency"));
        System.out.println("  max_latency: " + extractValue(json, "zk_max_latency"));
        System.out.println("  outstanding: " + extractValue(json, "zk_outstanding_requests"));
        System.out.println("  connections: " + extractValue(json, "zk_num_alive_connections"));
        System.out.println("  znodes: " + extractValue(json, "zk_znode_count"));
        System.out.println("  watches: " + extractValue(json, "zk_watch_count"));
        System.out.println("  ephemerals: " + extractValue(json, "zk_ephemerals_count"));
        System.out.println("  data_size: " + extractValue(json, "zk_approximate_data_size") + " bytes");
        System.out.println("  mode: " + extractValue(json, "zk_server_state"));
        System.out.println("---");
    }

    static String extractValue(String json, String key) {
        int keyIndex = json.indexOf(key);
        if (keyIndex == -1) return "N/A";
        int valueStart = json.indexOf(":", keyIndex) + 1;
        // 处理 JSON 值（可能是数字或字符串）
        StringBuilder value = new StringBuilder();
        for (int i = valueStart; i < json.length(); i++) {
            char c = json.charAt(i);
            if (c == ',' || c == '}' || c == ']') break;
            if (c != '"' && c != ' ') value.append(c);
        }
        return value.toString().trim();
    }
}
```

#### 步骤 4：Grafana 告警规则配置

创建 `alert-rules.yml`：

```yaml
groups:
  - name: zookeeper
    rules:
      # 1. 连接数突降告警（可能网络分区）
      - alert: ZKConnectionsDrop
        expr: zk_num_alive_connections < 10
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "ZooKeeper 连接数异常低"

      # 2. 请求积压告警
      - alert: ZKOutstandingRequests
        expr: zk_outstanding_requests > 100
        for: 30s
        labels:
          severity: warning
        annotations:
          summary: "ZooKeeper 请求积压 {{ $value }}"

      # 3. 写延迟高告警
      - alert: ZKHighLatency
        expr: zk_avg_latency > 100
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "ZooKeeper 写延迟 {{ $value }}ms"

      # 4. Followers 数量异常
      - alert: ZKFollowersCount
        expr: zk_followers < 2
        for: 30s
        labels:
          severity: critical
        annotations:
          summary: "Follower 数量不足（当前 {{ $value }}）"

      # 5. 文件描述符告警
      - alert: ZKFileDescriptors
        expr: zk_open_file_descriptor_count / zk_max_file_descriptor_count > 0.8
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "ZooKeeper 文件描述符使用率超过 80%"
```

#### 步骤 5：部署监控栈

```bash
# 1. 启动 ZooKeeper 集群（带 JMX）
docker-compose -f zk-jmx.yml up -d

# 2. 验证 JMX Exporter
curl http://127.0.0.1:9101/metrics | head -20

# 3. 验证 Prometheus
curl http://127.0.0.1:9090/api/v1/query?query=zk_znode_count

# 4. 打开 Grafana
# 浏览器访问 http://localhost:3000 (admin/admin)
# 添加 Prometheus 数据源: http://prometheus:9090
# 导入 ZooKeeper Dashboard (Grafana Dashboards ID: 15677)

# 5. 验证 Admin Server REST API
curl http://127.0.0.1:8080/commands/stat | python -m json.tool
```

### 测试验证

```bash
# 1. 验证 JMX 指标
curl http://127.0.0.1:9101/metrics | grep -E "zk_"

# 2. 验证 Prometheus 指标
curl http://127.0.0.1:9090/api/v1/query?query=zk_avg_latency

# 3. 创建一些 ZooKeeper 负载观察监控变化
for i in $(seq 1 100); do
  ./bin/zkCli.sh -server 127.0.0.1:2181 create /test-node-$i "data"
done
```

### 可能遇到的坑

| 问题 | 原因 | 解决 |
|------|------|------|
| JMX 端口未开放 | Docker 容器未映射端口 | 在 Compose 文件中映射 1099 |
| JMX Exporter 连接不上 | JMX 配置未生效 | 检查 ZooKeeper JMX 是否启用 |
| Prometheus 抓取超时 | 指标过多时 ZooKeeper 响应慢 | 增大 scrape_timeout |

---

## 4. 项目总结

### 监控体系三件套

| 组件 | 作用 | 端口 |
|------|------|------|
| ZooKeeper JMX | 暴露原生指标 | 1099 |
| JMX Exporter | 转为 Prometheus 格式 | 9101 |
| Prometheus | 时序数据库 + 告警 | 9090 |
| Grafana | 可视化 Dashboard | 3000 |
| Admin Server | REST API 监控 | 8080 |

### 核心告警规则

| 告警名称 | 阈值 | 说明 |
|---------|------|------|
| 连接数异常 | < 10 | 可能网络分区 |
| 请求积压 | outstanding > 100 | 写入压力过大 |
| 延迟高 | avg_latency > 50ms | 磁盘 IO 或 GC 问题 |
| Follower 不足 | followers < 2 | 集群故障 |
| 文件描述符 | > 80% | 连接泄漏 |

### 注意事项

- JMX 在低版本 ZooKeeper 中默认关闭，需要显式开启
- Admin Server 默认不鉴权，生产环境应配置防火墙或反向代理
- `mntr` 四字命令和 Admin Server 返回的指标一致

### 常见踩坑经验

**故障 1：JMX Exporter 配置导致 ZooKeeper 启动失败**

现象：配置 JMX Exporter 后，ZooKeeper 启动时报 `Address already in use`。

根因：JMX Exporter 的端口和 ZooKeeper Admin Server 端口冲突。Exporter 默认使用 8080 端口。

解决方案：将 Exporter 端口改为其他端口（如 9101）。

**故障 2：Prometheus 采集不到最新数据**

现象：Prometheus 显示的指标一直不变，即使 ZooKeeper 数据变了。

根因：Prometheus 的 `scrape_interval` 和 ZooKeeper 的 JMX 刷新频率不匹配。JMX MBean 的值变化后不会立即反映。

解决方案：减小 `scrape_interval` 到 10-15 秒，或使用 `scrape_timeout` 搭配。

### 思考题

1. ZooKeeper 的 `outstanding_requests > 0` 意味着有请求在排队。什么情况下 outstanding_requests 会持续增长？（提示：和磁盘 IO 和 GC 有关）
2. JMX 和四字命令 `mntr` 返回的指标值有什么区别？在什么情况下两者的值会不同？（提示：JMX 是瞬时快照，四字命令是累计值）

### 推广计划提示

- **开发**：开发环境可以运行 MetricsCollector 快速验证 ZooKeeper 集群状态
- **运维**：建议配置以上 5 条告警规则，配合 Grafana Dashboard 实现集群可视化监控
- **架构师**：监控体系是 ZooKeeper 运维的基础设施，建议在生产环境部署前完成
