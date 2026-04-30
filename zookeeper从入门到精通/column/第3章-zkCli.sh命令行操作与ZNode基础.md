# 第3章：zkCli.sh 命令行操作与 ZNode 基础

## 1. 项目背景

### 业务场景

ZooKeeper 集群已经跑起来了，现在你想和它"对话"。ZooKeeper 提供了一个强大的命令行工具 `zkCli.sh`，让你可以直接在终端里操作 ZNode——创建、读取、修改、删除、监听，一气呵成。

想象一个场景：你是团队的运维工程师，需要排查为什么服务 A 连不上 ZooKeeper。你可以在命令行快速连接 ZooKeeper，查看服务注册节点是否正常，检查配置数据是否正确——全程不需要写一行代码。

### 痛点放大

没有命令行工具之前，验证 ZooKeeper 中的数据需要：

1. 写一个 Java Main 方法，初始化 ZooKeeper 客户端
2. 处理 Watcher、Session 等回调逻辑
3. 编译、运行、看结果

每次改数据都要重复这套流程。`zkCli.sh` 就像 MySQL 的 CLI 一样，让你**直连 ZooKeeper、实时操作、即时反馈**。

---

## 2. 项目设计

### 剧本式交锋对话

**场景**：小胖第一次启动 zkCli.sh，界面看起来像个 shell。

**小胖**：我进了 zkCli.sh 了，这玩意儿怎么用？`ls` 能不能用？

**小白**：看起来确实像个文件系统。试试 `ls /`？

**小胖**：输出了 `[zookeeper]`！还真有根目录？那 `cd` 能进去吗？

**大师**：ZooKeeper 的 zkCli.sh 很像文件系统 CLI，但有几个关键区别。首先，ZooKeeper 没有 `cd` 命令——因为它没有"当前工作目录"的概念。所有操作都要用**完整路径**。

基本命令：

| 命令 | 作用 | 示例 |
|------|------|------|
| `ls /path` | 列出子节点 | `ls /config` |
| `create /path data` | 创建节点 | `create /myapp "hello"` |
| `get /path` | 读取节点数据 | `get /myapp` |
| `set /path data` | 更新节点数据 | `set /myapp "world"` |
| `delete /path` | 删除节点 | `delete /myapp` |
| `stat /path` | 查看节点元数据 | `stat /myapp` |

**小白**：那 ZooKeeper 的 `stat` 信息很丰富，我看到有 `czxid`、`mzxid`、`version` 这些，都是什么意思？

**大师**：每个 ZNode 都有一个 Stat 结构体，记录着这个节点的"档案"：

```
cZxid = 0x100000001     # 创建时的事务 ID
ctime = Thu Mar 14 ...  # 创建时间
mZxid = 0x100000002     # 最后修改时的事务 ID
mtime = Thu Mar 14 ...  # 最后修改时间
pZxid = 0x100000002     # 子节点最后变更时的事务 ID
cversion = 1            # 子节点版本号（子节点增删次数的计数）
dataVersion = 2         # 数据版本号（每次 set 数据 +1）
aclVersion = 0          # ACL 版本号
ephemeralOwner = 0x0    # 临时节点所属的 sessionId（0x0 表示持久节点）
dataLength = 5          # 数据长度（字节）
numChildren = 2         # 子节点数量
```

**小胖**：这些 zxid 的数字看着像十六进制，0x100000001，有什么含义？

**大师**：zxid 是 ZooKeeper 的**事务 ID**，高 32 位是 epoch（Leader 任期），低 32 位是 counter（该任期内的递增序号）。`0x100000001` 表示 epoch=1, counter=1。zxid 越大，说明该操作越"新"。

**小白**：那 `set -v` 是干什么用的？我看到 man 里提到版本号参数。

**大师**：`set -v` 是 ZooKeeper 的**乐观锁**版本控制。每个 ZNode 的 dataVersion 就是版本号：

```bash
# 第一次设置
set /myapp "v1"
# dataVersion 变为 1

# 只有版本号匹配才能修改成功
set -v 1 /myapp "v2"    # 成功，dataVersion 变为 2
set -v 1 /myapp "v3"    # 失败！BadVersion 错误，因为当前版本是 2，不是 1
```

这就是乐观锁——你写数据时带着期望的版本号，如果数据已经被别人修改过，ZooKeeper 拒绝你的写入。这在分布式锁和配置管理中非常重要。

> **技术映射**：Stat = 文件的属性信息，zxid = Git commit ID，dataVersion = Git 版本号，乐观锁 = Git push 时的冲突检测

**小胖**：那我要是创建一个节点，想指定它是临时的或者是顺序的呢？

**大师**：用 `create` 的选项：

```bash
# 持久节点（默认）
create /mynode "data"

# 临时节点（会话断开后自动删除）
create -e /ephemeral-node "temp"

# 顺序节点（自动追加递增编号）
create -s /seq-node "data"
# 实际创建：/seq-node0000000001

# 临时顺序节点
create -e -s /ephemeral-seq "data"
# 实际创建：/ephemeral-seq0000000001

# 容器节点（3.5+，子节点清空后自动删除）
create -c /container-node ""

# TTL 节点（3.5+，TTL 到期自动删除，需要配置 extendedTypesEnabled）
create -t 5000 /ttl-node "expire-in-5s"
```

> **技术映射**：`-e` = 便签贴，撕掉（断开连接）就掉了；`-s` = 取号机，自动生成递增号码

---

## 3. 项目实战

### 环境准备

使用第 2 章部署的 ZooKeeper 集群（任意节点均可）。确保至少有一个节点在运行。

### 分步实现

#### 步骤 1：连接 ZooKeeper

```bash
# 进入 ZooKeeper 安装目录
cd apache-zookeeper-3.9.2-bin

# 连接本地单机 ZooKeeper
./bin/zkCli.sh -server 127.0.0.1:2181

# 连接集群
./bin/zkCli.sh -server 127.0.0.1:2181,127.0.0.1:2182,127.0.0.1:2183

# 连接成功后，你会看到：
# Connecting to 127.0.0.1:2181
# Welcome to ZooKeeper!
# JLine support is enabled
# [zk: 127.0.0.1:2181(CONNECTED) 0]
```

#### 步骤 2：模拟配置管理场景

假设你正在管理一个应用的配置，配置项存储在 ZooKeeper 中。

```bash
# 创建配置根路径
create /config "app-config-root"

# 创建数据库连接配置
create /config/db-url "jdbc:mysql://localhost:3306/mydb"
create /config/db-user "admin"
create /config/db-password "encrypted-password"

# 读取配置
get /config/db-url
# 输出：jdbc:mysql://localhost:3306/mydb

# 查看 stat
stat /config/db-url
# cZxid = 0x200000002
# ctime = Thu Mar 14 10:00:00 CST 2025
# mZxid = 0x200000002
# mtime = Thu Mar 14 10:00:00 CST 2025
# pZxid = 0x200000002
# cversion = 0
# dataVersion = 0
# aclVersion = 0
# ephemeralOwner = 0x0
# dataLength = 34
# numChildren = 0

# 更新配置（模拟数据库切换）
set /config/db-url "jdbc:mysql://new-host:3306/mydb"
# 再次 stat，观察 dataVersion 变为 1

# 条件更新（乐观锁版本校验）
set -v 1 /config/db-password "new-encrypted-password"

# 尝试用错误的版本号更新（模拟并发冲突）
set -v 0 /config/db-password "will-fail"
# 输出：version No is not valid : /config/db-password
```

#### 步骤 3：使用四字命令查看状态

```bash
# 退出 zkCli（Ctrl+C 或 quit）
quit

# 使用四字命令（在 shell 中执行）
echo stat | nc 127.0.0.1 2181
# 输出 ZooKeeper 统计信息

echo ruok | nc 127.0.0.1 2181
# 输出：imok（表示服务正常）

echo mntr | nc 127.0.0.1 2181
# 输出监控指标
```

#### 步骤 4：完整的脚本化操作

创建一个脚本 `zk-config-setup.sh`：

```bash
#!/bin/bash

# 使用 zkCli.sh 批量执行命令
ZOOKEEPER_HOST="127.0.0.1:2181"
ZK_CLI="./bin/zkCli.sh -server $ZOOKEEPER_HOST"

# 创建配置结构
$ZK_CLI create /config ""
$ZK_CLI create /config/datasource ""
$ZK_CLI create /config/datasource/url "jdbc:mysql://localhost:3306/db"
$ZK_CLI create /config/datasource/max-active "50"
$ZK_CLI create /config/redis ""
$ZK_CLI create /config/redis/host "127.0.0.1"
$ZK_CLI create /config/redis/port "6379"
$ZK_CLI create /config/redis/timeout "3000"
```

### 运行结果

```
[zk: 127.0.0.1:2181(CONNECTED) 0] ls /config
[datasource, redis]

[zk: 127.0.0.1:2181(CONNECTED) 1] get /config/datasource/url
jdbc:mysql://localhost:3306/db

[zk: 127.0.0.1:2181(CONNECTED) 2] get /config/datasource/max-active
50
```

### 可能遇到的坑

| 问题 | 原因 | 解决 |
|------|------|------|
| `Connection refused` | ZooKeeper 未启动或端口不对 | 检查 `zkServer.sh status` |
| `NoNode for /xxx` | 父节点不存在 | 先创建父节点或用 `create` 的递归功能（需代码实现） |
| `BadVersion` | 乐观锁版本冲突 | `stat` 查看当前版本，重试 |
| `NodeExistsException` | 节点已存在 | 确认路径唯一或用 `set` 更新 |

### 完整代码清单

本章所有代码均在 ZooKeeper 命令行中完成，无需额外代码仓库。

---

## 4. 项目总结

### 优点 & 缺点

| 维度 | 优点 | 缺点 |
|------|------|------|
| 交互性 | 即时反馈，适合调试 | 不支持 Tab 补全（默认） |
| 功能覆盖 | 支持所有 ZNode 操作和四字命令 | 不能直接导出/导入数据 |
| 脚本友好 | 可批量执行命令 | 不支持事务性批量操作 |
| 学习曲线 | 接近文件系统 CLI，容易上手 | 路径必须完整，无 cd 命令 |

### 适用场景

- **快速验证**：排查节点是否存在、数据是否正确
- **临时操作**：手动创建/删除节点进行测试
- **教学演示**：演示 ZNode、Stat、Watcher 等概念
- **故障恢复**：手动清理异常节点、修改配置

**不适用场景**：
- 频繁的批量操作（建议用 Curator 客户端）
- 事务性操作（zkCli.sh 不支持多命令原子性执行）

### 注意事项

- `create` 创建节点前，父节点必须存在（无法递归创建）
- 临时节点不能有子节点
- 数据大小限制 1MB，存大数据会报错
- 路径命名区分大小写，`/Config` 和 `/config` 是两个不同的节点

### 常见踩坑经验

**故障 1：zkCli.sh 连接超时**

现象：启动 zkCli.sh 后一直卡在 Connecting 状态，直到超时报错。

根因：ZooKeeper 服务端端口未开放，或客户端防火墙拦截。检查 `telnet 127.0.0.1 2181` 是否能连通。

**故障 2：误删根节点**

现象：执行 `delete /` 或 `rmr /` 导致 ZooKeeper 集群状态异常。

根因：根节点 `/` 不可删除，但 `rmr /path` 可以递归删除整个子树。在 ZooKeeper 3.5+ 中 `rmr` 已被废弃，推荐使用 `deleteall`。

### 思考题

1. 假设你在 `get /config/db-url` 时看到 dataVersion = 5，但这个配置你从来没有改过。这是什么原因？
2. 用 `create -e -s /lock/resource` 创建了一个临时顺序节点。如果创建后客户端会话断开，这个节点会怎么样？断连期间已经有其他客户端创建了序号更小的节点，这时会怎样？

### 推广计划提示

- **开发**：掌握 zkCli.sh 的所有操作，后续的 Curator 客户端操作本质上是命令行的编程化版本
- **运维**：zkCli.sh 是日常排查的第一利器，建议把常用命令写成运维脚本
- **测试**：可以用 zkCli.sh 手动构造异常数据，验证系统的容错能力
