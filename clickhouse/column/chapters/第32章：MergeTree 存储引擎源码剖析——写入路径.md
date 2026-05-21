# 第32章：MergeTree 存储引擎源码剖析——写入路径

> **版本**：ClickHouse 25.x LTS
> **定位**：高级篇开篇第一章。直接深入到ClickHouse最核心的MergeTree存储引擎C++源码，逐层剖析从TCP网络连接建立到数据落盘格式化的完整写入链路。适合有C++基础、想真正理解"INSERT一条数据到底经历了什么"的工程师。
> **前置阅读**：第2章（MergeTree建表与分区）、第3章（ORDER BY与主键索引）、第6章（数据压缩与存储格式）、第17章（分布式架构设计）
> **预计阅读**：50 分钟 | **源码阅读耗时**：90 分钟

---

## 1. 项目背景

某广告归因平台使用ClickHouse存储每天80亿条曝光、点击、转化事件。线上表有12张物化视图（Materialized View，以下简称MV），分别做按广告主、按素材、按媒体、按时段的多维度实时聚合。架构稳定运行了半年，直到一次压测揭开了藏在水面下的冰山。

那天数据架构师老沈在灰度环境做双十一压测——用`clickhouse-benchmark`模拟100并发、每批次5000行的`INSERT`写入。基线场景（不含MV）下，单节点吞吐稳定在42万行/秒。但当他用`EXPLAIN SYNTAX`把生产DDL完整建出来、12张MV全部挂上之后，再次压测——吞吐掉到了21万行/秒，腰斩。

老沈把监控曲线投到屏幕上。"官方文档说物化视图是'轻量级'的，创建时也确实是十几毫秒就返回了。但为什么吞吐直接砍了一半？"团队里的小伙子翻遍文档也找不到答案。所有公开资料都在讲MV的查询加速效果，却没人告诉你——每一条INSERT数据，都要在写入原表之后，**逐张MV再写一遍**。12张MV意味着每行数据要被写入13次（1次原始表 + 12次MV），而每一次写入都要经过完整的MergeTree写入路径：从内存Block拆分成Granule、逐列压缩、写.bin文件、写.mrk标记文件、更新primary.idx主键索引、生成checksums.txt校验和、最后做原子rename。13次写入中任何一层的缓冲区竞争、文件系统inode锁、压缩算法的CPU抢占，都是吞吐腰斩的推手。

"问题不是MV重不重，"老沈在白板上画了一条从TCP端口一路通到磁盘扇区的箭头，"问题是你不了解这13次写入各自把时间花在了哪里。MergeTree的写入路径像一个七层工厂流水线——从`TCPHandler`接收二进制Block，到`InterpreterInsertQuery`解析执行计划，到`MergeTreeData::write`分配临时目录，到`MergeTreeDataWriter`做排序和Granule切分，到`MergedBlockOutputStream`逐列序列化压缩，再到`DiskWriteBuffer`真正通过`pwrite`系统调用落盘。每一层都可能成为瓶颈。而MV只是在最外层给这条流水线加了12个并行分支——分支内部的每个环节和原表完全相同。"

这就是本章的价值：带你走通MergeTree写入路径的全部C++源码。读完本章，你不仅能解释MV为什么影响写入吞吐，还能精确指出**缓冲区大小、Granule切分策略、压缩算法选择、fsync时机**如何影响端到端延迟，并将这些知识转化为可验证的调优决策。这不是文档能告诉你的——必须看源码。

---

## 2. 项目设计：剧本式交锋对话

周五下午的源码阅读会上，大师架起投影仪，IDE里打开的是ClickHouse仓库的`src/Storages/MergeTree/`目录。小胖抱着一杯奶茶窝在椅子里，小白已经把GDB attach到了本地编译的Debug版clickhouse-server进程上。

**小胖**（瞟了一眼满屏的C++头文件）："源码？我只看文档就够了。INSERT进去，数据存到磁盘，有什么好看的？`INSERT INTO t VALUES (1, 'hello');`——这条SQL我从入门那天就会写，你要我花一个下午看C++？"

**大师**笑了，在终端敲了一行命令：

```sql
INSERT INTO test SELECT number, toString(number) FROM numbers(10000000);
```

"一千万行写入，从客户端发起到`Ok.`返回，一共走了18个关键函数调用。小胖，你告诉我——这18个调用分别花了多少微秒？哪个函数在申请内存？哪个函数在做系统调用`pwrite`？哪个函数在做数据排序导致CPU飙到100%？文档不会告诉你这些，但源码里的每一行`LOG_TRACE`、每一个`Stopwatch`计时器，都明明白白写着答案。"

**技术映射 #1**：ClickHouse的写入链路不是"一条直线"，而是一条 **"管道（Pipeline）"**。数据块（Block）从客户端流入，在管道中经过多个处理器（Processor），每个处理器完成一道工序——解析→排序→切分→压缩→校验→落盘。理解这条管道的每个节点，才能定位性能瓶颈。类比工厂流水线：TCPHandler是原料入口，InterpreterInsertQuery是生产调度室，MergeTreeDataWriter是加工车间，MergedBlockOutputStream是包装线，而磁盘是成品仓库。

---

**小白**（GDB断点已经设好，停在`MergeTreeData::write`的第一行）："大师，我有四个具体问题。第一，`INSERT`的数据是先全部缓存到内存再写磁盘，还是边收边写？如果客户端发了一个100GB的大Block，ClickHouse会OOM吗？第二，我建表时设的`ORDER BY (a, b)`，数据写入时到底什么时候排序——是在内存里排好再落盘，还是先落盘再依赖后台Merge排序？第三，写入路径里有没有fsync？磁盘断电后多少数据会丢？第四，MergeTree的'原子性'是怎么保证的——如果写入写到一半服务器crash了，磁盘上会留下半成品文件吗？"

**大师**竖起大拇指："四个问题全是写入路径的核心。逐个拆。"

"**第一问：边收边写。** 当客户端发送`INSERT`时，`TCPHandler`通过TCP协议逐块（Block）接收数据——客户端每攒够一个Block（默认约1MB或`max_block_size`行）就发送一次，服务端收到一个Block立即处理一个。源码中的关键位置在`TCPHandler::processInsertQuery()`——它在一个`while`循环里不断调用`receiveData()`读Block，然后立即`out->write(block)`触发`MergeTreeData::write`。所以不是'全收完再写'，而是**流式处理**。至于100GB大Block的恐惧——ClickHouse客户端在发送端就会按`max_block_size`（默认65536行）自动切分，服务端永远收不到'100GB的单个Block'。退一步说，即使有超大Block，`MergeTreeDataWriter`内部也是按Granule（8192行）为最小单位逐批写盘的，不存在'攒够100GB再flush'的逻辑。"

"**第二问：写入时排序。** MergeTree在写入阶段就做了**部分排序（Partial Sort）**——数据在落盘前，会按`ORDER BY`键在当前Block范围内排序。这意味着每个Part内部都是有序的，Merge阶段只是做多路归并（K-way Merge），而不是全量重排。源码中`MergeTreeDataWriter::write()`会调用`sortBlockBySortKey(block)`，用的是ClickHouse自研的并行排序算法（基于`pdqsort`的改进版）。这个排序是**按Block进行的**——如果你用多个并发写入，每个写入各自排序各自的Block，产生多个独立的Part，最终由后台Merge把它们归并到一起。"

"**第三问：fsync的时机。** 默认情况下，MergeTree写入路径**不主动调用fsync**。数据通过`pwrite`系统调用写入Page Cache后，由操作系统决定何时刷盘。这意味着如果服务器突然断电，Page Cache中尚未刷盘的数据会丢失。但ClickHouse提供了`min_rows_to_fsync_after_merge`和`fsync_after_insert`设置来控制fsync行为——前者在Part写入完毕后、如果有超过N行就执行fsync；后者直接对每次INSERT后的Part做fsync。**代价是吞吐量显著下降**，因为fsync会阻塞直到磁盘确认落盘。如果你有RAID卡写缓存+电池保护，通常可以不设fsync，依赖硬件保证持久性。源码中的关键调用在`MergedBlockOutputStream`析构函数——Part所有数据写完、做原子rename之前，会检查是否需要fsync。"

"**第四问：原子rename保证原子性。** MergeTree的写入过程全部发生在一个临时目录下（如`tmp_insert_<UUID>`）。所有`.bin`、`.mrk`、`primary.idx`、`checksums.txt`文件都先写入这个临时目录。只有当**所有文件都写入完毕、checksum校验通过**，才会执行最后一步——`rename(tmp_dir, part_dir)`，把临时目录重命名为正式的Part名（如`20250101_0_1_0`）。`rename`在同一个文件系统内是**原子操作**——要么成功（Part完整出现），要么失败（临时目录被启动时清理）。所以在任何时候服务器crash，磁盘上都**不会留下半成品Part**——只可能留下一个未完成rename的tmp目录，重启后被`cleanupTemporaryParts()`清理掉。"

---

**小胖**（奶茶已经见底，难得坐直了身子）："等一下，你说的Granule和Part是什么关系？我一直以为MergeTree就是一堆文件，但你们老说Part、Granule、Block……这些概念到底怎么对应到磁盘上的文件？"

**大师**打开IDE，定位到`MergeTreeDataPart.h`：

```cpp
// 层次关系（从大到小）：
// Table (MergeTree引擎的一张表)
//   └── Part（磁盘上的一个目录，如 all_1_1_0/）
//         ├── columns.txt（列元数据）
//         ├── count.txt（行数）
//         ├── primary.idx（主键索引 = 每8192个Granule的第一个Granule的第一行）
//         ├── checksums.txt（所有文件的校验和）
//         ├── a.bin（列a的数据文件：连续的压缩数据块）
//         ├── a.mrk2（列a的标记文件：每个Granule在.bin中的偏移量）
//         ├── b.bin
//         ├── b.mrk2
//         └── ...
//               └── Granule（逻辑概念：8192行 = 1个Granule，是索引的最小粒度）
//                     └── Block（内存概念：查询执行时的数据块，可变大小）
```

"Part是磁盘上的**目录**——`/var/lib/clickhouse/data/<db>/<table>/all_1_1_0/`。一个INSERT产生一个或多个Part（取决于数据量）。Granule是**逻辑单元**，固定8192行（由`index_granularity`控制），是主键索引的最小跳转粒度——查询时ClickHouse通过primary.idx定位到'目标数据大约在第N个Granule'，然后通过.mrk文件找到该Granule在.bin文件里的字节偏移，只读取需要的压缩块。一个Part里容许多个Granule。Block是**内存对象**，是ClickHouse执行引擎中数据流动的基本单位——它包含若干列、每列是一个`IColumn`的派生类，可能包含数千行到数百万行不等，一个Block在写入时会被**拆成多个Granule**。"

**技术映射 #2**：Block → Granule的拆分逻辑在`MergeTreeDataWriter::write()`中。核心是两重循环——外层按Block遍历，内层按Granule（每个8192行）切片。每切出一个Granule，就叫`MergedBlockOutputStream`写入每个列的.bin文件，同时在.mrk文件中记录一行标记条目。标记条目的格式是两个UInt64：`(compressed_offset_in_compressed_block, uncompressed_offset_in_uncompressed_block)`——注意Mark v2格式（`.mrk2`）比Mark v1（`.mrk`）多了一个granule编号字段，用于支持宽格式Part（Wide Part）。

---

**小白**看着大师画的层次图，追问："那`primary.idx`和`.mrk`文件的物理格式是什么？如果我把这两个文件误删了，数据还能读回来吗？"

**大师**："不能。`.mrk`是数据能否读出的**关键锁钥**。没有`.mrk`，ClickHouse不知道`a.bin`里第3个Granule的起止字节位置，所有查询都会报错。而`primary.idx`本质上是**稀疏索引**——只存每8192个Granule的第一个Granule的ORDER BY列值。它的物理格式很简单：按行存，每行是ORDER BY各列的序列化值。比如ORDER BY (a UInt64, b String)，primary.idx的内容就是：

```
¦ a=100  b="apple"  ¦  ← 第0×8192个Granule的第一行
¦ a=200  b="banana" ¦  ← 第1×8192个Granule的第一行
¦ a=300  b="cherry" ¦  ← 第2×8192个Granule的第一行
```

"查询时，`WHERE a = 250`先通过二分查找在primary.idx定位到'250落在第1和第2个索引条目之间'，从而确定目标数据在第8192到第16383个Granule范围内。然后通过`.mrk`文件查到这些Granule在`.bin`中的确切偏移，直接`pread`那几段压缩字节、解压、返回。整个过程不谈'全表扫描'半个字。"

---

## 3. 项目实战

### 环境准备

本章需要ClickHouse的Debug编译环境，以便用GDB追踪调用栈。不建议在生产环境操作——在开发机上编译即可。

**第一步：克隆源码并编译Debug版本**

```bash
# 克隆仓库（约1.5GB，需要稳定的网络环境）
git clone --recursive https://github.com/ClickHouse/ClickHouse.git
cd ClickHouse

# 创建构建目录
mkdir build && cd build

# CMake配置：Debug模式 + 关闭测试（加快编译速度）
cmake -DCMAKE_BUILD_TYPE=Debug \
      -DENABLE_TESTS=OFF \
      -DENABLE_CLICKHOUSE_SERVER=ON \
      -DENABLE_CLICKHOUSE_CLIENT=ON \
      ..

# 编译server二进制（-j$(nproc)使用全部CPU核心，预计30-60分钟）
ninja clickhouse-server clickhouse-client
# 或者使用 make: make -j$(nproc) clickhouse-server clickhouse-client
```

> **编译耗时提示**：Debug模式下二进制体积较大（约2GB），编译时间取决于CPU核心数和内存。32核+64GB内存环境约需30分钟；8核+16GB环境可能需要2小时以上。如果只想看写入路径源码而不需要实际Debug，可以跳过编译，直接用IDE打开`src/Storages/MergeTree/`目录阅读。

**第二步：启动Debug版ClickHouse**

```bash
# 在build目录下
./programs/clickhouse-server --log-level trace -- --path /tmp/ch_debug_data

# 另开终端，连接client
./programs/clickhouse-client --send_logs_level=trace
```

`--log-level trace`会输出最详细的日志，包括每个Granule写入时的字节数、压缩前后大小、临时目录rename等。

---

### 分步实现

#### Step 1：定位写入入口——TCPHandler

写入路径的起点是TCP端口9000。当客户端通过Native Protocol发送INSERT语句时，第一个处理它的C++类是`TCPHandler`。

```cpp
// src/Server/TCPHandler.cpp（简化，展示关键调用链）
// 行号约在 1200-1350，函数 void TCPHandler::processInsertQuery()

void TCPHandler::processInsertQuery(
    ClientConnectionPtr connection,
    ASTInsertQuery * insert_ast)
{
    // 1. 创建插入执行器
    auto interpreter = InterpreterInsertQuery(
        insert_ast,          // AST：解析后的INSERT语法树
        query_context);      // Context：包含settings、临时表、用户信息

    // 2. execute() 返回一个 BlockIO —— 包含输入/输出管道
    BlockIO io = interpreter->execute();

    // 3. 循环接收数据块（Block），逐块送给输出管道
    while (true) {
        // 从TCP连接读取一个Block（二进制格式，含行数和列数据）
        Block block = connection->receiveData();

        if (!block)  // 空Block表示发送结束
            break;

        // io.out 是一个 IBlockOutputStream 指针
        // 实际指向 MergeTreeData::write() 创建的输出流
        io.out->write(block);
    }

    // 4. 所有数据发送完毕，通知输出流 finalize
    io.out->writeSuffix();

    // 关键：此处没有任何"攒满再写"的逻辑——
    // 每个Block到达后立即触发MergeTreeData::write()
}
```

> **关键洞察**：`io.out->write(block)`这行就是进入MergeTree写入世界的入口。`io.out`的实际类型是`MergeTreeDataWriter`创建的一个输出流链——从`MergedBlockOutputStream`到`CompressedWriteBuffer`再到`WriteBufferFromFile`。这种链式设计是ClickHouse I/O层的核心模式——每个Buffer包装下一个Buffer，形成装饰器链。

#### Step 2：MergeTreeData::write() 核心逻辑

从TCP接收到的Block进入`MergeTreeData::write()`，这是MergeTree引擎写入路径的"总控"函数。

```cpp
// src/Storages/MergeTree/MergeTreeData.cpp（简化，实际约在 3500-3700 行）
// 函数签名：void MergeTreeData::write(
//     const ASTInsertQuery & query,
//     const ContextPtr & query_context,
//     bool async_insert)

void MergeTreeData::write(const ASTInsertQuery & /*query*/,
                          const ContextPtr & /*context*/,
                          bool /*async_insert*/)
{
    // ═══════════════════════════════════════════════
    // 阶段1：创建写入环境
    // ═══════════════════════════════════════════════

    // 1.1 创建MergeTreeDataWriter——持有StoragePolicy、Settings等引用
    MergeTreeDataWriter writer(*this);

    // 1.2 生成临时Part目录名，如 tmp_insert_f47ac10b-58cc-4372-a567-0e02b2c3d479
    String tmp_dir = relative_data_path + "tmp_insert_" + toString(UUIDHelpers::generateV4());

    // 1.3 获取Block输入流（来自TCPHandler的接收管道）
    auto in_stream = context->getInputStream();

    // ═══════════════════════════════════════════════
    // 阶段2：逐Block处理（写入核心循环）
    // ═══════════════════════════════════════════════

    while (Block block = in_stream->read()) {

        // 2.1 检查是否有ALTER TABLE MODIFY COLUMN等Mutation等待处理
        // 如果当前Part的数据版本落后，先应用Mutation再写入
        checkMutations(block);

        // 2.2 按ORDER BY key做部分排序（Part范围内有序）
        // 内部使用 pdqsort 并行排序
        if (!is_pre_sorted)
            sortBlockBySortKey(block);

        // 2.3 核心：将Block写入临时目录
        // 这一步内部拆Granule、逐列写.bin、写.mrk
        writer.write(block, tmp_dir);
    }

    // ═══════════════════════════════════════════════
    // 阶段3：完结Part（finalize）
    // ═══════════════════════════════════════════════

    // 3.1 通知writer所有Block已写完，触发flush和生成元数据文件
    // 内部会写 columns.txt / count.txt / checksums.txt / partition.dat
    writer.finalize(tmp_dir);

    // 3.2 生成Part名称（格式：minBlock_maxBlock_level）
    String part_name = writer.getNewPartName(tmp_dir);
    // 例如：all_1_1_0（用 block_number = insert_counter 标识）

    // 3.3 原子rename：tmp_insert_xxx → all_1_1_0
    // 同一文件系统内的原子操作——成功则Part完整，失败则留下临时目录
    disk->moveDirectory(tmp_dir, part_name);

    // 3.4 在内存元数据中注册Part
    // 将Part信息写入 system.parts，使其对查询可见
    auto part = std::make_shared<MergeTreeDataPart>(...);
    addPartToWorkingSet(part_name, part);

    // 3.5 如果是 ReplicatedMergeTree，将Part名push到ZooKeeper复制队列
    if (is_replicated)
        queue.push({part_name});
}
```

> **关键洞察**：`rename`是MergeTree写入路径的**原子性边界**。在此之前，所有写入都在`tmp_insert_*`临时目录下——服务器crash后重启，`cleanupTemporaryParts()`会扫描并删除所有`tmp_`前缀目录。`rename`一旦成功，Part就从"不可见"变为"对查询可见"——这个切换是瞬时的，没有中间态。这也解释了为什么ClickHouse不需要WAL（Write-Ahead Log）：Part本身就是原子的持久化单位。

#### Step 3：Granule切分与列文件写入

`MergeTreeDataWriter::write()`是写入路径中逻辑最密集的函数——完成Block到Granule的拆分、列的逐Granule序列化、压缩、标记文件写入。

```cpp
// src/Storages/MergeTree/MergeTreeDataWriter.cpp（简化，实际约在 200-350 行）

void MergeTreeDataWriter::write(
    const Block & block,
    IBlockOutputStream & out_stream)
{
    // 配置参数
    size_t rows_per_granule = storage.getSettings()->index_granularity; // 默认8192
    size_t rows_in_block = block.rows();

    // ═══════════════════════════════════════════════
    // 外循环：按Granule切片（每次切 rows_per_granule 行）
    // ═══════════════════════════════════════════════

    for (size_t row_start = 0; row_start < rows_in_block; row_start += rows_per_granule) {

        size_t granule_rows = std::min(rows_per_granule, rows_in_block - row_start);
        auto granule_block = block.cloneEmpty();

        // ═══════════════════════════════════════════════
        // 内循环：逐列切出当前Granule的列数据
        // ═══════════════════════════════════════════════

        for (size_t col_idx = 0; col_idx < block.columns(); ++col_idx) {
            auto & src_col = block.getByPosition(col_idx).column;

            // 从原列中切出 [row_start, row_start+granule_rows) 行
            auto granule_col = src_col->cut(row_start, granule_rows);

            granule_block.insert(ColumnWithTypeAndName(
                granule_col,
                block.getByPosition(col_idx).type,
                block.getByPosition(col_idx).name));
        }

        // 将当前Granule写入所有列的.bin文件，同时在.mrk中记录偏移
        out_stream.write(granule_block);

        // ═══════════════════════════════════════════════
        // 写入 primary.idx（主键稀疏索引）
        // 条件：当前Granule编号可被 index_granularity 整除
        //       即每 8192×8192 行的位置写入一条索引
        // ═══════════════════════════════════════════════

        if (current_granule_count % index_granularity == 0) {
            // 取当前Granule第一行的ORDER BY列值，写入primary.idx
            auto first_row = granule_block.getSortColumns();
            index_stream->write(first_row);
        }

        current_granule_count++;
    }
}
```

> **关键洞察**：Granule切分发生在**内存中**。`src_col->cut(row_start, granule_rows)`操作对于不同列类型有不同的实现——定长列（如UInt64）直接用`memcpy`，变长列（如String）需要拷贝偏移数组和数据块。这是写入路径中CPU消耗较高的环节之一。如果你发现写入时CPU瓶颈，可以尝试增大`index_granularity`（如设为16384），减少切分次数，代价是主键索引变稀疏——查询时跳转范围变大，可能多读一些无关Granule。

#### Step 4：观察物理文件

创建测试表并插入数据，然后直接检查磁盘上的Part目录结构。

```sql
-- 创建测试表
CREATE TABLE write_demo (
    a UInt64,
    b String,
    c Float64
) ENGINE = MergeTree()
ORDER BY a
SETTINGS index_granularity = 8192;

-- 插入10万行
INSERT INTO write_demo
SELECT
    number,
    toString(number),
    rand() / 4294967295
FROM numbers(100000);
```

```bash
# 进入数据目录（默认路径 /var/lib/clickhouse/）
cd /var/lib/clickhouse/data/default/write_demo/

# 查看所有Part目录
ls -la
# drwxr-x--- 2 clickhouse clickhouse  4096 Apr 30 10:30 all_1_1_0/

# 进入Part目录
cd all_1_1_0/
ls -la
```

典型输出：

```
a.bin                          -- 列a的压缩数据（UInt64）
a.mrk2                         -- 列a的标记文件（Mark v2格式）
b.bin                          -- 列b的压缩数据（String）
b.mrk2                         -- 列b的标记文件
c.bin                          -- 列c的压缩数据（Float64）
c.mrk2                         -- 列c的标记文件
primary.idx                    -- 主键索引（存ORDER BY列每8192个Granule的首行值）
checksums.txt                  -- 所有文件的校验和（SHA-256 + 未压缩大小 + 压缩后大小）
columns.txt                    -- 列名和类型
count.txt                      -- 总行数（一个数字）
minmax_a.idx                   -- 分区修剪用的min/max索引（此处a是ORDER BY列）
partition.dat                  -- 分区键值
default_compression_codec.txt  -- 压缩算法（默认LZ4）
```

```bash
# 查看checksums.txt内容
cat checksums.txt
# 典型输出：
# a.bin       512  (compressed)  1024 (uncompressed)  a1b2c3d4... (SHA256)
# a.mrk2      64                  128                   e5f6a7b8...
# b.bin       2048                4096                  ...
# b.mrk2      96                  192                   ...
# ...

# 查看count.txt
cat count.txt
# 100000
```

也可以用ClickHouse自带的工具检查Part文件：

```sql
-- 在clickhouse-client中查看Part元数据
SELECT
    name,
    part_type,          -- Wide / Compact
    rows,
    marks_count,        -- Granule数量 = ceil(rows/8192)
    bytes_on_disk,
    modification_time
FROM system.parts
WHERE table = 'write_demo'
  AND active;

-- 输出示例：
-- name          | part_type | rows   | marks_count | bytes_on_disk
-- all_1_1_0     | Wide      | 100000 | 13          | 524288
-- (100000 / 8192 = 12.2 → 向上取整为 13 个Granule)
```

#### Step 5：日志埋点追踪写入

Debug编译后用trace级别日志观察写入路径的每一步。

```bash
# 启动server，trace级别日志
./programs/clickhouse-server --log-level trace \
    -- --path /tmp/ch_debug_data \
    > /tmp/ch_trace.log 2>&1 &

# 另开终端执行写入
./programs/clickhouse-client --send_logs_level=trace \
    -q "INSERT INTO write_demo VALUES (1, 'test', 3.14);"
```

在`/tmp/ch_trace.log`中搜索关键日志：

```
# 按日志中的类名过滤关键行：
# 1. 收到INSERT请求
<TCPHandler> Processing INSERT query

# 2. 创建临时目录
<MergeTreeData> Created temporary directory: tmp_insert_f47ac10b...

# 3. 写入Granule（每个Granule一条日志）
<MergedBlockOutputStream> Writing granule 0, rows=1
<MergedBlockOutputStream> Writing column 'a', compressed 8 → 16 bytes
<MergedBlockOutputStream> Writing column 'b', compressed 5 → 20 bytes
<MergedBlockOutputStream> Writing column 'c', compressed 4 → 12 bytes

# 4. flush压缩块
<MergedBlockOutputStream> Flushing compressed blocks to disk

# 5. 生成元数据文件
<MergeTreeDataWriter> Writing checksums.txt
<MergeTreeDataWriter> Writing columns.txt

# 6. 原子rename（最关键的一步）
<MergeTreeData> Renaming tmp_insert_f47ac10b... → all_2_2_0

# 7. Part注册完成，对查询可见
<MergeTreeData> Part all_2_2_0 committed, rows=1, marks=1
```

> **关键洞察**：注意日志中"compressed 8 → 16 bytes"——对于小数据量，LZ4压缩的元数据开销可能导致"压缩后反而更大"。这是正常的：LZ4的最小压缩单元是64KB，小于这个值的Block会直接以未压缩格式存储，加上压缩帧头所以略有膨胀。写入大批量数据（如每批次>10000行）才能充分发挥压缩效果。

#### Step 6：源码级调优

基于写入路径的源码理解，下面列出五个直接影响写入性能的设置项及其底层原理。

```cpp
// ── 设置1：index_granularity（默认8192） ──
// 位置：MergeTreeDataWriter::write() 中 rows_per_granule
// 
// 增大 → 更少的Granule → 更少的主键索引条目 → 更少的.mrk写入次数
// 代价：查询时每个Granule范围更大，可能需要解压更多无用数据
//
// 调优建议：
//   宽表（>100列）+ 查询常扫全表 → index_granularity = 32768 或更大
//   窄表（<20列） + 点查为主       → index_granularity = 4096 或保持默认

// ── 设置2：min_compress_block_size（默认65536字节） ──
// 位置：CompressedWriteBuffer::next() 中压缩决策
//
// MergedBlockOutputStream按Granule写入每个列时，列数据先进入
// CompressedWriteBuffer的未压缩缓冲区。当缓冲区累积到
// min_compress_block_size 字节后，触发一次 LZ4/ZSTD 压缩，
// 输出一个压缩块到 .bin 文件。
//
// 增大 → 更好的压缩率（大块更容易找到重复模式）
// 减小 → 更精细的索引粒度（每个压缩块更小，查询时可更精确跳转）
//
// 注意：此设置影响的是"压缩块的粒度"，不是"Granule的粒度"。
// 一个Granule可能横跨多个压缩块，一个压缩块也可能包含多个Granule。

// ── 设置3：min_rows_to_fsync_after_merge（默认0，即不主动fsync） ──
// 位置：MergedBlockOutputStream 析构函数中的 fsync 检查
//
// 设为10000000（一千万行）：每当一个Part写入超过一千万行后，
// 在rename之前调用 fsync() 强制刷盘。
// 这是"写入吞吐"vs"持久性保证"的经典权衡。
//
// 0（默认）= 依赖操作系统Page Cache，断电可能丢失未刷盘数据
// N > 0   = 每N行fsync一次，保证持久性但写入速度下降30-50%

// ── 设置4：write_final_mark（默认1，启用） ──
// 位置：MergedBlockOutputStream::writeSuffix()
//
// 在每个column.bin文件末尾写入一个"终结标记"（final mark），
// 记录最后一个Granule之后的文件长度。查询需要它来确定文件边界。
// 只有在极特殊的"写入不需查询"场景下可设为0。

// ── 设置5：max_part_loading_threads ──
// 启动时加载Part的并发线程数。只影响重启速度，不影响写入速度。
```

---

### 测试验证

**验证1：GDB追踪完整调用栈**

```bash
# 启动clickhouse-server（Debug版）
gdb --args ./programs/clickhouse-server -- --path /tmp/ch_gdb_data

# 在GDB中设置断点
(gdb) break MergeTreeData::write
(gdb) break MergeTreeDataWriter::write
(gdb) break MergedBlockOutputStream::write
(gdb) break WriteBufferFromFile::write

# 运行
(gdb) run

# 另开终端执行INSERT
./programs/clickhouse-client -q "INSERT INTO write_demo VALUES (1,'a',1.0);"

# GDB会在每个断点停下，用 bt 查看调用栈
(gdb) bt
# 0  MergedBlockOutputStream::write() at MergedBlockOutputStream.cpp:150
# 1  MergeTreeDataWriter::write() at MergeTreeDataWriter.cpp:280
# 2  MergeTreeData::write() at MergeTreeData.cpp:3550
# 3  InterpreterInsertQuery::execute() at InterpreterInsertQuery.cpp:230
# 4  TCPHandler::processInsertQuery() at TCPHandler.cpp:1280
# 5  TCPHandler::runImpl() at TCPHandler.cpp:500
```

**验证2：hexdump查看.bin文件物理格式**

```bash
# 看a.bin文件的前256字节
hexdump -C a.bin | head -20

# 典型的LZ4压缩帧格式：
# 00 00 00 00  ── magic number (0x82 = LZ4)
# 1d 00 00 00  ── 压缩后大小（29字节）
# 40 00 00 00  ── 未压缩大小（64字节）
# ... compressed data ...
```

**验证3：基准测试——不同Block大小对写入吞吐的影响**

```bash
# 小Block（每批1000行）
clickhouse-benchmark --iterations 10 --delay 0 \
    --query "INSERT INTO write_demo SELECT number, toString(number), rand() FROM numbers(1000)" \
    --concurrency 1

# 大Block（每批100000行）
clickhouse-benchmark --iterations 10 --delay 0 \
    --query "INSERT INTO write_demo SELECT number, toString(number), rand() FROM numbers(100000)" \
    --concurrency 1

# 预期：大Block吞吐更高，因为减少了Granule切分的循环次数和文件I/O次数
```

---

## 4. 项目总结

### 写入路径关键数据结构

| 组件 | 源文件 | 职责 |
|------|--------|------|
| `TCPHandler` | `src/Server/TCPHandler.cpp` | 接收客户端INSERT请求，逐Block读取二进制数据 |
| `InterpreterInsertQuery` | `src/Interpreters/InterpreterInsertQuery.cpp` | 解析INSERT AST，创建BlockIO管道 |
| `MergeTreeData::write()` | `src/Storages/MergeTree/MergeTreeData.cpp` | 总控：创建临时目录、调度Writer、原子rename、注册Part |
| `MergeTreeDataWriter` | `src/Storages/MergeTree/MergeTreeDataWriter.cpp` | Block排序、Granule切分、primary.idx写入 |
| `MergedBlockOutputStream` | `src/Storages/MergeTree/MergedBlockOutputStream.cpp` | 逐列序列化：压缩、写.bin、写.mrk2、flush |
| `IMergeTreeDataPart` | `src/Storages/MergeTree/IMergeTreeDataPart.h` | Part元数据抽象：子类Wide/Compact |
| `MergeTreeDataPartWide` | `src/Storages/MergeTree/MergeTreeDataPartWide.h` | Wide格式Part：每个列独立.bin/.mrk文件 |
| `MergeTreeDataPartCompact` | `src/Storages/MergeTree/MergeTreeDataPartCompact.h` | Compact格式Part：所有列合并到单文件data.bin |
| `CompressedWriteBuffer` | `src/Compression/CompressedWriteBuffer.cpp` | 压缩缓冲：累积到min_compress_block_size后压缩写入底层 |
| `WriteBufferFromFile` | `src/IO/WriteBufferFromFile.cpp` | 文件写入抽象：最终通过pwrite/fsync系统调用落盘 |

### 写入路径完整调用链

```
TCPHandler::processInsertQuery()           [Server/TCPHandler.cpp:1250]
  └─ InterpreterInsertQuery::execute()      [Interpreters/InterpreterInsertQuery.cpp:200]
       └─ MergeTreeData::write()            [Storages/MergeTree/MergeTreeData.cpp:3500]
            ├─ sortBlockBySortKey(block)    [Storages/MergeTree/MergeTreeDataWriter.cpp:150]
            ├─ MergeTreeDataWriter::write() [Storages/MergeTree/MergeTreeDataWriter.cpp:200]
            │    ├─ column->cut()           [Columns/IColumn.h] — Granule切分
            │    └─ MergedBlockOutputStream::write()
            │         ├─ CompressedWriteBuffer::write()    — 压缩
            │         └─ WriteBufferFromFile::write()      — 落盘
            ├─ writer.finalize()            — 写checksums.txt等元数据
            ├─ disk->moveDirectory()        — 原子rename
            └─ addPartToWorkingSet()        — 注册Part使其可查询
```

### 适用场景

- **写入性能调优**：通过理解Granule切分和压缩的CPU开销，调整`index_granularity`和`min_compress_block_size`以匹配工作负载。
- **数据可靠性调试**：理解fsync时机和原子rename机制，评估不同配置下的数据丢失风险。
- **物化视图代价分析**：物化视图本质上是在写入路径上添加并行分支——每张MV对原表写入路径的完整"复制"。N张MV = (N+1)次完整写入路径开销。
- **定制存储引擎开发**：MergeTree是ClickHouse存储层的基类，理解其写入路径是开发自定义引擎（如自定义MergeSelector、自定义数据写入格式）的前提。

### 注意事项

1. **Part的原子性依赖于同一文件系统的rename**。如果临时目录和最终Part目录在不同的文件系统上，rename不是原子操作——ClickHouse会检测并报错。所有Part目录必须与`<path>`配置在同一挂载点下。

2. **`.mrk`文件是数据读取的生命线**。如果`.mrk2`损坏或丢失，即使`.bin`文件完整，数据也无法读取——因为ClickHouse不知道每个Granule在压缩流中的起止位置。`.mrk2`文件没有独立的checksum（只有`checksums.txt`中有总校验），建议开启`allow_remote_fs_zero_copy_replication`或定期备份Part目录。

3. **单Part写入是单线程的**——`MergeTreeDataWriter::write()`顺序处理每个Block。如果想利用多核并行写入，需要从客户端并发发送多个INSERT（每个INSERT产生一个独立的Part），或使用`async_insert`模式（服务端积攒多个小INSERT合并为一个大Part，但排序和切分仍然单线程）。

4. **写入路径不涉及Merge**。Merge是独立的后台线程池（`background_schedule_pool`）执行的，与INSERT完全异步。一个INSERT的Part诞生的瞬间，它就已经可以查询了——Merge只是后期把多个小Part合并成大Part以减少文件数量、优化查询性能。

### 常见踩坑经验

**坑1：大量小INSERT导致Part爆炸（>10000个Part）**

每个单独的`INSERT`语句产生至少一个Part。如果应用层逐条INSERT（如`for row in data: INSERT INTO t VALUES (...)`），10万条数据会产生10万个Part。查询时ClickHouse需要打开10万个目录、读取10万套`.mrk`文件——文件系统inode耗尽、`open()`调用耗时从微秒级飙到秒级。**解法**：应用层攒批——积累到至少1000行或1MB再发一次INSERT；或使用`async_insert=1`让ClickHouse服务端内部合并小块。

**坑2：写入过程中kill进程留下tmp_*目录垃圾**

如果进程在`rename`之前被kill（或crash），临时目录`tmp_insert_<UUID>`会残留在磁盘上。ClickHouse下次启动时会调用`cleanupTemporaryParts()`清理，但如果进程频繁crash重启、临时目录积累过多，可能导致磁盘空间耗尽。**解法**：监控`/var/lib/clickhouse/data/`下的`tmp_*`目录数量，设置定期清理脚本兜底。

**坑3：`min_compress_block_size`设置过大导致写入延迟飙升**

有人想把压缩块设为1MB以追求更高压缩率，但`CompressedWriteBuffer`必须等缓冲区攒够1MB才触发压缩——对于小批次写入，这意味着一行数据就要等1MB的缓冲区填满，延迟从毫秒级变成秒级。**解法**：此设置不宜超过256KB。对于高实时性场景，保持默认的65536（64KB）。

**坑4：开启fsync后写入吞吐下降无法解释**

设置了`min_rows_to_fsync_after_merge = 1000000`后，写入吞吐从40万行/秒骤降到15万行/秒。原因是每次Part写完后fsync会强制将Page Cache刷入磁盘，而HDD的fsync延迟在10-50毫秒、SSD在1-5毫秒——每秒能执行的INSERT次数被fsync延迟卡死了。**解法**：在有BBU（电池备份单元）的RAID卡或企业级NVMe（内置电容保护）上，可安全关闭fsync；只在无硬件保护的场景开启。

### 思考题

1. **如果写入过程中服务器断电，tmp_*目录会被自动清理吗？未完成rename的Part数据会不会在重启后"幽灵般"出现？** 请结合源码中`cleanupTemporaryParts()`的调用时机（`MergeTreeData::startup()`）分析其可靠性——如果一个Part的所有文件都已写入但rename刚好在断电瞬间还未执行，这些文件占用磁盘空间但永远不会被查询到，是否有内存泄漏式的磁盘浪费？

> 提示：查看`src/Storages/MergeTree/MergeTreeData.cpp`中`startup()`函数的开头部分，追踪`cleanupTemporaryParts`的触发条件。思考：如果tmp目录和正式目录在不同文件系统上，rename不是原子的，ClickHouse会如何处理？

2. **为什么MergeTree要在写入时对数据按ORDER BY排序？如果数据本身已在外部排好序（如从Spark ETL导出时已按ORDER BY全排序），能否跳过写入时的排序步骤以提升吞吐？** 阅读源码中`sortBlockBySortKey()`的调用条件，找到控制"数据是否已预排序"的setting（提示：搜索`input_format_allow_seeks`或`optimize_on_insert`），分析跳过排序的前提条件和潜在风险。

> 提示：部分排序 vs 全排序的区别在于——MergeTree只保证**每个Part内部有序**，不同Part之间可能无序。即使外部已全排序，内部仍可能按Block为单位重新排序以覆盖"Block间无序"的场景。思考：设置`optimize_on_insert=1`与跳过排序是否相关？

---

> **本章完**。这是高级篇的第一章——我们从TCP端口一路追踪到了磁盘扇区，走完了MergeTree写入路径的全部关键函数。理解了这条流水线，你就能回答"物化视图为什么拖慢写入"、"多少行一批写入最优"、"断电会不会丢数据"这些文档不会告诉你的问题。**下一章，我们将深入写入路径的对称面——MergeTree的查询路径源码剖析，拆解主键索引、标记文件和PREWHERE优化在C++层面如何协同工作。**

*下一章预告：第33章 MergeTree 查询路径源码剖析——从WHERE到Granule跳过的精确过程。*
