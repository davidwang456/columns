# 第37章：内存管理与 Allocator

## 1. 项目背景（约500字）

凌晨三点，运维群炸了——ClickHouse 生产节点又一次被 OOM Killer 无情砍掉。这台机器配了 256GB 内存，`max_memory_usage` 已经设到了 200GB，按理说还剩 56GB 的余量，怎么会 OOM？

DBA 小王翻遍了 `system.query_log`，发现元凶是一个大宽表的聚合查询：`SELECT user_id, count(DISTINCT product_id), sum(amount), array_agg(event_type) FROM orders_year GROUP BY user_id`。日志显示 `memory_usage` 峰值为 198GB，低于 200GB 的限制，但机器的 RSS（常驻内存）却飙到了 220GB，触发了 Linux OOM Killer。

多出来的 22GB 从哪儿来的？

深入排查后发现，ClickHouse 的 `MemoryTracker` 只追踪了通过 jemalloc 分配的"可追踪内存"，但还有几类"隐身内存"未被计入：

- **Arena 分配池**：某些聚合函数（如 `groupArray`、`uniqExact`）的内部状态使用 Arena 线性分配器，这些分配在销毁前不会被精确反映到 MemoryTracker 的实时计数中。
- **MMap 区域**：外部聚合溢写磁盘时使用的临时文件映射，以及 `LowCardinality` 列的字典缓存。
- **线程栈与 TCMalloc/jemalloc 元数据**：每个工作线程约 8MB 栈空间，256 线程就是 2GB；jemalloc 的 background thread、tcache、arena 自身也占用不可忽略的内存。
- **OS 页缓存**：频繁读取的 MergeTree 数据块会被内核缓存，这部分不计入进程的 `VmRSS` 的 `anon` 部分，但同样占据物理内存。

更致命的是"延迟检测"问题。MemoryTracker 的 `checkLimits()` 不是在每次 `malloc` 时实时触发的，而是在关键路径（如插入一行数据到哈希表、创建新 Column 等）上按批次检查。当聚合函数在 Arena 上疯狂分配时，可能在两次检查之间已经多分配了 5-10GB，等 MemoryTracker 反应过来时，水已经漫过了大坝。

**痛点是双重的**：`max_memory_usage` 设得太高，机器 OOM；设得太低，大量查询被拒绝，浪费内存。根本原因在于对 ClickHouse 内存模型的认知盲区——Arena 池的分批分配策略、Column 的 padded 内存布局、MemoryTracker 的层级检查机制、jemalloc 的线程缓存行为，这些问题交织在一起，形成了一张复杂的内存调度图。

本章将深入 ClickHouse 的内存管理内核，拆解 Arena、PaddedPODArray、MemoryTracker、外部聚合溢写和 jemalloc 五个核心模块，帮你摆脱"凭感觉设限制"的困境。

---

## 2. 项目设计：剧本式交锋对话（约1200字）

**场景**：公司茶水间，午休时间。小胖刚从一场 OOM 复盘会上溜出来，一脸困惑。小白抱着一本《深入理解计算机系统》假装在看，实际也在想同一个问题。大师端着保温杯走进来，上面的枸杞暗示了他的资深地位。

---

**小胖**（把咖啡杯往桌上一墩）："我就想不通了！C++ 不是自动管理内存的吗？`new` 和 `delete`、`make_shared` 这些不都封装好了吗？内存管理说白了不就是 `malloc/free` 嘛，ClickHouse 搞那么多花里胡哨的 Arena、Pool、Tracker 干嘛？"

**小白**（合上书）："我也有这个疑惑。我知道 Arena 是批量分配器，但为什么不直接用 jemalloc？jemalloc 本身已经是工业级的高性能分配器了，再加一层 Arena 不是脱裤子放屁吗？还有那个 MemoryTracker，文档说是层级结构，但具体怎么检查超限的？为什么我设了 `max_memory_usage=200GB`，机器还是会 OOM？"

**大师**（拧开保温杯，呷了一口枸杞水）："好问题。先纠正一个认知——`malloc/free` 不是免费的。你们知道一次 `malloc(64)` 背后发生了什么吗？"

**小胖**："分配 64 字节？"

**大师**："jemalloc 要查线程本地缓存（tcache），tcache 没命中就去查 bin 的 slab 列表，还没命中就要向 arena 申请新的 slab，再不行就要 `mmap` 向内核要物理页。涉及自旋锁、红黑树、位图操作。一个 64 字节的分配，可能要走几十个 CPU 指令周期。而 ClickHouse 的一个聚合函数在哈希表中插入一行数据时，可能需要分配十几个小对象——key 的字符串副本、聚合状态的内部缓冲、过渡计算的中间结果。如果全走 jemalloc，`GROUP BY` 1 亿行数据，光是分配器开销就在秒级以上。"

**小胖**（挠头）："那 Arena 怎么做到的？"

**大师**："Arena 的核心逻辑简单到令人发指——就是一句 `ptr += size`。"

**小白**（眼睛亮了）："Bump Allocator！先 `mmap` 一大块内存，然后用一个指针从头往后挪，`alloc` 就是移动指针，`free` 什么也不做，等整个 Arena 销毁时一次性 `munmap`。零碎片，零锁竞争，O(1) 分配。"

**大师**："精确。但代价是：Arena 分配的内存要等到查询结束时才释放。一个跑 10 分钟的大聚合，这期间 Arena 可能已经堆了几十 GB。而 MemoryTracker 的问题更微妙——它的检查不是每条指令后都做的，而是在批次边界上检查。想象一个水坝，漏水检测器不是实时监控，而是每隔 10 分钟巡查一次，等查到的时候下游已经淹了。"

**小胖**："那 `max_bytes_before_external_group_by` 又是干嘛的？"

**大师**："这就是溢写机制。当聚合哈希表撑到某个阈值时，不等它超限，先把当前的部分结果排序写入磁盘，清空哈希表，腾出内存继续处理剩余数据。最后再把磁盘上的所有块归并。相当于内存不够了，在硬盘上开了个临时仓库。"

**小白**："那个 PaddedPODArray 呢？为什么 Column 不用 `std::vector`？"

**大师**："两个原因。第一，SIMD 指令一次读取 32 字节（AVX2）或 64 字节（AVX-512），如果数组末尾没有足够填充，读取最后一个元素时会把越界数据读进寄存器，虽然会被 mask 掉，但 sanitizer 会报警、某些 CPU 可能触发跨页 fault。所以在数组头尾各加一段 padding。第二，`std::vector` 的元素构造会调用构造函数，对于一个 `UInt64` 来说就是 `memset`，但对于复杂类型可能触发多余初始化。PaddedPODArray 只管理裸内存，完全绕过 C++ 对象模型的开销。"

**小胖**："最后一个问题，为什么 ClickHouse 默认用 jemalloc 而不是 tcmalloc？"

**大师**："历史和生态因素。jemalloc 的线程缓存设计对大量小对象分配更友好——这正是 OLAP 查询的场景。jemalloc 的 profiling 工具也更完善，可以输出详细的分配火焰图。tcmalloc 也不差，但 jemalloc 在 Facebook、Redis 等项目中久经考验，ClickHouse 社区选择了跟随。但说实话，两者性能差距在大部分场景下不到 5%，选哪个更多是工程决策。"

---

## 3. 项目实战（约 1500-2000 字）

### 环境准备

本实验需要 ClickHouse Debug 构建以启用 memory profiling，或使用标准 release 构建配合 `system.metrics` 和 `system.query_log` 进行观测。推荐准备一台 32GB 以上内存的测试机。

```bash
# 确认 ClickHouse 的 allocator 类型
clickhouse local --version 2>&1 | grep -i "allocator\|jemalloc\|tcmalloc"
# 输出示例: ClickHouse local version 24.3.1.2672 (official build). 
#            Built with jemalloc.

# 进入 ClickHouse 客户端
clickhouse client
```

```sql
-- 查看内存相关指标
SELECT metric, value, description 
FROM system.metrics 
WHERE metric LIKE '%Memory%'
ORDER BY metric;
```

### Step 1: Arena 内存池原理

Arena 是一个极简的 Bump Allocator，源码位于 `src/Common/Arena.h`。其核心数据结构是一个单链表，每个节点是一块大内存页（Page），页面内部用指针偏移完成小对象分配。

```cpp
// src/Common/Arena.h (简化版核心逻辑)

class Arena {
public:
    // 默认页大小：避免太小导致频繁创建新页，也避免太大浪费内存
    static constexpr size_t DEFAULT_PAGE_SIZE = 4096;
    static constexpr size_t DEFAULT_GROWTH_FACTOR = 2;

    struct Page {
        Page * next;
        size_t size;          // 此页的总容量
        size_t used;          // 已使用的字节数
        char data[];          // 柔性数组，实际内存在页末尾
        
        char * alloc(size_t bytes, size_t alignment) {
            // 对齐计算
            auto aligned_offset = (used + alignment - 1) & ~(alignment - 1);
            if (aligned_offset + bytes > size) 
                return nullptr;  // 本页装不下，返回空让调用者新建页
            char * result = data + aligned_offset;
            used = aligned_offset + bytes;
            return result;
        }
    };

private:
    Page * head = nullptr;    // 链表头
    size_t page_size;         // 新建页的大小

public:
    Arena(size_t initial_page_size = DEFAULT_PAGE_SIZE)
        : page_size(initial_page_size) {}

    /// 从 Arena 分配内存，永不失败（失败会抛异常）
    char * alloc(size_t size) {
        // 快速路径：尝试在当前页分配
        if (head) {
            if (auto * result = head->alloc(size, 1))
                return result;
        }

        // 慢速路径：对象太大，超过单页容量 -> 单独分配大块
        if (size >= page_size / 2) {
            auto * large_page = reinterpret_cast<Page *>(
                ::operator new(sizeof(Page) + size));
            large_page->next = head;
            large_page->size = size;
            large_page->used = size;
            head = large_page;
            return large_page->data;
        }

        // 慢速路径：当前页满了 -> 分配新页（新页大小指数增长）
        page_size = std::min(page_size * DEFAULT_GROWTH_FACTOR, 
                             size_t(1) << 20);  // 上限 1MB/页
        auto * new_page = reinterpret_cast<Page *>(
            ::operator new(sizeof(Page) + page_size));
        new_page->next = head;
        new_page->size = page_size;
        new_page->used = 0;
        head = new_page;
        return new_page->alloc(size, 1);  // 不递归，直接在新页上分配
    }

    /// 析构时一次性释放所有页（不需要逐对象 free）
    ~Arena() {
        while (head) {
            auto * next = head->next;
            ::operator delete(head);
            head = next;
        }
    }
};

// 使用场景：聚合函数中创建大量子字符串
// 例如 groupArray 逐个追加元素时，元素内部的小字符串
// 通过 Arena 分配，查询结束时整体回收，零碎片
void example_arena_usage() {
    Arena arena(4096);
    
    // 模拟 GROUP BY 中创建 100 万个短期字符串
    for (int i = 0; i < 1'000'000; ++i) {
        char * buf = arena.alloc(64);   // O(1)，只是指针移动
        snprintf(buf, 64, "key_%d", i); // 无 malloc 调用
    }
    // arena 析构 → 所有 64MB 一次性释放
}
```

**关键洞察**：Arena 的分配是 O(1) 的（纯粹指针移动），但不会在对象生命周期结束时回收内存。这意味着——查询跑得越久，Arena 撑得越大。

### Step 2: Column 内存布局

ClickHouse 的列存储中，`ColumnVector<T>` 不使用 `std::vector<T>`，而是使用特制的 `PaddedPODArray<T>`。

```cpp
// src/Columns/ColumnVector.h (简化版)

template <typename T>
class ColumnVector final : public COWHelper<IColumn, ColumnVector<T>> {
    // 数据存储区：带头尾填充的 POD 数组
    PaddedPODArray<T> data;
    
    // ... 列接口实现
};

// PaddedPODArray 的核心特征：
// 1. 元素数量 = N 时，实际分配大小 = header_padding + N * sizeof(T) + tail_padding
// 2. header_padding: 通常在分配起始前预留 16~64 字节（对齐到 cache line）
// 3. tail_padding:  在有效元素末尾后预留 32~64 字节
//    目的：SIMD 指令每次加载 256bit(32B) 或 512bit(64B) 时，
//    即使处理最后一个元素，也不会越界读取未映射内存

// 内存布局示意图（ColumnVector<UInt64>，1M 行）：
// ┌──────────────┬──────────────────────────┬──────────────┐
// │ 头填充 (40B) │ 有效数据 (8MB = 1M × 8B) │ 尾填充 (40B) │
// └──────────────┴──────────────────────────┴──────────────┘
// 总计 ≈ 8MB + 80B

// 对比 std::vector<UInt64> 同样 1M 元素：
// ┌──────────────────────────┐
// │ 有效数据 (8MB = 1M × 8B) │  ← 无填充，SIMD 尾部访问不安全
// └──────────────────────────┘
```

```cpp
// PaddedPODArray 的核心内存分配逻辑 (src/Common/PODArray.h 简化)
template <typename T>
void PODArrayBase<T>::alloc(size_t new_size) {
    size_t padded_size = PADDING_FOR_SIMD + new_size * sizeof(T) + PADDING_FOR_SIMD;
    
    // 底层通过 Allocator 分配（即 jemalloc）
    char * raw = reinterpret_cast<char *>(Allocator::alloc(padded_size, alignment));
    
    // 预留头部填充，实际数据从 raw + PADDING_FOR_SIMD 开始
    char * payload = raw + PADDING_FOR_SIMD;
    
    // 在尾填充区写哨兵值（Debug 模式下用于检测越界写）
    memset(payload + new_size * sizeof(T), 0xEF, PADDING_FOR_SIMD);
}
```

### Step 3: MemoryTracker 层级追踪

MemoryTracker 的核心不是"精确统计"，而是"层级限流"。每次分配必须顺着父节点链向上报告。

```cpp
// src/Common/MemoryTracker.h (简化核心逻辑)

class MemoryTracker : public std::enable_shared_from_this<MemoryTracker> {
public:
    // 变量类型区分：query / user / global / merge
    enum class Variable {
        amount,  // 当前分配量
        peak,    // 历史峰值
        limit,   // 硬限制
    };

private:
    std::shared_ptr<MemoryTracker> parent;  // 父节点（不可为空指向 global）
    
    // 原子变量，确保多线程安全（无锁递增/递减）
    std::atomic<Int64> amount{0};
    std::atomic<Int64> peak{0};
    std::atomic<Int64> limit{0};
    
    // 软限制相关
    std::atomic<Int64> soft_limit{0};
    std::atomic<double> profiler_step{0};

public:
    // === 分配路径 ===
    void allocImpl(Int64 size, bool throw_if_limit_exceeded) {
        // 沿父链向上逐个增加计数器
        for (auto * tracker = this; tracker; tracker = tracker->parent.get()) {
            Int64 new_amount = tracker->amount.fetch_add(size) + size;
            
            // 更新峰值（不精确但够用——峰值统计不需要原子CAS）
            Int64 current_peak = tracker->peak.load();
            while (new_amount > current_peak) {
                if (tracker->peak.compare_exchange_weak(current_peak, new_amount))
                    break;
            }
            
            // === 超限检查 ===
            Int64 lim = tracker->limit.load();
            if (lim > 0 && new_amount > lim && throw_if_limit_exceeded) {
                // 回滚当前及父链上的计数后再抛异常
                for (auto * rollback = this; rollback != tracker; rollback = rollback->parent.get())
                    rollback->amount.fetch_sub(size);
                tracker->amount.fetch_sub(size);
                
                throw Exception(ErrorCodes::MEMORY_LIMIT_EXCEEDED,
                    "Memory limit ({}B) exceeded: would use {}B, attempt to allocate {}B",
                    lim, new_amount, size);
            }
        }
    }

    // === 释放路径 ===
    void free(Int64 size) {
        for (auto * tracker = this; tracker; tracker = tracker->parent.get())
            tracker->amount.fetch_sub(size);
    }

    // === 层级设置 ===
    void setLimit(Int64 limit_) { limit.store(limit_); }
    void setParent(std::shared_ptr<MemoryTracker> parent_) { parent = std::move(parent_); }
};
```

**层级结构示意**：

```
GlobalMemoryTracker (limit = max_server_memory_usage)
├── UserTracker "analyst" (limit = 100GB)
│   ├── QueryTracker #1  (limit = 20GB, via max_memory_usage)
│   │   ├── AggregateTransform
│   │   ├── JoinTransform
│   │   └── Arena allocations within this query
│   └── QueryTracker #2  (limit = 20GB)
│       └── MergingSortedTransform
├── UserTracker "etl"    (limit = 150GB)
│   └── QueryTracker #3  (limit = 50GB)
└── MergeBackgroundTracker (limit = merge_max_memory_usage)
```

**`checkLimits()` 调用链路**：

```
每次有意义的分配 (Column创建 / HashTable扩容 / Arena.enlarge)
  → CurrentThread::getMemoryTracker().allocImpl(size, throw_flag)
    → 沿 parent 链向上逐级检查 limit
      → 超限则抛出 MEMORY_LIMIT_EXCEEDED 异常
        → 异常被 QueryPipeline 捕获
          → 当前查询被中止，Partial 结果丢弃
```

### Step 4: 查看内存使用

```sql
-- ========== 内存全景视图 ==========
SELECT 
    metric,
    value,
    formatReadableSize(value) AS readable,
    description
FROM system.metrics
WHERE metric IN (
    'MemoryTracking',                              -- 总追踪内存
    'MemoryTrackingInBackgroundProcessingPool',    -- 后台 merge/mutation
    'MemoryTrackingForMerges',                     -- merge 专用
    'MemoryCode',                                  -- 代码段映射
    'MemoryResident'                               -- 物理内存 RSS
)
ORDER BY metric;

-- 预期输出示例：
-- MemoryTracking:                    45.20 GiB   (jemalloc 分配 + 主动追踪)
-- MemoryCode:                        2.10 GiB    (可执行文件 + 动态库映射)
-- MemoryResident:                    53.00 GiB   (操作系统视角的 RSS)
-- 
-- 未追踪内存 ≈ MemoryResident - MemoryTracking ≈ 7.8 GiB (约 15%)

-- ========== 未追踪内存 Gap 分析 ==========
SELECT 
    formatReadableSize(untracked_gap) AS untracked_memory,
    untracked_gap,
    round(100.0 * untracked_gap / resident, 1) AS untracked_pct
FROM (
    SELECT 
        maxIf(value, metric = 'MemoryResident')  AS resident,
        maxIf(value, metric = 'MemoryTracking')  AS tracked,
        resident - tracked AS untracked_gap
    FROM system.metrics
);

-- ========== 历史查询内存分析 ==========
SELECT 
    query_id,
    formatReadableSize(memory_usage)       AS mem_usage,
    query_duration_ms                      AS duration_ms,
    formatReadableSize(memory_usage / greatest(query_duration_ms / 1000.0, 0.001)) 
                                            AS mem_per_sec,
    substring(query, 1, 100)               AS query_snippet,
    event_time
FROM system.query_log
WHERE type = 'QueryFinish'
  AND memory_usage > 0
ORDER BY memory_usage DESC
LIMIT 20;

-- ========== Profile Events: 查看外部聚合是否触发了溢写 ==========
SELECT 
    query_id,
    formatReadableSize(memory_usage)  AS mem,
    ProfileEvents['ExternalAggregationWriteBytes'] > 0 AS disk_spill_occurred
FROM system.query_log
WHERE type = 'QueryFinish'
  AND event_date >= today() - 7
ORDER BY memory_usage DESC
LIMIT 20;
```

### Step 5: 外部聚合溢写磁盘

当 `GROUP BY` 的键基数极高（如 `user_id` 有 3 亿个不同值），哈希表撑到几十 GB 是常态。`max_bytes_before_external_group_by` 允许部分数据临时写到磁盘。

```sql
-- 配置外部聚合
SET max_bytes_before_external_group_by = 10000000000;  -- 10GB 触发溢写
SET max_memory_usage = 50000000000;                    -- 50GB 总限制（含磁盘 I/O 缓冲）
SET max_bytes_before_external_sort = 10000000000;      -- 排序也允许溢写
SET tmp_path = '/data/clickhouse/tmp/';                -- 临时文件路径（建议 SSD）

-- 执行一个高基数聚合
SELECT 
    user_id,
    count()                    AS events,
    sum(amount)                AS total_amount,
    uniqExact(product_id)      AS distinct_products,
    groupArray(session_id)     AS sessions             -- 内存大户
FROM orders_2024
GROUP BY user_id;
```

**内部流程**：

```
阶段1: 内存聚合
  AggregatingTransform 读入数据 → 插入哈希表
  MemoryTracker 持续监控 → 当前查询金额逼近 10GB
  
阶段2: 触发溢写
  当 amount > max_bytes_before_external_group_by:
  1. 将当前哈希表按 key 排序
  2. 序列化为排序后的 Block 写入临时文件 (tmp_path/dataXXX.bin)
  3. 清空哈希表（释放对应 Arena），但 MemoryTracker 记录溢写块的元信息
  4. 继续处理剩余数据 → 如果再次超限，重复步骤 1~3
  5. 可能会在磁盘上产生多个临时文件

阶段3: 最终归并
  所有输入数据读取完毕:
  1. 从磁盘读取所有溢写文件（流式读取，不全部加载到内存）
  2. 与内存中剩余的哈希表块做多路归并
  3. 合并相同 key 的聚合状态
  4. 输出最终结果
```

```sql
-- 查询本次溢写细节
SELECT 
    event_time,
    query_id,
    ProfileEvents['ExternalAggregationWriteRows']  AS rows_flushed,
    ProfileEvents['ExternalAggregationWriteBytes']  AS bytes_flushed,
    ProfileEvents['ExternalAggregationCompressedBytes'] AS compressed_bytes,
    ProfileEvents['ExternalProcessingUncompressedBytesLimitExceeded'] AS limit_exceeded
FROM system.query_log
WHERE type = 'QueryFinish'
  AND ProfileEvents['ExternalAggregationWriteBytes'] > 0
ORDER BY event_time DESC
LIMIT 5;
```

### Step 6: jemalloc 配置与调优

```bash
# 确认当前构建使用的 allocator
clickhouse local -q "SELECT buildId()" 
# 或
clickhouse server --version 2>&1 | head -5

# 如果从源码构建，指定 jemalloc:
# cmake -DENABLE_JEMALLOC=ON ..

# 运行时配置：通过环境变量传递给 jemalloc
# 在 /etc/clickhouse-server/config.d/memory.xml 或启动脚本中设置
```

```bash
# jemalloc 关键 MALLOC_CONF 参数说明
export MALLOC_CONF="\
background_thread:true,\          # 开启后台线程异步归还脏页 → 降低延迟抖动
dirty_decay_ms:5000,\             # 脏页保留 5 秒后开始清理（默认 10s）
muzzy_decay_ms:5000,\             # 半脏页清理间隔
prof:true,\                       # 开启 heap profiling
prof_prefix:/var/log/clickhouse-server/jeprof,\
lg_prof_sample:19,\               # 采样间隔 2^19 = 512KB
stats_print:true,\                # 定期输出 jemalloc 统计信息
metadata_thp:disabled"            # 禁用 THP，避免大页导致的内存碎片

# 启动 ClickHouse 时注入
# MALLOC_CONF="..." clickhouse server --config-file=/etc/clickhouse-server/config.xml
```

**jemalloc vs tcmalloc vs glibc malloc 对比**：

| 特性 | jemalloc | tcmalloc | glibc malloc |
|------|----------|----------|--------------|
| 线程缓存 | per-thread tcache | per-thread central free list | arena 竞争 |
| 碎片控制 | size class + slab | size class + page heap | 传统 bin 结构 |
| Profiling | 完善 (jeprof + pprof) | 完善 (gperftools) | 需外部工具 |
| 大页支持 | 自动 THP awareness | 有限 | 无 |
| 默认场景 CPU | OLAP 查询 100% | OLAP 查询 97% | OLAP 查询 60~70% |
| ClickHouse 支持 | **默认** | 编译选项 | 不推荐生产使用 |

### 测试验证

```sql
-- ========== 验证 1: 内存限制强制执行 ==========
-- 先用一个极低限制确认机制工作正常
SET max_memory_usage = 100000000;  -- 100MB

-- 执行一个一定会超过 100MB 的查询
SELECT number, repeat('x', 10000) AS padding
FROM numbers(1000000)
GROUP BY number;

-- 预期: 抛出 MEMORY_LIMIT_EXCEEDED 异常
-- 日志: "Memory limit (for query) exceeded: would use XXX GiB..."

-- ========== 验证 2: 查看实时内存 ==========
-- 在另一个 session 中持续监控
SELECT 
    metric, 
    value, 
    formatReadableSize(value)
FROM system.metrics
WHERE metric IN ('MemoryTracking', 'MemoryResident');

-- ========== 验证 3: jemalloc profiling 火焰图 ==========
-- (需编译时启用 --with-jemalloc-prof)
-- 在查询运行中:
-- sudo jeprof --show_bytes --pdf /usr/bin/clickhouse /tmp/jeprof.*.heap > /tmp/mem_flame.pdf
-- 分析哪个函数/组件是内存分配热点
```

---

## 4. 项目总结（约 500-800 字）

### ClickHouse 内存分配策略全景

| 分配类型 | 机制 | 分配速度 | 碎片 | 释放行为 |
|----------|------|---------|------|---------|
| Arena（小对象、临时） | Bump Allocator（线性分配） | 极快（一次指针偏移） | 零碎片（整页回收） | 查询结束时整体释放 |
| PaddedPODArray | jemalloc（底层） + SIMD padding | 快 | 低 | Column 析构时逐个释放 |
| std::string 小字符串 | SSO（本地 ≤15 字节直接存嵌） | 极快 | N/A | 栈上自动回收 |
| 大内存块（>1MB） | jemalloc arena 直接分配 | 正常 | 低 | 引用计数归零时 free |
| 外部聚合溢写 | mmap + 临时文件 | 慢（磁盘 I/O） | N/A | 查询结束删除临时文件 |

### 关键结论

1. **Arena 是一把双刃剑**：它让聚合函数的百万次小对象分配几乎零开销，但也意味着内存在查询期间只增不减。`groupArray`、`uniqExact` 等聚合的 State 内部大量使用 Arena，是"隐身内存"的主要来源。

2. **MemoryTracker 是"最终一致"的**：它不保证每个 `malloc` 都被即时反映，而是通过批次检查来平衡精度和性能。`MemoryResident - MemoryTracking` 的差值通常在 10%~20% 之间，这部分差值就是设置 `max_server_memory_usage` 时你必须留出的余量。

3. **jemalloc 默认即最优 90%**：Thread-cache、slab、background thread 三件套已经能覆盖大多数场景。除非你有非常特殊的分配模式（如极端多的小字符串、极大量的大块分配），否则不要轻易调 `MALLOC_CONF`。

### 生产环境推荐配置

```
总物理内存 256GB:
  max_server_memory_usage = 230GB  (~90%, 预留 26GB 给 OS + 未追踪内存)
  max_memory_usage         = 180GB  (~70% 总内存, 单查询上限)
  max_bytes_before_external_group_by = 20GB  (聚合哈希表超过 20GB 开始溢写)
  max_bytes_before_external_sort     = 20GB  (排序超过 20GB 溢写)
  background_pool_size     = 16     (根据 CPU 核数)
  MALLOC_CONF: "background_thread:true,dirty_decay_ms:5000"
  
总物理内存 64GB:
  max_server_memory_usage = 55GB   (~86%)
  max_memory_usage        = 40GB   (~62%)
  max_bytes_before_external_group_by = 10GB
```

### 常见踩坑经验

1. **`max_memory_usage` 设为总内存的 90%，仍然 OOM** → 忘了预留 10-15% 的未追踪内存，再加上 OS 的其他进程开销。

2. **Arena 内存"泄漏"** → 其实不是泄漏，是长时间运行的查询（如复杂 CTE/子查询）让 Arena 一直持有大量内存不释放。解决方案：拆分成小查询、启用中间结果缓存。

3. **启用外部聚合后查询变慢几十倍** → 溢写磁盘是正确行为，但 HDD 的随机 I/O 可能成为瓶颈。务必使用 SSD 存放 `tmp_path`，并监控 `ProfileEvents['ExternalAggregationWriteBytes']`。

4. **jemalloc background_thread 没有生效** → 需要 jemalloc 5.0+ 编译，且 `MALLOC_CONF` 必须在进程启动前设置（通过环境变量或 `/etc/security/limits.conf`），无法在运行时动态修改。

### 思考题

1. **为什么 ClickHouse 要同时使用 Arena 和 jemalloc？两者各有什么优劣？**

   *提示：Arena 解决"高频小对象分配"的速度问题（O(1) vs O(logN)），jemalloc 解决"不定生命周期大对象"的碎片问题。两者分工不同，并非互相替代。*

2. **如果系统有 256GB RAM，`max_server_memory_usage` 设置为多少比较合理？为什么？**

   *提示：需要考虑 OS 文件缓存（Linux 会尽可能用空闲内存做页缓存）、线程栈空间（每线程 ~8MB）、未追踪的 mmap 区域、以及 ClickHouse 自身以外的进程。建议不超过 230GB，留下 10%+ 做安全垫。*

