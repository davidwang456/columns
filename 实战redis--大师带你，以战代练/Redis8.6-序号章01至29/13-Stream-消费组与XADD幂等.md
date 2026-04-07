# 第 13 章 · Stream：消费组与 XADD 幂等

本故事纯属虚构，如有雷同，纯属巧合。

> **版本说明**：Stream 自 5.0；**`XADD` 的 `IDMP`/`IDMPAUTO` 自 8.6.0**（`src/commands/xadd.json`）。

## 本话目标

- 跑通 **消费组**：创建组、读消息、ACK。  
- 理解 **IDMP** 解决哪一类重复（写侧），消费侧仍要 **幂等**。

## 步步引导：别用 Stream 当聊天室

**小白**：我们实时推送用 Stream 行不？

**大师**：先问要不要 **持久、追溯、重放**。只要**广播且丢得起**，Pub/Sub 更轻（[14-PubSub家族.md](14-PubSub家族.md)）。

**小白**：消费者挂了，消息会丢吗？

**大师**：在 **PEL** 里躺着；要 **`XCLAIM`/超时转移** 策略，别假装看不见。

**小白**：我写重试又 `XADD` 一条一样的……

**大师**：这正是 **8.6 IDMP** 要解决的痛点之一；但**处理逻辑重复**仍可能发生，幂等键不能省。

## 小剧场：账房与伙计

账房记账（`XADD`），伙计领活（`XREADGROUP`），交差画押（`XACK`）——**领活不交差**，账上永远挂着悬案。

---

## Stream：消费组、与 8.6 的「不重复捆法」

> **Stream** 可以理解成**持久化的日志型消息流**（带 ID、可裁剪）；下面对话里偶尔叫它「索链」，只是形象说法。

**大师**：**`XADD` 记账，`XREADGROUP` 派活，`XACK` 销账**，Pending 里关着**超时未 ACK 的冤魂**——要定期 `XPENDING` / `XCLAIM` 超度。**8.6** 起生产端可叠 **`IDMP`**：**同一业务重试，`XADD` 不插两条**。

**小白**：那消费者还要幂等吗？

**大师**：**要**。IDMP 管**写侧**；**处理侧**仍可能重复投递，PEL 里见真章。面试常问：**「至少一次」下如何做幂等？**

**趣味比方**：Stream 是**账房**；消费组是**伙计分桌**；IDMP 是**防重复入账**——但伙计把同一笔银子数两遍，还得靠**你自己不 double spend**。

---

## 消费组最小闭环

```text
XGROUP CREATE orders $ MKSTREAM
XADD orders * sku 1001 qty 2
XREADGROUP GROUP g1 c1 COUNT 10 BLOCK 5000 STREAMS orders >
XACK orders g1 <id>
```

**小白**：`>` 是啥？

**大师**：「只读新消息」的_cursor；复盘旧账用 `0` 或具体 ID，别混用场景。

---

## IDMP 示例（≥8.6）

```text
XADD orders * IDMP pay-service pay-001 sku 1001 amount 99
XADD orders * IDMP pay-service pay-001 sku 1001 amount 99
XLEN orders
```

第二次不应增加长度（以实际版本为准）。

**约束**：与 **自动 ID `*`** 同用；详见 [`src/t_stream.c`](../../src/t_stream.c)。

---

## 运维与排障

- `XINFO STREAM` / `XINFO GROUPS`：长度、滞后、消费者状态。  
- 8.6 可带 **idmp-*** 统计字段；调 **duration/maxsize** 前读官方说明。  
- 磁盘与内存：`MAXLEN`、`~` 近似裁剪策略，权衡**丢历史**与**成本**。

---

## 与 List 一眼对照

| 能力 | Stream | List |
|------|--------|------|
| 消费组 | 有 | 无 |
| 持久消息 | 强 | 中 |
| 实现复杂度 | 中高 | 低 |

详见 [15-List与Stream混合战.md](15-List与Stream混合战.md)。

---

## 动手试一试（练习库，按顺序粘贴）

```text
XGROUP CREATE demo:stream $ MKSTREAM
XADD demo:stream * sku 1001 qty 1
XREADGROUP GROUP demo:g demo:c1 STREAMS demo:stream >
XACK demo:stream demo:g <上一条返回的ID>
XINFO GROUPS demo:stream
```

≥8.6 环境加练（若命令支持）：对**同一业务键**连做两次 `XADD ... IDMP ...`，`XLEN` 观察是否只长一条。

## 实战锦囊

- 监控 **lag** 与 **PEL 长度**，比只看 QPS 有用。  
- `MAXLEN ~` 裁剪前确认**法务/审计**是否允许丢历史。  
- 与业务约定 **幂等键**（订单号、支付流水号）落在消息 field 里。

---

## 收式

**小白**：弟子先闭环再玩 IDMP。

**大师**：下一章：[14-PubSub家族.md](14-PubSub家族.md)。
