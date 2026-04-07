**尝鲜redis最新特性**

很多人问我自己的操作系统是window10，可是windows下redis的可以安装的版本最高为3.2，想要验证redis的诸如stream特性的话，就无能为力了。如何尝试redis的最新特性？

**解决方式**

解决方法之一

在windows上安装虚拟机，然后再虚拟机上安装linux操作系统，如centos，ubuntu等，然后再其上面安装redis最新版本。这个有点麻烦，如果不想如此麻烦，该怎么做呢？

解决方法二：

WSL 是由 Windows 内核团队与 Canonical 合作设计和开发的，可以让 Windows 10 下的开发者们在拥有 Windows 中那些强力支持之外，

还能使用 Linux 下丰富的开发环境与工具，而不用启动到另外的操作系统或者使用虚拟机。这绝对是一个“来自开发者，服务开发者”的 Windows 10 特色，它的目的是让开发者们每天的开发工作都变得顺畅而便捷。

本文以centos为例，进行演示

安装前准备工作

![](http://p3.toutiaoimg.com/large/pgc-image/cd47f5559bfc40dda664aff06645fe63)

1. window10 下面安装centos

![](http://p26.toutiaoimg.com/large/pgc-image/584a856d11a84b27af76e479b85954f2)

安装步骤就按照提示进行即可

2.centos 安装redis最新版本5.0.5

2.1 进入centos，安装wget

```
rpm -qa|grep "wget"
```

2.2 安装

```
yum -y install wget
```

2.3 下载redis最新包

```
 wget http://download.redis.io/releases/redis-5.0.5.tar.gz
```

2.4 解压

```
tar xzf redis-5.0.5.tar.gz
```

我是放到/usr/local目录下的

2.5 安装依赖

```
 yum groupinstall 'Development Tools'
 yum install gcc
 yum install gcc-c++
```

2.6 编译

进入redis-5.0.5 目录

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

进入redis-5.0.5 目录，重新编译，安装

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

2.7 修改redis.conf配置文件

```
protected-mode no # 关闭保护模式
daemonize yes # 守护进程模式开启
port 6381
```

2.8 启动redis

此时/usr/local/bin下面有redis-server，redis-cli，启动redis-server

```
/usr/local/bin/redis-server redis.conf
```

2.9 验证redis

进入/usr/local/bin目录

```
redis-cli -h 127.0.0.1 -p 6381
```

执行info

![](http://p3.toutiaoimg.com/large/pgc-image/0450cbb5b91c4a479b918868177fd8d7)

安装成功

3.从window10上进行测试

![](http://p6.toutiaoimg.com/large/pgc-image/96cd9f6c43ae4eb781a59f067ab92a73)

设置key，在centos客户端可以获取到。
