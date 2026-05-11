# 第6章：SQL任务节点与数据源管理

## 1. 项目背景

某零售企业的数据仓库团队，每天凌晨都需要完成一套固定的ETL流水线：先将前一天的订单数据从MySQL业务库同步到数据仓库的ODS层（原始数据层），接着对ODS数据进行清洗去重后写入DWD层（明细数据层），再按商品维度做聚合汇总产出DWS层（汇总数据层）的销售报表，最后将Top 10热销商品的报表通过邮件发送给管理层。整条链路涉及二十余条SQL语句，按严格顺序执行，任何一个环节出错——比如源表字段变更导致INSERT类型不匹配、中间临时表忘记清理引发数据翻倍、聚合查询超时卡死——都会导致管理层上午9点打开邮箱时看不到任何数据。

目前这套流程完全由DBA人工承担：每天早上7点，DBA准时打开DBeaver，依次连接三四个数据库，逐条粘贴SQL并点击执行。运气好时30分钟收工，运气差时——例如MySQL连接超时、HiveServer2响应慢、某个LEFT JOIN写错了字段名——排查一个小时也是常事。某天DBA因急事请了假，结果全公司的晨间报表集体推迟了4小时，CTO在管理群内直接放话："这些SQL脚本必须自动化运行，没有例外。"

Apache DolphinScheduler内置的SQL任务节点，正是为解决这类"按序执行大量多数据源SQL"的场景设计的。它不仅覆盖MySQL、Hive、PostgreSQL、ClickHouse、Oracle等28+种数据源（通过SPI插件体系持续扩展），还提供了独特的三段式SQL结构（前置SQL→主要SQL→后置SQL）和查询结果自动邮件推送能力。本章将以零售数据仓库的每日ETL流程为主线，带你掌握DS中SQL任务节点从数据源注册到邮件输出的完整闭环。

## 2. 项目设计——剧本式交锋对话

会议室里，小胖、小白和大师三人面前摊着DBA交接过来的二十多行SQL脚本，白板上画着从ODS到DWD再到报表的数据流向。

**小胖**（快速扫了一眼SQL脚本，往后一靠）：“这不就是把SQL粘进去，点个运行就完事了吗？跟Navicat里建个自动化任务差不多！我打开DS的SQL节点，选个数据源，把SQL往里一塞，设置个每天早上7点定时跑，齐活！” 

**小白**（放下手中的保温杯）：“你太乐观了。我先问几个问题——第一，DBA的SQL里有三条是建临时表的，三条是清理临时表的，如果主SQL执行到一半失败了，后面的清理SQL还会执行吗？谁保证临时表不会被留下污染下一次运行？第二，这套SQL里有的跑在MySQL上，有的跑在Hive上，有的跑在ClickHouse上，DS怎么知道用哪个JDBC驱动去连接？连接密码存哪？安全吗？第三，如果SELECT查询跑出来的报表结果有几百行，你总不能让人每次都点进日志里去复制粘贴吧？”

**小胖**（笑容逐渐消失）：“呃……那要不把建表和清表分开成两个独立的节点？失败的话就人工上去手动删？密码嘛……实在不行写死在代码里呗？”

**大师**（笑着把笔记本电脑的屏幕转向二人）：“小胖的思路是把SQL节点当成一个简单的'远程执行器'——这确实是最直观的理解，但它忽略了DS为SQL场景做的三项核心设计。来看这张图。”

大师在白板上画下SQL节点的三段结构：

```
┌─────────────────────────────────────────────┐
│              SQL 任务节点                     │
├─────────────────────────────────────────────┤
│  [前置SQL]   DROP TABLE IF EXISTS tmp_xxx   │
│             CREATE TEMPORARY TABLE ...       │
├─────────────────────────────────────────────┤
│  [主要SQL]   INSERT INTO dwd_orders SELECT...│
│             或 SELECT ... FROM ... GROUP BY  │
├─────────────────────────────────────────────┤
│  [后置SQL]   UPDATE tmp_xxx SET flag='done'  │
│             DROP TABLE IF EXISTS tmp_xxx     │
└─────────────────────────────────────────────┘
```

“这是一个SQL节点，不是一个SQL语句。它被设计成三个时段——你可以把前置SQL想象成'搭舞台'，建临时表、设置会话参数；主要SQL是'唱戏'，执行核心计算逻辑；后置SQL是'拆舞台'，做清理和标签打标。关键是：**三段SQL在同一个数据库连接和同一个事务中顺序执行，如果主要SQL失败了，后置SQL不会执行，事务会回滚**——这就是对小白第一个问题的回答。”

“数据源密码也不存明文。”大师继续说道，“DS的数据源中心存储密码时使用AES加密，密钥由部署时的配置决定。Worker执行SQL时，通过DS自有的连接池——元数据用HikariCP，用户数据源用Druid——动态创建JDBC连接。密码只在内存中存在，不会以明文日志形式输出。”

**小胖**（眼睛一亮）：“那查询结果发邮件是怎么回事？SELECT出来的结果能自动飞到老板邮箱里？”

**大师**：“SQL节点有两种类型——'查询'和'非查询'。你把节点类型选为'查询'，执行的SQL必须是一个SELECT语句，然后勾选'发送邮件'，DS会自动把查询结果格式化为HTML表格或Excel附件，通过配置好的告警渠道发送到你指定的邮箱。INSERT/UPDATE/DELETE之类修改数据的SQL，选'非查询'类型即可。”

**小白**（翻着笔记本沉思片刻）：“我还有一个问题——Hive里的UDF函数怎么办？我们有个自定义的JSON解析UDF，SQL里需要调用它。”

**大师**：“DS的资源中心里有专门的UDF管理入口。你先把UDF的JAR包上传到资源中心，然后注册一个函数名，绑定到JAR包和主类。在SQL节点里就可以直接`CREATE TEMPORARY FUNCTION`引用它——Worker会自动先加载JAR再执行你的SQL。”

**小胖**（拍着桌子站起来）：“这下我明白了！SQL节点不是简单的SQL粘贴板——它是一个带数据源管控、三段式事务、结果邮件推送、UDF动态加载的完整工具！”

大师微笑着合上笔记本：“总结一下——'技术映射'：数据源中心就是你的数据库通讯录，SQL三段式就是事务的'搭台-唱戏-拆台'，查询邮件就是自动化的数据快递员。记住，DS中的SQL节点由Worker通过标准JDBC驱动执行，不是Master直接连数据库——这个分工保证了调度器和执行器的职责隔离。”

## 3. 项目实战

### 步骤1：在数据源中心注册MySQL数据源

进入DolphinScheduler Web控制台 → "数据源中心" → "创建数据源"，选择MySQL类型，填写连接信息：

| 字段 | 示例值 | 说明 |
|------|--------|------|
| 数据源名称 | mysql_ods_retail | 英文命名，见名知意 |
| IP/主机名 | 192.168.10.101 | MySQL服务器地址 |
| 端口 | 3306 | 默认端口 |
| 数据库名 | retail_ods | ODS层数据库 |
| 用户名 | ds_etl_user | 只给SELECT和INSERT权限 |
| 密码 | ******** | 加密存储 |
| 连接参数 | useSSL=false&serverTimezone=Asia/Shanghai | 常用参数 |

填写完毕后点击"测试连接"，确认返回"连接成功"。**注意**：测试连接在API服务器上发起，实际SQL执行在Worker上——两者网络环境可能不同，如果API能通但Worker不通，需要在Worker机器上排查网络/防火墙。如果DS所在机器缺少MySQL JDBC驱动（mysql-connector-java），测试连接会报"Driver class not found"，需要将该JAR包放入DS的lib目录下。

### 步骤2：注册Hive/ClickHouse数据源（可选）

如果你的ETL涉及Hive或ClickHouse，按同样流程注册第二个数据源。例如注册Hive数据源：

| 字段 | 示例值 |
|------|--------|
| 数据源名称 | hive_dw_retail |
| IP/主机名 | 192.168.10.201 |
| 端口 | 10000 |
| 数据库名 | retail_dw |
| 用户名 | hive |
| 连接参数 | hive.server2.proxy.user=ds_etl_user |

若没有Hive/ClickHouse环境，本章实战可简化——在同一个MySQL中创建两个不同的库分别模拟ODS层和DWD层。DS支持28+种数据源插件（通过META-INF/services下的SPI机制注册），覆盖了主流的关系型数据库和大数据查询引擎，生产环境中按需注册即可。

### 步骤3：创建SQL任务"ods_to_dwd"

在DS中新建工作流"零售日报ETL"，拖入一个SQL任务节点，命名为"ods_to_dwd"。在节点配置中选择数据源为"mysql_ods_retail"，SQL类型选择"非查询"。

编写三段SQL：

**前置SQL**——清理临时表与设置会话参数：
```sql
-- 清理前一次运行可能残留的临时表，防止数据翻倍
DROP TABLE IF EXISTS tmp_daily_orders;

-- 设置会话级别的超时时间（单位：秒），防止慢查询卡死整个工作流
SET SESSION max_execution_time = 600000;
SET SESSION sql_mode = 'STRICT_TRANS_TABLES';
```

**主要SQL**——从ODS层抽取数据生成DWD层临时表：
```sql
-- 从ODS订单原始表抽取前一天的订单数据，建临时表
CREATE TABLE tmp_daily_orders AS
SELECT 
    order_id,
    user_id,
    product_id,
    amount,
    order_time,
    DATE(order_time) AS order_date
FROM ods_orders
WHERE DATE(order_time) = '${biz_date}';
```

**后置SQL**——对新数据打标签并清理：
```sql
-- 将之前未出现过的订单标记为新订单
UPDATE tmp_daily_orders 
SET order_type = 'NEW' 
WHERE order_id NOT IN (
    SELECT order_id FROM dwd_orders
);

-- 注：后置SQL在主要SQL成功后才执行，
-- 如果CREATE TABLE失败，这条UPDATE不会跑，临时表也不会被创建
```

保存节点后，在"自定义参数"区域添加一个启动参数（供定时调度使用）：

| 参数名 | 参数值 | 说明 |
|--------|--------|------|
| biz_date | ${system.biz.date} | 业务日期，自动替换为前一天 |

在主SQL中`'${biz_date}'`会被DS替换为具体日期值（例如`'20250506'`），从而实现每天自动跑前一天的数据。

### 步骤4：创建SQL任务"daily_report"（查询类型，启用邮件通知）

拖入第二个SQL任务节点，命名为"daily_report"。数据源仍选择"mysql_ods_retail"，但**SQL类型选择"查询"**。

主要SQL内容：
```sql
SELECT 
    product_id          AS '商品ID',
    COUNT(*)            AS '订单数',
    SUM(amount)         AS '销售额',
    ROUND(AVG(amount), 2) AS '客单价'
FROM tmp_daily_orders
GROUP BY product_id
ORDER BY 销售额 DESC
LIMIT 10;
```

> **注意**：查询类型SQL节点只能有一条SELECT语句（有且仅有一个结果集）。不要在前面加CREATE/DROP之类的操作——那些应该放在前置SQL中。DS会根据SELECT返回的ResultSet自动渲染邮件内容。

在节点的"邮件通知"配置中：
- 勾选"发送邮件"
- 邮件接收人：填写管理层的邮箱地址列表（如ceo@retail.com, cfo@retail.com）
- 附件格式：选择"Excel附件"（也可选"HTML表格内嵌"）
- 邮件主题：`【零售日报】${system.biz.date} Top 10商品销售报表`

前置SQL中可加入数据质量检查逻辑：
```sql
-- 检查临时表是否有数据，如果为空说明上游数据异常
SELECT COUNT(*) FROM tmp_daily_orders HAVING COUNT(*) = 0;
-- 如果临时表为空且有HAVING限制，DS会将此标记为非查询类型异常
```

**查询SQL的邮件部分原理**：Worker执行完SELECT后，将ResultSet序列化为Excel字节流或HTML字符串，通过告警服务（AlertServer）调用邮件渠道插件（如阿里云邮件推送、SMTP等），以附件形式投递。配置邮件发送通道需要先在"安全中心 → 告警渠道"中完成邮件服务器的SMTP配置。

### 步骤5：连接DAG依赖

在工作流编辑器中，用连线将两个任务连接起来：

```
[ods_to_dwd] ──→ [daily_report]
```

右键连线，确认依赖条件为"成功"（上游成功后下游才执行）。这样保证daily_report拿到的`tmp_daily_orders`表一定是ods_to_dwd成功生成的。如果ods_to_dwd失败，daily_report不会执行——避免管理层收到空报表或错误报表。

### 步骤6：配置工作流通知策略

点击工作流画布空白区域，在右侧全局配置面板中设置：

| 配置项 | 设置值 | 说明 |
|--------|--------|------|
| 失败策略 | 结束 | 一个节点失败则整个流程失败 |
| 通知策略 | 仅失败发 | 成功不发邮件（因为报表任务自己会发），只有失败时通知DBA |
| Worker分组 | default | 使用默认Worker |
| 超时告警 | 开，30分钟 | 整条流程最长跑30分钟 |

### 步骤7：运行并验证

保存工作流 → 点击"上线" → 点击"运行"，选择串行执行。观察工作流实例页面：

1. ods_to_dwd节点变为绿色（成功），点击查看日志，确认"前置SQL → 主要SQL → 后置SQL"三段均执行完毕
2. daily_report节点变为绿色（成功），检查邮箱中是否收到了Excel附件的报表邮件

**验证SQL语句**（在MySQL中手动检查）：
```sql
-- 验证临时表是否被正确创建
SELECT COUNT(*) FROM tmp_daily_orders;

-- 验证新订单标签是否正确
SELECT order_type, COUNT(*) FROM tmp_daily_orders GROUP BY order_type;
```

### 步骤8：常见错误与排查

| 异常现象 | 可能原因 | 解决方法 |
|----------|---------|---------|
| 测试连接报"Driver class not found" | Worker的lib目录缺少JDBC驱动JAR | 将mysql-connector-java-8.x.jar放入`${DS_HOME}/lib/`，重启Worker |
| SQL执行报"Communications link failure" | Worker到MySQL服务器的网络不通或防火墙拦截 | 在Worker机器上执行`telnet 192.168.10.101 3306`验证连通性 |
| 任务一直排队不执行 | 数据源连接池耗尽，所有连接被其他任务占用 | 调整数据源的连接池最大连接数，或为Hot数据源单独部署Worker |
| SQL报"Data truncation: Incorrect datetime value" | 数据源连接参数未设时区，INSERT时区不匹配 | 在连接参数中加上`serverTimezone=Asia/Shanghai` |
| 邮件未收到 | AlertServer邮件渠道未配置或SMTP参数错误 | 去"安全中心→告警渠道"检查SMTP配置并发送测试邮件 |
| 后置SQL没执行但数据已写入 | 主要SQL执行成功后Worker异常退出，日志不完整 | 检查Worker日志，排查内存溢出或进程被操作系统kill的问题 |
| 密码报"解密失败" | DS部署后重新生成了AES密钥，但数据库中存储的是旧密钥加密的密码 | 重新编辑数据源并重新输入密码保存，使其以新密钥加密 |

### 步骤9：注册Hive UDF（进阶操作）

若你的ETL中需要在Hive端调用自定义UDF函数（如JSON解析、复杂加密计算等），DS资源中心提供了UDF管理入口：

**9.1 上传UDF JAR包**：进入"资源中心" → "上传文件"，选择自定义UDF的JAR包（如`parse-json-hive-1.0.jar`），上传到`/udf/`目录下。

**9.2 注册UDF函数**：进入"资源中心" → "UDF管理" → "创建UDF函数"：

| 字段 | 示例值 | 说明 |
|------|--------|------|
| 函数名 | parse_json | SQL中调用的函数名 |
| 类名 | com.retail.hive.udf.ParseJsonUDF | UDF的全限定类名 |
| JAR包 | /udf/parse-json-hive-1.0.jar | 上一步上传的JAR路径 |
| 数据库名 | retail_dw | 该UDF作用于哪个数据库 |

**9.3 在SQL节点中引用**：

在SQL任务节点的前置SQL中声明临时函数：
```sql
-- 使用资源中心注册的UDF：DS会自动下载JAR并加载到Hive会话中
CREATE TEMPORARY FUNCTION parse_json AS 'com.retail.hive.udf.ParseJsonUDF'
USING JAR 'hdfs:///dolphinscheduler/resources/udf/parse-json-hive-1.0.jar';
```

之后在主要SQL中即可调用：
```sql
SELECT parse_json(raw_payload, '$.user_id') AS user_id
FROM ods_event_log
WHERE dt = '${biz_date}';
```

> **原理**：Worker在执行SQL前，先从资源中心（HDFS或本地存储）拉取UDF JAR到本地工作目录，然后通过`ADD JAR`或HiveQL的`USING JAR`子句将该JAR注册到当前Hive会话中。函数注册成功后，同节点的所有SQL（前置、主要、后置）均可调用该UDF。节点执行完毕后会话关闭，临时函数自动销毁，不留副作用。

## 4. 项目总结

### SQL任务节点：优点与局限

| 维度 | 优点 | 局限 |
|------|------|------|
| 连接管理 | 统一数据源中心，密码AES加密，连接池复用（Druid/HikariCP），无需在每个脚本中硬编码连接串 | Worker与数据库必须网络互通，跨VPC场景需额外配置网络代理 |
| SQL结构 | 三段式设计（前置/主要/后置），同连接同事务顺序执行，失败自动回滚 | 事务边界仅限单个数据源内部——**不支持跨数据源的分布式事务** |
| 结果输出 | 查询结果自动生成Excel/HTML邮件附件，无需额外编码 | 结果集过大（超10万行）时邮件附件可能超出邮件服务器限制，需拆分为多封或改用文件存储 |
| 函数扩展 | UDF管理闭环（上传JAR→注册函数→SQL引用），动态加载 | UDF的JAR包需要与Hive/Spark版本兼容，版本升级后需同步更新 |
| 数据库种类 | SPI插件体系支持28+种数据源，社区持续扩展 | 部分数据源的插件版本滞后（如ClickHouse驱动），需自行升级JAR |
| 性能 | 连接池复用减少建连开销，批处理类SQL可在Worker上长时间运行 | 大数据量查询（千万级）可能导致Worker内存溢出，需合理设置超时和内存限制 |

### 适用与不适用场景

**适用场景**：
1. **日/周固定ETL**：每天凌晨从ODS到DWD到DWS的多层数据加工流水线——这正是本章案例
2. **数据质量检查**：主流程跑完后执行一组CHECK SQL（如检查行数偏差、金额平账等），异常时自动发告警
3. **报表自动生成与推送**：按业务日期跑聚合SQL，结果自动邮件推送——本章的daily_report节点
4. **数据修正/批量补数**：结合DS的补数功能，录入历史日期范围自动重跑历史SQL

**不适用场景**：
1. **实时流处理**：SQL节点本质是批处理，不适合秒级/毫秒级的CDC或实时聚合——这种需求应使用Flink任务节点
2. **跨多数据源事务**：如果你需要在MySQL扣库存的同时在Hive写日志——DS不支持跨源分布式事务，需要引入Seata等方案在SQL脚本内自行协调
3. **单次查询返回海量数据**：如果SELECT结果有百万行且需要落盘处理，使用DataX/Sqoop同步任务节点更合适，而非SQL查询节点+邮件推送

### 常见踩坑经验

1. **SQL超时配置陷阱**：DS的SQL任务节点有自身的超时时间（任务级），数据源也有JDBC连接超时和查询超时。生产环境中常见的问题是——DS任务超时设为10分钟，但MySQL的`wait_timeout`默认是8小时，导致慢SQL一直卡着不报错。建议在SQL前置段显式设置`SET SESSION max_execution_time = N`，让数据库主动超时。
2. **大数据量查询内存溢出**：Worker执行SELECT查询时，ResultSet默认全部放入内存再渲染邮件附件。如果查询返回50万行数据，Worker可能直接OOM退出。建议在查询SQL中加`LIMIT`限制输出行数，或改用"非查询"类型+`INSERT INTO ... SELECT`方式，让数据在数据库内部流转，不经过Worker内存。
3. **数据源密码轮换导致任务批量失败**：如果MySQL管理员定期轮换密码，所有在该节点后注册的数据源会在一夜之间全部"连接失败"。建议将数据源密码统一存储在配置中心，通过API定期更新DS的数据源密码，并做好密码过期提醒。
4. **跨数据源事务的认知误区**：新手常以为DS一个SQL节点可以同时操作MySQL和Hive——实际上一个SQL节点绑定一个数据源。如果你需要在同一个工作流中先写MySQL再写Hive，分别是两个独立的SQL节点（或子流程），前置成功后写MySQL，再下游写Hive。MySQL的事务已提交，如果Hive写入失败，MySQL已提交的数据不会自动回滚——需要你在工作流中设计补偿逻辑。
5. **前置SQL失败但后置SQL跑了**：这种情况理论上不会出现（WSQL节点事务保证），但如果前置SQL中包含了允许失败的命令（例如`DROP TABLE IF EXISTS`对不存在的表也返回成功），那么主要SQL执行失败后事务回滚——但前置SQL中已执行成功的DROP不会回滚。换言之，**只有DML语句才会在事务回滚中被撤销，DDL语句（如CREATE、DROP）通常是隐式提交的**。这个MySQL自身的事务特性需要特别注意。

### 思考题

1. 假设ods_to_dwd任务的前置SQL中有一条`DROP TABLE IF EXISTS tmp_daily_orders`，主要SQL执行`CREATE TABLE tmp_daily_orders AS SELECT ...`失败（因源表字段变更导致类型不匹配）。前置SQL中的DROP已经执行，它会被回滚吗？为什么？这个特性对你的SQL设计有什么启示？

2. 你的工作流中有SQL任务节点A（查询类型，结果邮件发送给老板）和SQL任务节点B（非查询类型，向报表库INSERT结果数据）。如果节点B成功执行但节点A失败，老板没收到邮件但数据已经写入了报表库。请设计一个方案，保证"数据写入"和"邮件发送"要么都成功，要么都失败（允许最终一致性）。如果在DolphinScheduler内部无法做到强一致，你会选择什么补偿手段？
