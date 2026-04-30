# 第33章：请求处理器链——RequestProcessor 管道源码剖析

## 1. 项目背景

### 业务场景

第 32 章介绍了 ZooKeeper 源码的整体架构。本章深入最核心的机制——**请求处理器链（RequestProcessor Pipeline）**。

当客户端发送一个写请求（如 `setData("/config/db-url", "new-url")`），这个请求在 ZooKeeper 服务端内部经过了哪些处理步骤？每个步骤做了什么？

答案就是 RequestProcessor 管道——它是 ZooKeeper 服务端处理所有请求的核心机制。

### 痛点放大

理解 RequestProcessor 管道的价值：

- **性能分析**：知道每个请求经过哪些 Processor，可以定位性能瓶颈
- **故障排查**：知道某个请求卡在哪个 Processor，可以快速定位问题
- **定制扩展**：知道如何插入自定义 Processor，可以实现扩展功能

---

## 2. 项目设计

### 剧本式交锋对话

**场景**：小胖想了解 ZooKeeper 内部是如何处理一次写入请求的。

**小胖**：我往 ZooKeeper 写入了一条数据，服务端内部到底是怎么处理的？

**大师**：ZooKeeper 使用**责任链模式（Chain of Responsibility）**——请求（Request）依次经过多个 Processor，每个 Processor 处理一个环节。

**Leader 上的请求处理管道**：

```
Client → NIOServerCnxn
  → PrepRequestProcessor  (1. 预处理：解析请求、校验权限、生成 Txn)
  → SyncRequestProcessor   (2. 持久化：写入事务日志)
  → ProposalRequestProcessor (3. 提案：仅 Leader，广播给 Follower)
  → CommitProcessor        (4. 提交：等待 Quorum ACK)
  → ToBeAppliedRequestProcessor (5. 待应用：Commit 后的暂存)
  → FinalRequestProcessor  (6. 最终处理：更新 DataTree、触发 Watcher、返回响应)
  → Client
```

**Follower 上的请求处理管道**（更简单）：

```
Client → NIOServerCnxn
  → PrepRequestProcessor
  → SyncRequestProcessor
  → CommitProcessor        (Follower 等待 Leader 的 COMMIT 消息)
  → FinalRequestProcessor
```

**小白**：为什么 Follower 少了 Proposal 和 ToBeApplied 两个 Processor？

**大师**：Follower 不参与 Proposal 广播——那是 Leader 的工作。Follower 上的 CommitProcessor 等待 Leader 发来的 COMMIT 消息，一旦收到，就进入 FinalRequestProcessor 提交数据。

---

## 3. 项目实战

### 环境准备

- ZooKeeper 3.9.x 源码（已编译）
- IntelliJ IDEA
- JDK 17+

### 分步实现

#### 步骤 1：追踪 RequestProcessor 链的初始化

```java
// 在 ZooKeeperServer.java 中找到 setupRequestProcessors() 方法
// 这是一个抽象方法，Leader 和 Follower 有不同的实现

// Leader 的实现：LeaderZooKeeperServer.setupRequestProcessors()
// 文件位置：org.apache.zookeeper.server.quorum.LeaderZooKeeperServer
@Override
protected void setupRequestProcessors() {
    // 1. 创建最终的 Processor
    RequestProcessor finalProcessor = new FinalRequestProcessor(this);

    // 2. 创建 ToBeAppliedProcessor
    RequestProcessor toBeAppliedProcessor =
            new ToBeAppliedRequestProcessor(this, finalProcessor);

    // 3. 创建 CommitProcessor
    commitProcessor = new CommitProcessor(toBeAppliedProcessor,
            Long.toString(getServerId()), false, getZooKeeperServerListener());
    commitProcessor.start();

    // 4. 创建 ProposalRequestProcessor（Leader 特有！）
    ProposalRequestProcessor proposalProcessor = new ProposalRequestProcessor(this, commitProcessor);
    proposalProcessor.start();

    // 5. 创建 SyncRequestProcessor
    syncProcessor = new SyncRequestProcessor(this, proposalProcessor);
    syncProcessor.start();

    // 6. 创建 PrepRequestProcessor（链的起点）
    firstProcessor = new PrepRequestProcessor(this, syncProcessor);
    ((PrepRequestProcessor) firstProcessor).start();
}
```

```java
// Follower 的实现：FollowerZooKeeperServer.setupRequestProcessors()
// 文件位置：org.apache.zookeeper.server.quorum.FollowerZooKeeperServer
@Override
protected void setupRequestProcessors() {
    // 1. 最终 Processor
    RequestProcessor finalProcessor = new FinalRequestProcessor(this);

    // 2. CommitProcessor（等待 Leader 的 COMMIT）
    commitProcessor = new CommitProcessor(finalProcessor,
            Long.toString(getServerId()), false, getZooKeeperServerListener());
    commitProcessor.start();

    // 3. SyncRequestProcessor
    syncProcessor = new SyncRequestProcessor(this, commitProcessor);
    syncProcessor.start();

    // 4. PrepRequestProcessor（链的起点）
    // 注意：没有 ProposalRequestProcessor！
    firstProcessor = new PrepRequestProcessor(this, syncProcessor);
    ((PrepRequestProcessor) firstProcessor).start();
}
```

#### 步骤 2：深入每个 Processor

**PrepRequestProcessor**——请求预处理：

```java
// 文件位置：org.apache.zookeeper.server.PrepRequestProcessor
// 作用：解析请求、校验权限、生成事务（Txn）

protected void pRequest(Request request) throws Exception {
    switch (request.type) {
        case OpCode.create: {
            // 1. 解析请求
            CreateRequest createRequest = new CreateRequest();
            ByteBufferInputStream.byteBuffer2Record(
                    request.request, createRequest);

            // 2. 路径校验
            String path = createRequest.getPath();
            validatePath(path);

            // 3. 权限检查
            checkACL(zks, request.cnxn, zks.getZKDatabase().getACL(path),
                    ZooDefs.Perms.CREATE, request.authInfo);

            // 4. 生成事务体（Txn）
            List<ACL> list = ...;
            CreateTxn createTxn = new CreateTxn(path, data, list,
                    createRequest.getEphemeral(), sequence);
            request.setTxn(createTxn);

            // 5. 传递给下一个 Processor
            nextProcessor.processRequest(request);
            break;
        }
        // ... 其他操作类型
    }
}
```

**SyncRequestProcessor**——写事务日志：

```java
// 文件位置：org.apache.zookeeper.server.SyncRequestProcessor
// 作用：将事务写入磁盘（事务日志），并触发快照

public void run() {
    while (true) {
        Request si = queuedRequests.poll();
        if (si == null) break;

        // 1. 将事务写入日志文件（fsync！）
        if (zks.getZKDatabase().append(si)) {
            // 2. 计数，判断是否触发快照
            logCount++;
            if (logCount > (snapCount / 2 + randomRoll)) {
                // 3. 写入快照
                zks.getZKDatabase().snapshot();
                logCount = 0;
            }
        }

        // 4. 传递给下一个 Processor
        nextProcessor.processRequest(si);
    }
}
```

**ProposalRequestProcessor**——广播 Proposal（Leader 特有）：

```java
// 文件位置：org.apache.zookeeper.server.quorum.ProposalRequestProcessor
// 作用：Leader 将 Proposal 广播给所有 Follower

public void processRequest(Request request) throws RequestProcessorException {
    // 所有请求都要传递给 CommitProcessor
    nextProcessor.processRequest(request);

    // 只处理写请求
    if (request.getHdr() != null) {
        // 生成 Proposal
        leader.propose(request);
        // leader.propose() 内部：
        //   1. 创建 QuorumPacket.Proposal
        //   2. 将 Proposal 放入 outstandingProposals
        //   3. 调用 sendPacket() 发送给所有 Follower
        //   4. Follower 回复 ACK → leader.processAck()
    }
}
```

**CommitProcessor**——等待 Quorum ACK：

```java
// 文件位置：org.apache.zookeeper.server.quorum.CommitProcessor
// 作用：等待 Quorum ACK，确保一致性

public void run() {
    while (true) {
        Request request = queuedRequests.poll();
        if (request == null) continue;

        // Leader 模式：等待足够多的 ACK
        // Follower 模式：等待 Leader 的 COMMIT 消息
        if (!request.isQuorum()) {
            // 非写请求直接处理
            committedRequests.add(request);
        } else {
            // 写请求：等待 committed 队列
            waitForCommit(request);
        }

        // 提交已确认的请求给下一个 Processor
        processCommitted();
    }
}
```

**FinalRequestProcessor**——最终处理：

```java
// 文件位置：org.apache.zookeeper.server.FinalRequestProcessor
// 作用：更新 DataTree、触发 Watcher、返回响应

public void processRequest(Request request) {
    // 1. 应用事务到 DataTree
    if (request.getHdr().getType() == OpCode.create) {
        CreateTxn createTxn = (CreateTxn) request.getTxn();
        zks.getZKDatabase().createNode(createTxn.getPath(),
                createTxn.getData(), ...);
    } else if (request.getHdr().getType() == OpCode.setData) {
        SetDataTxn setDataTxn = (SetDataTxn) request.getTxn();
        // 更新 DataTree
        zks.getZKDatabase().setData(setDataTxn.getPath(),
                setDataTxn.getData(), ...);
    }

    // 2. 触发 Watcher
    zks.getZKDatabase().dataTree().triggerWatches();

    // 3. 发送响应给客户端
    // 序列化响应并写入网络连接
    cnxn.sendResponse(hdr, rsp, "response");
}
```

#### 步骤 3：添加调试日志追踪完整链路

创建 `DebugRequestProcessor.java`：

```java
package com.zkdemo.source;

import org.apache.zookeeper.server.*;
import org.apache.zookeeper.server.quorum.*;

/**
 * 调试版 RequestProcessor——在链中打印每个请求的路径
 */
public class DebugRequestProcessor extends PrepRequestProcessor {

    public DebugRequestProcessor(ZooKeeperServer zks,
                                  RequestProcessor nextProcessor) {
        super(zks, nextProcessor);
    }

    @Override
    protected void pRequest(Request request) throws RequestProcessorException {
        String path = "unknown";
        try {
            if (request.request != null) {
                // 尝试解析路径
                java.nio.ByteBuffer bb = request.request;
                // ... 解析逻辑
            }
        } catch (Exception ignored) {}

        System.out.println("[DebugProcessor] 处理请求: type="
                + request.type + ", path=" + path
                + ", zxid=" + Long.toHexString(request.getHdr() != null
                        ? request.getHdr().getZxid() : 0));

        // 继续处理器链
        super.pRequest(request);
    }
}
```

#### 步骤 4：在源码中插入日志追踪

在 `FinalRequestProcessor.processRequest()` 中插入日志：

```java
// 文件位置：org.apache.zookeeper.server.FinalRequestProcessor
// 在 processRequest 方法开始处添加：

public void processRequest(Request request) {
    // 添加调试日志
    if (request.getHdr() != null) {
        System.out.println("[FinalProcessor] " + new java.util.Date()
                + " | zxid=" + Long.toHexString(request.getHdr().getZxid())
                + " | type=" + request.getHdr().getType()
                + " | cxid=" + request.getCxid()
                + " | path=" + (request.path != null ? request.path : "N/A"));
    }

    // 原有逻辑...
    if (request.getHdr().getType() == OpCode.setData) {
        long startTime = System.nanoTime();
        // ... 原有 setData 逻辑 ...
        long elapsed = System.nanoTime() - startTime;
        System.out.println("[FinalProcessor] setData 耗时: "
                + (elapsed / 1000) + " μs");
    }

    // 原有的 processRequest 逻辑
    // ...
}
```

编译并重新运行：

```bash
# 重新编译
mvn compile -pl zookeeper-server -DskipTests

# 启动 ZooKeeper（在 IDE 中运行 Debug 配置）

# 执行写入操作
./bin/zkCli.sh -server 127.0.0.1:2181
create /processor-test "hello"

# 查看控制台输出的调试日志
# [FinalProcessor] 2025-03-14 10:00:00 | zxid=0x100000001 | type=1 | cxid=1 | path=/processor-test
# [FinalProcessor] setData 耗时: 12 μs
```

### 测试验证

```bash
# 验证 Processor 链完整
# 1. 在 FinalRequestProcessor 设置断点
# 2. 执行 setData
# 3. 观察调用栈，确认经过了所有 Processor

# 调用栈示例：
# FinalRequestProcessor.processRequest()
#   ← ToBeAppliedRequestProcessor.processRequest()
#     ← CommitProcessor.processRequest()
#       ← ProposalRequestProcessor.processRequest()
#         ← SyncRequestProcessor.processRequest()
#           ← PrepRequestProcessor.processRequest()
#             ← ZooKeeperServer.processRequest()
#               ← NIOServerCnxn.readRequest()
```

### 可能遇到的坑

| 问题 | 原因 | 解决 |
|------|------|------|
| Processor 链断掉 | 某个 Processor 中没有调用 nextProcessor | 检查自定义 Processor 的 processRequest 实现 |
| 请求被重复处理 | 同一个请求被放入多个队列 | 检查 Processor 的请求传递逻辑 |

---

## 4. 项目总结

### Processor 链对比

| Processor | Leader | Follower | 作用 |
|-----------|--------|---------|------|
| PrepRequestProcessor | ✓ | ✓ | 预处理、权限校验、生成 Txn |
| SyncRequestProcessor | ✓ | ✓ | 写事务日志、触发快照 |
| ProposalRequestProcessor | ✓ | ✗ | 广播 Proposal 给 Follower |
| CommitProcessor | ✓ | ✓ | 等待 Quorum ACK（Leader）或 Leader COMMIT（Follower） |
| ToBeAppliedRequestProcessor | ✓ | ✗ | 暂存提交待处理 |
| FinalRequestProcessor | ✓ | ✓ | 更新 DataTree、触发 Watcher、返回响应 |

### 关键发现

- **同步点**在 `SyncRequestProcessor`：每次写入都 fsync，这是写入性能瓶颈
- **一致性点**在 `CommitProcessor`：Leader 需要等 Quorum ACK，Follower 需要等 COMMIT
- **扩展点**在 `PrepRequestProcessor`：可以在这里插入自定义 Processor

### 思考题

1. `PrepRequestProcessor` 在生成 Txn 时，需要为写操作分配 zxid。这个 zxid 的分配机制在源码中是如何实现的？Leader 宕机后新 Leader 如何保证 zxid 不重复？
2. `SyncRequestProcessor` 中有一个 `queuedRequests` 队列，写请求先进入这个队列，然后由后台线程逐个处理。如果队列过长（写入量超过磁盘 fsync 能力），会发生什么？`outstanding_requests` 监控指标反映的就是这个队列的长度吗？

### 推广计划提示

- **开发**：理解 Processor 链有助于排查 ZooKeeper 请求处理延迟问题
- **运维**：`outstanding_requests` 指标反映的是 CommitProcessor 中的等待队列长度
- **贡献者**：自定义 Processor 是扩展 ZooKeeper 功能的主要方式（第 39 章详述）
