# 第 9 章：Admin 建表——描述符、列族、预分区

> 统一模板与检查表：[../00-template-pack.md](../00-template-pack.md)
> 官方文档：[HBase Reference Guide](https://hbase.apache.org/docs)

上一章：[第 8 章](08-第08章-Scan基础-边界与caching-Scan非快照.md) | 下一章：[第 10 章](10-第10章-一致性与超时-行级原子与batch.md)

---

**受众：主【Dev、Ops】 难度：基础**

### 0）本章路线图（由浅入深）

| 层次 | 你要达成的状态 | 建议用时 |
|------|----------------|----------|
| L1 听懂 | 理解预分区解决的是「初始热点」而非一切热点 | 20 min |
| L2 能做 | 用 Java Admin 创建带 splitKeys 的表，并 `describe` 对照 | 45 min |
| L3 能辩 | 能参与 DDL 评审：列族属性、分裂键与 RowKey 设计是否一致 | 架构级 |

### 开场一分钟（趣味钩子）

不预分区就像**新开商场只开一个收银台**：开业促销（批量导入）当天，队伍会从一楼排到地铁口。`splitKeys` 是**提前多开几个收银台**——不等顾客把台子压垮。

### 1）项目背景

- **开发**：代码化建表与 CI 环境；避免「shell 随手建表」无法审计。
- **运维**：与 shell 对照，统一 split 策略；变更窗口评估 `disable` 影响。
- **测试**：自动化环境里表结构可重复创建；用例注明**是否空表启动**。
- **若跳过本章**：上线初期单 Region 扛全量写入，热点从出生第一天开始。

### 2）项目设计（大师 × 小白）

- **小白**：「为啥要 splitKeys？」
- **大师**：「避免**单 Region 扛全部初始写入**，形成热点。」
- **小白**：「splitKeys 怎么选？」
- **大师**：「来自** RowKey 分布预估** + 业务前缀；乱选会导致空 Region 或仍然热点。」
- **小白**：「建表后能改预分区吗？」
- **大师**：「不能简单「改键」；常需**新表迁移**或依赖分裂策略（运维成本高）。」
- **小白**：「列族属性要在建表时定死吗？」
- **大师**：「很多可 `alter`，但**生产变更**要走评审；部分变更触发 major compaction。」
- **段子**：小白说「我们先建表，RowKey 下周再想。」大师：「可以，顺便把**简历**也更新一下。」

### 3）项目实战

```java
try (Connection conn = ConnectionFactory.createConnection(conf);
     Admin admin = conn.getAdmin()) {
  TableName tn = TableName.valueOf("training", "orders_java");
  TableDescriptorBuilder tdb = TableDescriptorBuilder.newBuilder(tn);
  ColumnFamilyDescriptor cf = ColumnFamilyDescriptorBuilder
      .newBuilder(Bytes.toBytes("d")).setMaxVersions(1).build();
  tdb.setColumnFamily(cf);
  byte[][] splits = new byte[][] {
    Bytes.toBytes("m"), Bytes.toBytes("t")
  };
  if (!admin.tableExists(tn)) {
    admin.createTable(tdb.build(), splits);
  }
}
```

**步骤**

1. 运行上述代码（或团队等价封装），`describe` 表验证列族与 Region 数。
2. 向各 Region **均匀 vs 顺序**写入小批量数据，用 UI 观察请求分布（定性）。
3. **验收**：提交代码 + 一句说明：split 键与业务 RowKey 的对应关系。

### 4）项目总结

- **优点**：可重复、可版本管理；与基础设施即代码一致。
- **缺点**：误删表代价高；错误 split 导致资源浪费。
- **适用**：自动化部署、多环境；大表上线前必备。
- **注意**：`disable` 期间不可用；生产 DDL 双人复核。
- **踩坑**：生产无预分区 + 顺序 RowKey；split 与 RowKey 设计脱节。
- **运维检查项**：DDL 评审与变更窗口；是否有回滚方案。
- **测试检查项**：CI 中建表失败重试是否安全（exists 检查）。

### 5）课堂自测与作业（讲师可选用）

**自测**

1. 预分区主要缓解哪种阶段的性能问题？
2. `createTable` 前 `tableExists` 为什么在多环境部署里常见？
3. 列族 `VERSIONS` 设大了有什么副作用？

**作业**

- 画一张表：未来 3 个月 RowKey 前缀分布假设 + 对应 split 方案（可与第 13 章合并）。

---

**返回目录**：[../README.md](../README.md)
