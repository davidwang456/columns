# 第 32 章：SQL 日志与性能分析（慢 SQL 意识）

示例模块：`mybatis-plus-sample-performance-analysis`。数据源使用 **P6Spy** 驱动包装 JDBC，便于在日志中观察实际 SQL 与耗时（具体格式以依赖版本为准）。

## 1）项目背景

分页、多租户、乐观锁等插件都会在运行期改写 SQL。线上一旦出现**慢查询**或**结果集异常**，排障的第一步往往是：**看到真正下发到数据库的语句与参数**。仅靠 MyBatis 默认日志有时不够直观；团队常引入 **p6spy**、数据源代理或 APM，在**可接受的开销**下把 SQL 文本与耗时落到日志或可观测平台。

**痛点放大**：若从不观察 SQL，容易在开发环境「小数据一切正常」，上线后才发现**全表扫描、缺索引、count 与 list 不一致**。MP 与插件链增加了「最终 SQL」与手写 XML 之间的**距离**，更需要**可观测**补回来。

**本章目标**：跑通 performance-analysis 示例；理解「性能分析」与业务功能解耦、多在**测试/预发**开启；建立与第 12～13 章分页 count 成本联想的意识。

## 2）项目设计：小胖、小白与大师的对话

**小胖**：我把日志级别调到 DEBUG，不就看见 SQL 了吗？

**大师**：DEBUG 能看到 MyBatis 绑定的语句，但**跨层代理、驱动包装**后的最终文本，有时需要 p6spy 这类工具对齐到 JDBC 层；另外生产全开 DEBUG 日志量会炸。

**技术映射**：**MyBatis 日志** ≈ 后厨监控；**JDBC 代理** ≈ 端到餐桌前的最后一道质检。

---

**小白**：p6spy 在生产能开吗？

**大师**：一般**预发/抽样**开启；生产全量要评估 IO 与脱敏；敏感列需脱敏或禁止打印参数。

**小白**：这和 MP 有啥关系？

**大师**：MP 插件改写了 SQL，**肉眼看到的 Mapper 方法名**与**最终 SQL**可能差一截；性能分析要对着**最终 SQL**做 `EXPLAIN`。

**本章金句**：优化 SQL 前，先确认你盯的是**进数据库的那一句**。

## 3）项目实战

**环境准备**

- 模块：`mybatis-plus-sample-performance-analysis`。
- `application.yml` 使用 `com.p6spy.engine.spy.P6SpyDriver` 与 `jdbc:p6spy:h2:mem:test`（见模块内配置）。

**步骤 1：阅读测试用例**

目标：理解示例仅调用 `selectList` 触发一次查询，用于配合日志/代理观察。

```13:22:d:\software\workspace\mybatis-plus\mybatis-plus-samples\mybatis-plus-sample-performance-analysis\src\test\java\com\baomidou\samples\performance\PerformanceTest.java
@SpringBootTest
public class PerformanceTest {

    @Autowired
    private StudentMapper studentMapper;

    @Test
    public void test(){
        studentMapper.selectList(new QueryWrapper<>());
    }

}
```

**运行结果**：测试通过；控制台或 spy 日志中出现实际 SQL（依本地 p6spy 配置）。

**可能遇到的坑**：未引入 p6spy 依赖或驱动类名拼写错误导致启动失败；H2 内存库与生产方言差异——优化结论需在生产库上复验。

**验证命令**：

```bash
cd mybatis-plus-samples
mvn -pl mybatis-plus-sample-performance-analysis -am test
```

**完整代码清单**：`mybatis-plus-samples/mybatis-plus-sample-performance-analysis/`。

## 4）项目总结

| 优点 | 缺点 / 边界 |
|------|-------------|
| 与具体业务代码解耦，专注观察 SQL | 代理层有额外开销，不宜无差别全量开 |
| 便于与分页 count 慢查询对照 | 需配合索引与执行计划，不能只盯日志 |
| 可接入团队统一日志规范 | 敏感数据需脱敏与权限管控 |

**适用场景**：联调阶段验证 MP 生成/改写后的 SQL；预发环境对比升级 MP 版本前后的语句差异。

**不适用场景**：已用 APM 统一采集且禁止重复埋点；对延迟极敏感的金融核心链路（需专项方案）。

**注意事项**：p6spy 版本与 Spring Boot、JDK 兼容性；日志磁盘与 ELK 成本。

**常见踩坑（案例化）**

1. **现象**：日志里 SQL 完整、数据库却慢。**根因**：统计信息与索引在预发与生产不一致。**处理**：在生产只读副本上 `EXPLAIN ANALYZE`。
2. **现象**：升级 MP 后同样接口变慢。**根因**：拦截器顺序或 jsqlparser 行为变化。**处理**：对比两条最终 SQL 与执行计划。
3. **现象**：开发环境极快、生产 count 超时。**根因**：大表无合适索引，分页 count 全表扫描。**处理**：索引与条件重写（见第 13 章）。

**思考题**

1. 若同时开启 MyBatis DEBUG 与 p6spy，如何避免日志重复与体积翻倍？
2. 分页插件产生的 count 语句，如何在日志中快速与 list 语句配对？

**课后动作**：[LABS_CHECKLIST.md](LABS_CHECKLIST.md) 第 32 章；可与第 33 章连续完成。
