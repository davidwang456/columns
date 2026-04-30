# 第5章：Watcher 机制入门——事件驱动的奥秘

## 1. 项目背景

### 业务场景

你在第 3 章中已经通过 zkCli.sh 手动操作了 ZooKeeper 的数据——创建节点、修改数据、查询数据。但如果数据发生变化，客户端如何**自动感知**？

想象一个微服务架构中的配置管理场景：

- 数据库连接字符串存储在 ZooKeeper 的 `/config/db-url` 节点中
- 10 个微服务实例都引用了这个配置
- 运维人员把数据库地址从旧主库切换到新主库，修改了 `/config/db-url`
- **我们希望所有 10 个实例能在几秒内自动感知配置变化，刷新数据库连接池**

如果没有自动通知机制，每个客户端必须**轮询**——每隔几秒就去 `get /config/db-url` 一次，看看数据有没有变。这种方案叫 Pull 模式，缺点很明显：轮询间隔太短浪费资源，间隔太长响应不及时。

ZooKeeper 的 **Watcher 机制** 就是解决这个问题的：它提供了一种**被动通知（Push）模式**——数据变化时，ZooKeeper 主动推送通知给注册了 Watcher 的客户端。

### 痛点放大

没有 Watcher 机制，实现配置动态刷新：

```java
// 方案一：轮询
while (true) {
    byte[] data = zk.getData("/config/db-url", false, null);
    if (!Arrays.equals(data, cachedData)) {
        // 刷新连接池
        refreshConnectionPool(new String(data));
        cachedData = data;
    }
    Thread.sleep(5000); // 5秒轮询一次
}
```

这个方案的问题：
- 5 秒轮询一次，配置变更后最多延迟 5 秒才能感知
- 改成 1 秒轮询一次，ZooKeeper 服务端压力增大 5 倍
- 每个服务实例都轮询，集群规模大时 ZooKeeper 成为瓶颈
- 如果实例数上百，轮询请求每秒数百次，全是无意义的"没变化"响应

Watcher 机制的核心就是：**一次注册，主动通知，零无效请求**。

---

## 2. 项目设计

### 剧本式交锋对话

**场景**：小胖正在写一个配置刷新程序，使用轮询方式检查配置变化。小白觉得效率太低了。

**小胖**：我用了个线程池，每个配置项每隔 3 秒就 `getData` 一次看有没有变化，这样保证最多延迟 3 秒。还行吧？

**大师**：三秒 10 个实例就是每秒 3.3 次请求，100 个就是每秒 33 次。如果配置项再多几个，ZooKeeper 就要被你打满了。

**小白**：我听说 ZooKeeper 有 Watcher 机制，可以注册监听，数据变了自己通知。

**大师**：对，Watcher 是 ZooKeeper 最核心的设计之一。它像一个**门铃**——你按一次门铃（注册 Watcher），有人在房间里按开门按钮（数据变化），门铃响了通知你。但注意，是一次性的——按完门铃后，你需要重新按一次才能再次被通知。

**小胖**：一次性？那我每次收到通知后还得重新注册，这不麻烦吗？

**大师**：确实需要在回调中重新注册，但这比轮询优雅得多。给你看一个用 `getData` 注册 Watcher 的例子：

```bash
# 窗口 A：注册 Watcher
get /config/db-url true    # true 表示注册 Watcher

# 窗口 B：修改数据（触发 Watcher）
set /config/db-url "jdbc:mysql://new-db:3306/mydb"

# 窗口 A 自动收到通知：
# WATCHER::
# WatchedEvent state:SyncConnected type:NodeDataChanged path:/config/db-url
```

**小白**：那 Watcher 的触发条件有哪几种？我只知道数据变化会触发。

**大师**：Watcher 触发事件有 4 种类型：

| 事件类型 | 触发条件 | 注册方式 |
|---------|---------|---------|
| `NodeCreated` | 节点被创建 | `exists` |
| `NodeDeleted` | 节点被删除 | `exists` / `getData` / `getChildren` |
| `NodeDataChanged` | 节点数据改变 | `exists` / `getData` |
| `NodeChildrenChanged` | 子节点增减 | `getChildren` |

注意：`getData` 只能 watch 数据变化，`exists` 可以 watch 节点创建/删除/数据变化，`getChildren` 可以 watch 子节点变化。

**小白**：那如果一个节点被删除了，再重新创建，会触发哪些事件？

**大师**：这涉及到一个重要的细节——Watcher 是在服务端**先触发**还是**先改数据**？

ZooKeeper 的语义是：**先修改数据，再触发 Watcher，再返回给客户端**。也就是说，当你收到 `NodeDataChanged` 通知时，数据已经被修改了，用 `getData` 拿到的是新数据。但注意——服务端不能保证所有 Watcher 通知到达客户端时数据已经是新版本。所以正确的做法是：收到通知后主动调用一次 `getData` 获取最新数据。

> **技术映射**：Watcher = 门铃，一次性触发 = 按一次响一次，重新注册 = 再次按门铃

**小胖**：那如果我注册了很多 Watcher，ZooKeeper 服务端会不会撑不住？

**大师**：这个问题问得好。Watcher 是存储在服务端内存中的。假设一个 3 节点集群，每个节点有 10 万个 Watcher 条目（每个条目约 100 字节），就是 10MB 左右，压力不大。

但要注意**Watcher 风暴**——如果一个节点的数据变化触发了成千上万个 Watcher，可能会导致瞬间的**惊群效应**。比如一个服务注册父节点 `/services` 上有 2000 个 Watcher（每个实例都 watch 了 `/services` 的子节点变化），当新服务注册时，2000 个客户端同时收到通知，同时发起新的 `getChildren` 请求。这就是为什么生产建议：**不要让太多客户端 watch 同一个节点**。

> **技术映射**：Watcher 风暴 = 一寝室的人听到门铃同时去开门

---

## 3. 项目实战

### 环境准备

- ZooKeeper 3.9.x 集群
- Java 11+
- Maven

### 分步实现

#### 步骤 1：编写配置自动刷新程序

创建 `ConfigWatcherDemo.java`：

```java
package com.zkdemo;

import org.apache.zookeeper.*;
import org.apache.zookeeper.data.Stat;

import java.util.concurrent.CountDownLatch;

public class ConfigWatcherDemo {
    private static final String ZK_URL = "127.0.0.1:2181";
    private static final String CONFIG_PATH = "/config/db-url";
    private static String cachedDbUrl = null;
    private static final CountDownLatch connectedSignal = new CountDownLatch(1);

    public static void main(String[] args) throws Exception {
        // 创建客户端连接
        ZooKeeper zk = new ZooKeeper(ZK_URL, 5000, event -> {
            if (event.getState() == Watcher.Event.KeeperState.SyncConnected) {
                connectedSignal.countDown();
            }
        });
        connectedSignal.await();

        // 初始化配置
        initConfig(zk);

        // 注册 Watcher 并读取配置
        watchAndReadConfig(zk);

        // 模拟业务运行
        System.out.println("Config watcher is running. Will auto-refresh on config change...");
        Thread.sleep(Long.MAX_VALUE);
    }

    private static void initConfig(ZooKeeper zk) throws Exception {
        Stat stat = zk.exists(CONFIG_PATH, false);
        if (stat == null) {
            // 创建父节点
            zk.create("/config", "".getBytes(),
                    ZooDefs.Ids.OPEN_ACL_UNSAFE, CreateMode.PERSISTENT);
            // 创建配置节点
            zk.create(CONFIG_PATH, "jdbc:mysql://old-db:3306/mydb".getBytes(),
                    ZooDefs.Ids.OPEN_ACL_UNSAFE, CreateMode.PERSISTENT);
            cachedDbUrl = "jdbc:mysql://old-db:3306/mydb";
            System.out.println("初始化配置: " + cachedDbUrl);
        } else {
            byte[] data = zk.getData(CONFIG_PATH, false, null);
            cachedDbUrl = new String(data);
            System.out.println("当前配置: " + cachedDbUrl);
        }
    }

    private static void watchAndReadConfig(ZooKeeper zk) throws Exception {
        // 注册 Watcher（第三个参数为 true 表示使用默认 Watcher）
        byte[] data = zk.getData(CONFIG_PATH, new Watcher() {
            @Override
            public void process(WatchedEvent event) {
                System.out.println("\n========== Watcher 触发 ==========");
                System.out.println("事件类型: " + event.getType());
                System.out.println("事件路径: " + event.getPath());
                System.out.println("连接状态: " + event.getState());

                if (event.getType() == Event.EventType.NodeDataChanged
                        || event.getType() == Event.EventType.NodeCreated) {
                    try {
                        // Watcher 是一次性的，重新注册
                        // 重新读取配置
                        Stat stat = new Stat();
                        byte[] newData = zk.getData(CONFIG_PATH, this, stat);
                        String newDbUrl = new String(newData);

                        // 对比配置是否有变化
                        if (!newDbUrl.equals(cachedDbUrl)) {
                            System.out.println("配置已变更:");
                            System.out.println("  旧值: " + cachedDbUrl);
                            System.out.println("  新值: " + newDbUrl);
                            cachedDbUrl = newDbUrl;
                            // 模拟刷新数据库连接池
                            refreshConnectionPool(newDbUrl);
                        } else {
                            System.out.println("配置未变化（可能是重复通知）");
                        }

                        System.out.println("Watcher 已重新注册，等待下次变更...\n");
                    } catch (Exception e) {
                        e.printStackTrace();
                    }
                } else if (event.getType() == Event.EventType.NodeDeleted) {
                    System.out.println("警告: 配置节点已被删除！");
                    // 可以采取默认配置或告警
                }
            }
        }, null);

        cachedDbUrl = new String(data);
        System.out.println("Watcher 已注册，当前配置: " + cachedDbUrl);
    }

    private static void refreshConnectionPool(String newDbUrl) {
        System.out.println(">>> 正在刷新数据库连接池...");
        System.out.println(">>> 新连接池已建立: " + newDbUrl);
        // 实际项目：关闭旧连接池，创建新连接池
    }
}
```

#### 步骤 2：运行并测试

**测试准备**：

```bash
# 1. 启动 ZooKeeper
./bin/zkServer.sh start

# 2. 编译并运行 WatcherDemo
mvn compile
mvn exec:java -Dexec.mainClass="com.zkdemo.ConfigWatcherDemo"

# 输出：
# 初始化配置: jdbc:mysql://old-db:3306/mydb
# Watcher 已注册，当前配置: jdbc:mysql://old-db:3306/mydb
# Config watcher is running. Will auto-refresh on config change...
```

**测试 Watcher 触发**：

```bash
# 打开新终端，连接 ZooKeeper
./bin/zkCli.sh -server 127.0.0.1:2181

# 修改配置
set /config/db-url "jdbc:mysql://new-db:3306/mydb"
```

然后在 Java 程序终端中，你会看到：

```
========== Watcher 触发 ==========
事件类型: NodeDataChanged
事件路径: /config/db-url
连接状态: SyncConnected
配置已变更:
  旧值: jdbc:mysql://old-db:3306/mydb
  新值: jdbc:mysql://new-db:3306/mydb
>>> 正在刷新数据库连接池...
>>> 新连接池已建立: jdbc:mysql://new-db:3306/mydb
Watcher 已重新注册，等待下次变更...
```

#### 步骤 3：验证 Watcher 一次性语义

在 zkCli.sh 中多次修改配置，观察每次都会触发 Watcher：

```bash
# 第二次修改（应该在第一次重新注册后）
set /config/db-url "jdbc:mysql://another-db:3306/mydb"
```

确认每次修改都会触发通知。

#### 步骤 4：验证 NodeDeleted 事件

```bash
# 删除节点
delete /config/db-url
```

程序输出：
```
========== Watcher 触发 ==========
事件类型: NodeDeleted
事件路径: /config/db-url
连接状态: SyncConnected
警告: 配置节点已被删除！
```

### 可能遇到的坑

| 问题 | 原因 | 解决 |
|------|------|------|
| Watcher 只触发一次 | Watcher 是一次性的 | 在回调中重新注册 Watcher |
| 收到通知后读到的数据不是最新 | 网络延迟 | 收到通知后调用 `getData` 读取最新值 |
| Watcher 未触发 | 注册时节点不存在 | 使用 `exists` 而不是 `getData` 注册 |
| Watcher 被多次触发 | 在多个路径上注册了 Watcher | 确认注册路径是否重复 |

### 完整代码清单

代码见 `column/code/chapter05/`。

### 测试验证

编写一个自动测试脚本（zkCli.sh 命令序列）：

```bash
#!/bin/bash
# test-watcher.sh

# 创建测试配置
./bin/zkCli.sh -server 127.0.0.1:2181 create /config ""
./bin/zkCli.sh -server 127.0.0.1:2181 create /config/db-url "initial"

# 在命令行注册 Watcher
# 注意：命令行的 watcher 注册方式
./bin/zkCli.sh -server 127.0.0.1:2181 get /config/db-url true

# 修改配置
./bin/zkCli.sh -server 127.0.0.1:2181 set /config/db-url "updated"

# 应看到 Watcher 被触发
```

---

## 4. 项目总结

### 优点 & 缺点

| 维度 | 优点 | 缺点 |
|------|------|------|
| 实时性 | 变更后立即通知（毫秒级） | 网络分区时通知可能延迟 |
| 资源消耗 | 无轮询，零无效请求 | 服务端内存随 Watcher 数量线性增长 |
| 易用性 | API 简单，`true` 参数即可注册 | 一次性语义导致需手动重新注册 |
| 可靠性 | 保证"触发后数据已变更"的语义 | 不保证通知的投递顺序 |

### 适用场景

- **配置中心**：配置项变化时实时推送到所有订阅者
- **服务发现**：服务上下线时通知其他服务
- **分布式锁**：锁释放时通知等待者
- **Leader 选举**：主节点下线时触发重新选举
- **命名服务**：变更时通知客户端更新本地缓存

**不适用场景**：
- 需要永久监听（Watcher 是一次性的，重新注册有时窗）
- 大量客户端监听同一个节点的子节点变化（Watcher 风暴）

### 注意事项

- Watcher 是一次性的，必须在回调中重新注册
- 在 `process()` 回调中不要做耗时操作，否则阻塞 EventThread
- 如果连接已断开，Watcher 不会被触发，需要在重连后重新注册
- 不要在多个线程中注册同一个路径的 Watcher（可能只有最后一个生效）

### 常见踩坑经验

**故障 1：Watcher 回调中直接读数据读到旧值**

现象：Watcher 触发后，在回调中调用 `getData`，拿到的是旧数据。

根因：Watcher 的回调 `process()` 在 EventThread 中执行。此时客户端可能还没有处理完服务端发来的数据变更响应。正确的做法是在回调中调用 `getData`（服务端保证 Watcher 触发时数据已提交），但如果用异步方式读取，要注意时序。

解决方案：在 `process()` 中同步调用 `getData`（使用 `this` 作为 Watcher 参数），确保读到最新数据。

**故障 2：Watcher 泄漏导致内存泄漏**

现象：应用运行一段时间后，ZooKeeper 服务端 Watcher 数量不断增长，最终 OOM。

根因：客户端每次注册 Watcher 都创建新的 Watcher 对象，但这些对象的引用未被释放。当连接断开又重连时，旧的 Watcher 不会被自动清理。

解决方案：使用单例 Watcher 模式，或者在 `close()` 时清理所有 Watcher 引用。

**故障 3：重连后 Watcher 丢失**

现象：客户端断线重连后，之前注册的 Watcher 不再触发。

根因：ZooKeeper 客户端在断线期间 Watcher 处于未注册状态。重连后所有 Watcher 需要重新注册。Curator 框架通过 `ConnectionStateListener` 处理了这个问题。

### 思考题

1. Watcher 是一次性的，这意味着在重新注册的间隙，如果数据发生了变化，客户端会丢失这次变更通知。如何设计一个可靠的配置监听器来避免这个问题？
2. 假设 1000 个客户端同时 watch 了 `/services` 的子节点变化。当一个新服务注册进来，这 1000 个 Watcher 会同时触发。如何设计才能避免这种"Watcher 风暴"？

### 推广计划提示

- **开发**：Watcher 机制是 ZooKeeper 事件驱动的核心，所有分布式原语（锁、选主、服务发现）都依赖它。务必亲手运行本章示例，观察 Watcher 的一次性行为和重新注册模式
- **运维**：通过四字命令 `wchs`、`wchc`、`wchp` 可以查看当前集群的 Watcher 信息，监控 Watcher 数量是否异常增长
- **测试**：测试 Watcher 相关功能时，需要覆盖网络断开重连场景，确保 Watcher 在重连后能重新生效
