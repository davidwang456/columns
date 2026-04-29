# 第 28 章：联网搜索与 SQL 数据库检索（合并章）

## 1. 项目背景

### 业务场景（拟真）

向量库擅长非结构化语义相似度，但有两类信息它覆盖不了：**最新的公共信息**（「今天 OpenAI 发布新产品了吗」需要联网搜索）和 **精确的结构化事实**（「订单 #12345 当前什么状态」需要查 SQL 数据库）。**联网搜索** 和 **SQL 数据库检索** 作为两种特殊的 ContentRetriever，与向量检索互补。

### 痛点放大

联网搜索的风险在于 **假新闻和不可信来源**——如果模型直接用了某篇自媒体文章的未经核实信息，就是事实性错误。SQL 检索的最大风险是 **注入和越权**——如果让模型直接或间接拼 SQL 查询，可能泄露全库数据。两条路径并行时，如果结果矛盾（联网说价格是 $999、数据库说 $899），需要仲裁规则。

## 2. 项目设计：小胖、小白与大师的对话

**小胖**：联网搜索是不是就像我在屋里不确定外面下没下雨，开窗伸手摸一下？

**大师**：很形象——但开窗伸出去的手，摸回来的可能不是雨水，是楼上倒下来的洗脚水（假新闻）。联网搜索必须配 **白名单域名和二次校验**：只从你信任的域名获取信息；对关键事实做交叉验证；并且明确告诉用户「这个信息来自外部网络，请核实」。SQL 检索则像打开公司的保险柜——你必须确保开柜门的手是戴着手套的（参数化查询），而且只能开自己的那层抽屉（行级安全）。

**小白**：联网和 SQL 同时返回了矛盾的信息——比如数据库说这个商品库存还有 10 件，联网的一个促销页面说已经售罄了——听谁的？

**大师**：**仲裁规则必须写死**，不能交给模型判断。通常的优先级是：**内部 DB > 向量库 > 联网摘要**。因为内部数据库是唯一的「事实源头」，向量库是「静态知识的快照」，联网搜索引擎是「未经核实的第三方信息」。当内部 DB 和联网信息矛盾时，优先展示 DB 的结果，并注明「根据我的记录……」。**技术映射**：**混合检索 = 最大风险面——每一路都有自己的安全、合规和运维问题，必须分域治理；事实源的仲裁规则必须在代码里写死，不能靠模型自己判断谁更可信**。

---

## 3. 项目实战

### 环境准备

```bash
# 联网搜索需要搜索引擎 API Key（如 Bing Search API）
export SEARCH_API_KEY="your-search-api-key"
export OPENAI_API_KEY="sk-your-key-here"

# SQL 检索需要数据库连接
export DB_URL="jdbc:postgresql://localhost:5432/orders"
```

### 分步实现

#### 步骤 1：WebSearchEngine 接入

```java
import dev.langchain4j.web.search.WebSearchEngine;
import dev.langchain4j.web.search.WebSearchResult;
import dev.langchain4j.web.search.bing.BingSearchEngine;
import dev.langchain4j.rag.content.retriever.WebSearchContentRetriever;

// Bing 搜索（需 API Key）
WebSearchEngine searchEngine = BingSearchEngine.builder()
        .apiKey(System.getenv("SEARCH_API_KEY"))
        .market("zh-CN")
        .build();

ContentRetriever webRetriever = WebSearchContentRetriever.builder()
        .webSearchEngine(searchEngine)
        .maxResults(3)
        .build();

// 搜索演示
List<Content> results = webRetriever.retrieve("OpenAI latest news 2024");
results.forEach(r -> System.out.println(r.textSegment().text()));
```

#### 步骤 2：SQL 数据库检索

```java
import javax.sql.DataSource;
import org.postgresql.ds.PGSimpleDataSource;

// 只读数据源
DataSource readOnlyDS = new PGSimpleDataSource();
((PGSimpleDataSource) readOnlyDS).setUrl(System.getenv("DB_URL"));

// 参数化查询——防注入
String sql = "SELECT order_id, status, amount FROM orders WHERE user_id = ? AND status = ?";
// 使用 jdbcTemplate.query(sql, userId, status) 执行
```

#### 步骤 3：仲裁规则

```java
// 事实源仲裁
public String arbitrate(String dbResult, String webResult) {
    if (dbResult != null && !dbResult.isEmpty()) {
        return "[DB] " + dbResult;  // 数据库优先
    }
    if (webResult != null && !webResult.isEmpty()) {
        return "[Web] " + webResult + " (此信息来自网络，请核实)";
    }
    return "未找到相关信息";
}
```

### 可能遇到的坑

| 坑 | 表现 | 解法 |
|----|------|------|
| 联网摘要被当事实 | 用户投诉信息不准确 | 明确标注来源 + 白名单域名 |
| 动态拼 SQL | SQL 注入漏洞 | 永远用参数化查询 + 只读账号 |
| 外部搜索未熔断 | 第三服务 | 设超时 + 熔断 + 降级文案 |

### 测试验证

```bash
# SQL 注入测试套件：输入 ' OR 1=1 -- 等
# 联网伪造结果对抗
```

### 完整代码清单

`_08_Advanced_RAG_Web_Search_Example.java`、`_10_Advanced_RAG_SQL_Database_Retreiver_Example.java`

---

## 4. 项目总结

### 优点与缺点

| 维度 | Web + SQL + 向量 | 仅向量 RAG | 仅联网 |
|------|-----------------|----------|--------|
| 时效性 | 强 | 弱 | 中 |
| 事实准确性 | 强（DB 保底） | 中 | 低 |
| 运维复杂度 | 高 | 低 | 中 |

### 适用场景

- 研究助理、经营分析
- 需同时查内部数据与外部信息的场景

### 不适用场景

- 禁止出网或禁止动态 SQL 的环境
- 信息可靠性要求极高不能引用网络信息

### 常见踩坑

1. **联网摘要当事实不核对** → 用户按错误信息操作出事故
2. **动态拼 SQL** → 注入泄露全库
3. **未熔断** → 外部搜索慢拖垮核心 API

### 进阶思考题

1. 搜索引擎返回结果的时间戳与 DB 更新时间戳冲突时，文案怎么写？
2. 只读视图 + 行级安全的测试矩阵如何设计？
