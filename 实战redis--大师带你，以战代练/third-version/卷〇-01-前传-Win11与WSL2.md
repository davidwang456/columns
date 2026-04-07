本故事纯属虚构，如有雷同，纯属巧合。

> **版本说明**：以 **Redis 8.6.x** 为推荐版本；`redis-server --version` 与本文不一致时，以你本机为准。

> **江湖规矩**：「大师」甩锅，「小白」踩坑；虚构叙事，实操请当真。

---

## 卷〇前传：不装整机虚拟机，也能把 Redis 8.6 揣兜里

**大师**：当年教你「Windows10 不装虚拟机玩转 Redis」，如今 Win11 普及，**WSL2** 才是当代「内功心法」——轻量、能跑原生 Linux 二进制，还能和你本机开发工具无缝勾连。

**小白**：弟子装过 WSL，一开机内存就少一截……

**大师**：所以叫**实战**：开发机开 WSL2，**生产**用 Docker 或裸机 Linux。前传只解决「**我今晚就要敲 `redis-cli`**」。

---

## 路线 A：WSL2 + 源码或包管理器

1. 启用 WSL2（微软文档为准），装 **Ubuntu 22.04/24.04** 一类 LTS。  
2. 进发行版后安装依赖，按仓库 [README.md](d:/software/workspace/redis/README.md) 的 *Build Redis from source* 一节执行。  
3. 你当前工作区已是源码树时：

```bash
cd /mnt/d/software/workspace/redis   # 按你实际挂载路径调整
make -j
./src/redis-server --daemonize yes
./src/redis-cli PING
```

**小白**：Windows 侧还要装 Redis for Windows 吗？

**大师**：**官方主线不维护旧版 Windows 原生服务端**。练真功，请 **WSL2 / Docker / 远程 Linux** 三选一，别在过期二进制上浪费时间。

---

## 路线 B：仅客户端在 Windows

应用跑在 Windows，Redis 在远端或 WSL：装 **Redis Insight**、或用 **WSL 里的 `redis-cli`** 连 `host.docker.internal` / `127.0.0.1`（注意防火墙与端口映射）。

---

## 深度一瞥：为何总提「本机源码树」

**大师**：你 `clone` 下来的不仅是能跑的程序，还是**活教材**：命令从 `src/commands/*.json` 起笔，实现在 `t_*.c`。前传不展开，记住路径，后文 **卷六** 再来挖。

---

## 收式

**小白**：弟子今晚 PING 通了，算不算入门？

**大师**：算**踏进山门**。明日读 [卷〇-02-潮式-Docker与Compose.md](卷〇-02-潮式-Docker与Compose.md)，学如何把「山门」装进一条 `docker compose up`。
