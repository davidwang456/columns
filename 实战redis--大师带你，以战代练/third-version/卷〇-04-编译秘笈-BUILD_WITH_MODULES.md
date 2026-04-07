本故事纯属虚构，如有雷同，纯属巧合。

> **版本说明**：编译步骤以仓库 [README.md](d:/software/workspace/redis/README.md) 各发行版为准；以下为常见 Linux/WSL 心智模型。

---

## 编译秘笈：`make` 与概率模块

**大师**：只会 `docker pull` 不算错，但**不会编译**就读不懂「为什么我这台机子没有 Bloom」。秘笈只有两行：

```bash
make
make BUILD_WITH_MODULES=yes
```

**小白**：第二行会慢多少？

**大师**：多模块、多测试，**慢得有理**。换的是「**命令全集**」与对照源码的勇气。

---

## 何时必须 `BUILD_WITH_MODULES=yes`

凡 README 中带 **\*** 的概率数据结构：**Bloom、Cuckoo、t-digest、Top-k、Count-min sketch**。写 [卷二-12](卷二-12-概率数据结构-模块篇.md) 前，先在本机执行：

```text
MODULE LIST
```

**大师**：列表空，不一定是错——可能你故意最小构建。**错的是文档写「直接复制」却不交代构建**。

---

## 验证清单

```bash
./src/redis-server --version
./src/redis-cli MODULE LIST
./src/redis-cli COMMAND COUNT
```

---

## 收式

附录全文：[卷〇-附录-模块与BUILD_WITH_MODULES对照表.md](卷〇-附录-模块与BUILD_WITH_MODULES对照表.md)。  
**卷一**起进入兵器谱：[卷一-05-字符串-破剑式新编.md](卷一-05-字符串-破剑式新编.md)。
