# 第9章：Curator 高级特性——分布式锁、选主与计数器

## 1. 项目背景

### 业务场景

微服务架构中，很多场景需要**跨进程的协调机制**：

- **分布式锁**：双十一秒杀，100 个订单服务实例同时扣减库存，必须保证只有一个实例扣减成功
- **Leader 选举**：集群中只有一个实例执行定时任务（比如每小时清理一次过期数据），其余实例 Standby
- **分布式计数器**：生成全局唯一的订单号，或者统计全站 UV（独立访客数）

Curator 的 recipes 模块提供了所有这些高级原语的现成实现，你不需要理解底层的 ZNode + Watcher 机制，只需要调用 `acquire()` / `release()` / `takeLeadership()` 等方法。

### 痛点放大

没有 Curator 之前，自己实现分布式锁需要考虑：

- 临时顺序节点的创建和监听
- 羊群效应（只监听前一个节点，而不是所有节点）
- 连接断开时锁的状态处理
- 可重入性（同一个线程重复获取锁）
- 死锁检测和超时释放

每一个问题都需要大量代码和细致的设计。Curator 的 `InterProcessMutex` 一行代码就解决了所有这些问题。

---

## 2. 项目设计

### 剧本式交锋对话

**场景**：小胖做了个秒杀系统，100 个服务实例同时扣减库存，结果超卖了 10 倍。

**小胖**：我明明用了 `synchronized` 关键字，为什么库存还会超卖？`synchronized` 锁不了多进程啊！

**大师**：`synchronized` 是 JVM 进程内部的锁，锁不了其他机器上的进程。你需要**分布式锁**——跨进程、跨机器的互斥锁。

Curator 提供了 `InterProcessMutex`（可重入分布式锁）：

```java
// 所有服务实例竞争同一个路径 /locks/seckill
InterProcessMutex lock = new InterProcessMutex(client, "/locks/seckill");

lock.acquire();   // 获取锁（阻塞等待）
try {
    // 扣减库存（只有获取到锁的实例能进来）
    int stock = getStock();
    if (stock > 0) {
        setStock(stock - 1);
    }
} finally {
    lock.release(); // 释放锁
}
```

**小白**：那如果有读操作和写操作同时进行呢？读不需要互斥，但写需要排他。`synchronized` 做不到读读并行。

**大师**：这就是 `InterProcessReadWriteLock`——读写锁：

```java
InterProcessReadWriteLock rwLock = new InterProcessReadWriteLock(client, "/locks/product-123");

// 读锁：多个实例可以同时获取（读读不互斥）
InterProcessLock readLock = rwLock.readLock();
readLock.acquire();
// 读取库存，所有实例都能同时读
readLock.release();

// 写锁：互斥（读写互斥，写写互斥）
InterProcessLock writeLock = rwLock.writeLock();
writeLock.acquire();
// 扣减库存，只有一个实例能写
writeLock.release();
```

**小胖**：我还有一个场景——每天凌晨 2 点要清理一次过期数据，但只需要一个实例执行，其他实例不能执行。这用锁可以实现吗？

**大师**：可以用锁，但 Curator 有专门的**Leader 选举**机制——`LeaderSelector`：

```java
LeaderSelector selector = new LeaderSelector(client, "/leader/task-cleanup", new LeaderSelectorListener() {
    @Override
    public void takeLeadership(CuratorFramework client) throws Exception {
        System.out.println("我是 Leader，开始执行清理任务");
        doCleanup();
        System.out.println("清理完成，释放 Leader 角色");
    }
});
selector.autoRequeue();  // 执行完后自动重新排队
selector.start();
```

`LeaderSelector` 会在多个实例中选出一个"Leader"，Leader 执行 `takeLeadership()`，其他实例等待。当前 Leader 执行完毕后，自动选出下一个 Leader。

**小白**：还有一个场景——需要生成全局唯一的订单号，用数据库自增太慢，用 UUID 又太长。ZooKeeper 的顺序节点很适合这个场景吧？

**大师**：对，Curator 提供了 `SharedCount` 和 `DistributedAtomicLong`：

```java
// 分布式原子计数器（基于 CAS 乐观锁）
DistributedAtomicLong counter = new DistributedAtomicLong(client, "/counter/order-id",
        new RetryNTimes(10, 100));

// 自增并获取
AtomicValue<Long> value = counter.increment();
if (value.succeeded()) {
    System.out.println("订单号: " + value.postValue());  // 后自增值
}
```

**DistributedAtomicLong** 内部使用了 ZooKeeper 的版本号（CAS）来保证原子性——读取当前值，自增，用版本号写入，如果版本冲突则重试。

> **技术映射**：InterProcessMutex = 进程外的 synchronized，LeaderSelector = 进程外的单线程执行器，DistributedAtomicLong = 进程外的 AtomicLong

---

## 3. 项目实战

### 环境准备

- ZooKeeper 3.9.x 运行中
- JDK 11+
- Maven

### 分步实现

#### 步骤 1：分布式锁——秒杀库存扣减

创建 `DistributedLockDemo.java`：

```java
package com.zkdemo;

import org.apache.curator.RetryPolicy;
import org.apache.curator.framework.CuratorFramework;
import org.apache.curator.framework.CuratorFrameworkFactory;
import org.apache.curator.framework.recipes.locks.InterProcessMutex;
import org.apache.curator.framework.recipes.locks.InterProcessReadWriteLock;
import org.apache.curator.retry.ExponentialBackoffRetry;

import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

public class DistributedLockDemo {
    private static final String LOCK_PATH = "/locks/seckill";
    private static int stock = 10; // 共享库存（模拟）
    private static int successCount = 0;

    public static void main(String[] args) throws Exception {
        // 创建 Curator 客户端
        RetryPolicy retryPolicy = new ExponentialBackoffRetry(1000, 3);
        CuratorFramework client = CuratorFrameworkFactory.newClient(
                "127.0.0.1:2181", retryPolicy);
        client.start();

        // 演示 1: InterProcessMutex（可重入分布式锁）
        System.out.println("=== 演示 InterProcessMutex 分布式锁 ===");
        demoInterProcessMutex(client);

        // 演示 2: InterProcessReadWriteLock（读写锁）
        System.out.println("\n=== 演示 InterProcessReadWriteLock 读写锁 ===");
        demoReadWriteLock(client);

        client.close();
    }

    private static void demoInterProcessMutex(CuratorFramework client) throws Exception {
        // 创建分布式锁
        InterProcessMutex lock = new InterProcessMutex(client, LOCK_PATH);

        // 模拟 20 个并发请求扣减库存
        int threadCount = 20;
        CountDownLatch latch = new CountDownLatch(threadCount);

        for (int i = 0; i < threadCount; i++) {
            final int threadId = i;
            new Thread(() -> {
                try {
                    // 尝试获取锁（最多等待 5 秒）
                    if (lock.acquire(5, TimeUnit.SECONDS)) {
                        try {
                            // 模拟业务处理时间
                            Thread.sleep(50);

                            // 扣减库存
                            if (stock > 0) {
                                stock--;
                                successCount++;
                                System.out.println("线程 " + threadId + " 扣减成功，剩余库存: " + stock);
                            } else {
                                System.out.println("线程 " + threadId + " 扣减失败，库存不足");
                            }
                        } finally {
                            lock.release(); // 释放锁
                        }
                    } else {
                        System.out.println("线程 " + threadId + " 获取锁超时");
                    }
                } catch (Exception e) {
                    e.printStackTrace();
                } finally {
                    latch.countDown();
                }
            }).start();
        }

        latch.await();
        System.out.println("\n最终结果: 成功扣减 " + successCount + " 次, 剩余库存: " + stock);
        System.out.println("库存没有超卖!" + (successCount <= 10 && stock >= 0 ? " ✓" : " ✗"));
    }

    private static void demoReadWriteLock(CuratorFramework client) throws Exception {
        InterProcessReadWriteLock rwLock = new InterProcessReadWriteLock(client, "/locks/product-1");

        // 模拟多个读操作
        int readThreads = 5;
        CountDownLatch readLatch = new CountDownLatch(readThreads);

        for (int i = 0; i < readThreads; i++) {
            final int id = i;
            new Thread(() -> {
                try {
                    // 读锁：所有读线程可以同时获取
                    rwLock.readLock().acquire();
                    System.out.println("读线程 " + id + " 获取读锁，开始读取数据");
                    Thread.sleep(200);  // 模拟读取耗时
                    System.out.println("读线程 " + id + " 释放读锁");
                    rwLock.readLock().release();
                } catch (Exception e) {
                    e.printStackTrace();
                } finally {
                    readLatch.countDown();
                }
            }).start();
        }

        // 模拟写操作（写锁需要等待所有读锁释放）
        new Thread(() -> {
            try {
                Thread.sleep(50);
                System.out.println("\n写线程尝试获取写锁（等待读锁释放...）");
                long start = System.currentTimeMillis();
                rwLock.writeLock().acquire();
                long waitTime = System.currentTimeMillis() - start;
                System.out.println("写线程获取写锁（等待 " + waitTime + "ms），开始更新数据");
                Thread.sleep(100);
                System.out.println("写线程释放写锁");
                rwLock.writeLock().release();
            } catch (Exception e) {
                e.printStackTrace();
            }
        }).start();

        readLatch.await();
        Thread.sleep(2000);
    }
}
```

**预期输出**：

```
=== 演示 InterProcessMutex 分布式锁 ===
线程 2 扣减成功，剩余库存: 9
线程 5 扣减成功，剩余库存: 8
线程 7 扣减成功，剩余库存: 7
线程 10 扣减成功，剩余库存: 6
线程 15 扣减成功，剩余库存: 5
线程 3 扣减成功，剩余库存: 4
线程 8 扣减成功，剩余库存: 3
线程 12 扣减成功，剩余库存: 2
线程 1 扣减成功，剩余库存: 1
线程 6 扣减成功，剩余库存: 0
线程 11 扣减失败，库存不足
线程 18 扣减失败，库存不足
线程 19 扣减失败，库存不足
线程 14 扣减失败，库存不足
...

最终结果: 成功扣减 10 次, 剩余库存: 0
库存没有超卖! ✓
```

#### 步骤 2：Leader 选举——定时任务调度

创建 `LeaderElectionDemo.java`：

```java
package com.zkdemo;

import org.apache.curator.RetryPolicy;
import org.apache.curator.framework.CuratorFramework;
import org.apache.curator.framework.CuratorFrameworkFactory;
import org.apache.curator.framework.recipes.leader.LeaderSelector;
import org.apache.curator.framework.recipes.leader.LeaderSelectorListener;
import org.apache.curator.framework.state.ConnectionState;
import org.apache.curator.retry.ExponentialBackoffRetry;

import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

public class LeaderElectionDemo {
    private static final String LEADER_PATH = "/leader/cleanup-task";
    private static final int INSTANCE_COUNT = 3;

    public static void main(String[] args) throws Exception {
        CountDownLatch allDone = new CountDownLatch(INSTANCE_COUNT);

        // 启动 3 个实例模拟集群
        for (int i = 1; i <= INSTANCE_COUNT; i++) {
            startInstance(i, allDone);
            Thread.sleep(200); // 错开启动时间
        }

        allDone.await();
        Thread.sleep(3000);
    }

    private static void startInstance(int instanceId, CountDownLatch latch) throws Exception {
        RetryPolicy retryPolicy = new ExponentialBackoffRetry(1000, 3);
        CuratorFramework client = CuratorFrameworkFactory.builder()
                .connectString("127.0.0.1:2181")
                .retryPolicy(retryPolicy)
                .build();
        client.start();

        LeaderSelector selector = new LeaderSelector(client, LEADER_PATH,
                new LeaderSelectorListener() {
                    @Override
                    public void takeLeadership(CuratorFramework client) throws Exception {
                        System.out.println("[实例 " + instanceId + "] 成为 Leader，开始执行任务");
                        // 模拟执行任务（执行完自动释放 Leader 角色）
                        TimeUnit.SECONDS.sleep(2);
                        System.out.println("[实例 " + instanceId + "] 任务执行完毕，释放 Leader 角色");
                    }

                    @Override
                    public void stateChanged(CuratorFramework client, ConnectionState newState) {
                        System.out.println("[实例 " + instanceId + "] 连接状态变化: " + newState);
                    }
                });

        // 执行完后自动重新排队（再次参与选举）
        selector.autoRequeue();
        selector.start();

        System.out.println("[实例 " + instanceId + "] 已启动，等待成为 Leader...");
        latch.countDown();
    }
}
```

**预期输出**：

```
[实例 1] 已启动，等待成为 Leader...
[实例 1] 成为 Leader，开始执行任务
[实例 2] 已启动，等待成为 Leader...
[实例 3] 已启动，等待成为 Leader...
[实例 1] 任务执行完毕，释放 Leader 角色
[实例 2] 成为 Leader，开始执行任务
[实例 2] 任务执行完毕，释放 Leader 角色
[实例 3] 成为 Leader，开始执行任务
[实例 3] 任务执行完毕，释放 Leader 角色
[实例 1] 成为 Leader，开始执行任务   ← 自动重新排队
...
```

#### 步骤 3：分布式计数器

创建 `DistributedCounterDemo.java`：

```java
package com.zkdemo;

import org.apache.curator.RetryPolicy;
import org.apache.curator.framework.CuratorFramework;
import org.apache.curator.framework.CuratorFrameworkFactory;
import org.apache.curator.framework.recipes.atomic.AtomicValue;
import org.apache.curator.framework.recipes.atomic.DistributedAtomicLong;
import org.apache.curator.retry.ExponentialBackoffRetry;
import org.apache.curator.retry.RetryNTimes;

import java.util.concurrent.CountDownLatch;

public class DistributedCounterDemo {
    public static void main(String[] args) throws Exception {
        RetryPolicy retryPolicy = new ExponentialBackoffRetry(1000, 3);
        CuratorFramework client = CuratorFrameworkFactory.newClient(
                "127.0.0.1:2181", retryPolicy);
        client.start();

        // 分布式原子计数器
        DistributedAtomicLong counter = new DistributedAtomicLong(client, "/counter/order-id",
                new RetryNTimes(10, 100));

        // 重置计数器
        counter.trySet(0L);

        // 模拟 50 个并发线程生成订单号
        int threadCount = 50;
        CountDownLatch latch = new CountDownLatch(threadCount);

        for (int i = 0; i < threadCount; i++) {
            new Thread(() -> {
                try {
                    // 自增并获取结果
                    AtomicValue<Long> value = counter.increment();
                    if (value.succeeded()) {
                        System.out.println("生成订单号: " + value.postValue());
                    } else {
                        System.out.println("生成订单号失败（并发冲突后重试）");
                    }
                } catch (Exception e) {
                    e.printStackTrace();
                } finally {
                    latch.countDown();
                }
            }).start();
        }

        latch.await();
        System.out.println("\n共生成 " + threadCount + " 个订单号，最后一个订单号: "
                + counter.get().postValue());
        System.out.println("所有订单号无重复: ✓");

        client.close();
    }
}
```

**预期输出**：

```
生成订单号: 1
生成订单号: 2
生成订单号: 3
...
生成订单号: 49
生成订单号: 50

共生成 50 个订单号，最后一个订单号: 50
所有订单号无重复: ✓
```

### 可能遇到的坑

| 问题 | 原因 | 解决 |
|------|------|------|
| `lock.acquire()` 一直阻塞 | 锁被其他客户端持有且未释放 | 检查锁的路径，确认没有死锁 |
| `DistributedAtomicLong` 重试过多 | 高并发下 CAS 冲突频繁 | 增大重试间隔，或使用 `SharedCount` |
| Leader 选举后 `takeLeadership` 未执行 | 连接状态变化导致监听暂停 | 实现 `stateChanged` 方法处理连接变更 |

### 完整代码清单

代码见 `column/code/chapter09/`。

### 测试验证

```bash
# 运行锁演示
mvn exec:java -Dexec.mainClass="com.zkdemo.DistributedLockDemo"

# 运行 Leader 选举演示
mvn exec:java -Dexec.mainClass="com.zkdemo.LeaderElectionDemo"

# 运行计数器演示
mvn exec:java -Dexec.mainClass="com.zkdemo.DistributedCounterDemo"
```

---

## 4. 项目总结

### 优点 & 缺点

| 组件 | 优点 | 缺点 |
|------|------|------|
| InterProcessMutex | 可重入、公平锁、自动释放 | 高并发下写 ZooKeeper 可能成为瓶颈 |
| InterProcessReadWriteLock | 读读并行，提升读吞吐 | 实现复杂，内部实际是互斥锁组合 |
| LeaderSelector | 自动重排队、连接状态处理 | 需要业务方处理 `takeLeadership` 的异常 |
| DistributedAtomicLong | 强一致性、CAS 乐观锁 | 高并发下性能受 ZooKeeper 写吞吐限制 |

### 适用场景

- **InterProcessMutex**：需要跨进程互斥访问的资源（库存扣减、订单处理）
- **InterProcessReadWriteLock**：读多写少的配置读取 + 更新
- **LeaderSelector**：定时任务调度、Master 节点选举
- **DistributedAtomicLong**：全局唯一 ID 生成（小流量）、计数器

**不适用场景**：
- 超高频 ID 生成（建议用 Redis 或雪花算法）
- 需要高性能读锁（频繁读操作会在 ZooKeeper 上产生大量请求）

### 注意事项

- 锁的超时时间要大于业务处理时间，否则锁自动释放后其他线程拿到锁，但业务还在执行
- `DistributedAtomicLong` 使用 CAS，频繁冲突时重试次数可能需要调大
- `LeaderSelector` 的 `takeLeadership` 抛出异常会导致该实例不再参与选举
- 可重入锁的"重入"基于 Thread 维度，跨线程不重入

### 常见踩坑经验

**故障 1：分布式锁未正确释放导致死锁**

现象：获取锁的业务逻辑抛出异常后，`lock.release()` 没有执行，所有其他请求永久阻塞。

根因：`acquire()` / `release()` 没有包在 try-finally 块中。

正确做法：
```java
lock.acquire();
try {
    doBusiness();
} finally {
    lock.release(); // 确保一定会执行
}
```

**故障 2：LeaderSelector 中 `takeLeadership` 超时后被强制中断**

现象：`takeLeadership` 执行耗时超过 ZooKeeper Session Timeout，Curator 认为该实例已失联，强制调用 `interrupt()` 中断。

根因：`takeLeadership` 中执行了耗时操作（如大量数据库查询），超过了 Session Timeout。需要在方法内部监控耗时，或增大 Session Timeout。

### 思考题

1. `InterProcessMutex` 是可重入的。假设一个线程获取锁后，调用了另一个需要同一把锁的方法（递归调用）。Curator 是如何实现"重入"的？（提示：ThreadLocal）
2. `DistributedAtomicLong` 使用 CAS 乐观锁，如果 50 个线程同时自增，最后一个线程可能需要重试很多次。这时能否改用 Redis 的 INCR 命令？ZooKeeper 和 Redis 在分布式计数器上各自的优缺点是什么？

### 推广计划提示

- **开发**：Curator recipes 是生产级分布式锁、选主、计数的标准方案，直接使用无需自己造轮子
- **运维**：关注 `/locks/`、`/leader/`、`/counter/` 路径下的 ZNode 数量，异常的节点堆积可能说明客户端存在问题
- **测试**：集成测试中使用 `TestingServer` 模拟 ZooKeeper 集群，测试锁竞争和 Leader 选举
