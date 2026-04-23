# 第 32 章：Concurrent 包与原子语义设计思路

## 1 项目背景

在并发框架设计中，资深架构师小孙需要实现无锁数据结构。研究 Guava 的并发工具源码，理解其原子语义和内存可见性设计。

## 2 项目设计

**大师**："Guava 并发工具基于 `sun.misc.Unsafe` 或 `java.util.concurrent.atomic`：

```java
// Striped：分段锁实现
Striped<Lock> striped = Striped.lock(16);  // 16 个槽位
Lock lock = striped.get(key);  // 根据 hash 选择锁

// AbstractFuture：状态机设计
// 状态：PENDING -> COMPLETE/FAILED/CANCELLED
// 使用 CAS 保证状态转换原子性
```

**技术映射**：Striped 就像是'银行多窗口'——把客户分到不同窗口，减少单个队列长度。"

## 3 项目实战

```java
// Striped 锁用于高并发计数器
public class StripedCounter {
    private final Striped<Lock> striped = Striped.lock(64);
    private final ConcurrentHashMap<String, AtomicLong> counters = 
        new ConcurrentHashMap<>();
    
    public void increment(String key) {
        Lock lock = striped.get(key);
        lock.lock();
        try {
            counters.computeIfAbsent(key, k -> new AtomicLong(0))
                   .incrementAndGet();
        } finally {
            lock.unlock();
        }
    }
}

// LongAdder 风格实现（分段累加）
public class LongAdderStriped {
    private final Striped<AtomicLong> adders = Striped.customStriped(
        16,
        AtomicLong::new
    );
    
    public void add(long x) {
        int index = ThreadLocalRandom.current().nextInt(16);
        adders.getAt(index).addAndGet(x);
    }
}
```

## 4 项目总结

### 并发设计原则

1. **分段减少竞争**：Striped
2. **CAS 替代锁**：无锁数据结构
3. **延迟初始化**：`Suppliers.memoize`
4. **可见性保证**：volatile + happens-before
