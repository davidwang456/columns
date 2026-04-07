docker容器技术在计算机技术日新月异的今天， 发展的如火如荼,docker 可以快速搭建需要的环境，我们可否用它来尝鲜呢？

现有环境工作：

windows10 专业版

1.准备工作

> > docker-for-windows 下载

> > 启用Hyper-V

打开控制面板 - 程序和功能 - 启用或关闭Windows功能，勾选Hyper-V，然后点击确定即可，如图：

![](http://p3.toutiaoimg.com/large/pgc-image/0123f17fa7db454896469bbee90a1db5)

点击确定后，启用完毕会提示重启系统，我们可以稍后再重启

2.安装docker-for-windows

默认安装即可，单击Close and log out，这个时候我们重启一次电脑。

3.启动docker

找到Docker for Windows快捷方式，双击启动即可！启动成功后托盘处会有一个小鲸鱼的图标。打开命令行输入命令：docker version可以查看当前docker版本号，如图：

![](http://p26.toutiaoimg.com/large/pgc-image/82032ff1d9744a40a4ba07ec0c5466d3)

也可以修改配置

点击托盘处docker图标右键选择-Settings，然后修改如下：

![](http://p9.toutiaoimg.com/large/pgc-image/a362b9542c3b425b952e722c7e27d79e)

4.安装redis

4.1 查看redis镜像

![](http://p9.toutiaoimg.com/large/pgc-image/2dbd7793d84647c0a2c926fcfb327dd2)

4.2 安装最新版本的redis

![](http://p3.toutiaoimg.com/large/pgc-image/2a0b44b9895e4c6bbcae192f89c4c73b)

4.3 查看redis版本或者进入redis-cli

![](http://p26.toutiaoimg.com/large/pgc-image/e0ae9117d81147c2a40baadf64f9b376)

5.stream特性尝鲜

![](http://p9.toutiaoimg.com/large/pgc-image/b7578b9d021e4907bd2f680302b2417a)

6.总结

本文介绍了我在Windows 10系统下使用Docker来构建redis环境进行开发和测试的一些经验和感受。Docker是一个非常好的东西，对开发，测试，运维都是个好工具，可以帮助我们提高工作效率，值得玩一玩。
