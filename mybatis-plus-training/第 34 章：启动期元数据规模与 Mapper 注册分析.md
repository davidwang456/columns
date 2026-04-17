# 第 34 章：启动期元数据规模与 Mapper 注册分析

示例模块：`mybatis-plus-startup-analysis`。通过 `GeneratorCode` **批量生成**大量实体/Mapper 代码，再运行 `StartupAnalysisApplication` 打印 **Mapper 数量**与 **MappedStatement 数量**，用于感受「元数据规模」对启动时间与内存的影响。

## 1）项目背景

大型单体或代码生成器一键生成数百张表的 Mapper 时，MyBatis **Configuration** 会在启动期解析 XML、注册 `MappedStatement`。数量上来后，**冷启动变慢**、**调试时 IDEA 索引卡顿**都会被感知。本章不是教你怎么写业务，而是建立**数量级意识**：插件链、多数据源、重复扫描包，都会放大启动成本。

**痛点放大**：若把「生成代码」当万能药而不做模块拆分，会出现**一个 SqlSessionFactory 扛所有表**；与 MP 插件组合后，问题更难拆。运维问「为什么这个服务启动要三分钟」，答案往往在这条链上。

**本章目标**：理解 `StartupAnalysisApplication` 输出含义；知道 `GeneratorCode` 用于**压测代码规模**而非生产逻辑；能将结论与「按域拆库/拆模块」架构讨论对接。

## 2）项目设计：小胖、小白与大师的对话

**小胖**：我代码生成器一下生成一千张表对应的 Mapper，启动慢一点怎么了？咖啡多泡一会。

**大师**：启动慢只是表象，背后是 **Configuration 里对象数量**与**类路径扫描成本**。更麻烦的是变更面：一千个 Mapper 任何一个 XML 写错，排查范围都巨大。

**技术映射**：**MappedStatement 数量** ≈ 菜谱卡片张数；卡片越多，后厨翻牌越慢。

---

**小白**：这个示例里的 `TABLE_SIZE = 1000` 是干啥的？

**大师**：用生成器**模拟**一千张表的结构，压出「注册点数量」；让你在不接真实千库的情况下体验规模。真实环境要配合分库分表与领域拆分，而不是单 JVM 扛所有元数据。

**本章金句**：**生成器省的是打字时间，不是架构分区责任。**

## 3）项目实战

**环境准备**

- 模块：`mybatis-plus-startup-analysis`。
- **先用** `test` 目录下 `GeneratorCode` 生成所需测试代码（路径与表数量见源码常量），再运行主类（见类注释）。

**步骤 1：阅读启动主类**

目标：打印 `MapperRegistry` 与 `MappedStatements` 规模。

```30:35:d:\software\workspace\mybatis-plus\mybatis-plus-samples\mybatis-plus-startup-analysis\src\main\java\com\baomidou\StartupAnalysisApplication.java
    @Override
    public void run(String... args) throws Exception {
        Configuration configuration = sqlSessionFactory.getConfiguration();
        System.out.println("注册Mapper数量:" + configuration.getMapperRegistry().getMappers().size());
        System.out.println("注册MappedStatements数量:" + configuration.getMappedStatements().size());
    }
```

**步骤 2：阅读生成器入口**

目标：理解 `MockTable` 如何模拟大量表与字段（`TABLE_SIZE`、`COLUMN_SIZE`）。

见 `GeneratorCode.java`（包 `com.baomidou.mybatisplus` 下），输出目录指向本模块 `src/main/java`（运行前确认工作目录，避免覆盖非预期路径）。

**运行结果（预期）**：控制台输出两行数量统计；具体数值与是否先运行生成器有关。

**可能遇到的坑**：生成器覆盖本地未提交代码——运行前提交 git 或改输出目录；Windows 路径与 `user.dir` 与类注释中的反斜杠。

**验证命令**：以「运行 `GeneratorCode#main` + 运行 `StartupAnalysisApplication`」为主，单元测试非必须。

**完整代码清单**：`mybatis-plus-samples/mybatis-plus-startup-analysis/`。

## 4）项目总结

| 优点 | 缺点 / 边界 |
|------|-------------|
| 直观量化 Mapper/Statement 规模 | 模拟数据不等价真实 SQL 复杂度 |
| 与拆分微服务、分模块的讨论衔接 | 仅看数量不看单语句质量仍会踩坑 |
| 可对比升级 MP 前后启动差异 | 生成代码污染工作区需自律 |

**适用场景**：巨型遗留单体启动优化前评估；代码生成规范评审；与架构师对齐「领域边界」。

**不适用场景**：无生成器、Mapper 极少的小服务——收益有限。

**注意事项**：生成后及时清理实验目录；不要在生产仓库随意提交生成的大量文件。

**常见踩坑（案例化）**

1. **现象**：本地启动快、容器里慢。**根因**：CPU/IO 限制与类加载差异。**处理**：基线对比与镜像层优化。
2. **现象**：MappedStatement 数量远大于预期。**根因**：XML 重复扫描或多数据源重复加载。**处理**：检查 `mapper-locations` 与模块边界。
3. **现象**：生成器跑完编译失败。**根因**：包名与目录不一致。**处理**：对照生成器 `global` 配置与包结构。

**思考题**

1. `MappedStatements` 数量与「业务接口数」是否一一对应？为什么？
2. 若使用动态表名插件，元数据规模与启动期行为会受何影响？（提示：第 25 章。）

**课后动作**：[LABS_CHECKLIST.md](LABS_CHECKLIST.md) 第 34 章。
