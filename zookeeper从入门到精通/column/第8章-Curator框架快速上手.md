# 第8章：Curator 框架快速上手

## 1. 项目背景

### 业务场景

通过第 7 章的学习，你掌握了 ZooKeeper 原生 Java API。但你很快发现一个问题：**每个项目都需要重复写连接管理、重试、Watcher 重新注册这些模板代码**。

具体来说，原生 API 的痛点：
- 连接断开后不会自动重试失败的操作
- Watcher 是一次性的，每次都要手动重新注册
- 没有递归创建/删除节点的方法
- 缺少高级原语（分布式锁、选主等）

Apache Curator 是 Netflix 开源并捐给 Apache 的 ZooKeeper 客户端框架，它封装了 ZooKeeper 原生 API 的所有繁琐细节。

### 痛点放大

用原生 API 做一个分布式锁，你需要：

1. 创建一个 ZooKeeper 客户端
2. 在循环中尝试创建临时顺序节点
3. 检查自己的序号是不是最小
4. 如果不是，注册 Watcher 监听前一个节点
5. 等待 Watcher 触发后重新检查
6. 处理连接断开（重连后重新获取锁节点）
7. 处理会话过期（销毁旧锁，重新创建新锁）

用 Curator，只需要：

```java
InterProcessMutex lock = new InterProcessMutex(client, "/locks/mylock");
lock.acquire();    // 加锁
// ... 业务逻辑 ...
lock.release();    // 解锁
```

---

## 2. 项目设计

### 剧本式交锋对话

**场景**：小胖和小白在讨论用原生 API 写的 ZooKeeper 工具类出了很多古怪的异常。

**小胖**：我那个 ZkClientUtil 最多间断连几次就崩溃了，重连后原来的 Watcher 全丢了。

**小白**：我的也是一样，连接断开后 `safeSetData` 一直抛异常，我加了个 while 循环一直重试，反而把 ZooKeeper 搞挂了。

**大师**：这就是为什么要用 Curator。Curator 帮你抽象了所有连接管理的细节。来看看 Curator 是怎么解决你们的问题的。

**Curator vs 原生 API 对比**：

| 能力 | 原生 API | Curator |
|------|---------|---------|
| 连接管理 | 手动处理 SyncConnected/Disconnected/Expired | 自动重连 + 状态监听 |
| 操作重试 | 手动 while 循环 | ConnectionStateListener + RetryPolicy |
| 递归创建 | 无，需逐级创建 | `creatingParentsIfNeeded()` |
| Watcher | 一次性，需重新注册 | `NodeCache` / `PathChildrenCache` 持续监听 |
| 高级原语 | 需自己实现 | 内置分布式锁、选主、计数器等 |

**小白**：那 Curator 怎么用？直接替换原生 API 就行吗？

**大师**：看一个例子。原生 API：

```java
// 1. 创建连接（异步！）
CountDownLatch latch = new CountDownLatch(1);
ZooKeeper zk = new ZooKeeper("127.0.0.1:2181", 5000, event -> {
    if (event.getState() == Watcher.Event.KeeperState.SyncConnected) {
        latch.countDown();
    }
});
latch.await();

// 2. 创建节点（如果父节点不存在，手动逐级创建）
if (zk.exists("/parent", false) == null) {
    zk.create("/parent", new byte[0], ZooDefs.Ids.OPEN_ACL_UNSAFE, CreateMode.PERSISTENT);
}
zk.create("/parent/child", "data".getBytes(), ZooDefs.Ids.OPEN_ACL_UNSAFE, CreateMode.PERSISTENT);
```

Curator：

```java
// 1. 创建连接（同步！自动重连！）
CuratorFramework client = CuratorFrameworkFactory.newClient("127.0.0.1:2181",
        new ExponentialBackoffRetry(1000, 3));
client.start();

// 2. 创建节点（自动递归创建父节点）
client.create().creatingParentsIfNeeded()
    .forPath("/parent/child", "data".getBytes());
```

**小胖**：这差距也太大了吧！一行 `creatingParentsIfNeeded()` 就解决了递归创建的问题。

**大师**：还不止这些。Curator 还有一个核心概念——**RetryPolicy**。ZooKeeper 的异常大多数是暂时的（ConnectionLoss、SessionMove等），可以重试。Curator 内置了 4 种重试策略：

- `ExponentialBackoffRetry`：指数退避重试（推荐），如 1s、2s、4s、8s…
- `RetryNTimes`：固定间隔重试 N 次
- `RetryOneTime`：只重试一次
- `RetryUntilElapsed`：在指定时间内持续重试

> **技术映射**：Curator = 高级全自动汽车，原生 API = 手动挡面包车

**小白**：那 Curator 的 Watcher 呢？原生 API 的一次性 Watcher 用起来真的很麻烦。

**大师**：Curator 提供了三种"Cache"来简化 Watcher 使用：

| Cache 类型 | 监听内容 | 触发时机 |
|-----------|---------|---------|
| `NodeCache` | 单个节点的数据变化 | 节点数据变化或创建/删除 |
| `PathChildrenCache` | 一个路径的子节点变化 | 子节点创建/删除/数据变化 |
| `TreeCache` | 整个子树的变化 | 任意节点创建/删除/数据变化 |

这些 Cache 会自动处理 Watcher 的重新注册，你只需要监听回调就行：

```java
NodeCache cache = new NodeCache(client, "/config/db-url");
cache.getListenable().addListener(() -> {
    byte[] data = cache.getCurrentData().getData();
    System.out.println("Config changed: " + new String(data));
});
cache.start();
// 无需担心 Watcher 重新注册，NodeCache 内部自动处理
```

**小胖**：最后，Curator 的 namespace 是干啥的？

**大师**：**namespace** 是 Curator 的一个神来之笔。假设你所有数据都放在 `/myapp/` 下，你可以设置 namespace 为 `myapp`：

```java
CuratorFramework client = CuratorFrameworkFactory.builder()
    .connectString("127.0.0.1:2181")
    .namespace("myapp")   // 所有操作自动加上 /myapp 前缀
    .retryPolicy(new ExponentialBackoffRetry(1000, 3))
    .build();

// 实际上操作的是 /myapp/config
client.create().forPath("/config", "data".getBytes());
```

这在多租户场景下非常有用——不同服务使用不同的 namespace，天然隔离。

> **技术映射**：namespace = 你的专属工作区，你的操作都在这个工作区里

---

## 3. 项目实战

### 环境准备

- ZooKeeper 3.9.x 运行中
- JDK 11+
- Maven

`pom.xml`：

```xml
<dependencies>
    <dependency>
        <groupId>org.apache.curator</groupId>
        <artifactId>curator-framework</artifactId>
        <version>5.6.0</version>
    </dependency>
    <dependency>
        <groupId>org.apache.curator</groupId>
        <artifactId>curator-recipes</artifactId>
        <version>5.6.0</version>
    </dependency>
    <dependency>
        <groupId>org.apache.curator</groupId>
        <artifactId>curator-test</artifactId>
        <version>5.6.0</version>
        <scope>test</scope>
    </dependency>
    <dependency>
        <groupId>org.slf4j</groupId>
        <artifactId>slf4j-simple</artifactId>
        <version>2.0.7</version>
    </dependency>
</dependencies>
```

### 分步实现

#### 步骤 1：创建 Curator 客户端

创建 `CuratorBasicsDemo.java`：

```java
package com.zkdemo;

import org.apache.curator.RetryPolicy;
import org.apache.curator.framework.CuratorFramework;
import org.apache.curator.framework.CuratorFrameworkFactory;
import org.apache.curator.framework.recipes.cache.*;
import org.apache.curator.retry.ExponentialBackoffRetry;
import org.apache.zookeeper.CreateMode;
import org.apache.zookeeper.data.Stat;

public class CuratorBasicsDemo {
    public static void main(String[] args) throws Exception {
        // 1. 创建 Curator 客户端（自动连接、自动重连）
        RetryPolicy retryPolicy = new ExponentialBackoffRetry(1000, 3);
        CuratorFramework client = CuratorFrameworkFactory.builder()
                .connectString("127.0.0.1:2181")
                .sessionTimeoutMs(5000)
                .connectionTimeoutMs(3000)
                .retryPolicy(retryPolicy)
                .namespace("myapp")  // 所有操作自动加上 /myapp 前缀
                .build();
        client.start();
        System.out.println("Curator 客户端已启动");

        // 2. Fluent 风格 CRUD
        System.out.println("\n=== CRUD 操作 ===");

        // 创建节点（自动递归创建父节点）
        client.create().creatingParentsIfNeeded()
                .withMode(CreateMode.PERSISTENT)
                .forPath("/config/db-url", "jdbc:mysql://localhost:3306/db".getBytes());
        System.out.println("创建节点: /config/db-url");

        // 读取节点
        byte[] data = client.getData().forPath("/config/db-url");
        System.out.println("读取数据: " + new String(data));

        // 更新节点
        client.setData().forPath("/config/db-url", "jdbc:mysql://new-host:3306/db".getBytes());
        byte[] newData = client.getData().forPath("/config/db-url");
        System.out.println("更新后: " + new String(newData));

        // 检查节点是否存在
        Stat stat = client.checkExists().forPath("/config/db-url");
        System.out.println("节点存在: " + (stat != null) + ", dataVersion=" + stat.getVersion());

        // 获取子节点列表
        System.out.println("\n=== 子节点列表 ===");
        var children = client.getChildren().forPath("/config");
        System.out.println("/config 的子节点: " + children);

        // 3. NodeCache 示例（持续监听节点变化）
        System.out.println("\n=== NodeCache 持续监听 ===");
        NodeCache nodeCache = new NodeCache(client, "/config/db-url");
        nodeCache.getListenable().addListener(() -> {
            ChildData currentData = nodeCache.getCurrentData();
            if (currentData != null) {
                System.out.println("NodeCache 通知: /config/db-url 已变更 -> "
                        + new String(currentData.getData()));
            }
        });
        nodeCache.start();
        System.out.println("NodeCache 已启动，请在另一个终端修改 /config/db-url...");

        // 4. PathChildrenCache 示例（监听子节点变化）
        System.out.println("\n=== PathChildrenCache 子节点监听 ===");
        // 创建一些子节点
        client.create().forPath("/services", "".getBytes());

        PathChildrenCache childrenCache = new PathChildrenCache(client, "/services", true);
        childrenCache.getListenable().addListener((curator, event) -> {
            System.out.println("子节点变更: " + event.getType()
                    + " | 路径: " + event.getData().getPath()
                    + " | 数据: " + new String(event.getData().getData()));
        });
        childrenCache.start(PathChildrenCache.StartMode.POST_INITIALIZED_EVENT);
        System.out.println("PathChildrenCache 已启动，请在另一个终端操作 /services...");

        // 等待用户手动操作
        System.out.println("\n按 Enter 键退出并清理...");
        System.in.read();

        // 5. 清理
        nodeCache.close();
        childrenCache.close();
        client.delete().deletingChildrenIfNeeded().forPath("/config");
        client.delete().deletingChildrenIfNeeded().forPath("/services");
        client.close();
        System.out.println("清理完成，客户端已关闭");
    }
}
```

#### 步骤 2：运行并手动测试

```bash
# 编译运行
mvn compile
mvn exec:java -Dexec.mainClass="com.zkdemo.CuratorBasicsDemo"
```

在另一个终端：

```bash
# 测试 NodeCache
./bin/zkCli.sh -server 127.0.0.1:2181
# 注意：由于有 namespace=myapp，实际操作的路径是 /myapp/config/db-url
# 但通过 Curator 操作时，会自动加前缀
# 如果通过 zkCli.sh 直接操作，需要手动加 /myapp 前缀

set /myapp/config/db-url "jdbc:mysql://changed-host:3306/db"

# 测试 PathChildrenCache
create /myapp/services/order-service "127.0.0.1:8080"
create /myapp/services/user-service "127.0.0.1:8081"
delete /myapp/services/order-service
```

期望输出：

```
Curator 客户端已启动

=== CRUD 操作 ===
创建节点: /config/db-url
读取数据: jdbc:mysql://localhost:3306/db
更新后: jdbc:mysql://new-host:3306/db
节点存在: true, dataVersion=1

=== 子节点列表 ===
/config 的子节点: [db-url]

=== NodeCache 持续监听 ===
NodeCache 已启动，请在另一个终端修改 /config/db-url...
NodeCache 通知: /config/db-url 已变更 -> jdbc:mysql://changed-host:3306/db

=== PathChildrenCache 子节点监听 ===
PathChildrenCache 已启动，请在另一个终端操作 /services...
子节点变更: CHILD_ADDED | 路径: /services/order-service | 数据: 127.0.0.1:8080
子节点变更: CHILD_ADDED | 路径: /services/user-service | 数据: 127.0.0.1:8081
子节点变更: CHILD_REMOVED | 路径: /services/order-service | 数据: 127.0.0.1:8080
```

#### 步骤 3：集成 Spring Boot

创建 `ZKConfig.java`：

```java
import org.apache.curator.RetryPolicy;
import org.apache.curator.framework.CuratorFramework;
import org.apache.curator.framework.CuratorFrameworkFactory;
import org.apache.curator.retry.ExponentialBackoffRetry;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class ZKConfig {

    @Bean(destroyMethod = "close")
    public CuratorFramework curatorFramework() {
        RetryPolicy retryPolicy = new ExponentialBackoffRetry(1000, 3);
        CuratorFramework client = CuratorFrameworkFactory.builder()
                .connectString("127.0.0.1:2181")
                .sessionTimeoutMs(5000)
                .retryPolicy(retryPolicy)
                .namespace("spring-app")
                .build();
        client.start();
        return client;
    }
}
```

在 Service 中使用：

```java
@Service
public class ConfigService {
    private final CuratorFramework client;

    public ConfigService(CuratorFramework client) {
        this.client = client;
    }

    public String getConfig(String key) throws Exception {
        byte[] data = client.getData().forPath("/config/" + key);
        return new String(data);
    }

    public void updateConfig(String key, String value) throws Exception {
        client.setData().forPath("/config/" + key, value.getBytes());
    }
}
```

### 可能遇到的坑

| 问题 | 原因 | 解决 |
|------|------|------|
| 操作报 `ConnectionLoss` | Curator 重试策略耗尽 | 增加重试次数或超时时间 |
| namespace 下节点不存在 | zkCli.sh 操作没加 namespace 前缀 | Curator 的 namespace 对 zkCli 不可见 |
| NodeCache 不触发 | 没有 `start()` | 创建 NodeCache 后必须显式 start |
| 版本冲突 | 多线程同时修改 | 使用 `setData().withVersion()` 乐观锁 |

### 完整代码清单

代码见 `column/code/chapter08/`。

### 测试验证

```bash
mvn test
```

---

## 4. 项目总结

### 优点 & 缺点

| 维度 | 优点 | 缺点 |
|------|------|------|
| 易用性 | Fluent API 简洁优雅 | 学习 Java 8+ 和函数式编程概念 |
| 功能 | 内置重试、Cache、高级原语 | jar 包体积较大（~5MB） |
| 可靠性 | 自动重连、会话管理 | 重试策略配置不当可能影响性能 |
| 生态 | Spring Boot 集成简单 | 部分 Recipe 内部实现复杂，排查困难 |

### 适用场景

- 所有生产环境的 Java ZooKeeper 客户端
- 需要分布式锁、选主、计数器等高级原语
- 需要持续监听节点变化的场景
- 微服务架构中的 ZooKeeper 集成

**不适用场景**：
- 非 Java 项目（可用 Kazoo/Python、zkClient/Go 等）
- 资源受限环境（jar 包较大）

### 注意事项

- Curator 5.x 对应 ZooKeeper 3.8+，Curator 4.x 对应 ZooKeeper 3.5+
- `NodeCache` 默认不开启数据压缩，大数据量时注意
- `PathChildrenCache.StartMode` 有三种模式，启动时是否初始化已有数据
- 使用 namespace 时，通过 zkCli.sh 操作要手动添加 namespace 前缀

### 常见踩坑经验

**故障 1：Curator 重试导致 ZooKeeper 请求积压**

现象：ZooKeeper 服务端短暂不可用后恢复，Curator 客户端大量重试请求导致服务端瞬间过载。

根因：`ExponentialBackoffRetry(1000, Integer.MAX_VALUE)` 导致无限重试。应该设置合理的最大重试次数，配合 `maxElapsedTimeMs` 限制。

**故障 2：NodeCache start 后读取到空数据**

现象：NodeCache 启动后，`getCurrentData()` 返回 null。

根因：`start()` 是异步的，启动后需要等待初始数据加载完成。使用 `startAsync()` 或等待 `initialized` 事件。

### 思考题

1. Curator 的 `NodeCache` 和 `PathChildrenCache` 在实现上有什么本质区别？它们分别适用于什么场景？
2. Curator 的 `ExponentialBackoffRetry` 在第三次重试时等待了 4 秒，但 ZooKeeper 在此期间已经恢复。这 4 秒的等待时间能优化吗？怎么优化？

### 推广计划提示

- **开发**：新项目一律使用 Curator 作为 ZooKeeper 客户端，不再直接使用原生 API
- **运维**：注意 Curator 的重试策略配置，避免重试风暴打垮 ZooKeeper 集群
- **测试**：使用 `curator-test` 的 `TestingServer` 和 `TestingCluster` 进行单元测试
