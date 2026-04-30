# 第25章：选主机制——Leader Latch 与 Leader  Election 实战

## 1. 项目背景

### 业务场景

很多分布式系统需要一个**主节点（Master）**来协调全局任务：

- **定时任务调度**：一个集群中只有一个实例执行定时任务（比如每小时清理一次过期数据）
- **分布式系统的单一决策者**：Kafka 的 Controller、HBase 的 HMaster
- **资源协调**：只有一个节点负责分配任务给其他节点

如果不用 ZooKeeper 的选主机制，你可能会尝试：配置文件指定谁是主（不靠谱，节点宕机了不会自动切换）、数据库行锁（性能差，无法自动感知节点状态）。

### 痛点放大

没有自动选主机制的问题：

- **人工切换主节点需要几分钟**：主节点宕机后需要人工介入
- **脑裂风险**：两个节点都认为自己是主，同时写入互相冲突的数据
- **任务重复执行**：多个节点同时执行定时任务

---

## 2. 项目设计

### 剧本式交锋对话

**场景**：小胖需要实现一个"多节点定时任务调度"，确保同一时刻只有一个实例执行任务。

**小胖**：我用了第 21 章的分布式锁来实现——获取锁的执行任务，锁释放后下一个实例获取锁。但感觉很重，有没有更轻量的方案？

**大师**：选主机制就是为这个场景设计的。Curator 提供了两种方案：

| 方案 | 原理 | 特点 |
|------|------|------|
| **LeaderLatch** | 所有节点争抢同一个临时节点，创建成功的为 Leader | 一旦选出不轻易切换，主挂了才重新选 |
| **LeaderSelector** | 所有节点创建临时顺序节点，序号最小的为 Leader | 主释放后自动选下一个，轮值模式 |

**LeaderLatch 原理**：

```
所有节点竞争一个路径 /leader/cron-job

节点 A：创建 /leader/cron-job 成功 → A 成为 Leader
节点 B：创建 /leader/cron-job 失败 → B Watcher /leader/cron-job
节点 C：创建 /leader/cron-job 失败 → C Watcher /leader/cron-job

节点 A 宕机 → /leader/cron-job 被自动删除
节点 B 和 C 的 Watcher 触发 → 再次竞争创建
节点 B 创建成功 → B 成为新 Leader
```

**LeaderSelector 原理**：

```
所有节点创建临时顺序节点

节点 A：/leader/cron-job/member-0000000001 → 序号最小 → Leader
节点 B：/leader/cron-job/member-0000000002 → 等待
节点 C：/leader/cron-job/member-0000000003 → 等待

节点 A 释放或关闭 → member-0000000001 被删除
节点 B 的 Watcher 触发 → 检查自己是最小序号 → 成为新 Leader
节点 B 业务执行完 → member-0000000002 被删除
节点 C 成为下一任 Leader
```

**小胖**：那 LeaderLatch 和 LeaderSelector 分别在什么情况下使用？

**大师**：

- **LeaderLatch**：适合"谁当 Leader 不重要，只要有一个"的场景。比如定时任务清理数据——任何一个节点都能执行，但只需要一个。主挂了就换一个，不会出现两个同时执行。
- **LeaderSelector**：适合"轮值"场景。每个节点都能当 Leader，依次轮流。比如 Leader 负责分发任务，累活大家轮着干。

> **技术映射**：LeaderLatch = 只有一个钥匙的储物柜，谁抢到钥匙谁用；LeaderSelector = 会议室预约签到表，排到谁谁用

**小白**：如果 Leader 宕机了，但有些任务已经在执行中，新 Leader 会不会重复执行？

**大师**：这就是**脑裂防范**的问题。解决办法：
1. 每个任务带上唯一 ID
2. 任务状态存储在 ZooKeeper 中（持久节点），标记为 RUNNING / DONE / FAILED
3. 新 Leader 启动时检查任务的最终状态，判断是否需要恢复

> **技术映射**：脑裂防范 = 领导换届后的工作交接，先查文档（ZK 中的任务状态）再决策

---

## 3. 项目实战

### 环境准备

- ZooKeeper 3.9.x 运行中
- JDK 11+
- Maven

### 分步实现

#### 步骤 1：LeaderLatch 实现定时任务调度

创建 `CronJobScheduler.java`：

```java
package com.zkdemo.election;

import org.apache.curator.framework.CuratorFramework;
import org.apache.curator.framework.CuratorFrameworkFactory;
import org.apache.curator.framework.recipes.leader.LeaderLatch;
import org.apache.curator.framework.recipes.leader.LeaderLatchListener;
import org.apache.curator.retry.ExponentialBackoffRetry;

import java.util.concurrent.TimeUnit;

public class CronJobScheduler implements AutoCloseable {
    private static final String LEADER_PATH = "/leader/cron-jobs";
    private final CuratorFramework client;
    private final LeaderLatch leaderLatch;
    private final String instanceId;
    private volatile boolean isLeader = false;

    public CronJobScheduler(String connectString, String instanceId) throws Exception {
        this.instanceId = instanceId;

        client = CuratorFrameworkFactory.builder()
                .connectString(connectString)
                .retryPolicy(new ExponentialBackoffRetry(1000, 3))
                .build();
        client.start();

        leaderLatch = new LeaderLatch(client, LEADER_PATH, instanceId);
        leaderLatch.addListener(new LeaderLatchListener() {
            @Override
            public void isLeader() {
                isLeader = true;
                System.out.println("\n=================================");
                System.out.println("[实例 " + instanceId + "] 成为 Leader!");
                System.out.println("=================================\n");
            }

            @Override
            public void notLeader() {
                isLeader = false;
                System.out.println("[实例 " + instanceId + "] 不再是 Leader");
            }
        });
        leaderLatch.start();

        System.out.println("[实例 " + instanceId + "] 启动，等待成为 Leader...");
    }

    public boolean isLeader() {
        // LeaderLatch 的 hasLeadership 方法更准确
        return leaderLatch.hasLeadership();
    }

    // 模拟定时任务执行
    public void executeJobIfLeader() {
        if (leaderLatch.hasLeadership()) {
            System.out.println("[实例 " + instanceId + " Leader] 执行定时任务: " + getJobName());
            try {
                // 模拟任务执行
                TimeUnit.SECONDS.sleep(2);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
            System.out.println("[实例 " + instanceId + " Leader] 任务完成");
        } else {
            System.out.println("[实例 " + instanceId + " 备机] 不是 Leader，跳过任务");
        }
    }

    private String getJobName() {
        String[] jobs = {
                "数据清理",
                "日志归档",
                "缓存预热",
                "报表生成"
        };
        return jobs[(int) (System.currentTimeMillis() / 1000 % jobs.length)];
    }

    public void close() throws Exception {
        if (leaderLatch != null) {
            leaderLatch.close();
        }
        if (client != null) {
            client.close();
        }
    }

    public static void main(String[] args) throws Exception {
        // 启动 3 个实例
        CronJobScheduler instance1 = new CronJobScheduler("127.0.0.1:2181", "A");
        CronJobScheduler instance2 = new CronJobScheduler("127.0.0.1:2181", "B");
        CronJobScheduler instance3 = new CronJobScheduler("127.0.0.1:2181", "C");

        // 等待初始选举
        TimeUnit.SECONDS.sleep(1);
        System.out.println("\n初始 Leader: " +
                (instance1.isLeader() ? "A" :
                 instance2.isLeader() ? "B" : "C"));

        // 模拟定时任务轮询（每 3 秒检查一次）
        System.out.println("\n=== 模拟定时任务调度 ===");
        for (int i = 0; i < 8; i++) {
            TimeUnit.SECONDS.sleep(3);
            instance1.executeJobIfLeader();
        }

        // 模拟 Leader 宕机
        System.out.println("\n=== 模拟 Leader 宕机 ===");
        String leaderId = instance1.isLeader() ? "A" :
                          instance2.isLeader() ? "B" : "C";
        System.out.println("停止 Leader: " + leaderId);
        if (leaderId.equals("A")) instance1.close();
        else if (leaderId.equals("B")) instance2.close();
        else instance3.close();

        TimeUnit.SECONDS.sleep(3);
        System.out.println("\n新 Leader 已选出，继续执行任务...");
        if (instance1.isLeader()) instance1.executeJobIfLeader();
        else if (instance2.isLeader() && instance2.isLeader()) instance2.executeJobIfLeader();
        else if (instance3.isLeader()) instance3.executeJobIfLeader();

        // 清理
        try { instance1.close(); } catch (Exception ignored) {}
        try { instance2.close(); } catch (Exception ignored) {}
        try { instance3.close(); } catch (Exception ignored) {}
    }
}
```

#### 步骤 2：LeaderSelector 实现分布式任务轮值

创建 `TaskDistributor.java`：

```java
package com.zkdemo.election;

import org.apache.curator.framework.CuratorFramework;
import org.apache.curator.framework.CuratorFrameworkFactory;
import org.apache.curator.framework.recipes.leader.LeaderSelector;
import org.apache.curator.framework.recipes.leader.LeaderSelectorListener;
import org.apache.curator.framework.state.ConnectionState;
import org.apache.curator.retry.ExponentialBackoffRetry;

import java.util.concurrent.TimeUnit;

public class TaskDistributor implements AutoCloseable {
    private static final String SELECTOR_PATH = "/selector/task-distributor";
    private final CuratorFramework client;
    private final LeaderSelector selector;
    private final String instanceId;

    public TaskDistributor(String connectString, String instanceId) {
        this.instanceId = instanceId;

        client = CuratorFrameworkFactory.builder()
                .connectString(connectString)
                .retryPolicy(new ExponentialBackoffRetry(1000, 3))
                .build();
        client.start();

        selector = new LeaderSelector(client, SELECTOR_PATH,
                new LeaderSelectorListener() {
            @Override
            public void takeLeadership(CuratorFramework client) throws Exception {
                System.out.println("\n═══════════════════════════════════");
                System.out.println("[实例 " + instanceId + "] 成为 Leader");
                System.out.println("═══════════════════════════════════\n");

                // 作为 Leader 分发任务
                distributeTasks();

                // takeLeadership 返回后自动释放 Leader 角色
                System.out.println("[实例 " + instanceId + "] 完成 Leader 职责，释放角色\n");
            }

            @Override
            public void stateChanged(CuratorFramework client, ConnectionState newState) {
                System.out.println("[实例 " + instanceId + "] 连接状态: " + newState);
                if (newState == ConnectionState.LOST) {
                    // 连接丢失，中断当前 Leader 任务
                    throw new RuntimeException("连接丢失");
                }
            }
        });

        // 自动重新排队（执行完自动排到队尾）
        selector.autoRequeue();
        selector.start();

        System.out.println("[实例 " + instanceId + "] 已加入选举队列");
    }

    private void distributeTasks() throws Exception {
        String[] tasks = {"任务-1: 数据清洗", "任务-2: 索引重建", "任务-3: 缓存刷新"};

        for (String task : tasks) {
            System.out.println("[实例 " + instanceId + " Leader] 分发: " + task);
            TimeUnit.SECONDS.sleep(1);
        }

        // 模拟 Leader 任务执行时间
        TimeUnit.SECONDS.sleep(3);
    }

    @Override
    public void close() throws Exception {
        if (selector != null) selector.close();
        if (client != null) client.close();
    }

    public static void main(String[] args) throws Exception {
        System.out.println("=== LeaderSelector 任务分配演示 ===\n");

        // 启动 3 个实例
        TaskDistributor d1 = new TaskDistributor("127.0.0.1:2181", "Node-1");
        TaskDistributor d2 = new TaskDistributor("127.0.0.1:2181", "Node-2");
        TaskDistributor d3 = new TaskDistributor("127.0.0.1:2181", "Node-3");

        // 观察轮值
        System.out.println("\n观察 Leader 轮值（每 6 秒切换一次）...\n");
        TimeUnit.SECONDS.sleep(20);

        d1.close();
        d2.close();
        d3.close();
        System.out.println("演示完成");
    }
}
```

#### 步骤 3：LeaderLatch vs LeaderSelector 对比

创建 `ElectionComparison.java`：

```java
package com.zkdemo.election;

public class ElectionComparison {
    public static void main(String[] args) {
        System.out.println("=== LeaderLatch vs LeaderSelector 对比 ===\n");

        String[][] rows = {
                {"特性", "LeaderLatch", "LeaderSelector"},
                {"选举原理", "争抢同一临时节点", "临时顺序节点，最小序号成为 Leader"},
                {"Leader 切换时机", "当前 Leader 断开连接", "Leader 主动释放或断开"},
                {"Leader 执行周期", "持续 Leader（直到断开）", "执行完即释放，轮值制"},
                {"适用场景", "主从模式（一个主，其余备）", "轮值模式（大家轮流当主）"},
                {"任务执行", "Leader 持续执行，备机等待", "Leader 执行完释放角色，下一个接替"},
                {"Curator 类", "LeaderLatch", "LeaderSelector"},
                {"API", "hasLeadership() / await()", "takeLeadership() 回调"},
        };

        for (String[] row : rows) {
            System.out.printf("%-25s %-35s %-35s%n", row[0], row[1], row[2]);
        }

        System.out.println("\n选型建议:");
        System.out.println("  LeaderLatch:  定时任务清理、监控采集、Master 选举（持续 Leader）");
        System.out.println("  LeaderSelector: 任务分发、负载均衡、灰度发布（轮值 Leader）");
    }
}
```

#### 步骤 4：基于 ZooKeeper 的 Cron 调度框架

创建 `ZkCronFramework.java`：

```java
package com.zkdemo.election;

import org.apache.curator.framework.CuratorFramework;
import org.apache.curator.framework.CuratorFrameworkFactory;
import org.apache.curator.framework.recipes.leader.LeaderLatch;
import org.apache.curator.framework.recipes.leader.LeaderLatchListener;
import org.apache.curator.retry.ExponentialBackoffRetry;
import org.apache.zookeeper.CreateMode;

import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;

/**
 * 基于 ZooKeeper LeaderLatch 的 Cron 调度框架
 * 确保同一时刻只有一个实例执行定时任务
 */
public class ZkCronFramework implements AutoCloseable {
    private static final String CRON_ROOT = "/cron-jobs";
    private final CuratorFramework client;
    private final String instanceId;
    private final ScheduledExecutorService scheduler = Executors.newScheduledThreadPool(2);

    public ZkCronFramework(String connectString, String instanceId) throws Exception {
        this.instanceId = instanceId;

        client = CuratorFrameworkFactory.builder()
                .connectString(connectString)
                .retryPolicy(new ExponentialBackoffRetry(1000, 3))
                .build();
        client.start();

        // 确保根路径存在
        if (client.checkExists().forPath(CRON_ROOT) == null) {
            client.create().creatingParentsIfNeeded()
                    .forPath(CRON_ROOT, "".getBytes());
        }
    }

    // 注册一个定时任务（全局唯一，Leader 执行）
    public void registerCronJob(String jobName, long intervalMs, Runnable task) {
        scheduler.scheduleWithFixedDelay(() -> {
            try {
                String leaderPath = CRON_ROOT + "/" + jobName;

                // 每个任务有自己独立的 Leader latch
                LeaderLatch latch = new LeaderLatch(client, leaderPath, instanceId);
                latch.start();

                // 等待 500ms 确认领导权
                boolean isLeader = latch.await(500, TimeUnit.MILLISECONDS);

                if (isLeader) {
                    String timestamp = LocalDateTime.now()
                            .format(DateTimeFormatter.ofPattern("HH:mm:ss"));
                    System.out.printf("[%s] [%s Leader] 执行任务: %s%n",
                            timestamp, instanceId, jobName);
                    task.run();
                }

                latch.close();
            } catch (Exception e) {
                System.err.println("任务执行失败: " + jobName + " - " + e.getMessage());
            }
        }, 0, intervalMs, TimeUnit.MILLISECONDS);

        System.out.println("注册定时任务: " + jobName + " (间隔: " + intervalMs + "ms)");
    }

    @Override
    public void close() throws Exception {
        scheduler.shutdown();
        client.close();
    }

    public static void main(String[] args) throws Exception {
        String instanceId = args.length > 0 ? args[0] : "Instance-A";

        ZkCronFramework framework = new ZkCronFramework("127.0.0.1:2181", instanceId);

        // 注册三个定时任务
        framework.registerCronJob("data-cleanup", 5000, () -> {
            System.out.println("  清理过期数据...");
            try { Thread.sleep(1000); } catch (InterruptedException e) {}
        });

        framework.registerCronJob("log-archive", 8000, () -> {
            System.out.println("  归档日志文件...");
            try { Thread.sleep(500); } catch (InterruptedException e) {}
        });

        framework.registerCronJob("cache-warm", 10000, () -> {
            System.out.println("  预热缓存...");
            try { Thread.sleep(2000); } catch (InterruptedException e) {}
        });

        System.out.println("\n" + instanceId + " 启动完成，等待执行定时任务...\n");

        Thread.sleep(60000);
        framework.close();
    }
}
```

### 测试验证

```bash
# 运行 LeaderLatch 示例
mvn exec:java -Dexec.mainClass="com.zkdemo.election.CronJobScheduler"

# 运行 LeaderSelector 示例
mvn exec:java -Dexec.mainClass="com.zkdemo.election.TaskDistributor"

# 运行 Cron 框架（多实例测试）
# 终端 1
mvn exec:java -Dexec.mainClass="com.zkdemo.election.ZkCronFramework" -Dexec.args="Instance-A"

# 终端 2（5 秒后启动）
mvn exec:java -Dexec.mainClass="com.zkdemo.election.ZkCronFramework" -Dexec.args="Instance-B"

# 停止 Instance-A，观察 Instance-B 接管
```

### 可能遇到的坑

| 问题 | 原因 | 解决 |
|------|------|------|
| LeaderLatch.await() 超时 | 另一个实例始终持有锁 | 检查 Session Timeout 是否导致持久节点残留 |
| LeaderSelector 不重新排队 | 忘记调用 `autoRequeue()` | LeaderSelector 启动后必须调用 autoRequeue |
| 连接丢失后 `takeLeadership` 被中断 | LeaderSelectorListener 监听 ConnectionState.LOST | 在 stateChanged 中抛出异常中断任务 |

---

## 4. 项目总结

### 优点 & 缺点

| 特性 | LeaderLatch | LeaderSelector |
|------|------------|---------------|
| Leader 稳定性 | 高（不会轻易切换） | 低（每次执行完切换） |
| 负载均衡 | 差（Leader 一直干，备机闲着） | 好（大家轮着当 Leader） |
| 使用复杂度 | 简单 | 中等 |
| 典型场景 | 主从架构 | 任务调度 |

### 适用场景

- **LeaderLatch**：Master 选举（HBase Master、Kafka Controller）、持续型定时任务
- **LeaderSelector**：任务分发、Job 轮值、灰度发布开关

### 注意事项

- `LeaderSelector` 的 `takeLeadership()` 返回后自动释放 Leader，不要在方法内留死循环
- 连接状态变化（`ConnectionState.LOST`）需要中断当前 Leader 任务，否则可能脑裂
- 两个 Latch 的 `close()` 必须在 finally 块中执行

### 常见踩坑经验

**故障 1：LeaderSelector takeLeadership 不退出**

现象：LeaderSelector 选出 Leader 后，`takeLeadership` 方法一直不返回，后续节点永远无法成为 Leader。

根因：`takeLeadership` 方法内部有 `while(true)` 或 `Thread.sleep(Long.MAX_VALUE)`，导致方法永远不会返回。LeaderSelector 的语义是"执行完任务就释放"，不是"永久是 Leader"。

**故障 2：LeaderLatch 会话过期后旧 Leader 还认为自己 是 Leader**

现象：Leader 实例的网络断开后无法连接 ZooKeeper，但 `hasLeadership()` 仍然返回 `true`，继续执行业务逻辑。

根因：LeaderLatch 的 `hasLeadership()` 判断的是本地状态，不是实时从 ZooKeeper 查询的。需要配合连接状态监听来纠正。

### 思考题

1. 在 `ZkCronFramework` 中，每个定时任务独立使用一个 `LeaderLatch`（路径不同）。如果有 10 个定时任务，每个任务都需要独立选举。这种设计有什么问题？如何优化？
2. `LeaderSelector` 的 `autoRequeue()` 在 Leader 执行完后自动重新排队。如果 Leader 的任务执行时间很长（比如 30 分钟），导致其他节点一直没有机会当 Leader，这公平吗？如何保证"最大 Leader 时长"？

### 推广计划提示

- **开发**：定时任务和单 Master 场景优先使用 LeaderLatch（简单可靠），轮值场景使用 LeaderSelector
- **运维**：监控 `/leader/` 和 `/selector/` 路径下的节点数量，确保 Leader 选举正常
- **测试**：测试需要覆盖 Leader 宕机、网络分区、Leader 恢复后的重新选举
