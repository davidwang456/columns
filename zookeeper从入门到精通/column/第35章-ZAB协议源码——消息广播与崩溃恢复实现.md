# 第35章：ZAB 协议源码——消息广播与崩溃恢复实现

## 1. 项目背景

### 业务场景

第 18-19 章从理论层面学习了 ZAB 协议的原子广播和崩溃恢复。本章从源码层面验证这些理论——当客户端发起一个写请求时，Leader 如何将其转换成 Proposal、广播给 Follower、收集 ACK、提交事务；以及 Leader 宕机后，新 Leader 如何恢复数据。

### 源码定位

```
Leader.java              ← Leader 角色（propose/processAck/lead）
Follower.java            ← Follower 角色（followLeader/processPacket）
Learner.java             ← Learner 基类（syncWithLeader）
QuorumPacket.java        ← 集群通信消息
```

---

## 2. 项目设计

### 剧本式交锋对话

**场景**：小胖已经理解了 ZAB 理论，想知道代码中是如何实现 Proposal 广播的。

**大师**：Leader 广播 Proposal 的源码流程：

```
Leader.propose(request)
  ↓
1. 创建 QuorumPacket.Proposal
   Packet = {type=PROPOSAL, zxid, data}
   ↓
2. 放入 outstandingProposals（等待 ACK 的集合）
   outstandingProposals.put(zxid, packet)
   ↓
3. 发送给所有 Follower
   sendPacket(follower.sid, packet)
   ↓
4. Follower 回复 ACK
   Follower 调用 processAck()
   ↓
5. leader.processAck(sid, zxid)
   ↓
6. 统计 ACK 数量
   如果 ACK 数 ≥ Quorum → Commit
   ↓
7. leader.commit(zxid)
   更新本地 + 广播 COMMIT
```

**小白**：那 Follower 收到 PROPOSAL 后怎么处理的？

**大师**：Follower 的处理在 `Follower.processPacket()` 中：

```
Follower.followLeader()
  ↓
循环读取 Leader 发送的 QuorumPacket
  ↓
switch (packet.type) {
  case PROPOSAL:
    // 1. 写入事务日志
    // 2. 回复 ACK
    break;
  case COMMIT:
    // 1. 提交事务到内存
    break;
  case UPTODATE:
    // 同步完成
    break;
}
```

---

## 3. 项目实战

### 环境准备

- ZooKeeper 3.9.x 源码
- IntelliJ IDEA
- JDK 17+

### 分步实现

#### 步骤 1：Leader.propose()——广播 Proposal

```java
// 文件位置：org.apache.zookeeper.server.quorum.Leader
// 提案广播——Leader 收到写请求后调用

public Proposal propose(Request request) throws IOException {
    // 1. 构建 Proposal 包
    byte[] data = SerializeUtils.serializeRequest(request);
    // 构造 QuorumPacket：类型=PROPOSAL, zxid=request.zxid, data=事务数据
    QuorumPacket pp = new QuorumPacket(Leader.PROPOSAL,
            request.getHdr().getZxid(), data, null);

    // 2. 创建 Proposal 对象，放入 outstandingProposals
    Proposal p = new Proposal();
    p.packet = pp;
    p.request = request;
    outstandingProposals.put(request.getHdr().getZxid(), p);

    // 3. 发送给所有 Follower
    // 遍历所有投票节点（Follower，不包括 Observer）
    for (LearnerHandler f : learners) {
        // 如果 Follower 已同步到该 zxid，发送 Proposal
        if (f.getLearnerState().getLastZxidSeen() <= request.getHdr().getZxid()) {
            f.sendPacket(pp);
        }
    }

    return p;
}
```

#### 步骤 2：Leader.processAck()——收集 ACK

```java
// 文件位置：org.apache.zookeeper.server.quorum.Leader
// 处理 Follower 的 ACK

public synchronized void processAck(long sid, long zxid) {
    // 1. 查找对应的 Proposal
    Proposal p = outstandingProposals.get(zxid);
    if (p == null) {
        // Proposal 已经被提交，忽略
        return;
    }

    // 2. 记录该 Follower 的 ACK
    p.ackSet.add(sid);

    // 3. 判断是否达到 Quorum
    // 注意：ACK 集合包括 Leader 自己（自 ACK）
    if (p.ackSet.size() >= self.getQuorumVerifier().getVotingSize()) {
        // Quorum 达成！
        outstandingProposals.remove(zxid);

        // 4. 提交事务
        commit(zxid);

        // 5. 通知观察者
        inform(p);
    }
}
```

#### 步骤 3：Leader.commit()——提交事务

```java
// 文件位置：org.apache.zookeeper.server.quorum.Leader
// 提交事务

public void commit(long zxid) {
    // 1. 构建 COMMIT 消息
    QuorumPacket qp = new QuorumPacket(Leader.COMMIT, zxid, null, null);

    // 2. 发送给所有 Follower
    for (LearnerHandler f : learners) {
        if (f.getLearnerState().getLastZxidSeen() <= zxid) {
            f.sendPacket(qp);
        }
    }

    // 3. 提交到本地的 CommitProcessor
    // CommitProcessor 会交给 FinalRequestProcessor 更新 DataTree
    CommitProcessor commitProcessor = self.getZooKeeperServer()
            .getCommitProcessor();
    commitProcessor.commit(zxid, p.request);
}
```

#### 步骤 4：Follower.followLeader()——Follower 主循环

```java
// 文件位置：org.apache.zookeeper.server.quorum.Follower
// Follower 的主循环

void followLeader() throws InterruptedException {
    while (this.isRunning()) {
        // 1. 读取 Leader 发送的 QuorumPacket
        QuorumPacket qp = new QuorumPacket();

        try {
            // 从网络读取消息
            leaderOs.readRecord(qp, "packet");
        } catch (IOException e) {
            // 连接断开，退出循环，重新选举
            break;
        }

        // 2. 处理不同类型的消息
        processPacket(qp);
    }
}

// 处理 Leader 发来的消息
protected void processPacket(QuorumPacket qp) throws Exception {
    switch (qp.getType()) {
        case Leader.PROPOSAL:
            // 收到 Proposal：写入事务日志
            Request request = SerializeUtils.deserializeRequest(qp.getData());
            if (request.getHdr() != null) {
                // 写入事务日志
                zk.getZKDatabase().append(request);
                // 回复 ACK
                QuorumPacket ack = new QuorumPacket(Leader.ACK,
                        qp.getZxid(), null, null);
                oa.writeRecord(ack, "packet");
            }
            break;

        case Leader.COMMIT:
            // 收到 COMMIT：提交到 CommitProcessor
            CommitProcessor commitProcessor = zk.getCommitProcessor();
            long zxid = qp.getZxid();
            commitProcessor.commit(zxid, ??);
            break;

        case Leader.COMMITANDACTIVATE:
            // 提交并激活（新 Leader 就绪）
            break;

        case Leader.UPTODATE:
            // Leader 告诉 Follower：数据已是最新
            break;

        case Leader.SNAP:
            // 快照同步
            break;

        case Leader.TRUNC:
            // 截断同步（Follower 有多余事务）
            break;

        case Leader.DIFF:
            // 差异同步
            break;
    }
}
```

#### 步骤 5：崩溃恢复——新 Leader 的数据同步

```java
// 文件位置：org.apache.zookeeper.server.quorum.Leader
// 新 Leader 的启动逻辑

void lead() throws IOException, InterruptedException {
    // 1. 恢复数据
    self.getZooKeeperServer().getZKDatabase().init();

    // 2. 等待 Follower 连接
    for (LearnerHandler f : learners) {
        f.start();
    }

    // 3. 等待 Quorum 的 Follower 同步完成
    waitForEpochAck(self.getId(), self.getAcceptedEpoch());
    waitForNewLeaderAck(self.getId(), self.getZxid());
}

// 数据同步方式选择
// 在 LearnerHandler.run() 中

// LearnerHandler 是每个 Follower 的处理器

// 同步方式判断逻辑：
long lastCommitted = leader.zk.getZKDatabase().getLastProcessedZxid();
long peerLastZxid = learnerState.getLastZxidSeen();

if (peerLastZxid == lastCommitted) {
    // Follower 数据已是最新 → 直接发送 UPTODATE
    queuePacket(Leader.UPTODATE);
} else if (peerLastZxid < lastCommitted) {
    // Follower 落后
    if (peerLastZxid < leader.zk.getZKDatabase()
            .getDataTreeLastProcessedZxid()) {
        // 落后太多 → SNAP（快照同步）
        queuePacket(Leader.SNAP);
    } else {
        // 落后一点点 → DIFF（差异同步）
        queuePacket(Leader.DIFF);
        // 发送缺失的事务日志
        sendDiffTxns(peerLastZxid, lastCommitted);
    }
} else {
    // Follower 有多余事务 → TRUNC（截断同步）
    queuePacket(Leader.TRUNC);
    // 告诉 Follower 回滚到哪个 zxid
    QuorumPacket trunc = new QuorumPacket(Leader.TRUNC,
            lastCommitted, null, null);
}
```

#### 步骤 6：添加调试日志追踪 ZAB 广播

在 `Leader.java` 的 `propose()` 和 `processAck()` 中添加调试日志：

```java
// 在 Leader.java 中的调试代码

public Proposal propose(Request request) throws IOException {
    System.out.println("[ZAB-Leader] PROPOSE: zxid=0x"
            + Long.toHexString(request.getHdr().getZxid())
            + ", type=" + request.getHdr().getType()
            + ", path=" + request.path
            + ", 发送给 " + learners.size() + " 个 Follower");

    // ... 原有代码 ...
}

public synchronized void processAck(long sid, long zxid) {
    System.out.println("[ZAB-Leader] ACK: sid=" + sid
            + ", zxid=0x" + Long.toHexString(zxid)
            + ", ackSet-size=" + (p != null ? p.ackSet.size() : 0)
            + "/" + self.getQuorumVerifier().getVotingSize());

    if (p.ackSet.size() >= self.getQuorumVerifier().getVotingSize()) {
        System.out.println("[ZAB-Leader] QUORUM REACHED! zxid=0x"
                + Long.toHexString(zxid));
    }

    // ... 原有代码 ...
}
```

编译运行并测试：

```bash
# 1. 修改源码添加日志
# 2. 重新编译
mvn compile -pl zookeeper-server -DskipTests

# 3. 启动 3 节点集群（伪集群或 Docker）
# 4. 写入数据
./bin/zkCli.sh -server 127.0.0.1:2181
create /zab-debug "test"

# 5. 查看 Leader 控制台的输出
# [ZAB-Leader] PROPOSE: zxid=0x200000001, type=1, path=/zab-debug, 发送给 2 个 Follower
# [ZAB-Leader] ACK: sid=2, zxid=0x200000001, ackSet-size=1/3
# [ZAB-Leader] ACK: sid=3, zxid=0x200000001, ackSet-size=2/3
# [ZAB-Leader] QUORUM REACHED! zxid=0x200000001
```

#### 步骤 7：以 zxid 为线索追踪写入链路

```java
/**
 * zxid 追踪：追一条写入请求的完整 ZAB 路径
 * 
 * 1. Client → Follower: setData /test "new-value"
 * 2. Follower → Leader: 转发写请求（Leader.REQUEST）
 * 3. Leader.propose(): 生成 zxid = 0x200000005, 广播 PROPOSAL
 * 4. Follower.processPacket(): 收到 PROPOSAL, 写日志, 回 ACK
 * 5. Leader.processAck(): 收到 ACK, 检测 Quorum
 * 6. Leader.commit(): 广播 COMMIT, 提交到 DataTree
 * 7. Follower.commit(): 收到 COMMIT, 提交到 DataTree
 * 8. Follower → Client: 返回成功响应
 */
```

### 测试验证

```bash
# 验证 ZAB 广播
# 1. 在 Leader 控制台开启 Debug 日志
# 2. 执行 setData 操作
# 3. 观察控制台输出 PROPOSE → ACK → COMMIT 的完整链路

# 验证崩溃恢复
# 1. 创建一个测试节点
# 2. 找到并停止 Leader
# 3. 观察新 Leader 是否保留了这个节点
# 4. 验证 TRUNC 场景：在 Leader 停止瞬间写入，观察是否回滚
```

### 可能遇到的坑

| 问题 | 原因 | 解决 |
|------|------|------|
| proposal 未发送 | Follower 尚未完成 UPTODATE 同步 | 检查 LearnerHandler 状态 |
| processAck 中的 Quorum 计数 | 包括 Leader 自 ACK | Quorum = 投票节点数 / 2 + 1 |
| 崩溃恢复后数据不一致 | SNAP/DIFF/TRUNC 选择错误 | 检查 lastProcessedZxid 对比逻辑 |

---

## 4. 项目总结

### ZAB 源码关键类

| 类 | 方法 | 作用 |
|-----|------|------|
| `Leader.propose()` | 生成 Proposal 并广播 | 原子广播起点 |
| `Leader.processAck()` | 处理 Follower ACK，检测 Quorum | 一致性保证 |
| `Leader.commit()` | 提交事务，广播 COMMIT | 事务生效 |
| `Follower.processPacket()` | 处理 PROPOSAL/COMMIT/SNAP/DIFF/TRUNC | Follower 消息处理 |
| `LearnerHandler.run()` | 数据同步逻辑（SNAP/DIFF/TRUNC 选择） | 崩溃恢复 |

### ZAB 协议数据流

```
Leader                          Follower(s)
  │                                 │
  ├─ PROPOSAL(zxid, data) ────────►│
  │                                 ├─ 写事务日志
  │◄───────────────── ACK(zxid) ────│
  │                                 │
  │ (收集 Quorum ACK)               │
  │                                 │
  ├─ COMMIT(zxid) ─────────────────►│
  │                                 ├─ 提交到 DataTree
  │                                 │
  │ (返回客户端成功)                  │
```

### 思考题

1. `Leader.propose()` 中，为什么需要 `outstandingProposals` 这个 Map？如果 Follower 回复 ACK 的速度很慢，这个 Map 会越来越大吗？
2. 崩溃恢复中，新 Leader 需要决定使用 SNAP、DIFF 还是 TRUNC 来同步某个 Follower。这个决策逻辑在 `LearnerHandler.run()` 中，看一下源码，它是如何根据 `peerLastZxid` 和 `lastCommitted` 的关系来选择同步方式的？

### 推广计划提示

- **开发**：理解 ZAB 源码有助于深入理解 ZooKeeper 的一致性模型
- **运维**：ZAB 广播的性能瓶颈在 Leader 的 `propose()` 方法——需要序列化请求、发送给所有 Follower、等待 ACK
- **贡献者**：修改 ZAB 协议行为时，需要修改 Leader.java 和 Follower.java 中的相关方法
