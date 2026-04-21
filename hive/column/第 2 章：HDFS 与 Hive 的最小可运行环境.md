# 第 2 章：HDFS 与 Hive 的最小可运行环境

> **专栏分档**：基础篇  
> **总纲索引**：[hive-column-outline.md](../hive-column-outline.md)（第五章 · 第 2 章对照表）  
> **业务主线**：电商平台「用户行为 + 交易」离线数仓（曝光、点击、下单、退款、风控特征）。

## 本章大纲备忘（写作前对照总纲）

| 项 | 内容 |
|----|------|
| 一句话摘要 | 用 Docker 或单机最小集跑通 Namenode/Datanode + Hive，验证 hive-site.xml。 |
| 业务锚点 / 技术焦点 | 新人本机搭环境反复失败、版本碎片化。 |
| 源码或文档锚点 | 详见 [hive-column-outline.md](../hive-column-outline.md) 第五章表格中第 2 章一行；官方示例 [source/hive/packaging/src/docker/README.md](../source/hive/packaging/src/docker/README.md)。 |

单章目标篇幅 **3000～5000 字**，四段结构对齐 [template.md](../template.md)。

---

## 1 项目背景（约 500 字）

数据平台要给「用户行为数仓」项目开环境：新人笔记本上要能 **跑通 HDFS 读写 + Hive CLI/Beeline 建表查询**，否则 DDL/DML 章节无法跟练。现实痛点是：每个人本机 JDK、Hadoop、Hive 小版本不一致，**ClassNotFound、Guava 冲突、端口占用** 在群里刷屏；还有人把「能启动进程」当成「环境 OK」，结果 Metastore 指向嵌入式 Derby，换机器元数据全丢。

团队决定收敛为 **Docker 一键栈**（或公司统一镜像），把 **NameNode/DataNode、Metastore、HiveServer2** 的最小依赖绑在一起，并在 Wiki 固定 **唯一受支持的版本矩阵**。本章目标：你按文档能复现「容器起来 → HDFS 有目录 → Hive 能 `CREATE TABLE`」，并理解 **hive-site.xml 里哪些键是「连得上」的关键**。

补充三个**工程现实**：（1）**JDK 版本**需与发行版矩阵一致，常见是 JDK8/11/17 分叉，混用会在 `beeline` 启动期就爆 `UnsupportedClassVersionError`。（2）**时区与 locale**：容器默认 UTC，若 SQL 里大量 `to_date`/`from_unixtime` 依赖本地时区，会出现「分区对但数不对」的错觉。（3）**资源预留**：Docker Desktop 若只给 4GB，Hive+Tez+DN 同时起极易 OOM，表现为 HS2 间歇 `Thrift timeout`，根因其实是 YARN 容器申请失败——排障时先看 `dmesg`/Docker stats，不要盲重试 SQL。

---

## 2 项目设计（约 1200 字）


> **角色（对齐 [template.md](../template.md)）**：**小胖**（生活化比喻、抛问题）· **小白**（原理、边界、风险与备选）· **大师**（选型与「**技术映射**」承接）。  
> **对话结构**：小胖开球 1～2 轮 → 小白追问 2～3 轮 → 大师解答并引出下一子话题；全文循环 **2～3 次**，覆盖本章核心概念。

**小胖**：为啥不直接装个「Hive 单机安装包」完事？Docker 还要学一堆命令。

**小白**：单机包也能行，但难在 **可重复**。Docker 把 OS、依赖、端口、卷挂载写进 Dockerfile/Compose，新人 `docker compose up` 得到的是同一拓扑。否则你本机 Hive 2.x、我本机 4.x，DDL 行为差异会让后面章节无法对齐。

**大师**：可以把 Docker 想成 **「可版本化的虚拟机」**。对 Hive 学习来说，最重要的是三件事：**HDFS 可用**（表数据落哪）、**Metastore 指向哪**（元数据存哪）、**HS2 暴露端口**（Beeline 连哪）。镜像只是载体。

**技术映射**：Compose 文件 ≈ **最小分布式拓扑声明**。

**小胖**：那我内存只有 16G，跑不动三五个 Java 进程怎么办？

**小白**：可以裁：开发机用 **伪分布式** 或官方 demo 镜像的 **缩减配置**，降低 `dfs.replication`、YARN 容器上限；或只在远端开发集群上开账号，本机只装 Beeline。关键是 **文档写清最低配置**，别默认人人 64G 工作站。

**大师**：对。学习路径上，**先远端后本地** 或 **先本地 Docker 后集群** 都可以，但要统一「验收命令」：同一条 `CREATE TABLE` + `INSERT` + `SELECT COUNT` 全员可跑通。

**技术映射**：验收命令 ≈ **黄金路径（golden path）**。

**小胖**：HDFS 和 Hive 谁依赖谁？

**小白**：Hive 依赖 **能存文件的分布式存储**（常见 HDFS）和 **元数据服务**。没有 HDFS 也能用本地文件或云存储适配，但教材与生产最常见仍是 HDFS。

**大师**：心智顺序建议：**HDFS 能 put/get → Metastore 能连库 → HS2 能连 Metastore**。任何一步 JDBC URL 写错，现象都是「能进 shell 不能查表」或「表建了找不到文件」。

**小胖**：我本地起不来，能不能只连公司 VPN 里的共享集群？

**小白**：可以，而且常常是 **更省时间** 的路径：本机只装兼容版 `beeline` 与 JDBC 驱动，所有重进程在远端。代价是 **网络抖动、权限审批、不能随意改 hive-site**。

**大师**：技术映射：**本地/远程** 不是优劣之分，而是 **反馈回路长度** 的权衡——新手先用共享集群跑黄金路径，再回本地 Docker 做破坏性实验（例如故意配错 `hive.metastore.uris` 观察报错栈）。

**技术映射**：把「环境问题」分类为 **网络 / 认证 / 元数据 / 执行引擎 / 资源** 五类，每类对应不同 owner（网络组、安全组、平台组）。

---

## 3 项目实战（约 1500～2000 字）

以下给出 **可复现思路**；具体镜像名与 Compose 片段以你拉取的 Apache Hive 源码树中 [packaging/src/docker/README.md](../source/hive/packaging/src/docker/README.md) 为准（版本迭代时命令可能微调）。

### 环境准备

- 安装 Docker Desktop（Windows/macOS）或 Docker Engine（Linux）。  
- 分配内存建议 **≥ 8GB** 给 Docker；CPU 2 核+。  
- 克隆或已拥有本仓库中的 `source/hive`，便于对照官方 Docker 说明。

### 步骤 1：阅读官方 Docker README（目标：选对 profile）

在仓库中打开 `source/hive/packaging/src/docker/README.md`，确认当前推荐启动方式（例如 `docker compose` 组合服务名、暴露端口 `10000` 给 HS2、`9870`/`9000` 给 HDFS 等）。把文档中的 **端口列表** 抄到自己的 `ENV-NOTES.md`。

### 步骤 2：启动栈并检查 HDFS（目标：证明文件系统可用）

按 README 启动后，在容器内或 `docker exec` 进入 NameNode 容器执行：

```bash
# 示例：列出根目录（具体 hdfs 命令路径以镜像为准）
hdfs dfs -ls /
```

**预期**：能列出目录，无 `Connection refused` 到 NameNode。

**坑**：Windows 路径与卷挂载权限导致 `dataNode` 起不来 → 检查 Docker 文件共享设置；或改用 WSL2 后端。

### 步骤 3：检查 Hive 配置入口（目标：认识 hive-site.xml）

在镜像或挂载卷中找到 `hive-site.xml`，确认至少理解以下键的语义（值以环境为准）：

```xml
<!-- 示例键名，值勿照抄生产 -->
<!-- javax.jdo.option.ConnectionURL：Metastore 后端 JDBC -->
<!-- hive.metastore.uris：远程 Metastore  thrift 地址（若分离部署） -->
<!-- hive.server2.thrift.bind.host / port：HS2 监听 -->
```

**目标**：能口述「**元数据在 DB 里，表文件在 HDFS 路径上**」。

### 步骤 4：用 Beeline 走黄金路径（目标：与第 4 章衔接）

```bash
# 在客户端容器或宿主机（需网络可达）
beeline -u "jdbc:hive2://<host>:10000" -n hive -p hive
```

进入后执行：

```sql
SHOW DATABASES;
CREATE DATABASE IF NOT EXISTS demo_ods;
USE demo_ods;
CREATE TABLE IF NOT EXISTS hello (id INT, msg STRING);
INSERT INTO hello VALUES (1, 'hdfs+hive ok');
SELECT * FROM hello;
```

**预期**：`INSERT` 可能走 MR/Tez/Spark（较慢属正常），最终 `SELECT` 有行。

**坑**

- `jdbc:hive2` 连错主机（用了 localhost 但容器网络别名不同）→ 检查 compose network。  
- Metastore 未就绪导致 `Could not open client transport` → 按日志顺序起服务。  
- 存储在容器内无持久卷 → 重建容器后库表空；学习环境可接受，但要心里有数。

**验证**：把 `SHOW DATABASES` 与 `SELECT` 输出贴到团队 onboarding PR 作为截图或文本附件。

### 排障速查表（建议打印贴在显示器边）

| 现象 | 优先检查 | 常见根因 |
|------|-----------|----------|
| `Connection refused` 到 10000 | HS2 是否 listen、`docker ps`、端口映射 | 进程未起 / 防火墙 / 写错 host |
| `Could not open client transport` | Metastore 日志、ZK（若用） | Metastore 晚于 HS2 启动 |
| `Relative path in absolute URI` | `hive-site.xml` 里 warehouse 路径 | Windows 路径与 `file://` 混用 |
| `Java heap space` on HS2 | JVM opts、并发连接数 | Docker 内存过小 / 大结果集拉取 |
| HDFS `Safe mode` | NN 日志 | 集群未完全就绪或副本不足 |

### 预期输出示例（文字描述即可归档）

- `hdfs dfs -ls /` 出现 `tmp`、`user`、`warehouse` 等目录；无 `Operation category READ is not supported in state safe mode` 持续刷屏。  
- Beeline 执行 `INSERT INTO hello ...` 后，`SELECT` 返回一行；YARN UI 若可见一个短暂应用，记录其 **elapsed** 作为本机性能基线。

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
| Docker 固化版本与拓扑，便于教学与 CI | 与真实生产 Kerberos、Ranger 等差距大 |
| 一键拉起，缩短新人上手时间 | 本机资源占用高，笔记本风扇常转 |
| 可挂载 `hive-site.xml` 做实验 | 镜像升级需有人跟进 README 变更 |
| 方便演示 HDFS + Hive 协作 | 网络/DNS/卷问题排障门槛仍在 |

### 适用与不适用

- **适用**：本地联调、写书示例、培训营、PoC。  
- **适用**：统一团队「黄金路径」验收。  
- **不适用**：直接当生产集群（缺高可用、备份、审计与配额治理）。  
- **不适用**：强依赖 GPU/特定硬件的算子测试（需另配镜像）。

### 注意事项

- **版本锁**：Hive、Hadoop、Tez/Spark 组合需对齐发行说明。  
- **持久化**：学习数据是否需要 bind mount，避免「以为表丢了其实是容器删了」。  
- **安全**：默认镜像常带弱口令，**禁止暴露到公网**。

### 常见生产踩坑

1. **各环境 hive-site 漂移**：测试能跑、上生产 JDBC 指向错 Metastore。  
2. **时间不同步**：Kerberos 或日志对齐失败（后续安全章展开）。  
3. **小文件直接灌 HDFS 再建外表**：NN 压力与统计失真（分区治理章展开）。

### 思考题

1. 若 Metastore 使用内嵌 Derby，多人共用一台开发机会发生什么？如何避免？  
2. `INSERT` 明显很慢时，你如何通过 `EXPLAIN` 先判断是「引擎冷启动」还是「数据倾斜前兆」？（第 14 章衔接。）  
3. 若公司要求 **镜像每月重建**，你如何保证 **hive-site.xml 与密钥** 不硬编码进镜像层、又能一键拉起？

### 跨部门推广提示

- **运维**：把 Compose 或 Helm 参数表纳入 **标准交付物**，写明端口与卷。  
- **测试**：用黄金路径 SQL 做 **冒烟脚本**，发版前跑一遍。  
- **开发**：本地与集群 **同名配置 profile**（dev/stage），减少复制粘贴错误。
