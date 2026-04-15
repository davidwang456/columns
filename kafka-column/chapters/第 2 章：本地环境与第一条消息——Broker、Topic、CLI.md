# 第 2 章：本地环境与第一条消息——Broker、Topic、CLI

> 级别：基础  
> 预计阅读：约 3000～5000 字（正文以汉字为主，含标点）  
> 配套示例：[examples/docker-compose.yml](../examples/docker-compose.yml)，Broker **3.7.0** / **KRaft** / **localhost:9092**

---

## 1. 项目背景

> **读法提示**：本章「项目设计」为 **小胖 / 小白 / 大师** 三角色对话——**小胖**用直觉与「土问题」抛误区，**小白**追问原理、边界与风险，**大师**结合业务约束给选型与架构级结论；请对照「项目实战」动手与「项目总结」沉淀。

读完第 1 章后，读者需要 **可验证的第一次接触**：本机起 Broker、创建 Topic、用 CLI 或客户端发一条消息并读回。痛点常是：**端口不通**、**Listener 配置**、**脚本路径**、**Windows 与容器路径** 混用导致挫败。本章产出物：**（1）** 能独立启动专栏配套的 `docker-compose`；**（2）** 能用 `kafka-topics` / `kafka-console-producer` / `kafka-console-consumer` 完成端到端；**（3）** 能口述 **Bootstrap**、**Topic**、**Broker** 在本地拓扑中的角色。

**Docker 前置**：建议 Docker **≥ 20.10.4**（官方镜像文档提示），避免卷权限问题导致配置目录不可写。若公司策略禁用 Docker，可改用公司提供的 **共享开发集群**，但需拿到 **bootstrap 地址、认证方式、Topic 命名规范**，并在笔记本上记录一份「最小收发」命令以便排障。

**与 CI 的关系**：流水线里可只跑 **API 版本探测** 或 **Testcontainers**（见第 38 章），但新人第一步仍建议本机 compose，便于对照日志与 `docker ps`。

当你第一次成功打印出「从 Topic 读到一行字符串」时，建议立刻做两件事：**第一**，把 **bootstrap、topic 名、分区数**记在笔记里；**第二**，打开 `kafka-topics --describe` 看 **Leader 与 ISR**。很多团队跳过第二步，导致后续一遇到 **UnderReplicated** 就慌乱——其实本地单节点 ISR 往往只含一个 broker id，这是**正常现象**，不是故障。把「正常长什么样」记下来，线上才分得清「异常长什么样」。

---

## 2. 项目设计：小胖、小白与大师的对话

**小胖**：我就想在本机跑通「发一条、收一条」，Docker、端口、脚本路径老打架——有没有最笨但一定能成的路？

**小白**：（接小胖）我 `docker compose up` 了，但客户端连 `localhost:9092` 超时，是不是 Kafka 坏了？

**大师**：先区分 **容器内监听** 与 **宿主机端口映射**。专栏 compose 将 **PLAINTEXT_HOST** 绑定到 `9092`，宿主机应访问 `localhost:9092`；若在容器内跑脚本，bootstrap 可能仍是 `localhost:9092` 或 `broker:19092`，取决于 **listener 设计**。一句话：**客户端看到的地址必须等于 advertised listener 对客户端的可达地址**。

**小白**：Topic 我先不建，直接发消息行不行？

**大师**：取决于 **auto.create.topics.enable** 是否开启；生产常关闭。专栏建议 **显式创建** topic，避免分区数、RF 与线上一致性失控。单节点环境 **RF 只能为 1**（见第 4 章）。

**小白**：`kafka-topics.sh` 在哪？我找不到 `bin` 目录。

**大师**：官方镜像通常在 **`/opt/kafka/bin`**。用 `docker exec -it <container> bash` 进入后执行；或把脚本路径写进团队文档。

**收束**：验收标准：**producer 发一条 → consumer 读一条**，且 `kafka-broker-api-versions` 能返回成功。

三个推论：**推论 A**，「端口通就等于集群健康」——还要 **metadata**、**磁盘**、**controller**；**推论 B**，「Topic 名随意起」——**命名规范**、**环境前缀**；**推论 C**，「本地单节点与生产一致」——**拓扑一致**不可能，但 **API 与语义** 应对齐。

---

## 3. 项目实战

### 3.1 环境说明

在仓库路径 `kafka-column/examples` 执行：

```bash
docker compose up -d
```

健康检查：容器日志出现 **Running in KRaft mode** 或等价信息；或使用：

```bash
docker exec -it kafka-column-broker /opt/kafka/bin/kafka-broker-api-versions.sh --bootstrap-server localhost:9092
```

（容器名以 [docker-compose.yml](../examples/docker-compose.yml) 中 `container_name` 为准。）

### 3.2 主操作：创建 Topic 并收发

**创建 Topic**（示例：2 分区、副本 1）：

```bash
docker exec -it kafka-column-broker /opt/kafka/bin/kafka-topics.sh \
  --create --if-not-exists --topic column.hello \
  --bootstrap-server localhost:9092 --partitions 2 --replication-factor 1
```

若提示 Topic 已存在，可省略创建或换名；若提示副本因子非法，请回到第 4 章核对 **Broker 数量与 RF**。

**消费端**（终端 A）：

```bash
docker exec -it kafka-column-broker /opt/kafka/bin/kafka-console-consumer.sh \
  --bootstrap-server localhost:9092 --topic column.hello --from-beginning
```

**生产端**（终端 B）：

```bash
docker exec -it kafka-column-broker /opt/kafka/bin/kafka-console-producer.sh \
  --bootstrap-server localhost:9092 --topic column.hello
```

带 Key 的生产（与第 3、5 章衔接，键值以 **Tab** 分隔，需开启 parse.key）：

```bash
docker exec -it kafka-column-broker /opt/kafka/bin/kafka-console-producer.sh \
  --bootstrap-server localhost:9092 --topic column.hello \
  --property parse.key=true --property key.separator=$'\t'
```

输入若干行后回车，观察消费者打印。

**期望现象**：消息按 **分区内** 顺序出现；**跨分区**顺序不保证（见第 3 章）。若使用 Key，可观察 **同一 Key** 是否稳定进入同一分区（第 3 章详解）。

### 3.3 贴近生产的变体

- 使用 **Java 客户端** 跑 [第 5 章](<第 5 章：Producer 基础——发送、Key、Headers.md>) 或 [第 3 章](<第 3 章：Topic 与 Partition——并行与有序边界.md>) 示例，对比 **RecordMetadata** 与 CLI 行为。
- 在 `kafka-topics.sh --describe` 查看 **Leader/ISR**（与第 4 章衔接）。

**排障清单（宿主机连不上）**：**(1)** `docker ps` 是否 healthy；**(2)** 端口映射是否被占用；**(3)** 防火墙与 VPN；**(4)** 是否误用容器内 hostname 作为宿主机 bootstrap。逐项排除后再怀疑「Kafka 配置」。


### 3.4 运维视角（提要）

- 变更与发布：配置、副本、Leader 迁移对写入与消费的影响；关注指标与日志入口（参见第 10、22 章及 [rollout/OPS_ALERT_MAPPING.md](../rollout/OPS_ALERT_MAPPING.md)）。
- 容量与磁盘：分区数、保留策略与磁盘水位；避免「只盯 Lag 不盯 ISR」。

### 3.5 测试视角（提要）

- 集成：最小可复现链路 + 断言幂等与顺序边界；异常：断网、Broker 重启、重复投递（按环境裁剪）。
- 性能：对比调参前后同负载下的延迟与吞吐，避免「感觉更快」。


---

## 4. 项目总结

### 4.1 优点

- **反馈快**：本地闭环建立信心。
- **可复现**：compose 版本化便于团队共享。

### 4.2 缺点

- **单节点** 无法演练多副本、ISR 抖动。
- **与生产网络**差异大（TLS、SASL、ACL 等）。

### 4.3 适用场景

- 新人上手、示例代码、CI 集成测试。

### 4.4 注意事项

- 固定 **Kafka 版本** 与 **客户端版本**（见 [WRITING_SPEC.md](../WRITING_SPEC.md)）。
- Windows 路径与 PowerShell 引号需注意转义。

### 4.5 常见踩坑

- **连错 bootstrap**（listener 与 advertised 不一致）。
- **容器未就绪** 就发消息。
- **RF>1** 在单 Broker 上创建失败（见第 4 章）。
- **消费端 `--from-beginning`** 与 **已存在组位移** 混用导致「读不到新消息」。
- **字符集与换行**：Windows 控制台复制粘贴到容器内脚本时引入 **BOM** 或异常字符。

**与后续章节的衔接**：第 3 章分区与 Key；第 4 章副本；第 5～8 章生产消费 API。


### 4.6 跨部门协作提要

- **研发**：把本章涉及的 API、配置键与默认值记入团队 Wiki；变更前评估对上下游 Topic 与消费者组的影响。
- **运维**：将关键指标接入告警与值班手册；发布与扩缩容步骤可回滚。
- **测试**：用例覆盖正常、重复、乱序、迟到与故障注入；与产品对齐 SLA 语言（如 RPO/RTO、可接受重复）。


---

## 附录（可选）

### 研发：设计评审问题清单

- 本地 Topic 命名是否与 **环境前缀** 隔离？

### 测试：用例模板

- 冒烟：compose 起停、收发成功。

### 运维：变更与告警映射

- 本地无告警；预发/生产对接 **磁盘、JMX**（第 10、22 章）。

---

## 命令速查（容器内路径以镜像为准）

| 目的 | 命令 |
|---|---|
| 列出 Topic | `kafka-topics.sh --bootstrap-server localhost:9092 --list` |
| 描述 Topic | `kafka-topics.sh --bootstrap-server localhost:9092 --describe --topic <t>` |
| 删除 Topic（慎用） | `kafka-topics.sh --bootstrap-server localhost:9092 --delete --topic <t>` |

**说明**：专栏冻结 **3.7.x** 与 **KRaft**；若你使用公司集群，需替换 **bootstrap** 与 **安全参数**（第 12 章）。

---

## 5. 练习与思考题（约 20 分钟）

1. **冷启动**：从零执行 compose → `kafka-broker-api-versions` → 创建 Topic → producer → consumer，全程截图或复制终端输出到笔记。  
2. **对比实验**：同一 Topic 发 10 条带 **Key** 的消息与 10 条 **Key=null** 的消息，观察 consumer 是否呈现不同分区行为（衔接第 3 章）。  
3. **故障注入**：`docker stop` Broker 再 `start`，重复 `kafka-topics --describe`，记录 Leader 是否变化（为第 4、10 章留印象）。  
4. **文档化**：把你在本机使用的 **容器名、bootstrap、脚本路径** 写成一页「团队标准」，避免新人每次从零踩坑。

---

## 6. 本地与共享集群对照

| 维度 | 本机 compose | 公司共享集群 |
|---|---|---|
| 认证 | 常无 | 常有 SASL/SSL |
| 副本 | 常 RF=1 | 常 RF≥3 |
| Topic 创建 | 自主 | 可能需工单 |
| 适用 | 学习/单测 | 联调/预发 |

---

## 7. 与 Windows 开发者的提示

- **路径**：优先在 **Git Bash** 或 **WSL** 中运行文档中的 shell 示例，PowerShell 需注意引号与转义。  
- **Docker Desktop**：分配足够内存与 CPU，避免 Broker OOM 被误判为「网络问题」。  
- **端口占用**：若 `9092` 被占用，可改 compose 映射，但需同步修改所有客户端 bootstrap。

---

## 8. 端到端检查清单（打印贴在显示器旁）

1. `docker compose ps` 显示 **running**。  
2. `kafka-broker-api-versions` **无异常栈**。  
3. `kafka-topics --list` 能看到 **目标 Topic**。  
4. consumer **能读到** producer 发送的字符串。  
5. `--describe` 中 **Leader** 为 **有效 broker id**。  

若第 4 步失败：先查 **Topic 名**、**ACL**（若启用）、**consumer 组位移**（是否从 earliest 读）。若第 5 步异常：查 **controller 日志**与 **broker 进程**（第 10 章）。

---

## 9. 与 Java 示例的关系

本章以 **CLI** 为主；**Java** 路径在 [第 5 章](<第 5 章：Producer 基础——发送、Key、Headers.md>) 与 [第 6 章](<第 6 章：Consumer 基础——poll、提交位移.md>)。CLI 与 Java **共用同一 bootstrap**，便于对照：CLI 能通而 Java 不通时，优先怀疑 **Java 客户端版本**、**序列化器**、**安全参数** 与 **DNS/代理**。

---

## 10. 小结

本地第一条消息是 **信心建立** 的第一步；更重要的是建立 **「describe 与日志」** 的习惯，让后续排障有参照。下一章（若已学第 3 章）请继续深入 **Key 与分区**；若按章节顺序，请进入 **第 3 章**。

---

## 11. 多终端协作演示（内训脚本）

**讲师**：终端 A 开 consumer（`--from-beginning`），终端 B 开 producer，观众应看到 **实时打印**。随后讲师故意 **输错 Topic**，让观众观察 **无输出** 与 **错误日志** 的差异；再纠正 Topic，强调 **「无输出不等于无错误」**——有时是 **缓冲**、有时是 **ACL**、有时是 **消费到末尾**。  
**学员**：轮流在 producer 输入一行 **带业务含义** 的字符串（如 `orderId=...`），并在 consumer 侧指出 **分区号**（若 CLI 未打印，可换 Java 示例）。  
**收尾**：讲师用 `kafka-consumer-groups.sh` 预告 **消费者组**（第 7、8 章），不要求当场理解，只建立「组与位移」名词印象。

---

## 12. 与容器资源相关

若笔记本内存紧张，可适当 **降低 JVM 堆**（镜像若支持）或减少 **其他容器**；但不要在未读文档的情况下随意改 **KRaft** 与 **listener**——那会把「资源问题」伪装成「协议问题」。资源与 JVM 高级调优见第 33 章。

---

## 13. 日志在哪里看（入门版）

- **容器日志**：`docker logs kafka-column-broker`（名称以 compose 为准），关注 **ERROR**、**FATAL**、**Started**。  
- **Broker 日志路径**：镜像内常见 `/opt/kafka/logs` 或等价路径；入门阶段以 **docker logs** 为主即可。  
- **客户端**：Java 侧打开 **org.apache.kafka** 相关 **DEBUG** 需谨慎，先以 **WARN/ERROR** 为主（第 10 章系统化）。

---

## 14. 与团队规范对齐的检查点

- Topic 命名：**环境前缀**（`dev.`/`stg.`）、**业务域**、**版本后缀** 是否统一？  
- 是否禁止 **生产直连** 本地 compose？（常应禁止。）  
- 是否要求 **最小示例** 进入内部知识库？本章命令块可直接粘贴为模板。

---

## 15. 练习：从零排障一次（刻意练习）

1. 故意把 compose 里 **端口映射**改成错误值，启动后记录 **现象**（连接拒绝/超时）。  
2. 改回正确端口，但在 producer 里写错 **Topic 名**，记录 consumer **无输出**时的排查路径。  
3. 恢复 Topic 名，把 consumer 加上 **--group** 固定组 id，发送消息后 **重启 consumer**，观察是否仍能从 **已提交位移** 继续（衔接第 8 章）。  

刻意练习的目的不是「背命令」，而是形成 **检查顺序**：**网络 → 元数据 → Topic → 组位移 → ACL**。

---

## 16. 与 macOS / Linux 的差异

- **文件权限**：Linux 上 Docker 权限问题相对少；macOS 需注意 **文件共享路径** 与 **性能**。  
- **端口**：`localhost` 解析问题较少；若用 **远程 Docker**，bootstrap 需改为 **宿主机 IP**。  
- **脚本**：路径分隔符统一用 **`/`** 写在文档中，减少 Windows 特例。

---

## 17. 本章交付物检查表

- [ ] compose 能启动且 **broker-api-versions** 成功  
- [ ] 已创建 **至少一个** Topic 并完成 **收发**  
- [ ] 已保存 **describe** 输出片段  
- [ ] 已记录 **本机 bootstrap 与容器名**  

四项打勾后，可进入第 3 章或继续巩固第 1 章术语。

---

## 18. 与远程 Docker / 云开发环境

部分公司使用 **远程 Docker** 或 **云 DevBox**：此时 `localhost:9092` 可能指向 **错误主机**。请把 **bootstrap** 改为 **可路由的 DNS/IP**，并确认 **安全组/防火墙** 放行；若使用 **SSH 隧道**，需同时把 **隧道端口** 与 **advertised listener** 策略对齐，否则会出现「容器内能通、笔记本不通」的半截现象。遇到此类情况，先向平台团队确认 **标准连接串**，再改 compose，而不是反复重装镜像。

---

## 19. 与「第一条消息」无关但必须知道的禁令

- **生产集群** 上随意 **create/delete topic**、**reset offset** 往往被 ACL 禁止；应用需走 **工单** 或 **GitOps**。  
- **共享集群** 上不要用 **`test`**、**`tmp`** 这类无命名空间 Topic 名，避免污染他人监控。  
- **压测** 不要用默认 **大消息** 与 **极高 QPS** 直接打生产；先在隔离环境（第 32 章）验证。

---

## 20. 版本冻结与升级提示

专栏冻结 **Broker 与客户端 3.7.0**（见 [WRITING_SPEC.md](../WRITING_SPEC.md)）。若你使用更高版本，CLI 子命令与默认值可能略有差异；升级时请对照 **Release Notes** 中的 **KIP** 与 **弃用项**。入门阶段不要混用 **大版本相差过多** 的客户端与 Broker，以免遇到 **协议不兼容** 却误以为是网络问题。

---

## 21. 与 IDE / 插件

可在 IDE 中保存 **HTTP 无效** 的「运行配置」：一键执行 `docker compose up` 与 `mvn compile exec:java`（后续章节）。插件与具体 IDE 无关，但建议把 **bootstrap** 写成 **环境变量**，避免硬编码到代码里提交到仓库。

---

## 22. 本章复盘三问

1. 你本机的 **bootstrap** 字符串是什么？能否在 5 秒内写出来？  
2. 你创建的 **Topic** 分区数是多少？为什么选这个数？  
3. 若 consumer 看不到消息，你的 **前三步排查** 是什么？  

三问都能回答，本章与第 2 章目标达成；否则回到 **第 8～11 节检查清单** 重走一遍。

---

## 23. 与后续章节的明确跳转

- 已跑通 CLI → 第 3 章 **Key/分区**；第 5 章 **Producer**；第 6 章 **Consumer**。  
- 若卡在 compose → 先解决 **Docker/网络**，不要跳读高级章节。  
- 若已能写 Java → 可把本章 CLI 命令与 **Java 日志**对照，建立「同一 bootstrap 两套入口」的信心。

---

## 24. 备份你的命令历史

建议把 **成功跑通** 的完整命令序列导出到团队文档（脱敏后），包括：**compose 版本**、**镜像 tag**、**Topic 名**、**是否使用 `--from-beginning`**。半年后再回头看，能节省大量「我当时怎么配的」时间；这也是 **运维可交接** 的最低要求。

---

## 25. 本章最后一句话

**能稳定复现的第一条消息，比一次偶然成功更有价值**——请把「复现步骤」当作本章真正的交付物。

---

## 26. 与课堂演示的备用方案

若现场网络受限无法拉镜像，可提前准备 **离线镜像包** 或 **预装环境的 U 盘**；若学员机器无法安装 Docker，可改用 **讲师投屏 + 共享只读集群账号**，但需提前测试 **ACL** 与 **Topic 权限**。教学场景下，**稳定性优先于最新特性**。讲师应准备 **一页 FAQ** 对应「连不上」「无输出」「权限拒绝」三类最高频问题，并在课前发到学员群，降低现场排队排障时间，把精力放在理解 Kafka 行为上即可。祝顺利。
