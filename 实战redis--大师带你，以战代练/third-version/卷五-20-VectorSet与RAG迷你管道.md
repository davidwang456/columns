本故事纯属虚构，如有雷同，纯属巧合。

> **版本说明**：**Vector set** 见 `modules/vector-sets/README.md`；Redis **8+** 主线叙事为合并进默认构建，仍以 `redis-cli` 实测为准。

---

## Vector set + RAG：Redis 管近邻，模型管「懂」

**大师**：**嵌入（embedding）在应用或模型服务生成**，Redis **`VADD` 存储、`VSIM` 搜近邻**，可选 **`FILTER`** 做属性裁剪。拼 prompt → LLM → 回答，便是**最小 RAG 管道**。

**小白**：Redis 是向量数据库吗？

**大师**：叫啥随市场。**工程上**问三件事：**延迟、召回、成本**——能答再贴标签。

---

## 极简命令流（维数请与模型一致）

```text
VADD kb VALUES 3 0.1 0.2 0.3 chunk:intro SETATTR {"doc":"readme"}
VSIM kb VALUES 3 0.11 0.19 0.28 COUNT 5 WITHSCORES
```

---

## 深度挂钩

[`modules/vector-sets/`](d:/software/workspace/redis/modules/vector-sets) — HNSW、量化、`FILTER` 表达式。

---

## 收式

下一篇：[卷五-21-语义缓存与路由.md](卷五-21-语义缓存与路由.md)。
