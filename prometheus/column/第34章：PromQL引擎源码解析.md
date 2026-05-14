# 第34章：PromQL引擎源码解析

## 1. 项目背景

某公司监控了10万个服务实例。运维小王日常用 `rate(http_requests_total[5m])` 查请求速率，200ms出结果，一切岁月静好。直到某天老板要看大盘——他写了一条带子查询+多维度聚合的复杂PromQL，查询直接超时（默认2分钟），Prometheus CPU飙到100%。他困惑：PromQL看似就是"函数调用+过滤"，为什么有的飞快、有的直接打挂实例？更诡异的是，同样的查询在Grafana定时刷新时很快，API调用却每次都要重新算。

这背后是PromQL引擎的核心机制。PromQL引擎本质上是一个mini数据库查询引擎——有完整的词法分析器（Lexer）、语法分析器（Parser，生成AST）、查询计划（Plan）和执行引擎（Execute）。每条PromQL都要走完这四个阶段。理解执行计划（何时走Index Scan、何时Full Scan、并行查询策略、批量加载优化），才能写出高效查询，诊断慢查询根因。本章追踪一条PromQL从文本到结果的完整旅程。

## 2. 剧本式交锋对话

**小胖**："大师救命！我写了个 `sum(rate(container_cpu[5m])) by (pod)` 套两层子查询，Prometheus直接OOM了。"

**大师**："你的查询是文本。Prometheus不认识文本。它在背后做了四件事：**Lex → Parse → Plan → Execute**。"

**小白**："Lex是什么？"

**大师**："词法分析器。把 `rate(http_requests_total{job='node'}[5m])` 切成Token：`rate` 识别为 `IDENTIFIER`，`(` 为 `LEFT_PAREN`，`5m` 为 `DURATION`（自动转成300000000000纳秒）。一节一节嚼碎喂给Parser。"

**小白**："Parser呢？"

**大师**："Parser把Token拼成AST树。你的 `rate(xxx[5m])` 在AST里长这样："

```
Call{Func: "rate", Args:[
  MatrixSelector{
    VectorSelector{Name:"http_requests_total", LabelMatchers:[...]},
    Range:5m
  }
]}
```

**大师**："注意层级关系：`rate`是Call节点，`[5m]`是MatrixSelector节点（Range Vector），指标选择器是VectorSelector节点。MatrixSelector包裹VectorSelector，Call又包裹MatrixSelector——这棵树直接决定执行顺序。"

**小胖**："Engine怎么执行？"

**大师**："递归遍历。`evaluator.eval()` 是一个巨大的switch：遇到VectorSelector去TSDB查当前数据；遇到MatrixSelector查一段窗口数据；遇到Call先递归求值参数再调函数体；遇到BinaryExpr递归求值左右子树再做运算。每层返回一个Vector，上层拿底层结果做自己的计算——像Excel公式，每个cell依赖其他cell。"

**小胖**："那为啥慢了？"

**大师**："两个原因。一是I/O密集——95%时间不在计算而在从TSDB读chunk数据，窗口越大数据越多。二是子查询就是'查询中的查询'——每层子查询独立做完整时间范围求值，两层就是指数级放大。"

**小白**："为什么Grafana刷新快？"

**大师**："TSDB有chunk缓存和index缓存。Grafana定时刷新同一条查询基本全命中缓存。API每次换时间参数就失效了。更重要的是，Engine在Plan阶段做了 `populateSeries` 优化——遍历AST预提取所有MatrixSelector，把它们的range vector一次性从TSDB批量加载。比如 `rate(a[5m]) + rate(b[5m])`，优化后一次批量查询，不优化两次独立查询。"

**小白**："函数计算能下推到TSDB吗？"

**大师**："目前没有。所有函数计算都在内存中完成，TSDB只返回原始采样点。这意味着窗口越大，内存压力越大。"

## 3. 项目实战

### 环境准备

关注核心文件：
- `promql/engine.go` — 引擎核心，exec主流程和evaluator
- `promql/parser/lex.go` — 词法分析器
- `promql/parser/parse.go` — 解析器入口
- `promql/parser/ast.go` — 全部AST节点定义

### 步骤1：追踪Lexer词法分析

打开 `promql/parser/lex.go`（第297行），`Lexer`结构体采用"状态机+函数式"模式：`stateFn` 是 `func(*Lexer) stateFn`，每个状态函数扫描并发射一个Token后返回下一个状态。

对于 `rate(http_requests_total{job="node"}[5m])`，Token流为：

```
[IDENTIFIER("rate"), LEFT_PAREN,
 IDENTIFIER("http_requests_total"), LEFT_BRACE,
 IDENTIFIER("job"), EQL, STRING("node"),
 RIGHT_BRACE, LEFT_BRACKET, DURATION(5m), RIGHT_BRACKET,
 RIGHT_PAREN, EOF]
```

核心机制：字母序列先查关键字表（`key` map），匹配到就用关键字ItemType，否则用普通IDENTIFIER。Duration `5m` 在 `lexDuration` 内被解析为 `5 * time.Minute`。调试时可在 `parse.go:70` 的 `ParseExpr` 入口打印Token流。

### 步骤2：理解Parser生成的AST

打开 `promql/parser/ast.go`。所有节点实现 `Node` 接口（`String()`/`Pretty()`/`PositionRange()`），表达式额外实现 `Type() ValueType`。核心节点类型：

```go
type Call struct {             // rate(...), sum(...)
    Func *Function
    Args Expressions
}
type MatrixSelector struct {   // http_req[5m]
    VectorSelector Expr
    Range          time.Duration
}
type VectorSelector struct {   // http_req{job="n"}
    Name          string
    LabelMatchers []*labels.Matcher
    Series        []storage.Series  // Plan阶段填充
}
type BinaryExpr struct {       // a + b, a > bool b
    Op       ItemType
    LHS, RHS Expr
}
type SubqueryExpr struct {     // expr[5m:1m]
    Expr  Expr
    Range time.Duration
    Step  time.Duration
}
type AggregateExpr struct {    // sum(...) by (l)
    Op       ItemType
    Expr     Expr
    Grouping []string
    Without  bool
}
```

解析入口 `parser.parseExpr()`（`parse.go:196`）调用 `p.parseGenerated(START_EXPRESSION)`，驱动yacc生成的LALR解析器逐Token归约为AST节点。语法错误（如缺括号）通过 `ParseErrors` 返回，但信息有时不友好——缺括号可能报 `unexpected $end`。

### 步骤3：追踪Engine的查询执行

入口 `Engine.exec()`（`engine.go:678`）带超时context和并发排队后，分发到 `execEvalStmt()`（第778行）。核心流程：

```go
func (ng *Engine) execEvalStmt(...) (...) {
    // Plan阶段：遍历AST，批量从TSDB获取所有SeriesSet
    ng.populateSeries(ctxPrepare, querier, s)

    // Execute阶段：创建evaluator，递归求值AST
    evaluator := &evaluator{...}
    val, err := evaluator.Eval(ctx, s.Expr)
}
```

`populateSeries()`（`engine.go:1036`）是核心优化：

```go
func (ng *Engine) populateSeries(...) {
    parser.Inspect(s.Expr, func(node parser.Node, path []parser.Node) error {
        switch n := node.(type) {
        case *parser.VectorSelector:
            hints := &storage.SelectHints{
                Start: start, End: end, Step: step, Range: evalRange,
                Func: extractFuncFromPath(path),
            }
            // 批量获取SeriesSet（不展开数据）
            n.UnexpandedSeriesSet = querier.Select(ctx, false, hints, n.LabelMatchers...)
        case *parser.MatrixSelector:
            evalRange = n.Range // 传递给内层VectorSelector
        }
        return nil
    })
}
```

`SelectHints` 携带三个优化信号：**Func**（上层函数名，如rate）、**Range**（时间窗口，TSDB据此裁剪chunk）、**Step+Grouping**（Range Query下采样优化）。

Evaluator的 `eval()`（第1916行）递归求值：

```go
func (ev *evaluator) eval(ctx context.Context, expr parser.Expr) (...) {
    switch e := expr.(type) {
    case *parser.VectorSelector:
        return ev.vectorSelector(e)     // 从TSDB查当前时刻数据
    case *parser.MatrixSelector:
        return ev.matrixSelector(e)     // 查窗口数据
    case *parser.Call:
        // 预展开MatrixSelector的SeriesSet → []storage.Series
        // 对每个Series逐chunk迭代，调用函数体
        for _, s := range series {
            it := storage.NewBuffer(selRange)
            it.Reset(s.Iterator(nil))
            // 遍历采样点 → 传入函数体计算
        }
    case *parser.BinaryExpr:
        lhs := ev.eval(ctx, e.LHS)
        rhs := ev.eval(ctx, e.RHS)
        return ev.binOp(e.Op, lhs, rhs)
    case *parser.SubqueryExpr:
        return ev.evalSubquery(ctx, e)   // 逐step独立求值
    }
}
```

Call节点的"逐Series迭代"模式内存友好——当前Series处理完即释放，下一个Series复用buffer。但数据总量 = 命中Series数 × 窗口内采样点数，决定了查询代价。

### 步骤4：添加耗时监控

Prometheus自带完善的引擎指标：

- `prometheus_engine_query_preparation_time_seconds` — Plan阶段耗时
- `prometheus_engine_inner_eval_time_seconds` — eval()执行耗时
- `prometheus_engine_result_sort_time_seconds` — 结果排序耗时
- `prometheus_engine_query_samples_total` — 每次查询加载的采样点数

通过这些指标可发现：90%以上的时间花在 `matrixSelector` 从TSDB读chunk，而非PromQL计算本身。

### 可能遇到的坑

1. **Parser错误不直观**：缺括号报 `unexpected $end`，先在简单查询上验证语法。
2. **maxSamples限制**：默认5000万（`EngineOpts.MaxSamples`），超限返回 `ErrTooManySamples`。需根据内存调整——每个采样点约24字节，5000万≈1.2GB。
3. **深度子查询可能栈溢出**：`a[1h:1m][30m:1m][5m:1m]` 递归深度增大，极端场景需注意。
4. **ActiveQueryTracker**：记录运行中的查询，崩溃重启后可定位肇事查询。`GetMaxConcurrent()` 控制并行查询上限。

### 测试验证

- 执行 `rate(prometheus_http_requests_total[5m])`，耗时100ms以内
- 执行 `up`，耗时10ms以内（纯VectorSelector，无range数据拉取）
- 执行 `count({__name__=~".+"})`，"全表扫描"——耗时与实例数成正比

## 4. 项目总结

### 执行四阶段流程

```
rate(http_requests_total{job="node"}[5m])
    │
    ▼ [Lex] lex.go: 状态机逐字符扫描 → Token流
    ▼ [Parse] generated_parser.y.go: LALR归约 → AST树
    ▼ [Plan] engine.go:populateSeries(): 预提取SeriesSet+SelectHints
    ▼ [Exec] engine.go:evaluator.eval(): 递归遍历AST，逐Series查chunk计算
    → Vector/Matrix
```

### AST节点速查

| 节点 | 语法 | 职责 |
|------|------|------|
| VectorSelector | `foo{job="x"}` | TSDB查当前时刻 |
| MatrixSelector | `foo[5m]` | TSDB查时间窗口 |
| Call | `rate(...)` | 调用函数体 |
| AggregateExpr | `sum(...) by (l)` | 分组聚合 |
| BinaryExpr | `a + b` / `a and b` | 二元运算/集合 |
| SubqueryExpr | `rate(foo[5m])[1h:1m]` | 嵌套时间范围求值 |

### 查询优化策略

| 策略 | 机制 | 效果 |
|------|------|------|
| MatrixSelector预加载 | populateSeries批量查TSDB | 多个range vector减少锁竞争 |
| SelectHints.Func | 传函数名给TSDB | TSDB跳过无关chunk |
| SelectHints.Range | 传窗口大小给TSDB | 裁剪chunk，减少I/O |
| Label Index | 倒排索引定位Series | 避免全表扫描 |
| 逐Series迭代 | iterator模式 | 内存只在当前Series分配 |

### 注意事项

1. 复杂PromQL优先考虑**Recording Rules**预计算，把压力从查询时移到写入时。
2. 查询窗口越大，chunk数据越多。`rate(foo[30d])` 比 `rate(foo[5m])` 可能慢百倍。
3. maxSamples需根据硬件调整，单次查询5000万采样点约需1.2GB内存。
4. 函数下推目前未实现——所有计算在内存完成，`avg_over_time`等也是拉原始数据后算。

### 常见踩坑

**踩坑1**：`rate(foo[5m])[1h:1s]` 意味着1小时内每秒算一次rate，3600次函数调用，每次拉5分钟数据——查询直接超时。子查询step不宜过小。

**踩坑2**：`rate(a[5m]) or rate(b[5m]) or ...` 连写20个，虽然有预加载优化，但20个MatrixSelector的Series同时展开，每个全量指标内存就爆了。应改用正则：`rate({__name__=~"a|b|c"}[5m])`。

**踩坑3**：`{job=~"node.*"}` 前缀匹配走倒排索引，`{job=~".*node"}` 后缀匹配退化为全扫描。尽量用等值或前缀正则。

### 思考题

1. `sum(rate(foo[5m])) by (bar)` 和 `sum by (bar)(rate(foo[5m]))` 在AST层面和查询性能上有区别吗？
2. 如果要实现PromQL查询缓存，应该在AST求值结果（某时刻的Vector）还是TSDB查询结果（SeriesSet的chunk数据）层面缓存？
