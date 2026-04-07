本故事纯属虚构，如有雷同，纯属巧合。

> 少林寺藏经阁卷帙浩繁，若无目录，再高的武功也要翻到手软。Redis 命令二百余式（随版本增减），**官网检索偶失灵、浏览器各怀鬼胎**——自建一张「命令索引表」，便是程序员的藏经目录。

**故事背景**

作者在 Chrome 打开 Redis 官网命令页，筛选框罢工，顿觉**查一招半式都要靠运气**。于是动念：把命令**导入 MySQL**，按版本、分组、关键字检索——从此告别「Ctrl+F 翻到眼瞎」。

![](http://p3.toutiaoimg.com/large/pgc-image/67ed1b8cb6884162ad6d90fd71669927)

**Redis 别传：索引式——把命令谱系请进 MySQL**

**大师**：你遇着什么问题？

**小白**：官网命令筛选不好使，又想知道**某命令从哪版开始有**，翻文档太慢。

**大师**：两条路：**运行时问 Redis**，或**读源码元数据**。你选哪条？

**小白**：弟子想两条都要——平时用库表查，线上用 `COMMAND INFO` 验。

**大师**：有出息。先搭表。

---

### 一、表结构（示例）

**大师**：字段不必拘泥于此，可按需加 `complexity`、`flags`、`deprecated_since` 等；核心是 **`name` + `since` + 分组**。

**小白**：建表 SQL 如下。

```sql
DROP TABLE IF EXISTS COMMANDS;

CREATE TABLE COMMANDS(
  `id` INT(11) NOT NULL AUTO_INCREMENT,
  `name` VARCHAR(30) DEFAULT NULL,
  `params` VARCHAR(300) DEFAULT NULL,
  `summary` VARCHAR(1024) DEFAULT NULL,
  `group` INT(5) DEFAULT 0,
  `since` VARCHAR(30) DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=INNODB AUTO_INCREMENT=1 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
```

**大师**：`summary` 若嫌短，可改 `TEXT`；命令名极个别超长时放宽 `VARCHAR(30)`。

---

### 二、数据从哪来？

**小白**：弟子当初手抄官网，生成一堆 `INSERT`……

```sql
INSERT INTO COMMANDS(`name`,params,summary,`group`,since) VALUES ("ACL CAT",
"[category`name`]",
"List the ACL categories or the commands inside a category",
9,
"6.0.0" );
-- ........ 余下略
```

**大师**：手抄可练耐心，**不可练版本**。你本地仓库里，真源在何处？

**小白**：（翻源码）`src/commands.def`，由 **`utils/generate-command-code.py`** 生成 `src/commands.c`、`commands.h`，`commands.c` 顶部 `#include "commands.def"`。

**大师**：善。**解析 `commands.def` 或生成后的表**，比从网页爬虫体面，且与分支版本一致。另可辅助 **`utils/generate-commands-json.py`** 导出 JSON 再入库，减少手写 SQL。

**大师**：线上实例还可直接：

```
COMMAND COUNT
COMMAND INFO GET SET XADD ...
```

**小白**：这样模块加载与否也会影响命令列表！

**大师**：正是——**索引表是「某版本静态快照」，运行时是「真相 + 模块」**。面试若问「命令数多少」，先反问「哪一版、是否加载模块」。

![](http://p3.toutiaoimg.com/large/pgc-image/b4edf22cdf1d451aace76cd49def805c)

![](http://p6.toutiaoimg.com/large/pgc-image/94470e2d237e4068b58141e16999fcf9)

---

### 三、导入后怎么玩？

**小白**：导入成功，截图留念。

![](http://p26.toutiaoimg.com/large/pgc-image/49d52df7d05345cca7b50563c18f5819)

**大师**：先按版本聚合，看「哪一代开始招式暴涨」。

```sql
SELECT since, COUNT(1) FROM COMMANDS GROUP BY since ORDER BY since DESC;
```

**小白**：早期 1.0.0 只有几十条，后面版本节节高……

![](http://p9.toutiaoimg.com/large/pgc-image/aa5b2a4b348a4a3a91b59c7a0abb092a)

**大师**：记住：**文中「259 条」是作者当时版本**；你 fork 的 Redis 8.x 若已增多，不是导入错了，是**Redis 又长个儿了**。

---

**番外：索引有了，武功还缺什么？**

**大师**：表在库里，**热点命令**在不在线上？

**小白**：弟子用 `INFO commandstats`、`SLOWLOG GET`、`LATENCY DOCTOR` 对照 `commands.def` 里的复杂度注释。

**大师**：再加一条：**别用库表替代阅读官方语义**——`SUMMARY` 一行话可能省略前置条件与边界；重大变更看 **release notes**。

---

**收式小结**

- **静态索引**（MySQL）+ **动态真相**（`COMMAND`）双持，才不易偏科。
- **命令元数据真源**：本仓库 **`commands.def` → commands.c** 链路。
- **版本与模块**会让「命令条数」成为伪命题，答题先界定范围。

**小白**：大师，这式叫「索引」，是不是最不打眼却最省力？

**大师**：藏经阁扫地僧也是这般——**看似琐碎，实则省下半生翻检功夫**。

**小白**：恭送大师！

> **彩蛋**：若表里条数与当前 minor 版本对不上，重跑生成脚本再导一遍，比在朋友圈求锦鲤管用。
