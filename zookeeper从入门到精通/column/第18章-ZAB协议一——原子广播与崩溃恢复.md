# 第18章：ZAB 协议一——原子广播与崩溃恢复

## 1. 项目背景

### 业务场景

在第 17 章中，你理解了 ZooKeeper 集群有三种角色（Leader、Follower、Observer），但你知道**写请求是如何在集群中保持一致性的**吗？

考虑一个场景：

- 客户端向 Follower 1 发送写请求："将 /config/db-url 的值改为 'new-db-url'"
- Follower 1 将请求转发给 Leader
- Leader 需要确保：**所有 Follower 要么都应用这个修改，要么都不应用**
- 即使 Leader 在广播过程中宕机，也要保证数据不丢失、不分裂

这就是 **ZAB（ZooKeeper Atomic Broadcast）协议** 要做的事情——保证 ZooKeeper 集群中数据的**顺序一致性**和**崩溃恢复**。

### 痛点放大

没有原子广播协议，分布式系统会面临：

- **脑裂**：集群分裂成两个"小集群"，各自写入不同的数据
- **状态不一致**：部分节点应用了修改，部分节点没有
- **写丢失**：Leader 宕机导致已提交的修改丢失

ZAB 协议就是 ZooKeeper 的"宪法"，规定了 Leader 和 Follower 之间如何通信、如何达成一致、Leader 宕机后如何恢复。

---

## 2. 项目设计

### 剧本式交锋对话

**场景**：小白的团队遇到了一个经典问题——集群中两个节点数据不一致。

**小白**：我们两个 ZooKeeper 节点（没有第三个节点），客户端在节点 A 上写入了一个配置，但节点 B 上读不到。重启后数据还丢了！

**大师**：这恰恰说明了 ZAB 协议的核心价值。ZAB 协议包含两个主要阶段：

1. **原子广播（Atomic Broadcast）**：Leader 如何将写请求广播给 Follower，并确保一致性
2. **崩溃恢复（Crash Recovery）**：Leader 宕机后，如何恢复集群并保证不丢数据

**原子广播的流程**：

```
Client → Follower → Leader（开始 ZAB 广播）

1. Leader 接收写请求
2. Leader 生成一个 Proposal（提案），分配 zxid（全局递增）
3. Leader 将 Proposal 广播给所有 Follower
4. 每个 Follower 收到 Proposal 后，写入事务日志，返回 ACK
5. Leader 收集 ACK，当收到超过半数（Quorum）ACK 后：
   a. 在自己的内存中提交该事务
   b. 广播 COMMIT 消息给所有 Follower
6. Follower 收到 COMMIT 后，在内存中提交该事务
7. Leader 通知客户端写入成功
```

**小胖**：这都是 ZAB 的标准流程了。那 zxid 是怎么保证顺序性的？

**大师**：zxid（ZooKeeper Transaction ID）是整个协议的关键：

```
zxid = epoch（高 32 位） + counter（低 32 位）
                     ↓                      ↓
           Leader 任期编号        该任期内的递增序号
                ↑                          ↑
        每次选举新 Leader +1      每个写请求 +1

示例：
zxid = 0x100000001
  epoch   = 0x1  = 1（第一任 Leader）
  counter = 0x1  = 1（第一个写请求）

zxid = 0x100000002
  epoch   = 0x1  = 1（第一任 Leader）
  counter = 0x2  = 2（第二个写请求）
```

zxid 的关键性质：
- **全局唯一、单调递增**：zxid 越大，操作越"新"
- **包含 epoch**：即使 Leader 换届，zxid 仍然单调递增（epoch 自增保证新 Leader 的 zxid 一定大于旧 Leader）
- **用于选举**：选举时优先选择 zxid 最大的节点作为新 Leader（拥有最新数据）

> **技术映射**：zxid = 护照号（国家代码 + 序列号），epoch = 国家代码（每届政府更换一次编号），counter = 序列号（本届政府颁发的第 N 个护照）

**小白**：那 ZAB 和两阶段提交（2PC）有什么区别？看起来都是"先提议，再确认，再提交"。

**大师**：好问题。ZAB 和 2PC 有本质区别：

| 特性 | 2PC（两阶段提交） | ZAB（ZooKeeper Atomic Broadcast） |
|------|-----------------|----------------------------------|
| 协调者 | 阻塞等待所有参与者 | Leader 流水线处理，不阻塞 |
| 性能 | 差（协调者瓶颈） | 好（流水线 + Quorum） |
| 容错 | 协调者宕机时参与者阻塞 | Leader 宕机自动选举恢复 |
| 提交条件 | 所有参与者 ACK | 超过半数（Quorum）ACK |
| 消息模式 | 同步阻塞 | 异步流水线 |

ZAB 优化的核心就是**流水线**：Leader 不需要等待上一个 Proposal 提交完毕就可以发送下一个 Proposal，性能远高于 2PC。

**小胖**：那如果广播过程中 Leader 宕机了怎么办？有些 Follower 收到了 Proposal 并返回了 ACK，有些没有。数据会丢失吗？

**大师**：这就是 ZAB 的**崩溃恢复**要做的事。Leader 宕机后，ZooKeeper 会：

1. 触发新 Leader 选举
2. 新 Leader 会**保证所有已提交的 Proposal 不丢失**
3. 新 Leader 会**回滚所有未提交的 Proposal**

具体来说，新 Leader 会做以下操作：

```
Leader 宕机时状态：
  ├── Proposal 1（zxid = 0x100000001）：已被 Quorum ACK → 已提交
  ├── Proposal 2（zxid = 0x100000002）：已被 Quorum ACK → 已提交
  └── Proposal 3（zxid = 0x100000003）：仅部分 ACK → 未提交

新 Leader 恢复后：
  1. 从多数 Follower 中确认最大已提交的 zxid（0x100000002）
  2. 保证 Proposal 1 和 2 的数据不丢失（通过快照 + 事务日志）
  3. 回滚 Proposal 3（未提交的 Proposal 直接丢弃）
  4. 客户端如果收到 Proposal 3 的成功响应 → 出现"幻读"
```

> **技术映射**：ZAB 崩溃恢复 = 数据库断电恢复，redo log 恢复已提交事务，undo log 回滚未提交事务

---

## 3. 项目实战

### 环境准备

- ZooKeeper 3.9.x 集群（3 节点 Docker Compose）
- `zkCli.sh` 命令行
- `nc` 命令

### 分步实现

#### 步骤 1：观察一次写入请求的完整 ZAB 广播

```bash
# 1. 找到 Leader 节点
for i in 1 2 3; do
  echo "Node $i: $(echo stat | nc -w 2 127.0.0.1 $((2180 + i)) | grep Mode)"
done

# 2. 在 Leader 节点上开启 DEBUG 日志（观察 ZAB 广播）
# 修改 log4j.properties
# 在 conf/log4j.properties 中添加：
# log4j.logger.org.apache.zookeeper.server.quorum=DEBUG

# 3. 重启 Leader（或者整个集群）
# 注意：生产环境不要随便启用 DEBUG 日志

# 4. 执行一次写入
./bin/zkCli.sh -server 127.0.0.1:2181
create /zab-test "hello-zab"

# 5. 查看 Leader 日志（如果有权限）
# 日志中应该能看到：
# LEADER: Proposing:: 0x100000001
# LEADER: Got ACK from follower:2
# LEADER: Got ACK from follower:3
# LEADER: Committing:: 0x100000001
```

#### 步骤 2：观察 zxid 的递增

```bash
# 连接 ZooKeeper
./bin/zkCli.sh -server 127.0.0.1:2181

# 创建节点并观察 zxid
create /zxid-test "v1"
stat /zxid-test
# cZxid = 0x200000001  ← 创建时的事务 ID

# 修改节点
set /zxid-test "v2"
stat /zxid-test
# mZxid = 0x200000002  ← 修改后 mzxid 递增了

# 再次修改
set /zxid-test "v3"
stat /zxid-test
# mZxid = 0x200000003  ← 每次修改 mzxid +1

quit
```

#### 步骤 3：编写程序追踪 zxid

创建 `ZxidTracker.java`：

```java
package com.zkdemo.zab;

import org.apache.zookeeper.*;
import org.apache.zookeeper.data.Stat;

public class ZxidTracker {
    private static final String ZK_URL = "127.0.0.1:2181";
    private static final String PATH = "/zxid-demo";

    public static void main(String[] args) throws Exception {
        ZooKeeper zk = new ZooKeeper(ZK_URL, 5000, event -> {});
        Thread.sleep(1000);

        System.out.println("=== ZooKeeper zxid 追踪演示 ===\n");

        // 创建节点
        Stat createStat = new Stat();
        zk.create(PATH, "initial".getBytes(), ZooDefs.Ids.OPEN_ACL_UNSAFE, CreateMode.PERSISTENT);
        zk.getData(PATH, false, createStat);

        System.out.printf("创建后: czxid=0x%x, mzxid=0x%x, dataVersion=%d%n",
                createStat.getCzxid(), createStat.getMzxid(), createStat.getVersion());

        // 多次更新
        for (int i = 1; i <= 5; i++) {
            Stat stat = new Stat();
            zk.getData(PATH, false, stat);
            zk.setData(PATH, ("v" + i).getBytes(), stat.getVersion());

            zk.getData(PATH, false, stat);
            long epoch = stat.getMzxid() >> 32;      // 高 32 位 = epoch
            long counter = stat.getMzxid() & 0xFFFFFFFFL; // 低 32 位 = counter

            System.out.printf("更新 %d: mzxid=0x%x (epoch=%d, counter=%d), dataVersion=%d%n",
                    i, stat.getMzxid(), epoch, counter, stat.getVersion());
        }

        zk.delete(PATH, -1);
        zk.close();
    }
}
```

**预期输出**：

```
=== ZooKeeper zxid 追踪演示 ===

创建后: czxid=0x200000001, mzxid=0x200000001, dataVersion=0
更新 1: mzxid=0x200000002 (epoch=2, counter=2), dataVersion=1
更新 2: mzxid=0x200000003 (epoch=2, counter=3), dataVersion=2
更新 3: mzxid=0x200000004 (epoch=2, counter=4), dataVersion=3
更新 4: mzxid=0x200000005 (epoch=2, counter=5), dataVersion=4
更新 5: mzxid=0x200000006 (epoch=2, counter=6), dataVersion=5
```

#### 步骤 4：模拟 Leader 宕机观察 ZAB 恢复

```bash
# 1. 确定 Leader
LEADER_PORT=$(for i in 1 2 3; do
  port=$((2180 + i))
  mode=$(echo stat | nc -w 2 127.0.0.1 $port | grep "Mode" | awk '{print $2}')
  if [ "$mode" = "leader" ]; then
    echo $port
    break
  fi
done)
echo "Leader port: $LEADER_PORT"

# 2. 停止 Leader
LEADER_CONTAINER=$(docker ps --format "{{.Names}}" | grep "$(echo stat | nc -w 2 127.0.0.1 $LEADER_PORT | head -1 | awk '{print $4}')")

# 获取 Leader 对应的容器名
# 更简单的方式：直接停止 Leader
docker stop $(docker ps --format "{{.Names}}" | grep "leader-candidate\|follower")
# 或者直接全部 stop 再逐个 start

# 更好的测试方式：
echo "=== 观察 Leader 宕机后的选举 ==="
echo "当前 Leader 端口: $LEADER_PORT"

# 3. 停止 Leader 节点
docker stop zk-leader-candidate

# 4. 观察剩余节点选举
sleep 3
for i in 1 2 3; do
  port=$((2180 + i))
  echo "Node $(echo stat | nc -w 2 127.0.0.1 $port 2>/dev/null | grep Mode)"
done

# 5. 验证数据不丢失
./bin/zkCli.sh -server 127.0.0.1:2182 get /zab-test
# 应该输出：hello-zab（数据不丢失）

# 6. 恢复 Leader
docker start zk-leader-candidate
```

### 测试验证

```bash
# 验证 ZAB 广播的核心性质
# 1. 在 Leader 上创建节点
./bin/zkCli.sh -server 127.0.0.1:2181 create /zab-verify "verified"

# 2. 在 Follower 上读取（数据已同步）
./bin/zkCli.sh -server 127.0.0.1:2182 get /zab-verify
# 输出：verified

# 3. 在 Observer 上读取（数据已同步，可能有毫秒级延迟）
./bin/zkCli.sh -server 127.0.0.1:2184 get /zab-verify
# 输出：verified
```

### 可能遇到的坑

| 问题 | 原因 | 解决 |
|------|------|------|
| zxid 不递增 | 连接到了 Observer，Observer 不处理写请求 | Leader 和 Follower 才有写能力 |
| 数据丢失 | Quorum 不足导致写入"看起来成功"但实际未提交 | 确保集群满足 Quorum |

---

## 4. 项目总结

### ZAB 协议核心流程图

```
写请求（Client → Follower → Leader）

Leader:
  ├── 1. 生成 Proposal (zxid)
  ├── 2. 广播到所有 Follower
  ├── 3. 等待 ACK（半数以上）
  ├── 4. 提交到内存
  └── 5. 广播 Commit

Follower:
  ├── 1. 收到 Proposal
  ├── 2. 写入事务日志
  ├── 3. 回复 ACK
  └── 4. 收到 Commit → 提交到内存
```

### 优点 & 缺点

| 维度 | 优点 | 缺点 |
|------|------|------|
| 一致性 | 强一致性（写入后所有节点最终一致） | 写操作所有节点都需要落盘，延迟较高 |
| 性能 | 流水线处理，无需等待前一个完成 | 单 Leader 瓶颈 |
| 容错 | Quorum 机制，容忍少数节点故障 | 选举期间写操作暂停 |

### 适用场景

- 所有需要 ZooKeeper 强一致性的场景
- 配置中心、分布式锁、Leader 选举

### 注意事项

- ZAB 不是 2PC（两阶段提交），不需要所有节点确认
- zxid 的高 32 位（epoch）在 Leader 选举时递增，确保新 Leader 发起的 zxid 大于旧 Leader
- 写入延迟 = 事务日志落盘时间 + 网络延迟，SSD 对写入性能至关重要

### 常见踩坑经验

**故障 1：zxid 用尽**

现象：写入时报错 `zxid overflow`。

根因：低 32 位 counter 最大为 0xFFFFFFFF（约 42 亿）。一个 epoch 内写入超过 42 亿次后会溢出。解法：重新选举 Leader（epoch +1，counter 重置为 0）。

实际中很难达到：42 亿次写入，假设每秒 10 万次，需要 42,000 秒 ≈ 11.7 小时。

**故障 2：新 Leader 无法提交旧 Leader 的 Proposal**

现象：Leader 选举后，新 Leader 认为旧 Leader 的某个 Proposal 已提交，但多数 Follower 没有该 Proposal 的日志。

根因：旧 Leader 已经向客户端回复"成功"，但还没有来得及将 COMMIT 广播到半数以上 Follower。当新 Leader 发现没有多数节点有该 Proposal，会回滚它，导致"已成功的写入丢失"。

解决方案：这是 ZAB 协议的理论限制。业务层需要加入幂等性设计。

### 思考题

1. ZAB 协议中，为什么需要 Quorum（超过半数）而不是全部节点确认？如果要求全部节点确认，ZooKeeper 的可用性会怎样？
2. 假设 Leader 在广播 COMMIT 消息前宕机了。此时 Follower A 收到了 Proposal 和 COMMIT，Follower B 只收到了 Proposal。新 Leader 如何确定事务是否应该提交？它和 Paxos / Raft 有何不同？

### 推广计划提示

- **开发**：理解 ZAB 协议有助于理解 ZooKeeper 的写入行为和一致性模型
- **运维**：关注事务日志落盘延迟（磁盘 IO），这是 ZAB 广播的主要瓶颈
- **架构师**：ZAB 是 ZK 最核心的协议，与 Raft、Paxos 并称为分布式一致性协议的三大主流方案
