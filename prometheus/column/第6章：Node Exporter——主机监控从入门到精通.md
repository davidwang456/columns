# 第6章：Node Exporter——主机监控从入门到精通

## 一、项目背景

运维团队刚把 Prometheus Server 部署好，按照第2章的配置跑起来了，但打开 Web UI，监控大盘空空如也——Targets 列表里只有 Prometheus 自己孤零零一条记录。组长走过来拍了拍小张的肩膀："先把所有服务器的 CPU、内存、磁盘监控搞起来，这是最基本的面子工程。"

小张调研后发现，Prometheus 生态中负责主机监控的组件叫 **Node Exporter**，它运行在每台被监控的机器上，暴露 `/metrics` 端点供 Prometheus 拉取。听起来很简单——下载、运行、配置 Target 三连搞定。但实际一上手就踩了连环坑：

- `/metrics` 端点输出超过 **1000 行**指标，密密麻麻让人头晕——哪些才是核心指标？哪些可以关掉？
- `node_cpu_seconds_total` 带有 `mode` 标签，取值有 idle、user、system、iowait、irq、softirq、steal、nice 等 8 种——如何组合才能算出正确的 CPU 使用率？
- 内存指标有 `MemTotal`、`MemFree`、`MemAvailable`、`Buffers`、`Cached`……到底看哪个？"free memory is wasted memory" 这句话把刚入行的小弟们绕晕了。
- 几台 Windows 服务器的配置照搬过去，怎么都不 work——后来才发现 Node Exporter **不支持 Windows**，得换成 `windows_exporter`。
- `textfile collector` 又是什么？怎么用它监控自定义脚本（比如定时备份任务）的执行状态？

本章将围绕这些真实痛点，带你从 Node Exporter 的 Collector 架构入手，深入理解 CPU、内存、磁盘、网络四大核心指标的 PromQL 表达，最后通过 Textfile Collector 打通自定义监控的"最后一公里"。

---

## 二、剧本式交锋对话

**角色**：小胖（只会复制粘贴的新手）、小白（踩过坑但一知半解的熟手）、大师（架构师，精通 Linux 内核和 Prometheus）

> **场景**：运维工位上，小胖对着 `curl localhost:9100/metrics` 输出的上千行数据发呆。

**小胖**：大师救命！我就按官方文档 `./node_exporter &` 启动了，结果 1000 多行指标，根本找不到哪个是 CPU 使用率。Prometheus 不是号称简单吗？

**大师**：你先搞清楚 Node Exporter 的架构——它由一堆 **Collector** 拼装而成，每个 Collector 负责一类指标。用 `--no-collector.cpu` 可以关掉 CPU 采集，用 `--collector.filesystem` 可以开启文件系统采集。想知道开启了哪些 Collector？直接看 `/metrics` 里 `node_exporter_build_info` 或者用 `node_exporter --help 2>&1 | grep collector`。

**小白**：这个我知道！我上次做了瘦身——把 `hwmon`、`ipvs`、`bonding` 这些没用的 Collector 全关了，指标量从 1200 行降到 600 多行：

```bash
node_exporter \
  --no-collector.hwmon \
  --no-collector.ipvs \
  --no-collector.bonding \
  --no-collector.infiniband
```

**大师**：不错，按需启停是生产环境的最佳实践。不过你刚才提到的 CPU 使用率问题才是重点。看 `node_cpu_seconds_total{mode="idle"}` 这个指标，注意 **mode=idle 并不是"当前处于空闲状态"**，而是 Linux 内核中 idle 线程累积消耗的 CPU 时间。每条 CPU 核都有一个 idle 线程，当没有其他任务可运行时，内核就跑 idle 线程——它的时间累积值就是该核的空闲时间。

**小胖**：所以 CPU 使用率 = (1 - idle 时间占比) × 100%？那我直接这样写行不行？

```promql
100 - (rate(node_cpu_seconds_total{mode="idle"}[5m]) * 100)
```

**大师**：方向对了，但有两个细节要修正。第一，`rate()` 算出来的是每秒增长量，你需要除以总核数才能归一化；用 `avg` 聚合每台机器的所有核：

```promql
100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[5m])) by (instance) * 100)
```

第二，**用 `mode="idle"` 而不是 `mode!="idle"`**——因为 `mode` 除了你熟悉的 user/system/iowait 之外，还有 guest、steal（虚拟化环境被宿主机偷走的时间）、nice 等。用 `mode="idle"` 取反最准确，一行代码覆盖所有非空闲模式。

**小白**：内存这块我也困惑了很久。`node_memory_MemFree_bytes` 经常只剩下几百 MB，但 `node_memory_MemAvailable_bytes` 还有好几个 GB，系统明明跑得好好的——到底以哪个为准？

**大师**：这是经典误区。Linux 内核的原则是 **"free memory is wasted memory"**——空闲内存会被拿来当 Page Cache 和 Buffer，加速文件读写。真正的可用内存计算公式是：

```
MemAvailable = MemFree + (可回收的 Page Cache + Buffer 部分)
```

所以你的监控 PromQL 必须用 `MemAvailable`，而不是 `MemFree`：

```promql
(1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes) * 100
```

否则你的告警会频繁误报"内存不足"。这是 P1 级事故的根源，很多新手都在这里翻过车。

**小胖**：磁盘和网络指标也帮我梳理下？尤其是磁盘，我跑出来一大堆 mountpoint，tmpfs、squashfs、/boot 都在里面……

**大师**：磁盘使用率查询必须带**标签过滤**，否则容器 overlay 文件系统和临时文件系统都会混进来：

```promql
(node_filesystem_size_bytes{fstype!~"tmpfs|squashfs",mountpoint!~"/boot.*|/run.*"} 
 - node_filesystem_avail_bytes{fstype!~"tmpfs|squashfs",mountpoint!~"/boot.*|/run.*"})
/ node_filesystem_size_bytes{fstype!~"tmpfs|squashfs",mountpoint!~"/boot.*|/run.*"} * 100
```

网络流量也一样：`node_network_receive_bytes_total` 的 `device` 标签包含 `lo`（loopback）、`docker0`、`veth*` 等虚拟网卡，必须排除：

```promql
rate(node_network_receive_bytes_total{device!~"lo|docker.*|veth.*"}[5m]) * 8
```

乘以 8 是把 Bytes 转成 bits，得到的是 **bps**（每秒比特数），这才是网络运维的通用单位。

**小白**：对了，我们有台 Windows 服务器，Node Exporter 跑不起来……

**大师**：Node Exporter 深度依赖 Linux 的 `/proc` 和 `/sys` 文件系统，**不支持 Windows**。微软平台的方案是 [windows_exporter](https://github.com/prometheus-community/windows_exporter)，安装后同样暴露 `:9182/metrics`，指标体系类似但有差异——比如 CPU 指标是 `windows_cpu_time_total{mode="idle"}` 而不是 `node_cpu_seconds_total`。记得在 Prometheus 的 `relabel_configs` 里给 Windows 机器加 `os="windows"` 标签，方便后续 PromQL 区分。

---

## 三、项目实战

### 环境准备

| 角色 | 系统 | IP | 说明 |
|------|------|----|------|
| Prometheus Server | Linux (CentOS 7+) | 10.0.0.10 | 延续第2章环境 |
| web-server | Linux (CentOS 7+) | 10.0.0.20 | 部署 Node Exporter |
| db-server | Linux (CentOS 7+) | 10.0.0.30 | 部署 Node Exporter |

至少准备两台 Linux 机器。Windows/Mac 用户推荐使用 Linux 虚拟机或 `prom/node-exporter` 的 Docker 镜像。

### 步骤1：部署 Node Exporter

**方式A：二进制部署（推荐生产环境）**

```bash
# 下载 Node Exporter（以 1.8.2 为例）
wget https://github.com/prometheus/node_exporter/releases/download/v1.8.2/node_exporter-1.8.2.linux-amd64.tar.gz
tar -xzf node_exporter-1.8.2.linux-amd64.tar.gz
mv node_exporter-1.8.2.linux-amd64/node_exporter /usr/local/bin/

# 创建 systemd 服务文件
cat > /etc/systemd/system/node_exporter.service << 'EOF'
[Unit]
Description=Node Exporter
After=network.target

[Service]
User=nobody
Group=nobody
Type=simple
ExecStart=/usr/local/bin/node_exporter \
  --collector.textfile.directory=/var/lib/node_exporter/textfile_collector \
  --no-collector.hwmon \
  --no-collector.ipvs

[Install]
WantedBy=multi-user.target
EOF

# 创建 textfile collector 目录并启动
mkdir -p /var/lib/node_exporter/textfile_collector
systemctl daemon-reload
systemctl enable --now node_exporter
```

**方式B：Docker 部署（快速体验）**

```bash
# Linux 宿主机使用 --net=host 以获取准确的宿主机网络指标
docker run -d --name node-exporter --net=host \
  -v /var/lib/node_exporter/textfile_collector:/var/lib/node_exporter/textfile_collector \
  prom/node-exporter:v1.8.2 \
  --collector.textfile.directory=/var/lib/node_exporter/textfile_collector

# 验证：查看 /metrics 前 20 行
curl -s http://localhost:9100/metrics | head -20
```

> **注意**：Docker `--net=host` 在 macOS/Windows 上不生效，虚拟机网络会导致宿主机网络指标丢失。开发环境体验 OK，但生产环境请用二进制部署。

### 步骤2：理解核心 CPU 指标

查看 CPU 指标的所有 mode 取值：

```bash
curl -s localhost:9100/metrics | grep node_cpu_seconds_total | \
  awk -F'"' '{print $4}' | sort -u
```

输出大约 8 种 mode：`idle`、`iowait`、`irq`、`nice`、`softirq`、`steal`、`system`、`user`。

CPU 使用率的核心 PromQL（在 Prometheus Web UI 中执行）：

```promql
100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[5m])) by (instance) * 100)
```

为什么不用 `mode!="idle"` 反选？因为 `mode` 标签的取值未来可能扩展（比如新版本内核增加的模式）。`mode="idle"` 取反是**闭包**：无论标签值如何变化，只要不是"真实空闲"，都会被计入使用率。多核机器上 `avg...by(instance)` 会自动计算所有核的平均使用率。

如果只想看**单核**使用率（看是否某个核被打满）：

```promql
100 - (rate(node_cpu_seconds_total{mode="idle"}[5m]) * 100)
```

去掉 `avg` 和 `by(instance)`，每条时间序列代表一个 CPU 核心。

### 步骤3：理解内存指标

核心内存指标对照：

| 指标名 | 含义 |
|--------|------|
| `node_memory_MemTotal_bytes` | 物理内存总量 |
| `node_memory_MemFree_bytes` | 完全空闲的内存（不含 cache） |
| `node_memory_Buffers_bytes` | 内核缓冲区 |
| `node_memory_Cached_bytes` | Page Cache（文件缓存，可回收） |
| `node_memory_MemAvailable_bytes` | **真正可用的内存**（含可回收缓存） |

可用内存百分比：

```promql
node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes * 100
```

**关键提醒**：`MemFree` 永远小于等于 `MemAvailable`。Linux 的 "free memory is wasted memory" 策略下，`MemFree` 很小是正常现象。一旦用 `MemFree` 做告警阈值，系统稍有文件读写就会被误报内存不足。

### 步骤4：磁盘和网络监控

**磁盘使用率**（必须过滤 mountpoint 和 fstype）：

```promql
(node_filesystem_size_bytes{fstype!~"tmpfs|squashfs",mountpoint!~"/boot.*|/run.*"} 
 - node_filesystem_avail_bytes{fstype!~"tmpfs|squashfs",mountpoint!~"/boot.*|/run.*"})
/ node_filesystem_size_bytes{fstype!~"tmpfs|squashfs",mountpoint!~"/boot.*|/run.*"} * 100
```

快速查看所有挂载点：

```promql
node_filesystem_size_bytes
```

在 Web UI 的 Table 视图里检查 `mountpoint` 标签，确认哪些需要过滤。

**网络流量（bps）**：

```promql
# 入站流量 (bit/s)
rate(node_network_receive_bytes_total{device!~"lo|docker.*|veth.*"}[5m]) * 8

# 出站流量 (bit/s)
rate(node_network_transmit_bytes_total{device!~"lo|docker.*|veth.*"}[5m]) * 8
```

排除 `lo`（回环）、`docker*`（Docker 网桥）、`veth*`（容器虚拟网卡），避免出向统计叠加导致带宽计算翻倍（这是生产环境多网卡绑定场景下的经典误告警案例）。

### 步骤5：Textfile Collector 实战

Textfile Collector 是 Node Exporter 的"万能钩子"——任何自定义脚本只要把指标写入指定目录下的 `.prom` 文件，Node Exporter 就会自动暴露它们。

**场景**：监控数据库备份脚本的执行状态。每次备份完成后记录时间戳和耗时。

**1. 编写备份监控脚本** `/opt/scripts/backup_status.sh`：

```bash
#!/bin/bash
# 输出指标到临时文件，再原子移动到 textfile 目录
TEXTFILE_DIR="/var/lib/node_exporter/textfile_collector"
TMP_FILE="${TEXTFILE_DIR}/backup_status.prom.tmp"
FINAL_FILE="${TEXTFILE_DIR}/backup_status.prom"

# 检查最近一次备份是否成功
LAST_BACKUP=$(ls -t /backup/*.sql.gz 2>/dev/null | head -1)
if [ -n "$LAST_BACKUP" ]; then
    BACKUP_TS=$(stat -c %Y "$LAST_BACKUP")
    BACKUP_SIZE=$(stat -c %s "$LAST_BACKUP")
    echo "backup_last_success_timestamp_seconds $BACKUP_TS" > "$TMP_FILE"
    echo "backup_last_size_bytes $BACKUP_SIZE" >> "$TMP_FILE"
    echo "backup_last_status 1" >> "$TMP_FILE"
else
    echo "backup_last_status 0" > "$TMP_FILE"
fi

# 原子写入：先写 tmp，再 rename
mv "$TMP_FILE" "$FINAL_FILE"
```

**2. 设置 cron 定时执行**：

```bash
# crontab -e 添加以下行（每小时执行一次）
0 * * * * /opt/scripts/backup_status.sh
```

**3. 在 Prometheus 中查询自定义指标**：

```promql
# 查看备份状态（1=成功，0=失败）
backup_last_status

# 距离上次成功备份的秒数
time() - backup_last_success_timestamp_seconds
```

**关键规范**：
- 文件名必须以 `.prom` 结尾
- 指标格式为 `metric_name{label="value"} float_value`，不含 `HELP` 和 `TYPE` 行也可以
- **必须原子写入**（先写 `.tmp` 再 `mv`），否则 Node Exporter 可能读到半截文件并静默忽略，metrics 中也不会报错——这是最难排查的坑

### 可能遇到的坑

1. **Docker `--net=host` 在 Mac/Windows 不生效**：改用 `-p 9100:9100` 可以暴露端口，但 `node_network_*` 指标将变成容器内部的虚拟网络，丢失宿主机真实网卡数据。开发机用 Docker 学习可以，生产环境务必二进制部署。

2. **Node Exporter 版本 <1.0 的指标名**：0.x 版本中 `node_cpu_seconds_total` 叫做 `node_cpu`，内存指标也没有 `_bytes` 后缀。升级时注意更新 PromQL 查询和 Grafana Dashboard。

3. **Textfile Collector 的静默失败**：`.prom` 文件如果格式错误（比如缺少空格、包含中文或特殊字符），Node Exporter 不会在 `/metrics` 输出任何错误提示，只会静默忽略该文件。排查时检查 Node Exporter 的日志：
   ```bash
   journalctl -u node_exporter -f | grep textfile
   ```

### 测试验证

```bash
# 1. 验证 /metrics 端点可访问
curl -s http://localhost:9100/metrics | wc -l
# 输出示例：1200（瘦身后约 600-800 行）

# 2. 验证核心指标存在
curl -s localhost:9100/metrics | grep -E "node_cpu_seconds_total|node_memory_MemTotal|node_filesystem_size_bytes"

# 3. 在 Prometheus Web UI (http://localhost:9090) 中执行以下查询，确认有数据返回：
# CPU使用率
# 内存可用率
# 磁盘使用率（按 mountpoint 分组）
# 网络入站速率
# textfile 自定义指标（如 backup_last_status）
```

在 Prometheus 的 **Status → Targets** 页面确认所有 Node Exporter 实例的 State 为 **UP**，Last Scrape 在 15 秒以内。

---

## 四、项目总结

### 核心指标速查表

| 监控项 | PromQL 模板 |
|--------|-------------|
| CPU 使用率 | `100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[5m])) by (instance) * 100)` |
| 内存使用率 | `(1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes) * 100` |
| 磁盘使用率 | `(node_filesystem_size_bytes{fstype!~"tmpfs\|squashfs",mountpoint!~"/boot.*\|/run.*"} - node_filesystem_avail_bytes{fstype!~"tmpfs\|squashfs",mountpoint!~"/boot.*\|/run.*"}) / node_filesystem_size_bytes{fstype!~"tmpfs\|squashfs",mountpoint!~"/boot.*\|/run.*"} * 100` |
| 磁盘 Inode 使用率 | `(node_filesystem_files_free{fstype!~"tmpfs\|squashfs"} / node_filesystem_files{fstype!~"tmpfs\|squashfs"}) * 100` |
| 网络入站 (bps) | `rate(node_network_receive_bytes_total{device!~"lo\|docker.*\|veth.*"}[5m]) * 8` |
| 系统负载 (1m) | `node_load1` |
| 可用内存 (GB) | `node_memory_MemAvailable_bytes / 1024 / 1024 / 1024` |

### Node Exporter vs 云厂商监控 Agent

| 维度 | Node Exporter | 云厂商 Agent（如云监控） |
|------|---------------|-------------------------|
| 数据粒度 | 按需配置，秒级采集 | 通常 1-5 分钟，不可调 |
| 指标丰富度 | 1000+ 指标，自由组合 | 有限预设指标 |
| 多平台支持 | Linux only（Windows 需 windows_exporter） | 通常全平台 |
| 存储成本 | 自建 Prometheus，按量算 | 按监控实例付费 |
| 自定义监控 | Textfile Collector 万能钩子 | 通常支持但有限制 |
| 适用场景 | 自建机房、混合云 | 单一公有云环境 |

### 注意事项

- 生产环境使用 `--no-collector.*` 按需关闭冷门 Collector（hwmon、ipvs、bonding、infiniband 等），减少 Prometheus 存储压力
- `textfile.directory` 目录权限必须对 `node_exporter` 进程可读，.prom 文件必须原子写入
- Windows 服务器使用 `windows_exporter` 替代，注意指标名差异和 `os` 标签
- v1.0 以上版本指标名已稳定，建议最低使用 1.5.x

### 三个经典踩坑案例

**案例1：多网卡流量叠加导致误告警**。某公司的服务器绑定了双网卡做 Bond 负载均衡，运维同学写的 PromQL 没有过滤 `device` 标签，导致 `rate()` 计算时把 eth0 和 eth1 以及 bond0 的流量全部叠加——一条 10Gbps 的物理链路被算成了 30Gbps，半夜触发大量误告警。修复方法：只保留物理网卡或 bond 接口。

**案例2：磁盘 Inode 耗尽被遗漏**。技术团队只监控了磁盘空间使用率，结果一台存放百万级小文件的 NFS 服务器磁盘空间还剩 60%，但 Inode 已用光——所有写操作失败，业务宕机 2 小时。后续补充了 `node_filesystem_files` 指标监控，告警提前一周发现问题。

**案例3：Textfile 文件格式错误被静默忽略**。一位同事在 shell 脚本中用 `echo "$value"` 写入 .prom 文件，但 `$value` 恰好为空字符串，导致指标行变成 `metric_name `，缺少数值。Node Exporter 静默忽略该行，Grafana 面板上指标断崖消失却没有触发任何告警——因为 Prometheus 根本没有采集到这个指标。"静默失败是最可怕的失败。"

### 思考题

1. **如何用 Node Exporter 监控 GPU？** 提示：Node Exporter 目前没有官方的 GPU Collector，但 Textfile Collector 是万能钩子——你可以用 `nvidia-smi` 或 `rocm-smi` 命令采集 GPU 温度、显存使用率、风扇转速等信息，格式化为 Prometheus metrics 写入 .prom 文件。查找社区是否有现成的 `node_exporter` GPU Collector 或 `nvidia_gpu_exporter` 项目。（关键词：nvidia_gpu_exporter）

2. **写一条 PromQL 找出过去 1 小时内 CPU 使用率超过 80% 且持续超过 5 分钟的主机。** 提示：利用 Prometheus 的 `avg_over_time` 和子查询：
   ```promql
   avg_over_time(
     (100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[5m])) by (instance) * 100) > 80)[1h:1m]
   )
   ```
   配合 `max_over_time` 或 `count_over_time` 可以进一步筛选"持续时长"——你能给出一个更精确的版本吗？
