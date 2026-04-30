# 第38章：Watcher 机制源码——事件触发与通知全链路

## 1. 项目背景

### 业务场景

Watcher 是 ZooKeeper 最核心的特性之一。第 5 章从使用层面学习了 Watcher，第 33 章粗略提到了 Watcher 在 FinalRequestProcessor 中触发。本章从源码层面深入 Watcher 的完整链路——**从服务端数据变化，到触发 Watcher，到序列化通知，到客户端收到回调**。

### 源码定位

```
WatchManager.java       ← 服务端 Watcher 管理
ServerCnxn.java         ← 连接抽象（Watcher 通知发送）
ZooKeeper.java          ← 客户端 Watcher 注册
ClientCnxn.java         ← 客户端 Watcher 回调
EventThread.java        ← 客户端事件线程
```

---

## 2. 项目设计

### 剧本式交锋对话

**场景**：小白想理解 Watcher 的完整生命周期。

**小白**：我从客户端调用 `getData("/config", watcher)`，服务端内部到底是怎么注册 Watcher 的？数据变化后又是怎么推送到客户端的？

**大师**：Watcher 的完整链路分三阶段：

```
阶段一：注册 Watcher（客户端 → 服务端）

客户端: ZooKeeper.getData("/config", watcher)
  → ClientCnxn.submitRequest()           ← 发送请求到服务端
  → SendThread.sendRequest()              ← 通过 TCP 发送
  → 服务端 NIOServerCnxn.doIO()          ← 收到请求
  → ZooKeeperServer.processRequest()      ← 开始处理
  → FinalRequestProcessor.processRequest() ← 最终处理
  → DataTree.getData("/config", watcher, stat) ← 读取数据
  → WatchManager.addWatch("/config", watcher)  ← 注册 Watcher!
  → NIOServerCnxn.sendResponse()          ← 返回数据 + 确认

阶段二：触发 Watcher（服务端）

DataTree.setData("/config", newData)
  → WatchManager.triggerWatch("/config", NodeDataChanged)
    → 从 watchTable 查找该路径的所有 Watcher
    → 从 watchTable 移除（一次性语义）
    → 对每个 Watcher:
      → ServerCnxn.process(WatchedEvent)
      → 序列化 WatchedEvent
      → 写入 TCP 连接

阶段三：处理回调（服务端 → 客户端）

客户端 SendThread.readResponse()
  → 反序列化 WatchedEvent
  → 放入 EventThread 队列
  → EventThread 处理:
    → Watcher.process(event)
    → 执行用户自定义回调
```

---

## 3. 项目实战

### 环境准备

- ZooKeeper 3.9.x 源码
- IntelliJ IDEA
- JDK 17+

### 分步实现

#### 步骤 1：WatchManager——服务端 Watcher 管理

```java
// 文件位置：org.apache.zookeeper.server.WatchManager
// 管理所有 Watcher 的注册和触发

public class WatchManager {
    private static final Logger LOG = LoggerFactory.getLogger(WatchManager.class);

    // 核心数据结构：
    // 1. watchTable: path → Watcher 列表
    //    路径到 Watcher 的映射（用于根据路径查找 Watcher）
    private final HashMap<String, HashSet<Watcher>> watchTable =
            new HashMap<>();

    // 2. watch2Paths: Watcher → path 列表
    //    Watcher 到路径的映射（用于 Watcher 被关闭时快速清理）
    private final HashMap<Watcher, HashSet<String>> watch2Paths =
            new HashMap<>();

    // 添加 Watcher
    public synchronized boolean addWatch(String path, Watcher watcher) {
        // 放入 watchTable（path → watcher）
        HashSet<Watcher> list = watchTable.computeIfAbsent(path, k -> new HashSet<>());
        list.add(watcher);

        // 放入 watch2Paths（watcher → path）
        HashSet<String> paths = watch2Paths.computeIfAbsent(watcher, k -> new HashSet<>());
        paths.add(path);

        return true;
    }

    // 触发 Watcher
    public Set<Watcher> triggerWatch(String path, EventType type) {
        return triggerWatch(path, type, null);
    }

    public synchronized Set<Watcher> triggerWatch(String path,
                                                   EventType type,
                                                   Set<Watcher> supress) {
        // 1. 从 watchTable 中取出并移除该路径的所有 Watcher
        //    关键：一次性语义——取出后立即从 watchTable 移除
        WatchedEvent e = new WatchedEvent(type, KeeperState.SyncConnected, path);
        HashSet<Watcher> watchers = watchTable.remove(path);

        if (watchers == null || watchers.isEmpty()) {
            LOG.debug("No watchers for path: {}", path);
            return null;
        }

        // 2. 从 watch2Paths 中移除
        for (Watcher w : watchers) {
            HashSet<String> paths = watch2Paths.get(w);
            if (paths != null) {
                paths.remove(path);
            }
        }

        // 3. 逐个通知
        for (Watcher w : watchers) {
            if (supress != null && supress.contains(w)) {
                continue;
            }
            // 发送 WatchedEvent 给客户端
            w.process(e);
        }

        return watchers;
    }

    // 移除与某个连接相关的所有 Watcher（连接关闭时调用）
    public synchronized void removeWatcher(Watcher watcher) {
        // 从 watch2Paths 中找到该 Watcher 的所有路径
        HashSet<String> paths = watch2Paths.remove(watcher);
        if (paths == null) return;

        // 从 watchTable 中逐个移除
        for (String path : paths) {
            HashSet<Watcher> list = watchTable.get(path);
            if (list != null) {
                list.remove(watcher);
                if (list.isEmpty()) {
                    watchTable.remove(path);
                }
            }
        }
    }
}
```

#### 步骤 2：Watcher 在 FinalRequestProcessor 中的触发

```java
// 文件位置：org.apache.zookeeper.server.FinalRequestProcessor
// 在 processRequest 中触发 Watcher

public void processRequest(Request request) {
    // ... 处理各种请求类型 ...

    switch (request.type) {
        case OpCode.create:
            // 创建节点后触发 NodeCreated + NodeChildrenChanged
            break;

        case OpCode.setData:
            // 1. 更新 DataTree
            Stat stat = zks.getZKDatabase().setData(
                    request.path, request.data, ...);

            // 2. 触发 Watcher（在 DataTree.setData 内部调用）
            // DataTree.setData() 内部：
            // dataWatches.triggerWatch(path, EventType.NodeDataChanged);
            // childWatches.triggerWatch(parentPath, EventType.NodeChildrenChanged);
            break;

        case OpCode.delete:
            // 删除节点后触发 NodeDeleted + NodeChildrenChanged
            break;
    }
}
```

#### 步骤 3：ServerCnxn——服务端发送通知给客户端

```java
// 文件位置：org.apache.zookeeper.server.ServerCnxn
// 服务端到客户端的连接抽象

public abstract class ServerCnxn implements Watcher, Stats {

    // Watcher 接口的 process 方法
    // 当 Watcher 被触发时，这个方法被调用
    @Override
    public synchronized void process(WatchedEvent event) {
        // 设置响应头（xid = -1 表示通知）
        ReplyHeader h = new ReplyHeader(
                ClientCnxn.NOTIFICATION_XID,  // xid = -1（通知）
                event.getZxid(),               // zxid
                0);                            // err

        // 序列化 WatchedEvent
        WatcherEvent e = event.getWrapper();

        // 发送给客户端
        try {
            sendResponse(h, e, "notification");
        } catch (IOException e) {
            LOG.error("Failed to send watch notification", e);
        }
    }
}

// NIOServerCnxn 的实现
// NIO 网络层的连接处理
public class NIOServerCnxn extends ServerCnxn {
    // sendResponse 将响应写入 TCP 缓冲区
    @Override
    public void sendResponse(ReplyHeader h, Record r, String tag) {
        // 序列化
        ByteArrayOutputStream baos = new ByteArrayOutputStream();
        BinaryOutputArchive boa = BinaryOutputArchive.getArchive(baos);
        h.serialize(boa, "header");
        r.serialize(boa, "response");

        // 写入发送缓冲区
        outgoingBuffers.add(ByteBuffer.wrap(baos.toByteArray()));

        // 唤醒 Selector 处理发送
        requestInterestOpsUpdate();
    }
}
```

#### 步骤 4：客户端处理 Watcher 通知

```java
// 文件位置：org.apache.zookeeper.ClientCnxn
// 客户端的 SendThread 处理服务端响应

public class ClientCnxn {

    class SendThread extends ZooKeeperThread {
        @Override
        public void run() {
            while (running) {
                // ... 读取服务端响应 ...

                // 读取响应头
                ReplyHeader replyHdr = ...;

                if (replyHdr.getXid() == NOTIFICATION_XID) {
                    // 这是一个 Watcher 通知！
                    // 反序列化 WatcherEvent
                    WatcherEvent event = new WatcherEvent();
                    event.deserialize(bbia, "event");

                    // 转换成 WatchedEvent
                    WatchedEvent we = new WatchedEvent(
                            event.getType(),
                            event.getState(),
                            event.getPath());

                    // 放入 EventThread 的待处理队列
                    events.add(we);
                } else {
                    // 普通请求的响应
                    pendingQueue.removeFirst();
                }
            }
        }
    }

    // EventThread——在单线程中顺序执行 Watcher 回调
    class EventThread extends ZooKeeperThread {
        private final LinkedBlockingQueue<Object> waitingEvents =
                new LinkedBlockingQueue<>();

        @Override
        public void run() {
            while (true) {
                Object event = waitingEvents.take();

                if (event instanceof WatchedEvent) {
                    // 处理 Watcher 通知
                    WatchedEvent we = (WatchedEvent) event;

                    // 调用用户注册的 Watcher
                    for (Watcher w : watchers) {
                        try {
                            w.process(we);
                        } catch (Exception e) {
                            LOG.error("Watcher process error", e);
                        }
                    }
                }
            }
        }
    }
}
```

#### 步骤 5：添加调试日志追踪 Watcher 全链路

创建 Watcher 调试代码（在源码中插入）：

```java
// 在 WatchManager.java 的 addWatch 和 triggerWatch 中添加：

// addWatch 中：
public synchronized boolean addWatch(String path, Watcher watcher) {
    System.out.println("[WatchManager] 注册 Watcher: path=" + path
            + ", watcher=" + watcher.getClass().getName());

    // ... 原有逻辑 ...
}

// triggerWatch 中：
public synchronized Set<Watcher> triggerWatch(String path, EventType type, Set<Watcher> supress) {
    System.out.println("[WatchManager] 触发 Watcher: path=" + path
            + ", type=" + type
            + ", 当前路径 Watcher 数=" + (watchTable.get(path) != null ?
                    watchTable.get(path).size() : 0));

    // ... 原有逻辑 ...

    for (Watcher w : watchers) {
        System.out.println("[WatchManager] 通知: " + w.getClass().getName());
        w.process(e);
    }
}
```

编译运行测试：

```bash
# 1. 编译
mvn compile -pl zookeeper-server -DskipTests

# 2. 在 IDE 中启动 ZooKeeper

# 3. 注册 Watcher
./bin/zkCli.sh -server 127.0.0.1:2181
get /config/watcher-test true

# 4. 触发 Watcher
set /config/watcher-test "new-value"

# 5. 观察控制台输出
# [WatchManager] 触发 Watcher: path=/config/watcher-test, type=NodeDataChanged
# [WatchManager] 通知: org.apache.zookeeper.server.NIOServerCnxn@...
# [WatchManager] 一次性语义：路径 /config/watcher-test 的 Watcher 已移除
```

#### 步骤 6：Watcher 性能测试

创建 `WatcherBenchmark.java`：

```java
package com.zkdemo.source;

/**
 * Watcher 性能基准测试
 * 测试大量 Watcher 注册和触发的性能
 */
public class WatcherBenchmark {
    public static void main(String[] args) {
        System.out.println("=== Watcher 性能基准测试 ===\n");

        // 测试场景 1：大量 Watcher 注册
        int watchCount = 100000;
        long start = System.nanoTime();

        // 模拟 WatchManager 的操作
        WatchManagerSimulator wm = new WatchManagerSimulator();
        for (int i = 0; i < watchCount; i++) {
            wm.addWatch("/path-" + i, new DummyWatcher());
        }
        long elapsed = System.nanoTime() - start;
        System.out.printf("注册 %d 个 Watcher: %.2f ms (%.0f ops/s)%n",
                watchCount, elapsed / 1_000_000.0,
                watchCount * 1_000_000_000.0 / elapsed);

        // 测试场景 2：触发 Watcher
        start = System.nanoTime();
        wm.triggerWatch("/path-0", "NodeDataChanged");
        elapsed = System.nanoTime() - start;
        System.out.printf("触发 %d 个 Watcher: %.2f ms (%.0f ops/s)%n",
                watchCount, elapsed / 1_000_000.0,
                watchCount * 1_000_000_000.0 / elapsed);
    }

    static class WatchManagerSimulator {
        java.util.Map<String, java.util.Set<Object>> watchTable = new java.util.HashMap<>();
        java.util.Map<Object, java.util.Set<String>> watch2Paths = new java.util.HashMap<>();

        synchronized void addWatch(String path, Object watcher) {
            watchTable.computeIfAbsent(path, k -> new java.util.HashSet<>()).add(watcher);
            watch2Paths.computeIfAbsent(watcher, k -> new java.util.HashSet<>()).add(path);
        }

        synchronized void triggerWatch(String path, String type) {
            java.util.Set<Object> watchers = watchTable.remove(path);
            if (watchers != null) {
                for (Object w : watchers) {
                    java.util.Set<String> paths = watch2Paths.get(w);
                    if (paths != null) paths.remove(path);
                }
            }
        }
    }

    static class DummyWatcher {}
}
```

### 测试验证

```bash
# 验证 Watcher 一次性语义
# 1. 注册 Watcher
# 2. 触发 Watcher（收到通知）
# 3. 再次触发（不注册新的 Watcher，不会收到通知）
```

### 可能遇到的坑

| 问题 | 原因 | 解决 |
|------|------|------|
| Watcher 重复触发 | 同一路径注册了多个 Watcher | 检查 watchTable 中该路径的 Watcher 数量 |
| Watcher 丢失 | 一次性语义导致 | 在回调中重新注册 |
| Watcher 泄漏 | 连接关闭后 Watcher 未清理 | WatchManager.removeWatcher 在连接关闭时调用 |

---

## 4. 项目总结

### Watcher 全链路图

```
客户端                         服务端
  │                              │
  │  getData("/path", watcher)   │
  │─────────────────────────────►│
  │                              ├─ FinalRequestProcessor
  │                              ├─ DataTree.getData()
  │                              ├─ WatchManager.addWatch()
  │                              │   watchTable["/path"] += watcher
  │                              │   watch2Paths[watcher] += "/path"
  │◄─────────────────────────────│ 返回数据
  │                              │
  │      ... 数据变化 ...        │
  │                              │
  │                              ├─ DataTree.setData()
  │                              ├─ WatchManager.triggerWatch()
  │                              │   watchers = watchTable.remove("/path")
  │                              │   for w in watchers:
  │                              │     w.process(WatchedEvent)
  │                              │     ServerCnxn 发送通知
  │◄─────────────────────────────│ Watcher 通知 (xid=-1)
  │                              │
  ├─ ClientCnxn.SendThread
  │  → 识别 xid=-1
  │  → 放入 EventThread 队列
  │
  ├─ EventThread
  │  → watcher.process(event)
  │  → onNodeDataChanged()
```

### 关键设计

| 设计 | 说明 | 原因 |
|------|------|------|
| 一次性语义 | 触发后从 watchTable 移除 | 防止重复通知 |
| watchTable + watch2Paths | 双向索引 | 快速查找 + 快速清理 |
| xid=-1 | 通知的特殊标识 | 区分普通响应和通知 |
| EventThread 单线程 | 顺序执行 Watcher 回调 | 避免并发竞争 |

### 思考题

1. `WatchManager.triggerWatch()` 从 `watchTable` 中移除路径后立即通知。如果在通知过程中有新的客户端注册了同一路径的 Watcher，这个新 Watcher 会被触发吗？为什么？
2. Watcher 通知的 `xid=-1` 是客户端识别通知的唯一标识。如果客户端同时收到了一个普通响应（xid=1）和一个 Watcher 通知（xid=-1），它们的处理顺序是怎样的？是先处理普通响应还是先处理 Watcher 通知？

### 推广计划提示

- **开发**：理解 Watcher 的一次性语义和 EventThread 的单线程模型，有助于写出正确的 Watcher 回调代码
- **运维**：通过 `wchs`、`wchc` 命令监控 Watcher 数量，异常增长可能是 Watcher 泄漏
- **贡献者**：WatchManager 可以扩展为支持"持久 Watcher"（不需要每次重新注册）
