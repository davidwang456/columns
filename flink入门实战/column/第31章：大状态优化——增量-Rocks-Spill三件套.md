# 第31章：大状态优化——增量/Rocks/Spill三件套

---

## 1. 项目背景

某广告计费系统：状态大小500GB，RocksDB State Backend，Checkpoint每次耗时8分钟——耗尽Checkpoint Timeout（10分钟），作业频繁因为Checkpoint超时重启。

这是一个典型的"大状态恶性循环"：

```
状态大 → Checkpoint耗时久 → 容易超时 → 作业重启
    ↑                                       │
    └────────── 重启后重新加载状态 ──────────┘
```

大状态优化的核心目标：
1. **减少状态体积**：压缩、TTL、合理的数据结构选择
2. **加速Checkpoint**：增量Checkpoint、异步快照、Local Recovery
3. **减少RocksDB开销**：Compaction调优、Block Cache配置、内存分配

---

## 2. 项目设计

> 场景：凌晨2点，计费作业又因为Checkpoint超时挂了，小胖和运维轮流重启。

**小胖**：500GB的状态，增量Checkpoint已经开了，但还是慢。每次Checkpoint要传300MB的SST文件——磁盘IO打满了。

**大师**：增量Checkpoint已经减少了传输量。但300MB的SST文件上传到HDFS/S3还是慢——瓶颈在**存储IO**。

**小白**：那Local Recovery呢？让RocksDB直接从本地磁盘恢复，不走远程存储。

**大师**：对的。`state.backend.local-recovery: true`让Flink在本地保留一份Checkpoint的副本。恢复时优先从本地读，速度快10-100倍。**技术映射：Local Recovery = 牺牲本地磁盘空间换取恢复速度。每个TaskManager保留最新的一份Checkpoint副本，故障恢复时从本地读取RocksDB文件，无需从远程下载。**

**小胖**：那优化RocksDB本身呢？我看文档有好多RocksDB参数。

**大师**：RocksDB调优的核心三参数：

```
1. MemTable大小：决定写入吞吐。越大写入越快，但flush时GC压力大
2. Block Cache大小：决定读热点命中率。越大读越快
3. Compaction策略：决定写放大因子。Level Style比Universal写放大更可控
```

**技术映射：RocksDB性能 ≈ 内存命中率。核心路径：Write → MemTable（内存）→ Flush（SST Level0）→ Compaction（合并SST）。Block Cache = 读缓存，Managed Memory = RocksDB的总内存预算。**

**小白**：有没有办法从业务层面减小状态？比如只保留最近7天的聚合数据？

**大师**：当然有——**State TTL**。设置TTL后，Flink会自动清理过期的状态。但对于大状态（500GB），TTL的清理效率可能不够——需要配合**增量清理**（`state.backend.rocksdb.ttl.compaction.filter.enabled: true`）让RocksDB在Compaction时顺便清理过期数据。

---

## 3. 项目实战

### 分步实现

#### 步骤1：State TTL最佳实践

**目标**：为大状态配置合理的TTL，避免无效状态堆积。

```java
// ========== 关键：State TTL的配置顺序 ==========

// 1. 创建TTL配置
StateTtlConfig ttlConfig = StateTtlConfig
        .newBuilder(Time.days(7))                  // 存活7天
        .setUpdateType(
                StateTtlConfig.UpdateType.OnCreateAndWrite)  // 写入和创建时更新TTL
        .setStateVisibility(
                StateTtlConfig.StateVisibility.NeverReturnExpired)
        .cleanupInRocksdbCompactFilter(1_000)       // RocksDB compaction时清理（增量清理）
        .build();

// 2. 应用到StateDescriptor
ValueStateDescriptor<Long> desc = new ValueStateDescriptor<>("count", Long.class);
desc.enableTimeToLive(ttlConfig);

// ========== 增量清理的原理 ==========
// cleanupInRocksdbCompactFilter(1000)
// 含义：每处理1000条RocksDB的记录，检查一次TTL
// 数字越小检查越频繁，但CPU开销越大（建议1000-5000）
```

**注意**：Flink 1.17+支持`cleanupFullSnapshot()`——在Checkpoint全量快照时清理过期数据，这对于"全量快照很少的大状态"很有用。

#### 步骤2：RocksDB参数调优

**目标**：根据作业特征调整RocksDB参数，最大化吞吐。

```properties
# ========== flink-conf.yaml 中的RocksDB配置 ==========

# ----- 基本配置 -----
state.backend: rocksdb
state.backend.incremental: true                     # 增量Checkpoint（大状态必开）
state.backend.local-recovery: true                  # 本地恢复

# ----- RocksDB内存管理 -----
state.backend.rocksdb.memory.managed: true          # Flink统一管理内存（默认true）
# taskmanager.memory.managed.size: 8g               # 给RocksDB的总内存
state.backend.rocksdb.memory.write-buffer-ratio: 0.5   # MemTable占总managed内存比例
state.backend.rocksdb.memory.high-prio-pool-ratio: 0.1 # 高优先级（Block Cache）的比例

# ----- Compaction优化 -----
state.backend.rocksdb.compaction.level.max-size-level-base: 512mb  # base level SST大小
state.backend.rocksdb.compaction.level.target-file-size-base: 64mb  # 目标SST大小
state.backend.rocksdb.compaction.level.use-dynamic-size: true       # 动态调整SST大小

# ----- 写入优化 -----
state.backend.rocksdb.writebuffer.size: 256mb      # 单个MemTable大小
state.backend.rocksdb.writebuffer.count: 4          # MemTable个数（写缓存池）
state.backend.rocksdb.writebuffer.number-to-merge: 2  # 每次合并的MemTable数

# ----- 读取优化 -----
state.backend.rocksdb.block.cache-size: 512mb       # Block Cache大小
state.backend.rocksdb.block.blocksize: 4kb           # Block大小

# ----- 后台线程 -----
state.backend.rocksdb.thread.num: 4                  # RocksDB后台Compaction线程数

# ----- TTL清理 -----
state.backend.rocksdb.ttl.compaction.filter.enabled: true  # Compaction时清理过期状态
```

#### 步骤3：RocksDB指标监控

**目标**：监控RocksDB的关键性能指标，判断是否需要调优。

```java
// 通过Flink Metrics暴露RocksDB内部指标
// 配置：flink-conf.yaml
metrics.reporter.prom.filter.out: ""   # 不排除任何metrics，让RocksDB指标全部暴露

// 关键RocksDB指标：
// rocksdb.actual-delimited-size-compact-sst-size  → 实际SST大小（与状态量相关）
// rocksdb.num-running-compactions                → 正在进行的Compaction数（持续>0说明写压力大）
// rocksdb.read-latency-average                  → 读延迟（>10μs说明Block Cache不够）
// rocksdb.write-latency-average                 → 写延迟（>20μs说明MemTable或Compaction问题）
// rocksdb.block-cache-usage                     → Block Cache使用率（>90%说明Cache不够大）
// rocksdb.mem-table-flush-pending               → 等待flush的MemTable数（>0说明写入太快）

// 告警规则：
// - rocksdb.write-stall-duration > 0 → 写入被阻塞（严重！需要立即处理）
// - rocksdb.num-running-compactions > 8 → Compaction太多，磁盘IO过载
```

#### 步骤4：大状态Checkpoint加速

**目标**：通过配置减少Checkpoint耗时。

```properties
# ========== Checkpoint加速配置 ==========

# 1. 启用Unalign Checkpoint（反压时不用等Barrier对齐）
execution.checkpointing.unaligned: true
execution.checkpointing.unaligned.max-buffers: 1000

# 2. 增大Checkpoint超时（给大状态足够时间）
execution.checkpointing.timeout: 30min

# 3. RocksDB增量Checkpoint（只上传变化的SST）
state.backend.incremental: true

# 4. 异步快照（默认开启，RocksDB快照不阻塞主线程）
# state.backend.async: true

# 5. 压缩Checkpoint数据（传输量减少50-80%）
execution.checkpointing.compression: true

# 6. Checkpoint存储优化（使用速度更快的存储）
# state.checkpoints.dir: s3a://bucket/chk/   # S3比HDFS快（某些场景）
```

#### 步骤5：两阶段聚合——从业务层面减小状态

**目标**：通过优化业务逻辑减少状态量。

```java
// ========== 场景：统计每个用户的累计消费金额 ==========
// 如果用户基数10亿，每个用户存一个ValueState<Long>

// ❌ 方案1：全量keyBy(userId)存储——状态随用户数线性增长
events.keyBy(e -> e.userId)
      .map(new RichMapFunction<>() {
          ValueState<Double> total;
          // 每个用户一个状态，10亿用户 = 10亿个state entry → OOM
      });

// ✅ 方案2：按省份keyBy（聚合维度升级）
// 如果业务只需要"省份级统计"而非"用户级"
events.keyBy(e -> e.province)
      .map(new RichMapFunction<>() {
          ValueState<Double> total;  // 34个省份 → 34个state entry
      });

// ✅ 方案3：保留最近用户（减少状态基数）
// 使用State TTL + MapState<String, Double>，设置TTL=7天
// 7天不活跃的用户状态自动删除
```

### 可能遇到的坑

1. **RocksDB write stall导致吞吐从10万降到1000**
   - 根因：L0的SST文件太多（默认超过4个触发stall），Compaction跟不上写入速度
   - 解决：增大`writebuffer.count`和`writebuffer.size`；增加`thread.num`加速Compaction

2. **增量Checkpoint的上传SST文件越来越多**
   - 根因：增量Checkpoint每次上传新SST文件但不清除旧的——文件持续累积
   - 解决：Flink 1.17+的`state.backend.rocksdb.checkpoint.cleaner.enable: true`（在CLEANER线程中删除过期SST）

3. **Local Recovery占用过多本地磁盘**
   - 根因：每个TaskManager都在本地保存一份Checkpoint副本。如果并行度=64，Checkpoint=500GB，总本地占用=500GB（不是64×500GB——每个TM只保存自己的那部分）
   - 解方：监控本地磁盘使用量；设置Local Recovery只保留最近N个Checkpoint（通过`state.backend.local-recovery.cleanup-strategy`）

---

## 4. 项目总结

### 大状态优化检查清单

```
□ 使用RocksDB State Backend（状态 > 1GB必选）
□ 开启增量Checkpoint
□ 设置合理的State TTL + Compaction清理
□ 配置Managed Memory（不少于状态大小的1.5倍）
□ 调整RocksDB writebuffer.block-cache-compaction参数
□ 开启Local Recovery（加速故障恢复）
□ 启用Checkpoint压缩
□ 反压时开启Unalign Checkpoint
□ 业务层面做状态裁剪（只保留必要的key）
□ 监控RocksDB指标（write stall / compaction / block cache）
```

### 参数调整优先级

```
1. Managed Memory 大小（最基础）
2. Write Buffer 大小 + 数量（影响写入吞吐）
3. Block Cache 大小（影响读取命中率）
4. Compaction 线程数（影响后台IO）
5. SST 目标大小（影响Compaction频率）
```

### 注意事项
- RocksDB的写放大因子通常在10-30之间——稳定状态下，每次写入最终会产生10-30倍的磁盘IO
- 增量Checkpoint的"增量"不是无限小的——它是"两次全量Checkpoint之间的diff"。如果全量Checkpoint的间隔很长（因为增量），第二次增量可能等于一次全量
- Local Recovery占用本地磁盘，每个TM约保留（状态大小 / TM数）× 2（全量+最近一次增量）

### 常见踩坑经验

**案例1：RocksDB的SST文件在HDFS上持续积累，没有自动清理**
- 根因：Flink 1.16之前的增量Checkpoint不自动清理过期SST文件
- 解方：升级到1.17+并开启cleaner；或手动编写清理脚本定期清理HDFS上超过N天的SST

**案例2：State TTL设置了7天但状态大小没有减少**
- 根因：TTL的清理发生在"读取时"或"Compaction时"。如果key没有被访问也没有被Compaction覆盖，过期状态可能一直存在
- 解方：开启`cleanupInRocksdbCompactFilter`；或主动触发全量Snapshot来清理

**案例3：开启增量Checkpoint后Checkpoint大小忽大忽小**
- 根因：增量Checkpoint只上传变化的SST。如果恰好跨了一个Compaction边界（大量SST变化），这次Checkpoint会比平时大很多
- 解方：这是正常现象。关注"平均"大小而非单次大小；设置Checkpoint Timeout足够覆盖峰值

### 优点 & 缺点

| | 大状态优化方案（增量+Rocks+Spill+TTL） | 默认配置无优化 |
|------|-----------|-----------|
| **优点1** | 增量Checkpoint大幅减少每次快照传输量 | 全量Checkpoint，大状态下耗时久 |
| **优点2** | RocksDB Memory Managed统一管理缓存，减少OOM | 堆内状态，容量受限 |
| **优点3** | Local Recovery加速故障恢复（本地读 vs 远程下载） | 远程恢复，速度慢10-100倍 |
| **优点4** | State TTL自动清理过期数据，控制状态膨胀 | 无TTL，状态无限增长直至OOM |
| **缺点1** | RocksDB调参复杂，需要深入理解LSM-Tree原理 | 默认配置即用，无需调参 |
| **缺点2** | Local Recovery占用本地磁盘空间 | 无需本地磁盘 |

### 适用场景

**典型场景**：
1. 大状态作业（>10GB）——广告计费、用户画像、实时聚合等GB-TB级状态
2. 高频Checkpoint场景——增量Checkpoint大幅减少IO开销
3. 故障恢复时间敏感——Local Recovery在恢复时节省数分钟
4. 状态需定期清理——TTL自动淘汰不活跃key

**不适用场景**：
1. 小状态作业（<1GB）——HashMapStateBackend更简单高效，无需RocksDB
2. 纯无状态转发作业——无需State Backend，无状态管理开销

### 思考题

1. State TTL的清理时机有两种：OnCreateAndWrite（写入和创建时更新）和OnReadAndWrite（读取时也更新）。一个用户每天登录一次，7天不活跃后状态过期——用OnCreateAndWrite会在第8天过期；用OnReadAndWrite会在第8天还是第15天过期？（提示：用户每天登录读取一次状态）

2. RocksDB的Compaction写放大（Write Amplification）是什么意思？Level Style Compaction的写放大为什么大约是10？如果我把Level从默认的7层减少到4层，写放大和读放大会怎么变化？

---

> **完整代码**：本章完整代码请参考附录或访问 https://github.com/flink-column/flink-practitioner  
> **思考题答案**：见附录文件 `appendix-answers.md`
