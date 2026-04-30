# 第23章：分布式屏障与计数器——Barrier、CountDownLatch 与 Atomic

## 1. 项目背景

### 业务场景

分布式系统中，经常需要多个节点在某个时刻**同步**或**汇聚**：

- **屏障（Barrier）**：分布式 MapReduce 中，需要在所有 Mapper 完成之后才能启动 Reducer
- **CountDownLatch**：主节点等待所有 Worker 就绪后再统一开始
- **分布式计数器**：多个节点同时统计访客数，最后合并结果

这些场景的共同点是：**多个节点需要对状态达成一致，并在此状态上触发下一步动作**。

### 痛点放大

不用分布式协调解决同步问题，可能用这些方案：

- **数据库轮询**：每个 Worker 完成时更新数据库 status 字段，Master 定时轮询。延迟高、浪费资源
- **固定时间等待**："等 30 秒，认为所有 Worker 都完成了"。不安全（可能有节点慢）、浪费时间
- **消息队列**：用 MQ 做任务完成后通知。但判断"所有完成"仍需要计数，MQ 不擅长聚合计数

---

## 2. 项目设计

### 剧本式交锋对话

**场景**：小胖在实现一个分布式爬虫，需要所有爬虫节点完成抓取后，统一进行数据清洗。

**小胖**：我的爬虫有 10 个 Worker 同时抓取网页，抓完后我需要统一做数据清洗。现在我用一个数据库表做标记——每个 Worker 完成时在 status 表里写一行。Master 每个 5 秒查一次表，10 行都出现了就启动清洗。

**大师**：这个方案有几个问题：
1. Worker 崩溃了怎么办？状态标记不会自动清除
2. 轮询有延迟，Worker 早完成了但 Master 还在等
3. 数据库写压力

**小白**：ZooKeeper 不是可以解决这个问题吗？用 Watcher 做通知？

**大师**：对。这就是**分布式屏障（Barrier）**。

```
分布式 Barrier 的原理——基于 ZNode + Watcher 计数：

1. 创建屏障路径 /barrier/my-job
2. 每个 Worker 启动时在 /barrier/my-job 下创建临时节点
3. Master 监听 /barrier/my-job 的子节点数量
4. 当子节点数量达到预定值时 → 屏障条件满足 → 通知 Master

/barrier/my-job/
├── worker-1 (临时节点)
├── worker-2 (临时节点)
├── ...
└── worker-10 (临时节点)

当子节点数 = 10 → 所有 Worker 就绪 → 开始任务
```

**小胖**：那 DistributedAtomicLong 呢？怎么保证多个实例同时递增不会冲突？

**大师**：DistributedAtomicLong 使用 CAS（Compare-And-Swap）乐观锁：

```
初始值：0

客户端 A：读取当前值 = 0，新值 = 1
客户端 B：读取当前值 = 0，新值 = 1（同时）

客户端 A 写入：setData(/counter, "1", version=0) → 成功
客户端 B 写入：setData(/counter, "1", version=0) → 失败（BadVersion）
客户端 B 重试：读取当前值 = 1，新值 = 2，version=1 → 成功
```

**小胖**：那数据量大的时候，CAS 冲突率很高，性能会不会差？

**大师**：是的，密集的 CAS 写入是分布式计数器的性能瓶颈。Curator 的 `DistributedAtomicLong` 内置了重试机制，但大量并发写入时（>1000 ops/s）ZooKeeper 单节点写性能会成瓶颈。

这时候可以考虑**分段计数器**：

```
分段计数器：

1. 创建 N 个分段 /counter/stats/seg-0 ~ seg-N
2. 每个实例根据 hash(实例ID) % N 选择分段
3. 写入自己的分段
4. 读取时聚合所有分段的值

这样将写入压力分散到 N 个 ZNode，ZooKeeper 写性能扩展 N 倍
```

> **技术映射**：Barrier = 马拉松起点线的发令枪（等所有人就位才开枪），CountDownLatch = 倒数计时器（从 10 数到 0），DistributedAtomicLong = 接力棒计数器（一个人跑完，数字 +1，交给下一个人）

---

## 3. 项目实战

### 环境准备

- ZooKeeper 3.9.x 运行中
- JDK 11+
- Maven

### 分步实现

#### 步骤 1：分布式屏障实现

创建 `DistributedBarrier.java`：

```java
package com.zkdemo.coordination;

import org.apache.zookeeper.*;
import org.apache.zookeeper.data.Stat;

import java.util.List;
import java.util.concurrent.CountDownLatch;

public class DistributedBarrier {
    private static final String BARRIER_ROOT = "/barriers";
    private final ZooKeeper zk;
    private final String barrierPath;
    private final int threshold; // 需要多少个参与者

    public DistributedBarrier(ZooKeeper zk, String name, int threshold) {
        this.zk = zk;
        this.barrierPath = BARRIER_ROOT + "/" + name;
        this.threshold = threshold;
    }

    // 初始化屏障
    public void init() throws Exception {
        Stat stat = zk.exists(BARRIER_ROOT, false);
        if (stat == null) {
            zk.create(BARRIER_ROOT, "".getBytes(),
                    ZooDefs.Ids.OPEN_ACL_UNSAFE, CreateMode.PERSISTENT);
        }
        stat = zk.exists(barrierPath, false);
        if (stat == null) {
            zk.create(barrierPath, "".getBytes(),
                    ZooDefs.Ids.OPEN_ACL_UNSAFE, CreateMode.PERSISTENT);
        }
    }

    // 参与者加入屏障（创建临时节点表示就绪）
    public void join(String participantId) throws Exception {
        zk.create(barrierPath + "/" + participantId,
                "ready".getBytes(),
                ZooDefs.Ids.OPEN_ACL_UNSAFE,
                CreateMode.EPHEMERAL);
        System.out.println("参与者 " + participantId + " 已就绪");
    }

    // 等待屏障（阻塞，直到参与者达到阈值）
    public void await() throws Exception {
        while (true) {
            List<String> children = zk.getChildren(barrierPath, false);
            int readyCount = children.size();

            if (readyCount >= threshold) {
                System.out.println("屏障条件满足! " + readyCount
                        + "/" + threshold + " 参与者已就绪");
                return;
            }

            System.out.println("等待屏障... 当前: " + readyCount
                    + "/" + threshold);

            // 注册子节点变化 Watcher
            CountDownLatch latch = new CountDownLatch(1);
            Stat stat = zk.exists(barrierPath, event -> {
                if (event.getType() == Watcher.Event.EventType.NodeChildrenChanged) {
                    latch.countDown();
                }
            });

            if (stat != null) {
                latch.await();
            }
        }
    }

    // 离开屏障（清理临时节点）
    public void leave(String participantId) throws Exception {
        String path = barrierPath + "/" + participantId;
        Stat stat = zk.exists(path, false);
        if (stat != null) {
            zk.delete(path, -1);
        }
    }
}
```

#### 步骤 2：分布式 CountDownLatch

创建 `DistributedCountDownLatch.java`：

```java
package com.zkdemo.coordination;

import org.apache.zookeeper.*;
import org.apache.zookeeper.data.Stat;

import java.util.concurrent.CountDownLatch;

/**
 * 分布式 CountDownLatch——基于子节点数量
 */
public class DistributedCountDownLatch {
    private static final String LATCH_ROOT = "/latches";
    private final ZooKeeper zk;
    private final String latchPath;
    private final int count;

    public DistributedCountDownLatch(ZooKeeper zk, String name, int count) {
        this.zk = zk;
        this.latchPath = LATCH_ROOT + "/" + name;
        this.count = count;
    }

    public void init() throws Exception {
        Stat stat = zk.exists(LATCH_ROOT, false);
        if (stat == null) {
            zk.create(LATCH_ROOT, "".getBytes(),
                    ZooDefs.Ids.OPEN_ACL_UNSAFE, CreateMode.PERSISTENT);
        }
        stat = zk.exists(latchPath, false);
        if (stat == null) {
            zk.create(latchPath, "".getBytes(),
                    ZooDefs.Ids.OPEN_ACL_UNSAFE, CreateMode.PERSISTENT);
        }

        // 初始化 count 个子节点
        for (int i = 0; i < count; i++) {
            zk.create(latchPath + "/node-", i + "".getBytes(),
                    ZooDefs.Ids.OPEN_ACL_UNSAFE, CreateMode.PERSISTENT_SEQUENTIAL);
        }
    }

    // 倒计数（删除一个子节点）
    public void countDown() throws Exception {
        // 获取一个子节点并删除
        var children = zk.getChildren(latchPath, false);
        if (!children.isEmpty()) {
            zk.delete(latchPath + "/" + children.get(0), -1);
            System.out.println("CountDown! 剩余: " + (children.size() - 1));
        }
    }

    // 等待倒计数归零
    public void await() throws Exception {
        while (true) {
            var children = zk.getChildren(latchPath, false);
            if (children.isEmpty()) {
                System.out.println("CountDownLatch 已归零!");
                return;
            }

            System.out.println("等待 CountDownLatch... 剩余: " + children.size());

            CountDownLatch latch = new CountDownLatch(1);
            Stat stat = zk.exists(latchPath, event -> {
                if (event.getType() == Watcher.Event.EventType.NodeChildrenChanged) {
                    latch.countDown();
                }
            });

            if (stat != null) {
                latch.await();
            }
        }
    }
}
```

#### 步骤 3：分布式计数器实现

创建 `DistributedAtomicCounter.java`：

```java
package com.zkdemo.coordination;

import org.apache.zookeeper.*;
import org.apache.zookeeper.data.Stat;

public class DistributedAtomicCounter {
    private static final String COUNTER_ROOT = "/counters";
    private final ZooKeeper zk;
    private final String counterPath;
    private static final int MAX_RETRIES = 10;

    public DistributedAtomicCounter(ZooKeeper zk, String name) {
        this.zk = zk;
        this.counterPath = COUNTER_ROOT + "/" + name;
    }

    public void init() throws Exception {
        Stat stat = zk.exists(COUNTER_ROOT, false);
        if (stat == null) {
            zk.create(COUNTER_ROOT, "".getBytes(),
                    ZooDefs.Ids.OPEN_ACL_UNSAFE, CreateMode.PERSISTENT);
        }
        stat = zk.exists(counterPath, false);
        if (stat == null) {
            zk.create(counterPath, "0".getBytes(),
                    ZooDefs.Ids.OPEN_ACL_UNSAFE, CreateMode.PERSISTENT);
        }
    }

    // 递增（CAS 乐观锁）
    public long increment() throws Exception {
        for (int i = 0; i < MAX_RETRIES; i++) {
            Stat stat = new Stat();
            byte[] data = zk.getData(counterPath, false, stat);
            long currentValue = Long.parseLong(new String(data));
            long newValue = currentValue + 1;

            try {
                zk.setData(counterPath, String.valueOf(newValue).getBytes(), stat.getVersion());
                return newValue;
            } catch (KeeperException.BadVersionException e) {
                // CAS 冲突，重试
                if (i == MAX_RETRIES - 1) throw e;
                Thread.sleep(10);
            }
        }
        throw new RuntimeException("Counter increment failed after " + MAX_RETRIES + " retries");
    }

    // 递减
    public long decrement() throws Exception {
        for (int i = 0; i < MAX_RETRIES; i++) {
            Stat stat = new Stat();
            byte[] data = zk.getData(counterPath, false, stat);
            long currentValue = Long.parseLong(new String(data));
            long newValue = currentValue - 1;

            try {
                zk.setData(counterPath, String.valueOf(newValue).getBytes(), stat.getVersion());
                return newValue;
            } catch (KeeperException.BadVersionException e) {
                if (i == MAX_RETRIES - 1) throw e;
            }
        }
        throw new RuntimeException("Counter decrement failed after " + MAX_RETRIES + " retries");
    }

    // 读取当前值
    public long get() throws Exception {
        byte[] data = zk.getData(counterPath, false, null);
        return Long.parseLong(new String(data));
    }
}
```

#### 步骤 4：分段计数器性能对比

创建 `CounterBenchmark.java`：

```java
package com.zkdemo.coordination;

import org.apache.zookeeper.Watcher;
import org.apache.zookeeper.ZooKeeper;

import java.util.concurrent.CountDownLatch;
import java.util.concurrent.atomic.AtomicInteger;

public class CounterBenchmark {
    public static void main(String[] args) throws Exception {
        CountDownLatch latch = new CountDownLatch(1);
        ZooKeeper zk = new ZooKeeper("127.0.0.1:2181", 5000, event -> {
            if (event.getState() == Watcher.Event.KeeperState.SyncConnected) {
                latch.countDown();
            }
        });
        latch.await();

        int threadCount = 20;
        int incrementsPerThread = 100;

        // 单个计数器
        System.out.println("=== 单个计数器性能测试 ===");
        DistributedAtomicCounter singleCounter = new DistributedAtomicCounter(zk, "single");
        singleCounter.init();

        long start = System.currentTimeMillis();
        runConcurrentIncrements(threadCount, incrementsPerThread, singleCounter);
        long elapsed = System.currentTimeMillis() - start;
        System.out.printf("单计数器: %d 次递增, 耗时 %d ms, 吞吐 %d ops/s%n%n",
                threadCount * incrementsPerThread, elapsed,
                (threadCount * incrementsPerThread * 1000L) / elapsed);

        // Curator DistributedAtomicLong
        System.out.println("=== Curator DistributedAtomicLong ===");
        org.apache.curator.framework.CuratorFramework curatorClient =
                org.apache.curator.framework.CuratorFrameworkFactory.newClient(
                        "127.0.0.1:2181",
                        new org.apache.curator.retry.ExponentialBackoffRetry(1000, 3));
        curatorClient.start();

        org.apache.curator.framework.recipes.atomic.DistributedAtomicLong curatorCounter =
                new org.apache.curator.framework.recipes.atomic.DistributedAtomicLong(
                        curatorClient, "/curator-counter",
                        new org.apache.curator.retry.RetryNTimes(10, 50));

        start = System.currentTimeMillis();
        AtomicInteger failures = new AtomicInteger(0);
        CountDownLatch doneLatch = new CountDownLatch(threadCount);

        for (int i = 0; i < threadCount; i++) {
            new Thread(() -> {
                for (int j = 0; j < incrementsPerThread; j++) {
                    try {
                        var value = curatorCounter.increment();
                        if (!value.succeeded()) {
                            failures.incrementAndGet();
                        }
                    } catch (Exception e) {
                        e.printStackTrace();
                    }
                }
                doneLatch.countDown();
            }).start();
        }

        doneLatch.await();
        elapsed = System.currentTimeMillis() - start;
        System.out.printf("Curator 计数器: %d 次递增, 失败 %d, 耗时 %d ms, 吞吐 %d ops/s%n",
                threadCount * incrementsPerThread, failures.get(), elapsed,
                (threadCount * incrementsPerThread * 1000L) / elapsed);

        curatorClient.close();
        zk.close();
    }

    static void runConcurrentIncrements(int threads, int increments,
                                         DistributedAtomicCounter counter) throws Exception {
        CountDownLatch doneLatch = new CountDownLatch(threads);
        for (int i = 0; i < threads; i++) {
            new Thread(() -> {
                try {
                    for (int j = 0; j < increments; j++) {
                        counter.increment();
                    }
                } catch (Exception e) {
                    e.printStackTrace();
                } finally {
                    doneLatch.countDown();
                }
            }).start();
        }
        doneLatch.await();
    }
}
```

#### 步骤 5：MapReduce 协调器实战

创建 `MapReduceCoordinator.java`：

```java
package com.zkdemo.coordination;

import org.apache.zookeeper.*;

import java.util.concurrent.CountDownLatch;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * 基于 ZooKeeper Barrier 的 MapReduce 协调器
 */
public class MapReduceCoordinator {
    private static final String BARRIER_NAME = "word-count-job";
    private static final int WORKER_COUNT = 5;
    private final ZooKeeper zk;

    public MapReduceCoordinator(ZooKeeper zk) {
        this.zk = zk;
    }

    // Map 阶段完成后的 Barrier
    public void mapPhase() throws Exception {
        DistributedBarrier barrier = new DistributedBarrier(zk, BARRIER_NAME, WORKER_COUNT);
        barrier.init();

        System.out.println("=== Map 阶段开始 ===");
        System.out.println("等待 " + WORKER_COUNT + " 个 Worker 完成 Map...");

        // Worker 模拟
        for (int i = 1; i <= WORKER_COUNT; i++) {
            int workerId = i;
            new Thread(() -> {
                try {
                    // 模拟 Map 任务
                    System.out.println("  Worker " + workerId + " 开始 Map...");
                    Thread.sleep((long) (Math.random() * 2000 + 1000));
                    System.out.println("  Worker " + workerId + " Map 完成!");
                    barrier.join("worker-" + workerId);
                } catch (Exception e) {
                    e.printStackTrace();
                }
            }).start();
        }

        // Master 等待所有 Worker 完成 Map
        barrier.await();
        System.out.println("所有 Worker 已完成 Map!\n");
    }

    // Reduce 阶段启动
    public void reducePhase() throws Exception {
        System.out.println("=== Reduce 阶段开始 ===");
        System.out.println("Master 开始分发 Reduce 任务...");
        Thread.sleep(1000);
        System.out.println("Reduce 阶段完成!\n");
    }

    public void cleanup() throws Exception {
        DistributedBarrier barrier = new DistributedBarrier(zk, BARRIER_NAME, 0);
        for (int i = 1; i <= WORKER_COUNT; i++) {
            barrier.leave("worker-" + i);
        }
    }

    public static void main(String[] args) throws Exception {
        CountDownLatch latch = new CountDownLatch(1);
        ZooKeeper zk = new ZooKeeper("127.0.0.1:2181", 5000, event -> {
            if (event.getState() == Watcher.Event.KeeperState.SyncConnected) {
                latch.countDown();
            }
        });
        latch.await();

        MapReduceCoordinator coordinator = new MapReduceCoordinator(zk);
        coordinator.mapPhase();
        coordinator.reducePhase();
        coordinator.cleanup();

        zk.close();
    }
}
```

### 测试验证

```bash
# 运行 MapReduce 协调器
mvn exec:java -Dexec.mainClass="com.zkdemo.coordination.MapReduceCoordinator"

# 运行计数器基准测试
mvn exec:java -Dexec.mainClass="com.zkdemo.coordination.CounterBenchmark"
```

### 可能遇到的坑

| 问题 | 原因 | 解决 |
|------|------|------|
| Barrier 永远等不到 | Worker 崩溃，临时节点持续存在 | 设置超时时间 |
| CAS 冲突过多 | 高并发递增 | 分段计数器或增大重试间隔 |
| 子节点过多 | 大量临时节点未清理 | 使用递归删除或自动清理策略 |

---

## 4. 项目总结

### 优点 & 缺点

| 组件 | 优点 | 缺点 |
|------|------|------|
| Barrier | 利用 Watcher 实时通知 | Worker 崩溃可能导致 Barrier 永远无法触发 |
| CountDownLatch | 原子递减操作 | 初始化时需创建 N 个节点 |
| DistributedAtomicLong | 强一致性 | CAS 冲突率高时性能下降 |

### 适用场景

- **Barrier**：MapReduce 协调、并行任务汇聚
- **CountDownLatch**：服务启动依赖检查、多节点任务就绪
- **DistributedAtomicLong**：全局计数器、统计、限流

### 注意事项

- Worker 崩溃时临时节点自动删除，如果不小心删了会提前满足 Barrier 条件
- 计数器应该加上 `init()` 初始化，确保节点存在
- 高并发计数器建议使用 Curator 的 `DistributedAtomicLong`（自带重试）

### 常见踩坑经验

**故障 1：Barrier 提前触发**

现象：只有 8 个 Worker 加入 Barrier（阈值为 10），但 Barrier 条件却满足了。

根因：某个 Worker 的临时节点因网络抖动被自动删除，然后 Worker 重新创建了同名节点。ZooKeeper 的 `getChildren` 计数没有区分"同一 Worker 的两次加入"。

解决方案：使用唯一标识（如 sessionId 或 UUID）作为临时节点名称，避免节点名重复。

**故障 2：计数器读到的值不是最新**

现象：Counter `get()` 返回的值小于实际写入次数。

根因：分布式计数器的 CAS 递增成功，但后续的 `get()` 读到了另一个还未完成 CAS 的中间值。

解决方案：使用 Leader 上的强一致性读（`sync()` 方法），或接受最终一致性读的延迟。

### 思考题

1. 分布式 Barrier 中，如果某个 Worker 在创建临时节点后崩溃了，ZooKeeper 自动删除该节点。此时 Barrier 的条件（N 个 Worker）还满足吗？如果本来有 10 个 Worker，一个崩溃后变成了 9 个，Barrier 是否会永远阻塞？
2. DistributedAtomicLong 在高并发下 CAS 冲突率很高。如果改成"预分配一段 ID 区间"（比如每次从 ZooKeeper 获取 [1-100]、[101-200] 区间，然后在本地递增），会有什么优缺点？

### 推广计划提示

- **开发**：Barrier 和 CountDownLatch 在 Curator 中都有现成实现，建议直接使用
- **测试**：测试 Barrier 时需要覆盖 Worker 崩溃、网络抖动、超时等场景
- **架构师**：分布式计数器在生产中优先考虑 Redis（性能更好），仅在强一致性要求下使用 ZooKeeper
