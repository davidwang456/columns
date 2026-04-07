**尝鲜 Redis 最新特性**

> **话外音（可跳过）**：江湖传言，Windows 上装 Redis 像「在茶馆里点佛跳墙」——不是不能做，是厨子（官方安装包）版本太老，端上来还是三年陈酿。下文教你借 WSL 这口「外地灶」，照样把新菜炒熟。

> **对照本仓库**：下文仍以 5.0.5 安装示例为主；你当前 clone 的源码树即为最新开发线，编译安装步骤相同（`make`、`src/redis-server`）。读完 `README.md` 与 `redis.conf` 里 6.x/7.x 新增模块与配置项，再回来看前传，会更容易把“版本差”对上号。**

很多人问我自己的操作系统是 Windows 10，可是 Windows 下可安装的 Redis 版本最高为 3.2，想要验证 Redis 的诸如 Stream 特性的话，就无能为力了。如何尝试 Redis 的最新特性？

**解决方式**

解决方法之一

在 Windows 上安装虚拟机，再在虚拟机上安装 Linux 操作系统，如 CentOS、Ubuntu 等，再在其上安装 Redis 最新版本。这个有点麻烦，如果不想如此麻烦，该怎么做呢？

解决方法二：

WSL 是由 Windows 内核团队与 Canonical 合作设计和开发的，可以让 Windows 10 下的开发者们在拥有 Windows 中那些强力支持之外，

还能使用 Linux 下丰富的开发环境与工具，而不用启动到另外的操作系统或者使用虚拟机。这绝对是一个“来自开发者，服务开发者”的 Windows 10 特色，它的目的是让开发者们每天的开发工作都变得顺畅而便捷。

本文以 centos 为例，进行演示

安装前准备工作

![](http://p3.toutiaoimg.com/large/pgc-image/cd47f5559bfc40dda664aff06645fe63)

1. window10 下面安装 centos

![](http://p26.toutiaoimg.com/large/pgc-image/584a856d11a84b27af76e479b85954f2)

安装步骤就按照提示进行即可

2.centos 安装 Redis 最新版本 5.0.5

2.1 进入 centos，安装 wget

```
rpm -qa|grep "wget"
```

2.2 安装

```
yum -y install wget
```

2.3 下载 Redis 最新包

```
 wget http://download.redis.io/releases/redis-5.0.5.tar.gz
```

2.4 解压

```
tar xzf redis-5.0.5.tar.gz
```

我是放到/usr/local 目录下的

2.5 安装依赖

```
 yum groupinstall 'Development Tools'
 yum install gcc
 yum install gcc-c++
```

2.6 编译

进入 redis-5.0.5 目录

```
make
```

报错：

```
fatal error: jemalloc/jemalloc.h: No such file or directory
```

处理报错

```
cd deps; make hiredis lua jemalloc linenoise
```

进入 redis-5.0.5 目录，重新编译，安装

```
make
make install
```

安装成功

```
Hint: It's a good idea to run 'make test' 
 INSTALL install
 INSTALL install
 INSTALL install
 INSTALL install
 INSTALL install
```

2.7 修改 redis.conf 配置文件

```
protected-mode no # 关闭保护模式
daemonize yes # 守护进程模式开启
port 6381
```

2.8 启动 Redis

此时/usr/local/bin 下面有 redis-server，redis-cli，启动 redis-server

```
/usr/local/bin/redis-server redis.conf
```

2.9 验证 Redis

进入/usr/local/bin 目录

```
redis-cli -h 127.0.0.1 -p 6381
```

执行 info

![](http://p3.toutiaoimg.com/large/pgc-image/0450cbb5b91c4a479b918868177fd8d7)

安装成功

3.从 window10 上进行测试

![](http://p6.toutiaoimg.com/large/pgc-image/96cd9f6c43ae4eb781a59f067ab92a73)

设置 key，在 centos 客户端可以获取到。

---

> **收尾梗**：若按文操作仍连不上，先默念三遍「防火墙、端口、bind、protected-mode」，再查 `redis.conf`——比转发锦鲤灵验。
