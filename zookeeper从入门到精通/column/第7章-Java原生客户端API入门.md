# 第7章：Java 原生客户端 API 入门

## 1. 项目背景

### 业务场景

你已经熟悉了 zkCli.sh 命令行和 Watcher 机制，但生产系统中，Java 服务需要编程方式与 ZooKeeper 交互。你需要：

- 服务启动时从 ZooKeeper 读取配置
- 运行时监听配置变化
- 向 ZooKeeper 注册服务地址
- 获取分布式锁

ZooKeeper 提供了原生 Java 客户端 API，这是所有其他客户端（Curator、ZkClient）的基础。

### 痛点放大

直接使用 zkCli.sh 不能满足编程需求，而原生 API 如果使用不当，会遇到一系列问题：

- **连接管理混乱**：ZooKeeper 对象的生命周期管理不当导致连接泄漏
- **Watcher 注册遗漏**：一次性 Watcher 忘记重新注册，配置变更无法感知
- **版本冲突**：并发写入时未使用版本号，导致数据相互覆盖
- **超时处理不当**：Session 过期后未正确处理，导致临时节点数据不一致
- **单点故障**：只连接一个 ZooKeeper 地址，集群中某节点宕机后无法连接

---

## 2. 项目设计

### 剧本式交锋对话

**场景**：小胖正在用 Java 写一个 ZooKeeper 操作工具类，遇到了连接状态问题。

**小胖**：我初始化了一个 `ZooKeeper` 对象，然后紧接着就 `create` 节点，结果报 `ConnectionLoss` 异常！

```java
ZooKeeper zk = new ZooKeeper("127.0.0.1:2181", 5000, null);
zk.create("/test", "data".getBytes(), ...);  // ConnectionLoss
```

**大师**：`ZooKeeper` 构造函数是**异步**的——它立即返回，此时连接可能还没建立。你需要等待 `SyncConnected` 事件后才能操作。

正确的做法：

```java
CountDownLatch latch = new CountDownLatch(1);

ZooKeeper zk = new ZooKeeper("127.0.0.1:2181", 5000, event -> {
    if (event.getState() == Watcher.Event.KeeperState.SyncConnected) {
        latch.countDown();  // 连接建立，释放锁
    }
});

latch.await();  // 等待连接建立
zk.create("/test", "data".getBytes(), ...);  // 现在可以了
```

**小白**：那连接建立了之后，万一 ZooKeeper 服务端挂了，或者网络出问题了，客户端怎么办？

**大师**：ZooKeeper 客户端有**自动重连**机制。连接断开时，客户端会在后台不断尝试重连。你需要监听连接状态变化：

- `SyncConnected`：连接正常，可以操作
- `Disconnected`：连接断开，正在重连，不要操作（操作会失败）
- `Expired`：会话过期！需要重新创建 `ZooKeeper` 对象
- `AuthFailed`：认证失败

**小胖**：同步 API 和异步 API 有啥区别？我看到 ZooKeeper 提供了两套方法。

**大师**：ZooKeeper 的每个操作都有同步和异步两种版本：

```java
// 同步版：阻塞直到结果返回或异常
Stat stat = zk.exists("/path", false);
byte[] data = zk.getData("/path", false, null);

// 异步版：立即返回，结果通过回调通知
zk.exists("/path", false, (rc, path, ctx, stat) -> {
    // 处理结果
}, null);
zk.getData("/path", false, (rc, path, ctx, data, stat) -> {
    // 处理结果
}, null);
```

**小白**：异步回调中的 `rc` 是什么意思？

**大师**：`rc` 是 `KeeperException.Code`，表示操作结果码：
- `0`：OK，成功
- `-4`：ConnectionLoss，连接断开
- `-110`：NodeExists，节点已存在
- `-101`：NoNode，节点不存在
- `-112`：SessionExpired，会话过期

> **技术映射**：同步 = 打电话等对方说完，异步 = 发微信等对方回

**小胖**：那版本号在 Java API 里怎么用？我见过 `setData` 的 `version` 参数。

**大师**：版本号是 ZooKeeper 实现乐观锁的关键：

```java
// 第一次读取，版本号为 0
Stat stat = new Stat();
byte[] data = zk.getData("/config", false, stat);
// stat.getVersion() == 0

// 第一次修改，版本号匹配，成功
zk.setData("/config", "v1".getBytes(), 0);  // 成功，版本变为 1

// 第二次修改，用的还是旧版本号，失败
zk.setData("/config", "v2".getBytes(), 0);  // 失败！BadVersion

// 正确做法：先获取最新版本，再修改
Stat newStat = new Stat();
zk.getData("/config", false, newStat);
zk.setData("/config", "v2".getBytes(), newStat.getVersion());  // 成功
```

这就是 CAS（Compare And Swap）乐观锁机制，在分布式锁和配置管理中使用频率极高。

> **技术映射**：版本号 = 文件版本控制，setData 带版本 = git push 前检查是否有冲突

---

## 3. 项目实战

### 环境准备

- ZooKeeper 3.9.x 运行中
- JDK 11+
- Maven 3.6+
- IDE（推荐 IntelliJ IDEA）

`pom.xml` 依赖：

```xml
<dependencies>
    <dependency>
        <groupId>org.apache.zookeeper</groupId>
        <artifactId>zookeeper</artifactId>
        <version>3.9.2</version>
    </dependency>
    <dependency>
        <groupId>org.slf4j</groupId>
        <artifactId>slf4j-simple</artifactId>
        <version>2.0.7</version>
    </dependency>
</dependencies>
```

### 分步实现

#### 步骤 1：编写 ZooKeeper 客户端工具类

创建 `ZkClientUtil.java`：

```java
package com.zkdemo;

import org.apache.zookeeper.*;
import org.apache.zookeeper.data.Stat;

import java.util.*;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.function.BiConsumer;

public class ZkClientUtil {
    private final ZooKeeper zk;
    private final String connectString;
    private final int sessionTimeout;
    private volatile boolean connected = false;

    public interface ConnectionStateListener {
        void onStateChanged(Watcher.Event.KeeperState state);
    }

    private final List<ConnectionStateListener> stateListeners = new ArrayList<>();

    public ZkClientUtil(String connectString, int sessionTimeout) throws Exception {
        this.connectString = connectString;
        this.sessionTimeout = sessionTimeout;
        CountDownLatch latch = new CountDownLatch(1);

        this.zk = new ZooKeeper(connectString, sessionTimeout, event -> {
            switch (event.getState()) {
                case SyncConnected:
                    connected = true;
                    latch.countDown();
                    break;
                case Disconnected:
                    connected = false;
                    System.out.println("连接断开，正在自动重连...");
                    break;
                case Expired:
                    connected = false;
                    System.out.println("会话过期！需要重新连接");
                    break;
                case AuthFailed:
                    System.out.println("认证失败！");
                    break;
            }
            for (ConnectionStateListener listener : stateListeners) {
                listener.onStateChanged(event.getState());
            }
        });

        if (!latch.await(sessionTimeout, TimeUnit.MILLISECONDS)) {
            throw new RuntimeException("ZooKeeper 连接超时");
        }
    }

    public void addStateListener(ConnectionStateListener listener) {
        stateListeners.add(listener);
    }

    public boolean isConnected() {
        return connected && zk.getState() == ZooKeeper.States.CONNECTED;
    }

    // 创建节点（自动创建父节点）
    public String create(String path, byte[] data, CreateMode mode) throws Exception {
        try {
            return zk.create(path, data, ZooDefs.Ids.OPEN_ACL_UNSAFE, mode);
        } catch (KeeperException.NoNodeException e) {
            // 父节点不存在，逐级创建
            String parent = path.substring(0, path.lastIndexOf("/"));
            create(parent, new byte[0], CreateMode.PERSISTENT);
            return zk.create(path, data, ZooDefs.Ids.OPEN_ACL_UNSAFE, mode);
        }
    }

    // 读取数据
    public byte[] getData(String path) throws Exception {
        return zk.getData(path, false, null);
    }

    // 读取数据（带 Watcher）
    public byte[] getDataWithWatcher(String path, Watcher watcher) throws Exception {
        return zk.getData(path, watcher, null);
    }

    // 更新数据（乐观锁）
    public Stat setData(String path, byte[] data, int version) throws Exception {
        return zk.setData(path, data, version);
    }

    // 安全更新：先读取再更新
    public Stat safeSetData(String path, byte[] newData) throws Exception {
        Stat stat = new Stat();
        zk.getData(path, false, stat);
        return zk.setData(path, newData, stat.getVersion());
    }

    // 检查节点是否存在
    public boolean exists(String path) throws Exception {
        return zk.exists(path, false) != null;
    }

    // 获取子节点
    public List<String> getChildren(String path) throws Exception {
        return zk.getChildren(path, false);
    }

    // 删除节点
    public void delete(String path) throws Exception {
        // 递归删除
        List<String> children = zk.getChildren(path, false);
        for (String child : children) {
            delete(path + "/" + child);
        }
        zk.delete(path, -1);
    }

    public void close() throws InterruptedException {
        zk.close();
    }

    // 注册配置监听
    public void watchConfig(String path, BiConsumer<String, byte[]> callback) throws Exception {
        zk.getData(path, new Watcher() {
            @Override
            public void process(WatchedEvent event) {
                if (event.getType() == Event.EventType.NodeDataChanged) {
                    try {
                        // 重新注册 Watcher
                        Stat stat = new Stat();
                        byte[] newData = zk.getData(path, this, stat);
                        callback.accept(path, newData);
                    } catch (Exception e) {
                        e.printStackTrace();
                    }
                }
            }
        }, null);
    }
}
```

#### 步骤 2：使用工具类

创建 `ZkClientDemo.java`：

```java
package com.zkdemo;

import org.apache.zookeeper.CreateMode;
import org.apache.zookeeper.data.Stat;

import java.util.concurrent.CountDownLatch;

public class ZkClientDemo {
    public static void main(String[] args) throws Exception {
        // 1. 创建客户端
        System.out.println("=== 连接 ZooKeeper ===");
        ZkClientUtil zk = new ZkClientUtil("127.0.0.1:2181", 5000);
        System.out.println("连接状态: " + zk.isConnected());

        // 2. 创建节点
        System.out.println("\n=== 创建节点 ===");
        String path = zk.create("/demo-app/config", "initial-config".getBytes(), CreateMode.PERSISTENT);
        System.out.println("创建节点: " + path);

        // 3. 读取数据
        System.out.println("\n=== 读取数据 ===");
        byte[] data = zk.getData("/demo-app/config");
        System.out.println("数据: " + new String(data));

        // 4. 更新数据（乐观锁）
        System.out.println("\n=== 原子更新 ===");
        Stat newStat = zk.safeSetData("/demo-app/config", "updated-config".getBytes());
        System.out.println("更新成功，新版本: " + newStat.getVersion());

        byte[] updatedData = zk.getData("/demo-app/config");
        System.out.println("新数据: " + new String(updatedData));

        // 5. 注册配置监听
        System.out.println("\n=== 注册配置监听 ===");
        CountDownLatch watchLatch = new CountDownLatch(1);
        zk.watchConfig("/demo-app/config", (p, d) -> {
            System.out.println("配置变更! 路径: " + p + ", 新值: " + new String(d));
            watchLatch.countDown();
        });
        System.out.println("监听已注册，请在另一个终端修改配置：");
        System.out.println("  set /demo-app/config \"changed-by-cli\"");

        // 等待配置变更
        watchLatch.await();
        System.out.println("配置监听测试通过!");

        // 6. 清理
        System.out.println("\n=== 清理 ===");
        zk.delete("/demo-app");
        System.out.println("已清理所有测试节点");

        zk.close();
        System.out.println("连接已关闭");
    }
}
```

#### 步骤 3：运行测试

编译运行后，在另一个终端执行：

```bash
./bin/zkCli.sh -server 127.0.0.1:2181
set /demo-app/config "changed-by-cli"
```

程序输出：

```
=== 连接 ZooKeeper ===
连接状态: true

=== 创建节点 ===
创建节点: /demo-app/config

=== 读取数据 ===
数据: initial-config

=== 原子更新 ===
更新成功，新版本: 1
新数据: updated-config

=== 注册配置监听 ===
监听已注册，请在另一个终端修改配置：
  set /demo-app/config "changed-by-cli"

配置变更! 路径: /demo-app/config, 新值: changed-by-cli
配置监听测试通过!

=== 清理 ===
已清理所有测试节点
连接已关闭
```

### 可能遇到的坑

| 问题 | 原因 | 解决 |
|------|------|------|
| `ConnectionLoss` | 网络断开或 ZooKeeper 繁忙 | 启用重试机制，使用 Curator |
| `SessionExpired` | 会话超时 | 重新创建 ZooKeeper 对象 |
| `BadVersion` | 版本冲突 | 先 `getData` 获取最新版本再 `setData` |
| Watcher 不生效 | 忘记在回调中重新注册 | Watcher 是一次性的，需重新注册 |
| 节点创建失败 | 父节点不存在 | 先确保父节点存在 |

### 完整代码清单

代码见 `column/code/chapter07/`。

### 测试验证

```bash
# 编译
mvn compile

# 运行
mvn exec:java -Dexec.mainClass="com.zkdemo.ZkClientDemo"
```

---

## 4. 项目总结

### 优点 & 缺点

| 维度 | 优点 | 缺点 |
|------|------|------|
| 可控性 | 完全掌控连接生命周期和 Watcher 注册 | 代码量较大，模板代码多 |
| 理解深度 | 练习后深入理解 ZooKeeper 通信机制 | 自动重连、会话管理等需手动处理 |
| 依赖 | 零额外依赖 | 功能较基础，缺少高级原语封装 |

### 适用场景

- 学习 ZooKeeper 原理和客户端通信机制
- 对依赖有严格限制（不能引入 Curator 等第三方库）的项目
- 需要高度自定义客户端行为的场景

**不适用场景**：
- 生产级应用（推荐使用 Curator，自动处理重连、会话管理）
- 需要分布式锁、选主等高级原语

### 注意事项

- `ZooKeeper` 构造函数是异步的，必须等待 `SyncConnected` 事件
- 不要在 Watcher 回调中做耗时操作（阻塞 EventThread）
- 版本号参数传 `-1` 表示跳过版本检查（不推荐，可能导致数据覆盖）
- 一个 `ZooKeeper` 实例的 Watcher 回调在单个线程（EventThread）中执行

### 常见踩坑经验

**故障 1：Watcher 回调抛出异常导致后续 Watcher 不触发**

现象：注册了多个 Watcher，第一个 Watcher 回调抛出异常后，后续所有 Watcher 都不再触发。

根因：EventThread 中任何一个 Watcher 抛出未捕获异常，会导致 EventThread 终止，所有后续 Watcher 无法执行。`process()` 方法必须 try-catch 所有异常。

**故障 2：频繁创建 ZooKeeper 连接导致 ZooKeeper 服务端连接数打满**

现象：每个请求都创建一个新的 `ZooKeeper` 对象，用完不关闭，ZooKeeper 服务端连接数飙升至上限。

根因：`ZooKeeper` 对象是重量级对象，每个实例对应一个 TCP 连接和一个线程池。应该复用 `ZooKeeper` 实例，使用连接池模式。

### 思考题

1. 在 `ZkClientUtil` 中，`watchConfig` 方法注册了一次性 Watcher。如果在重新注册 Watcher 的间隙中配置发生了变化，会丢失通知。如何设计一个"可靠的"配置监听，确保不丢失任何变更？
2. `getData` 的 Watcher 在节点删除时也会触发。如果节点被删除，Watcher 回调中的 `getData` 会抛出 `NoNodeException`。应该如何设计优雅的处理逻辑？

### 推广计划提示

- **开发**：本章的原生 API 是所有 ZK 客户端的基础，建议亲手实现 `ZkClientUtil` 加深理解
- **测试**：测试 ZooKeeper 操作时，覆盖 `ConnectionLoss`、`SessionExpired`、`BadVersion` 三种异常
- **运维**：了解原生客户端的连接状态变更机制，有助于排查客户端连接异常问题
