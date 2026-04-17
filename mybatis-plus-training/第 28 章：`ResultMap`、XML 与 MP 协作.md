# 第 28 章：`ResultMap`、XML 与 MP 协作

示例模块：`mybatis-plus-sample-resultmap`。

## 1）项目背景

复杂列映射、嵌套对象、鉴别器等仍需 **XML `resultMap`**。MP 负责单表 CRUD 与插件；**关联与嵌套结果**多在 XML 中声明，与 `BaseMapper` 共存于同一 Mapper 接口。

**痛点放大**：列别名与 `resultMap` 属性不一致会导致**部分字段恒为 null**；升级 MyBatis 后类型处理器行为变化需回归。

**本章目标**：跑通 `ResultmapTest`；阅读模块 `mapper/*.xml` 中嵌套映射；能说明何时不用 Wrapper 硬凑。

## 2）项目设计：小胖、小白与大师的对话

**小胖**：都 2026 了还写 XML？

**大师**：**复杂映射**时 XML 往往最可读；Wrapper 硬凑才是难维护。

**技术映射**：**resultMap** ≈ 装配说明书；**Wrapper** ≈ 标准件拼装。

---

**小白**：MP 生成的 SQL 和 XML 会冲突吗？

**大师**：**方法 id 不重复**即可；同一 Mapper 接口可并存 MP 注入方法与自定义 XML。

**本章金句**：**可读 SQL** 优先于「全 Wrapper 炫技」。

## 3）项目实战

**环境准备**：`mvn -pl mybatis-plus-sample-resultmap -am test`。

**步骤 1：嵌套查询链路**

```32:43:d:\software\workspace\mybatis-plus\mybatis-plus-samples\mybatis-plus-sample-resultmap\src\test\java\com\baomidou\mybatisplus\samples\resultmap\ResultmapTest.java
    @Test
    void t_c() {
        final Child child = childMapper.selectLinkById(1L);
        log.info("child: {}", child);
        assertThat(child).isNotNull();
        final Man laoHan = child.getLaoHan();
        assertThat(laoHan).isNotNull();
        assertThat(laoHan.getName()).isNotBlank();
        final Woman laoMa = child.getLaoMa();
        assertThat(laoMa).isNotNull();
        assertThat(laoMa.getName()).isNotBlank();
    }
```

**步骤 2**：打开同模块 `mapper` 下 XML，对照 `resultMap` 与列别名。

**验证命令**：`mvn -pl mybatis-plus-sample-resultmap -am test`

**完整代码清单**：`mybatis-plus-samples/mybatis-plus-sample-resultmap/`

## 4）项目总结

| 优点 | 缺点 / 边界 |
|------|-------------|
| 表达力强 | XML 与重构工具链弱于 Java |
| 与 MP 单表互补 | 深层嵌套影响性能 |

**适用场景**：复杂嵌套、鉴别器、数据库特有类型。

**不适用场景**：纯单表 CRUD（优先 Wrapper）。

**注意事项**：`mapper-locations` 与打包进 jar；与第 27 章 N+1 结合审视。

**常见踩坑（案例化）**

1. **现象**：嵌套对象为 null。**根因**：列别名不匹配。**处理**：对齐 SQL 与 map。
2. **现象**：打包后找不到 XML。**根因**：资源未进 jar。**处理**：Maven resources。
3. **现象**：逻辑删在关联 SQL 遗漏。**根因**：手写 SQL 未带条件。**处理**：规范与审查。

**思考题**

1. `autoResultMap` 与 XML `resultMap` 如何分工？
2. 何时将嵌套改为宽表或视图？

**课后动作**：[LABS_CHECKLIST.md](LABS_CHECKLIST.md) 第 28 章。
