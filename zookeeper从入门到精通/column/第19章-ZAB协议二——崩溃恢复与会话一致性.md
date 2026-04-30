# 第19章：ZAB 协议二——崩溃恢复与会话一致性

## 1. 项目背景

### 业务场景

第 18 章理解了 ZAB 的原子广播——Leader 如何保证一次写入的一致性。本章聚焦更棘手的场景：**Leader 宕机了怎么办？**

第 18 章末尾提到：Leader 宕机后，ZooKeeper 需要：

1. 选出新 Leader（Leader 选举——第 20 章详述）
2. 新 Leader 保证已提交的 Proposal 不丢失
3. 新 Leader 回滚未提交的 Proposal
4. 确保客户端会话在新 Leader 上保持一致

这 4 步合起来就是 ZAB 的**崩溃恢复（Crash Recovery）**。

### 痛点放大

没有崩溃恢复，Leader 宕机后：

- **已提交的数据丢失**：客户端收到了"写入成功"的响应，但新 Leader 不知道这个写入
- **数据分裂**：Leader 和部分 Follower 认为事务已提交，另一部分 Follower 没有
- **会话丢失**：客户端重连新 Leader 后，发现自己的临时节点和 Watcher 都不存在了
- **配置过期**：客户端拿着旧配置继续运行，直到出现运行时错误才发现

ZAB 的崩溃恢复设计目标就是：**Leader 宕机后，集群可以自动恢复且数据零丢失**。

---

## 2. 项目设计

### 剧本式交锋对话

**场景**：小胖的 ZooKeeper 集群 Leader 宕机了，恢复后他发现部分客户端报 "Session expired" 异常。

**小胖**：Leader 挂了 5 秒就选出新 Leader 了，但为什么那么多客户端连接都断开了？

**大师**：这取决于客户端连接到了哪个节点。如果客户端连接在宕机的 Leader 上，在选举期间它一直处于断连状态。如果 Session Timeout 设置得比较小（比如 5 秒），选举期间的断开时间就可能导致会话过期。

但崩溃恢复的核心问题是：**新 Leader 如何确保数据不丢失**。这个恢复过程分两个阶段：

**阶段一：Leader 选举后的数据同步**

新 Leader 选定后，需要将 Follower 的数据同步到和自己一致的状态：

```
新 Leader 视角：

1. 从所有 Follower 中收集它们的最后提交的 zxid（lastCommittedZxid）
2. 找到所有节点中最大的 lastCommittedZxid（保证不丢失）
3. 如果有 Follower 落后于自己：
   a. 落后太多 → 发送完整快照（SNAP）
   b. 落后不多 → 只发送增量事务日志（DIFF）
   c. Follower 有 Leader 没有的事务 → 回滚这些事务（TRUNC）
4. 等待 Quorum 的 Follower 同步完成
5. 宣布"恢复完成"，开始处理新的写请求
```

**小白**：我注意到有三种同步方式：SNAP、DIFF、TRUNC。能具体说说吗？

**大师**：

```bash
# SNAP（快照同步）—— Follower 落后太多
新 Leader：我有 zxid=100 的事务
Follower： 我只有 zxid=50 的事务（落后 50 个）
→ Leader 将当前完整的快照发给 Follower
→ Follower 清空自己的数据，加载快照
→ 追赶方式：SNAP

# DIFF（差异同步）—— Follower 少量落后
新 Leader：我有 zxid=100 的事务
Follower： 我有 zxid=98 的事务（落后 2 个）
→ Leader 只发送 zxid=99 和 zxid=100 的事务日志
→ Follower 逐一应用这些事务
→ 追赶方式：DIFF

# TRUNC（截断同步）—— Follower 有 Leader 没有的事务
新 Leader：我有 zxid=100 的事务
Follower： 我有 zxid=102 的事务（Follower 多了 2 个）
→ Leader 告诉 Follower：回滚到 zxid=100
→ Follower 删除 zxid=101 和 zxid=102 的事务
→ 追赶方式：TRUNC
```

> **技术映射**：SNAP = 从备份完全恢复，DIFF = 应用增量 binlog，TRUNC = git reset --hard

**小胖**：TRUNC 场景有意思！Follower 怎么会有 Leader 没有的事务？难道 Follower 自己也能写？

**大师**：Follower 本身不能写，这种情况发生在：

1. 旧 Leader 广播了 Proposal（zxid=101, 102）给这个 Follower
2. 旧 Leader 在提交前宕机了（没有 COMMIT）
3. 新 Leader 被选出来，但新 Leader 没有这些 Proposal（因为旧 Leader 还没来得及广播给新 Leader）
4. 这个 Follower 的事务日志中就有了 zxid=101, 102，但新 Leader 没有
5. TRUNC 回滚掉这些未提交的事务

**小白**：那客户端发起的写入请求，如果已经收到了"成功"响应，但在 Leader 宕机时还没有被 COMMIT 到多数 Follower，会怎样？

**大师**：这种情况叫"**幻读（Phantom Read）**"。客户端 A 收到了写入成功响应（但实际上只写入了 Leader 自身，没有 Quorum ACK），然后 Leader 宕机，新 Leader 回滚了该事务。客户端 A 以为自己写入成功了，但实际数据没有生效。

解决方案：
1. **客户端重试**：收到异常后重试写入
2. **幂等性设计**：使用唯一的业务 ID，即使重试也不会产生副作用
3. **同步写**：可以调用 `sync()` 方法强制刷新，但会降低性能

> **技术映射**：幻读 = 银行柜台说转账成功了，但实际后台系统还没有完成，然后柜台系统崩溃了，恢复后转账被回滚

**小白**：那会话一致性呢？Leader 宕机后，客户端重连到新 Leader，它的会话还在吗？

**大师**：会话信息是存储在 Follower 上的（不只是 Leader）。当新 Leader 被选出来时，它会从多数 Follower 中恢复会话信息：

1. 客户端之前连接在旧的 Leader 上（假设 sessionId=1000）
2. 旧 Leader 宕机，新 Leader 从 Follower 的会话列表中恢复 sessionId=1000
3. 如果客户端在 Session Timeout 内重连成功→会话有效
4. 如果超过 Session Timeout→会话过期

关键是：**会话状态是跟随 Quorum 的**，不只是 Leader 单点。所以只要在 Session Timeout 内重连到任何 Follower，会话就有可能恢复。

---

## 3. 项目实战

### 环境准备

- ZooKeeper 3.9.x 集群（3 节点 Docker Compose）
- `zkCli.sh` 命令行
- Java 11+

### 分步实现

#### 步骤 1：模拟 Leader 宕机并观察数据恢复

```bash
#!/bin/bash
# 模拟 Leader 宕机和数据恢复

echo "=== Step 1: 初始化测试数据 ==="
./bin/zkCli.sh -server 127.0.0.1:2181 create /crash-test "pre-crash"
./bin/zkCli.sh -server 127.0.0.1:2181 create /crash-test/data "important-data"

echo "=== Step 2: 找出 Leader ==="
for i in 1 2 3; do
  port=$((2180 + i))
  mode=$(echo stat | nc -w 2 127.0.0.1 $port | grep "Mode" | awk '{print $2}')
  if [ "$mode" = "leader" ]; then
    LEADER_PORT=$port
    LEADER_CONTAINER="zookeeper-$i"
    echo "Leader: 节点 $i (端口 $port)"
  fi
done

echo "=== Step 3: 模拟 Leader 宕机 ==="
docker stop $LEADER_CONTAINER
echo "Leader 已停止: $LEADER_CONTAINER"

echo "=== Step 4: 等待新 Leader 选出 ==="
sleep 5

echo "=== Step 5: 验证数据是否恢复 ==="
# 连接到剩余节点
./bin/zkCli.sh -server 127.0.0.1:2182 get /crash-test/data
# 应该输出：important-data（数据不丢失）

echo "=== Step 6: 验证新节点能写入 ==="
./bin/zkCli.sh -server 127.0.0.1:2182 create /crash-test/new-data "post-crash"
echo "新 Leader 写入成功!"

echo "=== Step 7: 恢复旧 Leader ==="
docker start $LEADER_CONTAINER
sleep 5

echo "=== Step 8: 验证旧 Leader 同步了新数据 ==="
OLD_PORT=$(echo $LEADER_PORT)
./bin/zkCli.sh -server 127.0.0.1:$OLD_PORT get /crash-test/new-data
# 应该输出：post-crash（数据已同步）

# 清理
./bin/zkCli.sh -server 127.0.0.1:2182 deleteall /crash-test
```

#### 步骤 2：模拟 TRUNC 场景

创建 `TruncScenario.java`：

```java
package com.zkdemo.zab;

import org.apache.zookeeper.*;
import org.apache.zookeeper.data.Stat;

import java.util.concurrent.CountDownLatch;

/**
 * 模拟 TRUNC 场景：
 * 1. 在旧 Leader 上写入（未被 Quorum ACK）
 * 2. 旧 Leader 宕机
 * 3. 新 Leader 回滚未提交的事务
 */
public class TruncScenario {
    private static final String ZK_URL = "127.0.0.1:2181";

    public static void main(String[] args) throws Exception {
        System.out.println("=== ZAB TRUNC 场景模拟 ===");
        System.out.println("该演示需要手动操作 ZooKeeper 集群\n");

        CountDownLatch latch = new CountDownLatch(1);
        ZooKeeper zk = new ZooKeeper(ZK_URL, 5000, event -> {
            if (event.getState() == Watcher.Event.KeeperState.SyncConnected) {
                System.out.println("已连接 ZooKeeper");
                latch.countDown();
            }
        });
        latch.await();

        // 创建测试数据
        Stat stat = zk.exists("/trunc-test", false);
        if (stat == null) {
            zk.create("/trunc-test", "before-crash".getBytes(),
                    ZooDefs.Ids.OPEN_ACL_UNSAFE, CreateMode.PERSISTENT);
            System.out.println("创建 /trunc-test = before-crash");
        }

        System.out.println("\n当前数据: " + new String(zk.getData("/trunc-test", false, null)));

        System.out.println("\n========= 手动操作 =========");
        System.out.println("1. 观察当前 zxid: stat /trunc-test");
        System.out.println("2. 停止 Leader 容器");
        System.out.println("3. 观察新 Leader 选举");
        System.out.println("4. 验证数据是否一致: get /trunc-test");
        System.out.println("============================\n");

        // 保持连接
        Thread.sleep(30000);
        zk.close();
    }
}
```

手工测试步骤：

```bash
# 1. 运行程序确认初始数据
# 2. 找到并停止 Leader
# 3. 在 Leader 停止的瞬间修改数据（模拟未提交的 Proposal）
# 4. 观察新 Leader 上的数据

# 简化实操：
# 1. 通过 Follower（2182）写入
./bin/zkCli.sh -server 127.0.0.1:2182
set /trunc-test "after-crash"

# 2. 如果在 Leader 停止的瞬间写入，且设置为同步模式（sync）
# 有可能出现"写入成功但数据丢失"的情况，这正是 TRUNC 场景
```

#### 步骤 3：验证会话一致性

创建 `SessionConsistencyTest.java`：

```java
package com.zkdemo.zab;

import org.apache.zookeeper.*;
import org.apache.zookeeper.data.Stat;

import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

public class SessionConsistencyTest {
    public static void main(String[] args) throws Exception {
        System.out.println("=== 会话一致性测试 ===\n");

        // 连接到 3 节点集群中的节点 1
        CountDownLatch connectLatch = new CountDownLatch(1);
        ZooKeeper zk = new ZooKeeper("127.0.0.1:2181", 30000, event -> {
            System.out.println("[Watcher] 状态: " + event.getState());
            if (event.getState() == Watcher.Event.KeeperState.SyncConnected) {
                connectLatch.countDown();
            }
        });
        connectLatch.await();

        long sessionId = zk.getSessionId();
        System.out.println("Session ID: " + Long.toHexString(sessionId));

        // 创建临时节点（绑定到此会话）
        zk.create("/session-consistency", "alive".getBytes(),
                ZooDefs.Ids.OPEN_ACL_UNSAFE, CreateMode.EPHEMERAL);
        System.out.println("临时节点 /session-consistency 已创建");

        // 模拟 Leader 宕机
        System.out.println("\n请手动停止 ZooKeeper Leader 容器...");
        System.out.println("然后在 20 秒内恢复它...\n");

        TimeUnit.SECONDS.sleep(25);

        // 检查会话是否仍然有效
        Stat stat = zk.exists("/session-consistency", false);
        if (stat != null) {
            System.out.println("✓ 会话一致: 临时节点仍然存在，sessionId=" + Long.toHexString(sessionId));
        } else {
            System.out.println("✗ 会话已过期: 临时节点被删除，需要重建连接");
        }

        zk.close();
    }
}
```

### 测试验证

```bash
# 验证崩溃恢复
# 1. 准备数据
zkCli.sh create /recovery-test "recovery-data"
zkCli.sh create /recovery-test/node1 "node1"

# 2. 查看当前 zxid
zkCli.sh stat /recovery-test

# 3. 停止 Leader 5 秒后恢复
docker stop zk-leader-candidate
sleep 5
docker start zk-leader-candidate

# 4. 验证数据
zkCli.sh get /recovery-test/node1
# 输出：node1（数据不丢失）
```

### 可能遇到的坑

| 问题 | 原因 | 解决 |
|------|------|------|
| 数据"回滚" | 未提交的 Proposal 被回滚（TRUNC） | 业务做幂等性保证 |
| 节点恢复后无法加入集群 | 数据太旧或 epoch 不匹配 | 清空 dataDir，重新同步 |
| 选举期间客户端超时 | Session Timeout 太小 | 增大 Session Timeout 到 30s+ |

---

## 4. 项目总结

### 崩溃恢复流程图

```
Leader 宕机
    ↓
开始 Leader 选举
    ↓
选出新 Leader
    ↓
新 Leader 收集所有 Follower 的 lastCommittedZxid
    ↓
对每个 Follower：
    ├── Follower.zxid < Leader.zxid - 阈值  → SNAP
    ├── Follower.zxid < Leader.zxid          → DIFF
    └── Follower.zxid > Leader.zxid          → TRUNC
    ↓
Quorum 的 Follower 同步完成
    ↓
新 Leader 宣布恢复完成，开始处理写请求
    ↓
原 Leader 节点恢复后，以 Follower 身份加入集群
```

### 优点 & 缺点

| 维度 | 优点 | 缺点 |
|------|------|------|
| 数据安全 | 已提交数据永不丢失 | 未提交数据可能回滚（幻读） |
| 恢复速度 | 毫秒到秒级选举 + 毫秒级同步 | 选举期间写操作暂停 |
| 一致性 | 最终所有节点数据一致 | 同步期间读请求可能读到旧数据 |

### 适用场景

ZAB 崩溃恢复机制适用于所有 ZooKeeper 集群，其保证：
- **已提交的数据不丢失**（安全性）
- **只要 Quorum 在，最终可用**（活性）

### 注意事项

- 客户端需要处理"幻读"：收到成功响应的事务可能在崩溃恢复中被回滚
- 建议在业务层加入幂等性设计，或使用 `sync()` 方法
- 事务日志的持久化是崩溃恢复的关键——不要关闭 fsync

### 常见踩坑经验

**故障 1：重启后的旧 Leader 导致数据回退**

现象：Leader 宕机后，旧 Leader 恢复时以 Follower 身份加入集群，但它的事务日志中有比当前 Leader 更新的未提交事务。当前 Leader 发现后强制 TRUNC 该 Follower，导致这个 Follower 刚恢复的数据消失。

解决方案：这是正常行为。新 Leader 保证整个集群的一致性，`TRUNC` 会回滚 Follower 上多余的未提交事务。只要等到该 Follower 从 Leader 同步到最新数据即可。

**故障 2：磁盘写缓存导致的事务日志丢失**

现象：ZooKeeper 报告事务已写入日志，但宕机后这些事务丢失了。

根因：操作系统或磁盘开启了写缓存。`fsync` 返回但数据实际上还在磁盘缓存中，掉电后丢失。ZooKeeper 依赖 `fsync` 的持久化保证，在 `zoo.cfg` 中配置 `forceSync=yes` 可强化（但会降低性能）。

### 思考题

1. 假设 5 节点集群，旧 Leader 广播了一个 Proposal（zxid=0x400000001），3 个 Follower 回复了 ACK，但旧 Leader 在发送 COMMIT 前宕机了。新 Leader 当选后，如何处理这个 Proposal？
2. 一个客户端在旧 Leader 上创建了一个临时节点，然后旧 Leader 宕机了。客户端在 Session Timeout 内重连到了新 Leader。这个临时节点还存在吗？为什么？

### 推广计划提示

- **开发**：理解 ZAB 崩溃恢复有助于设计健壮的 ZooKeeper 客户端，特别是在连接异常时正确处理重试
- **运维**：定期执行"领导切换演练"（手动停止 Leader），验证集群的自动恢复能力
- **架构师**：ZAB 的崩溃恢复设计是分布式一致性的经典实践，也是理解 Raft、Paxos 等协议的不错跳板
