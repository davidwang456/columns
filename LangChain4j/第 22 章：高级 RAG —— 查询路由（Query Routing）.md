# 第 22 章：高级 RAG —— 查询路由（Query Routing）

## 1. 项目背景

### 业务场景（拟真）

企业内部有多个知识域——**产品文档库**、**法务合同库**、**人事政策库**、**IT 运维 Runbook**。如果所有文档都混在同一个向量库里，会出现两个问题：① 用户问了一个法务相关问题，向量检索可能把产品文档里「长得像」的片段也召回回来——噪声；② 不同知识域的更新节奏不同——产品文档每周更新，法务文档按季度更新——同索引管理不便。

**查询路由（Query Routing）** 的作用是：在进入向量库检索之前，先判断本条问题应该检索哪个/哪些子索引。

### 痛点放大

没有路由时：全库检索的语义噪声高——用户问「年假怎么休」同时搜到了法务合同里的「休假条款」和人事政策里的「年假规定」，模型不知道用哪个。同时单次检索扫描的数据量过大，延迟随索引增长线性上升。各团队也无法独立管理自己知识域的索引版本和权限。

## 2. 项目设计：小胖、小白与大师的对话

**小胖**：路由像商场楼层导览——告诉你「买家电去三楼，吃饭去五楼」？走错了楼层就买不到对的东西？

**大师**：这个比喻很准确。路由就是那个楼层导览——它不负责帮你挑选商品，只告诉你去哪层找。路由和多路融合（第 27 章）的区别就在这里：**路由是「互斥选择」**——「这个问题应该去法律库还是 HR 库」；**融合是「并行合并」**——「从法律库和新闻库里都搜一下，然后合并结果」。多数场景先做路由再做融合：先决定「去哪找」，再从那里拿回结果。

**小白**：第一版能先用关键词规则做路由吗？什么时候应该上 LLM 或小模型做路由？

**大师**：第一版用关键词和正则完全可行——比如包含「合同」「违约」「条款」的去法务库，包含「年假」「薪资」「入职」的去 HR 库。什么时候升级到 LLM/小模型路由？当 **误路由的代价很高** 时——比如「帮我看一下这份合同里的赔偿条款」这句话既包含「合同」（法务特征）又包含「赔偿条款」（可能是法务也可能是客服）。关键词规则可能同时路由到两个库或者路由错了；LLM 路由可以理解上下文做出更准确的判断。而且当规则越加越多——从 5 条到 50 条——就变成了打地鼠问题，这时候小模型分类器更稳定。**技术映射**：**路由 = 检索范围的决策点，决定的是「去哪找」而不是「怎么合并找回来的东西」；路由的升级路径要跟着误路由成本走，不要一开始就上 LLM 路由**。

---

## 3. 项目实战

### 环境准备

```bash
cd langchain4j-examples/rag-examples
export OPENAI_API_KEY="sk-your-key-here"
```

### 分步实现

#### 步骤 1：用关键词规则做第一版路由

```java
import java.util.*;

public class KeywordRouter {

    private final Map<String, String> rules = new HashMap<>();
    
    public KeywordRouter() {
        // 关键词 → 路由目标
        rules.put("合同|违约|条款|赔偿|法律|起诉", "legal");
        rules.put("年假|薪资|入职|社保|公积金|考勤", "hr");
        rules.put("产品|规格|价格|购买|型号|保修", "products");
        rules.put("服务器|宕机|网络|密码|VPN|运维", "it");
    }

    public String route(String query) {
        for (Map.Entry<String, String> entry : rules.entrySet()) {
            if (query.matches(".*(" + entry.getKey() + ").*")) {
                return entry.getValue();
            }
        }
        return "general";  // 默认路由到通用知识库
    }
}

// 测试
KeywordRouter router = new KeywordRouter();
System.out.println(router.route("年假有几天"));           // hr
System.out.println(router.route("合同违约怎么赔偿"));     // legal
System.out.println(router.route("今天天气如何"));          // general
```

**预期输出**：
```
hr
legal
general
```

#### 步骤 2：关联路由与 ContentRetriever

```java
// 每个路由目标对应一个独立的 ContentRetriever
Map<String, ContentRetriever> retrievers = new HashMap<>();
retrievers.put("legal", createLegalRetriever());
retrievers.put("hr", createHrRetriever());
retrievers.put("products", createProductRetriever());
retrievers.put("it", createItRetriever());
retrievers.put("general", createGeneralRetriever());

// 根据路由结果选择对应的 retriever
String target = router.route(userQuery);
ContentRetriever selectedRetriever = retrievers.get(target);
```

#### 步骤 3：低置信度的安全默认策略

```java
// 当路由置信度低（比如同时匹配了多个域），走安全默认
public String routeWithFallback(String query) {
    List<String> matched = new ArrayList<>();
    for (Map.Entry<String, String> entry : rules.entrySet()) {
        if (query.matches(".*(" + entry.getKey() + ").*")) {
            matched.add(entry.getValue());
        }
    }
    if (matched.size() != 1) {
        // 0 个匹配或 >1 个匹配 → 走通用知识库 + 向用户澄清
        System.out.println("Low confidence routing, fallback to general");
        return "general";
    }
    return matched.get(0);
}
```

### 可能遇到的坑

| 坑 | 表现 | 解法 |
|----|------|------|
| 提示泄露内部索引代号 | 模型回答「我去 legal 库查一下」 | 用枚举或编号代替真实索引名 |
| 路由未与权限对齐 | 普通员工路由到了高管知识库 | 路由+第 25 章元数据过滤双重校验 |
| 只测路由准确率不测端到端 | 路由对了但答案错了 | 评估指标 = 路由准确率 × 最终答案质量 |

### 测试验证

```bash
# 跨域试探：问法务问题但用产品关键词，确认不会路由错
# 模糊问题：「这个怎么处理」→ 走 general + 澄清
```

### 完整代码清单

`_02_Advanced_RAG_with_Query_Routing_Example.java`

---

## 4. 项目总结

### 优点与缺点

| 维度 | Query Routing | 单库全量 | 仅关键词过滤 |
|------|-------------|---------|-----------|
| 语义噪声 | 低（精确域匹配） | 高 | 中 |
| 治理复杂度 | 高（每域独立索引） | 低 | 中 |
| 误路由代价 | 可高（用户看到完全不相关的结果） | 低 | 中 |

### 适用场景

- 多部门共享同一 AI 助理（跨知识域）
- 知识域之间有严格权限隔离
- 各域更新节奏不同（产品周更、法务季更）

### 不适用场景

- 单一知识域、小文档量（路由层过重）
- 所有知识必须一起召回才能正确回答（需用多路融合）

### 常见踩坑

1. **提示泄露内部分类名**：模型说「我在 legal 库里查到的」——用户知道你有法务库
2. **只测路由准确率不测权限侧信道**：路由对了但检索到的片段超越用户权限
3. **无 fallback 策略**：冷启动新知识域时无法回答

### 进阶思考题

1. 先路由再压缩与先压缩再路由的 A/B 实验设计？各有什么优劣？
2. routeId 如何写入统一观测与计费？如何按路由维度拆分 token 成本？

### 推广计划提示

| 角色 | 建议阅读顺序 | 协作要点 |
|------|-------------|----------|
| 开发 | 第 21 章 → 本章 → 第 27 章 | 路由+过滤双重校验 |
| 安全 | 本章 + 第 25 章 | 跨租户试探用例 |
| 运维 | 路由分布仪表 | 金丝雀+快速回滚开关 |