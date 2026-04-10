# 第 15 章：批量——BufferedMutator、batch、背压

> 统一模板与检查表：[../00-template-pack.md](../00-template-pack.md)
> 官方文档：[HBase Reference Guide](https://hbase.apache.org/docs)

上一章：[第 14 章](14-第14章-Filter-FilterList代价与误用.md) | 下一章：[第 16 章](16-第16章-异步客户端-AsyncConnection-AsyncTable.md)

---

**受众：主【Dev】 难度：中级**

### 0）本章路线图（由浅入深）

| 层次 | 你要达成的状态 | 建议用时 |
|------|----------------|----------|
| L1 听懂 | 知道逐条 Put 的 RPC 代价；buffer 与背压概念 | 15 min |
| L2 能做 | 用 BufferedMutator 完成批量写入并处理异常类 | 50 min |
| L3 能辩 | 能设计「失败重试 + 死信 + 限流」管道 | 数据工程 |

### 开场一分钟（趣味钩子）

逐条 `table.put` 像**每买一瓶水就刷一次信用卡**：银行（集群）和你（客户端）都累。`BufferedMutator` 像**团购**：一次刷卡多瓶水——但刷爆了要处理**部分退款**（部分失败、重试）。

### 1）项目背景

- **开发**：高吞吐写入路径；必须理解 flush、异常与 **close 隐式 flush**。
- **运维**：关注 memstore、flush、RS 压力；批量导入要错峰。
- **测试**：部分失败、进程 kill 中途、buffer OOM；断言不能只判断「没抛异常」。
- **若跳过本章**：导入任务「跑得慢」或「跑崩集群」二选一。

### 2）项目设计（大师 × 小白）

- **小白**：「for 循环 put 一万次？」
- **大师**：「RPC 爆炸；用 **`BufferedMutator`** 或合理 **batch**。」
- **小白**：「出错了呢？」
- **大师**：「看异常与 **`RetriedMutationsException`** 等；要有 **重试与死信** 策略。」
- **小白**：「buffer 越大越快？」
- **大师**：「越大越可能 **OOM** 与长卡顿；要压测折中。」
- **小白**：「和 Spark 写入比呢？」
- **大师**：「思想类似：都要**分区并行 + 批量 + 背压**；连接器细节见集成章。」
- **段子**：小白说「我 finally 里没 close mutator。」大师：「你写了一本《半本日记》。」

### 3）项目实战

```java
try (Connection conn = ConnectionFactory.createConnection(conf);
     BufferedMutator mutator = conn.getBufferedMutator(TableName.valueOf("training", "orders"))) {
  for (int i = 0; i < 1000; i++) {
    Put p = new Put(Bytes.toBytes("rk" + i));
    p.addColumn(Bytes.toBytes("d"), Bytes.toBytes("v"), Bytes.toBytes("x"));
    mutator.mutate(p);
  }
}
```

**对比实验**

- **同步逐条** `table.put` vs **`BufferedMutator`**：记录吞吐、客户端 CPU、RS 请求数（定性即可）。
- **故障注入（测试环境）**：`kill -9` 客户端中途，观察已刷盘与未刷盘边界（与业务幂等结合讨论）。

**验收**：对比表 + 团队选定的 **buffer 参数初值** 与理由（可引用压测计划编号）。

### 4）项目总结

- **优点**：显著提升吞吐；适合导入与异步管道。
- **缺点**：错误处理复杂；内存与 buffer 需调参；与幂等强相关。
- **适用**：批量导入、异步管道落库、日志归档。
- **注意**：`close` 前隐式 flush；监控客户端堆与 GC。
- **踩坑**：buffer 过大 OOM；忽略部分失败；无死信队列。
- **测试检查项**：部分失败、进程 kill 中途；重试风暴。
- **运维检查项**：导入窗口；RS flush 队列长度与告警。

### 5）课堂自测与作业（讲师可选用）

**自测**

1. BufferedMutator 主要减少什么开销？
2. 为何说批量写入与「超时未知」是同一章故事的两面？
3. `close()` 对 mutator 意味着什么？

**作业**

- 画一张导入管道：源 → 解析 → 批量写 → 死信 → 对账；标注每步幂等键。

---

**返回目录**：[../README.md](../README.md)
