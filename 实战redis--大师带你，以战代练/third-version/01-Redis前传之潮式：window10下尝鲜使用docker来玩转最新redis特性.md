Docker 容器技术在计算机技术日新月异的今天发展得如火如荼，Docker 可以快速搭建需要的环境，我们可否用它来尝鲜呢？

> **话外音**：若说 WSL 是「在 Windows 里开一间 Linux 厢房」，Docker 便是「拎箱入住的精装公寓」——镜像一拉，Redis 拎包入住。缺点也有：小鲸鱼偶尔闹脾气（守护进程未起、Hyper-V 抢地盘），届时莫急，重启大法与官方文档同在。

现有环境工作：

windows10 专业版

1.准备工作

> > docker-for-windows 下载

> > 启用 Hyper-V

打开控制面板 - 程序和功能 - 启用或关闭 Windows 功能，勾选 Hyper-V，然后点击确定即可，如图：

![](http://p3.toutiaoimg.com/large/pgc-image/0123f17fa7db454896469bbee90a1db5)

点击确定后，启用完毕会提示重启系统，我们可以稍后再重启

2.安装 docker-for-windows

默认安装即可，单击 Close and log out，这个时候我们重启一次电脑。

3.启动 docker

找到 Docker for Windows 快捷方式，双击启动即可！启动成功后托盘处会有一个小鲸鱼的图标。打开命令行输入命令：docker version 可以查看当前 docker 版本号，如图：

![](http://p26.toutiaoimg.com/large/pgc-image/82032ff1d9744a40a4ba07ec0c5466d3)

也可以修改配置

点击托盘处 docker 图标右键选择-Settings，然后修改如下：

![](http://p9.toutiaoimg.com/large/pgc-image/a362b9542c3b425b952e722c7e27d79e)

4.安装 Redis

4.1 查看 Redis 镜像

![](http://p9.toutiaoimg.com/large/pgc-image/2dbd7793d84647c0a2c926fcfb327dd2)

4.2 安装最新版本的 Redis

![](http://p3.toutiaoimg.com/large/pgc-image/2a0b44b9895e4c6bbcae192f89c4c73b)

4.3 查看 Redis 版本或者进入 redis-cli

![](http://p26.toutiaoimg.com/large/pgc-image/e0ae9117d81147c2a40baadf64f9b376)

5.stream 特性尝鲜

![](http://p9.toutiaoimg.com/large/pgc-image/b7578b9d021e4907bd2f680302b2417a)

6.总结

本文介绍了我在 Windows 10 系统下使用 Docker 来构建 Redis 环境进行开发和测试的一些经验和感受。Docker 是一个非常好的东西，对开发，测试，运维都是个好工具，可以帮助我们提高工作效率，值得玩一玩。

> **收尾梗**：镜像会过期，文档会过时，唯有「先 `docker pull` 再抱怨」的耐心常新。

**与源码学习衔接**：容器里跑的是**发行版二进制**，适合验命令与特性；若要对照本文系列里的 `t_hash.c`、`t_zset.c` 等，请在 WSL / Linux 下 **本目录 `make` 编译**，用同一版本 `redis-cli` 连**自建 `redis-server`**，这样 `OBJECT ENCODING`、`DEBUG OBJECT`（慎用）与源码分支才一一对应。
