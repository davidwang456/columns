# 背景

在redis的官网上，有命令行的筛选和查询，但我在chrome中使用时，不起作用。

![](http://p3.toutiaoimg.com/large/pgc-image/67ed1b8cb6884162ad6d90fd71669927)

这样的话，我使用命令就非常不方便了。另外，我如果想要知道哪些命令支持那个redis版本，又该怎么办呢？

如何能方便的查询这些命令呢？我做了一些小小的尝试：将所有的redis命令导入到数据库中。

# redis命令导入到mysql中

**准备工作：数据库表结构**

> DROP TABLE IF EXISTS COMMANDS;
> 
> CREATE TABLE COMMANDS(
> 
> `id` INT(11) NOT NULL AUTO_INCREMENT,
> 
> `name` VARCHAR(30) DEFAULT NULL,
> 
> `params` VARCHAR(300) DEFAULT NULL,
> 
> `summary` VARCHAR(1024) DEFAULT NULL,
> 
> `group` INT(5) DEFAULT 0,
> 
> `since` VARCHAR(30) DEFAULT NULL,
> 
> PRIMARY KEY (`id`)
> 
> )ENGINE=INNODB AUTO_INCREMENT=1 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

准备sql脚本：(接下来的段落会介绍sql脚本的由来)

> INSERT INTO COMMANDS(`name`,params,summary,`group`,since) VALUES ("ACL CAT",
> 
> "[category`name`]",
> 
> "List the ACL categories or the commands inside a category",
> 
> 9,
> 
> "6.0.0" );
> 
> ..........
> 
> .........
> 
> ..........

结果验证

![](http://p26.toutiaoimg.com/large/pgc-image/49d52df7d05345cca7b50563c18f5819)

从结果可以看到，redis的最新版本为6.0.6，总共259条命令，查询这些命令的分布：

> SELECT since,COUNT(1) FROM COMMANDS GROUP BY since ORDER BY since DESC;

可以看到每个版本新增的命令数目。最初的1.0.0版本只有63条命令：

![](http://p9.toutiaoimg.com/large/pgc-image/aa5b2a4b348a4a3a91b59c7a0abb092a)

有人会问我，你是怎么找到命令集合的？

# redis 6.0的命令集由来揭秘

从github上下载最新源码，

![](http://p3.toutiaoimg.com/large/pgc-image/b4edf22cdf1d451aace76cd49def805c)

命令的分类，定义在src目录下的help.h

![](http://p6.toutiaoimg.com/large/pgc-image/94470e2d237e4068b58141e16999fcf9)

# 总结

redis的源码简洁强悍，仅有52个*.h文件，86个*.c文件，学习redis时，稍稍扫一下源码，有助于我们加深对redis的理解。
