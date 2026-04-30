# 第4章：ZNode 深度解析——持久/临时/顺序/容器/TTL 节点

## 1. 项目背景

### 业务场景

你在 zkCli.sh 中已经尝试创建过 ZNode 了，但你有没有想过：为什么 ZooKeeper 需要多种节点类型？

考虑一个分布式锁的场景：
- 客户端 A 获取锁，如果突然宕机了，锁应该自动释放，否则其他客户端永远无法获取锁——这需要**临时节点**
- 多个客户端同时争抢锁，需要为每个客户端生成唯一的排队序号——这需要**顺序节点**
- 锁释放后，当初排队时创建的节点应该自动清理——**临时节点 + 顺序节点**的组合

再看一个服务发现的场景：
- 服务实例启动时注册自己，断连时自动注销——**临时节点**
- 多个同名的服务实例需要唯一的编号——**顺序节点**（或临时顺序节点）

### 痛点放大

如果不理解 ZNode 类型的差异，你可能会遇到这些问题：

- 服务实例注册用了持久节点，进程挂了后注册信息成为"僵尸数据"，导致服务发现拿到不可用的地址
- 分布式锁实现用了普通持久节点，客户端挂了解锁失败，所有客户端死锁
- 需要生成全局唯一 ID，用 UUID 虽然唯一但无序，用数据库自增又太慢
- 临时节点下创建了子节点，会话断开后临时节点删除，子节点却成了"孤儿"

ZooKeeper 的 ZNode 类型设计正是为了解决这些分布式场景中的常见问题。

---

## 2. 项目设计

### 剧本式交锋对话

**场景**：小白的代码里用了 ZooKeeper 做分布式锁，但出现了一个诡异的问题——锁永远不会释放。

**小白**：（皱眉）我用 ZooKeeper 实现了一个分布式锁，客户端 A 获取锁后写了一个 ZNode `/lock`，然后客户端 A 挂了。理论上这个锁应该在 A 断开后释放，但现在其他客户端永远拿不到锁。

**大师**：你创建 `/lock` 的时候，用的是 `create` 还是 `create -e`？

**小白**：就用了 `create /lock "locked-by-A"`。这有什么区别吗？

**大师**：区别大了！`create` 默认创建的是**持久节点**。持久节点的生命周期和数据创建者无关——即使创建它的客户端断开连接，这个节点依然存在。所以当你用了持久节点做锁，客户端 A 挂了的那个 `/lock` 节点永远留在那里，其他客户端每次 `create /lock` 都会失败。

**小胖**：哦！那应该用 `create -e`（临时节点），这样客户端断开后 ZooKeeper 自动把节点删了，锁自然就释放了！

**大师**：对。临时节点的生命周期绑定在客户端会话（Session）上。只要会话超时或者客户端主动关闭，所有属于这个会话的临时节点都会被 ZooKeeper 自动清理。

**小白**：那如果多个客户端同时 `create -e /lock`，只有一个能成功。其他的怎么办？一直重试吗？这不就是"惊群效应"吗？

**大师**：好问题。单个临时节点做锁，确实会有**惊群效应**——所有客户端都盯着同一个节点，谁创建成功谁获得锁，其他疯狂重试。效率低，而且造成 ZooKeeper 大量写请求。

正确的做法是用**临时顺序节点**：

```bash
create -e -s /lock/
```

这会创建一个类似 `/lock/0000000001`、`/lock/0000000002` 的节点。每个客户端创建后，检查自己的序号是不是最小的。如果是，获得锁；如果不是，监听前一个节点。

> **技术映射**：持久节点 = 永久贴在墙上的公告，临时节点 = 用胶带贴的便签（一撕就掉），顺序节点 = 银行叫号机

**小胖**：那 ZooKeeper 5 种节点类型怎么区分？我现在脑袋里有点乱。

**大师**：我给你画个表就清楚了：

| 类型 | 创建命令 | 生命周期 | 用途 |
|------|---------|---------|------|
| 持久节点 | `create /path` | 显式 delete 删除 | 配置存储、命名空间 |
| 持久顺序节点 | `create -s /path` | 显式 delete 删除 | 全局 ID 生成 |
| 临时节点 | `create -e /path` | 会话断开自动删除 | 服务注册、分布式锁 |
| 临时顺序节点 | `create -e -s /path` | 会话断开自动删除 | 公平分布式锁 |
| 容器节点（3.5+） | `create -c /path` | 子节点全部删除后自动删除 | 占位父节点 |
| TTL 节点（3.5+） | `create -t TTL /path` | 超时自动删除 | 临时数据、缓存 |

**小白**：容器节点和 TTL 节点是 3.5 以后才有的？它们解决什么问题？

**大师**：**容器节点**解决的是"空目录清理"问题。比如你有一个路径存放某个微服务的所有实例 `/services/order-service`，如果服务下线了所有临时节点都删了，这个目录路径还留着。容器节点会在它的子节点全部删除后自动自我清理。

**TTL 节点**解决的是"超时自动删除"场景。比如你想在 ZooKeeper 里存一个"密码重置验证 Token"，30 分钟后自动过期。用 TTL 节点就很自然，不用单独写一个定时清理任务。

> **技术映射**：容器节点 = 一次性饭盒，吃完就扔；TTL 节点 = 倒计时自动销毁的文件

**小白**：那临时节点能不能有子节点？

**大师**：不能！这是 ZooKeeper 的一个限制。临时节点不能创建子节点。原因很简单：临时节点的生命周期跟随会话，会话断开时 ZooKeeper 要递归删除临时节点及所有子节点。如果允许子节点，这个递归删除操作在极端情况下可能很慢，而且子节点可能是其他会话创建的，语义混乱。

---

## 3. 项目实战

### 环境准备

- ZooKeeper 3.9.x 集群（单机模式即可）
- Java 11+
- Maven 3.6+

### 分步实现

创建 Maven 项目，在 `pom.xml` 中添加依赖：

```xml
<dependencies>
    <dependency>
        <groupId>org.apache.zookeeper</groupId>
        <artifactId>zookeeper</artifactId>
        <version>3.9.2</version>
    </dependency>
</dependencies>
```

#### 步骤 1：编写 ZNode 类型演示程序

创建 `ZNodeTypeDemo.java`：

```java
package com.zkdemo;

import org.apache.zookeeper.*;
import org.apache.zookeeper.data.Stat;

import java.util.List;
import java.util.concurrent.CountDownLatch;

public class ZNodeTypeDemo {
    private static final String ZK_URL = "127.0.0.1:2181";
    private static final CountDownLatch connectedSignal = new CountDownLatch(1);

    public static void main(String[] args) throws Exception {
        // 创建客户端连接
        ZooKeeper zk = new ZooKeeper(ZK_URL, 5000, event -> {
            if (event.getState() == Watcher.Event.KeeperState.SyncConnected) {
                connectedSignal.countDown();
            }
        });
        connectedSignal.await();

        // 1. 持久节点：显式删除前一直存在
        System.out.println("=== 1. 持久节点 ===");
        zk.create("/persistent", "persistent-data".getBytes(),
                ZooDefs.Ids.OPEN_ACL_UNSAFE, CreateMode.PERSISTENT);
        byte[] data = zk.getData("/persistent", false, null);
        System.out.println("持久节点数据: " + new String(data));

        // 2. 持久顺序节点：自动编号
        System.out.println("\n=== 2. 持久顺序节点 ===");
        String seqPath1 = zk.create("/seq-", "seq1".getBytes(),
                ZooDefs.Ids.OPEN_ACL_UNSAFE, CreateMode.PERSISTENT_SEQUENTIAL);
        String seqPath2 = zk.create("/seq-", "seq2".getBytes(),
                ZooDefs.Ids.OPEN_ACL_UNSAFE, CreateMode.PERSISTENT_SEQUENTIAL);
        System.out.println("创建路径1: " + seqPath1);
        System.out.println("创建路径2: " + seqPath2);

        // 3. 临时节点：断开会话后自动删除
        System.out.println("\n=== 3. 临时节点 ===");
        zk.create("/ephemeral", "temp-data".getBytes(),
                ZooDefs.Ids.OPEN_ACL_UNSAFE, CreateMode.EPHEMERAL);
        Stat stat = zk.exists("/ephemeral", false);
        System.out.println("临时节点存在: " + (stat != null));

        // 临时节点不能有子节点（验证）
        try {
            zk.create("/ephemeral/child", "child".getBytes(),
                    ZooDefs.Ids.OPEN_ACL_UNSAFE, CreateMode.PERSISTENT);
        } catch (KeeperException.NoChildrenForEphemeralsException e) {
            System.out.println("临时节点不能创建子节点: " + e.getMessage());
        }

        // 4. 临时顺序节点：分布式锁的基础
        System.out.println("\n=== 4. 临时顺序节点 ===");
        String epSeqPath1 = zk.create("/lock-", "client-1".getBytes(),
                ZooDefs.Ids.OPEN_ACL_UNSAFE, CreateMode.EPHEMERAL_SEQUENTIAL);
        String epSeqPath2 = zk.create("/lock-", "client-2".getBytes(),
                ZooDefs.Ids.OPEN_ACL_UNSAFE, CreateMode.EPHEMERAL_SEQUENTIAL);
        System.out.println("Client1 锁节点: " + epSeqPath1);
        System.out.println("Client2 锁节点: " + epSeqPath2);
        // 序号较小的先获得锁
        String lock1 = epSeqPath1.substring(epSeqPath1.lastIndexOf("/") + 1);
        String lock2 = epSeqPath2.substring(epSeqPath2.lastIndexOf("/") + 1);
        System.out.println("获得锁的客户端: " + (lock1.compareTo(lock2) < 0 ? "Client1" : "Client2"));

        // 5. 容器节点（3.5+）：子节点全部删除后自动清理
        System.out.println("\n=== 5. 容器节点 ===");
        try {
            zk.create("/container", "".getBytes(),
                    ZooDefs.Ids.OPEN_ACL_UNSAFE, CreateMode.CONTAINER);
            // 创建子节点
            zk.create("/container/child1", "c1".getBytes(),
                    ZooDefs.Ids.OPEN_ACL_UNSAFE, CreateMode.PERSISTENT);
            zk.create("/container/child2", "c2".getBytes(),
                    ZooDefs.Ids.OPEN_ACL_UNSAFE, CreateMode.PERSISTENT);
            List<String> children = zk.getChildren("/container", false);
            System.out.println("容器节点子节点: " + children);

            // 删除所有子节点
            zk.delete("/container/child1", -1);
            zk.delete("/container/child2", -1);

            // 等待几秒让 ZooKeeper 异步清理容器节点
            Thread.sleep(1000);
            stat = zk.exists("/container", false);
            System.out.println("所有子节点删除后容器节点存在: " + (stat != null));
            // 注意：容器节点不是立即删除的，需要等待一段时间
        } catch (KeeperException.UnimplementedException e) {
            System.out.println("容器节点不受支持（需要 3.5+）");
        }

        // 6. TTL 节点（3.5+）：超时自动删除
        System.out.println("\n=== 6. TTL 节点 ===");
        // TTL 节点需要服务端启用 extendedTypesEnabled
        System.out.println("TTL 节点需要服务端配置 extendedTypesEnabled=true");

        // 7. 验证临时节点的自动删除
        System.out.println("\n=== 7. 验证临时节点自动删除 ===");
        System.out.println("关闭客户端连接，临时节点 /ephemeral 将自动删除...");
        zk.close();

        // 重新连接验证
        ZooKeeper zk2 = new ZooKeeper(ZK_URL, 5000, event -> {
            if (event.getState() == Watcher.Event.KeeperState.SyncConnected) {
                connectedSignal.countDown();
            }
        });
        connectedSignal.await();

        stat = zk2.exists("/ephemeral", false);
        System.out.println("重连后临时节点 /ephemeral 存在: " + (stat != null));
        // 输出: false（临时节点已自动删除）

        // 持久节点重新连接后仍然存在
        stat = zk2.exists("/persistent", false);
        System.out.println("重连后持久节点 /persistent 存在: " + (stat != null));
        // 输出: true

        // 清理
        zk2.delete("/persistent", -1);
        zk2.delete(seqPath1, -1);
        zk2.delete(seqPath2, -1);
        zk2.delete(epSeqPath1, -1);
        zk2.delete(epSeqPath2, -1);
        zk2.close();
    }
}
```

#### 步骤 2：运行程序

```bash
# 编译
mvn compile

# 运行
mvn exec:java -Dexec.mainClass="com.zkdemo.ZNodeTypeDemo"
```

#### 步骤 3：预期输出

```
=== 1. 持久节点 ===
持久节点数据: persistent-data

=== 2. 持久顺序节点 ===
创建路径1: /seq-0000000001
创建路径2: /seq-0000000002

=== 3. 临时节点 ===
临时节点存在: true
临时节点不能创建子节点: KeeperErrorCode = NoChildrenForEphemerals

=== 4. 临时顺序节点 ===
Client1 锁节点: /lock-0000000003
Client2 锁节点: /lock-0000000004
获得锁的客户端: Client1

=== 5. 容器节点 ===
容器节点子节点: [child1, child2]
所有子节点删除后容器节点存在: false

=== 6. TTL 节点 ===
TTL 节点需要服务端配置 extendedTypesEnabled=true

=== 7. 验证临时节点自动删除 ===
关闭客户端连接，临时节点 /ephemeral 将自动删除...
重连后临时节点 /ephemeral 存在: false
重连后持久节点 /persistent 存在: true
```

### 可能遇到的坑

- **临时节点路径复用**：如果断线重连，之前创建的临时节点已经删除，应用需要重新创建
- **容器节点不是实时删除**：ZooKeeper 3.5+ 的容器节点在子节点全部删除后，会在下一次 `getChildren` 等操作触发清理，不是立即删除
- **TTL 节点需服务端配置**：需要修改 zoo.cfg 添加 `extendedTypesEnabled=true` 并重启

### 完整代码清单

代码见上述示例，完整的 Maven 项目结构与测试代码见 `column/code/chapter04/`。

### 测试验证

```bash
# 验证持久节点持久化
./bin/zkCli.sh -server 127.0.0.1:2181
ls /
# 看到 /persistent 还在（程序结束后应已清理）

# 手动创建临时节点验证
create -e /test-ephemeral "test"
quit
# 重新连接
./bin/zkCli.sh -server 127.0.0.1:2181
ls /
# 看不到 /test-ephemeral（已自动删除）
```

---

## 4. 项目总结

### 优点 & 缺点

| 节点类型 | 优点 | 缺点 |
|---------|------|------|
| 持久节点 | 数据持久化，不随客户端状态变化 | 需手动清理，可能产生僵尸数据 |
| 持久顺序节点 | 天然全局唯一编号 | 序号可能用尽（理论上限 2^32） |
| 临时节点 | 自动清理，避免僵尸节点 | 不能有子节点 |
| 临时顺序节点 | 自动清理 + 唯一编号 | 不能有子节点 |
| 容器节点 | 自动清理空目录 | 非实时删除 |
| TTL 节点 | 超时自动清理 | 需服务端特定配置，精度有限 |

### 适用场景

- **持久节点**：配置文件、静态元数据、命名空间
- **持久顺序节点**：全局 ID 生成器、操作日志顺序记录
- **临时节点**：服务注册/发现、Master 选举、心跳检测
- **临时顺序节点**：公平分布式锁、顺序队列、Leader 选举
- **容器节点**：动态目录（父节点仅做路径占位）
- **TTL 节点**：临时授权 Token、验证码、限流计数

**不适用场景**：
- 临时节点不适合存储需要持久化的数据
- TTL 节点不适合精确到秒级的定时任务（精度受 tickTime 限制）

### 注意事项

- 临时节点不能创建子节点，这是 ZooKeeper 的硬性限制
- TTL 节点在 3.5+ 版本中默认关闭，需显式开启 `extendedTypesEnabled=true`
- 顺序节点的计数器是每个路径独立的吗？不是，是全局的（所有顺序节点共用计数器）

### 常见踩坑经验

**故障 1：临时节点未自动删除**

现象：客户端断连后，服务端对应的临时节点没有及时删除。

根因：服务端 `Session Timeout` 配置过大（如 60s），客户端断连后 ZooKeeper 还在等待会话超时才会清理临时节点。解决方案是适当减小 `tickTime` 和 `maxSessionTimeout`。

**故障 2：顺序节点序号回跳**

现象：顺序节点创建后，序号在某次 Leader 选举后出现了回跳。

根因：ZooKeeper 3.4.x 及以下版本，顺序节点计数器存储在内存中且持久化在事务日志中。若加载了旧的快照，序号可能回跳。3.5+ 已修复。

**故障 3：容器节点堆积**

现象：大量容器节点在子节点删除后没有被清理，占据内存。

根因：容器节点的清理是惰性的——ZooKeeper 不会主动扫描所有容器节点。只有当容器节点被访问（如 `getChildren`）时触发清理检查。在大量动态目录场景下，可主动触发 `ls /parent` 来触发清理。

### 思考题

1. 假设你用一个临时顺序节点做分布式锁，客户端创建了 `/lock/0000000003`。此时客户端 A 发现 `/lock/0000000001` 和 `/lock/0000000002` 都存在，它应该 Watcher 哪个节点？为什么？
2. 容器节点和持久节点在"没有子节点时自动删除"这个行为上有什么区别？如果你需要一个路径占位节点，但不确认自己是否真的需要在上面存数据，用哪种节点更合适？

### 推广计划提示

- **开发**：本章的 ZNode 类型知识是后续实现分布式原语的基石，建议亲手运行示例代码观察每种类型的行为
- **运维**：关注临时节点的数量，异常的临时节点堆积可能意味着客户端连接有问题
- **测试**：测试分布式锁时，可以手动模拟客户端断开连接，验证锁的自动释放
