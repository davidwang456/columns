# 卷〇 · 附录：模块与 `BUILD_WITH_MODULES` 对照表

> 本附录摘自仓库根目录 [README.md](d:/software/workspace/redis/README.md)（「Redis data types, processing engines, and capabilities」一节）的约定，供专栏读者**对照自己的二进制是怎么编出来的**，避免照抄带模块的命令却未加载能力。

**总则（README 原文要点）**

- 功能名前缀有 **星号（\*）** 的：从源码构建时需要 **`BUILD_WITH_MODULES=yes`**（参见 README 的 [Build Redis from source](d:/software/workspace/redis/README.md)）。
- **无星号**：按当前官方开源产品线的默认描述，视为常规发行能力；具体仍以你本机 `INFO server` 与 `MODULE LIST` 为准。
- **文档与二进制**：redis.io 上的 JSON、Query Engine、Time series 等说明，可能同时覆盖云与自管形态；若命令报错 `unknown command`，先查是否 **未编译/未加载模块**，不要先怀疑「Redis 是假的」。

---

## 一、README 中带 `*` 的能力（需 `BUILD_WITH_MODULES=yes`）

| 能力 | README 中的标记 | 典型用途（简述） |
|------|-----------------|------------------|
| [Bloom filter](https://redis.io/docs/latest/develop/data-types/probabilistic/bloom-filter/) | \* | 可能存在性判断、防刷、唯一性预检查 |
| [Cuckoo filter](https://redis.io/docs/latest/develop/data-types/probabilistic/cuckoo-filter/) | \* | 可删改场景的近似集合成员检测 |
| [t-digest](https://redis.io/docs/latest/develop/data-types/probabilistic/t-digest/) | \* | 分位数、延迟/监控聚合 |
| [Top-k](https://redis.io/docs/latest/develop/data-types/probabilistic/top-k/) | \* | 流式 TopK 热点 |
| [Count-min sketch](https://redis.io/docs/latest/develop/data-types/probabilistic/count-min-sketch/) | \* | 事件频次估计 |

**专栏写作纪律**：凡涉及上述命令的章节，文首必须写清 **编译开关 + 是否需 `MODULE LOAD`**（若你的安装方式需要）。

---

## 二、README 中无 `*` 的能力（仍请核对本地）

| 能力 | 说明 |
|------|------|
| String / Hash / List / Set / Sorted set | 核心类型；Hash 另见 [字段级过期](https://redis.io/docs/latest/develop/data-types/hashes/#field-expiration) |
| JSON | 嵌套文档 + JSONPath；常与 Query Engine 联用 |
| Redis Query Engine | 文档/二级索引/全文/向量查询等（以你本机命令为准） |
| Vector set (beta) | 向量相似检索；源码见 `modules/vector-sets` |
| Geospatial / Bitmap / Bitfield / HyperLogLog | 核心或字符串扩展能力 |
| Time series | README 未标 `*`；仍建议标注**最低版本** |
| Pub/Sub、Stream、Transaction、Programmability (Lua) | 消息与可编程 |

---

## 三、源码构建命令速查（Linux / WSL 常见）

```bash
make
make BUILD_WITH_MODULES=yes
```

```text
redis-server --version
redis-cli MODULE LIST
```

---

## 四、本地仓库延伸阅读

- 构建说明：`README.md` → *Build Redis from source*
- 命令元数据：`src/commands/`、`src/commands.def`
- 向量集合：`modules/vector-sets/README.md`
