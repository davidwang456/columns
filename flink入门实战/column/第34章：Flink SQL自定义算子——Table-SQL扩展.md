# 第34章：Flink SQL自定义算子——Table/SQL扩展

---

## 1. 项目背景

第14章我们学习了三种UDF（Scalar/Table/Aggregate Function）。但在某些场景下，UDF还不够灵活：

- 需要在SQL中实现窗口TopN的去重逻辑——UDAF无法访问窗口上下文
- 需要在SQL级别控制数据路由（如自定义分区器）——UDF不控制shuffle
- 需要SQL算子级别的性能优化（如自定义的聚合算法比内置的SUM更快）

以上场景需要**自定义SQL算子**——通过扩展Flink Table API的底层接口，在SQL执行计划中注入自己的逻辑。

---

## 2. 项目设计

> 场景：小胖需要在Flink SQL中实现一个"滑动去重"函数——统计每个滑动窗口内的UV，但Flink内置的COUNT DISTINCT在滑动窗口下性能极差。

**大师**：Flink SQL的COUNT DISTINCT是全量精确去重，在滑动窗口（如1h/1min）下每个窗口都维护一个全量Set——60个窗口就是60份Set，内存爆炸。

**小白**：那自定义SQL算子怎么解决？难道能控制SQL执行计划的生成？

**大师**：通过自定义`TableFunction` + `RichFunction`实现的approximate去重可以部分解决。但如果要真正"改写SQL执行计划"，需要用到Flink的**CodeGen（代码生成）** 扩展或自定义**StreamOperator**。

**技术映射：Flink SQL算子的底层 = DataStream API的算子。Flink SQL编译时会将SQL转化为DataStream API的Transformation链。自定义SQL算子的本质 = 跳过SQL编译，直接编写StreamOperator，再通过Table API的`toRetractStream`或`toChangelogStream`集成到SQL环境。**

**小胖**：那具体路径是什么？我需要一个在SQL中可调用的"滑动窗口UV估算"函数。

**大师**：两条路：
1. **UDAF + 自定义状态下推**：在`AggregateFunction`中使用HyperLogLog作为累加器，reduce内存占用
2. **自定义StreamOperator**：完全绕过SQL聚合，直接操作DataStream，再通过`tableEnv.fromDataStream()`注册为SQL表

---

## 3. 项目实战

### 分步实现

#### 步骤1：自定义StreamOperator——滑动窗口近似去重

**目标**：实现一个自定义的StreamOperator，在每个并行子任务中维护HyperLogLog做近似去重。

```java
package com.flink.column.chapter34.operator;

import org.apache.flink.streaming.api.operators.AbstractStreamOperator;
import org.apache.flink.streaming.api.operators.OneInputStreamOperator;
import org.apache.flink.streaming.api.operators.TimestampedCollectorOutput;
import org.apache.flink.streaming.api.watermark.Watermark;
import org.apache.flink.streaming.runtime.streamrecord.StreamRecord;
import org.apache.flink.table.data.RowData;
import org.apache.flink.table.runtime.typeutils.RowDataSerializer;
import org.apache.datasketches.hll.HllSketch;

/**
 * 自定义StreamOperator：滑动窗口近似UV去重
 * 每个并行实例维护一个HLL Sketch
 */
public class SlidingUvOperator extends AbstractStreamOperator<RowData>
        implements OneInputStreamOperator<RowData, RowData> {

    private final int windowSizeMinutes;
    private final int slideMinutes;
    private transient HllSketch sketch;
    private transient long lastWindowEnd = 0;

    public SlidingUvOperator(int windowSizeMinutes, int slideMinutes) {
        this.windowSizeMinutes = windowSizeMinutes;
        this.slideMinutes = slideMinutes;
    }

    @Override
    public void open() {
        sketch = new HllSketch(12);  // 12 = 4096字节，精度~2%
        lastWindowEnd = System.currentTimeMillis();
    }

    @Override
    public void processElement(StreamRecord<RowData> element) {
        // 获取当前数据的时间戳（假设在RowData的第2个字段）
        long eventTime = element.getValue().getLong(1);

        // 如果超过slide间隔，输出当前窗口的UV估算
        if (eventTime - lastWindowEnd >= slideMinutes * 60_000L) {
            long estimate = sketch.getEstimate();
            // 输出结果RowData
            RowData result = newRowData();
            result.setLong(0, lastWindowEnd);          // 窗口结束时间
            result.setLong(1, estimate);               // UV估算值
            output.collect(element.replace(result));

            // 重置Sketch（开始新窗口）
            sketch.reset();
            lastWindowEnd = eventTime;
        }

        // 更新Sketch——加入userId（假设在RowData的第1个字段）
        String userId = element.getValue().getString(0).toString();
        sketch.update(userId);
    }

    @Override
    public void processWatermark(Watermark mark) {}

    private RowData newRowData() {
        // 创建RowData（取决于Table Schema）
        return new org.apache.flink.table.data.GenericRowData(2);
    }
}
```

**集成到Table Environment**：

```java
// 将自定义Operator生成的DataStream注册为SQL表
DataStream<RowData> uvStream = env
        .fromSource(...)
        .transform("sliding-uv", Types.ROW(...), new SlidingUvOperator(60, 1));

Table uvTable = tableEnv.fromChangelogStream(uvStream);

// 现在可以在SQL中查询uvTable了
tableEnv.sqlQuery("SELECT window_end, uv FROM uvTable WHERE uv > 1000");
```

#### 步骤2：使用CodeGen修改SQL算子行为

**目标**：通过Flink SQL的CodeGen机制，在编译时注入自定义代码。

```java
// Flink SQL的CodeGen机制（代码生成器）
// 在编译SQL时，Flink会为每个算子生成Java代码
// 可以通过自定义CodeGenerator来修改生成的代码

// 关键类：
// org.apache.flink.table.codegen.CodeGeneratorContext
// org.apache.flink.table.codegen.GeneratedExpression
// org.apache.flink.table.runtime.generated.GeneratedCollector

// 示例：在生成的聚合代码中加入自定义累加器逻辑
// 这需要改写Flink的CodeGen模板——不建议在常规开发中使用
// 更实用的方式是：通过Table API的UDF来间接控制算子行为
```

**更实用的方式——通过Table API的自定义算子**：

```java
// 使用DataStream API实现聚合逻辑
// 然后通过 tableEnv.fromDataStream() 注册为SQL表
// 这样既享受了SQL的查询能力，又保留了DataStream的性能控制

Table uvApproxTable = tableEnv.fromDataStream(
        uvStream,
        Schema.newBuilder()
                .column("windowEnd", DataTypes.BIGINT())
                .column("uv", DataTypes.BIGINT())
                .watermark("windowEnd", "windowEnd - INTERVAL '1' SECOND")
                .build()
);
```

#### 步骤3：自定义SQL Operator的官方扩展点

**目标**：了解Flink Table API官方支持的扩展点。

```
// Flink SQL算子扩展的层次（从易到难）：

// 层级1: UDF（标量/表/聚合函数）——第14章
// 适用于：简单函数逻辑

// 层级2: TableFunction + RichFunction + State
// 适用于：需要在UDF中访问State、Timer

// 层级3: 自定义StreamOperator + Table API集成
// 适用于：需要控制数据路由、精确管理State生命周期

// 层级4: CodeGen扩展 + Planner自定义
// 适用于：改写SQL优化器的行为（不推荐——与Flink版本强耦合）

// 层级5: 自定义Catalog + Connector
// 适用于：对接外部存储系统
```

#### 步骤4：RuntimeFilter——SQL级别的自定义过滤优化

**目标**：在SQL执行计划中注入动态过滤条件（Runtime Filter），减少Join的Shuffle数据量。

```sql
-- 标准Join
SELECT * FROM orders o JOIN users u ON o.userId = u.userId;

-- 加入Runtime Filter（需要自定义优化规则）
-- 用probe side的值动态过滤build side
-- 需要实现：CustomFilterRule extends RelOptRule
-- 然后通过Planner注册
```

### 可能遇到的坑

1. **自定义StreamOperator输出RowData的Schema与SQL表Schema不匹配**
   - 根因：RowData的字段顺序、类型、nullable属性必须与Table Schema完全一致
   - 解决：使用`DataType.getLogicalType()`和`RowData.createFieldGetter()`确保类型匹配

2. **从`tableEnv.fromChangelogStream()`创建的Table不支持某些SQL查询**
   - 根因：Changelog Stream不能直接做无界聚合（需要Retraction支持）
   - 解方：将自定义算子输出标记为`RowKind.INSERT`（Append模式），或使用`fromDataStream()`

3. **自定义算子与SQL优化器的执行计划冲突**
   - 根因：SQL优化器可能将自定义算子下推、上拉或合并，破坏你的逻辑
   - 解方：在自定义算子上设置`disableChaining()`；或通过`planner.getPlanner().getRelBuilder()`修改优化规则

---

## 4. 项目总结

### 自定义SQL算子的四种方式

| 方式 | 复杂度 | 灵活性 | 与SQL集成度 | 推荐场景 |
|------|--------|--------|------------|---------|
| UDF | 低 | 中 | 完全集成 | 简单函数逻辑 |
| UDAF+State | 中 | 中高 | 完全集成 | 自定义聚合 |
| 自定义StreamOperator | 高 | 高 | 通过tableEnv集成 | 高级算法逻辑 |
| CodeGen扩展 | 极高 | 最高 | 完全集成 | 改写优化器行为 |

### Table API集成DataStream的方法

| 方法 | 适用于 | 说明 |
|------|-------|------|
| `fromDataStream()` | Append-only流 | 最常用，需要显式定义Schema |
| `fromChangelogStream()` | 有UPDATE/DELETE的流 | 需要RowKind支持 |
| `toDataStream()` | 读取Table结果 | 将Table的结果转回DataStream |
| `toChangelogStream()` | 读取Changelog | 处理Table的变更流 |

### 注意事项
- 自定义StreamOperator需要处理`RowData`——这是Flink内部的二进制行表示，与POJO不同
- SQL优化器可能对你的自定义算子做"意外优化"——如果遇到奇怪的行为，试试在算子链边界处调用`disableChaining()`
- 自定义SQL算子属于高级定制，不建议频繁使用——能用UDF解决的问题，不要上StreamOperator

### 常见踩坑经验

**案例1：自定义算子中的State在作业恢复后丢失**
- 根因：自定义StreamOperator没有正确实现`CheckpointedFunction`接口
- 解方：实现`CheckpointedFunction`的`snapshotState()`和`initializeState()`方法

**案例2：`tableEnv.fromDataStream()`注册的表在SQL查询中报"Encountered 'WINDOW' was expecting one of"**
- 根因：流表的数据类型没有定义rowtime属性，SQL中无法做窗口聚合
- 解方：在Schema定义中添加`.watermark("eventTime", "eventTime - INTERVAL '5' SECOND")`并标记rowtime列

**案例3：自定义CodeGen逻辑在Flink版本升级后不兼容**
- 根因：CodeGen机制在不同Flink版本间变化很大（1.14→1.15→1.18都有变化）
- 解方：使用更稳定的UDF/UDAF API；如果必须用CodeGen，锁定Flink版本

### 优点 & 缺点

| | 自定义SQL算子（StreamOperator+tableEnv集成） | UDF/UDAF标准扩展 |
|------|-----------|-----------|
| **优点1** | 完全控制算子逻辑——自定义State、Timer、数据路由 | 受限于UDF API的能力边界 |
| **优点2** | 可绕过SQL优化器实现极致性能优化 | 性能依赖优化器，不可控 |
| **优点3** | fromDataStream/fromChangelogStream灵活集成 | 完全在SQL执行计划内，集成自然 |
| **缺点1** | 开发复杂——需理解RowData、CheckpointedFunction等底层API | 开发简单，继承UDF基类即可 |
| **缺点2** | SQL优化器可能误优化自定义算子，行为难预测 | 优化器完全兼容 |
| **缺点3** | 版本升级时StreamOperator API可能不兼容 | UDF API跨版本稳定 |

### 适用场景

**典型场景**：
1. SQL中无法表达的复杂运算——自定义聚合算法、滑动去重
2. 极致性能优化——绕过SQL优化器，手写算子级优化
3. 自定义CodeGen注入——修改SQL生成的执行代码
4. 混合DataStream+SQL架构——DataStream逻辑注册为SQL表统一查询

**不适用场景**：
1. 简单UDF即可解决的场景——UDF开发成本低、维护简单
2. 纯SQL用户（BI分析师）——需Java开发能力，不适合SQL-only团队

### 思考题

1. `fromDataStream()`和`fromChangelogStream()`的区别本质是什么？在什么场景下必须使用`fromChangelogStream()`？（提示：参考RowKind的INSERT/UPDATE_BEFORE/UPDATE_AFTER/DELETE语义）

2. 自定义StreamOperator与SQL聚合的性能对比——对于"每分钟的UV统计"，自定义算子（HLL + 滑动窗口）和Flink SQL的COUNT DISTINCT + TUMBLE窗口相比，各自的性能和精度如何？为什么自定义算子可能比SQL快？

---

> **完整代码**：本章完整代码请参考附录或访问 https://github.com/flink-column/flink-practitioner  
> **思考题答案**：见附录文件 `appendix-answers.md`
