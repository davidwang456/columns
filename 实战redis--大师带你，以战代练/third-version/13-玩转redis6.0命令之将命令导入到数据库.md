# 背景

> **话外音**：官网命令检索偶尔「失灵」，未必是你鼠标坏了，也可能是前端脚本与你的浏览器八字不合。把命令扒进 MySQL，等于自建一本「Redis 招式索引」——以后查 `SINCE` 版本，比在论坛里喊「大佬救命」体面得多。

在 Redis 的官网上，有命令行的筛选和查询，但我在 Chrome 中使用时，不起作用。

![](http://p3.toutiaoimg.com/large/pgc-image/67ed1b8cb6884162ad6d90fd71669927)

这样的话，我使用命令就非常不方便了。另外，我如果想要知道哪些命令支持那个 Redis 版本，又该怎么办呢？

如何能方便地查询这些命令呢？我做了一些小小的尝试：将所有的 Redis 命令导入到数据库中。

# Redis 命令导入到 MySQL 中

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

准备 sql 脚本：(接下来的段落会介绍 sql 脚本的由来)

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

从结果可以看到，Redis 的最新版本为 6.0.6，总共 259 条命令，查询这些命令的分布：

> SELECT since,COUNT(1) FROM COMMANDS GROUP BY since ORDER BY since DESC;

可以看到每个版本新增的命令数目。最初的 1.0.0 版本只有 63 条命令：

![](http://p9.toutiaoimg.com/large/pgc-image/aa5b2a4b348a4a3a91b59c7a0abb092a)

有人会问我，你是怎么找到命令集合的？

# Redis 6.0 的命令集由来揭秘

从 github 上下载最新源码，

![](http://p3.toutiaoimg.com/large/pgc-image/b4edf22cdf1d451aace76cd49def805c)

**（与本仓库对齐的说明）** 早期版本常见单独的 `help.h` 生成客户端帮助；**当前树里命令元数据由 `src/commands.def` 描述，经 `utils/generate-command-code.py` 等脚本生成 `src/commands.c` / `commands.h`**，`commands.c` 顶部可见 `#include "commands.def"`。若要导出“命令名、参数、复杂度、since 版本、分组”等到 MySQL，**直接解析 `commands.def` 或生成后的 C 表**往往比手抄官网更稳。

命令行侧也可用内置帮助：`COMMAND`、`COMMAND COUNT`、`COMMAND INFO <name>` 在运行时列出服务端实际注册的命令（与模块加载情况一致）。

![](http://p6.toutiaoimg.com/large/pgc-image/94470e2d237e4068b58141e16999fcf9)

# 总结

Redis 的源码简洁强悍，仅有 52 个*.h 文件，86 个*.c 文件，学习 Redis 时，稍稍扫一下源码，有助于我们加深对 Redis 的理解。

**补一句实战**：把命令集入库适合自己做检索与版本对比；真正调优时还要结合 **`LATENCY DOCTOR`、`SLOWLOG`、`INFO commandstats`** 看线上热点命令，与 `commands.def` 里的复杂度注释交叉验证。

> **彩蛋**：若哪天你发现表里 259 条命令和当前版本对不上，别慌——不是你导入错了，是 Redis 又长个儿了，再跑一遍脚本便是。
