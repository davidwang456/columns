# 第 36 章：综合实战——亿级订单 HBase + Elasticsearch

> 统一模板与检查表：[../00-template-pack.md](../00-template-pack.md)
> 官方文档：[HBase Reference Guide](https://hbase.apache.org/docs)

上一章：[第 35 章](35-第35章-Replication-DR拓扑与切换演练.md) | 下一章：[第 37 章](37-第37章-选修-Phoenix-SQL层与HBase查询.md)

---

**受众：主【Dev、Ops、QA】 难度：综合（Capstone）**

### 0）本章路线图（由浅入深）

| 层次 | 你要达成的状态 | 建议用时（小组） |
|------|----------------|------------------|
| L1 能跟 | 能说清「HBase 明细 + ES 检索」边界、双写风险与**以谁为准** | 半日 |
| L2 能做 | 完成 RowKey 编解码单测 + 写路径 + 一种读路径（Scan 或批量 Get）+ 压测表头 | 2～3 日 |
| L3 能辩 | 通过跨部门答辩：一致性、降级、Runbook、测试与容量 | 按本章评分表 |

### 开场一分钟（趣味钩子）

把本方案想成**餐厅后厨 + 外卖搜索**：HBase 是冷库里的「整箱食材与出餐明细」（海量、便宜、按桌号取货快）；ES 是外卖 App 上的「筛选与排序」（运营爱用）。最怕的是：**外卖上架了，冷库没货**——所以本章反复钉死一句：**HBase 是事实来源，ES 是投影**。

---

## 1）项目背景

- **业务/开发**：订单量达**亿级**，需支持：按 `orderId` 查详情、按 `userId` 拉**最近 N 单**（高 QPS）、运营侧**多条件检索**（状态、时间、关键词、地域等）。单一 HBase 表难以同时优化所有访问模式。
- **运维/SRE**：双存储（HBase + ES）带来容量、备份、发布与故障域翻倍；需可观测性与切换 Runbook。
- **测试/质量**：双写一致性、超时重试、部分失败、ES 与 HBase 版本不一致时的用户体验需可验收。

**目标架构**：**HBase 为权威明细存储**；**Elasticsearch 为检索与列表投影**（可不含大字段）；RowKey 设计须**包含 userId** 以优化「我的订单」范围 Scan。

---

## 2）项目设计（大师 × 小白）

- **小白**：「只放 ES 行不行？省掉 HBase。」
- **大师**：「ES 擅长检索，但**海量明细行存、成本与压缩、与 Hadoop 生态批量分析**常不如 HBase+HDFS 组合稳妥；典型是 **ES 索引摘要 + HBase 存全量**。」
- **小白**：「双写会不会不一致？」
- **大师**：「会。工程上 **以 HBase 为准**；ES 用 **MQ 异步索引 + 对账补偿**；关键读路径可 **回源 HBase**。」
- **小白**：「RowKey 为什么要含 userId？」
- **大师**：「『我的订单』是 **userId 前缀下的时间倒序 Scan**；userId 进 RowKey 才能让数据在字典序上**聚集**，同时前面加 **短散列** 防热点。」
- **小白**：「运营要『全站关键词搜备注』，HBase 扛得住吗？」
- **大师**：「**扛不住当主路径**；应走 ES 或旁路索引，再用 `orderId` / `rowKey` 回源 HBase 取明细。」
- **小白**：「详情页只给 orderId，没有 userId 上下文怎么办？」
- **大师**：「要有 **`orderId → rowKey`** 映射（表或规则）、或 ES 文档冗余可回源字段；**禁止**为了查一条详情扫用户前缀。」
- **小白**：「MQ 丢了消息呢？」
- **大师**：「靠 **对账补偿**：定时比对 HBase 与 ES，差异入修复队列；关键促销期提高对账频率。」
- **段子**：小白说「我们 ES 查到就返回，HBase 慢慢补。」大师：「用户收到了『幽灵订单』，客服收到了『真实加班』。」

---

## 3）项目实战

### 3.1 RowKey 规范（须含 userId）

约定示例（字段均建议 **定长或补齐**）：

```text
RowKey = HexPrefix4(hash(userId)) + PadUserId(userId) + ReverseMillis(createdAt) + orderId
```

- `HexPrefix4`：例如对 `userId` 做稳定 hash 取 4 个 hex 字符，**打散**写入热点。
- `PadUserId`：固定宽度（如 20），左补零或规范化字符串，保证 Scan 边界正确。
- `ReverseMillis`：常用 `Long.MAX_VALUE - ts`，使**同一用户下新单排在 Scan 前部**（按字典序）。
- `orderId`：保证全局唯一，避免碰撞。

**主代码片段（写入 HBase）**：

```java
byte[] rowKey = OrderRowKeys.encode(userId, createdAt, orderId);
Put put = new Put(rowKey);
put.addColumn(Bytes.toBytes("d"), Bytes.toBytes("status"), Bytes.toBytes(order.getStatus()));
put.addColumn(Bytes.toBytes("d"), Bytes.toBytes("amt"), Bytes.toBytes(order.getAmount().toString()));
put.addColumn(Bytes.toBytes("d"), Bytes.toBytes("blob"), Bytes.toBytes(order.getDetailJson()));
table.put(put);
```

`OrderRowKeys.encode` 由团队实现并单测（覆盖边界 userId、相同毫秒多单）。

### 3.2 Elasticsearch 文档模型（摘要）

索引字段建议至少包含：`orderId`, `userId`, `status`, `createdAt`, `updatedAt`，以及检索需要的文本/标签；可选冗余 `rowKey` 字符串以便详情 **O(1) Get**（若可由规则从 `orderId` 推导则可不存）。

**异步索引（概念流程）**：

```text
业务写 HBase 成功 → 发 MQ（含 orderId、摘要字段）→ 消费者 bulk 写入 ES → 失败入 DLQ 重试
```

### 3.3 读路径

| 场景 | 实现 |
|------|------|
| 详情（已知 orderId） | 若 ES 文档带 `rowKey`：`Get(rowKey)`；否则用 **二级映射表**（`orderId → rowKey`）或规则反推 |
| 用户最近 N 单 | **HBase Scan**：`startRow = prefix(userId)`，`LIMIT N`，避免依赖 ES 排序深分页 |
| 运营多条件筛选 | **ES 查询** 得 `orderId` 列表 → **批量 Get**（`table.get(List<Get>)`），控制 batch 与超时 |

```java
List<Get> gets = orderIds.stream()
    .map(id -> new Get(rowKeyResolver.resolve(userIdContext, id)))
    .collect(Collectors.toList());
Result[] results = table.get(gets);
```

### 3.4 运维与观测

- **HBase**：RS 请求队列、block cache、flush/compaction、Region 热点；**表 Region 数与分裂**。
- **ES**：查询延迟、merge、线程池拒绝、磁盘水位；**索引副本与分片**。
- **应用**：双写失败率、MQ 积压、DLQ 深度、对账任务**不一致条数**。

### 3.5 故障注入演练（测试牵头）

在测试环境至少完成 **2 项** 并记录 RTO：

1. **单 RegionServer 宕机**：观察客户端重试与 P99；HBase 恢复后 ES 是否需补偿。
2. **ES 集群黄/红或查询超时**：应用降级——仅 HBase 路径（例如仅 orderId 详情可用）是否可接受；开关与文案。

### 3.6 交付物清单（小组提交）

1. **设计说明**（≤10 页）：RowKey、表结构、ES mapping、双写与对账、降级。
2. **核心代码**：RowKey 编解码单测 + 写路径 + 一种读路径（Scan 或 mget）。
3. **压测报告**：参照第 24 章模板；注明数据量级（可缩小为千万级模拟）。
4. **Runbook**：快照/备份、ES 索引重建、常见告警处置。
5. **测试报告**：一致性用例、超时用例、故障注入结果。

---

## 4）项目总结

### 优点与缺点

| | HBase + ES |
|---|------------|
| **优点** | 各取所长：HBase 扛明细与 user 维 Scan；ES 扛多维检索；RowKey 含 userId 优化「我的订单」 |
| **缺点** | 双写一致性与运维成本高；团队技能栈要求广 |

### 适用场景

- 电商/物流/支付等 **海量订单 + 多维运营查询**；可扩展到「用户行为 + 检索」同类问题。

### 注意事项

- **以 HBase 为事实来源**；ES 仅索引视图。
- **深分页**避免直连 ES；用 search_after 或限制页深。
- **批量 Get** 限制每批个数，防止打爆 RS。
- **GDPR/脱敏**：日志与 ES 字段合规。

### 常见踩坑

- RowKey **只有 hash** 没有可 Scan 的 userId 段，导致「我的订单」只能扫全表或全依赖 ES。
- **ES 当主库**，HBase 挂掉无法恢复业务。
- **双写先写 ES** 后写 HBase，崩溃导致索引有、库无。
- **不定长 userId** 导致 Scan 前缀错误。

---

## 跨部门答辩评分标准（建议）

| 维度 | 权重 | 考察点 |
|------|------|--------|
| RowKey 与访问模式 | 25% | 是否同时服务 user 维与 orderId；热点与定长 |
| 一致性工程 | 25% | MQ、对账、幂等、降级 |
| 可运维性 | 20% | 监控、备份、Runbook、容量 |
| 测试与质量 | 20% | 用例覆盖、故障注入、报告 |
| 表达与协作 | 10% | 跨角色答疑 |

**通过线**：各维度不低于 60%；**一致性**与 **RowKey** 单项不得低于 50%。

---

### 5）复盘节奏、课堂自测与「电梯演讲」（讲师可选用）

**复盘节奏（建议 2 h 工作坊）**

1. 每组 8 min 讲 RowKey 与访问路径（必须画**一条写序列图 + 一条读序列图**）。
2. 10 min 交叉质询：只准问「失败时怎么办」（超时、MQ 积压、ES 黄、HBase RS 挂）。
3. 讲师点评：对照上文评分表，只评**最危险的两点**，避免流水账。

**课堂自测（抢答）**

1. 为什么「以 HBase 为准」几乎总是双写系统的默认答案？反例场景是什么？
2. 深分页 + ES 排序会带来什么？本方案推荐怎么收口？
3. 批量 Get 时如何防止单次请求打爆 RS（列 3 个工程手段）？

**电梯演讲作业（每人 60 秒）**

- 模板：「我们存 **{什么}** 在 HBase，因为 **{访问模式}**；ES 存 **{什么投影}** 因为 **{检索模式}**；不一致时 **{用户可见行为}**，后台 **{对账/补偿}**。」

**一页纸交付（可挂 Wiki）**

| 项目 | 链接 / 位置 |
|------|-------------|
| RowKey 规范与单测 |  |
| 双写与对账设计 |  |
| 降级开关与文案 |  |
| Runbook（ES 重建 / HBase 快照） |  |
| 压测环境与 SLO |  |

---

**返回目录**：[../README.md](../README.md)
