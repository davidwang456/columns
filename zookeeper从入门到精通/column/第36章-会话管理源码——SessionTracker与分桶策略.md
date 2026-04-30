# 第36章：会话管理源码——SessionTracker 与分桶策略

## 1. 项目背景

### 业务场景

ZooKeeper 服务端需要管理成千上万个客户端会话（Session）。会话的核心是**心跳检测**和**超时清理**——客户端定时发送心跳，服务端更新会话到期时间；如果客户端超时未发心跳，服务端关闭会话，清理其临时节点。

ZooKeeper 使用**分桶策略（Bucket）** 来高效管理大量会话的过期检查，而不是逐个遍历。

### 源码定位

```
SessionTrackerImpl.java    ← 会话管理器核心
Session.java               ← 会话对象
```

---

## 2. 项目设计

### 剧本式交锋对话

**场景**：小白想了解 ZooKeeper 服务端是如何管理成千上万个客户端会话的。

**小白**：如果 ZooKeeper 有 10 万个客户端连接，每个客户端每 10 秒发一次心跳。ZooKeeper 如何高效地检查哪些会话过期了？难道每 1 秒遍历全部 10 万个会话吗？

**大师**：这正是**分桶策略（Bucket）** 要解决的问题。

原理很简单——把过期的检查粒度从"每个会话"变为"每个桶"。

```
分桶策略：

时间轴（tickTime = 2000ms）：
tick:    0      1      2      3      4      5      6      7
        |------|------|------|------|------|------|------|
桶:      T0     T1     T2     T3     T4     T5     T6     T7

每个桶包含在这个 tick 到期的所有会话。

例如 Session Timeout = 10000ms（5 tick）：
  客户端最后心跳时间 = tick 1
  到期时间 = tick 1 + 5 = tick 6
  → 该会话放入桶 T6

当时间到达 tick 6 时：
  检查桶 T6 → 桶内的所有会话过期 → 批量清理
  不需要逐个遍历所有会话！
```

**小胖**：那心跳（touchSession）时做了什么？

**大师**：心跳时，服务端把会话从**旧桶移到新桶**：

```
客户端在 tick 1 发送心跳：
  会话从桶 T6 移到桶 T1 + 5 = T6（不变？不，因为心跳刷新了到期时间）

等等，更准确的说：
  客户端当前到期时间 = tick 6（假设 Timeout=5 tick）
  收到心跳 → 到期时间 = 当前时间 + 5 tick = tick 1 + 5 = tick 6
  如果当前时间已经是 tick 3 → 到期时间 = tick 3 + 5 = tick 8
  会话从桶 T6 移到桶 T8
```

---

## 3. 项目实战

### 环境准备

- ZooKeeper 3.9.x 源码
- IntelliJ IDEA

### 分步实现

#### 步骤 1：SessionTrackerImpl 数据结构

```java
// 文件位置：org.apache.zookeeper.server.SessionTrackerImpl
// 会话管理器的核心数据结构

public class SessionTrackerImpl extends ZooKeeperCriticalThread
        implements SessionTracker {

    // 核心数据结构
    // 1. sessionsById：sessionId → Session
    ConcurrentHashMap<Long, SessionImpl> sessionsById =
            new ConcurrentHashMap<>();

    // 2. sessionSets：过期时间 → 在同一时间到期的会话集合
    // 这就是"桶"——key 是过期时间（毫秒），value 是该过期时间的所有会话
    HashMap<Long, SessionSet> sessionSets =
            new HashMap<>();

    // 3. 下一个要检查的过期时间
    long nextExpirationTime;

    // 4. tickTime 的基本单位
    private final long tickTime;

    @Override
    public void run() {
        // 主循环——定期检查过期会话
        while (running) {
            // 当前时间
            long now = Time.currentElapsedTime();

            // 检查是否有过期的桶
            if (now >= nextExpirationTime) {
                // 遍历所有到期的桶
                for (long expTime = nextExpirationTime;
                     expTime <= now;
                     expTime += tickTime) {

                    // 取出该桶中的所有会话
                    SessionSet sessionSet = sessionSets.remove(expTime);
                    if (sessionSet != null) {
                        // 批量过期这个桶中的所有会话
                        for (SessionImpl s : sessionSet.sessions) {
                            // 清理会话（删除临时节点、清理 Watcher）
                            setSessionClosing(s.sessionId);
                            expire(s);
                        }
                    }
                }
                // 更新下一次检查时间
                nextExpirationTime += tickTime;
            }

            // 等待直到下一个 tick
            wait(nextExpirationTime - now);
        }
    }
}
```

#### 步骤 2：Session 对象

```java
// 文件位置：org.apache.zookeeper.server.SessionTrackerImpl
// Session 的内部类

public static class SessionImpl implements Session {
    long sessionId;       // 会话 ID（全局唯一）
    int timeout;          // 会话超时时间（毫秒）
    long tickTime;        // 会话的 tick 时间
    boolean isClosing;    // 正在关闭中
    Object owner;         // 拥有者（ServerCnxn）
    long expireTime;      // 到期时间
}
```

#### 步骤 3：touchSession()——心跳刷新

```java
// 文件位置：org.apache.zookeeper.server.SessionTrackerImpl
// 客户端心跳时调用

synchronized public boolean touchSession(long sessionId, int timeout) {
    SessionImpl s = sessionsById.get(sessionId);
    if (s == null) {
        return false;  // 会话不存在
    }

    if (s.isClosing()) {
        return false;  // 会话正在关闭
    }

    // 计算新的过期时间
    long expireTime = roundToInterval(Time.currentElapsedTime() + timeout);
    s.expireTime = expireTime;

    // 将会话从旧桶移到新桶
    // 注意：这里没有显式从旧桶移除
    // 因为旧桶被检查和清空时，sessionSets.get(expTime) 返回的 Set 里
    // 可能有已经移走的会话（在 expire 时会检查 isClosing）
    sessionSets.computeIfAbsent(expireTime, k -> new SessionSet())
            .add(s);

    return true;
}

// 将时间对齐到 tickTime 的整数倍
private long roundToInterval(long time) {
    // 向上取整到最近的 tickTime 倍数
    return (time / tickTime + (time % tickTime != 0 ? 1 : 0)) * tickTime;
}
```

#### 步骤 4：会话创建和关闭

```java
// 文件位置：org.apache.zookeeper.server.SessionTrackerImpl
// 创建会话

synchronized public long createSession(int sessionTimeout) {
    // 分配 sessionId
    long sessionId = nextSessionId.getAndIncrement();

    // 创建 Session 对象
    SessionImpl s = new SessionImpl(sessionId, sessionTimeout, 0, 0);

    // 计算过期时间
    long expireTime = roundToInterval(Time.currentElapsedTime() + sessionTimeout);
    s.expireTime = expireTime;

    // 放入 sessionsById
    sessionsById.put(sessionId, s);

    // 放入桶
    sessionSets.computeIfAbsent(expireTime, k -> new SessionSet()).add(s);

    return sessionId;
}

// 关闭会话
synchronized public void expire(SessionImpl s) {
    if (s.isClosing()) return;

    // 标记为正在关闭
    s.isClosing = true;

    // 1. 从 sessionsById 移除
    sessionsById.remove(s.sessionId);

    // 2. 清理临时节点
    // （通过 DataTree 的 killSession 方法）
    zk.getZKDatabase().killSession(s.sessionId);

    // 3. 关闭客户端连接
    // （通过 ServerCnxn 的 close 方法）
    close(s.sessionId);
}
```

#### 步骤 5：分桶策略的运行逻辑

```java
// 文件位置：org.apache.zookeeper.server.SessionTrackerImpl
// run() 方法主循环的详细逻辑

@Override
public void run() {
    try {
        while (running) {
            long now = Time.currentElapsedTime();

            // 如果当前时间超过了下一个过期时间
            // 说明有桶需要被清理
            if (now >= nextExpirationTime) {
                // 遍历所有需要清理的桶
                // 从 nextExpirationTime 到 now，步长为 tickTime
                for (long expTime = nextExpirationTime;
                     expTime <= now;
                     expTime += tickTime) {

                    // 取出这个桶
                    SessionSet bucket = sessionSets.remove(expTime);

                    if (bucket != null) {
                        // 这个桶里的所有会话都过期了，逐个处理
                        for (SessionImpl s : bucket.sessions) {
                            try {
                                // 标记会话关闭
                                setSessionClosing(s.sessionId);
                                // 执行过期操作
                                expire(s);
                            } catch (Exception e) {
                                LOG.error("Session expire failed", e);
                            }
                        }
                    }
                }

                // 更新下一次检查时间
                nextExpirationTime += tickTime;
            } else {
                // 还没到检查时间，等待
                long waitTime = nextExpirationTime - now;
                if (waitTime > 0) {
                    wait(waitTime);
                }
            }
        }
    } catch (InterruptedException e) {
        // 被中断
    }
}
```

#### 步骤 6：添加 JMX 暴露桶信息

创建 `SessionMetrics.java`（用于监控）：

```java
package com.zkdemo.source;

import org.apache.zookeeper.server.SessionTrackerImpl;

import javax.management.*;
import java.lang.management.ManagementFactory;
import java.util.HashMap;
import java.util.Map;

/**
 * 将会话管理器的桶信息暴露为 JMX 指标
 */
public class SessionMetrics {
    private final SessionTrackerImpl sessionTracker;

    public SessionMetrics(SessionTrackerImpl sessionTracker) {
        this.sessionTracker = sessionTracker;
        registerMBean();
    }

    private void registerMBean() {
        try {
            MBeanServer mbs = ManagementFactory.getPlatformMBeanServer();
            ObjectName name = new ObjectName("org.apache.ZooKeeperService:type=SessionTracker");
            mbs.registerMBean(new SessionTrackerMXBean() {
                @Override
                public int getActiveSessionCount() {
                    return sessionTracker.getSessionCount();
                }

                @Override
                public Map<Long, Integer> getBucketDistribution() {
                    // 获取每个桶的会话数量
                    Map<Long, Integer> distribution = new HashMap<>();
                    // 通过反射获取 sessionSets
                    try {
                        var field = SessionTrackerImpl.class
                                .getDeclaredField("sessionSets");
                        field.setAccessible(true);
                        @SuppressWarnings("unchecked")
                        var sessionSets = (HashMap<Long, Object>) field.get(sessionTracker);
                        for (var entry : sessionSets.entrySet()) {
                            // 获取 Set 的大小
                            var setField = entry.getValue().getClass()
                                    .getDeclaredField("sessions");
                            setField.setAccessible(true);
                            var sessions = (java.util.Set<?>) setField.get(entry.getValue());
                            distribution.put(entry.getKey(), sessions.size());
                        }
                    } catch (Exception e) {
                        e.printStackTrace();
                    }
                    return distribution;
                }

                @Override
                public long getNextExpirationTime() {
                    // 获取下一次过期检查时间
                    try {
                        var field = SessionTrackerImpl.class
                                .getDeclaredField("nextExpirationTime");
                        field.setAccessible(true);
                        return field.getLong(sessionTracker);
                    } catch (Exception e) {
                        return -1;
                    }
                }
            }, name);
        } catch (Exception e) {
            e.printStackTrace();
        }
    }

    public interface SessionTrackerMXBean {
        int getActiveSessionCount();
        Map<Long, Integer> getBucketDistribution();
        long getNextExpirationTime();
    }
}
```

#### 步骤 7：添加调试日志

在 `SessionTrackerImpl.java` 中添加：

```java
// 在 expire() 和 touchSession() 中添加日志

// 在 expire 中添加：
System.out.println("[SessionTracker] 会话过期: sessionId=0x"
        + Long.toHexString(s.sessionId)
        + ", timeout=" + s.timeout
        + "ms, 清理临时节点");

// 在 touchSession 中添加：
System.out.println("[SessionTracker] 心跳刷新: sessionId=0x"
        + Long.toHexString(s.sessionId)
        + ", 到期时间=" + s.expireTime);
```

### 测试验证

```bash
# 1. 启动 ZooKeeper
# 2. 创建多个客户端连接
# 3. 观察 SessionTrackerImpl 的桶管理

# 使用四字命令查看会话信息
echo dump | nc 127.0.0.1 2181
# 输出当前所有活跃会话

# 使用 JMX 查看会话指标（需开启 JMX）
# 通过 JConsole 或 jvisualvm 连接
```

### 可能遇到的坑

| 问题 | 原因 | 解决 |
|------|------|------|
| 会话过期延迟 | tickTime 粒度有限 | 分桶策略的最小单位是 tickTime |
| 桶中会话数量不均 | 所有会话同时连接同时断开 | 正常现象，桶设计可以处理 |
| 桶中残留已过期会话 | 会话被 touchSession 移到新桶，但旧桶未清理 | expire 时检查 isClosing |

---

## 4. 项目总结

### 分桶策略架构

```
sessionsById (ConcurrentHashMap)
  key=sessionId → value=Session

         │
         ▼
sessionSets (HashMap)
  key=expireTime → value=SessionSet (桶)
         │
         ├── T0: [session-001, session-002, ...]
         ├── T1: [session-003, session-004, ...]
         ├── T2: []
         ├── T3: [session-005, ...]
         └── ...
         │
         ▼
run() 循环
  ├── 检查当前时间 >= nextExpirationTime
  ├── 取出过期桶 → 批量过期所有会话
  └── 更新 nextExpirationTime
```

### 效率分析

| 指标 | 逐个遍历 | 分桶策略 |
|------|---------|---------|
| 10 万会话的过期检查 | O(N) = 10 万次比较 | O(桶数) ≈ 5-10 次 |
| 内存开销 | 低 | 额外维护桶结构 |
| 实时性 | 取决于遍历频率 | tickTime 粒度（~2 秒） |

### 思考题

1. 分桶策略中，如果某个桶的会话数量特别多（比如 1 万个会话在同一时间到期），`expire()` 方法对这一个桶的处理耗时可能会很长。这时后面的桶是否会延迟检查？ZooKeeper 是如何避免"桶延迟"的连锁反应的？
2. `touchSession()` 将 Session 移动到新桶，但旧的桶中仍然保留着这个 Session 的引用。这会不会导致"过期了但旧桶中还有引用"的问题？SessionTrackerImpl 是如何处理这个问题的？

### 推广计划提示

- **开发**：理解会话管理机制，有助于合理设置 Session Timeout
- **运维**：通过 JMX 监控活跃会话数，异常增长可能是连接泄漏
- **贡献者**：SessionTrackerImpl 的分桶策略是 ZooKeeper 高效管理大量会话的关键设计
