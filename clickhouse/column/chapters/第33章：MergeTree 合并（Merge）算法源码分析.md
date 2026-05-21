# 第33章：MergeTree 合并（Merge）算法源码分析

> **版本**：ClickHouse 25.x LTS
> **定位**：高级篇核心章节。深入 MergeTree 内部机制——从 SimpleMergeSelector 的成本函数到 Horizontal/Vertical Merge 的执行路径，再到 Mutation 的"伪装合并"本质，完整拆解 Merge 从选择到落盘的全链路源码逻辑。
> **前置阅读**：第4章（MergeTree引擎与分区策略）、第5章（一级索引与稀疏索引）、第12章（数据部件与宽窄表设计）、第15章（TTL机制）、第26章（性能调优）
> **预计阅读**：50 分钟 | **实战耗时**：90 分钟

---

## 1. 项目背景

某数据平台团队维护着一套6节点ClickHouse集群，承载公司的实时数仓业务。数据来源包括20+套Kafka Topic，从用户行为埋点到服务端日志到IoT传感器数据，每天入湖约30亿行。为了追求最低的写入延迟，各业务方普遍采用高频小批次写入策略——每1秒甚至每500毫秒刷一批数据进来，每批几百行到几千行不等。

问题在第三个月集中爆发。监控面板上`system.parts`的活跃部件数量从月初的8000个一路飙升到20万个，其中部分高吞吐表（如`events_log`和`ad_impressions`）各自贡献了超过3万个活跃Part。运维组首先注意到的是查询P99延迟的异常：某个原本稳定在50毫秒的运营报表查询，开始间歇性地飙到2秒、3秒甚至5秒。业务方投诉"看板动不动就转圈"，数据团队的排查结论是——**Part数量过多**。

为什么Part多会导致查询变慢？因为MergeTree的查询引擎需要打开每一个匹配到主键索引范围的Part，逐一读取其`primary.idx`索引文件、逐一执行稀疏索引过滤、然后对命中的Granule执行并归读取。当一张表有3万个Part，而查询只命中了20个Granule的时候，引擎却需要先打开3万个文件来检查"你有没有我要的数据"。这种文件描述符的爆炸性消耗和元数据遍历开销，是查询降速的根本原因。

数据团队尝试手动运行`OPTIMIZE TABLE events_log FINAL`，结果发现这操作直接阻塞了对该表的所有写入——业务方的实时数据管道开始积压Kafka Lag，问题反而更严重。他们意识到一个残酷的事实：**不了解Merge的选择策略、预算控制和执行机制，调优Merge行为就是盲人摸象。** 什么时候触发合并？为什么有些表的合并速度跟不上写入速度？TTL合并和常规合并谁更优先？`ALTER TABLE DELETE`底层到底是什么？这些问题不搞清楚，就只能永远靠"重启服务器碰运气"。

CTO下了死命令：**一周内定位根因，两周内解决Part膨胀问题。** 这就是本章要交付的知识——从`SimpleMergeSelector`的成本函数出发，到`MergeTask`的任务调度，再到`Horizontal/Vertical Merge`的两条执行路径，逐行拆解MergeTree合并算法的源码实现。

---

## 2. 项目设计：剧本式交锋对话

周五下午，大师把小白和小胖叫到白板前。白板上画着一棵倒置的LSM-Tree——叶子节点是大量小Part，经过Merge逐层合并为更少的大Part。

**小胖**（把背包往桌上一扔，满脸不耐烦）："合并？不就把几个小文件合成一个大文件吗？这有什么好分析的？操作系统层面不就是`cat file1.bin file2.bin file3.bin > big_file.bin`吗？我昨天还手动把20个Part用OPTIMIZE合并成一个，花了两分钟——有什么技术含量？"

**大师**（从兜里掏出一把U盘，在桌上摆成一排）："20个Part你用两分钟。那3万个Part呢？你能OPTIMIZE到什么时候？而且你在OPTIMIZE的时候表被锁了，业务方的Kafka积压了800万条，你知道吗？"

**小胖**（愣住）："锁……锁住了？我说怎么运营群昨天在刷屏说数据停了半小时。"

**大师**："对。所以今天我们要搞清楚的不只是'合并是什么'，而是**什么时候该合并、选哪些Part合并、怎么合并、花多少资源合并、合并失败怎么重试**。这五件事，在ClickHouse约60万行的MergeTree源码里各占一份。我们重点拆三个文件：`SimpleMergeSelector.cpp`决定选谁合并，`MergeTreeDataMerger.cpp`负责调度执行，`MergeAlgorithm`决定怎么合。"

**技术映射 #1**：MergeTree的合并本质上是**LSM-Tree（Log-Structured Merge-Tree）理论的列存实践**。LSM-Tree的核心思想是"写时顺序追加，读时归并多版本"——写入全走内存MemTable，满了就刷成一个不可变的SSTable文件（在ClickHouse里就是Part）。当SSTable数量超过阈值，后台启动Compaction将多个小SSTable合并为一个大SSTable，减少查询需要打开的文件数。ClickHouse把这个Compaction叫作**Merge**。关键区别在于：传统LSM-Tree（如RocksDB）的Merge是有损的——合并时可以丢弃旧版本Key；而ClickHouse的常规Merge是**无损的**——只是重组数据，不删不改行。Mutation才是有损的特殊Merge。

---

**小白**（在平板电脑上翻着ClickHouse源码，推了推眼镜）："大师，我读了`SimpleMergeSelector.h`，有个困惑。这个Selector的核心逻辑是给每一对相邻Part算一个分数，分数最低的组合被选中合并。分数计算包含了大小比、年龄差异、层级差三个因子。我的问题是——**为什么要倾向合并大小相近的Part？** 如果总是合并最大Part和最小Part，不是也能减少Part总数吗？"

**大师**（拿起两支白板笔，一支长一支短，把它们并排放在一起）："你看这两支笔。想象它们代表两个Part——短的是1MB，长的是1GB。如果你把它们合并，你得到的是一个1.001GB的Part。但在这个过程中，你需要把1GB的Part全部读一遍、再全部写一遍——有效工作量只有1MB，浪费了99.9%的I/O。而且合并出来的新Part和原来最大的那个Part大小几乎一样——并没有让Part大小变得更均衡，下一轮合并还得把它再读一次。"

"现在想象你合并两个500MB的Part——得到1GB。两个输入大小相当，都值得'退休'，合并产出一个有意义的更大的文件。这就是**成本效率**——SimpleMergeSelector的score函数本质上是在计算'每单位I/O能消除多少个Part'。合并大小相近的Part，能用最小I/O把Part数量砍半。"

大师在白板上写下公式：

```cpp
// src/Storages/MergeTree/SimpleMergeSelector.cpp
// 核心成本函数（简化版）

double SimpleMergeSelector::score(double size_left, double size_right,
                                   double age_left, double age_right,
                                   size_t level_left, size_t level_right)
{
    // 因子1：大小比 —— 接近1最优（两个Part大小相当）
    double size_penalty = (size_left + size_right) / std::max(size_left, size_right);
    // 两个相等的Part → 2.0（最优）
    // 一个1MB一个1GB → ≈ 1.001（很差，几乎不合并也行）

    // 因子2：年龄 —— 老Part优先（TTL数据需要被清理）
    double max_age = std::max(age_left, age_right);
    double age_penalty = max_age / config.base;
    // 年龄越大，age_penalty越大，score越低 → 被优先选中

    // 因子3：层级差 —— 同层优先（维持平衡树结构）
    double level_diff = std::abs(int(level_left) - int(level_right));
    double level_penalty = level_diff * config.level_step;
    // level_diff=0 → penalty=0（最优）
    // level_diff=5 → penalty=5*2=10（很差）

    return size_penalty + age_penalty + level_penalty;
}
// 最终算法：选 score 最低的组合
```

**技术映射 #2**：SimpleMergeSelector维护了一个**Part层级（Level）**概念——每次成功合并，新Part的层级 = max(输入Part层级) + 1。插入的原始Part（未经合并）层级为0。因此层级近似于"这个Part经历过多少次合并"。这个设计参考了RocksDB的Universal Compaction——始终保持同一层级的Part大小在同一个量级，避免"一代Part"和"三代Part"混着合导致写放大。如果你见过一棵平衡归并树（如外部排序的锦标赛树），你会发现每个节点的两个子节点大小相近——SimpleMergeSelector做的正是动态维护这棵归并树的形状。

---

**小白**（笔飞快地记着，抬起头）："那TTL Merge呢？我们表上设了`TTL event_time + INTERVAL 30 DAY DELETE`，这些过期行是怎么被清理的？是一个独立的删除进程，还是也是Merge的一种？"

**大师**（赞许地看着小白）："问到关键了。TTL Merge**就是Merge的一种特殊形式**，不是什么独立的垃圾回收线程。区别在于——常规Merge在读每个Part的时候是'全读全写'，TTL Merge在读每个Part的时候额外加了一个过滤器：跳过TTL过期的行。这套计算还做了优化——在Granule级别判断，如果整个Granule都过期了（通过记录Granule的最大/最小TTL值），整个Granule会被直接丢弃，连解压都不用。"

```cpp
// 伪代码：TTL Merge的读取逻辑
void readWithTTL(const MergeTreeDataPart & part, const TTLDescription & ttl)
{
    for (auto & granule : part.getGranules())
    {
        // 快速路径：整个Granule都过期了 → 整块跳过
        if (granule.max_ttl_value < ttl.expiration_time)
        {
            skipped_granules++;
            continue;  // 零I/O！连解压都不做
        }

        // 慢速路径：Granule内有部分行过期 → 逐行检查
        auto block = part.readGranule(granule);
        for (auto & row : block)
        {
            if (row[ttl_column] < ttl.expiration_time)
                filtered_rows++;
            else
                output_block.append(row);
        }
    }
}
```

**小胖**（突然插嘴）："等等，那`ALTER TABLE ... DELETE WHERE ...`呢？这不也是删数据吗？也是Merge？"

**大师**："完全正确。`ALTER TABLE DELETE`在ClickHouse里叫**Mutation（突变）**。它是一种特殊的Merge——每个Part被单独拿出来，读出来，用DELETE谓词过滤掉要删的行，再写回成一个新Part。当这个分区的所有Part都被Mutation处理完以后，旧Part被标记删除，Mutation才算完成。"

"这里有个容易被误解的地方：DELETE不是立刻生效的！Mutation命令会被写入ZK（或Keeper），成为一个持久化的异步任务。后台的Mutation线程逐个Part地执行重写。如果表有1000个Part，Mutation就得跑1000次独立的重写——每次都是读一个Part、过滤、写一个新Part。这解释了为什么在大表上执行DELETE可能要花几个小时，也解释了为什么`SELECT * FROM table`在Mutation完成之前，有些Part能看到删除后的数据，有些Part还能看到删除前的数据——因为旧Part要等整个分区的Mutation都完成才会被统一删除。"

**技术映射 #3**：ClickHouse的Mutation设计体现了**不可变数据**的哲学——存储文件一旦写入就不再修改。DELETE不是在原文件上打个"已删除"标记，而是生成一个全新的、不含被删除行的文件。这个设计牺牲了DELETE性能（重写成本高），但换来了读取路径的极致简洁——读一个Part不需要同时读一个"删除位图"来过滤行，文件本身就是完整的。

---

## 3. 项目实战

### 环境准备

```bash
# 拉取 ClickHouse 源码（推荐 25.x 版本）
git clone https://github.com/ClickHouse/ClickHouse.git
cd ClickHouse
git checkout v25.3-lts

# Debug 构建（方便 GDB 断点到 Merge 函数）
mkdir build && cd build
cmake -DCMAKE_BUILD_TYPE=Debug -DENABLE_TESTS=ON ..
ninja -j$(nproc)

# 启动本地 ClickHouse 实例（方便随时修改日志级别）
./build/programs/clickhouse-server --config=config.xml &
```

### Step 1：理解 Merge 选择算法——SimpleMergeSelector

`SimpleMergeSelector`是ClickHouse最核心的Merge选择策略。它的输入是一组已排序的活跃Part列表，输出是一组"值得合并的Part组合"。整个算法的本质是一道**带约束的优化题**——在有限资源下，选哪些Part合并能最大化"Part数量减少量 / I/O消耗量"这个比值。

```cpp
// 源码路径: src/Storages/MergeTree/SimpleMergeSelector.h
// 核心数据结构

class SimpleMergeSelector
{
public:
    struct Part
    {
        size_t size;        // Part 字节大小
        size_t age;         // Part 创建以来的时间（秒）
        size_t level;       // Merge层级（0=原始insert, 1=经过一次merge...）
        size_t rows;        // 行数
    };

    struct Settings
    {
        // 基础分数计算参数
        double base = 5.0;                  // 年龄计算的分母——越大年龄影响越小
        double size_fixed_cost_to_add = 0;  // 固定I/O成本（打开文件的开销）
        double level_step = 2.0;            // 层级差的惩罚系数

        // 触发阈值
        size_t min_parts_to_merge = 2;      // 至少合并几个Part
        size_t max_parts_to_merge = 300;    // 单次合并最多300个Part

        // TTL相关
        double ttl_base = 2.0;              // TTL过期Part的优先级权重
        double ttl_deprecated_start_ratio = 0.5;
    };

    // 核心算法：从part列表中选择最优合并组合
    PartsRange select(
        const PartArrayView & parts,      // 按(min_block, max_block)排序的Part列表
        const Settings & settings
    );
};
```

**select() 算法的执行流程（简化）：**

```cpp
PartsRange SimpleMergeSelector::select(const PartArrayView & parts,
                                        const Settings & settings)
{
    // Phase 0: 如果Part太少，直接不合并
    if (parts.size() < settings.min_parts_to_merge)
        return {};  // 空结果 → 不触发Merge

    // Phase 1: 检查是否有 TTL 过期的 Part
    // TTL Merge 优先级最高——过期数据必须尽快清理
    for (size_t begin = 0; begin < parts.size(); ++begin)
    {
        if (isTTLExpired(parts[begin]))
        {
            // 扫描连续TTL过期的Part区间
            size_t end = begin + 1;
            while (end < parts.size() && isTTLExpired(parts[end])
                   && end - begin < settings.max_parts_to_merge)
            {
                ++end;
            }
            // TTL合并：无层级约束，凑够一批就合
            return PartsRange{begin, end};
        }
    }

    // Phase 2: 常规Merge —— 用成本函数评估所有可能的区间
    // 滑动窗口扫描：检查 [begin, end) 区间是否值得合并
    double best_score = std::numeric_limits<double>::max();
    PartsRange best_range;

    for (size_t begin = 0; begin < parts.size(); ++begin)
    {
        // 跳过层级太高的Part（说明已经是大Part，不值得再合）
        if (parts[begin].level > max_level)
            continue;

        for (size_t end = begin + settings.min_parts_to_merge;
             end <= std::min(begin + settings.max_parts_to_merge, parts.size());
             ++end)
        {
            // 计算该区间的合并成本
            // 核心公式：score = Σ[log(size_ratio)] + Σ[age_penalty] + Σ[level_diff]
            double score = computeCost(parts, begin, end, settings);

            // 成本是否在预算范围内？
            if (score < best_score && score < settings.max_score_to_merge)
            {
                best_score = score;
                best_range = PartsRange{begin, end};
            }
        }
    }

    return best_range;  // 可能为空——表示"现在不值得合并"
}
```

**关键洞察**：
1. TTL Merge 优先级最高且不关心Part大小均衡——因为过期数据存着就是浪费磁盘，越早清理越好。
2. 常规Merge严格按成本函数排序——如果当前所有候选组合的成本都高于阈值（`max_score_to_merge`），就**不合并**，等更多Part累积后再看。
3. 滑动窗口限制了单次Merge的Part数量上限（默认300个），防止一次合并吃掉所有I/O。

### Step 2：Merge 触发条件——从调度到执行

选择了合并候选Part后，下一步就是调度执行。触发Merge的入口在`MergeTreeDataMerger.cpp`。

```cpp
// 源码路径: src/Storages/MergeTree/MergeTreeDataMerger.cpp

bool MergeTreeDataMerger::scheduleMergeTask(bool force)
{
    // 层级1: Check —— 触发条件判断

    // 条件A: Part总数是否超过阈值？
    // max_parts_in_total 默认 = 300
    if (data.getAllDataParts().size() > data.settings.max_parts_in_total)
    {
        LOG_WARNING(log, "Too many parts ({} > {}). Force merge.",
                    data.getAllDataParts().size(),
                    data.settings.max_parts_in_total);
        // 触发紧急合并 —— 优先级最高，不受预算限制
        force = true;
    }

    // 条件B: 后台合并池是否已满？
    // max_background_pool_size 控制同时运行的Merge任务数（默认16）
    bg_pool_size = getScheduledBackgroundPoolSize();
    if (bg_pool_size >= data.settings.max_background_pool_size)
    {
        LOG_TRACE(log, "Background pool full ({} >= {}). Skip merge scheduling.",
                  bg_pool_size, data.settings.max_background_pool_size);
        return false;  // 别再加了，线程池满了
    }

    // 条件C: 合并预算（I/O burst）是否耗尽？
    if (!canUseFreeBurst())
    {
        LOG_TRACE(log, "Merge budget exhausted. Skip.");
        return false;
    }

    // 层级2: Select —— 选择合并候选
    // 实例化 SimpleMergeSelector，传入当前活跃Part列表
    SimpleMergeSelector selector;
    auto parts_to_merge = selector.select(
        data.getActiveDataParts(),    // 所有活跃Part
        merge_selector_settings      // 合并策略参数
    );

    if (parts_to_merge.empty())
    {
        LOG_TRACE(log, "No good merge candidates found.");
        return false;  // 没有值得合并的组合
    }

    // 层级3: Execute —— 创建并提交MergeTask
    auto merge_task = std::make_shared<MergeTask>(
        MergeTreeData::DataPartsVector(parts_to_merge.begin(), parts_to_merge.end()),
        getMergeAlgorithm(parts_to_merge)  // Horizontal or Vertical?
    );

    // 提交到后台线程池（基于 ThreadPool 实现）
    background_pool.scheduleOrThrowOnError(
        [this, merge_task]() {
            executeMergeTask(merge_task);
        }
    );

    LOG_INFO(log, "Scheduled merge: {} parts → 1 part, algorithm={}",
             parts_to_merge.size(),
             merge_task->getAlgorithmName());

    return true;
}
```

**触发时序图（简化）：**

```
每 N 秒 → backgroundSchedulePool定时唤醒
    │
    ▼
scheduleMergeTask()
    │
    ├─ Part数 > max_parts_in_total? ────Yes──▶ force=true（紧急合并）
    │
    ├─ 后台线程池满? ────────────────Yes──▶ return false（等下一轮）
    │
    ├─ Merge预算耗尽? ────────────────Yes──▶ return false（等下一轮）
    │
    ├─ SimpleMergeSelector.select() ──空结果──▶ return false
    │
    └─ 提交 MergeTask 到 background_pool 执行
```

### Step 3：Horizontal Merge vs Vertical Merge——两条执行路径

这是Merge算法中最精妙的设计维度。ClickHouse根据表的列数自动选择合并策略。

```cpp
// 源码路径: src/Storages/MergeTree/MergeTask.cpp

enum class MergeAlgorithm
{
    Horizontal,  // 默认策略：所有列一起读写（适合窄表）
    Vertical     // 列分组策略：按列分组分别读写（适合宽表）
};

MergeAlgorithm chooseMergeAlgorithm(const DataPartsVector & parts)
{
    size_t total_columns = parts[0]->getColumns().size();

    // threshold 默认 = 0 — 需要显式配置 enable_vertical_merge_algorithm
    if (total_columns >= settings.enable_vertical_merge_algorithm_threshold)
    {
        return MergeAlgorithm::Vertical;
    }
    return MergeAlgorithm::Horizontal;
}
```

**Horizontal Merge（默认，窄表专用）：**

```cpp
// Horizontal Merge 的执行流程（简化）
// 适用于列数 < 阈值的表（默认 threshold = 0，即默认不启用 Vertical）

void mergePartsHorizontal(const DataPartsVector & parts, const MergeTreeDataPart & result)
{
    // Step 1: 为每个输入Part打开读取流
    std::vector<MergeTreeReaderPtr> readers;
    for (const auto & part : parts)
    {
        readers.push_back(part->getReader(/* all columns */));
    }

    // Step 2: 使用 MergeSorter 对所有Part的数据做归并排序
    // 按 ORDER BY key 做 K-way merge（K = Part数量）
    MergingSortedBlockInputStream merger(
        readers,
        sorting_key,           // ORDER BY (event_time, event_type)
        merge_max_block_size   // 每次输出8192行的Block
    );

    // Step 3: 流式写入结果Part
    auto writer = result->getWriter();
    while (true)
    {
        Block block = merger.read();  // 从多个Part流式归并出下一个Block
        if (!block)
            break;

        // 逐列写入（Column-by-Column Write）
        // 每列独立压缩 → 独立的 .bin 文件
        writer->write(block);
    }

    // Step 4: 写入元数据文件和校验和
    writer->finalize();  // 生成 checksums.txt, columns.txt, count.txt

    // Step 5: 原子替换：新Part就绪后添加到活跃集合，旧Part标记删除
    data.renameTempPartAndReplace(result_part, old_parts);
}

// Horizontal Merge 的 I/O 特征：
// - 读：全部列 × 全部Part → 大内存压力
// - 写：全部列 × 1个新Part → 连续大块写，磁盘友好
// - 适用：列数 ≤ 50 的表
```

**Vertical Merge（宽表专用，>100列）：**

```cpp
// Vertical Merge 的执行流程（简化）
// 适用于列数 >= enable_vertical_merge_algorithm_threshold 的表

void mergePartsVertical(const DataPartsVector & parts, const MergeTreeDataPart & result)
{
    size_t total_columns = parts[0]->getColumns().size();

    // Step 1: 将列分成多个组（每组大约 ~30 列）
    auto column_groups = splitColumnsIntoGroups(parts[0]->getColumns(),
                                                  settings.vertical_merge_group_size);

    // Step 2: 逐组处理——每个列组独立完成一次归并+写入
    for (size_t group_idx = 0; group_idx < column_groups.size(); ++group_idx)
    {
        const auto & group_columns = column_groups[group_idx];

        // Phase A: 打开读取流 —— 只读取本组的列！
        std::vector<MergeTreeReaderPtr> readers;
        for (const auto & part : parts)
        {
            // 只读当前组的列，而不是全部列
            readers.push_back(part->getReader(group_columns));
        }

        // Phase B: K-way merge sort —— 依然按 ORDER BY key 排序
        // 但我们只关心排序键列 + 当前组的列
        MergingSortedBlockInputStream merger(readers, sorting_key, block_size);

        // Phase C: 写入结果Part的当前组列
        auto writer = result->getWriter(group_columns);
        while (true)
        {
            Block block = merger.read();
            if (!block) break;
            writer->write(block);
        }
        writer->finalize();
    }

    // Step 3: 所有列组处理完毕后，组装最终Part
    result->assemble();  // 汇总各组checksums，生成完整元数据
    data.renameTempPartAndReplace(result_part, old_parts);
}

// Vertical Merge 的 I/O 特征：
// - 读：每组列 × 全部Part → 每次内存压力小
// - 写：每组列 × 1个临时文件 → 多次小写，磁盘碎片化
// - 适用：列数 ≥ 100 的表（如500列的用户画像宽表）
```

**Horizontal vs Vertical 对比表：**

| 维度 | Horizontal Merge | Vertical Merge |
|------|-----------------|----------------|
| 内存压力 | 高（所有列同时在内存） | 低（每次只加载一组列） |
| I/O模式 | 一次大块顺序写 | 多次小块顺序写 |
| 适用场景 | 窄表（< 50列） | 宽表（≥ 100列） |
| 默认启用 | 是 | 否，需配置阈值 |
| 磁盘碎片 | 低 | 中等 |

### Step 4：Mutation——ALTER DELETE/UPDATE 的特殊 Merge 实现

很多人误以为`ALTER TABLE ... DELETE WHERE ...`是某种"标记删除"操作，实际上它是**逐Part重写**。每个Part被完整读出来、过滤掉被删的行、再写回一个新Part。

```cpp
// 源码路径: src/Storages/MergeTree/MergeTreeDataMutationExecutor.cpp
//            src/Storages/MergeTree/MutationCommands.cpp

// Stage 1: 提交Mutation命令
void MergeTreeData::mutate(const MutationCommands & commands)
{
    // 命令写入 ZooKeeper / Keeper
    // 路径: /clickhouse/tables/{table_id}/mutations/{mutation_id}
    auto mutation_id = zk->createSequential(
        mutations_path + "/",
        commands.serializeToString()
    );

    // 为每个Part创建一个Mutation任务条目
    for (const auto & part : getDataParts())
    {
        zk->create(
            mutations_path + "/" + mutation_id + "/parts/" + part->name,
            ""  // 空条目 = 尚未处理此Part
        );
    }
}

// Stage 2: 后台Mutation线程逐Part处理
class MutationExecutor
{
public:
    void execute(Block & block, const MutationCommands & commands)
    {
        for (const auto & cmd : commands)
        {
            switch (cmd.type)
            {
                case MutationCommand::Type::DELETE:
                {
                    // 计算DELETE谓词：WHERE user_id < 1000
                    auto filter = evaluateExpression(cmd.predicate, block);
                    // 过滤掉为 false 的行
                    block = filterBlock(block, filter);
                    break;
                }

                case MutationCommand::Type::UPDATE:
                {
                    // UPDATE table SET status = 'inactive'
                    for (const auto & assignment : cmd.assignments)
                    {
                        auto new_column = evaluateExpression(
                            assignment.expression, block
                        );
                        block.setColumn(assignment.column_index, new_column);
                    }
                    break;
                }

                case MutationCommand::Type::MATERIALIZE_COLUMN:
                {
                    // ALTER TABLE ... MATERIALIZE COLUMN
                    // 从默认表达式计算并物化列的物理存储
                    auto column = evaluateExpression(cmd.expression, block);
                    block.insert(column);
                    break;
                }
            }
        }
    }
};

// Stage 3: 判断Mutation是否完成
bool MergeTreeData::isMutationDone(const String & mutation_id)
{
    // 检查 ZooKeeper 中该Mutation下的Part条目
    // 如果所有Part都已被处理 → Mutation完成
    // 如果还有Part未被处理 → Mutation进行中
    auto parts_remaining = zk->getChildren(
        mutations_path + "/" + mutation_id + "/parts"
    );

    if (parts_remaining.empty())
    {
        // 所有Part处理完毕，清理旧版本Part
        // 旧Part从磁盘删除，Mutation标记为已完成
        zk->setStatus(mutation_id, "done");
        return true;
    }
    return false;
}
```

**Mutation 监控 SQL：**

```sql
-- 查看当前有哪些Mutation在进行
SELECT
    database,
    table,
    mutation_id,
    command,
    create_time,
    formatReadableSize(parts_to_do) AS remaining_parts,
    is_done
FROM system.mutations
WHERE is_done = 0
ORDER BY create_time;

-- 查看具体某个Mutation的进度
-- parts_to_do 从大到小递减 → Mutation在推进
-- parts_to_do 卡住不动 → 某个Part被长期查询锁住无法合并
```

**Mutation的三大关键特性：**

1. **非阻塞写入**：Mutation在后台异步执行，正常的INSERT不受影响。新插入的数据会成为新Part——这些Part**不包含被Mutation删除的行**（因为INSERT时已经应用了Mutation条件）。
2. **非原子可见**：在Mutation完成之前，有些Part已经被重写（删除了数据），有些Part还是原样。此时查询可能返回不一致的结果——旧Part中的被删行和新Part中没有被删行并存。
3. **Part粒度执行**：Mutation的单位是Part，不是行。这就是为什么"UPDATE 1行"也需要重写整个Part——因为Part是不可变的二进制文件，不能局部修改。

### Step 5：控制 Merge 行为——核心配置参数

```sql
-- ===== 场景A：写入高峰需要加速合并 =====
-- 生产环境慎用！加速合并意味着更多I/O被合并占用
ALTER TABLE events_log MODIFY SETTING
    max_bytes_per_sec = 0,                  -- 合并I/O不限制速度
    number_of_free_bursts = 10,            -- 允许10个并发合并
    merge_max_block_size = 16384,          -- 每次合并处理更大的Block
    min_age_to_force_merge_seconds = 0,    -- 不等待Part变老，立即合并
    max_parts_in_total = 100000;           -- Part总数超过10万才强制合并

-- ===== 场景B：查询高峰需要减速合并 =====
ALTER TABLE events_log MODIFY SETTING
    max_bytes_per_sec = 104857600,         -- 合并限速 100MB/s
    number_of_free_bursts = 1,            -- 最多1个并发合并
    max_parts_in_total = 300000,          -- 放宽Part上限（牺牲查询性能保写入）
    min_age_to_force_merge_seconds = 3600, -- Part必须存在1小时后才能被合并
    old_parts_lifetime = 600;             -- 旧Part在被合并后保留10分钟再删除

-- ===== 场景C：宽表启用Vertical Merge =====
-- user_profile 表有 450 列，Horizontal Merge 内存爆炸
ALTER TABLE user_profile MODIFY SETTING
    enable_vertical_merge_algorithm_threshold = 100;  -- 超过100列启用Vertical Merge
```

**关键参数速查表：**

| 参数 | 默认值 | 含义 | 调优建议 |
|------|--------|------|----------|
| `max_bytes_per_sec` | 0 (不限) | 合并操作的I/O速度上限 | 查询高峰设为100-200MB/s |
| `number_of_free_bursts` | 1 | 初始并发合并任务数 | 高写入量表可设为3-5 |
| `max_parts_in_total` | 300 | Part总数阈值（超过就强制合并） | 写入量大的表可调高到10000 |
| `min_age_to_force_merge_seconds` | 0 | Part最小的强制合并年龄 | 设为3600可防"合并风暴" |
| `merge_max_block_size` | 8192 | 每次合并处理的行数 | 内存充裕可调大到16384 |
| `old_parts_lifetime` | 480 | 旧Part保留时间(秒) | 对正在运行的查询提供保护 |
| `enable_vertical_merge_algorithm_threshold` | 0 | Vertical Merge的列数阈值（0=禁用） | 宽表设为100-200 |

### Step 6：调试 Merge 行为

**方法一：开启 Merge 专项 Trace 日志**

```xml
<!-- config.xml 中添加 -->
<clickhouse>
  <logger>
    <level>information</level>
    <levels>
      <!-- 这三个Logger覆盖了Merge的全链路日志 -->
      <MergeTreeData>trace</MergeTreeData>
      <MergeTreeDataMerger>trace</MergeTreeDataMerger>
      <SimpleMergeSelector>trace</SimpleMergeSelector>
    </levels>
  </logger>
</clickhouse>
```

重启后，在 `clickhouse-server.log` 中会看到类似输出：

```
2026-04-30 14:23:11.123 [ BGSchPool ] <Trace> MergeTreeDataMerger: 
  Evaluating merge for table events_log (2231 active parts)

2026-04-30 14:23:11.125 [ BGSchPool ] <Trace> SimpleMergeSelector: 
  Scoring: parts[1024..1026] size_ratio=1.97 age=342 level_diff=0 → score=2.31

2026-04-30 14:23:11.126 [ BGSchPool ] <Trace> SimpleMergeSelector: 
  Best range: parts[1024..1026] with score=2.31

2026-04-30 14:23:11.127 [ BGSchPool ] <Debug> MergeTreeDataMerger: 
  Scheduled merge: 3 parts → 1 part, algorithm=Horizontal
```

**方法二：通过 system.merges 实时监控**

```sql
SELECT
    database,
    table,
    elapsed,
    formatReadableSize(bytes_read_uncompressed) AS data_read,
    formatReadableSize(total_size_bytes_compressed) AS result_size,
    rows_read,
    rows_written,
    round(progress * 100, 2) AS pct_complete,
    num_parts,
    is_mutation,              -- 1 = Mutation, 0 = 常规Merge
    memory_usage,
    thread_id
FROM system.merges
ORDER BY elapsed DESC;
```

**方法三：从 query_log 追溯历史合并**

```sql
SELECT
    event_time,
    query_duration_ms,
    formatReadableSize(memory_usage) AS mem,
    substring(query, 1, 120) AS merge_info
FROM system.query_log
WHERE type = 'MergeTask'
  AND event_date >= today() - 3
ORDER BY event_time DESC
LIMIT 50;
```

### 测试验证

**测试1：观察Part累积 → 自动合并**

```sql
-- Session 1: 创建测试表
CREATE TABLE merge_test (
    event_time DateTime,
    user_id UInt64,
    event_type String
) ENGINE = MergeTree()
ORDER BY (event_time, user_id)
SETTINGS
    min_rows_for_wide_part = 0,
    min_bytes_for_wide_part = 0,
    max_parts_in_total = 20;  -- 设低阈值让合并更容易触发

-- Session 2: 监控Part数量变化
SELECT count() AS active_parts
FROM system.parts
WHERE database = 'default'
  AND table = 'merge_test'
  AND active = 1;

-- Session 1: 模拟高频小批次写入（每批5行）
INSERT INTO merge_test SELECT
    now() - number * 60,
    rand64() % 10000,
    ['page_view','click','scroll','purchase'][rand() % 4 + 1]
FROM numbers(5);

-- 重复执行 50 次
-- 观察 system.merges → 会自动触发合并
-- 观察 system.parts → active_parts 在触达20后开始下降
```

**测试2：观察Mutation执行**

```sql
-- 插入测试数据
INSERT INTO merge_test
SELECT now() - number * 3600, number, 'page_view'
FROM numbers(100000);

-- 提交 DELETE Mutation
ALTER TABLE merge_test DELETE WHERE user_id % 2 = 0;

-- 监控 Mutation 进度
SELECT
    mutation_id,
    command,
    create_time,
    parts_to_do,
    is_done
FROM system.mutations
WHERE table = 'merge_test' AND database = 'default'
ORDER BY create_time DESC;
-- parts_to_do 从 N → N/2 → ... → 0
-- is_done 最终会变成 1
```

---

## 4. 项目总结

### Merge 类型全景对比

| Merge 类型 | 触发条件 | 数据操作 | I/O 成本 | 阻塞写入？ | 可手动触发？ |
|-----------|---------|---------|---------|-----------|------------|
| **常规 Merge** | Part数量/大小超过阈值 | 归并排序，无损重组 | 中等 | 否 | 否（后台自动） |
| **TTL Merge** | Part中包含TTL过期的行 | 过滤掉过期行 | 低（Granule级跳过） | 否 | 否 |
| **Mutation Merge** | `ALTER TABLE DELETE/UPDATE` | 逐Part过滤/重写 | 高（每个Part全量重写） | 否 | 是（提交即触发） |
| **OPTIMIZE FINAL** | 手动命令 | 分区内全部Part合一 | 非常高 | 是（锁分区写入） | — |

### 适用场景

- **排查查询变慢**：通过`system.parts`确认是否Part数量过大 → 检查`max_parts_in_total`和合并速度是否匹配写入速度。
- **写入密集型业务调优**：适当提高`max_parts_in_total`和`number_of_free_bursts`，用更多并发合并换取写入吞吐。
- **查询密集型业务调优**：限制`max_bytes_per_sec`和降低`number_of_free_bursts`，把I/O留给查询。
- **宽表（>100列）优化**：启用Vertical Merge（设置`enable_vertical_merge_algorithm_threshold`），避免合并时内存溢出。
- **Mutation诊断**：查看`system.mutations`中`parts_to_do`是否在持续下降，如果卡住说明某个Part被长期查询锁定。

### 注意事项

1. **Merge是I/O密集型操作**——每次合并都要把多个Part的全部列读一遍再写一遍新Part。在SSD上这是顺序I/O，影响相对可控；在HDD上是随机读+顺序写，性能很差。
2. **`OPTIMIZE TABLE ... FINAL`是重型武器**——它会锁住整个分区、暂停所有写入、等待所有后台Merge完成后强制将分区所有Part合并为一个。生产环境中只应在维护窗口执行，且要做好耗时预估（10TB分区可能需要数小时）。
3. **Mutation不保证原子可见性**——在Mutation完成之前，新旧数据可能同时存在。业务逻辑如果有严格的"删除后立即可见"需求，需要在应用层通过`system.mutations`确认`is_done=1`后再让用户查询。
4. **Merge的"免费爆发"机制（free bursts）**：`number_of_free_bursts`允许系统在初始时有额外的并发合并配额。但burst用完后，合并速度会回落到`max_background_pool_size`限制的水平，导致"刚开始合得快，后面越来越慢"——这是设计特性，不是Bug。

### 常见踩坑经验

**坑1：大量并发INSERT → Part数爆炸 → Merge风暴 → 查询雪崩**

这是最常见的ClickHouse生产事故链。200个Kafka Consumer线程同时写入，每个线程每秒提交一个Batch，每个Batch生成一个新Part。60秒内累积12000个Part。后台Merge疯狂启动但跟不上写入速度，查询引擎面对海量Part直接超时。**解法**：降低Consumer并发度 + 增大每个Batch的`max_insert_block_size`让每次写入的Part更大 + 提高`number_of_free_bursts`。

**坑2：在查询高峰期执行OPTIMIZE TABLE导致其他查询超时**

OPTIMIZE会锁分区，且其合并操作不受`max_bytes_per_sec`限制，可能瞬间吃掉所有磁盘I/O。**解法**：永远在业务低峰期执行`OPTIMIZE`，且执行前先`ALTER TABLE ... MODIFY SETTING max_bytes_per_sec = 52428800`（限速50MB/s）。

**坑3：Mutation卡住——某个Part被长期查询锁定**

如果一个查询跑了30分钟（比如分析师的即席查询），它持有的Part快照会阻止Mutation线程对这些Part的重写——因为ClickHouse不允许在Part正在被读取时删除它。**解法**：设置`max_execution_time`限制长查询 + 查看`system.mutations`确认卡住的Part属于哪个查询 + 必要时`KILL QUERY`释放Part锁。

**坑4：TTL Merge不触发——以为设了TTL数据就会自动过期**

TTL Merge本质还是Merge，必须等待Merge调度器选中过期Part。如果Part数量很少（比如表通过OPTIMIZE合并成了一个），TTL Merge可能很久都不会触发，因为唯一的Part已经很大，Merge调度器觉得不值得合并。**解法**：设置`merge_with_ttl_timeout`参数，强制在指定时间内至少触发一次TTL合并。

### 思考题

1. **为什么SimpleMergeSelector倾向合并大小相近的Part？如果总是合并最大和最小的Part会怎样？**
   
   合并大小相近的Part是最优的I/O利用方式——合并两个500MB Part产出1GB，有效I/O和产出I/O比例接近1:1。如果总是合并一个1MB Part和一个1GB Part，你用了1.001GB的I/O但只消除了1MB的碎片——写放大接近1000倍。长期这样的策略会导致大的Part被反复重写而小的Part始终得不到合并，最终Part数量不会有效减少，写放大失控。

2. **如果一个表有100万行，UPDATE了其中1行，为什么需要重写整个Part而不仅仅是那一行？**

   因为MergeTree Part是不可变的二进制文件，物理存储是列式压缩后的`.bin`文件。在压缩数据中"原地修改一行"是不可能的——LZ4/ZSTD压缩的块必须整体解压、修改、再压缩。更根本的理由是ClickHouse的设计哲学：**读路径零负担**。如果允许Part内行级修改，每次读取时都需要额外维护一个"修改位图"来跳过旧行，这会拖慢所有查询。用写放大（重写整个Part）换取读路径的极致简洁，这是ClickHouse的核心取舍。

---

> **下一章预告**：第34章——《ReplicatedMergeTree 的数据一致性与 Quorum 写入》，将深入分析 ClickHouse 分布式副本如何通过 ZooKeeper/Keeper 实现多副本一致性，包括 Quorum 写入的两阶段提交协议、Leader 选举、以及副本间数据同步的队列机制。掌握这些知识后，你将能定位和解决生产环境中最棘手的"副本不同步"和"插入丢失"问题。
