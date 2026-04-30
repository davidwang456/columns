# 第17章：集群架构深度剖析——Quorum 与角色分工

## 1. 项目背景

### 业务场景

基础篇中，你一直在单机模式下操作 ZooKeeper。但在生产环境中，ZooKeeper 以**集群模式**运行——多个节点组成一个整体，对外提供高可用服务。

假设你的电商系统有几十个微服务，所有服务都依赖 ZooKeeper 做配置管理和服务发现。如果 ZooKeeper 挂了，整个系统就瘫痪了。你需要：即使 ZooKeeper 集群中有节点宕机，集群整体仍然可以正常服务。

这就是 ZooKeeper 集群架构要解决的问题：**Quorum（法定人数）机制**。

### 痛点放大

单机 ZooKeeper 有这些问题：

- **单点故障**：机器挂了，整个集群不可用
- **性能瓶颈**：读写都在一台机器上，QPS 有上限
- **数据丢失风险**：磁盘损坏后所有数据丢失

集群模式下能解决这些问题，但引入了新的复杂性：**谁来写？谁来读？谁来做决策？**

---

## 2. 项目设计

### 剧本式交锋对话

**场景**：小胖在部署生产 ZooKeeper 集群，对节点的角色分工很困惑。

**小胖**：我部署了 3 个 ZooKeeper 节点，怎么一个成了 Leader，两个成了 Follower？它们到底有什么不同？

**大师**：在 ZooKeeper 集群中，有三种角色：

```
┌─────────────────────────────────────────┐
│           ZooKeeper 集群                  │
│                                          │
│   ┌──────────┐    ┌──────────┐           │
│   │  Leader   │    │ Follower │           │
│   │ (写入口)  │◄──►│ (读+投票)│           │
│   └─────┬────┘    └────┬─────┘           │
│         │              │                 │
│   ┌─────▼────┐         │                 │
│   │ Follower │         │                 │
│   │ (读+投票)│         │                 │
│   └──────────┘         │                 │
│                        │                 │
│   ┌──────────┐         │                 │
│   │ Observer │ (只读)  │                 │
│   └──────────┘         │                 │
└─────────────────────────────────────────┘
```

| 角色 | 投票权 | 处理读请求 | 处理写请求 | 可选举 |
|------|--------|-----------|-----------|-------|
| **Leader** | ✓ | ✓ | ✓（唯一入口） | — |
| **Follower** | ✓ | ✓ | 转发到 Leader | ✓（可成为 Leader） |
| **Observer** | ✗ | ✓ | 转发到 Leader | ✗ |

**小白**：那 Quorum（法定人数）是什么意思？和"过半"有关系吗？

**大师**：Quorum 是 ZooKeeper 集群决策的核心概念：

```
Quorum = 集群节点数 / 2 + 1（向下取整）

3 节点集群：Quorum = 3/2 + 1 = 2（最多容忍 1 个节点宕机）
5 节点集群：Quorum = 5/2 + 1 = 3（最多容忍 2 个节点宕机）
7 节点集群：Quorum = 7/2 + 1 = 4（最多容忍 3 个节点宕机）
```

- **写入**：写请求必须得到超过半数（Quorum）的 Follower 确认才算成功
- **选举**：候选人必须得到超过半数的投票才能成为 Leader

**小胖**：那为什么推荐奇数节点？偶数节点不行吗？

**大师**：考虑 4 节点集群：

```
4 节点：Quorum = 4/2 + 1 = 3，容忍 1 个节点宕机
3 节点：Quorum = 3/2 + 1 = 2，容忍 1 个节点宕机
```

看出问题了吗？4 节点和 3 节点都能容忍 1 个节点宕机，但 4 节点多了一台机器的成本和网络开销，却没有提升容错能力。所以奇数节点更优：n 节点的容错能力 = (n-1)/2。

```
1 节点：容忍 0 个故障（无高可用）
3 节点：容忍 1 个故障
5 节点：容忍 2 个故障（生产推荐）
7 节点：容忍 3 个故障（大规模场景）
```

> **技术映射**：Quorum = 董事会决议，过半同意才能通过；Leader = 董事长，Follower = 董事有表决权，Observer = 列席会议但没有表决权

**小白**：Observer 没有投票权，那它有什么用？

**大师**：Observer 的价值在于**扩展读能力**而不影响写入性能。

假设你的集群目前是 3 个节点（1 Leader + 2 Follower），读 QPS 达到瓶颈：

```
方案 A：改为 7 节点（1 Leader + 6 Follower）
  ✓ 读能力扩展了 3 倍
  ✗ 写请求需要 4/7 节点确认（Quorum=4），网络开销更大
  ✗ 选举需要 4/7 投票，选举更慢

方案 B：3 节点 + 4 个 Observer
  ✓ 读能力扩展了 3 倍
  ✓ 写请求仍然只需要 2/3 节点确认（原有 Follower）
  ✓ 选举也只有 3 个投票节点，速度不变
  ✗ Observer 的数据有一定延迟（比 Follower 晚一点点）
```

所以 Observer 最适合**读写分离**场景——写请求走核心集群（Leader + Follower），读请求分散到 Observer。

---

## 3. 项目实战

### 环境准备

- JDK 11+
- Docker & Docker Compose
- ZooKeeper 3.9.x 镜像

### 分步实现

#### 步骤 1：部署 5 节点集群（3 投票节点 + 2 Observer）

创建 `docker-compose-cluster.yml`：

```yaml
version: '3.8'

services:
  zoo1:
    image: zookeeper:3.9
    hostname: zoo1
    container_name: zk-leader-candidate
    ports:
      - "2181:2181"
    environment:
      ZOO_MY_ID: 1
      ZOO_SERVERS: server.1=0.0.0.0:2888:3888;2181 server.2=zoo2:2888:3888;2181 server.3=zoo3:2888:3888;2181 server.4=zoo4:2888:3888;2181 server.5=zoo5:2888:3888;2181
      ZOO_4LW_COMMANDS_WHITELIST: "*"

  zoo2:
    image: zookeeper:3.9
    hostname: zoo2
    container_name: zk-follower-1
    ports:
      - "2182:2181"
    environment:
      ZOO_MY_ID: 2
      ZOO_SERVERS: server.1=zoo1:2888:3888;2181 server.2=0.0.0.0:2888:3888;2181 server.3=zoo3:2888:3888;2181 server.4=zoo4:2888:3888;2181 server.5=zoo5:2888:3888;2181
      ZOO_4LW_COMMANDS_WHITELIST: "*"

  zoo3:
    image: zookeeper:3.9
    hostname: zoo3
    container_name: zk-follower-2
    ports:
      - "2183:2181"
    environment:
      ZOO_MY_ID: 3
      ZOO_SERVERS: server.1=zoo1:2888:3888;2181 server.2=zoo2:2888:3888;2181 server.3=0.0.0.0:2888:3888;2181 server.4=zoo4:2888:3888;2181 server.5=zoo5:2888:3888;2181
      ZOO_4LW_COMMANDS_WHITELIST: "*"

  zoo4:
    image: zookeeper:3.9
    hostname: zoo4
    container_name: zk-observer-1
    ports:
      - "2184:2181"
    environment:
      ZOO_MY_ID: 4
      ZOO_SERVERS: server.1=zoo1:2888:3888;2181 server.2=zoo2:2888:3888;2181 server.3=zoo3:2888:3888;2181 server.4=0.0.0.0:2888:3888;2181 server.5=zoo5:2888:3888;2181
      ZOO_4LW_COMMANDS_WHITELIST: "*"
      # 标记为 Observer
      ZOO_CFG_EXTRA: "peerType=observer"

  zoo5:
    image: zookeeper:3.9
    hostname: zoo5
    container_name: zk-observer-2
    ports:
      - "2185:2181"
    environment:
      ZOO_MY_ID: 5
      ZOO_SERVERS: server.1=zoo1:2888:3888;2181 server.2=zoo2:2888:3888;2181 server.3=zoo3:2888:3888;2181 server.4=zoo4:2888:3888;2181 server.5=0.0.0.0:2888:3888;2181
      ZOO_4LW_COMMANDS_WHITELIST: "*"
      ZOO_CFG_EXTRA: "peerType=observer"
```

启动集群：

```bash
docker-compose -f docker-compose-cluster.yml up -d
```

#### 步骤 2：验证角色分工

```bash
# 查看各节点角色
for i in 1 2 3 4 5; do
  port=$((2180 + i))
  echo "Node $i (port $port):"
  echo stat | nc -w 2 127.0.0.1 $port | grep "Mode"
done

# 输出示例：
# Node 1 (port 2181): Mode: leader
# Node 2 (port 2182): Mode: follower
# Node 3 (port 2183): Mode: follower
# Node 4 (port 2184): Mode: observer
# Node 5 (port 2185): Mode: observer
```

#### 步骤 3：验证 Observer 不参与投票

```bash
# 停止一个 Follower（节点 2）
docker stop zk-follower-1

# 检查集群是否还能写入
echo mntr | nc 127.0.0.1 2181 | grep zk_server_state
# Mode: leader（集群仍然正常）

# 停止 Leader 观察 Observer
docker stop zk-leader-candidate

# 等选举完成
sleep 3

# 检查 Observer 的 Mode
echo stat | nc 127.0.0.1 2184 | grep "Mode"
# Mode: observer（Observer 不会被选为 Leader）

# 恢复所有节点
docker start zk-leader-candidate
docker start zk-follower-1
```

#### 步骤 4：读写分离验证

编写 `ReadWriteTest.java`：

```java
package com.zkdemo.cluster;

import org.apache.curator.framework.CuratorFramework;
import org.apache.curator.framework.CuratorFrameworkFactory;
import org.apache.curator.retry.ExponentialBackoffRetry;

public class ReadWriteTest {
    public static void main(String[] args) throws Exception {
        // 连接到 Observer（读流量打到 Observer）
        CuratorFramework observerClient = CuratorFrameworkFactory.builder()
                .connectString("127.0.0.1:2184") // Observer
                .retryPolicy(new ExponentialBackoffRetry(1000, 3))
                .build();
        observerClient.start();

        // 连接到 Follower（写流量打到 Follower，自动转发给 Leader）
        CuratorFramework followerClient = CuratorFrameworkFactory.builder()
                .connectString("127.0.0.1:2182") // Follower
                .retryPolicy(new ExponentialBackoffRetry(1000, 3))
                .build();
        followerClient.start();

        // 通过 Follower 写入
        followerClient.create().forPath("/cluster-test", "data".getBytes());
        System.out.println("通过 Follower 写入完成");

        // 通过 Observer 读取
        byte[] data = observerClient.getData().forPath("/cluster-test");
        System.out.println("通过 Observer 读取: " + new String(data));

        // Observer 也可以直接读取
        byte[] observerData = observerClient.getData().forPath("/cluster-test");
        System.out.println("Observer 读取: " + new String(observerData));

        followerClient.close();
        observerClient.close();
    }
}
```

#### 步骤 5：验证 Quorum 写入的容错性

编写 `QuorumFaultToleranceTest.java`：

```java
package com.zkdemo.cluster;

import org.apache.curator.framework.CuratorFramework;
import org.apache.curator.framework.CuratorFrameworkFactory;
import org.apache.curator.retry.ExponentialBackoffRetry;

import java.util.concurrent.TimeUnit;

public class QuorumFaultToleranceTest {
    public static void main(String[] args) throws Exception {
        String connectString = "127.0.0.1:2181,127.0.0.1:2182,127.0.0.1:2183,127.0.0.1:2184,127.0.0.1:2185";

        CuratorFramework client = CuratorFrameworkFactory.builder()
                .connectString(connectString)
                .sessionTimeoutMs(5000)
                .retryPolicy(new ExponentialBackoffRetry(500, 5))
                .build();
        client.start();

        System.out.println("1. 测试正常写入...");
        client.create().forPath("/quorum-test", "normal".getBytes());
        System.out.println("   成功!");

        System.out.println("\n2. 停止两个 Follower（模拟故障）...");
        System.out.println("   请执行: docker stop zk-follower-1 zk-follower-2");
        TimeUnit.SECONDS.sleep(3);

        System.out.println("   尝试写入（Quorum 可能不足）...");
        try {
            client.setData().forPath("/quorum-test", "after-failure".getBytes());
            System.out.println("   写入成功（Quorum 仍然满足）");
        } catch (Exception e) {
            System.out.println("   写入失败（Quorum 不足）: " + e.getClass().getSimpleName());
        }

        TimeUnit.SECONDS.sleep(2);

        System.out.println("\n3. 恢复 Follower...");
        System.out.println("   请执行: docker start zk-follower-1 zk-follower-2");
        TimeUnit.SECONDS.sleep(5);

        System.out.println("   尝试写入...");
        try {
            client.setData().forPath("/quorum-test", "after-recovery".getBytes());
            System.out.println("   写入成功!");
        } catch (Exception e) {
            System.out.println("   写入失败: " + e.getMessage());
        }

        client.close();
    }
}
```

### 可能遇到的坑

| 问题 | 原因 | 解决 |
|------|------|------|
| Observer 数据比 Follower 新 | Observer 不参与投票，它的数据延迟通常比 Follower 大 | Observer 适合读多写少 |
| 集群节点数理解错误 | Observer 不参与 Quorum 计算 | Quorum 只算投票节点（Leader + Follower） |
| 误停多数节点 | 集群 Quorum 不足，停止服务 | 永远不要同时停止超过半数投票节点 |

---

## 4. 项目总结

### 集群规模推荐

| 规模 | 节点数 | 投票节点 | Observer | 容忍故障 |
|------|--------|---------|---------|---------|
| 开发 | 3 | 3 | 0 | 1 |
| 生产（标准） | 5 | 3 | 2 | 1 |
| 生产（高可用） | 7 | 5 | 2 | 2 |
| 生产（读密集） | 9 | 5 | 4 | 2 |

### 优点 & 缺点

| 角色 | 优点 | 缺点 |
|------|------|------|
| Leader | 统一写入口，保证顺序一致性 | 单点写瓶颈 |
| Follower | 水平扩展读能力，参与投票 | 写请求仍需要路由到 Leader |
| Observer | 不降写性能扩展读能力 | 数据轻微延迟，不投票 |

### 适用场景

- **Follower 扩展**：读 QPS 不满足需求，且能容忍投票节点数增加带来的写性能下降
- **Observer 扩展**：读 QPS 不满足需求，且写性能不能下降（最常用）
- **跨机房部署**：主机房部署投票节点，灾备机房部署 Observer

### 注意事项

- Observer 的 `peerType=observer` 必须在 zoo.cfg 中配置
- 连接字符串可以包含 Observer 的连接串，客户端会自动发现 Leader
- Observer 不会参与 Leader 选举，也不会计入 Quorum

### 常见踩坑经验

**故障 1：部署了 2 个节点以为有高可用**

现象：2 节点集群，一个节点宕机后集群无法选举。

根因：2 节点 Quorum = 2/2 + 1 = 2，一个节点宕机后 Quorum 不足。2 节点集群没有容错能力。

**故障 2：Observer 配置错误导致启动失败**

现象：Observer 启动时报错 `Invalid configuration`，无法加入集群。

根因：配置了 `peerType=observer` 但没有在 `server.x` 配置中标记 observer。需要在 server.x 行末尾加 `:observer`：
```
server.4=zoo4:2888:3888:observer
```

### 思考题

1. 假设你有一个 5 节点投票集群 + 10 个 Observer。如果 2 个投票节点宕机，集群还能提供服务吗？能写入吗？5 个 Observer 加入投票节点后能提升容错能力吗？
2. 如果 Observer 的读请求量远远超过 Leader 的写请求量，Observer 的副本同步会成为瓶颈吗？什么情况下 Observer 的同步带宽会成为瓶颈？

### 推广计划提示

- **开发**：连接字符串中建议同时包含多个节点地址，客户端会自动发现当前可用的节点
- **运维**：监控每个节点的 `mntr` 中的 `zk_server_state` 和 `zk_followers` / `zk_observer` 数量
- **架构师**：读写分离场景优先考虑 Observer，而不是增加投票节点
