本故事纯属虚构，如有雷同，纯属巧合。

> **版本说明**：**Redis 8.6.x**；新命令以 [What's new 8.6](https://redis.io/docs/latest/develop/whats-new/8-6/) 与本地 `src/commands/*.json` 为准。

---

## 新总诀式：地图换了，指南针还是内存

**大师**：Redis 6 时代那段英文总诀，背的是**骨架**。今日地图上要再标五类地标：**JSON、Hash 字段 TTL、Vector set、Query Engine、Stream 幂等**——外加 **8.6** 的性能与运维补丁。

**小白**：弟子背不动……

**大师**：不用整段背。记住 **「我这条业务线要哪几块」** 即可。

---

## 8.6 增量速查（面试与排障用）

| 主题 | 关键词 |
|------|--------|
| Stream | `XADD` **`IDMP` / `IDMPAUTO`**（8.6.0），防重复追加 |
| 淘汰 | **`volatile-lrm` / `allkeys-lrm`**，按「最近修改」思路淘汰 |
| 可观测 | **`HOTKEYS`**、键内存直方图 |
| 性能官宣 | Vector set 插入/查询、Hash/Zset 内存与延迟（见发布公告） |
| 安全运维 | TLS 证书客户端认证、ACL/日志 PII 相关改进 |
| Time series | **NaN**、`COUNTNAN` / `COUNTALL`（见发行说明） |

---

## 能力分层（避免环境踩坑）

1. **核心类型**：String / Hash / List / Set / ZSet / Stream / Geo / Bitmap / Bitfield / HLL。  
2. **7.4+ / 8.0+**：Hash **字段级过期**（`HEXPIRE` 族；`HSETEX` 见 `since` 8.0）。  
3. **8.x 叙事**：**JSON**、**Vector set**、**Query Engine**（以你二进制 `COMMAND` 为准）。  
4. **带 `*` 模块**：Bloom / Cuckoo / CMS / Top-k / t-digest → **`BUILD_WITH_MODULES=yes`**。

---

## 源码行功（本地仓库）

- 事件循环：`ae.c`、`networking.c`、`server.c`  
- 类型实现：`t_string.c`、`t_hash.c`、`t_stream.c` …  
- 命令契约：`src/commands/*.json`

**大师**：总诀是**比例尺**；**`redis-server --version`** 是**图例**。图例不对，别怪地图骗人。

---

## 收式

下一篇：[卷〇-04-编译秘笈-BUILD_WITH_MODULES.md](卷〇-04-编译秘笈-BUILD_WITH_MODULES.md)。
