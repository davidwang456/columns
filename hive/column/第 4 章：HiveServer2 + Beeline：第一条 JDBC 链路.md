# 第 4 章：HiveServer2 + Beeline：第一条 JDBC 链路

> **专栏分档**：基础篇  
> **总纲索引**：[hive-column-outline.md](../hive-column-outline.md)（第五章 · 第 4 章对照表）  
> **业务主线**：电商平台「用户行为 + 交易」离线数仓（曝光、点击、下单、退款、风控特征）。

## 本章大纲备忘（写作前对照总纲）

| 项 | 内容 |
|----|------|
| 一句话摘要 | 启动 HS2，用 Beeline/JDBC 完成建库查表。 |
| 业务锚点 / 技术焦点 | 多语言接入、连接串与账号。 |
| 源码或文档锚点 | 详见 [hive-column-outline.md](../hive-column-outline.md) 第五章；`source/hive/service`、`beeline`、`jdbc`；[beeline使用.md](../beeline使用.md)。 |

单章目标篇幅 **3000～5000 字**，四段结构对齐 [template.md](../template.md)。

---

## 1 项目背景（约 500 字）

数据服务组要把「行为明细查询」从 **仅 CLI** 升级为 **多语言 JDBC 接入**：Java 报表服务、Python 离线脚本、BI 工具都需要统一走 **HiveServer2（HS2）**。现状痛点：有人仍用已废弃的直连方式；有人把 **用户名口令** 写死在仓库；大促期间 **连接数打满** 导致「无报错但一直排队」。本章用 Beeline 模拟 JDBC 客户端，跑通 **建库、会话参数、简单查询**，并建立 **连接串、认证、队列** 的初步概念（安全深化见中级篇）。

补充 **JDBC 客户端侧** 两个细节：（1）**`hive.server2.transport.mode`** 若为 `http`，URL 形态会变成 `jdbc:hive2://host:10001/...;transportMode=http` 一类（以发行版为准），与 BI 工具默认模板不一致时常踩坑。（2）**`fetchSize`**：默认过小会导致 **服务端游标频繁往返**，过大则 **客户端堆内存** 压力上升——批拉数与报表分页要分开设计。

---

## 2 项目设计（约 1200 字）


> **角色（对齐 [template.md](../template.md)）**：**小胖**（生活化比喻、抛问题）· **小白**（原理、边界、风险与备选）· **大师**（选型与「**技术映射**」承接）。  
> **对话结构**：小胖开球 1～2 轮 → 小白追问 2～3 轮 → 大师解答并引出下一子话题；全文循环 **2～3 次**，覆盖本章核心概念。

**小胖**：Beeline 和 JDBC 啥关系？不都是连数据库吗？

**小白**：Beeline 是官方 **JDBC 瘦客户端**，底层走 HiveServer2 的 Thrift 协议。你在 Java 里用 `DriverManager.getConnection("jdbc:hive2://...")` 与 Beeline 本质是同一类链路。

**大师**：技术映射：**HS2 = 多会话 SQL 网关**；Metastore = **目录**；执行引擎 = **算子落地**。客户端不该绕过 HS2 直连执行（除非特殊工具链）。

**小胖**：那连接串里 host、port、database 都要写吗？

**小白**：`jdbc:hive2://host:port/db` 是常见形态；还可带 `?mapreduce.job.queuename=...` 等参数（是否生效取决于版本与服务端策略）。生产常配合 **ZooKeeper 服务发现**（扩展章）或负载均衡 VIP。

**大师**：要把 **「谁连上来」** 搞清楚：Kerberos principal、LDAP 绑定、或简单用户名（内网实验）。否则审计上全是 `hive` 用户。

**技术映射**：连接串 ≈ **会话上下文初始化参数包**。

**小胖**：大促连不上，是不是加机器就行？

**小白**：可能是 HS2 线程/队列满、YARN 资源不足、元数据慢。要先看 **HS2 日志** 与 **YARN 排队**，不能盲扩容。

**大师**：本章先建立 **最小可观测习惯**：连接失败时记录 **客户端栈、服务端日志片段、YARN 应用 ID** 三件套。

**小胖**：Python 里用 `pyhive` / `jaydebeapi` 连，和 Java 有差别吗？

**小白**：协议仍是 Thrift/JDBC，差别在 **依赖管理**（驱动 fat jar）、**Kerberos 登录模块**、以及 **pandas 一次性拉全表** 这种「自杀式用法」。

**大师**：技术映射：**语言绑定不改变服务端语义**；改变的是 **你如何管理连接生命周期与结果集迭代**。

---

## 3 项目实战（约 1500～2000 字）

### 环境准备

- HS2 已监听（常见 `10000`）。  
- 安装 Beeline（通常随 Hive 包）。  
- 知晓测试用户名（内网可能无密码或弱密码）。

### 步骤 1：命令行 Beeline 登录（目标：验证 Thrift）

```bash
beeline -u "jdbc:hive2://<HS2_HOST>:10000/default" -n <USER> -p <PASSWORD_OR_EMPTY>
```

进入后：

```sql
SELECT current_user(), version();
SHOW DATABASES;
```

**预期**：返回当前用户与版本信息；库列表非空。

**坑**：`Connection refused` → 检查 HS2 是否绑定 `0.0.0.0`、防火墙、Docker 网络别名。

### 步骤 2：设置会话级参数（目标：理解「会话作用域」）

```sql
SET hive.exec.dynamic.partition = true;
SET hive.exec.dynamic.partition.mode = nonstrict;
SET mapreduce.job.queuename = root.users.dev;
```

执行 `SET mapreduce.job.queuename;` 查看回显。

**预期**：理解 **部分参数仅当前会话有效**，断开重连后需重新 `SET` 或由服务端默认配置注入。

### 步骤 3：Java 伪代码级 JDBC（目标：与多语言一致）

```java
// 需 hive-jdbc 与传递依赖；版本与服务器对齐
// String url = "jdbc:hive2://hs2:10000/default";
// Properties p = new Properties();
// p.setProperty("user", "dev");
// Connection c = DriverManager.getConnection(url, p);
// Statement s = c.createStatement();
// ResultSet rs = s.executeQuery("SELECT 1");
```

将以上片段保存为团队 **Snippet 库** 链接，避免每人复制不同驱动版本。

### 步骤 4：压测意识（可选）

用 `n` 个并行 shell 同时 `beeline -e "SELECT 1"` 观察 HS2 日志与响应时间，记录 **拐点连接数** 作为容量参考。

**验证**：从非 Hive 节点（如跳板机）完成一次远程 Beeline 查询，证明 **网络与认证** 正确。

### 非交互验收（适合 CI 或 cron）

```bash
beeline -u "jdbc:hive2://<HS2_HOST>:10000/default" -n <USER> -e "SELECT 1 AS ok;"
```

**预期**：标准输出末尾出现 `ok` 列值为 `1`；非零退出码应触发告警到 on-call。

### 会话元数据自查

```sql
SET system:user.name;
SET hive.execution.engine;
SET mapreduce.job.queuename;
```

把三者截屏存档，可快速判断「这个人连的是谁、跑在哪个引擎、进哪个队列」——**工单第一屏信息**。

### 运行结果与测试验证（模板对齐）

- 各步骤给出「预期 / 验证」；建议 `beeline -f` 批量执行。**自测回执**：SQL 文件链接 + 成功输出 + 失败 stderr 前 80 行。

### 完整代码清单与仓库附录（模板对齐）

- **本章清单**：合并上文可执行片段为单文件纳入团队 Git（建议 `column/_scripts/`）。
- **上游参考**：<https://github.com/apache/hive>（对照本仓库 `source/hive`）。
- **本仓库路径**：`../source/hive`。

---

## 4 项目总结（约 500～800 字）

### 优点与缺点

| 优点 | 缺点 |
|------|------|
| 标准 JDBC，生态成熟 | 高并发短查询非强项 |
| 统一入口便于审计与限流 | 错误栈常需结合 YARN 日志 |
| 与 BI 工具兼容好 | 驱动与服务器版本不匹配易踩坑 |
| 支持会话变量与服务器默认值组合 | 长会话占用句柄，需超时治理 |

### 适用与不适用

- **适用**：批查询、探索式分析、ETL 调度提交 SQL。  
- **不适用**：替代 OLTP 连接池承载高 QPS 点查。  

### 注意事项

- **驱动版本锁**：与 HS2 主版本对齐。  
- **不要在公网暴露 10000**。  
- **队列**：大作业与探测查询分队列，避免互相饿死。

### 常见生产踩坑

1. **所有客户端共用超级账号**：审计失效 + 误删风险。  
2. **JDBC URL 指向旧节点**：切主后仍连旧 IP。  
3. **未设置 fetch size 导致 OOM**：超大结果集拉取方式错误（按框架调优）。

### 思考题

1. 若 HS2 前加负载均衡，Zookeeper 与静态 VIP 各有什么运维取舍？  
2. `SET` 与 `hive-site.xml` 默认值的优先级在你们的发行版里如何验证？  
3. 设计一个 **连接池大小** 公式：已知峰值 QPS、平均查询时长、允许排队长度，如何估算 JDBC 池上限以避免打满 HS2？

### 跨部门推广提示

- **运维**：提供 **标准 JDBC URL 模板** 与 TLS/Kerberos 开关说明。  
- **测试**：用 JDBC 集成测试跑 **最小结果集契约**。  
- **开发**：禁止在业务微服务中 **同步** 拉 Hive 大结果集阻塞请求线程。
