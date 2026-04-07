# 第 01 章 · 前传：Win11 与 WSL2 尝鲜 Redis 8.6

本故事纯属虚构，如有雷同，纯属巧合。

> **版本说明**：以 **Redis 8.6.x** 为推荐版本；`redis-server --version` 与本文不一致时，以你本机为准。

> **江湖规矩**：「大师」甩锅，「小白」踩坑；虚构叙事，实操请当真。

> **给新读者**：**Redis** 是常用的内存型数据结构存储（很多人当缓存用）。在 **Windows** 上跑**官方主线**服务端，推荐 **WSL2** 或 **Docker**，不要依赖过期的「Windows 版 Redis 安装包」。本目录从第 01 章起**自成体系**，无需读过其他 Redis 书。

## 本话目标

- 说清：**为什么在 Windows 上优先 WSL2 / Docker**，而不是老旧「Windows 安装包」。  
- 跑通：**编译或安装后 `redis-cli PING` 得到 `PONG`**，并知道下一章如何「钉死版本」。

## 项目背景

**贯穿设定**：后文各章用**某电商平台**作为**统一的假想业务**（营销、交易、履约、客服等常见能力，**不指涉任何真实公司**），便于把「命令」挂到「要解决的问题」上。

**与本章关系**：业务代码终究要连 Redis，团队在 Win11 上约定：**本机借 WSL2 跑 Linux 版、与文档一致的 Redis 8.6**，避免依赖过期的「Windows 版安装包」与社区主线脱节。本章**不讲具体数据类型**，只要求 **`redis-cli PING` 得 `PONG`**——先有可用实例，后续章节再在同一业务叙事下讲 String、Hash、Stream、Geo 等。

## 步步引导：今晚就要连上

**大师**：先不急动手。你今晚最想达成的一件事，用一句话怎么说？

**小白**：弟子想……在电脑上跑起新版 Redis，能 `PING` 通。

**大师**：好。那你心里**默认的安装路径**是什么？最省心的那一招，往往长什么样？

**小白**：下「Redis for Windows」之类的一键包，最快。

**大师**：这一步在「快」上常常成立。你再往深想一层：**今天社区里大家讨论、修 bug、发版的「主战场」**，更常发生在哪种环境？

**小白**：好像是 Linux……那 Windows 就无路可走了？

**大师**：路是有的，只是**不必假装 Windows 原生就是那条主路**。常见有三条借道：**WSL2 里跑 Linux**、**Docker**、或 **SSH 到远程 Linux**——都是「借灶」，你选最顺手的一口。

**小白**：弟子也可以开虚拟机装 Ubuntu？

**大师**：当然可以。你不妨自问：**一天里多少次要回到 Windows 写文档、开会、用浏览器**？若「切换整个世界」很频繁，WSL2 往往更轻；若你本来就长驻 Linux，虚拟机或专用机也很好。

**小白**：仓库在 `D:\`，进了 WSL 该怎么进目录？

**大师**：`/mnt/d/...` 这条路径，**足够你把第一遍编译跑通**。若日后觉得「慢」，再试把大仓库放到 WSL 自己的文件系统（例如 `~/code`）——**差异从何而来**，亲手比一次，比为师讲十遍记得牢。

**小白**：`make` 报缺库、缺头文件……

**大师**：这时最养人的，反而是**只读报错的第一行**：它多半已经点名缺了什么。对照仓库 [`README.md`](../../README.md) 逐项补上即可；泛搜「编译失败」十条帖，不如**和报错对视一行**。

## 小剧场：借灶炖汤

隔壁桌问：「你 Redis 啥版本？」你答：「安装向导写的 3.x。」大师默默递来一杯茶：**「那是咸菜白粥摊的价目表；你要试 8.6，得换一口 Linux 灶。」**

---

## 不装整机虚拟机，也能把 Redis 8.6 揣兜里

**大师**：很多新手本机是 **Win10/11**，又想跑**最新开源 Redis**、甚至改 **C 源码**——最省事的路往往是 **WSL2**：相当于在 Windows 里嵌一台轻量 Linux，能直接 `make`、跑 `redis-server`，还能和 VS Code、Docker Desktop、浏览器一起用。

**小白**：弟子装过 WSL，一开机内存就少一截，风扇还转……

**大师**：所以叫**实战**：**开发机**开 WSL2 练手；**生产**用 Docker 或裸机 Linux。前传只解决「**我今晚就要敲 `redis-cli`，还要能对上源码行号**」。

**小白**：那和「直接装 Docker」比呢？

**大师**：想**钉死版本、一键起停**，看下一篇 [02-潮式-Docker与Compose.md](02-潮式-Docker与Compose.md)。想**改源码、`make`、跑单测、用 `gdb`**，WSL2 更顺手。两条路不互斥——很多人 **WSL2 里再跑 Docker**。

---

## 路线 A：WSL2 + 源码（推荐与本仓库联动）

### 第一回合：发行版与内核

1. 启用 **WSL2**（以 [微软文档](https://learn.microsoft.com/zh-cn/windows/wsl/install) 为准），优先 **Ubuntu 22.04 / 24.04 LTS**。  
2. `wsl --update`，避免旧内核导致 IO 或网络怪问题。  
3. 若代码在 **Windows 盘**（如 `D:\software\workspace\redis`），在 WSL 里路径形如 `/mnt/d/software/workspace/redis`——**大仓库编译尽量放在 WSL 原生 ext4 目录**（`~/code/redis`）会更快，这是无数少侠用血泪换来的经验。

### 第二回合：依赖与编译

进发行版后安装构建依赖，完整步骤见仓库根目录 [README.md](../../README.md) 的 *Build Redis from source*。你当前工作区已是源码树时，骨架命令如下（路径请按实际挂载调整）：

```bash
cd /mnt/d/software/workspace/redis   # 或 ~/code/redis
sudo apt update && sudo apt install -y build-essential pkg-config libssl-dev
make -j"$(nproc)"
./src/redis-server --daemonize yes
./src/redis-cli PING
```

**小白**：`PONG` 之后呢？

**大师**：看一眼版本与模块，和后文地图对齐：

```bash
./src/redis-cli INFO server | head
./src/redis-cli MODULE LIST
```

### 第三回合：日常「行功」习惯

| 习惯 | 用处 |
|------|------|
| 改配置用 `redis.conf` 副本，启动时显式指定 | 避免默认配置上生产踩雷 |
| 本机只 `bind 127.0.0.1` | 防局域网误扫 |
| 需要概率模块时记住 `BUILD_WITH_MODULES=yes` | 见 [04-编译秘笈-BUILD与模块对照表.md](04-编译秘笈-BUILD与模块对照表.md) |

**小白**：Windows 侧还要装 Redis for Windows 吗？

**大师**：**官方主线不维护旧版 Windows 原生服务端**。练真功，请 **WSL2 / Docker / 远程 Linux** 三选一，别在过期二进制上浪费时间——那是「练假把式」，面试一问版本就露馅。

---

## 路线 B：仅客户端在 Windows

应用跑在 Windows，Redis 在 WSL 或远端：

- 用 **WSL 里的 `redis-cli`** 连 `127.0.0.1`（端口映射一致即可）。  
- 或装 **Redis Insight**、各语言客户端，连接串填 **WSL IP** 或 `localhost`（视网络模式而定）。  
- **防火墙**：Win11 对 WSL2 的回环策略若折腾人，优先在 WSL 内测通，再让 Windows 应用连 **WSL 地址**——具体以你当前 Windows 版本文档为准。

**踩坑备忘**：「能 ping 不能连 6379」多半是 **bind / protected-mode / 云安全组**，别先怀疑人生。

---

## 深度一瞥：为何总提「本机源码树」

**大师**：你 `clone` 下来的不仅是能跑的程序，还是**活教材**：命令从 `src/commands/*.json` 起笔，实现在 `t_*.c`。前传不展开，记住三条**寻宝线**：

1. 想查某命令参数：**`COMMAND INFO <cmd>`** 与 `src/commands/<cmd>.json`。  
2. 想跟执行路径：**`src/server.c`**、各 `t_*.c`。  
3. 想理解 8.x 新叙事：**第03章地图** + **第22章编码**。

---

## 动手试一试（开发机即可）

1. WSL 终端里进入源码目录，执行上一节的 `make` 与 `redis-server` / `redis-cli PING`。  
2. 再跑：`redis-cli INFO server | head -n 3`，确认 **版本号**与你预期一致。  
3. 故意输错端口或停掉 `redis-server`，看客户端报错——**先认识错误长啥样**，以后排障不慌。

## 实战锦囊

- **本机练习**也建议显式 `bind 127.0.0.1`，别把未设密码的实例露到局域网。  
- 编译与 Docker **两套环境**时，用 `INFO server` 记清「我到底连的哪一颗」。  
- 下一章 [02](02-潮式-Docker与Compose.md) 专门解决「**团队同版本**」问题。

---

## 本章小结（自查）

- [ ] WSL2 发行版可 `make` 通过  
- [ ] `redis-cli PING` 返回 `PONG`  
- [ ] 能说出「为何不在 Win 原生跑服务端」  
- [ ] 知道下一章用 Docker 钉死版本

---

## 收式

**小白**：弟子今晚 PING 通了，算不算入门？

**大师**：算**入门第一步走稳了**。下一章：[02-潮式-Docker与Compose.md](02-潮式-Docker与Compose.md)。
