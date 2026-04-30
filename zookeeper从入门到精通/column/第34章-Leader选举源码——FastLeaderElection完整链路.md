# 第34章：Leader 选举源码——FastLeaderElection 完整链路

## 1. 项目背景

### 业务场景

第 20 章从算法层面理解了 FLE 的原理：epoch > zxid > sid。本章从源码层面，追踪选举的完整代码链路——当 Leader 宕机后，一个普通节点如何变成 LOOKING 状态，然后通过投票选举出新的 Leader。

### 源码定位

```
FastLeaderElection.java       ← 选举算法核心
QuorumCnxManager.java         ← 选举网络通信
QuorumPeer.java               ← 状态管理（LOOKING/LEADING/FOLLOWING）
```

---

## 2. 项目设计

### 剧本式交锋对话

**场景**：小白在阅读 FLE 选举源码，遇到了理解障碍。

**小白**：`lookForLeader()` 方法好长，几百行代码，做了很多事。它的主流程是什么？

**大师**：核心逻辑其实很清晰：

```
lookForLeader() 主流程：

1. 初始化：把自己的 (epoch, zxid, sid) 作为初始投票
2. 将投票发送给所有节点（通过 WorkerSender）
3. 循环接收其他节点的投票（通过 WorkerReceiver）
4. 比较收到的投票和本地投票：
   a. 如果对方更好 → 更新本地投票并广播
   b. 如果自己更好 → 通知对方
5. 统计票数，如果某节点获得超过半数的投票 → 胜出
6. 通知所有节点，返回选举结果
```

**小胖**：源码中的 `sendNotifications()` 和 `recvQueue` 是怎么工作的？

**大师**：有专门的线程负责网络通信：

```
┌──────────────────────────────────────────┐
│          FastLeaderElection              │
├──────────────────────────────────────────┤
│  lookForLeader()                          │
│    ├── 发送投票 → WorkerSender 线程       │
│    │              → 序列化 → TCP 发送      │
│    │                                      │
│    └── 接收投票 ← WorkerReceiver 线程     │
│                   ← TCP 接收 ← 反序列化    │
│                   ← 放入 recvQueue         │
│                                            │
│  recvQueue → lookForLeader() 不断 poll    │
└──────────────────────────────────────────┘
                       │
         ┌─────────────┴──────────┐
         ▼                        ▼
  QuorumCnxManager          TCP 连接管理
  ├── Listener（监听新连接）       │
  ├── SendWorker（发送线程）       │
  └── RecvWorker（接收线程）       │
```

---

## 3. 项目实战

### 环境准备

- ZooKeeper 3.9.x 源码
- IntelliJ IDEA
- JDK 17+

### 分步实现

#### 步骤 1：找到选举入口

```java
// 文件位置：org.apache.zookeeper.server.quorum.QuorumPeer
// 选举的触发入口

public class QuorumPeer extends ZooKeeperThread {
    // 核心运行时方法
    @Override
    public void run() {
        while (running) {
            switch (getPeerState()) {
                case LOOKING:
                    // 触发选举！
                    setCurrentVote(election.lookForLeader());
                    break;
                case LEADING:
                    lead();
                    break;
                case FOLLOWING:
                    followLeader();
                    break;
                case OBSERVING:
                    observeLeader();
                    break;
            }
        }
    }

    // 启动选举
    public synchronized void startLeaderElection() {
        // 创建选举算法实例
        if (electionAlg == 3) {  // 3 = FastLeaderElection
            election = new FastLeaderElection(this, qcm);
        }
        // 初始化投票
        currentVote = new Vote(myid, getLastLoggedZxid(), getCurrentEpoch());
    }
}
```

#### 步骤 2：FastLeaderElection.lookForLeader() 源码追踪

```java
// 文件位置：org.apache.zookeeper.server.quorum.FastLeaderElection
// 核心选举方法

public Vote lookForLeader() throws InterruptedException {
    // 步骤 1：记录当前信息
    HashMap<Long, Vote> recvset = new HashMap<>();  // 收到的投票
    HashMap<Long, Vote> outofelection = new HashMap<>();  // 选举结束后的投票
    int notTimeout = minNotificationInterval;  // 最小通知间隔

    // 步骤 2：初始化投票
    synchronized (this) {
        // logicalClock 是当前选举的 epoch（服务端逻辑时钟）
        logicalclock.incrementAndGet();
        // 初始化投给自己
        updateProposal(getInitId(), getInitLastLoggedZxid(), getPeerEpoch());
    }

    LOG.debug("New election. My id = {}, proposed zxid=0x{}",
            myid, Long.toHexString(proposedZxid));

    // 步骤 3：广播自己的投票
    sendNotifications();

    // 步骤 4：主循环——不断接收投票并比较
    while (running && remaining > 0) {
        // 4.1 从 recvQueue 获取一个投票
        Notification n = recvqueue.poll(notTimeout,
                TimeUnit.MILLISECONDS);

        if (n == null) {
            // 超时没收到投票 → 重新广播
            if (manager.haveDelivered()) {
                sendNotifications();
            } else {
                // 尝试重新建立连接
                manager.connectAll();
            }
            continue;
        }

        // 4.2 更新逻辑时钟
        if (n.electionEpoch > logicalclock.get()) {
            // 对方的 epoch 更大 → 以对方为准
            logicalclock.set(n.electionEpoch);
            recvset.clear();
            // 更新自己的投票为对方
            updateProposal(n.leader, n.zxid, n.peerEpoch);
            sendNotifications();
        } else if (n.electionEpoch < logicalclock.get()) {
            // 对方的 epoch 更小 → 忽略
            break;
        }

        // 4.3 比较投票
        if (totalOrderPredicate(n.leader, n.zxid, n.peerEpoch,
                proposedLeader, proposedZxid, proposedEpoch)) {
            // 对方的投票更好 → 更新自己的投票
            updateProposal(n.leader, n.zxid, n.peerEpoch);
            sendNotifications();
        }

        // 4.4 记录该节点的投票
        recvset.put(n.sid, new Vote(n.leader, n.zxid, n.electionEpoch, n.peerEpoch));

        // 4.5 检测是否达到 Quorum
        if (haveQuorum(recvset, proposedLeader)) {
            // 验证胜选者
            Vote endVote = new Vote(proposedLeader, proposedZxid, proposedEpoch);
            // 确认 Leader
            return endVote;
        }
    }

    return null;
}

// 比较规则——totalOrderPredicate
protected boolean totalOrderPredicate(long newId, long newZxid,
                                       long newEpoch, long curId,
                                       long curZxid, long curEpoch) {
    if (newEpoch > curEpoch) {
        // 1. epoch 大的胜
        return true;
    } else if (newEpoch < curEpoch) {
        return false;
    }
    if (newZxid > curZxid) {
        // 2. zxid 大的胜
        return true;
    } else if (newZxid < curZxid) {
        return false;
    }
    // 3. sid 大的胜
    return newId > curId;
}
```

#### 步骤 3：QuorumCnxManager 网络通信

```java
// 文件位置：org.apache.zookeeper.server.quorum.QuorumCnxManager
// 选举网络通信管理器

public class QuorumCnxManager {
    // 连接规则：大 SID 连接小 SID，避免双连接
    // 例如：sid=100 主动连接 sid=50，sid=50 不会主动连接 sid=100

    public class Listener extends ZooKeeperThread {
        public void run() {
            // 监听选举端口（3888）
            ServerSocket ss = new ServerSocket(port);
            while (running) {
                Socket s = ss.accept();
                // 处理新连接
                receiveConnection(s);
            }
        }
    }

    // 接收连接
    public void receiveConnection(Socket sock) {
        // 判断 sid 大小关系
        if (sid < mySid) {
            // 对方 sid 更小 → 断开此连接，由对方重新连接
            // 避免两个节点之间同时存在两个连接
            sendConnection(sock);
        } else {
            // 创建 SendWorker、RecvWorker 处理通信
            SendWorker sw = new SendWorker(sock, sid);
            RecvWorker rw = new RecvWorker(sock, sid, sw);
            sw.start();
            rw.start();
        }
    }
}
```

#### 步骤 4：Notification 消息格式

```java
// 文件位置：org.apache.zookeeper.server.quorum.FastLeaderElection

// 投票消息——Notification
static public class Notification {
    // 发起的投票
    long leader;        // 推荐的 Leader ID
    long zxid;          // Leader 的最大 zxid
    long electionEpoch; // 选举 epoch（逻辑时钟）
    long state;         // 节点状态（LOOKING/LEADING/FOLLOWING）
    long sid;           // 发送者 ID
    long peerEpoch;     // 节点 epoch
    long version;       // 协议版本
}

// 发送投票
void sendNotifications() {
    for (long sid : self.getCurrentAndNextConfigVoters()) {
        if (quorumVerifier.getVotingMember(sid) == null) continue;

        // 构建通知消息
        QuorumPacket qp = new QuorumPacket(
                Leader.OBSERVATION,  // 消息类型
                proposedVersion,     // 版本
                proposedLeader,      // 推荐的 Leader
                proposedZxid,        // zxid
                logicalclock.get(),  // 逻辑时钟
                null                 // 其他数据
        );

        // 通过 manager 发送
        manager.toSend(sid, qp);
    }
}
```

#### 步骤 5：调试选举过程

创建选举调试启动器：

```java
package com.zkdemo.source;

import org.apache.zookeeper.server.quorum.*;
import org.apache.zookeeper.server.quorum.FastLeaderElection;

import java.lang.reflect.Field;
import java.util.concurrent.atomic.AtomicLong;

/**
 * 在 FastLeaderElection.lookForLeader() 中添加调试日志
 * 用于观察每次投票的变化
 */
public class ElectionDebugger {

    public static void main(String[] args) throws Exception {
        System.out.println("=== ZooKeeper 选举调试器 ===\n");
        System.out.println("请先在 FastLeaderElection.java 中添加以下调试代码：\n");

        String debugCode = """
            // 在 lookForLeader() 的 while 循环中，收到 Notification 后添加：
            System.out.println("[选举] " + new java.util.Date()
                + " | 收到投票: sid=" + n.sid
                + ", leader=" + n.leader
                + ", zxid=0x" + Long.toHexString(n.zxid)
                + ", epoch=" + n.electionEpoch
                + ", state=" + n.state);

            // 在检测到 Quorum 时添加：
            System.out.println("[选举] Quorum 达成！Leader=" + proposedLeader
                + ", 总投票数=" + recvset.size());
            """;

        System.out.println(debugCode);
    }
}
```

在源码中添加上述 Debug 日志，编译运行：

```bash
# 1. 修改 FastLeaderElection.java
# 2. 重新编译
mvn compile -pl zookeeper-server -DskipTests

# 3. 启动 3 节点伪集群
# 4. 停止 Leader，观察 Debug 输出

# 输出示例：
# [选举] 2025-03-14 10:00:00 | 收到投票: sid=2, leader=2, zxid=0x200000010, epoch=2, state=LOOKING
# [选举] 2025-03-14 10:00:00 | 收到投票: sid=3, leader=2, zxid=0x200000010, epoch=2, state=LOOKING
# [选举] Quorum 达成！Leader=2, 总投票数=3
# [选举] 2025-03-14 10:00:01 | 收到投票: sid=1, leader=2, zxid=0x200000010, epoch=2, state=LEADING
```

#### 步骤 6：边界情况分析

```java
// 场景 1：网络分区
// 5 节点集群，3 个节点在一个分区，2 个在另一个分区
// 3 个节点的分区 → 可以选举（3 ≥ 3 Quorum）
// 2 个节点的分区 → 无法选举（2 < 3 Quorum）

// 场景 2：偶数节点集群
// 4 节点集群，Quorum = 3
// 2 个节点故障 → 剩余 2 个无法达到 Quorum

// 场景 3：选举超时
// notTimeout 从 200ms 开始，每次超时翻倍
// 最多 200ms * 2^5 = 6.4 秒
```

### 测试验证

```bash
# 验证选举流程
# 1. 启动 5 节点集群
# 2. 观察初始选举
# 3. 停止 Leader，观察重新选举

# 验证选举优先级
# 1. 向 sid=1 的节点写入大量数据（增加 zxid）
# 2. 停止 Leader
# 3. 观察 sid=1 是否被选为新 Leader（因为它的 zxid 最大）
```

### 可能遇到的坑

| 问题 | 原因 | 解决 |
|------|------|------|
| 选举消息未发送 | QuorumCnxManager 连接未建立 | 检查 3888 端口是否互通 |
| 无限 LOOKING | 无法达到 Quorum | 检查偶数节点问题 |
| 选举后数据丢失 | 选出的 Leader zxid 不是最大 | 检查 zxid 比较逻辑 |

---

## 4. 项目总结

### FLE 源码关键类

| 类 | 作用 | 关键方法 |
|-----|------|---------|
| `FastLeaderElection` | 选举算法核心 | `lookForLeader()`, `totalOrderPredicate()`, `sendNotifications()` |
| `QuorumCnxManager` | 选举网络通信 | `Listener.run()`, `receiveConnection()`, `toSend()` |
| `QuorumPeer` | 状态机管理 | `run()`, `startLeaderElection()`, `getPeerState()` |
| `Notification` | 投票消息 | sid, leader, zxid, electionEpoch, peerEpoch |
| `Vote` | 投票结果 | id, zxid, epoch |

### 源码阅读要点

1. **lookForLeader() 的主循环**：接收投票 → 比较 → 更新 → 广播 → 检测 Quorum
2. **totalOrderPredicate 比较规则**：epoch > zxid > sid
3. **QuorumCnxManager 的连击规则**：大 SID 连小 SID，避免双连接
4. **Notification 的消息格式**：包含所有比较所需的信息

### 思考题

1. `totalOrderPredicate` 方法中，比较顺序是 epoch → zxid → sid。如果改成 sid → zxid → epoch，算法还正确吗？会有什么问题？
2. `haveQuorum()` 方法如何判断是否达到 Quorum？如果 5 个节点的集群中，2 个节点投票给了 A，2 个节点投票给了 B，1 个节点还没投票，`haveQuorum()` 会认为谁赢？

### 推广计划提示

- **开发**：FLE 是分布式一致性算法的优秀实现，值得反复阅读
- **运维**：如果集群出现"选举风暴"（频繁 LOOKING），检查网络延迟和心跳配置
- **贡献者**：理解 FLE 是修改选举行为或实现自定义选举算法的基础
