# 第 6 章：Java 入门——Configuration、Connection、Table

> 统一模板与检查表：[../00-template-pack.md](../00-template-pack.md)
> 官方文档：[HBase Reference Guide](https://hbase.apache.org/docs)

上一章：[第 5 章](05-第05章-数据模型-RowKey列族Qualifier-Cell时间戳.md) | 下一章：[第 7 章](07-第07章-Put-Get-Delete与行大小意识.md)

---

**受众：主【Dev】 辅【QA】 难度：基础**

### 0）本章路线图（由浅入深）

| 层次 | 你要达成的状态 | 建议用时 |
|------|----------------|----------|
| L1 听懂 | 能解释 Connection 与 Table 的生命周期与轻重 | 15 min |
| L2 能做 | 用 try-with-resources 完成建连、getTable、关闭 | 40 min |
| L3 能讲 | 能说明线程安全边界与常见泄漏排查（jstack / 指标） | 进阶 |

### 开场一分钟（趣味钩子）

`Connection` 像**公司门禁卡**：办一张成本高，进出门刷就行；`Table` 像**当天访客贴纸**：轻、频繁领退。别每次进门都**重新办门禁卡**——那是线上事故的「文件描述符慈善家」。

### 1）项目背景

- **开发**：正确管理连接生命周期，避免连接泄漏；多线程服务中要明确 **Table 非线程安全** 的用法。
- **测试**：自动化用例使用相同模式，便于稳定性测试与资源泄漏检测（长稳跑）。
- **运维**：协助排查「客户端把 ZK / RS 连接打满」类问题，知道从应用侧查 Connection 池化是否合理。
- **若跳过本章**：后续 Put/Scan 写得再漂亮，也会在压力下「神秘卡死」。

### 2）项目设计（大师 × 小白）

- **小白**：「每次操作 new 一个 Connection？」
- **大师**：「**Connection 重、可复用**；`Table` 轻、用完关。推荐 **try-with-resources**。」
- **小白**：「线程安全吗？」
- **大师**：「**Table 非线程安全**；多线程要么每线程一个 Table，要么做好同步（见官方客户端文档）。」
- **小白**：「我用 Spring 单例 Bean 注入 Table？」
- **大师**：「要谨慎；常见是单例 **ConnectionFactory** + 短生命周期 **Table**，或封装线程本地。」
- **小白**：「Configuration 从哪来？」
- **大师**：「`HBaseConfiguration.create()` + 加载 `hbase-site.xml`；容器环境注意 **classpath 与挂载**。」
- **段子**：小白说「我 catch 了异常但没 close。」大师：「恭喜你实现了**资源泄漏的 try-finally 反面教材**。」

### 3）项目实战

参考本仓库 `ConnectionFactory` 注释（`hbase-client/.../ConnectionFactory.java`）。

**最小可运行骨架（必做）**

```java
Configuration conf = HBaseConfiguration.create();
// 本地可显式 addResource("hbase-site.xml");
try (Connection connection = ConnectionFactory.createConnection(conf);
     Table table = connection.getTable(TableName.valueOf("training", "orders"))) {
  // 下一章再写 Put / Get
}
```

**验收清单**

1. 程序正常退出后，用 `jps` + 短暂等待，确认无悬挂连接（或用运维提供的客户端指标）。
2. 故意去掉 `try-with-resources`，用 `lsof` 或类似工具观察 FD 增长（讲师演示）。

**进阶（选做）**

- 读官方 [Client Architecture](https://hbase.apache.org/book.html#client) 一页纸笔记：meta 缓存、线程模型关键词。

### 4）项目总结

- **优点**：API 清晰，与集群共享底层资源；生态成熟。
- **缺点**：错误使用会导致 FD、线程耗尽；异步路径另有一套（第 16 章）。
- **适用**：所有 Java 业务接入。
- **注意**：`close()` 与异常路径；超时配置见第 10、22 章。
- **踩坑**：在循环里反复 `createConnection` 不关闭；多线程共用一个 Table。
- **测试检查项**：长稳测试是否监控连接数；是否压测过异常重试下的资源释放。

### 5）课堂自测与作业（讲师可选用）

**自测**

1. Connection 与 Table 哪个更重？推荐复用谁？
2. 为何 Table 不建议多线程无锁共享？
3. `try-with-resources` 解决了哪类生产事故？

**作业**

- 按 [00-template-pack.md](../00-template-pack.md) 示例 A：完成 Put + Get（可与第 7 章合并提交）。

---

**返回目录**：[../README.md](../README.md)
