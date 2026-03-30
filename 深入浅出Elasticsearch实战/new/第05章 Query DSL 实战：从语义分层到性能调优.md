# 第05章 Query DSL 实战：从语义分层到性能调优

## 1. Query DSL 核心心智模型与实战原则

### 1.1 查询语义的双重本质

Elasticsearch Query DSL 的设计哲学根植于信息检索领域的经典理论，将查询操作明确区分为两个相互独立却又紧密协作的语义层面。**过滤语义（Filtering）** 负责快速判定文档是否满足硬性条件，**评分语义（Scoring）** 负责计算匹配文档的相关性得分以支持排序。这种分离设计使得 ES 能够针对不同类型的操作采用最优的执行策略，从而在功能丰富性和执行效率之间取得平衡。

理解这两种语义的本质差异，是编写高效 Query DSL 的前提。实践中常见的错误包括：将本应作为过滤条件的结构化查询放入 `must` 子句，导致不必要的评分计算；或者将需要相关性排序的文本查询误用为 `filter`，导致结果失去排序意义。正确的做法是根据业务需求的语义类型，选择对应的查询上下文——`filter` 上下文用于过滤语义，`query` 上下文用于评分语义。

#### 1.1.1 过滤语义：精确判定命中与否

过滤语义的核心特征是**二值判断**——文档要么满足条件，要么不满足，不存在中间状态。这类条件在业务逻辑中通常表现为"硬约束"，即不满足则直接排除。典型的过滤条件包括商品类目筛选、价格区间限定、库存状态过滤、发布时间范围等结构化字段。

从实现机制来看，过滤操作直接作用于倒排索引的文档列表（postings list），通过位运算（bitwise operations）快速求交或求并，计算复杂度与结果集大小呈线性关系而非与总文档数相关。更为重要的是，**过滤结果可以被缓存为固定大小的位集合（bitset）**，后续相同条件的查询可直接复用，避免重复的索引扫描。根据官方文档和性能测试数据，合理使用 filter 缓存可使重复查询的响应时间降低 **80% 以上**，在高并发场景下这一优势更为显著 。

过滤语义的另一个关键特性是**不计算相关性得分**。这意味着 filter 子句的执行路径完全绕过了评分计算模块，包括 TF-IDF 或 BM25 等算法的调用、字段长度归一化、词频统计等耗时操作。在实际业务中，一个典型的电商搜索请求可能包含 5-10 个过滤条件，若全部使用 `must` 子句，每个条件都会参与评分计算，造成大量无意义的 CPU 开销。将这些条件迁移至 `filter` 子句后，查询吞吐量通常可提升 **2-5 倍**。

#### 1.1.2 评分语义：计算相关性排序

评分语义解决的是"命中后谁更靠前"的问题，这是搜索引擎区别于传统数据库的核心能力。ES 默认采用 **BM25 算法** 计算相关性得分，该算法综合考虑了词频（term frequency）、逆文档频率（inverse document frequency）和字段长度归一化（field length normalization）三个核心因素。与过滤语义的二值判断不同，评分语义输出的是连续数值，允许对匹配文档进行精细排序。

评分计算的开销显著高于过滤操作。根据 Lucene 的实现细节，每个参与评分的查询子句都需要：扫描倒排索引获取词频信息、查询 norms 数据结构获取字段长度、执行对数运算和乘法运算组合各因子、跨字段聚合最终得分。在复杂查询中，一个文档可能经历数十次评分计算，累积成本不容忽视。因此，**性能优化的核心原则之一是最小化评分参与范围**——仅将真正影响排序逻辑的条件放入 `must` 或 `should`，其余一律归入 `filter`。

#### 1.1.3 业务需求的四层拆解法

将业务需求转化为高效的 Query DSL，需要系统化的拆解方法。基于过滤语义与评分语义的分离原则，可将任意搜索需求拆解为四个层次：

| 层级       | 语义定位      | 典型场景     | 技术实现          | 性能特征           |
|:-------- |:--------- |:-------- |:------------- |:-------------- |
| 第一层：硬过滤  | 必须满足，无评分  | 类目、状态、权限 | `filter` 子句   | **可缓存，O(1)复用** |
| 第二层：核心检索 | 必须满足，参与评分 | 关键词匹配    | `must` 子句     | 不可缓存，需评分       |
| 第三层：偏好加权 | 非必须，提升评分  | 品牌优先、热度  | `should` 子句   | 匹配数影响得分        |
| 第四层：硬性排除 | 必须不满足     | 黑名单、敏感词  | `must_not` 子句 | **性能代价较高**     |

这种四层拆解法的价值在于将模糊的业务需求转化为明确的查询结构，每个层次对应 `bool` 查询的一个子句，实现语义清晰、性能可控、易于调优的查询设计。当业务反馈"搜索结果不准"时，可以快速定位是过滤层漏了数据、核心匹配层语义偏差，还是加权层参数失衡。

### 1.2 查询性能的第一性原理

Elasticsearch 的查询性能从根本上取决于三个因素：**倒排索引的利用效率**、**评分计算的开销控制**、以及**缓存机制的有效利用**。这三个因素相互交织，共同决定了查询的响应时间与系统吞吐量。

#### 1.2.1 倒排索引的利用效率决定查询速度

倒排索引是 ES 查询性能的根本来源。一个优化良好的查询应当最大化利用索引的有序性和压缩特性，最小化需要实时计算的文档集合：

| 查询类型              | 索引利用方式        | 时间复杂度         | 性能特征          |
|:----------------- |:------------- |:------------- |:------------- |
| `term`            | 直接词条查找        | O(1)          | **最优，直接定位**   |
| `terms`           | 多词条合并         | O(n)，n为词条数    | 良好，批量优化       |
| `range`（数值/日期）    | BKD-Tree 范围裁剪 | O(log n + m)  | 良好，与范围宽度相关    |
| `range`（keyword）  | 有序词条扫描        | O(m)，m为范围内词条数 | 一般，依赖数据分布     |
| `wildcard` 前缀通配   | 无法利用索引        | O(N)，N为总词条数   | **劣化，避免生产使用** |
| `must_not` 高选择性排除 | 补集运算          | O(N)，N为总文档数   | **灾难，需特别规避**  |

**索引扫描 vs 跳跃**：Lucene 支持基于跳表（skip list）的快速定位，当查询条件具有高选择性时，可直接跳转至目标文档区间，避免全量扫描。`range` 查询在数值和日期字段上的高效性正源于此。

#### 1.2.2 评分计算的开销与避免策略

评分计算是查询执行路径中的"昂贵操作"。根据性能剖析数据，评分阶段通常占据总查询时间的 **30%-70%**，具体比例取决于查询复杂度。避免不必要的评分是优化的核心杠杆：

| 策略               | 实现方式                       | 适用场景         | 预期收益           |
|:---------------- |:-------------------------- |:------------ |:-------------- |
| **迁移至 filter**   | 将非排序条件从 `must` 移至 `filter` | 结构化过滤条件      | **2-5x 吞吐量提升** |
| `constant_score` | 用固定得分替代动态评分                | 简单 `term` 查询 | 消除评分开销         |
| 禁用 `_score`      | 设置 `"sort": ["_doc"]`      | 无需排序的批量导出    | 跳过完整评分流程       |
| `rescore` 窗口     | 对 Top N 结果二次精排             | 复杂算法仅需应用于头部  | 减少 90%+ 评分计算   |

#### 1.2.3 缓存机制对重复查询的加速作用

ES 维护两级缓存结构以加速重复查询：

| 缓存类型              | 作用范围 | 缓存内容             | 失效触发      | 适用场景       |
|:----------------- |:---- |:---------------- |:--------- |:---------- |
| **Filter Cache**  | 节点级  | `filter` 子句的位集合  | 段合并、索引刷新  | 高频重复过滤条件   |
| **Request Cache** | 分片级  | 完整查询结果（`size=0`） | 索引刷新、显式清除 | 静态数据上的聚合查询 |

Filter Cache 的运作机制：跟踪最近 256 个查询中的过滤条件，出现频率超过阈值的 bitset 被正式缓存。小 segment（<1000 文档或 <3% 总大小）不被缓存，因其合并速度快、缓存价值低 。监控缓存效率可通过 `/_nodes/stats/indices/query_cache` 端点，命中率低于 **50%** 通常提示查询模式或缓存空间配置存在问题。

## 2. 常用查询组件的实战选型与陷阱规避

### 2.1 精确匹配场景：term vs match

`term` 查询与 `match` 查询是 Elasticsearch 中最基础也最常被混淆的两个查询类型。理解它们的本质差异，是避免查询结果异常的第一步。

#### 2.1.1 term查询的适用场景与字段类型要求

`term` 查询执行**倒排索引词条的精确匹配**，不对查询字符串进行任何分析处理，直接与索引中的词项进行比对。这意味着查询字符串必须与索引词项**完全一致**，包括大小写、标点、空格等。

**适用场景与字段类型**：

| 场景       | 字段类型      | 示例                                    | 关键约束      |
|:-------- |:--------- |:------------------------------------- |:--------- |
| ID精确匹配   | `keyword` | `{"term": {"user_id": "U123456"}}`    | 完整匹配，无分词  |
| 状态码匹配    | `keyword` | `{"term": {"status": "published"}}`   | 枚举值，大小写敏感 |
| 标签过滤     | `keyword` | `{"term": {"tags": "elasticsearch"}}` | 标签为完整词项   |
| 数值/日期/布尔 | 原生类型      | `{"term": {"count": 100}}`            | 类型一致，无转换  |

`term` 查询的行为严重依赖字段的映射类型。对于 `text` 类型字段，ES 会在索引时执行分词，原始文本被拆分为多个词条，`term` 查询只能匹配这些词条之一而非完整文本。例如，对 `text` 类型的 `title` 字段执行 `{"term": {"title": "iPhone 15"}}`，实际上是在查找包含精确词条 "iphone" 或 "15" 的文档，而非完整短语 "iPhone 15"，这通常导致大量误匹配或零结果。

**正确的做法**是为需要精确匹配的文本字段配置**多字段映射（multi-field）**：

```json
{
  "mappings": {
    "properties": {
      "title": {
        "type": "text",
        "analyzer": "ik_max_word",
        "fields": {
          "keyword": {
            "type": "keyword",
            "ignore_above": 256
          }
        }
      }
    }
  }
}
```

此时，`title` 字段用于全文检索（`match` 查询），`title.keyword` 子字段用于精确匹配（`term` 查询）。这种设计模式在电商、内容管理等场景中被广泛采用。

#### 2.1.2 match查询的分词行为与意外结果

`match` 查询是 ES 最常用的全文检索接口，其默认行为是将查询字符串送入与索引时相同的分析器进行处理，生成词条列表后执行布尔查询。这一过程隐藏了复杂的文本处理，也是许多意外结果的来源。

**关键参数与行为控制**：

| 参数                     | 默认值      | 作用       | 调优建议                  |
|:---------------------- |:-------- |:-------- |:--------------------- |
| `operator`             | `or`     | 词条间的布尔逻辑 | 精确场景改为 `and`          |
| `minimum_should_match` | 1（`or`时） | 最少匹配词条数  | 长查询设为 `75%` 或 `2<90%` |
| `fuzziness`            | `AUTO`   | 模糊匹配编辑距离 | 拼写纠错场景启用              |
| `analyzer`             | 字段默认     | 查询时分析器   | 确保与索引分析器一致            |

**分词行为的典型影响**：

| 查询字符串           | 分析器        | 生成的词条                     | 匹配文档示例                                |
|:--------------- |:---------- |:------------------------- |:------------------------------------- |
| "iPhone 15 Pro" | `standard` | `["iphone", "15", "pro"]` | 包含任意一个词条的文档                           |
| "running shoes" | `english`  | `["run", "shoe"]`         | 包含 "run"、"running"、"shoe"、"shoes" 的文档 |
| "2024-03-15"    | `standard` | `["2024", "03", "15"]`    | 包含任意数字的文档（**非预期**）                    |

`minimum_should_match` 的灵活控制是平衡精确与召回的有效手段。例如，`"minimum_should_match": "75%"` 表示 4 个词条中至少匹配 3 个，在长尾查询中避免过度严格的全部匹配要求。

#### 2.1.3 常见陷阱：keyword字段误用match

生产环境中最频繁的性能陷阱，是对 `keyword` 类型字段使用 `match` 查询：

| 场景    | 错误查询                                   | 实际行为                                    | 正确做法                                  | 性能影响             |
|:----- |:-------------------------------------- |:--------------------------------------- |:------------------------------------- |:---------------- |
| 状态码匹配 | `match: {"status": "in_progress"}`     | 拆分为 "in" OR "progress"，匹配大量无关文档         | `term: {"status": "in_progress"}`     | **3-5x  slower** |
| 邮箱查找  | `match: {"email": "user@example.com"}` | 拆分为 "user"、"example"、"com"，匹配域内所有邮箱     | `term: {"email": "user@example.com"}` | **结果错误**         |
| 标签过滤  | `match: {"tags": "machine-learning"}`  | 拆分为 "machine" OR "learning"，匹配相关但不精确的标签 | `term: {"tags": "machine-learning"}`  | **召回率失真**        |

此类错误的排查可通过 `validate_query?explain=true` 接口辅助，该接口展示查询的解析结果和执行计划，帮助识别意外的分词行为。

### 2.2 全文检索场景：match的进阶用法

#### 2.2.1 match_phrase的短语匹配与slop调参

`match_phrase` 查询用于**短语匹配**，要求查询词条在文档中以相同顺序出现，且词项之间的位置偏移不超过指定的 `slop` 值。

**slop 参数的实战意义**：

| slop 值  | 匹配行为                      | 适用场景           |
|:------- |:------------------------- |:-------------- |
| `0`（默认） | 严格相邻，"A B" 只匹配 "A B"      | 精确引语、固定搭配      |
| `1`     | 允许一个词条间隔，"A B" 匹配 "A X B" | 容忍停用词插入        |
| `2-3`   | 允许少量间隔或词序微调               | 近似短语、用户输入变体    |
| `>5`    | 非常宽松的匹配                   | **不推荐，引入大量噪声** |

slop 值的设定需要通过搜索质量评估（如 NDCG 指标）确定最优值。建议从 `slop: 2` 开始实验，根据业务反馈逐步调整。

#### 2.2.2 multi_match的多字段检索与类型选择

`multi_match` 查询扩展了 `match` 的多字段能力，核心参数 `type` 决定字段间的组合策略：

| type 值            | 行为描述                       | 适用场景        | 典型配置                           |
|:----------------- |:-------------------------- |:----------- |:------------------------------ |
| `best_fields`（默认） | 取单字段最高得分，少量累加其他字段          | 字段有主次之分     | `title^3, content^1`           |
| `most_fields`     | 累加所有字段得分                   | 字段互补，如多语言版本 | `title_en, title_zh, title_ja` |
| `cross_fields`    | 将字段视为单一逻辑字段，词条分布跨字段计算      | 信息分散存储      | `first_name, last_name` 作为完整姓名 |
| `phrase`          | 各字段执行 `match_phrase`，取最高得分 | 短语匹配的多字段扩展  | 标题、副标题的短语匹配                    |
| `bool_prefix`     | 最后一个词条前缀匹配各字段              | 搜索建议、自动补全   | 实时搜索场景                         |

字段权重通过 `^` 符号指定，如 `title^3,content^1` 表示标题匹配贡献 3 倍于正文的得分。合理的权重配置可使核心字段的主导作用提升 **30%-50%** 的相关性质量。

#### 2.2.3 query_string的复杂语法与安全性风险

`query_string` 查询提供了类 Lucene 语法的强大表达能力，但伴随显著风险：

**安全性风险**：

- 语法错误导致查询失败
- 未加限制的通配符查询（如 `*keyword`）触发全索引扫描
- 用户输入直接嵌入存在注入攻击风险

**生产环境建议**：除非有严格的输入校验和语法白名单，否则避免直接暴露 `query_string` 给终端用户。优先考虑 `simple_query_string`（容错性更好，不支持复杂语法）或在前端构建结构化查询。

### 2.3 区间过滤场景：range查询的优化

#### 2.3.1 数值范围与日期范围的性能差异

| 字段类型                                  | 底层索引结构          | 范围查询复杂度       | 性能特征            |
|:------------------------------------- |:--------------- |:------------- |:--------------- |
| 数值（`integer`/`long`/`float`/`double`） | **BKD-Tree**    | O(log n + m)  | **最优，与范围宽度弱相关** |
| 日期（`date`）                            | BKD-Tree（时间戳存储） | O(log n + m)  | 良好，需注意时区转换开销    |
| 字符串（`keyword`）                        | 有序词条列表          | O(m)，m为范围内词条数 | 一般，依赖数据分布       |

BKD-Tree（Block K-d Tree）是 Lucene 对数值和多维数据优化的索引结构，支持高效的区间裁剪和最近邻搜索。对于单值数值字段，`range` 查询的时间复杂度接近对数级，即使范围很大也能保持高效。

#### 2.3.2 时区处理与日期格式统一

日期范围查询的时区陷阱是生产环境的常见问题。ES 内部以 UTC 存储日期，查询时的时区转换规则：

| 做法             | 示例                                   | 风险                 | 推荐度        |
|:-------------- |:------------------------------------ |:------------------ |:---------- |
| 无显式时区          | `"gte": "2024-03-15T00:00:00"`       | 按 UTC 解释，可能偏移 8 小时 | ❌ 不推荐      |
| 带时区偏移          | `"gte": "2024-03-15T00:00:00+08:00"` | 正确解释，但需确保输入正确      | ⚠️ 可用      |
| `time_zone` 参数 | `"time_zone": "+08:00"`              | ES 处理转换，统一入口       | ✅ 推荐       |
| 应用层 UTC 转换     | 外部计算时间戳                              | 完全控制，无歧义           | ✅ **最佳实践** |

**最佳实践**：索引模板中强制指定日期格式，应用层统一使用 UTC 时间戳进行交互，仅在展示层进行时区转换。

#### 2.3.3 范围边界的开闭选择对结果的影响

| 边界组合             | 数学表示       | 典型场景          | 注意事项                |
|:---------------- |:---------- |:------------- |:------------------- |
| `gte` + `lte`    | [a, b]     | 闭区间，包含两端      | 连续区间拼接时**避免重叠**     |
| `gt` + `lt`      | (a, b)     | 开区间，排除两端      | 用于排除精确边界值           |
| **`gte` + `lt`** | **[a, b)** | **左闭右开，半开区间** | **时间范围标准选择，便于区间拼接** |
| `gt` + `lte`     | (a, b]     | 左开右闭          | 较少使用，特定业务需求         |

时间范围的半开区间 `[start, end)` 是最佳实践，它允许相邻区间精确拼接而不重叠或遗漏。例如，按月统计时，1 月范围为 `[2024-01-01, 2024-02-01)`，2 月为 `[2024-02-01, 2024-03-01)`，确保每个文档恰好落入一个区间。

## 3. bool查询的语义分层与性能优化

### 3.1 四个子句的语义定位与执行顺序

`bool` 查询的执行遵循特定的优化顺序，这一顺序与语义设计高度一致：

```
执行顺序：filter → must_not → must → should
```

这种排序反映了各子句的计算代价和结果裁剪能力：**`filter` 最快且可缓存，优先执行以最小化后续处理的数据集**；`must_not` 次之，通过排除进一步缩小范围；`must` 需要评分计算，在较小数据集上执行；`should` 最后处理，用于微调相关性排序。

#### 3.1.1 filter子句：硬条件的首选位置

`filter` 子句是 `bool` 查询中性能最优的组件，其核心特征包括：

| 特性        | 说明                 | 性能收益              |
|:--------- |:------------------ |:----------------- |
| **零评分开销** | 完全不参与相关性得分计算       | 跳过 BM25 完整流程      |
| **自动缓存**  | 满足条件的 bitset 被缓存复用 | **重复查询 5-10x 加速** |
| **短路优化**  | 多个 filter 按选择性排序执行 | 优先处理最稀疏的条件        |

`filter` 子句的适用条件具有明确的边界：任何不影响排序、仅需判定是否满足的条件都应放入 `filter`。典型场景包括精确值匹配、数值或日期范围、存在性检查、地理过滤等。

#### 3.1.2 must子句：评分必需的检索条件

`must` 子句与 `filter` 子句在"必须满足"的语义上等价，但增加了**评分参与**。这一差异决定了 `must` 的严格适用场景：仅当条件的匹配程度需要影响排序时，才应使用 `must`。

`must` 子句的性能优化策略：

| 策略                  | 实现                   | 效果                |
|:------------------- |:-------------------- |:----------------- |
| 减少 `must` 子句数量      | 将非排序条件迁移至 `filter`   | 降低评分计算次数          |
| 使用 `constant_score` | 用固定得分替代动态评分          | 消除 TF-IDF/BM25 计算 |
| 限制查询深度              | 设置 `terminate_after` | 提前截断高成本查询         |

#### 3.1.3 should子句：相关性加权的灵活应用

`should` 子句的默认行为因 `bool` 上下文而异：

| `bool` 上下文              | 默认 `minimum_should_match` | 行为描述                       |
|:----------------------- |:------------------------- |:-------------------------- |
| **仅有 `should`**         | **1**                     | 至少满足 1 个 `should` 条件，否则无结果 |
| **含 `must` 或 `filter`** | **0**                     | `should` 纯用于加分，不满足不影响返回    |

这一默认行为常导致意外结果。开发者可能误以为 `should` 条件总是"可选加分"，而在无 `must`/`filter` 场景下发现文档被意外过滤。**显式设置 `minimum_should_match` 可消除这一歧义**。

#### 3.1.4 must_not子句：排除逻辑的性能代价

`must_not` 子句在语义上直观易懂，但其实现机制存在特殊的性能局限。**倒排索引天然优化于"查找包含某词条的文档"，对于"查找不包含某词条的文档"无法直接利用索引**。Lucene 的实现方式是：先获取包含该词条的文档列表，再从全量文档集合中减去该列表，得到不包含的文档。

这一"取反"操作的时间复杂度为 **O(N)**，其中 N 为索引文档总数，与词项的文档频率无关。当索引文档数达到千万甚至亿级时，`must_not` 的开销可能超过其他所有子句的总和。

### 3.2 must与filter的深度对比

#### 3.2.1 评分参与与否的结果差异

| 查询结构              | 匹配文档的 `_score` | 结果排序依据          | 适用场景       |
|:----------------- |:-------------- |:--------------- |:---------- |
| 仅 `filter`        | 0.0（或固定值）      | 无排序（默认按 `_doc`） | 批量导出、精确过滤  |
| 仅 `must`          | 基于 BM25 计算     | 按 `_score` 降序   | 全文检索、相关性排序 |
| `must` + `filter` | `must` 贡献的分数   | 按 `_score` 降序   | **标准搜索场景** |

评分参与与否直接影响分页行为：相同查询条件，`filter` 与 `must` 可能返回不同顺序的结果，深分页时尤为明显。

#### 3.2.2 查询缓存的利用机制

| 缓存层级          | 键                  | 值                 | 失效触发      | 优化方向                |
|:------------- |:------------------ |:----------------- |:--------- |:------------------- |
| Filter Cache  | filter 条件的规范化 JSON | FixedBitSet（文档位图） | 段合并、索引刷新  | 提升条件重复频率            |
| Request Cache | 完整查询体 + 分片状态       | 查询结果（hits/aggs）   | 索引刷新、显式清除 | 使用 `size: 0`，对齐刷新周期 |

Filter Cache 的 bitset 结构极为紧凑，支持高效的位运算（AND/OR/NOT）。多个 `filter` 条件的组合通过位运算快速完成，无需重复访问倒排索引。

#### 3.2.3 执行计划的底层差异

通过 Profile API 可观察 `must` 与 `filter` 的执行计划差异：

| 指标                   | `must` 子句 | `filter` 子句            |
|:-------------------- |:--------- |:---------------------- |
| `score` 阶段           | 有，详细耗时    | 无，或固定 `constant_score` |
| `build_scorer`       | 构建评分迭代器   | 构建匹配迭代器                |
| `next_doc`/`advance` | 遍历 + 实时评分 | 纯遍历，无评分                |
| 缓存标记                 | 无         | 可能显示 `cached`          |

### 3.3 must_not的性能陷阱与规避策略

#### 3.3.1 倒排索引对排除查询的支持局限

`must_not` 的性能问题根源在于倒排索引的结构特性。索引优化的是"包含"查询，"不包含"查询需要补集运算，无法利用索引的快速定位能力。

#### 3.3.2 大数据量下的性能衰减现象

| 数据总量  | `must_not` 匹配比例 | 响应时间（相对基准） | 内存峰值   |
|:----- |:--------------- |:---------- |:------ |
| 100万  | 10%             | 1.2x       | 低      |
| 100万  | 90%             | 3.2x       | 高      |
| 1000万 | 10%             | 1.3x       | 低      |
| 1000万 | **90%**         | **8.5x**   | **很高** |

当 `must_not` 匹配比例超过 **70%** 且数据量达千万级时，响应时间可能恶化至不可接受的程度。

#### 3.3.3 替代方案：正向过滤与索引设计

| 原 `must_not` 场景 | 正向替代方案   | 实现方式                                                | 性能收益       |
|:--------------- |:-------- |:--------------------------------------------------- |:---------- |
| 排除删除文档          | 筛选活跃文档   | `filter: {"term": {"is_active": true}}`             | **10-50x** |
| 排除黑名单           | 白名单机制    | 维护有效 ID 列表，`terms` 查询                               | 避免补集运算     |
| 排除特定类目          | 明确指定允许类目 | `filter: {"terms": {"category": [允许列表]}}`           | 直接索引查找     |
| 排除时间范围          | 明确指定有效时间 | `filter: {"range": {"valid_until": {"gt": "now"}}}` | 范围索引优化     |

**核心原则**：将"排除 A"转化为"只包含非 A"，通过索引设计实现正向过滤。

### 3.4 should子句的minimum_should_match控制

#### 3.4.1 默认行为与显式配置的差异

显式配置示例：

```json
{
  "bool": {
    "must": [{"term": {"category": "laptop"}}],
    "should": [
      {"match": {"title": "高性能"}},
      {"match": {"title": "轻薄"}},
      {"range": {"price": {"lte": 5000}}}
    ],
    "minimum_should_match": 2
  }
}
```

上述查询要求：必须属于 laptop 类目，**且至少满足 2 个 `should` 条件**（高性能、轻薄、低价中的任意两个）。这种配置实现了"基础过滤 + 多偏好满足"的复杂业务逻辑。

#### 3.4.2 动态匹配数的百分比设置

| 表达式       | 含义          | 5 个 `should` 时的要求      |
|:--------- |:----------- |:---------------------- |
| `3`       | 固定值         | 必须满足 3 个               |
| `75%`     | 百分比，向上取整    | 必须满足 4 个（5×75%=3.75→4） |
| `-2`      | 负值，允许最多不满足数 | 必须满足 3 个（5-2=3）        |
| `3<75%`   | 条件表达式       | 3 个时需全匹配，5 个时需 4 个     |
| `2<75%<4` | 多段条件        | 2-4 个时 75%，否则边界值       |

百分比设置在用户输入长度变化的场景尤为重要，如搜索建议、标签过滤等。

#### 3.4.3 组合查询中的优先级处理

复杂 `bool` 查询中的 `should` 子句优先级需要仔细设计：

- **高层级 `should`**：控制主要分支，如品牌、类目等粗粒度划分
- **低层级 `should`**：控制细粒度加权，如型号、属性等排序微调
- **避免 `should` 深度超过 2 层**：过深嵌套影响可读性和性能

## 4. 业务查询的通用拆解模式

### 4.1 电商商品搜索的标准分层

| 层级         | 典型条件                | 查询类型                               | 优化要点                              |
|:---------- |:------------------- |:---------------------------------- |:--------------------------------- |
| **filter** | 类目、价格、库存、地域、品牌      | `term`, `terms`, `range`, `geo`    | **最大化缓存利用率**，动态条件省略而非 `match_all` |
| **must**   | 关键词匹配标题/副标题/描述      | `multi_match`, `match_phrase`      | 字段权重差异化，`best_fields` 优先          |
| **should** | 品牌加权、销量加权、新品加权、促销加权 | `term` + `boost`, `function_score` | 归一化处理，非线性变换，可解释性保留                |

完整电商查询示例：

```json
{
  "query": {
    "bool": {
      "filter": [
        {"term": {"category": "laptop"}},
        {"range": {"price": {"gte": 3000, "lte": 8000}}},
        {"term": {"in_stock": true}},
        {"terms": {"brand": ["Apple", "Dell", "HP"]}}
      ],
      "must": [
        {"multi_match": {
          "query": "MacBook Pro",
          "fields": ["title^3", "subtitle^2", "description"],
          "type": "best_fields"
        }}
      ],
      "should": [
        {"match": {"title": {"query": "MacBook Pro", "boost": 2.0}}},
        {"term": {"is_new": {"value": true, "boost": 1.5}}},
        {"range": {"sales_30d": {"gte": 1000, "boost": 1.2}}}
      ],
      "minimum_should_match": 0
    }
  }
}
```

### 4.2 内容搜索的语义调整

| 维度   | 电商搜索      | 内容搜索               |
|:---- |:--------- |:------------------ |
| 标题权重 | 3-5 倍     | **5-10 倍**（信息密度更高） |
| 时间因素 | 次要（促销期除外） | **核心**（时效性优先）      |
| 热度因子 | 销量、评分     | 阅读量、点赞、分享、评论       |
| 个性化  | 购买历史、浏览行为 | 阅读偏好、关注关系、社交信号     |

**时间衰减函数**示例：

```json
{
  "function_score": {
    "gauss": {
      "publish_date": {
        "origin": "now",
        "scale": "7d",
        "decay": 0.5
      }
    }
  }
}
```

7 天前的内容得分衰减至 50%，`scale` 参数控制衰减速度，需根据内容更新频率调整。

### 4.3 日志分析的查询特化

| 特征          | 优化策略                                 | 实现方式                                    |
|:----------- |:------------------------------------ |:--------------------------------------- |
| **时间范围主导**  | 索引按时间分片，查询路由                         | `logs-2024.03.30` 命名，别名过滤               |
| **结构化字段为主** | `term`/`terms` 精确匹配                  | `level`, `service`, `host` 等 keyword 字段 |
| **聚合需求频繁**  | 查询与聚合分离，`size: 0`                    | 先 `filter` 裁剪，再聚合                       |
| **海量数据采样**  | `sampler` 或 `diversified_sampler` 聚合 | 减少计算量，保持代表性                             |

## 5. 性能优化实战技巧

### 5.1 查询结构优化

| 原则                   | 具体做法                           | 预期收益           |
|:-------------------- |:------------------------------ |:-------------- |
| **硬条件优先放入 `filter`** | 所有不参与排序的条件移至 `filter`          | **2-10x 性能提升** |
| 避免深层 `bool` 嵌套       | 深度 ≤ 2 层，扁平化 `should` 组合       | 降低解析开销，优化器友好   |
| 限制 `should` 子句数量     | 数量 ≤ 10，复杂加权用 `function_score` | 控制评分复杂度        |
| 减少 `must` 子句冗余       | 合并同字段条件，迁移可过滤条件                | 精简评分流程         |

### 5.2 分页与数据获取优化

| 方案                         | 适用场景               | 关键约束              | 性能特征       |
|:-------------------------- |:------------------ |:----------------- |:---------- |
| `from`/`size`              | 浅分页（`from` < 1000） | 默认最大 10000        | 简单，深分页灾难   |
| **`search_after`**         | **实时搜索深分页**        | 需唯一排序字段，仅顺序翻页     | **最优实时方案** |
| `scroll`                   | 离线批量导出（已废弃）        | 快照隔离，不反映更新        | 高资源占用      |
| **`pit` + `search_after`** | **推荐实时深分页**        | ES 7.10+，显式生命周期管理 | 轻量，支持并发    |

**深分页陷阱数据**：

| `from` 值 | 单分片获取量  | 10 分片总获取量 | 典型响应时间    |
|:-------- |:------- |:--------- |:--------- |
| 0        | 10      | 100       | 10ms      |
| 1,000    | 1,010   | 10,100    | 50ms      |
| 10,000   | 10,010  | 100,100   | **500ms** |
| 100,000  | 100,010 | 1,000,100 | **超时/失败** |

### 5.3 缓存策略与利用

| 缓存类型            | 自动/显式         | 优化方向                   |
|:--------------- |:------------- |:---------------------- |
| Filter Cache    | **自动**        | 提升条件重复频率，对齐刷新周期        |
| Request Cache   | 显式（`size: 0`） | 聚合查询启用，避免动态时间参数        |
| Fielddata Cache | 自动（首次聚合时）     | 控制高基数字段的 eager loading |

**缓存预热策略**：系统启动时执行热门查询序列，索引更新后主动刷新相关缓存，使用 `preference` 路由提升局部性。

### 5.4 索引层面的配合优化

| 维度   | 优化策略                  | 实现方式                              |
|:---- |:--------------------- |:--------------------------------- |
| 字段类型 | dual-field 映射         | `text` + `keyword` 子字段            |
| 分片数量 | 20-50GB/分片，单节点 ≤ 20 个 | 时序数据按时间滚动                         |
| 索引模板 | 禁用动态映射，预定义完整映射        | `"dynamic": "strict"`             |
| 索引排序 | 按时间字段预排序，加速范围查询       | `"index.sort.field": "timestamp"` |

## 6. 慢查询排查与性能分析

### 6.1 慢查询日志的配置与解读

| 级别      | 典型阈值      | 用途           |
|:------- |:--------- |:------------ |
| `trace` | 200-500ms | 开发调试，捕获所有查询  |
| `debug` | 1-2s      | 测试环境，识别潜在问题  |
| `info`  | 2-5s      | 生产监控，关注明显慢查询 |
| `warn`  | 5-10s     | 生产告警，紧急处理问题  |

**关键日志字段**：`took`（协调节点测量时间）、`total`（各分片时间总和）、`source`（完整查询体）、索引与分片信息。

### 6.2 Profile API的深度应用

启用方式：查询中添加 `"profile": true`

**输出结构解析**：

| 层级                    | 关键指标                                      | 诊断价值       |
|:--------------------- |:----------------------------------------- |:---------- |
| `shards` → `searches` | 分片级总耗时                                    | 识别慢分片      |
| `query` → `type`      | 查询类型（`BooleanQuery`, `TermQuery` 等）       | 验证查询重写     |
| `breakdown`           | `score`, `build_scorer`, `next_doc` 等阶段耗时 | **定位具体瓶颈** |
| `children`            | 子查询嵌套结构                                   | 逐层展开分析     |

### 6.3 执行计划的理解与干预

| 干预手段         | 应用场景      | 实现方式                                                  |
|:------------ |:--------- |:----------------------------------------------------- |
| `routing`    | 数据分散，查询定向 | 索引时指定，查询时复用                                           |
| `preference` | 副本选择，缓存预热 | `_primary`, `_replica`, `_local` 等                    |
| 自适应副本选择      | 自动避开慢节点   | 默认启用，`cluster.routing.use_adaptive_replica_selection` |

## 7. 常见问题排查与解决方案

### 7.1 结果不准确问题

| 问题类型     | 根因          | 排查方法                       | 解决方案           |
|:-------- |:----------- |:-------------------------- |:-------------- |
| 分词器不匹配   | 索引/查询分析链不一致 | `_analyze` API 对比输出        | 统一分析器配置        |
| 字段映射动态变化 | 动态映射导致类型漂移  | `GET /index/_mapping` 历史对比 | 禁用动态映射，重建索引    |
| 评分算法版本差异 | ES 升级导致排序变化 | 建立评分基准测试集                  | 调整 `boost` 值补偿 |

### 7.2 性能波动问题

| 问题类型    | 根因            | 监控指标                                   | 解决方案              |
|:------- |:------------- |:-------------------------------------- |:----------------- |
| 缓存命中率下降 | 索引刷新、段合并、集群重启 | `query_cache.hit_count/miss_count`     | 预热策略，应用层二级缓存      |
| 热点分片    | 数据/查询分布不均     | 节点级 CPU/内存/IO 对比                       | 重新分片，`routing` 优化 |
| 并发资源竞争  | 线程池队列堆积       | `thread_pool.search.queue`, `rejected` | 扩容，限流熔断，异步化       |

### 7.3 查询失败问题

| 问题类型   | 典型错误                     | 解决方案                           |
|:------ |:------------------------ |:------------------------------ |
| 深度嵌套限制 | `too_many_clauses`       | 简化查询结构，`max_clause_count` 谨慎调整 |
| 字段不存在  | `null_pointer_exception` | `exists` 查询前置检查，映射默认值          |
| 版本兼容性  | `parsing_exception`      | 迁移指南对照，渐进升级，兼容性 API            |

## 8. 实战代码示例与扩展

### 8.1 完整电商查询的curl实现

```bash
#!/bin/bash

ES_URL="${ES_URL:-http://localhost:9200}"
ES_USER="${ES_USER:-elastic}"
ES_PASS="${ES_PASS:-changeme}"
IDX="products_v1"

# 参数化输入
KEYWORD="${1:-笔记本}"
CATEGORY="${2:-laptop}"
PRICE_MIN="${3:-3000}"
PRICE_MAX="${4:-8000}"
BRANDS="${5:-}"  # 逗号分隔

# 动态构建查询体
cat <<EOF | curl -s -u "$ES_USER:$ES_PASS" \
  -X POST "$ES_URL/$IDX/_search?pretty" \
  -H "Content-Type: application/json" \
  -d @-
{
  "query": {
    "bool": {
      "filter": [
        {"term": {"category": "$CATEGORY"}},
        {"range": {"price": {"gte": $PRICE_MIN, "lte": $PRICE_MAX}}},
        {"term": {"in_stock": true}}
        $( [[ -n "$BRANDS" ]] && echo ",{\"terms\":{\"brand\":[$(echo \"$BRANDS\" | sed 's/,/\",\"/g' | sed 's/^/\"/;s/$/\"/')]}}" )
      ],
      "must": [
        {"multi_match": {
          "query": "$KEYWORD",
          "fields": ["title^3", "subtitle", "description"],
          "type": "best_fields"
        }}
      ],
      "should": [
        {"match": {"title": {"query": "$KEYWORD", "boost": 2.0}}},
        {"term": {"is_new": {"value": true, "boost": 1.5}}}
      ],
      "minimum_should_match": 0
    }
  },
  "profile": true
}
EOF
```

### 8.2 Java SDK的流畅构建

```java
import co.elastic.clients.elasticsearch.ElasticsearchClient;
import co.elastic.clients.elasticsearch.core.SearchRequest;
import co.elastic.clients.elasticsearch.core.SearchResponse;
import co.elastic.clients.json.jackson.JacksonJsonpMapper;
import co.elastic.clients.transport.ElasticsearchTransport;
import co.elastic.clients.transport.rest_client.RestClientTransport;
import org.apache.http.HttpHost;
import org.elasticsearch.client.RestClient;

import java.io.IOException;
import java.util.List;
import java.util.Map;

public class ProductSearch {

    private final ElasticsearchClient client;

    public ProductSearch() {
        RestClient restClient = RestClient.builder(
            new HttpHost("localhost", 9200)).build();
        ElasticsearchTransport transport = new RestClientTransport(
            restClient, new JacksonJsonpMapper());
        this.client = new ElasticsearchClient(transport);
    }

    public SearchResponse<Map> search(
            String keyword,
            String category,
            double priceMin,
            double priceMax,
            List<String> brands) throws IOException {

        return client.search(s -> s
            .index("products_v1")
            .query(q -> q.bool(b -> {
                // filter层：硬条件
                b.filter(f -> f.term(t -> t.field("category").value(category)));
                b.filter(f -> f.range(r -> r
                    .number(n -> n.field("price")
                        .gte(priceMin).lte(priceMax))));
                b.filter(f -> f.term(t -> t.field("in_stock").value(true)));

                if (brands != null && !brands.isEmpty()) {
                    b.filter(f -> f.terms(t -> t
                        .field("brand")
                        .terms(v -> v.value(brands.stream()
                            .map(FieldValue::of).toList()))));
                }

                // must层：核心检索
                b.must(m -> m.multiMatch(mm -> mm
                    .query(keyword)
                    .fields("title^3", "subtitle", "description")
                    .type(TextQueryType.BestFields)));

                // should层：加权因子
                b.should(sq -> sq.match(m -> m
                    .field("title")
                    .query(keyword)
                    .boost(2.0f)));
                b.should(sq -> sq.term(t -> t
                    .field("is_new")
                    .value(true)
                    .boost(1.5f)));

                b.minimumShouldMatch("0");
                return b;
            }))
            .size(20),
            Map.class);
    }
}
```

### 8.3 Python客户端的等效实现

```python
from elasticsearch import Elasticsearch
from typing import List, Optional, Dict, Any

class ProductSearch:
    def __init__(self, hosts: List[str], username: str, password: str):
        self.client = Elasticsearch(
            hosts=hosts,
            basic_auth=(username, password)
        )

    def build_query(
        self,
        keyword: str,
        category: str,
        price_min: float,
        price_max: float,
        brands: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """构建分层查询结构"""

        # filter层：硬条件
        filter_clauses = [
            {"term": {"category": category}},
            {"range": {"price": {"gte": price_min, "lte": price_max}}},
            {"term": {"in_stock": True}}
        ]

        if brands:
            filter_clauses.append({"terms": {"brand": brands}})

        # must层：核心检索
        must_clauses = [{
            "multi_match": {
                "query": keyword,
                "fields": ["title^3", "subtitle", "description"],
                "type": "best_fields"
            }
        }]

        # should层：加权因子
        should_clauses = [
            {"match": {"title": {"query": keyword, "boost": 2.0}}},
            {"term": {"is_new": {"value": True, "boost": 1.5}}}
        ]

        return {
            "bool": {
                "filter": filter_clauses,
                "must": must_clauses,
                "should": should_clauses,
                "minimum_should_match": 0
            }
        }

    def search(
        self,
        keyword: str,
        category: str,
        price_min: float,
        price_max: float,
        brands: Optional[List[str]] = None,
        size: int = 20
    ) -> Dict[str, Any]:
        """执行搜索并返回结果"""

        query = self.build_query(keyword, category, price_min, price_max, brands)

        return self.client.search(
            index="products_v1",
            query=query,
            size=size,
            profile=True  # 启用性能分析
        )

    # 查询模板复用
    QUERY_TEMPLATES = {
        "new_arrivals": {
            "bool": {
                "filter": [
                    {"term": {"is_new": True}},
                    {"range": {"created_at": {"gte": "now-7d"}}}
                ],
                "must": [{"match_all": {}}]
            }
        },
        "hot_sales": {
            "bool": {
                "filter": [{"range": {"sales_30d": {"gte": 1000}}}],
                "must": [{"match_all": {}}],
                "sort": [{"sales_30d": "desc"}]
            }
        }
    }

    def search_by_template(self, template_name: str, **params) -> Dict[str, Any]:
        """使用预定义模板执行搜索"""
        template = self.QUERY_TEMPLATES.get(template_name)
        if not template:
            raise ValueError(f"Unknown template: {template_name}")

        # 参数化替换（简化示例，生产环境使用更安全的机制）
        import json
        query_str = json.dumps(template)
        for key, value in params.items():
            query_str = query_str.replace(f"{{{{{key}}}}}", str(value))

        return self.client.search(
            index="products_v1",
            query=json.loads(query_str)
        )
```

## 9. 进阶练习与性能验证

### 9.1 分层查询的构建练习

| 练习                     | 目标                | 验证要点            |
|:---------------------- |:----------------- |:--------------- |
| 多条件组合 `bool`           | 实现 5+ 条件的复杂查询     | 语义正确性，执行顺序      |
| `must` 与 `filter` 位置互换 | 对比相同语义的两种写法       | **性能差异，结果排序变化** |
| `should` 权重调整实验        | 理解 `boost` 对排序的影响 | 相关性评估，A/B 测试    |

### 9.2 性能基准测试

| 测试项                | 方法                                      | 关键指标                      |
|:------------------ |:--------------------------------------- |:------------------------- |
| 相同语义的不同写法对比        | `must` vs `filter`，`terms` vs 多个 `term` | 响应时间，吞吐量                  |
| Profile API 优化前后分析 | 保存 `profile` 输出，逐项对比                    | 各阶段耗时分解                   |
| 压测工具与指标采集          | esrally, JMeter, 或自定义脚本                 | P50/P95/P99 延迟，错误率，CPU/内存 |

**推荐压测配置**：

```yaml
# esrally 测试轨道示例
name: product_search_benchmark
indices:
  - name: products_v1
    body: products_mapping.json
    source-file: products_documents.json.bz2

operations:
  - name: search-by-keyword
    operation-type: search
    index: products_v1
    body:
      query:
        bool:
          must: [{match: {title: "{{ keyword }}"}}]
          filter: [{term: {category: "{{ category }}"}}]

schedule:
  - operation: search-by-keyword
    clients: 10
    warmup-time-period: 60
    time-period: 300
    target-throughput: 100
```
