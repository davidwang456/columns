# 第 33 章：RPC 与 Protobuf——服务边界

> 统一模板与检查表：[../00-template-pack.md](../00-template-pack.md)
> 官方文档：[HBase Reference Guide](https://hbase.apache.org/docs)

上一章：[第 32 章](32-第32章-MVCC与读点-行内可见性与Scan语义.md) | 下一章：[第 34 章](34-第34章-Coprocessor-Observer-Endpoint与发布风险.md)

---

**受众：主【Dev】 难度：高级**

### 0）本章路线图（由浅入深）

| 层次 | 你要达成的状态 | 建议用时 |
|------|----------------|----------|
| L1 听懂 | 客户端与服务端通过 Hadoop RPC + Protobuf，不是 REST | 15 min |
| L2 能做 | 列出 5 个读写相关 RPC 方法名（浏览接口即可） | 45 min |
| L3 能讲 | 能解释版本不匹配时为何「奇怪 ClassNotFound」 | 升级 |

### 开场一分钟（趣味钩子）

把 HBase 想成**前后端分离**：只不过前端是 Java Client，后端是 RS，中间是 **RPC 快递单（Protobuf）**。你改字段不通知对方，就像**收件人电话写错**——不是 HTTP 404，而是**序列化惨案**。

### 1）项目背景

- **开发**：自定义 Filter、Coprocessor 或排查「服务端到底执行了啥」时需能定位 RPC 层；升级时要关注 wire compatibility。
- **运维**：网络策略、TLS、Kerberos 与 RPC 端口；抓包权限与合规。
- **测试**：混合版本滚动升级的风险清单。
- **若跳过本章**：读源码像走进迷宫，没有「地图图层」。

### 2）项目设计（大师 × 小白）

- **小白**：「HBase 用 HTTP 吗？」
- **大师**：「客户端与服务端主要是 **Hadoop RPC + Protobuf** 定义的服务接口；不是 REST。」
- **小白**：「能 curl 调吗？」
- **大师**：「日常不行；要用客户端或专用工具。」
- **小白**：「protobuf 改字段会怎样？」
- **大师**：「**版本协商与兼容规则**决定生死；升级要读 release note。」
- **小白**：「我能反射调私有 RPC 吗？」
- **大师**：「可以，同时准备**简历**。」
- **段子**：小白在客户端塞了自定义对象。大师：「RS：这包裹拒收。」

### 3）项目实战（源码导读）

- [`MasterRpcServices.java`](../../../hbase-server/src/main/java/org/apache/hadoop/hbase/master/MasterRpcServices.java)
- 在 `hbase-server` 中查找 **RegionServer** 侧 `...RpcServices` 类（名称随版本如 `RSRpcServices`），列出 **5 个** 与读写相关的 RPC 方法名（从接口或类声明中浏览即可）。

**输出**：方法名列表 + 每个**一句话猜测职责**（可错，课堂上纠正）。

### 4）项目总结

- **优点**：读源码时知道从哪一层入手；升级有章可循。
- **缺点**：protobuf 与版本兼容细节多；网络与安全栈复杂。
- **适用**：自定义 filter / coprocessor 联调；性能与超时分析。
- **注意**：客户端与服务端版本匹配；禁止依赖未公开 API。
- **踩坑**：用反射 hack 私有 RPC；混用不同 minor 版本客户端。
- **测试检查项**：滚动升级组合矩阵；兼容性回归。
- **运维检查项**：RPC 端口与白名单；TLS 证书链。

### 5）课堂自测与作业（讲师可选用）

**自测**

1. 为何说 RPC 边界对 Filter / Coprocessor 联调重要？
2. 客户端与服务端版本不一致可能导致哪类错误（举例）？
3. Master 与 RS 的 RPC 面各解决什么问题（各一句）？

**作业**

- 画一张「一次 Put」从客户端调用栈到 RS 入口的**想象图**（不必精确，标 4～6 框即可）。

---

**返回目录**：[../README.md](../README.md)
