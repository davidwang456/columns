本故事纯属虚构，如有雷同，纯属巧合。

> **版本说明**：Stream 自 5.0；**`XADD` 的 `IDMP`/`IDMPAUTO` 自 8.6.0**（`src/commands/xadd.json`）。

---

## Stream：索链、消费组、与 8.6 的「不重复捆法」

**大师**：**`XADD` 记账，`XREADGROUP` 派活，`XACK` 销账**，Pending 里关着**超时未 ACK 的冤魂**。**8.6** 起生产端可叠 **`IDMP`**：**同一业务重试，`XADD` 不插两条**。

**小白**：那消费者还要幂等吗？

**大师**：**要**。IDMP 管**写侧**；**处理侧**仍可能重复投递，PEL 里见真章。

---

## 消费组最小闭环

```text
XGROUP CREATE orders $ MKSTREAM
XADD orders * sku 1001 qty 2
XREADGROUP GROUP g1 c1 STREAMS orders >
XACK orders g1 <id>
```

---

## IDMP 示例（≥8.6）

```text
XADD orders * IDMP pay-service pay-001 sku 1001 amount 99
XADD orders * IDMP pay-service pay-001 sku 1001 amount 99
XLEN orders
```

第二次不应增加长度（以实际版本为准）。

**约束**：与 **自动 ID `*`** 同用；详见 [`src/t_stream.c`](d:/software/workspace/redis/src/t_stream.c)。

---

## 运维

`XINFO STREAM` 在 8.6 可带 **idmp-*** 统计字段；调 **duration/maxsize** 前读官方说明。

---

## 收式

下一篇：[卷三-14-PubSub家族.md](卷三-14-PubSub家族.md)。
