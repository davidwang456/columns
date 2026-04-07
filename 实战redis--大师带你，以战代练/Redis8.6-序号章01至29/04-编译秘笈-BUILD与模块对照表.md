# 第 04 章 · 编译秘笈：`BUILD_WITH_MODULES` 与模块对照表

本故事纯属虚构，如有雷同，纯属巧合。

> **版本说明**：编译步骤以仓库 [README.md](../../README.md) 各发行版为准；以下为常见 Linux/WSL 心智模型。

## 本话目标

- 分清：**默认 `make` 与 `BUILD_WITH_MODULES=yes` 差在哪**。  
- 会自检：`MODULE LIST`、`COMMAND INFO`，避免「抄文档却环境不对」。

## 项目背景

**贯穿设定**：同一**某电商平台**上，常见会用到 **Bloom（去重预判）**、**TimeSeries（指标曲线）**、**JSON（文档字段）** 等**模块能力**；它们不是「装了 Redis 就一定有」。

**与本章关系**：本机敲 `BF.ADD` 报错，多半是**构建选项或镜像版型**未带对应 `.so`，而非业务写错。开发、预发、生产的 **`MODULE LIST` 是否一致**，要在上线前对表。本章把「环境与命令是否匹配」钉死，后面模块相关章节才不会断层。

## 步步引导：为什么我这没有 Bloom？

**大师**：你照着文档敲 `BF.ADD`，屏幕上回了什么？

**小白**：说不认识命令……弟子第一反应是：文档写错了？

**大师**：先别急下这个判断。你不妨**换一个问法**：「此刻连上的这颗 Redis，**身上挂了哪些模块**？」——哪条命令能告诉你？

**小白**：……`MODULE LIST`？若是空的……

**大师**：空，未必是坏事，可能只是**刻意瘦身的构建**。你再往上追问：**这颗二进制当初是怎么 `make` 出来的？镜像 Dockerfile 里写了哪一步？** 答案往往在**构建说明**，不在**教程正文**。

**小白**：可弟子是 Docker 拉的官方镜像，也会缺吗？

**大师**：「官方」也会**分版型**。你且养成习惯：**读完镜像页脚那一小段说明**，对照 README 里带 `*` 的能力——**不是「官方」二字自动等于「全集」**。

**小白**：那我在源码目录 `make` 两次，会不会把自己绕糊涂？

**大师**：会的，若**安装路径**与**参数**全靠脑子记。你不妨用**固定脚本**或**不同前缀**，让「昨天那颗」和「今天这颗」**各居其所**，日后对账不费劲。

**小白**：生产环境要不要也上全模块？

**大师**：先问**业务要不要**，再问**攻击面要不要**。常常**够用就好**——开发机宽一些，线上**收紧一些**，反而是成熟 team's 常态。

## 小剧场：借来的剑

小白抱怨：「江湖秘籍写 Bloom，我手里却是光板剑。」大师：**「剑谱第一页写的是铸剑炉在哪；你跳页开练，划伤的只能是自己。」**

---

## `make` 与概率模块

**大师**：只会 `docker pull` 不算错，但**不会编译**就读不懂「为什么我这台机子没有 Bloom」。秘笈核心就两行：

```bash
make
make BUILD_WITH_MODULES=yes
```

**小白**：第二行会慢多少？

**大师**：多模块、多测试，**慢得有理**。换的是「**命令全集**」与对照源码的勇气。第一次编译去泡杯茶，回来若见 `Linking … redis-server`，心中当念：**这是正经功夫**。

**小白**：两棵二进制能并存吗？

**大师**：实用做法是**不同前缀或不同目录安装**，或 Docker 一镜像一事。别在同一 `src` 目录里来回 `make clean` 到怀疑人生——用脚本钉参数。

---

## 何时必须 `BUILD_WITH_MODULES=yes`

凡 README 中带 **\*** 的概率数据结构：**Bloom、Cuckoo、t-digest、Top-k、Count-min sketch**。读 [12-概率模块-Bloom与伙伴.md](12-概率模块-Bloom与伙伴.md) 前，先在本机执行：

```text
MODULE LIST
```

**大师**：列表空，不一定是错——可能你故意最小构建。**错的是文档写「直接复制」却不交代构建**，更错的是**生产与文档各跑各的**。

---

## 验证清单（建议抄到团队 Wiki）

```bash
./src/redis-server --version
./src/redis-cli MODULE LIST
./src/redis-cli COMMAND COUNT
./src/redis-cli COMMAND INFO BF.ADD
```

最后一行若返回空或错误，**别骂客户端**——先骂**构建选项**。

---

## 常见踩坑

| 现象 | 可能原因 |
|------|----------|
| `unknown command` | 未编译模块 / 未 `LOADMODULE` / 云厂商裁剪版 |
| 链接错误 | 缺 `libssl-dev`、工具链过旧 |
| 与 Docker 行为不一致 | 镜像不是你自己 `BUILD_WITH_MODULES` 打的那颗 |

---

## 附：模块与 `BUILD_WITH_MODULES` 对照表（完整速查）

> 摘自仓库根目录 [README.md](../../README.md)（「Redis data types, processing engines, and capabilities」一节）。

**总则**

- 功能名前缀有 **星号（\*）** 的：从源码构建时需要 **`BUILD_WITH_MODULES=yes`**。  
- **无星号**：按当前官方开源产品线的默认描述，视为常规发行能力；具体仍以你本机 `INFO server` 与 `MODULE LIST` 为准。  
- 若命令报错 `unknown command`，先查是否 **未编译/未加载模块**。

### 带 `*` 的能力（需 `BUILD_WITH_MODULES=yes`）

| 能力 | 典型用途 |
|------|----------|
| [Bloom filter](https://redis.io/docs/latest/develop/data-types/probabilistic/bloom-filter/) | 可能存在性判断、防刷、唯一性预检查 |
| [Cuckoo filter](https://redis.io/docs/latest/develop/data-types/probabilistic/cuckoo-filter/) | 可删改场景的近似集合成员检测 |
| [t-digest](https://redis.io/docs/latest/develop/data-types/probabilistic/t-digest/) | 分位数、延迟/监控聚合 |
| [Top-k](https://redis.io/docs/latest/develop/data-types/probabilistic/top-k/) | 流式 TopK 热点 |
| [Count-min sketch](https://redis.io/docs/latest/develop/data-types/probabilistic/count-min-sketch/) | 事件频次估计 |

### 无 `*` 的能力（仍请核对本地）

| 能力 | 说明 |
|------|------|
| String / Hash / List / Set / Sorted set | 核心类型；Hash 另见 [字段级过期](https://redis.io/docs/latest/develop/data-types/hashes/#field-expiration) |
| JSON | 嵌套文档 + JSONPath；常与 Query Engine 联用 |
| Redis Query Engine | 文档/二级索引/全文/向量查询等（以本机命令为准） |
| Vector set (beta) | 向量相似检索；源码见 `modules/vector-sets` |
| Geospatial / Bitmap / Bitfield / HyperLogLog | 核心或字符串扩展能力 |
| Time series | README 未标 `*`；仍建议标注**最低版本** |
| Pub/Sub、Stream、Transaction、Programmability (Lua) | 消息与可编程 |

### 延伸阅读

- 命令元数据：`src/commands/`、`src/commands.def`  
- 向量集合：`modules/vector-sets/README.md`

---

## 动手试一试

在**源码树**里（路径见 [01 章](01-前传-Win11与WSL2尝鲜Redis86.md)）：

1. `make -j` 成功后 `./src/redis-cli MODULE LIST`，记下**是否为空**。  
2. `make BUILD_WITH_MODULES=yes`（或团队约定的目标）再来一次 `MODULE LIST`，对比差异。  
3. `./src/redis-cli COMMAND INFO BF.ADD`（若仍失败，把错误原文贴进团队文档——**这是资产**）。

## 实战锦囊

- 在 **CI** 里缓存编译产物时，同时缓存 **`redis-server --version` 与 `MODULE LIST` 输出**，排障先对齐环境。  
- 给业务同学一份 **「我们线上到底有没有 Bloom」** 的明确答复，避免文档复制粘贴事故。  
- 把两次 `MODULE LIST` 的输出**对比留存**，以后排「环境不对」省半天嘴仗。

---

## 收式

**小白**：弟子模块列表里终于看见 Bloom 了，像捡到五虎将。

**大师**：能编译、能列、能讲清边界，才算读过秘笈。下一章从最常用的 **String** 开始：[05-字符串-破剑式新编.md](05-字符串-破剑式新编.md)。
