# 第34章：查询执行引擎——Pipeline 与向量化计算

> **版本**：ClickHouse 25.x LTS
> **定位**：高级篇核心章节。从一条SQL语句出发，逐层拆解ClickHouse如何将文本转换为并行CPU指令——Pipeline调度、向量化执行、SIMD加速，三把钥匙打开查询执行引擎的黑盒。
> **前置阅读**：第7章（主键与稀疏索引）、第26章（性能调优——从配置到内核）、第27章（查询级性能剖析）
> **预计阅读**：40 分钟 | **实战耗时**：60 分钟

---

## 1. 项目背景

某广告平台的性能工程师阿磊最近接了一个诡异的性能Case。业务方抱怨一条简单的聚合查询太慢——`SELECT count(), sum(amount) FROM orders WHERE created_at >= '2025-01-01'`，数据量约1GB，却跑了整整5秒。阿磊的第一反应是"磁盘I/O瓶颈"——他们用的是NVMe SSD，顺序读写速度在3GB/s以上，按道理1GB数据不到0.4秒就能读完。那剩下的4.6秒去哪了？

阿磊在查询前后各打了一个时间戳，用`send_logs_level='trace'`把日志全量抓了下来。日志显示：I/O等待只有400毫秒——与预估相符。但FilterTransform的work()方法累计耗时2.1秒，AggregatingTransform又吃掉1.8秒，MergingAggregatedTransform的合并阶段单线程跑了1.2秒。三者加起来正好是4.9秒——**瓶颈不在磁盘，在CPU**。

"CPU明明才跑了40%，怎么还是慢？"阿磊把监控曲线放大，发现了端倪：这是一个16核的机器，但这4.9秒的计算中至少有3秒是**单线程串行执行**的——Aggregation的合并阶段和最终的排序都是单线程。CPU使用率不高只是因为大部分核心在**等那个单线程阶段结束**。

这让阿磊想起了一个经典比喻：一条八车道高速公路，收费站只有一个窗口——车再多也得排成一列慢慢过。ClickHouse的Pipeline也是如此：Source阶段可以开8个线程并行读，Filter可以开8个线程并行过滤，但到了MergeAggregation阶段，所有线程的结果要汇总到一个线程去合并，这个"收费站"就是瓶颈。

问题的根源在于：如果你不理解ClickHouse的Pipeline执行模型——数据如何流经一个个Processor、哪个阶段可以并行、哪个阶段强制串行、向量化到底在哪里发挥作用——你就无法解释为什么一个查询明明数据量不大却跑得慢，为什么`max_threads`从8改成64查询反而更慢，为什么同样的机器跑某些查询CPU吃满而另一些查询CPU在"摸鱼"。

本章将带你深入ClickHouse查询执行引擎的核心——从Parser到Pipeline再到向量化计算，彻底搞懂一条SQL如何变成高效（或不高效）的并行CPU指令。

---

## 2. 项目设计：剧本式交锋对话

周五下午，大师在会议室白板上画了一条流水线，从左到右依次写着：**Parser → Analyzer → Planner → Builder → Executor**。小胖趴在桌上刷手机，小白已经在笔记本上画起了图。

**小胖**（抬头瞄了一眼白板）："查询不就是解析SQL然后查数据吗？MySQL的火山模型我熟——一行一行往上吐，吐到最上层出结果。ClickHouse搞什么Pipeline、向量化，不就是换了个名字？"

**大师**（合上笔记本）："小胖，我问你一个场景。你有一张10亿行的表，`SELECT count() FROM t WHERE a > 10`。MySQL的火山模型怎么跑？"

**小胖**："走索引扫全表呗，每行调一次`next()`，判断`a > 10`，符合条件的count++。10亿次函数调用，大概跑个几十秒到几分钟。"

**大师**："ClickHouse呢？同样10亿行，它不会一行一行处理。它把数据切成一个个Block——每个Block默认65536行——然后对整块数据做向量化处理。10亿行 ÷ 65536 = 大约15258个Block，只有15258次函数调用。而且65536行连续排列在内存里，CPU的L1/L2缓存命中率极高。再加上SIMD指令——一条`_mm256_cmpgt_epi64`可以同时对4个64位整数做比较——理论上把比较操作又压缩了4倍。这就是为什么ClickHouse的count()通常毫秒级出结果。"

**技术映射 #1**：火山模型（Volcano Model）的核心特征是**一次一行**（Row-at-a-Time），每次`next()`调用伴随虚函数开销、分支预测失败和缓存不友好。ClickHouse的向量化模型（Vectorized Model）是**一次一块**（Block-at-a-Time），将控制流开销均摊到65536行上，同时为CPU的指令级并行（ILP）和数据级并行（SIMD）创造了最佳条件。这不是"换名字"，是执行范式的根本转变。

---

**小白**（放下笔，推了推眼镜）："向量化我理解了——批量处理。但Pipeline是什么？Source、Transform、Sink这些概念听起来像数据流图。那它是静态的还是动态的？查询执行的时候，这些Processor之间的数据怎么传递？会不会出现上游生产太快、下游消费不过来的情况？"

**大师**点头："问得好，这恰恰是Pipeline设计的精髓。Pipeline确实是一个数据流图——你可以把它想象成工厂流水线。Source是投料工位（从MergeTree读数据），Transform是加工工位（过滤、聚合、排序），Sink是包装工位（输出结果）。数据以Block为单位在工位之间流转。"

大师在白板上画了一张图：

```
MergeTreeSource × 8  →  FilterTransform × 8  →  AggregatingTransform × 8
                                                         ↓
                                              MergingAggregatedTransform × 1
                                                         ↓
                                                  SortingTransform × 1
                                                         ↓
                                                      NullSink
```

"每个Processor都是一个独立的执行单元。关键来了——**Processor之间的数据传递不是推模式，也不是拉模式，而是协作式调度**。每个Processor实现了两个核心方法：`prepare()`和`work()`。"

"`prepare()`是'探路'——Processor检查自己能不能干活。它的输入端口有数据吗？输出端口有空位吗？如果输入没数据，返回`Status::NeedData`，告诉调度器'我没粮了，去喂上一个'。如果输出端口满了，返回`Status::PortFull`，告诉调度器'我堵住了，让下游先消费'。只有当输入有数据、输出有空位时，才返回`Status::Ready`——这时候调度器才会调用`work()`真正做计算。"

**小白**追问："这种协作式调度不会引入大量检查开销吗？而且既然每个Processor有自己的节奏，会不会出现死锁？"

**大师**："死锁是个真实问题。比如A的输入连着B的输出，A返回NeedData、B返回PortFull——两者互相等待。ClickHouse的调度器解决这个问题的方式是：**全局轮询**。每轮调度，对所有Processor调用一次`prepare()`，收集所有Ready的Processor，然后依次调用它们的`work()`。一轮结束再来下一轮。当没有任何Processor返回Ready、也没有任何Processor有未完成的工作时，Pipeline执行完成。这个算法的正确性依赖于一个关键约束：**每次work()调用必须推进部分计算，不能空转，且必须在极短时间内返回（通常<1ms）**，否则其他Processor就会被饿死。"

**技术映射 #2**：协作式调度（Cooperative Scheduling）和抢占式调度（Preemptive Scheduling）的核心区别在于：前者由Processor主动让出控制权（通过返回非Ready状态），后者由操作系统强制切换。协作式调度的优势是零上下文切换开销和确定性的执行流，代价是某个Processor的work()耗时过长会拖慢整个Pipeline。这就是为什么ClickHouse的work()实现强调"每次只处理一个Block"——65k行处理完马上返回，绝不恋战。

---

**小胖**突然坐直了："等等，你刚才画的图上，MergingAggregatedTransform只有1个线程，而AggregatingTransform有8个线程。为什么不把合并也搞成多线程？8个线程的输出汇总到一个线程，这不就成了瓶颈吗？"

**大师**（赞许地看了小胖一眼）："抓到关键点了。这就是**Pipeline并行度的阿喀琉斯之踵**——聚合的合并阶段、排序、Limit截断，都是天然串行的操作。多线程合并聚合结果之所以难，是因为你要保证合并结果全局有序、全局正确。如果用多线程合并，就变成了归并排序的并行化——要引入额外的数据分区和协调开销。ClickHouse的设计哲学是：**并行能做好的就并行，必须串行的接受串行，用Pipeline可视化工具找瓶颈，针对性优化。**"

**小胖**："那如果我发现合并阶段是瓶颈，怎么办？"

**大师**："三种思路。一：启用`distributed_aggregation_memory_efficient=1`，这个参数让ClickHouse在内存不足时把部分聚合结果溢写到磁盘，降低单线程合并的内存压力——虽然不直接并行化合并，但能缓解合并导致的背压。二：在分布式场景下，合并工作被分摊到各个分片节点——每个分片先做本地合并，发起节点只合并分片级别结果，压力被稀释。三：也是最重要的——如果你发现合并是瓶颈，说明你的查询本身聚合基数太高。考虑用物化视图把聚合前置到写入阶段，查询时直接读预聚合结果。"

**技术映射 #3**：Pipeline的并行度由两个因素决定——数据分区的物理并行度（Part数量决定Source能开多少线程）和计算的逻辑串行度（算法本身决定Transform能不能并行）。理解这两个因素的博弈，是Pipeline优化的全部。

---

**小白**翻开笔记本："最后一个问题——SIMD具体是怎么在向量化执行中发挥作用的？是ClickHouse的手写SIMD代码，还是编译器自动向量化？"

**大师**："两者都有。ClickHouse的核心数据结构`PaddedPODArray`——也就是Column的底层存储——保证了数据在内存中16字节对齐，这是SIMD高效工作的前提。编译器开启`-msse4.2 -mavx2`编译选项后，很多循环会被自动向量化。比如这个过滤循环：`for (size_t i = 0; i < n; i++) { if (col[i] > threshold) result.push_back(i); }`——编译器会尝试把它编译成AVX2的`_mm256_cmpgt_epi64`指令，一次比较4个64位整数。"

"但编译器自动向量化并不总是最优的。ClickHouse在一些性能关键路径上手写了SIMD intrinsic代码。最典型的是`src/Common/StringUtils.h`中的字符串操作——`isAllASCII()`函数用AVX2 intrinsic一次检查32字节。还有聚合函数中的sum、avg实现，调用了`__builtin_prefetch`预取指令来提高缓存命中率。"

"还有一点很多人不知道——ClickHouse在启动时会用`CPUID`指令检测CPU支持的指令集，然后**动态选择最优的代码路径**。比如你的CPU支持AVX-512，ClickHouse会走AVX-512的实现；如果只支持SSE4.2，就走SSE4.2的实现。这意味着同一份二进制部署在不同代CPU上，性能会自适应。"

**小胖**："原来如此。那我总结一下——Parser把SQL变成AST，Analyzer解析语义，Planner生成逻辑执行计划，Builder把逻辑计划变成物理Pipeline，最后Executor调度执行。Pipeline里每个阶段是一个Processor，通过prepare/work协作调度，数据按Block（65536行）批量流转，SIMD在底层加速。对吧？"

**大师**笑了："基本正确。再加一句你就出师了——**真正的高手不看查询跑多快，而是看Pipeline里哪个Processor在拖后腿。**"

---

## 3. 项目实战

### 环境准备

启动一个带调试能力的ClickHouse实例，开启trace日志以便观察Pipeline执行细节：

```bash
# 启动ClickHouse容器，允许追踪日志
docker run -d --name ch-pipeline-lab \
  --cpus=8 --memory=16g \
  -p 8123:8123 -p 9000:9000 \
  clickhouse/clickhouse-server:25

# 进入容器
docker exec -it ch-pipeline-lab bash

# 修改日志级别为trace（仅测试用，生产慎用）
cat > /etc/clickhouse-server/config.d/log_trace.xml << 'EOF'
<clickhouse>
    <logger>
        <level>trace</level>
    </logger>
</clickhouse>
EOF

# 重启使配置生效（Docker容器内可省略）
# 不重启，后续用 send_logs_level='trace' 即可
```

创建测试数据：

```sql
-- 创建一张足够大的表，模拟生产数据
CREATE TABLE pipeline_demo
(
    id          UInt64,
    user_id     UInt32,
    status      Enum8('active'=1, 'inactive'=2, 'pending'=3),
    amount      Decimal(10, 2),
    created_at  DateTime,
    category    LowCardinality(String)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(created_at)
ORDER BY (user_id, created_at);

-- 批量插入测试数据（5000万行）
INSERT INTO pipeline_demo
SELECT
    number AS id,
    rand() % 100000 AS user_id,
    (rand() % 3 + 1)::Enum8('active'=1, 'inactive'=2, 'pending'=3) AS status,
    toDecimal64(randCanonical() * 1000, 2) AS amount,
    toDateTime('2025-01-01') + (rand() % 31536000) AS created_at,
    ['electronics','clothing','food','books','sports'][rand() % 5 + 1] AS category
FROM numbers(50000000);
```

---

### Step 1: 查询处理全流程——从SQL文本到物理执行

**目标**：理解 `executeQuery()` 函数的六个阶段，建立完整的心智模型。

这是ClickHouse查询执行引擎的总入口——`src/Interpreters/executeQuery.cpp`。我们把它拆解成六个阶段（代码为简化示意，非完整实现）：

```cpp
// src/Interpreters/executeQuery.cpp (simplified)
void executeQuery(
    const String & query,
    ContextPtr context,
    QueryProcessingStage::Enum stage)
{
    // ============ Phase 1: Parse ============
    // SQL text → AST (Abstract Syntax Tree)
    // src/Parsers/
    ASTPtr ast = parseQuery(parser, query, context->getSettingsRef().max_query_size);

    // "SELECT count() FROM t WHERE a > 10"
    // 解析后变成一棵树：
    //   ASTSelectQuery
    //   ├── ASTFunction('count')       ← select expression
    //   ├── ASTTables('t')             ← from clause
    //   └── ASTWhere('a > 10')         ← where clause
    //       └── ASTFunction('greater')
    //           ├── ASTIdentifier('a')
    //           └── ASTLiteral(10)


    // ============ Phase 2: Analyze ============
    // AST → Semantic Analyzed AST
    // src/Analyzer/
    auto analyzed = analyzeQuery(ast, context);
    // 关键工作：
    // - 解析表名：'t' → StorageMergeTree (从 system.tables 查找)
    // - 解析列名：'a' → ColumnUInt64 (从 system.columns 查找)
    // - 解析函数：'count()' → AggregateFunctionCount
    // - 类型检查：'a > 10' → a(UInt64) > 10(UInt64) ✓


    // ============ Phase 3: Plan ============
    // Analyzed AST → QueryPlan (logical plan)
    // src/Planner/
    auto planner = Planner(analyzed, context);
    QueryPlan plan = planner.buildQueryPlan();
    // 逻辑计划是一棵树：
    //   QueryPlan
    //   └── Aggregation (count(), group by: [])
    //       └── ReadFromMergeTree
    //           filter: a > 10
    //           columns: [a]


    // ============ Phase 4: Build Pipeline ============
    // QueryPlan → Physical Pipeline
    // src/QueryPipeline/
    auto builder = QueryPipelineBuilder(plan);
    QueryPipeline pipeline = builder.buildPipeline();
    // 物理执行计划是一个DAG（有向无环图）：
    //   [MergeTreeSource × 8 threads]
    //       ↓
    //   [FilterTransform × 8 threads]
    //       ↓
    //   [AggregatingTransform × 8 threads]
    //       ↓
    //   [MergingAggregatedTransform × 1 thread]
    //       ↓
    //   [NullSink]
    // 并行度由 plan 中的 max_parallel_replicas 和 max_threads 共同决定


    // ============ Phase 5: Execute ============
    // Run the pipeline
    // src/Processors/Executors/PipelineExecutor.cpp
    PipelineExecutor executor(pipeline);
    executor.execute(context->getSettingsRef().max_threads);
    // executor.execute() 内部是一个循环：
    // while (has_work) {
    //    for (auto & proc : processors) {
    //        status = proc->prepare();
    //        if (status == Ready) proc->work();
    //    }
    // }


    // ============ Phase 6: Return Results ============
    return executor.getResult();
}
```

**六个阶段的职责总结**：Parser负责"翻译"——把人类读的SQL变成机器读的树；Analyzer负责"理解"——把符号绑定到实际的表和列；Planner负责"规划"——制定逻辑执行策略；Builder负责"落地"——把策略变成可执行的物理工序；Executor负责"执行"——调度所有工序协同工作；最后一步负责"交付"——把结果返回给客户端。

---

### Step 2: IProcessor接口——协作式调度的核心协议

**目标**：理解`IProcessor`的`prepare()`与`work()`如何实现高效的协作式调度。

```cpp
// src/Processors/IProcessor.h

class IProcessor {
public:
    // ---- 核心状态机 ----
    // prepare() 返回 Status，告诉调度器当前处于什么状态
    enum class Status {
        Ready,      // 有数据也有空间，可以立即调用 work()
        NeedData,   // 输入端口没有数据，等待上游投喂
        PortFull,   // 输出端口已满，等待下游消费
        Finished,   // 不再产生数据，处理器已完成工作
        Async       // 等待异步操作（如网络I/O），稍后重试
    };

    // 调度器每次循环都调用此方法检查状态
    virtual Status prepare() = 0;

    // 仅在 prepare() 返回 Ready 时被调用
    // 执行一次计算，处理一个 Block（65536行）
    virtual void work() = 0;

    // 端口访问
    InputPorts  & getInputs();   // 输入端口集合
    OutputPorts & getOutputs();  // 输出端口集合

protected:
    // 端口定义
    class Port {
        bool isConnected() const;
        bool hasData() const;   // 对于 InputPort
        bool isFull() const;    // 对于 OutputPort
        Chunk peek();           // 预览数据（不消耗）
        Chunk pull();           // 取走数据（消耗）
        void push(Chunk data);  // 写入数据
        void finish();          // 标记结束
    };
};
```

**调度算法的核心逻辑**（简化版）：

```cpp
// src/Processors/Executors/PipelineExecutor.cpp
void PipelineExecutor::execute(size_t num_threads) {
    // 创建线程池
    ThreadPool thread_pool(num_threads);

    while (true) {
        bool has_work = false;

        // 第一遍：收集所有 Ready 的 Processor
        std::vector<IProcessor*> ready_queue;
        for (auto & proc : processors) {
            auto status = proc->prepare();

            if (status == IProcessor::Status::Ready) {
                ready_queue.push_back(proc.get());
                has_work = true;
            }
            // NeedData / PortFull / Async → 本轮跳过，下一轮重试
            // Finished → 处理器退出调度循环
        }

        if (!has_work) break;  // 所有处理器都 Finished 了

        // 第二遍：并行执行所有 Ready 的 Processor
        // 每个 work() 被提交到线程池
        for (auto & proc : ready_queue) {
            thread_pool.scheduleOrThrow([proc]() {
                proc->work();  // 处理一个 Block
            });
        }
        thread_pool.wait();  // 等待本轮所有任务完成
    }
}
```

**一个具体的 FilterTransform 实现示例**：

```cpp
// src/Processors/Transforms/FilterTransform.cpp (simplified)

class FilterTransform : public IProcessor {
public:
    FilterTransform(Block header, const ActionsDAG & actions)
        : IProcessor({header}, {header})  // 1 input port + 1 output port
        , expression(std::make_shared<ExpressionActions>(actions))
    {}

    Status prepare() override {
        auto & input  = inputs.front();
        auto & output = outputs.front();

        // 检查三个退出条件
        if (input.isFinished() && !input.hasData())
            return Status::Finished;     // 上游没数据了

        if (!input.hasData())
            return Status::NeedData;     // 等上游投喂

        if (output.isFull())
            return Status::PortFull;     // 下游来不及消费

        return Status::Ready;            // 万事俱备
    }

    void work() override {
        auto & input  = inputs.front();
        auto & output = outputs.front();

        // 拉一个 Block（最多 65536 行）
        Block block = input.pull();

        // 对整个 Block 做过滤——向量化！
        // expression->execute() 内部对每一列调用的都是批量处理函数
        expression->execute(block);

        // 获取过滤结果列（UInt8，0/1标记）
        auto & filter_column = block.getByName(filter_column_name).column;
        const auto & filter_data = assert_cast<const ColumnUInt8 &>(*filter_column).getData();

        // 根据过滤器重新排列所有列（保留 filter_data[i]==1 的行）
        // 这里使用了 PaddedPODArray 的批量操作，CPU缓存友好
        for (size_t i = 0; i < block.columns(); i++) {
            block.getByPosition(i).column =
                block.getByPosition(i).column->filter(filter_data, count_ones);
        }

        // 推给下游
        output.push(block);
    }
};
```

**协作式调度的关键洞察**：
- 每个`work()`调用只处理一个Block，耗时通常在微秒到毫秒级
- 如果某个Processor的work()耗时过长（比如复杂表达式计算），可以通过**嵌套Pipeline**把一个大Processor拆成多个小Processor
- `Async`状态是为外部I/O设计的——比如从远程分片拉取数据，Processor会把fd注册到epoll，返回Async让度线程

---

### Step 3: 向量化执行——Block-at-a-Time vs Row-at-a-Time

**目标**：用代码对比展示向量化执行相对于传统行式执行的性能优势。

```cpp
// ==========================================
// 传统行式执行（MySQL/PostgreSQL风格）
// ==========================================
// 伪代码，展示核心思想

uint64_t count = 0;
// 火山模型的 next() 循环：每次处理一行
while (true) {
    Row row = storage_engine.next();  // 虚函数调用，每行一次！
    if (row.is_end()) break;

    if (row.getInt64("a") > 10) {    // 行级别的值提取
        count++;                       // 简单的累加
    }
}
// 问题分析：
// 1. 10亿行 = 10亿次 next() 虚函数调用 → 分支预测频繁失败
// 2. 每行的列值分散在内存中 → CPU缓存频繁miss
// 3. 编译器无法自动向量化 → 每行独立处理，无法利用SIMD


// ==========================================
// ClickHouse 向量化执行
// ==========================================

// Step 1: 数据按 Block 组织
struct Block {
    std::vector<ColumnWithTypeAndName> columns;
    // 每个 Column 内部是一个连续内存数组
    // 例如 ColumnUInt64 底层是 PaddedPODArray<UInt64> —— 一块连续的64位整数数组
};

// Step 2: 按Block批量处理（一次处理65536行）
class VectorizedFilter {
public:
    void work() {
        // 拉取一个Block（65536行 × N列）
        Block block = source->read();

        // 获取"a"列的底层数组——连续内存！
        auto & col_a = block.getByName("a").column;
        const auto & data = assert_cast<const ColumnUInt64 &>(*col_a).getData();
        // data 就是 const UInt64* —— 一个指向65536个连续UInt64的指针！

        size_t rows = data.size();  // 65536

        // 批量过滤：编译器会把下面的循环自动向量化为SIMD指令
        // 在AVX2下：_mm256_cmpgt_epi64 一次比较4个UInt64
        // 在AVX-512下：_mm512_cmpgt_epi64 一次比较8个UInt64
        ColumnUInt8::Container filter_result(rows);
        for (size_t i = 0; i < rows; ++i) {
            filter_result[i] = (data[i] > 10) ? 1 : 0;
        }

        // 应用过滤——同样批量操作
        Block filtered = block.filter(filter_result);
        sink->write(filtered);
    }
};

// 关键性能差异：
//
// | 维度               | 行式 (Row-at-a-Time)      | 向量化 (Block-at-a-Time)  |
// |--------------------|---------------------------|---------------------------|
// | 函数调用次数       | N行 = N次           | N/65536 ≈ 15k次（10亿行） |
// | CPU缓存局部性      | 差（行数据跨列分散）       | 好（列数据连续排列）       |
// | 分支预测           | 差（每行判断一次）         | 好（循环内分支稳定）       |
// | SIMD加速           | 几乎不可能                 | 编译器自动向量化+手写SIMD |
// | 每次处理的元数据开销| N次（列类型检查等）  | 1次（整个Block共享元数据） |
```

**SIMD 自动向量化详解**：

```cpp
// 以下代码在 Release 编译（-O2 -mavx2）下会被编译器自动向量化

// 示例：列求和 sum(amount)
// ClickHouse 源码路径：src/AggregateFunctions/AggregateFunctionSum.h
template <typename T>
void addMany(const T * __restrict ptr, size_t count, T & result) {
    // __restrict 告诉编译器：ptr 指向的内存不会被其他指针别名
    // 这给了编译器更大的向量化自由

    T local_sum = 0;
    for (size_t i = 0; i < count; ++i) {
        local_sum += ptr[i];
    }
    result += local_sum;

    // 编译器生成的 SIMD 版本（伪汇编）：
    //   __m256i sum_vec = _mm256_setzero_si256();
    //   for (i = 0; i < count; i += 4) {
    //       __m256i data_vec = _mm256_loadu_si256(&ptr[i]);  // 一次加载4个值
    //       sum_vec = _mm256_add_epi64(sum_vec, data_vec);   // 一次加4个值
    //   }
    //   // 横向归约：把 SIMD 向量里的4个值加起来
    //   result += horizontal_add(sum_vec);
}

// ClickHouse 运行时检测 CPU 指令集
// src/Common/TargetSpecific.h
namespace TargetSpecific {
    enum Level {
        SSE2,
        SSE42,
        AVX,
        AVX2,
        AVX512F
    };

    // 启动时检测
    Level getCurrentLevel() {
        // CPUID 指令检测
        if (cpu_has_feature(CPUFeature::AVX512F))
            return Level::AVX512F;
        if (cpu_has_feature(CPUFeature::AVX2))
            return Level::AVX2;
        // ...
        return Level::SSE2;
    }

    // 根据检测结果，dispatch到最优实现
    // 例如 AggregateFunctionSum 有多个版本：
    //   sumImplSSE42() → 128bit SIMD
    //   sumImplAVX2()  → 256bit SIMD
    //   sumImplAVX512F() → 512bit SIMD
}
```

---

### Step 4: EXPLAIN PIPELINE 可视化

**目标**：学会用 `EXPLAIN PIPELINE` 观察查询的并行度和串行瓶颈。

```sql
-- 生成带图形信息的Pipeline
EXPLAIN PIPELINE graph=1
SELECT
    toStartOfHour(created_at) AS hour,
    status,
    count() AS cnt,
    sum(amount) AS total_amount
FROM pipeline_demo
WHERE created_at >= '2025-06-01'
GROUP BY hour, status
ORDER BY hour, status
FORMAT TSV;
```

**预期输出解读**：

```
┌─EXPLAIN─────────────────────────────────────────────────────┐
│ (Expression)                                                │
│ ExpressionTransform                                        │
│   (OrderBy)                                                 │
│   ExpressionTransform                                      │
│     MergingSortedTransform  × 1    ← 单线程排序合并！        │
│       (Aggregating)                                         │
│       MergingAggregatedTransform × 1 ← 单线程聚合合并！     │
│         AggregatingTransform × 8   ← 8线程并行聚合          │
│           (Expression)                                      │
│           ExpressionTransform × 8  ← 8线程表达式计算         │
│             (Filter)                                        │
│             FilterTransform × 8   ← 8线程并行过滤           │
│               (ReadFromMergeTree)                           │
│               MergeTreeSource × 8 ← 8线程并行读            │
└─────────────────────────────────────────────────────────────┘
```

**Pipeline 诊断要点**：

从输出中可以立即发现瓶颈——凡是 `× 1` 的阶段，就是串行执行，是潜在的性能瓶颈。在这个查询中：
- `MergeTreeSource × 8`：并行度高，取决于表的Part数量
- `FilterTransform × 8`：继承了上游的并行度
- `AggregatingTransform × 8`：8线程并行聚合，各线程独立维护自己的哈希表
- `MergingAggregatedTransform × 1`：**单线程合并8个哈希表的结果**——常见瓶颈！
- `MergingSortedTransform × 1`：**ORDER BY 的排序合并也是单线程**

---

### Step 5: 定位Pipeline瓶颈

**目标**：使用系统表和分析工具定位Pipeline中的实际瓶颈。

```sql
-- 方法1：使用 query_log 查看查询在各阶段的资源消耗
-- 先开启追踪
SET send_logs_level = 'trace';

-- 执行目标查询
SELECT
    toStartOfHour(created_at) AS hour,
    status,
    count() AS cnt,
    sum(amount) AS total_amount
FROM pipeline_demo
WHERE created_at >= '2025-06-01'
GROUP BY hour, status
ORDER BY hour, status;

-- 查看最近的慢查询剖析
SELECT
    query,
    query_duration_ms,
    read_rows,
    read_bytes,
    memory_usage,
    thread_count,
    -- 关键：如果 thread_count 很高但 query_duration_ms 也很高，
    -- 说明存在串行瓶颈，多线程没有帮上忙
    query_duration_ms / NULLIF(thread_count, 0) AS ms_per_thread_ratio
FROM system.query_log
WHERE type = 'QueryFinish'
  AND query_duration_ms > 1000
ORDER BY event_time DESC
LIMIT 10;
```

**方法2：定位不同查询类型的Pipeline差异**：

```sql
-- 类型A：纯过滤查询（无聚合）—— 高度并行
EXPLAIN PIPELINE graph=1
SELECT * FROM pipeline_demo
WHERE created_at >= '2025-06-01' AND status = 'active'
FORMAT TSV;

-- 类型B：聚合 + ORDER BY —— 合并阶段串行瓶颈
EXPLAIN PIPELINE graph=1
SELECT status, count(), sum(amount)
FROM pipeline_demo
WHERE created_at >= '2025-06-01'
GROUP BY status
ORDER BY status
FORMAT TSV;

-- 类型C：聚合 + ORDER BY + LIMIT —— LIMIT也是串行
EXPLAIN PIPELINE graph=1
SELECT status, count() AS cnt
FROM pipeline_demo
WHERE created_at >= '2025-06-01'
GROUP BY status
ORDER BY cnt DESC
LIMIT 10
FORMAT TSV;
```

对比这三种查询的Pipeline图，你会发现：
- 类型A：每个阶段都是 `× 8`，几乎完美并行
- 类型B：`MergingAggregatedTransform × 1` 和 `MergingSortedTransform × 1` 是串行
- 类型C：多了个 `LimitsCheckingTransform × 1` —— LIMIT也是单线程

**常见瓶颈与对策**：

| 瓶颈阶段 | 表现 | 解决方案 |
|----------|------|----------|
| MergingAggregatedTransform | 聚合基数高时合并慢 | 物化视图预聚合；或 `distributed_aggregation_memory_efficient=1` |
| SortingTransform | 大数据量排序慢 | 减少排序数据量（先聚合再排序）；`optimize_read_in_order=1` 利用主键有序读取 |
| LimitTransform | 大表扫全表再截断 | `distributed_push_down_limit=1` 将LIMIT下推到分片 |
| MergeTreeSource | Part太少导致并行度低 | 增加Part数量（调整分区粒度）；使用 `max_threads` 调整读取并行度 |

---

### Step 6: 性能剖析——插入统计代码

**目标**：在Processor的`work()`方法中插入耗时统计，精确定位每个阶段的CPU时间占比。

```cpp
// 插入统计代码的 InstrumentedProcessor（演示用，非生产）
// 你可以通过修改 ClickHouse 源码或在子类中重写 work() 来实现

class InstrumentedProcessor : public IProcessor {
public:
    InstrumentedProcessor(std::unique_ptr<IProcessor> inner, const String & name)
        : inner_processor(std::move(inner))
        , stage_name(name)
    {}

    Status prepare() override {
        return inner_processor->prepare();
    }

    void work() override {
        // ---- 统计开始 ----
        auto start = std::chrono::high_resolution_clock::now();

        // 执行原始 work()
        inner_processor->work();

        // ---- 统计结束 ----
        auto end = std::chrono::high_resolution_clock::now();
        auto elapsed_us = std::chrono::duration_cast<std::chrono::microseconds>(end - start).count();

        // 累加到全局统计
        stats.total_us.fetch_add(elapsed_us, std::memory_order_relaxed);
        stats.call_count.fetch_add(1, std::memory_order_relaxed);

        // 可选的日志输出（生产环境慎用——日志量可能很大）
        if (elapsed_us > 1000) {  // 只记录 > 1ms 的调用
            LOG_TRACE(log, "{}::work() took {} us (call #{})",
                stage_name, elapsed_us, stats.call_count.load());
        }
    }

    // 获取阶段统计
    struct StageStats {
        std::atomic<uint64_t> total_us{0};
        std::atomic<uint64_t> call_count{0};
    };

    StageStats getStats() const { return stats.load(); }

private:
    std::unique_ptr<IProcessor> inner_processor;
    String stage_name;
    StageStats stats;
};

// 使用示例：包装所有 Processor
// auto source = std::make_shared<MergeTreeSource>(...);
// auto instr_source = std::make_shared<InstrumentedProcessor>(source, "MergeTreeSource");
//
// 执行后输出各阶段耗时占比：
// [MergeTreeSource]      total: 120ms (25%)  calls: 420
// [FilterTransform]      total:  80ms (17%)  calls: 380
// [AggregatingTransform] total: 150ms (31%)  calls: 360
// [MergingAggregated]    total: 110ms (23%)  calls:   8  ← 单线程但每次调用时间长！
// [SortingTransform]     total:  20ms (4%)   calls:   1
```

---

### 测试验证

**测试1：不同 max_threads 对查询性能的影响**

```sql
-- 基准测试
SELECT count(), sum(amount)
FROM pipeline_demo
WHERE created_at >= '2025-06-01'
SETTINGS max_threads = 1;
-- 记录耗时

-- 逐步增加线程
SELECT count(), sum(amount)
FROM pipeline_demo
WHERE created_at >= '2025-06-01'
SETTINGS max_threads = 2;
-- 预期：耗时 ≈ 基准/2

SELECT count(), sum(amount)
FROM pipeline_demo
WHERE created_at >= '2025-06-01'
SETTINGS max_threads = 4;

SELECT count(), sum(amount)
FROM pipeline_demo
WHERE created_at >= '2025-06-01'
SETTINGS max_threads = 8;

-- 关键观察：当 max_threads 超过实际并行需求后，继续增加线程
-- 不仅不加速，还可能因为上下文切换开销而变慢
```

**测试2：对比聚合查询和纯扫描查询的 Pipeline 差异**

```sql
-- 第一条：纯扫描——完全并行
SELECT * FROM pipeline_demo WHERE created_at >= '2025-07-01';
-- 第二条：聚合——存在合并串行瓶颈
SELECT category, count(), sum(amount) FROM pipeline_demo
WHERE created_at >= '2025-07-01' GROUP BY category;
-- 第三条：聚合+排序——两个串行阶段
SELECT category, count() AS cnt FROM pipeline_demo
WHERE created_at >= '2025-07-01' GROUP BY category ORDER BY cnt DESC;
```

**验证方法**：对每条查询执行 `EXPLAIN PIPELINE`，看 `× 1` 阶段的数量——纯扫描为0个，聚合为1个，聚合+排序为2个。然后用 `clickhouse-benchmark` 实测，验证是否存在与 `× 1` 数量成正比的耗时增长。

---

## 4. 项目总结

### Pipeline各阶段职责与风险

| 阶段 | 类型 | 并行度 | 核心瓶颈风险 |
|------|------|--------|-------------|
| MergeTreeSource | Source | 取决于Part数量 | I/O带宽不足 |
| FilterTransform | Transform | 继承上游 | WHERE条件复杂时CPU密集 |
| ExpressionTransform | Transform | 继承上游 | 复杂表达式计算（如正则匹配） |
| AggregatingTransform | Transform | 继承上游 | 聚合基数高 → 哈希表膨胀 → 内存 |
| MergingAggregatedTransform | Transform | **强制单线程** | 聚合基数高时单线程CPU成为瓶颈 |
| MergingSortedTransform | Transform | **强制单线程** | 排序数据量大时成为瓶颈 |
| SortingTransform | Transform | **强制单线程** | 大数据量排序O(n log n) |
| LimitsCheckingTransform | Sink | **强制单线程** | 全表扫描后再截断，浪费计算 |

### 向量化 vs 行式执行的本质差异

| 维度 | 向量化（ClickHouse） | 行式（传统数据库） |
|------|---------------------|-------------------|
| 处理粒度 | Block（65536行） | Row（1行） |
| 函数调用开销 | N/65536 次 | N 次 |
| 元数据开销 | 每个Block一次（类型、空值检查等） | 每行一次 |
| SIMD加速 | 天然支持（连续内存+编译器向量化） | 难以实现 |
| 缓存友好度 | 高（列数据连续存储） | 低（跨列跳转） |
| 首行延迟 | 高（必须凑满一个Block） | 低（出一行返一行） |
| 聚合吞吐 | 极高 | 一般 |

### 适用场景

- **查询性能调优**：当你发现一条SQL查询慢，第一步就是 `EXPLAIN PIPELINE` 看串行瓶颈
- **线程利用率分析**：`max_threads` 调高但CPU不涨 → 说明Pipeline存在串行阶段限制了并行度
- **聚合查询加速**：理解 MergingAggregated 和 MergingSorted 的单线程特性，选择物化视图或优化策略
- **分布式查询规划**：将两阶段聚合的思想推广到集群，降低协调节点的合并压力

### 注意事项

- **并行度 ≠ Part数量**：即使Part只有1个，ClickHouse也能通过`max_threads`创建多个Source读取同一个Part的不同Granule范围
- **Block大小是双刃剑**：默认65536行在OLAP场景下是最优的平衡点，但点查场景（只查一两行）会导致不必要的读取开销
- **SIMD的前提是对齐**：ClickHouse的`PaddedPODArray`保证了数据地址16/32/64字节对齐，如果你自定义Column，必须注意内存对齐

### 常见踩坑经验

1. **大量小Part导致并行度虚高**：MergeTreeSource的并行度取决于Part数量。如果表有10万个Part（分区过细），Source阶段会创建大量线程，上下文切换开销超过计算收益，查询反而变慢。解决方案：`OPTIMIZE TABLE ... FINAL` 触发Merge减少Part数量。

2. **以为 `max_threads` 能加速所有查询**：`max_threads` 只影响可以并行的阶段。如果你的查询瓶颈是 MergingAggregatedTransform（单线程），把 `max_threads` 从8改到256，查询速度纹丝不动。**真正该做的是降低聚合基数或使用物化视图**。

3. **聚合基数过高导致 MergingAggregated 雪崩**：当一个`GROUP BY`的基数达到百万甚至千万级别时（比如按user_id聚合），每个 AggregatingTransform 线程维护的哈希表都可能膨胀到几百MB。当8个线程的8张哈希表最终合并时，单线程的 MergingAggregatedTransform 要逐行合并键值对，耗时呈指数增长。解决方案：`max_bytes_before_external_group_by` 让部分数据溢写到磁盘，或使用 `group_by_two_level_threshold` 启用两阶段聚合。

### 思考题

1. **为什么聚合的Merge阶段必须是单线程的？如果尝试用多线程合并会面临什么挑战？有没有可能在未来的ClickHouse版本中实现并行Merge？**

2. **向量化执行是否在所有场景下都比行式执行快？请举出一个行式执行可能更优的具体场景，并解释原因。**

---

> **下一章预告**：第35章「分布式查询——计划拆分与结果合并」。一个 `SELECT * FROM distributed_table` 在集群中的完整旅程——从发起节点的查询切分，到各分片的并行执行，再到协调查询节点的结果汇总。我们将深入 `StorageDistributed::read()` 和 `RemoteQueryExecutor` 的源码，揭示分布式查询的扇出与聚合全流程。
