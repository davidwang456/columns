本故事纯属虚构，如有雷同，纯属巧合。

> 《笑傲江湖》里，桃谷六仙抬着担架飞奔，讲究一个「快」字。在 Windows 上起 Redis，**Docker** 便是现代版担架——**镜像一拉，容器一起**，比从零编译少费许多口舌。

**故事背景**

容器技术日新月异，**Docker** 能快速复制环境：开发、测试、CI 一条线。少侠本机 **Windows 10 专业版**，启用 **Hyper-V** 与 **Docker Desktop** 后，可**不装完整虚拟机**就跑到较新的 **redis-server**，顺手试 **Stream** 等特性。

**Redis 前传：潮式·借舟式——Docker 上漂一只 Redis**

**大师**：上一篇教你 **WSL 借灶**；这一篇教你 **借舟**——Docker 镜像即舟，**pull** 即借，**run** 即下水。

**小白**：WSL 与 Docker 二选一吗？

**大师**：不必。有人 **WSL2 + Docker Desktop 集成**，有人只用其一。**能稳定复现环境**即是好招。

---

### 第一回合：准备 Hyper-V 与 Docker Desktop

**现有环境**：Windows 10 专业版（或支持 Docker Desktop 的版本）。

**1. 准备工作**

- Docker for Windows 下载（现多为 **Docker Desktop**）
- 启用 **Hyper-V**（或与 WSL2 后端的组合，以官方安装向导为准）

打开「控制面板 → 程序和功能 → 启用或关闭 Windows 功能」，勾选 **Hyper-V**：

![](http://p3.toutiaoimg.com/large/pgc-image/0123f17fa7db454896469bbee90a1db5)

**小白**：提示重启，好烦。

**大师**：重启一次，省下半月「我电脑为什么又连不上 Docker」的玄学时间。

---

### 第二回合：安装并启动 Docker

**2.** 安装 **Docker Desktop**，按向导完成；提示 **Close and log out** 时**重启**。

**3.** 启动 Docker，托盘出现**小鲸鱼**。命令行执行：

```
docker version
```

![](http://p26.toutiaoimg.com/large/pgc-image/82032ff1d9744a40a4ba07ec0c5466d3)

**大师**：`Server` 与 `Client` 都有版本输出，才算守护进程起来。

可在 **Settings** 里调整资源（CPU/内存/磁盘路径）：

![](http://p9.toutiaoimg.com/large/pgc-image/a362b9542c3b425b952e722c7e27d79e)

---

### 第三回合：拉镜像、起容器、进 redis-cli

**4. 安装 Redis**

**4.1** 查看镜像（示例用官方或可信镜像）：

![](http://p9.toutiaoimg.com/large/pgc-image/2dbd7793d84647c0a2c926fcfb327dd2)

**4.2** 拉取**带版本标签**的镜像（**勿盲信 `latest` 上生产**）：

![](http://p3.toutiaoimg.com/large/pgc-image/2a0b44b9895e4c6bbcae192f89c4c73b)

**4.3** 查看 Redis 版本或进入 `redis-cli`：

![](http://p26.toutiaoimg.com/large/pgc-image/e0ae9117d81147c2a40baadf64f9b376)

**大师**：`docker run` 时记得 **映射端口**、必要时挂 **数据卷**、生产加 **密码与网络策略**。下面步骤以原文截图流程为准。

---

### 第四回合：Stream 尝鲜

**5. stream 特性尝鲜**

![](http://p9.toutiaoimg.com/large/pgc-image/b7578b9d021e4907bd2f680302b2417a)

**小白**：`XADD` 成功是不是就算潮过了？

**大师**：算**尝过鲜**。要**潮得稳**，继续学 **消费者组、`XPENDING`、`XAUTOCLAIM`**（见《破索式》）。

---

**番外：容器二进制 vs 本机编译**

**大师**：容器里跑的是**发行版构建好的 redis-server**，适合**验命令、写 demo**；要与本仓库 **C 源码** 行级对照，仍建议在 **WSL 或 Linux 下 `make`**，用**同一分支**的 `redis-cli` 连**自建进程**。

**小白**：小鲸鱼偶尔罢工咋办？

**大师**：**重启 Docker、清掉僵尸容器、看 WSL2 是否更新、磁盘是否满**——比在朋友圈求签管用。

---

**收式 · 总结**

本文记录作者在 Windows 10 下用 **Docker** 搭建 Redis 开发与测试环境的经历。**Docker** 对开发、测试、运维都是利器，**值得玩一玩**——但**玩和生产**中间还隔着一整本 **redis.conf**。

> **收尾梗**：镜像会过期，文档会过时，唯有「先 `docker pull` 再抱怨」的耐心常新。

**小白**：弟子这式叫「借舟」，下一式是不是该「自己造船」（源码编译）？

**大师**：正是上一篇《借灶式》与附篇《内视式》的路子——**三样都摸过，才算会在 Windows 江湖立足**。

**小白**：恭送大师！

**与源码学习衔接**：容器里跑的是**发行版二进制**，适合验命令与特性；若要对照本文系列里的 `t_hash.c`、`t_zset.c` 等，请在 WSL / Linux 下 **本目录 `make` 编译**，用同一版本 `redis-cli` 连**自建 `redis-server`**，这样 `OBJECT ENCODING`、`DEBUG OBJECT`（慎用）与源码分支才一一对应。
