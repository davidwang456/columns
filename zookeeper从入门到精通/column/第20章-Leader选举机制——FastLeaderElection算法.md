# 第20章：Leader 选举机制——FastLeaderElection 算法

## 1. 项目背景

### 业务场景

第 19 章中，Leader 宕机后集群自动恢复了——但恢复过程中的第一个关键步骤是**Leader 选举**。在 ZooKeeper 集群中，当以下情况发生时需要选举：

- **集群初始化**：所有节点刚启动，还没有 Leader
- **Leader 宕机**：当前 Leader 无法服务
- **Leader 失去 Quorum**：网络分区导致 Leader 与过半 Follower 失联

ZooKeeper 使用 **FastLeaderElection（FLE）** 算法来选出新 Leader。这个算法需要在**几秒内**完成选举，同时保证选出的 Leader 拥有**最全的数据**。

### 痛点放大

如果没有高效的选举算法：

- **选举太久**：选举期间整个集群不能写入，影响业务
- **选出落后节点**：选出的 Leader 缺少数据，部分写入丢失
- **脑裂**：选出两个 Leader，数据分裂
- **网络风暴**：选举中的投票信息在网络中泛滥

FastLeaderElection 就是为了同时解决效率和正确性问题而设计的。

---

## 2. 项目设计

### 剧本式交锋对话

**场景**：小白在调试 ZooKeeper 集群的选举过程，但不知道 FLE 算法的具体步骤。

**小白**：FastLeaderElection 到底是怎么选出 Leader 的？我看了下源代码，有很多逻辑判断。

**大师**：FLE 算法的核心可以用一句话概括：**"选 epoch 最大、zxid 最大、SID 最大的节点当 Leader"**。

具体选举流程：

```
每个节点初始化时投自己一票：(epoch, zxid, sid)
   |
   向所有其他节点广播自己的投票
   |
   收到其他节点的投票后，进行比较：
   a. 先比 epoch：大的获胜
   b. epoch 相同，比 zxid：大的获胜
   c. epoch 和 zxid 都相同，比 sid（节点 ID）：大的获胜
   |
   如果自己的投票输了，改为投胜出的节点
   |
   某节点获得超过半数的投票 → 当选 Leader
   |
   通知所有节点："我已经是 Leader"
```

**小胖**：epoch、zxid、sid 这三个比较维度的先后顺序怎么理解？

**大师**：可以这么理解选举的"优先级"：

- **epoch（任期编号）**：每换届一次 +1。如果有节点有更大的 epoch，说明它参与了更近一次选举，数据更"新鲜"
- **zxid（事务 ID）**：在同一次换届中，zxid 最大的节点拥有最新的数据。选它做 Leader，恢复时数据最全
- **sid（节点 ID）**：如果 epoch 和 zxid 都相同（一般只发生在集群启动时所有节点都没数据），选 sid 大的节点当 Leader。这只是一种"打破平局"的约定

> **技术映射**：选举 = 选班长，epoch = 年级，zxid = 成绩，sid = 学号。先看年级高低（epoch），同年级比成绩（zxid），成绩相同比学号（sid）

**小白**：那选举的通信机制呢？节点之间怎么交换投票信息？

**大师**：ZooKeeper 使用专门的**选举端口（默认 3888）**进行选举通信。底层使用 `QuorumCnxManager` 管理节点间的 TCP 连接：

```
选举端口的建立规则：
  sid 小的节点主动连接 sid 大的节点

3 节点集群（SID = 1, 2, 3）：
  Node 1 → 连接 Node 2 和 Node 3
  Node 2 → 连接 Node 3
  Node 3 → 等待被连接（不主动发起连接）
```

这种规则避免了两个节点之间的"双连接"问题（重复建立 TCP 连接）。

**小胖**：那选举需不需要很多消息？会不会导致网络风暴？

**大师**：FLE 算法的消息量是可控的。每个节点会将自己当前的投票广播给所有 `(n-1)` 个节点。每个节点收到投票后会更新自己的投票（如果需要），再回传。

最坏情况下，3 节点集群 > 9 条消息，5 节点集群 > 25 条消息。这点消息量对现代网络来说微不足道。

**选举超时（electionAlg）** 有一个默认的 200ms 的"随机等待"，防止所有节点同时发起选举导致"活锁"。

---

## 3. 项目实战

### 环境准备

- ZooKeeper 3.9.x 集群（3 节点 Docker Compose）
- `nc` 命令
- Java 11+

### 分步实现

#### 步骤 1：观察选举全流程

```bash
#!/bin/bash
# 观察 ZooKeeper 选举全过程

echo "=== 场景 1: 集群初始化选举 ==="
echo "先停止所有节点，再逐个启动观察选举"

# 停止所有节点
docker stop zk-leader-candidate zk-follower-1 zk-follower-2

# 清空数据（模拟全新集群）
# 注意：这会丢失所有数据！
# docker exec zk-leader-candidate rm -rf /data/*
# docker exec zk-follower-1 rm -rf /data/*
# docker exec zk-follower-2 rm -rf /data/*

sleep 2

echo ""
echo "启动第一个节点...（应该是 LOOKING 状态，没有 Leader）"
docker start zk-leader-candidate
sleep 3
echo "状态:"
echo stat | nc -w 2 127.0.0.1 2181 | grep "Mode"
# Mode: standalone（单节点模式，因为没有其他节点）

echo ""
echo "启动第二个节点..."
docker start zk-follower-1
sleep 5

echo "节点 1 状态:"
echo stat | nc -w 2 127.0.0.1 2181 | grep "Mode"
echo "节点 2 状态:"
echo stat | nc -w 2 127.0.0.1 2182 | grep "Mode"
# 应该有一个 leader，一个 follower

echo ""
echo "启动第三个节点..."
docker start zk-follower-2
sleep 3
echo "节点 3 状态:"
echo stat | nc -w 2 127.0.0.1 2183 | grep "Mode"
```

#### 步骤 2：观察 Leader 宕机后的选举

```bash
#!/bin/bash
echo "=== 场景 2: Leader 宕机后的重新选举 ==="

# 找出当前 Leader
LEADER_PORT=""
for i in 1 2 3; do
  port=$((2180 + i))
  mode=$(echo stat | nc -w 2 127.0.0.1 $port | grep "Mode" | awk '{print $2}')
  echo "节点 $i (port $port): $mode"
  if [ "$mode" = "leader" ]; then
    LEADER_PORT=$port
    LEADER_CONTAINER="zk-$(echo $mode | tr 'A-Z' 'a-z')-candidate"
  fi
done

# 根据端口确定容器名
if [ "$LEADER_PORT" = "2181" ]; then
  LEADER_CONTAINER="zk-leader-candidate"
elif [ "$LEADER_PORT" = "2182" ]; then
  LEADER_CONTAINER="zk-follower-1"
else
  LEADER_CONTAINER="zk-follower-2"
fi

echo ""
echo "停止 Leader: $LEADER_CONTAINER (port $LEADER_PORT)"
docker stop $LEADER_CONTAINER

echo "等待选举..."
sleep 5

echo "选举结束，检查各角色:"
for i in 1 2 3; do
  port=$((2180 + i))
  mode=$(echo stat | nc -w 2 127.0.0.1 $port | grep "Mode" 2>/dev/null)
  if [ -z "$mode" ]; then
    echo "节点 $i (port $port): 已宕机"
  else
    echo "节点 $i (port $port): $mode"
  fi
done

echo ""
echo "恢复旧 Leader: $LEADER_CONTAINER"
docker start $LEADER_CONTAINER
sleep 5

echo "恢复后检查各角色:"
for i in 1 2 3; do
  port=$((2180 + i))
  mode=$(echo stat | nc -w 2 127.0.0.1 $port | grep "Mode")
  echo "节点 $i (port $port): $mode"
done
```

#### 步骤 3：编写选举观察程序

创建 `ElectionObserver.java`：

```java
package com.zkdemo.election;

import org.apache.curator.framework.CuratorFramework;
import org.apache.curator.framework.CuratorFrameworkFactory;
import org.apache.curator.retry.ExponentialBackoffRetry;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.util.Arrays;
import java.util.List;

/**
 * 通过四字命令观察选举过程
 */
public class ElectionObserver {
    private static final List<String> NODES = Arrays.asList(
            "127.0.0.1:2181",
            "127.0.0.1:2182",
            "127.0.0.1:2183"
    );

    public static void main(String[] args) throws Exception {
        System.out.println("=== ZooKeeper 选举观察器 ===\n");
        System.out.println("观察间隔: 2 秒");
        System.out.println("可通过停止/启动容器观察选举变化\n");

        String prevLeader = "";

        while (true) {
            StringBuilder sb = new StringBuilder();
            String currentLeader = "";

            for (String node : NODES) {
                String[] parts = node.split(":");
                String host = parts[0];
                int port = Integer.parseInt(parts[1]);

                try {
                    Process p = new ProcessBuilder("echo", "stat")
                            .redirectErrorStream(true)
                            .start();

                    // 使用 nc 发送命令
                    Process nc = new ProcessBuilder("nc", "-w", "2", host, String.valueOf(port))
                            .start();

                    // 将 "stat" 写入 nc
                    nc.getOutputStream().write("stat\n".getBytes());
                    nc.getOutputStream().flush();

                    BufferedReader reader = new BufferedReader(
                            new InputStreamReader(nc.getInputStream()));

                    String line;
                    boolean foundMode = false;
                    while ((line = reader.readLine()) != null) {
                        if (line.contains("Mode:")) {
                            String mode = line.trim();
                            sb.append(String.format("  %s:%d → %s%n", host, port, mode));
                            foundMode = true;
                            if (mode.contains("leader")) {
                                currentLeader = host + ":" + port;
                            }
                            break;
                        }
                    }

                    if (!foundMode) {
                        sb.append(String.format("  %s:%d → 无响应%n", host, port));
                    }

                    nc.destroy();
                } catch (Exception e) {
                    sb.append(String.format("  %s:%d → 错误: %s%n", host, port, e.getMessage()));
                }
            }

            // 检测 Leader 变化
            if (!currentLeader.equals(prevLeader)) {
                System.out.println("========== " + new java.util.Date() + " ==========");
                System.out.println(sb);
                if (!prevLeader.isEmpty() && !currentLeader.isEmpty()) {
                    System.out.println("⚠ Leader 已切换: " + prevLeader + " → " + currentLeader);
                } else if (!currentLeader.isEmpty()) {
                    System.out.println("✓ Leader 已选出: " + currentLeader);
                } else {
                    System.out.println("✗ 没有 Leader（选举中或无节点存活）");
                }
                System.out.println();
                prevLeader = currentLeader;
            }

            Thread.sleep(2000);
        }
    }
}
```

#### 步骤 4：测试选举优先级

创建 `ElectionPriorityTest.java`：

```java
package com.zkdemo.election;

import java.io.BufferedReader;
import java.io.InputStreamReader;

/**
 * 验证 FLE 选举优先级：epoch > zxid > sid
 */
public class ElectionPriorityTest {
    public static void main(String[] args) throws Exception {
        System.out.println("=== FastLeaderElection 优先级验证 ===\n");

        // 在测试之前，先在一个节点上写入一些数据（增加它的 zxid）
        System.out.println("准备：在节点 1 (2181) 上写入数据增加 zxid...");

        // 然后停止这个节点
        System.out.println("开始测试：停止节点 1（数据最新）...");
        ProcessBuilder pb = new ProcessBuilder("docker", "stop", "zk-leader-candidate");
        pb.inheritIO().start();

        Thread.sleep(3000);

        // 观察哪个节点被选为 Leader
        System.out.println("\n观察选举结果...");
        for (int i = 0; i < 5; i++) {
            Thread.sleep(2000);
            for (int port = 2182; port <= 2183; port++) {
                try {
                    Process nc = new ProcessBuilder("nc", "-w", "2", "127.0.0.1", String.valueOf(port)).start();
                    nc.getOutputStream().write("stat\n".getBytes());
                    nc.getOutputStream().flush();

                    BufferedReader reader = new BufferedReader(
                            new InputStreamReader(nc.getInputStream()));
                    String line;
                    while ((line = reader.readLine()) != null) {
                        if (line.contains("Mode:")) {
                            System.out.println("  127.0.0.1:" + port + " - " + line.trim());
                        }
                    }
                    nc.destroy();
                } catch (Exception ignored) {
                }
            }
            System.out.println("---");
        }

        // 恢复节点 1
        System.out.println("\n恢复节点 1...");
        pb = new ProcessBuilder("docker", "start", "zk-leader-candidate");
        pb.inheritIO().start();
    }
}
```

### 测试验证

```bash
# 测试选举的基本流程
# 1. 确保所有节点启动
docker-compose -f docker-compose-cluster.yml up -d

# 2. 查看初始选举结果
for i in 1 2 3; do
  echo "Node $i: $(echo stat | nc -w 2 127.0.0.1 $((2180 + i)) | grep Mode)"
done

# 3. 停止 Leader，观察重新选举
# 4. 恢复旧 Leader，观察它作为 Follower 加入

# 5. 连续停止 2 个节点，验证第三个节点是否还能服务
docker stop zk-leader-candidate zk-follower-1
sleep 3
echo "只有一个节点时:"
echo stat | nc -w 2 127.0.0.1 2183 | grep Mode
# 输出应该显示 Mode: standalone（单节点模式）
```

### 可能遇到的坑

| 问题 | 原因 | 解决 |
|------|------|------|
| 选举周期过长 | 心跳超时设置太大 | 调小 `initLimit` 和 `syncLimit` |
| 选举后数据丢失 | 选出的 Leader 没有最新数据 | 确保触发快照的频率合理 |
| 偶发"双 Leader" | 网络分区导致两个 Quorum 分别选举 | 全连接的网络拓扑，避免分区 |

---

## 4. 项目总结

### 选举流程图

```
集群初始化 / Leader 宕机 / 失去 Quorum
                    ↓
          状态变为 LOOKING
                    ↓
    初始化投票：(epoch, zxid, sid)
                    ↓
    发送投票给所有节点 + 接收其他节点的投票
                    ↓
       ┌──────────────────────────┐
       │ 比较当前投票和收到的投票  │
       │                          │
       │ epoch 更大 → 投 epoch 大 │
       │ epoch 相同，zxid 更大 →  │
       │   投 zxid 大的           │
       │ epoch 和 zxid 相同，     │
       │   sid 更大 → 投 sid 大的 │
       └────────────┬─────────────┘
                    ↓
          更新本地投票为胜出者
                    ↓
        收集选票，检测是否过半数
                    ↓
       ┌──── 是 ────┴──── 否 ────┐
       ↓                         ↓
   状态变为 LEADING         继续接收投票
   通知所有节点:                     ↑
   "我是 Leader"            ────────┘
```

### 优点 & 缺点

| 维度 | 优点 | 缺点 |
|------|------|------|
| 速度 | 小型集群（3-5 节点）毫秒级完成 | 大型集群（>13 节点）选票交换量大 |
| 正确性 | 确保选出数据最全的节点 | 选举期间写入暂停 |
| 简单性 | 比较逻辑直接（epoch > zxid > sid） | 网络分区场景下的行为较复杂 |

### 适用场景

- 所有 ZooKeeper 集群的正常选举场景

### 注意事项

- 超过 13 个投票节点时，选举消息 O(n²) 增长，建议控制投票节点数
- 选举端口（3888）需要防火墙开放，否则节点之间无法通信
- 选举期间客户端读请求仍可服务（连接到 Follower 的请求）

### 常见踩坑经验

**故障 1：选举时间过长导致客户端会话大量过期**

现象：Leader 宕机后，选举耗时超过 10 秒，大量客户端会话过期。

根因：`initLimit` 配置过大（默认 10 tickTime = 20 秒），Follower 发现 Leader 失联的时间就花了半分钟，加上选举时间，整体恢复时间过长。

解决方案：减小 `initLimit` 为 5（10 秒），配合 `syncLimit=2`（4 秒）。

**故障 2：偶数节点集群选举失败**

现象：4 节点集群，2 个节点宕机后，剩余 2 个节点无法选出 Leader。

根因：4 节点 Quorum = 3，剩余 2 个节点无法达到 Quorum。选举时每节点需要 3 票，但只有 2 个节点存在。

### 思考题

1. 假设 5 节点集群，sid 分别为 1, 2, 3, 4, 5。sid=5 的节点宕机后，剩余节点重新选举。此时 sid=4 的节点 zxid 最大（0x500000100），sid=3 的节点 epoch 最大（6 vs 5），谁会成为新 Leader？为什么？
2. FLE 算法和 Raft 算法的选举有什么本质区别？在选票比较规则上，Raft 的"term 更大 > log index 更大 > 先到先得"和 FLE 的"epoch > zxid > sid"有什么异同？

### 推广计划提示

- **开发**：理解选举算法有助于调试 ZooKeeper 集群的异常行为
- **运维**：关注 `initLimit` 和 `syncLimit` 配置，它们直接影响 Leader 故障检测速度和选举效率
- **架构师**：FLE 是 ZAB 协议的核心组成部分，也是分布式一致性算法的重要参考
