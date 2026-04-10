# 第 4 章：hbase shell——namespace、DDL、describe、count

> 统一模板与检查表：[../00-template-pack.md](../00-template-pack.md)
> 官方文档：[HBase Reference Guide](https://hbase.apache.org/docs)

上一章：[第 3 章](03-第03章-环境搭建-单机伪分布式或Docker.md) | 下一章：[第 5 章](05-第05章-数据模型-RowKey列族Qualifier-Cell时间戳.md)

---

**受众：主【全员】 难度：基础**

### 0）本章路线图（由浅入深）

| 层次 | 你要达成的状态 | 建议用时 |
|------|----------------|----------|
| L1 听懂 | 知道 shell 的定位：运维 / 调试，不是业务主战场 | 15 min |
| L2 能做 | 独立完成 namespace、建表、describe、put/get/scan、预分区演示 | 30～40 min |
| L3 能讲 | 能解释 `count` 代价、`disable` 影响，并给新人演示规范 | 助教级 |

### 开场一分钟（趣味钩子）

`hbase shell` 像**消防演习用的训练梯**：平时爬一爬很健康，但别指望靠它装修整栋楼。生产业务请走 **Java API / 你们封装的 SDK**。课堂上把 shell 练熟，是为了**紧急排障时手不抖**。

### 1）项目背景

- **开发 / 运维**：快速验证表结构、Region、列族属性；DDL 变更前用 shell **dry-run 心智**（先看 `describe`）。
- **测试**：准备数据与清理环境；用例里写清 **namespace**，避免表名撞车。
- **全员**：统一「表名 = namespace:table」口径，减少口头沟通事故。
- **若跳过本章**：后续 Java 实验没有共同语言，排障时不敢动表。

### 2）项目设计（大师 × 小白）

- **小白**：「shell 能写业务逻辑吗？」
- **大师**：「**运维与调试**为主；生产业务应用走 **Java / REST 等 API**。」
- **小白**：「namespace 干嘛用？」
- **大师**：「**多租户隔离**、权限与命名空间，类似逻辑上的 database。」
- **小白**：「表名太长怎么办？」
- **大师**：「长的是**约定问题**；namespace 帮你分组。Qualifier 太长才伤存储与缓存。」
- **小白**：「`count` 为啥卡？」
- **大师**：「大表 `count` 往往要扫；**生产慎用**，用采样或业务计数器。」
- **小白**：「删表前要干啥？」
- **大师**：「通常 **`disable` → `drop`**；中间表不可服务，确认无人依赖。」
- **段子**：小白说「我在生产 scan 了一下没加 LIMIT。」大师：「恭喜，你完成了**分布式春游**。」

### 3）项目实战

**环境**：已启动的 HBase shell（第 3 章环境）。

**任务 1：基础 DDL + 读写（必做）**

```text
create_namespace 'training'
create 'training:orders', { NAME => 'd', VERSIONS => 1 }
describe 'training:orders'
put 'training:orders', 'rk001', 'd:status', 'NEW'
get 'training:orders', 'rk001'
scan 'training:orders', { LIMIT => 10 }
list_namespace_tables 'training'
```

**任务 2：预分区（与第 9 章呼应）**

```text
create 'training:orders_splitted', 'd', SPLITS => ['10','20','30']
```

然后用 `scan 'hbase:meta'` 或 UI 观察 Region 边界（讲师演示即可，避免新手迷航）。

**任务 3：计时小挑战（趣味）**

- 向空表 `put` 100 行，再 `scan` 带 `LIMIT`；对比某位同学误执行全表 `count` 的「心理等待时间」（讲师及时 `Ctrl+C` 救场）。
- **验收**：截图或文本贴出 `describe` 关键行（列族名、VERSIONS）。

### 4）项目总结

- **优点**：上手快，适合课堂演示与紧急排障。
- **缺点**：大表 `count` 很慢；勿在生产大表随意 count / 无界 scan。
- **适用**：培训、紧急排障、验证 DDL。
- **注意**：命令大小写与引号；多租户下表名必须带 namespace。
- **踩坑**：未加 namespace 导致表名冲突；`drop` 前未备份。
- **测试检查项**：用例是否注明使用的 namespace；清理脚本是否 idempotent。

### 5）课堂自测与作业（讲师可选用）

**自测**

1. `disable` 一张表后，业务读写预期是什么？
2. 为什么培训里强调 `scan` 要带 **LIMIT**？
3. `describe` 输出里你最关心哪三项（任意合理三项即可）？

**作业**

- 整理一份「shell 禁术清单」：生产禁止对核心业务表执行的命令（团队自定义）。

---

**返回目录**：[../README.md](../README.md)
