本故事纯属虚构，如有雷同，纯属巧合。

> 《天龙八部》里，虚竹破了珍珑棋局，得了无崖子七十年功力——在 Windows 上想玩**新版 Redis**，有时也要先**借一局别人的棋盘**：WSL 里跑 Linux，等于在微软地盘上圈了一块「逍遥派后山」。

**故事背景**

许多少侠本机是 **Windows 10/11**，官方 MSI 还停留在 **3.2** 岁月，想试 **Stream、新模块、新配置**，犹如在茶馆点佛跳墙——厨子说：「本店招牌是咸菜白粥。」装整机虚拟机又嫌笨重，于是有了**第二条路**：**WSL（Windows Subsystem for Linux）**，不重启进双系统，却能摸到 **gcc、make、与 redis-server** 真身。



**Redis 前传：借灶式——在 WSL 里炖一盅新 Redis**

> **对照本仓库**：下文示例仍以 **5.0.5**  tarball 为主线；你当前 clone 的源码树即为最新开发线，**编译步骤仍是 `make` / `src/redis-server`**。读完本仓库 `README.md` 与 `redis.conf` 中 6.x/7.x 以来新增项，再回读前传，更易把「版本差」对上号。

**大师**：你为何愁眉不展？

**小白**：弟子用 Windows，装 Redis 只能到 3.2，想练 **Stream**，师父说「去 Linux」——可弟子不想开虚拟机。

**大师**：那便**借灶**：装 **WSL**，在子系统里装 **CentOS 或 Ubuntu 镜像**（下文以 **CentOS** 演示），与开虚拟机相比，轻省许多。另一条路是 **Docker**（见下一篇《潮式》）。

**小白**：WSL 究竟是啥？

**大师**：让 Windows 10 直接跑 Linux 用户态与工具链，**不必整盘切换操作系统**。微软与 Canonical 等合作，目标一句话：**开发者在 Windows 上也能用 Linux 的开发体验**。

---

### 第一回合：安装 WSL 与 CentOS

**小白**：第一步做什么？

**大师**：按微软文档启用 **WSL**，在 Microsoft Store 或指定渠道安装 **CentOS/Ubuntu** 发行版。图示如下，**具体菜单以你系统版本为准**。

1. Windows 10/11 下安装 CentOS（或 Ubuntu）



**小白**：装完就能 `yum` 了吗？

**大师**：进入子系统终端，先当自己是**一台 Linux 盒子**，该 `yum` 的 `yum`，该 `apt` 的 `apt`。

---

### 第二回合：下载、编译 Redis 5.0.5

**大师**：示例用 **5.0.5** 源码包；你若要 **7.x**，把 URL 与目录名换成对应版本即可，**步骤骨架不变**。

**2.1** 进子系统，检查/安装 `wget`：

```
rpm -qa|grep "wget"
```

**2.2** 若无则安装：

```
yum -y install wget
```

**2.3** 下载（官方 releases，可按需换版本）：

```
wget http://download.redis.io/releases/redis-5.0.5.tar.gz
```

**2.4** 解压：

```
tar xzf redis-5.0.5.tar.gz
```

**小白**：弟子习惯放到 `/usr/local` 下。

**大师**：路径随你，**记清即可**，后面 `make` 都在源码目录里做。

**2.5** 编译依赖：

```
yum groupinstall 'Development Tools'
yum install gcc
yum install gcc-c++
```

**2.6** 编译与安装

进入 `redis-5.0.5` 目录：

```
make
```

若报错：

```
fatal error: jemalloc/jemalloc.h: No such file or directory
```

**大师**：别慌，先编依赖子目录，再回头：

```
cd deps; make hiredis lua jemalloc linenoise
```

回到 `redis-5.0.5` 根目录：

```
make
make install
```

成功时可见类似：

```
Hint: It's a good idea to run 'make test' 
 INSTALL install
 ...
```

**小白**：`make test` 要跑吗？

**大师**：**时间充裕建议跑**，能提前暴露平台差异；赶课可先跳过，**上线前务必在目标环境验证**。

---

### 第三回合：配置、启动与验证

**2.7** 修改 `redis.conf`（示例）：

```
protected-mode no   # 本机学习可关；生产请配合 bind、密码、防火墙
daemonize yes
port 6381
```

**大师**：**生产切忌照抄 `protected-mode no`**。学习场景也要**限制 bind 在 127.0.0.1** 或内网，勿对公网裸奔。

**2.8** 启动：

此时 `/usr/local/bin` 下应有 `redis-server`、`redis-cli`：

```
/usr/local/bin/redis-server /path/to/redis.conf
```

**2.9** 验证：

```
redis-cli -h 127.0.0.1 -p 6381
```

执行 `INFO`：



**小白**：装成了！

---

### 第四回合：从 Windows 侧连过去

**大师**：在 Windows 里用 **Redis Desktop Manager、自己写的客户端、或另一终端**，填 **WSL 的 IP 与端口**（若跨网络栈，注意 **Windows 与 WSL2 的地址转发**——**WSL1 与 WSL2 行为不同**，以微软文档为准）。

1. 从 Windows 上测试



**小白**：`set` 一把，子系统里 `get` 得到，算通关吗？

**大师**：算**借灶成功**。下一步该想：**持久化、密码、systemd 托管**——别只会前台 `redis-server`。

---

**番外：WSL1 / WSL2 与「对照源码」怎么配合？**

**小白**：弟子日后要对照 `t_stream.c`，该用 tarball 还是 clone 仓库？

**大师**：**clone 本仓库 + `make`**，与课堂九式、附篇《内视式》同一套代码路径；WSL 里编译的 `redis-server` 与 **Docker 镜像里的二进制**版本可能不同，`**OBJECT ENCODING`、模块列表以你实际进程为准**。

**小白**：常见踩坑？

**大师**：**防火墙、端口占用、`bind 127.0.0.1` 与 Windows 访问方向、protected-mode**——连不上时先念这四句，再查 `redis.conf`。

---

**收式**

**大师**：借灶只为**尝鲜与开发**；真要扛流量，还有 **Linux 真机、K8s、云托管** 等你。

**小白**：弟子先去 `XADD` 一把 Stream，再睡！

**大师**：且慢——先确认 **数据目录与 AOF/RDB** 别落在会丢的子系统路径上（WSL 文件系统与 `/mnt/c` 性能、持久性不同，**查微软说明**）。

**小白**：……弟子记下了。恭送大师！

> **收尾梗**：若按文操作仍连不上，先默念三遍「防火墙、端口、bind、protected-mode」，再查 `redis.conf`——比转发锦鲤灵验。

